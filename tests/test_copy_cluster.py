"""The UX-9 copy cluster (board review 2026-09-23, CPO-UX §UX-9), each item measured on the real
window before it was fixed — jailed, SD_30_08 (0065), 1440x900:

  a) the first-session PB card said "First LAP logged here" over "Your first SESSION on this track",
     offered "Share your PB →" for a best that had nothing to beat, and sat over the Laps page's
     excluded strip — 30 px of its 42, across all 295 px of the card, for the card's whole life;
  b) the charts' hero read "Δideal +0.00 s" on every load, because the poster seek parks the
     playhead on the lap's first frame; the lap's answer (+0.69 s to the ideal) was only readable
     off the end of the Δ trace;
  c) the Library PB chart printed the 31 Jul day tick into the "Aug" month label ("31Aug");
  d) the Library dialog spent 12 wrapped lines (168 px, the list down to 5.32 visible rows) on the
     privacy paragraph on every open;
  e) ⌘K "focus" found nothing (pinned in tests/test_command_palette.py; run on a real session here).

After, same window: a) "First session logged here", no share, 0 px over the strip; b) +0.69 s
until the playhead moves; c) no label within SPACE_S of another; d) one line and a "Your data &
privacy…" button, 8.93 rows; e) both focus commands, run.

Run: python tests/test_copy_cluster.py   (~15 s; the pixi env's ffmpeg writes the video trak)
"""
import atexit
import contextlib
import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _qtapp import themed_app  # noqa: E402

from studio import library, theme  # noqa: E402
from studio._signal import fmt_time  # noqa: E402

_APP = themed_app()


