"""The lap panel's data-quality chip OPENS the DATA TRUST row that explains it (N15).

THE CHIP WAS STATIC BY DESIGN, AND THE DESIGN'S REASON WAS SOUND. It was a QLabel "so it adds no
tab stop and announces itself to assistive tech as text rather than as a button that does
nothing". That argument refutes a button with no action. It says nothing about a chip that takes
you to the card explaining it — and the Stats page's DATA TRUST card now has that explanation.

MEASURED BEFORE BUILDING, over the real `Session.load` of every bundled sample and all five of the
owner's recordings (D24 0060 + 0062, Sandown 0059 + 0064, SD 0065):

    state                     chip        DATA TRUST "Timing" row, as shipped
    ------------------------  ----------  -------------------------------------------------------
    media-clock fallback      ESTIMATED   "video clock (estimated) · 0% of moving fixes rejected"
      (9 of the 10 samples)               — UNMARKED, and nothing says what "estimated" costs
    low GPS, true clock       GPS LOW     "GPS9 true clock · 12% of moving fixes rejected"
      (no real recording)                 — UNMARKED, and word for word the clean row's shape
    no GPS trace              GPS LOW (!) "⚠ no GPS fixes survived in this recording — …"
      (karma.mp4)
    clean (all 5 owner recs)  hidden      —

So landing on the card was a dead end in two of the three states — the row the chip would open
read like a clean recording's — and in the third the chip's word ("LOW") contradicted the row it
would open ("no GPS fixes survived"). Each of those is asserted here, on the real widgets, next to
the navigation that makes them matter.

Real `CentralView`s: two over the real `Session.load` of the bundled GPS5-era `hero6.mp4` and
the no-GPS `karma.mp4`, and one over the two-lap stadium synthetic with its timing quality set
to a true clock with 12 % of fixes rejected, which no real recording reaches. Each is hosted in a
`StudioWindow` shell with the app's REAL window shortcuts, because Space is play/pause at window level and a
QShortcut outranks a focused QPushButton: a chip that is a tab stop but cannot be activated from
the keyboard would be exactly the "button that does nothing" the old comment warned about.

Run: QT_QPA_PLATFORM=offscreen PYTHONPATH=bindings/pacer python tests/test_quality_chip_trust.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QAccessible, QColor, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel, QMainWindow  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from test_central_view_realqt import _jail_every_live_app_support_seam, _real_central_view  # noqa: E402

from studio import data_quality, theme  # noqa: E402

theme.register_fonts()
theme.apply_theme(_APP)
_jail_every_live_app_support_seam()   # before any window reads prefs

_SAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "3rdparty", "gpmf-parser", "samples")
_STATS_TAB = 2
_LOW = data_quality.TimingQuality(dropped_fraction=0.12)   # true clock, 12 % of fixes rejected


def _settle(n=6):
    for _ in range(n):
        _APP.processEvents()


def _host(view):
    """A StudioWindow shell around `view` with the app's REAL window-level shortcuts (Space is
    play/pause there) — `__new__` + `_build_shortcuts`, the seam test_command_palette uses, so no
    recording loads and no menu is built. Sized so the Stats page has to scroll."""
    from studio.app import StudioWindow
    win = StudioWindow.__new__(StudioWindow)
    QMainWindow.__init__(win)
    win.view = view
    win._build_shortcuts()
    win.setCentralWidget(view)
    win.resize(1280, 640)
    win.show()
    win.activateWindow()
    _settle()
    return win


def _loaded(sample):
    from studio.central_view import CentralView
    from studio.session import Session
    path = os.path.join(_SAMPLES, sample)
    session = Session.load([path])
    view = CentralView(session, [path], sidecar_path=None)
    return _host(view), view, session


def _synthetic(quality):
    view, session, _t0, _t1 = _real_central_view()
    session._timing_quality = quality
    view.refresh_timing_trust()
    return _host(view), view, session


#: (name, build, the chip's word, what the landing row must say about it)
def _states():
    return (
        ("ESTIMATED (hero6.mp4, media clock)", lambda: _loaded("hero6.mp4"), "ESTIMATED",
         ("estimated", "~0.1%")),
        ("GPS LOW (synthetic, 12 % rejected)", lambda: _synthetic(_LOW), "GPS LOW",
         ("GPS quality low", "12%")),
        ("NO GPS (karma.mp4)", lambda: _loaded("karma.mp4"), "NO GPS",
         ("no GPS fixes",)),
    )


def _close(win):
    win.hide()
    win.deleteLater()
    _settle(2)


def _in_viewport(scroll, widget) -> bool:
    """Is `widget` wholly inside the scroll area's visible viewport right now?"""
    vp = scroll.viewport()
    rect = QRect(widget.mapTo(vp, QPoint(0, 0)), widget.size())
    return vp.rect().contains(rect)


