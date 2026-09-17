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

The two DELIBERATE destructive acts — ``rename_track`` and ``remove_track``, the editing half of
``Save as track…`` — are held to the same rule and then some: each copies the file to that same
``tracks.json.bak`` FIRST (``_copy_to_backup``), and ``restore`` puts it back as a reversible swap,
exactly as ``library.clear``/``restore`` and ``session_record.remove_and_save``/``restore`` do for
their own stores. Both REFUSE rather than write a silently wrong answer: a blank name (which would
make the circuit vanish on the next read), a name already in use (the merged view is name-keyed, so
one entry would swallow another), and a BUILT-IN the user's file does not hold — the seed is layered
under that file, so "deleting" it would drop nothing and report success while the circuit came back
on the next launch, and "renaming" it would fork one place into two circuits.

WHAT A DELETE DOES NOT TOUCH: the recordings. A track NAME is an identity key in three other stores
— the library index files a circuit's personal-best history under it, the focus list is keyed by it,
and a session record stamps it as provenance — but all three match on the string they already hold
rather than looking a circuit up here, so a deleted name is a dangling NAME and never a dangling
pointer. Every analysed session keeps the name it was driven under; a delete removes future
auto-detection and nothing else. A RENAME is the opposite case and the dangerous one: it must be
carried into all three, or the next recording auto-detects the new name and one circuit ends up with
two half-histories. Each of those stores has its own ``rename_track``, and the app composes the four
(``LibraryController._rename_track``).

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

from . import app_support

_log = logging.getLogger(__name__)

VERSION = 1

_FILENAME = "tracks.json"

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


class TrackNameInUse(ValueError):
    """Raised by ``rename_track`` when the NEW name already names a saved circuit.

    DIFFERENT RULE FROM ``TrackNameTaken``, on purpose. That one is about re-saving a recording's
    lines under a name anchored somewhere else, and it deliberately allows the same-place case (that
    is the documented refine flow). A RENAME onto an existing name is refused at ANY distance: the
    merged view (``all_tracks``) is keyed by name, so two entries sharing one means the second
    silently swallows the first, and renaming is never the gesture that should merge two circuits.

    A ValueError subclass for the same reason ``TrackNameTaken`` is: every caller of this module
    already guards ValueError, so an unaware one refuses the write and reports instead."""

    def __init__(self, name: str):
        self.name = str(name)
        super().__init__(f"a track called {self.name!r} is already saved — pick another name")


class BuiltInTrack(ValueError):
    """Raised when a rename or delete targets a BUILT-IN (``SEED``) circuit the user's own file does
    not hold a copy of. The seed is layered UNDER the user DB by ``all_tracks``, so editing the user
    file cannot reach it, and doing it anyway gives a silently wrong answer either way:

      * a DELETE would drop nothing, report success, and the circuit would be back on next launch;
      * a RENAME would write a SECOND entry at the same anchor under the new name while the built-in
        kept the old one — two circuits for one place, detection choosing between them by distance.

    So the act is refused and named instead. Refining a built-in (``Save as track…`` at that
    location) creates a user entry that CAN be renamed or deleted; deleting THAT one reverts to the
    shipped line rather than removing the circuit — see ``reverts_to_builtin``."""

    def __init__(self, name: str):
        self.name = str(name)
        super().__init__(
            f"{self.name!r} is one of pacer's built-in tracks, so it cannot be renamed or deleted")


def _app_support_dir() -> str:
    """macOS app-support dir for pacer (the single seam tests monkeypatch so the suite never
    touches the real DB). Same location/idiom as ``library._app_support_dir``, resolved through
    ``app_support.resolve``."""
    return app_support.resolve()


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


def unreadable(path: str | None = None) -> bool:
    """True when a track DB FILE EXISTS and this build cannot read a single circuit out of it — the
    three file-level shapes ``load`` answers with ``empty_db()``: it does not parse to a JSON
    object, its ``version`` is missing / not an int, or ``tracks`` is not a list.

    DELIBERATELY NARROWER than ``_lossy_to_overwrite``: a DB with one malformed entry among five
    good ones is not unreadable, it is repaired-on-read, and telling the user "your saved tracks
    couldn't be read" there would be false. Absent is False for the same reason absent is silent for
    a sidecar — a user with no saved circuits has nothing wrong.

    Exists because the silence was total: a corrupt tracks.json made every recording open as
    "unknown track — start/finish line was auto-fitted", indistinguishable from a genuinely new
    circuit, with nothing on screen naming the file (QA D2-16). Reading it is the caller's job (see
    StudioWindow._session_notice), so this stays a pure question with no Qt in it. `path` defaults
    to ``db_path()``."""
    if path is None:
        path = db_path()
    if not os.path.exists(path):
        return False
    ok, data = _is_loadable_dict(path)
    if not ok:
        return True
    if _schema_version(data) is None:
        return True
    return not isinstance(data.get("tracks"), list)


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


