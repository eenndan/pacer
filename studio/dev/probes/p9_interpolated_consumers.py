"""P9 — the consumers that still count an interpolated corner cell, and what the rule costs them (C5).

#331 put the CORNERS table, the Corners page ★ and cells, the Best's provenance panel, the phase
split and STRAIGHTS on one rule (`CornerModel.lap_corner_resolved` / `lap_edge_resolved`): a time,
apex or grip figure needs BOTH corner edges matched on track, a speed only its own edge. Five
consumers were left counting every cell. This probe measures, on the real footage and through the
real accessors, what applying the same rule does to each of them — ON TOP OF #335, which recovered
most of 0060's unmatched boundaries, so every figure the backlog carries from #331 is re-measured
here rather than quoted:

  1. how many cells / edges are resolved at all, per recording;
  2. COACHING: the per-corner median loss, its rank and its evidence gate, counted both ways;
  3. the IDEAL LAP: which segment donors sit on an unresolved edge, and the total either way;
  4. BRAKING: the per-corner "m later" habit and its lap count, both ways;
  5. COASTING: the ranked places and the leader's separation, both ways;
  6. the per-lap CSV: how many of the exported corner cells are interpolated;
  7. T15: how many ranked coaching rows the brake hint's geometry gate suppresses.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p9_interpolated_consumers
    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p9_interpolated_consumers 0060

SAFETY, enforced rather than promised (as p4/p6/p8 enforce it):
  * EVERY FOOTAGE FOLDER IS READ-ONLY. Paths under ~/Desktop are only ever opened for reading, and
    every file in each folder is snapshotted (size, mtime) before and after each load.
    `D24/GX010060.MP4` is a 2.4 MB JSON stub that overwrote the owner's footage; it is never listed
    here and never opened.
  * `Session.load` UPSERTS INTO THE LIBRARY, so `studio.dev._jail` diverts every app-support seam
    BEFORE any studio import resolves one, and the real app-support directory is snapshotted before
    and after the whole run. This probe writes no file at all.
"""

from __future__ import annotations

import os
import sys
import time

import numpy as np

DESKTOP = os.path.expanduser("~/Desktop")
RECORDINGS = {
    "0060": ("D24", ["GX020060.MP4", "GX030060.MP4"]),
    "0062": ("D24", ["GX010062.MP4", "GX020062.MP4", "GX030062.MP4"]),
}
_STUB = "GX010060.MP4"
_REAL_APP_SUPPORT = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "pacer")


def _folder_state(path: str) -> dict:
    if not os.path.isdir(path):
        return {}
    return {n: (os.stat(os.path.join(path, n)).st_size, os.stat(os.path.join(path, n)).st_mtime_ns)
            for n in sorted(os.listdir(path)) if not n.startswith(".")}


def _seg_resolved(edges: list[bool]) -> np.ndarray:
    """The 2N+1 partition segments' resolution from one lap's 2N edge flags. Segment j is bounded
    by ref_edges[j] and ref_edges[j+1], where ref_edges = [0, *edges, total]; the two timing-line
    endpoints are the same physical point on every lap, so they are resolved by definition."""
    e = np.asarray(edges, bool)
    n2 = e.size
    lo = np.concatenate(([True], e))
    hi = np.concatenate((e, [True]))
    assert lo.size == hi.size == n2 + 1
    return lo & hi


def _median(x) -> float:
    x = np.asarray([v for v in x if np.isfinite(v)], float)
    return float(np.median(x)) if x.size else float("nan")


# ─── 1. what is resolved ─────────────────────────────────────────────────────────────────────────
def report_resolution(s, ids) -> tuple[np.ndarray, np.ndarray]:
    cells = np.array([s.corners.lap_corner_resolved(i) for i in ids], bool)
    edges = np.array([s.corners.lap_edge_resolved(i) for i in ids], bool)
    print(f"1. resolved cells {int(cells.sum())}/{cells.size} "
          f"({100 * cells.mean():.1f} %), edges {int(edges.sum())}/{edges.size} "
          f"({100 * edges.mean():.1f} %) over {len(ids)} clean laps")
    return cells, edges


