"""The Lap Race's controller, card and map layer (`studio/race_controller.py`, `race_ladder.py`,
`map_view._RaceLayer`) — and the window around them.

A NEW SURFACE, so a new file: `tests/test_lap_race.py` pins the Qt-free core (`race_state`, packs,
intervals against known truth); nothing pinned what the app DOES with it — the race clock, what the
card lists, what the map draws, and what entering and leaving a race does to the rest of the
window. Two halves:

  * the CONTROLLER on a real `RaceTracks` (the core's own straight-lap fields and a real synthetic
    Session) with a FAKE clock and recorder views: race time is wall-clock dt × speed (no sleeps),
    clamped per tick, stopped at the finish; a tick rebuilds nothing; the card's rows ARE
    `RaceState.packs` (and the selected lap's pack is always listed); the fixed readout IS
    `interval_to_best`; a provisional line shows the reason and no rows; an estimated clock is named
    with its widened threshold; the laps not raced are counted;
  * the REAL WINDOW on one synthetic recording of the demo circuit (`synth_gopro`, as
    test_copy_cluster opens it): entering pauses the video, hides the marker and adds every race
    item with `ignoreBounds`; the dots sit at `race_state` positions; ticks add no item; exit puts
    marker, key, items, playhead and selection back; Esc, compare, a start-line re-segment and a new
    load each end the race; under two raceable laps View ▸ Lap race is off with its reason.

Run: python tests/test_race_controller.py   (~20 s; the pixi env's ffmpeg writes the video trak)
"""
import contextlib
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
from _qtapp import themed_app  # noqa: E402

from studio import lap_race, race_controller  # noqa: E402
from studio.race_controller import RaceController, pack_rows  # noqa: E402

_APP = themed_app()


# ---------------------------------------------------------------- controller half: fakes
class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class _Map:
    """Records what the controller asks the map to draw."""

    def __init__(self):
        self.started = self.ended = 0
        self.state = None
        self.selected = None

    def start_race(self, tracks, colours, selected):
        self.started += 1
        self.colours, self.selected = colours, selected

    def update_race(self, state):
        self.state = state

    def set_race_selected(self, lap_id):
        self.selected = lap_id

    def end_race(self):
        self.ended += 1


class _Card:
    def __init__(self):
        self.model = None
        self.visible = False

    def configure(self, colours, winner):
        self.colours = colours

    def show_model(self, model):
        self.model = model

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False


class _Session:
    """Serves one RaceTracks, as `Session.race_tracks` memoizes it."""

    def __init__(self, tracks):
        self.tracks = tracks

    def race_tracks(self):
        return self.tracks


def _straight(lap_id: int, speed: float, length: float = 600.0) -> lap_race.RaceLap:
    lt = length / speed
    el = np.linspace(0.0, lt, int(lt * 10) + 1)
    d = el * speed
    return lap_race.RaceLap(lap_id=lap_id, lap_time=lt, clock_start=0.0, elapsed=el, dist=d,
                            xs=d.copy(), ys=np.zeros_like(d),
                            warp=(np.array([0.0, length]), np.array([0.0, length])))


def _wide_field(**kw):
    """Nine laps whose lap times step 0.3 s: nine packs at the finish, every one its own."""
    laps = [_straight(k, 600.0 / (30.0 + 0.3 * k)) for k in range(9)]
    return lap_race.build(laps, laps[0], {9: lap_race.EXCLUDED_DROPOUT,
                                          10: lap_race.EXCLUDED_NOT_CLEAN,
                                          11: lap_race.EXCLUDED_NOT_CLEAN}, **kw)


def _controller(tracks):
    clock, mp, card = _Clock(), _Map(), _Card()
    paused = []
    rc = RaceController(_Session(tracks), mp, card, pause_video=lambda: paused.append(1),
                        rates=(0.25, 0.5, 1.0, 2.0), clock=clock)
    return rc, clock, mp, card, paused


