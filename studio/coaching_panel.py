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

from . import APP_NAME, coaching, theme, units
from ._signal import lap_label
from .lap_table import CORNER_DIR_GLYPH
from .theme import C

if TYPE_CHECKING:  # the injected session — typed for readers, not imported at runtime
    from .session import Session

# column indices
_COL_CORNER, _COL_LOST, _COL_SIGMA, _COL_PHASES, _COL_REASON, _COL_GO = range(6)
# NB (M4): "Time lost" is the cross-lap MEDIAN per-corner delta; the Entry·Apex·Exit column is a
# DIFFERENT statistic — the typical lap's Δt profile across the corner (where in the corner it wins
# or loses), which does NOT sum to "Time lost" and can even net faster. Its header must not also
# claim to be "time lost", or the two columns read as self-contradictory.
_HEADERS = ["Corner", "Time lost", "±σ", "Entry · Apex · Exit Δt", "How to find it", ""]

# L2: the time-lost cells render at 2 dp ("+{t:.2f} s"), so any loss under half a centisecond rounds
# to "+0.00 s" — an informationless "opportunity" with a live Jump button. summarize() keeps the raw
# 1e-9 ranking (used by the golden fingerprint + share card), but the DISPLAYED opportunity lists
# (dialog + panel) drop rows below the shown resolution so no "+0.00 s" row ever appears.
DISPLAY_MIN_LOST_S = 0.005  # < this rounds to +0.00 s at 2 dp — not a shown opportunity


