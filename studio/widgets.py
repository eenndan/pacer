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
    QVBoxLayout,
    QWidget,
)

from . import theme

#: The numeric sort key a `NumItem` cell compares on (see below). One role for both tables.
NUM_ROLE = Qt.UserRole

#: The "no signal" value — an em-dash, never a fake 0. The app-wide convention the Stats page
#: established and every `Tile` now inherits by default.
DASH = "—"


def space_at_least(px: float) -> int:
    """The smallest spacing step that is at least `px` — a MEASURED gap, snapped to the scale.

    The scale exists so nobody picks 5 or 9 by nudging. But some gaps are not chosen at all: they
    are the size a third-party item needs in order not to paint outside its own box, and that size
    is a font metric, discovered at runtime (see PlotsView._budget_axis_gutters). Rounding such a
    measurement UP to a declared step is what keeps the two ideas compatible — the number comes
    from the widget, the value that lands in the layout is still one of the app's eight.

    The same shape as theme.focus_pad: a derivation of the scale, not a fourth kind of number."""
    for step in (theme.SPACE_XXS, theme.SPACE_XS, theme.SPACE_S, theme.SPACE_M,
                 theme.SPACE_L, theme.SPACE_XL, theme.SPACE_2XL, theme.SPACE_3XL):
        if step >= px:
            return step
    return theme.SPACE_3XL


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


def budget_plot_gutters(view, layout, plots, *, inset: int) -> tuple[int, int]:
    """Give a pyqtgraph plot's AXIS TITLES the room they measure. Returns the (left, bottom) applied.

    THE DEFECT THIS EXISTS FOR, in pyqtgraph's own numbers. `AxisItem._updateWidth` reserves room
    for a title with ``w += self.label.boundingRect().height() * 0.8`` — the ``* 0.8`` carries the
    comment "bounding rect is usually an overestimate" — and `AxisItem.resizeEvent` then places that
    title a further ``nudge = 5`` px OUTSIDE the axis (``p.setX(-nudge)`` on a left axis;
    ``size().height() - br.height() + nudge`` on a bottom one). Reserving four fifths of a box and
    then pushing the box further out than you reserved overhangs by construction. Measured on the
    shipped app: `speed (km/h)` and `Δ to ideal (s)` lost their left 2 px and `distance (m)` its
    bottom 5.8 px at EVERY window size, from 1440x900 down to the app's own 845x414 minimum,
    because the overhang is a font metric and not a layout pressure.

    A SECOND, UNRELATED DEFICIT rides on the bottom edge. pyqtgraph's GraphicsView sizes its SCENE
    from the widget (``autoPixelRange`` reads ``self.size()``) but only the VIEWPORT is visible, and
    this app puts a 2 px border on every QGraphicsView so a keyboard focus ring can be painted
    without re-laying the plot out (see the QGraphicsView rule in theme.py — the reservation is
    deliberate, and paid in the resting state). Scene origin and viewport origin coincide, so the
    whole difference lands at the bottom: a 917x456 scene inside a 913x452 viewport draws its last
    four rows where nothing can show them. Both deficits are absorbed here because the measurement
    below is taken against what the viewport can SHOW, which is the only rectangle that matters.

    WHY MEASURED AND NOT A FIXED RESERVE. The overhang is `nudge` plus a fifth of the title's own
    bounding height: it does not move when the text does (`speed (km/h)` -> `speed (mph)`,
    `Δ to best` -> `Δ to ref`), but it moves with the FACE those strings are set in — and the app
    ships a font STACK, so the metrics depend on which of Inter / the system UI font a machine
    resolves. A constant chosen against Inter 11 on one box silently starts clipping on the next.

    THE MEASUREMENT IS MARGIN-INVARIANT, which is what makes one pass enough. It asks for
    ``current_margin + (how far past the visible edge the title paints)``: each pixel of margin
    moves the title exactly one pixel back inside, so that sum is the same number whatever the
    current margin is — and it can come out SMALLER than the current margin, so a title that later
    needs less gets its pixels back instead of keeping a stale reserve.

    `space_at_least` then rounds it up to a declared step, so the value that reaches the layout is
    still one of the app's eight (the same bargain theme.focus_pad strikes: derive the number, keep
    the scale). Snapping a margin-invariant quantity is itself stable, so this cannot oscillate.

    `view` is the pg.GraphicsView (GraphicsLayoutWidget / PlotWidget); `layout` is the
    QGraphicsGridLayout whose margins position those plots — ``glw.ci.layout`` for a layout widget,
    ``plotItem.layout`` for a bare PlotWidget; `plots` are the PlotItems to inspect. Idempotent: it
    only writes when the margins would change, so it is safe to call from refresh() and resize()."""
    viewport = view.viewport()
    margins = layout.getContentsMargins()
    if viewport is None:
        return int(margins[0]), int(margins[3])
    left_need = bottom_need = None
    for plot in plots:
        for side in ("left", "bottom"):
            axis = plot.getAxis(side)
            label = getattr(axis, "label", None)
            # An axis with no TITLE still has a label item — an empty 8 px box that would measure
            # as an overhang and buy a gutter for nothing (the Stats sparkline is exactly this).
            if (axis is None or not axis.isVisible() or label is None
                    or not label.isVisible() or not getattr(axis, "labelText", "")):
                continue
            r = label.mapRectToScene(label.boundingRect())
            if side == "left":
                need = margins[0] + (0.0 - r.left())
                left_need = need if left_need is None else max(left_need, need)
            else:
                need = margins[3] + (r.bottom() - viewport.height())
                bottom_need = need if bottom_need is None else max(bottom_need, need)
    left = space_at_least(max(float(inset), left_need)) if left_need is not None \
        else int(margins[0])
    bottom = space_at_least(max(float(inset), bottom_need)) if bottom_need is not None \
        else int(margins[3])
    if (round(margins[0]), round(margins[3])) != (left, bottom):
        layout.setContentsMargins(left, margins[1], margins[2], bottom)
    return left, bottom