def test_the_clock_is_wall_dt_times_speed_and_stops_at_the_finish():
    """Race time advances by the clock's step × speed — not per tick — is clamped per tick, and
    stops (paused, at the slowest lap's time) at the finish."""
    tracks = _wide_field()
    rc, clock, mp, card, paused = _controller(tracks)
    assert rc.enter(selected=3, speed=2.0) and paused == [1] and mp.started == 1 and card.visible
    assert rc.t == 0.0 and rc.speed == 2.0 and rc.playing
    for _ in range(3):
        rc.tick()                       # no wall time passed: nothing moves
    assert rc.t == 0.0
    clock.now += 0.1
    rc.tick()
    assert abs(rc.t - 0.2) < 1e-12, rc.t
    rc.step_speed(-1)                   # 1×
    clock.now += 0.1
    rc.tick()
    assert abs(rc.t - 0.3) < 1e-12, rc.t
    clock.now += 5.0                    # a stalled tick advances at most MAX_STEP_S of wall time
    rc.tick()
    assert abs(rc.t - (0.3 + race_controller.MAX_STEP_S)) < 1e-12, rc.t
    rc.toggle_play()
    clock.now += 0.2
    rc.tick()
    assert abs(rc.t - (0.3 + race_controller.MAX_STEP_S)) < 1e-12, "paused race time moved"
    rc.toggle_play()
    rc.scrub(0.99)
    for _ in range(40):
        clock.now += 0.1
        rc.tick()
    assert rc.t == tracks.duration and not rc.playing and card.model.over
    rc.end()
    assert mp.ended == 1 and not card.visible and not rc.active
    print("ok clock: dt × speed, clamped, paused, stopped at the finish")


def test_a_tick_rebuilds_nothing():
    """The tracks are built once at entry: a hundred ticks build none."""
    tracks = _wide_field()
    rc, clock, *_ = _controller(tracks)
    rc.enter()
    before = lap_race.BUILD_COUNT
    for _ in range(100):
        clock.now += 0.033
        rc.tick()
    assert lap_race.BUILD_COUNT == before and rc.ticks == 101, (lap_race.BUILD_COUNT, rc.ticks)
    print("ok no rebuild per tick")


def test_the_rows_are_the_packs_and_the_selected_pack_is_always_listed():
    """The card's rows are `RaceState.packs` row for row — position, laps, interval, spread — the
    first PACKS_SHOWN of them, plus the selected lap's pack when it is further down."""
    tracks = _wide_field()
    rc, clock, mp, card, _ = _controller(tracks)
    rc.enter(selected=8)
    for t in (0.0, 5.0, 17.0, tracks.duration):
        rc.scrub(t / tracks.duration)
        rc.tick()
        state = lap_race.race_state(tracks, rc.t)
        rows = card.model.rows
        shown = [p for p in state.packs[:race_controller.PACKS_SHOWN]]
        extra = [p for p in state.packs if 8 in p.laps and p not in shown]
        assert [(r.position, r.laps, r.interval_s, r.spread_s) for r in rows] == [
            (p.position, p.laps, p.interval_s, p.spread_s) for p in shown + extra], t
        assert sum(r.selected for r in rows) == 1 and any(8 in r.laps for r in rows), t
        assert card.model.hidden_packs == len(state.packs) - len(rows)
        # dots: the map was handed exactly this state
        assert np.array_equal(mp.state.x, state.x) and np.array_equal(mp.state.y, state.y)
    assert len(state.packs) == 9 and rows[-1].laps == (8,), "the selected pack was not appended"
    print("ok rows == packs, selected pack listed")


