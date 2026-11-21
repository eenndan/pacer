"""reference_lap_corner: how far the Corners table's Δref (one lap's corner time minus the loaded
reference recording's lap's corner time) reads from the truth when the two laps come from two
different recordings — the step its colour must clear (`stats.REFERENCE_CORNER_LEVEL_S`).

WHY. With a reference RECORDING loaded, the Corners table's Δ column ("Δref") and the export's
corner cards ("vs ref" / "≈ ref") difference ONE lap of this session against ONE lap of another
recording, corner by corner. Their colour used the single-recording timing step (0.07 s,
`library.PB_PRECISION_S`). But each corner time is a duration between two corner edges that each
recording places through its own noisy fixes, and the reference's edges are placed through a
second warp (`CornerModel.reference_alignment`, the reference's trace moved into this session's
frame by the overlay's similarity fit). This measures the difference's error on synthetic race
days of known truth.

THE GRID. #667's (`focus_cross_session`): the demo circuit, one driver, habit-free days (nothing
changed), each day its own realisation of the laps and of the receiver; per GPS noise level
(`NOISES`, per-lap timing σ 0.025-0.086 s, the span docs/ACCURACY.md publishes) and per SEED SET
(`--sets`, set j at seeds 401 + 100·j). Every ordered pair of a set's days (X, Y): X is opened, Y
adopted as its reference through the app's own `Session.set_reference_session`, and every clean
lap L of X is read off the Corners table's own source (`CornerModel.lap_corner_stats`: the Δref
of each corner). The truth: X's best lap's corner window (the table's frame) mapped to the
circuit's arc length, S*, and the TRUE time over S* on lap L (X's truth) and on Y's adopted lap
(Y's truth).

  * err = app Δref − (T_L(S*) − T_ref(S*)): the measurement error of the difference. A constant
    per-corner offset common to both figures cancels in it; nothing is removed from it.
  * sigma_diff — its RMS about zero, pooled over corners, laps, pairs and seed sets; the step is
    the house rule's 2σ of a difference, DIFF_2SIGMA_K (2·√2) × sigma_one where sigma_one =
    sigma_diff / √2 is the per-figure σ the difference is built from (so the step = 2·sigma_diff);
  * the MODEL, checked: err = e_L − e_ref, each figure's own error against S*, and each of those
    = a TIMING part (app time − true time over the span the figure's own edges cover) + a WINDOW
    part (true time over its own span − over S*). The reference's window part is the alignment
    term: its edges are placed by the cross-recording warp (edge_ref_m, metres from S*'s edges);
  * sigma_within — the same error for Δbest, a local lap against the local best lap with no
    reference loaded: the single-recording difference the 0.07 s step was made for;
  * true_sd — the sd of the TRUE difference T_L(S*) − T_ref(S*): the driver's own lap-to-lap and
    day-to-day scatter. It is a true fact about the two laps, not noise: the colour must beat the
    measurement error alone, so it is reported, never folded into the step;
  * app_sd / app_p95 — the app's Δref as printed, best lap against best lap (the reviewer's
    statistic): truth and error together;
  * colour rates: cells coloured under 0.07 s and under the step; of those, the share whose true
    difference is under 0.03 s or of the other sign;
  * power — a true corner gain g (0.10 / 0.15 / 0.20 s) coloured "faster" when the printed
    −g + err clears the step, over every measured err at that noise (the convolution of a planted
    gain with this exact error population; a planted habit moves the driving, not the receiver).

MEASURED (defaults, 4 sets × 10 days = 40 days per noise, ordered pairs within a set, about 16
minutes as two processes of two `--set-index` each): at REFERENCE_NOISE (per-lap σ 0.025 s, the
one reference recording's, `library.REFERENCE_RECORDING`) sigma_one 0.0205 s (sets
0.0183-0.0222) → 2√2σ = 0.058 → 0.06 s, under the timing step, which `stats.corner_delta_step`
then reads; Δbest's own error 0.0203 s. The table across the noises, the model and the power are
in `stats.REFERENCE_CORNER_SIGMA_S`'s comment, and every published recording's row in
`stats.PUBLISHED_NOISE_SIGMAS`, which the summary checks at each noise it holds.

ARGV CONTRACT: named flags only. `--noises 1.9,4.0` (default NOISES), `--sets N` (default 4),
`--set-index J` (run only set J), `--days N` (default 10). Every recording is generated into ONE
`mkdtemp` under $TMPDIR and removed before exit; the app-support store is jailed and the sidecar
write stubbed. Output is stdout: one `{"set": …}` line per noise and seed set, then the pooled
summary as the last line. `--summarize FILE[,FILE…]` instead READS such stdout logs and prints the
same pooled summary; it writes nothing.

    pixi run python -m studio.dev.probes.reference_lap_corner
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import shutil
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")

from studio.dev._jail import divert_app_support  # noqa: E402

_JAIL = divert_app_support("reference_lap_corner_")

import numpy as np  # noqa: E402

from studio import chapters, library, sidecar, stats  # noqa: E402
from studio._signal import fmt_signed  # noqa: E402
from studio.dev import make_demo  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.dev.probes.focus_cross_session import (  # noqa: E402
    BASE_SEED,
    NOISES,
    REFERENCE_NOISE,
    SET_STRIDE,
    _generate,
    _lap_bounds,
)
from studio.session import Session  # noqa: E402

sidecar.save = lambda *a, **k: None   # nothing is ever written beside a recording here

GAINS = (0.10, 0.15, 0.20)
# The small fixed set tests/test_corners.py re-measures (`measure_sigma`): seeds at REFERENCE_NOISE.
CHECK_SEEDS = (401, 402, 403)
# "Level on truth" for the colour-rate table: a true difference this small is no difference a
# driver could act on, so colouring it is colouring the receiver.
TRUE_LEVEL_S = 0.03


def _printed(d: float) -> float:
    """The Δ as the Corners table prints it (`fmt_signed(d, 2)`), read back."""
    return float(fmt_signed(d, 2).replace("−", "-"))


def _truth_s_at(s: Session, truth, lap_id: int, d) -> np.ndarray:
    """The truth's arc length under `lap_id`'s app trace at lap odometer(s) `d` of session `s`."""
    import pacer  # the core's local → global conversion, after the jail above

    dist = s._lap_arrays(lap_id)[0]
    xs, ys = s.lap_trace_xy(lap_id)
    m = min(len(dist), len(xs))
    out = []
    for dd in np.atleast_1d(d):
        g = s.cs.global_(pacer.Vec3f(float(np.interp(dd, dist[:m], xs[:m])),
                                     float(np.interp(dd, dist[:m], ys[:m])), 0.0))
        out.append(float(truth.s_at_latlon(np.array([g.lat]), np.array([g.lon]))[0]))
    return np.asarray(out)


def _true_time(day: dict, lap: int, s0: float, s1: float) -> float | None:
    """The TRUE time from arc length s0 to s1 on `day`'s lap `lap` (its true start-line bounds)."""
    t0, t1 = day["bounds"][lap]
    a, b = day["truth"].times_at(s0), day["truth"].times_at(s1)
    hit = [t for t in a if t0 - 1.0 <= t < t1]
    if not hit or not (b > hit[0]).any():
        return None
    return float(b[b > hit[0]][0] - hit[0])


