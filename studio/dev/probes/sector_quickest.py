"""sector_quickest: which level step a sector's quickest split is named under (Corners ▸ Quickest
passes' sector rows, `stats.SECTOR_LEVEL_S`), measured against synthetic truth.

WHY. A corner's quickest pass is named with every pass level with it under the 0.07 s timing step
(refused-2026-09 §32). A split is timed at two sector lines and `docs/ACCURACY.md` publishes its σ,
0.11 s at recording C's GPS noise: larger than that step. This asks what each candidate step does:
per sector, the quickest split, its lap, how many laps are level with it, whether it stands alone
(a ★), and whether a lone quickest is the TRUE quickest lap and by how much.

THE GRID. The demo (its published noise 1, and its telemetry re-generated at GPS noise 2 and 4.5)
and `synth_gopro` seeds 1 and 2 at noise 2 and 4.5; on each, 2, 3 and 4 sector lines at the middle
of `Circuit.straights` CONFIGS, placed through `apply_timing_lines_latlon` as a reload places them.
Truth splits are matched to app laps by start time, as the truth matrix matches them.

ARGV CONTRACT: none. Every recording is generated into ONE `mkdtemp` under $TMPDIR and removed
before exit; the app-support store is jailed; the sidecar write is stubbed. Output is stdout, its
last line the JSON summary.

    pixi run python -m studio.dev.probes.sector_quickest
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")

from studio.dev._jail import divert_app_support  # noqa: E402

_JAIL = divert_app_support("sector_quickest_")

from studio import sidecar  # noqa: E402  (after the jail, as every load must be)

sidecar.save = lambda *a, **k: None   # a line edit writes beside the recording: never here
from studio import chapters  # noqa: E402
from studio import stats as st  # noqa: E402
from studio.dev import make_demo  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.dev.probes.clip_refusals import _true_splits  # noqa: E402
from studio.session import Session  # noqa: E402

CONFIGS = {2: (2, 5), 3: (1, 3, 5), 4: (1, 2, 4, 5)}
STEPS = (0.07, 0.11, st.SECTOR_LEVEL_S, 0.30)   # the lap step, σ, √2σ (the app's), 2√2σ (§26)
NOISES = (None, 2.0, 4.5)                       # None: the demo as published (noise 1)


def _load(path: str) -> Session:
    return Session.load(chapters.discover_siblings(path))


def _sessions(work: str):
    for noise in NOISES:
        if noise is None:
            truth = make_demo.make(os.path.join(work, "demo.mp4"), render=False)
            yield "demo", 1.0, _load(os.path.join(work, "demo.mp4")), truth
            continue
        rec = sg.generate(os.path.join(work, f"demo_{noise:g}"), make_demo.DEMO_SEED,
                          make_demo.DEMO_LAPS, chapters=1, origin=make_demo.DEMO_ORIGIN,
                          habit=make_demo.DEMO_HABIT, gps_noise=noise)
        yield "demo", noise, _load(rec.paths[0]), rec.truth
        for seed in (1, 2):
            rec = sg.generate(os.path.join(work, f"s{seed}_{noise:g}"), seed=seed, gps_noise=noise)
            yield f"seed{seed}", noise, _load(rec.paths[0]), rec.truth


def per_sector(s: Session, truth, n: int) -> list[dict]:
    lines = [truth.line_at(sum(truth.circuit.straights[j]) / 2.0) for j in CONFIGS[n]]
    start, _ = s.timing_lines_latlon()
    assert s.apply_timing_lines_latlon(start, lines, confirmed=True), "sector lines refused"
    qs, true = s.quickest_sectors(), _true_splits(s, truth, lines)
    out = []
    for c, qp in enumerate(qs.passes):
        counted = sorted((t, lap) for lap, t in qp.marks)
        gaps = [(st._ms(t) - st._ms(qp.time_s)) / 1000 for t, _ in counted[1:]]
        tl = {lap: true[lap][c] for _, lap in counted if lap in true}
        ranked = sorted(tl, key=tl.get)
        rec = {"S": c + 1, "laps": qp.n_counted, "quickest": round(qp.time_s, 3),
               "lap": qp.lap_id + 1, "next_gap": qp.next_gap_s, "true_lap": ranked[0] + 1,
               "true_margin": round(tl[ranked[1]] - tl[ranked[0]], 3)}
        for step in STEPS:
            level = sum(1 for g in gaps if st.is_level(g, step))
            rec[f"level@{step:g}"] = level
            rec[f"star@{step:g}"] = level == 0
            rec[f"star_wrong@{step:g}"] = level == 0 and qp.lap_id != ranked[0]
        out.append(rec)
    return out


def main() -> int:
    work = tempfile.mkdtemp(prefix="sector_quickest_")
    rows = []
    try:
        for name, noise, s, truth in _sessions(work):
            for n in CONFIGS:
                for rec in per_sector(s, truth, n):
                    rows.append({"session": name, "noise": noise, "lines": n, **rec})
                    print(json.dumps(rows[-1]), flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    summary = {}
    for noise in (1.0, 2.0, 4.5):
        mine = [r for r in rows if r["noise"] == noise]
        summary[f"noise {noise:g}"] = {
            "sectors": len(mine), "largest_true_margin": max(r["true_margin"] for r in mine),
            "quickest_is_true_quickest": sum(r["lap"] == r["true_lap"] for r in mine),
            **{f"stars@{step:g}": [sum(r[f"star@{step:g}"] for r in mine),
                                   sum(r[f"star_wrong@{step:g}"] for r in mine)] for step in STEPS}}
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