def _trust_heading(stats):
    return next(w for w in stats.findChildren(QLabel) if w.text() == "DATA TRUST")


def _scroll_page_away(view):
    """Open Stats, scroll it to the BOTTOM so DATA TRUST is out of view, and go back to Laps — the
    state a reader who has used the page before is in. Asserted, so the navigation test cannot
    pass by the card merely happening to be on screen already."""
    view.select_lap_tab(_STATS_TAB)
    _settle()
    stats = view.stats_view
    bar = stats._scroll.verticalScrollBar()
    assert bar.maximum() > 0, "the Stats page does not scroll at this size — the test is vacuous"
    bar.setValue(bar.maximum())
    _settle()
    assert not _in_viewport(stats._scroll, _trust_heading(stats)), (
        "DATA TRUST is still in view at the bottom of the page — the test is vacuous")
    view.select_lap_tab(0)
    _settle()


# ======================================================================= what the chip says
def test_every_state_the_chip_shows_names_the_same_fact_as_the_row_it_opens():
    """The chip's WORD and the DATA TRUST row it lands on must agree — "two surfaces showing one
    quantity must agree". Before: karma.mp4 (no GPS fix at all) wore "GPS LOW" while its row said
    "no GPS fixes survived"; the media-clock and low-GPS rows were unmarked and never said what the
    chip was warning about, so on those two the row read like a clean recording's."""
    for name, build, word, must in _states():
        win, view, session = build()
        try:
            assert session.timing_quality.degraded, name
            chip = view.quality_badge
            assert chip.isVisibleTo(view), f"{name}: the chip is hidden on a degraded recording"
            assert chip.text() == word, f"{name}: the chip reads {chip.text()!r}, not {word!r}"
            rows = {t: (v, c) for t, v, c in view.stats_view.trust_card.rows()}
            assert "Timing" in rows, f"{name}: no Timing row on the card: {sorted(rows)}"
            value, caveat = rows["Timing"]
            for phrase in must:
                assert phrase in value, (
                    f"{name}: the Timing row does not explain the {word} chip — {phrase!r} is not "
                    f"in {value!r}")
            assert caveat, (
                f"{name}: the chip warns in amber and the row it opens is unmarked — the card "
                f"cannot read the same as a clean recording's: {value!r}")
        finally:
            _close(win)
    print("test_every_state_the_chip_shows_names_the_same_fact_as_the_row_it_opens OK")


def test_a_clean_recording_keeps_its_timing_row_and_hides_the_chip():
    """Nothing changes where there is no GPS problem: the chip stays hidden and the Timing row
    stays the shipped, unmarked sentence (tests/test_stats.py pins the same string on a stub)."""
    view, session, _t0, _t1 = _real_central_view()
    win = _host(view)
    try:
        assert not session.timing_quality.degraded
        assert not view.quality_badge.isVisibleTo(view)
        row = next(r for r in view.stats_view.trust_card.rows() if r[0] == "Timing")
        assert row == ("Timing", "GPS9 true clock · 0% of moving fixes rejected", False), row
    finally:
        _close(win)
    print("test_a_clean_recording_keeps_its_timing_row_and_hides_the_chip OK")


