"""Marks: what the DRIVER concluded, against what the app measured.

WHY THIS EXISTS. Every other surface in this app measures. The map, the charts, the lap grid, the
corner model, the coaching page, the quality strip — all of them are artifacts of measurement, and
not one of them can hold the sentence a driver says out loud while watching his own footage: *"I
was baulked there"*, *"that kerb is why the rear stepped out"*, *"that one was the lap"*. A lap
time says the lap was 68.31 s. Nothing in pacer could say WHY, in the driver's own words, at the
second it happened. A mark is that: a moment or a range on the recording, with a type, a colour and
a note.

The reference implementation is Foxglove's Events — a bookmark dropped at the playhead, drawn above
the playback bar, carrying TYPED properties rather than free text alone, and searchable. The types
here are the six things a kart driver actually distinguishes (see `MANUAL_TYPES`); the note is
where everything finer goes.

TWO KINDS, AND ONLY ONE OF THEM IS EVER WRITTEN TO DISK.

  * MANUAL marks are hand-authored and are the whole content of the store. They cannot be
    re-derived from the footage — nothing in a GPS stream records that the kart in front braked
    early — so they get the session-record's persistence discipline, not the old prefs one.
  * AUTO marks are DERIVED, every load, from the detectors the app already ships, and are never
    persisted. That is not a simplification, it is the honesty rule made structural: an auto-mark
    asserts "the app detected something", and a STORED auto-mark could outlive, contradict or
    silently disagree with the detector that produced it. Two surfaces disagreeing about whether a
    lap had a dropout is the class of defect this codebase has fixed repeatedly. Here it cannot
    arise, because `auto_marks` reads the same `gapfill.find_gaps`, the same
    `Session.excluded_lap_ids` and the same `QualityTimeline` the lap table, the excluded strip and
    the quality strip read, and there is no second copy to drift.

WHICH CLOCK A MARK ANCHORS TO — and it is NOT a bare media time.

A mark is authored at the PLAYHEAD, which is a global media-clock second: the whole of this app's
time axis is the media clock (`QualityTimeline` is "on the media clock the scrubber runs on",
`lap_window` returns media-clock seconds, the scrub slider is global ms). Telemetry time is not a
separate axis in this build — there is one clock, and the playhead is in it. So the AUTHORING
clock is settled.

The STORAGE clock is not the same question, and storing the global media second would have been
wrong twice over:

  1. The store is keyed by the LIBRARY FINGERPRINT, which strips the chapter index
     (`library.fingerprint`: "GX010062" -> "GX0062") so that a single-chapter open and a full
     chaptered open of one recording share one key — exactly as they share one library row and one
     session record. But global t=0 is wherever the OPEN began: the same instant of GX020062 is
     global 5.0 s when that chapter is opened alone and global 1735.0 s when the trio is chained.
     One key, two meanings, and a note typed on Sunday would land three-quarters of an hour away on
     Monday.
  2. The global offsets are cumulative VIDEO DURATIONS. This wave has already moved them once (the
     chapter-seam fix). A stored global time silently relocates under any build that re-measures a
     duration; nothing would say so, and a hand-typed note cannot be recovered from the footage.

So a mark stores a CHAPTER-RELATIVE anchor: the chapter's own file STEM plus seconds into that
chapter's own media (`anchor_from_global` / `global_from_anchor`, both pure arithmetic over
`chapters.ChapterMap`). That anchor means the same instant in every open, survives a re-measured
duration, and is legible in a text editor a year later. A mark whose chapter is simply not part of
this open resolves to `placed=False` and is LISTED but never drawn — it is not relocated, not
clamped and not dropped.

Schema (version 1) — one JSON object, recordings keyed by the library fingerprint::

    {"version": 1,
     "recordings": {
       "GX0062": {"marks": [
         {"id":      "9f2c1ab07e41",            # unique within the recording
          "kind":    "manual",                  # every mark in this file is hand-authored
          "chapter": "GX010062",                # the chapter STEM the anchor is relative to
          "t":       842.31,                    # seconds into THAT chapter's own media
          "t_end":   null,                      # a moment; a float makes it a range
          "type":    "traffic",                 # MANUAL_TYPES
          "colour":  "coral",                   # COLOURS — a NAME, never a hex
          "note":    "baulked out of 4",
          "created": "2026-09-11T14:03:22"},
         ...]}}}

The colour is stored as a NAME and resolved by `theme.mark_colour` at paint. A frozen hex would
opt every mark out of the colour-blind palette the rest of the app switches with one menu item.

PERSISTENCE DISCIPLINE — `studio/session_record.py`'s, item for item, because the same argument
applies with more force: a tyre pressure at least existed on a gauge once, while "that was the one"
existed only in the driver's head.

  * `VERSION` is READ, not merely written — an older/unstamped file is MIGRATED forward
    (`_migrate`) with every mark preserved;
  * a NEWER file is read BEST-EFFORT and unknown per-mark FIELDS SURVIVE THE ROUND-TRIP
    (`_norm_mark` carries them through verbatim), so a v1 build opening a v2 file is never the
    reason a v2 field is destroyed;
  * a single malformed mark is dropped (count logged) and the rest are kept, so one bad row cannot
    cost a season of notes;
  * only genuine FILE-level corruption falls back to an empty store — and before any write would
    overwrite bytes this build could not round-trip, `save` copies them to a `marks.json.bak`
    sidecar (`_backup_unsafe`);
  * every write is atomic (temp file + `os.replace`);
  * the destructive acts reachable from the UI — deleting a recording's marks, forgetting a
    recording, clearing the store — take that `.bak` copy FIRST, and `restore` puts it back as a
    reversible SWAP.

PACER-FREE AND QT-FREE by contract (`tests/test_layering.py`): JSON I/O, validation, the anchor
arithmetic and the auto-mark derivation. The band that draws them is `video_view._MarksBand` and
the list is `studio/marks_panel.py`.
"""

