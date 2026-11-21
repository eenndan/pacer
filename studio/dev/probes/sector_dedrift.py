"""sector_dedrift: does removing each lap's rigid receiver shift (`CornerModel.lap_shift`) before the
sector lines are crossed make a sector split better — and where does it stop helping or hurt?

PR #675 measured it on the synthetic grid at the reference noise: the worst sector of 13 placements
from 0.088 s to 0.038 s (`sector_split_sigma`'s `pair_dedrifted`). That generator's position error is
an Ornstein-Uhlenbeck drift (τ 90 s, 0.35 m per axis) plus white noise (0.30 m), scaled by DOP — NOT
a rigid per-lap shift: over a ~50 s lap the drift decorrelates to about exp(−50/90) = 0.57. This
probe asks whether the gain is an artefact of that model, by replacing the receiver's position
error (`synth_gopro._gps_rows`' ex/ey, everything else untouched) with each of `VARIANTS`:

  * base — the generator as shipped (OU τ 90 s + white);
  * tau10 / tau30 / tau300 — the same drift amplitude decorrelating faster or slower than a lap;
  * rigid — a constant offset per lap (an OU of τ 90 s sampled once per lap of distance), + white;
  * white — no drift at all, white noise only (0.30 m, and `white2` at twice that): a shift fitted
    on noise alone, where de-drifting can only add error;
  * none — no position noise: the method's own error;
  * rot / scale — the drift is a slowly varying ROTATION (about the circuit's centre) or SCALE of
    the whole trace, of the same RMS displacement as base's drift: not a translation, so a rigid
    shift can only fit part of it, + white.

Per variant and placement (`sector_split_sigma._configs`), each statistic as a triple — the splits
as timed before the de-drift, de-drifted against the BEST lap (`lap_shift`, the corner cells'
frame), and as shipped (against the session's consensus line, `Session._sector_drift`): the pair σ
(`sector_split_sigma.pair_sigma`, the split against another split of its recording); the COLUMN
BIAS (per day and sector column, the mean of app − truth over the counted laps, RMS over days — a
common per-day bias cancels in every consumer's difference; this is what an absolute split
carries, and the best lap's frame adds that lap's own drift to every lap of the column); the
correlation of adjacent sectors' errors (a boundary moved by drift lengthens one sector by what it
takes off the next); and per day the identity a de-drift must keep (every counted lap's shipped
splits sum to its lap time) and whether the session had a geometry at all.

MEASURED (10 days per variant, seeds 401-410, at the reference noise; worst sector of the 13
placements, before → shipped): base 0.079 → 0.038; τ 10 s 0.093 → 0.079; τ 30 s 0.088 → 0.056;
τ 300 s 0.062 → 0.028; rigid 0.099 → 0.034; white 0.0202 → 0.0207; white2 0.041 → 0.041; none
0.0052 → 0.0065; rotation 0.081 → 0.084; scale 0.106 → 0.104. So the gain is the drift's share that
is one vector over a lap: half the σ at τ 90 s, a third at τ 30 s, a seventh at τ 10 s; on noise
alone the fitted shift adds error, at most 0.0015 s at C's white noise and 0.005 s on a placement
under rotation or scale, never near the step. Half-lap coherence (`half_lap_shifts`, 5 days):
base 0.66, τ 30 s 0.43, τ 10 s 0.05, rigid 0.93, white −0.15, rotation −0.52, scale −0.56.

ARGV CONTRACT: named flags only. `--variants a,b,…` (default all), `--days N` (default 10, from set
0's seeds), `--first-day K` (default 0), `--noise X` (default the reference's). Every recording is
generated into ONE `mkdtemp` under $TMPDIR and removed before exit; the app-support store is jailed
and the sidecar write stubbed. Output is stdout: one `{"day": …}` line per day, then the summary.
`--summarize FILE[,FILE…]` READS such logs and prints the summary; it writes nothing.

    pixi run python -m studio.dev.probes.sector_dedrift --variants base,white --days 10
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

_JAIL = divert_app_support("sector_dedrift_")

import numpy as np  # noqa: E402

from studio import chapters, corners, sidecar, stats  # noqa: E402
from studio.dev import make_demo  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.dev.probes import sector_split_sigma as ssp  # noqa: E402
from studio.dev.probes.clip_refusals import _true_splits  # noqa: E402
from studio.dev.probes.focus_cross_session import BASE_SEED, _generate  # noqa: E402
from studio.session import Session  # noqa: E402

sidecar.save = lambda *a, **k: None   # nothing is ever written beside a recording here

VARIANTS = ("base", "tau10", "tau30", "tau300", "rigid", "white", "white2", "none", "rot", "scale")
_ORIG_GPS_ROWS = sg._gps_rows


def _ou(rng, n: int, sigma: float, tau_s: float, dt: float = 0.1) -> np.ndarray:
    rho = math.exp(-dt / tau_s)
    e = rng.normal(0.0, sigma * math.sqrt(1 - rho * rho), n)
    out = np.empty(n)
    out[0] = rng.normal(0.0, sigma)
    for i in range(1, n):
        out[i] = rho * out[i - 1] + e[i]
    return out


def _error_model(variant: str):
    """A replacement for the generator's (ex, ey) position error, given (rng, n, k, x, y, s_abs,
    circuit): the shipped model's terms re-drawn per `variant` (module doc)."""
    def err(rng, n, k, x, y, s_abs, circuit):
        white = 0.30 * (2.0 if variant == "white2" else 1.0)
        wx, wy = rng.normal(0.0, white, n), rng.normal(0.0, white, n)
        if variant == "none":
            return np.zeros(n), np.zeros(n)
        if variant in ("white", "white2"):
            return wx * k, wy * k
        if variant in ("base", "tau10", "tau30", "tau300"):
            tau = {"base": 90.0, "tau10": 10.0, "tau30": 30.0, "tau300": 300.0}[variant]
            return (_ou(rng, n, 0.35, tau) + wx) * k, (_ou(rng, n, 0.35, tau) + wy) * k
        if variant == "rigid":
            lap = np.floor(s_abs / circuit.length).astype(int)
            lap -= lap.min()
            m = int(lap.max()) + 1
            # one draw per lap from the OU's stationary law, correlated lap to lap as τ 90 s at a
            # ~50 s lap would be
            ox, oy = _ou(rng, m, 0.35, 90.0, 50.0), _ou(rng, m, 0.35, 90.0, 50.0)
            return (ox[lap] + wx) * k, (oy[lap] + wy) * k
        cx, cy = float(np.mean(x)), float(np.mean(y))
        rx, ry = x - cx, y - cy
        r_rms = float(np.sqrt(np.mean(rx * rx + ry * ry)))
        # the same RMS displacement as base's two drift axes, 0.35·√2 m
        amp = 0.35 * math.sqrt(2.0) / r_rms
        a = _ou(rng, n, amp, 90.0)
        if variant == "rot":
            dx, dy = rx * np.cos(a) - ry * np.sin(a) - rx, rx * np.sin(a) + ry * np.cos(a) - ry
        else:  # scale
            dx, dy = rx * a, ry * a
        return (dx + wx) * k, (dy + wy) * k
    return err


