"""The video filling the screen (F) shows a live readout; the lap ruler wears ★ and ▼.

With the video in focus the charts, the map, the hero Δ and the Laps table all leave the screen,
and the picture was all that was left (discovery B-workflow candidate 4). So while — and only
while — the video fills the screen, a numbers-only readout sits over it, top-left, opposite the
g-meter: the lap and its clock ("LAP 7   0:22.649", the export strip's label), the hero's own Δ
and speed (its text and its level colour, handed over by the hero itself), and during a played
pass "C3 · lap 7 (2.35)". I hides it. And the scrub bar's lap ruler marks the best lap ★ and the
slow laps ▼ — the laps the Laps table's Lap cells mark, read off the table.

These tests pin, on the demo in the real window (PACER_NO_MEDIA):
  * shown in focus only; hidden in the normal layout and in compare; I toggles it (with a line
    on the status bar when the video is not in focus);
  * its content per state: mid-lap, between laps ("No lap"), a pass with Loop, provisional timing
    (the lap line muted), and the Δ run + colour identical to the hero's — level under the step;
  * where it sits: inside the video, clear of the g-meter dial and of the transport;
  * the tick it adds stays a cheap lookup;
  * the ruler's ★ / ▼ are exactly `LapTable.ruler_marks`, ★ withheld on provisional timing, and
    the hover names the lap and its time; cleared in compare;
  * the ? card and ⌘K offer I.
"""
import os
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import prefs, theme  # noqa: E402

theme.apply_theme(_APP)

_DEMO: dict = {}


def _demo_path() -> str:
    if "path" not in _DEMO:
        from studio.dev import make_demo as md
        _DEMO["dir"] = tempfile.TemporaryDirectory(prefix="readout_demo_")
        _DEMO["path"] = os.path.join(_DEMO["dir"].name, "GX019000.MP4")
        md.make(_DEMO["path"], render=False)
    return _DEMO["path"]


def _settle(seconds=0.1):
    end = time.time() + seconds
    while time.time() < end:
        _APP.processEvents()
        time.sleep(0.005)


def _window(size=(1440, 900)):
    from studio.app import StudioWindow
    for k in ("critical", "warning", "information"):
        setattr(QMessageBox, k, staticmethod(lambda *a, **_kw: QMessageBox.Ok))
    prefs.set_corners_view("quickest")
    win = StudioWindow([_demo_path()])
    deadline = time.time() + 60
    while win.view is None and time.time() < deadline:
        _settle(0.02)
    assert win.view is not None, "the demo did not load"
    win.resize(*size)
    win.show()
    _settle(0.2)
    QTest.keyClick(win, Qt.Key_Escape)   # leave the debrief landing
    _settle(0.1)
    return win


def _close(win):
    from studio.video_view import VideoView
    VideoView._readout_wanted = True
    win.close()
    win.deleteLater()
    _APP.processEvents()
    prefs.set_corners_view("lap")


def _at(view, t):
    """What a playing pane reports at `t`, and the tick that applies it (applied_t set first,
    as `tick` does, so nothing that re-reads it mid-apply sees a stale or missing time)."""
    view._playback.latest_t = view._playback.applied_t = t
    view.video.pane.positionChanged.emit(t)
    view._apply_position(t)


def _focus(win):
    win.view.set_video_focus(True)
    _settle(0.2)


def _readout(view):
    return view.video.pane.live_readout


def _global_rect(w):
    return QRect(w.mapToGlobal(QPoint(0, 0)), w.size())


