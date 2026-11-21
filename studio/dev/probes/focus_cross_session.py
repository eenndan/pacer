"""focus_cross_session: how far two recordings of the SAME driving disagree on a corner's typical
time — the step the focus list's cross-session verdict (`focus.verdict`) must clear before it says
"faster" or "slower" (`focus.CROSS_SESSION_LEVEL_S`).

WHY. The focus list is the one place the app makes a claim across two sessions: "C4 — 0.11 s
faster than 6 Jun". Its bar was the larger of half the wider lap-to-lap spread (`SPREAD_MARGIN` ×
IQR) and the 0.07 s timing step. Neither is a property of a SESSION: each recording's corner
typical carries its own bias from its own receiver noise, and that bias does not average away
with more laps of the same recording. This measures it on synthetic race days with known truth.

THE GRID. The demo circuit (its built-in track, so every start line is trusted and the verdict's
gates pass). Per GPS noise level (NOISES, chosen so their per-lap timing σ spans the 0.025-0.087 s
docs/ACCURACY.md publishes against official timing) and per SEED SET (`--sets`, set j starts at
seed 401 + 100·j): `--days` habit-free days of one driver — nothing changed, each day its own
realisation of the laps and of the receiver — and `--plant-days` days per gain with C4 planted
quicker by a grip factor (true gains of about 0.10 / 0.15 / 0.20 s, `PLANTS`). Every ordered pair
of a set's days (earlier → later) runs the app's own path: the earlier day promotes each corner
(`Session.focus_items`), the later re-measures it over the stored window (`Session.focus_report`,
both days recorded "dry"). Each day's corner typical is also timed on its TRUTH, the same laps
between the window's two edges mapped to the truth's arc length.

  * sigma_one — the sd of ONE session's corner-typical bias: app − truth of every day of the set
    over one stored window, the window's own mean removed (a constant offset cancels between two
    days), pooled over windows and over seed sets (sums of squares over degrees of freedom);
  * the step is the house rule for a difference of two figures that each carry σ, DIFF_2SIGMA_K
    (2·√2) × sigma_one, at REFERENCE_NOISE: the noise whose per-lap σ matches the one reference
    recording's (`library.REFERENCE_RECORDING`, `library.REFERENCE_LAP_SIGMA_S`);
  * diff_sd — the sd of the app's unchanged-day difference over every pair and corner. It carries
    the bias of both days AND the driver's own day-to-day scatter (true_diff_sd, ~0.015 s at every
    noise), so 2 × diff_sd is not the step: it is larger, by most at low noise, where the scatter
    is a larger share.
Then, per noise, pooled over the sets: the false calls (unchanged corners read "faster"/"slower";
the day's top three; a top-three list with any; an every-corner read with any) and the power
(planted C4 read "faster"; and with no other corner of the pair falsely called) under the bar
before (0.07 s), under the shipped step (`focus.level_step`), and under the rule's own step at
that noise.

MEASURED (defaults, 4 sets × 10 days = 40 habit-free days per noise, about six minutes as four
processes, one per `--set-index`): at REFERENCE_NOISE per-lap σ 0.0253 s (the reference's 0.0247),
sigma_one 0.0178 s (sets 0.0142-0.0206) → 2√2σ = 0.050 → 0.05 s, under the timing step, which
`focus.level_step` then reads. The table across the noises is in `focus.CORNER_DAY_SIGMA_S`'s
comment, false calls and power in refused-2026-09 §38, and every published recording's row in
`stats.PUBLISHED_NOISE_SIGMAS`, which the summary checks at each noise it holds.

ARGV CONTRACT: named flags only. `--noises 1.9,4.0` (default NOISES), `--sets N` (default 4),
`--set-index J` (run only set J of them), `--days N` (default 10), `--plant-days N` (per gain, per
set, default 3). Every recording is generated into ONE `mkdtemp` under $TMPDIR and removed before
exit; the app-support store is jailed and the sidecar write stubbed. Output is stdout: one
`{"set": …}` line per noise and seed set, then the pooled summary as the last line.
`--summarize FILE[,FILE…]` instead READS such stdout logs (several partial runs, say) and prints
the same pooled summary; it writes nothing.

    pixi run python -m studio.dev.probes.focus_cross_session
"""
from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import math
import os
import shutil
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")

