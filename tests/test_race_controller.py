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
import shutil
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

    def set_race_follow(self, on):
        self.follow_calls = [*getattr(self, "follow_calls", []), on]


class _Card:
    def __init__(self):
        self.model = None
        self.visible = False

    def configure(self, colours, lap_times):
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
    assert card.model.excluded == "3 laps not raced: 2 not clean (L11, L12), 1 GPS dropout (L10).", \
        card.model.excluded
    assert race_controller.excluded_line({1: lap_race.EXCLUDED_NOT_CLEAN}) == \
        "1 lap not raced: 1 not a clean lap (L2)."
    assert race_controller.excluded_line({}) is None
    # QA EVAL-3: a lap no table lists is a crossing too brief to be a lap, said in the Laps tab's
    # own sentence — not counted as a lap the reader then cannot find.
    from studio.lap_table import too_brief_note
    excl = {4: lap_race.EXCLUDED_DROPOUT, 14: lap_race.EXCLUDED_NOT_CLEAN}
    assert race_controller.excluded_line({14: lap_race.EXCLUDED_NOT_CLEAN}, set(range(14))) == \
        too_brief_note(1)
    assert race_controller.excluded_line(excl, set(range(14))) == \
        f"1 lap not raced: 1 GPS dropout (L5). {too_brief_note(1)}"
    print("ok provisional / estimated / excluded")


def test_finish_gaps_are_built_from_the_printed_milliseconds():
    """At the finish a pack's gap is the difference of the two PRINTED lap times, as the Laps
    table's Δbest is (`lap_table.printed_ms`): 45.0004 and 45.0016 print 0:45.000 and 0:45.002,
    so the gap reads +0.002 — the raw difference, 0.0012, would read +0.001."""
    from studio.race_ladder import interval_text
    row = race_controller.PackRow(position=2, laps=(1,), chips=(1,), more=0, interval_s=0.0012,
                                  spread_s=0.0, selected=False)
    assert interval_text(row, True, {0: 45.0004, 1: 45.0016}) == "+0.002 s"
    lead = race_controller.PackRow(position=1, laps=(0,), chips=(0,), more=0, interval_s=0.0,
                                   spread_s=0.0, selected=False)
    assert interval_text(lead, True, {0: 45.0004, 1: 45.0016}) == "0:45.000"
    assert interval_text(lead, False, {0: 45.0004}) == "leader"
    print("ok finish gaps from printed ms")


def test_a_running_card_shows_one_instant_at_a_readable_rate():
    """While the race runs, the card takes a snapshot at most every CARD_REFRESH_S of wall time and
    shows it whole — its title clock is the time its rows are from — and a pause shows now."""
    from studio.race_ladder import CARD_REFRESH_S, RaceCard
    rc, clock, mp, _card, _ = _controller(_wide_field())
    card = RaceCard()
    rc.card = card
    rc.enter(selected=4, speed=2.0)
    seen = set()
    for _ in range(30):                     # one second of 30 Hz ticks at 2x
        clock.now += 1 / 30
        rc.tick()
        seen.add(card.model.t)
        assert card.lines()[0] == f"LAP RACE · {card.model.t:4.1f} s"
        assert 0 <= rc.t - card.model.t < CARD_REFRESH_S * 2 + 1e-9, (rc.t, card.model.t)
    assert len(seen) <= 1 / CARD_REFRESH_S + 1, len(seen)
    rc.toggle_play()
    rc.tick()
    assert card.model.t == rc.t and not card.model.playing, "a pause did not show the race now"
    print("ok running card: one instant, at most 10 Hz; a pause shows now")


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
    assert lines[-1] == "3 laps not raced: 2 not clean (L11, L12), 1 GPS dropout (L10).", lines
    card.grab()      # paints without raising
    print("ok card paints the model")


# ---------------------------------------------------------------- the real window
def _demo_copy(folder: str) -> str:
    """The published demo's recording (`make_demo.make`, `render=False`) at
    `<folder>/demo/GX019001.MP4`. Made ONCE per run and copied into each test's own folder, so
    every window still opens a file nothing before it has touched: the copy costs milliseconds,
    the make 1.9 s, and this file made it nine times (2026-11-18)."""
    if "src" not in _DEMO:
        from studio.dev import make_demo as md
        _DEMO["dir"] = tempfile.TemporaryDirectory(prefix="race_demo_")
        _DEMO["src"] = os.path.join(_DEMO["dir"].name, "GX019001.MP4")
        md.make(_DEMO["src"], render=False)
    path = os.path.join(folder, "demo", "GX019001.MP4")
    os.makedirs(os.path.dirname(path))
    shutil.copyfile(_DEMO["src"], path)
    return path


_DEMO: dict = {}


