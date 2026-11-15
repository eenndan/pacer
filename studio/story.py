"""The session's story: WHERE a slow lap's time went, and a few session moments with their ties.

Qt-free and pacer-free. `Session.slow_lap_notes()` / `Session.session_moments()` feed it the clean
laps' times (`consistency_lap_ids`: valid, no GPS dropout) and the CORNERS BY LAP grid
(`Session.corner_matrix`); every judgement here is that grid's own (`stats.CornerMatrix`: its
typical pass per corner and its ▼ step), never a second rule. It LABELS laps; it never removes,
reorders or excuses one (refusal §14), scores no consistency and praises nothing.

SLOW LAP. A clean lap is slow when it is more than max(the clean laps' IQR, SLOW_MIN_MARGIN_S) over
their median lap: a DEFAULT, chosen by the W4 design lane on few sessions, and every note carries
the rule as text (`SlowLapNote.rule`) so a surface can say so. Its EXCESS is its time over that
median lap. Each corner's share of the excess is the corner's time over its typical pass (the
grid's median), read at print resolution exactly as the grid's heat shade reads it.

WHY CORNERS AGAINST THEIR TYPICAL PASS, NOT AGAINST THE BEST LAP. A lap's corner times are the odd
pieces of `corners.segment_times`' exact 2N+1 partition (the grid reads the same numbers), so the
pieces sum to the lap time — but corner Δs against the BEST lap do not sum to the lap gap: the
straights hold the rest (the design lane: 29 % of it on the demo). And only against the typical pass
is there a noise gate, the ▼ step.

CATEGORIES (only a corner BEYOND ITS ▼ STEP may be named — `CornerMatrix.is_behind`):
  * `one`     — the worst ▼ corner holds more than CULPRIT_SHARE of the excess;
  * `two`     — the two worst ▼ corners together do;
  * `partial` — a ▼ corner exists, but under that share: it is named, and the rest is "spread";
  * `spread`  — no corner beyond its ▼ step.
STRAIGHTS ARE NEVER NAMED. A straight has no ▼ step today (the grid has corner columns only), so
there is no honest noise gate to say "the time went on the straight after C4" — such a lap reads
`spread` (or `partial`). Inventing a straight gate here would be a second rule the grid does not
show.

WHICH LAPS. Only the grid's rows: a ⊘ excluded or ⚠ dropout lap is never a row (its corner windows
are cut from a speed-integrated odometer), so it gets no note and is in no moment. A blank row (the
lap projected no corners) gets no note. A cell the grid mutes (an edge not matched on track) is never
▼; a `spread` lap with muted cells says which corners were not measured.

TIMING TRUST, ONE RULE, the grid's: the grid keeps its ▼ under a provisional start line or an
estimated clock (corner windows are matched on track, not timed from the line) and demotes only
lap times. So notes and moments exist in both states and carry `caveat`; the lap-time pieces follow
the Laps table — under a provisional line it blanks Δbest and withholds the ★, so a note has no
`gap_to_best_s` and there is no best-lap moment.

MOMENTS (each None rather than invented): the best lap, with every clean lap the Laps table calls
level with it (printed gap under `library.PB_PRECISION_S`) named as tied; the biggest single-corner
loss beyond its ▼ step; the longest run of consecutive clean laps with no ▼; the best 5-lap run by
median. Not built — the lane measured them unstable under timing jitter: a best 3-lap run, a best
pass per corner (always a tie of 2-14 passes).

STABILITY, held by tests/test_story.py on the demo (200 seeded draws): with lap times ±0.07 s and
corner cells ±0.035 s the biggest loss and the longest ▼-less run never change. The best lap's tie
list always holds the true best when each lap moves by up to ±0.035 s (a GAP by up to 0.07 s, the
floor's own definition); at ±0.07 s per lap it loses it in 22 of 200 draws, so that is not claimed.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from ._signal import fmt_signed, fmt_time, lap_label, plural
from .library import PB_PRECISION_S, within_timing_precision
from .stats import MATRIX_DECIMALS, CornerMatrix

#: A lap is slow above its median clean lap by more than the larger of the clean laps' IQR and
#: this (s). A default picked on few sessions (W4 design lane), stated in every note's `rule`.
SLOW_MIN_MARGIN_S = 0.5
#: The share of a slow lap's excess one (or two) ▼ corners must hold, STRICTLY, to be its culprit.
CULPRIT_SHARE = 0.5
#: The window of the best-run moment, in consecutive clean laps.
RUN_WINDOW = 5
#: The shortest run of consecutive ▼-less clean laps that is a moment at all.
MIN_CLEAN_RUN = 2
#: The grid's mark, spelled here because `theme.DELTA_BEHIND_ARROW` lives in a Qt module.
BEHIND_MARK = "▼"

ONE, TWO, PARTIAL, SPREAD = "one", "two", "partial", "spread"
CAVEAT_PROVISIONAL = "provisional"
CAVEAT_ESTIMATED = "estimated"
BEST_LAP, BIGGEST_LOSS, CLEAN_RUN, BEST_WINDOW = "best_lap", "biggest_loss", "clean_run", "best_window"


def printed_ms(seconds: float) -> int:
    """`seconds` as the whole milliseconds `fmt_time` prints — a copy of `lap_table.printed_ms`,
    which lives in a Qt module (tests/test_story.py holds the two equal; extracting it is parked)."""
    m, sec = divmod(seconds, 60)
    return int(m) * 60_000 + round(float(f"{sec:.3f}") * 1000)


@dataclass(frozen=True)
class CornerLoss:
    """One corner of a lap against its typical pass: `loss_s` is the gap at print resolution."""

    cid: int
    loss_s: float
    typical_s: float
    step_s: float


@dataclass(frozen=True)
class SlowLapNote:
    """Where one slow lap's time went. `corners` are the ▼ corners named, worst first (empty for
    `spread`); `unmeasured` the cids whose cell the grid mutes."""

    lap_id: int
    gap_to_best_s: float | None     # on the printed ms, as the Laps table's Δbest; None provisional
    excess_s: float                 # over the median clean lap, at print resolution
    category: str
    corners: tuple[CornerLoss, ...]
    unmeasured: tuple[int, ...]
    caveat: str | None
    rule: str
    text: str


@dataclass(frozen=True)
class Mention:
    """A lap (or run of laps) and, for a corner moment, its corner, with the moment's value."""

    lap_ids: tuple[int, ...]
    cid: int | None
    value: float