def _edges_on(align, frame: np.ndarray, total_frame: float, total_lap: float) -> np.ndarray:
    """The frame's corner edges on a lap's odometer through its warp (knot_frame, knot_lap); the
    normalized fraction where there is none — what `corners.project_boundaries` reads."""
    if align is None:
        return frame * total_lap / total_frame
    return np.interp(frame, align[0], align[1])


def _day(work: str, noise: float, seed: int, day_no: int) -> dict:
    path = _generate(work, noise, seed, day_no, None)
    s = Session.load(chapters.discover_siblings(path))
    truth = sg.simulate(seed, 14, origin=make_demo.DEMO_ORIGIN)
    d = {"s": s, "truth": truth, "path": path, "ids": s.consistency_lap_ids()}
    d["bounds"] = _lap_bounds(truth, s)
    d["ids"] = [i for i in d["ids"] if i in d["bounds"]]
    best = s.best_lap_id()
    corner_list, total = s.corners.basis()
    d["cids"] = [c.cid for c in corner_list]
    d["frame"] = np.array([b for c in corner_list for b in (float(c.enter), float(c.exit))])
    d["total"] = float(total)
    # S*: the table's frame (the best lap's corner windows) on the circuit's arc length.
    d["star"] = _truth_s_at(s, truth, best, d["frame"])
    # Each local lap's own edges on the circuit (its warp onto the frame), and Δbest as shown.
    d["edges"], d["dbest"] = {}, {}
    for lap in d["ids"]:
        tot = float(s._lap_arrays(lap)[0][-1])
        align = None if lap == best else s.corners.lap_alignment(lap, tot)
        d["edges"][lap] = _truth_s_at(s, truth, lap, _edges_on(align, d["frame"], d["total"], tot))
        d["dbest"][lap] = [(st.time, st.delta) for st in s.corners.lap_corner_stats(lap)]
    d["best"] = best
    return d


