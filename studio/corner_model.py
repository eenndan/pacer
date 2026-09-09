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
# Recall is 0 % STRUCTURALLY: every convictable cell sits on a lap below
# corners.NORMALIZED_DRIFT_MAX, whose projection IS `ref_span × total_lap/total_ref`, so its
# deviation from that is zero by construction and this test can never reject it however misaligned
# the window is. And those laps are not clean: they carry a median 1.16 % / p90 9.02 % true span
# error on the pair (1.03 / 9.42 on ch 1, 0.48 / 2.92 on 0062), 5 cells worse than −10 %.
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
# ── DEFERRED ─────────────────────────────────────────────────────────────────────────────────
# Because corners.NORMALIZED_DRIFT_MAX keeps below-drift laps on the normalized projection, this
# test is inert on 22 of the pair's 38 laps. What the gate costs, measured LIKE FOR LIKE — gated
# vs warp-every-lap at the SAME admission tolerance, so the two changes are not conflated:
#
#              pair      ch 1      0062
#   at ±5 %   +0.316 s  +0.347 s  −0.071 s
#   at ±3 %   +0.501 s  +0.670 s  +0.060 s
#
# (The ±5 % row is also the move against the SHIPPED number, since that is the shipped tolerance.
# An earlier draft quoted +0.644/+0.712/+0.110 for the ±3 % row by differencing against the ±5 %
# shipped value, which folds the tolerance change into the gate's residual — hence this note about
# which two things are being differenced.) That residual is the price of the byte-identity the
# drift gate buys, and it is the same follow-up corners.NORMALIZED_DRIFT_MAX's note names.
MAX_DONOR_SPAN_DEV = 0.05
# ── SUB-RESOLUTION SEGMENTS: A KNOWN LIMITATION, DELIBERATELY NOT "FIXED" ─────────────────────
# On a 2.3 m sliver the ±5 % band is ±0.11 m, an order of magnitude under the
# ±corners.SPATIAL_MATCH_MAX_M the boundary matches are guaranteed to — so admission there is
# decided by noise, and segment 4 of the 0060 pair refuses 4 of its 38 laps arbitrarily. 16 of the
# pair's 23 real segments have a band under that tolerance at all.
#
# Exempting them (as POINT segments are exempt) was implemented and MEASURED, then reverted:
#
#   exempt ref_span ≤ 3.0 m   pair 65.145 (−0.004)  ch1 66.102 (0.000)  0062 66.781 (0.000)
#     …but the pair's WINNER span-fraction floor falls 0.944 → 0.850, because a 2.3 m segment is
#     then won on a window 0.35 m off — noise, admitted into a MINIMUM.
#   exempt band < 3.0 m       pair 64.164 (−0.984)  ch1 65.037 (−1.066)  0062 66.563 (−0.217)
#     …which exempts the 42.8 m straight this whole repair is about (band ±2.14 m) and reverts
#     most of the fix.
#
# The narrow version buys 0.004 s of principle and costs a downward bias; the wide version undoes
# the repair. AN ADMISSION RULE FEEDING A MINIMUM MUST FAIL CLOSED: rejecting a lap arbitrarily
# only removes a candidate (bounded here by one 0.15 s segment), while admitting one arbitrarily
# lets noise win the segment — the same failure direction as the defect this constant exists for.
# So the noise is real, its cost is bounded and upward, and it stays. Resolving it properly means
# not cutting sub-sample segments in the first place (a partition-design change: it moves
# `IdealSample.corners`/`segments`, which every ideal-lap disclosure prints).


