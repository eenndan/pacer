"""P4 — is a PER-CORNER GPS-quality abstain reachable, and would it distinguish corners?

`coaching.corner_evidence` abstains on too few laps, a lone-outlier target, or a claim inside the
corner's own spread — never on degraded GPS through THAT corner. This probe asks whether it could,
at the exact cell such a gate would key on: one (clean lap x corner) window.

Per cell it reports

  * the `QualityTimeline` class the window inherits (the app's own worst-cell fold, the same one
    `Session.lap_quality` applies to a whole lap);
  * how many raw fixes inside the window the loader's quality gate REJECTED — the key the backlog
    proposed ("the fraction of degraded fixes inside the corner window");
  * the worst DOP among the fixes it KEPT;
  * the window's interior sample gap and sample count, re-measuring #255's own two numbers rather
    than inheriting them;
  * whether the cell's verdict survives being indexed on the media clock instead of the telemetry
    clock — the crossing `Session.lap_quality` documents as harmless at LAP scale, asked again at
    corner scale, where the window is ~3-6 s rather than ~70 s.

The verdict is written up in `studio/docs/refused-2026-09.md` §3. Every number there comes from
here.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p4_corner_gps_quality
    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p4_corner_gps_quality 0060

TWO SAFETY PROPERTIES, both enforced rather than promised, exactly as `_cache` enforces them:
  * `~/Desktop/D24` IS READ-ONLY. No path under it is ever passed as an output argument, and every
    chapter's (size, mtime) is snapshotted before and after each load and compared.
  * `Session.load` UPSERTS INTO THE USER'S LIBRARY, so `studio.dev._jail` diverts every app-support
    seam to a throwaway dir BEFORE any studio import resolves one.
"""

from __future__ import annotations

import os
import sys

import numpy as np

# ~/Desktop/D24 is READ-ONLY: opened for reading only, never as an output argument. GX010060.MP4 is
# deliberately absent — it is a 2.4 MB JSON stub that overwrote 11.9 GB of the owner's footage.
D24 = os.path.expanduser("~/Desktop/D24")
RECORDINGS = {
    "0060": ["GX020060.MP4", "GX030060.MP4"],
    "0062": ["GX010062.MP4", "GX020062.MP4", "GX030062.MP4"],
}
SAMPLES = "3rdparty/gpmf-parser/samples"


def _d24_state(paths):
    """(size, mtime_ns) per chapter — the read-only tripwire, taken before and after the load."""
    return {p: (os.path.getsize(p), os.stat(p).st_mtime_ns) for p in paths}


def _windows(session, corners_mod, lap, corner_list, total_ref, best_cols):
    """The corner windows on ONE lap, projected exactly as `corners.lap_corner_stats` projects
    them — the same whole-partition frame, the same spatial warp, the same boundaries.

    -> per corner: (d0, d1, t0, t1, n_samples, max_interior_gap_s), times on the lap's own
    TELEMETRY axis."""
    times, xs, ys, _v, cum = session._lap_columns(lap)
    times = np.asarray(times, float)
    cum = np.asarray(cum, float)
    if len(cum) < 2:
        return []
    total_lap = float(cum[-1])
    b_xs, b_ys, b_cum = best_cols
    traces = (b_xs, b_ys, b_cum, xs, ys, cum)
    frame = [b for c in corner_list for b in (float(c.enter), float(c.exit))]
    align = corners_mod.lap_alignment(frame, total_ref, total_lap, traces=traces)
    proj = corners_mod.project_boundaries(frame, total_ref, total_lap, traces=traces,
                                          alignment=align)
    out = []
    for i in range(len(corner_list)):
        d0, d1 = float(proj[2 * i]), float(proj[2 * i + 1])
        idx = np.flatnonzero((cum >= d0) & (cum <= d1))
        gap = float(np.max(np.diff(times[idx]))) if len(idx) >= 2 else float("nan")
        out.append((d0, d1, float(np.interp(d0, cum, times)), float(np.interp(d1, cum, times)),
                    len(idx), gap))
    return out


