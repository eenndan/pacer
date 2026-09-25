"""Whole-public-API numerical fingerprint of a real Session — the equivalence gate for the
F1 god-object decomposition.

Loads one real recording — `PACER_GOLDEN_MP4`, by default chapter 1 of the working set's
`MK_18_09_26` (see REAL below) — with the `library` AND `track_db` app-support seams redirected to a
temp dir, so nothing touches the user's app-support and the fingerprint cannot depend on which tracks
they happen to have saved; then dumps a DENSE fingerprint (thousands of float/int values) of EVERY
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
        The RECORDING to dump is REAL / $PACER_GOLDEN_MP4 — never a CLI argument. It must be a
        real MP4: the tool refuses (exit 2) a path that is missing, not an MP4 container, or
        unreadable by the GPMF parser, rather than fingerprinting whatever it can open.
        No PYTHONPATH is needed — the module puts the built bindings on `sys.path` itself — and a
        refusal names only what it measured (see `preflight`).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import numpy as np

# repo root is three levels up from studio/dev/<this file> (studio/dev -> studio -> root).
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
# …and the BUILT BINDINGS, so the workflow AGENTS.md documents works as written.
#
# That workflow is `pixi run python -m studio.dev.golden_session_dump <out.json>` with no
# PYTHONPATH, and from the repo root it used to resolve `pacer` to the C++ SOURCE directory:
# `pacer/` has no `__init__.py`, and neither does the `site-packages/pacer/` the build deploys the
# compiled module into, so both are PEP 420 namespace PORTIONS — `import pacer` then succeeds and
# has no `GPMFSource`. `bindings/pacer/pacer/` is a REGULAR package, and a regular package beats
# any number of namespace portions, so putting it on the path settles the resolution in every
# install state. Inserted immediately after the repo root: exactly where `PYTHONPATH=bindings/pacer`
# would put it, which is the same fix the `smoke` pixi task applies via its env.
sys.path.insert(1, os.path.join(_ROOT, "bindings", "pacer"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# The gate's recording: PACER_GOLDEN_MP4, else `studio.dev.footage.RECORDING_DEFAULT` — since G2
# (2026-09-23) chapter 1 of the working set's MK_18_09_26, the recording on D24's own circuit, so a
# dump covers the corners and braking zones the D24 gate covered (the reason is beside the default).
# Overridable, because the default path is only a convention — and a file that EXISTS while failing
# to parse used to surface as a bare "Failed to open file". It is READ, never written: it is the
# owner's footage, and nothing about it comes from this tool's command line.
#
# WHY THAT LAST CLAUSE IS LOAD-BEARING. The D24-era default was chapter **2**, GX020060.MP4, because
# GX010060.MP4 — the obvious default, and the one this line once carried — is the file this tool's
# own CLI destroyed, writing a JSON dump over 11.9 GB of the owner's footage (its argument is the
# OUTPUT; see the usage note above). It went on parsing as a GoPro name while holding 2.4 MB of
# JSON, so every real-D24 run that did not set PACER_GOLDEN_MP4 fingerprinted nothing at all.
# D24 left the dev machine on 2026-09-19; `_resolve_out_path` still refuses any output that is not
# a new `.json`, whatever the recording is.
# Point PACER_GOLDEN_MP4 at any real recording; both sides of a comparison just have to use the
# same one (the fingerprint is recording-specific — a before/after pair taken on DIFFERENT
# recordings compares nothing). The variable and the default live in `studio.dev.footage`, which
# the real-footage checks in tests/ read too: one variable points all of them at a recording.
# A default that is not on the machine running the dump is refused by `preflight`, by name.
from studio import stats as stats_service  # noqa: E402
from studio import units  # noqa: E402
from studio.dev import footage  # noqa: E402

REAL = footage.recording_path()


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


def _sampled(a, n: int = 51):
    """`n` evenly spaced values of a long per-sample series, BOTH ENDS KEPT (the end of a Δ curve
    is its headline number). For the curves added by B1b only: their 400-point grids would add
    ~10k leaves a phase for a shape that 51 points already pin — every interior sample of a
    running total moves when any segment before it does."""
    if a is None:
        return None
    a = np.asarray(a, float)
    if a.ndim != 1 or len(a) <= n:
        return _round(a)
    return _round(a[np.linspace(0, len(a) - 1, n).round().astype(int)])


def _curve(xy):
    """An (x, dy) pair — `ideal_delta_to_best`'s shape and each value of `delta_to_ideal`."""
    return None if xy is None else {"x": _sampled(xy[0]), "dy": _sampled(xy[1])}


