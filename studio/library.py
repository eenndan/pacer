"""Session library: a local index of analyzed recordings (F8) with PB progression.

Every successful load UPSERTS the opened recording into one JSON index stored in the macOS
app-support dir, ``~/Library/Application Support/pacer/library.json``. Re-opening the SAME
recording updates its entry in place rather than appending a duplicate — the dialog then lists
every recording you've analyzed (date / track / best lap / theoretical best), re-opens any of
them, and draws a per-track PB-progression mini-chart (best lap vs date).

This module is PACER-FREE BY CONTRACT — like ``studio.sidecar`` it is pure path resolution,
schema validation and atomic JSON I/O, unit-testable with no telemetry file and no Qt. The
recording's analyzed values are extracted from the live ``Session`` by the caller
(``Session.library_entry()``) and handed in as a plain dict; this file never imports pacer.

Identity — the FINGERPRINT. One entry per *recording*, keyed by
``"<first-chapter stem>|<total duration rounded to 0.1 s>"`` (e.g. ``"GX010062|275.4"``).
WHY this key and not the file path: the same recording can be opened as a single chapter
(GX010062.MP4) or as its full chaptered chain (GX010062+GX020062+GX030062), and the path list
differs between those two opens — but the FIRST chapter's stem and the recording's total media
duration are the same either way, so both opens map to ONE entry. The duration disambiguates the
rare case of two unrelated recordings whose first chapter happens to share a stem in different
folders (the stem is the basename, not the full path). Rounded to 0.1 s so the fingerprint is
stable across the sub-millisecond jitter in a remuxed/recomputed duration.

Schema (version 1) — one JSON object::

    {"version": 1,
     "entries": [
       {"fingerprint": "GX010062|275.4",   # the identity key (see above)
        "stem":        "GX010062",          # first-chapter stem, for display
        "track":       <registry track name or null>,
        "date":        "YYYY-MM-DD" | null,  # GPS9 wall-clock date (Session.session_date)
        "lap_count":   <int>,                # valid lap count
        "best":        <float seconds> | null,    # best lap time
        "theoretical": <float seconds> | null,    # Session.theoretical_best
        "paths":       ["/abs/GX010062.MP4", ...]}, # the chapter file path(s) as opened
       ...]}

Defensive load: ANY corruption (missing file, not JSON, wrong version, malformed entries) →
a fresh EMPTY index ``{"version": 1, "entries": []}`` — the same "self-heal to a safe default"
philosophy as the sidecar's revert guard, so a garbage file never crashes the library and the
next upsert writes a clean index over it.

Float round-trip: json writes floats with ``repr`` (the shortest EXACT double string), so a
save→load round trip of ``best``/``theoretical`` is bit-identical.
"""

from __future__ import annotations

import json
import math
import os

VERSION = 1

# The library file's name + its directory under the user's app-support root. Kept as a module
# constant so a test can monkeypatch `library._app_support_dir` (the seam below) to a temp dir
# and NEVER touch the user's real ~/Library/Application Support/pacer.
_FILENAME = "library.json"
_APP_DIR_NAME = "pacer"


def _app_support_dir() -> str:
    """The macOS per-user app-support directory for pacer
    (``~/Library/Application Support/pacer``). The single override seam the tests monkeypatch
    so the suite never reads or writes the user's real library — patch THIS function."""
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


def fingerprint(stem: str, total_duration: float) -> str:
    """The recording identity key: ``"<stem>|<duration rounded to 0.1 s>"`` (see module docstring
    for the WHY). The duration is rounded to one decimal and formatted with one decimal place so
    275.4 and 275.44 collapse to the same key and the string is canonical (no float repr drift)."""
    return f"{stem}|{round(float(total_duration), 1):.1f}"


def _valid_entry(e) -> bool:
    """True iff `e` is a structurally valid library entry. Used by the defensive load — one bad
    entry rejects the WHOLE file (self-heal to empty), matching the sidecar's all-or-nothing
    contract (a half-trusted index is worse than a clean empty one)."""
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
    # bool is an int subclass — accept it as 0/1 would be wrong here, but lap counts are written
    # as real ints; reject bool explicitly to keep the schema honest.
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
    """Canonicalize a validated entry to the exact stored shape + key order (floats stay floats
    for repr round-trip; lap_count an int; paths a fresh list)."""
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
    """Load + validate the library index. Returns the normalized index dict
    (``{"version", "entries"}``); on ANY problem — file absent, unreadable, not JSON, not
    version-1, or a single malformed entry — returns a fresh ``empty_index()`` instead of raising.
    `path` defaults to ``library_path()`` (honours a patched app-support seam)."""
    if path is None:
        path = library_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return empty_index()
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return empty_index()
    entries = data.get("entries")
    if not isinstance(entries, list) or not all(_valid_entry(e) for e in entries):
        return empty_index()
    return {"version": VERSION, "entries": [_norm_entry(e) for e in entries]}


def save(index: dict, path: str | None = None) -> None:
    """Write the index atomically: a same-directory temp file + ``os.replace`` so a crash
    mid-write can never leave a truncated library. Creates the app-support directory if missing.
    `path` defaults to ``library_path()``. Raises OSError on an unwritable destination — the
    caller (the post-load upsert) SWALLOWS that so a read-only app-support dir can never disrupt
    a load."""
    if path is None:
        path = library_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Re-normalize on the way out so a hand-built index (or one mutated by upsert) is stored in
    # the canonical shape/order, and only the schema fields are persisted.
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
