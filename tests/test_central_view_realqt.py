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
import os
import sys
import time
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Build the real widget tree with the inert media triplet (no decoder/audio device) — set BEFORE
# importing the studio widgets (the seam is read once at PlayerPane construction).
os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMainWindow  # noqa: E402

_APP = QApplication.instance() or QApplication([])

# _synthetic_session lives in test_session_services (a sibling test module, not a registered test on
# its own import); reuse its REAL-corner-detection stadium fixture instead of re-deriving one.
from test_session_services import _synthetic_session  # noqa: E402

from studio import chapters, data_quality, render_cache, theme, tracks  # noqa: E402
from studio.central_view import CentralView  # noqa: E402


# --------------------------------------------------------------------- fixture
def _real_central_view():
    """A REAL CentralView (its production __init__: real panels + real ScrubController /
    CompareController / PlaybackState / signal wiring) over the two-lap stadium synthetic session.

    The synthetic Session already serves the REAL delta / corner / driving / g-meter math; this only
    adds the few pacer-laps-backed reads CentralView's panels touch that a bare Session lacks:
      * a consistent FULL-trace tx/ty/tt/tv (so the map marker index_at_time -> tx[i] is in bounds);
      * a real LapRenderCache (MapView's best-overlay draw segments);
      * lap_window / lap_at_time / lap_time (the global-clock windows the tick + scrub resolve);
      * lap_rows / dropout / sector splits / consistency stubs (the LapTable + the Stats page);
      * a tiny pacer Sectors (real start_line Segment) so the Session.start_line property resolves.
    Returns (central_view, session, t0, t1) where t0/t1 are the two laps' media-clock time arrays.
    """
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
        lap_has_dropout=s.lap_has_dropout, lap_time=s.lap_time, trace_times=s.tt)

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
    from studio.app import StudioWindow

    _view, s, _t0, _t1 = _real_central_view()
    win = StudioWindow.__new__(StudioWindow)
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

    # Confirm the timing (what a start-line drag does) and rebuild → Verified → strip clears.
    s.confirm_timing()
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
    """Every brake-point glyph brush currently DRAWN, upper-cased hex — the speed chart's series
    and the identical glyphs on the map trace, read off the live ScatterPlotItems (not inferred)."""
    out = []
    for it in view.plots._brake_items:
        out.append(it.opts["brush"].color().name().upper())
    for it in view.map._brake_markers._items:
        out.append(it.opts["brush"].color().name().upper())
    return out


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
    assert best_text.startswith("Δ "), best_text
    assert "ideal" in view.diff_box.toolTip().lower(), view.diff_box.toolTip()
    print(f"test_hero_readout_leads_with_labelled_delta_to_ideal OK ({text!r} / {best_text!r})")


