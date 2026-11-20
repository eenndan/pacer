"""corner_typical_compare: the corner twin of `sector_typical_compare` — where "Compare with my
typical pass" is offered on a CORNER row of Corners ▸ Quickest passes, and how much of the gap it
names is real.

WHY. The compare is offered where the printed typical pass minus the printed quickest beats the
timing step (`stats.is_level`, 0.07 s for a corner). The quickest of 14 noisy times is partly a
lucky one, so a gap measured past the step can be mostly noise. Per corner row: the quickest lap,
`Session.pass_vs_typical`, whether the compare is offered, and the TRUE typical − quickest gap,
both passes timed between fixed truth positions of the corner's entry and exit (the quickest
lap's app entry and exit mapped to the truth's arc length). Then every counted pass against its
own typical: how many are offered, and how many of those are truly under the step.

THE GRID. `sector_quickest`'s: the demo (published noise 1, and re-generated at GPS noise 2 and
4.5) and `synth_gopro` seeds 1 and 2 at noise 2 and 4.5. First written by the PR #641 review.

ARGV CONTRACT: none. Every recording is generated into ONE `mkdtemp` under $TMPDIR and removed
before exit; the app-support store is jailed and the sidecar write stubbed (on importing
`sector_quickest`). Output is stdout, its last line the JSON summary.

    pixi run python -m studio.dev.probes.corner_typical_compare
"""
from __future__ import annotations

import json
import shutil
import tempfile

import numpy as np

from studio.dev.probes.sector_quickest import _sessions  # jails + stubs the sidecar on import


def true_corner_times(s, truth, cid, ref_lap, laps) -> dict[int, float]:
    """Each lap's TRUE time between the truth positions of `ref_lap`'s app entry and exit."""
    import pacer  # the core's local → global conversion, after the jail above

    cm = s.corners
    tl = truth.crossings(s.timing_lines_latlon()[0])
    starts = {i: float(s._lap_columns(i)[0][0]) for i in s.valid_lap_ids()}
    offset = float(np.median([t0 - tl[int(np.argmin(np.abs(tl - t0)))] for t0 in starts.values()]))
    t, xs, ys = s._lap_columns(ref_lap)[:3]
    sl = {}
    for edge in ("enter", "exit"):
        te = cm.corner_edge_media_time(ref_lap, cid, edge)
        x, y = float(np.interp(te, t, xs)), float(np.interp(te, t, ys))
        g = s.cs.global_(pacer.Vec3f(x, y, 0.0))
        sl[edge] = float(np.asarray(truth.s_at_latlon(np.array([g.lat]), np.array([g.lon])))[0])
    te_all, tx_all = truth.times_at(sl["enter"]), truth.times_at(sl["exit"])
    out = {}
    for lap in laps:
        e = cm.corner_edge_media_time(lap, cid, "enter")
        x = cm.corner_edge_media_time(lap, cid, "exit")
        if e is None or x is None:
            continue
        ke = te_all[int(np.argmin(np.abs(te_all - (e - offset))))]
        kx = tx_all[int(np.argmin(np.abs(tx_all - (x - offset))))]
        if abs(ke - (e - offset)) < 0.5 and abs(kx - (x - offset)) < 0.5 and kx > ke:
            out[lap] = kx - ke
    return out


def main() -> int:
    work = tempfile.mkdtemp(prefix="corner_typical_compare_")
    rows, cells = [], {}
    try:
        for name, noise, s, truth in _sessions(work):
            m = s.corner_matrix()
            for qp in s.quickest_passes().passes:
                if qp.lap_id is None:
                    continue
                cid, q = qp.cid, qp.lap_id
                col = m.cids.index(cid)
                counted = [int(lap) for lap, row, ok
                           in zip(m.lap_ids, m.cells, m.resolved, strict=True)
                           if row[col] is not None and ok[col]]
                tru = true_corner_times(s, truth, cid, q, counted)
                v = s.pass_vs_typical(q, cid)
                if v is None:
                    continue
                tg = (round(tru[v.typical_lap_id] - tru[q], 3)
                      if q in tru and v.typical_lap_id in tru else None)
                rows.append({"session": name, "noise": noise, "cid": cid, "lap": q + 1,
                             "typical_lap": v.typical_lap_id + 1, "gap": v.gap_s, "true_gap": tg,
                             "offered": not v.level and not v.is_typical, "step": v.level_s})
                print(json.dumps(rows[-1]), flush=True)
                c = cells.setdefault(noise, [0, 0, 0])
                for lap in counted:
                    w = s.pass_vs_typical(lap, cid)
                    if w is None or w.is_typical:
                        continue
                    c[0] += 1
                    if not w.level:
                        c[1] += 1
                        if lap in tru and w.typical_lap_id in tru and abs(
                                tru[w.typical_lap_id] - tru[lap]) < w.level_s:
                            c[2] += 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    summary = {}
    for noise in (1.0, 2.0, 4.5):
        mine = [r for r in rows if r["noise"] == noise]
        off = [r for r in mine if r["offered"]]
        summary[f"noise {noise:g}"] = {
            "corner_rows": len(mine), "offered": len(off),
            "offered_true_gap_under_step": sum(
                r["true_gap"] is not None and r["true_gap"] < r["step"] for r in off),
            "offered_true_negative": sum(
                r["true_gap"] is not None and r["true_gap"] < 0 for r in off),
            "all_cells": cells.get(noise, [0])[0],
            "all_cells_offered": cells.get(noise, [0, 0])[1],
            "all_cells_offered_true_under_step": cells.get(noise, [0, 0, 0])[2]}
    print(json.dumps(summary, default=int))   # the counts sum numpy booleans
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
