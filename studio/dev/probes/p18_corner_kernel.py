"""p18_corner_kernel: should the corner model's curvature window be centred? (X4)

#383 centred the curvature kernel the GPS-lag estimator reads (`corners.lap_curvature(centred=True)`)
and left the corner model on the plain boxcar: `corners.pooled_curvature` smooths each lap's κ over
w = round(8 m / median spacing) fixes, and an EVEN w averages [i − w/2, i + w/2 − 1], half a fix
late. This probe asks whether centring it brings the corner model closer to the truth, and what it
would move on the owner's footage. The kernel is swapped only inside `pooled_curvature`, so nothing
else that reads `lap_curvature` changes. Every number it computes is printed.

  symmetric  a symmetric corner sampled at a UNIFORM spacing (w = 4, 5, 6, 7), where only the kernel
             can move anything: the |κ| centroid and a half-peak window's midpoint vs the true centre.
  synthetic  `studio/dev/synth_gopro.py`'s recording through the real `Session.load`, each kernel:
             the apex (|κ|-weighted centroid) and the window midpoint against the true arc midpoint,
             then per lap and corner the time, minimum, entry and exit speed against the kart's true
             motion over a TRUTH WINDOW (the model's own width, centred on the true corner, which is
             symmetric), and each corner's median loss to the best lap. `--controls` adds an odd
             window (the kernels are then identical), an even w = 6, a constant-speed kart and no
             load-time position boxcar. `--quick` runs two seeds at default noise.
  real       the four working-set recordings, jailed and tripwired: how far each boundary, corner
             time and speed, coaching row and the ideal lap would move.

Its verdict is `studio/docs/refused-2026-09.md` §17.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p18_corner_kernel symmetric
    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p18_corner_kernel synthetic
    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p18_corner_kernel real
"""
from __future__ import annotations

import contextlib
import functools
import math
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")

import numpy as np

from studio.dev._jail import divert_app_support

divert_app_support("p18_corner_kernel_")

import pacer  # noqa: E402  (after the jail, as every load below must be)
from studio import chapters, corners  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.session import Session  # noqa: E402

KERNELS = ("current", "centred")
RECORDINGS = (("Sandown 3h 2026", "GX010064.MP4"), ("SD_19_09_26", "GX010068.MP4"),
              ("SD_30_08_26", "GX010065.MP4"), ("MK_18_09_26", "GX010067.MP4"))


@contextlib.contextmanager
def kernel(name: str):
    """The corner model's pooled profile smoothed by `name`'s kernel, and nothing else."""
    if name == "current":
        yield
        return
    pooled = corners.pooled_curvature

    def centred_pooled(traces, total_ref):
        plain = corners.lap_curvature
        corners.lap_curvature = functools.partial(plain, centred=True)
        try:
            return pooled(traces, total_ref)
        finally:
            corners.lap_curvature = plain

    corners.pooled_curvature = centred_pooled
    try:
        yield
    finally:
        corners.pooled_curvature = pooled


def _rms(a) -> float:
    a = np.asarray(a, float)
    return float(np.sqrt(np.mean(a ** 2))) if len(a) else float("nan")


def _windows(s, ids) -> list[int]:
    out = []
    for i in ids:
        cum = s._lap_columns(i)[4]
        cum = cum[np.concatenate(([True], np.diff(cum) > 1e-9))]
        out.append(max(int(round(corners.KAPPA_SMOOTH_M / float(np.median(np.diff(cum))))), 1))
    return out


