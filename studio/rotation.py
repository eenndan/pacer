"""Body yaw rate from the GoPro's gyroscope — the app's first MEASURED rotation channel.

PACER-FREE AND Qt-FREE BY CONTRACT (numpy only); fed arrays, like `gmeter.py`, whose shape and
documentation voice this module follows.

Every GoPro from the HERO5 on writes a 3-axis rate gyroscope (`GYRO`, rad/s) beside the
accelerometer on the same media clock, at ~200 Hz — and on several models FASTER than the
accelerometer, so the two streams are joined on time, never by row. Pacer read `ACCL`, `GRAV` and
`CORI` for eight releases and never read it. Meanwhile the app has inferred rotation rate TWICE from
geometry — `gmeter`'s GPS yaw rate (`lateral_g = |v|*yaw_rate/g`) and `corners.lap_curvature`'s
path curvature kappa — and had no measurement of it at all.

The channel:
  yaw_rate(t) = omega(t) . ghat(t)          rad/s, + = turning LEFT

`omega` is the raw GYRO vector; `ghat` is `GRAV` permuted onto the gyro's element order by
`gmeter.GRAV_PERM` and normalised. GYRO declares ACCL's element orientation — the GPMF ORIN and
ORIO fields of the two streams are identical on every camera measured (both D24 recordings and
all ten bundled gpmf-parser sample clips, across HERO5/6/7/8/13, Fusion, Max and Karma; what they
SAY differs by model, and on several they say nothing at all) — so it inherits ACCL's frame and
needs no handling of its own. And that frame is the RAW one the elements are written in, not the
one ORIN names: `gmeter.GRAV_PERM`'s block records the measurement that settled it, and
`gmeter.axis_check` is the per-recording guard behind that constant — on the G-METER path, which
is the only path it gates. It is NOT a guard on this one: nothing here calls it, and this module
carries its own, narrower check that the gravity direction exists at all (`MIN_GRAV_NORM`).
Projecting on gravity is what makes the number a ROAD-plane yaw rate rather than a camera-axis
one: it is independent of how the camera is tilted on its mount.

  * The permutation is load-bearing and fails QUIETLY. Projecting on the UNPERMUTED GRAV still
    produces a corner-shaped signal — r=+0.65/+0.62 against the GPS path, gain 0.35/0.32 — which
    reads as a working channel with a scale problem rather than a wrong axis. Permuted it is
    r=+0.87/+0.85, gain 0.89/0.92. Only the closed-loop test below makes the difference
    unmistakable: the un-permuted channel integrates to -0.48/-0.44 x 2*pi per lap.
  * The sign is empirical, not assumed: `+ = left` was resolved by measuring against the path's
    signed curvature (which is also + = left), not by asserting a handedness for the GoPro frame.
  * Routing omega through CORI into a fixed world frame (the `gmeter` recipe) was measured and
    changes nothing — r and gain move by <0.005 — so this module does NOT depend on CORI, and
    inherits none of its yaw drift.

WHY THERE IS NO INTEGRATED ANGLE HERE. Rate is drift-free; an angle is not. This repo measured
CORI's own gyro-integrated world yaw drifting 0.08-0.15 deg/s, i.e. 130-200 deg across one
chapter, which is what rotated and eventually inverted the lateral g the driver read. The one
integral this module does take is over a SINGLE CLOSED LAP (see `RotationCheck.loop_ratio_*`),
where the answer is known exactly and the drift has no time to accumulate.

WHY THERE IS NO BIAS SUBTRACTION. A bias estimated from the straights is an order of magnitude
LARGER than the bias that is actually there. Measured: mean yaw rate over the (guard-eroded)
straights is +0.026 / +0.015 rad/s, against a path reading of +0.009 / +0.004 — so a per-straight
estimate would subtract ~0.015 rad/s. But the closed-loop test bounds the true bias far tighter:
the integrated yaw over a lap lands within 1.7-2.5 % of 2*pi over a ~69 s lap, so any constant
bias is under ~0.002 rad/s. Subtracting the straights estimate would inject ~7x the error it
removes. The straights mean is contaminated because a kart circuit's straights are short and the
GPS/IMU clocks differ by ~0.46 s (below), which drops corner-magnitude samples inside them.

THE CROSS-CHECK, and why it has a headline number a correlation cannot give
--------------------------------------------------------------------------
The natural comparison is the path-derived rate, and `RotationCheck` reports its correlation and
its gain (the regression slope — Pearson r is scale-blind, and this repo has shipped a channel
that tracked perfectly and read half). But BOTH of those describe a comparison between two
estimates, neither of which is truth.

A lap does have a truth: it is a closed loop, so the total heading change over one lap is exactly
2*pi, whatever the racing line and whatever the smoothing. `loop_ratio_gyro` is the median of
(integral of yaw_rate dt) / 2*pi over the laps, and `loop_ratio_path` is the same integral of the
app's own path-derived rate. That turns "is this channel correctly scaled?" into a question with
an exact answer. Measured on the two D24 recordings (38 and 65 clean laps):

    integral of gyro dt            0.983 / 0.975 x 2*pi   <- the measured channel
    path heading change            1.000 / 1.000 x 2*pi   <- the geometry, as a control
    integral of dtheta/dt dt       1.001 / 1.001 x 2*pi   <- the inferred channel

So the gyro is within 1.7-2.5 % of exact: the remaining ~0.89/0.92 regression gain between the
two is the sensor's, not the reference's. It is also the reason `ok` keys off the loop ratio and
not off the gain.

BOTH D24 RECORDINGS RUN ANTICLOCKWISE, AND THAT WAS A HOLE IN THE VERDICT. A clockwise circuit
closes at -2*pi, which is exact in exactly the same way, but `ok` compared the SIGNED ratio
against [0.90, 1.10] — so every right-hand track failed a test it passed. Measured over the real
load path, all three of the owner's clockwise recordings printed DISAGREE while agreeing to
within 3 %: Sandown_09_05_2026 -0.974 x 2*pi vs -0.999 inferred over 59 laps (corners r=+0.94),
SD_30_08_26 -0.961 vs -0.999 over 37 (r=+0.95), Sandown 3h 2026 -0.973 vs -1.000 over 62
(r=+0.93). The band is now on |ratio| and the SIGN is checked against the path reference; the
exact target a surface quotes is `RotationCheck.loop_exact`, +1.000 or -1.000, and not a
literal. `tests/test_rotation.py` mirrors its fixture to carry the clockwise case the suite had
never had.

WHAT THIS TEST CAUGHT FIRST. When this channel landed, the path reference here was `v * kappa`
and it read 1.102 / 1.065 x 2*pi — a 6.5-10 % over-read that neither the correlation (+0.87) nor
the control (1.000) could have located, and which was written up as a property of the inferred
channel. It was a unit error. kappa is dtheta/ds against the lap's own odometer, and `v` is the
GPS Doppler speed — a different measure of distance — so the product was the lap's rotation
times the ratio between them. The reference is now `corners.lap_yaw_rate` (kappa on the trace's
OWN ds/dt) and the whole excess goes away; that function carries the measured budget. What it
cost to mistake the reference for the measurement: this module's first reading of the gyro's
scale was 0.83/0.87 where the honest one is 0.89/0.92, and a defect in the app's own geometry
was written down as a property of the sensor.

THE TWO CHANNELS ARE ON DIFFERENT CLOCKS, AND THE GPS ONE IS LATE
----------------------------------------------------------------
MEASURED HERE, AND CORRECTED WHERE THE PICTURE MEETS THE TELEMETRY — NOT IN THESE CHANNELS.
`measure_lag` reports it per recording, `RotationCheck.gps_lag_s` carries it and the Stats page's
DATA TRUST rotation row prints it. `Session._install_gps_lag` then folds that number into the
recording's `MediaClock`, so everything drawn over the video reads the trace at the instant the
frame shows. Nothing shifts either CHANNEL onto the other, so every statistic in this module is
still computed with the offset left in — which is most of why the corner correlation is 0.95 and
not higher — and the measurement itself always runs on `MediaClock.without_gps_lag()`, or it would
be measuring the correction it produced.

WHAT IT IS — ONE FIGURE, AND WHICH STATISTIC EVERY OTHER ONE IS. The figure is what `measure_lag`
returns over the WHOLE recording, because that is the number `Session._install_gps_lag` installs
and the DATA TRUST row prints: on the media clock the GPS trace's timestamps run **+0.4764 s
(0060) and +0.4589 s (0062)** BEHIND the gyro's for the same event (printed 0.48 s / 0.46 s).
Every other lag figure in this repo is one of these statistics of the same quantity, and says so:

    all through `measure_lag`, on the stamp map   0060                0062
    WHOLE RECORDING  <- THE figure                +0.4764             +0.4589
    per chapter                                   +0.499 / +0.470     +0.466 / +0.472 / +0.444
    per lap: median                               +0.4827             +0.4512
    per lap: IQR                                  [0.450, 0.509]      [0.439, 0.478]
    5 laps either side of a seam (medians)        +0.501 | +0.450     +0.463 | +0.493, +0.448 | +0.465

It is a CONSTANT, not a drift: no seam step is larger than the per-lap spread.

"0.483" IS TWO DIFFERENT STATISTICS, which is why a three-digit spelling cannot say which one it
is. PR #291 published it as the WHOLE-RECORDING figure from its research harness — a plain
per-sample np.interp sweep, which reads +0.4834 / +0.4593 over the same samples, 7.1 ms and 0.4 ms
from `measure_lag`'s uniform-grid lookup — and on the shipped estimator the PER-LAP MEDIAN
happens to read +0.4827. Neither is the installed figure. Quote +0.4764 / +0.4589, or name the
statistic beside the number. (That harness's per-chapter and per-lap figures were 0.491/0.471,
0.467/0.469/0.445 and a median 0.485/0.458; the table re-measures them on `measure_lag`.)

WHY THIS REPO USED TO READ ~0.35-0.40 s. That figure was measured against RAW TELEMETRY time, and
the two axes run at different rates — the media clock is ~27 ppm fast — so the lag slid by 0.100 s
(0060) and 0.167 s (0062) across a session and its average came out short. Measured per lap the
telemetry-axis figure trends +38.9 / +37.4 ppm, i.e. the rate itself; mapped through
`media_clock` the same laps trend +12.2 / +10.2 ppm and, with the load-time position boxcar off,
-2.2 / -0.1 ppm. THE DRIFT WAS THE MEASUREMENT, NOT THE CHANNEL.

IT IS NOT AN ARTIFACT OF THE FILTERS, and the filters are the first thing to suspect: two signals
put through different windows can manufacture a lag. Every window here is centred, so none of them
can move a peak, and switching them off says so. Whole-recording, media-mapped, in seconds of GPS
lag, through #291's per-sample harness (which is why the first pair reads 0.483 where `measure_lag`
reads 0.476): app filters 0.483/0.459; the gyro low-pass off 0.529/0.479; the curvature boxcar off
0.475/0.454; both off 0.433/0.479; the 13-sample load-time POSITION boxcar off (`smooth_window=1`,
a second full load) 0.471/0.465. The estimator itself was checked by delaying the real gyro
stream 0.400 s: it recovered 0.400 s on both recordings, to the millisecond.

WHICH CLOCK IS LATE — SETTLED AGAINST THE PICTURE. The video is the one reference outside both
channels, and the user watches it. Yaw taken from the FRAMES themselves (phase correlation
between consecutive frames, decoded at 160x90) sits with the GYRO and not with the GPS: over 13
one-lap windows spanning every chapter of both recordings, picture-vs-gyro ran -0.096…+0.073 s
(median -0.00) while picture-vs-GPS ran +0.03…+0.49 s. At matched bandwidth (both channels
boxcarred to the position smoother's own 1.3 s, which is zero-phase and cannot move a peak) the
difference between the two is +0.373…+0.457 s over 6 windows — the same offset this module
measures. So the gyro rides the picture's clock and the GPS trace is the one arriving late.

WHAT IS STILL OPEN. Whether the GPS timestamps are late because of the receiver's own fix latency
or because of where the camera files a fix inside a GPMF payload is NOT separable from these
streams, and the two are indistinguishable to everything downstream — and to the correction, which
only needs to know how far the trace is from the picture. What this measurement is used FOR lives
in `media_clock.MediaClock.gps_lag`: without it every GPS-derived overlay (speed, the map dot, Δ)
was drawn from a trace that trailed the picture by about half a second. Lap TIMES are differences
taken on one clock and are untouched by any of this.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ._signal import boxcar
from .corners import derive_threshold, lap_curvature, lap_yaw_rate
from .gmeter import GRAV_PERM, MIN_GRAV_NORM

# Pre-output low-pass (s). The gyro is a 200 Hz sensor on a vibrating kart mount; the rotation a
# driver can act on is corner-scale. Chosen by sweeping the window and watching BOTH statistics:
# 0.15 -> 0.30 s lifts the correlation with the path from 0.837/0.808 to 0.869/0.847 while the
# gain moves 0.894 -> 0.893 and 0.922 -> 0.921. That is the check that matters — smoothing which
# bought agreement by shrinking the signal would show up as a falling gain, and this does not.
LOWPASS_S = 0.30

# Guard (s) each side of a sample before it counts as straight/corner for the cross-check. The
# GPS and IMU clocks differ by ~0.46 s (module doc; `measure_lag` states it per recording), so a
# mask taken from the GPS trace and
# applied to the gyro leaks corner-magnitude samples into the straights: eroding by half a second
# cuts the straights' gyro RMS from 0.313 to 0.237 rad/s on the D24 0060 pair and lifts the
# corner correlation from 0.905 to 0.946 (0.913 to 0.959 on 0062). It removes ~30 % of the
# samples and no signal — the closed-loop ratios, which use every sample, do not move.
GUARD_S = 0.5

# THE GRAVITY DIRECTION HAS TO EXIST BEFORE ANYTHING CAN BE PROJECTED ON IT. GRAV is a UNIT
# vector on every camera that writes one — |GRAV| measures 1.0000 at the 5th, 50th AND 95th
# percentile on both GoPro Max sample clips and on both D24 recordings (103,680 rows each) — so a
# stream carrying no direction is unmistakable rather than a judgement call, and 0.5 sits halfway
# between the only two values that occur.
#
# It is not hypothetical: the bundled `hero8.mp4` sample's GRAV is ALL ZEROS. Un-guarded that
# neither raises nor reads as missing. `up` normalises (0,0,0) straight back to (0,0,0) (the
# 1e-12 floor below keeps the divide finite), so the dot product is EXACTLY 0.0 at every sample
# and the app reports a full-length, `has_data=True` channel reading "not turning" over a clip
# whose own gyro has a median |omega| of 0.269 rad/s. That is the same silence `gmeter.axis_check`
# exists to end, and it refuses hero8 on the G-METER path as a GRAV with no direction — but
# axis_check gates the g-meter only. Nothing stood in front of THIS path, and the module doc above
# said otherwise. An absent direction is now an ABSENT CHANNEL, which is what `compute` already
# does for a camera with no GYRO at all.
#
# The floor itself is `gmeter.MIN_GRAV_NORM`, imported above rather than retyped: the g-meter's
# axis gate refuses on the same one, so the two channels cannot disagree about whether a recording's
# gravity direction exists.

_MIN_LAP_SAMPLES = 16       # a lap trace shorter than this cannot carry a curvature profile
_TWO_PI = 2.0 * np.pi

# --- the TRUST VERDICT ---------------------------------------------------------------------
# Shape: the channel must track the path through the corners. Measured 0.946 / 0.959 on the
# guard-eroded corners of the two D24 recordings, so a floor of 0.6 is generous — but it is the
# only thing standing between a helmet-cam's vibration-dominated stream and a "measured" label,
# and a dead stream scores 0 outright.
_CORR_MIN = 0.6
# Scale: the closed-loop ratio, whose exact target is ONE WHOLE TURN — |1.0|, not +1.0. The
# measured medians are 0.983 and 0.975, with per-lap spreads of [0.945, 1.036] and [0.947, 0.997].
# A +-10 % band on the MEDIAN clears both comfortably while a x0.5 mis-scale lands at 0.49 — and
# THAT is the case the correlation cannot see: halving the gyro leaves the corner r bit-identical
# at +0.946 (measured), so without this ratio the verdict would pass a channel reading half.
#
# THE BAND IS ON THE MAGNITUDE, BECAUSE DIRECTION OF TRAVEL IS NOT A SENSOR PROPERTY. A clockwise
# circuit closes at -2*pi and an anticlockwise one at +2*pi; both are exact, and which one a
# recording gets is a fact about the track. Comparing the SIGNED ratio against [0.90, 1.10]
# therefore failed every right-hand track outright — measured over the real load path, all three
# of the owner's clockwise recordings printed DISAGREE with both channels agreeing to within 3 %
# (Sandown_09_05_2026 -0.974 vs -0.999 x 2*pi over 59 laps, r=+0.94 through the corners;
# SD_30_08_26 -0.961 vs -0.999 over 37; Sandown 3h 2026 -0.973 vs -1.000 over 62). Both D24
# recordings run anticlockwise (+0.983 / +0.975), which is why nothing caught it.
#
# The SIGN is still checked — against the path reference rather than against a hard-coded +1, so
# the question it asks is "do the two channels agree which way the kart went?", which is the
# thing a mount or permutation error breaks. A wrong gravity permutation lands at -0.48 / -0.44
# on the D24 recordings: mirrored AND at half scale, refused on either count on its own.
_LOOP_MIN, _LOOP_MAX = 0.90, 1.10


@dataclass
class RotationCheck:
    """Measured gyro yaw rate vs the app's path-derived rate (`corners.lap_yaw_rate`), over the
    clean laps.

    `corr`/`gain` describe two estimates against each other; `loop_ratio_gyro` /
    `loop_ratio_path` compare each of them against a lap's exact 2*pi of heading change, which is
    the only number here with a ground truth. See the module doc."""

    n: int                       # samples compared
    corr: float                  # Pearson r, gyro vs the path rate
    gain: float                  # regression slope: gyro = gain * path_rate
    corner_n: int
    corner_corr: float
    corner_gain: float
    straight_n: int
    straight_rms_gyro: float     # rad/s
    straight_rms_path: float     # rad/s
    straight_mean_gyro: float    # rad/s — the closest thing to an observable yaw bias
    loop_n: int                  # laps whose closed-loop integral was measurable
    loop_ratio_gyro: float       # median (integral yaw_rate dt) / 2*pi over those laps; 1 = exact
    loop_ratio_path: float       # the same for the app's path-derived dtheta/dt channel
    ok: bool
    # The two channels' CLOCK OFFSET, measured on the media clock (see `measure_lag`). None means
    # it could not be measured, never 0.0 — a camera whose gyro does not track the path at all has
    # no offset to state, and printing 0.00 s for it would be a claim nobody measured.
    gps_lag_s: float | None = None   # + = the GPS trace's timestamps run BEHIND the gyro's
    lag_corr: float = 0.0            # correlation at that offset …
    lag_corr_at_zero: float = 0.0    # … against the correlation with the offset left in

    @property
    def loop_exact(self) -> float:
        """This recording's exact closed-lap target: +1.000 x 2*pi anticlockwise, -1.000 clockwise.

        Read off the PATH reference, never off the gyro: which way the circuit runs is a fact
        about the track, and the path is the channel here that is not the one under test. Every
        surface quoting "against an exact 1.000" has to quote this instead, or it tells a driver
        at a right-hand circuit that a correct channel is 200 % wrong."""
        return -1.0 if self.loop_ratio_path < 0 else 1.0

    @property
    def loop_error_pct(self) -> float:
        """How far the measured channel's per-lap rotation lands from the exact one turn, in
        percent. Signed target (`loop_exact`), so a mirrored channel still reads ~200 % — the
        distance is from the turn the track actually makes, not from its magnitude."""
        return abs(self.loop_ratio_gyro - self.loop_exact) * 100.0

    @property
    def path_loop_error_pct(self) -> float:
        """The same for the inferred channel — reported beside it because it is the reference the
        gain is measured against, and a reference that has drifted off its own exact target is
        not one a scale verdict can be read from. It was 6.5-10 % off until the basis fix the
        module doc describes; on both D24 recordings it is now 0.1 %, and 0.1 % on all three of
        the owner's clockwise recordings too."""
        return abs(self.loop_ratio_path - self.loop_exact) * 100.0

    @property
    def lag_clause(self) -> str:
        """The clock offset as one human clause, or "" when it was not measured. Single-sourced
        here because the load-time log and the DATA TRUST row state the same fact."""
        if self.gps_lag_s is None:
            return ""
        if abs(self.gps_lag_s) < 0.005:
            # A measured zero is a result, but "0.00 s behind" reads as a direction nobody
            # measured. Say what was actually established: the two clocks agree.
            return "the GPS trace and the gyroscope agree on the clock to within 0.01 s"
        side = "behind" if self.gps_lag_s >= 0 else "ahead of"
        # What this clause is about is the CHANNELS on this card. Neither is resampled onto the
        # other, so every figure beside it — r, gain, the closed-loop ratios — carries the offset.
        # It deliberately says nothing about the VIDEO overlay, which IS corrected by this number
        # (`Session._install_gps_lag`): that is a property of the recording's picture<->trace map,
        # not of this measurement, and the surface that knows whether it was installed states it.
        return (f"the GPS trace runs {abs(self.gps_lag_s):.2f} s {side} the gyroscope, "
                f"and these figures are measured with that offset left in")

    def summary(self) -> str:
        verdict = "AGREE" if self.ok else "DISAGREE"
        lag = (f"; {self.lag_clause} (r={self.lag_corr:+.2f} at that offset against "
               f"{self.lag_corr_at_zero:+.2f} with it left in)") if self.lag_clause else ""
        return (f"rotation cross-check [{verdict}] over {self.n} samples: "
                f"r={self.corr:+.2f} vs path dtheta/dt (gain x{self.gain:.2f}); "
                f"corners r={self.corner_corr:+.2f} gain x{self.corner_gain:.2f}; "
                f"straights rms {self.straight_rms_gyro:.2f} vs {self.straight_rms_path:.2f} "
                f"rad/s; closed-lap rotation {self.loop_ratio_gyro:.3f}x2pi measured vs "
                f"{self.loop_ratio_path:.3f}x2pi inferred ({self.loop_error_pct:.1f}% vs "
                f"{self.path_loop_error_pct:.1f}% off an exact {self.loop_exact:+.3f}) over "
                f"{self.loop_n} laps{lag}.")


