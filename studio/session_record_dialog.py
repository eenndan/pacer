"""The session-record editor: the form that makes cross-session comparison mean anything.

A self-contained QDialog over ONE ``studio.session_record`` record dict. It does no file I/O of
its own — the caller loads the store, hands in the record (or ``session_record.prefill``'s
pre-populated blank) and takes the edited record back from ``result_record()`` — so the dialog
stays hermetic in tests and the app owns every write, the same dependency-injected split
``library_dialog`` uses.

    ┌────────────────────────────────────────────────┐
    │  Daytona MK · 2026-06-14 · 22 laps · 1:08.201  │ ← AUTO-STAMPED: what the app already knows
    │  Carried over from your last session…          │ ← only when prefill actually filled something
    │  CONDITIONS                                    │
    │    Conditions   [Dry ▾]                        │
    │    Air / track  [24 ] [38 ] °C                 │
    │    Humidity     [41 ] %                        │
    │  TYRES                                         │
    │    Set          [MG Yellow #3            ]     │
    │    Laps on them [42 ]  → 64 after this session │ ← the app does the arithmetic, not the driver
    │    Cold F / R   [10 ] [10.5]  [psi ▾]          │
    │    Hot  F / R   [13 ] [13.5]                   │
    │  KART                                          │
    │    Chassis / Sprockets / Axle / Seat           │
    │  NOTES                                         │
    │    [                                    ]      │
    │  [Delete record]          [Cancel] [ Save ]    │
    └────────────────────────────────────────────────┘

FAST TO FILL IN IS THE WHOLE TEST — a form nobody completes is worse than nothing, because a
half-kept notebook makes "were these two sessions comparable?" unanswerable in a NEW way. Four
things are spent on that and nothing else:

  * THE KART IS ALREADY IN THE FORM. A new record opens pre-populated from the driver's last one
    (``session_record.prefill``: chassis, axle, seat, gearing, tyre set, pressure unit) because
    none of that changed overnight — and the tyre laps have advanced themselves by the last
    session's lap count. What is carried is SAID, in one line, so nothing is asserted on the
    driver's behalf without his seeing it.
  * NOTHING IS REQUIRED. Every field may stay blank; a blank field is stored as "not recorded",
    never as zero. Saving a form with nothing in it REMOVES the record rather than writing an
    all-dashes row that would claim the session was documented.
  * THE CONDITIONS COME FIRST AND TAKE THE FOCUS. They are the fields that decay — the coach's
    whole point is that nobody remembers the weather 18 months later — and the ones the Library
    filters on.
  * RETURN SAVES, ESCAPE CANCELS (Qt's default/reject buttons; no new app shortcut, so nothing is
    added to the ``help_dialog`` reference). The notes box takes its own Return, so a paragraph
    cannot be cut short by the save key.

NO NETWORK, DELIBERATELY: see the ``session_record`` module docstring. Every number here is typed
by the driver. The privacy line at the foot of the form says so on the surface that collects it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, session_record, theme
from ._signal import fmt_time, plural

# The conditions combo's first row: no tag. A record with no conditions tag is the normal state of
# a record that documents the KART and not the day, so "—" is a legal answer, not a prompt.
_NO_CONDITION = "—"

# What the numeric fields will accept. Wide enough for any real value, narrow enough that a
# mis-keyed decimal cannot be typed: air/track °C is bounded by the extremes a kart is driven in,
# pressure by the range of a kart gauge in either unit, humidity by its own definition. The bounds
# are a typo guard, not a physics claim — the validator only refuses keystrokes that could not be
# any of the values this field is for.
_TEMP_RANGE = (-40.0, 90.0)
_PRESSURE_RANGE = (0.0, 60.0)
_HUMIDITY_RANGE = (0.0, 100.0)
_DECIMALS = 2
# Counted fields: laps on a tyre set, and a sprocket's teeth.
_LAPS_MAX = 9999
_TEETH_MAX = 199


def _num_edit(low: float, high: float, placeholder: str = "") -> QLineEdit:
    """A decimal field: a plain line edit that will not accept a non-number. A QLineEdit rather
    than a QDoubleSpinBox because a spin box cannot be EMPTY — it always shows a value, and the
    difference between "10 psi" and "I did not record the pressure" is the whole point of this
    form (a spin box would silently turn every unrecorded field into a measurement of zero)."""
    edit = QLineEdit()
    validator = QDoubleValidator(low, high, _DECIMALS)
    validator.setNotation(QDoubleValidator.StandardNotation)
    edit.setValidator(validator)
    edit.setPlaceholderText(placeholder)
    edit.setClearButtonEnabled(True)
    return edit


def _int_edit(high: int, placeholder: str = "") -> QLineEdit:
    """A counted field (laps, teeth) — same empty-is-meaningful reasoning as ``_num_edit``."""
    edit = QLineEdit()
    edit.setValidator(QIntValidator(0, high))
    edit.setPlaceholderText(placeholder)
    edit.setClearButtonEnabled(True)
    return edit


def _pair_row(*widgets: QWidget) -> QWidget:
    """Two (or three) controls that are ONE idea on one form row — front and rear, air and track.
    They share the row's width rather than each picking a size, so the form has no hand-chosen
    field widths to drift (and the dimensional guard has nothing to catch)."""
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(theme.SPACE_S)
    for w in widgets:
        lay.addWidget(w, 1)
    return row


def _read_num(edit: QLineEdit) -> float | None:
    """A decimal field's value, or None when it is blank / unparseable. Tolerant of a comma
    decimal: a European driver typing "1,05 bar" means 1.05, and a validator built on the C locale
    would otherwise hand this back an unparseable string it had itself allowed through."""
    text = edit.text().strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _read_int(edit: QLineEdit) -> int | None:
    """A counted field's value, or None when blank / unparseable."""
    text = edit.text().strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _write_num(edit: QLineEdit, value) -> None:
    """Put a stored number back in a field, in the shortest honest form ("24", not "24.0") — the
    same rendering ``session_record._fmt_num`` gives the summaries, so a record reads identically
    in the form that captured it and in the Library row that quotes it."""
    edit.setText(session_record._fmt_num(value) if value is not None else "")


