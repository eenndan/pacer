"""F3 rainbow track map — bucketization + Δ-resampling unit tests (synthetic data, offscreen).

Pure-numpy core (studio.map_view module functions):
  * `bucketize` — values → bucket ids over [lo, hi]: known mappings, hi lands in the TOP bucket
    (clamped), NaN → -1, degenerate (flat) range → the middle bucket, explicit lo/hi override.
  * `bucket_polylines` — per-bucket draw arrays: consecutive same-bucket segments share their
    joint point; NON-adjacent runs are separated by exactly one NaN (the connect='finite'
    break); -1 segments are skipped; unused buckets come back empty.
  * `resample_grid_to_points` — the 400-grid Δ resampled onto a lap's odometer == a direct
    np.interp on normalized distance (REUSE, never recompute), endpoint preserved.
  * `theme.rainbow_colors` — 16 perceptually-ordered entries anchored red (C.behind) → amber
    (C.accent) → green (C.ahead), so bucket 0 = slow/losing and bucket 15 = fast/gaining.

MapView-level (offscreen, stub session — no pacer laps, no telemetry file):
  * toggling OFF restores the EXACT pre-toggle rendering: the same item objects with the same
    pen objects (they were only hidden, never rebuilt) and the rainbow items emptied.
  * the 30 Hz tick path (set_current_lap with an unchanged lap + marker moves) performs ZERO
    bucket rebuilds; a genuine lap change rebuilds exactly once.
Run: python tests/test_rainbow_map.py
"""
import math
import os
import sys
from types import SimpleNamespace

import numpy as np
import pyqtgraph as pg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from _synthetic import bare_session  # noqa: E402

from studio import theme  # noqa: E402
from studio.map_view import (  # noqa: E402
    MapView,
    bucket_polylines,
    bucketize,
    resample_grid_to_points,
)


# ------------------------------------------------------------------ bucketize
def test_bucketize_known_values():
    """Values spread over [0, 16) with 16 buckets land in floor(v) buckets; the exact max
    CLAMPS into the top bucket (never an out-of-range id)."""
    v = [0.0, 0.5, 1.0, 7.99, 15.0, 16.0]
    got = bucketize(v, 16, lo=0.0, hi=16.0)
    assert got.tolist() == [0, 0, 1, 7, 15, 15], got
    # Default lo/hi = the data min/max: min → bucket 0, max → top bucket.
    got = bucketize([10.0, 12.0, 20.0], 4)
    assert got[0] == 0 and got[-1] == 3
    # Below-lo / above-hi inputs clamp to the extreme buckets (no -1, no overflow).
    got = bucketize([-5.0, 99.0], 8, lo=0.0, hi=10.0)
    assert got.tolist() == [0, 7], got
    print("test_bucketize_known_values OK")


def test_bucketize_nan_and_flat():
    """NaN/inf → -1 (the 'skip this segment' marker); a FLAT channel (hi <= lo) puts every
    finite value in the MIDDLE bucket — no fake red/green story without contrast."""
    got = bucketize([1.0, float("nan"), 2.0, float("inf")], 16)
    assert got[1] == -1 and got[3] == -1 and got[0] == 0 and got[2] == 15
    flat = bucketize([5.0, 5.0, float("nan")], 16)
    assert flat.tolist() == [7, 7, -1], flat  # (16-1)//2 == 7
    assert bucketize([float("nan")] * 3, 16).tolist() == [-1, -1, -1]
    print("test_bucketize_nan_and_flat OK")


def test_bucketize_monotonic_in_value():
    """Bucket id is non-decreasing in the channel value — the gradient can never invert."""
    v = np.linspace(-3.0, 11.0, 257)
    ids = bucketize(v, 16)
    assert (np.diff(ids) >= 0).all()
    assert ids[0] == 0 and ids[-1] == 15
    print("test_bucketize_monotonic_in_value OK")