from __future__ import annotations

import datetime
import json
import logging
import math
import os
import shutil
import uuid

from . import data_quality

_log = logging.getLogger(__name__)

VERSION = 1

_FILENAME = "marks.json"
_APP_DIR_NAME = "pacer"

# ---------------------------------------------------------------- the vocabulary
#: The two kinds. `KIND_AUTO` never reaches the file — see the module docstring.
KIND_MANUAL = "manual"
KIND_AUTO = "auto"

# The MANUAL types. Six, because six is what a kart driver actually distinguishes while watching
# his own footage, and because a seventh would be a synonym of one of these. Anything finer belongs
# in the note, which is free text and has no length limit. `note` is the catch-all and the default:
# a driver who has just pressed the key wants to type, not to classify, so the type he does not
# choose is the one that claims nothing.
TYPE_NOTE = "note"
TYPE_GOOD = "good"
TYPE_MISTAKE = "mistake"
TYPE_TRAFFIC = "traffic"
TYPE_TRACK = "track"
TYPE_KART = "kart"
MANUAL_TYPES = (TYPE_NOTE, TYPE_GOOD, TYPE_MISTAKE, TYPE_TRAFFIC, TYPE_TRACK, TYPE_KART)

# The AUTO types — one per detector the app already ships, and each one names the surface it agrees
# with. They are types rather than a single "auto" type because the list filters on type and
# "show me the dropouts" is a different question from "show me the laps that were thrown out".
TYPE_DROPOUT = "dropout"
TYPE_EXCLUDED = "excluded"
TYPE_DEGRADED = "degraded"
AUTO_TYPES = (TYPE_DROPOUT, TYPE_EXCLUDED, TYPE_DEGRADED)

TYPES = (*MANUAL_TYPES, *AUTO_TYPES)

TYPE_LABEL = {
    TYPE_NOTE: "Note",
    TYPE_GOOD: "Good",
    TYPE_MISTAKE: "Mistake",
    TYPE_TRAFFIC: "Traffic",
    TYPE_TRACK: "Track",
    TYPE_KART: "Kart",
    TYPE_DROPOUT: "GPS dropout",
    TYPE_EXCLUDED: "Excluded lap",
    TYPE_DEGRADED: "GPS degraded",
}

#: The colour vocabulary a mark may be stored with — NAMES, resolved by `theme.mark_colour` so a
#: mark follows the colour-blind palette like every other coloured surface. Five identity names
#: (the app's categorical `theme.CHART_SERIES` slots, which already carry the measured deuteranopia
#: separation work), one NEUTRAL, and two SEMANTIC hues reserved for the derived marks so a mark
#: that reports a detector wears the colour the surfaces reporting it already wear.
#:
#: THE APP ACCENT (amber) IS DELIBERATELY ABSENT, and the omission was found by measurement rather
#: than chosen: the first cut of this vocabulary offered `amber` (CHART_SERIES slot 0) beside
#: `warn`, and `theme.mark_colour` resolved both to #F5A623 in the standard palette — one colour
#: wearing two labels, so a plain note and a degraded-GPS mark painted the same pixel on the same
#: band. `warn` keeps the hue because it has to agree with the quality strip a sub-step below; the
#: identity slot goes, and with it the second problem it had, which is that the scrub bar 10 px
#: under this one already paints the CURRENT LAP's bracket in that exact accent.
COLOURS = ("grey", "cyan", "purple", "blue", "coral", "lime", "warn", "bad")
DEFAULT_COLOUR = "grey"

