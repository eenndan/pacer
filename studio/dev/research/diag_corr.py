"""Decisive analysis: what actually drives the lap-time residual vs the transponder?

Hypotheses:
  H1 (brief): a GPS dropout (large interior gap) near the S/F crossing inflates the residual.
  H2: the residual is driven by the S/F crossing CHORD time-spacing (the dt between the two
      real samples straddling the S/F line) — a longer chord = a worse constant-velocity
      interpolation of the crossing instant. Both this lap's start crossing and finish crossing.
  H3: it's just 10 Hz quantization noise, uncorrelated with anything.

We compute, per validated lap, several candidate predictors and correlate |residual| with them.
The S/F finish crossing of lap k is the start crossing of lap k+1, so we look at both."""
from __future__ import annotations
import json, sys
import numpy as np
from studio import chapters, transponder
from studio.session import Session
import studio.dev._validate_wallclock as vw
import datetime as dt

def sf_chord_dt(sess, lid):
    """Time-spacing of the two real samples straddling this lap's START crossing, plus the
    finish crossing. lap.points = [interp_start, real..., interp_finish]. The START crossing
    chord is between the LAST real sample of the previous lap and the FIRST of this lap; we
    approximate by the gap at the very start (points[1].time - points[0].time gives the
    post-crossing half) and the finish by points[-1]-points[-2]."""
    lap = sess.laps.get_lap(lid)
    ts = np.array([p.time for p in lap.points])
    start_dt = float(ts[1]-ts[0]) if len(ts)>1 else 0.0
    finish_dt = float(ts[-1]-ts[-2]) if len(ts)>1 else 0.0
    return start_dt, finish_dt, float(np.diff(ts).max())

def main(rec, dumpfile):
    d = json.load(open(dumpfile))
    paths = chapters.discover_siblings(rec)
    sess = Session.load(paths)
    valid = sess.valid_lap_ids()
    rows = d["per_lap"]
    # map app_lap -> residual
    resid = {r["app_lap"]: r["r_def"] for r in rows}
    csvlap = {r["app_lap"]: r["csv_lap"] for r in rows}
    flag = {r["app_lap"]: r["flag"] for r in rows}

    rec_r, rec_maxgap, rec_sfdt, rec_sumdt = [], [], [], []
    print(f"{'lap':>4} {'csv':>4} {'resid':>7} {'startdt':>8} {'finishdt':>9} {'maxgap':>7} {'flag':>9}")
    for lid in valid:
        if lid not in resid: continue
        sdt, fdt, mg = sf_chord_dt(sess, lid)
        r = resid[lid]
        # only clean racing laps for the correlation (exclude pit/slow)
        if flag[lid] == "pit/slow":
            continue
        rec_r.append(r); rec_maxgap.append(mg); rec_sfdt.append(sdt+fdt); rec_sumdt.append(sdt+fdt)
        mark = "*" if abs(r) > 0.15 else ""
        print(f"{lid:>4} {csvlap[lid]:>4} {r:>+7.3f} {sdt:>8.3f} {fdt:>9.3f} {mg:>7.3f} {flag[lid]:>9} {mark}")
    rec_r = np.array(rec_r); rec_maxgap = np.array(rec_maxgap); rec_sfdt = np.array(rec_sfdt)
    print(f"\nn={len(rec_r)} racing+dropout laps")
    print(f"corr(|resid|, maxgap)         = {np.corrcoef(np.abs(rec_r), rec_maxgap)[0,1]:+.3f}")
    print(f"corr(|resid|, start+finish dt)= {np.corrcoef(np.abs(rec_r), rec_sfdt)[0,1]:+.3f}")
    # Does a longer S/F chord predict a bigger error?  Split by median sf dt.
    med = np.median(rec_sfdt)
    lo = np.abs(rec_r)[rec_sfdt <= med]; hi = np.abs(rec_r)[rec_sfdt > med]
    print(f"|resid| where S/F chord dt <= median ({med:.3f}s): mean={lo.mean():.4f} (n={len(lo)})")
    print(f"|resid| where S/F chord dt >  median:               mean={hi.mean():.4f} (n={len(hi)})")
    # dropout vs not
    has_drop = rec_maxgap > 0.35
    print(f"|resid| dropout laps:    mean={np.abs(rec_r)[has_drop].mean():.4f} std={rec_r[has_drop].std():.4f} (n={int(has_drop.sum())})")
    print(f"|resid| no-dropout laps: mean={np.abs(rec_r)[~has_drop].mean():.4f} std={rec_r[~has_drop].std():.4f} (n={int((~has_drop).sum())})")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