@contextlib.contextmanager
def _window():
    """A real, jailed StudioWindow on the PUBLISHED DEMO's recording: `make_demo.make`, the recipe
    the downloadable demo is built from (its driver habit included), with the flat placeholder
    picture (`render=False`: the same telemetry, so the same laps and lap times, in seconds)."""
    from test_debrief_landing import _fresh_app_support, _open

    from studio import sidecar
    from studio.app import StudioWindow
    saved = sidecar.save
    sidecar.save = lambda *a, **k: None     # a start-line test must not write beside the clip
    with tempfile.TemporaryDirectory(prefix="race_ctl_") as folder, _fresh_app_support():
        path = _demo_copy(folder)
        win = StudioWindow([])
        win.resize(1440, 900)
        win.show()
        try:
            _open(win, path)
            _open(win, path)
            win._escape_out()
            yield win, path, _open
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
        pauses, plays = [], []
        player = v.video.pane.player
        player.pause = lambda: pauses.append(1)
        player.play = lambda: plays.append(1)
        win._sync_view_menu()
        act = win._race_action
        assert act.isEnabled() and act.shortcut().toString() == "R", act.text()
        before = dict(marker=mp.marker.isVisible(), pos=tuple(mp.marker.pos()),
                      key=mp._map_key.isHidden(), items=set(map(id, vb.addedItems)),
                      scene=len(mp.plot.items), applied=v._playback.applied_t,
                      sel=v.table.selected_lap_ids(), rate=v.video.playback_rate())
        assert before["sel"], "nothing selected: the selection-restore check would test nothing"
        act.trigger()
        assert v.is_racing() and act.isChecked()
        assert pauses, "entering the race did not pause the video"
        assert not mp.marker.isVisible() and mp._map_key.isHidden()
        race = v.race
        from studio.video_view import PLAYBACK_RATES
        assert race.rates == tuple(PLAYBACK_RATES), "the race does not run on the transport's ladder"
        items = mp.race_items()
        assert len(items) == 4 + len(race.tracks.coloured), len(items)
        for it in items:
            assert it in mp.plot.items and id(it) not in set(map(id, vb.addedItems)), \
                f"{type(it).__name__} joins the view bounds (no ignoreBounds)"
        n_items = len(mp.plot.items)
        builds = lap_race.BUILD_COUNT
        for _ in range(10):                 # the window's own ticks drive it
            _settle(0.05)
        assert race.t > 0, "the race clock did not run on the window's tick"
        # Space is the race's, not the transport's; [ / ] are its speed, not the video's.
        toggles = []
        real_toggle = v.video.toggle
        v.video.toggle = lambda: toggles.append(1)
        was = race.playing
        win.toggle_playback()
        v.video.toggle = real_toggle
        assert race.playing != was and toggles == [], "Space leaked to the video transport"
        speed = race.speed
        win.slower_playback()
        assert race.speed < speed and v.video.playback_rate() == before["rate"], "[ leaked"
        win.faster_playback()
        assert race.speed == speed and v.video.playback_rate() == before["rate"], "] leaked"
        race.playing = False
        race.scrub(0.4)
        v.tick()
        state = lap_race.race_state(race.tracks, race.t)
        x, y = mp._race.positions()
        o = mp._race.order
        assert np.allclose(x, state.x[o]) and np.allclose(y, state.y[o]), "dots ≠ race_state"
        assert len(mp.plot.items) == n_items and lap_race.BUILD_COUNT == builds
        rows = v.race_card.model.rows
        assert [r.laps for r in rows] == [p.laps for p in pack_rows(
            state, race.tracks.coloured, race.selected)[0]]
        # A report exported mid-race shows the ordinary map: no card, no race item in the grab.
        seen = {}

        def spy(widget):
            seen["card"] = v.race_card.isVisible()
            seen["race"] = any(it.isVisible() for it in mp.race_items())
            return b""
        real_grab = win.exports.grab_png
        win.exports.grab_png = spy
        try:
            win._grab_report_map_png(mp)
        finally:
            win.exports.grab_png = real_grab
        assert seen == {"card": False, "race": False}, f"the report grab shows the race: {seen}"
        assert v.race_card.isVisible() and all(it.isVisible() for it in mp.race_items())
        # The card re-renders only on a change of what it shows, not on every map repaint.
        card = v.race_card
        card.repaint()
        renders = card.renders
        for _ in range(5):              # the same content, repainted as the map under it repaints
            card.show_model(card.model)
            card.repaint()
        assert card.renders == renders, (renders, card.renders)
        race.scrub(0.5)
        v.tick()
        card.repaint()
        assert card.renders == renders + 1, "a change of content did not re-render the card"
        # At the finish the intervals are the Laps table's Δbest strings, to the digit.
        from studio.lap_table import _DELTA_COL
        from studio.race_ladder import interval_header, interval_text
        race.scrub(1.0)
        v.tick()
        m = v.race_card.model
        assert m.over and race.tracks.ids[0] == s.best_lap_id()
        table = {v.table._lap_id(r): v.table.table.item(r, _DELTA_COL).text()
                 for r in range(v.table.table.rowCount())}
        assert interval_header(True) == "time / interval" and len(m.rows) > 1
        for r in m.rows[1:]:
            got = interval_text(r, True, v.race_card.lap_times)
            assert got == table[r.laps[0]] + " s", (r.laps[0], got, table[r.laps[0]])
        # Esc ends it; everything is back, and the video stays PAUSED at its playhead.
        win.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        assert not v.is_racing() and not act.isChecked() and v.race_card.isHidden()
        assert mp.race_items() == [] and len(mp.plot.items) == before["scene"]
        assert set(map(id, vb.addedItems)) == before["items"]
        assert mp.marker.isVisible() == before["marker"] and mp._map_key.isHidden() == before["key"]
        assert tuple(mp.marker.pos()) == before["pos"] and v._playback.applied_t == before["applied"]
        assert v.table.selected_lap_ids() == before["sel"], "the selection changed"
        assert plays == [] and not v.video.is_playing(), "ending the race resumed the video"
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
        # a trust flip without a re-segment ends it
        assert v.start_lap_race()
        v.refresh_timing_trust()
        assert not v.is_racing(), "refresh_timing_trust left the race running"
        # the map leaving the screen: a race ends, and the command is off with its reason
        assert v.start_lap_race()
        v._toggle_panel_maximized(v._plots_panel)
        _settle(0.2)
        assert not v.map_on_screen() and not v.is_racing(), "a race kept running off screen"
        win._sync_view_menu()
        assert not win._race_action.isEnabled()
        assert win._race_action.toolTip() == win._NO_MAP_RACE_REASON
        assert not v.start_lap_race()
        v._restore_splitter_sizes()
        _settle(0.2)
        win._sync_view_menu()
        assert v.map_on_screen() and win._race_action.isEnabled()
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
        real_tracks = win.session.race_tracks
        win.session.race_tracks = lambda *a, **k: None
        win._sync_view_menu()
        act = win._race_action
        assert not act.isEnabled() and act.toolTip() == race_controller.NO_RACE_REASON
        assert act.text().startswith("Lap race — needs at least two clean laps"), act.text()
        act.trigger()
        assert not win.view.is_racing()
        win.session.race_tracks = real_tracks
        # closing the window clears the race
        assert win.view.start_lap_race()
        view = win.view
        win.close()                     # closeEvent runs synchronously: no tick in between
        assert not view.race.active, "a closed window left race.active set"
    print("ok real window: enter, draw, keys, report grab, finish = table Δbest, exit (video "
          "paused), compare / trust / off-screen / re-segment / load / close end it, gated")


