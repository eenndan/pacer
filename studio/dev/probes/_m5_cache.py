"""Shared load-once cache for the M5 probes (hesitation, lap x corner polish, grip re-grounding).

Each recording is a 15-24 GB GPMF scan, and the three M5 probes ask three questions of the SAME
per-(lap x corner) numbers. So a recording is loaded ONCE through the real `Session.load` path,
everything the three probes read is packed into one pickle under `$TMPDIR`, and each probe reads
that.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes._m5_cache          # all four
    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes._m5_cache MK_18_09 # just one

WHAT IT STORES, AND WHY EACH PIECE IS HERE
  * The per-(lap x corner) matrix the app itself publishes — `CornerModel.lap_corner_stats` (time,
    apex/entry/exit speed), `lap_corner_resolved` (was the window matched on track, or interpolated
    between neighbours — an interpolated cell's time is a ~0.22 s instrument, see
    `stats.CornerMatrix`), and `DrivingChannels.lap_corner_grip`. Read through the app's accessors,
    never re-implemented: a probe that re-derives the rule under test carries a copy of its defects.
  * The per-lap 10 Hz columns (odometer, speed, elapsed) — the hesitation probe rebuilds the
    session's own longitudinal g off them with `studio._signal.speed_long_g`, which is the series
    `driving.brake_events` runs on, and needs the samples themselves to say what a 10 Hz channel
    can and cannot resolve.
  * The app's own brake events and coasting spans per lap, plus this session's theta_b: the
    hesitation gap is measured BETWEEN those two shipped channels, so it must read them, not a
    second detector.
  * The projected corner windows on each lap's own odometer, built exactly as
    `DrivingChannels.lap_brake_points` builds them (one monotone warp per lap, `CORNER_LEAD_M`
    upstream), so a brake event is attached to a corner by the shipped rule.

SAFETY, enforced rather than promised:
  * EVERY FOOTAGE FOLDER IS READ-ONLY. Paths under ~/Desktop are only ever opened for reading, and
    every file in each folder is snapshotted (size, mtime) before and after each load; a change
    aborts the run before anything is written.
  * `Session.load` UPSERTS INTO THE OWNER'S LIBRARY, so `studio.dev._jail` diverts every
    app-support seam BEFORE any studio import resolves one, and the loader refuses to run if the
    seam still resolves to the real directory.
"""

from __future__ import annotations

import os
import pickle
import sys
import time

import numpy as np

DESKTOP = os.path.expanduser("~/Desktop")
# key -> (folder, the FIRST chapter; the rest come from chapters.discover_siblings, which is what
# the app itself opens with — without it no lap can cross a chapter seam).
RECORDINGS = {
    "Sandown3h": ("Sandown 3h 2026", "GX010064.MP4"),   # 62 laps, three chapters, clockwise
    "SD_30_08": ("SD_30_08_26", "GX010065.MP4"),        # 37 laps, clockwise
    "SD_19_09": ("SD_19_09_26", "GX010068.MP4"),        # 36 laps, clockwise
    "MK_18_09": ("MK_18_09_26", "GX010067.MP4"),        # 19 laps, a DIFFERENT track, anticlockwise
}
REAL_APP_SUPPORT = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "pacer")


def folder_state(path: str) -> dict:
    """(size, mtime_ns) per file — the read-only tripwire, taken before and after every load."""
    if not os.path.isdir(path):
        return {}
    return {n: (os.stat(os.path.join(path, n)).st_size, os.stat(os.path.join(path, n)).st_mtime_ns)
            for n in sorted(os.listdir(path)) if not n.startswith(".")}


def cache_path(key: str) -> str:
    # $TMPDIR is shared between agent sessions, so the name carries the package.
    return os.path.join(os.environ.get("TMPDIR", "/tmp"), f"pacer_m5_{key}.pkl")


def jail_app_support(prefix: str = "pacer-m5-") -> str:
    """Divert every app-support seam and REFUSE to go on if the diversion did not take."""
    from studio.dev import _jail

    jail = _jail.divert_app_support(prefix)
    from studio import library
    if os.path.abspath(library._app_support_dir()) == os.path.abspath(REAL_APP_SUPPORT):
        raise SystemExit("the app-support seam still resolves to the real library — refusing to run")
    return jail.dir


