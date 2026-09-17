"""P6 — why does D24 0060 match only 48 % of its corner cells on track, against >99 % on 0062? (M6)

A lap's corner windows come from ONE warp per lap (`corners.lap_alignment`), whose knots are the
corner boundaries that `corners._spatial_matches` found on that lap. A boundary that does not match
is interpolated, and #329 measured interpolated cells a median 0.22 s wrong. This probe asks WHY the
match fails on 0060 and not on 0062, testing each candidate cause instead of assuming one:

  1. which GATE fails — the search arc, the heading gate, the 3 m distance gate, or monotonicity —
     using the real `_spatial_matches` with one gate relaxed at a time, never a copy of it;
  2. the GPS itself — DOP, fix interval, interior gaps, rejected fixes;
  3. how far each lap's trace sits from the session's CONSENSUS line, how much of that is ONE rigid
     translation per lap (which no driving line can produce around a closed circuit), whether that
     translation persists from lap to lap, and the ALTITUDE witness (which a line cannot move either);
  4. the reference lap: where it sits against the consensus, and what every other choice would give;
  5. counterfactuals on the shipped gate: translation removed, a different reference, both, and the
     threshold swept;
  6. chapter seams;
  7. which boundaries fail, and whether DOP at a boundary predicts it.

Whether #300, #322 or #325 changed any of this is not re-run here (it needs the old trees): the
method and result are in `studio/docs/corner-match-0060-2026-09.md`, which is where every number this
probe prints is written up.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p6_corner_match_cause
    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p6_corner_match_cause 0060

TWO SAFETY PROPERTIES, both enforced rather than promised, exactly as `p4` enforces them:
  * `~/Desktop/D24` IS READ-ONLY. No path under it is ever passed as an output argument, and every
    chapter's (size, mtime) is snapshotted before and after each load and compared. This probe
    writes no file at all.
  * `Session.load` UPSERTS INTO THE USER'S LIBRARY, so `studio.dev._jail` diverts every app-support
    seam to a throwaway dir BEFORE any studio import resolves one.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass

import numpy as np

# ~/Desktop/D24 is READ-ONLY: opened for reading only, never as an output argument. GX010060.MP4 is
# deliberately absent — it is a 2.4 MB JSON stub that overwrote 11.9 GB of the owner's footage.
D24 = os.path.expanduser("~/Desktop/D24")
RECORDINGS = {
    "0060": ["GX020060.MP4", "GX030060.MP4"],
    "0062": ["GX010062.MP4", "GX020062.MP4", "GX030062.MP4"],
}
ANCHOR_M = 5.0              # consensus-line anchors, every 5 m of the reference lap
WIDE_ARC = 0.25             # an "unwindowed" search: ±25 % of the lap, never the far side
THRESHOLDS_M = (2.0, 3.0, 4.0, 5.0, 6.0, 8.0)
SHUFFLES = 2000


def _d24_state(paths):
    """(size, mtime_ns) per chapter — the read-only tripwire, taken before and after the load."""
    return {p: (os.path.getsize(p), os.stat(p).st_mtime_ns) for p in paths}


@dataclass
class Rec:
    key: str
    cols: dict           # lap id -> (x, y, cum, dop, alt, t, speed km/h)
    clean: list[int]
    best: int
    frame: np.ndarray    # the whole corner partition, reference odometer (enter, exit, ...)
    total_ref: float
    resolved: np.ndarray  # (clean, boundaries) — a knot of the app's own warp, or on the line
    seam_laps: list[int]
    dropped_fraction: float
    cells_shipped: np.ndarray  # (clean, corners) — `CornerModel.lap_corner_resolved`, as shipped

    def interior(self) -> np.ndarray:
        """Boundaries strictly inside the lap. C1's enter and C12's exit sit ON the timing line on
        both recordings, where the warp is anchored exactly and no match is needed."""
        return (self.frame > 1e-9) & (self.frame < self.total_ref - 1e-9)


def _seam_laps(session, lap_ids) -> list[int]:
    """Laps whose own span contains a chapter seam. A chapter offset is a position in the footage's
    stamps, so it is carried to the telemetry axis on the stamp map; at lap scale either map names
    the same laps."""
    stamp = session.media_clock.without_gps_lag()
    seams = [float(stamp.to_telemetry(c.offset)) for c in session.chapters.chapters[1:]]
    out = []
    for lid in lap_ids:
        t = session._lap_columns(lid)[0]
        if any(t[0] <= s <= t[-1] for s in seams):
            out.append(lid)
    return out


def load(key: str) -> Rec:
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
    cols, resolved = {}, []
    for lid in clean:
        t, x, y, v, cum = s._lap_columns(lid)
        fixes = s._lap_fixes(lid)
        cols[lid] = (np.asarray(x, float), np.asarray(y, float), np.asarray(cum, float),
                     np.asarray(fixes.dop, float), s.lap_elevation_channel(lid),
                     np.asarray(t, float), np.asarray(v, float) * 3.6)
        warp = s.corners.lap_alignment(lid, float(cum[-1]))
        on_line = (frame <= 1e-9) | (frame >= total_ref - 1e-9)
        knot = np.array([warp is not None and bool(np.any(np.abs(warp[0] - b) < 1e-9))
                         for b in frame])
        resolved.append(knot | on_line)
    return Rec(key, cols, clean, int(s.best_lap_id()), frame, float(total_ref),
               np.asarray(resolved), _seam_laps(s, range(s.lap_count())),
               float(s.timing_quality.dropped_fraction),
               np.asarray([s.corners.lap_corner_resolved(lid) for lid in clean], bool))


@contextmanager
def _gates(*, match_m=None, arc=None, min_cos=None):
    """Relax ONE gate of the real `corners._spatial_matches` for the duration of a block."""
    from studio import corners

    saved = (corners.SPATIAL_MATCH_MAX_M, corners._SPATIAL_SEARCH_FRAC,
             corners._SPATIAL_HEADING_MIN_COS)
    try:
        if match_m is not None:
            corners.SPATIAL_MATCH_MAX_M = match_m
        if arc is not None:
            corners._SPATIAL_SEARCH_FRAC = arc
        if min_cos is not None:
            corners._SPATIAL_HEADING_MIN_COS = min_cos
        yield
    finally:
        (corners.SPATIAL_MATCH_MAX_M, corners._SPATIAL_SEARCH_FRAC,
         corners._SPATIAL_HEADING_MIN_COS) = saved


def _match(rec: Rec, lid: int, ref: int, shift=(0.0, 0.0)) -> np.ndarray:
    """The real match of every boundary of `lid` against reference lap `ref` — the session's corner
    partition carried onto `ref`'s odometer by fraction (identity for the app's own reference) —
    with `lid`'s trace optionally translated by `shift` first. Odometer, NaN where it failed."""
    from studio import corners

    rx, ry, rc = rec.cols[ref][:3]
    lx, ly, lc = rec.cols[lid][:3]
    frame = rec.frame * float(rc[-1]) / rec.total_ref
    return corners._spatial_matches(frame, float(rc[-1]), rx, ry, rc,
                                    lx - shift[0], ly - shift[1], lc)