def test_the_race_puts_out_the_hovered_corner_label():
    """QA REG-3: a corner hovered on the map when the race starts lost its ring but kept the chart
    label lit (with its dotted guide) until the pointer moved — the map cleared its own hover
    without saying so."""
    with _window() as (win, _path, _open):
        v, mp = win.view, win.view.map
        assert mp.pinned_corner() is None, "a pinned ring would keep the label lit: tests nothing"
        mp._set_corner_hovered("C1")        # what a pointer move over C1's apex emits
        assert v.plots._corner_hot == "C1"
        assert v.start_lap_race()
        assert v.plots._corner_hot is None, "the chart label stayed lit under the race"
    print("ok race start puts out the chart's hovered corner label")


def test_the_race_hides_the_drag_handles():
    """QA REG-4: the start line's drag handles stayed revealed, and draggable, over the race's
    dimmed map (corner hover is off under it). The race's end puts them back."""
    with _window() as (win, _path, _open):
        mp = win.view.map
        mp._timing_hover = True             # the pointer near the start line reveals its handles
        mp._sync_timing_chrome()
        assert mp._start.handles_visible
        assert win.view.start_lap_race()
        assert not mp._start.handles_visible, "the drag handles stayed up under the race"
        mp._sync_timing_chrome()            # a pointer move near the line during the race
        assert not mp._start.handles_visible
        win.view.end_lap_race()
        assert mp._start.handles_visible, "the handles did not come back after the race"
    print("ok the drag handles hide under the race and come back after it")


def _race_at(win, est: bool, excluded: dict | None = None):
    """Start a race on the open window, the slowest lap selected (its pack is listed last), held
    mid-race; `est` names an estimated clock, `excluded` replaces the laps not raced."""
    import dataclasses
    v = win.view
    v.end_lap_race()
    assert v.start_lap_race()
    r = v.race
    if est:
        r.tracks = dataclasses.replace(r.tracks, clock_estimated=True)
    if excluded is not None:
        r._excluded = race_controller.excluded_line(excluded, r._listed_laps())
    r.set_selected(r.tracks.ids[-1])
    r.playing = False
    r.scrub(0.5)
    v.tick()
    _settle(0.1)
    return r


def _inside(card, mapw) -> bool:
    return card.geometry().bottom() < mapw.height()


def test_the_card_fits_the_map_at_a_small_window():
    """QA REG-2: at a 1280×800 window the card ran off the map's bottom: the selected lap's
    readout and the not-raced line were cut, worse with the estimated-clock line. The rows now
    give way (into "N more packs"); the card lies inside the map, those lines always show, every
    pack is either listed or counted, and a taller map gets its rows back."""
    with _window() as (win, _path, _open):
        v = win.view
        card, mapw = v.race_card, v.map.widget
        for est in (False, True):
            win.resize(1280, 800)
            _settle(0.3)
            r = _race_at(win, est)
            m = card.model
            assert len(m.rows) > 2 and m.excluded, "too few rows to overflow: tests nothing"
            assert _inside(card, mapw), (est, card.geometry(), mapw.height())
            lines = card.lines()
            assert race_controller.readout_text(m) in lines and m.excluded in lines, lines
            assert any("pack" in ln for ln in lines[2:]), "the dropped packs are not counted"
        for size in ((1280, 800), (1440, 900), (1728, 1117)):
            win.resize(*size)
            _settle(0.3)
            shown, packs = card._shown, len(r.state().packs)
            assert len(shown.rows) + shown.hidden_packs == packs, (size, len(shown.rows),
                                                                   shown.hidden_packs, packs)
            assert _inside(card, mapw), size
        assert len(card.lines()) > len(lines), "a taller map did not get its rows back"
    print("ok the card fits the map at 1280×800 (plain and estimated clock); listed + counted = "
          "every pack at three sizes; rows return when taller")


def test_a_long_not_raced_list_stays_two_rows_and_inside_the_map():
    """QA review of #561: naming every lap not raced made the line unbounded (24 not clean + 3
    dropouts + a brief crossing = 247 characters, four wrapped rows), which took every pack row
    from a 192 px map and, with the estimated-clock line, overflowed it again. Each reason now
    names three laps and counts the rest; the fit measures the line's real height."""
    from studio.lap_table import too_brief_note
    with _window() as (win, _path, _open):
        s = win.session
        not_clean, dropouts = list(range(100, 124)), list(range(200, 203))
        s.excluded_lap_ids = lambda: not_clean + dropouts      # listed in the ⊘ strip
        big = {**{i: lap_race.EXCLUDED_NOT_CLEAN for i in not_clean},
               **{i: lap_race.EXCLUDED_DROPOUT for i in dropouts},
               14: lap_race.EXCLUDED_NOT_CLEAN}                # the demo's brief crossing
        v = win.view
        card, mapw = v.race_card, v.map.widget
        win.resize(1280, 800)
        _settle(0.3)
        for est in (False, True):
            _race_at(win, est, big)
            line = card._shown.excluded
            assert _inside(card, mapw), (est, card.geometry(), mapw.height())
            assert line == ("27 laps not raced: 24 not clean (L101, L102, L103 +21), "
                            f"3 GPS dropouts (L201, L202, L203). {too_brief_note(1)}"), line
            from studio.race_ladder import _PAD
            fm, inner = card._fm, card.width() - 2 * _PAD
            laps = line[:line.index(too_brief_note(1))].strip()     # the laps sentence alone
            assert card._wrapped_h(laps, inner) <= 2 * fm.lineSpacing() + 2, laps
            assert line in card.lines()
    print("ok a long not-raced list names three laps per reason, wraps to ≤ 2 rows, fits the map")


