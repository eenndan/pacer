"""Headless diagnostics for a telemetry file — root-cause studio issues without the GUI.

Reports raw sample stats, GPS-noise (consecutive-step jumps), the time axis (naive vs
interpolated, monotonicity), and lap segmentation (count + lap-time distribution) so we can
see WHY lap times / map sync / plots look wrong.

Run:  pixi run python -m studio.diagnose -- /path/to/file.MP4 [--interp]
"""

from __future__ import annotations

import sys
import time

import numpy as np

import pacer

from .session import DEFAULT_SAMPLE, _read_gpmf


def pct(a, ps):
    a = np.asarray(a, float)
    if a.size == 0:
        return "n/a"
    return {p: round(float(np.percentile(a, p)), 3) for p in ps}


def local_xy(cs, samples):
    out = np.empty((len(samples), 2))
    for i, s in enumerate(samples):
        v = cs.local(s)
        out[i, 0], out[i, 1] = v[0], v[1]
    return out


def clean(samples, spans, naive, cs, max_step=50.0, min_start_speed=3.0):
    """Trim the stationary lead-in, then drop lone GPS spikes: a point that is far from
    BOTH neighbours while the neighbours are close to each other (a teleport-and-return).
    Avoids the anchoring cascade of comparing to a single 'last good' point."""
    start = next((k for k, s in enumerate(samples) if s.full_speed > min_start_speed), 0)
    s = samples[start:]
    sp = spans[start:]
    t = naive[start:]
    xy = local_xy(cs, s)

    def d(i, j):
        return float(np.hypot(xy[i, 0] - xy[j, 0], xy[i, 1] - xy[j, 1]))

    keep = [True] * len(s)
    dropped = 0
    for i in range(1, len(s) - 1):
        if d(i, i - 1) > max_step and d(i, i + 1) > max_step and d(i - 1, i + 1) < max_step:
            keep[i] = False
            dropped += 1
    out_s = [s[i] for i in range(len(s)) if keep[i]]
    out_sp = [sp[i] for i in range(len(s)) if keep[i]]
    out_t = [t[i] for i in range(len(s)) if keep[i]]
    print(f"  clean: trimmed {start} lead-in, dropped {dropped} spikes -> {len(out_s)} samples")
    return out_s, out_sp, out_t


def widen(seg, factor):
    """Scale a Segment about its midpoint (longer start/sector line)."""
    mx, my = (seg.first.x + seg.second.x) / 2, (seg.first.y + seg.second.y) / 2
    new = pacer.Segment()
    a, b = pacer.Point(), pacer.Point()
    a.x = mx + (seg.first.x - mx) * factor
    a.y = my + (seg.first.y - my) * factor
    b.x = mx + (seg.second.x - mx) * factor
    b.y = my + (seg.second.y - my) * factor
    new.first, new.second = a, b
    return new


def build_laps(samples, times, label, widen_start=1.0):
    laps = pacer.Laps()
    for s, t in zip(samples, times):
        laps.add_point(s, float(t))
    mn, mx = laps.min_max()
    cs = pacer.CoordinateSystem(
        pacer.GPSSample(lat=(mn.y + mx.y) / 2, lon=(mn.x + mx.x) / 2, altitude=0)
    )
    laps.set_coordinate_system(cs)
    start_line = laps.pick_random_start()
    if widen_start != 1.0:
        start_line = widen(start_line, widen_start)
    laps.sectors = pacer.Sectors(start_line=start_line, sector_lines=[])
    laps.update()
    n = laps.laps_count()
    lts = np.array([laps.lap_time(i) for i in range(n)])
    print(f"[{label}] laps={n}")
    if n:
        good = int(((lts > 40) & (lts < 120)).sum())
        print(f"    lap-time pctiles(s): {pct(lts, [0, 25, 50, 75, 100])}")
        print(f"    in 40-120s window: {good}/{n}  | min={lts.min():.2f} max={lts.max():.2f}")
        print(f"    n samples/lap pctiles: {pct([laps.sample_count(i) for i in range(n)], [0, 50, 100])}")
    return laps


