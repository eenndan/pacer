"""Geometry track-match: decide whether two recordings were shot at the SAME circuit purely
from their GPS footprint, for the cross-recording reference ("race a friend's GoPro").

The cross-recording reference (studio/cross_reference.py) geometrically ALIGNS two racing-line
loops, so the overlay itself does not need a shared track NAME. But the admission gate in
`Session.set_reference_session` must still refuse two genuinely different circuits (overlaying
them is worse than refusing). When BOTH recordings carry a detected registry `track_name` the
gate matches on the name; when EITHER is an unknown track (name None — the common case, since the
shipped DB holds ~one track) there is no name to compare, so we fall back to GEOMETRY here.

Two independent checks, BOTH required (AND) — either alone is spoofable:

  * LOCATION — the two GPS centroids must be within `MATCH_RADIUS_M`. A circuit is small (a kart
    track spans ~100-300 m, a road course a few km) so two recordings OF THE SAME circuit have
    centroids that coincide within tens of metres — they drift only by how much of an in/out-lap
    each recording kept. Different circuits are kilometres to continents apart. The threshold is
    deliberately CONSERVATIVE (see the constant): tight enough that no two distinct circuits share
    it, loose enough to absorb out-lap centroid drift.

  * SIZE — the bounding-box diagonals must be within `SIZE_RATIO_MAX`×. Two circuits can share a
    centroid region (a big track and a tiny kart track nested inside the same park) yet be
    completely different; requiring comparable extent stops a small track being matched to a large
    course that merely overlaps its centroid.

PACER-FREE (numpy/math only) — takes plain lat/lon centroids + bbox extents, so it is unit-testable
without a telemetry file or a pacer build. Distance is a real haversine on the sphere (the trace
spans a few metres to a few km, well inside where the great-circle formula is exact for our needs).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Mean Earth radius (m) — same value studio/track_db.py uses for its detection distance.
EARTH_RADIUS_M = 6_371_000.0

# Two recordings are treated as the SAME circuit iff their GPS centroids are within this distance.
# CONSERVATIVE by design — a wrong match overlays two DIFFERENT tracks, which is worse than
# refusing, so we err toward refusing. Reasoning for 750 m:
#   * an upper bound on real same-circuit centroid drift — the centroid is the trace bbox centre,
#     which moves only with how much of an in/out-lap each recording keeps; even a long pit approach
#     shifts it by tens to low-hundreds of metres, not ~750 m;
#   * comfortably below the spacing of distinct circuits (even two tracks in the same motorsport
#     park sit >1 km apart), so no two different circuits fall inside it;
#   * TIGHTER than track_db.DETECT_RADIUS_M (1500 m), which is intentionally generous for matching a
#     recording to a saved registry entry; here, with no name to corroborate, we want the smaller
#     margin. Half that radius is the conservative choice.
MATCH_RADIUS_M = 750.0

# The two footprints must also be of comparable SIZE: the larger bbox diagonal at most this many
# times the smaller. Guards the "same centroid region, wildly different track" case (a kart track
# nested inside a big circuit). 2× tolerates real lap-coverage differences (one recording keeping an
# extra out-lap loop or a longer straight run-off) while rejecting an order-of-magnitude size gap.
SIZE_RATIO_MAX = 2.0


@dataclass(frozen=True)
class GeoMatch:
    """The verdict of a geometry track-match: whether the two footprints look like the same
    circuit, plus the measured distance/size gap (for the refusal message + diagnostics)."""

    same_track: bool
    distance_m: float       # haversine distance between the two centroids
    size_ratio: float       # larger bbox diagonal / smaller (>= 1.0)
    reason: str             # "" on a match, else a short human-readable why-not


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two (lat, lon) points (degrees). Haversine on the
    mean-radius sphere — exact enough over the metres-to-kilometres a single recording spans."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def bbox_diagonal_m(bbox: tuple[float, float, float, float]) -> float:
    """The haversine length (m) of a lat/lon bounding box's diagonal — a scale-invariant measure
    of a track's footprint size. `bbox` is (min_lat, min_lon, max_lat, max_lon), matching
    `Session.track_location()`."""
    min_lat, min_lon, max_lat, max_lon = bbox
    return haversine_m(min_lat, min_lon, max_lat, max_lon)


def match(
    centroid_a: tuple[float, float],
    bbox_a: tuple[float, float, float, float],
    centroid_b: tuple[float, float],
    bbox_b: tuple[float, float, float, float],
    *,
    radius_m: float = MATCH_RADIUS_M,
    size_ratio_max: float = SIZE_RATIO_MAX,
) -> GeoMatch:
    """Decide whether footprints A and B look like the SAME circuit. Each `centroid` is (lat, lon)
    and each `bbox` is (min_lat, min_lon, max_lat, max_lon) — exactly `Session.track_location()`'s
    shape. Same iff the centroids are within `radius_m` AND the bbox diagonals are within
    `size_ratio_max`×. Conservative: any failure (or a degenerate/zero-size footprint) => no match.
    """
    dist = haversine_m(centroid_a[0], centroid_a[1], centroid_b[0], centroid_b[1])
    diag_a = bbox_diagonal_m(bbox_a)
    diag_b = bbox_diagonal_m(bbox_b)
    # Ratio >= 1.0 always (larger / smaller); a zero-size footprint can't be sized so it never matches.
    lo, hi = sorted((diag_a, diag_b))
    size_ratio = (hi / lo) if lo > 0 else math.inf

    if dist > radius_m:
        return GeoMatch(False, dist, size_ratio,
                        f"GPS locations are {dist:.0f} m apart (> {radius_m:.0f} m)")
    if not math.isfinite(size_ratio) or size_ratio > size_ratio_max:
        return GeoMatch(False, dist, size_ratio,
                        f"track sizes differ {size_ratio:.1f}× (> {size_ratio_max:.1f}×)")
    return GeoMatch(True, dist, size_ratio, "")
