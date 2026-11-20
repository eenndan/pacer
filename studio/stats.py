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
`Session.lap_sector_splits`, and its constants exist to keep a heat grid from marking a lap for
less than GPS noise can move a split. CORNERS BY LAP (`corner_matrix()` ->
`CornerMatrix`) is the same grid over the detected corners with one measured difference: a cell
whose corner window was not matched on track at both edges is never marked and never sets the
typical (CornerMatrix says why)."""

from __future__ import annotations

import datetime
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, overload

import numpy as np

from ._signal import PRINT_DECIMALS, fmt_signed, is_best_at_print, lap_label
from .consistency import sigma
from .lap_race import ESTIMATED_CLOCK_TIE_S
from .library import PB_PRECISION_S, within_timing_precision

if TYPE_CHECKING:  # the injected meter — typed for the checker, not imported at runtime
    from .gmeter import GMeter

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
# THE ODOMETER READS SHORT OF THE DISTANCE DRIVEN, stated in copy and sized here once (TRUTH2-ODO).
# `load._smooth_track` boxcars the positions over `_signal.SMOOTH_WINDOW` fixes (~1.3 s) before the
# core sees them, and a boxcar pulls a corner's fixes toward its inside: the smoothed path is
# rounder and shorter than the kart's. Every distance printed is a chord sum of it — the lap
# odometer (the lap table's Dist; `LapStat.avg_kmh` is it over the lap time) and the SESSION
# `path_distance` — so each reads short, and the average speed low, by the same share. Measured
# 2026-09-30 per valid dropout-free lap against the GPS Doppler ground speed integrated over the
# lap's own window (the boxcar smooths positions, never speeds; the same laps loaded with the
# smoothing off agree with that integral within 0.13 %): 2.63-2.68 % short on the three Sandown
# recordings (~20 m of a ~760 m lap, lap-to-lap sd 0.1-0.3 %; the session distance 2.6-2.7 %) and
# 1.96 % on MK_18_09_26 (~21 m of ~1087 m, sd 0.5 %). No check re-measures those. A GPS dropout
# adds to it: its gap is one straight chord (MK's 15 gaps, 96 s, put its session distance 4.7 %
# short). On the synthetic GoPro's gentler circuit it is ~1.1 %: tests/test_truth_matrix.py rows
# lap.dist, lap.avg and session.distance, which tests/test_stats.py ties the LOW end to. So the
# copy states a range (low = the truth matrix's, high = the working set's). Changing the smoothing
# is TRUTH-8's, not this constant's.
ODOMETER_SHORT_PCT = (1.0, 3.0)   # % of the distance actually driven
# The one sentence every surface that quotes a lap distance, an average speed or the session
# distance carries (tests/test_stats.py scans for them). Plain ASCII with nothing HTML escapes: it
# is also a laps.csv trailer note and a report paragraph.
ODOMETER_NOTE = (
    f"Distance is measured along the GPS trace as smoothed at load, which rounds every corner off, "
    f"so it reads about {ODOMETER_SHORT_PCT[0]:g}-{ODOMETER_SHORT_PCT[1]:g}% short of the distance "
    f"actually driven (more across a GPS dropout, which it bridges with a straight line); an "
    f"average speed, being that distance over the time, reads low by the same share.")
# Sample floor for any statistic that describes a DISTRIBUTION of laps (spread, banked-pace
# count) — the same floor consistency.sigma applies. One lap has no spread and is trivially
# within 1% of itself; printing "+0.00 s" and "1 / 1" dresses that up as a measurement.
MIN_DIST_LAPS = 2
# THE MIDDLE HALF's floor: the fewest times its quartiles are read from. σ counts every clean lap,
# so one slow lap can speak for the session (the shipped demo: σ 1.54 s, middle half spans
# 0.59 s; 13 of its 14 clean laps lie within 44.95-46.21 s and one took 51.13 s). The middle half
# is the WIDTH Q3 − Q1, never "± about the median": the band is not symmetric (the demo's runs
# 0.38 s below its median and 0.21 s above). It is the robust second reading, and only robust once
# ONE outlier cannot reach a quartile: with numpy's linear interpolation Q3 sits at position
# 0.75·(n−1), which touches the slowest time while n < 5. Measured on synthetic sets (laps σ 0.10 s,
# one +10 s spin, 4000 draws):
#     n                        3      4      5      6      8      12
#     |Δ width| median         4.97   2.50   0.034  0.030  0.023  0.014   s
# At n = 5 the width is the spread of three laps (order statistics 2 to 4), and with no outlier its
# relative error is large: σ(width)/mean 0.57 at n = 5, 0.40 at n = 8 (normal laps, 200 000 sets).
# Stability to the printed digit was asked and cannot decide n: under ±0.07 s timing jitter neither
# the width nor σ holds its 0.01 s digit at any n from 3 to 12 (the width moves a digit in 73-89 %
# of draws, σ in 48-80 %).
MIN_MIDDLE_HALF_N = 5
# Below this a LAP-time width is inside the timing precision of a gap between two laps, and it is
# worded with the FLOOR, never the value — library's rule for a sub-floor gap ("< 0.07 s").
MIDDLE_HALF_FLOOR_S = PB_PRECISION_S
# A CORNER width carries no precision claim (matched corner cells are timed far finer than a lap):
# it only stops printing "0.00 s" for a width the 2-dp print cannot show.
CORNER_MIDDLE_HALF_FLOOR_S = 0.01
# THE CORNERS RANKING ("erratic AND slow") is middle half × median loss, not σ × median loss:
# σ is carried by single passes. On the shipped demo one lap (L6, the 51 s lap) holds 91 % of C6's
# variance and 92 % of C5's (σ 0.221 → 0.029 s and 0.757 → 0.060 s without it), and σ × loss
# marked C6 though its middle half, 0.034 s, is the narrowest of all seven corners; the middle half
# marks C1, C5, C7. The middle half (IQR; /1.349 makes it a σ for normal passes, a constant that
# cannot change a ranking, so the score is the width the σ hover prints) was chosen over MAD×1.4826
# on synthetic corners (4000 draws, steady N(0, 0.03 s), erratic N(0, 0.20 s), +1.0 s outliers):
#     one outlier       both robust from n = 5: P(erratic outranks it) 0.98 / 0.97, score 1.4× steady
#     two outliers      MAD holds (0.91 at n = 5); the middle half only from n = 9 (0.43 at n = 5,
#                       0.95 at n = 8): Q3 reaches the second-slowest pass below that
#     40 % of passes +0.4 s (two lines, a genuinely erratic corner, the demo's C1 shape)
#                       middle half 0.99-1.00 at n = 6-14; MAD 0.72 at n = 6, 0.91 at n = 10, as
#                       its median sits inside the larger mode and calls the corner steady
# Below MIN_MIDDLE_HALF_N passes there is no middle half, so the corner scores 0.0 and is never
# marked: falling back to σ there would bring the one-pass mark back where one pass weighs most.
# σ stays on screen as the measurement it is; when it falls by more than this fraction with its
# single worst pass left out of the ESTIMATE, the σ hover names that pass (CornerReport).
ONE_PASS_SIGMA_FRACTION = 0.5
# THE MARK'S FLOOR: no ◆ on a corner whose middle half is under the level step (MIDDLE_HALF_FLOOR_S,
# 0.07 s). The demo's third-ranked corner, C7, had a middle half of 0.052 s, no wider than C3's
# or C4's, and under ±0.035 s cell jitter (1000 draws) the top 3 stayed {C1, C5, C7} in only
# 36.6 % of draws (C7 in 39-42 %, C3 28 %, C4 26 %): a mark chosen by noise, the defect this rule
# exists to stop. With the floor the demo marks C1 and C5 (in 100 % and 85 % of 500 seeded jittered
# draws: tests/test_stats.py), and a near-perfect session marks nothing, where both the σ and the unfloored middle-half
# rules marked three corners of 0.016-0.019 s.
MARK_MIN_MIDDLE_HALF_S = MIDDLE_HALF_FLOOR_S
def corner_spread_clears_level(middle_half_s: float | None) -> bool:
    """True when a corner's middle half is a spread at all: measured (MIN_MIDDLE_HALF_N passes)
    and not under MARK_MIN_MIDDLE_HALF_S, the level step. The ONE test both tabs apply — the ◆
    mark here (`marked_corners`) and whether the Coaching tab may print its consistency reason
    (`coaching.summarize`) — so neither calls a spread inside the noise one the other works on."""
    return middle_half_s is not None and middle_half_s >= MARK_MIN_MIDDLE_HALF_S
# Pace-trend gate: below this many clean laps a fitted slope is noise dressed as insight,
# so the trend statistic reports None and the tile shows a dash.
TREND_MIN_LAPS = 6
# Pace-trend verdict band: a fitted slope within ±this (s/lap) reads "steady" — don't narrate
# noise as a trend. Lives beside the statistic it qualifies rather than in the view, because the
# exported report and the clipboard summary print the same verdict off the same slope and a
# second copy of the band is how the file starts disagreeing with the screen.
TREND_STEADY_BAND = 0.02

#: Two tile captions the Stats page AND the exported summary print (export_data mirrors the page's
#: rows), so one number has one name on both (QA 2026-09-26). The SESSION duration is the kept GPS
#: trace's span, not the footage's — the DATA TRUST card's GPS-quality line spans the footage
#: (LOOK-5). The SPEED · G minimum is `SessionStats.slowest_corner`'s TYPICAL, not the session's
#: one slowest moment (LOOK-12).
DURATION_CAPTION = "GPS trace"
VMIN_CAPTION = "slowest corner · typical"
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
# The laps x sectors grid: the floor under its marks, then its two anchors.
#
# THE FLOOR ON THE TINT SCALE (why a floor at all is under "So:" below). Its VALUE is a design
# floor, not a measured resolution: a lap is marked behind in a sector only when it gave away at
# least 0.30 s there, clear of the GPS noise on one split (a tenth or two at the owner's noise
# levels, tests/test_truth_matrix.py row 2). It once rested on interior splits stepping in whole
# 10 Hz samples; since a boundary is where the lap crosses the line, they do not. The corner grid
# shares it, and there it was checked against the marks it makes (CornerMatrix).
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
# SplitMatrix.is_behind. The comparison that decides a mark has to be made at the same resolution
# the view prints, and only ONE place can own that number: it is `_signal.PRINT_DECIMALS`, shared
# with the Laps tab's split cells and the Corners page's corner times, which put the same ★ on the
# same kind of quantity and used to decide it on the raw doubles instead. Aliased here (not
# retyped) so this module's own name for it can never drift from theirs.
MATRIX_DECIMALS = PRINT_DECIMALS
# THE HEAT SHADE behind CORNERS BY LAP's cells (CornerMatrix.heat): the ▼ rule, graded, not a
# second rule. A cell's gap to its corner's median is read in units of that corner's OWN ▼ step
# (`scales[c]`): the shade is a SHARE OF THAT CORNER'S ▼ STEP, and claims nothing else. Two shaded
# levels: from ¾ of the step, and the whole step — the ▼ itself, so every ▼ cell is the deepest
# shade and nothing short of a ▼ is. Two-sided: on the synthetic demo six laps are a whole step
# QUICKER through C1 (its planted habit's clean laps).
#
# WHAT THE SHADE IS NOT: a mistake detector wherever the step is above the 0.30 s floor. There the
# step is the column's own p90 deviation, so the ▼ marks about one cell in ten BY CONSTRUCTION and
# the shading grades your slowest passes rather than detecting errors. Pure noise (40 seeds × 10
# corners, 40 laps; % of cells shaded slower ≥ ¾ / ▼ / quicker ≥ ¾; 5 and 15 laps read within
# ~1 pt on the slower side):
#
#     Gaussian σ 0.05: 0 / 0 / 0        0.10: 1.5 / 0.1 / 1.7     0.15: 7.4 / 2.3 / 7.5
#              σ 0.20: 12.9 / 6.4 / 13.4                          0.30: 18.3 / 11.1 / 18.3
#     skewed   σ 0.05: 0.9 / 0.3 / 0    0.10: 4.4 / 2.2 / 0       0.30: 15.6 / 10.4 / 0.5
#
# At the floor (σ ≲ 0.10, a steady corner) the ¾ step is 0.22 s, and noise alone shades ≲ 2 % of
# cells per side (4.4 % slower on a right-skewed corner); tests/test_stats.py pins σ 0.05 at ~0.
# A ½ band was measured and dropped: half a p90 step is ~0.64 σ, and it shaded 16-27 % of
# pure-noise cells per side at σ 0.15-0.30.
MATRIX_HEAT_STEPS = (0.75, 1.0)

# --------------------------------------------------------------------- coasting, by place
# WHERE THE COASTING IS: every clean lap's coast seconds split over the corner/straight partition —
# the same pieces the STRAIGHTS and IDEAL LAP tables are cut from — and ranked by seconds per lap.
#
# THE PIECES ARE THE PARTITION, NOT ZONES GROWN FROM THE COAST DATA, and that is measured (F5).
# Zones grown from where the laps coast — a coverage threshold plus a merge gap — crowned a
# different leader as either knob moved: across 5-25 % coverage and 5-20 m gaps, D24 0060's leader
# was a C3 zone, then C1 and C3 fused into one, then C5, and Sandown 09-05's swung between C1 and C4.
# The ranking was a property of the knobs. The partition has no knob the coasting can move.
#
# THE ORDER IS CLAIMED ONLY WHERE THE LAPS SEPARATE IT. Every place is TIED with the leader unless a
# paired sign-flip test over the clean laps (each lap's coast seconds in the leader minus the same
# lap's in that place) puts the difference at p < COAST_LEAD_ALPHA. That is the question #311
# found a ranking can pass while its top position fails, and it does fail here: the leader ties
# with 10 other places on D24 0060 and 7 on 0062, and the two recordings crown different corners
# (C1, C10), while all three Sandown recordings crown C1 clear of every other place.
# studio/dev/probes/p6_coast_places.py prints the tables, the controls and the half-session splits.
#
# SELECTING THE LEADER DOES NOT LOOSEN THE TEST HERE. With each lap's places shuffled among
# themselves, so that no place leads by construction, "the leader separates" fired on 0 of 1,000
# shuffles on every one of the five recordings. That is the opposite of the selection trap #311
# found for "the top is separable from SOME other place", which is not a question asked here: the
# leader has to separate from EVERY other place, the runner-up included.
COAST_LEAD_ALPHA = 0.05
# Sign-flip draws per test. Seeded, so a session renders the same verdict every time it opens; at
# 10,000 draws the p of a borderline place is resolved to about +-0.002.
COAST_SIGNFLIP_DRAWS = 10_000


# --------------------------------------------------------------------- value objects
@dataclass(frozen=True)
class SessionTotals:
    """Whole-recording totals (trace-level — independent of the lap segmentation)."""

    duration_s: float          # recorded span, first→last kept sample (media clock, incl. gaps)
    moving_s: float            # time with speed ≥ MOVING_MS (dropout gaps excluded)
    distance_m: float | None   # speed-gated path length of the smoothed trace (sum of chords);
    #                            None when too little of it survived the gate to mean anything
    distance_kept_frac: float  # share of the raw chord length the gate kept (1.0 = a clean trace)
    start_clock: str | None    # local wall-clock "HH:MM" of the first kept fix; None ONLY when no
    #                            fix carries a wall clock at all (see clock_hhmm) — NOT the GPS5
    #                            era, which stamps every fix from GPSU and renders a real time
    end_clock: str | None      # …and of the last kept fix


@dataclass(frozen=True)
class PathDistance:
    """A gated path length plus how much of the raw chord sum it threw away — so a caller can
    tell "the trace is 2.1 km" from "the trace is mostly GPS glitches" (see CHORD_SPEED_TOL)."""

    metres: float        # the chords that passed the speed gate
    kept_frac: float     # metres / (the un-gated chord sum); 1.0 when nothing was rejected
    rejected_n: int      # how many chords the gate refused


#: The DRIVING braking figure's caption, one string for the Stats tile and the exported DRIVING
#: group. It says what is counted — time ON THE BRAKES (`LapStat.brake_s`) — because the caption it
#: replaces, "braking / lap", sat over the summed event spans: 26.0 s a lap on MK 0067, where
#: 17.8 s was at or past the brake threshold (LOOK-2).
BRAKE_TILE_CAPTION = "on the brakes / lap · median"


@dataclass(frozen=True)
class LapStat:
    """One valid lap's statistics row. None = the underlying signal is absent (no g-meter),
    NOT a zero — the view renders those as a dash, never as 0."""

    idx: int                    # lap id (0-based, same as LapRow["idx"])
    time: float                 # lap time (s)
    vmax_kmh: float | None      # max full_speed on the lap
    avg_kmh: float | None       # odometer / lap time: low by the odometer's ODOMETER_SHORT_PCT
    vmin_kmh: float | None      # min full_speed on the lap — the slowest-corner speed
    peak_lat_g: float | None    # max |lateral g| (IMU lateral — the trusted axis)
    peak_brake_g: float | None  # max deceleration, reported positive (validated GPS-derived long)
    brake_s: float | None       # time ON THE BRAKES: at/past theta_b inside the brake events
    #                             (driving.brake_time) — NOT the events' summed spans, which run
    #                             through each lift-off tail (the block above driving.brake_time)
    brake_n: int | None         # number of brake events (the map's brake glyphs, one per event)
    coast_s: float | None       # total time coasting
    coast_frac: float | None    # coast_s / lap time


@dataclass(frozen=True)
class CornerReport:
    """One corner's whole-session statistics row (the Stats page's CORNERS table): the
    session's demonstrated best/typical/spread through the corner, the apex speeds, and
    the median grip utilization. None = not derivable (no g signal / <2 laps), never 0.

    Every column counts the SAME cells: the laps whose window through this corner was matched on
    track at both edges (see `corner_report`). `n` is how many that is and `n_laps` how many laps
    had a row at all, so `n < n_laps` says some laps' corner was interpolated and left out, and
    `n == 0` is a corner no lap resolved — every value None, never a guess."""

    cid: int                        # Corner.cid (1-based, track order)
    direction: int                  # +1 left / -1 right (Corner.direction)
    n: int                          # counted laps: matched on track here, with a finite time
    best_s: float | None            # session-best time-in-corner
    median_s: float | None          # the typical lap's time-in-corner
    sigma_s: float | None           # sample σ (ddof=1; None with <2 laps)
    median_loss_s: float | None     # median − best (≥ 0): what the typical lap gives away
    apex_best_kmh: float | None     # the fastest apex speed carried through
    apex_median_kmh: float | None
    grip_median: float | None       # median per-lap grip utilization (0..~1.1); None w/o g
    score: float                    # middle_half × median_loss — the erratic-and-slow weight
    #                                 (0.0 when either is missing; see corner_report). NOT
    #                                 consistency.py's σ × median_loss, which coaching reads
    n_laps: int | None = None       # laps with a corner row at all (the denominator of `n`);
    #                                 None only from a caller that never counted them
    middle_half_s: float | None = None  # `middle_half` (width Q3 − Q1) of the counted times; None
    #                                     below MIN_MIDDLE_HALF_N
    # The ONE PASS farthest from the mean — the pass whose omission lowers σ the most — and σ
    # with it left out of the ESTIMATE (never out of the data: every other column counts it).
    # None below three counted passes; the lap is None from a caller that gave no lap ids.
    worst_pass_lap: int | None = None          # 0-based lap id (display through lap_label)
    worst_pass_vs_median_s: float | None = None  # its time − this row's median (signed)
    sigma_without_worst_s: float | None = None

    @property
    def one_pass_sigma(self) -> bool:
        """True when ONE pass carries most of σ: σ falls by more than ONE_PASS_SIGMA_FRACTION
        with that pass left out of the estimate. Only from MIN_MIDDLE_HALF_N counted passes:
        below that, σ over the two or three passes left says little either way."""
        return (self.n >= MIN_MIDDLE_HALF_N
                and self.sigma_s is not None and self.sigma_without_worst_s is not None
                and self.sigma_without_worst_s < ONE_PASS_SIGMA_FRACTION * self.sigma_s)


def marked_corners(report, top_n: int) -> list[CornerReport]:
    """The CORNERS rows that earn the ◆ mark, best score first: at most `top_n` and at most half
    the field (a mark on every row highlights nothing), only a positive `score`, and only a
    corner whose middle half reaches MARK_MIN_MIDDLE_HALF_S (why: the constant's block)."""
    k = min(top_n, max(1, len(report) // 2))
    ranked = sorted(report, key=lambda r: -r.score)[:k]
    return [r for r in ranked if r.score > 0 and corner_spread_clears_level(r.middle_half_s)]


# ---- the CORNERS table's words, shared by the Stats page (stats_corners, a Qt module) and the
# exported report (export_data, Qt-free): one rule, one glyph, one sentence on both.
#: The mark on a marked corner's loss cell (why ◆, not ⚠ or ▲: stats_corners.WORST_LOSS_MARK).
CORNER_MARK = "◆"
#: At most this many corners carry it (`marked_corners`' `top_n`).
CORNER_MARK_N = 3


def corner_mark_rule(*, hover: bool = True) -> str:
    """What the mark means and how it is ranked: the CORNERS tooltip's sentence (`hover`), or the
    report's, where the middle half is a column of its own, nothing can be hovered, and Coaching's
    list is the report's own COACHING section."""
    where = "(the σ cell's hover)" if hover else "(the Middle half column)"
    score = "; hover one for its own score" if hover else ""
    # The page's "this column" is the loss column the tooltip hangs on; under a table it names it.
    largest = ("this column's three largest numbers" if hover
               else "the Med loss column's three largest numbers")
    # The report prints Coaching's list itself, as its COACHING section (the page version, #581).
    coaching_list = "the Coaching tab's list" if hover else "the COACHING section's list"
    return (
        f"Up to {CORNER_MARK_N} loss cells are marked {CORNER_MARK} and tinted: the "
        "corners where your passes vary most and cost most, ranked by the middle half "
        f"{where} × median loss, which is why the marked cells are not "
        f"simply {largest}{score}. The "
        "middle half is used rather than σ so that one unusual pass cannot earn the "
        f"mark. A corner with fewer than {MIN_MIDDLE_HALF_N} counted "
        "passes is never marked, and a corner whose middle half is under "
        f"{MARK_MIN_MIDDLE_HALF_S:.2f} s, the level step, is never "
        f"marked. It is a consistency ranking, not {coaching_list} of what "
        "to work on (time lost against your best lap), so the two need not agree on the "
        "order. They agree on the level step: Coaching never gives a corner under it a "
        "consistency reason.")


@overload
def printed_gap_s(a: float, b: float) -> float: ...
@overload
def printed_gap_s(a: float | None, b: float | None) -> float | None: ...
def printed_gap_s(a: float | None, b: float | None) -> float | None:
    """`a` − `b` AS PRINTED: each side rounded to `MATRIX_DECIMALS` first, then the difference
    (rounded again only to shed float dust); None when either side is missing. The number a reader
    gets by subtracting the two figures the page shows beside it, and the only way a surface that
    prints both sides may print their gap."""
    if a is None or b is None:
        return None
    return round(round(float(a), MATRIX_DECIMALS) - round(float(b), MATRIX_DECIMALS),
                 MATRIX_DECIMALS)


# What the app calls a corner's median pass, wherever a pass is set against it: the slow-lap notes
# and MOMENTS ("over your typical pass"), and the share card's opportunity ("typical pass vs your
# best lap's"). One definition, so the card cannot name the statistic in different words.
TYPICAL_PASS = "typical pass"


@overload
def pass_vs_typical_s(val: float, typical: float) -> float: ...
@overload
def pass_vs_typical_s(val: float | None, typical: float | None) -> float | None: ...
def pass_vs_typical_s(val: float | None, typical: float | None) -> float | None:
    """ONE PASS against its corner's (or sector's) typical pass, the median of the counted passes,
    in seconds, signed (+ = slower): the PRINTED pass minus the PRINTED typical (`printed_gap_s`).
    None when either side is missing.

    The one function that takes this gap, for the ▼ decision (`_behind_at_print`), the heat shade,
    and every surface that prints it: the slow-lap notes, MOMENTS, the σ hover's one-pass sentence
    and the CORNERS BY LAP / SECTORS BY LAP hovers. Before, the slow-lap note rounded only the pass
    and the hovers rounded only the difference: on the demo, lap 6 at C6 (3.6540 against a typical
    2.8289) read "+0.82 s" in the Laps ▼ tooltip and "+0.83 s" on Stats, on one page. The hover
    prints "[▼ 3.65] … against your typical C6 (2.83 s)", so the number a reader can check is
    3.65 − 2.83 = 0.82. Subtracting first and rounding once disagreed with that check in 2,356 of
    9,127 ▼ corners on a 4,000-session hundredth-edge sweep, and printed 5 of them below their own
    ▼ step; this rule does neither (tests/test_story.py)."""
    return printed_gap_s(val, typical)


def typical_pass_lap(lap_ids, times, typical: float | None, exclude: int | None = None
                     ) -> int | None:
    """WHICH LAP'S PASS IS THE TYPICAL ONE: the counted pass whose PRINTED time is nearest the
    printed typical (`pass_vs_typical_s`); among passes level at print, the one raw-nearest the
    typical; then the lowest lap id. Never `exclude` (the best lap, which a typical-vs-best compare
    cannot put on both sides). None with no typical or no other pass. `lap_ids` and `times` are
    parallel and already the counted cells.

    NEAREST, NOT EQUAL. A median over an even count is the mean of its two middle passes and no
    lap need print it: the demo's C1 is 14 passes whose middle two print 3.37 and 3.39 around a
    typical of 3.38, equally far from it raw too, so the tie goes to the lower lap. With an odd
    count the median IS a pass, and the raw distance (0 for it) is what keeps a lower-numbered lap
    printing the same hundredth from taking its place: the PR #601 review found that on 20 of 43
    odd-count rows of synthetic seeds 1-8. The raw distance is compared to 1e-9 s, so the two
    middle passes of an even count tie exactly rather than by a last-bit float difference."""
    if typical is None:
        return None
    pairs = [(abs(pass_vs_typical_s(float(t), typical)), round(abs(float(t) - typical), 9),
              int(i))
             for i, t in zip(lap_ids, times, strict=True)
             if int(i) != exclude and t is not None and np.isfinite(t)]
    return min(pairs)[2] if pairs else None


@dataclass(frozen=True)
class TypicalPass:
    """One corner's typical pass beside the best lap's, for a compare at that corner
    (`CornerMatrix.typical_pass`). Times in seconds, raw; print them with `MATRIX_DECIMALS`.

    It carries no loss figure on purpose. The Coaching row's Time lost is the median of each pass
    less the best lap's, rounded once (`coaching.lost_text`); the printed typical less the printed
    best pass disagreed with it by 0.01 s on 29 of 88 ranked rows of synthetic seeds 1-8 (PR #601
    review), so a surface beside the row prints the row's own figure."""

    cid: int
    lap_id: int          # the lap whose pass is the typical one (`typical_pass_lap`)
    pass_s: float        # that lap's time through the corner
    typical_s: float     # the corner's typical pass: the median of the counted passes
    n: int               # how many passes that median is over
    best_lap_id: int
    best_pass_s: float   # the best lap's time through the same corner


@dataclass(frozen=True)
class PassVsTypical:
    """ONE pass beside its corner's typical pass, for "compare this pass with my typical pass"
    (`CornerMatrix.pass_vs_typical`). The typical pass is Coaching's (`typical_pass_lap`, the same
    `CornerMatrix.typical_pass`), only with THIS lap on the other side instead of the best lap.
    `gap_s` is the printed typical minus the printed pass (+ = this pass quicker), the number a
    reader gets from the two times the status line prints; `level` is that gap under the timing
    step (`is_level`), where the compare is not offered. `is_typical`: this pass IS the corner's
    typical pass, nearer the median than any other."""

    cid: int
    lap_id: int
    pass_s: float
    typical_lap_id: int
    typical_pass_s: float
    gap_s: float
    level: bool
    is_typical: bool
    level_s: float


def pass_vs_typical_of(key: int, lap: int, counted: dict[int, float], typical: float | None,
                       level_s: float) -> PassVsTypical | None:
    """THE ONE RULE for a pass beside its stretch's typical pass, a corner's or a sector's
    (`CornerMatrix.pass_vs_typical`, `SplitMatrix.pass_vs_typical`): the other side is
    `typical_pass_lap` over the `counted` passes (lap → time) with `lap` excluded, `level` is their
    printed gap under `level_s`, and `is_typical` asks the same with nothing excluded. None where
    `lap` is not counted, there is no typical, or no other pass."""
    lap = int(lap)
    other = typical_pass_lap(list(counted), list(counted.values()), typical, exclude=lap)
    if typical is None or lap not in counted or other is None:
        return None
    nearest = typical_pass_lap(list(counted), list(counted.values()), typical)
    gap = printed_gap_s(counted[other], counted[lap])
    return PassVsTypical(cid=int(key), lap_id=lap, pass_s=counted[lap], typical_lap_id=other,
                         typical_pass_s=counted[other], gap_s=gap, level=is_level(gap, level_s),
                         is_typical=nearest == lap, level_s=level_s)



def fmt_pass_vs_typical(gap: float) -> str:
    """`pass_vs_typical_s`'s printed form, "+0.82 s" — the one formatter every surface that prints
    a pass's time against its corner's typical pass goes through."""
    return fmt_signed(gap, MATRIX_DECIMALS, unit="s")


def printed_med_loss(row) -> float | None:
    """A CORNERS row's Med loss as its cell prints it: the printed Median minus the printed Best,
    the two cells beside it (`printed_gap_s`). The demo's C4 printed +0.07 beside 3.08 and 3.02.
    Only the printed figure: the ◆ ranks on the unrounded `score`, which this does not touch.

    None (the dash) under two counted passes, σ's own floor (`consistency.sigma`): one pass is both
    the corner's Best and its Median, so its "+0.00" was a loss of 0 by construction — and a
    one-lap session summed seven of them into "Summed, that is 0.00 s."."""
    n = getattr(row, "n", None)
    if n is not None and n < 2:
        return None
    return printed_gap_s(getattr(row, "median_s", None), getattr(row, "best_s", None))


def _worst_pass_gap(report) -> float:
    """The one-pass sentence's gap: the printed pass minus the printed median (the CORNERS row's
    Median cell), as every other surface prints this pass. The pass's time is taken back from the
    row's own median + gap rather than stored as one more field (a field is a golden leaf): the
    round trip moved no printed pass on 160,000 hundredth-edge cells."""
    med, gap = getattr(report, "median_s", None), report.worst_pass_vs_median_s
    if med is None:
        return float(gap)
    return pass_vs_typical_s(float(med) + float(gap), float(med))


def corner_one_pass_note(report) -> str:
    """"" unless ONE pass carries most of a corner's σ (`CornerReport.one_pass_sigma`), which is
    why such a corner is not marked on σ's word; else the sentence naming that pass. The pass
    stays in every number on the row; only the sentence's σ leaves it out."""
    if not getattr(report, "one_pass_sigma", False):
        return ""
    lap = getattr(report, "worst_pass_lap", None)
    name = "" if lap is None else f"L{lap_label(lap)}: "
    return (f"One pass carries most of this σ ({name}"
            f"{fmt_pass_vs_typical(_worst_pass_gap(report))} against the median); "
            f"without it σ would be {report.sigma_without_worst_s:.2f} s.")


def corner_count_note(report, *, page: bool = True) -> list[str]:
    """The CORNERS note's opening sentences: what Med loss is measured against (its sum), and,
    when not every lap counted, how many lap × corner times did and why the rest did not. `page`
    False is the report's wording: it has no CORNERS BY LAP grid to mute them in, and names them
    in its laps table's Interpolated column instead (export_data.REPORT_INTERPOLATED_HEAD)."""
    shown = ("shown muted lap by lap" if page
             else "named lap by lap in the laps table's Interpolated column")
    # The sum of the Med loss cells AS PRINTED (`printed_med_loss`), so it adds up on the page —
    # and no sum at all when every cell is a dash: a total of nothing is not "0.00 s".
    printed = [g for g in map(printed_med_loss, report) if g is not None]
    summed = f" Summed, that is {sum(printed):.2f} s." if printed else ""
    parts = [f"Med loss is measured against each corner's own Best above — the quickest "
             f"anyone went through it, which is usually not your best lap's corner.{summed}"]
    # C4: which cells this table counted, when that is not all of them — the one thing that
    # makes its numbers differ from a surface that counts every lap (corner_report).
    counted = [(r.n, r.n_laps) for r in report if getattr(r, "n_laps", None) is not None]
    left_out = sum(of - n for n, of in counted)
    if left_out:
        was, are = ("was", "is") if left_out == 1 else ("were", "are")
        parts.append(
            f"Only corners matched on track count: {sum(n for n, _ in counted)} of "
            f"{sum(of for _, of in counted)} lap × corner times here; the other {left_out} "
            f"{was} interpolated between matched points, and {are} {shown}.")
        untimed = [f"C{r.cid}" for r in report if getattr(r, "n_laps", None) and r.n == 0]
        if untimed:
            parts.append(f"No lap matched {', '.join(untimed)} on track, so "
                         f"{'it has' if len(untimed) == 1 else 'they have'} no times.")
    return parts


def corner_no_mark_note(report) -> str:
    """A short session where NO corner has the passes a middle half needs says why there is no
    mark at all, rather than leave a reader to infer that every corner is steady; else ""."""
    if all(getattr(r, "middle_half_s", None) is None for r in report):
        return f"No {CORNER_MARK} under {MIN_MIDDLE_HALF_N} counted passes per corner."
    return ""


# ---- the PACE σ tile's words (stats_panel), shared with the exported report.
SIGMA_CAPTION = "σ lap"
SIGMA_TOOLTIP = (
    "σ counts every clean lap, a spin included, so one bad lap can carry it; the middle half "
    "spans the central 50 % of your clean laps, slowest of them minus fastest (their "
    "interquartile range). Neither removes a lap. "
    f"The middle half is shown from {MIN_MIDDLE_HALF_N} clean laps; under "
    f"{MIDDLE_HALF_FLOOR_S:.2f} s it is inside the timing precision of a gap "
    f"between two laps and reads < {MIDDLE_HALF_FLOOR_S:.2f} s.")


def sigma_caption(middle_half: float | None) -> str:
    """The σ tile's caption: "σ lap", plus the middle half's span when there are laps enough."""
    shown = fmt_middle_half(middle_half)
    return SIGMA_CAPTION if shown is None else f"{SIGMA_CAPTION} · middle half spans {shown}"


# ---- the PACE "best rolling" tile's caption (stats_panel), shared with the exported report.
#
# A rolling best is never slower than the best lap (a complete lap is one of its windows), and it
# printed a few ms under it with no note: the demo's 44.945 beside a best lap of 44.950, 0.006 s
# apart, read as a faster loop the timing cannot see. Under the timing step the two are level, and
# the caption says "level", the word the Laps tab and the lap strip use for a tie; the tooltip
# carries their whole sentence. ONE WORD ON PURPOSE: "· level with best lap" widened the grid's
# first column and, measured at 1280 px in the quadrant, put the page into sideways scroll (the
# race-pace caption and DATA TRUST clipped); the tile sits beside "best lap". The step is `library.PB_PRECISION_S`, or under an estimated clock that clock's published
# bound (`lap_race.ESTIMATED_CLOCK_TIE_S`), the pair `story.tie_floor` reads. Words only: both
# numbers still print. Measured on 8 synthetic seeds × 6/10/20 laps: 16 of 24 sessions level
# (0.0006-0.069 s), 8 apart (0.073-0.222 s).
ROLLING_CAPTION = "best rolling"


def rolling_level_floor(clock_estimated: bool) -> float:
    """The step under which the rolling best and the best lap read level."""
    return ESTIMATED_CLOCK_TIE_S if clock_estimated else PB_PRECISION_S


def is_level(gap_s: float, floor: float) -> bool:
    """Whether two figures `gap_s` apart are LEVEL under the step `floor`: strictly closer than it,
    the gap read to the millisecond. At `library.PB_PRECISION_S` that is
    `within_timing_precision` (exactly 0.070 apart is NOT level, 0.069 is); under an estimated
    clock's wider step the same strict rule on the gap rounded to 1 ms. The one rule the session
    moments (`story`) and the Corners tab's Quickest passes name a tie by."""
    return (within_timing_precision(gap_s) if floor == PB_PRECISION_S
            else abs(round(gap_s, 3)) < floor)


def rolling_caption(rolling: float | None, best: float | None, clock_estimated: bool = False
                    ) -> str:
    """"best rolling", or "best rolling · level" when both exist and sit closer than
    `rolling_level_floor`."""
    if rolling is None or best is None or not (math.isfinite(rolling) and math.isfinite(best)):
        return ROLLING_CAPTION   # a non-finite figure is level with nothing (abs(nan) >= x is False)
    if abs(best - rolling) >= rolling_level_floor(clock_estimated):
        return ROLLING_CAPTION
    return f"{ROLLING_CAPTION} · level"


def rolling_level_note(rolling: float | None, best: float | None,
                       clock_estimated: bool = False) -> str:
    """The tooltip sentence behind the level caption ("" when the caption has none): the floor, not
    the gap, the way the PB sentences word a level pair (`library._precision_clause`)."""
    if rolling_caption(rolling, best, clock_estimated) == ROLLING_CAPTION:
        return ""
    return (f"Within timing precision (under {rolling_level_floor(clock_estimated):.2f} s apart): "
            "level with the best lap as far as the timing can tell.")


@dataclass(frozen=True)
class BrakeConsistency:
    """One corner's braking repeatability + commitment over the included laps (the BRAKING
    table). Onsets are compared in the REFERENCE odometer (each lap's onset scaled by
    ref_total/lap_total — the house normalized projection), so cross-lap σ measures the
    DRIVER's scatter, not lap-length drift. Honesty floor: an onset lands on a 10 Hz fix, so it
    is quantized by one fix of travel (`driving.ONSET_QUANT_M`, derived there), and a σ at or
    below that is measurement quantization, not driving."""

    cid: int                        # Corner.cid (1-based, track order)
    n: int                          # laps with a matched brake event into this corner
    median_dist_m: float | None     # median onset (reference odometer)
    sigma_m: float | None           # cross-lap σ of the onset (m); None with <2 laps
    span_m: float | None            # max − min onset spread (m)
    commit_pct: float | None        # median (event peak decel / session a_max) × 100
    metres_later_med: float | None  # median (model optimum − actual onset), m: the D4 model's
    #                                 BOUND — past the onset by construction, never room to gain
    #                                 (refused-2026-09.md §16); BRAKING's "Bound m (est)" column.


@dataclass(frozen=True)
class StraightStat:
    """One straight's session stats (the STRAIGHTS table). Straight k runs corner k's exit →
    corner k+1's entry; k=0 is start line → C1 and k=N is C_N → start line — on a circuit
    those two are ONE physical straight split by the timing line. Trap speed = the speed at
    the straight's END (the next corner's entry; the finish-line speed for the last).
    exit_delta_kmh is the PRECEDING corner's median exit speed vs the best lap's (+ = the
    field exits faster than best); leverage = how much a slow exit costs down THIS straight
    (positive deficit × positive time spread) — 'fix the corner before the long straight
    first', measured not modeled.

    C4: each column counts the laps whose edges IT reads were matched on track — the time both of
    the straight's ends, the trap speed its end, the exit Δ the preceding corner's exit (see
    `straights_report`). So the three counts can differ; `n_laps` is the denominator of all three,
    and the timing line itself is always a matched end."""

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
    n_laps: int | None = None       # laps with a row (None only from a caller that never counted)
    n_trap: int | None = None       # laps whose trap speed counted (the straight's END matched)
    n_exit: int | None = None       # laps whose preceding exit speed counted (None for k=0)


@dataclass(frozen=True)
class CoastPlace:
    """One piece of the corner/straight partition in the COASTING table: how much of the session's
    coasting happens there. Seconds are read off each lap's own clock inside the piece's projected
    edges, so a span that runs from one piece into the next is split between them, not counted
    twice."""

    index: int          # 0-based position in the partition: 0 = S/F → C1, 1 = C1, 2 = C1 → C2 …
    label: str          # "C3" for a corner; a straight takes the STRAIGHTS table's own label
    ring_cid: int       # the corner the map rings for this row (a straight: the corner feeding it)
    s_per_lap: float    # coasting seconds inside the piece over all clean laps, / the lap count
    laps: int           # clean laps that coasted inside the piece at all
    share: float        # s_per_lap as a fraction of all the coasting per lap (0..1)
    tied: bool          # the leader's own row, and every row the laps cannot separate from it


@dataclass(frozen=True)
class CoastReport:
    """The session's coasting by place (see COAST_LEAD_ALPHA for what `tied` claims)."""

    n_laps: int                 # clean laps the report reads
    per_lap_s: float            # all coasting per clean lap — the MEAN, which s_per_lap sums to
    places: list[CoastPlace]    # most coasting first; a piece no clean lap coasted in is left out
    lead_separable: bool        # True when the leader separates from every other place


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
    middle_half: float | None = None  # `middle_half` (width Q3 − Q1); None below MIN_MIDDLE_HALF_N


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
    start_s: float               # telemetry time the first lap started
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
    robust percentile of the column's own upper spread, floored at MATRIX_SCALE_MIN_S. See
    MATRIX_SCALE_MIN_S for that floor, and why the mark is anchored on the median while the ★ is
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
    # rendering the grid — while interior splits still stepped on a ~0.0998 s grid (a boundary was
    # then the nearest fix), which made them commoner; a print tie needs no grid:
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
        return _behind_at_print(val, med, self.scales[col])

    def is_best(self, row: int, col: int) -> bool:
        """Is this cell the sector's best — or timed level with it? `best_lap[col]` stays the one
        lap that owns the minimum (it is what a tooltip names); this is what gets the ★.

        The rule itself is `_signal.is_best_at_print`, shared with every other surface that marks
        a session best — the Laps tab's split cells and the Corners page's corner times both wear
        this same ★ and used to decide it on the raw doubles (see that helper for what the two
        rules cost on the owner's own recordings)."""
        return is_best_at_print(self.cells[row][col], self.bests[col], MATRIX_DECIMALS)

    def pass_vs_typical(self, col: int, lap: int, level_s: float) -> PassVsTypical | None:
        """Lap `lap`'s split through sector column `col` beside the sector's typical pass, by the
        corners' rule (`pass_vs_typical_of`) over this column's counted cells and its median; the
        result is keyed by the sector's stretch key (`sector_key`)."""
        if not 0 <= col < self.columns:
            return None
        counted = {int(lid): float(val) for lid, row in zip(self.lap_ids, self.cells, strict=True)
                   if (val := row[col]) is not None}
        return pass_vs_typical_of(sector_key(col), lap, counted, self.medians[col], level_s)


@dataclass(frozen=True)
class CornerMatrix:
    """The laps × corners grid behind the Stats page's CORNERS BY LAP table: every clean lap's
    time through every corner, with the corners where a lap lost notably more than usual marked.

    WHY IT EXISTS. None of the owner's recordings carries a sector line (see stats_panel's
    SECTORS_EMPTY), so SPLITS has never been visible on any of them, and every other per-corner surface is either ONE lap (the Corners page)
    or a summary over all of them (CORNERS, BRAKING, Coaching). "Which laps lost time at C4?" had
    no answer short of clicking through 38 or 65 laps.

    THE MARK IS THE SPLIT GRID'S RULE, NOT A NEW ONE: the column's MEDIAN is the anchor and a
    cell is ▼ at `_typical_and_scale`'s threshold, floored at MATRIX_SCALE_MIN_S. That floor is a
    design value, so it was measured here as what it does to the marks. The check is independent
    of the time itself: a lap that really lost time in a corner should usually carry a lower
    minimum speed through it, and the minimum speed is read off the Doppler channel, not off the
    window edges.

    ⚠ STALE — NOT RE-MEASURABLE (T16). Both tables in this docstring were measured on D24 before
    #335 changed corner matching, which moved which cells are resolved: 220 → 422 of 456 on the
    0060 pair and 776 → 780 of 780 on 0062 (#335's own count). So their counts no longer describe
    the app, and their other cells were never re-measured. D24 is no longer available, so neither
    table can be. They are the record of that measurement, not what the app computes today. The
    first is what checked MATRIX_SCALE_MIN_S's 0.30 on corner times; that check is carried forward
    unverified (the constant's own reason, a design floor above one split's noise, does not
    depend on it).

    Resolved cells only, both D24 recordings, floor → marks · share with a below-typical minimum:

        floor           0.10    0.15    0.20    0.25    0.30    0.40
        0060 (38 laps)  30·77%  30·77%  26·73%  23·74%  18·83%  11·82%
        0062 (65 laps)  92·88%  78·88%  62·89%  46·91%  35·91%  21·90%

    Shuffling the minimum speeds within each corner gives 51 % and 49 % for the 0.30 row (p95 12 of
    18, 22 of 35). Every floor beats that; 0.30 is kept because it is the highest-agreement row on
    both recordings AND the one that makes a ▼ read the same on both grids of the page.

    WHY `resolved`. A lap's corner window is projected by ONE warp per lap
    (`corners.project_boundaries`) whose knots are the boundaries that matched the reference lap
    on track; a boundary that did not match is INTERPOLATED between its neighbours. Scored against
    an independent time — the moment the lap crosses a gate drawn across the track at each
    boundary — the two classes are different instruments:

        cells with both edges matched    0060: 220 of 456   |Δ| median 0.004 s, p90 0.014, max 0.024
                                         0062: 776 of 780   |Δ| median 0.001 s, p90 0.006, max 0.021
        cells with an interpolated edge  0060: 236 of 456   |Δ| median 0.219 s, p90 0.579, max 0.886
                                         0062:   4 of 780   |Δ| max 0.082 s (3 scorable)

    Before #335, C4 re-measured the same 236 cells for `corner_report` against a line crossing it
    built itself, and read a median 0.221 s, max 0.960 s. That is the same statistic over the same
    cells, with two separately built crossing references. The medians agree to 2 ms. The maxima
    differ by 0.074 s, and nothing on record says which reference is the better one, so a surface
    quoting a maximum says which measurement it is: 0.89 s is this gate's, 0.96 s is C4's line.
    Both are from before #335.

    On 0060 the interpolated cells' error is as large as the ▼ itself: of the 10 marks the plain
    rule put on them that the gate could score, it agreed with 3; of the 20 it put on resolved
    cells, with all 20. They also moved the median the ▼ is measured against, by up to 0.354 s
    (C7). So an unresolved cell is SHOWN — it is the same number the Corners page prints for that
    lap — but it is muted, never marked, and left out of the typical and the scale. On 0062 this
    changes nothing (35 marks either way, all 27 the gate can score confirmed).

    NO ★. The quickest time through a corner is the CORNERS table's Best. When this grid shipped
    that Best counted every cell and sat on an unresolved one on 0060 in C2, C6 and C8, where the
    gate time is 0.53, 0.41 and 0.29 s slower; since C4 it counts exactly the resolved cells this
    grid's typical does (`corner_report`), so its Median is `medians[c]` wherever that exists (the
    one exception: a lap with a non-finite corner time is a blank row here and keeps its other
    cells there — no recording has produced one). A
    grid whose purpose is "which lap lost time here" does not need a second rule for "quickest".

    `cells[r][c]` is lap `lap_ids[r]`'s time through corner `cids[c]`, or None where the lap
    projected no row. `medians` / `scales` are per column over the RESOLVED cells, None where
    fewer than MATRIX_MIN_LAPS of them exist; `n_resolved[c]` is that count."""

    lap_ids: list[int]
    cids: list[int]
    cells: list[list[float | None]]
    resolved: list[list[bool]]
    medians: list[float | None]
    scales: list[float | None]
    n_resolved: list[int]

    def is_behind(self, row: int, col: int) -> bool:
        """Is this RESOLVED cell notably slower than its corner's typical lap? Compared at print
        resolution, exactly as SplitMatrix.is_behind."""
        val = self.cells[row][col]
        med = self.medians[col]
        scale = self.scales[col]
        if val is None or med is None or scale is None or not self.resolved[row][col]:
            return False
        return _behind_at_print(val, med, scale)

    def heat(self, row: int, col: int) -> int:
        """The cell's heat shade, −2 … +2: how many of MATRIX_HEAT_STEPS (fractions of the column's
        ▼ step) its gap to the column's median reaches — positive slower, negative quicker, 0 short
        of the first. 0 wherever `is_behind` refuses (muted, blank, no typical). Compared at print
        resolution exactly as `is_behind`, so +2 IS `is_behind`: one rule, never two that can
        disagree at the edge."""
        val = self.cells[row][col]
        med = self.medians[col]
        scale = self.scales[col]
        if val is None or med is None or scale is None or not self.resolved[row][col]:
            return 0
        gap = pass_vs_typical_s(val, med)
        level = sum(1 for f in MATRIX_HEAT_STEPS
                    if abs(gap) >= round(f * scale, MATRIX_DECIMALS))
        return level if gap > 0 else -level

    def typical_pass(self, cid: int, best_lap: int | None) -> TypicalPass | None:
        """Corner `cid`'s typical pass (`typical_pass_lap` over this column's RESOLVED cells, the
        cells its median is taken over) beside `best_lap`'s pass. None where the column has no
        typical (under MATRIX_MIN_LAPS resolved passes), the best lap has no resolved cell here,
        or no other lap has one."""
        if best_lap is None or int(cid) not in self.cids:
            return None
        col = self.cids.index(int(cid))
        med = self.medians[col]
        counted: dict[int, float] = {}
        for lid, row, ok in zip(self.lap_ids, self.cells, self.resolved, strict=True):
            val = row[col]
            if val is not None and ok[col]:
                counted[int(lid)] = float(val)
        lap = typical_pass_lap(list(counted), list(counted.values()), med, exclude=best_lap)
        if med is None or best_lap not in counted or lap is None:
            return None
        return TypicalPass(cid=int(cid), lap_id=lap, pass_s=counted[lap], typical_s=float(med),
                           n=len(counted), best_lap_id=int(best_lap),
                           best_pass_s=counted[best_lap])

    def pass_vs_typical(self, cid: int, lap: int, level_s: float) -> PassVsTypical | None:
        """Lap `lap`'s pass through corner `cid` beside the corner's typical pass — `typical_pass`
        with `lap` where the best lap stands (so never `lap` itself on the other side). None where
        `lap`'s cell is not counted (no time, or not matched on track) or the corner has no typical.
        `is_typical` asks the same `typical_pass_lap` with nothing excluded (`pass_vs_typical_of`,
        the rule a sector's shares)."""
        if int(cid) not in self.cids:
            return None
        col = self.cids.index(int(cid))
        counted = {int(lid): float(val) for lid, row, ok
                   in zip(self.lap_ids, self.cells, self.resolved, strict=True)
                   if (val := row[col]) is not None and ok[col]}
        return pass_vs_typical_of(cid, lap, counted, self.medians[col], level_s)


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
    straight-line chord (an under-count of the real path, never an over-count, and not always a
    slight one: MK_18_09_26's 15 gaps bridge 867 m where the speed at their ends puts ~1470 m — see
    ODOMETER_SHORT_PCT, which also states the smoothing's own shortfall).

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
    """LOCAL wall-clock "HH:MM" for an epoch-ms timestamp, or None for the no-wall-clock
    sentinel (<= 0 — the same one session_date() checks). Local for the same reason as
    session_date: the time of day the driver actually experienced.

    THE SENTINEL IS NOT THE GPS5 ERA. This used to say "GPS5 reports 0"; measured over the ten
    bundled clips, all nine GPS5-era ones stamp every fix from the payload's GPSU and render a
    real time here (hero6 reads 19:28). Only a recording with no wall clock on its fixes at all
    (`karma.mp4`, no GPS stream) reaches the sentinel."""
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
                     spread=(med - best) if len(a) >= MIN_DIST_LAPS else None,
                     middle_half=middle_half(a))


def middle_half(values) -> float | None:
    """The MIDDLE HALF: the width Q3 − Q1 of the band the central 50 % of `values` fall in. A
    width, not a ± about the median — the median need not sit in the band's middle. Quartiles are
    numpy's default `np.percentile` method, linear interpolation between order statistics.
    Non-finite values are dropped; None with fewer than MIN_MIDDLE_HALF_N left (why five: the
    constant's block). A second description of spread beside σ, never a replacement: no value is
    removed or down-weighted, the quartiles simply do not reach the outer laps."""
    a = np.asarray(list(values), float)
    a = a[np.isfinite(a)]
    if len(a) < MIN_MIDDLE_HALF_N:
        return None
    q1, q3 = np.percentile(a, [25, 75])
    return float(q3 - q1)


def fmt_middle_half(width: float | None, floor: float = MIDDLE_HALF_FLOOR_S) -> str | None:
    """The middle half's width as printed: `0.59 s`, or `< 0.07 s` below `floor` (the floor, not
    the value). Laps take MIDDLE_HALF_FLOOR_S, corners CORNER_MIDDLE_HALF_FLOOR_S. None passes
    through. A width under the level step (MARK_MIN_MIDDLE_HALF_S) that two decimals would round up
    TO it prints a third decimal, `0.066 s`, so no surface says "0.07 s, under 0.07 s"."""
    if width is None:
        return None
    if width < floor:
        return f"< {floor:.2f} s"
    if width < MARK_MIN_MIDDLE_HALF_S <= round(width, 2):
        return f"{width:.3f} s"
    return f"{width:.2f} s"


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
                  grip_by_lap, resolved_by_lap=None, lap_ids=None) -> list[CornerReport]:
    """The corner-by-corner session report: one CornerReport per corner (track order).

    Each *_by_lap input is one row per included lap, aligned to `cids` (ragged rows are
    tolerated — column k reads only rows long enough). σ via consistency.sigma (ddof=1);
    score = middle_half × median_loss, the both-erratic-AND-slow product (why the product:
    studio/consistency.py; why the middle half and not σ: ONE_PASS_SIGMA_FRACTION's block) —
    0.0 when either input is missing, so a corner under MIN_MIDDLE_HALF_N passes is never
    ranked. `lap_ids`, one per row, names the worst pass (None → the lap is not named).

    WHICH CELLS COUNT. `resolved_by_lap` is `CornerModel.lap_corner_resolved` for each row (the
    rows of all four inputs are then the same laps, in the same order): a lap's corner whose
    window was not matched on track at both edges counts towards NOTHING in that corner's row —
    not its time, not its apex speed, not its grip. It is the rule the CORNERS BY LAP grid
    (`corner_matrix`) marks by, re-measured for this table on the owner's D24 recordings against an
    independent time, the moment each lap crosses a line drawn across the track at each edge.

    ⚠ STALE — NOT RE-MEASURABLE (T16). Measured on D24 before #335 changed corner matching, which
    left 34 of the 0060 pair's 456 cells interpolated rather than 236 (#339's count), so the
    split below no longer describes the app. D24 is no longer available, so the table cannot be
    re-measured. Before #335 `CornerMatrix` read the same 236 cells against a gate of its own,
    median 0.219 s, max 0.886 s; a maximum quoted anywhere says which of the two it is.

        0060 pair (38 laps)   matched 220 of 456 cells   |Δt| median 0.004 s, max 0.022 s
                              interpolated 236           |Δt| median 0.221 s, max 0.960 s
                                                         apex |Δ| p90 0.53 km/h, max 8.3 km/h
        0062 (65 laps)        matched 776 of 780         |Δt| median 0.001 s, max 0.021 s
                              (a matched cell's apex speed: 0.0 km/h off on both recordings)

    Counting every cell, this table's Best on 0060 sat on an interpolated cell in C2, C6 and C8,
    whose crossing times were 0.53, 0.41 and 0.29 s slower than the time printed; the Best moves by
    up to 0.25 s (C6) and the Median by up to 0.35 s (C7) when only matched cells count. 0062 moves
    by at most 0.004 s. A corner with NO matched cell keeps its row with every value None: a guess
    between neighbours is not a measurement, and dropping the row would hide the corner instead of
    saying it could not be timed. None → every cell counts (a pure caller with no warp)."""

    def counted(rows):
        if resolved_by_lap is None:
            return rows
        return [[v if k < len(ok) and ok[k] else np.nan for k, v in enumerate(r)]
                for r, ok in zip(rows, resolved_by_lap, strict=True)]

    def column(rows, k):
        vals = np.asarray([r[k] for r in rows if k < len(r)], float)
        return vals[np.isfinite(vals)]

    times_by_lap, apex_by_lap = counted(times_by_lap), counted(apex_by_lap)
    grip_by_lap = counted(grip_by_lap)
    n_laps = len(times_by_lap)
    ids = list(lap_ids) if lap_ids is not None else [None] * n_laps
    out: list[CornerReport] = []
    for k, (cid, direction) in enumerate(zip(cids, directions, strict=True)):
        times = column(times_by_lap, k)
        # the lap id of each counted time, in the same order (`column` keeps row order)
        owners = [i for r, i in zip(times_by_lap, ids, strict=True)
                  if k < len(r) and np.isfinite(r[k])]
        apex = column(apex_by_lap, k)
        grip = column(grip_by_lap, k)
        n = len(times)
        best = float(np.min(times)) if n else None
        med = float(np.median(times)) if n else None
        sig = sigma(times)
        loss = med - best if med is not None and best is not None else None  # both iff n
        half = middle_half(times)
        worst = int(np.argmax(np.abs(times - times.mean()))) if n >= 3 else None
        owner = owners[worst] if worst is not None else None
        out.append(CornerReport(
            cid=int(cid), direction=int(direction), n=n, n_laps=n_laps,
            best_s=best, median_s=med, sigma_s=sig, median_loss_s=loss,
            apex_best_kmh=float(np.max(apex)) if len(apex) else None,
            apex_median_kmh=float(np.median(apex)) if len(apex) else None,
            grip_median=float(np.median(grip)) if len(grip) else None,
            score=(half * loss) if half is not None and loss is not None else 0.0,
            middle_half_s=half,
            worst_pass_lap=None if owner is None else int(owner),
            worst_pass_vs_median_s=(None if worst is None or med is None
                                    else float(times[worst]) - med),
            sigma_without_worst_s=(None if worst is None
                                   else sigma(np.delete(times, worst))),
        ))
    return out


def brake_consistency(cids, rows_by_lap) -> list[BrakeConsistency]:
    """Aggregate per-lap braking rows into per-corner repeatability + commitment stats.

    `rows_by_lap`: one dict per included lap, cid → (onset_ref_m, commit_frac | None,
    metres_later | None) — a corner absent from a lap's dict simply had no matched brake event
    there (an unbraked or undetected pass; it lowers n, it does not fake a 0). These are
    `Session._brake_rows`, the one list every braking surface reads; the median `metres_later` is
    the model's bound the BRAKING table prints as "Bound m (est)", never room to gain."""
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


def straight_label(cids, k: int) -> tuple[str, int]:
    """(label, ring_cid) of straight `k` of the corner/straight partition (N corners → N+1
    straights, k=0 from the timing line into C1). The ring is the corner FEEDING the straight, and
    straight 0's is C_N across the timing line — one physical straight split by the line. One
    definition, so the STRAIGHTS and COASTING tables name and ring a straight the same way."""
    n_corners = len(cids)
    if k == 0:
        return (f"S/F → C{cids[0]}" if n_corners else "S/F",
                int(cids[-1]) if n_corners else 0)  # the wrap: C_N feeds the S/F straight
    if k == n_corners:
        return f"C{cids[-1]} → S/F", int(cids[-1])
    return f"C{cids[k - 1]} → C{cids[k]}", int(cids[k - 1])


def coast_seconds_by_piece(spans, edges, dist, elapsed) -> np.ndarray:
    """Seconds of ONE lap's coasting inside each piece [edges[j], edges[j+1]] of the partition.

    `spans` are that lap's `driving.CoastSpan`s; `edges` the partition edges on the SAME lap's
    odometer (timing line, each corner's projected enter/exit, lap end — what
    `corners.segment_times` cuts at); `dist`/`elapsed` the lap's own arrays the spans were detected
    on. Each span is clipped to each piece and the clipped stretch is read off the lap's own clock,
    so a coast running across a corner's edge is split at the edge rather than counted in full on
    both sides, and a lap's pieces add up to its coasting exactly."""
    dist = np.asarray(dist, float)
    elapsed = np.asarray(elapsed, float)
    edges = np.asarray(edges, float)
    out = np.zeros(max(len(edges) - 1, 0))
    if out.size == 0 or len(dist) < 2:
        return out
    lo, hi = edges[:-1], edges[1:]
    for sp in spans:
        a = np.maximum(lo, float(sp.start_dist))
        b = np.minimum(hi, float(sp.end_dist))
        inside = b > a
        if np.any(inside):
            out[inside] += (np.interp(b[inside], dist, elapsed)
                            - np.interp(a[inside], dist, elapsed))
    return out


def paired_signflip_p(diffs, *, draws: int = COAST_SIGNFLIP_DRAWS, seed: int = 0) -> np.ndarray:
    """Two-sided paired sign-flip permutation p for each column of `diffs` (laps x k): under the
    null each lap's difference is as likely to have the other sign, so the observed |mean| is ranked
    against `draws` random sign patterns (seeded — the same session renders the same verdict). A
    column of zeros reads p = 1."""
    d = np.asarray(diffs, float)
    if d.ndim == 1:
        d = d[:, None]
    n, k = d.shape
    if n == 0 or k == 0:
        return np.ones(k)
    obs = np.abs(d.mean(axis=0))
    signs = np.random.default_rng(seed).integers(0, 2, size=(draws, n)) * 2.0 - 1.0
    null = np.abs(signs @ d) / n
    # A hair of tolerance so a sign pattern reproducing the observed mean counts as reaching it
    # rather than falling a last-bit float short.
    hits = np.count_nonzero(null >= obs - 1e-12 * np.maximum(obs, 1.0), axis=0)
    return (1.0 + hits) / (draws + 1.0)


def coast_report(cids, seconds_by_lap) -> CoastReport | None:
    """The session's coasting by place: `seconds_by_lap` holds one row per clean lap of the 2N+1
    piece seconds `coast_seconds_by_piece` returns, pieces in partition order (S/F → C1, C1,
    C1 → C2, …). None without a lap. See COAST_LEAD_ALPHA for the tie rule."""
    n_pieces = 2 * len(cids) + 1
    m = np.asarray(seconds_by_lap, float)
    if m.size == 0:
        return None
    m = m.reshape(-1, n_pieces)
    n = m.shape[0]
    mean = m.mean(axis=0)
    per_lap = float(mean.sum())
    order = [int(j) for j in np.argsort(-mean, kind="stable") if mean[j] > 0.0]
    if not order:
        return CoastReport(n_laps=n, per_lap_s=0.0, places=[], lead_separable=False)
    lead, rest = order[0], order[1:]
    tied = {lead}
    if rest:
        p = paired_signflip_p(m[:, [lead]] - m[:, rest])
        tied |= {j for j, pj in zip(rest, p, strict=True) if pj >= COAST_LEAD_ALPHA}

    def name(j: int) -> tuple[str, int]:
        return (f"C{cids[(j - 1) // 2]}", int(cids[(j - 1) // 2])) if j % 2 else \
            straight_label(cids, j // 2)

    places = [CoastPlace(index=j, label=name(j)[0], ring_cid=name(j)[1],
                         s_per_lap=float(mean[j]), laps=int(np.count_nonzero(m[:, j] > 0.0)),
                         share=float(mean[j]) / per_lap, tied=j in tied)
              for j in order]
    return CoastReport(n_laps=n, per_lap_s=per_lap, places=places,
                       lead_separable=len(tied) == 1)


def straights_report(cids, times_by_lap, traps_by_lap, exits_by_lap,
                     best_exits, edges_by_lap=None, best_edges=None) -> list[StraightStat]:
    """The straight-line report: per straight (N corners → N+1 straights) the session's
    best/median/σ time + trap-speed best/median, and the preceding corner's exit-speed
    delta + leverage (see StraightStat). `times_by_lap`/`traps_by_lap` rows are aligned to
    the N+1 straights; `exits_by_lap` rows + `best_exits` to the N corners. Ragged rows are
    tolerated (a degenerate lap contributes only the columns it has).

    WHICH CELLS COUNT (C4). `edges_by_lap` is `CornerModel.lap_edge_resolved` for each row (the
    four per-lap inputs then being the same laps in the same order), `best_edges` the same for the
    lap `best_exits` came from. Straight k runs from corner k's exit (the start line for k=0) to
    corner k+1's entry (the finish line for k=N), and a value counts only where the edges it READS
    were matched on track: the time needs both ends, the trap speed its end, the exit Δ the
    preceding corner's exit — on the best lap too, or there is no Δ. The timing line is the warp's
    fixed anchor, so it is always matched. An interpolated corner edge put the time a median
    0.22 s and the speed there a median 1.1-1.6 km/h (up to 11.5 km/h) off an independent line
    crossing on the D24 0060 pair before #335 (`CornerModel.lap_edge_resolved`); a matched one
    0.004 s and 0.015 km/h. None → every value counts (a pure caller with no warp)."""
    n_corners = len(cids)

    if edges_by_lap is not None:
        def keep(rows, flags):
            return [[v if k < len(ok) and ok[k] else np.nan for k, v in enumerate(r)]
                    for r, ok in zip(rows, flags, strict=True)]

        ends = [(list(e[1::2]), list(e[0::2])) for e in edges_by_lap]   # (exits, enters)
        start_ok = [[True, *exits] for exits, _ in ends]                 # N+1 straight starts
        end_ok = [[*enters, True] for _, enters in ends]                 # N+1 straight ends
        times_by_lap = keep(times_by_lap, [[a and b for a, b in zip(s0, s1, strict=True)]
                                           for s0, s1 in zip(start_ok, end_ok, strict=True)])
        traps_by_lap = keep(traps_by_lap, end_ok)
        exits_by_lap = keep(exits_by_lap, [exits for exits, _ in ends])
    if best_edges is not None:
        best_exit_ok = list(best_edges[1::2])
        best_exits = [v if k < len(best_exit_ok) and best_exit_ok[k] else np.nan
                      for k, v in enumerate(best_exits)]
    n_laps = len(times_by_lap) if edges_by_lap is not None else None

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
        exits = column(exits_by_lap, k - 1) if k >= 1 else None
        if exits is not None:  # iff k >= 1
            if len(exits) and k - 1 < len(best_exits) and np.isfinite(best_exits[k - 1]):
                delta = float(np.median(exits)) - float(best_exits[k - 1])
        spread = (med - best) if med is not None and best is not None else None  # both iff n
        leverage = (max(0.0, -delta) * max(0.0, spread)
                    if delta is not None and spread is not None else 0.0)
        label, ring = straight_label(cids, k)
        out.append(StraightStat(
            index=k, label=label, ring_cid=ring, n=n,
            best_s=best, median_s=med, sigma_s=sigma(times),
            trap_best_kmh=float(np.max(traps)) if len(traps) else None,
            trap_median_kmh=float(np.median(traps)) if len(traps) else None,
            exit_delta_kmh=delta, leverage=leverage,
            n_laps=n_laps,
            n_trap=len(traps) if n_laps is not None else None,
            n_exit=len(exits) if (n_laps is not None and exits is not None) else None,
        ))
    return out


def phase_matrix(cids, triples_by_lap) -> PhaseReport:
    """Aggregate per-lap per-corner (entry, apex, exit) Δt-vs-best triples into the session
    phase-loss matrix: per corner the MEDIAN triple (element-wise, over laps with a fully
    finite triple; ragged rows tolerated), plus the positive-part PhaseShare (see the
    dataclass for why positive-part). The per-lap triples come from the SAME per-lap-aligned
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
    """The trend VALUE as both surfaces print it: `+0.14 s/lap`, `−0.35 s/lap` (the true minus,
    `_signal.fmt_signed`), or a flat `0.00 s/lap` for a signed near-zero (a "±0.00" display reads
    as a glitch). None passes through as None."""
    if slope is None:
        return None
    return fmt_signed(slope, 2, "s/lap")


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
        v = np.asarray([np.nan if (x := vs[k]) is None else float(x) for k in ks], float)
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
        col = [(k, x) for k, row in enumerate(cells) if (x := row[c]) is not None]
        if not col:
            bests.append(None)
            medians.append(None)
            best_lap.append(None)
            scales.append(MATRIX_SCALE_MIN_S)
            continue
        k_best = min(col, key=lambda kv: kv[1])[0]
        med, scale = _typical_and_scale([v for _, v in col])
        bests.append(cells[k_best][c])
        medians.append(med)
        best_lap.append(ids[k_best])
        scales.append(scale)
    return SplitMatrix(lap_ids=ids, columns=n_cols, cells=cells, bests=bests,
                       medians=medians, best_lap=best_lap, scales=scales)


def _typical_and_scale(values) -> tuple[float, float]:
    """One heat-grid column's two anchors: its MEDIAN, and how far above it a cell has to be to
    read as behind — the MATRIX_SCALE_PCT percentile of the column's own deviations, floored at
    MATRIX_SCALE_MIN_S. Shared by the sector and the corner grid so a ▼ means one thing on the
    page (see MATRIX_SCALE_MIN_S for why the anchor is the median and why there is a floor)."""
    med = float(np.median(values))
    return med, max(MATRIX_SCALE_MIN_S,
                    float(np.percentile([v - med for v in values], MATRIX_SCALE_PCT)))


def _behind_at_print(val: float, med: float, scale: float) -> bool:
    """The ▼ comparison, made on the values the reader is SHOWN (see SplitMatrix.is_behind): the
    printed cell minus the printed median (`pass_vs_typical_s`, the gap every hover and note prints
    beside the ▼) against the printed step. The median is rounded too since #586: with it unrounded
    a ▼ gap could print a hundredth below its own step (5 of 9,127 ▼ on the hundredth-edge sweep).
    Measured: the marks move on 3 of 144,400 sweep cells and on no demo cell."""
    return pass_vs_typical_s(val, med) >= round(scale, MATRIX_DECIMALS)


def corner_matrix(lap_ids, cids, times_by_lap, resolved_by_lap) -> CornerMatrix | None:
    """The laps × corners grid (see CornerMatrix). `times_by_lap[k]` is lap `lap_ids[k]`'s
    time-in-corner list and `resolved_by_lap[k]` whether each of those windows had both edges
    matched on track (`CornerModel.lap_corner_resolved`). A row whose length disagrees with the
    corner count is kept but blanked, as in split_matrix.

    None below MATRIX_MIN_LAPS laps or with no corner. A COLUMN with fewer than MATRIX_MIN_LAPS
    resolved cells keeps its cells and gets no typical and no scale, so nothing in it is marked."""
    ids = list(lap_ids)
    cids = [int(c) for c in cids]
    n_cols = len(cids)
    if len(ids) < MATRIX_MIN_LAPS or n_cols == 0:
        return None
    cells: list[list[float | None]] = []
    resolved: list[list[bool]] = []
    for times, res in zip(times_by_lap, resolved_by_lap, strict=True):
        times, res = list(times), list(res)
        ok = (len(times) == n_cols and len(res) == n_cols
              and all(np.isfinite(x) and x > 0 for x in times))
        cells.append([float(x) for x in times] if ok else [None] * n_cols)
        resolved.append([bool(x) for x in res] if ok else [False] * n_cols)
    medians: list[float | None] = []
    scales: list[float | None] = []
    n_resolved: list[int] = []
    for c in range(n_cols):
        col = [row[c] for row, res in zip(cells, resolved, strict=True)
               if row[c] is not None and res[c]]
        n_resolved.append(len(col))
        if len(col) < MATRIX_MIN_LAPS:
            medians.append(None)
            scales.append(None)
            continue
        med, scale = _typical_and_scale(col)
        medians.append(med)
        scales.append(scale)
    return CornerMatrix(lap_ids=ids, cids=cids, cells=cells, resolved=resolved,
                        medians=medians, scales=scales, n_resolved=n_resolved)


# ------------------------------------------------------------------------- quickest passes
# The Corners tab's "Quickest passes" view: per corner, the quickest pass, the lap it was on, and
# every other pass LEVEL with it. Read off the CORNERS BY LAP cells and nothing else, so its time
# is the CORNERS table's Best by construction (the same counted cells, the same minimum).
#
# WHY "LEVEL" AND NOT A RANK. refused-2026-09 §32 measured the quickest pass through a corner on the
# demo: at six corners of seven it is one of 6-11 passes within the 0.07 s timing step, and which
# one comes out on top changes in up to 69 % of the draws under that step. Naming it alone would be
# a coin toss presented as a fact, so every pass level with it is listed beside it, quickest first,
# and the view plays them in turn. The step is the app's own: `library.PB_PRECISION_S`, or under an
# estimated clock that clock's published bound (`rolling_level_floor`, the pair `story.tie_floor`
# reads), compared on the printed milliseconds as `story._best_lap` compares lap times.


@dataclass(frozen=True)
class QuickestPass:
    """One corner's quickest counted pass. `time_s` / `lap_id` are None when no lap's pass through
    the corner was matched on track at both edges (nothing to name). `level_laps` are the OTHER laps
    whose pass is level with it, quickest first; `next_gap_s` is the printed gap to the next
    quickest counted pass (None with fewer than two)."""

    cid: int
    time_s: float | None
    lap_id: int | None
    level_laps: tuple[int, ...]
    next_gap_s: float | None
    n_counted: int
    level_times: tuple[float, ...] = ()   # each level lap's own pass, aligned with level_laps
    # Every COUNTED pass (lap_id, time_s) in the grid's lap order, the corner's typical (the grid
    # column's median: the typical a Coaching row's Time lost is measured from), and the laps whose
    # cell CORNERS BY LAP marks ▼. The strip beside the row draws these (`pass_strip`).
    marks: tuple[tuple[int, float], ...] = ()
    typical_s: float | None = None
    behind_laps: tuple[int, ...] = ()

    @property
    def alone(self) -> bool:
        """The quickest stands clear of every other pass: nothing level with it, and a runner-up
        exists to be clear of (one counted pass is not "alone", it is unchallenged)."""
        return self.lap_id is not None and not self.level_laps and self.next_gap_s is not None

    def play_order(self) -> tuple[int, ...]:
        """The laps the view plays on repeated activation: the quickest, then each level lap."""
        return () if self.lap_id is None else (self.lap_id, *self.level_laps)

    def pass_time(self, k: int) -> float | None:
        """The time of `play_order()[k]`'s own pass through the corner."""
        if self.time_s is None:
            return None
        return self.time_s if k == 0 else self.level_times[k - 1]


@dataclass(frozen=True)
class QuickestPasses:
    """`quickest_passes`'s result: one QuickestPass per corner in track order, the level step used,
    and the laps NOT counted at one corner or more (left out of the grid — ⊘ excluded, ⚠ dropout —
    or with a corner edge not matched on track), session order."""

    passes: list[QuickestPass]
    level_s: float
    not_counted: tuple[int, ...]


def _ms(x: float) -> int:
    return int(round(float(x) * 1000))


def quickest_passes(matrix: CornerMatrix | None, *, clock_estimated: bool = False,
                    lap_ids=()) -> QuickestPasses | None:
    """Per corner of `matrix` (the CORNERS BY LAP grid), its quickest counted pass and the passes
    level with it (see the block above). `lap_ids` are the session's laps a reader could expect
    counted (the Laps table's); any of them that is not a grid row is reported as not counted.

    None when there is no grid (no corners, or under MATRIX_MIN_LAPS laps: the grid's own gate)."""
    if matrix is None:
        return None
    level_s = rolling_level_floor(clock_estimated)
    rows = list(matrix.lap_ids)
    partial = {lap for lap, row, res in zip(rows, matrix.cells, matrix.resolved, strict=True)
               if not all(v is not None and ok for v, ok in zip(row, res, strict=True))}
    partial |= set(lap_ids) - set(rows)
    out = [_quickest_column(matrix, rows, col, cid, level_s, matrix.resolved)
           for col, cid in enumerate(matrix.cids)]
    return QuickestPasses(out, level_s, tuple(sorted(partial)))


def _quickest_column(matrix, rows, col: int, key: int, level_s: float, resolved=None
                     ) -> QuickestPass:
    """Column `col` of a heat grid (CornerMatrix or SplitMatrix: `cells`, `medians`, `is_behind`)
    as one QuickestPass keyed `key`. `resolved` (the corner grid's) masks cells not counted."""
    marks = tuple((int(lap), float(val))
                  for r, (lap, row) in enumerate(zip(rows, matrix.cells, strict=True))
                  if (val := row[col]) is not None and (resolved is None or resolved[r][col]))
    counted = sorted((t, lap) for lap, t in marks)
    if not counted:
        return QuickestPass(key, None, None, (), None, 0, ())
    best, lap = counted[0]
    gaps = [(_ms(t) - _ms(best)) / 1000 for t, _ in counted[1:]]
    level = [(lp, t) for (t, lp), g in zip(counted[1:], gaps, strict=True) if is_level(g, level_s)]
    behind = tuple(int(rows[r]) for r in range(len(rows)) if matrix.is_behind(r, col))
    return QuickestPass(key, best, lap, tuple(lp for lp, _ in level),
                        round(gaps[0], 3) if gaps else None, len(counted),
                        tuple(t for _, t in level), marks, matrix.medians[col], behind)


# ------------------------------------------------------------------------ quickest sectors
# The same view over the driver's own SECTORS (Stats ▸ SPLITS cells, one source), when he has drawn
# sector lines. A STRETCH is what a pass goes through: a corner, keyed by its cid (>= 1), or a
# sector, keyed by `sector_key` (< 0) — ONE int key, so every pass path (play, zoom, the subject,
# the walk, ⌘K) carries a sector exactly as it carries a corner.
#
# WHY A WIDER LEVEL STEP FOR A SPLIT. A split is timed where the lap crosses two sector lines, and
# `docs/ACCURACY.md` publishes its σ: "0.11 s at recording C's GPS noise", truth matrix row 2
# (`sector.interior_sd` at noise 2, SPLIT_SIGMA_S unrounded), several times a lap time's. The
# HOUSE RULE for "level" is 2σ of the difference of two figures that each carry σ, DIFF_2SIGMA_K ×
# σ: the lap step `library.PB_PRECISION_S` is 2√2 × 0.0247 s, and refused-2026-09 §26 gated sector
# chips at 2√2 × this same split σ ("The gate is then 0.30 s", `clip_refusals.GATE_K` is this
# constant). So two splits closer than SECTOR_LEVEL_S (0.30 s) are LEVEL. Measured
# (studio/dev/probes/sector_quickest.py, the demo and seeds 1 and 2, 2/3/4 sector lines, 84
# sectors): no sector's TRUE quickest is 0.07 s clear of its next (the largest true margin is
# 0.055 s), yet at 0.07 s four sectors at noise 2 and ten at noise 4.5 would wear a lone ★, six of
# those ten on the wrong lap; at SECTOR_LEVEL_S none does, at any noise. On such sessions every
# sector is a tie: the view names the quickest lap, plays each level lap in turn, and says why.
DIFF_2SIGMA_K = 2.0 * math.sqrt(2.0)
SPLIT_SIGMA_S = 0.106567
SECTOR_LEVEL_S = round(DIFF_2SIGMA_K * SPLIT_SIGMA_S, 2)


def sector_key(col: int) -> int:
    """The stretch key of sector column `col` (0-based: S1 is -1)."""
    return -(int(col) + 1)


def sector_col(key: int) -> int | None:
    """The sector column a stretch key names, or None for a corner's cid."""
    return -int(key) - 1 if key < 0 else None


def sector_level_floor(clock_estimated: bool) -> float:
    """The step under which two splits read level: SECTOR_LEVEL_S, or an estimated clock's wider
    one should it ever be wider."""
    return max(SECTOR_LEVEL_S, rolling_level_floor(clock_estimated))


def quickest_sectors(matrix: SplitMatrix | None, *, clock_estimated: bool = False,
                     lap_ids=()) -> QuickestPasses | None:
    """Per sector of `matrix` (the SPLITS grid), its quickest split and the splits level with it,
    keyed `sector_key(col)`. Laps of `lap_ids` with a blank row, or no row, are not counted.
    None without the grid (no sector lines, or under MATRIX_MIN_LAPS laps)."""
    if matrix is None:
        return None
    level_s = sector_level_floor(clock_estimated)
    rows = list(matrix.lap_ids)
    partial = {lap for lap, row in zip(rows, matrix.cells, strict=True) if None in row}
    partial |= set(lap_ids) - set(rows)
    out = [_quickest_column(matrix, rows, col, sector_key(col), level_s)
           for col in range(matrix.columns)]
    return QuickestPasses(out, level_s, tuple(sorted(partial)))


# The Quickest passes row's strip: every counted pass through the corner as a dot on a time axis,
# quickest at the left. ONE ROW, ONE SCALE: a scale shared by the seven rows would squash a tight
# corner (the demo's C2 spans 0.11 s, its C1 0.44 s) into a few pixels, so each row runs from its
# quickest to its slowest pass and says so by drawing the LEVEL BAND at its true width, quickest →
# quickest + the timing step: a wide band is a tight corner. The span is floored at two steps, so
# the band never fills more than half the strip and can always be told from the whole range. A
# cell CORNERS BY LAP marks ▼ (the lap that spun) sits off the scale at its right edge rather than
# squashing the rest: the strip's only "far slower" is the grid's own ▼ rule, no new threshold.
STRIP_MIN_SPAN_STEPS = 2


@dataclass(frozen=True)
class StripMark:
    """One counted pass on a row's strip: `x` is its place on the row's scale, 0 (the quickest) to
    1 (the slowest on-scale pass); `level` whether it is level with the quickest (the row's
    `play_order`, the `is_level` rule — never the pixel); `behind` a ▼ cell drawn off the scale."""

    lap_id: int
    time_s: float
    x: float
    level: bool
    behind: bool


@dataclass(frozen=True)
class PassStrip:
    """`pass_strip`'s result: the row's scale (`lo` → `hi` seconds), the level band's right edge
    (`band_x`, as a fraction of the scale; it starts at 0), the typical's place (`typical_x`, None
    without a typical), and one StripMark per counted pass in lap order."""

    lo: float
    hi: float
    band_x: float
    typical_x: float | None
    marks: tuple[StripMark, ...]

    def nearest(self, x: float) -> StripMark | None:
        """The mark nearest scale position `x` (a hover), the earlier lap on a tie."""
        return min(self.marks, key=lambda m: abs(m.x - x), default=None)


def pass_strip(qp: QuickestPass, level_s: float) -> PassStrip | None:
    """Row `qp`'s strip (see the block above). None with no counted pass."""
    if qp.time_s is None or not qp.marks:
        return None
    lo = float(qp.time_s)
    behind = set(qp.behind_laps)
    on_scale = [t for lap, t in qp.marks if lap not in behind]
    hi = max([lo + STRIP_MIN_SPAN_STEPS * level_s, *on_scale])
    span = hi - lo

    def frac(t: float) -> float:
        return min(max((t - lo) / span, 0.0), 1.0)

    level = set(qp.play_order())
    marks = tuple(StripMark(lap, t, 1.0 if lap in behind else frac(t), lap in level, lap in behind)
                  for lap, t in qp.marks)
    typical = None if qp.typical_s is None else frac(float(qp.typical_s))
    return PassStrip(lo, hi, level_s / span, typical, marks)


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
    event lists and `brake_time` the seconds on the brakes inside those events (all already cached
    per lap by DrivingChannels)."""

    def __init__(self, *,
                 gmeter: Callable[[], GMeter],
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
                 brake_time: Callable[[int], float],
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
        self._brake_time = brake_time
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
        brake/coast reduce the driving-channel event lists. `brake_s` is the channel's own time on
        the brakes (`brake_time`), never Σ event.duration: an event is held open through its
        lift-off tail, so that sum booked 26.0 s a lap on MK 0067 where 17.8 s was braking.
        Signal-absent fields are None (never 0) — see LapStat."""
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
                brake_s = float(self._brake_time(i))
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
        windows = [w for w in windows if w is not None]  # the same `keep` rows, in order
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

    def slowest_corner(self) -> tuple[float, int, float, int] | None:
        """(typical km/h, laps, lowest km/h, its lap id): the SPEED · G "slowest corner" tile.

        TYPICAL = the median over the CLEAN laps of each lap's slowest speed — the statistic STINTS'
        "Min" column takes per run, over the same laps, off the same `LapStat.vmin_kmh`. The session
        MINIMUM this tile printed before is one lap's moment, not a corner: measured (LOOK-12, QA
        2026-09-26) it was 14.5 km/h on MK_18_09 lap 18 against a typical 30.1 km/h (4 of 19 laps
        under 21 km/h: traffic and the opening laps), and 31.4 km/h on SD_19_09 lap 6 against 39.3.
        The minimum and its lap stay available for the tooltip. None with no speed on a clean lap."""
        clean = set(self._consistency_lap_ids())
        rows = [(st.vmin_kmh, st.idx) for st in self.lap_stats()
                if st.vmin_kmh is not None and st.idx in clean]
        if not rows:
            return None
        low = min(rows)
        return float(np.median([v for v, _ in rows])), len(rows), float(low[0]), int(low[1])

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
