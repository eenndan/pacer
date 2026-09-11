# G-meter validation: camera→kart frame transform & ACCL-vs-GPS cross-check

The friction-circle g-meter overlay is driven by the GoPro's **real accelerometer** (the GPMF
`ACCL` stream), transformed from the camera body frame into the kart's horizontal frame. This
note records how the transform was derived empirically and how well the ACCL-derived g matches
an independent GPS-derived g — the acid test that the transform is right (and an honest record
if it weren't).

Test recording: `GX010060.MP4` (+ chapters `GX020060`/`GX030060`), Daytona Milton Keynes,
kart-mounted GoPro (cockpit view — *not* a helmet cam; this matters, see below).

## Streams bound in the C++ core

`pacer/gps-source` now parses, alongside GPS5/GPS9, three IMU streams (all carried on the
**media clock**, the same basis as GPS payload spans / the video; chapter offsets applied by
the `SequentialGPSSource` chain):

| stream | rate | content | datatype |
|--------|------|---------|----------|
| `ACCL` | 200 Hz | 3-axis accelerometer, m/s² (native element order Z,X,Y on this camera) | `IMUSample` (t,x,y,z) |
| `GRAV` | 60 Hz | gravity **unit** vector (native order X,Z,Y — see §Axis conventions) | `IMUSample` |
| `CORI` | 60 Hz | camera-orientation quaternion (w,x,y,z) | `QuatSample` (t,w,x,y,z) |

Read-back on the real file: ACCL 346,728 samples @ 200.5 Hz (|a|≈13 m/s² driving), GRAV
103,680 @ 59.9 Hz (|g|=1.000 exactly), CORI 103,680 @ 59.9 Hz.

## Resolving the frame conventions (empirical)

The three streams use *different* element conventions; these were pinned against the data:

1. **GRAV → ACCL axis permutation.** At rest, ACCL ≈ 9.81·ĝ. Brute-forcing permutation+sign to
   match `accl/9.81` to GRAV gives `PERM=(1,0,2)`, all positive, residual 0.012 — i.e.
   `ACCL[i]` aligns with `GRAV[PERM[i]]`. So GRAV (permuted) is the gravity vector in the ACCL
   frame, and **linear accel = ACCL − 9.81·ĝ** removes the static 1 g. (With ACCL's elements in
   camera Z,X,Y, that makes GRAV's own order camera **X,Z,Y**. This doc said X,Y,Z for eight
   releases; so did the C++ header, and so did two of the three synthetic test fixtures, which
   consequently ran with gravity essentially *un-removed* — 88° of frame error — and only their
   shape assertions survived it. Fixed and now pinned by
   `test_gmeter.test_the_legacy_fixtures_are_themselves_axis_consistent`.)

2. **CORI is world→camera (use the conjugate).** Rotating gravity into the world frame with the
   *conjugate* CORI quaternion makes world-gravity **constant** (std ≈0.035 over the session) —
   confirming camera→world. The naïve (non-conjugated) rotation gave std ≈0.7 (wrong). CORI's
   axis convention matches GRAV's, so ACCL-frame vectors are permuted by `PERM` before rotating.

3. **CORI yaw is NOT GPS north, RESETS each chapter, and DRIFTS within one.** Camera heading does
   not track GPS heading (the optical-axis-vs-velocity offset has ~uniform multi-radian spread),
   and chapter 1 begins at the identity quaternion — CORI is referenced to *each chapter's own*
   capture start. So the yaw+handedness between CORI's world plane and GPS ENU is fit **per
   chapter** by Procrustes (SVD) against the GPS-derived horizontal accel. A single global fit
   across chapters fails (see below).

   That yaw is **not constant within a chapter**. CORI comes from integrating the gyro with no
   magnetometer, so its world reference creeps: measured **+0.08 °/s** on a D24 recording and
   **+0.15 °/s** on a Sandown one — 130–200° end to end over a ~27 min chapter. One fit per chapter
   is therefore only correct where the drift crosses its mean (the middle laps); towards either end
   the g vector comes out rotated by the accumulated error, which scales the lateral g by
   cos(error) and eventually inverts it. Per lap, the observed lateral gain tracked cos(residual
   yaw) on **every** lap of both recordings — a pure rotation error, not noise:

   | recording | lap 1 | middle laps | last lap |
   |---|---|---|---|
   | Sandown GX010065 (before) | r −0.73, gain −0.33 | r +0.97, gain 1.03 | r +0.04, gain 0.01 |
   | Sandown GX010065 (after)  | r +0.97, gain 1.06 | r +0.97, gain 1.06 | r +0.98, gain 1.03 |
   | D24 GX020060 (before) | r +0.60, gain 0.28 | r +0.96, gain 1.04 | r +0.85, gain 0.57 |
   | D24 GX020060 (after)  | r +0.96, gain 1.05 | r +0.96, gain 1.04 | r +0.96, gain 1.04 |

   So the yaw is fitted in **overlapping 90 s windows** and interpolated per sample (linearly
   extrapolated past the first/last window centre, since the drift is a ramp). A window is used
   only if it holds enough moving samples, real cornering, and an acceleration-direction spread
   that actually determines a rotation — near-collinear vectors (one long corner) fit noise and
   would corrupt an alignment that was already right.

The pipeline: `ACCL − gravity → rotate by conj(CORI) → project onto the horizontal plane
(⊥ constant world-gravity) → align (per chapter) to ENU → de-drift the yaw across the chapter →
split per-sample into forward (along GPS velocity) and lateral (perpendicular)`.
`long_g = a_fwd/9.81`, `lat_g = a_left/9.81`.

## Cross-check results (ACCL-derived vs GPS-derived g)

GPS-derived: `long = (d|v|/dt)/9.81`, `lat = (|v|·yaw_rate)/9.81` (= v²·curvature/9.81), on the
session's median-filtered, smoothed trajectory; glitch spikes clipped.

| recording | moving samples | **lateral r** | longitudinal r | lateral RMS (ACCL vs GPS) | long RMS (ACCL vs GPS) |
|-----------|---------------:|--------------:|---------------:|---------------------------|------------------------|
| single chapter (GX010060) | 15,180 | **+0.89** | +0.36 | 0.69 vs 0.73 g | 0.46 vs 0.26 g |
| full 3-chapter (per-chapter align) | 42,582 | **+0.90** | +0.38 | 0.68 vs 0.72 g | 0.47 vs 0.25 g |
| full 3-chapter (single global align) | 42,582 | +0.10 (FAIL) | +0.02 | — | — |

**Sign agreement** on events: lateral **96.5 %** (corners > 0.3 g), longitudinal 75.1 % (> 0.15 g).

### Verdict — AGREE (kart-mounted, usable)

- **Lateral g is strongly recovered** (r ≈ 0.89–0.90, near-identical RMS to GPS, 96.5 % sign
  agreement). Lateral dominates karting and is the cleanest channel — this is the headline.
- **Longitudinal g** matches in magnitude and is correct in sign most of the time, but the
  per-sample correlation is weaker (≈0.37). That's expected, not a bug: forward g is small, and
  the GPS reference (`d|v|/dt` from 10 Hz speed) is itself noisy. ACCL's longitudinal RMS is a
  bit *higher* than GPS's because the 200 Hz accelerometer resolves brake/throttle transients the
  10 Hz GPS derivative smears out.
- The camera is **kart-mounted** (verified in the rendered frames — cockpit/steering-wheel view).
  This is why ACCL is usable. A **helmet cam** would be head-motion-dominated and would fail this
  test; the loader's trust heuristic (`lat_corr ≥ 0.4`) would then auto-fall-back to the
  GPS-derived g (`source="gps"`) rather than ship a garbage meter — see the next section.

## The axis convention is a fitted constant, and measurement says it must stay one

`GRAV_PERM` was fitted on a HERO13. GoPro physically moved the IMU between models, so the raw
element order really does differ by camera — and the container says so, in GPMF's `ORIN` (the raw
element orientation, lowercase = negated) and `ORIO` (the orientation the camera intends a
consumer to present). That looks like an obvious defect: *read the field instead of assuming.*
It was measured, and the obvious fix is wrong.

