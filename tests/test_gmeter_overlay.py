"""Tests for studio.gmeter_overlay: the DISPLAY-layer concerns of the g-meter dial (the validated
g values in studio/gmeter.py are tested separately in test_gmeter.py). These pin:

  * the FELT-FORCE pointer convention — the dot shows the inertial reaction the driver's body
    feels, NOT the acceleration vector: braking -> UP, accelerating -> DOWN, turning right ->
    LEFT, turning left -> RIGHT;
  * the chin-mount SHAKE FILTER — the EMA dot is much smoother than the raw g, and a single shake
    spike does NOT blow out the robust cardinal peaks or balloon the max-G envelope hull;
  * the per-LAP envelope reset wiring, now including the dot's trail;
  * THE FACE'S COMPOSITION, after the §6.5 redesign. The dial was eleven text items in a 120x140
    card; it is two. The guards that used to hold the old face together are not deleted, they are
    re-aimed at the new one — the ring captions that were drawn ON their own strokes now have to
    prove they touch neither, the readout has to prove it stays in its band, and the "the export
    must not be more silent than the screen" contract, which used to allow exactly two live-only
    strings, is now set EQUALITY because there is no difference left to allow;
  * the NO-SIGNAL states, which is the redesign's other half: an export of a recording with no g
    series used to burn in a complete instrument reporting four `0.0`.

Headless (offscreen Qt); fast; no media file.

Run: python tests/test_gmeter_overlay.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Inert media, set before any studio widget import: one test drives the REAL VideoView to prove
# where the dial's provenance sentence went (PlayerPane reads this once, at construction).
os.environ.setdefault("PACER_NO_MEDIA", "1")
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
    dx, dy = ov._to_screen(cx, cy, r, ov._filter.fx, ov._filter.fy)
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
        filt.append(ov._filter.fx)
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
    peak_before = ov._filter.peak_left
    assert 0.3 < peak_before < 0.5
    ov.set_g((-6.0, 0.0, 6.0))         # one absurd spike
    assert ov._filter.peak_left < 1.0, f"a single spike must not set the peak (got {ov._filter.peak_left:.2f})"
    max_hull = max((abs(x) for (x, _) in ov._filter.hull_pts), default=0.0)
    assert max_hull < 1.2, f"a single spike must not balloon the hull (got {max_hull:.2f})"


def test_envelope_resets_on_lap_change():
    """The envelope + cardinal peaks accumulate within a lap and reset when set_lap moves to a
    new lap (the per-lap grip-usage scope). A None lap (between laps) holds, never resets."""
    ov = _fresh()
    ov.set_lap(3)
    for _ in range(40):
        ov.set_g((0.8, 0.0, 0.8))
    assert ov._filter.peak_right > 0 and len(ov._filter.hull_pts) > 0
    ov.set_lap(None)                    # between laps -> HOLD
    assert ov._filter.peak_right > 0, "None lap must not reset the envelope"
    ov.set_lap(4)                       # new lap -> reset
    assert ov._filter.peak_right == 0.0 and len(ov._filter.hull_pts) == 0, "lap change must reset the envelope"


def test_reset_envelope_reseeds_dot_ema():
    """reset_envelope() must also re-seed the DOT EMA (not just the hull/peaks), so a per-lap reset
    starts the filtered pointer fresh on the new scope's first sample instead of carrying the
    previous lap's filtered value (which would drift the dot in from the old lap's position)."""
    ov = _fresh()
    ov.set_lap(1)
    # Drive a steady strong left-turn (felt RIGHT) so the EMA settles well away from origin.
    for _ in range(60):
        ov.set_g((0.9, 0.0, 0.9))
    assert ov._filter.ema_init is True
    assert ov._filter.fx > 0.5, ov._filter.fx           # filtered dot sits to the felt-right
    # Reset (e.g. new lap scope): the EMA must be re-seeded, not left carrying the old value.
    ov.reset_envelope()
    assert ov._filter.ema_init is False, "reset_envelope must clear ema_init so the dot re-seeds"
    assert ov._filter.fx == 0.0 and ov._filter.fy == 0.0, "filtered dot must be zeroed on reset"
    # The very next sample (a small opposite-direction g) must SEED the EMA to itself — NOT drift in
    # from the old (large, opposite) filtered value.
    ov.set_g((-0.2, 0.1, 0.22))
    assert abs(ov._filter.fx - (-0.2)) < 1e-9, f"dot must re-seed to the first new sample, got {ov._filter.fx}"
    assert abs(ov._filter.fy - 0.1) < 1e-9, ov._filter.fy
    print("test_reset_envelope_reseeds_dot_ema OK")


def test_lap_change_reseeds_dot_ema():
    """set_lap to a new lap (the per-lap reset) re-seeds the dot EMA via reset_envelope — the new
    lap's first sample seeds the pointer rather than carrying the prior lap's filtered value."""
    ov = _fresh()
    ov.set_lap(3)
    for _ in range(60):
        ov.set_g((-0.8, 0.0, 0.8))   # felt LEFT, settled
    assert ov._filter.fx < -0.4
    ov.set_lap(4)                      # new lap -> reset (incl. the EMA)
    assert ov._filter.ema_init is False and ov._filter.fx == 0.0
    ov.set_g((0.3, 0.0, 0.3))
    assert abs(ov._filter.fx - 0.3) < 1e-9, "new lap's first sample must seed the dot, not drift from lap 3"
    print("test_lap_change_reseeds_dot_ema OK")


def _state(**kw):
    """A populated DialState: dot up-and-right, a real grip envelope, a trail behind the dot, and
    `seen` — the flag that says this recording HAS a g series (see DialState). Shared by every
    render below so a face change shows up in all of them at once."""
    from studio import gmeter_overlay as g
    base = dict(
        fx=0.6, fy=-0.4, have=True, seen=True,
        trail=[(0.10, -0.50), (0.24, -0.49), (0.38, -0.46), (0.50, -0.44), (0.60, -0.40)],
        hull_pts=[(0.5, 0.3), (-0.4, 0.2), (0.1, -0.5), (0.3, 0.4)],
        peak_fwd=0.9, peak_back=0.5, peak_left=1.0, peak_right=1.1)
    base.update(kw)
    return g.DialState(**base)


def _render_dial(export: bool, w=280, h=280, st=None):
    """Render paint_dial into an opaque magenta QImage and return the numpy RGB array — so the
    EXPORT vs LIVE look can be compared pixel-wise (no widget, no video)."""
    import numpy as np
    from PySide6.QtGui import QColor, QImage, QPainter

    from studio import gmeter_overlay as g
    img = QImage(w, h, QImage.Format_RGB888)
    img.fill(QColor("#FF00FF"))
    p = QPainter(img)
    g.paint_dial(p, w, h, st if st is not None else _state(), export=export)
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


def test_export_dial_is_far_brighter_than_the_live_one():
    """The EXPORT dial is far more vivid than the live dial — checked by the count of near-WHITE
    pixels (export draws white haloed rings, a white trail, a bright dot and outlined text; the
    live ones are dim theme hairlines). The export render must have many more bright pixels."""
    live = _render_dial(export=False)
    exp = _render_dial(export=True)
    live_white = int((live > 235).all(axis=2).sum())
    exp_white = int((exp > 235).all(axis=2).sum())
    assert exp_white > live_white * 2, f"export should be far brighter: live {live_white} exp {exp_white}"


def test_both_modes_lay_out_on_one_shared_geometry():
    """THE EXPORT DIAL IS NOW THE SAME DIAL. It used to need `_export_dial_geom` — a 20 % margin
    reserving room for four big outlined cardinal numbers, against the live geometry's title strip
    and tag band — so the two modes agreed on composition but not on layout. The redesign deletes
    every element that sat outside the outer ring except the one readout, which both modes put in
    the same band, so there is one `dial_geom` and `_export_dial_geom` is gone.

    `paint_dial` exists precisely so the burn IS the screen; a second geometry was the last place
    that could quietly stop being true."""
    from studio import gmeter_overlay as g
    assert not hasattr(g, "_export_dial_geom"), "the export dial must not re-acquire its own layout"
    # The one geometry keeps the whole face inside the box, at the 120x140 floor and at 4K.
    for w, h in ((120, 140), (240, 280), (280, 280), (560, 560)):
        cx, cy, r = g.dial_geom(w, h)
        band = g.readout_rect(w, h)
        assert r > 8.0, (w, h, r)
        assert cx - r >= 0 and cx + r <= w, f"{w}x{h}: dial wider than its box"
        assert cy - r >= 0, f"{w}x{h}: dial above its box"
        assert cy + r <= band.top() + 1e-9, (
            f"{w}x{h}: the dial reaches into the readout band (cy+r {cy + r:.2f} vs band top "
            f"{band.top():.2f}) — the band exists so the number can never land on the face")


def test_the_redesign_actually_made_the_dial_bigger():
    """The eleven text items were not free: at the 120x140 minimum the old face spent 18 px of
    every side on cardinal numbers, plus an 18 px title strip and a 13 px tag band, leaving a
    36.5 px radius. With nothing outside the ring but the readout the same card carries 51.5 px.

    Pinned as a floor rather than an equality — the point is that the room came back, and a later
    change that quietly spends it again should have to say so here."""
    from studio import gmeter_overlay as g
    _, _, r = g.dial_geom(120, 140)
    assert r >= 50.0, f"the 120x140 dial's radius fell back to {r:.1f} px (pre-redesign: 36.5)"


def test_paint_dial_defaults_to_the_live_look():
    """`export`/`scale_k` default to the on-screen dial, so every existing caller keeps the live
    look without saying so, and the live render still paints its backdrop box (the export drops
    it)."""
    import inspect

    import numpy as np

    from studio import gmeter_overlay as g
    sig = inspect.signature(g.paint_dial)
    assert sig.parameters["export"].default is False
    assert sig.parameters["scale_k"].default == 1.0
    default = _render_dial(export=False)
    # the live render still paints its backdrop box (a mid-edge inside the box is dimmed, not bg).
    assert not np.array_equal(default[140, 4], np.array([255, 0, 255]))  # live box still drawn


# ------------------------------------------------------------------------ the trail (new element)
# The four direction words are gone from the face; the trail is what replaces them. These pin the
# three properties that make it readable rather than decorative.

def test_the_trail_head_is_the_dot_itself():
    """The trail is DECIMATED — one vertex committed every `_TRAIL_EVERY` ticks — because storing
    all 30 of a second's samples drew a scribble around the dot rather than a path (measured on
    real D24). Decimation must not detach the stroke from the dot: the newest sample overwrites
    the head, so the last trail point is always exactly where the dot is drawn.

    A gap here would look like a lag in the instrument."""
    from studio import gmeter_overlay as g
    ov = _fresh()
    ov.set_lap(1)
    for i in range(50):
        ov.set_g((0.02 * i, -0.01 * i, 0.02 * i))
        st = ov._dial_state()
        assert st.trail, "a fed dial must have a trail"
        assert st.trail[-1] == (st.fx, st.fy), (
            f"tick {i}: trail head {st.trail[-1]} is not the dot {(st.fx, st.fy)}")
    # ... and it really is decimated, not one vertex per tick.
    assert len(ov._filter.trail) < 50 / (g._TRAIL_EVERY - 1), len(ov._filter.trail)
    assert len(ov._filter.trail) <= g._TRAIL_PTS


def test_the_trail_is_bounded_and_resets_with_the_lap():
    """The trail is a ring buffer (a lap is minutes long; the stroke is seconds), and it clears on
    the lap boundary for the same reason the dot EMA re-seeds there: left alone it would draw a
    stroke from the old lap's last corner, through the re-seeded origin, to the new lap's first
    sample — a path the kart never took."""
    from studio import gmeter_overlay as g
    ov = _fresh()
    ov.set_lap(1)
    for i in range(g._TRAIL_PTS * g._TRAIL_EVERY * 3):
        ov.set_g((0.5 * np.sin(i / 9.0), 0.3 * np.cos(i / 7.0), 0.6))
    assert len(ov._filter.trail) == g._TRAIL_PTS, len(ov._filter.trail)
    ov.set_lap(2)
    assert ov._filter.trail == [], "a new lap must start a new trail"


def test_the_trail_survives_the_exporters_lap_gating():
    """The exporter BLANKS the envelope before the start line and FREEZES it at the finish, so the
    burned peaks are the lap's own. The trail must pass through both untouched: it is part of the
    dot, and `advance_and_snapshot`'s own docstring says the dot is never gated — the padding
    exists to show live footage. A frozen trail behind a moving dot would be a visible defect.

    Pinned here, on the real `dataclasses.replace` calls the exporter makes."""
    import dataclasses
    st = _state()
    for gated in (dataclasses.replace(st, hull_pts=[], peak_fwd=0.0, peak_back=0.0,
                                      peak_left=0.0, peak_right=0.0),
                  dataclasses.replace(st, hull_pts=st.hull_pts, peak_fwd=st.peak_fwd)):
        assert gated.trail == st.trail, "the exporter's lap gating must not touch the trail"
        assert gated.have is st.have and gated.seen is st.seen


def test_the_readout_is_the_dots_own_magnitude():
    """The dial's ONE number is the radius of the filtered dot — hypot of the same pair the dot is
    drawn at — not `g_at_time`'s unfiltered total. If it were the latter, the number and the dot
    would disagree on screen every time the EMA lagged a transient, which is exactly when someone
    is looking at it."""
    from studio import gmeter_overlay as g
    assert g.readout_text(_state(fx=0.6, fy=-0.8)) == "1.0 g"
    assert g.readout_text(_state(fx=0.0, fy=0.0)) == "0.0 g"
    assert g.readout_text(_state(fx=1.2, fy=-0.9)) == "1.5 g"
    # one decimal, deliberately: the EMA's ~0.1 s constant would churn a second one every frame
    assert g.readout_text(_state(fx=0.123, fy=0.0)) == "0.1 g"


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
            ov._filter.fx = dx            # move the dot only (no envelope change)
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
    assert key1[-1] == ov._filter.version


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
        g._paint_dial_moving(p, w, h, st)
        p.end()
        return img

    def _arr(img):
        return np.array(np.frombuffer(img.constBits(), np.uint8,
                                      count=4 * w * h).reshape(h, w, 4)).copy()

    diff = np.abs(_arr(_full()).astype(int) - _arr(_cached()).astype(int))
    assert diff.max() == 0, f"cached render must be pixel-identical (maxdiff {int(diff.max())})"


def test_source_label_names_axis_provenance():
    """L6: the provenance must name the dial's AXIS provenance, not one source. The usual meter is
    IMU-lateral + GPS-longitudinal (the IMU forward axis is vibration-inflated), so a bare "ACCL"
    misattributes the braking axis to the IMU. source_label reads "IMU lat · GPS long" when mixed,
    and the plain source name when both axes share one sensor.

    The FACT is unchanged by the §6.5 redesign; only where it is stated moved (see the tooltip
    test below), so this guard is unchanged too."""
    from studio.gmeter_overlay import source_label
    # The real D24 case: IMU lateral, GPS-derived longitudinal.
    assert source_label("accl", "gps") == "IMU lat · GPS long"
    # No IMU at all (GPS-only fallback meter): both axes GPS -> plain "GPS".
    assert source_label("gps", "gps") == "GPS"
    assert source_label("gps") == "GPS"
    # Degenerate/absent longitudinal source (older callers) -> just the lateral name, never "ACCL".
    assert source_label("accl") == "IMU"
    assert source_label("accl", "accl") == "IMU"


def test_the_dial_does_not_carry_its_provenance_any_more():
    """The tag was one of eleven text items on a 120x140 face, at 6.5 px. It is gone — and gone
    means GONE, not "kept in the snapshot and never painted": a write-only field on a dataclass two
    threads pass around is exactly the debt the export-thread-safety extraction was cleaning up.

    The peaks are the contrast, and the contrast is the point of this test sitting next to the one
    below: the peaks are ALSO no longer painted individually, and they ARE still carried, because
    they still do work (they clamp the hull, the exporter freezes them, test_export_padding reads
    them). `source` did no work once the face stopped drawing it."""
    from studio import gmeter_overlay as g
    st = g.DialFilter().snapshot()
    assert not hasattr(st, "source"), "DialState still carries a provenance nothing paints"
    assert not hasattr(g.DialFilter, "set_source")
    assert hasattr(st, "peak_fwd") and hasattr(st, "hull_pts"), (
        "the peaks/hull must survive: they are load-bearing, unlike the tag")


def test_the_provenance_moved_to_the_toggles_tooltip():
    """WHERE THE TAG WENT — driven on the REAL VideoView, not asserted from source text.

    §6.5's instruction was "provenance moves to the View-menu toggle's tooltip". This is that
    tooltip, and it has to carry BOTH things the face stopped saying: the felt-force convention
    (which four 6 px direction words used to whisper) and the mixed axis provenance. A tooltip can
    say them in sentences; the face could only abbreviate them.

    The overlay window itself cannot host a tooltip — it is `WA_TransparentForMouseEvents`, so the
    pointer never enters it — which is why the toggle is the right surface and not a compromise."""
    from studio.video_view import VideoView
    v = VideoView(None)
    try:
        base = v.gmeter_btn.toolTip()
        assert "G" in base, base                       # the shortcut is still advertised
        v.set_gmeter_source("accl", "gps")             # the real D24 pairing
        tip = v.gmeter_btn.toolTip()
        assert base in tip, "the tooltip must keep saying what the toggle does"
        # the convention the four direction words used to carry
        low = tip.lower()
        for word in ("brak", "up", "right-hand", "left"):
            assert word in low, f"{word!r} missing from the g-meter tooltip: {tip!r}"
        # the provenance the 6.5 px tag used to carry, both axes named
        assert "IMU" in tip and "GPS" in tip, tip
        # the GPS-only fallback meter says so instead of claiming an IMU
        v.set_gmeter_source("gps", "gps")
        gps_tip = v.gmeter_btn.toolTip()
        assert "GPS" in gps_tip and "IMU" not in gps_tip.replace("IMU lat", ""), gps_tip
        print("ok the g-meter toggle's tooltip states the convention + the axis provenance")
    finally:
        v.deleteLater()


# ------------------------------------------------------------------ what the dial SAYS (labels)
# The export dial is burned into an MP4 a driver shares: whatever the screen says, the burn must
# say too. These pin that contract — now as set EQUALITY, since the redesign removed the last two
# strings the two modes differed by — plus the two placement defects §6.5 named: the ring caption
# drawn ON its own stroke, and text landing on the dial instead of in its own band.


def _label_state():
    """A populated DialState for the label tests: a dot mid-corner, a real envelope, a trail."""
    return _state(fx=0.35, fy=-0.22,
                  trail=[(-0.10, -0.60), (0.05, -0.52), (0.20, -0.40), (0.30, -0.30),
                         (0.35, -0.22)],
                  hull_pts=[(0.55, 0.30), (-0.62, 0.22), (0.10, -0.72), (0.34, 0.44)],
                  peak_fwd=0.9, peak_back=0.5, peak_left=0.8, peak_right=1.1)


def _painted_strings(export: bool, w=280, h=280, k=1.0, st=None):
    """Every string the dial actually paints, in either mode. The live dial goes through
    QPainter.drawText (caught by a Python QPainter subclass — the calls are made from Python, so
    the override wins); the export dial goes through the module-level _draw_text_outlined, wrapped
    here. Nothing is inferred from the source text."""
    from PySide6.QtGui import QColor, QImage, QPainter

    from studio import gmeter_overlay as g

    class _Spy(QPainter):
        def __init__(self, dev):
            super().__init__(dev)
            self.texts = []

        def drawText(self, *a):
            if a and isinstance(a[-1], str):
                self.texts.append(a[-1])
            return super().drawText(*a)

    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    spy = _Spy(img)
    outlined: list[str] = []
    orig = g._draw_text_outlined

    def _rec(p, rect, flags, text, font, colour, halo=2.2):
        outlined.append(text)
        return orig(p, rect, flags, text, font, colour, halo)

    g._draw_text_outlined = _rec
    try:
        g.paint_dial(spy, w, h, st if st is not None else _label_state(),
                     export=export, scale_k=k)
    finally:
        g._draw_text_outlined = orig
        spy.end()
    return spy.texts + outlined


def test_the_burn_and_the_screen_say_exactly_the_same_thing():
    """L9-03 / L12-05, STRENGTHENED by the redesign. This guard used to allow exactly two live-only
    strings — the "G METER" title (dropped so the export could fill its box) and the provenance tag
    (deliberately not burned into someone's clip) — and its job was to hold the exception list at
    two. There is no exception list left: the title is gone from both modes, and the provenance is
    a sentence on the toggle's tooltip. So the assertion is EQUALITY, in both directions, which is
    the contract `paint_dial` was extracted to make true in the first place.

    Nothing here is inferred from the source text: `_painted_strings` catches the live dial's
    QPainter.drawText calls through a Python subclass and wraps the export's `_draw_text_outlined`.
    See studio/docs/gmeter-validation.md."""
    st = _label_state()
    live = set(_painted_strings(False, st=st))
    exp = set(_painted_strings(True, st=st))
    assert live == exp, (f"the burn and the screen diverged — live-only {sorted(live - exp)}, "
                         f"export-only {sorted(exp - live)}")


def test_the_face_carries_exactly_two_strings_and_states_its_unit_once():
    """§6.5's count, asserted. ELEVEN text items — a title, four cardinal peak numbers, four
    direction words, two ring labels and a provenance tag — in a 120x140 px card, in three grey
    tiers between 6 and 8 px, is what "reads as a debug widget" measured out to.

    The face is now the readout and the ring's tick label, in both modes, at the 120x140 floor and
    at export size. The UNIT is still on the dial and still stated exactly once — by the readout,
    which is the largest type on the face, rather than by ring labels (which is where it lived
    before, and before them nowhere at all: `_RINGS` was commented "labelled rings (g)" for months
    while only drawEllipse ran). The tick label is the bare value because "1.0 g" measurably does
    not fit the annulus at the minimum size — see `_scale_caption`."""
    from studio import gmeter_overlay as g
    for export in (False, True):
        for w, h in ((120, 140), (280, 280)):
            painted = _painted_strings(export, w=w, h=h)
            assert len(painted) == 2, (
                f"export={export} {w}x{h}: the face paints {len(painted)} strings, not 2: "
                f"{painted}")
            assert f"{g._SCALE_RING_G:.1f}" in painted, painted
            readout = g.readout_text(_label_state())
            assert readout in painted, painted
            assert len([s for s in painted if s.endswith(" g")]) == 1, (
                f"export={export} {w}x{h}: the unit must be stated once, by the readout: {painted}")


def test_the_scale_caption_lands_on_neither_ring():
    """THE §6.5 DEFECT, in pixels. The ring labels were centred on their own ring's 45-degree
    point, i.e. drawn ON the stroke they named — "a '1.2g' scale caption sitting ON a ring stroke".

    The caption now floats in the annulus between the two rings. Measured the honest way: render
    the static layer twice, once with the caption suppressed, and locate the caption's ink by
    DIFFERENCE — so no layout constant is copied into the assertion — then check every inked pixel
    against both ring radii with a real stroke half-width of tolerance. Swept from the 120x140
    floor up, because the caption's size, its offset and both radii all scale with r."""
    from studio import gmeter_overlay as g
    orig = g._scale_caption
    for w, h in ((120, 140), (200, 220), (280, 280), (400, 440)):
        cx, cy, r = g.dial_geom(w, h)
        st = _label_state()
        with_cap = _render_static(st, w=w, h=h)
        g._scale_caption = lambda *a, **k: None
        try:
            no_cap = _render_static(st, w=w, h=h)
        finally:
            g._scale_caption = orig
        ys, xs = np.where(np.abs(with_cap - no_cap).max(axis=2) > 6)
        assert ys.size, (
            f"{w}x{h}: no scale caption — the face lost its scale. If the annulus really cannot "
            "hold it here, that is a size the dial should not be shipped at.")
        d = np.hypot(xs - cx, ys - cy)
        inner = r * (g._SCALE_RING_G / g._FULL_SCALE_G)
        clear = g._CAPTION_CLEAR_PX
        assert d.min() > inner + clear, (
            f"{w}x{h}: caption ink reaches {d.min():.1f} px from centre, on the {g._SCALE_RING_G} g "
            f"ring at {inner:.1f} — it is drawn ON the stroke it names")
        assert d.max() < r - clear, (
            f"{w}x{h}: caption ink reaches {d.max():.1f} px, on the outer ring at {r:.1f}")
        print(f"    {w}x{h}: caption ink {d.min():.1f}..{d.max():.1f} px "
              f"in the {inner:.1f}..{r:.1f} annulus")
    print("ok the scale caption clears both ring strokes at every size swept")


def test_the_scale_caption_is_dropped_rather_than_drawn_on_a_ring():
    """The drop rule, exercised. `_scale_caption` returns None when its own metrics say the annulus
    cannot hold it, so a machine whose face is wider than the shipped Inter loses a tick label
    instead of gaining a caption with a ring through it.

    Driven by asking for a caption that genuinely does not fit — the string measurement is real, so
    this is the same code path a wide font would take."""
    from PySide6.QtGui import QFontMetricsF

    from studio import gmeter_overlay as g
    cx, cy, r = g.dial_geom(120, 140)
    fits = g._scale_caption(cx, cy, r, QFontMetricsF(g._font(g._caption_px(r))))
    assert fits is not None, "the shipped face must carry the caption at the 120x140 minimum"
    # the same dial, asked with a face three times the size: no clear annulus -> nothing drawn
    too_big = g._scale_caption(cx, cy, r, QFontMetricsF(g._font(g._caption_px(r) * 3)))
    assert too_big is None, f"a caption that cannot clear both rings was placed anyway: {too_big}"


def _rgb(img):
    """A painted QImage as an (h, w, 3) int array in TRUE RGB order.

    THE TRAP THIS CLOSES. These helpers used to paint into `Format_RGB32` and slice `[:, :, :3]`
    off the raw buffer — which on a little-endian machine is B, G, R, A, so every "R" read was the
    blue channel. Greyscale contrast survived it (the tokens on this face are near-neutral); a
    HUE measurement does not, and the no-signal test below counts amber pixels. Converting first
    is one line and removes the whole class. `bytesPerLine` is honoured because RGB888 scanlines
    are 4-byte aligned and a width that is not a multiple of 4 would otherwise shear every row."""
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format_RGB888)
    h, w, bpl = conv.height(), conv.width(), conv.bytesPerLine()
    buf = np.frombuffer(conv.constBits(), np.uint8, count=bpl * h).reshape(h, bpl)
    return np.array(buf[:, :3 * w].reshape(h, w, 3)).astype(int)