# ------------------------------------------------------------- bucket_polylines
def test_bucket_polylines_runs_and_nan_breaks():
    """6 points / 5 segments with seg buckets [0, 0, 1, -1, 1]:
      * bucket 0: one run, segments 0-1 → points 0..2 inclusive, NO NaN;
      * bucket 1: two NON-adjacent runs (segments 2 and 4) → points 2..3, ONE NaN, points 4..5;
      * the -1 segment (3) is painted by nobody; every other bucket is empty."""
    xs = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    ys = xs * 10.0
    out = bucket_polylines(xs, ys, [0, 0, 1, -1, 1], n_buckets=4)
    assert len(out) == 4
    b0x, b0y = out[0]
    assert b0x.tolist() == [0.0, 1.0, 2.0] and b0y.tolist() == [0.0, 10.0, 20.0]
    assert np.isfinite(b0x).all(), "single run must carry no NaN break"
    b1x, b1y = out[1]
    # [x2, x3, NaN, x4, x5] — exactly one NaN, exactly between the two runs.
    assert len(b1x) == 5 and math.isnan(b1x[2]) and math.isnan(b1y[2])
    assert b1x[[0, 1, 3, 4]].tolist() == [2.0, 3.0, 4.0, 5.0]
    # connect='finite' semantics: the finite mask has exactly two runs of 2 points.
    finite_runs = np.flatnonzero(np.diff(np.isfinite(b1x).astype(int)) != 0)
    assert len(finite_runs) == 2, "exactly one break"
    for b in (2, 3):
        assert out[b][0].size == 0 and out[b][1].size == 0
    print("test_bucket_polylines_runs_and_nan_breaks OK")


def test_bucket_polylines_shared_joint_points():
    """Adjacent segments of DIFFERENT buckets both include their shared joint point, so the
    painted line is continuous (no 1-segment hole at every colour change)."""
    xs = np.arange(4.0)
    ys = np.zeros(4)
    out = bucket_polylines(xs, ys, [0, 1, 1], n_buckets=2)
    assert out[0][0].tolist() == [0.0, 1.0]            # segment 0 → points 0..1
    assert out[1][0].tolist() == [1.0, 2.0, 3.0]       # segments 1-2 → points 1..3
    # Point 1 appears in BOTH buckets — the joint is shared, the line unbroken.
    assert 1.0 in out[0][0] and 1.0 in out[1][0]
    print("test_bucket_polylines_shared_joint_points OK")


# ------------------------------------------------------- Δ resampling (grid → points)
def test_resample_grid_to_points_matches_direct_interp():
    """The helper must equal a DIRECT np.interp of the 400-grid onto normalized distances —
    nothing recomputed, endpoints preserved (Δ at the finish == the laptime difference)."""
    rng = np.random.default_rng(42)
    # A realistic non-uniform odometer (speeds vary) ending at ~830 m.
    steps = rng.uniform(0.4, 1.6, 900)
    cum = np.concatenate([[0.0], np.cumsum(steps)])
    grid_vals = np.cumsum(rng.normal(0.0, 0.01, 400))  # a wandering Δ curve on the 400-grid
    got = resample_grid_to_points(cum, grid_vals)
    want = np.interp(cum / cum[-1], np.linspace(0.0, 1.0, 400), grid_vals)
    assert np.array_equal(got, want)
    assert got[0] == grid_vals[0] and got[-1] == grid_vals[-1]  # endpoints exact
    assert len(got) == len(cum)
    print("test_resample_grid_to_points_matches_direct_interp OK")


def test_delta_sign_convention_ahead_lands_in_green_buckets():
    """The Δ channel is NEGATED before bucketing: ahead (Δ < 0) must land in the HIGH (green)
    buckets and behind (Δ > 0) in the LOW (red) buckets — the colour rule the Δ readout uses."""
    delta = np.array([-1.0, -0.5, 0.0, 0.5, 1.0])  # ahead → behind
    ids = bucketize(-delta, 16)
    assert ids[0] == 15, "most ahead → top (green) bucket"
    assert ids[-1] == 0, "most behind → bottom (red) bucket"
    assert (np.diff(ids) <= 0).all(), "monotonic: more behind → redder"
    print("test_delta_sign_convention_ahead_lands_in_green_buckets OK")


# ------------------------------------------------------------------ colormap
def test_rainbow_colors_anchored_and_ordered():
    """16 entries; ends anchored EXACTLY on the semantic tokens (red C.behind → green C.ahead)
    with the amber accent mid-ramp; red strictly hands over to green along the ramp."""
    cols = theme.rainbow_colors()
    assert len(cols) == theme.MAP_RAINBOW_N == 16
    assert cols[0].upper() == theme.C.behind.upper()
    assert cols[-1].upper() == theme.C.ahead.upper()
    mid = theme.rainbow_colors(3)[1]
    assert mid.upper() == theme.C.accent.upper()

    def rgb(h):
        return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))

    g = [rgb(c)[1] for c in cols]
    assert all(b >= a for a, b in zip(g[:-1], g[1:], strict=True)), "green channel must rise"
    print("test_rainbow_colors_anchored_and_ordered OK")