def _span(edges: np.ndarray, k: int) -> tuple[float, float]:
    return float(edges[2 * k]), float(edges[2 * k + 1])


def _wrap(a: float, b: float, length: float) -> float:
    """b − a on the circuit, the short way round."""
    return float((b - a + length / 2) % length - length / 2)


def pair_rows(x: dict, y: dict) -> list[list]:
    """Every clean lap of `x` × every corner, with `y` adopted as the reference, as
    [cid, is_best, app Δref, true Δ over S*, e_L timing, e_L window, e_ref timing, e_ref window,
     ref edge displacement in (m), ref edge displacement out (m)]."""
    s = x["s"]
    reason = s.set_reference_session(y["s"], "ref")
    if reason is not None:
        return []
    try:
        ref_lap = s.reference_lap_id()
        ref_stats = s.corners.reference_corner_stats()
        if ref_stats is None or ref_lap not in y["bounds"]:
            return []
        rd = y["s"]._lap_arrays(ref_lap)[0]
        ref_edges = _truth_s_at(y["s"], y["truth"], ref_lap,
                                _edges_on(s.corners.reference_alignment(), x["frame"], x["total"],
                                          float(rd[-1])))
        length = x["truth"].circuit.length
        rows = []
        r_cache = {}
        for k in range(len(x["cids"])):
            s0, s1 = _span(x["star"], k)
            r0, r1 = _span(ref_edges, k)
            tr_star = _true_time(y, ref_lap, s0, s1)
            tr_own = _true_time(y, ref_lap, r0, r1)
            if tr_star is None or tr_own is None:
                continue
            r_cache[k] = (ref_stats[k].time - tr_own, tr_own - tr_star, tr_star,
                          _wrap(s0, r0, length), _wrap(s1, r1, length))
        for lap in x["ids"]:
            st = s.corners.lap_corner_stats(lap)
            for k, cid in enumerate(x["cids"]):
                if k not in r_cache or k >= len(st):
                    continue
                s0, s1 = _span(x["star"], k)
                l0, l1 = _span(x["edges"][lap], k)
                tl_star = _true_time(x, lap, s0, s1)
                tl_own = _true_time(x, lap, l0, l1)
                if tl_star is None or tl_own is None:
                    continue
                et_r, ew_r, tr_star, de0, de1 = r_cache[k]
                rows.append([cid, int(lap == x["best"]), round(st[k].delta, 5),
                             round(tl_star - tr_star, 5), round(st[k].time - tl_own, 5),
                             round(tl_own - tl_star, 5), round(et_r, 5), round(ew_r, 5),
                             round(de0, 3), round(de1, 3)])
        return rows
    finally:
        s.clear_reference()


def within_rows(x: dict) -> list[list]:
    """Δbest's error on `x` alone: [cid, app Δbest, true Δ over S*] for every non-best clean lap."""
    out = []
    best = x["best"]
    for k, cid in enumerate(x["cids"]):
        s0, s1 = _span(x["star"], k)
        tb = _true_time(x, best, s0, s1)
        if tb is None:
            continue
        for lap in x["ids"]:
            if lap == best or k >= len(x["dbest"][lap]):
                continue
            tl = _true_time(x, lap, s0, s1)
            if tl is not None:
                out.append([cid, round(x["dbest"][lap][k][1], 5), round(tl - tb, 5)])
    return out