def probe_recording(key: str, paths: list[str]) -> None:
    from studio import corners as corners_mod
    from studio import data_quality
    from studio.session import Session

    print(f"\n{'=' * 78}\n{key}: {', '.join(os.path.basename(p) for p in paths)}\n{'=' * 78}")
    before = _d24_state(paths)
    session = Session.load(paths)
    after = _d24_state(paths)
    if after != before:
        moved = [os.path.basename(p) for p in paths if after[p] != before[p]]
        raise SystemExit(f"REFUSING TO CONTINUE: a D24 chapter changed during the load "
                         f"({', '.join(moved)}). That directory is read-only.")
    print(f"D24 read-only check: {len(paths)} chapters unchanged (size + mtime)")

    quality = session.timing_quality
    strip = session.quality_timeline
    clean = session.consistency_lap_ids()
    corner_list = session.corners.corner_list()
    basis = session.corners.basis()
    total_ref = float(basis[1]) if basis is not None else None
    best = session.best_lap_id()
    print(f"laps: {session.lap_count()} total, {len(session.valid_lap_ids())} valid, "
          f"{len(clean)} clean; corners: {len(corner_list)}; best lap {best}")
    print(f"whole-recording verdict: clock={quality.clock} dropped={quality.dropped_pct()}% "
          f"low_gps_quality={quality.low_gps_quality} degraded={quality.degraded}")
    named = {data_quality.QUALITY_LABEL[c]: n for c, n in sorted(strip.counts().items())}
    print(f"strip: {len(strip)} cells of {strip.cell_s:.0f}s -> {named}")
    print(f"strip.summary(): {strip.summary()}")

    if not clean or not corner_list or total_ref is None or best is None:
        print("NO CLEAN LAPS / NO CORNER MODEL — nothing per-corner to measure here.")
        return

    # The per-LAP fold the DATA TRUST card publishes, for the row this would sit one level under.
    lap_cls = {lid: session.lap_quality(lid) for lid in clean}
    below = [lid for lid, c in lap_cls.items() if c is not None and c < data_quality.GOOD]
    print(f"per-LAP (the card's row): {len(below)} of {len(clean)} clean laps inherit a class "
          f"below GOOD")

    _bt, b_xs, b_ys, _bv, b_cum = session._lap_columns(best)
    best_cols = (b_xs, b_ys, b_cum)

    n_corners = len(corner_list)
    cls_hist: dict[int, int] = {}
    per_corner_below = [0] * n_corners
    per_corner_cells = [0] * n_corners
    dropped_cells = total_dropped = clock_flips = n_cells = 0
    worst_gap = 0.0
    thinnest = 10 ** 9
    dops: list[float] = []

    for lid in clean:
        for i, (_d0, _d1, t0, t1, n_s, gap) in enumerate(
                _windows(session, corners_mod, lid, corner_list, total_ref, best_cols)):
            n_cells += 1
            per_corner_cells[i] += 1
            cls = strip.worst_between(t0, t1)
            stats = strip.stats_between(t0, t1)
            if cls is not None:
                cls_hist[cls] = cls_hist.get(cls, 0) + 1
                if cls < data_quality.GOOD:
                    per_corner_below[i] += 1
            if stats is not None:
                if stats["dropped"]:
                    dropped_cells += 1
                    total_dropped += int(stats["dropped"])
                if np.isfinite(stats["dop"]):
                    dops.append(float(stats["dop"]))
            # Does the verdict survive the OTHER clock? `lap_quality` measures this crossing as
            # harmless over a whole lap; a corner window is an order of magnitude shorter.
            if strip.worst_between(session.media_time(t0), session.media_time(t1)) != cls:
                clock_flips += 1
            if np.isfinite(gap):
                worst_gap = max(worst_gap, gap)
            thinnest = min(thinnest, n_s)

    named_hist = {data_quality.QUALITY_LABEL[c]: n for c, n in sorted(cls_hist.items())}
    print(f"\nPER-CORNER CELLS: {n_cells} (lap x corner) over {len(clean)} clean laps")
    print(f"  class histogram: {named_hist}")
    print(f"  cells containing a REJECTED fix: {dropped_cells} "
          f"({total_dropped} rejected fixes in total)")
    if dops:
        a = np.asarray(dops)
        print(f"  worst-kept-DOP per cell: min {a.min():.2f} median {np.median(a):.2f} "
              f"p95 {np.percentile(a, 95):.2f} max {a.max():.2f} "
              f"(> {data_quality.DOP_GOOD_MAX:g} on {int((a > data_quality.DOP_GOOD_MAX).sum())} "
              f"of {len(a)} cells)")
    print(f"  worst interior sample gap in any corner window: {worst_gap:.4f}s")
    print(f"  thinnest corner window: {thinnest} samples")
    print(f"  cells whose class changes if indexed on the MEDIA clock instead: "
          f"{clock_flips} of {n_cells}")
    print("  per-corner cells below GOOD (the separability question):")
    for i, c in enumerate(corner_list):
        n_bad, n_tot = per_corner_below[i], per_corner_cells[i]
        print(f"    C{c.cid:<3} {n_bad:>4} / {n_tot:<4} "
              f"({100.0 * n_bad / n_tot if n_tot else 0.0:5.1f}%)")
    fracs = [per_corner_below[i] / per_corner_cells[i] if per_corner_cells[i] else 0.0
             for i in range(n_corners)]
    print(f"  corner-to-corner spread of that fraction: min {min(fracs):.3f} "
          f"max {max(fracs):.3f} range {max(fracs) - min(fracs):.3f}")


def probe_samples() -> None:
    """Every bundled sample: can any of them reach a per-corner gate at all?"""
    from studio import data_quality
    from studio.session import Session

    print(f"\n{'=' * 78}\nBUNDLED SAMPLES ({SAMPLES})\n{'=' * 78}")
    for name in sorted(os.listdir(SAMPLES)):
        if not name.endswith(".mp4"):
            continue
        try:
            session = Session.load([os.path.join(SAMPLES, name)])
        except Exception as exc:  # noqa: BLE001 — a sample that cannot load is a result, not a crash
            print(f"{name:<20} LOAD FAILED: {type(exc).__name__}: {exc}")
            continue
        strip = session.quality_timeline
        counts = {data_quality.QUALITY_LABEL[c]: n for c, n in sorted(strip.counts().items())}
        print(f"{name:<20} laps={session.lap_count():<3} "
              f"clean={len(session.consistency_lap_ids()):<3} "
              f"corners={len(session.corners.corner_list()):<3} "
              f"clock={session.timing_quality.clock:<20} "
              f"dropped={session.timing_quality.dropped_pct()}% cells={counts}")


def main() -> None:
    from studio.dev import _jail

    # BEFORE any studio import resolves a seam: the load-time library upsert resolves it at call
    # time, so diverting afterwards is too late for exactly the write that matters.
    jail = _jail.divert_app_support("pacer-p4-")
    print(f"app-support seams diverted to {jail.dir}")

    for key in sys.argv[1:] or ["0060", "0062", "samples"]:
        if key == "samples":
            probe_samples()
        else:
            probe_recording(key, [os.path.join(D24, n) for n in RECORDINGS[key]])


if __name__ == "__main__":
    main()
