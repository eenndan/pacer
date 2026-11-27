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


def test_every_row_select_table_gives_left_and_right_to_the_window():
    """← / → (⇧ ±5 s) step the video from the WINDOW's keyPressEvent, which only sees a key the
    focused widget left alone. A table that selects whole rows has no use for them — its cell
    cursor is invisible — yet the lap table and the Coaching table each swallowed them to walk it
    (w26 discovery: three → presses before a step). Every focusable row-select table the view
    builds, on every page, must let both keys (and their ⇧ forms) travel up untouched — a table
    added tomorrow included, which is why this walks the tree rather than naming four tables.
    Real key events through QApplication, so they propagate exactly as a press does."""
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QAbstractItemView

    v = _view()

    class _Top(QObject):
        def __init__(self):
            super().__init__()
            self.got = 0

        def eventFilter(self, obj, event):
            if (obj is v and event.type() == QEvent.KeyPress
                    and event.key() in (Qt.Key_Left, Qt.Key_Right)):
                self.got += 1
            return False

    top = _Top()
    v.installEventFilter(top)
    tables = [t for t in v.findChildren(QAbstractItemView)
              if t.selectionBehavior() == QAbstractItemView.SelectRows
              and t.focusPolicy() != Qt.NoFocus]
    assert len(tables) >= 4, f"only {len(tables)} row-select tables found: the walk is broken"
    kept = []
    try:
        for t in tables:
            for key in (Qt.Key_Left, Qt.Key_Right):
                for mod in (Qt.NoModifier, Qt.ShiftModifier):
                    before = top.got
                    QTest.keyClick(t, key, mod)
                    if top.got != before + 1:
                        kept.append(f"{type(t.parent()).__name__}>{type(t).__name__} "
                                    f"{'Shift+' if mod else ''}{'Right' if key == Qt.Key_Right else 'Left'}")
    finally:
        v.removeEventFilter(top)
    assert not kept, f"row-select tables that keep ←/→ from the window's video step: {kept}"
    print(f"test_every_row_select_table_gives_left_and_right_to_the_window OK ({len(tables)} tables)")
# ------------------------------------- w26: a focused button is pressed by the keyboard, not by Space
_PLAYED: list = []
_WINDOW = None


def _bare_window(view):
    """A real StudioWindow (menu bar + the real window shortcuts, Space among them) around
    `view` — the seam tests/test_command_palette.py uses, plus a view, because the question is
    what Space does INSIDE the window that binds it. `toggle_playback` is recorded rather than run
    (no media here); patched before `_build_shortcuts` binds it."""
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
    win.view = view
    win.setCentralWidget(view)
    win.resize(1440, 900)
    win.show()
    win.activateWindow()
    _settle(8)
    return win


def _window_with_view():
    """The two-lap stadium synthetic's real CentralView in a bare window (`_bare_window`)."""
    global _WINDOW
    if _WINDOW is None:
        v, _s, _t0, _t1 = _real_central_view()
        _WINDOW = _bare_window(v)
    return _WINDOW


_DEMO_WINDOW: dict = {}


def _demo_window():
    """The shipped synthetic demo's real CentralView in a bare window: enough laps for every
    page's buttons — Coaching's per-row Jump / Compare / focus-list cells, Corners' Remove, the
    focus list — that the two-lap stadium never grows."""
    if not _DEMO_WINDOW:
        import tempfile

        from studio.central_view import CentralView
        from studio.dev import make_demo as md
        from studio.session import Session
        tmp = tempfile.TemporaryDirectory(prefix="w26-focus-demo-")
        path = os.path.join(tmp.name, "pacer-synthetic-demo.mp4")
        md.make(path, render=False)
        view = CentralView(Session.load([path]), [path], sidecar_path=None)
        _DEMO_WINDOW.update(tmp=tmp, win=_bare_window(view))
    return _DEMO_WINDOW["win"]


def _tab_to(win, target, limit=160):
    """Walk REAL Tab presses from the map canvas until `target` has the focus — the only road on
    which the keyboard counts as having put it there (`widgets.KeyboardPress`). True if reached."""
    from PySide6.QtTest import QTest
    win.activateWindow()
    win.view.map.widget.setFocus(Qt.MouseFocusReason)
    _settle(2)
    for _ in range(limit):
        if _APP.focusWidget() is target:
            return True
        QTest.keyClick(_APP.focusWidget() or win, Qt.Key_Tab)
    return _APP.focusWidget() is target


