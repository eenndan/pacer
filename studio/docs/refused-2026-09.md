# Features measured and refused — 2026-09

Thirty-seven features were built far enough to **measure**, and the measurement said not to ship
them. The work was real; the evidence lived only in a pull-request body, where nobody re-proposing
the idea would ever look. It is written down here so the next person to suggest one of these starts
from the numbers instead of from the idea.

**These stay refused unless someone brings NEW evidence.** "It would be nice to have" is not new
evidence. What would be: a recording whose numbers come out differently from the ones below, or a
different statistic that answers the same question without the defect named here.

<!-- Numbered 1..N, and the first sentence spells N. tests/test_measured_figures.py fails on a
doubled or skipped number, a count nobody bumped, and a citation of a section that moved. Take the
next free number on your own base; if another PR takes it first, the later one renumbers on merge
and moves its citations. -->

---

## 1. The mistake-hangover detector — refused (#265)

**The idea.** A bad corner makes the *next* corner bad too: you carry the mistake with you for a
few seconds. Detect that and tell the driver about it.

**How it was tested.** Two independent nulls, 20,000 permutations each, family-wise max-statistic
correction over all 12 adjacent corner pairs. The within-lap permutation keeps every lap's own mean,
spread and pace **exactly** and destroys only corner identity — so the obvious confound (a slow lap
is slow everywhere) lives *inside* the null and cannot manufacture significance.

**The result: one pair survives on each recording, and it is a different pair.**

| | 0060 | 0062 |
|---|---|---|
| survivor | C1→C2, ρ +0.581, p_fwer 0.0026 | C8→C9, ρ +0.418, p_fwer 0.0181 |
| the same pair on the other recording | p_fwer 0.126 | p_fwer 0.089 |

Four measured reasons to refuse it:

1. **It does not replicate.** Across the 12 pairs the two recordings agree at r = **+0.234**, matching
   sign on **6 of 12** — a coin flip. Same driver, same track, one day apart.
2. **Adjacency is not a special class.** Residualized |ρ|: adjacent **0.174** vs non-adjacent
   **0.171** on 0062. If "the corner before" carried the effect, adjacency would stand out. It does
   not.
3. **Both survivors have a physics explanation, not a psychological one.** They are exactly the
   pairs whose corner windows sit closer together than the model's own 30 m brake approach — C1 exit
   to C2 enter is 24.0 m / 20.3 m. Braking for corner *n* begins **inside** corner *n−1*'s measured
   window, so exit speed out of one **is** entry speed into the next. The correlation is the
   geometry of the windows, not a driver carrying a mistake.
4. **The threshold form — what a coach actually means — is worse.** On 0060 the strongest result in
   the whole study is C4→C5 at **−0.270 s**: a bad C4 makes C5 *faster*. Zero overlap with 0062, and
   cross-recording r = **−0.109**.

Split-half **within a single recording** gives r = +0.291 on 0062 — unstable even inside one
session.

**The part worth remembering:** raw uncorrected p < 0.05 fired on **3–4 of 12 pairs per recording**.
The max-statistic correction is the only thing that removed them. Without it this would have
shipped, and it would have been noise.

Two premises from the brief were refuted on the way:

- The briefed lap-pace confound is **real but small here** (|ρ| moves ~0.02). The confound that
  actually matters is **corner-window proximity**, which the brief did not name.
- "The spread may be too wide to support the recommendation" — **never happens** on real data: q25 > 0
  on all 23 corner-habits across both recordings, so an abstain gate there would be dead code.

---

## 2. The ideal-lap recombination dotplot — refused (#272)

**The idea.** Draw the ideal lap's uncertainty as a dotplot: sample many recombinations of your own
segments, plot them, and mark where the ideal and your best lap fall.

**What was measured.** The actual 20-dot layout was built, rather than argued about from moments.
Re-measured on the working set on 2026-09-23 (T16b), on the pair that stands where D24's 0060 and
0062 stood: 0068 is `SD_19_09_26` (`GX010068` + `GX020068`) and 0064 is `Sandown 3h 2026`
(`GX010064` + `GX020064` + `GX030064`). Both are timed on the built-in Sandown Park line, which is
the owner's own saved line (Q2, re-measured the same day; until then a fresh library cut them on the
loader's own line, and the ideals read 46.176 and 45.768 s):

| | 0068 (36 laps) | 0064 (62 laps) |
|---|---|---|
| ideal / best lap | 46.196 / 46.808 | 45.809 / 47.076 |
| recombination support | [46.196 … 52.286] = **6.1 s** | [45.809 … 83.666] = **37.9 s** |
| recombination sd | 0.540 (**0.70×** the real lap-time sd) | 2.435 (**0.42×**) |
| best lap's percentile in it | **0.28th** | **0.24th** |
| the 20 dots span | 46.962 … 49.331 | 47.393 … 56.219 |
| **dots at or left of the best lap** | **0 of 20** | **0 of 20** |

The D24 edition it replaces, re-measured after #300 warped every lap (which moved every cell except
the best lap). It was measured before #335 changed corner matching and went stale then: #339 read
0060's ideal as 65.637 s after #335 and 65.864 s after its own change, against the 65.464 s here.
It is kept as the record of that measurement:

| | 0060 (38 laps) | 0062 (65 laps) |
|---|---|---|
| ideal / best lap | 65.464 / 68.228 | 66.709 / 68.201 |
| recombination support | [65.464 … 84.930] = **19.5 s** | [66.709 … 79.418] = **12.7 s** |
| recombination sd | 1.122 (**0.60×** the real lap-time sd) | 0.559 (**0.60×**) |
| best lap's percentile in it | **1.08th** | **0.008th** |
| the 20 dots span | 68.398 … 72.971 | 68.764 … 71.009 |
| **dots at or left of the best lap** | **0 of 20** | **0 of 20** |

As #272 published it, before #300:

| | 0060 (38 laps) | 0062 (65 laps) |
|---|---|---|
| ideal / best lap | 65.149 / 68.228 | 66.781 / 68.201 |
| recombination support | [65.149 … 88.153] = **23.0 s** | [66.781 … 79.980] = **13.2 s** |
| recombination sd | 1.178 (**0.63×** the real lap-time sd) | 0.558 (**0.59×**) |
| best lap's percentile in it | **1.157th** | **0.001st** |
| the 20 dots span | 68.377 … 73.039 | 68.832 … 71.057 |
| **dots at or left of the best lap** | **0 of 20** | **0 of 20** |

**The verdict holds on the working set.** Both reasons below are true of every cell of the first
table: no dot sits at or left of the best lap on either recording, and the spread is narrower than
the laps driven on both. It held on D24 too. Between #272's table and the D24 edition after #300,
the support shrank (23.0 → 19.5 s, 13.2 → 12.7 s) mostly at its slow end, where fewer of the slowest
cells are admitted as donors after #300. The 20 dots, which are what a plot would draw, moved by at
most 0.07 s.

**How it is measured**, since #272 did not write it down. The D24 tables' 0060 is `GX020060` +
`GX030060` and their 0062 is `GX010062` + `GX020062` + `GX030062`. A recombination draws each
corner/straight segment independently and uniformly from the clean laps whose cell MAY DONATE,
which is two conditions and not one: the cell is admitted (`corner_model.MAX_DONOR_SPAN_DEV`) and,
since #339, resolved — its two boundaries matched on track rather than interpolated — with the
model's own two-stage fallback, a segment nothing may donate on dropping back to that segment's
admitted cells. This paragraph said "admitted" alone until H9, while the stand-in that re-measures
the record (`tests/test_measured_figures.py`) has applied both conditions since #339: the published
method was describing a rule the check had stopped using. The D24 cells predate #339; the
working-set table was measured under both conditions. Support is the sum of the per-segment minima
and maxima, so its left end is the ideal. The sd is the square root of the summed per-segment variances, divided by
the sample sd of the clean laps' times. The percentile comes from the exact distribution of the sum
on a 1 ms grid. The 20 dots sit at its (i + ½)/20 quantiles. On #272's own tree this reproduces
every published cell: support and sd exactly, dots within 6 ms, jackknife exactly. The exception is
the percentile, which #272 estimated by Monte Carlo; the exact figures there are 1.173 and 0.002.
`tests/test_measured_figures.py` re-measures the table against the footage and derives the verdict
row from the rest of it.

**Two reasons to refuse it:**

1. **The mark lands off the plot.** On both recordings the cloud centres on the **median** lap, with
   the ideal *and* the best lap stranded off the left edge. The annotation the feature exists to
   carry is outside the plotted body.
2. **The premise is wrong at the root.** A uniform recombination is the **average** stitching, not an
   achievable one, and the central limit theorem over its independent per-segment picks makes its
   spread *narrower than the laps actually driven* (0.42–0.70× the real sd). The plot would
   claim to show what you can do while **understating real lap-to-lap variation**. That is the
   exact overclaim the ideal-lap disclosure exists to prevent.

The brief expected the plot might be "too tight to say anything". The measurement found the
opposite, and worse.

**Two alternatives were measured and also not shipped:**

- **Jackknife** (drop one lap, recompute, partition held): on the working set it moves the ideal by
  at most **0.042 s** and **0.132 s**, with only **13/36** and **13/62** laps moving it at all (on
  D24 after #300: 0.200 s and 0.073 s, 13/38 and 20/65; #272: 0.231 s and 0.135 s, 15/38 and
  18/65). So "is this fragile to one lap?" is already answered — **no** — and a plot to answer it
  would be decoration.
- **Ideal over any N of these laps** does work as a dotplot, but it is a **different statistic**: it
  needs a stochastic accessor with golden-gate care, and the repo has already ruled against baking
  per-recording empirical constants into shipping copy.

**Why the headline ideal needs no uncertainty band at all:** it is exactly the minimum over the laps
driven — an **order statistic**, with no sampling uncertainty over which laps were driven. Its only
instability in that sense is in **N**, and N is a monotone ladder, not a distribution. That ladder
is what `corner_model.IdealSample` publishes and what the Stats page's sample disclosure states.
*Wording corrected 2026-09-29 (TRUTH-1):* it is exact of the laps **as measured**, and every cell
carries GPS measurement noise, which a minimum picks the lucky side of. Against synthetic truth
(`tests/test_truth_matrix.py`, row 3) the ideal reads +7 ms at noise 0 but 161 ms and 698 ms fast
at noise 2 and 4.5 (mean of 3 seeds). That is a bias with one sign, a point correction's job
(TRUTH-11), not a spread a band would draw, so the band stays refused.

---

## 3. A seconds interval on coaching recommendations — refused (#311)

**The idea.** A coaching row claims a median time lost for a corner. Put an interval in seconds
beside it — "worth 0.10–0.18 s" — so the driver knows how much the recommendation is worth.

**Measured on current `main`**, not from any earlier wave's figures: #289 moved the coaching brake
window onto the shared alignment and #300 then removed the drift gate, which reordered the corner
ranking on both recordings. 38 clean laps × 12 corners (0060) and 65 × 12 (0062), through the real
`Session.load` path.

### Three different quantities all get called "the interval", and they disagree by 4×

For the top ranked corner of each recording:

| | 0060 · C12 | 0062 · C3 |
|---|---|---|
| median loss — what the row publishes | **+0.330 s** | **+0.148 s** |
| SPREAD: that corner's own lap-to-lap IQR | 0.331 s | 0.158 s |
| UNCERTAINTY: bootstrap 95 % CI of the median | [+0.250, +0.395] (0.146 wide) | [+0.099, +0.183] (0.084 wide) |
| jackknife, drop one lap | [+0.327, +0.333] (0.006 wide) | [+0.148, +0.149] (0.001 wide) |

The lap-to-lap spread is 2× the CI and 50× the jackknife range. An app that prints one of these
beside a number called "time lost" has to say which, and only the first is already shipped
(`Evidence.iqr`, which the abstain gate reads and the abstain sentence spells out).

### The question an interval has to earn: is the top recommendation separable from the second?

**No — on both recordings, and it fails before any correction is applied.**

| | 0060 | 0062 |
|---|---|---|
| top / second | C12 +0.330 s / C4 +0.244 s | C3 +0.148 s / C12 +0.143 s |
| gap | 0.086 s | **0.005 s** |
| paired within-lap permutation, 20,000, uncorrected | **p = 0.125** | **p = 0.837** |
| the same, max-statistic FWER over 11 comparisons | 0.837 | 0.998 |
| bootstrap (20,000 lap resamples): top keeps the crown | 85.9 % | **53.8 %** (C12 takes 46.0 %) |
| split-half of the SAME session (odd vs even laps) | crowns C4 vs C12 — **disagree** | crowns C3 vs C12 — **disagree** |
| jackknife: does dropping one lap change the crown? | 0 of 38 | 0 of 65 |

The jackknife row is the trap: "is this fragile to one lap?" answers **no** on both recordings, and
it is the wrong question — the crown is decided by many laps at once, which is what the bootstrap
and the split-half see.

**The ranking itself survives; only its top is unsupported.** 17 of the 30 ranked pairs separate at
p < 0.05, and the top corner is separable from 8 of 11 (0060) and 7 of 11 (0062) other corners at
FWER 0.05. And the ranking does not replicate ACROSS recordings at all: loss-vs-loss r = **−0.122**,
rank ρ = **−0.196**, top corner C12 vs C3 — same driver, same track.

### T4's own proposal — the observed-benefit partition — is refuted

"On the laps that already did the recommended thing, the corner's time vs the laps that did not",
measured per ranked row (laps split at that corner's own median brake point / coast / apex speed;
5,000-permutation label null; `pace-adj` removes each lap's own pace by regression):

| recording · corner | reason | n did / not | benefit | perm p | pace-adj | lap-time gap |
|---|---|---|---|---|---|---|
| 0060 C12 | braking | 19 / 19 | **+0.122 s** | 0.066 | +0.104 | **−0.295 s** |
| 0060 C4 | braking | 19 / 19 | **−0.085 s** | 0.794 | −0.076 | +0.154 s |
| 0060 C9 | apex | 19 / 19 | +0.023 s | 0.750 | +0.026 | −0.121 s |
| 0060 C6 | braking | 19 / 19 | +0.043 s | 0.549 | +0.046 | −0.024 s |
| 0062 C3 | braking | 22 / 22 | +0.008 s | 0.823 | +0.026 | **−0.382 s** |
| 0062 C12 | braking | 33 / 32 | **−0.032 s** | 0.488 | −0.046 | +0.211 s |
| 0062 C6 | braking | 33 / 32 | −0.018 s | 0.539 | −0.055 | −0.037 s |
| 0062 C5 | braking | 33 / 32 | +0.040 s | 0.088 | +0.047 | +0.117 s |
| 0062 C8 | apex | 33 / 32 | +0.054 s | **0.0002** | +0.059 | +0.141 s |

The sign flips: on each recording one of the two top rows says the laps that DID the recommended
thing were **slower** through that corner. On the top row of each recording the group's lap-time gap
(−0.295 s, −0.382 s) is larger than the benefit itself — those laps were simply faster laps, which
is the confound #265 already named. One partition of the nine is significant, and it is a 6th-ranked
apex row, not a braking one. A range built on this would print a number whose **sign** is not stable.

Two rows carry `REASON_LINE` (the spread fallback, named `REASON_CONSISTENCY` since COACHING-5),
which has no behavioural lever at all, so the partition cannot even be formed for them — a "range"
feature that abstains on the reason the app falls back to is not a feature.

### Controls

- **Negative control** (corner labels shuffled *within* each lap — every lap keeps its own pace and
  spread exactly, only corner identity dies; 300 replicates): "the top corner is separable from
  *some* other corner" fires **19.3 % / 20.7 %** of the time, not 5 %. The max-statistic correction
  covers the 11 comparisons but **not the selection of which corner is top**. The top-vs-second
  comparison fires **0 %** under the same null, and that is the comparison the verdict above rests
  on — so the verdict is on the conservative side of its own control.
- **Positive control**: +0.25 s planted on the second-ranked corner is recovered exactly (+0.250 s)
  and the bootstrap CI covers the planted size, but it reaches only p_fwer 0.645 (0060) / 0.096
  (0062) against the old top. The family-wise test is **too blunt for adjacent ranks**, which is the
  second reason the verdict above is quoted on the *uncorrected* p — where it already fails.

### Why the modelled form is the wrong answer here even where it is tight

The coaching surfaces already publish an **observed** middle half and say so in as many words
("your observed spread, not a modelled margin", the brake-point tooltip). A bootstrap CI would be
the only modelled margin on the page, and it would sit next to a row whose claim is already gated
by the observed spread (`SPREAD_MARGIN`). Two spread numbers, one observed and one modelled, on one
row, is the disagreement this app keeps paying to remove.

### What shipped instead

The measurement found a live overclaim rather than a missing feature: the theme's second action
crowned **one** corner ("Start with C3: +0.15 s") on a recording where the runner-up is 0.005 s
behind and takes the crown in 46 % of resamples. That sentence now names both corners when their
gap is inside the pair's own lap-to-lap spread — `gap < SPREAD_MARGIN × min(IQR)`, the same
actionability margin the per-row gate already applies, on numbers the row already carries (no
modelled interval, no new field, no golden leaf). Validated against the permutation ground truth on
all 30 ranked pairs of both recordings: **0 misses** (it never stays silent on a pair the
permutation calls a tie) and 2 over-calls, both marginal (p = 0.039 and p = 0.066).

**Revised 2026-09-29 on short-session evidence (ADV-2, QA round 4); the interval stays refused.**
Half a spread has no lap-count term, and on the 19-lap MK_18_09_26 the rule named C5 without C8
(gap 0.071 s against a 0.054 s bar) where the same permutation test calls that lead pair a tie
(p = 0.190). The tie margin is now the wider of half the smaller spread and 1.5 standard errors of
the pair's difference (each median's from its own IQR and lap count), the latter capped at the
wider spread so the sentence's "closer together than your own lap-to-lap spread" stays true. On
the 22 ranked pairs of the four working-set recordings (19-62 laps): misses 2 → 1 (SD_19_09_26 C5
vs C2, p = 0.151, not a lead pair), over-calls 4 → 4; the D24 figures above are history. The
standard error only widens a tie: nothing new is printed, so no interval reaches the page.

---

## 4. A per-corner GPS-quality abstain — refused (#255, re-measured here)

**The idea.** `coaching.corner_evidence` refuses to make a claim about a corner with too few clean
instances (`ABSTAIN_FEW_LAPS`), with no second lap at the target (`ABSTAIN_ONE_OFF`), or whose claim
is inside the corner's own interquartile spread (`ABSTAIN_SPREAD`) — but never because the GPS was
degraded *through that corner*. Add a fourth reason keyed on fix quality inside the corner window.

**It was refused once already, in #255, on a different statistic** (the interior sample gap), **and
that evidence lived only in the pull request** — which is the exact failure this file exists to fix.
So it is re-measured here at the cell such a gate would actually key on: one (clean lap × corner)
window, over both recordings and all ten bundled samples, via `studio/dev/probes/p4_corner_gps_quality.py`.

|  | 0060 | 0062 |
|---|---|---|
| clean laps × corners = cells | 38 × 12 = **456** | 65 × 12 = **780** |
| cells inheriting a class below `GOOD` | **26** (5.7 %) | **0** |
| cells containing a **rejected** fix | **0** | **0** |
| worst kept DOP per cell (median / max) | 2.60 / **8.96** | 1.62 / **4.80** |
| cells above `DOP_GOOD_MAX` (5.0) | 26 | **0** |
| whole-recording verdict | `gps9_trueclock`, 0 % rejected, **not degraded** | `gps9_trueclock`, 0 % rejected, **not degraded** |
| per-LAP, the DATA TRUST card's row | 17 of 38 below good | 0 of 65 |

**Three measured reasons to refuse it** (a fourth this section used to give is corrected below):

1. **The proposed key is identically zero.** Keyed on *degraded fixes inside the corner window* —
   the form the backlog specified — it fires on **0 of 1,236 cells across both recordings**. Not one
   rejected fix falls inside any corner window of either recording. 0060 rejects no fix at all, and
   every one of 0062's 482 rejections is in the 48 s of lock acquisition before the kart moves. The
   gate as specified is unreachable code.
2. **The only key that CAN fire was already refused one level up.** The reachable signal is the DOP
   band, and it fires on 0060 only — 26 of 456 cells, on a recording the app itself reports as `0 %
   of moving fixes rejected` and **not degraded**. That is precisely the case the `[u]` block in
   [`studio/data_quality.py`](../data_quality.py) already measured and refused at LAP level: the
   same recording puts 17 of 38 clean laps below good, and marking them "would mark 45 % of the rows
   of a recording the app itself reports as clean". A per-corner abstain is that same invention one
   level down. **The codes follow the app's verdicts; they do not add to them.**
3. **It does not replicate.** 0060 has real structure — C8 18.4 %, C10 15.8 %, C9 and C11 10.5 %,
   C6 7.9 %, and **five corners at exactly zero**. 0062 has nothing at all: 0 of 780 cells, worst
   kept DOP anywhere **4.80**, below the 5.0 threshold at every corner of every lap. Same driver,
   same track, one day apart. So the corner a per-corner reason would name is a property of one
   afternoon's sky, not of the corner — and a driver who was told "C8 is unreliable" on Saturday
   would be told nothing at all about it on Sunday.
**Corrected (T10): the clock crossing decides one cell, not a third of them.** This section first
gave a fourth reason: that indexing the 456 cells on the media clock moved **9** of them across a
class boundary, "about a third of the 26 cells that fire", because a corner window (~3-6 s) is so
much shorter than a lap (~70 s). **That comparison crossed the wrong map.** It used
`Session.media_time`, which since #301 also carries the GPS lag (+0.476 s on 0060) — a correction
for the *picture*, not part of the strip's axis, since a fix's telemetry and naive stamps carry it
equally. Measured per kept fix against the naive stamps the strip actually binned
(`studio/dev/probes/p5_clock_crossing_scale.py`):

|  | 0060 (456 cells) | 0062 (780 cells) |
|---|---|---|
| telemetry label, as `p4` grades them | **1** wrong | 0 |
| rate fit alone, `media_clock.without_gps_lag()` | **0** wrong | 0 |
| rate fit + GPS lag, `Session.media_time` (the old figure) | **10** wrong | 0 |
| cells below `GOOD` against the fixes' own stamps | **27** (the label says 26) | 0 |

The one cell is lap 31's C8, which the label grades Good and the fixes' own stamps grade Moderate —
the misaligned join looked *cleaner*, not dirtier. Nor is window length what separates the corner
figure from the lap one: how often a window flips is ~0.1 % at every length from 0.1 s to 20 s on
0060; what length changes is the share of below-good verdicts that are wrong (0.07 % at 70 s, 1.1 %
at 10 s, 2.6 % at 3 s). The full rule is in `Session.quality_timeline`. **The refusal stands on the
three reasons above**, none of which rests on the clock.

