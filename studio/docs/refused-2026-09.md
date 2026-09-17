# Features measured and refused — 2026-09

Five features were built far enough to **measure**, and the measurement said not to ship them. The
work was real; the evidence lived only in a pull-request body, where nobody re-proposing the idea
would ever look. It is written down here so the next person to suggest one of these starts from the
numbers instead of from the idea.

**These stay refused unless someone brings NEW evidence.** "It would be nice to have" is not new
evidence. What would be: a recording whose numbers come out differently from the ones below, or a
different statistic that answers the same question without the defect named here.

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
independently and uniformly from the clean laps whose cell is admitted as a donor
(`corner_model.MAX_DONOR_SPAN_DEV`). Support is the sum of the per-segment minima and maxima, so its
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
