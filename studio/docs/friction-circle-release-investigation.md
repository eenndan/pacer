# Friction-circle brake-release detection: measured, and NOT shipped

A proposal to detect an early trail-brake release with no pedal channels, from the shape of the
total planar g vector alone. It was measured end-to-end on both D24 recordings and **rejected**.
This note records the numbers so the idea is not rebuilt from scratch; it is attractive enough to
be proposed again.

**The proposal.** During a correct rotation through the friction circle, total planar
|a| = hypot(lateral, longitudinal) stays near the driver's own observed maximum while the vector
rotates from longitudinal to lateral. Releasing the brake too early should leave a dip in |a|
between end-of-braking and peak lateral g. Every competitor detects this from brake-pressure and
throttle channels; the friction-circle formulation needs neither, only the gravity-compensated
accelerometer the app already has.

**The verdict.** The dip is measurable but it is (a) mostly an artifact of the two axes of the
friction circle being smoothed over different windows, and (b) unable to tell a fast attempt at a
corner from a slow one. Three of the four conclusions below are what killed it; the fourth is that
the confound the idea has to beat could not be separated from the signal.

## Method

Fixtures: `GX020060`+`GX030060` (38 clean laps) and `GX010062`+`GX020062`+`GX030062` (65 clean
laps), 12 detected corners each — 456 and 780 lap×corner attempts.

Per attempt, read on the g-meter's **native 50 Hz grid** (never on the 10 Hz GPS sample times,
which would destroy the resolution the dip lives at):

- `t_lat` = the peak |lateral g| inside the projected corner window.
- `t_rel` = end of braking: the last sample at or before `t_lat` still decelerating harder than
  the session's own `driving.Thresholds.theta_b` (0.163 g and 0.160 g on these two recordings).
- the dip = `min(|a(t_rel)|, |a(t_lat)|) − min |a|` strictly between them, and that over the
  anchor as a fraction.
- a matched **NULL**: the identical detector on an equal-duration window on the preceding
  straight, ending where this corner's braking begins.

Corner windows are the existing drift-gated projection (`corners.project_boundaries`, one warp per
lap). Discrimination is a within-corner Spearman rho against that lap's corner time, pooled
sample-weighted over corners, with a permutation test that shuffles attempt labels **within** a
corner — so each corner's own difficulty and geometry are held fixed and the only thing tested is
whether the metric orders that corner's own attempts.

The |a| noise floor, for scale: a robust sigma of the high-frequency residual against a 0.5 s
median is **0.069–0.075 g**. Nothing below is a detectability problem.

## 1. The window the metric needs is empty in the median attempt

At the app's shipped settings (IMU lateral, GPS-derived longitudinal):

| | 0060 | 0062 |
|---|---|---|
| brake still on at peak lateral (within one 50 Hz sample) | **243 / 456 = 53 %** | **398 / 780 = 51 %** |
| a measurable release→peak window | 174 = 38 % | 342 = 44 % |
| coast (release→peak lateral), median | **0.000 s** | **0.000 s** |
| coast, p75 / p90 | 0.300 / 0.788 s | 0.360 / 0.860 s |
| lateral g already loaded at release, median (as a fraction of the peak) | **1.000** | **1.000** |

In the median attempt the brake comes off **at or after** peak lateral g. The premise assumes a gap
between end-of-braking and peak lateral in which a dip can sit; on this data that gap usually does
not exist. Whatever these drivers are doing wrong, "releasing before the tyres are loaded" is not
visible as a systematic gap.

## 2. Where a window does exist, the dip barely separates from a straight

Shipped settings, on the attempts that had a window:

| | 0060 (n=174) | 0062 (n=342) |
|---|---|---|
| dip, median / p90 | 0.163 / 0.497 g | 0.141 / 0.534 g |
| NULL dip (straight), median / p90 | 0.005 / 0.213 g | 0.010 / 0.171 g |
| dip **fraction**, median / p90 | 0.149 / 0.403 | 0.122 / 0.463 |
| NULL dip fraction, median / p90 | 0.008 / **0.359** | 0.031 / **0.600** |
| AUC, corner dip vs straight dip | 0.717 | 0.684 |

In absolute g there is some separation (AUC 0.68–0.72). Normalised — which is the form any
threshold would have to use, because the anchor varies corner to corner — the null's p90 reaches
0.359 and 0.600 against an observed p90 of 0.403 and 0.463. **The distributions overlap.** A
straight piece of track produces dip fractions as large as a corner does.

