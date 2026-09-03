"""Cross-recording reference lap (F7) — pure-Python unit tests.

No telemetry file, no pacer build dependency for the logic covered: a synthetic SECOND Session
(bare, seeded via tests/_synthetic) is adopted as the reference for a primary bare Session, and
the asserts are the contract the feature must hold:

  * delta endpoint with a reference active == (primary lap time − reference lap time), aligned by
    NORMALIZED distance, even when the two laps have different total distances (different recordings);
  * the same baseline drives the per-tick readout (delta_at_lap) and both x-axis modes;
  * the track-mismatch guard refuses a foreign-track reference (and a no-valid-laps reference)
    without disturbing the local best;
  * the LAP-LENGTH band refuses a same-track recording segmented into laps of a different length
    (QA-W2R-02) while still admitting a genuinely comparable one;
  * the IDENTITY guard refuses the recording already open as its own reference (QA-W2R-04) — every
    other guard passes there — while admitting a different recording, and `reference_is_own_-
    recording` reports that state for the surfaces downstream of the baseline;
  * clear_reference reverts to the local best;
  * DORMANT identity: with no reference, delta() is byte-identical to the pre-feature output (the
    "no change when off" invariant), checked here on a bare Session against a hand-computed baseline;
  * the cross_reference.build map-overlay fit gate (a good fit overlays, a gross mis-fit is dropped
    but the data side still works), including its SCALE half (QA-W2R-06): the fit may resize the
    reference loop, so a wrongly-SIZED reference passes the RMS check and must be refused anyway.

Run:  python tests/test_cross_reference.py
"""
import os
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio import cross_reference as xr  # noqa: E402
from studio._signal import LAP_DIST_BAND_HI, LAP_DIST_BAND_LO  # noqa: E402
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


# ------------------------------------- lap-length band (the same track, segmented differently)
# The track gate above only proves both recordings are the same CIRCUIT. A recording whose start
# line lands somewhere the driver crosses several times a lap is segmented into short fragments,
# and its "lap" is then stretched over a full lap of the primary by the normalized-distance
# alignment — the length difference surfacing as seconds gained. So the reference lap must ALSO
# be the same LENGTH as a lap here: within `_band_lap_ids`' own ±10% distance band around this
# session's MEDIAN valid-lap distance. These stub `laps.get_lap_distance` (the accessor
# `_band_lap_ids` getattr-guards) so the band runs without a telemetry file.
def set_lap_distances(session, dists_by_id):
    """Give a bare Session per-lap odometer distances, WITHOUT a telemetry file: add a
    `get_lap_distance` accessor to the existing `laps` stub, preserving whatever `laps_count` /
    `min_max` stubs make_session / set_location already installed. Call it AFTER `set_location`
    — that one REBUILDS `laps` from scratch and would drop the accessor."""
    keep = {n: staticmethod(getattr(session.laps, n))
            for n in ("laps_count", "min_max") if hasattr(session.laps, n)}
    keep["get_lap_distance"] = staticmethod(lambda i, d=dict(dists_by_id): float(d[i]))
    session.laps = type("L", (), keep)()