def measure_sigma(work: str, noise: float, seeds) -> tuple[float, float]:
    """(sigma_one, per-lap σ) over every ordered pair of days at `seeds` — the test's fixed check."""
    days = [_day(work, noise, seed, k + 1) for k, seed in enumerate(seeds)]
    rows = [r for x, y in itertools.permutations(days, 2) for r in pair_rows(x, y)]
    err = np.array([r[2] - r[3] for r in rows])
    lap_err = np.concatenate([_lap_err(d) for d in days])
    return math.sqrt(float(np.mean(err ** 2)) / 2.0), float(np.std(lap_err, ddof=1))


def _lap_err(d: dict) -> np.ndarray:
    s = d["s"]
    return np.array([s.lap_time(i) - (d["bounds"][i][1] - d["bounds"][i][0]) for i in d["ids"]])


def measure_set(work: str, noise: float, j: int, n_days: int) -> dict:
    days = [_day(work, noise, BASE_SEED + SET_STRIDE * j + k, 1000 * j + k + 1)
            for k in range(n_days)]
    return {"noise": noise, "set": j, "days": n_days,
            "lap_err": [float(e) for d in days for e in _lap_err(d)],
            "rows": [r for x, y in itertools.permutations(days, 2) for r in pair_rows(x, y)],
            "within": [r for d in days for r in within_rows(d)]}


def _rates(app: np.ndarray, true: np.ndarray, step: float) -> dict:
    printed = np.array([_printed(v) for v in app])
    col = np.abs(printed) >= step - 1e-9
    n = int(col.sum())
    return {"coloured": [n, len(app)],
            "of_which_true_level": int((col & (np.abs(true) < TRUE_LEVEL_S)).sum()),
            "of_which_wrong_sign": int((col & (np.sign(printed) != np.sign(true))
                                        & (np.abs(true) >= TRUE_LEVEL_S)).sum())}


def _power(err: np.ndarray, step: float) -> dict:
    out = {}
    for g in GAINS:
        printed = np.array([_printed(-g + e) for e in err])
        out[f"{g:.2f}"] = round(float(np.mean(printed <= -(step - 1e-9))), 3)
    return out


def _rms(a) -> float:
    a = np.asarray(a, float)
    return float(math.sqrt(np.mean(a ** 2)))


def _sd_about_corner_mean(rows: list, col: int) -> float:
    """sd of column `col` about each corner's own mean (a constant offset per corner, common to
    every figure, cancels in any difference)."""
    by: dict[int, list] = {}
    for r in rows:
        by.setdefault(r[0], []).append(r[col])
    ss = sum(float(np.sum((np.asarray(v) - np.mean(v)) ** 2)) for v in by.values())
    dof = sum(len(v) - 1 for v in by.values())
    return math.sqrt(ss / dof)


