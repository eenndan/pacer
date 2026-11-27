"""dropout_in_periods: should the GPS-dropout ⚠ for a gap INSIDE a lap (`DROPOUT_IN_LAP`, a kept-fix
gap over `gapfill.GAP_TIME_S` = 0.35 s) count fix PERIODS instead, so an 18 Hz recording is judged
like a 10 Hz one? Its verdict is `studio/docs/refused-2026-09.md` §43.

THE QUESTION. 0.35 s is three missing fixes at 10 Hz but six at 18 Hz, so on an 18 Hz recording a
hole of five fixes in a row (0.33 s) away from the start/finish line leaves its lap unflagged and in
the bests; §42 saw one move a hairpin split by up to 56 ms. What does a hole of N fixes do to the
figures of a lap the app trusts, at each rate, and from which N can one of them be wrong by more
than its published step?

THE MATRIX. The synthetic GoPro (`studio.dev.synth_gopro`, 10 laps, seeds 1-8) at 10 and 18 Hz (the
18 Hz stream is `sector_split_holes._rows_at`'s, GPS9-stamped) and GPS noise 1 and 1.9 (the reference
recording's), five sector lines as in §42, through the real loader with fixes reported 2D (the gate
rejects them). On every other lap a hole of N = 2..8 consecutive fixes is placed at the fastest point
of a straight, the hardest braking and the slowest point (the hairpin apex), each over 1 s from any
sector line and 1.5 s from the start/finish line, or starting just past the hairpin's sector line.
`run --noflag` switches the interior-gap rule off, so every holed lap stays in the bests at every N
and the matrix reads what a hole of that length does to a lap the app trusts. Per holed lap, against
the same lap on the clean trace: the sector splits, and with the clean session's corner list and best
lap pinned (a re-detected corner set would compare different corners), each corner's time and apex
speed and the brake onset (`DrivingChannels.lap_brake_points`). Each is reduced to its LAP-SPECIFIC
part: minus the median change of the unholed laps in the same column, which takes off the frame every
lap shares (§42). The added split σ is against the generator's true sector-line crossings; the map
chord's error is the true path's distance from the straight line across the hole. Cases scat1/2/5
reject 1/2/5 % of fixes at random: the cost side, each lap's longest interior gap in median periods
against today's flags.

    pixi run python -m studio.dev.probes.dropout_in_periods run --out /tmp/a.jsonl --noflag
    pixi run python -m studio.dev.probes.dropout_in_periods run --out /tmp/b.jsonl
    pixi run python -m studio.dev.probes.dropout_in_periods summary /tmp/a.jsonl
    pixi run python -m studio.dev.probes.dropout_in_periods summary /tmp/b.jsonl

`run` writes one JSON line per case to `--out` (a NEW file; it refuses an existing one) and makes its
recordings in a mkdtemp. Synthetic only: it reads no footage and writes nothing else.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from collections import defaultdict

import numpy as np

LOCS = ("straight", "braking", "apex", "sector")
NS = (2, 3, 4, 5, 6, 7, 8)
KS = (2.5, 3.5, 4.5, 5.5, 6.5)   # candidate rules: a gap longer than K median periods


def _figures(s, i, pinned: bool) -> dict:
    out = {"corner": {int(c.cid): [float(c.time), float(c.apex_speed)] for c in s.corners.lap_corner_stats(i)},
           "brake": {int(b.cid): float(b.actual_brake_dist) for b in s.driving.lap_brake_points(i)}}
    if not pinned:
        out.update(split=[float(x) for x in s.lap_sector_splits(i)], flag=bool(s.lap_has_dropout(i)))
    return out


def _pin(s, clean_basis, best_id) -> None:
    """Hold `s`'s corner list and best lap at the clean session's."""
    s.corners._best_lap_id = lambda: best_id
    s.corners.invalidate()
    s.corners._basis_cache = clean_basis
    s.driving.invalidate()


def _hole_masks(tau, v, acc, pxy, tc, tsec, n):
    """case -> (mask over the raw fixes, the true path's largest distance from the chord across a hole)."""
    allsec = np.sort(np.concatenate(tsec))
    px, py = pxy
    out = {}
    for loc in LOCS:
        for nn in NS:
            m, sag = np.zeros(n, bool), 0.0
            for j in range(1, len(tc) - 1, 2):
                inlap = np.flatnonzero((tau > tc[j] + 1.5) & (tau < tc[j + 1] - 1.5))
                if loc == "sector":
                    tl = tsec[2][(tsec[2] > tc[j]) & (tsec[2] < tc[j + 1])]
                    if not len(tl):
                        continue
                    a = int(np.searchsorted(tau, tl[0])) + 1
                else:
                    far = inlap[np.min(np.abs(tau[inlap][:, None] - allsec[None, :]), axis=1) > 1.0]
                    pool = inlap if loc == "apex" else far
                    c = pool[{"straight": np.argmax(v[pool]), "braking": np.argmin(acc[pool]),
                              "apex": np.argmin(v[pool])}[loc]]
                    a = c - nn // 2
                m[a:a + nn] = True
                dx, dy = px[a + nn] - px[a - 1], py[a + nn] - py[a - 1]
                d = np.abs((px[a:a + nn] - px[a - 1]) * dy - (py[a:a + nn] - py[a - 1]) * dx)
                sag = max(sag, float(d.max() / max(math.hypot(dx, dy), 1e-9)))
            out[f"{loc}_{nn}"] = (m, sag)
    return out


def run(out_path: str, seeds, noises, hzs, noflag: bool) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("PACER_NO_MEDIA", "1")
    os.environ.setdefault("PACER_APP_SUPPORT_JAIL", "1")
    import pacer
    from studio import gapfill, load
    from studio.dev import synth_gopro as sg
    from studio.dev.probes import sector_split_holes as ssh
    from studio.session import Session
    if os.path.exists(out_path):
        sys.exit(f"refusing to overwrite {out_path}: --out must be a new file")
    if noflag:   # the interior-gap rule off (`Session.lap_dropout_kinds` calls find_gaps with its default)
        gapfill.find_gaps.__defaults__ = (math.inf, None)
    orig = sg._gps_rows
    with open(out_path, "x") as fo:
        for hz in hzs:
            sg._gps_rows = ssh._rows_at(sg, hz)
            try:
                for noise in noises:
                    for seed in seeds:
                        rec = sg.generate(os.path.join(tempfile.mkdtemp(prefix="dropout_in_periods_"), "rec"),
                                          seed=seed, laps=ssh.LAPS, gps_noise=noise)
                        cached = load.read_recording(rec.paths)
                        truth, n = rec.truth, len(cached[0])
                        tau = ((np.asarray(cached[2], float) - truth.gps_lag_s)
                               / (1.0 + truth.media_ppm * 1e-6))
                        s_abs, v, acc = sg._kinematics(truth, tau)
                        px, py, _h, _k = truth.circuit.at(np.mod(s_abs, truth.circuit.length))
                        clean = ssh._load_with(pacer, load, Session, rec.paths, cached, np.zeros(n, bool))
                        line = clean.timing_lines_latlon()[0]
                        mid = np.mean(np.array(line), axis=0)
                        s0 = float(truth.s_at_latlon(np.array([mid[0]]), np.array([mid[1]]))[0])
                        length = truth.circuit.length
                        secs = [truth.line_at(float(np.mod(s0 + length * f, length))) for f in ssh.FRACS]
                        assert clean.apply_timing_lines_latlon(line, secs, confirmed=True)
                        tc = truth.crossings(line)
                        sts = [clean.laps.start_timestamp(i) for i in clean.valid_lap_ids()]
                        off = float(np.median([x - tc[np.argmin(np.abs(tc - x))] for x in sts]))
                        tsec = [truth.crossings(sl) for sl in secs]
                        cidx = ssh._lap_index(clean, tc, off)
                        cfig = {j: _figures(clean, i, False) for i, j in cidx.items()}
                        cbest, cbasis = cidx.get(int(clean.best_lap_id())), clean.corners.basis()
                        cases = _hole_masks(tau, v, acc, (px, py), tc, tsec, n)
                        for p in (1, 2, 5):
                            cases[f"scat{p}"] = (np.random.default_rng([seed, hz, p, 25]).random(n) < p / 100, None)
                        for name, (mask, sag) in cases.items():
                            s = ssh._load_with(pacer, load, Session, rec.paths, cached, mask)
                            assert s.apply_timing_lines_latlon(line, secs, confirmed=True)
                            idx = ssh._lap_index(s, tc, off)
                            period = s._missing_fix_steps()[2]
                            fig = {j: _figures(s, i, False) for i, j in idx.items()}
                            inv = {j: i for i, j in idx.items()}
                            pfig = {}
                            if cbest in inv:
                                _pin(s, cbasis, inv[cbest])
                                pfig = {j: _figures(s, i, True) for i, j in idx.items()}
                            laps = []
                            for i, j in idx.items():
                                t = np.asarray(s._lap_point_times(i), float)
                                tb = [tc[j], *(float(x[(x > tc[j]) & (x < tc[j + 1])][0]) for x in tsec), tc[j + 1]]
                                laps.append({"j": j, "gap": float(np.diff(t).max() / period), "truth": np.diff(tb).tolist(),
                                             "lap": fig[j], "pinned": pfig.get(j), "clean": cfig.get(j)})
                            fo.write(json.dumps({"seed": seed, "noise": noise, "hz": hz, "case": name,
                                                 "sagitta": sag, "laps": laps}) + "\n")
                        fo.flush()
                        print(f"hz {hz} noise {noise} seed {seed} done", flush=True)
            finally:
                sg._gps_rows = orig


def _lap_specific(row) -> dict:
    """tag -> per holed lap, the largest |Δ vs clean − the column's median Δ over the unholed laps|."""
    def deltas(lap):
        c, L, P = lap["clean"], lap["lap"], lap["pinned"]
        if c is None or not P:
            return {}
        out = {("sp", k): x - y for k, (x, y) in enumerate(zip(L["split"], c["split"], strict=False))
               if len(L["split"]) == len(c["split"])}
        for k in P["corner"]:
            if k in c["corner"]:
                out[("ct", k)] = P["corner"][k][0] - c["corner"][k][0]
                out[("ap", k)] = P["corner"][k][1] - c["corner"][k][1]
        out.update({("bp", k): P["brake"][k] - c["brake"][k] for k in P["brake"] if k in c["brake"]})
        return out
    cols = defaultdict(list)
    for lap in row["laps"]:
        if lap["j"] % 2 == 0 and not lap["lap"]["flag"]:
            for k, d in deltas(lap).items():
                cols[k].append(d)
    med = {k: float(np.median(v)) for k, v in cols.items()}
    out = defaultdict(list)
    for lap in row["laps"]:
        if lap["j"] % 2 == 1 and not lap["lap"]["flag"]:
            per = defaultdict(list)
            for (tag, k), d in deltas(lap).items():
                per[tag].append(abs(d - med.get((tag, k), 0.0)))
            for tag, v in per.items():
                out[tag].append(max(v))
    return out


def summary(paths) -> None:
    """Every figure §43 quotes: per (Hz, noise, place, N) over the holed laps the app trusts."""
    agg, scat = defaultdict(lambda: defaultdict(list)), defaultdict(list)
    for path in paths:
        with open(path) as f:
            for row in map(json.loads, f):
                if row["case"].startswith("scat"):
                    scat[(row["hz"], row["case"])] += [(lap["gap"], lap["lap"]["flag"]) for lap in row["laps"]]
                    continue
                loc, nn = row["case"].rsplit("_", 1)
                a = agg[(row["hz"], row["noise"], loc, int(nn))]
                a["sag"].append(row["sagitta"])
                for lap in row["laps"]:
                    if lap["j"] % 2 == 1:
                        a["flag"].append(lap["lap"]["flag"])
                        if not lap["lap"]["flag"] and len(lap["lap"]["split"]) == len(lap["truth"]):
                            tr = np.array(lap["truth"])
                            a["e1"] += (np.array(lap["lap"]["split"]) - tr).tolist()
                            a["e0"] += (np.array(lap["clean"]["split"]) - tr).tolist()
                for tag, v in _lap_specific(row).items():
                    a[tag] += v

    def mx(x, k=1.0):
        return "-" if not x else f"{np.max(x) * k:.1f}/{np.percentile(x, 95) * k:.1f}"
    print("hz noise place N | flagged today | chord error m | LAP-SPECIFIC max/p95 over unflagged holed laps:"
          " split ms, corner time ms, apex km/h, brake onset m | added split σ vs truth ms")
    for key in sorted(agg):
        a = agg[key]
        e1, e0 = np.array(a["e1"]), np.array(a["e0"])
        dv = float(np.mean(e1 ** 2) - np.mean(e0 ** 2)) if len(e1) else float("nan")
        print(f"{key[0]} {key[1]} {key[2]:8s} {key[3]} | {sum(a['flag'])}/{len(a['flag'])} | {max(a['sag']):.2f} |"
              f" {mx(a['sp'], 1000)} | {mx(a['ct'], 1000)} | {mx(a['ap'])} | {mx(a['bp'])} |"
              f" {math.copysign(math.sqrt(abs(dv)), dv) * 1000:+.1f}")
    print("\nscattered rejections: hz case | laps | flagged today | laps whose longest interior gap is over"
          f" K periods, K = {KS}")
    for key in sorted(scat):
        g = np.array([x[0] for x in scat[key]])
        print(key, len(g), sum(x[1] for x in scat[key]), [int((g > k).sum()) for k in KS])


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--out", required=True, help="a NEW .jsonl file to write")
    r.add_argument("--seeds", default="1,2,3,4,5,6,7,8")
    r.add_argument("--noise", default="1,1.9")
    r.add_argument("--hz", default="10,18")
    r.add_argument("--noflag", action="store_true", help="switch the interior-gap rule off")
    s = sub.add_parser("summary")
    s.add_argument("jsonl", nargs="+")
    args = ap.parse_args(argv)
    if args.cmd == "run":
        run(args.out, [int(x) for x in args.seeds.split(",")], [float(x) for x in args.noise.split(",")],
            [int(x) for x in args.hz.split(",")], args.noflag)
    else:
        summary(args.jsonl)


if __name__ == "__main__":
    main()