def test_only_while_the_video_fills_the_screen_and_i_hides_it():
    from studio.app import READOUT_OFF, READOUT_ON
    win = _window()
    try:
        view = win.view
        t = sum(win.session.lap_window(6)) / 2
        _at(view, t)
        assert not _readout(view).isVisible(), "the normal layout shows these numbers already"
        QTest.keyClick(win, Qt.Key_I)              # outside focus: the choice, said on the bar
        assert win.statusBar().currentMessage() == READOUT_OFF
        QTest.keyClick(win, Qt.Key_I)
        assert win.statusBar().currentMessage() == READOUT_ON
        _focus(win)
        assert _readout(view).isVisible()
        assert _readout(view).lines().lap.startswith("LAP 7   "), "filled at once, before a tick"
        QTest.keyClick(win, Qt.Key_I)
        assert not _readout(view).isVisible()
        QTest.keyClick(win, Qt.Key_I)
        assert _readout(view).isVisible()
        view.set_video_focus(False)
        _settle()
        assert not _readout(view).isVisible()
        # compare refuses focus, so the readout never shows beside two panes
        view.video.compare_btn.click()
        _settle()
        view.set_video_focus(True)
        _settle()
        assert not _readout(view).isVisible() and not view.video.live_readout_shown()
    finally:
        _close(win)


def test_the_lines_mirror_the_hero_and_the_lap_clock():
    from studio._signal import fmt_time
    win = _window()
    try:
        view, s = win.view, win.session
        _focus(win)
        w = s.lap_window(6)
        t = w[0] + 22.649
        _at(view, t)
        ln = _readout(view).lines()
        assert ln.lap == f"LAP 7   {fmt_time(22.649)}", ln.lap
        head, _sep, tail = view.diff_box.text().partition("     ")
        assert (ln.delta, ln.tail) == (head, tail.strip()), (ln, view.diff_box.text())
        assert ln.delta_colour == view.diff_box.delta_colour()
        assert not ln.lap_dim and ln.pass_ == ""
        # before the first lap: no lap, no Δ, as the hero
        first = min(s.lap_window(i)[0] for i in s.valid_lap_ids())
        _at(view, max(0.5, first - 5.0))
        ln = _readout(view).lines()
        assert s.lap_at_time(max(0.5, first - 5.0)) is None
        assert ln.lap == "No lap" and ln.delta == view.diff_box.text().partition("     ")[0]
    finally:
        _close(win)


def test_the_level_rule_is_the_heros():
    """A Δ the timing step cannot tell from zero prints neutral on the hero — and so here, because
    the readout is handed the hero's own colour after its level rule (no second rule)."""
    from studio.overlay import broadcast
    win = _window()
    try:
        view, s = win.view, win.session
        _focus(win)
        w = s.lap_window(6)
        seen = {"level": 0, "coloured": 0}
        for k in range(1, 40):
            _at(view, w[0] + (w[1] - w[0]) * k / 40)
            ln = _readout(view).lines()
            assert ln.delta_colour == view.diff_box.delta_colour()
            value = view._diff_shown
            if value is not None and broadcast.is_level(value):
                assert ln.delta_colour is None, (ln, value)
                seen["level"] += 1
            elif value is not None:
                assert ln.delta_colour is not None, (ln, value)
                seen["coloured"] += 1
        assert seen["level"] and seen["coloured"], seen
    finally:
        _close(win)


def test_a_pass_says_its_corner_and_lap_and_looping():
    win = _window()
    try:
        view = win.view
        _focus(win)
        q = view.corners_page.quickest
        q.activate(q.row_of(3))
        q.activate(q.row_of(3))                    # lap 7, a level lap
        c0, c1 = view.video.pane.pass_clip()
        _at(view, (c0 + c1) / 2)
        assert _readout(view).lines().pass_ == "C3 · lap 7 (2.35)"
        QTest.keyClick(win, Qt.Key_L)
        _at(view, (c0 + c1) / 2 + 0.1)
        assert _readout(view).lines().pass_ == "C3 · lap 7 (2.35) · looping"
        view.video.seek(5.0)                       # leaving the pass: the line goes
        _at(view, 5.0)
        assert _readout(view).lines().pass_ == ""
    finally:
        _close(win)


