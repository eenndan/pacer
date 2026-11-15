"""The session's story: WHERE a slow lap's time went, and a few session moments with their ties.

Qt-free and pacer-free. `Session.slow_lap_notes()` / `Session.session_moments()` feed it the clean
laps' times (`consistency_lap_ids`: valid, no GPS dropout) and the CORNERS BY LAP grid
(`Session.corner_matrix`); every judgement here is that grid's own (`stats.CornerMatrix`: its
typical pass per corner and its ▼ step), never a second rule. It LABELS laps; it never removes,
reorders or excuses one (refusal §14), scores no consistency and praises nothing.

SLOW LAP. A clean lap is slow when it is more than max(the clean laps' IQR, SLOW_MIN_MARGIN_S) over
their median lap: a DEFAULT, chosen by the W4 design lane on few sessions, and every note carries
the rule as text (`SlowLapNote.rule`) so a surface can say so. Where the IQR is over ATYPICAL_IQR_S
the rule says outright that the median is not a racing lap in this session. A note's EXCESS is the
lap's time over that median — "your typical lap" — and each corner's share of it is the corner's
time over its typical pass (the grid's median), read at print resolution as the grid's heat shade
reads it.

ONE BASELINE IN THE TEXT, the typical lap. The head is "5.50 s over your typical lap", never the
gap to the best: the corner losses are measured against typical passes, and a sentence that put a
gap-to-best next to them mixed two baselines and hid the denominator its shares were taken on. The
gap to the best stays a field (`gap_to_best_s`, the Laps table's Δbest, on printed ms); it is the
table's column, at its 3 decimals. The head prints 2, like every loss in the note: a different
quantity from Δbest, not a rounding of it.

WHY CORNERS AGAINST THEIR TYPICAL PASS, NOT AGAINST THE BEST LAP. A lap's corner times are the odd
pieces of `corners.segment_times`' exact 2N+1 partition (the grid reads the same numbers), so the
pieces sum to the lap time — but corner Δs against the BEST lap do not sum to the lap gap: the
straights hold the rest. And only against the typical pass is there a noise gate, the ▼ step.

CATEGORIES (only a corner BEYOND ITS ▼ STEP may be named — `CornerMatrix.is_behind`):
  * `over_lap` — the worst ▼ corner lost more than the whole excess: time was gained back elsewhere;
  * `one`      — the worst ▼ corner holds more than CULPRIT_SHARE (strictly) of the excess;
  * `two`      — the two worst ▼ corners together do;
  * `partial`  — a ▼ corner exists, but under that share: every ▼ corner is named, the rest "spread";
  * `spread`   — no corner beyond its ▼ step.
No ▼ corner is dropped: beside the culprit(s), the others are `also` ("; also C6 ▼ +0.83 s", at most
ALSO_NAMED of them, then "+N more").
STRAIGHTS ARE NEVER NAMED. A straight has no ▼ step today (the grid has corner columns only), so
there is no honest noise gate to say "the time went on the straight after C4" — such a lap reads
`spread` (or `partial`). Inventing a straight gate here would be a second rule the grid does not
show. The session's first timed lap is tagged "(first lap)" (`first_lap`): it is slow for a reason.

WHICH LAPS. Only the grid's rows: a ⊘ excluded or ⚠ dropout lap is never a row (its corner windows
are cut from a speed-integrated odometer), so it gets no note and is in no moment — and a dropout
lap level with the best is not in the best lap's ≈ list. Such a lap also breaks a 5-lap window. A
blank row gets no note. A cell the grid mutes (an edge not matched on track) is never ▼; a `spread`
lap with muted cells says which corners were not measured.

TIMING TRUST, ONE RULE, the grid's: the grid keeps its ▼ under a provisional start line or an
estimated clock (corner windows are matched on track, not timed from the line) and demotes only
lap times. So notes and moments exist in both states and carry `caveat` — a tuple naming EVERY
state that holds (a recording can be provisional AND on an estimated clock); the lap-time pieces
follow the Laps table — under a provisional line it blanks Δbest and withholds the ★, so a note has
no `gap_to_best_s` and there is no best-lap moment. Under an estimated clock a moment's ties widen
to that clock's published bound (`tie_floor`: 0.15 s, the Lap race's own), and say so.

MOMENTS (each None rather than invented): the best lap, with every clean lap the Laps table calls
level with it (printed gap under `library.PB_PRECISION_S`) named with its gap; the biggest
single-corner loss beyond its ▼ step; the best 5-lap run by median, the windows whose MEDIAN is
level merged into SPANS (maximal runs of level windows starting on consecutive laps, so "every
5-lap median in Laps 8–14 is level" is literally true of the span — of its medians, not its laps,
which on the demo spread 0.83 s; more than two spans is "no single best"). Not built — measured unstable under timing jitter by the design lane: a best 3-lap
run, a best pass per corner. Refused in review: the longest run with no ▼ (the ▼ marks about one
cell in ten by construction, so on pure noise that run is half the session).

STABILITY, held by tests/test_story.py (200 seeded draws): on the shipped demo, with lap times
±0.07 s and corner cells ±0.035 s, the biggest loss never changes (its best lap stands 0.215 s clear,
so its tie list cannot lose it). On a planted 0.001 s near-tie the true best always stays in the
tie list when each lap moves by up to ±0.035 s (a GAP by up to 0.07 s, the floor's own definition);
at ±0.07 s per lap it drops out in about 1 draw in 6 (33 of 200), so that is not claimed.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import numpy as np

from ._signal import fmt_signed, fmt_time, lap_label, plural
from .lap_race import ESTIMATED_CLOCK_TIE_S
from .library import PB_PRECISION_S, within_timing_precision
from .stats import MATRIX_DECIMALS, CornerMatrix

#: A lap is slow above its median clean lap by more than the larger of the clean laps' IQR and
#: this (s). A default picked on few sessions (W4 design lane), stated in every note's `rule`.
SLOW_MIN_MARGIN_S = 0.5
#: An IQR above this (twice the floor) means the median clean lap is not a racing lap: the rule
#: says so outright.
ATYPICAL_IQR_S = 2 * SLOW_MIN_MARGIN_S
#: The share of a slow lap's excess one (or two) ▼ corners must hold, STRICTLY, to be its culprit.
CULPRIT_SHARE = 0.5
#: How many other ▼ corners a note names beside its culprit(s) before "+N more".
ALSO_NAMED = 2
#: The window of the best-run moment, in consecutive clean laps.
RUN_WINDOW = 5
#: More level spans than this and there is no single best 5-lap run.
MAX_WINDOW_SPANS = 2
#: The grid's mark, spelled here because `theme.DELTA_BEHIND_ARROW` lives in a Qt module.
BEHIND_MARK = "▼"

OVER_LAP, ONE, TWO, PARTIAL, SPREAD = "over_lap", "one", "two", "partial", "spread"
CAVEAT_PROVISIONAL = "provisional"
CAVEAT_ESTIMATED = "estimated"
BEST_LAP, BIGGEST_LOSS, BEST_WINDOW = "best_lap", "biggest_loss", "best_window"


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
    """Where one slow lap's time went. `corners` are the culprit ▼ corners, worst first (empty for
    `spread`); `also` the lap's other ▼ corners; `unmeasured` the cids whose cell the grid mutes."""

    lap_id: int
    first_lap: bool                 # the session's first timed lap
    gap_to_best_s: float | None     # the Laps table's Δbest (printed ms); None while provisional
    excess_s: float                 # over the median clean lap, at print resolution
    category: str
    corners: tuple[CornerLoss, ...]
    also: tuple[CornerLoss, ...]
    unmeasured: tuple[int, ...]
    caveat: tuple[str, ...]          # every timing state that holds; () when none
    rule: str
    text: str