@dataclass
class Rotation:
    """Precomputed body yaw rate on the MEDIA clock, plus the path cross-check.

    `times` is strictly increasing (seconds, global media clock) and `yaw_rate` is in rad/s,
    positive turning LEFT — the same sign convention as `gmeter.lat_g` and
    `corners.lap_curvature`. `device` is the camera that recorded it (GPMF `DVNM`), carried here
    because the answer to "why is this channel missing?" is almost always the camera model."""

    times: np.ndarray
    yaw_rate: np.ndarray
    cross: RotationCheck | None
    device: str = ""

    def __len__(self) -> int:
        return len(self.times)

    @property
    def has_data(self) -> bool:
        return len(self.times) > 0

    def at_time(self, t: float) -> float | None:
        """Yaw rate (rad/s, + = left) at media time `t`, or None if there is no series. O(log n)
        searchsorted + nearest pick, the same shape as `GMeter.at_time`."""
        n = len(self.times)
        if n == 0:
            return None
        i = int(np.searchsorted(self.times, t))
        i = min(max(i, 0), n - 1)
        if 0 < i < n and abs(self.times[i - 1] - t) < abs(self.times[i] - t):
            i -= 1
        return float(self.yaw_rate[i])

    def deg_at_time(self, t: float) -> float | None:
        """`at_time` in degrees per second — the unit a rotation rate reads in for a human."""
        v = self.at_time(t)
        return None if v is None else float(np.degrees(v))