def _shown_rows(opps: coaching.Opportunity) -> list[coaching.Opportunity]:
    """The opportunity rows worth SHOWING: those whose time_lost does not round to +0.00 s at the
    2-dp display resolution (L2). Ranking/order is preserved; only sub-resolution rows are dropped.
    Takes an ``Opportunities`` (typed loosely to avoid a runtime import cycle)."""
    return [r for r in opps.rows if r.time_lost >= DISPLAY_MIN_LOST_S]


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
    """A tiny horizontal entry/apex/exit Δt-profile for one corner on the TYPICAL lap (D2): three
    proportional segments (widths ∝ each third's seconds slower than best) over the row's three
    numbers. This is a WHERE-in-the-corner profile of the typical lap vs best — NOT the row's
    "Time lost" (a cross-lap median), which it need not sum to or even agree in sign with. Only the
    phases slower than best (positive Δt) get a coloured segment; faster-than-best thirds are shown
    as a near-zero sliver. Read-only; the segment widths are the visual cue, the small numbers
    underneath the precise values, the tooltip the full breakdown."""

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

        # M4: this bar is the TYPICAL lap's Δt profile across the corner (where in the corner it is
        # faster/slower than best), a DIFFERENT statistic from the row's "Time lost" (a cross-lap
        # median). Label it as a profile, call the sum the typical-lap NET (not "time lost"), and —
        # when that net is ≤ 0 (the typical lap is net faster over the window) — say so plainly so a
        # positive-loss headline row never reads as if the corner were net faster overall.
        net = phases.total
        if net > 1e-6:
            net_line = (f"Typical-lap net {net:+.2f} s over the window "
                        f"— slowest third: {_PHASE_LABEL[dominant].lower()}.")
        elif net < -1e-6:
            net_line = (f"Typical-lap net {net:+.2f} s over the window (net faster than best here) "
                        "— the row's Time lost is the cross-lap median, a different measure.")
        else:
            net_line = "Typical-lap net ~0 s over the window (on your best-lap pace here)."
        self.setToolTip(
            "Where in the corner your typical lap is faster/slower than your best lap "
            "(Δt per third, s) — NOT the same as the row's Time lost:\n"
            + "   ".join(f"{_PHASE_LABEL[p]} {v:+.2f}" for p, v in zip(ids, vals, strict=True))
            + "\n" + net_line)


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
    # theme.ESTIMATED_MARK is the ONE canonical inline "estimated" badge (was a stray "(EST)" here) —
    # so the brake-point hint reads the same "(est)" as the grip column / brake-throttle legend.
    mark = theme.ESTIMATED_MARK
    if m > 0:
        return f"Brake ~{m:.0f} m later into C{bp.cid} {mark}"
    return f"Brake ~{abs(m):.0f} m earlier into C{bp.cid} {mark}"


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
        self.setWindowTitle(f"{APP_NAME} — opportunities")
        # A wider default than the persistent panel: the modal carries two extra columns the panel
        # doesn't (the fixed ~150-px Entry·Apex·Exit PhaseBar + the per-row Jump button), which
        # squeeze the stretch reason column into a sliver that truncates ("brake …", "find tim…").
        # Give the reason real room so it reads as 1–2 wrapped lines, and keep the modal resizable.
        self.resize(920, 380)
        self.setMinimumWidth(720)
        self._opps = opportunities
        self._jump_to = jump_to
        self._brake_points = brake_points or {}
        # Speed display unit (km/h default) for the reason sentence's apex deficit; opened fresh
        # per view so it's fixed at construction (no live flip needed on a modal).
        self._speed_unit = speed_unit

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # L2: drop rows whose loss rounds to +0.00 s at the shown 2-dp resolution so no
        # informationless "+0.00 s" row (with a live Jump button) is listed as an opportunity.
        shown = _shown_rows(opportunities)
        if opportunities.enough and shown:
            n = opportunities.n_laps
            lap = opportunities.median_lap_id
            # `n` is a COUNT (stays as-is); `lap` is a lap ID, so it renders 1-based (lap_label).
            title = QLabel(f"Biggest gains vs your best lap — median of {n} clean laps"
                           + (f" (typical lap {lap_label(lap)})" if lap is not None else ""))
        else:
            title = QLabel("Opportunities")
        title.setProperty("role", "PanelHeader")
        title.setWordWrap(True)
        root.addWidget(title)

        if not (opportunities.enough and shown):
            root.addWidget(self._empty_state(opportunities), 1)
        else:
            root.addWidget(self._build_table(shown), 1)

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

    def _build_table(self, rows: list[coaching.Opportunity]) -> QWidget:
        table = QTableWidget(len(rows), len(_HEADERS))
        table.setHorizontalHeaderLabels(_HEADERS)
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QAbstractItemView.NoSelection)  # read-only; Jump is the only action
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setFocusPolicy(Qt.NoFocus)
        table.setAlternatingRowColors(True)
        # Word-wrap the reason cell + let each row grow to its wrapped content instead of a fixed
        # 40-px section that clips a 2nd line (the modal's extra PhaseBar + Jump columns squeeze the
        # stretch reason column, so the "How to find it" sentence wraps and MUST get the height for
        # it). Mirrors the persistent OpportunitiesPanel's now-untruncated behaviour (#66) so the two
        # coaching surfaces read consistently.
        table.setWordWrap(True)
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(_COL_REASON, QHeaderView.Stretch)
        for col in (_COL_CORNER, _COL_LOST, _COL_SIGMA, _COL_GO):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        # The D2 phase breakdown bar wants a stable width (the segments are proportional).
        hdr.setSectionResizeMode(_COL_PHASES, QHeaderView.Fixed)
        table.setColumnWidth(_COL_PHASES, 150)
        num_font = theme.mono_font(theme.TABLE)

        for r, opp in enumerate(rows):
            table.setItem(r, _COL_CORNER, _corner_cell(opp))
            table.setItem(r, _COL_LOST, _lost_cell(opp, num_font))
            table.setItem(r, _COL_SIGMA, _sigma_cell(opp, num_font))  # lap-to-lap consistency σ
            table.setCellWidget(r, _COL_PHASES, PhaseBar(opp.phases))  # D2 entry/apex/exit Δt
            table.setItem(r, _COL_REASON, _reason_cell(opp, self._brake_points, self._speed_unit))
            table.setCellWidget(r, _COL_GO, self._go_button(opp))
        # Fit each row to its wrapped-reason height at the current column widths (the reason is the
        # stretch column, so a 2-line sentence needs the extra height — same as the panel's fill).
        table.resizeRowsToContents()
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


class _HeaderBar(QWidget):
    """A PanelHeader-styled strip whose WHOLE surface is a click target: a left-click anywhere on
    the bar toggles the panel collapsed/expanded (the chevron is just the visible affordance), so a
    user re-opens the calm-collapsed coaching panel by clicking its header. The click is forwarded
    to the injected ``on_click`` only when it lands on the bar's own background — a press on a child
    button (the chevron) is handled by that child, so the two don't double-fire."""

    def __init__(self, on_click):
        super().__init__()
        self._on_click = on_click
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_click()
            event.accept()
            return
        super().mousePressEvent(event)


