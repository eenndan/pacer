"""Keyboard focus cues on the REAL widget tree (QA batch B18: U9-01, U9-03).

The app has 15 tab stops and four of them used to paint NOTHING when the keyboard arrived: the lap
table (0 changed pixels of 213,725 — the app's primary control), any toggle that happened to be
CHECKED, and both pyqtgraph canvases. Two independent causes, both in the global stylesheet:

  * U9-01 — `QTableView, QTableWidget { border: none; outline: none }` suppressed Qt's own focus
    chrome and put nothing back, and the same held for the QGraphicsView-derived plot canvases;
  * U9-03 — `QPushButton:focus` and `QPushButton:checked` both specified `1px solid C.accent`, so
    focusing a checked toggle changed literally nothing (0 px on 4 of 4 checkable buttons, against
    222-512 px for the same buttons unchecked).

The obvious repair for U9-01 is a trap, and this file exists mostly to keep it out. Appending
`QTableWidget:focus { border: 1px solid <accent> }` to a `border: none` base was measured on the
real 21-lap table: it paints exactly 189 px, ALL of them at x=0, because the horizontal header
owns the top strip and the vertical scrollbar the right one, and a 1px frame has nowhere else to
go. So the assertions here are deliberately not "some pixels changed":

  1. every visible tab stop changes pixels when focused (RGB, both toggle states);
  2. the table's ring paints on all FOUR edges — the shape the sliver fails;
  3. nothing moves — widget geometry, size hint, scroll-area viewport and the style's own contents
     rect are identical focused and unfocused, which is what "reserve the ring in both states"
     buys and the reason the QSS compensates its padding.

Real CentralView over the two-lap stadium synthetic (reuses test_central_view_realqt's fixture),
under the REAL theme. Frames are compared as Format_RGB32 RGB — never sha1(constBits()), whose
scanline padding differs between two pixel-identical grabs. No telemetry file, no media.

Run: QT_QPA_PLATFORM=offscreen python tests/test_focus_cues.py
"""
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage, QKeySequence  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractScrollArea,
    QApplication,
    QComboBox,
    QPushButton,
    QStyle,
    QStyleOptionButton,
    QTableWidget,
    QWidget,
)

_APP = QApplication.instance() or QApplication([])

from test_central_view_realqt import _real_central_view  # noqa: E402

from studio import theme  # noqa: E402
from studio.overlays import PBToast  # noqa: E402

theme.apply_theme(_APP)

_VIEW = None


def _view():
    """ONE real CentralView for the whole file (building it is the expensive part), shown at the
    default window size so every panel is laid out and grabbable — WITH A LIVE PB TOAST ON IT.

    The toast is here because leaving it out is how a HIGH regression shipped green. This fixture
    enumerates tab stops by walking a CentralView, and the PB celebration card is owned by the
    WINDOW and overlaid on top, so its three controls — including "Share your PB →", the primary
    action of the one card in the app that deletes itself after six seconds — were structurally
    invisible to every assertion in this file. When an ID-level rule silently out-specified that
    button's focus ring, all 72 tests stayed green. Three tab stops the app really has were not
    being counted; now they are.

    Raised through the widget's own show_for(), not hand-placed, so the card is in the state the
    app puts it in. Its auto-dismiss is stopped straight afterwards: a 6 s timer would delete the
    fixture partway through the file and turn a real failure into an AttributeError."""
    global _VIEW
    if _VIEW is None:
        v, _s, _t0, _t1 = _real_central_view()
        v.resize(1440, 860)
        v.show()
        _settle(6)
        # With the compare, the card's fullest shape: the primary "Compare with your previous PB →"
        # and "Share your PB →" demoted to a link are both tab stops to measure.
        toast = PBToast("New personal best!", "1:02.418 — 0.317 s faster than your previous best.",
                        on_progress=lambda: None, on_share=lambda: None, parent=v,
                        on_compare=lambda: None)
        toast.show_for(v)
        toast._timer.stop()
        _settle(4)
        _VIEW = v
    return _VIEW


def _settle(n=4):
    for _ in range(n):
        _APP.processEvents()


def _rgb(w):
    """The widget's painted pixels as (h, w, 3) uint8. Format_RGB32 first: a raw grab's scanline
    padding and alpha bytes vary between calls even when every visible pixel is identical."""
    img = w.grab().toImage().convertToFormat(QImage.Format_RGB32)
    a = np.frombuffer(bytes(img.constBits()), np.uint8).reshape(img.height(), img.width(), 4)
    return a[..., :3]