def _empty(device: str = "") -> Rotation:
    z = np.empty(0)
    return Rotation(times=z, yaw_rate=z.copy(), cross=None, device=device)


def _corr(a, b) -> float:
    if len(a) < 2 or np.std(a) < 1e-9 or np.std(b) < 1e-9:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _gain(a, b) -> float:
    """Least-squares slope of `a` on `b` through the origin. Through the origin because both
    series are signed rotation rates about the same zero — an intercept would be a bias neither
    channel is entitled to (see the module doc on why no bias is subtracted)."""
    denom = float(np.sum(np.asarray(b, float) ** 2))
    return float(np.sum(np.asarray(a, float) * np.asarray(b, float)) / max(denom, 1e-12))


def _erode(mask: np.ndarray, t: np.ndarray, guard: float) -> np.ndarray:
    """True only where `mask` holds continuously over [t-guard, t+guard] (see GUARD_S).

    Runs on the cumulative sum of the mask so the whole erosion is two searchsorted passes and a
    subtraction — a per-sample window loop over a full session's laps is not."""
    if guard <= 0 or len(t) == 0:
        return mask
    lo = np.searchsorted(t, t - guard, "left")
    hi = np.searchsorted(t, t + guard, "right")
    cum = np.concatenate(([0], np.cumsum(mask.astype(np.int64))))
    return mask & ((cum[hi] - cum[lo]) == (hi - lo))


