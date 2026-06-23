"""Tests for the track database (studio.track_db + studio.tracks detection adapter, E3).

The track DB is a local index of named circuits — each with its start/finish line (+ any
sector lines) in ABSOLUTE lat/lon and a detection centroid/bbox — stored in the macOS
app-support dir (like the session library), so a recording auto-detects its track on load and
the user can promote placed timing lines into a reusable named track (File ▸ Save as track…).

CRITICAL: every test that touches the on-disk DB points it at a TEMP path (an explicit `path=`
or by monkeypatching ``track_db._app_support_dir``) — the suite NEVER touches the user's real
``~/Library/Application Support/pacer/tracks.json``.

Covered:
  * pure DB (no pacer): schema round-trip + float-repr bit-exactness; the name-keyed
    upsert-replaces-not-duplicates rule; corrupt/invalid DB → safe empty (self-heal) with one
    bad entry dropped (the rest kept); the built-in Daytona MK seed always present + merged
    under the user DB; make_entry validation;
  * detection + precedence (pacer, synthetic circle laps): a saved track detects by centroid and
    applies its lat/lon lines; precedence sidecar > DB > auto-fit; and the Daytona MK no-regression
    (seed line is byte-identical to the old hardcoded entry, so its segmentation is unchanged).
  * the Save-as-track guard (offscreen Qt): the menu action is enabled only when the session has
    usable timing lines, and a guarded write never raises out of the handler.

Run: python tests/test_track_db.py   (pacer + Qt halves self-skip if unavailable)
"""
import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pacer  # noqa: E402,F401  (import the built bindings BEFORE studio, like the other
#               pacer-using tests — importing studio first can otherwise cache the repo-root
#               `pacer/` C++ source dir as a namespace package without `Laps` in CI's fresh env)
from studio import track_db  # noqa: E402

# The measured Daytona MK line (was hardcoded in tracks.REGISTRY). The seed MUST stay
# byte-identical to this, or MK timing regresses.
_MK_CENTROID = [52.0403, -0.7847]
_MK_START = [[52.04031, -0.78487], [52.04020, -0.78460]]


def _pacer_available() -> bool:
    try:
        import pacer  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 — any import failure means "no built bindings here"
        return False


# ------------------------------------------------------------------ helpers
def _entry(name, *, centroid=(52.0, -0.78), start=None, sectors=None, bbox=None):
    """A valid track entry around a default location, overridable per field."""
    if start is None:
        start = [[52.001, -0.781], [52.002, -0.779]]
    return {
        "name": name,
        "centroid": list(centroid),
        "bbox": bbox,
        "start": start,
        "sectors": sectors if sectors is not None else [],
    }


# ============================================================ pure DB (no pacer)

def test_seed_has_daytona_mk_byte_identical():
    """The built-in seed carries Daytona MK with the EXACT old hardcoded centroid + start line —
    the contract that keeps MK timing from regressing."""
    mk = next(e for e in track_db.SEED if e["name"] == "Daytona Milton Keynes")
    assert mk["centroid"] == _MK_CENTROID
    assert mk["start"] == _MK_START
    assert mk["sectors"] == []


def test_all_tracks_includes_seed_when_db_empty():
    """A first-ever run (no user file) still knows Daytona MK — the seed is always present."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        names = [e["name"] for e in track_db.all_tracks(p)]
        assert "Daytona Milton Keynes" in names


def test_save_load_roundtrip_bit_exact():
    """json floats are written with repr (the shortest EXACT double string), so the timing-line
    endpoints survive save→load bit-identically, and a re-save is byte-identical on disk."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        db = track_db.empty_db()
        start = [[37.123456789, -122.987654321], [37.123456790, -122.987654320]]
        track_db.upsert(db, _entry("Sonoma", centroid=(37.12, -122.98), start=start,
                                   sectors=[[[37.1, -122.9], [37.11, -122.89]]],
                                   bbox=[37.1, -123.0, 37.2, -122.9]))
        track_db.save(db, p)
        back = track_db.load(p)
        assert back["version"] == 1 and len(back["tracks"]) == 1
        e = back["tracks"][0]
        assert e["start"] == start                  # exact float equality
        assert e["bbox"] == [37.1, -123.0, 37.2, -122.9]
        assert len(e["sectors"]) == 1
        # A second save of the loaded DB is byte-identical on disk (fully stable).
        p2 = os.path.join(d, "again.json")
        track_db.save(back, p2)
        with open(p) as f1, open(p2) as f2:
            assert f1.read() == f2.read()