def _provenance(p):
    """The numbers a provenance panel states: the value, its re-derivation, and the window and fix
    quality it was measured over. Its tables (the lap's own fixes — `lap_channels_best` carries
    them) and its prose (steps, notes) are left out, so a copy edit is not a golden diff."""
    if p is None:
        return None
    return _round({"formatted": p.formatted, "value": p.value, "reconstructed": p.reconstructed,
                   "reconstructed_formatted": p.reconstructed_formatted, "n": p.n,
                   "window": p.window, "quality": p.quality})


def _quality_strip(tl):
    """The quality strip as the scrub bar paints it: each run of one class as [first cell, class],
    plus the per-class counts. Lossless for the painted classes at ~1 % of the per-cell arrays."""
    cls = np.asarray(tl.cls)
    starts = np.flatnonzero(np.r_[True, cls[1:] != cls[:-1]]) if len(cls) else []
    return {"cells": len(cls), "cell_s": _round(tl.cell_s), "reports_quality": bool(tl.reports_quality),
            "counts": {str(k): int(v) for k, v in sorted(tl.counts().items())},
            "runs": [[int(i), int(cls[i])] for i in starts]}


def _split_matrix(s):
    """The Stats page's SPLITS grid, composed exactly as `StatsPanel._split_matrix` composes it
    (a staticmethod in a Qt module, so it is re-stated here rather than imported: this dump stays
    headless). The math and the marks are `stats.split_matrix`'s."""
    n_cols = s.effective_sector_count() + 1
    if n_cols < 2:
        return None
    ids = s.consistency_lap_ids()
    return stats_service.split_matrix(ids, [s.lap_sector_splits(i) for i in ids], columns=n_cols)


