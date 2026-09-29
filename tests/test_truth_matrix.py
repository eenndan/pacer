"""THE TRUTH MATRIX: every number above the timing core against the ground truth it was generated
from (TRUTH-1; next-level review 2026-09-28, moves 4 and 10).

WHY. Until this file no test compared a sector split, the ideal lap, the gap to it, a corner's time
or minimum speed, the rise of the Δ trace through a corner or the time on the brakes with TRUTH:
`test_synth_gopro.py` holds lap times at one seed and noise 0/1, the corner count and direction,
and whether the Stats tiles are "populated". A number can be populated and wrong. The review found
three that are (sector splits snap to the 10 Hz fixes; the ideal lap reads fast under noise; the Δ
trace puts half of a corner's line loss elsewhere), none of which any test could see.

THE GRID. `studio/dev/synth_gopro.py`'s recording through the REAL loader (`discover_siblings` ->
`Session.load`), at SEEDS × NOISES: three seeds (the CI seed is among the least biased for the ideal
lap, seed 2 among the worst) × GPS noise 0, 2 and 4.5 (pooled calibration: the owner's real lap
σ of 25/53/87 ms reads as noise ≈ 2.1/4.4/7.2). Each recording keeps the app's own start line and
gets the golden gate's two sector lines (mid-straight on `Circuit.straights[2]` and `[5]`), placed
through `apply_timing_lines_latlon` as a user places them. App laps are matched to true laps by
their START TIME, so a noise level that segments a different lap count fails by name instead of
comparing lap k with lap k+1. Row 6 adds two bare-Session fixtures with a planted one-corner line
change (`tests/_synthetic.line_change_session`): the stadium, and the GoPro's 7-corner circuit —
a non-stadium shape, so a fix proven here is not a fix for one geometry.

ONE TABLE, `ROWS`: per statistic and noise level (or fixture), the value MEASURED on the tree that
wrote it, a status and its bounds. Every value is a truth error — app minus truth — never a pinned
app output (review §7), and every bound is the measured value with at most 1.5× headroom
(`test_the_table_keeps_its_own_rules` holds that):
  * green     — stat ≤ tol.
  * known-red — tol < stat ≤ ceiling. `tol` is what the named fix (`fixed_by`) must reach and
                `ceiling` is today's value with headroom, so the row is strict BOTH ways: a fix
                that turns it green fails here until it flips the status, and a change that makes
                it worse fails too.
  * stated    — a known effect pinned under its ceiling, to be said in words on its surface.
Braking truth (row 7) is the app's OWN pipeline — its session threshold, its COAST_SMOOTH_S
boxcar, its event detector — run on the noise-free TRUE speed at the same instants, so the row
isolates what GPS noise adds and nothing definitional.

Rows: 1 lap time · 2 sector split · 3 ideal lap (mean bias over seeds) · 4 gap to ideal · 5 corner
time and minimum speed (p18's truth window) · 6 rise of Δ through each corner, on the GoPro and on
the line-change fixtures (trace, Corners table, and the two against each other) · 7 time on the
brakes. The Stats rows are TRUTH-2's.

Run: python tests/test_truth_matrix.py [--measure]   (--measure prints every statistic, for a
re-measure after an intentional change; ~20 s, needs the pixi env's ffmpeg)
"""
import dataclasses
import math
import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

import numpy as np  # noqa: E402

import pacer  # noqa: E402  (app-local metres -> GPS, the Session's own coordinate system)
from _synthetic import line_change_delta, line_change_session  # noqa: E402
from studio import chapters, driving  # noqa: E402
from studio._signal import speed_long_g  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.session import Session  # noqa: E402

SEEDS = (sg.DEFAULT_SEED, 1, 2)
NOISES = (0.0, 2.0, 4.5)
SECTOR_STRAIGHTS = (2, 5)        # the golden gate's placement (test_golden_synthetic)
LINE_CHANGES = {"stadium": dict(amp_m=1.0, corner=1, layout="stadium"),
                "circuit": dict(amp_m=2.0, corner=4, layout="circuit")}


@dataclasses.dataclass(frozen=True)
class Row:
    stat: str
    level: object                # a GPS noise level, or a line-change fixture's name
    measured: float              # this tree's value (see MEASURED_ON)
    status: str                  # green | known-red | stated
    tol: float | None = None     # green: stat <= tol; known-red: the fix's target, stat > tol
    ceiling: float | None = None  # known-red / stated: stat <= ceiling
    fixed_by: str = ""