#: The colour each type opens with. A driver may change it (the mark stores its own), but the
#: default has to mean something on its own: `note` — the type you get for NOT choosing one — takes
#: the neutral, because a catch-all that claims nothing should not arrive wearing a hue that does;
#: good is the lime, a mistake and traffic the two warm identity hues, track and kart the two cool
#: ones; and the three derived types take the semantic pair rather than an identity slot.
TYPE_COLOUR = {
    TYPE_NOTE: "grey",
    TYPE_GOOD: "lime",
    TYPE_MISTAKE: "coral",
    TYPE_TRAFFIC: "purple",
    TYPE_TRACK: "cyan",
    TYPE_KART: "blue",
    TYPE_DROPOUT: "bad",
    TYPE_EXCLUDED: "bad",
    TYPE_DEGRADED: "warn",
}

# A degraded stretch becomes a MARK only at this length, and the number is measured rather than
# chosen. Counted over every way the owner's two recordings can be opened (both full chains, all
# five single chapters, and the bundled sample), the maximal runs of not-GOOD quality cells are:
#
#     0060 full  23 runs — lengths 1,1,1,1,1,1,2,2,2,2,2,2,2,2,2,3,3,3,4,4 …  max 4 s
#     0062 full   6 runs — 49 s (POOR, the receiver acquiring its lock), then 1,1,2,2 s
#     0062 ch3    1 run  — 1 s, and it is the LAST cell: the video track outruns the GPMF track
#
# There is not one run between 5 s and 49 s anywhere in eight opens: the population is a handful of
# one-and-two-second DOP flecks plus, on one recording, a real block. The flecks are already drawn
# — at the same x, in the same row, by the quality strip a sub-step under the groove — so a mark on
# top of one asserts nothing the strip does not already assert, and twenty rows reading "GPS
# degraded · 2 s" would bury the four notes a driver actually wrote. 5 s is the bottom of the empty
# band between the two populations, i.e. the least aggressive cut that separates them, and it is
# also the shortest stretch there is anything to LOOK at when the jump key lands you on it.
#
# Suppression is never silent: `auto_marks` returns the count it held back and the marks panel says
# so in one line, pointing at the strip that does draw them. A surface that quietly showed fewer
# degraded stretches than the strip would be the very disagreement this feature must not create.
MIN_DEGRADED_S = 5.0


# ---------------------------------------------------------------- app-support paths
def _app_support_dir() -> str:
    """macOS app-support dir for pacer (~/Library/Application Support/pacer). The single seam the
    tests monkeypatch, so the suite never touches the user's real marks (mirrors
    ``session_record._app_support_dir`` / ``library._app_support_dir``)."""
    return os.path.join(
        os.path.expanduser("~"), "Library", "Application Support", _APP_DIR_NAME)


def marks_path() -> str:
    """Absolute path to the store (``<app-support>/pacer/marks.json``). Resolves the app-support
    dir through ``_app_support_dir`` so a patched seam is honoured. Does NOT create the directory —
    that happens lazily on the first ``save``."""
    return os.path.join(_app_support_dir(), _FILENAME)


def empty_store() -> dict:
    """A fresh, valid, empty store — the safe default every corruption path returns to."""
    return {"version": VERSION, "recordings": {}}


# ---------------------------------------------------------------- value normalizers
def _text(v) -> str:
    """A free-text field: a stripped string, or "" for anything that isn't one. Numbers are NOT
    coerced — a note that came back as `12` is a corrupt field, not the note "12"."""
    return v.strip() if isinstance(v, str) else ""


