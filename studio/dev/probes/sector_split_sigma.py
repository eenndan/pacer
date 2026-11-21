"""sector_split_sigma: the σ of ONE sector split, measured the way every other step's σ is — on the
40-day synthetic grid at the one reference recording's GPS noise — and the step that σ gives the
split (`stats.SECTOR_LEVEL_S`) under the house rule (`stats.house_step`, 2·√2·σ).

WHY. Every other precision step is read off one table, `stats.PUBLISHED_NOISE_SIGMAS`, measured on
one grid (`focus_cross_session`'s: the demo circuit, habit-free race days of one driver, four seed
sets of ten days per noise, set j / day k at seed 401 + 100·j + k) at the GPS noise whose per-lap σ
matches each published recording's. The split's step was the exception: truth matrix row 2
(`tests/test_truth_matrix.py`, the max over three seeds of one recording's interior-split sd, at
the matrix's own noise 2) on two-chapter recordings at the default origin. This re-measures it on
the grid, at the sector-line placements the app meets.

WHAT A SPLIT IS TODAY. `Session.lap_sector_splits`: each boundary is where the lap's trace crosses
the sector line, between two fixes (TRUTH-5), and the elapsed time there is interpolated on the
lap's (odometer, time) chord, as the lap itself is timed. It does NOT snap to a 10 Hz fix.

PLACEMENTS (`CONFIGS`, lap metres on the circuit, lines square across it as `Truth.line_at` draws
them, applied through `apply_timing_lines_latlon` as a reload applies them):
  * golden — mid-straight on `Circuit.straights[2]` and `[5]`, the golden gate's and the truth
    matrix's placement; mid3 / mid4 — the 3- and 4-line mid-straight sets `sector_quickest` uses;
  * entry — 5 m before the end of the same two straights (braking into the corner);
  * apex — the apex of the corner after each (C3, C6); exit — 5 m into the two straights (leaving
    C2 and C5); hairpin — the apexes of the two slowest corners (C1 and C5);
  * the PR #675 review's six: maxg (244 m and 726 m), c4c5, c1_c5exit, short_c5 (26 m across the
    hairpin), short_straight (10 m mid-straight) and three_slow (three slow lines).

THE STATISTICS, one per consumer (each as app − truth over the SAME laps):
  * pair — one split against another of the same recording (the Quickest passes sector rows'
    level set and ★, the pass compare's Δ badges, the export's Δ plate): per day and sector column,
    the sd of the split's error about that column's mean (a constant bias cancels in a difference
    of two splits of one recording), pooled over columns, days and sets by sums of squares over
    degrees of freedom. That is σ; the house step is 2·√2·σ;
  * typical — a pass against its sector's typical pass (`typical_pass_lap` with this lap
    excluded: "Compare with my typical pass" on a sector, on a Quickest row or a pass menu): the
    RMS of the printed gap's error. σ_equiv = RMS / √2, so 2·RMS is its house step;
  * no consumer compares a split ACROSS recordings (Δref and the focus list are corners only).

Then, per noise and placement, the false calls and power of the three sector decisions (★, the
quickest row's typical offer, a pass menu's typical offer) under a list of candidate steps, on
habit-free days (false: the truth's gap under 0.03 s, or under the step itself) and on planted days
(C4 slowed on every lap but one, `quickest_bias.PLANTS`: the sector holding C4 has a true quickest
by about the gain), with the luck allowance (`stats.quickest_luck_s`) where the app adds it.

THE LUCK (at the reference noise): per placement, the quickest pass's bias by n on the typical and
next gaps and its two-parameter fit (`quickest_bias.bias_by_n` / `fit` on these splits); the fit at
the step's basis placement (`worst_split`) is what `stats.quickest_luck_s` ships for a sector, which
the summary checks (`luck_basis.app_fit_is_this_fit`).

MEASURED (defaults: 40 habit-free + 24 planted days per noise, at 1.9, 4.0 and 6.5; about an hour
as two processes; `--set-index 4,5,6,7 --plant-days 0` out of sample). The app's splits are
de-drifted since cool/sector-dedrift (`Session._sector_drift`); row field 3 is the same lap timed
as before (`uncorrected_splits`, summary `pair_uncorrected`). `--drift-tau 30` re-draws the
receiver's drift with a 30 s time constant (`sector_dedrift`'s error model), the split column's
basis (`stats.SPLIT_DRIFT_TAU_S`; the summary says `is_table_*` only at that τ). At the reference
noise and τ 30 s the split σ is 0.026-0.029 s pooled mid-straight and 0.040-0.042 s at the slow
lines; the worst sector of all 13 placements, a 26 m sector across C5's 0.0475 s, is
`stats.PUBLISHED_NOISE_SIGMAS`' split column (B 0.0989, A 0.1624), and the luck fit there is
typical +0.0007 + 0.0178·blom(n), next +0.0135 + 0.0077·blom(n); out of sample 0.0442 s. At the
shipped generator's τ 90 s: worst 0.0377 s (three slow lines; B 0.0796, A 0.1308), out of sample
0.0355. As timed before at τ 90 s: 0.0408 s golden pooled and 0.0881 s at the hairpin's worst
sector (B 0.1873, A 0.3062), #675's figures to the digit. The full tables, the call floor and the
decisions are in `stats.SECTOR_LEVEL_S`'s comment.

`--matrix` instead re-runs truth matrix row 2's own recordings (three seeds, noise 2, two chapters,
the default origin, golden placement) and reports each seed's per-lap interior-split errors over
every valid lap and over the SPLITS grid's counted laps: where the matrix's σ came from.

ARGV CONTRACT: named flags only. `--noises 1.9,4.0,6.5` (default NOISES), `--sets N` (default 4),
`--set-index J[,J…]` (only those sets; 4-7 are out of sample: seeds 801-1110), `--days N` (default 10), `--plant-days N` (per gain per set, default
2), `--drift-tau S` (the receiver drift's time constant; default the shipped generator), `--matrix`. Every recording is generated into ONE `mkdtemp` under $TMPDIR and removed before
exit; the app-support store is jailed and the sidecar write stubbed. Output is stdout: one
`{"day": …}` line per day, then the summary. `--summarize FILE[,FILE…]` READS such logs and prints
the summary; it writes nothing.

    pixi run python -m studio.dev.probes.sector_split_sigma
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")

from studio.dev._jail import divert_app_support  # noqa: E402

_JAIL = divert_app_support("sector_split_sigma_")

import numpy as np  # noqa: E402

from studio import chapters, sidecar, stats  # noqa: E402
from studio.dev import make_demo  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.dev.probes import quickest_bias as qb  # noqa: E402
from studio.dev.probes.clip_refusals import _true_splits  # noqa: E402
from studio.dev.probes.focus_cross_session import BASE_SEED, SET_STRIDE, _generate  # noqa: E402
from studio.dev.probes.quickest_bias import PLANT_CID, PLANT_LAP, PLANTS  # noqa: E402
from studio.session import Session  # noqa: E402

sidecar.save = lambda *a, **k: None   # nothing is ever written beside a recording here

NOISES = (1.9, 4.0, 6.5)          # recordings C, B and A (stats.PUBLISHED_NOISE_SIGMAS)
PLANT_SEED = 3011                 # quickest_bias's planted days, so the two probes plant alike
TRUE_LEVEL_S = 0.03               # a true gap this small is no difference a driver could act on
STEPS = (0.07, 0.10, 0.12, 0.13, 0.14, 0.15, 0.16, 0.18, 0.25, 0.30)


def _configs(c) -> dict[str, list[float]]:
    """Placement name → the sector lines' lap distances (m) on circuit `c`."""
    mid = [sum(st) / 2.0 for st in c.straights]
    apex = {k.cid: k.apex_s for k in c.corners}
    return {"golden": [mid[2], mid[5]], "mid3": [mid[1], mid[3], mid[5]],
            "mid4": [mid[1], mid[2], mid[4], mid[5]],
            "entry": [c.straights[2][1] - 5.0, c.straights[5][1] - 5.0],
            "apex": [apex[3], apex[6]],
            "exit": [c.straights[2][0] + 5.0, c.straights[5][0] + 5.0],
            "hairpin": [apex[1], apex[5]],
            # the independent review's placements (PR #675): the drift model's worst pair, C4's
            # apex to just past C5's, C1's apex to 5 m out of C5, a 26 m sector across the
            # hairpin, a 10 m sector mid-straight, and three slow lines
            "maxg": [244.0, 726.0], "c4c5": [578.0, 742.0],
            "c1_c5exit": [apex[1], c.straights[5][0] + 5.0], "short_c5": [724.0, 750.0],
            "short_straight": [400.0, 410.0], "three_slow": [244.0, 578.0, 730.0]}


