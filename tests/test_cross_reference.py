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


class _Pt:
    """A min_max() corner point: .x = LONGITUDE, .y = LATITUDE (the pacer Point convention that
    Session.track_location() reads — see load.py `clat,clon = (mn.y+mx.y)/2, (mn.x+mx.x)/2`)."""

    def __init__(self, lon, lat):
        self.x, self.y = float(lon), float(lat)


def set_location(session, *, clat, clon, half_lat=0.0009, half_lon=0.0009):
    """Give a bare Session a GPS footprint so `track_location()` (and the geometry track-match) can
    read it, WITHOUT a telemetry file: stub `laps.min_max()` to return the bbox corners centred on
    (clat, clon) with the given half-extents (degrees). Keeps the existing `laps_count` stub so the
    delta range checks still pass. ~0.0009° ≈ 100 m, a kart-track scale by default."""
    laps_count = session.laps.laps_count  # preserve the delta() range-check stub already installed
    mn = _Pt(clon - half_lon, clat - half_lat)
    mx = _Pt(clon + half_lon, clat + half_lat)
    session.laps = type("L", (), {
        "laps_count": staticmethod(laps_count),
        "min_max": staticmethod(lambda mn=mn, mx=mx: (mn, mx)),
    })()


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


# ----------------------------------------- geometry track-match fallback (unknown track)
# When EITHER side has no detected track NAME (the common case — the DB ships ~one track), the
# gate can no longer compare names, so it falls back to GPS GEOMETRY: same location (haversine
# centroid distance) AND comparable footprint size => admit as UNVERIFIED, else refuse. These
# stub `laps.min_max()` (via set_location) + `track_name` so the match runs without a telemetry
# file. A geometry-admitted reference must ALSO be flagged geometric (reference_match_is_geometric).
def _stub_loops(primary, ref):
    """The overlay-fit inputs aren't what these gate tests exercise; stub them so build() runs but
    produces no overlay (identical to the existing delta-endpoint tests)."""
    primary._reference_fit_loop = lambda: None
    ref.lap_trace_xy = lambda _lid: (np.zeros(0), np.zeros(0))


def test_geometry_match_allows_and_flags_unverified():
    # Two UNKNOWN-track recordings whose centroids nearly coincide (a few metres apart) and whose
    # footprints are the same size -> same circuit -> ALLOW, flagged UNVERIFIED (geometry match).
    p_times, p_dists = odometer(120, 0.4, 0.0, 900.0)
    primary = make_session({1: (p_times, p_dists)}, best=1, valid=[1], track=None)
    ref = make_session({2: odometer(120, 0.4, 0.0, 900.0)}, best=2, valid=[2], track=None)
    set_location(primary, clat=52.0403, clon=-0.7847)
    set_location(ref, clat=52.04035, clon=-0.78475)  # ~6 m away, same ~100 m footprint
    _stub_loops(primary, ref)
    reason = primary.set_reference_session(ref, source_label="friend")
    assert reason is None, reason
    assert primary.has_reference()
    assert primary.reference_match_is_geometric(), "a location-only match must be flagged unverified"
    assert primary.reference_label() == "friend"
    print("test_geometry_match_allows_and_flags_unverified OK")


def test_geometry_match_far_apart_refuses():
    # Same unknown-track setup but the centroids are ~30 km apart (different circuits) -> REFUSE.
    p_times, p_dists = odometer(120, 0.4, 0.0, 900.0)
    primary = make_session({1: (p_times, p_dists)}, best=1, valid=[1], track=None)
    ref = make_session({2: odometer(120, 0.4, 0.0, 900.0)}, best=2, valid=[2], track=None)
    set_location(primary, clat=52.0403, clon=-0.7847)
    set_location(ref, clat=52.30, clon=-0.7847)  # ~0.26° lat ≈ 29 km north
    _stub_loops(primary, ref)
    reason = primary.set_reference_session(ref)
    assert reason is not None and "different location" in reason, reason
    assert not primary.has_reference()
    print("test_geometry_match_far_apart_refuses OK")


def test_geometry_match_same_centroid_different_size_refuses():
    # Centroids coincide but the footprints differ ~10× in extent (a tiny kart track vs a big
    # course sharing the centroid region) -> REFUSE (the size check).
    p_times, p_dists = odometer(120, 0.4, 0.0, 900.0)
    primary = make_session({1: (p_times, p_dists)}, best=1, valid=[1], track=None)
    ref = make_session({2: odometer(120, 0.4, 0.0, 900.0)}, best=2, valid=[2], track=None)
    set_location(primary, clat=52.0403, clon=-0.7847, half_lat=0.0009, half_lon=0.0009)   # ~100 m
    set_location(ref, clat=52.0403, clon=-0.7847, half_lat=0.009, half_lon=0.009)          # ~1 km
    _stub_loops(primary, ref)
    reason = primary.set_reference_session(ref)
    assert reason is not None and "different location" in reason, reason
    assert not primary.has_reference()
    print("test_geometry_match_same_centroid_different_size_refuses OK")


