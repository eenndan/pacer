"""apex_line: two coaching figures the discovery-6 market lane proposed, measured against planted
truth before anything is built. Their verdicts are `studio/docs/refused-2026-09.md` §36 (early /
late apex) and §37 (line offset at the apex).

WHY. Both would put a corner's name on a metre figure — "your quickest passes reached their slowest
point 3 m later", "1.6 m wider at the apex" — and both read one GPS fix's position (10 Hz: 1.1-1.7 m
of track at kart corner speeds) on a receiver whose error drifts over a session. So each is
measured where the answer is known: `studio/dev/synth_gopro.py`'s recording through the REAL loader
(`discover_siblings` -> `Session.load`), at the truth matrix's three seeds and GPS noise 2 and 4.5
(`tests/test_truth_matrix.py`: the owner's lap σ of 25 / 53 / 87 ms reads as noise ≈ 2.1 / 4.4 /
7.2), with the thing to find PLANTED on the even timed laps at C1, C3, C5 and C7, and C2, C4 and C6
left alone as the same run's null.

  * apex — WHERE the minimum speed happens. The generator's kart holds a flat speed over each
    arc (its limit is sqrt(grip / κ) and κ is constant there), so "where the minimum is" has no
    answer on it. The plant gives every pass a unique minimum: the speed becomes
    max(physics, v(s_a) + RISE · ((s − s_a) / 10 m)²) around s_a = the corner's true apex + SHIFT,
    a bowl RISE km/h deep at ±10 m, its bottom exactly at s_a (`_bowl`). The odd laps keep
    SHIFT 0. SHIFT 0 / 2 / 5 / 10 m, RISE 1 and 3 km/h. The figure read per pass is the app's
    `CornerStat.apex_dist` (the lap odometer at the min-speed fix) placed in the reference lap's
    frame through the lap's OWN projected window (`project_boundaries` with the lap's warp, the
    window `lap_corner_stats` measures in): enter + (apex_dist − d0) · (exit − enter) / (d1 − d0).
    And a refinement: the vertex of a parabola through the 3 (and the 5) fixes around the minimum.
  * line — the signed lateral offset at the apex (`Corner.apex`, the reference-lap odometer of the
    ringed apex), wide positive: `SessionGeometry.offsets` (the perpendicular offset from the
    reference lap the de-drift itself fits), read three ways — raw; de-drifted (the lap's
    `lap_shift`, the app's rigid per-lap receiver shift, projected on the normal there); and
    referenced to the straights (the de-drifted offset minus its median over the stations 20-60 m
    outside each end of the window, a local re-zero). The plant runs the even laps AMP metres wide
    through C1, C3, C5 and C7, a raised cosine over the corner's arc (the shape
    `tests/_synthetic.line_change_laps` uses), by displacing the circuit the GPS positions are
    drawn from (`_Planted`); AMP 0 / 0.5 / 1 / 2 m.

THE STATISTIC is what the sentence would print: for one corner, median(planted laps) −
median(the other laps), with a bootstrap 95 % interval over laps (B resamples, seeded). The rule
the market lane wrote down before any number came back:
  * apex: fire when |Δ| ≥ max(2 fix spacings, k·σ) AND the interval excludes 0 — one fix spacing
    is the median distance between the corner's fixes on the lap, σ the per-pass scatter of the
    figure around its corner median on the run's null corners;
  * line: build only if a 1 m change is recovered with the null's 95th percentile under half of
    it, per lap and for the group median.
Every figure is a truth error or a share of seeded draws; the probe is deterministic.

ARGV CONTRACT: nothing names an input or an output. Every recording is generated into ONE
`mkdtemp` under $TMPDIR and removed before exit (the placeholder picture: nothing decodes a frame);
the app-support store is jailed; the sidecar write is stubbed. Output is stdout; the last line is
the JSON. `--quick` runs one seed. Runtime ~16 min alone on a quiet Mac (69 recordings: 48
apex, 18 line, 3 noise-free controls; the line's AMP 0 is the apex's SHIFT 0 recording).
Also the quickest-third split (`order_check`, 4 of 14 passes) on every plant-free recording and on
the demo, and the demo's flat bottoms (`demo_flat_m`).

    pixi run python -m studio.dev.probes.apex_line [--quick]
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import os
import shutil
import sys
import tempfile

os.environ.setdefault("PACER_NO_MEDIA", "1")

from studio.dev._jail import divert_app_support  # noqa: E402

_JAIL = divert_app_support("apex_line_")

import numpy as np  # noqa: E402

from studio import sidecar  # noqa: E402  (after the jail, as every load must be)

sidecar.save = lambda *a, **k: None   # nothing here edits a line, and nothing may write beside it
from studio import chapters, corners  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.session import Session  # noqa: E402

SEEDS = (sg.DEFAULT_SEED, 1, 2)       # the truth matrix's three seeds
NOISES = (2.0, 4.5)                   # the truth matrix's GPS noise levels
SHIFTS = (0.0, 2.0, 5.0, 10.0)        # apex plant, m later along the lap
RISES = (1.0, 3.0)                    # the bowl's depth at ±10 m, km/h
AMPS = (0.0, 0.5, 1.0, 2.0)           # line plant, m wide
PLANTED = (1, 3, 5, 7)                # corners carrying the plant; 2, 4, 6 are the run's null
BOWL_M = 10.0
BOOT = 2000
K_SIGMA = 2.0
STRAIGHT_REF = (20.0, 60.0)           # the straights' re-zero band, m outside each window end


# ─── the plants ─────────────────────────────────────────────────────────────────────────────────
def _bowl(truth: sg.Truth, shift: float, rise_kmh: float) -> sg.Truth:
    """`truth` with every flying lap's speed given a unique minimum per corner: even grid laps at
    s_a = apex + `shift` for the PLANTED corners, everything else at the apex. The times are
    re-integrated from the new speeds exactly as `simulate` integrates them."""
    c, L = truth.circuit, truth.circuit.length
    d = truth.d_nodes
    v = truth.v_nodes.copy()
    s_abs = truth.s_start + d
    lap = np.floor(s_abs / L).astype(int)
    s_lap = s_abs - lap * L
    rise = rise_kmh / 3.6
    for k in range(1, truth.laps + 1):
        on_lap = lap == k
        for ct in c.corners:
            sa = ct.apex_s + (shift if (k % 2 == 0 and ct.cid in PLANTED) else 0.0)
            near = on_lap & (np.abs(s_lap - sa) < 40.0)
            if not near.any():
                continue
            v_a = float(np.interp(sa, s_lap[near], v[near]))
            bowl = v_a + rise * ((s_lap[near] - sa) / BOWL_M) ** 2
            v[near] = np.maximum(v[near], bowl)
    t = sg.T_LEAD + np.concatenate([[0.0], np.cumsum(2.0 * sg.DS / (v[:-1] + v[1:]))])
    return dataclasses.replace(truth, v_nodes=v, t_nodes=t, t_end=float(t[-1] + sg.T_TAIL))


@dataclasses.dataclass(frozen=True)
class _Planted(sg.Circuit):
    """The circuit with the even flying laps run `amp` m WIDE through the PLANTED corners: a
    raised cosine over each corner's arc along the outward normal. `at` is what `_gps_rows`
    draws the fixes from, with the UNWRAPPED distance, so the lap is known there; everything else
    (the centreline, the crossings, the truth readers) is the plain circuit's."""

    amp: float = 0.0

    def at(self, s):
        s = np.asarray(s, float)
        x, y, h, k = super().at(s)
        if not self.amp:
            return x, y, h, k
        lap = np.floor(s / self.length).astype(int)
        s_lap = s - lap * self.length
        push_l = np.zeros_like(s_lap)
        for ct in self.corners:
            if ct.cid not in PLANTED:
                continue
            half = abs(np.radians(ct.turn_deg)) * ct.radius / 2.0
            rel = s_lap - ct.apex_s
            on = (lap % 2 == 0) & (lap >= 2) & (np.abs(rel) < half)
            # wide = away from the corner's centre: right of a left-hander, left of a right
            push_l = push_l + np.where(on, -np.sign(ct.turn_deg) * self.amp * 0.5
                                       * (1.0 + np.cos(np.pi * rel / half)), 0.0)
        return x - push_l * np.sin(h), y + push_l * np.cos(h), h, k