def _visible_buttons(win):
    from PySide6.QtWidgets import QAbstractButton
    # An exclusive group's UNCHECKED members are not tab stops — Qt's rule: Tab visits the checked
    # one and the arrows move inside the group (Corners' "Lap N" · "Quickest passes" switch).
    return [b for b in win.view.findChildren(QAbstractButton)
            if b.isVisible() and b.isEnabled() and b.focusPolicy() & Qt.TabFocus
            and not (b.group() is not None and b.group().exclusive() and not b.isChecked())]


class _NoClicks:
    """Record every QAbstractButton.click() instead of running it — the Return / Enter road — so a
    test can press every button on every page without opening a dialog or moving a panel."""

    def __enter__(self):
        from PySide6.QtWidgets import QAbstractButton
        self.got = []
        self._orig = QAbstractButton.click
        QAbstractButton.click = lambda b: self.got.append(b)
        return self

    def __exit__(self, *_exc):
        from PySide6.QtWidgets import QAbstractButton
        QAbstractButton.click = self._orig
        return False


def _space_reaches(b) -> bool:
    """Press (not release) Space with `b` focused: did the key reach the button — is it held down —
    or did the window's play/pause take it? Released with the button's signals blocked, so the
    click Qt makes on release runs no handler (a checked state it flips is put back)."""
    from PySide6.QtTest import QTest
    was = b.isChecked()
    QTest.keyPress(_APP.focusWidget(), Qt.Key_Space)
    down = b.isDown()
    b.blockSignals(True)
    try:
        QTest.keyRelease(_APP.focusWidget(), Qt.Key_Space)
        b.setChecked(was) if b.isCheckable() else None
    finally:
        b.blockSignals(False)
    _settle(2)
    return down


def test_every_button_on_every_page_presses_from_the_keyboard():
    """On all fifteen tab stops of the window a Tab-focused button could not be pressed: Space was
    play/pause (a window QShortcut outranks the focused button) and Return / Enter did nothing,
    while seven of those controls had no other way in. Now a button the user reached with REAL Tab
    presses answers Space, Return and keypad Enter, and plays nothing — every visible button on
    every lap-panel page, whatever its class or focus policy (Coaching's cell buttons and Corners'
    "Remove" are plain StrongFocus QPushButtons), which is why the rule lives in one place."""
    win = _demo_window()
    tb = win.view.tab_bar
    failed, seen, unreachable = [], set(), []
    try:
        for page in range(tb.count()):
            tb.setCurrentIndex(page)
            _settle(4)
            for b in _visible_buttons(win):
                if id(b) in seen or not (b.isVisible() and b.isEnabled()):
                    continue
                seen.add(id(b))
                name = f"{tb.tabText(page)}: {_label(b)}"
                if not _tab_to(win, b):
                    unreachable.append(name)
                    continue
                _PLAYED.clear()
                if not _space_reaches(b) or _PLAYED:
                    failed.append(f"{name} Space: reached={b.isDown()} played={len(_PLAYED)}")
                for key, mod in ((Qt.Key_Return, Qt.NoModifier), (Qt.Key_Enter, Qt.KeypadModifier)):
                    with _NoClicks() as clicks:
                        from PySide6.QtTest import QTest
                        QTest.keyClick(_APP.focusWidget(), key, mod)
                    if clicks.got != [b] or _PLAYED:
                        failed.append(f"{name} {QKeySequence(key).toString()}: clicks "
                                      f"{len(clicks.got)} played {len(_PLAYED)}")
    finally:
        tb.setCurrentIndex(0)
        _settle(3)
    assert len(seen) >= 12, f"only {len(seen)} buttons found across the pages: the walk broke"
    assert not unreachable, f"buttons Tab never reaches: {unreachable}"
    assert not failed, failed
    print(f"test_every_button_on_every_page_presses_from_the_keyboard OK ({len(seen)} buttons, "
          f"{tb.count()} pages)")


