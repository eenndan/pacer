"""corner_refusals: two things the Wave 5 discovery round (2026-11-16) proposed about corners, each
measured on the synthetic demo. Their verdicts are `studio/docs/refused-2026-09.md` §33 and §34, and
`tests/test_measured_figures.py` re-runs this probe and holds each section to it.

WHY. Both are measured where anyone can re-run them, on the demo (`make_demo`'s telemetry, its
planted C1 habit included, nobody's driving; not re-measured on real footage here), through
`Session.load` alone: no window, ~10 s. Every figure is
arithmetic on what the session's own accessors return, and the one null is computed exactly, so
the probe is deterministic.
  * culprit — the sentence "N of M slow laps lost their time at one corner". The slow laps and
    their culprits are `Session.slow_lap_notes` (`story.culprit_cue`: a lap names a culprit only
    as `one`, `over_lap` or `two`). The statistic is the largest number of slow laps whose cue
    names one corner; a `two` lap counts at both corners its cue names (the cue shows both), and
    a `partial` or `spread` lap counts in M at none. THE NULL: each culprit-bearing slow lap draws
    its culprit corner(s) independently, every corner of the grid equally likely (a `two` lap
    draws two different ones), and p is the chance that some corner collects at least the
    observed count. Every corner equally likely is the null MOST generous to the sentence: any
    unequal weighting makes a shared corner likelier by chance. `weighted_p` and the table's
    `p_half_weighted` re-compute p with each corner weighted by its own lap-to-lap spread (the
    sample σ of its grid column over the clean laps that are not slow), to show it. The table is
    the same null for M slow laps with one culprit each: the smallest count that beats it at
    P_ALPHA, and p for half of them or more at one corner. Exact (`_p_at_least`), not drawn. And
    the gate "print only at p ≤ P_ALPHA": how often its equal-odds threshold fires by chance when
    the culprits follow the σ weights instead (`gate_weighted`; an illustration, since C1's σ is
    inflated by the planted habit).
    Also what the sentence can say of the demo's ONE PLANTED HABIT (`make_demo.DEMO_HABIT`): the ▼
    its corner carries, and how many of its laps are slow.
  * split — splitting a long corner window into two corners. Per corner the window's length on the
    reference lap and its ringed apex (`Corner.apex`, the point `corner_map_markers` puts the
    map's apex dot and ring on), metres into the window. In every window: the local maxima of the
    pooled curvature (`corners.pooled_curvature`, what `detect_corners` reads) and the lowest |κ|
    between the first and the last, against the window's peak; and the same between the maxima
    that reach MAJOR_SHARE of the peak. And where each pass LOSES its time: on a
    grid of GRID points along the window (each lap's own projected window, `project_boundaries`
    through its `lap_alignment`), the time since the window's entry against a typical pass (the
    median over the clean laps' resolved passes); the loss rate is its slope, smoothed over
    SMOOTH_M, and the PEAK LOSS is where that slope is highest, in the reference window's metres.
    For every ▼ pass of the grid, against the median pass; and for each habit lap at C1, against
    the median of the habit-free laps, with the share of its loss lost before the ringed apex.

ARGV CONTRACT: nothing names an input or an output. The demo is generated (`make_demo`, a
placeholder picture: nothing here decodes a frame) into ONE `mkdtemp` under $TMPDIR, removed before
exit; the app-support store is jailed; the sidecar write is stubbed. Output is stdout; the last line
is the JSON.

    pixi run python -m studio.dev.probes.corner_refusals
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from fractions import Fraction
from math import factorial

os.environ.setdefault("PACER_NO_MEDIA", "1")

from studio.dev._jail import divert_app_support  # noqa: E402

_JAIL = divert_app_support("corner_refusals_")

import numpy as np  # noqa: E402

from studio import sidecar  # noqa: E402  (after the jail, as every load must be)

sidecar.save = lambda *a, **k: None   # nothing here edits a line, and nothing may write beside it
from studio import corners as corners_mod  # noqa: E402
from studio import story  # noqa: E402
from studio.dev import make_demo  # noqa: E402
from studio.session import Session  # noqa: E402

P_ALPHA = 0.05              # a count beats chance when the null's p is at most this
TABLE_M = range(3, 9)       # the slow-lap counts the table spells out
GRID = 201                  # points along a corner window for the loss trace
SMOOTH_M = 3.0              # the loss rate's boxcar, metres: about two GPS fixes at C1's speed
MAJOR_SHARE = 0.5           # a curvature maximum counts as a bend at this share of its window's peak


# ─── §33 "N of M slow laps lost their time at one corner" ───────────────────────────────────────
def _poly_mul(a: list, b: list, n: int) -> list:
    out = [Fraction(0)] * (n + 1)
    for i, x in enumerate(a):
        if x:
            for j, y in enumerate(b[:n + 1 - i]):
                out[i + j] += x * y
    return out


def _p_at_least(m: int, k: int, weights) -> float:
    """P(some corner collects at least k of m single-culprit laps), each lap's culprit drawn
    independently with probability ∝ `weights`. Exact: P(every count < k) is m! × the x^m
    coefficient of Π_c Σ_{j<k} (w_c x)^j / j! (the multinomial, truncated per corner)."""
    tot = sum(Fraction(w).limit_denominator(10**9) for w in weights)
    poly = [Fraction(1)]
    for w in weights:
        p = Fraction(w).limit_denominator(10**9) / tot
        poly = _poly_mul(poly, [p**j / factorial(j) for j in range(min(k, m + 1))], m)
    below = poly[m] * factorial(m) if len(poly) > m else Fraction(0)
    return float(1 - below)


def _p_mixed(n_one: int, n_two: int, observed: int, corners: int) -> float:
    """p for a mix of one- and two-culprit laps under the uniform null, enumerated exactly (the
    demo's mixes are tiny: a lap's draw is one corner, or one unordered pair of different ones)."""
    if n_one + n_two == 0:
        return 1.0
    pairs = [(a, b) for a in range(corners) for b in range(a + 1, corners)]
    dist = {(0,) * corners: Fraction(1)}
    for options in [[(c,) for c in range(corners)]] * n_one + [pairs] * n_two:
        nxt: dict = {}
        for counts, pr in dist.items():
            for opt in options:
                c = list(counts)
                for k in opt:
                    c[k] += 1
                key = tuple(c)
                nxt[key] = nxt.get(key, Fraction(0)) + pr / len(options)
        dist = nxt
    return float(sum(pr for counts, pr in dist.items() if max(counts) >= observed))


def culprit(s: Session) -> dict:
    notes = s.slow_lap_notes()
    mx = s.corner_matrix()
    assert mx is not None, "the demo must have a CORNERS BY LAP grid"
    named = [tuple(int(c.cid) for c in n.corners) for n in notes if story.culprit_cue(n)]
    count = {c: sum(c in n for n in named) for c in mx.cids}
    top = max(count.values()) if named else 0
    n_one = sum(len(n) == 1 for n in named)
    n_two = sum(len(n) == 2 for n in named)
    p = _p_mixed(n_one, n_two, top, len(mx.cids)) if named else 1.0
    slow = {n.lap_id for n in notes}
    rest = [r for r, lap in enumerate(mx.lap_ids) if lap not in slow]
    sig = [float(np.std([mx.cells[r][k] for r in rest if mx.resolved[r][k]], ddof=1))
           for k in range(len(mx.cids))]
    weighted = (_p_at_least(len(named), top, sig) if named and n_two == 0 else None)
    table = []
    flat = [1] * len(mx.cids)
    for m in TABLE_M:
        k = next((k for k in range(1, m + 1) if _p_at_least(m, k, flat) <= P_ALPHA), None)
        half = -(-m // 2)
        # A gate "print only at p ≤ P_ALPHA" fires at k_min of m under the equal-odds null; if the
        # culprits instead follow each corner's σ, it fires this often by chance (`gate_weighted`).
        table.append({"m": m, "k_min": k, "p_k_min": round(_p_at_least(m, k, flat), 3) if k else None,
                      "gate_weighted": round(_p_at_least(m, k, sig), 3) if k else None,
                      "half": half, "p_half": round(_p_at_least(m, half, flat), 2),
                      "p_half_weighted": round(_p_at_least(m, half, sig), 2)})
    h = make_demo.DEMO_HABIT
    hk = mx.cids.index(h.cid)
    habit_laps = [lap for lap in mx.lap_ids if lap + 1 not in h.clean_laps]
    return {"clean_laps": len(mx.lap_ids), "corners": len(mx.cids), "slow": len(notes),
            "slow_laps": [int(n.lap_id) for n in notes], "categories": [n.category for n in notes],
            "culprits": [list(n) for n in named], "top_count": top,
            "top_cids": [int(c) for c, v in count.items() if v == top and top],
            "p": round(p, 2), "weighted_p": round(weighted, 2) if weighted is not None else None,
            "sigma_s": [round(x, 3) for x in sig], "alpha": P_ALPHA, "table": table,
            "habit_cid": int(h.cid), "habit_laps": len(habit_laps),
            "habit_marks": sum(mx.is_behind(r, hk) for r in range(len(mx.lap_ids))),
            "habit_slow": sum(lap in slow for lap in habit_laps),
            "habit_cell_s": [round(min(mx.cells[r][hk] - mx.medians[hk] for r, lap in enumerate(mx.lap_ids)
                                       if lap + 1 in h.clean_laps), 2),
                             round(max(mx.cells[r][hk] - mx.medians[hk] for r, lap in enumerate(mx.lap_ids)
                                       if lap + 1 in h.clean_laps), 2)],
            "step_s": round(float(mx.scales[hk]), 2)}


# ─── §34 splitting a long corner window ─────────────────────────────────────────────────────────
def _taus(s: Session, c, laps) -> dict:
    """{lap: time since the window's entry at GRID points along that lap's own projected window}."""
    total_ref = float(s.corners.basis()[1])
    f = np.linspace(0.0, 1.0, GRID)
    out = {}
    for lap in laps:
        t, _xs, _ys, _v, cum = (np.asarray(a, float) for a in s._lap_columns(lap))
        al = s.corners.lap_alignment(lap, float(cum[-1]))
        d0, d1 = corners_mod.project_boundaries([c.enter, c.exit], total_ref, float(cum[-1]), alignment=al)
        d = d0 + f * (d1 - d0)
        out[lap] = np.interp(d, cum, t) - float(np.interp(d0, cum, t))
    return out


def _peak(loss: np.ndarray, length_m: float) -> float:
    """Metres into the reference window where `loss` (s, on GRID points) grows fastest."""
    x = np.linspace(0.0, length_m, GRID)
    rate = np.gradient(loss, x)
    w = max(1, int(round(SMOOTH_M / (length_m / (GRID - 1)))))
    return float(x[int(np.argmax(np.convolve(rate, np.ones(w) / w, mode="same")))])


def split(s: Session) -> dict:
    mx = s.corner_matrix()
    assert mx is not None
    cl = s.corners.corner_list()
    windows = [{"cid": int(c.cid), "length_m": round(c.exit - c.enter, 1), "apex_m": round(c.apex - c.enter, 1)}
               for c in cl]
    # ▼ passes, each against its corner's median pass.
    marked = []
    for k, c in enumerate(cl):
        rows = [r for r in range(len(mx.lap_ids)) if mx.resolved[r][k]]
        if not any(mx.is_behind(r, k) for r in rows):
            continue
        taus = _taus(s, c, [mx.lap_ids[r] for r in rows])
        typ = np.median(np.array(list(taus.values())), axis=0)
        for r in rows:
            if mx.is_behind(r, k):
                lap = mx.lap_ids[r]
                pk = _peak(taus[lap] - typ, c.exit - c.enter)
                marked.append({"cid": int(c.cid), "lap": int(lap), "peak_m": round(pk, 1),
                               "apex_m": round(c.apex - c.enter, 1),
                               "from_apex_m": round(pk - (c.apex - c.enter), 1)})
    # The habit's corner: its curvature, and where each habit lap loses against the habit-free laps.
    h = make_demo.DEMO_HABIT
    c = next(x for x in cl if x.cid == h.cid)
    hk = mx.cids.index(h.cid)
    traces = [tuple(s._lap_columns(i)[1:3]) + (s._lap_columns(i)[4],) for i in s.consistency_lap_ids()]
    dg, kap = (np.asarray(a, float) for a in corners_mod.pooled_curvature(traces, float(s.corners.basis()[1])))
    bends = {}
    for x in cl:
        inside = (dg >= x.enter) & (dg <= x.exit)
        kk, dd = np.abs(kap[inside]), dg[inside] - x.enter
        peaks = [i for i in range(1, len(kk) - 1) if kk[i] >= kk[i - 1] and kk[i] > kk[i + 1]]
        lo = int(peaks[0] + np.argmin(kk[peaks[0]:peaks[-1] + 1])) if len(peaks) > 1 else None
        # The same dip between the maxima that reach MAJOR_SHARE of the peak: a ripple of a
        # straightened exit is a "maximum" too, and the plain dip reads the straight between them.
        major = [i for i in peaks if kk[i] >= MAJOR_SHARE * kk.max()]
        dip_major = float(kk[major[0]:major[-1] + 1].min() / kk.max()) if len(major) > 1 else 1.0
        bends[int(x.cid)] = {
            "major": len(major), "dip_major": round(dip_major, 3),
            "maxima": len(peaks), "maxima_m": [round(float(dd[i]), 1) for i in peaks],
            "maxima_rel": [round(float(kk[i] / kk.max()), 3) for i in peaks],
            "dip": round(float(kk[lo] / kk.max()), 3) if lo is not None else 1.0,
            "length_m": round(x.exit - x.enter, 1)}
    hb = bends[int(h.cid)]
    rows = [r for r in range(len(mx.lap_ids)) if mx.resolved[r][hk]]
    taus = _taus(s, c, [mx.lap_ids[r] for r in rows])
    free = [lap for lap in taus if lap + 1 in h.clean_laps]
    typ = np.median(np.array([taus[i] for i in free]), axis=0)
    length, apex = c.exit - c.enter, c.apex - c.enter
    ia = int(round(apex / length * (GRID - 1)))
    habit = []
    for lap in (x for x in taus if x not in free):
        loss = taus[lap] - typ
        pk = _peak(loss, length)
        habit.append({"lap": int(lap), "loss_s": round(float(loss[-1]), 3), "peak_m": round(pk, 1),
                      "from_apex_m": round(pk - apex, 1), "before_apex": round(float(loss[ia] / loss[-1]), 2)})
    lengths = sorted((w["length_m"] for w in windows), reverse=True)
    hw = next(w for w in windows if w["cid"] == h.cid)
    return {"windows": windows, "habit_cid": int(h.cid), "habit_length_rank": lengths.index(hw["length_m"]) + 1,
            "longest": max(windows, key=lambda w: w["length_m"]),
            "kappa_peaks_m": hb["maxima_m"], "kappa_dip": hb["dip"], "bends": bends,
            "habit_marks": sum(mx.is_behind(r, hk) for r in range(len(mx.lap_ids))),
            "marked": marked, "free_laps": len(free), "habit": habit,
            "habit_loss_s": [min(x["loss_s"] for x in habit), max(x["loss_s"] for x in habit)],
            "habit_peak_m": [min(x["peak_m"] for x in habit), max(x["peak_m"] for x in habit)],
            "habit_from_apex_m": max(abs(x["from_apex_m"]) for x in habit),
            "habit_before_apex": [min(x["before_apex"] for x in habit), max(x["before_apex"] for x in habit)],
            "smooth_m": SMOOTH_M}


def main(argv=None) -> int:
    work = tempfile.mkdtemp(prefix="corner_refusals_")
    try:
        recording = os.path.join(work, "demo.mp4")
        make_demo.make(recording, render=False)
        s = Session.load([recording])
        res = {"culprit": culprit(s), "split": split(s)}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("culprit " + ", ".join(f"{k} {v}" for k, v in res["culprit"].items()))
    print("split   " + ", ".join(f"{k} {v}" for k, v in res["split"].items()))
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