class OpportunitiesPanel(QWidget):
    """The PERSISTENT, always-on coaching summary (the front-door surface): a compact collapsible
    strip showing the TOP-3 opportunities (corner · time lost · dominant reason) over a freshly
    computed ``coaching.Opportunities``. Mirrors the consistency strip's pattern (header + chevron +
    bounded body); the full modal ``OpportunitiesDialog`` stays available for the detail.

    Reads ONLY session accessors (``coaching_opportunities`` + ``coaching_brake_points``) — no
    analysis here. Refreshed on load / lap-selection / re-segmentation (never on the 30 Hz tick).
    A row click emits ``corner_clicked(cid)`` so the app can ring the corner's apex on the map (the
    Jump-to-corner detail action stays in the modal dialog). Honours the shared ESTIMATED labelling
    (the ``(est)`` brake-point lines via ``_reason_cell``, from ``theme.ESTIMATED_MARK``) and the
    friendly "need more laps" state when there aren't enough clean laps."""

    # Clicked corner cid (None on deselect) -> the map apex-ring highlight (wired in central_view).
    corner_clicked = Signal(object)
    # Emitted after the user collapses/expands the panel (via the chevron OR a header click), True =
    # now collapsed. The window persists it so the calm-default collapsed state survives a reload.
    collapsed_changed = Signal(bool)

    _COLUMNS = ["Corner", "Time lost", "±σ", "How to find it"]

    def __init__(self, session: Session, collapsed: bool = True):
        super().__init__()
        self.session = session
        self._num_font = theme.mono_font(theme.TABLE)
        self._cids: list[int] = []  # row -> corner cid, set in refresh()
        # The last headline (e.g. "0.60 s across your top 3 corners"), stashed so the collapsed
        # header can read "Coaching · <headline>" — an obvious re-open affordance (P1 wording kept).
        self._headline = ""
        # Speed display unit (km/h default) for the reason sentence's apex deficit; pushed by the
        # window's Units toggle via set_speed_unit.
        self._speed_unit = units.DEFAULT_UNIT

        # --- header: title · headline summary · collapse chevron (the consistency-strip pattern).
        # The WHOLE header bar is a click target (the chevron is just the visible affordance), so a
        # user re-opens the calm-collapsed panel by clicking anywhere on its header — see
        # _HeaderBar.mousePressEvent below. The title reads "COACHING" (the product word the menu +
        # docs use) rather than the internal "OPPORTUNITIES".
        self._title = QLabel("COACHING")
        self._title.setProperty("role", "BarLabel")
        self.summary_label = QLabel("")  # "0.42 s in 3 corners …" — set in refresh()/_apply_collapsed
        self.summary_label.setProperty("role", "BarLabel")
        self.summary_label.setToolTip(
            "The biggest realistic time gains vs your own best lap (median of your clean, "
            "GPS-dropout-free laps). Open Coaching ▸ Opportunities… for the full ranking + jump-to.")
        # Chevron: the ph.caret-* icon (matches the rest of the app's chevrons) rather than a bare
        # unicode glyph. checked = collapsed; the icon + a click both flow through _set_collapsed.
        self.collapse_btn = QPushButton()
        self.collapse_btn.setCheckable(True)  # checked = collapsed
        self.collapse_btn.setFlat(True)
        self.collapse_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.collapse_btn.setToolTip("Collapse / expand the coaching panel")
        self.collapse_btn.toggled.connect(self._set_collapsed)
        header = _HeaderBar(self._toggle_collapsed)
        header.setProperty("role", "PanelHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(8)
        row.addWidget(self._title)
        row.addStretch(1)
        row.addWidget(self.summary_label)
        row.addWidget(self.collapse_btn)
        self._header = header

        # --- body: a stack of {top-3 table, friendly "need more laps" label}, swapped in refresh().
        self.table = QTableWidget(0, len(self._COLUMNS))
        self.table.setHorizontalHeaderLabels(self._COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        # Let each row grow to fit its wrapped "How to find it" cell instead of a fixed 34-px row
        # that clips a 2nd line at a narrow panel width (the ellipsis-truncation bug). The body's
        # max-height cap still keeps the panel compact — it scrolls once the auto-height rows exceed
        # it, rather than cutting off the coaching sentence.
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
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
        # Apply the initial collapsed state LAST (after refresh() has set the headline), so the
        # collapsed header already reads "Coaching · <headline>". Set the button quietly (no signal)
        # then apply directly, so building collapsed doesn't fire collapsed_changed at construction.
        self.collapse_btn.blockSignals(True)
        self.collapse_btn.setChecked(bool(collapsed))
        self.collapse_btn.blockSignals(False)
        self._apply_collapsed(bool(collapsed))

    # ------------------------------------------------------------------ build
    def refresh(self):
        """Recompute the opportunities from the session and rebuild the top-3 rows (or the friendly
        excluded state). Called on load / lap-selection / re-segmentation — never on the 30 Hz tick.
        Clears any held row selection (a stale cid would mis-ring the map)."""
        opps = self.session.coaching_opportunities()
        brake_points = self.session.coaching_brake_points()
        # L2: only rows above the shown resolution count as opportunities (no "+0.00 s" rows).
        if opps.enough and _shown_rows(opps):
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
        # L2: only shown-resolution rows are opportunities (drop the "+0.00 s" rows).
        rows = _shown_rows(opps)[:PANEL_TOP_N]
        total = sum(r.time_lost for r in rows)
        # P1: phrase the headline by COUNT — "in your worst corner" reads right for one, "across your
        # top N corners" for several, so it never says the ungrammatical "across the top 1".
        if len(rows) == 1:
            self._headline = f"{total:.2f} s in your worst corner"
        else:
            self._headline = f"{total:.2f} s across your top {len(rows)} corners"
        self._refresh_summary_label()

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
        # Grow each row to its wrapped-reason height for the current column widths (the reason is the
        # stretch column, so its width — and thus the wrap — depends on the panel's live size).
        self.table.resizeRowsToContents()
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
        self._headline = ""
        self._refresh_summary_label()
        self.empty_label.setText(msg)
        self.body.setCurrentIndex(1)

    def resizeEvent(self, event):
        """Re-fit the row heights when the panel width changes: the reason (stretch) column
        re-wraps as the panel narrows, so a row that was one line can become two — auto-height keeps
        the full "How to find it" sentence visible instead of clipping it (the truncation bug)."""
        super().resizeEvent(event)
        self.table.resizeRowsToContents()

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

    def is_collapsed(self) -> bool:
        """True when the panel body is collapsed to just its header bar."""
        return self.collapse_btn.isChecked()

    def _toggle_collapsed(self):
        """Flip the collapsed state (driven by a click anywhere on the header bar). Routes through
        the checkable chevron so the button state, the icon and the body all stay in one truth; the
        button's toggled signal then calls _set_collapsed."""
        self.collapse_btn.setChecked(not self.collapse_btn.isChecked())

    def _set_collapsed(self, collapsed: bool):
        """The user collapsed/expanded the panel (chevron toggle OR header click): apply it, then
        emit collapsed_changed so the window can persist the choice (survives a reload)."""
        self._apply_collapsed(collapsed)
        self.collapsed_changed.emit(bool(collapsed))

    def _apply_collapsed(self, collapsed: bool):
        """Hide/show the body; the header strip (with the headline summary) stays as the re-open
        affordance. Swap the chevron icon (▸ collapsed / ▾ expanded, via the themed ph.caret glyph)
        and refresh the summary label so it reads "Coaching · <headline>" while collapsed. The
        uppercase COACHING title is hidden while collapsed so the bar doesn't say "Coaching" twice
        (the sentence-case "Coaching · " summary carries the identity); expanded, the title returns
        and the summary drops the prefix."""
        self.body.setVisible(not collapsed)
        self._title.setVisible(not collapsed)
        self.collapse_btn.setIcon(
            theme.icon("ph.caret-right" if collapsed else "ph.caret-down", color=C.text_dim))
        self._refresh_summary_label()

    def _refresh_summary_label(self):
        """Set the header summary text from the stashed headline. Collapsed, it leads with the
        "Coaching · " prefix so the thin header bar is an obvious, self-labelling re-open affordance
        ("Coaching · 0.60 s across your top 3 corners") — the uppercase title is hidden there;
        expanded, the title reads COACHING so the summary is just the headline. Empty headline (the
        friendly no-opportunity state) → no summary either way."""
        if not self._headline:
            self.summary_label.setText("")
            return
        collapsed = self.collapse_btn.isChecked()
        self.summary_label.setText(f"Coaching · {self._headline}" if collapsed else self._headline)
