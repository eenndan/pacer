"""quickest_bias: how much the QUICKEST of n noisy passes is quick by luck — the bias the Quickest
passes view must clear before it offers "Compare with my typical pass" on a quickest row, or wears a
★ ("alone"): `stats.quickest_luck_s`.

WHY. The level step (0.07 s for a corner, `library.PB_PRECISION_S`; `stats.SECTOR_LEVEL_S` for a
sector split) is the house rule for a difference of two figures that each carry symmetric noise.
The quickest of n passes is not such a figure: it is CHOSEN for being quickest, so its own timing
error is chosen too, and it sits quick of its true time on average. Two gaps are read off it:

  * typical − quickest (the offer on a Quickest row): the printed typical pass (`typical_pass_lap`,
    the counted pass nearest the median, the quickest excluded) minus the printed quickest;
  * the next gap (the ★): the second-quickest counted pass minus the quickest, to the millisecond
    as `_quickest_column` reads it. Its claim is that the quickest is clear of EVERY other pass,
    so its truth is the quickest lap's true margin over the truly quickest of the others.

Each is measured as app − truth for the SAME laps (bias = its mean), on synthetic race days of
known truth: #667's grid (`focus_cross_session`), the demo circuit, one driver, per GPS noise
(`NOISES`, per-lap σ 0.025-0.086 s) and per seed set (set j at seeds 401 + 100·j), `--days`
habit-free days each. Every counted corner pass of the CORNERS BY LAP grid is timed on truth over
the best lap's corner window mapped to arc length (S*, as `reference_lap_corner` does), and every
sector split of three sector lines (mid-straights 1, 3, 5) on the truth's own crossings
(`clip_refusals._true_splits`). For n from `stats.MATRIX_MIN_LAPS` to the day's count, n of a
stretch's counted passes are drawn (`DRAWS` seeded draws; the whole set once at the full count),
and the two gaps are read on them exactly as the app reads them.

PLANTED DAYS (`--plant-days` per gain per set): C4 slowed by a grip factor on every lap but one
(`sg.Habit(clean_laps=(PLANT_LAP,))`), so that one lap's C4 pass, and the split of the sector that
holds C4, is truly quicker than the rest by about the gain (`PLANTS`, printed as true_gain@). These
give the power of the offer and of the ★ before and after the luck term.

The summary, per noise and per stretch kind: the bias of each gap by n with its standard error;
at REFERENCE_NOISE (the one reference recording's, `library.REFERENCE_RECORDING`) the one-parameter
fit `stats.quickest_luck_s` ships, bias(n) = k × the expected largest of n standard normals
(Blom's approximation), its k by least squares weighted by draws, and the largest residual; then,
over every stretch-day at the full count, the offers and stars before (level step) and after
(level step + luck): how many, how many truly under the step / under 0.03 s / of the wrong sign,
and on planted days how many name the planted lap.

MEASURED (defaults, at REFERENCE_NOISE, 40 habit-free days): see `stats.quickest_luck_s`'s
comment for the table and the fit, and the PR that added it for the grid's before/after.

ARGV CONTRACT: named flags only. `--noises 1.9` (default NOISES), `--sets N` (default 4),
`--set-index J` (run only set J), `--days N` (default 10), `--plant-days N` (default 2). Every
recording is generated into ONE `mkdtemp` under $TMPDIR and removed before exit; the app-support
store is jailed and the sidecar write stubbed. Output is stdout: one `{"day": …}` line per day,
then the summary as the last line. `--summarize FILE[,FILE…]` instead READS such stdout logs and
prints the summary; it writes nothing.

    pixi run python -m studio.dev.probes.quickest_bias
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

_JAIL = divert_app_support("quickest_bias_")

import numpy as np  # noqa: E402

from studio import chapters, sidecar, stats  # noqa: E402
from studio.dev import make_demo  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.dev.probes.clip_refusals import _true_splits  # noqa: E402
from studio.dev.probes.focus_cross_session import (  # noqa: E402
    BASE_SEED,
    NOISES,
    REFERENCE_NOISE,
    SET_STRIDE,
    _generate,
    _lap_bounds,
)
from studio.dev.probes.reference_lap_corner import _true_time, _truth_s_at  # noqa: E402
from studio.session import Session  # noqa: E402

sidecar.save = lambda *a, **k: None   # nothing is ever written beside a recording here

PLANT_SEED = 3011
PLANT_CID = 4
PLANT_LAP = 7                 # the one timed lap (1-based) the habit leaves clean
# Target true gain (s) → the C4 grip factor of every other lap, so the clean lap's pass reads about
# that gain under the others' typical on truth (printed as true_gain@).
PLANTS = {0.10: 0.911, 0.15: 0.874, 0.20: 0.837}
SECTOR_STRAIGHTS = (1, 3, 5)  # three sector lines, mid-straight (sector_quickest's 3-line set)
DRAWS = 24                    # seeded draws of n passes per stretch-day and n below the full count
TRUE_LEVEL_S = 0.03           # a true gap this small is no difference a driver could act on
# The small fixed set tests/test_quickest_passes.py re-measures (`measure_bias`): seeds, the n.
CHECK_SEEDS = (401, 402, 403, 404, 405, 406)
CHECK_N = (5, 9, 13)


def _r(x) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), 5)


def day_record(work: str, noise: float, seed: int, day_no: int, plant: float | None = None) -> dict:
    """One synthetic day: every counted corner pass and sector split, app and truth."""
    habit = None if plant is None else sg.Habit(cid=PLANT_CID, grip=PLANTS[plant], brake=1.0,
                                                clean_laps=(PLANT_LAP,))
    path = _generate(work, noise, seed, day_no, habit)
    s = Session.load(chapters.discover_siblings(path))
    truth = sg.simulate(seed, 14, origin=make_demo.DEMO_ORIGIN, habit=habit)
    d = {"truth": truth, "bounds": _lap_bounds(truth, s)}
    corner_list, _total = s.corners.basis()
    frame = np.array([b for c in corner_list for b in (float(c.enter), float(c.exit))])
    star = _truth_s_at(s, truth, s.best_lap_id(), frame)
    rec = {"noise": noise, "seed": seed, "plant": plant, "corners": {}, "sectors": {}}
    m = s.corner_matrix()
    for col, cid in enumerate([] if m is None else m.cids):
        rows = []
        for r, lap in enumerate(m.lap_ids):
            val = m.cells[r][col]
            if val is None or not m.resolved[r][col] or lap not in d["bounds"]:
                continue
            tt = _true_time(d, lap, float(star[2 * col]), float(star[2 * col + 1]))
            if tt is not None:
                rows.append([int(lap), _r(val), _r(tt)])
        rec["corners"][str(cid)] = rows
    # the sector lines last: they re-segment the session
    lines = [truth.line_at(sum(truth.circuit.straights[j]) / 2.0) for j in SECTOR_STRAIGHTS]
    start, _ = s.timing_lines_latlon()
    if s.apply_timing_lines_latlon(start, lines, confirmed=True):
        true_sp = _true_splits(s, truth, lines)
        sm = s.split_matrix()
        for col in range(0 if sm is None else sm.columns):
            rows = []
            for r, lap in enumerate(sm.lap_ids):
                val = sm.cells[r][col]
                if val is not None and lap in true_sp:
                    rows.append([int(lap), _r(val), _r(true_sp[lap][col])])
            rec["sectors"][str(stats.sector_key(col))] = rows
    return rec


def _gaps(laps, app, true) -> tuple[float, float, float, float] | None:
    """(typical − quickest as printed, its truth, next gap to the ms, the quickest's true margin)
    over one set of passes, read as `pass_vs_typical_of` and `_quickest_column` read them."""
    if len(laps) < 2:
        return None
    counted = dict(zip(laps, app, strict=True))
    order = sorted(zip(app, laps, strict=True))
    q = order[0][1]
    typical, _scale = stats._typical_and_scale(list(app))
    v = stats.pass_vs_typical_of(1, q, counted, typical, stats.PB_PRECISION_S)
    if v is None:
        return None
    t = dict(zip(laps, true, strict=True))
    nxt = (round(order[1][0] * 1000) - round(order[0][0] * 1000)) / 1000
    margin = min(t[lap] for lap in laps if lap != q) - t[q]
    return v.gap_s, t[v.typical_lap_id] - t[q], nxt, margin


def bias_draws(rows, n: int, rng) -> list[tuple[float, float]]:
    """(typical gap app − truth, next gap app − truth margin) over draws of n of `rows`."""
    if len(rows) < n:
        return []
    picks = [np.arange(len(rows))] if n == len(rows) else [
        rng.choice(len(rows), size=n, replace=False) for _ in range(DRAWS)]
    out = []
    for idx in picks:
        sub = [rows[i] for i in sorted(idx)]
        g = _gaps([x[0] for x in sub], [x[1] for x in sub], [x[2] for x in sub])
        if g is not None:
            out.append((g[0] - g[1], g[2] - g[3]))
    return out


def blom(n: int) -> float:
    """The expected largest of n standard normals, Blom's approximation (0 for n < 2)."""
    from statistics import NormalDist
    return 0.0 if n < 2 else NormalDist().inv_cdf((n - 0.375) / (n + 0.25))


def bias_by_n(days: list[dict], kind: str, seed: int = 0) -> dict[int, dict]:
    """Per n: the mean and standard error of each gap's app − truth, over every stretch-day."""
    acc: dict[int, list] = {}
    for d in days:
        if d["plant"] is not None:
            continue
        for key, rows in d[kind].items():
            rng = np.random.default_rng([seed, d["seed"], abs(int(key)) + 50, len(rows)])
            for n in range(stats.MATRIX_MIN_LAPS, len(rows) + 1):
                acc.setdefault(n, []).extend(bias_draws(rows, n, rng))
    out = {}
    for n, xs in sorted(acc.items()):
        a = np.asarray(xs, float)
        out[n] = {"draws": len(a),
                  "typical": round(float(a[:, 0].mean()), 4),
                  "typical_se": round(float(a[:, 0].std(ddof=1) / math.sqrt(len(a))), 4),
                  "next": round(float(a[:, 1].mean()), 4),
                  "next_se": round(float(a[:, 1].std(ddof=1) / math.sqrt(len(a))), 4)}
    return out


def fit(by_n: dict[int, dict], gap: str) -> dict:
    """(a, b) in bias(n) = a + b × blom(n), least squares weighted by draws, over every n with at
    least 100 draws; the largest residual, and each residual in standard errors."""
    ns = [n for n in by_n if by_n[n]["draws"] >= 100]
    w = np.array([by_n[n]["draws"] for n in ns], float)
    x = np.array([blom(n) for n in ns])
    y = np.array([by_n[n][gap] for n in ns])
    a_mat = np.stack([np.ones_like(x), x], axis=1) * np.sqrt(w)[:, None]
    (a, b), *_ = np.linalg.lstsq(a_mat, y * np.sqrt(w), rcond=None)
    res = y - (a + b * x)
    se = np.array([by_n[n][f"{gap}_se"] for n in ns])
    return {"a": round(float(a), 4), "b": round(float(b), 4), "n": [min(ns), max(ns)],
            "max_abs_residual": round(float(np.max(np.abs(res))), 4),
            "max_abs_residual_in_se": round(float(np.max(np.abs(res) / np.maximum(se, 1e-6))), 2),
            "residual_by_n": {int(n): round(float(r), 4) for n, r in zip(ns, res, strict=True)}}


def _decide(days: list[dict], kind: str, luck) -> dict:
    """Offers and stars at the full count, under the level step plus `luck(key, n, gap)` (or 0)."""
    step = stats.PB_PRECISION_S if kind == "corners" else stats.SECTOR_LEVEL_S
    out = {"rows": 0, "offered": 0, "offer_true_under_step": 0, "offer_true_under_0.03": 0,
           "offer_wrong_sign": 0, "stars": 0, "star_false_by_claim": 0, "star_true_under_0.03": 0}
    power: dict[str, list[int]] = {}
    for d in days:
        for key, rows in d[kind].items():
            if len(rows) < stats.MATRIX_MIN_LAPS:
                continue
            laps, app, true = ([x[i] for x in rows] for i in range(3))
            g = _gaps(laps, app, true)
            if g is None:
                continue
            gap, tgap, nxt, margin = g
            n = len(rows)
            # the app's own rule: not level under the step plus the luck term, to the millisecond
            offer = gap > 0 and not stats.is_level(
                gap, stats._with_luck(step, luck(int(key), n, "typical")))
            star = not stats.is_level(nxt, stats._with_luck(step, luck(int(key), n, "next")))
            if d["plant"] is None:
                out["rows"] += 1
                out["offered"] += offer
                out["offer_true_under_step"] += offer and tgap < step
                out["offer_true_under_0.03"] += offer and tgap < TRUE_LEVEL_S
                out["offer_wrong_sign"] += offer and tgap < 0
                out["stars"] += star
                out["star_false_by_claim"] += star and margin < step
                out["star_true_under_0.03"] += star and margin < TRUE_LEVEL_S
                continue
            planted_here = (kind == "corners" and int(key) == PLANT_CID) or (
                kind == "sectors" and _holds_plant(d, key))
            if not planted_here:
                continue
            q = min(zip(app, laps, strict=True))[1]
            true_q = min(zip(true, laps, strict=True))[1]
            hit = q == true_q
            p = power.setdefault(f"{d['plant']:.2f}", [0, 0, 0, 0])
            p[0] += 1
            p[1] += hit and offer
            p[2] += hit and star
            p[3] += 1 if hit else 0
    out["power"] = {g: {"days": v[0], "offered": v[1], "starred": v[2], "named": v[3]}
                    for g, v in sorted(power.items())}
    return out


def _holds_plant(d: dict, key: str) -> bool:
    """Whether sector `key` of planted day `d` is the one whose true quickest is the clean lap by
    the most — the sector holding C4 (the others carry no planted gain)."""
    best, best_key = -1.0, None
    for k, rows in d["sectors"].items():
        t = sorted(x[2] for x in rows)
        if len(t) > 1 and float(np.median(t)) - t[0] > best:
            best, best_key = float(np.median(t)) - t[0], k
    return key == best_key


def summarize(days: list[dict]) -> dict:
    out = {}
    for noise in sorted({d["noise"] for d in days}):
        mine = [d for d in days if d["noise"] == noise]
        rec = {"habit_free_days": sum(d["plant"] is None for d in mine),
               "planted_days": sum(d["plant"] is not None for d in mine)}
        for kind in ("corners", "sectors"):
            by_n = bias_by_n(mine, kind)
            rec[f"{kind}_bias_by_n"] = by_n
            rec[f"{kind}_fit"] = {g: fit(by_n, g) for g in ("typical", "next")}
            rec[f"{kind}_before"] = _decide(mine, kind, lambda key, n, gap: 0.0)
            rec[f"{kind}_after"] = _decide(mine, kind, stats.quickest_luck_s) \
                if hasattr(stats, "quickest_luck_s") else None
            gains = {}
            for d in mine:
                if d["plant"] is None:
                    continue
                rows = d["corners"].get(str(PLANT_CID), [])
                t = sorted(x[2] for x in rows)
                if len(t) > 1:
                    gains.setdefault(d["plant"], []).append(float(np.median(t)) - t[0])
            rec["true_gain@"] = {f"{g:.2f}": round(float(np.median(v)), 3)
                                 for g, v in sorted(gains.items())}
            if noise == REFERENCE_NOISE and hasattr(stats, "_QUICKEST_LUCK_FIT"):
                # The fit the app ships must be this probe's at the reference noise.
                name = kind[:-1]
                rec[f"{kind}_app_fit_is_this_fit"] = all(
                    stats._QUICKEST_LUCK_FIT[(name, g)] == (rec[f"{kind}_fit"][g]["a"],
                                                            rec[f"{kind}_fit"][g]["b"])
                    for g in ("typical", "next"))
        out[f"noise {noise:g}"] = rec
    return out


def measure_bias(work: str, noise: float, seeds, ns) -> dict:
    """{kind: {n: (typical bias, next bias)}} over habit-free days at `seeds` — the test's check."""
    days = []
    for k, seed in enumerate(seeds):
        days.append(day_record(work, noise, seed, k + 1))
        for name in os.listdir(work):
            shutil.rmtree(os.path.join(work, name), ignore_errors=True)
    out = {}
    for kind in ("corners", "sectors"):
        by_n = bias_by_n(days, kind)
        out[kind] = {n: (by_n[n]["typical"], by_n[n]["next"]) for n in ns if n in by_n}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--noises", default=",".join(f"{n:g}" for n in NOISES))
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
    work = tempfile.mkdtemp(prefix="quickest_bias_")
    days = []
    try:
        for noise in (float(n) for n in a.noises.split(",")):
            for j in sets:
                jobs = [(BASE_SEED + SET_STRIDE * j + k, 1000 * j + k + 1, None)
                        for k in range(a.days)]
                jobs += [(PLANT_SEED + SET_STRIDE * j + 10 * gi + k, 1000 * j + 100 + 10 * gi + k,
                          g) for gi, g in enumerate(PLANTS) for k in range(a.plant_days)]
                for seed, day_no, plant in jobs:
                    rec = day_record(work, noise, seed, day_no, plant)
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
