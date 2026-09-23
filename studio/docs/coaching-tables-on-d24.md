# The coaching-family tables as they were last published on D24

A dated record (2026-09-23, T16b part A). Nothing in the app reads this file, and the quote scans
in `tests/test_measured_figures.py` skip it, as they skip `CHANGELOG.md`: every figure here is
superseded.

**What happened to these tables.** Each was measured on the owner's D24 recordings — 0060 is
`GX020060` + `GX030060`, 0062 is `GX010062` + `GX020062` + `GX030062`; `GX010060` is a JSON file a
tool wrote over the footage and was never opened — and the floor and beat-rate tables also on
`Sandown_09_05_2026` (`GX0*0059`) and `SD_30_08_26` (`GX010065`). #335 changed corner matching, and
#339 found eight of them no longer matched the app. #344 marked each one stale (the brake-hint gate
unverified) where it was published. D24 and Sandown_09_05_2026 then left the owner's machine, so none
could be re-measured. On 2026-09-23 the owner made the Desktop recordings the working set, and T16b
re-measured each table there:

| role | was | now |
|---|---|---|
| the pair the coaching tables compare | D24 0060 | `SD_19_09_26` 0068 (`GX010068` + `GX020068`) |
| | D24 0062 | `Sandown 3h 2026` 0064 (`GX010064` + `GX020064` + `GX030064`) |
| the floor and beat-rate tables' recordings | D24 1 / 3 chapters (0062) | Sandown 3h 1 / 3 chapters (0064) |
| | Sandown_09_05_2026 1 / 3 chapters (0059) | SD_19_09 1 / 2 chapters (0068) |
| | SD_30_08 (`GX010065`) | SD_30_08, unchanged |
| | — | MK_18_09 1 / 2 chapters (`MK_18_09_26`, 0067): the one anticlockwise recording |

Each section below is the table's last D24 edition, verbatim, with the mark it carried.

## theme.py — the pointwise Δ-to-ideal floor table (above `format_ideal_run`)

```text
# pointwise. Inside a segment the ideal replays its DONOR lap's pace, and a lap that carries more
# speed into the same corner is transiently ahead of that donor. Swept at 25 ms of media clock over
# every valid lap of the owner's five real recordings, after #300 warped every lap; "prints a minus
# sign" is what the readout printed before the clamp below existed, a raw Δ under -DELTA_EVEN_EPS_S:
#
# ⚠ STALE — NOT RE-MEASURABLE (T16). The table was measured before #335 changed corner matching.
# #339 re-ran its footage check after #335 and it no longer matched the app, and its own change
# moved none of it. The D24 and Sandown_09_05_2026 rows cannot be re-measured, because those
# recordings are no longer available. SD_30_08's two rows can, and re-measured on 2026-09-19 both
# are stale too: −0.262 s on the loader's line (10.75 % / 9.61 %) and −0.255 s on the saved one
# (8.32 % / 7.59 %), on the same 23 laps. Every row and the wobble sentence under the table are the
# record of that measurement, not what the app computes today. The clamp's case does not rest on
# them: the ideal is defined at partition edges, not between them.
#
#   recording              laps  samples     floor   raw Δ < 0   prints a minus sign
#   D24 1 chapter            21    58567  -0.020 s     1.18 %        0.58 %
#   D24 3 chapters           65   181288  -0.008 s     0.11 %        0.03 %
#   Sandown chapter 1        23    47081  -0.164 s     4.52 %        3.75 %
#   Sandown 3 chapters       59   118831  -0.052 s     0.47 %        0.38 %
#   SD_30_08                 23    44389  -0.280 s    12.01 %       10.96 %
#   and on the start line the owner saved beside the recording (its .pacer.json), as the app opens it:
#   Sandown chapter 1 †      24    49130  -0.013 s     0.10 %        0.05 %
#   Sandown 3 chapters †     59   118823  -0.030 s     0.80 %        0.42 %
#   SD_30_08 †               23    44408  -0.246 s     6.28 %        5.46 %
#
# The first five rows are the loader's own start line, which is how this table was first measured
# (#211); D24 has no saved line, so its rows are also what the app shows. † rows restore the saved
# one. On SD_30_08 the two lines now count the same 23 laps and cut them in different places, which
# is all that separates the two rows. Until T13 they did not: the loader's line cut each 46 s
# Sandown Park lap into a 13.3 s and a 34 s piece and counted 25 of the short ones, and the
# unmarked row read -0.039 s over those. Recordings: D24 is GX010062 alone and with GX020062 +
# GX030062; Sandown is GX010059 alone and with GX020059 + GX030059; SD_30_08 is GX010065.
# tests/test_measured_figures.py re-measures every row from them.
#
# against end-of-lap values of +0.48 … +11.81 s. So it is a wobble of at most 0.28 s (0.25 s on the
# owner's saved lines) on a number whose job is to read 0 … +1.5 s, and the next partition edge always
# takes it back: over a segment, and over the lap, you cannot be ahead of the ideal.
```