**No fixture outside D24 can reach it either.** All ten bundled samples come back with **0 corners
and 0 clean laps** — most are a few seconds long, and `karma.mp4` carries no GPS at all
(`NO_GPS_TRACE`). A synthetic-only gate would therefore be a reason no shipped fixture exercises,
gating a claim no real recording asked it to gate.

**#255's own two numbers reproduce exactly**, and they are the reason the window is thin enough for
all of this to be marginal: the worst interior sample gap inside any corner window on any clean lap
is **0.1010 s** on both recordings — exactly the 10 Hz fix period, i.e. no interior gap at all — and
the thinnest window is **20 samples** (0060) / **22** (0062).

**What would be new evidence:** a recording that rejects fixes *while moving* inside a corner
window, or one whose degraded corners are the same corners on two different days. Neither exists in
anything this repo can currently load.

---

## 5. A minimum segment length for the ideal-lap partition — refused (M4)

**The idea.** 16 of the 0060 pair's 23 real segments have a donor-admission band (±5 % of the
segment's own span) narrower than the ±3 m a boundary match is guaranteed to, so admission on those
segments is decided by noise — the pair's 2.25 m C2-C3 sliver refuses laps arbitrarily. Give the
partition a **minimum segment length** so those slivers are never cut, and the noise goes away.

**How it was tested.** The real `CornerModel` driven over each fixture's own per-lap arrays — the
stand-in reproduces `Session.ideal_total()` to 1e-9 on all three before anything is varied — with
the admission **bootstrapped over each boundary's OWN measured residual**: every projected edge is
re-drawn from the residual distribution measured at *that* edge, the admission is recomputed, and
the segment times are left alone, so every move in the ideal is the admission decision moving.
Fixtures: `GX020060+GX030060` (38 clean laps), `GX020060` alone (24), `GX010062+GX020062+GX030062`
(65).

**The premise's yardstick is the wrong one.** `corners.SPATIAL_MATCH_MAX_M` is the worst case a
match may *pass*, not the error it carries. Since #300 warped every lap, the measured per-boundary
longitudinal residual is one to two orders of magnitude under that gate:

| | 0060 pair | 0060 ch 1 | 0062 |
|---|---|---|---|
| boundary residual, median | 0.099 m | 0.077 m | 0.010 m |
| segments with band < 3 m — *the premise* | 16 / 23 | 16 / 23 | 17 / 23 |
| segments with band < their own residual | **7 / 23** | **4 / 23** | **0 / 23** |
| refused cells | 170 / 950 | 117 / 600 | 33 / 1625 |
| refusals within one cell-residual of the threshold | 41 % | 38 % | 9 % |
| the named sliver (C2-C3, 2.25 m) | 6 of 38, 5 marginal | 3 of 24, 3 marginal | **0 of 65** |

On the 65-lap recording the app headlines, the defect class does not exist.

**The candidate, built and measured.** Absorb every straight under 5 m into the corner before it,
so no sub-sample segment is ever cut. Corner and segment counts stay 12 and 25 — merging the corner
*pair* instead is a re-cut, and a coarser partition raises the ideal by +0.2 … +1.5 s for the
reason `corner_model.IdealSample`'s last paragraph already gives.

| | 0060 pair | 0060 ch 1 | 0062 |
|---|---|---|---|
| ideal, shipped | 65.464 s | 66.450 s | 66.709 s |
| ideal, candidate | 65.500 (**+0.036**) | 66.479 (**+0.030**) | 66.735 (**+0.026**) |
| admission-noise sd, shipped → candidate | 0.294 → **0.289** | 0.324 → **0.325** | 0.005 → **0.006** |
| …sub-resolution segments held instead | 0.183 | 0.214 | 0.002 |
| best lap's time-in-corner moves | +0.29 s on 5 of 12 | +0.29 s on 3 of 12 | +0.24 s on 5 of 12 |

**Four measured reasons to refuse it:**

1. **It does not remove the noise it was proposed for.** The admission's own spread is unchanged to
   three decimal places, and on two of three fixtures the candidate is very slightly *noisier*.
2. **The noise is not a property of short segments.** Holding the sub-resolution segments at their
   shipped decision takes the pair's spread 0.294 → 0.183 s, so they carry about a third of it; the
   worst-flipping segments include a **17.3 m** and a **61.6 m** one, because the pair's badly
   matched boundaries sit around C7–C9 (median residual 2–3 m) and not on its slivers. A minimum
   segment length cannot reach those.
3. **The move is far inside the number's own width.** +0.026 … +0.036 s against the ±0.25 s
   cross-fixture spread `MAX_DONOR_SPAN_DEV` already documents, and against 0.72 / 0.60 / 0.36 s for
   a doubling of N on these three fixtures (arbiter refit per candidate). It would move the app's
   headline claim without improving it.
4. **Nothing is missing from the reader's surface today.** On all three fixtures the best lap is
   admitted on all 25 segments, so no row is dropped from the Stats decomposition either way. The
   price, in exchange, is a quarter-second change in a displayed time-in-corner on up to 5 of 12
   corners and a move in every corner-derived leaf in the app.

**Premises refuted on the way:**

- The backlog's "segment 4 refuses **4 of 38** laps" is pre-#300; it refuses **6 of 38** today, and
  **0 of 65** on the other recording.
- The two exemption rows in `corner_model.MAX_DONOR_SPAN_DEV`'s block were also pre-#300 (the wide
  one read −0.984 / −1.066 / −0.217, and reads −1.200 / −1.080 / −0.184 today). They are refreshed
  in that block, which is where the next person will look.

---

## 6. An overdriving detector — refused (F4)

**The idea.** Overdriving is carrying more speed into a corner than lets you get out of it fast.
Detect it per corner from the laps you already drove: if the laps that entered a corner faster
were the laps that got through it slower, name that corner.

**What was built.** The backlog's form, exactly: over the clean laps, the Spearman ρ of entry speed
(`CornerStat.entry_speed`, read at the corner's projected enter boundary) against time in the
corner; a within-lap permutation null (each lap's corner labels shuffled, so a lap that is fast
everywhere stays fast everywhere and only corner identity dies); max-statistic FWER over the
corners; an 8-lap gate. It was also run on two other outcomes a coach would accept as the cost of
overdriving: the corner plus the straight after it, and the exit speed. 20,000 permutations each,
through the real `Session.load` path on current `main`, by `studio/dev/probes/p7_overdrive.py`:
D24 0060 (38 clean laps × 12 corners), D24 0062 (65 × 12), Sandown 09-05 (59 × 7), SD 30-08
(37 × 7) and Sandown 3h (62 × 7). Corner identity across recordings of one track was checked by apex
position: C1…C12 on the two D24 days sit within 8.8 m of each other, and C1…C7 on the three Sandown
days within 4.5 m.

### As proposed, it names no corner on any recording

| recording | time in corner | corner + next straight | exit speed | largest ρ on the backlog's form |
|---|---|---|---|---|
| 0060 | — | — | **C9** | C4 +0.36, p_fwer 0.301 |
| 0062 | — | — | — | C9 +0.03, p_fwer 1.000 |
| Sandown 09-05 | — | — | — | C2 +0.14, p_fwer 1.000 |
| SD 30-08 | — | — | — | C6 −0.09, p_fwer 1.000 |
| Sandown 3h | — | — | — | C6 −0.31, p_fwer 0.991 |

One overdriven corner in 135 corner-outcome cells, and none on the backlog's own outcome. What the
detector does find is the opposite: faster in, faster through (0060 C1; 0062 C3, and C7 and C11 on
exit speed). A faster lap produces that, and so does reading a boundary inside a braking zone: arrive
faster and you cover the first metres of the window sooner. It is not the claim.

### The one overdriven corner is where the boundary was measured badly

0060 C9 on exit speed: ρ +0.50, p_fwer 0.041. Every entry and exit speed is read at a projected
boundary, and C9's enter boundary sits in a braking zone where speed falls by about a km/h per metre.
The probe measures where that boundary lands on each lap, against the reference lap's own point:

| C9 | 0060 | 0062 |
|---|---|---|
| enter boundary is a spatial match (not interpolated) | **26 %** of laps | 100 % |
| longitudinal residual of the projected enter point, sd | **3.10 m** | 0.01 m |
| speed gradient there | −0.89 km/h per m | −0.97 km/h per m |
| entry-speed error that residual alone implies | **2.77 km/h** | 0.01 km/h |
| entry-speed spread across the laps (sd) | 2.66 km/h | 1.91 km/h |

On 0060 the placement error by itself is as large as the spread it is supposed to measure. It is not
independent of the outcome either: the enter residual correlates **+0.41** with the exit speed.
Re-read at the point level with the reference lap's point, the two readings of the same 38 laps'
entry speed agree only at ρ +0.49, and the exit-speed relation falls from **+0.50 to +0.05**
(p_fwer 1.000).

**The re-read has an artifact of its own, with the same sign.** Re-read level, C9 is named on time in
the corner (ρ +0.64, p_fwer 0.001) and on corner + straight (+0.49, p_fwer 0.041). That is the
window's own length. Between the two re-read ends it measures 42.3 m, with sd **3.74 m** and range
**33.6–49.0 m**; on 0062 it is 39.3 m with sd 0.95 m. ρ(path, time) is +0.89 and ρ(entry, path)
+0.67; holding the path length, the time relation is **+0.12**. No driving line makes a 42 m corner
15 m longer on one lap than another. That spread is where the GPS put the two ends.

So each reading of that corner carries a measurement artifact that lands on the overdriving sign,
and neither is named under the other reading. The same corner, same driver, next day, with every
boundary matched: 0062 C9 reads +0.03 / +0.12 / +0.18 on the app's readings and +0.03 / +0.13 /
+0.19 re-read (time in corner / corner + straight / exit speed; p_fwer ≥ 0.93 on all six). Inside 0060 itself, the backlog's form names C9 in neither half and in
neither the odd nor the even laps; exit speed names it in the even laps only.

C9 is not the only exposed corner on 0060. The same arithmetic gives C11 2.91 km/h of implied entry
error against a 5.15 km/h spread, C5 2.51 against 2.98 and C7 1.94 against 2.97. On 0062 and all
three Sandown recordings no enter boundary exceeds 0.59 km/h (Sandown 3h C4).

### Controls

**Negative control** — entry speeds shuffled across laps, 300 replicates × 1,000 permutations, on
time in corner and on exit speed. It names an overdriven corner **0.7–3.7 %** of the time and names
any corner 2.7–6.3 %. The flag is the maximum over every corner, so choosing which corner to name is
inside that rate. (#311's selection problem was a different question: is the top corner separable
from *some* other.)

**Positive control** — an overdriving cost planted into each corner in turn, on the laps as driven:
the corner's time grows by β × (that lap's entry speed − the mean). Corners named:

| β, s per km/h | 0060 | 0062 | Sandown 09-05 | SD 30-08 | Sandown 3h |
|---|---|---|---|---|---|
| 0.01 | 0 / 12 | 0 / 12 | 0 / 7 | 0 / 7 | 0 / 7 |
| 0.02 | 2 / 12 | 0 / 12 | 0 / 7 | 0 / 7 | 0 / 7 |
| 0.04 | 4 / 12 | 8 / 12 | 0 / 7 | 1 / 7 | 0 / 7 |
| 0.08 | 9 / 12 | 11 / 12 | 3 / 7 | 4 / 7 | 2 / 7 |

At 0.04 s per km/h, a lap that enters 2–3 km/h faster (about one lap-to-lap spread at most corners)
loses about 0.1 s in that corner. A coach would point that out at once, and the detector names it on
**13 of 45** corners. On Sandown 3h the laps' own entry speeds already go with faster corners at every
corner (ρ −0.31 to −0.52). There a planted 0.04 is invisible on all 7 corners, and 0.08 is found on 2.

**Covariate cross-check** — a partial rank correlation given the rest of the lap, lap order and
stint, in place of the within-lap null. It names no overdriven corner either. Its placebo pairs a
corner's entry speed with a non-adjacent corner's time, where no causal path exists, and names a
corner in the faster-in, faster-elsewhere direction **19.0–34.0 %** of the time (0 % on SD 30-08).
The rest of the lap, lap order and stint do not remove lap-level pace. The within-lap null does,
which is why it is the form measured above. It is also why its power is what the table says.

### Replication across recordings of one track

The per-corner ρ on time in corner agrees between 0060 and 0062 at r = +0.48 (same sign on 8 of 12).
That agreement is almost entirely in the faster-in, faster-through direction. On exit speed it is
+0.07. Across the three Sandown days the agreement on time in corner is −0.08, −0.01 and +0.65.

### Three measured reasons to refuse it

1. **As proposed, it names nothing.** No corner on any of five recordings on the backlog's outcome,
   and one corner-outcome cell of 135 on any outcome.
2. **Its silence means nothing.** A cost of 0.1 s for a lap that enters one spread faster
   (0.04 s per km/h) goes unnamed on 32 of 45 corners, 20 of the 21 at Sandown. "No corner is
   overdriven" would be printed about laps that could not have shown it.
3. **The one corner it names is the measurement.** Two different placement artifacts put the same
   corner on the overdriving sign, and neither is named under the other reading. The corner does
   not replicate on the next day's recording, where every boundary is matched.

**What would be new evidence:** a recording whose enter boundaries are spatially matched on nearly
every lap, as on 0062 and the Sandown sessions today, where a corner is named on the backlog's form
in both halves of the session with a window path-length spread near 0062's 1 m. An entry-speed
measurement that does not depend on where a boundary lands would also count, provided a planted
0.04 s per km/h is named on most corners.

**Premises refuted on the way:**

- The brief expected the effect to be confounded by line, lap in the stint, tyre state and traffic.
  The lap-level ones are absorbed by the within-lap null. The confound that decided the only named
  corner was **where the boundary lands inside a braking zone**, which the brief did not name. This
  echoes #265, where corner-window proximity mattered more than lap pace.
- #311's warning that the max-statistic correction does not cover selection does not apply to this
  flag, which is itself the maximum over all corners: the shuffled-label control names an
  overdriven corner at 0.7–3.7 %.

---

## 7. A background batch-import queue for a multi-recording drop — refused (F3)

**The idea.** Dropping several unrelated recordings opens the first and tells the user to open the
rest one at a time, so the library and the personal-best history only learn about the others by
hand. Give the drop a background queue that loads each remaining recording **headless into the
library, not into the window**, reusing `Session.load` and the library upsert; cancellable; one
summary line at the end. The backlog rated it *"low value per unit work"*.

**How it was tested.** The real drop path, driven over the owner's four footage folders exactly as
a folder drop reaches it — `StudioWindow._dropped_mp4s` on a `QMimeData` carrying the folder URL,
then `chapters.group_into_recordings` — and then the real `Session.load` on every recording the
queue would have imported. Read-only throughout; all four folders were size/mtime-snapshotted before
and after and were unchanged, and every app-support seam was diverted through `studio/dev/_jail.py`.

### The gesture is not rare — but four fifths of what the queue would import is Pacer's own output

| dropped folder | .MP4 files | recordings | opened in the window | **queued by F3** |
|---|---|---|---|---|
| `D24` | 6 | 2 | recording 0060 · 3 chapters | recording 0062 · 3 chapters |
| `Sandown_09_05_2026` | 3 | 1 | recording 0059 · 3 chapters | — |
| `SD_30_08_26` | 4 | **3** | recording 0065 · 2 chapters | `GX010065_lap22_overlay.mp4`, `GX010065_lap23_overlay.mp4` |
| `Sandown 3h 2026` | 5 | **3** | recording 0064 · 3 chapters | `GX010064_lap30_overlay.mp4`, `Sandown_3h_2026.mp4` |

So the brief's "rare gesture" is **wrong** — three of the owner's four folders are a multi-recording
drop today. But `group_into_recordings` parses filenames and nothing else, and the app's OWN exports
live in the footage folder, because `_export_default` saves next to the recording
(`export_controller.py:157-163`, suffix `_overlay.mp4` at `:756-757`). Handing those five queue
candidates to the real loader:

| queued candidate | size | `Session.load` |
|---|---|---|
| recording 0062 (3 chapters) | 34.7 GB | **OK in 2.44 s, 65 valid laps** |
| `GX010065_lap22_overlay.mp4` | 0.1 GB | `RuntimeError: Failed to open file`, 0.00 s |
| `GX010065_lap23_overlay.mp4` | 0.1 GB | `RuntimeError: Failed to open file`, 0.00 s |
| `GX010064_lap30_overlay.mp4` | 0.1 GB | `RuntimeError: Failed to open file`, 0.00 s |
| `Sandown_3h_2026.mp4` | 8.5 GB | `RuntimeError: Failed to open file`, 0.00 s |

**One of five candidates is a real recording.** The other four carry no telemetry — two of them are
overlay clips Pacer rendered from the very recording it had just opened. The whole measured yield of
the feature, across every recording the owner has on this machine, is **one library row**.

### The row it would produce is real — and the drop that already works produces the same one

This is the half of the backlog entry that holds up, so it is worth stating plainly. Loaded headless
with an **empty** track database, D24 0062 comes back `track='Daytona Milton Keynes'` (matched from
the built-in registry), `verified=True`, `degraded=False`, `lap_count=65`, `best=68.20060941901284`,
`library.is_trustworthy` **True**, `trust_label` None — a fully PB-eligible entry, not the grey
"unknown track · provisional" row that would have made the feature pointless. Saving the track first
and re-loading changes only the name and the last two digits of the best (`68.20060942531973`).

But `_open_recordings` computes `to_load` the same way for a recording dropped alone as for one in a
crowd (`app.py`, `order_chapters(first + discover_siblings(first[0]))`), so **dropping the second
recording on its own produces a byte-identical entry** — two independent loads of that chained path
agreed on `best` to all 17 digits. The queue does not buy a row the user cannot otherwise get, and it
does not buy it sooner in wall-clock terms either: the load costs the same 2.3 s whoever asks for it.
What it buys is **one drop gesture**, for a recording the user just dropped because they intend to
open it.

### "Cancellable" is machinery for an operation shorter than finding the cancel control

`Session.load` over the 34.7 GB, three-chapter recording 0062: **2.32 s, 2.22 s, 2.44 s** across
three runs. A three-recording queue finishes in about seven seconds. The app has nowhere to put a
cancel control for that — the status bar is not interactive, and the only precedent
(`_cancel_demo`) is a welcome-screen button that exists because a demo fetch is an unbounded
network download with no upper bound on its duration.

### The summary line would be a report about the app's own exports

F3 asks for "one summary line at the end", and partial failure is the normal case. On
`SD_30_08_26` that line reads *"2 of 2 recordings couldn't be read"* — about two overlay clips
Pacer produced from the recording it had just opened. That is a worse message than the one it
replaces, and there is no phrasing that rescues it, because the premise it is reporting on is wrong.

### Three more costs, for completeness

- **It widens a store with a live incident this month.** `update_library` reads `self.win.session`
  in three places plus `_library_excludes`, so a headless importer means parameterising the
  load-time library writer on a session that is not the window's. That store was polluted for real
  on 2026-09-17 and only jailed by #328; a second, unattended writer into it is new risk against
  one measured library row.
- **It needs its own token space.** Every `_load` does `_load_token += 1`, and so does
  `closeEvent`, so a batch worker parked in `_load_workers` would be silently superseded the moment
  the user opened anything else — the one thing a background import must survive.
- **It converts a drop from "open what I picked" into "read every MP4 in this folder"**, and the
  owner's folders hold 8.5 GB derived edits beside the footage.

### What shipped instead

Measuring the queue found the defect it was proposing to build on top of: **the drop message
over-counted the owner's recordings and then sent him to open files the loader refuses.** The
question the drop actually needed answering was not "import these for me" but "is this a recording
at all", and that question is **cheap**. `pacer.GPMFSource`'s constructor is the loader's own first
gate (`pacer/gps-source/gps-source.cpp:17-23`); it reads the moov atom, not the payloads:

| file | size | `pacer.GPMFSource(path)` |
|---|---|---|
| `GX010062.MP4` | 11.9 GB | 5.23 ms — opened |
| `GX020062.MP4` | 11.9 GB | 1.64 ms — opened |
| `GX010065_lap22_overlay.mp4` | 0.1 GB | 0.78 ms — refused |
| `Sandown_3h_2026.mp4` | 8.5 GB | 1.32 ms — refused |
| `GX010060.MP4` (the destroyed stub) | 2.4 MB | 0.42 ms — refused |

**0.4–5.2 ms, against 2,300 ms plus a thread, a queue, a token space, a cancel affordance and a
summary line.** So `ingest.carries_telemetry` / `recording_carries_telemetry` answer it on the UI
thread during the drop itself, and `_open_recordings` counts only the recordings it could actually
offer. An unreadable file still counts — "I could not read it" is not a verdict on its contents,
the same three-answer discipline `chapters.probe_mp4` already keeps.

**What would be new evidence:** a user whose card folders hold several real recordings and few or no
Pacer exports, for whom the first table's one-in-five becomes most-of-five; or a measurement showing
the second drop gesture is a real barrier rather than a keystroke. Note that the cheap gate shipped
here is also what a future queue would want as its admission test, so it has been paid for already.

---

## 8. A heat-to-heat comparison against the previous comparable session — refused (F8)