@dataclass(frozen=True)
class Moment:
    """One session moment: the named laps/corner and value, every `tie` named with it, its text."""

    kind: str
    lap_ids: tuple[int, ...]
    cid: int | None
    value: float
    tie: tuple[Mention, ...]
    caveat: str | None
    text: str


@dataclass(frozen=True)
class SessionMoments:
    best_lap: Moment | None
    biggest_loss: Moment | None
    clean_run: Moment | None
    best_window: Moment | None


def caveat_for(provisional: bool, clock_estimated: bool) -> str | None:
    return CAVEAT_PROVISIONAL if provisional else CAVEAT_ESTIMATED if clock_estimated else None


def slow_threshold(lap_times: Mapping[int, float]) -> tuple[float, float, float] | None:
    """(median lap, IQR, margin) over the clean laps, or None with none."""
    v = [float(t) for t in lap_times.values() if np.isfinite(t)]
    if not v:
        return None
    q1, med, q3 = (float(x) for x in np.percentile(v, [25, 50, 75]))
    return med, q3 - q1, max(q3 - q1, SLOW_MIN_MARGIN_S)


def slow_rule_text(median: float, iqr: float, margin: float) -> str:
    return (f"Slow: more than {margin:.2f} s over your median clean lap ({fmt_time(median)}), the "
            f"larger of the clean laps' IQR ({iqr:.2f} s) and {SLOW_MIN_MARGIN_S:.2f} s. A default "
            "chosen on few sessions.")


def _loss(matrix: CornerMatrix, row: int, col: int) -> float | None:
    """The cell's gap to its typical pass at print resolution (`CornerMatrix.heat`'s reading)."""
    val, med = matrix.cells[row][col], matrix.medians[col]
    if val is None or med is None:
        return None
    return round(round(val, MATRIX_DECIMALS) - med, MATRIX_DECIMALS)