from studio.dev._jail import divert_app_support  # noqa: E402

_JAIL = divert_app_support("focus_cross_session_")

import numpy as np  # noqa: E402

from studio import chapters, focus, library, session_record, sidecar, stats  # noqa: E402
from studio.coaching import SPREAD_MARGIN  # noqa: E402
from studio.dev import make_demo  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.session import Session  # noqa: E402
from studio.stats import DIFF_2SIGMA_K  # noqa: E402

sidecar.save = lambda *a, **k: None   # nothing is ever written beside a recording here

# Per-lap timing σ 0.025 (recording C), 0.036, 0.053 (B), 0.064 and 0.086 s (A's 0.087), measured.
NOISES = (1.9, 2.7, 4.0, 4.8, 6.5)
# The level the shipped step is measured at: the noise whose per-lap σ matches the ONE reference
# recording's (`library.REFERENCE_LAP_SIGMA_S`), as `stats.PUBLISHED_NOISE_SIGMAS` records it.
REFERENCE_NOISE = stats.REFERENCE_SIGMAS.noise
BASE_SEED = 401
PLANT_SEED = 2011
SET_STRIDE = 100
PLANT_CID = 4
# Target true gain (s) → C4 grip factor, set so the planted days read about that gain on truth
# over the stored window against the habit-free days (printed as true_gain@).
PLANTS = {0.10: 1.094, 0.15: 1.146, 0.20: 1.198}
# The small fixed set tests/test_focus_list.py re-measures (`measure_sigma`): seeds and noise.
CHECK_SEEDS = (401, 402, 403, 404)


def _called(delta: float, bar: float, floor: float) -> bool:
    """`focus.verdict`'s rule with its step set to `floor`: a claim when |Δ| clears both."""
    return not (abs(delta) < bar or abs(delta) < max(floor, library.PB_PRECISION_S))


def _generate(work: str, noise: float, seed: int, day: int, habit) -> str:
    """One race day: a distinct date and recording number, so the two sides order and key apart.
    The generator's two module globals are put back after, so a test that runs this probe leaves
    every later synthetic recording of its process dated as before."""
    keep = sg.START_UTC, sg.RECORDING_NUMBER
    sg.START_UTC = dt.datetime(2026, 3, 1, 10, tzinfo=dt.UTC) + dt.timedelta(days=day)
    sg.RECORDING_NUMBER = 8000 + day
    try:
        out = os.path.join(work, f"n{noise:g}_d{day}")
        rec = sg.generate(out, seed, 14, chapters=1, gps_noise=noise,
                          origin=make_demo.DEMO_ORIGIN, habit=habit)
    finally:
        sg.START_UTC, sg.RECORDING_NUMBER = keep
    return rec.paths[0]


def _lap_bounds(truth, s: Session) -> dict[int, tuple[float, float]]:
    """The TRUE start-line crossings around each counted app lap, matched by lap-time sequence."""
    cr = truth.crossings(s.timing_lines_latlon()[0])
    true_lt = np.diff(cr)
    ids = sorted(s.valid_lap_ids())
    best_k, best_err = 0, math.inf
    for k in range(-3, 4):
        errs = [abs(s.lap_time(i) - true_lt[i + k]) for i in ids if 0 <= i + k < len(true_lt)]
        if len(errs) == len(ids) and float(np.median(errs)) < best_err:
            best_k, best_err = k, float(np.median(errs))
    return {i: (float(cr[i + best_k]), float(cr[i + best_k + 1])) for i in ids}


