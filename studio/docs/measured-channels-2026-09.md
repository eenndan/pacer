# Measured-channel probes: sideslip rate, wheel hop, track bump map

**2026-09-16.** Three speculative channels (§6 of the market research) asked as falsifiable
questions against both D24 recordings. **Two are refused. One is real and is not a feature yet.**

This is a measurement, not a feature. No user-facing surface changed in the work that produced it,
and none of these probes is wired to anything. The probes live in `studio/dev/probes/` and every
number below is reproducible from them.

| channel | question | verdict |
|---|---|---|
| **P1 sideslip rate** (`beta_dot`) | does `omega_path − omega_gyro` measure the car? | **REFUSED** — below its own noise floor on 0062 (0.71x), 1.36x on 0060; no per-lap resolution |
| **P2 wheel hop / chatter** | is there a coherent 5-25 Hz oscillation? | **REFUSED** — no narrowband peak, and the peak frequency is indistinguishable from random |
| **P3 track bump map** | is vertical roughness a property of the track? | **REAL** — r=+0.952 across two different days against a 0.288 null, and it is not the speed profile. Feature decision deliberately NOT taken here |

## What the recordings are

| | 0060 | 0062 |
|---|---|---|
| chapters | `GX020060` + `GX030060` | `GX010062` + `GX020062` + `GX030062` |
| camera | HERO13 Black | HERO13 Black |
| clean laps (no GPS dropout) | 38 of 39 | 65 of 66 |
| detected corners | 12 | 12 |
| raw ACCL | 586,300 samples @ 200.45 Hz | 1,012,320 @ 200.48 Hz |

Loaded through the real `Session.load`. `studio.dev._jail` diverts every app-support seam first, so
the load cannot touch the owner's library, track DB or preferences — and because `track_db` is
diverted too, every run takes the unknown-track auto-fit start-line path instead of depending on
which tracks happen to be saved. `~/Desktop/D24` is read-only: the cache builder snapshots every
chapter's size and mtime before and after the load and refuses to write if any moved. Both
recordings passed on every run.

## The one fact that decides all three: the two channels are on different clocks

Every probe here compares a GPS-derived quantity against an inertial one, and **two** corrections
stack between them. Neither is optional; together they are worth about half a second.

1. **The axes are different.** `Session._lap_columns` times are the GPS9 **true-clock (telemetry)**
   axis `load._gps9_times` builds. `ACCL`/`GRAV`/`GYRO` times are the camera's **media** stamps.
   The map between them is `Session.media_clock.without_gps_lag()` — the rate fit alone — and it
   is not identity:

   | | rate | offset | ramp over the recording |
   |---|---|---|---|
   | 0060 | +26.73 ppm | +0.0256 s | 0.078 s over 49 min |
   | 0062 | +27.11 ppm | +0.0380 s | 0.137 s over 84 min |

   **Two in-repo docstrings used to say otherwise; both have since been corrected**:
   `pacer/laps/laps.hpp` ("times  media-clock seconds") and `Session._lap_columns`
   ("media-clock seconds"), along with the rest of that comment family. The code that
   gives them away is `Session._build_rotation`, which passes
   `to_media=self.media_clock.without_gps_lag().to_media` into `rotation.compute`
   precisely because the lap traces it hands over are *not* on the gyro's clock. Anything that
   believes those docstrings inherits the ramp as a fake drift — which is exactly why this repo
   used to read the offset below as "~0.35-0.40 s".

2. **A constant offset remains underneath.** PR #291: the GPS trace's timestamps land after the
   gyro's for the same event, constant, with no seam step larger than the per-lap spread, settled
   against the picture itself. `RotationCheck.gps_lag_s` — `rotation.measure_lag`'s whole-recording
   figure, the one the app installs — reads **+0.4764 s (0060)** and **+0.4589 s (0062)**.

**The two inertial streams do not ride the same one of those clocks.** GYRO and ACCL are stamped
together on one sample grid, and their *content* still disagrees about when by about the lag
itself. Measured with `rotation.measure_lag` (+ = the path runs behind the channel):

| 0060 / 0062 | through the stamp map (rate fit only) | through the rate fit **and** the lag |
|---|---|---|
| GYRO yaw rate vs path yaw rate | +0.476 / +0.459 s | **+0.007 / +0.002 s** |
| raw \|ACCL horizontal\| vs \|v · ω_path\| | **+0.095 / +0.053 s** | −0.382 / −0.406 s |

