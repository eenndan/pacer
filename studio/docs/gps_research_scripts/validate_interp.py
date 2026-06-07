"""Compare the upstream Adam timestamp interpolation against the shipping GPS9 true-clock,
both timed against the SAME transponder CSV with the SAME duration-correlation alignment.

Reuses studio._validate_wallclock's pure helpers verbatim so the alignment is identical to the
shipping validator; the ONLY thing that changes is whether the Session is loaded with
interpolate=True (Adam fit) or False (GPS9 true-clock, the default).

Run:
  PYTHONPATH=. pixi run python studio/docs/gps_research_scripts/validate_interp.py <REC.MP4> <CSV> \
      --race-start "2026-05-23 12:00:00Z" [--dump out.json]

See studio/docs/upstream-20ms-investigation.md for the result (interp matches GPS9 on the clean
recording 0062, diverges on the noisier 0060 — never beats GPS9).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

import numpy as np

from studio import chapters, transponder
from studio._validate_wallclock import (
    RACING_MAX_S,
    _has_dropout,
    _parse_when,
    best_offset,
    cumulative_completion,
    footage_gps9_window,
    lap_being_driven,
    residual_stats,
)
from studio.session import Session


def time_laps(sess: Session):
    valid = sess.valid_lap_ids()
    app = np.array([sess.laps.lap_time(i) for i in valid])
    return valid, app


def align_and_score(label, valid, app, laps, completion, first_utc, race_start, dropout):
    elapsed_start = (first_utc - race_start).total_seconds()
    drv_start = lap_being_driven(completion, elapsed_start)
    lo = max(min(laps), drv_start - 12)
    hi = drv_start + 12
    start, corr, offsets = best_offset(app, laps, lo, hi)
    if start is None:
        print(f"[{label}] could not lock alignment")
        return None
    csv_ids = [start + k for k in range(len(valid))]
    csv_t = np.array([laps[i] for i in csv_ids])
    r = app - csv_t
    m = (app <= RACING_MAX_S) & (csv_t <= RACING_MAX_S)
    clean = m & ~dropout
    a, c = app[clean], csv_t[clean]
    k_fit = float(np.sum(a * c) / np.sum(a * a)) if len(a) else float("nan")
    out = {
        "label": label,
        "csv_lap_range": [csv_ids[0], csv_ids[-1]],
        "corr": corr,
        "k_fit": k_fit,
        "all": residual_stats(r),
        "racing": residual_stats(r[m]),
        "clean": residual_stats(r[clean]),
        "per_lap": [
            {"k": k, "app_lap": int(valid[k]), "csv_lap": csv_ids[k],
             "csv_s": float(csv_t[k]), "app_s": float(app[k]), "r": float(r[k]),
             "clean": bool(clean[k]), "dropout": bool(dropout[k])}
            for k in range(len(valid))
        ],
    }
    s = out["clean"]
    print(f"[{label}] csv {csv_ids[0]}..{csv_ids[-1]} corr={corr:.4f} k_fit={k_fit:.6f}  "
          f"CLEAN n={s['n']} mean={s['mean']:+.4f} median={s['median']:+.4f} "
          f"std={s['std']:.4f} rms={s['rms']:.4f}")
    sr = out["racing"]
    print(f"          RACING n={sr['n']} mean={sr['mean']:+.4f} median={sr['median']:+.4f} "
          f"std={sr['std']:.4f} rms={sr['rms']:.4f}")
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("recording")
    ap.add_argument("csv")
    ap.add_argument("--race-start", required=True)
    ap.add_argument("--dump", default=None)
    args = ap.parse_args([a for a in argv if a != "--"])
    race_start = _parse_when(args.race_start)

    paths = chapters.discover_siblings(args.recording)
    print(f"recording: {chapters.recording_label(paths)} ({len(paths)} chapter(s))")
    laps = transponder.parse_csv(args.csv)
    completion = cumulative_completion(laps)
    first_ms, last_ms, total_dur = footage_gps9_window(paths)
    first_utc = dt.datetime.fromtimestamp(first_ms / 1000.0, dt.UTC)

    results = {}
    # --- GPS9 true-clock (shipping default) ---
    sess_g = Session.load(paths)
    valid_g, app_g = time_laps(sess_g)
    drop_g = np.array([_has_dropout(sess_g, i) for i in valid_g])
    results["gps9"] = align_and_score("GPS9", valid_g, app_g, laps, completion,
                                      first_utc, race_start, drop_g)

    # --- Adam interpolation (opt-in --interp; rejected->naive if it diverges) ---
    sess_i = Session.load(paths, interpolate=True)
    valid_i, app_i = time_laps(sess_i)
    drop_i = np.array([_has_dropout(sess_i, i) for i in valid_i])
    results["interp"] = align_and_score("INTERP", valid_i, app_i, laps, completion,
                                        first_utc, race_start, drop_i)

    if args.dump:
        with open(args.dump, "w") as f:
            json.dump(results, f, indent=2)
        print("wrote", args.dump)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
