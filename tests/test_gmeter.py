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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nALL {len(tests)} gmeter tests passed")