**What the fields actually contain** (`RawGPSSource::ReadImuOrientation`, pinned by
`tests/test_imu_orientation.py`; ACCL and GYRO are identical on every camera, and no other stream
carries either field on any camera):

| clip / recording | `DVNM` | ACCL `ORIN` | ACCL `ORIO` | has GRAV/CORI |
|---|---|---|---|---|
| `hero5.mp4` | Camera | *(absent)* | *(absent)* | no |
| `karma.mp4` | Camera | *(absent)* | *(absent)* | no |
| `Fusion.mp4` | Fusion | *(absent)* | *(absent)* | no |
| `hero6.mp4` | Hero6 Black | *(absent)* | *(absent)* | no |
| `hero6a.mp4`, `hero6+ble.mp4` | Hero6 Black | `YxZ` | `ZXY` | no |
| `hero7.mp4` | Hero7 Black | `YxZ` | `ZXY` | no |
| `hero8.mp4` | HERO8 Black | `zxY` | `ZXY` | yes (GRAV is **all zero**) |
| `max-heromode.mp4`, `max-360mode.mp4` | GoPro Max | `XzY` | `ZXY` | yes |
| `GX0*0060.MP4`, `GX0*0062.MP4` | HERO13 Black | `ZXY` | ***(absent)*** | yes |