def _r(x) -> float:
    return round(float(x), 5)


def splits_with(s: Session, lap: int, drift) -> list[float]:
    """`lap`'s splits with `drift(lap_id) -> (dx, dy)` taken off each trace in place of the app's
    own `Session._sector_drift`, read through the app's `lap_sector_splits`; the session is put
    back after."""
    s._sector_drift = drift
    s._sector_collapse_cache = None
    try:
        return s.lap_sector_splits(lap)
    finally:
        del s._sector_drift
        s._sector_collapse_cache = None


def uncorrected_splits(s: Session, lap: int) -> list[float]:
    """`lap`'s splits as they were timed before cool/sector-dedrift: its trace crossed with the
    sector lines WITHOUT its receiver shift taken off (`Session._sector_drift` held at (0, 0))."""
    return splits_with(s, lap, lambda _lap: (0.0, 0.0))


def placements(s: Session, truth) -> dict:
    """Every placement on session `s`: its lines' metres and true speeds, and per counted lap of
    the SPLITS grid (lap, app splits, true splits, the splits as timed before the de-drift
    `uncorrected_splits`), with the grid's column medians."""
    out = {}
    start, _ = s.timing_lines_latlon()
    for name, metres in _configs(truth.circuit).items():
        lines = [truth.line_at(m) for m in metres]
        if not s.apply_timing_lines_latlon(start, lines, confirmed=True):
            out[name] = None
            continue
        sm, true = s.split_matrix(), _true_splits(s, truth, lines)
        rows = []
        for lap, cells in zip([] if sm is None else sm.lap_ids, [] if sm is None else sm.cells,
                              strict=True):
            if lap in true and None not in cells and len(true[lap]) == len(cells):
                rows.append([int(lap), [_r(v) for v in cells], [_r(v) for v in true[lap]],
                             [_r(v) for v in uncorrected_splits(s, lap)]])
        speeds = [float(np.median(truth.speed_at(truth.times_at(m)[1:-1]))) for m in metres]
        out[name] = {"metres": [round(m, 2) for m in metres],
                     "speed_ms": [round(v, 2) for v in speeds],
                     "medians": None if sm is None else [_r(v) for v in sm.medians], "rows": rows}
    return out