def _nearest_m(rec: Rec, lid: int) -> np.ndarray:
    """Distance from each reference anchor to the point the match lands on with the arc widened to
    WIDE_ARC and no distance gate — the closest approach the 3 m gate is judging."""
    rx, ry, rc = rec.cols[rec.best][:3]
    lx, ly, lc = rec.cols[lid][:3]
    with _gates(match_m=np.inf, arc=WIDE_ARC):
        m = _match(rec, lid, rec.best)
    return np.hypot(np.interp(m, lc, lx) - np.interp(rec.frame, rc, rx),
                    np.interp(m, lc, ly) - np.interp(rec.frame, rc, ry))


def _offsets(rec: Rec, lid: int, anchors: np.ndarray):
    """Signed perpendicular offset of lap `lid` from the app's reference lap at reference-odometer
    `anchors` (same-direction nearest segment within WIDE_ARC), plus the reference's unit normals."""
    from studio import corners

    rx, ry, rc = rec.cols[rec.best][:3]
    lx, ly, lc = rec.cols[lid][:3]
    ax, ay = np.interp(anchors, rc, rx), np.interp(anchors, rc, ry)
    tx, ty = corners._unit_tangents(rx, ry)
    atx, aty = np.interp(anchors, rc, tx), np.interp(anchors, rc, ty)
    norm = np.hypot(atx, aty)
    atx, aty = atx / norm, aty / norm
    nx, ny = -aty, atx
    ltx, lty = corners._unit_tangents(lx, ly)
    n = len(lc)
    centers = np.clip(np.searchsorted(lc, anchors / rc[-1] * lc[-1]), 0, n - 1)
    off = np.full(len(anchors), np.nan)
    for i, c in enumerate(centers):
        lo, hi = max(0, c - int(WIDE_ARC * n)), min(n - 1, c + int(WIDE_ARC * n))
        x0, y0, vx, vy = lx[lo:hi], ly[lo:hi], np.diff(lx[lo:hi + 1]), np.diff(ly[lo:hi + 1])
        len2 = vx * vx + vy * vy
        len2[len2 == 0] = np.inf
        u = np.clip(((ax[i] - x0) * vx + (ay[i] - y0) * vy) / len2, 0.0, 1.0)
        qx, qy = x0 + u * vx, y0 + u * vy
        d2 = np.where(atx[i] * ltx[lo:hi] + aty[i] * lty[lo:hi] >= 0.5,
                      (qx - ax[i]) ** 2 + (qy - ay[i]) ** 2, np.inf)
        if np.isfinite(d2).any():
            j = int(np.argmin(d2))
            off[i] = (qx[j] - ax[i]) * nx[i] + (qy[j] - ay[i]) * ny[i]
    return off, nx, ny


