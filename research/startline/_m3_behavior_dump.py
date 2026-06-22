"""Behavior-identical dump for M3 refactor verification.

Loads the single-chapter session (GX010060) and the chaptered session (GX0x0062) via the studio
Session pipeline (which drives the full pacer C++ engine: GPS gating/clean/smooth, coordinate
system, lap segmentation, distances, plus the IMU/g-meter), and writes every numeric output we
care about to a JSON. Run in BEFORE (M2) and AFTER (M3) trees; the two JSONs must be byte/numeric
identical (max-abs-diff 0).
"""
import json
import os
import sys

import numpy as np

D = "~/Desktop/D24"
SESSIONS = {
    "single_GX010060": [f"{D}/GX010060.MP4"],
    "chaptered_GX0x0062": [f"{D}/GX010062.MP4", f"{D}/GX020062.MP4", f"{D}/GX030062.MP4"],
}


def dump_session(paths):
    from studio.session import Session
    s = Session.load(paths)
    out = {}
    # --- raw smoothed GPS trace arrays the whole app reads (local metres, time, speed) ---
    out["tx"] = s.tx.tolist()
    out["ty"] = s.ty.tolist()
    out["tt"] = s.tt.tolist()
    out["tv"] = s.tv.tolist()
    out["point_count"] = int(s.laps.point_count())
    # --- per-point GPS samples straight off the laps model (lat/lon/alt/speed/ts + time) ---
    pts = []
    for i in range(s.laps.point_count()):
        p = s.laps.get_point(i)
        g = p.point
        pts.append([g.lat, g.lon, g.altitude, g.full_speed, g.ground_speed,
                    g.timestamp_ms, g.dop, g.fix, p.time])
    out["gps_points"] = pts
    # --- lap segmentation outputs ---
    n = s.laps.laps_count()
    out["laps_count"] = int(n)
    out["lap_times"] = [s.laps.lap_time(i) for i in range(n)]
    out["lap_starts"] = [s.laps.start_timestamp(i) for i in range(n)]
    out["lap_dists"] = [s.laps.get_lap_distance(i) for i in range(n)]
    out["lap_entry_speed"] = [s.laps.lap_entry_speed(i) for i in range(n)]
    out["best_lap_id"] = s.best_lap_id()
    # --- start line + sectors geometry ---
    sl = s.laps.sectors.start_line
    out["start_line"] = [sl.first.x, sl.first.y, sl.second.x, sl.second.y]
    # --- IMU / g-meter series (the read_imu path + vehicle-frame g) ---
    gm = getattr(s, "_gmeter", None)
    if gm is not None and getattr(gm, "has_data", False):
        out["gmeter_len"] = int(len(gm))
        out["gmeter_source"] = gm.source
        # the full vehicle-frame g(t) series (the read_imu -> gmeter.compute output)
        for attr in ("times", "lat_g", "long_g"):
            v = getattr(gm, attr, None)
            if isinstance(v, np.ndarray):
                out[f"gmeter_{attr}"] = v.tolist()
    else:
        out["gmeter_len"] = 0
    return out


def main():
    result = {}
    for name, paths in SESSIONS.items():
        result[name] = dump_session(paths)
    outpath = sys.argv[1]
    with open(outpath, "w") as f:
        json.dump(result, f)
    print("WROTE", outpath, "sessions:", list(result.keys()),
          "pts:", {k: v["point_count"] for k, v in result.items()},
          "laps:", {k: v["laps_count"] for k, v in result.items()})


if __name__ == "__main__":
    main()