The quotes of it elsewhere in the tree read "the floor is −0.280 s, on SD_30_08 on the loader's own
start line (−0.246 s on the one the owner saved), and 4.52 % of samples on Sandown chapter 1 /
12.01 % on SD_30_08 are negative at all on the loader's own lines" (`Session.delta_to_ideal`,
`CornerModel.ideal_elapsed`, `tests/test_session_pure.py`, `tests/test_contrast.py`,
`tests/test_charts_header_budget.py`).

## corner_model.py — the beat-rate correlation table (in `SegmentBests.beat_counts`)

```text
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
```

## refused-2026-09.md §2 — the #272 recombination record

Its D24 editions stay in §2 itself, below the working-set table: the edition re-measured after #300
(the one #344 marked stale) and #272's own. The mark it carried read: "The table below was measured on
D24 before #335 changed corner matching, and it is known stale. #339 read 0060's ideal as 65.637 s
after #335 and 65.864 s after its own change, against the 65.464 s below."

## coaching.py — the evidence table, and REACH_REPEAT_FRAC's and SPREAD_MARGIN's notes

```text
# ⚠ STALE — NOT RE-MEASURABLE (T16). The table below was measured on D24 before #335 changed corner
# matching. #339 re-ran its footage check after #335 and it no longer matched the app, on 0062's
# rows as well as 0060's, although 0062 has no interpolated corner cell. D24 is no longer
# available, so the table cannot be re-measured. Its cells, the bullets under it and every note in
# this file that reads them (REACH_REPEAT_FRAC's and SPREAD_MARGIN's included) are the record of
# that measurement, not what the app computes today. Restoring D24, re-basing the table on other
# recordings or leaving it disclosed like this is the owner's decision.
#
# MEASURED, on the two real D24 recordings (0060: 38 clean laps, 12 corners, 9 rows above the
# panel's display resolution; 0062: 65 clean laps, 12 corners, 10 rows). 0060 is GX020060 +
# GX030060 and 0062 is GX010062 + GX020062 + GX030062. Every row, re-measured after #300 warped
# every lap (rank is by time lost, before the gate sinks the abstained rows; reached is how many
# clean laps matched the best lap's own time through the corner):
#
#   rec   corner  rank  lost s  sigma s  IQR s  reached  gate
#   0060  C12        1   0.330    0.227  0.331     2/38  ranked
#   0060  C4         2   0.244    0.240  0.322     6/38  ranked
#   0060  C2         3   0.204    0.449  0.220     7/38  ranked
#   0060  C9         4   0.153    0.144  0.193     7/38  ranked
#   0060  C6         5   0.105    0.164  0.186     8/38  ranked
#   0060  C8         7   0.057    0.073  0.101     6/38  ranked
#   0060  C1         6   0.058    0.409  0.200    13/38  spread
#   0060  C10        8   0.055    0.593  0.174    14/38  spread
#   0060  C7         9   0.022    0.264  0.331    17/38  spread
#   0062  C3         1   0.148    0.174  0.158     6/65  ranked
#   0062  C12        2   0.143    0.150  0.206     4/65  ranked
#   0062  C1         3   0.085    0.227  0.109     5/65  ranked
#   0062  C6         4   0.084    0.096  0.103    13/65  ranked
#   0062  C5         5   0.076    0.127  0.113    13/65  ranked
#   0062  C8         7   0.056    0.064  0.060     6/65  ranked
#   0062  C11        6   0.063    0.192  0.175    18/65  spread
#   0062  C10        8   0.045    0.253  0.135    20/65  spread
#   0062  C4         9   0.037    0.214  0.202    22/65  spread
#   0062  C9        10   0.018    0.079  0.103    25/65  spread
#
# (tests/test_measured_figures.py derives every figure below from this table, and re-measures the
# table itself when pointed at the footage — which CI does not have.)
#
#   * σ ≥ time_lost on 16 of those 19 rows. The worst: 0060 C7 lost 0.022 s against σ 0.264 s
#     (12.0x), 0060 C10 lost 0.055 s against σ 0.593 s (10.9x). Without the gate below, those rows
#     would carry a live Jump button beside a number smaller than a tenth of the corner's own
#     scatter.
#   * σ is NOT the right spread statistic: on 0062 C1 it reads 0.227 s while the interquartile
#     range is 0.109 s — twice the width of the whole middle half, where normal scatter would put σ
#     at three quarters of it — because a handful of slow laps drag the second moment and a
#     quartile does not. The gate below reads the IQR.
#   * the reach rate (how many clean laps already matched the corner's target) runs 5 %..45 % and
#     splits cleanly at 1 lap in 10 — 14 of the 19 rows are corners the driver reaches routinely,
#     5 are corners reached about once a session.
#
# WHAT THE BRIEFED PREMISE GOT WRONG, and it is worth writing down: the target the ranking uses is
# the BEST LAP's time through the corner, and that is almost never a lone outlier. `z > 1.5` (the
# other laps' mean minus the target, over their σ) fires on one of the 19 rows, 0060 C12 at z 1.59,
# and every row but that one has at least two OTHER laps strictly beating it (1..24 of them). Nor is
# it "your optimal line": on both recordings the best lap's corner time was slower than that
# corner's own best instance at all 12 of 12 corners (by 0.04..0.68 s on 0060, 0.01..0.21 s on
# 0062). So ABSTAIN_ONE_OFF below has not fired on either full recording — it fires on four of the
# five single chapters, and a 3-lap session can trivially produce it.

# A corner needs at least this many clean instances before ANY per-corner claim is made about it.
# (The session-level MIN_LAPS gate above is a different question — it asks whether the median is
# defined at all; this one asks whether THIS corner was actually driven enough times to talk about.)
MIN_CORNER_LAPS = 3

# "You have already done this" needs more than one lap saying so. The BEST LAP's own instance is
# always in the candidate set, so a reach count of 1 means nothing but the baseline itself ever got
# there — a target with no second witness is not a target.
MIN_REACH_LAPS = 2

# ...and it needs to be more than a rounding-level rate: fewer than 1 lap in 10 at the target is a
# corner you have visited, not a pace you have established.
#
# THE MEASURED REASON FOR 10 % NO LONGER HOLDS, AND THE VALUE IS CARRIED FORWARD UNVERIFIED (T16).
# The line was put in a gap. On the evidence table above, measured on D24 before #335, the 19 real
# rows' reach rates sort as 5.3 6.2 7.7 9.2 9.2 | 15.8 15.8 18.4 18.4 20.0 20.0 21.1 27.7 30.8
# 33.8 34.2 36.8 38.5 44.7 % — no row sits between 9.2 and 15.8 %, and that gap across 10 % is the
# second-widest in the set (6.56 points, against 6.64 for 21.1 → 27.7 %). Before #300 it was the
# widest. #339 re-measured the rows after #335 changed corner matching, and the gap is gone: the
# 10 % line now falls in only the 4th-widest gap of the set, 3.08 points wide, against 6.33 for
# 10.8 → 17.1 %. So no measurement puts the line at 10 % rather than somewhere else any more, and
# a different line would re-sort corners between execution and pace work. The value is NOT moved
# here. Re-deciding a behaviour constant needs the rows re-measured, and D24 is no longer
# available; re-basing them on other recordings is the owner's call (T16).
REACH_REPEAT_FRAC = 0.10

# A claim must clear half the corner's own INTERQUARTILE spread to be aimable. Not a significance
# test — with 38-65 laps the standard error of a median is ~0.03 s and almost nothing would abstain
# — but an ACTIONABILITY test: a driver cannot aim at 0.03 s inside a band whose middle half is
# 0.20 s wide, however real the 0.03 s is. Measured: 7 of the 19 real rows abstain here, and the
# highest-ranked is sixth on both recordings (0060 C1, 0.058 s on offer against a 0.200 s
# interquartile band; 0062 C11, 0.063 s against 0.175 s). Those rows are the evidence table's, stale
# since #335 (T16). The margin's case is the actionability argument above, not those counts.
SPREAD_MARGIN = 0.5
```

## coaching.py — the THEME table, and THEME_SHARE's note

```text
# ⚠ STALE — NOT RE-MEASURABLE (T16). The table below was measured on D24 before #335 changed corner
# matching. #339 re-ran its footage check after #335 and it no longer matched the app. D24 is no
# longer available, so the table cannot be re-measured. Its cells, the verdicts drawn from them
# below and THEME_SHARE's note are the record of that measurement, not what the app computes today.
#
# MEASURED, on the two real recordings and on each of their chapters loaded alone, over the ranked
# (non-abstained) rows, after #300 warped every lap. "top cause" is the reason holding the most
# ranked time, whether or not it clears THEME_SHARE:
#
#   lap set           laps  ranked  ranked s  abstained s  execution   pace  top cause
#   0060                38       6     1.094        0.135       70 %   30 %  braking 62 %
#   0062                65       6     0.593        0.162       27 %   73 %  braking 76 %
#   0060 chapter 2      24       5     0.932        0.424       45 %   55 %  line 45 %
#   0060 chapter 3      13       2     0.442        0.888      100 %    0 %  line 51 %
#   0062 chapter 1      21       6     0.521        0.164       82 %   18 %  braking 79 %
#   0062 chapter 2      24       4     0.344        0.210       71 %   29 %  braking 53 %
#   0062 chapter 3      18       5     0.548        0.454      100 %    0 %  line 74 %
#
# 0060 splits 70 % execution / 30 % pace and 0062 splits 27 % / 73 % — SAME driver, SAME track, a
# day apart, and the theme comes out opposite. That is the finding that justifies the feature. The
# cause axis agrees on the two full recordings — braking holds 62 % of 0060's ranked time and 76 %
# of 0062's, a theme on each — but it does not survive a smaller lap set: of the five single
# chapters, three name no single cause, so the cause line is conditional and will often read "no
# single cause dominates".
#
# AND THE HONEST CAVEAT, also measured: the theme is a property of the LAP SET, and it moves with
# it. Loading only chapter 2 of each recording (24 laps instead of 38 and 65) moves BOTH verdicts —
# 0060 falls to a SPLIT and 0062 flips to execution — and across the five single chapters the
# verdict is execution on four and a split on one, where the two full recordings disagree. Corners
# cluster near the 1-in-10 reach line, so a different lap set moves several of them across it at
# once. The sentence therefore always states its own share, and THEME_SPLIT exists so a balanced
# session is not forced to pick a side.
THEME_EXECUTION = "execution"  # most of the ranked time is in corners already driven at this pace
THEME_PACE = "pace"            # most of it is in corners the driver has rarely reached
THEME_SPLIT = "split"          # neither side holds a clear majority — say so, don't invent one
THEME_NONE = "none"            # nothing ranked (every row abstained, or there are no rows)

# The share one side must hold before it is called the session's theme. 0.60 is a clear majority
# with room to spare; measured on the THEME table above (stale since #335, T16), 0060 lands at 0.70
# and 0062 at 0.73, and one corner tips either:
# 0060 would fall to a SPLIT if its C4 (reached on 6 of 38 laps; the line is 4) tipped the other
# way, and 0062 would if its C3 (reached on 6 of 65 laps; the line is 7) did.
THEME_SHARE = 0.60
```

## coaching.py — the brake-habit table, and MIN_BRAKE_LAPS' note

```text
# ------------------------------------------- the braking habit: ONE answer to "how much later?"
#
# WHY THIS EXISTS. The app answered the driver's plainest question — "how much later can I brake
# into this corner?" — with TWO different numbers on two surfaces, and named neither. The coaching
# row's hint read the BEST lap's single brake application (`driving.BrakePoint.metres_later` on
# `best_lap_id()`); Stats ▸ BRAKING's "m later" column read the MEDIAN of that same quantity over
# the clean laps. Nothing cross-referenced them, so the disagreement was invisible and unresolvable.
#
# ⚠ STALE — NOT RE-MEASURABLE (T16). The table below was measured on D24 before #335 changed corner
# matching. #339 re-ran its footage check after #335 and it no longer matched the app. D24 is no
# longer available, so the table cannot be re-measured. Its cells, the prose under it and
# MIN_BRAKE_LAPS' note are the record of that measurement, not what the app computes today. D2
# moved them again, unmeasurably here: a string of brake blips with no sustained brake is no
# longer a brake event, so a lap that only lifted no longer counts toward "laps". On the four
# present recordings that cut one corner from 20 to 9 laps and moved a habit by up to 1.5 m. THE
# RULE at the end of this block does not rest on the cells: it is an argument about which
# statistic to print.
#
# MEASURED, on the two real D24 recordings, over the rows whose hint the coaching panel would show
# if it still read the best lap: a RANKED row whose best lap has a matched brake application, at
# least BRAKE_HINT_MIN_M of metres, and an optimum no more than BRAKE_HINT_MAX_PAST_TURN_IN_M past
# the turn-in. "best lap" is that lap's single `metres_later`, "habit" is the median the BRAKING
# table's "m later" column prints (+ = could brake later), "laps" is how many clean laps matched an
# application, and "rank" is the row's place in the evidence table above. Re-measured after #300;
# tests/test_measured_figures.py derives the prose below from these cells and, given the footage,
# re-measures every one:
#
#   rec   corner  rank  best lap m  habit m   laps
#   0060  C12        1         9.7     14.2  38/38
#   0060  C4         2        16.5     16.7  38/38
#   0060  C2         3        29.0     23.2  38/38
#   0060  C9         4        22.7     18.4  31/38
#   0062  C12        2         7.0     10.8  65/65
#   0062  C1         3         2.9     12.2  62/65
#   0062  C6         4        14.5     27.1  65/65
#   0062  C5         5        13.4     15.9  65/65
#
# The two answers sit 4.4 m apart at the median on 0060 (worst 5.9 m, C2) and 6.6 m apart on 0062
# (worst 12.6 m, C6).
#
# 0062's C1 is the one that shows what the split cost: the best lap happened to brake within 3 m of
# its own optimum, so coaching printed "~3 m later" — barely over the BRAKE_HINT_MIN_M noise floor,
# i.e. a shrug — while the driver's HABIT over 62 laps was 12.2 m early. Both say "later"; one of
# them says it is not worth doing, from one lap of sampling noise. The reverse case is just as bad:
# 0062's best lap had no matched brake event at all into C3, so the top-ranked row said nothing about
# a corner 44 laps DID brake into.
#
# THE RULE, therefore: a coaching instruction is about the driver's HABIT, so it is a cross-lap
# statistic over the same clean laps every other number on the row already uses (`time_lost` is a
# cross-lap median; "Done it?" is a count over those laps). The best lap's own application is one
# sample of a scattered distribution — the BRAKING table's σ and span columns exist precisely
# because that scatter is large — and it was the only number on the row that was not.

# A braking habit needs at least this many matched applications before it is a habit. (Measured on
# the table above, before #335 and stale since (T16), a real recording is nowhere near it: every
# corner on the two D24 recordings matched on at least 31 of its clean laps. This guards a 3-lap
# session, not a normal one.)
MIN_BRAKE_LAPS = 3
```

## focus.py — the cross-session tables (module docstring)

```text
⚠ STALE — NOT RE-MEASURABLE (T16). The two tables below were measured on D24 before #335 changed
corner matching. #339 re-ran their footage check after #335 and it no longer matched the app; its
record does not say which cells moved. D24 is no longer available, so neither table can be
re-measured. Every figure below, and every quote of one elsewhere in the tree, is the record of
that measurement, not what the app computes today.

MEASURED, on the two real D24 recordings — the same driver at the same track on CONSECUTIVE DAYS
(0060: 2026-05-23, 38 laps; 0062: 2026-05-24, 65 laps), which is the input this feature takes.
Promote 0060's top three ranked corners and measure them again on 0062 over the SAME windows.
"promoted for" is the coaching row's time lost; the medians and interquartile ranges are the
window's seconds over each session's clean laps; "bar" is ``SPREAD_MARGIN`` × the wider of the two
IQRs, the test ``verdict`` applies. Re-measured after #300; tests/test_measured_figures.py derives
the prose from these cells and, given the footage, re-measures every one:

  corner  promoted for  0060 median  IQR    0062 median  IQR    change  bar    verdict
  C12     +0.330 s      6.774 s      0.213  6.747 s      0.167  −0.026  0.107  unchanged
  C4      +0.244 s      4.537 s      0.129  4.597 s      0.134  +0.061  0.067  unchanged
  C2      +0.204 s      2.364 s      0.128  2.403 s      0.073  +0.039  0.064  unchanged

  * every one of the three changes is inside its bar. The honest verdict on the only real
    cross-session pair this repo has is "no change you can act on", three times out of three —
    which is why ``OUTCOME_UNCHANGED`` is a first-class answer here and not an error path. Only
    the "promoted for" column moved when #300 warped the corner service; every window cell came
    back identical, because ``window_times`` reads each lap's own clock, not that warp;
  * neither recording has a session record (the owner's app-support dir has no
    ``session_records.json`` at all), so the like-for-like gate blocks all three verdicts before the
    spread test is even reached. What the feature says today, on real data, is "I can't tell you
    whether these two sessions were comparable" — not a number.

AND THE WINDOW PROBLEM, which is the one this module exists to solve and the reason a focus item
stores a WINDOW rather than a corner id. The corner partition is re-derived per session from that
session's own trace, so "C8" is not the same measurement twice. Each recording's own window for the
corner, the median time over it, and 0062's median over 0060's STORED window instead:

  corner  0060 window  0062 window  0060 own  0062 own  own change  0062 over 0060's  stored change
  C8      45.0 m       56.3 m       2.110 s   2.660 s   +0.549 s    2.153 s           +0.042 s
  C10     75.1 m       81.0 m       3.951 s   4.220 s   +0.268 s    4.014 s           +0.063 s

Reported as a cross-session change, C8's own-window +0.549 s is "you got slower" and every
millisecond of it is the detector drawing a longer window; over the stored window it is +0.042 s. So
a focus item stores its window as a FRACTION of the lap odometer and both sides are measured by the
same function over that fraction; the corner id is a label on it, never the identity. The
partitions do line up that way: across the twelve corners the two sessions' apexes agree to
−4.2..+0.2 m (0062's apex against 0060's scaled by the two lap totals), and the lap totals to
0.65 % (1059.2 vs 1066.2 m).
```

## focus.py — MAX_LAP_TOTAL_DRIFT's note

```text
# How far the two sessions' lap odometers may disagree before a fraction-mapped window stops being
# the same stretch of track. MEASURED: the two D24 recordings' lap totals differ by 6.93 m on 1059 m
# — 0.65 % — which displaces a corner boundary by at most ~0.3 m inside a 50 m window. 2 % is three
# times that: comfortably past any re-fit of the same lap, and short of a genuinely different route
# or a start line placed somewhere else.
MAX_LAP_TOTAL_DRIFT = 0.02
```

## Quotes of these tables elsewhere, as they read on D24

- `coaching.theme_sentence`: "0060's chapter 3 alone: 0.442 s ranked against 0.888 s abstained"; the SPLIT state "0060's
  chapter 2 alone lands here".
- `studio/README.md`, `coaching_panel._reach_cell`, `focus.CornerSample`, `tests/test_coaching.py`: σ ≥ time lost on 16 of
  19 shown rows (worst 12.0x), 7 abstain; on 0062 C1 σ 0.227 s against a 0.109 s IQR; 0060 70 % execution, 0062 73 %
  pace; braking 62 % of 0060's ranked time and 76 % of 0062's; at least two other laps on all but 0060's C12
  (1..24 of them).
- `Session.coaching_brake_points`, `studio/README.md`, `tests/test_coaching.py`: the best lap and the habit up to 12.6 m
  apart; at 0062's C1 the best lap braked within 3 m of its own optimum while the habit over 62 laps was 12.2 m early.
- `Session.focus_samples` / `focus_items`, `LibraryController.focus_add`, `studio/README.md`, `tests/CMakeLists.txt`,
  `tests/test_focus_list.py`: C8's window grew 45.0 m → 56.3 m (11.2 m), +0.549 s own-window, +0.042 s over the
  stored window; the pair's lap totals 0.65 % apart (1059.2 vs 1066.2 m); promoted C12 +0.330 s, C4 +0.244 s,
  C2 +0.204 s moved −0.026 / +0.061 / +0.039 s, all three unchanged; window vs corner-service medians 0.03–0.18 s
  apart on 0060 and 0.01–0.08 s on 0062.
