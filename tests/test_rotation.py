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

from studio import _signal, corners, gmeter, rotation  # noqa: E402

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
           path=_stadium, per=_STADIUM_PER, phase=_STADIUM_PHASE, v=_V):
    """(gyro, grav, lap_traces) for `laps` clean laps of `path`.

    `scale` multiplies the gyro vector (the mis-scale fault a correlation cannot see); `wobble`
    adds a large sinusoidal rate about an axis PERPENDICULAR to `up` (roll/pitch content that
    must not leak into the yaw projection); `grav_elements` overrides the GRAV stream (used to
    inject the wrong axis permutation); `v` is the speed, which sets the GPS fix spacing and so
    the curvature window's width (15 m/s: 1.5 m apart, an ODD 5-sample window)."""
    lap_dur = per / v

    # --- GYRO on its own dense grid, across the whole recording
    t = np.arange(0.0, laps * lap_dur, 1.0 / _GYRO_HZ)
    _x, _y, k = path(v * t + phase)
    omega = v * k                                     # rad/s, + = left
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
        s = v * (lt - i * lap_dur)
        x, y, _k = path(s + phase)
        traces.append((lt, x, y, np.full(len(lt), v), s))
    return gyro, grav, traces


def _build_oval(**kw):
    """`_build` on the varying-curvature oval — the fixture for the cross-check STATISTICS."""
    return _build(path=_oval, per=_OVAL_PER, phase=_OVAL_PHASE * _OVAL_PER, **kw)


def _mirror(path):
    """The SAME track driven the other way round: reflect it in the x axis.

    Every shape above is counterclockwise, which is how a verdict that only ever accepts a lap
    closing at +2*pi reached production — D24 runs anticlockwise and the suite had no clockwise
    case at all. A reflection turns each left-hand corner into the identical right-hand one:
    radii, arc lengths, speeds and the arc-length parametrisation are untouched, both channels'
    sign flips, and the lap closes at -2*pi instead of +2*pi. So the ONLY thing that differs
    between a fixture and its mirror is the direction of travel — and in IEEE arithmetic the
    negation is exact, which is why the tests below can compare the two to the last bit."""

    def mirrored(s):
        x, y, k = path(s)
        return x, -y, -k

    return mirrored


def _build_oval_cw(**kw):
    """The oval fixture, driven CLOCKWISE (`_mirror`). Every Sandown recording runs this way."""
    return _build(path=_mirror(_oval), per=_OVAL_PER, phase=_OVAL_PHASE * _OVAL_PER, **kw)


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


