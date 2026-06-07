"""Re-derive the transponder-calibrated GPS9 clock-rate factor (`session.GPS9_RATE_FACTOR`).

Given a GoPro recording (its `--full` sibling chapters) and the kart's lap-timing transponder
CSV for the same stint, this:
  1. loads the app's valid laps on the UNCALIBRATED gps9 axis (rate_factor=1.0),
  2. parses + slices the transponder CSV stint (`studio/transponder.py`),
  3. ALIGNS the two sequences by a correlation-maximizing integer offset (the app drops some
     edge/pit laps the transponder keeps), anchoring on the mutual best lap,
  4. characterizes the residual (mean/median/std + a rate vs offset regression), and
  5. fits + 10-fold cross-validates a single global clock-RATE factor k (corrected = k·app).

The transponder CSV is a reference INPUT only — never committed. Re-run if the camera changes.

Run:  pixi run python -m studio._calib -- <recording.MP4> <transponder.csv> <lap_lo> <lap_hi>
e.g.  pixi run python -m studio._calib -- /path/GX010060.MP4 "/path/race.csv" 298 358
"""
from __future__ import annotations

import sys

import numpy as np

from studio import chapters, transponder
from studio.session import GPS9_RATE_FACTOR, Session


def _app_lap_times(paths):
    """The app's valid-lap times on the UNCALIBRATED gps9 axis, so the fit derives the ABSOLUTE
    clock-rate factor from scratch (not the residual on top of the committed one).

    `Session.load` applies the committed `GPS9_RATE_FACTOR` to the within-run spacing, and a lap
    inside one run scales linearly with that factor — so dividing each lap time by the current
    factor exactly recovers the uncalibrated (k=1) lap time. (Laps that straddle a run break are a
    tiny minority and the cross-validation / clean-lap mask absorb them.)"""
    sess = Session.load(paths)
    valid = sess.valid_lap_ids()
    raw = [sess.laps.lap_time(i) / GPS9_RATE_FACTOR for i in valid]
    return sess, valid, raw


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    recording, csv_path, lo, hi = argv[0], argv[1], int(argv[2]), int(argv[3])
    paths = chapters.discover_siblings(recording)
    print(f"recording: {len(paths)} chapter(s)")

    laps_csv = transponder.parse_csv(csv_path)
    stint = transponder.stint_times(laps_csv, lo, hi)
    csv_ids = [i for i, _ in stint]
    csv_t = np.array([t for _, t in stint])
    print(f"transponder stint {lo}..{hi}: n={len(stint)} best={csv_t.min():.3f} "
          f"@lap{csv_ids[int(np.argmin(csv_t))]}")

    sess, valid, app_t = _app_lap_times(paths)
    app = np.array(app_t)
    print(f"app valid laps: {len(app)}  best={app.min():.4f} @pos {int(np.argmin(app))}")

    # Align by the integer sequence offset that maximizes correlation.
    best = (-1e9, 0)
    for sh in range(-8, 9):
        idx = [(i, i + sh) for i in range(len(app)) if 0 <= i + sh < len(csv_t)]
        if len(idx) < max(20, len(app) // 2):
            continue
        a = np.array([app[i] for i, _ in idx])
        c = np.array([csv_t[j] for _, j in idx])
        corr = float(np.corrcoef(a, c)[0, 1])
        best = max(best, (corr, sh))
    corr, sh = best
    print(f"alignment: shift={sh:+d} corr={corr:.4f}")

    pairs = [(i, i + sh) for i in range(len(app)) if 0 <= i + sh < len(csv_t)]
    a = np.array([app[i] for i, _ in pairs])
    c = np.array([csv_t[j] for _, j in pairs])
    # Clean racing laps: drop edges + pit/traffic (>72 s).
    m = (a <= 72.0) & (c <= 72.0)
    a, c = a[m], c[m]
    r = a - c
    print(f"residual (clean n={len(r)}): mean={r.mean():+.4f} med={np.median(r):+.4f} "
          f"std={r.std():.4f}")
    A = np.vstack([c, np.ones_like(c)]).T
    (slope, icpt), *_ = np.linalg.lstsq(A, r, rcond=None)
    print(f"  regress resid~slope*csv+icpt: slope={slope:+.5f} icpt={icpt:+.4f} "
          f"(rate signature = strong + slope; noise ~0)")

    k = float(np.sum(a * c) / np.sum(a * a))
    print(f"\nGLOBAL RATE FIT: k={k:.6f} ((k-1)={(k-1)*1e6:+.0f} ppm); after: "
          f"mean={(k*a-c).mean():+.4f} std={(k*a-c).std():.4f}")
    rng = np.random.default_rng(7)
    order = rng.permutation(len(a))
    held = []
    for test in np.array_split(order, 10):
        tr = np.array([x for x in order if x not in set(test.tolist())])
        kf = np.sum(a[tr] * c[tr]) / np.sum(a[tr] ** 2)
        held.extend((kf * a[test] - c[test]).tolist())
    held = np.array(held)
    print(f"10-fold CV held-out: mean={held.mean():+.4f} std={held.std():.4f} "
          f"RMS={np.sqrt(np.mean(held**2)):.4f} (identity RMS={np.sqrt(np.mean((a-c)**2)):.4f})")
    print(f"\n=> set session.GPS9_RATE_FACTOR = {k:.6f}")
    return 0


if __name__ == "__main__":
    sys.exit(main([x for x in sys.argv[1:] if x != "--"]))
