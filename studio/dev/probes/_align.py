"""THE CLOCK CONVERSIONS every probe in this package goes through — and there are TWO, not one.

Each of these probes compares a GPS-derived quantity (a path rotation rate, a track position)
against an inertial one (gyro yaw rate, ACCL vertical). **The two are not on the same clock**, and
two independent facts stack up between them:

  1. THE AXES ARE DIFFERENT. `Session._lap_columns` times are the GPS9 TRUE-clock (telemetry) axis
     that `load._gps9_times` builds; `ACCL`/`GRAV`/`GYRO` times are the camera's MEDIA stamps
     `ingest` returns. `Session.media_clock.without_gps_lag()` is the affine map between them — the
     media clock runs ~27 ppm fast, which is up to 0.22 s of divergence over one D24 recording, and
     it is a RAMP, not an offset.

     Two in-repo docstrings used to say otherwise — `pacer/laps/laps.hpp` ("times  media-clock
     seconds") and `Session._lap_columns` ("media-clock seconds") — and both were CORRECTED once
     this was measured; the whole family of lap-axis comments now names the telemetry clock. The
     code that gave them away is `Session._build_rotation`, which passes
     `to_media=self.media_clock.without_gps_lag().to_media` into `rotation.compute` precisely
     because the lap traces it hands over are NOT on the gyro's clock. Anything that skips the
     conversion inherits the ramp.

  2. AND THE GPS TIMESTAMPS ARE LATE UNDERNEATH. Once (1) is taken out, PR #291 measured the GPS
     trace's timestamps landing AFTER the gyro's for the same physical event — constant, with no
     step at a chapter seam larger than the per-lap spread, and settled against the PICTURE (yaw
     taken from the frames themselves sits with the gyro, not with the GPS). The figure is
     `RotationCheck.gps_lag_s`, `rotation.measure_lag`'s whole-recording reading: **+0.4764 s
     (0060) / +0.4589 s (0062)**, the number the cache stores and every probe here subtracts
     (`rotation`'s module doc tables its per-chapter and per-lap statistics).

THE TWO INERTIAL STREAMS DO NOT RIDE THE SAME ONE OF THOSE CLOCKS, which is why this module used to
be wrong about one of them. GYRO and ACCL are stamped together on one sample grid, and their CONTENT
still disagrees about WHEN by about the lag itself. Measured with the product's own
`rotation.measure_lag` (+ = the path runs behind the channel), 0060 / 0062:

    GYRO yaw rate  vs path yaw rate        stamp map +0.476 / +0.459    GYRO map (below) +0.007 / +0.002
    raw |ACCL horizontal| vs |v * w_path|  stamp map +0.095 / +0.053    GYRO map         -0.382 / -0.406
                                           (r 0.905 / 0.921 either way — the r cannot tell them apart)

So the gyro's content rides the picture and the ACCL's rides its stamps, with the GPS timestamps'
delay in it (`Session.g_at_time` and `studio/docs/gmeter-validation.md` reach the same result from
the g series). Until T9 this module had one conversion, the gyro's, and `_accel` placed every ACCL
sample through it: P2's and P3's roughness sat ~0.4 s — about one 5.3 m bin at race speed — from
where it was measured. `to_accl_clock` is that stream's own map.

WHY THIS IS NOT OPTIONAL HERE. A difference of two rotation rates with a clock offset δ between
them returns, to first order,

    ω(t) − ω(t + δ)  ≈  −δ · dω/dt

which peaks at turn-in and reverses at exit — exactly the shape a "sideslip" or "scrub" channel is
supposed to have. At a corner-scale |dω/dt| of ~1 rad/s², the 0.46-0.48 s offset alone forges
~0.46 rad/s of a signal whose real corner RMS is a tenth of that. For the place-on-track probes the
same offset is a DISPLACEMENT: half a second at 60 km/h is ~8 m, about 1.5 bins of a 200-bin lap,
which smears a bump map and drags its split-half correlation down. Left alone, all three probes
measure the clock and not the car.

THE CONVENTION, stated once so no probe has to re-derive it
-----------------------------------------------------------
For one physical event E, with `stamp = media_clock.without_gps_lag()`:

    stamp.to_media(t_gps_telemetry(E))  =  t_gyro(E) + gps_lag_s
                                        ≈  t_accl(E)          (to the ~0.05-0.1 s residual above)

so lap-column times land on each stream's clock as

    t_gyro  =  stamp.to_media(t_lap_telemetry) − gps_lag_s     `to_gyro_clock`  (= Session.media_time)
    t_accl  =  stamp.to_media(t_lap_telemetry)                 `to_accl_clock`

THE SIGN AND THE STREAM ARE VERIFIED, NOT ASSUMED. `check` re-measures the gyro's residual with the
product's own `rotation.measure_lag` after `to_gyro_clock` — a sign error reads a doubled ~0.96 s
instead of ~0.00 s — and `check_accl` does the same for the ACCL through `to_accl_clock`, with the
gyro's map printed beside it as the control that reads ~0.4 s off. Every probe runs the check for
each stream it places.
"""

