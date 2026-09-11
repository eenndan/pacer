"""Session-level statistics (the Stats page): pure reducers + the SessionStats service.

PACER-FREE BY CONTRACT (numpy only, no Qt). Two layers, mirroring the established split
(studio/consistency.py for the pure math, studio/driving_channels.py for the DI service):

  * module functions — pure numpy reducers over plain arrays/lists, unit-tested directly;
  * SessionStats — the Session-composed service. All inputs are Session-bound callables
    (Session owns the pacer side + the g-meter), so NO method here reaches a `_`-private
    attribute of Session. Trace-level results (totals) depend only on the constant trace and
    survive a re-segment; lap-level results are projected through the segmentation and are
    dropped by invalidate() (Session.set_timing_lines), exactly like the driving channels.

Everything here is a REDUCTION of channels the app already computes and trusts — lap arrays,
the validated g-meter series, the brake/coast event lists — never a new estimate. Peak
longitudinal g reads the GPS speed-derivative (long_g_gps) when present, the same validated
signal the dial/brake channels use (the IMU forward axis is vibration-inflated).

The BAND DISTRIBUTIONS (speed_bands / lateral_g_bands, the Stats page's DISTRIBUTIONS charts) are
reductions too, of the same two channels — but they are TIME-WEIGHTED, which is a contract rather
than an implementation detail and is carried by the `Bands` type: a bar is seconds PER LAP, never a
sample count, so the bands of one chart sum to a real lap time and two groups of different sizes
can be laid over each other. The fastest/slowest comparison pools QUARTILES rather than pairing the
best lap against the median lap, because the pair was measured and does not separate (SPLIT_DENOM
carries the numbers).

STINTS (`stints()` -> `Stint`) are the one reduction here that is not of a channel but of the
SEGMENTATION: a run on track is what is left between the stretches of recording no lap was
analysed over. The STINTS block below sets out why that is the only thing this app can see of a
pit stop, why the threshold is in the track's own units, and why the absolute GPS epoch is not
consulted. SPLITS (`split_matrix()` -> `SplitMatrix`) is pure presentation over
`Session.lap_sector_splits`, and its constants exist to keep a heat grid from tinting the 10 Hz
sample grid its interior columns are quantized to."""

from __future__ import annotations

import datetime
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .consistency import sigma

# "moving" threshold, m/s — the SAME cutoff the g-meter/thresholds use for their moving
# masks (gmeter._MOVING_MS), so "time moving" and every g statistic agree on what counts
# as driving vs sitting in the pits.
MOVING_MS = 4.0
# A media-clock step longer than this is a GPS dropout / chapter seam, not a real 10 Hz
# sample interval — moving time skips such steps (conservative: a gap while moving is NOT
# counted as time on track, because nothing was measured there).
MAX_SAMPLE_GAP_S = 1.0
# Path-distance chord gate. A GPS fix can jump: on one 9.6 s clip a single 177.8 m chord spanned
# 56 ms (~11 500 km/h) and, summed un-gated, rendered 2.3 km of "distance" for a trace whose own
# speed channel caps it at 72 m. The lap/speed/g pipelines all reject such a fix; the session
# total was the one place it still landed. So each chord is weighed against what the trace's OWN
# speed says was possible over the same interval. The tolerance is slack, not licence: the trace
# is position-smoothed while `speed` is the GPS-reported scalar, so the two disagree by a few
# percent on a hard corner — 1.5x covers that with room, and a teleport misses it by 100x.
CHORD_SPEED_TOL = 1.5
# Below this share of the raw chord LENGTH surviving the gate, what is left is not a measurement
# of a path — the view says so with a dash instead of printing a number.
MIN_KEPT_FRAC = 0.5
# Sample floor for any statistic that describes a DISTRIBUTION of laps (spread, banked-pace
# count) — the same floor consistency.sigma applies. One lap has no spread and is trivially
# within 1% of itself; printing "+0.00 s" and "1 / 1" dresses that up as a measurement.
MIN_DIST_LAPS = 2
# Pace-trend gate: below this many clean laps a fitted slope is noise dressed as insight,
# so the trend statistic reports None and the tile shows a dash.
TREND_MIN_LAPS = 6
# Pace-trend verdict band: a fitted slope within ±this (s/lap) reads "steady" — don't narrate
# noise as a trend. Lives beside the statistic it qualifies rather than in the view, because the
# exported report and the clipboard summary print the same verdict off the same slope and a
# second copy of the band is how the file starts disagreeing with the screen.
TREND_STEADY_BAND = 0.02
# "Race pace" window: the best mean of this many CONSECUTIVE clean laps — the sustained-run
# number next to the single glory lap.
RACE_PACE_N = 3
# The demonstrated combined-g envelope percentile — the SAME robust p98 convention as the
# driving channels' friction-circle envelope (driving.grip_envelope), so the dashed ring on
# the g-g plot and the per-corner grip normalisation can never disagree in spirit.
ENVELOPE_PCT = 98.0

# --------------------------------------------------------------------- band distributions
# THE BAND WIDTHS, and both are measured rather than picked.
#
# Speed: 5 km/h. On the two D24 recordings the clean laps span 13.6-90.4 and 25.3-88.2 km/h, so
# that is 17 and 13 bands, of which 13 and 12 carry at least 0.5 % of a lap each. Halving it to
# 2.5 km/h does not buy resolution, it buys noise: the median clean lap's distance from the
# session's own average profile rises from 0.074 to 0.090 (0060) and 0.058 to 0.080 (0062) — the
# extra bands fill with lap-to-lap scatter rather than with structure.
SPEED_BAND = 5.0
# ...and 2.5 mph on an mph page, WHICH IS NOT THE SAME NUMBER ON PURPOSE. A band is only worth
# counting in if it is round in the unit on the axis, but the chart must not change RESOLUTION when
# a reader flips View ▸ Units: 2.5 mph is 4.02 km/h — within a fifth of the km/h band, and 16-20
# bands on these recordings — while a matching "5 mph" would be 8.05 km/h and collapse the 0062
# session to EIGHT bars.
SPEED_BAND_MPH = 2.5
# Lateral g: 0.2 g, SIGNED and centred on zero.
#
# 0.2 g because that is where the bars stop being a comb: 20 bands of which 15 carry ≥0.5 % on both
# recordings, against 0.1 g's 36-40 bands (29 carrying) and 0.25 g's 16 (12-13). The fast/slow
# separation is unmoved by the choice (TVD 0.061/0.052 at 0.2 g against 0.066/0.054 at 0.1), so the
# tie goes to the width a reader can actually count.
#
# SIGNED because the distribution is not one hump — it is three: a spike at zero (the straight-line
# running, 18-19 % of the lap) and a corner hump either side. Those humps are the same HEIGHT in g
# on both recordings (p98 +1.36 / −1.38 on 0060, +1.33 / −1.34 on 0062) and very different in TIME
# (34.8 s per lap outside the centre band turning left against 21.5 turning right, and 35.2 against
# 21.9) — the track's handedness, which an absolute-value axis folds away for nothing.
LAT_G_BAND = 0.2
# The fastest/slowest comparison: QUARTILES of the clean laps, floored at three laps a side.
#
# WHY A POOLED GROUP AND NOT THE BEST LAP AGAINST THE MEDIAN LAP. Because the pair does not
# separate. Over the 38 and 65 clean laps of the two D24 recordings the best lap's speed profile
# differs from the median lap's by a total-variation distance of 0.113 and 0.089 — against a median
# of 0.105 and 0.087 over ALL pairs of clean laps. It sits at the 60th and 54th percentile of the
# null: two laps picked at random differ by the same amount. Lateral g is the same story (0.100 and
# 0.110, at the 40th and 64th percentile). A chart of one lap against one other lap is a chart of
# lap-to-lap noise with two laps' names on it.
#
# Pooled quartiles do separate, and the separation survives a permutation test on both recordings:
# fastest-quarter vs slowest-quarter TVD 0.073 and 0.061 against a shuffled-label p95 of 0.055 and
# 0.034 (z = +3.7 and +6.5) for speed, 0.070 and 0.054 against 0.050 and 0.035 (z = +4.5 and +5.8)
# for lateral g. Pooling is what buys it: the noise on a group of n laps falls as sqrt(n) while the
# fast/slow difference does not.
SPLIT_DENOM = 4           # quartiles — the fastest quarter against the slowest quarter
SPLIT_MIN_SIDE = 3        # ...but never fewer than three laps a side
# ...so a comparison needs this many clean laps. At 3 a side the split still clears the permutation
# null on both recordings (0.150 vs a p95 of 0.099, and 0.110 vs 0.079), and below it the two
# "groups" would be one or two laps each — the single-pair defect above, wearing a group's clothes.
MIN_SPLIT_LAPS = 2 * SPLIT_MIN_SIDE + 2