# ------------------------------------------------------------------------------------ symmetric
def symmetric() -> None:
    fine, radius, clo, straight = 0.01, 20.0, 10.0, 120.0
    arc = math.radians(90.0) * radius - clo
    s = np.arange(0.0, 2 * straight + 2 * clo + arc + 1e-9, fine)
    a, b, c, d = straight, straight + clo, straight + clo + arc, straight + 2 * clo + arc
    k = np.interp(s, [a, b, c, d], [0.0, 1 / radius, 1 / radius, 0.0], left=0.0, right=0.0)
    th = np.concatenate([[0.0], np.cumsum(0.5 * (k[1:] + k[:-1]) * fine)])
    x = np.concatenate([[0.0], np.cumsum(np.cos(0.5 * (th[1:] + th[:-1])) * fine)])
    y = np.concatenate([[0.0], np.cumsum(np.sin(0.5 * (th[1:] + th[:-1])) * fine)])
    centre = (a + d) / 2
    print("symmetric corner, uniform spacing (+ = late, metres along the lap)")
    for ds in (2.0, 1.6, 1.35, 1.1):
        si = np.arange(0.0, s[-1], ds) + 0.37 * ds
        xs, ys = np.interp(si, s, x), np.interp(si, s, y)
        cum = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(xs), np.diff(ys)))]) + si[0]
        w = int(round(corners.KAPPA_SMOOTH_M / float(np.median(np.diff(cum)))))
        row = []
        for centred in (False, True):
            m = np.abs(corners.lap_curvature(xs, ys, cum, centred=centred))
            half = 0.5 * m.max()
            up = np.flatnonzero(m >= half)
            e = np.interp(half, m[up[0] - 1:up[0] + 1], cum[up[0] - 1:up[0] + 1])
            x1 = np.interp(half, m[[up[-1] + 1, up[-1]]], cum[[up[-1] + 1, up[-1]]])
            row.append(f"centroid {np.sum(cum * m) / np.sum(m) - centre:+.3f} "
                       f"mid {(e + x1) / 2 - centre:+.3f}")
        print(f"  spacing {ds:4.2f} m  w={w} ({'even' if w % 2 == 0 else 'odd'}; half a fix "
              f"{ds / 2:.3f} m)  default: {row[0]}  centred: {row[1]}")


# ------------------------------------------------------------------------------------ synthetic
def _to_truth(s, truth, xs, ys):
    lat, lon = np.empty(len(xs)), np.empty(len(xs))
    for k, (x, y) in enumerate(zip(xs, ys, strict=True)):
        g = s.cs.global_(pacer.Vec3f(float(x), float(y), 0.0))
        lat[k], lon[k] = g.lat, g.lon
    return sg.to_local(lat, lon, truth.origin)


def _true_s(circuit, px, py):
    """Lap distance of the foot of each point on the true centreline."""
    cx, cy = circuit.x[:-1], circuit.y[:-1]
    out = np.empty(len(px))
    for k in range(len(px)):
        j = int(np.argmin((cx - px[k]) ** 2 + (cy - py[k]) ** 2))
        h = circuit.heading[j]
        out[k] = circuit.s[j] + (px[k] - cx[j]) * math.cos(h) + (py[k] - cy[j]) * math.sin(h)
    return np.mod(out, circuit.length)


def _synthetic_case(rec, name: str, smooth: int) -> dict:
    """One loaded recording under one kernel: placement and per-lap errors against the truth."""
    truth, c = rec.truth, rec.truth.circuit
    L = c.length

    def wrap(v):
        return (v + L / 2) % L - L / 2

    with kernel(name):
        s = Session.load(chapters.discover_siblings(rec.paths[0]), smooth_window=smooth)
        clist, _total = s.corners.basis()
        best = s.best_lap_id()
        _t, bx, by, _v, bcum = s._lap_columns(best)
        valid = s.valid_lap_ids()
        cand = set(s.consistency_lap_ids())
        cross = truth.crossings(s.timing_lines_latlon()[0])
        true_laps = np.diff(cross)
        off = min(range(-3, 4), key=lambda o: float(np.median(
            [abs(s.lap_time(i) - true_laps[i + o]) if 0 <= i + o < len(true_laps) else 1e9
             for i in valid])))
        d_cross = np.interp(cross, truth.t_nodes, truth.d_nodes)
        s_line = float(np.mod(truth.s_start + d_cross[0], L))

        def at(j, s_lap, col):
            return np.interp(d_cross[j] + np.mod(s_lap - s_line, L), truth.d_nodes, col)

        apex_true = {k.cid: k.apex_s for k in c.corners}
        out = {"windows": _windows(s, s.corners._clean_lap_ids()), "apex": [], "mid": [],
               "cells": [], "loss": []}
        for cn in clist:
            px, py = _to_truth(s, truth, np.interp([cn.enter, cn.apex, cn.exit], bcum, bx),
                               np.interp([cn.enter, cn.apex, cn.exit], bcum, by))
            se, sa, sx = _true_s(c, np.asarray(px), np.asarray(py))
            at_true = min(apex_true.values(), key=functools.partial(_gap, sa, L))
            width = float(np.mod(sx - se, L))
            out["apex"].append(float(wrap(sa - at_true)))
            out["mid"].append(float(wrap(se + width / 2 - at_true)))
            t0, t1 = at_true - width / 2, at_true + width / 2
            per_lap = {}
            for i in valid:
                j = i + off
                st = s.corners.lap_corner_stats(i)
                if not 0 <= j < len(true_laps) or len(st) != len(clist):
                    continue
                cs = st[cn.cid - 1]
                d0, d1 = (d_cross[j] + np.mod(q - s_line, L) for q in (t0, t1))
                inside = (truth.d_nodes >= d0) & (truth.d_nodes <= d1)
                t_true = float(at(j, t1, truth.t_nodes) - at(j, t0, truth.t_nodes))
                per_lap[i] = (cs.time, t_true)
                out["cells"].append((cs.time - t_true,
                                     cs.apex_speed - float(truth.v_nodes[inside].min()) * 3.6,
                                     cs.entry_speed - float(at(j, t0, truth.v_nodes)) * 3.6,
                                     cs.exit_speed - float(at(j, t1, truth.v_nodes)) * 3.6))
            if best in per_lap:
                others = [i for i in per_lap if i in cand and i != best]
                lm = np.median([per_lap[i][0] - per_lap[best][0] for i in others])
                lt = np.median([per_lap[i][1] - per_lap[best][1] for i in others])
                out["loss"].append((float(lm), float(lt)))
    return out