def summarize(recs: list[dict]) -> dict:
    out = {}
    for noise in sorted({r["noise"] for r in recs}):
        mine = sorted((r for r in recs if r["noise"] == noise), key=lambda r: r["set"])
        rows = [x for r in mine for x in r["rows"]]
        app = np.array([x[2] for x in rows])
        true = np.array([x[3] for x in rows])
        err = app - true
        sigma_diff = _rms(err)
        sigma_one = sigma_diff / math.sqrt(2.0)
        rule = round(stats.DIFF_2SIGMA_K * sigma_one, 2)
        per_set = []
        for r in mine:
            e = np.array([x[2] - x[3] for x in r["rows"]])
            per_set.append(round(_rms(e) / math.sqrt(2.0), 4))
        within = [x for r in mine for x in r["within"]]
        w_err = np.array([x[1] - x[2] for x in within])
        best = [x for x in rows if x[1]]
        b_app = np.array([x[2] for x in best])
        b_err = np.array([x[2] - x[3] for x in best])
        by_cid = {}
        for cid in sorted({x[0] for x in rows}):
            e = np.array([x[2] - x[3] for x in rows if x[0] == cid])
            by_cid[str(cid)] = round(_rms(e) / math.sqrt(2.0), 4)
        lap = np.concatenate([np.asarray(r["lap_err"], float) for r in mine])
        edges = np.abs(np.array([[x[8], x[9]] for x in rows])).ravel()
        e_l = np.array([x[4] + x[5] for x in rows])
        e_r = np.array([x[6] + x[7] for x in rows])
        # The step the Δref colour actually applies: the rule's, or the timing step where wider.
        shipped = stats.corner_delta_step(True) if hasattr(stats, "corner_delta_step") else None
        rec = {
            "days": sum(r["days"] for r in mine), "sets": len(mine), "cells": len(rows),
            "lap_sigma": round(float(np.std(lap, ddof=1)), 4),
            "sigma_diff": round(sigma_diff, 4), "err_mean": round(float(np.mean(err)), 4),
            "sigma_one": round(sigma_one, 4), "sigma_one_per_set": per_set,
            "step_rule": round(stats.DIFF_2SIGMA_K * sigma_one, 4), "step_rule_rounded": rule,
            "err_p95": round(float(np.percentile(np.abs(err), 95)), 4),
            "sigma_one_by_cid": by_cid,
            "within_sigma_diff": round(_rms(w_err), 4),
            "within_sigma_one": round(_rms(w_err) / math.sqrt(2.0), 4),
            "within_step_rule": round(2 * _rms(w_err), 3),
            # the model: err = e_L − e_ref, each = timing + window (vs S*), corner offsets removed
            "e_L_sd": round(_sd_about_corner_mean([[x[0], v] for x, v in zip(rows, e_l, strict=True)], 1), 4),
            "e_ref_sd": round(_sd_about_corner_mean([[x[0], v] for x, v in zip(rows, e_r, strict=True)], 1), 4),
            "e_L_timing_sd": round(_sd_about_corner_mean(rows, 4), 4),
            "e_L_window_sd": round(_sd_about_corner_mean(rows, 5), 4),
            "e_ref_timing_sd": round(_sd_about_corner_mean(rows, 6), 4),
            "e_ref_window_sd": round(_sd_about_corner_mean(rows, 7), 4),
            "ref_edge_m_median": round(float(np.median(edges)), 2),
            "ref_edge_m_p95": round(float(np.percentile(edges, 95)), 2),
            "true_sd": round(float(np.std(true, ddof=1)), 4),
            "best_vs_best_app_sd": round(float(np.std(b_app, ddof=1)), 4),
            "best_vs_best_app_p95": round(float(np.percentile(np.abs(b_app), 95)), 4),
            "best_vs_best_err_rms": round(_rms(b_err), 4),
            "best_vs_best_true_sd": round(float(np.std([x[3] for x in best], ddof=1)), 4),
            "rates_0.07": _rates(app, true, library.PB_PRECISION_S),
            f"rates_rule_{rule:.2f}": _rates(app, true, rule),
            "power_0.07": _power(err, library.PB_PRECISION_S),
            f"power_rule_{rule:.2f}": _power(err, rule),
        }
        if shipped is not None:
            rec[f"rates_shipped_{shipped:.2f}"] = _rates(app, true, shipped)
            rec[f"power_shipped_{shipped:.2f}"] = _power(err, shipped)
        for rec_name, row in stats.PUBLISHED_NOISE_SIGMAS.items():
            if row.noise == noise:
                # The published table's cells must be this probe's pooled σs at that noise.
                rec[f"table_{rec_name}_is_this_sigma"] = (
                    round(sigma_one, 4) == row.reference_corner
                    and round(_rms(w_err) / math.sqrt(2.0), 4) == row.corner_pass)
        if noise == REFERENCE_NOISE and hasattr(stats, "REFERENCE_CORNER_SIGMA_S"):
            rec["app_sigma_is_this_sigma"] = round(sigma_one, 4) == stats.REFERENCE_CORNER_SIGMA_S
            rec["app_step_is_this_step"] = rule == stats.REFERENCE_CORNER_LEVEL_S
        out[f"noise {noise:g}"] = rec
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--noises", default=",".join(f"{n:g}" for n in NOISES))
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
    work = tempfile.mkdtemp(prefix="reference_lap_corner_")
    recs = []
    try:
        for noise in (float(n) for n in a.noises.split(",")):
            for j in sets:
                rec = measure_set(work, noise, j, a.days)
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
