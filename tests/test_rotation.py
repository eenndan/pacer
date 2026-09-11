"""Tests for studio.rotation: the measured gyro yaw-rate channel, on SYNTHETIC input.

Builds a kart lapping a stadium loop (two straights, two 180-degree left arcs) with a KNOWN yaw
rate, and a camera bolted on at an arbitrary fixed tilt, then asserts the module recovers that
rate in sign AND magnitude, rejects rotation about every other axis, and — the part a
correlation cannot do — FAILS a mis-scaled or mis-permuted channel.

Why synthetic: the real cross-check runs on the D24 recordings and its measured numbers are in
the studio/rotation.py module doc (closed-lap rotation 0.983/0.975 x 2*pi measured against
1.001/1.001 x 2*pi for the app's inferred dtheta/dt). These tests pin the math itself — the
gravity permutation, the projection, the closed-loop scale test and the verdict — without a
12 GB file.

Run: python tests/test_rotation.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio import corners, gmeter, rotation  # noqa: E402

# A camera bolted on at an arbitrary tilt. Deliberately asymmetric in elements 0 and 1 so that
# GRAV_PERM (which swaps exactly those two) is not accidentally the identity for this fixture —
# otherwise the mis-permutation test below would pass a broken module.
_UP = np.array([0.25, -0.40, 1.0])
_UP = _UP / np.linalg.norm(_UP)

_R = 30.0        # arc radius (m)
_L = 80.0        # straight length (m)
_V = 15.0        # speed (m/s) -> arc yaw rate 0.5 rad/s, a realistic kart corner
_GPS_HZ = 10.0
_GYRO_HZ = 200.0


def _grav_elements(up):
    """GRAV stream elements for an up-direction expressed in the GYRO element frame.

    `rotation` reads `grav[:, 1 + GRAV_PERM[i]]` for component i, and GRAV_PERM swaps elements 0
    and 1, so the inverse is the same swap. Built from the constant, not from a literal, so this
    fixture cannot drift away from the module's convention."""
    p = gmeter.GRAV_PERM
    return np.array([up[p.index(0)], up[p.index(1)], up[p.index(2)]])


def _stadium(s):
    """Position + heading + signed curvature at arc length `s` on the stadium loop.

    Counterclockwise, so both arcs turn LEFT and the total heading change over one lap is
    exactly +2*pi — which is the ground truth the closed-loop test leans on."""
    per = 2 * _L + 2 * np.pi * _R
    s = np.asarray(s, float) % per
    x = np.empty_like(s)
    y = np.empty_like(s)
    k = np.zeros_like(s)

    a = s < _L                                        # bottom straight, heading +x
    x[a] = -_L / 2 + s[a]
    y[a] = -_R

    b = (s >= _L) & (s < _L + np.pi * _R)             # right arc, centre (+L/2, 0)
    u = (s[b] - _L) / _R
    x[b] = _L / 2 + _R * np.cos(-np.pi / 2 + u)
    y[b] = _R * np.sin(-np.pi / 2 + u)
    k[b] = 1.0 / _R

    c = (s >= _L + np.pi * _R) & (s < 2 * _L + np.pi * _R)   # top straight, heading -x
    x[c] = _L / 2 - (s[c] - _L - np.pi * _R)
    y[c] = _R

    d = s >= 2 * _L + np.pi * _R                      # left arc, centre (-L/2, 0)
    u = (s[d] - 2 * _L - np.pi * _R) / _R
    x[d] = -_L / 2 + _R * np.cos(np.pi / 2 + u)
    y[d] = _R * np.sin(np.pi / 2 + u)
    k[d] = 1.0 / _R
    return x, y, k