class IdealSample(NamedTuple):
    """WHAT THE IDEAL LAP WAS MINIMISED OVER — the four counts any surface printing the composite
    has to print with it, because the composite is a function of all four.

    `SegmentBests.total` is a sum of per-segment MINIMA. A minimum over more laps is never larger
    and is usually smaller, so the "ideal" falls as a session accumulates laps, and it falls again
    when the partition is cut finer. Both are properties of an order statistic over a partition,
    not of the driving — so two ideals are only comparable when these counts are comparable.

    Measured on the owner's five recordings (random subsets of the clean laps, 200 draws per N,
    `ideal_total` as the app computes it):

    | recording        | 5 laps | 10 | 20 | 40 | all | per doubling of N |
    |------------------|--------|----|----|----|-----|-------------------|
    | D24 1 chapter    | 68.333 | 68.057 | 67.844 | — | 67.831 (21) | 0.174 s |
    | D24 3 chapters   | 67.957 | 67.578 | 67.192 | 66.832 | 66.563 (65) | **0.384 s** |
    | Sandown ch 1     | 48.585 | 48.225 | 47.982 | — | 47.933 (23) | 0.241 s |
    | Sandown 3 ch     | 48.338 | 47.998 | 47.735 | 47.483 | 47.374 (59) | 0.194 s |
    | SD_30_08         | 13.025 | 12.945 | 12.878 | — | 12.856 (25) | 0.068 s |

    There is no plateau: on D24 3 chapters the decrement per doubling GROWS with N (0.31 s over
    8→15 laps, 0.42 s over 30→65). The gap the app headlines ("on the table") therefore grows with
    lap count on all five — D24 3 chapters reads −0.90 s at 5 laps and −1.64 s at 65, same driving.

    THE BEST LAP HAS THE SAME PROPERTY, WHICH IS WHY THE DISCLOSURE IS PER RECORDING AND NOT PER
    COLUMN. The best lap is also a minimum over the session's laps: measured the same way it falls
    0.221 / 0.148 / 0.033 / 0.094 / 0.080 s per doubling on those five, i.e. FASTER than the ideal
    on two of them (D24 1 chapter and SD_30_08). Suppressing the ideal's ranking while leaving the
    best lap's alone would fix the smaller half of the problem on 2 of 5 recordings; naming the
    sample fixes both. See studio/library_dialog.py's Laps column.

    `corners` / `segments` are the partition's size, and they move when the corner detector re-runs
    — which it does on every start/finish-line drag. Measured over six start-line positions per
    recording the detected corner count moves 11↔12 on D24 and 7↔8 on Sandown, and the headline gap
    with it: D24 1 chapter 0.94 s at the fitted line, 1.08 … 1.48 s over those six positions
    (+58 %); Sandown 3 chapters 1.14 s, 1.16 … 1.93 s (+69 %). A mechanical midpoint refinement
    (every segment split in two, no new information) buys another 0.30 … 1.67 s. So a user who
    drags the line and sees the gap move is looking at a re-cut partition, not at their driving —
    and `corners`/`segments` on screen is what lets them see that."""

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
                f"{plural(self.corners, 'corner')} and {plural(self.straights, 'straight')} pacer "
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
      admitted  — (len(lap_ids), 2N+1) bool: which cells may donate (see MAX_DONOR_SPAN_DEV).
      bests     — 2N+1 minima over the admitted cells.
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
        durations") does not reproduce and understated the coupling. Re-measured independently on
        FIVE recordings, subject = the best lap, non-point segments only, beat rate against each
        segment's mean duration:

        | recording      | n  | r      | Spearman | permutation p |
        |----------------|----|--------|----------|---------------|
        | D24 1 ch       | 24 | −0.176 | −0.239   | 0.264 |
        | D24 3 ch       | 24 | −0.340 | −0.331   | 0.117 |
        | Sandown ch 1   | 15 | −0.297 | −0.289   | 0.297 |
        | Sandown 3 ch   | 15 | −0.602 | −0.596   | **0.019** |
        | SD_30_08       |  5 | −0.905 | −0.900   | 0.067 (exact, 120 permutations) |

        One of five is distinguishable from chance, and the strongest r sits on the recording with
        FIVE segments, where n makes p ≥ 0.017 unreachable at any effect size. A residual negative
        correlation is also what a real track produces — a short piece of road has less room to
        differ, so more laps land level with the subject — and because the statistic is provably
        invariant to scale, that correlation is a fact about the driving, not about the units. The
        rejected `hit_counts` had no such defence: its coupling came from a tolerance measured in
        seconds against segments of unequal length.

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

    def invalidate(self) -> None:
        """Drop EVERY corner cache — called from Session.set_timing_lines (the single
        re-segmentation point): the corner set is detected on + projected through the
        segmentation, so all of them are stale after a timing-line change."""
        self._basis_cache = _UNSET
        self._stats_cache.clear()
        self._bests_cache = _UNSET
        self._segment_bests_cache = _UNSET
        self._align_cache.clear()

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
        costs nothing measurable and removes a whole class of staleness argument."""
        self._stats_cache.clear()
        self._align_cache.clear()

    # ----------------------------------------------------------- drift-gate spatial traces
    def _best_trace(self) -> tuple | None:
        """The best (reference) lap's local-frame trace (xs, ys, cum) — the spatial anchor side of
        the per-corner drift gate (corners.project_boundaries). None when there is no usable best
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
        """The (ref_xs, ref_ys, ref_cum, lap_xs, lap_ys, lap_cum) trace pair the drift gate's
        spatial fallback needs to map this session's corner windows onto `lap_id`. None (→ the
        gate keeps the normalized projection) when either trace is degenerate. A reference-lap
        (cross-recording) projection has no local trace pair, so it stays normalized."""
        if ref_trace is None:
            return None
        _t, xs, ys, _v, cum = self._lap_columns(lap_id)
        if len(cum) < 2 or float(cum[-1]) <= 0:
            return None
        return (*ref_trace, xs, ys, cum)

    def lap_alignment(self, lap_id: int, total_lap: float) -> object | None:
        """ONE lap's monotone warp onto the reference (best) lap's odometer — the thing every
        corner-window projection in the app is a read of — MEMOIZED per (lap, total_lap).
        None is a legal value: "this lap keeps the normalized projection" (below the drift gate,
        or no spatial match survived). Pass the result as `alignment=` to
        `corners.project_boundaries` / `segment_times` / `lap_corner_stats`.

        WHY THIS EXISTS. The warp is built from the WHOLE corner partition, so it is the same
        object for every window of a lap — but nine independent call paths each derived it for
        themselves. Measured on the real D24 0060 pair (38 laps, exactly 16 of them past
        `corners.NORMALIZED_DRIFT_MAX`), ONE `stats_view.refresh` ran `corners._spatial_matches`
        **144 times** — 9x per lap that needs it — against the 16 the memo now costs. (Call counts
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
        align = corners.lap_alignment(frame, total_ref, float(total_lap),
                                      traces=self._lap_traces(lap_id, self._best_trace()))
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
                # dropout); the best lap is always included so a session where every lap is
                # dropout-flagged still detects on the best lap alone.
                ids = [i for i in self._valid_lap_ids() if not self._lap_has_dropout(i)]
                if best not in ids:
                    ids.append(best)
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
        # Drift-gate spatial traces: the best lap (the corner-window reference frame) + this lap.
        # The best lap itself projects onto its OWN odometer (zero drift → identity), so its trace
        # pair is harmless; a degenerate trace → None → normalized projection (unchanged).
        traces = self._lap_traces(lap_id, self._best_trace())
        stats = corners.lap_corner_stats(corner_list, total_ref, dist, speed_kmh, elapsed,
                                         ref=ref or None, traces=traces,
                                         alignment=self.lap_alignment(lap_id, float(dist[-1])))
        self._stats_cache[lap_id] = stats
        return stats

    def corner_session_bests(self) -> list[float]:
        """Per-corner session-best time-in-corner across all VALID laps (the purple-cell
        convention, matching the per-sector session bests). [] when no corners. Cached;
        cleared on re-segment."""
        if self._bests_cache is not _UNSET:
            return self._bests_cache
        per_lap = [self.lap_corner_stats(i) for i in self._valid_lap_ids()]
        per_lap = [st for st in per_lap if st]
        n = len(self.corner_list())
        self._bests_cache = [
            min(st[i].time for st in per_lap) for i in range(n)
        ] if per_lap and n else []
        return self._bests_cache

    # ------------------------------------------------------------------ ideal lap (D1)
    def _composite_lap_ids(self) -> list[int]:
        """The laps the ideal composite may draw on: `Session.consistency_lap_ids()` — VALID and
        DROPOUT-FREE, the same set every consistency statistic runs on. A dropout lap's distance
        is speed-integral reconstructed, so its segment boundaries (and therefore its segment
        TIMES) are exactly the ones that must not be allowed to win a segment.

        The BEST lap is appended when the dropout rule excluded it — the same guarantee
        `basis()` makes for the detection profile. It is what keeps `total <= best lap time`
        true in the degenerate session where every valid lap is dropout-flagged and
        `best_candidate_ids` fell back to the flagged set."""
        ids = [i for i in self._valid_lap_ids() if not self._lap_has_dropout(i)]
        best = self._best_lap_id()
        if best is not None and best not in ids:
            ids.append(best)
        return ids

    def segment_bests(self) -> SegmentBests | None:
        """The IDEAL LAP: the per-segment minimum of the corner/straight partition over the
        clean laps (`_composite_lap_ids`), with its donors, its per-lap matrix and the partition
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
        rows, spans, edges, lap_ids, expected = [], [], [], [], []
        for lid in self._composite_lap_ids():
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
        masked = np.where(admitted, times, np.inf)
        bests = [float(v) for v in masked.min(axis=0)]
        donors: list[int | None] = [
            None if bests[j] <= 0.0 else lap_ids[int(masked[:, j].argmin())]
            for j in range(times.shape[1])
        ]
        self._segment_bests_cache = SegmentBests(
            labels=labels, cids=[int(c.cid) for c in corner_list], lap_ids=lap_ids,
            times=times, admitted=admitted, bests=bests,
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

        WHY IT MATTERS, measured: drawing each segment as a straight line in (distance, time)
        instead sent `Session.delta_to_ideal_at` to −0.87 s on 18.4 % of samples on the Sandown
        recording — a 163 m / 9.9 s corner is nowhere near constant pace, so the line is nowhere
        near anything anybody drove. Following the donor cuts the worst excursion to **−0.159 s**
        (2.09 % of samples on Sandown chapter 1, 6.42 % on SD_30_08 — the per-recording table is
        in `theme.format_ideal_run`'s note), which is then a real "you were up on the ideal through
        here" rather than an artefact of the drawing.

        Those two figures read −0.052 s / "under 1 %" until this sweep was redone: the original was
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
        """Media-clock time (s) `lap_id` enters corner `cid` — the jump-to seek target. Projects
        the corner's enter point onto this lap's odometer and reads elapsed->media there. None if
        unknown/degenerate. Absolute (lap start + elapsed).

        Goes through the SAME drift-gated alignment as its siblings — since the memo it now reads
        (`lap_alignment`) is shared, the warp is LITERALLY the one `lap_corner_stats` used, not
        merely one built from the same frame. On
        a drifted lap the bare normalized fraction it used before landed the seek up to ~12 m from
        the corner entry the Corners table was pointing at; measured move on the D24 0060 pair,
        0.373 s.

        IT IS NOT THE LAST UN-GATED PROJECTION IN THE APP. `coaching._win` still scales a corner
        window by `lap_total / corner_dist_total` with no drift gate and no traces, and its output
        feeds `Reason.brake_extra_s` / `coast_extra_s` — so a coaching row can carry a warp-derived
        phase triple beside a normalized-frame reason. Pre-existing and untouched here; migrating
        `_win` onto `lap_alignment` is the follow-up."""
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
