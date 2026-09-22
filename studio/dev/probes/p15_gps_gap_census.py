"""P15 — does a present recording have a GPS dropout for a gyro bridge to bridge? (backlog L2)

L2 proposed integrating gyro yaw across a GPS dropout, only across the gap and anchored at both
ends, so the map's bridge curves the way the kart turned instead of borrowing another lap's shape
(`studio/gapfill.py`). `studio/docs/gps-accuracy-research.md` T5 had already ranked it a
low-priority visual nicety with no timing effect. The package is only worth anything if two things
hold, and this probe measures both, in that order. Its verdict is `studio/docs/refused-2026-09.md`
§12.

  1. THE CENSUS. How often does a present recording have a gap, and how long is it? One real
     `Session.load` per recording, all chapters (`chapters.discover_siblings`). The loader's own
     `read_recording`, `_gate_quality` and `_clean` are WRAPPED — called through unchanged, their
     inputs and outputs counted — so every hole is attributed to where it could come from: the
     receiver not reporting (a raw hole), the quality gate rejecting fixes, or `_clean` dropping a
     glitch. The gap the map would bridge is then counted exactly as the app counts it:
     `gapfill.find_gaps` over each lap's kept-point times, the call `Session.lap_has_dropout`, the
     lap table's flag and the marks band all make.
  2. THE CEILING. If a gap did occur, how far from the true path is the bridge the app draws
     today? No present recording has one to measure, so gaps are PLANTED: fixes are deleted in
     memory from a real lap, `gapfill.reconstruct_lap` is run on what is left with the donors the
     app itself would give it (`Session._donors_for`), and the fill is scored against the deleted
     fixes. A planted gap is NOT evidence that dropouts happen; it only says how much a better
     bridge could ever win if one did. The straight chord (what the export's map inset would draw,
     since `lap_trace_xy` is not gap-filled) is scored alongside.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p15_gps_gap_census

The kept points are the SMOOTHED trace (`load._smooth_track`, a 13-fix boxcar), and a planted gap
is cut after smoothing, so the fixes either side of it were averaged with the fixes that are now
missing. A real dropout's mouths are smoothed per gap-free run instead. Over a 13-fix window this
moves a mouth by far less than the errors reported here; it is a stated bias, not a hidden one.

Everything is on the TELEMETRY clock (`Session.tt`, the lap columns). Nothing here crosses to the
media clock or reads an inertial stream, so no entry in `tests/test_media_clock.py::CROSSINGS`.

SAFETY, enforced rather than promised (as p10 enforces it):
  * EVERY FOOTAGE FOLDER IS READ-ONLY. Paths under ~/Desktop are only opened for reading, and every
    file in each folder is snapshotted (size, mtime) before and after each load.
  * `studio.dev._jail` diverts every app-support seam BEFORE any studio import that could reach
    one. The owner's `tracks.json` is COPIED into the jail (read on the real side, written only into
    the jail), so track detection and the start line are the ones the owner's own app uses.
  * The real app-support directory is snapshotted before and after the whole run and must come back
    unchanged. This probe writes no file of its own.
"""

from __future__ import annotations

import os
import shutil
import sys
import time

import numpy as np

DESKTOP = os.path.expanduser("~/Desktop")
# The owner's present recordings (common brief §1). 0067 is the one anticlockwise track.
RECORDINGS = {
    "0064": ("Sandown 3h 2026", "GX010064.MP4"),
    "0065": ("SD_30_08_26", "GX010065.MP4"),
    "0068": ("SD_19_09_26", "GX010068.MP4"),
    "0067": ("MK_18_09_26", "GX010067.MP4"),
}
_REAL_APP_SUPPORT = os.path.join(os.path.expanduser("~"), "Library", "Application Support",
                                 "pacer")

# Planted gap lengths (s). 0.5 s is the shortest a real hole must be for `find_gaps` to see it at
# all (> GAP_TIME_S = 0.35 s); 5 s is past where consumer dead-reckoning literature stops trusting a
# MEMS gyro (research doc T1). Starts are spaced PLANT_EVERY_S apart along each lap.
PLANT_S = (0.5, 1.0, 2.0, 3.0, 5.0)
PLANT_EVERY_S = 4.0
# Keep planted gaps clear of the lap's interpolated start/finish crossing points.
PLANT_MARGIN = 5