def _patch(variant: str) -> None:
    """Swap `synth_gopro._gps_rows` for a copy whose receiver position error is `variant`'s
    (`_error_model`); DOP, fix flags, the two lone teleport glitches, altitude, speeds, stamps and
    the media clock are the shipped function's, line for line."""
    model = _error_model(variant)

    def gps_rows(truth, rng, gps_noise):
        phase = 0.037
        tk = phase + 0.1 * np.arange(int((truth.t_end - phase) / 0.1) + 1)
        s_abs, v, _ = sg._kinematics(truth, tk)
        x, y, _, _ = truth.circuit.at(s_abs)
        n = len(tk)
        dop = np.clip(1.1 + _ou(rng, n, 0.12, 90.0), 0.8, 2.5)
        fix = np.full(n, 3)
        fix[tk < 8.0] = 2
        fix[tk < 4.0] = 0
        dop = np.where(tk < 20.0, dop + np.clip(20.0 - tk, 0, None) * 0.6, dop)
        k = gps_noise * dop / 1.1
        ex, ey = model(rng, n, k, x, y, s_abs, truth.circuit)
        moving = np.flatnonzero(v > 5.0)
        for frac, jump in ((0.35, (61.0, -44.0)), (0.71, (-38.0, 57.0))):
            i = moving[int(frac * len(moving))]
            ex[i] += jump[0]
            ey[i] += jump[1]
        lat, lon = sg.to_latlon(x + ex, y + ey, truth.origin)
        alt = 31.0 + _ou(rng, n, 1.2, 90.0) + rng.normal(0.0, 0.4, n)
        v2 = np.maximum(v + rng.normal(0.0, 0.06, n) * gps_noise, 0.0)
        v3 = np.maximum(v + rng.normal(0.0, 0.08, n) * gps_noise, 0.0)
        ms0 = round((sg.START_UTC - sg.dt.datetime(2000, 1, 1, tzinfo=sg.dt.UTC)).total_seconds()
                    * 1000.0)
        days, msod = np.divmod(ms0 + 100 * np.arange(n, dtype=np.int64), 86_400_000)
        raw = np.column_stack([np.round(lat * 1e7), np.round(lon * 1e7), np.round(alt * 1000),
                               np.round(v2 * 1000), np.round(v3 * 100), days, msod])
        media = tk * (1.0 + truth.media_ppm * 1e-6) + truth.gps_lag_s
        rows = [sg.struct.pack(">7i2H", *map(int, r), int(round(d * 100)), int(f))
                for r, d, f in zip(raw, dop, fix, strict=True)]
        return media, rows

    sg._gps_rows = gps_rows