Two things fall out of that table before any analysis. `ORIN` where present agrees exactly with
the per-model "Data order" rows in the vendored `3rdparty/gpmf-parser/README.md` (HERO6 `YxZ` =
"Y,-X,Z"), so it is trustworthy. And **the same camera model can differ by firmware** —
`hero6.mp4` carries no declaration where `hero6a.mp4` does — while the *newest* camera here
carries `ORIN` with **no `ORIO` at all**. Any reader of these fields must survive both.

**Would honouring them change anything?** On the HERO13 — the only camera with full-length,
GPS-validated recordings here — `ORIN` is `ZXY`, which is precisely what every camera that names a
presented orientation names as its `ORIO`.
The conversion is the **identity**. So on every recording this app has ever been measured on,
honouring the field changes *nothing*: not a g value, not a golden leaf.

**And on the other cameras it would make things worse.** `GRAV`, `CORI` and `IORI` carry no
orientation field on any camera that has them, so half of `ACCL − 9.81·ĝ`, rotated by `CORI`, has
nothing to canonicalise by. Which frame are they in? Differentiate `CORI` into a body angular rate
and ask which of the 48 signed element permutations maps raw `GYRO` onto it:

| recording | ACCL/GYRO `ORIN` | best map | 2nd | the ORIN-honouring map |
|---|---|---|---|---|
| `hero8.mp4` | `zxY` | **(+g1,+g0,+g2)** 21.2° | 53.3° | rank **36**/48, 105.0° |
| `max-heromode.mp4` | `XzY` | **(+g1,+g0,+g2)** 12.1° | 43.4° | rank **17**/48, 84.0° |
| `max-360mode.mp4` | `XzY` | **(+g1,+g0,+g2)** 15.2° | 50.8° | rank **15**/48, 80.8° |
| `GX020060.MP4` | `ZXY` | **(+g1,+g0,+g2)** 10.0° | 39.8° | *(identity — same map)* |
| `GX010062.MP4` | `ZXY` | **(+g1,+g0,+g2)** 9.5° | 38.4° | *(identity — same map)* |

`(+g1,+g0,+g2)` **is** `GRAV_PERM`. One constant, rank 1 of 48 on three camera models declaring
three *different* raw orders. So `GRAV`/`CORI` ride the **raw** element frame, not the presented
one, and canonicalising `ACCL`/`GYRO` by `ORIN` while they stay raw would break an alignment that
currently holds on every camera anyone can test. **The fitted constant is not a stand-in for the
field; it is the correct answer, and the field is a different question.**

So: the field is **read and reported** (`read_imu_orientation`, printed by `studio.dev.diagnose`)
and never applied, and the assumption it was standing in for is now **measured per recording**.

### The guard: `gmeter.axis_check`

A wrong element frame is silent — `ACCL − 9.81·ĝ` with a mis-framed `ĝ` *adds* gravity instead of
removing it, and Pearson r is blind to the constant it leaves behind. So the invariant is measured
directly and needs no camera model: while the kart is unloaded (|low-passed ACCL| within 2 % of g)
the accelerometer **is** gravity, so `GRAV_PERM(GRAV)` must point where the ACCL points. Over the
threshold, `compute` refuses the IMU path and falls back to GPS-derived g, exactly as a failed
cross-check does.

| recording | measured tilt | nearest wrong element order (any) | nearest that moves the dominant gravity element |
|---|---|---|---|
| `max-heromode.mp4` | **1.46°** | 9.71° | 75.58° |
| `max-360mode.mp4` | **1.90°** | 15.06° | 81.81° |
| `GX030060.MP4` | **4.30°** | 31.93° | 31.93° |
| `GX010062.MP4` | **5.06°** | 31.01° | 36.85° |
| `GX020062.MP4` | **9.33°** | 26.51° | 45.50° |
| `GX020060.MP4` | **9.38°** | 29.36° | 41.04° |