# --- THE CLOCK OFFSET BETWEEN THE TWO CHANNELS -------------------------------------------------
# The GYRO rides the camera's MEDIA clock (GPMF payload spans, exactly like ACCL); the GPS trace is
# timed on the GPS9 TRUE clock. Those are two different clocks, and they disagree about WHEN: for
# one event the GPS timestamp lands about half a second LATER than the gyro timestamp.
#
# MEASURE IT ON ONE CLOCK OR THE ANSWER DRIFTS. The two axes also run at different RATES — the media
# clock is ~27 ppm fast (`media_clock.py`) — so a lag measured against raw telemetry time slides
# through a recording and reads as a drift that is really the rate. Measured per lap on the D24
# recordings through this function, telemetry axis vs the same laps on the stamp map
# (`MediaClock.without_gps_lag()`), sign as `measure_lag` returns it (+ = the GPS trace is late):
#
#     0060 (38 laps)   telemetry +0.420 s per-lap median, trend -41.5 ppm   <- the rate, as a drift
#                      stamp map +0.483 s per-lap median, trend -12.9 ppm
#     0062 (65 laps)   telemetry +0.361 s per-lap median, trend -36.8 ppm
#                      stamp map +0.451 s per-lap median, trend  -9.6 ppm
#
# On the media clock it is a CONSTANT, not a drift. THE figure is the whole-recording one,
# +0.4764 / +0.4589 s; the per-chapter and seam readings are tabled in the module doc beside it,
# each named as the statistic it is. (This block used to spell these with the opposite sign — the
# sweep's raw peak, before `measure_lag` flips it — and with #291's per-sample harness, whose
# per-lap medians were 0.485 / 0.458.) The earlier ~0.35-0.40 s figure in this repo was measured
# on the telemetry axis, so it averaged the 27 ppm ramp and understated it.
#
# THE SEARCH IS COARSE-TO-FINE because the load path pays for it: one pass at LAG_COARSE_S over
# +-LAG_SEARCH_S, then LAG_FINE_S around the winner and a parabolic vertex. The peak is broad
# (correlations within 0.002 of it span ~+-0.05 s), so the coarse step cannot miss it.
LAG_SEARCH_S = 2.0      # a camera whose GPS is a full two seconds out is not a clock to fit
LAG_COARSE_S = 0.05
LAG_FINE_S = 0.005
# Below this, the peak is not a measurement of anything. The whole-recording correlation AT the
# peak is 0.917 / 0.879 on the two D24 recordings, so this is generous by a wide margin; it exists
# to refuse a helmet-cam whose gyro never tracked the path in the first place.
LAG_MIN_CORR = 0.5