def half_lap_shifts(s: Session) -> dict:
    """How rigid the receiver error is OVER ONE LAP, read without truth: each clean lap's rigid
    shift fitted separately on the first and the second half of its stations (the same offsets
    from the consensus line, the same least squares as `corners._rigid_shift`), and the
    correlation of the two halves' shifts across laps (x and y pooled, each centred on the
    session's mean). Near 1: one vector per lap, what a whole-lap shift removes; near 0: the error
    changes within the lap, and a whole-lap shift can only remove its lap mean."""
    geom = s.corners.geometry()
    if geom is None:
        return {}
    nx, ny = geom.frame[4], geom.frame[5]
    half = len(geom.stations) // 2
    a, b = [], []
    for lid in sorted(geom.shift):
        _t, xs, ys, _v, cum = s._lap_columns(lid)
        off = geom.offsets(xs, ys, cum) - geom.consensus
        first, second = off.copy(), off.copy()
        first[half:] = np.nan
        second[:half] = np.nan
        a.append(corners._rigid_shift(first, nx, ny)[0])
        b.append(corners._rigid_shift(second, nx, ny)[0])
    if len(a) < 3:
        return {}
    a, b = np.array(a), np.array(b)
    a, b = a - a.mean(0), b - b.mean(0)
    rho = float(np.sum(a * b) / math.sqrt(float(np.sum(a * a)) * float(np.sum(b * b))))
    return {"laps": len(a), "rho": round(rho, 3),
            "rms_m": [round(float(np.sqrt(np.mean(a * a))), 3),
                      round(float(np.sqrt(np.mean(b * b))), 3)]}


def day_record(work: str, variant: str, noise: float, seed: int, day_no: int,
               halves_only: bool = False) -> dict:
    _patch(variant)
    try:
        path = _generate(work, noise, seed, day_no, None)
    finally:
        sg._gps_rows = _ORIG_GPS_ROWS
    s = Session.load(chapters.discover_siblings(path))
    truth = sg.simulate(seed, 14, origin=make_demo.DEMO_ORIGIN)
    geom = s.corners.geometry()
    start, _ = s.timing_lines_latlon()
    out = {"variant": variant, "noise": noise, "seed": seed, "set": 0, "plant": None,
           "geometry": geom is not None, "placements": {}, "sum_err_max": 0.0,
           "shift_rms_m": None}
    if geom is not None:
        sh = [s.corners.lap_shift(i) for i in s.valid_lap_ids()]
        out["shift_rms_m"] = round(float(np.sqrt(np.mean(np.square(sh)))), 3)
    out["halves"] = half_lap_shifts(s)
    if halves_only:
        return out
    for name, metres in ssp._configs(truth.circuit).items():
        lines = [truth.line_at(m) for m in metres]
        if not s.apply_timing_lines_latlon(start, lines, confirmed=True):
            out["placements"][name] = None
            continue
        sm, true = s.split_matrix(), _true_splits(s, truth, lines)
        rows = []
        for lap, cells in zip([] if sm is None else sm.lap_ids, [] if sm is None else sm.cells,
                              strict=True):
            if lap in true and None not in cells and len(true[lap]) == len(cells):
                # 1: as timed before the de-drift; 3: de-drifted against the best lap
                # (`lap_shift`, the corner cells' frame); 4: as shipped (the consensus frame)
                before = ssp.uncorrected_splits(s, lap)
                dd = ssp.splits_with(s, lap, s.corners.lap_shift)
                dc = list(cells)
                out["sum_err_max"] = max(out["sum_err_max"], abs(sum(dc) - s.lap_time(lap)))
                rows.append([int(lap), [ssp._r(v) for v in before], [ssp._r(v) for v in true[lap]],
                             [ssp._r(v) for v in dd], [ssp._r(v) for v in dc]])
        out["placements"][name] = {"metres": [round(m, 2) for m in metres],
                                   "medians": None if sm is None else [ssp._r(v) for v in sm.medians],
                                   "rows": rows}
    return out


