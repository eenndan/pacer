"""The session record: what the kart was, and what the day was, for one analyzed recording.

WHY THIS EXISTS, AND WHY IT IS NOT A CHART. Comparing two sessions is only valid if the two
sessions are comparable, and a lap time on its own does not say whether they were. A coach,
answering a driver who could not see 18 months of improvement in his lap times:

    "you're not comparing like for like… I can go to my local track tonight and again a month
    from now and find a difference anything up to 4 seconds purely because of different
    temperature and humidity conditions. […] I don't know about you but I can barely remember
    what I had for breakfast never mind how the weather was 18 months ago."

pacer stored none of it. The library index (``studio/library.py``) holds track, date, lap count
and best lap — the RESULT — and nothing about the conditions that produced it, so every
cross-session comparison the app offers (the PB chart, the progress summary, "0.57 s faster than
your previous best") silently assumed like-for-like. This store is the other half: one record per
recording, holding the handful of things a kart racer actually keeps in a notebook.

NO NETWORK, DELIBERATELY. An archive weather API would fill air temperature and humidity
automatically and it is an explicit non-goal: the app's headline promise is that nothing leaves
the machine (README non-goals, ``Help ▸ Your data & privacy``). Conditions are TYPED BY THE
DRIVER. Nothing in this module, or in the dialog over it, opens a socket.

Schema (version 1) — one JSON object, records keyed by the LIBRARY FINGERPRINT so a record
follows the recording, not the file (a single-chapter and a full chaptered open share one key,
exactly as they share one library entry)::

    {"version": 1,
     "records": {
       "GX0062": {
         # conditions — typed, never fetched
         "conditions":     "dry" | "damp" | "wet" | "mixed" | "",
         "air_temp_c":     <float | null>,
         "track_temp_c":   <float | null>,
         "humidity_pct":   <float | null>,
         # tyres: the identity AND the age, because both move lap times
         "tyre_set":       "<free text, e.g. MG Yellow #3>",
         "tyre_laps":      <int | null>,     # laps on the set BEFORE this session
         "pressure_unit":  "psi" | "bar",
         "cold_front":     <float | null>, "cold_rear": <float | null>,
         "hot_front":      <float | null>, "hot_rear":  <float | null>,
         # the kart
         "chassis":        "<free text>",
         "sprocket_front": <int | null>, "sprocket_rear": <int | null>,
         "axle":           "<free text, e.g. Medium / H / U>",
         "seat":           "<free text, e.g. standard, 5 mm back>",
         "notes":          "<free text, any length>",
         # auto-stamped from what the app already knows (see AUTO_FIELDS)
         "date":       "YYYY-MM-DD" | null,
         "track":      "<track name>" | null,
         "lap_count":  <int | null>,
         "updated":    "YYYY-MM-DDTHH:MM:SS"},
       ...}}

THE AUTO-STAMPED THREE ARE PROVENANCE, NOT THE SOURCE OF TRUTH. ``date`` / ``track`` /
``lap_count`` are copied from the library entry when a record is saved, so a record read on its
own (in the .bak, in a text editor, in a future export) says which session it describes. Every
SURFACE reads those from the library entry instead — that is the authoritative copy, and it is
re-written on every re-open — so the two cannot drift into disagreement on screen.

PERSISTENCE DISCIPLINE — the library/track_db pattern, and NOT the old prefs one. A season of
setup notes is the most irreplaceable thing this app will ever hold: unlike the library index it
cannot be rebuilt by re-opening the footage, because nothing in the footage records what tyres
were on the kart. So:

  * ``VERSION`` is READ, not merely written. An OLDER (or unstamped) file is MIGRATED forward
    (``_migrate``), every record preserved;
  * a NEWER file (a downgrade) is read BEST-EFFORT, and unknown per-record FIELDS SURVIVE THE
    ROUND-TRIP (``_norm_record`` carries them through verbatim). This is the one place this store
    is stricter than ``library.py``, which drops unknown entry fields: a library entry is
    re-derivable from the recording and a hand-typed tyre pressure is not, so a v1 build opening a
    v2 file must not be the reason a v2 field is destroyed;
  * a single malformed record is dropped (count logged) and the rest are kept, so the next write
    cannot lose the whole notebook to one bad row;
  * only genuine FILE-level corruption (unreadable / not JSON / not an object / bad version /
    non-object ``records``) falls back to an empty store — and before any write would overwrite
    bytes that could not be round-tripped, ``save`` copies them to a ``session_records.json.bak``
    sidecar (``_backup_unsafe``);
  * every write is atomic (temp file + ``os.replace``), so a crash mid-write cannot truncate it;
  * the two destructive acts reachable from the UI — forgetting one recording and clearing the
    whole store — take that same ``.bak`` copy FIRST, and ``restore`` puts it back as a reversible
    SWAP (the store it replaces becomes the new backup).

PACER-FREE AND QT-FREE by contract (``tests/test_layering.py``): pure JSON I/O, validation and
display-agnostic text helpers. The dialog over it is ``studio/session_record_dialog.py``.
"""