from __future__ import annotations

import numpy as np

from studio import rotation
from studio._signal import boxcar
from studio.media_clock import MediaClock

# The ACCL check's smoothing (s): the horizontal magnitude is road buzz at 200 Hz, and the path
# rate it is compared with is 1.3 s-smoothed GPS. The same 0.30 s the gyro channel uses.
_ACCL_CHECK_LOWPASS_S = 0.30


def clock_of(rec) -> MediaClock:
    """The recording's telemetry→media STAMP map (the rate fit alone, no GPS lag), rebuilt from the
    two floats the cache stored."""
    return MediaClock(rate=rec.clock_rate, offset=rec.clock_offset)


def to_gyro_clock(rec, t_telemetry):
    """Lap-column (GPS9 true-clock) times → the GYRO's media clock, EVENT-ALIGNED.

    Both corrections at once: the affine telemetry→media map, then `gps_lag_s` removed so the
    same physical instant carries the same number on both channels. This is the PICTURE map —
    numerically `Session.media_time` — and it is right for the gyro only. See the module doc."""
    t = np.asarray(t_telemetry, float)
    return clock_of(rec).to_media(t) - rec.gps_lag_s


def from_gyro_clock(rec, t_gyro):
    """The exact inverse of `to_gyro_clock`: a GYRO media time → the lap columns' telemetry axis.

    Use this to ask "where on track was the kart when the gyroscope recorded this?"."""
    t = np.asarray(t_gyro, float)
    return clock_of(rec).to_telemetry(t + rec.gps_lag_s)


def to_accl_clock(rec, t_telemetry):
    """Lap-column (GPS9 true-clock) times → the ACCL's media stamps, EVENT-ALIGNED.

    The affine map ALONE. The accelerometer's content carries very nearly the delay the GPS
    timestamps carry, so taking `gps_lag_s` out here moves an ACCL sample ~0.4 s away from its
    event instead of onto it (module doc; `check_accl` measures it)."""
    t = np.asarray(t_telemetry, float)
    return clock_of(rec).to_media(t)


def describe(rec) -> str:
    """One line stating the correction this recording gets, for every probe's header."""
    c = clock_of(rec)
    ppm = (c.rate - 1.0) * 1e6
    span = float(rec.accl[-1, 0] - rec.accl[0, 0]) if len(rec.accl) else 0.0
    return (f"clock: media_clock rate {ppm:+.2f} ppm offset {c.offset:+.4f}s "
            f"(ramps {abs(ppm) * span * 1e-6:.3f}s over this {span / 60:.0f}-minute recording) "
            f"+ gps_lag_s {rec.gps_lag_s:+.4f}s, taken out for the GYRO only "
            f"(r={rec.lag_corr:+.3f} at that offset vs {rec.lag_corr_at_zero:+.3f} with it in)")


