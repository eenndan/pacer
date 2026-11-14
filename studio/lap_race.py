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
`delta_between` does — agreement to float rounding, pinned in tests/test_lap_race.py. The
design lane's 1/30 s grid agreed to 0.15 ms; the knots cost the same per tick, hold no per-frame
copy of every lap, and disagree by nothing, so there is no grid. (Positions are the same plain
linear read as `timeline.trace_point_at`; its hold-across-a-dropout rule never fires because a lap
with a dropout is not in the field.)

"m back along the best line" (`Rung.back_m`) is a DIFFERENT quantity — the leader's place minus
this lap's, both on the best lap's odometer — and is named apart so no surface prints one as the
other.

TIES. A rung whose interval to the rung above is under the tie threshold is marked tied ("≈") and
given no position. Between two FINISHED laps the interval is their lap-time difference, and the
app's floor for that is `library.PB_PRECISION_S` (0.07 s: 2·√2·σ of docs/ACCURACY.md row C, the
lap-time difference the best measured instrument can resolve) — the same rule that colours a lap
"level" in the Laps table, so the final ladder and the table cannot disagree. While either lap is
still RUNNING the interval also carries the place match: the design lane measured the interval
against the synthetic demo's truth at σ 7–12 ms, p95 21–36 ms per pair. `RACE_TIE_S` = 0.10 s sits
between the floor and that p95 added in quadrature (0.079 s) and added linearly (0.106 s), rounded
to the 0.1 s the ladder prints. The best five laps of a session sit within it of each other most of
the race (88 % of adjacent rungs on the owner's recording): an honest blob, so the ladder says "≈".

AT THE FINISH a finished lap's interval is its lap time minus the leader's, by construction, and
finished laps rank ahead of every running lap in lap-time order — so the final ladder IS lap-time
order.

THE FIELD (`Session.race_tracks`): every clean lap (`valid_lap_ids`) by default, because the best
five alone are one blob. A lap is LEFT OUT, with its reason in `RaceTracks.excluded` for the UI to
count, when its place would be a guess: an interior GPS dropout (`EXCLUDED_DROPOUT`; the dot would
cross the infield and the interval would read an interpolated odometer), or no on-track match
(`EXCLUDED_UNMATCHED`: no corner warp could be built, so its place would be an equal-fraction
guess). Fewer than `MIN_FIELD` laps left → no race (None).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .library import PB_PRECISION_S

#: The tie threshold (s) between two rungs while either is still running — see the module doc.
RACE_TIE_S = 0.10
#: The tie threshold between two finished laps: their interval is a lap-time difference, and this
#: is the app's one floor for that (`library.within_timing_precision`).
FINISH_TIE_S = PB_PRECISION_S
#: Fewer clean, matched laps than this and there is no race.
MIN_FIELD = 2
#: The fastest this many laps of the field get their own colour; the rest are drawn neutral.
COLOURED_LAPS = 5
#: The ladder shows the top this many rungs, plus the selected lap wherever it is.
LADDER_TOP = 8

EXCLUDED_DROPOUT = "dropout"
EXCLUDED_UNMATCHED = "unmatched"

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
    """One ladder rung. `position` is None when `tied` (the rung above is inside the threshold)."""

    lap_id: int
    position: int | None
    tied: bool
    interval_s: float           # to the leader: the compare badge's Δ (delta_between)
    back_m: float               # metres back along the best line — a different quantity
    finished: bool
    coloured: bool


@dataclass(frozen=True)
class RaceState:
    """The race at time `t`. `x`, `y`, `place_m`, `finished` are aligned with `RaceTracks.ids`;
    `rungs` is the whole ladder, leader first (`shown_rungs` picks what the ladder draws)."""

    t: float
    x: np.ndarray
    y: np.ndarray
    place_m: np.ndarray         # each lap's place on the best lap's odometer
    finished: np.ndarray
    rungs: list[Rung]
    over: bool                  # every lap has finished


def _pack(arrays, step: float) -> tuple[np.ndarray, np.ndarray]:
    """Concatenate per-lap increasing x-arrays, lap k shifted by k × `step` (> every lap's span),
    so ONE `np.interp` at `q_k + k × step` reads every lap's own piecewise-linear map at once.
    Returns (packed x, the per-lap offsets)."""
    offs = np.arange(len(arrays), dtype=float) * step
    return np.concatenate([np.asarray(a, float) + o for a, o in zip(arrays, offs, strict=True)]), offs


@dataclass(frozen=True)
class RaceTracks:
    """Everything a race tick reads, precomputed once. `ids` is the field in lap-time order;
    `excluded` maps each lap left out to its reason; `coloured` the fastest `COLOURED_LAPS`."""

    ids: tuple[int, ...]
    lap_times: np.ndarray
    clock_starts: np.ndarray
    best_total_m: float
    excluded: dict[int, str]
    coloured: frozenset[int]
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


def build(laps: list[RaceLap], best_total_m: float, excluded: dict[int, str]) -> RaceTracks | None:
    """A `RaceTracks` over `laps` (each already matched and dropout-free), or None under
    `MIN_FIELD`. Sorted into lap-time order (lap id breaks an exact tie)."""
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
        best_total_m=float(best_total_m),
        excluded=dict(excluded),
        coloured=frozenset(lap.lap_id for lap in laps[:COLOURED_LAPS]),
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
    # Finished laps first, in lap-time order; then running laps, furthest along first.
    order = np.argsort(np.where(finished, lt - 1e9, -place), kind="stable")
    lead = int(order[0])
    ll = tracks.laps[lead]
    # The leader's elapsed time where each lap is now: delta_between's lap_b half, for every lap.
    e_lead = np.interp(np.interp(place, ll.warp[0], ll.warp[1]), ll.dist, ll.elapsed)
    interval = tc - e_lead
    interval[finished] = lt[finished] - lt[lead]   # at the line: the lap-time difference, exactly
    interval[lead] = 0.0
    back = place[lead] - place
    back[finished] = 0.0
    rungs: list[Rung] = []
    prev = None
    for rank, k in enumerate(order.tolist()):
        tied = False
        if prev is not None:
            floor = FINISH_TIE_S if finished[k] and finished[prev] else RACE_TIE_S
            tied = bool(interval[k] - interval[prev] < floor)
        lap_id = tracks.ids[k]
        rungs.append(Rung(lap_id=lap_id, position=None if tied else rank + 1, tied=tied,
                          interval_s=float(interval[k]), back_m=float(back[k]),
                          finished=bool(finished[k]), coloured=lap_id in tracks.coloured))
        prev = k
    return RaceState(t=float(t), x=x, y=y, place_m=place, finished=finished, rungs=rungs,
                     over=bool(finished.all()))


def shown_rungs(state: RaceState, selected: int | None = None) -> list[Rung]:
    """The rungs the ladder draws: the top `LADDER_TOP`, plus the selected lap's rung wherever it
    is (appended below them, so the reader sees it is further down)."""
    top = state.rungs[:LADDER_TOP]
    if selected is None or any(r.lap_id == selected for r in top):
        return top
    return top + [r for r in state.rungs if r.lap_id == selected]