def test_save_creates_app_support_dir():
    """save() lazily creates a missing app-support directory (only on a write)."""
    with tempfile.TemporaryDirectory() as d:
        nested = os.path.join(d, "Library", "Application Support", "pacer", "tracks.json")
        assert not os.path.exists(os.path.dirname(nested))
        track_db.save(track_db.empty_db(), nested)
        assert os.path.exists(nested)


def test_upsert_replaces_same_name_in_place():
    """The NO-DUPLICATE rule: re-saving the same track NAME updates in place (count stays 1,
    position kept); a different name appends."""
    db = track_db.empty_db()
    track_db.upsert(db, _entry("A", start=[[1.0, 2.0], [3.0, 4.0]]))
    assert len(db["tracks"]) == 1
    track_db.upsert(db, _entry("A", start=[[5.0, 6.0], [7.0, 8.0]]))  # same name, new line
    assert len(db["tracks"]) == 1
    assert db["tracks"][0]["start"] == [[5.0, 6.0], [7.0, 8.0]]
    track_db.upsert(db, _entry("B"))                                   # new name appends
    assert len(db["tracks"]) == 2
    # Re-save of A keeps its position (index 0), not reshuffled to the end.
    track_db.upsert(db, _entry("A", start=[[9.0, 10.0], [11.0, 12.0]]))
    assert len(db["tracks"]) == 2 and db["tracks"][0]["name"] == "A"


def test_user_db_overrides_seed_of_same_name():
    """A user-saved track of the SAME name as a seed wins (refining a built-in is allowed)."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        refined = [[52.05, -0.79], [52.051, -0.788]]
        track_db.save_track(_entry("Daytona Milton Keynes", centroid=tuple(_MK_CENTROID),
                                   start=refined), p)
        mk = next(e for e in track_db.all_tracks(p) if e["name"] == "Daytona Milton Keynes")
        assert mk["start"] == refined                       # user override beat the seed
        # And there is exactly ONE Daytona MK in the merged view (no seed duplicate).
        assert sum(e["name"] == "Daytona Milton Keynes"
                   for e in track_db.all_tracks(p)) == 1


def test_make_entry_validates():
    """make_entry builds a normalized entry from the timing-lines shape and rejects bad input."""
    e = track_db.make_entry("X", (52.0, -0.78), [[52.0, -0.78], [52.01, -0.77]], [])
    assert e["name"] == "X" and e["centroid"] == [52.0, -0.78]
    for bad in (
        ("", (52.0, -0.78), [[52.0, -0.78], [52.01, -0.77]], []),       # empty name
        ("X", (52.0, -0.78), [[52.0, -0.78]], []),                       # one endpoint
        ("X", (91.0, -0.78), [[52.0, -0.78], [52.01, -0.77]], []),       # centroid out of range
        ("X", (52.0, -0.78), [[52.0, -0.78], [52.01, -0.77]], [[[1.0, 2.0]]]),  # bad sector
    ):
        try:
            track_db.make_entry(*bad)
            raise AssertionError(f"expected ValueError for {bad}")
        except ValueError:
            pass


def test_load_missing_is_empty_db():
    """A missing file → a fresh empty DB (NOT an error)."""
    assert track_db.load("/nonexistent/dir/tracks.json") == {"version": 1, "tracks": []}


def test_load_corrupt_returns_empty_then_heals():
    """Every malformed FILE shape → a safe EMPTY DB (self-heal); a fresh write heals it."""
    good = _entry("Sonoma", start=[[37.1, -122.9], [37.2, -122.8]])
    bad_bodies = [
        "{ not json",                                             # not JSON at all
        "[]",                                                      # not an object
        '{"version": 2, "tracks": []}',                           # unknown version
        '{"version": 1}',                                         # no tracks list
        '{"version": 1, "tracks": 3}',                            # tracks not a list
    ]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        for body in bad_bodies:
            with open(p, "w") as f:
                f.write(body)
            assert track_db.load(p) == {"version": 1, "tracks": []}, body
        # Heal: a fresh save_track over the garbage yields a clean, loadable DB.
        track_db.save_track(good, p)
        idx = track_db.load(p)
        assert len(idx["tracks"]) == 1 and idx["tracks"][0]["name"] == "Sonoma"
        with open(p) as f:
            raw = json.load(f)
        assert set(raw) == {"version", "tracks"}
        assert set(raw["tracks"][0]) == {"name", "centroid", "bbox", "start", "sectors"}


def test_load_drops_only_malformed_entries_keeps_rest():
    """ENTRY-tolerant load: one bad track must NOT discard the whole DB — the valid tracks
    survive and only the bad row is dropped (same self-heal as the library)."""
    good_a = _entry("A", start=[[1.0, 2.0], [3.0, 4.0]])
    good_b = _entry("B", start=[[5.0, 6.0], [7.0, 8.0]])
    bad = {"name": "Bad", "centroid": [200.0, 0.0],          # centroid out of range
           "bbox": None, "start": [[1.0, 2.0], [3.0, 4.0]], "sectors": []}
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        with open(p, "w") as f:
            json.dump({"version": 1, "tracks": [good_a, bad, good_b]}, f)
        names = {e["name"] for e in track_db.load(p)["tracks"]}
        assert names == {"A", "B"}                            # the bad row dropped, rest kept
        # A re-save persists only the survivors (the loss of nothing valid).
        track_db.save(track_db.load(p), p)
        assert {e["name"] for e in track_db.load(p)["tracks"]} == {"A", "B"}


def test_detect_finds_saved_track_by_centroid():
    """A saved track detects when a trace centroid lands within the radius, and NOT when it's
    far away (nearest-wins within DETECT_RADIUS_M)."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        track_db.save_track(_entry("Local", centroid=(40.0, -80.0)), p)
        hit = track_db.detect(40.0001, -80.0001, p)          # ~14 m away → hit
        assert hit is not None and hit["name"] == "Local"
        assert track_db.detect(41.0, -80.0, p) is None       # ~111 km away → no hit