MEASURED_ON = "2026-09-29, main 9a1e3db + #462 (GATES-6)"
G, R = "green", "known-red"
_T5 = "TRUTH-5 (sector boundary at the true line crossing)"
_T9 = "TRUTH-9 (the de-drift stops absorbing a line change)"
_T10 = "TRUTH-10 (the Δ family on the warp frame)"
_T11 = "TRUTH-11 (de-bias the ideal; TRUTH-6 says it in words)"
_T12 = "TRUTH-12 (noise-aware brake threshold, noise 0-2), else stated by TRUTH-7"
# Seconds unless named; `level` is the GPS noise, or the line-change fixture. Every value is an
# ERROR against truth (app − truth), so it is the size of the error that is bounded.
ROWS: tuple[Row, ...] = (
    # 1 · lap time at the app's own line: every matched lap, pooled over the seeds
    Row("lap_time.max", 0.0, 0.00401213, G, tol=0.0060),
    Row("lap_time.max", 2.0, 0.0551679, G, tol=0.080),
    Row("lap_time.max", 4.5, 0.123078, G, tol=0.180),
    Row("lap_time.mean", 0.0, 0.000161181, G, tol=0.00024),
    Row("lap_time.mean", 2.0, 0.00157637, G, tol=0.0023),
    Row("lap_time.mean", 4.5, 0.00357036, G, tol=0.0053),
    # 2 · sector split at the golden placement. The interior split (S2) sits between two
    # boundaries snapped to the nearest 10 Hz fix, so it is quantized to 0.1 s steps: noise 2 and
    # 4.5 snap the same laps a whole fix apart, which is why their pooled column means coincide.
    Row("sector.interior_sd", 0.0, 0.0539754, R, tol=0.0010, ceiling=0.080, fixed_by=_T5),
    Row("sector.max", 0.0, 0.0846015, R, tol=0.010, ceiling=0.125, fixed_by=_T5),
    Row("sector.col_mean", 0.0, 0.00711365, G, tol=0.0105),
    Row("sector.col_mean", 2.0, 0.0237803, G, tol=0.035),
    Row("sector.col_mean", 4.5, 0.0237803, G, tol=0.035),
    # 3 · the ideal lap: |mean over the seeds| of app − true ideal (an order statistic of noisy
    # cells reads fast; the target is TRUTH-11's 30 ms)
    Row("ideal.mean_bias", 0.0, 0.00739398, G, tol=0.011),
    Row("ideal.mean_bias", 2.0, 0.160903, R, tol=0.030, ceiling=0.240, fixed_by=_T11),
    Row("ideal.mean_bias", 4.5, 0.698047, R, tol=0.030, ceiling=1.04, fixed_by=_T11),
    # 4 · the gap to ideal (best lap − ideal): worst seed
    Row("gap.max", 0.0, 0.00946271, G, tol=0.014),
    Row("gap.max", 2.0, 0.302574, R, tol=0.030, ceiling=0.45, fixed_by=_T11),
    Row("gap.max", 4.5, 0.799545, R, tol=0.030, ceiling=1.19, fixed_by=_T11),
    # 5 · corner time (s) and minimum speed (km/h) per (lap, corner) over p18's truth window: rms
    Row("corner.time_rms", 0.0, 0.02221, G, tol=0.033),
    Row("corner.time_rms", 2.0, 0.0936704, G, tol=0.14),
    Row("corner.time_rms", 4.5, 0.128161, G, tol=0.19),
    Row("corner.vmin_rms", 0.0, 0.0189383, G, tol=0.028),
    Row("corner.vmin_rms", 2.0, 0.994005, G, tol=1.49),
    Row("corner.vmin_rms", 4.5, 2.26322, G, tol=3.39),
    # 6a · rise of Δ through each corner on the GoPro, worst (lap, corner): the Δ trace, the
    # Corners table, and the two against each other. Noise-free, the table (warp frame) is within
    # 27 ms and the trace (odometer fraction) is 121 ms off — on the slow lap, whose half-spin at
    # C5 changes its odometer where the others' does not. The trace's target is the table's own
    # accuracy; trace vs table is TRUTH-10's 10 ms at every noise.
    Row("delta.trace", 0.0, 0.121476, R, tol=0.040, ceiling=0.18, fixed_by=_T10),
    Row("delta.trace", 2.0, 0.144103, G, tol=0.21),
    Row("delta.trace", 4.5, 0.236005, G, tol=0.35),
    Row("delta.table", 0.0, 0.0267495, G, tol=0.040),
    Row("delta.table", 2.0, 0.113373, G, tol=0.17),
    Row("delta.table", 4.5, 0.211276, G, tol=0.31),
    Row("delta.trace_vs_table", 0.0, 0.0967454, R, tol=0.010, ceiling=0.145, fixed_by=_T10),
    Row("delta.trace_vs_table", 2.0, 0.127973, R, tol=0.010, ceiling=0.19, fixed_by=_T10),
    Row("delta.trace_vs_table", 4.5, 0.385544, R, tol=0.010, ceiling=0.57, fixed_by=_T10),
    # 6b · the planted one-corner line change (truth: stadium C1 0.128 s, circuit C4 0.114 s),
    # worst corner. Off the stadium the table is already true; on it the de-drift absorbs half.
    Row("line_change.trace", "stadium", 0.0973711, R, tol=0.010, ceiling=0.145,
        fixed_by=f"{_T9} + {_T10}"),
    Row("line_change.trace", "circuit", 0.0853238, R, tol=0.010, ceiling=0.125, fixed_by=_T10),
    Row("line_change.table", "stadium", 0.0627417, R, tol=0.010, ceiling=0.094, fixed_by=_T9),
    Row("line_change.table", "circuit", 0.00285145, G, tol=0.0042),
    Row("line_change.trace_vs_table", "stadium", 0.0346295, R, tol=0.010, ceiling=0.051,
        fixed_by=_T10),
    Row("line_change.trace_vs_table", "circuit", 0.0824724, R, tol=0.010, ceiling=0.12,
        fixed_by=_T10),
    # 7 · time on the brakes per lap, |mean| of app − the app's own pipeline on the true speed:
    # exactly what GPS noise adds
    Row("brake.mean", 0.0, 0.00238095, G, tol=0.0035),
    Row("brake.mean", 2.0, 0.696424, R, tol=0.30, ceiling=1.04, fixed_by=_T12),
    Row("brake.mean", 4.5, 0.76616, R, tol=0.30, ceiling=1.14, fixed_by=_T12),
)