def _corner_loss(matrix: CornerMatrix, row: int, col: int) -> CornerLoss:
    return CornerLoss(cid=matrix.cids[col], loss_s=float(_loss(matrix, row, col) or 0.0),
                      typical_s=float(matrix.medians[col] or 0.0),
                      step_s=float(matrix.scales[col] or 0.0))


def _pass(c: CornerLoss) -> str:
    return f"C{c.cid} {fmt_signed(c.loss_s)} s"


def _body(category: str, named: tuple[CornerLoss, ...], unmeasured: tuple[int, ...]) -> str:
    if category == ONE:
        return f"{_pass(named[0])} over your typical pass ({BEHIND_MARK})"
    if category == TWO:
        return f"{_pass(named[0])} and {_pass(named[1])} over your typical passes ({BEHIND_MARK})"
    if category == PARTIAL:
        marks = ", ".join(f"C{c.cid} {BEHIND_MARK} {fmt_signed(c.loss_s)} s" for c in named)
        return f"{marks}; the rest is spread across the lap"
    tail = (f" ({', '.join(f'C{c}' for c in unmeasured)} not matched on track)"
            if unmeasured else "")
    return f"Spread across the lap: no corner beyond its {BEHIND_MARK} step{tail}"


def slow_lap_notes(lap_times: Mapping[int, float], matrix: CornerMatrix | None,
                   best_lap: int | None, *, provisional: bool = False,
                   clock_estimated: bool = False) -> list[SlowLapNote]:
    """One note per slow clean lap that is a row of `matrix`, in session order. [] without a grid
    (fewer than `stats.MATRIX_MIN_LAPS` clean laps, or no corners)."""
    th = slow_threshold(lap_times)
    if matrix is None or th is None:
        return []
    median, iqr, margin = th
    rule = slow_rule_text(median, iqr, margin)
    caveat = caveat_for(provisional, clock_estimated)
    best_ms = (printed_ms(lap_times[best_lap])
               if best_lap is not None and best_lap in lap_times and not provisional else None)
    out = []
    for row, lap in enumerate(matrix.lap_ids):
        t = lap_times.get(lap)
        if t is None or not t - median > margin or all(c is None for c in matrix.cells[row]):
            continue
        excess = round(t - median, MATRIX_DECIMALS)
        cols = range(len(matrix.cids))
        behind = sorted((c for c in cols if matrix.is_behind(row, c)),
                        key=lambda c: -(_loss(matrix, row, c) or 0.0))
        losses = [_loss(matrix, row, c) or 0.0 for c in behind]
        if not behind:
            category, named = SPREAD, ()
        elif losses[0] / excess > CULPRIT_SHARE:
            category, named = ONE, behind[:1]
        elif len(behind) >= 2 and (losses[0] + losses[1]) / excess > CULPRIT_SHARE:
            category, named = TWO, behind[:2]
        else:
            category, named = PARTIAL, behind
        corners = tuple(_corner_loss(matrix, row, c) for c in named)
        unmeasured = tuple(matrix.cids[c] for c in cols if not matrix.resolved[row][c])
        gap = (printed_ms(t) - best_ms) / 1000 if best_ms is not None else None
        head = f"Lap {lap_label(lap)}"
        if gap is not None:
            head += f" · {fmt_signed(gap)} s off your best"
        out.append(SlowLapNote(
            lap_id=lap, gap_to_best_s=gap, excess_s=excess, category=category, corners=corners,
            unmeasured=unmeasured, caveat=caveat, rule=rule,
            text=f"{head} · {_body(category, corners, unmeasured)}"))
    return out


# ------------------------------------------------------------------------------------- moments
def _laps_text(ids: tuple[int, ...]) -> str:
    if len(ids) == 1:
        return f"Lap {lap_label(ids[0])}"
    return f"Laps {lap_label(ids[0])}–{lap_label(ids[-1])}"


def _ties_text(tie: tuple[Mention, ...]) -> str:
    return f" · ≈ {', '.join(_laps_text(m.lap_ids) for m in tie)}" if tie else ""


def _best_lap(lap_times, best_lap, provisional, caveat) -> Moment | None:
    if provisional or best_lap not in lap_times:
        return None
    best_ms = printed_ms(lap_times[best_lap])
    tie = tuple(Mention((lap,), None, float(t)) for lap, t in sorted(lap_times.items())
                if lap != best_lap
                and within_timing_precision((printed_ms(t) - best_ms) / 1000))
    t = float(lap_times[best_lap])
    note = f" (within {PB_PRECISION_S:.2f} s: timing precision)" if tie else ""
    return Moment(BEST_LAP, (best_lap,), None, t, tie, caveat,
                  f"Best lap: Lap {lap_label(best_lap)} · {fmt_time(t)}{_ties_text(tie)}{note}")


