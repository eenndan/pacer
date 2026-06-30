"""Tests for the timing-line sidecar (studio.sidecar + the Session lat/lon helpers).

The user's hand-tuned start/sector lines persist next to the MP4 as
``<first-chapter stem>.pacer.json`` in ABSOLUTE (lat, lon) — local metres would drift
because the local frame's origin (the cleaned-trace bbox centre) shifts between loads.

Covered here:
  * sidecar path resolution — non-GoPro stem, and a synthesized chaptered sibling set on
    disk resolving every chapter to the CHAPTER-1 stem (pure Python, tmp dir);
  * schema round-trip + float stability (json writes repr → bit-exact reload) and the
    corrupt/invalid-file → None contract (pure Python);
  * the Session export/apply round trip on a synthetic 3-lap pacer.Laps — identical valid
    laps and lap times, lat/lon export stable across apply→export→apply; and the REVERT
    GUARD: applying foreign lines (zero valid laps) restores the previous segmentation.

Run: python tests/test_sidecar.py   (pacer needed for the Session half; no Qt, no media)
"""
import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pacer  # noqa: E402
from studio import sidecar, tracks  # noqa: E402
from studio.session import Seg, Session  # noqa: E402

# ----------------------------------------------------------------- path resolution


def _touch(folder, name):
    with open(os.path.join(folder, name), "w") as f:
        f.write("")


def test_path_non_gopro_uses_own_stem():
    p = sidecar.sidecar_path("/some/dir/hero6.mp4")
    assert p == "/some/dir/hero6.pacer.json", p


def test_path_chaptered_resolves_to_chapter1_stem():
    """Every chapter of a recording maps to ONE sidecar: the chapter-1 stem. Synthesized
    sibling files on disk (discover_siblings really lists the folder)."""
    with tempfile.TemporaryDirectory() as d:
        for name in ("GX010062.MP4", "GX020062.MP4", "GX030062.MP4",
                     "GX010060.MP4", "notes.txt"):
            _touch(d, name)
        want = os.path.join(d, "GX010062.pacer.json")
        for chap in ("GX010062.MP4", "GX020062.MP4", "GX030062.MP4"):
            got = sidecar.sidecar_path(os.path.join(d, chap))
            assert got == want, (chap, got)
        # A different recording in the same folder keeps its own sidecar.
        assert sidecar.sidecar_path(os.path.join(d, "GX010060.MP4")) \
            == os.path.join(d, "GX010060.pacer.json")


def test_path_single_chapter_without_siblings():
    """A lone chapter-1 file (no siblings on disk) uses its own stem — the same path the
    full chaptered load resolves to, so both share the sidecar."""
    with tempfile.TemporaryDirectory() as d:
        _touch(d, "GX010060.MP4")
        assert sidecar.sidecar_path(os.path.join(d, "GX010060.MP4")) \
            == os.path.join(d, "GX010060.pacer.json")


# ------------------------------------------------------- schema round-trip / validation

_START = [[52.040310000000001, -0.78487000000000013], [52.04020, -0.78460]]
_SECTOR = [[52.039000000000001, -0.78300000000000003], [52.03910, -0.78310]]