@dataclass(frozen=True)
class Mention:
    """A lap (or span of laps) and, for a corner moment, its corner, with the moment's value."""

    lap_ids: tuple[int, ...]
    cid: int | None
    value: float


@dataclass(frozen=True)
class Moment:
    """One session moment: the named laps/corner and value, every `tie` named with it, its text.
    For `best_window`, `lap_ids` is the best window — () when there is no single best — and `tie`
    holds the level spans (see `_best_window`)."""

    kind: str
    lap_ids: tuple[int, ...]
    cid: int | None
    value: float
    tie: tuple[Mention, ...]
    caveat: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class SessionMoments:
    best_lap: Moment | None
    biggest_loss: Moment | None
    best_window: Moment | None


def caveat_for(provisional: bool, clock_estimated: bool) -> tuple[str, ...]:
    """Every timing state that holds, provisional first; () when the timing is trusted."""
    return ((CAVEAT_PROVISIONAL,) if provisional else ()) + (
        (CAVEAT_ESTIMATED,) if clock_estimated else ())


def slow_threshold(lap_times: Mapping[int, float]) -> tuple[float, float, float] | None:
    """(median lap, IQR, margin) over the clean laps, or None with none."""
    v = [float(t) for t in lap_times.values() if np.isfinite(t)]
    if not v:
        return None
    q1, med, q3 = (float(x) for x in np.percentile(v, [25, 50, 75]))
    return med, q3 - q1, max(q3 - q1, SLOW_MIN_MARGIN_S)