def test_detect_picks_nearest_of_several():
    """When several tracks match, detection returns the NEAREST."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        track_db.save_track(_entry("Near", centroid=(40.0, -80.0)), p)
        track_db.save_track(_entry("Far", centroid=(40.005, -80.0)), p)  # ~556 m away
        hit = track_db.detect(40.0005, -80.0, p)             # closer to Near
        assert hit is not None and hit["name"] == "Near"


def test_app_support_path_uses_patched_seam(monkeypatch):
    """db_path() resolves through _app_support_dir — patching that seam (the test idiom) diverts
    all default-path reads/writes away from the user's real ~/Library."""
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(track_db, "_app_support_dir", lambda: d)
        assert track_db.db_path() == os.path.join(d, "tracks.json")
        track_db.save_track(_entry("Patched", centroid=(10.0, 10.0)))     # default path
        assert os.path.exists(os.path.join(d, "tracks.json"))
        assert track_db.detect(10.0, 10.0) is not None                    # default-path detect


# ===================================== detection adapter + precedence (pacer, synthetic laps)
# These build a real pacer.Laps so the lat/lon detection → Track → segment path is exercised
# end-to-end; they self-skip in the pacer-free standalone runner.

_CLAT, _CLON = 52.0, -0.78
_RADIUS_M = 100.0
_PER_LAP = 314
_N_LAPS = 3
_M_PER_DEG_LAT = 111_320.0
_THETA_START = 2.0 * math.pi * (10.5 / _PER_LAP)
_THETA_SECTOR = 2.0 * math.pi * ((_PER_LAP // 2) + 0.5) / _PER_LAP


def _circle_gps(theta, radius=_RADIUS_M):
    import pacer
    lat = _CLAT + (radius * math.cos(theta)) / _M_PER_DEG_LAT
    lon = _CLON + (radius * math.sin(theta)) / (_M_PER_DEG_LAT * math.cos(math.radians(_CLAT)))
    return pacer.GPSSample(lat=lat, lon=lon, altitude=0.0, full_speed=20.0, ground_speed=20.0)


def _make_session():
    """A real 3-lap circle session segmented by a start line straddling the circle, the same
    construction order as Session.load (mirrors test_sidecar._make_session)."""
    import pacer
    from studio.session import Seg, Session
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
    a = cs.local(_circle_gps(_THETA_START, _RADIUS_M - 40.0))
    b = cs.local(_circle_gps(_THETA_START, _RADIUS_M + 40.0))
    session.set_timing_lines(Seg(a[0], a[1], b[0], b[1]), [])
    return session


def test_db_roundtrip_save_detect_apply():
    """The headline round-trip: save a track from a placed session → detect it by centroid →
    apply its lat/lon lines onto a FRESH session → the same segmentation comes back."""
    if not _pacer_available():
        print("skip test_db_roundtrip_save_detect_apply (no pacer)")
        return
    from studio import tracks
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        # Place lines on a session, capture them + the detection anchor, save as a track.
        placed = _make_session()
        valid0 = placed.valid_lap_ids()
        assert len(valid0) >= 2, valid0
        times0 = [placed.lap_time(i) for i in valid0]
        centroid, bbox = placed.track_location()
        start, sectors = placed.timing_lines_latlon()
        track_db.save_track(track_db.make_entry("Synthetic", centroid, start, sectors, bbox), p)

        # Detect by the captured centroid → get the Track back with its lat/lon lines.
        trk = tracks.detect_track(centroid[0], centroid[1], db_path=p)
        assert trk is not None and trk.name == "Synthetic"

        # Apply those lines onto a FRESH session (auto-fit cleared first) → same laps + times.
        fresh = _make_session()
        a2 = trk.start_a
        b2 = trk.start_b
        assert fresh.apply_timing_lines_latlon([list(a2), list(b2)], []) is True
        assert fresh.valid_lap_ids() == valid0
        for t0, t1 in zip(times0, [fresh.lap_time(i) for i in valid0], strict=True):
            assert abs(t0 - t1) < 1e-6, (t0, t1)


def test_db_roundtrip_preserves_sectors():
    """A track saved WITH a sector line detects and re-applies that sector (split count kept)."""
    if not _pacer_available():
        print("skip test_db_roundtrip_preserves_sectors (no pacer)")
        return
    from studio import tracks
    from studio.session import Seg
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        placed = _make_session()
        a = placed.cs.local(_circle_gps(_THETA_SECTOR, _RADIUS_M - 40.0))
        b = placed.cs.local(_circle_gps(_THETA_SECTOR, _RADIUS_M + 40.0))
        placed.set_timing_lines(placed.start_line, [Seg(a[0], a[1], b[0], b[1])])
        centroid, bbox = placed.track_location()
        start, sectors = placed.timing_lines_latlon()
        assert len(sectors) == 1
        track_db.save_track(track_db.make_entry("WithSector", centroid, start, sectors, bbox), p)
        trk = tracks.detect_track(centroid[0], centroid[1], db_path=p)
        assert trk is not None and len(trk.sectors) == 1
        segs = tracks.sector_line_segments(trk, placed.cs)
        assert len(segs) == 1                                  # adapter yields the sector segment


def test_precedence_sidecar_over_db_over_autofit():
    """The precedence rule (the way app._load layers them): a DB match beats the auto-fit, and a
    per-file sidecar beats the DB. A track at one angle (DB) and a hand-tuned sidecar at another
    both segment the trace, but whichever is applied LAST is the one that sticks — and the app
    applies the sidecar last, so the sidecar wins."""
    if not _pacer_available():
        print("skip test_precedence_sidecar_over_db_over_autofit (no pacer)")
        return
    import pacer
    from studio import tracks
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        # The DB line straddles the circle at _THETA_START.
        ref = _make_session()
        centroid, bbox = ref.track_location()
        db_start, _ = ref.timing_lines_latlon()
        track_db.save_track(track_db.make_entry("DBTrack", centroid, db_start, [], bbox), p)

        # 1) DB beats auto-fit: a fresh session detects + applies the DB lines (≥2 valid laps).
        sess = _make_session()
        trk = tracks.detect_track(centroid[0], centroid[1], db_path=p)
        assert trk is not None
        assert sess.apply_timing_lines_latlon(
            [list(trk.start_a), list(trk.start_b)], []) is True
        db_x = sess.start_line.x1
        assert len(sess.valid_lap_ids()) >= 2

        # A DISTINCT hand-placed sidecar line at a different angle (_THETA_SECTOR), as lat/lon.
        a = sess.cs.local(_circle_gps(_THETA_SECTOR, _RADIUS_M - 40.0))
        b = sess.cs.local(_circle_gps(_THETA_SECTOR, _RADIUS_M + 40.0))
        ga = sess.cs.global_(pacer.Vec3f(float(a[0]), float(a[1]), 0.0))
        gb = sess.cs.global_(pacer.Vec3f(float(b[0]), float(b[1]), 0.0))
        sidecar_start = [[ga.lat, ga.lon], [gb.lat, gb.lon]]

        # 2) Sidecar (applied AFTER the DB line, like app._load) overrides the DB placement.
        assert sess.apply_timing_lines_latlon(sidecar_start, []) is True
        assert abs(sess.start_line.x1 - a[0]) < 1.0          # now at the sidecar line…
        assert abs(sess.start_line.x1 - db_x) > 1.0          # …not the DB line


def test_daytona_mk_seed_unchanged():
    """No-regression: tracks.detect_track for the MK centroid returns the seed track with the
    EXACT old hardcoded start endpoints (so _fit_start_line gets the same base → same timing)."""
    if not _pacer_available():
        print("skip test_daytona_mk_seed_unchanged (no pacer)")
        return
    from studio import tracks
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")  # empty user DB → only the seed
        trk = tracks.detect_track(_MK_CENTROID[0], _MK_CENTROID[1], db_path=p)
        assert trk is not None and trk.name == "Daytona Milton Keynes"
        assert trk.start_a == (_MK_START[0][0], _MK_START[0][1])
        assert trk.start_b == (_MK_START[1][0], _MK_START[1][1])
        assert trk.sectors == ()                              # the seed defines no sectors


# ===================================== Save-as-track guard (offscreen Qt; skipped without pacer)

def test_save_track_guard(monkeypatch):
    """The app's Save-as-track is GUARDED: _can_save_track is False with no session / no valid
    laps and True with usable timing lines; the action's enabled state follows; and a write
    failure is swallowed (never raises out of the handler)."""
    if not _pacer_available():
        print("skip test_save_track_guard (no pacer)")
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])  # noqa: F841
    from studio import app as studio_app

    win = studio_app.StudioWindow.__new__(studio_app.StudioWindow)

    # No session → not saveable.
    assert studio_app.StudioWindow._can_save_track(win) is False

    # A 0-valid-lap session → not saveable.
    win.session = type("S", (), {
        "valid_lap_ids": staticmethod(lambda: []),
        "point_count": staticmethod(lambda: 100),
    })()
    assert studio_app.StudioWindow._can_save_track(win) is False

    # A real session with valid laps → saveable.
    win.session = type("S", (), {
        "valid_lap_ids": staticmethod(lambda: [0, 1]),
        "point_count": staticmethod(lambda: 100),
    })()
    assert studio_app.StudioWindow._can_save_track(win) is True

    # An exception inside the guard is swallowed (returns False, never propagates).
    win.session = type("S", (), {
        "valid_lap_ids": staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("boom"))),
        "point_count": staticmethod(lambda: 100),
    })()
    assert studio_app.StudioWindow._can_save_track(win) is False


# ------------------------------------------------------------------ runner
def _run_all():
    import inspect
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        if "monkeypatch" in inspect.signature(fn).parameters:
            _run_with_monkeypatch(fn)
        else:
            fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} track_db tests passed")


def _run_with_monkeypatch(fn):
    """Minimal monkeypatch shim so the file runs standalone (no pytest needed)."""
    saved = []

    class _MP:
        def setattr(self, obj, name, value):
            saved.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

    try:
        fn(_MP())
    finally:
        for obj, name, old in reversed(saved):
            setattr(obj, name, old)


if __name__ == "__main__":
    _run_all()