# ─── 2. coaching ─────────────────────────────────────────────────────────────────────────────────
def report_coaching(s, ids, cells) -> None:
    from studio import coaching

    corner_list = s.corners.corner_list()
    n = len(corner_list)
    best = s.best_lap_id()
    rows, keep, lap_ids = [], [], []
    for k, i in enumerate(ids):
        st = s.corners.lap_corner_stats(i)
        if len(st) != n:
            continue
        rows.append([x.time for x in st])
        keep.append(cells[k])
        lap_ids.append(i)
    times = np.asarray(rows, float)
    mask = np.asarray(keep, bool)
    best_stats = s.corners.lap_corner_stats(best)
    best_times = np.asarray([x.time for x in best_stats], float)
    best_res = np.asarray(s.corners.lap_corner_resolved(best), bool)
    # The rule: a lap's cell counts where it is resolved AND the best lap's cell it is subtracted
    # from is (the subtrahend must be measured too — the same pairing phase_report applies).
    counted = mask & best_res[None, :]
    loss_all = np.median(times - best_times[None, :], axis=0)
    ruled = np.where(counted, times - best_times[None, :], np.nan)
    with np.errstate(invalid="ignore"):
        loss_rule = np.array([_median(ruled[:, j]) for j in range(n)])
    print(f"2. COACHING — the per-corner median loss, every cell vs the rule "
          f"(best lap {best} resolves {int(best_res.sum())}/{n})")
    print("   corner  n_all  n_ruled   loss_all  loss_rule     Δ   gate_all -> gate_rule")
    order_all = [j for j in np.argsort(-loss_all, kind="stable") if loss_all[j] > 1e-9]
    order_rule = [j for j in np.argsort(-np.nan_to_num(loss_rule, nan=-1e9), kind="stable")
                  if np.isfinite(loss_rule[j]) and loss_rule[j] > 1e-9]
    for j in range(n):
        ev_a = coaching.corner_evidence(times[:, j], float(best_times[j]), float(loss_all[j]))
        col = times[:, j][counted[:, j]]
        ev_r = (coaching.corner_evidence(col, float(best_times[j]), float(loss_rule[j]))
                if col.size and np.isfinite(loss_rule[j]) else None)
        ra = order_all.index(j) + 1 if j in order_all else 0
        rr = order_rule.index(j) + 1 if j in order_rule else 0
        gate_a = ev_a.abstain or "ranked"
        gate_r = "NO CELLS" if ev_r is None else (ev_r.abstain or "ranked")
        print(f"   C{corner_list[j].cid:<4}  {times.shape[0]:>4}  {int(counted[:, j].sum()):>6}   "
              f"{loss_all[j]:>+8.3f}  {loss_rule[j]:>+9.3f}  {loss_rule[j] - loss_all[j]:>+6.3f}   "
              f"#{ra} {gate_a} -> #{rr} {gate_r}")
    print(f"   ranked order all : {[f'C{corner_list[j].cid}' for j in order_all]}")
    print(f"   ranked order rule: {[f'C{corner_list[j].cid}' for j in order_rule]}")
    # The LINE signal's σ — the same quantity the CORNERS table prints, which has counted by the
    # rule since #331 while `Session.corner_consistency` (coaching's only reader) counts every cell.
    from studio import consistency

    sig_all = consistency.corner_spreads([c.cid for c in corner_list], [list(r) for r in times])
    rep = s.corner_report()
    print("   σ: corner_consistency (every cell) vs the CORNERS table (the rule)")
    for j, c in enumerate(corner_list):
        mine = next(sp.sigma for sp in sig_all if sp.cid == c.cid)
        theirs = next((r.sigma_s for r in rep if r.cid == c.cid), None)
        flag = "" if theirs is None or abs(mine - theirs) < 5e-4 else "   <-- disagree"
        print(f"   C{c.cid:<4} coaching σ {mine:.3f}  CORNERS σ "
              f"{'—' if theirs is None else f'{theirs:.3f}'}{flag}")


