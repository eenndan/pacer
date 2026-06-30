"""ConsistencyPanel (F6): collapsible strip under the lap table.

Two read-only views over consistency.py across the valid (warn-excluded) laps: a lap-time TREND
sparkline (PB laps green) and a TOP-5 most-inconsistent-corner list (ranked by σ × median-loss).
Clicking a corner row emits `corner_clicked(cid)`, wired only to the map apex-ring highlight.

Pacer-free; refreshed on load / re-segmentation, never on the 30 Hz tick."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import consistency, theme
from ._signal import fmt_time
from .lap_table import CORNER_DIR_GLYPH
from .theme import C

if TYPE_CHECKING:  # the injected session — typed for readers, not imported at runtime
    from .session import Session

COLUMNS = ["Corner", "σ (s)", "med Δ (s)"]
TOP_N = 5            # ranked corners shown — the actionable shortlist, not the full table
# Body height: default BODY_HEIGHT, min BODY_MIN_HEIGHT (below it the corner list scrolls).
# Resizable splitter section in app.py.
BODY_HEIGHT = 150    # px; the strip's natural/default height (splitter starts here)
BODY_MIN_HEIGHT = 70   # px; below this the corner list scrolls instead of vanishing
ROW_HEIGHT = 22
# Trend: dim line + dots; PB (running-min) laps green.
TREND_PEN = pg.mkPen(C.text_dim, width=1)
TREND_DOT_BRUSH = pg.mkBrush(C.text_muted)
PB_DOT_BRUSH = pg.mkBrush(C.ahead)
PB_DOT_PEN = pg.mkPen(C.canvas, width=1)
# Dashed green baseline at session best.
BASELINE_PEN = pg.mkPen(C.ahead, width=1, style=Qt.DashLine)
SPARK_AXIS_FONT = 10  # tabular tick font for the minimal min/max + first/last labels
# Vertical headroom so extreme dots/labels aren't clipped.
SPARK_Y_PAD_FRAC = 0.12
TREND_TOOLTIP = ("Lap-time trend over the valid laps (GPS-dropout ⚠ laps excluded). "
                 "Green dots mark session-best (PB) laps; the dashed green line is the "
                 "session best (the floor). The y labels are the fastest / slowest laps.")
LIST_TOOLTIP = ("Most inconsistent corners, ranked by σ × median time lost vs that "
                "corner's session best — corners that are both erratic AND slow rank "
                "highest. Click a row to ring its apex on the map.")


class ConsistencyPanel(QWidget):
    # Clicked corner cid (None on deselect) -> MapView.highlight_corner.
    corner_clicked = Signal(object)

    def __init__(self, session: Session):
        super().__init__()
        self.session = session

        # --- header: title · σ summary · collapse chevron
        title = QLabel("CONSISTENCY")
        title.setProperty("role", "BarLabel")
        self.sigma_label = QLabel("")  # "σ lap 0.42 · S1 0.15 …" — set in refresh()
        self.sigma_label.setProperty("role", "BarLabel")
        self.sigma_label.setToolTip(
            "Sample σ (ddof=1) of the lap times and of each sector's split times, "
            "over the valid laps (⚠ dropout laps excluded).")
        self.collapse_btn = QPushButton("▾")
        self.collapse_btn.setCheckable(True)  # checked = collapsed
        self.collapse_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.collapse_btn.setToolTip("Collapse / expand the consistency panel")
        self.collapse_btn.toggled.connect(self._on_collapse)
        header = QWidget()
        header.setProperty("role", "PanelHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(8)
        row.addWidget(title)
        row.addStretch(1)
        row.addWidget(self.sigma_label)
        row.addWidget(self.collapse_btn)

        # --- body: trend sparkline (left) · top-5 inconsistent corners (right).
        self.spark = pg.PlotWidget()
        self.spark.setToolTip(TREND_TOOLTIP)
        plot = self.spark.getPlotItem()
        # Minimal axes: dim tokens, tabular font, no minor ticks; the 2 explicit ticks/axis are
        # injected in refresh().
        for side in ("left", "bottom"):
            ax = plot.getAxis(side)
            ax.setPen(C.border)            # dim axis line
            ax.setTextPen(C.text_dim)      # tick labels
            ax.setTickFont(theme.mono_font(SPARK_AXIS_FONT))  # tabular figures, align digits
            ax.setStyle(maxTickLevel=0, tickLength=3)  # only our explicit ticks; tiny tick marks
        # Give the left axis just enough fixed width for an "m:ss.mmm" label so the curve doesn't
        # jump around as labels change width across recordings.
        plot.getAxis("left").setWidth(58)
        plot.setMouseEnabled(x=False, y=False)
        plot.setMenuEnabled(False)
        plot.hideButtons()
        self.spark.setBackground(None)  # transparent: panel surface shows through
        # Baseline (set in refresh): the session-best floor.
        self._baseline = pg.InfiniteLine(angle=0, pen=BASELINE_PEN, movable=False)
        plot.addItem(self._baseline)
        # Trend curve + dots; data set in refresh.
        self._curve = self.spark.plot([], [], pen=TREND_PEN)
        self._curve.setDownsampling(auto=True)
        self._curve.setClipToView(True)
        self._dots = pg.ScatterPlotItem(size=4, pen=None, brush=TREND_DOT_BRUSH, pxMode=True)
        self._pb_dots = pg.ScatterPlotItem(size=7, pen=PB_DOT_PEN, brush=PB_DOT_BRUSH,
                                           pxMode=True)
        plot.addItem(self._dots)
        plot.addItem(self._pb_dots)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setToolTip(LIST_TOOLTIP)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self._num_font = theme.mono_font(theme.TABLE)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self._cids: list[int] = []  # row -> corner cid, set in refresh()

        self.body = QWidget()
        body_lay = QHBoxLayout(self.body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)
        body_lay.addWidget(self.spark, 3)
        body_lay.addWidget(self.table, 2)
        # Min/Max (not fixed) so the splitter shrinks this strip, not the lap table.
        self.body.setMinimumHeight(BODY_MIN_HEIGHT)
        self.body.setMaximumHeight(BODY_HEIGHT)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(header)
        lay.addWidget(self.body)
        self.refresh()

    # ------------------------------------------------------------------ build
    def refresh(self):
        """Rebuild both views from the session (load / re-segmentation). Clears the row selection
        (a held cid would be stale)."""
        trend = self.session.lap_time_trend()
        ids = [i for i, _t in trend]
        times = [t for _i, t in trend]

        # Header σ summary: lap-time σ + each sector column's σ (when sectors exist).
        parts = []
        lap_sigma = consistency.sigma(times)
        parts.append(f"σ lap {lap_sigma:.2f}s" if lap_sigma is not None else "σ lap —")
        for k, s in enumerate(self.session.sector_sigmas()):
            parts.append(f"S{k + 1} {s:.2f}" if s is not None else f"S{k + 1} —")
        self.sigma_label.setText(" · ".join(parts))

        # x = real lap ids (a skipped invalid lap reads as a gap); y = lap time; PBs green.
        self._curve.setData(ids, times)
        pb = consistency.pb_mask(times)
        self._dots.setData([i for i, on in zip(ids, pb, strict=True) if not on],
                           [t for t, on in zip(times, pb, strict=True) if not on])
        self._pb_dots.setData([i for i, on in zip(ids, pb, strict=True) if on],
                              [t for t, on in zip(times, pb, strict=True) if on])
        self._refresh_spark_context(ids, times)

        # Top-N inconsistent corners (ranked by session.corner_consistency).
        ranked = self.session.corner_consistency()[:TOP_N]
        glyph = {c.cid: CORNER_DIR_GLYPH.get(c.direction, "") for c in self.session.corners.corner_list()}
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setRowCount(len(ranked))
        self._cids = [sp.cid for sp in ranked]
        for r, sp in enumerate(ranked):
            cells = [f"C{sp.cid} {glyph.get(sp.cid, '')}", f"{sp.sigma:.2f}",
                     f"{sp.median_loss:+.2f}"]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if col >= 1:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    item.setFont(self._num_font)
                self.table.setItem(r, col, item)
        self.table.blockSignals(False)

    def _refresh_spark_context(self, ids: list[int], times: list[float]):
        """Set the sparkline's baseline + 2-tick axes (fastest/slowest y, first/last lap x).
        <2 laps: clear them."""
        plot = self.spark.getPlotItem()
        left, bottom = plot.getAxis("left"), plot.getAxis("bottom")
        if len(times) < 2:
            self._baseline.setVisible(False)
            left.setTicks([])
            bottom.setTicks([])
            return
        lo, hi = min(times), max(times)  # fastest / slowest lap time over the valid laps
        # Baseline at the session best (the green-dot floor) so the swings read against it.
        self._baseline.setVisible(True)
        self._baseline.setValue(lo)
        # A hair of headroom so the extreme dots + y labels aren't clipped; range frozen here.
        pad = max((hi - lo) * SPARK_Y_PAD_FRAC, 1e-3)
        plot.setYRange(lo - pad, hi + pad, padding=0)
        plot.setXRange(ids[0], ids[-1], padding=0.04)
        # Two explicit ticks/axis: y = fastest/slowest times, x = first/last lap.
        left.setTicks([[(lo, fmt_time(lo)), (hi, fmt_time(hi))]])
        bottom.setTicks([[(ids[0], str(ids[0])), (ids[-1], str(ids[-1]))]])

    # ------------------------------------------------------------- interaction
    def _on_row_selected(self):
        """Emit the clicked row's corner cid (None on deselect). The map ring is the only
        consumer — deliberately no lap-selection / seek side effects (read-only panel)."""
        rows = self.table.selectionModel().selectedRows()
        if rows and 0 <= rows[0].row() < len(self._cids):
            self.corner_clicked.emit(self._cids[rows[0].row()])
        else:
            self.corner_clicked.emit(None)

    def _on_collapse(self, collapsed: bool):
        """Hide/show the body; the header strip (with the σ summary) stays. The chevron
        flips so the affordance reads the right way in both states."""
        self.body.setVisible(not collapsed)
        self.collapse_btn.setText("▸" if collapsed else "▾")
