"""StatsView (the Stats page): the session-statistics dashboard behind the Laps|Corners|Stats
header toggle.

A read-only, scrollable column of stat groups over studio/stats.py's SessionStats service +
the existing Session accessors — SESSION totals, the DATA TRUST card (what the page's numbers
are worth: the start-line/track/exclusion caveats, the timing clock, the g provenance and the
IMU↔GPS cross-check), PACE distribution, SPEED & G peaks, the g-g friction circle, DRIVING
(brake/coast reductions), per-SECTOR best/median/σ, and a per-lap statistics table. Compact in
the quadrant; the panel-maximize button (⤢) turns it into a full-window dashboard.

HONESTY RULES. The maximized dashboard hides the map, so the page carries its OWN unverified-
timing banner rather than leaning on the map's. Unverified timing mutes the PER LAP Time column
(the same cells the Laps tab mutes) and the stitched target tiles, and suppresses the ★ best —
never the measured tiles beside them, which ARE laps you drove. A statistic that cannot exist
(no accelerometer, no complete lap) says so in words next to the em-dash.

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

# The Coaching panel's OWN row filter and top-N, imported (not re-implemented) so the digest tile
# and the coaching headline can never state different totals for the same three corners — L5-02.
from .coaching_panel import PANEL_TOP_N, _shown_rows
from .consistency import pb_mask
from .lap_table import (
    BEST_LAP_MARK,
    CORNER_DIR_GLYPH,
    DROPOUT_SUFFIX,
    DROPOUT_TOOLTIP,
    NUM_ROLE,
    PROVISIONAL_COLOR,
    PROVISIONAL_TOOLTIP,
    _NumItem,
    estimated_timing_tooltip,
)
from .theme import C

if TYPE_CHECKING:  # the injected session — typed for readers, not imported at runtime
    from .session import Session

DASH = "—"                # the "no signal" cell/tile — an em-dash, never a fake 0
# The page's OWN unverified-timing banner. View ▸ Session statistics maximizes the lap panel, which
# hides the MAP — and with it the app's one prominent "Lap timing is unverified" strip — exactly on
# the surface that then paints a full page of bold statistics. So the CTA is repeated here, above
# SESSION, where it is read before any number. Same opening sentence as the map strip (one wording,
# two places); the page-specific half names what it invalidates.
PROVISIONAL_BANNER = ("Lap timing is unverified — every lap time, split and “best” on this page is "
                      "measured from an auto-fitted start/finish line. Drag it on the map to where "
                      "a lap begins.")
# The 0-lap page: the status bar's own copy plus the next action, ON the page. Without it the
# PACE/SPEED groups render as a wall of em-dashes whose only explanation is a status-bar line
# outside the maximized panel.
NO_LAPS_TEXT = ("No complete laps in this recording — so there are no lap statistics to show.\n\n"
                "The GPS may not have locked, or the recording is too short to cross the "
                "start/finish line. If the track looks right on the map, drag the start/finish "
                "line to where a lap begins.")
# The absent-accelerometer sentence — used BOTH in the DATA TRUST card and under the SPEED · G
# tiles, so the dashes and the trust card explain themselves in the same words.
NO_GMETER_NOTE = ("g-meter: no accelerometer in this recording — lateral g, braking g and grip "
                  "are unavailable.")
TILE_VALUE_PT = 15        # tile value type size (between BODY 13 and HERO 22)
TILES_PER_ROW = 4         # tile-grid MAX columns (wide/maximized panels)
TILE_MIN_PX = 148         # reflow threshold: columns = viewport width // this, clamped 2..4
#                           (C6 — the hard-coded 4 pushed the 4th tile column, incl. the
#                           coaching digest, off-pane at the default quadrant width)
GG_HEIGHT = 220           # px; the friction-circle plot's fixed height
SPARK_HEIGHT = 96         # px; the PACE trend sparkline (absorbed from the retired
#                           ConsistencyPanel — its content lives here now)
SPARK_AXIS_FONT = 10      # tabular tick font for the sparkline's min/max + first/last labels
SPARK_Y_PAD_FRAC = 0.12   # vertical headroom so extreme dots/labels aren't clipped
SPARK_TOOLTIP = ("Lap-time trend over the clean laps (GPS-dropout ⚠ laps excluded). "
                 "Highlighted dots mark session-best (PB) laps; the dashed line is the "
                 "session best (the floor). Y labels: fastest / slowest lap.")
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
BRAKE_COLUMNS = ["Corner", "n", "Onset σ m", "Span m", "Commit %", "m later"]
STRAIGHT_COLUMNS = ["Straight", "Best", "Median", "σ (s)", "Trap best", "Trap med", "Exit Δ"]
RING_ROLE = NUM_ROLE + 1   # the map-ring corner cid stored on a straight row's label item
STRAIGHTS_TOOLTIP = ("Straight-by-straight over the clean laps (the corner/straight "
                     "partition — segments sum to the lap time exactly): best/median/σ "
                     "time, the trap speed at the straight's END, and Exit Δ — the "
                     "preceding corner's median exit speed vs your best lap's (+ is "
                     "faster). A slow exit ahead of a long straight is the costliest "
                     "mistake on track: the FIX FIRST tile ranks exit deficit × straight "
                     "time spread. Trap speed doubles as a gearing/engine-health proxy. "
                     "Click a row to ring the corner feeding that straight.")
BRAKING_TOOLTIP = ("Braking repeatability per corner, over the clean laps: the cross-lap "
                   "scatter of your brake-onset POINT (σ and max−min span, metres, compared "
                   "in the reference lap's odometer) plus commitment — the median event's "
                   "peak decel as a % of the session's demonstrated maximum — and the "
                   "ESTIMATED median metres you could brake later (the D4 brake-point "
                   "model). Corners with no matched brake event are omitted. Honesty floor: "
                   "10 Hz GPS quantizes the onset by ~1.5 m — a σ at or below that is "
                   "measurement, not driving. Click a row to ring the corner on the map.")
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
# The two stitched TARGETS (moved here from the Laps tab's SESSION-BESTS footer, which cost the
# lap grid 63px — two lap rows — on every recording). Each now sits with the data it is derived
# from: the rolling best beside the other lap-time paces, the theoretical best inside SECTORS,
# whose per-sector bests it literally sums.
ROLLING_TOOLTIP = ("Best rolling — the fastest single complete loop regardless of where it "
                   "starts: the minimum time from passing any track position to passing it "
                   "again one lap later (windows spanning a GPS-dropout ⚠ lap are excluded). "
                   "A reference target, not a lap you drove.")
THEORETICAL_TOOLTIP = ("Theoretical best — the sum of the session-best sector splits (the purple "
                       "cells on the Laps tab): the lap you'd drive by stitching every best "
                       "sector together. A reference target, not a lap you drove. Shown only "
                       "with sector lines — without them it degenerates to the best lap time.")


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
        # C6 responsive tiles: every _grid registers here; _reflow_tiles re-places them when
        # the pane crosses a column threshold. Built at max columns, reflowed on first resize.
        self._tile_grids: list = []
        self._tile_cols = TILES_PER_ROW
        self._scroll = None

        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(12, 8, 12, 12)
        col.setSpacing(6)

        # --- the page's own trust banner + empty state, above everything they qualify.
        self.provisional_banner = QLabel(PROVISIONAL_BANNER)
        # The map trust strip's amber call-to-action style, by object name — one QSS rule, so the
        # two surfaces can never drift apart visually.
        self.provisional_banner.setObjectName("ProvisionalBanner")
        self.provisional_banner.setWordWrap(True)
        self.provisional_banner.setToolTip(PROVISIONAL_TOOLTIP)
        self.provisional_banner.setVisible(False)
        col.addWidget(self.provisional_banner)
        self.no_laps_note = QLabel(NO_LAPS_TEXT)
        self.no_laps_note.setProperty("role", "EmptyState")  # the lap grid's empty-state styling
        self.no_laps_note.setWordWrap(True)
        self.no_laps_note.setVisible(False)
        col.addWidget(self.no_laps_note)

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

        # --- DATA TRUST (the start-line/track/exclusion caveats + the timing-quality,
        # g-provenance and IMU↔GPS cross-check card). It sits SECOND, right under the session
        # totals: at the foot of the page it was ~1200px down — below the fold of even a
        # 1728x1117 maximized dashboard — so the caveats that say how much every number below is
        # worth were only reachable by scrolling past all of them.
        col.addWidget(self._section("DATA TRUST"))
        self.trust_label = QLabel("")
        self.trust_label.setWordWrap(True)
        self.trust_label.setStyleSheet(f"color: {C.text_dim};")
        self.trust_label.setFont(theme.ui_font(theme.CAPTION))
        col.addWidget(self.trust_label)

        # --- PACE distribution
        self._pace_section = self._section("PACE")
        self._pace_section.setToolTip(PACE_TOOLTIP)
        col.addWidget(self._pace_section)
        self.t_best = _Tile("best lap")
        self.t_median = _Tile("median lap")
        self.t_race_pace = _Tile("race pace · best 3-lap run")
        self.t_race_pace.setToolTip(
            "The best average of 3 CONSECUTIVE clean laps — your sustained pace, next to "
            "the single glory lap.")
        self.t_rolling = _Tile("best rolling")
        self.t_rolling.setToolTip(ROLLING_TOOLTIP)
        # IA-04: the caption names the BASE. This tile is the median lap rebased, so it routinely
        # reads slower than the "best lap" tile two cells away — uncaptioned that looks like a
        # target you have already beaten. L4-08: no "→" — the tile is not clickable (the Coaching
        # tab is a tab away, and a painted arrow that does nothing is a broken affordance); the
        # tooltip points there in words instead.
        self.t_digest = _Tile(f"median lap · top {PANEL_TOP_N} fixed")
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
        col.addLayout(self._grid(self.t_best, self.t_median, self.t_race_pace, self.t_rolling,
                                 self.t_digest, self.t_sigma, self.t_spread, self.t_cov,
                                 self.t_within, self.t_trend))
        # The lap-time trend sparkline (PB dots + session-best baseline) — absorbed from the
        # retired ConsistencyPanel strip; hidden with <2 clean laps.
        self.spark = pg.PlotWidget()
        self.spark.setToolTip(SPARK_TOOLTIP)
        spark_plot = self.spark.getPlotItem()
        for side in ("left", "bottom"):
            ax = spark_plot.getAxis(side)
            ax.setPen(C.border)
            ax.setTextPen(C.text_dim)
            ax.setTickFont(theme.mono_font(SPARK_AXIS_FONT))
            ax.setStyle(maxTickLevel=0, tickLength=3)
        # Fixed left-axis width for an "m:ss.mmm" label so the curve doesn't jump across sessions.
        spark_plot.getAxis("left").setWidth(58)
        spark_plot.setMouseEnabled(x=False, y=False)
        spark_plot.setMenuEnabled(False)
        spark_plot.hideButtons()
        self.spark.setBackground(None)
        self.spark.setFixedHeight(SPARK_HEIGHT)
        self._spark_baseline = pg.InfiniteLine(angle=0, movable=False)
        spark_plot.addItem(self._spark_baseline)
        self._spark_curve = self.spark.plot([], [], pen=pg.mkPen(C.text_dim, width=1))
        self._spark_curve.setDownsampling(auto=True)
        self._spark_curve.setClipToView(True)
        self._spark_dots = pg.ScatterPlotItem(size=4, pen=None,
                                              brush=pg.mkBrush(C.text_muted), pxMode=True)
        self._spark_pb_dots = pg.ScatterPlotItem(size=7, pen=pg.mkPen(C.canvas, width=1),
                                                 pxMode=True)
        spark_plot.addItem(self._spark_dots)
        spark_plot.addItem(self._spark_pb_dots)
        col.addWidget(self.spark)

        # --- SPEED & G peaks
        self._speed_section = self._section("SPEED · G")
        col.addWidget(self._speed_section)
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
        # Without an accelerometer two of those four tiles can only ever be em-dashes — say why
        # WHERE the dashes are, not only in the trust card (the DRIVING and FRICTION CIRCLE
        # sections hide themselves entirely, so these are the g surfaces left visible).
        self.no_gmeter_note = QLabel(NO_GMETER_NOTE)
        self.no_gmeter_note.setWordWrap(True)
        self.no_gmeter_note.setStyleSheet(f"color: {C.text_dim};")
        self.no_gmeter_note.setFont(theme.ui_font(theme.CAPTION))
        self.no_gmeter_note.setVisible(False)
        col.addWidget(self.no_gmeter_note)

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
        # The sum of THIS section's best splits. It lives here rather than in PACE because these
        # are literally its inputs — and it inherits the section's 0-sector hide for free: with no
        # sector lines it degenerates to the best lap time (a duplicate of the ★ starred best that
        # can even read slower than the rolling best), so it carries no information there.
        self.t_theoretical = _Tile("theoretical best")
        self.t_theoretical.setToolTip(THEORETICAL_TOOLTIP)
        self._sector_target_grid = self._grid(self.t_theoretical)
        col.addLayout(self._sector_target_grid)
        self.sector_table = self._make_table(SECTOR_COLUMNS)
        col.addWidget(self.sector_table)

        # --- the corner-by-corner session report (hidden without detected corners)
        self._corners_section = self._section("CORNERS")
        col.addWidget(self._corners_section)
        # The phase-loss headline: where the session's corner time goes (entry/apex/exit),
        # from the per-lap drift-gated thirds decomposition — coach-grade, and computed, not
        # modeled. Hidden with the section / without phase data.
        phase_tip = ("Every clean lap's Δt-vs-best through each corner, split into "
                     "equal-distance entry / apex / exit thirds (the same decomposition the "
                     "coaching reasons use), medianed per corner, positive parts summed. "
                     "Seconds = what a typical lap gives away in that phase across the whole "
                     "track; hover a corner's loss cell for its own triple.")
        self.t_phase_entry = _Tile("lost on entry")
        self.t_phase_apex = _Tile("lost at apex")
        self.t_phase_exit = _Tile("lost on exit")
        for t in (self.t_phase_entry, self.t_phase_apex, self.t_phase_exit):
            t.setToolTip(phase_tip)
        col.addLayout(self._grid(self.t_phase_entry, self.t_phase_apex, self.t_phase_exit))
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

        # --- braking repeatability + commitment (hidden without corners / a g signal)
        self._braking_section = self._section("BRAKING")
        col.addWidget(self._braking_section)
        self.braking_table = self._make_table(BRAKE_COLUMNS)
        self.braking_table.setToolTip(BRAKING_TOOLTIP)
        self.braking_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.braking_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.braking_table.setFocusPolicy(Qt.ClickFocus)
        self.braking_table.itemSelectionChanged.connect(self._on_brake_row_selected)
        self.braking_table.horizontalHeader().sortIndicatorChanged.connect(
            self._on_corner_sort)
        self.braking_table.horizontalHeader().setSortIndicator(0, Qt.AscendingOrder)
        col.addWidget(self.braking_table)

        # --- the straight-line report (hidden without corners / a best lap)
        self._straights_section = self._section("STRAIGHTS")
        col.addWidget(self._straights_section)
        self.t_fix_first = _Tile("fix first")
        self.t_fix_first.setToolTip(
            "The corner whose exit deficit costs the most down the following straight "
            "(exit-speed deficit × the straight's median−best time spread) — measured, "
            "not modeled. Fix this one before chasing apex speed elsewhere.")
        col.addLayout(self._grid(self.t_fix_first))
        self.straights_table = self._make_table(STRAIGHT_COLUMNS)
        self.straights_table.setToolTip(STRAIGHTS_TOOLTIP)
        self.straights_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.straights_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.straights_table.setFocusPolicy(Qt.ClickFocus)
        self.straights_table.itemSelectionChanged.connect(self._on_straight_row_selected)
        self.straights_table.horizontalHeader().sortIndicatorChanged.connect(
            self._on_corner_sort)
        self.straights_table.horizontalHeader().setSortIndicator(0, Qt.AscendingOrder)
        col.addWidget(self.straights_table)

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
        # AsNeeded (not AlwaysOff): the report tables are content-sized, so in a narrow pane
        # the page h-scrolls instead of silently clipping their rightmost columns.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setWidget(body)
        self._scroll = scroll
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

    def _grid(self, *tiles: _Tile) -> QGridLayout:
        g = QGridLayout()
        g.setContentsMargins(0, 0, 0, 4)
        g.setHorizontalSpacing(18)
        g.setVerticalSpacing(8)
        self._place_tiles(g, list(tiles), self._tile_cols)
        # Registered for the responsive reflow (C6): a narrow quadrant re-places every grid
        # at fewer columns instead of pushing the 4th column off-pane.
        self._tile_grids.append((g, list(tiles)))
        return g

    @staticmethod
    def _place_tiles(g: QGridLayout, tiles: list, cols: int):
        for i, t in enumerate(tiles):
            g.addWidget(t, i // cols, i % cols)
        for c in range(TILES_PER_ROW + 1):
            g.setColumnStretch(c, 0)
        g.setColumnStretch(cols, 1)  # left-pack the tiles; slack stays right

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow_tiles()

    def _reflow_tiles(self):
        """C6: fit the tile grids to the actual pane — columns = width // TILE_MIN_PX,
        clamped 2..TILES_PER_ROW. Re-places widgets only when the count changes (cheap; a
        resize otherwise costs nothing)."""
        width = self._scroll.viewport().width() if self._scroll is not None else self.width()
        cols = max(2, min(TILES_PER_ROW, width // TILE_MIN_PX))
        if cols == self._tile_cols:
            return
        self._tile_cols = cols
        for g, tiles in self._tile_grids:
            self._place_tiles(g, tiles, cols)

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

    def _set_target_tile(self, tile: _Tile, value, tip: str):
        """Render a stitched TARGET tile (theoretical best / best rolling).

        These are not laps anyone drove — they are composed from the session's best splits and
        loops — so they share the lap timing's authority: while the timing is PROVISIONAL (an
        arbitrary start line) OR the clock is DEGRADED (media-clock / low-GPS estimate) the value
        is muted + italic and carries the explaining note, restored to the normal tile once
        Verified AND high-quality. Kept byte-for-byte in spirit with the Laps footer this moved
        from; the measured PACE tiles beside it are unmuted because they ARE laps you drove."""
        session = self.session
        tile.set(fmt_time(value) if value is not None else None)
        provisional = not getattr(session, "timing_verified", True)
        quality = getattr(session, "timing_quality", None)
        muted = provisional or bool(quality is not None and quality.degraded)
        font = tile.value.font()
        font.setItalic(muted)
        tile.value.setFont(font)
        tile.value.setStyleSheet(
            f"color: {theme.PROVISIONAL_COLOR if muted else C.text};")
        if not muted:
            tile.setToolTip(tip)
            return
        note = PROVISIONAL_TOOLTIP if provisional else estimated_timing_tooltip(quality)
        tile.setToolTip(f"{note}\n\n{tip}")

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
        # The page's own trust banner: the maximized dashboard has no map to carry the app's
        # amber "drag the start/finish line" CTA. Pointless with no laps — the empty-state block
        # below already tells that story (and names the same line as its next action).
        self.provisional_banner.setVisible(
            bool(valid) and not getattr(session, "timing_verified", True))
        self._set_no_laps_state(bool(valid))
        # The absent-accelerometer note sits under the SPEED · G tiles whose dashes it explains,
        # so it hides with them on a lapless recording (the empty-state block tells that story).
        self.no_gmeter_note.setVisible(
            bool(valid) and not getattr(session, "has_gmeter", False))
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
        # The rolling best is a stitched target, not a measured lap — it reads straight off
        # Session (never the pace summary) so it survives a session with no clean-lap stats.
        rolling = (session.best_rolling_lap()
                   if hasattr(session, "best_rolling_lap") else None)
        self._set_target_tile(self.t_rolling, rolling, ROLLING_TOOLTIP)
        self._set_digest(session, pace)
        self._refresh_spark(session)

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
        self._refresh_braking(session)
        self._refresh_straights(session, unit, u_label)
        self._refresh_trust(session)
        self._refresh_lap_table(session, rows, unit, u_label)

    def _refresh_spark(self, session):
        """The PACE trend sparkline: lap time per clean lap (x = the 1-BASED lap number, the
        same number every table shows), PB laps in the best-lap hue, the session best as a
        dashed baseline. Hidden with <2 clean laps (a one-dot trend is noise)."""
        trend = getattr(session, "lap_time_trend", list)() or []
        visible = len(trend) >= 2
        self.spark.setVisible(visible)
        if not visible:
            return
        laps = [i + 1 for i, _t in trend]   # 1-based, the app-wide display rule
        times = [t for _i, t in trend]
        pb = pb_mask(times)
        best_colour = QColor(theme.best_lap_colour())  # palette-aware at render time
        self._spark_curve.setData(laps, times)
        self._spark_dots.setData([n for n, on in zip(laps, pb, strict=True) if not on],
                                 [t for t, on in zip(times, pb, strict=True) if not on])
        self._spark_pb_dots.setBrush(pg.mkBrush(best_colour))
        self._spark_pb_dots.setData([n for n, on in zip(laps, pb, strict=True) if on],
                                    [t for t, on in zip(times, pb, strict=True) if on])
        lo, hi = min(times), max(times)
        self._spark_baseline.setPen(pg.mkPen(best_colour, width=1, style=Qt.DashLine))
        self._spark_baseline.setValue(lo)
        plot = self.spark.getPlotItem()
        pad = max((hi - lo) * SPARK_Y_PAD_FRAC, 1e-3)
        plot.setYRange(lo - pad, hi + pad, padding=0)
        plot.setXRange(laps[0], laps[-1], padding=0.04)
        plot.getAxis("left").setTicks([[(lo, fmt_time(lo)), (hi, fmt_time(hi))]])
        plot.getAxis("bottom").setTicks([[(laps[0], str(laps[0])),
                                          (laps[-1], str(laps[-1]))]])

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
        """The coaching digest tile: the projected lap if the top-N corner losses were fixed,
        anchored to the MEDIAN lap (the honesty rule — the best lap already banks some of
        those corners, so best − losses would overclaim). Dash without enough clean laps /
        no coaching data.

        L5-02 — the saving is the Coaching panel's ARITHMETIC, not a parallel one: its rows
        (`_shown_rows`, sub-resolution losses dropped), its count (`PANEL_TOP_N`) and its
        rounding (the 2-dp cells the user can add up by eye, summed and re-rounded). Summing the
        raw floats instead made the two surfaces disagree by a rounding penny for the same three
        corners — 0.31 s here against 0.32 s on the Coaching page — and made this tile disagree
        with its OWN tooltip, which printed 0.31 while subtracting 0.3134."""
        opp_fn = getattr(session, "coaching_opportunities", None)
        opp = opp_fn() if opp_fn is not None else None
        has_rows = getattr(opp, "enough", False) and getattr(opp, "rows", None)
        rows = _shown_rows(opp)[:PANEL_TOP_N] if has_rows else []
        if pace is None or not rows:
            self.t_digest.set(None)
            self.t_digest.setToolTip("")
            return
        saved = round(sum(round(r.time_lost, 2) for r in rows), 2)
        projected = pace.median - saved
        self.t_digest.set(fmt_time(projected), f"median lap · top {len(rows)} fixed")
        self.t_digest.setToolTip(
            f"Projected from your MEDIAN lap ({fmt_time(pace.median)}) minus the top-"
            f"{len(rows)} corner losses ({saved:.2f} s, measured vs your best lap's "
            "corners). Anchored to the typical lap, not best-minus-losses: your best lap "
            "already banks some of those corners — so this target can read SLOWER than your "
            "best lap and still be the honest one. The Coaching tab lists the corners.")

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
    def _set_no_laps_state(self, has_laps: bool):
        """With zero valid laps every PACE and SPEED · G tile can only render an em-dash, so hide
        both groups behind ONE explanatory block carrying the status bar's copy and the next
        action. SESSION stays (its recorded time/distance/clock are real, lap or no lap) and so
        does DATA TRUST — it is the diagnostic for why no lap was found. Reversible: a
        re-segmentation that finds laps restores every tile."""
        self.no_laps_note.setVisible(not has_laps)
        for section in (self._pace_section, self._speed_section):
            section.setVisible(has_laps)
        for t in (self.t_best, self.t_median, self.t_race_pace, self.t_rolling, self.t_digest,
                  self.t_sigma, self.t_spread, self.t_cov, self.t_within, self.t_trend,
                  self.t_vmax, self.t_vmin, self.t_peak_lat, self.t_peak_brake):
            t.setVisible(has_laps)

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
        # The theoretical best hides WITH the section — the 0-sector rule, for free (see the
        # tile's comment in __init__). Recomputed each refresh so a later sector-line edit
        # reveals it.
        self.t_theoretical.setVisible(has)
        self._set_target_tile(
            self.t_theoretical,
            session.theoretical_best() if hasattr(session, "theoretical_best") else None,
            THEORETICAL_TOOLTIP)
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
        phase = (getattr(session, "phase_report", lambda: None)() if has else None)
        phase_rows = (dict(zip(phase.cids, phase.rows, strict=True))
                      if phase is not None else {})
        self._refresh_phase_tiles(phase)
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
            tri = phase_rows.get(cr.cid)
            if tri is not None:
                # The corner's own phase matrix, on hover — where INSIDE this corner the
                # typical lap loses (positive = slower than best over that third).
                loss.setToolTip(f"Median vs best — entry {tri[0]:+.2f} · "
                                f"apex {tri[1]:+.2f} · exit {tri[2]:+.2f} s")
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

    def _refresh_phase_tiles(self, phase):
        """The where-the-time-goes headline tiles: percent of the lost corner time per phase
        + the seconds behind it. Hidden when there is no phase data (no corners / no best /
        nothing lost)."""
        tiles = (self.t_phase_entry, self.t_phase_apex, self.t_phase_exit)
        share = getattr(phase, "share", None)
        fr = share.fracs() if share is not None else None
        if fr is None:
            for t in tiles:
                t.setVisible(False)
            return
        secs = (share.entry_s, share.apex_s, share.exit_s)
        caps = ("lost on entry", "lost at apex", "lost on exit")
        for t, f, s, cap in zip(tiles, fr, secs, caps, strict=True):
            t.setVisible(True)
            t.set(f"{f * 100.0:.0f} %", f"{cap} · {s:.1f} s")

    def _refresh_braking(self, session):
        """The BRAKING table: one row per corner WITH a matched brake event (an unbraked
        kink adds noise, not signal). Same sort/click idiom as the CORNERS table."""
        report = [r for r in (getattr(session, "brake_report", list)() or []) if r.n > 0]
        has = bool(report)
        self._braking_section.setVisible(has)
        self.braking_table.setVisible(has)
        if not has:
            self.braking_table.setRowCount(0)
            return
        mono = theme.mono_font(theme.TABLE)

        def cell(val, fmtstr):
            item = _NumItem(fmtstr.format(val) if val is not None else DASH)
            item.setData(NUM_ROLE, val)
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item.setFont(mono)
            return item

        t = self.braking_table
        t.setSortingEnabled(False)
        t.blockSignals(True)
        t.clearSelection()
        t.setRowCount(len(report))
        for r, bc in enumerate(report):
            name = _NumItem(f"C{bc.cid}")
            name.setData(NUM_ROLE, bc.cid)
            t.setItem(r, 0, name)
            t.setItem(r, 1, cell(bc.n, "{:d}"))
            t.setItem(r, 2, cell(bc.sigma_m, "{:.1f}"))
            t.setItem(r, 3, cell(bc.span_m, "{:.1f}"))
            t.setItem(r, 4, cell(bc.commit_pct, "{:.0f}"))
            t.setItem(r, 5, cell(bc.metres_later_med, "{:+.1f}"))
        t.blockSignals(False)
        t.setSortingEnabled(True)
        self._fit_table(t)

    def _refresh_straights(self, session, unit, u_label):
        report = getattr(session, "straights_report", list)() or []
        # B8: a start line inside a corner section produces ~0-duration S/F stubs — noise
        # rows with no driving content (BRAKING already omits unmatched corners the same way).
        report = [st for st in report
                  if max(st.best_s or 0.0, st.median_s or 0.0) >= 0.05]
        has = bool(report)
        self._straights_section.setVisible(has)
        self.straights_table.setVisible(has)
        if not has:
            self.t_fix_first.setVisible(False)
            self.straights_table.setRowCount(0)
            return
        self._straights_section.setText(f"STRAIGHTS · speeds in {u_label}")
        # The FIX FIRST tile: the biggest exit-deficit × straight-spread product.
        top = max(report, key=lambda s: s.leverage)
        if top.leverage > 0:
            self.t_fix_first.setVisible(True)
            self.t_fix_first.set(
                f"C{top.ring_cid}",
                f"fix first · exit {units.convert_speed(top.exit_delta_kmh, unit):+.1f} "
                f"{u_label} → +{top.median_s - top.best_s:.2f} s straight")
        else:
            self.t_fix_first.setVisible(False)
        mono = theme.mono_font(theme.TABLE)

        def cell(val, fmtstr):
            item = _NumItem(fmtstr.format(val) if val is not None else DASH)
            item.setData(NUM_ROLE, val)
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item.setFont(mono)
            return item

        t = self.straights_table
        t.setSortingEnabled(False)
        t.blockSignals(True)
        t.clearSelection()
        t.setRowCount(len(report))
        for r, st in enumerate(report):
            name = _NumItem(st.label)
            name.setData(NUM_ROLE, st.index)      # sort key: track order
            name.setData(RING_ROLE, st.ring_cid)  # the corner feeding this straight
            t.setItem(r, 0, name)
            t.setItem(r, 1, cell(st.best_s, "{:.2f}"))
            t.setItem(r, 2, cell(st.median_s, "{:.2f}"))
            t.setItem(r, 3, cell(st.sigma_s, "{:.2f}"))
            t.setItem(r, 4, cell(units.convert_speed(st.trap_best_kmh, unit)
                                 if st.trap_best_kmh is not None else None, "{:.1f}"))
            t.setItem(r, 5, cell(units.convert_speed(st.trap_median_kmh, unit)
                                 if st.trap_median_kmh is not None else None, "{:.1f}"))
            t.setItem(r, 6, cell(units.convert_speed(st.exit_delta_kmh, unit)
                                 if st.exit_delta_kmh is not None else None, "{:+.1f}"))
        t.blockSignals(False)
        t.setSortingEnabled(True)
        self._fit_table(t)

    def _on_straight_row_selected(self):
        """A straight row rings the CORNER FEEDING it (its exit sets the straight's story);
        same corner_clicked pathway as the other tables."""
        rows = self.straights_table.selectionModel().selectedRows()
        if rows:
            item = self.straights_table.item(rows[0].row(), 0)
            self.corner_clicked.emit(item.data(RING_ROLE) if item else None)
        else:
            self.corner_clicked.emit(None)

    def _on_brake_row_selected(self):
        """A BRAKING-table row is a corner too — emit the same corner_clicked the CORNERS
        table does (one map-ring pathway, maximize-aware in CentralView)."""
        rows = self.braking_table.selectionModel().selectedRows()
        if rows:
            item = self.braking_table.item(rows[0].row(), 0)
            self.corner_clicked.emit(item.data(NUM_ROLE) if item else None)
        else:
            self.corner_clicked.emit(None)

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
        """The DATA TRUST card: what the numbers on this page are worth.

        The TRUST-BREAKING facts LEAD — an unconfirmed start line, an unknown track, laps left
        out of every statistic. Without them the card printed provenance only, and read
        identically on a session where all three were wrong and one where all three were fine.
        The provenance lines (clock, g source, dropouts, cross-check) follow."""
        lines: list[str] = []
        tips: list[str] = []
        valid = session.valid_lap_ids() if hasattr(session, "valid_lap_ids") else []
        # Gated on having laps, like the banner: with none, "every lap time below" refers to
        # nothing, and the empty-state block already makes placing the line the next action.
        if valid and not getattr(session, "timing_verified", True):
            lines.append("Start/finish line: auto-fitted, not confirmed — every lap time and "
                         "split below is measured from an arbitrary point. Drag it on the map.")
        # "" (not None) as the getattr default: a test double that models no track at all must
        # not be reported as a recording whose track lookup FAILED.
        if getattr(session, "track_name", "") is None:
            lines.append("Track: unknown — not in the track database, so the start/finish line "
                         "could not be placed for you.")
        excluded = getattr(session, "excluded_lap_ids", list)() or []
        if excluded:
            # Denominator = the laps the segmenter FOUND, not valid+excluded: a recording can
            # also carry slivers that never reached the ⊘ band at all, and "24 of 49" would be
            # arithmetic invented to make the two numbers meet. State both true counts instead.
            count = getattr(session, "lap_count", None)
            total = count() if callable(count) else len(valid) + len(excluded)
            lines.append(f"Statistics use {len(valid)} of the {total} laps found — "
                         f"{len(excluded)} ⊘ excluded, their distance off the session median "
                         "(see the Laps tab).")
        quality = getattr(session, "timing_quality", None)  # a Session @property
        if quality is not None:
            clock = ("video clock (estimated)" if quality.media_clock
                     else "GPS9 true clock")
            # "of MOVING fixes" is not padding: the fraction is judged over the RETAINED MOVING
            # trace, deliberately (load.py:266-272 — the raw count includes the stationary
            # GPS-acquisition lead-in the pipeline trims, which flagged clean footage as
            # degraded purely on how many chapters were opened). Naming the population is the
            # fix; the number itself is the shipped one.
            lines.append(f"Timing: {clock} · {quality.dropped_pct()}% of moving fixes rejected")
            tips.append("The rejected-fix share is measured over the fixes taken WHILE MOVING. "
                        "The stationary lead-in before you drive off is trimmed by the loader "
                        "and left out of the verdict, so opening one chapter or all of them "
                        "gives the same answer.")
        if getattr(session, "has_gmeter", False):
            src = {"accl": "IMU", "gps": "GPS"}
            lat_src = src.get(session.gmeter_source(), session.gmeter_source())
            long_src = src.get(session.gmeter_long_source(), session.gmeter_long_source())
            lines.append(f"g-meter: {lat_src} lateral · {long_src}-derived longitudinal")
        else:
            # The card used to go SILENT about the g channel exactly when it is missing — while
            # the peak-g tiles, the per-lap g columns and the corner Grip % all render em-dashes
            # with no stated reason anywhere on the window.
            lines.append(NO_GMETER_NOTE)
        # In-lap GPS dropouts: the ⚠ rule made visible — the count AND what it means for
        # the statistics on this page (those laps feed no best/σ/pace number).
        dropouts = session.dropout_lap_ids() if hasattr(session, "dropout_lap_ids") else set()
        if dropouts:
            lines.append(f"GPS dropout inside {len(dropouts)} of {len(valid)} laps — "
                         "flagged ⚠ and left out of bests, σ and pace")
        cross = session.gmeter_cross() if hasattr(session, "gmeter_cross") else None
        if cross is not None:
            verdict = "agree" if cross.ok else "DISAGREE"
            lines.append(f"IMU↔GPS cross-check: {verdict} · lateral r={cross.lat_corr:+.2f} · "
                         f"longitudinal r={cross.long_corr:+.2f} · {cross.n} samples")
            tips.append(cross.summary())
        self.trust_label.setText("\n".join(lines) if lines else DASH)
        # Set unconditionally (both ways): a stale cross-check summary must not survive a
        # re-render onto a session that has none.
        self.trust_label.setToolTip("\n\n".join(tips))

    def _refresh_lap_table(self, session, rows, unit, u_label):
        has = bool(rows)
        self._laps_section.setVisible(has)
        self.lap_table.setVisible(has)
        self._laps_section.setText(f"PER LAP · speeds in {u_label}")
        # These Time cells are literally the laps the Laps tab already demotes, so demote them
        # identically here: muted + italic + the explaining tooltip while the start line is
        # PROVISIONAL or the clock is DEGRADED. Only the Time column — Vmax/Avg/g/brake are
        # measured whatever the start line is, and muting the whole table would tell the reader
        # nothing about which numbers the unverified line actually moves.
        verified = getattr(session, "timing_verified", True)
        quality = getattr(session, "timing_quality", None)
        timing_note = (PROVISIONAL_TOOLTIP if not verified
                       else estimated_timing_tooltip(quality)
                       if quality is not None and quality.degraded else "")
        best = session.best_lap_id() if hasattr(session, "best_lap_id") else None
        # The Laps tab suppresses the ★/best colour entirely while provisional (a "best" against
        # an arbitrary start line is meaningless). Match it, or this page would keep vouching for
        # a fastest lap in the very column it just muted.
        if not verified:
            best = None
        # C7: the page's DATA TRUST card says dropout laps are "flagged ⚠" — flag them HERE
        # too (the Laps tab already does), or the promise reads as broken.
        dropouts = session.dropout_lap_ids() if hasattr(session, "dropout_lap_ids") else set()
        self.lap_table.setRowCount(len(rows))
        best_colour = QColor(theme.best_lap_colour())
        for r, s in enumerate(rows):
            mark = BEST_LAP_MARK if s.idx == best else ""
            warn = DROPOUT_SUFFIX if s.idx in dropouts else ""
            lap_item = QTableWidgetItem(f"{mark}{s.idx + 1}{warn}")  # 1-based, the app-wide rule
            if warn:
                lap_item.setToolTip(DROPOUT_TOOLTIP)
            if s.idx == best:
                lap_item.setForeground(best_colour)
            self.lap_table.setItem(r, 0, lap_item)

            def num(v, fmtstr):
                return self._num_item(fmtstr.format(v) if v is not None else DASH)
            time_item = self._num_item(fmt_time(s.time))
            if timing_note:
                time_item.setForeground(PROVISIONAL_COLOR)
                theme.apply_provisional_style(time_item)
                time_item.setToolTip(timing_note)
            self.lap_table.setItem(r, 1, time_item)
            for c, kmh in ((2, s.vmax_kmh), (3, s.avg_kmh), (4, s.vmin_kmh)):
                self.lap_table.setItem(
                    r, c, num(units.convert_speed(kmh, unit)
                              if kmh is not None else None, "{:.1f}"))
            self.lap_table.setItem(r, 5, num(s.peak_lat_g, "{:.2f}"))
            self.lap_table.setItem(r, 6, num(s.peak_brake_g, "{:.2f}"))
            self.lap_table.setItem(r, 7, num(s.brake_s, "{:.1f}"))
            self.lap_table.setItem(r, 8, num(s.coast_s, "{:.1f}"))
        self._fit_table(self.lap_table)