def test_the_verdict_does_not_depend_on_which_way_the_track_runs():
    """A CLOCKWISE lap closes at -2*pi, and that is exactly as exact as +2*pi.

    The defect this pins: the verdict read `_LOOP_MIN <= loop_ratio_gyro <= _LOOP_MAX` on the
    SIGNED ratio, so a lap that closes clockwise failed a test it passes. Measured over the real
    load path on the owner's own footage before the fix — all three of his clockwise recordings
    printed "Rotation cross-check: DISAGREES" in DATA TRUST while both channels agreed to within
    3 %:

        Sandown_09_05_2026  gyro -0.974 x 2pi, path -0.999, corners r=+0.94, 59 laps -> DISAGREE
        SD_30_08_26         gyro -0.961 x 2pi, path -0.999, corners r=+0.95, 37 laps -> DISAGREE
        Sandown 3h 2026     gyro -0.973 x 2pi, path -1.000, corners r=+0.93, 62 laps -> DISAGREE
        D24 0060 / 0062     gyro +0.983 / +0.975 x 2pi                          -> AGREE

    D24 is anticlockwise, which is why nothing in this suite caught it: every fixture above turns
    left. The mirror is compared leaf by leaf against its original, so this cannot pass by the
    clockwise case merely landing somewhere lenient."""
    ccw = rotation.compute(*_build_oval()).cross
    cw = rotation.compute(*_build_oval_cw()).cross
    assert ccw is not None and cw is not None
    assert ccw.loop_ratio_gyro > 0 and ccw.loop_ratio_path > 0, "the fixture must turn LEFT"
    assert cw.loop_ratio_gyro < 0 and cw.loop_ratio_path < 0, (
        f"the mirrored fixture must turn RIGHT (gyro {cw.loop_ratio_gyro:+.4f} x 2pi)")
    assert ccw.ok, "the counter-clockwise control must pass"
    assert cw.ok, (
        f"a CLOCKWISE lap failed the verdict: closed-lap rotation {cw.loop_ratio_gyro:+.4f} x 2pi "
        f"measured vs {cw.loop_ratio_path:+.4f} inferred, corners r={cw.corner_corr:+.4f} — the "
        f"two channels agree, and the only thing wrong is the direction of travel")

    # …and not merely "also passes": every statistic on the card must be the mirror's own.
    assert abs(cw.loop_ratio_gyro + ccw.loop_ratio_gyro) < 1e-9, (cw.loop_ratio_gyro,
                                                                  ccw.loop_ratio_gyro)
    assert abs(cw.loop_ratio_path + ccw.loop_ratio_path) < 1e-9
    assert abs(cw.corner_corr - ccw.corner_corr) < 1e-9, (cw.corner_corr, ccw.corner_corr)
    assert abs(cw.corner_gain - ccw.corner_gain) < 1e-9
    # The "% off exact" figures are DISTANCES from the target, so they must be mirror-invariant
    # too — they read 197.4 % and 199.9 % on the owner's clockwise footage before the fix.
    assert abs(cw.loop_error_pct - ccw.loop_error_pct) < 1e-6, (cw.loop_error_pct,
                                                                ccw.loop_error_pct)
    assert abs(cw.path_loop_error_pct - ccw.path_loop_error_pct) < 1e-6
    assert cw.loop_error_pct < 1.0, cw.loop_error_pct
    # …and the exact target the surfaces quote carries the direction rather than always +1.000.
    assert cw.loop_exact == -1.0 and ccw.loop_exact == 1.0
    assert "-1.000" in cw.summary() and "AGREE" in cw.summary(), cw.summary()
    print(f"ok direction-blind verdict: clockwise loop {cw.loop_ratio_gyro:+.4f} x 2pi vs "
          f"counter-clockwise {ccw.loop_ratio_gyro:+.4f}, both ok, both "
          f"{cw.loop_error_pct:.2f}% off their own exact target")


def test_a_channel_turning_the_opposite_way_from_the_path_is_still_rejected():
    """Making direction irrelevant must not make the SIGN irrelevant. Feed the clockwise lap
    traces the counter-clockwise gyro — a mirrored sensor, the fault a gravity permutation
    produces — and the two channels now disagree about which way the kart went. Both the corner
    correlation and the closed-lap sign catch it, and both are asserted: the correlation is the
    one that bites here, so the sign guard is pinned directly rather than through `ok`."""
    gyro_ccw, grav, _ = _build_oval()
    _g, _v, traces_cw = _build_oval_cw()
    c = rotation.compute(gyro_ccw, grav, traces_cw).cross
    assert c is not None
    assert not c.ok, (
        f"a channel turning the opposite way from the path passed: loop {c.loop_ratio_gyro:+.4f} "
        f"vs {c.loop_ratio_path:+.4f} x 2pi, corners r={c.corner_corr:+.4f}")
    assert np.sign(c.loop_ratio_gyro) != np.sign(c.loop_ratio_path), (
        c.loop_ratio_gyro, c.loop_ratio_path)
    assert c.corner_corr < rotation._CORR_MIN, c.corner_corr
    # The magnitude alone would have passed — which is exactly why the sign is checked against
    # the path reference and not against a hard-coded +1.
    assert rotation._LOOP_MIN <= abs(c.loop_ratio_gyro) <= rotation._LOOP_MAX, c.loop_ratio_gyro
    print(f"ok mirrored sensor rejected: loop {c.loop_ratio_gyro:+.4f} vs path "
          f"{c.loop_ratio_path:+.4f} x 2pi (|ratio| {abs(c.loop_ratio_gyro):.3f} is IN band), "
          f"corners r={c.corner_corr:+.4f}")


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
    assert "AGREE" in s and "r=" in s and "gain" in s and "2pi" in s
    # The exact target is quoted WITH ITS SIGN: this fixture turns left, so +1.000. A surface that
    # prints a bare "1.000" tells a driver at a right-hand circuit his correct channel is 200 %
    # wrong — which is what DATA TRUST did on all three of the owner's clockwise recordings.
    assert "off an exact +1.000" in s, s
    print(f"ok summary: {s}")