def adjacent_corr(days, name: str, app: int) -> float | None:
    """Correlation of adjacent sectors' errors (app − truth, each column centred per day): the
    signature of a boundary moved by drift, which lengthens one sector by what it takes off the
    next."""
    a, b = [], []
    for d in days:
        p = d["placements"].get(name)
        if not p or len(p["rows"]) < 3:
            continue
        err = np.array([np.subtract(r[app], r[2]) for r in p["rows"]])
        err = err - err.mean(axis=0)
        for c in range(err.shape[1] - 1):
            a += err[:, c].tolist()
            b += err[:, c + 1].tolist()
    return round(float(np.corrcoef(a, b)[0, 1]), 3) if len(a) > 2 else None


def column_bias(days, name: str, app: int) -> float | None:
    """RMS over days and sector columns of the column's mean error (app − truth)."""
    vals = []
    for d in days:
        p = d["placements"].get(name)
        if not p or len(p["rows"]) < 2:
            continue
        err = np.array([np.subtract(r[app], r[2]) for r in p["rows"]])
        vals += err.mean(axis=0).tolist()
    return round(math.sqrt(float(np.mean(np.square(vals)))), 4) if vals else None


def summarize(days: list[dict]) -> dict:
    out = {}
    for v in [x for x in VARIANTS if any(d["variant"] == x for d in days)]:
        mine = [d for d in days if d["variant"] == v]
        rec = {"days": len(mine), "geometry_days": sum(d["geometry"] for d in mine),
               "sum_err_max": max(d["sum_err_max"] for d in mine),
               "shift_rms_m": round(float(np.mean([d["shift_rms_m"] or 0 for d in mine])), 3)}
        worst = {"app": (0.0, ""), "dd": (0.0, ""), "dc": (0.0, "")}
        for name in mine[0]["placements"]:
            a, b, c = (ssp.pair_sigma(mine, name, k) for k in (1, 3, 4))
            if not a:
                continue
            rec[name] = {"pooled": [a["pooled"], b["pooled"], c["pooled"]],
                         "worst": [a["worst"], b["worst"], c["worst"]],
                         "bias_rms": [column_bias(mine, name, k) for k in (1, 3, 4)],
                         "adj_corr": [adjacent_corr(mine, name, k) for k in (1, 3, 4)]}
            worst["app"] = max(worst["app"], (a["worst"], name))
            worst["dd"] = max(worst["dd"], (b["worst"], name))
            worst["dc"] = max(worst["dc"], (c["worst"], name))
        rec["worst_of_all"] = {k: {"sigma": w[0], "placement": w[1],
                                   "step": stats.house_step(w[0])} for k, w in worst.items()}
        out[v] = rec
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--first-day", type=int, default=0)
    ap.add_argument("--noise", type=float, default=stats.REFERENCE_SIGMAS.noise)
    ap.add_argument("--summarize", default="")
    ap.add_argument("--halves-only", action="store_true")
    a = ap.parse_args()
    if a.summarize:
        days = []
        for path in a.summarize.split(","):
            with open(path, encoding="utf-8") as f:
                days += [json.loads(ln)["day"] for ln in f if ln.startswith('{"day"')]
        print(json.dumps(summarize(days)))
        return 0
    work = tempfile.mkdtemp(prefix="sector_dedrift_")
    try:
        days = []
        for v in a.variants.split(","):
            for k in range(a.first_day, a.first_day + a.days):
                rec = day_record(work, v, a.noise, BASE_SEED + k, k + 1, a.halves_only)
                print(json.dumps({"day": rec}), flush=True)
                days.append(rec)
                for name in os.listdir(work):
                    shutil.rmtree(os.path.join(work, name), ignore_errors=True)
        if not a.halves_only:
            print(json.dumps(summarize(days)))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
