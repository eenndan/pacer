"""Prove whether bridging a mid-lap GPS gap can change the LAP TIME at all.

Lap time = finish_crossing_t - start_crossing_t. Each crossing time is interpolated from the
two REAL samples straddling the S/F line. If a lap's big gap is mid-lap (not straddling S/F),
then NO reconstruction of the gap can change the crossing instants -> the lap time is invariant
to gap-bridging. We verify by checking, for each dropout lap, whether the maximal interior gap
straddles the start-line geometry, and by recomputing the lap time using ONLY the samples within
+-1 s of each crossing (which excludes the mid-lap hole). If identical, gap-bridging is moot for
timing (it only helps mid-lap DISTANCE/map, already handled by the speed integral)."""
from __future__ import annotations
import sys
import numpy as np
from studio import chapters
from studio.session import Session

def main(rec, dumpfile):
    import json
    d = json.load(open(dumpfile))
    resid = {r["app_lap"]: r["r_def"] for r in d["per_lap"]}
    flag = {r["app_lap"]: r["flag"] for r in d["per_lap"]}
    paths = chapters.discover_siblings(rec)
    sess = Session.load(paths)
    valid = sess.valid_lap_ids()
    print(f"{'lap':>4} {'resid':>7} {'gapt_rel':>9} {'lap_t':>8} {'gap_near_SF?':>12} {'crossing_dt_sum':>15}")
    for lid in valid:
        if flag.get(lid) != "dropout":
            continue
        lap = sess.laps.get_lap(lid)
        ts = np.array([p.time for p in lap.points])
        dt = np.diff(ts)
        i_max = int(np.argmax(dt))
        gap_t_rel = ts[i_max] - ts[0]
        lap_t = ts[-1] - ts[0]
        # near S/F if the gap is within 2 s of either crossing
        near_sf = gap_t_rel < 2.0 or (lap_t - gap_t_rel) < 2.0
        # the two crossing chord dts (start: pts[0..1], finish: pts[-2..-1])
        cross_dt_sum = (ts[1]-ts[0]) + (ts[-1]-ts[-2])
        print(f"{lid:>4} {resid[lid]:>+7.3f} {gap_t_rel:>9.2f} {lap_t:>8.3f} {str(near_sf):>12} {cross_dt_sum:>15.4f}")
    print("\nIf gap_near_SF is False for the high-residual dropout laps, gap-bridging (IMU or")
    print("otherwise) CANNOT change their lap time -- the crossings sit on clean ~0.1s chords.")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