def _park(target):
    """A focusable widget that is NOT `target` and not related to it, to hold focus for the
    'before' grab (setFocus on nothing would leave the previous stop lit)."""
    return next(x for x in _stops()
                if x is not target and not x.isAncestorOf(target) and not target.isAncestorOf(x))


def _stops():
    """Every visible tab stop in the view — the ring a Tab press walks."""
    v = _view()
    return [x for x in [v] + v.findChildren(QWidget)
            if x.isVisible() and x.isEnabled() and x.focusPolicy() != Qt.NoFocus
            and (isinstance(x, (QPushButton, QComboBox, QTableWidget))
                 or type(x).__name__ in ("PlotWidget", "GraphicsLayoutWidget"))]


def _focus_mask(w):
    """Boolean (h, w) mask of the pixels that change when `w` takes the keyboard."""
    _park(w).setFocus(Qt.TabFocusReason)
    _settle()
    before = _rgb(w)
    w.setFocus(Qt.TabFocusReason)
    _settle()
    assert _APP.focusWidget() is w, f"{_label(w)} did not take focus"
    return (before != _rgb(w)).any(-1)


def _label(w):
    txt = (w.text() if hasattr(w, "text") else "") or w.objectName() or ""
    return f"{type(w).__name__}({txt})"


# ==================================================================== the cue exists
def test_every_tab_stop_paints_a_focus_cue():
    """U9-01. A keyboard user has to be able to see where they are — on EVERY stop, not 11 of 15.
    The four that painted nothing were the lap table, the checked toggle, and the two plot
    canvases; the canvases are QGraphicsViews, which is why the fix reaches them from the QSS."""
    silent = []
    for w in _stops():
        n = int(_focus_mask(w).sum())
        if n == 0:
            silent.append(_label(w))
    assert not silent, f"{len(silent)} of {len(_stops())} tab stops paint no focus cue: {silent}"
    print(f"test_every_tab_stop_paints_a_focus_cue OK ({len(_stops())} stops)")


def test_a_checked_toggle_is_still_visibly_focusable():
    """U9-03. The focus ring must be a cue `:checked` does not already own. While both drew the
    same 1px accent border, a control that was ON was the one class of control a keyboard user
    could not see they had landed on — 0 changed pixels on all four checkable buttons."""
    checkable = [w for w in _stops() if getattr(w, "isCheckable", lambda: False)()]
    assert checkable, "no checkable buttons in the view — this test would pass vacuously"
    for w in checkable:
        was = w.isChecked()
        try:
            for state in (False, True):
                w.setChecked(state)
                _settle()
                n = int(_focus_mask(w).sum())
                assert n > 0, f"{_label(w)} checked={state} paints no focus cue"
        finally:
            w.setChecked(was)
            _settle()
    print(f"test_a_checked_toggle_is_still_visibly_focusable OK ({len(checkable)} toggles)")


def test_the_table_focus_ring_paints_on_all_four_edges():
    """The refuted one-liner, pinned out. `QTableWidget:focus { border: … }` over a `border: none`
    base paints down the covered edges only — the horizontal header owns the top strip and the
    vertical scrollbar the right one, so on the real table it managed 189 px of left edge. Only a
    ring RESERVED in the resting state sits outside both children. So: pixels must change on all
    four edges, and there must be enough of them to be a ring rather than a sliver. (Which edges
    survive depends on the fixture — the top one never does, because every table has a header.)"""
    t = next(w for w in _stops() if isinstance(w, QTableWidget))
    m = _focus_mask(t)
    h, w = m.shape
    edges = {"top": m[0].any(), "bottom": m[-1].any(),
             "left": m[:, 0].any(), "right": m[:, -1].any()}
    assert all(edges.values()), f"the ring is missing edges: {edges} — the covered-edge sliver"
    floor = 2 * (w + h)  # a 1px perimeter; the real ring is theme.FOCUS_RING_PX thick
    assert int(m.sum()) >= floor, f"only {int(m.sum())} px changed on a {w}x{h} table (floor {floor})"
    print(f"test_the_table_focus_ring_paints_on_all_four_edges OK ({int(m.sum())} px on {w}x{h})")


# ==================================================================== the cue costs nothing
def test_taking_focus_never_moves_or_resizes_a_control():
    """The other half of "reserve it in both states": a ring that only exists while focused eats
    its width out of the content box, so text, rows and plots jump every time the user tabs. Every
    box we can measure has to be identical in both states — the widget's geometry and size hint,
    a scroll area's viewport, and the style's own contents rect (the one the padding compensation
    in the QSS exists to hold still)."""
    for w in _stops():
        p = _park(w)
        p.setFocus(Qt.TabFocusReason)
        _settle()
        before = _boxes(w)
        w.setFocus(Qt.TabFocusReason)
        _settle()
        assert _boxes(w) == before, f"{_label(w)} changed box on focus: {before} -> {_boxes(w)}"
    print("test_taking_focus_never_moves_or_resizes_a_control OK")


