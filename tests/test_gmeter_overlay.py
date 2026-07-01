"""Tests for studio.gmeter_overlay: the DISPLAY-layer concerns of the g-meter dial (the validated
g values in studio/gmeter.py are tested separately in test_gmeter.py). These pin:

  * the FELT-FORCE pointer convention — the dot shows the inertial reaction the driver's body
    feels, NOT the acceleration vector: braking -> UP, accelerating -> DOWN, turning right ->
    LEFT, turning left -> RIGHT;
  * the chin-mount SHAKE FILTER — the EMA dot is much smoother than the raw g, and a single shake
    spike does NOT blow out the robust cardinal peaks or balloon the max-G envelope hull;
  * the per-LAP envelope reset wiring.

Headless (offscreen Qt); fast; no media file.

Run: python tests/test_gmeter_overlay.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio.gmeter_overlay import GMeterOverlay, _convex_hull, _pct  # noqa: E402


def _fresh(w=200, h=224):
    ov = GMeterOverlay()
    ov.resize(w, h)
    return ov


def test_felt_force_convention_signs():
    """The dial maps the FELT force: braking up, accelerating down, right-turn left, left-turn
    right. Checked through _to_screen on the dial geometry."""
    ov = _fresh()
    cx, cy, r = ov._geom()
    # longitudinal: g_at_time gives +long = accelerating, -long = braking.
    _, brake_y = ov._to_screen(cx, cy, r, 0.0, -0.6)   # braking
    _, accel_y = ov._to_screen(cx, cy, r, 0.0, +0.6)   # accelerating
    assert brake_y < cy, "braking must put the pointer UP"
    assert accel_y > cy, "accelerating must put the pointer DOWN"
    # lateral: +lat = turning left, -lat = turning right.
    right_x, _ = ov._to_screen(cx, cy, r, -0.8, 0.0)   # turning right
    left_x, _ = ov._to_screen(cx, cy, r, +0.8, 0.0)    # turning left
    assert right_x < cx, "turning RIGHT must put the pointer LEFT"
    assert left_x > cx, "turning LEFT must put the pointer RIGHT"


def test_dot_tracks_felt_force_after_filter():
    """After feeding a steady braking-while-turning-right g, the filtered dot sits up-and-left."""
    ov = _fresh()
    for _ in range(60):
        ov.set_g((-0.8, -0.5, 0.94))   # turning right (lat<0), braking (long<0)
    cx, cy, r = ov._geom()
    dx, dy = ov._to_screen(cx, cy, r, ov._fx, ov._fy)
    assert dx < cx and dy < cy, f"expected up-left dot, got dx={dx:.0f} dy={dy:.0f} c=({cx:.0f},{cy:.0f})"


def test_ema_filter_is_smoother_than_raw():
    """The EMA-filtered dot has far less step-to-step jitter than the raw shaky signal, while
    still tracking the true level (the chin-mount shake filter)."""
    rng = np.random.default_rng(0)
    n = 600
    true_lat = 0.5
    raw = true_lat + rng.normal(0, 0.6, n)
    ov = _fresh()
    filt = []
    for v in raw:
        ov.set_g((float(v), 0.0, abs(float(v))))
        filt.append(ov._fx)
    filt = np.array(filt)
    raw_j = np.std(np.diff(raw))
    filt_j = np.std(np.diff(filt[50:]))
    assert filt_j < raw_j / 2, f"EMA should at least halve the jitter: raw {raw_j:.3f} filt {filt_j:.3f}"
    assert abs(filt[200:].mean() - true_lat) < 0.1, "EMA must still track the true level"


def test_single_shake_spike_does_not_blow_out_peaks_or_envelope():
    """A lone helmet-shake spike must NOT set a cardinal peak (high-percentile gate) nor balloon
    the max-G envelope hull (hull points clamped to the robust peaks)."""
    ov = _fresh()
    ov.set_lap(1)
    for _ in range(120):
        ov.set_g((-0.4, 0.0, 0.4))     # steady right turn -> felt LEFT, ~0.4 g
    peak_before = ov._peak_left
    assert 0.3 < peak_before < 0.5
    ov.set_g((-6.0, 0.0, 6.0))         # one absurd spike
    assert ov._peak_left < 1.0, f"a single spike must not set the peak (got {ov._peak_left:.2f})"
    max_hull = max((abs(x) for (x, _) in ov._hull_pts), default=0.0)
    assert max_hull < 1.2, f"a single spike must not balloon the hull (got {max_hull:.2f})"


def test_envelope_resets_on_lap_change():
    """The envelope + cardinal peaks accumulate within a lap and reset when set_lap moves to a
    new lap (the per-lap grip-usage scope). A None lap (between laps) holds, never resets."""
    ov = _fresh()
    ov.set_lap(3)
    for _ in range(40):
        ov.set_g((0.8, 0.0, 0.8))
    assert ov._peak_right > 0 and len(ov._hull_pts) > 0
    ov.set_lap(None)                    # between laps -> HOLD
    assert ov._peak_right > 0, "None lap must not reset the envelope"
    ov.set_lap(4)                       # new lap -> reset
    assert ov._peak_right == 0.0 and len(ov._hull_pts) == 0, "lap change must reset the envelope"


def test_reset_envelope_reseeds_dot_ema():
    """reset_envelope() must also re-seed the DOT EMA (not just the hull/peaks), so a per-lap reset
    starts the filtered pointer fresh on the new scope's first sample instead of carrying the
    previous lap's filtered value (which would drift the dot in from the old lap's position)."""
    ov = _fresh()
    ov.set_lap(1)
    # Drive a steady strong left-turn (felt RIGHT) so the EMA settles well away from origin.
    for _ in range(60):
        ov.set_g((0.9, 0.0, 0.9))
    assert ov._ema_init is True
    assert ov._fx > 0.5, ov._fx           # filtered dot sits to the felt-right
    # Reset (e.g. new lap scope): the EMA must be re-seeded, not left carrying the old value.
    ov.reset_envelope()
    assert ov._ema_init is False, "reset_envelope must clear _ema_init so the dot re-seeds"
    assert ov._fx == 0.0 and ov._fy == 0.0, "filtered dot must be zeroed on reset"
    # The very next sample (a small opposite-direction g) must SEED the EMA to itself — NOT drift in
    # from the old (large, opposite) filtered value.
    ov.set_g((-0.2, 0.1, 0.22))
    assert abs(ov._fx - (-0.2)) < 1e-9, f"dot must re-seed to the first new sample, got {ov._fx}"
    assert abs(ov._fy - 0.1) < 1e-9, ov._fy
    print("test_reset_envelope_reseeds_dot_ema OK")


def test_lap_change_reseeds_dot_ema():
    """set_lap to a new lap (the per-lap reset) re-seeds the dot EMA via reset_envelope — the new
    lap's first sample seeds the pointer rather than carrying the prior lap's filtered value."""
    ov = _fresh()
    ov.set_lap(3)
    for _ in range(60):
        ov.set_g((-0.8, 0.0, 0.8))   # felt LEFT, settled
    assert ov._fx < -0.4
    ov.set_lap(4)                      # new lap -> reset (incl. the EMA)
    assert ov._ema_init is False and ov._fx == 0.0
    ov.set_g((0.3, 0.0, 0.3))
    assert abs(ov._fx - 0.3) < 1e-9, "new lap's first sample must seed the dot, not drift from lap 3"
    print("test_lap_change_reseeds_dot_ema OK")


def _render_dial(export: bool, w=280, h=280):
    """Render paint_dial into an opaque magenta QImage and return the numpy RGB array — so the
    EXPORT vs LIVE look can be compared pixel-wise (no widget, no video)."""
    import numpy as np
    from PySide6.QtGui import QColor, QImage, QPainter

    from studio import gmeter_overlay as g
    st = g.DialState(fx=0.6, fy=-0.4, have=True,
                     hull_pts=[(0.5, 0.3), (-0.4, 0.2), (0.1, -0.5), (0.3, 0.4)],
                     peak_fwd=0.9, peak_back=0.5, peak_left=1.0, peak_right=1.1, source="accl")
    img = QImage(w, h, QImage.Format_RGB888)
    img.fill(QColor("#FF00FF"))
    p = QPainter(img)
    g.paint_dial(p, w, h, st, export=export)
    p.end()
    # .copy() — constBits() is a VIEW into the QImage buffer; the image is local and would be freed
    # on return, leaving the numpy array dangling. Copy detaches it.
    return np.array(np.frombuffer(img.constBits(), np.uint8, count=3 * w * h).reshape(h, w, 3))


def test_export_dial_has_no_backdrop_box():
    """The EXPORT g-dial drops the live overlay's translucent backdrop box. The live render fills a
    big rounded rect (dimming most of the box), so it has FAR less of the magenta background left
    than the export render, which paints only the rings/numbers/dot over an otherwise clear box.
    Proof the export removed the backdrop panel."""
    import numpy as np
    live = _render_dial(export=False)
    exp = _render_dial(export=True)
    magenta = np.array([255, 0, 255])
    live_bg = int((live == magenta).all(axis=2).sum())
    exp_bg = int((exp == magenta).all(axis=2).sum())
    # the live backdrop covers most of the box; the export leaves most of it transparent (magenta).
    assert exp_bg > live_bg * 2, f"export must drop the backdrop box: live_bg {live_bg} exp_bg {exp_bg}"
    # and the export keeps a clearly-transparent region near a mid-edge the live box would dim.
    assert np.array_equal(exp[140, 4], magenta), "export box edge must stay background (no panel)"


def test_export_dial_numbers_are_bigger():
    """The EXPORT dial is far more vivid than the live dial — checked by the count of near-WHITE
    pixels (export draws big white outlined numbers + white rings + a bright dot; the live ones are
    small + dim). The export render must have many more bright pixels."""
    live = _render_dial(export=False)
    exp = _render_dial(export=True)
    live_white = int((live > 235).all(axis=2).sum())
    exp_white = int((exp > 235).all(axis=2).sum())
    assert exp_white > live_white * 2, f"export should be far brighter: live {live_white} exp {exp_white}"


def test_export_dial_geom_reserves_bigger_number_margin():
    """`_export_dial_geom` reserves a larger margin for the (bigger) cardinal numbers than the live
    `dial_geom`, so the radius is smaller relative to the box — the numbers sit clearly outside."""
    from studio.gmeter_overlay import _export_dial_geom, dial_geom
    _, _, r_live = dial_geom(280, 280)
    _, _, r_exp = _export_dial_geom(280, 280)
    assert r_exp < r_live, f"export dial radius {r_exp:.0f} should be < live {r_live:.0f} (bigger margin)"


def test_live_paint_dial_default_is_unchanged():
    """The new `export`/`scale_k` params default to the original LIVE behaviour, so the on-screen
    overlay is byte-identical to before. Guards the defaults + that the live render still paints
    its backdrop box."""
    import inspect

    import numpy as np

    from studio import gmeter_overlay as g
    sig = inspect.signature(g.paint_dial)
    assert sig.parameters["export"].default is False
    assert sig.parameters["scale_k"].default == 1.0
    default = _render_dial(export=False)
    # the live render still paints its backdrop box (a mid-edge inside the box is dimmed, not bg).
    assert not np.array_equal(default[140, 4], np.array([255, 0, 255]))  # live box still drawn


def test_pct_and_hull_helpers():
    assert _pct([], 90) == 0.0
    assert _pct([1.0], 90) == 1.0
    # 90th pct of 0..10 is 9.0
    assert abs(_pct(list(range(11)), 90) - 9.0) < 1e-9
    # convex hull of a square (+ an interior point) has 4 vertices
    hull = _convex_hull([(0, 0), (1, 0), (1, 1), (0, 1), (0.5, 0.5)])
    assert len(hull) == 4


# ------------------------------------------------------------- per-frame repaint caching (perf)
# The live overlay's paintEvent fires every ~30 Hz tick to move the dot. These pin that the SLOW
# work (the convex hull + the static ring/backdrop/number layer) is cached and reused across frames,
# recomputed only when the envelope / size / palette actually change — while the on-screen pixels
# stay identical (the split is a rendering-COST optimisation, not a visual change).

def _spy_hull(monkeypatch=None):
    """Wrap studio.gmeter_overlay._convex_hull with a call counter. Returns (restore, counter)."""
    from studio import gmeter_overlay as g
    orig = g._convex_hull
    calls = {"n": 0}

    def counted(points):
        calls["n"] += 1
        return orig(points)

    g._convex_hull = counted
    return (lambda: setattr(g, "_convex_hull", orig)), calls


def _seed(ov, n=40, lap=1):
    ov.set_lap(lap)
    for _ in range(n):
        ov.set_g((0.6, -0.4, 0.72))


def _paint(ov):
    """Drive one real paintEvent (render into an offscreen pixmap) — the per-frame repaint path."""
    from PySide6.QtGui import QPixmap
    pm = QPixmap(ov.size())
    ov.render(pm)


def test_static_layer_cached_hull_computed_once_across_frames():
    """Two consecutive repaints with an UNCHANGED envelope recompute the convex hull exactly ONCE —
    the static layer (hull included) is cached and re-blitted; only the dot is redrawn each frame."""
    ov = _fresh()
    _seed(ov)
    restore, calls = _spy_hull()
    try:
        # First paint: cache miss -> renders the static layer (one hull compute).
        _paint(ov)
        assert calls["n"] == 1, f"first frame should compute the hull once, got {calls['n']}"
        # Simulate more playback frames WITHOUT new g-data (paused / static frame): each repaint
        # must reuse the cached static layer and NOT recompute the hull.
        for _ in range(5):
            _paint(ov)
        assert calls["n"] == 1, f"unchanged envelope must not recompute the hull, got {calls['n']}"
    finally:
        restore()


def test_moving_dot_alone_does_not_recompute_static_layer():
    """set_g that only MOVES the dot (its envelope/peaks may tick, but a paint after a manual dot
    move on a frozen envelope) reuses the static layer. Here we freeze the envelope by not feeding
    new samples and just re-render at shifted dot positions — the hull is computed only once."""
    ov = _fresh()
    _seed(ov)
    restore, calls = _spy_hull()
    try:
        _paint(ov)
        base = calls["n"]
        for dx in (0.1, -0.2, 0.3):
            ov._fx = dx            # move the dot only (no envelope change)
            _paint(ov)
        assert calls["n"] == base, "moving only the dot must not recompute the hull/static layer"
    finally:
        restore()


def test_envelope_change_invalidates_and_recomputes_hull():
    """A NEW g-sample (envelope grows / peaks tick) invalidates the cache: the next repaint
    recomputes the hull. A stale cached envelope would be a real bug."""
    ov = _fresh()
    _seed(ov)
    restore, calls = _spy_hull()
    try:
        _paint(ov)
        assert calls["n"] == 1
        _paint(ov)
        assert calls["n"] == 1, "no data change -> cached"
        ov.set_g((0.9, -0.7, 1.14))   # new felt sample -> envelope/version changes
        _paint(ov)
        assert calls["n"] == 2, "an envelope update must recompute the hull on the next paint"
    finally:
        restore()


def test_palette_change_invalidates_static_layer():
    """A palette flip invalidates the static-layer cache (the key includes the active palette), so
    the next repaint re-renders it (recomputing the hull). Conservative invalidation — a missed
    palette flip would leave stale chrome."""
    from studio import theme
    ov = _fresh()
    _seed(ov)
    restore, calls = _spy_hull()
    prev = theme.active_palette()
    try:
        _paint(ov)
        assert calls["n"] == 1
        _paint(ov)
        assert calls["n"] == 1
        theme.set_palette(theme.PALETTE_COLORBLIND if prev == theme.PALETTE_STANDARD
                          else theme.PALETTE_STANDARD)
        _paint(ov)
        assert calls["n"] == 2, "a palette change must invalidate the cached static layer"
    finally:
        theme.set_palette(prev)
        restore()


def test_resize_invalidates_static_layer():
    """Resizing the widget invalidates the cache (the key includes width/height) — the static layer
    is size-dependent (dial geometry), so it must re-render at the new size."""
    ov = _fresh()
    _seed(ov)
    restore, calls = _spy_hull()
    try:
        _paint(ov)
        assert calls["n"] == 1
        ov.resize(240, 268)
        _paint(ov)
        assert calls["n"] == 2, "a resize must invalidate the size-keyed static layer"
    finally:
        restore()


def test_static_cache_key_reused_across_frames():
    """The static-layer cache key is (width, height, dpr, palette, env_version) and is stable across
    frames with an unchanged envelope, so the same cached QPixmap object is reused (not rebuilt)."""
    ov = _fresh()
    _seed(ov)
    _paint(ov)
    pm1, key1 = ov._static_pixmap, ov._static_key
    assert pm1 is not None and key1 is not None
    _paint(ov)
    assert ov._static_pixmap is pm1, "unchanged frame must reuse the SAME cached pixmap object"
    assert ov._static_key == key1
    # env_version is part of the key and monotonically identifies the envelope state.
    assert key1[-1] == ov._env_version


def test_cached_paint_is_pixel_identical_to_full_render():
    """The cached-static-layer + dot composite is BYTE-IDENTICAL to a fresh single-pass full render
    of the same state, ON THE WIDGET'S TRANSLUCENT SURFACE (the real on-screen path). This is the
    pixel-identity guarantee: the optimisation removes redundant recompute/redraw, nothing visual."""
    from PySide6.QtGui import QColor, QImage, QPainter

    from studio import gmeter_overlay as g
    ov = _fresh(280, 280)
    _seed(ov, n=60)
    st = ov._dial_state()
    w = h = 280

    def _full():
        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        img.fill(QColor(0, 0, 0, 0))                     # the widget's translucent surface
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        g.paint_dial(p, w, h, st, export=False)
        p.end()
        return img

    def _cached():
        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.drawPixmap(0, 0, ov._static_layer(st))
        g._paint_dial_dot(p, w, h, st)
        p.end()
        return img

    def _arr(img):
        return np.array(np.frombuffer(img.constBits(), np.uint8,
                                      count=4 * w * h).reshape(h, w, 4)).copy()

    diff = np.abs(_arr(_full()).astype(int) - _arr(_cached()).astype(int))
    assert diff.max() == 0, f"cached render must be pixel-identical (maxdiff {int(diff.max())})"


def test_source_change_invalidates_static_layer():
    """The source tag lives in the cached static layer, so set_source to a NEW label invalidates it;
    an unchanged label is a no-op (no needless re-render)."""
    ov = _fresh()
    _seed(ov)
    ov.set_source("accl")
    v0 = ov._env_version
    ov.set_source("accl")               # unchanged -> no bump
    assert ov._env_version == v0, "unchanged source must not invalidate the static layer"
    ov.set_source("gps")                # changed -> bump
    assert ov._env_version > v0, "a source change must invalidate the static layer"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nALL {len(tests)} gmeter-overlay tests passed")