# ─── 3. the ideal lap ────────────────────────────────────────────────────────────────────────────
def report_ideal(s, edges_by_lap, ids) -> None:
    sb = s.corners.segment_bests()
    if sb is None:
        print("3. IDEAL LAP — no composite")
        return
    res = {i: _seg_resolved(e) for i, e in zip(ids, edges_by_lap, strict=True)}
    times = np.asarray(sb.times, float)
    admitted = np.asarray(sb.admitted, bool)
    seg_res = np.asarray([res[i] for i in sb.lap_ids], bool)
    bad = [j for j, d in enumerate(sb.donors)
           if d is not None and not res[d][j]]
    print(f"3. IDEAL LAP — {len(sb.bests)} segments, {len(sb.lap_ids)} donors considered; "
          f"{len(bad)} winning donors sit on an unresolved edge: "
          f"{[sb.labels[j] for j in bad]}")
    both = admitted & seg_res
    for j in range(times.shape[1]):
        if not both[:, j].any():
            both[:, j] = admitted[:, j]
    ruled = np.where(both, times, np.inf).min(axis=0)
    now = float(np.sum(sb.bests))
    then = float(np.sum(ruled))
    print(f"   ideal total {now:.3f} s -> {then:.3f} s ({then - now:+.3f} s); "
          f"per-segment worst change {np.max(ruled - np.asarray(sb.bests)):+.3f} s")
    moved = [(sb.labels[j], sb.bests[j], float(ruled[j])) for j in range(len(sb.bests))
             if abs(ruled[j] - sb.bests[j]) > 1e-9]
    for lbl, a, b in moved:
        print(f"   {lbl:<10} {a:.3f} -> {b:.3f} ({b - a:+.3f} s)")
    print(f"   segments where NO admitted donor is resolved: "
          f"{[sb.labels[j] for j in range(times.shape[1]) if not (admitted[:, j] & seg_res[:, j]).any()]}")
    # The PLAN rides on the same `admitted` mask (SegmentBests.beat_counts / decomposition): how
    # far the best lap's achievability counts move if resolution joins admission.
    best = s.best_lap_id()
    if best in sb.lap_ids:
        row = np.asarray(sb.times[sb.lap_ids.index(best)], float)
        moved = 0
        for j in range(times.shape[1]):
            a, b = admitted[:, j], both[:, j]
            if (int(((times[:, j] <= row[j]) & a).sum()), int(a.sum())) != \
               (int(((times[:, j] <= row[j]) & b).sum()), int(b.sum())):
                moved += 1
        rows_now = int(admitted[sb.lap_ids.index(best)].sum())
        rows_rule = int(both[sb.lap_ids.index(best)].sum())
        print(f"   plan: {moved}/{times.shape[1]} beat counts move; the best lap's decomposition "
              f"lists {rows_now} -> {rows_rule} segments")


# ─── 4. braking ──────────────────────────────────────────────────────────────────────────────────
def report_braking(s, ids, cells) -> None:
    corner_list = s.corners.corner_list()
    res_by_lap = {i: cells[k] for k, i in enumerate(ids)}
    per_cid_all: dict[int, list[float]] = {c.cid: [] for c in corner_list}
    per_cid_rule: dict[int, list[float]] = {c.cid: [] for c in corner_list}
    index = {c.cid: k for k, c in enumerate(corner_list)}
    for i in ids:
        for bp in s.driving.lap_brake_points(i):
            per_cid_all[bp.cid].append(float(bp.metres_later))
            if res_by_lap[i][index[bp.cid]]:
                per_cid_rule[bp.cid].append(float(bp.metres_later))
    print("4. BRAKING — the per-corner habit (median m later), every cell vs the rule")
    print("   corner  laps_all  laps_rule   m_all   m_rule      Δm")
    for c in corner_list:
        a, b = per_cid_all[c.cid], per_cid_rule[c.cid]
        ma, mb = _median(a), _median(b)
        print(f"   C{c.cid:<4}  {len(a):>8}  {len(b):>9}  {ma:>+6.1f}  {mb:>+6.1f}  "
              f"{mb - ma:>+6.1f}")