def _gap(s_model: float, length: float, s_true: float) -> float:
    return abs((s_model - s_true + length / 2) % length - length / 2)


def _kendall(a, b) -> float:
    n = len(a)
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    return float(np.mean([np.sign(a[i] - a[j]) * np.sign(b[i] - b[j]) for i, j in pairs]))


def _report(label: str, runs: list[dict]) -> None:
    """One line per kernel over `runs` (one dict per recording, per kernel)."""
    ws = [w for r in runs for w in r["current"]["windows"]]
    print(f"  {label}: {len(runs)} recordings; curvature windows {sorted(set(ws))}, "
          f"{sum(w % 2 == 0 for w in ws)}/{len(ws)} clean laps even")
    for name in KERNELS:
        apex = [v for r in runs for v in r[name]["apex"]]
        mid = [v for r in runs for v in r[name]["mid"]]
        case_mid = [float(np.mean(r[name]["mid"])) for r in runs]
        t, vmin, ent, ext = np.array([c for r in runs for c in r[name]["cells"]]).T
        lm, lt = np.array([q for r in runs for q in r[name]["loss"]]).T
        tau = np.mean([_kendall(*np.array(r[name]["loss"]).T) for r in runs])
        print(f"    {name:>8}: apex {np.mean(apex):+.2f} m (rms {_rms(apex):.2f}) | window midpoint "
              f"{np.mean(mid):+.2f} m (rms {_rms(mid):.2f}; per recording {min(case_mid):+.2f} … "
              f"{max(case_mid):+.2f}) | corner time {np.mean(t) * 1e3:+.1f} ms (rms "
              f"{_rms(t) * 1e3:.1f}) | min / entry / exit speed rms {_rms(vmin):.2f} / "
              f"{_rms(ent):.2f} / {_rms(ext):.2f} km/h | loss to best rms {_rms(lm - lt) * 1e3:.1f} ms,"
              f" ranking tau {tau:.2f} (n = {len(t)} cells)")


def _synthetic_set(label: str, seeds, noises, smooth: int = 13, vscale: float = 1.0,
                   const_v: float = 0.0) -> None:
    keep = {k: getattr(sg, k) for k in ("V_TOP", "LAT_G", "BRAKE_G", "ACCEL0")}
    runs: dict[float, list[dict]] = {noise: [] for noise in noises}
    print(f"{label}: seeds {list(seeds)}, GPS noise {list(noises)}, both directions")
    for seed in seeds:
        for noise in noises:
            for mirror in (False, True):
                try:
                    if const_v:       # constant speed: no braking/acceleration asymmetry anywhere
                        sg.V_TOP, sg.LAT_G, sg.ACCEL0 = const_v, 1000.0, 20.0
                    sg.V_TOP *= vscale
                    for k in ("LAT_G", "BRAKE_G", "ACCEL0"):
                        setattr(sg, k, getattr(sg, k) * vscale ** 2)
                    with tempfile.TemporaryDirectory(prefix="p18_corner_kernel_") as tmp:
                        rec = sg.generate(os.path.join(tmp, "rec"), seed=seed, chapters=1,
                                          gps_noise=noise, mirror=mirror)
                        for k, v in keep.items():
                            setattr(sg, k, v)
                        runs[noise].append({name: _synthetic_case(rec, name, smooth)
                                            for name in KERNELS})
                finally:
                    for k, v in keep.items():
                        setattr(sg, k, v)
    for noise in noises:
        _report(f"GPS noise {noise:g}", runs[noise])
    if len(noises) > 1:
        _report("pooled", [r for noise in noises for r in runs[noise]])