# --------------------------------------------------------------------- stints
# A STINT IS A RUN ON TRACK, and the only thing this app can see of one is TIME THAT IS NOT
# ANALYSED. Laps tile the trace — pacer cuts at start-line crossings, so lap k+1 begins at the
# instant lap k ends and the media gap between two ADJACENT laps is exactly 0.000 s (measured:
# every one of the 37 and 64 adjacencies on the two D24 recordings). A break can therefore only
# show up as laps MISSING from the analysed set: the long lap that contains a pit stop falls
# outside the ±band and is excluded, a dropout lap is flagged ⚠ and dropped from the clean set,
# and what is left is a hole whose WIDTH is the seconds the driver spent not being measured.
#
# THAT WIDTH IS THE SIGNAL, and it is the one thing a lap-id gap alone cannot give: one missing
# lap (a dropout, ~69 s here) and six missing laps (a pit stop, ~7 min) are the same "gap of 1 in
# the id sequence" reading and completely different events.
#
# NOT THE ABSOLUTE EPOCH. studio/load.py's header says the GPS9 UTC epoch is not trusted across
# chapters — only its per-sample SPACING is, re-anchored per contiguous run — so a stint boundary
# read off `timestamp_ms` would be reading a number the timing pipeline deliberately does not use.
# It costs nothing here: measured on both recordings the epoch and the analysis clock agree to
# 0.000 s end to end (0 deltas over 1 s in 28,237 and 46,761 samples, 0 negative), so the analysis
# clock IS the wall clock wherever there is one, and it stays right where there is not.
#
# THE THRESHOLD IS IN THE TRACK'S OWN UNITS. An absolute "120 s" means five missed laps at a 25 s
# kart circuit and half a missed lap at a 4-minute one. Three median laps' worth of unmeasured
# time is the break: one or two laps lost to a dropout is not a run change, and at D24's 69.2 s
# median that puts the threshold at 208 s against the 69 s a single dropped lap costs.
STINT_GAP_LAPS = 3.0
# ...with a floor, because the scaling has to survive a very short lap: the quickest kart laps run
# 20-30 s, where three of them is a spin-and-recover rather than a new run.
STINT_GAP_MIN_S = 90.0
# The min-corner-speed trend's "steady" band (km/h per lap) — the tyre channel's TREND_STEADY_BAND.
# MEASURED as a shuffled-label null: re-order the same per-lap minimum speeds at random and the
# Theil-Sen slope still lands inside ±0.133 km/h/lap on the 38-lap recording and ±0.029 on the
# 65-lap one (400 permutations each). 0.15 clears the wider of the two, so "grip is fading" is
# never printed for a slope that shuffling the laps would have produced anyway. Both recordings
# read INSIDE the band on their own (+0.142 and -0.026), i.e. grip held on both.
VMIN_STEADY_BAND = 0.15

# --------------------------------------------------------------------- the split-time matrix
# The laps x sectors grid. Two numbers it needs and only measurement can give.
#
# THE 10 Hz FLOOR IS NOT UNIFORM ACROSS THE ROW. A split is read by projecting the sector line's
# midpoint onto the lap and taking the elapsed time at the NEAREST TRACE POINT (see
# Session.lap_sector_splits), so every interior boundary lands on a GPS sample while the lap's own
# start and finish are interpolated along the chord. The first and last sub-sector are therefore
# continuous and the ones between them step in whole samples: with three sector lines the two
# interior columns take 17-18 distinct values across 38 and 65 laps, stepping ~0.05-0.10 s over a
# total spread of 1.2-3.6 s, while the two end columns take a different value on every single lap.
# A grid that tinted those middle columns finely would be painting the sample grid.
#
# So the tint saturates no finer than this, ~3 sample steps — the smallest difference the
# measurement can actually resolve in its coarsest column.
MATRIX_SCALE_MIN_S = 0.30
# TWO ANCHORS, AND THEY ARE DIFFERENT ON PURPOSE. The ★ marks each column's BEST — a fact, the
# quickest anyone went through that sector, and the target. The behind mark is measured against
# that column's MEDIAN — how the sector normally goes for this driver — because the question a
# heat grid answers is "which lap lost time HERE", and a minimum cannot answer it.
#
# RENDERED, NOT REASONED. The first cut measured the mark against the column best on one pooled
# scale and the grid came out wrong on the owner's own recording: D24 0060 with three sector lines
# has an S2 best of 15.40 s against a column median of 16.70 and a σ of 0.38 — one lap 0.8 s clear
# of every other — so 14 of that column's 38 cells were flagged for being ordinary. Scaling per
# column fixed the pooling half and not the freak half: where a column is TIGHT behind a freak
# best, every gap-to-best is nearly the same number, so its own 90th percentile is that number and
# the mark lands on almost the whole column again. Against the median both failure modes go: the
# freak is one cell below its own median and moves nothing, and each sector is scaled by its own
# spread (the same three lines give per-column σ of 1.34 / 0.38 / 0.56 / 0.83 s — one scale over
# all four is too coarse for the tight ones and too fine for the loose one, whichever it takes).
#
# So: `scales[c]` is the 90th percentile of column c's own deviations above its median, floored.
# The FLOOR is what keeps a percentile from being a rank — on a session where every lap is within
# a tenth, the p90 deviation falls under MATRIX_SCALE_MIN_S, the floor wins and nothing is marked,
# rather than 10 % of the grid being marked because 10 % of anything is always the worst 10 %.
#
# And a percentile needs values to be a percentile of rather than a maximum, which is what sets the
# lap floor: five laps over the thinnest useful grid (one sector line, two columns) is ten cells,
# and below that the "p90" is simply the worst cell. A heat grid over three laps is decoration.
MATRIX_MIN_LAPS = 5
MATRIX_SCALE_PCT = 90.0
# The decimals a split is PRINTED to, and a contract rather than a formatting detail — see
# SplitMatrix.is_behind. It lives here because the comparison that decides the mark has to be made
# at the same resolution the view prints, and only one of the two can own that number.
MATRIX_DECIMALS = 2


# --------------------------------------------------------------------- value objects
@dataclass(frozen=True)
class SessionTotals:
    """Whole-recording totals (trace-level — independent of the lap segmentation)."""

    duration_s: float          # recorded span, first→last kept sample (media clock, incl. gaps)
    moving_s: float            # time with speed ≥ MOVING_MS (dropout gaps excluded)
    distance_m: float | None   # speed-gated path length of the smoothed trace (sum of chords);
    #                            None when too little of it survived the gate to mean anything
    distance_kept_frac: float  # share of the raw chord length the gate kept (1.0 = a clean trace)
    start_clock: str | None    # local wall-clock "HH:MM" of the first kept fix (GPS9); None GPS5
    end_clock: str | None      # …and of the last kept fix


@dataclass(frozen=True)
class PathDistance:
    """A gated path length plus how much of the raw chord sum it threw away — so a caller can
    tell "the trace is 2.1 km" from "the trace is mostly GPS glitches" (see CHORD_SPEED_TOL)."""

    metres: float        # the chords that passed the speed gate
    kept_frac: float     # metres / (the un-gated chord sum); 1.0 when nothing was rejected
    rejected_n: int      # how many chords the gate refused


@dataclass(frozen=True)
class LapStat:
    """One valid lap's statistics row. None = the underlying signal is absent (no g-meter),
    NOT a zero — the view renders those as a dash, never as 0."""

    idx: int                    # lap id (0-based, same as LapRow["idx"])
    time: float                 # lap time (s)
    vmax_kmh: float | None      # max full_speed on the lap
    avg_kmh: float | None       # odometer / lap time — the distance-true average
    vmin_kmh: float | None      # min full_speed on the lap — the slowest-corner speed
    peak_lat_g: float | None    # max |lateral g| (IMU lateral — the trusted axis)
    peak_brake_g: float | None  # max deceleration, reported positive (validated GPS-derived long)
    brake_s: float | None       # total time in brake events
    brake_n: int | None         # number of brake events
    coast_s: float | None       # total time coasting
    coast_frac: float | None    # coast_s / lap time


@dataclass(frozen=True)
class CornerReport:
    """One corner's whole-session statistics row (the Stats page's CORNERS table): the
    session's demonstrated best/typical/spread through the corner, the apex speeds, and
    the median grip utilization. None = not derivable (no g signal / <2 laps), never 0."""

    cid: int                        # Corner.cid (1-based, track order)
    direction: int                  # +1 left / -1 right (Corner.direction)
    n: int                          # included laps with a finite time in this corner
    best_s: float | None            # session-best time-in-corner
    median_s: float | None          # the typical lap's time-in-corner
    sigma_s: float | None           # sample σ (ddof=1; None with <2 laps)
    median_loss_s: float | None     # median − best (≥ 0): what the typical lap gives away
    apex_best_kmh: float | None     # the fastest apex speed carried through
    apex_median_kmh: float | None
    grip_median: float | None       # median per-lap grip utilization (0..~1.1); None w/o g
    score: float                    # σ × median_loss — the inconsistency weight (0.0 when
    #                                 either input is missing), same product as consistency.py


@dataclass(frozen=True)
class BrakeConsistency:
    """One corner's braking repeatability + commitment over the included laps (the BRAKING
    table). Onsets are compared in the REFERENCE odometer (each lap's onset scaled by
    ref_total/lap_total — the house normalized projection), so cross-lap σ measures the
    DRIVER's scatter, not lap-length drift. Honesty floor: at 10 Hz a ~15 m/s kart moves
    ~1.5 m per fix, so a σ at or below that is measurement quantization, not driving."""

    cid: int                        # Corner.cid (1-based, track order)
    n: int                          # laps with a matched brake event into this corner
    median_dist_m: float | None     # median onset (reference odometer)
    sigma_m: float | None           # cross-lap σ of the onset (m); None with <2 laps
    span_m: float | None            # max − min onset spread (m)
    commit_pct: float | None        # median (event peak decel / session a_max) × 100
    metres_later_med: float | None  # median metres-left-on-table (optimal − actual; + = can
    #                                 brake later). ESTIMATED, from the D4 brake-point model.


