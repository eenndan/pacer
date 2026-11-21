"""Lap Race: the session's clean laps run at once from the start line, each on its own lap clock.

Race time `t` is seconds since every lap crossed the start line together. At `t` each lap is where
it was `t` seconds into ITS OWN lap, and the ladder orders them by place on track. This module is
the Qt-free, pacer-free data core: `RaceTracks` (built once by `Session.race_tracks`, the warp is
private to the session) and `race_state(tracks, t)` (the per-tick read). The UI is elsewhere.

WHAT THE INTERVAL IS. A rung's interval to the leader is the time the leader reached the place this
lap is at now, subtracted from now: `t − E_leader(place)`. It is the SAME quantity the compare
badge shows, `Session.delta_between(lap, leader, start_lap + t)`: the place is read through the
session's on-track alignment (each lap's corner-boundary warp onto the best lap's odometer, the Δ
family's frame since TRUTH-10), not by equal odometer fraction. It is computed exactly, not off a
resampled grid: each lap keeps its own knots (sample times, warp knots) and one tick is a handful of
`np.interp` calls over all laps at once (`_pack`), which evaluate the same piecewise-linear maps
`delta_between` does — agreement to float rounding, pinned in tests/test_lap_race.py. (Positions
are the same plain linear read as `timeline.trace_point_at`; its hold-across-a-dropout rule never
fires because a lap with a dropout is not in the field.)

THE LEADER is the lap furthest along (finished laps first, in lap-time order). At t = 0 every lap
is on the line and the stable sort makes the field's fastest lap the leader. The leader CHANGES
during a race — a lap quick through the first corners leads until a quicker lap catches it — and
every interval's reference changes with it, so an interval can step by a few ms at a leader change.
`interval_to_best` is the FIXED-reference readout for one chosen lap: its Δ to the session's best
lap (`delta_between(lap, best, …)`), which never changes reference.

"m back along the best line" (`Rung.back_m`) is a DIFFERENT quantity — the leader's place minus
this lap's, both on the best lap's odometer — and is named apart so no surface prints one as the
other.

PACKS, NOT CHAINED TIES. The ladder is a list of packs. A pack is a run of laps (in ladder order)
each within the tie threshold of the pack's FIRST lap — never of the lap above it, so ties do not
chain: laps at +0.00/+0.05/+0.10/+0.15 s at the finish are two packs, not one. Only a pack's leading
lap has a position (1, then 1 + the number of laps ahead of the next pack's leader); the other laps
are members, unranked ("≈"). `RaceState.packs` carries them for the pack rows the UI draws.
  * Between two FINISHED laps the interval is their lap-time difference, and the threshold is
    `FINISH_TIE_S` = `library.PB_PRECISION_S` (0.07 s: 2·√2·σ of docs/ACCURACY.md row C) — the rule
    by which the Laps table calls a lap level with the best. So at the finish the FIRST pack is
    exactly the laps the Laps table calls level with the best (when the best lap is in the field).
  * While either lap is RUNNING the interval also carries the place match, so `RACE_TIE_S` =
    `PB_PRECISION_S` + `PLACE_MATCH_ALLOWANCE_S` (0.03 s) = 0.10 s. The allowance is what the place
    match is allowed to add; on the synthetic truth (a lap 1 m off the line through a corner) its
    p95 is under 1 ms, and tests/test_lap_race.py asserts the allowance covers it. Real GPS places
    worse than that synthetic; the allowance is a judgment, not a measured real-world bound.
On a real 36-lap session most adjacent laps sit inside the threshold most of the race: an honest
blob, so most of the ladder is packs.

TIMING TRUST, part of the core (carried on `RaceTracks`):
  * PROVISIONAL start line (`Session.timing_verified` False): every lap time is referenced to an
    arbitrary point, and the Laps table blanks its Δbest there. `ranked` is False with
    `unranked_reason`: the state still has every dot (x, y, place, finished) — the controller may
    draw the race on the map — but NO rungs, packs, positions or intervals, and `interval_to_best`
    is None. The controller shows the reason where the ladder would be.
  * ESTIMATED clock (`timing_quality.media_clock`, an older camera's media-clock fallback): every
    lap time can read up to ~0.15 s off (`data_quality.MEDIA_CLOCK_LAP_ERROR`), so both the running
    and the finished threshold widen to `ESTIMATED_CLOCK_TIE_S` = 0.15 s. Ranked, with wider packs;
    the controller should name the clock beside the ladder as the Laps table does.
  * Otherwise: ranked, the thresholds above.

AT THE FINISH a finished lap's interval is its lap time minus the leader's, by construction, and
finished laps rank ahead of every running lap in lap-time order — so the final ladder IS lap-time
order.

THE FIELD (`Session.race_tracks`): every clean lap (`valid_lap_ids`) by default, because the best
five alone are one blob. Every lap NOT raced is listed in `RaceTracks.excluded` with its reason, for
the UI to count: not a clean lap (`EXCLUDED_NOT_CLEAN`: the band left it out, or a caller asked for
it), an interior GPS dropout (`EXCLUDED_DROPOUT`; the dot would cross the infield and the interval
would read an interpolated odometer), a lap that left the line the other laps drive
(`EXCLUDED_OFF_LINE`: a cut is not the same circuit, so it is out of the bests and out of the race
alike), a lap timed across a missing chapter (`EXCLUDED_CHAPTER_GAP`: its time is short by the
chapter), or no on-track match (`EXCLUDED_UNMATCHED`: no corner warp could be built, so its place
would be an equal-fraction guess). Fewer than `MIN_FIELD` laps left → no race (None).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import data_quality
from .library import PB_PRECISION_S

#: What the place match may add to a running interval's noise — see the module doc.
PLACE_MATCH_ALLOWANCE_S = 0.03
#: The tie threshold (s) to a pack's leader while either lap is still running.
RACE_TIE_S = round(PB_PRECISION_S + PLACE_MATCH_ALLOWANCE_S, 2)
#: The tie threshold between two finished laps: their interval is a lap-time difference, and this
#: is the app's one floor for that (`library.within_timing_precision`).
FINISH_TIE_S = PB_PRECISION_S
#: Both thresholds on an estimated (media-clock) recording: the number in
#: `data_quality.MEDIA_CLOCK_LAP_ERROR` ("up to ~0.15 s"; tests/test_lap_race.py reads it there).
ESTIMATED_CLOCK_TIE_S = 0.15
#: Fewer clean, matched laps than this and there is no race.
MIN_FIELD = 2
#: The fastest this many laps of the field get their own colour; the rest are drawn neutral.
COLOURED_LAPS = 5
#: The ladder shows the top this many rungs, plus the selected lap wherever it is.
LADDER_TOP = 8

EXCLUDED_NOT_CLEAN = "not a clean lap"
# The three ⚠ reasons are the ⚠ kinds themselves (data_quality.lap_warning_kinds).
EXCLUDED_DROPOUT = data_quality.WARN_DROPOUT
EXCLUDED_OFF_LINE = data_quality.WARN_OFF_LINE
EXCLUDED_CHAPTER_GAP = data_quality.WARN_CHAPTER_GAP
EXCLUDED_UNMATCHED = "unmatched"

UNRANKED_PROVISIONAL = ("The start/finish line is not confirmed, so lap times are provisional: "
                        "the race is drawn but not ranked.")

#: How many `RaceTracks` have been built in this process — a test reads it to prove a tick never
#: rebuilds one.
BUILD_COUNT = 0


@dataclass(frozen=True)
class RaceLap:
    """One lap's inputs, as `Session.race_tracks` reads them. `elapsed` (s from the lap's first
    sample), `dist` (its odometer, m), `xs`/`ys` (local m) are index-aligned; `warp` is
    (knot_best, knot_lap), the lap's odometer as a monotone function of the best lap's."""

    lap_id: int
    lap_time: float
    clock_start: float          # telemetry time of the lap's first sample (delta_between's start)
    elapsed: np.ndarray
    dist: np.ndarray
    xs: np.ndarray
    ys: np.ndarray
    warp: tuple[np.ndarray, np.ndarray]


@dataclass(frozen=True)
class Rung:
    """One lap on the ladder. `position` is set only for a pack's leading lap (`tied` False);
    a member (`tied` True) is unranked. `pack` indexes `RaceState.packs`."""

    lap_id: int
    position: int | None
    tied: bool
    pack: int
    interval_s: float           # to the race leader: the compare badge's Δ (delta_between)
    back_m: float               # metres back along the best line — a different quantity
    finished: bool
    coloured: bool


@dataclass(frozen=True)
class Pack:
    """A run of laps each within the tie threshold of the first (`laps[0]`, the pack's leader).
    `position` is the leader's (1 + laps ahead of it); `interval_s` is the leader's interval to the
    race leader; `spread_s` is the last member's interval minus the leader's (0 for a lone lap)."""

    laps: tuple[int, ...]
    position: int
    interval_s: float
    spread_s: float

    @property
    def leader(self) -> int:
        return self.laps[0]


@dataclass(frozen=True)
class RaceState:
    """The race at time `t`. `ids`, `x`, `y`, `place_m`, `finished` are per lap, aligned with
    `RaceTracks.ids`. When `ranked`, `rungs` is the whole ladder (leader first) and `packs` its
    packs; when not, both are empty and `unranked_reason` says why (dots only)."""

    t: float
    ids: tuple[int, ...]
    x: np.ndarray
    y: np.ndarray
    place_m: np.ndarray         # each lap's place on the best lap's odometer
    finished: np.ndarray
    rungs: list[Rung]
    packs: list[Pack]
    over: bool                  # every lap has finished
    ranked: bool
    unranked_reason: str | None
    to_best: np.ndarray | None = field(repr=False)  # per lap: `interval_to_best`, None unranked


def _pack(arrays, step: float) -> tuple[np.ndarray, np.ndarray]:
    """Concatenate per-lap increasing x-arrays, lap k shifted by k × `step` (> every lap's span),
    so ONE `np.interp` at `q_k + k × step` reads every lap's own piecewise-linear map at once.
    Returns (packed x, the per-lap offsets)."""
    offs = np.arange(len(arrays), dtype=float) * step
    return np.concatenate([np.asarray(a, float) + o for a, o in zip(arrays, offs, strict=True)]), offs


@dataclass(frozen=True)
class RaceTracks:
    """Everything a race tick reads, precomputed once. `ids` is the field in lap-time order;
    `excluded` maps every lap not raced to its reason; `coloured` the fastest `COLOURED_LAPS`;
    `best` the session's best lap (the frame, and `interval_to_best`'s reference — raced or not).
    Trust: `ranked` / `unranked_reason`, and the two thresholds this recording's clock allows."""

    ids: tuple[int, ...]
    lap_times: np.ndarray
    clock_starts: np.ndarray
    best_total_m: float
    excluded: dict[int, str]
    coloured: frozenset[int]
    ranked: bool
    unranked_reason: str | None
    clock_estimated: bool
    race_tie_s: float
    finish_tie_s: float
    best: RaceLap = field(repr=False)
    laps: tuple[RaceLap, ...] = field(repr=False)
    _t_xp: np.ndarray = field(repr=False)
    _t_off: np.ndarray = field(repr=False)
    _x_fp: np.ndarray = field(repr=False)
    _y_fp: np.ndarray = field(repr=False)
    _d_fp: np.ndarray = field(repr=False)
    _w_xp: np.ndarray = field(repr=False)
    _w_off: np.ndarray = field(repr=False)
    _w_fp: np.ndarray = field(repr=False)
    _t_end: np.ndarray = field(repr=False)      # each lap's last sample, s from its start
    _w_lo: np.ndarray = field(repr=False)       # each warp's lap-odometer span
    _w_hi: np.ndarray = field(repr=False)

    @property
    def duration(self) -> float:
        """Race length (s): the slowest lap's time."""
        return float(self.lap_times.max())

    def index(self, lap_id: int) -> int:
        """The lap's row in every per-lap array."""
        return self.ids.index(lap_id)


def build(laps: list[RaceLap], best: RaceLap, excluded: dict[int, str], *,
          provisional: bool = False, clock_estimated: bool = False) -> RaceTracks | None:
    """A `RaceTracks` over `laps` (each already matched and dropout-free), or None under
    `MIN_FIELD`. Sorted into lap-time order (lap id breaks an exact tie). `best` is the session's
    best lap (the warp frame). `provisional` → unranked; `clock_estimated` → the widened ties."""
    global BUILD_COUNT
    if len(laps) < MIN_FIELD:
        return None
    BUILD_COUNT += 1
    laps = sorted(laps, key=lambda lap: (lap.lap_time, lap.lap_id))
    # Each lap's span is [0, elapsed[-1]] (s) and [warp[1][0], warp[1][-1]] (m); a step past the
    # widest keeps the packed laps apart. Queries are clipped to the lap's own span (race_state).
    t_step = max(float(lap.elapsed[-1]) for lap in laps) + 10.0
    w_step = max(float(lap.warp[1][-1] - lap.warp[1][0]) for lap in laps) + 10.0
    t_xp, t_off = _pack([lap.elapsed for lap in laps], t_step)
    w_xp, w_off = _pack([lap.warp[1] for lap in laps], w_step)
    return RaceTracks(
        ids=tuple(lap.lap_id for lap in laps),
        lap_times=np.array([lap.lap_time for lap in laps]),
        clock_starts=np.array([lap.clock_start for lap in laps]),
        best_total_m=float(best.dist[-1]),
        excluded=dict(excluded),
        coloured=frozenset(lap.lap_id for lap in laps[:COLOURED_LAPS]),
        ranked=not provisional,
        unranked_reason=UNRANKED_PROVISIONAL if provisional else None,
        clock_estimated=clock_estimated,
        race_tie_s=ESTIMATED_CLOCK_TIE_S if clock_estimated else RACE_TIE_S,
        finish_tie_s=ESTIMATED_CLOCK_TIE_S if clock_estimated else FINISH_TIE_S,
        best=best,
        laps=tuple(laps),
        _t_xp=t_xp, _t_off=t_off,
        _x_fp=np.concatenate([lap.xs for lap in laps]),
        _y_fp=np.concatenate([lap.ys for lap in laps]),
        _d_fp=np.concatenate([lap.dist for lap in laps]),
        _w_xp=w_xp, _w_off=w_off,
        _w_fp=np.concatenate([lap.warp[0] for lap in laps]),
        _t_end=np.array([float(lap.elapsed[-1]) for lap in laps]),
        _w_lo=np.array([float(lap.warp[1][0]) for lap in laps]),
        _w_hi=np.array([float(lap.warp[1][-1]) for lap in laps]),
    )


def _elapsed_at_place(lap: RaceLap, place: np.ndarray) -> np.ndarray:
    """`lap`'s elapsed time where it was at each best-lap odometer `place`: delta_between's lap_b
    half (place → lap_b's odometer through its warp → its elapsed there)."""
    return np.interp(np.interp(place, lap.warp[0], lap.warp[1]), lap.dist, lap.elapsed)


def race_state(tracks: RaceTracks, t: float) -> RaceState:
    """The race at time `t` (s since the common start): O(field) numpy reads, nothing rebuilt."""
    lt = tracks.lap_times
    tc = np.clip(t, 0.0, lt)                       # each lap's own elapsed time, held at its finish
    # Clipped to each lap's own knots, the clamp np.interp applies inside delta_between: past
    # them a packed read would interpolate into the next lap.
    q = np.minimum(tc, tracks._t_end) + tracks._t_off
    x = np.interp(q, tracks._t_xp, tracks._x_fp)
    y = np.interp(q, tracks._t_xp, tracks._y_fp)
    d_lap = np.clip(np.interp(q, tracks._t_xp, tracks._d_fp), tracks._w_lo, tracks._w_hi)
    place = np.interp(d_lap + tracks._w_off, tracks._w_xp, tracks._w_fp)
    finished = t >= lt
    over = bool(finished.all())
    if not tracks.ranked:
        return RaceState(t=float(t), ids=tracks.ids, x=x, y=y, place_m=place, finished=finished,
                         rungs=[], packs=[], over=over, ranked=False,
                         unranked_reason=tracks.unranked_reason, to_best=None)
    # Finished laps first, in lap-time order; then running laps, furthest along first. At t = 0
    # every place is 0 and the stable sort leaves the field's fastest lap leading.
    order = np.argsort(np.where(finished, lt - 1e9, -place), kind="stable")
    lead = int(order[0])
    interval = tc - _elapsed_at_place(tracks.laps[lead], place)
    interval[finished] = lt[finished] - lt[lead]   # at the line: the lap-time difference, exactly
    interval[lead] = 0.0
    back = place[lead] - place
    back[finished] = 0.0
    to_best = tc - _elapsed_at_place(tracks.best, place)
    rungs: list[Rung] = []
    groups: list[list[int]] = []
    head = -1
    for rank, k in enumerate(order.tolist()):
        tied = False
        if head >= 0:
            floor = tracks.finish_tie_s if finished[k] and finished[head] else tracks.race_tie_s
            tied = bool(interval[k] - interval[head] < floor)
        if not tied:
            head = k
            groups.append([])
        groups[-1].append(k)
        lap_id = tracks.ids[k]
        rungs.append(Rung(lap_id=lap_id, position=None if tied else rank + 1, tied=tied,
                          pack=len(groups) - 1, interval_s=float(interval[k]),
                          back_m=float(back[k]), finished=bool(finished[k]),
                          coloured=lap_id in tracks.coloured))
    packs: list[Pack] = []
    ahead = 0
    for g in groups:
        packs.append(Pack(laps=tuple(tracks.ids[k] for k in g), position=ahead + 1,
                          interval_s=float(interval[g[0]]),
                          spread_s=float(interval[g[-1]] - interval[g[0]])))
        ahead += len(g)
    return RaceState(t=float(t), ids=tracks.ids, x=x, y=y, place_m=place, finished=finished,
                     rungs=rungs, packs=packs, over=over, ranked=True, unranked_reason=None,
                     to_best=to_best)


def interval_to_best(state: RaceState, lap_id: int) -> float | None:
    """The FIXED-reference readout for one lap: its Δ to the session's best lap at its place now,
    `Session.delta_between(lap, best, start_lap + t)` — unlike `Rung.interval_s`, whose reference
    (the race leader) changes during the race. None when the race is unranked or the lap is not
    in the field."""
    if state.to_best is None or lap_id not in state.ids:
        return None
    return float(state.to_best[state.ids.index(lap_id)])


def shown_rungs(state: RaceState, selected: int | None = None) -> list[Rung]:
    """The rungs the ladder draws: the top `LADDER_TOP`, plus the selected lap's rung wherever it
    is (appended below them, so the reader sees it is further down)."""
    top = state.rungs[:LADDER_TOP]
    if selected is None or any(r.lap_id == selected for r in top):
        return top
    return top + [r for r in state.rungs if r.lap_id == selected]