def test_space_never_presses_a_button_qt_moved_the_focus_to():
    """F1 of the #739 review: Space pressed a button the user never focused. A mouse user with the
    Laps grid focused clicked the "Corners" tab; hiding the grid made Qt move the focus to the next
    tab stop — "Lap 13 corners" — with a TAB focus reason, and Space pressed it instead of playing.
    Only a button the user TABBED to may take Space. Here, after every way Qt moves the focus by
    itself, Space plays and presses nothing: a click on each lap-panel tab, a maximize and its Esc
    restore, a dialog closing, and the keyboard's own button being disabled or hidden."""
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QAbstractButton, QDialog
    win = _window_with_view()
    v = win.view
    grid, tb = v.table.table, v.tab_bar
    log = []

    def space(what):
        fw = _APP.focusWidget()
        _PLAYED.clear()
        with _NoClicks():
            QTest.keyPress(fw or win, Qt.Key_Space)
            down = isinstance(fw, QAbstractButton) and fw.isDown()
            if down:
                fw.blockSignals(True)
            QTest.keyRelease(fw or win, Qt.Key_Space)
            if down:
                fw.blockSignals(False)
        _settle(2)
        if down or _PLAYED != [1]:
            log.append(f"{what}: focus={_label(fw) if fw else None} pressed={down} "
                       f"played={len(_PLAYED)}")

    try:
        # The exact repro, on every tab, in both fixtures: the demo is the one with a Coaching page
        # full enough to hold the focus-list ActionChip Qt moves the focus to.
        for w_ in (_demo_window(), win):
            w_.activateWindow()
            g_, t_ = w_.view.table.table, w_.view.tab_bar
            for i in range(t_.count()):
                t_.setCurrentIndex(0)
                _settle(3)
                g_.setFocus(Qt.MouseFocusReason)
                _settle(2)
                QTest.mouseClick(t_, Qt.LeftButton, Qt.NoModifier, t_.tabRect(i).center())
                _settle(4)
                space(f"grid focused, click tab {t_.tabText(i)!r}")
            t_.setCurrentIndex(0)
            _settle(3)
        win.activateWindow()
        tb.setCurrentIndex(0)
        _settle(3)
        grid.setFocus(Qt.MouseFocusReason)                 # maximize by mouse, then Esc
        QTest.mouseClick(v._table_max_btn, Qt.LeftButton, Qt.NoModifier,
                         v._table_max_btn.rect().center())
        _settle(4)
        space("lap panel maximized by mouse")
        QTest.keyClick(_APP.focusWidget() or win, Qt.Key_Escape)
        _settle(4)
        space("then Esc restored it")
        assert _tab_to(win, v.map.snap_btn)                # a dialog opens and closes
        d = QDialog(win)
        d.show()
        _settle(3)
        d.close()
        win.activateWindow()             # what the window system does when a dialog closes
        _settle(4)
        space("a dialog closed over a Tab-focused button")
        assert _tab_to(win, v.map.snap_btn)                # the keyboard's button disabled
        v.map.snap_btn.setEnabled(False)
        _settle(3)
        space("the Tab-focused button was disabled")
        v.map.snap_btn.setEnabled(True)
        assert _tab_to(win, v.map.snap_btn)                # ...or hidden
        v.map.snap_btn.hide()
        _settle(3)
        space("the Tab-focused button was hidden")
        v.map.snap_btn.show()
        _settle(3)
        # An ActionChip claimed Space whenever it had the focus, however it got it: in the full
        # app, clicking the Coaching tab moved a mouse user's focus onto the focus-list chip and
        # Space opened it. Given the focus by code (as Qt does), it now leaves Space to play/pause.
        from studio.widgets import ActionChip
        chip_ = ActionChip("Focus list: C1", parent=v)
        chip_.setGeometry(10, 10, 120, 24)
        chip_.show()
        _settle(3)
        chip_.setFocus(Qt.TabFocusReason)
        _settle(2)
        space("an ActionChip Qt gave the focus to")
        chip_.hide()
        chip_.deleteLater()
        _settle(2)
    finally:
        tb.setCurrentIndex(0)
        _settle(3)
    assert not log, log
    print("test_space_never_presses_a_button_qt_moved_the_focus_to OK")


