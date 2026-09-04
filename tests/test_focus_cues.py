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
from PySide6.QtGui import QImage  # noqa: E402
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
        toast = PBToast("New personal best!", "1:02.418 — 0.317 s faster than your previous best.",
                        on_progress=lambda: None, on_share=lambda: None, parent=v)
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


def _run_all():
    test_every_tab_stop_paints_a_focus_cue()
    test_a_checked_toggle_is_still_visibly_focusable()
    test_the_table_focus_ring_paints_on_all_four_edges()
    test_every_id_rule_that_borders_a_control_also_rings_it()
    test_taking_focus_never_moves_or_resizes_a_control()
    print("\nAll focus-cue tests passed.")


if __name__ == "__main__":
    _run_all()