def day_record(work: str, noise: float, seed: int, day_no: int, plant: float | None = None,
               drift_tau: float | None = None) -> dict:
    habit = None if plant is None else sg.Habit(cid=PLANT_CID, grip=PLANTS[plant], brake=1.0,
                                                clean_laps=(PLANT_LAP,))
    if drift_tau is None:
        path = _generate(work, noise, seed, day_no, habit)
    else:
        # the receiver's drift re-drawn with this time constant (sector_dedrift's error model;
        # every other term of the recording is the shipped generator's)
        from studio.dev.probes import sector_dedrift
        sector_dedrift._patch(f"tau{drift_tau:g}")
        try:
            path = _generate(work, noise, seed, day_no, habit)
        finally:
            sg._gps_rows = sector_dedrift._ORIG_GPS_ROWS
    s = Session.load(chapters.discover_siblings(path))
    truth = sg.simulate(seed, 14, origin=make_demo.DEMO_ORIGIN, habit=habit)
    return {"noise": noise, "seed": seed, "plant": plant, "set": (seed - BASE_SEED) // SET_STRIDE
            if plant is None else None, "drift_tau": drift_tau, "placements": placements(s, truth)}


# ------------------------------------------------------------------------------- the summary
def _ss(days, name: str, app: int = 1) -> tuple[list[float], list[int]]:
    """Per sector column: (sum of squares of the error about each day's column mean, dof), the
    app's splits read from row field `app` (1: as shipped, 3: as timed before the de-drift)."""
    ss: list[float] = []
    dof: list[int] = []
    for d in days:
        p = d["placements"].get(name)
        if not p or len(p["rows"]) < 2 or len(p["rows"][0]) <= app:
            continue
        err = np.array([np.subtract(r[app], r[2]) for r in p["rows"]])
        if not ss:
            ss, dof = [0.0] * err.shape[1], [0] * err.shape[1]
        for c in range(err.shape[1]):
            ss[c] += float(np.sum((err[:, c] - err[:, c].mean()) ** 2))
            dof[c] += err.shape[0] - 1
    return ss, dof


