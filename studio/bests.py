"""Bests (F1): the session-summary "best" cluster extracted from Session — the headline best
lap, the per-column session-best splits (the purple cells), the theoretical best (their sum),
and the best rolling lap. numpy-only (no pacer core).

DEPENDENCY INJECTION (like studio/render_cache.py + corner_model/driving_channels): the
constructor takes Session-bound callables over Session's own primitives, so NO method here
reaches a `_`-private attribute of Session — Session owns the pacer side + the memoized lap
sets and wires its primitives into the callables. Session keeps thin delegators
(session.best_lap_id -> self.bests.best_lap_id, …) so the existing call sites and the tests that
monkey-patch `s.best_lap_id` / `s.session_best_splits` / `s.theoretical_best` keep working.

The `best_lap_id` memoization slot stays on Session (`_best_cache`, cleared in set_timing_lines,
seeded by the pure-Python tests), reached here through the injected `best_cache_get` /
`best_cache_set` accessors + the `unset` sentinel — so the state lives where it is cleared/seeded
while the logic lives in this service.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np

# Nearest-point search arc as a fraction of lap samples (~21 m; line-length drift measured
# <0.5%). Floor of 5 samples keeps short synthetic laps searchable. Module-level (not a class
# attribute) because Session.sector_boundary_distances reuses the SAME ±2% arc for its wrong-pass
# window guard — one source, imported there.
ROLLING_SEARCH_FRAC = 0.02
# Match must travel the same direction (within 60°) to reject the OTHER leg of a corner/
# out-and-back; genuine same-point matches measure cos > 0.9.
_ROLLING_HEADING_MIN_COS = 0.5
# Refined closest approach must be ≤ 3 m to count as the same point (genuine winners ≤ 1.6 m).
# A rejected anchor only drops a candidate window, so the gate can't bias the minimum down.
_ROLLING_MATCH_MAX_M = 3.0


def _unit_tangents(xs: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-sample unit direction-of-travel of a trace (central differences, normalized;
    a zero-length step keeps a zero vector, which any heading gate then rejects). Used by
    `best_rolling_lap`'s same-direction match filter. Needs len ≥ 2 (guarded by callers)."""
    tx = np.gradient(xs)
    ty = np.gradient(ys)
    norm = np.hypot(tx, ty)
    norm[norm == 0] = 1.0
    return tx / norm, ty / norm