r is 0.917 / 0.879 and 0.905 / 0.921 whichever map is used — only the lag tells them apart. So lap
times reach the **gyro's** clock as `stamp.to_media(t) − gps_lag_s` (`_align.to_gyro_clock`,
numerically `Session.media_time`) and the **accelerometer's** as `stamp.to_media(t)`
(`_align.to_accl_clock`). The g series says the same thing from the product side
(`studio/docs/gmeter-validation.md`).

**Corrected 2026-09-17 (T9).** This document first described one conversion for both streams, and
`_accel` placed every ACCL sample through the gyro's. P1 (gyro) was unaffected. **P2 and P3 placed
their roughness ~0.4 s — ~7 m at the recordings' median speed, one 5.3 m bin — from where it was
measured.** Neither verdict moves; P3's numbers below are re-measured through the right map, and
the values they replace are given beside them.

**The placement is verified, not assumed.** Every probe re-measures the residual offset of each
stream it places with the product's own `rotation.measure_lag` after applying that stream's map. A
sign error on the gyro does not produce a small error, it produces a doubled one (≈ −0.95 s):

| | GYRO before its correction | GYRO after | ACCL through its own map | ACCL through the gyro's (control) |
|---|---|---|---|---|
| 0060 | +0.476 s | **+0.007 s** | **+0.095 s** | −0.382 s |
| 0062 | +0.459 s | **+0.002 s** | **+0.053 s** | −0.406 s |

The ACCL's residual is the ~0.05–0.1 s by which its content's own delay differs from the GPS
timestamps'; no map removes it.

P1's independent residual-lag sweep agrees: its correlation peak sits at −0.010 s (0060) and
+0.000 s (0062), with the plateau within 0.002 of the peak spanning ±0.08 s.

**Why this is not bookkeeping.** A difference of two rotation rates with an offset δ between them
returns `ω(t) − ω(t+δ) ≈ −δ·dω/dt`, which peaks at turn-in and reverses at exit — the exact shape a
scrub index is supposed to have. Measured on 0060: a deliberate ±0.5 s misalignment **doubles**
`beta_dot`'s corner RMS, from 0.1016 to 0.221 rad/s, and *raises* its corner-to-floor ratio from
1.36x to 1.55x. **A misaligned probe looks like a better channel than a correct one.** The rescued
draft of P1 fitted this offset as a free parameter by maximising correlation, which absorbs the
ppm ramp into one constant and leaves it in; this version takes the measured number and checks it.

For the place-on-track probes the same offset is a displacement: half a second at 60 km/h is ~8 m,
about 1.5 bins of a 200-bin lap.

---

## P1 — sideslip rate: REFUSED