# SUB-SAMPLING THE PATH WAS TRIED AND REFUSED, and it is worth writing down because it looks free.
# This sweep is a LOAD-PATH cost — 504 ms (0060) and 652 ms (0062) written the obvious way, 13.6 %
# and 17.6 % of the whole load — and the obvious economy is to correlate every Nth path sample.
# MEASURED (with #291's per-sample harness, whose whole-recording 0060 reading is +0.483 where this
# function's is +0.4764 — module doc), that moves the answer: a bound of 8,000 samples is every 4th
# fix on 0060 (+0.483 -> +0.493, 9 ms) and every 6th on 0062, where it reads +0.633 against the
# true +0.459 — a 174 ms error, a third of the quantity being measured. Every 6th fix is one sample
# per 0.6 s, which sits
# right at the bandwidth of the 1.3 s-smoothed path rate and well inside the 0.3 s-smoothed gyro's,
# so the decimation ALIASES both channels and the aliases move the peak. The comparison therefore
# keeps every sample, and the speed comes from the gyro side instead: one uniform copy of the gyro
# series, after which a lag is an index shift rather than tens of thousands of binary searches.
# Same samples, same answer (checked against a plain per-sample sweep on both recordings), ~1/4
# the time.


def _uniform(t, y):
    """`y` resampled onto a uniform grid at its own mean rate -> (t0, hz, values).

    The gyro arrives nearly-but-not-exactly uniform (a GPMF payload lays its samples evenly across
    its own span, and the spans are not identical), so this changes nothing the stream carried; it
    buys `measure_lag` an O(1) lookup per sample."""
    span = float(t[-1] - t[0])
    if span <= 0 or len(t) < 2:
        return float(t[0]) if len(t) else 0.0, 0.0, np.asarray(y, float)
    hz = (len(t) - 1) / span
    n = len(t)
    return float(t[0]), hz, np.interp(float(t[0]) + np.arange(n) / hz, t, y)


