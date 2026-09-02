"""Persisted track database: multiple named tracks, each carrying its start/finish line (and
any sector lines) as ABSOLUTE lat/lon, keyed/detected by GPS location.

This is the persistence + lookup layer behind ``studio.tracks`` (which stays the pacer-touching
geometry layer). It mirrors ``studio.library``: one JSON index in the macOS app-support dir,
atomic write, file-level corruption -> safe empty + one bad entry dropped (the rest kept).
PACER-FREE BY CONTRACT — pure path resolution, schema validation, lat/lon math and JSON I/O;
the lat/lon <-> local-metre conversion lives in session.py / tracks.py.

A circuit here is DURABLE HISTORY — a start/finish line the user placed by hand, that every
future recording at that location inherits — so a read fallback must never become a WRITE that
destroys it. Two guarantees, both borrowed from ``studio.library`` (which grew them first) after
one ordinary ``Save as track…`` over a half-written file emptied a three-circuit DB:

  * a ``version`` that is not this build's is read BEST-EFFORT (every entry that still validates
    is kept), NOT treated as corruption — a file from a newer build survives a downgrade;
  * before ``save`` overwrites a file this build could not round-trip in full — unreadable, a
    different schema version, or holding an entry that failed validation — the original bytes are
    copied to ``tracks.json.bak`` (``_backup_unsafe``), so nothing is ever silently lost. Ask
    ``backup_pending()`` BEFORE the write to also TELL the user it happened.

A track entry is location-anchored: its timing lines are stored in lat/lon so they map onto ANY
recording of that circuit (via the recording's own CoordinateSystem), and it carries a detection
centroid + bbox so a fresh recording auto-detects the track on load.

The Daytona Milton Keynes line is a BUILT-IN SEED (``SEED``), so a first-ever run already
auto-detects MK with its measured line — its timing is identical to the old hardcoded entry. The
user DB is merged ON TOP of the seed (a user entry of the same name overrides the seed), so
``Save as track…`` can refine a built-in too. Reusing a name for a DIFFERENT place is a different
act — it destroys that circuit's stored lines — so it is REFUSED (``TrackNameTaken``) until the
caller confirms; see ``save_track`` / ``replaces``.

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
import shutil

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


class TrackNameTaken(ValueError):
    """Raised by ``save_track`` when the entry would OVERWRITE a known track of the same NAME
    anchored somewhere ELSE — one circuit's stored start/sector lines destroyed by another's,
    with nothing on screen to say so. Carries the stored entry (``existing``) and the distance
    between the two anchors (``distance_m``) so the caller can NAME what it is about to replace
    and ask first (see ``replaces``); pass ``replace=True`` once the user has confirmed.

    A ValueError subclass on purpose: every caller of the track DB already guards it against
    ValueError (a rejected entry), so an unaware one refuses the write and reports instead of
    silently destroying the other track."""

    def __init__(self, existing: dict, distance_m: float):
        self.existing = existing
        self.distance_m = float(distance_m)
        # Reads as a status line too: the message reaches the user verbatim through the app's
        # existing `except (OSError, ValueError)` guard, so it names the conflict and one action a
        # caller that has NOT yet grown a confirm can actually offer.
        super().__init__(
            f"a different circuit is already saved as {existing['name']!r} "
            f"({distance_m / 1000:.1f} km away) — save this one under another name")


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
    """A fresh, valid, empty DB — the safe default a FILE-level corruption falls back to (the seed
    is layered on top by ``detect``, NOT stored here, so a user's file only ever holds user
    tracks). A read fallback only: ``save`` backs the unreadable file up before this empty view
    could ever overwrite it."""
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


def _is_loadable_dict(path: str) -> tuple[bool, dict | None]:
    """(readable_json_object, parsed) for `path`: True/parsed when the file exists and parses to a
    JSON object, else (False, None). The seam ``load`` and ``_lossy_to_overwrite`` share, so
    "genuine file-level corruption" is decided in exactly ONE place — the two must never disagree
    about whether a file was readable, or a save would skip the backup for a file it then wipes.
    Mirrors ``library._is_loadable_dict``."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False, None
    if not isinstance(data, dict):
        return False, None
    return True, data