# ------------------------------------------------------------------------------------ harness
_TMP = tempfile.TemporaryDirectory(prefix="truth_matrix_")
_PICTURES: dict = {}
_CASES: dict = {}


def _picture(path, truth, first, n, ffmpeg):
    """One chapter's placeholder picture, encoded once per (first payload, payload count) and
    copied after: the three noise levels of a seed share their timeline, so they share pictures
    (the analyses never read a pixel)."""
    key = (first, n)
    if key not in _PICTURES:
        _PICTURES[key] = os.path.join(_TMP.name, f"picture_{first}_{n}.mp4")
        sg._encode_video(_PICTURES[key], n * sg.FRAMES_PER_PAYLOAD, ffmpeg)
    shutil.copyfile(_PICTURES[key], path)


@dataclasses.dataclass
class Case:
    seed: int
    noise: float
    truth: sg.Truth
    s: Session
    tl: np.ndarray               # true crossings of the app's start line
    match: dict                  # app lap id -> true lap index k (lap k spans tl[k] .. tl[k+1])
    offset: float                # app clock minus true clock (s)
    sector_lines: list


def _case(seed: int, noise: float) -> Case:
    key = (seed, noise)
    if key in _CASES:
        return _CASES[key]
    rec = sg.generate(os.path.join(_TMP.name, f"rec_{seed}_{noise:g}"), seed=seed, gps_noise=noise,
                      video=_picture)
    s = Session.load(chapters.discover_siblings(rec.paths[0]))
    t = rec.truth
    start, _ = s.timing_lines_latlon()
    lines = [t.line_at(sum(t.circuit.straights[j]) / 2.0) for j in SECTOR_STRAIGHTS]
    assert s.apply_timing_lines_latlon(start, lines, confirmed=True), (
        f"seed {seed} noise {noise:g}: the golden sector lines were refused")
    tl = t.crossings(s.timing_lines_latlon()[0])
    starts = {i: float(s._lap_columns(i)[0][0]) for i in s.valid_lap_ids()}
    near = {i: int(np.argmin(np.abs(tl - t0))) for i, t0 in starts.items()}
    offset = float(np.median([starts[i] - tl[k] for i, k in near.items()]))
    match = {}
    for i, t0 in starts.items():
        k = int(np.argmin(np.abs(tl - (t0 - offset))))
        if abs(tl[k] - (t0 - offset)) < 0.25 and k + 1 < len(tl):
            match[i] = k
    unmatched = sorted(set(starts) - set(match))
    missing = sorted(set(range(len(tl) - 1)) - set(match.values()))
    assert not unmatched and not missing and len(set(match.values())) == len(match), (
        f"seed {seed} noise {noise:g}: app laps {unmatched} start at no true crossing; true laps "
        f"{missing} have no app lap ({len(starts)} valid app laps, {len(tl) - 1} true laps)")
    _CASES[key] = Case(seed, noise, t, s, tl, match, offset, lines)
    return _CASES[key]