from __future__ import annotations

import datetime
import json
import logging
import math
import os
import shutil

_log = logging.getLogger(__name__)

VERSION = 1

_FILENAME = "session_records.json"
_APP_DIR_NAME = "pacer"

# The conditions TAG — the one field the Library filters on, so it is a closed vocabulary rather
# than free text ("wet", "Wet", "very wet" and "damp/wet" are four buckets nobody wants). Four
# values is what a kart driver actually distinguishes at the trackside; anything finer belongs in
# `notes`. "" (untagged) is always legal and is what an unfilled record carries.
CONDITIONS = ("dry", "damp", "wet", "mixed")
CONDITION_LABELS = {"dry": "Dry", "damp": "Damp", "wet": "Wet", "mixed": "Mixed"}

# Tyre pressures are written in psi by most of the English-speaking kart world and in bar by most
# of Europe, and a number with no unit beside it is worthless a year later — so the unit is stored
# ON THE RECORD (not app-wide): a driver who switches gauges keeps both seasons readable.
PRESSURE_UNITS = ("psi", "bar")
DEFAULT_PRESSURE_UNIT = "psi"

# The field groups, by normalizer. Order here IS the stored key order (see `_norm_record`), which
# is also the order the form asks for them in — one list, so the dialog cannot drift from the file.
TEXT_FIELDS = ("tyre_set", "chassis", "axle", "seat", "notes")
NUM_FIELDS = ("air_temp_c", "track_temp_c", "humidity_pct",
              "cold_front", "cold_rear", "hot_front", "hot_rear")
INT_FIELDS = ("tyre_laps", "sprocket_front", "sprocket_rear")

# What the app fills in by itself, copied off the library entry at save time. Provenance only —
# see the module docstring — and NEVER something the user types, which is why they are excluded
# from `is_empty`: a record carrying nothing but its own auto-stamp is still an empty record.
AUTO_FIELDS = ("date", "track", "lap_count", "updated")

# What carries forward to the NEXT session's blank form (`prefill`). This is the whole answer to
# "fast to fill in after a session": the kart does not change between Saturday and Sunday, so the
# chassis / axle / seat / gearing / tyre set / pressure unit are already right and the driver only
# corrects what moved. Conditions, temperatures, pressures and notes are deliberately NOT here —
# carrying yesterday's weather forward would put a number in the field that is simply false, and
# this store exists precisely because a false record of the conditions is worse than none.
STICKY_FIELDS = ("tyre_set", "pressure_unit", "chassis", "sprocket_front", "sprocket_rear",
                 "axle", "seat")


def _app_support_dir() -> str:
    """macOS app-support dir for pacer (~/Library/Application Support/pacer). The single seam the
    tests monkeypatch, so the suite never touches the user's real records (mirrors
    ``library._app_support_dir`` / ``prefs._app_support_dir``)."""
    return os.path.join(
        os.path.expanduser("~"), "Library", "Application Support", _APP_DIR_NAME)


def records_path() -> str:
    """Absolute path to the store (``<app-support>/pacer/session_records.json``). Resolves the
    app-support dir through ``_app_support_dir`` so a patched seam is honoured. Does NOT create the
    directory — that happens lazily on the first ``save``."""
    return os.path.join(_app_support_dir(), _FILENAME)


def empty_store() -> dict:
    """A fresh, valid, empty store — the safe default every corruption path returns to."""
    return {"version": VERSION, "records": {}}


def blank_record() -> dict:
    """An all-empty record in canonical shape: every text field "", every number None, the
    conditions tag untagged and the pressure unit at its default. The starting point the form
    binds to, and the thing ``is_empty`` reports True for."""
    rec = {"conditions": "", "pressure_unit": DEFAULT_PRESSURE_UNIT}
    rec.update({k: "" for k in TEXT_FIELDS})
    rec.update({k: None for k in (*NUM_FIELDS, *INT_FIELDS)})
    rec.update({k: None for k in AUTO_FIELDS})
    return _norm_record(rec)


