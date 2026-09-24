# How accurate is Pacer's lap timing — and how do we know?

![Pacer lap-time error vs official timing: σ 0.025 s, 0.053 s and 0.087 s over 121 clean laps across three recordings, mean error within ±0.003 s](media/accuracy.png)

**Pacer's lap times are validated out-of-sample against official timing** — the kind of hard
ground truth a race series or a circuit scores a session with: a real transponder log, and a
circuit's own published timing sheet. Over **121 clean laps** across three recordings, the per-lap
spread against that ground truth is **σ 0.025 s, 0.053 s and 0.087 s**, and the timing is
essentially **unbiased** (mean error within **±0.003 s**). On a ~68 s kart lap the worst
recording's σ is about **0.13%**. That is at the noise floor of 10 Hz GPS: the remaining error is
per-fix positional noise on the samples that straddle the finish line, and we can show — with data
— that it is irreducible from the streams a GoPro records.

That is a modest sample, and it is the honest one: 121 is the number of laps actually compared,
not the span of lap IDs they occupied in a 24-hour transponder log.

## The validated numbers

Three GoPro recordings, all at Daytona Milton Keynes on its anticlockwise layout, each timed by
Pacer's **default shipping pipeline** and compared lap-for-lap against official timing. A and B
are **D24**, the Daytona 24-hour race of 23–24 May 2026 — A is `GX0*0060`, B is `GX0*0062` —
checked against the race's transponder log in June 2026; their rows are that measurement, as
recorded. C is a sprint race on **18 September 2026** (`MK_18_09_26`, two chapters), validated in
September 2026 against the circuit's own **Club Speed** timing, whose heat pages are public — on
footage that is on the development machine, by one command a real-footage check re-runs. The
residual is `pacer lap time − official lap time`, measured only on **clean** laps — racing laps
(≤ 72 s on both clocks) with no GPS dropout.

| Recording | Ground truth | GPS quality (median DOP) | Laps aligned | Clean laps measured | Mean error | σ (std) |
|-----------|--------------|--------------------------|--------------|---------------------|------------|---------|
| **A** — D24, higher-noise GPS | transponder log, June 2026 | 2.4 | 57 (transponder laps 302–358) | 48 | **+0.0030 s** | **0.0871 s** |
| **B** — D24, cleaner GPS | transponder log, June 2026 | 1.4 | 65 (transponder laps 856–920) | 59 | **+0.0015 s** | **0.0527 s** |
| **C** — MK sprint, 18 Sep 2026 | Club Speed timing, September 2026 | 1.25 | 15 (sheet laps 2–16) | 14 | **+0.0010 s** | **0.0247 s** |

Those are lap *counts*. An earlier version of this page printed "300+" and "850+" for the same two
rows: those were the transponder log's lap **ID ranges** — it runs continuously for a 24-hour race
across many drivers — not the number of laps compared. The aligned count is the length of the
range (358 − 302 + 1 = 57; 920 − 856 + 1 = 65), and the clean count drops the GPS-dropout and
non-racing laps on top of that. The correction is a factor of ten, in the unflattering direction.

Row C's window is the race itself: its 15 laps are laps 2–16 of one driver's row on the sheet (lap
1 there runs from the start, before the first line crossing a recording can time). The one aligned
lap left out of the clean 14 is the race's opening flying lap, over the 72 s racing cap, and it
reads −0.348 s; the 14 clean residuals all lie between −0.033 s and +0.044 s. The recording also
holds a four-lap qualifying run, with a single lap under the cap — too short a fingerprint to lock,
so it is not measured at all. The receiver gated 10.4% of the recording's fixes, every one of them
in its first seven minutes while it acquired; inside the race none were gated, and the median DOP
there was 1.15.

The rows tell the whole story: **recording-level GPS quality sets the floor.** Recording B has
roughly half the spread of A for one reason only — its GPS was cleaner (median DOP 1.4 vs 2.4, ~1%
vs ~4% of fixes gated). Nothing in the algorithm distinguishes the two; the hardware's fix quality
does. C, with the lowest median DOP of the three, has the smallest spread. All three are unbiased
to well under a hundredth of a second.

## How it's measured

