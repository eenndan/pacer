"""apex_speed_sigma: how far the Corners table's Δapex (one lap's apex — minimum — speed through a
corner minus the baseline lap's) reads from the truth: the step its colour must clear
(`stats.APEX_SPEED_LEVEL_KMH`).

WHY. Δapex was coloured with `theme.delta_colour`, whose "even" band is 0.005 — a TIME tolerance in
seconds — applied to km/h, so a +0.04 km/h gap printed "+0.0" in green. The speed chart's gain
shading leaves gaps under `plots_view.GAIN_GATE_KMH` (2√2 × `chart_stats.SPEED_NOISE_KMH`, 1.75 km/h)
unshaded, but that σ is the 10 Hz speed channel's per-SAMPLE residual (median of 24 real laps). An
apex speed is not a sample: it is the MINIMUM of a corner's samples, read off a window each lap
places through its own warp, so its noise is its own and is measured here.

THE GRID. `reference_lap_corner`'s (#667's `focus_cross_session` days): the demo circuit, one
driver, habit-free days (nothing changed), each day its own realisation of the laps and of the
receiver; per GPS noise (`--noises`) and per SEED SET (set j at seeds 401 + 100·j). THE APEX IS A
V: the generator's kart holds a flat speed through each arc, the case most favourable to a minimum
of noisy fixes (many of them share the bottom), so every corner of every lap is given a unique
minimum, a bowl BOWL_KMH deep at ±10 m (`apex_line._bowl`, re-integrated), where one or two fixes
carry it. `--bowl 0` measures the flat arc instead (reported, checked against nothing).

  * within — Δapex as the Corners table shows it with no reference: every clean non-best lap L of a
    day against that day's best lap B. Truth: the TRUE minimum speed over S* (the best lap's corner
    window mapped to the circuit's arc length) on L minus that on B. err = app − truth;
    sigma_one = RMS(err) / √2, the σ of ONE lap's apex speed that the difference is built from.
  * reference — Δapex with another day's recording adopted as the reference
    (`Session.set_reference_session`), every ordered pair of a set's days, every clean lap × every
    corner: app Δ (`CornerModel.lap_corner_stats` with the reference set) against the true
    difference over S* on L and on the reference's adopted lap.
  * the step: the house rule, `stats.DIFF_2SIGMA_K` (2·√2) × sigma_one, printed beside each
    noise; at REFERENCE_NOISE (the one reference recording's, `library.REFERENCE_RECORDING`) the
    summary checks it against `stats.APEX_SPEED_SIGMA_KMH` and `stats.APEX_SPEED_LEVEL_KMH`.
  * colour rates — of the cells whose PRINTED Δ (one decimal, km/h) clears a step, the share whose
    true difference is under 0.5 km/h (no difference a driver could act on); at the old rule
    (|Δ| past 0.005, "0.0" included), at 1.75 km/h and at the measured step.

MEASURED (defaults: the V apex, 4 sets × 10 days per noise, about 10 minutes a noise on a loaded
Mac): at REFERENCE_NOISE σ 0.362 km/h against a lap of the same recording (sets 0.348-0.380) and
0.366 km/h against another recording's → 2√2σ = 1.02 / 1.04 km/h; the table across the noises and
the flat arc's figures are in `stats.APEX_SPEED_SIGMA_KMH`'s comment.

ARGV CONTRACT: named flags only. `--noises 1.9,4.0,6.5` (default), `--sets N` (default 4),
`--set-index J` (run only set J), `--days N` (default 10), `--no-reference` (Δbest only),
`--bowl RISE` (default BOWL_KMH; 0 = the flat arc). Every recording is generated into ONE
`mkdtemp` under $TMPDIR and removed before exit; the app-support store is jailed and the sidecar
write stubbed. Output is stdout: one `{"set": …}` line per noise and seed set, then the pooled
summary as the last line. `--summarize FILE[,FILE…]` instead READS such stdout logs and prints the
same pooled summary; it writes nothing.

    pixi run python -m studio.dev.probes.apex_speed_sigma
"""
from __future__ import annotations

import argparse
import contextlib
import itertools
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

from studio import chart_stats, stats  # noqa: E402
from studio.dev.probes import reference_lap_corner as rlc  # noqa: E402
from studio.dev.probes.focus_cross_session import (  # noqa: E402
    BASE_SEED,
    REFERENCE_NOISE,
    SET_STRIDE,
)