def pair_sigma(days, name: str, app: int = 1) -> dict:
    ss, dof = _ss(days, name, app)
    if not ss:
        return {}
    cols = [math.sqrt(a / b) for a, b in zip(ss, dof, strict=True)]
    pooled = math.sqrt(sum(ss) / sum(dof))
    sets = {}
    for j in sorted({d["set"] for d in days if d["set"] is not None}):
        s2, f2 = _ss([d for d in days if d["set"] == j], name, app)
        sets[j] = round(math.sqrt(sum(s2) / sum(f2)), 4) if s2 else None
    interior = cols[1:-1]
    return {"pooled": round(pooled, 4), "columns": [round(c, 4) for c in cols],
            "worst": round(max(cols), 4),
            "interior": round(math.sqrt(sum(ss[1:-1]) / sum(dof[1:-1])), 4) if interior else None,
            "sets": sets, "step_pooled": stats.house_step(pooled),
            "step_worst": stats.house_step(max(cols))}


def _counted(p, c: int) -> tuple[dict[int, float], dict[int, float]]:
    return ({r[0]: r[1][c] for r in p["rows"]}, {r[0]: r[2][c] for r in p["rows"]})


def typical_rms(days, name: str) -> dict:
    """Every counted pass against its typical pass (this lap excluded), printed gap − true gap."""
    errs = []
    for d in days:
        p = d["placements"].get(name)
        if not p or len(p["rows"]) < stats.MATRIX_MIN_LAPS:
            continue
        for c in range(len(p["medians"])):
            app, true = _counted(p, c)
            for lap in app:
                v = stats.pass_vs_typical_of(stats.sector_key(c), lap, app, p["medians"][c], 0.3)
                if v is not None:
                    errs.append(v.gap_s - (true[v.typical_lap_id] - true[lap]))
    if not errs:
        return {}
    rms = math.sqrt(float(np.mean(np.square(errs))))
    return {"n": len(errs), "mean": round(float(np.mean(errs)), 4), "rms": round(rms, 4),
            "sigma_equiv": round(rms / math.sqrt(2), 4), "step": round(2 * rms, 2)}


def _sector_decisions(app, true, med, key, step):
    """(★, its truth margin, quickest-row offer, its true gap, [(menu offer, true gap)…])."""
    order = sorted((t, lap) for lap, t in app.items())
    q = order[0][1]
    nxt = (round(order[1][0] * 1000) - round(order[0][0] * 1000)) / 1000
    luck_next = stats.quickest_luck_s(key, len(app), "next")
    star = not stats.is_level(nxt, stats.with_luck(step, luck_next))
    margin = min(true[lap] for lap in app if lap != q) - true[q]
    vq = stats.pass_vs_typical_of(key, q, app, med, step)
    offer_q = vq is not None and vq.gap_s > 0 and not vq.level
    tq = None if vq is None else true[vq.typical_lap_id] - true[q]
    menu = []
    for lap in app:
        v = stats.pass_vs_typical_of(key, lap, app, med, step)
        if v is not None:
            menu.append((lap, not v.level, true[v.typical_lap_id] - true[lap], v.gap_s))
    return q, star, margin, offer_q, tq, menu


