"""Shared Qt widget primitives — one home for the small behaviours more than one view needs.

Pacer-free and view-agnostic (Qt only, no telemetry / session / app imports), so anything here is
safe to reuse from any dialog or panel without dragging that surface's dependencies along with it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from . import theme


class WrapLabel(QLabel):
    """A word-wrapping QLabel that claims the height its wrapped text actually needs.

    QLabel implements ``heightForWidth``, but a layout's MINIMUM is built from each item's
    ``minimumSizeHint`` — which for a wrapping label is its ONE-LINE height. So a layout hands a
    wrapping label one line's worth of room and every further line paints outside it: sliced
    through the letterforms and over whatever sits below, with the widget's own minimum never
    growing to say so. Re-asserting an explicit ``minimumHeight`` on each resize is what the layout
    does read, and it makes the fix width-independent: a longer string, a bigger system font or a
    translation grows the box instead of clipping it. Converges — ``heightForWidth`` depends only
    on the width, which the extra height does not change.

    This trap has bitten three separate surfaces in this app — the Help shortcut/About/privacy
    cards (#135), the map's refused-reference notice (#170) and the Library dialog's privacy note
    (#172) — which is why the wrapper lives here rather than in any one of them. Use it instead of
    ``QLabel`` + ``setWordWrap(True)`` for any paragraph a layout has to make room for.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        need = self.heightForWidth(self.width())
        if need > 0 and need != self.minimumHeight():
            self.setMinimumHeight(need)


class PanelHeader(QWidget):
    """A panel's IDENTITY row — what this panel IS, what it is showing right now, and ⛶.

    WHY THIS EXISTS. Every panel header in the app already went through one shared builder, and the
    four panels still stood at four different heights (32 / 38 / 43 / 43 before the spatial tokens,
    36 / 36 / 36 / 38 after them), because the builder set MARGINS and never a HEIGHT: the bar was
    as tall as whichever control happened to be tallest — a ``QTabBar`` here, a ``QPushButton``
    there, the 30 px hero ``#DiffBox`` in the charts panel. Four panels, four heights, by accident,
    and nothing in the code you could point at and call wrong. So the height is DECLARED here
    (``theme.PANEL_HDR_H``) and every child is vertically centred inside it: adding a control to a
    header can no longer move the header.

    THREE SLOTS, NOT TWO, and the middle one is the point. The charts panel's hero Δ readout is a
    live VALUE, not a control — it is the number you read while the video plays — so it belongs
    with the identity that names it ("SPEED · Δ TO IDEAL" followed by the Δ it is talking about),
    not in a row of buttons. The lap panel's quality badge is the same kind of thing: a status chip
    about the data under the tabs. Both sit in ``status``, immediately after the identity they
    qualify, which is why this class does not simply take ``*widgets``.

        identity · status* · ‹stretch› · trailing

    Everything that ACTS goes in a `PanelToolbar` below instead. That separation is what let the
    charts panel's four-tier degradation ladder (a measured width budget, an ``eventFilter`` and a
    label that could vanish entirely) be deleted rather than maintained: identity and controls no
    longer share one width budget, so identity never has to lose.

    `identity` is a string (wrapped in a BarLabel) or a widget (the lap panel's `QTabBar`, whose
    tabs ARE its identity). `trailing` is the panel's ⛶ maximize button, right-aligned in all four
    panels by construction rather than by four call sites agreeing.
    """

    #: Height available to a child once the row's own vertical margins are paid.
    CONTENT_H = theme.PANEL_HDR_H - 2 * theme.SPACE_XXS

    def __init__(self, identity, *, status=(), trailing=None, parent=None):
        super().__init__(parent)
        # The existing themed role: surface background + the bottom hairline every panel header has
        # worn since the app had panels.
        self.setProperty("role", "PanelHeader")
        # DECLARED. setFixedHeight also pins the vertical size policy, so a QVBoxLayout can neither
        # stretch this row nor squeeze it.
        self.setFixedHeight(theme.PANEL_HDR_H)
        # SPACE_XXS vertically, not SPACE_XS: the hero readout is 30 px tall in the mono stack at
        # HERO/600 (its font, not a padding we could trim), and a SPACE_XS inset would leave 28 and
        # clip the live number by a pixel top and bottom. SPACE_XXS leaves CONTENT_H = 32, which
        # clears the tallest thing any header carries and still centres a CTRL_H control.
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.SPACE_S, theme.SPACE_XXS, theme.SPACE_S, theme.SPACE_XXS)
        row.setSpacing(theme.SPACE_S)

        if isinstance(identity, str):
            label = QLabel(identity)
            label.setProperty("role", "BarLabel")
            identity = label
        self.identity = identity
        self.status = tuple(status)
        self.trailing = trailing

        for w in (identity, *self.status):
            self._add(row, w)
        row.addStretch(1)
        if trailing is not None:
            self._add(row, trailing)

    @staticmethod
    def _add(row: QHBoxLayout, w: QWidget) -> None:
        """Add one child at its own natural height, vertically centred.

        The alignment is load-bearing, not cosmetic. Without it a QBoxLayout stretches any child
        whose vertical policy allows it to the full row height — which is how the quality badge, a
        CHIP with its own tint, border and radius, shipped as a 28 px slab in a 36 px bar instead of
        the 20 px pill the stylesheet draws. Aligned, every child paints the size it asks for and
        the row's declared height is the only thing that decides how tall the header is."""
        row.addWidget(w, 0, Qt.AlignVCenter)


class PanelToolbar(QWidget):
    """A panel's CONTROL row — the things you click, right-aligned, under the header.

    Only two panels have one (map: line channel · snap · add/reset sector; charts: the hero's
    reference toggle · the two chart toggles · the x-axis mode), and a panel without controls does
    not get an empty strip.

    Every child is pinned to ``theme.CTRL_H`` and to its own sizeHint width:

      * HEIGHT, because "one control height" is otherwise only true by coincidence. Measured under
        the shipped theme, a plain QPushButton and a QComboBox stand at 28 while the three ICONED
        toggles ask for 31 — the icon is taller than the text and the stylesheet's ``min-height``
        can only push a control up, never hold it down. Shipped, `snap_btn` was already being
        silently squeezed from 31 to 28 by the map header's margins while the identical
        `brake_throttle_btn` sat at 30 in the taller charts header. Declaring it makes the three
        agree instead of each landing wherever its container left it.
      * WIDTH (``QSizePolicy.Fixed``), because a QPushButton does not elide — it CENTRE-CLIPS, and
        "Reset sectors" clipped to "et sec" is a destructive action rendered as a non-word. A Fixed
        policy makes the toolbar's honest need part of the panel's minimum width, so the layout
        refuses the squeeze instead of resolving it by clipping. The toolbars need ~509 px (map) and
        ~326 px (charts) against a panel minimum the charts HEADER already sets at ~569, so this
        costs the layout nothing it was not already paying.
    """

    def __init__(self, *controls: QWidget, parent=None):
        super().__init__(parent)
        # Reuses the header's themed role — surface + bottom hairline — so the two rows read as one
        # block of panel chrome with a rule between identity and actions. A `PanelToolbar` role of
        # its own belongs with the rest of the control vocabulary, in the phase that owns theme.py.
        self.setProperty("role", "PanelHeader")
        self.setFixedHeight(theme.TOOLBAR_H)
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.SPACE_S, theme.SPACE_XXS, theme.SPACE_S, theme.SPACE_XXS)
        row.setSpacing(theme.SPACE_S)
        row.addStretch(1)
        self.controls = tuple(controls)
        for c in self.controls:
            c.setFixedHeight(theme.CTRL_H)
            c.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            row.addWidget(c, 0, Qt.AlignVCenter)