def test_hero_readout_keeps_every_character_when_the_charts_header_is_squeezed():
    """The hero #DiffBox is a QLabel — QLabels never elide, they HARD-CLIP — and the charts header's
    natural minimum is far wider than the column minimum the splitter allows, so Qt squeezes every
    item proportionally. Centred, that ate BOTH ends of the live number (".deal +0.00 s" / "74 km,").
    The number must survive intact at any column width: it carries an explicit floor sized to the
    widest readout it can render, it is LEFT-aligned so any residual squeeze costs the tail rather
    than the leading Δ, and the secondary controls yield first.

    Measured against the PAINTED font (the QSS styles #DiffBox in the mono stack, not in the font
    the widget was constructed with), so the theme is applied for the duration and restored after."""
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
        # The user's own layout (a WIDE left column) first, then well past it.
        for right_w in (931, 700, 460):
            view._main_splitter.setSizes([1511 - right_w, right_w])
            _APP.processEvents()
            room = box.width() - pad
            assert room >= fm.horizontalAdvance(box.text()), (
                right_w, box.width(), box.text(), fm.horizontalAdvance(box.text()))
        # The controls beside it are the ones that gave way — and they now do it by dropping to
        # their icon (full label in the tooltip + accessible name) rather than being centre-clipped
        # under a 34 px floor, so what proves the yield is the empty text, not a squeezed box.
        assert view.plots.ideal_btn.text() == "", view.plots.ideal_btn.text()
        assert view.plots.ideal_btn.width() == view.plots.ideal_btn.sizeHint().width()
    finally:
        _APP.setStyleSheet(prior[0])
        _APP.setFont(prior[1])
        _APP.setPalette(prior[2])
        view.hide()
    print("test_hero_readout_keeps_every_character_when_the_charts_header_is_squeezed OK")


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
    """The map's rainbow channel is now a LABELLED dropdown (Off · Speed · Δ · Grip · Elevation) —
    every channel visible and one click, Grip no longer an undiscoverable blind-cycle step. Selecting
    the Grip entry sets the grip mode (the same render path the old cycle hit); the cycle API works."""
    view, _s, _t0, _t1 = _real_central_view()
    combo = view.map.rainbow_combo
    # Every channel is a labelled, visible entry (not hidden behind a cycle).
    modes = [combo.itemData(i) for i in range(combo.count())]
    assert modes == ["off", "speed", "delta", "grip", "elevation"], modes
    grip_idx = modes.index("grip")
    assert "grip" in combo.itemText(grip_idx).lower(), combo.itemText(grip_idx)

    # Selecting Grip drives the map to the grip channel in ONE click. (Clear the current lap first so
    # _apply_rainbow cleanly no-ops here — the grip channel needs a g signal this fixture doesn't
    # seed; we're pinning the control wiring / mode selection, not the render math, which
    # test_rainbow_map covers.)
    view.map._current_lap = None
    combo.setCurrentIndex(grip_idx)
    _APP.processEvents()
    assert view.map._rainbow_mode == "grip", "the labelled Grip entry must select the grip channel"
    # And the legacy cycle path is preserved + keeps the combo in sync (the rainbow tests' driver).
    view.map._cycle_rainbow()  # grip -> elevation
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
    assert view.opportunities.empty_label.text(), "the excluded state must carry a friendly message"
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
    test_real_qtimer_fires_view_tick_through_studiowindow()
    test_position_signal_then_real_tick_applies_once_and_is_stable()
    test_compare_button_click_is_single_source_of_truth_no_reentrancy()
    test_compare_scrub_fans_one_seek_to_each_real_pane_per_tick()
    test_compare_tick_keeps_panes_consistent_no_reentry()
    test_provisional_banner_shows_and_clears_with_trust_state()
    test_trust_flip_without_a_rebuild_refreshes_the_table_and_the_map_cue()
    test_palette_flip_without_a_selection_recolours_the_brake_glyphs()
    test_quality_banner_is_informational_and_independent()
    test_provisional_and_degraded_share_one_trust_strip()
    test_hero_readout_leads_with_labelled_delta_to_ideal()
    test_hero_readout_keeps_every_character_when_the_charts_header_is_squeezed()
    test_delta_to_ideal_tooltips_are_honest_not_best_sector()
    test_grip_map_reachable_via_labelled_combo()
    test_opportunities_panel_is_the_coaching_tab_page()
    test_window_fullscreen_toggle_and_menu_text_and_esc()
    test_video_focus_enters_and_restores_cleanly()
    test_transport_fullscreen_button_and_video_dblclick_trigger_focus()
    test_video_focus_disabled_while_comparing()
    test_every_panel_header_has_a_maximize_button_that_toggles_and_reflects_state()
    test_tab_bar_switches_pages_and_names_the_corners_lap()
    test_one_chapter_phrasing_on_the_banner_and_the_transport()
    test_corner_row_click_rings_the_map()
    test_charts_header_yields_control_text_before_the_baseline_naming()
    test_show_stats_maximized_is_a_true_toggle()
    test_stats_corner_row_click_restores_grid_then_rings_map()
    test_splitter_handles_stay_thin_under_the_theme()
    test_gmeter_overlay_stays_pinned_to_its_video_and_stands_down_with_it()
    print("ALL CENTRAL-VIEW REAL-QT TESTS PASSED")