def _extract(session, name: str) -> dict:
    """Every number the three M5 probes read, through the app's own accessors."""
    from studio import corners as corners_alg
    from studio import driving

    basis = session.corners.basis()
    if not basis or not basis[0]:
        raise SystemExit(f"{name}: no corner basis — nothing per-corner to measure")
    corner_list, total_ref = basis
    n_c = len(corner_list)
    th = session.driving.thresholds()
    lap_ids = session.consistency_lap_ids()
    stint_of = {lid: s.index for s in session.stats.stints() for lid in s.lap_ids}

    rows: dict[str, list] = {k: [] for k in
                             ("lap_id", "lap_time", "stint", "t_start", "time", "apex_v", "entry_v",
                              "exit_v", "resolved", "grip", "win_enter", "win_exit", "seg",
                              "dist", "speed", "elapsed", "brakes", "coasts")}
    interior = [b for c in corner_list for b in (c.enter, c.exit)]
    for lid in lap_ids:
        arr = session._lap_arrays(lid)
        if arr is None:
            continue
        dist, speed_kmh, elapsed = arr
        if len(dist) < 4 or float(dist[-1]) <= 0:
            continue
        st = session.corners.lap_corner_stats(lid)
        res = session.corners.lap_corner_resolved(lid)
        grip = session.driving.lap_corner_grip(lid)
        if len(st) != n_c or len(res) != n_c:
            continue
        total_lap = float(dist[-1])
        align = session.corners.lap_alignment(lid, total_lap)
        proj = corners_alg.project_boundaries(
            interior, total_ref, total_lap,
            traces=session.driving._corner_traces(lid), alignment=align)
        rows["win_enter"].append([float(proj[2 * i]) for i in range(n_c)])
        rows["win_exit"].append([float(proj[2 * i + 1]) for i in range(n_c)])
        rows["seg"].append(corners_alg.segment_times(corner_list, total_ref, dist, elapsed,
                                                     alignment=align))
        rows["lap_id"].append(int(lid))
        rows["lap_time"].append(float(session.lap_time(lid)))
        rows["stint"].append(int(stint_of.get(lid, 0)))
        rows["t_start"].append(float(session._lap_columns(lid)[0][0]))
        rows["time"].append([float(s.time) for s in st])
        rows["apex_v"].append([float(s.apex_speed) for s in st])
        rows["entry_v"].append([float(s.entry_speed) for s in st])
        rows["exit_v"].append([float(s.exit_speed) for s in st])
        rows["resolved"].append([bool(b) for b in res])
        rows["grip"].append([float(g) for g in grip] if len(grip) == n_c else [np.nan] * n_c)
        rows["dist"].append(np.asarray(dist, float))
        rows["speed"].append(np.asarray(speed_kmh, float))
        rows["elapsed"].append(np.asarray(elapsed, float))
        rows["brakes"].append([(e.onset_dist, e.onset_time, e.peak_decel, e.duration)
                               for e in session.driving.lap_brake_events(lid)])
        rows["coasts"].append([(c.start_dist, c.end_dist, c.duration)
                               for c in session.driving.lap_coasting_spans(lid)])

    # Corner identity ACROSS recordings of one track is by apex position on the ground, never by
    # label: two sessions of the same track need not detect the same number of corners.
    import pacer
    apex_latlon = []
    for _label, x, y, _d in session.corners.corner_map_markers():
        g = session.cs.global_(pacer.Vec3f(float(x), float(y), 0.0))
        apex_latlon.append((float(g.lat), float(g.lon)))

    out = {
        "name": name,
        "n_corners": n_c,
        "apex_latlon": np.asarray(apex_latlon, float),
        "cid": np.asarray([c.cid for c in corner_list], int),
        "corner_enter": np.asarray([c.enter for c in corner_list], float),
        "corner_exit": np.asarray([c.exit for c in corner_list], float),
        "corner_apex": np.asarray([c.apex for c in corner_list], float),
        "corner_dir": np.asarray([c.direction for c in corner_list], int),
        "corner_turn_deg": np.asarray([c.turn_deg for c in corner_list], float),
        "total_ref": float(total_ref),
        "theta_b": float(th.theta_b) if th is not None else float("nan"),
        "brake_p90": float(th.brake_p90) if th is not None else float("nan"),
        "grip_envelope": float(session.driving._grip_envelope()),
        "coast_window_s": float(driving.COAST_SMOOTH_S),
        "min_coast_s": float(driving.MIN_COAST_S),
        "corner_lead_m": float(driving.CORNER_LEAD_M),
        "brake_match_lead_m": float(driving.BRAKE_MATCH_LEAD_M),
        "valid_laps": len(session.valid_lap_ids()),
        "best_lap_id": session.best_lap_id(),
    }
    for k in ("lap_id", "lap_time", "stint", "t_start"):
        out[k] = np.asarray(rows[k], float if k in ("lap_time", "t_start") else int)
    for k in ("time", "apex_v", "entry_v", "exit_v", "grip"):
        out[k] = np.asarray(rows[k], float)
    out["resolved"] = np.asarray(rows["resolved"], bool)
    out["win_enter"] = np.asarray(rows["win_enter"], float)
    out["win_exit"] = np.asarray(rows["win_exit"], float)
    out["seg"] = np.asarray(rows["seg"], float)
    for k in ("dist", "speed", "elapsed", "brakes", "coasts"):
        out[k] = rows[k]
    return out


