"""Empirically test start-line position impact on segmentation + transponder residual.

Loads a recording ONCE (the expensive part), then re-segments the SAME pacer.Laps with several
candidate start lines and, for each, reports:
  - laps_count, valid lap count, double/missed-crossing diagnostics
  - duration-correlation lock vs the transponder CSV, and clean-racing residual mean/std/RMS
This isolates the effect of line POSITION while holding the timing axis (GPS9 true-clock) fixed.

Candidate lines are given as (lat,lon)-(lat,lon) pairs. We also include the SHIPPING fitted line
(from tracks.py via Session.load) as the baseline.

Usage: pixi run python .startline_tmp/sweep.py <recording.MP4> <csv> <race_start> <candidates.json>
where candidates.json = {"name": [[la1,lo1],[la2,lo2]], ...}
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import pacer
from studio import chapters, transponder
from studio.session import (
    Session, MIN_LAP_SAMPLES, MIN_LAP_TIME, LAP_BAND_LO, LAP_BAND_HI,
)
from studio._validate_wallclock import (
    cumulative_completion, footage_gps9_window, lap_being_driven, best_offset,
    residual_stats, RACING_MAX_S, DROPOUT_GAP_S,
)
import datetime as dt


def valid_lap_ids(laps):
    basic = [(i, laps.lap_time(i)) for i in range(laps.laps_count())
             if laps.sample_count(i) >= MIN_LAP_SAMPLES and laps.lap_time(i) >= MIN_LAP_TIME]
    if not basic:
        return []
    med = float(np.median([t for _, t in basic]))
    lo, hi = LAP_BAND_LO * med, LAP_BAND_HI * med
    return [i for i, t in basic if lo <= t <= hi]


def seg_line_from_latlon(cs, a, b):
    la = cs.local(pacer.GPSSample(lat=a[0], lon=a[1], altitude=0))
    lb = cs.local(pacer.GPSSample(lat=b[0], lon=b[1], altitude=0))
    seg = pacer.Segment()
    p1, p2 = pacer.Point(), pacer.Point()
    p1.x, p1.y = float(la[0]), float(la[1])
    p2.x, p2.y = float(lb[0]), float(lb[1])
    seg.first, seg.second = p1, p2
    return seg


def widen(seg, factor):
    mx = (seg.first.x + seg.second.x) / 2
    my = (seg.first.y + seg.second.y) / 2
    out = pacer.Segment()
    a, b = pacer.Point(), pacer.Point()
    a.x, a.y = mx + (seg.first.x - mx) * factor, my + (seg.first.y - my) * factor
    b.x, b.y = mx + (seg.second.x - mx) * factor, my + (seg.second.y - my) * factor
    out.first, out.second = a, b
    return out


def has_dropout(laps, lap_id):
    lap = laps.get_lap(lap_id)
    ts = np.array([p.time for p in lap.points])
    return bool(len(ts) > 1 and np.diff(ts).max() > DROPOUT_GAP_S)


def evaluate(laps, cs, label, csv_laps, completion, first_utc, race_start, n_widen_probe=True):
    valid = valid_lap_ids(laps)
    app = np.array([laps.lap_time(i) for i in valid])
    laps_count = laps.laps_count()
    if len(valid) < 5:
        print(f"  [{label}] laps_count={laps_count} valid={len(valid)} -- too few; skip")
        return None

    elapsed_start = (first_utc - race_start).total_seconds()
    drv = lap_being_driven(completion, elapsed_start)
    lo = max(min(csv_laps), drv - 14)
    hi = drv + 14
    start, corr, offsets = best_offset(app, csv_laps, lo, hi)
    if start is None:
        print(f"  [{label}] laps_count={laps_count} valid={len(valid)} -- NO LOCK")
        return None
    csv_ids = [start + k for k in range(len(valid))]
    csv_t = np.array([csv_laps[i] for i in csv_ids])
    r = app - csv_t
    m = (app <= RACING_MAX_S) & (csv_t <= RACING_MAX_S)
    dropout = np.array([has_dropout(laps, i) for i in valid])
    clean = m & ~dropout
    sc = residual_stats(r[clean]) if clean.sum() else {"mean": float("nan"), "std": float("nan"), "rms": float("nan"), "n": 0}
    sa = residual_stats(r[m]) if m.sum() else sc
    # second-best correlation (uniqueness margin)
    corrs = sorted([c for _, c, _ in offsets], reverse=True)
    margin = corrs[0] - corrs[1] if len(corrs) > 1 else float("nan")
    print(f"  [{label}] laps_count={laps_count} valid={len(valid)} "
          f"lock=CSV{csv_ids[0]}..{csv_ids[-1]} corr={corr:.4f} (margin={margin:+.3f}) | "
          f"clean(n={sc['n']}): mean={sc['mean']:+.4f} std={sc['std']:.4f} RMS={sc['rms']:.4f} | "
          f"racing(n={sa['n']}): std={sa['std']:.4f}")
    return {"label": label, "laps_count": laps_count, "valid": len(valid),
            "csv_range": [csv_ids[0], csv_ids[-1]], "corr": corr, "margin": margin,
            "clean": sc, "racing": sa}


def main(recording, csv_path, race_start_str, candidates):
    paths = chapters.discover_siblings(recording)
    print(f"=== {chapters.recording_label(paths)} ===", flush=True)
    race_start = dt.datetime.fromisoformat(race_start_str.replace("Z", "+00:00"))
    csv_laps = transponder.parse_csv(csv_path)
    completion = cumulative_completion(csv_laps)
    first_ms, last_ms, _ = footage_gps9_window(paths)
    first_utc = dt.datetime.fromtimestamp(first_ms / 1000.0, dt.UTC)

    sess = Session.load(paths)
    laps, cs = sess.laps, sess.cs

    results = []
    # baseline: the shipping fitted line (already on laps after Session.load)
    results.append(evaluate(laps, cs, "SHIPPING(fitted)", csv_laps, completion,
                            first_utc, race_start))

    for name, (a, b) in candidates.items():
        base = seg_line_from_latlon(cs, a, b)
        # exact line
        laps.sectors = pacer.Sectors(start_line=base, sector_lines=[])
        laps.update()
        results.append(evaluate(laps, cs, f"{name}(exact)", csv_laps, completion,
                                first_utc, race_start))
        # widened (like _fit_start_line would, x1.5) to catch missed passes
        laps.sectors = pacer.Sectors(start_line=widen(base, 1.5), sector_lines=[])
        laps.update()
        results.append(evaluate(laps, cs, f"{name}(x1.5)", csv_laps, completion,
                                first_utc, race_start))

    out = recording.split("/")[-1].replace(".MP4", "")
    with open(f".startline_tmp/sweep_{out}.json", "w") as f:
        json.dump([r for r in results if r], f, indent=2)
    print(f"wrote .startline_tmp/sweep_{out}.json", flush=True)


if __name__ == "__main__":
    cands = json.load(open(sys.argv[4]))
    main(sys.argv[1], sys.argv[2], sys.argv[3], cands)