def measure_lag(t_gyro, yaw, path_t, path_w):
    """How far the GPS trace's clock runs BEHIND the gyro's → (gps_lag_s, corr_at_lag, corr_at_0).

    `+0.4764` means an event's GPS timestamp is 0.4764 s LATER than the same event's gyro
    timestamp — the whole-recording D24 0060 reading, and the figure the app installs (+0.4589 on
    0062; the module doc tables every other statistic of it). `path_t` must already be on the
    gyro's STAMP clock: map it through `media_clock.without_gps_lag().to_media` first. Not the
    raw telemetry axis — the answer then carries the two clocks' 27 ppm rate difference as a fake
    drift (the block above) — and NOT `Session.media_clock.to_media` / `Session.media_time`, which
    since #301 carry this very lag and would read ~0 (+0.007 / +0.002 s), the correction measuring
    itself.

    Returns None when the peak is not a measurement: at the edge of the search window, or below
    `LAG_MIN_CORR`. None means "not measured", never "zero"."""
    t_gyro = np.asarray(t_gyro, float)
    path_t = np.asarray(path_t, float)
    path_w = np.asarray(path_w, float)
    if len(t_gyro) < 2 or len(path_t) < _MIN_LAP_SAMPLES:
        return None

    # EVERY path sample is compared, at every lag (see the block above for what decimating cost).
    t0, hz, grid = _uniform(t_gyro, yaw)
    if hz <= 0:
        return None
    last = len(grid) - 1

    def corr_at(lag: float) -> float:
        idx = np.rint((path_t + lag - t0) * hz).astype(np.intp)
        np.clip(idx, 0, last, out=idx)
        return _corr(grid[idx], path_w)

    coarse = np.arange(-LAG_SEARCH_S, LAG_SEARCH_S + 1e-9, LAG_COARSE_S)
    rc = np.array([corr_at(float(x)) for x in coarse])
    k = int(np.argmax(rc))
    if k == 0 or k == len(coarse) - 1:
        return None
    fine = np.arange(coarse[k] - LAG_COARSE_S, coarse[k] + LAG_COARSE_S + 1e-9, LAG_FINE_S)
    rf = np.array([corr_at(float(x)) for x in fine])
    j = int(np.argmax(rf))
    best, r_best = float(fine[j]), float(rf[j])
    if r_best < LAG_MIN_CORR:
        return None
    if 0 < j < len(fine) - 1:
        y0, y1, y2 = float(rf[j - 1]), float(rf[j]), float(rf[j + 1])
        den = y0 - 2.0 * y1 + y2
        if den < 0:
            best += 0.5 * (y0 - y2) / den * LAG_FINE_S
    # The sweep offsets the GYRO, so its peak is negative when the gyro is EARLY. Flip it once,
    # here, so every caller reads one direction: positive = the GPS trace is the late one.
    return -best, r_best, corr_at(0.0)


