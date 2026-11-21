"""selection_surfaces: how much of what five surfaces claim is the selection effect alone — a figure
read off a MINIMUM (or maximum) of noisy figures — on habit-free synthetic days at the one reference
recording's GPS noise, and what each would lose to a correction.

WHY. v0.21.0 (#670) gave the quickest of n passes its luck (`stats.quickest_luck_s`) where a gap is
read off it: the ★ and a quickest row's typical offer. DECISIONS §24 lists the surfaces that read a
figure off a selected extreme and were not corrected:

  (a) Coaching's time lost per corner: the median of (pass − the BEST LAP's pass), and its ranking;
  (b) the story moment "Biggest single-corner loss … over your typical pass": the largest of every
      lap × corner gap to the corner's typical pass;
  (c) the share card: the ideal gap ("+0.47 s vs your ideal lap") and Coaching's first row;
  (d) the Stats sector hovers, "… against the sector best": a split minus the column's quickest;
  (e) the ideal lap's gap to the best lap ("on the table · vs your best"): a SUM OF MINIMA.

Each is read here exactly as the app reads it (its own accessor) and the same figure is rebuilt on
the truth for the same laps; app − truth is the error, its mean the bias.

THE GRID. #667's (`focus_cross_session`): the demo circuit, one driver, 14 laps, set j / day k at
seed 401 + 100·j + k (`--sets` × `--days`, default 4 × 10), habit-free; plus planted days
(`--plant-days` per gain per set) with one lap's C4 truly quicker than the rest (`quickest_bias`'s
PLANTS), for what a correction would cost in true claims.

THE TRUTH, per surface.
  * Corner passes: each counted pass timed on truth over the best lap's corner window mapped to
    arc length (S*, as `reference_lap_corner` / `quickest_bias` do).
  * The ideal: the SAME composite on the true clock — every partition edge (`SegmentBests.s_edges`
    on the best lap's odometer) mapped to the truth's arc length, every lap's true time between
    those, the minimum per segment over the same admitted and resolved cells
    (`tests/test_truth_matrix._ideal`'s mapping). For n below the day's count, n laps are drawn
    (`DRAWS` seeded draws) and both composites re-taken over them, with the best lap the quickest
    of the n as timed.
  * Splits: three sector lines mid-straights 1, 3, 5, the truth's own crossings
    (`clip_refusals._true_splits`).

ARGV CONTRACT: named flags only — `--noise` (default the reference), `--sets N`, `--set-index J`,
`--days N`, `--plant-days N`; `--summarize FILE[,FILE…]` READS such stdout logs and prints the
summary, writing nothing. Every recording is generated into ONE `mkdtemp` under $TMPDIR and removed
before exit; the app-support store is jailed and the sidecar write stubbed. Output is stdout: one
`{"day": …}` line per day, then the summary as the last line.

    pixi run python -m studio.dev.probes.selection_surfaces
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

_JAIL = divert_app_support("selection_surfaces_")

import numpy as np  # noqa: E402

from studio import chapters, coaching, sidecar, stats  # noqa: E402
from studio.dev import make_demo  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.dev.probes.clip_refusals import _true_splits  # noqa: E402
from studio.dev.probes.focus_cross_session import (  # noqa: E402
    BASE_SEED,
    REFERENCE_NOISE,
    SET_STRIDE,
    _generate,
    _lap_bounds,
)
from studio.dev.probes.quickest_bias import PLANT_CID, PLANT_LAP, PLANT_SEED, PLANTS  # noqa: E402
from studio.dev.probes.reference_lap_corner import _true_time, _truth_s_at  # noqa: E402
from studio.session import Session  # noqa: E402

sidecar.save = lambda *a, **k: None   # nothing is ever written beside a recording here

SECTOR_STRAIGHTS = (1, 3, 5)
DRAWS = 24                 # seeded draws of n laps per day and n below the full count
TRUE_LEVEL_S = 0.03        # a true gap this small is no difference a driver could act on
CHECK_SEEDS = (401, 402, 403, 404)   # the small set tests/test_stats_ideal.py re-measures


def _r(x) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), 5)


def _ideal_truth(s: Session, d: dict, sb) -> np.ndarray:
    """(laps, segments) TRUE segment times of `sb.lap_ids` over the same partition edges."""
    truth, length = d["truth"], d["truth"].circuit.length
    best = s.best_lap_id()
    times_b, _x, _y, _v, cum_b = s._lap_columns(best)
    total_ref = float(cum_b[-1])
    starts = {i: float(s._lap_columns(i)[0][0]) for i in sb.lap_ids if i in d["bounds"]}
    offset = float(np.median([starts[i] - d["bounds"][i][0] for i in starts]))
    out = np.full((len(sb.lap_ids), len(sb.s_edges) - 1), np.nan)
    s_laps = []
    for e in sb.s_edges:
        if 0.0 < e < 1.0:
            t_true = float(np.interp(e * total_ref, cum_b, times_b)) - offset
            s_laps.append((truth.s_start + float(np.interp(t_true, truth.t_nodes,
                                                           truth.d_nodes))) % length)
        else:
            s_laps.append(None)
    for r, lap in enumerate(sb.lap_ids):
        if lap not in d["bounds"]:
            continue
        t0, t1 = d["bounds"][lap]
        pts = []
        for e, sl in zip(sb.s_edges, s_laps, strict=True):
            if sl is None:
                pts.append(t0 if e <= 0.0 else t1)
                continue
            ts = truth.times_at(sl)
            hit = ts[(ts > t0) & (ts < t1)]
            pts.append(float(hit[0]) if len(hit) == 1 else np.nan)
        out[r] = np.diff(pts)
    return out


def day_record(work: str, noise: float, seed: int, day_no: int, plant: float | None = None) -> dict:
    """One synthetic day: every figure the five surfaces read, app and truth."""
    from studio import share_card

    habit = None if plant is None else sg.Habit(cid=PLANT_CID, grip=PLANTS[plant], brake=1.0,
                                                clean_laps=(PLANT_LAP,))
    path = _generate(work, noise, seed, day_no, habit)
    s = Session.load(chapters.discover_siblings(path))
    truth = sg.simulate(seed, 14, origin=make_demo.DEMO_ORIGIN, habit=habit)
    d = {"truth": truth, "bounds": _lap_bounds(truth, s)}
    best = s.best_lap_id()
    rec = {"noise": noise, "seed": seed, "plant": plant, "best": int(best),
           "lap_app": {str(i): _r(s.lap_time(i)) for i in d["bounds"]},
           "lap_true": {str(i): _r(b - a) for i, (a, b) in d["bounds"].items()}}
    # corner passes: the CORNERS BY LAP grid, app and truth (S*)
    corner_list, _total = s.corners.basis()
    frame = np.array([b for c in corner_list for b in (float(c.enter), float(c.exit))])
    star = _truth_s_at(s, truth, best, frame)
    m = s.corner_matrix()
    rec["corners"] = {}
    for col, cid in enumerate([] if m is None else m.cids):
        rows = []
        for r, lap in enumerate(m.lap_ids):
            val = m.cells[r][col]
            if val is None or lap not in d["bounds"]:
                continue
            tt = _true_time(d, lap, float(star[2 * col]), float(star[2 * col + 1]))
            if tt is not None:
                rows.append([int(lap), _r(val), _r(tt), int(bool(m.resolved[r][col]))])
        rec["corners"][str(cid)] = rows
    # (a) Coaching, as the app reads it
    opps = s.coaching_opportunities()
    rec["coach"] = [{"cid": int(o.cid), "lost": _r(o.time_lost), "ranked": bool(o.evidence.ranked),
                     "abstain": o.evidence.abstain, "n": int(o.evidence.n_laps)}
                    for o in opps.rows]
    rec["coach_short"] = [int(o.cid) for o in coaching.shortlist(opps)]
    rec["coach_lead_ties"] = ([int(o.cid) for o in coaching.lead_ties(list(opps.rows),
                                                                      rec["coach_short"][0])]
                              if rec["coach_short"] else [])
    rec["coach_headline"] = coaching.headline(opps)
    # (b) the biggest single-corner loss moment
    bl = s.session_moments().biggest_loss
    rec["biggest"] = None if bl is None else {
        "lap": int(bl.lap_ids[0]), "cid": int(bl.cid), "value": _r(bl.value),
        "tie": [[int(t.lap_ids[0]), int(t.cid), _r(t.value)] for t in bl.tie]}
    # (e) the ideal: the app's composite and the same composite on truth
    sb = s.ideal_segment_bests()
    if sb is not None:
        mask = np.asarray(sb.admitted) & np.asarray(sb.resolved)
        tseg = _ideal_truth(s, d, sb)
        rec["ideal"] = {"lap_ids": [int(i) for i in sb.lap_ids],
                        "app": [[_r(x) for x in row] for row in np.asarray(sb.times, float)],
                        "true": [[_r(x) for x in row] for row in tseg],
                        "mask": [[int(x) for x in row] for row in mask],
                        "total": _r(sb.total), "segments": len(sb.bests),
                        "single_donor": sb.single_donor_id() is not None,
                        "app_gap": _r(s.lap_time(best) - sb.total)}
    # (c) the share card, as built
    cd = share_card.card_data(s)
    rec["card"] = {"delta_ideal": _r(cd.delta_to_ideal_s),
                   "top": None if cd.top_opp is None else {
                       "cids": list(cd.top_opp.cids), "lost": _r(cd.top_opp.time_lost_s)}}
    # (d) the sector lines last: they re-segment the session
    lines = [truth.line_at(sum(truth.circuit.straights[j]) / 2.0) for j in SECTOR_STRAIGHTS]
    start, _ = s.timing_lines_latlon()
    rec["sectors"] = {}
    if s.apply_timing_lines_latlon(start, lines, confirmed=True):
        true_sp = _true_splits(s, truth, lines)
        sm = s.split_matrix()
        for col in range(0 if sm is None else sm.columns):
            rows = [[int(lap), _r(sm.cells[r][col]), _r(true_sp[lap][col])]
                    for r, lap in enumerate(sm.lap_ids)
                    if sm.cells[r][col] is not None and lap in true_sp]
            rec["sectors"][str(col)] = {"rows": rows, "best": _r(sm.bests[col]),
                                        "best_lap": int(sm.best_lap[col])}
    return rec


# ------------------------------------------------------------------------------ the summary
def _composite(app, true, mask, rows) -> tuple[float, float] | None:
    """(app ideal, true ideal) over `rows` of the matrices: per segment the minimum over the cells
    that may donate, falling back to every cell of a segment none may (the model's fallback)."""
    a, t, k = app[rows], true[rows], mask[rows]
    ai, ti = 0.0, 0.0
    for j in range(a.shape[1]):
        use = k[:, j] if k[:, j].any() else np.ones(len(rows), bool)
        ok = use & np.isfinite(a[:, j]) & np.isfinite(t[:, j])
        if not ok.any():
            return None
        ai += float(a[ok, j].min())
        ti += float(t[ok, j].min())
    return ai, ti


def ideal_rows(day: dict, n: int, rng) -> list[dict]:
    """Per draw of n of the day's laps: the app's gap (quickest as timed − app composite), the true
    gap (that lap's true time − true composite), each composite's error and each gap's."""
    rec = day.get("ideal")
    if not rec or rec["single_donor"]:
        return []
    ids = rec["lap_ids"]
    app = np.array(rec["app"], float)
    true = np.array(rec["true"], float)
    mask = np.array(rec["mask"], bool)
    lap_app = {int(k): v for k, v in day["lap_app"].items()}
    lap_true = {int(k): v for k, v in day["lap_true"].items()}
    usable = [r for r, i in enumerate(ids) if i in lap_app and np.isfinite(true[r]).all()]
    n = len(usable) if n is None else n
    if len(usable) < n:
        return []
    picks = [usable] if n == len(usable) else [
        sorted(rng.choice(usable, size=n, replace=False).tolist()) for _ in range(DRAWS)]
    out = []
    for rows in picks:
        got = _composite(app, true, mask, rows)
        if got is None:
            continue
        best = min(rows, key=lambda r: lap_app[ids[r]])
        gap_app = lap_app[ids[best]] - got[0]
        gap_true = lap_true[ids[best]] - got[1]
        out.append({"n": n, "gap_app": gap_app, "gap_true": gap_true,
                    "ideal_err": got[0] - got[1], "best_err": lap_app[ids[best]] - lap_true[ids[best]]})
    return out


def blom(n: int) -> float:
    from statistics import NormalDist
    return 0.0 if n < 2 else NormalDist().inv_cdf((n - 0.375) / (n + 0.25))


def _stats(x) -> dict:
    a = np.asarray(x, float)
    if not len(a):
        return {"n": 0}
    return {"n": len(a), "mean": round(float(a.mean()), 4),
            "se": round(float(a.std(ddof=1) / math.sqrt(len(a))), 4) if len(a) > 1 else None,
            "sd": round(float(a.std(ddof=1)), 4) if len(a) > 1 else None,
            "p05": round(float(np.percentile(a, 5)), 4), "p50": round(float(np.median(a)), 4),
            "p95": round(float(np.percentile(a, 95)), 4)}


def summarize_ideal(days: list[dict]) -> dict:
    free = [d for d in days if d["plant"] is None]
    by_n: dict[int, list[dict]] = {}
    for d in free:
        rng = np.random.default_rng([d["seed"], 77])
        for n in range(stats.MATRIX_MIN_LAPS, 15):
            by_n.setdefault(n, []).extend(ideal_rows(d, n, rng))
    bias = {n: {"draws": len(v), **{k: _stats([x[k] for x in v])["mean"] for k in
                                    ("gap_app", "gap_true", "ideal_err", "best_err")},
                "gap_err": _stats([x["gap_app"] - x["gap_true"] for x in v])}
            for n, v in sorted(by_n.items()) if v}
    # fit the gap's bias a + b·blom(n), weighted by draws (n with ≥ 100 draws)
    ns = [n for n in bias if bias[n]["draws"] >= 100] or list(bias)
    w = np.array([bias[n]["draws"] for n in ns], float)
    x = np.array([blom(n) for n in ns])
    y = np.array([bias[n]["gap_err"]["mean"] for n in ns])
    a_mat = np.stack([np.ones_like(x), x], axis=1) * np.sqrt(w)[:, None]
    (a, b), *_ = np.linalg.lstsq(a_mat, y * np.sqrt(w), rcond=None)
    res = y - (a + b * x)
    full = [ideal_rows(d, None, None) for d in free if d.get("ideal")]
    full = [r[0] for r in full if r]
    errs = np.array([r["gap_app"] - r["gap_true"] for r in full])
    apps = np.array([r["gap_app"] for r in full])
    trues = np.array([r["gap_true"] for r in full])
    claims = {
        "days": len(full),
        "app_gap": _stats(apps), "true_gap": _stats(trues), "gap_err": _stats(errs),
        "selection_share_of_app_gap": round(float(errs.mean() / apps.mean()), 3) if len(apps) else None,
        "over_by_more_than_0.07": int(np.sum(errs > stats.PB_PRECISION_S)),
        "over_by_more_than_half_true": int(np.sum(errs > 0.5 * trues)),
    }
    planted = {}
    for d in days:
        if d["plant"] is None or not d.get("ideal"):
            continue
        r = ideal_rows(d, None, None)
        if r:
            planted.setdefault(f"{d['plant']:.2f}", []).append(r[0])
    plant_out = {g: {"days": len(v), "app_gap": _stats([x["gap_app"] for x in v])["mean"],
                     "true_gap": _stats([x["gap_true"] for x in v])["mean"],
                     "gap_err": _stats([x["gap_app"] - x["gap_true"] for x in v])["mean"]}
                 for g, v in sorted(planted.items())}
    return {"bias_by_n": bias,
            "fit": {"a": round(float(a), 4), "b": round(float(b), 4), "n": [min(ns), max(ns)],
                    "residual_by_n": {int(n): round(float(r_), 4) for n, r_ in zip(ns, res,
                                                                                   strict=True)}},
            "full_count": claims, "planted": plant_out}


def summarize_coaching(days: list[dict]) -> dict:
    """(a): each shown row's time lost as printed vs the same laps' truth (median true pass − the
    best lap's true pass); the lead as the app names it vs the truly biggest loss; the headline's
    sum vs the same corners on truth."""
    errs, rows_n, false_lead, wrong_sign, truly_under = [], 0, 0, 0, 0
    head_app, head_true, ranked_n = [], [], 0
    rank_err, lead_app, lead_err, rows_app = [], [], [], []
    for d in days:
        if d["plant"] is not None:
            continue
        best = d["best"]
        truth_lost = {}
        for cid, rows in d["corners"].items():
            tb = next((x[2] for x in rows if x[0] == best), None)
            if tb is None:
                continue
            truth_lost[int(cid)] = float(np.median([x[2] for x in rows])) - tb
        for row in d["coach"]:
            if row["cid"] not in truth_lost or row["lost"] is None:
                continue
            if row["lost"] < coaching.DISPLAY_MIN_LOST_S:
                continue
            rows_n += 1
            rows_app.append(row["lost"])
            e = row["lost"] - truth_lost[row["cid"]]
            errs.append(e)
            if row["ranked"]:
                ranked_n += 1
                rank_err.append(e)
                wrong_sign += truth_lost[row["cid"]] < 0
                truly_under += truth_lost[row["cid"]] < TRUE_LEVEL_S
        if d["coach_short"]:
            sh = [c for c in d["coach_short"] if c in truth_lost]
            head_app.append(sum(r["lost"] for r in d["coach"] if r["cid"] in sh))
            head_true.append(sum(truth_lost[c] for c in sh))
            lead = d["coach_lead_ties"] or d["coach_short"][:1]
            first = next(r for r in d["coach"] if r["cid"] == d["coach_short"][0])
            if first["cid"] in truth_lost:
                lead_app.append(first["lost"])
                lead_err.append(first["lost"] - truth_lost[first["cid"]])
            true_top = max(truth_lost, key=truth_lost.get)
            # the lead is false as named when the truly biggest loss is past the step from it
            named_true = max(truth_lost[c] for c in lead if c in truth_lost)
            false_lead += truth_lost[true_top] - named_true > stats.PB_PRECISION_S
    head_err = np.array(head_app) - np.array(head_true)
    step = stats.PB_PRECISION_S
    return {"shown_rows": rows_n, "row_app": _stats(rows_app), "row_err": _stats(errs),
            "rows_over_by_step": int(np.sum(np.array(errs) > step)), "ranked_rows": ranked_n,
            "ranked_err": _stats(rank_err), "ranked_wrong_sign": wrong_sign,
            "ranked_truly_under_0.03": truly_under,
            "headline_app": _stats(head_app), "headline_true": _stats(head_true),
            "headline_err": _stats(head_err),
            "headline_over_by_step": int(np.sum(head_err > step)),
            "lead_row_app": _stats(lead_app), "lead_row_err": _stats(lead_err),
            "lead_row_over_by_step": int(np.sum(np.array(lead_err) > step)),
            "days_with_lead": len(head_app), "lead_false_by_step": false_lead}


def summarize_biggest(days: list[dict]) -> dict:
    """(b): the moment's printed gap over the typical pass vs the same cell on truth (true pass −
    the true median of that corner's counted passes), and whether the named cell is truly the
    biggest loss (within the step)."""
    errs, apps, trues, not_biggest = [], [], [], 0
    for d in days:
        if d["plant"] is not None or not d["biggest"]:
            continue
        b = d["biggest"]
        rows = d["corners"].get(str(b["cid"]), [])
        cell = next((x for x in rows if x[0] == b["lap"]), None)
        if cell is None:
            continue
        t_med = float(np.median([x[2] for x in rows]))
        tv = cell[2] - t_med
        apps.append(b["value"])
        trues.append(tv)
        errs.append(b["value"] - tv)
        true_max = max(x[2] - float(np.median([y[2] for y in r]))
                       for r in d["corners"].values() for x in r)
        named = [tv] + [next((x[2] for x in d["corners"].get(str(c), []) if x[0] == lap), -9) -
                        float(np.median([y[2] for y in d["corners"].get(str(c), [])] or [0]))
                        for lap, c, _v in b["tie"]]
        not_biggest += true_max - max(named) > stats.PB_PRECISION_S
    # Every synthetic day carries the generator's one slow lap (`synth_gopro.SLOW_LAP`), whose off
    # is a real multi-second loss, so the moment above is that lap. A day without an off is the case
    # the selection matters in: the same rule (the largest cell − its corner's median) with the
    # slowest lap left out, over every remaining cell.
    e2, a2, t2 = [], [], []
    for d in days:
        if d["plant"] is not None:
            continue
        slow = max(d["lap_true"], key=lambda k: d["lap_true"][k])
        cells = []
        for rows in d["corners"].values():
            rows = [x for x in rows if str(x[0]) != slow]
            if len(rows) < 2:
                continue
            am = stats.printed_gap_s(0.0, 0.0)  # 0, keeps the printed-gap rule in one place
            a_med = float(np.median([x[1] for x in rows]))
            t_med = float(np.median([x[2] for x in rows]))
            cells += [(stats.printed_gap_s(x[1], a_med) + am, x[2] - t_med) for x in rows]
        if cells:
            a, t = max(cells)
            a2.append(a)
            t2.append(t)
            e2.append(a - t)
    return {"days": len(apps), "app": _stats(apps), "true": _stats(trues), "err": _stats(errs),
            "selection_share": round(float(np.mean(errs) / np.mean(apps)), 3) if apps else None,
            "named_not_truly_biggest_by_step": not_biggest,
            "without_the_off": {"app": _stats(a2), "true": _stats(t2), "err": _stats(e2),
                                "over_by_step": int(np.sum(np.array(e2) > stats.PB_PRECISION_S)),
                                "selection_share": round(float(np.mean(e2) / np.mean(a2)), 3)
                                if a2 else None}}


def summarize_sectors(days: list[dict]) -> dict:
    """(d): every non-best cell's "against the sector best" as printed vs the same two laps' true
    gap; the bias is the quickest's luck (the cell's own noise is symmetric)."""
    errs, apps = [], []
    for d in days:
        if d["plant"] is not None:
            continue
        for sec in d["sectors"].values():
            rows = sec["rows"]
            tb = next((x[2] for x in rows if x[0] == sec["best_lap"]), None)
            if tb is None:
                continue
            for lap, a, t in rows:
                if lap == sec["best_lap"]:
                    continue
                g = stats.printed_gap_s(a, sec["best"])
                apps.append(g)
                errs.append(g - (t - tb))
    return {"cells": len(apps), "app": _stats(apps), "err": _stats(errs),
            "over_by_step": int(np.sum(np.array(errs) > stats.PB_PRECISION_S)),
            "over_by_sector_step": int(np.sum(np.array(errs) > stats.SECTOR_LEVEL_S)),
            "selection_share": round(float(np.mean(errs) / np.mean(apps)), 3) if apps else None}


def summarize_card(days: list[dict]) -> dict:
    """(c): the card's two figures are the ideal gap and Coaching's first row — the same numbers
    (e) and (a) measure; this checks they are the same, card for card."""
    same_gap, same_top, n = 0, 0, 0
    for d in days:
        if d["plant"] is not None or not d.get("ideal"):
            continue
        n += 1
        g = d["card"]["delta_ideal"]
        same_gap += g is not None and abs(g - d["ideal"]["app_gap"]) < 1e-4
        top = d["card"]["top"]
        same_top += (top is None and not d["coach_short"]) or (
            top is not None and bool(d["coach_short"]) and top["cids"][0] == d["coach_short"][0])
    return {"days": n, "card_gap_is_ideal_gap": same_gap, "card_top_is_coaching_lead": same_top}


def measure_ideal_bias(work: str, noise: float, seeds, ns) -> dict[int, float]:
    """{n: the ideal gap's mean app − truth} over habit-free days at `seeds` — the test's check."""
    days = []
    for k, seed in enumerate(seeds):
        days.append(day_record(work, noise, seed, k + 1))
        for name in os.listdir(work):
            shutil.rmtree(os.path.join(work, name), ignore_errors=True)
    by_n = summarize_ideal(days)["bias_by_n"]
    return {n: by_n[n]["gap_err"]["mean"] for n in ns if n in by_n}


def summarize(days: list[dict]) -> dict:
    """Every surface's figures, and at the reference noise whether the fit the app ships is this
    probe's (`stats._IDEAL_LUCK_FIT`)."""
    out = _summarize(days)
    fit = out["ideal"]["fit"]
    if {d["noise"] for d in days} == {REFERENCE_NOISE} and len(days) >= 40:
        out["ideal"]["app_fit_is_this_fit"] = stats._IDEAL_LUCK_FIT == (fit["a"], fit["b"])
    return out


def _summarize(days: list[dict]) -> dict:
    return {"habit_free_days": sum(d["plant"] is None for d in days),
            "planted_days": sum(d["plant"] is not None for d in days),
            "ideal": summarize_ideal(days), "coaching": summarize_coaching(days),
            "biggest": summarize_biggest(days), "sectors": summarize_sectors(days),
            "card": summarize_card(days)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--noise", type=float, default=REFERENCE_NOISE)
    ap.add_argument("--sets", type=int, default=4)
    ap.add_argument("--set-index", type=int)
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--plant-days", type=int, default=2)
    ap.add_argument("--summarize", default="")
    a = ap.parse_args()
    if a.summarize:
        days = []
        for path in a.summarize.split(","):
            with open(path, encoding="utf-8") as f:
                days += [json.loads(ln)["day"] for ln in f if ln.startswith('{"day"')]
        print(json.dumps(summarize(days)))
        return 0
    sets = [a.set_index] if a.set_index is not None else list(range(a.sets))
    work = tempfile.mkdtemp(prefix="selection_surfaces_")
    days = []
    try:
        for j in sets:
            jobs = [(BASE_SEED + SET_STRIDE * j + k, 1000 * j + k + 1, None) for k in range(a.days)]
            jobs += [(PLANT_SEED + SET_STRIDE * j + 10 * gi + k, 1000 * j + 100 + 10 * gi + k, g)
                     for gi, g in enumerate(PLANTS) for k in range(a.plant_days)]
            for seed, day_no, plant in jobs:
                rec = day_record(work, a.noise, seed, day_no, plant)
                print(json.dumps({"day": rec}), flush=True)
                days.append(rec)
                for name in os.listdir(work):
                    shutil.rmtree(os.path.join(work, name), ignore_errors=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(summarize(days)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