def calls(days, name: str, step: float) -> dict:
    out = {"sectors": 0, "stars": 0, "star_false_by_claim": 0, "star_true_under_0.03": 0,
           "offers": 0, "offer_true_under_step": 0, "offer_true_under_0.03": 0,
           "offer_wrong_sign": 0, "menu_cells": 0, "menu_offers": 0, "menu_true_under_0.03": 0,
           "menu_true_under_step": 0, "menu_wrong_sign": 0}
    power: dict[str, list[int]] = {}
    for d in days:
        p = d["placements"].get(name)
        if not p or len(p["rows"]) < stats.MATRIX_MIN_LAPS:
            continue
        for c in range(len(p["medians"])):
            app, true = _counted(p, c)
            q, star, margin, offer_q, tq, menu = _sector_decisions(
                app, true, p["medians"][c], stats.sector_key(c), step)
            if d["plant"] is None:
                out["sectors"] += 1
                out["stars"] += star
                out["star_false_by_claim"] += star and margin < step
                out["star_true_under_0.03"] += star and margin < TRUE_LEVEL_S
                out["offers"] += offer_q
                out["offer_true_under_step"] += offer_q and tq < step
                out["offer_true_under_0.03"] += offer_q and tq < TRUE_LEVEL_S
                out["offer_wrong_sign"] += offer_q and tq < 0
                for _lap, offer, tg, g in menu:
                    out["menu_cells"] += 1
                    out["menu_offers"] += offer
                    out["menu_true_under_0.03"] += offer and abs(tg) < TRUE_LEVEL_S
                    out["menu_true_under_step"] += offer and abs(tg) < step
                    out["menu_wrong_sign"] += offer and tg * g < 0
                continue
            # planted: only the sector whose true quickest is the clean lap by the most
            gains = {}
            for cc in range(len(p["medians"])):
                t = sorted(r[2][cc] for r in p["rows"])
                gains[cc] = float(np.median(t)) - t[0]
            if max(gains, key=gains.get) != c:
                continue
            true_q = min(true, key=true.get)
            hit = q == true_q
            menu_hit = any(off and g > 0 for lap, off, _tg, g in menu if lap == true_q)
            row = power.setdefault(f"{d['plant']:.2f}", [0, 0, 0, 0])
            row[0] += 1
            row[1] += hit and star
            row[2] += hit and offer_q
            row[3] += menu_hit
    out["power"] = {g: {"days": v[0], "starred": v[1], "quickest_offer": v[2], "menu_offer": v[3]}
                    for g, v in sorted(power.items())}
    return out


def _luck_days(days, name: str) -> list[dict]:
    """`days` at placement `name` in `quickest_bias`'s day shape (its `bias_by_n` and `fit` read
    the quickest pass's luck exactly as they read the mid-straight sectors' there)."""
    out = []
    for d in days:
        p = d["placements"].get(name)
        if not p:
            continue
        out.append({"plant": d["plant"], "seed": d["seed"], "sectors": {
            str(stats.sector_key(c)): [[r[0], r[1][c], r[2][c]] for r in p["rows"]]
            for c in range(len(p["medians"]))}})
    return out


def luck(days, name: str) -> dict:
    """The quickest pass's luck at placement `name`: bias by n of the typical and next gaps (app −
    truth) and the two-parameter fit `stats.quickest_luck_s` ships for sectors, a + b·blom(n)."""
    by_n = qb.bias_by_n(_luck_days(days, name), "sectors")
    if not by_n:
        return {}
    fits = {g: qb.fit(by_n, g) for g in ("typical", "next")}
    top = max(by_n)
    return {"fit": {g: (f["a"], f["b"]) for g, f in fits.items()},
            "max_abs_residual_in_se": {g: f["max_abs_residual_in_se"] for g, f in fits.items()},
            "at_n": {n: (by_n[n]["typical"], by_n[n]["next"]) for n in (5, 9, top) if n in by_n}}


