"""Extract GPS trace + lap geometry from a recording into a small JSON cache, so the
start-line analysis can iterate without reloading the 12 GB MP4s each time.

Dumps, for a recording:
  - trace: lat, lon, x (local), y (local), t (media s), v (km/h)
  - cs origin (clat, clon) used by the Session
  - the current start line both in local metres and back-projected to lat/lon
  - valid lap ids + lap times (default fitted line)
Usage: pixi run python .startline_tmp/extract_trace.py <recording.MP4> <out.json>
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import pacer
from studio import chapters
from studio.session import Session


def main(recording, out):
    paths = chapters.discover_siblings(recording)
    print(f"loading {chapters.recording_label(paths)} ({len(paths)} chapters)...", flush=True)
    sess = Session.load(paths)
    cs = sess.cs

    # cs origin: recover by mapping local (0,0) back to global.
    o = cs.global_(pacer.Vec3f(0.0, 0.0, 0.0))
    clat, clon = float(o.lat), float(o.lon)

    n = len(sess.tx)
    # trace lat/lon: map each local point back to global
    lat = np.empty(n)
    lon = np.empty(n)
    for i in range(n):
        g = cs.global_(pacer.Vec3f(float(sess.tx[i]), float(sess.ty[i]), 0.0))
        lat[i] = g.lat
        lon[i] = g.lon

    sl = sess.start_line  # Seg in local metres (the FITTED line actually used)
    # back-project the start line endpoints to lat/lon
    ga = cs.global_(pacer.Vec3f(float(sl.x1), float(sl.y1), 0.0))
    gb = cs.global_(pacer.Vec3f(float(sl.x2), float(sl.y2), 0.0))

    valid = sess.valid_lap_ids()
    lap_times = [float(sess.laps.lap_time(i)) for i in valid]

    result = {
        "recording": chapters.recording_label(paths),
        "cs_origin": [clat, clon],
        "trace": {
            "lat": lat.tolist(), "lon": lon.tolist(),
            "x": [float(v) for v in sess.tx], "y": [float(v) for v in sess.ty],
            "t": [float(v) for v in sess.tt], "v": [float(v) for v in sess.tv],
        },
        "fitted_start_line_local": [sl.x1, sl.y1, sl.x2, sl.y2],
        "fitted_start_line_latlon": [[ga.lat, ga.lon], [gb.lat, gb.lon]],
        "valid_lap_ids": [int(i) for i in valid],
        "lap_times": lap_times,
        "laps_count": int(sess.laps.laps_count()),
    }
    with open(out, "w") as f:
        json.dump(result, f)
    print(f"wrote {out}: n_trace={n}, valid_laps={len(valid)}, "
          f"laps_count={sess.laps.laps_count()}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