def test_a_mouse_press_anywhere_forgets_the_tabbed_button():
    """N1 of the #739 re-review: Tab onto Brake/Throttle, click the middle of the map or the
    charts, press Space — the button was pressed and nothing played. Those surfaces take no focus,
    so no focus change ever told KeyboardPress the user had moved on. Now any mouse press in the
    window forgets the Tab-reached button; and when the press lands where no widget takes the
    focus, the button gives its focus (and ring) up too, so what is drawn and what Space does
    agree. A click on the Tab-reached button itself is just a click, and forgets it as well."""
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QAbstractButton
    win = _window_with_view()
    v = win.view
    b = v.plots.brake_throttle_btn
    targets = [("map", v.map), ("charts", v.plots), ("video", v.video), ("scrubber", v.video.slider),
               ("hero readout", v.diff_box), ("map header", v._map_header),
               ("lap panel header", v._table_header), ("the button itself", b)]
    log = []
    # ...and a no-focus surface once more THROUGH THE WINDOW: a real click reaches the QWindow first and is
    # routed to the widget under it, which is the delivery the app sees (QTest on a widget skips it).
    targets.append(("hero readout, via the window", v.diff_box))
    for what, target in targets:
        assert _tab_to(win, b), "Tab never reaches Brake/Throttle"
        was = b.isChecked()
        if what.endswith("via the window"):
            QTest.mouseClick(win.windowHandle(), Qt.LeftButton, Qt.NoModifier,
                             target.mapTo(win, target.rect().center()))
        else:
            QTest.mouseClick(target, Qt.LeftButton, Qt.NoModifier, target.rect().center())
        _settle(3)
        if b.isChecked() != was:
            b.click()                          # the click on the button toggled it: put it back
            _settle(2)
        fw = _APP.focusWidget()
        _PLAYED.clear()
        QTest.keyPress(fw or win, Qt.Key_Space)
        down = isinstance(fw, QAbstractButton) and fw.isDown()
        if down:
            fw.blockSignals(True)
        QTest.keyRelease(fw or win, Qt.Key_Space)
        if down:
            fw.blockSignals(False)
        _settle(2)
        ring_left = fw is b and target is not b   # the ring stayed on a button Space no longer presses
        if down or _PLAYED != [1] or ring_left:
            log.append(f"click {what}: focus={_label(fw) if fw else None} pressed={down} "
                       f"played={len(_PLAYED)}")
    assert not log, log
    print(f"test_a_mouse_press_anywhere_forgets_the_tabbed_button OK ({len(targets)} targets)")


def test_no_table_keeps_tab_and_every_text_field_tabs_on_to_a_button():
    """N2 of the #739 re-review: Tab out of the Marks search went into the marks table and stayed
    there — Qt's cell-wise Tab navigation is on by default, and the Coaching, Corners and Marks
    grids all kept every Tab press, so the controls after them (and every button reached only by
    Tab) were out of the keyboard's reach. On every lap-panel page of the demo, one Tab from each
    focusable table must leave it, and Tab from each text field must reach a button."""
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QAbstractButton, QAbstractItemView, QLineEdit, QPlainTextEdit
    win = _demo_window()
    tb = win.view.tab_bar
    traps, dead_ends, tables, fields = [], [], 0, 0
    try:
        for page in range(tb.count()):
            tb.setCurrentIndex(page)
            _settle(4)
            for t in win.view.findChildren(QAbstractItemView):
                if not (t.isVisible() and t.focusPolicy() & Qt.TabFocus):
                    continue
                tables += 1
                win.activateWindow()
                t.setFocus(Qt.MouseFocusReason)
                QTest.keyClick(t, Qt.Key_Tab)
                if _APP.focusWidget() is t:
                    traps.append(f"{tb.tabText(page)}: {type(t.parentWidget()).__name__}>{_label(t)}")
            for f in win.view.findChildren(QLineEdit) + win.view.findChildren(QPlainTextEdit):
                if not f.isVisible() or f.parentWidget() is None or isinstance(
                        f.parentWidget(), QAbstractItemView):
                    continue
                fields += 1
                win.activateWindow()
                f.setFocus(Qt.MouseFocusReason)
                for _ in range(40):
                    QTest.keyClick(_APP.focusWidget() or win, Qt.Key_Tab)
                    if isinstance(_APP.focusWidget(), QAbstractButton):
                        break
                else:
                    dead_ends.append(f"{tb.tabText(page)}: {_label(f)}")
    finally:
        tb.setCurrentIndex(0)
        _settle(3)
    assert tables >= 4 and fields >= 1, (tables, fields)
    assert not traps, f"tables that keep Tab: {traps}"
    assert not dead_ends, f"text fields Tab never leads on from to a button: {dead_ends}"
    print(f"test_no_table_keeps_tab_and_every_text_field_tabs_on_to_a_button OK ({tables} tables, "
          f"{fields} text fields)")