def _time(v) -> float | None:
    """A stored time as a finite, non-negative float, or None. Rejects bool (an int subclass), NaN,
    ±inf and negatives: a chapter-relative second before the chapter started is not a time, and a
    NaN would sort as a hole and paint at x=0."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) and v >= 0.0 else None


def _type(v) -> str:
    """The mark type, held to `MANUAL_TYPES`; anything else is a plain note. The vocabulary is
    CLOSED for the same reason the session record's conditions tag is — it is what the list filters
    on, and "traffic", "Traffic" and "traffic?" are three buckets nobody wants.

    Only the MANUAL types are admitted here, even though `TYPES` is wider: an auto type in the file
    would claim a detector produced a mark that no detector has seen (see `_kind`)."""
    v = v.strip().lower() if isinstance(v, str) else ""
    return v if v in MANUAL_TYPES else TYPE_NOTE


def _colour(v) -> str:
    """The colour name, held to `COLOURS`; anything else falls back to the default. A garbage
    colour must never make a stored note unreadable — it just paints amber."""
    v = v.strip().lower() if isinstance(v, str) else ""
    return v if v in COLOURS else DEFAULT_COLOUR


def _kind(_v) -> str:
    """Always `KIND_MANUAL`, and the argument is ignored ON PURPOSE.

    The file holds hand-authored marks only. Honouring a `kind` read off disk would let a
    hand-edited (or a future-schema) file present a mark as DERIVED — drawn in the warning hue,
    listed as "GPS dropout", asserting that a detector found something — when no detector has run.
    That is precisely the two-surfaces-disagreeing defect the auto/manual split exists to make
    impossible, so the field is written for a reader's benefit and forced on the way back in. A
    schema that genuinely wants to persist a derived mark bumps `VERSION` and relaxes this."""
    return KIND_MANUAL


def _norm_mark(m: dict) -> dict:
    """Canonicalize one mark to the stored shape + key order, PRESERVING UNKNOWN KEYS.

    The preservation is deliberate and is `session_record._norm_record`'s rule, for the same
    reason: a library entry is re-derivable by re-opening the recording and a hand-typed note is
    not, so a v1 build that opens a file written by a later schema round-trips that schema's fields
    untouched instead of quietly deleting them on the next save. Unknown keys sort after the known
    ones so the file stays readable; a key colliding with a known field is the known field.

    `t_end` is dropped to None unless it is strictly AFTER `t` — a range that ends where it starts
    is a moment, and one that ends before it started is corrupt. Both are drawn as moments rather
    than refused, because the note is the irreplaceable half."""
    t = _time(m.get("t"))
    t_end = _time(m.get("t_end"))
    if t is None or t_end is None or t_end <= t:
        t_end = None
    out: dict = {
        "id": _text(m.get("id")),
        "kind": _kind(m.get("kind")),
        "chapter": _text(m.get("chapter")),
        "t": t,
        "t_end": t_end,
        "type": _type(m.get("type")),
        "colour": _colour(m.get("colour")),
        "note": _text(m.get("note")),
        "created": _text(m.get("created")) or None,
    }
    for key in sorted(m):
        if key not in out and isinstance(key, str):
            out[key] = m[key]
    return out


def _valid_mark(m) -> bool:
    """True iff `m` is a structurally usable mark: a dict with an id, a chapter and a real time.
    A mark missing any of the three cannot be placed, listed or edited — it is not a mark whose
    fields are wrong, it is not a mark — so `load` drops it and keeps the rest."""
    return (isinstance(m, dict) and bool(_text(m.get("id")))
            and bool(_text(m.get("chapter"))) and _time(m.get("t")) is not None)


def new_mark(chapter: str, t: float, *, t_end: float | None = None, type: str = TYPE_NOTE,
             colour: str | None = None, note: str = "") -> dict:
    """A fresh mark in canonical shape, with a new id and a `created` stamp.

    `chapter` / `t` are the ANCHOR (see `anchor_from_global`), never a global media time. The
    colour defaults to the type's own, so the common path — press the key, type a word, hit Enter —
    produces a mark that already means something on the bar."""
    return _norm_mark({
        "id": uuid.uuid4().hex[:12],
        "kind": KIND_MANUAL,
        "chapter": chapter,
        "t": t,
        "t_end": t_end,
        "type": type,
        "colour": colour or TYPE_COLOUR.get(type, DEFAULT_COLOUR),
        "note": note,
        "created": datetime.datetime.now().replace(microsecond=0).isoformat(),
    })


# ---------------------------------------------------------------- file I/O
def _is_loadable_dict(path: str) -> tuple[bool, dict | None]:
    """(readable_json_object, parsed) for `path`. The seam `load` and `save` share, so "genuine
    corruption" — the only case that falls back to empty, and the only one that triggers a backup —
    is decided in ONE place (mirrors ``session_record._is_loadable_dict``)."""
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
    PRESERVING EVERY MARK. Per-version transforms run in ascending order and each MUST keep every
    mark it does not explicitly retire.

    There is no transform yet and that is not the same as there being no hook: v1 is the first
    schema, and `from_version` is 0 only for an unstamped file (a hand-edit is the only way to
    produce one), whose marks are already v1-shaped — so the identity IS the correct v0->v1
    migration. The point of writing it now is that the FIRST real bump adds its
    ``if from_version < 2:`` block HERE, beside the load path that calls it, instead of the schema
    change arriving with nowhere to put its transform and a choice between reinterpreting a stale
    value and wiping a season of notes. ``studio/prefs.py`` shipped without this hook and PR #222
    is what that cost."""
    return data