# ======================================================================= what the chip IS
def _tab_walk(win, start):
    """Every widget the Tab key lands on, starting from `start`, through the real focus chain —
    driven by real key presses, so a stop the chain skips is never counted."""
    start.setFocus(Qt.TabFocusReason)
    _settle(2)
    seen = []
    for _ in range(120):
        QTest.keyClick(_APP.focusWidget() or win, Qt.Key_Tab)
        _settle(1)
        w = _APP.focusWidget()
        if w is None or (seen and w is seen[0]):
            break
        seen.append(w)
    return seen


def test_the_chip_is_a_keyboard_stop_only_while_it_is_shown():
    """A BUTTON, reachable by Tab, announced as a button — and only while it is on screen: on a
    clean recording the lap panel's tab ring is exactly what it was."""
    win, view, session = _synthetic(data_quality.TimingQuality())
    try:
        chip = view.quality_badge
        assert isinstance(chip, QAbstractButton), (
            f"the quality chip is a {type(chip).__name__}: nothing a keyboard or a pointer can "
            f"activate, so it cannot take anyone to the card that explains it")
        clean = _tab_walk(win, view.table.table)
        assert chip not in clean, "a HIDDEN chip is a tab stop"

        session._timing_quality = _LOW
        view.refresh_timing_trust()
        _settle()
        assert chip.isVisible()
        shown = _tab_walk(win, view.table.table)
        assert chip in shown, "the shown chip is not reachable with Tab"
        assert [w for w in shown if w is not chip] == clean, (
            "showing the chip changed the rest of the tab ring, not just added itself")
        iface = QAccessible.queryAccessibleInterface(chip)
        assert iface is not None and iface.role() == QAccessible.Role.Button, (
            f"assistive tech is told this is a {iface.role() if iface else None}, not a button")
        assert chip.toolTip().startswith(session.timing_quality.detail()), chip.toolTip()
        assert "DATA TRUST" in chip.toolTip(), (
            f"the hover never says where a click goes: {chip.toolTip()!r}")
    finally:
        _close(win)
    print("test_the_chip_is_a_keyboard_stop_only_while_it_is_shown OK")


def _rgb(w):
    """The widget's painted pixels as (h, w, 3) uint8 in R, G, B order. Format_RGB32 is stored
    little-endian as B, G, R, A — a colour compared without the swap matches nothing."""
    img = w.grab().toImage().convertToFormat(QImage.Format_RGB32)
    bgra = np.frombuffer(bytes(img.constBits()), np.uint8).reshape(img.height(), img.width(), 4)
    return bgra[..., [2, 1, 0]]


def test_the_shown_chip_keeps_its_amber_pill_and_rings_on_focus_without_moving():
    """The design contracts a new tab stop inherits. The amber tint must still REACH it (a QSS
    rule that silently stops reaching a widget has happened here four times), its focus ring must
    paint (tests/test_focus_cues.py's rule for every stop), and taking focus must not move or
    resize it."""
    win, view, _session = _synthetic(_LOW)
    try:
        chip = view.quality_badge
        assert isinstance(chip, QAbstractButton), f"the chip is a {type(chip).__name__}, not a stop"
        img = _rgb(chip)
        accent = np.array([QColor(theme.C.accent).red(), QColor(theme.C.accent).green(),
                           QColor(theme.C.accent).blue()])
        near = (np.abs(img.astype(int) - accent).sum(-1) <= 30).sum()
        assert near > 0, "the chip lost the amber warn tint — the tone rule does not reach it"
        view.table.table.setFocus(Qt.TabFocusReason)
        _settle()
        before, box = _rgb(chip), (chip.geometry(), chip.sizeHint())
        chip.setFocus(Qt.TabFocusReason)
        _settle()
        assert _APP.focusWidget() is chip
        assert int((before != _rgb(chip)).any(-1).sum()) > 0, "focus paints no cue on the chip"
        assert (chip.geometry(), chip.sizeHint()) == box, "taking focus moved or resized the chip"
    finally:
        _close(win)
    print("test_the_shown_chip_keeps_its_amber_pill_and_rings_on_focus_without_moving OK")


