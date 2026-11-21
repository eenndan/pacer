"""apex_speed_sigma: the noise of the Corners table's Δapex (one lap's apex — minimum — speed through
a corner minus the baseline lap's), by the SHAPE of the apex, against the noise of one speed fix:
the evidence that Δapex needs no step of its own and is coloured from the speed chart's gain gate
(`chart_stats.GAIN_GATE_KMH`, the house rule on the per-sample speed σ measured on a real recording).

WHY. Δapex was coloured with `theme.delta_colour`, whose "even" band is 0.005 — a TIME tolerance in
seconds — applied to km/h, so a +0.04 km/h gap printed "+0.0" in green. Its colour needs a speed
step. An apex speed is `argmin` over the corner's fixes (`corners.lap_corner_stats`), so its noise
depends on how many fixes share the bottom: on a flat arc many do and the minimum's noise is small;
on a straight-sided V (constant braking in, constant acceleration out, a real hairpin) ONE fix
carries it and its noise is that fix's. THE SHAPE DECIDES THE APEX σ, and no recording tells the app
which shape a corner has — but whatever the shape, the minimum's σ is CAPPED by one fix's σ. So the
app gates Δapex on the per-sample figure, the only speed noise measured on a real recording
(`chart_stats.SPEED_SIGMA_KMH`, 0.62 km/h), which is what the chart's shading already reads.

THE GRID. `reference_lap_corner`'s (#667's `focus_cross_session` days): the demo circuit, one
driver, habit-free days (nothing changed), each day its own realisation of the laps and of the
receiver, per SEED SET (set j at seeds 401 + 100·j), at REFERENCE_NOISE (the one reference
recording's, `library.REFERENCE_RECORDING`) unless `--noise` says otherwise. Per SHAPE every corner
of every lap is given its apex: `flat` (the generator's arc), `curved:R` (a parabolic bowl R km/h
deep at ±10 m, `apex_line._bowl`) or `vee:R` (straight sides, R km/h at ±10 m), re-integrated.

  * apex σ — every clean non-best lap L against its day's best B, Δapex as the table shows it, against
    the TRUE minimum speed over S* (the best lap's corner window on the circuit's arc length) on L
    minus that on B; σ = RMS(app − truth) / √2, the σ of ONE apex speed, with its standard error;
  * sample σ — the chart's own statistic on the same seeds' FLAT days (a planted profile's TRUE
    speed can step at the edge of its ±40 m window, which is not receiver noise): the app's 10 Hz
    speed against its 0.5 s moving average, sd per lap, median over laps (`SPEED_SIGMA_KMH`'s definition; it reads a white
    per-fix σ low by √0.8, so the cap it states is the stricter one);
  * the cap: apex σ ≤ sample σ + 2 SE, per shape (`cap_check`, which tests/test_corners.py runs on
    the worst shape at a small fixed seed set);
  * colour rates — of the cells truly level (|true Δ| < 0.5 km/h), the share coloured by the old rule
    (|Δ| past 0.005, "0.0" included) and by the gate (printed |Δ| ≥ 1.75 km/h).

MEASURED (defaults: 40 days per shape at noise 1.9, two processes, about 20 minutes): apex σ flat
0.316, curved 1 / 3 / 6 / 10 km/h 0.343 / 0.362 / 0.397 / 0.412, vee 4 / 10 km/h 0.424 / 0.536 km/h
(SE ≤ 0.007), every one under the per-fix σ, 0.668 km/h on the flat days. The 1.75 km/h gate colours
0-0.8 % of the truly level cells on every shape but vee 10, where it colours 3.4 %. The #684 review's
own sweep (seeds 401-440) read the same σs within 0.01 km/h and showed a 1.02 km/h step (2√2 × the
curved-3 σ) colouring 7.5 % / 11.6 % / 17.0 % of truly level cells on curved 3 / vee 4 / vee 10.

ARGV CONTRACT: named flags only. `--noise 1.9` (default REFERENCE_NOISE), `--shapes
flat,curved:3,vee:10` (default SHAPES), `--sets N` (default 4), `--set-index J`, `--days N`
(default 10). Every recording is generated into ONE `mkdtemp` under $TMPDIR and removed before exit;
the app-support store is jailed and the sidecar write stubbed. Output is stdout: one `{"set": …}`
line per shape and seed set, then the pooled summary as the last line. `--summarize FILE[,FILE…]`
instead READS such stdout logs and prints the same pooled summary; it writes nothing.

    pixi run python -m studio.dev.probes.apex_speed_sigma
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import math
import os
import shutil
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")

from studio.dev._jail import divert_app_support  # noqa: E402

_JAIL = divert_app_support("apex_speed_sigma_")

import numpy as np  # noqa: E402

from studio import chart_stats  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.dev.probes import reference_lap_corner as rlc  # noqa: E402
from studio.dev.probes.focus_cross_session import (  # noqa: E402
    BASE_SEED,
    REFERENCE_NOISE,
    SET_STRIDE,
)

SHAPES = ("flat", "curved:1", "curved:3", "curved:6", "curved:10", "vee:4", "vee:10")
# The shape tests/test_corners.py checks the cap on: the worst measured (one fix carries the minimum).
WORST_SHAPE = "vee:10"
# Its small fixed seed set at REFERENCE_NOISE (`cap_check`).
CHECK_SEEDS = (401, 402, 403)
# "Level on truth" for the colour-rate table: an apex-speed difference this small is no difference a
# driver could act on, so colouring it is colouring the receiver.
TRUE_LEVEL_KMH = 0.5
_HALF_M = 10.0      # both plants are R km/h above the apex at ±10 m
# ±40 m around the apex, `apex_line._bowl`'s window. A deep profile can still be above the physics at
# its edge, so the planted TRUE speed steps there: harmless to the minimum (which sits at the apex),
# but it is not receiver noise, so the per-fix σ is read on the FLAT days of the same seeds.
_REACH_M = 40.0


def _shaped(truth, shape: str):
    """`truth` with every flying lap's speed given `shape`'s apex at each corner, re-integrated as
    `simulate` integrates (the plant never lowers a speed: max(physics, the apex's profile))."""
    kind, _, rise = shape.partition(":")
    if kind == "flat":
        return truth
    c, length = truth.circuit, truth.circuit.length
    v = truth.v_nodes.copy()
    s_abs = truth.s_start + truth.d_nodes
    lap = np.floor(s_abs / length).astype(int)
    s_lap = s_abs - lap * length
    r = float(rise) / 3.6
    for k in range(1, truth.laps + 1):
        on_lap = lap == k
        for ct in c.corners:
            near = on_lap & (np.abs(s_lap - ct.apex_s) < _REACH_M)
            if not near.any():
                continue
            v_a = float(np.interp(ct.apex_s, s_lap[near], v[near]))
            x = np.abs(s_lap[near] - ct.apex_s) / _HALF_M
            v[near] = np.maximum(v[near], v_a + r * (x ** 2 if kind == "curved" else x))
    t = sg.T_LEAD + np.concatenate([[0.0], np.cumsum(2.0 * sg.DS / (v[:-1] + v[1:]))])
    return dataclasses.replace(truth, v_nodes=v, t_nodes=t, t_end=float(t[-1] + sg.T_TAIL))


@contextlib.contextmanager
def _apex_shape(shape: str):
    """Inside, every synthetic recording (and its truth) has `shape`'s apex at every corner of every
    lap; the generator is put back after."""
    real = sg.simulate
    sg.simulate = lambda *a, **kw: _shaped(real(*a, **kw), shape)
    try:
        yield
    finally:
        sg.simulate = real


def _true_apex(day: dict, lap: int, s0: float, s1: float) -> float | None:
    """The TRUE minimum speed (km/h) from arc length s0 to s1 on `day`'s lap `lap`."""
    t0, _t1 = day["bounds"][lap]
    a, b = day["truth"].times_at(s0), day["truth"].times_at(s1)
    hit = [t for t in a if t0 - 1.0 <= t < _t1]
    if not hit or not (b > hit[0]).any():
        return None
    ta, tb = hit[0], float(b[b > hit[0]][0])
    tau = np.linspace(ta, tb, max(8, int((tb - ta) / 0.01)))
    return float(np.min(day["truth"].speed_at(tau))) * 3.6


def _day(work: str, noise: float, seed: int, day_no: int) -> dict:
    d = rlc._day(work, noise, seed, day_no)
    s = d["s"]
    d["dapex"] = {lap: [st.apex_speed_delta for st in s.corners.lap_corner_stats(lap)]
                  for lap in d["ids"]}
    d["true_apex"] = {lap: [_true_apex(d, lap, *rlc._span(d["star"], k))
                            for k in range(len(d["cids"]))] for lap in d["ids"]}
    d["sample_sd"] = []
    for lap in d["ids"]:
        v = s._lap_arrays(lap)[1]
        if len(v) >= 50:   # the chart's statistic: residual against a 0.5 s (5-fix) moving average
            d["sample_sd"].append(float(np.std(v[2:-2] - np.convolve(v, np.ones(5) / 5, "valid"))))
    return d


def within_rows(x: dict) -> list[list]:
    """[cid, app Δapex, true Δ over S*] for every non-best clean lap of `x` against its best."""
    out = []
    best = x["best"]
    for k, cid in enumerate(x["cids"]):
        tb = x["true_apex"][best][k]
        if tb is None:
            continue
        for lap in x["ids"]:
            if lap == best or k >= len(x["dapex"][lap]):
                continue
            tl = x["true_apex"][lap][k]
            if tl is not None:
                out.append([cid, round(x["dapex"][lap][k], 4), round(tl - tb, 4)])
    return out


def _rms(a) -> float:
    a = np.asarray(a, float)
    return float(math.sqrt(np.mean(a ** 2)))


def _sigma(rows: list) -> tuple[float, float]:
    """(σ of one apex speed, its standard error) over `rows`."""
    s = _rms([r[1] - r[2] for r in rows]) / math.sqrt(2.0)
    return s, s / math.sqrt(2.0 * len(rows))


def cap_check(work: str, noise: float, seeds, shape: str = WORST_SHAPE) -> dict:
    """{"apex", "apex_se", "sample"} at `seeds`: the apex σ under `shape`, the per-fix σ on the same
    seeds' FLAT days (the receiver's noise, not the plant's step) — the test's fixed check."""
    with _apex_shape(shape):
        days = [_day(work, noise, seed, k + 1) for k, seed in enumerate(seeds)]
    sigma, se = _sigma([r for d in days for r in within_rows(d)])
    flat = [_day(work, noise, seed, 100 + k + 1) for k, seed in enumerate(seeds)]
    return {"apex": sigma, "apex_se": se,
            "sample": float(np.median([x for d in flat for x in d["sample_sd"]]))}


def _rates(rows: list) -> dict:
    app = np.array([r[1] for r in rows])
    true = np.array([r[2] for r in rows])
    printed = np.array([float(f"{v:+.1f}") for v in app])
    level = np.abs(true) < TRUE_LEVEL_KMH
    out = {"level_cells": int(level.sum())}
    for name, col in (("old", np.abs(app) > 0.005),
                      (f"{chart_stats.GAIN_GATE_KMH:.2f}",
                       np.abs(printed) >= chart_stats.GAIN_GATE_KMH - 1e-9)):
        out[f"coloured_{name}"] = int(col.sum())
        out[f"level_coloured_{name}"] = round(float((col & level).sum() / max(level.sum(), 1)), 4)
    return out


def summarize(recs: list[dict]) -> dict:
    out = {}
    # the per-fix σ is the receiver's, read on the flat days (a planted profile steps at its edge)
    flat = [x for r in recs if r["shape"] == "flat" for x in r["sample_sd"]]
    sample = float(np.median(flat)) if flat else None
    for shape in dict.fromkeys(r["shape"] for r in recs):
        mine = [r for r in recs if r["shape"] == shape]
        rows = [x for r in mine for x in r["within"]]
        sigma, se = _sigma(rows)
        out[shape] = {"noise": mine[0]["noise"], "days": sum(r["days"] for r in mine),
                      "cells": len(rows), "apex_sigma": round(sigma, 3), "apex_se": round(se, 4),
                      "capped": None if sample is None else sigma <= sample + 2.0 * se,
                      **_rates(rows)}
    out["sample_sigma_flat_days"] = None if sample is None else round(sample, 3)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--noise", type=float, default=REFERENCE_NOISE)
    ap.add_argument("--shapes", default=",".join(SHAPES))
    ap.add_argument("--sets", type=int, default=4)
    ap.add_argument("--set-index", type=int)
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--summarize", default="")
    a = ap.parse_args()
    if a.summarize:
        recs = []
        for path in a.summarize.split(","):
            with open(path, encoding="utf-8") as f:
                recs += [json.loads(ln)["set"] for ln in f if ln.startswith('{"set"')]
        print(json.dumps(summarize(recs)))
        return 0
    sets = [a.set_index] if a.set_index is not None else list(range(a.sets))
    work = tempfile.mkdtemp(prefix="apex_speed_sigma_")
    recs = []
    try:
        for shape in a.shapes.split(","):
            for j in sets:
                with _apex_shape(shape):
                    days = [_day(work, a.noise, BASE_SEED + SET_STRIDE * j + k, 1000 * j + k + 1)
                            for k in range(a.days)]
                rec = {"shape": shape, "noise": a.noise, "set": j, "days": a.days,
                       "within": [r for d in days for r in within_rows(d)],
                       "sample_sd": [x for d in days for x in d["sample_sd"]]}
                print(json.dumps({"set": rec}), flush=True)
                recs.append(rec)
                for name in os.listdir(work):     # this set's recordings are done with
                    shutil.rmtree(os.path.join(work, name), ignore_errors=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(summarize(recs)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
