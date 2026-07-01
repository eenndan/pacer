"""Synthetic unit tests for studio.corners — the curvature-based corner model (F-corner).

A stadium loop with KNOWN geometry (two straights + two 180-degree arcs, built analytically
by arc length) drives the detection: corner count/positions/directions must match the
construction, the threshold must come from the track's own |kappa| distribution and stay
stable under GPS-grade noise, the corner/straight partition must sum to the lap time to
1e-9, and the per-corner apex speed must equal a direct np.min over the projected window
EXACTLY. The Session wiring runs on a bare Session (tests/_synthetic seeding idiom — no
pacer Laps, no telemetry file); the CornerTable + map corner-marker overlay run offscreen
on stubs. Run:  QT_QPA_PLATFORM=offscreen python tests/test_corners.py
"""
import os
import sys
from types import SimpleNamespace

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio import corners as C  # noqa: E402
from studio._signal import _smooth  # noqa: E402

RADIUS = 30.0
STRAIGHT = 200.0
ARC = np.pi * RADIUS                  # 94.248 m per 180-degree arc
TOTAL = 2 * STRAIGHT + 2 * ARC        # 588.5 m


def stadium(ds: float = 1.5, mirror: bool = False):
    """(xs, ys, cum) of a stadium loop, parametrized EXACTLY by arc length: straight
    (0,0)->(200,0), 180-deg left arc to (200,60), straight back to (0,60), 180-deg left
    arc to the start. CCW => both corners are LEFT (kappa = +1/RADIUS); `mirror` flips to
    CW (right-handers). Corner truth: arcs span [200, 294.25] and [494.25, 588.5]."""
    s = np.arange(0.0, TOTAL, ds)
    xs = np.empty_like(s)
    ys = np.empty_like(s)
    for i, si in enumerate(s):
        if si < STRAIGHT:                                   # bottom straight, heading +x
            xs[i], ys[i] = si, 0.0
        elif si < STRAIGHT + ARC:                           # right 180-deg arc (left turn)
            th = (si - STRAIGHT) / RADIUS
            xs[i] = STRAIGHT + RADIUS * np.sin(th)
            ys[i] = RADIUS - RADIUS * np.cos(th)
        elif si < 2 * STRAIGHT + ARC:                       # top straight, heading -x
            xs[i] = STRAIGHT - (si - STRAIGHT - ARC)
            ys[i] = 2 * RADIUS
        else:                                               # left 180-deg arc (left turn)
            th = (si - 2 * STRAIGHT - ARC) / RADIUS
            xs[i] = -RADIUS * np.sin(th)
            ys[i] = RADIUS + RADIUS * np.cos(th)
    if mirror:
        ys = -ys
    return xs, ys, s.copy()


def elapsed_for(cum, speed_mps):
    """Elapsed time from a per-sample speed profile: t = cumulative integral of ds/v."""
    dt = np.diff(cum) / ((speed_mps[:-1] + speed_mps[1:]) / 2.0)
    return np.concatenate(([0.0], np.cumsum(dt)))


def speed_profile(cum, phase: float):
    """A positive, varying speed (m/s) so distance<->time is genuinely non-linear."""
    return 12.0 + 8.0 * np.sin(2 * np.pi * cum / cum[-1] + phase) ** 2