def test_provisional_timing_mutes_the_lap_and_withholds_the_ruler_star():
    from studio.session import Session
    win = _window()
    saved = Session.timing_verified
    try:
        view, s = win.view, win.session
        best = s.best_lap_id()
        assert view.table.ruler_marks().get(best) == "★"
        Session.timing_verified = property(lambda self: False)
        view.refresh_timing_trust()
        _focus(win)
        _at(view, sum(s.lap_window(6)) / 2)
        assert _readout(view).lines().lap_dim
        marks = {seg[0]: seg[2] for seg in view.video.slider.segments()}
        assert "★" not in marks.values(), "no best against an arbitrary start line"
        assert all("provisional timing" in seg[3] for seg in view.video.slider.segments())
    finally:
        Session.timing_verified = saved
        _close(win)


def test_it_sits_inside_the_video_clear_of_the_dial_and_the_transport():
    for size in ((1280, 800), (1440, 900), (1920, 1080)):
        win = _window(size)
        try:
            view = win.view
            _focus(win)
            view.video.gmeter_btn.click()
            q = view.corners_page.quickest
            q.activate(q.row_of(3))
            c0, c1 = view.video.pane.pass_clip()
            _at(view, (c0 + c1) / 2)
            _settle(0.2)
            ro, dial = _readout(view), view.video.pane.gmeter
            assert ro.isVisible() and dial.isVisible(), size
            video = _global_rect(view.video.pane.video)
            r = ro.geometry()
            assert video.contains(r), (size, video, r)
            assert not r.intersects(dial.geometry()), (size, r, dial.geometry())
            assert not r.intersects(_global_rect(view.video.transport)), size
            assert not r.intersects(_global_rect(view.video.scrub_row)), size
            assert r.left() - video.left() < video.width() / 4 and r.top() - video.top() < 40
        finally:
            _close(win)


def test_the_readout_keeps_the_tick_a_cheap_lookup():
    """The readout adds two string formats and, when a printed run changed, one small repaint
    — measured over 300 ticks across a lap, readout on vs off, best of three passes."""
    win = _window((1920, 1080))
    try:
        view, s = win.view, win.session
        _focus(win)
        w = s.lap_window(6)
        ts = [w[0] + (w[1] - w[0]) * k / 300 for k in range(300)]

        def run():
            per = []
            for t in ts:
                a = time.perf_counter()
                view._apply_readout(t)
                per.append(time.perf_counter() - a)
            return statistics.median(per)

        on = min(run() for _ in range(3))
        QTest.keyClick(win, Qt.Key_I)
        off = min(run() for _ in range(3))
        print(f"tick median: readout on {on * 1e3:.3f} ms, off {off * 1e3:.3f} ms")
        assert on - off < 0.5e-3, (on, off)
    finally:
        _close(win)


def test_the_ruler_marks_the_laps_the_table_marks():
    win = _window()
    try:
        view, s = win.view, win.session
        slider = view.video.slider
        table_marks = view.table.ruler_marks()
        assert "★" in table_marks.values() and any(m.startswith("▼") for m in table_marks.values())
        segs = slider.segments()
        assert len(segs) == len(s.valid_lap_ids())
        assert {i: m for i, (_a, _b, m, _t) in zip(s.valid_lap_ids(), segs, strict=True) if m} \
            == table_marks
        glyphs = sorted(g for _x, g in slider.mark_plan())
        assert glyphs == sorted(m.split()[0] for m in table_marks.values()), glyphs
        # the hover names the lap under the pointer, its time and its mark
        from studio._signal import fmt_time, lap_label
        best = s.best_lap_id()
        a, b, _m, tip = next(seg for seg in segs if seg[2] == "★")
        x = (slider._x_for(a) + slider._x_for(b)) // 2
        assert slider.segment_at(x)[3] == tip
        assert tip == f"Lap {lap_label(best)} · {fmt_time(s.lap_time(best))} · ★ session best"
        # a slow lap's hover: "▼ slow lap", then where its time went in the cue cell's own words
        from studio import story
        slow = next(i for i, m in table_marks.items() if m.startswith("▼"))
        note = next(n for n in s.slow_lap_notes() if n.lap_id == slow)
        stip = next(seg[3] for i, seg in zip(s.valid_lap_ids(), segs, strict=True) if i == slow)
        assert stip == (f"Lap {lap_label(slow)} · {fmt_time(s.lap_time(slow))} · ▼ slow lap\n"
                        f"{story.row_text(note)}"), stip
        # compare: one lap on the bar, no session ruler
        view.video.compare_btn.click()
        _settle()
        assert slider.segments() == [] and slider.mark_plan() == []
    finally:
        _close(win)