# A second track shape, for the statistics rather than the exact values. The stadium's arcs hold
# ONE curvature, so inside a corner both channels are constant and a correlation over those
# samples measures nothing (it is 0/0). This closed polar oval, r = R0*(1 + a*cos 2*theta), sweeps
# a curvature that varies continuously and changes sign — corners of several radii and two gentle
# counter-curves, like a real circuit — while still being a closed loop whose total heading change
# is exactly 2*pi. Its curvature comes from the analytic polar formula, so the fixture never
# borrows the numerical derivative the module under test is being compared against.
_R0, _A = 55.0, 0.25
_TH = np.linspace(0.0, 2 * np.pi, 40001)
_RR = _R0 * (1.0 + _A * np.cos(2 * _TH))
_SS = np.concatenate(([0.0], np.cumsum(np.hypot(
    np.diff(_RR * np.cos(_TH)), np.diff(_RR * np.sin(_TH))))))
_OVAL_PER = float(_SS[-1])


def _oval(s):
    """Position + signed curvature at arc length `s` on the polar oval (counterclockwise)."""
    th = np.interp(np.asarray(s, float) % _OVAL_PER, _SS, _TH)
    r = _R0 * (1.0 + _A * np.cos(2 * th))
    rp = -2.0 * _A * _R0 * np.sin(2 * th)
    rpp = -4.0 * _A * _R0 * np.cos(2 * th)
    k = (r ** 2 + 2.0 * rp ** 2 - r * rpp) / (r ** 2 + rp ** 2) ** 1.5
    return r * np.cos(th), r * np.sin(th), k


_STADIUM_PER = 2 * _L + 2 * np.pi * _R
# Where the start/finish line sits on each shape. A real timing line is on a straight (the
# assumption `corners.detect_corners` states outright), and it matters here: with the line at an
# arc EXIT, the gyro's low-pass window straddles the seam and the first sample of every lap
# carries the previous lap's rotation — which is a property of the fixture, not of the module.
_STADIUM_PHASE = _L / 2                 # mid-way along the bottom straight
_OVAL_PHASE = 0.25                      # fraction of the lap: the oval's least-curved stretch


def _build(laps=3, up=_UP, scale=1.0, wobble=0.0, grav_elements=None,
           path=_stadium, per=_STADIUM_PER, phase=_STADIUM_PHASE):
    """(gyro, grav, lap_traces) for `laps` clean laps of `path`.

    `scale` multiplies the gyro vector (the mis-scale fault a correlation cannot see); `wobble`
    adds a large sinusoidal rate about an axis PERPENDICULAR to `up` (roll/pitch content that
    must not leak into the yaw projection); `grav_elements` overrides the GRAV stream (used to
    inject the wrong axis permutation)."""
    lap_dur = per / _V

    # --- GYRO on its own dense grid, across the whole recording
    t = np.arange(0.0, laps * lap_dur, 1.0 / _GYRO_HZ)
    _x, _y, k = path(_V * t + phase)
    omega = _V * k                                    # rad/s, + = left
    vec = omega[:, None] * up[None, :] * scale
    if wobble:
        perp = np.cross(up, [1.0, 0.0, 0.0])
        perp = perp / np.linalg.norm(perp)
        vec = vec + (wobble * np.sin(2 * np.pi * 3.0 * t))[:, None] * perp[None, :] * scale
    gyro = np.column_stack([t, vec])

    ge = _grav_elements(up) if grav_elements is None else np.asarray(grav_elements, float)
    tg = np.arange(0.0, laps * lap_dur, 1.0 / 50.0)   # GRAV is slower than GYRO, as on a GoPro
    grav = np.column_stack([tg, np.repeat(ge[None, :], len(tg), axis=0)])

    # --- one (times, xs, ys, speed, cum_distances) trace per lap, as Session._lap_columns yields
    traces = []
    for i in range(laps):
        lt = np.arange(0.0, lap_dur + 1e-9, 1.0 / _GPS_HZ) + i * lap_dur
        s = _V * (lt - i * lap_dur)
        x, y, _k = path(s + phase)
        traces.append((lt, x, y, np.full(len(lt), _V), s))
    return gyro, grav, traces


def _build_oval(**kw):
    """`_build` on the varying-curvature oval — the fixture for the cross-check STATISTICS."""
    return _build(path=_oval, per=_OVAL_PER, phase=_OVAL_PHASE * _OVAL_PER, **kw)