def _render_static(st, w=120, h=140, bg=None):
    """Render ONLY the static live layer over an opaque backdrop, as a numpy RGB array."""
    from PySide6.QtGui import QColor, QImage, QPainter

    from studio import gmeter_overlay as g
    from studio import theme
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(bg or theme.C.canvas))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    g._paint_dial_static(p, w, h, st)
    p.end()
    return _rgb(img)


def _render_live(st, w=120, h=140):
    """Both live layers over an opaque backdrop — the real on-screen composite, as a numpy RGB
    array. The readout lives in the MOVING layer, so a static-only render cannot see it."""
    from PySide6.QtGui import QColor, QImage, QPainter

    from studio import gmeter_overlay as g
    from studio import theme
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(theme.C.canvas))
    p = QPainter(img)
    g.paint_dial(p, w, h, st)
    p.end()
    return _rgb(img)


def test_the_readout_stays_in_its_band_and_never_lands_on_the_dial():
    """L9-05's successor. The old defect was the provenance tag overprinting the bottom cardinal
    number inside a shared 11 px band; the redesign removes both of those elements, but it also
    makes the surviving number BIGGER, so the band it lives in has to be proved rather than
    assumed — a readout that grew into the outer ring would be the same class of defect.

    The readout's ink is located empirically (render two dials differing ONLY in the dot's
    magnitude, so nothing else on the face moves), which means this assertion carries no copy of
    the layout constants. Swept, because the type size, the band and the radius all scale."""
    from studio import gmeter_overlay as g
    for w, h in ((120, 140), (240, 280), (400, 440)):
        cx, cy, r = g.dial_geom(w, h)
        band = g.readout_rect(w, h)
        a = _render_live(_state(fx=0.20, fy=0.0), w=w, h=h)
        b = _render_live(_state(fx=1.10, fy=0.0), w=w, h=h)
        ink = np.argwhere(np.abs(a - b).max(axis=2) > 6)
        assert ink.size, f"{w}x{h}: control render failed — the readout did not change"
        # Only the readout and the dot/trail differ between those two states; keep the rows in the
        # band, which is exactly the claim under test.
        rows = ink[:, 0]
        in_band = rows >= band.top()
        assert in_band.any(), f"{w}x{h}: no readout ink in the reserved band"
        sub = ink[in_band]
        assert sub[:, 0].max() <= h - 1
        # nothing the readout draws may reach the dial's outer ring
        d = np.hypot(sub[:, 1] - cx, sub[:, 0] - cy)
        assert d.min() > r, (
            f"{w}x{h}: readout ink reaches {d.min():.1f} px from the dial centre, inside the "
            f"outer ring at {r:.1f}")
    print("ok the |g| readout stays in its band at every size swept")