def slow_rule_text(median: float, iqr: float, margin: float) -> str:
    text = (f"Slow: more than {margin:.2f} s over your typical lap, the median clean lap "
            f"({fmt_time(median)}); the margin is the larger of the clean laps' IQR ({iqr:.2f} s) "
            f"and {SLOW_MIN_MARGIN_S:.2f} s. A default chosen on few sessions.")
    if iqr > ATYPICAL_IQR_S:
        text += (f" Your laps are not typical in this session (IQR over {ATYPICAL_IQR_S:.2f} s): the "
                 "median is not a racing lap here, and the notes compare to it anyway.")
    return text


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


def _marked(c: CornerLoss) -> str:
    return f"C{c.cid} {BEHIND_MARK} {fmt_signed(c.loss_s)} s"


def _also(rest: tuple[CornerLoss, ...]) -> str:
    if not rest:
        return ""
    more = f", +{len(rest) - ALSO_NAMED} more" if len(rest) > ALSO_NAMED else ""
    return f"; also {', '.join(_marked(c) for c in rest[:ALSO_NAMED])}{more}"


def _body(category: str, named: tuple[CornerLoss, ...], also: tuple[CornerLoss, ...],
          unmeasured: tuple[int, ...]) -> str:
    if category == OVER_LAP:
        return (f"{_marked(named[0])}, more than the whole lap: time was gained back elsewhere"
                f"{_also(also)}")
    if category == ONE:
        return f"{_pass(named[0])} of it, over your typical pass ({BEHIND_MARK}){_also(also)}"
    if category == TWO:
        return (f"{_pass(named[0])} and {_pass(named[1])} of it, over your typical passes "
                f"({BEHIND_MARK}){_also(also)}")
    if category == PARTIAL:
        return f"{', '.join(_marked(c) for c in named)}; the rest is spread across the lap"
    tail = (f" ({', '.join(f'C{c}' for c in unmeasured)} not matched on track)"
            if unmeasured else "")
    return f"Spread across the lap: no corner beyond its {BEHIND_MARK} step{tail}"


def slow_lap_notes(lap_times: Mapping[int, float], matrix: CornerMatrix | None,
                   best_lap: int | None, *, first_lap_id: int | None = None,
                   provisional: bool = False, clock_estimated: bool = False) -> list[SlowLapNote]:
    """One note per slow clean lap that is a row of `matrix`, in session order. [] without a grid
    (fewer than `stats.MATRIX_MIN_LAPS` clean laps, or no corners). `first_lap_id` is the session's
    first timed lap (tagged "(first lap)")."""
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
            category, n_named = SPREAD, 0
        elif losses[0] > excess:
            category, n_named = OVER_LAP, 1
        elif losses[0] / excess > CULPRIT_SHARE:
            category, n_named = ONE, 1
        elif len(behind) >= 2 and (losses[0] + losses[1]) / excess > CULPRIT_SHARE:
            category, n_named = TWO, 2
        else:
            category, n_named = PARTIAL, len(behind)
        marked = tuple(_corner_loss(matrix, row, c) for c in behind)
        corners, also = marked[:n_named], marked[n_named:]
        unmeasured = tuple(matrix.cids[c] for c in cols if not matrix.resolved[row][c])
        first = lap == first_lap_id
        head = (f"Lap {lap_label(lap)}{' (first lap)' if first else ''} · "
                f"{excess:.2f} s over your typical lap")
        out.append(SlowLapNote(
            lap_id=lap, first_lap=first,
            gap_to_best_s=(printed_ms(t) - best_ms) / 1000 if best_ms is not None else None,
            excess_s=excess, category=category, corners=corners, also=also,
            unmeasured=unmeasured, caveat=caveat, rule=rule,
            text=f"{head} · {_body(category, corners, also, unmeasured)}"))
    return out


