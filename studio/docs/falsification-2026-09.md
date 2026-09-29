# What would prove the four headline numbers wrong — 2026-09-29

**The ideal lap's exit (MOAT-3): its noise bias is NOT ESTIMABLE from a recording.** The only
per-recording estimator on the table, the anti-correlation between adjacent segments, recovers
the true bias in 2 of 9 synthetic cases, and at noise 2 and 4.5 it sees a quarter to a ninth of
it. So neither branch of the exit rule can be taken as written: whether the real bias is within
~25 ms (row C's lap σ) is not known, and a de-bias has no estimate to subtract. **TRUTH-6** takes
its "not estimable" branch: the IDEAL LAP surface says in words that a minimum over noisy laps reads
fast, with no size and no bias figure. **TRUTH-11** (de-bias the ideal) is not eligible, because
its estimator failed the synthetic check, and closes with this note.

This is the review's move 4, outputs (a) and (b): the ideal lap's bias on real footage, and one
written line per headline number saying what observation would prove it wrong, each checked by
resampling and by a holdout. It is a measurement: no product code and no published figure changed.
The probe is [`studio/dev/probes/p19_truth_real.py`](../dev/probes/p19_truth_real.py), with 1,000
lap resamples and 2,000 noise draws, both seeded (20260929). It read the four working-set
recordings in date order, each jailed and tripwired:

| | Sandown 3h 2026 | SD_30_08_26 | MK_18_09_26 | SD_19_09_26 |
|---|---|---|---|---|
| date · track | 19 Jul · Sandown Park | 30 Aug · Sandown Park | 18 Sep · Daytona Milton Keynes | 19 Sep · Sandown Park |
| laps in the ideal's matrix | 62 | 37 | 19 | 36 |

## 1. The ideal lap's noise bias (output a)

**The estimator.** A boundary's timing error enters the segment before it with one sign and the
segment after it with the other, so across laps it shows up as a negative covariance between the
two. Per boundary, σ² = max(0, −cov); a cell's noise is its two boundaries' sum; the bias is the mean
over 2,000 draws of min(cells + noise) − min(cells), summed over the segments. The truth it is held
to is TRUTH-1's (`tests/test_truth_matrix.py`, row 3): app ideal − true ideal on the synthetic
GoPro. It passes only if every one of 9 cases (3 seeds × noise 1, 2, 4.5) lands within 30 ms or
30 % of the truth.

| noise | true bias, mean of 3 seeds (range) | estimate, mean | cases within |
|---|---|---|---|
| 0 (unscored) | +7.4 ms (+5.1 … +8.9) | −6.3 ms | — |
| 1 | −60.9 ms (−135.9 … −9.2) | −12.6 ms | 2 of 3 |
| 2 | −160.9 ms (−256.5 … −87.6) | −22.9 ms | 0 of 3 |
| 4.5 | −698.0 ms (−922.6 … −473.4) | −121.5 ms | 0 of 3 |

**FAIL, 2 of 9.** Three held-out seeds (3, 4, 5) agree: truth −57.1 / −198.6 / −531.8 ms at noise
1 / 2 / 4.5, estimate −14.6 / −54.1 / −152.0 ms. A post-hoc variant that first takes each lap's pace
out of its segments (regression on the lap time, which carries no interior boundary's error) also
fails, 2 of 9, and reads −43 ms on noise-free recordings whose truth is +7 ms.

**Why it cannot see the noise.** Driving couples neighbouring segments positively: a slow corner exit
is carried down the straight after it. With no noise at all, 13 to 14 of 15 boundaries already
clamp at zero. On the owner's recordings the estimator clamps 15 of 15 (Sandown 3h), 14 of 15
(SD_30_08), 23 of 23 (MK) and 13 of 15 (SD_19_09), and reads 0.0, −3.9, 0.0 and −29.6 ms. Those
are the readings it also gives on synthetic recordings whose true bias is 88 to 923 ms, so they
bound nothing. Even a known noise level fixes the size only loosely: at synthetic noise 1 the three
seeds' truth runs from −9 to −136 ms.

**Noise-free, the ideal is not fast.** At noise 0 it reads +7.4 ms slow on TRUTH-1's seeds and
−3.6 ms on the held-out ones, within ±12 ms either way. It reads fast from noise 1 upward. The
pooled calibration in `tests/test_truth_matrix.py` puts the owner's lap σ at synthetic noise 2 to
7, where the synthetic minimum reads fast by tenths of a second. That transfers only as far as the
synthetic noise model does, so no surface may print it (review §7).

## 2. One falsification line per headline number (output b)

Each line names the observation that would prove the number wrong, the check run now, its figures,
and the check to run on the owner's next recording (`real` on its folder, once the probe's allow-list
names it). "Halves" means odd and even laps, interleaved, so a trend within the session cancels.

### The ideal lap: "time you have already demonstrated, one segment at a time"

**It would be false if** another draw of the same session's laps gave a different ideal by more than
its own resampling spread: the two halves apart by more than twice the bootstrap sd.

| | Sandown 3h | SD_30_08 | MK | SD_19_09 |
|---|---|---|---|---|
| ideal | 45.809 s | 46.218 s | 66.004 s | 46.196 s |
| bootstrap sd | 88 ms | 52 ms | 146 ms | 46 ms |
| halves apart | +7 ms | +111 ms | +135 ms | −5 ms |
| each half, slower than the whole | +219 / +212 ms | +209 / +98 ms | +514 / +378 ms | +148 / +153 ms |

**Holds on three, fails on SD_30_08**, whose halves are 111 ms apart against a bar of 104 ms. The
bar is strict, since each half has half the laps and so a wider spread than the whole. Every half
reads 0.1 to 0.5 s slower than the whole session, the order statistic that `IdealSample` publishes:
the ideal is a minimum over the laps you happened to drive, and it falls as they grow. And it
minimises over a noise whose effect this recording cannot size (§1).

### Corner time lost: the Coaching lead, "start with Cn"

**It would be false if** the other half of the same session named another corner first.

| | Sandown 3h | SD_30_08 | MK | SD_19_09 |
|---|---|---|---|---|
| lead · time lost | C1 · 0.229 s | C7 · 0.099 s | C5 · 0.135 s | C1 · 0.147 s |
| lead kept in resamples, fixed / re-selected best lap | 61 / 51 % | 54 / 44 % | 23 / 18 % | 82 / 64 % |
| bootstrap 90 % of the lead's seconds (fixed best) | 0.176–0.309 | 0.070–0.106 | 0.019–0.222 | 0.121–0.195 |
| halves: which corner leads | C1 / C4 | C5 / C7 | C4 / C6 | C1 / C1 |
| halves: the lead's seconds | 0.229 / 0.229 | 0.089 / 0.101 | 0.094 / 0.135 | 0.195 / 0.142 |

**False as a ranking on three of four:** only SD_19_09's halves agree on the lead. The number holds
better than the order: in every half the lead's own seconds stay inside its bootstrap interval
(SD_19_09's odd half at its upper end). The lead is the top of a loose set (ADV-8 below), which the
page already words as a default, not a decision.

### Braking: "on the brakes / lap · median", the Stats tile

**It would be false if** the other half of the session printed a different tile by more than its
sampling spread: halves apart by more than twice the bootstrap sd.

| | Sandown 3h | SD_30_08 | MK | SD_19_09 |
|---|---|---|---|---|
| median · tile | 10.250 · 10.3 s | 9.950 · 10.0 s | 17.800 · 17.8 s | 9.425 · 9.4 s |
| bootstrap sd | 192 ms | 188 ms | 254 ms | 124 ms |
| tile text unchanged in resamples | 12 % | 26 % | 63 % | 57 % |
| halves | 10.600 / 10.200 | 9.950 / 9.951 | 17.825 / 17.800 | 9.400 / 9.525 |

**Holds on three, fails on Sandown 3h**, whose halves are 0.40 s apart against a bar of 0.38 s. On
every recording the tile's last digit is finer than its own sampling spread: resampled, the printed
tenth changes 37 to 88 % of the time. What GPS noise adds to the same number is truth-matrix row 7's,
and TRUTH-7 states it.

### The focus verdict: "improved / slower / unchanged" against a stored corner

**It would be false if** resampling either session's laps, or comparing the halves, flipped the
verdict. The verdict is cross-session, so the probe built the jail's focus list the way the app
builds one. Sandown 3h's shortlist (C1, C4, C7) was pre-promoted on its first open. Each later
Sandown recording was then measured against it and, where the verdict waited on a session record,
"Mark both dry" was clicked, in the jail.

| | SD_30_08 vs Sandown 3h | SD_19_09 vs Sandown 3h |
|---|---|---|
| as the app says it today | no verdict ×3 (no session record) | no verdict ×3 (no session record) |
| C1 after Mark both dry · resamples agreeing · halves | unchanged +0.058 · 95 % · both agree | unchanged −0.029 · 100 % · both agree |
| C4 | improved −0.173 · 79 % · both agree | improved −0.170 · 79 % · both agree |
| C7 | unchanged −0.049 · 99 % · both agree | unchanged +0.004 · 99 % · both agree |

**Holds on 6 of 6 halves.** C4's "improved" is the least firm: one resample in five calls it
unchanged. The SD_19_09 column reproduces `focus.py`'s own table. Sandown 3h is the baseline, so it
has nothing to compare. MK is **not computable read-only**: it is the working set's only session at
its track, and D24, the other MK recording, is off-limits.

## 3. Caveats recorded, no fix

- **ADV-7: which lap was best moves the advice as much as the driving does.** Re-measured on
  Sandown 3h's windows over 7 corners × 2 consecutive Sandown pairs: the best lap's cell moved by a
  mean of 0.082 s, the median by 0.078 s (QA round 4's figures exactly). The focus verdict compares
  medians and is immune. The coaching target and "Replace with today's top 3" read the best lap's
  cell and are not. Pre-promotion and Replace are frozen (DECISIONS.md), and O5 decides.
- **ADV-8: the pre-promoted shortlist is one draw of a loose set.** The same three corners come back
  in 33 / 28 / 8 / 36 % of resamples with the best lap fixed, and in 25 / 18 / 5 / 30 % with it
  re-selected (Sandown 3h, SD_30_08, MK, SD_19_09; QA round 4 read 33 / 26 / 11 / 34 % and
  24 / 17 / 5 / 26 %). No floor fixes it, and O5 decides.

## Reproduce

```bash
pixi run python -m studio.dev.probes.p19_truth_real synthetic   # §1: estimator vs truth (~2 min)
pixi run python -m studio.dev.probes.p19_truth_real real        # §1-§3 on the four recordings
```

The `real` run takes only the four folder names above, writes nothing but stdout and its jail, and
fails if a footage folder or the owner's app-support directory changes while it runs.