def test_lap_length_band_refuses_a_differently_segmented_same_track_reference():
    """QA-W2R-02. F.D's auto-fitted start line cuts Sandown into ~203 m fragments; F.C's cuts the
    SAME circuit into 740 m laps. track_match calls them the same place (4.12 m apart), so the
    track gate admits — and on main the 739.9 m lap becomes the Δ baseline for 13 s laps, plotting
    the session's OWN best at −35 s. The lap-length band must refuse instead, naming BOTH lengths."""
    # Five primary laps at F.D's measured scale: median 202 m, best (197 m) deliberately NOT the
    # median, so a guard anchored on the best would answer with a different number than this one.
    lap_m = {1: 199.0, 2: 202.0, 3: 203.0, 4: 197.0, 5: 207.0}
    laps = {i: odometer(120, 0.11, 0.0, m) for i, m in lap_m.items()}
    primary = make_session(laps, best=4, valid=sorted(lap_m), track=None)
    set_location(primary, clat=51.37604, clon=-0.36106)          # F.D's measured centroid
    set_lap_distances(primary, lap_m)
    # The reference: F.C's real best lap, 739.9 m / 48.515 s, on the same circuit.
    ref = make_session({30: odometer(400, 0.1215, 0.0, 739.9)}, best=30, valid=[30], track=None)
    set_location(ref, clat=51.37604, clon=-0.36096)              # F.C's, ~7 m away
    set_lap_distances(ref, {30: 739.9})
    _stub_loops(primary, ref)
    assert primary._track_admits_reference(ref)[0], "the track gate alone still admits this pair"

    reason = primary.set_reference_session(ref, source_label="friend")
    assert reason is not None, "a 3.7x-length reference must be refused"
    assert "202 m" in reason and "740 m" in reason, f"both lengths must be named: {reason!r}"
    assert not primary.has_reference()
    # The local best lap is untouched: Δ still baselines on lap 4, and the session's own best
    # plots at a flat zero rather than the −35 s of the mis-adopted reference.
    base_id, _speed, delta = primary.delta([4], x_mode="distance")
    assert base_id == 4 and REFERENCE_ID not in delta
    assert abs(float(delta[4][1][-1])) < 1e-9
    print(f"test_lap_length_band_refuses OK: refused with {reason!r}")


def test_lap_length_band_admits_a_genuine_same_track_recording():
    """The band must NOT refuse two recordings of the same circuit that are both segmented sanely
    — measured, that pair's lap lengths agree to ~1.4%. These are the real figures from two
    independent Sandown recordings: primary laps 731.9 / 740.5 / 754.2 m (median 740.5) against
    the other recording's best lap of 730.3 m, i.e. 0.986x."""
    lap_m = {1: 731.9, 2: 740.5, 3: 754.2}
    primary = make_session({i: odometer(200, 0.24, 0.0, m) for i, m in lap_m.items()},
                           best=2, valid=sorted(lap_m), track=None)
    ref = make_session({31: odometer(200, 0.236, 0.0, 730.3)}, best=31, valid=[31], track=None)
    set_location(primary, clat=51.37604, clon=-0.36096)
    set_location(ref, clat=51.37604, clon=-0.36101)
    set_lap_distances(primary, lap_m)
    set_lap_distances(ref, {31: 730.3})
    _stub_loops(primary, ref)

    reason = primary.set_reference_session(ref, source_label="friend")
    assert reason is None, f"a genuine same-track pair must still be admitted: {reason!r}"
    assert primary.has_reference() and primary.reference_lap_id() == 31
    print("test_lap_length_band_admits_genuine_same_track OK: 730.3 m vs 740.5 m median admitted")


def test_lap_length_band_edges_are_the_local_band():
    """The threshold IS `_band_lap_ids`' distance band, so a reference lap is admitted exactly
    when this session would have counted it as one of its own laps: 1.09x in, 1.11x out."""
    lap_m = {1: 990.0, 2: 1000.0, 3: 1010.0}      # median 1000 m
    base = {i: odometer(150, 0.4, 0.0, m) for i, m in lap_m.items()}
    for total, want_ok in ((1090.0, True), (1110.0, False), (910.0, True), (890.0, False)):
        primary = make_session(base, best=2, valid=sorted(lap_m), track="T")
        set_lap_distances(primary, lap_m)
        ref = make_session({9: odometer(150, 0.4, 0.0, total)}, best=9, valid=[9], track="T")
        set_lap_distances(ref, {9: total})
        _stub_loops(primary, ref)
        reason = primary.set_reference_session(ref)
        assert (reason is None) is want_ok, (total, reason)
        assert primary.has_reference() is want_ok
    print(f"test_lap_length_band_edges OK: band is "
          f"[{LAP_DIST_BAND_LO:.2f}, {LAP_DIST_BAND_HI:.2f}]x the median valid-lap distance")