def test_recovers_the_known_yaw_rate_through_an_arbitrary_camera_tilt():
    """The whole point: a kart turning at a known rate, a camera bolted on crooked, and the
    module must return the KART's rate — not a camera-axis component of it."""
    gyro, grav, _traces = _build()
    t, yaw = rotation.yaw_rate_series(gyro, grav)
    assert len(t) == len(gyro)
    # Pick samples by POSITION on the loop, well clear of an entry or exit: the output is
    # low-passed over LOWPASS_S, so the ramp at each transition is not a constant-rate sample.
    edge = 2.0 * rotation.LOWPASS_S * _V
    s = (_V * t + _STADIUM_PHASE) % _STADIUM_PER
    arc = (((s > _L + edge) & (s < _L + np.pi * _R - edge))
           | ((s > 2 * _L + np.pi * _R + edge) & (s < _STADIUM_PER - edge)))
    straight = ((s > edge) & (s < _L - edge)) | (
        (s > _L + np.pi * _R + edge) & (s < 2 * _L + np.pi * _R - edge))
    assert np.allclose(yaw[arc], _V / _R, atol=1e-6), (
        f"arc yaw rate {yaw[arc].mean():.6f} != expected {_V / _R:.6f} rad/s")
    # ...and the straights read zero, not a leaked tilt component.
    assert np.max(np.abs(yaw[straight])) < 1e-9
    print(f"ok arc yaw rate {yaw[arc].mean():+.6f} rad/s (expected {_V / _R:+.6f}), "
          f"straights |max| {np.max(np.abs(yaw[straight])):.2e}")


def test_sign_is_left_positive_and_agrees_with_path_curvature():
    """+ = left, the same convention as `corners.lap_curvature` and `gmeter.lat_g`. Asserted
    against the path's own signed curvature rather than against a hand-written expectation, so
    the two channels cannot drift apart in sign."""
    gyro, grav, traces = _build()
    _t, yaw = rotation.yaw_rate_series(gyro, grav)
    lt, x, y, v, d = traces[0]
    kappa = corners.lap_curvature(x, y, d)
    assert np.max(kappa) > 0, "fixture must turn LEFT (positive curvature)"
    assert np.max(yaw) > 0.4 and np.min(yaw) > -1e-2, "a left-only loop must read positive"
    print(f"ok sign: path kappa max {np.max(kappa):+.5f} 1/m, gyro yaw max {np.max(yaw):+.4f} rad/s")


def test_rotation_about_a_perpendicular_axis_does_not_leak_into_yaw():
    """Roll and pitch are large on a kart. Projecting on gravity is what keeps them out of the
    yaw number, so a 3 Hz, 2 rad/s wobble about a perpendicular axis must change nothing."""
    gyro_a, grav, _ = _build()
    gyro_b, _grav_b, _ = _build(wobble=2.0)
    _t, ya = rotation.yaw_rate_series(gyro_a, grav)
    _t, yb = rotation.yaw_rate_series(gyro_b, grav)
    assert np.max(np.abs(ya - yb)) < 1e-9, (
        f"perpendicular rotation leaked {np.max(np.abs(ya - yb)):.2e} rad/s into yaw")
    print("ok a 2 rad/s perpendicular wobble leaks < 1e-9 rad/s into the yaw channel")


