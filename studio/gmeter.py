"""Vehicle-frame g from the GoPro's real accelerometer, with a GPS-derived cross-check.

Computes kart-frame lateral/longitudinal g (in g) from the GoPro IMU streams (ACCL, GRAV, CORI,
all on the media clock) plus an independent GPS-derived cross-check. Axis conventions resolved
empirically (see studio/docs/gmeter-validation.md).

The camera->kart transform:
  1. Gravity-remove: GRAV permuted onto ACCL's axes via PERM=(1,0,2); linear = ACCL - 9.81*ĝ.
  2. Rotate camera->world via CORI's conjugate (CORI stores world->camera); the rotated gravity
     is constant over time, confirming the rotation.
  3. Project onto the horizontal plane (perpendicular to world-gravity). Magnitude is correct but
     the in-plane yaw is arbitrary (CORI yaw drifts, not tied to GPS north).
  4. Resolve the constant yaw + handedness by a per-recording Procrustes (SVD) fit of the ACCL
     horizontal accel onto the GPS-derived one (a one-time mount calibration). Then split each
     sample into forward (along GPS velocity) and lateral using the GPS heading.

  longitudinal_g = a_forward / 9.81     (+ = accelerating, - = braking)
  lateral_g      = a_left    / 9.81     (+ = turning left, - = turning right)

GPS cross-check (the acid test of the transform): from the GPS trajectory,
longitudinal_g = (d|v|/dt)/9.81 and lateral_g = (|v|*yaw_rate)/9.81; compared over the moving
session via correlation + RMS (lateral correlates strongly; longitudinal magnitude matches but
its per-sample correlation is weaker, as expected for the noisy forward-g channel).

All of this runs once at load; GMeter.at_time is a cheap searchsorted lookup for the 30 Hz tick.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ._signal import G, boxcar, speed_long_g

# Empirically resolved GoPro stream-frame conventions (see module docstring + validation doc).
# GRAV/CORI element order is a permutation of ACCL's native (Z,X,Y) element order.
_PERM = (1, 0, 2)
# CORI stores world->camera; conjugate it to rotate camera->world.
_CORI_CONJUGATE = True

_OUTPUT_HZ = 50.0    # output rate; g is band-limited well below ACCL 200Hz
_LOWPASS_S = 0.15    # pre-output low-pass (s): kills road buzz without lagging corners/brakes
_MOVING_MS = 4.0     # m/s; heading is ill-defined at a standstill (used for fit + cross-check)
# The live dial / export overlay read LONGITUDINAL g from the GPS speed derivative, not the IMU
# forward axis: the latter is vibration-dominated (~1.5x inflated, weakly correlated with the
# validated GPS-derived g — see studio/docs/gmeter-validation.md and the brake/coast channels). A
# 0.35 s boxcar removes the d|v|/dt spikes, leaving a signal that is both correctly scaled AND
# smoother than the raw IMU. Lateral g (which the IMU gets right, r~0.9) is unchanged.
_DIAL_LONG_SMOOTH_S = 0.35

# --- CORI yaw DRIFT ---------------------------------------------------------------------------
# The GoPro derives CORI's world frame by integrating its gyro, with no magnetometer to hold it, so
# the "world" yaw DRIFTS steadily through a chapter — measured at ~0.08 deg/s on a D24 recording and
# ~0.15 deg/s on a Sandown one, i.e. 130-200 deg end to end over a ~27 min chapter. ONE Procrustes
# fit per chapter can therefore only be right where the drift crosses its mean (the middle laps);
# towards either end the g vector comes out rotated by the accumulated error, which SHRINKS the
# lateral g the driver reads by cos(error) and eventually INVERTS it. Measured per lap, the observed
# lateral gain tracked cos(residual yaw) to within a few percent across every lap of both
# recordings — a pure rotation error, not noise.
#
# So the yaw is fitted in WINDOWS across the chapter and interpolated per sample. The window is a
# compromise: long enough that a window holds several corners in both directions (a fit needs both
# to be conditioned), short enough to track the drift within a few degrees.
_YAW_WIN_S = 90.0          # window length for one yaw fit
_YAW_HOP_S = 45.0          # hop between window centres (50% overlap)
_YAW_MIN_SAMPLES = 200     # moving samples a window needs before its fit is trusted
_YAW_MIN_LAT_RMS = 0.15    # g; a window with no real cornering can't fit a yaw — skip it
_YAW_MIN_WINDOWS = 3       # below this, keep the single whole-chapter fit (nothing to interpolate)
# A rotation is only determined if the reference vectors point in a SPREAD of directions. If they
# are near-collinear — a single long corner, or a slalom whose lateral acceleration always lies on
# one world axis — every rotation mapping that line onto itself fits equally well and the fit
# returns noise, which would corrupt an alignment that was already correct. Gate on the 2x2
# direction scatter's singular-value ratio; a real lap sweeps the full circle and clears this
# easily. (Measured: without the gate, a collinear synthetic drove a clean fixture from
# lateral r=+0.69 down to -0.05.)
_YAW_MIN_COND = 0.08


def _norm_rows(a):
    return a / np.maximum(np.linalg.norm(a, axis=1, keepdims=True), 1e-12)


def _quat_rotate_world(qw, qx, qy, qz, v):
    """Rotate camera-frame vectors `v` (N,3) into the world frame using the per-sample CORI
    quaternion (already conjugated if needed). Vectorised; builds the rotation matrix terms
    inline so it stays pure-numpy and fast."""
    r00 = 1 - 2 * (qy * qy + qz * qz)
    r01 = 2 * (qx * qy - qz * qw)
    r02 = 2 * (qx * qz + qy * qw)
    r10 = 2 * (qx * qy + qz * qw)
    r11 = 1 - 2 * (qx * qx + qz * qz)
    r12 = 2 * (qy * qz - qx * qw)
    r20 = 2 * (qx * qz - qy * qw)
    r21 = 2 * (qy * qz + qx * qw)
    r22 = 1 - 2 * (qx * qx + qy * qy)
    return np.column_stack([
        r00 * v[:, 0] + r01 * v[:, 1] + r02 * v[:, 2],
        r10 * v[:, 0] + r11 * v[:, 1] + r12 * v[:, 2],
        r20 * v[:, 0] + r21 * v[:, 1] + r22 * v[:, 2],
    ])


@dataclass
class CrossCheck:
    """ACCL-derived g vs GPS-derived g over the moving session: per-channel correlation + RMS,
    the fitted mount yaw/handedness, and a trust verdict (ok)."""
    n: int               # number of moving samples compared
    lat_corr: float      # Pearson r, ACCL lateral g vs GPS lateral g
    long_corr: float     # Pearson r, ACCL longitudinal g vs GPS longitudinal g
    lat_rms_accl: float
    lat_rms_gps: float
    long_rms_accl: float
    long_rms_gps: float
    align_yaw_deg: float    # fitted CORI-world -> ENU yaw (per-recording mount calibration)
    align_reflect: bool     # whether the fit needed a handedness flip
    ok: bool                # heuristic: is the ACCL g trustworthy (vs head-dominated garbage)?

    def summary(self) -> str:
        verdict = "AGREE" if self.ok else "DISAGREE (ACCL may be mount/vibration-dominated)"
        return (f"g cross-check [{verdict}] over {self.n} moving samples: "
                f"lateral r={self.lat_corr:+.2f} (rms {self.lat_rms_accl:.2f} vs "
                f"{self.lat_rms_gps:.2f} g), longitudinal r={self.long_corr:+.2f} "
                f"(rms {self.long_rms_accl:.2f} vs {self.long_rms_gps:.2f} g); "
                f"mount yaw {self.align_yaw_deg:+.0f} deg"
                f"{', reflected' if self.align_reflect else ''}.")


@dataclass
class GMeter:
    """Precomputed vehicle-frame g time series on the MEDIA clock, plus the GPS cross-check.

    `times` is strictly increasing (seconds, global media clock). `lat_g`/`long_g` are the
    kart-frame lateral / longitudinal acceleration in g. `at_time` is the cheap per-tick lookup
    the overlay uses. `source` records which sensor produced the live signal ("accl" by default;
    "gps" if the GPS fallback was selected)."""
    times: np.ndarray
    lat_g: np.ndarray
    long_g: np.ndarray
    cross: CrossCheck | None
    source: str = "accl"
    # The dial/overlay longitudinal series (GPS speed derivative, smoothed) on the same `times`
    # grid; None for a synthetic/GPS-only meter, in which case at_time falls back to long_g.
    long_g_gps: np.ndarray | None = None

    def __len__(self) -> int:
        return len(self.times)

    @property
    def long_source(self) -> str:
        """Where the dial's longitudinal g comes from: 'gps' (the validated speed derivative) when
        available, else 'accl' (the raw IMU forward axis)."""
        return "gps" if self.long_g_gps is not None else self.source

    def at_time(self, t: float) -> tuple[float, float, float] | None:
        """(lateral_g, longitudinal_g, total_g) at media time `t`, or None if no g series.
        Lateral is from the IMU (which it gets right); LONGITUDINAL is the GPS-derived signal
        (long_g_gps) when present — the IMU forward axis is vibration-inflated. O(log n)
        searchsorted + nearest pick; called at the 30 Hz tick."""
        n = len(self.times)
        if n == 0:
            return None
        i = int(np.searchsorted(self.times, t))
        i = min(max(i, 0), n - 1)
        # nearest of the two bracketing samples (the series is dense, so this is plenty)
        if 0 < i < n and abs(self.times[i - 1] - t) < abs(self.times[i] - t):
            i -= 1
        lat = float(self.lat_g[i])
        lon = float(self.long_g_gps[i] if self.long_g_gps is not None else self.long_g[i])
        return lat, lon, float(np.hypot(lat, lon))

    @property
    def has_data(self) -> bool:
        return len(self.times) > 0


def _empty() -> GMeter:
    z = np.empty(0)
    return GMeter(times=z, lat_g=z.copy(), long_g=z.copy(), cross=None)


def _gps_derived_g(gt, gx, gy, gspeed):
    """GPS-derived signed (longitudinal_g, lateral_g) and the per-sample forward/left unit
    vectors in ENU, plus a `moving` mask. Robust to GPS glitches: positions are median-filtered
    then boxcar-smoothed before differencing; speed is taken from the GPS-reported value (clean)
    and only its time-derivative is used for longitudinal g; lateral g = v*yaw_rate. Spikes are
    clipped to a sane karting envelope so a lone glitch can't dominate the cross-check."""
    n = len(gt)
    dt = np.gradient(gt)
    dt[dt <= 0] = np.median(dt[dt > 0]) if np.any(dt > 0) else 1.0

    def medfilt(a, k=5):
        # Edge-shrinking running median: interior via sliding_window_view; the h windows at each end shrink.
        h = k // 2
        if n < k:  # too short for any full window — every window shrinks; do them all directly
            return np.array([np.median(a[max(0, i - h):min(n, i + h + 1)]) for i in range(n)])
        out = np.empty_like(a)
        out[h:n - h] = np.median(np.lib.stride_tricks.sliding_window_view(a, k), axis=1)
        for i in range(h):
            out[i] = np.median(a[:i + h + 1])          # left edge: window clipped at 0
            out[n - 1 - i] = np.median(a[n - 1 - i - h:])  # right edge: window clipped at n
        return out

    xs = boxcar(medfilt(gx), 11)
    ys = boxcar(medfilt(gy), 11)
    vx = np.gradient(xs) / dt
    vy = np.gradient(ys) / dt
    vmag = np.hypot(vx, vy)
    fwd = np.column_stack([vx, vy]) / np.maximum(vmag, 1e-6)[:, None]
    left = np.column_stack([-fwd[:, 1], fwd[:, 0]])
    psi = np.unwrap(np.arctan2(vy, vx))
    yaw_rate = np.gradient(boxcar(psi, 11)) / dt
    spd = boxcar(gspeed, 9)
    long_g = np.clip(np.gradient(spd) / dt / G, -2.0, 2.0)
    lat_g = np.clip(spd * yaw_rate / G, -3.0, 3.0)
    moving = spd > _MOVING_MS
    return long_g, lat_g, fwd, left, moving