`AXIS_MAX_TILT_DEG = 20` sits 2.1× above the worst true value and 1.6× below the nearest *damaging*
one. The middle column is why the guard is deliberately not tighter: on a mount whose gravity has a
near-zero element (the Max clips) some wrong relabellings sit under 20° — and those are exactly the
ones that leave ~0.17 g of residual instead of 1–2 g. Gating on them would be gating on noise.

It also catches something that was already live: `hero8.mp4`'s `GRAV` stream is **all zeros**,
which today reaches the transform as a zero gravity direction rather than as an error.

Neither D24 recording moved: lateral r **+0.9564 → +0.9564**, gain **1.0916 → 1.0916** (0060) and
r **+0.9587 → +0.9587**, gain **1.1079 → 1.1079** (0062), with `source="accl"` on both.

## Honesty / fallback

The g source is structured to switch easily. `studio/gmeter.compute` always computes the GPS
cross-check; if the (per-chapter-weighted) lateral correlation is below 0.4 it **keeps the
user-chosen ACCL OFF and uses GPS-derived g for the live meter**, logging the reason. With no
IMU at all (older GoPro lacking GRAV/CORI, e.g. the bundled hero6 sample) it likewise falls back
to GPS. The loader prints the cross-check summary at startup so the verdict is always visible.

For this recording the recommendation is **ACCL** — it agrees with GPS and gives a higher-fidelity
(200 Hz) signal than the GPS derivative. If a future recording is a helmet cam, prefer the GPS
fallback.

## Where the provenance is STATED (and where it is not)

The dial mixes sensors — that is the direct consequence of the table above. Its **lateral** axis is
the IMU (r ≈ +0.89 against GPS, near-identical RMS, 96.5 % sign agreement); its **braking/accel**
axis is the **GPS speed-derivative**, because the IMU forward axis is vibration-inflated
(r ≈ +0.36). A bare source name would therefore misattribute the braking axis, which is why
`gmeter_overlay.source_label` composes the mixed string `"IMU lat · GPS long"` rather than printing
one sensor's name.

That string is stated on the **g-meter toggle's tooltip** — the control that turns the dial on —
as one clause of `gmeter_overlay.source_sentence`, alongside the felt-force convention. It is not
on the dial's face and not burned into the exported video.