def build(key: str) -> dict | None:
    """Load one recording through the real pipeline and write its M5 cache."""
    from studio import chapters
    from studio.session import Session

    folder, first = RECORDINGS[key]
    root = os.path.join(DESKTOP, folder)
    head = os.path.join(root, first)
    if not os.path.isfile(head):
        print(f"{key}: footage missing at {head} — skipped")
        return None
    paths = chapters.discover_siblings(head)          # without this no lap crosses a chapter seam
    before = folder_state(root)
    t0 = time.time()
    session = Session.load(paths)
    restored = session.restore_saved_timing_lines()
    data = _extract(session, key)
    after = folder_state(root)
    if after != before:
        moved = [n for n in set(before) | set(after) if before.get(n) != after.get(n)]
        raise SystemExit(f"REFUSING TO CONTINUE: {root} changed during the load ({moved}).")
    data["paths"] = paths
    data["restored_saved_lines"] = restored
    print(f"{key}: {folder}/{', '.join(os.path.basename(p) for p in paths)} loaded in "
          f"{time.time() - t0:.0f} s; saved lines restored: {restored}; "
          f"{len(data['lap_id'])} clean laps (of {data['valid_laps']} valid) x {data['n_corners']} "
          f"corners; theta_b={data['theta_b']:.3f} g; grip envelope={data['grip_envelope']:.3f} g; "
          f"{len(before)} files in {folder} unchanged (size + mtime)")
    with open(cache_path(key), "wb") as f:
        pickle.dump(data, f)
    print(f"{key}: wrote {cache_path(key)} ({os.path.getsize(cache_path(key)) / 1e6:.1f} MB)")
    return data


def load(key: str) -> dict | None:
    """The cached recording, building it (a full Session.load) if it is not there yet."""
    p = cache_path(key)
    if os.path.exists(p):
        with open(p, "rb") as f:
            return pickle.load(f)
    return build(key)


def present_keys() -> list[str]:
    return [k for k, (folder, first) in RECORDINGS.items()
            if os.path.isfile(os.path.join(DESKTOP, folder, first))]


def main() -> None:
    real_before = folder_state(REAL_APP_SUPPORT)
    print(f"app-support seams diverted to {jail_app_support()}")
    for key in sys.argv[1:] or list(RECORDINGS):
        build(key)
    if folder_state(REAL_APP_SUPPORT) != real_before:
        raise SystemExit("THE REAL APP-SUPPORT DIRECTORY CHANGED during this run — stop and look.")
    print("real app-support directory unchanged (size + mtime)")


if __name__ == "__main__":
    main()