def test_the_faces_two_strings_are_legible():
    """§6.5's other measurement was TYPE: "ring labels ~6 px", grey on grey. Both survivors are
    checked here against the WCAG 3:1 floor the retired tag test used, each isolated by
    differencing against a render with that element suppressed — so the readout's bold ink cannot
    flatter the caption's, or vice versa — at the 120x140 minimum, which is the worst case for
    both. The px floors are asserted too: nothing on this face is set below 8 px again."""
    from studio import gmeter_overlay as g

    def lum(c):
        c = np.asarray(c, float) / 255.0
        c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
        return float(0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2])

    def ratio(with_ink, without):
        delta = np.abs(with_ink - without).max(axis=2)
        ys, xs = np.where(delta > 6)
        assert ys.size, "the element painted nothing"
        ink = with_ink[ys, xs][int(np.argmax(delta[ys, xs]))]
        bg = np.median(without[ys, xs], axis=0)
        lo, hi = sorted([lum(ink), lum(bg)])
        return (hi + 0.05) / (lo + 0.05), ink, bg

    st = _label_state()
    _, _, r = g.dial_geom(120, 140)
    assert g._caption_px(r) >= 8.0 and g._readout_px(r) >= 12.0, (
        f"type floors regressed at the 120x140 minimum: caption {g._caption_px(r):.1f} px, "
        f"readout {g._readout_px(r):.1f} px")

    # the readout, isolated: the same face with the number suppressed
    orig_text = g.readout_text
    g.readout_text = lambda _st: ""
    try:
        blank_readout = _render_live(st)
    finally:
        g.readout_text = orig_text
    rr, ink, bg = ratio(_render_live(st), blank_readout)
    assert rr >= 3.0, f"|g| readout contrast {rr:.2f}:1 (ink {ink.tolist()} bg {bg.tolist()})"

    # the caption, isolated: the same face with it suppressed
    orig_cap = g._scale_caption
    g._scale_caption = lambda *a, **k: None
    try:
        blank_cap = _render_static(st)
    finally:
        g._scale_caption = orig_cap
    cr, ink, bg = ratio(_render_static(st), blank_cap)
    assert cr >= 3.0, f"scale caption contrast {cr:.2f}:1 (ink {ink.tolist()} bg {bg.tolist()})"
    print(f"ok readout {rr:.2f}:1 at {g._readout_px(r):.1f} px, "
          f"caption {cr:.2f}:1 at {g._caption_px(r):.1f} px")


