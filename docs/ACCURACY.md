# How accurate is Pacer's lap timing — and how do we know?

![Pacer lap-time error vs a real transponder: mean within ±0.003 s, σ ≈ 0.05–0.09 s across two recordings](media/accuracy.png)

**Pacer's lap times are validated out-of-sample against a real transponder** — the kind of hard
ground truth a race series uses to score a session. Across the two recordings we tested, the timing
is essentially **unbiased** (mean error within **±0.003 s**), with a spread of **σ ≈ 0.05–0.09 s**.
On a ~68 s kart lap that σ is about **0.13%**, and the per-lap correlation to the transponder is
**r ≥ 0.99**. That is at the noise floor of 10 Hz GPS: the remaining error is per-fix positional
noise on the samples that straddle the finish line, and we can show — with data — that it is
irreducible from the streams a GoPro records.

## The validated numbers

Two GoPro recordings of the same kart, each holding a full session of laps, timed by Pacer's
**default shipping pipeline** and compared lap-for-lap against the transponder. The residual is
`pacer lap time − transponder lap time`, measured only on clean (non-dropout) laps.

| Recording | GPS quality (median DOP) | Laps | Mean error | σ (std) | Correlation |
|-----------|--------------------------|------|------------|---------|-------------|
| **A** — higher-noise GPS | 2.4 | 300+ | **+0.0030 s** | **0.0871 s** | 0.992 |
| **B** — cleaner GPS | 1.4 | 850+ | **+0.0015 s** | **0.0527 s** | 0.997 |

The two rows tell the whole story: **recording-level GPS quality sets the floor.** Recording B has
roughly half the spread of A for one reason only — its GPS was cleaner (median DOP 1.4 vs 2.4, ~1%
vs ~4% of fixes gated). Nothing in the algorithm distinguishes the two; the hardware's fix quality
does. Both are unbiased to well under a hundredth of a second.

## How it's measured

- **True-clock timing.** On a **GPS9 camera (Hero 9 and newer)**, every GPS sample carries its own
  timestamp on the camera's clock. Pacer times laps on *that* clock — not the video/sample clock,
  which drifts (~0.1% fast). A lap time is `(finish crossing instant) − (start crossing instant)`,
  where each instant is interpolated along the chord between the two real GPS samples straddling the
  start/finish line.
- **Default pipeline, nothing special.** These numbers come from the shipping configuration —
  GPS9 true-clock, clock rate = 1.0, boxcar smoothing w=13 — not a tuned-for-the-benchmark variant.
- **Auto-locked to the transponder.** The transponder log and the Pacer laps are aligned by
  duration-correlation, so the comparison is objective and needs no hand-matching of laps.
- **Reproducible.** The harness is [`studio/dev/_validate_wallclock.py`](../studio/dev/_validate_wallclock.py).
  The transponder CSV is a private reference input and is **never committed** — the method is public;
  the ground-truth file stays out of the repo.

## Three findings that show where the limit actually is

The interesting part of this work is not the headline number — it's that we went looking for ways to
push it lower and found, with out-of-sample evidence, that there is nothing left to win on timing.
Three results explain why.

**1. The residual is the raw GPS positional-noise floor.** The remaining error is dominated by
per-fix positional noise on the (present, clean) samples that straddle the finish line. We confirmed
this by ruling everything else out: the error does not correlate with the finish-line chord spacing,
does not track any single GPS-quality predictor across both recordings, and does not shrink under a
principled Kalman/RTS smoother. It is set by the recording's intrinsic 10 Hz fix quality — the same
DOP that separates recording A from B — and is irreducible from the GPMF streams a GoPro exposes.

**2. GPS-dropout laps have their gaps *mid-lap*, not at the finish line.** This is the decisive
finding. The natural assumption is that a lap whose finish crossing falls inside a GPS hole is badly
mis-timed, and that fusing the IMU (dead-reckoning, a Kalman bridge, a spline) across the hole would
fix it. We checked every dropout lap on both recordings: in **every** case the gap sits deep inside
the lap, and **both** start/finish crossings land on clean ~0.1 s chords. Because a lap time depends
only on those two crossing instants — and they sit on real samples far from the hole — **no
gap-bridging method can change these laps' times at all.** The hypothesis was inverted by the data:
the dropouts don't sit where the error would have to be for fusion to help. (What a mid-lap hole
*does* corrupt is the lap *distance* on the map, which Pacer already reconstructs with a gap-aware
speed integral.)

**3. Sub-tick "refine the millisecond at the line" tricks buy nothing at 10 Hz.** Phone lap-timers
that run on 1 Hz GPS use IMU acceleration to interpolate the exact instant the car crossed the line,
because at 1 Hz the chord between fixes is a full second wide. At Pacer's 10 Hz the crossing chord is
already ~0.05 s (about a 0.5 m car length at speed), so the constant-velocity crossing interpolation
is already **sub-sample** — a higher-order spline moves the crossing instant by under a millisecond.
The trick that matters at 1 Hz is moot here; we are already past it.

Together these say the same thing three ways: **Pacer's lap timing is at the practical limit for this
data.** We tested GPS+IMU sensor fusion, Kalman/RTS smoothing, Doppler-aided positioning, and
map-matching — out-of-sample, on *both* recordings — and none improved the timing residual on both.
The one method that looked best on recording B was the *worst* on recording A: a textbook overfit,
caught precisely because we refuse to validate on a single recording. The full research write-up,
with every technique evaluated and rejected on evidence, is in
[`studio/docs/gps-accuracy-research.md`](../studio/docs/gps-accuracy-research.md).

## Why this matters

Most consumer and phone telemetry tools time laps off the **video or sample clock**, which drifts on
the order of 0.1% — enough to quietly bias every lap in a session. Pacer times off the camera's own
**GPS true-clock** and has **validated that timing against a real transponder**, out-of-sample, on
footage of the kind you already own. That is the defensible differentiator: transponder-grade lap
timing, from a GoPro you already have, for free — with the receipts to prove it.