class Bests:
    """Session summary bests (headline best / session-best splits / theoretical / rolling),
    computed over Session-bound primitives.

    All inputs are Session-bound callables (Session owns the pacer side + its memoized lap sets):
    `valid_lap_ids` / `lap_has_dropout` are the memoized lap-set accessors; `lap_time` is the
    per-lap time (reads Session's pacer `laps.lap_time`); `lap_sector_splits` the per-lap split
    columns; `sector_line_count` the number of sector lines; `lap_columns` the per-lap array
    fetch. `best_cache_get` / `best_cache_set` read/write Session's `_best_cache` memo slot and
    `unset` is Session's "not computed" sentinel (None is a legal cached "no best lap")."""

    def __init__(self, *,
                 valid_lap_ids: Callable[[], list[int]],
                 lap_has_dropout: Callable[[int], bool],
                 lap_time: Callable[[int], float],
                 lap_sector_splits: Callable[[int], list[float]],
                 sector_line_count: Callable[[], int],
                 lap_columns: Callable[[int], tuple],
                 best_cache_get: Callable[[], object],
                 best_cache_set: Callable[[object], None],
                 unset: object):
        self._valid_lap_ids = valid_lap_ids
        self._lap_has_dropout = lap_has_dropout
        self._lap_time = lap_time
        self._lap_sector_splits = lap_sector_splits
        self._sector_line_count = sector_line_count
        self._lap_columns = lap_columns
        self._best_cache_get = best_cache_get
        self._best_cache_set = best_cache_set
        self._unset = unset

    def best_candidate_ids(self) -> list[int]:
        """Laps eligible to be the HEADLINE best / Δ-baseline / session-best split: valid laps
        with NO interior GPS dropout. A dropout lap's distance is speed-integral-reconstructed
        and its timing is less reliable (it is already excluded from consistency σ and corner
        detection), so it must not be allowed to become the 'best lap', the delta baseline, or a
        purple session-best cell. Falls back to all valid laps when EVERY valid lap has a dropout
        (a flagged best beats no best). The lap table still SHOWS dropout laps, flagged ⚠."""
        valid = self._valid_lap_ids()
        clean = [i for i in valid if not self._lap_has_dropout(i)]
        return clean if clean else valid

    def best_lap_id(self) -> int | None:
        """The fastest dropout-free valid lap, else None. Memoized (same lifetime as
        valid_lap_ids; cleared on re-segment) — resolved several times per tick."""
        cached = self._best_cache_get()
        if cached is not self._unset:
            return cached
        candidates = self.best_candidate_ids()
        best = min(candidates, key=self._lap_time) if candidates else None
        self._best_cache_set(best)
        return best

    def session_best_splits(self) -> list[float | None]:
        """The session-best (minimum) split per sub-sector COLUMN, computed independently per
        column across all VALID laps — exactly the values the lap table paints purple (F5).
        N sector lines → N+1 columns; a column with no finite data → None. Hoisted here from
        lap_table so the table's purple cells and the theoretical-best footer read ONE
        computation and can never disagree. With NO sector lines a lap is a single sub-sector
        whose split is its lap time, so the one column's best is the best lap time.

        Recomputed per call (refresh-time only, never per-tick): the inputs are the cached
        per-lap `lap_sector_splits`, so memoizing here would only add another slot to clear
        on re-segment."""
        # N+1 columns, matching the lap-table headers; a deduped lap contributes nothing to a
        # missing trailing column (i<len(sp) guard).
        n_splits = self._sector_line_count() + 1
        # Dropout laps are excluded (see best_candidate_ids): a reconstructed-distance lap must
        # not own a purple session-best split or feed theoretical_best.
        all_splits = [self._lap_sector_splits(lap_id) for lap_id in self.best_candidate_ids()]
        best: list[float | None] = []
        for i in range(n_splits):
            # min over finite, strictly-positive splits only, so a stray 0/negative split can't
            # poison theoretical_best.
            vals = [sp[i] for sp in all_splits
                    if i < len(sp) and math.isfinite(sp[i]) and sp[i] > 0]
            best.append(min(vals) if vals else None)
        return best

    def theoretical_best(self) -> float | None:
        """The THEORETICAL BEST lap time (seconds): the sum of the session-best sector splits
        (`session_best_splits` — the purple cells), i.e. the lap you'd drive by stitching every
        best sector together. Always exactly the sum of the purple cells, because both read the
        same accessor. With no sector lines a lap is one sub-sector, so this DEGENERATES to the
        best lap time by definition (documented choice: the footer row stays meaningful before
        any sectors are placed instead of reading '—'). None when no valid laps exist or some
        column has no finite split (every lap partial there)."""
        bests = self.session_best_splits()
        if not bests or any(b is None for b in bests):
            return None
        return float(sum(bests))

    def best_rolling_lap(self) -> float | None:
        """The BEST ROLLING lap time (seconds): the fastest single COMPLETE loop of the track
        regardless of where it starts — the minimum, over every track position P, of the time
        from passing P to passing P again one lap later. None if no valid laps.

        Per-pair windows anchored to the same SPATIAL point: for each consecutive valid-lap pair
        (k, k+1), every lap-k sample is an anchor P and the window ends when lap k+1 passes
        CLOSEST to P (nearest same-direction sample within the search arc, sub-sample refined).
        WHY not a normalized-distance phase / fixed-odometer window: the laps' line lengths differ,
        so equal phase is a different physical point — those bias the min optimistically.

        Window admission: straddling windows only across consecutive valid laps where NEITHER has
        a GPS dropout (else the timing is unreliable). Every complete valid lap is admitted as the
        S/F-aligned degenerate window, so best_rolling ≤ best lap time."""
        valid = self._valid_lap_ids()
        if not valid:
            return None
        # Complete laps are themselves (S/F-aligned) rolling windows: rolling ≤ best. Seed the
        # floor from the dropout-free candidate set (same rule as best_lap_id) so a dropout lap's
        # unreliable time can't undercut the rolling best; the pair windows below already skip
        # dropout laps (line: lap_has_dropout guard).
        best = min(self._lap_time(i) for i in self.best_candidate_ids())
        valid_set = set(valid)
        for a in valid:
            b = a + 1
            if b not in valid_set or self._lap_has_dropout(a) or self._lap_has_dropout(b):
                continue
            times_a, xs_a, ys_a, _spd_a, cum_a = self._lap_columns(a)
            times_b, xs_b, ys_b, _spd_b, cum_b = self._lap_columns(b)
            n_a, n_b = len(times_a), len(times_b)
            if n_a < 2 or n_b < 2:
                continue
            total_a, total_b = float(cum_a[-1]), float(cum_b[-1])
            if total_a <= 0 or total_b <= 0:
                continue
            # Consecutive valid laps are time-contiguous by construction (lap a's interpolated
            # finish crossing IS lap b's start crossing — one crossing instant computed once in
            # the segmentation). Defensive: skip a pair with a real hole between the laps (only
            # reachable on hand-seeded sessions), where the windows would not be one loop.
            if abs(float(times_b[0]) - float(times_a[-1])) > 1e-3:
                continue
            # Nearest lap-b sample per anchor, searched only inside the ±_ROLLING_SEARCH_FRAC
            # arc around the anchor's normalized fraction, and only among samples travelled in
            # the same direction (the heading gate — see both constants' WHY above).
            s_a = cum_a / total_a
            s_b = cum_b / total_b
            k = max(5, int(ROLLING_SEARCH_FRAC * n_b))
            centers = np.clip(np.searchsorted(s_b, s_a), 0, n_b - 1)
            idx = np.clip(centers[:, None] + np.arange(-k, k + 1)[None, :], 0, n_b - 1)
            d2 = (xs_b[idx] - xs_a[:, None]) ** 2 + (ys_b[idx] - ys_a[:, None]) ** 2
            tax, tay = _unit_tangents(xs_a, ys_a)
            tbx, tby = _unit_tangents(xs_b, ys_b)
            heading_cos = tax[:, None] * tbx[idx] + tay[:, None] * tby[idx]
            d2 = np.where(heading_cos >= _ROLLING_HEADING_MIN_COS, d2, np.inf)
            rowmin = np.argmin(d2, axis=1)
            anchors = np.arange(n_a)
            j = idx[anchors, rowmin]
            # An anchor whose whole search arc fails the heading gate (degenerate geometry)
            # simply contributes no window.
            usable = np.isfinite(d2[anchors, rowmin])

            # Sub-sample refinement: project each anchor onto the two trace segments adjacent
            # to its nearest sample; the closer projection's chord parameter interpolates the
            # crossing time (the same chord idiom the C++ start-line crossing uses).
            def _project(j0, j1, xs_b=xs_b, ys_b=ys_b, times_b=times_b,
                         xs_a=xs_a, ys_a=ys_a):
                vx, vy = xs_b[j1] - xs_b[j0], ys_b[j1] - ys_b[j0]
                len2 = vx * vx + vy * vy
                safe = np.where(len2 > 0, len2, 1.0)
                u = ((xs_a - xs_b[j0]) * vx + (ys_a - ys_b[j0]) * vy) / safe
                u = np.clip(np.where(len2 > 0, u, 0.0), 0.0, 1.0)
                qx, qy = xs_b[j0] + u * vx, ys_b[j0] + u * vy
                dist2 = (qx - xs_a) ** 2 + (qy - ys_a) ** 2
                return dist2, times_b[j0] + u * (times_b[j1] - times_b[j0])

            d2_lo, t_lo = _project(np.maximum(j - 1, 0), j)
            d2_hi, t_hi = _project(j, np.minimum(j + 1, n_b - 1))
            t_cross = np.where(d2_lo <= d2_hi, t_lo, t_hi)
            # Distance gate on the REFINED closest approach (see _ROLLING_MATCH_MAX_M's WHY).
            usable &= np.minimum(d2_lo, d2_hi) <= _ROLLING_MATCH_MAX_M ** 2
            w = np.where(usable, t_cross - times_a, np.inf)
            if np.isfinite(w).any():
                best = min(best, float(np.min(w)))
        return float(best)