# ------------------------------------------------------------------ detection geometry
def test_stadium_detection_matches_construction():
    xs, ys, cum = stadium()
    d, k = C.pooled_curvature([(xs, ys, cum)], cum[-1])
    cs = C.detect_corners(d, k)
    assert len(cs) == 2, cs
    assert [c.cid for c in cs] == [1, 2]
    assert all(c.direction == 1 for c in cs), cs          # CCW stadium: both left
    truth = [(STRAIGHT, STRAIGHT + ARC), (2 * STRAIGHT + ARC, TOTAL)]
    for c, (t_enter, t_exit) in zip(cs, truth, strict=False):
        # Boundaries within the kappa smoothing support (the boxcar spreads the edges).
        assert abs(c.enter - t_enter) <= C.KAPPA_SMOOTH_M, (c, t_enter)
        assert abs(c.exit - t_exit) <= C.KAPPA_SMOOTH_M, (c, t_exit)
        # Apex == the constructed arc midpoint (constant kappa => weighted centroid = middle).
        assert abs(c.apex - (t_enter + t_exit) / 2) <= 3.0, (c, (t_enter + t_exit) / 2)
        assert abs(c.turn_deg - 180.0) <= 10.0, c
    print(f"ok geometry: apexes {[round(c.apex, 1) for c in cs]} "
          f"vs truth {[round((a + b) / 2, 1) for a, b in truth]}")


def test_mirrored_stadium_detects_right_handers():
    xs, ys, cum = stadium(mirror=True)
    d, k = C.pooled_curvature([(xs, ys, cum)], cum[-1])
    cs = C.detect_corners(d, k)
    assert len(cs) == 2 and all(c.direction == -1 for c in cs), cs


def test_threshold_is_between_the_modes():
    """The derived threshold must sit between the straight-line noise floor and the arc
    curvature — i.e. it really is a split of the track's own bimodal |kappa| distribution."""
    xs, ys, cum = stadium()
    d, k = C.pooled_curvature([(xs, ys, cum)], cum[-1])
    thr = C.derive_threshold(k)
    arc_kappa = 1.0 / RADIUS
    straight_kappa = float(np.median(np.abs(k[(d > 40) & (d < 160)])))  # mid-straight
    assert straight_kappa < thr < arc_kappa, (straight_kappa, thr, arc_kappa)


def _noisy_stadium(seed: int, sigma: float = 0.3):
    """The stadium with GPS-grade noise, then the load pipeline's 13-sample boxcar — the
    same smoothing the real trace gets before it ever reaches the corner model."""
    xs, ys, cum = stadium()
    rng = np.random.default_rng(seed)
    xn = _smooth(xs + rng.normal(0, sigma, len(xs)), 13)
    yn = _smooth(ys + rng.normal(0, sigma, len(ys)), 13)
    cum_n = np.concatenate(([0.0], np.cumsum(np.hypot(np.diff(xn), np.diff(yn)))))
    return xn, yn, cum_n


def test_threshold_and_corners_stable_under_noise():
    """The threshold ADAPTS to the data's own noise floor (that is the point of deriving it
    from the distribution: a noisier trace has a higher straight-mode floor), so the right
    stability claims are: (a) under noise it still sits IN THE VALLEY — above the measured
    straight floor, below the arc curvature; (b) it is stable ACROSS noise realizations;
    (c) the detected corner set is unchanged vs the clean geometry."""
    xs, ys, cum = stadium()
    d_c, k_clean = C.pooled_curvature([(xs, ys, cum)], cum[-1])
    cs_clean = C.detect_corners(d_c, k_clean)
    thresholds = []
    for seed in (42, 1042):
        xn, yn, cum_n = _noisy_stadium(seed)
        d_n, k_n = C.pooled_curvature([(xn, yn, cum_n)], cum_n[-1])
        thr_n = C.derive_threshold(k_n)
        # (a) in the valley: above the noisy straight floor, below the arc curvature
        floor = float(np.median(np.abs(k_n[(d_n > 40) & (d_n < 160)])))
        assert floor < thr_n < 1.0 / RADIUS, (floor, thr_n)
        thresholds.append(thr_n)
        # (c) identical corner set, same directions, apexes within a few metres
        cs_n = C.detect_corners(d_n, k_n)
        assert len(cs_n) == len(cs_clean) == 2, (cs_clean, cs_n)
        assert [c.direction for c in cs_n] == [c.direction for c in cs_clean]
        scale = cum[-1] / cum_n[-1]
        for a, b in zip(cs_clean, cs_n, strict=False):
            assert abs(a.apex - b.apex * scale) <= 5.0, (a.apex, b.apex * scale)
    # (b) stable across realizations (measured on the real recordings: 9% apart; allow 50%)
    ratio = thresholds[1] / thresholds[0]
    assert 1 / 1.5 < ratio < 1.5, thresholds
    print(f"ok noise: thresholds {[f'{t:.5f}' for t in thresholds]} (ratio {ratio:.2f}), "
          f"corner set unchanged")