def _value_with_handle_at(slider, x):
    """The slider value whose handle centre sits nearest pixel `x`."""
    lo, hi = slider.minimum(), slider.maximum()
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if slider._x_for(mid) < x:
            lo = mid
        else:
            hi = mid
    return hi


def test_a_mark_is_never_painted_over_the_handle():
    """Review P2: only the glyph's centre was tested against the handle, so the ★ drew over the
    handle's edge with the handle 12-14 px away. Now any mark whose plate touches the handle is
    left out — at offsets 0, ±8 and ±14 px — and comes back once the handle has moved off."""
    win = _window((1280, 800))
    try:
        slider = win.view.video.slider
        star_x = next(x for x, g in slider.mark_plan() if g == "★")
        for k in (-14, -8, 0, 8, 14, 60):
            slider.setValue(_value_with_handle_at(slider, star_x + k))
            _APP.processEvents()
            handle = slider._handle_rect()
            drawn = [x for x, g in slider.painted_marks() if g == "★"]
            if abs(k) <= 14:
                assert drawn == [], (k, handle, star_x)
            else:
                assert drawn == [star_x], k
            for x, g in slider.painted_marks():
                r = slider._mark_rect(x, g)
                assert r.right() < handle.left() or r.left() > handle.right() + 1, (k, x, g)
    finally:
        _close(win)