## 3. The dip is largely an artifact of the smoothing asymmetry

`gmeter` low-passes the horizontal accel over `_LOWPASS_S` = 0.15 s, which sets the bandwidth of
the **lateral** axis (and of the IMU forward axis). The longitudinal series the app actually
displays is the GPS speed derivative boxcarred over `LONG_SMOOTH_S` = 0.35 s. So |a| combines two
axes whose smoothing supports differ by 2.3x — and a brake release is exactly the step-like event
a wider boxcar smears into a ramp.

Sweeping the longitudinal window and re-measuring, everything else identical (median dip, g):

| longitudinal window | 0.05 | 0.10 | 0.15 | 0.25 | **0.35 (shipped)** | 0.50 |
|---|---|---|---|---|---|---|
| 0060 | 0.076 | 0.061 | 0.066 | 0.086 | **0.163** | 0.128 |
| 0062 | 0.021 | 0.014 | 0.039 | 0.103 | **0.141** | 0.204 |

And with the two axes given the **same** bandwidth:

| matched configuration | 0060 | 0062 |
|---|---|---|
| both axes 0.15 s (lateral + IMU longitudinal, one shared low-pass) | **0.000 g** | **0.000 g** |
| both axes 0.35 s | 0.005 g | 0.016 g |

The dip rises with the window — Spearman rho(window, median dip) = **+0.771** on 0060 and
**+0.943** on 0062, a ~10x swing on 0062 off one constant (it is not strictly monotone: 0060
inverts between 0.05 and 0.10 s and again between 0.35 and 0.50 s) — and collapses to nothing when
the mismatch is removed, on both recordings. It is substantially a property of the filter chain,
not of the driving. That is also why the IMU longitudinal (which
shares the lateral's 0.15 s low-pass) reports a median dip of exactly 0.000 g on both fixtures
while the GPS longitudinal reports 0.14–0.16 g on the same footage, same corners, same lateral
channel. A headline number that flips that far on an internal choice the driver cannot see is not
one to put on screen.

## 4. It does not discriminate

Within-corner Spearman rho, permutation p (20 000 shuffles), dip fraction against that lap's
corner time — the sign that would support the idea is positive (a bigger dip = a slower corner):

| | 0060 | 0062 |
|---|---|---|
| GPS longitudinal | +0.111 (p=0.162, n=172) | +0.013 (p=0.816, n=342) |
| IMU longitudinal | −0.022 (p=0.712, n=284) | +0.046 (p=0.282, n=562) |

None significant, and the sign is not even stable across fixture or source. The same holds against
corner+following-straight time and against lap time. Dip fraction vs **exit speed** reaches
p=0.017 once (0062, GPS long, rho=−0.134) with the predicted sign, and p=0.065 on 0060 — but on the
IMU longitudinal, the same footage, it is +0.053 and −0.028. Across 32 tests (8 metrics × 2 sources
× 2 fixtures) that is what chance looks like.

## 5. The confound could not be separated

The idea has to beat an obvious alternative: |a| also dips when the driver is simply **not near
the limit** — a slow, tidy corner where the tyres were never loaded. It does not beat it.

The load level itself (peak |a| over the attempt, against the driver's own observed p99) vs corner
time: rho −0.272 and −0.239, **p=0.0000**, on 0062 for both longitudinal sources — the strongest
and most replicable effect measured anywhere in this investigation. On 0060 the same test is
−0.038 and +0.005, i.e. nothing. And dip fraction vs load level is significant in two of the four
configurations **with opposite signs** (+0.162, p=0.004 on 0062/GPS; −0.153, p=0.012 on 0060/IMU).

So the dip and "never loaded the tyres" are entangled in a way that is not even consistent in
direction between two recordings from the same driver at the same track. Restricting to genuinely
loaded attempts does not rescue the dip (0060/GPS: rho −0.007, p=0.891).

## Honesty constraints that would have applied, had it shipped

Recorded because they apply to any future attempt at this, and to the existing g surfaces:

- The reference is the driver's **own observed maximum |a|** over the session — an order statistic,
  not a grip limit. Here p99 = 1.472 g (0060) and 1.415 g (0062), with p100 at 2.024 and 1.897.
  Never a "percentage of available grip": the friction limit is unknown and moves with tyre
  temperature and track state.
- Not "trail braking" — nothing here is measured from a pedal. It is the shape of a rotation
  through the friction circle.
- The smoothing window and sample rate belong **where the number is read**, not in a tooltip:
  §3 is the demonstration that the window *is* the number.