@dataclass(frozen=True)
class StraightStat:
    """One straight's session stats (the STRAIGHTS table). Straight k runs corner k's exit →
    corner k+1's entry; k=0 is start line → C1 and k=N is C_N → start line — on a circuit
    those two are ONE physical straight split by the timing line. Trap speed = the speed at
    the straight's END (the next corner's entry; the finish-line speed for the last).
    exit_delta_kmh is the PRECEDING corner's median exit speed vs the best lap's (+ = the
    field exits faster than best); leverage = how much a slow exit costs down THIS straight
    (positive deficit × positive time spread) — 'fix the corner before the long straight
    first', measured not modeled."""

    index: int                      # 0-based straight index (0 = start line → C1)
    label: str                      # "S/F → C1", "C3 → C4", "C12 → S/F"
    ring_cid: int                   # the corner feeding this straight (wraps: straight 0 ← C_N)
    n: int                          # laps included
    best_s: float | None            # fastest time down the straight
    median_s: float | None
    sigma_s: float | None           # cross-lap σ (None with <2)
    trap_best_kmh: float | None     # best end-of-straight speed
    trap_median_kmh: float | None
    exit_delta_kmh: float | None    # preceding corner: median exit − best lap's exit (None k=0)
    leverage: float                 # max(0, −exit_delta) × max(0, median − best); 0 when N/A


@dataclass(frozen=True)
class PhaseShare:
    """Where the session's corner time goes: the POSITIVE-part column sums of the per-corner
    median (entry, apex, exit) losses — seconds a typical lap gives away per phase. Positive
    part on purpose: this is "where time is LOST" accounting, so a phase the driver GAINS in
    (negative median) must not cancel losses elsewhere."""

    entry_s: float
    apex_s: float
    exit_s: float

    @property
    def total_s(self) -> float:
        return self.entry_s + self.apex_s + self.exit_s

    def fracs(self) -> tuple[float, float, float] | None:
        """(entry, apex, exit) as fractions of the lost total — the "you lose 61% of your
        corner time on entry" headline. None when nothing is lost (degenerate)."""
        t = self.total_s
        if t <= 0:
            return None
        return (self.entry_s / t, self.apex_s / t, self.exit_s / t)


@dataclass(frozen=True)
class PhaseReport:
    """The session phase-loss matrix: per corner (aligned to `cids`) the MEDIAN
    (entry, apex, exit) Δt-vs-best triple over the included laps (None where no lap had a
    finite triple), plus the session-wide PhaseShare (None when nothing is lost)."""

    cids: list[int]
    rows: list[tuple[float, float, float] | None]
    share: PhaseShare | None


@dataclass(frozen=True)
class PaceStats:
    """Lap-time distribution over the consistency laps (valid ∧ dropout-free)."""

    n: int                # laps in the distribution
    best: float           # min lap time (s)
    median: float         # median lap time (s)
    sigma: float | None   # sample σ (ddof=1; None with <2 laps — consistency.sigma)
    spread: float | None  # median − best: what the TYPICAL lap gives away (s, ≥ 0). None below
    #                       MIN_DIST_LAPS — with one lap the median IS the best and the honest
    #                       answer is "unknown", not the +0.00 s that reads as a measurement.


@dataclass(frozen=True)
class Stint:
    """One run on track: the clean laps between two breaks, and its own pace (see the STINTS
    block above for what a break is and why it is measured on the analysis clock).

    TWO TRENDS AND NO VERDICT. `trend` is the lap time fading or improving; `vmin_trend` is the
    same fit over the slowest corner speed of each lap. Read together they are the answer to "is
    it me or the tyres?" — lap time fading WITH the minimum corner speed falling away is grip
    going off; lap time fading with corner speed holding (and σ widening) is the driver. The pair
    is reported, the conclusion is not: the app has never measured a stint with a real fade in it
    (both D24 recordings read steady on BOTH channels against their own shuffled-label nulls), so
    a rule that named the cause would be an untested inference printed with a measurement's
    authority. Both slopes carry the same TREND_MIN_LAPS gate as the session trend tile."""

    index: int                   # 1-based, session order
    lap_ids: list[int]           # the clean laps in it, ascending
    start_s: float               # media time the first lap started
    end_s: float                 # ...and the last one finished
    gap_before_s: float | None   # unanalysed seconds since the previous stint; None on the first
    best: float                  # quickest lap in the stint (s)
    median: float                # typical lap (s)
    sigma: float | None          # sample σ (ddof=1); None below MIN_DIST_LAPS
    trend: float | None          # lap-time slope, s per lap (Theil–Sen); None below TREND_MIN_LAPS
    vmin_median: float | None    # typical slowest-corner speed (km/h); None with no speed channel
    vmin_trend: float | None     # ...and its slope, km/h per lap; None below TREND_MIN_LAPS

    @property
    def n(self) -> int:
        return len(self.lap_ids)

    @property
    def duration_s(self) -> float:
        """First green flag to last chequer — the stint's own span, gaps inside it included."""
        return self.end_s - self.start_s


@dataclass(frozen=True)
class SplitMatrix:
    """The laps × sectors grid behind the Stats page's SPLITS heat table.

    `cells[r][c]` is lap `lap_ids[r]`'s split through sub-sector c, or None where that lap's
    projection produced no comparable row (never 0 — a missing split is missing). `bests` /
    `medians` / `scales` are per COLUMN, and `best_lap[c]` is the lap that owns column c's best.
    `scales[c]` is how far ABOVE THAT COLUMN'S MEDIAN a cell has to be to read as behind — a
    robust percentile of the column's own upper spread, floored at what the 10 Hz projection can
    resolve. See MATRIX_SCALE_MIN_S for why the mark is anchored on the median while the ★ is
    anchored on the best, and why neither is one number for the whole grid."""

    lap_ids: list[int]
    columns: int
    cells: list[list[float | None]]
    bests: list[float | None]
    medians: list[float | None]
    best_lap: list[int | None]
    scales: list[float]

    # A MARK MUST NOT SPLIT A TIE THE DISPLAY CANNOT SHOW, which is why both predicates live here
    # instead of being re-derived by the view from `cells` and `bests`: each compares the value the
    # READER IS SHOWN, rounded to MATRIX_DECIMALS, and never the float behind it.
    #
    # It is not a theoretical nicety, and both halves were caught on the owner's own recordings by
    # rendering the grid. Interior splits are differences of two GPS sample times, so they live on
    # a ~0.0998 s grid:
    #
    #   * a threshold derived from those same values lands exactly ON one of its steps — D24 0060
    #     with three lines put S2's at 17.1000 with cells at both 17.099 and 17.100, so two cells
    #     printing the identical `17.10` came out one marked and one plain;
    #   * and a column's minimum is routinely tied at print: 0062 with five lines has SIX cells
    #     reading 11.30 in S3, of which one happened to be 0.001 s quicker. A ★ on that one is a
    #     distinction the measurement does not support and the page cannot show.
    def is_behind(self, row: int, col: int) -> bool:
        """Does this cell read as notably slower than its sector's typical lap?"""
        val = self.cells[row][col]
        med = self.medians[col]
        if val is None or med is None:
            return False
        return round(round(val, MATRIX_DECIMALS) - med, MATRIX_DECIMALS) >= round(
            self.scales[col], MATRIX_DECIMALS)

    def is_best(self, row: int, col: int) -> bool:
        """Is this cell the sector's best — or timed level with it? `best_lap[col]` stays the one
        lap that owns the minimum (it is what a tooltip names); this is what gets the ★."""
        val = self.cells[row][col]
        best = self.bests[col]
        if val is None or best is None:
            return False
        return round(val, MATRIX_DECIMALS) <= round(best, MATRIX_DECIMALS)


@dataclass(frozen=True)
class Bands:
    """One channel's TIME-WEIGHTED distribution over a set of laps: SECONDS PER LAP in each band.

    Seconds per lap and not raw seconds, and not a sample count — that is the whole weighting
    contract of this type and the reason it is a type at all:

      * TIME, because "how much of the lap is spent here" is the question a driver is asking.
        A sample count would answer it only for a channel sampled at a constant rate; the GPS
        speed trace is not (a dropout leaves a hole), and a count would silently weigh a
        one-sample band the same as a one-second one.
      * PER LAP, because these are compared across groups of different sizes — the fastest
        quarter against the slowest quarter. Raw seconds would make the bigger group taller
        everywhere and say nothing.

    `seconds` sums to the mean of the laps' MEASURED time less any dropout gap inside them (see
    sample_durations), so a band read off this chart is a real share of a real lap."""

    edges: np.ndarray            # len n+1, the band boundaries in the channel's own unit
    seconds: np.ndarray          # len n, mean seconds per lap in each band
    laps: int                    # how many laps the mean is over
    lap_ids: tuple[int, ...]     # ...and which — so a caption can name the sample
    median_lap_s: float | None   # the group's median lap time; None with no lap

    @property
    def centres(self) -> np.ndarray:
        return 0.5 * (self.edges[:-1] + self.edges[1:])

    @property
    def total_s(self) -> float:
        """Seconds per lap over every band — the measured driving time one lap accounts for."""
        return float(np.sum(self.seconds))

    def carrying(self, floor_frac: float = 0.005) -> int:
        """How many bands hold at least `floor_frac` of the lap — the honest "how many bars is
        this chart really made of", as against how many were drawn."""
        total = self.total_s
        if total <= 0:
            return 0
        return int(np.sum(self.seconds / total >= floor_frac))


