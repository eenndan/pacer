"""Registry of known tracks, so the start/finish line is a TRACK property (fixed at the
real start/finish straight) rather than guessed per-session via `pick_random_start`.

A track entry carries a detection centroid (lat/lon) and the start/finish line as two
ABSOLUTE lat/lon points. `detect_track` matches a trace centroid to an entry within a
small radius; `start_line_segment` converts the two points into a `pacer.Segment` in the
LOCAL meters that the laps/timing lines live in (via `cs.local`).

This is the only studio module besides session.py that names `pacer`; it touches only the
pure geometry types (GPSSample/CoordinateSystem/Segment/Point), no I/O — kept here so
session.py stays the single owner of the load/segmentation pipeline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pacer

# Match a trace to a track when its centroid is within this many metres of the entry's
# detection centroid (generous — GPS centroids drift with how much of an out-lap is kept).
DETECT_RADIUS_M = 1500.0
EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True)
class Track:
    """A known track: a detection centroid and a fixed start/finish line (abs lat/lon)."""

    name: str
    centroid_lat: float
    centroid_lon: float
    start_a: tuple[float, float]  # (lat, lon) of one start/finish endpoint
    start_b: tuple[float, float]  # (lat, lon) of the other endpoint


REGISTRY: list[Track] = [
    Track(
        name="Daytona Milton Keynes",
        centroid_lat=52.0403,
        centroid_lon=-0.7847,
        start_a=(52.04031, -0.78487),
        start_b=(52.04020, -0.78460),
    ),
]


def _equirect_metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Equirectangular metres between two lat/lon points (fine over a few km)."""
    lat0 = math.radians((lat1 + lat2) / 2)
    dx = math.radians(lon2 - lon1) * math.cos(lat0) * EARTH_RADIUS_M
    dy = math.radians(lat2 - lat1) * EARTH_RADIUS_M
    return math.hypot(dx, dy)


def make_segment(x1: float, y1: float, x2: float, y2: float) -> "pacer.Segment":
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


def detect_track(lat: float, lon: float) -> Track | None:
    """The registry track whose detection centroid is within DETECT_RADIUS_M of (lat, lon),
    or None. Picks the nearest if several match."""
    best, best_d = None, DETECT_RADIUS_M
    for trk in REGISTRY:
        d = _equirect_metres(lat, lon, trk.centroid_lat, trk.centroid_lon)
        if d <= best_d:
            best, best_d = trk, d
    return best


def start_line_segment(track: Track, cs) -> "pacer.Segment":
    """The track's start/finish line as a `pacer.Segment` in LOCAL meters (via cs.local).

    Construction goes through `make_segment` so the Segment write-pattern lives in one place.
    """
    a = cs.local(pacer.GPSSample(lat=track.start_a[0], lon=track.start_a[1], altitude=0))
    b = cs.local(pacer.GPSSample(lat=track.start_b[0], lon=track.start_b[1], altitude=0))
    return make_segment(a[0], a[1], b[0], b[1])