**The idea.** The library stores each session's best lap, ideal lap and lap count, but not its
typical pace, so it cannot answer "was this session better than the last one I can compare it
with?". Store the median lap, σ and top speed on every entry (a schema bump from v3, with a
migration of the owner's live `library.json`). Show the change only against a session that the
session record (#258) says was comparable, using the refusal rules the focus list already applies
(`focus.verdict`).

**How it was tested.** `studio/dev/probes/p10_heat_to_heat.py` loads the owner's three present
Sandown recordings through the real `Session.load`, all chapters. Every app-support seam is jailed,
and the owner's `tracks.json` is copied into the jail so the track and start line are the ones his
own app uses. The footage folders and the real app-support directory were size/mtime-snapshotted
before and after, and were unchanged. The comparison is `focus.verdict` itself, with the whole lap
as its window: the baseline is the earlier session's clean-lap median and interquartile range
(IQR), and the sample is the later session's. It runs twice: once against the owner's real
session-record store, and once against a planted pair of records that agree ("dry" on both).

| recording | date | clean laps | median | IQR | σ | best | top speed | lap length | runs (laps @ median) |
|---|---|---|---|---|---|---|---|---|---|
| 0064, Sandown 3h | 19 Jul | 62 | 48.202 | 2.846 | 5.816 | 47.076 | 86.1 km/h | 730.6 m | 2 @ 65.828, 6 @ 49.220, 22 @ 47.795, 32 @ 48.080 |
| 0065, SD 30-08 | 30 Aug | 37 | 47.566 | 0.776 | 1.681 | 46.912 | 87.4 km/h | 737.9 m | 12 @ 48.052, 25 @ 47.360 |
| 0068, SD 19-09 | 19 Sep | 36 | 47.435 | 0.520 | 0.767 | 46.808 | 87.0 km/h | 737.3 m | 11 @ 47.697, 25 @ 47.370 |

All three have a trusted start line and a clock that is not estimated. Each best lap is identical,
to every stored digit, to the one on the owner's library row for that recording.

### Today it refuses every pair, and always for the same reason

On 2026-09-19 the owner has no `session_records.json` at all. So every pair is refused because there is no
session record. Every pair passes the four gates checked before that one: same track, a trusted
start line on both sides, neither clock estimated, and lap lengths within 1.01 % of each other
(the limit is 2 %).

| pair | previous session? | the verdict today | with records that agree |
|---|---|---|---|
| 0064 → 0065 | yes | no verdict: no session record | unchanged |
| 0064 → 0068 | no | no verdict: no session record | unchanged |
| 0065 → 0068 | yes | no verdict: no session record | unchanged |

A comparison in the Library would therefore print the focus list's own refusal on every row the
owner has: *"There's no session record for 30 Aug and today…"*. The focus list already prints that
line for corners, along with the fix (File ▸ Session record…). A second surface would repeat it.

### With the records written, it would say "no change you can act on" three times out of three

| pair | Δ median | bar (½ × wider IQR) | Δ / bar | SE of Δ | Δ / SE | Δ best | Δ top speed |
|---|---|---|---|---|---|---|---|
| 0064 → 0065 | −0.636 | 1.423 | 0.45 | 0.356 | 1.8 | −0.163 | +1.3 km/h |
| 0064 → 0068 | −0.766 | 1.423 | 0.54 | 0.345 | 2.2 | −0.268 | +0.9 km/h |
| 0065 → 0068 | −0.131 | 0.388 | 0.34 | 0.143 | 0.9 | −0.104 | −0.4 km/h |

The bar is the one the focus list applies to a corner, `coaching.SPREAD_MARGIN` × the wider of the
two IQRs. The standard error (SE) uses the normal approximation that `focus.verdict`'s comment
uses, 1.2533 × IQR / 1.349 / √laps, for each median.

- **Every change is inside the bar,** at 0.34 to 0.54 of it.
- **The one pair with the same shape of day cannot be told from zero.** 0065 → 0068 is two runs
  each, 11–12 laps and then 25. Its 0.131 s is 0.9 standard errors. The two 25-lap runs are
  47.360 and 47.370 s, 0.010 s apart. All of the session-level change comes from the short first
  run (48.052 → 47.697 s). A session median measures how much of the day was a warm-up run as much
  as it measures pace.
- **The endurance recording cannot be compared this way.** 0064's first run is 2 laps at a
  65.8 s median. That stretches its σ to 5.8 s and its IQR to 2.85 s, which puts its bar at
  1.42 s, 3.7 times 0065's. Almost no change would clear it.
- **The sizes are small next to the conditions.** The largest change here is 0.77 s. The coach
  quoted in `session_record.py` puts up to 4 s a lap on temperature and humidity alone.
- **Top speed moves by 0.4–1.3 km/h,** about 1.5 % at most. A driver changes it through gearing, and
  gearing is in the session record, which no session has.

**What a driver could act on: nothing at the session level.** The best-lap change is already on
the Library's PB chart (46.912 → 46.808 s). The comparison a driver can act on is per corner, and
the focus list already makes it, behind the same gate.

### What building it would have cost

- **A migration of the owner's live library for fields no real pair could use.** The library has
  8 entries at v3, and 4 of them point at recordings that are gone: three rows for D24 0060 and one
  for Sandown 09-05. Their new fields would stay empty for good. That leaves Milton Keynes with one
  present recording (0067) and so no pair at all. This package changed nothing in `library.py`,
  and the owner's `library.json` is untouched.
- **A backup-slot hazard, for whoever bumps the schema next.** `library.json.bak` is also the slot
  `clear` writes and `restore` reads. A migration backup written there would replace the backup of
  a cleared library. A future bump should write a version-named copy instead. (An older build
  reading a newer file is already safe: `load` reads it best-effort, and `save` backs it up before
  overwriting it.)

**What would be new evidence:** session records on two sessions at one track that agree, and a
median-lap change larger than half the wider IQR. A statistic that leaves out the warm-up run (for
example, the median of the longest run) would also count, if it clears that bar on a real pair.
Storing the numbers is a separate question. Recordings do disappear (D24 and Sandown 09-05 did), so
recording the numbers when a session is opened is the only way to keep them. That is a reason to
store them once something reads them, not before.

---

## 9. The hesitation metric (lift-to-brake gap per corner) — refused (M5)

**The idea.** Between lifting off the throttle and getting on the brake there is a gap. A driver who
dithers there loses time no lap chart shows, so publish that gap per corner (roadmap LATER,
"Hesitation metric", scored 6.5 · M).

**What it is measured with, and why that settles it.** Both ends of the gap come off ONE series —
`_signal.speed_long_g` on the lap's native ~10 Hz grid, the same derivative `driving.brake_events`
runs on. So the gap inherits that grid's **0.1 s quantum** and that derivative's **0.10–0.12 g of
noise**, while the signal it has to find — a kart's off-power drag — measures **0.042–0.046 g**.
The thing to detect is smaller than the noise on the instrument detecting it.

Measured on four present recordings (D24 is gone), through the app's own brake events and the
shipped corner-match window; a brake attaches to 86–94 % of the (lap × corner) cells.

> **Re-measured after D2** (the brake detector stopped counting a string of blips with no
> sustained brake as a brake event, so 14 / 14 / 14 / 12 cells lost the brake they had attached
> to). Every figure in this section is D2's, from the same probe on the same four recordings. The
> verdict did not move, and one number that argued against it is gone: before D2 one of the
> eight pooled tests below reached p < 0.05 (Sandown 3h, pace removed, +0.110, p 0.035); now none
> does. The figures before D2: attachment 91–98 %; 0.50 s gap p90 0.350 / 0.151 / 0.250 / 0.510;
> literal-reading median 5.5–6.4 s; planted-gap shares 1 / 6 / 21 / 76 / 94 %; shares over the
> planted-zero p95 4 / 6 / 5 / 2 % and 14 / 3 / 6 / 16 %; pooled ρ +0.038 / +0.029 / +0.070 /
> +0.023, pace removed +0.110 / −0.095 / +0.027 / +0.011; power 100 / 100 / 100 / 100 % and
> 100 / 100 / 100 / 71 %, bare 14 / 14 / 22 / 8 %; selection control 3.3–6.7 % and 20.0–34.7 %;
> split-half ρ +0.68 / +0.83 / +0.39 / +0.43. SD_19_09's "coast span ends within 0.5 s" cell read
> 6 % here before D2, but the probe gives 0 % on the detector before D2 as well.

| | Sandown 3h · 62×7 | SD_30_08 · 37×7 | SD_19_09 · 36×7 | MK_18_09 · 19×12 |
|---|---|---|---|---|
| gap on the brake detector's own series | **0.050 s** [p10 0.050, p90 0.150] | 0.050 [0.050, 0.150] | 0.050 [0.050, 0.150] | 0.050 [0.050, 0.150] |
| gap on the coast channel's 0.50 s series | 0.150 [0.050, 0.281] | 0.150 [0.050, 0.150] | 0.150 [0.050, 0.250] | 0.150 [0.050, 0.450] |
| a coast span ENDS within 0.5 s of the onset | 4 % | 1 % | 0 % | 6 % |

0.050 s is this estimator's floor — half a sample, returned when there is no off-power sample at
all between the throttle and the brake. **That is the median on every recording.** The backlog's
literal wording ("the end of the app's own coast span to the brake onset") is a different number
again — median **5.5–6.7 s**, because the coast the app detects is almost never the one abutting
the brake.

### The null: what the instrument reports when there is no hesitation at all

The shipped estimator was run over a synthetic approach carrying a **planted** gap, built from each
recording's own entry speed, drag decel, brake ramp and block-bootstrapped speed noise (600
approaches per planted value).

| planted gap | 0.0 s | 0.2 s | 0.3 s | 0.5 s | 0.8 s |
|---|---|---|---|---|---|
| estimate, brake series (Sandown 3h) | 0.050 | 0.050 | 0.050 | 0.050 | **0.050** |
| estimate, 0.50 s series (Sandown 3h) | 0.150 | 0.150 | 0.250 | 0.450 | 0.650 |
| share clearing the planted-zero p95, 0.50 s series | 1 % | 6 % | 19 % | 73 % | 89 % |

**With the noise switched off the same estimator recovers the planted gap** (0.8 s → 0.75 s,
0.5 → 0.45, 0.3 → 0.25) — the probe's own negative control, so what follows is the channel, not a
broken estimator.

Two things follow. The brake detector's own series **cannot resolve a gap at all** — 0.8 s of
planted hesitation still reads 0.050 s. The coast channel's 0.50 s series **can**, from about
0.3 s upward, at the cost of ~0.05 s of bias and ±0.1 s of spread per event.

**And the measured data is the planted-zero distribution.** Observed median vs planted-zero median:
0.050 / 0.050 s on the bare series and 0.150 / 0.150 s on the 0.50 s series, on all four
recordings. The share of events above the planted-zero p95 — 5 % is what no hesitation looks like —
is 4 / 6 / 0 / 2 % (bare) and 13 / 3 / 7 / 15 % (0.50 s). The typical approach on these recordings
goes from throttle to brake inside one sample.

### With the instrument that does work, the gap predicts nothing

Per-corner Spearman of the 0.50 s gap against the corner's own time, pooled (pre-specified,
selects no corner), 20,000 permutations:

| | Sandown 3h | SD_30_08 | SD_19_09 | MK_18_09 |
|---|---|---|---|---|
| pooled ρ | −0.005 (p 0.93) | −0.002 (p 0.98) | +0.089 (p 0.26) | −0.005 (p 0.95) |
| pooled ρ, lap pace removed | +0.045 (p 0.45) | **−0.110** (p 0.12) | +0.017 (p 0.83) | +0.058 (p 0.53) |

None of the eight reaches 0.05, and the same statistic on two recordings of the same track has
**opposite signs**. With lap pace removed the per-corner best is never family-wise significant
(p_fwer 0.12 at the lowest). On the raw series one Sandown 3h corner is (ρ +0.514, p_fwer 0.001;
+0.496, p_fwer 0.002 before D2), and with lap pace removed it is not (p_fwer 0.12).

**Power, end to end.** A hesitation of 0.30 s ± 0.15 s lap to lap, costing the corner its own
duration (β = 1.0 s per s — the physical ceiling), planted and then passed through the simulation's
own measurement model before the test: on the 0.50 s series the pooled test fires **100 / 100 / 100 /
96 %** (Sandown 3h, SD_30_08, SD_19_09, MK) and at β = 0.5 still **97 / 100 / 100 / 58 %**. On the
bare series the same effect is found **10 / 10 / 16 / 11 %** of the time. So the silence above is a
measurement, not a shortage of laps — the test would have seen the effect, on the series that can
carry it, had it been there.

**Selection control** (nothing to find): "some corner at FWER 0.05" fires 3.0–6.3 %, "the TOP
corner read off its own p" fires **20.0–33.7 %**, the #311 trap again.

### Why this is a refusal and not a smaller feature

A per-corner number whose median IS its instrument's floor cannot be published with a corner's name
on it. Quantised to 0.1 s, the whole distribution occupies two or three values, the two windows the
app already ships over this signal disagree by a median 0.100 s about it, and the per-corner median
does not repeat between the odd and even laps of one session on the series that can measure it
(split-half ρ +0.68 / +0.83 / +0.72 / +0.37, but over per-corner medians that take only the values
0.05 and 0.15).

The probe behind this section is `studio/dev/probes/p11_hesitation.py` (with the M5 set's shared
`_m5_cache` and `_m5_stats`).

**What would be new evidence:** a throttle or brake-pressure channel (a real pedal input, not a
speed derivative), or a GPS/IMU chain that puts the lift and the onset on a grid finer than 0.1 s
with noise below the ~0.04 g drag step. Nothing on a GoPro's GPS9 does.

---

## 10. Driver learning vs session evolution, by median polish — refused (M5)

**The idea.** A session gets faster for two reasons — the track and tyres coming to the driver, and
the driver working a corner out. Median-polish the laps × corners matrix of corner times: the lap
effects' trend is the session, and a corner whose residual trends on top of that is learning
(roadmap LATER, scored 6 · L, "most speculative survivor").

**The first problem is not statistical.** Median polish separates **common** from
**corner-specific**. It does not separate the driver from the track. A driver who simply gets
faster everywhere lands entirely in the lap effects, beside the rubbering-in, and no arithmetic
here can tell those two apart. Only the corner-specific half is testable, so only it was tested.

**The common half is real, and the app already ships it** (the Stats page's pace trend and the
per-stint trends, `stats.Stint`):

| lap effects' Theil-Sen trend | Sandown 3h | SD_30_08 | SD_19_09 | MK_18_09 |
|---|---|---|---|---|
| across the session | −0.045 s | −0.064 s | −0.087 s | **−0.335 s** |
| shuffled-order p | 0.31 | 0.028 | 0.0001 | 0.0018 |

**The corner-specific half never beats its null.** The null shuffles the lap ORDER, permuting whole
rows of the residual matrix, so every lap keeps its own residuals across every corner and only time
order dies. The pre-specified statistic is the summed squared per-corner rank trend, which selects
nothing (20,000 permutations):

| | Sandown 3h | SD_30_08 | SD_19_09 | MK_18_09 |
|---|---|---|---|---|
| pre-specified global p | **0.139** | **0.457** | **0.308** | **0.297** |
| top corner, uncorrected p | 0.035 | 0.065 | 0.050 | 0.029 |
| the same corner, family-wise p | 0.198 | 0.357 | 0.284 | 0.171 |
| split-half (odd vs even laps) agreement of the per-corner trends | r +0.46, 5 of 7 signs | r −0.18, 1 of 7 | r +0.11, 4 of 7 | r −0.09, 2 of 6 |

The second and third rows are the whole story: read by eye, every recording has a corner at
p < 0.07; corrected, none of them survives. The selection control says why — with the lap order
shuffled, "the top corner by its own p" fires **27.3–30.3 %** of the time.

**Power, by planting into the real matrices.** A known corner-specific improvement added to ONE
corner and put through the whole chain (polish, then the trend test):

| planted across the session | Sandown 3h (61 laps) | SD_30_08 (35) | SD_19_09 (34) | MK_18_09 (18) |
|---|---|---|---|---|
| 0.10 s — some corner named / the RIGHT one | 26 % / 26 % | 18 % / 18 % | 4 % / 4 % | 0 % / 0 % |
| 0.20 s | 42 % / 23 % | 46 % / 46 % | 86 % / 86 % | 0 % / 0 % |
| 0.40 s | 98 % / 84 % | 100 % / 100 % | 100 % / 100 % | 0 % / 0 % |

So the test is not blind: it finds a 0.4 s corner-specific move almost always on a 34–61 lap
session, and names the right corner 84–100 % of the time. It finds nothing in the real data. On a
19-lap session it finds nothing at any size, which is itself the answer for short sessions.

**The mirror-image control passes:** a purely COMMON improvement of the same size (planted into
every corner) is misread as corner-specific **0 %** of the time, 200 replicates at each size on
every recording. The decomposition does not leak — there is simply nothing in the corner-specific
half to decompose.

**The one hit, and why it is not an exception.** Of eleven tests (four whole sessions + seven
stints), one fires: Sandown 3h's fourth stint (32 laps), global p 0.0026, C5 ρ +0.603
(p_fwer 0.0022). P(the smallest of eleven p-values is that small under a global null) ≈ 2.8 %, so it
is marginal before anything else is said — and the sign is wrong for the story: C5 gets **slower**
across that stint while C1 and C2 get faster (ρ −0.39, −0.41), which is a redistribution of one
lap's time between corners, not a corner being learned. The same recording's third stint reads
C5 ρ +0.23, global p 0.88.

The probe behind this section is `studio/dev/probes/p12_median_polish.py` (with the M5 set's shared
`_m5_cache` and `_m5_stats`).

**What would be new evidence:** a session of 40+ laps where a corner-specific trend of ≥0.2 s
survives the pre-specified global test AND repeats in the odd/even split of the same session — the
two things the planted controls say such a session would show.

---

## 11. An engine-RPM readout, from the gearing or from the audio track — refused (M3)

**The idea.** Two routes to engine RPM for a single-speed kart. (1) Arithmetic: trap speed ÷ tyre
circumference × rear/front sprocket teeth. The session record already stores the sprockets, so add
tyre circumference and kart class to it and show RPM at trap speed, never for a shifter. (2) The
audio: read RPM off the engine's tone. The research that proposed (2) said it "would not bet" on
it, expecting wind noise and the camera's automatic gain control to drown the engine.

**What shipped.** Only the arithmetic: `studio/gearing.py`, with hand-worked tests
(`tests/test_gearing.py`). Its class gate is structural. A shifter, or a class nobody stated, gets
no drivetrain object, so there is nothing to ask for a number. The p14 probe is its one caller. No
record field, no form row and no readout shipped.

### The gearing readout: nobody could see it

On 2026-09-22 the owner has **no `session_records.json` at all**. That is PR #343's finding, and
this probe re-read the directory. So a gearing RPM would show on **0 of his 4 present recordings**.
A filled-in record would not be enough either: it holds sprockets, but no tyre circumference and no
class. The two new fields would have no writer except a form row that serves no one yet. All four
recordings are at Daytona circuits (Sandown Park, Milton Keynes). The audio below also finds the
drivetrain constant unchanged, to within half a sprocket tooth, across three Sandown days months
apart. That fits circuit-set gearing better than gearing the driver chooses, so the sprocket fields
may stay blank even when records exist.

### The audio: the research was wrong about the wind, and it is still not an RPM

`studio/dev/probes/p14_rpm_audio.py` tested a pitch estimator on a **known signal first**. The
estimator is subharmonic summation on a whitened 0.4 s spectrum, one estimate per GPS fix. The
known signal is a harmonic tone following a kart-like speed trace, with three harmonic profiles.
It sits in wind noise whose level grows with the square of speed and gusts by ±3 dB. The mix then
goes through an automatic gain control, a limiter and ffmpeg's AAC codec. The estimator abstains
below the 99th percentile of what wind alone scores. That threshold was fixed on the synthetic
signal and never moved on footage.

| planted fundamental | +10 dB | 0 dB | −10 dB | −15 dB | −20 dB |
|---|---|---|---|---|---|
| 22–50 Hz | 100 % / 100 % | 98 / 99 | 53 / 89 | 18 / 71 | 6 / 59 |
| 45–100 Hz | 100 / 99 | 95 / 99 | 77 / 99 | 38 / 92 | 13 / 81 |
| 90–200 Hz | 90 / 96 | 89 / 97 | 76 / 96 | 60 / 94 | 23 / 90 |
| 135–300 Hz | 89 / 94 | 86 / 92 | 77 / 91 | 52 / 86 | 31 / 87 |

Each cell reads *frames the estimator answers / of those, within 5 % of the planted
fundamental*. The median error of an answered frame is 0.11–0.33 %. SNR is engine power over wind
power in 20–1200 Hz, before the gain control. **The gain control and the codec cost nothing:** at
90–200 Hz, answered / within 5 % goes from 72 / 97 % with the resampler alone to 86 / 98 % with
the gain control and the codec, at −10 dB.

**Then the owner's four recordings**, all chapters, footage read only through an ffmpeg pipe. Above
60 % of top speed a centrifugal clutch is locked, so the engine's tone **must** be one constant
times road speed. Measured:

| | 0064 Sandown 3h | 0065 SD 30-08 | 0068 SD 19-09 | 0067 MK 18-09 |
|---|---|---|---|---|
| clutch-locked frames answered | 75 % | 71 % | 71 % | 68 % |
| … of those within 3 % / 5 % of ONE constant | **83 / 91 %** | **87 / 92 %** | **83 / 90 %** | 71 / 81 % |
| … an octave off | 4 % | 4 % | 4 % | 3 % |
| top 20 % of speed, where wind is worst: answered / within 3 % | 90 / 89 % | 87 / 93 % | 84 / 92 % | 76 / 81 % |
| the constant (Hz per km/h); per-lap spread (IQR / median) | 2.2105; 0.53 % (61 laps) | 2.2188; 0.16 % (37) | 2.2251; 0.21 % (36) | 2.1419; 0.30 % (19) |
| tone at each lap's top speed (median) | 183.3 Hz @ 84.1 km/h | 187.5 @ 85.9 | 188.0 @ 85.9 | 185.0 @ 87.9 |
| 100 ms audio level, p5 / p50 / p95 (dBFS) | −16.4 / −11.8 / −10.1 | −14.9 / −11.7 / −9.6 | −13.3 / −11.5 / −9.6 | −22.3 / −11.7 / −9.4 |

- **Wind does not dominate.** The tone is answered most often, and most accurately, at the top of
  the straight, where the wind is loudest. The footage's median salience per speed band is 25–36.
  The synthetic signal scores 23–35 at −10 dB and 17–25 at −15 dB, so real footage behaves like
  the −10 dB row, where the estimator still holds.
- **Gain control is present, and harmless.** The median level moves by ≤ 1.1 dB between 20–40 %
  and 80–100 % of top speed on every recording. Wind pressure grows with the square of speed, so
  over that threefold range wind alone would read ~19 dB louder on an uncontrolled microphone.
  Pitch does not care about level; the synthetic chain above says so.
- **It is the engine, not the chain or a tyre.** With the kart stopped, a tone is still there in
  56–90 % of frames (median 41–72 Hz). Below 60 % of top speed the tone runs **2–12 % above** the
  locked constant on all four recordings (20–40 %: ×1.018–1.119; 40–60 %: ×1.035–1.053). That is
  a centrifugal clutch below lock-up. A wheel or chain tone can do neither.

**So why is it refused?** Three measured reasons.

1. **Above lock-up it is the speed trace again.** 83–87 % of frames at Sandown (71 % at Milton
   Keynes) sit within 3 % of speed × one constant, and that constant moves by 0.16–0.53 % from lap
   to lap. An RPM trace drawn from it would repeat the speed trace the app already draws, scaled.