def test_save_load_roundtrip_bit_exact():
    """json floats are written with repr — the shortest EXACT double representation — so a
    save→load round trip returns bit-identical endpoints (== on floats, no tolerance)."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "GX010060.pacer.json")
        sidecar.save(p, "Daytona Milton Keynes", _START, [_SECTOR])
        data = sidecar.load(p)
        assert data is not None
        assert data["version"] == 1
        assert data["track"] == "Daytona Milton Keynes"
        assert data["start"] == _START          # exact float equality
        assert data["sectors"] == [_SECTOR]
        # And a second save of the loaded data is byte-identical (fully stable on disk).
        p2 = os.path.join(d, "again.pacer.json")
        sidecar.save(p2, data["track"], data["start"], data["sectors"])
        with open(p) as f1, open(p2) as f2:
            assert f1.read() == f2.read()


def test_save_none_track_and_no_sectors():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "clip.pacer.json")
        sidecar.save(p, None, _START, [])
        data = sidecar.load(p)
        assert data["track"] is None and data["sectors"] == []
        # The raw file matches the documented schema keys exactly.
        with open(p) as f:
            raw = json.load(f)
        assert set(raw) == {"version", "track", "start", "sectors", "confirmed"}


def test_confirmed_roundtrip_and_legacy_default():
    """The timing-trust marker `confirmed` persists; a legacy sidecar written before the marker
    (no `confirmed` key) loads as confirmed=True — it could only have come from a user edit."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "GX010060.pacer.json")
        # Explicit confirmed=False survives the round trip.
        sidecar.save(p, None, _START, [], confirmed=False)
        data = sidecar.load(p)
        assert data["confirmed"] is False
        # Explicit confirmed=True (the save default) survives.
        sidecar.save(p, None, _START, [], confirmed=True)
        assert sidecar.load(p)["confirmed"] is True
        # A legacy file with NO `confirmed` key → defaults True (back-compat).
        legacy = f'{{"version": 1, "track": null, "start": {json.dumps(_START)}, "sectors": []}}'
        with open(p, "w") as f:
            f.write(legacy)
        assert sidecar.load(p)["confirmed"] is True
        # A non-bool `confirmed` is rejected like any other malformed field.
        bad = f'{{"version": 1, "start": {json.dumps(_START)}, "confirmed": "yes"}}'
        with open(p, "w") as f:
            f.write(bad)
        assert sidecar.load(p) is None


def test_load_missing_file_is_none():
    assert sidecar.load("/nonexistent/dir/GX010060.pacer.json") is None


def test_load_rejects_corrupt_and_invalid():
    """Every malformed shape → None (one contract: caller keeps the auto-fitted lines)."""
    start_json = json.dumps(_START)
    bad_bodies = [
        "{ not json",                                              # not JSON at all
        "[]",                                                      # not an object
        f'{{"version": 2, "start": {start_json}}}',                # unknown version
        '{"version": 1}',                                          # no start line
        '{"version": 1, "start": [[52.0, -0.78]]}',                # one endpoint only
        '{"version": 1, "start": [[52.0, -0.78], [52.0, -0.78], [52.0, -0.78]]}',
        '{"version": 1, "start": [[52.0, "x"], [52.0, -0.78]]}',   # non-numeric coord
        '{"version": 1, "start": [[true, -0.78], [52.0, -0.78]]}',  # bool is not a coord
        '{"version": 1, "start": [[NaN, -0.78], [52.0, -0.78]]}',  # non-finite
        '{"version": 1, "start": [[91.0, -0.78], [52.0, -0.78]]}',  # lat out of range
        '{"version": 1, "start": [[52.0, -181.0], [52.0, -0.78]]}',  # lon out of range
        f'{{"version": 1, "start": {start_json}, "sectors": [[[1.0, 2.0]]]}}',
        f'{{"version": 1, "start": {start_json}, "sectors": 3}}',
        f'{{"version": 1, "start": {start_json}, "track": 7}}',
    ]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "bad.pacer.json")
        for body in bad_bodies:
            with open(p, "w") as f:
                f.write(body)
            assert sidecar.load(p) is None, body


# ------------------------------------------- Session export/apply (pacer, synthetic laps)

