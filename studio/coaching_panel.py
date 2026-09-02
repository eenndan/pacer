"""The auto coaching "Opportunities" dialog (F10): where to find time vs your own best lap.

A read-only QDialog over a precomputed ``coaching.Opportunities`` (no analysis here). PACER-FREE:
only the ``coaching`` dataclasses + ``coaching.reason_sentence``. Each row's Jump button calls the
injected ``jump_to(cid, entry_dist)`` (the app selects the corner + seeks the best lap to its
entry). When ``opportunities.enough`` is False the table is a friendly "need more laps" message.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
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
    QStyle,
    QStyleOptionViewItem,
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

# column indices — the modal dialog's six, then the panel's reason column (it drops the dialog's
# PhaseBar + Jump columns, so its "How to find it" sits at 3, not 4).
_COL_CORNER, _COL_LOST, _COL_SIGMA, _COL_PHASES, _COL_REASON, _COL_GO = range(6)
_PANEL_COL_REASON = 3
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


# IA-01: the ONE scope word both coaching surfaces lead with. Every opportunity is a median over the
# session's clean laps — the page does not, and cannot, re-scope to the lap you have selected (see
# OpportunitiesPanel's scope note), so it says so where the number is read.
_SCOPE_PREFIX = "Whole session"


def _clean_laps_phrase(n: int) -> str:
    """"median of N clean laps" — the sample the opportunities are a median OVER, so the headline
    carries its own denominator (singular for the degenerate one-lap case)."""
    return f"median of {n} clean lap{'' if n == 1 else 's'}"


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
    proportional segments (widths ∝ each third's |Δt| vs best) over the row's three numbers. This is
    a WHERE-in-the-corner profile of the typical lap vs best — NOT the row's "Time lost" (a cross-lap
    median), which it need not sum to or even agree in sign with. Read-only; the segment widths are
    the visual cue, the small numbers underneath the precise values, the net line the sign of the
    whole window, the tooltip the full breakdown.

    L5-05: a FASTER-than-best third is a real, readable state, not an absence of one. It used to
    render as a `C.border` sliver — 1.19:1 against the row, i.e. invisible — so a corner whose three
    thirds were ALL faster than best looked empty beside its "+0.08 s" Time lost, and only the
    tooltip reconciled the two measures. Faster thirds now take the palette's ahead colour, are
    sized by |Δt| like the losing ones, and the window's net is stated on the row face."""

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
        # L5-05: |Δt| sizes the bar, so a faster-than-best third is as visible as a losing one (the
        # old sum-of-losses scale gave an all-faster row three 1-px slivers and no readable state).
        mags = [abs(v) for v in vals]
        scale = sum(mags)

        # proportional bar
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(1)
        for pid, v, m in zip(ids, vals, mags, strict=True):
            seg = QWidget()
            seg.setFixedHeight(self._BAR_H)
            # stretch ∝ the third's |Δt|; a tiny floor so a flat row still shows three slivers
            bar.addWidget(seg, max(int(round(m / scale * 100)), 1) if scale > 1e-9 else 1)
            seg.setStyleSheet(f"background:{self._phase_colour(pid, v, dominant)}; "
                              "border-radius:2px;")
        lay.addLayout(bar)

        # the three numbers under the bar (the dominant loss accented, the faster thirds ahead-hued)
        nums = QHBoxLayout()
        nums.setContentsMargins(0, 0, 0, 0)
        nums.setSpacing(4)
        num_font = theme.mono_font(theme.CAPTION)
        for pid, v in zip(ids, vals, strict=True):
            lbl = QLabel(f"{v:+.2f}")
            lbl.setFont(num_font)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(f"color:{self._phase_colour(pid, v, dominant)};")
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

        # L5-05: the sign of the WINDOW on the row face, not only on hover — a row headlined
        # "+0.08 s lost" whose typical lap is net faster across the corner must say so where it is
        # read. Faster reads in the palette's ahead hue, slower stays muted (the accent is reserved
        # for the dominant losing third above).
        face = QLabel(f"net {net:+.2f} s" if abs(net) > 1e-6 else "net ~0 s")
        face.setFont(theme.mono_font(theme.CAPTION))
        face.setAlignment(Qt.AlignCenter)
        face.setStyleSheet(f"color:{theme.ahead_colour() if net < -1e-6 else C.text_dim};")
        lay.addWidget(face)

        self.setToolTip(
            "Where in the corner your typical lap is faster/slower than your best lap "
            "(Δt per third, s) — NOT the same as the row's Time lost:\n"
            + "   ".join(f"{_PHASE_LABEL[p]} {v:+.2f}" for p, v in zip(ids, vals, strict=True))
            + "\n" + net_line)

    @staticmethod
    def _phase_colour(pid: str, v: float, dominant: str) -> str:
        """One third's colour: the palette's AHEAD hue when it is faster than best (L5-05 — a real
        state, not an absence of one), the accent for the dominant losing third, muted otherwise.
        Routes through theme.ahead_colour() so the colour-blind palette recolours it too, and the
        already-signed number under the bar keeps the cue non-colour."""
        if v < -1e-6:
            return theme.ahead_colour()
        if v > 1e-6 and pid == dominant:
            return C.accent
        return C.text_dim if abs(v) > 1e-6 else C.border