def fingerprint(s, *, strict: bool = True) -> dict:
    """Dense fingerprint of one Session STATE — every public analysis accessor, swept.

    strict=True (default, the real-footage gate): every accessor is called directly; any exception
    propagates — behaviour is byte-identical to the original single-flow dump.

    strict=False (the CI synthetic gate): each accessor is guarded so an accessor a *bare*
    synthetic Session cannot serve (no pacer `laps` object -> the pacer-passthrough accessors
    raise AttributeError) records the `_UNSUPPORTED` sentinel instead of aborting the dump. The
    Python Session-math the equivalence gate protects (real corner detection / driving channels /
    delta / bests / consistency, all seeded on the synthetic session) is still fingerprinted in
    full; only the C++ Laps passthroughs (lap_count, sector geometry, session_date, ...) fall to
    the sentinel. This never runs on the real-footage path, so the real fingerprint stays
    byte-identical."""
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
    # The Stats page's CORNERS table (Best / Median / σ / Med loss / apex / grip per corner). It had
    # no leaf of its own: its Best agreed with `corner_session_bests` only by construction, and its
    # Median, σ, apex and grip columns reached no leaf at all — so C4, which changes which lap ×
    # corner cells those columns count, would have moved them in silence.
    put("corner_report", lambda: _round(s.corner_report()))
    # ...and the STRAIGHTS table, for the same reason: its times, trap speeds and exit Δ are read at
    # the same corner edges, and nothing fingerprinted them either.
    put("straights_report", lambda: _round(s.straights_report()))
    put("coaching_opportunities", lambda: _round(s.coaching_opportunities()))
    # The Stats page's phase matrix had NO golden coverage until the decomposition it reads moved
    # from ∫ds/v to the lap's own clock and nothing in this dump noticed: the change showed up only
    # through `coaching_opportunities.phases`, because the same numbers reach a second, wider
    # surface through here. A user-facing number with no fingerprint is a number that can move in
    # silence, so this one is fingerprinted too.
    put("phase_report", lambda: _round(s.phase_report()))
    # The Stats page's COASTING table (F5): per place, the coast seconds per lap and whether the
    # laps separate it from the leader — a user-facing ranking, so it is fingerprinted from birth.
    put("coast_report", lambda: _round(s.coast_report()))
    # The Stats page's CORNERS BY LAP grid. Its cells are `lap_corner_stats` times (fingerprinted
    # per lap below), but the typical, the scale and the marks are decided over the RESOLVED cells
    # only, and that resolution is a read of the lap warps' knots that no other leaf exposes.
    put("corner_matrix", lambda: _round(s.corner_matrix()))

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
        row["lap_corner_resolved"] = guard(
            lambda lid=lid: _round(s.corners.lap_corner_resolved(lid)))
        # C4: the same read one level finer — which EDGES matched. The STRAIGHTS table's trap
        # speed and exit Δ count by it, and no other leaf exposes it.
        row["lap_edge_resolved"] = guard(lambda lid=lid: _round(s.corners.lap_edge_resolved(lid)))
        row["lap_corner_grip"] = guard(lambda lid=lid: _round(s.driving.lap_corner_grip(lid)))
        row["lap_brake_events"] = guard(lambda lid=lid: _round(s.driving.lap_brake_events(lid)))
        row["lap_coasting_spans"] = guard(lambda lid=lid: _round(s.driving.lap_coasting_spans(lid)))
        row["lap_brake_map_markers"] = guard(
            lambda lid=lid: _round(s.driving.lap_brake_map_markers(lid)))
        row["corner_map_markers_count"] = guard(lambda: len(s.corners.corner_map_markers()))
        # corner-entry time per corner (telemetry, whatever the accessor's name says).
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

    _surfaces(s, out, put, guard, laps, best, cids, sel)
    return out


