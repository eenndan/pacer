"""The Marks page + the mark editor — the list half of `studio/marks.py`.

A filterable table of everything marked on this recording: what the driver wrote down, and what
pacer detected. Rows are read, searched, jumped to and edited; the persisting is the window's, so
this module holds no store and writes no file — it emits intents and renders what it is given, the
way `coaching_panel.OpportunitiesPanel` does.

WHY THE LIST IS A PAGE AND NOT A DIALOG. The two surfaces a mark has are the BAND (where it is, on
the same axis as the footage) and the LIST (what it says). A list you consult while scrubbing has
to stay on screen while you scrub, which rules a modal out; the lap panel is already the app's list
surface and already has the tab bar to put it behind. MEASURED on the shipped theme, the whole
surface costs the window's minimum width 955 -> 984 px — 26 of that is the fifth tab (the tab bar's
own +52, halved by the splitter) and 3 is this page's toolbar — and NOTHING in height, which stays
588. That is the one budget this wave has moved twice, so it is spent deliberately and once.

The editor (`MarkDialog`) is a modal, because authoring IS a mode: the video pauses, you say what
happened, and you go back to watching.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, theme
from . import marks as marks_model
from ._signal import DASH, fmt_hms, lap_label
from .widgets import NUM_ROLE, EmptyState, NumItem, PanelToolbar, icon_button

# The filter's three senses. WHOSE, not which type: the type filter is a second control, and the
# question a driver actually opens this page with is "what did I write down?" — which a type list
# answers badly (six types, and "mine" is five of them). `ALL` leads because it is the honest
# default: a page that opened already hiding rows would be a page that lies about the count.
FILTER_ALL, FILTER_MINE, FILTER_AUTO = "all", "mine", "auto"
_FILTERS = ((FILTER_ALL, "All marks"), (FILTER_MINE, "Mine"), (FILTER_AUTO, "Found by pacer"))

_HEADERS = ("Time", "Lap", "Type", "What")

# Column indices, named — four string literals read worse than four names in five methods.
COL_TIME, COL_LAP, COL_TYPE, COL_NOTE = range(4)

# The row's identity, carried on the Time cell. NOT `Qt.UserRole`: `widgets.NUM_ROLE` IS
# `Qt.UserRole` and `NumItem` sorts on it, so writing the id there would make every row's sort key
# a string and silently break the time ordering. One role up.
_ID_ROLE = Qt.UserRole + 1

_EMPTY_TITLE = "Nothing marked yet."
_EMPTY_BODY = (
    "A mark is where you write down what you concluded — \"baulked here\", \"kerb\", \"that was "
    "the one\" — at the second it happened, against the footage that shows it. Press B while the "
    "video is playing to drop one at the playhead; , and . jump between them afterwards. "
    "pacer adds its own marks for the things it detects: GPS dropouts, laps it left out of your "
    "times, and stretches where the GPS went bad."
)
_FILTERED_TITLE = "No marks match this filter."
_FILTERED_BODY = "Clear the search box, or switch the filter back to All marks."


def _time_text(mark: dict) -> str:
    """A mark's WHEN: a timecode, or a range, or — for a mark this open cannot place — the chapter
    it belongs to instead. Never a number pretending to be a position it does not have."""
    if not mark.get("placed"):
        return f"in {mark.get('chapter') or 'another chapter'}"
    text = fmt_hms(mark["t"])
    if mark.get("t_end") is not None:
        text = f"{text}–{fmt_hms(mark['t_end'])}"
    return text


class MarksPanel(QWidget):
    """The lap panel's Marks page: a toolbar (filter · search · the row actions) over the table.

    It owns no store and no session. `set_marks` hands it the merged runtime list
    (`marks.merge`) plus the count of degraded stretches that were too short to mark, and every
    gesture leaves as a signal."""

    #: A row was activated (clicked or ⏎) — seek the video to it. Carries the mark id.
    markActivated = Signal(str)
    #: The toolbar's four verbs. `addRequested` carries nothing (the window knows the playhead);
    #: the other three carry the selected mark's id.
    addRequested = Signal()
    editRequested = Signal(str)
    deleteRequested = Signal(str)
    extendRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._marks: list[dict] = []
        self._suppressed = 0

        # THE TOOLBAR IS WIDTH-BUDGETED, and the budget is the reason the four verbs are icons.
        # `PanelToolbar` pins every control to its own sizeHint width (QSizePolicy.Fixed) precisely
        # so a squeeze is refused rather than absorbed as clipped text — which means this bar's
        # honest need becomes the LAP PANEL's minimum width, and roughly half of any growth there
        # reaches the window's own minimum. Written as text buttons ("Mark (B)" · "Edit…" ·
        # "Extend to playhead" · "Delete") the four came to ~340 px of button alone. As
        # `icon_button`s they are 4 x ICON_BTN and the whole bar measures 423 px — 3 px over the
        # 420 the FIVE-TAB panel header already asks for, so of the window's measured 955 -> 984 px
        # the tab itself is 26 and this list is 3. Every verb carries its words in its tooltip.
        self.filter_combo = QComboBox()
        for key, label in _FILTERS:
            self.filter_combo.addItem(label, key)
        self.filter_combo.setToolTip(
            "Which marks to list: everything, only the ones you wrote, or only the ones pacer "
            "derived from what it detected.")
        self.filter_combo.currentIndexChanged.connect(self._refill)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search marks…")
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip("Filter the list by what a mark says, or by its type.")
        self.search.textChanged.connect(self._refill)

        self.add_btn = icon_button(
            "ph.plus", tooltip="Add a mark at the playhead (B) — the video pauses while you say "
                               "what happened")
        self.add_btn.clicked.connect(self.addRequested)
        self.edit_btn = icon_button(
            "ph.pencil-simple", tooltip="Edit the selected mark's type, colour or note")
        self.edit_btn.clicked.connect(lambda: self._emit_for_selection(self.editRequested))
        self.extend_btn = icon_button(
            "ph.arrows-out-line-horizontal",
            tooltip="Extend the selected mark to the playhead — turn it into a RANGE, for "
                    "something that lasted (a whole corner, a stint behind someone) rather than "
                    "happened")
        self.extend_btn.clicked.connect(lambda: self._emit_for_selection(self.extendRequested))
        self.delete_btn = icon_button(
            "ph.trash", tooltip="Delete the selected mark — the whole file is backed up first, and "
                                "File ▸ Library… can put it back")
        self.delete_btn.clicked.connect(lambda: self._emit_for_selection(self.deleteRequested))

        self.toolbar = PanelToolbar(
            (self.edit_btn, self.extend_btn, self.delete_btn),
            leading=(self.add_btn, self.filter_combo, self.search))

        self.table = QTableWidget(0, len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_TIME, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_LAP, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_TYPE, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_NOTE, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._sync_actions)
        # A single CLICK activates, not a double-click: this table's whole job is to move the
        # playhead, and every other jump-to surface in the app (the lap table, the coaching rows,
        # the stats corner rows) moves on one click. A double-click requirement here would make the
        # list feel like a form.
        self.table.itemClicked.connect(self._on_item_clicked)

        # The one line that is not a row: how many degraded stretches were held back as too short
        # to mark, and where they ARE drawn. Without it this page would show fewer degraded
        # stretches than the quality strip two panels away and say nothing about the difference —
        # the exact two-surfaces-disagree defect the auto marks exist to avoid.
        self.suppressed_note = QLabel("")
        self.suppressed_note.setProperty("role", "TableNote")
        self.suppressed_note.setWordWrap(True)
        self.suppressed_note.setVisible(False)

        self._empty = EmptyState(_EMPTY_TITLE, _EMPTY_BODY, icon="ph.bookmark-simple")

        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)    # index 0 — rows
        self.stack.addWidget(self._empty)   # index 1 — nothing to list

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.toolbar)
        column.addWidget(self.stack, 1)
        column.addWidget(self.suppressed_note)
        self._sync_actions()

    # ------------------------------------------------------------------ the data
    def set_marks(self, marks: list[dict] | None, suppressed: int = 0) -> None:
        """Adopt the merged mark list and the suppressed-stretch count, and refill."""
        self._marks = list(marks or [])
        self._suppressed = int(suppressed or 0)
        self._refill()

    @property
    def marks(self) -> list[dict]:
        return self._marks

    def visible_marks(self) -> list[dict]:
        """The marks the current filter + search admit, in list order — the single source the table
        is filled from and the regression test reads."""
        key = self.filter_combo.currentData()
        needle = self.search.text().strip().lower()
        out = []
        for m in self._marks:
            if key == FILTER_MINE and m["kind"] != marks_model.KIND_MANUAL:
                continue
            if key == FILTER_AUTO and m["kind"] != marks_model.KIND_AUTO:
                continue
            if needle:
                hay = f"{marks_model.TYPE_LABEL.get(m['type'], '')} {m.get('note') or ''}".lower()
                if needle not in hay:
                    continue
            out.append(m)
        return out

    def selected_id(self) -> str | None:
        """The selected mark's id, or None. Read off the row's own data rather than off an index,
        so sorting the table cannot make the buttons act on a different mark than the one the user
        can see is selected."""
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        item = self.table.item(rows[0].row(), COL_TIME)
        return item.data(_ID_ROLE) if item is not None else None

    def select_mark(self, mark_id: str) -> bool:
        """Select the row for `mark_id` (and scroll to it); False when it is not currently listed —
        which a filter can legitimately cause, so the caller decides what to say about it."""
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_TIME)
            if item is not None and item.data(_ID_ROLE) == mark_id:
                self.table.selectRow(row)
                self.table.scrollToItem(item)
                return True
        return False

    # ------------------------------------------------------------------ rendering
    def _refill(self) -> None:
        shown = self.visible_marks()
        if not shown:
            # TWO empty states, one object. "Nothing marked yet" teaches the feature and keeps its
            # icon; "nothing matches this filter" is a dead end the user made and must not read as
            # the first one — a page that answered a search with "press B to add a mark" would be
            # telling them the recording has no marks when it has five.
            filtered = bool(self._marks)
            state = (_FILTERED_TITLE, _FILTERED_BODY) if filtered else (_EMPTY_TITLE, _EMPTY_BODY)
            self._empty.set_state(*state)
            self._empty.set_icon_visible(not filtered)
            self.stack.setCurrentIndex(1)
        else:
            self.stack.setCurrentIndex(0)
        keep = self.selected_id()
        # Sorting must be OFF while the rows are replaced, or Qt re-sorts after every setItem and
        # the row a cell lands in stops being the row it was written for.
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(shown))
        for row, m in enumerate(shown):
            self._fill_row(row, m)
        self.table.setSortingEnabled(True)
        if keep:
            self.select_mark(keep)
        self._sync_suppressed_note()
        self._sync_actions()

    def _fill_row(self, row: int, mark: dict) -> None:
        # The time cell sorts on the mark's own seconds, not on its rendered text — "1:02:03" and
        # "in GX030062" do not sort against each other as strings, and an unplaceable mark has no
        # position at all, so it sorts to the end (the same place `marks.merge` puts it).
        when = NumItem(_time_text(mark))
        when.setData(NUM_ROLE, mark["t"] if mark.get("placed") else None)
        when.setData(_ID_ROLE, mark["id"])
        lap = mark.get("lap")
        lap_item = QTableWidgetItem(lap_label(lap) if lap is not None else DASH)
        type_item = QTableWidgetItem(marks_model.TYPE_LABEL.get(mark["type"], "Mark"))
        # The COLOUR is the mark's own, and it goes on the TYPE cell rather than on the note: the
        # band paints a mark's colour, so the list has to show which pin is this row, and the note
        # is the one column that must stay plain readable text at any length.
        type_item.setForeground(QColor(theme.mark_colour(mark["colour"])))
        note_item = QTableWidgetItem(marks_model.summary(mark))
        tip = _row_tooltip(mark)
        for item in (when, lap_item, type_item, note_item):
            item.setToolTip(tip)
        self.table.setItem(row, COL_TIME, when)
        self.table.setItem(row, COL_LAP, lap_item)
        self.table.setItem(row, COL_TYPE, type_item)
        self.table.setItem(row, COL_NOTE, note_item)

    def _sync_suppressed_note(self) -> None:
        if self._suppressed <= 0:
            self.suppressed_note.setVisible(False)
            return
        n = self._suppressed
        self.suppressed_note.setText(
            f"{n} shorter stretch{'' if n == 1 else 'es'} of degraded GPS "
            f"(under {marks_model.MIN_DEGRADED_S:.0f} s) {'is' if n == 1 else 'are'} not marked — "
            "they are drawn on the quality strip under the scrub bar.")
        self.suppressed_note.setVisible(True)

    def _sync_actions(self) -> None:
        """The three row verbs act on a selection, and only a HAND-AUTHORED mark can be edited,
        extended or deleted. A derived mark is re-derived on every load and has nothing to save —
        offering Delete on one would promise a change the next load undoes."""
        mark = self._selected_mark()
        mine = mark is not None and mark["kind"] == marks_model.KIND_MANUAL
        for btn in (self.edit_btn, self.delete_btn):
            btn.setEnabled(mine)
        # Extending needs a placed mark as well: a range has to start somewhere this open can show.
        self.extend_btn.setEnabled(mine and bool(mark.get("placed")))

    def _selected_mark(self) -> dict | None:
        mark_id = self.selected_id()
        return next((m for m in self._marks if m["id"] == mark_id), None) if mark_id else None

    def _emit_for_selection(self, signal) -> None:
        mark_id = self.selected_id()
        if mark_id:
            signal.emit(mark_id)

    def _on_item_clicked(self, item: QTableWidgetItem) -> None:
        cell = self.table.item(item.row(), COL_TIME)
        mark_id = cell.data(_ID_ROLE) if cell is not None else None
        mark = next((m for m in self._marks if m["id"] == mark_id), None)
        if mark is not None and mark.get("placed"):
            self.markActivated.emit(mark_id)


def _row_tooltip(mark: dict) -> str:
    """One mark's full story for the hover: what it is, when, how long, and who said so."""
    lines = [f"{marks_model.TYPE_LABEL.get(mark['type'], 'Mark')} — {marks_model.summary(mark)}"]
    if mark.get("placed"):
        span = marks_model.duration(mark)
        lines.append(_time_text(mark) + (f"  ·  {span:.1f} s" if span else ""))
    else:
        lines.append(f"This mark is in {mark.get('chapter')}, which this open does not include — "
                     "open the full recording to see it on the bar.")
    lines.append("Found by pacer, and re-checked on every open."
                 if mark["kind"] == marks_model.KIND_AUTO else "You wrote this.")
    return "\n".join(lines)