def _schema_version(data: dict) -> int | None:
    """The file's ``version`` when it is a real schema number (a plain int), else None. A missing
    or non-int version is untrustworthy SHAPE, not a version, so the caller treats it as
    corruption; bool is an int subclass and is rejected explicitly."""
    v = data.get("version")
    if isinstance(v, bool) or not isinstance(v, int):
        return None
    return v


def load(path: str | None = None) -> dict:
    """Load + validate the track DB, returning the normalized dict. NEVER discards a circuit it
    can still read:

      * a ``version`` that is not this build's is read BEST-EFFORT (every entry that still
        validates is kept, unknown fields ignored) rather than treated as corruption — a DB
        written by a newer build survives a downgrade instead of coming back empty. ``save``
        copies the file to ``<path>.bak`` before re-stamping it to this build's VERSION, and a
        real schema bump adds its forward transform here (a version bump must MIGRATE, never
        wipe — the rule ``library`` already states);
      * a single malformed entry is dropped (count logged), the rest kept.

    Only genuine FILE-level corruption (absent / unreadable / not JSON / not a dict / missing or
    non-int ``version`` / non-list ``tracks``) -> ``empty_db()`` — and even then the original
    bytes are copied to ``<path>.bak`` before any write replaces them, so an empty read can no
    longer become permanent loss. `path` defaults to ``db_path()``."""
    if path is None:
        path = db_path()
    ok, data = _is_loadable_dict(path)
    if not ok:
        return empty_db()
    version = _schema_version(data)
    if version is None:
        return empty_db()
    if version != VERSION:
        _log.warning("track_db: %s is schema version %d, not this build's %d — reading it "
                     "best-effort; save() backs it up before re-stamping it",
                     path, version, VERSION)
    raw = data.get("tracks")
    if not isinstance(raw, list):
        return empty_db()
    tracks = [e for e in raw if _valid_entry(e)]
    dropped = len(raw) - len(tracks)
    if dropped:
        # A later save rewrites only the survivors, healing the file — with the original kept as
        # a .bak first (_backup_unsafe), so the dropped row is recoverable rather than gone.
        _log.warning("track_db: dropped %d malformed track%s of %d from %s (the original is "
                     "copied to %s.bak before the next save rewrites it)",
                     dropped, "" if dropped == 1 else "s", len(raw), path,
                     os.path.basename(path))
    return {"version": VERSION, "tracks": [_norm_entry(e) for e in tracks]}


def _lossy_to_overwrite(path: str) -> bool:
    """True when rewriting `path` from ``load(path)``'s view would LOSE something the file holds —
    the one condition that earns a ``.bak``. False for a healthy file that round-trips, so the
    ordinary save never churns a backup.

    Lossy in exactly the shapes that destroyed circuits in the field:
      * the file does not parse to a JSON object, its ``version`` is missing / not an int, or
        ``tracks`` is not a list — ``load`` returns ``empty_db()``, so the save writes an EMPTY DB
        over every circuit the file held;
      * ``version`` is not this build's — ``load`` reads it best-effort and the save re-stamps it,
        dropping any field this build does not know;
      * one or more entries fail validation — ``load`` keeps the rest and the save persists only
        the survivors. Healing the file is defensible; doing it without keeping the original
        is not."""
    ok, data = _is_loadable_dict(path)
    if not ok:
        return True
    if _schema_version(data) != VERSION:
        return True
    raw = data.get("tracks")
    if not isinstance(raw, list):
        return True
    return not all(_valid_entry(e) for e in raw)


def backup_pending(path: str | None = None) -> str | None:
    """The ``<path>.bak`` the next ``save`` would leave behind, or None when the stored DB
    round-trips cleanly. The pre-save question a UI asks — the same idiom as ``replaces`` — so it
    can TELL the user that some of their saved circuits could not be read and name where the
    rescued copy went. `path` defaults to ``db_path()``.

    A ``.bak`` nobody is told about is only half a rescue: it makes the loss RECOVERABLE, not
    VISIBLE. This is the hook that closes that half; ``save`` writes the copy either way, so a
    caller that never asks still cannot destroy anything irrecoverably."""
    if path is None:
        path = db_path()
    return path + ".bak" if os.path.exists(path) and _lossy_to_overwrite(path) else None