def yaw_rate_series(gyro, grav, lowpass_s: float = LOWPASS_S):
    """(times, yaw_rate) from the raw GYRO + GRAV arrays — the projection, without the check.

    `gyro` and `grav` are (N,4) [t, x, y, z] on the media clock, as `ingest.read_gyro` /
    `ingest.read_recording` return them. GRAV is interpolated onto the gyro's (denser, ~200 Hz)
    grid and permuted onto its element order before the projection; see the module doc for why
    that permutation is not optional."""
    if gyro is None or grav is None:
        return np.empty(0), np.empty(0)
    gyro = np.asarray(gyro, float)
    grav = np.asarray(grav, float)
    if gyro.ndim != 2 or len(gyro) < 2 or grav.ndim != 2 or len(grav) < 2:
        return np.empty(0), np.empty(0)
    t = gyro[:, 0]
    up = np.column_stack(
        [np.interp(t, grav[:, 0], grav[:, 1 + GRAV_PERM[i]]) for i in range(3)])
    norms = np.linalg.norm(up, axis=1, keepdims=True)
    if float(np.median(norms)) < MIN_GRAV_NORM:
        # No gravity direction to project on (see MIN_GRAV_NORM). Refuse the channel rather than
        # returning the all-zero one a degenerate direction silently produces.
        return np.empty(0), np.empty(0)
    up /= np.maximum(norms, 1e-12)
    yaw = np.sum(gyro[:, 1:4] * up, axis=1)
    span = float(t[-1] - t[0])
    if lowpass_s > 0 and span > 0:
        yaw = boxcar(yaw, max(int(round(lowpass_s * len(t) / span)), 1))
    return t, yaw


def _path_reference(lap_traces):
    """Per-lap path-derived rotation: -> (times, dtheta/dt, kappa, lap_slices).

    `lap_traces` is an iterable of `(times, xs, ys, speed_mps, cum_distances)` — exactly the tuple
    `Session._lap_columns` yields for one lap. Stationary duplicate odometer samples are dropped
    first because `lap_curvature` requires a strictly increasing arc length.

    The rate is `corners.lap_yaw_rate` — kappa on the trace's OWN speed ds/dt. The speed_mps
    column is deliberately NOT used: it is the GPS Doppler speed, a different measure of distance
    from the odometer kappa is differentiated against, and mixing the two is what made this
    reference over-read a lap's rotation by 6.5-10 % (see that function, and the module doc).

    The laps come back as index SLICES into the concatenated arrays, not as time bounds: a
    materialized lap ends on the interpolated finish-line crossing and the next begins on the same
    instant, so a `t0 <= t <= t1` window would claim its neighbour's first sample."""
    ts, ws, ks, lengths = [], [], [], []
    for cols in lap_traces:
        t, x, y, v, d = (np.asarray(c, float) for c in cols)
        n = min(len(t), len(x), len(y), len(v), len(d))
        t, x, y, d = t[:n], x[:n], y[:n], d[:n]
        if n < _MIN_LAP_SAMPLES:
            continue
        keep = np.concatenate(([True], np.diff(d) > 1e-9))
        t, x, y, d = t[keep], x[keep], y[keep], d[keep]
        if len(d) < _MIN_LAP_SAMPLES or d[-1] <= 0:
            continue
        k = lap_curvature(x, y, d)
        w = lap_yaw_rate(x, y, d, t, kappa=k)
        if not (np.all(np.isfinite(k)) and np.all(np.isfinite(w))):
            continue
        ts.append(t)
        ws.append(w)
        ks.append(k)
        lengths.append(len(t))
    if not ts:
        return np.empty(0), np.empty(0), np.empty(0), []
    starts = np.concatenate(([0], np.cumsum(lengths)))
    slices = [slice(int(starts[i]), int(starts[i + 1])) for i in range(len(lengths))]
    return np.concatenate(ts), np.concatenate(ws), np.concatenate(ks), slices


def _loop_ratios(t_gyro, yaw, path_t, path_w, slices):
    """Median closed-lap rotation of each channel, as a multiple of 2*pi.

    One lap is one closed circuit of the track, so the integrated yaw over it is exactly 2*pi —
    the only exact target in this comparison. Trapezoidal on each channel's own grid (the gyro on
    its native ~200 Hz samples, the path on the GPS samples), so neither is resampled onto the
    other before being judged. Only laps where BOTH integrals are measurable are counted, so the
    two medians describe the same set of laps."""
    gy, pa = [], []
    for sl in slices:
        lt = path_t[sl]
        if len(lt) <= _MIN_LAP_SAMPLES:
            continue
        m = (t_gyro >= lt[0]) & (t_gyro <= lt[-1])
        if int(np.sum(m)) <= _MIN_LAP_SAMPLES:
            continue
        gy.append(float(np.trapezoid(yaw[m], t_gyro[m])) / _TWO_PI)
        pa.append(float(np.trapezoid(path_w[sl], lt)) / _TWO_PI)
    if not gy:
        return 0, float("nan"), float("nan")
    return len(gy), float(np.median(gy)), float(np.median(pa))