def measure_luck(work: str, noise: float, seeds, name: str, ns,
                 drift_tau: float | None = stats.SPLIT_DRIFT_TAU_S) -> dict[int, tuple[float, float]]:
    """{n: (typical bias, next bias)} at placement `name` over habit-free days at `seeds` — the
    small fixed re-measure tests/test_quickest_passes.py checks the shipped sector luck against."""
    days = []
    for k, seed in enumerate(seeds):
        days.append(day_record(work, noise, seed, k + 1, drift_tau=drift_tau))
        for entry in os.listdir(work):
            shutil.rmtree(os.path.join(work, entry), ignore_errors=True)
    by_n = qb.bias_by_n(_luck_days(days, name), "sectors")
    return {n: (by_n[n]["typical"], by_n[n]["next"]) for n in ns if n in by_n}


def summarize(days: list[dict]) -> dict:
    out = {}
    for noise in sorted({d["noise"] for d in days}):
        mine = [d for d in days if d["noise"] == noise]
        free = [d for d in mine if d["plant"] is None]
        taus = {d.get("drift_tau") for d in mine}
        # the table and the shipped luck are read at ONE drift time constant,
        # `stats.SPLIT_DRIFT_TAU_S`; a grid at another (None: the shipped generator's 90 s) is a
        # comparison and never "is the table"
        tau_is_basis = taus == {stats.SPLIT_DRIFT_TAU_S}
        rec = {"habit_free_days": len(free), "planted_days": len(mine) - len(free),
               "drift_tau": sorted(taus, key=str)}
        names = list(free[0]["placements"]) if free else []
        for name in names:
            some = next((d["placements"][name] for d in free if d["placements"].get(name)), None)
            rec[name] = {"metres": some and some["metres"],
                         "speed_ms": some and some["speed_ms"],
                         "pair": pair_sigma(free, name), "typical": typical_rms(free, name),
                         "pair_uncorrected": pair_sigma(free, name, app=3),
                         "calls": {f"{st:g}": calls(mine, name, st) for st in STEPS}}
        # The σ one step must cover: the worst sector of the worst placement measured, which is
        # `stats.PUBLISHED_NOISE_SIGMAS`' split column at the noise matching each recording.
        measured = [(rec[n]["pair"]["worst"], n) for n in names if rec[n]["pair"]]
        worst = max(measured) if measured else None
        if worst is not None:
            rec["worst_split"] = {"sigma": worst[0], "placement": worst[1],
                                  "step": stats.house_step(worst[0])}
            for rec_name, row in stats.PUBLISHED_NOISE_SIGMAS.items():
                if row.noise == noise:
                    rec["worst_split"][f"is_table_{rec_name}"] = (row.split == worst[0]
                                                                 and tau_is_basis)
        # The quickest pass's luck, at the reference noise: per placement, and the fit at the
        # step's own basis placement, which is what `stats.quickest_luck_s` ships for a sector.
        if worst is not None and noise == stats.REFERENCE_SIGMAS.noise:
            rec["luck"] = {n: luck(mine, n) for n in names}
            basis = rec["luck"][worst[1]]
            rec["luck_basis"] = {"placement": worst[1], **basis, "app_fit_is_this_fit": tau_is_basis
                                 and all(stats._QUICKEST_LUCK_FIT[("sector", g)]
                                         == tuple(basis["fit"][g]) for g in ("typical", "next"))}
        out[f"noise {noise:g}"] = rec
    return out


