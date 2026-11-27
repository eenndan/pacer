"""story_refusals: four things the Wave 4 design pass (2026-11-15) proposed for telling the session's
story, each measured on the synthetic demo. Their verdicts are `studio/docs/refused-2026-09.md`
§29, §30, §31 and §32, and `tests/test_measured_figures.py` re-runs this probe and holds each
section to it.

WHY. The design pass measured these on the owner's footage, which cannot be published. This
re-measures each one where anyone can, on the demo (`make_demo`'s telemetry, its planted C1 habit
included, nobody's driving). Every figure is arithmetic on what the session's own accessors
return, so it is deterministic. The demo opens in the real window only so the map's scale is the
map's own (~20 s in all, most of it generating the demo and opening the window).
  * brake — brake-onset spread bands on the map. Per braking zone, over the clean laps' brake rows
    (`Session._brake_rows_by_lap`, the one list every braking surface reads): the middle-half
    spread of the onset position (the interquartile range, in metres) against the distance the kart
    travels in ONE GPS fix at that place (the best lap's speed at the median onset × the
    recording's median fix interval). A band drawn only where it clears one fix is the design's
    rule; how many zones clear it is the figure, and the widest band's length on the map pinned at
    MAP_SIZE (§28's size). The planted habit's corner is also split by the generator's truth (the
    habit laps, the clean ones), to say what its band would be made of, beside what the app already
    says there: its BRAKING row (`Session.brake_report`) and its brake-direction line
    (`Session.coaching_brake_direction`).
  * nailed — "what you nailed": the mirror of the Coaching page's opportunity gate, a corner whose
    typical pass is within `library.PB_PRECISION_S` (the 0.07 s the app calls level) of its best
    pass (`Session.corner_report`'s median − best). Then the same rule under ±PB_PRECISION_S / 2 of
    cell jitter (JITTER_DRAWS seeded draws): how often each corner fires and how often the same
    number of corners does; and which praised corners the Coaching page ranks as opportunities.
  * film — an ideal-lap film stitched from each segment's best pass (`Session.ideal_segment_bests`):
    donors, cuts, the film's length and a cut's spacing; at every cut between two donors the jump
    in speed and in position (each donor's own lap, at its own odometer edge), against the same
    jumps at SEED-ed random cuts between two clean laps at one partition edge (FILM_BASELINE_CUTS).
  * moments — "session moments" under the app's level step: JITTER_DRAWS seeded draws, every lap
    time moved by a uniform ±PB_PRECISION_S and every corner cell by ±PB_PRECISION_S / 2, all
    independent; per moment, the share of draws in which the lap it names changes. The same again
    at the MEASURED timing noise, Gaussian σ TIMING_SIGMA_S per lap and TIMING_SIGMA_S / √2 per
    cell (docs/ACCURACY.md row C), which is narrower. The longest run
    without a ▼ recomputes the CORNERS BY LAP grid (`stats.corner_matrix`) from the jittered cells.
    Also each corner's tie group: the passes within PB_PRECISION_S of its quickest, and the 5-lap
    windows within PB_PRECISION_S of the best one. And the longest run without a ▼ on NOISE_DRAWS
    seeded grids of pure Gaussian noise (the demo's laps, corners and column medians; each column's
    sample σ, and again its robust σ, 1.4826 × MAD), marked by the real rule, with the ▼ each grid
    carries against the demo's own: a run that is stable under jitter must still beat noise.

ARGV CONTRACT: nothing names an input or an output. The demo is generated (`make_demo`, a
placeholder picture: nothing here decodes a frame) into ONE `mkdtemp` under $TMPDIR, removed before
exit; the app-support store is jailed; the sidecar write is stubbed. Output is stdout; the last line
is the JSON.

    pixi run python -m studio.dev.probes.story_refusals
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")

from studio.dev._jail import divert_app_support  # noqa: E402

_JAIL = divert_app_support("story_refusals_")

import numpy as np  # noqa: E402

from studio import sidecar  # noqa: E402  (after the jail, as every load must be)

sidecar.save = lambda *a, **k: None   # a line edit writes beside the recording: never here
from studio import stats as stats_service  # noqa: E402
from studio._signal import lap_label  # noqa: E402
from studio.dev import make_demo  # noqa: E402
from studio.dev import media_capture as mc  # noqa: E402
from studio.library import PB_PRECISION_S  # noqa: E402
from studio.session import Session  # noqa: E402

SEED = 7                    # one generator for every random draw below: the probe is deterministic
JITTER_DRAWS = 1000
NOISE_DRAWS = 1000          # pure-noise grids for the run with no ▼ (§32), at two σ: ~2 s
# docs/ACCURACY.md row C: the measured σ of a lap time against official timing (the test ties it).
TIMING_SIGMA_S = 0.0247
FILM_BASELINE_CUTS = 200
RUNS = (3, 5)               # the run lengths the moments name
MAP_WINDOW = mc.WINDOW      # 1440x900 logical, as motion_refusals (§28) opens it
MAP_SIZE = (480, 480)       # the map widget, logical px, pinned as in §28: the scale must not follow fonts/layout


# ─── §29 brake-onset bands ──────────────────────────────────────────────────────────────────────
def habit_free(lap_id: int) -> bool:
    """Whether the session's 0-based lap `lap_id` is one the demo's habit spares: `DEMO_HABIT.clean_laps`
    are 1-based lap numbers (`_signal.lap_label`), so lap id 6 is lap 7."""
    return int(lap_label(lap_id)) in make_demo.DEMO_HABIT.clean_laps


def map_px_per_m(app, w) -> float:
    """Logical px per metre on the real window's map, pinned at MAP_SIZE (the axes are locked 1:1)."""
    m = w.view.map
    w.view.tab_bar.setCurrentIndex(0)
    m.widget.setFixedSize(*MAP_SIZE)
    mc.settle(app)
    px_w, px_h = m.plot.getViewBox().viewPixelSize()
    assert abs(px_w - px_h) < 1e-6 * px_w, "the map's axes are no longer locked 1:1"
    return 1.0 / float(px_w)


