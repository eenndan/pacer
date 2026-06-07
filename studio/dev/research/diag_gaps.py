"""Characterize the GPS-gap structure of each validated lap and how the gaps relate to the
start/finish crossing — to know whether dead-reckoning across a gap could fix the timing.

For each valid lap we report: the largest interior time gap, where it sits relative to the
lap's start crossing (which is the S/F crossing that defines the lap time), and whether a gap
straddles the S/F line of THIS lap (start) or the NEXT lap. The lap TIME error from a gap comes
from a gap straddling the crossing chord: when the two samples bracketing the S/F line are far
apart in time, the interpolated crossing time is only as good as the constant-velocity assumption
over that long chord."""
from __future__ import annotations
import sys
import numpy as np
from studio import chapters
from studio.session import Session

def main(rec):
    paths = chapters.discover_siblings(rec)
    sess = Session.load(paths)
    valid = sess.valid_lap_ids()
    print(f"{rec}: {len(valid)} valid laps")
    print(f"{'lap':>4} {'time':>7} {'maxgap':>7} {'gap@t-rel':>10} {'n_gaps':>6} {'sf_chord_dt':>11} {'sf_chord_m':>10}")
    for lid in valid:
        lap = sess.laps.get_lap(lid)
        pts = lap.points
        ts = np.array([p.time for p in pts])
        dt = np.diff(ts)
        t0 = ts[0]
        lap_time = ts[-1] - ts[0]
        # interior gaps (exclude the open ends which are interpolated crossings)
        interior = dt[1:-1] if len(dt) > 2 else dt
        maxgap = float(dt.max()) if len(dt) else 0.0
        i_max = int(np.argmax(dt)) if len(dt) else 0
        gap_rel = (ts[i_max] - t0)  # seconds into lap where the big gap starts
        n_gaps = int((dt > 0.35).sum())
        # The S/F start crossing: it's interpolated between points[0] (the crossing) and the
        # first real sample. The chord that defines THIS lap's START crossing is actually the
        # segment in the PREVIOUS lap. But the lap.points[0] is the interpolated crossing and
        # points[1] the first real sample; the dt there is the post-crossing step.
        sf_chord_dt = float(ts[1] - ts[0]) if len(ts) > 1 else 0.0
        # chord length of the first segment (start crossing -> first sample) in metres
        xs, ys = [], []
        for p in pts[:2]:
            v = sess.cs.local(p.point); xs.append(v[0]); ys.append(v[1])
        sf_chord_m = float(np.hypot(xs[1]-xs[0], ys[1]-ys[0])) if len(xs) > 1 else 0.0
        flag = "DROPOUT" if maxgap > 0.35 else ""
        print(f"{lid:>4} {lap_time:>7.3f} {maxgap:>7.3f} {gap_rel:>10.2f} {n_gaps:>6} {sf_chord_dt:>11.3f} {sf_chord_m:>10.2f} {flag}")

if __name__ == "__main__":
    main(sys.argv[1])