def test_both_named_same_track_allows_without_caveat():
    # BOTH sides carry the SAME detected track name -> confirmed match: ALLOW with NO caveat
    # (byte-identical to the pre-fallback behaviour; not flagged geometric).
    p_times, p_dists = odometer(120, 0.4, 0.0, 900.0)
    primary = make_session({1: (p_times, p_dists)}, best=1, valid=[1], track="Daytona MK")
    ref = make_session({2: odometer(120, 0.4, 0.0, 900.0)}, best=2, valid=[2], track="Daytona MK")
    _stub_loops(primary, ref)
    reason = primary.set_reference_session(ref)
    assert reason is None, reason
    assert primary.has_reference()
    assert not primary.reference_match_is_geometric(), "a confirmed named match carries NO caveat"
    print("test_both_named_same_track_allows_without_caveat OK")


def test_named_vs_different_named_still_refuses():
    # BOTH named but DIFFERENT names -> a name mismatch is authoritative -> REFUSE (no geometry).
    p_times, p_dists = odometer(120, 0.4, 0.0, 900.0)
    primary = make_session({1: (p_times, p_dists)}, best=1, valid=[1], track="Track A")
    ref = make_session({2: odometer(120, 0.4, 0.0, 900.0)}, best=2, valid=[2], track="Track B")
    # Even with coincident centroids, a name mismatch must win (never reaches geometry).
    set_location(primary, clat=52.0403, clon=-0.7847)
    set_location(ref, clat=52.0403, clon=-0.7847)
    _stub_loops(primary, ref)
    reason = primary.set_reference_session(ref)
    assert reason is not None and "different track" in reason, reason
    assert not primary.has_reference()
    print("test_named_vs_different_named_still_refuses OK")


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


# ----------------------------------------------- F7 Phase B: cross-recording VIDEO compare
def test_reference_session_retained_and_cleared():
    """Phase B keeps the LIVE reference Session alive (Phase A discarded it). It must be reachable
    via reference_session() after load, expose the reference lap id, and be dropped on clear."""
    p_times, p_dists = odometer(150, 0.40, 0.0, 900.0)
    primary = make_session({2: (p_times, p_dists)}, best=2, valid=[2])
    r_times, r_dists = odometer(150, 0.40, 0.0, 920.0)
    ref = make_session({5: (r_times, r_dists)}, best=5, valid=[5])
    primary._reference_fit_loop = lambda: None
    ref.lap_trace_xy = lambda _lid: (np.zeros(0), np.zeros(0))
    assert primary.set_reference_session(ref) is None
    assert primary.reference_session() is ref, "the live reference Session must be retained"
    assert primary.reference_lap_id() == 5, "pane B locks to the reference best lap"
    primary.clear_reference()
    assert primary.reference_session() is None, "clear must drop the live reference Session"
    assert primary.reference_lap_id() is None
    print("test_reference_session_retained_and_cleared OK")


