"""Known-track geometry: the start/finish line is a TRACK property (fixed at the real
start/finish straight) rather than guessed per-session via `pick_random_start`.

A track carries a detection centroid (lat/lon) and the start/finish line (+ any sector
lines) as ABSOLUTE lat/lon points. `detect_track` matches a trace centroid to a known
track within a small radius; `start_line_segment` / `sector_line_segments` convert the
endpoints into `pacer.Segment`s in the LOCAL meters the laps/timing lines live in (via
`cs.local`).

The known tracks themselves live in the persisted database (studio/track_db.py): a built-in
seed (Daytona MK, byte-identical to the old hardcoded entry) layered under a per-user JSON
file in the app-support dir. This module is the pacer-touching adapter over that DB — it
turns a DB entry dict into a `Track`/`pacer.Segment`. So `track_db` stays pacer-free and
this stays I/O-free.

One of the few studio modules that may name `pacer` (with session.py, load.py, and
ingest.py); it touches only the pure geometry types (GPSSample/CoordinateSystem/Segment/
Point) — kept here so load.py stays the single owner of the load/segmentation pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pacer

from . import track_db

# Re-exported so callers (load.py, dev tools) keep a single import for the detect radius.
DETECT_RADIUS_M = track_db.DETECT_RADIUS_M


@dataclass(frozen=True)
class Track:
    """A known track: a detection centroid and a fixed start/finish line (+ any sector lines),
    all absolute lat/lon. Built from a track_db entry via `_from_entry`."""

    name: str
    centroid_lat: float
    centroid_lon: float
    start_a: tuple[float, float]  # (lat, lon) of one start/finish endpoint
    start_b: tuple[float, float]  # (lat, lon) of the other endpoint
    # Each sector line as ((lat, lon), (lat, lon)); empty when the track defines no sectors.
    sectors: tuple[tuple[tuple[float, float], tuple[float, float]], ...] = field(
        default_factory=tuple)


def _from_entry(e: dict) -> Track:
    """A `Track` from a normalized track_db entry dict (name / centroid / start / sectors)."""
    (a_lat, a_lon), (b_lat, b_lon) = e["start"]
    sectors = tuple(((s[0][0], s[0][1]), (s[1][0], s[1][1])) for s in e.get("sectors", []))
    return Track(
        name=e["name"],
        centroid_lat=e["centroid"][0],
        centroid_lon=e["centroid"][1],
        start_a=(a_lat, a_lon),
        start_b=(b_lat, b_lon),
        sectors=sectors,
    )


def make_segment(x1: float, y1: float, x2: float, y2: float) -> pacer.Segment:
    """A `pacer.Segment` from two LOCAL-metre endpoints (x1,y1)-(x2,y2).

    Single-sources the pacer.Segment write-pattern (set Point.x/.y by field assignment, then
    assign Segment.first/.second wholesale — the binding round-trips through fresh objects) for
    every construction site (Seg.to_pacer, _widen, start_line_segment). Lives here because
    tracks.py — like session.py — is allowed to name `pacer`; the geometry types only, no I/O.
    """
    seg = pacer.Segment()
    p1, p2 = pacer.Point(), pacer.Point()
    p1.x, p1.y = float(x1), float(y1)
    p2.x, p2.y = float(x2), float(y2)
    seg.first, seg.second = p1, p2
    return seg


def detect_track(lat: float, lon: float, db_path: str | None = None) -> Track | None:
    """The known track whose detection centroid is within DETECT_RADIUS_M of (lat, lon), or
    None; the nearest if several match. Searches the merged built-in-seed + user database
    (studio/track_db.detect), so a built-in track and a user-saved one are both detectable.
    `db_path` overrides the DB file (tests point it at a temp dir)."""
    entry = track_db.detect(lat, lon, db_path)
    return None if entry is None else _from_entry(entry)


def start_line_segment(track: Track, cs) -> pacer.Segment:
    """The track's start/finish line as a `pacer.Segment` in LOCAL meters (via cs.local).

    Construction goes through `make_segment` so the Segment write-pattern lives in one place.
    """
    a = cs.local(pacer.GPSSample(lat=track.start_a[0], lon=track.start_a[1], altitude=0))
    b = cs.local(pacer.GPSSample(lat=track.start_b[0], lon=track.start_b[1], altitude=0))
    return make_segment(a[0], a[1], b[0], b[1])


def sector_line_segments(track: Track, cs) -> list[pacer.Segment]:
    """The track's sector lines as `pacer.Segment`s in LOCAL meters (via cs.local), in order.
    Empty when the track defines no sectors."""
    out = []
    for (a_lat, a_lon), (b_lat, b_lon) in track.sectors:
        a = cs.local(pacer.GPSSample(lat=a_lat, lon=a_lon, altitude=0))
        b = cs.local(pacer.GPSSample(lat=b_lat, lon=b_lon, altitude=0))
        out.append(make_segment(a[0], a[1], b[0], b[1]))
    return out