# --------------------------------------------------------------- projection + partition
def _two_laps():
    """Lap A (the reference) + lap B (same loop, 1.3% longer line, different speed)."""
    xs, ys, cum_a = stadium()
    speed_a = speed_profile(cum_a, 0.7)
    elapsed_a = elapsed_for(cum_a, speed_a)
    cum_b = cum_a * 1.013
    speed_b = speed_profile(cum_b, 2.1)
    elapsed_b = elapsed_for(cum_b, speed_b)
    d, k = C.pooled_curvature([(xs, ys, cum_a)], cum_a[-1])
    cs = C.detect_corners(d, k)
    return cs, cum_a, speed_a, elapsed_a, cum_b, speed_b, elapsed_b


def test_partition_identity_sums_exactly():
    """Corners + straights PARTITION the lap: the segment times sum to the lap time, and
    the per-segment deltas sum to the lap delta, both to 1e-9 (telescoping interpolation)."""
    cs, cum_a, _sa, el_a, cum_b, _sb, el_b = _two_laps()
    total_ref = float(cum_a[-1])
    seg_a = C.segment_times(cs, total_ref, cum_a, el_a)
    seg_b = C.segment_times(cs, total_ref, cum_b, el_b)
    assert len(seg_a) == 2 * len(cs) + 1
    assert abs(float(seg_a.sum()) - float(el_a[-1])) < 1e-9
    assert abs(float(seg_b.sum()) - float(el_b[-1])) < 1e-9
    lap_delta = float(el_b[-1] - el_a[-1])
    assert abs(float((seg_b - seg_a).sum()) - lap_delta) < 1e-9
    print(f"ok partition: lap delta {lap_delta:+.3f} s == sum of "
          f"{len(seg_a)} segment deltas (err {abs(float((seg_b - seg_a).sum()) - lap_delta):.2e})")


def test_corner_stats_deltas_and_window_speeds():
    cs, cum_a, sp_a, el_a, cum_b, sp_b, el_b = _two_laps()
    total_ref = float(cum_a[-1])
    kmh_a, kmh_b = sp_a * 3.6, sp_b * 3.6
    ref = C.lap_corner_stats(cs, total_ref, cum_a, kmh_a, el_a)            # the best lap
    st = C.lap_corner_stats(cs, total_ref, cum_b, kmh_b, el_b, ref=ref)
    seg_a = C.segment_times(cs, total_ref, cum_a, el_a)
    seg_b = C.segment_times(cs, total_ref, cum_b, el_b)
    assert all(r.delta == 0.0 and r.apex_speed_delta == 0.0 for r in ref)
    for i, (c, s) in enumerate(zip(cs, st, strict=False)):
        # time-in-corner is the partition's own corner slice; delta telescopes from it
        assert s.time == float(seg_b[2 * i + 1])
        assert abs(s.delta - (seg_b[2 * i + 1] - seg_a[2 * i + 1])) < 1e-12
        # apex speed == direct np.min over the projected window — EXACT equality
        d0 = c.enter / total_ref * cum_b[-1]
        d1 = c.exit / total_ref * cum_b[-1]
        win = (cum_b >= d0) & (cum_b <= d1)
        assert s.apex_speed == float(np.min(kmh_b[win]))
        assert s.apex_dist == float(cum_b[win][int(np.argmin(kmh_b[win]))])
        # entry/exit speeds are the interpolated boundary values
        assert abs(s.entry_speed - float(np.interp(d0, cum_b, kmh_b))) < 1e-12
        assert abs(s.exit_speed - float(np.interp(d1, cum_b, kmh_b))) < 1e-12
    print("ok stats: apex == np.min over window (exact), deltas telescoped")