def synthetic(quick: bool, controls: bool) -> None:
    seeds = (20260924, 7) if quick else (20260924, 7, 11, 101, 2024)
    _synthetic_set("realistic kart", seeds, (1.0,) if quick else (0.0, 1.0, 2.0))
    if controls:
        _synthetic_set("CONTROL odd window (x0.8 speed)", seeds[:2], (0.0,), vscale=0.8)
        _synthetic_set("even w = 6 (x0.67 speed)", seeds[:2], (0.0, 1.0), vscale=0.67)
        _synthetic_set("CONTROL constant 20 m/s", seeds[:2], (0.0,), const_v=20.0)
        _synthetic_set("no load-time position boxcar", seeds[:2], (0.0,), smooth=1)


# ------------------------------------------------------------------------------------ real
def _real_case(paths, name: str) -> dict:
    with kernel(name):
        s = Session.load(paths)
        clist, _total = s.corners.basis()
        best = s.best_lap_id()
        bt, _x, _y, _v, bcum = s._lap_columns(best)
        return {"windows": _windows(s, s.corners._clean_lap_ids()),
                "edges": np.array([(c.enter, c.apex, c.exit) for c in clist]),
                "t_edges": np.array([np.interp((c.enter, c.exit), bcum, bt) for c in clist]),
                "cells": {i: np.array([(c.time, c.apex_speed, c.entry_speed, c.exit_speed)
                                       for c in s.corners.lap_corner_stats(i)])
                          for i in s.valid_lap_ids()},
                "coach": [(r.cid, r.time_lost) for r in s.coaching_opportunities().rows],
                "ideal": s.ideal_total()}


def real() -> None:
    for folder, first in RECORDINGS:
        root = os.path.join(os.path.expanduser("~/Desktop"), folder)
        if not os.path.exists(os.path.join(root, first)):
            print(f"{folder}: SKIPPED, not on this machine")
            continue

        def snap(root=root):
            return {n: (st.st_size, st.st_mtime_ns) for n in sorted(os.listdir(root))
                    for st in [os.stat(os.path.join(root, n))]}

        before = snap()
        paths = chapters.discover_siblings(os.path.join(root, first))
        a, b = (_real_case(paths, name) for name in KERNELS)
        assert snap() == before, f"{folder}: a file under it changed"
        ws = a["windows"]
        de = b["edges"] - a["edges"]
        dt = (b["t_edges"] - a["t_edges"]) * 1e3
        cells = np.concatenate([b["cells"][i] - a["cells"][i] for i in a["cells"]
                                if len(a["cells"][i]) == len(b["cells"].get(i, ()))])
        print(f"{folder}: {sum(w % 2 == 0 for w in ws)}/{len(ws)} clean laps on an even window; "
              f"{len(de)} corners, files unchanged")
        print(f"  apex {de[:, 1].mean():+.2f} m (range {de[:, 1].min():+.2f} … {de[:, 1].max():+.2f}); "
              f"boundaries moved {int(np.sum(np.abs(de[:, [0, 2]]) > 1e-9))}/{2 * len(de)}, by "
              f"{np.abs(de[:, [0, 2]]).max():.2f} m at most, {np.abs(dt).max():.1f} ms on the best lap")
        for j, (what, unit, sc) in enumerate((("corner time", "ms", 1e3), ("min speed", "km/h", 1),
                                              ("entry speed", "km/h", 1), ("exit speed", "km/h", 1))):
            v = cells[:, j] * sc
            print(f"  {what}: {np.mean(np.abs(v) < 1e-9) * 100:.0f} % of {len(v)} cells unchanged, "
                  f"median {np.median(v):+.2f}, max |Δ| {np.abs(v).max():.2f} {unit}")
        la, lb = dict(a["coach"]), dict(b["coach"])
        print(f"  coaching order {[q for q, _ in a['coach']]} -> {[q for q, _ in b['coach']]}; "
              f"time lost max |Δ| {max(abs(lb[q] - la[q]) for q in la if q in lb) * 1e3:.1f} ms; "
              f"ideal {(b['ideal'] - a['ideal']) * 1e3:+.2f} ms")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "symmetric"
    if what == "symmetric":
        symmetric()
    elif what == "synthetic":
        synthetic("--quick" in sys.argv, "--controls" in sys.argv)
    elif what == "real":
        real()
    else:
        sys.exit(__doc__)
