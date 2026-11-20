"""focus_cross_session: how far two recordings of the SAME driving disagree on a corner's typical
time — the step the focus list's cross-session verdict (`focus.verdict`) must clear before it says
"faster" or "slower" (`focus.CROSS_SESSION_LEVEL_S`).

WHY. The focus list is the one place the app makes a claim across two sessions: "C4 — 0.11 s
faster than 6 Jun". Its bar was the larger of half the wider lap-to-lap spread (`SPREAD_MARGIN` ×
IQR) and the 0.07 s timing step. Neither is a property of a SESSION: each recording's corner
typical carries its own bias from its own receiver noise, and that bias does not average away
with more laps of the same recording. This measures it on synthetic race days with known truth.

THE GRID. The demo circuit (its built-in track, so every start line is trusted and the verdict's
gates pass), habit-free days at `synth_gopro` seeds 401.. — one driver, nothing changed, each day
its own realisation of the laps and of the receiver — at GPS noise 1 and at REALISTIC_NOISE, plus
days with C4 planted quicker by a grip factor (true gains of about 0.10 / 0.15 / 0.20 s,
`PLANTS`). Every ordered pair of days (earlier → later) runs the app's own path: the earlier day
promotes each corner (`Session.focus_items`), the later re-measures it over the stored window
(`Session.focus_report`, both days recorded "dry"). Each day's corner typical is also timed on its
TRUTH, the same laps between the window's two edges mapped to the truth's arc length.

  * sigma_one — the sd of ONE session's corner-typical bias (app − truth, per corner, the corner's
    mean bias removed: a constant offset cancels between two days), pooled over corners and days;
  * diff_sd   — the sd of the app's unchanged-day difference over every pair and corner, which
    also carries the driver's own day-to-day scatter;
  * the step is the house rule for a difference of two figures that each carry σ, DIFF_2SIGMA_K
    (2·√2) × sigma_one; 2 × diff_sd is the same quantity read off the pairs (Var(a − b) = 2σ²),
    and both are printed so they can be seen to agree.
Then, per noise, the false calls (unchanged pairs read "faster"/"slower") and the power (planted
C4 read "faster") under the bar before this probe and with the step added.

MEASURED (first run, defaults): at noise 2.5 sigma_one 0.0272 s → step 0.077 → 0.08 s, and
2 × diff_sd = 2 × 0.0395 = 0.079 s; false calls 22 → 15 of 315; planted 0.10 / 0.15 / 0.20 s
found on 46 → 39, 55 → 53, 60 → 60 of 60 pairs. At noise 1: false 1 → 0 of 315; found 57 → 49,
60 → 60, 60 → 60. The whole table is in refused-2026-09 §38.

ARGV CONTRACT: optional `--days N` (habit-free days per noise, default 10) and `--plant-days N`
(days per planted gain, default 6). Every recording is generated into ONE `mkdtemp` under $TMPDIR
and removed before exit; the app-support store is jailed and the sidecar write stubbed. Output is
stdout, its last line the JSON summary.

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

from studio import chapters, focus, library, session_record, sidecar  # noqa: E402
from studio.coaching import SPREAD_MARGIN  # noqa: E402
from studio.dev import make_demo  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.session import Session  # noqa: E402
from studio.stats import DIFF_2SIGMA_K  # noqa: E402

sidecar.save = lambda *a, **k: None   # nothing is ever written beside a recording here

NOISES = (1.0, 2.5)
# The GPS noise the step is measured at: its per-lap timing σ (printed as lap_sigma) sits inside
# the 0.025-0.087 s docs/ACCURACY.md publishes against official timing, between recording C's and
# recording B's.
REALISTIC_NOISE = 2.5
BASE_SEED = 401
PLANT_SEED = 611
PLANT_CID = 4
# Target true gain (s) → C4 grip factor, set so the planted days read about that gain on truth
# over the stored window against the habit-free days (printed as true_gain@).
PLANTS = {0.10: 1.08, 0.15: 1.132, 0.20: 1.185}


def old_bar(item_iqr: float, now_iqr: float, delta: float) -> bool:
    """True when the bar before this probe read `delta` as a change (`focus.verdict` before)."""
    spread = max(item_iqr, now_iqr)
    return not (abs(delta) < SPREAD_MARGIN * spread or library.within_timing_precision(delta))


def _generate(work: str, noise: float, seed: int, day: int, habit) -> str:
    """One race day: a distinct date and recording number, so the two sides order and key apart."""
    sg.START_UTC = dt.datetime(2026, 3, 1, 10, tzinfo=dt.UTC) + dt.timedelta(days=day)
    sg.RECORDING_NUMBER = 8000 + day
    out = os.path.join(work, f"n{noise:g}_d{day}")
    rec = sg.generate(out, seed, 14, chapters=1, gps_noise=noise, origin=make_demo.DEMO_ORIGIN,
                      habit=habit)
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


def _bias_groups(days: list[dict]) -> list[list[float]]:
    """Per stored window (each day's every corner): app − truth of every day's corner typical over
    that ONE window. Within a group the window is fixed, so what varies is each session's own bias;
    where the window itself sits (one noisy reference lap) is common to the group and drops out,
    exactly as it drops out of a difference of two days over the same stored window."""
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
            groups.append(group)
    return groups


def _pooled_sd(groups: list[list[float]]) -> float:
    """The within-group sd, pooled: each group's own mean removed (a constant offset of a window
    cancels between two days), the squares summed over every group on its own degrees of freedom."""
    ss = sum(float(np.sum((np.asarray(g) - np.mean(g)) ** 2)) for g in groups if len(g) > 1)
    dof = sum(len(g) - 1 for g in groups if len(g) > 1)
    return math.sqrt(ss / dof)


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
            rows.append({"cid": item.cid, "kind": o.kind, "blocker": o.blocker})
            continue
        tx, ty = (true_median(side, *x["true_s"][item.cid]) for side in (x, y))
        rows.append({"cid": item.cid, "kind": o.kind, "delta": round(o.delta, 4),
                     "old_call": old_bar(item.iqr_s, o.now.iqr, o.delta),
                     "true": None if tx is None or ty is None else round(ty - tx, 4),
                     "top3": item.cid in x["top3"]})
    return rows


def _called_now(r: dict) -> bool:
    return r["kind"] in (focus.OUTCOME_IMPROVED, focus.OUTCOME_SLOWER)


def _rates(null: list[list[dict]], power: dict, called) -> dict:
    """False calls and power under one rule `called` (a row → bool)."""
    rows = [r for pair in null for r in pair if "delta" in r]
    top = [r for r in rows if r["top3"]]
    out = {"false": [sum(map(called, rows)), len(rows)],
           "false_top3": [sum(map(called, top)), len(top)],
           # a 3-corner list (the day's top three) with at least one false call on it
           "lists_with_a_false_call": [sum(any(called(r) for r in pair if "delta" in r and r["top3"])
                                           for pair in null), len(null)],
           # every corner read at once ("which corners moved since last time")
           "all_corner_reads_with_a_false_call": [
               sum(any(called(r) for r in pair if "delta" in r) for pair in null), len(null)]}
    for g, pairs in power.items():
        c4 = [r for pair in pairs for r in pair if "delta" in r and r["cid"] == PLANT_CID]
        out[f"found@{g:.2f}"] = [sum(1 for r in c4 if called(r) and r["delta"] < 0), len(c4)]
        # …and found with no other corner of the same pair falsely called beside it
        clean = sum(1 for pair in pairs
                    if any(r["cid"] == PLANT_CID and "delta" in r and called(r) and r["delta"] < 0
                           for r in pair)
                    and not any(r["cid"] != PLANT_CID and "delta" in r and called(r) for r in pair))
        out[f"found_alone_all_corners@{g:.2f}"] = [clean, len(pairs)]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--plant-days", type=int, default=6)
    ap.add_argument("--noises", default=",".join(f"{n:g}" for n in NOISES))
    a = ap.parse_args()
    work = tempfile.mkdtemp(prefix="focus_cross_session_")
    summary: dict = {}
    try:
        day_no = 0
        for noise in (float(n) for n in a.noises.split(",")):
            base, plants = [], {g: [] for g in PLANTS}
            for k in range(a.days):
                day_no += 1
                base.append(_day(work, noise, BASE_SEED + k, day_no))
            for gain, grip in PLANTS.items():
                habit = sg.Habit(cid=PLANT_CID, grip=grip, brake=1.0)
                for k in range(a.plant_days):
                    day_no += 1
                    plants[gain].append(_day(work, noise, PLANT_SEED + k, day_no, habit))
            null = [_pairs(x, y) for x, y in itertools.combinations(base, 2)]
            power = {g: [_pairs(x, y) for x in base for y in plants[g]] for g in PLANTS}
            for pair in null:
                for r in pair:
                    print(json.dumps({"noise": noise, "null": r}), flush=True)
            sigma_one = _pooled_sd(_bias_groups(base))
            deltas = np.array([r["delta"] for pair in null for r in pair if "delta" in r])
            diff_sd = float(np.std(deltas, ddof=1))
            lap_err = np.concatenate([d["lap_err"] for d in base])
            tv = {g: [r["true"] for pair in pairs for r in pair
                      if r["cid"] == PLANT_CID and r.get("true") is not None]
                  for g, pairs in power.items()}
            out = {
                "lap_sigma": round(float(np.std(lap_err, ddof=1)), 4),
                "sigma_one": round(sigma_one, 4),
                "step_2sqrt2_sigma_one": round(DIFF_2SIGMA_K * sigma_one, 4),
                "diff_sd": round(diff_sd, 4), "step_2_diff_sd": round(2 * diff_sd, 4),
                "diff_p95": round(float(np.percentile(np.abs(deltas), 95)), 4),
                "true_diff_sd": round(float(np.std([r["true"] for pair in null for r in pair
                                                    if r.get("true") is not None], ddof=1)), 4),
                "step": round(DIFF_2SIGMA_K * sigma_one, 2),
                **{f"true_gain@{g:.2f}": round(-float(np.median(v)), 3) for g, v in tv.items()},
                # the bar before the step (`old_bar`), and the app's verdict as it stands
                "before": _rates(null, power, lambda r: r["old_call"]),
                "after": _rates(null, power, _called_now),
            }
            if noise == REALISTIC_NOISE:
                # The constant the app ships must be this probe's step at the realistic noise.
                out["app_step"] = focus.CROSS_SESSION_LEVEL_S
                out["app_step_is_this_step"] = out["step"] == focus.CROSS_SESSION_LEVEL_S
            summary[f"noise {noise:g}"] = out
            print(json.dumps({f"noise {noise:g}": out}), flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