def _pass(case: Case, k: int, s_lap: float) -> float:
    """True time lap k passed lap distance `s_lap`."""
    t = case.truth.times_at(s_lap)
    inside = t[(t > case.tl[k]) & (t < case.tl[k + 1])]
    assert len(inside) == 1, (case.seed, case.noise, k, s_lap, t)
    return float(inside[0])


def _true_s_of(case: Case, xs, ys) -> np.ndarray:
    """True lap distance of app-local points: the Session's own local -> GPS map, then the
    circuit's frame."""
    lat, lon = [], []
    for x, y in zip(xs, ys, strict=True):
        g = case.s.cs.global_(pacer.Vec3f(float(x), float(y), 0.0))
        lat.append(g.lat)
        lon.append(g.lon)
    return case.truth.s_at_latlon(np.array(lat), np.array(lon))


# ------------------------------------------------------------------------------------ the rows
def _lap_errors(case: Case) -> np.ndarray:
    return np.array([case.s.lap_time(i) - (case.tl[k + 1] - case.tl[k])
                     for i, k in case.match.items()])


def _sector_errors(case: Case) -> np.ndarray:
    """(laps, 3) app split minus true split."""
    xs = [case.truth.crossings(ln) for ln in case.sector_lines]
    out = []
    for i, k in case.match.items():
        lo, hi = case.tl[k], case.tl[k + 1]
        b = sorted(float(x[(x > lo) & (x < hi)][0]) for x in xs)
        out.append(np.array(case.s.lap_sector_splits(i)) - np.diff([lo, *b, hi]))
    return np.array(out)


def _ideal(case: Case) -> tuple[float, float]:
    """(app ideal − true ideal, app gap to ideal − true gap). The truth is the SAME composite on
    the true clock: each partition edge -> the true lap distance where the best lap passed it, every
    lap's true time between those distances, the minimum per segment over the same admitted and
    resolved cells (the review's moat3_verify mapping)."""
    s, t, L = case.s, case.truth, case.truth.circuit.length
    sb = s.ideal_segment_bests()
    best = s.best_lap_id()
    kb = case.match[best]
    times_b, _x, _y, _v, cum_b = s._lap_columns(best)
    total_ref = float(cum_b[-1])
    bounds = []
    for e in sb.s_edges:
        if e <= 0.0:
            bounds.append(case.tl[:-1])
        elif e >= 1.0:
            bounds.append(case.tl[1:])
        else:
            t_true = float(np.interp(e * total_ref, cum_b, times_b)) - case.offset
            s_lap = (t.s_start + float(np.interp(t_true, t.t_nodes, t.d_nodes))) % L
            bounds.append(np.array([_pass(case, k, s_lap) for k in range(len(case.tl) - 1)]))
    true_seg = np.diff(np.array(bounds).T, axis=1)
    mask = np.asarray(sb.admitted) & np.asarray(sb.resolved)
    cells = np.where(mask, true_seg[[case.match[i] for i in sb.lap_ids]], np.nan)
    ideal_true = float(np.nanmin(cells, axis=0).sum())
    gap_app = s.lap_time(best) - sb.total
    gap_true = (case.tl[kb + 1] - case.tl[kb]) - ideal_true
    return sb.total - ideal_true, gap_app - gap_true


