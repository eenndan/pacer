"""CornerModel — the per-segmentation corner analysis extracted from Session: the detected
corner list + reference total, the per-lap projected corner stats (incl. the cross-recording
reference's under reference_id), the per-corner session-best times, and the IDEAL-LAP segment
composite (`segment_bests`). All derive from the current segmentation, so Session composes this
service + delegates.

PACER-FREE (numpy on Session's cached per-lap primitives). `invalidate()` (from
set_timing_lines) drops every cache on re-segment; `invalidate_stats()` drops only the
per-lap stats when a cross-recording reference changes (the detection windows are unchanged).

DEPENDENCY INJECTION (like studio/render_cache.py): the constructor takes Session-bound
callables over Session's own primitives, so NO method here reaches a `_`-private attribute of
Session — Session owns the pacer side + wires its privates into the callables.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np

from . import corners

# "not yet computed" sentinel (None is a legal cached value); module-local to avoid importing
# Session.
_UNSET = object()

# ------------------------------------------------------- ideal-lap donor admission (D1)
# A partition edge pair closer together than this ON THE REFERENCE ODOMETER is a POINT, not a
# segment. Two cases produce one: the first corner can begin on the start line (and the last can
# end on it), and the detector can leave a sub-metre sliver between two corners it did not merge.
# A point carries ~0 s on every lap, so every lap "wins" it with 0 and it contributes nothing to
# the composite either way — it must NOT be mistaken for a lap that collapsed (below).
POINT_SPAN_M = 0.5
# A lap may donate a segment only if it actually DROVE it. `corners.project_boundaries` clamps a
# crossed spatial match onto its neighbour (np.maximum.accumulate), which can collapse a real
# segment to ZERO width on ONE lap while it is full width on all the others. That lap's time for
# the segment went to the NEIGHBOURING segment, so a naive min would bank a free 0 that nobody
# drove — measured at 0.111 s of a claimed 1.252 s on the Sandown recording.
#
# Threshold evidence (four real recordings, 2,929 (lap, segment) cells): every cell that is not a
# hard collapse carries ≥ 0.93 of the segment's reference span, and every collapsed one carries
# ≤ 0.0001. Any threshold in (0.01, 0.9) selects exactly the same cells; 0.5 sits in the middle of
# a gap four orders of magnitude wide, so this is a separator, not a tuned knob.
MIN_DONOR_SPAN_FRAC = 0.5


class IdealSample(NamedTuple):
    """WHAT THE IDEAL LAP WAS MINIMISED OVER — the four counts any surface printing the composite
    has to print with it, because the composite is a function of all four.

    `SegmentBests.total` is a sum of per-segment MINIMA. A minimum over more laps is never larger
    and is usually smaller, so the "ideal" falls as a session accumulates laps, and it falls again
    when the partition is cut finer. Both are properties of an order statistic over a partition,
    not of the driving — so two ideals are only comparable when these counts are comparable.

    Measured on the owner's five recordings (random subsets of the clean laps, 200 draws per N,
    `ideal_total` as the app computes it):

    | recording        | 5 laps | 10 | 20 | 40 | all | per doubling of N |
    |------------------|--------|----|----|----|-----|-------------------|
    | D24 1 chapter    | 68.333 | 68.057 | 67.844 | — | 67.831 (21) | 0.174 s |
    | D24 3 chapters   | 67.957 | 67.578 | 67.192 | 66.832 | 66.563 (65) | **0.384 s** |
    | Sandown ch 1     | 48.585 | 48.225 | 47.982 | — | 47.933 (23) | 0.241 s |
    | Sandown 3 ch     | 48.338 | 47.998 | 47.735 | 47.483 | 47.374 (59) | 0.194 s |
    | SD_30_08         | 13.025 | 12.945 | 12.878 | — | 12.856 (25) | 0.068 s |

    There is no plateau: on D24 3 chapters the decrement per doubling GROWS with N (0.31 s over
    8→15 laps, 0.42 s over 30→65). The gap the app headlines ("on the table") therefore grows with
    lap count on all five — D24 3 chapters reads −0.90 s at 5 laps and −1.64 s at 65, same driving.

    THE BEST LAP HAS THE SAME PROPERTY, WHICH IS WHY THE DISCLOSURE IS PER RECORDING AND NOT PER
    COLUMN. The best lap is also a minimum over the session's laps: measured the same way it falls
    0.221 / 0.148 / 0.033 / 0.094 / 0.080 s per doubling on those five, i.e. FASTER than the ideal
    on two of them (D24 1 chapter and SD_30_08). Suppressing the ideal's ranking while leaving the
    best lap's alone would fix the smaller half of the problem on 2 of 5 recordings; naming the
    sample fixes both. See studio/library_dialog.py's Laps column.

    `corners` / `segments` are the partition's size, and they move when the corner detector re-runs
    — which it does on every start/finish-line drag. Measured over six start-line positions per
    recording the detected corner count moves 11↔12 on D24 and 7↔8 on Sandown, and the headline gap
    with it: D24 1 chapter 0.94 s at the fitted line, 1.08 … 1.48 s over those six positions
    (+58 %); Sandown 3 chapters 1.14 s, 1.16 … 1.93 s (+69 %). A mechanical midpoint refinement
    (every segment split in two, no new information) buys another 0.30 … 1.67 s. So a user who
    drags the line and sees the gap move is looking at a re-cut partition, not at their driving —
    and `corners`/`segments` on screen is what lets them see that."""

    donors: int    # distinct laps that won at least one segment (SegmentBests.donor_ids)
    laps: int      # clean laps the minimum ran over (SegmentBests.lap_ids)
    corners: int   # corners in the partition
    segments: int  # 2N+1 pieces the lap was cut into


@dataclass(frozen=True)
class SegmentGain:
    """One row of the ideal lap's DECOMPOSITION — where a subject lap's gap to the composite
    lives, and whether that gain is a PLAN or a taunt (`SegmentBests.decomposition`).

    `gain` is the seconds the subject gives away in this segment; the gains over all segments
    sum EXACTLY to `subject lap time − SegmentBests.total`, because a lap's segment times sum
    exactly to its lap time. `beat` is how many of the composite's laps drove this segment at
    least as fast as the subject did — the ACHIEVABILITY, and the difference between "you have
    done this 42 times" and "you did it once".

    `priority` is the order the surface presents: `gain × beat/n`. Both factors are on screen,
    so the ranking is checkable by eye against the two columns beside it."""

    index: int              # segment index into SegmentBests (0 … 2N)
    label: str              # "C4" / "C3 → C4" / "S/F → C1" — stats.straights_report's convention
    gain: float             # s the subject lap gives away here (≥ 0)
    beat: int               # laps that drove it at least as fast as the subject (includes it)
    n: int                  # laps admitted on this segment
    donor: int | None       # 0-based lap id owning the segment best (None on a POINT segment)
    ring_cid: int | None    # the corner a surface should point at for this row

    @property
    def priority(self) -> float:
        """gain × the share of laps that already matched the subject here. See the class note —
        and `SegmentBests.beat_counts` for why the share is not a fixed-tolerance hit rate."""
        return self.gain * self.beat / self.n if self.n else 0.0


@dataclass(frozen=True)
class SegmentBests:
    """The IDEAL LAP as a composite of the corner/straight partition — the per-segment minimum
    over the session's clean laps, and everything a caller needs to explain it.

    WHY a partition and not a distance envelope: `corners.segment_times` asserts that a lap's
    2N+1 segment times SUM EXACTLY to its lap time, so summing one lap's best C3 with another's
    best back straight double-counts nothing and drops nothing — the pieces tile the lap. A
    pointwise minimum of cumulative-elapsed curves (what this replaced) cannot make that claim:
    every lap's cumulative elapsed ends at its own lap time, so the min at the finish line is
    just the BEST LAP TIME and the "ideal" is a structural duplicate of it.

    Fields:
      labels    — 2N+1 segment names in track order, ["start", "C1", "C1-C2", …, "C{N}-finish"].
                  The MODEL's names; a surface prints `display_label`, which is the app's.
      cids      — the N corner ids in track order (Corner.cid), so a row can name the corner it
                  belongs to and point a map ring at it without re-reading the corner list.
      lap_ids   — the laps that contributed a row, in session order (see CornerModel.segment_bests
                  for the set).
      times     — (len(lap_ids), 2N+1) float: each lap's own segment times.
      admitted  — (len(lap_ids), 2N+1) bool: which cells may donate (see MIN_DONOR_SPAN_FRAC).
      bests     — 2N+1 minima over the admitted cells.
      donors    — 2N+1 lap ids, the argmin per segment; None for a POINT segment (best == 0),
                  where every lap ties at 0 and naming a winner would be arbitrary.
      s_edges   — 2N+2 normalized distances [0…1] of the partition edges on the REFERENCE
                  odometer — the x positions the cumulative ideal is defined at.
      donor_span— 2N+1 (enter, exit) odometer metres of each segment ON ITS DONOR'S own lap, so
                  the ideal curve can follow the donor's real pace through the segment instead of
                  a straight line (CornerModel.ideal_elapsed).
    """

    labels: list[str]
    cids: list[int]
    lap_ids: list[int]
    times: np.ndarray
    admitted: np.ndarray
    bests: list[float]
    donors: list[int | None]
    s_edges: list[float]
    donor_span: list[tuple[float, float]]

    @property
    def total(self) -> float:
        """The ideal lap time (s) — the sum of the per-segment minima. ≤ every donor lap's time,
        because each donor's own segments sum exactly to its lap time and a sum of minima can
        never exceed the minimum of those sums.

        IT IS AN ORDER STATISTIC, NOT A FLOOR, and every surface that prints it must print
        `sample` beside it. A minimum over more laps is never larger, so this number keeps falling
        as the session grows and moves again when the partition is re-cut — measured, tabulated
        and sourced in `IdealSample`. Nothing here is wrong; what would be wrong is showing the
        number without the counts that set it."""
        return float(sum(self.bests))

    @property
    def sample(self) -> IdealSample:
        """The four counts this composite is a minimum over — see `IdealSample`, which carries the
        measured table. One accessor so the Stats block, the hero chip and any future surface print
        the same four numbers rather than each deriving their own."""
        return IdealSample(donors=len(self.donor_ids()), laps=len(self.lap_ids),
                           corners=len(self.cids), segments=len(self.bests))

    def cumulative(self) -> np.ndarray:
        """The ideal's elapsed time at each partition edge (2N+2 values, 0 … total) — the ideal
        LAP CURVE's y, sampled at `s_edges`. Non-decreasing by construction (every segment time
        is ≥ 0), which is why the old envelope's `np.maximum.accumulate` repair is gone."""
        return np.concatenate(([0.0], np.cumsum(np.asarray(self.bests, float))))

    def donor_ids(self) -> list[int]:
        """The distinct laps the composite actually draws on, sorted. A correct ideal draws on
        more than one; exactly one means the ideal IS that lap (see `single_donor_id`)."""
        return sorted({d for d in self.donors if d is not None})

    def single_donor_id(self) -> int | None:
        """The lap id when ONE lap wins every non-point segment — the ideal is then that lap, not
        a synthetic one, and a surface must say so rather than print a duplicate of it. None when
        the composite is genuinely stitched (the normal case) or draws on no segment at all."""
        ids = self.donor_ids()
        return ids[0] if len(ids) == 1 else None

    def gains_vs(self, lap_id: int) -> list[float] | None:
        """Per segment, how much time `lap_id` leaves on the table there (its own time minus the
        segment best, ≥ 0). None when that lap did not contribute a row. This is the ideal's
        DECOMPOSITION — where the gap lives, not just how big it is."""
        if lap_id not in self.lap_ids:
            return None
        row = self.times[self.lap_ids.index(lap_id)]
        return [float(row[j] - self.bests[j]) for j in range(len(self.bests))]

    def display_label(self, j: int) -> str:
        """Segment `j`'s name as the app writes it: a corner is "C4", a straight is "C3 → C4"
        with the timing line spelled "S/F" at both ends ("S/F → C1", "C12 → S/F").

        This is `stats.straights_report`'s convention, deliberately and not by coincidence: the
        STRAIGHTS table and the ideal-lap decomposition list the SAME pieces of track a few
        hundred pixels apart, and two spellings of one segment would read as two segments.
        tests/test_stats_panel_realqt.py pins the two against each other."""
        if j % 2:
            return f"C{self.cids[(j - 1) // 2]}"
        left = f"C{self.cids[j // 2 - 1]}" if j else "S/F"
        right = f"C{self.cids[j // 2]}" if j // 2 < len(self.cids) else "S/F"
        return f"{left} → {right}"

    def ring_cid(self, j: int) -> int | None:
        """The corner segment `j` should point a map ring at: itself if it is a corner, and the
        corner FEEDING it if it is a straight — including the wrap, where the S/F straight is fed
        by the last corner (again `stats.straights_report`'s rule, `ring_cid`)."""
        if not self.cids:
            return None
        return self.cids[(j - 1) // 2] if j % 2 else self.cids[j // 2 - 1]

    def beat_counts(self, lap_id: int) -> list[tuple[int, int]] | None:
        """Per segment, (laps that drove it at least as fast as `lap_id` did, laps admitted
        there) — ACHIEVABILITY. None when that lap did not contribute a row. Ties count, so the
        subject always counts itself and the first number is never 0.

        THIS REPLACES `hit_counts(tol)`, WHICH MEASURED SEGMENT LENGTH. That method counted laps
        within a FIXED 0.1 s of the segment best, over segments whose own duration on these
        recordings runs 0.16 s to 10.07 s — so the tolerance was 62 % of one segment and 1 % of
        another, and the "achievability" it reported correlated with segment DURATION at
        r = −0.95 / −0.82 / −0.80 / −0.95 on the four real recordings. Ranking by
        gain × that rate put a 0.039 s straight at the top of D24's plan, above a 0.213 s corner.

        THIS COUNT IS SCALE-FREE BY CONSTRUCTION, WHICH IS A STRONGER CLAIM THAN A CORRELATION AND
        IS THE ONE THAT IS TRUE. It is the subject's RANK among the admitted laps: multiply any
        segment's column by an arbitrary c > 0 and `times[:, j] <= row[j]` is unchanged, while
        `admitted` is a function of the SPANS and not of the times at all — so every pair this
        returns is identical. `hit_counts(0.1)` is not: the same rescaling changes its answer.
        Both halves are pinned, with the negative control, by
        tests/test_session_pure.py::test_beat_counts_are_not_a_fixed_tolerance_hit_rate.

        The correlation this docstring used to lead with ("r = −0.04 … −0.50 against the same
        durations") does not reproduce and understated the coupling. Re-measured independently on
        FIVE recordings, subject = the best lap, non-point segments only, beat rate against each
        segment's mean duration:

        | recording      | n  | r      | Spearman | permutation p |
        |----------------|----|--------|----------|---------------|
        | D24 1 ch       | 24 | −0.176 | −0.239   | 0.264 |
        | D24 3 ch       | 24 | −0.340 | −0.331   | 0.117 |
        | Sandown ch 1   | 15 | −0.297 | −0.289   | 0.297 |
        | Sandown 3 ch   | 15 | −0.602 | −0.596   | **0.019** |
        | SD_30_08       |  5 | −0.905 | −0.900   | 0.067 (exact, 120 permutations) |

        One of five is distinguishable from chance, and the strongest r sits on the recording with
        FIVE segments, where n makes p ≥ 0.017 unreachable at any effect size. A residual negative
        correlation is also what a real track produces — a short piece of road has less room to
        differ, so more laps land level with the subject — and because the statistic is provably
        invariant to scale, that correlation is a fact about the driving, not about the units. The
        rejected `hit_counts` had no such defence: its coupling came from a tolerance measured in
        seconds against segments of unequal length.

        It is also the question a driver is actually asking: have I been here before, or was that
        once? And both of its factors are printed as columns beside the row, with the product in
        the row's own tooltip, so the ranking is checkable by eye — the app's standing answer to a
        ranking whose factor a reader cannot see (stats_panel's note at the CORNERS table).

        It is deliberately measured against the SUBJECT lap rather than against the segment best.
        The gain a row shows is the distance from the subject to the best, and the honest
        achievability question about that gain is how routinely the subject's own time there is
        beaten — not how many laps landed inside an arbitrary window around a single minimum."""
        if lap_id not in self.lap_ids:
            return None
        row = self.times[self.lap_ids.index(lap_id)]
        out = []
        for j in range(len(self.bests)):
            adm = self.admitted[:, j]
            out.append((int(((self.times[:, j] <= row[j]) & adm).sum()), int(adm.sum())))
        return out

    def decomposition(self, lap_id: int) -> list[SegmentGain] | None:
        """`lap_id`'s gap to the composite, segment by segment, ranked most-actionable first
        (`SegmentGain.priority` = gain × beat/n). None when that lap did not contribute a row.

        Every segment appears, including the 0.00 s ones — a caller decides what is worth
        showing and must account for the rest, because the gains SUM to the headline gap and a
        top-N slice that does not say so is a table contradicting the tile above it.

        One exception, and it is a correctness exception rather than a display one: a segment the
        subject lap is not ADMITTED on is dropped. `corners.project_boundaries` can collapse a
        segment to zero width on one lap and push its time into the neighbour, so that lap's
        `gain` there is measured against a time it never drove (and its neighbour's is inflated
        by the same amount). The pair still sums correctly — which is why the headline is safe —
        but neither number is advice. Not observed on any of the owner's recordings; guarded
        because the admission rule exists precisely because it does happen."""
        gains = self.gains_vs(lap_id)
        beats = self.beat_counts(lap_id)
        if gains is None or beats is None:
            return None
        i = self.lap_ids.index(lap_id)
        rows = [SegmentGain(index=j, label=self.display_label(j), gain=gains[j],
                            beat=beats[j][0], n=beats[j][1], donor=self.donors[j],
                            ring_cid=self.ring_cid(j))
                for j in range(len(self.bests)) if self.admitted[i, j]]
        return sorted(rows, key=lambda r: -r.priority)


class CornerModel:
    """Corner detection + per-corner per-lap stats over Session-bound primitives.

    All inputs are Session-bound callables (Session owns the pacer side + its memoized lap sets /
    per-lap caches / active reference): `best_lap_id` / `valid_lap_ids` / `lap_has_dropout` are the
    memoized lap-set accessors; `lap_columns` / `lap_arrays` / `lap_time_dist` are the per-lap
    array fetches; `reference` returns the active cross-recording ReferenceLap (or None). The corner
    math is just numpy on those arrays. `reference_id` is Session.REFERENCE_ID, the sentinel the
    reference stats are parked under.
    """

    def __init__(self, *, reference_id: int,
                 best_lap_id: Callable[[], int | None],
                 valid_lap_ids: Callable[[], list[int]],
                 lap_has_dropout: Callable[[int], bool],
                 lap_columns: Callable[[int], tuple],
                 lap_arrays: Callable[[int], tuple],
                 lap_time_dist: Callable[[int], tuple | None],
                 reference: Callable[[], object | None]):
        self._reference_id = reference_id
        self._best_lap_id = best_lap_id
        self._valid_lap_ids = valid_lap_ids
        self._lap_has_dropout = lap_has_dropout
        self._lap_columns = lap_columns
        self._lap_arrays = lap_arrays
        self._lap_time_dist = lap_time_dist
        self._reference = reference
        self._basis_cache: object = _UNSET  # (corners, total_ref) or None
        self._stats_cache: dict[int, list[corners.CornerStat]] = {}  # per-lap stats + the reference's own under reference_id
        self._bests_cache: object = _UNSET  # per-corner session-best time
        self._segment_bests_cache: object = _UNSET  # the ideal-lap segment composite

    def invalidate(self) -> None:
        """Drop EVERY corner cache — called from Session.set_timing_lines (the single
        re-segmentation point): the corner set is detected on + projected through the
        segmentation, so all of them are stale after a timing-line change."""
        self._basis_cache = _UNSET
        self._stats_cache.clear()
        self._bests_cache = _UNSET
        self._segment_bests_cache = _UNSET

    def invalidate_stats(self) -> None:
        """Drop ONLY the per-lap stats (not the corner detection) — called from
        Session.set_reference_session / clear_reference: the per-corner Δ baseline switched
        (best lap <-> reference lap), so every cached per-lap stat delta is stale, but the
        corner windows themselves are unchanged. Recomputed lazily against the new baseline."""
        self._stats_cache.clear()

    # ----------------------------------------------------------- drift-gate spatial traces
    def _best_trace(self) -> tuple | None:
        """The best (reference) lap's local-frame trace (xs, ys, cum) — the spatial anchor side of
        the per-corner drift gate (corners.project_boundaries). None when there is no usable best
        lap. The reference-odometer corner windows are expressed in this lap's frame, so it is the
        fixed half of every (ref, comparison) trace pair."""
        best = self._best_lap_id()
        if best is None:
            return None
        _t, xs, ys, _v, cum = self._lap_columns(best)
        if len(cum) < 2 or float(cum[-1]) <= 0:
            return None
        return xs, ys, cum

    def _lap_traces(self, lap_id: int, ref_trace: tuple | None) -> tuple | None:
        """The (ref_xs, ref_ys, ref_cum, lap_xs, lap_ys, lap_cum) trace pair the drift gate's
        spatial fallback needs to map this session's corner windows onto `lap_id`. None (→ the
        gate keeps the normalized projection) when either trace is degenerate. A reference-lap
        (cross-recording) projection has no local trace pair, so it stays normalized."""
        if ref_trace is None:
            return None
        _t, xs, ys, _v, cum = self._lap_columns(lap_id)
        if len(cum) < 2 or float(cum[-1]) <= 0:
            return None
        return (*ref_trace, xs, ys, cum)

    # ------------------------------------------------------------------ basis + corners
    def basis(self) -> tuple[list[corners.Corner], float] | None:
        """The cached (corner list, reference total distance) pair, or None when there is no
        usable best lap. The reference total is the best lap's odometer length — the basis
        the corner windows (and the delta plot's distance axis) are expressed in."""
        if self._basis_cache is not _UNSET:
            return self._basis_cache
        self._basis_cache = None
        best = self._best_lap_id()
        if best is not None:
            _t, _xs, _ys, _v, cum_best = self._lap_columns(best)
            if len(cum_best) >= 8 and float(cum_best[-1]) > 0:
                total_ref = float(cum_best[-1])
                # The median curvature profile pools the session's clean laps (valid, no GPS
                # dropout); the best lap is always included so a session where every lap is
                # dropout-flagged still detects on the best lap alone.
                ids = [i for i in self._valid_lap_ids() if not self._lap_has_dropout(i)]
                if best not in ids:
                    ids.append(best)
                traces = []
                for lid in ids:
                    _lt, xs, ys, _lv, cum = self._lap_columns(lid)
                    traces.append((xs, ys, cum))
                d_grid, kappa = corners.pooled_curvature(traces, total_ref)
                self._basis_cache = (corners.detect_corners(d_grid, kappa), total_ref)
        return self._basis_cache

    def corner_list(self) -> list[corners.Corner]:
        """The detected corners (C1… in track order) in best-lap odometer metres. [] when
        no best lap exists. Computed once per segmentation (see basis)."""
        basis = self.basis()
        return basis[0] if basis is not None else []

    # ------------------------------------------------------------------ per-lap stats
    def reference_corner_stats(self) -> list[corners.CornerStat] | None:
        """The cross-recording reference lap's per-corner stats projected onto THIS session's
        corner windows (the same normalized-distance projection any local lap uses), or None
        when no reference is loaded. Cached under the reference sentinel key; invalidated when
        the reference or the segmentation changes (invalidate_stats / invalidate)."""
        ref = self._reference()
        if ref is None:
            return None
        got = self._stats_cache.get(self._reference_id)
        if got is not None:
            return got
        basis = self.basis()
        if basis is None or not basis[0]:
            return None
        corner_list, total_ref = basis
        dist, speed_kmh, elapsed = ref.arrays()
        if len(dist) < 2 or float(dist[-1]) <= 0:
            return None
        # ref=None: the reference IS the baseline (self-deltas 0).
        stats = corners.lap_corner_stats(corner_list, total_ref, dist, speed_kmh, elapsed,
                                         ref=None)
        self._stats_cache[self._reference_id] = stats
        return stats

    def lap_corner_stats(self, lap_id: int) -> list[corners.CornerStat]:
        """Per-corner metrics for one lap (time-in-corner, apex/entry/exit speeds, deltas vs
        the baseline's same corner). [] for a degenerate lap or when no corners were detected.
        Cached per lap; cleared on re-segment (and on a reference change via invalidate_stats).

        The Δ baseline is the local best lap normally, or the CROSS-RECORDING reference lap's
        projected corner stats when one is loaded (F7)."""
        got = self._stats_cache.get(lap_id)
        if got is not None:
            return got
        basis = self.basis()
        best = self._best_lap_id()
        if basis is None or not basis[0] or best is None:
            return []
        corner_list, total_ref = basis
        dist, speed_kmh, elapsed = self._lap_arrays(lap_id)
        if len(dist) < 2 or float(dist[-1]) <= 0:
            return []
        ref_stats = self.reference_corner_stats()
        if ref_stats is not None:
            ref = ref_stats
        else:
            ref = self.lap_corner_stats(best) if lap_id != best else None
        # Drift-gate spatial traces: the best lap (the corner-window reference frame) + this lap.
        # The best lap itself projects onto its OWN odometer (zero drift → identity), so its trace
        # pair is harmless; a degenerate trace → None → normalized projection (unchanged).
        traces = self._lap_traces(lap_id, self._best_trace())
        stats = corners.lap_corner_stats(corner_list, total_ref, dist, speed_kmh, elapsed,
                                         ref=ref or None, traces=traces)
        self._stats_cache[lap_id] = stats
        return stats

    def corner_session_bests(self) -> list[float]:
        """Per-corner session-best time-in-corner across all VALID laps (the purple-cell
        convention, matching the per-sector session bests). [] when no corners. Cached;
        cleared on re-segment."""
        if self._bests_cache is not _UNSET:
            return self._bests_cache
        per_lap = [self.lap_corner_stats(i) for i in self._valid_lap_ids()]
        per_lap = [st for st in per_lap if st]
        n = len(self.corner_list())
        self._bests_cache = [
            min(st[i].time for st in per_lap) for i in range(n)
        ] if per_lap and n else []
        return self._bests_cache

    # ------------------------------------------------------------------ ideal lap (D1)
    def _composite_lap_ids(self) -> list[int]:
        """The laps the ideal composite may draw on: `Session.consistency_lap_ids()` — VALID and
        DROPOUT-FREE, the same set every consistency statistic runs on. A dropout lap's distance
        is speed-integral reconstructed, so its segment boundaries (and therefore its segment
        TIMES) are exactly the ones that must not be allowed to win a segment.

        The BEST lap is appended when the dropout rule excluded it — the same guarantee
        `basis()` makes for the detection profile. It is what keeps `total <= best lap time`
        true in the degenerate session where every valid lap is dropout-flagged and
        `best_candidate_ids` fell back to the flagged set."""
        ids = [i for i in self._valid_lap_ids() if not self._lap_has_dropout(i)]
        best = self._best_lap_id()
        if best is not None and best not in ids:
            ids.append(best)
        return ids

    def segment_bests(self) -> SegmentBests | None:
        """The IDEAL LAP: the per-segment minimum of the corner/straight partition over the
        clean laps (`_composite_lap_ids`), with its donors, its per-lap matrix and the partition
        edges. None when there is no corner partition to composite on — no basis, or no corner
        detected, in which case the "partition" is the whole lap and its minimum is just the best
        lap time. That degenerate value is NOT returned dressed as an ideal: callers hide it,
        following the precedent at stats_panel/export_data.

        Each lap's times come from `corners.segment_times`, which asserts they sum exactly to
        that lap's time — the guarantee that makes a cross-lap composite legitimate. A lap is
        refused a segment whose projected span collapsed on it (MIN_DONOR_SPAN_FRAC).

        Cached; cleared on re-segment (`invalidate`)."""
        if self._segment_bests_cache is not _UNSET:
            return self._segment_bests_cache
        self._segment_bests_cache = None
        basis = self.basis()
        if basis is None or not basis[0]:
            return None
        corner_list, total_ref = basis

        # Partition edges on the REFERENCE odometer: start, each corner's enter/exit, finish.
        # This is the shared frame every lap's own edges are projected FROM, so it is where the
        # composite curve's x lives. (Each lap's own edges are `corners.project_boundaries` of
        # these onto its odometer — computed per lap inside segment_times.)
        ref_edges = [0.0]
        for c in corner_list:
            ref_edges.extend((float(c.enter), float(c.exit)))
        ref_edges.append(float(total_ref))
        ref_span = np.diff(np.asarray(ref_edges, float))
        # A POINT segment (corner starts on the line, or two corners nearly touch) carries ~0 s on
        # every lap; nobody can collapse it further, so every lap is admitted there.
        is_point = ref_span <= POINT_SPAN_M
        floor = np.where(is_point, -1.0, MIN_DONOR_SPAN_FRAC * ref_span)

        labels = ["start"]
        for i, c in enumerate(corner_list):
            labels.append(c.label)
            nxt = corner_list[i + 1].label if i + 1 < len(corner_list) else "finish"
            labels.append(f"{c.label}-{nxt}")

        ref_trace = self._best_trace()
        rows, spans, edges, lap_ids = [], [], [], []
        for lid in self._composite_lap_ids():
            dist, _speed_kmh, elapsed = self._lap_arrays(lid)
            if len(dist) < 2 or float(dist[-1]) <= 0:
                continue
            traces = self._lap_traces(lid, ref_trace)
            total_lap = float(dist[-1])
            rows.append(corners.segment_times(corner_list, total_ref, dist, elapsed, traces))
            # The same edges segment_times interpolates at — the admission test's input, and the
            # donor's own odometer frame for the ideal curve. project_boundaries is the public
            # half of corners._window_edges; the two constant endpoints (0, total_lap) are never
            # projected, so they cannot collapse.
            interior = corners.project_boundaries(ref_edges[1:-1], total_ref, total_lap,
                                                  traces=traces)
            lap_edges = np.concatenate(([0.0], interior, [total_lap]))
            edges.append(lap_edges)
            spans.append(np.diff(lap_edges))
            lap_ids.append(lid)
        if not rows:
            return None

        times = np.asarray(rows, float)
        edges = np.asarray(edges, float)
        admitted = np.asarray(spans, float) >= floor[None, :]
        # A segment no lap is admitted on cannot happen while a point segment admits everyone,
        # but a min over an empty set would be inf — fall back to the whole column rather than
        # poison the total.
        for j in range(times.shape[1]):
            if not admitted[:, j].any():
                admitted[:, j] = True
        masked = np.where(admitted, times, np.inf)
        bests = [float(v) for v in masked.min(axis=0)]
        donors: list[int | None] = [
            None if bests[j] <= 0.0 else lap_ids[int(masked[:, j].argmin())]
            for j in range(times.shape[1])
        ]
        self._segment_bests_cache = SegmentBests(
            labels=labels, cids=[int(c.cid) for c in corner_list], lap_ids=lap_ids,
            times=times, admitted=admitted, bests=bests,
            donors=donors,
            s_edges=[e / total_ref for e in ref_edges] if total_ref > 0 else ref_edges,
            donor_span=[(0.0, 0.0) if donors[j] is None else
                        (float(edges[lap_ids.index(donors[j]), j]),
                         float(edges[lap_ids.index(donors[j]), j + 1]))
                        for j in range(times.shape[1])],
        )
        return self._segment_bests_cache

    def ideal_elapsed(self, s_grid) -> np.ndarray | None:
        """The IDEAL LAP's elapsed-time curve sampled at normalized distances `s_grid` ∈ [0,1] —
        the y of `Session.ideal_lap_elapsed`. None when there is no composite.

        Inside each segment the curve follows THE DONOR'S OWN PACE, not a straight line: the ideal
        lap is a real drive (this lap's C3, that lap's back straight), so its shape through a
        segment is the shape its donor drove. The donor's segment time is already exactly the
        segment's best, so replaying its profile lands on the right value at both edges with no
        rescaling — the curve is exact at the edges and honest in between.

        WHY IT MATTERS, measured: drawing each segment as a straight line in (distance, time)
        instead sent `Session.delta_to_ideal_at` to −0.87 s on 18.4 % of samples on the Sandown
        recording — a 163 m / 9.9 s corner is nowhere near constant pace, so the line is nowhere
        near anything anybody drove. Following the donor cuts the worst excursion to **−0.159 s**
        (2.09 % of samples on Sandown chapter 1, 6.42 % on SD_30_08 — the per-recording table is
        in `theme.format_ideal_run`'s note), which is then a real "you were up on the ideal through
        here" rather than an artefact of the drawing.

        Those two figures read −0.052 s / "under 1 %" until this sweep was redone: the original was
        measured on a fixture set that substituted Sandown chapter **3** — one valid lap, so the
        excursion is identically zero — for chapter 1, which is where the real floor is. See
        `Session.delta_to_ideal` for the full note; the lesson is that the sweep passed because its
        fixture could not express the property, not because the bound held."""
        sb = self.segment_bests()
        if sb is None:
            return None
        s = np.asarray(s_grid, float)
        s_edges = np.asarray(sb.s_edges, float)
        cum = sb.cumulative()
        n_seg = len(sb.bests)
        # Each grid point belongs to the segment whose [s_edges[j], s_edges[j+1]) contains it.
        idx = np.clip(np.searchsorted(s_edges, s, side="right") - 1, 0, n_seg - 1)
        out = cum[idx].copy()
        for j in range(n_seg):
            here = idx == j
            lo_s, hi_s = float(s_edges[j]), float(s_edges[j + 1])
            donor = sb.donors[j]
            # A point segment (or a segment nobody donates) contributes its whole time at once:
            # the curve steps at the edge and there is no interior to shape.
            if not here.any() or donor is None or hi_s <= lo_s:
                continue
            d_lo, d_hi = sb.donor_span[j]
            if d_hi <= d_lo:
                continue
            dist, _speed_kmh, elapsed = self._lap_arrays(donor)
            frac = (s[here] - lo_s) / (hi_s - lo_s)
            d_on_donor = d_lo + frac * (d_hi - d_lo)
            t0 = float(np.interp(d_lo, dist, elapsed))
            out[here] = cum[j] + (np.interp(d_on_donor, dist, elapsed) - t0)
        return out

    # ------------------------------------------------------------------ map / seek glue
    def corner_map_markers(self) -> list[tuple[str, float, float, int]]:
        """(label, x, y, direction) per corner — the apex position in LOCAL metres on the
        best lap's trace, for the map's corner labels. [] when no corners/best lap."""
        basis = self.basis()
        best = self._best_lap_id()
        if basis is None or not basis[0] or best is None:
            return []
        corner_list, _total_ref = basis
        _t, xs, ys, _v, cum = self._lap_columns(best)
        apexes = np.asarray([c.apex for c in corner_list])
        mx = np.interp(apexes, cum, xs)
        my = np.interp(apexes, cum, ys)
        return [(c.label, float(mx[i]), float(my[i]), c.direction)
                for i, c in enumerate(corner_list)]

    def corner_entry_media_time(self, lap_id: int, cid: int) -> float | None:
        """Media-clock time (s) `lap_id` enters corner `cid` — the jump-to seek target. Projects
        the corner's enter point onto this lap's odometer and reads elapsed->media there. None if
        unknown/degenerate. Absolute (lap start + elapsed)."""
        basis = self.basis()
        if basis is None or not basis[0]:
            return None
        corner_list, total_ref = basis
        corner = next((c for c in corner_list if c.cid == cid), None)
        if corner is None:
            return None
        td = self._lap_time_dist(lap_id)
        if td is None:
            return None
        times, dists = td
        total_lap = float(dists[-1])
        if total_lap <= 0:
            return None
        d_enter = corner.enter / total_ref * total_lap  # project onto THIS lap's odometer
        return float(np.interp(d_enter, dists, times))
