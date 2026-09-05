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
    {"version":   1,
     "track":     <registry track name or null>,
     "start":     [[lat, lon], [lat, lon]],
     "sectors":   [[[lat, lon], [lat, lon]], ...],
     "confirmed": <bool — the user placed/confirmed the start line (optional; absent → True
                   for legacy sidecars, which could only have been written by a user edit)>}

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

# What ``load`` raises when the file IS THERE and cannot be used, so a caller can tell that from
# "there is nothing saved for this recording". The two were one answer (None) for both, and the one
# caller read it as "nothing to restore" — so a damaged sidecar silently discarded the user's
# hand-placed start/finish line and the app then asked them to place it again (QA D2-04). Absent is
# still None: it is the ordinary case, and it is correctly silent.
#
# An EXCEPTION rather than a second return value because that is what the distinction is: a file the
# user has that this build cannot honour is exceptional, a file they never wrote is not. Callers
# that only want the lines keep the old shape by catching it.


class SidecarUnreadable(Exception):
    """A sidecar exists at this path but is unusable — unreadable, not JSON, not this version, or
    structurally invalid. Carries the path and a short machine-ish `reason` for the log."""

    def __init__(self, path: str, reason: str):
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


# The fourth answer ``Session.restore_saved_timing_lines`` can give, beside True (applied) / False
# (the revert guard rejected them) / None (nothing saved). Spelled once, here, so the seam that
# RAISES and the window that REPORTS cannot drift into two names for one state. A plain string
# rather than a sentinel object because the other three answers are `is`-compared singletons and a
# fourth falsy/truthy object would make `restored is False` a coin toss to read.
UNREADABLE = "unreadable"


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
    ``version``/``track``/``start``/``sectors``/``confirmed``), or **None when there is no sidecar
    at all** — the ordinary case, correctly silent, and the caller keeps its auto-fitted lines.

    Raises ``SidecarUnreadable`` when a file IS there and cannot be used: unreadable, not JSON, not
    a JSON object, not version-1, or structurally invalid. That distinction is the whole point of
    this function's contract — every one of those cases used to return the same None as "absent",
    so the user's hand-placed start/finish line was discarded without a word (QA D2-04).

    ``confirmed`` defaults to True when the key is absent: a legacy sidecar (pre-trust-marker)
    was only ever written by a deliberate user edit, so it counts as a confirmed start line."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None                      # no sidecar for this recording — nothing to say
    except IsADirectoryError as exc:
        raise SidecarUnreadable(path, "is a directory") from exc
    except OSError as exc:               # permissions, an unreadable volume, a half-copied file
        raise SidecarUnreadable(path, f"unreadable ({type(exc).__name__})") from exc
    except ValueError as exc:            # json.JSONDecodeError — truncated or not JSON at all
        raise SidecarUnreadable(path, "not valid JSON") from exc
    if not isinstance(data, dict):
        raise SidecarUnreadable(path, "not a JSON object")
    if data.get("version") != VERSION:
        raise SidecarUnreadable(path, f"version {data.get('version')!r}, not {VERSION}")
    track = data.get("track")
    if track is not None and not isinstance(track, str):
        raise SidecarUnreadable(path, "track is not a name")
    start = data.get("start")
    sectors = data.get("sectors", [])
    if not _valid_line(start):
        raise SidecarUnreadable(path, "no usable start line")
    if not isinstance(sectors, list) or not all(_valid_line(s) for s in sectors):
        raise SidecarUnreadable(path, "a sector line is not two lat/lon points")
    confirmed = data.get("confirmed", True)
    if not isinstance(confirmed, bool):
        raise SidecarUnreadable(path, "confirmed is not a boolean")
    return {"version": VERSION, "track": track, "confirmed": confirmed,
            "start": _norm_line(start), "sectors": [_norm_line(s) for s in sectors]}


def save(path: str, track: str | None, start, sectors, confirmed: bool = True) -> None:
    """Write the sidecar for a recording: the user's current timing lines as absolute
    (lat, lon) endpoint pairs (`start` = one line, `sectors` = a list of lines), plus the
    detected track name (or None) and whether the start line is user-``confirmed`` (the
    timing-trust marker). Written via a same-directory temp file + ``os.replace`` so a crash
    mid-write can never leave a truncated sidecar. Raises OSError on an unwritable destination
    — the caller decides how to surface that."""
    data = {"version": VERSION, "track": track, "confirmed": bool(confirmed),
            "start": _norm_line(start), "sectors": [_norm_line(s) for s in sectors]}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