def test_tab_bar_switches_pages_and_names_the_corners_lap():
    """The lap panel's QTabBar is the ONE page switcher: tab index == stack index for all four
    pages, and the Corners tab text always names the lap its rows describe (1-BASED, the
    app-wide display rule — the old mode label leaked the 0-based id)."""
    view, _s, _t0, _t1 = _real_central_view()
    assert view.tab_bar.count() == 4
    assert [view.tab_bar.tabText(i) for i in (0, 2, 3)] == ["Laps", "Stats", "Coaching"]
    for idx in (1, 2, 3, 0):
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


def test_one_chapter_phrasing_on_the_banner_and_the_transport():
    """P4: the chapter banner (above the video) and the transport timecode (below it) are visible
    at the SAME time, so they must speak ONE format. The transport used to compress it to
    "chapter 2/3" against the banner's "chapter 2 of 3", which reads as two different facts; both
    now render through chapters.format_chapter."""
    view, s, _t0, _t1 = _real_central_view()
    dur = s.chapters.total_duration / 3.0
    # A real 3-chapter load: the session carries the map (transport readout) and so does the
    # player pane (which is what makes video.is_multi — the banner's gate — true).
    s.chapters = chapters.ChapterMap(["/tmp/a.MP4", "/tmp/b.MP4", "/tmp/c.MP4"], [dur] * 3)
    view.video.pane._chapters = s.chapters

    view._update_chapter_label(1)                       # 0-based index 1 == the 2nd chapter
    banner = view.chapter_label.text()
    transport = view._transport_readout(dur * 1.5)      # a moment inside the 2nd chapter

    assert "chapter 2 of 3" in banner, banner
    assert "chapter 2 of 3" in transport, transport
    assert "2/3" not in transport, transport
    print("test_one_chapter_phrasing_on_the_banner_and_the_transport OK")


def test_charts_header_yields_control_text_before_the_baseline_naming():
    """The charts header is genuinely cramped at a narrow right column, and it now spends the
    shortfall in the RIGHT order.

    This test used to pin the opposite contract ("the decorative label must yield when cramped"),
    which is how the L6-01 regression shipped: #122 made the section label the bar's first casualty
    while it really was decorative, then #125 made that same label the only surface naming the
    baseline the LOWER CHART draws — a different reference from the hero readout's own. The label
    stopped being decorative; nothing moved it up the yield order, so at every width the app ships
    at the naming lost to two button labels whose meaning is already in their tooltips.

    Now the toggles give up their TEXT first (falling back to their icon, tooltip + accessible name
    intact) and nothing is ever centre-clipped under a floor."""
    view, _s, _t0, _t1 = _real_central_view()
    view.resize(1511, 940)
    view.show()
    _APP.processEvents()

    view._main_splitter.setSizes([580, 931])       # the reporter's layout — header is cramped
    for _ in range(4):
        _APP.processEvents()
    assert view._plots_label.isVisibleTo(view), "the baseline naming must survive a cramped bar"
    assert "Δ" in view._plots_label.text(), view._plots_label.text()
    for w in (view.plots.brake_throttle_btn, view.plots.ideal_btn, view.plots.x_mode_combo):
        assert w.width() >= w.sizeHint().width(), (w.text() if hasattr(w, "text") else w, w.width())
    # The toggles are what gave way, and hovering still names them.
    for btn in (view.plots.brake_throttle_btn, view.plots.ideal_btn):
        assert btn.text() == "", btn.accessibleName()
        assert btn.accessibleName() in btn.toolTip(), btn.toolTip()

    view._main_splitter.setSizes([300, 1211])      # plenty of room again
    for _ in range(4):
        _APP.processEvents()
    assert view._plots_label.isVisibleTo(view), "…and it is still there when there is room"
    assert view.plots.brake_throttle_btn.text() == "Brake/Throttle"
    view.hide()
    print("test_charts_header_yields_control_text_before_the_baseline_naming OK")


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
    t = view.stats_view.corners_table
    assert t.rowCount() > 0, "stadium fixture must yield a corner report"

    t.selectRow(0)
    _APP.processEvents()
    assert view._maximized_panel is None, "row click must restore the grid before ringing"
    print("test_stats_corner_row_click_restores_grid_then_rings_map OK")


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


if __name__ == "__main__":
    _run_all()
