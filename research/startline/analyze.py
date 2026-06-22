"""Analyze the GPS trace geometry vs the current start line.

Loads the cached trace JSON, computes (in local metres):
  - the trace extent + the current/candidate start line endpoints
  - how many times the FULL trace crosses a given line segment (proxy for total laps)
  - per-lap crossing count for candidate lines (the segmentation risk metric)
  - the speed of the trace near the current line vs a candidate line
No pacer import needed for the geometry (pure numpy seg-seg intersection, matching
pacer::Segment::Intersects semantics).
"""
import json
import math
import sys

import numpy as np

EARTH_R = 6_371_000.0


def latlon_to_local(lat, lon, clat, clon):
    """Equirectangular local metres about (clat,clon): x=East, y=North. Matches the sign
    convention well enough for geometry comparison (the actual cs is ellipsoidal but this is
    only used for our own overlay math; for crossing counts we use the stored local xy)."""
    lat0 = math.radians(clat)
    x = math.radians(np.asarray(lon) - clon) * math.cos(lat0) * EARTH_R
    y = math.radians(np.asarray(lat) - clat) * EARTH_R
    return x, y


def seg_intersect(p1, p2, a, b):
    """True if segment p1->p2 crosses segment a->b (proper crossing). Vectorized over p1,p2
    arrays of shape (N,2); a,b are length-2. Returns boolean array length N."""
    p1 = np.asarray(p1, float)
    p2 = np.asarray(p2, float)
    a = np.asarray(a, float)
    b = np.asarray(b, float)

    def cross(o, u, v):
        return (u[..., 0] - o[..., 0]) * (v[..., 1] - o[..., 1]) - \
               (u[..., 1] - o[..., 1]) * (v[..., 0] - o[..., 0])

    d1 = cross(a, b, p1)
    d2 = cross(a, b, p2)
    d3 = cross(p1, p2, a)
    d4 = cross(p1, p2, b)
    return ((d1 * d2 < 0) & (d3 * d4 < 0))


def count_crossings(x, y, a, b):
    """Number of times the polyline (x,y) crosses the segment a-b."""
    p1 = np.column_stack([x[:-1], y[:-1]])
    p2 = np.column_stack([x[1:], y[1:]])
    return int(seg_intersect(p1, p2, a, b).sum())


def main(path):
    d = json.load(open(path))
    clat, clon = d["cs_origin"]
    x = np.array(d["trace"]["x"])
    y = np.array(d["trace"]["y"])
    v = np.array(d["trace"]["v"])
    t = np.array(d["trace"]["t"])
    print(f"=== {d['recording']} ===")
    print(f"cs_origin = ({clat:.6f}, {clon:.6f})")
    print(f"trace n={len(x)}  x[{x.min():.1f},{x.max():.1f}] y[{y.min():.1f},{y.max():.1f}]")
    print(f"valid_laps={len(d['valid_lap_ids'])} laps_count={d['laps_count']}")

    # current coords from tracks.py
    cur_a_ll = (52.04031, -0.78487)
    cur_b_ll = (52.04020, -0.78460)
    ax, ay = latlon_to_local(cur_a_ll[0], cur_a_ll[1], clat, clon)
    bx, by = latlon_to_local(cur_b_ll[0], cur_b_ll[1], clat, clon)
    print(f"\nCURRENT line A/B in local m (our equirect): A=({float(ax):.1f},{float(ay):.1f}) "
          f"B=({float(bx):.1f},{float(by):.1f})  len={math.hypot(float(bx-ax),float(by-ay)):.1f}m")

    # The FITTED line actually used (from Session, exact cs)
    fl = d["fitted_start_line_local"]
    fll = d["fitted_start_line_latlon"]
    print(f"FITTED line (Session, exact cs) local: A=({fl[0]:.1f},{fl[1]:.1f}) "
          f"B=({fl[2]:.1f},{fl[3]:.1f})  len={math.hypot(fl[2]-fl[0],fl[3]-fl[1]):.1f}m")
    print(f"FITTED line lat/lon: A={fll[0]}  B={fll[1]}")

    # crossings of the FITTED line over the whole trace (proxy for total real laps + double/missed)
    ncross_fit = count_crossings(x, y, (fl[0], fl[1]), (fl[2], fl[3]))
    print(f"\nFULL-TRACE crossings of FITTED line = {ncross_fit}  "
          f"(laps_count={d['laps_count']}, so crossings≈laps+1)")

    # Find where the fitted line midpoint sits, and the trace speed there
    mfx, mfy = (fl[0] + fl[2]) / 2, (fl[1] + fl[3]) / 2
    di = (x - mfx) ** 2 + (y - mfy) ** 2
    near = np.argsort(di)[:200]
    print(f"trace speed near FITTED line midpoint: median={np.median(v[near]):.1f} km/h "
          f"min={v[near].min():.1f} max={v[near].max():.1f}  "
          f"(nearest pt dist={math.sqrt(di.min()):.1f}m)")
    return d


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".startline_tmp/trace_0060.json")