# ----------------------------------------------------- drift-gated per-corner alignment
def _scaled_odometer(cum, *, region_end: float, region_scale: float):
    """A NON-UNIFORM odometer for the SAME geometry: the per-sample step lengths in [0, region_end)
    are scaled by `region_scale` (the driver weaved through that stretch), so the total line length
    drifts but the xy points are unchanged. The spatial match can recover the true position; the
    normalized fraction cannot (the extra distance biases every downstream fraction)."""
    diffs = np.diff(cum).astype(float)
    diffs[cum[:-1] < region_end] *= region_scale
    return np.concatenate(([0.0], np.cumsum(diffs)))


def test_drift_within_bound_is_identical_to_normalized():
    """The common, well-matched case: when the line-length drift is within NORMALIZED_DRIFT_MAX,
    the gated projection is BYTE-IDENTICAL to the legacy normalized projection even when spatial
    traces are supplied (the gate is a no-op) — the validated coaching math is not destabilised."""
    xs, ys, cum_a = stadium()
    d, k = C.pooled_curvature([(xs, ys, cum_a)], cum_a[-1])
    cs = C.detect_corners(d, k)
    total_ref = float(cum_a[-1])
    interior = [v for c in cs for v in (c.enter, c.exit)]
    # 0.3% uniform drift — inside the 0.5% bound.
    cum_b = cum_a * 1.003
    assert C.line_length_drift(float(cum_b[-1]), total_ref) <= C.NORMALIZED_DRIFT_MAX
    normalized = np.asarray(interior) * (cum_b[-1] / total_ref)
    traces = (xs, ys, cum_a, xs, ys, cum_b)
    gated = C.project_boundaries(interior, total_ref, float(cum_b[-1]), traces=traces)
    assert np.array_equal(gated, normalized), (gated, normalized)
    # …and with NO traces it is always the normalized projection (the pure-numpy callers' path).
    assert np.array_equal(
        C.project_boundaries(interior, total_ref, float(cum_b[-1])), normalized)
    print(f"ok drift gate no-op: drift "
          f"{C.line_length_drift(float(cum_b[-1]), total_ref):.4f} <= {C.NORMALIZED_DRIFT_MAX} "
          f"→ gated == normalized (max|Δ|={float(np.max(np.abs(gated - normalized))):.0e})")


def test_high_drift_engages_spatial_and_recovers_true_position():
    """Above the bound, the spatial fallback ENGAGES and is the more-correct number: with the drift
    packed into the bottom straight (a non-uniform odometer over the SAME xy), the normalized
    fraction biases the corner boundaries by metres while the spatial match recovers the true
    physical odometer to machine precision."""
    xs, ys, cum_a = stadium()
    d, k = C.pooled_curvature([(xs, ys, cum_a)], cum_a[-1])
    cs = C.detect_corners(d, k)
    total_ref = float(cum_a[-1])
    interior = [v for c in cs for v in (c.enter, c.exit)]
    # 6% longer line over the bottom straight only → ~2% total drift, ABOVE the bound.
    cum_b = _scaled_odometer(cum_a, region_end=STRAIGHT, region_scale=1.06)
    drift = C.line_length_drift(float(cum_b[-1]), total_ref)
    assert drift > C.NORMALIZED_DRIFT_MAX, drift
    normalized = np.asarray(interior) * (cum_b[-1] / total_ref)
    traces = (xs, ys, cum_a, xs, ys, cum_b)
    gated = C.project_boundaries(interior, total_ref, float(cum_b[-1]), traces=traces)
    # The true odometer of each reference point on lap B (same xy ⇒ same index, its cum_b value).
    idx = np.interp(interior, cum_a, np.arange(len(cum_a)))
    true = np.interp(idx, np.arange(len(cum_b)), cum_b)
    assert np.max(np.abs(gated - true)) < 1e-6, (gated, true)         # spatial == truth
    assert np.max(np.abs(gated - normalized)) > 1.0, (gated, normalized)  # ≠ the biased normalized
    print(f"ok spatial engages: drift {drift:.4f} > {C.NORMALIZED_DRIFT_MAX}; "
          f"max|gated-normalized|={float(np.max(np.abs(gated - normalized))):.2f} m, "
          f"max|gated-true|={float(np.max(np.abs(gated - true))):.1e} m")