@dataclass(frozen=True)
class BandReport:
    """One channel's distribution over the clean laps, plus the fastest/slowest comparison.

    ONE SET OF EDGES for all three, which is why this is a report and not three calls: two
    distributions binned differently are not a comparison, they are two pictures. `fast`/`slow`
    are None together, below MIN_SPLIT_LAPS (see the constant for the measured reason)."""

    all: Bands
    fast: Bands | None
    slow: Bands | None

    @property
    def edges(self) -> np.ndarray:
        return self.all.edges

    @property
    def has_split(self) -> bool:
        return self.fast is not None and self.slow is not None


# --------------------------------------------------------------------- pure reducers
def sample_durations(times, max_gap_s: float = MAX_SAMPLE_GAP_S) -> np.ndarray:
    """Seconds each sample of a time series stands for — the weight a time-weighted histogram
    gives it. Same length as `times`.

    TRAPEZOIDAL (each sample takes half of the interval either side of it), NOT the leading-sample
    attribution `moving_time_s` uses. The two answer different questions: `moving_time_s` thresholds
    on the leading sample's own speed, so an interval belongs wholly to the sample whose speed
    decided it, while a distribution has to RECONCILE — the bands of one lap must sum to that lap's
    measured time, and leading attribution drops the final interval on the floor.

    A step longer than `max_gap_s` is a GPS dropout or a chapter seam and contributes to neither of
    its endpoints: nothing was measured across it, and the same conservative rule moving_time_s
    applies (see MAX_SAMPLE_GAP_S). So the sum is the span LESS its gaps, never more."""
    t = np.asarray(times, float)
    n = len(t)
    if n < 2:
        return np.zeros(n)
    dt = np.diff(t)
    dt = np.where(np.isfinite(dt) & (dt > 0) & (dt <= max_gap_s), dt, 0.0)
    w = np.zeros(n)
    w[:-1] += dt / 2.0
    w[1:] += dt / 2.0
    return w


def band_edges(values, width: float, *, centre_on_zero: bool = False) -> np.ndarray:
    """Band boundaries of `width` covering every finite value, aligned to the width.

    `centre_on_zero` puts a band ON zero (edges at ±width/2, ±3·width/2, …) instead of at it. That
    is for a SIGNED channel, and it is not cosmetic: with zero on an edge the lateral-g chart's
    straight-line time — a fifth of the lap — splits across two adjacent bars as a fake "slightly
    left / slightly right" pair, and the left and right corner humps then sit at different offsets
    from the middle and stop being comparable by eye.

    An empty / all-NaN input gets one band, so a caller never has to special-case the shape."""
    v = np.asarray(values, float)
    v = v[np.isfinite(v)]
    if len(v) == 0:
        return np.array([0.0, width])
    if centre_on_zero:
        # `k` counts the bands OUTSIDE the centre one, each side, so the axis comes out symmetric:
        # 2k+2 edges over 2k+1 bands, running ±(k + ½)·width. Offsetting a plain arange(-k, k+1)
        # by half a band instead is the obvious spelling and is wrong — it shifts every edge the
        # same way and the axis lands at −1.9 … +2.1, a whole band longer on the right.
        k = max(0, int(np.ceil((float(np.max(np.abs(v))) - width / 2.0) / width)))
        return (np.arange(-k - 1, k + 1, dtype=float) + 0.5) * width
    lo = np.floor(float(np.min(v)) / width) * width
    hi = np.ceil(float(np.max(v)) / width) * width
    if hi <= lo:
        hi = lo + width
    return np.arange(lo, hi + width / 2.0, width)


def band_seconds(values, durations, edges) -> np.ndarray:
    """Seconds spent in each band: the time-weighted histogram of `values`. Values outside
    `edges` are dropped (the edges are derived from the data, so this is the NaN case)."""
    v = np.asarray(values, float)
    w = np.asarray(durations, float)
    n = min(len(v), len(w))
    v, w = v[:n], w[:n]
    keep = np.isfinite(v) & np.isfinite(w) & (w > 0)
    if not np.any(keep):
        return np.zeros(len(np.asarray(edges)) - 1)
    return np.histogram(v[keep], bins=np.asarray(edges, float), weights=w[keep])[0]