**Why it moved off the face.** It used to be printed at 6.5 px in the live dial's bottom-right
corner, one of eleven text items in a 120×140 px card (critical review §6.5: "reads as a debug
widget"). The export already declined to burn it — a line of sensor plumbing under a dial in a clip
someone watches is not where a viewer reads provenance — so the fact was already only half-stated,
and the half that survived was the one nobody could read. A tooltip states it in a sentence
(*"Cornering g comes from the IMU; braking and acceleration from the GPS speed derivative, which
the vibration-inflated IMU forward axis is not trustworthy enough to carry."*) on the surface a
driver hovers when they want to know what the dial is. `source_label` keeps its exact string;
`DialFilter` no longer carries it at all, because a field the painter never reads is a field two
threads pass around for nothing.

**What still guards it.** `tests/test_gmeter_overlay.py` pins both halves of the move:
`test_the_dial_does_not_carry_its_provenance_any_more` (the dataclass really shed it — as against
the four cardinal peaks, which are ALSO unpainted now and ARE still carried, because they clamp the
hull and the exporter freezes them) and `test_the_provenance_moved_to_the_toggles_tooltip`, which
drives the real `VideoView` and requires the convention *and* both axis sources in the tooltip
text. And because there is no longer any string one mode paints and the other does not,
`test_the_burn_and_the_screen_say_exactly_the_same_thing` asserts set EQUALITY between the live and
exported faces — a stronger contract than the old "exactly two may differ".

## The friction circle's two axes are not on one window (measured; kept, and stated)

`LAT_SMOOTH_S` = 0.15 s sets the lateral bandwidth; `LONG_SMOOTH_S` = 0.35 s boxcars the
GPS-derived longitudinal. So every quantity built on `hypot(lat, long)` — the g-g cloud, its p98
grip envelope, the dial's `|g|` readout, every grip-utilization number — combines two axes whose
smoothing spans differ by **2.3x**. Measured on both D24 pairs (38 and 65 valid laps) before being
kept:

| shipped surface | shipped (0.15 / 0.35) | both 0.15 s | both 0.35 s |
|---|---:|---:|---:|
| p98 grip envelope — the dashed ring + "grip ceiling" tile (0060) | 1.425 g | 1.447 (+1.5 %) | 1.350 (−5.2 %) |
| …(0062) | 1.368 g | 1.378 (+0.7 %) | 1.309 (−4.4 %) |
| `driving.grip_envelope`, the divisor under every grip number (0060 / 0062) | 1.424 / 1.370 g | +1.7 % / +1.1 % | −4.9 % / −4.7 % |
| peak lateral g tile (0060 / 0062) | 1.717 / 1.858 g | unchanged | 1.503 / 1.495 (−12 % / −20 %) |
| CORNERS "Grip %" column | 54–78 % | ≤1 point; 0 and 1 of 66 corner pairs reorder | +4 to +6 points; 4 of 66 reorder |
| per-lap envelope utilization, rank vs shipped | — | ρ +0.970 / +0.974 | ρ +0.965 / +0.925 |
| cross-check lateral **gain** | 1.092 / 1.108 | unchanged | 1.075 / 1.091 |
| cross-check lateral r | 0.956 / 0.959 | unchanged | 0.967 / 0.969 |

**The numbers barely move, because p98 |a| is very nearly a lateral statistic**: 1.375 of the
1.425 g (and 1.334 of the 1.368) is the lateral axis on its own. Matching at 0.15 s shifts the
headline envelope by one last digit of a tile that prints two.

**Matching at 0.35 s is not free**, and the way it fails is the familiar one: the lateral
correlation *improves* (0.956 → 0.967) while the **gain drifts** (1.092 → 1.075) — a channel
tracking better and reading smaller, which is exactly the shape of the CORI yaw-drift defect the
gain check exists to catch.

**Matching the magnitude alone breaks the picture it would fix.** Give `|a|` one bandwidth and
leave each display channel as it is, and the dashed ring is computed off a series the cloud beneath
it is not drawn from: at 0.35 s it sits at 1.344 / 1.297 g with **4.85 % and 4.58 %** of that
cloud's own points outside a ring the key line calls "p98 of combined g" (shipped: 2.00 % and
2.01 %, exactly the p98 it claims). A ring and the cloud it bounds have to come off one series.

**And the wider longitudinal window is a measurement, not a preference.** Its input is a 10 Hz GPS
speed differentiated. Welch PSDs on the g-meter's own 50 Hz grid, over the valid laps:

| series | f50 | f90 | f95 | f99 | RMS |
|---|---:|---:|---:|---:|---:|
| `lat_g` (0060 / 0062) | 0.08 / 0.08 Hz | 0.29 / 0.29 | 0.55 / 0.53 | 2.89 / 2.58 | 0.78 / 0.78 g |
| `long_g_gps` shipped | 0.16 / 0.11 Hz | 0.84 / 0.59 | 1.20 / 1.11 | 2.06 / 3.61 | 0.28 / 0.24 g |
| GPS longitudinal **before** the boxcar | 0.48 / 0.46 Hz | **4.45 / 4.97** | 5.75 / 5.85 | **12.6 / 8.07** | 0.35 / 0.32 g |
| IMU forward axis (`long_g`) | 0.13 / 0.15 Hz | 3.46 / 3.94 | 4.27 / 4.49 | 5.63 / 5.64 | 0.26 / 0.26 g |

Ten percent of the pre-boxcar longitudinal's power sits above 4.45 Hz and one percent above
12.6 Hz — **above the 5 Hz Nyquist of the fixes it is made of**, so it cannot be driving.
Unsmoothed, the per-lap peak deceleration runs a median **1.58 / 1.17 g** on the 50 Hz g grid with
the 2.0 g `MAX_LONG_G` clip firing on both recordings. (On the native 10 Hz grid the same
unsmoothed peak is 1.081 g with a max of 1.94 g — the figures the peak-braking tile's own evidence
quotes; they differ only in which grid the maximum is taken on.)

**What the asymmetry does shape is the cloud's HEIGHT.** Putting both axes on the lateral's window
leaves the p98 width untouched (1.375 / 1.334 g) and grows the p98 braking extent 9–13 %
(0.625 → 0.681, 0.524 → 0.593 g) and the p98 acceleration extent 22–26 % (0.436 → 0.530,
0.315 → 0.398 g). Per 30° sector the envelope moves by up to 0.280 g (0060) and 0.310 g (0062).

So the windows stay as they are and the asymmetry is **stated where it is read**: `stats_panel`'s
`GG_TOOLTIP` composes BOTH constants and says the cloud is smoothed more in height than in width.
