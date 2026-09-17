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
    under the user DB; make_entry validation; the name-collision refusal (QA L12-09 — reusing a
    name for a circuit 79 km away destroyed the first one's timing lines silently) alongside the
    same-place refine that must stay silent;
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


# --------------------------------------------------- data safety (QA sweep W7-01)
# The measured loss: a tracks.json holding three user circuits was truncated (a crash mid-write,
# or a hand-edit via "Reveal in Finder"). load() mapped that to empty_db(), and ONE ordinary
# File ▸ Save as track… at a circuit 79 km away rewrote the file from that empty view — 3
# circuits -> 1, no .bak, no modal, and the status bar reported success. The same shape reached
# through two more doors: a version stamp the build doesn't know, and a single malformed entry
# "healed" away. A read fallback must never become a destructive write.

def test_save_backs_up_a_db_it_could_not_read():
    """W7-01: an ordinary save over an UNREADABLE DB leaves the original bytes beside it, so the
    circuits the build could not read are recoverable rather than gone — and says so beforehand
    through ``backup_pending``. A healthy DB is never backed up (no churn)."""
    circuits = [_entry("Sandown Park", centroid=(51.376, -0.361)),
                _entry("Buckmore Park", centroid=(51.30, 0.55)),
                _entry("Whilton Mill", centroid=(52.28, -1.10))]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        track_db.save({"version": 1, "tracks": circuits}, p)
        good = open(p).read()
        # A hand-edit through "Reveal in Finder" that breaks the JSON but keeps every byte: all
        # three circuits are still IN there, and must still be there after the save.
        with open(p, "w") as f:
            f.write(good + "}")
        assert track_db.load(p) == track_db.empty_db()       # the read fallback, unchanged
        track_db.save_track(_entry("Daytona Milton Keynes", centroid=(52.0403, -0.7847)), p)
        assert os.path.exists(p + ".bak"), "an unreadable DB was overwritten with no backup"
        rescued = open(p + ".bak").read()
        assert rescued == good + "}", "the .bak is not the original bytes"
        for name in ("Sandown Park", "Buckmore Park", "Whilton Mill"):
            assert name in rescued, f"{name} is not recoverable"
        # The file is healthy again, so later saves neither churn nor clobber that rescue.
        assert track_db.backup_pending(p) is None
        track_db.save_track(_entry("Croft", centroid=(54.45, -1.55)), p)
        assert open(p + ".bak").read() == rescued

        # A TRUNCATION (the crash-mid-write shape the sweep measured) keeps whatever survived it —
        # the save can no longer take the rest away, and the UI is told BEFORE the write.
        half = good[: len(good) // 2]
        with open(p, "w") as f:
            f.write(half)
        assert track_db.backup_pending(p) == p + ".bak"
        track_db.save_track(_entry("Croft", centroid=(54.45, -1.55)), p)
        assert open(p + ".bak").read() == half
        assert "Sandown Park" in half


def test_newer_schema_version_keeps_its_tracks_and_is_backed_up():
    """A version stamp this build doesn't know is NOT corruption: load reads it best-effort so
    every circuit survives a downgrade, and save keeps the newer file before re-stamping it.
    (The old `!= VERSION` test emptied the DB, and the next save made that permanent.)"""
    newer = {"version": track_db.VERSION + 1,
             "tracks": [_entry("A", centroid=(52.0, -0.78)),
                        _entry("B", centroid=(53.0, -1.78))],
             "unknown_future_field": {"whatever": 1}}
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        with open(p, "w") as f:
            json.dump(newer, f)
        loaded = track_db.load(p)
        assert {e["name"] for e in loaded["tracks"]} == {"A", "B"}, "a newer DB was emptied"
        assert loaded["version"] == track_db.VERSION
        assert track_db.backup_pending(p) == p + ".bak"
        track_db.save_track(_entry("C", centroid=(54.0, -2.78)), p)
        # Nothing lost on the way down, and the newer original is still on disk.
        assert {e["name"] for e in track_db.load(p)["tracks"]} == {"A", "B", "C"}
        with open(p + ".bak") as f:
            saved = json.load(f)
        assert saved["version"] == track_db.VERSION + 1
        assert saved["unknown_future_field"] == {"whatever": 1}


def test_healing_a_malformed_entry_keeps_the_original():
    """Dropping one bad row and rewriting the survivors is defensible — doing it with no copy is
    not. The heal still happens; the pre-heal file is now recoverable from the .bak."""
    bad = {"name": "Bad", "centroid": [200.0, 0.0],           # centroid out of range
           "bbox": None, "start": [[1.0, 2.0], [3.0, 4.0]], "sectors": []}
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        with open(p, "w") as f:
            json.dump({"version": 1,
                       "tracks": [_entry("A", centroid=(52.0, -0.78)), bad,
                                  _entry("B", centroid=(53.0, -1.78))]}, f)
        track_db.save(track_db.load(p), p)                    # the heal
        assert {e["name"] for e in track_db.load(p)["tracks"]} == {"A", "B"}
        assert os.path.exists(p + ".bak"), "the heal rewrote the file with no copy of the original"
        with open(p + ".bak") as f:
            original = json.load(f)
        assert [e["name"] for e in original["tracks"]] == ["A", "Bad", "B"]


def test_backup_is_best_effort_and_never_blocks_the_save(monkeypatch):
    """An unwritable backup slot must not cost the user their new track: the copy fails, the save
    still lands (a save that keeps the app usable beats refusing to save)."""
    def _boom(*a, **k):
        raise OSError("read-only backup slot")

    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        with open(p, "w") as f:
            f.write("{ not json")
        monkeypatch.setattr(track_db.shutil, "copy2", _boom)
        track_db.save_track(_entry("A", centroid=(52.0, -0.78)), p)
        assert {e["name"] for e in track_db.load(p)["tracks"]} == {"A"}
        assert not os.path.exists(p + ".bak")


# --------------------------------------------------- name collisions (QA sweep L12-09)
# The two REAL anchors the sweep measured: Save as track… under one name from a session at
# SD_30_08_26, then again from a session at Daytona MK — 79 km apart, entry count 1 → 1, the
# first circuit's start line gone, no dialog, and a success message byte-identical to a create.
_SD_CENTROID = (51.37604946538461, -0.36101118296703305)
_MK_TRACE_CENTROID = (52.039685942307685, -0.7833087395604396)


def test_save_track_refuses_to_overwrite_a_different_circuit():
    """A name reused for somewhere ELSE is refused, not silently applied: the stored circuit keeps
    its start line and its anchor, and the caller gets a ValueError it already guards (so even a
    caller with no confirm reports instead of destroying). replace=True is the confirmed path."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        first = _entry("My Circuit", centroid=_SD_CENTROID,
                       start=[[51.376, -0.361], [51.3761, -0.3608]])
        track_db.save_track(first, p)
        second = _entry("My Circuit", centroid=_MK_TRACE_CENTROID,
                        start=[[52.04031, -0.78487], [52.04020, -0.78460]])
        raised = None
        try:
            track_db.save_track(second, p)
        except ValueError as exc:      # the app's existing guard — catching ValueError is enough
            raised = exc
        assert raised is not None, "a 79 km name collision must not be written silently"
        assert isinstance(raised, track_db.TrackNameTaken)
        assert raised.existing["name"] == "My Circuit"          # names what is at risk…
        assert raised.existing["start"] == first["start"]       # …and hands over its lines
        assert raised.distance_m > track_db.DETECT_RADIUS_M
        assert "My Circuit" in str(raised) and "km away" in str(raised)
        # DECLINED ⇒ the stored track is exactly as it was.
        db = track_db.load(p)
        assert len(db["tracks"]) == 1
        assert db["tracks"][0]["start"] == first["start"]
        assert db["tracks"][0]["centroid"] == list(_SD_CENTROID)
        # CONFIRMED ⇒ the replacement goes through, in place (the no-duplicate rule is unchanged).
        track_db.save_track(second, p, replace=True)
        db = track_db.load(p)
        assert len(db["tracks"]) == 1
        assert db["tracks"][0]["start"] == second["start"]


def test_replaces_reports_the_track_a_save_would_destroy():
    """The question a caller asks BEFORE saving, so its confirm can name the circuit: the stored
    entry for a far collision, None for a fresh name and None for the same place."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        track_db.save_track(_entry("My Circuit", centroid=_SD_CENTROID), p)
        at_risk = track_db.replaces(_entry("My Circuit", centroid=_MK_TRACE_CENTROID), p)
        assert at_risk is not None and at_risk["centroid"] == list(_SD_CENTROID)
        assert track_db.replaces(_entry("Another Circuit", centroid=_MK_TRACE_CENTROID), p) is None
        assert track_db.replaces(_entry("My Circuit", centroid=_SD_CENTROID), p) is None
        # A user entry SHADOWS a built-in of the same name in the merged view, taking its detection
        # with it — so the seed is guarded by the same rule even with an empty user DB.
        empty = os.path.join(d, "empty.json")
        shadow = _entry("Daytona Milton Keynes", centroid=_SD_CENTROID)
        assert track_db.replaces(shadow, empty) is not None


def test_save_track_still_refines_the_track_at_this_location():
    """Refining a line you already saved — the documented Save-as-track flow, built-ins included —
    is NOT a collision: same name, same place (inside DETECT_RADIUS_M) still replaces in place with
    no confirmation, so the fix costs the common case nothing."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        nudged = [[51.3762, -0.3612], [51.3763, -0.3609]]
        track_db.save_track(_entry("My Circuit", centroid=_SD_CENTROID), p)
        # ~11 m away (a re-run of the same session keeps a slightly different trace centroid).
        moved = (_SD_CENTROID[0] + 0.0001, _SD_CENTROID[1])
        track_db.save_track(_entry("My Circuit", centroid=moved, start=nudged), p)
        db = track_db.load(p)
        assert len(db["tracks"]) == 1 and db["tracks"][0]["start"] == nudged
        # And the built-in MK seed is refinable at MK, exactly as documented.
        refined = [[52.0405, -0.7849], [52.0404, -0.7846]]
        track_db.save_track(_entry("Daytona Milton Keynes", centroid=_MK_TRACE_CENTROID,
                                   start=refined), p)
        mk = next(e for e in track_db.all_tracks(p) if e["name"] == "Daytona Milton Keynes")
        assert mk["start"] == refined


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


# ===================================== F2 — renaming and deleting saved tracks
# A track NAME is an identity key in three OTHER stores, measured on this tree:
#   * library.py   — every entry carries {"track": <name>} and prior_best / best_entry / pb_series /
#                    track_summary all match on `e.get("track") == track`, so a rename that does not
#                    carry them SPLITS a circuit's personal-best history in two;
#   * focus.py     — the per-track focus list is keyed {"track": <name>} (for_track / set_for_track);
#   * session_record.py — each record carries an auto-stamped `track` (provenance).
# The per-video `.pacer.json` sidecar also stores a track name, but `Session.restore_saved_timing_
# lines` reads only start/sectors/confirmed — it has NO consumer — so a rename deliberately does not
# rewrite files that live beside the user's footage.

_FOCUS_ITEM_KW = dict(
    cid=3, direction=1, enter_frac=0.10, exit_frac=0.20, median_s=4.5, iqr_s=0.12, n_laps=9,
    time_lost=0.31, reason="entry", reach="execution", fingerprint="GX0002", date="2024-06-01",
    lap_total=1059.0, verified=True, degraded=False)


def _seed_library(track, path):
    """A library index of three sessions on `track` (two of them a real PB progression)."""
    from studio import library
    idx = library.empty_index()
    for i, (stem, date, best) in enumerate((("GX010001", "2024-05-01", 70.0),
                                            ("GX010002", "2024-06-01", 68.0),
                                            ("GX010003", "2024-07-01", 69.0))):
        library.upsert(idx, {
            "fingerprint": library.fingerprint(stem), "stem": stem, "track": track, "date": date,
            "lap_count": 10 + i, "best": best, "theoretical": None,
            "verified": True, "degraded": False, "dropout": False,
            "paths": [f"/media/{stem}.MP4"]})
    library.save(idx, path)
    return idx


def _seed_focus(track, path):
    """A focus store holding one per-track list for `track`."""
    from studio import focus
    store = focus.empty_store()
    focus.set_for_track(store, track, [focus.FocusItem(**_FOCUS_ITEM_KW)])
    focus.save(store, path)
    return store


def _seed_records(track, path):
    """A session-record store with one record auto-stamped with `track`."""
    from studio import session_record
    store = session_record.empty_store()
    rec = session_record.blank_record()
    rec["conditions"] = "dry"
    rec["track"] = track
    session_record.put(store, "GX0002", rec)
    session_record.save(store, path)
    return store


def test_remove_track_deletes_only_that_circuit():
    """Deleting one saved circuit leaves every other one exactly as it was — same start line, same
    anchor, still detectable — and the deleted one stops detecting."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        keep_start = [[51.376, -0.361], [51.3761, -0.3608]]
        track_db.save_track(_entry("Sandown Park", centroid=(51.376, -0.361),
                                   start=keep_start), p)
        track_db.save_track(_entry("Croft", centroid=(54.45, -1.55)), p)
        track_db.remove_track("Croft", p)
        names = {e["name"] for e in track_db.load(p)["tracks"]}
        assert names == {"Sandown Park"}, names
        kept = track_db.load(p)["tracks"][0]
        assert kept["start"] == keep_start, "deleting one circuit moved another's start line"
        assert track_db.detect(51.376, -0.361, p)["name"] == "Sandown Park"
        assert track_db.detect(54.45, -1.55, p) is None, "the deleted circuit still detects"


def test_remove_track_keeps_a_backup_of_the_db_it_deleted_from():
    """A delete destroys a start/finish line the user placed by hand, so the store it deleted from
    is copied to tracks.json.bak FIRST — the same rule library.clear and
    session_record.remove_and_save already follow for their own destructive acts."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        gone_start = [[54.45, -1.55], [54.451, -1.549]]
        track_db.save_track(_entry("Sandown Park", centroid=(51.376, -0.361)), p)
        track_db.save_track(_entry("Croft", centroid=(54.45, -1.55), start=gone_start), p)
        track_db.remove_track("Croft", p)
        bak = p + ".bak"
        assert os.path.exists(bak), "a deleted circuit left no backup at all"
        rescued = {e["name"]: e for e in track_db.load(bak)["tracks"]}
        assert "Croft" in rescued, f"the backup does not hold the deleted circuit ({sorted(rescued)})"
        assert rescued["Croft"]["start"] == gone_start, "the backup lost the deleted start line"


def test_a_deleted_track_can_be_restored():
    """Recoverable, not merely backed up: restore puts the deleted circuit back, and — because the
    restore is a SWAP — it is itself reversible (library.restore / session_record.restore's rule)."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        track_db.save_track(_entry("Sandown Park", centroid=(51.376, -0.361)), p)
        track_db.save_track(_entry("Croft", centroid=(54.45, -1.55)), p)
        track_db.remove_track("Croft", p)
        assert {e["name"] for e in track_db.load(p)["tracks"]} == {"Sandown Park"}
        info = track_db.backup_summary(p)
        assert info is not None and info["tracks"] == 2, info
        track_db.restore(p)
        assert {e["name"] for e in track_db.load(p)["tracks"]} == {"Sandown Park", "Croft"}
        # …and back again: what the restore replaced became the new backup.
        track_db.restore(p)
        assert {e["name"] for e in track_db.load(p)["tracks"]} == {"Sandown Park"}


def test_removing_the_last_track_leaves_a_usable_store():
    """Deleting the ONLY saved circuit must leave a clean empty DB, not a broken one: the built-in
    seed is still there, nothing detects at the deleted anchor, and the next save still works."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        track_db.save_track(_entry("Croft", centroid=(54.45, -1.55)), p)
        track_db.remove_track("Croft", p)
        assert track_db.load(p) == track_db.empty_db()
        assert track_db.unreadable(p) is False, "an emptied DB must not read as a damaged one"
        names = [e["name"] for e in track_db.all_tracks(p)]
        assert names == ["Daytona Milton Keynes"], names
        assert track_db.detect(54.45, -1.55, p) is None
        track_db.save_track(_entry("Whilton Mill", centroid=(52.28, -1.10)), p)
        assert {e["name"] for e in track_db.load(p)["tracks"]} == {"Whilton Mill"}


def test_deleting_a_track_does_not_touch_the_recordings_that_reference_it():
    """THE dangling-reference case. The library keys its PB history by track NAME, so deleting the
    circuit must not delete, re-key or drop a single analysed session: the rows are the record of
    what was driven, and they stay readable under the name they were driven as. Same for the focus
    list and the session record. Deleting a circuit removes future auto-detection, nothing else."""
    from studio import focus, library, session_record
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        lib_p = os.path.join(d, "library.json")
        focus_p = os.path.join(d, "focus.json")
        rec_p = os.path.join(d, "session_records.json")
        track_db.save_track(_entry("Sonoma", centroid=(38.16, -122.45)), p)
        _seed_library("Sonoma", lib_p)
        _seed_focus("Sonoma", focus_p)
        _seed_records("Sonoma", rec_p)

        track_db.remove_track("Sonoma", p)

        idx = library.load(lib_p)
        assert len(idx["entries"]) == 3, "deleting a circuit dropped analysed sessions"
        assert all(e["track"] == "Sonoma" for e in idx["entries"])
        assert library.prior_best(idx, "Sonoma") == 68.0, "the personal best was lost with the track"
        assert library.pb_series(idx, "Sonoma") == [
            ("2024-05-01", 70.0), ("2024-06-01", 68.0), ("2024-07-01", 69.0)]
        assert library.track_summary(idx, "Sonoma")["sessions"] == 3
        assert len(focus.for_track(focus.load(focus_p), "Sonoma")) == 1, "the focus list was lost"
        assert session_record.get(session_record.load(rec_p), "GX0002")["track"] == "Sonoma"


def test_deleting_a_built_in_track_is_refused_rather_than_silently_coming_back():
    """The built-in Daytona MK seed is NOT in the user's file — `all_tracks` layers it under it — so
    a delete that just drops a user row reports success and the circuit is back on the next launch.
    That silent resurrection is refused with a ValueError the callers already guard."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        raised = None
        try:
            track_db.remove_track("Daytona Milton Keynes", p)
        except ValueError as exc:
            raised = exc
        assert raised is not None, "deleting a built-in reported success and it will just come back"
        assert isinstance(raised, track_db.BuiltInTrack)
        assert "Daytona Milton Keynes" in str(raised)
        assert track_db.detect(52.0403, -0.7847, p) is not None


def test_deleting_a_refined_built_in_says_the_built_in_comes_back():
    """Deleting a user entry that SHADOWS a built-in does not remove the circuit — it reverts to the
    shipped line. That is a different outcome from "it is gone", so the store can be asked which one
    is about to happen (the `replaces` / `backup_pending` ask-before idiom this module already uses)."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        refined = [[52.05, -0.79], [52.051, -0.788]]
        track_db.save_track(_entry("Daytona Milton Keynes", centroid=tuple(_MK_CENTROID),
                                   start=refined), p)
        reverts = track_db.reverts_to_builtin("Daytona Milton Keynes", p)
        assert reverts is not None, "nothing said the built-in would come back"
        assert reverts["start"] == _MK_START
        assert track_db.reverts_to_builtin("Croft", p) is None
        track_db.remove_track("Daytona Milton Keynes", p)
        mk = next(e for e in track_db.all_tracks(p) if e["name"] == "Daytona Milton Keynes")
        assert mk["start"] == _MK_START, "the shipped Daytona MK line did not come back"


def test_rename_to_a_name_already_taken_is_refused():
    """Renaming onto an existing circuit's name would put two entries under one key — and
    `all_tracks` is name-keyed, so one would silently swallow the other. Refused; both survive."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        a_start = [[51.376, -0.361], [51.3761, -0.3608]]
        b_start = [[54.45, -1.55], [54.451, -1.549]]
        track_db.save_track(_entry("Sandown Park", centroid=(51.376, -0.361), start=a_start), p)
        track_db.save_track(_entry("Croft", centroid=(54.45, -1.55), start=b_start), p)
        raised = None
        try:
            track_db.rename_track("Sandown Park", "Croft", p)
        except ValueError as exc:
            raised = exc
        assert raised is not None, "a rename onto a taken name was written silently"
        assert isinstance(raised, track_db.TrackNameInUse)
        assert "Croft" in str(raised)
        stored = {e["name"]: e["start"] for e in track_db.load(p)["tracks"]}
        assert stored == {"Sandown Park": a_start, "Croft": b_start}, stored


def test_rename_to_an_empty_name_is_refused():
    """An empty (or all-blank) name is not a name: `_valid_entry` rejects it, so writing one makes
    the circuit VANISH on the next load. Refused before it reaches disk; the track is untouched."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        start = [[51.376, -0.361], [51.3761, -0.3608]]
        track_db.save_track(_entry("Sandown Park", centroid=(51.376, -0.361), start=start), p)
        for bad in ("", "   ", "\t\n"):
            raised = None
            try:
                track_db.rename_track("Sandown Park", bad, p)
            except ValueError as exc:
                raised = exc
            assert raised is not None, f"a rename to {bad!r} was accepted"
            db = track_db.load(p)
            assert len(db["tracks"]) == 1, f"the circuit vanished renaming to {bad!r}"
            assert db["tracks"][0]["name"] == "Sandown Park"
            assert db["tracks"][0]["start"] == start


def test_rename_keeps_the_line_the_anchor_and_the_detection():
    """A rename is a NAME change and nothing else: same start line (bit-exact), same sectors, same
    anchor, and the circuit still detects — under the new name."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        start = [[37.123456789, -122.987654321], [37.123456790, -122.987654320]]
        sectors = [[[37.1, -122.9], [37.11, -122.89]]]
        track_db.save_track(_entry("Sonom", centroid=(37.12, -122.98), start=start,
                                   sectors=sectors, bbox=[37.1, -123.0, 37.2, -122.9]), p)
        track_db.rename_track("Sonom", "Sonoma Raceway", p)
        db = track_db.load(p)
        assert [e["name"] for e in db["tracks"]] == ["Sonoma Raceway"]
        assert db["tracks"][0]["start"] == start          # exact float equality
        assert db["tracks"][0]["sectors"] == sectors
        assert db["tracks"][0]["bbox"] == [37.1, -123.0, 37.2, -122.9]
        hit = track_db.detect(37.12, -122.98, p)
        assert hit is not None and hit["name"] == "Sonoma Raceway"


def test_renaming_a_built_in_is_refused():
    """The seed is not in the user's file, so "renaming" it writes a SECOND circuit at the same
    anchor under the new name while the built-in stays under the old one — two entries for one
    place, detection picking between them by distance. Refused."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        raised = None
        try:
            track_db.rename_track("Daytona Milton Keynes", "MK", p)
        except ValueError as exc:
            raised = exc
        assert raised is not None, "renaming a built-in silently forked it into two circuits"
        assert isinstance(raised, track_db.BuiltInTrack)
        names = [e["name"] for e in track_db.all_tracks(p)]
        assert names == ["Daytona Milton Keynes"], names


def test_rename_keeps_a_backup_first():
    """A rename rewrites durable history, so it takes the same .bak copy a delete does."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        track_db.save_track(_entry("Sandown Par", centroid=(51.376, -0.361)), p)
        track_db.rename_track("Sandown Par", "Sandown Park", p)
        assert os.path.exists(p + ".bak"), "a rename left no backup"
        old = {e["name"] for e in track_db.load(p + ".bak")["tracks"]}
        assert old == {"Sandown Par"}, old


def test_renaming_a_track_that_is_not_there_is_refused():
    """Renaming a circuit the store does not hold must say so rather than write a new one."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tracks.json")
        track_db.save_track(_entry("Croft", centroid=(54.45, -1.55)), p)
        raised = None
        try:
            track_db.rename_track("Nowhere", "Somewhere", p)
        except ValueError as exc:
            raised = exc
        assert raised is not None, "renaming an absent circuit invented one"
        assert {e["name"] for e in track_db.load(p)["tracks"]} == {"Croft"}


# ---------------------------------------- the satellite stores a rename must carry with it
def test_library_rename_track_carries_the_whole_pb_history():
    """library.rename_track re-keys every entry of one circuit — the PB history, the progression
    series and the session count follow the new name, and nothing else in the index moves."""
    from studio import library
    with tempfile.TemporaryDirectory() as d:
        lib_p = os.path.join(d, "library.json")
        _seed_library("Sonoma", lib_p)
        idx = library.load(lib_p)
        library.upsert(idx, {
            "fingerprint": "GX0009", "stem": "GX010009", "track": "Croft", "date": "2024-08-01",
            "lap_count": 5, "best": 60.0, "theoretical": None,
            "verified": True, "degraded": False, "dropout": False, "paths": []})
        moved = library.rename_track(idx, "Sonoma", "Sonoma Raceway")
        assert moved == 3, moved
        assert library.prior_best(idx, "Sonoma") is None, "the old name still holds a PB"
        assert library.prior_best(idx, "Sonoma Raceway") == 68.0
        assert library.pb_series(idx, "Sonoma Raceway") == [
            ("2024-05-01", 70.0), ("2024-06-01", 68.0), ("2024-07-01", 69.0)]
        assert library.track_summary(idx, "Sonoma Raceway")["sessions"] == 3
        assert library.prior_best(idx, "Croft") == 60.0, "an unrelated circuit was re-keyed"
        assert len(idx["entries"]) == 4


def test_focus_rename_track_carries_the_focus_list():
    """focus.rename_track moves the per-track list, so the corners the driver is working on are
    still found after the circuit is renamed."""
    from studio import focus
    with tempfile.TemporaryDirectory() as d:
        focus_p = os.path.join(d, "focus.json")
        _seed_focus("Sonoma", focus_p)
        store = focus.load(focus_p)
        assert focus.rename_track(store, "Sonoma", "Sonoma Raceway") is True
        assert focus.for_track(store, "Sonoma") == [], "the old name kept the list"
        moved = focus.for_track(store, "Sonoma Raceway")
        assert len(moved) == 1 and moved[0].cid == 3
        assert focus.rename_track(store, "Nowhere", "Anywhere") is False


def test_session_record_rename_track_carries_the_stamp():
    """The record's `track` is provenance, and the library entry beside it is about to say the new
    name — two surfaces showing one quantity must agree, so the stamp moves too."""
    from studio import session_record
    with tempfile.TemporaryDirectory() as d:
        rec_p = os.path.join(d, "session_records.json")
        _seed_records("Sonoma", rec_p)
        store = session_record.load(rec_p)
        assert session_record.rename_track(store, "Sonoma", "Sonoma Raceway") == 1
        assert session_record.get(store, "GX0002")["track"] == "Sonoma Raceway"
        assert session_record.rename_track(store, "Nowhere", "Anywhere") == 0


# ===================================== the manager dialog + the app's four-store gesture
# Offscreen Qt, driving the REAL widget (a dialog test that re-implements the rule carries a copy
# of the defect). The app half builds a bare StudioWindow the way test_save_track_guard does.

def _dialog_rows():
    """The row model the app hands the dialog: a built-in, a refined built-in and a user track."""
    return [
        {"name": "Daytona Milton Keynes", "builtin": True, "editable": False, "sectors": 0},
        {"name": "Sandown Park", "builtin": False, "editable": True, "sectors": 2},
    ]


def _track_dialog(**kw):
    """A real TrackManagerDialog over `_dialog_rows()`, with any callback overridden."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from studio.track_dialog import TrackManagerDialog
    return TrackManagerDialog(_dialog_rows(), **kw)


def test_dialog_never_offers_to_edit_a_built_in():
    """A built-in cannot be renamed or deleted, so its row is not selectable and the two buttons
    stay off — the refusal is not left to a modal the user reaches by trying."""
    from PySide6.QtCore import Qt

    from studio.track_dialog import NAME_ROLE
    dlg = _track_dialog(rename_track=lambda *a: _dialog_rows(),
                        delete_track=lambda *a: _dialog_rows())
    builtin_row = next(i for i in range(dlg.list.count())
                       if dlg.list.item(i).data(NAME_ROLE) == "Daytona Milton Keynes")
    item = dlg.list.item(builtin_row)
    assert not (item.flags() & Qt.ItemIsSelectable), "a built-in row is selectable"
    assert not (item.flags() & Qt.ItemIsEnabled), "a built-in row is enabled"
    # The selection landed on the editable row instead, so the buttons ARE armed for that one.
    assert dlg._selected()["name"] == "Sandown Park"
    assert dlg.rename_btn.isEnabled() and dlg.delete_btn.isEnabled()
    dlg.deleteLater()


def test_dialog_rename_routes_the_typed_name_through_the_callback(monkeypatch):
    """The real rename path: the dialog asks for a name and hands (old, new) to the injected
    callback, then re-renders from the rows it returns."""
    from studio import track_dialog
    calls = []

    def _renamed(old, new):
        calls.append((old, new))
        return [{"name": "Sandown Park Karting", "builtin": False, "editable": True, "sectors": 2}]

    monkeypatch.setattr(track_dialog.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("  Sandown Park Karting  ", True)))
    dlg = _track_dialog(rename_track=_renamed)
    dlg._rename_selected()
    assert calls == [("Sandown Park", "Sandown Park Karting")], calls
    assert dlg.list.count() == 1
    assert "Sandown Park Karting" in dlg.list.item(0).text()
    dlg.deleteLater()


def test_dialog_shows_the_stores_refusal_and_keeps_the_list(monkeypatch):
    """A refusal reaches the user in the STORE's own words, and nothing in the list moves."""
    from studio import track_dialog
    warned = []

    def _refuse(old, new):
        raise track_db.TrackNameInUse(new)

    monkeypatch.setattr(track_dialog.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("Croft", True)))
    monkeypatch.setattr(track_dialog.QMessageBox, "warning",
                        staticmethod(lambda parent, title, text: warned.append((title, text))))
    dlg = _track_dialog(rename_track=_refuse)
    dlg._rename_selected()
    assert len(warned) == 1, warned
    assert "already saved" in warned[0][1], warned
    assert dlg.list.count() == 2, "a refused rename changed the list"
    dlg.deleteLater()


def test_dialog_delete_asks_first_and_a_declined_confirm_deletes_nothing(monkeypatch):
    """Destructive and confirmed: No means the callback is never called; Yes means it is. The
    confirm names what survives (the analysed sessions) and where the copy went."""
    from studio import track_dialog
    calls = []
    asked = []

    def _answer(value):
        def _q(parent, title, text, buttons=None, default=None):
            asked.append(text)
            return value
        return staticmethod(_q)

    monkeypatch.setattr(track_dialog.QMessageBox, "question",
                        _answer(track_dialog.QMessageBox.No))
    dlg = _track_dialog(delete_track=lambda name: calls.append(name) or [])
    dlg._delete_selected()
    assert calls == [], "a declined confirm still deleted the circuit"
    assert asked and "personal-best history" in asked[0], asked
    assert "tracks.json.bak" in asked[0], asked
    monkeypatch.setattr(track_dialog.QMessageBox, "question",
                        _answer(track_dialog.QMessageBox.Yes))
    dlg._delete_selected()
    assert calls == ["Sandown Park"], calls
    dlg.deleteLater()


def test_dialog_delete_of_a_refined_built_in_says_the_built_in_comes_back(monkeypatch):
    """Two different outcomes must not read as one sentence: deleting a refined built-in restores
    pacer's own line rather than removing the circuit, and the confirm says so."""
    from studio import track_dialog
    asked = []
    monkeypatch.setattr(track_dialog.QMessageBox, "question", staticmethod(
        lambda parent, title, text, buttons=None, default=None:
        asked.append(text) or track_dialog.QMessageBox.No))
    dlg = _track_dialog(delete_track=lambda name: [],
                        reverts_to_builtin=lambda name: {"name": name, "start": _MK_START})
    dlg._delete_selected()
    assert asked, "no confirm was shown"
    assert "built-in" in asked[0] and "comes back" in asked[0], asked[0]
    dlg.deleteLater()


def test_app_rename_carries_every_store_the_name_keys(monkeypatch):
    """THE COMPOSITION. LibraryController._rename_track moves the circuit in the track DB and
    re-keys the three stores that file things under its NAME, so one circuit keeps one
    history."""
    if not _pacer_available():
        print("skip test_app_rename_carries_every_store_the_name_keys (no pacer)")
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from studio import app as studio_app
    from studio import focus, library, session_record
    from studio.library_controller import LibraryController
    with tempfile.TemporaryDirectory() as d:
        for mod in (track_db, library, focus, session_record):
            monkeypatch.setattr(mod, "_app_support_dir", lambda _d=d: _d)
        track_db.save_track(_entry("Sonom", centroid=(37.12, -122.98)))
        _seed_library("Sonom", library.library_path())
        _seed_focus("Sonom", focus.focus_path())
        _seed_records("Sonom", session_record.records_path())

        win = studio_app.StudioWindow.__new__(studio_app.StudioWindow)
        ctl = LibraryController(win, studio_app.STATUS_MS)
        rows = ctl._rename_track("Sonom", "Sonoma Raceway")

        assert {e["name"] for e in track_db.load()["tracks"]} == {"Sonoma Raceway"}
        idx = library.load()
        assert library.prior_best(idx, "Sonoma Raceway") == 68.0, "the PB history did not follow"
        assert library.prior_best(idx, "Sonom") is None, "the old name kept a PB history"
        assert len(idx["entries"]) == 3, "the re-key dropped a session"
        assert len(focus.for_track(focus.load(), "Sonoma Raceway")) == 1, "the focus list stayed"
        assert session_record.get(session_record.load(), "GX0002")["track"] == "Sonoma Raceway"
        assert [r["name"] for r in rows if r["editable"]] == ["Sonoma Raceway"], rows


def test_app_delete_leaves_every_analysed_session_alone(monkeypatch):
    """The dangling-reference guard at the APP level: the gesture a user actually fires deletes the
    circuit and touches none of the three stores that reference it by name."""
    if not _pacer_available():
        print("skip test_app_delete_leaves_every_analysed_session_alone (no pacer)")
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from studio import app as studio_app
    from studio import focus, library, session_record
    from studio.library_controller import LibraryController
    with tempfile.TemporaryDirectory() as d:
        for mod in (track_db, library, focus, session_record):
            monkeypatch.setattr(mod, "_app_support_dir", lambda _d=d: _d)
        track_db.save_track(_entry("Sonoma", centroid=(37.12, -122.98)))
        _seed_library("Sonoma", library.library_path())
        _seed_focus("Sonoma", focus.focus_path())
        _seed_records("Sonoma", session_record.records_path())

        win = studio_app.StudioWindow.__new__(studio_app.StudioWindow)
        ctl = LibraryController(win, studio_app.STATUS_MS)
        rows = ctl._delete_track("Sonoma")

        assert [r["name"] for r in rows] == ["Daytona Milton Keynes"], rows
        assert track_db.load()["tracks"] == []
        idx = library.load()
        assert len(idx["entries"]) == 3, "deleting a circuit dropped analysed sessions"
        assert library.prior_best(idx, "Sonoma") == 68.0, "deleting a circuit lost its PB history"
        assert len(focus.for_track(focus.load(), "Sonoma")) == 1
        assert session_record.get(session_record.load(), "GX0002")["track"] == "Sonoma"
        assert os.path.exists(track_db.backup_path()), "the app's delete left no backup"


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