def budget_plot_min_height(view, layout, plots) -> int:
    """The smallest widget height at which stacked plots can each NAME their y axis. Applies it.

    THE DEFECT THIS EXISTS FOR. A pyqtgraph left-axis title is ROTATED and centred on its axis,
    with no length check anywhere — `AxisItem.resizeEvent` sets the label's position from the axis
    height and lets it overhang both ends when the string is longer than the axis. Two stacked
    plots therefore do not merely clip; they overprint EACH OTHER. Measured on the shipped charts
    quadrant at the app's own 845x414 minimum: `speed (km/h)` is 93.5 px of rotated text on a
    35.9 px axis, `Δ to ideal (s)` is 88.9 px on the same, and the two titles shared 24 x 49.5 px
    of one gutter — an unreadable mash of two alphabets — while the top 26.5 px of `speed (km/h)`
    was outside the viewport entirely. `budget_plot_gutters` cannot see any of this: it measures
    the axis-PERPENDICULAR direction, and this is a length along the axis.

    WHY A MINIMUM AND NOT A DEGRADATION. The honest reading of the measurement is that 414 px is
    not a height two stacked labelled charts fit in — at the point they stop colliding the window
    is ~530 px tall — so the choice is between a window minimum that admits that and a charts
    panel that drops or shortens its titles under pressure. This is the second: hiding a title on
    a condition evaluated mid-layout was built in an earlier phase and reverted, because the
    condition fired transiently at a SHIPPED size and took the friction circle's y-title with it
    (see .claude/design-system-2026-09-04.md §8). A minimum has no condition to misfire: it is
    declared once, Qt enforces it, and every size the app can then be driven at is a size where
    both charts are named in full.

    THE NUMBER IS MEASURED, for the same reason the gutters are: the overhang is a font metric of
    whichever face of this app's font STACK a machine resolves, not a layout pressure, so a
    constant chosen against Inter 11 on one box is wrong on the next. Each plot is asked for two
    quantities that are both independent of the current height — its title's length along the axis
    (`mapRectToScene(...).height()` of the rotated label) and its non-axis CHROME (everything in
    the plot that is not the left axis: the bottom axis and its labels). Verified height-invariant
    across a 414 -> 800 px sweep: the two titles stayed 93.5 / 88.9 px and the chrome 1.5 / 40.7 px
    at every step. One pass is therefore enough, and re-running it can only produce the same
    answer — it cannot ratchet.

    The condition it enforces is the strict one, `every axis is at least as long as its own title`,
    which is ~8 px stricter than "the two titles do not touch" and does not depend on the row
    stretch, on which plot owns the shared x axis, or on how many plots there are.

    Returns the minimum height applied to `view` (0 if nothing could be measured). Idempotent: it
    only writes when the value would change, so it is safe to call from resize()."""
    margins = layout.getContentsMargins()
    need = float(margins[1]) + float(margins[3])
    rows = 0
    for plot in plots:
        axis = plot.getAxis("left")
        label = getattr(axis, "label", None)
        if (axis is None or not axis.isVisible() or label is None
                or not label.isVisible() or not getattr(axis, "labelText", "")):
            continue
        title = label.mapRectToScene(label.boundingRect()).height()
        chrome = max(0.0, plot.boundingRect().height() - axis.boundingRect().height())
        need += title + chrome
        rows += 1
    if rows == 0:
        return int(view.minimumHeight())
    need += layout.verticalSpacing() * (rows - 1)
    # The scene is sized from the WIDGET and only the VIEWPORT is visible — this app puts a
    # FOCUS_RING_PX border on every QGraphicsView so a focus ring costs no re-layout — so the
    # widget has to be that much taller than the height the scene needs. Same accounting as the
    # bottom gutter in budget_plot_gutters, from the other side.
    border = max(0, view.height() - view.viewport().height()) if view.viewport() is not None else 0
    applied = int(need) + border
    if view.minimumHeight() != applied:
        view.setMinimumHeight(applied)
    return applied


