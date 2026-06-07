# GPS positional / lap-timing accuracy — research & empirical evaluation

**Branch:** `gps-research` (off `lap-time-accuracy`). **Date:** 2026-06.
**Goal:** find techniques to push GPS positional / lap-timing accuracy *higher* for our data
(consumer ~10 Hz GoPro GPS9 on a kart; lap timing + track map), evaluate each critically against
our **real, already-validated** findings, and validate the reasonable ones **empirically, out of
sample, on BOTH recordings** against the transponder ground truth.

**Bottom line up front:** Our lap timing is already at the practical limit for this data.
None of the researched smoothing/fusion techniques improved the lap-time residual on **both**
recordings — the one that looked best on 0062 (Doppler-aided RTS) was the **worst** on 0060, i.e. a
textbook overfit, exactly the failure mode the team already caught once with the clock-rate factor.
**The single most important empirical finding is that the GPS-dropout laps — assumed to be the #1
fixable error — have their dropouts *mid-lap*, not at the start/finish crossing, so no gap-bridging
method (IMU dead-reckoning, Kalman, spline) can change their lap time at all.** The residual is set
by per-fix positional noise on the (clean, present) crossing samples, which is irreducible from the
GPMF streams we have. GPS+IMU fusion is real and the IMU data *is* present at 200 Hz, but its
leverage here is on the **map** (heading through a dropout), not on timing — and even there our
existing boxcar already beats the camera-orientation heading. Recommendation: **do not productionize
any of these for timing.** Optionally add a low-confidence **flag** for dropout laps, and (low
priority) an IMU-curved gap-fill for the map.

---

## 0. Baseline reproduced (the bar to beat)

`studio/_validate_wallclock.py`, default shipping pipeline (GPS9 true-clock, rate=1.0, boxcar w=13),
auto-locked to the transponder by duration-correlation:

| recording | CSV laps | corr | clean-lap residual (app−transponder) | racing std | dropout laps |
|-----------|----------|------|--------------------------------------|------------|--------------|
| **0060**  | 302–358  | 0.992 | mean +0.0030 s, **std 0.0871 s**     | 0.0984 s   | 7 (worse GPS, 4.4% gated) |
| **0062**  | 856–920  | 0.997 | mean +0.0015 s, **std 0.0527 s**     | 0.0530 s   | 2 (better GPS, 1.0% gated) |

These match the brief's baseline (0.098 / 0.053). 0062 has roughly half the residual of 0060 because
its GPS was simply better (median DOP 1.37 vs 2.40, 1.0% vs 4.4% fixes gated) — **recording-level GPS
quality sets the floor**, a fact that recurs throughout.

---

## 1. What actually limits us (measured, not assumed)

Before evaluating techniques we instrumented the *real* error structure (scripts in
`studio/docs/gps_research_scripts/`). This reshaped the whole problem:

**1a. The crossing interpolation is NOT the bottleneck.** Lap time = (finish crossing instant) −
(start crossing instant); each instant is interpolated along the chord between the two real GPS
samples straddling the S/F line (`pacer::Split`, `t = t0 + f·(t1−t0)`). We measured the time-spacing
of those straddling samples ("S/F chord dt") per lap and correlated it with |residual|:

- corr(|residual|, start+finish chord dt) = **−0.10 (0060), −0.04 (0062)** — *no* relationship.
- |residual| for laps with above-median vs below-median chord dt: **0.080 vs 0.079 s (0060)** — identical.

So the constant-velocity crossing interpolation is already sub-sample accurate (the chord is ~0.05 s
≈ 0.5 m at racing speed). **Sub-tick crossing refinement using IMU acceleration "to interpolate the
exact millisecond at the line" — the headline trick of phone-GPS lap timers like *telemetra* — buys
us nothing, because that trick only matters at 1 Hz GPS where the chord is a full second.** At 10 Hz
we are already past it.

**1b. The GPS-dropout laps have their gaps MID-LAP, not at S/F.** This is the decisive finding. For
**every** dropout lap on **both** recordings, the largest interior gap sits deep inside the lap and
both S/F crossings sit on clean ~0.1 s chords (`diag_gap_irrelevant.py`):