`beta_dot = d(path heading)/dt − body yaw rate`. The body rate is measured (GYRO); the path rate is
geometry (`corners.lap_yaw_rate`, kappa carried at the trace's own ds/dt). A second, fully
independent path reference (the raw trace tangent differentiated in time) is carried throughout so
the verdict does not rest on one construction — **it agrees everywhere**, and both are quoted below
as `kappa / tangent` where they differ.

**Both references close a lap.** Over one closed lap the integrated heading change is exactly 2π:

| | gyro | `lap_yaw_rate` | trace tangent |
|---|---|---|---|
| 0060 | 0.9854 | 0.9997 | 0.9971 |
| 0062 | 0.9754 | 0.9979 | 0.9981 |

**The straights are the noise floor.** The kart is going straight, so `beta_dot` should read ~0
there. Whatever it does on the straights is what the two channels' noise does, and a corner reading
has to beat it:

| | corners | straights (floor) | ratio |
|---|---|---|---|
| 0060 | 0.1016 rad/s | 0.0747 rad/s | **1.36x** |
| 0062 | 0.0897 rad/s | 0.1266 rad/s | **0.71x** |

**On 0062 the channel is smaller in corners than its own noise on the straights.** That alone ends
it. (This is the defect class that reproduces on 0062 only, in reverse: 0060 alone would have
looked marginally alive.) For scale, `omega_path` in corners is 0.6335 / 0.6231 rad/s, so
`beta_dot` is 0.160 / 0.144 of the rotation it is differenced from.

**And what little there is, is mostly not sideslip.** A pure scale error between the two channels
produces `beta_dot = (1−g)·omega_path` exactly. Regressing that out, plus a residual-clock term —
a model with **no sideslip in it at all**:

| | gyro-on-path gain | variance explained by scale alone | scale + residual-lag | implied residual offset |
|---|---|---|---|---|
| 0060 | 0.9511 | 0.078 | 0.079 | +7 ms |
| 0062 | 0.9585 | 0.049 | 0.049 | −1 ms |

The residual offset the fit implies is a handful of milliseconds, which is an independent
confirmation of the alignment. But note what the table means: the confounds explain only ~5-8 % of
`beta_dot`'s variance, and the residual RMS in corners (0.0966 / 0.0857 rad/s) is essentially the
original. **`beta_dot` is not mostly a scale artefact — it is mostly noise.** For the error bar: a
20 / 50 / 80 ms residual misalignment would forge 0.009 / 0.022 / 0.035 rad/s of it.

**It has no per-lap resolution.** A coaching channel must separate corners by more than it wanders
between laps of the same corner:

| | between-corner sd | within-corner (across-lap) sd | separation |
|---|---|---|---|
| 0060 raw | 0.0166 | 0.0158 | 1.05 |
| 0060 scale-corrected | 0.0197 | 0.0156 | 1.26 |
| 0062 raw | 0.0110 | 0.0162 | **0.68** |
| 0062 scale-corrected | 0.0171 | 0.0163 | 1.05 |

**The lap-time correlation has a null that nearly matches it.** Per-lap mean |beta_dot| in corners
vs lap time reads +0.328 (0060) and +0.244 (0062) — but the *same statistic taken on the straights*,
where there is no scrub to measure, reads +0.247 and +0.149. Most of that correlation is "how noisy
was this lap", not "how much did the driver scrub".

**The one thing that does reproduce, and why it is not a rescue.** The per-corner *mean* signature
correlates across the two recordings at r=+0.871 (raw) and +0.919 (scale-corrected) over 12 corners.
But the raw per-corner means track corner **direction** (left corners positive, right negative),
which is the signature of the gyro's ~5 % scale under-read times each corner's *signed* rotation
rate — it reproduces because the corners reproduce, not because a driver behaviour does. Whatever
survives scale correction is the same size as the lap-to-lap scatter (separation 1.05-1.34), so it
cannot be read off a single lap, which is what coaching needs.

**No bandwidth rescues it.** Corner-to-floor ratio across matched bandwidths:

| bandwidth | 0.3 s | 0.5 s | 0.8 s | 1.3 s | 2.0 s |
|---|---|---|---|---|---|
| 0060 | 1.11x | 1.16x | 1.27x | 1.36x | 1.28x |
| 0062 | 0.53x | 0.53x | 0.58x | 0.71x | 0.75x |

**What would change this verdict:** a GPS trace whose heading is not band-limited to the 1.3 s
load-time position boxcar (the corner-scale content is smoothed away before lap columns exist), or
an independent measurement of body slip angle to validate against. Not a clock improvement — the
residual is already ±0.08 s and the forged amount at that offset (0.035 rad/s) is well under the
gap. Never label this "understeer".

---

## P2 — wheel hop / chatter: REFUSED

The practitioner description is grip building and releasing repeatedly: a **narrowband** oscillation
that appears in some corners and not others and repeats across laps.

**First, the app's own pipeline cannot see this band at all**, which is why the probe starts from
the raw 200 Hz stream. Measured on a probe tone through `gmeter`'s 0.15 s boxcar:

| tone | 5 Hz | 10 Hz | 15 Hz | 25 Hz |
|---|---|---|---|---|
| survives at | 0.308 (−10 dB) | 0.213 (−13 dB) | 0.100 (−20 dB) | 0.064 (−24 dB) |

…and 25 Hz is the Nyquist of the 50 Hz resampled output, so anything above it aliases.

**There is no peak — the spectrum just falls.** Mean vertical spectrum, dB relative to 5 Hz:

| | 2 Hz | 5 Hz | 8 Hz | 12 Hz | 16 Hz | 20 Hz | 25 Hz | 35 Hz | 50 Hz | 80 Hz |
|---|---|---|---|---|---|---|---|---|---|---|
| 0060 | −11.3 | 0.0 | −8.2 | −16.6 | −21.3 | −24.7 | −27.5 | −34.5 | −37.3 | −38.5 |
| 0062 | −11.0 | 0.0 | −10.1 | −18.1 | −22.7 | −24.9 | −28.1 | −35.4 | −38.3 | −38.2 |

Monotone. This is why the probe scores **local** prominence (each bin over a running median of its
neighbours) rather than max-over-band-median, which on a steeply falling spectrum reports a "peak"
pinned to the band's lower edge by construction.

**Prominence is barely above chance.** Against a white-noise null (a periodogram bin of Gaussian
noise is chi-square with 2 dof), median / p90 / p99:

| | measured | white-noise null |
|---|---|---|
| 0060 vertical | 6.55x / 18.70x / 62.45x | 5.90 / 11.93 / 24.74 |
| 0062 vertical | 6.95x / 23.47x / 91.85x | 5.90 / 11.93 / 24.74 |
| 0060 horizontal | 4.27x / 10.14x / 27.79x | 5.90 / 11.93 / 24.74 |
| 0062 horizontal | 4.50x / 12.03x / 35.87x | 5.90 / 11.93 / 24.74 |

**The horizontal plane is *below* the white-noise null at the median.** Vertically, 5.9 % (0060) and
9.3 % (0062) of frames beat the null's p99 — above the 1 % chance rate, so *something* occasionally
peaks, but see the next test.

**The decisive null: the peak does not sit at a stable frequency.** Peak-frequency spread over the
20 Hz band, where a uniformly random pick would give sd 5.8 Hz:

| | median | IQR | sd |
|---|---|---|---|
| 0060 vertical | 9.4 Hz | [5.5, 18.0] | **6.5 Hz** |
| 0062 vertical | 7.0 Hz | [5.5, 17.2] | **6.3 Hz** |

**Indistinguishable from random.** A resonance has a linewidth; this has the whole band. Persistence
agrees: at a lag of 4 frames (the first that shares **no** samples — adjacent STFT frames overlap by
75 %, so their agreement is mostly the window agreeing with itself) the same peak recurs 22.8 % /
27.6 % of the time against a 12.0 % chance rate. Weakly above chance, with no stable frequency to
recur *at*.

**Band power does repeat by place on track** — r=+0.953 / +0.974 vertical, +0.966 / +0.978
horizontal, split-half odd vs even laps (placed through the ACCL's own clock; through the gyro's,
before T9, +0.954 / +0.972 and +0.968 / +0.980). But that is P3's result, not this one: a place-on-track
pattern with no narrowband peak is a bump map, not chatter. The probe said so in advance.

**Where the energy actually is** (vertical): 0.5-5 Hz 53.6 % / 57.0 %, 5-25 Hz 44.5 % / 41.3 %,
25-50 Hz 0.3 %, 50-100 Hz 1.2 % / 1.1 %. Plenty of energy in the band, no structure in it — which is
consistent with the standing finding that this accelerometer's longitudinal axis is
vibration-inflated ~1.5x and weakly correlated with the GPS derivative. **This probe is what that
vibration looks like close up: broadband road buzz.**

**What would change this verdict:** a higher-rate or better-mounted IMU, or a recording of a kart
known to be hopping, to prove the method can detect one at all. As it stands the method's nulls are
sound and the answer is no.

---

## P3 — track bump map: the signal is REAL (and this package stops there)

Vertical acceleration, high-passed above 3 Hz (boxcar subtraction, the primitive the rest of the app
filters with), rolling 0.25 s RMS, median-binned into 200 track-distance bins (~5.3 m). Every
number in this section is placed through the ACCL's own clock (`_align.to_accl_clock`); the figure
in brackets is the one first published here, placed through the gyro's (see the T9 correction
above). The two profiles are one bin apart.