def _truth_s(s: Session, truth, frac: float) -> float:
    """The truth's arc length under a window edge at `frac` of the best lap's odometer."""
    import pacer  # the core's local → global conversion, after the jail above

    lap = s.best_lap_id()
    dist = s._lap_arrays(lap)[0]
    xs, ys = s.lap_trace_xy(lap)
    m = min(len(dist), len(xs))
    d = frac * float(s.corners.basis()[1])
    g = s.cs.global_(pacer.Vec3f(float(np.interp(d, dist[:m], xs[:m])),
                                 float(np.interp(d, dist[:m], ys[:m])), 0.0))
    return float(truth.s_at_latlon(np.array([g.lat]), np.array([g.lon]))[0])


def true_median(day: dict, s0: float, s1: float) -> float | None:
    """The median TRUE time from arc length s0 to s1 over `day`'s counted laps."""
    a, b = day["truth"].times_at(s0), day["truth"].times_at(s1)
    out = []
    for lap in day["ids"]:
        t0, t1 = day["bounds"][lap]
        hit = [t for t in a if t0 <= t < t1]
        if hit and (b > hit[0]).any():
            out.append(float(b[b > hit[0]][0] - hit[0]))
    return float(np.median(out)) if out else None


def _day(work: str, noise: float, seed: int, day: int, habit=None) -> dict:
    path = _generate(work, noise, seed, day, habit)
    s = Session.load(chapters.discover_siblings(path))
    truth = sg.simulate(seed, 14, origin=make_demo.DEMO_ORIGIN, habit=habit)
    entry = s.library_entry([path])
    cids = [c.cid for c in s.corners.corner_list()]
    # One corner at a time: a list holds at most focus.MAX_ITEMS, and every corner is measured.
    items = [i for c in cids for i in s.focus_items([c], entry)]
    d = {"s": s, "truth": truth, "entry": entry, "items": items, "ids": s.consistency_lap_ids(),
         "top3": [r.cid for r in s.coaching_opportunities().rows[:3]]}
    d["bounds"] = _lap_bounds(truth, s)
    d["true_s"] = {i.cid: (_truth_s(s, truth, i.enter_frac), _truth_s(s, truth, i.exit_frac))
                   for i in items}
    d["lap_err"] = [s.lap_time(i) - (d["bounds"][i][1] - d["bounds"][i][0]) for i in d["ids"]]
    return d


def _bias_groups(days: list[dict]) -> list[tuple[int, list[float]]]:
    """Per stored window (each day's every corner): (cid, app − truth of every day's corner typical
    over that ONE window). Within a group the window is fixed, so what varies is each session's own
    bias; where the window itself sits (one noisy reference lap) is common to the group and drops
    out, exactly as it drops out of a difference of two days over the same stored window."""
    groups = []
    for x in days:
        windows = [(i.enter_frac, i.exit_frac) for i in x["items"]]
        per_day = [d["s"].focus_samples(windows) for d in days]
        for k, item in enumerate(x["items"]):
            group = []
            for d, samples in zip(days, per_day, strict=True):
                tm = true_median(d, *x["true_s"][item.cid])
                if samples[k] is not None and tm is not None:
                    group.append(samples[k].median - tm)
            groups.append((item.cid, group))
    return groups


def _ss(groups) -> tuple[float, int]:
    """(sum of squares about each group's own mean, degrees of freedom) over `groups`."""
    ss = sum(float(np.sum((np.asarray(g) - np.mean(g)) ** 2)) for g in groups if len(g) > 1)
    return ss, sum(len(g) - 1 for g in groups if len(g) > 1)


def _pairs(x: dict, y: dict) -> list[dict]:
    """Every corner of `x`, promoted on x and re-measured on y the app's way."""
    records = {"version": session_record.VERSION, "records": {}}
    dry = {**session_record.blank_record(), "conditions": "dry"}
    for side in (x, y):
        session_record.put(records, side["entry"]["fingerprint"], dry)
    items = x["items"]
    outcomes = [o for k in range(0, len(items), focus.MAX_ITEMS)
                for o in y["s"].focus_report(items[k:k + focus.MAX_ITEMS], y["entry"],
                                             records).outcomes]
    rows = []
    for item, o in zip(items, outcomes, strict=True):
        if o.now is None or o.delta is None:
            continue
        tx, ty = (true_median(side, *x["true_s"][item.cid]) for side in (x, y))
        rows.append({"cid": item.cid, "delta": round(o.delta, 5),
                     "bar": round(SPREAD_MARGIN * max(item.iqr_s, o.now.iqr), 5),
                     "true": None if tx is None or ty is None else round(ty - tx, 5),
                     "top3": item.cid in x["top3"]})
    return rows