def test_the_readout_is_interval_to_best():
    """The one fixed-reference line is the selected lap's `interval_to_best`, named with both laps,
    "≈" inside the threshold."""
    tracks = _wide_field()
    rc, clock, mp, card, _ = _controller(tracks)
    rc.enter(selected=4)
    for t in (2.0, 10.0, tracks.duration):
        rc.scrub(t / tracks.duration)
        rc.tick()
        want = lap_race.interval_to_best(lap_race.race_state(tracks, rc.t), 4)
        assert card.model.to_best == want, (card.model.to_best, want)
    text = race_controller.readout_text(card.model)
    assert text == f"L5 vs best L1: +{want:.2f} s", text
    rc.scrub(0.0)
    rc.tick()
    assert race_controller.readout_text(card.model).startswith("L5 vs best L1: ≈ ")
    # A field whose leader is NOT the best lap early on: lap 1 is quick for 300 m, then slow. The
    # readout keeps the best as its reference while the leader (and every row's interval) changes.
    el = np.concatenate([np.linspace(0, 12, 121), 12 + np.linspace(0, 20, 201)[1:]])
    d = np.where(el <= 12, el * 25.0, 300 + (el - 12) * 15.0)
    quick_start = lap_race.RaceLap(lap_id=1, lap_time=32.0, clock_start=0.0, elapsed=el, dist=d,
                                   xs=d.copy(), ys=np.zeros_like(d),
                                   warp=(np.array([0.0, 600.0]), np.array([0.0, 600.0])))
    best, third = _straight(0, 20.0), _straight(2, 600.0 / 31.0)
    tracks = lap_race.build([best, quick_start, third], best, {})
    rc, clock, mp, card, _ = _controller(tracks)
    rc.enter(selected=2)
    rc.scrub(10.0 / tracks.duration)
    rc.tick()
    state = lap_race.race_state(tracks, rc.t)
    assert state.rungs[0].lap_id == 1, "the quick starter should lead at 10 s"
    rung = next(r for r in state.rungs if r.lap_id == 2)
    assert card.model.to_best == lap_race.interval_to_best(state, 2) != rung.interval_s
    print("ok readout == interval_to_best")


def test_provisional_estimated_and_excluded():
    """Provisional → no rows, the reason; estimated → the clock named, the widened tie; the laps
    not raced are counted with their reasons."""
    rc, clock, mp, card, _ = _controller(_wide_field(provisional=True))
    rc.enter(selected=2)
    m = card.model
    assert m.rows == () and m.unranked_reason == lap_race.UNRANKED_PROVISIONAL and m.to_best is None
    assert race_controller.readout_text(m) is None
    assert mp.state is not None and len(mp.state.x) == 9, "an unranked race still draws its dots"
    rc, clock, mp, card, _ = _controller(_wide_field(clock_estimated=True))
    rc.enter(selected=2)
    assert card.model.clock_estimated and card.model.tie_s == lap_race.ESTIMATED_CLOCK_TIE_S
    assert card.model.excluded == "3 laps not raced: 2 not clean, 1 GPS dropout", card.model.excluded
    assert race_controller.excluded_line({1: lap_race.EXCLUDED_NOT_CLEAN}) == \
        "1 lap not raced: 1 not a clean lap"
    assert race_controller.excluded_line({}) is None
    print("ok provisional / estimated / excluded")


def test_the_card_paints_the_model():
    """The real card lists the rows, the readout, the clock and the excluded line it was given."""
    from studio.race_ladder import RaceCard
    rc, clock, mp, _card, _ = _controller(_wide_field(clock_estimated=True))
    card = RaceCard()
    rc.card = card
    rc.enter(selected=8)
    rc.scrub(1.0)
    rc.tick()
    lines = card.lines()
    assert lines[0] == "LAP RACE · FINAL" and lines[1] == race_controller.CAPTION, lines
    assert any(ln.startswith("1 L1") for ln in lines) and any(ln.startswith("9 L9") for ln in lines)
    assert race_controller.readout_text(card.model) in lines
    assert any("video clock" in ln and "0.15 s" in ln for ln in lines), lines
    assert lines[-1] == "3 laps not raced: 2 not clean, 1 GPS dropout"
    card.grab()      # paints without raising
    print("ok card paints the model")


# ---------------------------------------------------------------- the real window
@contextlib.contextmanager
def _window():
    from test_debrief_landing import _fresh_app_support, _open

    from studio import sidecar
    from studio.app import StudioWindow
    from studio.dev import make_demo as md
    from studio.dev import synth_gopro as sg
    saved = sidecar.save
    sidecar.save = lambda *a, **k: None     # a start-line test must not write beside the clip
    with tempfile.TemporaryDirectory(prefix="race_ctl_") as folder, _fresh_app_support():
        rec = sg.generate(os.path.join(folder, "rec"), md.DEMO_SEED, md.DEMO_LAPS, chapters=1,
                          origin=md.DEMO_ORIGIN)
        win = StudioWindow([])
        win.resize(1440, 900)
        win.show()
        try:
            _open(win, rec.paths[0])
            _open(win, rec.paths[0])
            win._escape_out()
            yield win, rec.paths[0], _open
        finally:
            sidecar.save = saved
            win.close()
            win.deleteLater()
            _APP.processEvents()