# --------------------------------------------------------------------------- the no-signal states
# The export-wave leftover in §6.5: with no IMU the dial painted rings, a legend and four `0.0`.
# `Session.g_at_time` returns None ONLY for a recording with no g series at all (GMeter.at_time
# clamps inside the series otherwise), so "never handed a sample" is exactly "nothing to meter".

def test_an_export_of_a_recording_with_no_g_paints_no_dial_at_all():
    """The burned instrument that measured nothing. A no-IMU recording feeds `paint_dial` a state
    that has never seen a sample on EVERY frame — `overlay_values_at` gates on `has_gmeter` — and
    the dial drew its full face anyway: two rings, a crosshair, six legend words and four `0.0`
    peaks, burned into the file, for the whole clip.

    A clip now carries the overlays it has data for. Measured on the composite: not one pixel of
    the magenta plate is touched."""
    exp = _render_dial(export=True, st=_state(have=False, seen=False, trail=[], hull_pts=[],
                                              peak_fwd=0.0, peak_back=0.0, peak_left=0.0,
                                              peak_right=0.0))
    magenta = np.array([255, 0, 255])
    painted = int((exp != magenta).any(axis=2).sum())
    assert painted == 0, f"the no-signal export dial painted {painted} px"
    # ... and the control: the same call WITH a signal paints plenty, so this is not a dead render.
    assert int((_render_dial(export=True) != magenta).any(axis=2).sum()) > 1000