def test_a_very_short_map_drops_the_caption_then_the_bar():
    """QA review of #561: below ~153 px of map the zero-row card still ran off the bottom. The
    caption gives way first, then the progress bar, so the readout and the notes stay inside down
    to the floors measured here; below them the card is clipped (documented in `_fit`)."""
    with _window() as (win, _path, _open):
        v = win.view
        card, mapw = v.race_card, v.map.widget
        win.resize(1280, 800)
        _settle(0.3)
        from studio.race_controller import CAPTION
        seen = set()
        # The measured floors (the card's `_fit` docstring): ~120 px plain, ~136 px with the
        # estimated-clock line, against 153 px before.
        for est, heights in ((False, (150, 131, 120, 112)), (True, (150, 140, 131))):
            _race_at(win, est)
            for h in heights:
                mapw.resize(mapw.width(), h)       # what a dragged splitter does to the map
                _APP.processEvents()
                assert _inside(card, mapw), (est, h, card.geometry())
                lines = card.lines()
                assert race_controller.readout_text(card.model) in lines, (h, lines)
                chrome = getattr(card, "_chrome", 2)
                assert (CAPTION in lines) == (chrome >= 2), (h, chrome, lines)
                seen.add(chrome)
        assert {1, 0} <= seen, seen
    print("ok a very short map drops the caption, then the bar; the readout stays inside")