# ----------------------------------------------------- MapView toggle / tick invariants
def _stub_session(n=60):
    """A bare Session dressed with the read surface MapView touches — pacer-free stubs over a
    synthetic 2-lap layout (lap 0 = best, lap 1 = current). lap_channels carries a slow→fast
    speed ramp; delta() returns a known ahead→behind curve on the 400-grid."""
    t = np.arange(n) * 0.1
    xs = np.cos(np.linspace(0, 2 * math.pi, n)) * 50.0
    ys = np.sin(np.linspace(0, 2 * math.pi, n)) * 30.0
    speed = np.linspace(20.0, 60.0, n)            # km/h, strictly rising
    cum = np.linspace(0.0, 500.0, n)
    dvals = np.linspace(-0.8, 1.2, 400)           # Δ: ahead early, behind late

    s = bare_session(best=0, valid=[0, 1])
    s.tx, s.ty, s.tt, s.tv = xs, ys, t, speed
    # start_line/sector_lines are read-only properties over laps.sectors — stub the laps shape
    # they (and Seg.from_pacer) read, so MapView's _rebuild constructs real timing lines.
    line = SimpleNamespace(first=SimpleNamespace(x=-60.0, y=0.0),
                           second=SimpleNamespace(x=-40.0, y=0.0))
    s.laps = SimpleNamespace(sectors=SimpleNamespace(start_line=line, sector_lines=[]))
    s.lap_trace_segments = lambda lid: [SimpleNamespace(xs=xs, ys=ys, measured=True)]
    # lap_channels is the unified per-sample dict (one accessor for the rainbow map AND the
    # CSV export); the map reads t_media_s / x_m / y_m / speed_kmh / dist_m.
    s.lap_channels = lambda lid: {
        "t_media_s": t, "x_m": xs, "y_m": ys, "speed_kmh": speed, "dist_m": cum}
    s.delta = lambda ids, x_mode="distance": (
        0, {}, {lid: (np.linspace(0, 500.0, 400), dvals) for lid in ids})
    # D5 grip channel: a utilization ramp 0.1 (unused) → 1.1 (over the p98 limit), aligned to xy.
    # Stub the DrivingChannels service face MapView reads (session.driving.lap_grip_utilization);
    # set the backing _driving slot since session.driving is a (settable-only-via-slot) property.
    grip = np.linspace(0.1, 1.1, n)
    s._driving = SimpleNamespace(lap_grip_utilization=lambda lid: grip)
    return s


def _pen_key(item):
    p = item.opts["pen"]
    return (p.color().name(), p.width(), p.style().value)


def test_toggle_off_restores_exact_items_and_pens():
    """OFF after Speed/Δ restores the EXACT pre-toggle rendering: the same _LapOverlay item
    OBJECTS (identity) with the same pen OBJECTS, visible again — they were only hidden, never
    rebuilt — and every rainbow bucket item is emptied."""
    s = _stub_session()
    mv = MapView(s)
    mv.set_rainbow_mode("off")  # start from OFF (the map now defaults to speed) for a clean baseline
    mv.set_current_lap(1)
    before_items = list(mv._current_overlay._items)
    before_pens = [it.opts["pen"] for it in before_items]
    before_keys = [_pen_key(it) for it in before_items]
    assert all(it.isVisible() for it in before_items)

    mv._cycle_rainbow()  # off → speed
    assert mv._rainbow_mode == "speed"
    assert all(not it.isVisible() for it in mv._current_overlay._items), "overlay must hide"
    assert sum(it.xData.size for it in mv._rainbow._items) > 0, "rainbow must hold data"
    assert mv._legend.isVisibleTo(mv), "legend shows while painted"

    mv._cycle_rainbow()  # speed → delta
    assert mv._rainbow_mode == "delta"
    mv._cycle_rainbow()  # delta → grip
    assert mv._rainbow_mode == "grip"
    assert sum(it.xData.size for it in mv._rainbow._items) > 0, "grip rainbow must hold data"
    mv._cycle_rainbow()  # grip → off
    assert mv._rainbow_mode == "off"
    after_items = list(mv._current_overlay._items)
    assert [id(a) for a in after_items] == [id(b) for b in before_items], "items rebuilt!"
    assert [id(it.opts["pen"]) for it in after_items] == [id(p) for p in before_pens], "pens!"
    assert [_pen_key(it) for it in after_items] == before_keys
    assert all(it.isVisible() for it in after_items)
    assert all(it.xData is None or it.xData.size == 0 for it in mv._rainbow._items)
    assert not mv._legend.isVisibleTo(mv), "legend hides when off"
    print("test_toggle_off_restores_exact_items_and_pens OK")