NOISES = (1.9, 4.0, 6.5)
# The V every apex is given (km/h deep at ±10 m): apex_line's deeper plant.
BOWL_KMH = 3.0
# The small fixed set tests/test_corners.py re-measures (`measure_sigma`): seeds at REFERENCE_NOISE.
CHECK_SEEDS = (401, 402, 403)
# "Level on truth" for the colour-rate table: an apex-speed difference this small is no difference a
# driver could act on, so colouring it is colouring the receiver.
TRUE_LEVEL_KMH = 0.5
# The gain shading's gate (plots_view.GAIN_GATE_KMH), from its own σ so this probe imports no Qt view.
CHART_GATE_KMH = round(stats.DIFF_2SIGMA_K * chart_stats.SPEED_NOISE_KMH, 2)


def _true_apex(day: dict, lap: int, s0: float, s1: float) -> float | None:
    """The TRUE minimum speed (km/h) from arc length s0 to s1 on `day`'s lap `lap`."""
    t0, t1 = day["bounds"][lap]
    a, b = day["truth"].times_at(s0), day["truth"].times_at(s1)
    hit = [t for t in a if t0 - 1.0 <= t < t1]
    if not hit or not (b > hit[0]).any():
        return None
    ta, tb = hit[0], float(b[b > hit[0]][0])
    tau = np.linspace(ta, tb, max(8, int((tb - ta) / 0.01)))
    return float(np.min(day["truth"].speed_at(tau))) * 3.6


def _day(work: str, noise: float, seed: int, day_no: int) -> dict:
    d = rlc._day(work, noise, seed, day_no)
    s = d["s"]
    d["apex"] = {lap: [(st.apex_speed, st.apex_speed_delta) for st in s.corners.lap_corner_stats(lap)]
                 for lap in d["ids"]}
    d["true_apex"] = {}
    for lap in d["ids"]:
        d["true_apex"][lap] = [_true_apex(d, lap, *rlc._span(d["star"], k))
                               for k in range(len(d["cids"]))]
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
            if lap == best or k >= len(x["apex"][lap]):
                continue
            tl = x["true_apex"][lap][k]
            if tl is not None:
                out.append([cid, round(x["apex"][lap][k][1], 4), round(tl - tb, 4)])
    return out


def pair_rows(x: dict, y: dict) -> list[list]:
    """[cid, app Δapex vs the reference, true Δ over S*] for every clean lap of `x` × corner, with
    `y` adopted as the reference."""
    s = x["s"]
    if s.set_reference_session(y["s"], "ref") is not None:
        return []
    try:
        ref_lap = s.reference_lap_id()
        if ref_lap not in y["bounds"]:
            return []
        rows = []
        for k, cid in enumerate(x["cids"]):
            s0, s1 = rlc._span(x["star"], k)
            tr = _true_apex(y, ref_lap, s0, s1)
            if tr is None:
                continue
            for lap in x["ids"]:
                st = s.corners.lap_corner_stats(lap)
                tl = x["true_apex"][lap][k]
                if k < len(st) and tl is not None:
                    rows.append([cid, round(st[k].apex_speed_delta, 4), round(tl - tr, 4)])
        return rows
    finally:
        s.clear_reference()


def measure_sigma(work: str, noise: float, seeds) -> dict:
    """{"within": sigma_one, "reference": sigma_one} at `seeds`, on the V apex — the test's fixed
    check. The generator is put back after."""
    with _bowl_every_apex(BOWL_KMH):
        days = [_day(work, noise, seed, k + 1) for k, seed in enumerate(seeds)]
    w = np.array([r[1] - r[2] for d in days for r in within_rows(d)])
    p = np.array([r[1] - r[2] for x, y in itertools.permutations(days, 2) for r in pair_rows(x, y)])
    return {"within": _rms(w) / math.sqrt(2.0), "reference": _rms(p) / math.sqrt(2.0)}


def measure_set(work: str, noise: float, j: int, n_days: int) -> dict:
    days = [_day(work, noise, BASE_SEED + SET_STRIDE * j + k, 1000 * j + k + 1)
            for k in range(n_days)]
    return {"noise": noise, "set": j, "days": n_days,
            "within": [r for d in days for r in within_rows(d)],
            "rows": [r for x, y in itertools.permutations(days, 2) for r in pair_rows(x, y)]}


def _rms(a) -> float:
    a = np.asarray(a, float)
    return float(math.sqrt(np.mean(a ** 2)))


def _printed(v: float) -> float:
    return float(f"{v:+.1f}")


def _rates(app: np.ndarray, true: np.ndarray, step: float | None) -> dict:
    printed = np.array([_printed(v) for v in app])
    # step None = the old rule: theme.delta_colour's 0.005 dead band on the raw km/h figure
    col = np.abs(app) > 0.005 if step is None else np.abs(printed) >= step - 1e-9
    n = int(col.sum())
    return {"coloured": [n, len(app)],
            "of_which_true_level": int((col & (np.abs(true) < TRUE_LEVEL_KMH)).sum()),
            "printed_00_coloured": int((col & (printed == 0.0)).sum()),
            "inside_chart_gate_coloured": int((col & (np.abs(app) < CHART_GATE_KMH)).sum())}