def backup_path(path: str | None = None) -> str:
    """Absolute path of the DB's backup sidecar (``tracks.json.bak``) — the ONE slot every backup
    here writes and ``restore`` reads, so "where the copy went" is stated in one place. `path`
    defaults to ``db_path()``. Same shape as ``library.backup_path`` /
    ``session_record.backup_path``."""
    if path is None:
        path = db_path()
    return path + ".bak"


def _copy_to_backup(path: str, what: str) -> bool:
    """Copy `path` to its ``.bak`` sidecar, best-effort; True on success.

    Shared by all three reasons a backup is taken here — an un-round-trippable file about to be
    overwritten (``_backup_unsafe``), a DELETE and a RENAME — so all three land in the same slot
    with the same guarantees: ``shutil.copy2`` (mtime preserved), one slot overwritten rather than
    accumulating, and ANY failure logged instead of raised (a backup must never be the reason a
    write the user asked for doesn't happen). Mirrors ``library._copy_to_backup`` /
    ``session_record._copy_to_backup``."""
    try:
        shutil.copy2(path, backup_path(path))
    except OSError as exc:
        _log.warning("track_db: could not back up %s before overwrite (%r)", path, exc)
        return False
    _log.warning("track_db: backed up %s to %s", what, os.path.basename(backup_path(path)))
    return True


def _backup_unsafe(path: str) -> str | None:
    """Before ``save`` OVERWRITES an on-disk DB this build could not round-trip in full, copy it to
    a ``<path>.bak`` sidecar so the user's original circuits are never silently lost; returns the
    backup path written, else None. Mirrors ``library._backup_unsafe``, which the session index has
    had since PR #55 — the track DB shipped without it, and one ordinary Save-as-track over a
    half-written file destroyed three circuits' start/finish lines with no copy and no warning.

    Best-effort, and it MUST NOT block the write: a failed copy only logs (a save that keeps the
    app usable beats refusing to save because the backup slot is unwritable). The ``.bak`` is
    overwritten each time, so it mirrors the last replaced-yet-unreadable file rather than
    accumulating — and a healthy file never touches it, so the copy of a bad file survives every
    later save."""
    if not os.path.exists(path) or not _lossy_to_overwrite(path):
        return None
    if not _copy_to_backup(path, f"{os.path.basename(path)} (could not be read in full)"):
        return None
    return backup_path(path)


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


def is_builtin(name: str) -> bool:
    """True when `name` is one of the built-in ``SEED`` circuits — the ones that ship with the app
    and are layered UNDER the user's own file by ``all_tracks``."""
    return any(e["name"] == name for e in SEED)


def user_names(path: str | None = None) -> list[str]:
    """The circuit names the USER's own file holds, in stored order. Deliberately NOT the merged
    view: the seed is not in that file, so this is what a rename or a delete can actually reach."""
    return [e["name"] for e in load(path).get("tracks", [])]


def reverts_to_builtin(name: str, path: str | None = None) -> dict | None:
    """The SEED entry that would come BACK if the user's `name` entry were deleted, or None when a
    delete simply removes the circuit.

    The ask-before question a confirm uses — the same idiom as ``replaces`` and ``backup_pending`` —
    so "the circuit is gone" and "your refinements are gone and the shipped line is back" are not
    reported to the user as the same act."""
    if not is_builtin(name) or name not in user_names(path):
        return None
    return next(_norm_entry(e) for e in SEED if e["name"] == name)


def remove(db: dict, name: str) -> bool:
    """Drop the track called `name` from `db` (mutates the tracks list); True if one was removed.
    The pure half — ``remove_track`` owns the guards, the backup and the write. Mirrors
    ``library.remove``."""
    tracks = db.setdefault("tracks", [])
    for i, e in enumerate(tracks):
        if e.get("name") == name:
            del tracks[i]
            return True
    return False


def remove_track(name: str, path: str | None = None) -> dict:
    """Delete the saved circuit `name`, KEEPING A COPY, and return the new DB.

    A saved circuit is DURABLE HISTORY — a start/finish line the user placed by hand that every
    future recording at that location inherits — so this is the most destructive act this store
    offers, and it takes the precaution its two peer stores take for theirs (``library.clear``,
    ``session_record.remove_and_save``): the file is copied to ``tracks.json.bak`` FIRST, and
    ``restore`` puts it back as a reversible swap.

    REFUSES (``BuiltInTrack``) to "delete" a built-in the user file does not hold — that would drop
    nothing, report success, and the circuit would be back on the next launch.

    WHAT IT DOES NOT TOUCH, DELIBERATELY: the recordings. The library index keys a circuit's
    personal-best history by track NAME, and every analysed session keeps the name it was driven
    under, so deleting a circuit removes future AUTO-DETECTION and nothing else. No library row, no
    focus list, no session record and no file beside the user's footage is read or written here — a
    dangling name is not a dangling pointer, because every one of those stores matches on the string
    it already holds rather than looking the circuit up here. A no-op delete (no such track) writes
    nothing and takes no backup, so browsing cannot churn the one backup slot out from under a copy
    that matters. Raises OSError on an unwritable destination."""
    if path is None:
        path = db_path()
    if is_builtin(name) and name not in user_names(path):
        raise BuiltInTrack(name)
    db = load(path)
    if not remove(db, name):
        return db
    if os.path.exists(path):
        _copy_to_backup(path, "the track database before deleting a circuit")
    save(db, path)
    return db