def main():
    args = sys.argv[1:]
    do_interp = "--interp" in args
    paths = [a for a in args if not a.startswith("-")] or [DEFAULT_SAMPLE]
    print("file:", paths)

    t0 = time.time()
    samples, spans, naive = _read_gpmf(paths)
    print(f"GPMF parse: {time.time() - t0:.1f}s, {len(samples)} raw samples")
    if not samples:
        return 1

    spd = np.array([s.full_speed for s in samples])
    naive = np.array(naive)
    print("\n--- TIME AXIS ---")
    print(f"span range: {spans[0][0]:.2f} .. {spans[-1][1]:.2f}s  (= {spans[-1][1] - spans[0][0]:.1f}s)")
    print(f"naive range: {naive.min():.2f} .. {naive.max():.2f}  monotonic={bool(np.all(np.diff(naive) >= 0))}")
    print(f"unique spans: {len({s for s in spans})}  (≈ payload chunks)")

    print("\n--- SPEED (m/s) ---")
    print("pctiles:", pct(spd, [0, 5, 25, 50, 75, 95, 100]))
    for thr in (1e-6, 0.5, 3.0, 5.0):
        print(f"  full_speed > {thr}: {int((spd > thr).sum())}/{len(spd)}")

    print("\n--- GPS NOISE (consecutive-step meters) ---")
    cs = pacer.CoordinateSystem(samples[len(samples) // 2])
    loc = local_xy(cs, samples)
    steps = np.hypot(np.diff(loc[:, 0]), np.diff(loc[:, 1]))
    print("step pctiles:", pct(steps, [50, 90, 99, 100]))
    print(f"  steps >50m: {int((steps > 50).sum())}  >200m: {int((steps > 200).sum())}  "
          f">1000m: {int((steps > 1000).sum())}")
    big = np.where(steps > 50)[0]
    print("  first big-jump sample indices:", big[:12].tolist())
    print("  bbox extent (m): x", round(float(np.ptp(loc[:, 0])), 1), "y", round(float(np.ptp(loc[:, 1])), 1))
    # leading noise: speed of first 30 samples
    print("  first 20 speeds (m/s):", [round(float(x), 1) for x in spd[:20]])

    print("\n--- SEGMENTATION ---")
    build_laps(samples, naive, "naive timing, all samples")

    if "--clean" in args:
        print("\n--- CLEANED (trim lead-in + drop outliers) ---")
        cs_mid = pacer.CoordinateSystem(samples[len(samples) // 2])
        cs_, csp_, cn_ = clean(samples, spans, naive, cs_mid)
        build_laps(cs_, cn_, "cleaned, naive timing, 5m start")
        build_laps(cs_, cn_, "cleaned, naive timing, 3x start", widen_start=3.0)

    if do_interp:
        print("\n--- INTERPOLATION ---")
        keep = [i for i, s in enumerate(samples) if s.full_speed > 1e-6]
        ss = [samples[i] for i in keep]
        sp = [spans[i] for i in keep]
        t1 = time.time()
        res = pacer.interpolate_timestamps(ss, sp, pacer.CoordinateSystem(ss[0]))
        it = np.array(res.timestamps)
        print(f"interp: {time.time() - t1:.1f}s  freq={res.frequency:.4f} loss={res.loss:.5g}")
        print(f"  result monotonic={bool(np.all(np.diff(it) >= 0))}  range={it.min():.1f}..{it.max():.1f}")
        d = np.diff(it)
        print(f"  inter-sample dt pctiles(s): {pct(d, [0, 5, 50, 95, 100])}")
        build_laps(ss, it, "interp timing, moving samples")
    return 0


if __name__ == "__main__":
    sys.exit(main())