def _folder_state(path: str) -> dict:
    if not os.path.isdir(path):
        return {}
    return {n: (os.stat(os.path.join(path, n)).st_size, os.stat(os.path.join(path, n)).st_mtime_ns)
            for n in sorted(os.listdir(path)) if not n.startswith(".")}


def _instrument_load(seen: dict):
    """Wrap the three loader stages whose outputs decide which fixes survive. Each wrapper calls
    the real function with the real arguments and returns its result unchanged; it only records
    what went in and what came out. Returns an `undo` callable."""
    from studio import load

    real_read, real_gate, real_clean = load.read_recording, load._gate_quality, load._clean

    def read_recording(paths):
        out = real_read(paths)
        samples, _spans, naive = out[0], out[1], out[2]
        seen["raw_naive"] = np.asarray(naive, float)
        seen["raw_speed"] = np.array([s.full_speed for s in samples], float)
        seen["raw_ok"] = np.array([load._quality_ok(s) for s in samples], bool)
        return out

    def gate(samples, spans, naive, moving_speed=0.0):
        out = real_gate(samples, spans, naive, moving_speed=moving_speed)
        seen["gate_in"], seen["gate_out"], seen["gate_moving_frac"] = len(samples), len(out[0]), out[4]
        return out

    def clean(samples, spans, naive):
        out = real_clean(samples, spans, naive)
        seen["clean_in"], seen["clean_out"] = len(samples), len(out[0])
        seen["clean_naive"] = np.asarray(out[2], float)
        return out

    load.read_recording, load._gate_quality, load._clean = read_recording, gate, clean

    def undo():
        load.read_recording, load._gate_quality, load._clean = real_read, real_gate, real_clean
    return undo


def _runs(mask: np.ndarray) -> np.ndarray:
    """Lengths of the True runs in a boolean mask."""
    if not mask.any():
        return np.zeros(0, int)
    edges = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return np.flatnonzero(edges == -1) - np.flatnonzero(edges == 1)


def _point_to_polyline(px, py, lx, ly) -> np.ndarray:
    """Distance (m) from each point (px, py) to the polyline (lx, ly)."""
    ax, ay, bx, by = lx[:-1], ly[:-1], lx[1:], ly[1:]
    dx, dy = bx - ax, by - ay
    seg2 = np.maximum(dx * dx + dy * dy, 1e-12)
    t = np.clip(((px[:, None] - ax) * dx + (py[:, None] - ay) * dy) / seg2, 0.0, 1.0)
    cx, cy = ax + t * dx, ay + t * dy
    return np.min(np.hypot(px[:, None] - cx, py[:, None] - cy), axis=1)


def census(key: str, s, seen: dict) -> dict:
    """Part 1: every hole, where it could come from, and what the map would bridge."""
    from studio import gapfill, load

    moving = seen["raw_speed"] > load.MIN_START_SPEED
    raw_dt = np.diff(seen["raw_naive"])
    both_moving = moving[:-1] & moving[1:]
    raw_holes = raw_dt[both_moving & (raw_dt > gapfill.GAP_TIME_S)]
    rejected_moving = moving & ~seen["raw_ok"]
    rej_runs = _runs(rejected_moving)

    valid = set(s.valid_lap_ids())
    lap_gaps, lap_dts = {}, []
    for lap in range(s.lap_count()):
        t = s._lap_point_times(lap)
        lap_gaps[lap] = gapfill.find_gaps(t)
        if lap in valid and len(t) > 2:
            lap_dts.append(np.diff(t[1:-1]))  # interior fixes only: the ends are interpolated
    dts = np.concatenate(lap_dts) if lap_dts else np.zeros(0)
    trace_gaps = gapfill.find_gaps(s.tt)
    in_valid = [g for lap, gs in lap_gaps.items() if lap in valid for g in gs]
    in_other = [g for lap, gs in lap_gaps.items() if lap not in valid for g in gs]
    flagged = s.dropout_lap_ids()
    assert flagged == {lap for lap in valid if lap_gaps[lap]}, "lap-table flag disagrees"
    span = (float(s.tt[-1] - s.tt[0]) if len(s.tt) > 1 else 0.0)
    # Where each whole-trace hole sits against the lap windows: a hole no lap contains is one the
    # per-lap bridge can never be asked to fill (L2 is per lap, never across a lap boundary).
    windows = [w for w in (s.lap_window(lap) for lap in range(s.lap_count())) if w is not None]
    first, last = (min(w[0] for w in windows), max(w[1] for w in windows)) if windows else (0, 0)
    for g in trace_gaps:
        ta, tb = float(s.tt[g["i"]]), float(s.tt[g["j"]])
        inside = [lap for lap, w in enumerate(windows) if ta < w[1] and tb > w[0]]
        g["where"] = (f"inside lap(s) {inside}" if inside
                      else f"{first - tb:.0f} s before lap 0 starts" if tb <= first
                      else f"{ta - last:.0f} s after the last lap ends" if ta >= last
                      else "between laps")
        g["kmh"] = (float(s.tv[g["i"]]), float(s.tv[g["j"]]))
    return {
        "key": key, "laps": s.lap_count(), "valid": len(valid), "raw": len(seen["raw_naive"]),
        "raw_moving": int(moving.sum()), "raw_dt_med": float(np.median(raw_dt)),
        "raw_holes": raw_holes, "rejected": int((~seen["raw_ok"]).sum()),
        "rejected_moving": int(rejected_moving.sum()),
        "rej_run_max": int(rej_runs.max()) if len(rej_runs) else 0,
        "gate": (seen["gate_in"], seen["gate_out"], seen["gate_moving_frac"]),
        "clean": (seen["clean_in"], seen["clean_out"]), "kept": len(s.tt), "span": span,
        "trace_gaps": trace_gaps, "gaps_valid": in_valid, "gaps_other": in_other,
        "flagged": sorted(flagged), "dts": dts,
        "extent": (float(np.ptp(s.tx)), float(np.ptp(s.ty))),
    }


