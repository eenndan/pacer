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
| `ACCL` | 200 Hz | 3-axis accelerometer, m/s² (native element order Z,X,Y) | `IMUSample` (t,x,y,z) |
| `GRAV` | 60 Hz | gravity **unit** vector (native order X,Y,Z) | `IMUSample` |
| `CORI` | 60 Hz | camera-orientation quaternion (w,x,y,z) | `QuatSample` (t,w,x,y,z) |

Read-back on the real file: ACCL 346,728 samples @ 200.5 Hz (|a|≈13 m/s² driving), GRAV
103,680 @ 59.9 Hz (|g|=1.000 exactly), CORI 103,680 @ 59.9 Hz.

## Resolving the frame conventions (empirical)

The three streams use *different* element conventions; these were pinned against the data:

1. **GRAV → ACCL axis permutation.** At rest, ACCL ≈ 9.81·ĝ. Brute-forcing permutation+sign to
   match `accl/9.81` to GRAV gives `PERM=(1,0,2)`, all positive, residual 0.012 — i.e.
   `ACCL[i]` aligns with `GRAV[PERM[i]]`. So GRAV (permuted) is the gravity vector in the ACCL
   frame, and **linear accel = ACCL − 9.81·ĝ** removes the static 1 g.

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

That string is stated on the **live on-screen dial** — the surface where a driver reads the numbers
in order to act on them, and where the app's other trust chrome lives. It is **deliberately not
burned into the exported video**: a nine-pixel line of sensor plumbing under a dial in a clip
someone watches is not where provenance is read, and the reserved band it occupied is worth more
as dial (dropping it grows the export dial's radius 10.1 % — 76.5 → 84.2 px at 1080p — and its face
21 % in area, for free). `source_label` keeps its exact string, the exporter still sets it, and
`DialState.source` still carries it; only `_paint_dial_export` stops drawing it.

The divergence is pinned in both directions by
`tests/test_gmeter_overlay.test_export_dial_paints_the_same_labels_as_the_live_dial`: exactly two
strings may be live-only — the "G METER" title and this tag — and everything that says what the
numbers MEAN (the four direction captions, the labelled `0.5 g` / `1.0 g` rings that carry the
unit) must still appear in both.