def test_lap_length_band_is_skipped_when_no_per_lap_distances_exist():
    """No `get_lap_distance` (the lighter test doubles, and any stream reporting no usable
    distance) => no median to band against => admit, exactly as before the band existed. The
    same getattr fallback `_band_lap_ids` takes, so the guard can never refuse on missing data."""
    primary = make_session({1: odometer(120, 0.4, 0.0, 900.0)}, best=1, valid=[1], track="T")
    assert not hasattr(primary.laps, "get_lap_distance")
    assert primary._median_valid_lap_distance() is None
    ref = make_session({2: odometer(120, 0.4, 0.0, 3000.0)}, best=2, valid=[2], track="T")
    _stub_loops(primary, ref)
    assert primary.set_reference_session(ref) is None, "unbandable data must not be refused"
    assert primary.has_reference()
    print("test_lap_length_band_skipped_without_distances OK")


# ------------------------------------------------- QA-W2R-04: a recording as its OWN reference
def give_paths(session, *paths):
    """Give a bare Session the on-disk provenance `Session._source_paths` reads: a ChapterMap-shaped
    stub (one Chapter per path) plus the first path as `video_path`, the way `Session.load` leaves
    them. Paths need not exist — the guard compares realpaths, not file contents."""
    session.chapters = SimpleNamespace(
        chapters=[SimpleNamespace(path=p) for p in paths])
    session.video_path = paths[0] if paths else None
    return session


def _comparable_pair(track="Track A"):
    """A primary + a reference that pass every OTHER guard (same track name, same lap length, a
    fittable loop) — so any refusal below can only be the identity guard."""
    primary = make_session({4: odometer(120, 0.45, 0.0, 950.0)}, best=4, valid=[4], track=track)
    ref = make_session({4: odometer(120, 0.45, 0.0, 950.0)}, best=4, valid=[4], track=track)
    _stub_loops(primary, ref)
    return primary, ref


def test_a_recording_is_refused_as_its_own_reference():
    """QA-W2R-04. The picker lists every .MP4 on disk including the one already open, and
    `chapters.discover_siblings` expands a picked chapter to the SAME chain the session was loaded
    from — so one wrong click makes a session its own Δ baseline. Every other guard PASSES there
    (identical track, identical lap length), and the result is a lap compared with itself dressed
    as a measurement. It must be refused, and the refusal must SAY which mistake it was."""
    primary, ref = _comparable_pair()
    give_paths(primary, "/recordings/GX010062.MP4", "/recordings/GX020062.MP4")
    give_paths(ref, "/recordings/GX010062.MP4", "/recordings/GX020062.MP4")
    reason = primary.set_reference_session(ref, source_label="recording 0062 · 2 chapters")
    assert reason is not None, "a recording must not be its own reference"
    assert "already have open" in reason, reason
    assert not primary.has_reference(), "the refusal must leave the local best lap in place"
    assert primary.reference_session() is None
    print(f"test_a_recording_is_refused_as_its_own_reference OK: refused with {reason!r}")


def test_a_partial_chapter_overlap_is_the_same_recording():
    """Opening chapter 1 alone and referencing the whole three-chapter chain (or the reverse) is
    still this recording against itself over the part they share — the guard is an OVERLAP test,
    not set equality. Symmetric, and blind to how the path was spelled: a relative path, a symlink
    directory and an absolute one all name the same file."""
    for mine, theirs in ((["/rec/GX010062.MP4"],
                          ["/rec/GX010062.MP4", "/rec/GX020062.MP4", "/rec/GX030062.MP4"]),
                         (["/rec/GX010062.MP4", "/rec/GX020062.MP4"],
                          ["/rec/GX020062.MP4"]),
                         (["/rec/GX010062.MP4"], ["/rec/./sub/../GX010062.MP4"])):
        primary, ref = _comparable_pair()
        give_paths(primary, *mine)
        give_paths(ref, *theirs)
        reason = primary.set_reference_session(ref)
        assert reason is not None and "already have open" in reason, (mine, theirs, reason)
        assert not primary.has_reference()
    print("test_a_partial_chapter_overlap_is_the_same_recording OK")


