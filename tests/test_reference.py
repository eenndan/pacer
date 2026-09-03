"""Synthetic unit tests for studio.reference — the closed-loop reference-centerline fit.

Pure Python + numpy: no `pacer`, no telemetry file, fast. A regression guard for the cyclic
arc-length correspondence fit: it must recover a known similarity transform (scale,
rotation, translation) from a noisy loop, including the cyclic-start-offset + reversed
traversal + reflection case the old unordered-cloud ICP flunked (it collapsed onto an inner
sub-loop, ~30 % footprint coverage on the real MK sessions).
Run:  python tests/test_reference.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from studio import reference as ref  # noqa: E402


def make_track(n=300):
    """An asymmetric closed loop (egg + bumps) — no rotational symmetry, so the recovered
    rotation is unambiguous."""
    th = np.linspace(0, 2 * np.pi, n, endpoint=False)
    r = 1.0 + 0.35 * np.cos(th) + 0.18 * np.sin(2 * th) + 0.08 * np.cos(3 * th + 0.7)
    return np.column_stack([r * np.cos(th), r * np.sin(th)])


def apply_similarity(xy, scale, ang, t, reflect=False):
    c, s = np.cos(ang), np.sin(ang)
    R = np.array([[c, -s], [s, c]])
    if reflect:
        R = R @ np.array([[1.0, 0.0], [0.0, -1.0]])
    return scale * np.asarray(xy, float) @ R.T + np.asarray(t, float)


def _check_fit(info, scale, rms_tol=4.0):
    assert abs(info["scale"] - scale) / scale < 0.02, info["scale"]
    assert info["rms"] < rms_tol, info["rms"]
    assert info["coverage"] > 0.98, info["coverage"]


def test_recovers_known_transform():
    # The "stored reference" is the track itself; the "lap" is a similarity-transformed,
    # GPS-noisy copy. The fit must recover the transform.
    track = make_track()
    scale, ang, t = 173.0, 0.9, np.array([512.0, -288.0])
    rng = np.random.default_rng(42)
    lap = apply_similarity(track, scale, ang, t) + rng.normal(0, 1.5, (len(track), 2))
    fitted, info = ref.fit_loop_to_loop(track, lap)
    _check_fit(info, scale)
    # Rotation recovered: the fitted R (det +1 here) matches the ground-truth rotation.
    c, s = np.cos(ang), np.sin(ang)
    assert np.linalg.det(info["R"]) > 0
    assert np.allclose(info["R"], [[c, -s], [s, c]], atol=0.02), info["R"]
    assert np.allclose(info["t"], t, atol=4.0), info["t"]
    print("test_recovers_known_transform OK:",
          f"rms={info['rms']:.2f} cov={info['coverage']:.3f}")


def test_cyclic_offset_reverse_reflect():
    # The case the old free-scale ICP flunked: the stored loop starts mid-lap, runs the
    # OPPOSITE direction, and is reflected (image y-down vs local y-up). The cyclic search
    # must find the offset+direction and the Umeyama solve must pick the reflection.
    track = make_track()
    stored = np.roll(track, 117, axis=0)[::-1] * np.array([1.0, -1.0])
    rng = np.random.default_rng(7)
    lap = apply_similarity(track, 80.0, -2.2, [-1000.0, 400.0]) \
        + rng.normal(0, 1.0, (len(track), 2))
    fitted, info = ref.fit_loop_to_loop(stored, lap)
    _check_fit(info, 80.0)
    assert np.linalg.det(info["R"]) < 0  # the reflection was recovered
    print("test_cyclic_offset_reverse_reflect OK:",
          f"rms={info['rms']:.2f} cov={info['coverage']:.3f} reversed={info['reversed']}")


def test_mk_trace_self_fit():
    # The REAL stored MK polyline (outer ring + infield switchbacks — the exact geometry the
    # old ICP collapsed on): a transformed noisy copy of it must be re-fit near-perfectly.
    norm = ref._load_normalized()
    assert norm is not None and len(norm) >= 30
    truth = ref._resample_closed(norm, 700)
    rng = np.random.default_rng(3)
    lap = np.roll(apply_similarity(truth, 480.0, 2.4, [300.0, 900.0], reflect=True), 250,
                  axis=0) + rng.normal(0, 1.2, (700, 2))
    fitted, info = ref.fit_loop_to_loop(norm, lap)
    _check_fit(info, 480.0)
    print("test_mk_trace_self_fit OK:",
          f"rms={info['rms']:.2f} cov={info['coverage']:.3f}")


def test_centerline_local_guards():
    # Degenerate inputs return an empty array (the gap-fill fallback just doesn't exist).
    assert ref.centerline_local(None).shape == (0, 2)
    assert ref.centerline_local(np.zeros((4, 2))).shape == (0, 2)
    print("test_centerline_local_guards OK")


# ------------------------------------------------------------------ W8-03: the circuit gate
# The stored polyline is ONE circuit. On main it was returned for ANY loop, so LapRenderCache
# armed a Daytona MK ring as the gap-fill donor on Sandown (fit at 39 % of the size it takes on
# its own track) and on an unnamed 53 x 67 m kart track — and on a recording with a single valid
# lap it was the ONLY donor. These pin the admission test that now stands in the way.

def _mk_lap(scale=207.0, ang=2.4, t=(300.0, 900.0), reflect=True, noise=0.8, seed=11, n=700):
    """A synthetic lap ON the stored circuit: the stored polyline put through a similarity
    transform, rolled to a different start point and given GPS-grade noise."""
    truth = ref._resample_closed(ref._load_normalized(), n)
    rng = np.random.default_rng(seed)
    return np.roll(apply_similarity(truth, scale, ang, t, reflect=reflect), 250, axis=0) \
        + rng.normal(0, noise, (n, 2))


def _foreign_lap(kind="ellipse", n=700):
    """A loop that is NOT the stored circuit, at a plausible kart-track size."""
    th = np.linspace(0, 2 * np.pi, n, endpoint=False)
    if kind == "ellipse":
        return np.column_stack([100.0 * np.cos(th), 60.0 * np.sin(th)])
    # A rounded rectangle — an outer ring with none of MK's infield switchbacks.
    return np.column_stack([80.0 * np.sign(np.cos(th)) * np.abs(np.cos(th)) ** 0.5,
                            50.0 * np.sign(np.sin(th)) * np.abs(np.sin(th)) ** 0.5])


def test_the_gate_brackets_the_measured_envelope():
    """The thresholds are not free parameters: they sit in the gap between 65 real Daytona MK
    laps (coverage 1.000, RMS 0.57-1.45 m) and 26 laps of two other circuits (coverage 0.719 and
    0.824-0.971, RMS 9.40 and 5.11-6.35 m), all measured in the app. Both halves must hold, and
    a missing/non-finite input is never a match."""
    assert ref.fit_is_this_circuit(1.45, 1.000)   # the worst of 65 real Daytona MK laps
    assert ref.fit_is_this_circuit(0.57, 1.000)   # the best of them
    assert not ref.fit_is_this_circuit(5.11, 0.971)  # the BEST of 26 foreign laps — both halves
    assert not ref.fit_is_this_circuit(9.40, 0.719)  # the Sandown fit that filed W8-03
    assert not ref.fit_is_this_circuit(6.35, 0.824)
    # Coverage alone at the reporter's suggested ~0.95 would have admitted that best foreign lap.
    assert ref.FIT_COVERAGE_MIN > 0.971, ref.FIT_COVERAGE_MIN
    # Either half alone is enough to refuse.
    assert not ref.fit_is_this_circuit(0.74, 0.90), "a close but incomplete fit was admitted"
    assert not ref.fit_is_this_circuit(4.00, 1.00), "a complete but far fit was admitted"
    for bad in (np.nan, np.inf):
        assert not ref.fit_is_this_circuit(bad, 1.0) and not ref.fit_is_this_circuit(1.0, bad)
    assert not ref.fit_is_this_circuit(None, 1.0) and not ref.fit_is_this_circuit(1.0, None)
    print("test_the_gate_brackets_the_measured_envelope OK")


def test_centerline_local_refuses_a_foreign_loop():
    """The W8-03 landmine itself: a loop that is not this circuit gets an empty ring, so no
    donor exists to draw. On main every one of these came back as a full 601-point ring."""
    own = ref.centerline_local(_mk_lap())
    assert own.shape[0] > 100 and own.shape[1] == 2, own.shape
    for kind in ("ellipse", "rect"):
        got = ref.centerline_local(_foreign_lap(kind))
        assert got.shape == (0, 2), f"{kind}: a foreign circuit was armed as a donor {got.shape}"
    print("test_centerline_local_refuses_a_foreign_loop OK")


def test_the_gate_judges_shape_and_deliberately_not_size():
    """Same circuit, half the metres: still accepted. Not an oversight — the stored polyline is
    normalized into a unit box, so it carries no true size to compare a fitted scale against,
    and what a donor actually contributes is a SHAPE (gapfill re-pins the borrowed sub-path to
    the gap's two mouths with its own similarity transform, so the ring's absolute size never
    reaches the map). The sibling gate in cross_reference CAN test scale because both of its
    loops are already in metres; this one cannot, and refuses on shape instead."""
    small = ref.centerline_local(_mk_lap(scale=96.0, noise=0.35, seed=5))
    assert small.shape[0] > 100, "the same circuit at another size was refused"
    _fitted, info = ref.fit_loop_to_loop(ref._load_normalized(), _mk_lap(scale=96.0, noise=0.35,
                                                                        seed=5))
    assert info["scale"] < 0.6 * 207.0, info["scale"]  # genuinely a different fitted scale
    print("test_the_gate_judges_shape_and_deliberately_not_size OK:",
          f"scale={info['scale']:.1f} rms={info['rms']:.2f} cov={info['coverage']:.3f}")


def test_the_donor_list_drops_the_reference_on_a_foreign_track():
    """End of the path, where it bit: LapRenderCache.donors_for offered "MK-ref" for EVERY
    session. It now offers it only where the fit accepted the circuit — and on the filed case
    (one valid lap on another track) the list goes from ['MK-ref'] to empty, so gapfill bridges
    with its own spline instead of drawing a foreign track into the hole."""
    from studio import render_cache  # noqa: PLC0415 — a test-local import, like the app's own

    def cache_over(loop, laps=2):
        xyt = {i: (loop[:, 0], loop[:, 1], np.arange(len(loop)) * 0.1) for i in range(laps)}
        return render_cache.LapRenderCache(
            lap_xyt=lambda i: xyt[i], valid_lap_ids=lambda: sorted(xyt),
            lap_has_dropout=lambda i: False, lap_time=lambda i: 60.0 + i,
            trace_times=np.arange(len(loop) * laps) * 0.1)

    names = [d["name"] for d in cache_over(_mk_lap(), laps=2).donors_for(0)]
    assert names == ["1", "MK-ref"], names          # its own circuit: unchanged

    names = [d["name"] for d in cache_over(_foreign_lap(), laps=2).donors_for(0)]
    assert names == ["1"], f"a foreign circuit is still offered as a donor: {names}"

    # The filed shape: ONE valid lap on another track, so there is no cross-lap donor either.
    names = [d["name"] for d in cache_over(_foreign_lap(), laps=1).donors_for(0)]
    assert names == [], f"the only fill source was a foreign track: {names}"
    print("test_the_donor_list_drops_the_reference_on_a_foreign_track OK")


if __name__ == "__main__":
    test_recovers_known_transform()
    test_cyclic_offset_reverse_reflect()
    test_mk_trace_self_fit()
    test_centerline_local_guards()
    test_the_gate_brackets_the_measured_envelope()
    test_centerline_local_refuses_a_foreign_loop()
    test_the_gate_judges_shape_and_deliberately_not_size()
    test_the_donor_list_drops_the_reference_on_a_foreign_track()
    print("ALL OK")