# --------------------------------------------------------------- truth matrix row 2, re-run
def matrix_rerun(work: str) -> dict:
    """Row 2's own recordings (three seeds, noise 2, two chapters, the default origin) at the
    golden placement: each seed's interior-split error per lap, over every valid lap and over the
    SPLITS grid's counted laps, and the same at one chapter."""
    out = {}
    for chap in (2, 1):
        for seed in (sg.DEFAULT_SEED, 1, 2):
            rec = sg.generate(os.path.join(work, f"m{seed}_{chap}"), seed=seed, gps_noise=2.0,
                              chapters=chap)
            s = Session.load(chapters.discover_siblings(rec.paths[0]))
            t = rec.truth
            mid = [sum(st) / 2.0 for st in t.circuit.straights]
            lines = [t.line_at(mid[2]), t.line_at(mid[5])]
            start, _ = s.timing_lines_latlon()
            assert s.apply_timing_lines_latlon(start, lines, confirmed=True)
            true = _true_splits(s, t, lines)
            every = {i: (np.subtract(s.lap_sector_splits(i), true[i])) for i in true}
            counted = set(s.split_matrix().lap_ids)
            e_all = np.array([every[i][1] for i in sorted(every)])
            e_cnt = np.array([every[i][1] for i in sorted(every) if i in counted])
            out[f"chapters {chap} seed {seed}"] = {
                "valid_laps": len(e_all), "sd_valid": round(float(e_all.std()), 4),
                "counted_laps": len(e_cnt), "sd_counted": round(float(e_cnt.std()), 4),
                "s2_err_by_lap": {int(i): round(float(every[i][1]), 3) for i in sorted(every)},
                "not_counted": sorted(set(every) - counted),
                "lap_err_sd": round(float(np.std([s.lap_time(i) - sum(true[i]) for i in true])), 4)}
            print(json.dumps({"matrix": out[f"chapters {chap} seed {seed}"],
                              "key": f"chapters {chap} seed {seed}"}), flush=True)
            shutil.rmtree(os.path.join(work, f"m{seed}_{chap}"), ignore_errors=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--noises", default=",".join(f"{n:g}" for n in NOISES))
    ap.add_argument("--sets", type=int, default=4)
    ap.add_argument("--set-index", default="")
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--plant-days", type=int, default=2)
    ap.add_argument("--matrix", action="store_true")
    ap.add_argument("--summarize", default="")
    ap.add_argument("--drift-tau", type=float, default=None)
    a = ap.parse_args()
    if a.summarize:
        days = []
        for path in a.summarize.split(","):
            with open(path, encoding="utf-8") as f:
                days += [json.loads(ln)["day"] for ln in f if ln.startswith('{"day"')]
        print(json.dumps(summarize(days)))
        return 0
    work = tempfile.mkdtemp(prefix="sector_split_sigma_")
    try:
        if a.matrix:
            print(json.dumps({"matrix_summary": matrix_rerun(work)}))
            return 0
        sets = ([int(j) for j in a.set_index.split(",")] if a.set_index
                else list(range(a.sets)))
        days = []
        for noise in (float(n) for n in a.noises.split(",")):
            for j in sets:
                # A day's number dates its recording; past ~4000 days the MP4's 32-bit date
                # overflows, so the out-of-sample sets (4+) are numbered from 3500.
                base = 1000 * j if j < 4 else 3500 + 100 * (j - 4)
                jobs = [(BASE_SEED + SET_STRIDE * j + k, base + k + 1, None)
                        for k in range(a.days)]
                jobs += [(PLANT_SEED + SET_STRIDE * j + 10 * gi + k, 1000 * j + 100 + 10 * gi + k,
                          g) for gi, g in enumerate(PLANTS) for k in range(a.plant_days)]
                for seed, day_no, plant in jobs:
                    rec = day_record(work, noise, seed, day_no, plant, a.drift_tau)
                    print(json.dumps({"day": rec}), flush=True)
                    days.append(rec)
                    for name in os.listdir(work):
                        shutil.rmtree(os.path.join(work, name), ignore_errors=True)
        print(json.dumps(summarize(days)))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