def test_an_all_zero_grav_stream_is_no_channel_not_a_channel_of_zeros():
    """GRAV is a UNIT vector on every camera that writes one: |GRAV| measures 1.0000 at the 5th,
    50th and 95th percentile on both GoPro Max sample clips and on both D24 recordings (103,680
    rows each). A stream of zeros therefore carries no direction at all — and it is not
    hypothetical, the bundled `hero8.mp4` sample's GRAV is ALL ZEROS.

    Un-guarded that neither raises nor reads as missing. `up` normalises (0,0,0) back to (0,0,0),
    so every projection is EXACTLY 0.0 rad/s and the app reports a full-length, `has_data=True`
    rotation channel saying "not turning" for a recording that plainly is — hero8's own gyro has a
    median |omega| of 0.269 rad/s over the same samples. `gmeter.axis_check` refuses this
    recording on the G-METER path (a GRAV with no direction, `gmeter.MIN_GRAV_NORM`), but nothing
    stood in front of THIS path, even though rotation.py's module doc says that guard does."""
    gyro, grav, traces = _build(grav_elements=[0.0, 0.0, 0.0])
    assert np.all(grav[:, 1:] == 0.0)
    # ...while the gyro itself is a real, turning signal: the channel has something to report.
    assert float(np.median(np.linalg.norm(gyro[:, 1:], axis=1))) > 0.05

    t, yaw = rotation.yaw_rate_series(gyro, grav)
    assert len(t) == 0 and len(yaw) == 0, (
        f"a zero GRAV direction still produced {len(t)} yaw-rate samples, "
        f"all-zero={bool(np.all(yaw == 0.0))}")

    rot = rotation.compute(gyro, grav, traces, device="HERO8 Black")
    assert not rot.has_data, (
        f"a zero GRAV direction produced a has_data channel of {len(rot)} samples reading "
        f"{rot.at_time(float(gyro[0, 0]))} rad/s throughout")
    assert rot.cross is None
    print("ok an all-zero GRAV stream yields NO channel, not a channel of zeros")


def test_a_unit_grav_stream_is_untouched_by_the_zero_guard():
    """The guard must cost a real recording nothing: a genuine unit-length GRAV still builds the
    channel and its cross-check. Pins the separation the threshold rests on (1.0000 vs 0.0000)."""
    gyro, grav, traces = _build()
    assert abs(float(np.median(np.linalg.norm(grav[:, 1:], axis=1))) - 1.0) < 1e-9
    rot = rotation.compute(gyro, grav, traces)
    assert rot.has_data and rot.cross is not None
    assert float(np.std(rot.yaw_rate)) > 0.0
    print(f"ok a unit GRAV stream still builds {len(rot)} samples + a cross-check")