def test_closed_lap_rotation_is_exactly_one_turn():
    """A lap is a closed loop, so the integrated yaw over it is 2*pi. This is the only statistic
    in the cross-check with a ground truth, and it is what the verdict keys off."""
    for name, build in (("stadium", _build), ("oval", _build_oval)):
        gyro, grav, traces = build()
        c = rotation.compute(gyro, grav, traces).cross
        assert c is not None and c.loop_n == len(traces)
        assert abs(c.loop_ratio_gyro - 1.0) < 0.01, (name, c.loop_ratio_gyro)
        # The path reference is held to the SAME exactness as the measurement. It used to be
        # allowed 5x the slack, which is how it went to production 6.5-10 % long on the real
        # recordings without a synthetic test noticing — the defect is a basis mismatch between
        # the speed column and the odometer, and this fixture's two agree by construction, so
        # the teeth for it live in tests/test_corners.py where the disagreement is injected.
        assert abs(c.loop_ratio_path - 1.0) < 0.01, (name, c.loop_ratio_path)
        assert c.loop_error_pct < 1.0 and c.path_loop_error_pct < 1.0
        print(f"ok closed lap ({name}): gyro {c.loop_ratio_gyro:.4f} x 2pi, "
              f"path dtheta/dt {c.loop_ratio_path:.4f} x 2pi over {c.loop_n} laps")


def test_the_verdict_catches_a_halved_channel_the_correlation_cannot_see():
    """The failure this repo has shipped before: a channel that tracks every corner perfectly
    and reads half. Pearson r is scale-invariant, so it must come out IDENTICAL — only the
    closed-loop ratio moves, and only it can fail the verdict."""
    gyro, grav, traces = _build_oval()
    good = rotation.compute(gyro, grav, traces).cross
    half = rotation.compute(np.column_stack([gyro[:, 0], gyro[:, 1:4] * 0.5]), grav, traces).cross
    assert abs(good.corner_corr - half.corner_corr) < 1e-9, (
        "the correlation must be blind to scale — if it moved, this test proves nothing")
    assert good.ok and not half.ok
    assert abs(half.loop_ratio_gyro - 0.5 * good.loop_ratio_gyro) < 1e-6
    assert abs(half.gain - 0.5 * good.gain) < 1e-6
    print(f"ok x0.5 fault: corner r unchanged at {half.corner_corr:+.4f}, "
          f"loop {good.loop_ratio_gyro:.3f} -> {half.loop_ratio_gyro:.3f} x 2pi, ok -> False")


def test_the_verdict_catches_the_wrong_gravity_permutation():
    """Projecting on the UNPERMUTED GRAV yields a corner-shaped signal at the wrong scale (and,
    for this mount, the wrong sign) — the quiet failure the module doc warns about."""
    gyro, grav, traces = _build_oval()
    unpermuted = _build_oval(grav_elements=_UP)[1]  # GRAV in the gyro's own element order
    good = rotation.compute(gyro, grav, traces).cross
    bad = rotation.compute(gyro, unpermuted, traces).cross
    assert good.ok, "the correctly-permuted fixture must pass"
    assert not bad.ok, f"un-permuted GRAV passed the verdict (loop {bad.loop_ratio_gyro:.3f})"
    print(f"ok wrong permutation: loop {bad.loop_ratio_gyro:+.3f} x 2pi vs "
          f"{good.loop_ratio_gyro:+.3f}, ok -> False")


def test_the_permutation_is_read_from_gmeter_not_retyped():
    """One convention, one definition. `rotation` projects onto the SAME gravity direction the
    g-meter removes; two copies of (1, 0, 2) is exactly how one of them silently rotates."""
    assert rotation.GRAV_PERM is gmeter.GRAV_PERM
    print(f"ok GRAV_PERM single-sourced from gmeter: {rotation.GRAV_PERM}")


def test_straights_read_zero_on_both_channels():
    """On the stadium — the fixture with REAL straights — the guard-eroded straight samples must
    read ~0 on the measured channel as well as the inferred one, and carry no standing bias."""
    gyro, grav, traces = _build()
    c = rotation.compute(gyro, grav, traces).cross
    assert c.straight_n > 0, "the stadium fixture must produce straights"
    assert c.straight_rms_gyro < 0.02 and c.straight_rms_path < 0.02
    assert abs(c.straight_mean_gyro) < 0.01
    print(f"ok straights n={c.straight_n}: gyro rms {c.straight_rms_gyro:.5f}, "
          f"path rms {c.straight_rms_path:.5f}, gyro mean {c.straight_mean_gyro:+.5f} rad/s")