def plant(s) -> dict:
    """Part 2: cut PLANT_S holes into every valid lap and score today's bridge against the truth."""
    from studio import gapfill

    med_dt = s._median_sample_dt()
    out = {d: {"fill_max": [], "fill_rms": [], "chord_max": [], "src": {}, "worst": {}}
           for d in PLANT_S}
    for lap in s.valid_lap_ids():
        xs, ys, ts = s._lap_trace_xyt(lap)
        n = len(ts)
        if n < 4 * PLANT_MARGIN:
            continue
        donors = s._donors_for(lap)
        starts = np.arange(ts[PLANT_MARGIN], ts[n - PLANT_MARGIN] - max(PLANT_S), PLANT_EVERY_S)
        for d in PLANT_S:
            for t0 in starts:
                cut = (ts > t0) & (ts < t0 + d)
                idx = np.flatnonzero(cut)
                if len(idx) == 0 or idx[0] < PLANT_MARGIN or idx[-1] > n - 1 - PLANT_MARGIN:
                    continue
                keep = ~cut
                segs, fills = gapfill.reconstruct_lap(xs[keep], ys[keep], ts[keep], donors,
                                                      med_dt=med_dt)
                assert len(fills) == 1, f"lap {lap}: planted one gap, found {len(fills)}"
                fill = next(sg for sg in segs if not sg.measured)
                px, py = xs[idx], ys[idx]
                err = _point_to_polyline(px, py, np.asarray(fill.xs), np.asarray(fill.ys))
                a, b = idx[0] - 1, idx[-1] + 1
                chord = _point_to_polyline(px, py, xs[[a, b]], ys[[a, b]])
                rec = out[d]
                rec["fill_max"].append(float(err.max()))
                rec["fill_rms"].append(float(np.sqrt(np.mean(err ** 2))))
                rec["chord_max"].append(float(chord.max()))
                kind = fills[0]["source"].split(":")[0]
                rec["src"][kind] = rec["src"].get(kind, 0) + 1
                rec["worst"][kind] = max(rec["worst"].get(kind, 0.0), float(err.max()))
    return out


def _pct(a, q) -> float:
    return float(np.percentile(a, q)) if len(a) else float("nan")