def compute(accl, grav, cori, gps_t, gps_x, gps_y, gps_speed, segment_bounds=None):
    """Build the vehicle-frame g series from the raw IMU + GPS trajectory.

    Inputs (all numpy arrays on the MEDIA clock):
      accl: (Na,4) [t, x, y, z]  accelerometer m/s^2 (native ACCL element order)
      grav: (Ng,4) [t, x, y, z]  gravity unit vector (native GRAV element order)
      cori: (Nc,5) [t, w, x, y, z] camera-orientation quaternion
      gps_t, gps_x, gps_y: GPS trajectory time + local-metre east/north (the smoothed track)
      gps_speed: GPS speed (m/s) aligned to gps_t
      segment_bounds: optional list of (t_start, t_end) spans, one per chapter. CORI's world yaw
        resets each chapter, so the CORI-plane->ENU alignment MUST be fit independently per
        chapter; None = a single global fit.

    Returns a GMeter. If the IMU is missing or the cross-check shows the ACCL is unusable, the
    GPS-derived g is used instead (source/cross say so) — we never ship a garbage meter.
    """
    gps_t = np.asarray(gps_t, float)
    if len(gps_t) >= 4:
        long_gps, lat_gps, fwd, left, moving = _gps_derived_g(
            gps_t, np.asarray(gps_x, float), np.asarray(gps_y, float),
            np.asarray(gps_speed, float))
    else:
        long_gps = lat_gps = fwd = left = moving = None

    have_imu = (accl is not None and len(accl) > 10
                and grav is not None and len(grav) > 4
                and cori is not None and len(cori) > 4)

    if not have_imu:
        # No IMU (e.g. an older GoPro): fall back to the GPS-derived g as the live signal.
        if long_gps is None:
            return _empty()
        return _resample_gps_only(gps_t, long_gps, lat_gps)

    accl = np.asarray(accl, float)
    grav = np.asarray(grav, float)
    cori = np.asarray(cori, float)
    ta = accl[:, 0]

    h1, h2 = _horizontal_accel(accl, grav, cori, ta)

    # Per-chapter alignment: CORI's world yaw resets each chapter, so fit the CORI-plane->ENU
    # rotation independently on each segment, then stitch the aligned g back together.
    if segment_bounds is None:
        segment_bounds = [(ta[0], ta[-1] + 1.0)]

    long_g = np.zeros_like(ta)
    lat_g = np.zeros_like(ta)
    crosses = []
    fwd_a = left_a = None
    if fwd is not None:
        fwd_a = np.column_stack([np.interp(ta, gps_t, fwd[:, 0]),
                                 np.interp(ta, gps_t, fwd[:, 1])])
        left_a = np.column_stack([-fwd_a[:, 1], fwd_a[:, 0]])

    for (t0, t1) in segment_bounds:
        seg = (ta >= t0) & (ta < t1)
        if not np.any(seg):
            continue
        R, reflect, cross = (np.eye(2), False, None)
        have_ref = long_gps is not None and moving is not None
        seg_g = ((gps_t >= t0) & (gps_t < t1)) if have_ref else None
        if have_ref and np.any(moving & seg_g):
            R, reflect, cross = _fit_segment(
                ta[seg], h1[seg], h2[seg], gps_t[seg_g],
                long_gps[seg_g], lat_gps[seg_g], fwd[seg_g], left[seg_g],
                moving[seg_g])
        P = np.column_stack([h1[seg], h2[seg]]) / G
        if reflect:
            P = np.column_stack([P[:, 0], -P[:, 1]])
        P_enu = P @ R.T
        # Undo the CORI gyro's yaw drift WITHIN this chapter (see the _YAW_* constants). Without
        # this the single fit above is only right near the drift's midpoint; the first and last
        # laps come out rotated by 60-110 deg, which is what shrank the lateral g the driver reads
        # by cos(error) and eventually inverted it.
        if cross is not None:
            drift = _yaw_drift_correction(
                ta[seg], P_enu, gps_t[seg_g], long_gps[seg_g], lat_gps[seg_g],
                fwd[seg_g], left[seg_g], moving[seg_g])
            if drift is not None:
                P_enu = _rotate(P_enu, drift)
                # Report the yaw this chapter actually used at its midpoint, drift included.
                cross.align_yaw_deg += float(np.degrees(drift[len(drift) // 2]))
        if fwd_a is not None:
            long_g[seg] = np.sum(P_enu * fwd_a[seg], axis=1)
            lat_g[seg] = np.sum(P_enu * left_a[seg], axis=1)
        else:
            long_g[seg], lat_g[seg] = P_enu[:, 0], P_enu[:, 1]
        # Re-derive the trust verdict from the CORRECTED series: the pre-correction correlation was
        # averaged over a chapter whose ends were badly rotated, so it understated a good mount and
        # its reported rms described a signal nobody ever sees.
        if cross is not None and fwd_a is not None:
            fixed = _cross_check(
                long_g[seg], lat_g[seg],
                np.interp(ta[seg], gps_t, long_gps), np.interp(ta[seg], gps_t, lat_gps),
                np.interp(ta[seg], gps_t, moving.astype(float)) > 0.5,
                cross.align_yaw_deg, reflect)
            cross = fixed if fixed is not None else cross
        if cross is not None:
            crosses.append(cross)

    cross = _merge_crosses(crosses)
    times, lat_g, long_g = _resample(ta, lat_g, long_g)

    use_gps = cross is not None and not cross.ok and long_gps is not None
    if use_gps:
        gm = _resample_gps_only(gps_t, long_gps, lat_gps)
        gm.cross = cross
        gm.source = "gps"
        return gm
    # The dial/overlay longitudinal: the GPS speed derivative on the output grid, smoothed (the IMU
    # forward axis is vibration-inflated). Lateral keeps the IMU. None if there's no GPS trajectory.
    long_g_gps = None
    if len(gps_t) >= 4:
        spd_kmh = np.interp(times, gps_t, np.asarray(gps_speed, float) * 3.6)
        w = max(int(round(_DIAL_LONG_SMOOTH_S * _OUTPUT_HZ)), 1)
        long_g_gps = boxcar(speed_long_g(spd_kmh, times), w)
    return GMeter(times=times, lat_g=lat_g, long_g=long_g, cross=cross, source="accl",
                  long_g_gps=long_g_gps)


def _horizontal_accel(accl, grav, cori, ta):
    """ACCL -> linear (gravity removed) -> CORI-world -> horizontal plane. Returns the two
    in-plane components (h1,h2) in m/s^2 at the ACCL times `ta` (lightly low-passed). The plane
    is perpendicular to the constant world-gravity direction; its in-plane yaw is arbitrary
    (resolved per-chapter against GPS by the caller)."""
    A = accl[:, 1:4]
    # gravity unit vector in the ACCL frame (GRAV permuted onto ACCL's axes)
    gperm = np.column_stack([np.interp(ta, grav[:, 0], grav[:, 1 + _PERM[i]]) for i in range(3)])
    gperm = _norm_rows(gperm)
    lin = A - G * gperm                                  # linear (gravity-removed) accel
    lin_p = np.column_stack([lin[:, _PERM[i]] for i in range(3)])    # to CORI axis order
    g_p = gperm[:, list(_PERM)]                                       # gravity in CORI axis order

    qw = np.interp(ta, cori[:, 0], cori[:, 1])
    qx = np.interp(ta, cori[:, 0], cori[:, 2])
    qy = np.interp(ta, cori[:, 0], cori[:, 3])
    qz = np.interp(ta, cori[:, 0], cori[:, 4])
    qn = np.sqrt(qw**2 + qx**2 + qy**2 + qz**2)
    qn[qn == 0] = 1.0
    qw, qx, qy, qz = qw / qn, qx / qn, qy / qn, qz / qn
    if _CORI_CONJUGATE:
        qx, qy, qz = -qx, -qy, -qz

    lin_world = _quat_rotate_world(qw, qx, qy, qz, lin_p)
    g_world = _quat_rotate_world(qw, qx, qy, qz, g_p)
    gdir = g_world.mean(axis=0)
    gdir = gdir / np.linalg.norm(gdir)                   # constant world-down (validated)

    horiz = lin_world - (lin_world @ gdir)[:, None] * gdir
    e1 = np.cross(gdir, [1.0, 0.0, 0.0])
    if np.linalg.norm(e1) < 1e-3:
        e1 = np.cross(gdir, [0.0, 1.0, 0.0])
    e1 = e1 / np.linalg.norm(e1)
    e2 = np.cross(gdir, e1)
    e2 = e2 / np.linalg.norm(e2)
    lp_w = max(int(_LOWPASS_S * len(ta) / max(ta[-1] - ta[0], 1e-6)), 1)
    return boxcar(horiz @ e1, lp_w), boxcar(horiz @ e2, lp_w)


def _fit_segment(ta, h1, h2, gps_t, long_gps, lat_gps, fwd, left, moving):
    """Fit one CORI-plane->ENU rotation (+ optional handedness flip) via Procrustes on the
    moving samples of a single chapter, and build that chapter's cross-check. Returns
    (R, reflect, CrossCheck|None)."""
    h1g = np.interp(gps_t, ta, h1) / G
    h2g = np.interp(gps_t, ta, h2) / G
    a_gps_world = long_gps[:, None] * fwd + lat_gps[:, None] * left  # ENU (g)
    m = moving & np.isfinite(h1g) & np.isfinite(h2g)
    if np.sum(m) < 10:
        return np.eye(2), False, None
    P = np.column_stack([h1g, h2g])

    best = None
    for s in (1, -1):
        Ps = np.column_stack([P[m, 0], s * P[m, 1]])
        H = Ps.T @ a_gps_world[m]
        U, _, Vt = np.linalg.svd(H)
        R = Vt.T @ U.T
        pred = Ps @ R.T
        along = np.sum(pred * fwd[m], axis=1)
        lat = np.sum(pred * left[m], axis=1)
        ca = _corr(along, long_gps[m])
        cl = _corr(lat, lat_gps[m])
        score = ca + cl
        if best is None or score > best[0]:
            best = (score, s, R, ca, cl, along, lat)

    _, s, R, ca, cl, along, lat = best
    reflect = (s == -1)
    yaw = float(np.degrees(np.arctan2(R[1, 0], R[0, 0])))
    # Trust heuristic: lateral is the discriminating channel (clear correlation = a real mount,
    # weak = head/vibration-dominated).
    ok = bool(cl >= 0.4 and np.isfinite(cl))
    cross = CrossCheck(
        n=int(np.sum(m)), lat_corr=float(cl), long_corr=float(ca),
        lat_rms_accl=float(np.sqrt(np.mean(lat**2))),
        lat_rms_gps=float(np.sqrt(np.mean(lat_gps[m]**2))),
        long_rms_accl=float(np.sqrt(np.mean(along**2))),
        long_rms_gps=float(np.sqrt(np.mean(long_gps[m]**2))),
        align_yaw_deg=yaw, align_reflect=reflect, ok=ok)
    return R, reflect, cross


def _fit_yaw(A, B, weights):
    """Best-fit rotation angle (radians) taking the 2-D vectors `A` onto `B`, sample-weighted.

    Weighted Procrustes restricted to a pure rotation — the mount is rigid, so only the angle is
    free (a free scale would silently absorb a genuinely mis-scaled axis). Weighting exists because
    the IMU's FORWARD axis is vibration-dominated garbage (r~0.1-0.3): left unweighted its noise
    dominates the least squares and drags the angle off the true mount yaw."""
    wa = A * weights[:, None]
    h = wa.T @ (B * weights[:, None])
    u, _s, vt = np.linalg.svd(h)
    r = vt.T @ u.T
    return float(np.arctan2(r[1, 0], r[0, 0]))


def _yaw_drift_correction(ta, p_enu, gps_t, long_gps, lat_gps, fwd, left, moving):
    """Per-sample residual yaw (radians) undoing the CORI gyro drift across one chapter.

    Fits the leftover rotation between the chapter-aligned IMU vector and the GPS-derived one in
    overlapping windows (see _YAW_WIN_S), then interpolates the unwrapped angle onto `ta`. Windows
    without enough moving samples or without real cornering are dropped — their angle comes from
    the neighbours the interpolation spans. Returns None when too few windows survive, in which
    case the caller keeps the single whole-chapter fit.

    Note the interpolation is over TIME, not sample index, so a GPS gap can't skew the ramp."""
    if len(gps_t) < 2:
        return None
    a_imu = np.column_stack([np.interp(gps_t, ta, p_enu[:, 0]),
                             np.interp(gps_t, ta, p_enu[:, 1])])
    a_gps = long_gps[:, None] * fwd + lat_gps[:, None] * left
    centres, angles = [], []
    t_first, t_last = float(gps_t[0]), float(gps_t[-1])
    starts = np.arange(t_first, max(t_last - _YAW_WIN_S, t_first) + _YAW_HOP_S, _YAW_HOP_S)
    for t0 in starts:
        win = moving & (gps_t >= t0) & (gps_t < t0 + _YAW_WIN_S)
        n = int(np.sum(win))
        if n < _YAW_MIN_SAMPLES:
            continue
        lat_w = lat_gps[win]
        if float(np.sqrt(np.mean(lat_w**2))) < _YAW_MIN_LAT_RMS:
            continue          # a straight/parade stint pins nothing down
        b = a_gps[win] * np.abs(lat_w)[:, None]
        sv = np.linalg.svd(b, compute_uv=False)
        if sv[0] <= 0 or sv[1] / sv[0] < _YAW_MIN_COND:
            continue          # near-collinear: no rotation is determined (see _YAW_MIN_COND)
        centres.append(float(np.mean(gps_t[win])))
        angles.append(_fit_yaw(a_imu[win], a_gps[win], np.abs(lat_w)))
    if len(centres) < _YAW_MIN_WINDOWS:
        return None
    order = np.argsort(centres)
    centres = np.asarray(centres)[order]
    # Unwrap before interpolating: consecutive fits can straddle +-pi mid-drift, and a naive
    # interpolation across that seam would sweep the correction the wrong way round the circle.
    angles = np.unwrap(np.asarray(angles)[order])
    theta = np.interp(ta, centres, angles)
    # Linear EXTRAPOLATION past the first/last window CENTRE. np.interp clamps there, which would
    # freeze the correction across the half-window at each end of the chapter — i.e. the first and
    # last laps, precisely where the accumulated drift is largest (measured: it left ~20 deg on the
    # table). The drift is a gyro bias integrating, so continuing the end slope is the right model.
    for mask, i0, i1 in ((ta < centres[0], 0, 1), (ta > centres[-1], -1, -2)):
        if not np.any(mask):
            continue
        span = centres[i0] - centres[i1]
        if abs(span) < 1e-9:
            continue
        slope = (angles[i0] - angles[i1]) / span
        theta[mask] = angles[i0] + slope * (ta[mask] - centres[i0])
    return theta


def _rotate(p, theta):
    """Rotate each row of the (n,2) array `p` by its own angle `theta[i]` (radians)."""
    c, s = np.cos(theta), np.sin(theta)
    return np.column_stack([c * p[:, 0] - s * p[:, 1], s * p[:, 0] + c * p[:, 1]])


def _cross_check(along, lat, long_gps, lat_gps, moving, yaw_deg, reflect):
    """The IMU-vs-GPS trust verdict, built from the FINAL aligned series (post drift correction) so
    the reported correlations describe the g the app actually shows."""
    m = moving & np.isfinite(along) & np.isfinite(lat)
    if int(np.sum(m)) < 10:
        return None
    cl = _corr(lat[m], lat_gps[m])
    ca = _corr(along[m], long_gps[m])
    return CrossCheck(
        n=int(np.sum(m)), lat_corr=float(cl), long_corr=float(ca),
        lat_rms_accl=float(np.sqrt(np.mean(lat[m]**2))),
        lat_rms_gps=float(np.sqrt(np.mean(lat_gps[m]**2))),
        long_rms_accl=float(np.sqrt(np.mean(along[m]**2))),
        long_rms_gps=float(np.sqrt(np.mean(long_gps[m]**2))),
        align_yaw_deg=float(yaw_deg), align_reflect=bool(reflect),
        ok=bool(cl >= 0.4 and np.isfinite(cl)))


def _merge_crosses(crosses):
    """Combine per-chapter cross-checks (sample-count-weighted correlations + RMS). `ok` keys off
    the whole recording's weighted lateral correlation so one weak chapter can't condemn it."""
    crosses = [c for c in crosses if c is not None and c.n > 0]
    if not crosses:
        return None
    n = sum(c.n for c in crosses)
    wl = sum(c.lat_corr * c.n for c in crosses) / n
    wa = sum(c.long_corr * c.n for c in crosses) / n
    def wrms(attr):
        return float(np.sqrt(sum(getattr(c, attr)**2 * c.n for c in crosses) / n))
    # report the first chapter's alignment as representative (per-chapter yaw differs)
    return CrossCheck(
        n=n, lat_corr=float(wl), long_corr=float(wa),
        lat_rms_accl=wrms("lat_rms_accl"), lat_rms_gps=wrms("lat_rms_gps"),
        long_rms_accl=wrms("long_rms_accl"), long_rms_gps=wrms("long_rms_gps"),
        align_yaw_deg=crosses[0].align_yaw_deg, align_reflect=crosses[0].align_reflect,
        ok=bool(wl >= 0.4))


def _corr(a, b):
    if len(a) < 2 or np.std(a) < 1e-9 or np.std(b) < 1e-9:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _resample(t, lat_g, long_g):
    """Resample a per-sample g signal to the uniform output rate, on the same media clock."""
    t = np.asarray(t, float)
    if len(t) < 2:
        return t, np.asarray(lat_g, float), np.asarray(long_g, float)
    out_t = np.arange(t[0], t[-1], 1.0 / _OUTPUT_HZ)
    return out_t, np.interp(out_t, t, lat_g), np.interp(out_t, t, long_g)


def _resample_gps_only(gps_t, long_gps, lat_gps):
    """Build a GMeter from the GPS-derived g alone (IMU absent or rejected)."""
    t, lat_g, long_g = _resample(gps_t, lat_gps, long_gps)
    return GMeter(times=t, lat_g=lat_g, long_g=long_g, cross=None, source="gps")