def measure_sigma(work: str, noise: float, seeds) -> tuple[float, float]:
    """(sigma_one, per-lap σ) over habit-free days at `seeds` — the test's small fixed check."""
    days = [_day(work, noise, seed, k + 1) for k, seed in enumerate(seeds)]
    ss, dof = _ss([g for _cid, g in _bias_groups(days)])
    lap_err = np.concatenate([d["lap_err"] for d in days])
    return math.sqrt(ss / dof), float(np.std(lap_err, ddof=1))


def measure_set(work: str, noise: float, j: int, n_days: int, n_plant: int) -> dict:
    """One seed set at one noise: the sums the summary pools, and every pair's rows."""
    day_no = 1000 * j
    base = []
    for k in range(n_days):
        day_no += 1
        base.append(_day(work, noise, BASE_SEED + SET_STRIDE * j + k, day_no))
    groups = _bias_groups(base)
    by_cid: dict[int, list] = {}
    for cid, g in groups:
        by_cid.setdefault(cid, []).append(g)
    rec = {"noise": noise, "set": j, "days": n_days,
           "lap_err": [e for d in base for e in d["lap_err"]],
           "ss_dof": _ss([g for _cid, g in groups]),
           "ss_dof_by_cid": {str(cid): _ss(gs) for cid, gs in by_cid.items()},
           "top3_days": {str(cid): sum(cid in d["top3"] for d in base) for cid in by_cid},
           "null": [_pairs(x, y) for x, y in itertools.combinations(base, 2)], "power": {}}
    for gain, grip in PLANTS.items():
        habit = sg.Habit(cid=PLANT_CID, grip=grip, brake=1.0)
        plants = []
        for k in range(n_plant):     # dated after every habit-free day of the set
            day_no += 1
            plants.append(_day(work, noise, PLANT_SEED + SET_STRIDE * j + k, day_no, habit))
        rec["power"][f"{gain:.2f}"] = [_pairs(x, y) for x in base for y in plants]
    return rec


def _rates(recs: list[dict], floor: float) -> dict:
    """False calls and power under `floor`, pooled over the seed sets in `recs`."""
    def hit(r):
        return _called(r["delta"], r["bar"], floor)
    null = [pair for rec in recs for pair in rec["null"]]
    rows = [r for pair in null for r in pair]
    top = [r for r in rows if r["top3"]]
    out = {"false": [sum(map(hit, rows)), len(rows)], "false_top3": [sum(map(hit, top)), len(top)],
           "top3_lists_with_a_false_call": [
               sum(any(hit(r) for r in pair if r["top3"]) for pair in null), len(null)],
           "all_corner_reads_with_a_false_call": [
               sum(any(hit(r) for r in pair) for pair in null), len(null)]}
    for g in PLANTS:
        pairs = [pair for rec in recs for pair in rec["power"][f"{g:.2f}"]]
        c4 = [r for pair in pairs for r in pair if r["cid"] == PLANT_CID]
        out[f"found@{g:.2f}"] = [sum(1 for r in c4 if hit(r) and r["delta"] < 0), len(c4)]
        out[f"found_alone@{g:.2f}"] = [
            sum(1 for pair in pairs
                if any(r["cid"] == PLANT_CID and hit(r) and r["delta"] < 0 for r in pair)
                and not any(r["cid"] != PLANT_CID and hit(r) for r in pair)), len(pairs)]
    return out


