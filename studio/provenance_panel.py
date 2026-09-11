"""The "inspect this number" panel — a thin renderer over one `provenance.Provenance`.

Right-click a lap time, a sector split or a corner best and this opens: the raw GPS fixes that
produced it, the method in one sentence, N and the exact window, the fix-quality distribution over
that window, the arithmetic with its numbers substituted, the value RE-DERIVED from those rows —
and Copy as CSV.

IT IS A RENDERER AND NOTHING ELSE. Every number, sentence and table on this panel arrives inside
the `Provenance` value that `studio/provenance.py` defines and `Session` assembles. This module
computes no statistic, re-derives no value and knows nothing about laps, sectors or corners; swap
in a `Provenance` built for a fourth number and the panel renders it with no edit here. That split
is the point of the feature rather than an implementation detail — a panel that reverse-engineered
where a value came from would agree with the app exactly until the day the app changed.

WHY IT IS READ-ONLY, and stays read-only. The precedents (Grafana's panel inspector, Datasette's
"View and edit SQL") both let you edit the query; this deliberately does not. The app's one product
rule is that it never shows a number it cannot vouch for, and a user-composed expression is by
construction a number it cannot vouch for. Inspection plus CSV is the whole scope: take the rows
somewhere else and the disagreement is yours, which is the honest place for it.

NO STYLESHEET OF ITS OWN. Section headings, notes and tables take the roles the theme already
declares (`PanelHeader` / `Title` / `Note` / `TableNote`), and every gap is a SPACE token — the
same discipline `tests/test_inline_styles.py` and `tests/test_design_system.py` hold the rest of
the view layer to.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, theme
from .widgets import WrapLabel

# The panel's extents, in the app's units rather than in numbers somebody liked (the argument
# `command_palette` makes for the same pair). A ten-column fix table wants width above all: 16 and
# 11 times SPACE_3XL are 768 x 528, which fits the widest table this panel can build without a
# horizontal scrollbar and still opens inside the smallest 13-inch scaled mode.
_MEASURE_PX = 16 * theme.SPACE_3XL
_HEIGHT_PX = 11 * theme.SPACE_3XL

#: How many table rows are rendered before the table is truncated with a note. A lap's fix table is
#: ~686 rows and Qt builds every item eagerly; the CSV is never truncated, which is what keeps the
#: cap a rendering decision rather than a claim about the data.
MAX_ROWS = 400
TRUNCATION = ("Showing the first {shown} of {total} rows. Copy as CSV for all {total} — the "
              "clipboard is never truncated.")

#: A grid's floor — tall enough to show a handful of rows and its header, so a table never opens
#: as a sliver the reader has to resize before it is a table, and narrow enough that the grid
#: never becomes the thing that decides how wide the panel is.
_GRID_MIN_H = 5 * theme.SPACE_3XL
_GRID_MIN_W = 6 * theme.SPACE_3XL

#: The menu item every surface uses, so three context menus cannot drift into three wordings.
MENU_LABEL = "Inspect this number…"


def _section(text: str) -> QLabel:
    """A section heading, in the role the four panel headers already wear."""
    label = QLabel(text)
    label.setProperty("role", "PanelHeader")
    return label


def _note(text: str) -> WrapLabel:
    """One paragraph of explanation. WrapLabel so the measure is the panel's, not the sentence's."""
    label = WrapLabel(text)
    label.setProperty("role", "Note")
    return label


def _mono(text: str, *, strong: bool = False) -> QLabel:
    """A line of ARITHMETIC. Mono because these are expressions with digits that should line up,
    and because the app's one mono face is where a number that must be read exactly belongs.

    IT WRAPS, AND IT MUST. A crossing step is ~90 characters of substituted expression, and a
    QLabel reports its whole single line as its minimum width — which made the scroll area's body
    WIDER THAN ITS VIEWPORT, put a horizontal scrollbar under the panel, and then clipped the
    wrapped prose above it at the viewport edge (the method sentence lost "at t = t0 + f" off the
    right-hand side; caught in an offscreen capture, not by any assertion). Wrapping a long
    expression is mildly ugly; silently truncating the sentence that explains the number is not a
    trade this panel gets to make."""
    label = QLabel(text)
    label.setFont(theme.mono_font(theme.TABLE, theme.W_SEMIBOLD if strong else theme.W_REGULAR))
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setWordWrap(True)
    return label


def _table_widget(table) -> QTableWidget:
    """One `provenance.Table` as a read-only grid, in the house table idiom (`library_dialog` /
    `command_palette`): no grid lines, alternating rows, whole-row selection, no edit triggers."""
    shown = min(len(table.rows), MAX_ROWS)
    grid = QTableWidget(shown, len(table.columns))
    grid.setHorizontalHeaderLabels(list(table.columns))
    grid.verticalHeader().setVisible(False)
    grid.setShowGrid(False)
    grid.setAlternatingRowColors(True)
    grid.setSelectionBehavior(QAbstractItemView.SelectRows)
    grid.setEditTriggers(QAbstractItemView.NoEditTriggers)
    grid.verticalHeader().setDefaultSectionSize(theme.GRID_ROW_H)
    mono = theme.mono_font(theme.TABLE)
    for r in range(shown):
        for c in range(len(table.columns)):
            item = QTableWidgetItem(table.cell(r, c))
            item.setFont(mono)
            # Numbers right, words left — the convention the lap table already applies, and the
            # only thing that makes a column of coordinates readable at a glance. Decided by the
            # column's own FORMAT rather than by its position: a positional rule ("all but the
            # last three") is right for the ten-column fix table and wrong for the three-column
            # population table beside it, which is how that one shipped entirely left-aligned.
            if "{:" in table.formats[c]:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.setItem(r, c, item)
    header = grid.horizontalHeader()
    for c in range(len(table.columns)):
        header.setSectionResizeMode(c, QHeaderView.ResizeToContents)
    # NO stretch-last-section: it fights ResizeToContents on a grid narrower than its content and
    # squeezes the final column instead of widening it — which elided the `role` cell, the one
    # column whose whole job is to say whether a row was measured or derived.
    header.setStretchLastSection(False)
    # A ten-column fix table is wider than the panel on a small display. It scrolls INSIDE ITSELF
    # rather than widening the body — the same reason the mono lines wrap: one horizontally
    # scrolling region in a column of prose clips the prose.
    for c in range(len(table.columns)):
        if "{:" in table.formats[c]:
            grid.horizontalHeaderItem(c).setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    grid.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    grid.setMinimumWidth(_GRID_MIN_W)
    return grid


class ProvenancePanel(QDialog):
    """One displayed number, opened up. Built fresh per open from an already-built `Provenance`,
    so it holds no session and needs no refresh path: the value it describes was true when the
    panel was asked for, and the panel says which window it was true over.

    Constructible headlessly from a bare `Provenance` (no Session, no pacer, no telemetry file),
    which is what `tests/test_provenance_panel.py` builds it from."""

    def __init__(self, prov, parent=None):
        super().__init__(parent)
        self.prov = prov
        self.setWindowTitle(f"{APP_NAME} — {prov.title}")
        self.setMinimumWidth(_MEASURE_PX)
        self.resize(_MEASURE_PX, _HEIGHT_PX)

        root = QVBoxLayout(self)
        root.setContentsMargins(theme.SPACE_M, theme.SPACE_M, theme.SPACE_M, theme.SPACE_M)
        root.setSpacing(theme.SPACE_S)

        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.SPACE_M)
        self._build(column)
        column.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # The column wraps, so it never needs horizontal room; an h-scrollbar here would only ever
        # mean something inside it had refused to fit, which is the defect above rather than a
        # feature (the same call `help_dialog._copy_column` makes, for the same reason).
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        root.addLayout(self._buttons())

    # ------------------------------------------------------------------ the column, top to bottom
    def _build(self, column: QVBoxLayout) -> None:
        p = self.prov

        # 1. WHAT THIS IS. The value exactly as the cell painted it, next to what it is a value OF.
        head = QLabel(f"{p.title}   {p.formatted}")
        head.setProperty("role", "Title")
        head.setTextInteractionFlags(Qt.TextSelectableByMouse)
        column.addWidget(head)

        # 2. THE METHOD, in one sentence, straight out of provenance.METHODS.
        column.addWidget(_note(p.method))

        # 3. THE WINDOW AND N — the two facts a number is meaningless without.
        column.addWidget(_section("MEASURED OVER"))
        axis = "media-clock seconds" if p.window.kind == "time" else "lap odometer metres"
        column.addWidget(_mono(f"{axis}   {p.window.label()}"))
        column.addWidget(_mono(f"N = {p.n} raw GPS fixes in that window"))
        if p.source:
            column.addWidget(_note(f"Computed by {p.source}."))

        # 4. THE FIX QUALITY OVER THAT WINDOW — not over the recording.
        column.addWidget(_section("FIX QUALITY IN THIS WINDOW"))
        for line in p.quality.lines():
            column.addWidget(_mono(line))

        # 5. THE ARITHMETIC, with its numbers substituted.
        if p.steps:
            column.addWidget(_section("THE ARITHMETIC"))
            for step in p.steps:
                column.addWidget(_mono(f"{step.label:>18}   {step.text}", strong=step.highlight))

        # 6. THE RE-DERIVATION — the claim the whole panel exists to let you check.
        column.addWidget(_section("RE-DERIVED FROM THE ROWS BELOW"))
        column.addWidget(_mono(p.residual_note(), strong=p.matches_display))

        # 7. THE ROWS. tables[0] is always the raw fixes; a second table appears where the number
        #    is a reduction over a population (a corner best is a minimum over laps).
        for table in p.tables:
            column.addWidget(_section(table.caption.upper()))
            grid = _table_widget(table)
            grid.setMinimumHeight(_GRID_MIN_H)
            column.addWidget(grid)
            if len(table.rows) > MAX_ROWS:
                column.addWidget(_note(TRUNCATION.format(shown=MAX_ROWS, total=len(table.rows))))
            if table.note:
                note = _note(table.note)
                note.setProperty("role", "TableNote")
                column.addWidget(note)

        # 8. WHAT THE PANEL WOULD OTHERWISE BE QUIETLY ASSUMING.
        if p.notes:
            column.addWidget(_section("WHAT THIS NUMBER DOES NOT SAY"))
            for note in p.notes:
                column.addWidget(_note(note))

    def _buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SPACE_S)
        self.copy_button = QPushButton("Copy as CSV")
        self.copy_button.setFixedHeight(theme.CTRL_H)
        self.copy_button.clicked.connect(self.copy_csv)
        row.addWidget(self.copy_button)
        row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        row.addWidget(buttons)
        return row

    def copy_csv(self) -> str:
        """Put the WHOLE inspection on the clipboard — metadata, arithmetic, every table, every
        row, floats at full precision. Returns the text so a headless test can assert what a user
        would paste rather than what the panel meant to give them.

        A clipboard can be absent (offscreen Qt with no platform clipboard); the text is still
        returned, because the caller's question is what the CSV says."""
        text = self.prov.to_csv()
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        self.copy_button.setText(f"Copied {len(self.prov.samples.rows)} rows")
        return text



def open_for(prov, parent=None) -> ProvenancePanel | None:
    """Open the panel for `prov`, or do nothing when there is no provenance to show.

    The ONE entry point every surface calls, so a menu item that cannot be served fails the same
    way everywhere — by not opening — rather than by three call sites each inventing an error
    dialog for a degenerate lap."""
    if prov is None:
        return None
    panel = ProvenancePanel(prov, parent)
    panel.show()
    return panel
