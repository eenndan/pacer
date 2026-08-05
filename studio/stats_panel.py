"""StatsView (the Stats page): the session-statistics dashboard behind the Laps|Corners|Stats
header toggle.

A read-only, scrollable column of stat groups over studio/stats.py's SessionStats service +
the existing Session accessors — SESSION totals, PACE distribution, SPEED & G peaks, the g-g
friction circle, DRIVING (brake/coast reductions), per-SECTOR best/median/σ, the DATA TRUST
card (the IMU↔GPS cross-check's first in-app surface — until now stdout-only), and a per-lap
statistics table. Compact in the quadrant; the panel-maximize button (⤢) turns it into a
full-window dashboard.

Pacer-free; refreshed on load / re-segmentation, never on the 30 Hz tick. Numbers render in
the mono stack (tabular figures); a signal-absent statistic shows an em-dash, never a fake 0."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme, units
from ._signal import fmt_time
from .lap_table import BEST_LAP_MARK, CORNER_DIR_GLYPH, NUM_ROLE, _NumItem
from .theme import C

if TYPE_CHECKING:  # the injected session — typed for readers, not imported at runtime
    from .session import Session

DASH = "—"                # the "no signal" cell/tile — an em-dash, never a fake 0
TILE_VALUE_PT = 15        # tile value type size (between BODY 13 and HERO 22)
TILES_PER_ROW = 4         # tile-grid width — 4 fits the quadrant, still calm maximized
GG_HEIGHT = 220           # px; the friction-circle plot's fixed height
GG_DOT_ALPHA = 90         # scatter alpha (0-255): a cloud, not 4000 opaque dots
GG_RING_STEP = 0.5        # g; concentric reference rings every half g
ROW_HEIGHT = 22           # per-lap/sector table row height (the consistency-table convention)
# Speed units live in the PER-LAP section label (one place), keeping the columns narrow
# enough that the whole table fits the quadrant with no clipped column.
LAP_COLUMNS = ["Lap", "Time", "Vmax", "Avg", "Min", "Lat g", "Brk g", "Brake s", "Coast s"]
CORNER_COLUMNS = ["Corner", "Best", "Median", "σ (s)", "Med loss", "Apex best", "Apex med",
                  "Grip %"]
WORST_TINT_N = 3          # the top-N inconsistency-score corners get the loss cell tinted
CORNERS_TOOLTIP = ("Corner-by-corner over the clean laps: session-best / median / σ "
                   "time-in-corner, the median loss vs best, apex speeds and median grip "
                   "utilization. The worst 3 loss cells (by σ × median-loss — erratic AND "
                   "slow) are tinted: that's where practice pays first. Click a row to ring "
                   "the corner's apex on the map; click a column header to sort.")
# Pace-trend verdict band: a fitted slope within ±this (s/lap) reads "steady" — don't
# narrate noise as a trend.
TREND_STEADY_BAND = 0.02
SECTOR_COLUMNS = ["Sector", "Best", "Median", "σ (s)"]

GG_TOOLTIP = ("The friction circle: every g-meter sample on the valid laps — lateral g across, "
              "longitudinal g up (accelerating) / down (braking). A driver using the tyre "
              "fills the rim of the circle; rings every 0.5 g. Longitudinal is the validated "
              "GPS-derived signal (the IMU forward axis is vibration-inflated).")
LAP_TABLE_TOOLTIP = ("Per-lap statistics over the valid laps. Vmax/Avg from the lap's own GPS "
                     "speed; peak g from the g-meter (lateral IMU, longitudinal GPS-derived); "
                     "Brake/Coast are the summed detected events — the same events the map "
                     "glyphs and coaching read. ★ marks the session-best lap.")
PACE_TOOLTIP = ("Lap-time distribution over the clean laps (valid, no GPS dropout — the same "
                "set every σ statistic uses). Spread = median − best: what the typical lap "
                "gives away to your demonstrated pace.")


def _fmt_hms(seconds: float) -> str:
    """A duration as m:ss, or h:mm:ss from an hour up — session totals span both."""
    s = max(int(round(seconds)), 0)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


class _Tile(QWidget):
    """One stat tile: a mono value over a dim caption. set() rewrites both in place."""

    def __init__(self, caption: str):
        super().__init__()
        self.value = QLabel(DASH)
        self.value.setFont(theme.mono_font(TILE_VALUE_PT, theme.W_SEMIBOLD))
        self.caption = QLabel(caption)
        self.caption.setFont(theme.ui_font(theme.CAPTION))
        self.caption.setStyleSheet(f"color: {C.text_dim};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        lay.addWidget(self.value)
        lay.addWidget(self.caption)

    def set(self, value: str | None, caption: str | None = None):
        self.value.setText(value if value else DASH)
        if caption is not None:
            self.caption.setText(caption)


class StatsView(QWidget):
    """The Stats page (see the module docstring). Contract: refresh() on load/re-segment,
    refresh_palette() after a palette flip, set_speed_unit() from the View ▸ Units toggle."""

    # Clicked CORNERS-table row's cid (None on deselect) -> the map apex ring, via the
    # maximize-aware CentralView handler (restore the grid first, then ring).
    corner_clicked = Signal(object)

    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self._speed_unit = getattr(self, "_speed_unit", units.DEFAULT_UNIT)

        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(12, 8, 12, 12)
        col.setSpacing(6)

        # --- SESSION totals
        col.addWidget(self._section("SESSION"))
        self.t_laps = _Tile("laps")
        self.t_laps.setToolTip("Valid laps · ⊘ band-excluded · ⚠ laps with a GPS dropout")
        self.t_duration = _Tile("recorded")
        self.t_moving = _Tile("moving")
        self.t_distance = _Tile("distance")
        self.t_clock = _Tile("on track")
        col.addLayout(self._grid(self.t_laps, self.t_duration, self.t_moving,
                                 self.t_distance, self.t_clock))

        # --- PACE distribution
        pace_hdr = self._section("PACE")
        pace_hdr.setToolTip(PACE_TOOLTIP)
        col.addWidget(pace_hdr)
        self.t_best = _Tile("best lap")
        self.t_median = _Tile("median lap")
        self.t_race_pace = _Tile("race pace · best 3 straight")
        self.t_race_pace.setToolTip(
            "The best average of 3 CONSECUTIVE clean laps — your sustained pace, next to "
            "the single glory lap.")
        self.t_digest = _Tile("fix your top 3 →")
        self.t_sigma = _Tile("σ lap")
        self.t_spread = _Tile("median − best")
        self.t_cov = _Tile("consistency · σ/median")
        self.t_cov.setToolTip(
            "Coefficient of variation: sample σ of the clean lap times over the median, as "
            "a percent. Scale-free, so it is comparable across tracks — lower is steadier.")
        self.t_within = _Tile("within 1% of best")
        self.t_trend = _Tile("trend")
        self.t_trend.setToolTip(
            "Robust lap-time trend over the session (Theil–Sen median slope — one traffic "
            "lap can't fake it). Negative = getting faster. Shown from 6 clean laps up.")
        col.addLayout(self._grid(self.t_best, self.t_median, self.t_race_pace, self.t_digest,
                                 self.t_sigma, self.t_spread, self.t_cov, self.t_within,
                                 self.t_trend))

        # --- SPEED & G peaks
        col.addWidget(self._section("SPEED · G"))
        self.t_vmax = _Tile("top speed")
        self.t_vmax.setToolTip("Max 3D GPS speed across the valid laps (10 Hz).")
        self.t_vmin = _Tile("slowest point")
        self.t_vmin.setToolTip(
            "The slowest on-lap speed across the valid laps — typically the tightest "
            "corner (a traffic or off-line lap can dip lower).")
        self.t_peak_lat = _Tile("peak lateral g")
        self.t_peak_lat.setToolTip(
            "Peak |lateral g| over the valid laps — IMU lateral, the GPS-cross-checked axis "
            "(see DATA TRUST).")
        self.t_peak_brake = _Tile("peak braking g")
        self.t_peak_brake.setToolTip(
            "Peak deceleration — from the smoothed GPS speed derivative (the validated "
            "longitudinal; the raw IMU forward axis is vibration-inflated). 10 Hz GPS "
            "quantizes brake onsets by ~1.5 m.")
        col.addLayout(self._grid(self.t_vmax, self.t_vmin, self.t_peak_lat,
                                 self.t_peak_brake))

        # --- the g-g friction circle
        self._gg_section = self._section("FRICTION CIRCLE")
        col.addWidget(self._gg_section)
        self.gg = pg.PlotWidget()
        self.gg.setToolTip(GG_TOOLTIP)
        plot = self.gg.getPlotItem()
        plot.setAspectLocked(True)  # a circle must render round, whatever the pane shape
        for side in ("left", "bottom"):
            ax = plot.getAxis(side)
            ax.setPen(C.border)
            ax.setTextPen(C.text_dim)
            ax.setTickFont(theme.mono_font(10))
            ax.setStyle(maxTickLevel=0, tickLength=3)
        plot.setMouseEnabled(x=False, y=False)
        plot.setMenuEnabled(False)
        plot.hideButtons()
        self.gg.setBackground(None)
        self.gg.setFixedHeight(GG_HEIGHT)
        # Compact + left-aligned (like the tiles/tables): a maximized panel widens the pane,
        # not the plot — the circle stays a circle with no vacant flanks.
        self.gg.setMaximumWidth(GG_HEIGHT * 2)
        # Reference geometry (rings + axes) is drawn per-refresh, sized to the cloud.
        self._gg_rings: list = []
        self._gg_dots = pg.ScatterPlotItem(size=3, pen=None, pxMode=True)
        plot.addItem(self._gg_dots)
        col.addWidget(self.gg, 0, Qt.AlignLeft)

        # --- DRIVING reductions (hidden without a g signal)
        self._driving_section = self._section("DRIVING")
        col.addWidget(self._driving_section)
        self.t_brake = _Tile("braking / lap · median")
        self.t_brake_n = _Tile("brake events / lap")
        self.t_coast = _Tile("coasting / lap · median")
        self.t_longest_coast = _Tile("longest coast")
        self.t_grip_ceiling = _Tile("grip envelope · p98")
        self.t_grip_ceiling.setToolTip(
            "The session's demonstrated combined-g ceiling: the 98th percentile of "
            "hypot(lateral, longitudinal) over the valid laps — the dashed ring on the "
            "friction circle, and the same robust convention the per-corner grip "
            "normalises to. ESTIMATED (lateral-dominant).")
        self._driving_grid = self._grid(self.t_brake, self.t_brake_n, self.t_coast,
                                        self.t_longest_coast, self.t_grip_ceiling)
        col.addLayout(self._driving_grid)

        # --- per-SECTOR best/median/σ (hidden without sector lines)
        self._sector_section = self._section("SECTORS")
        col.addWidget(self._sector_section)
        self.sector_table = self._make_table(SECTOR_COLUMNS)
        col.addWidget(self.sector_table)

        # --- the corner-by-corner session report (hidden without detected corners)
        self._corners_section = self._section("CORNERS")
        col.addWidget(self._corners_section)
        self.corners_table = self._make_table(CORNER_COLUMNS)
        self.corners_table.setToolTip(CORNERS_TOOLTIP)
        # Unlike the other stats tables this one is interactive: row-select → map ring,
        # header-click → sort (numeric via _NumItem, the lap-table idiom).
        self.corners_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.corners_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.corners_table.setFocusPolicy(Qt.ClickFocus)
        self.corners_table.itemSelectionChanged.connect(self._on_corner_row_selected)
        self.corners_table.horizontalHeader().sortIndicatorChanged.connect(
            self._on_corner_sort)
        # Explicit initial indicator: TRACK ORDER (corner id ascending). Without this, Qt's
        # untouched default indicator is column-0 DESCENDING and the first fill's
        # setSortingEnabled(True) would silently reverse the track.
        self.corners_table.horizontalHeader().setSortIndicator(0, Qt.AscendingOrder)
        col.addWidget(self.corners_table)

        # --- DATA TRUST (the timing-quality + g-provenance + IMU↔GPS cross-check card)
        col.addWidget(self._section("DATA TRUST"))
        self.trust_label = QLabel("")
        self.trust_label.setWordWrap(True)
        self.trust_label.setStyleSheet(f"color: {C.text_dim};")
        self.trust_label.setFont(theme.ui_font(theme.CAPTION))
        col.addWidget(self.trust_label)

        # --- per-lap statistics table
        self._laps_section = self._section("PER LAP")
        col.addWidget(self._laps_section)
        self.lap_table = self._make_table(LAP_COLUMNS)
        self.lap_table.setToolTip(LAP_TABLE_TOOLTIP)
        col.addWidget(self.lap_table)
        col.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(scroll)
        self.refresh()

    # ------------------------------------------------------------------ scaffolding
    @staticmethod
    def _section(title: str) -> QLabel:
        lab = QLabel(title)
        lab.setProperty("role", "BarLabel")
        return lab

    @staticmethod
    def _grid(*tiles: _Tile) -> QGridLayout:
        g = QGridLayout()
        g.setContentsMargins(0, 0, 0, 4)
        g.setHorizontalSpacing(18)
        g.setVerticalSpacing(8)
        for i, t in enumerate(tiles):
            g.addWidget(t, i // TILES_PER_ROW, i % TILES_PER_ROW)
        g.setColumnStretch(TILES_PER_ROW, 1)  # left-pack the tiles; slack stays right
        return g

    def _make_table(self, columns: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(columns))
        t.setHorizontalHeaderLabels(columns)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionMode(QAbstractItemView.NoSelection)
        t.setAlternatingRowColors(True)
        t.setFocusPolicy(Qt.NoFocus)
        # The OUTER scroll column owns scrolling; each table is sized to its content (no
        # stretched last column — it clips in the quadrant and balloons maximized).
        t.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        t.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        return t

    @staticmethod
    def _fit_table(t: QTableWidget):
        """Pin the table's size to its content so the outer scroll column does the scrolling
        and the table reads left-packed (like the tiles) when the panel is maximized."""
        t.resizeColumnsToContents()
        header_h = t.horizontalHeader().height()
        t.setFixedHeight(header_h + ROW_HEIGHT * t.rowCount() + 2 * t.frameWidth())
        width = sum(t.columnWidth(c) for c in range(t.columnCount()))
        t.setFixedWidth(width + 2 * t.frameWidth() + 2)

    def _num_item(self, text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        item.setFont(theme.mono_font(theme.TABLE))
        return item

    # ------------------------------------------------------------------ contract
    def refresh(self):
        """Rebuild every group from the session (load / re-segmentation / unit or palette
        flip — the reads are cached on the service side, so a re-render is cheap)."""
        session = self.session
        st = getattr(session, "stats", None)
        unit = self._speed_unit
        u_label = units.speed_label(unit)

        # SESSION totals
        valid = session.valid_lap_ids()
        excluded = getattr(session, "excluded_lap_ids", list)() or []
        dropouts = session.dropout_lap_ids() if hasattr(session, "dropout_lap_ids") else set()
        lap_bits = [str(len(valid))]
        if excluded:
            lap_bits.append(f"{len(excluded)}⊘")
        if dropouts:
            lap_bits.append(f"{len(dropouts)}⚠")
        self.t_laps.set(" · ".join(lap_bits) if valid else None)
        tot = st.totals() if st is not None else None
        if tot is not None and tot.duration_s > 0:
            self.t_duration.set(_fmt_hms(tot.duration_s))
            self.t_moving.set(_fmt_hms(tot.moving_s))
            self.t_distance.set(f"{tot.distance_m / 1000.0:.1f} km")
            clock = (f"{tot.start_clock}–{tot.end_clock}"
                     if tot.start_clock and tot.end_clock else None)
            self.t_clock.set(clock)
        else:
            for t in (self.t_duration, self.t_moving, self.t_distance, self.t_clock):
                t.set(None)

        # PACE
        pace = st.pace() if st is not None else None
        if pace is not None:
            self.t_best.set(fmt_time(pace.best))
            self.t_median.set(fmt_time(pace.median), f"median · {pace.n} clean laps")
            self.t_sigma.set(f"{pace.sigma:.2f} s" if pace.sigma is not None else None)
            self.t_spread.set(f"+{pace.spread:.2f} s")
            rp = st.race_pace()
            self.t_race_pace.set(fmt_time(rp) if rp is not None else None)
            cov = st.pace_cov()
            self.t_cov.set(f"{cov:.1f} %" if cov is not None else None)
            count, n = st.laps_within_pct(1.0)
            self.t_within.set(f"{count} / {n}" if n else None)
            self._set_trend(st.pace_trend())
        else:
            for t in (self.t_best, self.t_median, self.t_sigma, self.t_spread,
                      self.t_race_pace, self.t_cov, self.t_within, self.t_trend):
                t.set(None)
        self._set_digest(session, pace)

        # SPEED · G — session peaks over the per-lap stats
        rows = st.lap_stats() if st is not None else []
        vmax = st.session_vmax() if st is not None else None
        if vmax is not None:
            self.t_vmax.set(f"{units.convert_speed(vmax[0], unit):.1f} {u_label}",
                            f"top speed · lap {vmax[1] + 1}")
        else:
            self.t_vmax.set(None, "top speed")
        vmins = [r.vmin_kmh for r in rows if r.vmin_kmh is not None]
        self.t_vmin.set(f"{units.convert_speed(min(vmins), unit):.1f} {u_label}"
                        if vmins else None)
        lat_peaks = [r.peak_lat_g for r in rows if r.peak_lat_g is not None]
        brk_peaks = [r.peak_brake_g for r in rows if r.peak_brake_g is not None]
        self.t_peak_lat.set(f"{max(lat_peaks):.2f} g" if lat_peaks else None)
        self.t_peak_brake.set(f"{max(brk_peaks):.2f} g" if brk_peaks else None)

        self._refresh_gg(st)
        self._refresh_driving(st, rows)
        self._refresh_sectors(session)
        self._refresh_corners(session, unit, u_label)
        self._refresh_trust(session)
        self._refresh_lap_table(session, rows, unit, u_label)

    def _set_trend(self, slope: float | None):
        """The trend tile: signed s/lap + a plain-language verdict caption. A slope inside
        ±TREND_STEADY_BAND reads "steady" (don't narrate noise); None (short session) is a
        dash with the base caption."""
        if slope is None:
            self.t_trend.set(None, "trend")
            return
        if slope <= -TREND_STEADY_BAND:
            verdict = "improving"
        elif slope >= TREND_STEADY_BAND:
            verdict = "fading"
        else:
            verdict = "steady"
        # A ±0.00 display (signed near-zero) reads as a glitch — flatten it for "steady".
        text = "0.00 s/lap" if round(slope, 2) == 0 else f"{slope:+.2f} s/lap"
        self.t_trend.set(text, f"trend · {verdict}")

    def _set_digest(self, session, pace):
        """The coaching digest tile: the projected lap if the top-3 corner losses were fixed,
        anchored to the MEDIAN lap (the honesty rule — the best lap already banks some of
        those corners, so best − losses would overclaim). Dash without enough clean laps /
        no coaching data."""
        opp_fn = getattr(session, "coaching_opportunities", None)
        opp = opp_fn() if opp_fn is not None else None
        rows = getattr(opp, "rows", None) if getattr(opp, "enough", False) else None
        if pace is None or not rows:
            self.t_digest.set(None)
            self.t_digest.setToolTip("")
            return
        saved = sum(r.time_lost for r in rows[:3])
        projected = pace.median - saved
        self.t_digest.set(fmt_time(projected))
        self.t_digest.setToolTip(
            f"Projected from your MEDIAN lap ({fmt_time(pace.median)}) minus the top-"
            f"{min(len(rows), 3)} corner losses ({saved:.2f} s, measured vs your best "
            "lap's corners — see the coaching panel). Anchored to the typical lap, not "
            "best-minus-losses: your best lap already banks some of those corners.")

    def refresh_palette(self):
        """Re-render after a colour-blind-palette flip: the best-lap ★ row tint + the purple
        best-sector cells go through the palette accessors, so a re-render recolours them.
        Cheap — every service read is cached."""
        self.refresh()

    def set_speed_unit(self, unit: str):
        """Re-render the speed-bearing tiles/columns in the new display unit (View ▸ Units)."""
        unit = units.normalize_unit(unit)
        if unit == self._speed_unit:
            return
        self._speed_unit = unit
        self.refresh()

    # ------------------------------------------------------------------ groups
    def _refresh_gg(self, st):
        cloud = st.gg_cloud() if st is not None else None
        plot = self.gg.getPlotItem()
        for ring in self._gg_rings:
            plot.removeItem(ring)
        self._gg_rings = []
        has = cloud is not None and len(cloud[0]) > 0
        self._gg_section.setVisible(has)
        self.gg.setVisible(has)
        if not has:
            self._gg_dots.setData([], [])
            return
        lat, lon = cloud
        # Identity (non-semantic) cloud colour — the first chart-series hue, translucent.
        colour = pg.mkColor(theme.CHART_SERIES[0])
        colour.setAlpha(GG_DOT_ALPHA)
        self._gg_dots.setData(np.asarray(lat), np.asarray(lon), brush=pg.mkBrush(colour))
        # Reference rings every 0.5 g out to the cloud's envelope, + hairline axes.
        r_max = float(np.ceil(max(np.max(np.abs(lat)), np.max(np.abs(lon))) / GG_RING_STEP)
                      ) * GG_RING_STEP
        r_max = max(r_max, GG_RING_STEP)
        angles = np.linspace(0.0, 2.0 * np.pi, 90)
        ring_pen = pg.mkPen(C.border, width=1)
        r = GG_RING_STEP
        while r <= r_max + 1e-9:
            ring = plot.plot(r * np.cos(angles), r * np.sin(angles), pen=ring_pen)
            self._gg_rings.append(ring)
            r += GG_RING_STEP
        for angle in (0, 90):
            line = pg.InfiniteLine(pos=(0, 0), angle=angle, pen=ring_pen, movable=False)
            plot.addItem(line)
            self._gg_rings.append(line)
        # The demonstrated grip envelope (p98 of combined g): a dashed accent ring — the
        # "ceiling you actually reached", vs the neutral 0.5 g reference rings.
        env = st.gg_envelope() if st is not None else None
        if env:
            env_pen = pg.mkPen(C.accent, width=1, style=Qt.DashLine)
            ring = plot.plot(env * np.cos(angles), env * np.sin(angles), pen=env_pen)
            self._gg_rings.append(ring)
        ticks = [(v, f"{v:+.1f}") for v in (-r_max, 0.0, r_max)]
        plot.getAxis("left").setTicks([ticks])
        plot.getAxis("bottom").setTicks([ticks])
        pad = 0.1 * r_max
        plot.setXRange(-r_max - pad, r_max + pad, padding=0)
        plot.setYRange(-r_max - pad, r_max + pad, padding=0)

    def _refresh_driving(self, st, rows):
        brake = [r.brake_s for r in rows if r.brake_s is not None]
        counts = [r.brake_n for r in rows if r.brake_n is not None]
        coast = [r.coast_s for r in rows if r.coast_s is not None]
        has = bool(brake or counts or coast)
        self._driving_section.setVisible(has)
        for t in (self.t_brake, self.t_brake_n, self.t_coast, self.t_longest_coast,
                  self.t_grip_ceiling):
            t.setVisible(has)
        if not has:
            return
        self.t_brake.set(f"{np.median(brake):.1f} s" if brake else None)
        self.t_brake_n.set(f"{np.median(counts):.0f}" if counts else None)
        self.t_coast.set(f"{np.median(coast):.1f} s" if coast else None)
        longest = st.longest_coast_s() if st is not None else None
        self.t_longest_coast.set(f"{longest:.1f} s" if longest is not None else None)
        env = st.gg_envelope() if st is not None else None
        self.t_grip_ceiling.set(f"{env:.2f} g" if env is not None else None)

    def _refresh_sectors(self, session):
        sigmas = session.sector_sigmas() if hasattr(session, "sector_sigmas") else []
        has = bool(sigmas)
        self._sector_section.setVisible(has)
        self.sector_table.setVisible(has)
        if not has:
            self.sector_table.setRowCount(0)
            return
        bests = session.session_best_splits()
        medians = (session.sector_medians()
                   if hasattr(session, "sector_medians") else [None] * len(sigmas))
        best_colour = QColor(theme.best_sector_colour())
        self.sector_table.setRowCount(len(sigmas))
        for k in range(len(sigmas)):
            name = QTableWidgetItem(f"S{k + 1}")
            self.sector_table.setItem(k, 0, name)
            best = bests[k] if k < len(bests) else None
            best_item = self._num_item(fmt_time(best) if best is not None else DASH)
            if best is not None:
                best_item.setForeground(best_colour)  # the purple session-best hue
            self.sector_table.setItem(k, 1, best_item)
            med = medians[k] if k < len(medians) else None
            self.sector_table.setItem(
                k, 2, self._num_item(fmt_time(med) if med is not None else DASH))
            sig = sigmas[k]
            self.sector_table.setItem(
                k, 3, self._num_item(f"{sig:.2f}" if sig is not None else DASH))
        self._fit_table(self.sector_table)

    def _refresh_corners(self, session, unit, u_label):
        report = getattr(session, "corner_report", list)() or []
        has = bool(report)
        self._corners_section.setVisible(has)
        self.corners_table.setVisible(has)
        if not has:
            self.corners_table.setRowCount(0)
            return
        self._corners_section.setText(f"CORNERS · speeds in {u_label}")
        # The worst corners by σ × median-loss get their loss cell tinted in the "behind"
        # hue — erratic AND slow is where practice pays first. Capped at WORST_TINT_N and
        # at half the field: a tint that covers every row highlights nothing.
        k = min(WORST_TINT_N, max(1, len(report) // 2))
        worst = {r.cid for r in sorted(report, key=lambda r: -r.score)[:k] if r.score > 0}
        behind = QColor(theme.behind_colour())
        mono = theme.mono_font(theme.TABLE)

        def cell(val, fmtstr):
            item = _NumItem(fmtstr.format(val) if val is not None else DASH)
            item.setData(NUM_ROLE, val)
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item.setFont(mono)
            return item

        t = self.corners_table
        t.setSortingEnabled(False)   # Qt requirement: never fill a live-sorting table
        t.blockSignals(True)
        t.clearSelection()
        t.setRowCount(len(report))
        for r, cr in enumerate(report):
            name = _NumItem(f"C{cr.cid} {CORNER_DIR_GLYPH.get(cr.direction, '')}")
            name.setData(NUM_ROLE, cr.cid)   # numeric key: C10 must not sort before C2
            t.setItem(r, 0, name)
            t.setItem(r, 1, cell(cr.best_s, "{:.2f}"))
            t.setItem(r, 2, cell(cr.median_s, "{:.2f}"))
            t.setItem(r, 3, cell(cr.sigma_s, "{:.2f}"))
            loss = cell(cr.median_loss_s, "+{:.2f}")
            if cr.cid in worst:
                loss.setForeground(behind)
            t.setItem(r, 4, loss)
            t.setItem(r, 5, cell(units.convert_speed(cr.apex_best_kmh, unit)
                                 if cr.apex_best_kmh is not None else None, "{:.1f}"))
            t.setItem(r, 6, cell(units.convert_speed(cr.apex_median_kmh, unit)
                                 if cr.apex_median_kmh is not None else None, "{:.1f}"))
            t.setItem(r, 7, cell(cr.grip_median * 100.0
                                 if cr.grip_median is not None else None, "{:.0f}"))
        t.blockSignals(False)
        t.setSortingEnabled(True)
        self._fit_table(t)

    def _on_corner_row_selected(self):
        """Emit the selected row's corner cid (None on deselect) — read from the row's own
        item (sorting reorders rows, so a row→cid list would go stale)."""
        rows = self.corners_table.selectionModel().selectedRows()
        if rows:
            item = self.corners_table.item(rows[0].row(), 0)
            self.corner_clicked.emit(item.data(NUM_ROLE) if item else None)
        else:
            self.corner_clicked.emit(None)

    @staticmethod
    def _on_corner_sort(_index, order):
        """Keep _NumItem's blanks-last convention through descending sorts (the lap-table
        idiom: the class flag flips before Qt reverses the order)."""
        _NumItem._descending = order == Qt.DescendingOrder

    def _refresh_trust(self, session):
        lines: list[str] = []
        quality = getattr(session, "timing_quality", None)  # a Session @property
        if quality is not None:
            clock = ("video clock (estimated)" if quality.media_clock
                     else "GPS9 true clock")
            lines.append(f"Timing: {clock} · {quality.dropped_pct()}% of fixes rejected")
        if getattr(session, "has_gmeter", False):
            src = {"accl": "IMU", "gps": "GPS"}
            lat_src = src.get(session.gmeter_source(), session.gmeter_source())
            long_src = src.get(session.gmeter_long_source(), session.gmeter_long_source())
            lines.append(f"g-meter: {lat_src} lateral · {long_src}-derived longitudinal")
        # In-lap GPS dropouts: the ⚠ rule made visible — the count AND what it means for
        # the statistics on this page (those laps feed no best/σ/pace number).
        valid = session.valid_lap_ids() if hasattr(session, "valid_lap_ids") else []
        dropouts = session.dropout_lap_ids() if hasattr(session, "dropout_lap_ids") else set()
        if dropouts:
            lines.append(f"GPS dropout inside {len(dropouts)} of {len(valid)} laps — "
                         "flagged ⚠ and left out of bests, σ and pace")
        cross = session.gmeter_cross() if hasattr(session, "gmeter_cross") else None
        if cross is not None:
            verdict = "agree" if cross.ok else "DISAGREE"
            lines.append(f"IMU↔GPS cross-check: {verdict} · lateral r={cross.lat_corr:+.2f} · "
                         f"longitudinal r={cross.long_corr:+.2f} · {cross.n} samples")
            self.trust_label.setToolTip(cross.summary())
        self.trust_label.setText("\n".join(lines) if lines else DASH)

    def _refresh_lap_table(self, session, rows, unit, u_label):
        has = bool(rows)
        self._laps_section.setVisible(has)
        self.lap_table.setVisible(has)
        self._laps_section.setText(f"PER LAP · speeds in {u_label}")
        best = session.best_lap_id() if hasattr(session, "best_lap_id") else None
        self.lap_table.setRowCount(len(rows))
        best_colour = QColor(theme.best_lap_colour())
        for r, s in enumerate(rows):
            mark = BEST_LAP_MARK if s.idx == best else ""
            lap_item = QTableWidgetItem(f"{mark}{s.idx + 1}")  # 1-based, the app-wide rule
            if s.idx == best:
                lap_item.setForeground(best_colour)
            self.lap_table.setItem(r, 0, lap_item)

            def num(v, fmtstr):
                return self._num_item(fmtstr.format(v) if v is not None else DASH)
            self.lap_table.setItem(r, 1, self._num_item(fmt_time(s.time)))
            for c, kmh in ((2, s.vmax_kmh), (3, s.avg_kmh), (4, s.vmin_kmh)):
                self.lap_table.setItem(
                    r, c, num(units.convert_speed(kmh, unit)
                              if kmh is not None else None, "{:.1f}"))
            self.lap_table.setItem(r, 5, num(s.peak_lat_g, "{:.2f}"))
            self.lap_table.setItem(r, 6, num(s.peak_brake_g, "{:.2f}"))
            self.lap_table.setItem(r, 7, num(s.brake_s, "{:.1f}"))
            self.lap_table.setItem(r, 8, num(s.coast_s, "{:.1f}"))
        self._fit_table(self.lap_table)