# ─── 5. coasting ─────────────────────────────────────────────────────────────────────────────────
def report_coasting(s, ids, edges_by_lap) -> None:
    from studio import stats as stats_service

    rows = s._coast_rows()
    if rows is None:
        print("5. COASTING — no report")
        return
    cids, matrix = rows
    res = np.asarray([_seg_resolved(e) for e in edges_by_lap], bool)
    m = np.asarray(matrix, float)
    if res.shape != m.shape:
        print(f"5. COASTING — shape mismatch {res.shape} vs {m.shape}; skipped")
        return
    now = stats_service.coast_report(cids, [r for r in m])
    lead = now.places[0]
    print(f"5. COASTING all cells: leader {lead.label} {lead.s_per_lap:.3f} s/lap, "
          f"separates={now.lead_separable}, "
          f"top5={[(p.label, round(p.s_per_lap, 3)) for p in now.places[:5]]}")
    # `coast_report` means over the column, so the rule is a NaN mask + a nanmean. Ranking only —
    # what the rule would do to the order and to each place's seconds.
    masked = np.where(res, m, np.nan)
    with np.errstate(invalid="ignore"):
        mean_rule = np.nanmean(masked, axis=0)
    mean_all = m.mean(axis=0)
    idx = {p.index: p.label for p in now.places}
    order_rule = [int(j) for j in np.argsort(-np.nan_to_num(mean_rule, nan=-1.0), kind="stable")
                  if np.isfinite(mean_rule[j]) and mean_rule[j] > 0.0]
    print(f"   rule      : leader {idx.get(order_rule[0], order_rule[0])} "
          f"{mean_rule[order_rule[0]]:.3f} s/lap, "
          f"top5={[(idx.get(j, j), round(float(mean_rule[j]), 3)) for j in order_rule[:5]]}")
    worst = int(np.nanargmax(np.abs(mean_rule - mean_all)))
    print(f"   per-lap total {float(mean_all.sum()):.3f} s -> {float(np.nansum(mean_rule)):.3f} s; "
          f"biggest per-place move {idx.get(worst, worst)} "
          f"{mean_all[worst]:.3f} -> {float(mean_rule[worst]):.3f} s "
          f"({float(mean_rule[worst]) - mean_all[worst]:+.3f}); "
          f"cells dropped {int((~res).sum())}/{res.size}")
    full = int(res.all(axis=1).sum())
    print(f"   laps whose whole partition is resolved: {full}/{res.shape[0]} "
          f"(the sum-preserving alternative to a per-cell mask)")
    # Is an unresolved cell BIASED, or only noisier? Per place, the mean over each kind.
    worst_bias, worst_j = 0.0, -1
    for j in range(m.shape[1]):
        ok, bad = m[res[:, j], j], m[~res[:, j], j]
        if bad.size >= 3 and ok.size >= 3 and abs(ok.mean() - bad.mean()) > worst_bias:
            worst_bias, worst_j = abs(float(ok.mean() - bad.mean())), j
    if worst_j >= 0:
        print(f"   biggest resolved-vs-interpolated mean gap {idx.get(worst_j, worst_j)} "
              f"{worst_bias:.3f} s (over >=3 cells of each kind)")