def residual_lag(rec, gyro_t, gyro_yaw, applied: bool = True):
    """Re-measure the GPS↔gyro offset with the PRODUCT's own estimator, after our correction.

    -> (lag_s, corr, corr_at_zero) from `rotation.measure_lag`, or None when it refuses.

    `applied=True` is the check that matters: having mapped the lap times onto the gyro's clock
    AND removed `gps_lag_s`, a re-measurement must come back ≈ 0.000 s. `applied=False` reproduces
    the uncorrected reading (≈ `gps_lag_s`) as the control beside it, so the pair shows the
    correction working rather than asserting it. A sign error reads ≈ −2 × `gps_lag_s`."""
    path_t, path_w, _kappa, _slices = rotation._path_reference(
        [rec.lap(i) for i in range(len(rec))])
    if len(path_t) == 0:
        return None
    q = to_gyro_clock(rec, path_t) if applied else clock_of(rec).to_media(path_t)
    finite = np.isfinite(q) & np.isfinite(path_w)
    return rotation.measure_lag(gyro_t, gyro_yaw, q[finite], path_w[finite])


def check(rec, gyro_t, gyro_yaw, tol_s: float = 0.05) -> bool:
    """Print both GYRO readings and say whether its correction landed. Called by every probe that
    places a gyro sample."""
    before = residual_lag(rec, gyro_t, gyro_yaw, applied=False)
    after = residual_lag(rec, gyro_t, gyro_yaw, applied=True)
    if before is None or after is None:
        print("  GYRO alignment check: measure_lag refused — cannot verify the correction")
        return False
    ok = abs(after[0]) <= tol_s
    verdict = "OK" if ok else f"FAILED (a sign error reads about {-2 * rec.gps_lag_s:+.2f}s)"
    print(f"  GYRO alignment check: residual GPS lag {before[0]:+.3f}s BEFORE the correction -> "
          f"{after[0]:+.3f}s AFTER it (r={after[1]:+.3f}); {verdict}")
    return ok


def residual_lag_accl(rec, map_fn):
    """Re-measure the ACCL↔path offset through `map_fn` (`to_accl_clock` or, as the control,
    `to_gyro_clock`) -> (lag_s, corr, corr_at_zero) or None.

    Rotation-free, so no frame is needed: the horizontal ACCL magnitude against the path's
    centripetal |v · ω_path|. r is 0.905 / 0.921 on the two D24 recordings, and identical under
    either map — only the lag separates them."""
    from ._accel import vertical_and_horizontal

    t, _av, h1, h2 = vertical_and_horizontal(rec.accl, rec.grav)
    if len(t) < 2:
        return None
    fs = (len(t) - 1) / (t[-1] - t[0])
    ah = boxcar(np.hypot(h1, h2), max(int(round(_ACCL_CHECK_LOWPASS_S * fs)), 1))
    laps = [rec.lap(i) for i in range(len(rec))]
    path_t, path_w, _kappa, _slices = rotation._path_reference(laps)
    if len(path_t) == 0:
        return None
    lap_t = np.concatenate([np.asarray(c[0], float) for c in laps])
    lap_v = np.concatenate([np.asarray(c[3], float) for c in laps])
    centripetal = np.abs(np.interp(path_t, lap_t, lap_v) * path_w)
    q = np.asarray(map_fn(rec, path_t), float)
    finite = np.isfinite(q) & np.isfinite(centripetal)
    return rotation.measure_lag(t, ah, q[finite], centripetal[finite])


def check_accl(rec, tol_s: float = 0.15) -> bool:
    """Print the ACCL residual through its own map and through the gyro's, and say whether the
    ACCL placement landed. Called by every probe that places an accelerometer sample.

    The tolerance is wider than the gyro's because the ACCL content's own delay differs from the
    GPS timestamps' by ~0.05-0.1 s (the stamp-map residual), which no map removes — but it is well
    inside the ~0.4 s the gyro's map would put in."""
    own = residual_lag_accl(rec, to_accl_clock)
    control = residual_lag_accl(rec, to_gyro_clock)
    if own is None or control is None:
        print("  ACCL alignment check: measure_lag refused — cannot verify the placement")
        return False
    ok = abs(own[0]) <= tol_s
    verdict = "OK" if ok else "FAILED"
    print(f"  ACCL alignment check: residual {own[0]:+.3f}s through its own stamp map "
          f"(r={own[1]:+.3f}); through the GYRO's map it would read {control[0]:+.3f}s; {verdict}")
    return ok