def load(path: str | None = None) -> dict:
    """Load + validate the store, returning the normalized dict. NEVER wipes marks on a version
    mismatch:

      * an OLDER ``version`` is MIGRATED forward (``_migrate``) and re-stamped — marks preserved;
      * a NEWER ``version`` (a downgrade) is read BEST-EFFORT, and each mark's unknown fields
        survive (``_norm_mark``), so a round-trip through this build does not destroy them;
      * a single malformed mark is dropped (count logged), the rest kept.

    Only genuine FILE-level corruption (absent / unreadable / not JSON / not an object / missing or
    non-int ``version`` / non-object ``recordings``) -> ``empty_store()``; ``save`` backs those
    bytes up before overwriting them. `path` defaults to ``marks_path()``."""
    if path is None:
        path = marks_path()
    ok, data = _is_loadable_dict(path)
    if not ok:
        return empty_store()
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        # A missing / non-int version is untrustworthy SHAPE (the marks under it have a schema to
        # be wrong about), so — unlike the flat prefs store — it is corruption, not a legacy file.
        return empty_store()
    if version < VERSION:
        _log.warning("marks: migrating store from version %d to %d (%s)", version, VERSION, path)
        data = _migrate(data, version)
    elif version > VERSION:
        _log.warning("marks: store is version %d, newer than this build's %d — reading "
                     "best-effort, unknown fields preserved (%s)", version, VERSION, path)
    raw = data.get("recordings")
    if not isinstance(raw, dict):
        return empty_store()
    recordings, dropped = {}, 0
    for key, rec in raw.items():
        if not (isinstance(key, str) and key and isinstance(rec, dict)):
            dropped += 1
            continue
        raw_marks = rec.get("marks")
        kept = []
        if isinstance(raw_marks, list):
            for m in raw_marks:
                if _valid_mark(m):
                    kept.append(_norm_mark(m))
                else:
                    dropped += 1
        elif raw_marks is not None:
            dropped += 1
        entry = {k: v for k, v in rec.items() if k != "marks"}   # a later schema's own fields
        entry["marks"] = kept
        recordings[key] = entry
    if dropped:
        # A later save rewrites only the survivors, healing the file.
        _log.warning("marks: dropped %d malformed record%s from %s",
                     dropped, "" if dropped == 1 else "s", path)
    return {"version": VERSION, "recordings": recordings}


def backup_path(path: str | None = None) -> str:
    """Absolute path of the store's backup sidecar (``<marks.json>.bak``) — the ONE slot every
    backup here writes and ``restore`` reads. `path` defaults to ``marks_path()``."""
    if path is None:
        path = marks_path()
    return path + ".bak"


def _copy_to_backup(path: str, what: str) -> bool:
    """Copy `path` to its ``.bak`` sidecar, best-effort; True on success. Shared by every reason a
    backup is taken — an un-round-trippable file about to be overwritten (``_backup_unsafe``), a
    deliberate wipe (``clear``) and forgetting one recording (``remove_and_save``) — so they land
    in the same slot with the same guarantees: ``shutil.copy2`` (mtime preserved), one slot
    overwritten rather than accumulating, and ANY failure logged instead of raised (a backup must
    never be the reason a write the user asked for doesn't happen)."""
    try:
        shutil.copy2(path, backup_path(path))
        _log.warning("marks: backed up %s to %s", what, os.path.basename(backup_path(path)))
        return True
    except OSError as exc:
        _log.warning("marks: could not back up %s before overwrite (%r)", path, exc)
        return False


def _backup_unsafe(path: str) -> None:
    """Before ``save`` would OVERWRITE an existing store this build could not round-trip, copy it to
    the ``.bak`` sidecar so the user's original bytes are never silently lost. A healthy
    current/older file that ``load`` migrated is rewritten normally (no backup churn); a healthy
    store being WIPED is ``clear``'s business.

    THE CONDITION IS "``load`` WOULD HAVE THROWN THIS FILE AWAY", enumerated against ``load``'s own
    fall-backs, and it is deliberately wider than the obvious "unreadable or newer" pair. Written
    that narrow way — the shape ``session_record._backup_unsafe`` still has — a file whose
    ``version`` is a STRING, or whose ``recordings`` is a list, parses as a JSON object, fails
    ``load``'s validation, comes back as an EMPTY store, and is then overwritten by the very next
    write with no copy taken: every mark in it gone, and the one mechanism that exists to prevent
    exactly that standing down because the bytes happened to be valid JSON. Measured while writing
    ``tests/test_marks.py``, which asserts the ``.bak`` for all five corrupt payloads."""
    if not os.path.exists(path):
        return
    ok, data = _is_loadable_dict(path)
    if not ok:
        _copy_to_backup(path, "an unreadable marks store")
        return
    version = data.get("version")
    usable_version = isinstance(version, int) and not isinstance(version, bool)
    if (not usable_version) or version > VERSION or not isinstance(data.get("recordings"), dict):
        _copy_to_backup(path, "a marks store this build could not read back")


def save(store: dict, path: str | None = None) -> None:
    """Write the store atomically (temp file + ``os.replace``) so a crash mid-write can't leave a
    truncated notebook. Creates the app-support dir if missing. `path` defaults to ``marks_path()``.
    Raises OSError on an unwritable destination.

    A recording whose mark list has gone EMPTY is dropped from the file rather than written as an
    empty shell — an empty list is the same fact as no entry, and one of the two would then have to
    be explained to every reader.

    DATA-SAFETY: before overwriting an existing file that could not be parsed or migrated (genuine
    corruption or a NEWER downgrade-incompatible file), the original is first copied to a
    ``marks.json.bak`` sidecar (``_backup_unsafe``)."""
    if path is None:
        path = marks_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _backup_unsafe(path)
    out_recordings = {}
    for key, rec in sorted((store or {}).get("recordings", {}).items()):
        marks = [_norm_mark(m) for m in rec.get("marks", []) if isinstance(m, dict)]
        if not marks:
            continue
        entry = {k: v for k, v in rec.items() if k != "marks"}
        entry["marks"] = sorted(marks, key=lambda m: (m["chapter"], m["t"], m["id"]))
        out_recordings[key] = entry
    out = {"version": VERSION, "recordings": out_recordings}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- record access