def test_corners_carry_the_signal_and_track_the_path():
    """On the oval — the fixture whose curvature VARIES — the corner samples must correlate
    strongly and at the right scale. (A constant-radius corner cannot test this: both series are
    flat inside it and the correlation is 0/0, which is why there are two fixtures.)"""
    gyro, grav, traces = _build_oval()
    c = rotation.compute(gyro, grav, traces).cross
    assert c.corner_n > 0 and c.straight_n > 0, (
        f"the oval fixture must produce both (corners {c.corner_n}, straights {c.straight_n})")
    assert c.corner_corr > 0.95, c.corner_corr
    assert 0.9 < c.corner_gain < 1.1, c.corner_gain
    assert c.corr > 0.95 and 0.9 < c.gain < 1.1
    print(f"ok corners n={c.corner_n} r={c.corner_corr:+.4f} gain x{c.corner_gain:.3f}; "
          f"all n={c.n} r={c.corr:+.4f} gain x{c.gain:.3f}")


def test_guard_erosion_only_removes_samples_near_a_transition():
    """`_erode` must keep a sample only when the mask holds across its whole guard window, and
    must not reach across a lap boundary (it is called per lap)."""
    t = np.arange(0.0, 10.0, 0.1)
    mask = (t >= 3.0) & (t < 7.0)
    kept = rotation._erode(mask, t, 0.5)
    assert kept.sum() < mask.sum()
    assert np.all(t[kept] >= 3.5 - 1e-9) and np.all(t[kept] <= 6.5 + 1e-9)
    assert rotation._erode(mask, t, 0.0) is mask
    print(f"ok erosion: {mask.sum()} -> {kept.sum()} samples, span "
          f"[{t[kept][0]:.2f}, {t[kept][-1]:.2f}]")


def test_a_camera_without_a_gyro_yields_an_empty_channel_not_a_crash():
    """Pre-HERO5 cameras write no GYRO. There is deliberately NO GPS fallback: a path-derived
    rate is the thing this channel exists to check, not to imitate."""
    _gyro, grav, traces = _build()
    for absent in (np.empty((0, 4)), None):
        rot = rotation.compute(absent, grav, traces, device="HERO4 Silver")
        assert not rot.has_data and len(rot) == 0 and rot.cross is None
        assert rot.at_time(1.0) is None and rot.deg_at_time(1.0) is None
        assert rot.device == "HERO4 Silver"
    print("ok no GYRO -> empty Rotation, device name preserved, no fallback invented")


def test_compute_without_lap_traces_still_builds_the_channel():
    """The cross-check needs laps; the channel does not."""
    gyro, grav, _traces = _build()
    rot = rotation.compute(gyro, grav)
    assert rot.has_data and rot.cross is None
    assert np.isfinite(rot.yaw_rate).all()
    print(f"ok channel without laps: {len(rot)} samples, cross=None")


def test_at_time_picks_the_nearest_sample_and_degrees_agree():
    gyro, grav, traces = _build()
    rot = rotation.compute(gyro, grav, traces)
    for t in (rot.times[0], rot.times[len(rot) // 3], rot.times[-1]):
        i = int(np.argmin(np.abs(rot.times - t)))
        assert rot.at_time(float(t)) == float(rot.yaw_rate[i])
        assert np.isclose(rot.deg_at_time(float(t)), np.degrees(rot.at_time(float(t))))
    assert rot.at_time(-1e6) == float(rot.yaw_rate[0])
    assert rot.at_time(1e6) == float(rot.yaw_rate[-1])
    print("ok at_time: nearest pick, clamped ends, deg_at_time consistent")


def test_summary_names_both_scale_statistics():
    """The summary line is what a DATA TRUST surface will quote, so it must carry the number
    that discriminates (the closed-lap ratio) beside the one that does not (the correlation)."""
    gyro, grav, traces = _build_oval()
    s = rotation.compute(gyro, grav, traces).cross.summary()
    assert "AGREE" in s and "r=" in s and "gain" in s and "2pi" in s and "off exact" in s
    print(f"ok summary: {s}")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} rotation tests passed")
