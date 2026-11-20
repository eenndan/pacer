"""sector_typical_compare: would "Compare with my typical pass" ever be offered on a SECTOR row of
Corners ▸ Quickest passes, measured on the demo and synthetic truth sessions.

WHY. A corner row offers the compare (#626) only where the printed typical pass minus the printed
quickest pass beats the timing step (`stats.is_level`): a level pair has no difference to look
for. A sector's step is `stats.SECTOR_LEVEL_S` (0.30 s, 2·√2·σ_split: a split is timed at two
sector lines). This asks, per sector: the quickest split, the sector's typical pass (Coaching's
rule, `stats.typical_pass_lap` with the quickest lap excluded), their printed gap, and whether it
reaches the step — i.e. whether the item would ever be enabled. It also counts, over EVERY counted
lap × sector cell, how many passes are not level with their typical one (the SPLITS grid's own
menu), and the TRUE typical − quickest gap from the synthetic truth, to see whether a gap the app
measures past the step is real.

THE GRID. `sector_quickest`'s: the demo (published noise 1, and re-generated at GPS noise 2 and
4.5) and `synth_gopro` seeds 1 and 2 at noise 2 and 4.5; 2, 3 and 4 sector lines at the middle of
`Circuit.straights`, placed through `apply_timing_lines_latlon` as a reload places them.

ARGV CONTRACT: none. Every recording is generated into ONE `mkdtemp` under $TMPDIR and removed
before exit; the app-support store is jailed; the sidecar write is stubbed. Output is stdout, its
last line the JSON summary.

    pixi run python -m studio.dev.probes.sector_typical_compare
"""
from __future__ import annotations

import json
import shutil
import tempfile

import numpy as np

from studio import stats as st
from studio.dev.probes.clip_refusals import _true_splits
from studio.dev.probes.sector_quickest import CONFIGS, _sessions


def per_sector(s, truth, n: int) -> tuple[list[dict], tuple[int, int]]:
    lines = [truth.line_at(sum(truth.circuit.straights[j]) / 2.0) for j in CONFIGS[n]]
    start, _ = s.timing_lines_latlon()
    assert s.apply_timing_lines_latlon(start, lines, confirmed=True), "sector lines refused"
    m, true = s.split_matrix(), _true_splits(s, truth, lines)
    step = st.sector_level_floor(bool(s.timing_quality.media_clock))
    out, cells, offered = [], 0, 0
    for c in range(m.columns):
        counted = {lid: row[c] for lid, row in zip(m.lap_ids, m.cells, strict=True)
                   if row[c] is not None}
        quick = min(counted, key=counted.get)
        typ = st.typical_pass_lap(list(counted), list(counted.values()), m.medians[c],
                                  exclude=quick)
        gap = st.printed_gap_s(counted[typ], counted[quick])
        true_gap = (round(float(true[typ][c] - true[quick][c]), 3)
                    if typ in true and quick in true else None)
        out.append({"S": c + 1, "laps": len(counted), "quickest": round(counted[quick], 3),
                    "typical": round(counted[typ], 3), "gap": gap, "true_gap": true_gap,
                    "offered": not st.is_level(gap, step)})
        # Every counted pass against its own typical pass (the SPLITS grid's menu on any cell).
        for lap in counted:
            other = st.typical_pass_lap(list(counted), list(counted.values()), m.medians[c],
                                        exclude=lap)
            if other is None:
                continue
            cells += 1
            offered += not st.is_level(st.printed_gap_s(counted[other], counted[lap]), step)
    return out, (cells, offered)


def main() -> int:
    work = tempfile.mkdtemp(prefix="sector_typical_compare_")
    rows, grid = [], {}
    try:
        for name, noise, s, truth in _sessions(work):
            for n in CONFIGS:
                recs, (cells, offered) = per_sector(s, truth, n)
                g = grid.setdefault(noise, [0, 0])
                g[0] += cells
                g[1] += offered
                for rec in recs:
                    rows.append({"session": name, "noise": noise, "lines": n, **rec})
                    print(json.dumps(rows[-1]), flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    summary = {}
    for noise in (1.0, 2.0, 4.5):
        mine = [r for r in rows if r["noise"] == noise]
        tg = [r["true_gap"] for r in mine if r["true_gap"] is not None]
        summary[f"noise {noise:g}"] = {
            "sectors": len(mine),
            "quickest_rows_offered": sum(r["offered"] for r in mine),
            "largest_gap": max(r["gap"] for r in mine),
            "median_gap": round(float(np.median([r["gap"] for r in mine])), 3),
            "largest_true_gap": max(tg) if tg else None,
            "true_gap_at_step": sum(g >= st.SECTOR_LEVEL_S for g in tg),
            "offered_with_true_gap_under_step": sum(
                r["offered"] and r["true_gap"] is not None and r["true_gap"] < st.SECTOR_LEVEL_S
                for r in mine),
            "all_cells": grid[noise][0], "all_cells_offered": grid[noise][1]}
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
