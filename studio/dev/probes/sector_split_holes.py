"""sector_split_holes: how far does a missing GPS fix move a SECTOR SPLIT whose lap it leaves
unflagged, why, and does any change fit the constraints? Its verdict is
`studio/docs/refused-2026-09.md` §42.

THE QUESTION. After 0.26.0 a missing fix moves a lap time by at most 4.5 ms at native noise, but a
split beside a hole was reported to move by up to ~45 ms with nothing flagged, blamed on the
splits being mapped by DISTANCE. A split is not: `Session._project_sector_lines` finds the chord
where the (de-drifted) trace crosses the sector line, and `lap_sector_splits` reads the time on
that same chord (odometer and time are both linear along it), so a split IS the geometric
crossing, timed as the lap's own crossings are. What a hole can move is (a) the smoothed trace at
the line (the time-aware fit, `_signal._smooth_timed`, inside its ±6.5-period reach) and (b) the
de-drift frame (`CornerModel.sector_shift`): its consensus line is a median over the clean laps,
measured from the BEST lap's trace, so a hole that flags a lap or changes the best lap moves it.

THE MATRIX. The synthetic GoPro (`studio.dev.synth_gopro`, 10 laps, seeds 1-5) at 10 and 18 Hz and
GPS noise 1, 1.9 (the reference recording's, `library.REFERENCE_RECORDING`) and 3, through the real
loader with fixes reported 2D (the quality gate rejects them: a missing fix), five sector lines at
0.20 / 0.37 / 0.55 / 0.69 / 0.85 of the lap (the third at the hairpin, ~13 m/s). Cases, on every
other lap so the laps between measure the knock-on: one or two fixes missing 1-10 periods before or
after each sector crossing; 1 / 2 / 5 % rejected at random; holes of 0.3 / 0.5 / 1.0 s 0.1-0.5 s
from a sector line; 0.3-1.0 s holes mid-sector; and three holes on the clean BEST lap (two fixes at
its finish line, 0.6 s mid-lap, one fix beside each sector line). Each unflagged lap's splits are
read against the generator's TRUE sector-line crossings and against the same lap on the clean
trace, as shipped (M0) and re-timed with the frame pinned: each lap's sector shift held at its
clean-trace value (Mshift: what is left is the trace at the line), and the corner model's best lap
(Mref) and also its consensus lap set (Mrefc) held at the clean session's.

    pixi run python -m studio.dev.probes.sector_split_holes run --out /tmp/x.jsonl --hz 10 --noise 1.9
    pixi run python -m studio.dev.probes.sector_split_holes summary /tmp/x.jsonl [...]

`run` writes one JSON line per case to `--out` (a NEW file; it refuses an existing one) and makes
its recordings in a mkdtemp. Synthetic only: it reads no footage and writes nothing else.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import struct
import sys
import tempfile
from collections import defaultdict

import numpy as np

FRACS = (0.2, 0.37, 0.55, 0.69, 0.85)
LAPS = 10


def _rows_at(sg, hz: int):
    """`synth_gopro._gps_rows` at `hz` (the shipped generator is 10 Hz): the same noise model."""
    if hz == 10:
        return sg._gps_rows

    def rows(truth, rng, gps_noise):
        dtk = 1.0 / hz
        tk = 0.037 + dtk * np.arange(int((truth.t_end - 0.037) / dtk) + 1)
        s_abs, v, _ = sg._kinematics(truth, tk)
        x, y, _, _ = truth.circuit.at(s_abs)
        n = len(tk)
        rho = math.exp(-dtk / 90.0)

        def ou(sigma):
            e = rng.normal(0.0, sigma * math.sqrt(1 - rho * rho), n)
            out = np.empty(n)
            out[0] = rng.normal(0.0, sigma)
            for i in range(1, n):
                out[i] = rho * out[i - 1] + e[i]
            return out
        dop = np.clip(1.1 + ou(0.12), 0.8, 2.5)
        fix = np.full(n, 3)
        fix[tk < 8.0] = 2
        fix[tk < 4.0] = 0
        dop = np.where(tk < 20.0, dop + np.clip(20.0 - tk, 0, None) * 0.6, dop)
        k = gps_noise * dop / 1.1
        ex = (ou(0.35) + rng.normal(0.0, 0.30, n)) * k
        ey = (ou(0.35) + rng.normal(0.0, 0.30, n)) * k
        moving = np.flatnonzero(v > 5.0)
        for frac, jump in ((0.35, (61.0, -44.0)), (0.71, (-38.0, 57.0))):
            i = moving[int(frac * len(moving))]
            ex[i] += jump[0]
            ey[i] += jump[1]
        lat, lon = sg.to_latlon(x + ex, y + ey, truth.origin)
        alt = 31.0 + ou(1.2) + rng.normal(0.0, 0.4, n)
        v2 = np.maximum(v + rng.normal(0.0, 0.06, n) * gps_noise, 0.0)
        v3 = np.maximum(v + rng.normal(0.0, 0.08, n) * gps_noise, 0.0)
        ms0 = round((sg.START_UTC - dt.datetime(2000, 1, 1, tzinfo=dt.UTC)).total_seconds() * 1000.0)
        days, msod = np.divmod(ms0 + np.round(1000.0 * dtk * np.arange(n)).astype(np.int64), 86_400_000)
        raw = np.column_stack([np.round(lat * 1e7), np.round(lon * 1e7), np.round(alt * 1000),
                               np.round(v2 * 1000), np.round(v3 * 100), days, msod])
        media = tk * (1.0 + truth.media_ppm * 1e-6) + truth.gps_lag_s
        return media, [struct.pack(">7i2H", *map(int, r), int(round(d * 100)), int(f))
                       for r, d, f in zip(raw, dop, fix, strict=True)]
    return rows


def _load_with(pacer, load, session_cls, paths, cached, mask):
    """Session.load with every fix where `mask` is True reported 2D (rejected by the gate)."""
    def read(_paths):
        samples = [s if not mask[i] else pacer.GPSSample(
            lat=s.lat, lon=s.lon, altitude=s.altitude, full_speed=s.full_speed,
            ground_speed=s.ground_speed, timestamp_ms=s.timestamp_ms, dop=s.dop, fix=2)
            for i, s in enumerate(cached[0])]
        return (samples, *cached[1:])
    real = load.read_recording
    load.read_recording = read
    try:
        return session_cls.load(paths)
    finally:
        load.read_recording = real


def _lap_index(s, tc, off):
    """valid lap id -> the index of its start crossing among the truth crossings."""
    out = {}
    for i in s.valid_lap_ids():
        j = int(np.argmin(np.abs(tc - (s.laps.start_timestamp(i) - off))))
        if abs(tc[j] - (s.laps.start_timestamp(i) - off)) < 1.0 and j + 1 < len(tc):
            out[int(i)] = j
    return out


def _splits(s, idx):
    return {j: [float(x) for x in s.lap_sector_splits(i)] for i, j in idx.items()}


def _pinned(s, name, clean, idx):
    """`s`'s splits re-timed with part of the frame held at the clean session's (module doc)."""
    cm = s.corners
    inv = {j: i for i, j in idx.items()}
    if name == "Mshift":
        s._sector_drift = lambda lid: clean["shift"].get(idx.get(int(lid)), (0.0, 0.0))
    else:
        cb = inv.get(clean["best_j"])
        if cb is None:
            return None
        cm._best_lap_id = lambda: cb
        if name == "Mrefc":
            kept = [inv[j] for j in clean["clean_js"] if j in inv]
            cm._clean_lap_ids = lambda: kept
        cm.invalidate()
    s._sector_collapse_cache = None
    try:
        return _splits(s, idx)
    finally:
        s.__dict__.pop("_sector_drift", None)
        cm.__dict__.pop("_clean_lap_ids", None)
        cm._best_lap_id = s.best_lap_id
        cm.invalidate()
        s._sector_collapse_cache = None


def _masks(tau, scross, tc, best_j, n, seed, hz):
    """case name -> boolean mask over the raw fixes (True = rejected)."""
    def odd(c):
        return (int(np.searchsorted(tc, c)) - 1) % 2 == 1
    out = {"clean": np.zeros(n, bool)}
    for side in "ba":
        for k in (1, 2, 3, 4, 6, 8, 10):
            for w in (1, 2):
                m = np.zeros(n, bool)
                for c in np.concatenate(scross):
                    if odd(c):
                        after = int(np.searchsorted(tau, c))
                        first = after - k - (w - 1) if side == "b" else after + k - 1
                        m[max(0, first):max(0, first + w)] = True
                out[f"sec{w}_{side}{k}"] = m
        for size in (0.3, 0.5, 1.0):
            for d in (0.1, 0.2, 0.5):
                m = np.zeros(n, bool)
                for c in np.concatenate(scross):
                    if odd(c):
                        lo = c - d - size if side == "b" else c + d
                        m |= (tau > lo) & (tau < lo + size)
                out[f"hole{size}_{side}{d}"] = m
    for p in (1, 2, 5):
        out[f"scat{p}"] = np.random.default_rng([seed, hz, p, 24]).random(n) < p / 100.0
    allc = np.sort(np.concatenate([*scross, tc]))
    for size in (0.3, 0.6, 1.0):
        m = np.zeros(n, bool)
        for a, b in zip(allc[:-1], allc[1:], strict=True):
            c = 0.5 * (a + b)
            if odd(c) and b - a >= size + 2.0:
                m |= (tau > c - size / 2) & (tau < c + size / 2)
        out[f"mid_{size}"] = m
    if best_j is not None:
        a, b = tc[best_j], tc[best_j + 1]
        m = np.zeros(n, bool)
        after = int(np.searchsorted(tau, b))
        m[after - 1:after + 1] = True
        out["best_line2"] = m
        mids = [0.5 * (x + y) for x, y in zip(allc[:-1], allc[1:], strict=True) if a < x and y <= b + 1e-9]
        if mids:
            c = mids[len(mids) // 2]
            out["best_mid0.6"] = (tau > c - 0.3) & (tau < c + 0.3)
        m = np.zeros(n, bool)
        for c in np.concatenate(scross):
            if a < c < b:
                m[int(np.searchsorted(tau, c))] = True
        out["best_sec1"] = m
    return out


def run(out_path: str, seeds, noises, hzs) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("PACER_NO_MEDIA", "1")
    os.environ.setdefault("PACER_APP_SUPPORT_JAIL", "1")
    import pacer
    from studio import load
    from studio.dev import synth_gopro as sg
    from studio.session import Session
    if os.path.exists(out_path):
        sys.exit(f"refusing to overwrite {out_path}: --out must be a new file")
    orig = sg._gps_rows
    with open(out_path, "x") as fo:
        for hz in hzs:
            sg._gps_rows = _rows_at(sg, hz)
            try:
                for noise in noises:
                    for seed in seeds:
                        tmp = tempfile.mkdtemp(prefix="sector_split_holes_")
                        rec = sg.generate(os.path.join(tmp, "rec"), seed=seed, laps=LAPS, gps_noise=noise)
                        cached = load.read_recording(rec.paths)
                        truth = rec.truth
                        n = len(cached[0])
                        tau = ((np.asarray(cached[2], float) - truth.gps_lag_s)
                               / (1.0 + truth.media_ppm * 1e-6))
                        clean = _load_with(pacer, load, Session, rec.paths, cached, np.zeros(n, bool))
                        line = clean.timing_lines_latlon()[0]
                        mid = np.mean(np.array(line), axis=0)
                        s0 = float(truth.s_at_latlon(np.array([mid[0]]), np.array([mid[1]]))[0])
                        length = truth.circuit.length
                        secs = [truth.line_at(float(np.mod(s0 + length * f, length))) for f in FRACS]
                        assert clean.apply_timing_lines_latlon(line, secs, confirmed=True)
                        tc = truth.crossings(line)
                        sts = [clean.laps.start_timestamp(i) for i in clean.valid_lap_ids()]
                        off = float(np.median([x - tc[np.argmin(np.abs(tc - x))] for x in sts]))
                        tsec = [truth.crossings(sl) for sl in secs]
                        cidx = _lap_index(clean, tc, off)
                        cb = clean.best_lap_id()
                        ctx = {"best_j": cidx.get(int(cb)) if cb is not None else None,
                               "clean_js": [cidx[i] for i in clean.corners._clean_lap_ids() if i in cidx],
                               "shift": {j: clean._sector_drift(i) for i, j in cidx.items()}}
                        clean_sp = _splits(clean, cidx)
                        for name, mask in _masks(tau, tsec, tc, ctx["best_j"], n, seed, hz).items():
                            s = clean if name == "clean" else _load_with(pacer, load, Session, rec.paths,
                                                                          cached, mask)
                            assert s.apply_timing_lines_latlon(line, secs, confirmed=True)
                            idx = _lap_index(s, tc, off)
                            got = {"M0": _splits(s, idx)}
                            for v in ("Mshift", "Mref", "Mrefc"):
                                got[v] = _pinned(s, v, ctx, idx)
                            b = s.best_lap_id()
                            laps = []
                            for i, j in idx.items():
                                tb = [tc[j], *(float(t[(t > tc[j]) & (t < tc[j + 1])][0]) for t in tsec),
                                      tc[j + 1]]
                                laps.append({"j": j, "out": bool(s.lap_out_of_bests(i)),
                                             "truth": np.diff(tb).tolist(), "clean": clean_sp.get(j),
                                             **{v: (None if sp is None else sp.get(j))
                                                for v, sp in got.items()}})
                            fo.write(json.dumps({"seed": seed, "noise": noise, "hz": hz, "case": name,
                                                 "best_j": idx.get(int(b)) if b is not None else None,
                                                 "clean_best_j": ctx["best_j"], "laps": laps}) + "\n")
                        fo.flush()
                        print(f"hz {hz} noise {noise} seed {seed} done", flush=True)
            finally:
                sg._gps_rows = orig


def _family(case: str) -> str:
    if case.startswith("sec"):
        return f"{case[3]} fix{'es' if case[3] == '2' else ''} {'1-3' if int(case[6:]) <= 3 else '4-10'} periods"
    if case.startswith(("hole", "mid")):
        return "lap-flagging holes, the other laps"
    return case


def summary(paths) -> None:
    """Every figure §42 quotes, per (Hz, noise, family), over the UNFLAGGED laps."""
    agg = defaultdict(lambda: defaultdict(list))
    for path in paths:
        with open(path) as f:
            for row in map(json.loads, f):
                if row["case"] == "clean":
                    continue
                key = (row["hz"], row["noise"], _family(row["case"]))
                a = agg[key]
                holed_only = row["case"].startswith("sec")
                cols = defaultdict(list)
                owners = []
                for lap in row["laps"]:
                    if lap["out"] or lap["M0"] is None or lap["clean"] is None:
                        continue
                    m0, cl, tr = (np.array(lap[k]) for k in ("M0", "clean", "truth"))
                    if len(m0) != len(cl):
                        continue
                    owners.append((cl, m0, tr))
                    if lap["Mshift"] is not None:
                        ms = np.array(lap["Mshift"])
                        if not holed_only or lap["j"] % 2 == 1:
                            a["local"].extend((ms - cl).tolist())
                            a["quick"].append(int(((ms - cl) < -0.010).sum()))
                            a["slow"].append(int(((ms - cl) > 0.010).sum()))
                            a["e_holed"].extend((m0 - tr).tolist())
                            a["e_clean"].extend((cl - tr).tolist())
                        frame = np.cumsum(m0)[:-1] - np.cumsum(ms)[:-1]
                        for k, v in enumerate(frame):
                            cols[k].append(v)
                    a["m0"].extend((m0 - cl).tolist())
                    if lap["Mrefc"] is not None and len(lap["Mrefc"]) == len(cl):
                        a["refc"].extend((np.array(lap["Mrefc"]) - cl).tolist())
                for vals in cols.values():
                    v = np.array(vals)
                    a["common"].append(abs(float(np.median(v))))
                    a["specific"].extend(np.abs(v - np.median(v)).tolist())
                if len(owners) >= 3:
                    for c in range(len(owners[0][0])):
                        cl = np.array([o[0][c] for o in owners])
                        m0 = np.array([o[1][c] for o in owners])
                        a["cols"].append(1)
                        if int(np.argmin(m0)) != int(np.argmin(cl)):
                            srt = np.sort(cl)
                            a["owner_margin"].append(srt[1] - srt[0])
                            tr = np.array([o[2][c] for o in owners])
                            a["truly_quicker"].append(int(tr[np.argmin(m0)] < tr[np.argmin(cl)]))
                a["flips"].append(row["best_j"] != row["clean_best_j"])

    def ms(x, q=None):
        x = np.abs(np.asarray(x)) * 1000
        return "-" if not len(x) else f"{(x.max() if q is None else np.percentile(x, q)):.1f}"
    print("hz noise family | split Δ vs clean as shipped max/p95, Δ<-10ms | frame pinned max/p95,"
          " quick/slow by >10ms | best lap + consensus set pinned max | frame: column-common max,"
          " lap-specific max/p95 | added σ vs truth | purple owner changes (cols), widest clean margin,"
          " new owner truly quicker | best lap changed")
    for key in sorted(agg):
        a = agg[key]
        d = np.array(a["m0"])
        e1, e0 = np.array(a["e_holed"]), np.array(a["e_clean"])
        add = (math.copysign(math.sqrt(abs(np.mean(e1 ** 2) - np.mean(e0 ** 2))),
                             np.mean(e1 ** 2) - np.mean(e0 ** 2)) * 1000 if len(e1) else float("nan"))
        print(f"{key[0]} {key[1]} {key[2]} | {ms(d)}/{ms(d, 95)}, {int((d < -0.010).sum())} |"
              f" {ms(a['local'])}/{ms(a['local'], 95)}, {sum(a['quick'])}/{sum(a['slow'])} |"
              f" {ms(a['refc'])} | {ms(a['common'])}, {ms(a['specific'])}/"
              f"{ms(a['specific'], 95)} | {add:+.1f} ms | {len(a['owner_margin'])} ({len(a['cols'])}),"
              f" {ms(a['owner_margin'])}, {sum(a['truly_quicker'])} | {sum(a['flips'])} of {len(a['flips'])}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--out", required=True, help="a NEW .jsonl file to write")
    r.add_argument("--seeds", default="1,2,3,4,5")
    r.add_argument("--noise", default="1,1.9,3")
    r.add_argument("--hz", default="10,18")
    s = sub.add_parser("summary")
    s.add_argument("jsonl", nargs="+")
    args = ap.parse_args(argv)
    if args.cmd == "run":
        run(args.out, [int(x) for x in args.seeds.split(",")], [float(x) for x in args.noise.split(",")],
            [int(x) for x in args.hz.split(",")])
    else:
        summary(args.jsonl)


if __name__ == "__main__":
    main()