def test_a_slow_lap_narrower_than_its_glyph_still_shows_as_a_tick():
    """The 0.17.0 QA: on 30-40-lap sessions docked at 1280x800 / 1440x900 a lap is 10-13 px, the ▼
    needs more, and the slow lap the Laps table marks had no presence on the bar at all. Now it is
    a reduced mark — a tick in the ▼'s ink on the same plate — under the same handle rule, within
    the bar's own height; the ★ and a wide lap's ▼ are unchanged."""
    from PySide6.QtGui import QColor

    from studio import theme
    from studio.video_view import RULER_SLOW_TICK, ruler_mark_colour
    for size in ((1280, 800), (1440, 900)):
        win = _window(size)
        try:
            slider = win.view.video.slider
            lo, hi = slider.minimum(), slider.maximum()
            n = 40
            edges = [lo + (hi - lo) * k // n for k in range(n + 1)]
            segs = [(edges[k], edges[k + 1], "", f"Lap {k + 1}") for k in range(n)]
            segs[3] = (*segs[3][:2], "★", "Lap 4 · ★")
            segs[30] = (*segs[30][:2], "▼ C5", "Lap 31 · ▼ slow lap")
            slider.set_lap_segments(segs)
            slider.setValue(lo)
            _APP.processEvents()
            width = slider._x_for(edges[31]) - slider._x_for(edges[30])
            assert width < 14, (size, width)     # narrower than the ▼'s plate needs
            plan = dict((g, x) for x, g in slider.mark_plan())
            assert set(plan) == {"★", RULER_SLOW_TICK}, (size, plan)
            assert slider.painted_marks() == slider.mark_plan(), "the handle is at the start"
            x = plan[RULER_SLOW_TICK]
            r = slider._mark_rect(x, RULER_SLOW_TICK)
            assert 0 <= r.top() and r.bottom() <= slider.height(), (r, slider.height())
            assert r.width() <= width, "the plate stays inside its lap"
            # the hover is the lap's own, unchanged
            assert slider.segment_at(x)[3] == "Lap 31 · ▼ slow lap"
            # it is painted: the tick's ink at its centre
            img = slider.grab().toImage()
            dpr = img.devicePixelRatio()
            cy = slider._groove_rect().center().y()
            ink = QColor(img.pixel(round(x * dpr), round(cy * dpr))).name()
            assert ink.lower() == QColor(ruler_mark_colour(RULER_SLOW_TICK)).name().lower(), ink
            assert ruler_mark_colour(RULER_SLOW_TICK) == ruler_mark_colour("▼") == theme.C.text
            # the handle rule: on the slow lap, the tick is not drawn under or beside it
            slider.setValue(_value_with_handle_at(slider, x))
            _APP.processEvents()
            handle = slider._handle_rect()
            assert RULER_SLOW_TICK not in [g for _x, g in slider.painted_marks()]
            for px, g in slider.painted_marks():
                pr = slider._mark_rect(px, g)
                assert pr.right() < handle.left() or pr.left() > handle.right() + 1, (px, g)
            # a lap wide enough for the glyph keeps the glyph
            wide = [(lo, edges[20], "", "a"), (edges[20], hi, "▼ C2", "b")]
            slider.set_lap_segments(wide)
            assert [g for _x, g in slider.mark_plan()] == ["▼"]
            # a slow lap beside the best lap: the tick never meets the ★'s plate. Sixty laps (8 px):
            # the ★'s 16 px plate reaches into both neighbours, so their ticks are left out, and a
            # slow lap further on keeps its own
            e60 = [lo + (hi - lo) * k // 60 for k in range(61)]
            s60 = [(e60[k], e60[k + 1], "", f"Lap {k + 1}") for k in range(60)]
            s60[10] = (*s60[10][:2], "★", "best")
            for k in (9, 11, 40):
                s60[k] = (*s60[k][:2], "▼ C1", f"slow {k}")
            slider.set_lap_segments(s60)
            plan = slider.mark_plan()
            star = next(slider._mark_rect(x, g) for x, g in plan if g == "★")
            ticks = [x for x, g in plan if g == RULER_SLOW_TICK]
            assert len(ticks) == 1, (size, plan)
            assert slider.segment_at(ticks[0])[3] == "slow 40"
            assert not slider._mark_rect(ticks[0], RULER_SLOW_TICK).intersects(
                star.adjusted(-1, 0, 1, 0))
            # 80 and 120 laps (review of #629): a tick's plate stays inside its own lap and a pixel
            # clear of the next one's, so two slow laps side by side never merge; a lap narrower
            # than the plate + 2 px gets none
            for n in (80, 120):
                edges = [lo + (hi - lo) * k // n for k in range(n + 1)]
                segs = [(edges[k], edges[k + 1], "▼ C3" if k % 2 else "", f"Lap {k + 1}")
                        for k in range(n)]
                slider.set_lap_segments(segs)
                spans = {(slider._x_for(a) + slider._x_for(b)) // 2:
                         (slider._x_for(a), slider._x_for(b)) for a, b, m, _t in segs if m}
                plates = []
                for x, g in slider.mark_plan():
                    assert g == RULER_SLOW_TICK, g
                    r, (xa, xb) = slider._mark_rect(x, g), spans[x]
                    assert xa <= r.left() and r.right() <= xb, (n, size, r, xa, xb)
                    plates.append(r)
                for r1, r2 in zip(plates, plates[1:], strict=False):
                    assert r2.left() - r1.right() >= 1, (n, size, r1, r2)
                narrow = min(xb - xa for xa, xb in spans.values())
                if n == 120:
                    assert narrow < 7 and slider.mark_plan() == [], (size, narrow)
        finally:
            _close(win)


def test_a_hidden_or_closed_window_takes_both_overlays_with_it():
    """Review P2: the readout and the g-meter are top-level windows; one tick after the app window
    was hidden, the re-pin showed both again over the desktop."""
    from studio.gmeter_overlay import GMeterOverlay
    from studio.player_pane import LiveReadout
    win = _window()
    try:
        view = win.view
        _focus(win)
        view.video.gmeter_btn.click()
        t = sum(win.session.lap_window(6)) / 2
        _at(view, t)
        ro, dial = _readout(view), view.video.pane.gmeter
        assert ro.isVisible() and dial.isVisible()
        win.hide()
        _at(view, t + 0.5)                         # one tick with the window hidden
        _settle()
        assert not ro.isVisible() and not dial.isVisible()
        win.show()
        _settle(0.2)
        _at(view, t + 1.0)
        assert ro.isVisible() and dial.isVisible(), "and back with the window"
        view.video.pane.hide()                     # the pane itself hidden: the readout goes too
        _APP.processEvents()
        assert not ro.isVisible() and not dial.isVisible(), "hidden with the pane, at once"
        _at(view, t + 1.5)                         # ...and the next tick does not bring it back
        assert not ro.isVisible() and not dial.isVisible()
        view.video.pane.show()
        _settle(0.1)
        _at(view, t + 2.0)
        assert ro.isVisible()
        win.setWindowState(win.windowState() | Qt.WindowMinimized)   # minimised: the same
        _APP.processEvents()
        assert win.isMinimized()
        _at(view, t + 2.5)
        assert not ro.isVisible() and not dial.isVisible()
        win.setWindowState(win.windowState() & ~Qt.WindowMinimized)
    finally:
        _close(win)
    _settle(0.2)
    orphans = [w for w in QApplication.topLevelWidgets()
               if isinstance(w, (LiveReadout, GMeterOverlay)) and w.isVisible()]
    assert orphans == [], orphans


def test_the_pass_line_goes_when_the_pass_ends_while_paused():
    win = _window()
    try:
        view = win.view
        _focus(win)
        q = view.corners_page.quickest
        q.activate(q.row_of(3))
        c0, c1 = view.video.pane.pass_clip()
        _at(view, (c0 + c1) / 2)
        assert _readout(view).lines().pass_ == "C3 · lap 13 (2.32)"
        view.video.loop_btn.click()                # Loop flips: no tick needed
        assert _readout(view).lines().pass_.endswith(" · looping")
        view.video.loop_btn.click()                # ...and back off: a plain pass again
        assert _readout(view).lines().pass_ == "C3 · lap 13 (2.32)"
        view.video.pane.end_pass()                 # paused, no position change follows
        assert _readout(view).lines().pass_ == ""
    finally:
        _close(win)


def test_the_pass_line_follows_the_one_pass_subject():
    """The pass line reads `CentralView.pass_subject()` — the one source of truth (play-every-pass):
    a per-lap pass (a Corners row, a Stats cell, the ▼ cue) names that lap's own time, the corner
    walk moves it, the clip's own auto-pause keeps it, and leaving the pass (Esc, a lap pick)
    takes it away."""
    win = _window()
    try:
        view, s = win.view, win.session
        _focus(win)
        corners = s.corners
        idx = next(i for i, c in enumerate(corners.corner_list()) if c.cid == 5)
        took = corners.lap_corner_stats(5)[idx].time            # lap 6's own C5 time
        assert view.play_lap_pass(5, 5)
        c0, c1 = view.video.pane.pass_clip()
        _at(view, (c0 + c1) / 2)
        assert _readout(view).lines().pass_ == f"C5 · lap 6 ({took:.2f})"
        assert view.pass_subject()[:2] == (5, 5)
        view.video.pane._on_position(int(c1 * 1000))           # the clip's own end: auto-pause
        _at(view, c1)
        assert view.pass_subject() is not None
        assert _readout(view).lines().pass_ == f"C5 · lap 6 ({took:.2f})", "kept after the pause"
        assert view.step_corner_pass(+1)                         # ⌥→: C6 on the same lap
        c0, c1 = view.video.pane.pass_clip()
        _at(view, (c0 + c1) / 2)
        assert _readout(view).lines().pass_.startswith("C6 · lap 6 ("), _readout(view).lines()
        assert view.escape_pass()                                # Esc on a zoomed pass leaves it
        _at(view, (c0 + c1) / 2)
        assert view.pass_subject() is None and _readout(view).lines().pass_ == ""
        assert view.play_lap_pass(5, 5)                          # ...and a lap pick leaves it
        view.table.laps_selected.emit([3])
        _settle()
        _at(view, sum(s.lap_window(3)) / 2)
        assert view.pass_subject() is None and _readout(view).lines().pass_ == ""
    finally:
        _close(win)


def test_a_degraded_clock_mutes_the_lap_clock_as_the_table_mutes_lap_times():
    from studio import lap_table
    win = _window()
    try:
        view, s = win.view, win.session
        _focus(win)
        _at(view, sum(s.lap_window(6)) / 2)
        assert not _readout(view).lines().lap_dim and not lap_table.durations_muted(s)
        quality = s.timing_quality
        saved = type(quality).degraded
        try:
            type(quality).degraded = property(lambda self: True)
            assert lap_table.durations_muted(s)
            _at(view, sum(s.lap_window(6)) / 2 + 0.3)
            assert _readout(view).lines().lap_dim
        finally:
            type(quality).degraded = saved
    finally:
        _close(win)


def test_the_readout_repaints_only_when_a_printed_run_changes():
    win = _window()
    try:
        view = win.view
        _focus(win)
        ro = _readout(view)
        t = sum(win.session.lap_window(6)) / 2
        _at(view, t)
        _settle(0.2)

        class _Paints(QObject):
            n = 0

            def eventFilter(self, _obj, ev):  # noqa: N802 (Qt override)
                if ev.type() == QEvent.Paint:
                    self.n += 1
                return False

        paints = _Paints()
        ro.installEventFilter(paints)
        try:
            for _ in range(5):
                _at(view, t)                       # the same moment, five ticks
                _settle(0.03)
            assert paints.n == 0, paints.n
            _at(view, t + 0.25)                    # the clock prints a new value: one repaint
            _settle(0.1)
            assert paints.n == 1, paints.n
        finally:
            ro.removeEventFilter(paints)
    finally:
        _close(win)


def test_the_marks_cost_the_video_no_height():
    """Glyphs on the groove: the slider keeps its height and the video its pixels (main: 498×230
    at 1280×800, 515×277 at 1440×900; this build measured 498×231 / 515×277).

    482 wide at 1280×800 since cool/coaching-ranks-past-the-step: the video shares the left column,
    which opens at the docked lap panel's minimum width, and the demo's shorter Coaching headline
    ("0.36 s in your worst corner") made that 468 px; Quickest passes' words (`QuickestPassTable.
    _hold_words_width`) now hold it at 482. Not the marks: the height is the claim here."""
    for size, floor in (((1280, 800), (482, 230)), ((1440, 900), (515, 277))):
        win = _window(size)
        try:
            video = win.view.video
            # main's numbers, typed: the slider is HIT_MIN + SPACE_XXS = 26, the scrub row 48
            assert video.slider.height() == theme.HIT_MIN + theme.SPACE_XXS == 26, \
                video.slider.height()
            assert video.scrub_row.height() == 48, video.scrub_row.height()
            v = video.pane.video
            assert v.width() >= floor[0] and v.height() >= floor[1], (size, v.size())
        finally:
            _close(win)


def test_the_card_and_cmd_k_offer_i():
    from studio import command_palette, help_dialog
    rows = [c for c in help_dialog.COMMANDS if c.run == "toggle_live_readout"]
    assert len(rows) == 1 and rows[0].key == "I"
    win = _window()
    try:
        entry = next(e for e in command_palette.entries(win) if e.title == rows[0].title)
        assert entry.key == "I" and entry.enabled
        _focus(win)
        assert _readout(win.view).isVisible()
        entry.run()
        assert not _readout(win.view).isVisible()
    finally:
        _close(win)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\nALL {len(tests)} FULLSCREEN-READOUT TESTS PASSED")