def test_a_different_recording_of_the_same_track_is_still_admitted():
    """The guard must cost the feature nothing: a DIFFERENT recording of the same circuit — the
    whole point of a cross-recording reference — is admitted exactly as before, and so is a pair
    with no known provenance at all (the synthetic doubles), which must never be ASSUMED to
    collide."""
    primary, ref = _comparable_pair()
    give_paths(primary, "/recordings/GX010062.MP4")
    give_paths(ref, "/recordings/GX010059.MP4")
    assert primary.set_reference_session(ref, source_label="recording 0059") is None
    assert primary.has_reference() and primary.reference_lap_id() == 4

    bare_primary, bare_ref = _comparable_pair()
    assert not bare_primary._source_paths() and not bare_ref._source_paths()
    assert bare_primary.set_reference_session(bare_ref) is None, "unknown provenance must admit"
    assert bare_primary.has_reference()
    print("test_a_different_recording_of_the_same_track_is_still_admitted OK")


def test_reference_is_own_recording_reports_the_state_the_ui_asks_about():
    """The predicate the Corners dashes and the compare same-lap badge consult instead of ASSUMING
    a reference is a different recording (QA-W2R-04's downstream half). False for a normal
    reference, False when nothing is loaded, True when the retained reference Session shares this
    session's footage."""
    primary, ref = _comparable_pair()
    give_paths(primary, "/recordings/GX010062.MP4")
    give_paths(ref, "/recordings/GX010059.MP4")
    assert not primary.reference_is_own_recording(), "dormant: no reference at all"
    assert primary.set_reference_session(ref) is None
    assert not primary.reference_is_own_recording(), "a different recording is not our own"
    # Re-point the retained reference Session's provenance at ours: the predicate is live, so the
    # UI can never be told "different recording" about footage that is in fact this one.
    give_paths(ref, "/recordings/GX010062.MP4")
    assert primary.reference_is_own_recording()
    primary.clear_reference()
    assert not primary.reference_is_own_recording(), "cleared: nothing to be our own"
    print("test_reference_is_own_recording_reports_the_state_the_ui_asks_about OK")


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
    # A reference loop in ANOTHER recording's frame — rotated and translated away, but the same
    # TRUE SIZE (both frames are metres, so a same-circuit lap is the same size): the closed-loop
    # fit must recover the rotation/offset and the overlay lands close (low RMS, scale ~1).
    th = 0.7
    R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    ref_loop = loop_xy(scale=100.0) @ R.T + np.array([300.0, -120.0])
    ref = xr.build(dist=dist, speed_kmh=speed, elapsed=elapsed, loop_xy=ref_loop,
                   primary_loop_xy=primary_loop, source_label="r", lap_id=0)
    assert ref.overlay_xy is not None, "a rotated/translated true-size loop must overlay"
    assert ref.map_fit_rms is not None and ref.map_fit_rms < xr.MAP_FIT_RMS_TOL_M
    assert ref.map_fit_scale is not None and abs(ref.map_fit_scale - 1.0) < 0.01, ref.map_fit_scale
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