def _backup_unsafe(path: str) -> str | None:
    """Before ``save`` OVERWRITES an on-disk DB this build could not round-trip in full, copy it to
    a ``<path>.bak`` sidecar so the user's original circuits are never silently lost; returns the
    backup path written, else None. Mirrors ``library._backup_unsafe``, which the session index has
    had since PR #55 — the track DB shipped without it, and one ordinary Save-as-track over a
    half-written file destroyed three circuits' start/finish lines with no copy and no warning.

    Best-effort, and it MUST NOT block the write: a failed copy only logs (a save that keeps the
    app usable beats refusing to save because the backup slot is unwritable). ``shutil.copy2``
    preserves mtime; the ``.bak`` is overwritten each time, so it mirrors the last replaced-yet-
    unreadable file rather than accumulating — and a healthy file never touches it, so the copy
    of a bad file survives every later save."""
    if not os.path.exists(path) or not _lossy_to_overwrite(path):
        return None
    dest = path + ".bak"
    try:
        shutil.copy2(path, dest)
    except OSError as exc:
        _log.warning("track_db: could not back up %s before overwrite (%r)", path, exc)
        return None
    _log.warning("track_db: %s could not be read in full — copied it to %s before overwriting",
                 os.path.basename(path), os.path.basename(dest))
    return dest


def save(db: dict, path: str | None = None) -> None:
    """Write the DB atomically (temp file + ``os.replace``) so a crash mid-write can't leave a
    truncated DB. Creates the app-support dir if missing. `path` defaults to ``db_path()``.
    Raises OSError on an unwritable destination.

    DATA-SAFETY: before overwriting an existing file this build could not round-trip (unreadable,
    a different schema version, or holding an entry that failed validation), the original is first
    copied to a ``tracks.json.bak`` sidecar (``_backup_unsafe``) — so no ordinary Save-as-track
    can destroy circuits it never managed to read. Call ``backup_pending()`` BEFORE this to also
    tell the user it happened."""
    if path is None:
        path = db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _backup_unsafe(path)
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


def _clash(known: list[dict], norm: dict) -> tuple[dict, float] | None:
    """The known track `norm` would DESTROY, paired with the metres between the two anchors: an
    entry of the same NAME anchored further than DETECT_RADIUS_M away. Inside that radius the two
    are the SAME circuit by the app's own detection rule, so re-saving a track to refine its lines
    (the documented Save-as-track flow, built-ins included) is not a clash — only reusing a name
    for a different place is. Names are unique in the merged view, so the first match is the only
    one."""
    for e in known:
        if e.get("name") == norm["name"]:
            d = equirect_metres(norm["centroid"][0], norm["centroid"][1],
                                e["centroid"][0], e["centroid"][1])
            return (e, d) if d > DETECT_RADIUS_M else None
    return None


def replaces(entry: dict, path: str | None = None) -> dict | None:
    """The stored track `entry` would overwrite — a DIFFERENT circuit saved under the same name —
    or None when the save is safe. The question a caller asks BEFORE ``save_track`` so its confirm
    can name the circuit whose lines are about to go (``track_db.replaces(e)`` → the entry dict).
    Searched over the merged SEED+user view, because that view is name-keyed: a user entry
    shadowing a built-in of the same name takes its detection with it."""
    clash = _clash(all_tracks(path), _norm_entry(entry))
    return None if clash is None else clash[0]


def save_track(entry: dict, path: str | None = None, *, replace: bool = False) -> dict:
    """Load the current DB, upsert `entry`, write it back atomically, return the new DB. The one
    call the app's Save-as-track makes. Any OSError from the write propagates to the caller, which
    guards it (a DB write must never disrupt the session — mirror library.upsert_and_save).

    REFUSES, with ``TrackNameTaken``, to overwrite a different circuit stored under the same name
    (see ``_clash``): that write is silent data loss — the other track's start line, its sector
    lines and its GPS anchor, with no undo and no second copy. ``replace=True`` is the confirmed
    path; refining the lines of the track already saved at this location never needs it.

    Where the name clash is REFUSED (the caller can name what is at risk), a DB the build could
    not read is instead written THROUGH — refusing there would leave the user unable to save any
    track at all until they hand-repaired a file the app never shows them. ``save`` keeps their
    original bytes as ``tracks.json.bak`` instead; ``backup_pending()`` says so beforehand."""
    db = load(path)
    norm = _norm_entry(entry)
    if not replace:
        clash = _clash(all_tracks(path), norm)
        if clash is not None:
            raise TrackNameTaken(*clash)
    upsert(db, norm)
    save(db, path)
    return db