def get(store: dict, fingerprint: str) -> list[dict]:
    """This recording's stored (hand-authored) marks, normalized and ordered — [] when it has
    none. The EMPTY STATE every surface renders as a sentence rather than as a blank grid."""
    rec = (store or {}).get("recordings", {}).get(fingerprint)
    if not isinstance(rec, dict):
        return []
    return sorted((_norm_mark(m) for m in rec.get("marks", []) if isinstance(m, dict)),
                  key=lambda m: (m["chapter"], m["t"], m["id"]))


def put(store: dict, fingerprint: str, mark: dict) -> dict:
    """Insert or REPLACE `mark` under `fingerprint`, matched on its id (mutates and returns
    `store`). Replacing on the id is what makes editing a mark an edit rather than a duplicate."""
    rec = store.setdefault("recordings", {}).setdefault(fingerprint, {})
    marks = [m for m in rec.get("marks", []) if isinstance(m, dict)]
    norm = _norm_mark(mark)
    marks = [m for m in marks if _text(m.get("id")) != norm["id"]]
    marks.append(norm)
    rec["marks"] = marks
    return store


def remove(store: dict, fingerprint: str, mark_id: str) -> bool:
    """Drop one mark by id (mutates the store). True if one was removed."""
    rec = (store or {}).get("recordings", {}).get(fingerprint)
    if not isinstance(rec, dict):
        return False
    marks = [m for m in rec.get("marks", []) if isinstance(m, dict)]
    kept = [m for m in marks if _text(m.get("id")) != mark_id]
    rec["marks"] = kept
    return len(kept) != len(marks)


def put_and_save(fingerprint: str, mark: dict, path: str | None = None) -> dict:
    """Load, insert-or-replace, write back atomically, and return the new store. The one call the
    mark editor's OK button makes. Any OSError from the write propagates to the caller."""
    store = load(path)
    put(store, fingerprint, mark)
    save(store, path)
    return store


def remove_and_save(fingerprint: str, mark_id: str, path: str | None = None) -> dict:
    """Delete one mark and write the store back, KEEPING A COPY.

    Deleting a mark destroys a sentence that exists nowhere else, so — exactly like ``clear`` — the
    whole store is copied to its ``.bak`` sidecar FIRST and ``restore`` puts it back. A no-op (no
    such mark) writes nothing and takes no backup, so clicking Delete on an already-gone row cannot
    churn the one backup slot out from under the marks that are still there."""
    if path is None:
        path = marks_path()
    store = load(path)
    if not any(m.get("id") == mark_id for m in get(store, fingerprint)):
        return store
    if os.path.exists(path):
        _copy_to_backup(path, "the marks before deleting one")
    remove(store, fingerprint, mark_id)
    save(store, path)
    return store


def forget_and_save(fingerprint: str, path: str | None = None) -> dict:
    """Forget one RECORDING's marks entirely and write the store back, KEEPING A COPY. The privacy
    "forget this recording" gesture reaches this, beside the library row and the session record it
    already drops."""
    if path is None:
        path = marks_path()
    store = load(path)
    if not get(store, fingerprint):
        return store
    if os.path.exists(path):
        _copy_to_backup(path, "the marks before forgetting one recording")
    store.get("recordings", {}).pop(fingerprint, None)
    save(store, path)
    return store


def clear(path: str | None = None) -> dict:
    """Wipe every mark and write the store back atomically, KEEPING A COPY in the ``.bak`` sidecar
    first (``restore`` puts it back). Returns the fresh empty store. The copy is best-effort like
    every other backup here — a failed copy logs and the wipe still proceeds."""
    if path is None:
        path = marks_path()
    if os.path.exists(path):
        _copy_to_backup(path, "the marks before clearing them")
    save(empty_store(), path)
    return load(path)


def count(store: dict) -> int:
    """How many hand-authored marks the whole store holds, across every recording."""
    return sum(len(get(store, fp)) for fp in (store or {}).get("recordings", {}))


def backup_summary(path: str | None = None) -> dict | None:
    """What the ``.bak`` sidecar holds, or None when there is nothing worth restoring (no backup,
    an unreadable one, or one with zero marks)::

        {"path": <the .bak path>, "marks": <int >= 1>, "mtime": <float POSIX seconds | None>}

    Kept format-free (a raw mtime, not a date string) so this module stays display-agnostic."""
    bak = backup_path(path)
    if not os.path.exists(bak):
        return None
    n = count(load(bak))
    if not n:
        return None
    try:
        mtime = os.path.getmtime(bak)
    except OSError:
        mtime = None
    return {"path": bak, "marks": n, "mtime": mtime}