2. **The constant is not an RPM, and the audio cannot say which RPM.** At each recording's median
   top-of-lap speed, the tone reads **11,150–11,470 rpm** if it is the crank's own rate (a
   two-stroke fires once a turn). It reads **5,580–5,740 rpm** if it is twice that rate (a
   four-stroke single's strong second order). Read as a four-stroke's firing rate it would be over
   22,000 rpm, which no kart engine turns. Ordinary sprockets on a nominal 0.88 m tyre fit **both**
   readings on every recording (via `studio.gearing`, within 0.75 %). On 0068, 11 pairs fit the
   first reading (9/63 through 14/99) and 7 fit the second (15/53 through 20/71). On the other
   three, 6–8 pairs fit the first and 4–6 the second. The engine type would have to be typed in.
3. **The one independent check cannot run.** The gearing arithmetic is the only thing an audio RPM
   could be checked against, and it needs the record that does not exist: **0 of 4** recordings.

**Two things it did find, not built.** (a) Clutch slip below ~60 % of top speed is the one quantity
the audio measures that speed cannot. On a fleet kart the driver cannot change the clutch, so it
would need its own package and its own reason. (b) The drivetrain constant is a fingerprint of the
drivetrain. Three Sandown days differ by only 0.28–0.66 % (each constant's standard error is
≤ 0.05 %), less than half of one rear tooth (1.3 % on a 76). Milton Keynes is 3.1–3.7 % lower,
about two and a half to three rear teeth (or a different tyre). That could one day answer the session record's "was the gearing the same?"
without anyone typing it. Nothing asks that question today.

**A clock note for whoever owns the overlay lag (X3).** The audio and the GPS speed field line up
best when the speed is taken **+0.12 to +0.32 s** late. That comes from two independent measures
on each recording: the most frames within 2 %, and the peak of the cross-correlation of their
changes. At that lag, the constant under braking and under acceleration differ by 0.27–0.59 %. At
the installed GPS lag (+0.39 to +0.50 s, measured from gyro against the GPS *positions*) they differ
by **3.5–4.2 %** on all four recordings. Either the receiver's speed field has less latency than its
positions, or the audio sits behind the picture. This probe cannot tell which. Only the picture can.
(X2, 2026-09-24: that installed figure was overstated by half a GPS sample on every lap whose
curvature window came out even; centred, it is +0.38 to +0.46 s on the same four recordings. The gap
to the audio narrows by 5–40 ms and stays open.)

**What would be new evidence:** session records on the present recordings that carry the
sprockets, a measured tyre circumference and the engine type. p14's gearing check would then print
the two RPMs side by side. If they agree within ~2 % at trap speed on two recordings, an audio RPM
has its independent check and both fields earn their form rows.

---

## 12. The Brake/Throttle band: a graded brake half, or one held over each detected brake event — refused (D1)

**The ideas.** #347 painted the speed chart's Brake/Throttle band onto the map and found two things
wrong with it: each braking zone came out in pieces, and the brake half was on/off only. It
suggested holding the band at full over each detected brake event. D1 fixed the pieces a different
way: the band now paints the brake detector's own Schmitt fragments that last `MIN_BRAKE_S`, full
from their first sample past −θ_b to their last. The two alternatives below stay refused.

**How they were judged.** Against braking zones defined without the band or the detector: runs where
the speed, smoothed over 0.5 s, decelerates past 0.16 g for at least 0.3 s and 2 km/h. At 0.5 s the
10 Hz derivative's ~0.1 g of noise is down to ~0.03 g, and the reference has no hysteresis and no
merge, so it favours none of the candidates. It covered the four present recordings, 154 valid laps.
Across a sweep of 0.3/0.5/0.7 s × 0.12/0.16/0.20 g, the shipped band beat the old one on recall,
precision and fragmentation at every setting on every recording. Holding the band over each event had
the lowest precision everywhere.

| 0.5 s / 0.16 g reference | Sandown 3h · 0064 | SD_19_09 · 0068 | SD_30_08 · 0065 | MK_18_09 · 0067 |
|---|---|---|---|---|
| zones painted in 2+ pieces: old band → shipped | 46.6 → 10.5 % | 36.5 → 7.3 % | 46.7 → 5.8 % | 50.0 → 12.9 % |
| precision: old / shipped / **held over each event** / **whole fragment** | 74.4 / 82.4 / **45.7** / **73.5** % | 81.5 / 86.5 / **56.2** / **78.5** % | 83.4 / 88.0 / **61.4** / **79.7** % | 84.0 / 88.9 / **62.1** / **82.4** % |
| recall: old / shipped / held over each event | 83.6 / 91.9 / 98.7 % | 86.0 / 93.1 / 99.1 % | 86.2 / 94.6 / 99.7 % | 86.1 / 93.5 / 99.0 % |
| brake painted while the kart accelerates, s a lap: old / shipped / held | 0.39 / 0.02 / **4.31** | 0.23 / 0.00 / **1.82** | 0.24 / 0.00 / **1.34** | 0.46 / 0.01 / **3.60** |

Three measured reasons:

1. **Holding the band over each event paints the maneuver merge's coasts.** `merge_brake_maneuvers`
   fuses fragments across up to 25 m of coast so that a corner gets ONE brake point. The event's
   span is the extent of that point, not of the braking. Painting it buys 5-7 points of recall and
   paints brake for 1.3–4.3 s a lap while the kart is accelerating. Precision falls to 45.7–62.1 %.
2. **Painting the whole fragment, lead-in and release included, costs precision too.** Those tails
   decelerate at 0.056–0.16 g, which is `COAST_DRAG_MIN`..θ_b: the coast channel's own band.
   Precision falls below the OLD band's on all four recordings (73.5–82.4 % against 74.4–84.0 %).
3. **A graded brake level would be half noise.** Inside the reference zones, the series the band
   reads carries per-sample noise (raw minus 0.5 s-smoothed) of sd 0.136 / 0.113 / 0.109 / 0.129 g.
   The braking's own variation there is sd 0.132 / 0.149 / 0.143 / 0.122 g. The design already
   normalises braking at θ_b ("g == −θ_b reads full brake"), so any genuine brake saturates, and
   deceleration is not pedal pressure either. Grading it off a smoothed series would be a new
   instrument with a new normalisation, not a fix.

**What would be new evidence:** a pedal-pressure channel, or a smoothed brake series validated
sample by sample against one.

---

## 13. A gyro-yaw bridge across GPS dropouts on the map — refused (L2)

**The idea.** Where a lap's GPS drops out, the map bridges the hole with another lap's shape
(`studio/gapfill.py`), or with a spline when no lap covers that stretch. The export's map inset
draws a straight chord, because `lap_trace_xy` is not gap-filled. The proposal was to integrate
the gyro's yaw across the hole instead, only across the hole, never across a lap, and anchored at
both ends, so the bridge curves the way the kart turned. `gps-accuracy-research.md` T5 had already
ranked it a map-only visual nicety that cannot change a lap time.

**How it was tested.** `studio/dev/probes/p15_gps_gap_census.py` loads all four present recordings
through the real `Session.load`, all chapters. Every app-support seam is jailed, and the owner's
`tracks.json` is copied into the jail. The probe wraps the loader's read, quality-gate and clean
stages, calling each one through unchanged, so every hole can be traced to where it came from. It
counts the gaps the map would bridge with `gapfill.find_gaps`, the call behind the lap table's
dropout flag and the marks band, over every lap, valid or excluded. The footage folders and the
real app-support directory were size/mtime-snapshotted before and after, and were unchanged.

### There is nothing to bridge

| recording | laps (valid) | raw fixes | raw holes > 0.35 s while moving | fixes the gate rejects, all / while moving | gaps in the kept trace | gaps inside a lap | longest step inside a valid lap |
|---|---|---|---|---|---|---|---|
| 0064, Sandown 3h | 70 (62) | 45,027 | 0 | 0 / 0 | 0 | 0 | 0.101 s |
| 0065, SD 30-08 | 39 (37) | 25,663 | 0 | 561 / 0 | 0 | 0 | 0.101 s |
| 0068, SD 19-09 | 38 (36) | 23,246 | 0 | 0 / 0 | 0 | 0 | 0.101 s |
| 0067, MK 18-09 (anticlockwise) | 22 (19) | 26,832 | 0 | 2,799 / 1,260 (6.4 %) | 16 | 0 | 0.101 s |

- **Not one fix is missing inside any lap of any present recording.** Across the 154 valid laps
  there are 79,480 steps between interior fixes, and none is longer than 0.101 s. None is even
  one missing fix long (> 0.15 s). The excluded laps have no gap either.
- **The receiver never stopped reporting.** Every hole comes from the quality gate
  (`_gate_quality`: no 3D fix, or DOP > 10). Every raw fix inside each of MK's 16 holes was one the
  gate rejected, and on the three Sandown recordings the gate rejects nothing while the kart is
  moving.
- **MK's 16 holes are all before the first lap.** They run from 0.40 s to 33.4 s, about 96 s in
  total, and all 16 fall in the 194 s of driving before lap 0 starts. No lap overlay draws that
  stretch. The whole-trace overlay breaks there on purpose (`map_view._trace_runs`), and L2 bridges
  only inside a lap, so none of these is a gap it could ever be asked to fill.

This is the same answer the D24 recordings gave: `Session.auto_marks` records zero dropout marks
on both full chains. (#332's "0 gaps in any clean lap" cannot say this by itself: the clean set,
`consistency_lap_ids`, excludes dropout laps by definition.)

### Even a planted gap is already bridged to within a pen width

No present recording has a gap, so gaps were **planted**. Fixes were deleted in memory from every
valid lap, every 4 s along it, and `gapfill.reconstruct_lap` was run on what was left with the
app's own donors (`Session._donors_for`). **A planted gap is not evidence that dropouts happen.**
It says only how much a better bridge could win if one did. The error is each deleted fix's
distance from the bridge the app would draw. Each cell is the 90th percentile, over all planted
holes of that length, of the worst deleted fix per hole, in metres:

| hole | 0064 | 0065 | 0068 | 0067 (MK) | straight chord, all four |
|---|---|---|---|---|---|
| 0.5 s | 0.06 | 0.04 | 0.04 | 0.09 | 0.34–0.38 |
| 1 s | 0.25 | 0.22 | 0.20 | 0.37 | 1.35–1.52 |
| 2 s | 0.88 | 0.81 | 0.77 | 1.05 | 5.05–5.64 |
| 3 s | 1.53 | 1.26 | 1.40 | 1.89 | 10.93–11.66 |
| 5 s | 2.45 | 1.82 | 1.92 | 3.43 | 24.03–25.19 |

That is 723, 410, 398 and 317 holes of each length. The kept trace is already smoothed (a 13-fix
boxcar), and the holes were cut after smoothing, so their edge fixes were averaged with the fixes
that are now missing. A real dropout's edges are smoothed without them. That bias is stated here,
not corrected.

**At the map's own scale.** The whole Sandown circuit spans 196–213 m and MK spans 326 m. Fitted to
the map panel's 544 px minimum width, that is 0.36–0.39 m per pixel at Sandown and 0.60 at MK
(0.20–0.21 and 0.33 at 1,000 px). The current lap is drawn with a 3 px pen, 1.1–1.8 m wide at
that size.

- **Up to 2 s, today's bridge is inside its own line width.** Its 90th percentile is 0.77–1.05 m,
  about 2 px. At 3 s it is about one pen width, and at 5 s 5–6 px. That is still 7–14 times closer
  than the straight chord.
- **The only large misses are hairpins with no donor.** On 5 s holes, 1–3 per recording fall back
  to the spline because no donor qualifies, and they miss by 19–27 m. All seven are hairpins: the
  heading turns 174–193°, and the true path is 2.3–3.5 times its chord. The donor filter rejects
  every donor there, six times in seven on its arc/chord ceiling (`DONOR_ARC_RATIO_HI` = 3.0).
  This is the one place a gyro bridge would draw visibly better. The cheaper fix, if a real 5 s
  hole through a hairpin ever appears, is that bound. On MK the fallback also fires on 10 shorter
  holes (0.5–3 s), nine of them on lap 1, and the worst of those misses by 5.94 m.

### What building it would have cost

- **A new clock crossing.** Gyro content lines up with `media_time`, and GPS with
  `media_clock.without_gps_lag()`. On these four recordings the two are 0.39–0.50 s apart (the
  loader's own lag estimates were +0.498, +0.439, +0.393 and +0.433 s). The bridge would need its
  own `CROSSINGS` entry in `tests/test_media_clock.py` and a test of its own.
- **A yaw sign, tested in both directions.** Every Sandown recording is clockwise, and a
  clockwise-only sign bug shipped unnoticed in #334.
- **Anchoring at both ends,** which is exactly what the borrow's `_similarity_map` already does to
  a donor shape.
- **No analysis value moves either way.** The bridge is only drawn (see the `gapfill` module
  docstring).

**What would be new evidence:** a recording with gaps inside its laps (`p15_gps_gap_census` prints
them) long enough, about 3 s or more, that today's bridge misses by more than a pen width at the
map's size. A hole through a hairpin where no donor qualifies would also count. Even then, try
the donor arc/chord bound before a gyro path.

---

## 14. Taking a neutralised race's slow laps out of the clean set — refused (H10)

**The claim.** #358 found that on `Sandown 3h 2026` chapter 1 opened alone, 8 of the 17 laps the app
counts as clean run 61–90 s against a 47.4 s best. Clean laps feed the ideal lap, every σ, coaching
and the published ideal-lap sample table. So either those laps are mis-segmented, which is a defect,
or they are valid laps driven slowly, and then the question is whether the pace statistics should
admit them.

**How it was tested.** `studio/dev/probes/p16_slow_clean_laps.py` loads each working-set recording
through the real `Session.load` and the start line saved beside it. It loads each Sandown 3h chapter
alone and every recording whole. Every app-support seam is jailed, and the footage folders and the
real app-support directory were size/mtime-snapshotted before and after, unchanged. For every
substantial lap it reads the evidence: the lap's distance, the closure the classifier reads, how far
it strays from the best lap's line, the classifier's own stop measure, and its time under 40 km/h.
It then loads Sandown 3h twice more with the valid set wrapped, so every number downstream is the
app's own code, to see what each pace surface does without the laps. With `--owner-tracks` it
repeats the census on the owner's own start line. Lap numbers below are the app's internal ids; the
lap table prints each one higher.

### All eight are laps, run under a yellow or a safety car

| lap | time | × best | distance | finish from start | furthest off the best lap's line | longest stop | under 40 km/h |
|---|---|---|---|---|---|---|---|
| 1 | 61.82 s | 1.30 | 748.9 m | 0.4 m | 4.7 m | 0.0 s | 25.0 s |
| 2 | 74.65 s | 1.57 | 747.1 m | 0.5 m | 3.2 m | 0.0 s | 54.5 s |
| 3 | 88.27 s | 1.86 | 761.5 m | 1.0 m | 4.7 m | 0.0 s | 80.2 s |
| 4 | 89.73 s | 1.89 | 758.0 m | 0.8 m | 4.6 m | 0.0 s | 79.2 s |
| 5 | 83.99 s | 1.77 | 752.6 m | 0.6 m | 3.6 m | 0.0 s | 82.6 s |
| 7 | 82.56 s | 1.74 | 756.8 m | 0.5 m | 4.6 m | 0.0 s | 68.5 s |
| 13 | 77.09 s | 1.63 | 755.8 m | 0.2 m | 4.3 m | 0.0 s | 56.6 s |
| 15 | 68.36 s | 1.44 | 753.2 m | 1.7 m | 3.2 m | 0.0 s | 31.7 s |
| the other 9 | 47.43–53.92 s | 1.00–1.14 | 726.7–745.3 m | ≤ 1.7 m | ≤ 7.7 m | 0.0 s | 0.1–6.3 s |

- **None is mis-segmented.** A missed start-line crossing would double a lap's distance and an
  extra one would halve it. These run 747–762 m, 1–3 % longer than the racing laps, which a slower,
  wider line accounts for. Each ends within 1.7 m of where it started, travelling the same way
  (≤ 3°).
- **None is a pit lap.** The pit lane on this layout runs about 17 m off the racing line. The two
  pit laps (14, and 37 on the whole recording) spend 57–84 s more than 10 m off it and stand still
  for 98–131 s, and both are out on every load. None of the eight strays more than 4.7 m, less than
  the racing laps' own 7.7 m.
- **None stops.** The classifier's stop measure reads 0.0 s on all eight. Lap 6, the one lap in
  that stretch that did stop (6.6 s, on track), is already out.
- **All eight are sustained slow running**, 25–83 s per lap under 40 km/h against at most 6.3 s on a
  racing lap. Laps 1–7 are the ten minutes after the opening lap, and 13 and 15 sit either side of
  the first pit stop. Two are only partly slow. Lap 1 runs from the start to C4 at 0.97–1.08× the
  other clean laps' median per segment, then 1.30–2.71×. Lap 15 runs C6 to the line at 0.97–1.01×.
  That is a yellow or a safety car coming out, and going in, mid-lap.

The owner's own start line cuts the laps in different places: seven clean laps over 1.3× the best,
69.6–90.4 s. Every column says the same there: 747–761 m, finish within 2.2 m of the start, at
most 4.7 m off the line, no stop, 31.7–81.8 s under 40 km/h. Nothing like them exists anywhere else
in the working set. No clean lap of Sandown 3h's chapter 2 or 3 alone, SD_19_09, SD_30_08 or
MK_18_09 is slower than 1.3× its best; their slowest are 1.22, 1.26, 1.06, 1.20 and 1.19×.

### Why chapter 1 counts them and the whole recording does not

The time band is 0.5–1.6 × the MEDIAN of the closed, substantial laps (`_signal._classify_laps`).
On chapter 1, 11 of those 20 laps are slow, so the median is itself a slow lap: 65.09 s, with the
upper edge at 104.1 s. On the whole recording the median is 48.40 s and the edge 77.4 s. That is
why laps 3, 4, 5 and 7 (82.6–89.7 s) are clean on chapter 1 and "off the session median" on the
whole recording, and why the whole recording still counts laps 1, 2, 13 and 15 (61.8–77.1 s). The
same thing disarms the Stats sparkline's Tukey fence: on the whole recording it frames out exactly
those four laps, and on chapter 1 alone it frames out none.

### What each surface does with them and without them

Measured on the real Session, fresh library (the conditions #358's table was measured under). At
Sandown that meant the loader's own start line until Q2 made Sandown Park a built-in track on the
owner's saved line; a fresh library now opens chapter 1 the way the paragraph above on his line
describes it (17 clean laps, seven of them 69.6–90.4 s; the ideal 46.582 s on chapter 1 and 45.809 s
on the whole recording). The table below is still the loader's line; the census on his line is the
paragraph above. "Lower
quartile" is the band re-centred on the 25th percentile instead of the median, the direct fix for
the dragged centre: it drops laps 3, 4, 5 and 7 on chapter 1, and lap 13 on the whole recording.

| surface | ch 1 as counted | without the 8 | lower quartile | whole as counted | without the 4 | lower quartile |
|---|---|---|---|---|---|---|
| clean laps | 17 | 9 | 13 | 62 | 58 | 61 |
| ideal lap | 46.612 s | 46.666 s | 46.618 s | 45.768 s | 45.777 s | 45.766 s |
| ideal, per doubling of laps | 0.740 s | 0.384 s | 0.485 s | 0.283 s | 0.277 s | 0.283 s |
| best lap, per doubling | 0.905 s | 0.425 s | 0.682 s | 0.092 s | 0.086 s | 0.091 s |
| median lap | 53.916 s | 51.077 s | 51.327 s | 48.158 s | 47.974 s | 48.050 s |
| σ lap | 15.74 s | 2.31 s | 10.32 s | 6.11 s | 2.82 s | 5.11 s |
| CoV | 29.2 % | 4.5 % | 20.1 % | 12.7 % | 5.9 % | 10.6 % |
| trend | −1.304 s/lap | +0.365 s/lap | −0.012 s/lap | −0.015 s/lap | −0.000 s/lap | −0.011 s/lap |
| runs | 2 | 2 | 3 | 4 | 3 | 4 |
| demonstrated peak braking (`a_max`) | 0.755 g | 0.878 g | 0.780 g | 0.813 g | 0.818 g | 0.816 g |
| grip ring | 1.435 g | 1.484 g | 1.465 g | 1.490 g | 1.501 g | 1.501 g |
| coaching rows ranked | 0 | 0 | 0 | 4 | 4 | 4 |

Per corner on chapter 1, the eight laps are most of the spread: C1's σ is 3.73 s with them and
0.64 s without, and coaching's median time lost at C3 is 0.539 s with them and 0.031 s without. On
the whole recording they move C1's σ from 1.23 to 0.73 s and C1's time lost from 0.252 to 0.223 s.

### Why none of the alternatives ships

1. **The ideal lap has to keep them.** It is a per-segment minimum, so a slow lap can only give it
   what it drove fast. On chapter 1, lap 15 wins C6 with 3.151 s, 0.048 s under any other clean lap,
   in the stretch it drove at race pace, and lap 1 wins the 0.16 s link from C1 to C2 by 0.001 s.
   Removing them raises chapter 1's ideal by 0.054 s (the partition is re-cut as well) by throwing
   away segments the driver really drove.
2. **Re-centring the band is half a fix, and it costs valid laps.** The lower quartile makes chapter
   1 agree with the whole recording about laps 3, 4, 5 and 7. It also drops lap 13, a valid 77.09 s
   lap, from the whole recording, where nothing was wrong, and chapter 1's σ is still 10.32 s. It
   would move both of #358's Sandown 3h rows (17 → 13 laps, the ideal's rate 0.740 → 0.485 s; 62 →
   61 laps, 45.768 → 45.766 s) and nothing on any other recording. The band exists to catch laps
   that are not laps; this version excludes more laps that are.
3. **A pace cut for σ, the trend and coaching has nowhere to go.** Only one recording in the working
   set has a neutralised stretch, so any threshold would be fitted to these eight laps, and even
   here there is no gap to put it in. Against the best lap, the whole recording's clean laps run
   1.00–1.15, then 1.22, 1.23, 1.26, 1.31, 1.45, 1.59 and 1.64. Valid laps on SD_30_08 and MK reach
   1.197× and 1.190×. The cut that takes exactly #358's eight on chapter 1, 1.3×, clears lap 1 by
   0.3 % (1.3035×). The speed trace does not separate them either: lap 1 spends 25.0 s under
   40 km/h, and lap 61, in chapter 3, which a 1.3× cut keeps, spends 25.6 s. A cut against the best
   lap would also remove a slower co-driver's whole stint in an endurance race, which is a stint,
   not a neutralisation. The working set cannot test that: the best laps of Sandown 3h's three
   stretches between pit stops are 47.43, 47.05 and 47.15 s.
4. **The surfaces already say what they include.** The pace tiles print "median · 17 clean laps",
   coaching prints "median of 17 clean laps", the ideal prints "theoretical best · 17 laps", and the
   trend sparkline plots all seventeen. A σ of 15.74 s sits beside the laps that make it.

**What moves because of this refusal: nothing.** The classifier is unchanged, so the clean set,
#358's ideal-lap table, the coaching tables and the golden fingerprint are unchanged too.

**What would be new evidence:** a slow clean lap that fails the census above (a distance off the
racing laps', an open closure, a stretch off the line, a stop). Or a second recording with a
neutralised stretch whose laps separate from the racing laps on some measured quantity, with room
on both sides. Or a statistic that answers "how consistent is the driver?" without a threshold and
without breaking when nearly half the laps are neutralised. The median band, the sparkline's fence
and the Theil–Sen trend all break at chapter 1's 8 of 17.

---

## 15. Moving the unknown-track start line to mid-straight, or further back off the braking point — refused (X1)

**The claim.** #371 measured, on the synthetic recording's known truth (`studio/dev/synth_gopro.py`),
that the unknown-track heuristic put its line at the fastest fix, which is where laps start braking,
and that the load-time position boxcar then costs up to 13.1 ms a lap there against 0.41 ms at a line
across the middle of the main straight. So: place the auto-fitted line mid-straight, on the longest
straight, or on the nearest constant-speed stretch.

**How it was tested.** Twenty seeds of the synthetic recording at four GPS-noise levels, each loaded
through the real `Session.load`; every lap timed at each candidate line through the loaded
`pacer.Laps` and compared with the truth at that same line (14 laps a seed, 280 a cell, rms of
the lap-time error in ms). "Held peak" is what X1 shipped: the maximum of the speed held over the
11 fixes the heading is read from, instead of the fastest single fix.

| line | noise-free | ½× noise | default noise | 2× noise |
|---|---|---|---|---|
| fastest single fix (before X1) | 4.27 | 7.20 | 11.98 | 23.32 |
| **held peak (X1)** | **0.68** | **5.57** | **11.09** | **22.12** |
| held peak, 5 fixes further back | 0.06 | 5.57 | 11.22 | 22.49 |
| held peak, 10 fixes further back | 0.06 | 5.62 | 11.34 | 22.56 |
| held peak, 20 fixes further back | 0.08 | 5.86 | 11.69 | 23.58 |
| middle of the main (and longest) straight | 0.11–0.13 | 6.1–6.3 | 12.2–12.6 | 24.4–25.2 |

- **The braking bias is real and small.** Noise-free, every rule off the braking point is within a
  millisecond. With noise the lap-time error is σ/v at each crossing, so a slower line is a noisier
  one: mid-straight is **worse than the old line** at default noise and above, and so is backing off
  20 fixes at 2× noise.
- **Real footage cannot see the difference, and official timing cannot rank a placement.** A
  Doppler-only model of the boxcar (the along-track distance integrated from GPS speed and smoothed
  like the positions; it reproduces the synthetic noise-free error at r = +0.997 over 2,729
  lap-line pairs) puts the braking bias at the held peak at 1.6–4.4 ms rms on MK_18_09 and the three
  Sandown recordings; backing off moves it by at most 2.6 ms while losing up to 4.6 km/h of
  crossing speed. MK's residual against Club Speed is 24.7 ms at the built-in line: in quadrature
  the bias change is worth at most 0.3 ms, the slower crossing can cost 1.5 ms, and σ over 14 laps
  has a standard error near 5 ms. The same sheet reads
  29 ms 10 m from that line, 41–50 ms 20 m away and 76 ms 60 m away, because a lap timed at another
  line is a different lap: it measures driving between the two lines, not error.

**What moves because of this refusal: nothing.** X1 ships the held peak only.

**What would be new evidence:** a recording with official timing whose residual at the timing loop
itself shows a braking-point bias above ~5 ms, or a position smoother whose noise does not grow as
the crossing slows.

---

## 16. A relative braking-room hint in place of the absolute "brake ~N m later" — refused (L6)

**The claim.** Board review UX-2: the estimated brake-point hint — "Brake ~11 m later into C7 (est)"
on a coaching row, and the "m later" column of Stats ▸ BRAKING, one number since #349 — is positive
at every corner, so the one figure a driver would act on literally cannot say *where*. Its optimum
is the apex-speed stop at the session's demonstrated **peak** deceleration held constant from the
onset; a kart that ramps into and trails off the brake never sustains its peak, so the optimum lands
past the driver's brake point by construction. Present the room *relative* instead: the driver's own
best-quartile onset against his median onset, or the hint minus the session's across-corner median.
Success: the sign or size varies across corners beyond its own noise, and at least one corner reads
"already on your best braking".

**How it was tested.** `studio/dev/probes/p17_braking_room.py` loads all four working-set
recordings — SD_19_09 (0068), Sandown 3h (0064) and SD_30_08 (0065), clockwise, 7 corners, and
MK_18_09 (0067), the anticlockwise control on another track, 12 corners — rebuilds the per-lap list
both braking surfaces medianize and asserts it equal to the app's (`Session._brake_rows`), and reads
three candidates per corner. **R3** is the review's first option read literally: the latest-braking
quarter's onset minus the median onset. **R2** is its second: the hint minus the recording's
across-corner median of it. **R1** reads the first the way the coaching rows read "Done it?": the
median onset of the corner's *fastest* quarter of passes minus everyone's. Each is held to its own
noise bar — half the corner's IQR (`coaching.SPREAD_MARGIN`), or for R1 a seeded permutation null of
random quarters — and to the lap outcome: Spearman ρ of the onset against the time through
[enter, exit], [enter − 60 m, exit] and [enter − 100 m, exit], with a permutation p, and whether the
latest-braking quarter is quicker than a random quarter.

**The premise holds, and its cause shows.** The hint is positive at **33 of 33** corners (+1.8 …
+47.2 m), and at **15 of 33** no clean lap ever braked at or past the optimum it names. The driver's
own mean deceleration from onset to apex is **0.36–0.44 × a_max** (the four recordings' medians;
IQR 0.25–0.57), and across corners the hint follows the braking zone's energy drop
(v_onset² − v_apex²)/2 — Spearman ρ **+0.50, +0.89, +0.93, +0.80** on 0068, 0064, 0065 and 0067. It
mostly says how much speed each corner takes off.

**Each relative candidate fails a different test.**

| candidate | (a) sign / size varies | (b) beyond its noise bar | (c) reads "already on your best" | (d) the lap outcome |
|---|---|---|---|---|
| R3: latest quarter − median onset | size only, never negative | it *is* the onset scatter: BRAKING's σ and span, restated | 16/33 (under 2 m) | the latest-braking quarter is quicker than a random quarter at **0/33**, slower at **2/33** |
| R2: hint − across-corner median | sign, by construction | 5/21 Sandown and 7/12 MK corners clear ½ IQR | no such reading | wrong sign at 2 of the 6 corners where the outcome speaks |
| R1: fastest quarter − median onset | −9.0 … +6.1 m | outside its permutation null at **0/33** (0068 C5 on the edge) | all 33 | built from the outcome, which says "where you usually brake" at every corner |

- **R3 would ask for the braking that cost time.** Its largest Sandown values are 0064's C6
  (11.6 m) and C4 (10.1 m). At C4 the latest-braking quarter's median pass was **0.42 s slower**
  through the corner than the median of all passes (0.51 s from 60 m out), outside the band a random
  quarter reaches; C2 on the same recording, +0.17 s. Nor is R3 new: a corner's latest quarter
  minus its median is its onset scatter, which the BRAKING table already prints as σ and span.
- **R2 ranks the track, not the driver.** With the hint following the energy drop, subtracting a
  median only moves the uniform sign onto the corners that take off the least speed. The three
  Sandown days agree on R2's sign at 3 of 7 corners: C4 and C6, the two lightest applications
  (26–29 and 14–25 J/kg), always negative, and C5 always positive. Where the outcome does speak
  (the six corners below), R2 has the wrong sign at two, one of them 0064 C6 (ρ −0.42, p 0.001),
  where it reads −6.3 m, the least room on that recording.
- **R1 is the defect's mirror image.** The fastest quarter brakes where the driver usually brakes
  at every corner of every recording, so it would print "already on your best braking" 33 times,
  as uniform as "brake later" and just as silent about where.

**The one signal there is, and why it is still not a distance.** Later onsets go with quicker
passes at **6 of 33** corners (p < 0.05 on the corner time: 0068 C3 and C5, 0064 C6, 0065 C3,
0067 C2 and C8) and with slower ones at **none**. Sandown's C3 reads that way on 0068 and 0065, and
0064 has the same sign (ρ −0.40, −0.36, −0.22). But even at those six the fastest quarter's median
onset sits 0.8–1.4 m from everyone's (3.4 m at MK's C8), inside its permutation null at all six. The
footage backs a direction there, not a distance, and no R1 value clears its noise bar at these
corners or any other.

**What moves because of this refusal: nothing.** The absolute estimate stays where #349 put it
(the Coaching rows and Stats ▸ BRAKING, labelled `(est)`) and stays off the first-open debrief
(#391). Whether a figure that is positive at 33 of 33 corners by construction should stay on the
ordinary Coaching page is the owner's call; this section is its evidence.

*Since then (L7, 2026-09-24):* that call was made on this evidence. The Coaching rows print no
braking metres; where a corner's clean laps separate a direction once corrected for every corner
the recording tested (Holm's step-down at α 0.05, the standard #311 set for a coaching claim), the
row says which way braking went with quicker passes, with its lap count and never a distance
(`coaching.BrakeDirection`). Of the six corners above, two survive the correction, 0064 C6 and
0067 C8, and coaching.py's table re-measures them through the app; the other four are documented
here, uncorrected, and print nothing. Stats ▸ BRAKING still prints the estimate, and since L8
(2026-09-25) as what it is: headed `Bound m (est)` instead of "m later", with no "+" and a hover
that calls it a model's bound, not room to gain. It was relabelled rather than removed because it
is not noise: the same corner's median moves between the three Sandown days beyond both days'
95 % bootstrap bands at 3 of 7 corners (C1, C5 and C7; `p17_braking_room` prints it), so a reader
comparing sessions sees a real change in how far that day's braking sat from a constant-peak stop,
while across one recording's corners it largely ranks the track (above).

**What would be new evidence:** an outcome-grounded per-corner claim — "your quicker passes braked
later here", from the rank correlation above — that survives a family-wise correction over the
recording's corners and replicates on a second recording of the same track, with a distance whose
noise bar clears `BRAKE_HINT_MIN_M`; or a braking model fitted to the deceleration profile a kart
actually produces (ramp-in and trail-off, not a constant peak) whose optimum some clean laps reach.

---

## 17. Centring the corner model's curvature window — refused (X4)

**The claim.** #383 found that `_signal`'s plain boxcar runs half a fix late whenever its window is
even, and centred the curvature window the GPS-lag estimator reads. It left the corner model's alone
on purpose, because centring it moves corner results and published tables. `corners.pooled_curvature`
smooths every lap's κ over w = round(8 m / median fix spacing) fixes. That is even on 55 of 62 clean
laps of Sandown 3h, 23 of 36 of SD_19_09, 23 of 37 of SD_30_08 and 4 of 19 of MK_18_09. So every
corner boundary sits 0.7–1 m late: centre it and re-cut the tables.

**How it was tested.** `studio/dev/probes/p18_corner_kernel.py` swaps the kernel only inside
`pooled_curvature` and measures it three ways.
- **A symmetric corner sampled at a uniform spacing**, where only the kernel can move anything.
- **The synthetic GoPro** (`studio/dev/synth_gopro.py`) through the real `Session.load`: five seeds ×
  GPS noise 0, 1 and 2 × both directions, 30 recordings and 2,940 lap × corner cells. Its corners
  are symmetric arcs, so a true window of any width is centred on the arc's midpoint. The model's
  apex (the |κ|-weighted centroid) and its window's midpoint are measured against that. Each lap's
  corner time and minimum, entry and exit speeds are measured against the kart's true motion over a
  window of the model's own width, centred on the true corner. So are each corner's median loss to
  the best lap and the order the coaching rows rank. Four controls, two seeds each, noise-free: an
  odd window (the two kernels are then identical), w = 6, a constant-speed kart, and no load-time
  position boxcar.
- **The four working-set recordings**, jailed and tripwired, and all 16 footage checks run with the
  centred kernel.

**The defect is real, exactly as stated.** At a uniform spacing the plain kernel reads +0.994 m at
w = 4 (half a fix is 1.000 m) and +0.672 m at w = 6 (0.675 m), and nothing at w = 5 or 7. The
centred kernel is within 0.006 m at every width.

**But the corner window is not late, because a second bias runs the other way.** Synthetic GoPro,
10 recordings a row, plain (today) → centred; + = late along the lap:

| GPS noise | apex − truth | window midpoint − truth, mean (per recording) | corner time − truth, mean (rms) |
|---|---|---|---|
| none | +1.02 → −0.03 m | **−0.00 (−0.07 … +0.08) → −1.06 (−1.10 … −0.99) m** | −13.4 (21.1) → −20.7 (30.1) ms |
| default | +1.14 → +0.09 m | +0.32 (−0.03 … +0.83) → −0.79 (−1.21 … −0.09) m | −12.8 (27.4) → −20.5 (35.0) ms |
| 2× | +1.52 → +0.48 m | +1.04 (−0.10 … +2.02) → −0.07 (−1.01 … +1.16) m | −7.1 (33.5) → −15.0 (38.8) ms |
| all 30 | +1.23 → +0.18 m | +0.45 → −0.64 m (rms 1.16 → 1.21) | −11.1 (27.8) → −18.7 (34.8) ms |

| control, noise-free | window midpoint − truth, plain → centred |
|---|---|
| odd window, w = 5 (the kernels are identical) | −0.88 → −0.88 m |
| w = 6 | −0.05 → −0.81 m |
| a constant-speed kart, w = 4 | +1.02 → +0.11 m |
| no load-time position boxcar, w = 4 | +0.61 → −0.46 m |

- **The second bias is the kart, seen through filters that count fixes.** The load-time position
  boxcar (13 fixes) and this window both smooth by fix index. A kart brakes into a corner far
  harder than it accelerates out (1.15 g against at most 0.45 g in the synthetic). So the fixes sit
  further apart on the entry, the index blur reaches further up the entry than down the exit, and
  the thresholded window opens early. That is −0.88 m where nothing offsets it (the odd window). At
  constant speed it is gone, and there centring is exactly right. The position boxcar carries a
  little over half of it: −0.46 m remains without it.
- **Today's late half fix cancels it.** Noise-free, every recording's mean window midpoint sits within
  0.08 m of truth at w = 4 and 0.06 m at w = 6. Centring moves every window ~1 m early and each
  lap's corner time 7.6 ms further from truth on average. Only at 2× noise, where noise pushes the
  window late again, does centring bring the midpoint nearer; the corner times are further from
  truth at every noise level (minimum speed unchanged; entry speed rms 1.21 → 0.95, exit 0.54 → 0.66
  km/h over all 30).
- **The apex would come right, but no published number reads it.** It is a centroid, which a
  symmetric blur cannot move, so centring puts it on truth. Its one reader is the map's corner
  labels (`CornerModel.corner_map_markers`). Every corner figure reads the window's enter and exit.
- **What a driver reads does not improve.** The loss to the best lap, which the coaching rows rank,
  is a difference over one window, so a shared shift cancels: rms error 14.5 against 14.4 ms,
  Kendall τ against the true order 0.76 against 0.77.

**On the footage it would move a great deal and settle nothing.** The moves are half a fix times each
recording's even-window share. The apex moves −0.65 / −0.41 / −0.37 / −0.06 m on Sandown 3h /
SD_19_09 / SD_30_08 / MK_18_09. Boundaries move in the pooled grid's 0.75 m steps: 11 of 14, 7 of 14,
10 of 14 and 3 of 24 of them, by up to 2.25 m and 98 ms on the best lap. A corner-time cell moves by
up to 138 ms, time handed between a corner and its straight. The coaching order is unchanged on all
four, no corner's time lost moves more than 9.9 ms, and the ideal lap moves −3.6 … +7.3 ms. Yet 8 of
the 9 footage checks of published corner figures fail with the centred kernel (the first-open
debrief's verdict holds), while the other seven (renders, the compare pane, the pedal band, MK's
official timing) pass. Two cells change in kind: Sandown 3h chapter 2's coaching THEME turns from
"pace" (0 % execution, 100 % pace) to "split" (47 / 53), and SD_19_09 chapter 1 ranks two corners
instead of three. The golden gate would re-cut 4,204 of its 113,437 leaves. Footage has no
corner-geometry truth to say which placement is right; the synthetic says today's.

**What moves because of this refusal: nothing.** The corner model keeps the plain window.
`corners.lap_curvature` now says why; it used to say that "a shape does not care", and it does, by
0.7–1 m. The lag reference stays centred (#383): there the timing is the measurement, and centring
was measured closer to its truth.

**What would be new evidence:** smoothing that stops counting fixes through braking and acceleration
(positions or κ smoothed over arc length or time), after which this probe's constant-speed control
says centring is right; or surveyed corner geometry on real footage that puts today's windows late.

---

## 18. pytest as the CTest runner — refused (ARCH-7); pytest for local `-k` shipped instead

**The claim.** The test files are plain scripts whose `__main__` blocks call their tests, and a
CTest registration is a whole file. Tests had silently never run: four `def test_*` no runner called,
and three Stats tests below a misplaced `__main__` (fixed in c91f121). There is no `-k` and no
per-test duration. So run every file as `python -m pytest -q tests/<file>.py` under CTest.

**How it was tested.** pytest 9.1.1 in the env; all 132 files run with their registration's exact
environment (read from `ctest --show-only=json-v1`) and jailed: as the script under a
`sys.monitoring` recorder of which top-level `test_*` start, and under pytest. The timing is two
interleaved whole-suite pairs of the 130 files `test-fast` runs, four wide (load average 8–11 from
other lanes, so CPU time is the steadier figure).

**The never-run class is empty today.** pytest collects 2,140 tests and the `__main__` blocks run
2,124. The 16 between them are exactly the 15 footage checks and 1 soak that each runner leaves out
on purpose. Nothing is stranded and nothing a runner calls is invisible to pytest.

**Run unchanged, pytest breaks 4 files and would reach the owner's data.**

| what pytest does differently | measured |
|---|---|
| a test that takes the runner's `monkeypatch_restore` | 27 ERRORs in test_export_video / _compare / _padding |
| its log capture keeps each failed load's traceback | test_load_affordances: 5 QThreads "leak" (1 → 6) |
| collects the footage and soak checks | test_measured_figures 7 FootageMissing; test_compare_lifecycle 1.3 → 112.8 s |
| enters through its own `__main__.py`, not a file in tests/ | a by-hand run resolves all 8 app-support seams to the real directory |

**And it costs every run.** Scripts 673.0 / 675.7 s CPU (157.9 / 153.0 s wall); pytest 696.9 /
696.2 s CPU (152.9 / 152.2 s wall): **+3.3 % CPU**, flat per process (median +0.15 s a file; no file
more than +1.4 s). Wall time was lost in the load; on an idle Mac that start-up tax is ~+5 s of
`test-fast`'s ~155 s. What the runner would buy beyond that is a file reporting every failing test
rather than stopping at the first. `-k` and `--durations` need pytest in the env, not as the gate.

**What shipped instead.** pytest stays in the env for local iteration, and `tests/conftest.py` +
`tests/pytest.ini` make `pixi run python -m pytest tests/test_x.py -k <name>` jailed and faithful:
132/132 files green, 2,124 passed + 16 deselected. `-k` ran 3 of test_stats' 125 tests in 0.84 s,
where the file takes ~15 s. `tests/test_layering.py` holds pytest and every runner to one set
statically; on the tree before c91f121 it names the three Stats tests. Its reachability rule had
exempted six explicit-runner files that merely contained a spelling like `globals()`. It now looks
for a call, and it caught a test stranded in `test_layering.py` itself while this was written.

**What would be new evidence:** a defect the per-file runners let through and pytest's runner
would have caught, found on this tree; or a way to run pytest without paying its start-up once
per file.

---

## 19. Painting export frames on several threads, and the ink-only map blit on the overlay-only canvas — refused (E9)

**The claim.** The export painted one frame at a time on one thread, and painting looked like 60 % of
a 1080p render (cProfile, 57 fps). QPainter on separate QImages is allowed off the GUI thread, so a
pool of painters should scale it. And the map inset blits a layer the size of the frame on every
frame, so blitting only its ink should save the rest.

**How it was tested.** MK_18_09_26 lap 14, the real `OverlayPainter` on the real per-frame values:
600 frames painted by a thread pool of 1/2/3/4/6 (paint only — no decode, no encode); and the
ink-only blit against the full one on 62 frames a configuration, compared byte for byte.

**Threads do not scale: PySide6 holds the GIL through every QPainter call.** 127.6 / 136.2 / 133.4 /
128.0 / 123.5 fps at 1080p on 1/2/3/4/6 threads; 49.9 / 51.6 / 52.3 / 51.7 / 51.4 at 4K
overlay-only. The profile had also overstated the paint: without the profiler a 1080p frame paints
in 7.2 ms (138 fps) while the decoder alone delivered 60 fps, so the export was waiting on the
DECODE. What shipped instead (E9): the read and the write on threads of their own (they run
without the GIL, in the kernel), and a relay of VideoToolbox decoders.

**The ink-only blit changes pixels on the overlay-only canvas.** On the opaque composite it is exact
(0 of 62 frames differ) and it ships. On the straight-alpha canvas Qt premultiplies and
un-premultiplies every pixel the full blit visits, which rewrites the nearly transparent edge
pixels of the g-dial painted before it: 56 of 62 frames differ at 4K (20 pixels on frame 0, a
channel moving by up to 255 at alpha ~1), 50 of 62 at 1080p. The overlay-only path keeps the full
blit. (The rewritten pixels are the lossy ones; changing them is a decision of its own.)

**What would be new evidence:** a PySide6 that releases the GIL in QPainter; a paint in worker
PROCESSES (not built — the painter reads the Session on every frame and each process would need
its own QGuiApplication and fonts); or a decision to let the overlay-only dial's edge pixels change.

---

## 20. Splitting a merged brake event where the kart went back on power — refused (K)

**The idea.** #421 found SD_19_09 lap 1's 12.5 s "brake event" to be three applications with the
kart back on power between them, fused by `merge_brake_maneuvers`. The merge joins fragments across
up to 25 m of coast, and its throttle gate needs +0.5 g, which a kart rarely pulls. Split such an
event where the kart gained speed, so that each application is its own event, glyph and brake point.

**How it was measured.** The real loader on the four working-set recordings, with a copy of the
merge that records which fragments each event holds, asserted equal to the app's events on every lap.
A gap inside an event counts as a clear release when it lasts more than 0.5 or 1.0 s under
θ_b × `RELEASE_RATIO`, or when the 0.5 s-smoothed speed rises by more than 1.5 km/h across it
(the kart back on power). Each consumer was then re-read through the app's own accessors with the
merge patched to split there. The probes and their output are the K package's evidence (#429).

| | MK_18_09 (0067) | SD_19_09 (0068) | SD_30_08 (0065) | Sandown 3h (0064) |
|---|---|---|---|---|
| events (valid laps) | 200 (19) | 251 (36) | 269 (37) | 472 (62) |
| fused across a release > 0.5 s / > 1.0 s | 22 / 12 | 14 / 8 | 15 / 7 | 87 / 39 |
| fused across a return to power (> 1.5 km/h) | **10 (5.0 %)** | **9 (3.6 %)** | **8 (3.0 %)** | **59 (12.5 %)** |
| of those, applications for two corners | 8 | 2 | 2 | 25 |
| (lap, corner) brake points the split moves > 5 m or restores | 11 / 199 | 10 / 222 | 8 / 238 | 59 / 416 |

Fused across a release over 0.5 s, an event spans a median 44-103 m and 3.6-6.5 s, against 26-31 m
and 1.9-2.4 s for every event.
No flagged gap lies inside one detector-free reference zone. The typical one is the approach to
Sandown's C1, which the corner model draws as one 175 m corner. He dabs the brakes at the kink
(100-140 m), goes back on power, then brakes for the hairpin around 215 m. The kink application is
there on 36/36, 36/37 and 58/62 laps and the hairpin brake on 36/36, 37/37 and 61/62, but
light-deceleration blips under 25 m apart join the two on 9, 7 and 19 laps. On 7, 5 and 15 of those, C1's brake point
is the kink's instead of the hairpin's. At MK C10→C11 the fused event hides C11's brake on 2 of 19 laps.

**What it costs, and what a split costs.** The pedal band paints fragments, not events, and is
byte-identical under every variant. Brake events / lap moves only on Sandown 3h (8 → 9); Brake s by
at most 0.5 s on a lap. No ranked coaching row changes its reason. The real cost is BRAKING's
C1 row on the three Sandown recordings: onset σ **42.2 / 39.0 / 45.0 m** and span 128.6 / 114.9 /
149.7 m, a scatter the merge makes, not the driver. A split brings σ to 13.9 / 2.7 / 20.2 m. It also
hands other corners the wrong brake, because `lap_brake_points` takes the LAST onset in
[enter − 30 m, exit], and at close corner pairs the next corner's brake starts inside that window
once it is its own event. Sandown 3h's C6 brake point moves 13-38 m later on 11 laps, up to C7's brake (lap 54: 622.7 → 660.8 m,
past C6's exit), and its published "Braking later went with quicker passes" line stops firing
(ρ −0.419, p_Holm 0.013 → −0.186, 1.0). MK's C10 does the same on 6 laps, onto C11's brake. Three repairs of that
matching were measured, each with and without the split, and each drops a corner's own brake
points instead:

| matching rule | what it loses |
|---|---|
| window ends at this lap's apex (the lowest speed) | Sandown 3h C4 7 of 53, C6 5 of 57; MK C11 3; SD_19_09 C4 2 |
| window ends where the next corner's lead-in starts | Sandown 3h C6 10 of 57, C5 3; MK C3 5 of 12, C8 2 |
| an event belongs to the corner that holds its lowest speed | MK C8 15 of 17 and its published direction line; C10 10-16 |

**What would be new evidence:** a corner model that draws Sandown's kink and hairpin as two corners
(the C1 scatter then leaves the merge's reach); or a rule that gives each application to exactly one
corner and, on these four recordings, moves no corner's brake point onto another corner's brake and
drops none.

---

## 21. A leave-one-window-out refit of the de-drift's receiver shift — refused (TRUTH-9)

**The idea.** The session geometry removes each lap's receiver bias as ONE rigid shift, a plain
least-squares fit over 256 stations (`corners._rigid_shift`). A lap that runs wide through one corner
tilts that fit. On a stadium, a 1 m wide line through C1 (truth 0.128 s) reads 0.065 s at C1 and
0.030 s at C2 in the Corners table (DOMAIN-1). The proposed fix was to refit the lap's shift with
each corner window left out in turn. When one window moves the shift by more than a threshold set
above the noise, drop it and refit without it.

**How it was measured.** The need was measured first, against a rule written into the PR (#509)
before any number came back.
- **Fixtures.** Line changes of 0.5, 1 and 2 m were planted through every corner of the synthetic
  GoPro's 7-corner circuit and both turns of the stadium (`tests/_synthetic.line_change_laps`). They
  were run at GPS noise 0, 2 and 4.5 (the generator's own position noise, then the load-time
  boxcar), with three noise seeds, each paired with the same noise and no change. The de-drift's
  share of the Corners table's error is today's error minus the error with the changed lap's shift
  held at its no-change value.
- **The leave-one-window-out change** ‖T₋w − T‖ of every clean lap's applied shift. It was measured
  on the synthetic GoPro through the real loader (3 seeds; the kart drives the centreline, so every
  metre of it is noise) and on the four working-set recordings, read-only and jailed.
- **The refit itself** was prototyped exactly as specified: vector change, one absolute threshold,
  the relative shift refitted over the kept stations, and a conditioning guard. It was measured
  against the package's pass criteria and, on footage, against `p8_corner_anchor`'s curvature
  witness, which a translation cannot move. The probes are in the TRUTH-9 evidence folder.

| | stadium (2 turns) | circuit (7 corners) |
|---|---|---|
| tilt the de-drift absorbs, 0.5 / 1 / 2 m change | 0.21 / 0.42 / 0.85 m | ≤ 0.02 / 0.04 / 0.08 m |
| Corners table error, noise-free, worst corner | 31 / 63 / 126 ms | ≤ 1.9 / 3.8 / 7.8 ms |
| the de-drift's share of it, worst, noise 2 / 4.5 | 116 / 109 ms | 7.2 / 4.7 ms |

| leave-one-window-out change, largest per lap | median | max |
|---|---|---|
| synthetic GoPro, noise 0 / 2 / 4.5 (39 laps each) | 0.006 / 0.075 / 0.153 m | **0.066 / 0.18 / 0.37 m** |
| Sandown 3h / SD_19_09 / SD_30_08 / MK_18_09 | 0.31 / 0.32 / 0.30 / 0.18 m | 0.98 / 0.71 / 0.58 / 1.07 m |

**The rule fired, and the fix does not reach what fired it.** On 8 of the 168 circuit cells, all at
noise 2 or 4.5, the table is 10.5-12.3 ms off a line change, and the de-drift contributes 1.3-7.2
ms of that. On 49 of the 150 real laps the change is over the noise-4.5 maximum, and dropping the
window moves one of the lap's corner times by more than 10 ms. Built, the refit fails its own pass
criteria.
- **Off the stadium the signal is under the noise.** A line change's tilt, ≤ 0.08 m, is smaller
  than the change noise 2 alone makes, 0.18 m. Even noise-free it is barely above the 0.066 m
  produced by the load-time boxcar, which blurs laps of different speed differently. At the
  noise-4.5 threshold the refit acts on 0 of the 168 circuit cells. At 0.03 m it acts on every lap
  and still leaves 3 of the 6 noise-2 trigger cells and 1 of the 2 noise-4.5 ones over 10 ms; at
  noise 4.5 the cells over 10 ms go from 17 to 19.
- **On the stadium the evidence is symmetric.** Each turn alone fixes the shift's component along
  the straights, so leaving out either one moves the shift by the same amount, and the refit picks
  between them on a numerical tie. With 1 m at C2 the error goes 0.063 → 0.000 s; with 1 m at C1
  it goes 0.063 → 0.126 s. 28 of the 32 stadium trigger cells stay over 10 ms. With a rigid bias
  and noise and no line change at all, it acts on the noise and undoes the de-drift: at noise 4.5,
  seed 103, the three laps' misalignment goes 0.02 / 0.04 / 0.16 → 0.66 / 0.78 / 1.03 m.
- **On footage it would move a third of the laps, and not clearly towards the track.** It acts on
  20/61, 14/35, 9/36 and 6/18 laps and moves their corner times by a median 59, 56, 68 and 75 ms
  (up to 0.22 s). On those laps the curvature witness goes 1.00 → 1.00 m (126 boundaries better,
  114 worse) on Sandown 3h, 0.79 → 0.68 m (111/66) on SD_19_09, 0.81 → 0.78 m (58/62) on SD_30_08
  and 1.95 → 1.75 m (57/38) on MK_18_09.
- **No one absolute threshold is one noise floor.** The change grows with a window's share of the
  lap. Sandown's C1 is the 175 m kink and hairpin (§20), 24 % of the lap, and it is the window
  dropped on 34 of 61, 23 of 35 and 31 of 36 laps.

**What moves because of this refusal: nothing in the app.** The shift stays plain least squares
(`corners._rigid_shift` points here). In `tests/test_truth_matrix.py` the stadium's line-change
rows stay known-red with this section as their reason. Row 6c holds the circuit grid within 10 ms,
the tilt and the three noise floors, and asserts that the tilt stays under the noise-2 floor.

**What would be new evidence:** a way to name the window that moved that does not come from the
shift's own residuals, such as the curvature witness above or a surveyed centreline. Or a recording
with a known, marked line change in one corner whose Corners table the de-drift mis-states by more
than 10 ms.

---

## 22. A 2 s keyframe interval on VideoToolbox's H.264 exports — refused (#503)

**The idea.** The H.264 export through VideoToolbox passes no `-g`, so ffmpeg's `gop_size` default
of 12 becomes the encoder's maximum keyframe interval: 150 keyframes a minute at 30 fps, where the
libx264 fallback runs x264's 250 frames. At a fixed bitrate a keyframe costs the bytes of several
P-frames, so a 2 s interval (`-g 60` at 30 fps) should hand those bytes to the picture.

**How it was measured.** Against a rule written into the PR (#503) before any number came back, on
an M1 Pro with ffmpeg 7.1.1, 2026-09-30. `studio/dev/probes/vt_gop.py` renders a 20 s window ONCE
through the real `Renderer`, at 1080p30 "high", into a lossless ffv1 intermediate. It then encodes
that file with `_video_codec_args`' exact VideoToolbox argv, with `-g` 30, 60 or 120 appended or
none (ffmpeg's 12), three times each.
- **Per encode:** the yield (video bits over `vt_target_bitrate` × duration), the keyframes,
  counted because `-g` is only a maximum on VideoToolbox, and SSIM and PSNR against the
  intermediate.
- **Per interval, the scrub cost:** an accurate one-frame seek on the hardware decoder,
  `ffmpeg -hwaccel videotoolbox -ss T -i FILE -frames:v 1 -f null -`, at 20 seeded random frames.
  It is taken net of the same command at frame 0, a keyframe, which pays the process start and the
  decoder set-up and decodes nothing past it. Every call is interleaved in shuffled order across the
  intervals, so the machine's load lands on all of them alike.
- **Sources:** MK_18_09_26's lap 14, read-only and jailed; and a synthetic GoPro with a 1920×1080
  picture, since the default synthetic picture is too small for a 1080p export.
- **The rule:** adopt `-g round(2 × fps)` only if the real lap gains ≥ 0.002 SSIM or ≥ 0.3 dB PSNR
  at a yield within 0.70 ± 0.03, the synthetic loses nothing, and on both sources the seek at
  `-g 60` stays ≤ 100 ms net and ≤ 2× the default's gross.

| MK_18_09_26 lap 14 | keyframes a minute | yield | SSIM | PSNR | seek, net of frame 0 |
|---|---|---|---|---|---|
| no `-g` (ffmpeg's 12) | 150 | 0.700 | 0.98255 | 41.47 dB | 25 ms |
| `-g 30` | 60 | 0.700 | 0.98296 | 41.73 dB | 79 ms |
| **`-g 60` (2 s)** | 30 | 0.700 | 0.98305 | 41.82 dB | **135 ms** |
| `-g 120` | 15 | 0.700 | 0.98299 | 41.83 dB | 242 ms |

**The picture gain is real and passed its bar. The seek is what refused it.**
- At `-g 60` the real lap gains +0.35 dB PSNR and +0.0005 SSIM at the same 0.700 yield, and the
  synthetic gains +0.72 dB and +0.0011. Both clear their quality criteria.
- The same interval makes a random seek decode about five times further. At about 4 ms a frame on
  the hardware decoder, the net seek goes from 25 to 135 ms on the real lap and from 23 to 133 ms
  on the synthetic: over the 100 ms bound on both. The gross bound held (324 against 215 ms on the
  real lap, 1.51×).
- It is not the machine's load. The rule asked for a quiet Mac and none came, so the quietest seek
  phases decided, at load averages of 7.9 and 10.2. Across all four runs, at loads of 5.5 to 20,
  the net seek at `-g 60` was 133-146 ms, never within 30 ms of the bound, and the decode cost
  3.8-4.4 ms a frame.
- It is not the seeded draw. The 20 targets sit a median 36.5 frames past a `-g 60` keyframe
  against an expected 29.5, but even 29.5 × ~4 ms is about 120 ms.
- The encoder put its keyframes exactly on the maximum (600 / G in every 600-frame file), and the
  three encodes of each setting came out identical in size and in every metric.

**What moves because of this refusal: nothing in the app.** The VideoToolbox branch keeps no `-g`
(`_video_codec_args`' docstring points here), and `VT_H264_YIELD` stays 0.70. The libx264 path, the
alpha ProRes path and the finish frame's forced keyframe are untouched.
`tests/test_export_video.py` pins the absence of `-g` and `-keyint_min` at five frame rates and
both qualities, and that the forced finish keyframe stays.

**Re-run it** (outputs go to one `mkdtemp` under `$TMPDIR`; the recording is only read):

```bash
pixi run python -m studio.dev.probes.vt_gop synthetic   # its last line is the JSON: save it
pixi run python -m studio.dev.probes.vt_gop mk          # the same, for MK_18_09_26 lap 14
pixi run python -m studio.dev.probes.vt_gop verdict SYNTH.json MK.json   # applies the rule
```

**What would be new evidence:** a hardware decoder that brings the net seek at `-g 60` within
100 ms on both sources (today it decodes ~30 frames past the keyframe at ~4 ms each); or footage
on which the 2 s interval buys far more than +0.35 dB at the same yield, weighed against the seek
by a rule fixed before measuring.

---

## 23. Always-on antialiasing on the speed and Δ charts — refused (cool/compare-live-delta)

**The idea.** `plots_view` draws the lap curves with pyqtgraph's antialiasing OFF, so at 2x a speed
trace looks a little stepped where it climbs out of a corner. Turning it on is one option, and the
charts are the panel a driver reads longest.

**How it was measured.** On an M1 Pro (8 cores), macOS 26.5, PySide6 6.11.2 and pyqtgraph 0.14.0,
2026-11-15, in two runs at load averages 3.5-3.8 and 5.7-6.3. `studio/dev/probes/chart_aa.py`
opens the synthetic demo in the real `StudioWindow`, offscreen at 1728×1117, with one lap and the
best lap on the charts (the default two-curve overlay). It times two things the charts do, each
step including the repaint and one event-loop pass:
- **a cursor sweep:** `PlotsView.set_playhead_time` at 120 places in the best lap, which is the
  charts' share of the ~30 Hz tick;
- **a lap switch:** `set_laps([lap, best])` round six laps, the charts' share of a click in the lap
  table.

Each variant runs five times, interleaved with the others, and its figure is the median of the five
per-round medians. The demo is used so that anyone can re-run the probe without the owner's footage.
The table gives each figure as the range of the two runs' medians.

| synthetic demo, 2 laps on the charts | cursor sweep, per step | lap switch |
|---|---|---|
| antialiasing off (shipped) | **7.6-8.0 ms** | **22.7-23.3 ms** |
| on, every curve | 14.8-15.2 ms (+90-95 %) | 30.1-30.4 ms (+30-33 %) |
| on, the selected lap's two curves only | 11.0-11.5 ms (+44 %) | — |

Within a run the rounds of a variant spread by up to 0.6 ms in a sweep step and up to 1.0 ms in a
switch, with one switch round at 35.9 ms. An independent review measured a 1.4 ms spread on the
off sweep. Every spread is far smaller than the gaps between the variants. The design lane
measured the same shape on the owner's footage: 7.5 → 13.5 ms a sweep step (11.1 ms on the selected
lap only), and 21.5 → 27.5 ms a lap switch.

**The cost is the tick's, and the gain is small.**
- The ~30 Hz tick has 33 ms. With antialiasing on, the charts' share of each tick goes from a
  quarter of it to nearly half, before the map, the hero, the video and, in compare, the second
  pane have drawn anything. That is the budget `studio/README.md`'s perf invariants exist for
  ("plot curves are downsampled + clipped with antialias off").
- Antialiasing only the selected lap still adds 3.4-3.5 ms to every tick.
- At 2x the difference is a softer edge on the steepest stretches of a trace, and nothing that
  changes what a reader takes from the chart. Look at the two captures the probe saves.

**What moves because of this refusal: nothing in the app.** The module-level
`pg.setConfigOptions(antialias=False)` in `plots_view.py` stays, and its comment points here.

**Re-run it** (the demo is generated into one `mkdtemp` and removed, or read from `PACER_DEMO_MP4`):

```bash
pixi run python -m studio.dev.probes.chart_aa                       # its last line is the JSON
pixi run python -m studio.dev.probes.chart_aa --png-dir /tmp/aa     # also the 2x look, off and on
```

**What would be new evidence:** a pyqtgraph or Qt that draws antialiased polylines within about
1 ms of the aliased ones on this sweep; or a cursor that repaints without redrawing the curves
under it (a cached curve layer), which would take the curves out of the tick altogether.

---

## 24. A predicted lap time on the exported clip — refused (W3 design pass)

**The idea.** The clip burns the live Δ to the reference lap. Add the reference lap's time to it
and the strip can count a "predicted" lap time from the first corner, as lap timers do.

**How it was measured.** `studio/dev/probes/clip_refusals.py` (`figures`) takes every clean lap but
the best and reads, at a quarter, a half and three quarters of the lap's own time, the best lap's
time plus `Session.delta_at_lap`, the Δ the strip burns. It compares that with the lap's real time.
Two sources, neither anybody's driving:
- the synthetic demo (`make_demo`'s telemetry), whose laps differ mostly at C1, where the planted
  habit costs 8 of its 14 laps about half a second;
- `synth_gopro` with no planted habit, at GPS noise 2 (recording C's in the truth matrix's
  calibration), on the truth matrix's three seeds. Its laps differ a little at each of seven
  corners, so a lap's loss keeps arriving until the line.

| \|predicted − actual\|, median / worst decile | laps | at 25 % | at 50 % | at 75 % | at the line, worst |
|---|---|---|---|---|---|
| synthetic demo | 13 | 0.28 / 0.53 s | 0.13 / 0.28 s | 0.08 / 0.18 s | 0.02 ms |
| `synth_gopro`, no habit, noise 2, three seeds | 39 | 0.27 / 0.67 s | 0.18 / 0.56 s | 0.07 / 0.21 s | 0.08 ms |

**It is exact only where the clip already says the time.** At the line the prediction is the lap
time to within a tenth of a millisecond, and there the strip already prints the time. A
quarter of the way round, the median lap's prediction is about 0.3 s off on both sources, and one
lap in ten is more than half a second off. The prediction counts no loss the lap has not made yet,
and how much is still to come is what the driver cannot know either. The demo's laps are far more
alike than real ones, so this is the error at its smallest, and it is still several times the
0.07 s under which the app calls two laps level (`library.PB_PRECISION_S`). A number that reads
like a lap time and is wrong by that much would be read as a lap time.

**What moves because of this refusal: nothing in the app.** The strip keeps the live Δ.

**What would be new evidence:** a prediction that also prices the rest of the lap (from the laps
already driven, say) and comes within `PB_PRECISION_S` at half a lap on the synthetic sources here,
with its own error stated on the clip.

---

## 25. A scrolling Δ-trace band on the exported clip — refused (W3 design pass)

**The idea.** A band along the bottom of the clip that draws this lap's Δ to the best lap up to the
current frame, on a fixed scale, with a zero line and the ±0.1 s tie band: the Δ chart, burned in.

**How it was measured.** `studio/dev/probes/clip_refusals.py` on the synthetic demo. The band is
the design pass's: bottom-centre, 0.50 × 0.085 of the frame's short side, its scale the lap's own
largest |Δ| × 1.1 (at least 0.3 s), the trace a 2.6 px stroke at 1080p, rebuilt on
`export_video`'s own primitives. `figures` reads every clean lap's Δ as the export samples it and
places the band in the overlay's real 16:9 and 9:16 layouts. `paint` times it at 1080p and 4K on a
demo lap against the shipped overlay, interleaved over five rounds. `budget` runs a real 4K export
of one lap of `synth_gopro`'s circuit under a 4K test picture, as shipped and with a 10 ms
busy-wait added to every frame's paint, three rounds of each, interleaved (the demo's own picture
is too small for a 4K export, and the exporter never upscales).

| synthetic demo, 13 clean laps but the best | |
|---|---|
| largest \|Δ\| on a lap, median / the session's | 0.82 / 6.20 s |
| band pixels per 0.1 s at 1080p, on those scales | 4.4 / 0.6 px |
| a corner's rise of Δ on its lap's band, median | 2.32 px |
| corner rises thinner than the 2.6 px stroke | 52 of 91 |

| ms per frame, a demo lap, two runs at load averages 12-31 | 1080p | 4K |
|---|---|---|
| the shipped overlay | 6.4–9.1 | 9.9–10.7 |
| the band, added to it | 4.9–5.4 | 5.8–6.9 |

**What it would show is mostly flat.** On the median lap a tenth of a second is 4.4 px. A
corner's loss, which is what the trace exists to locate, rises a median 2.32 px, and 52 of 91 rise
less than the line is wide. Scaled to the session's slowest clean lap, the half-spin lap, a tenth
is 0.6 px. Whatever the band adds, the strip's Δ number already says to the hundredth.

**What it costs is a share of the overlay; the export budget is not measured here.** At 4K the band adds 5.8–6.9 ms to every
frame, 59–64 % of what the shipped overlay paints. The design pass's budget for a new element, 5 %
of a 4K export, could not be re-derived on this machine: at load averages of 20 to 34 the same 4K
export took 76 to 224 s, and the busy-wait's measured cost came out several times the wait itself,
which no pipeline can do. `budget` re-runs it on a quieter Mac. Until then the band is weighed
against the overlay it would join, and one element would add more than half of what the clip
already paints for all of them.

**It does not fit a 9:16 clip.** At 1080×1920 the readout and the map leave 516 px between them
once the overlay's one-margin gaps are kept, and the band is 540 px wide. Centred as in landscape,
it covers 21.8 % of its area with the map inset; anywhere else it is one more row over the picture.

**What moves because of this refusal: nothing in the app.** The clip keeps the Δ on the strip.

Shipped beside this refusal (#565): Broadcast's Δ bar, one horizontal fill for the current Δ
against the lap's own scale; it is not a trace and scrolls nothing.

**What would be new evidence:** a band whose corner rises clear its own stroke on most corners of
the demo, with a 9:16 place that covers no other element; and a `budget` run on a quiet machine
whose 5 % share of a 4K export holds the band's added paint.

---

## 26. Sector chips on the clip, purple or green only beyond the noise — refused (W3 design pass)

**The idea.** At each sector line the clip shows the split as a chip, purple or green when it is a
new best, as broadcasts do. The app's splits carry GPS noise, so a chip would turn colour only when
it beats the best earlier split by more than the noise: 2√2·σ, two σ of the difference of two
splits that each carry σ.

**The noise is published.** `docs/ACCURACY.md` gives the interior split's σ on a synthetic
recording, row 2 of `tests/test_truth_matrix.py`: 0.11 s at recording C's GPS noise (2) and 0.24 s
at B's (4.5). The gate is then 0.30 s and 0.68 s.

**How it was measured.** `studio/dev/probes/clip_refusals.py` (`figures`) places the golden gate's
two sector lines on two sets of recordings at both noises: the truth matrix's own three `synth_gopro`
seeds, on which row 2's σ was measured, and the demo's telemetry (its C1 habit included). On every
clean lap after the first it compares each split with the best earlier clean lap's in that sector,
as the app measures them and as the generator's truth has them.

| recordings | GPS noise | σ | gate | chips | new bests, app / truth | coloured wrong | over the gate, app / truth | largest true margin |
|---|---|---|---|---|---|---|---|---|
| `synth_gopro`, three seeds | 2 | 0.11 s | 0.30 s | 117 | 31 / 27 | 16 | 2 / 2 | 0.32 s |
| the demo's telemetry | 2 | 0.11 s | 0.30 s | 39 | 6 / 6 | 2 | 1 / 2 | 0.52 s |
| `synth_gopro`, three seeds | 4.5 | 0.24 s | 0.68 s | 117 | 27 / 27 | 24 | 0 / 0 | 0.32 s |
| the demo's telemetry | 4.5 | 0.24 s | 0.68 s | 39 | 8 / 6 | 4 | 0 / 0 | 0.52 s |

**Ungated, the colours are the noise's. Gated, they almost never come on.**
- Without the gate, 31 and 27 of the 117 `synth_gopro` chips would turn colour, and 16 at noise 2
  and 24 at 4.5 would be coloured where the truth has no new best, or grey where it has one.
- With the gate, 2 of the 117 colour at noise 2 and none at 4.5. The demo's real half second at
  C1, its largest true margin, clears the gate at noise 2 and not at 4.5.
- The truth itself rarely holds a margin that large: the three seeds' largest is 0.32 s, at the
  noise-2 gate's edge, and the demo's planted habit is the one exception. A chip that stays grey
  on almost every lap says nothing the split's number does not.

**What moves because of this refusal: nothing in the app.** Splits stay in the SPLITS grid, whose
★ and ▼ carry the column's own scale.

**What would be new evidence:** a split σ well under a tenth of a second at recording C's noise
(the gate then falls under the true margins above), or footage whose new-best margins clear the
gate on more than a few laps in a session.

---

## 27. Per-corner cells on the share card — refused (W3 design pass)

**The idea.** Under the best lap's time, a row of cells on the share card, one per corner, each the
best lap's gap through that corner: to the session's typical lap, or to the ideal lap.

**How it was measured.** `studio/dev/probes/clip_refusals.py` (`figures`) on the synthetic demo,
whose truth is known: the one corner where the laps really differ is C1 (the planted habit,
`make_demo.DEMO_HABIT`). For the best lap it reads each corner's gap to the CORNERS BY LAP typical
(`Session.corner_matrix`, the column median) against that corner's ▼ step, the gap to the ideal
lap per corner (`Session.ideal_segment_bests`), and the card's own opportunity
(`share_card.card_data`).

| best lap (L13), s | C1 | C2 | C3 | C4 | C5 | C6 | C7 |
|---|---|---|---|---|---|---|---|
| gap to the typical lap | −0.361 | −0.036 | −0.083 | −0.028 | +0.005 | −0.028 | −0.034 |
| that corner's ▼ step | 0.30 | 0.30 | 0.30 | 0.30 | 0.30 | 0.30 | 0.30 |
| gap to the ideal lap | 0.020 | 0.014 | 0.000 | 0.039 | 0.093 | 0.000 | 0.047 |

**Against the typical lap, six cells say nothing and the seventh is already on the card.** Six of
the seven gaps are inside their corner's ▼ step, the floor (`stats.MATRIX_SCALE_MIN_S`) under which
the app marks no corner at all. The one outside it is C1, which the card already names as its
biggest opportunity, +0.36 s.

**Against the ideal lap, the cells name the wrong corner.** The largest gap is C5, 0.093 s. The card
names C1, and C1 is where the demo's laps really differ. The ideal takes each corner's quickest
pass, so its gap is largest where the passes scatter most. C5 is the corner whose grip the generator
varies most from lap to lap. It is the order statistic of §2, cell by cell.

**What moves because of this refusal: nothing in the app.** The card keeps one opportunity, ranked
by the Coaching page's gate.

Shipped beside this refusal (#558): corner cards on the exported clip, one corner at a time, the
Corners table's own cell, "≈ best" inside 0.07 s; the share card still carries no corner cells.

**What would be new evidence:** a per-corner gap that clears its corner's own noise on more corners
than the card's one opportunity, and whose largest cell agrees with that opportunity on the demo.

---

## 28. A fade of the lap you left on the map, and a slide when the race card's rows reorder — refused (#557)

**The idea.** The motion pass (W2 design, section C) proposed three transitions. One shipped: the
hero Δ readout counts to its new value on a seek within a lap (`studio/motion.py`). The other two:
- on a lap change, the outgoing lap's line on the map **fades out** over 200 ms instead of
  vanishing;
- when the Lap race card's rows change order, they **slide** to their new places over 120 ms.

#557 built both, measured them and took them out again (`map_view.py` and `race_ladder.py` have no
diff from it). Its numbers lived only in the PR body. They are re-measured here.

**How it was measured.** `studio/dev/probes/motion_refusals.py` runs on the synthetic demo in the
real `StudioWindow`, offscreen at 1440×900. The map widget is pinned at 480×480 and grabbed at 2x,
so its figures do not move with a machine's fonts or layout. The probe needs no fade and no slide,
only what each one would have to move:
- **map:** for each of the 13 consecutive pairs of the demo's clean laps (lap n → n + 1, what
  playback does when it crosses the line), it grabs the map on the outgoing lap and on the incoming
  one, with the marker and the editing chrome hidden (`MapView.grab_clean`). It counts the pixels
  that differ, in every Line mode. Those pixels are everything a fade between the two lines could
  ever show, whatever its curve and whichever layer it is drawn on. They are given as a share of
  the whole map and as a share of what is drawn on it: the pixels that are not background in
  either grab, which are the track, every lap's faint trace, the corner labels and the line.
- **race:** the whole race at the app's ~30 Hz tick, speed 1, with nothing selected. The race is
  read as `RaceController` reads it (`lap_race.race_state`), and the card's rows as the card draws
  them (`race_controller.pack_rows`). A row is its pack's leading lap. It counts how often two rows
  that stayed on the card swapped order, which is a slide's whole job. It also counts how often the
  set of rows changed instead, and how often the laps changed order on the ladder underneath.

| synthetic demo, 13 lap changes | map pixels | of the map, median | of the map, largest | of what is drawn, median | of what is drawn, largest |
|---|---|---|---|---|---|
| Line: Off | 921,600 | 0.53 % | 0.70 % | 9.8 % | 12.9 % |
| Line: Speed | 921,600 | 1.29 % | 2.81 % | 23.9 % | 51.7 % |
| Line: Δ to best | 921,600 | 2.54 % | 3.34 % | 47.0 % | 61.4 % |
| Line: Δ rate | 921,600 | 2.56 % | 3.34 % | 47.8 % | 61.4 % |
| Line: Grip (est) | 921,600 | 1.15 % | 1.58 % | 21.5 % | 29.0 % |
| Line: Pedal (est) | 921,600 | 1.80 % | 2.09 % | 33.4 % | 38.4 % |
| Line: Elevation | 921,600 | 3.00 % | 3.26 % | 55.9 % | 60.0 % |

| synthetic demo, one whole race (14 laps, 51.1 s) | |
|---|---|
| card snapshots at the tick | 1535 |
| rows shown / packs hidden | 1–5 / up to 4 |
| two rows still on the card swapped order | 0 |
| the set of rows changed (a merge, a split, a row in or out) | 81 |
| the laps changed order on the ladder underneath | 217 |
| scrubbed in jumps of 3 s: jumps / rows swapped | 18 / 0 |

**On the map, a fade would be visible: the share of the whole map flatters it.** Most of the map
is empty field round the track, so the share of the whole map is small in every mode. Against what
is drawn, it is not:
- **Line: Off.** About a tenth of what is drawn changes (9.8 % median, 12.9 % at most).
  Consecutive laps drive the same track, so the plain line mostly moves a little sideways.
- **The coloured modes.** A lap change recolours most of the line: up to 61.4 % of what is drawn
  in Δ to best and Δ rate, and 47.0 % at the median. A fade there would be a colour cross-fade
  along most of the track, not a line moving off it.

So the refusal does not stand on the fade being invisible. It stands on what the fade cost and the
machinery it needed, both measured in #557 and both outside the tree:
- **The cost.** Its per-frame cost on the demo was +2.3 to +7.3 ms over a playback repaint, over
  the ~2 ms budget #557 set for a transition frame. That needed the fade itself, which is not in
  the tree, so it is #557's figure, measured on a loaded Mac, and not re-measured here.
- **The machinery.** Faded *under* the new line, the old one showed nothing. Faded *above* it, it
  needed a lifted layer and a second pool of colour buckets beside the map's own.

**On the race card, there is nothing to slide during playback.** At the playback tick, rows that
stayed on the card never changed order over the whole race. The laps underneath did: the ladder's
order changed on 217 ticks. Every one of those passes happened inside a pack, or showed on the card
as the rows re-forming, which they did on 81 ticks. That follows from how the card packs laps. A
lap that closes on the pack ahead comes within the tie threshold of that pack's leader before it
can pass the pack's last lap, so it joins the pack instead of overtaking its row. What changes on
the card is which rows exist, and a slide cannot animate that.

**The zero holds for playback only.** The card's progress bar scrubs, and a jump can put two rows
in a new order without their merging first. On the demo it did not: 18 jumps of 3 s swapped no
rows, because the demo's laps are far more alike than real ones. An independent review of this
section counted 24 swaps with 3 s jumps on synthetic fields whose laps differ by ±6 % in pace (its
own fields, not in the tree). A scrub jumps the field and re-forms the card anyway, and a slide
there would animate a jump the user just made.

**What moves because of this refusal: nothing in the app.** A lap change cuts the map line. The
race card redraws its rows in place. `studio/motion.py`'s module docstring points here.

**Re-run it** (the demo is generated into one `mkdtemp` and removed; its last line is the JSON):

```bash
pixi run python -m studio.dev.probes.motion_refusals
```

**What would be new evidence:**
- for the fade: a fade built on the map's own bucket pool, without a second one, whose added frame
  cost is measured on the demo under #557's ~2 ms budget for a transition frame;
- for the slide: a card whose rows can swap order without first merging during playback, on a
  session where the probe counts more than a handful of such swaps at the tick.

**Addendum, 2026-11-15 (W4 design pass): a variant of the fade's reopen condition was tried, and
the fade stays refused.** The design pass built the cheapest fade that needs no second pool of
colour buckets, though it is not built on the map's own pool either: at the lap change it grabs the
map once and lays that snapshot over the new line at falling opacity. On the demo, in the real
window offscreen at 1728×1117 (not this section's pinned 480 × 480 harness), the grab took 7.3 ms
and every frame of
the fade cost +10.0 ms over a plain map repaint (7.2 → 17.2 ms, medians of 100 frames), five times
#557's ~2 ms budget for a transition frame. These are the design pass's figures, measured once on a
loaded Mac, and not re-measured here: like #557's, they need the fade itself, which is not in the
tree. The addendum restates the reopen condition rather than closing it: a fade on the map's own
bucket pool is still unmeasured.

---

## 29. Brake-onset spread bands on the map — refused (W4 design pass)

**The idea.** On the map's line, at each braking zone, a band over the middle half of the clean
laps' brake-onset positions with a tick at the median: where you brake, and how much it wanders.

**How it was measured.** `studio/dev/probes/story_refusals.py` on the synthetic demo. For each
braking zone it takes the clean laps' brake onsets from `Session._brake_rows_by_lap`, the one list
every braking surface reads, and sets their middle-half spread (the interquartile range) against
the distance the kart covers in one GPS fix there: the best lap's speed at the median onset times
the recording's fix interval, 0.1 s. An onset lands on a fix, so a band narrower than one fix would
draw the receiver's sampling, not the driver: the design drew a band only where it clears one fix.
The map is the real window's, pinned at 480 × 480 as in §28, where a metre is 1.25 px.

| synthetic demo, clean laps, m | C1 | C2 | C3 | C4 | C5 | C6 | C7 |
|---|---|---|---|---|---|---|---|
| middle-half spread of the onset | 5.4 | 1.4 | 1.3 | 1.4 | 2.0 | 1.3 | 0.7 |
| one GPS fix at that speed | 2.7 | 2.2 | 2.7 | 2.3 | 2.5 | 2.3 | 2.5 |

| C1, the planted habit's corner, split by the generator's truth | laps | median onset | middle-half spread |
|---|---|---|---|
| laps with the habit | 8 | 89.8 m | 2.9 m |
| laps without it | 6 | 95.1 m | 2.1 m |

**Six bands would not be drawn, and the seventh mixes two groups.** At six of the seven zones the
spread is inside one fix. The one that clears it is C1, the corner of the demo's planted habit
(`make_demo.DEMO_HABIT`): its band would be 6.7 px long, and it mixes two groups that each spread
about one fix, so the band is mostly the gap between them. Split by the truth, the habit laps brake
about 5 m before the clean ones. The
finding itself is already on C1's Coaching row as a measured "brake later" (ρ −0.81, Holm p 0.003),
and BRAKING gives its σ 3.9 m and span 13.3 m; BRAKING already reads a σ at or under one fix's
quantisation (`driving.ONSET_QUANT_M`, 1.5 m) as the receiver's, not the driver's. The design pass
reported the same shape on the one real recording it measured: the bands that clear
one fix there are a few pixels long at the map's scale, and the one band long enough to read was
§20's merged brake event, the kink and the hairpin fused into one onset.

**What moves because of this refusal: nothing in the app.** The map keeps the lap's own brake
glyphs; the spread stays a column of BRAKING.

**Re-run it** (the demo is generated into one `mkdtemp` and removed; its last line is the JSON):

```bash
pixi run python -m studio.dev.probes.story_refusals
```

**What would be new evidence:** a receiver with a higher fix rate, or a band whose length is
readable at the map's scale and says something BRAKING's σ column and the brake-direction line do
not.

---

## 30. "What you nailed" — refused (W4 design pass)

**The idea.** The debrief says where you lost time. Say also where you did not: a positive line,
"C4: nailed it", for the corners you drove as well as your best every time.

**How it was measured.** `studio/dev/probes/story_refusals.py` on the synthetic demo. The line is
the mirror of an opportunity. An opportunity is the time the typical pass gives away against the
best one, so praise is a corner whose typical pass (`Session.corner_report`'s median) is within
0.07 s of its best pass, the step under which the app calls two times level
(`library.PB_PRECISION_S`).

| synthetic demo, clean laps, s | C1 | C2 | C3 | C4 | C5 | C6 | C7 |
|---|---|---|---|---|---|---|---|
| typical pass − best pass | 0.381 | 0.050 | 0.083 | 0.066 | 0.088 | 0.028 | 0.081 |
| fires under ±0.035 s of cell jitter, share of draws | 0.00 | 0.77 | 0.35 | 0.56 | 0.10 | 0.95 | 0.36 |

The second row is the same rule after 1,000 seeded draws that move every corner cell by a uniform
amount within ±0.035 s, half the level step, as §32's jitter does.

**On the demo it fires at 3 of 7 corners: C2, C4 and C6, and C4 by 0.004 s. Three things are
wrong with it.**
- **It is unstable.** Under the jitter the same number of corners fires in only 39 % of the draws,
  and only C6 fires in more than four draws in five. A line that would praise C4 or C3 depending on
  a few hundredths of noise is praising the noise.
- **It contradicts the Coaching page.** It would praise C2 and C4, which the Coaching page ranks as
  opportunities on the demo (`Session.coaching_opportunities`, ranked C1, C2, C7, C4).
- **It measures the quickest pass, not repeatability.** Typical − best is a gap to a minimum, the
  order statistic of §2. More laps bring a lower best pass while the median holds, so the line
  fires less the longer you drive.

The design pass reported it fired at no corner of the one real recording it measured.

**What moves because of this refusal: nothing in the app.** The debrief names opportunities only.

**What would be new evidence:** a rule for praise that holds under this jitter on the demo, does
not praise a corner the Coaching page ranks, and fires on a real session replicated on a second.

---

## 31. An ideal-lap film — refused (W4 design pass)

**The idea.** The ideal lap is stitched from each segment's best pass. Stitch the video the same
way: one clip that plays each corner and straight from the lap that drove it quickest.

**How it was measured.** `studio/dev/probes/story_refusals.py` on the synthetic demo. It reads the
ideal's own partition (`Session.ideal_segment_bests`): its donors, and each cut where one donor
hands over to the next. At every cut it measures the jump in speed and in position between the
outgoing donor's last point and the incoming donor's first, each on its own lap. For scale, it
measures the same two jumps at 200 seeded random cuts between two clean laps at one partition edge.

| synthetic demo | |
|---|---|
| segments of the partition | 15 |
| donor laps / cuts between them | 9 / 12 |
| ideal / best lap | 44.63 / 44.95 s |
| a cut every | 3.4 s |
| speed jump at a cut, median / largest | 0.6 / 2.9 km/h |
| position jump at a cut, median / largest | 0.5 / 0.7 m |
| a random cut between two clean laps, 90th percentile | 5.7 km/h, 1.0 m |

**The seams are not the problem.** The cuts jump less than random cuts at the same edges between
two clean laps: the largest speed jump is under the random cuts' 90th percentile, and the position
jump is under a metre.

**Three other things are.**
- **It cuts every 3.4 s.** 12 cuts in 44.6 s, between 9 laps, is a montage, not a lap.
- **It shows an order statistic as a lap that was driven.** The ideal is the sum of each segment's
  minimum (§2). No lap was driven at it: on the demo it is 0.32 s under the best lap, and §2
  measured no recombination of the laps at or left of the best lap, 0 of 20 on both recordings. It
  falls as the session grows: 0.72 / 0.60 / 0.36 s for a doubling of the laps on §5's three
  fixtures. A film would give that number a picture, which is the overclaim the ideal-lap
  disclosure exists to prevent.
- **The exporter renders one continuous window.** `export_video.resolve_video_source` resolves one
  global window [t0, t1) to its chapters. A film of 13 shots needs a new export that renders many
  windows and joins them, for this one feature.

**What moves because of this refusal: nothing in the app.** The ideal stays a number with its
sample beside it, and a lap's export stays one window.

**What would be new evidence:** a multi-window export that exists for another reason, and a
presentation that reads as a montage of best passes, not as a lap.

---

## 32. Two session moments that do not survive the app's level step — refused (W4 design pass)

**The idea.** A short list of the session's moments, each a jump to the lap it names: the best
lap, the biggest single-corner loss, the longest run with no ▼, the best 5-lap run, the best 3-lap
run, and the best pass through each corner.

**How it was measured.** `studio/dev/probes/story_refusals.py` on the synthetic demo, three ways.
- **Under the app's level step:** 1,000 seeded draws. In every draw each lap time moves by a
  uniform amount within ±0.07 s (`library.PB_PRECISION_S`, the step the app treats as level), and
  each corner cell, the CORNERS BY LAP grid's time through one corner, by a uniform amount within
  ±0.035 s, every one independent of the others. The grid's ▼ marks are recomputed from the moved
  cells. For each moment it counts the share of draws in which the lap it names changes.
- **At the measured timing noise:** the step is wider than the measured timing noise (σ 0.0247 s,
  `docs/ACCURACY.md` row C). At that σ, Gaussian per lap and σ/√2 per corner cell, the probe
  re-measures every row with another 1,000 seeded draws: the second column.
- **Pure noise:** a moment can be stable and still be what noise alone would produce. A ▼ marks a
  cell above its corner's 90th-percentile deviation from the median, floored at 0.30 s
  (`stats._typical_and_scale`). The probe draws 1,000 seeded grids of pure Gaussian noise at the
  demo's 14 laps and 7 corners, each corner its demo median plus noise of that corner's own sample
  σ (0.031–0.757 s), and again at its robust σ, 1.4826 × the median absolute deviation (0.022–0.080
  s). It marks them through the real constructor (`stats.corner_matrix`) and reads each grid's ▼
  and its longest run with no ▼.

| synthetic demo, 14 clean laps, 1,000 draws | under the level step | at the measured σ | |
|---|---|---|---|
| best pass per corner, at six of seven corners | 0.13–0.69 | 0.07–0.69 | a tie of 6–11 passes within 0.07 s |
| best pass per corner, at C5 | 0.00 | 0.00 | alone |
| longest run with no ▼ | 0.00 | 0.00 | 8 laps |
| best 3-lap run | 0.16 | 0.04 | 5 of 12 runs within 0.07 s of the best |
| best lap | 0.00 | 0.00 | 0.215 s clear of the next |
| biggest single-corner loss | 0.00 | 0.00 | |
| best 5-lap run | 0.00 | 0.00 | 3 of 10 runs within 0.07 s of the best |

| longest run with no ▼, 14 laps × 7 corners | ▼ per grid | median | 90th percentile | grids at least as long as the demo's |
|---|---|---|---|---|
| pure noise at each corner's sample σ, 1,000 grids | 4.1 | 5 laps | 8 laps | 0.16 |
| pure noise at each corner's robust σ, 1,000 grids | 0.0 | 14 laps | 14 laps | 1.00 |
| the demo | 2 | 8 laps | | |

**Two moments name a lap the noise could have picked.**
- **The best pass through a corner** is a tie at six corners of seven, 6 to 11 passes within
  0.07 s of the quickest. Which one is named changes in up to 69 % of the draws under the level
  step, and in 7 % to 69 % at the measured σ. Only C5's stands alone, the corner whose grip the
  generator varies most (§27). Naming one pass of eleven as "the" best pass is a coin toss
  presented as a moment.
- **The longest run with no ▼** never changes under either jitter, and that was the wrong test. The
  demo's grid carries 2 ▼ in all, at C5 and C6; at every other corner the 0.30 s floor wins. Noise
  at each corner's sample σ, which those same outlier cells inflate, carries 4.1 ▼ a grid, and the
  demo's run of 8 laps is the 90th percentile of what it produces on the same grid, and 16 % of the
  noise grids run at least as long. At each corner's robust σ the floor wins everywhere, the noise
  grids carry no ▼, and the run is the whole session every time. The run's length counts the
  session's outlier cells, not its calm. The review of the story-core PR that raised this measured
  the same shape on its own noise grids, with 7 corners (median / 90th percentile of the longest
  run): 7 / 12 at 14 laps and σ 0.15 s, 12 / 18 at 36 laps and σ 0.15 s, 4 / 5 at 40 laps and σ
  0.30 s. Those are the review's figures, not re-measured here.

**The best 3-lap run is not built as a second moment.** That is a design choice, not a noise
refusal: at the measured σ it changes in only 4 % of the draws (16 % under the level step). The
figure already exists, as the Stats page's "race pace · best 3-lap mean" tile
(`stats_panel.py`, from `stats.best_consecutive_mean`: the best mean of `stats.RACE_PACE_N`
consecutive clean laps). A moment naming its laps would name a tie group: 5 of the 12 runs sit
within 0.07 s of the best. The best 5-lap run moment already carries the idea, named with its ties.

**The moments the design pass kept survive the jitter.** The best lap, worded as a tie where
another lap is within 0.07 s of it; the biggest single-corner loss; and the best 5-lap run, named
with its ties. On the demo none of them changes at either noise level. The pure-noise test was run
only on the run with no ▼, the one moment that is a count of the grid's marks.

**What moves because of this refusal: nothing in the app.** The Stats page's MOMENTS section
(#570) carries the three kept moments; these two are not in it.

**What would be new evidence:** a corner's best pass that changes in fewer than one draw in ten at
the measured σ, on more than one recording; and, for the run with no ▼, a rule for a calm stretch
that pure noise at the session's own lap count and spread does not satisfy at that rate.

---

## 33. "N of M slow laps lost their time at one corner" — refused (W5 discovery)

**The idea.** The Laps table cues each slow lap with its culprit corner ("▼ C5"). Add the cues up
and say where they gather: one sentence, "N of M slow laps lost their time at" the corner most cues
name.

**How it was measured.** `studio/dev/probes/corner_refusals.py` on the synthetic demo. The slow
laps and their culprits are the app's own (`Session.slow_lap_notes`, `story.culprit_cue`): a lap
names one culprit when one ▼ corner holds more than half of its excess, and two when two ▼ corners
together do. The count is the most cues that name one corner. A lap whose cue names two corners
counts at both; a slow lap with no culprit counts in M and at no corner.
- **The null.** Every slow lap with a culprit draws it independently, each of the grid's corners
  equally likely (a two-corner lap draws two different corners). p is the chance that some corner
  gathers at least the observed count, computed exactly, not drawn.
- **Why that null.** Equal odds is the null most generous to the sentence: weighting the corners
  unequally makes a shared corner likelier by chance. The last row of the second table shows it,
  with each corner weighted by its own lap-to-lap σ on the demo (0.029–0.203 s, over the 13 clean
  laps that are not slow). That row is an illustration, not a second null: C1's σ is inflated by
  the planted habit, which splits its laps into two groups.

| synthetic demo | |
|---|---|
| clean laps / corners | 14 / 7 |
| slow laps / with a culprit | 1 / 1, Lap 6 at C5 |
| the sentence | "1 of 1 slow laps lost their time at C5" |
| p under the null | 1.00 |

| 7 corners, M slow laps with one culprit each | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|
| fewest at one corner that beats chance, p ≤ 0.05 | 3 | 4 | 4 | 4 | 5 | 5 |
| p of half of them or more at one corner | 0.39 | 0.65 | 0.16 | 0.29 | 0.07 | 0.13 |
| the same, each corner weighted by its σ | 0.59 | 0.83 | 0.47 | 0.66 | 0.43 | 0.58 |

**On the demo the sentence reads "1 of 1", at p 1.00: a single slow lap gathers at its own corner
whatever that corner is.** Two things are wrong with it beyond that.
- **The demo has too few slow laps for the count to mean anything.** It has 1 slow lap of 14.
  Over 7 corners the count beats chance only at about two-thirds of the slow laps or more (4 of
  6, 5 of 8), and "half of them at one corner" never does at any M in the table. The sentence
  would print a coincidence in the same words as a pattern.
- **It cannot see the demo's planted habit.** The habit (`make_demo.DEMO_HABIT`, §29) costs 8 of
  the 14 laps about 0.4 s each at C1, and C1 carries no ▼ on any lap. The habit laps ARE the
  typical pass there: the 6 habit-free laps sit 0.36–0.38 s under it, and a ▼ marks only a loss
  over the typical pass beyond its step (0.30 s). The one slow lap is a habit lap, and its cue
  names C5. Counting cues counts what went wrong once; the habit is the Coaching page's C1 row.

| the gate at p ≤ 0.05 under equal odds, M | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|
| fires by chance if culprits follow each corner's σ | 0.104 | 0.046 | 0.147 | 0.284 | 0.171 | 0.288 |

**The smaller feature is refused too: print the sentence only when p ≤ 0.05.** The gate would
have to compute p under equal odds, the most lenient null, because one session cannot estimate
how often each corner holds a culprit: on the demo that rate is 1 cue in 14 laps. If culprits
instead follow each corner's spread, the gate's own threshold fires by chance far more often than
1 time in 20: 4 of 6 at one corner (p 0.034 under equal odds) would print by chance in 28 % of
sessions with 6 slow laps. The weights are the σ row's, so the figure is an illustration of the
direction, not a measured rate; the right null needs per-corner culprit rates across sessions.

**What moves because of this refusal: nothing in the app.** Each slow lap keeps its own cue and
note; a finder that steps through the slow laps names no corner they share.

**Re-run it** (the demo is generated into one `mkdtemp` and removed; its last line is the JSON):

```bash
pixi run python -m studio.dev.probes.corner_refusals
```

**What would be new evidence:** a session whose cues gather at one corner at p ≤ 0.05 under this
null, and a second session where they gather at the same corner.

---

## 34. Splitting a long corner window into two corners — refused (W5 discovery)

**The idea.** A long corner window can hold two slow points. Split it into two corners, so a loss
is placed in its own half of the bend.

**How it was measured.** `studio/dev/probes/corner_refusals.py` on the synthetic demo.
- **The windows.** Each corner's window on the reference lap, and its ringed apex (`Corner.apex`,
  where the map draws the apex dot and the ring), in metres into the window.
- **The bends.** In every window, the pooled curvature the corner detector reads
  (`corners.pooled_curvature`): its local maxima, and the lowest |κ| between the first and the
  last, against the window's peak. The second dip is the same between the maxima that reach half
  of the peak.
- **Where a pass loses its time.** Along each lap's own projected window, the time since entry
  against a typical pass. Its slope is smoothed over 3 m, about two GPS fixes, and the peak loss is
  where that slope is highest. Every ▼ pass is measured against its corner's median pass, and each
  habit lap at C1 against the median of the 6 habit-free laps.

| synthetic demo, m | C1 | C2 | C3 | C4 | C5 | C6 | C7 |
|---|---|---|---|---|---|---|---|
| window length | 51.8 | 54.1 | 48.8 | 56.3 | 55.6 | 58.6 | 70.6 |
| ringed apex, into the window | 27.0 | 27.5 | 25.3 | 29.7 | 28.4 | 29.3 | 35.7 |
| maxima of the pooled curvature | 2 | 1 | 1 | 1 | 4 | 1 | 5 |
| lowest curvature between them, share of the peak | 0.989 | – | – | – | 0.024 | – | 0.965 |
| the same between maxima over half the peak | 0.989 | – | – | – | 0.869 | – | 0.965 |

| every ▼ pass on the demo, m into the window | peak loss | ringed apex | peak − apex |
|---|---|---|---|
| Lap 6 at C5 | 26.1 | 28.4 | −2.3 |
| Lap 6 at C6 | 6.7 | 29.3 | −22.6 |

| C1, the 8 habit laps against the 6 habit-free laps | |
|---|---|
| loss over the window | 0.357–0.428 s |
| peak loss | 22.0–32.4 m into the window |
| farthest peak from the ringed apex | 5.4 m |
| share of the loss lost before the apex | 50–57 % |

**The renumbering is the argument that carries the refusal.** A split adds a corner, so every
corner after it takes the next id, on every recording. On the demo C2–C7 would become C3–C8, and
every corner id published for it would move: the Coaching ranking §30 quotes, the corner columns of
§29–§32, the slow-lap note "C5 +2.84 s" that `tests/test_story.py` pins, and every test pin, card
and figure that names a corner.

**And the demo gives no window to split.**
- **C1 is not long.** It is the sixth longest of seven windows. A length rule that reached it would
  split every window but C3.
- **C1 is one bend.** The pooled |κ| has two local maxima 3.0 m apart (24.8 and 27.8 m), and
  between them it never falls below 98.9 % of its peak. There is no valley for a split to sit in.
- **So are five of the other six.** C2, C3, C4 and C6 have a single maximum. C7, the longest, has
  five within 11.3 m, and between them its curvature never falls below 96.5 % of its peak.
- **C5 is the one exception by this metric, and it is an edge ripple.** Its lowest curvature
  between the first and last maxima is 2.4 % of its peak, because its last maximum, at 54.8 m of
  a 55.6 m window, reaches only 2.6 % of the peak: a ripple on the exit after the bend has
  straightened, with no turn in it. Between the maxima that reach half of the peak (27.0–32.3 m)
  the curvature never falls below 86.9 % of it. Split there, the second corner would be a straight.
- **The demo has no ▼ pass at C1** (§33), so it cannot show where a ▼ pass loses its time there.
  Its two ▼ passes are elsewhere, both on Lap 6: at C5 the peak loss sits 2.3 m before the ringed
  apex, at C6 22.6 m before it, on the entry.
- **The loss C1 does carry straddles its apex.** Every habit lap's peak loss is within 5.4 m of the
  ringed apex, and half of its loss (50–57 %) comes before it. But the habit is the generator's
  choice, a grip × brake factor over the whole corner (`synth_gopro.Habit`), so where its loss
  falls is a property of the demo, not evidence about real corners. It only shows that on the demo
  a split near the apex would cut the one planted habit into two corners, each with about half.

**What moves because of this refusal: nothing in the app.** A long window stays one corner. Where
in it the time went stays the Coaching row's Entry·Apex·Exit thirds (`Session.phase_report`).

**Re-run it:** `pixi run python -m studio.dev.probes.corner_refusals`, as §33.

**What would be new evidence:** a window whose pooled curvature has a real valley, two bends the
detector merged, and whose ▼ passes lose their time on one side of it, on more than one recording.
That would have to be worth renumbering every corner after it.

---

## 35. Running the export-video tests on a pull request only when export code changes — refused (#605)

**The idea.** The 24 export-labelled registrations (the 18 `test_export_*` video suites and the 6
`videotoolbox.*` checks) are 796 of CI's 2,904 test-seconds (27 %, the 0.14.0 release run). Skip
them on a pull request that touches no export code; run them on every push to main, tag, manual
and weekly run. The owner approved trying it on 2026-11-18. #605 built it: a ctest label, a CI
step that reads the PR's changed paths, a path list, and a guard test that derives the list from
the labelled tests' imports.

**How it was measured.** The guard's static import closure (AST, transitive, function-local imports
and the `tests/_*.py` helpers included) of the labelled tests, and a replay of the changed-file
lists of the last 200 merged pull requests (#405–#604) against the path list it forced.

| | |
|---|---|
| app modules the export tests import, transitively | **91 of 93** (all but `studio/__main__.py`, `studio/gearing.py`) |
| why | every export suite builds a `Session`; `studio/session.py` imports `coaching`, `stats`, `focus`… at module level; five suites import `studio/app.py` |
| a sound path list therefore holds | `studio/*.py` whole, plus the core, the build, the bindings and every test helper |
| of the last 200 merged PRs, would have skipped the tier | **25** (docs, changelog fragments, non-export tests) |
| of the last 60 (#545–#604) | **0** |
| saving on a skipped run | ≈ 200 s of the 736 s ctest step — an **estimate**: no skipped run was ever measured |
| saving on every other run | ≈ 0 |

**Why it is refused.** A narrow list that would skip the tier on, say, a `coaching.py` change is
unsound: the guard watched it fail on 75 uncovered files, `studio/coaching.py` among them (imported
by `session.py`, which every export suite loads). The sound list runs the tier on any app change,
so in a product-heavy mix it saves nothing, and on the rare docs-only pull request about 3 minutes.
Against that it costs a label, a CI script, a path list and a guard test, each to keep in step with
every new suite and every new import. The owner's other ruling the same day — the slowest suites do
the same checking in less time, measured against planted defects — cuts every run instead.

**What moves because of this refusal: nothing.** Every registration runs on every pull request.

**What would be new evidence:** lazy imports in `studio/session.py` (and the export path) that
make the export tests' import set small, so a sound list could skip the tier on most app changes;
or a pull-request mix dominated by docs-only changes, measured over a campaign.

---

## 36. "Your quickest passes reached their slowest point N m later" (early / late apex) — refused (W13 measure)

**The idea.** `CornerStat.apex_dist`, the lap odometer at a pass's minimum-speed fix, is computed
for every lap and corner and shown nowhere. Say where the minimum came on the quickest passes
against the typical ones: "your quickest passes reached their slowest point 3 m later here".
Only where the difference beats the fix spacing, by the rule the discovery lane wrote down before
any number came back: |Δ| ≥ max(2 fix spacings, 2σ), and the bootstrap 95 % interval excludes 0.

**How it was measured.** `studio/dev/probes/apex_line.py`, through the real loader, on the
synthetic GoPro at the truth matrix's three seeds and GPS noise 2 and 4.5.
- **The plant.** The generator's kart holds a flat speed over each arc, so it has no single
  minimum. The probe gives every pass one: the speed becomes max(physics, a bowl RISE km/h deep at
  ±10 m), with its bottom on the corner's true apex, or SHIFT metres later on the even laps at
  C1, C3, C5 and C7. C2, C4 and C6 stay unshifted as the same run's null. SHIFT is 0, 2, 5 or
  10 m. RISE is 1 or 3 km/h.
- **The figure.** Each pass's `apex_dist`, placed in the reference lap's frame through the lap's
  own projected corner window, the window `lap_corner_stats` measures in. Δ is the median of the
  planted laps minus the median of the others (7 against 7), with 2,000 bootstrap resamples. σ is
  the corner's own per-pass scatter, the one the app could compute.
- **The refinement.** The vertex of a parabola through the 3 and the 5 fixes around the minimum.
- **The controls.** The same plant with the noise switched off. On every plant-free recording, the
  split the sentence would really use: the quickest third of the passes by corner time (4 of 14)
  against the rest. And the demo.

| noise · bowl | per-pass σ | one fix | rule fires, nothing planted | 2 m planted: reads / fires | 5 m | 10 m | laps per group, 2 m at 80 % |
|---|---|---|---|---|---|---|---|
| 2 · 3 km/h | 1.90 m | 2.01 m | 1 of 48 | 1.67 m / 1 of 12 | 5.33 m / 9 of 12 | 8.04 m / 12 of 12 | 23 |
| 2 · 1 km/h | 2.69 m | 1.95 m | 0 of 48 | 0.42 m / 0 of 12 | 3.87 m / 3 of 12 | 8.41 m / 9 of 12 | 45 |
| 4.5 · 3 km/h | 2.41 m | 1.99 m | 1 of 48 | 1.57 m / 1 of 12 | 5.53 m / 5 of 12 | 7.95 m / 12 of 12 | 36 |
| 4.5 · 1 km/h | 4.11 m | 1.96 m | 1 of 48 | −0.12 m / 0 of 12 | 3.37 m / 1 of 12 | 5.51 m / 4 of 12 | 104 |

σ is robust (1.4826 × the median absolute deviation) over 147 unplanted passes. "Reads" is the
median Δ over the 12 planted cells (4 corners × 3 seeds). The last column is the normal
approximation for the interval alone, at the median's efficiency, from that σ.

**The rule is safe, and it can see almost nothing a driver would call early or late.**
- **It does not invent.** With nothing planted it fires in 0 or 1 of 48 cells, and in 0 of the 84
  quickest-third cells of the plant-free recordings.
- **2 m is invisible.** A shift of about a kart's length reads −0.12 to 1.67 m and fires in 0 or 1
  of 12 cells. Its interval needs 23 to 104 laps in each group, where a session gives 7 or so.
- **5 m is a coin toss.** It fires in 1 to 9 of 12 cells, depending on how deep the bowl is.
  Only 10 m of a deep bowl is reliable, and it reads 8 m. Across the grid the interval held the
  planted shift in 75 to 92 % of cells, against its nominal 95 %.
- **The fix spacing is not the floor.** The per-pass scatter is 0.9 to 2.1 fixes. Where the
  minimum is flat, the minimum fix wanders by the bowl's own width, not by one fix.
- **The parabola tightens it and changes nothing.** Five fixes cut σ to 1.38 / 2.24 / 1.82 /
  3.25 m (in the table's row order), and 2 m still fires in 0 of 12 cells everywhere.
- **The estimator itself is sound.** With the noise off, the 5 m plant reads 4.60 to 5.41 m.

**On the demo the rule fires once, at a corner with no minimum to place.** It fires at 1 of 7
corners: C7, where the quickest 4 of its 14 passes reach their slowest fix 14.25 m earlier (interval −18.96 to
−5.92 m, against a 9.34 m threshold). The order of the passes seems to agree: the apex position
against the corner time gives Spearman ρ +0.61. But C7 is a 41.9 m arc of constant radius, and
on every lap the demo's true speed stays within 0.1 km/h of its minimum over 31.8 m of it. The
demo has no early or late at C7, and agreement with the time order does not make the sentence
true. On the real recording, checked privately, the rule fired at 0 of 7 corners over a whole
session.

**What moves because of this refusal: nothing in the app.** `apex_dist` stays an input to the
brake-point bound (`driving_channels.py`). The Corners table keeps its apex speed, with no
apex position.

**Re-run it** (69 recordings in one `mkdtemp`, then removed, about 16 minutes on a quiet Mac. The
last line is the JSON):

```bash
pixi run python -m studio.dev.probes.apex_line
```

**What would be new evidence:** a receiver with a higher fix rate, or a speed channel fine enough
that a 2 m shift fires on a session's laps at this rule's false-positive rate. Or a real session
where the rule fires at a corner and a second session of the same track fires there the same way.

---

## 37. "On your quickest passes you were N m wider at the apex" (line offset at the apex) — refused (W13 measure)

**The idea.** Name the line habit as a number: on the quickest passes against the typical ones,
how far wider or tighter the kart ran at the apex. The discovery lane set the bar before measuring:
build only if a 1 m change is recovered with the null's 95th percentile under half of it.

**How it was measured.** `studio/dev/probes/apex_line.py`, on the same recordings as §36.
- **The figure.** The signed perpendicular offset from the reference lap at the ringed apex
  (`Corner.apex`), wide positive, from `SessionGeometry.offsets`, the offsets the de-drift itself
  fits. It was read three ways:
  - raw;
  - de-drifted: the lap's `lap_shift`, the app's rigid per-lap receiver shift, taken off;
  - referenced to the straights: the de-drifted offset minus its median 20–60 m outside each end
    of the corner window, a local re-zero against drift within the lap.
- **The plant.** The even laps run AMP metres wide through C1, C3, C5 and C7, a raised cosine over
  each corner's arc (the shape of `tests/_synthetic.line_change_laps`). The circuit the GPS fixes
  are drawn from is displaced, so the plant passes through the receiver's noise, drift and the
  load-time boxcar like a real line. AMP is 0, 0.5, 1 or 2 m.
- **Per lap and per group.** Per lap is one planted lap minus the other group's median. Per group
  is the median against the median. Each is set against its null: the unplanted corners, and
  every corner at AMP 0.

| | raw | de-drifted | referenced to the straights |
|---|---|---|---|
| per-lap null, 95th percentile, noise 2 / 4.5 | 1.84 / 4.10 m | 0.79 / 1.59 m | 0.68 / 1.34 m |
| group null (7 against 7), 95th percentile, noise 2 / 4.5 | 0.63 / 1.41 m | 0.48 / 1.06 m | 0.44 / 0.82 m |
| 1 m planted, group median reads, noise 2 / 4.5 | 0.66 / 0.66 m | 0.58 / 0.63 m | 0.57 / 0.32 m |
| 1 m groups over the group null's 95th percentile, noise 2 / 4.5 | 6 / 2 of 12 | 8 / 3 of 12 | 8 / 1 of 12 |
| 2 m planted, group median reads, noise 2 / 4.5 | 1.34 / 1.26 m | 1.17 / 1.00 m | 1.18 / 1.03 m |
| lap-to-lap bias, SD of a lap's mean offset, noise 2 / 4.5 | 0.78 / 1.85 m | 0.13 / 0.27 m | 0.16 / 0.32 m |

**Drift is not what stops it.** A receiver's drift between laps puts 0.78 m (noise 2) and 1.85 m
(noise 4.5) of bias on a lap's raw offset, which would swamp a 1 m change. The app's rigid
de-drift takes it down to 0.13 and 0.27 m. Re-zeroing against the straights does no better than
that.

**Two other things stop it.**
- **A metre does not read as a metre.** A 1 m line reads 0.58 m de-drifted at noise 2, and with
  the noise switched off it still reads 0.33 to 0.66 m. A plant as long as a corner is about as
  long as the load-time position boxcar (13 fixes), which spreads it out. The rule's clause on
  the null passes at noise 2 by 0.02 m (0.48 m under 0.5 m), but a figure that prints a 1 m habit
  as half a metre fails the clause on recovery. At noise 4.5 the null alone fails it (1.06 m).
- **The split the sentence needs manufactures a line.** On the plant-free recordings, where every
  pass drives the centreline, the quickest third by corner time (4 of 14) reads tighter than the
  rest. The
  median is −0.22 m at noise 2 and −0.47 m at 4.5. The interval excludes 0 in 8 of 42 cells at
  noise 2 (all 8 tighter) and in 11 of 42 at 4.5 (10 tighter). Noise-free it excludes 0 at 6 of 7
  corners, by 0.02 to 0.07 m. A pass whose fixes read tighter also reads quicker through the
  window the same fixes place. On the demo, whose kart drives one line at every corner, the
  sentence would say "tighter" at 3 of 7 corners (C1, C2 and C6). The real recording was checked
  privately: the interval excludes 0 at 5 of 7 corners, every one on the side this artifact
  predicts, so the footage cannot tell a habit from the instrument.

**What moves because of this refusal: nothing in the app.** The map draws each lap's line, and
the compare draws two. Neither prints a lateral figure.

**Re-run it:** `pixi run python -m studio.dev.probes.apex_line`, as §36.

**What would be new evidence:** a position chain whose 1 m line change reads within 0.25 m of
1 m noise-free, and an outcome split that does not read a line from the same fixes that time the
pass. For example, laps grouped by a decision made before the session, or by the video, with a
group null under 0.5 m at noise 4.5.

---

## Related refusals that already live in the tree

These were also refused on measurement and already have their reasoning in code, so they are not
repeated here — follow the pointer:

- **The friction-circle brake-release channel** (#260, #264) —
  [`friction-circle-release-investigation.md`](friction-circle-release-investigation.md).
- **Sideslip rate and wheel hop** (#299) — [`measured-channels-2026-09.md`](measured-channels-2026-09.md).
- **A weather API for the session record** (#258) — the rejection and its reason are in
  `studio/session_record.py`'s module docstring. Nothing leaves this Mac.
- **Five of the nine UK Government Analysis Function table symbols** (`[x]`, `[z]`, `[r]`, `[f]`,
  `[c]`) — each refused with its own reason in `studio/data_quality.py`.