def test_tick_path_does_zero_rainbow_rebuilds():
    """With the rainbow ON, the per-tick calls (set_current_lap with an UNCHANGED lap +
    marker placement) must do ZERO bucket rebuilds; a genuine lap change rebuilds exactly
    once, and a re-segment (refresh_overlays) rebuilds exactly once."""
    s = _stub_session()
    mv = MapView(s)
    mv.set_current_lap(1)
    mv.set_rainbow_mode("speed")  # ensure the SPEED channel is on (independent of the default)
    base = mv._rainbow.rebuilds
    assert base >= 1
    for k in range(100):  # 100 ticks inside the same lap
        mv.set_current_lap(1)
        mv.set_marker_index(k % len(s.tx))
    assert mv._rainbow.rebuilds == base, "tick path rebuilt the buckets!"
    mv.set_current_lap(0)  # lap-change edge
    assert mv._rainbow.rebuilds == base + 1
    mv.refresh_overlays()  # re-segment path
    assert mv._rainbow.rebuilds == base + 2
    print("test_tick_path_does_zero_rainbow_rebuilds OK")


def test_speed_extremes_land_in_extreme_buckets():
    """On the stub's strictly-rising speed ramp the FASTEST segment is painted by the TOP
    bucket item and the SLOWEST by the BOTTOM one (red = slow, green = fast)."""
    s = _stub_session()
    mv = MapView(s)
    mv.set_current_lap(1)
    mv.set_rainbow_mode("speed")  # the SPEED channel (also the map's default on a fresh load)
    items = mv._rainbow._items
    assert items[0].xData.size > 0, "slowest segments missing from the bottom (red) bucket"
    assert items[-1].xData.size > 0, "fastest segments missing from the top (green) bucket"
    # The slow end of the ramp is the polyline's START → bucket 0 holds the first point.
    assert items[0].xData[0] == s.tx[0]
    # The fast end is the polyline's END → the top bucket holds the last point.
    assert items[-1].xData[-1] == s.tx[-1]
    print("test_speed_extremes_land_in_extreme_buckets OK")


def test_default_channel_is_speed_and_combo_reads_speed():
    """A freshly-built MapView leads with the SPEED gradient (the signature visual): the mode is
    'speed', the labelled combo already shows 'Line: Speed', and once the current lap is set the
    speed rainbow paints without any user action. Cycling still walks off → speed → Δ → grip → off
    and the combo stays in sync at every step."""
    s = _stub_session()
    mv = MapView(s)
    assert mv._rainbow_mode == "speed", "the map must open speed-coloured"
    assert mv.rainbow_combo.currentData() == "speed"
    assert mv.rainbow_combo.currentText() == "Line: Speed"
    # Setting the current lap paints the speed rainbow with no explicit toggle.
    mv.set_current_lap(1)
    assert sum(it.xData.size for it in mv._rainbow._items) > 0, "speed line must paint on load"
    assert mv._legend.isVisibleTo(mv), "the speed legend shows on load"
    # Cycling still visits every channel in order, combo mirroring the mode each step.
    seen = [mv._rainbow_mode]
    for _ in range(4):
        mv._cycle_rainbow()
        assert mv.rainbow_combo.currentData() == mv._rainbow_mode, "combo must stay in sync"
        seen.append(mv._rainbow_mode)
    assert seen == ["speed", "delta", "grip", "off", "speed"], seen
    print("test_default_channel_is_speed_and_combo_reads_speed OK")