def test_high_drift_no_spatial_match_falls_back_per_boundary():
    """Defensive: above the bound but with a comparison trace whose geometry shares NO point within
    the 3 m gate of the reference, every boundary falls back to its normalized value — never a NaN
    or None where there was a number."""
    xs, ys, cum_a = stadium()
    d, k = C.pooled_curvature([(xs, ys, cum_a)], cum_a[-1])
    cs = C.detect_corners(d, k)
    total_ref = float(cum_a[-1])
    interior = [v for c in cs for v in (c.enter, c.exit)]
    cum_b = _scaled_odometer(cum_a, region_end=STRAIGHT, region_scale=1.06)
    # Shove lap B's trace 1 km away so no point is within _SPATIAL_MATCH_MAX_M of the reference.
    far_xs, far_ys = xs + 1000.0, ys + 1000.0
    traces = (xs, ys, cum_a, far_xs, far_ys, cum_b)
    gated = C.project_boundaries(interior, total_ref, float(cum_b[-1]), traces=traces)
    normalized = np.asarray(interior) * (cum_b[-1] / total_ref)
    assert np.array_equal(gated, normalized), (gated, normalized)
    assert np.all(np.isfinite(gated))
    print("ok per-boundary fallback: no spatial match → normalized, all finite")


def test_segment_times_partition_holds_under_spatial_alignment():
    """The corner/straight partition still sums to the lap time exactly even when the spatial
    fallback (non-monotone-risk) is engaged — the lap start/end stay the literal S/F endpoints and
    the gated interior boundaries are clamped non-decreasing, so segment_times' assertion holds."""
    xs, ys, cum_a = stadium()
    d, k = C.pooled_curvature([(xs, ys, cum_a)], cum_a[-1])
    cs = C.detect_corners(d, k)
    total_ref = float(cum_a[-1])
    cum_b = _scaled_odometer(cum_a, region_end=STRAIGHT, region_scale=1.06)
    sp_b = speed_profile(cum_b, 2.1)
    el_b = elapsed_for(cum_b, sp_b)
    traces = (xs, ys, cum_a, xs, ys, cum_b)
    seg = C.segment_times(cs, total_ref, cum_b, el_b, traces)  # asserts internally
    assert abs(float(seg.sum()) - float(el_b[-1] - el_b[0])) < 1e-9
    st = C.lap_corner_stats(cs, total_ref, cum_b, sp_b * 3.6, el_b, traces=traces)
    assert len(st) == len(cs) and all(s.time > 0 for s in st)  # no negative corner times
    print("ok partition under spatial: sums to lap time, no negative corner slices")