# ======================================================================= what the chip DOES
def _assert_landed(name, view, word):
    from studio.stats_panel import TIMING_TERM
    stats = view.stats_view
    assert view.tab_bar.currentIndex() == _STATS_TAB, f"{name}: {word} did not open the Stats page"
    assert view.table_stack.currentWidget() is stats, name
    scroll = stats._scroll
    assert _in_viewport(scroll, _trust_heading(stats)), (
        f"{name}: the DATA TRUST heading is not in view after {word} "
        f"(scroll {scroll.verticalScrollBar().value()}/{scroll.verticalScrollBar().maximum()})")
    card = stats.trust_card
    assert card.highlighted() == TIMING_TERM, (
        f"{name}: the row that explains {word} is not the one marked: {card.highlighted()!r}")
    term_w, value_w = card.row_widgets(TIMING_TERM)
    assert _in_viewport(scroll, term_w) and _in_viewport(scroll, value_w), (
        f"{name}: the Timing row is not in view")
    lit = [w for pair in card._widgets for w in pair if w.property("highlight") == "true"]
    assert set(map(id, lit)) == {id(term_w), id(value_w)}, (
        f"{name}: exactly the Timing row's two labels carry the highlight, got {len(lit)}")
    assert _APP.focusWidget() is card, (
        f"{name}: keyboard focus stayed on {type(_APP.focusWidget()).__name__}, not the card the "
        f"reader was sent to")


def test_activating_the_chip_opens_data_trust_at_the_row_it_is_about():
    """Pointer AND keyboard, in all three states. Each run starts from a Stats page scrolled to the
    bottom (DATA TRUST out of view) with the Laps tab showing; activating the chip must switch to
    Stats, bring the heading and the Timing row into view, mark that row, and move keyboard focus
    onto the card. The keyboard run presses Space in a window whose Space is play/pause."""
    for name, build, word, _must in _states():
        for how in ("click", "Space", "Return"):
            win, view, _session = build()
            try:
                chip = view.quality_badge
                assert isinstance(chip, QAbstractButton), (
                    f"{name}: the chip is a static {type(chip).__name__} — there is nothing to "
                    f"activate")
                _scroll_page_away(view)
                if how == "click":
                    QTest.mouseClick(chip, Qt.LeftButton)
                else:
                    chip.setFocus(Qt.TabFocusReason)
                    _settle(2)
                    assert _APP.focusWidget() is chip, f"{name}: the chip cannot take focus"
                    QTest.keyClick(chip, Qt.Key_Space if how == "Space" else Qt.Key_Return)
                _settle()
                _assert_landed(f"{name} via {how}", view, word)
            finally:
                _close(win)
    print("test_activating_the_chip_opens_data_trust_at_the_row_it_is_about OK")


def test_leaving_the_stats_page_clears_the_mark():
    """The highlight answers "why was I sent here"; it is not a state of the card. Leaving the page
    takes it away, so the next visit through the tab bar sees the card as it always was."""
    win, view, _session = _synthetic(_LOW)
    try:
        QTest.mouseClick(view.quality_badge, Qt.LeftButton)
        _settle()
        card = view.stats_view.trust_card
        assert card.highlighted() == "Timing"
        view.select_lap_tab(0)
        _settle()
        assert card.highlighted() is None, card.highlighted()
        assert not [w for pair in card._widgets for w in pair if w.property("highlight") == "true"]
        view.select_lap_tab(_STATS_TAB)
        _settle()
        assert card.highlighted() is None, "a tab-bar visit re-lit the chip's mark"
    finally:
        _close(win)
    print("test_leaving_the_stats_page_clears_the_mark OK")


if __name__ == "__main__":
    test_every_state_the_chip_shows_names_the_same_fact_as_the_row_it_opens()
    test_a_clean_recording_keeps_its_timing_row_and_hides_the_chip()
    test_the_chip_is_a_keyboard_stop_only_while_it_is_shown()
    test_the_shown_chip_keeps_its_amber_pill_and_rings_on_focus_without_moving()
    test_activating_the_chip_opens_data_trust_at_the_row_it_is_about()
    test_leaving_the_stats_page_clears_the_mark()
    print("ALL OK")