def context_line(entry: dict | None) -> str:
    """The AUTO-STAMPED header: what pacer already knows about this session, so the driver never
    types it. ``"Daytona MK  ·  2026-06-14  ·  22 laps  ·  best 1:08.201"``, with each clause
    dropped when the library entry has nothing for it (an unknown track, a GPS5 recording with no
    date). Read from the LIBRARY ENTRY, which is re-written on every re-open and is therefore the
    authoritative copy — the record's own stamped copy is provenance for reading the file
    standalone (see ``session_record.stamp_context``)."""
    if not entry:
        return ""
    parts = []
    if entry.get("track"):
        parts.append(str(entry["track"]))
    if entry.get("date"):
        parts.append(str(entry["date"]))
    laps = entry.get("lap_count")
    if isinstance(laps, int) and laps:
        parts.append(plural(laps, "lap"))
    best = entry.get("best")
    if best is not None:
        parts.append(f"best {fmt_time(best)}")
    return "  ·  ".join(parts)


def carried_over_line(rec: dict | None) -> str:
    """The one line that says what ``prefill`` put in the form before the driver saw it, or "" when
    it filled nothing (the first record ever, or a last session that recorded no kart).

    It exists because a pre-filled form is a claim: leaving "OTK 401R · 11/82 · axle H" sitting in
    a new record unannounced would let a chassis change go unrecorded simply because nobody
    noticed the field was already answered. Naming the carry makes correcting it the obvious next
    move, which is what keeps the speed from costing accuracy."""
    kart = session_record.kart_text(rec)
    tyres = session_record.tyre_text(rec)
    carried = "  ·  ".join(c for c in (tyres, kart) if c)
    if not carried:
        return ""
    return f"Carried over from your last session: {carried} — correct anything that changed."