def pace_split(lap_ids, lap_times) -> tuple[list[int], list[int]] | None:
    """(fastest laps, slowest laps) — the quartile split the band comparison pools over, or None
    below MIN_SPLIT_LAPS finite laps (see the constant for why a single best-vs-median pair is not
    a comparison at all).

    Ties are broken by lap id, so the split is deterministic: two laps of identical time can never
    swap sides between refreshes and move the chart."""
    pairs = [(float(t), int(i)) for i, t in zip(lap_ids, lap_times, strict=True)
             if np.isfinite(t)]
    if len(pairs) < MIN_SPLIT_LAPS:
        return None
    pairs.sort()
    q = max(SPLIT_MIN_SIDE, len(pairs) // SPLIT_DENOM)
    return [i for _t, i in pairs[:q]], [i for _t, i in pairs[-q:]]


def moving_time_s(times, speed_ms, threshold_ms: float = MOVING_MS) -> float:
    """Seconds spent at speed ≥ `threshold_ms`. Each inter-sample interval is attributed to
    its LEADING sample's speed; intervals longer than MAX_SAMPLE_GAP_S (dropouts / chapter
    seams) are skipped — nothing was measured there, so they count as neither moving nor
    stopped (see the module constant)."""
    t = np.asarray(times, float)
    v = np.asarray(speed_ms, float)
    n = min(len(t), len(v))
    if n < 2:
        return 0.0
    dt = np.diff(t[:n])
    keep = (dt > 0) & (dt <= MAX_SAMPLE_GAP_S) & (v[: n - 1] >= threshold_ms)
    return float(np.sum(dt[keep]))


def path_distance(xs, ys, times=None, speed_ms=None) -> PathDistance:
    """Path length of a local-metre trace: the sum of chords between consecutive samples — the
    same convention as the core's cum_distances odometer. A dropout gap contributes its
    straight-line chord (a slight under-count of the real path, never an over-count).

    With `times` and `speed_ms` supplied, each chord is GATED against what the trace's own speed
    channel allows over that interval (`CHORD_SPEED_TOL × max(v_i, v_i+1) × dt`): a GPS fix that
    teleports is not driving, and un-gated it can dominate the total by 30x (see the constant).
    The bound uses the FASTER of the two endpoint speeds, so a real hard-braking step is never
    clipped. Without them the sum is un-gated, exactly as before."""
    x = np.asarray(xs, float)
    y = np.asarray(ys, float)
    n = min(len(x), len(y))
    if n < 2:
        return PathDistance(metres=0.0, kept_frac=1.0, rejected_n=0)
    d = np.hypot(np.diff(x[:n]), np.diff(y[:n]))
    keep = np.isfinite(d)
    raw = float(np.sum(d[keep]))
    if times is not None and speed_ms is not None:
        t = np.asarray(times, float)
        v = np.asarray(speed_ms, float)
        if len(t) >= n and len(v) >= n:
            dt = np.diff(t[:n])
            v_hi = np.maximum(v[:n - 1], v[1:n])
            limit = CHORD_SPEED_TOL * np.abs(v_hi) * np.abs(dt)
            keep &= np.isfinite(dt) & np.isfinite(v_hi) & (d <= limit)
    kept = float(np.sum(d[keep]))
    return PathDistance(metres=kept,
                        kept_frac=(kept / raw) if raw > 0 else 1.0,
                        rejected_n=int(np.sum(np.isfinite(d)) - np.sum(keep)))


def path_distance_m(xs, ys, times=None, speed_ms=None) -> float:
    """`path_distance(...).metres` — the plain number, for callers that don't need the gate's
    rejection accounting."""
    return path_distance(xs, ys, times, speed_ms).metres


def clock_hhmm(epoch_ms) -> str | None:
    """LOCAL wall-clock "HH:MM" for a GPS9 epoch-ms timestamp, or None when the stream has
    no wall clock (GPS5 reports 0 — the same sentinel session_date() checks). Local for the
    same reason as session_date: the time of day the driver actually experienced."""
    ms = int(epoch_ms)
    if ms <= 0:
        return None
    return datetime.datetime.fromtimestamp(ms / 1000.0).strftime("%H:%M")


def pace_stats(lap_times) -> PaceStats | None:
    """The lap-time distribution summary, or None with no laps. σ via consistency.sigma
    (sample σ, ddof=1) so this can never disagree with the consistency panel. `spread` carries
    the SAME minimum-sample gate as σ (MIN_DIST_LAPS) — gating it here rather than in the view
    is what stops one tile dashing while its neighbour prints a number from the same one lap."""
    a = np.asarray(list(lap_times), float)
    a = a[np.isfinite(a)]
    if len(a) == 0:
        return None
    best = float(np.min(a))
    med = float(np.median(a))
    return PaceStats(n=len(a), best=best, median=med, sigma=sigma(a),
                     spread=(med - best) if len(a) >= MIN_DIST_LAPS else None)


def peak_g(lat_g, long_g) -> tuple[float | None, float | None]:
    """(peak |lateral| g, peak braking g) over a g series — braking is the most NEGATIVE
    longitudinal sample, reported positive, floored at 0 (an all-throttle span brakes 0 g,
    not negative). (None, None) for an empty series."""
    lat = np.asarray(lat_g, float)
    lon = np.asarray(long_g, float)
    lat = lat[np.isfinite(lat)]
    lon = lon[np.isfinite(lon)]
    if len(lat) == 0 or len(lon) == 0:
        return None, None
    return float(np.max(np.abs(lat))), float(max(0.0, -np.min(lon)))


def in_windows_mask(times, windows) -> np.ndarray:
    """Boolean mask: which of `times` fall inside ANY of the (t0, t1) media-clock `windows`
    (t0 inclusive, t1 exclusive — a lap's end instant belongs to the next lap, matching
    lap_window's (start, start + lap_time) convention)."""
    t = np.asarray(times, float)
    mask = np.zeros(len(t), dtype=bool)
    for t0, t1 in windows:
        mask |= (t >= t0) & (t < t1)
    return mask


def sector_medians(splits_by_lap: list[list[float]]) -> list[float | None]:
    """Per-sector-column MEDIAN split over the included laps — the "typical" companion to
    consistency.sector_sigmas, same column convention (widest lap defines the count, a
    column with no finite split reads None)."""
    n_cols = max((len(sp) for sp in splits_by_lap), default=0)
    out: list[float | None] = []
    for k in range(n_cols):
        vals = np.asarray([sp[k] for sp in splits_by_lap if k < len(sp)], float)
        vals = vals[np.isfinite(vals)]
        out.append(float(np.median(vals)) if len(vals) else None)
    return out


def corner_report(cids, directions, times_by_lap, apex_by_lap,
                  grip_by_lap) -> list[CornerReport]:
    """The corner-by-corner session report: one CornerReport per corner (track order).

    Each *_by_lap input is one row per included lap, aligned to `cids` (ragged rows are
    tolerated — column k reads only rows long enough). σ via consistency.sigma (ddof=1);
    score = σ × median_loss, the same both-erratic-AND-slow product the consistency
    ranking uses (rationale in studio/consistency.py) — 0.0 when either input is missing
    so an under-sampled corner never outranks a measured one."""

    def column(rows, k):
        vals = np.asarray([r[k] for r in rows if k < len(r)], float)
        return vals[np.isfinite(vals)]

    out: list[CornerReport] = []
    for k, (cid, direction) in enumerate(zip(cids, directions, strict=True)):
        times = column(times_by_lap, k)
        apex = column(apex_by_lap, k)
        grip = column(grip_by_lap, k)
        n = len(times)
        best = float(np.min(times)) if n else None
        med = float(np.median(times)) if n else None
        sig = sigma(times)
        loss = med - best if n else None
        out.append(CornerReport(
            cid=int(cid), direction=int(direction), n=n,
            best_s=best, median_s=med, sigma_s=sig, median_loss_s=loss,
            apex_best_kmh=float(np.max(apex)) if len(apex) else None,
            apex_median_kmh=float(np.median(apex)) if len(apex) else None,
            grip_median=float(np.median(grip)) if len(grip) else None,
            score=(sig * loss) if sig is not None and loss is not None else 0.0,
        ))
    return out


def brake_consistency(cids, rows_by_lap) -> list[BrakeConsistency]:
    """Aggregate per-lap braking rows into per-corner repeatability + commitment stats.

    `rows_by_lap`: one dict per included lap, cid → (onset_ref_m, commit_frac | None,
    metres_later | None) — a corner absent from a lap's dict simply had no matched brake
    event there (an unbraked or undetected pass; it lowers n, it does not fake a 0)."""
    out: list[BrakeConsistency] = []
    for cid in cids:
        vals = [r[cid] for r in rows_by_lap if cid in r]
        if not vals:
            out.append(BrakeConsistency(cid=int(cid), n=0, median_dist_m=None, sigma_m=None,
                                        span_m=None, commit_pct=None, metres_later_med=None))
            continue
        onsets = np.asarray([v[0] for v in vals], float)
        commits = [v[1] for v in vals if v[1] is not None]
        laters = [v[2] for v in vals if v[2] is not None]
        out.append(BrakeConsistency(
            cid=int(cid), n=len(vals),
            median_dist_m=float(np.median(onsets)),
            sigma_m=sigma(onsets),
            span_m=float(np.max(onsets) - np.min(onsets)),
            commit_pct=float(np.median(commits)) * 100.0 if commits else None,
            metres_later_med=float(np.median(laters)) if laters else None,
        ))
    return out


def straights_report(cids, times_by_lap, traps_by_lap, exits_by_lap,
                     best_exits) -> list[StraightStat]:
    """The straight-line report: per straight (N corners → N+1 straights) the session's
    best/median/σ time + trap-speed best/median, and the preceding corner's exit-speed
    delta + leverage (see StraightStat). `times_by_lap`/`traps_by_lap` rows are aligned to
    the N+1 straights; `exits_by_lap` rows + `best_exits` to the N corners. Ragged rows are
    tolerated (a degenerate lap contributes only the columns it has)."""
    n_corners = len(cids)

    def column(rows, k):
        vals = np.asarray([r[k] for r in rows if k < len(r)], float)
        return vals[np.isfinite(vals)]

    out: list[StraightStat] = []
    for k in range(n_corners + 1):
        times = column(times_by_lap, k)
        traps = column(traps_by_lap, k)
        n = len(times)
        best = float(np.min(times)) if n else None
        med = float(np.median(times)) if n else None
        # The preceding corner's exit delta (median vs the best lap's own exit). k=0's
        # preceding corner is C_N across the timing line — its delta is reported on the
        # LAST straight (same physical exit), so k=0 reads None rather than double-counting.
        delta = None
        if k >= 1:
            exits = column(exits_by_lap, k - 1)
            if len(exits) and k - 1 < len(best_exits) and np.isfinite(best_exits[k - 1]):
                delta = float(np.median(exits)) - float(best_exits[k - 1])
        spread = (med - best) if n else None
        leverage = (max(0.0, -delta) * max(0.0, spread)
                    if delta is not None and spread is not None else 0.0)
        if k == 0:
            label = f"S/F → C{cids[0]}" if n_corners else "S/F"
            ring = int(cids[-1]) if n_corners else 0  # the wrap: C_N feeds the S/F straight
        elif k == n_corners:
            label = f"C{cids[-1]} → S/F"
            ring = int(cids[-1])
        else:
            label = f"C{cids[k - 1]} → C{cids[k]}"
            ring = int(cids[k - 1])
        out.append(StraightStat(
            index=k, label=label, ring_cid=ring, n=n,
            best_s=best, median_s=med, sigma_s=sigma(times),
            trap_best_kmh=float(np.max(traps)) if len(traps) else None,
            trap_median_kmh=float(np.median(traps)) if len(traps) else None,
            exit_delta_kmh=delta, leverage=leverage,
        ))
    return out


def phase_matrix(cids, triples_by_lap) -> PhaseReport:
    """Aggregate per-lap per-corner (entry, apex, exit) Δt-vs-best triples into the session
    phase-loss matrix: per corner the MEDIAN triple (element-wise, over laps with a fully
    finite triple; ragged rows tolerated), plus the positive-part PhaseShare (see the
    dataclass for why positive-part). The per-lap triples come from the SAME drift-gated
    coaching.corner_phase_losses decomposition the coaching reasons use."""
    rows: list[tuple[float, float, float] | None] = []
    e_sum = a_sum = x_sum = 0.0
    for k in range(len(cids)):
        tri = np.asarray([row[k] for row in triples_by_lap if k < len(row)], float)
        if len(tri):
            tri = tri[np.all(np.isfinite(tri), axis=1)]
        if len(tri) == 0:
            rows.append(None)
            continue
        med = np.median(tri, axis=0)
        rows.append((float(med[0]), float(med[1]), float(med[2])))
        e_sum += max(0.0, float(med[0]))
        a_sum += max(0.0, float(med[1]))
        x_sum += max(0.0, float(med[2]))
    share = PhaseShare(e_sum, a_sum, x_sum) if (e_sum + a_sum + x_sum) > 0 else None
    return PhaseReport(cids=list(cids), rows=rows, share=share)


def trend_verdict(slope: float | None) -> str | None:
    """The pace trend's plain-language verdict: "improving" / "fading" / "steady", or None for a
    slope the sample was too short to fit (`TREND_MIN_LAPS`).

    Pure and Qt-free so the Stats tile's caption ("trend · improving") and the exported summary's
    row label are ONE rule. Sign convention is the app's: a negative slope means lap times are
    falling, i.e. the driver is getting faster."""
    if slope is None:
        return None
    if slope <= -TREND_STEADY_BAND:
        return "improving"
    if slope >= TREND_STEADY_BAND:
        return "fading"
    return "steady"


def fmt_trend(slope: float | None) -> str | None:
    """The trend VALUE as both surfaces print it: `+0.14 s/lap`, or a flat `0.00 s/lap` for a
    signed near-zero (a "±0.00" display reads as a glitch). None passes through as None."""
    if slope is None:
        return None
    return "0.00 s/lap" if round(slope, 2) == 0 else f"{slope:+.2f} s/lap"


def theil_sen_slope(values, x=None) -> float | None:
    """Robust trend: the MEDIAN of all pairwise slopes (Theil–Sen), per unit of `x`. Outlier-immune
    — one traffic lap can't fake a trend the way it drags a least-squares fit. None with fewer than
    2 finite values. O(n²) pairs is nothing at session lap counts.

    `x` IS THE POINT (§4.2). Without it the step is the INDEX of the filtered series, so a session
    whose clean laps are 1,2,3,10,11,12 — a pit stop, or three laps lost to a GPS dropout — is fitted
    as if those were six consecutive laps, and a slope reported "per lap" is really per *clean* lap.
    Passing the lap ids makes the gap a gap. Both series are filtered to the finite pairs together,
    so `x` and `values` cannot fall out of step."""
    a = np.asarray(list(values), float)
    xs = np.arange(len(a), dtype=float) if x is None else np.asarray(list(x), float)
    keep = np.isfinite(a) & np.isfinite(xs)
    a, xs = a[keep], xs[keep]
    if len(a) < 2:
        return None
    i, j = np.triu_indices(len(a), k=1)
    dx = xs[j] - xs[i]
    ok = dx != 0
    if not np.any(ok):
        return None
    return float(np.median((a[j][ok] - a[i][ok]) / dx[ok]))


def consecutive_runs(ids) -> list[list[int]]:
    """Split a sorted id list into RUNS of consecutive integers: [1,2,3,10,11] -> [[1,2,3],[10,11]].

    The one thing "3 consecutive laps" needs and a filtered list cannot provide."""
    runs: list[list[int]] = []
    for i in ids:
        if runs and i == runs[-1][-1] + 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def best_consecutive_mean(values, n: int = RACE_PACE_N, ids=None) -> float | None:
    """The best (lowest) mean over `n` CONSECUTIVE values — "race pace": the best sustained
    n-lap run, the honest companion to the single best lap. Windows containing a non-finite
    value are skipped (NaN propagates through the window sum); None when no full window
    exists.

    `ids` MAKES "CONSECUTIVE" MEAN CONSECUTIVE (§4.2). The caller's series is already filtered to
    the clean laps, so adjacency IN THAT LIST is not adjacency on track: a session whose clean laps
    are 1,2,3,10,11,12 would otherwise report a three-lap "sustained run" spanning laps 3→10, i.e.
    across whatever removed 4-9 — a pit stop, a spin, three laps a GPS dropout flagged. With the
    lap ids the window is taken within each run of consecutively-numbered laps and never across a
    gap. Omitted → the old index behaviour, for callers with no ids to give.

    AND THE ID RUN IS THE RIGHT PARTITION, not the stint one — a distinction worth writing down
    because the stint view sitting beside this looks like the stronger rule and is the weaker one.
    Laps TILE the trace (lap k+1 begins where lap k ends, measured 0.000 s on all 101 adjacencies
    of the two D24 recordings), so a break can only appear where laps are missing, which means
    every stint boundary is also an id gap — but not every id gap is a stint boundary. One lap
    dropped for a GPS dropout leaves a ~69 s hole that is far too short to be a run change, and a
    window bridging it would still be averaging two laps that are not next to each other."""
    a = np.asarray(list(values), float)
    if len(a) < n:
        return None
    if ids is None:
        windows = [a]
    else:
        pos = {i: k for k, i in enumerate(ids)}
        windows = [a[[pos[i] for i in run]] for run in consecutive_runs(list(ids))
                   if len(run) >= n]
    best = None
    for w in windows:
        if len(w) < n:
            continue
        means = np.convolve(w, np.ones(n) / n, mode="valid")
        if np.any(np.isfinite(means)):
            m = float(np.nanmin(means))
            best = m if best is None else min(best, m)
    return best


def stint_gap_s(lap_times) -> float:
    """The break threshold for THIS track, in seconds (see the STINTS block): STINT_GAP_LAPS
    median laps' worth of unanalysed time, never below STINT_GAP_MIN_S. Falls back to the floor
    on an empty/degenerate series, which is also the only value a session with no laps can use."""
    a = np.asarray(list(lap_times), float)
    a = a[np.isfinite(a) & (a > 0)]
    if len(a) == 0:
        return STINT_GAP_MIN_S
    return max(STINT_GAP_MIN_S, STINT_GAP_LAPS * float(np.median(a)))


def split_stints(lap_ids, starts, ends, gap_s: float) -> list[list[int]]:
    """Split analysed laps into RUNS ON TRACK: a break wherever `gap_s` or more seconds of the
    recording between two of them was not analysed as a lap.

    `starts[k]` / `ends[k]` are lap `lap_ids[k]`'s media-clock window (Session.lap_window). The
    gap measured is `starts[k] - ends[k-1]`, so what it counts is precisely the time the excluded
    and dropout laps between them took — the pit stop, the spin, the dropout — and nothing else.
    Adjacent laps give exactly 0.0 and never break."""
    ids = list(lap_ids)
    s = list(starts)
    e = list(ends)
    runs: list[list[int]] = []
    for k, i in enumerate(ids):
        gap = None if k == 0 else float(s[k]) - float(e[k - 1])
        if runs and (gap is None or gap < gap_s):
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def stint_rows(lap_ids, lap_times, starts, ends, vmins=None,
               gap_s: float | None = None) -> list[Stint]:
    """One Stint per run on track (see Stint / split_stints). `vmins` is the per-lap slowest
    corner speed in km/h (LapStat.vmin_kmh), aligned to `lap_ids`; None or an all-absent series
    leaves both minimum-speed fields None rather than inventing a zero.

    Every derived statistic carries the gate its session-wide twin carries — σ below
    MIN_DIST_LAPS and both slopes below TREND_MIN_LAPS are None, so a two-lap out/in run reports
    its two lap times and refuses to describe their "trend"."""
    ids = list(lap_ids)
    times = [float(t) for t in lap_times]
    s = [float(x) for x in starts]
    e = [float(x) for x in ends]
    vs = [None] * len(ids) if vmins is None else list(vmins)
    if not ids:
        return []
    gap = stint_gap_s(times) if gap_s is None else float(gap_s)
    pos = {i: k for k, i in enumerate(ids)}
    out: list[Stint] = []
    prev_end: float | None = None
    for n, run in enumerate(split_stints(ids, s, e, gap), start=1):
        ks = [pos[i] for i in run]
        t = np.asarray([times[k] for k in ks], float)
        v = np.asarray([np.nan if vs[k] is None else float(vs[k]) for k in ks], float)
        has_v = bool(np.any(np.isfinite(v)))
        long_enough = len(run) >= TREND_MIN_LAPS
        out.append(Stint(
            index=n,
            lap_ids=list(run),
            start_s=s[ks[0]],
            end_s=e[ks[-1]],
            gap_before_s=None if prev_end is None else s[ks[0]] - prev_end,
            best=float(np.min(t)),
            median=float(np.median(t)),
            sigma=sigma(t),
            trend=theil_sen_slope(t, run) if long_enough else None,
            vmin_median=float(np.nanmedian(v)) if has_v else None,
            vmin_trend=(theil_sen_slope(v, run) if (has_v and long_enough) else None),
        ))
        prev_end = e[ks[-1]]
    return out


def split_matrix(lap_ids, splits_by_lap, columns: int | None = None) -> SplitMatrix | None:
    """The laps × sectors grid (see SplitMatrix). `splits_by_lap[k]` is lap `lap_ids[k]`'s split
    list; rows whose length disagrees with the session's column count are kept but blanked, so a
    lap can never be silently dropped OUT of a table that is indexed by lap.

    None below MATRIX_MIN_LAPS or with fewer than two columns — one column is the lap time, which
    the page prints in four other places."""
    ids = list(lap_ids)
    rows = [list(r) for r in splits_by_lap]
    n_cols = columns if columns is not None else max((len(r) for r in rows), default=0)
    if len(ids) < MATRIX_MIN_LAPS or n_cols < 2:
        return None
    cells: list[list[float | None]] = []
    for r in rows:
        ok = len(r) == n_cols and all(np.isfinite(x) and x > 0 for x in r)
        cells.append([float(x) for x in r] if ok else [None] * n_cols)
    bests: list[float | None] = []
    medians: list[float | None] = []
    best_lap: list[int | None] = []
    scales: list[float] = []
    for c in range(n_cols):
        col = [(k, row[c]) for k, row in enumerate(cells) if row[c] is not None]
        if not col:
            bests.append(None)
            medians.append(None)
            best_lap.append(None)
            scales.append(MATRIX_SCALE_MIN_S)
            continue
        k_best = min(col, key=lambda kv: kv[1])[0]
        med = float(np.median([v for _, v in col]))
        bests.append(cells[k_best][c])
        medians.append(med)
        best_lap.append(ids[k_best])
        scales.append(max(MATRIX_SCALE_MIN_S,
                          float(np.percentile([v - med for _, v in col], MATRIX_SCALE_PCT))))
    return SplitMatrix(lap_ids=ids, columns=n_cols, cells=cells, bests=bests,
                       medians=medians, best_lap=best_lap, scales=scales)


def within_pct_of_best(values, pct: float) -> int | None:
    """How many values sit within `pct` percent of the best (minimum) — the "banked pace"
    count (the best itself counts). 0 for an empty/all-NaN input; None below MIN_DIST_LAPS,
    where the answer is always "all of them" and so measures nothing (the same gate σ and
    `spread` carry — see MIN_DIST_LAPS)."""
    a = np.asarray(list(values), float)
    a = a[np.isfinite(a)]
    if len(a) == 0:
        return 0
    if len(a) < MIN_DIST_LAPS:
        return None
    return int(np.sum(a <= float(np.min(a)) * (1.0 + pct / 100.0)))


def cov_pct(values) -> float | None:
    """Coefficient of variation as a percent: sample σ / median × 100 — the one-number,
    scale-free consistency rating (comparable across tracks/lap lengths, unlike raw σ).
    σ via consistency.sigma (ddof=1); None with <2 finite values or a degenerate median."""
    a = np.asarray(list(values), float)
    a = a[np.isfinite(a)]
    s = sigma(a)
    if s is None:
        return None
    med = float(np.median(a))
    if med <= 0:
        return None
    return s / med * 100.0


def envelope_g(lat_g, long_g, pct: float = ENVELOPE_PCT) -> float | None:
    """The demonstrated combined-g envelope: the `pct`th percentile of hypot(lat, long) —
    robust to lone spikes (the same p98 convention as the driving channels' grip envelope).
    None on an empty series."""
    lat = np.asarray(lat_g, float)
    lon = np.asarray(long_g, float)
    m = min(len(lat), len(lon))
    if m == 0:
        return None
    combined = np.hypot(lat[:m], lon[:m])
    combined = combined[np.isfinite(combined)]
    if len(combined) == 0:
        return None
    return float(np.percentile(combined, pct))


# --------------------------------------------------------------------- the service
class SessionStats:
    """Session statistics over Session-bound primitives (see the module docstring).

    `gmeter` returns the live GMeter (built after construction, so it must be a callable);
    `trace_times` / `trace_speed_kmh` / `trace_xy` return the full smoothed trace (Session.tt /
    .tv / (.tx, .ty)); `wall_clock_ms` the (first, last) kept-fix GPS9 epoch timestamps;
    `valid_lap_ids` / `consistency_lap_ids` the memoized lap sets; `lap_time` / `lap_arrays` /
    `lap_window` the per-lap fetches; `brake_events` / `coast_spans` the driving-channel
    event lists (already cached per lap by DrivingChannels)."""

    def __init__(self, *,
                 gmeter: Callable[[], object],
                 trace_times: Callable[[], np.ndarray],
                 trace_speed_kmh: Callable[[], np.ndarray],
                 trace_xy: Callable[[], tuple[np.ndarray, np.ndarray]],
                 wall_clock_ms: Callable[[], tuple[int, int]],
                 valid_lap_ids: Callable[[], list[int]],
                 consistency_lap_ids: Callable[[], list[int]],
                 lap_time: Callable[[int], float],
                 lap_arrays: Callable[[int], tuple],
                 lap_window: Callable[[int], tuple[float, float] | None],
                 brake_events: Callable[[int], list],
                 coast_spans: Callable[[int], list]):
        self._gmeter = gmeter
        self._trace_times = trace_times
        self._trace_speed_kmh = trace_speed_kmh
        self._trace_xy = trace_xy
        self._wall_clock_ms = wall_clock_ms
        self._valid_lap_ids = valid_lap_ids
        self._consistency_lap_ids = consistency_lap_ids
        self._lap_time = lap_time
        self._lap_arrays = lap_arrays
        self._lap_window = lap_window
        self._brake_events = brake_events
        self._coast_spans = coast_spans
        # totals depend only on the constant trace → computed once, survives re-segments.
        self._totals_cache: SessionTotals | None = None
        # lap-level results are projected through the segmentation → dropped by invalidate().
        self._lap_stats_cache: list[LapStat] | None = None
        self._gg_cache: tuple[np.ndarray, np.ndarray] | None = None
        # …and the band distributions, keyed by their arguments (the speed bands are binned in
        # whatever unit the page displays, so there is one report per unit, not one per session).
        self._bands_cache: dict[tuple, BandReport | None] = {}
        # …and the stint partition, which is a function of WHICH laps survive the bands and where
        # they sit on the clock — both of which a timing-line drag re-decides.
        self._stints_cache: list[Stint] | None = None

    def invalidate(self) -> None:
        """Drop the segmentation-derived caches on re-segment (Session.set_timing_lines);
        the trace totals are unchanged by a timing-line edit and are kept."""
        self._lap_stats_cache = None
        self._gg_cache = None
        self._bands_cache = {}
        self._stints_cache = None

    # ------------------------------------------------------------------ trace level
    def totals(self) -> SessionTotals:
        """Whole-recording totals; cached (the trace never changes for a loaded session)."""
        if self._totals_cache is not None:
            return self._totals_cache
        t = np.asarray(self._trace_times(), float)
        v = np.asarray(self._trace_speed_kmh(), float) / 3.6
        xs, ys = self._trace_xy()
        w0, w1 = self._wall_clock_ms()
        # The trace's own clock + speed gate the chords: a teleporting GPS fix is not distance
        # driven, and it used to be summed straight into this headline (see CHORD_SPEED_TOL).
        dist = path_distance(xs, ys, t, v)
        self._totals_cache = SessionTotals(
            duration_s=float(t[-1] - t[0]) if len(t) >= 2 else 0.0,
            moving_s=moving_time_s(t, v),
            distance_m=dist.metres if dist.kept_frac >= MIN_KEPT_FRAC else None,
            distance_kept_frac=dist.kept_frac,
            start_clock=clock_hhmm(w0),
            end_clock=clock_hhmm(w1),
        )
        return self._totals_cache

    # ------------------------------------------------------------------ lap level
    def lap_stats(self) -> list[LapStat]:
        """One LapStat per VALID lap, in session order; cached per segmentation. Speed stats
        come from the lap's own arrays; g peaks slice the g-meter by the lap's media window;
        brake/coast reduce the driving-channel event lists. Signal-absent fields are None
        (never 0) — see LapStat."""
        if self._lap_stats_cache is not None:
            return self._lap_stats_cache
        gm = self._gmeter()
        has_g = bool(getattr(gm, "has_data", False))
        if has_g:
            long_src = gm.long_g_gps if gm.long_g_gps is not None else gm.long_g
        out: list[LapStat] = []
        for i in self._valid_lap_ids():
            lap_time = float(self._lap_time(i))
            dist, speed_kmh, elapsed = self._lap_arrays(i)
            vmax = float(np.max(speed_kmh)) if len(speed_kmh) else None
            vmin = float(np.min(speed_kmh)) if len(speed_kmh) else None
            avg = (float(dist[-1]) / lap_time * 3.6
                   if len(dist) and lap_time > 0 else None)
            lat_pk = brake_pk = None
            if has_g:
                win = self._lap_window(i)
                if win is not None:
                    m = in_windows_mask(gm.times, [win])
                    lat_pk, brake_pk = peak_g(gm.lat_g[m], long_src[m])
            brake_s = brake_n = coast_s = coast_frac = None
            if has_g:
                events = self._brake_events(i)
                spans = self._coast_spans(i)
                brake_s = float(sum(e.duration for e in events))
                brake_n = len(events)
                coast_s = float(sum(sp.duration for sp in spans))
                coast_frac = coast_s / lap_time if lap_time > 0 else None
            out.append(LapStat(idx=i, time=lap_time, vmax_kmh=vmax, avg_kmh=avg,
                               vmin_kmh=vmin, peak_lat_g=lat_pk, peak_brake_g=brake_pk,
                               brake_s=brake_s, brake_n=brake_n,
                               coast_s=coast_s, coast_frac=coast_frac))
        self._lap_stats_cache = out
        return out

    def pace(self) -> PaceStats | None:
        """Lap-time distribution over the CONSISTENCY laps (valid ∧ dropout-free — the same
        set every σ statistic runs over, so Pace and the consistency panel always agree).
        Cheap (a handful of floats) → not cached, like Session's consistency assemblers."""
        return pace_stats([self._lap_time(i) for i in self._consistency_lap_ids()])

    def _clean_times(self) -> list[float]:
        """The consistency laps' times in session order — the one series every pace-quality
        statistic below runs over (same set as pace(), so the tiles can never disagree)."""
        return [self._lap_time(i) for i in self._consistency_lap_ids()]

    def pace_trend(self) -> float | None:
        """The robust lap-time trend (Theil–Sen median slope, s/lap; negative = getting
        faster) over the clean laps IN SESSION ORDER. Gated at TREND_MIN_LAPS — a slope
        fitted to five laps is noise, so short sessions honestly report None.

        Fitted against the LAP IDS, not the position in the filtered list (§4.2): the excluded and
        dropout laps are holes in the series, and a slope of seconds *per lap* has to count them."""
        times = self._clean_times()
        if len(times) < TREND_MIN_LAPS:
            return None
        return theil_sen_slope(times, self._consistency_lap_ids())

    def race_pace(self) -> float | None:
        """The best mean of RACE_PACE_N consecutive clean laps — the sustained-run pace next
        to the single glory lap. None with fewer than a full window of clean laps.

        CONSECUTIVE ON TRACK (§4.2). The series is already filtered to the clean laps, so a window
        taken over that list can span whatever was removed — a pit stop, a spin, laps a GPS dropout
        flagged. The lap ids go with the times, and the window never crosses a gap."""
        return best_consecutive_mean(self._clean_times(), ids=self._consistency_lap_ids())

    def stints(self) -> list[Stint]:
        """The session's runs on track, in order; cached per segmentation.

        Over the CLEAN laps, the same set every pace statistic above runs over — a run's "best"
        and the PACE group's "best lap" have to be able to be the same lap. The slowest-corner
        speed comes from `lap_stats()`, so the tyre channel is the identical `vmin_kmh` the
        SPEED · G group's "slowest corner" tile reduces, never a second estimate of it."""
        if self._stints_cache is not None:
            return self._stints_cache
        ids = self._consistency_lap_ids()
        windows = [self._lap_window(i) for i in ids]
        # A lap with no window cannot be placed on the clock — drop the pair together rather than
        # substituting a zero, which would read as a break of the whole session's length.
        keep = [k for k, w in enumerate(windows) if w is not None]
        ids = [ids[k] for k in keep]
        windows = [windows[k] for k in keep]
        vmin_by_lap = {st.idx: st.vmin_kmh for st in self.lap_stats()}
        self._stints_cache = stint_rows(
            ids,
            [float(self._lap_time(i)) for i in ids],
            [w[0] for w in windows],
            [w[1] for w in windows],
            vmins=[vmin_by_lap.get(i) for i in ids],
        )
        return self._stints_cache

    def stint_break_s(self) -> float:
        """The unanalysed span this session calls a run change (stint_gap_s over the clean lap
        times) — the number the STINTS note prints, read from the same place the split used it
        rather than re-derived from the rows it produced."""
        return stint_gap_s(self._clean_times())

    def stint_count(self) -> int:
        """How many runs on track the session holds — the SESSION group's "runs" tile. 0 with no
        clean laps; 1 is the ordinary answer for a recording the camera rolled straight through."""
        return len(self.stints())

    def pace_cov(self) -> float | None:
        """The consistency rating: coefficient of variation (σ/median %) of the clean lap
        times — scale-free, so it is comparable across tracks. None with <2 laps."""
        return cov_pct(self._clean_times())

    def laps_within_pct(self, pct: float = 1.0) -> tuple[int | None, int]:
        """(count, n): how many of the n clean laps sit within `pct` % of the session best —
        the "banked pace" count (the best lap itself counts). count is None below MIN_DIST_LAPS
        (one lap is trivially within 1% of itself), so the tile dashes like σ does."""
        times = self._clean_times()
        return within_pct_of_best(times, pct), len(times)

    def longest_coast_s(self) -> float | None:
        """The longest single coasting span (s) across the valid laps — the headline "where
        seconds hide" number next to the median coast tile. None without a g signal; 0.0 is
        a real (and good) zero with one."""
        gm = self._gmeter()
        if not getattr(gm, "has_data", False):
            return None
        longest = 0.0
        for i in self._valid_lap_ids():
            for sp in self._coast_spans(i):
                longest = max(longest, float(sp.duration))
        return longest

    def gg_envelope(self) -> float | None:
        """The demonstrated combined-g envelope (p98 of hypot over the valid-lap g cloud) —
        the dashed ring on the friction circle and the "grip ceiling" tile. None without a
        g signal / valid laps."""
        cloud = self.gg_cloud()
        if cloud is None:
            return None
        return envelope_g(cloud[0], cloud[1])

    def session_vmax(self) -> tuple[float, int] | None:
        """(top speed km/h, lap id) over the valid laps — the session's headline Vmax and
        where it happened. None when no lap has a speed sample."""
        best: tuple[float, int] | None = None
        for st in self.lap_stats():
            if st.vmax_kmh is not None and (best is None or st.vmax_kmh > best[0]):
                best = (st.vmax_kmh, st.idx)
        return best

    # ------------------------------------------------------------------ band distributions
    def _band_report(self, per_lap: Callable[[int], tuple | None], width: float, *,
                     centre_on_zero: bool = False) -> BandReport | None:
        """Bin one channel over the CLEAN laps and split the result fastest-vs-slowest.

        `per_lap(lap_id)` returns that lap's (values, per-sample durations) or None. The edges are
        derived ONCE from every clean lap's samples pooled, so the three distributions in the
        report are the same chart drawn three times and can be read against each other.

        The CLEAN laps (valid ∧ dropout-free), the set PACE and every σ statistic on this page run
        over. A lap with a GPS dropout is exactly the lap whose time-weighting cannot be trusted —
        the hole is not a band, it is an absence — so it is out of the distribution for the same
        reason it is out of the σ."""
        rows: dict[int, np.ndarray] = {}
        values: dict[int, np.ndarray] = {}
        for i in self._consistency_lap_ids():
            got = per_lap(i)
            if got is None:
                continue
            v, w = np.asarray(got[0], float), np.asarray(got[1], float)
            if len(v) == 0 or not np.any(np.isfinite(v)) or float(np.sum(w)) <= 0:
                continue
            values[i] = v
            rows[i] = w
        if not values:
            return None
        edges = band_edges(np.concatenate(list(values.values())), width,
                           centre_on_zero=centre_on_zero)
        hist = {i: band_seconds(values[i], rows[i], edges) for i in values}

        def group(ids: list[int]) -> Bands:
            keep = [i for i in ids if i in hist]
            times = [float(self._lap_time(i)) for i in keep]
            return Bands(edges=edges,
                         seconds=np.mean([hist[i] for i in keep], axis=0),
                         laps=len(keep), lap_ids=tuple(keep),
                         median_lap_s=float(np.median(times)) if times else None)

        ids = sorted(hist)
        split = pace_split(ids, [self._lap_time(i) for i in ids])
        return BandReport(all=group(ids),
                          fast=group(split[0]) if split else None,
                          slow=group(split[1]) if split else None)

    def speed_bands(self, *, scale: float = 1.0,
                    width: float = SPEED_BAND) -> BandReport | None:
        """Time at speed: how many seconds of a lap are spent in each speed band.

        `scale` converts the stored km/h to the unit the page is displaying (units.convert_speed
        of 1.0), and `width` is a band IN THAT UNIT — a reader counts in the number on the axis, so
        the bands are round mph on an mph page rather than round km/h relabelled.

        The 10 Hz GPS speed trace is the one channel on this page whose distribution needs no
        caveat: every sample is a real 100 ms of driving, so a time-weighted histogram of it is
        exactly what it claims to be (contrast the g bands, whose channel is filtered — see
        lateral_g_bands). Cached per (scale, width) and dropped on re-segment."""
        key = ("speed", float(scale), float(width))
        if key not in self._bands_cache:
            def per_lap(i):
                _dist, speed_kmh, elapsed = self._lap_arrays(i)
                n = min(len(speed_kmh), len(elapsed))
                return np.asarray(speed_kmh[:n], float) * scale, sample_durations(elapsed[:n])
            self._bands_cache[key] = self._band_report(per_lap, float(width))
        return self._bands_cache[key]

    def lateral_g_bands(self, *, width: float = LAT_G_BAND) -> BandReport | None:
        """Time at lateral g: how many seconds of a lap are spent at each cornering load, SIGNED
        (+ left / − right, the g-g plot's own convention) and centred on zero.

        THE TRUSTED AXIS AND ONLY THE TRUSTED AXIS. This reads `lat_g` — the IMU-derived lateral,
        which cross-checks against GPS at r ≈ +0.90 with 96.5 % sign agreement — and never the
        forward axis, which is vibration-inflated (r ≈ +0.36, ~2x RMS: studio/docs/gmeter-
        validation.md). There is deliberately no longitudinal companion to this method; the view
        says why.

        THE RATE IS NOT THE SENSOR'S. ACCL is sampled at 200 Hz, but gmeter.compute boxcars it over
        gmeter.LAT_SMOOTH_S and resamples to gmeter.OUTPUT_HZ before anything downstream sees it, so
        these bands are a distribution of a FILTERED 50 Hz series — still five times the GPS rate,
        and still the reason a g distribution is worth drawing at all, but not 200 Hz and the view
        must not say 200. None without an accelerometer. Cached per width; dropped on re-segment."""
        key = ("lat_g", float(width))
        if key not in self._bands_cache:
            gm = self._gmeter()
            if not getattr(gm, "has_data", False):
                self._bands_cache[key] = None
            else:
                times = np.asarray(gm.times, float)
                lat = np.asarray(gm.lat_g, float)

                def per_lap(i):
                    win = self._lap_window(i)
                    if win is None:
                        return None
                    m = in_windows_mask(times, [win])
                    return lat[m], sample_durations(times[m])
                self._bands_cache[key] = self._band_report(per_lap, float(width),
                                                           centre_on_zero=True)
        return self._bands_cache[key]

    def gg_cloud(self, max_points: int = 4000) -> tuple[np.ndarray, np.ndarray] | None:
        """The friction-circle scatter: (lat_g, long_g) samples restricted to the VALID laps'
        media windows (no pit/out-lap noise), evenly strided down to ≤ `max_points` so the
        view never draws an unbounded cloud. Longitudinal is the validated GPS-derived signal
        when present (the same axis convention as the dial / grip envelope). None when there
        is no g signal or no valid lap. Cached per segmentation."""
        if self._gg_cache is not None:
            return self._gg_cache
        gm = self._gmeter()
        if not getattr(gm, "has_data", False):
            return None
        windows = [w for w in (self._lap_window(i) for i in self._valid_lap_ids())
                   if w is not None]
        if not windows:
            return None
        mask = in_windows_mask(gm.times, windows)
        long_src = gm.long_g_gps if gm.long_g_gps is not None else gm.long_g
        lat = np.asarray(gm.lat_g[mask], float)
        lon = np.asarray(long_src[mask], float)
        if len(lat) == 0:
            return None
        stride = max(1, int(np.ceil(len(lat) / max_points)))
        self._gg_cache = (lat[::stride], lon[::stride])
        return self._gg_cache
