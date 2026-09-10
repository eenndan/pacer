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
all eleven bundled gpmf-parser sample clips, across HERO5/6/7/8/13, Fusion, Max and Karma) — so
it inherits ACCL's frame and needs no handling of its own. Projecting on gravity is what makes
the number a ROAD-plane yaw rate rather than a camera-axis one: it is independent of how the
camera is tilted on its mount.

  * The permutation is load-bearing and fails QUIETLY. Projecting on the UNPERMUTED GRAV still
    produces a corner-shaped signal — r=+0.65/+0.62 against the GPS path, gain 0.32/0.30 — which
    reads as a working channel with a scale problem rather than a wrong axis. Permuted it is
    r=+0.87/+0.85, gain 0.83/0.87. Only the closed-loop test below makes the difference
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
GPS/IMU clocks differ by ~0.4 s (below), which drops corner-magnitude samples inside them.

THE CROSS-CHECK, and why it has a headline number a correlation cannot give
--------------------------------------------------------------------------
The natural comparison is the path-derived rate `v * kappa`, and `RotationCheck` reports its
correlation and its gain (the regression slope — Pearson r is scale-blind, and this repo has
shipped a channel that tracked perfectly and read half). But BOTH of those describe a comparison
between two estimates, neither of which is truth.

A lap does have a truth: it is a closed loop, so the total heading change over one lap is exactly
2*pi, whatever the racing line and whatever the smoothing. `loop_ratio_gyro` is the median of
(integral of yaw_rate dt) / 2*pi over the laps, and `loop_ratio_path` is the same integral of the
app's own `v * kappa`. That turns "is this channel correctly scaled?" into a question with an
exact answer. Measured on the two D24 recordings (38 and 65 clean laps):

    integral of gyro dt   0.983 / 0.975 x 2*pi     <- the measured channel
    path heading change   1.000 / 1.000 x 2*pi     <- the geometry, as a control
    integral of v*kappa   1.102 / 1.065 x 2*pi     <- the app's existing inferred channel

So the gyro is within 1.7-2.5 % of exact, and `v * kappa` OVER-READS total rotation by 6.5-10 %.
That is most of the ~0.87 regression gain between them: the deficit is in the reference, not in
the measurement. It is also the reason `ok` keys off the loop ratio and not off the gain.

Known and NOT corrected here: the GYRO series leads the GPS trace by ~0.35-0.40 s (a lag sweep
peaks there on both recordings, lifting r from 0.836 to 0.877 and 0.808 to 0.837). Corner-scale
yaw swings between +-1.5 rad/s inside a second, so that offset is most of the residual
disagreement, and all of the apparent excess rotation on straights. Diagnosing which clock is
late is a separate question from reading the stream.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ._signal import boxcar
from .corners import derive_threshold, lap_curvature
from .gmeter import GRAV_PERM

# Pre-output low-pass (s). The gyro is a 200 Hz sensor on a vibrating kart mount; the rotation a
# driver can act on is corner-scale. Chosen by sweeping the window and watching BOTH statistics:
# 0.15 -> 0.30 s lifts the correlation with the path from 0.836/0.808 to 0.867/0.847 while the
# gain moves 0.833 -> 0.832 and 0.872 -> 0.871. That is the check that matters — smoothing which
# bought agreement by shrinking the signal would show up as a falling gain, and this does not.
LOWPASS_S = 0.30

# Guard (s) each side of a sample before it counts as straight/corner for the cross-check. The
# GPS and IMU clocks differ by ~0.35-0.40 s (module doc), so a mask taken from the GPS trace and
# applied to the gyro leaks corner-magnitude samples into the straights: eroding by half a second
# cuts the straights' gyro RMS from 0.313 to 0.237 rad/s on the D24 0060 pair and lifts the
# corner correlation from 0.904 to 0.944 (0.913 to 0.959 on 0062). It removes ~30 % of the
# samples and no signal — the closed-loop ratios, which use every sample, do not move.
GUARD_S = 0.5

_MIN_LAP_SAMPLES = 16       # a lap trace shorter than this cannot carry a curvature profile
_TWO_PI = 2.0 * np.pi

# --- the TRUST VERDICT ---------------------------------------------------------------------
# Shape: the channel must track the path through the corners. Measured 0.944 / 0.959 on the
# guard-eroded corners of the two D24 recordings, so a floor of 0.6 is generous — but it is the
# only thing standing between a helmet-cam's vibration-dominated stream and a "measured" label,
# and a dead stream scores 0 outright.
_CORR_MIN = 0.6
# Scale: the closed-loop ratio, which unlike a regression slope has an exact target (1.0). The
# measured medians are 0.983 and 0.975, with per-lap spreads of [0.945, 1.036] and [0.947, 0.997].
# A +-10 % band on the MEDIAN clears both comfortably while a x0.5 mis-scale lands at 0.49 — and
# THAT is the case the correlation cannot see: halving the gyro leaves the corner r bit-identical
# at +0.944 (measured), so without this ratio the verdict would pass a channel reading half.
# A wrong gravity permutation lands at -0.48 / -0.44 on those recordings, failing on sign too.
_LOOP_MIN, _LOOP_MAX = 0.90, 1.10


