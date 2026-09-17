"""P8 — does de-drifting each lap, and anchoring on the session's consensus line, recover the
corner boundaries D24 0060 loses to the 3 m distance gate? (M7)

M6 (`studio/docs/corner-match-0060-2026-09.md`) measured the CAUSE: every failed interior boundary
on 0060 fails the distance gate, and two things compound — a rigid whole-lap receiver shift (median
1.34 m, up to 3.65) and a reference lap that is itself one of the most displaced in the session
(rank 34/38 from the consensus). This probe measures the FIX, half at a time, through the real
`corners._spatial_matches` and the real `corners.session_geometry` the product now uses:

  1. the fitted geometry itself — |T| per lap, what the fit leaves behind, how it persists;
  2. THE CONTROL that decides whether this may ship at all: does the rigid fit absorb a racing
     line? Planted translations and planted wide lines on a REAL lap, recovered separately, plus
     the whole measurement re-run on 0062 where the match is already ~100 % and the line varies;
  3. interior-boundary match rate for each half and for both, against the shipped gate;
  4. how far a boundary that ALREADY matched moves, and what that does to its corner time.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p8_corner_anchor
    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p8_corner_anchor 0060

TWO SAFETY PROPERTIES, both enforced rather than promised (as `p4` and `p6` enforce them):
  * `~/Desktop/D24` IS READ-ONLY. No path under it is ever passed as an output argument, and every
    chapter's (size, mtime) is snapshotted before and after each load and compared. This probe
    writes no file at all.
  * `Session.load` UPSERTS INTO THE USER'S LIBRARY, so `studio.dev._jail` diverts every app-support
    seam to a throwaway dir BEFORE any studio import resolves one.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import numpy as np

# ~/Desktop/D24 is READ-ONLY: opened for reading only, never as an output argument. GX010060.MP4 is
# deliberately absent — it is a 2.4 MB JSON stub that overwrote 11.9 GB of the owner's footage.
D24 = os.path.expanduser("~/Desktop/D24")
RECORDINGS = {
    "0060": ["GX020060.MP4", "GX030060.MP4"],
    "0062": ["GX010062.MP4", "GX020062.MP4", "GX030062.MP4"],
}
PLANTED_SHIFTS_M = ((1.5, 0.0), (0.0, -2.5), (2.0, 2.0))
PLANTED_LINE_M = (0.5, 1.5, 3.0)


def _d24_state(paths):
    """(size, mtime_ns) per chapter — the read-only tripwire, taken before and after the load."""
    return {p: (os.path.getsize(p), os.stat(p).st_mtime_ns) for p in paths}


@dataclass
class Rec:
    key: str
    trace: dict            # lap id -> (xs, ys, cum)
    td: dict               # lap id -> (dist, elapsed)
    clean: list[int]
    best: int
    frame: np.ndarray
    total_ref: float
    geometry: object
    session: object

    def interior(self) -> np.ndarray:
        return (self.frame > 1e-9) & (self.frame < self.total_ref - 1e-9)

    def resolved(self) -> tuple[np.ndarray, np.ndarray]:
        """The app's own per-cell and per-edge trust flags over the clean laps (#331's rule)."""
        cells = np.array([self.session.corners.lap_corner_resolved(i) for i in self.clean], bool)
        edges = np.array([self.session.corners.lap_edge_resolved(i) for i in self.clean], bool)
        return cells, edges


def load(key: str) -> Rec:
    from studio import corners
    from studio.session import Session

    paths = [os.path.join(D24, n) for n in RECORDINGS[key]]
    before = _d24_state(paths)
    s = Session.load(paths)
    after = _d24_state(paths)
    if after != before:
        moved = [os.path.basename(p) for p in paths if after[p] != before[p]]
        raise SystemExit(f"REFUSING TO CONTINUE: a D24 chapter changed during the load "
                         f"({', '.join(moved)}). That directory is read-only.")
    clean = s.consistency_lap_ids()
    corner_list, total_ref = s.corners.basis()
    frame = np.asarray([b for c in corner_list for b in (c.enter, c.exit)], float)
    trace, td = {}, {}
    for lid in clean:
        _t, x, y, _v, cum = s._lap_columns(lid)
        trace[lid] = (np.asarray(x, float), np.asarray(y, float), np.asarray(cum, float))
        dist, _speed, elapsed = s._lap_arrays(lid)
        td[lid] = (np.asarray(dist, float), np.asarray(elapsed, float))
    best = int(s.best_lap_id())
    geometry = corners.session_geometry(trace[best], trace)
    return Rec(key, trace, td, clean, best, frame, float(total_ref), geometry, s)


def _match(rec: Rec, lid: int, *, ref=None, shift=(0.0, 0.0), anchor=None) -> np.ndarray:
    """The real match of every corner boundary onto lap `lid`. `ref` other than the app's own
    reference carries the partition onto that lap's odometer by fraction (M6's counterfactual)."""
    from studio import corners

    ref = rec.best if ref is None else ref
    rx, ry, rc = rec.trace[ref]
    lx, ly, lc = rec.trace[lid]
    frame = rec.frame if ref == rec.best else rec.frame * float(rc[-1]) / rec.total_ref
    return corners._spatial_matches(frame, float(rc[-1]), rx, ry, rc, lx, ly, lc,
                                    lap_shift=shift, anchor_offset=anchor)


def _anchor(rec: Rec):
    from studio import corners

    return corners.anchor_offsets(rec.frame, rec.geometry, rec.best, *rec.trace[rec.best])


# --------------------------------------------------------------- the translation-invariant witness
KAPPA_WIN_M = 30.0      # half-window of curvature profile compared around a boundary
KAPPA_STEP_M = 0.25     # sampling step inside that window
KAPPA_SEARCH_M = 12.0   # widest longitudinal disagreement this witness can report


def _kappa_shift(rec: Rec, lid: int, positions: np.ndarray) -> np.ndarray:
    """For each boundary, the longitudinal shift (m) that best lines lap `lid`'s CURVATURE profile
    up with the reference lap's, minus the shift the projection actually used — i.e. how far the
    projected boundary sits from where the track's own shape says it is. NaN where the projection
    has no value.

    WHY CURVATURE. It is computed from the trace's HEADING, and a rigid translation does not change
    a heading: this witness is blind to exactly the receiver error the fix removes, so it can say
    whether the fix moved boundaries towards the track or away from it without being moved by the
    same bias itself."""
    from studio import corners

    rx, ry, rc = rec.trace[rec.best]
    lx, ly, lc = rec.trace[lid]
    k_ref = corners.lap_curvature(rx, ry, rc)
    k_lap = corners.lap_curvature(lx, ly, lc)
    u = np.arange(-KAPPA_WIN_M, KAPPA_WIN_M + KAPPA_STEP_M, KAPPA_STEP_M)
    deltas = np.arange(-KAPPA_SEARCH_M, KAPPA_SEARCH_M + KAPPA_STEP_M, KAPPA_STEP_M)
    out = np.full(len(positions), np.nan)
    for i, (d, p) in enumerate(zip(rec.frame, positions, strict=True)):
        if not np.isfinite(p):
            continue
        a = np.interp(d + u, rc, k_ref)
        grid = p + deltas[:, None] + u[None, :]
        b = np.interp(grid, lc, k_lap)
        err = np.mean((b - a[None, :]) ** 2, axis=1)
        out[i] = deltas[int(np.argmin(err))]
    return out


def _pct(a) -> str:
    return f"{100.0 * float(np.mean(a)):.1f} %"


def _variant_rows(rec: Rec, *, ref=None, drift=False, anchor=False):
    """Matched-odometer rows (one per comparison lap) under one variant."""
    ref = rec.best if ref is None else ref
    a = _anchor(rec) if anchor else None
    rows = []
    for lid in rec.clean:
        if lid == ref:
            continue
        shift = ((0.0, 0.0) if not (drift and rec.geometry)
                 else rec.geometry.relative_shift(rec.geometry.shift[lid], ref))
        rows.append(_match(rec, lid, ref=ref, shift=shift, anchor=a))
    return np.array(rows)


def _lag1(v: np.ndarray) -> float:
    v = v - v.mean(axis=0)
    return float(np.sum(v[:-1] * v[1:]) / np.sqrt(np.sum(v[:-1] ** 2) * np.sum(v[1:] ** 2)))


def probe(rec: Rec) -> None:
    from studio import corners

    inner = rec.interior()
    g = rec.geometry
    print(f"\n{'=' * 78}\n{rec.key}: {len(rec.clean)} clean laps, reference (best) lap {rec.best}, "
          f"{int(inner.sum())} interior boundaries of {len(rec.frame)}, total_ref "
          f"{rec.total_ref:.2f} m\n{'=' * 78}")
    if g is None:
        print("no session geometry could be fitted")
        return

    # ---- 1. the fitted geometry
    ids = sorted(g.shift)
    size = np.array([float(np.hypot(*g.shift[i])) for i in ids])
    res = np.array([g.residual_rms[i] for i in ids])
    c = g.consensus[np.isfinite(g.consensus)]
    print(f"1. fitted geometry: |T| median {np.median(size):.2f} m, p90 "
          f"{np.percentile(size, 90):.2f} m, max {size.max():.2f} m; residual RMS after it median "
          f"{np.median(res):.2f} m; consensus line vs the reference lap: median |c| "
          f"{np.median(np.abs(c)):.2f} m, max {np.abs(c).max():.2f} m "
          f"(stations answered {len(c)}/{len(g.consensus)})")
    tv = np.array([g.shift[i] for i in ids])
    print(f"   reference lap {rec.best}: |T| {np.hypot(*g.shift[rec.best]):.2f} m (rank "
          f"{1 + int(np.sum(size < np.hypot(*g.shift[rec.best])))}/{len(ids)} smallest), residual "
          f"{g.residual_rms[rec.best]:.2f} m; consecutive-lap lag-1 of T {_lag1(tv):+.2f}")

    # ---- 2. THE CONTROL: a planted translation is recovered, a planted wide line is not
    victim = [i for i in rec.clean if i != rec.best][0]
    xs, ys, cum = rec.trace[victim]
    frame = corners._station_frame(g.stations, *rec.trace[rec.best])
    base = corners.perpendicular_offsets(g.stations, rec.total_ref, frame, xs, ys, cum)
    nx, ny = frame[4], frame[5]
    for tx, ty in PLANTED_SHIFTS_M:
        off = corners.perpendicular_offsets(g.stations, rec.total_ref, frame, xs + tx, ys + ty, cum)
        t, _r = corners._rigid_shift(off - base, nx, ny)
        print(f"2. planted TRANSLATION ({tx:+.1f}, {ty:+.1f}) m on lap {victim} -> recovered "
              f"({t[0]:+.2f}, {t[1]:+.2f}), error {np.hypot(t[0] - tx, t[1] - ty):.3f} m")
    # A genuinely wider line: displace the lap along its OWN outward normal, which is what a
    # driver can do and a receiver cannot.
    ltx, lty = corners._unit_tangents(xs, ys)
    for w in PLANTED_LINE_M:
        off = corners.perpendicular_offsets(g.stations, rec.total_ref, frame,
                                            xs + w * -lty, ys + w * ltx, cum)
        t, r = corners._rigid_shift(off - base, nx, ny)
        print(f"   planted WIDE LINE {w:+.1f} m along the lap's own normal -> fitted |T| "
              f"{np.hypot(*t):.2f} m ({100 * np.hypot(*t) / w:.0f} % absorbed), residual {r:.2f} m")

    # ---- 3. the halves and the whole, interior boundaries
    variants = {
        "as shipped": dict(),
        "de-drift only": dict(drift=True),
        "consensus anchor only": dict(anchor=True),
        "both": dict(drift=True, anchor=True),
    }
    shipped_rows = None
    for name, kw in variants.items():
        rows = _variant_rows(rec, **kw)
        if shipped_rows is None:
            shipped_rows = rows
        print(f"3. {name:<24} interior boundaries matched {_pct(np.isfinite(rows[:, inner]))}")
    # M6's counterfactual for comparison: the nearest-consensus lap as the reference.
    dev = {i: float(np.hypot(*g.shift[i])) for i in rec.clean}
    alt = min(dev, key=dev.get)
    for name, kw in ((f"M6 reference = lap {alt}", dict(ref=alt)),
                     ("M6 both", dict(ref=alt, drift=True))):
        rows = _variant_rows(rec, **kw)
        print(f"   {name:<24} interior boundaries matched {_pct(np.isfinite(rows[:, inner]))}")

    # ---- 4. what moves that already matched
    others = [i for i in rec.clean if i != rec.best]
    for name, kw in (("de-drift only", dict(drift=True)),
                     ("both", dict(drift=True, anchor=True))):
        rows = _variant_rows(rec, **kw)
        keep = np.isfinite(shipped_rows) & np.isfinite(rows)
        move = np.abs(rows[keep] - shipped_rows[keep])
        dt = []
        for r, lid in enumerate(others):
            dist, elapsed = rec.td[lid]
            for k in range(len(rec.frame) // 2):
                e, x = 2 * k, 2 * k + 1
                if keep[r, e] and keep[r, x]:
                    dt.append((np.interp(rows[r, x], dist, elapsed)
                               - np.interp(rows[r, e], dist, elapsed))
                              - (np.interp(shipped_rows[r, x], dist, elapsed)
                                 - np.interp(shipped_rows[r, e], dist, elapsed)))
        dt = np.abs(np.asarray(dt))
        lost = int((np.isfinite(shipped_rows) & ~np.isfinite(rows)).sum())
        print(f"4. {name:<14} boundaries matched before AND after {int(keep.sum())} (lost {lost}); "
              f"|Δ odometer| median {np.median(move):.3f} m, p90 {np.percentile(move, 90):.3f} m, "
              f"max {move.max():.3f} m; those cells' corner TIMES ({len(dt)}) |Δ| median "
              f"{np.median(dt):.4f} s, p90 {np.percentile(dt, 90):.4f} s, max {dt.max():.4f} s")


    # ---- 5. the translation-invariant witness: did the boundaries move TOWARDS the track?
    candidates = {"de-drift only": _variant_rows(rec, drift=True),
                  "both": _variant_rows(rec, drift=True, anchor=True)}
    keep_all = np.isfinite(shipped_rows) & inner
    for rows in candidates.values():
        keep_all &= np.isfinite(rows)
    scored = {"as shipped": shipped_rows, **candidates}
    err = {}
    for name, rows in scored.items():
        err[name] = np.concatenate([
            np.abs(_kappa_shift(rec, lid, np.where(keep_all[r], rows[r], np.nan)))[keep_all[r]]
            for r, lid in enumerate(others) if keep_all[r].any()])
    base = err["as shipped"]
    print(f"5. curvature witness on the {len(base)} interior boundaries every variant matched — "
          f"|longitudinal disagreement with the track's own shape|:")
    for name in scored:
        e = err[name]
        better = int(np.sum(e < base - 1e-9))
        worse = int(np.sum(e > base + 1e-9))
        print(f"   {name:<16} median {np.median(e):.2f} m, mean {e.mean():.2f}, p90 "
              f"{np.percentile(e, 90):.2f}" + ("" if name == "as shipped" else
              f"; better on {better}, worse on {worse}, unchanged on {len(e) - better - worse}"))

    # ---- 6. THROUGH THE REAL APP: how many of #331's dashed cells become measurements
    from studio import corners as corners_mod

    cm = rec.session.corners
    new_cells, new_edges = rec.resolved()
    real_geometry, real_anchor = cm.geometry, corners_mod.anchor_offsets
    corners_mod.anchor_offsets = lambda *a, **k: None
    cm.invalidate_stats()
    drift_cells, drift_edges = rec.resolved()
    cm.geometry = lambda: None
    cm.invalidate_stats()
    old_cells, old_edges = rec.resolved()
    cm.geometry, corners_mod.anchor_offsets = real_geometry, real_anchor
    cm.invalidate_stats()
    if not np.array_equal(rec.resolved()[0], new_cells):
        raise SystemExit("the geometry memo did not restore — fix the probe before reading it")
    for name, cells, edges in (("de-drift only", drift_cells, drift_edges),
                               ("both", new_cells, new_edges)):
        print(f"6. {name:<14} CornerModel.lap_corner_resolved over {len(rec.clean)} clean laps: "
              f"{int(old_cells.sum())}/{old_cells.size} cells -> {int(cells.sum())} "
              f"({int((cells & ~old_cells).sum())} dashed cells become measurements, "
              f"{int((old_cells & ~cells).sum())} go the other way); lap_edge_resolved "
              f"{int(old_edges.sum())}/{old_edges.size} -> {int(edges.sum())}")
    best_row = rec.clean.index(rec.best)
    print(f"   the reference lap {rec.best} still resolves every cell: "
          f"{bool(new_cells[best_row].all())} (edges {bool(new_edges[best_row].all())})")


def main() -> None:
    from studio.dev import _jail

    # BEFORE any studio import resolves a seam: the load-time library upsert resolves it at call
    # time, so diverting afterwards is too late for exactly the write that matters.
    jail = _jail.divert_app_support("pacer-p8-")
    print(f"app-support seams diverted to {jail.dir}")
    for key in sys.argv[1:] or list(RECORDINGS):
        probe(load(key))


if __name__ == "__main__":
    main()
