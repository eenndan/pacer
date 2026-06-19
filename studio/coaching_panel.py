"""The auto coaching "Opportunities" dialog (F10): where to find time vs your own best lap.

A self-contained, READ-ONLY QDialog over a ``studio.coaching.Opportunities`` value (already
computed by the caller from the Session — the dialog does no analysis of its own). PACER-FREE:
it consumes only the plain ``coaching`` dataclasses + the pure ``coaching.reason_sentence``
helper, so it never imports the app or the pacer bindings (the studio architecture rule).

Layout — the top-3 ranked corners, biggest realistic gain first::

    ┌──────────────────────────────────────────────────────────────────┐
    │  Opportunities — biggest gains vs your best lap (median of N laps) │
    ├──────┬───────────┬─────────────────────────────────────┬──────────┤
    │ C7 ⟳ │  +0.42 s  │ carry more apex speed (−6.1 km/h)    │ [Go →]   │  ← jump-to
    │ C3 ⟲ │  +0.31 s  │ brake later / shorter (+0.18 s …)    │ [Go →]   │
    │ C11 ⟳│  +0.22 s  │ be consistent here (σ 0.14 s)        │ [Go →]   │
    └──────┴───────────┴─────────────────────────────────────┴──────────┘

Each row's "Go →" button calls the injected ``jump_to(cid, entry_dist)`` (the app selects that
corner — map highlight + the Corners view — and seeks the video to the BEST lap's entry to the
corner). The dialog stays open so the user can step through all three. Read-only otherwise.

EXCLUDED STATE: when ``opportunities.enough`` is False (fewer than ``coaching.MIN_LAPS`` valid,
dropout-free laps) the table is replaced by a friendly "need more laps" message — no crash.

Refreshed by the app only on load / re-segmentation (the dialog is rebuilt each time it is
opened from the freshly-computed Opportunities), so there is zero per-tick cost.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import coaching, theme
from .lap_table import CORNER_DIR_GLYPH
from .theme import C

# Column layout. Corner / Time lost / Reason sentence / the jump-to button (a cell widget).
_COL_CORNER, _COL_LOST, _COL_REASON, _COL_GO = range(4)
_HEADERS = ["Corner", "Time lost", "How to find it", ""]

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


class OpportunitiesDialog(QDialog):
    """The Coaching ▸ Opportunities… dialog. `opportunities` is a freshly-computed
    ``coaching.Opportunities``; `jump_to(cid, entry_dist)` is called when a row's Go button is
    clicked (the app selects the corner + seeks the best lap to its entry). `jump_to` may be
    None (the buttons are then disabled — used in headless tests of the layout)."""

    def __init__(self, opportunities: coaching.Opportunities,
                 jump_to: Callable[[int, float], None] | None = None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("pacer studio — opportunities")
        self.resize(560, 320)
        self._opps = opportunities
        self._jump_to = jump_to

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
        """The friendly excluded / nothing-to-show panel. Two cases: too few clean laps to
        analyse (the documented <MIN_LAPS gate), or enough laps but no corner is losing time on
        the typical lap (the driver is already at their best everywhere — a nice problem)."""
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
        table.setSelectionMode(QAbstractItemView.NoSelection)  # read-only; Go is the only action
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setFocusPolicy(Qt.NoFocus)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setDefaultSectionSize(40)
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(_COL_REASON, QHeaderView.Stretch)
        for col in (_COL_CORNER, _COL_LOST, _COL_GO):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        num_font = theme.mono_font(theme.TABLE)

        for r, opp in enumerate(opps.rows):
            glyph = CORNER_DIR_GLYPH.get(opp.direction, "")
            corner_item = QTableWidgetItem(f"C{opp.cid} {glyph}")
            corner_item.setFlags(corner_item.flags() & ~Qt.ItemIsEditable)

            # Time lost: a positive gain (red — it's time you're giving away), tabular.
            lost_item = QTableWidgetItem(f"+{opp.time_lost:.2f} s")
            lost_item.setFlags(lost_item.flags() & ~Qt.ItemIsEditable)
            lost_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            lost_item.setFont(num_font)
            lost_item.setForeground(QColor(theme.delta_colour(opp.time_lost)))

            reason_item = QTableWidgetItem(coaching.reason_sentence(opp))
            reason_item.setFlags(reason_item.flags() & ~Qt.ItemIsEditable)
            reason_item.setToolTip(_REASON_TIP.get(opp.reason.kind, ""))

            table.setItem(r, _COL_CORNER, corner_item)
            table.setItem(r, _COL_LOST, lost_item)
            table.setItem(r, _COL_REASON, reason_item)
            table.setCellWidget(r, _COL_GO, self._go_button(opp))
        self.table = table  # exposed for the tests
        return table

    def _go_button(self, opp: coaching.Opportunity) -> QPushButton:
        """The per-row jump-to button. Captures the row's (cid, entry_dist) and calls the
        injected `jump_to` — the app selects the corner (map + Corners view) and seeks the best
        lap to its entry. Disabled when no callback was injected (headless layout tests)."""
        # "Jump" + a Phosphor arrow icon, NOT a literal "Go →": the Unicode arrow doesn't render in
        # the UI font (it came out as a garbled glyph), so use the bundled icon font. variant=primary
        # (QSS) makes it read as the row's call-to-action; a min width keeps the label from clipping.
        btn = QPushButton(theme.icon("ph.arrow-right", color=C.on_accent), "Jump")
        btn.setProperty("variant", "primary")
        btn.setMinimumWidth(88)  # room for the arrow icon + "Jump" so neither clips
        btn.setToolTip(f"Select C{opp.cid} on the map and jump the video to your best lap's "
                       "entry to this corner")
        if self._jump_to is None:
            btn.setEnabled(False)
        else:
            cid, entry = opp.cid, opp.entry_dist
            btn.clicked.connect(lambda _checked=False, c=cid, d=entry: self._jump_to(c, d))
        return btn