def _biggest_loss(matrix: CornerMatrix, caveat) -> Moment | None:
    cells = sorted(((_loss(matrix, r, c) or 0.0, lap, matrix.cids[c])
                    for r, lap in enumerate(matrix.lap_ids) for c in range(len(matrix.cids))
                    if matrix.is_behind(r, c)), key=lambda x: (-x[0], x[1], x[2]))
    if not cells:
        return None
    top, lap, cid = cells[0]
    tie = tuple(Mention((lp,), cd, v) for v, lp, cd in cells[1:]
                if round(top - v, MATRIX_DECIMALS) < PB_PRECISION_S)
    also = "".join(f" · ≈ Lap {lap_label(m.lap_ids[0])} C{m.cid} {fmt_signed(m.value)} s"
                   for m in tie)
    return Moment(BIGGEST_LOSS, (lap,), cid, top, tie, caveat,
                  f"Biggest single-corner loss: Lap {lap_label(lap)} · C{cid} {fmt_signed(top)} s "
                  f"over your typical pass ({BEHIND_MARK}){also}")


def _runs(ids) -> list[tuple[int, ...]]:
    """Maximal runs of consecutive lap ids."""
    runs: list[list[int]] = []
    for lap in sorted(ids):
        if runs and lap == runs[-1][-1] + 1:
            runs[-1].append(lap)
        else:
            runs.append([lap])
    return [tuple(r) for r in runs]


def _clean_run(matrix: CornerMatrix, caveat) -> Moment | None:
    if all(m is None for m in matrix.medians):
        return None     # no corner has a typical pass, so "no ▼" would be vacuous
    cols = range(len(matrix.cids))
    calm = [lap for r, lap in enumerate(matrix.lap_ids)
            if any(v is not None for v in matrix.cells[r])
            and not any(matrix.is_behind(r, c) for c in cols)]
    runs = [r for r in _runs(calm) if len(r) >= MIN_CLEAN_RUN]
    if not runs:
        return None
    n = max(len(r) for r in runs)
    longest = [r for r in runs if len(r) == n]
    tie = tuple(Mention(r, None, float(n)) for r in longest[1:])
    return Moment(CLEAN_RUN, longest[0], None, float(n), tie, caveat,
                  f"Longest run with no {BEHIND_MARK}: {_laps_text(longest[0])} "
                  f"({plural(n, 'lap')}){_ties_text(tie)}")


def _best_window(lap_times, caveat) -> Moment | None:
    windows = [run[k:k + RUN_WINDOW] for run in _runs(lap_times)
               for k in range(len(run) - RUN_WINDOW + 1)]
    if not windows:
        return None
    meds = [float(np.median([printed_ms(lap_times[i]) for i in w])) / 1000 for w in windows]
    k = int(np.argmin(meds))
    tie = tuple(Mention(w, None, m) for j, (w, m) in enumerate(zip(windows, meds, strict=True))
                if j != k and within_timing_precision(m - meds[k]))
    return Moment(BEST_WINDOW, windows[k], None, meds[k], tie, caveat,
                  f"Best {RUN_WINDOW}-lap run: {_laps_text(windows[k])} · median "
                  f"{fmt_time(meds[k])}{_ties_text(tie)}")


def session_moments(lap_times: Mapping[int, float], matrix: CornerMatrix | None,
                    best_lap: int | None, *, provisional: bool = False,
                    clock_estimated: bool = False) -> SessionMoments:
    """The four moments over the clean laps (`lap_times`) and their grid; each None when absent."""
    caveat = caveat_for(provisional, clock_estimated)
    return SessionMoments(
        best_lap=_best_lap(lap_times, best_lap, provisional, caveat),
        biggest_loss=_biggest_loss(matrix, caveat) if matrix is not None else None,
        clean_run=_clean_run(matrix, caveat) if matrix is not None else None,
        best_window=_best_window(lap_times, caveat))
