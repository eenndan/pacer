"""Whole-public-API numerical fingerprint of a real Session — the equivalence gate for the
F1 god-object decomposition.

Loads the real D24 recording (the `library` AND `track_db` app-support seams redirected to a temp
dir, so nothing touches the user's app-support and the fingerprint cannot depend on which tracks
they happen to have saved), then dumps a DENSE fingerprint (thousands of float/int values) of EVERY
public analysis method the refactor might touch, across a representative sweep of laps + modes + a
distance/time grid. FIVE phases are captured into one JSON so cache-invalidation behaviour is
fingerprinted too:
  * "base"   — the freshly-loaded session;
  * "reseg"  — after set_timing_lines(current lines) (a no-op-geometry re-segmentation, which
               still clears + recomputes every per-lap cache — proves invalidate() clears
               exactly what the old hand-clearing did);
  * "ref"    — after set_reference_session(a second load of the SAME file, re-labelled as another
               recording) — exercises the reference path everywhere a delta is drawn. Same file, so
               every number is identical to the self-reference this replaced; a literal self
               reference is now refused by Session (QA-W2R-04);
  * "ref_cleared" — after clear_reference() (must revert byte-for-byte to "base");
  * "detected" — a RELOAD after seeding the (hermetic, empty) track DB from this session's own
               geometry, so the loader takes the detected-track path (stored lines adopted,
               timing_verified True) instead of the auto-fitted unknown-track path the other four
               phases exercise.

Run BEFORE refactoring to write golden_session.json, then AFTER to write a candidate and diff
(via studio.dev.golden_compare). This was the F1 god-object-decomposition equivalence gate.
Usage:  python -m studio.dev.golden_session_dump <out.json> [--force]
        The argument is the OUTPUT file (must end in .json, must not already exist).
        The RECORDING to dump is REAL / $PACER_GOLDEN_MP4 — never a CLI argument.
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

# The gate's reference recording. Overridable, because the default path is only a convention — and
# a file that EXISTS while failing to parse used to surface as a bare "Failed to open file". (The
# default path is in fact such a file: this tool's own CLI destroyed it, writing a JSON dump over
# 11.9 GB of the owner's footage. Its argument is the OUTPUT — see the usage note above.) Point
# PACER_GOLDEN_MP4 at any real recording; both sides of a comparison just have to use the same one.
REAL = os.path.expanduser(os.environ.get("PACER_GOLDEN_MP4", "~/Desktop/D24/GX010060.MP4"))


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


# Sentinel recorded for a leaf that an accessor could not serve in non-strict mode (e.g. the
# bare synthetic Session has no pacer `laps`, so the pacer-passthrough accessors raise). A
# distinct, comparison-stable string so golden_compare still catches a supported->unsupported
# regression, without one missing accessor aborting the whole dump.
_UNSUPPORTED = "__unsupported__"


def fingerprint(s, *, strict: bool = True) -> dict:
    """Dense fingerprint of one Session STATE — every public analysis accessor, swept.

    strict=True (default, the real D24 gate): every accessor is called directly; any exception
    propagates — behaviour is byte-identical to the original single-flow dump.

    strict=False (the CI synthetic gate): each accessor is guarded so an accessor a *bare*
    synthetic Session cannot serve (no pacer `laps` object -> the pacer-passthrough accessors
    raise AttributeError) records the `_UNSUPPORTED` sentinel instead of aborting the dump. The
    Python Session-math the equivalence gate protects (real corner detection / driving channels /
    delta / bests / consistency, all seeded on the synthetic session) is still fingerprinted in
    full; only the C++ Laps passthroughs (lap_count, sector geometry, session_date, ...) fall to
    the sentinel. This never runs on the D24 path, so the real fingerprint stays byte-identical."""
    out: dict = {}

    def put(key, thunk):
        """Assign out[key] from thunk(). strict: propagate. non-strict: sentinel on failure."""
        if strict:
            out[key] = thunk()
            return
        try:
            out[key] = thunk()
        except Exception:
            out[key] = _UNSUPPORTED

    def guard(thunk, default=_UNSUPPORTED):
        """Evaluate thunk() for use inside a composite leaf; sentinel/default on failure
        (non-strict only — strict re-raises so the real gate is unchanged)."""
        if strict:
            return thunk()
        try:
            return thunk()
        except Exception:
            return default

    laps = s.valid_lap_ids()
    out["valid_lap_ids"] = _round(laps)
    out["best_lap_id"] = _round(s.best_lap_id())
    put("lap_count", lambda: s.lap_count())
    put("sector_count", lambda: s.sector_count())
    put("point_count", lambda: s.point_count())
    put("lap_rows", lambda: _round(s.lap_rows()))
    put("best_lap_total_distance", lambda: _round(s.best_lap_total_distance()))
    put("active_baseline_total_distance", lambda: _round(s.active_baseline_total_distance()))
    put("session_best_splits", lambda: _round(s.session_best_splits()))
    put("theoretical_best", lambda: _round(s.theoretical_best()))
    put("best_rolling_lap", lambda: _round(s.best_rolling_lap()))
    put("dropout_lap_ids", lambda: _round(sorted(s.dropout_lap_ids())))
    put("session_date", lambda: _round(s.session_date()))
    put("track_name", lambda: _round(s.track_name))
    out["has_gmeter"] = bool(s.has_gmeter)
    put("gmeter_source", lambda: _round(s.gmeter_source()))
    out["has_reference"] = bool(s.has_reference())
    put("reference_label", lambda: _round(s.reference_label()))
    put("reference_lap_time", lambda: _round(s.reference_lap_time()))
    put("reference_lap_id", lambda: _round(s.reference_lap_id()))
    put("reference_overlay_xy_shape", lambda: (
        list(s.reference_overlay_xy().shape) if s.reference_overlay_xy() is not None else None))
    put("driving_thresholds", lambda: _round(s.driving.thresholds()))

    # Corners (session-wide).
    put("corners", lambda: _round(s.corners.corner_list()))
    put("corner_session_bests", lambda: _round(s.corners.corner_session_bests()))
    put("corner_map_markers", lambda: _round(s.corners.corner_map_markers()))
    put("consistency_lap_ids", lambda: _round(s.consistency_lap_ids()))
    put("lap_time_trend", lambda: _round(s.lap_time_trend()))
    put("sector_sigmas", lambda: _round(s.sector_sigmas()))
    put("corner_consistency", lambda: _round(s.corner_consistency()))
    put("coaching_opportunities", lambda: _round(s.coaching_opportunities()))

    # Per-lap sweeps. Use a representative subset of valid laps (all of them — there are ~18).
    cids = [c.cid for c in guard(lambda: s.corners.corner_list(), default=[])]
    per_lap: dict = {}
    for lid in laps:
        row: dict = {}
        # Every thunk binds lid=lid (guard calls it immediately in-iteration, so the binding is
        # only a defensive late-binding guard — it keeps ruff's B023 quiet).
        row["lap_time"] = guard(lambda lid=lid: _round(s.lap_time(lid)))
        row["lap_window"] = guard(lambda lid=lid: _round(s.lap_window(lid)))
        row["lap_sector_splits"] = guard(lambda lid=lid: _round(s.lap_sector_splits(lid)))
        row["sector_boundary_distances"] = guard(
            lambda lid=lid: _round(s.sector_boundary_distances(lid)))
        row["lap_has_dropout"] = guard(lambda lid=lid: bool(s.lap_has_dropout(lid)))
        row["lap_corner_stats"] = guard(lambda lid=lid: _round(s.corners.lap_corner_stats(lid)))
        row["lap_corner_grip"] = guard(lambda lid=lid: _round(s.driving.lap_corner_grip(lid)))
        row["lap_brake_events"] = guard(lambda lid=lid: _round(s.driving.lap_brake_events(lid)))
        row["lap_coasting_spans"] = guard(lambda lid=lid: _round(s.driving.lap_coasting_spans(lid)))
        row["lap_brake_map_markers"] = guard(
            lambda lid=lid: _round(s.driving.lap_brake_map_markers(lid)))
        row["corner_map_markers_count"] = guard(lambda: len(s.corners.corner_map_markers()))
        # corner-entry media time per corner.
        row["corner_entry_media_time"] = _round(
            {cid: guard(lambda lid=lid, cid=cid: s.corners.corner_entry_media_time(lid, cid))
             for cid in cids})
        # brake/coast plot positions in both modes.
        for mode in ("distance", "time"):
            row[f"lap_brake_plot_positions_{mode}"] = guard(
                lambda lid=lid, mode=mode: _round(s.driving.lap_brake_plot_positions(lid, mode)))
            row[f"lap_coasting_plot_spans_{mode}"] = guard(
                lambda lid=lid, mode=mode: _round(s.driving.lap_coasting_plot_spans(lid, mode)))
        per_lap[str(lid)] = row
    out["per_lap"] = per_lap

    # sector_plot_positions in both modes.
    for mode in ("distance", "time"):
        put(f"sector_plot_positions_{mode}", lambda mode=mode: _round(s.sector_plot_positions(mode)))

    # The delta family on a dense grid. Pick the best lap window for time sweeps.
    best = s.best_lap_id()
    if best is not None:
        w = guard(lambda: s.lap_window(best), default=None)
        if w is not None:
            t0, t1 = w
            grid = np.linspace(t0, t1, 200)
            put("delta_at_time", lambda: _round([s.delta_at_time(float(t)) for t in grid]))
            put("delta_at_lap_best", lambda: _round([s.delta_at_lap(best, float(t)) for t in grid]))
            put("g_at_time", lambda: _round([s.g_at_time(float(t)) for t in grid]))
            put("lap_at_time", lambda: _round([s.lap_at_time(float(t)) for t in grid]))
            put("index_at_time", lambda: _round([s.index_at_time(float(t)) for t in grid]))
            # scrub conversions over the grid, in both modes.
            bd = guard(lambda: s.active_baseline_total_distance(), default=None)
            for mode in ("distance", "time"):
                xs_grid = np.linspace(0.0, (bd or 100.0), 100) if mode == "distance" \
                    else np.linspace(0.0, float(t1 - t0), 100)
                put(f"media_time_at_plot_x_{mode}", lambda mode=mode, xs_grid=xs_grid: _round(
                    [s.media_time_at_plot_x(best, float(x), mode, bd) for x in xs_grid]))
                put(f"plot_x_at_media_time_{mode}", lambda mode=mode: _round(
                    [s.plot_x_at_media_time(best, float(t), mode, bd) for t in grid]))
    # delta_between across several pairs (needs >=3 valid laps for the (best, laps[2]) pair).
    if len(laps) >= 3:
        pairs = [(laps[0], laps[-1]), (laps[1], laps[0]), (best, laps[2])]
        db = {}
        for a, b in pairs:
            wa = guard(lambda a=a: s.lap_window(a), default=None)
            if wa is None:
                continue
            ta = np.linspace(wa[0], wa[1], 50)
            db[f"{a}->{b}"] = guard(
                lambda a=a, b=b, ta=ta: _round([s.delta_between(a, b, float(t)) for t in ta]))
        out["delta_between"] = db
    elif len(laps) >= 2:
        # Two-lap sweep (the synthetic session): the one cross-lap pair the window math supports.
        a, b = laps[0], laps[1]
        wa = guard(lambda: s.lap_window(a), default=None)
        db = {}
        if wa is not None:
            ta = np.linspace(wa[0], wa[1], 50)
            db[f"{a}->{b}"] = guard(lambda: _round([s.delta_between(a, b, float(t)) for t in ta]))
        else:
            # No lap window (bare synthetic) — sweep delta_between on the seeded lap clocks so the
            # cross-lap Δ math (the F2 delta-engine) is still fingerprinted deterministically.
            ta = guard(lambda: np.linspace(*_lap_clock_span(s, a), 50), default=None)
            if ta is not None:
                db[f"{a}->{b}"] = _round([guard(lambda t=t: s.delta_between(a, b, float(t)))
                                          for t in ta])
        out["delta_between"] = db

    # delta() output for a subset of lap selections, both modes.
    sel = laps[: min(4, len(laps))]
    delta_out = {}
    for mode in ("distance", "time"):
        res = guard(lambda mode=mode: s.delta(sel, mode), default=_UNSUPPORTED)
        if res is None or res == _UNSUPPORTED:
            delta_out[mode] = None if res is None else _UNSUPPORTED
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
        put("lap_channels_best",
            lambda: {k: _round(v) for k, v in sorted(s.lap_channels(best).items())})

    # reference-specific accessors (active only in the ref phase; harmless dumps otherwise).
    if s.has_reference():
        w = guard(lambda: s.lap_window(best), default=None) if best is not None else None
        if w is not None:
            grid = np.linspace(w[0], w[1], 80)
            put("reference_delta_vs_lap", lambda: _round(
                [s.reference_delta_vs_lap(best, float(t)) for t in grid]))
            put("reference_overlay_index_at_progress", lambda: _round(
                [s.reference_overlay_index_at_progress(float(t)) for t in grid]))
    return out


def _lap_clock_span(s, lap_id):
    """(t0, t1) media-clock span of a seeded lap from `_dist_cache` — used only by the non-strict
    synthetic path to sweep delta_between when there is no pacer `laps` to give a lap_window."""
    times = s._dist_cache[lap_id][0]
    return float(times[0]), float(times[-1])


def _resolve_out_path(argv: list[str]) -> str:
    """The dump's DESTINATION, validated before a single byte is written.

    This tool's first positional argument is the OUTPUT path, which reads like an INPUT to anyone
    who doesn't check (the recording it dumps comes from REAL / PACER_GOLDEN_MP4, not the CLI).
    That misreading already cost an 11.9 GB recording on a dev machine: the file was passed here
    positionally and silently overwritten with JSON. So the destination now has to earn the write:
    it must be a .json path and it must not already exist (--force to replace a previous dump).
    A dev tool that can clobber an arbitrary path on a typo has no business existing."""
    args = [a for a in argv[1:] if a != "--force"]
    force = "--force" in argv[1:]
    out_path = args[0] if args else "/tmp/claude/pacer-review/golden_session.json"
    if not out_path.endswith(".json"):
        print(f"FATAL: refusing to write the dump to {out_path!r} — the output path must end in "
              ".json (this argument is the DESTINATION, not the recording to dump; the recording "
              "comes from PACER_GOLDEN_MP4).", file=sys.stderr)
        sys.exit(2)
    if os.path.exists(out_path) and not force:
        print(f"FATAL: {out_path} already exists — refusing to overwrite it. Pass --force to "
              "replace a previous dump, or choose a new path.", file=sys.stderr)
        sys.exit(2)
    return out_path


def main():
    out_path = _resolve_out_path(sys.argv)
    if not os.path.exists(REAL):
        print(f"FATAL: real session not found at {REAL} "
              "(set PACER_GOLDEN_MP4 to another recording)", file=sys.stderr)
        sys.exit(2)
    try:  # present-but-unparseable → say so here, don't raise from deep inside the loader
        import pacer
        pacer.GPMFSource(REAL)
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: {REAL} exists but is not readable as GPMF ({exc}) — a partial copy, or a "
              "file some tool overwrote. Set PACER_GOLDEN_MP4 to a complete recording.",
              file=sys.stderr)
        sys.exit(2)

    # Redirect EVERY app-support seam to a temp dir. `library` so nothing touches the user's data —
    # and `track_db` for a second reason that matters more to a gate: `Session.load` resolves
    # `track_name` through `tracks.detect_track` -> `track_db.detect`, which reads the user's live
    # `tracks.json`. Leaving it live makes the fingerprint depend on machine state. A saved track
    # sets `track_name`, the loader adopts the stored line instead of auto-fitting one, and
    # `_track_admits_reference` switches from the geometric path to the by-name path. Measured on a
    # 1.2 GB recording: two runs of IDENTICAL code disagreed on **15,655 of 35,082 leaves** (45%),
    # the only difference being that a track had been saved in between. A gate that reports a
    # 45% diff for no code change is worse than no gate — it trains you to ignore it.
    #
    # The rule is "all of them", not "the ones Session.load happens to read today": the seams are
    # cheap to redirect and the next one added would silently reintroduce this. tests/
    # test_golden_hermetic.py enforces that this list stays complete.
    tmp = tempfile.mkdtemp(prefix="pacer-golden-")
    from studio import demo, library, prefs, track_db
    for _mod in (demo, library, prefs, track_db):
        _mod._app_support_dir = lambda: tmp  # type: ignore[attr-defined]

    from studio.session import Session
    s = Session.load([REAL])

    result: dict = {}
    result["base"] = fingerprint(s)

    # Re-segmentation with the CURRENT lines (geometry unchanged, but every per-lap cache is
    # cleared + recomputed) — proves invalidate() clears exactly what the old hand-clearing did.
    s.set_timing_lines(s.start_line, s.sector_lines)
    result["reseg"] = fingerprint(s)

    # REFERENCE phase: exercises every delta path with a cross-recording reference loaded.
    #
    # The reference is a SECOND load of the same file, re-labelled as a distinct recording. It used
    # to be `set_reference_session(s)` — the session itself — which `Session` now refuses outright
    # ("a lap can't be a reference for itself"; QA-W2R-04), so that shortcut no longer reaches the
    # code this phase exists to fingerprint. A second load is the deterministic stand-in for the
    # real state — another recording of the same track, driven identically — and it is deliberately
    # THE SAME FILE so every number this phase dumps is unchanged: same pipeline, same arrays, same
    # best lap, hence a byte-identical fingerprint to the self-reference it replaces.
    ref = Session.load([REAL])
    ref.video_path = None          # a distinct recording's provenance, not this one's
    ref.chapters = None
    reason = s.set_reference_session(ref, source_label="self-ref")
    result["ref_set_reason"] = reason
    result["ref"] = fingerprint(s)

    # Clear -> must revert to the dormant state, byte-identical to "base" minus session-identity.
    s.clear_reference()
    result["ref_cleared"] = fingerprint(s)

    # DETECTED-TRACK phase. The hermetic DB above means every phase so far ran the unknown-track
    # path (`track_name is None`, start line auto-fitted). That is half the loader's behaviour, so
    # seed the empty DB from THIS session's own geometry and load again: `detect` now matches, the
    # stored lines are adopted instead of auto-fitted, and `timing_verified` is True. Seeding from
    # the recording keeps it deterministic — the entry is a function of the input file, not of the
    # machine. A failure here is reported, not raised: the phase is extra coverage, and a gate that
    # refuses to produce the other four phases because of it would be worse than one without it.
    try:
        centroid, bbox = s.track_location()
        start, sectors = s.timing_lines_latlon()
        track_db.save_track(track_db.make_entry("golden-seed", centroid, start, sectors, bbox=bbox))
        s2 = Session.load([REAL])
        result["detected_track_name"] = s2.track_name
        result["detected_timing_verified"] = bool(s2.timing_verified)
        result["detected"] = fingerprint(s2)
    except Exception as exc:  # noqa: BLE001 — coverage phase, never fatal to the gate
        result["detected_error"] = f"{type(exc).__name__}: {exc}"
        print(f"warning: detected-track phase skipped ({type(exc).__name__}: {exc})",
              file=sys.stderr)

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