def rename(db: dict, old: str, new: str) -> bool:
    """Rename the track `old` to `new` in `db`, keeping its POSITION, its lines and its anchor
    (mutates); True if one was renamed. The pure half — ``rename_track`` owns the guards, the backup
    and the write."""
    for e in db.setdefault("tracks", []):
        if e.get("name") == old:
            e["name"] = str(new)
            return True
    return False


def rename_track(old: str, new: str, path: str | None = None) -> dict:
    """Rename the saved circuit `old` to `new`, KEEPING A COPY, and return the new DB. The start
    line, the sector lines and the detection anchor are untouched — only the name moves.

    REFUSES, with a ValueError every caller already guards, rather than writing a wrong answer:

      * a BLANK name — ``_valid_entry`` rejects an empty name, so writing one would make the circuit
        VANISH the next time the file is read;
      * a name ALREADY IN USE (``TrackNameInUse``) — the merged view is name-keyed, so the renamed
        entry would silently swallow the circuit already standing there;
      * a BUILT-IN the user file does not hold (``BuiltInTrack``) — the seed would keep the old name
        while this write added a second circuit at the same anchor;
      * a circuit that is NOT THERE — renaming an absent track would invent one.

    THE NAME IS AN IDENTITY KEY ELSEWHERE, AND THIS FUNCTION DOES NOT OWN THOSE STORES. The library
    index (personal-best history), the per-track focus list and the session record's provenance
    stamp all match a circuit by NAME, so a rename that moved only this file would split a circuit's
    history in two the moment the next recording auto-detected the new name — past sessions under
    the old name, future ones under the new, neither complete. Each of those stores has its own
    ``rename_track``; the app composes the four into one gesture
    (``LibraryController._rename_track``), the same way it composes "forget this recording" across the index, the sidecar, the record and
    the marks."""
    if path is None:
        path = db_path()
    new = (new or "").strip()
    if not new:
        raise ValueError("a track name cannot be blank")
    old = str(old)
    if new == old:
        return load(path)
    if is_builtin(old) and old not in user_names(path):
        raise BuiltInTrack(old)
    if any(e["name"] == new for e in all_tracks(path)):
        raise TrackNameInUse(new)
    db = load(path)
    if not rename(db, old, new):
        raise ValueError(f"no saved track called {old!r}")
    if os.path.exists(path):
        _copy_to_backup(path, "the track database before renaming a circuit")
    save(db, path)
    return db


def backup_summary(path: str | None = None) -> dict | None:
    """What the ``.bak`` sidecar holds, or None when there is nothing worth restoring (no backup, an
    unreadable one, or one with zero circuits)::

        {"path": <the .bak path>, "tracks": <int >= 1>, "mtime": <float POSIX seconds | None>}

    The read half of the backup slot: a caller shows this in its confirm so the user sees BOTH sides
    of a restore before it happens. Kept format-free (a raw mtime, not a date string) so this module
    stays display-agnostic — the same shape ``library.backup_summary`` returns."""
    bak = backup_path(path)
    if not os.path.exists(bak):
        return None
    tracks = load(bak).get("tracks", [])
    if not tracks:
        return None
    try:
        mtime = os.path.getmtime(bak)
    except OSError:
        mtime = None
    return {"path": bak, "tracks": len(tracks), "mtime": mtime}


def restore(path: str | None = None) -> dict:
    """Put the ``.bak`` backup back as the live track DB, and return the result.

    A restore is a SWAP: the DB it replaces becomes the new ``.bak``, so restoring is itself
    reversible and a restore fired at the wrong moment can be taken back exactly the way it was
    made. REFUSES to act — returns the current DB unchanged — when the backup is missing, unreadable
    or holds no circuits: replacing a live DB with nothing would be the very data loss this exists
    to undo. `path` defaults to ``db_path()``; raises OSError on an unwritable destination (the swap
    half is best-effort and only logs). Mirrors ``library.restore`` / ``session_record.restore``."""
    if path is None:
        path = db_path()
    db = load(backup_path(path))
    if not db["tracks"]:
        return load(path)
    swap = path + ".swap"
    kept = False
    if os.path.exists(path):
        try:
            shutil.copy2(path, swap)
            kept = True
        except OSError as exc:
            _log.warning("track_db: could not keep the replaced DB before restoring (%r)", exc)
    save(db, path)
    if kept:
        try:
            os.replace(swap, backup_path(path))
        except OSError as exc:
            _log.warning("track_db: restored the DB but could not swap the backup (%r)", exc)
    return load(path)


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