# L5-03: a QTableWidget wraps the "How to find it" cell NARROWER than it measures it. The row height
# comes from the delegate's sizeHint, which the stylesheet style computes at the FULL section width
# and then ADDS the QSS `QTableWidget::item {padding: 4px 8px}` to; the paint pass instead DEDUCTS
# that padding, so it wraps into 16 fewer px. At the app's own 1440x900 default that 16-px delta
# costs a whole line — a reason sentence advancing 309 px in a 317-px column is measured as one line
# and painted as two, and the row's "(est)" brake-point line is silently dropped, cell ending on a
# literal "…", with no user action at all. So measure each reason cell at the width the delegate
# really PAINTS into (the style's own SE_ItemViewItemText rect, QSS padding included) and pin the
# resulting height as the item's explicit sizeHint, which the delegate returns verbatim.
def _wire_reason_fit(table: QTableWidget, col: int):
    """Keep `col`'s wrapped rows fitted for the life of `table`, and fit them now.

    The reason column STRETCHES, and the header settles its final width *after* every signal we can
    hook: `sectionResized` stops firing partway (measured: last emission 189 px against a final
    445 px) and a build-time fit measures a width the table never uses. So re-fit from a coalesced
    queued call that lands once the layout pass is over, in addition to the immediate one that keeps
    headless callers (and the tests) correct without an event loop."""
    timer = QTimer(table)
    timer.setSingleShot(True)
    timer.timeout.connect(lambda: _fit_reason_rows(table, col))
    table.horizontalHeader().sectionResized.connect(lambda *_: timer.start(0))
    _fit_reason_rows(table, col)
    timer.start(0)