def restore(path: str | None = None) -> dict:
    """Put the ``.bak`` backup back as the live store, and return the result.

    A restore is a SWAP: the store it replaces becomes the new ``.bak``, so restoring is itself
    reversible. REFUSES to act — returns the current store unchanged — when the backup is missing,
    unreadable or holds no marks: replacing live notes with nothing would be the very data loss this
    exists to undo. `path` defaults to ``marks_path()``; raises OSError on an unwritable
    destination (the swap half is best-effort and only logs)."""
    if path is None:
        path = marks_path()
    store = load(backup_path(path))
    if not count(store):
        return load(path)
    swap = path + ".swap"
    kept = False
    if os.path.exists(path):
        try:
            shutil.copy2(path, swap)
            kept = True
        except OSError as exc:
            _log.warning("marks: could not keep the replaced store before restoring (%r)", exc)
    save(store, path)
    if kept:
        try:
            os.replace(swap, backup_path(path))
        except OSError as exc:
            _log.warning("marks: restored the store but could not swap the backup (%r)", exc)
    return load(path)


# ---------------------------------------------------------------- the anchor
def chapter_stem(path: str) -> str:
    """The anchor's chapter key: a file's basename without its extension ("GX010062"). The STEM
    rather than the whole path because a recording that moves folders is the same recording — the
    timing-line sidecar and the library index both key on the stem for the same reason."""
    return os.path.splitext(os.path.basename(str(path or "")))[0]


def anchor_from_global(chapter_map, t: float) -> tuple[str, float] | None:
    """(chapter stem, seconds into that chapter) for a GLOBAL media-clock time — the conversion a
    mark is AUTHORED through. None when there is no chapter map to convert against.

    Duck-typed on `chapters.ChapterMap` (`.to_local`, `.chapters`), like `chapters.desync_notice`,
    so a test can hand in a stand-in without building one."""
    if chapter_map is None or not getattr(chapter_map, "chapters", None):
        return None
    index, local = chapter_map.to_local(float(t))
    return chapter_stem(chapter_map.chapters[index].path), float(local)


def global_from_anchor(chapter_map, chapter: str, t: float) -> float | None:
    """The GLOBAL media-clock time of an anchor, or None when that chapter is not part of THIS open.

    None is the honest answer and the callers treat it as one: a mark typed against chapter 3 while
    only chapter 1 is loaded is still the driver's note, so it is listed (greyed, naming its
    chapter) and simply not drawn. Clamping it into the loaded span would put a sentence about turn
    4 on the start/finish straight, and dropping it would lose it."""
    if chapter_map is None:
        return None
    for c in getattr(chapter_map, "chapters", ()):
        if chapter_stem(c.path) == chapter:
            return float(c.offset) + float(t)
    return None


def resolve(stored: list[dict], chapter_map) -> list[dict]:
    """Stored marks -> RUNTIME marks: the same fields plus the resolved global media times and a
    `placed` flag. The one shape every view consumes, so the band, the list and the jump keys can
    never disagree about where a mark is.

    An unplaceable mark (its chapter is not in this open) keeps `t=None` and `placed=False`."""
    out = []
    for m in stored:
        t = global_from_anchor(chapter_map, m["chapter"], m["t"])
        t_end = (None if t is None or m["t_end"] is None
                 else t + (m["t_end"] - m["t"]))
        out.append({**m, "t": t, "t_end": t_end, "placed": t is not None,
                    "anchor_t": m["t"], "anchor_end": m["t_end"]})
    return sorted(out, key=lambda m: (not m["placed"], m["t"] if m["placed"] else 0.0, m["id"]))


# ---------------------------------------------------------------- the derived half
def _auto(mark_id: str, type: str, t: float, t_end: float | None, note: str,
          lap: int | None = None) -> dict:
    """One derived mark, in the same runtime shape `resolve` produces. It carries no anchor: an
    auto mark is re-derived on every load and never written, so it has nothing to survive."""
    return {"id": mark_id, "kind": KIND_AUTO, "chapter": "", "t": float(t),
            "t_end": None if t_end is None else float(t_end), "type": type,
            "colour": TYPE_COLOUR[type], "note": note, "created": None, "placed": True,
            "anchor_t": None, "anchor_end": None, "lap": lap}


