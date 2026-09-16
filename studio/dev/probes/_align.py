"""THE ONE CLOCK CONVERSION every probe in this package goes through.

Each of these probes compares a GPS-derived quantity (a path rotation rate, a track position)
against an inertial one (gyro yaw rate, ACCL vertical). **The two are not on the same clock**, and
two independent facts stack up between them:

  1. THE AXES ARE DIFFERENT. `Session._lap_columns` times are the GPS9 TRUE-clock (telemetry) axis
     that `load._gps9_times` builds; `ACCL`/`GRAV`/`GYRO` times are the MEDIA clock `ingest`
     returns. `Session.media_clock` is the affine map between them — the media clock runs ~27 ppm
     fast, which is up to 0.22 s of divergence over one D24 recording, and it is a RAMP, not an
     offset.

     Two in-repo docstrings used to say otherwise — `pacer/laps/laps.hpp` ("times  media-clock
     seconds") and `Session._lap_columns` ("media-clock seconds") — and both were CORRECTED once
     this was measured; the whole family of lap-axis comments now names the telemetry clock. The
     code that gave them away is `Session._build_rotation`, which passes
     `to_media=self.media_clock.to_media` into `rotation.compute` precisely because the lap traces
     it hands over are NOT on the gyro's clock. Anything that skips the conversion inherits the
     ramp.

  2. AND THERE IS STILL A CONSTANT OFFSET UNDERNEATH. Once (1) is taken out, PR #291 measured the
     GPS trace's timestamps landing +0.483 s (0060) and +0.459 s (0062) AFTER the gyro's for the
     same physical event — constant, with no step at any chapter seam, and settled against the
     PICTURE (yaw taken from the frames themselves sits with the gyro, not with the GPS). It is
     exposed as `RotationCheck.gps_lag_s`. Pacer does not shift either channel in the product; a
     probe that difference two channels must.

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
For one physical event E:

    t_gps_media(E)  =  media_clock.to_media(t_gps_telemetry(E))
                    =  t_gyro(E) + gps_lag_s

so lap-column times land on the inertial (ACCL/GYRO) clock as

    t_inertial  =  media_clock.to_media(t_lap_telemetry) − gps_lag_s

and that is `to_inertial` below. Everything else in this package is a call to it.

THE SIGN IS VERIFIED, NOT ASSUMED. `residual_lag` re-measures the offset with the product's own
`rotation.measure_lag` after the correction has been applied. Getting the sign backwards does not
produce a small error, it produces a doubled one (~0.96 s instead of ~0.00 s), so this is a
negative control with a very loud failure mode, and every probe runs it at start-up.
"""

from __future__ import annotations

import numpy as np

from studio import rotation
from studio.media_clock import MediaClock


def clock_of(rec) -> MediaClock:
    """The recording's telemetry→media map, rebuilt from the two floats the cache stored."""
    return MediaClock(rate=rec.clock_rate, offset=rec.clock_offset)


def to_inertial(rec, t_telemetry):
    """Lap-column (GPS9 true-clock) times → the ACCL/GYRO media clock, EVENT-ALIGNED.

    Both corrections at once: the affine telemetry→media map, then `gps_lag_s` removed so the
    same physical instant carries the same number on both channels. See the module doc."""
    t = np.asarray(t_telemetry, float)
    return clock_of(rec).to_media(t) - rec.gps_lag_s


def to_track_clock(rec, t_inertial):
    """The exact inverse: an ACCL/GYRO media time → the lap columns' telemetry axis.

    Use this to ask "where on track was the kart when the accelerometer recorded this?"."""
    t = np.asarray(t_inertial, float)
    return clock_of(rec).to_telemetry(t + rec.gps_lag_s)


def describe(rec) -> str:
    """One line stating the correction this recording gets, for every probe's header."""
    c = clock_of(rec)
    ppm = (c.rate - 1.0) * 1e6
    span = float(rec.accl[-1, 0] - rec.accl[0, 0]) if len(rec.accl) else 0.0
    return (f"clock: media_clock rate {ppm:+.2f} ppm offset {c.offset:+.4f}s "
            f"(ramps {abs(ppm) * span * 1e-6:.3f}s over this {span / 60:.0f}-minute recording) "
            f"+ gps_lag_s {rec.gps_lag_s:+.3f}s from PR #291 "
            f"(r={rec.lag_corr:+.3f} at that offset vs {rec.lag_corr_at_zero:+.3f} with it in)")


def residual_lag(rec, gyro_t, gyro_yaw, applied: bool = True):
    """Re-measure the GPS↔gyro offset with the PRODUCT's own estimator, after our correction.

    -> (lag_s, corr, corr_at_zero) from `rotation.measure_lag`, or None when it refuses.

    `applied=True` is the check that matters: having mapped the lap times onto the inertial clock
    AND removed `gps_lag_s`, a re-measurement must come back ≈ 0.000 s. `applied=False` reproduces
    the uncorrected reading (≈ `gps_lag_s`) as the control beside it, so the pair shows the
    correction working rather than asserting it. A sign error reads ≈ −2 × `gps_lag_s`."""
    path_t, path_w, _kappa, _slices = rotation._path_reference(
        [rec.lap(i) for i in range(len(rec))])
    if len(path_t) == 0:
        return None
    q = to_inertial(rec, path_t) if applied else clock_of(rec).to_media(path_t)
    finite = np.isfinite(q) & np.isfinite(path_w)
    return rotation.measure_lag(gyro_t, gyro_yaw, q[finite], path_w[finite])


def check(rec, gyro_t, gyro_yaw, tol_s: float = 0.05) -> bool:
    """Print both readings and say whether the correction landed. Called by every probe."""
    before = residual_lag(rec, gyro_t, gyro_yaw, applied=False)
    after = residual_lag(rec, gyro_t, gyro_yaw, applied=True)
    if before is None or after is None:
        print("  ALIGNMENT CHECK: measure_lag refused — cannot verify the correction")
        return False
    ok = abs(after[0]) <= tol_s
    verdict = "OK" if ok else f"FAILED (a sign error reads about {-2 * rec.gps_lag_s:+.2f}s)"
    print(f"  alignment check: residual GPS lag {before[0]:+.3f}s BEFORE the correction -> "
          f"{after[0]:+.3f}s AFTER it (r={after[1]:+.3f}); {verdict}")
    return ok
