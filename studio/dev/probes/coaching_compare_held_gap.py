"""coaching_compare_held_gap: a Coaching row's Compare — the corner's typical pass (pane A) beside
the best lap's (pane B) — and how much of the gap it holds after the exit is real.

WHY. Since #643 the held gap of "Compare with my typical pass" is neutral: that compare is offered
on the QUICKEST of many noisy passes, partly a lucky one (corner_typical_compare). A Coaching row's
Compare still coloured its held gap past the timing step. Its pane B is the BEST LAP's pass — a lap
picked on its whole-lap time, not on this corner — and it is offered only on a ranked row
(`coaching.corner_evidence`: enough laps, another lap matching the best lap's time through the
corner, a loss past the corner's own scatter), and the rows are ranked BY that loss. Does that rule
keep the selection effect out? Per offered Compare: the held gap as printed
(`stats.printed_gap_s`), whether the seam would colour it (past `library.PB_PRECISION_S`,
`PassAnchor.reads_level`), and the TRUE gap, both passes timed between fixed truth positions of
the best lap's app entry and exit (`corner_typical_compare.true_corner_times`).

THE GRID. `sector_quickest`'s: the demo (published noise 1, and re-generated at GPS noise 2 and
4.5) and `synth_gopro` seeds 1 and 2 at noise 2 and 4.5 (no planted habit). With the environment
variable COACH_EXTRA_SEEDS="3-8", those generator seeds alone at noise 2 and 4.5 instead.

MEASURED (2026-11-20, PR "Coaching compare: the held gap is neutral"). Offered Compares, those
whose held gap is past the step (coloured before), and how many of those are TRULY under it:
the demo (its planted habit) 1 + 1 + 1 of 4 + 4 + 4 at noise 1, 2, 4.5, none under; seeds 1-8 with
no habit 12 of 36, 8 truly under the step (2 of 5 at noise 2, 6 of 7 at 4.5) and 1 of the wrong
sign (seed 5, noise 4.5: +0.07 printed, −0.005 true). The typical-pass compare's corners: 13 of 17
on seeds 1-2. Comparable, so the held gap is neutral here too.

ARGV CONTRACT: none. Every recording is generated into ONE `mkdtemp` under $TMPDIR and removed
before exit; the app-support store is jailed and the sidecar write stubbed (on importing
`sector_quickest`). Output is stdout, its last line the JSON summary.

    pixi run python -m studio.dev.probes.coaching_compare_held_gap
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile

from studio.dev.probes.corner_typical_compare import true_corner_times
from studio.dev.probes.sector_quickest import _sessions  # jails + stubs the sidecar on import


def main() -> int:
    from studio import coaching
    from studio.dev import synth_gopro as sg
    from studio.dev.probes.sector_quickest import _load
    from studio.library import PB_PRECISION_S
    from studio.stats import printed_gap_s

    def extra(work):
        # COACH_EXTRA_SEEDS="3-8": more habit-free generator seeds at noise 2 and 4.5, alone
        lo, hi = map(int, os.environ["COACH_EXTRA_SEEDS"].split("-"))
        for noise in (2.0, 4.5):
            for seed in range(lo, hi + 1):
                rec = sg.generate(os.path.join(work, f"s{seed}_{noise:g}"), seed=seed,
                                  gps_noise=noise)
                yield f"seed{seed}", noise, _load(rec.paths[0]), rec.truth

    work = tempfile.mkdtemp(prefix="coaching_compare_held_gap_")
    rows = []
    try:
        source = extra(work) if os.environ.get("COACH_EXTRA_SEEDS") else _sessions(work)
        for name, noise, s, truth in source:
            typical = s.typical_passes()
            for opp in coaching.shown_rows(s.coaching_opportunities()):
                tp = typical.get(opp.cid) if opp.evidence.ranked else None
                if tp is None:
                    continue          # the row's Compare is greyed: nothing offered
                held = printed_gap_s(tp.pass_s, tp.best_pass_s)   # + = the typical pass slower
                tru = true_corner_times(s, truth, opp.cid, tp.best_lap_id,
                                        [tp.lap_id, tp.best_lap_id])
                tg = (round(tru[tp.lap_id] - tru[tp.best_lap_id], 3)
                      if len(tru) == 2 else None)
                rows.append({"session": name, "noise": noise, "cid": opp.cid,
                             "typical_lap": tp.lap_id + 1, "best_lap": tp.best_lap_id + 1,
                             "n": tp.n, "lost": round(opp.time_lost, 3), "held": held,
                             "coloured": bool(abs(held) >= PB_PRECISION_S), "true_gap": tg})
                print(json.dumps(rows[-1]), flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    def counts(mine):
        col = [r for r in mine if r["coloured"]]
        known = [r for r in col if r["true_gap"] is not None]
        return {"offered": len(mine), "coloured": len(col), "true_unknown": len(col) - len(known),
                "coloured_true_under_step": sum(abs(r["true_gap"]) < PB_PRECISION_S
                                                for r in known),
                "coloured_wrong_sign": sum((r["true_gap"] > 0) != (r["held"] > 0) for r in known),
                "true_gap_min": min((r["true_gap"] for r in known), default=None)}
    summary = {f"demo noise {n:g}": counts([r for r in rows if r["session"] == "demo"
                                            and r["noise"] == n]) for n in (1.0, 2.0, 4.5)}
    summary["seeds noise 2 and 4.5"] = counts([r for r in rows if r["session"] != "demo"])
    print(json.dumps(summary, default=int))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
