"""Test the 'bad-GPS-period' hypothesis: does GPS quality (DOP / fix) NEAR the S/F crossing
predict the lap-time residual? If the high-residual laps have worse DOP at the crossing fixes,
the error is positional noise at the line (irreducible without better raw GPS), not a timing-axis
or gap-bridging problem. We read DOP from the RAW (pre-clean) samples around each crossing time.
"""
from __future__ import annotations
import json, sys
import numpy as np
from studio import chapters
from studio.session import Session, _read_gpmf, _gate_quality, _clean, _gps9_times

def main(rec, dumpfile):
    d = json.load(open(dumpfile))
    resid = {r["app_lap"]: r["r_def"] for r in d["per_lap"]}
    flag = {r["app_lap"]: r["flag"] for r in d["per_lap"]}
    paths = chapters.discover_siblings(rec)
    sess = Session.load(paths)
    valid = sess.valid_lap_ids()

    # Build a time->dop lookup from the cleaned samples (same pipeline the laps use).
    samples, spans, naive, _ = _read_gpmf(paths)
    samples, spans, naive = _gate_quality(samples, spans, naive)
    samples, spans, naive = _clean(samples, spans, naive)
    times = np.array(_gps9_times(samples, spans, naive))
    dop = np.array([getattr(s, "dop", np.nan) for s in samples], float)
    dop[dop <= 0] = np.nan

    rs, dops_sf, dops_lap = [], [], []
    for lid in valid:
        if flag.get(lid) == "pit/slow":
            continue
        lap = sess.laps.get_lap(lid)
        ts = np.array([p.time for p in lap.points])
        t_start, t_finish = ts[0], ts[-1]
        # DOP within +-1.5s of each crossing
        def dop_near(tc):
            sel = np.abs(times - tc) < 1.5
            return float(np.nanmean(dop[sel])) if sel.any() else np.nan
        dsf = np.nanmean([dop_near(t_start), dop_near(t_finish)])
        # DOP averaged over the whole lap
        sel = (times >= t_start) & (times <= t_finish)
        dlap = float(np.nanmean(dop[sel])) if sel.any() else np.nan
        rs.append(resid[lid]); dops_sf.append(dsf); dops_lap.append(dlap)
    rs = np.array(rs); dops_sf = np.array(dops_sf); dops_lap = np.array(dops_lap)
    ok = np.isfinite(rs) & np.isfinite(dops_sf)
    print(f"n={ok.sum()}  (laps with finite DOP)")
    if ok.sum() > 3:
        print(f"corr(|resid|, DOP near S/F) = {np.corrcoef(np.abs(rs[ok]), dops_sf[ok])[0,1]:+.3f}")
        oklap = np.isfinite(rs) & np.isfinite(dops_lap)
        print(f"corr(|resid|, DOP over lap) = {np.corrcoef(np.abs(rs[oklap]), dops_lap[oklap])[0,1]:+.3f}")
        med = np.nanmedian(dops_sf[ok])
        loo = np.abs(rs[ok])[dops_sf[ok] <= med]; hii = np.abs(rs[ok])[dops_sf[ok] > med]
        print(f"|resid| where DOP@SF <= median ({med:.2f}): mean={loo.mean():.4f} (n={len(loo)})")
        print(f"|resid| where DOP@SF >  median:            mean={hii.mean():.4f} (n={len(hii)})")
    print(f"DOP stats: min={np.nanmin(dops_lap):.2f} median={np.nanmedian(dops_lap):.2f} max={np.nanmax(dops_lap):.2f}")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