# ------------------------------------------------------------------------------------- moments
# ONE WORDING. A builder finds the laps, the corner and the value; `moment_row` words them from
# those fields alone, and the builder's `text` is that row joined (`_worded`). A surface that sets
# the label apart (the Stats page) and one that prints `text` can never read differently.


def tie_floor(caveat: tuple[str, ...]) -> float:
    """How close two figures must be to be named level: the timing precision, or under an
    estimated clock that clock's published bound (`lap_race.ESTIMATED_CLOCK_TIE_S`, the Lap race's
    own wider tie: a lap can read up to ~0.15 s off there, so 0.07 s would claim a precision the
    clock does not have)."""
    return ESTIMATED_CLOCK_TIE_S if CAVEAT_ESTIMATED in caveat else PB_PRECISION_S


def _level(gap_s: float, floor: float) -> bool:
    return (within_timing_precision(gap_s) if floor == PB_PRECISION_S
            else abs(round(gap_s, 3)) < floor)


def _laps_text(ids: tuple[int, ...]) -> str:
    if len(ids) == 1:
        return f"Lap {lap_label(ids[0])}"
    return f"Laps {lap_label(ids[0])}–{lap_label(ids[-1])}"


def _worded(m: Moment) -> Moment:
    row = moment_row(m)
    return replace(m, text=f"{row.label}: {row.value}")


def _best_lap(lap_times, best_lap, provisional, caveat) -> Moment | None:
    if provisional or best_lap not in lap_times:
        return None
    best_ms, floor = printed_ms(lap_times[best_lap]), tie_floor(caveat)
    tie = tuple(Mention((lap,), None, float(t)) for lap, t in sorted(lap_times.items())
                if lap != best_lap and _level((printed_ms(t) - best_ms) / 1000, floor))
    return _worded(Moment(BEST_LAP, (best_lap,), None, float(lap_times[best_lap]), tie, caveat, ""))


def _biggest_loss(matrix: CornerMatrix, caveat) -> Moment | None:
    cells = sorted(((_loss(matrix, r, c) or 0.0, lap, matrix.cids[c])
                    for r, lap in enumerate(matrix.lap_ids) for c in range(len(matrix.cids))
                    if matrix.is_behind(r, c)), key=lambda x: (-x[0], x[1], x[2]))
    if not cells:
        return None
    top, lap, cid = cells[0]
    floor = tie_floor(caveat)
    tie = tuple(Mention((lp,), cd, v) for v, lp, cd in cells[1:]
                if round(top - v, MATRIX_DECIMALS) < floor)
    return _worded(Moment(BIGGEST_LOSS, (lap,), cid, top, tie, caveat, ""))


def _runs(ids) -> list[tuple[int, ...]]:
    """Maximal runs of consecutive lap ids."""
    runs: list[list[int]] = []
    for lap in sorted(ids):
        if runs and lap == runs[-1][-1] + 1:
            runs[-1].append(lap)
        else:
            runs.append([lap])
    return [tuple(r) for r in runs]


def _best_window(lap_times, caveat) -> Moment | None:
    """The best RUN_WINDOW-lap run by median (printed ms), consecutive clean laps only. Every
    window whose MEDIAN is level with it (`tie_floor`) is merged into SPANS: maximal runs of level
    windows whose first laps are consecutive, so every window median inside a span is level — the
    laps themselves need not be. `lap_ids` is the best window while there are at most
    MAX_WINDOW_SPANS spans, () beyond; `tie` holds the spans, as Mentions of all their laps with
    their best median: the best window's own span when it is wider than the window, then the
    others — every span when there is no single best."""
    windows = [run[k:k + RUN_WINDOW] for run in _runs(lap_times)
               for k in range(len(run) - RUN_WINDOW + 1)]
    if not windows:
        return None
    meds = [float(np.median([printed_ms(lap_times[i]) for i in w])) / 1000 for w in windows]
    k = int(np.argmin(meds))
    best, med = windows[k], meds[k]
    floor = tie_floor(caveat)
    spans: list[list[int]] = []     # indices into `windows`
    for j, w in enumerate(windows):
        if not (j == k or _level(meds[j] - med, floor)):
            continue
        if spans and windows[spans[-1][-1]][0] + 1 == w[0]:
            spans[-1].append(j)
        else:
            spans.append([j])
    as_mention = [Mention(tuple(range(windows[s[0]][0], windows[s[-1]][-1] + 1)), None,
                          min(meds[j] for j in s)) for s in spans]
    if len(spans) > MAX_WINDOW_SPANS:
        return _worded(Moment(BEST_WINDOW, (), None, med, tuple(as_mention), caveat, ""))
    own = next(i for i, s in enumerate(spans) if k in s)
    tie = ([as_mention[own]] if len(spans[own]) > 1 else []) + [
        m for i, m in enumerate(as_mention) if i != own]
    return _worded(Moment(BEST_WINDOW, best, None, med, tuple(tie), caveat, ""))