def _surfaces(s, out, put, guard, laps, best, cids, sel) -> None:
    """THE USER-FACING NUMBERS NO LEAF ABOVE CARRIED (B1b; board review RISK-7): every SessionStats
    tile, the ideal-lap Δ family, the BRAKING table and the coaching braking direction, the trust
    surfaces (verified, quality strip, marks, provenance panels, the g-meter and gyro cross-checks),
    the picture<->telemetry map, the focus list's window samples and the per-sample map channels.

    Called AFTER every leaf above and in loops of its own, so the leaves above are computed in the
    order they always were: adding these moves no existing leaf, and the re-cut that added them
    proved it (tests/test_golden_synthetic.py). Left out on purpose, and why:
      * numbers composed inside Qt panels — the coaching headline's total, the Stats digest, the
        chart_stats readouts, the g-meter dial's filtering: this dump is headless and Session-level,
        and each of those is a sum or format of leaves fingerprinted here (the SPLITS grid is the
        exception worth re-stating — see `_split_matrix`);
      * `library_entry` / `focus_items` / `focus_report`: keyed on the recording's paths and file
        identity, which a fingerprint must not depend on — their numbers are `focus_samples` and
        the coaching rows;
      * `lap_elevation_channel` (its docstring declines it), `lap_trace_xy` for laps other than the
        best (the best lap's x/y are in `lap_channels_best`), per-lap `lap_brake_points` (reduced
        into `brake_report`), the quality strip's per-cell inputs, and `nearest_*` (picking)."""
    st = guard(lambda: s.stats, default=None)
    if st is None:
        out["stats"] = _UNSUPPORTED
    else:
        mph = units.convert_speed(1.0, units.MPH)
        tiles = {
            "totals": st.totals, "lap_stats": st.lap_stats, "pace": st.pace,
            "pace_trend": st.pace_trend, "race_pace": st.race_pace, "stints": st.stints,
            "stint_break_s": st.stint_break_s, "stint_count": st.stint_count,
            "pace_cov": st.pace_cov, "laps_within_1pct": lambda: st.laps_within_pct(1.0),
            "longest_coast_s": st.longest_coast_s, "gg_envelope": st.gg_envelope,
            "session_vmax": st.session_vmax, "speed_bands_kmh": st.speed_bands,
            "speed_bands_mph": lambda: st.speed_bands(
                scale=mph, width=stats_service.SPEED_BAND_MPH),
            "lateral_g_bands": st.lateral_g_bands,
        }
        # Each tile its own guard: a bare session with no wall clock loses that tile, not the page.
        out["stats"] = {k: guard(lambda f=f: _round(f())) for k, f in tiles.items()}

        def cloud():
            got = st.gg_cloud()
            return None if got is None else {
                "n": len(got[0]), "lat": _sampled(got[0], 101), "long": _sampled(got[1], 101)}
        out["stats"]["gg_cloud"] = guard(cloud)

    put("brake_report", lambda: _round(s.brake_report()))
    put("coaching_brake_direction", lambda: _round(s.coaching_brake_direction()))
    put("sector_medians", lambda: _round(s.sector_medians()))
    put("effective_sector_count", lambda: s.effective_sector_count())
    put("collapsed_sector_lines", lambda: _round(s.collapsed_sector_lines()))
    put("split_matrix", lambda: _round(_split_matrix(s)))
    put("excluded_lap_rows", lambda: _round(s.excluded_lap_rows()))
    put("excluded_lap_reasons", lambda: _round(s.excluded_lap_reasons()))

    # The ideal lap beyond its total (`theoretical_best` above): the composite every ideal number
    # is a reduction of, its curve, the Δ curves drawn against it and the Stats decomposition.
    put("ideal_sample", lambda: _round(s.ideal_sample()))
    put("ideal_donor_lap_id", lambda: _round(s.ideal_donor_lap_id()))
    put("ideal_segment_bests", lambda: _round(s.ideal_segment_bests()))
    put("ideal_lap_elapsed", lambda: _sampled(s.ideal_lap_elapsed(), 101))
    put("ideal_delta_to_best", lambda: {m: _curve(s.ideal_delta_to_best(m))
                                        for m in ("distance", "time")})

    def to_ideal(mode):
        got = s.delta_to_ideal(sel, mode)
        return None if got is None else {str(k): _curve(v) for k, v in sorted(got.items())}
    put("delta_to_ideal", lambda: {m: to_ideal(m) for m in ("distance", "time")})

    def decomposition():
        sb = s.ideal_segment_bests()
        return None if sb is None or best is None else _round(sb.decomposition(best))
    put("ideal_decomposition_best", decomposition)

    # Trust: what the page says about how far to believe the numbers above.
    put("timing_verified", lambda: bool(s.timing_verified))
    put("timing_user_confirmed", lambda: bool(s.timing_user_confirmed))
    put("timing_quality", lambda: _round(s.timing_quality))
    put("gps_lag_applied_s", lambda: _round(s.gps_lag_applied_s))
    put("quality_strip", lambda: _quality_strip(s.quality_timeline))
    put("auto_marks", lambda: _round(s.auto_marks()))
    put("gmeter_long_source", lambda: _round(s.gmeter_long_source()))
    put("gmeter_axis", lambda: _round(s.gmeter_axis()))
    put("gmeter_cross", lambda: _round(s.gmeter_cross()))
    put("rotation_cross", lambda: _round(s.rotation_cross()))
    put("rotation_device", lambda: _round(s.rotation_device()))
    put("has_rotation", lambda: bool(s.has_rotation))
    put("corner_best_provenance", lambda: {
        str(c): _provenance(s.corner_best_provenance(c)) for c in cids})

    # Geometry the track DB, the sidecar and the "suggest sectors" action write.
    put("timing_lines_latlon", lambda: _round(s.timing_lines_latlon()))
    put("track_location", lambda: _round(s.track_location()))
    put("suggest_sectors", lambda: _round([[g.x1, g.y1, g.x2, g.y2] for g in s.suggest_sectors(3)]))

    def focus():
        basis = s.corners.basis()
        if basis is None:
            return None
        corner_list, total = basis
        return _round(s.focus_samples([(c.enter / total, c.exit / total) for c in corner_list]))
    put("focus_samples", focus)

    for lid in laps:
        row = out["per_lap"][str(lid)]
        row["lap_quality"] = guard(lambda lid=lid: _round(s.lap_quality(lid)))
        row["lap_time_provenance"] = guard(lambda lid=lid: _provenance(s.lap_time_provenance(lid)))

    if best is not None:
        put("sector_split_provenance_best", lambda: [
            _provenance(s.sector_split_provenance(best, k))
            for k in range(len(s.lap_sector_splits(best)))])
        put("brake_throttle_best", lambda: [_sampled(a) for a in s.driving.lap_brake_throttle(best)])
        put("grip_utilization_best", lambda: _sampled(s.driving.lap_grip_utilization(best)))
        w = guard(lambda: s.lap_window(best), default=None)
        if w is not None:
            grid = np.linspace(w[0], w[1], 200)
            # The picture<->telemetry map every seek and every burned overlay goes through.
            put("media_time", lambda: _round([s.media_time(float(t)) for t in grid]))
            put("telemetry_time", lambda: _round([s.telemetry_time(float(t)) for t in grid]))
            put("yaw_rate_at_time", lambda: _round([s.yaw_rate_at_time(float(t)) for t in grid]))
            put("delta_to_ideal_at_best", lambda: _round(
                [s.delta_to_ideal_at(best, float(t)) for t in grid]))

    if s.has_reference():
        put("reference_lap_choices", lambda: _round(s.reference_lap_choices()))
        put("reference_match_is_geometric", lambda: bool(s.reference_match_is_geometric()))
        put("reference_is_own_recording", lambda: bool(s.reference_is_own_recording()))


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