| | median | range | roughest / smoothest |
|---|---|---|---|
| 0060 | 3.651 m/s² [3.656] | [1.478, 14.374] [1.440, 14.131] | 9.7x [9.8x] |
| 0062 | 4.101 m/s² [4.048] | [1.378, 15.039] [1.397, 14.923] | 10.9x [10.7x] |

**(1) It repeats lap to lap.** Split-half, odd vs even laps, by track position:

| | split-half r | phase-randomised surrogate null (\|r\| p95) |
|---|---|---|
| 0060 | **+0.967** [+0.967] | 0.297 [0.306] |
| 0062 | **+0.986** [+0.986] | 0.291 [0.293] |

The null matters: a bump profile is a smooth 1-D signal, and two smooth signals correlate by chance
far more often than independent-sample intuition says. The surrogates keep the profile's power
spectrum — its smoothness — and destroy only its phase.

**(2) It repeats session to session — different days, same track.** r=**+0.952** [+0.945], at a
circular shift of **0 bins**, against a 0.288 [0.301] null. The control is the speed profile the
app already draws, which *must* reproduce: r=+0.989 at a shift of 1 bin. So the two independently auto-fitted
start lines agree to about one bin, and the bump map reproduces nearly as well as speed does.

**(3) It is not the speed profile.** This is the confound that could have made it worthless — road
excitation grows with speed, so a "bump map" that is really a speed map would reproduce perfectly
and add nothing. It does not:

| | r(bump, speed) | split-half with speed regressed out | across-session with speed regressed out |
|---|---|---|---|
| 0060 | −0.134 [−0.229] | +0.967 [+0.966] | — |
| 0062 | −0.238 [−0.309] | +0.986 [+0.985] | — |
| both | — | — | **+0.952** (vs +0.952 raw) [+0.941 vs +0.945] |

The correlation with speed is weak and **negative** — weaker still once the roughness sits where it
was measured — and removing it costs the cross-session correlation nothing [0.004]. The signal is where the track is, not how fast the kart was going.

**(4) The alignment sensitivity test, and what it cannot see.** Repeating the within-session
split-half at deliberate mis-alignments of ±0.25 s and ±0.5 s moves r by at most 0.002
(+0.967 → +0.966…+0.968 on 0060; +0.986 → +0.984…+0.986 on 0062). **Stated honestly: this test is
weak by construction** — both halves are displaced *identically*, so a common-mode shift is
invisible to it. What it establishes is that the map's *reproducibility* is robust; what it cannot
establish is that the map is correctly *positioned*, and neither can (2), whose two recordings went
through the same map. **That blindness is exactly how the first version of this section shipped one
bin mispositioned**: it took the GPS lag out of a stream whose content carries it, every test here
passed, and only a residual lag against an independent reference — `_align.check_accl`, run on
every probe start — could see it. Positioning is what matters the moment this is drawn next to a
corner on a map, so that check is the evidence for it, not (2).

### Verdict, and why it stops here

The signal is **real, reproducible across days, separable from noise by a wide margin, and not a
restatement of speed**. It is the one channel of the three that passes every null it was given.

**It is deliberately not promoted to a feature in this package**, and the reason is the third
question, not the first two: *is it specific enough to tell a driver something they could act on?*
A bump map is a property of the **track**, and a driver cannot change the track. Its value is
diagnostic rather than instructional — it disambiguates the one thing a coach named and nobody
resolves: a sawtooth lateral-g trace means either a rough track **or** a rough driver, and this
separates them. That is a real use, but it is a different feature from "draw roughness on the map",
and it needs a design decision (and a golden re-cut) that a measurement package should not make on
its own.

**What a follow-up would have to settle:** whether the disambiguation is worth a surface at all;
where it would live (the lateral-g trace's own explanation, not a new map mode); and whether the
9.7-10.9x dynamic range survives on a track that is not this one. Note also that this channel is
made of exactly the vibration that makes the IMU's longitudinal axis untrustworthy — the same energy,
localized instead of averaged.

---

## Re-running any of this

```
pixi run python -m studio.dev.probes._cache          # both recordings, ~3 s each + a 44/76 MB cache
pixi run python -m studio.dev.probes.p1_sideslip
pixi run python -m studio.dev.probes.p2_chatter
pixi run python -m studio.dev.probes.p3_bumpmap
```

The cache is keyed `0060` / `0062` under `$TMPDIR` and holds the raw 200 Hz IMU streams, the per-lap
GPS columns, the corner partition **and the two clock numbers** — a cache without those last two
cannot be aligned, and `_cache.build` refuses to write one that lacks a measured `gps_lag_s`.