def _fit_reason_rows(table: QTableWidget, col: int):
    """Re-height every wrapped reason cell in `col` from the rect the delegate paints into, then
    re-fit the rows. Idempotent — safe to call on every resize."""
    # Re-entrancy guard: re-fitting rows can toggle the vertical scrollbar, which re-stretches the
    # header, which calls back in here. One pass at a time; the next width change refits anyway.
    if table.property("_fitting_reason"):
        return
    opt = QStyleOptionViewItem()
    opt.initFrom(table)
    opt.features = QStyleOptionViewItem.HasDisplay
    opt.rect = QRect(0, 0, table.columnWidth(col), 100)
    text_rect = table.style().subElementRect(QStyle.SE_ItemViewItemText, opt, table)
    avail, pad_v = text_rect.width(), 100 - text_rect.height()
    if avail <= 0:  # a collapsed column: leave Qt's own heights alone rather than pin nonsense
        return
    fm = table.fontMetrics()
    rows = [r for r in range(table.rowCount()) if table.item(r, col) is not None]
    table.setProperty("_fitting_reason", True)
    try:
        # Qt's own answer first (drop any hint pinned by a previous pass, so this shrinks again when
        # the column widens). We only ever GROW past it — the fix is the dropped line, not a
        # re-invention of the row metrics, and the pin must stay harmless where there is no
        # stylesheet padding to mis-measure.
        for r in rows:
            table.item(r, col).setData(Qt.SizeHintRole, None)
        table.resizeRowsToContents()
        for r in rows:
            item = table.item(r, col)
            wrapped = fm.boundingRect(QRect(0, 0, avail, 0), Qt.TextWordWrap, item.text()).height()
            # The width must be a REAL one: setSizeHint DISCARDS an invalid QSize (a -1 "don't care"
            # width clears the role instead of pinning the height). The section stretches, so the
            # width we pass never drives the layout.
            item.setSizeHint(QSize(table.columnWidth(col),
                                   max(wrapped + pad_v, table.rowHeight(r))))
        table.resizeRowsToContents()
    finally:
        table.setProperty("_fitting_reason", False)


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
            lap = opportunities.median_lap_id
            # `n_laps` is a COUNT (stays as-is); `lap` is a lap ID, so it renders 1-based
            # (lap_label). IA-01: name the SCOPE here too — this ranking is the whole session's, not
            # the selected lap's, and the panel it mirrors now says so.
            title = QLabel(f"Biggest gains vs your best lap — {_SCOPE_PREFIX.lower()}, "
                           f"{_clean_laps_phrase(opportunities.n_laps)}"
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
        # stretch column, so a 2-line sentence needs the extra height — same as the panel's fill),
        # measured at the width the delegate PAINTS into so no line is dropped, and re-fitted every
        # time the header re-stretches the column (L5-03).
        _wire_reason_fit(table, _COL_REASON)
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
        # B10 (belt+braces with the theme.icon color_active fix): never the focused-default
        # styling that repainted the arrow amber-on-amber.
        btn.setAutoDefault(False)
        btn.setDefault(False)
        if self._jump_to is None:
            btn.setEnabled(False)
        else:
            cid, entry = opp.cid, opp.entry_dist
            # C8: close the modal FIRST, then jump — the ApplicationModal dialog otherwise
            # stays centred over exactly the map/corner state the jump just changed, making
            # the CTA read as doing nothing.
            def _jump(_checked=False, c=cid, d=entry):
                self.accept()
                self._jump_to(c, d)
            btn.clicked.connect(_jump)
        return btn


# How many opportunities the coaching page surfaces (the actionable shortlist — the full ranking
# + jump-to stays in the modal dialog).
PANEL_TOP_N = 3


class OpportunitiesPanel(QWidget):
    """The Coaching page of the lap panel's tab stack: the TOP-3 opportunities (corner · time
    lost · dominant reason) over a freshly computed ``coaching.Opportunities``, at the panel's
    FULL height — the full reason sentences get room to breathe (this replaced the old capped
    under-table strip whose whole drag range was 68 px). The modal ``OpportunitiesDialog``
    stays available for the full ranking + jump-to.

    SCOPE — WHOLE SESSION, NOT THE SELECTED LAP (IA-01). ``coaching_opportunities()`` takes no lap:
    every row is the MEDIAN loss vs best over the clean laps, ±σ is the cross-lap σ, and the reason
    is read off the median lap — none of which a single lap can answer. Its sibling Corners tab IS
    the per-lap surface (it renames itself "Corners · L6"), so this page must SAY it does not follow
    the selection rather than look like it silently failed to: the headline leads with the scope and
    the tab tooltip names it. Do not wire this to ``laps_selected`` — ``refresh()`` recomputes the
    identical session statistic, so that would repaint the same pixels and change nothing.

    Reads ONLY session accessors (``coaching_opportunities`` + ``coaching_brake_points``) — no
    analysis here. Refreshed on load / re-segmentation / unit + palette change (never on the 30 Hz
    tick, never on selection — see the scope note).
    A row click emits ``corner_clicked(cid)`` so the app can ring the corner's apex on the map.
    Honours the shared ESTIMATED labelling (the ``(est)`` brake-point lines via ``_reason_cell``,
    from ``theme.ESTIMATED_MARK``) and the friendly "need more laps" state when there aren't
    enough clean laps."""

    # Clicked corner cid (None on deselect) -> the map apex-ring highlight (wired in central_view).
    corner_clicked = Signal(object)

    _COLUMNS = ["Corner", "Time lost", "±σ", "How to find it"]

    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self._num_font = theme.mono_font(theme.TABLE)
        self._cids: list[int] = []  # row -> corner cid, set in refresh()
        # The headline (e.g. "0.60 s across your top 3 corners") — the page's one-line framing.
        self._headline = ""
        # Speed display unit (km/h default) for the reason sentence's apex deficit; pushed by the
        # window's Units toggle via set_speed_unit.
        self._speed_unit = units.DEFAULT_UNIT

        # --- the headline strip: the tab bar already names the page, so this is just the
        # summary sentence (no title, no chevron — a tab you leave costs nothing).
        self.summary_label = QLabel("")  # "Whole session · 0.42 s in 3 corners …" — set in refresh()
        self.summary_label.setProperty("role", "BarLabel")
        self.summary_label.setToolTip(
            "The biggest realistic time gains vs your own best lap, across the WHOLE session "
            "(the median over your clean, GPS-dropout-free laps) — these rows do NOT follow the "
            "lap you select; the Corners tab is the per-lap view. Open Coaching ▸ Opportunities… "
            "for the full ranking + jump-to.")
        header = QWidget()
        header.setProperty("role", "PanelHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(8)
        row.addWidget(self.summary_label)
        row.addStretch(1)
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
        # that clips a 2nd line at a narrow panel width (the ellipsis-truncation bug). As a full
        # tab page there is normally room for all rows; the table scrolls only when the panel is
        # dragged very short.
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        hdr = self.table.horizontalHeader()
        hdr.setStretchLastSection(True)  # the reason column takes the slack
        for col in (0, 1, 2):  # corner · time-lost · σ size to content; reason (last) stretches
            hdr.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        # L5-03: keep the wrapped reason rows fitted to the width the delegate really paints into,
        # re-measured whenever the header re-stretches the column.
        _wire_reason_fit(self.table, _PANEL_COL_REASON)

        self.empty_label = QLabel("")
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(f"color: {C.text_dim};")

        self.body = QStackedWidget()
        self.body.addWidget(self.table)        # index 0 — the top-3 rows
        self.body.addWidget(self.empty_label)  # index 1 — the friendly excluded state

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(header)
        lay.addWidget(self.body, 1)  # the rows take the page's full height
        self.refresh()

    # ------------------------------------------------------------------ build
    def refresh(self):
        """Recompute the opportunities from the session and rebuild the top-3 rows (or the friendly
        excluded state). Called on load / re-segmentation / unit + palette change — never on the
        30 Hz tick, and never on a lap selection (the summary is session-scoped; see the class note).
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
        # B12: sum the 2-dp DISPLAYED values, not the raw floats — the headline ("0.56 s")
        # and the visible rows (+0.26 +0.20 +0.11 = 0.57) must never disagree by a rounding
        # penny; the header is an aggregate of what the user can check by eye.
        total = sum(round(r.time_lost, 2) for r in rows)
        # P1: phrase the headline by COUNT — "in your worst corner" reads right for one, "across your
        # top N corners" for several, so it never says the ungrammatical "across the top 1".
        gains = (f"{total:.2f} s in your worst corner" if len(rows) == 1
                 else f"{total:.2f} s across your top {len(rows)} corners")
        # IA-01: LEAD with the scope. The tab strip beside this page renames itself "Corners · L6"
        # on a selection, so a coaching headline that neither moves nor names its scope reads as the
        # selected lap's number — on D24 lap 6 that understated the lap's own +2.08 s as "0.21 s".
        # State the session scope and the sample it is a median of, in the same "·" idiom the tabs
        # use, so the two pages can be told apart at a glance.
        self._headline = f"{_SCOPE_PREFIX} · {gains} ({_clean_laps_phrase(opps.n_laps)})"
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
        _fit_reason_rows(self.table, _PANEL_COL_REASON)
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
        _fit_reason_rows(self.table, _PANEL_COL_REASON)

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

    def _refresh_summary_label(self):
        """Set the headline-strip text from the stashed headline ("0.60 s across your top 3
        corners"). Empty headline (the friendly no-opportunity state) → no summary."""
        self.summary_label.setText(self._headline)