@dataclass(frozen=True)
class MomentRow:
    """A moment as a surface's row: `label` and `value`, which join with ": " into the moment's
    `text` (the builders take their text from here), and where its Jump lands — `lap_id` (None:
    nothing to jump to, a "no single best" run) and, for a corner moment, `cid` to ring."""

    label: str
    value: str
    lap_id: int | None
    cid: int | None


_ROW_LABEL = {BEST_LAP: "Best lap", BIGGEST_LOSS: "Biggest single-corner loss",
              BEST_WINDOW: f"Best {RUN_WINDOW}-lap run"}


def moment_row(m: Moment) -> MomentRow:
    """`m` worded for a row from its own fields (never by slicing `text`)."""
    jump = m.lap_ids[0] if m.lap_ids else None
    floor = tie_floor(m.caveat)
    if m.kind == BEST_LAP:
        value = f"{_laps_text(m.lap_ids)} · {fmt_time(m.value)}"
        if m.tie:
            best_ms = printed_ms(m.value)
            gaps = sorted((printed_ms(t.value) - best_ms) / 1000 for t in m.tie)
            who = (f"Lap {lap_label(m.tie[0].lap_ids[0])} is" if len(m.tie) == 1 else
                   f"Laps {', '.join(lap_label(t.lap_ids[0]) for t in m.tie)} are")
            span = f"{gaps[0]:.3f} s" if gaps[0] == gaps[-1] else f"{gaps[0]:.3f}–{gaps[-1]:.3f} s"
            bound = (f"the {floor:.2f} s timing precision" if floor == PB_PRECISION_S
                     else f"this clock's {floor:.2f} s bound")
            value += f" · {who} level ({span}, under {bound})"
        return MomentRow(_ROW_LABEL[BEST_LAP], value, jump, None)
    if m.kind == BIGGEST_LOSS:
        value = (f"{_laps_text(m.lap_ids)} · C{m.cid} {fmt_signed(m.value)} s over your typical "
                 f"pass ({BEHIND_MARK})" + "".join(
                     f" · ≈ Lap {lap_label(t.lap_ids[0])} C{t.cid} {fmt_signed(t.value)} s"
                     for t in m.tie))
        return MomentRow(_ROW_LABEL[BIGGEST_LOSS], value, jump, m.cid)
    # It is the 5-lap MEDIANS that are level, never the laps: on the demo laps 8–14 spread 0.83 s
    # while every 5-lap median in them sits within 0.07 s of the best.
    level = f"within {floor:.2f} s"
    each = f"every {RUN_WINDOW}-lap median in"
    if not m.lap_ids:
        return MomentRow(f"No single best {RUN_WINDOW}-lap run",
                         f"{plural(len(m.tie), 'stretch', 'stretches')} of level "
                         f"{RUN_WINDOW}-lap medians ({level})", None, None)
    value = f"{_laps_text(m.lap_ids)} · median {fmt_time(m.value)}"
    for t in m.tie:
        # The best window's own span is the one tie holding the whole window: another span can
        # share a lap with it, never all five (their first windows are at least two laps apart).
        own = set(m.lap_ids) <= set(t.lap_ids)
        value += (f" · {each} {_laps_text(t.lap_ids)} is level ({level})" if own else
                  f" · also level ({level}): {each} {_laps_text(t.lap_ids)}")
    return MomentRow(_ROW_LABEL[BEST_WINDOW], value, jump, None)


def session_moments(lap_times: Mapping[int, float], matrix: CornerMatrix | None,
                    best_lap: int | None, *, provisional: bool = False,
                    clock_estimated: bool = False) -> SessionMoments:
    """The three moments over the clean laps (`lap_times`) and their grid; each None when absent."""
    caveat = caveat_for(provisional, clock_estimated)
    return SessionMoments(
        best_lap=_best_lap(lap_times, best_lap, provisional, caveat),
        biggest_loss=_biggest_loss(matrix, caveat) if matrix is not None else None,
        best_window=_best_window(lap_times, caveat))