# ----------------------------------------------------- D5 grip-utilization map channel
def test_grip_channel_paints_and_on_limit_is_red():
    """Selecting Grip colours the line by per-sample utilization: on the stub's 0.1→1.1 ramp the
    ON-LIMIT end (highest util, polyline END) lands in the BOTTOM (red) bucket and the UNUSED end
    (lowest util, polyline START) in the TOP (green) bucket — green = grip left unused."""
    s = _stub_session()
    mv = MapView(s)
    mv.set_rainbow_mode("off")  # normalise the start point (default is now speed)
    mv.set_current_lap(1)
    for _ in range(3):  # off → speed → delta → grip
        mv._cycle_rainbow()
    assert mv._rainbow_mode == "grip"
    items = mv._rainbow._items
    assert sum(it.xData.size for it in items) > 0, "grip must paint the line"
    # The painted buckets reproduce the negated-util-on-fixed-scale contract: util RISES 0.1→1.1, so
    # the per-segment bucket ids must FALL (more util = redder = lower bucket) on the fixed 0..1.2
    # scale. Rebuild them the same way _build_rainbow does and check monotone-decreasing.
    util = s.driving.lap_grip_utilization(1)
    vals = -np.asarray(util, float)
    seg_vals = 0.5 * (vals[:-1] + vals[1:])
    seg_ids = bucketize(seg_vals, theme.MAP_RAINBOW_N, lo=-1.2, hi=0.0)
    assert (np.diff(seg_ids) <= 0).all(), "rising util must paint monotonically redder"
    assert seg_ids[0] > seg_ids[-1], (seg_ids[0], seg_ids[-1])  # unused greener than on-limit
    assert mv._legend.isVisibleTo(mv)
    print(f"test_grip_channel_paints_and_on_limit_is_red OK "
          f"(unused b{seg_ids[0]} > on-limit b{seg_ids[-1]})")


def test_grip_channel_degrades_when_no_g():
    """No g signal (driving.lap_grip_utilization → None) must NOT paint or crash: the rainbow stays empty and
    the normal current-lap overlay remains visible (graceful degrade, like Δ with no best lap)."""
    s = _stub_session()
    s.driving.lap_grip_utilization = lambda lid: None  # no g signal on this recording
    mv = MapView(s)
    mv.set_rainbow_mode("off")  # normalise the start point (default is now speed)
    mv.set_current_lap(1)
    for _ in range(3):  # off → speed → delta → grip
        mv._cycle_rainbow()
    assert mv._rainbow_mode == "grip"
    assert all(it.xData is None or it.xData.size == 0 for it in mv._rainbow._items), "must not paint"
    assert all(it.isVisible() for it in mv._current_overlay._items), "overlay stays visible"
    assert not mv._legend.isVisibleTo(mv), "legend hidden when nothing painted"
    print("test_grip_channel_degrades_when_no_g OK")


# ----------------------------------------------- B4 unknown-track provisional start cue
# The auto-fitted start/finish line on an UNKNOWN track is only a guess, so lap times are
# arbitrary until the user drags it. MapView overlays a dashed accent line + a "drag to set
# start/finish — lap timing provisional" callout while session.track_name is None, and REMOVES
# both once the track is known. Losing that cue silently = wrong lap times with no warning, so
# pin the unknown→present / known→absent gate and the re-pin-not-duplicate behaviour.
def test_provisional_cue_present_on_unknown_track():
    """Unknown track (track_name is None, the bare-Session default): the cue is PRESENT — both
    the dashed line and the callout exist, are added to the plot, and the label warns the user
    the timing is a guess they must drag to set."""
    s = _stub_session()
    assert getattr(s, "track_name", None) is None, "bare stub must read as an unknown track"
    mv = MapView(s)
    assert mv._provisional_line is not None, "unknown track must overlay the dashed start cue"
    assert mv._provisional_label is not None, "unknown track must show the provisional callout"
    assert mv._provisional_line in mv.plot.items, "the cue line must be added to the plot"
    assert mv._provisional_label in mv.plot.items, "the callout must be added to the plot"
    text = mv._provisional_label.toPlainText()
    assert "drag to set start/finish" in text, text
    assert "provisional" in text, text
    print("test_provisional_cue_present_on_unknown_track OK")


def test_provisional_cue_absent_on_known_track():
    """Known track (track_name set): NO cue — both state slots are None and neither a stray line
    nor callout lingers in the plot (the start/finish line is trusted, no warning needed)."""
    s = _stub_session()
    s.track_name = "Daytona"
    mv = MapView(s)
    assert mv._provisional_line is None, "a known track must not draw the provisional line"
    assert mv._provisional_label is None, "a known track must not draw the provisional callout"
    assert not any(isinstance(it, pg.TextItem) for it in mv.plot.items), \
        "no provisional callout may linger on a known track"
    print("test_provisional_cue_absent_on_known_track OK")


