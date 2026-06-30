"""F10 real-data verification (throwaway dev script): the coaching summary on a real recording.

Run:  python -m studio.dev._verify_coaching /path/to/GX010060.MP4

Prints the top-3 opportunities with their reasons + numbers, the per-corner median-loss table,
the manual delta-chart cross-check of #1, the time-loss-vs-consistency ranking comparison, the
determinism assertion, and the jump-to seek-target assertion. Not part of the app; not registered.
"""
import os
import sys

import numpy as np

from studio import coaching
from studio.session import Session


def main(path: str) -> int:
    print(f"loading {path} …", flush=True)
    s = Session.load([path])
    valid = s.valid_lap_ids()
    clean = s.consistency_lap_ids()
    best = s.best_lap_id()
    corners = s.corners.corner_list()
    print(f"\ntrack={s.track_name}  valid_laps={len(valid)}  clean_laps={len(clean)}  "
          f"best_lap={best} ({s.lap_time(best):.3f}s)  corners={len(corners)}")

    opp = s.coaching_opportunities()
    print(f"\nenough={opp.enough}  n_laps={opp.n_laps}  median(typical)_lap={opp.median_lap_id}"
          + (f" ({s.lap_time(opp.median_lap_id):.3f}s)" if opp.median_lap_id is not None else ""))

    # --- the full per-corner median-loss table (every corner, ranked) -------------------
    print("\n=== per-corner median time lost vs best (over the clean laps), ranked ===")
    # Recompute the raw losses for transparency (same as the model).
    best_stats = s.corners.lap_corner_stats(best)
    best_ct = {st.cid: st.time for st in best_stats}
    losses = {}
    for c in corners:
        per = []
        for i in clean:
            st = s.corners.lap_corner_stats(i)
            if len(st) == len(corners):
                per.append(st[c.cid - 1].time - best_ct[c.cid])
        if per:
            losses[c.cid] = float(np.median(per))
    for cid in sorted(losses, key=lambda k: -losses[k]):
        print(f"  C{cid:<2d} median_loss={losses[cid]:+.3f}s")

    # --- the top-3 with reasons + numbers -----------------------------------------------
    print("\n=== TOP-3 OPPORTUNITIES (with the dominant measured reason + numbers) ===")
    for rank, o in enumerate(opp.rows[:3], 1):
        r = o.reason
        print(f"  #{rank}  C{o.cid} (dir {'L' if o.direction > 0 else 'R'})  "
              f"time_lost={o.time_lost:+.3f}s  entry_dist={o.entry_dist:.1f}m")
        print(f"        reason={r.kind}  contribution={r.contribution:.3f}s  "
              f"=> {coaching.reason_sentence(o)}")
        print(f"        [apex_deficit={r.apex_speed_deficit:.2f}km/h  "
              f"brake_extra={r.brake_extra_s:.3f}s  coast_extra={r.coast_extra_s:.3f}s  "
              f"sigma={r.sigma:.3f}s]")

    if not opp.rows:
        print("  (no opportunities — excluded or no corner loses time)")
        return 0

    # --- manual delta-chart cross-check of #1 -------------------------------------------
    # The #1 corner should be where the TYPICAL lap visibly bleeds time vs best ON THE DELTA
    # CHART. Subtlety: the model ranks by the MEDIAN-ACROSS-LAPS per-corner loss (robust), which
    # is NOT the same as any single lap's per-corner bleed — the median lap can be an outlier in a
    # given corner. So the apples-to-apples chart cross-check is the MEDIAN over the clean laps of
    # each lap's delta-chart bleed across the corner window (Δ at exit − Δ at entry on that lap's
    # delta() curve). That median bleed must equal the model's median loss and peak at the #1
    # corner.
    top = opp.rows[0]
    print(f"\n=== manual delta-chart cross-check of #1 (C{top.cid}): ===")
    # per clean lap, the delta-chart bleed across every corner window
    bleed_by_corner = {c.cid: [] for c in corners}
    for lid in clean:
        res = s.delta([lid])
        if res is None or lid not in res[2]:
            continue
        x, dy = res[2][lid]  # x = s*best_total_distance (m), dy = Δ-to-best seconds
        for c in corners:
            lo = float(np.interp(c.enter, x, dy))
            hi = float(np.interp(c.exit, x, dy))
            bleed_by_corner[c.cid].append(hi - lo)
    med_bleed = {cid: float(np.median(v)) for cid, v in bleed_by_corner.items() if v}
    worst = max(med_bleed, key=lambda k: med_bleed[k])
    print(f"  median delta-chart bleed across each corner window (over the {len(clean)} clean laps):")
    for cid in sorted(med_bleed, key=lambda k: -med_bleed[k])[:4]:
        print(f"    C{cid:<2d} median chart-bleed = {med_bleed[cid]:+.3f}s")
    print(f"  => the corner with the LARGEST median chart-bleed is C{worst} "
          f"({med_bleed[worst]:+.3f}s); #1 ranked corner is C{top.cid}  "
          f"=> {'MATCH' if worst == top.cid else 'DIFFER'}")
    print(f"  (model #1 median loss {top.time_lost:+.3f}s vs its median chart-bleed "
          f"{med_bleed.get(top.cid, float('nan')):+.3f}s — same statistic, must agree)")

    # --- time-loss ranking vs the F6 consistency (σ) ranking ----------------------------
    print("\n=== time-LOSS ranking vs F6 consistency (σ × loss) ranking ===")
    loss_order = [o.cid for o in opp.rows]
    cons = s.corner_consistency()
    cons_order = [sp.cid for sp in cons]
    print(f"  time-loss order : {loss_order[:5]}")
    print(f"  consistency order: {cons_order[:5]}")
    by_cid = {sp.cid: sp for sp in cons}
    for cid in loss_order[:3]:
        sp = by_cid.get(cid)
        if sp is not None:
            print(f"    C{cid}: median_loss={sp.median_loss:+.3f}s  sigma={sp.sigma:.3f}s  "
                  f"score(σ×loss)={sp.score:.4f}")

    # --- determinism --------------------------------------------------------------------
    a = s.coaching_opportunities()
    b = s.coaching_opportunities()
    assert a == b, "DETERMINISM FAILED: two calls differ"
    print("\n=== determinism: two coaching_opportunities() calls are IDENTICAL — OK ===")

    # --- jump-to: #1's seek target == best lap's corner-entry media time ----------------
    target = s.corners.corner_entry_media_time(best, top.cid)
    win = s.lap_window(best)
    # the corner's window on the best lap (media times at enter/exit)
    t0, _xs, _ys, _v, cum = s._lap_columns(best)
    c = next(cc for cc in corners if cc.cid == top.cid)
    t_enter = float(np.interp(c.enter, cum, t0))
    t_exit = float(np.interp(c.exit, cum, t0))
    assert target is not None and abs(target - t_enter) < 1e-6, (target, t_enter)
    assert win[0] <= target <= win[1], "seek target outside the best lap's window"
    assert t_enter <= target <= t_exit + 1e-9, "seek target not at the corner entry"
    print(f"\n=== jump-to #1 (C{top.cid}): seek target = {target:.3f}s == best-lap corner-entry "
          f"media time (window [{t_enter:.3f}, {t_exit:.3f}]s) — OK ===")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else
                  os.path.expanduser("~/Desktop/D24/GX010060.MP4")))