# ------------------------------------------------------------------- Session wiring
def _bare_corner_session():
    """A bare Session (tests/_synthetic idiom) with two stadium laps seeded into the bulk
    `_cols_cache` (times, xs, ys, full_speed m/s, cum); the corner model lives in the F1
    CornerModel service now, reset (REAL detection, no seeded basis) via reset_corner_caches."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _synthetic import bare_session, reset_corner_caches

    s = bare_session(valid=[0, 1], best=0)
    s._cols_cache = {}
    reset_corner_caches(s)
    xs, ys, cum_a = stadium()
    sp_a = speed_profile(cum_a, 0.7)
    s._cols_cache[0] = (100.0 + elapsed_for(cum_a, sp_a), xs, ys, sp_a, cum_a)
    cum_b = cum_a * 1.013
    sp_b = speed_profile(cum_b, 2.1)
    s._cols_cache[1] = (300.0 + elapsed_for(cum_b, sp_b), xs, ys, sp_b, cum_b)
    return s


def test_session_accessors():
    s = _bare_corner_session()
    cs = s.corners.corner_list()
    assert len(cs) == 2 and cs is s.corners.corner_list(), "corners() must compute once and cache"
    ref = s.corners.lap_corner_stats(0)
    st = s.corners.lap_corner_stats(1)
    assert all(r.delta == 0.0 for r in ref), ref
    assert len(st) == 2 and st is s.corners.lap_corner_stats(1), "per-lap stats must cache"
    bests = s.corners.corner_session_bests()
    assert bests == [min(a.time, b.time) for a, b in zip(ref, st, strict=False)]
    markers = s.corners.corner_map_markers()
    assert len(markers) == 2
    for (label, mx, my, d), c in zip(markers, cs, strict=False):
        assert label == c.label and d == c.direction
        _t, xs, ys, _v, cum = s._cols_cache[0]
        assert abs(mx - float(np.interp(c.apex, cum, xs))) < 1e-9
        assert abs(my - float(np.interp(c.apex, cum, ys))) < 1e-9
    print(f"ok session: bests {[round(b, 2) for b in bests]}")


# ----------------------------------------------------------------------- UI (offscreen)
def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _StubSession:
    """Duck-typed stand-in for CornerTable: the corner/driving service faces it reads."""

    def __init__(self, corner_list, stats_by_lap, bests, n_laps=8):
        self._stats = stats_by_lap
        self._n = n_laps
        self.calls = 0
        # session.corners / session.driving service stand-ins (the access pattern CornerTable uses).
        self.corners = SimpleNamespace(
            corner_list=lambda: corner_list,
            lap_corner_stats=self._lap_corner_stats,
            corner_session_bests=lambda: bests,
        )
        # F5 Grip % column reads session.driving.lap_corner_grip; [] (no g) -> the cell shows a dash
        # (this corner test pins the corner metrics; grip is covered in tests/test_driving.py).
        self.driving = SimpleNamespace(lap_corner_grip=lambda lap_id: [])

    def lap_count(self):
        return self._n

    def _lap_corner_stats(self, lap_id):
        self.calls += 1
        return self._stats.get(lap_id, [])


def test_corner_table_populates_and_highlights():
    _qapp()
    from PySide6.QtGui import QColor

    from studio import theme
    from studio.lap_table import CORNER_COLUMNS, CornerTable
    cs, cum_a, sp_a, el_a, cum_b, sp_b, el_b = _two_laps()
    total_ref = float(cum_a[-1])
    ref = C.lap_corner_stats(cs, total_ref, cum_a, sp_a * 3.6, el_a)
    st = C.lap_corner_stats(cs, total_ref, cum_b, sp_b * 3.6, el_b, ref=ref)
    bests = [min(a.time, b.time) for a, b in zip(ref, st, strict=False)]
    stub = _StubSession(cs, {0: ref, 1: st}, bests)
    table = CornerTable(stub)
    table.set_lap(1)
    assert table.table.rowCount() == len(cs)
    assert table.table.columnCount() == len(CORNER_COLUMNS)
    from studio.lap_table import BEST_SECTOR_MARK
    for r, s in enumerate(st):
        assert table.table.item(r, 0).text().startswith(cs[r].label)
        # A session-best corner time carries the trailing ★ non-colour mark (matches the lap
        # table's best-split cells); a non-best time is the bare number.
        is_best = abs(s.time - bests[r]) < 1e-9
        expected_time = f"{s.time:.2f}" + (BEST_SECTOR_MARK if is_best else "")
        assert table.table.item(r, 1).text() == expected_time
        assert table.table.item(r, 2).text() == f"{s.delta:+.2f}"
        assert table.table.item(r, 3).text() == f"{s.apex_speed:.1f}"
        # best-sector-coloured + bold Time cell iff this lap holds the session best for that corner
        # (default palette: best_sector_colour() == C.best purple)
        item = table.table.item(r, 1)
        assert (item.foreground().color() == QColor(theme.best_sector_colour())) == is_best
        assert item.font().bold() == is_best
    # set_lap is a no-op when unchanged (cheap on the auto-follow path)
    n = stub.calls
    table.set_lap(1)
    assert stub.calls == n
    # range-guard: a stale lap id after a re-segment shows empty instead of raising
    table.set_lap(99)
    assert table.table.rowCount() == 0
    print("ok corner table: rows, formats, purple session-best, no-op set_lap, range guard")


def test_map_corner_markers_overlay():
    _qapp()
    import pyqtgraph as pg

    from studio.map_view import _CornerMarkers
    widget = pg.PlotWidget()  # keep the widget referenced — it owns the ViewBox
    cm = _CornerMarkers(widget.getPlotItem())
    markers = [("C1", 0.0, 0.0, 1), ("C2", 10.0, 5.0, -1), ("C3", -4.0, 8.0, 1)]
    cm.set_corners(markers)
    # one scatter group per direction present (L and R) + one text item per corner
    assert len(cm._items) == 2 + len(markers), cm._items
    texts = [it for it in cm._items if isinstance(it, pg.TextItem)]
    assert sorted(t.textItem.toPlainText() for t in texts) == ["C1", "C2", "C3"]
    cm.set_corners([])  # clears cleanly
    assert cm._items == []
    print("ok map overlay: dots per direction + a label per corner, clears cleanly")


def test_map_corner_labels_declutter_offset_and_no_overlap():
    """Corner labels are drawn OFFSET from their apex (nudged outward + clear of the start
    crosshair), and two near-coincident corners' labels do NOT land on the same spot — the
    declutter separates them rather than dropping one. Detection/apexes are untouched: this is a
    LABEL-draw offset only."""
    _qapp()
    import pyqtgraph as pg

    from studio.map_view import _CornerMarkers
    widget = pg.PlotWidget()
    widget.resize(400, 300)  # give the viewbox a real px size so the offsets are meaningful
    widget.getPlotItem().getViewBox().setRange(xRange=(-20, 20), yRange=(-20, 20), padding=0)
    cm = _CornerMarkers(widget.getPlotItem())
    # Three corners bunched at the origin — right where the start/finish crosshair sits — the exact
    # near-start cluster the audit flagged. Every label must move off its apex and off its neighbours.
    apexes = {"C1": (0.0, 0.0), "C11": (0.1, 0.0), "C12": (0.0, 0.1)}
    markers = [("C1", 0.0, 0.0, 1), ("C11", 0.1, 0.0, -1), ("C12", 0.0, 0.1, 1)]
    cm.set_corners(markers, start_xy=(0.0, 0.0))
    texts = {it.textItem.toPlainText(): it for it in cm._items if isinstance(it, pg.TextItem)}
    assert set(texts) == {"C1", "C11", "C12"}, "every corner keeps a label (none dropped)"
    positions = []
    for label, (ax, ay) in apexes.items():
        pos = texts[label].pos()
        # (a) the label is drawn OFF the raw apex point (a visible nudge, not on top of the dot).
        assert (pos.x() - ax) ** 2 + (pos.y() - ay) ** 2 > 1e-6, (label, pos)
        positions.append((round(pos.x(), 6), round(pos.y(), 6)))
    # (b) no two labels share the exact same position — the near-coincident cluster is separated.
    assert len(set(positions)) == len(positions), positions
    print(f"ok label declutter: 3 near-coincident labels offset off-apex + distinct {positions}")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nALL {len(tests)} CORNER TESTS PASSED")
