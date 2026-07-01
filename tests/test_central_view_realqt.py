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

from studio import chapters, data_quality, render_cache, tracks  # noqa: E402
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
      * lap_rows / dropout / sector splits / consistency stubs (the LapTable + consistency strip);
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

    class _Laps:
        def __init__(self):
            self.sectors = SimpleNamespace(start_line=start_seg, sector_lines=[])

        def laps_count(self):
            return 2

        def start_timestamp(self, lid):
            return windows[lid][0]

        def lap_time(self, lid):
            return windows[lid][1] - windows[lid][0]
    s.laps = _Laps()
    s.track_name = "StadiumLoop"
    # A single-chapter ChapterMap spanning the two laps so VideoView's slider/inert pane build.
    s.chapters = chapters.ChapterMap(["/tmp/stadium.MP4"], [float(t1[-1] - t0[0] + 5.0)])
    s.video_path = None

    view = CentralView(s, ["/tmp/stadium.MP4"], sidecar_path=None, consistency_visible=False)
    return view, s, t0, t1


def _pump(deadline_s: float, until):
    """Pump the real event loop until `until()` is truthy or the deadline elapses (bounded, no raw
    sleep-only wait) — the test_video_view_compare pattern for letting real async/timer work settle."""
    end = time.time() + deadline_s
    while time.time() < end and not until():
        _APP.processEvents()
        time.sleep(0.005)
    return until()


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
    win._consistency_visible = False
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
    """The map's rainbow channel is now a LABELLED dropdown (Off · Speed · Δ · Grip) — every channel
    visible and one click, Grip no longer an undiscoverable 4th blind-cycle step. Selecting the Grip
    entry sets the grip mode (the same render path the old cycle hit); the cycle API still works."""
    view, _s, _t0, _t1 = _real_central_view()
    combo = view.map.rainbow_combo
    # Every channel is a labelled, visible entry (not hidden behind a cycle).
    modes = [combo.itemData(i) for i in range(combo.count())]
    assert modes == ["off", "speed", "delta", "grip"], modes
    grip_idx = modes.index("grip")
    assert "grip" in combo.itemText(grip_idx).lower(), combo.itemText(grip_idx)

    # Selecting Grip drives the map to the grip channel in ONE click. (Clear the current lap first so
    # _apply_rainbow cleanly no-ops on this lap_channels-free synthetic fixture — we're pinning the
    # control wiring / mode selection here, not the render math, which test_rainbow_map covers.)
    view.map._current_lap = None
    combo.setCurrentIndex(grip_idx)
    _APP.processEvents()
    assert view.map._rainbow_mode == "grip", "the labelled Grip entry must select the grip channel"
    # And the legacy cycle path is preserved + keeps the combo in sync (the rainbow tests' driver).
    view.map._cycle_rainbow()  # grip -> off (wraps)
    assert view.map._rainbow_mode == "off"
    assert combo.currentData() == "off", "the cycle must keep the labelled combo in sync"
    print("test_grip_map_reachable_via_labelled_combo OK")


# ============================================================ persistent opportunities panel
def test_opportunities_panel_persistent_and_visible_by_default():
    """The coaching front-door is an ALWAYS-ON in-window panel (not just the modal dialog): the real
    CentralView builds an OpportunitiesPanel under the lap table, visible by default. On the 2-lap
    synthetic (< MIN_LAPS clean laps) it shows the friendly excluded state, not an empty box."""
    from studio.coaching_panel import OpportunitiesPanel

    view, _s, _t0, _t1 = _real_central_view()
    assert isinstance(view.opportunities, OpportunitiesPanel), "the panel must be built into the view"
    assert view.opportunities.isVisibleTo(view), "the opportunities panel is visible by default"
    # 2-lap synthetic -> friendly 'need more laps' excluded page (index 1), never an empty table.
    assert view.opportunities.body.currentIndex() == 1, "too few clean laps -> the friendly state"
    assert view.opportunities.empty_label.text(), "the excluded state must carry a friendly message"
    # The rebuild seam refreshes it without error (the re-segmentation / reference path).
    view.rebuild_derived_views(reselect=True)
    assert view.opportunities.isVisibleTo(view)
    print("test_opportunities_panel_persistent_and_visible_by_default OK")


def _run_all():
    test_real_qtimer_fires_view_tick_through_studiowindow()
    test_position_signal_then_real_tick_applies_once_and_is_stable()
    test_compare_button_click_is_single_source_of_truth_no_reentrancy()
    test_compare_scrub_fans_one_seek_to_each_real_pane_per_tick()
    test_compare_tick_keeps_panes_consistent_no_reentry()
    test_provisional_banner_shows_and_clears_with_trust_state()
    test_quality_banner_is_informational_and_independent()
    test_provisional_and_degraded_share_one_trust_strip()
    test_hero_readout_leads_with_labelled_delta_to_ideal()
    test_delta_to_ideal_tooltips_are_honest_not_best_sector()
    test_grip_map_reachable_via_labelled_combo()
    test_opportunities_panel_persistent_and_visible_by_default()
    print("ALL CENTRAL-VIEW REAL-QT TESTS PASSED")


if __name__ == "__main__":
    _run_all()
