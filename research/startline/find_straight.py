"""Identify the main straight on the GPS trace + characterize the current line position.

For a representative (best/median) lap, walk the lap in track order and report:
  - speed profile vs lap distance, so the long high-speed straight stands out
  - where the current start line sits along the lap (distance fraction, local position)
This grounds 'which straight is the main straight' against the plan (S/F on the main straight
by the pits, between the two hairpins corner 11 and corner 1).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import pacer
from studio import chapters
from studio.session import Session


def main(recording):
    paths = chapters.discover_siblings(recording)
    sess = Session.load(paths)
    cs = sess.cs
    best = sess.best_lap_id()
    xs, ys = sess.lap_trace_xy(best)
    xs, ys = np.array(xs), np.array(ys)
    lap = sess._get_lap(best)
    speeds = np.array([p.point.full_speed * 3.6 for p in lap.points])
    n = min(len(xs), len(speeds))
    xs, ys, speeds = xs[:n], ys[:n], speeds[:n]
    seg = np.hypot(np.diff(xs), np.diff(ys))
    dist = np.concatenate([[0], np.cumsum(seg)])
    total = dist[-1]
    print(f"best lap {best}: total={total:.1f}m  n={n}  vmax={speeds.max():.1f} "
          f"vmin={speeds.min():.1f} km/h")

    # Current line midpoint in local metres
    def to_local(la, lo):
        v = cs.local(pacer.GPSSample(lat=la, lon=lo, altitude=0))
        return float(v[0]), float(v[1])
    ca = to_local(52.04031, -0.78487)
    cb = to_local(52.04020, -0.78460)
    cmx, cmy = (ca[0] + cb[0]) / 2, (ca[1] + cb[1]) / 2
    j = int(np.argmin((xs - cmx) ** 2 + (ys - cmy) ** 2))
    print(f"current line midpoint local=({cmx:.1f},{cmy:.1f}); nearest lap pt idx={j} "
          f"dist_frac={dist[j]/total:.3f} speed_there={speeds[j]:.1f} km/h "
          f"pos=({xs[j]:.1f},{ys[j]:.1f})")

    # Speed profile sampled every ~5% of the lap, with local position, to locate the straights.
    print("\nfrac  dist_m   speed  local_x  local_y")
    for f in np.linspace(0, 1, 21):
        k = min(int(f * (n - 1)), n - 1)
        print(f"{f:.2f}  {dist[k]:6.1f}  {speeds[k]:5.1f}  {xs[k]:7.1f}  {ys[k]:7.1f}")

    # Report the longest sustained high-speed run (the main straight): contiguous samples > 60 km/h
    fast = speeds > 60
    runs = []
    i = 0
    while i < n:
        if fast[i]:
            jj = i
            while jj < n and fast[jj]:
                jj += 1
            runs.append((i, jj - 1, dist[jj - 1] - dist[i]))
            i = jj
        else:
            i += 1
    runs.sort(key=lambda r: -r[2])
    print("\nLongest >60km/h runs (start_idx,end_idx,length_m, mid_local):")
    for s, e, ln in runs[:4]:
        mid = (s + e) // 2
        print(f"  idx {s}..{e}  len={ln:.1f}m  midspeed={speeds[mid]:.1f}  "
              f"mid_local=({xs[mid]:.1f},{ys[mid]:.1f})  dist_frac={dist[mid]/total:.3f}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "~/Desktop/D24/GX010060.MP4")