# ============================================================ the ring's other half: specificity
#: Classes the focus-ring section rings from a selector that carries NO id — i.e. the ones an ID
#: rule can out-specify. Qt's cascade is CSS's: an ID selector is (1,0,0) and beats any number of
#: attributes and pseudo-classes, so `QPushButton#Name { border: … }` outranks BOTH
#: `QPushButton:focus` (0,1,1) and `QPushButton[variant="primary"]:focus` (0,2,1).
_RINGED_CLASSES = ("QPushButton", "QComboBox", "QTableView", "QTableWidget", "QGraphicsView",
                   "QLineEdit", "QAbstractScrollArea")
#: The properties the ring is DRAWN with and PAID FOR with. A rule that fixes either at ID level
#: takes it away from the ring: `border` deletes the ring outright, `padding` wins over the
#: compensating padding and lets the ring grow the box instead (which the pixel test above forbids).
_RING_PROPS = ("border", "padding")


def _id_rules():
    """(selector, {declared properties}) for every rule in the theme's QSS, by selector part.

    Same parse as tests/test_design_system.py::_qss_blocks and for the same stated reason: anchor
    on nothing, because the regex that consumes the previous rule's closing brace silently reads
    every OTHER rule (that bug shipped in this repo's own contrast guard)."""
    qss = re.sub(r"/\*.*?\*/", "", theme._build_qss(), flags=re.S)
    out = {}
    for sel, body in re.findall(r"([^{}]*)\{([^{}]*)\}", qss, flags=re.S):
        props = {d.split(":", 1)[0].strip().lower() for d in body.split(";") if ":" in d}
        for part in sel.split(","):
            out.setdefault(" ".join(part.split()).strip(), set()).update(props)
    return out


def test_every_id_rule_that_borders_a_control_also_rings_it():
    """The SIXTH time a QSS mechanism has silently failed to reach a widget here — as a rule.

    The pixel tests above catch a missing ring on the stops they can enumerate. They caught nothing
    when PR #182 gave the PB toast's primary action an `objectName` rule with a `border` in it,
    because a window-owned overlay is not in a CentralView, and they would catch nothing about a
    dialog, a menu or a card that has not been built yet either. The mechanism is not "this button
    lost its ring", it is "an ID selector out-specifies the shared one", and that is checkable
    without a widget at all: for every `QClass#Name` rule that fixes a `border` or a `padding` on a
    class the focus-ring section rings from an id-less selector, the SAME id must declare its own
    `:focus`, because nothing lower can reach past it.

    It found `#LoadingCancel` the moment it was written — the load card's Cancel, its ONLY control,
    measured at 0 changed pixels of 186x28 on focus — which is the argument for having it. Every
    entry is either paired or exempt with a reason; the exemption list may only shrink."""
    # (selector, why it needs no :focus of its own) — prose, and this list may only shrink.
    EXEMPT = {}
    rules = _id_rules()
    unringed = []
    for sel, props in sorted(rules.items()):
        m = re.fullmatch(r"(Q\w+)#(\w+)", sel)
        if not m or m.group(1) not in _RINGED_CLASSES:
            continue
        if not any(p == q or p.startswith(q + "-") for p in props for q in _RING_PROPS):
            continue
        if f"{sel}:focus" in rules or sel in EXEMPT:
            continue
        unringed.append(f"{sel} declares {sorted(props & set(_RING_PROPS))} and has no {sel}:focus")
    assert not unringed, (
        "ID-level rules that out-specify the shared focus ring and put nothing back — a keyboard "
        "user lands on these and sees NOTHING change (or sees the control resize under them):\n  "
        + "\n  ".join(unringed))
    assert not (set(EXEMPT) - set(rules)), f"dead exemption(s): {sorted(set(EXEMPT) - set(rules))}"
    paired = sum(1 for s in rules if re.fullmatch(r"Q\w+#\w+:focus", s))
    assert paired >= 4, f"only {paired} id-level :focus rules — the parse stopped seeing them"
    print(f"test_every_id_rule_that_borders_a_control_also_rings_it OK "
          f"({paired} id-level focus rules, {len(EXEMPT)} exempted)")


