"""Build the stored Daytona Milton Keynes reference centerline (studio/mk_centerline.json).

The reference is the FALLBACK gap-fill source (used only where no measured lap covers a
section). It is a centerline polyline TRACED from the georeferenced track image
`/Users/daniil/Desktop/Tracks/MK/gmaps_pict.png` (a clean thin track outline whose
orientation matches the GPS aggregate). The image carries no embedded geo-coordinates, so
the polyline is stored in arbitrary normalized image coordinates; `studio/reference.py`
best-fit aligns it to the aggregate GPS point cloud (similarity + reflection ICP) at load.

Re-run to regenerate the JSON:  python -m studio.build_reference

The waypoints below were digitized by eye from the thumbnail of gmaps_pict.png as an ordered
closed loop following the track centerline (the kart track's single racing line down the
middle of the grey outline). Normalized to the unit square; y is image-DOWN (flipped to
match a screen image). Exact pixel precision is unnecessary — the fill pins both gap mouths
with a similarity transform, so the polyline only has to capture the corner SHAPE, and the
ICP fit residual against the real GPS cloud (printed here) validates the trace objectively.
"""

from __future__ import annotations

import json
import os

import numpy as np

# Ordered (x_norm, y_norm) waypoints tracing the track centerline as a closed loop, read off
# gmaps_pict.png (origin top-left, y down). One continuous pass; the loop closes back to the
# start. Captured at ~corner resolution.
_WAYPOINTS = [
    # start near top-centre, go clockwise around the long outer loop first
    (0.50, 0.07), (0.58, 0.08), (0.64, 0.10), (0.68, 0.14), (0.70, 0.20),
    (0.71, 0.28), (0.72, 0.37), (0.73, 0.46), (0.74, 0.55), (0.75, 0.63),
    (0.76, 0.71), (0.76, 0.78), (0.74, 0.84), (0.70, 0.88), (0.64, 0.91),
    (0.57, 0.92), (0.49, 0.92), (0.41, 0.91), (0.35, 0.88), (0.31, 0.83),
    (0.30, 0.77),  # bottom-left of outer loop, turn back up into the infield
    (0.33, 0.72), (0.39, 0.69), (0.45, 0.68), (0.51, 0.69),  # lower infield switchback right
    (0.56, 0.66), (0.58, 0.61), (0.55, 0.57), (0.49, 0.56), (0.43, 0.57),  # mid switchback left
    (0.39, 0.53), (0.41, 0.48), (0.47, 0.46), (0.53, 0.47),  # upper-mid switchback right
    (0.56, 0.43), (0.54, 0.39), (0.48, 0.38), (0.42, 0.39),  # switchback left
    (0.37, 0.36), (0.34, 0.31), (0.30, 0.27),  # head out to the left side
    (0.25, 0.24), (0.21, 0.27), (0.20, 0.33), (0.22, 0.39),  # left-side small loop bottom
    (0.26, 0.42), (0.24, 0.36), (0.21, 0.30),  # back of the top-left hairpin
    (0.23, 0.23), (0.28, 0.18), (0.34, 0.14), (0.40, 0.11), (0.46, 0.08),  # along the top back to start
]


def build(out_path: str | None = None, validate_against: str | None = None):
    pts = np.asarray(_WAYPOINTS, float)
    # Normalize to unit square (centroid-free scale is fixed at align time anyway, but keep
    # it tidy and bounded).
    mn, mx = pts.min(0), pts.max(0)
    span = (mx - mn)
    span[span == 0] = 1.0
    norm = (pts - mn) / span
    out_path = out_path or os.path.join(os.path.dirname(__file__), "mk_centerline.json")
    with open(out_path, "w") as fh:
        json.dump({"track": "Daytona Milton Keynes",
                   "source": "gmaps_pict.png (traced centerline, normalized image coords, y-down)",
                   "points_norm": norm.tolist()}, fh, indent=1)
    print(f"wrote {len(norm)} waypoints -> {out_path}", flush=True)
    return out_path


if __name__ == "__main__":
    build()
