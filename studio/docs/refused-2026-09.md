# Features measured and refused — 2026-09

Thirteen features were built far enough to **measure**, and the measurement said not to ship them. The
work was real; the evidence lived only in a pull-request body, where nobody re-proposing the idea
would ever look. It is written down here so the next person to suggest one of these starts from the
numbers instead of from the idea.

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
Re-measured after #300 warped every lap, which moved every cell below except the best lap:

> **⚠ STALE — NOT RE-MEASURABLE (T16).** The table below was measured on D24 before #335 changed
> corner matching, and it is known stale. #339 read 0060's ideal as 65.637 s after #335 and
> 65.864 s after its own change, against the 65.464 s below. D24 is no longer available, so the
> table cannot be re-measured. It is the record of that measurement, not what the app computes
> today, and whether the verdict under it still holds cell for cell is unverified.

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

**The verdict holds on the new numbers.** Both reasons below are still true of every cell: no dot
sits at or left of the best lap on either recording, and the spread is narrower than the laps
driven on both. The support shrank (23.0 → 19.5 s, 13.2 → 12.7 s) mostly at its slow end, where
fewer of the slowest cells are admitted as donors after #300. The 20 dots, which are what a plot
would draw, moved by at most 0.07 s.

**How it is measured**, since #272 did not write it down. 0060 is `GX020060` + `GX030060`; 0062 is
`GX010062` + `GX020062` + `GX030062`. A recombination draws each of the 25 corner/straight segments
independently and uniformly from the clean laps whose cell MAY DONATE, which is two conditions and
not one: the cell is admitted (`corner_model.MAX_DONOR_SPAN_DEV`) and, since #339, resolved — its
two boundaries matched on track rather than interpolated — with the model's own two-stage fallback,
a segment nothing may donate on dropping back to that segment's admitted cells. This paragraph said
"admitted" alone until H9, while the stand-in that re-measures the record
(`tests/test_measured_figures.py`) has applied both conditions since #339: the published method was
describing a rule the check had stopped using. The cells below predate #339 and are not re-measured
by this correction. Support is the sum of the per-segment minima and maxima, so its
left end is the ideal. The sd is the square root of the summed per-segment variances, divided by
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
   achievable one, and the central limit theorem over 25 independent picks makes its spread
   *narrower than the laps actually driven* (0.60× the real sd on both recordings). The plot would
   claim to show what you can do while **understating real lap-to-lap variation**. That is the
   exact overclaim the ideal-lap disclosure exists to prevent.

The brief expected the plot might be "too tight to say anything". The measurement found the
opposite, and worse.

**Two alternatives were measured and also not shipped:**

- **Jackknife** (drop one lap, recompute, partition held): moves the ideal by at most **0.200 s**
  and **0.073 s**, with only **13/38** and **20/65** laps moving it at all (#272: 0.231 s and
  0.135 s, 15/38 and 18/65). So "is this fragile to one lap?" is already answered — **no** — and a
  plot to answer it would be decoration.
- **Ideal over any N of these laps** does work as a dotplot, but it is a **different statistic**: it
  needs a stochastic accessor with golden-gate care, and the repo has already ruled against baking
  per-recording empirical constants into shipping copy.

**Why the headline ideal needs no uncertainty band at all:** it is exactly the minimum over the laps
driven — an **order statistic**, with no sampling uncertainty. Its only instability is in **N**, and
N is a monotone ladder, not a distribution. That ladder is what `corner_model.IdealSample` publishes
and what the Stats page's sample disclosure states.

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

Two rows carry `REASON_LINE`, which has no behavioural lever at all, so the partition cannot even be
formed for them — a "range" feature that abstains on the reason the app falls back to is not a
feature.

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
