"""The auto coaching "Opportunities" dialog (F10): where to find time vs your own best lap.

A read-only QDialog over a precomputed ``coaching.Opportunities`` (no analysis here). PACER-FREE:
only the ``coaching`` dataclasses + ``coaching.reason_sentence``. Each row's Jump button calls the
injected ``jump_to(cid, entry_dist)`` (the app selects the corner + seeks the best lap to its
entry). When ``opportunities.enough`` is False the table is a friendly "need more laps" message.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import coaching, theme, units
from .lap_table import CORNER_DIR_GLYPH
from .theme import C

if TYPE_CHECKING:  # the injected session — typed for readers, not imported at runtime
    from .session import Session

# column indices
_COL_CORNER, _COL_LOST, _COL_SIGMA, _COL_PHASES, _COL_REASON, _COL_GO = range(6)
_HEADERS = ["Corner", "Time lost", "±σ", "Entry · Apex · Exit", "How to find it", ""]

# Human label per coaching.PHASE_* id, in track order (for the breakdown bar segments + tooltip).
_PHASE_LABEL = {coaching.PHASE_ENTRY: "Entry", coaching.PHASE_APEX: "Apex",
                coaching.PHASE_EXIT: "Exit"}

# A short, friendly per-reason hint shown as the row tooltip (the sentence already carries the
# numbers; this explains what the lever IS). Keyed by the coaching.REASON_* ids.
_REASON_TIP = {
    coaching.REASON_APEX: "Your typical lap's minimum (apex) speed here is below your best "
                          "lap's — carry more speed through the slowest point.",
    coaching.REASON_BRAKING: "You spend longer on the brakes into this corner than on your best "
                             "lap — brake later and/or release sooner.",
    coaching.REASON_COASTING: "There's a coasting phase here (neither braking nor on throttle) "
                              "your best lap doesn't have — get back to throttle sooner.",
    coaching.REASON_LINE: "The loss here is mostly inconsistency (lap-to-lap spread) rather than "
                          "one fixable input — repeat the same line.",
    coaching.REASON_NONE: "Time is available here versus your best lap.",
}


class PhaseBar(QWidget):
    """A tiny horizontal entry/apex/exit Δt-vs-best breakdown for one corner (D2): three
    proportional segments (widths ∝ each third's seconds of loss) over the row's three numbers.
    Only the phases LOSING time (positive Δt) get a coloured segment; faster-than-best thirds are
    shown as a near-zero sliver. Read-only; the segment widths are the visual cue, the small
    numbers underneath the precise values, the tooltip the full breakdown."""

    _BAR_H = 6  # px; the proportional bar's height (the numbers sit below it)

    def __init__(self, phases: coaching.PhaseLoss, parent=None):
        super().__init__(parent)
        self._phases = phases
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(2)

        vals = phases.as_tuple()                      # (entry, apex, exit) seconds
        dominant = phases.dominant
        ids = (coaching.PHASE_ENTRY, coaching.PHASE_APEX, coaching.PHASE_EXIT)
        pos = [max(v, 0.0) for v in vals]            # only losses size the bar
        scale = sum(pos)

        # proportional bar
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(1)
        for pid, v, p in zip(ids, vals, pos, strict=True):
            seg = QWidget()
            seg.setFixedHeight(self._BAR_H)
            # stretch ∝ the third's loss; a tiny floor so a flat row still shows three slivers
            bar.addWidget(seg, max(int(round(p / scale * 100)), 1) if scale > 1e-9 else 1)
            losing = v > 1e-6
            col = (C.accent if pid == dominant else C.text_dim) if losing else C.border
            seg.setStyleSheet(f"background:{col}; border-radius:2px;")
        lay.addLayout(bar)

        # the three numbers under the bar (the dominant one accented)
        nums = QHBoxLayout()
        nums.setContentsMargins(0, 0, 0, 0)
        nums.setSpacing(4)
        num_font = theme.mono_font(theme.CAPTION)
        for pid, v in zip(ids, vals, strict=True):
            lbl = QLabel(f"{v:+.2f}")
            lbl.setFont(num_font)
            lbl.setAlignment(Qt.AlignCenter)
            colour = C.accent if (pid == dominant and v > 1e-6) else C.text_dim
            lbl.setStyleSheet(f"color:{colour};")
            nums.addWidget(lbl, 1)
        lay.addLayout(nums)

        self.setToolTip(
            "Time lost vs your best lap, split across the corner (s):\n"
            + "   ".join(f"{_PHASE_LABEL[p]} {v:+.2f}" for p, v in zip(ids, vals, strict=True))
            + f"\nTotal {phases.total:+.2f} s — biggest loss on {_PHASE_LABEL[dominant].lower()}.")


# D4: below this many metres the brake-point delta is within the estimate's noise — show no hint.
BRAKE_HINT_MIN_M = 2.0


def _brake_point_hint(bp) -> str | None:
    """A short, ESTIMATED braking-point coaching line for a corner's driving.BrakePoint, or None
    when the metres are negligible (< BRAKE_HINT_MIN_M — within the estimate's noise). Positive
    metres_later => "brake later"; negative => "brake earlier". Labelled ESTIMATED (constant-decel
    assumption at the session's demonstrated peak braking)."""
    m = float(bp.metres_later)
    if abs(m) < BRAKE_HINT_MIN_M:
        return None
    if m > 0:
        return f"Brake ~{m:.0f} m later into C{bp.cid} (EST)"
    return f"Brake ~{abs(m):.0f} m earlier into C{bp.cid} (EST)"


# --- shared per-row cell builders (the modal dialog AND the persistent panel render rows the SAME
# way, so the corner / time-lost / reason cells can't drift between the two surfaces). ---
def _corner_cell(opp: coaching.Opportunity) -> QTableWidgetItem:
    """The 'C<n> <dir-glyph>' corner cell (read-only)."""
    glyph = CORNER_DIR_GLYPH.get(opp.direction, "")
    item = QTableWidgetItem(f"C{opp.cid} {glyph}")
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


def _lost_cell(opp: coaching.Opportunity, num_font) -> QTableWidgetItem:
    """The '+<t> s' time-lost cell (right-aligned, red = time given away)."""
    item = QTableWidgetItem(f"+{opp.time_lost:.2f} s")
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    item.setFont(num_font)
    item.setForeground(QColor(theme.delta_colour(opp.time_lost)))
    return item


def _sigma_cell(opp: coaching.Opportunity, num_font) -> QTableWidgetItem:
    """The lap-to-lap consistency cell (±σ s): the σ of time-in-corner over the clean laps, folded
    onto the CANONICAL coaching row so 'how much time' (the lost cell) and 'how repeatable' read
    together — the Consistency panel's signal on the same rows, so the two surfaces can't disagree.
    Small σ = repeatable; large = time left on the table inconsistently here."""
    item = QTableWidgetItem(f"±{opp.reason.sigma:.2f}")
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    item.setFont(num_font)
    item.setForeground(QColor(C.text_dim))
    item.setToolTip(
        "Lap-to-lap consistency through this corner: σ of time-in-corner over your clean laps. "
        "Small = repeatable; large = you're inconsistently leaving time here (the Consistency "
        "panel ranks corners by σ × median loss).")
    return item


def _reason_cell(opp: coaching.Opportunity, brake_points: dict,
                 speed_unit: str | None = None) -> QTableWidgetItem:
    """The 'How to find it' reason cell: the coaching sentence (apex deficit in `speed_unit`, km/h
    default) + (when a braking-point estimate is available for this corner) the ESTIMATED 'brake
    ~N m' line, with the per-reason tooltip."""
    sentence = coaching.reason_sentence(opp, speed_unit)
    bp = brake_points.get(opp.cid)
    hint = _brake_point_hint(bp) if bp is not None else None
    item = QTableWidgetItem(f"{sentence}\n{hint}" if hint else sentence)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    tip = _REASON_TIP.get(opp.reason.kind, "")
    if hint is not None:
        tip = (f"{tip}\n\n{hint}: the apex-speed-matched latest sustainable brake point is "
               f"~{bp.optimal_brake_dist:.0f} m; you brake at ~{bp.actual_brake_dist:.0f} m. "
               "ESTIMATED (constant decel at this session's demonstrated peak braking).")
    item.setToolTip(tip)
    return item


class OpportunitiesDialog(QDialog):
    """Coaching ▸ Opportunities dialog over a freshly-computed ``coaching.Opportunities``.
    jump_to(cid, entry_dist) fires on a row's Jump button; None disables them (headless layout
    tests). `brake_points` (optional, cid -> driving.BrakePoint for the best lap) appends a light
    ESTIMATED "brake ~N m later" line to a row's reason (D4)."""

    def __init__(self, opportunities: coaching.Opportunities,
                 jump_to: Callable[[int, float], None] | None = None,
                 brake_points: dict | None = None,
                 parent=None, speed_unit: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("pacer studio — opportunities")
        self.resize(560, 320)
        self._opps = opportunities
        self._jump_to = jump_to
        self._brake_points = brake_points or {}
        # Speed display unit (km/h default) for the reason sentence's apex deficit; opened fresh
        # per view so it's fixed at construction (no live flip needed on a modal).
        self._speed_unit = speed_unit

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        if opportunities.enough and opportunities.rows:
            n = opportunities.n_laps
            lap = opportunities.median_lap_id
            title = QLabel(f"Biggest gains vs your best lap — median of {n} clean laps"
                           + (f" (typical lap {lap})" if lap is not None else ""))
        else:
            title = QLabel("Opportunities")
        title.setProperty("role", "PanelHeader")
        title.setWordWrap(True)
        root.addWidget(title)

        if not (opportunities.enough and opportunities.rows):
            root.addWidget(self._empty_state(opportunities), 1)
        else:
            root.addWidget(self._build_table(opportunities), 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

    # ------------------------------------------------------------------ states
    def _empty_state(self, opps: coaching.Opportunities) -> QWidget:
        """Friendly message for the two no-table cases: too few clean laps, or no corner losing
        time."""
        if not opps.enough:
            msg = (f"Need at least {coaching.MIN_LAPS} clean (valid, GPS-dropout-free) laps to "
                   f"find coaching opportunities.\nThis session has {opps.n_laps}. "
                   "Drive a few more laps and reload.")
        else:
            msg = ("No corner is losing time versus your best lap on your typical lap — your "
                   "best-lap pace is consistent across the lap. Nice driving.")
        label = QLabel(msg)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet(f"color: {C.text_dim};")
        return label

    def _build_table(self, opps: coaching.Opportunities) -> QWidget:
        table = QTableWidget(len(opps.rows), len(_HEADERS))
        table.setHorizontalHeaderLabels(_HEADERS)
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QAbstractItemView.NoSelection)  # read-only; Jump is the only action
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setFocusPolicy(Qt.NoFocus)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setDefaultSectionSize(40)
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(_COL_REASON, QHeaderView.Stretch)
        for col in (_COL_CORNER, _COL_LOST, _COL_SIGMA, _COL_GO):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        # The D2 phase breakdown bar wants a stable width (the segments are proportional).
        hdr.setSectionResizeMode(_COL_PHASES, QHeaderView.Fixed)
        table.setColumnWidth(_COL_PHASES, 150)
        num_font = theme.mono_font(theme.TABLE)

        for r, opp in enumerate(opps.rows):
            table.setItem(r, _COL_CORNER, _corner_cell(opp))
            table.setItem(r, _COL_LOST, _lost_cell(opp, num_font))
            table.setItem(r, _COL_SIGMA, _sigma_cell(opp, num_font))  # lap-to-lap consistency σ
            table.setCellWidget(r, _COL_PHASES, PhaseBar(opp.phases))  # D2 entry/apex/exit Δt
            table.setItem(r, _COL_REASON, _reason_cell(opp, self._brake_points, self._speed_unit))
            table.setCellWidget(r, _COL_GO, self._go_button(opp))
        self.table = table  # exposed for the tests
        return table

    def _go_button(self, opp: coaching.Opportunity) -> QPushButton:
        """Per-row jump-to button; captures (cid, entry_dist) and calls the injected `jump_to`.
        Disabled when no callback was injected (headless layout tests)."""
        # Phosphor arrow icon + "Jump" (the Unicode arrow didn't render); primary CTA styling.
        btn = QPushButton(theme.icon("ph.arrow-right", color=C.on_accent), "Jump")
        btn.setProperty("variant", "primary")
        btn.setMinimumWidth(88)
        btn.setToolTip(f"Select C{opp.cid} on the map and jump the video to your best lap's "
                       "entry to this corner")
        if self._jump_to is None:
            btn.setEnabled(False)
        else:
            cid, entry = opp.cid, opp.entry_dist
            btn.clicked.connect(lambda _checked=False, c=cid, d=entry: self._jump_to(c, d))
        return btn


# How many opportunities the always-on panel surfaces (the actionable shortlist — the full ranking
# stays in the modal dialog). Compact by design so it never crowds the 2x2 studio.
PANEL_TOP_N = 3
# Panel body height bounds (resizable splitter section in central_view, like the consistency strip).
PANEL_BODY_HEIGHT = 132   # px; natural/default height (~3 rows + a slim column header)
PANEL_BODY_MIN_HEIGHT = 64   # px; below this the list scrolls instead of vanishing


class OpportunitiesPanel(QWidget):
    """The PERSISTENT, always-on coaching summary (the front-door surface): a compact collapsible
    strip showing the TOP-3 opportunities (corner · time lost · dominant reason) over a freshly
    computed ``coaching.Opportunities``. Mirrors the consistency strip's pattern (header + chevron +
    bounded body); the full modal ``OpportunitiesDialog`` stays available for the detail.

    Reads ONLY session accessors (``coaching_opportunities`` + ``coaching_brake_points``) — no
    analysis here. Refreshed on load / lap-selection / re-segmentation (never on the 30 Hz tick).
    A row click emits ``corner_clicked(cid)`` so the app can ring the corner's apex on the map (the
    Jump-to-corner detail action stays in the modal dialog). Honours the existing ESTIMATED
    labelling (the ``(EST)`` brake-point lines via ``_reason_cell``) and the friendly "need more
    laps" state when there aren't enough clean laps."""

    # Clicked corner cid (None on deselect) -> the map apex-ring highlight (wired in central_view).
    corner_clicked = Signal(object)

    _COLUMNS = ["Corner", "Time lost", "±σ", "How to find it"]

    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self._num_font = theme.mono_font(theme.TABLE)
        self._cids: list[int] = []  # row -> corner cid, set in refresh()
        # Speed display unit (km/h default) for the reason sentence's apex deficit; pushed by the
        # window's Units toggle via set_speed_unit.
        self._speed_unit = units.DEFAULT_UNIT

        # --- header: title · headline summary · collapse chevron (the consistency-strip pattern).
        title = QLabel("OPPORTUNITIES")
        title.setProperty("role", "BarLabel")
        self.summary_label = QLabel("")  # "0.42 s in 3 corners …" — set in refresh()
        self.summary_label.setProperty("role", "BarLabel")
        self.summary_label.setToolTip(
            "The biggest realistic time gains vs your own best lap (median of your clean, "
            "GPS-dropout-free laps). Open Coaching ▸ Opportunities… for the full ranking + jump-to.")
        self.collapse_btn = QPushButton("▾")
        self.collapse_btn.setCheckable(True)  # checked = collapsed
        self.collapse_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.collapse_btn.setToolTip("Collapse / expand the opportunities panel")
        self.collapse_btn.toggled.connect(self._on_collapse)
        header = QWidget()
        header.setProperty("role", "PanelHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(8)
        row.addWidget(title)
        row.addStretch(1)
        row.addWidget(self.summary_label)
        row.addWidget(self.collapse_btn)

        # --- body: a stack of {top-3 table, friendly "need more laps" label}, swapped in refresh().
        self.table = QTableWidget(0, len(self._COLUMNS))
        self.table.setHorizontalHeaderLabels(self._COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setDefaultSectionSize(34)
        hdr = self.table.horizontalHeader()
        hdr.setStretchLastSection(True)  # the reason column takes the slack
        for col in (0, 1, 2):  # corner · time-lost · σ size to content; reason (last) stretches
            hdr.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._on_row_selected)

        self.empty_label = QLabel("")
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(f"color: {C.text_dim};")

        self.body = QStackedWidget()
        self.body.addWidget(self.table)        # index 0 — the top-3 rows
        self.body.addWidget(self.empty_label)  # index 1 — the friendly excluded state
        self.body.setMinimumHeight(PANEL_BODY_MIN_HEIGHT)
        self.body.setMaximumHeight(PANEL_BODY_HEIGHT)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(header)
        lay.addWidget(self.body)
        self.refresh()

    # ------------------------------------------------------------------ build
    def refresh(self):
        """Recompute the opportunities from the session and rebuild the top-3 rows (or the friendly
        excluded state). Called on load / lap-selection / re-segmentation — never on the 30 Hz tick.
        Clears any held row selection (a stale cid would mis-ring the map)."""
        opps = self.session.coaching_opportunities()
        brake_points = self.session.coaching_brake_points()
        if opps.enough and opps.rows:
            self._fill_rows(opps, brake_points)
        else:
            self._show_excluded(opps)

    def set_speed_unit(self, unit: str):
        """Switch the reason sentence's apex-deficit unit live: re-fill the rows. No-op if
        unchanged."""
        unit = units.normalize_unit(unit)
        if unit == self._speed_unit:
            return
        self._speed_unit = unit
        self.refresh()

    def _fill_rows(self, opps: coaching.Opportunities, brake_points: dict):
        """Populate the compact top-3 table from `opps.rows` (shared cell builders, so a row reads
        identically to the modal dialog) and the headline summary."""
        rows = opps.rows[:PANEL_TOP_N]
        total = sum(r.time_lost for r in rows)
        self.summary_label.setText(f"{total:.2f} s across the top {len(rows)}")

        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setRowCount(len(rows))
        self._cids = [opp.cid for opp in rows]
        for r, opp in enumerate(rows):
            self.table.setItem(r, 0, _corner_cell(opp))
            self.table.setItem(r, 1, _lost_cell(opp, self._num_font))
            self.table.setItem(r, 2, _sigma_cell(opp, self._num_font))  # consistency σ on the row
            self.table.setItem(r, 3, _reason_cell(opp, brake_points, self._speed_unit))
        self.table.blockSignals(False)
        self.body.setCurrentIndex(0)

    def _show_excluded(self, opps: coaching.Opportunities):
        """Show the friendly "need more laps" / "no corner losing time" state (NOT an empty box),
        matching the modal dialog's wording so the two surfaces read the same."""
        self._cids = []
        if not opps.enough:
            msg = (f"Drive at least {coaching.MIN_LAPS} clean (valid, GPS-dropout-free) laps to "
                   f"surface coaching opportunities — this session has {opps.n_laps}.")
        else:
            msg = ("No corner is losing time vs your best lap on your typical lap — your best-lap "
                   "pace is consistent. Nice driving.")
        self.summary_label.setText("")
        self.empty_label.setText(msg)
        self.body.setCurrentIndex(1)

    # ------------------------------------------------------------- interaction
    def _on_row_selected(self):
        """Emit the clicked row's corner cid (None on deselect). The map apex-ring is the only
        consumer — read-only panel, no seek/lap-selection side effects (the Jump-to-corner detail
        action lives in the modal dialog)."""
        rows = self.table.selectionModel().selectedRows()
        if rows and 0 <= rows[0].row() < len(self._cids):
            self.corner_clicked.emit(self._cids[rows[0].row()])
        else:
            self.corner_clicked.emit(None)

    def _on_collapse(self, collapsed: bool):
        """Hide/show the body; the header strip (with the headline summary) stays. The chevron flips
        so the affordance reads the right way in both states."""
        self.body.setVisible(not collapsed)
        self.collapse_btn.setText("▸" if collapsed else "▾")