def brake_bands(s: Session, px_per_m: float) -> dict:
    best = s.best_lap_id()
    t = np.asarray(s._lap_columns(best)[0], float)
    fix_s = float(np.median(np.diff(t)))
    dist, kmh, _el = (np.asarray(a, float) for a in s._lap_arrays(best))
    rows = s._brake_rows_by_lap()
    zones = []
    for c in s.corners.corner_list():
        on = np.array([r[c.cid][0] for _lap, r in rows if c.cid in r], float)
        if len(on) < 3:
            continue
        q1, med, q3 = np.percentile(on, [25, 50, 75])
        fix_m = float(np.interp(med, dist, kmh)) / 3.6 * fix_s
        zones.append({"cid": int(c.cid), "n": len(on), "iqr_m": round(float(q3 - q1), 3),
                      "fix_m": round(fix_m, 3), "clears": bool(q3 - q1 > fix_m)})
    # The planted habit's corner, its laps split by the generator's truth: with the habit, without.
    # `clean_laps` counts the generator's flying laps from 1, as the Laps table labels them, while a
    # row's lap is the session's 0-based id: `habit_free` is the one conversion.
    # Each group's time through the corner (the CORNERS BY LAP grid) says which laps the habit
    # really slowed, whatever their ids are called: the test holds the two ranges apart.
    h = make_demo.DEMO_HABIT
    mx = s.corner_matrix()
    assert mx is not None
    hk = mx.cids.index(h.cid)
    cell = {lap: float(mx.cells[r][hk]) for r, lap in enumerate(mx.lap_ids) if mx.resolved[r][hk]}
    groups = {}
    for name, keep in (("habit", lambda lap: not habit_free(lap)), ("clean", habit_free)):
        laps = [lap for lap, r in rows if h.cid in r and keep(lap)]
        on = np.array([r[h.cid][0] for lap, r in rows if h.cid in r and keep(lap)], float)
        q1, med, q3 = np.percentile(on, [25, 50, 75])
        groups[name] = {"n": len(on), "median_m": round(float(med), 1), "iqr_m": round(float(q3 - q1), 2),
                        "laps": [int(x) for x in laps],
                        "corner_s": [round(min(cell[x] for x in laps), 3), round(max(cell[x] for x in laps), 3)]}
    widest = max(z["iqr_m"] for z in zones)
    # What the app already says about the habit's corner: its BRAKING row and its brake-direction line.
    bc = next(r for r in s.brake_report() if r.cid == h.cid)
    bd = s.coaching_brake_direction().get(h.cid)
    return {"laps": len(rows), "fix_s": round(fix_s, 3), "zones": zones,
            "map_px_per_m": round(px_per_m, 3), "widest_px": round(widest * px_per_m, 1),
            "clear": [z["cid"] for z in zones if z["clears"]], "habit_cid": h.cid, "habit_groups": groups,
            "braking": {"sigma_m": round(float(bc.sigma_m), 2), "span_m": round(float(bc.span_m), 2)},
            "direction": {"verdict": bd.verdict if bd else None, "rho": round(float(bd.rho), 3) if bd else None,
                          "p_holm": round(float(bd.p_holm), 4) if bd else None}}