def _one(rows: list, label: str) -> dict:
    app = np.array([r[1] for r in rows])
    true = np.array([r[2] for r in rows])
    err = app - true
    sigma_one = _rms(err) / math.sqrt(2.0)
    step = stats.house_step(sigma_one)
    by_cid = {}
    for cid in sorted({r[0] for r in rows}):
        e = np.array([r[1] - r[2] for r in rows if r[0] == cid])
        by_cid[str(cid)] = round(_rms(e) / math.sqrt(2.0), 3)
    return {f"{label}_cells": len(rows), f"{label}_sigma_one": round(sigma_one, 3),
            f"{label}_err_mean": round(float(np.mean(err)), 3),
            f"{label}_step_rule": step, f"{label}_sigma_by_cid": by_cid,
            f"{label}_true_sd": round(float(np.std(true, ddof=1)), 3),
            f"{label}_rates_old": _rates(app, true, None),
            f"{label}_rates_{CHART_GATE_KMH:.2f}": _rates(app, true, CHART_GATE_KMH),
            f"{label}_rates_rule": _rates(app, true, step)}


def summarize(recs: list[dict]) -> dict:
    out = {}
    shipped = all(r.get("bowl") == BOWL_KMH for r in recs)   # the grid the table is read from
    for noise in sorted({r["noise"] for r in recs}):
        mine = sorted((r for r in recs if r["noise"] == noise), key=lambda r: r["set"])
        rec = {"days": sum(r["days"] for r in mine), "sets": len(mine),
               "bowl_kmh": mine[0].get("bowl", 0.0)}
        rec.update(_one([x for r in mine for x in r["within"]], "within"))
        if any(r["rows"] for r in mine):
            rec.update(_one([x for r in mine for x in r["rows"]], "reference"))
        rec["within_sigma_per_set"] = [
            round(_rms([x[1] - x[2] for x in r["within"]]) / math.sqrt(2.0), 3) for r in mine]
        for rec_name, row in stats.PUBLISHED_NOISE_SIGMAS.items():
            if row.noise == noise and shipped:
                # The published table's km/h cells must be this probe's pooled σs at that noise.
                rec[f"table_{rec_name}_is_this_sigma"] = (
                    rec["within_sigma_one"] == row.apex_kmh
                    and rec.get("reference_sigma_one") == row.reference_apex_kmh)
        if noise == REFERENCE_NOISE and shipped:
            rec["app_sigma_is_this_sigma"] = rec["within_sigma_one"] == stats.APEX_SPEED_SIGMA_KMH
            rec["app_step_is_this_step"] = (stats.house_step(rec["within_sigma_one"])
                                            == stats.APEX_SPEED_LEVEL_KMH)
        out[f"noise {noise:g}"] = rec
    return out


@contextlib.contextmanager
def _bowl_every_apex(rise_kmh: float):
    """Inside, every synthetic recording (and its truth) has a V-shaped apex `rise_kmh` deep at
    ±10 m at every corner of every lap (nothing changes at 0); the generator is put back after."""
    from studio.dev import synth_gopro as sg
    from studio.dev.probes.apex_line import _bowl

    real = sg.simulate
    if rise_kmh:
        sg.simulate = lambda *a, **kw: _bowl(real(*a, **kw), 0.0, rise_kmh)
    try:
        yield
    finally:
        sg.simulate = real


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--noises", default=",".join(f"{n:g}" for n in NOISES))
    ap.add_argument("--sets", type=int, default=4)
    ap.add_argument("--set-index", type=int)
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--no-reference", action="store_true",
                    help="within-recording Δapex only (no reference pairs: ~10× faster)")
    ap.add_argument("--bowl", type=float, default=BOWL_KMH,
                    help="the V every apex is given, km/h deep at ±10 m; 0 = the flat arc")
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
        with _bowl_every_apex(a.bowl):
            for noise in (float(n) for n in a.noises.split(",")):
                for j in sets:
                    if a.no_reference:
                        days = [_day(work, noise, BASE_SEED + SET_STRIDE * j + k, 1000 * j + k + 1)
                                for k in range(a.days)]
                        rec = {"noise": noise, "set": j, "days": a.days,
                               "within": [r for d in days for r in within_rows(d)], "rows": []}
                    else:
                        rec = measure_set(work, noise, j, a.days)
                    rec["bowl"] = a.bowl
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
