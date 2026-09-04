"""Shared Qt widget primitives — one home for the small behaviours more than one view needs.

Pacer-free and view-agnostic (Qt only, no telemetry / session / app imports), so anything here is
safe to reuse from any dialog or panel without dragging that surface's dependencies along with it.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidgetItem,
    QWidget,
)

from . import theme

#: The numeric sort key a `NumItem` cell compares on (see below). One role for both tables.
NUM_ROLE = Qt.UserRole


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


# ====================================================================== the control vocabulary
def _resolve(colour) -> str | None:
    """A colour that may be a token string, a zero-argument ACCESSOR, or None (the default tint).

    Callable is not a convenience: `theme.best_sector_colour` and friends are a CALL-TIME contract
    (see the palette block in theme.py). A toggle that captured `theme.best_sector_colour()` at
    construction would freeze the hue the colour-blind flip is supposed to move — which is exactly
    the defect tests/test_contrast.py::test_ideal_star_icon_and_ideal_line_share_one_accessor was
    written for, on this very button."""
    return colour() if callable(colour) else colour


def icon_button(glyph: str, *, tooltip: str = "", size: QSize | None = None,
                glyph_px: int | None = None, checkable: bool = False,
                parent=None) -> QPushButton:
    """A SQUARE button whose whole label is one Phosphor glyph, at the app's one icon-button size.

    Two families shipped, and neither was declared anywhere: `central_view._HDR_ICON_BTN` (26x24, a
    15 px glyph, plus a `setFlat(True)` that this theme's QPushButton rule made a no-op) and
    `video_view._ICON_BTN` (32x30, an 18 px glyph). Each was applied by its own three-or-four-line
    block at nine call sites, and the 26x24 one did not even survive contact with the stylesheet:
    a `min-height` on a blanket selector REPLACES a widget's own minimum, so the four ⛶ buttons
    stood at 26x28 — a size no line of code asked for.

    So the size is `theme.ICON_BTN` and the glyph `theme.ICON_PX`, both declared, and the role
    carries the padding that makes a 16 px glyph fit a 28 px box (see the [role="IconButton"] rule).
    `size` exists for a control that has a MEASURED reason to differ, not as a convenience — pass a
    named token, never a literal."""
    btn = QPushButton(parent)
    btn.setProperty("role", "IconButton")
    box = size or theme.ICON_BTN
    px = theme.ICON_PX if glyph_px is None else glyph_px
    btn.setIconSize(QSize(px, px))
    btn.setFixedSize(box)
    btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    btn.setIcon(theme.icon(glyph))
    if checkable:
        btn.setCheckable(True)
    if tooltip:
        btn.setToolTip(tooltip)
    return btn


class ToggleButton(QPushButton):
    """A checkable button whose GLYPH tracks its checked state — the app's one two-state control.

    THE PATTERN THIS REPLACES appeared seven times, in four files, always as the same four lines:
    build a QPushButton, `setCheckable(True)`, connect `toggled`, and re-tint (sometimes re-choose)
    the icon inside the handler. Six of the seven also disagreed about something:

      * HEIGHT. The stylesheet's `min-height` can push a control UP but never hold it down, and an
        icon makes a button taller than its text does — so the three toggles that carry both a
        label and a glyph asked for 31 px against a CTRL_H of 28, and each then landed wherever its
        container happened to leave it (28 in the map header, 30 in the taller charts one). Pinning
        `iconSize` to ICON_PX and the height to CTRL_H here makes that impossible rather than
        merely fixed.
      * THE OFF TINT. Four sites passed `C.text` and two passed `None` — which is `theme.icon`'s
        default of `C.text` plus an ACTIVE state of `C.accent`, i.e. a different hover behaviour,
        chosen by whichever call site was written last.
      * THE ON COLOUR'S BINDING TIME. `ideal_btn` must read `theme.best_sector_colour()` at paint
        time or it freezes out of the colour-blind palette; the others take the amber accent, which
        is palette-independent. `on_colour` therefore accepts a token OR an accessor (see _resolve).

    `glyph_on` is for the two buttons whose glyph is not merely re-tinted but REPLACED when on (⤢
    becomes "contract"), so the state is legible without colour.

    The TOOLTIP is deliberately not managed here. Three of the seven rewrite theirs on toggle and
    two of those consult state this class cannot see (whether compare is available, whether the
    gesture is refused); a `tooltip_on`/`tooltip_off` pair would have covered five sites and lied
    about the other two, so the callers keep their own `toggled` connections for that."""

    def __init__(self, text: str = "", *, glyph: str | None = None, glyph_on: str | None = None,
                 on_colour=None, off_colour=None, tooltip: str = "", checked: bool = False,
                 icon_only: bool = False, parent=None):
        super().__init__(text, parent)
        self._glyph = glyph
        self._glyph_on = glyph_on
        self._on_colour = theme.C.accent if on_colour is None else on_colour
        self._off_colour = off_colour
        self.setCheckable(True)
        if tooltip:
            self.setToolTip(tooltip)
        if icon_only:
            self.setProperty("role", "IconButton")
            self.setFixedSize(theme.ICON_BTN)
            self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        else:
            # One control height, declared. A PanelToolbar re-asserts this for its own children;
            # the video transport is not a toolbar and would otherwise keep the 30/31 px it had.
            self.setFixedHeight(theme.CTRL_H)
        if glyph is not None:
            self.setIconSize(QSize(theme.ICON_PX, theme.ICON_PX))
        # setChecked BEFORE connecting, then one explicit refresh: a `checked=True` default must not
        # look like a user toggle to whatever the caller connects next.
        if checked:
            self.setChecked(True)
        self.toggled.connect(lambda _on: self.refresh_glyph())
        self.refresh_glyph()

    def refresh_glyph(self) -> None:
        """Re-apply the glyph for the CURRENT checked state.

        Public because two callers reflect an authoritative state onto the button with signals
        blocked (video_view's compare/fullscreen buttons, whose checked state is owned by a
        controller) and would otherwise never repaint the icon. Also the hook a palette flip needs
        for an accessor-coloured toggle."""
        if self._glyph is None:
            return
        on = self.isChecked()
        name = self._glyph_on if (on and self._glyph_on) else self._glyph
        self.setIcon(theme.icon(name, color=_resolve(self._on_colour if on else self._off_colour)))


def chip(text: str = "", *, tone: str | None = None, parent=None) -> QLabel:
    """A STATIC chip — a small pill that qualifies the thing beside it, and is not clickable.

    The interactive twin is a `ToggleButton` carrying `role="Chip"`; the two share every pixel of
    the [role="Chip"] rule and differ only in the half a stylesheet cannot decide (focus, hit
    target, the role assistive tech announces). See that rule in theme.py for why the split is the
    point rather than a compromise.

    `tone="warn"` is the amber trust tint."""
    label = QLabel(text, parent)
    label.setProperty("role", "Chip")
    if tone:
        label.setProperty("tone", tone)
    return label


def set_tone(widget: QWidget, tone: str | None) -> None:
    """Change a widget's `tone` property AND make Qt repaint for it.

    A dynamic property that takes part in a selector is only re-evaluated on a style re-polish, so
    the naive `setProperty` alone changes the state and nothing on screen. The one caller that
    flips a tone at runtime is the excluded-lap strip's warning escalation."""
    widget.setProperty("tone", tone or "")
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def is_blank(v) -> bool:
    """A numeric sort key is "blank" when it is absent or NaN (a partial lap with fewer splits,
    a library entry with no theoretical best)."""
    return v is None or (isinstance(v, float) and v != v)


class NumItem(QTableWidgetItem):
    """A table cell that sorts by a numeric key (`NUM_ROLE`), not by its text, with blanks LAST.

    Two byte-similar copies of this shipped — `lap_table._NumItem` and `library_dialog._NumItem` —
    which had already drifted: one treated NaN as blank and the other only None, and the library's
    "None compares as +inf" trick quietly reverses under a descending sort while the lap table's
    explicit `_descending` flip does not. This is the lap table's (stricter) behaviour, and the
    library keeps its own because it never sets the flag: with `_descending` False, blanks sort last
    ascending and first descending, which is exactly what +inf did there.

    `_descending` IS A CLASS ATTRIBUTE and that is why each owner subclasses rather than shares.
    LapTable sets it on its own subclass before every sort; if both tables sat on this one class,
    the lap table's last sort direction would silently decide how the Library dialog orders its
    blanks the next time it is opened."""

    _descending = False  # the active sort direction, set by the owner before each sort

    def __lt__(self, other: QTableWidgetItem) -> bool:  # noqa: D401 (Qt sort hook)
        a = self.data(NUM_ROLE)
        b = other.data(NUM_ROLE)
        a_blank, b_blank = is_blank(a), is_blank(b)
        if a_blank or b_blank:
            if a_blank and b_blank:
                return False  # two blanks: equal, stable order
            # Flip the blank ordering by direction so blanks land LAST after Qt's descending reversal.
            if a_blank:        # self is the blank
                return self._descending
            return not self._descending  # other is the blank, self is real
        return float(a) < float(b)


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