# ─── §30 what you nailed ────────────────────────────────────────────────────────────────────────
def _nailed_from(cells: list, resolved: list, n: int) -> list[int]:
    """The corner columns whose typical pass (median) is within PB_PRECISION_S of the best one."""
    out = []
    for k in range(n):
        col = [row[k] for row, res in zip(cells, resolved, strict=True) if res[k]]
        if float(np.median(col)) - min(col) <= PB_PRECISION_S:
            out.append(k)
    return out


def nailed(s: Session) -> dict:
    rep = s.corner_report()
    loss = {int(r.cid): round(float(r.median_loss_s), 3) for r in rep if r.median_loss_s is not None}
    fires = sorted(k for k, v in loss.items() if v <= PB_PRECISION_S)
    # Under the corner cells' share of the level step (±PB_PRECISION_S / 2, as §32's jitter), how
    # often each corner fires, and how often the same NUMBER of corners does.
    mx = s.corner_matrix()
    assert mx is not None
    cells = [[float(c) for c in row] for row in mx.cells]
    assert [mx.cids[k] for k in _nailed_from(cells, mx.resolved, len(mx.cids))] == fires, \
        "the grid's median − best is no longer corner_report's"
    rng = np.random.default_rng([SEED, 30])
    per = dict.fromkeys(mx.cids, 0)
    same = 0
    for _ in range(JITTER_DRAWS):
        cj = [[c + rng.uniform(-PB_PRECISION_S / 2, PB_PRECISION_S / 2) for c in row] for row in cells]
        hit = _nailed_from(cj, mx.resolved, len(mx.cids))
        same += len(hit) == len(fires)
        for k in hit:
            per[mx.cids[k]] += 1
    opps = s.coaching_opportunities()
    ranked = [int(o.cid) for o in opps.ranked_rows()]
    # Since Coaching ranks only past the timing step its level corners are held back, not ranked.
    level = [int(o.cid) for o in opps.rows if o.evidence.abstain == "level"]
    return {"corners": len(rep), "median_loss": loss, "fires": fires,
            "jitter_same_count": round(same / JITTER_DRAWS, 2),
            "jitter_fires": {str(c): round(v / JITTER_DRAWS, 2) for c, v in per.items()},
            "coaching_ranked": ranked, "praised_and_ranked": [c for c in fires if c in ranked],
            "praised_and_level": [c for c in fires if c in level]}