def _cross_check(t_gyro, yaw, lap_traces, to_media=None) -> RotationCheck | None:
    path_t, path_w, kappa, slices = _path_reference(lap_traces)
    if len(path_t) < _MIN_LAP_SAMPLES:
        return None
    at = np.interp(path_t, t_gyro, yaw)
    finite = np.isfinite(at) & np.isfinite(path_w)
    if int(np.sum(finite)) < _MIN_LAP_SAMPLES:
        return None
    threshold = derive_threshold(kappa)
    # Erode within each lap, never across the seam between two laps: consecutive laps sit
    # back to back in the concatenated arrays, but a lap boundary is a real edge — a corner at the
    # end of one lap must not lend its guard to the start of the next.
    corner = np.zeros(len(path_t), bool)
    straight = np.zeros(len(path_t), bool)
    for sl in slices:
        c = np.abs(kappa[sl]) >= threshold
        corner[sl] = _erode(c, path_t[sl], GUARD_S)
        straight[sl] = _erode(~c, path_t[sl], GUARD_S)
    corner &= finite
    straight &= finite

    # The two channels' clock offset. Measured on the GYRO's own clock — `to_media` maps the GPS
    # times onto it — because measuring it on the telemetry axis reads the two clocks' 27 ppm rate
    # difference as a drift (see the module doc). Every finite sample, corners and straights
    # alike; the same set `corr` describes.
    q = path_t if to_media is None else np.asarray(to_media(path_t), float)
    lag = measure_lag(t_gyro, yaw, q[finite], path_w[finite])

    loop_n, loop_gyro, loop_path = _loop_ratios(t_gyro, yaw, path_t, path_w, slices)
    corner_corr = _corr(at[corner], path_w[corner]) if int(np.sum(corner)) > 2 else 0.0
    # SCALE on the magnitude, DIRECTION against the path — see the _LOOP_MIN/_LOOP_MAX block.
    # A lap closes at one whole turn either way round, so `abs` is what makes a right-hand circuit
    # judged on its channel rather than on its geography; the sign is then required to match the
    # reference, so "direction is irrelevant" never becomes "sign is unchecked". Both are NaN-safe:
    # a NaN ratio compares False in every relation here, and `loop_n > 0` already excludes it.
    same_way = np.sign(loop_gyro) == np.sign(loop_path) and np.sign(loop_gyro) != 0
    ok = (corner_corr >= _CORR_MIN and loop_n > 0 and same_way
          and _LOOP_MIN <= abs(loop_gyro) <= _LOOP_MAX)
    return RotationCheck(
        n=int(np.sum(finite)),
        corr=_corr(at[finite], path_w[finite]),
        gain=_gain(at[finite], path_w[finite]),
        corner_n=int(np.sum(corner)),
        corner_corr=corner_corr,
        corner_gain=_gain(at[corner], path_w[corner]) if int(np.sum(corner)) else 0.0,
        straight_n=int(np.sum(straight)),
        straight_rms_gyro=(float(np.sqrt(np.mean(at[straight] ** 2)))
                           if int(np.sum(straight)) else 0.0),
        straight_rms_path=(float(np.sqrt(np.mean(path_w[straight] ** 2)))
                           if int(np.sum(straight)) else 0.0),
        straight_mean_gyro=(float(np.mean(at[straight])) if int(np.sum(straight)) else 0.0),
        loop_n=loop_n, loop_ratio_gyro=loop_gyro, loop_ratio_path=loop_path,
        gps_lag_s=lag[0] if lag is not None else None,
        lag_corr=lag[1] if lag is not None else 0.0,
        lag_corr_at_zero=lag[2] if lag is not None else 0.0,
        ok=bool(ok))


def compute(gyro, grav, lap_traces=None, device: str = "", to_media=None) -> Rotation:
    """Build the measured yaw-rate channel from the raw GYRO + GRAV streams.

    Inputs (numpy arrays on the MEDIA clock, as `ingest` returns them):
      gyro: (Ng,4) [t, x, y, z] rate gyroscope, rad/s, native GYRO element order
      grav: (Nv,4) [t, x, y, z] gravity unit vector, native GRAV element order
      lap_traces: optional iterable of `(times, xs, ys, speed_mps, cum_distances)`, one per clean
        lap — the tuple `Session._lap_columns` returns. Supplying it produces the cross-check;
        without it the channel is still built, with `cross=None`.
      device: the recording camera's `DVNM`, carried through onto the result.
      to_media: optional telemetry-time -> media-time map (`media_clock.MediaClock.to_media`).
        Only the clock-offset measurement uses it, and only to put the GPS times on the gyro's own
        clock before the sweep; without it that offset carries the two clocks' rate difference as
        a drift and is not reported as a constant. Nothing else in this module is affected.

    Returns a `Rotation`. A camera with no GYRO (pre-HERO5) yields an empty one — unlike the
    g-meter there is no GPS fallback here, because a path-derived rate is precisely the thing
    this channel exists to check rather than to imitate."""
    t, yaw = yaw_rate_series(gyro, grav)
    if len(t) == 0:
        return _empty(device)
    cross = None
    if lap_traces is not None:
        traces = list(lap_traces)
        if traces:
            cross = _cross_check(t, yaw, traces, to_media)
    return Rotation(times=t, yaw_rate=yaw, cross=cross, device=device)