class SessionRecordDialog(QDialog):
    """The session-record form. `record` is the existing ``session_record`` record for this
    recording, or a ``prefill``-ed blank for one that has none; `entry` is the recording's library
    entry (for the auto-stamped context line) and may be None. `name` is the recording's filename,
    shown in the title so two same-day sessions are told apart.

    `is_new` says which of the two the record is — it drives the "carried over" line and whether a
    Delete control is offered at all. The dialog NEVER writes: ``result_record()`` hands the edited
    record back and ``deleted()`` reports the delete request, and the caller (the app / the Library
    dialog) owns the store write."""

    def __init__(self, record: dict, entry: dict | None = None, name: str = "",
                 is_new: bool = True, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — session record" + (f": {name}" if name else ""))
        self._record = session_record._norm_record(record or {})
        self._entry = entry
        self._is_new = is_new
        self._deleted = False

        root = QVBoxLayout(self)
        # A CONTROL surface, operated rather than read — the same gutter the export options dialog
        # takes, for the same reason (see export_controller._ask_export_options).
        root.setContentsMargins(theme.SPACE_M, theme.SPACE_M, theme.SPACE_M, theme.SPACE_M)
        root.setSpacing(theme.SPACE_M)

        context = context_line(entry)
        if context:
            label = QLabel(context)
            label.setProperty("role", "Note")
            label.setFont(theme.mono_font(theme.TABLE_HEADER))
            root.addWidget(label)
        carried = carried_over_line(self._record) if is_new else ""
        if carried:
            hint = QLabel(carried)
            hint.setWordWrap(True)
            hint.setProperty("role", "Hint")
            root.addWidget(hint)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(theme.SPACE_M)
        form.setVerticalSpacing(theme.SPACE_S)
        # Labels wrap rather than force the dialog wider — this form has more rows than the app's
        # other one and no column of it is allowed to set the window's width on its own.
        form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._build_conditions(form)
        self._build_tyres(form)
        self._build_kart(form)
        root.addLayout(form)

        notes_header = QLabel("NOTES")
        notes_header.setProperty("role", "PanelHeader")
        root.addWidget(notes_header)
        self.notes = QPlainTextEdit(self._record.get("notes") or "")
        self.notes.setPlaceholderText(
            "Anything the fields above don't hold — how the kart felt, what you changed mid-session, "
            "who else was out.")
        root.addWidget(self.notes, 1)

        privacy = QLabel(
            "Typed by you and stored on this Mac only — pacer never looks conditions up online. "
            "Blank fields are simply not recorded.")
        privacy.setWordWrap(True)
        privacy.setProperty("role", "Hint")
        root.addWidget(privacy)

        buttons = QHBoxLayout()
        buttons.setSpacing(theme.SPACE_S)
        # Delete only exists once there IS a record — an editor for a session that has none has
        # nothing to delete, and a permanently-greyed control on a first-run form is noise. It sits
        # away from Save/Cancel, the placement the Library dialog gives its own destructive wipe.
        if not is_new:
            self.delete_btn = QPushButton("Delete record")
            self.delete_btn.setToolTip(
                "Remove this recording's setup + conditions record. Your video, its timing lines "
                "and its library row are not touched.")
            self.delete_btn.clicked.connect(self._on_delete)
            buttons.addWidget(self.delete_btn)
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.save_btn = QPushButton("Save")
        # Return saves. A QPlainTextEdit consumes its own Return, so the notes box keeps its
        # paragraphs — which is why the notes field is the one control that does not lose the key.
        self.save_btn.setDefault(True)
        self.save_btn.setAutoDefault(True)
        self.save_btn.clicked.connect(self._on_save)
        buttons.addWidget(cancel)
        buttons.addWidget(self.save_btn)
        root.addLayout(buttons)

        # The conditions are what decays and what the Library filters on, so the caret starts
        # there — a driver who fills in one thing after a session should fill in that one.
        self.conditions.setFocus()

    # ------------------------------------------------------------------ sections
    def _section(self, form: QFormLayout, title: str) -> None:
        """A section rule inside the form — the app's panel-header role, so the four groups read
        as the four questions they are rather than as one 13-row wall."""
        label = QLabel(title)
        label.setProperty("role", "PanelHeader")
        form.addRow(label)

    def _build_conditions(self, form: QFormLayout) -> None:
        """The day. First, because it is the half that cannot be recovered later."""
        self._section(form, "CONDITIONS")
        self.conditions = QComboBox()
        self.conditions.addItem(_NO_CONDITION, "")
        for tag in session_record.CONDITIONS:
            self.conditions.addItem(session_record.CONDITION_LABELS[tag], tag)
        idx = self.conditions.findData(self._record.get("conditions") or "")
        self.conditions.setCurrentIndex(max(0, idx))
        self.conditions.setToolTip(
            "The one field the Library filters on — a dry-day best and a wet-day best are not the "
            "same measurement")
        form.addRow("Conditions", self.conditions)

        self.air = _num_edit(*_TEMP_RANGE, "air")
        self.track_temp = _num_edit(*_TEMP_RANGE, "track")
        _write_num(self.air, self._record.get("air_temp_c"))
        _write_num(self.track_temp, self._record.get("track_temp_c"))
        form.addRow("Air / track °C", _pair_row(self.air, self.track_temp))

        self.humidity = _num_edit(*_HUMIDITY_RANGE, "%")
        _write_num(self.humidity, self._record.get("humidity_pct"))
        form.addRow("Humidity %", self.humidity)

    def _build_tyres(self, form: QFormLayout) -> None:
        """The tyres — identity AND age, the two the coach's answer turns on."""
        self._section(form, "TYRES")
        self.tyre_set = QLineEdit(self._record.get("tyre_set") or "")
        self.tyre_set.setPlaceholderText("e.g. MG Yellow #3")
        self.tyre_set.setClearButtonEnabled(True)
        form.addRow("Set", self.tyre_set)

        self.tyre_laps = _int_edit(_LAPS_MAX, "laps before today")
        if self._record.get("tyre_laps") is not None:
            self.tyre_laps.setText(str(self._record["tyre_laps"]))
        # The running total, computed rather than asked for: "42" in the box plus this session's
        # own lap count is what the set will have done when the driver next opens this form, and
        # it is the number he is least likely to keep accurately by hand.
        self._tyre_after = QLabel("")
        self._tyre_after.setProperty("role", "Hint")
        self.tyre_laps.textChanged.connect(self._sync_tyre_after)
        form.addRow("Laps on them", _pair_row(self.tyre_laps, self._tyre_after))
        self._sync_tyre_after()

        self.unit = QComboBox()
        for unit in session_record.PRESSURE_UNITS:
            self.unit.addItem(unit, unit)
        unit_idx = self.unit.findData(self._record.get("pressure_unit"))
        self.unit.setCurrentIndex(max(0, unit_idx))
        self.unit.setToolTip("The unit these pressures are written in — stored with the record, so "
                             "a season read back a year later still means something")
        self.cold_front = _num_edit(*_PRESSURE_RANGE, "front")
        self.cold_rear = _num_edit(*_PRESSURE_RANGE, "rear")
        self.hot_front = _num_edit(*_PRESSURE_RANGE, "front")
        self.hot_rear = _num_edit(*_PRESSURE_RANGE, "rear")
        for edit, key in ((self.cold_front, "cold_front"), (self.cold_rear, "cold_rear"),
                          (self.hot_front, "hot_front"), (self.hot_rear, "hot_rear")):
            _write_num(edit, self._record.get(key))
        form.addRow("Cold F / R", _pair_row(self.cold_front, self.cold_rear, self.unit))
        form.addRow("Hot F / R", _pair_row(self.hot_front, self.hot_rear))

    def _build_kart(self, form: QFormLayout) -> None:
        """The kart — the fields that stay put between sessions, and therefore the ones ``prefill``
        has usually already answered."""
        self._section(form, "KART")
        self.chassis = QLineEdit(self._record.get("chassis") or "")
        self.chassis.setPlaceholderText("e.g. OTK Tony Kart 401R")
        self.chassis.setClearButtonEnabled(True)
        form.addRow("Chassis", self.chassis)

        self.sprocket_front = _int_edit(_TEETH_MAX, "engine")
        self.sprocket_rear = _int_edit(_TEETH_MAX, "axle")
        for edit, key in ((self.sprocket_front, "sprocket_front"),
                          (self.sprocket_rear, "sprocket_rear")):
            if self._record.get(key) is not None:
                edit.setText(str(self._record[key]))
        form.addRow("Sprockets F / R", _pair_row(self.sprocket_front, self.sprocket_rear))

        self.axle = QLineEdit(self._record.get("axle") or "")
        self.axle.setPlaceholderText("e.g. Medium / H / U")
        self.axle.setClearButtonEnabled(True)
        form.addRow("Axle", self.axle)

        self.seat = QLineEdit(self._record.get("seat") or "")
        self.seat.setPlaceholderText("e.g. standard, 5 mm back")
        self.seat.setClearButtonEnabled(True)
        form.addRow("Seat", self.seat)

    # ------------------------------------------------------------------ live derivations
    def _sync_tyre_after(self) -> None:
        """Keep the "→ N laps after this session" note in step with the laps field. Silent when
        either half is missing — an arithmetic hint has nothing to say without both numbers, and a
        stale sum beside an emptied field would be worse than no hint at all."""
        laps = _read_int(self.tyre_laps)
        session_laps = (self._entry or {}).get("lap_count")
        if laps is None or not isinstance(session_laps, int) or not session_laps:
            self._tyre_after.setText("")
            return
        self._tyre_after.setText(f"→ {laps + session_laps} after this session")

    # ------------------------------------------------------------------ result
    def result_record(self) -> dict:
        """The edited record, normalized — what the caller stores. Reading the widgets rather than
        keeping a live model means an accepted dialog and a rejected one are the same code path
        with one branch, and there is no half-applied state to reconcile."""
        rec = dict(self._record)
        rec.update({
            "conditions": self.conditions.currentData() or "",
            "air_temp_c": _read_num(self.air),
            "track_temp_c": _read_num(self.track_temp),
            "humidity_pct": _read_num(self.humidity),
            "tyre_set": self.tyre_set.text(),
            "tyre_laps": _read_int(self.tyre_laps),
            "pressure_unit": self.unit.currentData(),
            "cold_front": _read_num(self.cold_front),
            "cold_rear": _read_num(self.cold_rear),
            "hot_front": _read_num(self.hot_front),
            "hot_rear": _read_num(self.hot_rear),
            "chassis": self.chassis.text(),
            "sprocket_front": _read_int(self.sprocket_front),
            "sprocket_rear": _read_int(self.sprocket_rear),
            "axle": self.axle.text(),
            "seat": self.seat.text(),
            "notes": self.notes.toPlainText(),
        })
        return session_record._norm_record(session_record.stamp_context(rec, self._entry))

    def deleted(self) -> bool:
        """Whether the user asked to delete this recording's record (rather than save one). The
        caller routes an accepted dialog through ``session_record.remove_and_save`` when this is
        True — which takes a backup first, because this destroys hand-typed data."""
        return self._deleted

    def _on_delete(self) -> None:
        """Confirm, then accept the dialog with the delete flag set. Confirmed because the data is
        irreplaceable: nothing in the footage records what tyres were on the kart, so unlike every
        other row in the library this cannot be recovered by re-opening the recording."""
        ok = QMessageBox.question(
            self, "Delete session record",
            "Delete the setup and conditions recorded for this session?\n\n"
            "Nothing in the video can restore it — these are notes only you have. A copy of your "
            "records is kept as session_records.json.bak first.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        self._deleted = True
        self.accept()

    def _on_save(self) -> None:
        """Accept. An EMPTY form is a legitimate save — ``session_record.put`` turns it into a
        removal — so there is nothing to validate here and nothing to refuse: every field is
        optional by design, and a form that argued with the driver about which blanks were
        acceptable would be the form he stopped filling in."""
        self.accept()