def test_an_injected_clock_offset_is_recovered_in_size_and_in_sign():
    """The estimator's own check: delay one stream by a known amount and the module must report
    that amount AND name the right channel as the late one. Both directions, because a sign error
    here would blame the wrong clock on the DATA TRUST card — and the card's whole job is to say
    which number a reader can trust."""
    gyro, grav, traces = _build_oval(laps=4)
    base = rotation.compute(gyro, grav, traces).cross
    assert base.gps_lag_s is not None, "a clean synthetic fixture is measurable"
    assert abs(base.gps_lag_s) < 0.02, base.gps_lag_s
    # A measured zero states agreement rather than a direction nobody measured.
    assert "within" in base.lag_clause, base.lag_clause

    # Stamp the GYRO 0.4 s LATE -> the GPS trace is now the EARLY one.
    late = gyro.copy()
    late[:, 0] += 0.4
    c = rotation.compute(late, grav, traces).cross
    assert abs(c.gps_lag_s + 0.4) < 0.02, c.gps_lag_s
    assert "0.40 s ahead of" in c.lag_clause, c.lag_clause

    # …and the other way, which is the D24 direction: the GPS trace lands behind the gyro.
    early = gyro.copy()
    early[:, 0] -= 0.4
    c2 = rotation.compute(early, grav, traces).cross
    assert abs(c2.gps_lag_s - 0.4) < 0.02, c2.gps_lag_s
    assert "0.40 s behind" in c2.lag_clause, c2.lag_clause
    # The clause is about THESE figures, which carry the offset; whether the video overlay is
    # corrected by it is the session's business (Session._install_gps_lag), not this dataclass's.
    assert "measured with that offset left in" in c2.lag_clause, c2.lag_clause
    assert c2.lag_clause in c2.summary(), "the load-time log states it in the same words"
    print(f"ok injected offset recovered both ways: {c.gps_lag_s:+.3f} / {c2.gps_lag_s:+.3f} s")


def test_an_even_curvature_window_does_not_read_as_gps_lag():
    """The fixture above laps at 15 m/s, 1.5 m between 10 Hz fixes, so the path reference's 8 m
    curvature window is 5 samples — ODD, and centred either way. At 20 m/s it is 4 and at
    13.5 m/s it is 6: EVEN, and a plain boxcar of an even width averages [i - w/2, i + w/2 - 1],
    i.e. it is centred half a sample LATE. `measure_lag` read that as clock offset — +0.047 s of
    GPS lag on these offset-free fixtures, and on the owner's recordings the installed figure
    carried it on every even-window lap (55 of 62 laps on Sandown 3h). Measured end to end on the
    synthetic GoPro, whose truth is known, it was +57.7 ms of the +75.4 ms the installed correction
    overshot."""
    for v in (20.0, 13.5):
        gyro, grav, traces = _build_oval(laps=4, v=v)
        spacing = float(np.median(np.diff(traces[0][4])))
        w = int(round(corners.KAPPA_SMOOTH_M / spacing))
        assert w % 2 == 0, f"at {v} m/s the fixture must exercise an EVEN window, got w={w}"
        c = rotation.compute(gyro, grav, traces).cross
        assert c.gps_lag_s is not None, "a clean synthetic fixture is measurable"
        assert abs(c.gps_lag_s) < 0.01, (
            f"{v} m/s (w={w}): an offset-free fixture reads {c.gps_lag_s:+.4f} s of GPS lag")
        print(f"ok w={w} at {v} m/s reads {c.gps_lag_s:+.4f} s on a fixture with no offset")