def _boxes(w):
    b = [w.geometry(), w.sizeHint()]
    if isinstance(w, QAbstractScrollArea):
        b.append(w.viewport().geometry())
    if isinstance(w, QPushButton):
        opt = QStyleOptionButton()
        w.initStyleOption(opt)
        b.append(w.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, opt, w))
    return b


# ------------------------------------- w26: a focused button is pressed by the keyboard, not by Space
_PLAYED: list = []
_WINDOW = None


def _window_with_view():
    """A real StudioWindow (menu bar + the real window shortcuts, Space among them) holding its own
    real CentralView — the seam tests/test_command_palette.py uses, plus a view, because the
    question is what Space does INSIDE the window that binds it. `toggle_playback` is recorded
    rather than run (no media here); patched before `_build_shortcuts` binds it."""
    global _WINDOW
    if _WINDOW is not None:
        return _WINDOW
    import tempfile

    from PySide6.QtWidgets import QMainWindow

    from studio import app as A
    from studio import library, prefs, units
    from studio.export_controller import ExportController
    from studio.library_controller import LibraryController
    tmp = tempfile.mkdtemp(prefix="w26-focus-")
    library._app_support_dir = prefs._app_support_dir = (lambda: tmp)
    orig = A.StudioWindow.toggle_playback
    A.StudioWindow.toggle_playback = lambda self: _PLAYED.append(1)
    try:
        win = A.StudioWindow.__new__(A.StudioWindow)
        QMainWindow.__init__(win)
        win._excluded_visible = True
        win._colorblind = False
        win._speed_unit = units.KMH
        win.exports = ExportController(win, A.STATUS_MS)
        win.library_ctl = LibraryController(win, A.STATUS_MS)
        win._build_menu()
        win._build_shortcuts()
    finally:
        A.StudioWindow.toggle_playback = orig
    v, _s, _t0, _t1 = _real_central_view()
    win.view = v
    win.setCentralWidget(v)
    win.resize(1440, 900)
    win.show()
    win.activateWindow()
    _settle(8)
    _WINDOW = win
    return win


def _buttons_on_screen(win):
    from PySide6.QtWidgets import QAbstractButton
    return [b for b in win.view.findChildren(QAbstractButton)
            if b.isVisible() and b.isEnabled() and b.focusPolicy() != Qt.NoFocus]


def _put_back(win, b):
    """Undo a press so the next one starts from the same state: restore a maximized panel, flip a
    toggle back. (Add sector / Reset sectors stay pressed: harmless on this sidecar-less fixture.)"""
    v = win.view
    if getattr(v, "_maximized_panel", None) is not None:
        v._restore_splitter_sizes()
    elif b.isCheckable():
        b.click()
    _settle(3)


def test_a_keyboard_focused_button_presses_on_space_return_and_enter():
    """On all fifteen tab stops of the window a Tab-focused button could not be pressed: Space was
    play/pause (a window QShortcut outranks the focused button) and Return / Enter did nothing,
    while seven of those controls had no other way in. Now, with the ring on a button, Space,
    Return and keypad Enter press THAT button and play nothing. And every button the keyboard can
    reach on screen must be keyboard-ONLY (Qt.TabFocus) — the property that keeps the mouse half of
    the ruling true (next test); a button added tomorrow with StrongFocus fails here."""
    from PySide6.QtTest import QTest
    win = _window_with_view()
    buttons = _buttons_on_screen(win)
    assert len(buttons) >= 8, [b.text() or b.toolTip()[:20] for b in buttons]
    strong = [b.text() or b.toolTip()[:30] for b in buttons if b.focusPolicy() != Qt.TabFocus]
    assert not strong, f"buttons a click would park the keyboard on (not Qt.TabFocus): {strong}"
    failed = []
    for b in buttons:
        pressed = []
        b.clicked.connect(lambda *_a, _p=pressed: _p.append(1))
        for key, mod in ((Qt.Key_Space, Qt.NoModifier), (Qt.Key_Return, Qt.NoModifier),
                         (Qt.Key_Enter, Qt.KeypadModifier)):
            if not (b.isVisible() and b.isEnabled()):
                break
            b.setFocus(Qt.TabFocusReason)
            _settle(2)
            _PLAYED.clear()
            pressed.clear()
            QTest.keyClick(_APP.focusWidget(), key, mod)
            _settle(3)
            if pressed != [1] or _PLAYED:
                failed.append(f"{_label(b)} {QKeySequence(key).toString()}: pressed {len(pressed)}, "
                              f"played {len(_PLAYED)}")
            if pressed:
                _put_back(win, b)
    assert not failed, failed
    print(f"test_a_keyboard_focused_button_presses_on_space_return_and_enter OK ({len(buttons)} buttons)")