# ─── §31 the ideal-lap film ─────────────────────────────────────────────────────────────────────
def _at(s: Session, lap: int, d: float) -> tuple[float, float, float]:
    """(x, y, km/h) on `lap` at its own odometer metre `d`."""
    _t, xs, ys, v, cum = (np.asarray(a, float) for a in s._lap_columns(lap))
    return float(np.interp(d, cum, xs)), float(np.interp(d, cum, ys)), float(np.interp(d, cum, v)) * 3.6


def film(s: Session, rng) -> dict:
    sb = s.ideal_segment_bests()
    assert sb is not None, "the demo must have a corner partition"
    dv, dp = [], []
    for j in range(1, len(sb.bests)):
        a, b = sb.donors[j - 1], sb.donors[j]
        if a is None or b is None or a == b:
            continue
        xa, ya, va = _at(s, a, sb.donor_span[j - 1][1])
        xb, yb, vb = _at(s, b, sb.donor_span[j][0])
        dv.append(abs(va - vb))
        dp.append(float(np.hypot(xa - xb, ya - yb)))
    ids = s.consistency_lap_ids()
    length = {i: float(np.asarray(s._lap_columns(i)[4], float)[-1]) for i in ids}
    bv, bp = [], []
    for _ in range(FILM_BASELINE_CUTS):
        a, b = (int(x) for x in rng.choice(ids, 2, replace=False))
        e = sb.s_edges[int(rng.integers(1, len(sb.bests)))]
        xa, ya, va = _at(s, a, e * length[a])
        xb, yb, vb = _at(s, b, e * length[b])
        bv.append(abs(va - vb))
        bp.append(float(np.hypot(xa - xb, ya - yb)))
    cuts = len(dv)
    return {"segments": len(sb.bests), "donors": len(sb.donor_ids()), "cuts": cuts,
            "ideal_s": round(sb.total, 2), "best_s": round(float(s.lap_time(s.best_lap_id())), 2),
            "cut_every_s": round(sb.total / (cuts + 1), 1),
            "seam_kmh": [round(float(np.median(dv)), 1), round(max(dv), 1)],
            "seam_m": [round(float(np.median(dp)), 1), round(max(dp), 1)],
            "random_kmh_p90": round(float(np.percentile(bv, 90)), 1),
            "random_m_p90": round(float(np.percentile(bp, 90)), 1)}


# ─── §32 session moments under the timing's noise ───────────────────────────────────────────────
def _no_flag_run(mx, order) -> tuple[int, int | None]:
    """(length, first lap) of the longest run of consecutive lap ids with no ▼ in any corner."""
    rows = {lap: r for r, lap in enumerate(mx.lap_ids)}
    best, cur = (0, None), []
    for i in order:
        r = rows.get(i)
        clean = r is not None and not any(mx.is_behind(r, k) for k in range(len(mx.cids)))
        cur = (cur + [i]) if (clean and cur and i == cur[-1] + 1) else ([i] if clean else [])
        if len(cur) > best[0]:
            best = (len(cur), cur[0])
    return best


def _windows(lt: dict, order: list[int], w: int) -> list[tuple[float, int]]:
    """(median lap time, first lap) of every run of `w` consecutive clean lap ids, quickest first."""
    out = [(float(np.median([lt[i] for i in order[k:k + w]])), order[k])
           for k in range(len(order) - w + 1) if order[k + w - 1] - order[k] == w - 1]
    return sorted(out)