def _corner_windows_true(case: Case):
    """Per detected corner: (enter, exit) true lap distances where the best lap's boundaries are,
    and p18's truth window — the model's own width centred on the TRUE corner's apex."""
    s, c = case.s, case.truth.circuit
    clist, _total = s.corners.basis()
    _t, bx, by, _v, bcum = s._lap_columns(s.best_lap_id())
    out = []
    for cn in clist:
        at = [cn.enter, cn.apex, cn.exit]
        se, sa, sx = _true_s_of(case, np.interp(at, bcum, bx), np.interp(at, bcum, by))
        apex = min((k.apex_s for k in c.corners),
                   key=lambda a, sa=sa: abs((sa - a + c.length / 2) % c.length - c.length / 2))
        width = float((sx - se) % c.length)
        out.append((cn, float(se), float(sx), apex - width / 2, apex + width / 2))
    return out


def _corner_errors(case: Case) -> tuple[np.ndarray, np.ndarray]:
    """(corner time errors s, minimum-speed errors km/h) over every (matched lap, corner)."""
    t = case.truth
    wins = _corner_windows_true(case)
    dt, dv = [], []
    for i, k in case.match.items():
        stats = case.s.corners.lap_corner_stats(i)
        assert len(stats) == len(wins), (case.seed, case.noise, i, len(stats), len(wins))
        for st, (_cn, _se, _sx, w0, w1) in zip(stats, wins, strict=True):
            ta, tb = _pass(case, k, w0 % t.circuit.length), _pass(case, k, w1 % t.circuit.length)
            inside = (t.t_nodes >= ta) & (t.t_nodes <= tb)
            vmin = min(float(t.v_nodes[inside].min()), *t.speed_at(np.array([ta, tb])))
            dt.append(st.time - (tb - ta))
            dv.append(st.apex_speed - 3.6 * vmin)
    return np.array(dt), np.array(dv)


def _delta_rise_errors(case: Case) -> np.ndarray:
    """(n, 3) over every non-best matched lap × corner: the rise of `Session.delta` across the
    corner window minus the true rise, the Corners table's Δ minus the true rise, and the trace
    minus the table. True Δ at a boundary: the lap's true time at the physical position the best
    lap had there, minus the best lap's, each from its own true start."""
    s = case.s
    best = s.best_lap_id()
    kb = case.match[best]
    ids = [i for i in case.match if i != best]
    _b, _spd, delta = s.delta(ids)
    table = {i: s.corners.lap_corner_stats(i) for i in ids}
    out = []
    for c, (cn, se, sx, _w0, _w1) in enumerate(_corner_windows_true(case)):
        for i in ids:
            k = case.match[i]
            x, dl = delta[i]
            trace = float(np.interp(cn.exit, x, dl) - np.interp(cn.enter, x, dl))
            d0, d1 = ((_pass(case, k, q) - case.tl[k]) - (_pass(case, kb, q) - case.tl[kb])
                      for q in (se, sx))
            out.append((trace - (d1 - d0), table[i][c].delta - (d1 - d0), trace - table[i][c].delta))
    return np.array(out)


def _brake_errors(case: Case) -> np.ndarray:
    """Per matched lap: the app's time on the brakes minus the SAME pipeline on the true speed."""
    s = case.s
    theta = s.driving.thresholds().theta_b
    rows = {r.idx: r.brake_s for r in s.stats.lap_stats()}
    out = []
    for i in case.match:
        dists, _kmh, elapsed = s._lap_arrays(i)
        t_true = float(s._lap_columns(i)[0][0]) - case.offset + elapsed
        g = speed_long_g(3.6 * case.truth.speed_at(t_true), elapsed)
        events = driving.brake_events(dists, elapsed, g, theta,
                                      corner_windows=s.driving._corner_windows(i, float(dists[-1])))
        out.append(rows[i] - driving.brake_time(elapsed, g, theta, events))
    return np.array(out)


