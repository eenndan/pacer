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

from ._signal import boxcar

G = 9.80665  # m/s^2

# Empirically resolved GoPro stream-frame conventions (see module docstring + validation doc).
# GRAV/CORI element order is a permutation of ACCL's native (Z,X,Y) element order.
_PERM = (1, 0, 2)
# CORI stores world->camera; conjugate it to rotate camera->world.
_CORI_CONJUGATE = True

_OUTPUT_HZ = 50.0    # output rate; g is band-limited well below ACCL 200Hz
_LOWPASS_S = 0.15    # pre-output low-pass (s): kills road buzz without lagging corners/brakes
_MOVING_MS = 4.0     # m/s; heading is ill-defined at a standstill (used for fit + cross-check)


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

    def __len__(self) -> int:
        return len(self.times)

    def at_time(self, t: float) -> tuple[float, float, float] | None:
        """(lateral_g, longitudinal_g, total_g) at media time `t`, or None if no g series.
        O(log n) — a `searchsorted` plus nearest pick; called at the 30 Hz tick."""
        n = len(self.times)
        if n == 0:
            return None
        i = int(np.searchsorted(self.times, t))
        i = min(max(i, 0), n - 1)
        # nearest of the two bracketing samples (the series is dense, so this is plenty)
        if 0 < i < n and abs(self.times[i - 1] - t) < abs(self.times[i] - t):
            i -= 1
        lat = float(self.lat_g[i])
        lon = float(self.long_g[i])
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
        if long_gps is not None and moving is not None:
            seg_g = (gps_t >= t0) & (gps_t < t1)
            if np.any(moving & seg_g):
                R, reflect, cross = _fit_segment(
                    ta[seg], h1[seg], h2[seg], gps_t[seg_g],
                    long_gps[seg_g], lat_gps[seg_g], fwd[seg_g], left[seg_g],
                    moving[seg_g])
                if cross is not None:
                    crosses.append(cross)
        P = np.column_stack([h1[seg], h2[seg]]) / G
        if reflect:
            P = np.column_stack([P[:, 0], -P[:, 1]])
        P_enu = P @ R.T
        if fwd_a is not None:
            long_g[seg] = np.sum(P_enu * fwd_a[seg], axis=1)
            lat_g[seg] = np.sum(P_enu * left_a[seg], axis=1)
        else:
            long_g[seg], lat_g[seg] = P_enu[:, 0], P_enu[:, 1]

    cross = _merge_crosses(crosses)
    times, lat_g, long_g = _resample(ta, lat_g, long_g)

    use_gps = cross is not None and not cross.ok and long_gps is not None
    if use_gps:
        gm = _resample_gps_only(gps_t, long_gps, lat_gps)
        gm.cross = cross
        gm.source = "gps"
        return gm
    return GMeter(times=times, lat_g=lat_g, long_g=long_g, cross=cross, source="accl")


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
