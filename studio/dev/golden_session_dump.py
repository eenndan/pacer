"""Whole-public-API numerical fingerprint of a real Session — the equivalence gate for the
F1 god-object decomposition.

Loads the real D24 recording (library redirected to a temp dir so nothing touches the user's
app-support), then dumps a DENSE fingerprint (thousands of float/int values) of EVERY public
analysis method the refactor might touch, across a representative sweep of laps + modes + a
distance/time grid. THREE phases are captured into one JSON so cache-invalidation behaviour is
fingerprinted too:
  * "base"   — the freshly-loaded session;
  * "reseg"  — after set_timing_lines(current lines) (a no-op-geometry re-segmentation, which
               still clears + recomputes every per-lap cache — proves invalidate() clears
               exactly what the old hand-clearing did);
  * "ref"    — after set_reference_session(self) (a SELF reference: same track, valid best lap,
               so it is adopted) — exercises the reference path everywhere a delta is drawn;
  * "ref_cleared" — after clear_reference() (must revert byte-for-byte to "base").

Run BEFORE refactoring to write golden_session.json, then AFTER to write a candidate and diff
(via studio.dev.golden_compare). This was the F1 god-object-decomposition equivalence gate.
Usage:  python -m studio.dev.golden_session_dump <out.json>
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import numpy as np

# repo root is three levels up from studio/dev/<this file> (studio/dev -> studio -> root).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REAL = os.path.expanduser("~/Desktop/D24/GX010060.MP4")


def _round(v):
    """Recursively normalize a value into a JSON-safe, comparison-stable form. Floats are kept
    full precision (json dumps repr-exact for doubles); numpy scalars/arrays -> Python."""
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, np.ndarray):
        return [_round(x) for x in v.tolist()]
    if isinstance(v, (list, tuple)):
        return [_round(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _round(val) for k, val in v.items()}
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    # dataclass / object: dump its public float/int/str fields by __dict__ or known attrs.
    if hasattr(v, "__dict__"):
        return {k: _round(val) for k, val in sorted(vars(v).items())
                if not k.startswith("_")}
    return repr(v)


def fingerprint(s) -> dict:
    """Dense fingerprint of one Session STATE — every public analysis accessor, swept."""
    out: dict = {}
    laps = s.valid_lap_ids()
    out["valid_lap_ids"] = _round(laps)
    out["best_lap_id"] = _round(s.best_lap_id())
    out["lap_count"] = s.lap_count()
    out["sector_count"] = s.sector_count()
    out["point_count"] = s.point_count()
    out["lap_rows"] = _round(s.lap_rows())
    out["best_lap_total_distance"] = _round(s.best_lap_total_distance())
    out["active_baseline_total_distance"] = _round(s.active_baseline_total_distance())
    out["session_best_splits"] = _round(s.session_best_splits())
    out["theoretical_best"] = _round(s.theoretical_best())
    out["best_rolling_lap"] = _round(s.best_rolling_lap())
    out["dropout_lap_ids"] = _round(sorted(s.dropout_lap_ids()))
    out["session_date"] = _round(s.session_date())
    out["track_name"] = _round(s.track_name)
    out["has_gmeter"] = bool(s.has_gmeter)
    out["gmeter_source"] = _round(s.gmeter_source())
    out["has_reference"] = bool(s.has_reference())
    out["reference_label"] = _round(s.reference_label())
    out["reference_lap_time"] = _round(s.reference_lap_time())
    out["reference_lap_id"] = _round(s.reference_lap_id())
    out["reference_overlay_xy_shape"] = (
        list(s.reference_overlay_xy().shape) if s.reference_overlay_xy() is not None else None)
    out["driving_thresholds"] = _round(s.driving.thresholds())

    # Corners (session-wide).
    out["corners"] = _round(s.corners.corner_list())
    out["corner_session_bests"] = _round(s.corners.corner_session_bests())
    out["corner_map_markers"] = _round(s.corners.corner_map_markers())
    out["consistency_lap_ids"] = _round(s.consistency_lap_ids())
    out["lap_time_trend"] = _round(s.lap_time_trend())
    out["sector_sigmas"] = _round(s.sector_sigmas())
    out["corner_consistency"] = _round(s.corner_consistency())
    out["coaching_opportunities"] = _round(s.coaching_opportunities())

    # Per-lap sweeps. Use a representative subset of valid laps (all of them — there are ~18).
    cids = [c.cid for c in s.corners.corner_list()]
    per_lap: dict = {}
    for lid in laps:
        row: dict = {}
        row["lap_time"] = _round(s.lap_time(lid))
        row["lap_window"] = _round(s.lap_window(lid))
        row["lap_sector_splits"] = _round(s.lap_sector_splits(lid))
        row["sector_boundary_distances"] = _round(s.sector_boundary_distances(lid))
        row["lap_has_dropout"] = bool(s.lap_has_dropout(lid))
        row["lap_corner_stats"] = _round(s.corners.lap_corner_stats(lid))
        row["lap_corner_grip"] = _round(s.driving.lap_corner_grip(lid))
        row["lap_brake_events"] = _round(s.driving.lap_brake_events(lid))
        row["lap_coasting_spans"] = _round(s.driving.lap_coasting_spans(lid))
        row["lap_brake_map_markers"] = _round(s.driving.lap_brake_map_markers(lid))
        row["corner_map_markers_count"] = len(s.corners.corner_map_markers())
        # corner-entry media time per corner.
        row["corner_entry_media_time"] = _round(
            {cid: s.corners.corner_entry_media_time(lid, cid) for cid in cids})
        # brake/coast plot positions in both modes.
        for mode in ("distance", "time"):
            row[f"lap_brake_plot_positions_{mode}"] = _round(
                s.driving.lap_brake_plot_positions(lid, mode))
            row[f"lap_coasting_plot_spans_{mode}"] = _round(
                s.driving.lap_coasting_plot_spans(lid, mode))
        per_lap[str(lid)] = row
    out["per_lap"] = per_lap

    # sector_plot_positions in both modes.
    for mode in ("distance", "time"):
        out[f"sector_plot_positions_{mode}"] = _round(s.sector_plot_positions(mode))

    # The delta family on a dense grid. Pick the best lap window for time sweeps.
    best = s.best_lap_id()
    if best is not None:
        w = s.lap_window(best)
        if w is not None:
            t0, t1 = w
            grid = np.linspace(t0, t1, 200)
            out["delta_at_time"] = _round([s.delta_at_time(float(t)) for t in grid])
            out["delta_at_lap_best"] = _round([s.delta_at_lap(best, float(t)) for t in grid])
            out["g_at_time"] = _round([s.g_at_time(float(t)) for t in grid])
            out["lap_at_time"] = _round([s.lap_at_time(float(t)) for t in grid])
            out["index_at_time"] = _round([s.index_at_time(float(t)) for t in grid])
            # scrub conversions over the grid, in both modes.
            bd = s.active_baseline_total_distance()
            for mode in ("distance", "time"):
                xs_grid = np.linspace(0.0, (bd or 100.0), 100) if mode == "distance" \
                    else np.linspace(0.0, float(t1 - t0), 100)
                out[f"media_time_at_plot_x_{mode}"] = _round(
                    [s.media_time_at_plot_x(best, float(x), mode, bd) for x in xs_grid])
                out[f"plot_x_at_media_time_{mode}"] = _round(
                    [s.plot_x_at_media_time(best, float(t), mode, bd) for t in grid])
    # delta_between across several pairs.
    if len(laps) >= 2:
        pairs = [(laps[0], laps[-1]), (laps[1], laps[0]), (best, laps[2])]
        db = {}
        for a, b in pairs:
            wa = s.lap_window(a)
            if wa is None:
                continue
            ta = np.linspace(wa[0], wa[1], 50)
            db[f"{a}->{b}"] = _round([s.delta_between(a, b, float(t)) for t in ta])
        out["delta_between"] = db

    # delta() output for a subset of lap selections, both modes.
    sel = laps[: min(4, len(laps))]
    delta_out = {}
    for mode in ("distance", "time"):
        res = s.delta(sel, mode)
        if res is None:
            delta_out[mode] = None
            continue
        bid, speed, delta = res
        delta_out[mode] = {
            "best": bid,
            "speed": {str(k): _round(v) for k, v in speed.items()},
            "delta": {str(k): _round(v) for k, v in delta.items()},
        }
    out["delta"] = delta_out

    # lap_channels for the best lap (export path).
    if best is not None:
        ch = s.lap_channels(best)
        out["lap_channels_best"] = {k: _round(v) for k, v in sorted(ch.items())}

    # reference-specific accessors (active only in the ref phase; harmless dumps otherwise).
    if s.has_reference():
        w = s.lap_window(best) if best is not None else None
        if w is not None:
            grid = np.linspace(w[0], w[1], 80)
            out["reference_delta_vs_lap"] = _round(
                [s.reference_delta_vs_lap(best, float(t)) for t in grid])
            out["reference_overlay_index_at_progress"] = _round(
                [s.reference_overlay_index_at_progress(float(t)) for t in grid])
    return out


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/claude/pacer-review/golden_session.json"
    if not os.path.exists(REAL):
        print(f"FATAL: real session not found at {REAL}", file=sys.stderr)
        sys.exit(2)

    # Redirect the library app-support dir to a temp dir so nothing touches the user's data.
    import studio.library as library
    tmp = tempfile.mkdtemp(prefix="pacer-golden-")
    library._app_support_dir = lambda: tmp  # type: ignore[attr-defined]

    from studio.session import Session
    s = Session.load([REAL])

    result: dict = {}
    result["base"] = fingerprint(s)

    # Re-segmentation with the CURRENT lines (geometry unchanged, but every per-lap cache is
    # cleared + recomputed) — proves invalidate() clears exactly what the old hand-clearing did.
    s.set_timing_lines(s.start_line, s.sector_lines)
    result["reseg"] = fingerprint(s)

    # SELF reference: same track + a valid best lap, so it is adopted. Exercises every delta path
    # with a reference loaded.
    reason = s.set_reference_session(s, source_label="self-ref")
    result["ref_set_reason"] = reason
    result["ref"] = fingerprint(s)

    # Clear -> must revert to the dormant state, byte-identical to "base" minus session-identity.
    s.clear_reference()
    result["ref_cleared"] = fingerprint(s)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(result, f, sort_keys=True)
    print(f"wrote {out_path}")
    # quick stats: count the leaf float values.
    def count(o):
        if isinstance(o, dict):
            return sum(count(v) for v in o.values())
        if isinstance(o, list):
            return sum(count(v) for v in o)
        return 1
    print(f"leaf values: {count(result)}")


if __name__ == "__main__":
    main()