def _line_change_errors(name: str) -> dict:
    """The planted line change: per corner, |trace rise − truth|, |table − truth|, |trace − table|."""
    s, laps = line_change_session(**LINE_CHANGES[name])
    clist, _total = s.corners.basis()
    _b, _spd, delta = s.delta([1])
    x, dl = delta[1]
    table = [st.delta for st in s.corners.lap_corner_stats(1)]
    trace, true = [], []
    for cn in clist:
        trace.append(float(np.interp(cn.exit, x, dl) - np.interp(cn.enter, x, dl)))
        true.append(float(line_change_delta(laps, 1, cn.exit) - line_change_delta(laps, 1, cn.enter)))
    trace, true, table = np.array(trace), np.array(true), np.array(table)
    return {"planted": LINE_CHANGES[name]["corner"], "truth": true, "trace": trace, "table": table,
            "line_change.trace": float(np.abs(trace - true).max()),
            "line_change.table": float(np.abs(table - true).max()),
            "line_change.trace_vs_table": float(np.abs(trace - table).max())}


# ------------------------------------------------------------------------------------ statistics
_STATS: dict = {}


def _pool(fn, noise):
    return np.concatenate([np.ravel(fn(_case(seed, noise))) for seed in SEEDS])


def stats() -> dict:
    """{(stat, level): value} — every statistic the table rows, computed once per process."""
    if _STATS:
        return _STATS
    out = {}
    for noise in NOISES:
        lap = _pool(_lap_errors, noise)
        out["lap_time.max", noise] = float(np.abs(lap).max())
        out["lap_time.mean", noise] = abs(float(lap.mean()))
        sec = [_sector_errors(_case(seed, noise)) for seed in SEEDS]
        allsec = np.concatenate(sec)
        out["sector.col_mean", noise] = float(np.abs(allsec.mean(axis=0)).max())
        if noise == 0.0:
            out["sector.interior_sd", noise] = max(float(e[:, 1].std()) for e in sec)
            out["sector.max", noise] = float(np.abs(allsec).max())
        ideal = np.array([_ideal(_case(seed, noise)) for seed in SEEDS])
        out["ideal.mean_bias", noise] = abs(float(ideal[:, 0].mean()))
        out["gap.max", noise] = float(np.abs(ideal[:, 1]).max())
        ct, cv = zip(*(_corner_errors(_case(seed, noise)) for seed in SEEDS), strict=True)
        ct, cv = np.concatenate(ct), np.concatenate(cv)
        out["corner.time_rms", noise] = float(np.sqrt(np.mean(ct ** 2)))
        out["corner.vmin_rms", noise] = float(np.sqrt(np.mean(cv ** 2)))
        rise = np.concatenate([_delta_rise_errors(_case(seed, noise)) for seed in SEEDS])
        for j, key in enumerate(("delta.trace", "delta.table", "delta.trace_vs_table")):
            out[key, noise] = float(np.abs(rise[:, j]).max())
        out["brake.mean", noise] = abs(float(_pool(_brake_errors, noise).mean()))
    for name in LINE_CHANGES:
        got = _line_change_errors(name)
        for key in ("line_change.trace", "line_change.table", "line_change.trace_vs_table"):
            out[key, name] = got[key]
        out["_line_change", name] = got
    _STATS.update(out)
    return _STATS


# ------------------------------------------------------------------------------------ the tests
def check(row: Row, stat: float) -> str | None:
    """None when `stat` satisfies `row`, else why not (the row's semantics, module doc)."""
    where = f"{row.stat} @ {row.level}: {stat:.6g} (measured {row.measured:.6g} on {MEASURED_ON})"
    if row.status == G and not stat <= row.tol:
        return f"{where} is past its tolerance {row.tol:g} — the number moved away from truth"
    if row.status == R and not stat <= row.ceiling:
        return f"{where} is past its ceiling {row.ceiling:g} — a known error got worse"
    if row.status == R and not stat > row.tol:
        return (f"{where} is within {row.tol:g} now: {row.fixed_by or 'a fix'} turned it green — "
                f"flip its status to green and re-measure")
    if row.status == "stated" and not stat <= row.ceiling:
        return f"{where} is past its ceiling {row.ceiling:g} — the stated effect grew"
    return None