def test_every_window_the_lag_is_measured_through_is_centred():
    """A centred smoother leaves a straight line where it is; one that is not moves it by its own
    delay. The plain boxcar of an EVEN width moves it half a sample, which is the defect above;
    `_signal.centred_boxcar` does not at either parity — and both of the measurement's windows use
    it: the gyro's 0.30 s low-pass (60 samples at 200 Hz, even: 2.5 ms late before) and the
    path's curvature window."""
    ramp = np.arange(300.0)
    for w in (2, 4, 5, 6, 60, 61):
        out = _signal.centred_boxcar(ramp, w)
        assert np.allclose(out[w:-w], ramp[w:-w], atol=1e-9), f"w={w} moved a straight line"
    assert np.allclose(_signal.boxcar(ramp, 4)[8:-8], ramp[8:-8] - 0.5), (
        "the plain even-width boxcar is no longer half a sample late: this test's premise is gone")
    # The gyro channel: a yaw rate rising linearly in time comes back exactly, not 2.5 ms late.
    t = np.arange(0.0, 20.0, 1.0 / _GYRO_HZ)
    rate = 0.05 * t
    gyro = np.column_stack([t, rate[:, None] * _UP[None, :]])
    tg = np.arange(0.0, 20.0, 1.0 / 50.0)
    grav = np.column_stack([tg, np.repeat(_grav_elements(_UP)[None, :], len(tg), axis=0)])
    tt, yaw = rotation.yaw_rate_series(gyro, grav)
    inner = (tt > 1.0) & (tt < 19.0)
    assert np.allclose(yaw[inner], rate[inner], atol=1e-9), (
        f"the gyro low-pass delays a ramp by {float(np.mean(rate[inner] - yaw[inner]) / 0.05) * 1e3:+.2f} ms")
    # The path reference: a clothoid's curvature rises linearly in arc length, 2 m between fixes
    # (w = 4). Centred, the profile sits on it; the corner model's default sits half a sample back.
    ds, c = 2.0, 1e-4
    s = np.arange(0.0, 600.0 + 1e-9, ds)
    heading = 0.5 * c * s ** 2
    x = np.concatenate([[0.0], np.cumsum(np.cos(heading[:-1] + 0.5 * c * ds * (s[:-1] + ds / 2)) * ds)])
    y = np.concatenate([[0.0], np.cumsum(np.sin(heading[:-1] + 0.5 * c * ds * (s[:-1] + ds / 2)) * ds)])
    d = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
    inner = slice(10, -10)
    centred = corners.lap_curvature(x, y, d, centred=True)[inner] - c * d[inner]
    default = corners.lap_curvature(x, y, d)[inner] - c * d[inner]
    half = c * ds / 2
    assert abs(float(np.mean(centred))) < 0.1 * half, (float(np.mean(centred)), half)
    assert abs(float(np.mean(default)) + half) < 0.1 * half, (float(np.mean(default)), half)
    print("ok both measurement windows are centred; the default curvature window is half a fix late")


def test_the_offset_is_measured_on_one_clock_or_the_two_clocks_rate_reads_as_a_drift():
    """The trap this repo already fell into, and the reason `to_media` exists. The GPS axis and
    the media axis differ by a RATE (~27 ppm on the D24 recordings), so a sweep against raw
    telemetry time slides across a session and averages to something SHORTER than the real,
    constant offset — which is how a 0.46 s offset got written down as 0.35-0.40 s.

    The fixture exaggerates the rate to 2000 ppm so a 95-second synthetic can show what 27 ppm
    does over an 84-minute recording."""
    gyro, grav, traces = _build_oval(laps=4)
    rate, offset = 1.002, 0.0
    tel_traces = [((t - offset) / rate, x, y, v, d) for (t, x, y, v, d) in traces]
    shifted = gyro.copy()
    shifted[:, 0] -= 0.4                      # the D24 direction: GPS 0.40 s behind the gyro

    one_clock = rotation.compute(
        shifted, grav, tel_traces,
        to_media=lambda t: rate * np.asarray(t, float) + offset).cross
    assert abs(one_clock.gps_lag_s - 0.4) < 0.02, one_clock.gps_lag_s

    raw = rotation.compute(shifted, grav, tel_traces).cross
    assert abs(raw.gps_lag_s - 0.4) > 0.05, (
        f"measuring on the telemetry axis returned {raw.gps_lag_s:+.3f} s, indistinguishable from "
        f"the mapped {one_clock.gps_lag_s:+.3f} s — this fixture no longer exercises the trap")
    print(f"ok one clock {one_clock.gps_lag_s:+.3f} s vs telemetry axis {raw.gps_lag_s:+.3f} s")