def test_a_mouse_click_leaves_space_as_play_pause():
    """The other half of the ruling: a mouse user's Space is play/pause exactly as before. Each
    toolbar button is CLICKED with the mouse (from a grid that has the keyboard), then Space is
    pressed: it plays, the button is not pressed a second time, and the keyboard never left the
    grid. On main, under the Fusion style the tests run in, every click left the focus (and the
    ring) on the button."""
    from PySide6.QtTest import QTest
    win = _window_with_view()
    v = win.view
    grid = v.table.table
    targets = [v.map.snap_btn, v.map.add_sector_btn, v.map.reset_sectors_btn, v.race_btn,
               v.plots.ideal_btn, v.plots.brake_throttle_btn, v.ideal_readout_btn, v._map_max_btn,
               v._plots_max_btn, v._video_max_btn, v._table_max_btn]
    failed, clicked = [], 0
    for b in targets:
        if not (b.isVisible() and b.isEnabled()):
            continue
        grid.setFocus(Qt.MouseFocusReason)
        _settle(2)
        pressed = []
        b.clicked.connect(lambda *_a, _p=pressed: _p.append(1))
        QTest.mouseClick(b, Qt.LeftButton, Qt.NoModifier, b.rect().center())
        _settle(3)
        clicked += bool(pressed)
        focus = _APP.focusWidget()
        _PLAYED.clear()
        pressed.clear()
        QTest.keyClick(focus or win, Qt.Key_Space)
        _settle(3)
        if focus is b or pressed or _PLAYED != [1]:
            failed.append(f"{_label(b)}: focus on it={focus is b} re-pressed={len(pressed)} "
                          f"played={len(_PLAYED)}")
        _put_back(win, b)
    assert clicked >= 6, f"only {clicked} toolbar buttons could be clicked: the fixture changed"
    assert not failed, failed
    print(f"test_a_mouse_click_leaves_space_as_play_pause OK ({clicked} buttons clicked)")


def test_typing_a_space_in_a_text_field_still_types_it():
    """A focused text field keeps Space as a character (and ⌥← as word movement): the
    button rule is about buttons only."""
    from PySide6.QtTest import QTest
    win = _window_with_view()
    field = win.view.marks_panel.search
    win.view.tab_bar.setCurrentIndex(4)
    _settle(3)
    field.setFocus(Qt.MouseFocusReason)
    _settle(2)
    _PLAYED.clear()
    QTest.keyClicks(field, "a b")
    QTest.keyClick(field, Qt.Key_Left, Qt.AltModifier)
    assert field.text() == "a b" and not _PLAYED, (field.text(), _PLAYED)
    assert field.cursorPosition() == 2, field.cursorPosition()
    field.clear()
    win.view.tab_bar.setCurrentIndex(0)
    _settle(3)
    print("test_typing_a_space_in_a_text_field_still_types_it OK")


def test_lap_race_takes_its_tab_stop_in_visual_order():
    """"Lap race" sits LEFT of the map toolbar's right-aligned run but was built after it, so Tab
    visited it after "Reset sectors". It now comes straight after the map header's button, then the
    run left to right — the order the eye reads the bar in."""
    win = _window_with_view()
    v = win.view
    order = [v._map_max_btn]
    while len(order) < 7:
        order.append(order[-1].nextInFocusChain())
    stops = [w for w in order if w.focusPolicy() != Qt.NoFocus]
    want = [v._map_max_btn, v.race_btn, v.map.rainbow_combo, v.map.snap_btn,
            v.map.add_sector_btn, v.map.reset_sectors_btn]
    assert stops[:6] == want, [_label(w) for w in stops[:6]]
    print("test_lap_race_takes_its_tab_stop_in_visual_order OK")


def _run_all():
    test_every_tab_stop_paints_a_focus_cue()
    test_a_checked_toggle_is_still_visibly_focusable()
    test_the_table_focus_ring_paints_on_all_four_edges()
    test_every_id_rule_that_borders_a_control_also_rings_it()
    test_taking_focus_never_moves_or_resizes_a_control()
    test_a_keyboard_focused_button_presses_on_space_return_and_enter()
    test_a_mouse_click_leaves_space_as_play_pause()
    test_typing_a_space_in_a_text_field_still_types_it()
    test_lap_race_takes_its_tab_stop_in_visual_order()
    print("\nAll focus-cue tests passed.")


if __name__ == "__main__":
    _run_all()