def _check_rows(prefix: str) -> None:
    st = stats()
    rows = [r for r in ROWS if r.stat.startswith(prefix)]
    assert rows, f"no row for {prefix}"
    for r in rows:
        print(f"  {r.stat:28s} {r.level!s:>8}  {st[r.stat, r.level]:10.6g}  {r.status:9s} "
              f"tol {r.tol:g}" + (f"  ceiling {r.ceiling:g}" if r.ceiling is not None else ""))
    failed = [msg for r in rows if (msg := check(r, st[r.stat, r.level]))]
    assert not failed, "\n".join(failed)


def test_the_table_keeps_its_own_rules():
    """Every statistic has exactly one row and every row a statistic; every bound is the measured
    value with at most 1.5× headroom (a green tol, a known-red or stated ceiling); a known-red
    row names its fix and its target lies below today's value."""
    keys = [(r.stat, r.level) for r in ROWS]
    assert len(keys) == len(set(keys)), "a statistic has two rows"
    computed = {k for k in stats() if not k[0].startswith("_")}
    assert computed == set(keys), (f"no row: {sorted(computed - set(keys), key=str)}; "
                                   f"no statistic: {sorted(set(keys) - computed, key=str)}")
    for r in ROWS:
        assert r.status in (G, R, "stated"), r
        if r.status == G:
            assert r.ceiling is None and r.measured <= r.tol <= 1.5 * r.measured, r
        else:
            assert r.measured <= r.ceiling <= 1.5 * r.measured, r
        if r.status == R:
            assert r.fixed_by and r.tol < r.measured, r


def test_row1_lap_time():
    _check_rows("lap_time.")


def test_row2_sector_split():
    _check_rows("sector.")


def test_row3_ideal_lap():
    _check_rows("ideal.")


def test_row4_gap_to_ideal():
    _check_rows("gap.")


def test_row5_corner_time_and_minimum_speed():
    _check_rows("corner.")


def test_row6_delta_rise_through_each_corner():
    _check_rows("delta.")
    _check_rows("line_change.")


def test_row7_time_on_the_brakes():
    _check_rows("brake.")


def test_every_row_has_teeth():
    """Each known-red row fails if its status is flipped to green, and a planted defect turns a
    green row red: +20 ms on every lap time (row 1, noise 0) and a +20 ms shift of the first
    sector boundary (row 2's column mean, noise 0) — exercised through the same statistics."""
    st = stats()
    for r in ROWS:
        if r.status == R:
            assert check(dataclasses.replace(r, status=G), st[r.stat, r.level]), r
    rows = {(r.stat, r.level): r for r in ROWS}
    lap_time, splits = Session.lap_time, Session.lap_sector_splits
    Session.lap_time = lambda self, i: lap_time(self, i) + 0.020
    try:
        lap = _pool(_lap_errors, 0.0)
    finally:
        Session.lap_time = lap_time
    assert check(rows["lap_time.max", 0.0], float(np.abs(lap).max()))
    assert check(rows["lap_time.mean", 0.0], abs(float(lap.mean())))
    Session.lap_sector_splits = lambda self, i: list(np.add(splits(self, i), (0.020, -0.020, 0.0)))
    try:
        sec = _pool(_sector_errors, 0.0).reshape(-1, 3)
    finally:
        Session.lap_sector_splits = splits
    assert check(rows["sector.col_mean", 0.0], float(np.abs(sec.mean(axis=0)).max()))


def _run_all():
    for fn in (test_the_table_keeps_its_own_rules, test_row1_lap_time, test_row2_sector_split,
               test_row3_ideal_lap, test_row4_gap_to_ideal, test_row5_corner_time_and_minimum_speed,
               test_row6_delta_rise_through_each_corner, test_row7_time_on_the_brakes,
               test_every_row_has_teeth):
        t0 = time.time()
        fn()
        print(f"ok {fn.__name__} ({time.time() - t0:.1f} s)")


def measure() -> None:
    t0 = time.time()
    st = stats()
    for (stat, level), v in sorted(st.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        if stat.startswith("_"):
            print(f"  {stat} {level}: planted C{v['planted']}; truth {np.round(v['truth'], 3)}; "
                  f"trace {np.round(v['trace'], 3)}; table {np.round(v['table'], 3)}")
        else:
            print(f"  {stat:28s} {level!s:>8}: {v:.6g}")
    print(f"({time.time() - t0:.1f} s)")


if __name__ == "__main__":
    if "--measure" in sys.argv:
        measure()
    else:
        _run_all()
