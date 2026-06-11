"""Sidecar JSON persistence of the user's start/sector timing lines.

A recording's hand-tuned timing lines (the dragged start/finish line + any sector lines)
are saved next to the MP4 as ``<first-chapter stem>.pacer.json`` so they survive an app
restart. The endpoints are stored as ABSOLUTE (lat, lon) — NOT local metres — because the
local frame's origin is the cleaned-trace bbox centre (see ``Session.load``), which shifts
between loads whenever cleaning keeps a slightly different point set; absolute coordinates
are load-invariant. The lat/lon <-> local-metre conversion lives in session.py (it needs
the bound ``CoordinateSystem``); this module is PACER-FREE BY CONTRACT — pure path
resolution, schema validation and JSON I/O, unit-testable with no telemetry file.

Path rule: the sidecar belongs to the RECORDING, not the opened file. It is named after
the FIRST chapter's stem (via ``chapters.discover_siblings``), so a chaptered session
(GX010062+GX020062+GX030062) and a single-file open of any one chapter share ONE sidecar.

Schema (version 1) — one JSON object:
    {"version": 1,
     "track":   <registry track name or null>,
     "start":   [[lat, lon], [lat, lon]],
     "sectors": [[[lat, lon], [lat, lon]], ...]}

Float round-trip: the json module writes floats with ``repr`` — the shortest string that
round-trips the double EXACTLY — so save→load returns bit-identical endpoints and
apply→export→apply is stable.
"""

from __future__ import annotations

import json
import math
import os

from . import chapters

VERSION = 1
SUFFIX = ".pacer.json"


def sidecar_path(recording_path: str) -> str:
    """The sidecar path for (any chapter of) a recording: the FIRST chapter's stem +
    ``.pacer.json``, in the same folder as the MP4. For a non-GoPro name (no chapter
    siblings, e.g. the bundled sample clip) this is just the file's own stem."""
    first = chapters.discover_siblings(recording_path)[0]
    return os.path.splitext(first)[0] + SUFFIX


def _valid_line(line) -> bool:
    """True iff `line` is [[lat, lon], [lat, lon]] with four finite in-range numbers."""
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


def _norm_line(line) -> list[list[float]]:
    return [[float(line[0][0]), float(line[0][1])], [float(line[1][0]), float(line[1][1])]]


def load(path: str) -> dict | None:
    """Parse + validate the sidecar at `path`. Returns the normalized dict (keys:
    ``version``/``track``/``start``/``sectors``) or None when the file is absent,
    unreadable, not valid JSON, not version-1, or structurally invalid — the caller
    treats every None identically (keep the session's auto-fitted lines)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return None
    track = data.get("track")
    if track is not None and not isinstance(track, str):
        return None
    start = data.get("start")
    sectors = data.get("sectors", [])
    if not _valid_line(start):
        return None
    if not isinstance(sectors, list) or not all(_valid_line(s) for s in sectors):
        return None
    return {"version": VERSION, "track": track,
            "start": _norm_line(start), "sectors": [_norm_line(s) for s in sectors]}


def save(path: str, track: str | None, start, sectors) -> None:
    """Write the sidecar for a recording: the user's current timing lines as absolute
    (lat, lon) endpoint pairs (`start` = one line, `sectors` = a list of lines), plus the
    detected track name (or None). Written via a same-directory temp file + ``os.replace``
    so a crash mid-write can never leave a truncated sidecar. Raises OSError on an
    unwritable destination — the caller decides how to surface that."""
    data = {"version": VERSION, "track": track,
            "start": _norm_line(start), "sectors": [_norm_line(s) for s in sectors]}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