def _settle(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        _APP.processEvents()
        time.sleep(0.01)


def test_the_race_on_the_real_window():
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    with _window() as (win, path, _open):
        v, s = win.view, win.session
        mp = v.map
        vb = mp.plot.getViewBox()
        pauses = []
        player = v.video.pane.player
        player.pause = lambda: pauses.append(1)
        win._sync_view_menu()
        act = win._race_action
        assert act.isEnabled() and act.shortcut().toString() == "R", act.text()
        before = dict(marker=mp.marker.isVisible(), pos=tuple(mp.marker.pos()),
                      key=mp._map_key.isVisible(), items=set(map(id, vb.addedItems)),
                      scene=len(mp.plot.items), applied=v._playback.applied_t,
                      sel=v.table.selected_ids() if hasattr(v.table, "selected_ids") else None)
        act.trigger()
        assert v.is_racing() and act.isChecked()
        assert pauses, "entering the race did not pause the video"
        assert not mp.marker.isVisible() and not mp._map_key.isVisible()
        race = v.race
        from studio.video_view import PLAYBACK_RATES
        assert race.rates == tuple(PLAYBACK_RATES), "the race does not run on the transport's ladder"
        items = mp.race_items()
        assert len(items) == 3 + len(race.tracks.coloured), len(items)
        for it in items:
            assert it in mp.plot.items and id(it) not in set(map(id, vb.addedItems)), \
                f"{type(it).__name__} joins the view bounds (no ignoreBounds)"
        n_items = len(mp.plot.items)
        builds = lap_race.BUILD_COUNT
        for _ in range(10):                 # the window's own ticks drive it
            _settle(0.05)
        assert race.t > 0, "the race clock did not run on the window's tick"
        race.playing = False
        race.scrub(0.4)
        v.tick()
        state = lap_race.race_state(race.tracks, race.t)
        x, y = mp._race.dots.getData()
        o = mp._race.order
        assert np.allclose(x, state.x[o]) and np.allclose(y, state.y[o]), "dots ≠ race_state"
        assert len(mp.plot.items) == n_items and lap_race.BUILD_COUNT == builds
        rows = v.race_card.model.rows
        assert [r.laps for r in rows] == [p.laps for p in pack_rows(
            state, race.tracks.coloured, race.selected)[0]]
        # Esc ends it; everything is back
        win.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        assert not v.is_racing() and not act.isChecked() and v.race_card.isHidden()
        assert mp.race_items() == [] and len(mp.plot.items) == before["scene"]
        assert set(map(id, vb.addedItems)) == before["items"]
        assert mp.marker.isVisible() == before["marker"] and mp._map_key.isVisible() == before["key"]
        assert tuple(mp.marker.pos()) == before["pos"] and v._playback.applied_t == before["applied"]
        if before["sel"] is not None:
            assert v.table.selected_ids() == before["sel"]
        t_after = race.t
        _settle(0.2)
        assert race.t == t_after, "the race clock runs after exit"
        # compare ends it
        assert v.start_lap_race()
        v.video.compare_btn.click()
        _settle(0.1)
        assert not v.is_racing() and mp.race_items() == []
        if v.compare.active:
            v.video.compare_btn.click()
            _settle(0.1)
        # a start-line re-segment ends it
        assert v.start_lap_race()
        mp.timing_lines_changed.emit(s.start_line, list(s.sector_lines))
        _settle(0.1)
        assert not v.is_racing() and mp.race_items() == []
        # a new load ends it (the outgoing view's race is over and its items gone)
        assert win.view.start_lap_race()
        old = win.view
        _open(win, path)
        assert not old.race.active, "loading a recording left the race running"
        # under two raceable laps: off, with its reason
        win.session.race_tracks = lambda *a, **k: None
        win._sync_view_menu()
        act = win._race_action
        assert not act.isEnabled() and act.toolTip() == race_controller.NO_RACE_REASON
        assert act.text().startswith("Lap race — needs at least two clean laps"), act.text()
        act.trigger()
        assert not win.view.is_racing()
    print("ok real window: enter, draw, tick, exit, compare / re-segment / load end it, gated")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} RACE-CONTROLLER TESTS PASSED")
