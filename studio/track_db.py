"""Persisted track database: multiple named tracks, each carrying its start/finish line (and
any sector lines) as ABSOLUTE lat/lon, keyed/detected by GPS location.

This is the persistence + lookup layer behind ``studio.tracks`` (which stays the pacer-touching
geometry layer). It mirrors ``studio.library``: one JSON index in the macOS app-support dir,
atomic write, file-level corruption -> safe empty + one bad entry dropped (the rest kept).
PACER-FREE BY CONTRACT — pure path resolution, schema validation, lat/lon math and JSON I/O;
the lat/lon <-> local-metre conversion lives in session.py / tracks.py.

A track entry is location-anchored: its timing lines are stored in lat/lon so they map onto ANY
recording of that circuit (via the recording's own CoordinateSystem), and it carries a detection
centroid + bbox so a fresh recording auto-detects the track on load.

The Daytona Milton Keynes line is a BUILT-IN SEED (``SEED``), so a first-ever run already
auto-detects MK with its measured line — its timing is identical to the old hardcoded entry. The
user DB is merged ON TOP of the seed (a user entry of the same name overrides the seed), so
``Save as track…`` can refine a built-in too.

Schema (version 1) — one JSON object::

    {"version": 1,
     "tracks": [
       {"name":         "Daytona Milton Keynes",
        "centroid":     [lat, lon],              # detection anchor (trace bbox centre)
        "bbox":         [min_lat, min_lon, max_lat, max_lon] | null,  # rough extent (optional)
        "start":        [[lat, lon], [lat, lon]],          # start/finish line
        "sectors":      [[[lat, lon], [lat, lon]], ...]},  # 0+ sector lines
       ...]}

Float round-trip: json writes floats with ``repr`` (shortest EXACT double string), so
save->load returns bit-identical endpoints.
"""

from __future__ import annotations

import json
import logging
import math
import os

_log = logging.getLogger(__name__)

VERSION = 1

_FILENAME = "tracks.json"
_APP_DIR_NAME = "pacer"

# Match a trace to a track when its centroid is within this many metres of the entry's detection
# centroid (generous — GPS centroids drift with how much of an out-lap is kept). Shared with the
# old hardcoded radius so detection behaviour is unchanged for the seed entry.
DETECT_RADIUS_M = 1500.0
EARTH_RADIUS_M = 6_371_000.0

# Built-in seed: the measured Daytona MK line (was hardcoded in tracks.REGISTRY). Its start
# endpoints are byte-identical to the old entry, so MK timing does not regress. No sectors / bbox
# in the seed (the old entry had neither) — detection is centroid-only, exactly as before.
SEED: list[dict] = [
    {
        "name": "Daytona Milton Keynes",
        "centroid": [52.0403, -0.7847],
        "bbox": None,
        "start": [[52.04031, -0.78487], [52.04020, -0.78460]],
        "sectors": [],
    },
]


def _app_support_dir() -> str:
    """macOS app-support dir for pacer (the single seam tests monkeypatch so the suite never
    touches the real DB). Same location/idiom as ``library._app_support_dir``."""
    return os.path.join(
        os.path.expanduser("~"), "Library", "Application Support", _APP_DIR_NAME)


def db_path() -> str:
    """Absolute path to the track DB (``<app-support>/pacer/tracks.json``). Resolves the
    app-support dir through ``_app_support_dir`` so a patched seam is honoured. Does NOT create
    the directory — that happens lazily on the first ``save``."""
    return os.path.join(_app_support_dir(), _FILENAME)


def empty_db() -> dict:
    """A fresh, valid, empty DB — the safe default every corruption path returns to (the seed is
    layered on top by ``detect``, NOT stored here, so a user's file only ever holds user tracks)."""
    return {"version": VERSION, "tracks": []}


def equirect_metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Equirectangular metres between two lat/lon points (accurate over a few km). The single
    distance helper detection uses; matches the old tracks._equirect_metres exactly."""
    lat0 = math.radians((lat1 + lat2) / 2)
    dx = math.radians(lon2 - lon1) * math.cos(lat0) * EARTH_RADIUS_M
    dy = math.radians(lat2 - lat1) * EARTH_RADIUS_M
    return math.hypot(dx, dy)


def _valid_line(line) -> bool:
    """True iff `line` is [[lat, lon], [lat, lon]] with four finite in-range numbers. Same rule
    as the sidecar's _valid_line — a timing line is the same shape in both stores."""
    if not isinstance(line, (list, tuple)) or len(line) != 2:
        return False
    for pt in line:
        if not isinstance(pt, (list, tuple)) or len(pt) != 2:
            return False
        for v in pt:
            # bool is an int subclass — reject it explicitly (true/false isn't a coordinate).
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                return False
        lat, lon = pt
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            return False
    return True


def _valid_latlon(pt) -> bool:
    """True iff `pt` is a finite, in-range [lat, lon] pair (the centroid)."""
    if not isinstance(pt, (list, tuple)) or len(pt) != 2:
        return False
    for v in pt:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return False
    lat, lon = pt
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def _valid_bbox(bbox) -> bool:
    """True iff `bbox` is null or [min_lat, min_lon, max_lat, max_lon] with finite in-range
    numbers and min<=max on each axis."""
    if bbox is None:
        return True
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return False
    for v in bbox:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return False
    min_lat, min_lon, max_lat, max_lon = bbox
    if not (-90.0 <= min_lat <= max_lat <= 90.0):
        return False
    return -180.0 <= min_lon <= max_lon <= 180.0


