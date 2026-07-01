"""Session library: a local index of analyzed recordings (F8) with PB progression.

Every successful load upserts the opened recording into one JSON index in the macOS app-support
dir; the dialog lists every analyzed recording, re-opens any, and draws a per-track PB chart.
PACER-FREE: pure path resolution, schema validation and atomic JSON I/O (the analyzed values
arrive as a plain dict from ``Session.library_entry()``).

fingerprint = (GoPro prefix, recording number) so every chapter of a recording maps to one
entry — neither the path list nor the media duration is stable across a single-chapter vs a full
chaptered open of the SAME recording.

Schema (version 1) — one JSON object::

    {"version": 1,
     "entries": [
       {"fingerprint": "GX0062",            # the chapter-invariant identity key (see above)
        "stem":        "GX010062",          # first-chapter stem, for display
        "track":       <registry track name or null>,
        "date":        "YYYY-MM-DD" | null,  # GPS9 wall-clock date (Session.session_date)
        "lap_count":   <int>,                # valid lap count
        "best":        <float seconds> | null,    # best lap time
        "theoretical": <float seconds> | null,    # Session.theoretical_best
        "paths":       ["/abs/GX010062.MP4", ...]}, # the chapter file path(s) as opened (absolute)
       ...]}

Load self-heals: file-level corruption -> empty index; one bad entry -> dropped (count logged),
the rest kept (so the next ``save``, which rewrites only the survivors, doesn't lose all history).
"""

from __future__ import annotations

import json
import logging
import math
import os
import re

_log = logging.getLogger(__name__)

VERSION = 1

# GoPro stem G[XHPL]<CC><NNNN>; the CC chapter index is stripped so every chapter shares one key.
_GOPRO_STEM_RE = re.compile(r"^(G[XHPL])\d{2}(\d{4})$", re.IGNORECASE)

_FILENAME = "library.json"
_APP_DIR_NAME = "pacer"


def _app_support_dir() -> str:
    """macOS app-support dir for pacer (~/Library/Application Support/pacer). The single seam
    tests monkeypatch so the suite never touches the real library."""
    return os.path.join(
        os.path.expanduser("~"), "Library", "Application Support", _APP_DIR_NAME)


def library_path() -> str:
    """Absolute path to the library index (``<app-support>/pacer/library.json``). Resolves the
    app-support dir through ``_app_support_dir`` so tests that patch that seam are honoured. Does
    NOT create the directory — that happens lazily on the first ``save`` (only a write needs it;
    a read of a missing file already returns the safe empty index)."""
    return os.path.join(_app_support_dir(), _FILENAME)


def empty_index() -> dict:
    """A fresh, valid, empty index — the safe default every corruption path returns to, and the
    starting point before the first recording is added."""
    return {"version": VERSION, "entries": []}


def fingerprint(stem: str) -> str:
    """Chapter-invariant identity key from a first-chapter stem: GoPro ``G[XHPL]<CC><NNNN>`` drops
    ``CC`` -> prefix+NNNN (``"GX010062"`` -> ``"GX0062"``); a non-GoPro stem keys on itself."""
    m = _GOPRO_STEM_RE.match(stem or "")
    if m is None:
        return stem
    return f"{m.group(1).upper()}{m.group(2)}"


def _valid_entry(e) -> bool:
    """True iff `e` is a structurally valid library entry; load() drops invalid rows (keeps the
    rest)."""
    if not isinstance(e, dict):
        return False
    fp, stem = e.get("fingerprint"), e.get("stem")
    if not isinstance(fp, str) or not fp or not isinstance(stem, str):
        return False
    track, date = e.get("track"), e.get("date")
    if track is not None and not isinstance(track, str):
        return False
    if date is not None and not isinstance(date, str):
        return False
    lap_count = e.get("lap_count")
    # bool is an int subclass; lap counts are real ints, so reject bool explicitly.
    if isinstance(lap_count, bool) or not isinstance(lap_count, int) or lap_count < 0:
        return False
    for key in ("best", "theoretical"):
        v = e.get(key)
        if v is not None and (
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
        ):
            return False
    paths = e.get("paths")
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        return False
    return True