@contextlib.contextmanager
def _planted(shift: float = 0.0, rise: float = RISES[0], amp: float = 0.0):
    real = sg.simulate

    def simulate(*a, **kw):
        t = _bowl(real(*a, **kw), shift, rise)
        c = t.circuit
        t.circuit = _Planted(**{f.name: getattr(c, f.name) for f in dataclasses.fields(c)},
                             amp=amp)
        return t

    sg.simulate = simulate
    try:
        yield
    finally:
        sg.simulate = real


# ─── one recording through the real loader ──────────────────────────────────────────────────────
_PICTURES: dict = {}


def _picture_in(tmp: str):
    def picture(path, truth, first, n, ffmpeg):
        key = (first, n)
        if key not in _PICTURES:
            _PICTURES[key] = os.path.join(tmp, f"picture_{first}_{n}.mp4")
            sg._encode_video(_PICTURES[key], n * sg.FRAMES_PER_PAYLOAD, ffmpeg)
        shutil.copyfile(_PICTURES[key], path)
    return picture


def _parabola_vertex(x, y) -> float:
    a, b, _c = np.polyfit(x, y, 2)
    if a <= 0:
        return float(x[int(np.argmin(y))])
    return float(np.clip(-b / (2 * a), x[0], x[-1]))


def measure_session(s: Session, truth: sg.Truth | None) -> list[dict]:
    """One row per (clean lap, corner): the apex position three ways, the lateral offset at the
    apex three ways, the corner time, the fix spacing there, and — when `truth` is given — the
    grid lap the pass belongs to (whose parity says whether it carries the plant)."""
    cm = s.corners
    basis = cm.basis()
    assert basis is not None and basis[0], "no corner basis"
    clist, total_ref = basis
    best = s.best_lap_id()
    ref_trace = cm._best_trace()
    geom = cm.geometry()
    assert geom is not None, "no session geometry"
    nx, ny = geom.frame[4], geom.frame[5]
    offset = None
    if truth is not None:
        tl = truth.crossings(s.timing_lines_latlon()[0])
        starts = {i: float(s._lap_columns(i)[0][0]) for i in s.valid_lap_ids()}
        offset = float(np.median([t0 - tl[int(np.argmin(np.abs(tl - t0)))]
                                  for t0 in starts.values()]))
    rows = []
    for lap in cm._clean_lap_ids():
        times, xs, ys, _v, cum = s._lap_columns(lap)
        dist, kmh, elapsed = s._lap_arrays(lap)
        total_lap = float(dist[-1])
        al = cm.lap_alignment(lap, total_lap)
        traces = cm._lap_traces(lap, ref_trace)
        edges = corners.project_boundaries([b for c in clist for b in (c.enter, c.exit)],
                                           total_ref, total_lap, traces=traces, alignment=al)
        stats = cm.lap_corner_stats(lap)
        off = geom.offsets(xs, ys, cum)
        tx, ty = cm.lap_shift(lap)
        off_dd = off - (nx * tx + ny * ty)
        for ci, (c, st) in enumerate(zip(clist, stats, strict=True)):
            d0, d1 = float(edges[2 * ci]), float(edges[2 * ci + 1])
            scale = (c.exit - c.enter) / (d1 - d0)
            idx = np.flatnonzero((dist >= d0) & (dist <= d1))
            j = int(idx[int(np.argmin(kmh[idx]))])
            assert dist[j] == st.apex_dist
            p3 = _parabola_vertex(dist[max(j - 1, 0):j + 2], kmh[max(j - 1, 0):j + 2])
            p5 = _parabola_vertex(dist[max(j - 2, 0):j + 3], kmh[max(j - 2, 0):j + 3])
            to_ref = lambda dd, d0=d0, c=c, scale=scale: c.enter + (dd - d0) * scale  # noqa: E731
            wide = -c.direction    # offsets are + left; wide is right of a left-hander
            at_apex = lambda o, c=c: float(np.interp(c.apex, geom.stations, o))  # noqa: E731
            band = (((geom.stations > c.enter - STRAIGHT_REF[1])
                     & (geom.stations < c.enter - STRAIGHT_REF[0]))
                    | ((geom.stations > c.exit + STRAIGHT_REF[0])
                       & (geom.stations < c.exit + STRAIGHT_REF[1])))
            straight = float(np.nanmedian(off_dd[band])) if np.isfinite(off_dd[band]).any() \
                else np.nan
            row = dict(lap=int(lap), cid=int(c.cid), best=lap == best, time=float(st.time),
                       apex=to_ref(float(st.apex_dist)), apex_p3=to_ref(p3), apex_p5=to_ref(p5),
                       fix_m=float(np.median(np.diff(dist[idx]))) if len(idx) > 1 else np.nan,
                       raw=wide * at_apex(off), dedrift=wide * at_apex(off_dd),
                       straights=wide * (at_apex(off_dd) - straight))
            if truth is not None:
                t_true = float(times[0] + np.interp(st.apex_dist, dist, elapsed)) - offset
                s_abs = truth.s_start + float(np.interp(t_true, truth.t_nodes, truth.d_nodes))
                row["grid_lap"] = int(s_abs // truth.circuit.length)
            rows.append(row)
    return rows


def run_case(tmp: str, seed: int, noise: float, *, shift=0.0, rise=RISES[0], amp=0.0) -> list[dict]:
    out = os.path.join(tmp, f"rec_{seed}_{noise:g}_{shift:g}_{rise:g}_{amp:g}")
    with _planted(shift, rise, amp):
        rec = sg.generate(out, seed=seed, gps_noise=noise, video=_picture_in(tmp))
    s = Session.load(chapters.discover_siblings(rec.paths[0]))
    rows = measure_session(s, rec.truth)
    shutil.rmtree(out)
    return rows


# ─── the statistic ──────────────────────────────────────────────────────────────────────────────
def group_delta(a, b, rng, boot=BOOT) -> tuple[float, float, float]:
    """(median(a) − median(b), bootstrap 2.5 %, 97.5 %) — laps resampled within each group."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = float(np.median(a) - np.median(b))
    ia = rng.integers(0, len(a), (boot, len(a)))
    ib = rng.integers(0, len(b), (boot, len(b)))
    bd = np.median(a[ia], axis=1) - np.median(b[ib], axis=1)
    lo, hi = np.percentile(bd, [2.5, 97.5])
    return d, float(lo), float(hi)


def _split(rows, cid, field):
    planted = [r[field] for r in rows if r["cid"] == cid and r["grid_lap"] % 2 == 0
               and np.isfinite(r[field])]
    other = [r[field] for r in rows if r["cid"] == cid and r["grid_lap"] % 2 == 1
             and np.isfinite(r[field])]
    return planted, other


def _scatter(rows, cids, field) -> np.ndarray:
    """Per-pass deviation of `field` from its corner's median, over the odd (unplanted) laps."""
    out = []
    for cid in cids:
        _p, o = _split(rows, cid, field)
        if len(o) > 1:
            out.extend(np.asarray(o) - np.median(o))
    return np.asarray(out)


def _robust_sd(x) -> float:
    x = np.asarray(x, float)
    return float(1.4826 * np.median(np.abs(x - np.median(x)))) if len(x) else float("nan")


def apex_rule(passes, other, fix_m, rng) -> tuple[bool, float, float, float, float]:
    """The market lane's rule for one corner: (fires, Δ, lo, hi, threshold). σ is the corner's
    own per-pass scatter (robust SD over every pass), the one the app could compute."""
    d, lo, hi = group_delta(passes, other, rng)
    sigma = _robust_sd(np.concatenate([np.asarray(passes) - np.median(passes),
                                       np.asarray(other) - np.median(other)]))
    thr = max(2.0 * fix_m, K_SIGMA * sigma)
    return bool(abs(d) >= thr and (lo > 0 or hi < 0)), d, lo, hi, thr


def _r(x, n=2):
    return None if x is None or not np.isfinite(x) else round(float(x), n)


def summarize_apex(cases: list[dict]) -> list[dict]:
    rng = np.random.default_rng(36)
    out = []
    for noise in NOISES:
        for rise in RISES:
            for field in ("apex", "apex_p3", "apex_p5"):
                sel = [c for c in cases if c["noise"] == noise and c["rise"] == rise]
                null_rows = [c["rows"] for c in sel if c["shift"] == 0.0]
                dev = np.concatenate([_scatter(r, range(1, 8), field) for r in null_rows])
                fix = float(np.median([r["fix_m"] for rows in null_rows for r in rows]))
                row = dict(noise=noise, rise=rise, field=field, n_dev=len(dev),
                           sd=_r(np.std(dev)), robust_sd=_r(_robust_sd(dev)),
                           p90_abs=_r(np.percentile(np.abs(dev), 90)), fix_m=_r(fix))
                fires_null, ci_null, n_null = 0, 0, 0
                for c in sel:
                    cids = range(1, 8) if c["shift"] == 0.0 else (2, 4, 6)
                    for cid in cids:
                        p, o = _split(c["rows"], cid, field)
                        f, d, lo, hi, _t = apex_rule(p, o, np.median(
                            [r["fix_m"] for r in c["rows"] if r["cid"] == cid]), rng)
                        fires_null += f
                        ci_null += (lo > 0 or hi < 0)
                        n_null += 1
                row.update(null_cells=n_null, null_fire=_r(fires_null / n_null, 3),
                           null_ci_excludes0=_r(ci_null / n_null, 3))
                for shift in SHIFTS[1:]:
                    ds, fires, cover = [], 0, 0
                    for c in (c for c in sel if c["shift"] == shift):
                        for cid in PLANTED:
                            p, o = _split(c["rows"], cid, field)
                            f, d, lo, hi, _t = apex_rule(p, o, np.median(
                                [r["fix_m"] for r in c["rows"] if r["cid"] == cid]), rng)
                            ds.append(d)
                            fires += f
                            cover += lo <= shift <= hi
                    row[f"shift{shift:g}"] = dict(median=_r(np.median(ds)), lo=_r(min(ds)),
                                                  hi=_r(max(ds)), fire=_r(fires / len(ds), 3),
                                                  ci_covers=_r(cover / len(ds), 3), n=len(ds))
                # laps per group for the CI alone to see a shift at 80 % power (normal
                # approximation, median's efficiency 1/1.2533): n = 2 (1.96 + 0.84)² (1.2533 σ)² / S²
                sig = _robust_sd(dev)
                row["laps_per_group_80pct"] = {f"{s:g}": int(np.ceil(
                    2 * (1.96 + 0.84) ** 2 * (1.2533 * sig) ** 2 / s ** 2)) for s in (2.0, 5.0)}
                out.append(row)
    return out


def _line_null_and_hits(cases, field):
    """Per lap: a planted-group lap minus the other group's median, at the null corners (and at
    every corner of AMP 0) and at the planted ones. Per group: the medians' difference."""
    null_lap, null_grp, hit_lap, hit_grp = [], [], {}, {}
    for c in cases:
        for cid in range(1, 8):
            p, o = _split(c["rows"], cid, field)
            if not p or not o:
                continue
            lap = list(np.asarray(p) - np.median(o))
            grp = float(np.median(p) - np.median(o))
            if c["amp"] == 0.0 or cid not in PLANTED:
                null_lap.extend(lap)
                null_grp.append(grp)
            else:
                hit_lap.setdefault(c["amp"], []).extend(lap)
                hit_grp.setdefault(c["amp"], []).append(grp)
    return np.asarray(null_lap), np.asarray(null_grp), hit_lap, hit_grp


def summarize_line(cases: list[dict]) -> list[dict]:
    out = []
    for noise in NOISES:
        sel = [c for c in cases if c["noise"] == noise]
        for field in ("raw", "dedrift", "straights"):
            nl, ng, hl, hg = _line_null_and_hits(sel, field)
            p95_lap = float(np.percentile(np.abs(nl), 95))
            p95_grp = float(np.percentile(np.abs(ng), 95))
            row = dict(noise=noise, field=field, null_lap_sd=_r(np.std(nl)),
                       null_lap_p95=_r(p95_lap), null_grp_p95=_r(p95_grp),
                       n_null_lap=len(nl), n_null_grp=len(ng))
            for amp in AMPS[1:]:
                lap, grp = np.asarray(hl[amp]), np.asarray(hg[amp])
                row[f"amp{amp:g}"] = dict(
                    lap_median=_r(np.median(lap)), lap_over_null_p95=_r(np.mean(lap > p95_lap), 3),
                    grp_median=_r(np.median(grp)), grp_lo=_r(grp.min()), grp_hi=_r(grp.max()),
                    grp_over_null_p95=_r(np.mean(grp > p95_grp), 3), n_grp=len(grp))
            row["build_rule_lap"] = bool(p95_lap < 0.5)
            row["build_rule_grp"] = bool(p95_grp < 0.5)
            out.append(row)
    # the lap-to-lap bias the de-drift and the straights remove: per lap, the mean over the null
    # corners of its deviation from each corner's median
    bias = {}
    for noise in NOISES:
        for field in ("raw", "dedrift", "straights"):
            per_lap = []
            for c in (c for c in cases if c["noise"] == noise):
                med = {cid: np.nanmedian([r[field] * 1.0 for r in c["rows"] if r["cid"] == cid])
                       for cid in (2, 4, 6)}
                laps = sorted({r["lap"] for r in c["rows"]})
                for lap in laps:
                    v = [r[field] - med[r["cid"]] for r in c["rows"]
                         if r["lap"] == lap and r["cid"] in med]
                    per_lap.append(float(np.mean(v)))
            bias[f"{noise:g}/{field}"] = _r(np.std(per_lap))
    out.append(dict(lap_bias_sd=bias))
    return out


def order_check(rows: list[dict]) -> list[dict]:
    """Per corner of a real-loader session with no planted truth (the demo): the quickest third of
    the passes (by the corner's own time) against the rest — the apex rule, its Δ, and the
    Spearman ρ of the apex position against the corner time; and the same Δ for the de-drifted
    lateral offset, with its group median against the line rule's floor."""
    rng = np.random.default_rng(37)
    out = []
    for cid in sorted({r["cid"] for r in rows}):
        rs = sorted((r for r in rows if r["cid"] == cid), key=lambda r: r["time"])
        k = max(2, len(rs) // 3)
        quick, rest = rs[:k], rs[k:]
        fix = float(np.median([r["fix_m"] for r in rs]))
        f, d, lo, hi, thr = apex_rule([r["apex"] for r in quick], [r["apex"] for r in rest],
                                      fix, rng)
        tm = np.array([r["time"] for r in rs])
        ap = np.array([r["apex"] for r in rs])
        rho = float(np.corrcoef(np.argsort(np.argsort(ap)), np.argsort(np.argsort(tm)))[0, 1])
        ld, llo, lhi = group_delta([r["dedrift"] for r in quick], [r["dedrift"] for r in rest], rng)
        out.append(dict(cid=cid, passes=len(rs), quick=k, fix_m=_r(fix), apex_delta=_r(d),
                        apex_ci=[_r(lo), _r(hi)], apex_threshold=_r(thr), apex_fires=f,
                        rho_apex_time=_r(rho), line_delta=_r(ld), line_ci=[_r(llo), _r(lhi)]))
    return out


def demo_flat_m() -> dict:
    """Per demo corner, the shortest stretch (over its flying laps) where the TRUE speed is within
    0.1 km/h of that pass's minimum: how long the demo's flat bottom is, i.e. how little "where the
    minimum is" means there."""
    from studio.dev import make_demo as md
    t = sg.simulate(md.DEMO_SEED, md.DEMO_LAPS, origin=md.DEMO_ORIGIN, habit=md.DEMO_HABIT)
    L = t.circuit.length
    s_abs = t.s_start + t.d_nodes
    lap = np.floor(s_abs / L).astype(int)
    s_lap = s_abs - lap * L
    out = {}
    for ct in t.circuit.corners:
        flat = []
        for k in range(1, t.laps + 1):
            m = (lap == k) & (np.abs(s_lap - ct.apex_s) < 40.0)
            v = t.v_nodes[m] * 3.6
            within = s_lap[m][v <= v.min() + 0.1]
            flat.append(float(within.max() - within.min()))
        out[ct.cid] = _r(min(flat), 1)
    return out


def demo_rows() -> list[dict]:
    from studio.dev import make_demo
    tmp = tempfile.mkdtemp(prefix="apex_line_demo_")
    try:
        path = os.path.join(tmp, "demo.mp4")
        make_demo.make(path, render=False)
        s = Session.load(chapters.discover_siblings(path))
        return measure_session(s, None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--quick", action="store_true", help="one seed only")
    args = ap.parse_args(argv)
    seeds = SEEDS[:1] if args.quick else SEEDS
    tmp = tempfile.mkdtemp(prefix="apex_line_")
    try:
        result = {"apex": [], "line": []}
        for noise in NOISES:
            for seed in seeds:
                for rise in RISES:
                    for shift in SHIFTS:
                        rows = run_case(tmp, seed, noise, shift=shift, rise=rise)
                        result["apex"].append(dict(seed=seed, noise=noise, rise=rise,
                                                   shift=shift, rows=rows))
                        print(f"apex seed {seed} noise {noise} rise {rise} shift {shift}: "
                              f"{len(rows)} rows", file=sys.stderr, flush=True)
                for amp in AMPS:
                    rows = (next(c["rows"] for c in result["apex"] if c["seed"] == seed
                                 and c["noise"] == noise and c["rise"] == RISES[0]
                                 and c["shift"] == 0.0) if amp == 0.0
                            else run_case(tmp, seed, noise, amp=amp))
                    result["line"].append(dict(seed=seed, noise=noise, amp=amp, rows=rows))
                    print(f"line seed {seed} noise {noise} amp {amp}: {len(rows)} rows",
                          file=sys.stderr, flush=True)
        result["control"] = controls(tmp)
        demo = demo_rows()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if _JAIL.created:
            shutil.rmtree(_JAIL.dir, ignore_errors=True)
    print(json.dumps(summarize(result, demo), default=float))
    return 0


def controls(tmp: str) -> dict:
    """The probe's own negative control: the same plants with the GPS noise switched off (seed
    DEFAULT), and a plant-free noise-free run for the quickest-third split."""
    return {"apex": run_case(tmp, SEEDS[0], 0.0, shift=5.0, rise=RISES[1]),
            "line": run_case(tmp, SEEDS[0], 0.0, amp=1.0),
            "null": run_case(tmp, SEEDS[0], 0.0)}


def summarize_controls(ctl: dict) -> dict:
    out = {}
    for key, field in (("apex", "apex"), ("line", "dedrift")):
        ds = []
        for cid in PLANTED:
            p, o = _split(ctl[key], cid, field)
            ds.append(float(np.median(p) - np.median(o)))
        out[key] = [_r(d) for d in ds]
    oc = order_check(ctl["null"])
    out["null_quick_line"] = [o["line_delta"] for o in oc]
    out["null_quick_line_ci_excl0"] = sum((o["line_ci"][0] > 0 or o["line_ci"][1] < 0) for o in oc)
    out["null_quick_apex_fires"] = sum(o["apex_fires"] for o in oc)
    return out


def summarize_order_nulls(cases: list[dict]) -> dict:
    """The quickest-third split on every plant-free recording: how often each sentence would
    print with nothing planted — the apex rule firing, and the line Δ's interval excluding 0
    (and on which side: negative = quicker passes read tighter)."""
    out = {}
    for noise in NOISES:
        cells = [o for c in cases if c["noise"] == noise and c["shift"] == 0.0
                 for o in order_check(c["rows"])]
        excl = [o for o in cells if o["line_ci"][0] > 0 or o["line_ci"][1] < 0]
        out[f"{noise:g}"] = dict(cells=len(cells), apex_fires=sum(o["apex_fires"] for o in cells),
                                 line_ci_excl0=len(excl),
                                 line_ci_excl0_tighter=sum(o["line_delta"] < 0 for o in excl),
                                 line_delta_median=_r(np.median([o["line_delta"] for o in cells])))
    return out


def summarize(result: dict, demo: list[dict]) -> dict:
    return {"apex": summarize_apex(result["apex"]), "line": summarize_line(result["line"]),
            "order_nulls": summarize_order_nulls(result["apex"]),
            "control": summarize_controls(result["control"]), "demo": order_check(demo),
            "demo_flat_m": demo_flat_m()}


if __name__ == "__main__":
    sys.exit(main())