# ------------------------------------------------------------------ value normalizers
def _text(v) -> str:
    """A free-text field: a stripped string, or "" for anything that isn't one. Numbers are NOT
    coerced to text — a tyre set that came back as `12` is a corrupt field, not the set "12"."""
    return v.strip() if isinstance(v, str) else ""


def _num(v) -> float | None:
    """A real-valued field (temperature, pressure, humidity) as a finite float, or None. Accepts a
    JSON int or float; rejects bool (an int subclass), NaN and ±inf — a stored NaN would print as
    a number and sort as a hole."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def _count(v) -> int | None:
    """A counted field (laps on a set, a sprocket's teeth) as a non-negative int, or None. Floats
    are truncated rather than rejected — a form that produced 42.0 means 42 — but bool, NaN and
    negatives are not counts at all."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if v >= 0 else None
    if isinstance(v, float) and math.isfinite(v) and v >= 0:
        return int(v)
    return None


def _tag(v) -> str:
    """The conditions tag, lower-cased and held to ``CONDITIONS``; anything else is untagged. A
    closed vocabulary is what makes the Library's conditions filter a filter rather than a
    guess."""
    v = v.strip().lower() if isinstance(v, str) else ""
    return v if v in CONDITIONS else ""


def _unit(v) -> str:
    """The pressure unit, held to ``PRESSURE_UNITS``; anything else falls back to the default. A
    garbage unit must never make a stored pressure unreadable — it just reads in psi."""
    v = v.strip().lower() if isinstance(v, str) else ""
    return v if v in PRESSURE_UNITS else DEFAULT_PRESSURE_UNIT


def _iso_date(v) -> str | None:
    """An auto-stamped "YYYY-MM-DD" (the library entry's GPS9 date), or None. Not parsed into a
    date object — this module stays display-agnostic and the library's own value is the
    authoritative copy; this is only checked to be a string."""
    return v if isinstance(v, str) and v else None


def _norm_record(rec: dict) -> dict:
    """Canonicalize one record to the stored shape + key order, PRESERVING UNKNOWN KEYS.

    The preservation is the deliberate difference from ``library._norm_entry``, which drops what it
    does not know. A library entry is re-derivable — re-open the recording and every field comes
    back — while a hot rear pressure exists nowhere but in this file. So a v1 build that opens a
    file written by a later schema round-trips that schema's fields untouched instead of quietly
    deleting them on the next save. Unknown keys sort after the known ones so the file stays
    readable; a key that collides with a known field is the known field (this function's shape
    wins, since it is the one every reader validates against)."""
    out: dict = {
        "conditions": _tag(rec.get("conditions")),
        "air_temp_c": _num(rec.get("air_temp_c")),
        "track_temp_c": _num(rec.get("track_temp_c")),
        "humidity_pct": _num(rec.get("humidity_pct")),
        "tyre_set": _text(rec.get("tyre_set")),
        "tyre_laps": _count(rec.get("tyre_laps")),
        "pressure_unit": _unit(rec.get("pressure_unit")),
        "cold_front": _num(rec.get("cold_front")),
        "cold_rear": _num(rec.get("cold_rear")),
        "hot_front": _num(rec.get("hot_front")),
        "hot_rear": _num(rec.get("hot_rear")),
        "chassis": _text(rec.get("chassis")),
        "sprocket_front": _count(rec.get("sprocket_front")),
        "sprocket_rear": _count(rec.get("sprocket_rear")),
        "axle": _text(rec.get("axle")),
        "seat": _text(rec.get("seat")),
        "notes": _text(rec.get("notes")),
        "date": _iso_date(rec.get("date")),
        "track": _text(rec.get("track")) or None,
        "lap_count": _count(rec.get("lap_count")),
        "updated": _text(rec.get("updated")) or None,
    }
    for key in sorted(rec):
        if key not in out and isinstance(key, str):
            out[key] = rec[key]
    return out


def is_empty(rec: dict | None) -> bool:
    """True when the user has filled in NOTHING — every text field blank, every number absent, no
    conditions tag. The auto-stamped fields and the pressure unit do NOT count (a unit is a
    default, not an entry), so a form opened and closed untouched is empty by this test, which is
    what lets ``put`` refuse to store it and ``put_and_save`` DELETE a record the user has just
    emptied. A form nobody completed must leave no trace to mislead a later comparison."""
    if not isinstance(rec, dict):
        return True
    rec = _norm_record(rec)
    if rec["conditions"]:
        return False
    return not any(rec.get(k) for k in TEXT_FIELDS) and not any(
        rec.get(k) is not None for k in (*NUM_FIELDS, *INT_FIELDS))


def filled_fields(rec: dict | None) -> int:
    """How many of the driver-typed fields carry a value — the "is this record worth showing?"
    count the surfaces use for their one-line summaries. The conditions tag counts as one."""
    if not isinstance(rec, dict):
        return 0
    rec = _norm_record(rec)
    n = 1 if rec["conditions"] else 0
    n += sum(1 for k in TEXT_FIELDS if rec.get(k))
    n += sum(1 for k in (*NUM_FIELDS, *INT_FIELDS) if rec.get(k) is not None)
    return n


# ------------------------------------------------------------------ file I/O
def _is_loadable_dict(path: str) -> tuple[bool, dict | None]:
    """(readable_json_object, parsed) for `path`: True/parsed when the file exists and parses to a
    JSON object, else (False, None). The seam ``load`` and ``save`` share so "genuine corruption" —
    the only case that falls back to empty, and the only one that triggers a backup — is decided in
    ONE place (mirrors ``library._is_loadable_dict``)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False, None
    if not isinstance(data, dict):
        return False, None
    return True, data


def _migrate(data: dict, from_version: int) -> dict:
    """Forward-migrate an OLDER on-disk store (`from_version` < ``VERSION``) to the current schema,
    PRESERVING EVERY RECORD. Per-version transforms run in ascending order and each MUST keep every
    record it does not explicitly retire — the rule ``library._migrate`` follows. Returns `data`
    (mutated in place); the caller re-stamps the version and re-validates after this runs.

    There is no transform yet and that is not the same as there being no hook: v1 is the first
    schema, and `from_version` is 0 only for an unstamped file (which a hand-edit is the only way
    to produce), whose records are already v1-shaped — so the identity IS the correct v0→v1
    migration. The point of writing it now is that the FIRST real bump — say pressures growing to
    four corners, or the tyre set gaining a first-used date — adds its ``if from_version < 2:``
    block HERE, beside the load path that calls it, instead of the schema change arriving with
    nowhere to put its transform and a choice between reinterpreting a stale value and wiping a
    season of notes. ``studio/prefs.py`` shipped without this hook and PR #222 is what that cost.
    """
    return data


def load(path: str | None = None) -> dict:
    """Load + validate the store, returning the normalized dict. NEVER wipes records on a version
    mismatch:

      * an OLDER ``version`` is MIGRATED forward (``_migrate``) and re-stamped — records preserved;
      * a NEWER ``version`` (a downgrade) is read BEST-EFFORT, and each record's unknown fields
        survive (``_norm_record``), so a round-trip through this build does not destroy them;
      * a single malformed record is dropped (count logged), the rest kept.

    Only genuine FILE-level corruption (absent / unreadable / not JSON / not an object / missing or
    non-int ``version`` / non-object ``records``) → ``empty_store()``; ``save`` backs those bytes up
    before overwriting them. `path` defaults to ``records_path()``."""
    if path is None:
        path = records_path()
    ok, data = _is_loadable_dict(path)
    if not ok:
        return empty_store()
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        # A missing / non-int version is untrustworthy SHAPE (the records under it have a schema to
        # be wrong about), so — unlike the flat prefs store — it is corruption, not a legacy file.
        return empty_store()
    if version < VERSION:
        _log.warning("session records: migrating store from version %d to %d (%s)",
                     version, VERSION, path)
        data = _migrate(data, version)
    elif version > VERSION:
        _log.warning("session records: store is version %d, newer than this build's %d — reading "
                     "best-effort, unknown fields preserved (%s)", version, VERSION, path)
    raw = data.get("records")
    if not isinstance(raw, dict):
        return empty_store()
    records, dropped = {}, 0
    for key, rec in raw.items():
        if isinstance(key, str) and key and isinstance(rec, dict):
            records[key] = _norm_record(rec)
        else:
            dropped += 1
    if dropped:
        # A later save rewrites only the survivors, healing the file.
        _log.warning("session records: dropped %d malformed record%s of %d from %s",
                     dropped, "" if dropped == 1 else "s", len(raw), path)
    return {"version": VERSION, "records": records}


def backup_path(path: str | None = None) -> str:
    """Absolute path of the store's backup sidecar (``<session_records.json>.bak``) — the ONE slot
    every backup here writes and ``restore`` reads. `path` defaults to ``records_path()``."""
    if path is None:
        path = records_path()
    return path + ".bak"


def _copy_to_backup(path: str, what: str) -> bool:
    """Copy `path` to its ``.bak`` sidecar, best-effort; True on success. Shared by all three
    reasons a backup is taken — an un-round-trippable file about to be overwritten
    (``_backup_unsafe``), a deliberate wipe (``clear``) and forgetting one recording
    (``remove_and_save``) — so they land in the same slot with the same guarantees:
    ``shutil.copy2`` (mtime preserved), one slot overwritten rather than accumulating, and ANY
    failure logged instead of raised (a backup must never be the reason a write the user asked for
    doesn't happen)."""
    try:
        shutil.copy2(path, backup_path(path))
        _log.warning("session records: backed up %s to %s", what,
                     os.path.basename(backup_path(path)))
        return True
    except OSError as exc:
        _log.warning("session records: could not back up %s before overwrite (%r)", path, exc)
        return False


def _backup_unsafe(path: str) -> None:
    """Before ``save`` would OVERWRITE an existing store it could not safely round-trip (genuine
    corruption, or a NEWER un-migratable file), copy it to the ``.bak`` sidecar so the user's
    original bytes are never silently lost. A healthy current/older file that ``load`` migrated is
    rewritten normally (no backup churn); a healthy store being WIPED is ``clear``'s business."""
    if not os.path.exists(path):
        return
    ok, data = _is_loadable_dict(path)
    if not ok:
        _copy_to_backup(path, "an unreadable session-record store")
        return
    # ENUMERATE AGAINST `load`'s OWN FALL-BACKS, not a shorter list. `load` returns `empty_store()`
    # for a non-int/bool `version` and for a non-object `records` — and BOTH of those parse as
    # perfectly good JSON objects, so `_is_loadable_dict` says yes and the old guard said "safe".
    # The file then got overwritten with no backup at all, silently losing a notebook that cannot
    # be rebuilt from the footage. The docstring on `load` already promised this backup; only the
    # predicate was short.
    version = data.get("version")
    bad_version = isinstance(version, bool) or not isinstance(version, int)
    newer = (not bad_version) and version > VERSION
    bad_records = not isinstance(data.get("records"), dict)
    if bad_version or newer or bad_records:
        _copy_to_backup(path, "an unreadable/newer session-record store")


def save(store: dict, path: str | None = None) -> None:
    """Write the store atomically (temp file + ``os.replace``) so a crash mid-write can't leave a
    truncated notebook. Creates the app-support dir if missing. `path` defaults to
    ``records_path()``. Raises OSError on an unwritable destination.

    DATA-SAFETY: before overwriting an existing file that could not be parsed or migrated (genuine
    corruption or a NEWER downgrade-incompatible file), the original is first copied to a
    ``session_records.json.bak`` sidecar (``_backup_unsafe``)."""
    if path is None:
        path = records_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _backup_unsafe(path)
    out = {"version": VERSION,
           "records": {k: _norm_record(v)
                       for k, v in sorted((store or {}).get("records", {}).items())}}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


# ------------------------------------------------------------------ record access
def get(store: dict, fingerprint: str) -> dict | None:
    """The record for `fingerprint`, normalized — or None when there is none. None is the EMPTY
    STATE every surface renders as a sentence rather than a grid of dashes."""
    rec = (store or {}).get("records", {}).get(fingerprint)
    return _norm_record(rec) if isinstance(rec, dict) else None


def put(store: dict, fingerprint: str, rec: dict) -> dict:
    """Store `rec` under `fingerprint` (mutates and returns `store`), stamping ``updated`` now.

    An EMPTY record (``is_empty``) is not stored — it is REMOVED. Saving a form the user just
    cleared has to mean "there is no record here", not "there is a record here and it says
    nothing": an all-dashes row in the Library would claim a session was documented when it was
    not, which is the exact failure this feature exists to prevent."""
    records = store.setdefault("records", {})
    if is_empty(rec):
        records.pop(fingerprint, None)
        return store
    norm = _norm_record(rec)
    norm["updated"] = datetime.datetime.now().replace(microsecond=0).isoformat()
    records[fingerprint] = norm
    return store


def remove(store: dict, fingerprint: str) -> bool:
    """Drop `fingerprint`'s record (mutates the store). True if one was removed."""
    return (store or {}).setdefault("records", {}).pop(fingerprint, None) is not None


def stamp_context(rec: dict, entry: dict | None) -> dict:
    """Copy the auto-stamped provenance (date / track / lap count) off a library `entry` onto
    `rec`, returning a new dict. This is the "auto-stamp what the app already knows" half: the
    driver types the conditions and the setup, the app fills in which session it was. `entry` None
    (no library row — the bundled sample, or a recording that never made it into the index) leaves
    the three fields as they are. See the module docstring for why the SURFACES read these from
    the library entry rather than from here."""
    out = dict(rec)
    if entry:
        out["date"] = entry.get("date")
        out["track"] = entry.get("track")
        out["lap_count"] = entry.get("lap_count")
    return out


def put_and_save(fingerprint: str, rec: dict, path: str | None = None) -> dict:
    """Load, store (or remove, when the record is empty — see ``put``), write back atomically, and
    return the new store. The one call a save button makes. Any OSError from the write propagates
    to the caller, which guards it."""
    store = load(path)
    put(store, fingerprint, rec)
    save(store, path)
    return store


def remove_and_save(fingerprint: str, path: str | None = None) -> dict:
    """Forget one recording's record and write the store back, KEEPING A COPY.

    The privacy "forget this recording" gesture reaches this, and it destroys hand-typed data that
    exists nowhere else — so, exactly like ``clear``, the whole store is copied to its ``.bak``
    sidecar FIRST. A no-op (no such record) writes nothing and takes no backup, so browsing the
    Library and forgetting recordings that were never documented cannot churn the one backup slot
    out from under a record that was."""
    if path is None:
        path = records_path()
    store = load(path)
    if fingerprint not in store.get("records", {}):
        return store
    if os.path.exists(path):
        _copy_to_backup(path, "the session records before forgetting one")
    remove(store, fingerprint)
    save(store, path)
    return store


def clear(path: str | None = None) -> dict:
    """Wipe every session record and write the store back atomically, KEEPING A COPY in the
    ``.bak`` sidecar first (``restore`` puts it back). Returns the fresh empty store. The copy is
    best-effort like every other backup here — a failed copy logs and the wipe still proceeds."""
    if path is None:
        path = records_path()
    if os.path.exists(path):
        _copy_to_backup(path, "the session records before clearing them")
    save(empty_store(), path)
    return load(path)


def backup_summary(path: str | None = None) -> dict | None:
    """What the ``.bak`` sidecar holds, or None when there is nothing worth restoring (no backup,
    an unreadable one, or one with zero records)::

        {"path": <the .bak path>, "records": <int >= 1>, "mtime": <float POSIX seconds | None>}

    Kept format-free (a raw mtime, not a date string) so this module stays display-agnostic."""
    bak = backup_path(path)
    if not os.path.exists(bak):
        return None
    records = load(bak).get("records", {})
    if not records:
        return None
    try:
        mtime = os.path.getmtime(bak)
    except OSError:
        mtime = None
    return {"path": bak, "records": len(records), "mtime": mtime}


def restore(path: str | None = None) -> dict:
    """Put the ``.bak`` backup back as the live store, and return the result.

    A restore is a SWAP: the store it replaces becomes the new ``.bak``, so restoring is itself
    reversible. REFUSES to act — returns the current store unchanged — when the backup is missing,
    unreadable or holds no records: replacing a live notebook with nothing would be the very data
    loss this exists to undo. `path` defaults to ``records_path()``; raises OSError on an
    unwritable destination (the swap half is best-effort and only logs)."""
    if path is None:
        path = records_path()
    store = load(backup_path(path))
    if not store["records"]:
        return load(path)
    swap = path + ".swap"
    kept = False
    if os.path.exists(path):
        try:
            shutil.copy2(path, swap)
            kept = True
        except OSError as exc:
            _log.warning("session records: could not keep the replaced store before restoring "
                         "(%r)", exc)
    save(store, path)
    if kept:
        try:
            os.replace(swap, backup_path(path))
        except OSError as exc:
            _log.warning("session records: restored the store but could not swap the backup (%r)",
                         exc)
    return load(path)


# ------------------------------------------------------------------ the fast-to-fill half
def _recency_key(rec: dict) -> tuple:
    """Sort key putting the most recently WRITTEN record last. ``updated`` (a wall-clock stamp) is
    the primary, since it is when the driver actually typed it; the session ``date`` breaks ties
    for records written in one sitting. Both are ISO strings, so lexical order is chronological."""
    return (rec.get("updated") or "", rec.get("date") or "")


def latest(store: dict, exclude: str | None = None) -> dict | None:
    """The most recently written record in `store`, ignoring `exclude`'s own — or None when there
    is none. The source ``prefill`` copies from."""
    others = [r for k, r in (store or {}).get("records", {}).items() if k != exclude]
    return max(others, key=_recency_key) if others else None


def prefill(store: dict, exclude: str | None = None, entry: dict | None = None) -> dict:
    """A blank record PRE-POPULATED from the driver's last session — the whole answer to "fast to
    fill in after a session", and the test this feature actually has to pass. A form that asks a
    tired driver to re-type his chassis, axle, seat position and gearing for the fourteenth time
    is a form he stops filling in, and an intermittently-filled record is worse than none: it makes
    the Library's "were these two sessions comparable?" question unanswerable in a NEW way.

    So ``STICKY_FIELDS`` carry forward (the kart did not change overnight) and nothing else does.
    The conditions, the temperatures, the pressures and the notes stay blank on purpose: yesterday's
    weather in today's record would be a fabricated observation, which is precisely what this store
    is here to replace.

    TYRE LAPS ADVANCE THEMSELVES. If the last session ran the SAME tyre set and recorded both a
    lap count and the laps that set had already done, the new record opens at their sum — the one
    number that is genuinely derivable, and the one a driver is least likely to keep accurately by
    hand. A different set (or a missing half) leaves it blank rather than guessing.

    `entry` is the library entry for the session being documented; when given, its date / track /
    lap count are stamped in (``stamp_context``) so an untouched form still knows what it is
    about."""
    rec = blank_record()
    prior = latest(store, exclude)
    if prior:
        for key in STICKY_FIELDS:
            rec[key] = prior.get(key)
        rec["pressure_unit"] = _unit(rec.get("pressure_unit"))
        if (prior.get("tyre_set") and prior.get("tyre_laps") is not None
                and prior.get("lap_count") is not None):
            rec["tyre_laps"] = int(prior["tyre_laps"]) + int(prior["lap_count"])
    return _norm_record(stamp_context(rec, entry))


# ------------------------------------------------------------------ display-agnostic text
def _fmt_num(v: float | None) -> str:
    """A stored number as the shortest honest string: "24" not "24.0", "1.05" kept. Used by every
    summary below so one record never prints two number formats."""
    if v is None:
        return ""
    return str(int(v)) if float(v).is_integer() else f"{float(v):g}"


def conditions_text(rec: dict | None) -> str:
    """The conditions clause: ``"Dry · air 24° · track 38° · 41% RH"``, or "" when the driver
    recorded no conditions at all. Degrees are bare (no C): the field asks for Celsius and a unit
    on every one of three numbers is noise in a one-line summary — the form is where the unit is
    stated."""
    if not isinstance(rec, dict):
        return ""
    rec = _norm_record(rec)
    parts = []
    if rec["conditions"]:
        parts.append(CONDITION_LABELS[rec["conditions"]])
    if rec["air_temp_c"] is not None:
        parts.append(f"air {_fmt_num(rec['air_temp_c'])}°")
    if rec["track_temp_c"] is not None:
        parts.append(f"track {_fmt_num(rec['track_temp_c'])}°")
    if rec["humidity_pct"] is not None:
        parts.append(f"{_fmt_num(rec['humidity_pct'])}% RH")
    return "  ·  ".join(parts)


def tyre_text(rec: dict | None) -> str:
    """The tyre clause: ``"MG Yellow #3, 42 laps"`` / ``"42 laps"`` / ``"MG Yellow #3"``, or "".
    The laps are the AGE — the thing the coach's answer turns on, and the one the Library sorts
    and filters on."""
    if not isinstance(rec, dict):
        return ""
    rec = _norm_record(rec)
    name, laps = rec["tyre_set"], rec["tyre_laps"]
    if name and laps is not None:
        return f"{name}, {laps} lap{'' if laps == 1 else 's'}"
    if laps is not None:
        return f"{laps} lap{'' if laps == 1 else 's'}"
    return name


def pressure_text(rec: dict | None) -> str:
    """The pressures clause: ``"cold 10/10.5, hot 13/13.5 psi"`` — front/rear per state, the unit
    said once at the end. Either half may be absent; "" when both are."""
    if not isinstance(rec, dict):
        return ""
    rec = _norm_record(rec)
    parts = []
    for label, front, rear in (("cold", "cold_front", "cold_rear"),
                               ("hot", "hot_front", "hot_rear")):
        pair = [_fmt_num(rec[k]) for k in (front, rear) if rec[k] is not None]
        if pair:
            parts.append(f"{label} {'/'.join(pair)}")
    return f"{', '.join(parts)} {rec['pressure_unit']}" if parts else ""


def kart_text(rec: dict | None) -> str:
    """The kart clause: ``"OTK 401R  ·  11/82  ·  axle H  ·  seat 5 mm back"``, or "". The
    sprockets print as the pair a kart racer says out loud; one alone prints alone."""
    if not isinstance(rec, dict):
        return ""
    rec = _norm_record(rec)
    parts = []
    if rec["chassis"]:
        parts.append(rec["chassis"])
    front, rear = rec["sprocket_front"], rec["sprocket_rear"]
    if front is not None and rear is not None:
        parts.append(f"{front}/{rear}")
    elif front is not None or rear is not None:
        parts.append(f"{front if front is not None else rear}T")
    if rec["axle"]:
        parts.append(f"axle {rec['axle']}")
    if rec["seat"]:
        parts.append(f"seat {rec['seat']}")
    return "  ·  ".join(parts)


def summary_line(rec: dict | None) -> str:
    """The one-line read of a whole record — conditions, then tyres, then pressures, then the kart
    — for a table cell's hover and the Library's selected-row line. "" when there is no record or
    it says nothing, which is the signal every caller uses to show its empty state instead."""
    clauses = [c for c in (conditions_text(rec), tyre_text(rec), pressure_text(rec),
                           kart_text(rec)) if c]
    return "  ·  ".join(clauses)


def comparable(a: dict | None, b: dict | None) -> list[str]:
    """What DIFFERS between two records, as short human clauses — the answer to "was I comparing
    like for like?", which is the only reason this store exists.

    Returns [] when the two are alike on everything recorded (or when there is nothing recorded to
    compare, which is NOT the same thing and is why callers check ``summary_line`` first). A field
    absent from either record is not a difference — an unrecorded value is unknown, and reporting
    "tyres differ" because one session forgot to write them down would manufacture the doubt this
    is supposed to resolve.

    The temperature thresholds are the ones the coach's own answer names: he puts up to 4 seconds
    on "different temperature and humidity conditions", so a difference worth flagging is one big
    enough to move a lap time rather than one big enough to measure. 3 °C of air, 5 °C of track and
    10 points of humidity are deliberately coarse."""
    a, b = (_norm_record(a) if isinstance(a, dict) else None,
            _norm_record(b) if isinstance(b, dict) else None)
    if not a or not b:
        return []
    out = []
    if a["conditions"] and b["conditions"] and a["conditions"] != b["conditions"]:
        out.append(f"{CONDITION_LABELS[a['conditions']]} vs {CONDITION_LABELS[b['conditions']]}")
    for key, label, tol in (("air_temp_c", "air", 3.0), ("track_temp_c", "track", 5.0),
                            ("humidity_pct", "humidity", 10.0)):
        x, y = a[key], b[key]
        if x is not None and y is not None and abs(x - y) >= tol:
            unit = "%" if key == "humidity_pct" else "°"
            out.append(f"{label} {_fmt_num(x)}{unit} vs {_fmt_num(y)}{unit}")
    if a["tyre_set"] and b["tyre_set"] and a["tyre_set"] != b["tyre_set"]:
        out.append(f"tyres {a['tyre_set']} vs {b['tyre_set']}")
    elif a["tyre_laps"] is not None and b["tyre_laps"] is not None \
            and abs(a["tyre_laps"] - b["tyre_laps"]) >= 20:
        out.append(f"tyre age {a['tyre_laps']} vs {b['tyre_laps']} laps")
    gear_a = (a["sprocket_front"], a["sprocket_rear"])
    gear_b = (b["sprocket_front"], b["sprocket_rear"])
    if None not in gear_a and None not in gear_b and gear_a != gear_b:
        out.append(f"gearing {gear_a[0]}/{gear_a[1]} vs {gear_b[0]}/{gear_b[1]}")
    for key, label in (("chassis", "chassis"), ("axle", "axle"), ("seat", "seat")):
        if a[key] and b[key] and a[key] != b[key]:
            out.append(f"{label} {a[key]} vs {b[key]}")
    return out