def test_a_channel_that_never_tracks_the_path_reports_no_offset_rather_than_zero():
    """A gyro that does not follow the racing line has no clock offset to state. The module says
    so with None — and the card then omits the sentence entirely — instead of reporting the
    0.00 s that taking the peak of a noise sweep would hand it."""
    gyro, grav, traces = _build_oval(laps=4)
    rng = np.random.default_rng(7)
    noise = gyro.copy()
    noise[:, 1:] = rng.normal(0.0, 0.5, size=noise[:, 1:].shape)
    c = rotation.compute(noise, grav, traces).cross
    assert c is not None, "the cross-check itself still exists; only the offset is unmeasurable"
    assert c.gps_lag_s is None, c.gps_lag_s
    assert c.lag_clause == "", c.lag_clause
    assert "GPS trace" not in c.summary(), c.summary()
    print("ok an untracking channel reports NO offset rather than 0.00 s")


def test_the_gps_lag_is_quoted_as_one_figure_or_names_its_statistic():
    """ONE FIGURE, AND EVERY OTHER SPELLING SAYS WHICH STATISTIC IT IS.

    The figure is `measure_lag` over the whole recording — +0.4764 s (0060) / +0.4589 s (0062),
    what `Session._install_gps_lag` installs. "0.483" spells TWO other statistics of the same
    quantity: #291's whole-recording reading through a plain per-sample np.interp harness
    (+0.4834), and — by coincidence — `measure_lag`'s own per-lap median (+0.4827). #301 then
    called #291's figure a per-lap median, and four agents in one campaign quoted one of these at
    each other and had to stop and say which (#299, #303). "0.485" is that harness's per-lap median.

    So a line that quotes either spelling near a lag must name its statistic within two lines —
    the harness, a per-lap median, a per-chapter figure — and the module doc must carry the figure
    itself. JSON (the golden baseline's floats) is not prose and is not scanned.

    NEGATIVE CONTROL, watched: on the tree before T9 this lists `studio/rotation.py`'s module doc
    ("run 0.483 s (0060) … BEHIND"), `studio/dev/probes/_align.py` ("+0.483 s (0060) and +0.459 s")
    and `tests/test_quality_strip.py`'s "real" 0060 `RotationCheck(gps_lag_s=0.483)`."""
    import re

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spelling = re.compile(r"(?<![\d.])0\.48[35](?!\d)")
    lag_word = re.compile(r"lag|late|behind|GPS", re.IGNORECASE)
    statistic = re.compile(r"harness|np\.interp|per[- ]lap|median|per[- ]chapter|statistic",
                           re.IGNORECASE)
    this_file = os.path.abspath(__file__)
    paths = [os.path.join(repo, n) for n in os.listdir(repo) if n.endswith(".md")]
    for top in ("studio", "tests", "docs"):
        for root, _dirs, files in os.walk(os.path.join(repo, top)):
            paths += [os.path.join(root, n) for n in files if n.endswith((".py", ".md"))]
    bad = []
    for path in sorted(paths):
        if os.path.abspath(path) == this_file:
            continue
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        for i, line in enumerate(lines):
            if not spelling.search(line):
                continue
            window = "\n".join(lines[max(0, i - 2):i + 3])
            if lag_word.search(window) and not statistic.search("\n".join(lines[max(0, i - 2):i + 1])):
                bad.append(f"{os.path.relpath(path, repo)}:{i + 1}: {line.strip()[:90]}")
    assert not bad, ("a GPS-lag spelling other than the installed +0.4764 / +0.4589 s, with no "
                     f"statistic named beside it (see studio/rotation.py 'WHAT IT IS'): {bad}")
    with open(os.path.join(repo, "studio", "rotation.py"), encoding="utf-8") as f:
        doc = f.read()
    assert "+0.4764 s" in doc and "+0.4589 s" in doc and "WHOLE RECORDING" in doc, (
        "rotation.py's module doc must state the installed figure itself")
    print("ok the GPS lag is one figure; every other spelling names its statistic")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} rotation tests passed")