@dataclass
class RotationCheck:
    """Measured gyro yaw rate vs the app's path-derived `v * kappa`, over the clean laps.

    `corr`/`gain` describe two estimates against each other; `loop_ratio_gyro` /
    `loop_ratio_path` compare each of them against a lap's exact 2*pi of heading change, which is
    the only number here with a ground truth. See the module doc."""

    n: int                       # samples compared
    corr: float                  # Pearson r, gyro vs v*kappa
    gain: float                  # regression slope: gyro = gain * (v*kappa)
    corner_n: int
    corner_corr: float
    corner_gain: float
    straight_n: int
    straight_rms_gyro: float     # rad/s
    straight_rms_path: float     # rad/s
    straight_mean_gyro: float    # rad/s — the closest thing to an observable yaw bias
    loop_n: int                  # laps whose closed-loop integral was measurable
    loop_ratio_gyro: float       # median (integral yaw_rate dt) / 2*pi over those laps; 1 = exact
    loop_ratio_path: float       # the same for the app's v*kappa channel
    ok: bool

    @property
    def loop_error_pct(self) -> float:
        """How far the measured channel's per-lap rotation lands from the exact 2*pi, in percent."""
        return abs(self.loop_ratio_gyro - 1.0) * 100.0

    @property
    def path_loop_error_pct(self) -> float:
        """The same for `v * kappa` — reported beside it because on both D24 recordings the
        inferred channel is the one further from truth, which is the whole point of measuring."""
        return abs(self.loop_ratio_path - 1.0) * 100.0

    def summary(self) -> str:
        verdict = "AGREE" if self.ok else "DISAGREE"
        return (f"rotation cross-check [{verdict}] over {self.n} samples: "
                f"r={self.corr:+.2f} vs path v*kappa (gain x{self.gain:.2f}); "
                f"corners r={self.corner_corr:+.2f} gain x{self.corner_gain:.2f}; "
                f"straights rms {self.straight_rms_gyro:.2f} vs {self.straight_rms_path:.2f} "
                f"rad/s; closed-lap rotation {self.loop_ratio_gyro:.3f}x2pi measured vs "
                f"{self.loop_ratio_path:.3f}x2pi inferred ({self.loop_error_pct:.1f}% vs "
                f"{self.path_loop_error_pct:.1f}% off exact) over {self.loop_n} laps.")


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
    up /= np.maximum(np.linalg.norm(up, axis=1, keepdims=True), 1e-12)
    yaw = np.sum(gyro[:, 1:4] * up, axis=1)
    span = float(t[-1] - t[0])
    if lowpass_s > 0 and span > 0:
        yaw = boxcar(yaw, max(int(round(lowpass_s * len(t) / span)), 1))
    return t, yaw


def _path_reference(lap_traces):
    """Per-lap path-derived rotation: -> (times, v*kappa, kappa, lap_slices).

    `lap_traces` is an iterable of `(times, xs, ys, speed_mps, cum_distances)` — exactly the tuple
    `Session._lap_columns` yields for one lap. Stationary duplicate odometer samples are dropped
    first because `lap_curvature` requires a strictly increasing arc length.

    The laps come back as index SLICES into the concatenated arrays, not as time bounds: a
    materialized lap ends on the interpolated finish-line crossing and the next begins on the same
    instant, so a `t0 <= t <= t1` window would claim its neighbour's first sample."""
    ts, ws, ks, lengths = [], [], [], []
    for cols in lap_traces:
        t, x, y, v, d = (np.asarray(c, float) for c in cols)
        n = min(len(t), len(x), len(y), len(v), len(d))
        t, x, y, v, d = t[:n], x[:n], y[:n], v[:n], d[:n]
        if n < _MIN_LAP_SAMPLES:
            continue
        keep = np.concatenate(([True], np.diff(d) > 1e-9))
        t, x, y, v, d = t[keep], x[keep], y[keep], v[keep], d[keep]
        if len(d) < _MIN_LAP_SAMPLES or d[-1] <= 0:
            continue
        k = lap_curvature(x, y, d)
        if not np.all(np.isfinite(k)):
            continue
        ts.append(t)
        ws.append(v * k)
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


def _cross_check(t_gyro, yaw, lap_traces) -> RotationCheck | None:
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

    loop_n, loop_gyro, loop_path = _loop_ratios(t_gyro, yaw, path_t, path_w, slices)
    corner_corr = _corr(at[corner], path_w[corner]) if int(np.sum(corner)) > 2 else 0.0
    ok = (corner_corr >= _CORR_MIN and loop_n > 0
          and _LOOP_MIN <= loop_gyro <= _LOOP_MAX)
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
        ok=bool(ok))


def compute(gyro, grav, lap_traces=None, device: str = "") -> Rotation:
    """Build the measured yaw-rate channel from the raw GYRO + GRAV streams.

    Inputs (numpy arrays on the MEDIA clock, as `ingest` returns them):
      gyro: (Ng,4) [t, x, y, z] rate gyroscope, rad/s, native GYRO element order
      grav: (Nv,4) [t, x, y, z] gravity unit vector, native GRAV element order
      lap_traces: optional iterable of `(times, xs, ys, speed_mps, cum_distances)`, one per clean
        lap — the tuple `Session._lap_columns` returns. Supplying it produces the cross-check;
        without it the channel is still built, with `cross=None`.
      device: the recording camera's `DVNM`, carried through onto the result.

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
            cross = _cross_check(t, yaw, traces)
    return Rotation(times=t, yaw_rate=yaw, cross=cross, device=device)
