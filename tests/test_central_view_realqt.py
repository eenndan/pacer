"""Real-Qt regression tests for the controller<->view fan-out (the PR#80/#81 blind spot, C3).

The existing controller tests (tests/test_controllers.py) drive ScrubController / CompareController
DIRECTLY against fake recorder views, and the existing video tests (tests/test_video_view_compare.py)
drive a real VideoView in isolation. NEITHER ever builds the real CentralView, lets a real QTimer
fire, or drives the controller<->view fan-out through ACTUAL Qt SIGNAL EMISSION. That is the exact
bug SHAPE that already shipped twice:

  * PR#80 issue 1 — the compare toggle's programmatic button-sync RE-EMITTED compareToggled, which
    re-entered the handler and clobbered pane B (signal re-entrancy on a cross-widget setter);
  * PR#81 — stale cross-widget state on a tick / the compare flag and the two-pane layout drifting
    apart (the FAKE-view tests passed green through both).

C4 made CompareController the single source of truth for compare (VideoView is a dumb renderer that
derives its layout from the live widget tree). These tests pin that REAL wiring: they construct a
REAL CentralView exactly as StudioWindow does (real ScrubController + CompareController + the shared
PlaybackState, cross-wired to the real VideoView / MapView / PlotsView / LapTable), drive it through
the production signals (compare_btn.click -> compareToggled, plots.scrubStarted/Moved/Ended,
video.positionChanged), let the REAL ~30 Hz QTimer fire, and assert no signal re-entrancy + consistent
cross-widget state.

CI-runnable without ~/Desktop/D24: PACER_NO_MEDIA=1 builds PlayerPane's inert media triplet (the full
production widget tree + signal wiring, no decoder/audio device), and the session is the deterministic
two-lap stadium-loop synthetic (tests/test_session_services._synthetic_session — REAL corner detection
+ a seeded g-meter) augmented with the handful of pacer-laps-backed reads CentralView's panels touch
(lap_rows / sector splits / timing-line Seg via a tiny pacer Sectors). So the whole real fan-out runs
on synthetic data with NO media file.

Coverage honesty: the only CI-available REAL media (3rdparty/gpmf-parser/samples/hero6.mp4) has 0
valid laps, so the compare/lap-state assertions below run on the synthetic 2-lap session, not on a
real Session.load. The wiring exercised — the signal connections, the QTimer, the controller<->view
fan-out — is identical regardless of how the session was built. Run:
    QT_QPA_PLATFORM=offscreen python tests/test_central_view_realqt.py
"""
import contextlib
import os
import re
import sys
import time
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# A committed clip is required INSIDE the test that opens it, before any window (and up front by
# this file's own runner): two dozen files import this one's helpers and need no clip.
import _samples  # noqa: E402

# Build the real widget tree with the inert media triplet (no decoder/audio device) — set BEFORE
# importing the studio widgets (the seam is read once at PlayerPane construction).
os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFontMetrics, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow  # noqa: E402

_APP = QApplication.instance() or QApplication([])

# _synthetic_session lives in test_session_services (a sibling test module, not a registered test on
# its own import); reuse its REAL-corner-detection stadium fixture instead of re-deriving one.
from test_session_services import _synthetic_session  # noqa: E402

from studio import chapters, data_quality, render_cache, theme, tracks  # noqa: E402
from studio._signal import fmt_time  # noqa: E402
from studio.central_view import CentralView  # noqa: E402

# --------------------------------------------------------------------- fixture
# The synthetic session's first fix, on the wall clock: 2025-08-30 12:00 UTC. Noon, so the LOCAL
# calendar day `Session.session_date` derives is the same date in every timezone from UTC-11 to +11.
_WALL_CLOCK_MS = 1_756_555_200_000


_SEAM_NAMES: list[str] = []


def _jail_every_live_app_support_seam():
    """Point every `studio` app-support seam that STILL resolves to the developer's real
    `~/Library/Application Support/pacer` at a throwaway dir, and leave alone any seam the calling
    test file already diverted (test_load_affordances keeps a demo clip in its own demo dir).

    WHY THE FIXTURE OWNS THIS. Until H6 the `_Laps` double below had no point API, so building a
    library entry for this session always raised, and every write downstream of it was dead code.
    With the entry resolving, `refresh_library_entry` really upserts. test_load_failure diverts no
    seam and fires `timingEdited`, and the first run with the fixed double wrote a `stadium` row
    into the owner's real library.json. Seventeen files use this fixture and ten of them divert
    nothing, so the guarantee belongs here, not in each caller.

    Not `_jail.divert_app_support`: that decides on `library` alone and then repoints all seven,
    which would move a seam a caller had already diverted elsewhere."""
    import importlib
    import tempfile

    from test_golden_hermetic import seams_in_studio

    from studio.dev import _jail
    real = os.path.abspath(_jail._REAL_DIR)
    target = None
    # Which modules HAVE a seam is a property of the source, read once per process (it parses every
    # studio/*.py: 0.31 s, paid on every one of this file's 62 views until 2026-11-18). Where each
    # seam POINTS is still checked on every call below.
    if not _SEAM_NAMES:
        _SEAM_NAMES.extend(sorted(seams_in_studio()))
    for name in _SEAM_NAMES:
        mod = importlib.import_module(f"studio.{name}")
        if os.path.abspath(mod._app_support_dir()) == real:
            target = target or tempfile.mkdtemp(prefix="pacer-test-realqt-")
            mod._app_support_dir = lambda t=target: t


def _real_central_view():
    """A REAL CentralView (its production __init__: real panels + real ScrubController /
    CompareController / PlaybackState / signal wiring) over the two-lap stadium synthetic session.

    The synthetic Session already serves the REAL delta / corner / driving / g-meter math; this only
    adds the few pacer-laps-backed reads CentralView's panels touch that a bare Session lacks:
      * a consistent FULL-trace tx/ty/tt/tv (so the map marker index_at_time -> tx[i] is in bounds);
      * a real LapRenderCache (MapView's best-overlay draw segments);
      * lap_window / lap_at_time / lap_time (the global-clock windows the tick + scrub resolve);
      * lap_rows / dropout / sector splits / consistency stubs (the LapTable + the Stats page);
      * a tiny pacer Sectors (real start_line Segment) so the Session.start_line property resolves;
      * the raw point stream (point_count / get_point) the window's library entry reads its date from.
    Returns (central_view, session, t0, t1) where t0/t1 are the two laps' media-clock time arrays.
    """
    _jail_every_live_app_support_seam()   # BEFORE any widget reads prefs or any store is written
    s = _synthetic_session()
    t0, x0, y0, _sp0, _c0 = s._cols_cache[0]
    t1, x1, y1, _sp1, _c1 = s._cols_cache[1]

    # One consistent full trace (parallel tx/ty/tt/tv), sorted by time, so index_at_time(t) indexes
    # the same arrays the map marker reads.
    ft = np.concatenate([t0, t1])
    order = np.argsort(ft)
    s.tt = ft[order]
    s.tx = np.concatenate([x0, x1])[order]
    s.ty = np.concatenate([y0, y1])[order]
    s.tv = np.full(len(s.tt), 50.0)

    s._render_cache = render_cache.LapRenderCache(
        lap_xyt=s._lap_trace_xyt, valid_lap_ids=s.valid_lap_ids,
        lap_out_of_bests=s.lap_out_of_bests, lap_time=s.lap_time, trace_times=s.tt)

    windows = {0: (float(t0[0]), float(t0[-1])), 1: (float(t1[0]), float(t1[-1]))}
    s.lap_window = lambda lid: windows.get(lid)

    def _lap_at_time(t):
        for lid, (w0, w1) in windows.items():
            if w0 <= t <= w1:
                return lid
        return None
    s.lap_at_time = _lap_at_time

    def _lap_time(lid):
        return float(s._dist_cache[lid][0][-1] - s._dist_cache[lid][0][0])
    s.lap_time = _lap_time

    def _total_dist(lid):
        return float(s._dist_cache[lid][1][-1])
    s.lap_rows = lambda: [{"idx": i, "time": _lap_time(i), "dist": _total_dist(i), "entry": 50.0}
                          for i in s.valid_lap_ids()]
    s.dropout_lap_ids = lambda: set()
    s.sector_count = lambda: 1
    s.lap_sector_splits = lambda lid: [_lap_time(lid)]
    s.session_best_splits = lambda: [min(_lap_time(i) for i in s.valid_lap_ids())]
    s.lap_time_trend = lambda: [(i, _lap_time(i)) for i in s.valid_lap_ids()]
    s.sector_sigmas = lambda: [None]

    # A tiny real pacer Sectors so the Session.start_line / sector_lines properties resolve through
    # self.laps.sectors (MapView's timing-line build reads them). Single start line, no sectors.
    start_seg = tracks.make_segment(float(x0[0]), float(y0[0]) - 5.0,
                                    float(x0[0]), float(y0[0]) + 5.0)

    # Per-lap point clouds (lat/lon carried as the x/y placeholders) so Session.lap_channels — now
    # reached on load because the map opens SPEED-coloured — resolves through self.laps.get_lap.
    lap_pts = {
        0: [SimpleNamespace(point=SimpleNamespace(lat=float(xx), lon=float(yy)))
            for xx, yy in zip(x0, y0, strict=False)],
        1: [SimpleNamespace(point=SimpleNamespace(lat=float(xx), lon=float(yy)))
            for xx, yy in zip(x1, y1, strict=False)],
    }

    class _Laps:
        def __init__(self):
            self.sectors = SimpleNamespace(start_line=start_seg, sector_lines=[])

        def laps_count(self):
            return 2

        def start_timestamp(self, lid):
            return windows[lid][0]

        def lap_time(self, lid):
            return windows[lid][1] - windows[lid][0]

        def get_lap(self, lid):
            return SimpleNamespace(points=lap_pts[lid])

        # The RAW point stream the real pacer.Laps serves. `Session.session_date` reads it inside
        # `library_entry`, which the record chip and the focus list build on every `_build_ui`.
        # Without it, EVERY window built on this fixture raised AttributeError inside
        # `LibraryController._current_library_entry`'s guard, which logged a traceback and returned
        # None: 142 swallowed tracebacks across ten test files, all passing (H6). The fixes are the
        # full trace above, stamped with a FIXED wall clock so the entry's date is deterministic.
        def point_count(self):
            return len(s.tt)

        def get_point(self, row):
            if not 0 <= row < len(s.tt):  # the binding's std::out_of_range, as Python sees it
                raise IndexError(row)
            return SimpleNamespace(time=float(s.tt[row]), point=SimpleNamespace(
                lat=float(s.tx[row]), lon=float(s.ty[row]),
                timestamp_ms=_WALL_CLOCK_MS + round((s.tt[row] - s.tt[0]) * 1000.0)))
    s.laps = _Laps()
    s.track_name = "StadiumLoop"
    # A single-chapter ChapterMap spanning the two laps so VideoView's slider/inert pane build.
    s.chapters = chapters.ChapterMap(["/tmp/stadium.MP4"], [float(t1[-1] - t0[0] + 5.0)])
    s.video_path = None

    view = CentralView(s, ["/tmp/stadium.MP4"], sidecar_path=None)
    return view, s, t0, t1


def _pump(deadline_s: float, until):
    """Pump the real event loop until `until()` is truthy or the deadline elapses (bounded, no raw
    sleep-only wait) — the test_video_view_compare pattern for letting real async/timer work settle."""
    end = time.time() + deadline_s
    while time.time() < end and not until():
        _APP.processEvents()
        time.sleep(0.005)
    return until()


def _studiowindow_with_view(*, build_menu: bool = False):
    """A REAL StudioWindow around the synthetic 2-lap CentralView, built exactly as the QTimer test
    does (StudioWindow.__new__ + the real _build_ui, so the production tick timer + video-focus
    wiring are present) — optionally with the real _build_menu so the View ▸ Full Screen action
    exists. Returns (win, view). No real Session.load / media file (PACER_NO_MEDIA)."""
    from shiboken6 import Shiboken

    from studio.app import StudioWindow

    _view, s, _t0, _t1 = _real_central_view()
    # The builder's own view is not the window's (_build_ui builds that one below). Delete it: a
    # parentless widget whose children hold Python lambdas is never collected, so it stayed alive for
    # the rest of the process holding the session — a second CentralView per window this fixture
    # built, and a false "the reload kept the old Session" in tests/test_reload_memory.py.
    _view.dispose()
    Shiboken.delete(_view)
    win = StudioWindow.__new__(StudioWindow)
    # The export cluster lives on its own controller (§7.1) and the real __init__ builds it. This
    # fixture goes through __new__, so it seeds the controller like it seeds the rest of the state
    # __init__ would have installed — in ONE place, because every window-level suite uses it.
    from studio.app import STATUS_MS
    from studio.export_controller import ExportController
    QMainWindow.__init__(win)
    win.view = None
    win._tick_timer = None
    win._excluded_visible = True
    win._lap_panel_tab = 0
    win._grid_sizes = None
    win._speed_unit = "kmh"
    win._colorblind = False           # _build_menu reads this for the colour-blind toggle
    win.session = s
    win._paths = ["/tmp/stadium.MP4"]
    win._sidecar_path = None
    win._ref_chip = None
    win._sync_full_recording_action = lambda: None
    win._update_reference_status = lambda: None
    # BEFORE _build_menu, which wires the File ▸ Export actions straight to this controller's
    # methods — the same ordering the real __init__ documents (§7.1).
    win.exports = ExportController(win, STATUS_MS)
    # …and the library controller, for the same reason and one more: _build_ui itself wires the
    # record chip, the focus list and the post-drag library refresh straight to it.
    from studio.library_controller import LibraryController
    win.library_ctl = LibraryController(win, STATUS_MS)
    if build_menu:
        win._build_menu()             # the persistent menu bar (incl. View ▸ Enter Full Screen)
    win._build_ui()                   # fresh real CentralView + the production tick timer + wiring
    return win, win.view


# ============================================================ real QTimer + real tick
def test_real_qtimer_fires_view_tick_through_studiowindow():
    """The ~30 Hz tick is a REAL QTimer on StudioWindow (33 ms, started in _build_ui) delegating to
    the current view's tick(). The fake-view tests only ever CALLED tick() directly; this lets the
    genuine timer fire through the event loop and drive the real view.tick() — proving the timer is
    wired, runs at ~30 Hz, and never re-enters / crashes the fan-out. (StudioWindow.__new__ + a real
    _build_ui so we get the production QTimer wiring without a real Session.load.)"""
    view, s, _t0, _t1 = _real_central_view()
    from studio.app import StudioWindow

    win = StudioWindow.__new__(StudioWindow)
    QMainWindow.__init__(win)
    win.view = None
    win._tick_timer = None
    # Tabbed-panel PR: the window passes the excluded-strip choice + persisted tab/grid sizes
    # into each fresh view (persisted on the real window); seed the defaults here.
    win._excluded_visible = True
    win._lap_panel_tab = 0
    win._grid_sizes = None
    win._speed_unit = "kmh"  # speed display unit (km/h default); _build_ui passes it into the view
    win.session = s
    win._paths = ["/tmp/stadium.MP4"]
    win._sidecar_path = None
    win._ref_chip = None
    # Persistent-chrome hooks _build_ui calls (window-level, unrelated to the controller fan-out).
    win._sync_full_recording_action = lambda: None
    win._update_reference_status = lambda: None
    # _build_ui wires the record chip / focus list / library refresh to the library controller,
    # which the real __init__ builds and this __new__ fixture therefore has to (§7.1).
    from studio.app import STATUS_MS
    from studio.library_controller import LibraryController
    win.library_ctl = LibraryController(win, STATUS_MS)
    win._build_ui()  # builds a FRESH real CentralView + creates/starts the real ~30 Hz QTimer

    assert win._tick_timer is not None and win._tick_timer.isActive()
    assert win._tick_timer.interval() == 33, win._tick_timer.interval()

    # Let the REAL timer fire view.tick() several times (bounded, not a raw sleep).
    count = [0]
    real_tick = win.view.tick

    def counting_tick():
        count[0] += 1
        real_tick()  # the genuine fan-out (drain marker, apply position/compare) runs each time
    win.view.tick = counting_tick

    fired = _pump(2.0, lambda: count[0] >= 3)
    assert fired, f"the real QTimer never fired view.tick() (count={count[0]})"
    print(f"test_real_qtimer_fires_view_tick_through_studiowindow OK: real timer fired {count[0]} ticks")


