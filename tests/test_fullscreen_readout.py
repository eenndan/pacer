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

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
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
    """What a playing pane reports at `t`, and the tick that applies it."""
    view._playback.applied_t = None
    view.video.pane.positionChanged.emit(t)
    view._apply_position(t)
    view._playback.applied_t = t


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
        # compare: one lap on the bar, no session ruler
        view.video.compare_btn.click()
        _settle()
        assert slider.segments() == [] and slider.mark_plan() == []
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