class EmptyState(QWidget):
    """The app's ONE not-loaded / nothing-here surface: icon? · title · body, one rhythm, one measure.

    WHAT WAS MEASURED. Sixteen states that answer the question "why is there nothing here?" were
    driven in the real app (QA D2). They shared exactly ONE property — the 13 px body size. An icon
    appeared at 1 of 16 sites and a title slot at 1; alignment was centred at 15 sites and left at
    one, and the outlier used the SAME QSS role as five of the centred ones; the measure ran from
    19 to 138 characters at that one type size because nothing anywhere set a maximum width; and
    the same pane changed colour when you switched tab (`#21252E` card on Laps, `#15181E` canvas on
    Coaching, identical rectangle). Spacing was NOT the finding: every internal gap was already on
    the scale. The space system was finished and the composition system had never been started.

    THE THREE SLOTS ARE THE OBJECT.

      * `title` — WHAT HAPPENED, one sentence, at `theme.EMPHASIS` (15). Not HERO 22: that step is
        the welcome WORDMARK (`role="Title"`, shared with the About and privacy cards), a brand
        lockup rather than a state title. Not BODY 13 either, or there is no title. EMPHASIS is the
        existing rung for "a value that must outrank its own label" and it is the only free step on
        11/13/15/22 — no new type size was bought for this.
      * `body` — WHY, then WHAT NEXT, in that order, wrapped at `theme.EMPTY_MEASURE_PX`.
      * `icon` — optional, a `theme.icon()` NAME (never a literal Unicode mark: see
        tests/test_glyph_vocabulary.py), rendered at ICON_PX in `C.text_muted`.

    ABSENT SLOTS ARE NOT CONSTRUCTED. A zero-height ghost label is not free: the Coaching panel's
    stray 11 px gap was an empty 7x14 header label nobody could see and nobody had removed.

    NO `busy` SLOT AND NO `action` SLOT, and both omissions are decisions rather than oversights.
    The object D2 §8.1 proposed carried both, for the LOADING CARD (`app.py`) and for a window-sized
    state's single button. Measured after PR #191 landed, the loading card is deliberately NOT this
    object: it is the SECOND FRAME OF THE WELCOME SCREEN and #191 built it on the welcome drop
    zone's own column, with the wordmark's 22 px `role="Title"` headline, precisely so the first
    click does not restyle the screen under the user. Adopting a 15 px state title there would undo
    that. With the loading card out, no site in the app passes `busy`, and the ACTION RULE — a state
    that owns a WINDOW may carry a button, a state that owns a PANEL gets a sentence, because a
    467 px lap pane cannot carry a button without competing with the panel's own toolbar — leaves
    every state this object serves as prose. The one window-sized candidate (the empty Library) can
    only offer "open a recording" through a seam `library_dialog` does not have. A slot with no
    caller is a slot with no proof, so the two go in with the caller that needs them.

    `owns_pane` decides the SURFACE, and it is the whole of D2-07: True paints the card (the
    `card="true"` dynamic property → `QWidget#EmptyState[card="true"]`), False leaves the canvas
    showing. It is a property of the SITE — does this state replace a panel's content, or float on
    the window? — so two tabs of one panel can no longer disagree about it by accident.

    `Qt.WA_StyledBackground` IS SET EXPLICITLY and the line is load-bearing. QStyleSheetStyle sets
    it for you only when `metaObject() == QWidget::staticMetaObject` — a BARE QWidget — so a QWidget
    SUBCLASS silently stops painting its QSS background while `palette()` cheerfully reports the
    rule's colour. That cost three widgets in PR #185; `PanelHeader.__init__` above carries the full
    note. The card is proved from the WINDOW COMPOSITE in tests/test_state_surfaces.py, never from a
    child `grab()`, because a child grab reads the colour out of the palette and would report
    success against a widget that composited nothing.

    THE MEASURE CAP IS ON THE LABELS, not on a wrapper widget, for the reason `PanelToolbar._mount`
    gives for using a nested LAYOUT: the theme's base rule paints every bare `QWidget` with the
    CANVAS colour, so a wrapper column inside the card would punch a canvas-coloured hole in it.
    """

    def __init__(self, title: str, body: str = "", *, icon: str | None = None,
                 owns_pane: bool = True, parent=None):
        super().__init__(parent)
        self.setObjectName("EmptyState")
        # See the class prose: a QWidget SUBCLASS paints its QSS box only when told to.
        self.setAttribute(Qt.WA_StyledBackground, True)
        # The app's own dynamic-property idiom (app.py._set_dragover): the string, or None to
        # remove the property entirely, so the [card="true"] selector simply stops matching.
        self.setProperty("card", "true" if owns_pane else None)

        column = QVBoxLayout(self)
        # SPACE_XL is documented as "a page's own breathing room (empty states, dialog bodies)" —
        # this is the surface that sentence was written about. It was already what the QSS rule
        # this object replaces spent as `padding`, so no state's inset moves.
        column.setContentsMargins(theme.SPACE_XL, theme.SPACE_XL, theme.SPACE_XL, theme.SPACE_XL)
        # Every gap is stated below, one addSpacing per boundary, so the rhythm cannot drift as
        # slots come and go — a layout `spacing` would silently apply itself to whichever pairs
        # happened to exist.
        column.setSpacing(0)
        column.setAlignment(Qt.AlignCenter)

        self.icon_label = None
        if icon:
            self.icon_label = QLabel(self)
            self.icon_label.setPixmap(theme.icon(icon, color=theme.C.text_muted)
                                      .pixmap(theme.ICON_PX, theme.ICON_PX))
            self.icon_label.setAlignment(Qt.AlignCenter)
            # A pixmap has no text for a screen reader; the title says the same thing, so the mark
            # is decorative and named after it rather than left anonymous.
            self.icon_label.setAccessibleName(title)
            # THE ICON'S GAP IS ITS OWN BOTTOM MARGIN, not an addSpacing item, because this is the
            # one slot a live surface hides and shows: the Library dialog's two senses differ by
            # exactly this mark, and "Forget all recordings" can flip between them while the dialog
            # is open. A layout SPACING item does not disappear with the widget above it, so an
            # addSpacing here would leave a 12 px hole every time the icon went away — which is the
            # defect this object exists to stop (Coaching's stray 11 px gap was a 7x14 empty header
            # label nobody could see). A hidden widget takes no space at all, margins included.
            self.icon_label.setContentsMargins(0, 0, 0, theme.SPACE_M)
            column.addWidget(self.icon_label, 0, Qt.AlignHCenter)

        self.title = QLabel(title, self)
        self.title.setProperty("role", "EmptyTitle")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setWordWrap(True)
        self.title.setMaximumWidth(theme.EMPTY_MEASURE_PX)
        column.addWidget(self.title, 0, Qt.AlignHCenter)

        self.body = WrapLabel(body, self)
        self.body.setProperty("role", "EmptyBody")
        self.body.setAlignment(Qt.AlignCenter)
        self.body.setMaximumWidth(theme.EMPTY_MEASURE_PX)
        # ...and the title→body gap the same way, on the same rule: EVERY GAP BELONGS TO THE SLOT
        # THAT CAN VANISH — the icon's below it, the body's above it — so hiding either takes its
        # own air with it and the two present slots are always exactly one step apart.
        self.body.setContentsMargins(0, theme.SPACE_S, 0, 0)
        column.addWidget(self.body, 0, Qt.AlignHCenter)
        self.body.setVisible(bool(body))

    def set_state(self, title: str, body: str = "") -> None:
        """Rewrite both slots. One call, because a state is a PAIR — a title with a stale body is
        how the Corners page came to tell a user to select a lap on a recording that has none."""
        self.title.setText(title)
        self.body.setText(body)
        self.body.setVisible(bool(body))

    def text(self) -> str:
        """Everything this state SAYS, as one string — the shape the QLabel placeholders it
        replaced had, so a guard (and the tests that predate the object) can read a state's whole
        copy without knowing how many labels it is made of."""
        return f"{self.title.text()}\n\n{self.body.text()}" if self.body.text() \
            else self.title.text()