def test_r_races_whenever_the_map_is_on_screen_and_says_why_when_not():
    """QA EVAL-1 / REG-1: View ▸ Lap race's enabled state was a cache synced at load — BEFORE the
    window's layout, so the map read as off screen — and again only when the View menu or ⌘K
    opened, a race started or ended, or a timing edit landed. A disabled action's key is dropped
    by Qt in silence, so R did nothing after every load, after Esc left the debrief, and after a
    maximise was restored. Driven by REAL key presses (`QTest.keyClick`, through Qt's shortcut
    map) and with no View menu opened, because opening it was what hid the defect."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from test_debrief_landing import _fresh_app_support, _open

    from studio import command_palette, sidecar
    from studio.app import StudioWindow

    def press_r():
        QTest.keyClick(win, Qt.Key_R)
        _settle(0.05)

    def palette_row():
        return next(e for e in command_palette.entries(win) if e.action is act)

    reason = StudioWindow._NO_MAP_RACE_REASON
    no_map = f"Lap race: {reason[0].lower()}{reason[1:]}"
    saved = sidecar.save
    sidecar.save = lambda *a, **k: None
    with tempfile.TemporaryDirectory(prefix="race_key_") as folder, _fresh_app_support():
        path = _demo_copy(folder)
        win = StudioWindow([])
        win.resize(1440, 900)
        win.show()
        try:
            _open(win, path)            # the demo's first open lands on the debrief
            v, act, bar = win.view, win._race_action, win.statusBar()
            assert v._maximized_panel is not None and not v.map_on_screen(), "no debrief landing"
            # On the debrief: R starts nothing and says why.
            press_r()
            assert not v.is_racing(), "R raced with the map off screen"
            assert bar.currentMessage() == no_map, bar.currentMessage()
            # The ⌘K row: off, with the same reason (the rule: disabled while the map is hidden).
            row = palette_row()
            assert not row.enabled and row.reason == StudioWindow._NO_MAP_RACE_REASON, row
            # Esc leaves the debrief. The map's return now re-reads the gate (the toolbar button's
            # binding, `app._RaceButtonBinding`), so the cache is fresh here; it is put back stale
            # by hand so R's own re-gate (`_ShortcutGate`) is still what this exercises.
            QTest.keyClick(win, Qt.Key_Escape)
            _settle(0.3)
            assert v.map_on_screen() and act.isEnabled(), "the map came back, the gate did not"
            win._gate_action(act, False, reason)
            assert not act.isEnabled(), "the stale state is not exercised"
            press_r()
            assert v.is_racing(), "R did not race after Esc left the debrief"
            assert bar.currentMessage() == "", "the refusal outlived the race it refused"
            # QA EVAL-3: the demo's 15th "lap" is a crossing too brief to be one; the card says
            # it in the Laps strip's / DATA TRUST card's sentence, not as a lap not raced.
            from studio.lap_table import too_brief_count, too_brief_note
            s = win.session
            brief = too_brief_count(s, len(s.valid_lap_ids()) + len(s.excluded_lap_ids()))
            assert brief == 1, "the demo's brief crossing is gone: this check tests nothing"
            assert v.race_card.model.excluded == too_brief_note(brief), v.race_card.model.excluded
            # Maximise during the race (it ends), restore: R races again.
            v._toggle_panel_maximized(v._plots_panel)
            _settle(0.2)
            assert not v.is_racing()
            press_r()
            assert not v.is_racing() and bar.currentMessage() == no_map, bar.currentMessage()
            v._restore_splitter_sizes()
            _settle(0.2)
            press_r()
            assert v.is_racing(), "R did not race after the grid was restored"
            press_r()                   # R again ends it
            assert not v.is_racing()
            # The ⌘K row runs it once the map is back.
            row = palette_row()
            assert row.enabled, row
            row.run()
            assert v.is_racing(), "the palette row did not race"
            v.end_lap_race()
            # A plain re-open (no debrief): R races with no menu opened since the load.
            _open(win, path)
            v = win.view
            assert v._maximized_panel is None and v.map_on_screen()
            press_r()
            assert v.is_racing(), "R did not race after a plain re-open"
        finally:
            sidecar.save = saved
            win.close()
            win.deleteLater()
            _APP.processEvents()
    print("ok R: races once the map is back (Esc, restore, re-open) with no menu opened; says why "
          "on the debrief and under a maximise; the ⌘K row agrees")


def _toolbar_fits(v) -> None:
    """Nothing on the map toolbar is clipped, overlapped or pushed: every laid-out control at its
    own width, the race button (when shown) clear of the right-hand run, and the map panel's
    minimum still the run's own need — under the charts panel's, as test_map_key holds."""
    tb, btn = v._map_toolbar, v.race_btn
    run = list(tb.leading + tb.controls)
    for c in run:
        assert c.width() == c.sizeHint().width(), (type(c).__name__, c.width(), c.sizeHint())
    assert run[-1].geometry().right() < tb.width(), "the run is pushed out of the bar"
    if btn.isVisible():
        assert btn.width() == btn.sizeHint().width(), ("race button clipped", btn.width())
        assert btn.geometry().right() + tb.layout().spacing() <= run[0].geometry().left(), (
            "the race button overlaps the run", btn.geometry(), run[0].geometry())
        assert btn.height() == run[0].height(), "the race button is not the controls' height"
    assert tb.minimumSizeHint().width() == tb.layout().sizeHint().width()
    assert v._map_panel.minimumSizeHint().width() <= v._plots_panel.minimumSizeHint().width()


def test_the_map_toolbar_button_is_view_lap_race():
    """The Lap race had no visible entry (View ▸ Lap race, R or ⌘K only). The map toolbar now has a
    "Lap race" button that IS that command: a click runs the action, and the button carries the
    action's check, enabled state and tooltip — so every exit route unchecks it, a shut gate greys
    it with the menu item's own reason, and the map's return re-opens it without a menu sync."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QSplitter
    from test_debrief_landing import _fresh_app_support, _open

    from studio import sidecar
    from studio.app import StudioWindow
    saved = sidecar.save
    sidecar.save = lambda *a, **k: None
    with tempfile.TemporaryDirectory(prefix="race_btn_") as folder, _fresh_app_support():
        path = _demo_copy(folder)
        win = StudioWindow([])
        win.resize(1280, 800)
        win.show()
        try:
            _open(win, path)            # the demo's first open lands on the debrief
            v, act = win.view, win._race_action
            btn = v.race_btn
            # The debrief: the map is maximised away, and so is the button (not on its picture).
            assert v._maximized_panel is not None and not btn.isVisible()
            win._escape_out()
            _settle(0.3)
            # The default layout at 1280x800: shown and enabled with no menu opened since the load.
            assert btn.isVisible() and btn.isEnabled(), (btn.isVisible(), btn.isEnabled())
            assert btn.text() == act.property("featureText") == "Lap race", btn.text()
            assert btn.accessibleName() == "Lap race"
            assert btn.toolTip() == act.toolTip() and btn.toolTip().startswith("Lap race (R): ")
            assert not btn.icon().isNull(), "a labelled toolbar control without its glyph"
            assert v._map_toolbar.isAncestorOf(btn) and not v.map.isAncestorOf(btn)
            _toolbar_fits(v)
            # The report's map picture is the map widget's: the button is not in it.
            shown_png = win._grab_report_map()[0]
            btn.hide()
            assert win._grab_report_map()[0] == shown_png, "the button changed the report's map"
            btn.place()
            assert btn.isVisible()
            # A click races, and the button reads checked; a lap pick re-points the race, no more.
            btn.click()
            assert v.is_racing() and btn.isChecked() and act.isChecked()
            ids = win.session.valid_lap_ids()
            v.table.laps_selected.emit([ids[1]])
            assert v.is_racing() and btn.isChecked(), "a lap pick unticked a running race"

            def start():
                if not v.is_racing():
                    btn.click()
                assert v.is_racing() and btn.isChecked()

            def compare():
                v.video.compare_btn.click()
                _settle(0.1)
                if v.compare.active:
                    v.video.compare_btn.click()
                    _settle(0.1)

            def maximise():
                v._toggle_panel_maximized(v._plots_panel)
                _settle(0.2)            # the tick ends a race whose map left the screen

            routes = {
                "the button again": btn.click,
                "R": lambda: QTest.keyClick(win, Qt.Key_R),
                "View ▸ Lap race": act.trigger,
                "Esc": lambda: win.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape,
                                                           Qt.NoModifier)),
                "the card's ×": v.race_card.closeRequested.emit,
                "compare": compare,
                "a maximise": maximise,
                "a start-line re-segment": lambda: v.map.timing_lines_changed.emit(
                    win.session.start_line, list(win.session.sector_lines)),
                "a trust refresh": v.refresh_timing_trust,
            }
            for name, route in routes.items():
                start()
                route()
                _settle(0.05)
                assert not v.is_racing(), f"{name} did not end the race"
                assert not btn.isChecked() and not act.isChecked(), f"{name} left it checked"
                if name == "a maximise":
                    # Off screen: greyed with the menu item's reason; restored: back with no sync.
                    win._sync_view_menu()
                    assert not btn.isEnabled() and btn.toolTip() == win._NO_MAP_RACE_REASON
                    v._restore_splitter_sizes()
                    _settle(0.2)
                    assert btn.isEnabled() and btn.toolTip() == act.toolTip(), btn.toolTip()
            # Gated: two raceable laps or none — greyed, with the menu item's own reason.
            real = win.session.race_tracks
            win.session.race_tracks = lambda *a, **k: None
            win._sync_view_menu()
            assert not btn.isEnabled() and not act.isEnabled()
            assert btn.toolTip() == act.toolTip() == race_controller.NO_RACE_REASON
            win.session.race_tracks = real
            win._sync_view_menu()
            assert btn.isEnabled()
            # Narrowest map panel the splitter allows: the button steps aside, nothing clips.
            main = v._map_panel
            while not (isinstance(main, QSplitter) and main.orientation() == Qt.Horizontal):
                main = main.parentWidget()
            main.setSizes([5000, 1])
            _settle(0.2)
            assert v._map_panel.width() < 660 and not btn.isVisible(), v._map_panel.width()
            _toolbar_fits(v)
            main.setSizes([1, 5000])
            _settle(0.2)
            assert btn.isVisible()
            _toolbar_fits(v)
            # A new load: the outgoing race ends, and the new view's button starts unchecked.
            start()
            old = v
            _open(win, path)
            assert not old.race.active and not win.view.race_btn.isChecked()
            assert not act.isChecked()
        finally:
            sidecar.save = saved
            win.close()
            win.deleteLater()
            _APP.processEvents()
    print("ok the map toolbar's Lap race button: one command with View ▸ Lap race, checked while a "
          "race runs, unchecked by every exit, greyed with the menu's reason, fits at 1280x800")


