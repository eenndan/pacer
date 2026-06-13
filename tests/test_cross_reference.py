"""Cross-recording reference lap (F7) — pure-Python unit tests.

No telemetry file, no pacer build dependency for the logic covered: a synthetic SECOND Session
(bare, seeded via tests/_synthetic) is adopted as the reference for a primary bare Session, and
the asserts are the contract the feature must hold:

  * delta endpoint with a reference active == (primary lap time − reference lap time), aligned by
    NORMALIZED distance, even when the two laps have different total distances (different recordings);
  * the same baseline drives the per-tick readout (delta_at_lap) and both x-axis modes;
  * the track-mismatch guard refuses a foreign-track reference (and a no-valid-laps reference)
    without disturbing the local best;
  * clear_reference reverts to the local best;
  * DORMANT identity: with no reference, delta() is byte-identical to the pre-feature output (the
    "no change when off" invariant), checked here on a bare Session against a hand-computed baseline;
  * the cross_reference.build map-overlay fit gate (a good fit overlays, a gross mis-fit is dropped
    but the data side still works).

Run:  python tests/test_cross_reference.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio import cross_reference as xr  # noqa: E402
from studio.session import REFERENCE_ID, Session  # noqa: E402
from tests._synthetic import bare_session, odometer, seed_cols  # noqa: E402


# ---------------------------------------------------------------- synthetic Session helpers
def make_session(laps, *, best, valid, track="Test Track"):
    """A bare Session seeded so the reference machinery's reads resolve: _dist_cache (delta math),
    _cols_cache (_lap_arrays / lap_trace_xy), the valid/best memos, and a track name + an empty
    laps stub so delta()'s laps_count() range check passes for the seeded ids."""
    s = bare_session(laps, valid=valid)
    s._best_cache = best  # always seed (incl. None) so best_lap_id() resolves on a no-laps ref
    for lid, (times, dists) in laps.items():
        seed_cols(s, lid, times, dists)
    s.track_name = track
    s._reference = None
    n = (max(laps) + 1) if laps else 0
    s.laps = type("L", (), {"laps_count": staticmethod(lambda n=n: n)})()
    return s


def loop_xy(n=120, scale=10.0, cx=0.0, cy=0.0):
    """A closed egg-shaped loop (no rotational symmetry) for the overlay-fit tests."""
    th = np.linspace(0, 2 * np.pi, n, endpoint=False)
    r = scale * (1.0 + 0.3 * np.cos(th) + 0.15 * np.sin(2 * th))
    return np.column_stack([cx + r * np.cos(th), cy + r * np.sin(th)])


# ---------------------------------------------------------------- the delta-endpoint contract
def test_delta_endpoint_equals_cross_recording_laptime_diff():
    # Primary best lap: 60.0 s over 1000 m. Reference (another recording): 58.0 s over 1040 m —
    # DIFFERENT length, so the normalized-distance alignment is what makes the endpoint right.
    p_times, p_dists = odometer(200, 0.30, 0.0, 1000.0)     # 199*0.30 = 59.7 s span
    primary = make_session({3: (p_times, p_dists)}, best=3, valid=[3])
    p_lap_time = float(p_times[-1] - p_times[0])

    r_times, r_dists = odometer(180, 0.34, 100.0, 1040.0)   # 179*0.34 = 60.86 s, anchored != 0
    ref = make_session({7: (r_times, r_dists)}, best=7, valid=[7])
    r_lap_time = float(r_times[-1] - r_times[0])
    # The reference loop fit isn't needed for the data path; stub the primary loop fetch to None
    # so build() simply produces no overlay (the charts/table don't depend on it).
    primary._reference_fit_loop = lambda: None
    ref.lap_trace_xy = lambda _lid: (np.zeros(0), np.zeros(0))  # < 10 pts -> overlay None

    assert primary.set_reference_session(ref, source_label="friend") is None
    assert primary.has_reference()
    assert primary.reference_label() == "friend"
    assert abs(primary.reference_lap_time() - r_lap_time) < 1e-9

    expected = p_lap_time - r_lap_time
    base_id, speed, delta = primary.delta([3], x_mode="distance")
    assert base_id == REFERENCE_ID
    assert REFERENCE_ID in delta and REFERENCE_ID in speed  # reference curve emitted
    endpoint = float(delta[3][1][-1])
    assert abs(endpoint - expected) < 1e-6, (endpoint, expected)
    # The reference's self-delta is the flat-zero green baseline.
    assert abs(float(delta[REFERENCE_ID][1][-1])) < 1e-9
    # Time mode endpoint is identical (only the x basis differs).
    _b, _s, delta_t = primary.delta([3], x_mode="time")
    assert abs(float(delta_t[3][1][-1]) - endpoint) < 1e-9
    print(f"test_delta_endpoint OK: endpoint={endpoint:+.4f}s == "
          f"(primary {p_lap_time:.3f} - reference {r_lap_time:.3f})")