def test_reference_delta_vs_lap_endpoint_is_negated_laptime_diff():
    """Pane B's badge = reference vs primary. The production contract is that `t_ref` is the
    reference recording's GLOBAL media clock (the reference lap sits at its lap_window start ≈
    1000 s here, NOT 0), so the method must REBASE it to seconds-into-the-reference-lap before the
    normalized-distance interp. The endpoint at the reference finish must equal
    (reference_time − primary_time) == −(pane A's endpoint), the cross-recording laptime diff; the
    MID-lap value must be the genuine mid delta, NOT the clamped finish delta — that mid assertion
    is what catches a global→into-lap regression (interp of a ~1000 s t_ref against a from-0 axis
    would clamp to the finish)."""
    p_times, p_dists = odometer(150, 0.40, 0.0, 900.0)
    primary = make_session({2: (p_times, p_dists)}, best=2, valid=[2])
    # The reference's curve is the primary's, elapsed scaled 0.97× (≈3 % faster everywhere) and
    # anchored at a GLOBAL window start of 1000 s — exactly the away-from-0 anchor a real reference
    # file has (its best lap sits ~1000 s into the recording, not at the media-clock origin).
    REF_START = 1000.0
    r_elapsed = (p_times - p_times[0]) * 0.97
    r_times = r_elapsed + REF_START
    ref = make_session({5: (r_times, p_dists.copy())}, best=5, valid=[5])
    ref.lap_window = lambda _lid, w=(float(r_times[0]), float(r_times[-1])): w  # GLOBAL clock window
    primary._reference_fit_loop = lambda: None
    ref.lap_trace_xy = lambda _lid: (np.zeros(0), np.zeros(0))
    assert primary.set_reference_session(ref) is None

    p_lap_time = float(p_times[-1] - p_times[0])
    r_lap_time = float(r_times[-1] - r_times[0])
    # delta_at_lap (pane A) at the primary finish == primary − reference (behind, positive).
    a_end = primary.delta_at_lap(2, float(p_times[-1]))
    # reference_delta_vs_lap (pane B) takes the GLOBAL reference clock; at the reference finish it is
    # reference − primary (ahead, negative). A clamp bug would also land here (s=1), so the mid is key.
    b_end = primary.reference_delta_vs_lap(2, float(r_times[-1]))
    assert a_end is not None and b_end is not None
    assert abs(a_end - (p_lap_time - r_lap_time)) < 1e-6, a_end
    assert abs(b_end - (r_lap_time - p_lap_time)) < 1e-6, b_end
    assert abs(a_end + b_end) < 1e-6, "pane A and pane B endpoints must be exact negatives"

    # MID-lap, on the GLOBAL clock (≈ 1000 + half the reference lap time). The reference's elapsed
    # is exactly 0.97× the primary's at the same track fraction s, so independently of the method:
    #   reference_delta = elapsed_ref(s) − elapsed_primary(s) = (0.97 − 1) × elapsed_primary(s).
    # We pick the global mid time, derive its s, and compute the expected delta by hand — a
    # clamp-to-finish regression would instead return b_end (the finish delta), failing this.
    t_mid = float(r_times[len(r_times) // 2])            # GLOBAL reference clock, mid lap
    t_into = t_mid - REF_START                            # seconds-into-the-reference-lap
    s_mid = float(np.interp(t_into, r_elapsed, p_dists)) / float(p_dists[-1])
    prim_elapsed_at_s = float(np.interp(s_mid * float(p_dists[-1]), p_dists, p_times - p_times[0]))
    want_mid = (0.97 - 1.0) * prim_elapsed_at_s          # reference − primary at the same s
    got_mid = primary.reference_delta_vs_lap(2, t_mid)
    assert got_mid is not None and abs(got_mid - want_mid) < 1e-6, (got_mid, want_mid)
    assert abs(got_mid - b_end) > 1e-3, "mid Δ must NOT equal the clamped finish Δ"
    assert want_mid < 0, "reference is faster, so the mid Δ (reference − primary) is negative"
    print(f"test_reference_delta_vs_lap OK: paneA={a_end:+.4f}s, paneB={b_end:+.4f}s, "
          f"mid={got_mid:+.4f}s (want {want_mid:+.4f}s, not the {b_end:+.4f}s finish)")


def test_reference_overlay_index_tracks_progress():
    """The cross-recording map ghost indexes the FITTED overlay ring by the reference lap's
    normalized progress at the GLOBAL reference clock `t_ref` — 0 at the start, the last index at
    the finish, monotone between. The reference window is anchored at 1000 s (away from 0), so this
    also proves the global→into-lap rebase: without it the ~1000 s t_ref would clamp every query to
    the finish index, and the mid assertion (0 < imid < m−1) would fail."""
    REF_START = 1000.0
    p_times, p_dists = odometer(150, 0.40, 0.0, 900.0)
    primary = make_session({2: (p_times, p_dists)}, best=2, valid=[2])
    r_times, r_dists = odometer(150, 0.40, REF_START, 900.0)  # GLOBAL clock anchored at 1000 s
    ref = make_session({5: (r_times, r_dists)}, best=5, valid=[5])
    ref.lap_window = lambda _lid, w=(float(r_times[0]), float(r_times[-1])): w
    # Give both a real, well-fitting loop so build() produces an overlay (the ghost line).
    primary._reference_fit_loop = lambda: loop_xy(scale=100.0)
    ref.lap_trace_xy = lambda _lid: (loop_xy(scale=100.0).T[0], loop_xy(scale=100.0).T[1])
    assert primary.set_reference_session(ref) is None
    assert primary.reference_overlay_xy() is not None, "a matching loop must overlay"
    m = len(primary.reference_overlay_xy())
    i0 = primary.reference_overlay_index_at_progress(float(r_times[0]))      # start (t_ref=1000)
    imid = primary.reference_overlay_index_at_progress(float(r_times[len(r_times) // 2]))
    i1 = primary.reference_overlay_index_at_progress(float(r_times[-1]))     # finish
    assert i0 == 0, i0
    assert i1 == m - 1, (i1, m)
    assert 0 < imid < m - 1, imid
    # Independent expectation for the mid index: rebase to into-lap, take s, index the ring — proves
    # the value isn't the clamped finish (which a global-clock-vs-from-0 interp would return).
    t_into = float(r_times[len(r_times) // 2]) - REF_START
    s_mid = float(np.interp(t_into, r_times - r_times[0], r_dists)) / float(r_dists[-1])
    want_mid = min(int(round(s_mid * (m - 1))), m - 1)
    assert imid == want_mid, (imid, want_mid)
    assert imid != i1, "mid index must NOT clamp to the finish index"
    print(f"test_reference_overlay_index OK: start={i0}, mid={imid} (want {want_mid}), "
          f"finish={i1} of {m}")


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
