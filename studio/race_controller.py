"""The Lap Race's controller: owns race time and turns `lap_race.race_state` into what the map and
the race card draw. Qt-free and pacer-free: the map layer, the card and the video's pause arrive
injected, and the clock is a callable, so tests drive it with a fake clock and no sleeps.

RACE TIME IS NOT MEDIA TIME. `t` is seconds since every clean lap left the start line together;
it advances by wall-clock dt × speed on the app's existing ~30 Hz tick (`CentralView.tick` calls
`tick()`), never from the video. So entering pauses the video, the map hides the video-position
marker, and the controller owns no timer of its own: when the race ends nothing is left running.
A tick that arrives late (a modal dialog, a stall) advances at most `MAX_STEP_S` of wall time, so
the field never teleports.

WHAT IT BUILDS, ONCE. `RaceTracks` comes from `Session.race_tracks()` at entry and is held for the
race; a tick is one `race_state` read (O(field) numpy) plus the map's one scatter update and the
card's repaint. Re-segmenting, a trust change, compare or a new recording END the race (CentralView
wires each), because the held tracks would then describe laps that no longer exist.

PACKS, NOT A LADDER. The card shows `RaceState.packs` (`pack_rows`): the first `PACKS_SHOWN`, and
always the pack holding the selected lap. Only a pack's leading lap has a position. While the race
runs, rows are listed in track order — the order `race_state` packs them — which is the truth of
"who is ahead on track now"; at the finish the same packs are the final classification in lap-time
order (finished laps rank by lap time, `lap_race`'s module doc). The one fixed-reference readout is
the selected lap's `interval_to_best`, which never changes reference when the leader does.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from . import lap_race
from ._signal import lap_label, plural

#: How many packs the card lists (plus the selected lap's, wherever it is).
PACKS_SHOWN = 5
#: The longest wall-clock step one tick may advance (s): a late tick slows the race, never jumps it.
MAX_STEP_S = 0.25
#: The excluded reasons as the card's muted line says them.
_EXCLUDED_WORDS = {
    lap_race.EXCLUDED_DROPOUT: ("GPS dropout", "GPS dropouts"),
    lap_race.EXCLUDED_NOT_CLEAN: ("not a clean lap", "not clean"),
    lap_race.EXCLUDED_UNMATCHED: ("no on-track match", "with no on-track match"),
}
#: Why the command is off when the session cannot field a race (the menu's " — " reason shape).
NO_RACE_REASON = ("Needs at least two clean laps placed on track — the race lines up every clean "
                  "lap on its own clock, and this recording has fewer.")
CAPTION = "each clean lap on its own clock from the start line"


@dataclass(frozen=True)
class PackRow:
    """One row of the card: a pack (`lap_race.Pack`) as drawn. `laps` is the whole pack in race
    order; `chips` the laps named on the row (the coloured ones, plus the selected lap whatever its
    colour); `more` how many grey laps the row only counts."""

    position: int
    laps: tuple[int, ...]
    chips: tuple[int, ...]
    more: int
    interval_s: float
    spread_s: float
    selected: bool


@dataclass(frozen=True)
class CardModel:
    """Everything the race card paints for one tick — numbers, not formatted strings (the view
    formats them with the theme)."""

    t: float
    duration: float
    over: bool
    playing: bool
    speed: float
    rows: tuple[PackRow, ...]
    hidden_packs: int             # packs not listed
    selected: int | None
    best: int
    to_best: float | None         # `interval_to_best` of the selected lap, None when unranked
    tie_s: float                  # the threshold the readout is neutral inside
    unranked_reason: str | None
    clock_estimated: bool
    excluded: str | None          # the muted "N laps not raced: …" line


def pack_rows(state: lap_race.RaceState, coloured: frozenset[int], selected: int | None,
              shown: int = PACKS_SHOWN) -> tuple[list[PackRow], int]:
    """The card's rows for `state`: the first `shown` packs, plus the pack holding `selected`
    (appended after them, so the reader sees it is further down). Returns (rows, packs left out).

    A row's chips are in FIELD order (lap-time order, `state.ids`), not in the order the laps sit
    inside the pack: inside a pack the timing cannot split them, and on the 36-lap session the
    in-pack order swapped chips every few frames — motion that claimed an order the numbers do not
    carry. Measured over 60 frames (2 s of race) the rows themselves held their order."""
    keep = list(range(min(shown, len(state.packs))))
    sel_pack = next((k for k, p in enumerate(state.packs) if selected in p.laps), None)
    if sel_pack is not None and sel_pack not in keep:
        keep.append(sel_pack)
    rows = []
    for k in keep:
        p = state.packs[k]
        named = set(i for i in p.laps if i in coloured or i == selected)
        chips = tuple(i for i in state.ids if i in named)
        rows.append(PackRow(position=p.position, laps=p.laps, chips=chips,
                            more=len(p.laps) - len(chips), interval_s=p.interval_s,
                            spread_s=p.spread_s, selected=k == sel_pack))
    return rows, len(state.packs) - len(keep)


def excluded_line(excluded: dict[int, str]) -> str | None:
    """'2 laps not raced: 1 GPS dropout, 1 not a clean lap' — or None when every lap raced."""
    if not excluded:
        return None
    counts: dict[str, int] = {}
    for reason in excluded.values():
        counts[reason] = counts.get(reason, 0) + 1
    parts = ", ".join(plural(n, *_EXCLUDED_WORDS.get(reason, (reason, reason)))
                      for reason, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    return f"{plural(len(excluded), 'lap')} not raced: {parts}"


def readout_text(model: CardModel) -> str | None:
    """The fixed-reference line, named as such: 'L12 vs best L30: +0.23 s' ('≈' inside the tie
    threshold, where the two cannot be told apart). None when unranked or nothing is selected."""
    if model.selected is None or model.to_best is None:
        return None
    if model.selected == model.best:
        return f"L{lap_label(model.best)} is the best lap: every other lap's reference"
    d = model.to_best
    sign = "−" if d < 0 else "+"
    near = "≈ " if abs(d) < model.tie_s else ""
    return f"L{lap_label(model.selected)} vs best L{lap_label(model.best)}: {near}{sign}{abs(d):.2f} s"


class RaceController:
    """The race's clock and its fan-out. `map_view` takes `start_race(tracks, colours, selected)`,
    `update_race(state)`, `set_race_selected(lap_id)` and `end_race()`; `card` takes
    `configure(colours, winner_time)`, `show_model(model)`, `show()`/`hide()`; `pause_video()` stops the picture on entry. `rates` is
    the playback-rate ladder the transport uses (0.25× … 2×)."""

    def __init__(self, session, map_view, card, *, pause_video: Callable[[], None],
                 rates: Sequence[float], clock: Callable[[], float] = time.monotonic,
                 on_change: Callable[[bool], None] | None = None):
        self.session = session
        self.map = map_view
        self.card = card
        self._pause_video = pause_video
        self.rates = tuple(rates)
        self._clock = clock
        self._on_change = on_change
        self.tracks: lap_race.RaceTracks | None = None
        self.t = 0.0
        self.speed = 1.0
        self.playing = False
        self.selected: int | None = None
        self._last: float | None = None
        self._dirty = False
        self.ticks = 0              # race ticks that redrew (a test reads it)

    @property
    def active(self) -> bool:
        return self.tracks is not None

    def available(self) -> bool:
        """True when this session can field a race (`Session.race_tracks` memoizes the field)."""
        return self.session.race_tracks() is not None

    # ------------------------------------------------------------------ enter / exit
    def enter(self, selected: int | None = None, speed: float = 1.0) -> bool:
        """Line the field up at t = 0 and start it running. False (nothing changed) when the
        session cannot field a race."""
        if self.active:
            return True
        tracks = self.session.race_tracks()
        if tracks is None:
            return False
        self._pause_video()
        self.tracks = tracks
        self.selected = selected if selected in tracks.ids or selected == tracks.best.lap_id \
            else None
        self.speed = min(self.rates, key=lambda r: abs(r - speed)) if self.rates else 1.0
        self.t = 0.0
        self.playing = True
        self._last = self._clock()
        self.map.start_race(tracks, self.colours(), self.selected)
        self.card.configure(self.colours(), float(tracks.lap_times.min()))
        self.card.show()
        self._redraw()
        if self._on_change is not None:
            self._on_change(True)
        return True

    def end(self) -> None:
        """Leave the race: remove every race item, put the map back exactly as it was. No-op when
        no race is on."""
        if not self.active:
            return
        self.tracks = None
        self.playing = False
        self._last = None
        self.map.end_race()
        self.card.hide()
        if self._on_change is not None:
            self._on_change(False)

    def colours(self) -> dict[int, int]:
        """Lap id → its series slot for the coloured five (the fastest, in lap-time order). Colour
        is lap IDENTITY, never rank: a lap keeps its slot all race."""
        assert self.tracks is not None
        fastest = [i for i in self.tracks.ids if i in self.tracks.coloured]
        return {lap: k for k, lap in enumerate(fastest)}

    # ------------------------------------------------------------------ transport
    def toggle_play(self) -> None:
        """Play / pause; at the finish, play restarts from the line."""
        tr = self.tracks
        if tr is None:
            return
        if not self.playing and self.t >= tr.duration:
            self.t = 0.0
        self.playing = not self.playing
        self._last = self._clock()
        self._dirty = True

    def restart(self) -> None:
        """Back to the line, running."""
        if not self.active:
            return
        self.t = 0.0
        self.playing = True
        self._last = self._clock()
        self._dirty = True

    def step_speed(self, direction: int) -> None:
        """One rung along the playback-rate ladder, clamped (the transport's [ / ])."""
        if not self.active or not self.rates:
            return
        i = min(range(len(self.rates)), key=lambda k: abs(self.rates[k] - self.speed))
        self.speed = self.rates[min(max(i + int(direction), 0), len(self.rates) - 1)]
        self._dirty = True

    def cycle_speed(self) -> None:
        """The card's speed chip: the next rung up, wrapping from the fastest to the slowest."""
        if not self.active or not self.rates:
            return
        i = min(range(len(self.rates)), key=lambda k: abs(self.rates[k] - self.speed))
        self.speed = self.rates[(i + 1) % len(self.rates)]
        self._dirty = True

    def scrub(self, fraction: float) -> None:
        """Jump race time to `fraction` of the race (the card's progress bar), keeping play state."""
        tr = self.tracks
        if tr is None:
            return
        self.t = min(max(float(fraction), 0.0), 1.0) * tr.duration
        self._last = self._clock()
        self._dirty = True

    def set_selected(self, lap_id: int | None) -> None:
        """Emphasise another lap (a click in the Laps table during the race)."""
        tr = self.tracks
        if tr is None or lap_id == self.selected:
            return
        self.selected = lap_id if lap_id in tr.ids or lap_id == tr.best.lap_id else None
        self.map.set_race_selected(self.selected)
        self._dirty = True

    # ------------------------------------------------------------------ the tick
    def tick(self) -> None:
        """Advance race time by the wall-clock step × speed and redraw if anything moved. Stops at
        the finish, where the card holds the final classification until Esc or a replay."""
        tr = self.tracks
        if tr is None:
            return
        now = self._clock()
        last = now if self._last is None else self._last
        self._last = now
        if self.playing:
            dur = tr.duration
            self.t = min(self.t + min(max(now - last, 0.0), MAX_STEP_S) * self.speed, dur)
            if self.t >= dur:
                self.playing = False
            self._dirty = True
        if self._dirty:
            self._redraw()

    def state(self) -> lap_race.RaceState:
        assert self.tracks is not None
        return lap_race.race_state(self.tracks, self.t)

    def model(self, state: lap_race.RaceState) -> CardModel:
        tr = self.tracks
        assert tr is not None
        rows, hidden = pack_rows(state, tr.coloured, self.selected) if state.ranked else ([], 0)
        to_best = (lap_race.interval_to_best(state, self.selected)
                   if self.selected is not None else None)
        sel_done = (self.selected in tr.ids
                    and bool(state.finished[tr.index(self.selected)]))
        return CardModel(
            t=state.t, duration=tr.duration, over=state.over, playing=self.playing,
            speed=self.speed, rows=tuple(rows), hidden_packs=hidden, selected=self.selected,
            best=tr.best.lap_id, to_best=to_best,
            tie_s=tr.finish_tie_s if sel_done else tr.race_tie_s,
            unranked_reason=state.unranked_reason, clock_estimated=tr.clock_estimated,
            excluded=excluded_line(tr.excluded))

    def _redraw(self) -> None:
        state = self.state()
        self.map.update_race(state)
        self.card.show_model(self.model(state))
        self._dirty = False
        self.ticks += 1
