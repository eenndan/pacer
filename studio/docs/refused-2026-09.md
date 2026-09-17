# Features measured and refused — 2026-09

Two features were built far enough to **measure**, and the measurement said not to ship them. The
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

| | 0060 (38 laps) | 0062 (65 laps) |
|---|---|---|
| ideal / best lap | 65.149 / 68.228 | 66.781 / 68.201 |
| recombination support | [65.149 … 88.153] = **23.0 s** | [66.781 … 79.980] = **13.2 s** |
| recombination sd | 1.178 (**0.63×** the real lap-time sd) | 0.558 (**0.59×**) |
| best lap's percentile in it | **1.157th** | **0.001st** |
| the 20 dots span | 68.377 … 73.039 | 68.832 … 71.057 |
| **dots at or left of the best lap** | **0 of 20** | **0 of 20** |

**Two reasons to refuse it:**

1. **The mark lands off the plot.** On both recordings the cloud centres on the **median** lap, with
   the ideal *and* the best lap stranded off the left edge. The annotation the feature exists to
   carry is outside the plotted body.
2. **The premise is wrong at the root.** A uniform recombination is the **average** stitching, not an
   achievable one, and the central limit theorem over 25 independent picks makes its spread
   *narrower than the laps actually driven* (0.59–0.63× the real sd). The plot would claim to show
   what you can do while **understating real lap-to-lap variation**. That is the exact overclaim the
   ideal-lap disclosure exists to prevent.

The brief expected the plot might be "too tight to say anything". The measurement found the
opposite, and worse.

**Two alternatives were measured and also not shipped:**

- **Jackknife** (drop one lap, recompute): moves the ideal by at most **0.231 s** and **0.135 s**,
  with only **15/38** and **18/65** laps moving it at all. So "is this fragile to one lap?" is
  already answered — **no** — and a plot to answer it would be decoration.
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

## 4. A minimum segment length for the ideal-lap partition — refused (M4)

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