def test_overlay_gate_refuses_a_mis_sized_reference():
    """QA-W2R-06: the overlay gate must weigh the fitted SCALE, not only the residual.

    `fit_loop_to_loop` solves a SIMILARITY transform, so uniform scale is a free parameter of the
    very fit the RMS scores: hand it a reference loop of the wrong SIZE and it shrinks (or grows)
    the loop onto the primary until the residual is tiny, and an RMS-only gate sees nothing wrong.
    Measured in the app on real recordings, a 190.6 x 124.8 m reference lap was drawn at
    54.2 x 72.1 m — scale 0.403 — for an rms of 4.33 m, a third of MAP_FIT_RMS_TOL_M.

    Both directions are checked (the same pair fits at s one way and 1/s the other, and the
    verdict must not depend on which recording the user loaded as the reference), and the band is
    checked from BELOW too: the widest genuine same-circuit lap-to-lap fit measured over 163 real
    laps was x1.038, which must still overlay."""
    from studio import reference as refmod
    dist, speed, elapsed = (np.linspace(0, 1000, 50), np.full(50, 50.0), np.linspace(0, 60, 50))
    primary_loop = loop_xy(scale=100.0)
    th = 0.7
    R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])

    def build_at(size, label):
        """A reference loop of the SAME shape at `size` x the primary's, in its own frame."""
        ref_loop = (loop_xy(scale=100.0) * size) @ R.T + np.array([300.0, -120.0])
        got = xr.build(dist=dist, speed_kmh=speed, elapsed=elapsed, loop_xy=ref_loop,
                       primary_loop_xy=primary_loop, source_label=label, lap_id=0)
        # Re-derive the scale from the fit itself, so this test states its own evidence rather
        # than trusting the field it is here to add.
        _fitted, info = refmod.fit_loop_to_loop(ref_loop, primary_loop)
        return got, float(info["scale"]), float(info["rms"])

    # 1. The filed case: the reference lap is 2.49x too big, so the fit draws it at 0.40x its true
    #    size. The RMS gate is delighted — the shrunk ring lies right on the primary loop.
    big, scale, rms = build_at(2.49, "too big")
    assert rms < xr.MAP_FIT_RMS_TOL_M, (rms, "this case is only interesting if the RMS passes")
    assert big.map_fit_rms is not None and big.map_fit_rms <= xr.MAP_FIT_RMS_TOL_M
    assert big.overlay_xy is None, (
        f"a reference loop 2.49x too big was accepted and drawn at {scale:.3f}x its true size "
        f"(fit rms {rms:.2f} m, well inside the {xr.MAP_FIT_RMS_TOL_M} m gate) — the map would "
        f"show a false racing line whose metres are not the map's metres")
    assert big.map_fit_scale is not None and abs(big.map_fit_scale - scale) < 1e-9
    # The DATA side is untouched by a refused overlay (charts/table align by distance).
    assert big.total_time == elapsed[-1] and len(big.arrays()[0]) == 50

    # 2. The mirror: a reference 2.49x too SMALL is grown onto the primary. Same verdict — the
    #    gate must not depend on which recording the user picked as the reference.
    small, scale_s, rms_s = build_at(1 / 2.49, "too small")
    assert rms_s < xr.MAP_FIT_RMS_TOL_M, rms_s
    assert small.overlay_xy is None, (
        f"a reference loop 2.49x too small was accepted and grown {scale_s:.2f}x onto the "
        f"primary (fit rms {rms_s:.2f} m)")

    # 3. NOT over-tightened. The worst genuine same-circuit lap-to-lap fit measured over 163 real
    #    laps (3 recordings, 2 circuits) was x1.0376 — it must still overlay, in both directions.
    for size in (1.0376, 1 / 1.0376):
        ok, sc, rm = build_at(size, "genuine")
        assert ok.overlay_xy is not None, (
            f"a genuine same-circuit reference at {size:.4f}x (fit scale {sc:.4f}, rms {rm:.2f} m) "
            f"was refused — the band is tighter than real GPS")
        assert ok.map_fit_scale is not None

    # 4. The band itself, stated once on the predicate the gate uses.
    tol = xr.MAP_FIT_SCALE_TOL
    assert xr.fit_is_drawable(1.0, 1.0) and xr.fit_is_drawable(1.0, 1 / 1.05)
    assert xr.fit_is_drawable(1.0, tol) and xr.fit_is_drawable(1.0, 1 / tol), "the edges are IN"
    assert not xr.fit_is_drawable(1.0, tol * 1.01), "outside the band, however good the RMS"
    assert not xr.fit_is_drawable(1.0, 1 / (tol * 1.01))
    assert not xr.fit_is_drawable(xr.MAP_FIT_RMS_TOL_M + 0.1, 1.0), "the RMS half still bites"
    assert not xr.fit_is_drawable(1.0, 0.0) and not xr.fit_is_drawable(1.0, float("nan"))
    assert not xr.fit_is_drawable(float("nan"), 1.0) and not xr.fit_is_drawable(None, None)
    print(f"test_overlay_gate_refuses_a_mis_sized_reference OK: 2.49x too big -> scale "
          f"{scale:.4f} rms {rms:.2f} m REFUSED (RMS alone would have drawn it); 2.49x too small "
          f"-> scale {scale_s:.4f} REFUSED; x1.0376 (the worst real lap) still drawn; band "
          f"[{1/tol:.4f}, {tol:.4f}]")


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print("\nALL CROSS-RECORDING REFERENCE TESTS PASSED")