def _moments(lt: dict, cells: list, mx, order: list[int]) -> dict:
    m: dict = {"best_lap": min(lt, key=lt.get)}
    for w in RUNS:
        m[f"run{w}"] = _windows(lt, order, w)[0][1]
    meds = [float(np.median([row[k] for row, res in zip(cells, mx.resolved, strict=True) if res[k]]))
            for k in range(len(mx.cids))]
    m["worst_corner"] = max(((row[k] - meds[k], lap, mx.cids[k])
                             for lap, row, res in zip(mx.lap_ids, cells, mx.resolved, strict=True)
                             for k in range(len(mx.cids)) if res[k]))[1:]
    for k, cid in enumerate(mx.cids):
        m[f"best_C{cid}"] = min((row[k], lap) for lap, row, res
                                in zip(mx.lap_ids, cells, mx.resolved, strict=True) if res[k])[1]
    m["no_flag_run"] = _no_flag_run(stats_service.corner_matrix(mx.lap_ids, mx.cids, cells, mx.resolved),
                                    order)
    return m


def moments(s: Session, rng) -> dict:
    mx = s.corner_matrix()
    assert mx is not None, "the demo must have a CORNERS BY LAP grid"
    lt = {i: float(s.lap_time(i)) for i in mx.lap_ids}
    order = sorted(lt)
    cells = [[float(c) for c in row] for row in mx.cells]
    base = _moments(lt, cells, mx, order)
    share = _shares(base, lt, cells, mx, order, rng, lambda g, n: g.uniform(-PB_PRECISION_S, PB_PRECISION_S, n), 0.5)
    ties = {}
    for k, cid in enumerate(mx.cids):
        v = [row[k] for row, res in zip(cells, mx.resolved, strict=True) if res[k]]
        ties[str(cid)] = sum(1 for x in v if x - min(v) <= PB_PRECISION_S)
    corner_flips = [share[f"best_C{c}"] for c in mx.cids]
    tied = [c for c in mx.cids if ties[str(c)] > 1]
    tied_runs = {}
    for w in RUNS:
        win = _windows(lt, order, w)
        tied_runs[f"run{w}_tied"] = sum(1 for m, _f in win if m - win[0][0] <= PB_PRECISION_S)
        tied_runs[f"run{w}_windows"] = len(win)
    srt = sorted(lt.values())
    return {"laps": len(lt), "corners": len(mx.cids), "draws": JITTER_DRAWS, "share": share,
            "corner_share": [min(corner_flips), max(corner_flips)],
            "corner_ties": [min(ties.values()), max(ties.values())], "ties": ties,
            "tied_corners": len(tied),
            "tied_share": [min(share[f"best_C{c}"] for c in tied), max(share[f"best_C{c}"] for c in tied)],
            "tied_ties": [min(ties[str(c)] for c in tied), max(ties[str(c)] for c in tied)],
            "alone": [int(c) for c in mx.cids if ties[str(c)] == 1],
            **tied_runs,
            "best_lap_margin_s": round(srt[1] - srt[0], 3),
            "no_flag_run": list(base["no_flag_run"]),
            "noise": noise_runs(mx, cells, order, rng),
            "timing_sigma_s": TIMING_SIGMA_S,
            "share_timing": (st := _shares(base, lt, cells, mx, order, np.random.default_rng([SEED, 32]),
                                           lambda g, n: g.normal(0.0, TIMING_SIGMA_S, n), 1.0 / np.sqrt(2.0))),
            "tied_share_timing": [min(st[f"best_C{c}"] for c in tied), max(st[f"best_C{c}"] for c in tied)]}


def _shares(base: dict, lt: dict, cells: list, mx, order: list[int], rng, draw, cell_scale: float) -> dict:
    """Per moment, the share of JITTER_DRAWS draws in which the lap it names changes: every lap time
    moved by draw(rng, n), every corner cell by draw(rng, n) × cell_scale, all independent."""
    flips = dict.fromkeys(base, 0)
    ids = list(lt)
    for _ in range(JITTER_DRAWS):
        dl = draw(rng, len(ids))
        lj = {i: lt[i] + float(d) for i, d in zip(ids, dl, strict=True)}
        cj = [[c + float(d) * cell_scale for c, d in zip(row, draw(rng, len(row)), strict=True)] for row in cells]
        for key, val in _moments(lj, cj, mx, order).items():
            flips[key] += val != base[key]
    return {k: round(v / JITTER_DRAWS, 3) for k, v in flips.items()}