def test_position_signal_then_real_tick_applies_once_and_is_stable():
    """The decode-path positionChanged signal must do almost nothing (just record latest_t); the tick
    applies the marker/cursor/readout off that path, exactly once, and only when the position actually
    advanced. Drive the REAL video.positionChanged signal, then the REAL tick(): the diff box / map
    marker update once, and a SECOND tick with no new position is a no-op (the latest_t != applied_t
    gate — no double-apply, no re-entrant churn)."""
    view, _s, t0, _t1 = _real_central_view()
    mid = float(t0[len(t0) // 2])
    view.video.positionChanged.emit(mid)   # real signal -> CentralView._on_position records latest_t
    assert view._playback.latest_t == mid

    view.tick()                            # latest_t != applied_t -> _apply_position/_apply_readout
    assert abs(view._playback.applied_t - mid) < 1e-9, "tick must apply the recorded position once"
    marker_pos = view.map.marker.pos()     # the map marker placed this tick (real TargetItem pos)
    diff_text = view.diff_box.text()
    assert diff_text, "the diff box must read a real moment after the tick"

    view.tick()                            # no new position -> early-out, no re-apply
    assert view.diff_box.text() == diff_text, "second tick must not re-apply (latest==applied gate)"
    assert view.map.marker.pos() == marker_pos, "marker must be stable across an idle tick"
    print("test_position_signal_then_real_tick_applies_once_and_is_stable OK")


def test_the_map_marker_moves_on_every_playback_tick():
    """HEALTH-2: while the video plays, the live map dot moves on EVERY tick, not at the GPS rate.

    It was placed on the NEAREST trace sample (`tx[index_at_time(t)]`), so on a 10 Hz trace it held
    still for three 30 Hz ticks and then jumped: on MK_18_09_26's best lap it moved 674 times in
    2,025 ticks, 1.5 m a step. It is now interpolated between the samples that bracket the playhead
    (`timeline.trace_point_at`, the export map's rule). Driven through the production path — the
    real positionChanged signal, then the real tick — at 30 Hz across lap 0 of the synthetic
    session, whose trace is sampled more coarsely than that; on a GPS sample the dot sits exactly
    on it."""
    view, s, t0, _t1 = _real_central_view()
    spacing = float(np.median(np.diff(t0)))
    assert spacing > 1.5 / 30.0, f"the fixture's trace must be coarser than the tick: {spacing}"
    ticks = np.arange(float(t0[0]) + 0.01, float(t0[-1]) - 0.01, 1.0 / 30.0)
    placed = []
    for t in ticks:
        view.video.positionChanged.emit(float(t))
        view.tick()
        p = view.map.marker.pos()
        placed.append((float(p.x()), float(p.y())))
    steps = np.hypot(*np.diff(np.array(placed), axis=0).T)
    held = int((steps < 1e-9).sum())
    assert held == 0, (
        f"the map marker held still on {held} of {len(steps)} playback ticks — it is stepping at "
        f"the trace's {1 / spacing:.0f} Hz instead of moving every tick")
    k = len(t0) // 2
    view.video.positionChanged.emit(float(t0[k]))
    view.tick()
    p = view.map.marker.pos()
    assert (float(p.x()), float(p.y())) == (float(s.tx[np.searchsorted(s.tt, t0[k])]),
                                            float(s.ty[np.searchsorted(s.tt, t0[k])])), (
        "on a sample time the marker must sit exactly on that sample")
    print(f"test_the_map_marker_moves_on_every_playback_tick OK: {len(steps)} ticks, "
          f"{len(steps)} moves, trace every {spacing * 1000:.0f} ms")


# ============================================================ compare toggle (C4 single source)
def test_compare_button_click_is_single_source_of_truth_no_reentrancy():
    """The PR#80 issue-1 bug class, at the REAL CentralView level: a user compare_btn.click() emits
    compareToggled once, which the controller turns into compare. The controller's set_compare flips
    the button checked to keep it visually in sync — and that programmatic sync must NOT re-emit
    compareToggled (the re-entrancy that clobbered pane B). So on_toggled fires EXACTLY ONCE per
    click, and the controller-owned state + the view layout move together (C4):
      * compare.active True, (lap_a, lap_b) pinned, a REAL secondary pane created, button checked;
      * a click to exit returns to active False, no secondary pane, button unchecked — once.
    The fake-view PR#80 tests passed through this bug; this real wiring would have caught it."""
    view, _s, _t0, _t1 = _real_central_view()
    assert view.video.compare_btn.isEnabled(), "two valid laps -> the compare toggle is enabled"
    assert view.compare.active is False and view.video.secondary is None

    # Spy the controller's on_toggled WITHOUT changing behaviour (wrap, still call the real slot).
    calls = []
    real_on_toggled = view.compare.on_toggled
    view.video.compareToggled.disconnect(view.compare.on_toggled)
    view.video.compareToggled.connect(lambda on: (calls.append(on), real_on_toggled(on)))

    view.video.compare_btn.click()         # the REAL user gesture: toggled -> compareToggled(True)
    _APP.processEvents()
    assert calls == [True], f"the button-sync must NOT re-enter the handler (got {calls})"
    # C4: controller state and view layout are consistent (single source of truth).
    assert view.compare.active is True
    assert view.compare.lap_a is not None and view.compare.lap_b is not None
    assert view.video.secondary is not None, "compare on -> a real second pane is mounted"
    assert view.video.compare_btn.isChecked() is True, "the button reflects the on state"
    assert view._comparing() is True, "the view derives 'comparing' from the controller, not a flag"

    calls.clear()
    view.video.compare_btn.click()         # exit compare
    _APP.processEvents()
    assert calls == [False], f"exit must fire exactly once, no re-entry (got {calls})"
    assert view.compare.active is False
    assert view.video.secondary is None, "compare off -> back to a single pane"
    assert view.video.compare_btn.isChecked() is False
    print("test_compare_button_click_is_single_source_of_truth_no_reentrancy OK")


def test_compare_scrub_fans_one_seek_to_each_real_pane_per_tick():
    """In compare mode a distance-locked plot scrub must drive BOTH real panes (primary via seek,
    secondary via seek_pane(1, .)) at the same track position, coalesced to ONE seek each per tick —
    the cross-pane fan-out PR#80/#81 were about, now driven through the REAL plots scrub SIGNALS onto
    the REAL secondary PlayerPane (not a fake recorder). Enter compare via the button, park the
    playhead in lap A, then scrubStarted/scrubMoved(distance)/tick and assert exactly one seek landed
    on each real pane."""
    view, s, t0, _t1 = _real_central_view()
    view.video.compare_btn.click()         # real compareToggled(True) -> CompareController.enter()
    _APP.processEvents()
    assert view.compare.active and view.video.secondary is not None
    sec = view.video.secondary

    # Park the playhead inside lap A so the scrub scopes to the pinned primary lap.
    view._playback.applied_t = float(t0[len(t0) // 2])

    # Record seeks on the REAL panes without altering them (wrap the real methods).
    prim_seeks, sec_seeks = [], []
    real_prim_seek, real_sec_seek = view.video.pane.seek, sec.seek
    view.video.pane.seek = lambda t: (prim_seeks.append(t), real_prim_seek(t))[1]
    sec.seek = lambda t: (sec_seeks.append(t), real_sec_seek(t))[1]

    view.plots.scrubStarted.emit()         # real grab signal -> ScrubController.on_started
    best_d = s.best_lap_total_distance()
    view.plots.scrubMoved.emit(0.5 * (best_d or 0.0), "distance")  # halfway down the shared axis
    assert view.scrub.is_active, "the scrub signals must make the controller active"

    view.tick()                            # coalesced apply: one primary seek + one secondary seek
    assert len(prim_seeks) == 1, f"exactly one coalesced primary seek/tick, got {prim_seeks}"
    assert len(sec_seeks) == 1, f"the distance-lock must fan ONE seek to the real pane B, got {sec_seeks}"
    # The two laps differ in length, so the distance-locked targets are NOT identical times.
    assert prim_seeks[0] != sec_seeks[0], "distance-lock must remap pane B, not copy pane A's time"

    # A second tick with no new move does nothing (coalescing cleared the dirty flags).
    view.tick()
    assert len(prim_seeks) == 1 and len(sec_seeks) == 1, "idle tick must not re-seek either pane"

    view.plots.scrubEnded.emit()           # real release signal -> ScrubController.on_ended
    assert view.scrub.is_active is False, "release clears the scrub state"
    print("test_compare_scrub_fans_one_seek_to_each_real_pane_per_tick OK")


def test_compare_tick_keeps_panes_consistent_no_reentry():
    """While comparing, the per-tick fan-out feeds both Δ badges + the secondary g + the map ghost
    off the live pane times. Drive several REAL ticks across moving pane times and assert it stays
    crash-free, the controller stays the single source of truth (active + pinned pair unchanged), and
    the compare button never silently drifts out of sync (the stale-cross-widget-state class). The
    badges are recomputed when a pane moves and early-out when neither does."""
    view, _s, t0, t1 = _real_central_view()
    view.video.compare_btn.click()
    _APP.processEvents()
    a, b = view.compare.lap_a, view.compare.lap_b
    sec = view.video.secondary
    assert a is not None and b is not None and sec is not None

    # Move both panes mid-lap (the inert panes serve current_pane_time from their set position), then
    # tick: the badges recompute for the new pair position without re-entering the toggle.
    view.video.pane.seek(float(t0[len(t0) // 2]))
    sec.seek(float(t1[len(t1) // 2]))
    view.tick()
    assert view.compare.active and (view.compare.lap_a, view.compare.lap_b) == (a, b)
    assert view.video.compare_btn.isChecked() is True, "the button stays in sync across ticks"

    # Several more ticks (idle + a move) must not crash, not flip the flag, not unmount the pane.
    for _ in range(5):
        view.tick()
    view.video.pane.seek(float(t0[len(t0) // 3]))
    view.tick()
    assert view.compare.active and view.video.secondary is sec, "the pane must not churn across ticks"
    assert view._comparing() is True
    print("test_compare_tick_keeps_panes_consistent_no_reentry OK")


# ============================================================ combined trust strip (de-clutter)
def test_provisional_banner_shows_and_clears_with_trust_state():
    """The ACTIONABLE tier of the ONE trust strip tracks Session.timing_verified end-to-end through
    the REAL CentralView:
      * a detected/verified track (the fixture's StadiumLoop) hides the strip;
      * flipping the session Provisional + rebuilding shows the actionable line (a prominent,
        persistent strip, NOT a status-bar line), and the lap table mutes its times with no bests;
      * a Verified flip + rebuild clears it again and restores the bests."""
    view, s, _t0, _t1 = _real_central_view()
    # Fixture is a known track → Verified → strip + banner hidden.
    assert s.timing_verified is True
    assert view.provisional_banner is not None
    assert not view._trust_strip.isVisibleTo(view), "verified track must hide the whole strip"
    assert not view.provisional_banner.isVisibleTo(view), "verified track must hide the banner"

    # Make it an unknown, unconfirmed track and rebuild the derived views (the load-time path).
    s.track_name = None
    s._timing_user_confirmed = False
    assert s.timing_verified is False
    view.rebuild_derived_views(reselect=True)
    assert view.provisional_banner.isVisibleTo(view), "provisional timing must show the banner"
    text = view.provisional_banner.text().lower()
    assert "unverified" in text and "start/finish" in text and "drag" in text, text
    # The lap table de-emphasizes the timing (no purple/green best authority while provisional).
    from studio import theme as _theme
    purple, green = _theme.C.best.upper(), _theme.C.ahead.upper()
    tbl = view.table.table
    painted = any(
        tbl.item(r, c) is not None
        and tbl.item(r, c).foreground().color().name().upper() in (purple, green)
        for r in range(tbl.rowCount()) for c in range(tbl.columnCount()))
    assert not painted, "provisional timing must paint no purple/green bests in the lap table"

    # Confirm the timing (the flag a start-line drag's set_timing_lines sets; this fixture's stub
    # laps cannot re-segment) and rebuild → Verified → strip clears.
    s._timing_user_confirmed = True
    view.rebuild_derived_views(reselect=True)
    assert s.timing_verified is True
    assert not view.provisional_banner.isVisibleTo(view), "confirming the timing must clear the banner"
    assert not view._trust_strip.isVisibleTo(view), "confirming the timing must hide the strip"
    print("test_provisional_banner_shows_and_clears_with_trust_state OK")


def test_trust_flip_without_a_rebuild_refreshes_the_table_and_the_map_cue():
    """QA W7-03: a trust flip that does NOT re-segment must still reach every surface that renders
    it. File ▸ Save as track… promotes the current lines into a named track — Provisional → Verified
    with no re-segmentation, so nothing rebuilds itself — and it only ever refreshed the trust strip.
    The strip cleared while the map canvas still painted "lap timing provisional" and the Laps table
    still rendered the lap in provisional italics with the ★ best mark withheld: three surfaces, two
    verdicts, one frame. refresh_timing_trust now drives the strip, the canvas cue and the table.

    Reproduces the gesture EXACTLY as app._save_as_track performs it (set track_name, then call
    refresh_timing_trust) — no rebuild_derived_views anywhere after the flip."""
    from studio import theme as _theme
    view, s, _t0, _t1 = _real_central_view()
    green, purple = _theme.C.ahead.upper(), _theme.C.best.upper()
    tbl = view.table.table

    def _italic_cells():
        return [(r, c) for r in range(tbl.rowCount()) for c in range(1, tbl.columnCount())
                if tbl.item(r, c) is not None and tbl.item(r, c).font().italic()]

    def _best_painted():
        return any(tbl.item(r, c) is not None
                   and tbl.item(r, c).foreground().color().name().upper() in (green, purple)
                   for r in range(tbl.rowCount()) for c in range(tbl.columnCount()))

    def _starred():
        return [tbl.item(r, 0).text() for r in range(tbl.rowCount())
                if tbl.item(r, 0) is not None and "★" in tbl.item(r, 0).text()]

    # Provisional, reached the load-time way (a rebuild) so all three surfaces genuinely agree.
    s.track_name = None
    s._timing_user_confirmed = False
    view.rebuild_derived_views(reselect=True)
    assert s.timing_verified is False
    assert view.provisional_banner.isVisibleTo(view)
    assert view.map._provisional_label is not None, "provisional timing must paint the canvas cue"
    assert _italic_cells(), "provisional timing must mute the start-line-derived cells"
    assert not _starred(), "no ★ may claim a best against an arbitrary start line"
    assert not _best_painted(), "no green/purple best while provisional"

    # THE GESTURE — exactly what _save_as_track does, and nothing else.
    s.track_name = "Sandown Park"
    assert s.timing_verified is True, "a named track IS a trusted start line"
    view.refresh_timing_trust()

    # One verdict per frame: the strip, the canvas caption and the table all say Verified. Collected
    # rather than asserted one at a time, so a regression names the WHOLE self-contradiction.
    assert not view._trust_strip.isVisibleTo(view), "the strip must clear"
    stale = []
    if view.map._provisional_label is not None or view.map._provisional_line is not None:
        stale.append("the map canvas still paints the 'lap timing provisional' cue")
    if _italic_cells():
        stale.append(f"the Laps table still mutes {len(_italic_cells())} cells in provisional "
                     "italics")
    if not _starred():
        stale.append("the ★ best-lap mark is still withheld")
    if not _best_painted():
        stale.append("the best-lap colour is still suppressed")
    assert not stale, ("the trust strip has cleared, but in the SAME frame: " + "; ".join(stale))
    print("test_trust_flip_without_a_rebuild_refreshes_the_table_and_the_map_cue OK")


def _brake_glyph_brushes(view):
    """Every brake-point glyph brush currently DRAWN on the speed chart, upper-cased hex, read off
    the live ScatterPlotItems (not inferred). The MAP's glyphs are not lap-coloured since
    cool/map-hero (theme.MAP_BRAKE_COLOR, palette-independent): `_map_brake_glyph_brushes`."""
    return [it.opts["brush"].color().name().upper() for it in view.plots._brake_items]


def _map_brake_glyph_brushes(view):
    return [it.opts["brush"].color().name().upper() for it in view.map._brake_markers._items]


def test_palette_flip_without_a_selection_recolours_the_brake_glyphs():
    """QA W4-03: refresh_palette must be a COMPLETE seam, the way refresh_timing_trust is.

    The brake-point glyphs on the speed chart and on the map are drawn from a CACHED
    (positions, colour, lap_id) tuple that plots_view/map_view were last PUSHED, so — unlike the
    curves they sit on — they cannot follow a palette flip on their own. refresh_palette re-penned
    the curves, the tables, the map ramp, stats and the hero box but never re-pushed the channels,
    so after View ▸ Colour-blind-safe cues the best lap's curve turned colour-blind blue while its
    own markers stayed the standard palette's green — on both surfaces, in one frame.

    Driven the way _on_colorblind_toggled does it (theme.set_palette, then view.refresh_palette())
    with NO lap-table selection in between: a selection re-pushes the channels and repairs the
    colour by accident, which is precisely why every test that drives the table missed this."""
    from PySide6.QtGui import QColor

    from studio import theme as _theme
    view, s, _t0, _t1 = _real_central_view()
    # The stadium synthetic brakes nowhere the detector calls a brake point, so give the channel ONE
    # onset per lap. Only the glyph's EXISTENCE is stubbed — its colour still comes from the app's
    # own _driving_lap_colour, which is the thing under test.
    s.driving.lap_brake_map_markers = lambda lid: [(0.0, 0.0, 0.8)]
    # …and on the speed chart, which since cool/map-hero is the surface whose glyphs follow the
    # palette (the map's wear their own palette-independent hue — asserted below).
    s.driving.lap_brake_plot_positions = lambda lid, mode: [(10.0, 0.8)]
    try:
        _theme.set_palette(_theme.PALETTE_STANDARD)
        view.rebuild_derived_views(reselect=True)
        assert view._corner_lap == s.best_lap_id(), (
            "this test reads the BEST lap's glyph hue, so the primary lap must be the best lap")
        std_best = QColor(_theme.best_lap_colour()).name().upper()
        before = _brake_glyph_brushes(view)
        assert before, "the synthetic session must draw at least one brake glyph to measure"
        assert std_best in before, (
            f"the best lap's glyphs should carry the standard best-lap hue {std_best}: {before}")

        # THE GESTURE — the palette flip's whole fan-out, and nothing else.
        _theme.set_palette(_theme.PALETTE_COLORBLIND)
        view.refresh_palette()

        cb_best = QColor(_theme.best_lap_colour()).name().upper()
        assert cb_best != std_best, "the two palettes must disagree for this test to mean anything"
        after = _brake_glyph_brushes(view)
        assert std_best not in after, (
            f"the glyph layer is still painting the OLD palette's {std_best} after the flip "
            f"(theme.best_lap_colour() is now {cb_best}): {after}")
        assert cb_best in after, (
            f"the best lap's glyphs must carry the active palette's {cb_best}: {after}")
        # The map's glyphs wear their own hue, in either palette — and they were re-pushed (the
        # seam is complete) rather than left over from before the flip.
        brake = QColor(_theme.MAP_BRAKE_COLOR).name().upper()
        on_map = _map_brake_glyph_brushes(view)
        assert on_map and set(on_map) == {brake}, (
            f"the map's brake glyphs must be {brake} in the colour-blind palette too: {on_map}")
    finally:
        _theme.set_palette(_theme.PALETTE_STANDARD)
    print("test_palette_flip_without_a_selection_recolours_the_brake_glyphs OK")


def test_quality_banner_is_informational_and_independent():
    """The INFORMATIONAL tier — timing ACCURACY (Session.timing_quality) — tracks a degraded clock
    end-to-end AND wears the calmer (non-CTA) style, independent of the start-line trust:
      * a normal GPS9 fixture (default high quality) hides the data-quality line + the strip;
      * forcing a media-clock fallback + refreshing shows it as a compact single line naming the
        cause, using the informational #InfoBanner objectName (NOT the amber #ProvisionalBanner CTA);
      * restoring high quality clears it. Pins that the two tiers are independent (a degraded clock
        does NOT require the start line to be provisional)."""
    view, s, _t0, _t1 = _real_central_view()
    # Default fixture: GPS9 true clock (not degraded) AND verified track → strip + line hidden.
    assert not s.timing_quality.degraded
    assert view.quality_banner is not None
    assert not view._trust_strip.isVisibleTo(view), "high-quality verified timing hides the strip"
    assert not view.quality_banner.isVisibleTo(view), "high-quality timing must hide the FYI line"
    # FYI-only tier uses the informational (calmer) style, NOT the amber CTA #ProvisionalBanner.
    assert view.quality_banner.objectName() == "InfoBanner", view.quality_banner.objectName()
    assert view.provisional_banner.objectName() == "ProvisionalBanner"

    # Force a media-clock fallback (older GPS5 camera) and refresh the trust strip.
    s._timing_quality = data_quality.TimingQuality(clock=data_quality.MEDIA_CLOCK_FALLBACK)
    assert s.timing_quality.degraded and s.timing_quality.media_clock
    view.refresh_timing_trust()
    assert view.quality_banner.isVisibleTo(view), "degraded timing must show the FYI line"
    assert view._trust_strip.isVisibleTo(view), "a live concern shows the strip"
    assert "video clock" in view.quality_banner.text().lower(), view.quality_banner.text()
    # Its hover quotes the ONE media-clock bound the banner line does, and no longer claims the
    # clock "runs ~0.1% fast and compresses every lap" (measured: the rates agree to ~27 ppm).
    tip = view.quality_banner.toolTip()
    assert data_quality.MEDIA_CLOCK_LAP_ERROR in tip and data_quality.MEDIA_CLOCK_LAP_ERROR in \
        view.quality_banner.text(), (view.quality_banner.text(), tip)
    assert "0.1%" not in tip and "compresses" not in tip, tip
    # Compact: a single line, not the multi-line per-concern paragraph it used to stack.
    assert "\n" not in view.quality_banner.text(), "the FYI line must stay a single compact line"
    # Independent of the start-line trust: the fixture is still a verified track, so the amber
    # actionable line stays hidden (only the FYI line shows).
    assert s.timing_verified is True
    assert not view.provisional_banner.isVisibleTo(view), "verified timing hides the actionable line"

    # Restore high quality → strip clears.
    s._timing_quality = data_quality.TimingQuality()
    view.refresh_timing_trust()
    assert not view.quality_banner.isVisibleTo(view), "restoring quality must clear the FYI line"
    assert not view._trust_strip.isVisibleTo(view), "no concern hides the strip"
    print("test_quality_banner_is_informational_and_independent OK")


def test_provisional_and_degraded_share_one_trust_strip():
    """The de-clutter core: when BOTH concerns apply (unknown track + older GoPro — the common
    first-run case) they show in ONE strip, not two separate word-wrapped ProvisionalBanner widgets
    eating a third of the map. The actionable line leads (amber CTA), the FYI line follows (calmer
    info style), and both live under the single #TrustStrip container."""
    view, s, _t0, _t1 = _real_central_view()
    # Provisional start line AND a degraded (media-clock) recording at once.
    s.track_name = None
    s._timing_user_confirmed = False
    s._timing_quality = data_quality.TimingQuality(clock=data_quality.MEDIA_CLOCK_FALLBACK)
    assert s.timing_verified is False and s.timing_quality.degraded
    view.rebuild_derived_views(reselect=True)

    # BOTH lines are visible inside the ONE strip.
    assert view._trust_strip.isVisibleTo(view), "a live concern shows the strip"
    assert view.provisional_banner.isVisibleTo(view), "the actionable line shows"
    assert view.quality_banner.isVisibleTo(view), "the FYI line shows"
    # The actionable call-to-action is present (drag the start/finish line).
    ptext = view.provisional_banner.text().lower()
    assert "drag" in ptext and "start/finish" in ptext, ptext

    # They are the SAME single strip container, not two independent top-level banner widgets: both
    # are children of view._trust_strip.
    assert view.provisional_banner.parent() is view._trust_strip
    assert view.quality_banner.parent() is view._trust_strip
    # Only ONE #TrustStrip exists in the map panel's banner area (not two stacked banners).
    from PySide6.QtWidgets import QWidget as _QW
    strips = [w for w in view.findChildren(_QW) if w.objectName() == "TrustStrip"]
    assert len(strips) == 1, f"exactly one trust strip, found {len(strips)}"
    print("test_provisional_and_degraded_share_one_trust_strip OK")


# ============================================================ Δ-to-ideal hero readout
def test_hero_readout_leads_with_labelled_delta_to_ideal():
    """The hero #DiffBox leads with Δ-to-IDEAL by default (the moat number, clearly LABELLED so it
    can't be read as Δ-to-best), and the toggle flips it to Δ-to-best — with the other number always
    in the box's tooltip. Drive a real position into lap 0, tick, and inspect the rendered text."""
    view, _s, t0, _t1 = _real_central_view()
    assert view.ideal_readout_btn.isChecked(), "the readout defaults to leading with Δ-to-ideal"

    mid = float(t0[len(t0) // 2])
    view.video.positionChanged.emit(mid)
    view.tick()
    text = view.diff_box.text()
    assert text.startswith("Δideal"), f"the hero readout must LEAD with the labelled Δideal: {text!r}"
    assert "km/h" in text, "speed stays in the readout"
    # The other reference (Δ-to-best) is never lost — it lives in the tooltip.
    assert "best lap" in view.diff_box.toolTip().lower(), view.diff_box.toolTip()

    # Flip to Δ-to-best: the lead label changes (no longer Δideal) and the tooltip now carries ideal.
    view.ideal_readout_btn.setChecked(False)
    best_text = view.diff_box.text()
    assert not best_text.startswith("Δideal"), f"toggle off must lead with Δ-to-best: {best_text!r}"
    assert best_text.startswith("Δbest "), best_text   # NAMED, like Δideal (cool/chart-corners)
    assert "ideal" in view.diff_box.toolTip().lower(), view.diff_box.toolTip()
    print(f"test_hero_readout_leads_with_labelled_delta_to_ideal OK ({text!r} / {best_text!r})")


def test_hero_names_the_baseline_the_compare_chart_draws():
    """LOOK-13 (QA 2026-09-26). In video compare the charts header read "SPEED · Δ TO BEST" and
    its axis "Δ to best (s)" while the hero beside it still led with "Δideal +1.48 s" — two
    baselines side by side. While comparing, the hero leads with the baseline the chart draws."""
    from studio import plots_view
    view, _s, t0, _t1 = _real_central_view()
    assert view.ideal_readout_btn.isChecked()
    view.video.positionChanged.emit(float(t0[len(t0) // 2]))
    view.tick()
    assert view.diff_box.text().startswith("Δideal"), view.diff_box.text()
    view._comparing = lambda: True                    # compare on, the chart drawing Δ-to-best
    view._plots_baseline_kind = plots_view.DELTA_BASELINE_BEST
    view._update_diff_box(view._playback.applied_t, view._last_diff_speed, view._last_diff_lap)
    text = view.diff_box.text()
    assert text.startswith("Δbest ") and not text.startswith("Δideal"), (
        "the hero must name the chart's baseline while comparing", text)
    view._plots_baseline_kind = plots_view.DELTA_BASELINE_IDEAL
    view._update_diff_box(view._playback.applied_t, view._last_diff_speed, view._last_diff_lap)
    assert view.diff_box.text().startswith("Δideal"), view.diff_box.text()
    print(f"test_hero_names_the_baseline_the_compare_chart_draws OK ({text!r})")


def test_compare_title_and_hero_agree_in_every_state_without_a_tick():
    """PRE-1 (cooler-2026-11 QA). Entering compare on a TWO-lap selection keeps the lower chart on
    Δ-to-best, so no baseline change fired — and LOOK-13's hero re-render rode only that signal.
    A paused window therefore kept `Δideal +0.21 s` beside `SPEED · Δ TO BEST` until the playhead
    moved. Driven over vs-ideal on/off × compare on/off × a reference loaded, with NO tick after
    each change (a paused window gets none): the title always names the chart's own baseline, the
    hero always names one, and while comparing the two name the SAME one."""
    from studio import plots_view
    view, s, t0, t1 = _real_central_view()
    best = s.best_lap_id()
    other = next(i for i in s.valid_lap_ids() if i != best)
    tt = {0: t0, 1: t1}[other]
    titles = {plots_view.DELTA_BASELINE_BEST: "Δ TO BEST", plots_view.DELTA_BASELINE_IDEAL:
              "Δ TO IDEAL", plots_view.DELTA_BASELINE_REFERENCE: "Δ TO REF"}
    heroes = {"Δideal ": plots_view.DELTA_BASELINE_IDEAL, "Δbest ": plots_view.DELTA_BASELINE_BEST,
              "Δref ": plots_view.DELTA_BASELINE_REFERENCE}
    states = 0
    for reference in (False, True):
        s.has_reference = (lambda: True) if reference else (lambda: False)
        for chip in (True, False):
            view.plots.set_laps([other, best])
            view.ideal_readout_btn.setChecked(chip)
            view.video.positionChanged.emit(float(tt[len(tt) // 2]))
            view.tick()
            for comparing in (False, True):
                if comparing:
                    view.compare.on_toggled(True)
                    _APP.processEvents()
                    assert view.compare.active
                if reference:   # what the chart's own refresh emits with a reference loaded
                    view.plots.deltaBaselineChanged.emit(plots_view.DELTA_BASELINE_REFERENCE)
                kind = view._plots_baseline_kind
                title, hero = view._plots_label.text(), view.diff_box.text()
                assert titles[kind] in title, (reference, chip, comparing, kind, title)
                hero_kind = next((k for pre, k in heroes.items() if hero.startswith(pre)), None)
                assert hero_kind is not None, f"the hero must name its baseline: {hero!r}"
                if comparing:
                    assert hero_kind == kind, (
                        "while comparing, the hero and the title must name one baseline",
                        reference, chip, title, hero)
                else:   # the chip chooses the hero's reference outside compare (by design)
                    assert (hero_kind == plots_view.DELTA_BASELINE_IDEAL) == chip, (chip, hero)
                states += 1
            view.compare.on_toggled(False)
            _APP.processEvents()
    assert states == 8, states
    s.has_reference = lambda: False
    print("test_compare_title_and_hero_agree_in_every_state_without_a_tick OK (8 states)")


def _hero_at(view, s, d, lead):
    """The hero's (text, colour) with the session's Δ pinned at `d` in the `lead` reading."""
    s.delta_at_lap = lambda _lap, _t: d
    s.delta_to_ideal_at = lambda _lap, _t: d
    view.ideal_readout_btn.setChecked(lead == "ideal")
    view._update_diff_box(view._playback.applied_t, view._last_diff_speed, view._last_diff_lap)
    return view.diff_box.text(), view.diff_box.delta_colour()


def test_hero_readout_is_level_under_the_timing_step():
    """cool/delta-readout-level (discovery6 C-craft P2). The readout was red/green for any |Δ| over
    5 ms — "Δideal +0.05 s" in red on the demo's best lap — while the compare seam and every burned
    overlay draw the same quantity neutral under the timing step (0.07 s, on the PRINTED value).
    Printed 0.06 is level; 0.07 and 0.08 are coloured; both signs; the number and its sign still
    print, and a level Δbest drops its ▲/▼ as the seam does. The ideal lead is one-way (clamped at
    0), so only its positive side is a colour question."""
    from studio import theme as T
    view, s, t0, _t1 = _real_central_view()
    keep = (s.delta_at_lap, s.delta_to_ideal_at)
    try:
        view.video.positionChanged.emit(float(t0[len(t0) // 2]))
        view.tick()
        for d, want in ((0.06, None), (0.07, T.behind_colour()), (0.08, T.behind_colour()),
                        (0.064, None), (0.066, T.behind_colour()),     # prints +0.06 / +0.07
                        (-0.06, None), (-0.07, T.ahead_colour()), (-0.08, T.ahead_colour())):
            text, colour = _hero_at(view, s, d, "best")
            shown = T.format_delta_value(d)
            assert text.startswith(f"Δbest {shown} s"), (d, text)
            assert colour == want, (f"Δbest printed {shown}", colour, want)
            arrow = T.delta_arrow(d)
            assert (arrow in text) == (want is not None), ("the ▲/▼ follows the colour", d, text)
            if d > 0:
                text, colour = _hero_at(view, s, d, "ideal")
                assert text.startswith(f"Δideal {shown} s"), (d, text)
                assert colour == want, (f"Δideal printed {shown}", colour, want)
    finally:
        s.delta_at_lap, s.delta_to_ideal_at = keep
        view.dispose()
    print("test_hero_readout_is_level_under_the_timing_step OK")


def test_hero_readout_asks_the_overlays_level_rule():
    """The shared-rule guard: the readout decides "level" by `overlay.broadcast.is_level` — the
    rule the Classic strip, Broadcast and the compare pill call — not by a copy of it. Swapping
    that one function flips the readout; and the readout agrees with it (and with the compare
    seam's `within_timing_precision` on the printed value) over a sweep of Δs."""
    from studio import library
    from studio import theme as T
    from studio.overlay import broadcast
    view, s, t0, _t1 = _real_central_view()
    keep = (s.delta_at_lap, s.delta_to_ideal_at, broadcast.is_level)
    try:
        view.video.positionChanged.emit(float(t0[len(t0) // 2]))
        view.tick()
        for d in np.round(np.arange(-0.2, 0.2001, 0.0013), 4):
            _text, colour = _hero_at(view, s, float(d), "best")
            level = broadcast.is_level(float(d))
            assert level == library.within_timing_precision(broadcast.printed_delta(float(d)))
            assert (colour is None) == (level or T.delta_colour(float(d)) is None), (d, colour)
        broadcast.is_level = lambda _d: True
        assert _hero_at(view, s, 0.5, "best")[1] is None, "the readout re-typed the rule"
        broadcast.is_level = lambda _d: False
        assert _hero_at(view, s, 0.02, "best")[1] == T.behind_colour(), "the readout re-typed it"
    finally:
        s.delta_at_lap, s.delta_to_ideal_at, broadcast.is_level = keep
        view.dispose()
    print("test_hero_readout_asks_the_overlays_level_rule OK")


def test_hero_readout_tail_is_secondary():
    """The craft review's P3: "whole lap" (and the speed) printed at the Δ's hero size and weight,
    so it read as a value. The Δ run keeps the HERO face; the tail paints a step down ON THE Δ's
    BASELINE — the live speed at EMPHASIS in the text colour (a glance while scrubbing), "whole
    lap" at BODY, dim. Pinned on the PAINTED pixels, not only on the fonts the widget holds: the
    tail's ink is at most 3/4 of the head's height and its foot sits on the head's (both runs are
    digits and letters without descenders here)."""
    from studio import theme as T
    from studio.central_view import HERO_AT_REST_TAG
    view, _s, _t0, _t1 = _real_central_view()
    view.resize(1280, 400)
    view.show()
    box = view.diff_box
    prior = (_APP.styleSheet(), _APP.font(), _APP.palette())
    theme.apply_theme(_APP)           # the real look: themed, the text light on the dark box

    def ink(img, x0, x1, colour):
        """(top row, bottom row) of the pixels near `colour` in columns [x0, x1)."""
        want = QColor(colour)
        rows = [y for y in range(img.height()) for x in range(max(x0, 0), min(x1, img.width()))
                if sum(abs(a - b) for a, b in zip(img.pixelColor(x, y).getRgb()[:3],
                                                   want.getRgb()[:3], strict=True)) < 60]
        return (min(rows), max(rows)) if rows else None

    try:
        assert box.font().pixelSize() == T.HERO or box.font().pointSize() == T.HERO, box.font()
        # Digits against digits (no Δ, no "/", no ascender), so the two inks compare like for like.
        for tail, colour in (("95", T.C.text), (HERO_AT_REST_TAG, T.C.text_dim)):
            head = "0.37"
            box.setText(f"{head}     {tail}")
            box.set_delta_colour(None)
            _APP.processEvents()
            font, want = box.tail_style(tail)
            assert want == colour, (tail, want)
            img = box.grab().toImage()
            split = box.contentsRect().left() + QFontMetrics(box.font()).horizontalAdvance(
                head + "  ")
            h_ink = ink(img, 0, split, T.C.text)
            t_ink = ink(img, split + 2, img.width(), colour)
            assert h_ink and t_ink, (tail, h_ink, t_ink)
            h_h, t_h = h_ink[1] - h_ink[0] + 1, t_ink[1] - t_ink[0] + 1
            assert t_h <= 0.75 * h_h, ("the tail paints as large as the Δ", tail, h_ink, t_ink)
            if tail != HERO_AT_REST_TAG:     # "whole lap" has a descender (p): its foot is lower
                assert abs(t_ink[1] - h_ink[1]) <= 1, ("the tail is off the Δ's baseline", tail,
                                                       h_ink, t_ink)
        size = font.pixelSize() if font.pixelSize() > 0 else font.pointSize()
        assert size == T.BODY, size
    finally:
        _APP.setStyleSheet(prior[0])
        _APP.setFont(prior[1])
        _APP.setPalette(prior[2])
        view.hide()
        view.dispose()
    print("test_hero_readout_tail_is_secondary OK")


def test_the_speed_chart_labels_every_corner_inside_its_span_in_both_x_modes():
    """cool/chart-corners. The map labels C1…; the speed chart named no corner at all (its only
    grey bands are coast spans). Each corner's map label must now ride the speed chart, on ITS
    corner — between that corner's entry and exit on the chart's own axis — in distance AND time
    mode, and follow an x-mode flip rather than staying where the old axis put it."""
    view, s, _t0, _t1 = _real_central_view()
    found = s.corners.corner_list()
    assert found, "the stadium synthetic has real corners to label"
    labels = [c.label for c in found]
    for idx, mode in ((0, "distance"), (1, "time")):
        view.plots.x_mode_combo.setCurrentIndex(idx)
        items = getattr(view.plots, "_corner_items", [])
        got = [(ln.label.toPlainText(), float(ln.value())) for ln in items]
        assert [g[0] for g in got] == labels, (mode, got, labels)
        enters = s._best_odometer_plot_positions(labels, [c.enter for c in found], mode)
        exits = s._best_odometer_plot_positions(labels, [c.exit for c in found], mode)
        for (lb, x), (_a, x0), (_b, x1) in zip(got, enters, exits, strict=True):
            assert x0 <= x <= x1, f"{lb} at {x:.2f} is outside its corner [{x0:.2f}, {x1:.2f}] ({mode})"
    view.plots.x_mode_combo.setCurrentIndex(0)
    print(f"test_the_speed_chart_labels_every_corner_inside_its_span_in_both_x_modes OK ({labels})")


def test_corner_labels_sit_on_the_maps_apex_not_the_middle_of_the_corner():
    """cool/chart-corners review P2-2. "Inside its span" also passes a label parked at the corner's
    MIDDLE; the chart label has to sit on the SAME point the map labels (`corner_map_markers`: the
    corner's apex on the best lap's odometer), in both x modes. The stadium's corners are
    symmetric, so their apex IS mid-span and could not tell the two apart: the apex is moved to a
    fifth of the way in, for the map and the chart alike (one corner basis feeds both)."""
    import dataclasses
    view, s, _t0, _t1 = _real_central_view()
    found, total = s.corners.basis()
    moved = [dataclasses.replace(c, apex=c.enter + 0.2 * (c.exit - c.enter)) for c in found]
    s.corners._basis_cache = (moved, total)
    best = s.best_lap_id()
    _t, bx, by, _v, cum = s._lap_columns(best)
    times, dists = s._lap_time_dist(best)
    markers = {m[0]: (m[1], m[2]) for m in s.corners.corner_map_markers()}
    for idx, mode in ((0, "distance"), (1, "time")):
        view.plots.x_mode_combo.setCurrentIndex(idx)   # a flip re-pushes the corner labels...
        view._refresh_sector_lines()                    # ...and the moved basis needs one here
        got = {ln.label.toPlainText(): float(ln.value()) for ln in view.plots._corner_items}
        assert set(got) == set(markers), (mode, got, markers)
        for c in moved:
            x = got[c.label]
            mid_d = 0.5 * (c.enter + c.exit)
            if mode == "distance":
                # The label's x, walked back onto the best lap's trace, IS the map marker.
                mx, my = float(np.interp(x, cum, bx)), float(np.interp(x, cum, by))
                assert abs(mx - markers[c.label][0]) < 1e-6 and abs(my - markers[c.label][1]) < 1e-6, (
                    c.label, (mx, my), markers[c.label])
                assert abs(x - mid_d) > 1.0, f"{c.label} sits mid-corner ({x:.2f}), not at the apex"
            else:
                want = float(np.interp(c.apex, dists, times)) - float(times[0])
                mid_t = float(np.interp(mid_d, dists, times)) - float(times[0])
                assert abs(x - want) < 1e-9, (c.label, x, want)
                assert abs(x - mid_t) > 0.05, f"{c.label} sits mid-corner ({x:.3f} s), not at the apex"
    view.plots.x_mode_combo.setCurrentIndex(0)
    print("test_corner_labels_sit_on_the_maps_apex_not_the_middle_of_the_corner OK")


def test_with_a_reference_loaded_the_hero_names_it_and_never_says_best_lap():
    """cool/chart-corners review P2-1/P2-2. With a cross-recording reference loaded `delta_at_lap` is
    measured against THAT lap, so the hero's chip-off run is `Δref`, its chip-on tooltip says "the
    reference lap", and on the session best it must not append "This IS your best lap … reads
    exactly zero for the whole lap" — against a reference the best lap's Δ moves like any other."""
    view, s, t0, t1 = _real_central_view()
    best = s.best_lap_id()
    tt = {0: t0, 1: t1}[best]
    view.video.positionChanged.emit(float(tt[len(tt) // 2]))
    view.tick()
    s.has_reference = lambda: True
    s.delta_at_lap = lambda _lid, _t: 0.42
    redraw = lambda: view._update_diff_box(view._playback.applied_t, 50.0, best)  # noqa: E731
    view.ideal_readout_btn.setChecked(True)
    redraw()
    tip = view.diff_box.toolTip()
    assert "the reference lap" in tip and "your best lap" not in tip, tip
    view.ideal_readout_btn.setChecked(False)
    redraw()
    text, tip = view.diff_box.text(), view.diff_box.toolTip()
    assert text.startswith("Δref "), text
    assert "IS your best lap" not in tip and "exactly" not in tip, tip
    s.has_reference = lambda: False   # ...and without one, the best-lap note is still there
    redraw()
    assert view.diff_box.text().startswith("Δbest "), view.diff_box.text()
    assert "IS your best lap" in view.diff_box.toolTip(), view.diff_box.toolTip()
    print("test_with_a_reference_loaded_the_hero_names_it_and_never_says_best_lap OK")


def _hero_ink(view):
    """Count the hero's inked pixels in the Δ run and in the speed run, by which colour they are
    nearest: (delta_run_hues, speed_run_hues) as {colour: n}. Split at the formatters' own join."""
    from PySide6.QtGui import QColor, QFontMetricsF
    box = view.diff_box
    img = box.grab().toImage()
    head, sep, _tail = box.text().partition("     ")
    split = box.contentsRect().left() + QFontMetricsF(box.font()).horizontalAdvance(head + sep) - 4
    cands = {"behind": theme.behind_colour(), "ahead": theme.ahead_colour(), "text": theme.C.text,
             "dim": theme.C.text_dim}   # "whole lap"'s tone since cool/delta-readout-level
    runs = ({}, {})
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.lightness() < 90:      # background / antialias fringe
                continue
            near = min(cands, key=lambda k: sum((a - b) ** 2 for a, b in zip(
                QColor(cands[k]).getRgb()[:3], c.getRgb()[:3], strict=True)))
            run = runs[0 if x < split else 1]
            run[near] = run.get(near, 0) + 1
    return runs


def test_the_hero_colours_only_its_delta_never_the_speed():
    """cool/chart-corners. The hero was ONE label in ONE colour, so a positive Δ painted the speed
    beside it red as well ("Δideal +0.09 s   45 km/h", both red) — as if the speed were a loss. The
    Δ run keeps its gain/loss hue for BOTH signs; the speed run stays the neutral text colour."""
    view, s, t0, _t1 = _real_central_view()
    view.resize(1440, 900)
    view.show()
    prior = (_APP.styleSheet(), _APP.font(), _APP.palette())
    theme.apply_theme(_APP)
    try:
        view.video.positionChanged.emit(float(t0[len(t0) // 2]))
        view.tick()
        view.ideal_readout_btn.setChecked(False)    # Δ-to-best: the run that has both signs
        for d, hue in ((0.37, "behind"), (-0.37, "ahead")):
            s.delta_at_lap = lambda _lid, _t, d=d: d
            view._update_diff_box(view._playback.applied_t, 45.0, view._last_diff_lap)
            from studio import motion
            motion.finish_all()     # a ±0.37 s flip rolls with PACER_MOTION=1: read its END
            _APP.processEvents()
            delta_run, speed_run = _hero_ink(view)
            assert max(delta_run, key=delta_run.get) == hue, (d, delta_run, view.diff_box.text())
            assert max(speed_run, key=speed_run.get) == "text", (
                f"the speed must stay neutral beside a Δ of {d:+}", speed_run, view.diff_box.text())
            assert speed_run.get(hue, 0) < 0.05 * sum(speed_run.values()), (d, speed_run)
    finally:
        _APP.setStyleSheet(prior[0])
        _APP.setFont(prior[1])
        _APP.setPalette(prior[2])
    print("test_the_hero_colours_only_its_delta_never_the_speed OK")


def test_the_three_delta_surfaces_name_their_baselines_and_agree_when_they_share_one():
    """cool/chart-corners. Three Δs sit in one panel: the hero (its baseline chosen by the `vs ideal`
    chip), the lower chart (titled in the header and on its axis) and the readout strip under it.
    They are different quantities by design — but with the chip OFF the hero printed a BARE
    `Δ 0.00 s` (it meant Δ to best) under a header reading "Δ TO IDEAL" beside a strip reading
    `Δ to ideal +0.085 s`: an unnamed Δ the eye gives the panel's baseline, which then disagrees.
    Every surface must NAME its baseline, the header and the strip must name the chart's, and two
    surfaces naming the same baseline must print the same number. Driven over both chip states,
    best-alone and a second lap."""
    from studio import plots_view
    view, s, t0, t1 = _real_central_view()
    best = s.best_lap_id()
    other = next(i for i in s.valid_lap_ids() if i != best)
    times = {0: t0, 1: t1}
    chart_words = {plots_view.DELTA_BASELINE_BEST: ("Δ TO BEST", "Δ to best"),
                   plots_view.DELTA_BASELINE_IDEAL: ("Δ TO IDEAL", "Δ to ideal")}
    checked = 0
    for sel, lap in (([best], best), ([other], other)):
        view.plots.set_laps(sel)
        for chip in (True, False, True):
            view.ideal_readout_btn.setChecked(chip)
            tt = times[lap]
            for frac in (0.3, 0.6, 0.9):
                view.video.positionChanged.emit(float(tt[int(frac * (len(tt) - 1))]))
                view.tick()
                from studio import motion
                motion.finish_all()   # a paused seek rolls with PACER_MOTION=1: compare its END
                kind = view.plots._delta_baseline_kind
                title_word, strip_word = chart_words[kind]
                hero, strip = view.diff_box.text(), view.plots.window_label.text()
                assert title_word in view._plots_label.text(), (kind, view._plots_label.text())
                assert strip_word in strip, (kind, strip)
                hero_base = ("ideal" if hero.startswith("Δideal ") else
                             "best" if hero.startswith("Δbest ") else None)
                assert hero_base is not None, f"the hero must name its baseline: {hero!r}"
                assert hero_base == ("ideal" if chip else "best"), (chip, hero)
                if hero_base == kind:
                    hv = float(hero.split()[1].replace("−", "-"))
                    sv = float(strip.split(strip_word)[1].split()[0].replace("−", "-"))
                    if not (hero_base == "ideal" and sv < 0):   # the hero floors Δideal at 0
                        assert abs(hv - sv) <= 0.011, (hero, strip)
                        checked += 1
    assert checked, "at least one same-baseline pair was compared"
    print(f"test_the_three_delta_surfaces_name_their_baselines_and_agree_when_they_share_one OK "
          f"({checked} same-baseline pairs)")


def test_hero_readout_keeps_every_character_at_every_column_width():
    """The hero #DiffBox is a QLabel — QLabels never elide, they HARD-CLIP — so the live number must
    survive intact at ANY charts-column width. Centred, an over-subscribed bar used to eat BOTH ends
    of it (".deal +0.00 s" / "74 km,"), which is why the box carries an explicit floor sized to the
    widest readout it can render and is LEFT-aligned so any residual squeeze costs the tail rather
    than the leading Δ.

    The three splitter positions below are pinned, and the third one (460 px) is the point: it is
    BELOW what the charts panel needs. It used to be reachable, and the header survived it by
    stripping its identity label and both control labels — the degradation ladder. There is no
    ladder now; the hero's own floor is part of the column's own minimum, so a drag to 460 px is
    REFUSED. The assertion is the same either way — the number is never clipped — but what makes it
    true is the layout declining the drag rather than the header dismantling itself.

    Measured against the PAINTED font — the label's font once the theme has polished it, which is
    what a QSS font rule would change — so the theme is applied for the duration and restored
    after."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFontMetrics

    view, _s, t0, _t1 = _real_central_view()
    assert view.diff_box.alignment() & Qt.AlignLeft, "a centred hero readout clips at BOTH ends"

    view.resize(1511, 940)
    view.show()
    prior = (_APP.styleSheet(), _APP.font(), _APP.palette())
    theme.apply_theme(_APP)
    try:
        view.video.positionChanged.emit(float(t0[len(t0) // 2]))
        view.tick()
        _APP.processEvents()
        box = view.diff_box
        fm = QFontMetrics(box.font())
        pad = 16  # the QSS's `#DiffBox { padding: 2px 8px }`
        floor = view._right_splitter.minimumSizeHint().width()
        # The user's own layout (a WIDE left column) first, then well past it.
        for right_w in (931, 700, 460):
            view._main_splitter.setSizes([1511 - right_w, right_w])
            for _ in range(4):
                _APP.processEvents()
            room = box.width() - pad
            assert room >= fm.horizontalAdvance(box.text()), (
                right_w, box.width(), box.text(), fm.horizontalAdvance(box.text()))
            assert view._plots_panel.width() >= floor, (
                f"asked for {right_w}px, got {view._plots_panel.width()} — below the {floor}px "
                "floor the charts header honestly needs")
        # …and no control paid for it. The two chart toggles used to be emptied to their icon at
        # exactly this layout; they now sit in the panel's own toolbar and keep their labels.
        assert view.plots.ideal_btn.text() == "Ideal lap", view.plots.ideal_btn.text()
        assert view.plots.brake_throttle_btn.text() == "Brake/Throttle"
        assert view.plots.ideal_btn.width() >= view.plots.ideal_btn.sizeHint().width()
    finally:
        _APP.setStyleSheet(prior[0])
        _APP.setFont(prior[1])
        _APP.setPalette(prior[2])
        view.hide()
    print("test_hero_readout_keeps_every_character_at_every_column_width OK")


def test_delta_to_ideal_tooltips_are_honest_not_best_sector():
    """Δ-to-ideal is a 400-point per-distance lower envelope — a synthetic curve no human drives in
    one pass — so its labels must describe it as a stitched-together theoretical ideal, NOT mis-sell
    it as the 'best of every clean sector'. Assert the hero readout-toggle tooltip and the ideal-lap
    plot toggle tooltip both read honestly (no 'best sector' claim, and the 'stitched'/'not a single
    lap' framing present)."""
    view, _s, _t0, _t1 = _real_central_view()
    readout_tip = view.ideal_readout_btn.toolTip().lower()
    assert "best sector" not in readout_tip and "best of every" not in readout_tip, readout_tip
    assert "stitched together" in readout_tip, readout_tip
    assert "not a single" in readout_tip, readout_tip

    plot_tip = view.plots.ideal_btn.toolTip().lower()
    assert "best sector" not in plot_tip and "best of every" not in plot_tip, plot_tip
    assert "stitched together" in plot_tip, plot_tip
    assert "not a single" in plot_tip, plot_tip
    print("test_delta_to_ideal_tooltips_are_honest_not_best_sector OK")


# ============================================================ labelled grip-map control
def test_grip_map_reachable_via_labelled_combo():
    """The map's rainbow channel is now a LABELLED dropdown (Off · Speed · Δ · Δ rate · Grip ·
    Pedal · Elevation) — every channel visible and one click, Grip no longer an undiscoverable
    blind-cycle step. Selecting the Grip entry sets the grip mode (the same render path the old
    cycle hit); the cycle API works. Both ESTIMATED channels say so in the real header (F6)."""
    view, _s, _t0, _t1 = _real_central_view()
    combo = view.map.rainbow_combo
    # Every channel is a labelled, visible entry (not hidden behind a cycle).
    modes = [combo.itemData(i) for i in range(combo.count())]
    assert modes == ["off", "speed", "delta", "delta_rate", "grip", "brake_throttle",
                     "elevation"], modes
    grip_idx = modes.index("grip")
    assert "grip" in combo.itemText(grip_idx).lower(), combo.itemText(grip_idx)
    for est in ("grip", "brake_throttle"):
        assert combo.itemText(modes.index(est)).endswith(theme.ESTIMATED_MARK), (
            f"the {est} entry is an estimate and must say so: {combo.itemText(modes.index(est))!r}")

    # Selecting Grip drives the map to the grip channel in ONE click. (Clear the current lap first so
    # _apply_rainbow cleanly no-ops here — the grip channel needs a g signal this fixture doesn't
    # seed; we're pinning the control wiring / mode selection, not the render math, which
    # test_rainbow_map covers.)
    view.map._current_lap = None
    combo.setCurrentIndex(grip_idx)
    _APP.processEvents()
    assert view.map._rainbow_mode == "grip", "the labelled Grip entry must select the grip channel"
    # And the legacy cycle path is preserved + keeps the combo in sync (the rainbow tests' driver).
    view.map._cycle_rainbow()  # grip -> pedal
    assert view.map._rainbow_mode == "brake_throttle"
    view.map._cycle_rainbow()  # pedal -> elevation
    assert view.map._rainbow_mode == "elevation"
    view.map._cycle_rainbow()  # elevation -> off (wraps)
    assert view.map._rainbow_mode == "off"
    assert combo.currentData() == "off", "the cycle must keep the labelled combo in sync"
    print("test_grip_map_reachable_via_labelled_combo OK")


# ============================================================ the Coaching tab page
def test_opportunities_panel_is_the_coaching_tab_page():
    """Coaching is a FULL page of the lap panel's tab stack (index 3) — no strip, no collapse,
    no height cap: selecting the tab shows the whole panel; on the 2-lap stadium synthetic
    (below coaching.MIN_LAPS) it shows the friendly need-more-laps state."""
    from studio.coaching_panel import OpportunitiesPanel

    view, _s, _t0, _t1 = _real_central_view()
    assert isinstance(view.opportunities, OpportunitiesPanel), "the page must be built into the view"
    assert view.table_stack.widget(3) is view.opportunities, "coaching is stack page 3"
    assert view.opportunities.body.maximumHeight() > 10_000, "the old 132px cap must be gone"
    view.select_lap_tab(3)
    assert view.table_stack.currentIndex() == 3
    assert view.opportunities.body.currentIndex() == 1, "too few clean laps -> the friendly state"
    assert view.opportunities.empty_state.text(), "the excluded state must carry a friendly message"
    print("test_opportunities_panel_is_the_coaching_tab_page OK")


def test_ia01_corners_and_coaching_tabs_declare_different_scopes():
    """IA-01: Corners is per-LAP (it renames itself "Corners · L6"), Coaching is the whole SESSION's
    median — but both tooltips used to promise the same "vs the best lap" with no scope word, so the
    two pages reading different numbers 130 px apart looked like a defect rather than two different
    questions. Each tooltip must now name its own scope, and Coaching must say it does not follow
    the selection."""
    view, _s, _t0, _t1 = _real_central_view()
    corners, coaching = view.tab_bar.tabToolTip(1), view.tab_bar.tabToolTip(3)
    assert "lap you select" in corners and "Follows your selection" in corners, corners
    assert "WHOLE session" in coaching, coaching
    assert "Does NOT follow your lap selection" in coaching, coaching
    assert "Corners tab is the per-lap view" in coaching, coaching
    print("test_ia01_corners_and_coaching_tabs_declare_different_scopes OK")


# ============================================================ full screen (window + video focus)
def test_window_fullscreen_toggle_and_menu_text_and_esc():
    """View ▸ Enter/Exit Full Screen (⌘⌃F): the menu action flips the window's real fullscreen state,
    its TEXT toggles Enter↔Exit (kept in sync via changeEvent), and Esc exits fullscreen. Built with
    the real menu so the _fullscreen_action exists (⌘⌃F = QKeySequence.FullScreen)."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent, QKeySequence

    win, _view = _studiowindow_with_view(build_menu=True)
    act = win._fullscreen_action
    assert act.text() == "Enter Full Screen", act.text()
    # The native fullscreen shortcut is bound (⌘⌃F on macOS).
    assert act.shortcut() == QKeySequence(QKeySequence.FullScreen), act.shortcut().toString()
    assert not win.isFullScreen()

    # Trigger the menu action -> window goes fullscreen, text flips to Exit (changeEvent synced it).
    act.trigger()
    _APP.processEvents()
    assert win.isFullScreen(), "the menu action must put the window into full screen"
    assert act.text() == "Exit Full Screen", act.text()

    # Esc exits fullscreen (keyPressEvent), text flips back to Enter.
    esc = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    win.keyPressEvent(esc)
    _APP.processEvents()
    assert not win.isFullScreen(), "Esc must exit full screen"
    assert act.text() == "Enter Full Screen", act.text()

    # Triggering again re-enters, and a second trigger exits (idempotent toggle).
    act.trigger()
    _APP.processEvents()
    assert win.isFullScreen()
    act.trigger()
    _APP.processEvents()
    assert not win.isFullScreen()
    print("test_window_fullscreen_toggle_and_menu_text_and_esc OK")


def test_video_focus_enters_and_restores_cleanly():
    """The "fullscreen video" gesture (the ⤢ button / a double-click on the video): entering
    MAXIMIZES the video panel into the grid + puts the window fullscreen; exiting RESTORES the video
    panel back into _left_splitter at index 0 at its original sizes, clears the maximize snapshot, and
    leaves the window normal — no crash, clean restore (mechanics only; the media frame is inert
    under PACER_NO_MEDIA)."""
    win, view = _studiowindow_with_view()
    win.resize(1400, 900)
    win.show()
    _APP.processEvents()
    vp = view._video_panel
    # BEFORE: the video panel is index 0 of the left splitter, nothing maximized, focus off.
    assert view._left_splitter.indexOf(vp) == 0
    assert view._maximized_panel is None
    assert view.is_video_focused() is False
    assert view.video.fullscreen_btn.isChecked() is False
    left_before = view._left_splitter.sizes()
    main_before = view._main_splitter.sizes()

    # ENTER video focus (the toggle the ⤢ button + double-click both call).
    view.toggle_video_focus()
    _APP.processEvents()
    assert view.is_video_focused() is True
    assert win.isFullScreen() is True, "entering video focus must put the window full screen"
    assert view._maximized_panel is vp, "the video panel must be maximized in the grid"
    assert view.video.fullscreen_btn.isChecked() is True, "the ⤢ button reflects the on state"
    # The video quadrant now fills its column + row (the sibling sections collapsed to 0).
    assert view._left_splitter.sizes()[1] == 0, view._left_splitter.sizes()
    assert view._main_splitter.sizes()[1] == 0, view._main_splitter.sizes()

    # EXIT video focus -> clean restore of geometry + parenting + window state.
    view.toggle_video_focus()
    _APP.processEvents()
    assert view.is_video_focused() is False
    assert win.isFullScreen() is False, "exiting video focus must return the window to normal"
    assert view._left_splitter.indexOf(vp) == 0, "the video panel is back in its splitter slot"
    assert vp.parent() is view._left_splitter, "the video panel is re-homed in the left splitter"
    assert view._maximized_panel is None, "the maximize snapshot is cleared on exit"
    assert view._saved_splitter_sizes is None
    # The grid is restored (not the collapsed [x, 0]) — both sections carry height/width again. The
    # load-bearing invariant is that the sibling is UN-COLLAPSED and the grid ratio matches the
    # pre-focus grid; absolute pixels aren't asserted because showFullScreen/showNormal rescales the
    # window, so Qt proportionally re-flows the splitters on the way back.
    left_after = view._left_splitter.sizes()
    main_after = view._main_splitter.sizes()
    assert left_after[1] > 0 and main_after[1] > 0, "the collapsed siblings must be restored"

    def _ratio(sizes):
        total = sum(sizes) or 1
        return sizes[0] / total
    assert abs(_ratio(left_after) - _ratio(left_before)) < 0.1, (left_after, left_before)
    assert abs(_ratio(main_after) - _ratio(main_before)) < 0.1, (main_after, main_before)
    assert view.video.fullscreen_btn.isChecked() is False
    print("test_video_focus_enters_and_restores_cleanly OK")


def test_transport_fullscreen_button_and_video_dblclick_trigger_focus():
    """The ⤢ transport button AND a double-click on the video CONTENT both toggle video focus (the
    two affordances the user reaches for). Each enters + exits, restoring cleanly, with the button
    state reflecting the mode."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    win, view = _studiowindow_with_view()
    btn = view.video.fullscreen_btn
    vp = view._video_panel

    # The transport ⤢ button: a click enters, a second click exits.
    btn.click()
    _APP.processEvents()
    assert view.is_video_focused() is True and win.isFullScreen() is True
    assert btn.isChecked() is True
    btn.click()
    _APP.processEvents()
    assert view.is_video_focused() is False and win.isFullScreen() is False
    assert view._left_splitter.indexOf(vp) == 0 and view._maximized_panel is None

    # A double-click on the video content (routed through the pane's event filter -> videoDoubleClicked
    # -> VideoView.videoFocusRequested -> CentralView.toggle_video_focus) enters focus.
    dbl = QMouseEvent(QEvent.MouseButtonDblClick, QPointF(5, 5),
                      Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    _APP.sendEvent(view.video.pane.video, dbl)
    _APP.processEvents()
    assert view.is_video_focused() is True, "double-clicking the video content must enter focus"
    assert win.isFullScreen() is True and btn.isChecked() is True
    # Another double-click exits + restores.
    _APP.sendEvent(view.video.pane.video, QMouseEvent(
        QEvent.MouseButtonDblClick, QPointF(5, 5), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    _APP.processEvents()
    assert view.is_video_focused() is False and win.isFullScreen() is False
    assert view._left_splitter.indexOf(vp) == 0 and view._maximized_panel is None
    print("test_transport_fullscreen_button_and_video_dblclick_trigger_focus OK")


def test_video_focus_disabled_while_comparing():
    """Video focus is single-video only: while comparing (the two-pane stage), the ⤢ gesture is a
    no-op, and turning compare ON while focused exits focus first (the maximize can't frame the
    compare stage)."""
    win, view = _studiowindow_with_view()
    # Enter compare, then try to focus -> no-op (stays a two-pane stage, not fullscreen).
    view.video.compare_btn.click()
    _APP.processEvents()
    assert view.compare.active is True
    view.toggle_video_focus()
    assert view.is_video_focused() is False, "video focus must be a no-op while comparing"
    assert win.isFullScreen() is False

    # Exit compare, focus, then entering compare again must exit focus + restore the window.
    view.video.compare_btn.click()
    _APP.processEvents()
    assert view.compare.active is False
    view.toggle_video_focus()
    _APP.processEvents()
    assert view.is_video_focused() is True and win.isFullScreen() is True
    view.video.compare_btn.click()  # compare ON -> must drop focus
    _APP.processEvents()
    assert view.compare.active is True
    assert view.is_video_focused() is False, "entering compare must exit video focus"
    assert win.isFullScreen() is False, "exiting focus for compare returns the window to normal"
    assert view._maximized_panel is None, "the video maximize is restored when focus drops"
    view.video.compare_btn.click()  # tidy: leave compare
    _APP.processEvents()
    print("test_video_focus_disabled_while_comparing OK")


def test_every_panel_header_has_a_maximize_button_that_toggles_and_reflects_state():
    """Discoverable panel-maximize (the visible affordance for the dblclick-header gesture): each of
    the four panel headers (video / table / map / plots) carries a maximize button wired to
    _toggle_panel_maximized. Clicking it maximizes that panel (_maximized_panel becomes it) and the
    button flips to the RESTORE glyph + tooltip; clicking again restores the grid (_maximized_panel
    is None) and the button reverts to the MAXIMIZE glyph. The button's action stays DISTINCT from
    the video transport's fullscreen ⤢ button (different glyph)."""
    from studio.central_view import (
        _MAXIMIZE_GLYPH,
        _RESTORE_GLYPH,
    )

    view, _s, _t0, _t1 = _real_central_view()

    # Every panel exposes a maximize button, registered against its own panel container.
    pairs = [
        (view._video_max_btn, view._video_panel),
        (view._table_max_btn, view._table_panel),
        (view._map_max_btn, view._map_panel),
        (view._plots_max_btn, view._plots_panel),
    ]
    for btn, panel in pairs:
        assert view._maximize_buttons.get(btn) is panel, "button must be registered to its panel"

    # The panel-maximize button must NOT be the same action as the video transport's fullscreen ⤢
    # button — a distinct glyph (fill the WINDOW quadrant vs fill the SCREEN).
    assert _MAXIMIZE_GLYPH != "ph.arrows-out" and _RESTORE_GLYPH != "ph.arrows-in"

    for btn, panel in pairs:
        assert view._maximized_panel is None
        max_tip = btn.toolTip()
        assert "Maximize" in max_tip, max_tip

        # Click -> this panel maximizes; the button reflects the restore state.
        btn.click()
        _APP.processEvents()
        assert view._maximized_panel is panel, "clicking the header button maximizes that panel"
        # Every OTHER button stays in the maximize state; only this one shows restore.
        assert btn.toolTip() != max_tip and "Restore" in btn.toolTip(), btn.toolTip()
        for other, _p in pairs:
            if other is not btn:
                assert "Maximize" in other.toolTip(), "siblings keep the maximize affordance"

        # Click again -> restore; the button reverts.
        btn.click()
        _APP.processEvents()
        assert view._maximized_panel is None, "clicking again restores the grid"
        assert btn.toolTip() == max_tip, "the button reverts to the maximize tooltip"

    # Double-clicking a header still maximizes the same panel (the pre-existing gesture the button
    # merely makes visible) and the button reflects it.
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    header = view._map_panel.layout().itemAt(0).widget()
    _APP.sendEvent(header, QMouseEvent(QEvent.MouseButtonDblClick, QPointF(2, 2),
                                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    _APP.processEvents()
    assert view._maximized_panel is view._map_panel, "dblclick-header still maximizes"
    assert "Restore" in view._map_max_btn.toolTip(), "the button tracks a dblclick maximize too"
    # tidy: restore
    view._map_max_btn.click()
    _APP.processEvents()
    assert view._maximized_panel is None
    print("test_every_panel_header_has_a_maximize_button_that_toggles_and_reflects_state OK")


def _run_all():
    test_hero_names_the_baseline_the_compare_chart_draws()
    test_compare_title_and_hero_agree_in_every_state_without_a_tick()
    test_hero_readout_is_level_under_the_timing_step()
    test_hero_readout_asks_the_overlays_level_rule()
    test_hero_readout_tail_is_secondary()
    test_real_qtimer_fires_view_tick_through_studiowindow()
    test_position_signal_then_real_tick_applies_once_and_is_stable()
    test_the_map_marker_moves_on_every_playback_tick()
    test_compare_button_click_is_single_source_of_truth_no_reentrancy()
    test_compare_scrub_fans_one_seek_to_each_real_pane_per_tick()
    test_compare_tick_keeps_panes_consistent_no_reentry()
    test_provisional_banner_shows_and_clears_with_trust_state()
    test_trust_flip_without_a_rebuild_refreshes_the_table_and_the_map_cue()
    test_palette_flip_without_a_selection_recolours_the_brake_glyphs()
    test_quality_banner_is_informational_and_independent()
    test_provisional_and_degraded_share_one_trust_strip()
    test_hero_readout_leads_with_labelled_delta_to_ideal()
    test_hero_readout_keeps_every_character_at_every_column_width()
    test_the_speed_chart_labels_every_corner_inside_its_span_in_both_x_modes()
    test_corner_labels_sit_on_the_maps_apex_not_the_middle_of_the_corner()
    test_with_a_reference_loaded_the_hero_names_it_and_never_says_best_lap()
    test_the_hero_colours_only_its_delta_never_the_speed()
    test_the_three_delta_surfaces_name_their_baselines_and_agree_when_they_share_one()
    test_delta_to_ideal_tooltips_are_honest_not_best_sector()
    test_grip_map_reachable_via_labelled_combo()
    test_opportunities_panel_is_the_coaching_tab_page()
    test_window_fullscreen_toggle_and_menu_text_and_esc()
    test_video_focus_enters_and_restores_cleanly()
    test_transport_fullscreen_button_and_video_dblclick_trigger_focus()
    test_video_focus_disabled_while_comparing()
    test_every_panel_header_has_a_maximize_button_that_toggles_and_reflects_state()
    test_ia01_corners_and_coaching_tabs_declare_different_scopes()
    test_tab_bar_switches_pages_and_names_the_corners_lap()
    test_the_chapter_is_named_once_in_the_panel()
    test_corner_row_click_rings_the_map()
    test_a_hovered_corner_lights_on_the_chart_and_the_map_and_clears_on_leave()
    test_leaving_the_map_canvas_onto_its_own_overlays_clears_the_hover()
    test_hovering_an_apex_dot_names_its_corner()
    test_a_rebuild_under_a_resting_pointer_lets_the_next_move_name_the_corner_again()
    test_a_coaching_jump_lights_the_chart_label_too()
    test_the_hovered_corner_travels_through_central_view_only()
    test_corner_hover_on_the_map_is_inert_during_a_lap_race()
    test_corner_hover_names_the_right_corner_in_time_mode()
    test_a_corner_whose_chart_label_was_dropped_still_lights_from_the_map()
    test_playback_ticks_do_no_corner_hover_work()
    test_the_charts_header_never_has_to_choose_between_identity_and_controls()
    test_show_stats_maximized_is_a_true_toggle()
    test_stats_corner_row_click_restores_grid_then_rings_map()
    test_every_grip_surface_says_it_compares_laps_not_corners()
    test_splitter_handles_stay_thin_under_the_theme()
    test_gmeter_overlay_stays_pinned_to_its_video_and_stands_down_with_it()
    test_hero8s_refused_imu_is_disclosed_on_the_real_view()
    test_hero8s_gps_derived_lateral_axis_is_named_on_the_real_stats_page()
    test_karmas_absent_g_meter_is_not_described_as_an_accelerometer_page()
    test_u2_lap_table_cap_notice_reaches_the_window_status_bar()
    test_the_fixture_window_writes_its_library_entry_into_a_jail_never_the_real_library()
    test_the_stats_page_handed_to_a_reader_is_never_stale()
    test_the_stats_page_pays_its_deferred_render_before_it_can_be_painted()
    test_a_rebuild_skips_the_stats_render_on_another_tab_and_does_it_on_the_stats_tab()
    test_motion_the_delta_rolls_on_a_seek_and_ends_exactly_on_the_direct_text()
    test_motion_a_baseline_or_lap_change_cuts_and_never_pairs_a_name_with_another_number()
    test_motion_playback_and_small_jumps_never_animate_and_idle_ticks_do_no_motion_work()
    test_motion_a_held_arrow_follows_directly_and_a_single_press_may_roll()
    test_motion_interruptions_end_the_roll_with_no_timer_left()
    test_motion_view_menu_reduce_motion_persists_wins_and_can_follow_the_system_again()
    print("ALL CENTRAL-VIEW REAL-QT TESTS PASSED")


def test_hero8s_refused_imu_is_disclosed_on_the_real_view():
    """hero8.mp4 is the one recording pacer can open whose accelerometer the axis gate REFUSES: its
    GRAV stream is all zeros, while both Max clips and all five D24 chapters measure ALIGNED at
    1.5-9.4 deg and the other bundled clips carry no GRAV to check at all. Loaded here through the
    REAL Session.load into a REAL CentralView, because both disclosures are WIRING rather than
    logic — the toggle's tooltip is set in `_construct_panels`, the card's row in StatsView — and a
    test that rebuilt either one would be testing its own copy.

    Before: the toggle said "No usable accelerometer, so both axes are derived from the GPS
    trajectory." and the DATA TRUST card said "g-meter: GPS lateral · GPS-derived longitudinal",
    unmarked. The refusal existed only on stdout."""
    from studio.session import Session
    path = _samples.require("hero8.mp4")
    s = Session.load([path])
    axis = s.gmeter_axis()
    assert axis is not None and not axis.ok and not axis.has_direction, axis
    assert s.has_gmeter and s.gmeter_source() == "gps", (s.has_gmeter, s.gmeter_source())
    view = CentralView(s, [path], sidecar_path=None)
    try:
        tip = view.video.gmeter_btn.toolTip()
        assert axis.refusal() in tip, tip
        assert "No usable accelerometer" not in tip, tip
        view.stats_view.refresh()
        row = next(r for r in view.stats_view.trust.card.rows() if r[0] == "g-meter")
        assert row[2] and axis.refusal() in row[1], row
    finally:
        view.deleteLater()
        _APP.processEvents()
    print("ok hero8's refused IMU is disclosed on the real toggle and the real DATA TRUST card")


def test_hero8s_gps_derived_lateral_axis_is_named_on_the_real_stats_page():
    """#283 taught the toggle and the DATA TRUST card that hero8's accelerometer was refused. FOUR
    texts on the Stats page went on calling the lateral axis an accelerometer reading anyway.

    Measured over the real `Session.load` on every bundled sample and both D24 recordings: seven of
    the ten samples run a GPS-derived lateral axis (six carry no GRAV/CORI at all; hero8's GRAV is
    all zeros and is REFUSED by `axis_check`), while both D24 recordings measure ALIGNED at 6.1 and
    5.9 deg and keep the accelerometer. tests/test_stats.py pins the composition on a stub; this
    pins the WIRING, on the real page, for the same reason the test above it exists — the tooltips
    are set in `StatsView._refresh_g_provenance`, and a test that rebuilt the copy would be testing
    its own string."""
    from studio import gmeter
    from studio.session import Session
    from studio.stats_panel import GG_TOOLTIP, LAP_TABLE_TOOLTIP, PEAK_LAT_TOOLTIP
    path = _samples.require("hero8.mp4")
    s = Session.load([path])
    assert s.has_gmeter and s.gmeter_source() == "gps", (s.has_gmeter, s.gmeter_source())
    refusal = s.gmeter_axis().refusal()
    view = CentralView(s, [path], sidecar_path=None)
    try:
        view.stats_view.refresh()
        stats = view.stats_view
        # Each of the three fixed texts moved off the accelerometer claim...
        assert stats.gg.toolTip() != GG_TOOLTIP, (
            "the friction circle still calls the lateral axis an accelerometer on a GPS-derived "
            "recording")
        assert stats.lap_table.toolTip() != LAP_TABLE_TOOLTIP, stats.lap_table.toolTip()
        assert stats.t_peak_lat.toolTip() != PEAK_LAT_TOOLTIP, stats.t_peak_lat.toolTip()
        assert "Lat g is the accelerometer" not in stats.lap_table.toolTip()
        assert "Lateral is the accelerometer" not in stats.gg.toolTip()
        assert "IMU lateral" not in stats.t_peak_lat.toolTip()
        # ...and each gives the SAME reason the toggle and the trust card give, never a second
        # wording for one refusal.
        for tip in (stats.gg.toolTip(), stats.lap_table.toolTip(), stats.t_peak_lat.toolTip()):
            assert refusal in tip, tip
        # ...and the two surfaces whose claim is a WINDOW rather than a sensor (S2). `long_g_gps`
        # — the series LONG_SMOOTH_S belongs to — is built only on the IMU path, so on hero8 the
        # peak-braking tile printed a number that never met the window it named, and DRIVING
        # contrasted its own bare derivative with "the 0.35 s one the peak-braking tile and the
        # friction circle above are drawn on". Neither is true here.
        window = f"{gmeter.LONG_SMOOTH_S:g} s"
        brake_tip = stats.t_peak_brake.toolTip()
        driving_tip = stats._driving_section.toolTip()
        assert "Peak SUSTAINED deceleration" not in brake_tip, brake_tip
        assert "the opposite choice from the" not in driving_tip, driving_tip
        for tip in (brake_tip, driving_tip):
            assert "absent" in tip and tip.count(window) == 1, tip
            assert refusal in tip, tip
    finally:
        view.deleteLater()
        _APP.processEvents()
    print("ok hero8's GPS-derived lateral axis is named on the real Stats page, in #283's words")


def test_karmas_absent_g_meter_is_not_described_as_an_accelerometer_page():
    """karma.mp4 is the one recording pacer can open that ends up with NO g-meter at all, and #288
    left its Stats page reading like an IMU one.

    `gps_lateral_clause` returns None here — correctly, this lateral g is not GPS-derived either —
    so every provenance text fell through to the accelerometer wording: the PER LAP grid said
    "Lat g is the accelerometer" and the peak-lateral tile said "IMU lateral", directly under the
    page's own note saying "no accelerometer in this recording". Three states, not two.

    Real load + real page, for the reason the two tests above it are: the tooltips are set in
    `StatsView._refresh_g_provenance`, and a test that rebuilt the copy would test its own string.
    """
    from studio import gmeter
    from studio.session import Session
    from studio.stats_panel import LAP_TABLE_TOOLTIP, NO_GMETER_CLAUSE, PEAK_LAT_TOOLTIP
    path = _samples.require("karma.mp4")
    s = Session.load([path])
    assert not s.has_gmeter, "karma.mp4 is the no-g-meter fixture; it grew a g-meter"
    view = CentralView(s, [path], sidecar_path=None)
    try:
        view.stats_view.refresh()
        stats = view.stats_view
        window = f"{gmeter.LONG_SMOOTH_S:g} s"
        lap_tip = stats.lap_table.toolTip()
        lat_tip = stats.t_peak_lat.toolTip()
        brake_tip = stats.t_peak_brake.toolTip()
        assert lap_tip != LAP_TABLE_TOOLTIP and lat_tip != PEAK_LAT_TOOLTIP, (lap_tip, lat_tip)
        assert "Lat g is the accelerometer" not in lap_tip, lap_tip
        assert "IMU lateral" not in lat_tip, lat_tip
        for tip in (lap_tip, lat_tip, brake_tip):
            assert window not in tip, tip
            assert NO_GMETER_CLAUSE in tip, tip
    finally:
        view.deleteLater()
        _APP.processEvents()
    print("ok karma's absent g-meter is explained in the page's own words on all three surfaces")


def test_tab_bar_switches_pages_and_names_the_corners_lap():
    """The lap panel's QTabBar is the ONE page switcher: tab index == stack index for all five
    pages, and the Corners tab text always names the lap its rows describe (1-BASED, the
    app-wide display rule — the old mode label leaked the 0-based id).

    FIVE since Marks joined them — and the tab bar and the stack are asserted to agree on the
    count, not just on a number typed here: the two are wired index-for-index, so a page added to
    one and not the other would otherwise show up as a blank panel rather than as a failure."""
    view, _s, _t0, _t1 = _real_central_view()
    assert view.tab_bar.count() == 5 == view.table_stack.count(), (
        view.tab_bar.count(), view.table_stack.count())
    assert [view.tab_bar.tabText(i) for i in (0, 2, 3, 4)] == [
        "Laps", "Stats", "Coaching", "Marks"]
    for idx in (1, 2, 3, 4, 0):
        view.select_lap_tab(idx)
        _APP.processEvents()
        assert view.table_stack.currentIndex() == idx, f"tab {idx} must show page {idx}"
    # The Corners tab names the primary lap, 1-based ("· L1" for lap id 0 / "· L2" for id 1).
    lap = view._corner_lap
    assert view.tab_bar.tabText(1) == (f"Corners · L{lap + 1}" if lap is not None else "Corners")
    # C3: the corner IDENTITY column keeps a readable floor (the old Stretch mode crushed it
    # to a 42px sliver at the default panel width).
    assert view.corner_table.table.columnWidth(0) >= 88
    # Out-of-range selects are ignored, never a blank page.
    view.select_lap_tab(9)
    assert view.table_stack.currentIndex() == 0
    # B1/B2: every tab must PAINT its whole name at a realistic panel width. Assert that on the
    # painted string, not on the tab geometry: the previous tabRect() >= text-advance check passed
    # (71 >= 56) while all four labels rendered as "La…" / "Corners ·…" / "St…" / "Coac…", because
    # the elision happens further in — QTabBar::initStyleOption applies the elide mode to the
    # style's TEXT sub-rect, which the QSS's own `padding: 6px 10px` has already been deducted
    # from a second time. opt.text is literally what the style draws.
    #
    # This runs under the REAL theme QSS (that padding IS the trigger — a stock unstyled QTabBar
    # never elides here, so an unstyled test could not see the bug), then restores the app's
    # default chrome so the later tests keep theirs.
    view.resize(576, 460)
    view.show()
    from PySide6.QtWidgets import QStyleOptionTab
    prior = (_APP.styleSheet(), _APP.font(), _APP.palette())
    theme.apply_theme(_APP)
    try:
        _APP.processEvents()
        bar = view.tab_bar
        assert bar.tabText(3) == "Coaching"
        for i in range(bar.count()):
            opt = QStyleOptionTab()
            bar.initStyleOption(opt, i)
            assert opt.text == bar.tabText(i), (i, opt.text, bar.tabText(i))
    finally:
        _APP.setStyleSheet(prior[0])
        _APP.setFont(prior[1])
        _APP.setPalette(prior[2])
    view.hide()
    print("test_tab_bar_switches_pages_and_names_the_corners_lap OK")


def test_the_chapter_is_named_once_in_the_panel():
    """P4, ONE SURFACE LATER. This began as "the banner and the transport timecode are visible at
    the same time, so they must speak ONE format" — a real fix (the transport had compressed it to
    "chapter 2/3" against the banner's "chapter 2 of 3"), and an admission that the same fact was
    printed twice in one panel, 300 px apart, in two bands.

    The banner is gone. It was a full-width strip above the video carrying the recording label the
    window TITLE already holds plus a chapter the timecode printed again; it is a `widgets.chip` in
    the panel's identity row now, in the `status` slot, beside the word it qualifies. So the
    assertion is no longer "the two agree" but "there is one of them" — which is the thing the
    phrasing fix was a proxy for, and which cannot drift.

    The formatter stays single-sourced (`chapters.format_chapter`), because the LAP panel and the
    export overlay still render it and a second spelling would be the original defect elsewhere."""
    view, s, _t0, _t1 = _real_central_view()
    dur = s.chapters.total_duration / 3.0
    # A real 3-chapter load: the session carries the map and so does the player pane (which is what
    # makes video.is_multi — the chip's gate — true).
    s.chapters = chapters.ChapterMap(["/tmp/a.MP4", "/tmp/b.MP4", "/tmp/c.MP4"], [dur] * 3)
    view.video.pane._chapters = s.chapters

    view._update_chapter_label(1)                       # 0-based index 1 == the 2nd chapter
    chip_text = view.chapter_label.text()
    transport = view._transport_readout(dur * 1.5)      # a moment inside the 2nd chapter

    assert chip_text == chapters.format_chapter(1, 3) == "chapter 2 of 3", chip_text
    assert "chapter" not in transport, (
        f"the timecode is printing the chapter a second time in the same panel: {transport!r}")
    assert "2/3" not in transport, transport
    assert transport == fmt_time(dur * 1.5), transport
    # ...and the chip is where a live fact about this panel belongs.
    assert view.chapter_label in view._video_header.status
    assert view.chapter_label.property("role") == "Chip", view.chapter_label.property("role")
    print("test_the_chapter_is_named_once_in_the_panel OK")


def test_the_charts_header_never_has_to_choose_between_identity_and_controls():
    """The charts header has no shortfall to spend any more, at any width the app can reach.

    THREE VERSIONS OF THIS TEST NOW EXIST, and the sequence is the argument for the header/toolbar
    split. It first pinned "the decorative label must yield when cramped" — which is how the L6-01
    regression shipped, because #125 turned that same label into the only surface naming the
    baseline the LOWER CHART draws and nothing moved it up the yield order. It was then inverted:
    the toggles give up their TEXT first, falling back to their icon. Both versions were pinning a
    YIELD ORDER, i.e. asserting which meaning the app throws away when its header runs out of room.

    There is no order to pin now. Identity + the live readout are in a PanelHeader, the controls are
    in a PanelToolbar under it, and neither can take width from the other — so this asserts the
    thing the earlier versions could not: at the reporter's cramped layout AND at a roomy one, EVERY
    item paints in full, and each of the two rows stands at its declared height regardless."""
    view, _s, _t0, _t1 = _real_central_view()
    view.resize(1511, 940)
    view.show()
    _APP.processEvents()

    for sizes in ([580, 931], [300, 1211]):        # the reporter's cramped layout, then a roomy one
        view._main_splitter.setSizes(sizes)
        for _ in range(4):
            _APP.processEvents()
        label = view._plots_label
        assert label.isVisibleTo(view), "the baseline naming must survive a cramped panel"
        assert "SPEED" in label.text() and "Δ" in label.text(), label.text()
        assert label.width() >= label.sizeHint().width(), (label.text(), label.width())
        for w in (view.ideal_readout_btn, view.plots.brake_throttle_btn, view.plots.ideal_btn,
                  view.plots.x_mode_combo):
            assert w.width() >= w.sizeHint().width(), (w.objectName(), w.width())
        assert view.plots.brake_throttle_btn.text() == "Brake/Throttle"
        assert view.plots.ideal_btn.text() == "Ideal lap"
        # Hovering still names the toggles — the icon-only tier is gone, the accessible name is not.
        for btn in (view.plots.brake_throttle_btn, view.plots.ideal_btn):
            assert btn.accessibleName() in btn.toolTip(), btn.toolTip()
        assert view._plots_header.height() == theme.PANEL_HDR_H, view._plots_header.height()
        assert view._plots_toolbar.height() == theme.TOOLBAR_H, view._plots_toolbar.height()
    view.hide()
    print("test_the_charts_header_never_has_to_choose_between_identity_and_controls OK")


def test_corner_row_click_rings_the_map():
    """B4: a Corners-tab row rings that corner's apex on the map — the same corner_clicked
    pathway the Stats CORNERS and Coaching rows use, so the three surfaces behave alike."""
    view, _s, _t0, _t1 = _real_central_view()
    view.select_lap_tab(1)
    _APP.processEvents()
    table = view.corner_table
    assert table.table.rowCount() > 0 and table._cids, "stadium fixture must project corners"
    seen = []
    table.corner_clicked.connect(seen.append)
    table.table.cellClicked.emit(0, 0)
    _APP.processEvents()
    assert seen == [table._cids[0]]
    assert view.map._corner_markers.highlighted == f"C{table._cids[0]}"
    print("test_corner_row_click_rings_the_map OK")


def _pointer_to(gv, scene_pt):
    """A REAL pointer move into a graphics view (see test_charts_panel._mouse_move): spaced past
    pyqtgraph's scene rate limit, which drops a move that follows the last too closely."""
    time.sleep(0.025)
    vp = gv.viewport()
    p = gv.mapFromScene(scene_pt)
    QApplication.sendEvent(vp, QMouseEvent(
        QEvent.Type.MouseMove, QPointF(p), QPointF(vp.mapToGlobal(p)), Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
    _APP.processEvents()


def _hover_rig():
    """A shown real CentralView and pointer helpers for its chart labels and map labels."""
    view, s = _shown_view_on_the_laps_tab()
    plots_gv = view.plots.p_speed.scene().views()[0]
    cm = view.map._corner_markers
    assert len(cm._texts) >= 2, "the stadium fixture must detect at least two corners"

    def chart(label):
        ln = next(ln for ln in view.plots._corner_items if ln.label.toPlainText() == label)
        assert ln.isVisible(), f"{label} is not painted on the chart"
        _pointer_to(plots_gv, ln.label.sceneBoundingRect().center())

    def chart_off():
        _pointer_to(plots_gv, view.plots.p_speed.getViewBox().sceneBoundingRect().center())

    def map_label(label):
        k = [m[0] for m in cm._markers].index(label)
        _pointer_to(view.map.widget, cm._texts[k].sceneBoundingRect().center())

    def map_off():
        _pointer_to(view.map.widget, view.map.plot.getViewBox().sceneBoundingRect().topLeft()
                    + QPointF(3, 3))
    return view, s, chart, chart_off, map_label, map_off


def test_a_hovered_corner_lights_on_the_chart_and_the_map_and_clears_on_leave():
    """cool/corner-hover-link: one corner, three surfaces. Hover C2's label on the speed chart →
    the map rings C2 and the chart emphasises C2; hover C1's label on the MAP → the chart
    emphasises C1 and the map rings it; leaving clears both. A Corners-row ring is followed by
    the chart too, a hover wins over it while it lasts, and leaving hands the ring back."""
    view, _s, chart, chart_off, map_label, map_off = _hover_rig()
    cm = view.map._corner_markers
    try:
        chart("C2")
        assert cm.highlighted == "C2", f"chart hover C2 rang {cm.highlighted} on the map"
        assert view.plots.corner_emphasis() == "C2"
        chart_off()
        assert cm.highlighted is None and view.plots.corner_emphasis() is None
        map_label("C1")
        assert view.plots.corner_emphasis() == "C1", view.plots.corner_emphasis()
        assert cm.highlighted == "C1"
        map_off()
        assert view.plots.corner_emphasis() is None and cm.highlighted is None
        # A row ring (Corners/Stats/Coaching) is the pinned ring: the chart follows it...
        view._on_stats_corner_clicked(2)
        assert cm.highlighted == "C2" and view.plots.corner_emphasis() == "C2"
        chart("C1")                       # ...a hover wins while it lasts...
        assert cm.highlighted == "C1" and view.plots.corner_emphasis() == "C1"
        chart_off()                       # ...and leaving hands both back to the pinned corner.
        assert cm.highlighted == "C2" and view.plots.corner_emphasis() == "C2"
        view.opportunities.corner_clicked.emit(None)
        assert cm.highlighted is None and view.plots.corner_emphasis() is None
    finally:
        view.hide()
        view.dispose()
    print("test_a_hovered_corner_lights_on_the_chart_and_the_map_and_clears_on_leave OK")


def test_leaving_the_map_canvas_onto_its_own_overlays_clears_the_hover():
    """The map key, the notice and the legend are children INSIDE MapView: a pointer moving from
    a corner label onto the key leaves the canvas without leaving the view, and Qt tells only the
    viewport. That Leave must clear the ring and the chart's emphasis (on the demo, C2's label sits
    under the key's top edge). The timing-handle reveal on the same Leave is untouched."""
    view, _s, _chart, _chart_off, map_label, _map_off = _hover_rig()
    cm = view.map._corner_markers
    try:
        map_label("C1")
        assert cm.highlighted == "C1" and view.plots.corner_emphasis() == "C1"
        QApplication.sendEvent(view.map.widget.viewport(), QEvent(QEvent.Type.Leave))
        _APP.processEvents()
        assert cm.highlighted is None, f"the ring stayed on {cm.highlighted} after the canvas Leave"
        assert view.plots.corner_emphasis() is None
        assert view.map._timing_hover is False
    finally:
        view.hide()
        view.dispose()
    print("test_leaving_the_map_canvas_onto_its_own_overlays_clears_the_hover OK")


def test_hovering_an_apex_dot_names_its_corner():
    """The map names a corner from its apex DOT as well as its label plate (within
    CORNER_HOVER_PX of the dot): the dot under the pointer, not a neighbour's."""
    from studio import map_view
    view, _s, _chart, _chart_off, _map_label, map_off = _hover_rig()
    cm = view.map._corner_markers
    vb = view.map.plot.getViewBox()
    try:
        for label, x, y, _d in cm._markers:
            at = vb.mapViewToScene(QPointF(float(x), float(y)))
            assert not any(t.sceneBoundingRect().contains(at) for t in cm._texts), (
                f"{label}'s apex is under a label plate: the dot branch would not be reached")
            _pointer_to(view.map.widget, at + QPointF(map_view.CORNER_HOVER_PX - 2.0, 0.0))
            assert cm.highlighted == label, f"hovering {label}'s apex dot rang {cm.highlighted}"
            assert view.plots.corner_emphasis() == label
            map_off()
    finally:
        view.hide()
        view.dispose()
    print("test_hovering_an_apex_dot_names_its_corner OK")


def test_a_rebuild_under_a_resting_pointer_lets_the_next_move_name_the_corner_again():
    """A re-segment drops both rings and CentralView's hovered corner. Each view must forget what
    it last said too, or — with the pointer still on C1 — its next move compares equal to the
    stale "C1" and says nothing, leaving C1 unlit under the pointer."""
    view, _s, chart, chart_off, map_label, map_off = _hover_rig()
    cm = view.map._corner_markers
    try:
        map_label("C1")
        assert cm.highlighted == "C1"
        view.rebuild_derived_views(reselect=True)
        _APP.processEvents()
        assert cm.highlighted is None and view.plots.corner_emphasis() is None
        map_label("C1")
        assert cm.highlighted == "C1", "the map stayed silent on C1 after a rebuild"
        assert view.plots.corner_emphasis() == "C1"
        map_off()
        chart("C2")
        view.rebuild_derived_views(reselect=True)
        _APP.processEvents()
        chart("C2")
        assert cm.highlighted == "C2", "the chart stayed silent on C2 after a rebuild"
        chart_off()
    finally:
        view.hide()
        view.dispose()
    print("test_a_rebuild_under_a_resting_pointer_lets_the_next_move_name_the_corner_again OK")


def test_a_coaching_jump_lights_the_chart_label_too():
    """Coaching's Jump rings the corner on the map; it went straight to `map.highlight_corner`,
    so the chart label never followed it. It now rides CentralView's one corner state."""
    from studio.app import StudioWindow
    view, s = _shown_view_on_the_laps_tab()
    try:
        win = SimpleNamespace(session=s, view=view, _jumping=False,
                              _reveal_jump_corner=lambda _cid, **_kw: None)
        StudioWindow._jump_to_opportunity(win, 2, 0.0)
        _APP.processEvents()
        assert view.map._corner_markers.highlighted == "C2"
        assert view.plots.corner_emphasis() == "C2", (
            f"a Jump to C2 left the chart on {view.plots.corner_emphasis()}")
    finally:
        view.hide()
        view.dispose()
    print("test_a_coaching_jump_lights_the_chart_label_too OK")


def test_the_hovered_corner_travels_through_central_view_only():
    """No view holds another: the chart's `cornerHovered` reaches the map ONLY through
    CentralView's one state. Stub the map's receiver and the id arrives through it; cut
    CentralView's slot and the same emission reaches nothing (no direct wire was ever made)."""
    view, _s = _shown_view_on_the_laps_tab()
    try:
        got = []
        view.map.set_hovered_corner = got.append
        view.plots.cornerHovered.emit("C2")
        assert got == ["C2"] and view._hovered_corner == "C2"
        view.plots.cornerHovered.emit(None)
        assert got == ["C2", None]
        view.plots.cornerHovered.disconnect(view._on_corner_hovered)
        view.map.cornerHovered.disconnect(view._on_corner_hovered)
        view.plots.cornerHovered.emit("C1")
        assert got == ["C2", None], "the chart reached the map without CentralView"
        emph = []
        view.plots.set_corner_emphasis = emph.append
        view.map.cornerHovered.emit("C1")
        assert emph == [], "the map reached the chart without CentralView"
    finally:
        view.hide()
        view.dispose()
    print("test_the_hovered_corner_travels_through_central_view_only OK")


def test_corner_hover_on_the_map_is_inert_during_a_lap_race():
    """While a Lap Race owns the map canvas, hovering a corner there names nothing, and a chart
    hover rings nothing on it (the chart's own label still lights). Ending the race restores it."""
    view, _s, chart, chart_off, map_label, map_off = _hover_rig()
    cm = view.map._corner_markers
    try:
        assert view.start_lap_race(), "the two-lap stadium must be raceable"
        seen = []
        view.map.cornerHovered.connect(seen.append)
        map_label("C1")
        assert seen == [] and view.plots.corner_emphasis() is None, seen
        chart("C2")
        assert cm.highlighted is None, f"a chart hover rang {cm.highlighted} under the race"
        assert view.plots.corner_emphasis() == "C2"
        chart_off()
        view.end_lap_race()
        map_label("C1")
        assert seen == ["C1"] and cm.highlighted == "C1"
    finally:
        view.end_lap_race()
        view.hide()
        view.dispose()
    print("test_corner_hover_on_the_map_is_inert_during_a_lap_race OK")


def test_corner_hover_names_the_right_corner_in_time_mode():
    """The chart labels move to the time axis on an x-mode flip (same items, new x); a hover in
    time mode must still name the corner under the pointer, and the map ring that corner."""
    view, s, chart, chart_off, _map_label, _map_off = _hover_rig()
    try:
        view.plots.x_mode_combo.setCurrentIndex(1)     # what picking "x: time" does
        _APP.processEvents()
        assert view.plots.axis_mode() == "time"
        want = dict(s.corner_plot_positions("time"))
        got = {lb: x for lb, x in view.plots.corner_labels_shown()}
        assert got and all(abs(got[lb] - want[lb]) < 1e-9 for lb in got), (got, want)
        last = sorted(got, key=got.get)[-1]
        chart(last)
        assert view.map._corner_markers.highlighted == last
        assert view.plots.corner_emphasis() == last
        chart_off()
    finally:
        view.hide()
        view.dispose()
    print("test_corner_hover_names_the_right_corner_in_time_mode OK")


def test_a_corner_whose_chart_label_was_dropped_still_lights_from_the_map():
    """The chart drops labels that do not fit (here: zoomed so C1 is off the axis). Hovering C1
    on the map still rings it there; the chart paints nothing for it — no other label lights up
    in its place — and the emphasis is on the hidden item, so a zoom back shows it lit."""
    view, _s, _chart, _chart_off, map_label, map_off = _hover_rig()
    try:
        x2 = dict(view.plots._corner_positions)["C2"]
        view.plots.p_speed.setXRange(x2 - 5.0, x2 + 5.0, padding=0)
        _APP.processEvents()
        assert "C1" not in dict(view.plots.corner_labels_shown())
        map_label("C1")
        assert view.map._corner_markers.highlighted == "C1"
        hot = theme.C.text
        lit = [ln.label.toPlainText() for ln in view.plots._corner_items
               if ln.isVisible() and ln.label.color.name() == QColor(hot).name()]
        assert lit == [], f"a dropped C1 lit another painted label: {lit}"
        map_off()
        assert view.map._corner_markers.highlighted is None
    finally:
        view.hide()
        view.dispose()
    print("test_a_corner_whose_chart_label_was_dropped_still_lights_from_the_map OK")


def test_playback_ticks_do_no_corner_hover_work():
    """Hover is event-driven: a 300-tick playback sweep calls neither the chart's hit-test, its
    emphasis push, nor the map's hover ring — not once (counters, not a clock)."""
    view, _s, t0, _t1 = _real_central_view()
    calls = {"corner_at": 0, "emphasis": 0, "map_hover": 0, "map_hit": 0}

    def count(name, fn):
        def wrapped(*a, **k):
            calls[name] += 1
            return fn(*a, **k)
        return wrapped
    view.plots.corner_at = count("corner_at", view.plots.corner_at)
    view.plots.set_corner_emphasis = count("emphasis", view.plots.set_corner_emphasis)
    view.map.set_hovered_corner = count("map_hover", view.map.set_hovered_corner)
    cm = view.map._corner_markers
    cm.hit = count("map_hit", cm.hit)
    for t in np.linspace(float(t0[0]) + 0.01, float(t0[-1]) - 0.01, 300):
        view.video.positionChanged.emit(float(t))
        view.tick()
    assert calls == {"corner_at": 0, "emphasis": 0, "map_hover": 0, "map_hit": 0}, calls
    print("test_playback_ticks_do_no_corner_hover_work OK")


def test_show_stats_maximized_is_a_true_toggle():
    """View ▸ Session statistics (⌘⇧S): one action flips the lap panel to Stats AND maximizes
    it; the same action again restores the grid but stays on the Stats page (mirroring the ⤢
    button's restore)."""
    view, _s, _t0, _t1 = _real_central_view()
    view.select_lap_tab(1)  # start from Corners to prove the flip

    view.show_stats_maximized()
    _APP.processEvents()
    assert view.tab_bar.currentIndex() == 2 and view.table_stack.currentIndex() == 2
    assert view._maximized_panel is view._table_panel, "one action must maximize the panel"

    view.show_stats_maximized()
    _APP.processEvents()
    assert view._maximized_panel is None, "second invocation restores the grid"
    assert view.tab_bar.currentIndex() == 2, "the page itself stays on Stats"
    print("test_show_stats_maximized_is_a_true_toggle OK")


def test_stats_corner_row_click_restores_grid_then_rings_map():
    """N10 continuity: a CORNERS-table row click while the dashboard is maximized must
    restore the grid FIRST (the apex ring would otherwise paint on a zero-width map), then
    ring the corner. Runs on the stadium synthetic's REAL corner detection."""
    view, _s, _t0, _t1 = _real_central_view()
    view.show_stats_maximized()
    _APP.processEvents()
    assert view._maximized_panel is view._table_panel
    t = view.stats_view.corners.table
    assert t.rowCount() > 0, "stadium fixture must yield a corner report"

    t.selectRow(0)
    _APP.processEvents()
    assert view._maximized_panel is None, "row click must restore the grid before ringing"
    print("test_stats_corner_row_click_restores_grid_then_rings_map OK")


# W2 (from #346): what the one supported grip comparison is, in the driver's words. Asserted as a
# phrase on each surface's REAL text rather than through the shared constant alone, so the test
# cannot pass by comparing a constant with itself.
_GRIP_SAYS = "lap to lap within the same corner, not corner to corner"


def test_every_grip_surface_says_it_compares_laps_not_corners():
    """W2. #346 measured that a corner's grip reading tracks that corner's own time ACROSS LAPS
    (pooled over four recordings, and with the lap's pace removed), and that ranking corners
    AGAINST EACH OTHER by it is unpowered with this few corners — see
    studio/docs/grip-regrounding-2026-09.md. Nothing beside the number said so, which left a low
    reading free to be read as "the corner with the most headroom": the one comparison the data
    does not support.

    FOUR places show it, found by search (every reader of `lap_corner_grip` /
    `lap_grip_utilization` / `grip_median`, and every doc naming the column): the Corners tab's
    Grip column, the Stats page's CORNERS Grip column, the map's Grip line and the driver's guide.
    They must say the SAME thing, so the three in-app ones carry one shared sentence and the guide
    its words. Each surface is read off the real widget, and every one missing the sentence is
    named before the test fails, not just the first.

    And they must call it by ONE NAME. #350 left the Corners tab and the map saying "Grip (est)"
    and Stats ▸ CORNERS saying "Grip %" for the same reading; a reader has no way to know those are
    one number. Every in-app label is read off its widget and must be `estimated_label("Grip")`."""
    view, _s, _t0, _t1 = _real_central_view()
    try:
        corners = view.corner_table.table
        stats = view.stats_view.corners.table
        combo = view.map.rainbow_combo
        grip_name = theme.estimated_label("Grip")
        labels = {
            "Corners tab header": [corners.horizontalHeaderItem(c).text()
                                   for c in range(corners.columnCount())],
            "Stats ▸ CORNERS header": [stats.horizontalHeaderItem(c).text()
                                       for c in range(stats.columnCount())],
            "map line-colour entry": [combo.itemText(i).removeprefix("Line: ")
                                      for i in range(combo.count())],
        }
        grip_named = {where: [t for t in texts if "grip" in t.lower()]
                      for where, texts in labels.items()}
        misnamed = {where: got for where, got in grip_named.items() if got != [grip_name]}
        assert not misnamed, (
            f"one grip reading, one name: every surface must say exactly {grip_name!r}, and these "
            f"do not: {misnamed}")
        grip_cols = [labels["Corners tab header"].index(grip_name)]
        guide = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "docs", "FIRST_LAP.md")
        with open(guide, encoding="utf-8") as f:
            guide_text = " ".join(f.read().split())   # the guide wraps its lines at 100 cols
        surfaces = {
            "Corners tab · Grip (est) header tooltip":
                corners.horizontalHeaderItem(grip_cols[0]).toolTip(),
            "Stats ▸ CORNERS table tooltip": stats.toolTip(),
            "map ▸ line-colour dropdown tooltip": view.map.rainbow_combo.toolTip(),
            "docs/FIRST_LAP.md": guide_text,
        }
        missing = [name for name, text in surfaces.items() if _GRIP_SAYS not in text]
        assert not missing, (
            f"these grip surfaces never say what the reading can compare "
            f"({_GRIP_SAYS!r}): {missing}")
        # One sentence, not four paraphrases of it: the in-app three carry the shared constant
        # verbatim, so a later edit to one cannot quietly leave the others saying something else.
        note = getattr(theme, "GRIP_COMPARE_NOTE", None)
        assert note and _GRIP_SAYS in note, note
        drift = [name for name, text in surfaces.items()
                 if name != "docs/FIRST_LAP.md" and note not in text]
        assert not drift, f"these surfaces paraphrase the grip sentence instead of sharing it: {drift}"
        # No figure in the prose: the measured ρ belong to #346's probe and record, where a test
        # derives them; a literal here would rot the day a recording changed.
        assert not any(ch.isdigit() for ch in note), note
    finally:
        view.dispose()
    print("test_every_grip_surface_says_it_compares_laps_not_corners OK")


def test_gmeter_overlay_stays_pinned_to_its_video_and_stands_down_with_it():
    """The g-meter dial is a frameless ALWAYS-ON-TOP window (a child widget would be hidden behind
    the QVideoWidget's native macOS surface), so nothing in Qt keeps it attached — the pane has to
    do it. Two ways it came adrift, both measured on the real window:

      * MAXIMIZING ANY OTHER PANEL. A collapsed splitter section is not a tidy zero-sized widget:
        maximizing the lap table left `video 1432x0`, and maximizing the charts left a healthy
        `280x471` inside a column Qt had simply MOVED to pos=(-1,-855) — every widget still
        isVisible(), no zero-sized ancestor. mapToGlobal then put the dial at (-251, 37) and
        (149, -787): a stray always-on-top window sitting outside the app, over the desktop.
      * DRAGGING THE APP WINDOW WHILE PAUSED. A child gets no Move event when its top-level moves,
        and the only other re-pin (set_g, ~30 Hz) runs solely when the playhead ADVANCES — so a
        paused window drag stranded the dial at its old screen coordinates.

    Assertions are on real geometry after real clicks: visible ⇒ inside the window's rect."""
    view, _s, _t0, _t1 = _real_central_view()
    win = QMainWindow()
    win.setCentralWidget(view)
    win.resize(1200, 800)
    win.move(80, 80)
    win.show()
    _APP.processEvents()
    overlay = view.video.pane.gmeter
    try:
        view.video.gmeter_btn.click()
        _APP.processEvents()
        assert overlay.isVisible(), "the toggle must show the dial"
        assert win.geometry().contains(overlay.geometry()), overlay.geometry()

        # A window drag with NO tick: the dial must travel with the video, not stay behind.
        before = overlay.geometry()
        win.move(360, 300)
        _APP.processEvents()
        assert overlay.geometry() != before, "the dial must follow a paused window drag"
        assert win.geometry().contains(overlay.geometry()), overlay.geometry()

        # Maximizing any OTHER panel collapses the video: the dial stands down, and comes back
        # (still inside the window) when the grid is restored.
        for btn in (view._table_max_btn, view._plots_max_btn, view._map_max_btn):
            btn.click()
            _APP.processEvents()
            assert not overlay.isVisible(), (
                f"the dial must hide when the video is collapsed, not float at "
                f"{overlay.geometry().getRect()}")
            btn.click()
            _APP.processEvents()
            assert overlay.isVisible(), "restoring the grid must bring the dial back"
            assert win.geometry().contains(overlay.geometry()), overlay.geometry()

        # Maximizing the VIDEO itself keeps it — that is the one panel that can host the dial.
        view._video_max_btn.click()
        _APP.processEvents()
        assert overlay.isVisible() and win.geometry().contains(overlay.geometry())
        view._video_max_btn.click()
        _APP.processEvents()

        # And the toggle still wins: off means off.
        view.video.gmeter_btn.click()
        _APP.processEvents()
        assert not overlay.isVisible()
    finally:
        overlay.hide()
        win.hide()
    print("test_gmeter_overlay_stays_pinned_to_its_video_and_stands_down_with_it OK")


def test_splitter_handles_stay_thin_under_the_theme():
    """A panel divider is a HIT AREA, not a gutter: under the real theme QSS a handle must stay
    thin on its split axis, paint a centred grip, and turn amber on hover.

    Asserted on laid-out GEOMETRY, because geometry is exactly what broke. The old rule inset a
    short grip with `margin: 24px 3px`, but Qt's stylesheet box model adds a handle rule's margins
    to BOTH axes of its sizeHint and QSplitter lays the handle out at that sizeHint — so the
    left/right divider rendered as a 67px dead band down the middle of the window (28px for each
    horizontal seam) while handleWidth() and PM_SplitterWidth both kept reporting 19. No property
    the code could read exposed it; only the pixels did.

    Runs under the REAL theme — the QSS IS the bug surface, a stock unstyled QSplitter never had
    this — then restores the app's default chrome for the tests that follow.
    """
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QHoverEvent
    from PySide6.QtWidgets import QSplitter, QWidget

    def centre_pixel(w):
        img = w.grab().toImage()
        return f"#{img.pixel(img.width() // 2, img.height() // 2) & 0xFFFFFF:06X}"

    view, _s, _t0, _t1 = _real_central_view()
    prior = (_APP.styleSheet(), _APP.font(), _APP.palette())
    theme.apply_theme(_APP)
    try:
        for orientation, span_of in ((Qt.Horizontal, lambda sz: sz.width()),
                                     (Qt.Vertical, lambda sz: sz.height())):
            sp = QSplitter(orientation)
            sp.addWidget(QWidget())
            sp.addWidget(QWidget())
            sp.resize(600, 300)
            sp.show()
            _APP.processEvents()
            handle = sp.handle(1)
            span = span_of(handle.sizeHint())
            assert span <= 10, f"{orientation} handle is {span}px — a gutter, not a divider"
            # The grip must still be visible (that is what the margins were for) ...
            assert centre_pixel(handle) == theme.C.border_strong, centre_pixel(handle)
            # ... and still brighten on hover: `border-color` hover no longer applies once the
            # grip is a gradient, so the hover rule has to restate the gradient.
            _APP.sendEvent(handle, QHoverEvent(QEvent.Type.HoverEnter, QPointF(4, 4),
                                               QPointF(-1, -1), QPointF(4, 4)))
            _APP.processEvents()
            assert centre_pixel(handle) == theme.C.accent, centre_pixel(handle)
            sp.hide()

        # The whole point, on the real window: the two columns must be flush. Anything the
        # splitter withholds from sizes() is bare canvas the user sees as a grey band.
        view.resize(1440, 900)
        view.show()
        _APP.processEvents()
        main = view._main_splitter
        band = main.width() - sum(main.sizes())
        assert band <= 10, f"{band}px of dead canvas between the left and right columns"
        for column in (view._left_splitter, view._right_splitter):
            seam = column.height() - sum(column.sizes())
            assert seam <= 10, f"{seam}px of dead canvas between stacked panels"
    finally:
        _APP.setStyleSheet(prior[0])
        _APP.setFont(prior[1])
        _APP.setPalette(prior[2])
    view.hide()
    print("test_splitter_handles_stay_thin_under_the_theme OK")


def test_u2_lap_table_cap_notice_reaches_the_window_status_bar():
    """U2(c), the wiring half: LapTable.selection_capped -> CentralView.statusNotice -> the REAL
    StudioWindow's status bar, through the production _build_ui. (The sentence itself is pinned in
    test_studio_features; this 2-lap fixture cannot exceed the cap, so the table's signal is fired
    directly — what is under test is that nothing between the table and the bar drops it.)"""
    win, view = _studiowindow_with_view()
    try:
        view.table.selection_capped.emit("probe: the charts overlay at most 6 laps")
        assert win.statusBar().currentMessage() == "probe: the charts overlay at most 6 laps", \
            win.statusBar().currentMessage()
    finally:
        win._tick_timer.stop()
        win.deleteLater()
    print("test_u2_lap_table_cap_notice_reaches_the_window_status_bar OK")


def test_the_fixture_window_writes_its_library_entry_into_a_jail_never_the_real_library():
    """H6. The fixture's session now builds a real library entry, so the window's library writes are
    live, and this file diverts no app-support seam of its own. The seams are checked BEFORE the
    write is driven, so a fixture that lost its jail fails on the assertion and never reaches the
    developer's library.

    The write is then driven for real and read back from the jail. A jail the refresh never reached
    would prove nothing, and neither would a refresh that still raised the way it did before the
    double had a point API."""
    import importlib

    from test_golden_hermetic import seams_in_studio

    from studio import library
    from studio.dev import _jail
    win, view = _studiowindow_with_view()
    try:
        seams = seams_in_studio()
        assert {"demo", "focus", "library", "marks", "prefs", "session_record",
                "track_db"} <= seams, f"the seam scan lost a known store: {sorted(seams)}"
        real = os.path.abspath(_jail._REAL_DIR)
        live = sorted(n for n in seams if os.path.abspath(
            importlib.import_module(f"studio.{n}")._app_support_dir()) == real)
        assert not live, f"the fixture window can reach the real app-support dir through {live}"

        win.library_ctl.refresh_library_entry()
        rows = [e for e in library.load()["entries"] if e.get("fingerprint") == "stadium"]
        assert len(rows) == 1 and rows[0]["lap_count"] == 2, (
            f"the refresh did not land the fixture's entry in the jailed index: {rows}")
    finally:
        win._tick_timer.stop()
        view.dispose()
        win.deleteLater()
    print("test_the_fixture_window_writes_its_library_entry_into_a_jail_never_the_real_library OK")


# ------------------------------------------------------------------ P1: the Stats page's staleness
#
# The three tests below are ONE contract in three parts. The Stats page renders lazily now
# (`StatsView.refresh_when_shown` — half of a 396 ms rebuild on a 65-lap recording went into a page
# nobody was looking at), and the risk that buys is a figure on the app's HONESTY SURFACE that
# silently predates the last edit. So two of the three are negative controls on staleness and only
# the third is about speed; if the design is ever unwound, the first two are what must go red.

def _shown_view_on_the_laps_tab():
    """A real CentralView, shown, with the lap panel on LAPS — i.e. the Stats page mounted in the
    stack but NOT visible, which is the state the deferral exists for."""
    view, s, _t0, _t1 = _real_central_view()
    view.resize(1000, 760)
    view.show()
    view.select_lap_tab(0)
    _APP.processEvents()
    assert not view._stats.isVisible(), "the Stats page should be a hidden stack page on Laps"
    return view, s


def _flag_a_dropout(view, s, ids=(1,)):
    """A session change the Stats page MUST show, on a NUMBER and not on prose: the laps tile
    counts GPS-dropout laps beside the valid ones (`2` -> `2 · 1 ⚠`), and it is one of the figures
    the DATA TRUST story on that page is told in. Returns (tile text before the change, the mark)."""
    from studio.lap_table import DROPOUT_MARK
    before = view.stats_view.t_laps.value.text()      # through the accessor: current by contract
    assert DROPOUT_MARK not in before, before
    s.dropout_lap_ids = lambda: set(ids)
    view.rebuild_derived_views(reselect=True)
    return before, DROPOUT_MARK


def test_the_stats_page_handed_to_a_reader_is_never_stale():
    """NEGATIVE CONTROL #1 — the accessor. Anything that says `view.stats_view` gets a page that
    already knows about the last edit, even though the page is hidden and deliberately did not
    re-render when the edit happened.

    This is the failure the whole design is built to prevent, and PR #233 declined to defer for
    exactly this reason. Watched fail on a tree with the `flush_if_stale()` line deleted from the
    `CentralView.stats_view` property:

        AssertionError: the accessor handed out a page that predates the edit: '2' == '2'

    The reader here is `view.stats_view` because that is the name `studio/dev/media_capture.py`,
    the corner-table wiring and every test use — there is no other name for the widget."""
    view, s = _shown_view_on_the_laps_tab()
    try:
        before, mark = _flag_a_dropout(view, s)
        assert view._stats._stale, (
            "a hidden page should have DEFERRED its render — this test proves nothing otherwise")
        after = view.stats_view.t_laps.value.text()
        assert after != before, (
            f"the accessor handed out a page that predates the edit: {after!r} == {before!r}")
        assert mark in after, f"the excluded lap is not on the page the reader was handed: {after!r}"
        assert not view._stats._stale, "the accessor left the page still owing a render"
    finally:
        view.hide()
        view.dispose()
    print("test_the_stats_page_handed_to_a_reader_is_never_stale OK")


def test_the_stats_page_pays_its_deferred_render_before_it_can_be_painted():
    """NEGATIVE CONTROL #2 — the other route to the page's contents: being LOOKED AT.

    Every read below goes through a reference held BEFORE the edit, never through
    `CentralView.stats_view`, so the accessor cannot rescue it: what is asserted is that arriving
    on the page — by the tab bar, and by the data-quality chip's `show_data_trust` — leaves nothing
    owed. Watched fail with `flush_if_stale()` deleted from `StatsView.showEvent`:

        AssertionError: the page became visible still owing a render

    `reveal_trust` is included deliberately: #333 made the GPS chip open the DATA TRUST card and
    scroll to a row, which makes it a reader of exactly the surface that must not lie."""
    view, s = _shown_view_on_the_laps_tab()
    try:
        page = view._stats                 # HELD: nothing below re-enters the accessor
        before, mark = _flag_a_dropout(view, s)
        assert page._stale and mark not in page.t_laps.value.text(), (
            "the hidden page rendered anyway — the deferral this test guards is not in force")

        view.select_lap_tab(2)             # exactly what a click on the "Stats" tab does
        _APP.processEvents()
        assert page.isVisible()
        assert not page._stale, "the page became visible still owing a render"
        assert mark in page.t_laps.value.text(), (
            f"a painted Stats page is showing pre-edit numbers: {page.t_laps.value.text()!r}")

        # ...and the same again for the chip's landing, from a fresh deferral.
        view.select_lap_tab(0)
        _APP.processEvents()
        s.dropout_lap_ids = lambda: {0, 1}
        view.rebuild_derived_views(reselect=True)
        assert page._stale
        view.show_data_trust()
        _APP.processEvents()
        assert not page._stale, "reveal_trust landed the reader on a page still owing a render"
        assert f"2 {mark}" in page.t_laps.value.text(), page.t_laps.value.text()
    finally:
        view.hide()
        view.dispose()
    print("test_the_stats_page_pays_its_deferred_render_before_it_can_be_painted OK")


def test_a_rebuild_skips_the_stats_render_on_another_tab_and_does_it_on_the_stats_tab():
    """THE SAVING ITSELF, asserted as a call count rather than a clock (a timing assertion on a
    loaded CI box is a flake). On D24 GX010062 (65 laps) the render this skips is 200 ms of a
    396 ms `rebuild_derived_views`; here it is simply "did it happen at all".

    Both directions, because half a deferral is a bug: a rebuild while the Stats tab is showing
    must still render immediately — the user is looking at the numbers that just changed."""
    view, _s = _shown_view_on_the_laps_tab()
    try:
        renders = []
        real = view._stats.refresh
        view._stats.refresh = lambda: (renders.append(1), real())[1]

        view.rebuild_derived_views(reselect=True)
        assert renders == [], f"the hidden Stats page rendered {len(renders)} time(s) anyway"

        view.select_lap_tab(2)             # arriving pays the one deferred render
        _APP.processEvents()
        assert len(renders) == 1, renders
        view.rebuild_derived_views(reselect=True)
        assert len(renders) == 2, (
            "a rebuild did NOT render the Stats page the user is looking at")
    finally:
        del view._stats.refresh
        view.hide()
        view.dispose()
    print("test_a_rebuild_skips_the_stats_render_on_another_tab_and_does_it_on_the_stats_tab OK")


# ============================================================ motion (studio/motion.py)
class _MotionClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@contextlib.contextmanager
def _motion_on():
    """Motion ON offscreen (PACER_MOTION=1, no Reduce motion) on a fake clock the test steps."""
    from studio import motion
    clock = _MotionClock()
    old = os.environ.get("PACER_MOTION")
    saved = (motion._reduce_pref, motion._system_reduce)
    os.environ["PACER_MOTION"] = "1"
    motion.set_reduce_pref(None)
    motion._system_reduce = False
    motion.set_clock(clock)
    try:
        yield clock
    finally:
        motion.finish_all()
        motion.set_clock(None)
        motion._reduce_pref, motion._system_reduce = saved
        if old is None:
            os.environ.pop("PACER_MOTION", None)
        else:
            os.environ["PACER_MOTION"] = old


def _seek(view, t):
    view.video.positionChanged.emit(float(t))
    view.tick()


def _hero_state(view):
    return view.diff_box.text(), view.diff_box.delta_colour()


def _sign_agrees_with_colour(text, colour):
    """A rolling frame never shows a sign in the other side's colour (or a colour on zero)."""
    head = text.partition("     ")[0]
    m = re.search(r"([+−-]?)(\d+\.\d+)", head)
    assert m, text
    sign, mag = m.group(1), float(m.group(2))
    if colour == theme.behind_colour():
        return sign == "+"
    if colour == theme.ahead_colour():
        return sign in ("−", "-")
    return mag < 0.07     # neutral only under the timing step (cool/delta-readout-level)


def _direct_could_print(view, text, colour):
    """True when `text`/`colour` is exactly what the DIRECT path prints for some Δ: the frame's own
    number fed back through the hero's formatter (so a clamped Δideal can never roll through a
    value the direct path would not show, e.g. a negative)."""
    head, sep, tail = text.partition("     ")
    m = re.search(r"([+−-]?)(\d+\.\d+)", head)
    v = float(m.group(2)) * (-1.0 if m.group(1) in ("−", "-") else 1.0)
    lap = view._last_diff_lap
    if view.ideal_readout_btn.isChecked() and view._ideal_state == "stitched":
        want_text, want_col = theme.format_ideal_readout(v, view._last_diff_speed, lap,
                                                         view._speed_unit)
    else:
        from studio.central_view import _name_hero_baseline
        want_text, want_col = theme.format_delta_speed(v, view._last_diff_speed, lap,
                                                       view._speed_unit)
        want_text = _name_hero_baseline(want_text, view.session.has_reference())
    from studio.central_view import _level_readout
    want_text, want_col = _level_readout(want_text, want_col, v)
    return want_text.partition("     ")[0] == head and want_col == colour


# Δideal on the two-lap stadium fixture: +0.49 s / +2.60 s on lap 0, +1.18 s / +4.60 s on lap 1.
_LAP0_EARLY_T, _LAP0_T, _LAP1_T, _LAP1_LATE_T = 112.82, 125.14, 305.78, 326.9


def _roll_frames(view, clock, n=14):
    from studio import motion
    frames = []
    for _ in range(n):
        clock.now += 1 / 60
        motion.pump()
        frames.append(_hero_state(view))
    return frames


def _frame_value(text):
    m = re.search(r"([+−-]?)(\d+\.\d+)", text.partition("     ")[0])
    return float(m.group(2)) * (-1.0 if m.group(1) in ("−", "-") else 1.0)


def _true_range(view, s, lap, a, b, lead):
    """min/max of the NAMED quantity the hero leads with, sampled densely between two playhead
    positions on one lap — what every rolling frame must lie within."""
    vals = []
    for t in np.linspace(a, b, 400):
        v = (s.delta_to_ideal_at(lap, float(t)) if lead == "ideal"
             else s.delta_at_lap(lap, float(t)))
        if v is not None:
            vals.append(max(v, 0.0) if lead == "ideal" else v)   # the hero floors Δideal at 0
    return min(vals), max(vals)


def test_motion_the_delta_rolls_on_a_seek_and_ends_exactly_on_the_direct_text():
    """A paused seek WITHIN a lap counts the hero Δ from the value on screen to the new one over
    DELTA_ROLL_MS. Every frame is a value the named quantity really took between the two playhead
    positions (sampled: Δ is continuous along a lap), one the direct path could print (the same
    formatter: the clamped Δideal never shows a negative), its sign agrees with its colour, and the
    END is the motion-off text and colour, character for character — in both leads."""
    from studio import motion
    # Lap 0 is the best lap: its Δbest is 0 throughout, so its seek rolls only under Δideal.
    pairs = {"ideal": ((_LAP1_T, _LAP1_LATE_T), (_LAP1_LATE_T, _LAP1_T), (_LAP0_EARLY_T, _LAP0_T)),
             "best": ((_LAP1_T, _LAP1_LATE_T), (_LAP1_LATE_T, _LAP1_T))}
    want = {}
    pinned = os.environ.get("PACER_MOTION")
    os.environ["PACER_MOTION"] = "0"      # the motion-off reference, even in a PACER_MOTION=1 run
    off, _s, _t0, _t1 = _real_central_view()
    try:
        for lead in ("ideal", "best"):
            off.ideal_readout_btn.setChecked(lead == "ideal")
            for a, b in pairs[lead]:
                _seek(off, a)
                _seek(off, b)
                want[(lead, a, b)] = _hero_state(off)
        assert motion.active_count() == 0, "with motion off nothing may run"
    finally:
        off.dispose()
        if pinned is None:
            os.environ.pop("PACER_MOTION", None)
        else:
            os.environ["PACER_MOTION"] = pinned
    with _motion_on() as clock:
        view, s, _t0, _t1 = _real_central_view()
        try:
            for lead in ("ideal", "best"):
                view.ideal_readout_btn.setChecked(lead == "ideal")
                for a, b in pairs[lead]:
                    _seek(view, a)
                    motion.finish_all()
                    before = _hero_state(view)
                    _seek(view, b)
                    assert view._diff_roll is not None, f"a {lead} seek {a}->{b} did not roll"
                    assert (_hero_state(view)[0].partition("     ")[0]
                            == before[0].partition("     ")[0]), "frame 0 must show the old value"
                    frames = _roll_frames(view, clock)
                    mid = {f[0] for f in frames[:-1]} - {want[(lead, a, b)][0], before[0]}
                    assert mid, f"the {lead} roll {a}->{b} showed no intermediate value"
                    lo, hi = _true_range(view, s, view._last_diff_lap, a, b, lead)
                    never = [f for f in frames if not lo - 0.006 <= _frame_value(f[0]) <= hi + 0.006]
                    assert not never, f"a frame the {lead} quantity never took in [{lo}, {hi}]: {never}"
                    bad = [f for f in frames if not _sign_agrees_with_colour(*f)]
                    assert not bad, f"a rolling frame's colour disagrees with its sign: {bad}"
                    odd = [f for f in frames if not _direct_could_print(view, *f)]
                    assert not odd, f"a rolling frame the direct path could not print: {odd}"
                    assert view._diff_roll is None and motion.active_count() == 0
                    assert not motion.timer_active()
                    assert _hero_state(view) == want[(lead, a, b)], (
                        _hero_state(view), want[(lead, a, b)])
        finally:
            view.dispose()
    print("test_motion_the_delta_rolls_on_a_seek_and_ends_exactly_on_the_direct_text OK")


def test_motion_a_baseline_or_lap_change_cuts_and_never_pairs_a_name_with_another_number():
    """A roll counts between two readings of ONE quantity. Flipping vs ideal (Δideal ↔ Δbest)
    and changing lap (another lap's Δ curve: no path joins the two readings) do NOT roll: the
    first frame is already the direct path's text, and no later frame differs from it — so no
    frame ever prints one baseline's number under another's name."""
    from studio import motion
    with _motion_on() as clock:
        view, _s, _t0, _t1 = _real_central_view()
        try:
            view.ideal_readout_btn.setChecked(True)
            _seek(view, _LAP1_LATE_T)
            motion.finish_all()
            for flip in (False, True):
                view.ideal_readout_btn.setChecked(flip)
                assert view._diff_roll is None, f"a vs-ideal flip to {flip} rolled"
                direct = _hero_state(view)
                assert direct[0].startswith("Δideal " if flip else "Δbest "), direct
                assert set(_roll_frames(view, clock, 6)) == {direct}, "a frame left the direct text"
            for a, b in ((_LAP0_T, _LAP1_T), (_LAP1_LATE_T, _LAP0_EARLY_T)):
                _seek(view, a)
                motion.finish_all()
                _seek(view, b)
                assert view._diff_roll is None, f"lap change {a}->{b} rolled"
                direct = _hero_state(view)
                assert set(_roll_frames(view, clock, 6)) == {direct}
        finally:
            view.dispose()
    print("test_motion_a_baseline_or_lap_change_cuts_and_never_pairs_a_name_with_another_number OK")


def test_motion_playback_and_small_jumps_never_animate_and_idle_ticks_do_no_motion_work():
    """With motion ON: a 300-tick playback sweep across both laps (a lap change at the line, Δ
    jumps of seconds) starts no transition and steps no frame — the playback path is the old
    path, counted, not timed. A paused nudge under ROLL_MIN_JUMP_S sets the text directly."""
    from studio import motion
    with _motion_on():
        view, _s, t0, t1 = _real_central_view()
        try:
            _seek(view, float(t0[0]) + 0.01)
            starts, frames = motion.starts, motion.frames
            view.video.is_playing = lambda: True
            for t in np.concatenate([np.linspace(float(t0[0]) + 0.02, float(t0[-1]) - 0.01, 150),
                                     np.linspace(float(t1[0]) + 0.01, float(t1[-1]) - 0.01, 150)]):
                _seek(view, t)
                assert view._diff_roll is None
            assert (motion.starts, motion.frames) == (starts, frames), (
                motion.starts - starts, motion.frames - frames)
            assert motion.active_count() == 0 and not motion.timer_active()
            del view.video.is_playing
            _seek(view, _LAP1_T)
            motion.finish_all()
            starts = motion.starts
            shown = view._diff_shown
            _seek(view, _LAP1_T + 0.02)
            assert abs(view._diff_shown - shown) < motion.ROLL_MIN_JUMP_S
            assert motion.starts == starts and view._diff_roll is None, "a small jump rolled"
        finally:
            view.dispose()
    print("test_motion_playback_and_small_jumps_never_animate_and_idle_ticks_do_no_motion_work OK")


def test_motion_a_held_arrow_follows_directly_and_a_single_press_may_roll():
    """←/→ held down auto-repeats every ~33 ms: a continuous gesture, so the window marks it and
    the hero follows directly (no roll restarted per repeat). A single press is a discrete seek."""
    from PySide6.QtGui import QKeyEvent

    from studio import motion
    with _motion_on():
        win, view = _studiowindow_with_view()
        try:
            seen = []
            real = view.note_continuous_gesture
            view.note_continuous_gesture = lambda: (seen.append(1), real())
            for rep in (False, True):
                win.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier, "",
                                            rep))
            assert seen == [1], f"auto-repeat must mark a continuous gesture, once: {seen}"
            del view.note_continuous_gesture
            view._continuous_until = 0.0
            _seek(view, _LAP1_T)
            motion.finish_all()
            _seek(view, _LAP1_LATE_T)          # a single press's seek: rolls
            assert view._diff_roll is not None
            motion.finish_all()
            view.note_continuous_gesture()      # held: the next seeks follow directly
            for t in (_LAP1_T, _LAP1_LATE_T, _LAP1_T):
                _seek(view, t)
                assert view._diff_roll is None, "a held arrow restarted the roll"
        finally:
            if win._tick_timer is not None:
                win._tick_timer.stop()
            view.dispose()
    print("test_motion_a_held_arrow_follows_directly_and_a_single_press_may_roll OK")


def test_motion_view_menu_reduce_motion_persists_wins_and_can_follow_the_system_again():
    """View ▸ Reduce motion toggles the EFFECTIVE setting, starting from the system's. A choice
    that differs from the system is remembered (prefs, jailed) and wins over PACER_MOTION=1; a
    choice equal to the system's clears it — the way back to following macOS — on either system."""
    from studio import motion, prefs
    with _motion_on():
        win, view = _studiowindow_with_view(build_menu=True)
        try:
            act = win._reduce_motion_action
            assert act.isCheckable() and not act.isChecked() and motion.enabled()
            view_menu = next(a.menu() for a in win.menuBar().actions()
                             if a.menu() is not None and a.text().replace("&", "") == "View")
            assert act in view_menu.actions(), "Reduce motion is not in the View menu"
            assert prefs.reduce_motion() is None, "never chosen = follow the system"
            act.trigger()
            assert prefs.reduce_motion() is True and not motion.enabled()
            _seek(view, _LAP1_T)
            _seek(view, _LAP1_LATE_T)
            assert view._diff_roll is None and motion.active_count() == 0
            act.trigger()               # back to the system's (off): follow the system again
            assert prefs.reduce_motion() is None and motion.enabled()
            motion._system_reduce = True    # a Mac with Reduce Motion on
            act.trigger()               # checked = reduced = the system's: still following it
            assert prefs.reduce_motion() is None and not motion.enabled()
            act.trigger()               # the user wants motion anyway: remembered, wins
            assert prefs.reduce_motion() is False and motion.enabled()
            act.trigger()
            assert prefs.reduce_motion() is None and not motion.enabled()
        finally:
            prefs.set(prefs.REDUCE_MOTION, None)
            if win._tick_timer is not None:
                win._tick_timer.stop()
            view.dispose()
    print("test_motion_view_menu_reduce_motion_persists_wins_and_can_follow_the_system_again OK")


def test_motion_interruptions_end_the_roll_with_no_timer_left():
    """A second seek mid-roll starts from the value ON SCREEN (one roll, never a queue); playback
    starting mid-roll shows the direct text at once; entering compare or a Lap race mid-roll, and a
    view disposed mid-roll (a load, a closed window), end it at once with no timer left and no
    callback into deleted widgets."""
    from shiboken6 import Shiboken

    from studio import motion
    with _motion_on() as clock:
        view, _s = _shown_view_on_the_laps_tab()     # shown: compare and a race need it
        try:
            _seek(view, _LAP1_T)
            motion.finish_all()
            _seek(view, _LAP1_LATE_T)
            clock.now += 0.05
            motion.pump()
            shown = view._diff_rolling_value
            _seek(view, _LAP1_T + 2.0)            # a second seek mid-roll, same lap
            assert view._diff_roll is not None and view._diff_rolling_value == shown
            assert motion.active_count() == 1
            view.video.is_playing = lambda: True   # playback starts mid-roll
            _seek(view, _LAP1_T + 2.1)
            assert view._diff_roll is None and motion.active_count() == 0
            del view.video.is_playing
            _seek(view, _LAP0_EARLY_T)
            motion.finish_all()
            _seek(view, _LAP0_T)                   # mid-roll, enter compare
            assert view._diff_roll is not None
            view.video.compare_btn.click()
            assert view._comparing() and view._diff_roll is None and motion.active_count() == 0
            view.video.compare_btn.click()
            _seek(view, _LAP0_EARLY_T)
            motion.finish_all()
            _seek(view, _LAP0_T)                   # mid-roll, start a race
            assert view._diff_roll is not None
            assert view.start_lap_race(), "the two-lap stadium must be raceable"
            assert view._diff_roll is None and motion.active_count() == 0
            view.end_lap_race()
            _seek(view, _LAP0_EARLY_T)             # mid-roll, the view goes away
            assert view._diff_roll is not None
        finally:
            view.hide()
            view.dispose()
            Shiboken.delete(view)
        assert motion.active_count() == 0 and not motion.timer_active()
        clock.now += 1.0
        motion.pump()                              # must not call into the deleted widgets
    print("test_motion_interruptions_end_the_roll_with_no_timer_left OK")


if __name__ == "__main__":
    # The file's own run fails at once on a missing clip, rather than 30 s in (tests/_samples.py).
    _samples.require("hero8.mp4")
    _samples.require("karma.mp4")
    _run_all()
