"""CornerModel — the per-segmentation corner analysis extracted from Session: the detected
corner list + reference total, the per-lap projected corner stats (incl. the cross-recording
reference's under reference_id), the per-corner session-best times, and the IDEAL-LAP segment
composite (`segment_bests`). All derive from the current segmentation, so Session composes this
service + delegates.

PACER-FREE (numpy on Session's cached per-lap primitives). `invalidate()` (from
set_timing_lines) drops every cache on re-segment; `invalidate_stats()` drops only the
per-lap stats when a cross-recording reference changes (the detection windows are unchanged).

DEPENDENCY INJECTION (like studio/render_cache.py): the constructor takes Session-bound
callables over Session's own primitives, so NO method here reaches a `_`-private attribute of
Session — Session owns the pacer side + wires its privates into the callables.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np

from . import corners
from ._signal import plural

# "not yet computed" sentinel (None is a legal cached value); module-local to avoid importing
# Session.
_UNSET = object()

# ------------------------------------------------------- ideal-lap donor admission (D1)
# A partition edge pair closer together than this ON THE REFERENCE ODOMETER is a POINT, not a
# segment. Two cases produce one: the first corner can begin on the start line (and the last can
# end on it), and the detector can leave a sub-metre sliver between two corners it did not merge.
# A point carries ~0 s on every lap, so every lap "wins" it with 0 and it contributes nothing to
# the composite either way — it must NOT be mistaken for a lap that collapsed (below).
POINT_SPAN_M = 0.5
# A lap may donate a segment only if it drove a COMPARABLE PIECE OF TRACK there. Every lap's
# segment times sum exactly to its lap time (corners.segment_times asserts it), so a segment whose
# projected window is short on one lap has simply pushed that time into its neighbour — the pair
# still sums, and a per-segment minimum over the laps then banks the short window's time without
# ever paying for the long one. The minimum buys the measurement window, not the driving.
#
# The test is SYMMETRIC and against the lap's OWN expected span: a segment is comparable when its
# projected width is within MAX_DONOR_SPAN_DEV of `ref_span × total_lap / total_ref` — the width
# the reference segment has after this lap's uniform line-length scaling. Inflated windows are
# refused for the same reason shrunken ones are: they are the other half of every shrink, and their
# time pollutes the neighbouring corner's Δ.
#
# THIS REPLACES `MIN_DONOR_SPAN_FRAC = 0.5`, A ONE-SIDED FLOOR CALIBRATED ON A FIXTURE THAT COULD
# NOT EXPRESS THE PROBLEM. Its note claimed "every cell that is not a hard collapse carries ≥ 0.93
# of the segment's reference span, and every collapsed one ≤ 0.0001 … a separator, not a tuned
# knob". That is true of the recording it was measured on (D24 0062: 1 cell in 1,625 below 0.93)
# and false of the owner's other one: on the D24 0060 pair 58 of its 874 non-point cells sit
# outside ±7 %, the per-segment WINNERS run down to 0.729 of the reference span, and 10 of 23
# winners are below 0.93. The floor at 0.5 admitted every one of them. (Non-point cells only: a
# POINT column has ref_span 0, so "deviation from expected" is 0/0 there and the ratio is not
# defined — every all-950 count of this quantity is a category error.)
#
# ── WHAT THIS TEST IS: A BIAS TRIMMER, NOT A CLASSIFIER ───────────────────────────────────────
# It measures deviation from UNIFORM line-length scaling, which is not the same quantity as
# projection error, and the gap is measurable. Scoring every (lap, segment) cell against the
# directly matched spatial position of BOTH its edges — ground truth to within
# corners.SPATIAL_MATCH_MAX_M — on the three D24 fixtures:
#
#   fixture     cells w/ true span err >5 %   of those REFUSED    clean cells REFUSED (FPR)
#   0060 pair              59                     0   (0 %)          39 / 399   (10 %)
#   0060 ch 1              42                     0   (0 %)          28 / 286   (10 %)
#   0062                   25                     0   (0 %)          10 / 1462  ( 1 %)
#
# Recall was 0 % STRUCTURALLY when this was measured: every convictable cell sat on a lap below the
# then-current `corners.NORMALIZED_DRIFT_MAX`, whose projection IS `ref_span × total_lap/total_ref`,
# so its deviation from that was zero by construction and this test could never reject it however
# misaligned the window was. And those laps were not clean: they carried a median 1.16 % / p90
# 9.02 % true span error on the pair (1.03 / 9.42 on ch 1, 0.48 / 2.92 on 0062), 5 cells worse than
# −10 %. THAT GATE IS GONE (every lap is warped now), so those cells are live here — which is what
# the DEFERRED note below was asking for and what drops admitted cells 880 → 780 on the pair.
# Conversely, of the cells it DOES refuse, every one that can be scored at all was refused on
# directly measured track: pair 39 scorable of 70 refusals, all 39 with both edges on matched
# knots; ch 1 28 of 50, all 28; 0062 10 of 10. (The rest — 31 and 22 — have at least one
# interpolated edge, so there is no ground truth to convict or acquit them with.) It removes a
# downward bias from the minimum; it does not identify bad cells, and nothing here should be read
# as if it did.
#
# ── CALIBRATION, AND HOW WIDE IT REALLY IS ────────────────────────────────────────────────────
# The arbiter is each recording's own order statistic, fitted on its BELOW-gate laps (which every
# candidate projects identically) and extrapolated to the full lap count. On the 0060 pair it reads
# 0.623 s per doubling → 65.228 s at N = 38, and the candidates score:
#
#   admission         ideal      vs arbiter    winner span-fraction min
#   0.5 floor (old)   62.869 s     −2.359 s           0.729
#   ±10 %             64.321 s     −0.907 s           0.905
#   ±7 %              64.854 s     −0.374 s           0.930
#   ±5 % (shipped)    65.149 s     −0.079 s           0.950
#   ±3 %              65.291 s     +0.063 s           0.970
#
# DO NOT READ −0.079 AS "THE BIAS". The same measurement is +0.252 on 0060 chapter 1 (same track,
# same car, same session) and −0.255 on 0062: the cross-fixture spread is ±0.25 s, three times the
# pair's residual, and that spread — not the pair's number — is this constant's honest width.
# The arbiter is not artifact-free either; it is fitted on exactly the laps the paragraph above
# shows this test cannot police. 0.05 is where the pair's bias is smallest while the other two
# straddle zero; anything in [0.03, 0.07] is defensible on this evidence.
#
# ── WHAT IT IS WORTH, SPLIT HONESTLY ──────────────────────────────────────────────────────────
# Against the pre-fix number, frame repair vs this constant: 0060 pair +0.981 / +1.299 s
# (43 % / 57 %); 0060 ch 1 +0.824 / +1.607 (34 % / 66 %); 0062 +0.000 / +0.217 (0 % / 100 %).
# THIS CONSTANT IS THE LARGER HALF ON ALL THREE. The frame repair is what makes it meaningful — it
# is what stops one lap carrying two frames — but crediting the headline move to the projection
# alone is wrong.
#
# ── THE DEFERRED HALF, NOW DONE ──────────────────────────────────────────────────────────────
# This block used to record that `corners.NORMALIZED_DRIFT_MAX` kept below-drift laps on the
# normalized projection, leaving this test inert on 22 of the pair's 38 laps, and what that cost
# measured LIKE FOR LIKE — gated vs warp-every-lap at the SAME admission tolerance, so the two
# changes were not conflated:
#
#              pair      ch 1      0062
#   at ±5 %   +0.316 s  +0.347 s  −0.071 s
#   at ±3 %   +0.501 s  +0.670 s  +0.060 s
#
# (The ±5 % row is also the move against the then-shipped number, since that is the shipped
# tolerance. An earlier draft quoted +0.644/+0.712/+0.110 for the ±3 % row by differencing against
# the ±5 % shipped value, which folds the tolerance change into the gate's residual.) THE GATE IS
# NOW GONE and the ±5 % row is realised: the pair's ideal reads 65.464 s and 0062's 66.709 s.
#
# CALIBRATION NOTE, because removing the gate moved the arbiter too. The table above is fitted on
# the BELOW-gate laps, which under the gate every candidate projected identically — that is what
# made it a neutral yardstick. Warping every lap moves those very laps, so the arbiter has to be
# REFITTED per candidate before the two ideals can be compared. Refitted, it does NOT discriminate:
# the gated ideal sits −0.046 s from its own arbiter on the pair and −0.182 s on 0062, the
# warp-every-lap ideal +0.068 s and +0.176 s from its own — same order, opposite signs, all inside
# the ±0.25 s cross-fixture spread above. Comparing warp-every-lap against the GATED arbiter reads
# a spurious +0.236 s on the pair, which is a moved yardstick, not a bias. The case for warping
# every lap rests on the boundary residual in corners.py, not on the ideal.
MAX_DONOR_SPAN_DEV = 0.05
# ── SUB-RESOLUTION SEGMENTS: RE-MEASURED AFTER #300, AND STILL NOT "FIXED" ────────────────────
# On a 2.3 m sliver the ±5 % band is ±0.11 m, far under the ±corners.SPATIAL_MATCH_MAX_M a
# boundary match is GUARANTEED to, and 16 of the 0060 pair's 23 real segments have a band under
# that tolerance at all. But that gate is the WORST CASE a match may pass, not the error it
# carries. #300 warps every lap, and the measured per-boundary longitudinal residual is now a
# median 0.099 m on the pair, 0.077 m on ch 1 and 0.010 m on 0062 — so scored against the
# projection's OWN error the count is 7, 4 and 0 of 23. On the 65-lap recording the app
# headlines, the class this paragraph is about no longer exists at all; the pair's segment 4
# (C2-C3, 2.25 m) refuses 6 of its 38 laps, 5 of them within one cell-residual of the threshold.
#
# Exempting them (as POINT segments are exempt) was implemented and MEASURED, then reverted. The
# rows below are that measurement RE-RUN on today's tree; the ones they replace predate #300:
#
#   exempt ref_span ≤ 3.0 m   pair 65.461 (−0.004)  ch1 66.450 (0.000)  0062 66.709 (0.000)
#     …but the pair's WINNER span-fraction floor falls 0.950 → 0.841, because a 2.3 m segment is
#     then won on a window 0.35 m off — noise, admitted into a MINIMUM.
#   exempt band < 3.0 m       pair 64.265 (−1.200)  ch1 65.370 (−1.080)  0062 66.525 (−0.184)
#     …which exempts the 42.8 m straight this whole repair is about (band ±2.14 m) and reverts
#     most of the fix.
#
# The narrow version buys 0.004 s of principle and costs a downward bias; the wide version undoes
# the repair. AN ADMISSION RULE FEEDING A MINIMUM MUST FAIL CLOSED: rejecting a lap arbitrarily
# only removes a candidate (bounded here by one 0.15 s segment), while admitting one arbitrarily
# lets noise win the segment — the same failure direction as the defect this constant exists for.
#
# AND THE PARTITION-DESIGN CHANGE THIS BLOCK USED TO ASK FOR WAS BUILT AND REFUSED. "Stop cutting
# sub-sample segments" = absorb every straight under 5 m into the corner before it, so the sliver
# is never a segment; corner and segment counts are unchanged, because merging the corner PAIR
# instead is a re-cut, and a coarser partition raises the ideal +0.2 … +1.5 s for the reason
# IdealSample's last paragraph gives. It moves the ideal +0.036 / +0.030 / +0.026 s on the three
# fixtures and DOES NOT REMOVE THE NOISE IT WAS PROPOSED FOR: bootstrapping the admission over
# each boundary's OWN measured residual, the ideal's spread goes sd 0.294 → 0.289 s on the pair,
# 0.324 → 0.325 on ch 1 and 0.005 → 0.006 on 0062. The noise is not a property of SHORT segments —
# holding the sub-resolution ones at their shipped decision takes the pair's sd to 0.183, and its
# worst-flipping segments include a 17.3 m and a 61.6 m one, because the pair's badly matched
# boundaries sit around C7-C9 (median residual 2-3 m) and not on its slivers. The price is paid on
# screen: the best lap's time-in-corner moves up to +0.29 s on 5 of 12 corners, and every
# corner-derived leaf moves with it. Nothing is missing from the reader's surface either way — on
# all three fixtures the best lap is admitted on all 25 segments, so its decomposition drops no
# row. Measured 2026-09-17; the full refusal is in studio/docs/refused-2026-09.md.


class IdealSample(NamedTuple):
    """WHAT THE IDEAL LAP WAS MINIMISED OVER — the four counts any surface printing the composite
    has to print with it, because the composite is a function of all four.

    `SegmentBests.total` is a sum of per-segment MINIMA. A minimum over more laps is never larger
    and is usually smaller, so the "ideal" falls as a session accumulates laps, and it falls again
    when the partition is cut finer. Both are properties of an order statistic over a partition,
    not of the driving — so two ideals are only comparable when these counts are comparable.

    MEASURED on the owner's five recordings, 20,000 random subsets of the clean laps per N, the
    per-segment minimum re-taken over each subset while the PARTITION stays the recording's own —
    so the table isolates the sample-size effect from the re-cut effect the last paragraph
    measures. The Monte-Carlo standard error is ≤ 0.003 s on every cell; the `all` column is the
    whole recording, i.e. the number the app itself prints on it. (Re-detecting the corners from
    each subset as well — what the app would do if that subset were the whole recording — moved
    the cells by −0.02 … +0.11 s when it was measured, before #300, and changed nothing about the
    shape. Holding the partition is what makes the fall a theorem rather than a trend: same
    pieces, more candidates.)

    ⚠ STALE — NOT RE-MEASURABLE (T16). ‡ ROWS ARE NOT RE-MEASURED, and since #335 changed corner
    matching that is all five. #339 found the `all` cells stale by 0.05–0.43 s after #335. The D24
    and Sandown_09_05_2026 rows cannot be re-measured, because those recordings are no longer
    available. SD_30_08's can, and re-measured on 2026-09-19 as the app opens it, it is stale too:
    46.764 / 46.570 / 46.435 / — / 46.413 (23), a rate of 0.160 s. Its `all` cell moved 0.017 s,
    less than the 0.05 s #339 gives as the smallest move. The cells, the rates, the gaps and the
    decrements below, and every surface that quotes them, are the record of that measurement, not
    what the app computes today. The best-lap rates are the exception: a best lap is a lap time,
    which corner matching does not move, and SD_30_08's re-measured 0.119 as published.

    | recording        | 5 laps | 10 | 20 | 40 | all | per doubling of N |
    |------------------|--------|----|----|----|-----|-------------------|
    | D24 1 chapter ‡  | 68.184 | 67.776 | 67.424 | — | 67.403 (21) | 0.377 s |
    | D24 3 chapters ‡ | 67.917 | 67.516 | 67.179 | 66.883 | 66.709 (65) | 0.326 s |
    | Sandown ch 1 ‡   | 48.867 | 48.408 | 48.151 | — | 48.097 (24) | 0.340 s |
    | Sandown 3 ch ‡   | 48.290 | 47.933 | 47.633 | 47.382 | 47.265 (59) | 0.288 s |
    | SD_30_08 ‡       | 46.768 | 46.594 | 46.455 | — | 46.430 (23) | 0.153 s |

    WHICH ROWS WERE TRUE OF THE APP, AND WHEN. All five, until #335: re-measured after #300 warped
    every lap (it removed the drift gate), AS THE APP OPENS EACH RECORDING: `Session.load`, then
    the start line the owner saved beside it, which `StudioWindow` applies before anything is
    drawn. D24 is `GX010062.MP4` alone (1 chapter) and with `GX020062` + `GX030062` (3 chapters),
    and has no saved line. Sandown is `GX010059` alone and with `GX020059` + `GX030059`, SD_30_08 is
    `GX010065`, and both have one. The D24 `all` cells had moved from 67.831 to 67.403 and from
    66.781 to 66.709 — on 3 chapters the −0.071 s #300 measured for its own change
    (MAX_DONOR_SPAN_DEV's block), to rounding. The Sandown rows were measured before #300 on the
    loader's line and moved by +0.156 s (chapter 1, which counts 24 laps on the saved line and 23
    on the loader's) and −0.110 s. THE SD_30_08 ROW WAS NOT A LAP. It read 12.862 s over 25 "laps",
    because until T13 the loader's line cut each 46 s Sandown Park lap into a 13.3 s and a 34 s
    piece and counted the short ones (`load._fit_start_line`). Every cell, gap, decrement and
    top-rung rate here is what tests/test_ideal_sample_table.py prints from its fixed seed when
    pointed at that footage, so re-running it reproduced them rather than approximating them. A
    row that has not been re-measured against the current app carries a ‡; since #335, all five do
    (the ⚠ paragraph above the table).

    THE RATE COLUMN IS THE WHOLE MEASURED RANGE — (5-lap cell − `all` cell) ÷ log2(laps ÷ 5) — so
    it is recomputable from the row's own two ends, and tests/test_ideal_sample_table.py recomputes
    it. It used to be the last rung alone, which on three of these rows is 20 → 21 laps: a ~0.01 s
    difference across a 5 % change in N, the noisiest quantity in the table, published as its
    headline.

    There is no plateau, and the decrement does NOT grow with N — this docstring said it did, off
    the boundary projection #228 replaced. Re-measured it shrinks and stays large: on D24
    3 chapters, 0.413 s per doubling over 5 → 8 laps, 0.357 over 8 → 15, 0.304 over 20 → 30, and
    still 0.233 over 50 → 65. A thirteenfold range of N takes nearly half off the decrement but
    does not reach a floor. So the gap the app headlines ("on the table") keeps growing with lap
    count — D24 3 chapters reads −0.95 s at 5 laps and −1.49 s at 65, same driving, same
    recording, and on D24 1 chapter the gap at the fitted line is 1.37 s over its 21 laps.

    THE BEST LAP HAS THE SAME PROPERTY, WHICH IS WHY THE DISCLOSURE IS PER RECORDING AND NOT PER
    COLUMN. The best lap is also a minimum over the session's laps: measured the same way, row for
    row, it falls 0.171 / 0.179 / 0.167 / 0.105 / 0.119 s per doubling on those five, against the
    ideal's 0.377 / 0.326 / 0.340 / 0.288 / 0.153 — LESS than the ideal on all five over the whole
    range. (A best lap is a lap time, which #300 did not move. Re-run on the two D24 rows it gives
    0.170 … 0.172 and 0.178 … 0.179 across seeds.) This docstring once claimed the best lap was
    the more sample-dependent of the two on two of them. That was the old last-rung rate on the
    old projection, and it survives neither: at the top rung (20 → 21 laps) D24 1 chapter reads
    0.299 ideal against 0.189 best. It also cited SD_30_08's last doubling, where the best lap
    moved more than the ideal (0.070 s against 0.060 over 20 → 25 laps); those 25 laps were the
    13 s pieces, and on its 23 real laps the top rung (20 → 23 laps) reads 0.124 ideal against
    0.070 best. So no recording here has the best lap moving more, and the argument never needed
    one: the best lap is still not a column a ranking can trust, because 0.10 … 0.18 s per
    doubling is the same order of magnitude as the ideal's. Suppressing the ideal's ranking while
    leaving the best lap's alone would advertise a distinction the numbers do not support; naming
    the sample fixes both. See studio/library_dialog.py's Laps column.

    `corners` / `segments` are the partition's size, and they move when the corner detector re-runs
    — which it does on every start/finish-line drag. Measured over six start-line positions per
    recording, the detected corner count moved 11↔12 on D24 and 7↔8 on Sandown, and the headline
    gap moved with it. On D24 1 chapter it was 0.94 s at the fitted line and 1.08 … 1.48 s over
    those six positions (+58 %). On Sandown 3 chapters it was 1.14 s at the fitted line and
    1.16 … 1.93 s over the six (+69 %). A mechanical midpoint refinement (every segment split in
    two, no new information) bought another 0.30 … 1.67 s. NONE OF THIS PARAGRAPH'S NUMBERS IS
    RE-MEASURED. They predate #228 and #300, and the six positions were never recorded, so they
    cannot be repeated as they were taken. That 0.94 s fitted-line gap is 1.37 s today. Read them
    as the size of the effect, not as today's values. So a user who drags the line and sees the
    gap move is looking at a re-cut partition, not at their driving — and `corners`/`segments` on
    screen is what lets them see that."""

    donors: int    # distinct laps that won at least one segment (SegmentBests.donor_ids)
    laps: int      # clean laps the minimum ran over (SegmentBests.lap_ids)
    corners: int   # corners in the partition
    segments: int  # 2N+1 pieces the lap was cut into

    @property
    def straights(self) -> int:
        """The partition's straights: `segments - corners`. Named rather than re-derived at each
        call site, because the arithmetic is only obvious once you know the partition is 2N+1."""
        return self.segments - self.corners

    def caption(self) -> str:
        """The ideal's SAMPLE, as the tile caption states it: `theoretical best · 65 laps`.

        The Stats tile, the laps.csv trailer and the exported HTML report all print this — it is
        the disclosure §5.4 found the leaving-the-app surfaces skipping — so it is defined ONCE,
        on the value object that carries the counts. A surface that composes its own string from
        `.laps` is free to disagree about the wording the moment either is edited; this cannot."""
        return f"theoretical best · {self.laps} laps"

    def sentence(self) -> str:
        """The full disclosure paragraph under the tiles: what the minimum ran over, and why BOTH
        counts set it. Lives here for the same reason as `caption()` — the Stats page and the
        Qt-free export writer print the identical sentence, and export_data cannot import a view.

        Deliberately says nothing this class's own docstring cannot back: the measured per-doubling
        table and the partition-sensitivity numbers are there, not baked into shipping copy (§5.5
        is the standing lesson about empirical constants in honesty text)."""
        return (f"Stitched from {self.donors} of your {self.laps} clean laps, across the "
                f"{plural(self.corners, 'corner')} and {plural(self.straights, 'straight')} Pacer "
                "found here. Both counts set it: the ideal is the minimum over those laps of those "
                "pieces, so more laps find a lower one and a different set of corners cuts it "
                "differently.")


@dataclass(frozen=True)
class SegmentGain:
    """One row of the ideal lap's DECOMPOSITION — where a subject lap's gap to the composite
    lives, and whether that gain is a PLAN or a taunt (`SegmentBests.decomposition`).

    `gain` is the seconds the subject gives away in this segment; the gains over all segments
    sum EXACTLY to `subject lap time − SegmentBests.total`, because a lap's segment times sum
    exactly to its lap time. `beat` is how many of the composite's laps drove this segment at
    least as fast as the subject did — the ACHIEVABILITY, and the difference between "you have
    done this 42 times" and "you did it once".

    `priority` is the order the surface presents: `gain × beat/n`. Both factors are on screen,
    so the ranking is checkable by eye against the two columns beside it."""

    index: int              # segment index into SegmentBests (0 … 2N)
    label: str              # "C4" / "C3 → C4" / "S/F → C1" — stats.straights_report's convention
    gain: float             # s the subject lap gives away here (≥ 0)
    beat: int               # laps that drove it at least as fast as the subject (includes it)
    n: int                  # laps admitted on this segment
    donor: int | None       # 0-based lap id owning the segment best (None on a POINT segment)
    ring_cid: int | None    # the corner a surface should point at for this row

    @property
    def priority(self) -> float:
        """gain × the share of laps that already matched the subject here. See the class note —
        and `SegmentBests.beat_counts` for why the share is not a fixed-tolerance hit rate."""
        return self.gain * self.beat / self.n if self.n else 0.0


@dataclass(frozen=True)
class SegmentBests:
    """The IDEAL LAP as a composite of the corner/straight partition — the per-segment minimum
    over the session's clean laps, and everything a caller needs to explain it.

    WHY a partition and not a distance envelope: `corners.segment_times` asserts that a lap's
    2N+1 segment times SUM EXACTLY to its lap time, so summing one lap's best C3 with another's
    best back straight double-counts nothing and drops nothing — the pieces tile the lap. A
    pointwise minimum of cumulative-elapsed curves (what this replaced) cannot make that claim:
    every lap's cumulative elapsed ends at its own lap time, so the min at the finish line is
    just the BEST LAP TIME and the "ideal" is a structural duplicate of it.

    Fields:
      labels    — 2N+1 segment names in track order, ["start", "C1", "C1-C2", …, "C{N}-finish"].
                  The MODEL's names; a surface prints `display_label`, which is the app's.
      cids      — the N corner ids in track order (Corner.cid), so a row can name the corner it
                  belongs to and point a map ring at it without re-reading the corner list.
      lap_ids   — the laps that contributed a row, in session order (see CornerModel.segment_bests
                  for the set).
      times     — (len(lap_ids), 2N+1) float: each lap's own segment times.
      admitted  — (len(lap_ids), 2N+1) bool: which cells cover their own segment (see
                  MAX_DONOR_SPAN_DEV). It is a function of the SPANS, never of the times.
      resolved  — (len(lap_ids), 2N+1) bool: which cells were MATCHED on track at both of their
                  boundaries (`CornerModel.lap_segment_resolved`) rather than interpolated
                  between neighbours. Also never a function of the times.
      bests     — 2N+1 minima over the cells that are BOTH admitted and resolved (C5).
      donors    — 2N+1 lap ids, the argmin per segment; None for a POINT segment (best == 0),
                  where every lap ties at 0 and naming a winner would be arbitrary.
      s_edges   — 2N+2 normalized distances [0…1] of the partition edges on the REFERENCE
                  odometer — the x positions the cumulative ideal is defined at.
      donor_span— 2N+1 (enter, exit) odometer metres of each segment ON ITS DONOR'S own lap, so
                  the ideal curve can follow the donor's real pace through the segment instead of
                  a straight line (CornerModel.ideal_elapsed).
    """

    labels: list[str]
    cids: list[int]
    lap_ids: list[int]
    times: np.ndarray
    admitted: np.ndarray
    resolved: np.ndarray
    bests: list[float]
    donors: list[int | None]
    s_edges: list[float]
    donor_span: list[tuple[float, float]]

    @property
    def total(self) -> float:
        """The ideal lap time (s) — the sum of the per-segment minima. ≤ every donor lap's time,
        because each donor's own segments sum exactly to its lap time and a sum of minima can
        never exceed the minimum of those sums.

        IT IS AN ORDER STATISTIC, NOT A FLOOR, and every surface that prints it must print
        `sample` beside it. A minimum over more laps is never larger, so this number keeps falling
        as the session grows and moves again when the partition is re-cut — measured, tabulated
        and sourced in `IdealSample`. Nothing here is wrong; what would be wrong is showing the
        number without the counts that set it."""
        return float(sum(self.bests))

    @property
    def sample(self) -> IdealSample:
        """The four counts this composite is a minimum over — see `IdealSample`, which carries the
        measured table. One accessor so the Stats block, the hero chip and any future surface print
        the same four numbers rather than each deriving their own."""
        return IdealSample(donors=len(self.donor_ids()), laps=len(self.lap_ids),
                           corners=len(self.cids), segments=len(self.bests))

    def cumulative(self) -> np.ndarray:
        """The ideal's elapsed time at each partition edge (2N+2 values, 0 … total) — the ideal
        LAP CURVE's y, sampled at `s_edges`. Non-decreasing by construction (every segment time
        is ≥ 0), which is why the old envelope's `np.maximum.accumulate` repair is gone."""
        return np.concatenate(([0.0], np.cumsum(np.asarray(self.bests, float))))

    def donor_ids(self) -> list[int]:
        """The distinct laps the composite actually draws on, sorted. A correct ideal draws on
        more than one; exactly one means the ideal IS that lap (see `single_donor_id`)."""
        return sorted({d for d in self.donors if d is not None})

    def single_donor_id(self) -> int | None:
        """The lap id when ONE lap wins every non-point segment — the ideal is then that lap, not
        a synthetic one, and a surface must say so rather than print a duplicate of it. None when
        the composite is genuinely stitched (the normal case) or draws on no segment at all."""
        ids = self.donor_ids()
        return ids[0] if len(ids) == 1 else None

    def gains_vs(self, lap_id: int) -> list[float] | None:
        """Per segment, how much time `lap_id` leaves on the table there (its own time minus the
        segment best, ≥ 0). None when that lap did not contribute a row. This is the ideal's
        DECOMPOSITION — where the gap lives, not just how big it is."""
        if lap_id not in self.lap_ids:
            return None
        row = self.times[self.lap_ids.index(lap_id)]
        return [float(row[j] - self.bests[j]) for j in range(len(self.bests))]

    def display_label(self, j: int) -> str:
        """Segment `j`'s name as the app writes it: a corner is "C4", a straight is "C3 → C4"
        with the timing line spelled "S/F" at both ends ("S/F → C1", "C12 → S/F").

        This is `stats.straights_report`'s convention, deliberately and not by coincidence: the
        STRAIGHTS table and the ideal-lap decomposition list the SAME pieces of track a few
        hundred pixels apart, and two spellings of one segment would read as two segments.
        tests/test_stats_panel_realqt.py pins the two against each other."""
        if j % 2:
            return f"C{self.cids[(j - 1) // 2]}"
        left = f"C{self.cids[j // 2 - 1]}" if j else "S/F"
        right = f"C{self.cids[j // 2]}" if j // 2 < len(self.cids) else "S/F"
        return f"{left} → {right}"

    def ring_cid(self, j: int) -> int | None:
        """The corner segment `j` should point a map ring at: itself if it is a corner, and the
        corner FEEDING it if it is a straight — including the wrap, where the S/F straight is fed
        by the last corner (again `stats.straights_report`'s rule, `ring_cid`)."""
        if not self.cids:
            return None
        return self.cids[(j - 1) // 2] if j % 2 else self.cids[j // 2 - 1]

    def beat_counts(self, lap_id: int) -> list[tuple[int, int]] | None:
        """Per segment, (laps that drove it at least as fast as `lap_id` did, laps admitted
        there) — ACHIEVABILITY. None when that lap did not contribute a row. Ties count, so the
        subject always counts itself and the first number is never 0.

        THIS REPLACES `hit_counts(tol)`, WHICH MEASURED SEGMENT LENGTH. That method counted laps
        within a FIXED 0.1 s of the segment best, over segments whose own duration on these
        recordings runs 0.16 s to 10.07 s — so the tolerance was 62 % of one segment and 1 % of
        another, and the "achievability" it reported correlated with segment DURATION at
        r = −0.95 / −0.82 / −0.80 / −0.95 on the four real recordings. Ranking by
        gain × that rate put a 0.039 s straight at the top of D24's plan, above a 0.213 s corner.

        THIS COUNT IS SCALE-FREE BY CONSTRUCTION, WHICH IS A STRONGER CLAIM THAN A CORRELATION AND
        IS THE ONE THAT IS TRUE. It is the subject's RANK among the admitted laps: multiply any
        segment's column by an arbitrary c > 0 and `times[:, j] <= row[j]` is unchanged, while
        `admitted` is a function of the SPANS and not of the times at all — so every pair this
        returns is identical. `hit_counts(0.1)` is not: the same rescaling changes its answer.
        Both halves are pinned, with the negative control, by
        tests/test_session_pure.py::test_beat_counts_are_not_a_fixed_tolerance_hit_rate.

        The correlation this docstring used to lead with ("r = −0.04 … −0.50 against the same
        durations") does not reproduce. Re-measured on the owner's FIVE recordings as the app opens
        them (the owner's saved start line restored where there is one, †): subject = the best lap;
        the segments longer than POINT_SPAN_M on the reference odometer; beat rate (beat / n)
        against the segment's mean duration over the composite laps; Spearman with tied ranks
        averaged; permutation p two-sided on Spearman, over 20,000 shuffles of the beat rates
        (seed 0). tests/test_measured_figures.py derives the sentence under the table from its cells
        and, given the footage, re-measures every cell.

        ⚠ STALE — NOT RE-MEASURABLE (T16). The table was measured before #335 changed corner
        matching, and #339 re-ran its footage check after #335: it no longer matched the app. The
        D24 and Sandown_09_05_2026 rows cannot be re-measured, because those recordings are no
        longer available. SD_30_08's row can, and re-measured on 2026-09-19 it is stale too: r
        −0.297, Spearman −0.335, permutation p 0.218, still not distinguishable from chance. Every
        cell and the verdict under the table are the record of that measurement, not what the app
        computes today.

        | recording      | n  | r      | Spearman | permutation p |
        |----------------|----|--------|----------|---------------|
        | D24 1 ch       | 23 | +0.058 | −0.021   | 0.926 |
        | D24 3 ch       | 23 | −0.326 | −0.361   | 0.094 |
        | Sandown ch 1 † | 15 | −0.056 | +0.082   | 0.772 |
        | Sandown 3 ch † | 15 | −0.249 | −0.239   | 0.389 |
        | SD_30_08 †     | 15 | −0.178 | −0.038   | 0.893 |

        None of the five is distinguishable from chance at p < 0.05, and the strongest r (D24 3 ch)
        explains 11 % of the variance in beat rate. The table this replaces (#213, before #300, on
        the loader's start lines) found one of five under 0.05, and was not measured the way it
        said: every row kept one POINT segment (a zero-width edge segment of the reference lap that
        reads 0 s on some laps and a sliver on others), its Spearman broke tied beat rates in sort
        order, and its SD_30_08 row was two corners of the 13 s pieces the loader cut from a 46 s
        lap until T13. Four of the five r are negative, which is what a real track produces — a
        short piece of road has less room to differ, so more laps land level with the subject — and
        because the statistic is provably invariant to scale, that correlation is a fact about the
        driving, not about the units. The rejected `hit_counts` had no such defence: its coupling
        came from a tolerance measured in seconds against segments of unequal length.

        It is also the question a driver is actually asking: have I been here before, or was that
        once? And both of its factors are printed as columns beside the row, with the product in
        the row's own tooltip, so the ranking is checkable by eye — the app's standing answer to a
        ranking whose factor a reader cannot see (stats_panel's note at the CORNERS table).

        It is deliberately measured against the SUBJECT lap rather than against the segment best.
        The gain a row shows is the distance from the subject to the best, and the honest
        achievability question about that gain is how routinely the subject's own time there is
        beaten — not how many laps landed inside an arbitrary window around a single minimum."""
        if lap_id not in self.lap_ids:
            return None
        row = self.times[self.lap_ids.index(lap_id)]
        out = []
        for j in range(len(self.bests)):
            adm = self.admitted[:, j]
            out.append((int(((self.times[:, j] <= row[j]) & adm).sum()), int(adm.sum())))
        return out

    def decomposition(self, lap_id: int) -> list[SegmentGain] | None:
        """`lap_id`'s gap to the composite, segment by segment, ranked most-actionable first
        (`SegmentGain.priority` = gain × beat/n). None when that lap did not contribute a row.

        Every segment appears, including the 0.00 s ones — a caller decides what is worth
        showing and must account for the rest, because the gains SUM to the headline gap and a
        top-N slice that does not say so is a table contradicting the tile above it.

        One exception, and it is a correctness exception rather than a display one: a segment the
        subject lap is not ADMITTED on is dropped. A projected window that is materially narrower
        or wider than the lap's own expected span has pushed time across the boundary into its
        neighbour, so that lap's `gain` there is measured against a time it never drove (and its
        neighbour's is inflated by the same amount). The pair still sums correctly — which is why
        the headline is safe — but neither number is advice. Observed on 7.4 % of the D24 0060
        pair's cells; see MAX_DONOR_SPAN_DEV."""
        gains = self.gains_vs(lap_id)
        beats = self.beat_counts(lap_id)
        if gains is None or beats is None:
            return None
        i = self.lap_ids.index(lap_id)
        rows = [SegmentGain(index=j, label=self.display_label(j), gain=gains[j],
                            beat=beats[j][0], n=beats[j][1], donor=self.donors[j],
                            ring_cid=self.ring_cid(j))
                for j in range(len(self.bests)) if self.admitted[i, j]]
        return sorted(rows, key=lambda r: -r.priority)


class CornerModel:
    """Corner detection + per-corner per-lap stats over Session-bound primitives.

    All inputs are Session-bound callables (Session owns the pacer side + its memoized lap sets /
    per-lap caches / active reference): `best_lap_id` / `valid_lap_ids` / `lap_has_dropout` are the
    memoized lap-set accessors; `lap_columns` / `lap_arrays` / `lap_time_dist` are the per-lap
    array fetches; `reference` returns the active cross-recording ReferenceLap (or None). The corner
    math is just numpy on those arrays. `reference_id` is Session.REFERENCE_ID, the sentinel the
    reference stats are parked under.
    """

    def __init__(self, *, reference_id: int,
                 best_lap_id: Callable[[], int | None],
                 valid_lap_ids: Callable[[], list[int]],
                 lap_has_dropout: Callable[[int], bool],
                 lap_columns: Callable[[int], tuple],
                 lap_arrays: Callable[[int], tuple],
                 lap_time_dist: Callable[[int], tuple | None],
                 reference: Callable[[], object | None]):
        self._reference_id = reference_id
        self._best_lap_id = best_lap_id
        self._valid_lap_ids = valid_lap_ids
        self._lap_has_dropout = lap_has_dropout
        self._lap_columns = lap_columns
        self._lap_arrays = lap_arrays
        self._lap_time_dist = lap_time_dist
        self._reference = reference
        self._basis_cache: object = _UNSET  # (corners, total_ref) or None
        self._stats_cache: dict[int, list[corners.CornerStat]] = {}  # per-lap stats + the reference's own under reference_id
        self._bests_cache: object = _UNSET  # per-corner session-best time
        self._segment_bests_cache: object = _UNSET  # the ideal-lap segment composite
        # Per-(lap, total_lap) monotone warp onto the reference odometer — see lap_alignment.
        self._align_cache: dict[tuple[int, float], object] = {}
        self._geometry_cache: object = _UNSET   # corners.SessionGeometry or None — see geometry()
        self._shift_cache: dict[int, object] = {}  # lap id -> its fitted rigid shift

    def invalidate(self) -> None:
        """Drop EVERY corner cache — called from Session.set_timing_lines (the single
        re-segmentation point): the corner set is detected on + projected through the
        segmentation, so all of them are stale after a timing-line change."""
        self._basis_cache = _UNSET
        self._stats_cache.clear()
        self._bests_cache = _UNSET
        self._segment_bests_cache = _UNSET
        self._align_cache.clear()
        self._geometry_cache = _UNSET
        self._shift_cache.clear()

    def invalidate_stats(self) -> None:
        """Drop ONLY the per-lap stats (not the corner detection) — called from
        Session.set_reference_session / clear_reference: the per-corner Δ baseline switched
        (best lap <-> reference lap), so every cached per-lap stat delta is stale, but the
        corner windows themselves are unchanged. Recomputed lazily against the new baseline.

        The warp memo goes with them. It does NOT depend on the Δ baseline (it is built from
        `basis()` + the LOCAL best lap's trace, neither of which a reference load moves), so
        keeping it here would be sound — but dropping it makes its lifetime a strict subset of
        `_stats_cache`'s, which is the invariant this file already documents and tests. The win
        is entirely WITHIN one refresh (one derivation instead of nine), so the extra clearing
        costs nothing measurable and removes a whole class of staleness argument.

        The session GEOMETRY memo goes with it for the same reason. Like the warp it does not
        depend on the Δ baseline — it is fitted from the clean laps' traces and the local best
        lap's — but tying its lifetime to `_stats_cache`'s keeps one invalidation story instead
        of two, and it is refitted once per refresh at worst."""
        self._stats_cache.clear()
        self._align_cache.clear()
        self._geometry_cache = _UNSET
        self._shift_cache.clear()

    # -------------------------------------------------------- spatial traces for the per-lap warp
    def _best_trace(self) -> tuple | None:
        """The best (reference) lap's local-frame trace (xs, ys, cum) — the spatial anchor side of
        the per-lap warp (corners.project_boundaries). None when there is no usable best
        lap. The reference-odometer corner windows are expressed in this lap's frame, so it is the
        fixed half of every (ref, comparison) trace pair."""
        best = self._best_lap_id()
        if best is None:
            return None
        _t, xs, ys, _v, cum = self._lap_columns(best)
        if len(cum) < 2 or float(cum[-1]) <= 0:
            return None
        return xs, ys, cum

    def _lap_traces(self, lap_id: int, ref_trace: tuple | None) -> tuple | None:
        """The (ref_xs, ref_ys, ref_cum, lap_xs, lap_ys, lap_cum) trace pair the spatial
        alignment needs to map this session's corner windows onto `lap_id`. None (→ this lap
        keeps the normalized projection) when either trace is degenerate. A reference-lap
        (cross-recording) projection has no local trace pair, so it stays normalized."""
        if ref_trace is None:
            return None
        _t, xs, ys, _v, cum = self._lap_columns(lap_id)
        if len(cum) < 2 or float(cum[-1]) <= 0:
            return None
        return (*ref_trace, xs, ys, cum)

    # ------------------------------------------------- the session's geometry (receiver de-drift)
    def geometry(self):
        """This session's `corners.SessionGeometry` — the consensus line of its CLEAN laps plus
        each lap's rigid receiver shift against it — or None when there is nothing to fit one
        from (no usable best lap, fewer than `corners.DRIFT_MIN_LAPS` clean laps, too few stations
        answering). Memoized with the warps and dropped by the same two invalidations.

        WHAT IT IS FOR is in `corners.SessionGeometry`: a consumer receiver's position error is
        nearly constant over one lap, so each lap's whole trace sits displaced by one vector, and
        the spatial match's 3 m gate was being spent on that instead of on the driving. It is
        fitted from the clean laps only (`_clean_lap_ids` — the same set every "best" here is
        drawn from) so that one dropout lap's reconstructed trace cannot tilt the consensus; a lap
        outside that set is still measured against it, on demand, by `lap_shift`."""
        if self._geometry_cache is not _UNSET:
            return self._geometry_cache
        self._geometry_cache = None
        ref_trace = self._best_trace()
        if ref_trace is not None:
            traces = {}
            for lid in self._clean_lap_ids():
                _t, xs, ys, _v, cum = self._lap_columns(lid)
                if len(cum) >= 2 and float(cum[-1]) > 0:
                    traces[int(lid)] = (xs, ys, cum)
            self._geometry_cache = corners.session_geometry(ref_trace, traces)
        return self._geometry_cache

    def lap_shift(self, lap_id: int) -> tuple[float, float]:
        """The rigid translation to remove from `lap_id` before matching it against the reference
        lap — `SessionGeometry.relative_shift`, so the reference lap gets exactly (0, 0) against
        itself. (0, 0) when there is no geometry, or when the lap has no usable trace.

        A lap that was not in the consensus set (a GPS-dropout lap: excluded from every "best",
        still drawn) is FITTED HERE against that consensus rather than left uncorrected, so one
        session has one frame. Memoized per lap alongside the warps."""
        geom = self.geometry()
        best = self._best_lap_id()
        if geom is None or best is None:
            return 0.0, 0.0
        lap_id = int(lap_id)
        got = self._shift_cache.get(lap_id, _UNSET)
        if got is _UNSET:
            got = geom.shift.get(lap_id)
            if got is None:
                _t, xs, ys, _v, cum = self._lap_columns(lap_id)
                got = (geom.fit(xs, ys, cum)[0] if len(cum) >= 2 and float(cum[-1]) > 0
                       else np.zeros(2))
            self._shift_cache[lap_id] = got
        return geom.relative_shift(got, best)

    def lap_alignment(self, lap_id: int, total_lap: float) -> object | None:
        """ONE lap's monotone warp onto the reference (best) lap's odometer — the thing every
        corner-window projection in the app is a read of — MEMOIZED per (lap, total_lap).
        Pass the result as `alignment=` to `corners.project_boundaries` / `segment_times` /
        `lap_corner_stats`.

        NONE IS A LEGAL VALUE, and it means "no warp could be built for this lap" — never "this
        lap drifted too little to need one". There are exactly three causes:
          * NO CORNER BASIS — no usable best lap, or no corner detected in the session (`basis`);
          * NO USABLE TRACE PAIR (`_lap_traces`) — this lap's or the best lap's local trace is
            degenerate, and a cross-recording reference lap has no local pair at all;
          * NO SPATIAL MATCH SURVIVED anywhere on the lap (`corners.lap_alignment`): every
            boundary failed the heading / SPATIAL_MATCH_MAX_M gates, leaving the two timing-line
            anchors, which ARE the normalized map.
        In all three the caller keeps the normalized projection — `project_boundaries` returns it
        verbatim — which is why None is passed through rather than raised on.

        This used to say None meant a lap whose own line length drifted under
        `corners.NORMALIZED_DRIFT_MAX`. #300 deleted that constant, so every lap with a trace pair
        has been warped since and the sentence was false from that commit. Measured on the owner's
        recordings, None is not reached at all: 0 of 38 laps (D24 0060 pair) and 0 of 65 (0062),
        carrying 12-22 and 22-22 matched interior knots of 24 corner boundaries (4-22 and 20-22
        before the session geometry below de-drifted them).
        `tests/test_corner_alignment_memo.py` drives all three causes and guards the wording.

        THE MATCH RUNS THROUGH THE SESSION'S GEOMETRY (M7). The comparison lap's trace is moved by
        `lap_shift` and the anchor sideways by `corners.anchor_offsets`, so the 3 m gate is spent on
        the two lines rather than on the receiver's own bias. On the D24 0060 pair that takes the
        interior boundaries matched from 61.8 % to 95.1 % and `lap_corner_resolved` from 220 to 422
        of 456 cells; on 0062, from 99.7 % to 100 % and 776 to 780 of 780.

        WHY THIS EXISTS. The warp is built from the WHOLE corner partition, so it is the same
        object for every window of a lap — but nine independent call paths each derived it for
        themselves. Measured on the real D24 0060 pair when a 0.5 % drift threshold still kept 22 of its
        38 laps off the spatial path, ONE `stats_view.refresh` ran `corners._spatial_matches`
        **144 times** — 9x per lap that needed it — against the 16 the memo cost. Every lap is
        warped now, so the memo costs one match per lap: 38 on the pair and 65 on 0062, which is
        what makes that refresh 30.4 → 40.0 ms and 42.5 → 65.7 ms of CPU. (Call counts
        are deterministic and are the honest evidence here; the wall/CPU figures in the PR were
        taken as min-of-9 CPU time because this box runs at load average 40-90 and one cProfile
        pass attributed the same work 97.7 ms in one run and 220.4 ms in another.)

        THE KEY IS THE WHOLE DEPENDENCY SET, which is why it is safe:
          * `lap_id` → the comparison lap's trace (`_lap_columns`, itself invalidated on
            re-segment) — and `total_lap` with it, keyed explicitly so a caller measuring the lap
            through a different accessor can never silently read a warp built for another length;
          * the reference half (`basis()`'s corner partition + total, `_best_trace()`) is NOT in
            the key because it is not per-lap — it is covered by INVALIDATION instead: both of the
            two events that can move it (`set_timing_lines` → `invalidate`, a reference change →
            `invalidate_stats`) clear this cache. There is no third writer of `Session._best_cache`.
        A stale warp after a start-line drag would be a far worse bug than the latency, so
        `tests/test_corner_alignment_memo.py` drives the drag / sector edit / undo / reference
        load and asserts the memoized answer equals a from-scratch recompute after each."""
        key = (int(lap_id), float(total_lap))
        got = self._align_cache.get(key, _UNSET)
        if got is not _UNSET:
            return got
        basis = self.basis()
        if basis is None or not basis[0]:
            return None
        corner_list, total_ref = basis
        # The warp is fitted to the WHOLE partition (corners.project_boundaries' `frame`), so a
        # caller asking about one corner gets the alignment the whole-partition callers use.
        frame = [b for c in corner_list for b in (float(c.enter), float(c.exit))]
        ref_trace = self._best_trace()
        # The receiver's rigid bias is taken out of the comparison lap, and the reference lap's own
        # NON-RIGID deviation from the session's consensus out of the anchor (corners.anchor_offsets
        # — sideways only, so the odometer frame is untouched). THE REFERENCE LAP IS EXEMPT FROM
        # BOTH: its odometer IS the frame, so it must keep matching its own trace exactly, and a
        # sideways anchor would put its own boundaries a metre or more from their definition — on
        # 0060, past the very gate this is widening (its residual runs to 4.9 m at the C8 exit) —
        # and `lap_corner_resolved(best)` would start reading False.
        shift, anchor = (0.0, 0.0), None
        if ref_trace is not None and lap_id != self._best_lap_id():
            shift = self.lap_shift(lap_id)
            anchor = corners.anchor_offsets(frame, self.geometry(), self._best_lap_id(), *ref_trace)
        align = corners.lap_alignment(frame, total_ref, float(total_lap),
                                      traces=self._lap_traces(lap_id, ref_trace),
                                      lap_shift=shift, anchor_offset=anchor)
        self._align_cache[key] = align
        return align

    # ------------------------------------------------------------------ basis + corners
    def basis(self) -> tuple[list[corners.Corner], float] | None:
        """The cached (corner list, reference total distance) pair, or None when there is no
        usable best lap. The reference total is the best lap's odometer length — the basis
        the corner windows (and the delta plot's distance axis) are expressed in."""
        if self._basis_cache is not _UNSET:
            return self._basis_cache
        self._basis_cache = None
        best = self._best_lap_id()
        if best is not None:
            _t, _xs, _ys, _v, cum_best = self._lap_columns(best)
            if len(cum_best) >= 8 and float(cum_best[-1]) > 0:
                total_ref = float(cum_best[-1])
                # The median curvature profile pools the session's clean laps (valid, no GPS
                # dropout), best lap always included — `_clean_lap_ids`, which is now the one
                # spelling of that rule rather than the first of three.
                ids = self._clean_lap_ids()
                traces = []
                for lid in ids:
                    _lt, xs, ys, _lv, cum = self._lap_columns(lid)
                    traces.append((xs, ys, cum))
                d_grid, kappa = corners.pooled_curvature(traces, total_ref)
                self._basis_cache = (corners.detect_corners(d_grid, kappa), total_ref)
        return self._basis_cache

    def corner_list(self) -> list[corners.Corner]:
        """The detected corners (C1… in track order) in best-lap odometer metres. [] when
        no best lap exists. Computed once per segmentation (see basis)."""
        basis = self.basis()
        return basis[0] if basis is not None else []

    # ------------------------------------------------------------------ per-lap stats
    def reference_corner_stats(self) -> list[corners.CornerStat] | None:
        """The cross-recording reference lap's per-corner stats projected onto THIS session's
        corner windows (the same normalized-distance projection any local lap uses), or None
        when no reference is loaded. Cached under the reference sentinel key; invalidated when
        the reference or the segmentation changes (invalidate_stats / invalidate)."""
        ref = self._reference()
        if ref is None:
            return None
        got = self._stats_cache.get(self._reference_id)
        if got is not None:
            return got
        basis = self.basis()
        if basis is None or not basis[0]:
            return None
        corner_list, total_ref = basis
        dist, speed_kmh, elapsed = ref.arrays()
        if len(dist) < 2 or float(dist[-1]) <= 0:
            return None
        # ref=None: the reference IS the baseline (self-deltas 0).
        stats = corners.lap_corner_stats(corner_list, total_ref, dist, speed_kmh, elapsed,
                                         ref=None)
        self._stats_cache[self._reference_id] = stats
        return stats

    def lap_corner_stats(self, lap_id: int) -> list[corners.CornerStat]:
        """Per-corner metrics for one lap (time-in-corner, apex/entry/exit speeds, deltas vs
        the baseline's same corner). [] for a degenerate lap or when no corners were detected.
        Cached per lap; cleared on re-segment (and on a reference change via invalidate_stats).

        The Δ baseline is the local best lap normally, or the CROSS-RECORDING reference lap's
        projected corner stats when one is loaded (F7)."""
        got = self._stats_cache.get(lap_id)
        if got is not None:
            return got
        basis = self.basis()
        best = self._best_lap_id()
        if basis is None or not basis[0] or best is None:
            return []
        corner_list, total_ref = basis
        dist, speed_kmh, elapsed = self._lap_arrays(lap_id)
        if len(dist) < 2 or float(dist[-1]) <= 0:
            return []
        ref_stats = self.reference_corner_stats()
        if ref_stats is not None:
            ref = ref_stats
        else:
            ref = self.lap_corner_stats(best) if lap_id != best else None
        # Spatial traces: the best lap (the corner-window reference frame) + this lap.
        # The best lap itself projects onto its OWN odometer (zero drift → identity), so its trace
        # pair is harmless; a degenerate trace → None → normalized projection (unchanged).
        traces = self._lap_traces(lap_id, self._best_trace())
        stats = corners.lap_corner_stats(corner_list, total_ref, dist, speed_kmh, elapsed,
                                         ref=ref or None, traces=traces,
                                         alignment=self.lap_alignment(lap_id, float(dist[-1])))
        self._stats_cache[lap_id] = stats
        return stats

    def lap_corner_resolved(self, lap_id: int) -> list[bool]:
        """Per corner (track order, aligned to `lap_corner_stats`): were BOTH edges of this lap's
        window read off a direct spatial match, rather than interpolated between neighbouring ones?
        [] exactly where `lap_corner_stats` is [] (no basis / degenerate lap); all False when the
        lap has no warp at all (`lap_alignment` is None, so every edge is the normalized fraction).

        It is a read of the SAME memoized warp `lap_corner_stats` used: a boundary is resolved iff
        it is one of that warp's knots (the two timing-line anchors count — they are the same
        physical point on every lap by definition). A match that was dropped for crossing its
        neighbour is not a knot and is therefore not resolved, which is the fail-closed direction.

        WHAT IT SEPARATES, measured against an independent gate-crossing time per cell on the two
        D24 recordings before #335 changed corner matching (full table, stale since, in
        `stats.CornerMatrix`): resolved cells agree with it to a
        median 0.004 s (max 0.024 s); cells with an interpolated edge disagree by a median 0.219 s
        (max 0.886 s) on the 0060 pair, where 236 of its 456 cells are unresolved (4 of 780 on 0062).
        THE ONE RULE for which cells a cross-lap corner statistic may count. The CORNERS BY LAP
        grid marks by it; since C4 the CORNERS table (`Session.corner_report`), its Best's
        provenance, the Corners page ★ (`corner_session_bests`) and the phase split
        (`Session.phase_report`) count by it; the STRAIGHTS table and the ideal lap
        (`segment_bests`) by the same rule one and two levels finer (`lap_edge_resolved`,
        `lap_segment_resolved`); and since C5 coaching (`Session.coaching_opportunities` and the σ
        it reads from `Session.corner_consistency`) and the BRAKING table's brake points
        (`Session._brake_rows`) as well.

        TWO SURFACES STILL COUNT EVERY CELL ON PURPOSE, each with its measurement. The per-lap CSV
        writes every value and DISCLOSES which are interpolated instead
        (`export_data.INTERPOLATED_COLUMN` — it is an external format, and a reader diffing last
        week's file needs the rows to stay put). The COASTING table's places
        (`Session.coast_report`) keep every cell because masking them would break the column's own
        stated invariant — "s / lap … adds up to the MEAN coasting per lap" — to reorder places the
        table already prints as tied: on the D24 0060 pair the leader is unchanged, the per-lap
        total would read 2.883 s where the laps coasted 2.812, and the sum-preserving alternative
        (whole laps only) throws away 16 of 38 laps."""
        basis = self.basis()
        if basis is None or not basis[0] or self._best_lap_id() is None:
            return []
        corner_list, _total_ref = basis
        dist, _speed_kmh, _elapsed = self._lap_arrays(lap_id)
        if len(dist) < 2 or float(dist[-1]) <= 0:
            return []
        align = self.lap_alignment(lap_id, float(dist[-1]))
        if align is None:
            return [False] * len(corner_list)
        knots = align[0]
        edges = np.asarray([b for c in corner_list for b in (float(c.enter), float(c.exit))], float)
        on_knot = np.isin(edges, knots)
        return [bool(on_knot[2 * i] and on_knot[2 * i + 1]) for i in range(len(corner_list))]

    def lap_edge_resolved(self, lap_id: int) -> list[bool]:
        """Per corner EDGE, interleaved [C1 enter, C1 exit, C2 enter, …] (aligned to the corner
        partition): was it read off a direct spatial match — a knot of this lap's warp — rather
        than interpolated? The same read of the same memoized warp as `lap_corner_resolved`, one
        level finer, and `lap_corner_resolved(lap)[k]` is exactly `edges[2k] and edges[2k + 1]`.
        [] where that is []; all False without a warp.

        WHY AN EDGE AND NOT ONLY A CORNER. A time, an apex or a grip figure is measured OVER a
        window, so it needs both edges. A SPEED is read AT one edge — the Corners page's Entry and
        Exit columns, and the STRAIGHTS table's trap speed (the next corner's entry) and exit Δ
        (the previous corner's exit) — so it needs only that one. Measured against the Doppler speed
        at an independent line crossing on each edge, over the D24 0060 pair's 38 laps (C4):

            entry speed   matched 272   |Δ| median 0.014 km/h, p90 0.10, max 0.29
                          interpolated 155   median 1.57 km/h, p90 4.70, max 11.5
            exit speed    matched 297   |Δ| median 0.015 km/h, p90 0.09, max 0.66
                          interpolated 156   median 1.10 km/h, p90 4.42, max 7.1

        (0062: 1,447 matched edges at a median 0.004 km/h; 4 interpolated.) Holding a matched entry
        to its interpolated exit would throw away a speed that is good to 0.014 km/h, so the rule
        is one rule applied at the granularity each quantity is read at."""
        basis = self.basis()
        if basis is None or not basis[0] or self._best_lap_id() is None:
            return []
        corner_list, _total_ref = basis
        dist, _speed_kmh, _elapsed = self._lap_arrays(lap_id)
        if len(dist) < 2 or float(dist[-1]) <= 0:
            return []
        align = self.lap_alignment(lap_id, float(dist[-1]))
        if align is None:
            return [False] * (2 * len(corner_list))
        edges = np.asarray([b for c in corner_list for b in (float(c.enter), float(c.exit))], float)
        return [bool(x) for x in np.isin(edges, align[0])]

    def lap_segment_resolved(self, lap_id: int) -> list[bool]:
        """`lap_edge_resolved` read on the 2N+1 pieces of the corner/straight PARTITION rather
        than on the corner windows: piece j runs between partition edges j and j+1, so it is
        resolved iff both of those were matched on track. [] where `lap_edge_resolved` is [].

        The two end pieces are bounded by the TIMING LINE, which is the warp's fixed anchor — the
        same physical point on every lap by definition — so "S/F → C1" needs only C1's entry and
        "C{N} → finish" only C{N}'s exit. This is the same one rule at the granularity a SEGMENT
        time is read at, and it is what `segment_bests` (the ideal lap) and the STRAIGHTS table
        count by; `stats.straights_report` derives the identical mask from the edges itself,
        because it is handed the edges and not the model."""
        edges = self.lap_edge_resolved(lap_id)
        if not edges:
            return []
        e = np.asarray(edges, bool)
        return [bool(x) for x in (np.concatenate(([True], e)) & np.concatenate((e, [True])))]

    def corner_session_bests(self) -> list[float | None]:
        """Per-corner session-best time-in-corner over the CLEAN laps (`_clean_lap_ids`) — the
        purple-cell convention, and now actually matching the per-sector session bests its own
        docstring has always claimed parity with. [] when no corners. Cached; cleared on
        re-segment.

        C4: only cells matched on track at both edges (`lap_corner_resolved`) can be the best — the
        rule the Stats page's CORNERS table and CORNERS BY LAP grid count by. An interpolated window
        was a median 0.22 s off an independent crossing time on the D24 0060 pair before #335
        (0.004 s for a matched one), and a MINIMUM is the statistic that error favours: counting every cell, the
        Corners page ★ sat on an interpolated cell in C2, C6 and C8, whose crossing times were 0.53,
        0.41 and 0.29 s slower than the starred time. None for a corner no clean lap matched: no
        cell there can be starred, rather than the least imprecise one being starred. (The best lap
        is matched against its own line, and resolves every corner on both D24 recordings.)

        §4.1: this ran on the raw valid set while `session_best_splits`, `best_lap_id`,
        `best_rolling_lap` and the ideal composite all excluded GPS-dropout laps. A dropout lap's
        distance is speed-integral reconstructed, so its corner boundaries — and therefore the
        time between them — are exactly what must not be allowed to win a corner. Latent on both
        fixtures (neither has a dropout lap), which is why it survived: nothing on screen was
        wrong until a recording with one arrived, and then only one column of it."""
        if self._bests_cache is not _UNSET:
            return self._bests_cache
        per_lap = [(self.lap_corner_stats(i), self.lap_corner_resolved(i))
                   for i in self._clean_lap_ids()]
        per_lap = [(st, res) for st, res in per_lap if st]
        n = len(self.corner_list())
        bests: list[float | None] = []
        for i in range(n):
            counted = [st[i].time for st, res in per_lap if res[i]]
            bests.append(min(counted) if counted else None)
        self._bests_cache = bests if per_lap and n else []
        return self._bests_cache

    # ------------------------------------------------------------------ ideal lap (D1)
    def _clean_lap_ids(self) -> list[int]:
        """The laps ANY "best" in this service may be drawn from: `Session.consistency_lap_ids()`
        — VALID and DROPOUT-FREE, the same set every consistency statistic runs on. A dropout
        lap's distance is speed-integral reconstructed, so its segment boundaries (and therefore
        its segment TIMES) are exactly the ones that must not be allowed to win a segment.

        The BEST lap is appended when the dropout rule excluded it — the same guarantee
        `basis()` makes for the detection profile. It is what keeps `total <= best lap time`
        true in the degenerate session where every valid lap is dropout-flagged and
        `best_candidate_ids` fell back to the flagged set.

        THREE SITES SPELLED THIS RULE OUT AND ONE OF THEM DID NOT (§4.1): the detection profile
        and the ideal composite both filtered dropouts and appended the best lap; the per-corner
        session bests — the purple cells, whose own docstring claimed parity with the per-sector
        bests — ran on the raw valid set. One name, one rule, and the odd one out cannot drift
        back."""
        ids = [i for i in self._valid_lap_ids() if not self._lap_has_dropout(i)]
        best = self._best_lap_id()
        if best is not None and best not in ids:
            ids.append(best)
        return ids

    def segment_bests(self) -> SegmentBests | None:
        """The IDEAL LAP: the per-segment minimum of the corner/straight partition over the
        clean laps (`_clean_lap_ids`), with its donors, its per-lap matrix and the partition
        edges. None when there is no corner partition to composite on — no basis, or no corner
        detected, in which case the "partition" is the whole lap and its minimum is just the best
        lap time. That degenerate value is NOT returned dressed as an ideal: callers hide it,
        following the precedent at stats_panel/export_data.

        Each lap's times come from `corners.segment_times`, which asserts they sum exactly to
        that lap's time. That sum is NECESSARY for a cross-lap composite and not sufficient: it
        holds just as well when a projected window is short and its time has moved into the
        neighbour, which is exactly the defect `corners.project_boundaries` documents. A lap is
        refused any segment whose projected span is not comparable to its own expected span
        (MAX_DONOR_SPAN_DEV) — that is the sufficiency half.

        C5: AND IT MUST HAVE BEEN MEASURED. A segment whose boundaries this lap did not match on
        track (`lap_segment_resolved`) is timed between two guesses, a median 0.22 s off an
        independent line crossing against 0.004 s for a matched one (D24, before #335) — and the composite is a
        MINIMUM, the statistic that error favours, so it is the same argument
        `corner_session_bests` makes for the purple cells. Measured on the D24 0060 pair (38 laps,
        92.5 % of cells resolved after #335) the composite's winning donor sat on an interpolated
        boundary in 5 of its 25 segments and the ideal read **65.637 s where the matched cells say
        65.864 s** — 0.226 s of ideal that nobody drove, 0.133 s of it in C7→C8 alone. The other
        four recordings the `IdealSample` table publishes do not move by so much as a millisecond
        (0062 one and three chapters resolve 100 % of their cells; Sandown and SD_30_08 96.4-98.8 %,
        and none of their unresolved cells was winning anything).

        `admitted` KEEPS ITS OWN MEANING and is not merged with this. It is published as a count
        (`SegmentGain.n`, `beat_counts`) and the scale-free proof in `beat_counts` rests on it
        being a function of the SPANS alone; resolution is likewise not a function of the times,
        so folding it in would keep that proof — but it would move 22 of the 25 beat counts on the
        0060 pair, which is a different statistic on a separately published table (`beat_counts`'
        own). The two masks are therefore both carried, and only the MINIMUM — the one an
        interpolated cell can win by being wrong — takes both. That the plan's denominator still
        counts interpolated cells is a known gap, recorded here rather than fixed in passing.

        Cached; cleared on re-segment (`invalidate`)."""
        if self._segment_bests_cache is not _UNSET:
            return self._segment_bests_cache
        self._segment_bests_cache = None
        basis = self.basis()
        if basis is None or not basis[0]:
            return None
        corner_list, total_ref = basis

        # Partition edges on the REFERENCE odometer: start, each corner's enter/exit, finish.
        # This is the shared frame every lap's own edges are projected FROM, so it is where the
        # composite curve's x lives. (Each lap's own edges are `corners.project_boundaries` of
        # these onto its odometer — computed per lap inside segment_times.)
        ref_edges = [0.0]
        for c in corner_list:
            ref_edges.extend((float(c.enter), float(c.exit)))
        ref_edges.append(float(total_ref))
        ref_span = np.diff(np.asarray(ref_edges, float))
        # A POINT segment (corner starts on the line, or two corners nearly touch) carries ~0 s on
        # every lap; nobody can collapse it further, so every lap is admitted there. Sub-resolution
        # segments are deliberately NOT exempt — see MAX_DONOR_SPAN_DEV's sub-resolution paragraph
        # for the two exemptions that were implemented, measured and reverted.
        is_point = ref_span <= POINT_SPAN_M

        labels = ["start"]
        for i, c in enumerate(corner_list):
            labels.append(c.label)
            nxt = corner_list[i + 1].label if i + 1 < len(corner_list) else "finish"
            labels.append(f"{c.label}-{nxt}")

        ref_trace = self._best_trace()
        rows, spans, edges, lap_ids, expected, resolved = [], [], [], [], [], []
        for lid in self._clean_lap_ids():
            dist, _speed_kmh, elapsed = self._lap_arrays(lid)
            if len(dist) < 2 or float(dist[-1]) <= 0:
                continue
            traces = self._lap_traces(lid, ref_trace)
            total_lap = float(dist[-1])
            align = self.lap_alignment(lid, total_lap)
            rows.append(corners.segment_times(corner_list, total_ref, dist, elapsed, traces,
                                              align))
            # The same edges segment_times interpolates at — the admission test's input, and the
            # donor's own odometer frame for the ideal curve. project_boundaries is the public
            # half of corners._window_edges; the two constant endpoints (0, total_lap) are never
            # projected, so they cannot collapse. `ref_edges[1:-1]` IS the memo's frame, so the
            # shared warp reads exactly the boundaries it was fitted to.
            interior = corners.project_boundaries(ref_edges[1:-1], total_ref, total_lap,
                                                  traces=traces, alignment=align)
            lap_edges = np.concatenate(([0.0], interior, [total_lap]))
            edges.append(lap_edges)
            spans.append(np.diff(lap_edges))
            # The width each reference segment has on THIS lap under its uniform line-length
            # scaling — what the projected span is compared against (see MAX_DONOR_SPAN_DEV).
            expected.append(ref_span * (total_lap / total_ref) if total_ref > 0 else ref_span)
            resolved.append(self.lap_segment_resolved(lid))
            lap_ids.append(lid)
        if not rows:
            return None

        times = np.asarray(rows, float)
        edges = np.asarray(edges, float)
        expected = np.asarray(expected, float)
        admitted = (is_point[None, :]
                    | (np.abs(np.asarray(spans, float) - expected)
                       <= MAX_DONOR_SPAN_DEV * expected))
        # A segment no lap is admitted on cannot happen while a point segment admits everyone,
        # but a min over an empty set would be inf — fall back to the whole column rather than
        # poison the total.
        for j in range(times.shape[1]):
            if not admitted[:, j].any():
                admitted[:, j] = True
        # C5: …and of those, the cells whose two boundaries this lap actually MATCHED on track.
        may_donate = admitted & np.asarray(resolved, bool)
        for j in range(times.shape[1]):
            if not may_donate[:, j].any():
                may_donate[:, j] = admitted[:, j]
        masked = np.where(may_donate, times, np.inf)
        bests = [float(v) for v in masked.min(axis=0)]
        donors: list[int | None] = [
            None if bests[j] <= 0.0 else lap_ids[int(masked[:, j].argmin())]
            for j in range(times.shape[1])
        ]
        self._segment_bests_cache = SegmentBests(
            labels=labels, cids=[int(c.cid) for c in corner_list], lap_ids=lap_ids,
            times=times, admitted=admitted, resolved=np.asarray(resolved, bool), bests=bests,
            donors=donors,
            s_edges=[e / total_ref for e in ref_edges] if total_ref > 0 else ref_edges,
            donor_span=[(0.0, 0.0) if donors[j] is None else
                        (float(edges[lap_ids.index(donors[j]), j]),
                         float(edges[lap_ids.index(donors[j]), j + 1]))
                        for j in range(times.shape[1])],
        )
        return self._segment_bests_cache

    def ideal_elapsed(self, s_grid) -> np.ndarray | None:
        """The IDEAL LAP's elapsed-time curve sampled at normalized distances `s_grid` ∈ [0,1] —
        the y of `Session.ideal_lap_elapsed`. None when there is no composite.

        Inside each segment the curve follows THE DONOR'S OWN PACE, not a straight line: the ideal
        lap is a real drive (this lap's C3, that lap's back straight), so its shape through a
        segment is the shape its donor drove. The donor's segment time is already exactly the
        segment's best, so replaying its profile lands on the right value at both edges with no
        rescaling — the curve is exact at the edges and honest in between.

        WHY IT MATTERS, measured in #211: drawing each segment as a straight line in (distance,
        time) instead sent `Session.delta_to_ideal_at` to −0.87 s on 18.4 % of samples on the
        Sandown recording — a 163 m / 9.9 s corner is nowhere near constant pace, so the line is
        nowhere near anything anybody drove — and following the donor cut that to −0.159 s. After
        #300 warped every lap the worst excursion is **−0.280 s**, on SD_30_08 on the loader's own
        start line (−0.246 s on the owner's saved one; 4.52 % of samples on Sandown chapter 1 and
        12.01 % on SD_30_08 are negative on the loader's lines — the per-recording table is in
        `theme.format_ideal_run`'s note), which is then a real "you were up on the ideal through
        here" rather than an artefact of the drawing.

        Those figures read −0.052 s / "under 1 %" until #211 redid this sweep: the original was
        measured on a fixture set that substituted Sandown chapter **3** — one valid lap, so the
        excursion is identically zero — for chapter 1, which is where the real floor is. See
        `Session.delta_to_ideal` for the full note; the lesson is that the sweep passed because its
        fixture could not express the property, not because the bound held."""
        sb = self.segment_bests()
        if sb is None:
            return None
        s = np.asarray(s_grid, float)
        s_edges = np.asarray(sb.s_edges, float)
        cum = sb.cumulative()
        n_seg = len(sb.bests)
        # Each grid point belongs to the segment whose [s_edges[j], s_edges[j+1]) contains it.
        idx = np.clip(np.searchsorted(s_edges, s, side="right") - 1, 0, n_seg - 1)
        out = cum[idx].copy()
        for j in range(n_seg):
            here = idx == j
            lo_s, hi_s = float(s_edges[j]), float(s_edges[j + 1])
            donor = sb.donors[j]
            # A point segment (or a segment nobody donates) contributes its whole time at once:
            # the curve steps at the edge and there is no interior to shape.
            if not here.any() or donor is None or hi_s <= lo_s:
                continue
            d_lo, d_hi = sb.donor_span[j]
            if d_hi <= d_lo:
                continue
            dist, _speed_kmh, elapsed = self._lap_arrays(donor)
            frac = (s[here] - lo_s) / (hi_s - lo_s)
            d_on_donor = d_lo + frac * (d_hi - d_lo)
            t0 = float(np.interp(d_lo, dist, elapsed))
            out[here] = cum[j] + (np.interp(d_on_donor, dist, elapsed) - t0)
        return out

    # ------------------------------------------------------------------ map / seek glue
    def corner_map_markers(self) -> list[tuple[str, float, float, int]]:
        """(label, x, y, direction) per corner — the apex position in LOCAL metres on the
        best lap's trace, for the map's corner labels. [] when no corners/best lap."""
        basis = self.basis()
        best = self._best_lap_id()
        if basis is None or not basis[0] or best is None:
            return []
        corner_list, _total_ref = basis
        _t, xs, ys, _v, cum = self._lap_columns(best)
        apexes = np.asarray([c.apex for c in corner_list])
        mx = np.interp(apexes, cum, xs)
        my = np.interp(apexes, cum, ys)
        return [(c.label, float(mx[i]), float(my[i]), c.direction)
                for i, c in enumerate(corner_list)]

    def corner_entry_media_time(self, lap_id: int, cid: int) -> float | None:
        """TELEMETRY-clock time (s) `lap_id` enters corner `cid` — the jump-to seek target, which
        the player crosses to the media clock itself; the name predates the second clock. Projects
        the corner's enter point onto this lap's odometer and reads elapsed there. None if
        unknown/degenerate. Absolute (lap start + elapsed).

        Goes through the SAME spatial alignment as its siblings — since the memo it now reads
        (`lap_alignment`) is shared, the warp is LITERALLY the one `lap_corner_stats` used, not
        merely one built from the same frame. On
        a drifted lap the bare normalized fraction it used before landed the seek up to ~12 m from
        the corner entry the Corners table was pointing at; measured move on the D24 0060 pair,
        0.373 s.

        `coaching._win` — which scaled a corner window by `lap_total / corner_dist_total` with no
        alignment and no traces, so a coaching row could carry a warp-derived phase triple beside
        a normalized-frame reason — has since moved onto this memo too (`coaching._project_window`),
        which leaves every CORNER-WINDOW projection on one warp.

        One normalized projection remains, in the other direction and outside this family:
        `Session._brake_rows` maps each lap's brake ONSETS back into the reference odometer by
        `× ref_total/lap_total` (studio/session.py) for the Stats ▸ BRAKING table and the "brake
        ~N m later" hint. It projects samples, not windows, and is untouched here."""
        basis = self.basis()
        if basis is None or not basis[0]:
            return None
        corner_list, total_ref = basis
        corner = next((c for c in corner_list if c.cid == cid), None)
        if corner is None:
            return None
        td = self._lap_time_dist(lap_id)
        if td is None:
            return None
        times, dists = td
        total_lap = float(dists[-1])
        if total_lap <= 0:
            return None
        d_enter = float(corners.project_boundaries(
            [float(corner.enter)], total_ref, total_lap,
            alignment=self.lap_alignment(lap_id, total_lap))[0])
        return float(np.interp(d_enter, dists, times))