def _translation(off, nx, ny):
    """Least-squares rigid 2D translation T with off_i ≈ n_i · T, and the residual."""
    ok = np.isfinite(off)
    a = np.column_stack([nx[ok], ny[ok]])
    t, *_ = np.linalg.lstsq(a, off[ok], rcond=None)
    return t, off[ok] - a @ t


def _lag1(v: np.ndarray) -> float:
    v = v - v.mean(axis=0)
    return float(np.sum(v[:-1] * v[1:]) / np.sqrt(np.sum(v[:-1] ** 2) * np.sum(v[1:] ** 2)))


def _persistence(v: np.ndarray, rng) -> tuple[float, float]:
    obs = _lag1(v)
    null = np.array([_lag1(v[rng.permutation(len(v))]) for _ in range(SHUFFLES)])
    return obs, float(np.mean(null >= obs))


def _pct(a) -> str:
    return f"{100.0 * float(np.mean(a)):.1f} %"


def probe(rec: Rec) -> None:
    rng = np.random.default_rng(6)
    inner = rec.interior()
    others = [lid for lid in rec.clean if lid != rec.best]
    print(f"\n{'=' * 78}\n{rec.key}: {len(rec.clean)} clean laps, reference (best) lap {rec.best}, "
          f"{len(rec.frame)} boundaries ({int(inner.sum())} interior), total_ref "
          f"{rec.total_ref:.2f} m\n{'=' * 78}")
    cells = rec.resolved[:, 0::2] & rec.resolved[:, 1::2]
    if not np.array_equal(cells, rec.cells_shipped):
        raise SystemExit("this probe's per-boundary knot rule disagrees with "
                         "CornerModel.lap_corner_resolved — fix the probe before reading it")
    print(f"cells resolved (CornerModel.lap_corner_resolved): {int(rec.cells_shipped.sum())} / "
          f"{rec.cells_shipped.size}; the per-boundary knot rule below agrees cell for cell")

    # ---- 1. which gate fails (the real function, one gate relaxed at a time)
    counts = dict.fromkeys(("matched", "search arc", "heading", "distance", "monotonic"), 0)
    for lid in others:
        shipped = np.isfinite(_match(rec, lid, rec.best))
        with _gates(arc=WIDE_ARC):
            wide = np.isfinite(_match(rec, lid, rec.best))
        with _gates(arc=WIDE_ARC, min_cos=-1.0):
            any_dir = np.isfinite(_match(rec, lid, rec.best))
        row = rec.resolved[rec.clean.index(lid)]
        for b in np.flatnonzero(inner):
            if shipped[b]:
                counts["matched" if row[b] else "monotonic"] += 1
            elif wide[b]:
                counts["search arc"] += 1
            elif any_dir[b]:
                counts["heading"] += 1
            else:
                counts["distance"] += 1
    total = sum(counts.values())
    print("1. interior boundaries by the gate that failed: "
          + ", ".join(f"{k} {v} ({100.0 * v / total:.1f} %)" for k, v in counts.items()))
    near = np.array([_nearest_m(rec, lid)[inner] for lid in others])
    print(f"   closest approach the 3 m gate judges: median {np.median(near):.2f} m, p90 "
          f"{np.percentile(near, 90):.2f} m")

    # ---- 2. the GPS itself
    dop = np.concatenate([rec.cols[lid][3][1:-1] for lid in rec.clean])
    dop = dop[np.isfinite(dop) & (dop > 0)]
    dt = np.concatenate([np.diff(rec.cols[lid][5][1:-1]) for lid in rec.clean])
    print(f"2. GPS: DOP median {np.median(dop):.2f}, p90 {np.percentile(dop, 90):.2f}; fix interval "
          f"median {np.median(dt):.3f} s, max {dt.max():.3f} s; interior gaps > 0.35 s: "
          f"{int(np.sum(dt > 0.35))}; rejected fixes: {100.0 * rec.dropped_fraction:.2f} %")

    # ---- 3. consensus line, rigid translation, altitude witness, persistence
    anchors = np.arange(ANCHOR_M / 2, rec.total_ref, ANCHOR_M)
    offs, alts, speeds = {}, {}, {}
    for lid in rec.clean:
        offs[lid], nx, ny = _offsets(rec, lid, anchors)
        _x, _y, cum, _d, alt, _t, v = rec.cols[lid]
        alts[lid] = np.interp(anchors / rec.total_ref * cum[-1], cum, alt)
        speeds[lid] = np.interp(anchors / rec.total_ref * cum[-1], cum, v)
    consensus = np.nanmedian(np.array([offs[lid] for lid in rec.clean]), axis=0)
    alt_consensus = np.median(np.array([alts[lid] for lid in rec.clean]), axis=0)
    trans, raw, res, alt_off, alt_wander = {}, [], [], [], []
    for lid in rec.clean:
        e = offs[lid] - consensus
        trans[lid], r = _translation(e, nx, ny)
        raw.append(np.nanmean(e ** 2))
        res.append(np.mean(r ** 2))
        a = alts[lid] - alt_consensus
        alt_off.append(np.median(a))
        alt_wander.append(np.sqrt(np.mean((a - np.median(a)) ** 2)))
    tv = np.array([trans[lid] for lid in rec.clean])
    size = np.hypot(tv[:, 0], tv[:, 1])
    print(f"3. laps vs the consensus line: RMS median {np.median(np.sqrt(raw)):.2f} m; one rigid "
          f"translation per lap |T| median {np.median(size):.2f} m, max {size.max():.2f} m, explains "
          f"{100.0 * (1 - sum(res) / sum(raw)):.0f} % of the offset variance; residual RMS median "
          f"{np.median(np.sqrt(res)):.2f} m")
    lag_t, p_t = _persistence(tv, rng)
    lag_a, p_a = _persistence(np.asarray(alt_off)[:, None], rng)
    print(f"   ALTITUDE (no driving line moves it): per-lap offset sd {np.std(alt_off):.2f} m, range "
          f"{min(alt_off):+.2f}..{max(alt_off):+.2f} m; within-lap wander median "
          f"{np.median(alt_wander):.2f} m")
    print(f"   PERSISTENCE, consecutive laps (shuffle p over {SHUFFLES}): translation lag-1 "
          f"{lag_t:+.2f} (p {p_t:.3f}); altitude offset lag-1 {lag_a:+.2f} (p {p_a:.3f})")

    # ---- 4. the reference lap
    dev = {lid: float(np.nanmedian(np.abs(offs[lid] - consensus))) for lid in rec.clean}
    ranked = sorted(dev, key=dev.get)
    t_ref, _ = _translation(-consensus, nx, ny)
    print(f"4. reference lap {rec.best}: median |offset| from consensus {dev[rec.best]:.2f} m (rank "
          f"{ranked.index(rec.best) + 1}/{len(ranked)} nearest), rigid translation "
          f"{np.hypot(*t_ref):.2f} m; nearest-consensus lap is {ranked[0]} ({dev[ranked[0]]:.2f} m)")
    as_ref = {}
    for ref in rec.clean:
        m = np.array([np.isfinite(_match(rec, lid, ref)) for lid in rec.clean if lid != ref])
        as_ref[ref] = float(np.mean(m[:, 0::2] & m[:, 1::2]))
    order = sorted(as_ref, key=as_ref.get, reverse=True)
    vals = np.array(list(as_ref.values()))
    print(f"   cells matched with each clean lap as the reference: min {100 * vals.min():.1f} %, "
          f"median {100 * np.median(vals):.1f} %, max {100 * vals.max():.1f} %; the app's reference "
          f"ranks {order.index(rec.best) + 1}/{len(order)} ({100 * as_ref[rec.best]:.1f} %)")

    # ---- 5. counterfactuals on the shipped 3 m gate, interior boundaries
    def rate(ref, shift_of=None):
        rows = [np.isfinite(_match(rec, lid, ref, shift_of(lid) if shift_of else (0.0, 0.0)))[inner]
                for lid in rec.clean if lid != ref]
        return _pct(np.array(rows))

    alt_ref = ranked[0]
    print(f"5. interior boundaries matched: as shipped {rate(rec.best)}; each lap's translation "
          f"relative to the reference removed {rate(rec.best, lambda lid: trans[lid] - t_ref)}; "
          f"reference = lap {alt_ref} {rate(alt_ref)}; both "
          f"{rate(alt_ref, lambda lid: trans[lid] - trans[alt_ref])}")
    for ref in (rec.best, alt_ref):
        sweep = []
        for thr in THRESHOLDS_M:
            with _gates(match_m=thr):
                m = np.array([np.isfinite(_match(rec, lid, ref)) for lid in rec.clean if lid != ref])
            sweep.append(f"{thr:g} m {_pct(m[:, 0::2] & m[:, 1::2])}")
        print(f"   cells vs threshold, reference lap {ref}: " + " | ".join(sweep))
    fail = [1 - np.mean(np.isfinite(_match(rec, lid, rec.best))[inner]) for lid in others]
    rel = [np.hypot(*(trans[lid] - t_ref)) for lid in others]
    print(f"   per-lap failure share vs |T_lap - T_reference|: r = {np.corrcoef(fail, rel)[0, 1]:+.2f}")
    worst_laps = sorted(zip(fail, others, rel, strict=True), reverse=True)[:5]
    print("   worst laps (lap: interior matched, |T_lap - T_reference|): " + ", ".join(
        f"{lid}: {round((1 - f) * inner.sum())}/{int(inner.sum())}, {r:.1f} m"
        for f, lid, r in worst_laps))

    # ---- 6. chapter seams
    by_lap = {lid: float(np.mean(np.isfinite(_match(rec, lid, rec.best))[inner])) for lid in others}
    near_seam = [lid for lid in by_lap if any(abs(lid - s) <= 1 for s in rec.seam_laps)]
    rest = [lid for lid in by_lap if lid not in near_seam]
    print(f"6. seam laps {rec.seam_laps}: laps within ±1 of a seam {_pct([by_lap[i] for i in near_seam])} "
          f"({len(near_seam)} laps), the rest {_pct([by_lap[i] for i in rest])} ({len(rest)} laps)")
    chapters = {}
    for lid in by_lap:
        if lid not in rec.seam_laps:
            chapters.setdefault(sum(s < lid for s in rec.seam_laps), []).append(by_lap[lid])
    print("   by loaded chapter (seam laps left out): " + ", ".join(
        f"chapter {k + 1} {_pct(v)} ({len(v)} laps)" for k, v in sorted(chapters.items())))

    # ---- 7. where it fails, and whether DOP there predicts it
    fails = 1 - np.array([np.isfinite(_match(rec, lid, rec.best)) for lid in others]).mean(axis=0)
    names = [f"C{b // 2 + 1}{'in' if b % 2 == 0 else 'out'}" for b in range(len(rec.frame))]
    worst = [b for b in np.argsort(-fails) if inner[b]][:4]
    print("7. worst boundaries: " + ", ".join(f"{names[b]} {100 * fails[b]:.0f} %" for b in worst))
    sd = np.nanstd(np.array([offs[lid] - consensus for lid in rec.clean]), axis=0)
    rigid = nx * t_ref[0] + ny * t_ref[1]
    ref_alt = alts[rec.best] - alt_consensus
    ref_speed = speeds[rec.best] - np.median(np.array([speeds[lid] for lid in rec.clean]), axis=0)
    for b in worst[:2]:
        at = np.abs(anchors - rec.frame[b]) <= ANCHOR_M
        print(f"   {names[b]}: reference offset from consensus {np.nanmean(-consensus[at]):+.1f} m "
              f"= rigid translation {np.mean(rigid[at]):+.1f} + local {np.nanmean(-consensus[at] - rigid[at]):+.1f} "
              f"(whole lap median |offset| {dev[rec.best]:.2f}); lap-to-lap sd there "
              f"{np.nanmedian(sd[at]):.2f} m vs {np.nanmedian(sd):.2f} m whole lap; reference "
              f"altitude vs consensus {np.mean(ref_alt[at]):+.1f} m there, {np.median(ref_alt):+.1f} m "
              f"whole lap; reference speed vs consensus {np.mean(ref_speed[at]):+.1f} km/h")
    pairs = []
    for lid in others:
        _x, _y, cum, d, _a, _t, _v = rec.cols[lid]
        m = np.isfinite(_match(rec, lid, rec.best))
        for b in np.flatnonzero(inner):
            here = rec.frame[b] * cum[-1] / rec.total_ref
            sel = (np.abs(cum - here) <= 10.0) & np.isfinite(d) & (d > 0)
            if sel.any():
                pairs.append((float(np.max(d[sel])), not m[b]))
    pairs = np.array(pairs)
    bands = []
    for lo, hi in ((0.0, 1.5), (1.5, 2.0), (2.0, 2.5), (2.5, 3.5), (3.5, 99.0)):
        s = (pairs[:, 0] >= lo) & (pairs[:, 0] < hi)
        if s.sum():
            bands.append(f"[{lo:g},{hi:g}) n={int(s.sum())} fail {_pct(pairs[s, 1])}")
    print("   worst DOP within ±10 m of the boundary on the lap: " + "; ".join(bands))


def main() -> None:
    from studio.dev import _jail

    # BEFORE any studio import resolves a seam: the load-time library upsert resolves it at call
    # time, so diverting afterwards is too late for exactly the write that matters.
    jail = _jail.divert_app_support("pacer-p6-")
    print(f"app-support seams diverted to {jail.dir}")
    for key in sys.argv[1:] or list(RECORDINGS):
        probe(load(key))


if __name__ == "__main__":
    main()