| rec | lap | residual | gap @ t-into-lap | S/F crossing chord-dt sum | gap near S/F? |
|-----|-----|----------|------------------|---------------------------|---------------|
| 0060| 7   | **+0.290** | 49.3 s (of 70.1) | 0.043 s (pristine)        | **No** |
| 0060| 13  | −0.160   | 7.0 s            | 0.152 s                   | No |
| 0060| 3   | −0.136   | 27.9 s           | 0.146 s                   | No |
| 0062| 21  | +0.088   | 23.9 s           | 0.014 s                   | No |
| 0062| 46  | +0.016   | 16.6 s           | 0.089 s                   | No |

Because lap time depends only on the two crossing instants, and those sit on real samples far from
the hole, **no reconstruction of the hole — IMU dead-reckoning, Kalman bridge, spline, cross-lap
borrow — can change these laps' times.** The brief's central hypothesis ("a lap whose S/F crossing
sits inside a ~2 s GPS hole is inherently ±0.85 s off") **does not occur in either recording.** The
gap-aware speed integral already fixes the only thing a mid-lap hole corrupts: the lap *distance*
(map side).

**1c. So why are 0060's dropout laps still noisier (|resid| 0.138 vs 0.071)?** The dropout is a
*symptom of a bad-GPS period* (multipath / poor satellite geometry), during which even the present
crossing fixes are positionally noisier, jittering where the chord geometrically intersects the S/F
line. Evidence: DOP-near-S/F vs |residual| correlates **+0.20 on 0060 but −0.27 on 0062** — weak and
sign-flipped across recordings, i.e. there is no clean, generalizable predictor. It is essentially
**irreducible 10 Hz positional noise**, worse in the recording with worse GPS.

**Conclusion of §1:** the remaining lap-time error is dominated by per-fix positional noise on the
crossing samples. It is not a time-axis problem, not a crossing-interpolation problem, and not a
gap-bridging problem. This is the lens for evaluating every technique below.

---

## 2. Is the IMU even there? (GPMF stream inventory)

Yes — confirmed by parsing the recordings with GoPro's own `gpmf-parser`
(`gps_research_scripts/inventory.c`). Per chapter of 0062 (1730 s):

| stream | rate | content |
|--------|------|---------|
| **GPS9** | 10.00 Hz | lat/lon/alt, 2D & 3D Doppler speed, DOP, fix — the timing source |
| **ACCL** | 200 Hz | 3-axis accelerometer (m/s²) — real kart data, ~8 m/s² magnitudes |
| **GYRO** | 200 Hz | 3-axis gyroscope (rad/s) |
| **CORI** | 59.94 Hz | camera-orientation quaternion (on-camera fused, for stabilization) |
| **GRAV** | 59.94 Hz | gravity vector |
| IORI | 59.94 Hz | image orientation quaternion |

So GPS+IMU fusion is **feasible** — 200 Hz IMU, 20× the GPS rate. The pacer C++ core currently parses
only GPS5/GPS9 (`pacer/gps-source/gps-source.cpp`); ACCL/GYRO/CORI are **not** bound. Binding them is
a real cost (C++ parse + datatypes + nanobind + a fusion filter), justified only if it pays off — so
we tested whether it *would* before proposing it.

---

## 3. Techniques researched & critically evaluated

Each is judged against §1 (the residual is crossing-fix positional noise, dropouts are mid-lap) and
our "already done / already unbiased" status.

### T1. GPS + IMU sensor fusion (EKF/UKF), incl. dead-reckoning across dropouts — *the headline lead*
- **Sources:** [arxiv 2405.08119 GPS-IMU EKF](https://arxiv.org/html/2405.08119v1);
  [RaceCapture-Pro EKF issue #545](https://github.com/autosportlabs/RaceCapture-Pro_firmware/issues/545);
  [MATLAB IMU+GPS fusion](https://www.mathworks.com/help/nav/ug/imu-and-gps-fusion-for-inertial-navigation.html);
  [telemetra karting telemetry](https://github.com/nicolacanzonieri/telemetra).
- **Targets:** dropout-lap timing (limitation #1) + map jitter (#3).
- **Critical verdict — does NOT help our timing.** Two independent reasons, both empirical:
  1. Dropouts are mid-lap (§1b) → fusion cannot change those laps' times.
  2. The "interpolate the exact ms at the line with acceleration" trick (telemetra's selling point)
     is a **1 Hz** fix; at 10 Hz our crossing chord is already negligible (§1a).
  The motorsport community uses EKF mainly to *de-noise position and raise the output rate* for
  display (RaceCapture issue is literally "GPS comes unfiltered with errors and dropouts"), not
  because 10 Hz lap timing is inadequate. Consumer-MEMS dead-reckoning literature is blunt: a typical
  build **disables DR after ~7 s of outage** because position drift (accel-bias → 2nd-order,
  gyro-bias → 3rd-order) runs to meters; for our 1–2 s holes the GPS **Doppler speed integral we
  already use** is a *better* gap estimator than double-integrating a $20 MEMS accel.
- **Where it *could* legitimately help:** map heading through a dropout (see T5) — minor, visual.
- **Cost:** high (C++ stream binding + a real fusion filter + axis/mount calibration).

### T2. Kalman filter + RTS smoother on position (constant-velocity / constant-acceleration)
- **Sources:** [EKF+RTS in a GPS receiver (IEEE)](https://ieeexplore.ieee.org/document/6469979/);
  [Kalman position smoothing (Medium)](https://medium.com/@omer.chandna_9250/smoothing-noisy-position-data-with-a-kalman-filter-a045a7c0e3fb).
- **Targets:** map jitter (#3), and *hoped* lap-timing std (#2).
- **Critical verdict — principled, but no timing gain (validated, §4).** An RTS smoother is the
  "right" optimal smoother vs our heuristic boxcar, but the timing limit is noise on the crossing
  fixes; a smoother that respects the data can't invent information that isn't there. Validated: it
  lands within noise of boxcar/raw on both recordings.
- **Cost:** low (pure numpy). Could *replace* the boxcar for the map as a cleaner denoiser, but that's
  a lateral move, not an accuracy win.

### T3. Doppler-velocity-aided positioning
- **Sources:** [Doppler-aided positioning, GPS World](https://www.gpsworld.com/gnss-systemalgorithms-methodsinnovation-doppler-aided-positioning-11601/).
- **Targets:** position/velocity noise.
- **Critical verdict — already exploited, and aiding the *smoother* with it OVERFITS.** GPS9's speed
  is Doppler-derived and we already use it for gap-aware distance. Feeding it as a velocity
  pseudo-measurement into the RTS smoother (`rts_dop`) gave the **best** 0062 number and the **worst**
  0060 number (§4) — i.e. it overfits to a recording's particular noise correlation. Rejected on the
  same out-of-sample principle that killed the clock-rate factor.

### T4. Map-matching / track-model constraint (snap to a known centerline)
- **Sources:** [TUMFTM racetrack-database](https://github.com/TUMFTM/racetrack-database);
  [multi-track map matching (arxiv 1209.2759)](https://arxiv.org/pdf/1209.2759).
- **Targets:** cross-track error (#3); *not* timing.
- **Critical verdict — wrong tool for timing, risky for the racing line.** Snapping to a single
  centerline would *erase the lap-to-lap racing-line differences* the app exists to visualize, and a
  centerline has no per-lap timing information. For along-track *timing* it does nothing. We already
  have a georeferenced MK centerline (`reference.py`) used only as a last-resort gap-fill donor.
- **Cost:** medium; **negative value** for the product (destroys signal).

### T5. IMU/CORI heading for the map (cross-track + curved dropout bridges)
- **Targets:** map jitter (#3) and the *shape* of a dropout bridge.
- **Critical verdict — real but marginal, and our boxcar already wins on clean track.** Measured
  (`analyze_imu.py`) heading-source HF noise on a moving 0062 window: raw GPS-differenced heading
  6.59 deg/sample; CORI camera-yaw resampled to 10 Hz 3.94 deg/sample (1.7× cleaner than *raw* GPS).
  But our boxcar w=13 already cuts heading jitter ~91% (PLAN), i.e. to ~0.6 deg/sample — **better than
  CORI.** So IMU heading is *not* cleaner than what we already plot on normal track. Its only edge is
  drawing a *curved* path through a dropout instead of the borrowed/straight fill — a minor visual
  nicety the cross-lap borrow already mostly covers.
- **Cost:** high (bind CORI/GYRO) for a small, map-only, visual gain.

### T6. Higher-order spline interpolation of the crossing
- **Critical verdict — pointless given §1a.** The crossing chord is ~0.05 s; linear is already exact
  to well under the noise floor. A spline changes the crossing instant by <1 ms.

### T7. Multi-lap geometry fusion / cross-lap borrow
- **Critical verdict — already done** for the map gap-fill (`gapfill.py`), and (per §1b) it cannot
  help timing because the borrowed geometry is mid-lap.

### Not applicable (honest):
- **RTK / PPK / carrier-phase** — needs raw pseudorange/carrier observables and a base station;
  GoPro exposes only the computed GPS9 fix. Impossible here.
- **Generic position smoothing** — already done (boxcar w=13), and §4 shows more of it doesn't help.
- **Clock-rate calibration** — already tried and removed as an overfit (validated).

---

## 4. Empirical validation — the prototypes, out of sample, on BOTH recordings

`gps_research_scripts/proto_smooth.py` reuses the exact studio load + the exact validator alignment,
swapping **only** the position-smoothing step, then re-times every lap and reports the residual vs the
transponder. Methods: `boxcar` (shipping w=13), `raw` (no smoothing), `rts` (CV Kalman+RTS on
position), `rts_dop` (RTS + Doppler-speed velocity pseudo-measurement, the T3 idea).

**Clean-lap residual std (the headline metric), the bar = boxcar:**

| method   | 0060 clean std | 0062 clean std | verdict |
|----------|----------------|----------------|---------|
| boxcar (baseline) | **0.0871** | **0.0527** | — |
| raw      | 0.0833 | 0.0521 | ~tie (marginally best, within noise) |
| rts      | 0.0855 | 0.0516 | ~tie |
| **rts_dop** | **0.0949 (WORSE)** | **0.0494 (best)** | **OVERFIT — fails to generalize** |

- **`rts_dop` is the smoking gun:** best on 0062 (0.0494), worst on 0060 (0.0949). Had we validated on
  0062 alone we'd have "found a 6% win" and shipped an overfit — the same trap as the removed
  clock-rate factor. Cross-checking on 0060 kills it.
- **All methods converge near `raw`** (0.083 / 0.052). A parameter sweep of the RTS (`meas_std` ∈
  {1,2,3} m, `accel_psd` ∈ {10,40,100}) on 0060 stays in **0.084–0.088** — the negative result is not
  a tuning artifact.
- **Dropout laps don't improve under any method** (0060 dropout std stays ~0.15) — consistent with
  §1b: nothing operating on positions can move a lap time whose crossings are clean.

**Interpretation:** the best achievable here is to *not add noise* — `raw` ≈ `rts` ≈ `boxcar` for
timing. We keep the boxcar because it's tuned for the **map** (corner apexes preserved, 39%/91%
jitter cut) and is timing-neutral. We are at the **10 Hz positional-noise floor**; the recording's
intrinsic GPS quality (DOP) sets it, not our algorithm.

---

## 5. Ranked recommendation

1. **Do nothing to the timing pipeline.** It is unbiased and at the practical floor on both
   recordings. No researched technique beats it out-of-sample; the best (Doppler-RTS) is an overfit.
   This is the most valuable result: it closes the "are we leaving accuracy on the table?" question
   with evidence.
2. **(Optional, cheap, honest) Flag dropout laps as low-confidence** in the UI/lap table — a lap with
   an interior gap > 0.35 s. They're not *fixable* (§1b) but the user should know their distance/map
   is reconstructed and their time carries the bad-GPS-period noise. This matches the PLAN's existing
   "flag rather than absorb" note. Pure-Python, no core change.
3. **(Low priority, map-only) IMU-curved dropout bridge.** If/when the core binds CORI or GYRO, draw
   the dropout fill as a heading-integrated curve instead of borrow/straight. Visual nicety only; the
   cross-lap borrow already reconstructs the real corner shape. **Not worth binding the IMU for this
   alone.**
4. **Do NOT** pursue: GPS+IMU EKF for timing (no leverage, high cost), map-matching to a centerline
   (destroys the racing-line signal), Doppler-aided smoothing (overfits), spline crossing (<1 ms),
   RTK/PPK (no raw observables).

### On GPS+IMU fusion specifically (the headline question)
The IMU is present and rich (200 Hz ACCL/GYRO). But for **lap timing** it has **no leverage on this
data**: dropouts are mid-lap so DR can't change crossing instants, and the 10 Hz crossing chord is
already negligible so sub-tick acceleration interpolation is moot. For the **map** it's marginal and
our boxcar already beats camera-yaw heading on clean track. **Cost/benefit: clearly not worth
productionizing.** The honest summary is the brief's own hypothesis, inverted by the data: *we're at
the practical limit; IMU fusion is not even the lever here because the dropouts don't sit where the
hypothesis assumed.*

---

## 6. Reproduce

```bash
# baseline (per recording)
pixi run python -m studio._validate_wallclock -- /path/GX010060.MP4 "<transponder.csv>" \
    --race-start "2026-05-23 12:00:00Z" --dump /tmp/claude/baseline_0060.json

# smoother prototypes (boxcar/raw/rts/rts_dop), both recordings
PYTHONPATH=. python studio/docs/gps_research_scripts/proto_smooth.py /path/GX010060.MP4 "<csv>"
PYTHONPATH=. python studio/docs/gps_research_scripts/proto_smooth.py /path/GX010062.MP4 "<csv>"

# error-structure diagnostics (need a baseline dump for residuals)
PYTHONPATH=. python studio/docs/gps_research_scripts/diag_gap_irrelevant.py /path/GX010060.MP4 dump.json
PYTHONPATH=. python studio/docs/gps_research_scripts/diag_corr.py /path/GX010060.MP4 dump.json
PYTHONPATH=. python studio/docs/gps_research_scripts/diag_dop.py  /path/GX010060.MP4 dump.json

# GPMF stream inventory + IMU heading analysis (C tools build against the bundled gpmf-parser)
cc -O2 -I3rdparty/gpmf-parser -o /tmp/inventory studio/docs/gps_research_scripts/inventory.c \
   3rdparty/gpmf-parser/GPMF_parser.c 3rdparty/gpmf-parser/GPMF_utils.c \
   3rdparty/gpmf-parser/demo/GPMF_mp4reader.c
/tmp/inventory /path/GX010062.MP4
```

The transponder CSV and all `/tmp` dumps are **inputs only — never committed.**

## Sources
- [GPS-IMU EKF for vehicle position (arxiv 2405.08119)](https://arxiv.org/html/2405.08119v1)
- [RaceCapture-Pro firmware — EKF for GPS/IMU fusion (issue #545)](https://github.com/autosportlabs/RaceCapture-Pro_firmware/issues/545)
- [MATLAB — IMU and GPS fusion for inertial navigation](https://www.mathworks.com/help/nav/ug/imu-and-gps-fusion-for-inertial-navigation.html)
- [telemetra — karting telemetry with GPS+accel Kalman fusion](https://github.com/nicolacanzonieri/telemetra)
- [Position estimation using EKF + RTS-smoother in a GPS receiver (IEEE 6469979)](https://ieeexplore.ieee.org/document/6469979/)
- [Smoothing noisy position data with a Kalman filter (Medium)](https://medium.com/@omer.chandna_9250/smoothing-noisy-position-data-with-a-kalman-filter-a045a7c0e3fb)
- [Innovation: Doppler-Aided Positioning (GPS World)](https://www.gpsworld.com/gnss-systemalgorithms-methodsinnovation-doppler-aided-positioning-11601/)
- [TUMFTM racetrack-database (centerlines)](https://github.com/TUMFTM/racetrack-database)
- [Multi-track map matching (arxiv 1209.2759)](https://arxiv.org/pdf/1209.2759)
- [MEMS IMU dead-reckoning drift in GPS outages (PMC / ResearchGate tables)](https://pmc.ncbi.nlm.nih.gov/articles/PMC5982656/)
- [GoPro gpmf-parser (ACCL/GYRO/GPS9/CORI stream definitions)](https://github.com/gopro/gpmf-parser)
- [py-gpmf-parser (Python ACCL/GYRO/GPS extraction)](https://pypi.org/project/py-gpmf-parser/)