def test_a_held_return_presses_once():
    """F2 of the #739 review: a held Return pressed a Tab-focused toggle once per auto-repeat (six
    repeats, six clicks). The first press clicks; the repeats are swallowed."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    win = _window_with_view()
    b = win.view.plots.brake_throttle_btn
    assert _tab_to(win, b)
    with _NoClicks() as clicks:
        for i in range(6):
            _APP.sendEvent(b, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier, "\r", i > 0))
        _APP.sendEvent(b, QKeyEvent(QEvent.KeyRelease, Qt.Key_Return, Qt.NoModifier, "\r", False))
    assert clicks.got == [b], f"held Return clicked {len(clicks.got)}x"
    print("test_a_held_return_presses_once OK")


def test_return_on_a_button_in_a_table_cell_fires_it_once():
    """F3 of the #739 review: a Tab-focused button in a table cell (Coaching's "Jump", a
    `setCellWidget` QPushButton) must answer Return ONCE. A QPushButton outside a dialog ignores
    Return, so the key travelled on to the table, whose own Return emits `activated` — two actions
    for one key. The press stops at the button. A row-select table with a cell button is mounted in
    the real window for this: the demo's Coaching rows only reach the table off the debrief."""
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QAbstractItemView, QPushButton, QTableWidget, QTableWidgetItem
    win = _window_with_view()
    table = QTableWidget(2, 2, win.view)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setTabKeyNavigation(False)
    for r in range(2):
        table.setItem(r, 0, QTableWidgetItem(f"C{r + 1}"))
    b = QPushButton("Jump")
    table.setCellWidget(0, 1, b)
    table.setGeometry(20, 20, 300, 120)
    table.setCurrentCell(0, 0)
    table.show()
    table.raise_()
    _settle(4)
    activated = []
    table.activated.connect(lambda *_a: activated.append(1))
    try:
        assert _tab_to(win, b), f"Tab never reaches {_label(b)}"
        with _NoClicks() as clicks:
            QTest.keyClick(_APP.focusWidget(), Qt.Key_Return)
        assert clicks.got == [b] and activated == [], (len(clicks.got), activated)
    finally:
        table.hide()
        table.deleteLater()
        _settle(3)
    print("test_return_on_a_button_in_a_table_cell_fires_it_once OK")


def test_a_mouse_click_leaves_space_as_play_pause():
    """The other half of the ruling: a mouse user's Space is play/pause exactly as before. Each
    toolbar button is CLICKED with the mouse from a grid that has the keyboard, then Space is
    pressed: it plays and the button is not pressed a second time — even where the click left the
    focus on the button (StrongFocus under the offscreen style), because a click is not Tab."""
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
        QTest.keyClick(focus or win, Qt.Key_Return)
        _settle(3)
        if pressed or _PLAYED != [1]:
            failed.append(f"{_label(b)}: re-pressed={len(pressed)} played={len(_PLAYED)}")
        if getattr(v, "_maximized_panel", None) is not None:
            v._restore_splitter_sizes()
        elif b.isCheckable() and b.isChecked() != (b is v.ideal_readout_btn):
            b.click()
        _settle(3)
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
    test_every_row_select_table_gives_left_and_right_to_the_window()
    test_every_tab_stop_paints_a_focus_cue()
    test_a_checked_toggle_is_still_visibly_focusable()
    test_the_table_focus_ring_paints_on_all_four_edges()
    test_every_id_rule_that_borders_a_control_also_rings_it()
    test_taking_focus_never_moves_or_resizes_a_control()
    test_every_button_on_every_page_presses_from_the_keyboard()
    test_space_never_presses_a_button_qt_moved_the_focus_to()
    test_a_mouse_press_anywhere_forgets_the_tabbed_button()
    test_no_table_keeps_tab_and_every_text_field_tabs_on_to_a_button()
    test_a_held_return_presses_once()
    test_return_on_a_button_in_a_table_cell_fires_it_once()
    test_a_mouse_click_leaves_space_as_play_pause()
    test_typing_a_space_in_a_text_field_still_types_it()
    test_lap_race_takes_its_tab_stop_in_visual_order()
    print("\nAll focus-cue tests passed.")


if __name__ == "__main__":
    _run_all()