def _marks(m) -> int:
    """How many cells of a CornerMatrix carry a ▼."""
    return sum(m.is_behind(r, k) for r in range(len(m.lap_ids)) for k in range(len(m.cids)))


def noise_runs(mx, cells: list, order: list[int], rng) -> dict:
    """The longest run with no ▼ on grids of PURE NOISE: NOISE_DRAWS seeded grids at the demo's lap
    and corner counts, each column its demo median plus Gaussian noise of that column's own sample
    σ, every cell resolved, marked by the real rule (`stats.corner_matrix`). Stability under jitter
    cannot tell a calm stretch from this; the run must beat what noise alone produces."""
    cols = np.array(cells, float).T
    med = np.median(cols, axis=1)
    sig = np.std(cols, axis=1, ddof=1)
    mad = 1.4826 * np.median(np.abs(cols - med[:, None]), axis=1)    # the robust σ of each column
    res = [[True] * len(mx.cids)] * len(mx.lap_ids)
    demo = _no_flag_run(mx, order)[0]

    def grids(scale, g) -> dict:
        runs, marks = [], []
        for _ in range(NOISE_DRAWS):
            grid = (med[:, None] + g.normal(0.0, 1.0, cols.shape) * scale[:, None]).T.tolist()
            m = stats_service.corner_matrix(mx.lap_ids, mx.cids, grid, res)
            runs.append(_no_flag_run(m, order)[0])
            marks.append(_marks(m))
        a = np.array(runs)
        return {"sigma_s": [round(float(scale.min()), 3), round(float(scale.max()), 3)],
                "median": int(np.percentile(a, 50, method="inverted_cdf")),
                "p90": int(np.percentile(a, 90, method="inverted_cdf")),
                "at_least_demo": round(float(np.mean(a >= demo)), 2),
                "marks_mean": round(float(np.mean(marks)), 1)}

    out = {"draws": NOISE_DRAWS, "demo_marks": _marks(mx),
           "demo_marked_cids": sorted({int(mx.cids[k]) for r in range(len(mx.lap_ids))
                                       for k in range(len(mx.cids)) if mx.is_behind(r, k)}),
           **grids(sig, rng)}
    out["mad"] = grids(mad, np.random.default_rng([SEED, 33]))
    return out


def figures(s: Session, px_per_m: float) -> dict:
    rng = np.random.default_rng(SEED)
    return {"brake": brake_bands(s, px_per_m), "nailed": nailed(s), "film": film(s, rng), "moments": moments(s, rng)}


def main(argv=None) -> int:
    work = tempfile.mkdtemp(prefix="story_refusals_")
    try:
        recording = os.path.join(work, "demo.mp4")
        make_demo.make(recording, render=False)
        app = mc.boot_app()
        w = mc.open_window(app, recording, size=MAP_WINDOW)
        mc._settle_ui(app, w)
        res = figures(w.session, map_px_per_m(app, w))
        w.close()
        mc.settle(app, 2)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    b = res["brake"]
    print(f"brake   {b['laps']} laps, fix {b['fix_s']} s; " + "; ".join(
        f"C{z['cid']} IQR {z['iqr_m']} m vs one fix {z['fix_m']} m" for z in b["zones"])
        + f"; zones clearing one fix: {b['clear']}; widest {b['widest_px']} px at {b['map_px_per_m']} px/m; C{b['habit_cid']} by the habit: {b['habit_groups']}")
    n = res["nailed"]
    print(f"nailed  fires at {len(n['fires'])} of {n['corners']} corners {n['fires']}; median − best {n['median_loss']}")
    print("film    " + ", ".join(f"{k} {v}" for k, v in res["film"].items()))
    print("moments " + ", ".join(f"{k} {v}" for k, v in res["moments"].items()))
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