def _valid_entry(e) -> bool:
    """True iff `e` is a structurally valid track entry; load() drops invalid rows (keeps the
    rest), the same entry-tolerant self-heal the library uses."""
    if not isinstance(e, dict):
        return False
    name = e.get("name")
    if not isinstance(name, str) or not name:
        return False
    if not _valid_latlon(e.get("centroid")):
        return False
    if not _valid_bbox(e.get("bbox")):
        return False
    if not _valid_line(e.get("start")):
        return False
    sectors = e.get("sectors", [])
    return isinstance(sectors, list) and all(_valid_line(s) for s in sectors)


def _norm_line(line) -> list[list[float]]:
    return [[float(line[0][0]), float(line[0][1])], [float(line[1][0]), float(line[1][1])]]


def _norm_entry(e: dict) -> dict:
    """Canonicalize a validated entry to the stored shape + key order."""
    bbox = e.get("bbox")
    return {
        "name": str(e["name"]),
        "centroid": [float(e["centroid"][0]), float(e["centroid"][1])],
        "bbox": None if bbox is None else [float(v) for v in bbox],
        "start": _norm_line(e["start"]),
        "sectors": [_norm_line(s) for s in e.get("sectors", [])],
    }


def load(path: str | None = None) -> dict:
    """Load + validate the track DB, returning the normalized dict. File-level corruption
    (absent / unreadable / not JSON / not a dict / wrong version / non-list ``tracks``) ->
    ``empty_db()``; a single malformed entry is dropped (count logged), the rest kept. `path`
    defaults to ``db_path()``."""
    if path is None:
        path = db_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return empty_db()
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return empty_db()
    raw = data.get("tracks")
    if not isinstance(raw, list):
        return empty_db()
    tracks = [e for e in raw if _valid_entry(e)]
    dropped = len(raw) - len(tracks)
    if dropped:
        # A later save rewrites only the survivors, healing the file.
        _log.warning("track_db: dropped %d malformed track%s of %d from %s",
                     dropped, "" if dropped == 1 else "s", len(raw), path)
    return {"version": VERSION, "tracks": [_norm_entry(e) for e in tracks]}


def save(db: dict, path: str | None = None) -> None:
    """Write the DB atomically (temp file + ``os.replace``) so a crash mid-write can't leave a
    truncated DB. Creates the app-support dir if missing. `path` defaults to ``db_path()``.
    Raises OSError on an unwritable destination."""
    if path is None:
        path = db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out = {"version": VERSION, "tracks": [_norm_entry(e) for e in db.get("tracks", [])]}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def upsert(db: dict, entry: dict) -> dict:
    """Insert `entry`, or REPLACE the existing track with the same NAME (the no-duplicate rule).
    Mutates and returns `db`. A replacement keeps the entry's POSITION; a new name appends.
    `entry` must be a valid entry dict; it is normalized on store."""
    norm = _norm_entry(entry)
    tracks = db.setdefault("tracks", [])
    for i, e in enumerate(tracks):
        if e.get("name") == norm["name"]:
            tracks[i] = norm
            return db
    tracks.append(norm)
    return db


def make_entry(name: str, centroid, start, sectors, bbox=None) -> dict:
    """Build a (validated) track entry from a name + detection centroid + lat/lon timing lines
    (the ``Session.timing_lines_latlon`` shape) + an optional bbox. Raises ValueError if the
    inputs don't form a valid entry, so a bad Save-as-track is rejected before it touches disk."""
    entry = {
        "name": name,
        "centroid": list(centroid),
        "bbox": None if bbox is None else list(bbox),
        "start": start,
        "sectors": list(sectors),
    }
    if not _valid_entry(entry):
        raise ValueError("invalid track entry")
    return _norm_entry(entry)


def all_tracks(path: str | None = None) -> list[dict]:
    """Every known track: the built-in SEED with the persisted user DB layered ON TOP (a user
    entry of the same name overrides its seed, so a refined built-in wins). Each is a normalized
    entry dict. This is the merged view detection + the app read from."""
    merged: dict[str, dict] = {}
    for e in SEED:
        merged[e["name"]] = _norm_entry(e)
    for e in load(path).get("tracks", []):
        merged[e["name"]] = e  # already normalized by load()
    return list(merged.values())


def detect(lat: float, lon: float, path: str | None = None) -> dict | None:
    """The known track whose detection centroid is within DETECT_RADIUS_M of (lat, lon), or None;
    the NEAREST if several match. Searches the merged SEED+user view, so a built-in and a
    user-saved track are both auto-detectable. Returns the normalized entry dict (or None)."""
    best, best_d = None, DETECT_RADIUS_M
    for e in all_tracks(path):
        clat, clon = e["centroid"]
        d = equirect_metres(lat, lon, clat, clon)
        if d <= best_d:
            best, best_d = e, d
    return best


def save_track(entry: dict, path: str | None = None) -> dict:
    """Load the current DB, upsert `entry`, write it back atomically, return the new DB. The one
    call the app's Save-as-track makes. Any OSError from the write propagates to the caller, which
    guards it (a DB write must never disrupt the session — mirror library.upsert_and_save)."""
    db = load(path)
    upsert(db, entry)
    save(db, path)
    return db
