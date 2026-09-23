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