def test_follow_is_kept_between_races_and_said_to_the_window():
    """Follow (the map's view follows the field) is the controller's switch: it outlives a race
    (the next one starts in it), reaches the map only while a race runs, is on the card's model,
    and every change is said through `on_follow` (the window remembers it)."""
    rc, _clock, mp, card, _ = _controller(_wide_field())
    said = []
    rc.on_follow = said.append
    assert not rc.follow and rc.enter(selected=None)
    assert not hasattr(mp, "follow_calls"), "Follow off still reached the map"
    assert card.model.follow is False
    rc.set_follow(True)
    assert mp.follow_calls == [True] and card.model.follow is True and said == [True]
    rc.set_follow(True)                 # no change: nothing said twice
    assert mp.follow_calls == [True] and said == [True]
    rc.end()
    rc.toggle_follow()
    rc.toggle_follow()                  # off and on again between races: the map never hears it
    assert mp.follow_calls == [True] and said == [True, False, True]
    assert rc.enter(selected=None) and mp.follow_calls == [True, True], "the next race forgot it"
    print("ok follow: kept between races, on the model, said to the window, map only in a race")


def _follow_geometry(mp):
    """(view width px, height px, free area's left px, free width px) of the map now."""
    r = mp.plot.getViewBox().rect()
    w, h = r.width(), r.height()
    x0 = mp._free_x0(w)
    return w, h, x0, w - x0


def _ref_offset_px(mp):
    """The camera's reference point's offset (px) from the free area's centre, and the dead-zone
    box (px). The reference is the median of what the width is fitted to (the middle 80 % of the
    dots and the selected lap); `cam.ref` is that point as the camera computed it this tick."""
    from studio import map_view as mv
    rx, ry = mp._follow.ref
    (xl, xh), (yl, yh) = mp.plot.getViewBox().viewRange()
    w, h, x0, fw = _follow_geometry(mp)
    px = (rx - xl) / (xh - xl) * w
    py = (yh - ry) / (yh - yl) * h
    return (abs(px - (x0 + fw / 2)), abs(py - h / 2)), (mv.FOLLOW_BOX * fw, mv.FOLLOW_BOX * h)