def report(c: dict, p: dict) -> None:
    g_in, g_out, g_frac = c["gate"]
    c_in, c_out = c["clean"]
    print(f"\n=== {c['key']}: {c['laps']} laps ({c['valid']} valid), kept trace {c['span']:.0f} s")
    print(f"  raw fixes {c['raw']:,} ({c['raw_moving']:,} moving > 3 m/s), median naive step "
          f"{c['raw_dt_med']:.3f} s; raw holes > 0.35 s while moving: {len(c['raw_holes'])}"
          + (f" (max {c['raw_holes'].max():.2f} s)" if len(c['raw_holes']) else ""))
    print(f"  quality gate: {g_in:,} -> {g_out:,} ({g_in - g_out} rejected, {c['rejected_moving']} "
          f"while moving = {100 * g_frac:.3f} % of the moving trace; longest moving run "
          f"{c['rej_run_max']} fixes)")
    print(f"  _clean (lead-in/cool-down trim + spikes + off-track): {c_in:,} -> {c_out:,}; "
          f"kept trace {c['kept']:,} fixes")
    print(f"  find_gaps: whole trace {len(c['trace_gaps'])}, inside valid laps "
          f"{len(c['gaps_valid'])}, inside other laps {len(c['gaps_other'])}; "
          f"lap-table dropout flags {c['flagged'] or 'none'}")
    for g in c["trace_gaps"]:
        print(f"    trace gap {g['dt']:6.2f} s (~{g['n_missing']} fixes), {g['where']}, mouths "
              f"{g['kmh'][0]:.0f} -> {g['kmh'][1]:.0f} km/h")
    for g in c["gaps_valid"] + c["gaps_other"]:
        print(f"    LAP gap: {g['dt']:.2f} s, ~{g['n_missing']} fixes missing")
    d = c["dts"]
    if len(d):
        print(f"  interior fix step inside valid laps ({len(d):,} steps): median {np.median(d):.4f}"
              f" s, p99.9 {_pct(d, 99.9):.4f} s, max {d.max():.4f} s; steps > 0.15 s (one fix "
              f"missing): {int((d > 0.15).sum())}; > 0.25 s: {int((d > 0.25).sum())}")
    ex, ey = c["extent"]
    big = max(ex, ey)
    print(f"  track extent {ex:.0f} x {ey:.0f} m -> {big / 544:.2f} m/px across the map's 544 px "
          f"minimum width, {big / 1000:.2f} m/px across 1000 px")
    print("  PLANTED gaps (NOT evidence that gaps occur) — today's bridge vs the deleted fixes, m:")
    print("    gap   n     bridge max p50/p90/p99/max       bridge rms p50/p90   chord max p50/p90"
          "   sources")
    for dur, r in p.items():
        fm, fr, cm = r["fill_max"], r["fill_rms"], r["chord_max"]
        src = ", ".join(f"{k} {v} (worst {r['worst'][k]:.2f})" for k, v in sorted(r["src"].items()))
        print(f"    {dur:>3.1f} s {len(fm):>5}   {_pct(fm, 50):5.2f} {_pct(fm, 90):5.2f} "
              f"{_pct(fm, 99):5.2f} {max(fm) if fm else float('nan'):5.2f}      "
              f"{_pct(fr, 50):5.2f} {_pct(fr, 90):5.2f}        {_pct(cm, 50):5.2f} "
              f"{_pct(cm, 90):6.2f}   {src}")


def main() -> None:
    from studio.dev import _jail

    real_before = _folder_state(_REAL_APP_SUPPORT)
    jail = _jail.divert_app_support("pacer-p15-")
    print(f"app-support seams diverted to {jail.dir}")
    real_tracks = os.path.join(_REAL_APP_SUPPORT, "tracks.json")
    if os.path.isfile(real_tracks):
        shutil.copyfile(real_tracks, os.path.join(jail.dir, "tracks.json"))
        print("owner's tracks.json copied INTO the jail (only read on the real side)")

    from studio import chapters
    from studio.session import Session

    for key, (folder, first) in RECORDINGS.items():
        root = os.path.join(DESKTOP, folder)
        if not os.path.isfile(os.path.join(root, first)):
            print(f"\n=== {key}: SKIPPED — {root}/{first} is not present")
            continue
        before = _folder_state(root)
        paths = chapters.discover_siblings(os.path.join(root, first))
        seen: dict = {}
        undo = _instrument_load(seen)
        t0 = time.time()
        try:
            s = Session.load(paths)
        finally:
            undo()
        print(f"\n{key}: {len(paths)} chapter(s) loaded in {time.time() - t0:.0f} s "
              f"({', '.join(os.path.basename(p) for p in paths)}), track {s.track_name!r}")
        report(census(key, s, seen), plant(s))
        if _folder_state(root) != before:
            raise SystemExit(f"ABORT: {root} changed during the load")

    if _folder_state(_REAL_APP_SUPPORT) != real_before:
        raise SystemExit("ABORT: the real app-support directory changed during the run")
    print("\nreal app-support directory unchanged; every footage folder unchanged")


if __name__ == "__main__":
    sys.exit(main())