def _norm_entry(e: dict) -> dict:
    """Canonicalize a validated entry to the stored shape + key order."""
    best = e.get("best")
    theo = e.get("theoretical")
    return {
        "fingerprint": str(e["fingerprint"]),
        "stem": str(e["stem"]),
        "track": e.get("track"),
        "date": e.get("date"),
        "lap_count": int(e["lap_count"]),
        "best": None if best is None else float(best),
        "theoretical": None if theo is None else float(theo),
        "paths": [str(p) for p in e.get("paths", [])],
    }


def load(path: str | None = None) -> dict:
    """Load + validate the library index, returning the normalized dict. File-level corruption
    (absent / unreadable / not JSON / not a dict / wrong version / non-list ``entries``) ->
    ``empty_index()``; a single malformed entry is dropped (count logged), the rest kept. `path`
    defaults to ``library_path()``."""
    if path is None:
        path = library_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return empty_index()
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return empty_index()
    raw = data.get("entries")
    if not isinstance(raw, list):
        return empty_index()
    entries = [e for e in raw if _valid_entry(e)]
    dropped = len(raw) - len(entries)
    if dropped:
        # A later save rewrites only the survivors, healing the file.
        _log.warning("library: dropped %d malformed entr%s of %d from %s",
                     dropped, "y" if dropped == 1 else "ies", len(raw), path)
    return {"version": VERSION, "entries": [_norm_entry(e) for e in entries]}


def save(index: dict, path: str | None = None) -> None:
    """Write the index atomically (temp file + ``os.replace``) so a crash mid-write can't leave a
    truncated library. Creates the app-support dir if missing. `path` defaults to
    ``library_path()``. Raises OSError on an unwritable destination."""
    if path is None:
        path = library_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Re-normalize on the way out: store only the schema fields, in canonical shape/order.
    out = {"version": VERSION, "entries": [_norm_entry(e) for e in index.get("entries", [])]}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def upsert(index: dict, entry: dict) -> dict:
    """Insert `entry`, or REPLACE the existing entry with the same fingerprint — the no-duplicate
    rule. Mutates and returns `index` (entries list). The replacement keeps the entry's POSITION
    so a re-open doesn't reshuffle the library order; a new fingerprint appends. `entry` must be
    a valid entry dict (built by ``Session.library_entry`` / a test); it is normalized on store."""
    norm = _norm_entry(entry)
    entries = index.setdefault("entries", [])
    for i, e in enumerate(entries):
        if e.get("fingerprint") == norm["fingerprint"]:
            entries[i] = norm
            return index
    entries.append(norm)
    return index


def upsert_and_save(entry: dict, path: str | None = None) -> dict:
    """Load the current index, upsert `entry`, write it back atomically, and return the new
    index. The one call the app makes post-load. Any OSError from the write propagates to the
    caller, which guards it (a library write must never disrupt the app)."""
    index = load(path)
    upsert(index, entry)
    save(index, path)
    return index


def remove(index: dict, fingerprint_key: str) -> bool:
    """Drop the entry with fingerprint `fingerprint_key` from `index` (mutates the entries list).
    Returns True if an entry was removed, False if none matched. The privacy "forget this recording"
    control's index half — the sidecar-file deletion is a separate, guarded os call in the app."""
    entries = index.setdefault("entries", [])
    for i, e in enumerate(entries):
        if e.get("fingerprint") == fingerprint_key:
            del entries[i]
            return True
    return False


def clear(path: str | None = None) -> None:
    """Wipe the whole library index to an empty one and write it back atomically. Removes ONLY the
    app-support index (the personal history of what/where you recorded) — the actual media files and
    their per-video ``.pacer.json`` sidecars are left untouched. `path` defaults to
    ``library_path()``; raises OSError on an unwritable destination."""
    save(empty_index(), path)


def pb_series(index: dict, track: str) -> list[tuple[str, float]]:
    """The PB-progression series for one `track`: ``[(date, best), ...]`` over every entry of that
    track that has BOTH a date and a best lap, sorted ascending by date (then by best, so two
    sessions on the same day order by lap time). The mini-chart plots best-vs-date from this.
    Entries with no date or no best are dropped (nothing to place on the time axis)."""
    pts = [
        (e["date"], float(e["best"]))
        for e in index.get("entries", [])
        if e.get("track") == track and e.get("date") and e.get("best") is not None
    ]
    pts.sort(key=lambda p: (p[0], p[1]))
    return pts