def test_follow_on_the_real_window():
    """Follow on the published demo in the real window, offscreen. OFF: a race's ticks never touch
    the view (no range change at all) and its frames are the frames of a race Follow never ran
    in. ON: the median dot stays inside the dead-zone box at 1×, 2× and through scrubs, motion on
    and off; the ground width is one of the ladder's widths to a pixel at three map sizes and a
    resize keeps it; the scale bar's bar is as long as its label says; ticks move items, never add
    them. LEAVING by any path puts the view back exactly (pixel-identical to the map before the
    race where nothing else changed the map); off outside a race with its reason; remembered."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from studio import map_view as mv
    from studio import motion, prefs
    from studio.app import StudioWindow, gate_refusal
    with _window() as (win, path, _open):
        v, mp = win.view, win.view.map
        vb = mp.plot.getViewBox()
        act = win._race_follow_action
        # Outside a race: off, with its reason, and ⇧R says it.
        win._sync_view_menu()
        assert not act.isEnabled() and act.toolTip() == win._NO_RACE_FOLLOW_REASON, act.toolTip()
        assert act.shortcut().toString() == "Shift+R" and not act.isChecked()
        QTest.keyClick(win, Qt.Key_R, Qt.ShiftModifier)
        _settle(0.05)
        assert win.statusBar().currentMessage() == gate_refusal(act)
        assert not win._race_follow, "⇧R outside a race changed the pref"
        before_range = vb.viewRange()
        before_px = mp.widget.grab().toImage()
        changes = []
        vb.sigRangeChanged.connect(lambda *_: changes.append(1))
        # OFF: a whole race at 2× never touches the range; its frames match a never-followed race.
        assert v.start_lap_race(2.0, (0.25, 0.5, 1.0, 2.0))
        race = v.race
        race.playing = False
        clocks = np.linspace(0.0, race.tracks.duration, 25)
        plain = []
        for t in clocks:
            race.t = float(t)
            race._dirty = True
            race.tick()
            plain.append(mp.widget.grab().toImage())
        assert changes == [] and vb.viewRange() == before_range, "Follow off moved the view"
        assert mp._scale_bar.isHidden()
        win._sync_view_menu()
        assert act.isEnabled() and not act.isChecked()
        # ON. Items are moved, never re-created; the median stays in the box; width on the ladder.
        act.trigger()
        assert race.follow and mp.race_following() and act.isChecked() and win._race_follow
        assert prefs.get("race_follow") is True, "the choice was not remembered"
        assert vb.viewRange() != before_range and not mp._scale_bar.isHidden()
        n_items, builds = len(mp.plot.items), lap_race.BUILD_COUNT
        real_enabled = motion.enabled
        relayouts = []
        real_relayout = mp._relayout_labels
        mp._relayout_labels = lambda *a, **k: (relayouts.append(1), real_relayout(*a, **k))[1]
        for motion_on in (False, True):
            motion.enabled = lambda on=motion_on: on
            try:
                for speed in (1.0, 2.0):
                    race.scrub(0.0)
                    race.tick()
                    worst = 0.0
                    relayouts.clear()
                    goals = 0
                    goal = mp._follow.goal
                    for t in np.arange(0.0, race.tracks.duration, speed / 30):
                        race.t = float(t)
                        race._dirty = True
                        race.tick()
                        (dx, dy), (bx, by) = _ref_offset_px(mp)
                        assert dx <= bx + 1 and dy <= by + 1, (motion_on, speed, t, dx, dy, bx, by)
                        worst = max(worst, dx / bx, dy / by)
                        assert mp._follow.w in mv.FOLLOW_WIDTHS_M or motion_on, mp._follow.w
                        goals += mp._follow.goal != goal
                        goal = mp._follow.goal
                    # The corner labels re-lay once per zoom step, never on every tick of an eased
                    # zoom (a re-layout a tick took the p95 tick from 13 to 42 ms).
                    assert goals > 0, "the width never changed: the re-layout check tests nothing"
                    assert len(relayouts) <= goals, (motion_on, speed, len(relayouts), goals)
                    for f in (0.8, 0.1, 0.55, 0.3):
                        race.scrub(f)
                        race.tick()
                        (dx, dy), (bx, by) = _ref_offset_px(mp)
                        assert dx <= 1 and dy <= 1, ("a scrub did not recentre", f, dx, dy)
            finally:
                motion.enabled = real_enabled
        mp._relayout_labels = real_relayout
        assert worst > 0.05, "the median never moved off centre: the box test tests nothing"
        assert len(mp.plot.items) == n_items and lap_race.BUILD_COUNT == builds, "a tick rebuilt"
        # The ground width at three map sizes, a resize keeping it, and the scale bar's honesty.
        race.scrub(0.45)
        race.tick()
        width = mp._follow.w
        for size in ((1440, 900), (1728, 1117), (1180, 820)):
            win.resize(*size)
            _settle(0.2)
            (xl, xh), _ = vb.viewRange()
            w_px = _follow_geometry(mp)[0]
            assert mp._follow.w == width, ("a resize changed the ground width", size)
            assert abs(xh - xl - width) <= width / w_px, (size, xh - xl, width)
            bar = mp._scale_bar
            assert abs(bar.length_px * (xh - xl) / w_px - bar.metres) <= (xh - xl) / w_px, size
            assert bar.label == f"{bar.metres:g} m" and bar.geometry().right() <= mp.widget.width()
        win.resize(1440, 900)
        _settle(0.2)
        # Pausing holds the view.
        race.playing = False
        held = vb.viewRange()
        for _ in range(5):
            race.tick()
        assert vb.viewRange() == held, "a paused race moved the view"
        # Off (the card's own button): the view is back exactly; the frames are a plain race's.
        rect = v.race_card._hits["follow"]
        QTest.mouseClick(v.race_card, Qt.LeftButton, Qt.NoModifier,
                         QPoint(int(rect.center().x()), int(rect.center().y())))
        assert not race.follow and not act.isChecked() and prefs.get("race_follow") is False
        if _APP.focusWidget() is not None:     # the click focused the map: its ring is not Follow's
            _APP.focusWidget().clearFocus()
        assert vb.viewRange() == before_range and mp._scale_bar.isHidden()
        assert vb.state["mouseEnabled"] == [True, True]
        for t, img in zip(clocks, plain, strict=True):
            race.t = float(t)
            race._dirty = True
            race.tick()
            assert mp.widget.grab().toImage() == img, f"frame at {t:.2f} s differs after Follow"
        # Every way out of a race puts the view back exactly.
        race.set_follow(True)

        def followed():
            assert v.start_lap_race()
            assert mp.race_following()
            v.race.scrub(0.4)
            v.race.tick()
            assert vb.viewRange() != before_range

        def esc():
            QTest.keyClick(win, Qt.Key_Escape)

        def r_key():
            QTest.keyClick(win, Qt.Key_R)

        def card_close():
            v.race_card.closeRequested.emit()

        for leave in (esc, r_key, card_close):
            v.end_lap_race()
            followed()
            leave()
            _settle(0.05)
            assert not v.is_racing() and not mp.race_following(), leave.__name__
            assert vb.viewRange() == before_range, leave.__name__
            assert mp.widget.grab().toImage() == before_px, f"{leave.__name__}: map not restored"
        followed()
        v._toggle_panel_maximized(v._plots_panel)       # another panel maximised: the race ends
        _settle(0.2)
        assert not v.is_racing() and not mp.race_following()
        v._restore_splitter_sizes()
        _settle(0.2)
        assert vb.viewRange() == before_range, "maximise: view not restored"
        assert mp.widget.grab().toImage() == before_px, "maximise: map not restored"
        for leave in (lambda: v.video.compare_btn.click(),
                      lambda: mp.timing_lines_changed.emit(win.session.start_line,
                                                           list(win.session.sector_lines))):
            followed()
            leave()
            _settle(0.1)
            assert not v.is_racing() and not mp.race_following() and mp._scale_bar.isHidden()
            assert vb.state["mouseEnabled"] == [True, True]
            if v.compare.active:
                v.video.compare_btn.click()
                _settle(0.1)
        followed()
        old_map = mp
        _open(win, path)                # another recording: the old race and its Follow are over
        assert not old_map.race_following() and win.view.race.follow, "the choice did not carry"
        # Remembered across a new window (the jailed prefs).
        win2 = StudioWindow([])
        try:
            assert win2._race_follow is True
            _open(win2, path)
            assert win2.view.race.follow is True
        finally:
            win2.close()
            win2.deleteLater()
    print("ok follow: off = view untouched and frames identical; on = median in the box (1×/2×, "
          "scrubs, motion on/off), ladder width ±1 px at 3 sizes, honest scale bar, no rebuild; "
          "every exit restores the view exactly; gated with its reason; remembered")


def test_follow_completes_its_zoom_when_race_time_holds():
    """Review of #576. The zoom eases in RACE time, so when race time stopped — the finish, a pause
    — an unfinished zoom froze half-way (the demo at 1×, motion on: target 30 m, the view held at
    58.5 m for the whole final classification) and the corner labels stayed laid for the old scale.
    Also the two mutants the first tests let live (pan/zoom left on under Follow; a report grab that
    kept Follow's zoom), a grab after a resize under Follow (it must frame the map at its size now),
    and the scale bar never showing before it has a scale."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from studio import motion
    with _window() as (win, _path, _open):
        v, mp = win.view, win.view.map
        vb = mp.plot.getViewBox()
        cm = mp._corner_markers
        assert cm._texts, "no corner labels: the label-scale checks would test nothing"

        def labels_match_view():
            (sx, sy), (lx, ly) = cm._px_per_data(), cm.layout_scale
            return abs(sx - lx) <= 1e-6 * lx and abs(sy - ly) <= 1e-6 * ly

        real_enabled = motion.enabled
        motion.enabled = lambda: True
        try:
            assert v.start_lap_race(1.0, (0.25, 0.5, 1.0, 2.0))
            race = v.race
            clock = {"now": 0.0}
            race._clock = lambda: clock["now"]
            race._last = 0.0
            # The scale bar shows only once it has a scale (Follow switched on before any state).
            mp._race_state = None
            mp.set_race_follow(True)
            assert mp._scale_bar.isHidden(), "the scale bar showed before its first scale"
            mp.update_race(race.state())
            assert not mp._scale_bar.isHidden() and mp._scale_bar.metres > 0
            mp.set_race_follow(False)
            race.set_follow(True)
            assert vb.state["mouseEnabled"] == [False, False], "pan/zoom left on under Follow"
            # A pause in the middle of a zoom completes it, and the labels follow the scale.
            race.scrub(0.0)
            race.tick()
            paused = None
            while race.playing:
                clock["now"] += 1 / 30
                race.tick()
                cam = mp._follow
                if cam.w != cam.goal and race.t > 1.0:
                    race.toggle_play()
                    paused = race.t
                    for _ in range(5):
                        clock["now"] += 1 / 30
                        race.tick()
                    break
            assert paused is not None, "no zoom in the demo race: the pause check tests nothing"
            assert mp._follow.w == mp._follow.goal, (paused, mp._follow.w, mp._follow.goal)
            assert labels_match_view(), (paused, cm.layout_scale, cm._px_per_data())
            # The finish: the last zoom completes and the labels follow it. On the way the selected
            # lap's dot stays in view: the width fits it with the middle 80 % of the field.
            race.toggle_play()
            seen_sel = []
            while race.playing:
                clock["now"] += 1 / 30
                race.tick()
                st = race.state()
                if race.selected in st.ids:
                    k = st.ids.index(race.selected)
                    (xl, xh), (yl, yh) = vb.viewRange()
                    seen_sel.append(xl <= st.x[k] <= xh and yl <= st.y[k] <= yh)
            assert len(seen_sel) > 100 and np.mean(seen_sel) >= 0.98, np.mean(seen_sel)
            for _ in range(30):
                clock["now"] += 1 / 30
                race.tick()
            cam = mp._follow
            assert cam.w == cam.goal, ("the finish froze a zoom", cam.w, cam.goal)
            (xl, xh), _ = vb.viewRange()
            assert abs((xh - xl) - cam.goal) <= cam.goal / vb.rect().width()
            assert labels_match_view(), ("labels laid for another scale at the finish",
                                         cm.layout_scale, cm._px_per_data())
        finally:
            motion.enabled = real_enabled
        # A report grab under Follow, after a resize: the map framed at its size now, not Follow's
        # zoom and not the framing from before the resize.
        win.resize(1728, 1117)
        _settle(0.2)
        seen = {}
        with mp.grab_clean():
            seen["range"] = vb.viewRange()
        assert vb.viewRange() != seen["range"], "Follow's view did not come back after the grab"
        race.set_follow(False)
        assert seen["range"] == vb.viewRange(), ("the grab did not frame the resized map",
                                                 seen["range"], vb.viewRange())
        QTest.keyClick(win, Qt.Key_Escape)
        win.resize(1440, 900)
        _settle(0.1)
        # A race started from compare (#575 reworked compare's exits): compare ends, the
        # remembered Follow is on, and leaving puts the plain map's view back.
        race.set_follow(True)
        assert not v.is_racing()
        before = vb.viewRange()
        v.video.compare_btn.click()
        _settle(0.1)
        if v.compare.active:
            assert v.start_lap_race(1.0, (0.25, 0.5, 1.0, 2.0))
            assert not v.compare.active and mp.race_following(), "a race from compare lost Follow"
            v.end_lap_race()
            assert not mp.race_following() and mp._scale_bar.isHidden()
        else:
            assert vb.viewRange() == before
    print("ok follow: a pause or the finish completes the zoom and re-lays the labels; pan/zoom "
          "off under Follow; a grab frames the resized map; no scale bar before its scale")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} RACE-CONTROLLER TESTS PASSED")