class MarkDialog(QDialog):
    """The mark editor: type · colour · note, over a mark that already knows WHEN it is.

    The time is shown and not editable, deliberately. A mark's position is authored by the
    playhead — you put it where you are — and a spin box over a media clock is a worse instrument
    than the video itself. Moving one means deleting it and marking again at the right second, which
    takes two keystrokes; "Extend to playhead" on the page is how a mark grows into a range."""

    def __init__(self, mark: dict, parent=None, *, title: str = "Add a mark"):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — {title.lower()}")
        self._mark = dict(mark)

        self.type_combo = QComboBox()
        for key in marks_model.MANUAL_TYPES:
            self.type_combo.addItem(marks_model.TYPE_LABEL[key], key)
        self.type_combo.setCurrentIndex(
            max(marks_model.MANUAL_TYPES.index(mark["type"])
                if mark["type"] in marks_model.MANUAL_TYPES else 0, 0))
        # Changing the TYPE re-picks the colour, but only while the colour is still the old type's
        # default: a driver who deliberately made this one lime keeps lime.
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)

        self.colour_combo = QComboBox()
        for name in marks_model.COLOURS:
            if name in ("warn", "bad"):
                continue   # the two SEMANTIC hues belong to the derived marks — see marks.COLOURS
            self.colour_combo.addItem(name.capitalize(), name)
        self._select_colour(mark.get("colour") or marks_model.DEFAULT_COLOUR)

        self.note_edit = QPlainTextEdit(mark.get("note") or "")
        self.note_edit.setPlaceholderText("What happened here?")
        self.note_edit.setTabChangesFocus(True)

        when = QLabel(_time_text(mark))
        when.setProperty("role", "BarLabel")

        form = QFormLayout()
        form.addRow("At", when)
        form.addRow("Type", self.type_combo)
        form.addRow("Colour", self.colour_combo)
        form.addRow("Note", self.note_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        column = QVBoxLayout(self)
        column.setContentsMargins(theme.SPACE_M, theme.SPACE_M, theme.SPACE_M, theme.SPACE_M)
        column.setSpacing(theme.SPACE_S)
        column.addLayout(form)
        column.addWidget(buttons)
        self.note_edit.setFocus()

    def _on_type_changed(self) -> None:
        previous = self._mark.get("type", marks_model.TYPE_NOTE)
        if self.colour_combo.currentData() == marks_model.TYPE_COLOUR.get(previous):
            self._select_colour(marks_model.TYPE_COLOUR.get(self.type_combo.currentData(),
                                                            marks_model.DEFAULT_COLOUR))
        self._mark["type"] = self.type_combo.currentData()

    def _select_colour(self, name: str) -> None:
        index = self.colour_combo.findData(name)
        self.colour_combo.setCurrentIndex(index if index >= 0 else 0)

    def result_mark(self) -> dict:
        """The edited mark — the one the caller persists. The ANCHOR fields come through untouched:
        this dialog edits what a mark SAYS, never where it is."""
        return {**self._mark,
                "type": self.type_combo.currentData(),
                "colour": self.colour_combo.currentData(),
                "note": self.note_edit.toPlainText().strip()}