def test_delta_at_lap_uses_reference_baseline():
    # The per-tick readout (delta_at_lap) must use the SAME reference baseline as the chart.
    p_times, p_dists = odometer(150, 0.40, 0.0, 900.0)
    primary = make_session({2: (p_times, p_dists)}, best=2, valid=[2])
    r_times, r_dists = odometer(150, 0.40, 0.0, 900.0)   # identical curve except scaled time
    r_times = r_times * 0.97  # reference is ~3% faster everywhere
    ref = make_session({5: (r_times, r_dists)}, best=5, valid=[5])
    primary._reference_fit_loop = lambda: None
    ref.lap_trace_xy = lambda _lid: (np.zeros(0), np.zeros(0))
    assert primary.set_reference_session(ref) is None

    # delta_at_lap at the finish == primary lap time - reference lap time (same as the endpoint).
    expected = float(p_times[-1] - p_times[0]) - float(r_times[-1] - r_times[0])
    d = primary.delta_at_lap(2, float(p_times[-1]))
    assert d is not None and abs(d - expected) < 1e-6, (d, expected)
    # Mid-lap it's a positive (behind) value since the reference is faster throughout.
    mid = primary.delta_at_lap(2, float(p_times[len(p_times) // 2]))
    assert mid is not None and mid > 0
    print(f"test_delta_at_lap OK: finish Δ={d:+.4f}s, mid Δ={mid:+.4f}s")


# ---------------------------------------------------------------- the guards
def test_track_mismatch_guard_refuses_and_keeps_local_best():
    p_times, p_dists = odometer(100, 0.5, 0.0, 800.0)
    primary = make_session({1: (p_times, p_dists)}, best=1, valid=[1], track="Track A")
    ref = make_session({1: odometer(100, 0.5, 0.0, 800.0)}, best=1, valid=[1], track="Track B")
    reason = primary.set_reference_session(ref)
    assert reason is not None and "different track" in reason, reason
    assert not primary.has_reference()
    # The local best is untouched: delta() still baselines on the local best lap.
    base_id, _s, _d = primary.delta([1], x_mode="distance")
    assert base_id == 1
    print(f"test_track_mismatch_guard OK: refused with {reason!r}")


def test_unknown_track_refused():
    # If EITHER side has an unknown track (name None) the match can't be proven -> refuse.
    p_times, p_dists = odometer(100, 0.5, 0.0, 800.0)
    primary = make_session({1: (p_times, p_dists)}, best=1, valid=[1], track="Track A")
    ref = make_session({1: odometer(100, 0.5, 0.0, 800.0)}, best=1, valid=[1], track=None)
    reason = primary.set_reference_session(ref)
    assert reason is not None and "different track" in reason
    assert not primary.has_reference()
    print("test_unknown_track_refused OK")


def test_no_valid_laps_reference_refused():
    p_times, p_dists = odometer(100, 0.5, 0.0, 800.0)
    primary = make_session({1: (p_times, p_dists)}, best=1, valid=[1], track="Track A")
    ref = make_session({}, best=None, valid=[], track="Track A")
    reason = primary.set_reference_session(ref)
    assert reason is not None and "no valid laps" in reason, reason
    assert not primary.has_reference()
    print("test_no_valid_laps_reference_refused OK")


def test_clear_reverts_to_own_best():
    p_times, p_dists = odometer(120, 0.45, 0.0, 950.0)
    primary = make_session({4: (p_times, p_dists)}, best=4, valid=[4])
    ref = make_session({8: odometer(120, 0.45, 0.0, 980.0)}, best=8, valid=[8])
    primary._reference_fit_loop = lambda: None
    ref.lap_trace_xy = lambda _lid: (np.zeros(0), np.zeros(0))
    primary.set_reference_session(ref)
    assert primary.has_reference()
    # Capture the dormant baseline FIRST (before ever loading a reference) for an exact compare.
    primary.clear_reference()
    assert not primary.has_reference()
    base_id, _s, delta = primary.delta([4], x_mode="distance")
    assert base_id == 4  # back to the local best lap
    assert abs(float(delta[4][1][-1])) < 1e-9  # best vs itself == 0
    assert REFERENCE_ID not in delta  # the reference curve is gone
    print("test_clear_reverts_to_own_best OK")


# ---------------------------------------------------------------- DORMANT identity
def test_dormant_delta_is_byte_identical():
    # With NO reference, delta() must equal a hand-rolled normalized-distance delta-to-best — the
    # "no change when off" invariant, proven numerically on a bare Session.
    a_times, a_dists = odometer(140, 0.5, 0.0, 1000.0, profile=lambda u: 1.0 + np.sin(u) ** 2)
    b_times, b_dists = odometer(120, 0.55, 0.0, 1010.0, profile=lambda u: 1.2 + np.cos(u) ** 2)
    s = make_session({1: (a_times, a_dists), 2: (b_times, b_dists)}, best=1, valid=[1, 2])
    assert not s.has_reference()
    base_id, speed, delta = s.delta([1, 2], x_mode="distance")
    assert base_id == 1 and REFERENCE_ID not in delta

    # Reference computation: align both laps on the SAME normalized grid vs the best (lap 1).
    N = Session._DELTA_GRID_N
    grid = np.linspace(0.0, 1.0, N)
    best_dist = a_dists
    best_elapsed = a_times - a_times[0]
    best_on_grid = np.interp(grid, best_dist / best_dist[-1], best_elapsed)
    for lid, (times, dists) in {1: (a_times, a_dists), 2: (b_times, b_dists)}.items():
        elapsed = times - times[0]
        on_grid = np.interp(grid, dists / dists[-1], elapsed)
        want = on_grid - best_on_grid
        got = delta[lid][1]
        assert np.allclose(got, want, atol=0, rtol=0), (lid, np.abs(got - want).max())
    print("test_dormant_delta_is_byte_identical OK")


# ---------------------------------------------------------------- the overlay fit gate
def test_overlay_fits_good_loop_and_drops_gross_misfit():
    dist, speed, elapsed = (np.linspace(0, 1000, 50), np.full(50, 50.0), np.linspace(0, 60, 50))
    # Realistic track scale (~100 m) so the metre tolerance is meaningful.
    primary_loop = loop_xy(scale=100.0)
    # A reference loop that is a SIMILARITY-transform of the primary (rotated/scaled/translated):
    # the closed-loop fit must recover it and the overlay lands close (low RMS).
    th = 0.7
    R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    ref_loop = (loop_xy(scale=100.0) * 1.4) @ R.T + np.array([300.0, -120.0])
    ref = xr.build(dist=dist, speed_kmh=speed, elapsed=elapsed, loop_xy=ref_loop,
                   primary_loop_xy=primary_loop, source_label="r", lap_id=0)
    assert ref.overlay_xy is not None, "a similarity-transformed loop must overlay"
    assert ref.map_fit_rms is not None and ref.map_fit_rms < xr.MAP_FIT_RMS_TOL_M
    # The fitted overlay sits in the PRIMARY frame (near the primary loop, not the ref's).
    assert abs(ref.overlay_xy[:, 0].max()) < primary_loop[:, 0].max() * 3

    # A genuinely DIFFERENT track shape — a long thin rectangle (200×40 m) has straights + sharp
    # corners that no similarity transform of the round egg loop can follow, so the fit RMS blows
    # past the tolerance and NO overlay is drawn. The DATA side stays intact (arrays/total_time
    # present), so the distance-aligned charts/table reference still works.
    def rect(w, h, n=120):
        per = 2 * (w + h)
        pts = []
        for d in np.linspace(0, per, n, endpoint=False):
            if d < w:                       # bottom edge, left -> right
                pts.append((d - w / 2, -h / 2))
            elif d < w + h:                 # right edge, bottom -> top
                pts.append((w / 2, d - w - h / 2))
            elif d < 2 * w + h:             # top edge, right -> left
                pts.append((w / 2 - (d - w - h), h / 2))
            else:                           # left edge, top -> bottom
                pts.append((-w / 2, h / 2 - (d - 2 * w - h)))
        return np.asarray(pts, float)
    bad = xr.build(dist=dist, speed_kmh=speed, elapsed=elapsed, loop_xy=rect(200.0, 40.0),
                   primary_loop_xy=primary_loop, source_label="r", lap_id=0)
    assert bad.overlay_xy is None, (bad.map_fit_rms,)
    assert bad.map_fit_rms is not None and bad.map_fit_rms > xr.MAP_FIT_RMS_TOL_M
    assert bad.total_time == elapsed[-1] and len(bad.arrays()[0]) == 50
    print(f"test_overlay_fit_gate OK: good rms={ref.map_fit_rms:.2f}m, "
          f"misfit rms={bad.map_fit_rms:.1f}m dropped")


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print("\nALL CROSS-RECORDING REFERENCE TESTS PASSED")