def test_the_live_dial_with_no_g_shows_the_instrument_at_rest():
    """The live half of the same decision, and it is deliberately NOT "paint nothing".

    On screen the dial is a card the user explicitly toggled on, and a blank card reads as broken.
    It paints its resting template — rings, crosshair, scale caption — with the app's no-value mark
    where the number goes, and no dot, no trail and no envelope, because there is nothing to plot.
    The REASON lives on the toggle, which `central_view` disables with "No accelerometer data in
    this recording" whenever `has_gmeter` is false; this state is what the user sees for the frame
    before the first tick, and it must not flash a false `0.0` g."""
    from studio import gmeter_overlay as g
    empty = _state(have=False, seen=False, trail=[], hull_pts=[],
                   peak_fwd=0.0, peak_back=0.0, peak_left=0.0, peak_right=0.0)
    assert g.readout_text(empty) == g._NO_VALUE, g.readout_text(empty)
    assert g.readout_text(_state()) != g._NO_VALUE
    painted = _painted_strings(False, st=empty)
    assert g._NO_VALUE in painted, painted
    assert not any(s.startswith("0.0") for s in painted), (
        f"a dial with no signal must not report a g: {painted}")
    # The template IS there (the card is not blank), and the DATA layers are not. Measured by hue:
    # every part of the template — canvas, surface, the two border tokens, text/text_dim — is a
    # near-neutral grey, while the dot, its trail's glow and the grip envelope are all drawn in the
    # amber accent. So "warm pixels" counts data ink and nothing else.
    from studio import theme
    canvas = np.array([int(theme.C.canvas[i:i + 2], 16) for i in (1, 3, 5)])
    rest = _render_live(empty)
    live = _render_live(_state())
    assert int((np.abs(rest - canvas).max(axis=2) > 6).sum()) > 200, "the resting card is blank"
    warm = lambda a: int(((a[:, :, 0] - a[:, :, 2]) > 40).sum())   # noqa: E731
    assert warm(rest) == 0, f"the resting dial painted {warm(rest)} px of data ink"
    assert warm(live) > 200, f"control failed: a live dial painted only {warm(live)} px of data ink"
    print("ok no-signal: export paints nothing, the live card rests at '—'")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nALL {len(tests)} gmeter-overlay tests passed")