def _settle(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        _APP.processEvents()
        time.sleep(0.01)


@contextlib.contextmanager
def _window_on_a_session():
    """A real, jailed StudioWindow on one synthetic recording of the built-in demo circuit, opened
    TWICE: the first open lands on its debrief, the re-open on the ordinary grid — the state every
    later open of a recording arrives in, with the charts and the Laps page on screen."""
    from test_debrief_landing import _fresh_app_support, _open

    from studio.app import StudioWindow
    from studio.dev import make_demo as md
    from studio.dev import synth_gopro as sg
    with tempfile.TemporaryDirectory(prefix="copy_cluster_") as folder, _fresh_app_support():
        rec = sg.generate(os.path.join(folder, "rec"), md.DEMO_SEED, md.DEMO_LAPS, chapters=1,
                          origin=md.DEMO_ORIGIN)
        win = StudioWindow([])
        win.resize(1440, 900)
        win.show()
        try:
            _open(win, rec.paths[0])
            _open(win, rec.paths[0])
            assert not win.view.is_debrief() and win.view.tab_bar.currentIndex() == 0
            yield win
        finally:
            win.close()
            win.deleteLater()
            _APP.processEvents()


def _seek(win, t: float) -> None:
    """Move the playhead the way the view's own seeks do (the seek, then the tick applies it)."""
    v = win.view
    v.video.seek(t)
    v._playback.latest_t = t
    _settle(0.2)


def _card(win, moment):
    win.library_ctl.show_pb_moment(moment)
    deadline = time.time() + 3
    while time.time() < deadline and not (win._pb_toast and win._pb_toast.isVisible()):
        _APP.processEvents()
        time.sleep(0.01)
    card = win._pb_toast
    assert card is not None and card.isVisible(), "no PB card appeared"
    return card


def test_the_cluster_on_a_real_window():
    from PySide6.QtCore import QPoint, QRect

    from studio import command_palette, focus
    with _window_on_a_session() as win:
        s, v = win.session, win.view
        best = s.best_lap_id()
        t0, t1 = s.lap_window(best)

        # ---- b) the hero reads the lap's total until the playhead moves
        at_rest = v.diff_box.text()
        total = s.delta_to_ideal_at(best, t1)
        want = theme.format_ideal_readout(total, v._last_diff_speed, best, v._speed_unit)[0]
        assert at_rest == want and total > 0.05, (at_rest, want, total)
        assert "whole lap's total" in v.diff_box.toolTip(), v.diff_box.toolTip()
        _seek(win, (t0 + t1) / 2)
        moved = v.diff_box.text()
        here = s.delta_to_ideal_at(best, v._playback.applied_t)
        assert moved == theme.format_ideal_readout(here, v._last_diff_speed, best,
                                                   v._speed_unit)[0], (moved, here)
        assert "whole lap's total" not in v.diff_box.toolTip()
        _seek(win, t0 + theme.LAP_SEEK_NUDGE_S)   # back to the poster frame: the rest is over
        assert "whole lap's total" not in v.diff_box.toolTip(), "the rest came back"

        # ---- a) the first-session card: a session, no share; clear of the excluded strip
        first = {"kind": "first", "track": s.track_name, "best": s.lap_time(best)}
        title, body = library.pb_moment_text(first, fmt_time)
        assert title == "First session logged here", title
        assert body == f"{s.track_name} — best lap {fmt_time(s.lap_time(best))}, the time to " \
                       "beat next time.", body
        strip = v.table.excluded_strip()
        strip.setVisible(True)   # this recording has no excluded lap; where a card goes doesn't care
        # Nothing selected, so the strip alone decides: here the ★ row sits right above the strip
        # and its own keep-out would lift the card clear of both, hiding a card that ignores it.
        v.table.table.clearSelection()
        _settle(0.2)
        assert win.library_ctl._pb_card_keepout() == [
            QRect(strip.mapTo(win, QPoint(0, 0)), strip.size())], win.library_ctl._pb_card_keepout()
        card = _card(win, first)
        assert card.title_label.text() == title and card.share_btn is None, \
            "a first session has nothing to beat: no 'Share your PB'"
        box = QRect(card.mapTo(win, QPoint(0, 0)), card.size())
        under = QRect(strip.mapTo(win, QPoint(0, 0)), strip.size())
        assert not box.intersects(under), f"the card {box} covers the excluded strip {under}"
        card.dismiss()
        _settle(0.1)
        beat = dict(first, kind="beat", prior=first["best"] + 0.2, improvement=0.2)
        card = _card(win, beat)
        assert card.share_btn is not None, "a beaten PB still offers its share card"
        card.dismiss()
        _settle(0.1)

        # ---- e) the focus list from the palette, run
        found = {e.title.split(" — ")[0]: e
                 for e in command_palette.matches(command_palette.entries(win), "focus")}
        found["Show focus list"].run()
        _settle(0.2)
        assert v.tab_bar.currentIndex() == 3, "Show focus list did not open the Coaching page"
        panel = v.opportunities
        listed = lambda: [i.cid for i in focus.for_track(focus.load(), s.track_name)]  # noqa: E731

        def add_command():
            return {e.title.split(" — ")[0]: e for e in command_palette.matches(
                command_palette.entries(win), "focus")}["Add selected corner to focus list"]

        # The first open's debrief filled the list with the corners on screen: one of them,
        # selected, is refused in the Add button's own words...
        cid = listed()[0]
        panel.table.selectRow(panel._cids.index(cid))
        _settle(0.1)
        refused = add_command()
        assert not refused.enabled and f"C{cid} is already on your focus list" in refused.reason, \
            refused
        # ...and once it is taken off (its own Remove), the same command puts it back.
        (drop,) = [b for b in panel.focus_block.drop_buttons if b.text() == f"Remove C{cid}"]
        drop.click()
        _settle(0.1)
        assert cid not in listed(), listed()
        panel.table.selectRow(panel._cids.index(cid))
        _settle(0.1)
        add = add_command()
        assert add.enabled, add
        add.run()
        _settle(0.1)
        assert cid in listed(), f"C{cid} was not added by the palette command"
    print("ok cluster: the hero read the lap total until it moved; the first-session card said "
          "session, offered no share and stood clear of the strip; ⌘K focus ran both commands")


# ------------------------------------------------------------------------------ c) the axis
_FOLDER = tempfile.mkdtemp(prefix="copy_cluster_rows_")
atexit.register(shutil.rmtree, _FOLDER, ignore_errors=True)


def _rows():
    """The three present Sandown rows' dates and bests, each over an (empty) file that EXISTS — a
    row whose footage is missing is disabled, and the chart follows the selected row."""
    rows = []
    for fp, date, best in (("GX0064", "2026-07-19", 47.076), ("GX0065", "2026-08-30", 46.912),
                           ("GX0068", "2026-09-19", 46.808)):
        path = os.path.join(_FOLDER, f"GX01{fp[2:]}.MP4")
        open(path, "a").close()
        rows.append({"fingerprint": fp, "stem": "GX01" + fp[2:], "track": "Sandown Park",
                     "date": date, "lap_count": 37, "best": best, "theoretical": best - 0.4,
                     "verified": True, "degraded": False, "dropout": False, "paths": [path]})
    return {"version": library.VERSION, "entries": rows}


def _labels(axis, levels):
    """(level, centre px, half width px, text) for every label an axis would draw."""
    from PySide6.QtGui import QFontMetrics
    lo, hi = axis.range
    px = axis.width() / (hi - lo)
    fm = QFontMetrics(axis.style.get("tickFont") or axis.font())
    out = []
    for level, (spacing, values) in enumerate(levels):
        for value, text in zip(values, axis.tickStrings(values, axis.scale, spacing), strict=True):
            out.append((level, (value - lo) * px, fm.horizontalAdvance(text) / 2, text))
    return out


def _collisions(labels):
    return [(a[3], b[3]) for i, a in enumerate(labels) for b in labels[i + 1:]
            if a[0] != b[0] and abs(a[1] - b[1]) < a[2] + b[2] + theme.SPACE_S]


def test_the_pb_chart_prints_no_date_over_another():
    import pyqtgraph as pg

    from studio.library_dialog import LibraryDialog
    dlg = LibraryDialog(_rows(), lambda paths: None)
    dlg.resize(844, 740)       # the size it opened at on the harness display, measured
    dlg.show()
    _settle(0.2)
    dlg.table.selectRow(0)     # the chart is the selected row's track
    _settle(0.4)
    try:
        axis = dlg.pb_plot.getPlotItem().getAxis("bottom")
        lo, hi = axis.range
        shipped = axis.tickValues(lo, hi, axis.width())
        plain = pg.DateAxisItem.tickValues(axis, lo, hi, axis.width())
        before, after = _collisions(_labels(axis, plain)), _collisions(_labels(axis, shipped))
        # Not a check over nothing: pyqtgraph's own levels DO collide at this size.
        assert before, "the fixture no longer reproduces the overprint — re-measure it"
        assert not after, f"date labels print over each other: {after}"
        months = [t for lvl, _x, _h, t in _labels(axis, shipped) if lvl == 0]
        assert months == ["Aug", "Sep"], months
    finally:
        dlg.close()
        dlg.deleteLater()
    print(f"ok axis: pyqtgraph's levels collided {before}; the chart's collide nowhere")


# --------------------------------------------------------------------------- d) the privacy line
def test_the_library_says_privacy_in_one_line_and_links_the_card():
    from PySide6.QtGui import QFontMetrics

    from studio.library_dialog import PRIVACY_LINK, PRIVACY_NOTE, LibraryDialog
    from studio.widgets import WrapLabel
    opened = []
    dlg = LibraryDialog(_rows(), lambda paths: None, clear_library=lambda: _rows(),
                        show_privacy=lambda: opened.append(True))
    dlg.resize(844, 740)
    dlg.show()
    _settle(0.3)
    try:
        (note,) = [lb for lb in dlg.findChildren(WrapLabel) if lb.text() == PRIVACY_NOTE]
        assert QFontMetrics(note.font()).horizontalAdvance(PRIVACY_NOTE) <= note.width(), \
            "the privacy line wraps"
        btn = dlg.privacy_btn
        assert btn.text().replace("&&", "&") == PRIVACY_LINK == "Your data & privacy…", btn.text()
        assert btn.y() >= note.y() - theme.SPACE_S and btn.y() <= note.y() + note.height()
        btn.click()
        assert opened == [True], "the button does not open the card"
    finally:
        dlg.close()
        dlg.deleteLater()
    bare = LibraryDialog(_rows(), lambda paths: None)
    assert not hasattr(bare, "privacy_btn"), "no card to open, no button"
    bare.deleteLater()
    print("ok privacy: one line, and a button that opens Help ▸ Your data & privacy")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} COPY-CLUSTER TESTS PASSED")