def auto_marks(dropouts=(), excluded=(), timeline=None,
               min_degraded_s: float = MIN_DEGRADED_S) -> tuple[list[dict], int]:
    """Derive this recording's auto marks from what the app has ALREADY detected. Returns
    ``(marks, suppressed)`` — the marks on the global media clock, and how many degraded stretches
    were held back as too short to mark (see `MIN_DEGRADED_S`).

      * `dropouts` — ``(lap_id, t0, t1)`` per interior GPS gap, straight from the very
        `gapfill.find_gaps` call behind `Session.lap_has_dropout`. Passed IN rather than re-derived
        here so the mark set and the lap table's dropout flag cannot be two different answers: a
        lap carries a dropout mark exactly when `lap_has_dropout` is True for it.
      * `excluded` — ``(lap_id, t0, t1)`` per lap in `Session.excluded_lap_ids`, over that lap's
        own window. Same rule: the strip that says a lap was left out and the mark that says so are
        the same list.
      * `timeline` — the `data_quality.QualityTimeline` the quality strip paints, folded into its
        maximal runs of not-GOOD cells with the same `CONCERN_CLASSES` the strip uses.

    UNREPORTED is not a concern class and so is never marked — a GPS5-era camera writes no
    per-sample quality at all, and a mark saying "GPS degraded" over a stream that carries nothing
    to be degraded about would be a claim the app cannot support."""
    out: list[dict] = []
    for lap_id, t0, t1 in dropouts:
        out.append(_auto(f"auto:dropout:{lap_id}:{t0:.3f}", TYPE_DROPOUT, t0, t1,
                         f"GPS dropout — {t1 - t0:.1f} s with no fix", lap=lap_id))
    for lap_id, t0, t1 in excluded:
        out.append(_auto(f"auto:excluded:{lap_id}", TYPE_EXCLUDED, t0, t1,
                         "lap left out of the times — its length is off the session median",
                         lap=lap_id))
    suppressed = 0
    for t0, t1, cls in _concern_runs(timeline):
        if t1 - t0 < float(min_degraded_s):
            suppressed += 1
            continue
        word = data_quality.QUALITY_LABEL[cls].lower()
        out.append(_auto(f"auto:degraded:{t0:.3f}", TYPE_DEGRADED, t0, t1,
                         f"GPS {word} for {t1 - t0:.0f} s — "
                         f"{data_quality.QUALITY_MEANING[cls]}"))
    out.sort(key=lambda m: (m["t"], m["id"]))
    return out, suppressed


def _concern_runs(timeline) -> list[tuple[float, float, int]]:
    """`timeline`'s maximal contiguous runs of not-GOOD cells as ``(t0, t1, worst class)``, on the
    same media clock the strip paints. Empty for no timeline.

    Worst-wins within a run, which is the rule `QualityTimeline.worst_between` and the strip's own
    pixel folding both use — a run holding one POOR second among MODERATE ones is a POOR run."""
    if timeline is None or not len(timeline):
        return []
    cls = timeline.cls
    cell = timeline.cell_s
    runs: list[tuple[float, float, int]] = []
    i, n = 0, len(cls)
    while i < n:
        if int(cls[i]) not in data_quality.CONCERN_CLASSES:
            i += 1
            continue
        j = i
        while j < n and int(cls[j]) in data_quality.CONCERN_CLASSES:
            j += 1
        runs.append((i * cell, j * cell, int(cls[i:j].min())))
        i = j
    return runs


# ---------------------------------------------------------------- display-agnostic helpers
def merge(auto: list[dict], manual: list[dict]) -> list[dict]:
    """The one ordered list every surface reads: the derived marks and the driver's own, in time
    order, with the unplaceable ones last. Ties go to the MANUAL mark — if a note was typed at the
    same instant a detector fired, the sentence a human wrote is the one to read first."""
    return sorted([*auto, *manual],
                  key=lambda m: (not m.get("placed", True),
                                 m["t"] if m.get("placed", True) else 0.0,
                                 0 if m["kind"] == KIND_MANUAL else 1,
                                 m["id"]))


def neighbour(marks: list[dict], t: float, direction: int, *,
              eps: float = 0.05) -> dict | None:
    """The next mark after `t` (direction +1) or the previous one before it (-1), or None at the
    end of the run. Only PLACED marks are reachable — a jump key must never seek to a mark this
    open cannot show.

    `eps` keeps a jump from landing on the mark you are already standing on: seeking is not exact
    (the player rounds to a frame), so "the next mark after here" has to mean "strictly later by
    more than a frame or two" or the key stops working at every mark it reaches."""
    placed = [m for m in marks if m.get("placed", True) and m.get("t") is not None]
    if not placed:
        return None
    if direction >= 0:
        later = [m for m in placed if m["t"] > t + eps]
        return min(later, key=lambda m: m["t"]) if later else None
    earlier = [m for m in placed if m["t"] < t - eps]
    return max(earlier, key=lambda m: m["t"]) if earlier else None


def is_range(mark: dict) -> bool:
    """True when a mark covers a SPAN rather than an instant."""
    return mark.get("t_end") is not None and mark.get("t") is not None


def duration(mark: dict) -> float | None:
    """A range mark's length in seconds, or None for a moment."""
    return (mark["t_end"] - mark["t"]) if is_range(mark) else None


def summary(mark: dict) -> str:
    """One line for a mark: its note if it has one, else the type's own label. A hand-authored mark
    with no note yet is not an error — the key drops the mark first and the words come after — so it
    reads as its type rather than as an empty row."""
    return mark.get("note") or TYPE_LABEL.get(mark.get("type", ""), "Mark")