def summarize(recs: list[dict]) -> dict:
    """The pooled table, per noise."""
    out = {}
    for noise in sorted({r["noise"] for r in recs}):
        mine = sorted((r for r in recs if r["noise"] == noise), key=lambda r: r["set"])
        ss = sum(r["ss_dof"][0] for r in mine)
        dof = sum(r["ss_dof"][1] for r in mine)
        sigma = math.sqrt(ss / dof)
        per_set = [round(math.sqrt(r["ss_dof"][0] / r["ss_dof"][1]), 4) for r in mine]
        cids = sorted({c for r in mine for c in r["ss_dof_by_cid"]}, key=int)
        by_cid = {c: round(math.sqrt(sum(r["ss_dof_by_cid"][c][0] for r in mine
                                         if c in r["ss_dof_by_cid"])
                                     / sum(r["ss_dof_by_cid"][c][1] for r in mine
                                           if c in r["ss_dof_by_cid"])), 4) for c in cids}
        c1_ratio = [round(math.sqrt(r["ss_dof_by_cid"]["1"][0] / r["ss_dof_by_cid"]["1"][1])
                          / math.sqrt(r["ss_dof"][0] / r["ss_dof"][1]), 2)
                    for r in mine if "1" in r["ss_dof_by_cid"]]
        top3 = {c: sum(r["top3_days"].get(c, 0) for r in mine) for c in cids}
        deltas = [x["delta"] for r in mine for pair in r["null"] for x in pair]
        trues = [x["true"] for r in mine for pair in r["null"] for x in pair
                 if x["true"] is not None]
        lap = np.concatenate([np.asarray(r["lap_err"], float) for r in mine])
        rule = round(DIFF_2SIGMA_K * sigma, 2)
        tv = {g: [-x["true"] for r in mine for pair in r["power"][f"{g:.2f}"] for x in pair
                  if x["cid"] == PLANT_CID and x["true"] is not None] for g in PLANTS}
        out[f"noise {noise:g}"] = {
            "habit_free_days": sum(r["days"] for r in mine),
            "sets": len(mine), "lap_sigma": round(float(np.std(lap, ddof=1)), 4),
            "sigma_one": round(sigma, 4), "sigma_one_per_set": per_set,
            "step_rule": round(DIFF_2SIGMA_K * sigma, 4), "step_rule_rounded": rule,
            "sigma_by_cid": by_cid, "c1_over_pooled_per_set": c1_ratio, "top3_days_by_cid": top3,
            "diff_sd": round(float(np.std(deltas, ddof=1)), 4),
            "true_diff_sd": round(float(np.std(trues, ddof=1)), 4),
            **{f"true_gain@{g:.2f}": round(float(np.median(v)), 3) for g, v in tv.items() if v},
            "before_0.07": _rates(mine, library.PB_PRECISION_S),
            f"shipped_{focus.level_step():.2f}": _rates(mine, focus.level_step()),
            f"rule_here_{rule:.2f}": _rates(mine, rule),
        }
        for rec_name, row in stats.PUBLISHED_NOISE_SIGMAS.items():
            if row.noise == noise:
                # The published table's cells must be this probe's pooled σs at that noise.
                out[f"noise {noise:g}"][f"table_{rec_name}_is_this_sigma"] = (
                    round(sigma, 4) == row.cross_session
                    and round(float(np.std(lap, ddof=1)), 4) == row.lap)
        if noise == REFERENCE_NOISE:
            # The constant the app ships must be this probe's pooled σ at the reference noise.
            out[f"noise {noise:g}"]["app_sigma_is_this_sigma"] = (
                round(sigma, 4) == focus.CORNER_DAY_SIGMA_S)
            out[f"noise {noise:g}"]["app_step_is_this_step"] = rule == focus.CROSS_SESSION_LEVEL_S
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--noises", default=",".join(f"{n:g}" for n in NOISES))
    ap.add_argument("--sets", type=int, default=4)
    ap.add_argument("--set-index", type=int)
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--plant-days", type=int, default=3)
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
    work = tempfile.mkdtemp(prefix="focus_cross_session_")
    recs = []
    try:
        for noise in (float(n) for n in a.noises.split(",")):
            for j in sets:
                rec = measure_set(work, noise, j, a.days, a.plant_days)
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