class BindingsUnavailable(Exception):
    """The compiled `pacer` bindings are not usable in THIS PROCESS.

    A fact about the run, and kept in its own exception type for one reason: it used to arrive at
    the same `except Exception` as every parser error, so an `import pacer` that had resolved to
    the repo's C++ source directory was announced as "a file some tool overwrote"."""


def gpmf_opener():
    """`pacer.GPMFSource`, or raise `BindingsUnavailable` naming what `pacer` resolved to instead.

    `import pacer` SUCCEEDING is not evidence that the bindings are there — see the sys.path note
    at the top of this module — so the attribute is what gets checked, not the import."""
    try:
        import pacer
    except Exception as exc:  # noqa: BLE001 — a half-built .so raises more than ImportError
        raise BindingsUnavailable(f"`import pacer` failed: {exc}") from exc
    opener = getattr(pacer, "GPMFSource", None)
    if opener is None:
        where = (getattr(pacer, "__file__", None)
                 or f"a namespace package spanning {list(getattr(pacer, '__path__', []))}")
        raise BindingsUnavailable(
            f"`import pacer` resolved to {where}, which has no GPMFSource")
    return opener


def preflight(path: str, *, opener_factory=gpmf_opener) -> str | None:
    """The FATAL message for `path`, or None when the gate may fingerprint it.

    PRESENT-BUT-NOT-A-RECORDING, in escalating probes, because `Session.load` no longer raises on
    one shape of it: it now SKIPS a path that was read and is not an MP4 (chapters.split_non_mp4)
    so a chaptered recording survives one destroyed chapter. That is right for the app and wrong
    for a gate — a fingerprint taken over "whatever of this recording could be opened" is not a
    fingerprint of the recording. So the gate insists on the file it was pointed at, itself, and
    says WHICH way it failed (an unreadable file is not an overwritten one).

    EVERY sentence here is held to one rule: report what was measured, never how it came to be.
    "Could not be read", "its first box header is not an ISO media box", "did not parse as GPMF"
    are observations this function made. "Some tool overwrote it" is a story about the past that
    no probe here can see — and on the machine this gate runs on, where a dev tool really did
    write a JSON dump over 11.9 GB of the owner's only race recording, it is the most alarming
    sentence the software can produce. It was printed for a missing `PYTHONPATH`.

    `opener_factory` is injected by tests/test_golden_hermetic.py so both failure modes can be
    driven without a build."""
    if not os.path.exists(path):
        named = bool(os.environ.get(footage.RECORDING_ENV, "").strip())
        return (f"FATAL: real session not found at {path} "
                + ("(PACER_GOLDEN_MP4 names it; point it at a recording that is here)" if named else
                   f"(that is the default, {footage.RECORDING_DEFAULT}; set PACER_GOLDEN_MP4 to a "
                   f"recording on this machine — the working set is listed in studio/dev/footage.py)"))
    from studio import chapters
    probe = chapters.probe_mp4(path)
    if probe == chapters.MP4_UNREADABLE:
        return (f"FATAL: {path} exists but could not be read (permissions, a directory, or a "
                f"volume that went away) — this says nothing about its contents. Fix the access "
                f"or set PACER_GOLDEN_MP4 to another recording.")
    if probe != chapters.MP4_CONTAINER:
        return (f"FATAL: {path} exists, and its first box header is not an ISO media box — so "
                f"whatever it holds, it is not video. Point PACER_GOLDEN_MP4 at a recording; on "
                f"the dev Desktop, one of the working set in studio/dev/footage.py (the default is "
                f"{footage.RECORDING_DEFAULT}).")
    try:
        open_gpmf = opener_factory()
    except BindingsUnavailable as exc:
        return (f"FATAL: the pacer bindings are not usable in this run ({exc}). That is a problem "
                f"with the ENVIRONMENT, and says nothing at all about {path}. Build them with "
                f"`pixi run build`; from a layout this module cannot work out for itself, run it "
                f"with PYTHONPATH=bindings/pacer.")
    try:  # an MP4 that the GPMF parser still refuses → say so here, not from deep in the loader
        open_gpmf(path)
    except Exception as exc:  # noqa: BLE001 — report the parser's own words, whatever they are
        return (f"FATAL: {path} is an MP4 container but did not parse as GPMF ({exc}). That is "
                f"what this read measured, not a claim about how the file came to be that way. "
                f"Point PACER_GOLDEN_MP4 at a recording this build can parse.")
    return None


def main():
    out_path = _resolve_out_path(sys.argv)
    fatal = preflight(REAL)
    if fatal:
        print(fatal, file=sys.stderr)
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
    from studio import (
        app_support,
        demo,
        focus,
        library,
        logsetup,
        marks,
        prefs,
        session_record,
        track_db,
    )
    for _mod in (demo, focus, library, logsetup, marks, prefs, session_record, track_db):
        _mod._app_support_dir = lambda: tmp  # type: ignore[attr-defined]
    os.environ[app_support.DIR_ENV] = tmp  # a patch stops at the process boundary; this does not

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
    # path (`track_name is None`, start line auto-fitted) — unless the recording is at a BUILT-IN
    # circuit (Daytona Milton Keynes, and Sandown Park since Q2), which detects even in an empty DB,
    # so there every phase already ran the detected path. The unknown path is half the loader, so
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
