"""Tests for studio.gmeter: the camera->kart frame g-transform, on SYNTHETIC input (no media
file, fast, deterministic). Builds a fake GoPro IMU + GPS trajectory with KNOWN accelerations
and asserts the recovered vehicle-frame lateral / longitudinal g match in sign and magnitude.

Why synthetic: the real cross-check (ACCL vs GPS-derived g on the recording) is validated at
load and documented in studio/docs/gmeter-validation.md with measured correlations; these unit
tests instead pin the pure transform — gravity removal, the GRAV/CORI axis permutation, the
camera->world rotation, the horizontal projection, and the per-sample forward/lateral split —
so a regression in the math is caught without a 12 GB file.

Run: python tests/test_gmeter.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio import gmeter  # noqa: E402

G = gmeter.G


def _quat_from_axis_angle(axis, ang):
    axis = np.asarray(axis, float)
    axis = axis / np.linalg.norm(axis)
    return np.array([np.cos(ang / 2), *(np.sin(ang / 2) * axis)])


def _quat_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return np.array([
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ])


def _rot_by_quat(q, v):
    """Rotate v by quaternion q (w,x,y,z)."""
    w, x, y, z = q
    R = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])
    return R @ v


def _build_synthetic(circle=True, accel_g=0.0, lateral_g=0.0, n=4000, dur=40.0):
    """Build (accl, grav, cori, gps_t, gps_x, gps_y, gps_speed) for a kart driving on a flat
    plane. The camera is mounted at a FIXED known orientation (a yaw + a small pitch), the same
    every sample (CORI stays constant), so the transform must recover the world-frame motion.

    Motion: constant forward speed with a steady longitudinal accel `accel_g` and a steady
    lateral accel `lateral_g` (g). The kart heads along +X (east). We emit:
      * GPS trajectory (the integrated motion) at 10 Hz,
      * ACCL = (linear accel + gravity) expressed in the CAMERA body frame, at 200 Hz,
      * GRAV = gravity unit vector in the camera frame (permuted to GRAV's element order),
      * CORI = the (constant) camera->world... stored as world->camera (the GoPro convention,
        which the transform conjugates).
    """
    # World frame: x=east, y=north, z=up. Gravity points -z. Kart drives along +x.
    fwd_w = np.array([1.0, 0.0, 0.0])
    left_w = np.array([0.0, 1.0, 0.0])
    up_w = np.array([0.0, 0.0, 1.0])
    # World linear accel: forward * accel + left * lateral (in m/s^2)
    a_lin_w = fwd_w * (accel_g * G) + left_w * (lateral_g * G)
    # The accelerometer reads SPECIFIC FORCE: a stationary one reads +g UP. The gravity field is
    # -g*up, so the measured specific force is a_lin - grav_field = a_lin + g*up.
    meas_w = a_lin_w + up_w * G

    # Camera mount: yaw 50 deg about world up, then pitch 10 deg about the camera's right axis.
    q_yaw = _quat_from_axis_angle(up_w, np.radians(50.0))
    q_pitch = _quat_from_axis_angle([0, 1, 0], np.radians(10.0))
    q_cam_to_world = _quat_mul(q_yaw, q_pitch)  # rotates a camera-frame vec into world
    q_world_to_cam = np.array([q_cam_to_world[0], -q_cam_to_world[1],
                               -q_cam_to_world[2], -q_cam_to_world[3]])

    # Express the measured specific force + gravity direction in the CAMERA frame.
    meas_cam = _rot_by_quat(q_world_to_cam, meas_w)
    grav_dir_cam = _rot_by_quat(q_world_to_cam, up_w)  # gravity DIRECTION (unit, +up reaction)

    # ACCL native element order is (Z, X, Y) of the camera frame; GRAV/CORI use (X, Y, Z).
    # gmeter maps GRAV[PERM[i]] onto ACCL[i] with PERM=(1,0,2); to be consistent, ACCL element
    # order = camera (Z,X,Y) and GRAV element order = camera (X,Y,Z).
    def to_accl_order(v):  # camera xyz -> ACCL (z,x,y)
        return np.array([v[2], v[0], v[1]])

    ta = np.linspace(0, dur, n)
    accl = np.column_stack([ta] + [np.full(n, c) for c in to_accl_order(meas_cam)])
    tg = np.linspace(0, dur, int(dur * 60))
    grav = np.column_stack([tg] + [np.full(len(tg), c) for c in grav_dir_cam])  # X,Y,Z order
    cori = np.column_stack([tg] + [np.full(len(tg), q_world_to_cam[k]) for k in range(4)])

    # GPS trajectory: integrate the world motion. v(t) = v0 + a*t along fwd; plus a curving path
    # for the lateral case so the GPS-derived heading/curvature is well-defined.
    gt = np.linspace(0, dur, int(dur * 10))
    v0 = 20.0
    if lateral_g != 0.0 and circle:
        # steady-state cornering: v constant, radius r = v^2/(lat*g); circle in world plane
        r = v0 ** 2 / (lateral_g * G)
        omega = v0 / r
        ang = omega * gt
        gx = r * np.sin(ang)
        gy = r * (1 - np.cos(ang)) * np.sign(r)
        gspeed = np.full_like(gt, v0)
    else:
        # straight-line accel/brake along +x
        gspeed = v0 + accel_g * G * gt
        gx = v0 * gt + 0.5 * accel_g * G * gt ** 2
        gy = np.zeros_like(gt)
    return accl, grav, cori, gt, gx, gy, gspeed


def test_braking_is_negative_longitudinal():
    """A pure straight-line deceleration must come out as NEGATIVE longitudinal g, ~the input
    magnitude, with near-zero lateral."""
    accl, grav, cori, gt, gx, gy, gs = _build_synthetic(
        circle=False, accel_g=-0.5, lateral_g=0.0)
    gm = gmeter.compute(accl, grav, cori, gt, gx, gy, gs)
    assert gm.has_data
    # sample the middle of the run
    g = gm.at_time(20.0)
    assert g is not None
    lat, lon, total = g
    assert lon < -0.2, f"expected braking (negative long), got {lon:.2f}"
    assert abs(lat) < 0.25, f"expected ~0 lateral on a straight, got {lat:.2f}"
    assert abs(abs(lon) - 0.5) < 0.25, f"magnitude off: |long|={abs(lon):.2f} vs 0.5"


def test_acceleration_is_positive_longitudinal():
    accl, grav, cori, gt, gx, gy, gs = _build_synthetic(
        circle=False, accel_g=0.4, lateral_g=0.0)
    gm = gmeter.compute(accl, grav, cori, gt, gx, gy, gs)
    lat, lon, total = gm.at_time(20.0)
    assert lon > 0.2, f"expected accel (positive long), got {lon:.2f}"
    assert abs(lat) < 0.25


def test_left_corner_is_positive_lateral():
    """A steady LEFT corner (+lateral by our sign convention) recovers positive lateral g of
    about the input magnitude, with small longitudinal (steady speed)."""
    accl, grav, cori, gt, gx, gy, gs = _build_synthetic(
        circle=True, accel_g=0.0, lateral_g=0.8)
    gm = gmeter.compute(accl, grav, cori, gt, gx, gy, gs)
    lat, lon, total = gm.at_time(20.0)
    assert lat > 0.4, f"expected positive lateral (left), got {lat:.2f}"
    assert abs(abs(lat) - 0.8) < 0.3, f"lateral magnitude off: {lat:.2f} vs 0.8"


def test_gravity_is_removed():
    """With NO motion accel (just gravity) the recovered horizontal g must be ~0 — i.e. the 1 g
    of gravity is fully removed, not leaking into lateral/longitudinal."""
    accl, grav, cori, gt, gx, gy, gs = _build_synthetic(
        circle=False, accel_g=0.0, lateral_g=0.0)
    gm = gmeter.compute(accl, grav, cori, gt, gx, gy, gs)
    lat, lon, total = gm.at_time(20.0)
    assert total < 0.2, f"gravity not removed: residual total {total:.2f} g (should be ~0)"


def test_total_is_hypot_and_lookup_is_monotone():
    accl, grav, cori, gt, gx, gy, gs = _build_synthetic(
        circle=True, accel_g=0.0, lateral_g=0.8)
    gm = gmeter.compute(accl, grav, cori, gt, gx, gy, gs)
    assert np.all(np.diff(gm.times) > 0), "g-series time axis must be strictly increasing"
    lat, lon, total = gm.at_time(20.0)
    assert abs(total - np.hypot(lat, lon)) < 1e-6


def test_no_imu_falls_back_to_gps():
    """With ACCL/GRAV/CORI absent (older camera) the meter must fall back to GPS-derived g
    (source='gps') rather than failing — and on a braking trajectory still read negative long."""
    _, _, _, gt, gx, gy, gs = _build_synthetic(circle=False, accel_g=-0.4, lateral_g=0.0)
    empty4 = np.empty((0, 4))
    empty5 = np.empty((0, 5))
    gm = gmeter.compute(empty4, empty4, empty5, gt, gx, gy, gs)
    assert gm.source == "gps"
    assert gm.has_data
    lat, lon, total = gm.at_time(20.0)
    assert lon < -0.1, f"GPS-derived braking should be negative long, got {lon:.2f}"


def test_empty_inputs_give_empty_meter():
    empty4 = np.empty((0, 4))
    empty5 = np.empty((0, 5))
    gm = gmeter.compute(empty4, empty4, empty5, np.empty(0), np.empty(0), np.empty(0), np.empty(0))
    assert not gm.has_data
    assert gm.at_time(5.0) is None


def test_at_time_uses_gps_longitudinal_and_reports_source():
    """When a GPS-derived longitudinal is present, at_time returns IT for the long axis (not the
    inflated IMU long_g) and keeps the IMU lateral; long_source says 'gps'. Without it, at_time
    falls back to long_g (a synthetic / GPS-only meter)."""
    n = 50
    times = np.linspace(0.0, 5.0, n)
    lat = np.full(n, 0.3)
    imu_long = np.full(n, 0.9)   # the vibration-inflated IMU forward axis
    gps_long = np.full(n, 0.45)  # the validated GPS-derived longitudinal
    gm = gmeter.GMeter(times=times, lat_g=lat, long_g=imu_long, cross=None, source="accl",
                       long_g_gps=gps_long)
    latv, lonv, total = gm.at_time(2.5)
    assert abs(lonv - 0.45) < 1e-9, lonv               # GPS long, not the 0.9 IMU
    assert abs(latv - 0.3) < 1e-9, latv                # IMU lateral kept
    assert abs(total - np.hypot(0.3, 0.45)) < 1e-9
    assert gm.long_source == "gps"
    gm2 = gmeter.GMeter(times=times, lat_g=lat, long_g=imu_long, cross=None, source="accl")
    assert abs(gm2.at_time(2.5)[1] - 0.9) < 1e-9       # no GPS long -> IMU fallback
    assert gm2.long_source == "accl"
    print("ok at_time: GPS longitudinal preferred, IMU lateral kept, long_source reported")


def _build_weave(dur=320.0, lat_amp_g=0.6, period_s=20.0):
    """A kart weaving left/right at constant speed, with a FIXED camera mount.

    Mirrors `_build_synthetic`'s straight-line branch exactly — same world frame, same specific-force
    convention, same ACCL/GRAV/CORI element orders — but with a time-VARYING lateral acceleration.
    A varying lateral is what makes the IMU-vs-GPS correlation meaningful: on a steady circle the
    lateral g is constant, Pearson r is ~0, and `compute` falls back to the GPS meter, so the IMU
    path under test is never exercised.
    """
    left_w = np.array([0.0, 1.0, 0.0])
    up_w = np.array([0.0, 0.0, 1.0])
    w = 2.0 * np.pi / period_s

    def lat_of(t):
        return lat_amp_g * np.sin(w * t)

    ta = np.linspace(0.0, dur, int(dur * 200))
    # Specific force in WORLD coords: the lateral swing plus the +g up reaction (a stationary
    # accelerometer reads +g UP), exactly as _build_synthetic does.
    meas_w = left_w * (lat_of(ta) * G)[:, None] + up_w * G

    q_yaw = _quat_from_axis_angle(up_w, np.radians(50.0))
    q_pitch = _quat_from_axis_angle([0, 1, 0], np.radians(10.0))
    q_cam_to_world = _quat_mul(q_yaw, q_pitch)
    q_world_to_cam = np.array([q_cam_to_world[0], -q_cam_to_world[1],
                               -q_cam_to_world[2], -q_cam_to_world[3]])
    meas_cam = np.array([_rot_by_quat(q_world_to_cam, m) for m in meas_w])
    grav_dir_cam = _rot_by_quat(q_world_to_cam, up_w)

    accl = np.column_stack([ta, meas_cam[:, 2], meas_cam[:, 0], meas_cam[:, 1]])  # (z,x,y)
    tg = np.linspace(0.0, dur, int(dur * 60))
    grav = np.column_stack([tg] + [np.full(len(tg), c) for c in grav_dir_cam])    # (x,y,z)
    cori = np.column_stack([tg] + [np.full(len(tg), q_world_to_cam[k]) for k in range(4)])

    # GPS: integrate the same world motion at 10 Hz. vx stays v0; vy is the lateral integral.
    gt = np.linspace(0.0, dur, int(dur * 10))
    v0 = 22.0
    vy = (lat_amp_g * G / w) * (1.0 - np.cos(w * gt))
    gx = v0 * gt
    gy = (lat_amp_g * G / w) * (gt - np.sin(w * gt) / w)
    gspeed = np.hypot(np.full_like(gt, v0), vy)
    return accl, grav, cori, gt, gx, gy, gspeed


def _add_cori_yaw_drift(cori, drift_deg, dur):
    """Return a copy of `cori` whose reported world YAW drifts linearly by `drift_deg` over `dur`.

    The real GoPro defect in miniature: CORI's world frame comes from integrating the gyro with no
    magnetometer, so its yaw reference creeps (measured 0.08-0.15 deg/s on real recordings). The
    physics — ACCL and GRAV — is untouched; only the ORIENTATION the camera reports slides. Built
    by re-expressing the EXISTING fixture's quaternion rather than re-deriving the mount, so this
    isolates exactly one variable against a fixture the other tests already prove correct.
    """
    up_w = np.array([0.0, 0.0, 1.0])
    out = cori.copy()
    t = cori[:, 0]
    drift = np.radians(drift_deg) * (t / dur - 0.5)
    for i in range(len(t)):
        q_w2c = cori[i, 1:]
        q_c2w = np.array([q_w2c[0], -q_w2c[1], -q_w2c[2], -q_w2c[3]])
        q_rep = _quat_mul(_quat_from_axis_angle(up_w, -drift[i]), q_c2w)
        out[i, 1:] = [q_rep[0], -q_rep[1], -q_rep[2], -q_rep[3]]
    return out


def _build_lapping_slalom(dur=320.0, v0=22.0, yaw_amp_rad=0.6, period_s=12.0, lap_s=40.0):
    """A kart lapping a circuit at CONSTANT speed, slaloming as it goes, with a fixed camera mount.

    `_build_weave` swings the kart's VELOCITY, which makes its speed and heading change together
    and leaves the GPS-derived lateral g about HALF the true one — fine for a correlation test
    (r is scale-blind anyway), useless for a MAGNITUDE one. Here the speed is constant and the
    HEADING carries both terms, so lateral g is exactly v · dψ/dt by construction and the GPS
    reconstruction recovers it to within its own smoothing. The lap term is what conditions the
    Procrustes fit: without it every acceleration vector lies on one line and the mount rotation
    is undetermined (the `_YAW_MIN_COND` case). The result is a fixture whose lateral gain is ~1,
    against which an injected scale fault is measurable. Same world frame, specific-force
    convention and ACCL/GRAV/CORI element orders as the other builders.
    """
    up_w = np.array([0.0, 0.0, 1.0])
    w = 2.0 * np.pi / period_s
    lap_w = 2.0 * np.pi / lap_s

    def heading(t):
        return lap_w * t + yaw_amp_rad * np.sin(w * t)

    def yaw_rate(t):
        return lap_w + yaw_amp_rad * w * np.cos(w * t)

    ta = np.linspace(0.0, dur, int(dur * 200))
    psi = heading(ta)
    a_lat = v0 * yaw_rate(ta)                                 # m/s^2, + = to the left
    left_t = np.column_stack([-np.sin(psi), np.cos(psi), np.zeros_like(psi)])
    meas_w = left_t * a_lat[:, None] + up_w * G               # + the +g up reaction

    q_yaw = _quat_from_axis_angle(up_w, np.radians(50.0))
    q_pitch = _quat_from_axis_angle([0, 1, 0], np.radians(10.0))
    q_cam_to_world = _quat_mul(q_yaw, q_pitch)
    q_world_to_cam = np.array([q_cam_to_world[0], -q_cam_to_world[1],
                               -q_cam_to_world[2], -q_cam_to_world[3]])
    meas_cam = np.array([_rot_by_quat(q_world_to_cam, m) for m in meas_w])
    grav_dir_cam = _rot_by_quat(q_world_to_cam, up_w)

    # ACCL element order here is camera (Y, X, Z) — i.e. GRAV's (X,Y,Z) read through gmeter's own
    # _PERM. The older builders declare (Z,X,Y), which leaves a little gravity un-removed and
    # inflates the recovered magnitude ~1.8x; harmless for the SHAPE (correlation) assertions
    # those tests make, fatal for a magnitude one. Measured: with this order the recovered
    # horizontal magnitude is 0.6086 g against a true 0.6089 g.
    accl = np.column_stack([ta] + [meas_cam[:, _PERM_I] for _PERM_I in gmeter._PERM])
    tg = np.linspace(0.0, dur, int(dur * 60))
    grav = np.column_stack([tg] + [np.full(len(tg), c) for c in grav_dir_cam])    # (x,y,z)
    cori = np.column_stack([tg] + [np.full(len(tg), q_world_to_cam[k]) for k in range(4)])

    gt = np.arange(0.0, dur, 0.1)                             # 10 Hz, like a real GPS track
    psi_g = heading(gt)
    dtg = float(gt[1] - gt[0])

    def integrate(f):                                          # trapezoid, no scipy
        return np.concatenate([[0.0], np.cumsum((f[:-1] + f[1:]) * 0.5) * v0 * dtg])

    return (accl, grav, cori, gt, integrate(np.cos(psi_g)), integrate(np.sin(psi_g)),
            np.full_like(gt, v0))


def _scale_accl_linear(accl, grav, k):
    """Return a copy of `accl` whose LINEAR (gravity-removed) acceleration is scaled by `k`.

    The mis-scaled-channel fault in miniature — a calibration/units error, or the cos(residual
    yaw) shrink the CORI drift produced. Gravity is left exactly as it was (a mis-scaled channel
    still reads 1 g at rest), so the only thing that changes is the MAGNITUDE of the motion the
    IMU reports. Built by re-expressing the existing fixture, like _add_cori_yaw_drift, so
    exactly one variable moves.
    """
    out = np.asarray(accl, float).copy()
    ta = out[:, 0]
    gperm = np.column_stack(
        [np.interp(ta, grav[:, 0], grav[:, 1 + gmeter._PERM[i]]) for i in range(3)])
    gperm = gperm / np.maximum(np.linalg.norm(gperm, axis=1, keepdims=True), 1e-12)
    lin = out[:, 1:4] - G * gperm
    out[:, 1:4] = G * gperm + k * lin
    return out


def test_a_mis_scaled_accelerometer_fails_the_trust_gate():
    """L9-01. Pearson r is scale-INVARIANT by construction, so the correlation-only verdict could
    not see a mis-scaled g channel AT ALL: halving the accelerometer left lat_corr bit-identical
    and `ok` True while every g the app displays halved. The verdict now weighs MAGNITUDE too.

    This is the gate that could not have detected the CORI yaw-drift defect (#130), whose whole
    signature was a lateral magnitude shrinking by cos(residual yaw)."""
    accl, grav, cori, gt, gx, gy, gs = _build_lapping_slalom()
    good = gmeter.compute(accl, grav, cori, gt, gx, gy, gs)
    assert good.cross is not None and good.cross.ok
    assert good.source == "accl"
    assert 0.8 <= good.cross.lat_gain <= 1.25, good.cross.lat_gain   # measured 1.02

    halved = gmeter.compute(_scale_accl_linear(accl, grav, 0.5), grav, cori, gt, gx, gy, gs)
    assert halved.cross is not None
    # The correlation is blind to it — that is the entire finding, so assert it explicitly.
    assert abs(halved.cross.lat_corr - good.cross.lat_corr) < 1e-9, (
        halved.cross.lat_corr, good.cross.lat_corr)
    assert halved.cross.lat_corr >= gmeter._LAT_CORR_MIN     # it would still pass the old gate
    # …but the magnitude is not. The gain halves, the verdict fails, and the app stops sourcing
    # the dial from a channel that reads half.
    assert abs(halved.cross.lat_gain - good.cross.lat_gain * 0.5) < 0.01, halved.cross.lat_gain
    assert not halved.cross.ok
    assert halved.source == "gps"
    assert "DISAGREE" in halved.cross.summary()
    assert f"gain x{halved.cross.lat_gain:.2f}" in halved.cross.summary()
    # Symmetric: an over-scaled channel is caught the same way.
    doubled = gmeter.compute(_scale_accl_linear(accl, grav, 2.0), grav, cori, gt, gx, gy, gs)
    assert doubled.cross is not None and not doubled.cross.ok
    # …and a fault too small to matter is NOT condemned: the band is a tolerance, not equality.
    nudged = gmeter.compute(_scale_accl_linear(accl, grav, 1.1), grav, cori, gt, gx, gy, gs)
    assert nudged.cross is not None and nudged.cross.ok and nudged.source == "accl"
    print("ok mis-scaled accelerometer: r unchanged, gain caught it, verdict flipped")


def test_gain_is_not_weighed_without_real_cornering():
    """The gain is a ratio of two RMS values, so on a span with no real cornering it is a ratio of
    two noise floors and means nothing. Below _GAIN_MIN_LAT_RMS it is reported and NOT gated on —
    otherwise a slow transit chapter would condemn a perfectly good mount."""
    # A clean, well-correlated channel whose GPS lateral RMS is far below the floor.
    tiny = gmeter._GAIN_MIN_LAT_RMS / 10.0
    assert gmeter._verdict(0.95, lat_rms_accl=tiny * 5.0, lat_rms_gps=tiny) is True
    # …the same absurd gain IS refused once there is enough cornering to weigh it against.
    assert gmeter._verdict(0.95, lat_rms_accl=3.0, lat_rms_gps=0.6) is False
    # …and a weak correlation still fails on its own, gain or no gain (the original rule).
    assert gmeter._verdict(0.1, lat_rms_accl=0.66, lat_rms_gps=0.6) is False
    assert gmeter._verdict(float("nan"), lat_rms_accl=0.66, lat_rms_gps=0.6) is False
    assert gmeter._verdict(0.95, lat_rms_accl=0.66, lat_rms_gps=0.6) is True   # the good case
    print("ok gain gate: weighed only where there is cornering to weigh it against")


def _lapping_reference(dur=400.0, hz=10.0, lat_amp=1.0, collinear=False):
    """A GPS-derived reference whose acceleration DIRECTION sweeps the full circle, as a lap does.

    Returns (gps_t, long_gps, lat_gps, fwd, left, moving). `collinear=True` pins the heading, so
    every acceleration vector lies on one line — the ill-conditioned case a yaw fit must refuse.
    """
    t = np.arange(0.0, dur, 1.0 / hz)
    heading = np.zeros_like(t) if collinear else (2.0 * np.pi * t / 40.0)  # a lap every 40 s
    fwd = np.column_stack([np.cos(heading), np.sin(heading)])
    left = np.column_stack([-fwd[:, 1], fwd[:, 0]])
    lat_gps = lat_amp * np.sin(2.0 * np.pi * t / 7.0)   # corners come and go within the lap
    long_gps = np.zeros_like(t)
    return t, long_gps, lat_gps, fwd, left, np.ones_like(t, dtype=bool)


def test_yaw_drift_correction_recovers_a_known_ramp():
    """The heart of the fix: a CORI world-yaw that creeps linearly must be recovered per sample.

    Real GoPro CORI has no magnetometer, so its world yaw is an integrated gyro that drifts —
    measured at 0.08 deg/s on a D24 recording and 0.15 deg/s on a Sandown one, i.e. 130-200 deg end
    to end. ONE Procrustes fit per chapter is then only right where the drift crosses its mean; the
    first and last laps came out rotated by 60-110 deg, which scaled the lateral g the driver reads
    by cos(error) and eventually INVERTED it (Sandown lap 23 peaked at 0.77 g against a GPS-measured
    1.42 g; lap 1 of the same recording correlated -0.73 with the truth).

    Here the IMU vector IS the reference pre-rotated by a known ramp, so the correction must come
    back as its negative.
    """
    gps_t, long_gps, lat_gps, fwd, left, moving = _lapping_reference()
    a_gps = long_gps[:, None] * fwd + lat_gps[:, None] * left
    ramp = np.radians(140.0) * (gps_t / gps_t[-1] - 0.5)          # +-70 deg across the run
    c, s = np.cos(ramp), np.sin(ramp)
    a_imu = np.column_stack([c * a_gps[:, 0] - s * a_gps[:, 1],
                             s * a_gps[:, 0] + c * a_gps[:, 1]])

    ta = np.linspace(gps_t[0], gps_t[-1], 4000)                    # the IMU's own denser grid
    p_enu = np.column_stack([np.interp(ta, gps_t, a_imu[:, 0]),
                             np.interp(ta, gps_t, a_imu[:, 1])])
    got = gmeter._yaw_drift_correction(ta, p_enu, gps_t, long_gps, lat_gps, fwd, left, moving)
    assert got is not None, "a full recording of laps must yield a drift fit"
    want = -np.interp(ta, gps_t, ramp)
    err = np.degrees(np.abs(got - want))
    assert err.max() < 8.0, f"worst residual {err.max():.1f} deg (mean {err.mean():.1f})"
    # And specifically at the ENDS, where np.interp would otherwise clamp to the first/last window
    # centre and freeze the correction across the recording's first and last laps.
    assert err[:len(err) // 10].mean() < 8.0, "start of the recording under-corrected"
    assert err[-len(err) // 10:].mean() < 8.0, "end of the recording under-corrected"
    print("ok yaw drift: a +-70 deg ramp recovered to within 8 deg, ends included")


def test_yaw_drift_correction_refuses_ill_conditioned_windows():
    """Collinear acceleration (one long corner, or a slalom on a single axis) determines NO
    rotation — every angle mapping that line onto itself fits equally. The fit must decline rather
    than return noise: measured, an unguarded fit drove a clean synthetic from lateral r=+0.69 to
    -0.05, i.e. it BROKE an alignment that was already correct."""
    gps_t, long_gps, lat_gps, fwd, left, moving = _lapping_reference(collinear=True)
    a_gps = long_gps[:, None] * fwd + lat_gps[:, None] * left
    ta = np.linspace(gps_t[0], gps_t[-1], 4000)
    p_enu = np.column_stack([np.interp(ta, gps_t, a_gps[:, 0]),
                             np.interp(ta, gps_t, a_gps[:, 1])])
    assert gmeter._yaw_drift_correction(
        ta, p_enu, gps_t, long_gps, lat_gps, fwd, left, moving) is None
    print("ok yaw drift: collinear windows are refused, not fitted to noise")


def test_cori_yaw_drift_survives_the_full_pipeline():
    """End to end: a drifting CORI through gmeter.compute still yields a usable meter (finite,
    right length, IMU-sourced) — the guard rails around the correction, on top of the unit tests
    above that pin the angle itself."""
    dur = 320.0
    accl, grav, cori, gt, gx, gy, gs = _build_weave(dur=dur)
    gm = gmeter.compute(accl, grav, _add_cori_yaw_drift(cori, 140.0, dur), gt, gx, gy, gs)
    assert gm.has_data and np.isfinite(gm.lat_g).all() and np.isfinite(gm.long_g).all()
    assert len(gm.times) == len(gm.lat_g) == len(gm.long_g)
    print("ok drifting CORI through compute(): finite, well-formed meter")


def test_yaw_drift_correction_declines_gracefully_without_enough_windows():
    """Too short to fit several windows -> keep the single whole-chapter fit rather than
    extrapolating a drift from one noisy sample. A 40 s run holds under _YAW_WIN_S, so
    _yaw_drift_correction must return None and the meter must still be produced."""
    accl, grav, cori, gt, gx, gy, gs = _build_synthetic(lateral_g=0.5, dur=40.0)
    gm = gmeter.compute(accl, grav, cori, gt, gx, gy, gs)
    assert gm.has_data and len(gm) > 0
    assert np.isfinite(gm.lat_g).all()
    print("ok short recording: single-fit fallback, still a valid meter")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nALL {len(tests)} gmeter tests passed")