_CLAT, _CLON = 52.0, -0.78
_RADIUS_M = 100.0
_PER_LAP = 314          # samples per lap @ 10 Hz → ~31.4 s laps (>= MIN_LAP_TIME, samples)
_N_LAPS = 3
_M_PER_DEG_LAT = 111_320.0
# Timing lines sit BETWEEN samples (half-sample offsets) — a trace vertex EXACTLY on a line
# is a known crossing-count edge case in the geometry core, and real GPS never lands a fix
# exactly on the line anyway.
_THETA_START = 2.0 * math.pi * (10.5 / _PER_LAP)
_THETA_SECTOR = 2.0 * math.pi * ((_PER_LAP // 2) + 0.5) / _PER_LAP


def _circle_gps(theta, radius=_RADIUS_M):
    lat = _CLAT + (radius * math.cos(theta)) / _M_PER_DEG_LAT
    lon = _CLON + (radius * math.sin(theta)) / (_M_PER_DEG_LAT * math.cos(math.radians(_CLAT)))
    return pacer.GPSSample(lat=lat, lon=lon, altitude=0.0, full_speed=20.0, ground_speed=20.0)


def _make_session() -> Session:
    """A real pacer.Laps driving N clean laps around a 100 m-radius circle, segmented by a
    start line crossing the circle at _THETA_START (between two samples) — the same
    construction order as Session.load (points → coordinate system → sectors → update)."""
    laps = pacer.Laps()
    n = _N_LAPS * _PER_LAP + 1
    for i in range(n):
        theta = 2.0 * math.pi * (i / _PER_LAP)
        laps.add_point(_circle_gps(theta), i * 0.1)
    mn, mx = laps.min_max()
    cs = pacer.CoordinateSystem(
        pacer.GPSSample(lat=(mn.y + mx.y) / 2, lon=(mn.x + mx.x) / 2, altitude=0))
    laps.set_coordinate_system(cs)
    session = Session(laps, cs, None)
    # Start line: radial segment straddling the circle (r-40 .. r+40) between two samples.
    a = cs.local(_circle_gps(_THETA_START, _RADIUS_M - 40.0))
    b = cs.local(_circle_gps(_THETA_START, _RADIUS_M + 40.0))
    session.set_timing_lines(Seg(a[0], a[1], b[0], b[1]), [])
    return session


def test_export_apply_roundtrip_preserves_segmentation():
    """export (cs.global_) → apply (cs.local) keeps the same valid laps + lap times, and the
    exported lat/lon is STABLE under apply→export→apply (so repeated app restarts can never
    walk the lines)."""
    session = _make_session()
    valid0 = session.valid_lap_ids()
    assert len(valid0) >= 2, valid0  # the synthetic track segments into real laps
    times0 = [session.lap_time(i) for i in valid0]

    start1, sectors1 = session.timing_lines_latlon()
    assert sectors1 == []
    for lat, lon in start1:  # sane absolute coordinates, near the synthetic centre
        assert abs(lat - _CLAT) < 0.01 and abs(lon - _CLON) < 0.01, (lat, lon)

    assert session.apply_timing_lines_latlon(start1, sectors1) is True
    assert session.valid_lap_ids() == valid0
    times1 = [session.lap_time(i) for i in valid0]
    for t0, t1 in zip(times0, times1, strict=True):
        assert abs(t0 - t1) < 1e-9, (t0, t1)  # the latlon round trip moves lines ~1e-10 m

    # Float stability: a second export must reproduce the first to far below GPS noise.
    # local↔global is not a bit-exact inverse; measured single-cycle wobble is ~4e-11 deg
    # (~5 µm) and BOUNDED (50 cycles ≈ 0.24 mm cumulative), so repeated app restarts can
    # never visibly walk the lines. Pin an order of magnitude above the measured wobble.
    start2, _ = session.timing_lines_latlon()
    for (lat1, lon1), (lat2, lon2) in zip(start1, start2, strict=True):
        assert abs(lat1 - lat2) < 1e-9 and abs(lon1 - lon2) < 1e-9, (start1, start2)


def test_sector_lines_roundtrip():
    """Sector lines persist the same way: one sector at theta=pi survives export→apply with
    the sector splits unchanged."""
    session = _make_session()
    a = session.cs.local(_circle_gps(_THETA_SECTOR, _RADIUS_M - 40.0))
    b = session.cs.local(_circle_gps(_THETA_SECTOR, _RADIUS_M + 40.0))
    session.set_timing_lines(session.start_line, [Seg(a[0], a[1], b[0], b[1])])
    valid0 = session.valid_lap_ids()
    assert valid0
    splits0 = [session.lap_sector_splits(i) for i in valid0]
    assert all(len(s) == 2 for s in splits0), splits0  # 1 sector line → 2 sub-sectors

    start, sectors = session.timing_lines_latlon()
    assert len(sectors) == 1
    assert session.apply_timing_lines_latlon(start, sectors) is True
    assert session.valid_lap_ids() == valid0
    assert session.sector_count() == 1
    splits1 = [session.lap_sector_splits(i) for i in valid0]
    for s0, s1 in zip(splits0, splits1, strict=True):
        for v0, v1 in zip(s0, s1, strict=True):
            # The ~µm endpoint wobble moves the split interpolation by tens of ns; pin µs.
            assert abs(v0 - v1) < 1e-6, (s0, s1)


def test_apply_foreign_lines_reverts():
    """The REVERT GUARD: lines from another recording/track (no crossings here → zero valid
    laps) must return False and restore the previous lines + segmentation untouched."""
    session = _make_session()
    valid0 = session.valid_lap_ids()
    times0 = [session.lap_time(i) for i in valid0]
    start0 = session.start_line

    # A "sidecar" written 1 km away — schema-valid, geometrically foreign.
    foreign = [[_CLAT + 0.01, _CLON], [_CLAT + 0.0101, _CLON + 0.0001]]
    assert session.apply_timing_lines_latlon(foreign, []) is False

    # Everything restored: same line object values, same valid laps, same times.
    s = session.start_line
    assert (s.x1, s.y1, s.x2, s.y2) == (start0.x1, start0.y1, start0.x2, start0.y2)
    assert session.valid_lap_ids() == valid0
    assert [session.lap_time(i) for i in valid0] == times0


def test_apply_keeps_sectors_on_revert():
    """Reverting restores the SECTOR lines too, not just the start line."""
    session = _make_session()
    a = session.cs.local(_circle_gps(_THETA_SECTOR, _RADIUS_M - 40.0))
    b = session.cs.local(_circle_gps(_THETA_SECTOR, _RADIUS_M + 40.0))
    session.set_timing_lines(session.start_line, [Seg(a[0], a[1], b[0], b[1])])
    assert session.sector_count() == 1
    foreign = [[_CLAT + 0.01, _CLON], [_CLAT + 0.0101, _CLON + 0.0001]]
    assert session.apply_timing_lines_latlon(foreign, []) is False
    assert session.sector_count() == 1  # the sector line came back with the revert


def test_timing_trust_state_transitions():
    """The Session timing-trust model (Provisional ↔ Verified):
      * a detected track (track_name set) is Verified on its own;
      * an unknown track with an auto-fitted, unconfirmed start line is Provisional;
      * a user edit (set_timing_lines) OR confirm_timing() flips it Verified;
      * apply_timing_lines_latlon restores the persisted `confirmed` marker on success."""
    session = _make_session()
    # _make_session ends with a set_timing_lines() (a user edit), so it's confirmed here; reset
    # to the auto-fitted, unknown-track baseline to exercise the Provisional state.
    session.track_name = None
    session._timing_user_confirmed = False
    assert session.timing_verified is False, "auto-fitted unknown track must be Provisional"

    # A detected track is Verified with no user confirmation.
    session.track_name = "Some Circuit"
    assert session.timing_verified is True
    session.track_name = None  # back to unknown for the confirmation paths

    # An explicit start-line edit confirms it.
    session.set_timing_lines(session.start_line, session.sector_lines)
    assert session.timing_user_confirmed is True and session.timing_verified is True

    # confirm_timing() is the same flip, idempotent.
    session._timing_user_confirmed = False
    session.confirm_timing()
    assert session.timing_verified is True

    # Restoring a sidecar carries the persisted marker: confirmed=False stays Provisional...
    start, sectors = session.timing_lines_latlon()
    assert session.apply_timing_lines_latlon(start, sectors, confirmed=False) is True
    assert session.timing_verified is False, "an unconfirmed restore stays Provisional"
    # ...and confirmed=True restores the Verified state.
    assert session.apply_timing_lines_latlon(start, sectors, confirmed=True) is True
    assert session.timing_verified is True


def test_make_segment_matches_tracks_helper():
    """Seg.to_pacer (used by apply via set_timing_lines) and tracks.make_segment are the same
    construction — pin that the sidecar path reuses the single Segment write-pattern."""
    seg = Seg(1.0, 2.0, 3.0, 4.0).to_pacer()
    ref = tracks.make_segment(1.0, 2.0, 3.0, 4.0)
    assert seg == ref


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} sidecar tests passed")


if __name__ == "__main__":
    _run_all()
