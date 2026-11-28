"""coaching_apex_step: the noise of Coaching's apex-speed reason — "carry more apex speed (−X km/h)",
the median over a corner's counted laps of (best lap's apex speed − that lap's) — and how often it
names a cause, or the session's theme, that the generator's truth does not have.

WHY. The statistic is a median of differences that all share ONE term, the best lap's own apex
reading: the laps' half averages down with their number, the best lap's half never does. Coaching
named it from any positive value (`coaching._pick_reason`), so a −0.5 km/h "deficit" made of
receiver noise could be printed as the instruction, on the share card and the report.

THE GRID. `apex_speed_sigma`'s: the demo circuit, habit-free days at REFERENCE_NOISE, set j at seeds
401 + 100·j, each SHAPE's apex planted at every corner of every lap (`apex_speed_sigma._apex_shape`),
here at a LAP COUNT per session (`--laps`). Per corner of every day, over exactly the laps Coaching
counts (`Session.coaching_opportunities`' candidates, the C5-resolved cells, the best lap's zero
among them):

  * app — the statistic as Coaching reads it (it equals the row's `Reason.apex_speed_gain`);
  * true — the same median on the TRUE minimum speeds over the best lap's corner window (S*);
  * e_best — the best lap's own apex reading error (app − true), shared by every difference;
  * e_med — the median of the other laps' reading errors.

  app − true ≈ e_best − e_med; the probe reports the sd of each part, the bias of app − true (the
best lap is partly a lucky one: does its apex read high?), and, per STEP, the reasons Coaching
names: each ranked row's reason and the headline theme's cause with the apex lever withheld below
the step (`--steps`; 0 = today). A named apex reason is FALSE where the true deficit is under
TRUE_LEVEL_KMH, and the theme's apex cause is false where under THEME_SHARE of its time is in true
apex corners.

  `--plant D` plants an apex habit of D km/h at PLANT_CID: every timed lap but the day's three
TRUE fastest (habit-free) has the corner's grip lowered so its apex is D km/h slower (brake
unchanged), so the true median deficit there is about D (plus the day's own grip scatter) — the
power a step keeps.

MEASURED (800 habit-free sessions: 5 shapes × 40 days × 5 / 9 / 14 / 20 laps, two processes, about
70 minutes): the error's sd 0.33–0.58 km/h, its mean within ±0.05 in every cell; 583 apex reasons
named, 70 false; every step but 0.5 km/h withholds more true reasons than it removes false ones
(1.75 km/h: 410 true, 69 false). The verdict and the tables are `studio/docs/refused-2026-09.md`
§40.

ARGV CONTRACT: named flags only. `--laps 14`, `--shapes flat,vee:10`, `--sets 4`, `--days 10`,
`--steps 0,1.0,1.75`, `--plant 0` (km/h; 0 = habit-free), `--noise` (default REFERENCE_NOISE).
Every recording is generated into ONE `mkdtemp` under $TMPDIR and removed before exit; the
app-support store is jailed. Output is stdout, one JSON line per day; `--summarize FILE[,FILE…]`
reads such logs and prints the tables; it writes nothing.

    pixi run python -m studio.dev.probes.coaching_apex_step --laps 14
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import os
import shutil
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")

from studio.dev._jail import divert_app_support  # noqa: E402

_JAIL = divert_app_support("coaching_apex_step_")

import numpy as np  # noqa: E402

from studio import chapters, coaching  # noqa: E402
from studio.dev import make_demo  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.dev.probes import apex_speed_sigma as ass  # noqa: E402
from studio.dev.probes import focus_cross_session as fcs  # noqa: E402
from studio.dev.probes import reference_lap_corner as rlc  # noqa: E402
from studio.session import Session  # noqa: E402

SHAPES = ("flat", "curved:3", "curved:10", "vee:4", "vee:10")
TRUE_LEVEL_KMH = ass.TRUE_LEVEL_KMH
PLANT_CID = fcs.PLANT_CID
CLEAN_LAPS = 3      # a planted day's laps driven without the habit: its three true fastest


@contextlib.contextmanager
def _apex_withheld(step: float):
    """Inside, `_pick_reason` reads an apex deficit under `step` as none: the lever withheld, on
    the level rule's path too (its deficit stays 0, so `_reason_when_level` cannot re-name it);
    every other reason and floor as it is. The signed gain rides along for the line signature."""
    real = coaching._pick_reason

    def pick(time_lost, apex_gain, *a, **kw):
        g = float(apex_gain)
        r = real(time_lost, 0.0 if 0.0 < g < step else g, *a, **kw)
        return dataclasses.replace(r, apex_speed_gain=g)

    coaching._pick_reason = pick
    try:
        yield
    finally:
        coaching._pick_reason = real


def _true_fastest(seed: int, laps: int) -> tuple[int, ...]:
    """The CLEAN_LAPS 1-based timed laps the habit-free truth drives fastest (distance-grid lap
    times)."""
    t = sg.simulate(seed, laps, origin=make_demo.DEMO_ORIGIN)
    L = t.circuit.length
    d = np.arange(1, laps + 2) * L - t.s_start
    lt = np.diff(np.interp(d, t.d_nodes, t.t_nodes))
    return tuple(int(k) + 1 for k in np.argsort(lt)[:CLEAN_LAPS])


def _plant(seed: int, laps: int, kmh: float) -> sg.Habit | None:
    """An apex habit of `kmh` km/h at PLANT_CID on every timed lap but the CLEAN_LAPS true fastest
    (one clean lap alone could be a one-off the evidence gate abstains on, when no second lap
    comes near the best): the corner limit is sqrt(grip / κ), so grip × (1 − kmh / v_apex)² takes kmh off it."""
    if kmh <= 0:
        return None
    t = sg.simulate(seed, laps, origin=make_demo.DEMO_ORIGIN)
    ct = next(c for c in t.circuit.corners if c.cid == PLANT_CID)
    s_abs = t.s_start + t.d_nodes
    on = (np.floor(s_abs / t.circuit.length) >= 1) & (np.floor(s_abs / t.circuit.length) <= laps)
    s_lap = np.mod(s_abs, t.circuit.length)
    near = on & (np.abs(s_lap - ct.apex_s) < 5.0)
    v = float(np.median(t.v_nodes[near])) * 3.6
    return sg.Habit(cid=PLANT_CID, grip=(1.0 - kmh / v) ** 2, brake=1.0,
                    clean_laps=_true_fastest(seed, laps))


def _generate(work: str, noise: float, seed: int, day: int, laps: int, habit) -> str:
    keep = sg.START_UTC, sg.RECORDING_NUMBER
    sg.START_UTC = fcs.dt.datetime(2026, 3, 1, 10, tzinfo=fcs.dt.UTC) + fcs.dt.timedelta(days=day)
    sg.RECORDING_NUMBER = 8000 + day % 1000
    try:
        rec = sg.generate(os.path.join(work, f"d{day}"), seed, laps, chapters=1, gps_noise=noise,
                          origin=make_demo.DEMO_ORIGIN, habit=habit)
    finally:
        sg.START_UTC, sg.RECORDING_NUMBER = keep
    return rec.paths[0]


def _theme(rows) -> dict:
    th = coaching.headline_theme(rows)
    return {"cause": th.cause, "cids": list(th.cause_cids), "share": round(th.cause_share, 3),
            "all_cause": coaching.session_theme(rows).cause}


def measure_day(work: str, noise: float, seed: int, day_no: int, laps: int, steps,
                plant: float) -> dict:
    habit = _plant(seed, laps, plant)
    path = _generate(work, noise, seed, day_no, laps, habit)
    s = Session.load(chapters.discover_siblings(path))
    truth = sg.simulate(seed, laps, origin=make_demo.DEMO_ORIGIN, habit=habit)
    best = s.best_lap_id()
    corner_list = s.corners.corner_list()
    n = len(corner_list)
    cand = [i for i in s.consistency_lap_ids() if len(s.corners.lap_corner_stats(i)) == n]
    d = {"truth": truth, "bounds": fcs._lap_bounds(truth, s)}
    out = {"seed": seed, "laps": laps, "plant": plant, "n_laps": len(cand), "corners": []}
    if best is None or best not in d["bounds"] or not cand:
        return out
    frame = np.array([b for c in corner_list for b in (float(c.enter), float(c.exit))])
    star = rlc._truth_s_at(s, truth, best, frame)
    cells = s._coaching_lap_inputs(cand)
    best_res = np.asarray(s.corners.lap_corner_resolved(best), bool)
    res = {i: np.asarray(s.corners.lap_corner_resolved(i), bool) for i in cand}
    true_apex = {i: [ass._true_apex(d, i, *rlc._span(star, k)) if i in d["bounds"] else None
                     for k in range(n)] for i in set(cand) | {best}}
    variants = {}
    for step in steps:
        with _apex_withheld(step):
            opp = s.coaching_opportunities()
        variants[step] = opp
    for k, c in enumerate(corner_list):
        laps_k = [i for i in cand if res[i][k] and best_res[k]]
        tb, ab = true_apex[best][k], cells[best][2][k]
        if tb is None or not laps_k:
            continue
        pairs = [(i, cells[i][2][k], true_apex[i][k]) for i in laps_k]
        if any(p[2] is None for p in pairs):
            continue
        app = float(np.median([ab - a for _i, a, _t in pairs]))
        true = float(np.median([tb - t for _i, _a, t in pairs]))
        e_med = float(np.median([a - t for i, a, t in pairs if i != best]))
        rec = {"cid": c.cid, "n": len(laps_k), "app": round(app, 3), "true": round(true, 3),
               "e_best": round(ab - tb, 3), "e_med": round(e_med, 3), "v": {}}
        for step, opp in variants.items():
            row = next((r for r in opp.rows if r.cid == c.cid), None)
            if row is not None:
                rec["v"][str(step)] = [row.reason.kind, int(row.evidence.ranked),
                                       round(row.time_lost, 4),
                                       int(row in coaching.shortlist_of(opp.rows))]
                rec["gain"] = round(row.reason.apex_speed_gain, 3)
        out["corners"].append(rec)
    out["theme"] = {str(step): _theme(opp.rows) for step, opp in variants.items()}
    return out


# ------------------------------------------------------------------------------- the tables
# THE STEPS ARE COUNTED FROM TODAY'S RUN. Withholding the apex lever under S changes no other
# strength, no loss and no shortlist, so it can only un-name a row today names apex whose statistic
# is under S: named(S) = named(0) with app ≥ S, and the theme's apex cause is re-decided on the same
# shortlist. (Checked against a full re-run at 1.0 km/h on the 800-day grid: 0 rows differ.) What a
# withheld row names instead needs the re-run: `replaced_by`, at each `--steps` value.
TABLE_STEPS = (0.0, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75)


def _apex_at(c: dict, step: float) -> bool:
    v = c["v"].get("0")
    return bool(v and v[1] and v[0] == coaching.REASON_APEX and c["app"] >= step)


def _step_counts(days: list[dict], step: float) -> dict:
    named = [c for r in days for c in r["corners"] if _apex_at(c, step)]
    theme = theme_false = 0
    for r in days:
        short = [c for c in r["corners"] if c["v"].get("0") and c["v"]["0"][1] and c["v"]["0"][3]]
        total = sum(c["v"]["0"][2] for c in short)
        apex = [c for c in short if _apex_at(c, step)]
        apex_s = sum(c["v"]["0"][2] for c in apex)
        if total > 0 and apex_s >= coaching.THEME_SHARE * total - 1e-12:
            theme += 1
            real = sum(c["v"]["0"][2] for c in apex if c["true"] >= TRUE_LEVEL_KMH)
            theme_false += real < coaching.THEME_SHARE * apex_s
    return {"named": len(named), "false": sum(c["true"] < TRUE_LEVEL_KMH for c in named),
            "theme": theme, "theme_false": theme_false}


def _tables(recs: list[dict]) -> dict:
    out: dict = {"noise": {}, "steps": {}, "replaced_by": {}, "plant": {}}
    free = [r for r in recs if not r["plant"]]
    for shape in dict.fromkeys(r["shape"] for r in free):
        for laps in sorted({r["laps"] for r in free}):
            cs = [c for r in free if (r["shape"], r["laps"]) == (shape, laps) for c in r["corners"]]
            if not cs:
                continue
            err = np.array([c["app"] - c["true"] for c in cs])
            out["noise"][f"{shape} / {laps} laps"] = {
                "corners": len(cs), "bias": round(float(err.mean()), 3),
                "sd": round(float(err.std()), 3),
                "p95": round(float(np.percentile(np.abs(err), 95)), 2),
                "best_sd": round(float(np.std([c["e_best"] for c in cs])), 3),
                "laps_sd": round(float(np.std([c["e_med"] for c in cs])), 3),
                "best_minus_laps_mean": round(float(np.mean([c["e_best"] - c["e_med"]
                                                             for c in cs])), 3)}
    for laps in [*sorted({r["laps"] for r in free}), "all"]:
        days = [r for r in free if laps == "all" or r["laps"] == laps]
        out["steps"][str(laps)] = {str(s): _step_counts(days, s) for s in TABLE_STEPS}
    for r in free:
        for c in r["corners"]:
            for step, v in c["v"].items():
                a = c["v"].get("0")
                if step != "0" and a and a[1] and a[0] == coaching.REASON_APEX \
                        and v[0] != coaching.REASON_APEX:
                    key = f"{v[0]} ({'true' if c['true'] >= TRUE_LEVEL_KMH else 'no'} apex deficit)"
                    out["replaced_by"].setdefault(step, {})
                    out["replaced_by"][step][key] = out["replaced_by"][step].get(key, 0) + 1
    for key in sorted({(r["plant"], r["laps"], r["shape"]) for r in recs if r["plant"]}):
        days = [r for r in recs if (r["plant"], r["laps"], r["shape"]) == key]
        pc = [c for r in days for c in r["corners"] if c["cid"] == PLANT_CID]
        ranked = [c for c in pc if c["v"].get("0") and c["v"]["0"][1]]
        out["plant"][f"{key[0]} km/h / {key[1]} laps / {key[2]}"] = {
            "days": len(days), "true_median": round(float(np.median([c["true"] for c in pc])), 2),
            "ranked": len(ranked),
            **{f"apex named at {s}": sum(_apex_at(c, s) for c in ranked)
               for s in (0.0, 1.0, 1.75)}}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--noise", type=float, default=fcs.REFERENCE_NOISE)
    ap.add_argument("--shapes", default=",".join(SHAPES))
    ap.add_argument("--laps", type=int, default=14)
    ap.add_argument("--sets", type=int, default=4)
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--steps", default="0,1.0,1.75")
    ap.add_argument("--plant", type=float, default=0.0)
    ap.add_argument("--summarize", default="")
    a = ap.parse_args()
    if a.summarize:
        recs = []
        for path in a.summarize.split(","):
            with open(path, encoding="utf-8") as f:
                recs += [json.loads(ln) for ln in f if ln.startswith('{"seed"')]
        print(json.dumps(_tables(recs), indent=1))
        return 0
    steps = [float(x) if float(x) else 0 for x in a.steps.split(",")]
    work = tempfile.mkdtemp(prefix="coaching_apex_step_")
    try:
        for shape in a.shapes.split(","):
            for j in range(a.sets):
                for k in range(a.days):
                    seed = fcs.BASE_SEED + fcs.SET_STRIDE * j + k
                    with ass._apex_shape(shape):
                        rec = measure_day(work, a.noise, seed, 1000 * j + k + 1, a.laps, steps,
                                          a.plant)
                    rec["shape"] = shape
                    print(json.dumps(rec), flush=True)
                    for name in os.listdir(work):
                        shutil.rmtree(os.path.join(work, name), ignore_errors=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