- **True-clock timing.** On a **GPS9 camera — a Hero 11 or a Hero 13**, every GPS sample carries its
  own timestamp on the camera's clock. Pacer times laps on *that* clock — not the video/sample clock,
  which drifts (~0.1% fast). A lap time is `(finish crossing instant) − (start crossing instant)`,
  where each instant is interpolated along the chord between the two real GPS samples straddling the
  start/finish line. GPS9 is narrower than it sounds: GoPro's metadata spec introduces it with the
  Hero 11, records it *removed* on the Hero 12 ("No GPS receiver in HERO12" — that camera cannot be
  lap-timed at all), and brings it back on the Hero 13. Hero 5 through Hero 10 and the Max emit GPS5
  only, which carries no per-sample clock; those recordings fall back to the video clock and every
  duration derived from it is muted and labelled estimated. The detection is per recording, off the
  stream itself (`studio/load.py::_used_gps9_trueclock`), not off a model name.
- **Default pipeline, nothing special.** These numbers come from the shipping configuration —
  GPS9 true-clock, clock rate = 1.0, boxcar smoothing w=13 — not a tuned-for-the-benchmark variant.
  Row C was timed on the built-in Daytona Milton Keynes start/finish line, exactly as a first open
  of the recording times it; its own best-fit clock rate comes out at 0.999983, 17 ppm from 1.
- **Auto-locked to the official timing — and the lock is *unique*.** No lap is hand-matched. The
  app's per-lap *duration* sequence is correlated against every candidate contiguous window of the
  ground truth and the alignment is taken at the maximum. Because that window is *chosen* to
  maximise r, the r value at the winner is not an independent accuracy statistic — the **margin
  over every rival window is**. Against the transponder log the fingerprint matches at **r ≥ 0.99
  at exactly one offset and below 0.29 at every other** (a margin of ≈ +0.70 on recording A), and
  three further signals bound the same window independently — the GPS9 wall clock, elapsed time
  since the green flag, and the long pit/driver-change laps that bracket the stint. A sprint has
  none of those, and its sheet holds every driver of every heat that day, so for row C the
  fingerprint is tried against all of them:

  | Recording | Searched | r at the lock | Best rival's r | Margin | Residual σ, lock vs nearest rival |
  |-----------|----------|---------------|----------------|--------|-----------------------------------|
  | **A** | one transponder log | 0.992 | below 0.29 | **≈ +0.70** | not recorded |
  | **B** | one transponder log | 0.997 | not recorded | not recorded | not recorded |
  | **C** | 122 windows of 99 driver rows, 11 heats | 0.9998 | 0.83 | **+0.17** | 0.025 s vs 1.10 s — **44×** |

  C's margin is small for a reason that is not ambiguity: every driver in one race shares its slow
  opening laps and its yellows, so over 14 laps another driver in the same race reaches r = 0.83 —
  and r is blind to level and scale, so it cannot see that driver missing every lap by seconds. The
  residual σ can: every window but one leaves at least 1.10 s, the lock leaves 0.025 s. The rule
  that calls a sprint lock unique — the best-r window also leaves the smallest residual σ, r ≥ 0.95,
  and every other window's σ is at least 3× the lock's — was committed to the harness before this
  sheet was first locked against it. Take the locked driver's row out of the sheet and the same
  laps lock onto nothing.
- **Reproducible — by one command, on footage on the development machine.** The harness is
  [`studio/dev/_validate_wallclock.py`](../studio/dev/_validate_wallclock.py): given a recording
  and a timing sheet, and no race start, it runs the lock above and reports each stint's residual
  only if it locks. [`studio/dev/clubspeed.py`](../studio/dev/clubspeed.py) turns the circuit's saved
  heat pages into that sheet, and never reads a driver's name off them. Row C is re-measured by the
  `footage.accuracy_mk` check on every `pixi run test-footage`, which fails if any figure above
  moves. The ground truth is never committed — the method is public; the transponder CSV and the
  timing sheets (other drivers' lap times) stay out of the repo. Recording B's footage is intact
  but kept off the development machine since September 2026; recording A's no longer exists (a tool
  overwrote it). Rows A and B are historical measurements, reported as they were recorded in
  [`studio/docs/`](../studio/docs/gps-accuracy-research.md) at the time.
- **Still to come: Sandown Park**, the other circuit the working footage comes from, run clockwise.
  It is timed on Club Speed too, but its heat pages sit behind a sign-in, so its three recordings
  have no row until the owner fetches their sheets.

## Three findings that show where the limit actually is

The interesting part of this work is not the headline number — it's that we went looking for ways to
push it lower and found, with out-of-sample evidence, that there is nothing left to win on timing.
Three results explain why. All three were measured on the two D24 recordings, A and B.

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
**GPS true-clock** and has **validated that timing against a real transponder and a circuit's own
official timing**, out-of-sample, on footage of the kind you already own. That is the defensible
differentiator: transponder-grade lap
timing, from a GoPro you already have, for free — with the receipts to prove it.