class Tile(QWidget):
    """One STAT TILE: a value in the mono face over a dim caption naming it. `set()` rewrites both.

    Promoted out of `stats_panel._Tile`, where the app's whole "a number and what it is" pattern had
    been living privately. It is the Stats page's unit of composition — ~30 of them — and it is the
    shape the Library and Coaching pages want too, so it belongs with the rest of the vocabulary
    rather than inside the one page that got there first.

    THE TYPE PAIR IS THE POINT, and it is why the value carries `theme.EMPHASIS`: a tile is legible
    only if the value outranks its own caption, and before the type scale had a step between BODY
    and HERO both painted at 13 px (the base QSS `font-size` outranks a `setFont`), so nothing but
    colour separated "1:08.771" from "best lap". EMPHASIS was discovered here and named in theme;
    this is where the discovery is now spent.

    The value-to-caption gap is SPACE_XXS — the sub-step, which exists for exactly this: a gap
    WITHIN one element, where anything larger would read as two stacked things instead of one."""

    def __init__(self, caption: str, parent=None):
        super().__init__(parent)
        self.value = QLabel(DASH)
        self.value.setFont(theme.mono_font(theme.EMPHASIS, theme.W_SEMIBOLD))
        self.caption = QLabel(caption)
        self.caption.setFont(theme.ui_font(theme.CAPTION))
        self.caption.setProperty("role", "Note")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme.SPACE_XXS)
        lay.addWidget(self.value)
        lay.addWidget(self.caption)

    def set(self, value: str | None, caption: str | None = None):
        self.value.setText(value if value else DASH)
        if caption is not None:
            self.caption.setText(caption)


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
        # ...AND THE BAR HAS TO BE TOLD TO PAINT IT. QStyleSheetStyle::polish sets
        # WA_StyledBackground for you only when `w->metaObject() == &QWidget::staticMetaObject` —
        # a BARE QWidget. A QWidget SUBCLASS honours neither the background nor the border, and
        # says nothing: the role is set, the rule matches, `palette()` even reports the rule's
        # colour, and the bar composites the canvas behind it. That is what happened here. These
        # bars were bare `QWidget()`s built by `central_view._header_bar` and DID paint; promoting
        # them to this class silently flattened all four panel headers and both toolbars to canvas
        # — no surface, no hairline, no visible extent for the "double-click the header" target.
        # Anything QLabel/QPushButton-derived draws its own box and never needs this line, which is
        # why every other #Name rule in the app just works (the dialogs' QLabel PanelHeaders among
        # them). tests/test_inline_styles.py's check 5 now holds every QWidget subclass the QSS
        # gives a box to this rule.
        self.setAttribute(Qt.WA_StyledBackground, True)
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
    """A panel's CONTROL row — the things you click, under the header.

    Three panels have one (map: line channel · snap · add/reset sector; charts: the hero's
    reference toggle · the two chart toggles · the x-axis mode; video: the transport), and a panel
    without controls does not get an empty strip.

    TWO GROUPS AROUND THE STRETCH, not one right-aligned run. The map and charts toolbars are pure
    right-aligned control runs, which is why this class began as `addStretch(1)` then everything.
    A media transport is not that shape: ▶ and 🔇 act on the picture and belong under its left
    edge, while the view toggles belong opposite them — and the app's only control zone that was
    NOT on a bar was the one that could not be expressed here. `leading` is the group before the
    stretch; `*controls` stays the group after it, so the two existing toolbars are unchanged.

    A GROUP IS A TUPLE, and it is a LAYOUT rather than a wrapper widget. Passing a tuple of
    widgets puts them in a nested row at SPACE_XS — "these are one idea" — while everything else
    in the bar stays SPACE_S apart, which is what stops five transport buttons reading as one
    undifferentiated run. It is not a wrapper QWidget because the theme's base rule paints every
    BARE QWidget with the canvas colour (a wrapper would punch a canvas hole in the bar's own
    surface) and a QWidget SUBCLASS silently paints nothing at all — see the long note in
    PanelHeader.__init__. A layout has no box, so it has neither problem.

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

    def __init__(self, *controls, leading=(), parent=None):
        super().__init__(parent)
        # Reuses the header's themed role — surface + bottom hairline — so the two rows read as one
        # block of panel chrome with a rule between identity and actions. A `PanelToolbar` role of
        # its own belongs with the rest of the control vocabulary, in the phase that owns theme.py.
        self.setProperty("role", "PanelHeader")
        # A QWidget subclass paints the QSS box only when told to — see the long note in
        # PanelHeader.__init__ for the mechanism and what it cost.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(theme.TOOLBAR_H)
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.SPACE_S, theme.SPACE_XXS, theme.SPACE_S, theme.SPACE_XXS)
        row.setSpacing(theme.SPACE_S)
        #: every control before the stretch, flat (a group's members counted individually)
        self.leading = self._mount(row, leading)
        row.addStretch(1)
        #: every control after the stretch, flat — the original right-aligned run
        self.controls = self._mount(row, controls)

    def _mount(self, row: QHBoxLayout, items) -> tuple:
        """Add each item to `row` and return every control that landed there, FLAT.

        An item is a control, or a TUPLE of controls that are one idea (see the class docstring).
        The flat return is what lets a guard assert "every control in a toolbar shares one height"
        without having to know which of them were grouped."""
        flat = []
        for item in items:
            if isinstance(item, (tuple, list)):
                group = QHBoxLayout()
                group.setContentsMargins(0, 0, 0, 0)
                group.setSpacing(theme.SPACE_XS)
                for c in item:
                    self._pin(c)
                    group.addWidget(c, 0, Qt.AlignVCenter)
                    flat.append(c)
                row.addLayout(group)
            else:
                self._pin(item)
                row.addWidget(item, 0, Qt.AlignVCenter)
                flat.append(item)
        return tuple(flat)

    @staticmethod
    def _pin(c: QWidget) -> None:
        """One control at the one control height and its own honest width (see the class prose)."""
        c.setFixedHeight(theme.CTRL_H)
        c.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