# ─── 6. the CSV export ───────────────────────────────────────────────────────────────────────────
def report_export(s) -> None:
    ids = [r["idx"] for r in s.lap_rows()]
    n = len(s.corners.corner_list())
    got = 0
    unresolved = 0
    for i in ids:
        st = s.corners.lap_corner_stats(i)
        if len(st) != n:
            continue
        res = s.corners.lap_corner_resolved(i)
        got += n
        unresolved += n - int(np.count_nonzero(res))
    print(f"6. CSV — {len(ids)} exported laps x {n} corners; {got} corner cells written, "
          f"{unresolved} of them interpolated ({100 * unresolved / max(got, 1):.1f} %)")


# ─── 7. T15: the brake hint's geometry gate ──────────────────────────────────────────────────────
def report_hint_gate(s) -> None:
    from studio import coaching, coaching_panel

    opps = s.coaching_opportunities()
    habits = s.coaching_brake_points()
    ranked = [r for r in opps.rows if r.evidence.ranked]
    shown, suppressed, no_habit = [], [], []
    for r in ranked:
        bp = habits.get(r.cid)
        if bp is None or int(bp.n_laps) < coaching.MIN_BRAKE_LAPS:
            no_habit.append(r.cid)
            continue
        if abs(float(bp.metres_later)) < coaching_panel.BRAKE_HINT_MIN_M:
            no_habit.append(r.cid)
            continue
        past = float(bp.optimal_brake_dist) - float(r.entry_dist)
        (suppressed if past > coaching_panel.BRAKE_HINT_MAX_PAST_TURN_IN_M else shown).append(
            (r.cid, past))
    print(f"7. T15 — {len(ranked)} ranked rows; the L5-10 geometry gate suppresses "
          f"{len(suppressed)} of them {[f'C{c}' for c, _ in suppressed]}, "
          f"{len(shown)} keep the hint, {len(no_habit)} have no hint to gate "
          f"{[f'C{c}' for c in no_habit]}")
    for cid, past in sorted(suppressed + shown):
        print(f"   C{cid:<4} optimum {past:+.1f} m past the turn-in "
              f"(gate {coaching_panel.BRAKE_HINT_MAX_PAST_TURN_IN_M:.0f} m)")


def probe_recording(name: str) -> None:
    from studio.session import Session

    folder, files = RECORDINGS[name]
    root = os.path.join(DESKTOP, folder)
    assert _STUB not in files, "the destroyed stub is never opened"
    paths = [os.path.join(root, f) for f in files]
    if not all(os.path.isfile(p) for p in paths):
        print(f"\n{name}: footage missing under {root} — skipped")
        return
    print(f"\n{'=' * 78}\n{name}: {folder}/{', '.join(files)}\n{'=' * 78}")
    before = _folder_state(root)
    t0 = time.time()
    s = Session.load(paths)
    if _folder_state(root) != before:
        raise SystemExit(f"REFUSING TO CONTINUE: a file in {root} changed during the load.")
    ids = s.consistency_lap_ids()
    print(f"loaded in {time.time() - t0:.0f} s — {s.lap_count()} laps, {len(ids)} clean, "
          f"{len(s.corners.corner_list())} corners, best lap {s.best_lap_id()}")
    cells, edges = report_resolution(s, ids)
    report_coaching(s, ids, cells)
    report_ideal(s, [list(e) for e in edges], ids)
    report_braking(s, ids, cells)
    report_coasting(s, ids, [list(e) for e in edges])
    report_export(s)
    report_hint_gate(s)


def main() -> None:
    from studio.dev import _jail

    jail = _jail.divert_app_support("pacer-p9-")
    print(f"app-support seams diverted to {jail.dir}")
    real_before = _folder_state(_REAL_APP_SUPPORT)
    for key in sys.argv[1:] or list(RECORDINGS):
        probe_recording(key)
    if _folder_state(_REAL_APP_SUPPORT) != real_before:
        raise SystemExit("REFUSING TO PASS: the real app-support directory changed during the run.")
    print("\nthe real app-support directory is unchanged")


if __name__ == "__main__":
    main()
