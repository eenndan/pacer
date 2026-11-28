"""A Coaching row's Compare: the big Δ readout counts from the corner's entry (cool/corner-compare-reads-the-corner).

WHY. During "Compare C4" the hero read "Δbest −0.06 s" at the corner's entry and +0.08 s at its exit
while the row that sent the driver there said +0.15 s: the hero was the Δ since the LAP START, the
row the loss INSIDE the corner. The seam under the videos already counted from the corner's entry
(`compare_controller.PassAnchor`); the hero now reads that same number from the same datum
(`CompareController.anchored_delta`) and its speed slot says so ("since C1 entry"), in the place the
at-rest "whole lap" tag uses — so the line keeps the width the hero's floor was sized for.

Pinned on the real window at 1280x800 over the three-habit demo circuit:
  1. through the corner the hero's Δ run is the seam's, to the character, named Δbest, and the tag
     names the corner; at the exit it is within the timing step of the row's Time lost;
  2. the hero box keeps its width while the tag shows;
  3. Esc, and a lap pick, give the lap-start Δ back; a plain compare (C) never shows the tag.

Run: python tests/test_corner_compare_reads_the_corner.py
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _qtapp import themed_app  # noqa: E402
from test_debrief_landing import _fresh_app_support, _open, _settle, _two_recordings  # noqa: E402

_APP = themed_app()


def _window():
    from studio import player_pane
    from studio.app import StudioWindow

    # The inert player (PACER_NO_MEDIA) never reports a position; a real QMediaPlayer emits
    # positionChanged after setPosition, which is what moves the panes, the badges and the hero.
    def _set_position(self, ms):
        self._pos = int(ms)
        self.positionChanged.emit(int(ms))
    player_pane._NullMediaPlayer.setPosition = _set_position
    win = StudioWindow([])
    win.resize(1280, 800)
    win.show()
    return win


def _at(v, s, tp, cid, frac):
    """Both panes `frac` of the way through their own pass of `cid`, then one tick."""
    for side, lap in ((1, tp.best_lap_id), (0, tp.lap_id)):
        entry, exit_ = s.stretch_edges(lap, cid)
        v.video.seek_pane(side, entry + frac * (exit_ - entry))
    _settle(0.1)
    v.tick()
    _settle(0.05)


def _head_tail(v):
    head, _sep, tail = v.diff_box.text().partition("     ")
    return head, tail


def _value(run: str) -> float:
    return float(run.split()[1].replace("−", "-"))


def test_the_hero_reads_the_corner_from_its_entry_as_the_seam_does():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from studio import coaching
    from studio.library import PB_PRECISION_S
    with tempfile.TemporaryDirectory(prefix="corner_hero_") as folder, _fresh_app_support():
        a, _b = _two_recordings(folder)
        win = _window()
        try:
            _open(win, a)
            QTest.keyClick(win, Qt.Key_Escape)
            _settle(0.2)
            v, s = win.view, win.session
            top = next(o for o in v.opportunities._all_rows if o.evidence.ranked)
            tp, lost = s.typical_passes()[top.cid], coaching.lost_text(top)
            width = v.diff_box.width()
            assert v.compare_at_corner(tp, lost)
            label = f"C{top.cid}"
            for frac in (0.25, 0.5, 0.75, 0.99):
                _at(v, s, tp, top.cid, frac)
                head, tail = _head_tail(v)
                seam = v.video.seam._text
                assert tail == f"since {label} entry", v.diff_box.text()
                assert head == "Δbest" + seam[1:], (frac, head, seam)
                assert v.diff_box.width() == width, ("the tag widened the hero",
                                                     v.diff_box.width(), width)
            # At the exit the hero is the loss the row names, within the timing step.
            exit_d = _value(_head_tail(v)[0])
            row = float(lost.split()[0].replace("−", "-"))
            assert abs(exit_d - row) <= PB_PRECISION_S, (exit_d, row)
            QTest.keyClick(win, Qt.Key_Escape)
            _settle(0.2)
            v._playback.latest_t = v._playback.applied_t + 0.5
            v.tick()
            _settle(0.05)
            assert not v._comparing() and "since" not in v.diff_box.text(), v.diff_box.text()
        finally:
            win.close()
            win.deleteLater()
            _APP.processEvents()
    print(f"ok: {label} hero {exit_d:+.2f} s at the exit against the row's {lost}")


def test_a_lap_pick_leaves_it_and_a_plain_compare_never_shows_the_tag():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from studio import coaching
    with tempfile.TemporaryDirectory(prefix="corner_hero_") as folder, _fresh_app_support():
        a, _b = _two_recordings(folder)
        win = _window()
        try:
            _open(win, a)
            QTest.keyClick(win, Qt.Key_Escape)
            _settle(0.2)
            v, s = win.view, win.session
            top = next(o for o in v.opportunities._all_rows if o.evidence.ranked)
            tp = s.typical_passes()[top.cid]
            assert v.compare_at_corner(tp, coaching.lost_text(top))
            _at(v, s, tp, top.cid, 0.5)
            assert "since" in v.diff_box.text()
            other = next(i for i in s.valid_lap_ids() if i not in (tp.lap_id, tp.best_lap_id))
            v._on_laps_selected([other])
            _settle(0.2)
            assert not v._comparing() and "since" not in v.diff_box.text(), v.diff_box.text()
            win.toggle_compare()            # C: a whole-lap compare
            _settle(0.2)
            assert v._comparing() and v.compare.pass_anchor() is None
            v._playback.latest_t = v._playback.applied_t + 1.0
            v.tick()
            _settle(0.05)
            assert "since" not in v.diff_box.text(), v.diff_box.text()
        finally:
            win.close()
            win.deleteLater()
            _APP.processEvents()
    print("ok: a lap pick leaves the corner readout; C's whole-lap compare never shows it")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} CORNER-HERO TESTS PASSED")