def test_provisional_cue_clears_when_timing_confirmed():
    """The cue now gates on session.timing_verified, not track_name alone: an unknown track whose
    start line the user has CONFIRMED (a drag) is Verified, so dragging the line into place must
    clear the dashed cue + callout. Simulate the confirm + the post-drag refresh_overlays the app
    runs, and assert the cue is gone (Provisional → Verified live)."""
    s = _stub_session()
    assert getattr(s, "track_name", None) is None
    mv = MapView(s)
    assert mv._provisional_line is not None, "starts Provisional (unknown, unconfirmed)"
    # A user drag confirms the timing (what Session.set_timing_lines does), then the app re-segments
    # and calls map.refresh_overlays(), which re-evaluates the cue.
    s.confirm_timing()
    assert s.timing_verified is True
    mv.refresh_overlays()
    assert mv._provisional_line is None, "confirming the start line must clear the dashed cue"
    assert mv._provisional_label is None, "confirming the start line must clear the callout"
    assert not any(isinstance(it, pg.TextItem) for it in mv.plot.items), \
        "no provisional callout may linger once the timing is confirmed"
    print("test_provisional_cue_clears_when_timing_confirmed OK")


def test_provisional_cue_repins_not_duplicates_on_refresh():
    """Re-pinning (a start-line move / rebuild re-runs _refresh_provisional_cue) REPOSITIONS the
    one cue rather than stacking a second: the same line+label OBJECTS persist and the plot gains
    no extra items. A drift on each move would litter the map with stale 'provisional' callouts."""
    s = _stub_session()
    mv = MapView(s)
    line0, label0 = mv._provisional_line, mv._provisional_label
    n_items = len(mv.plot.items)
    n_labels = sum(isinstance(it, pg.TextItem) for it in mv.plot.items)
    mv._refresh_provisional_cue()
    mv._refresh_provisional_cue()
    assert mv._provisional_line is line0, "the cue line must be repositioned, not rebuilt"
    assert mv._provisional_label is label0, "the callout must be repositioned, not rebuilt"
    assert len(mv.plot.items) == n_items, "re-pinning must not add plot items"
    assert sum(isinstance(it, pg.TextItem) for it in mv.plot.items) == n_labels == 1, \
        "exactly one provisional callout, never a stack"
    print("test_provisional_cue_repins_not_duplicates_on_refresh OK")


# ----------------------------------------------------- zero-lap in-panel empty state (FIX 5)
def test_map_empty_state_shown_only_with_zero_valid_laps():
    """The honest-first-run-UX fix: a load with ZERO valid laps must show a prominent, in-panel
    empty state over the map (the largest quadrant) — with the recovery action — not a black void.
    A session WITH laps must not show it. A re-segmentation that recovers laps (refresh_overlays)
    must hide it."""
    # isVisibleTo(parent), not isVisible(): the latter is False for any child of an unshown
    # top-level window, which every offscreen MapView is — it tests the widget's own visible flag.
    # With laps: the placeholder exists but stays hidden (the track/marker show through).
    with_laps = _stub_session()
    mv = MapView(with_laps)
    assert mv._empty_state is not None
    assert not mv._empty_state.isVisibleTo(mv.widget), "empty state must hide when there are laps"

    # Zero valid laps: the placeholder is shown, carries a message, and names the recovery action.
    no_laps = _stub_session()
    no_laps._valid_cache = []  # bare_session memo slot: valid_lap_ids() -> []
    mv2 = MapView(no_laps)
    assert mv2._empty_state.isVisibleTo(mv2.widget), "0-lap load must show the in-panel empty state"
    text = mv2._empty_state.text()
    assert text, "empty-state placeholder must carry a message"
    assert "start/finish" in text.lower() and "drag" in text.lower(), text
    assert mv2._empty_state.property("role") == "EmptyState"

    # Recovering laps (a dragged start line → refresh_overlays) hides the empty state again.
    no_laps._valid_cache = [0, 1]
    mv2.refresh_overlays()
    assert not mv2._empty_state.isVisibleTo(mv2.widget), "recovering laps must hide the empty state"
    print("test_map_empty_state_shown_only_with_zero_valid_laps OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nALL {len(tests)} RAINBOW MAP TESTS PASSED")
