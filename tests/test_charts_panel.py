"""Charts-panel honesty + legibility (QA-sweep batch B19: L6-02, L6-03, L6-04, L6-06, L6-07).

Five ways the speed/Δ panel said something it could not back up:

  * L6-02 — the "Ideal lap" toggle was a 0-of-441 077-pixel no-op in the app's OWN default state
    (the session best selected alone), because that case already re-references the lower chart to
    the ideal, so `_draw_ideal` early-returns — while the button happily latched amber.
  * L6-03 — the colour-blind palette deliberately does NOT touch `CHART_SERIES` (those are IDENTITY
    colours, not ahead/behind semantics), which is the right call — but hue was then the ONLY cue
    mapping a legend row to a curve, and two of the six hues sit UNDER the deuteranopic JND. The fix
    is a second channel that needs no colour at all, not a recolour: one dash pattern per slot.
  * L6-04 — the ESTIMATED brake/throttle strip is drawn inside the SPEED y-axis, so pyqtgraph printed
    a `20` km/h tick through a pedal trace, below the lap's true 28.5 km/h minimum. The band is not a
    speed; the axis said it was.
  * L6-06 — the legend plate sat top-left, the corner every fixture measured as the fullest (a lap
    starts flat out), hiding 413 of 2800 plotted samples; and its one escape — it is draggable — was
    unhinted, no cursor, no tooltip.
  * L6-07 — the empty state explained the cause, offered no way out, and left all three chart
    controls live and latching over a page that cannot redraw.

Real PlotsView, real pyqtgraph draw, real Qt (offscreen) over a narrow stub Session — the tests need
states the two-lap stadium synthetic cannot reach (six overlaid laps, zero laps, a populated pedal
band), and every number asserted here is read back off the widgets the app actually paints.

Run: QT_QPA_PLATFORM=offscreen python tests/test_charts_panel.py
"""
import ast
import itertools
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

# The deuteranopia/Lab maths comes from the theme batch's contrast guard rather than a second matrix
# derived here, so the two files can never disagree about what "indistinguishable" means.
from test_contrast import JND, _dE, _deut, _hx  # noqa: E402

from studio import plots_view, theme  # noqa: E402


# ------------------------------------------------------------------ the narrow Session stub
class _StubSession:
    """Exactly the Session surface PlotsView reads, over hand-built lap arrays.

    `laps` is {lap_id: (xs, speed_kmh, delta_s)}. Everything else (corner math, timing, media) is
    outside this widget's reach, so it is not faked."""

    def __init__(self, laps, best=None, ideal=True, reference=None):
        self._laps = {int(k): tuple(np.asarray(a, float) for a in v) for k, v in laps.items()}
        self._best = best
        self._ideal = ideal
        # F7: the cross-recording reference's source label, or None. When set, `delta()` reports
        # REFERENCE_ID as its baseline id, which is what makes the lower chart's baseline that
        # other recording's lap (plots_view.refresh).
        self._reference = reference

    def has_reference(self):
        return self._reference is not None

    def reference_label(self):
        return self._reference

    def reference_lap_time(self):
        return 68.2

    def best_lap_id(self):
        return self._best

    def delta(self, ids, x_mode="distance"):
        # With a reference loaded the baseline id IS the REFERENCE_ID sentinel, and its curve comes
        # back alongside the local laps' (mirrors Session.delta's reference branch).
        pool = dict(self._laps)
        if self._reference is not None:
            first = next(iter(self._laps.values()))
            pool[plots_view.REFERENCE_ID] = (first[0], first[1] - 2.0, first[2] + 0.3)
        sel = [i for i in ids if i in pool]
        if not sel:
            return None
        speed = {i: (pool[i][0], pool[i][1]) for i in sel}
        delta = {i: (pool[i][0], pool[i][2]) for i in sel}
        base = plots_view.REFERENCE_ID if self._reference is not None else self._best
        return base, speed, delta

    def delta_to_ideal(self, ids, x_mode="distance"):
        if not self._ideal:
            return None
        return {i: (self._laps[i][0], self._laps[i][2] - 0.35) for i in ids if i in self._laps}

    def ideal_delta_to_best(self, x_mode="distance"):
        if not self._ideal or self._best is None:
            return None
        xs = self._laps[self._best][0]
        return xs, -0.2 - 0.3 * np.sin(np.linspace(0, np.pi, len(xs)))

    def lap_time(self, lid):
        return 70.0 + 0.1 * lid

    def active_baseline_total_distance(self):
        return 1000.0

    def lap_window(self, lid):
        return None


def _laps(n, points=400):
    """n laps of a plausible speed profile (30..90 km/h) on a shared 0..1000 m x-grid."""
    xs = np.linspace(0.0, 1000.0, points)
    out = {}
    for lid in range(n):
        speed = 60.0 + 30.0 * np.sin(np.linspace(0, 3 * np.pi, points)) - 0.4 * lid
        out[lid] = (xs, speed, np.linspace(0.0, 0.2 * lid, points))
    return out


def _view(n=6, best=0, ideal=True, size=(900, 520), select=None, reference=None):
    """A real, laid-out PlotsView over n stub laps, refreshed and settled."""
    v = plots_view.PlotsView(_StubSession(_laps(n), best=best, ideal=ideal, reference=reference))
    v.resize(*size)
    v.show()
    v.set_laps(range(n) if select is None else select)
    for _ in range(4):
        _APP.processEvents()
    return v


def _speed_pens(v):
    """(name, #rrggbb, width, style, dash pattern) for every curve drawn on the SPEED plot."""
    return [(c.name(), c.opts["pen"].color().name(), c.opts["pen"].width(),
             c.opts["pen"].style(), tuple(c.opts["pen"].dashPattern() or ()))
            for plot, c in v._curves if plot is v.p_speed]


# ============================================================================ L6-02
def test_ideal_toggle_is_live_only_where_it_can_draw():
    """The toggle is enabled exactly when clicking it would change the chart.

    With the best lap drawn ALONE the lower chart is already Δ-to-IDEAL (P7), so `_draw_ideal`
    early-returns and the click drew nothing while the button lit amber — measured on the real app at
    0 of 441 077 RGB pixels. It now goes disabled and its own tooltip carries the reason. Selecting a
    second lap restores both the toggle and the tooltip it shipped with."""
    v = _view(n=3, best=0, select=[0])
    assert v._delta_ideal_mode is True, "setup: the best lap alone must enter Δ-to-ideal mode"
    assert v.ideal_btn.isEnabled() is False, "the ideal toggle is inert here, so it must be dead"
    tip = v.ideal_btn.toolTip()
    assert tip.endswith(plots_view.IDEAL_IS_BASELINE_TIP), tip
    assert "second lap" in tip.lower(), tip
    # the reason is APPENDED: the header elides this button to its icon, so the label must survive
    assert tip.startswith("Ideal lap:"), tip
    # a click on a dead button cannot latch it
    v.ideal_btn.click()
    for _ in range(2):
        _APP.processEvents()
    assert v.ideal_btn.isChecked() is False and v._show_ideal is False

    # ... and the control state: two laps, the toggle is live again and really draws.
    v.set_laps([0, 1])
    for _ in range(4):
        _APP.processEvents()
    assert v._delta_ideal_mode is False
    assert v.ideal_btn.isEnabled() is True
    assert v.ideal_btn.toolTip().startswith("Ideal lap:"), "the original tooltip must come back"
    before = len([1 for plot, _c in v._curves if plot is v.p_delta])
    v.ideal_btn.click()
    for _ in range(4):
        _APP.processEvents()
    assert len([1 for plot, _c in v._curves if plot is v.p_delta]) == before + 1
    v.deleteLater()
    print("test_ideal_toggle_is_live_only_where_it_can_draw OK")


# ============================================================================ L6-03
def test_identity_curves_carry_a_cue_that_survives_deuteranopia():
    """No two curves the panel can draw at once are separated by colour ALONE below the JND.

    `CHART_SERIES` is documented palette-independent and stays that way (see the guard below), so the
    accessibility has to come from somewhere that is not hue: each palette slot now owns a dash
    pattern, which pyqtgraph strokes into its 20 px legend sample as well as the curve. Shipped, all
    seven pens were `SolidLine` and #B794F6 vs #7FA8F5 was CIE76 dE 1.27 under the repo's own
    Machado-1.0 deuteranopia matrix — half the ~2.3 JND."""
    for palette in (theme.PALETTE_STANDARD, theme.PALETTE_COLORBLIND):
        try:
            theme.set_palette(palette)
            # the app's richest legitimate state: 6 selected identity laps + the always-on best
            v = _view(n=7, best=6, select=range(6))
            pens = _speed_pens(v)
            assert len(pens) == 7, pens
            collisions = []
            for a, b in itertools.combinations(pens, 2):
                same_cue = a[3] == b[3] and a[4] == b[4]     # same style AND same dash pattern
                if same_cue and _dE(_deut(_hx(a[1])), _deut(_hx(b[1]))) < JND:
                    collisions.append(f"{a[0]} {a[1]} vs {b[0]} {b[1]}")
            assert not collisions, (
                f"[{palette}] curves distinguishable only by a sub-JND hue: {collisions}")
            # the cue is really carried per SLOT, so it is stable across redraws, not per selection
            assert len({p[4] for p in pens}) == len(plots_view.SERIES_DASH)
            v.deleteLater()
        finally:
            theme.set_palette(theme.PALETTE_STANDARD)
    print("test_identity_curves_carry_a_cue_that_survives_deuteranopia OK")


def test_chart_series_stays_palette_independent():
    """The INTENT guard, not a regression: `CHART_SERIES` says WHICH LAP, not who is faster, so
    U10-01 deliberately left it out of the palette switch and L6-03 must not smuggle it back in. The
    best-lap hue is the one that follows the palette, and it is resolved per draw, not from this
    list."""
    try:
        theme.set_palette(theme.PALETTE_STANDARD)
        std = list(theme.CHART_SERIES)
        std_best = theme.best_lap_colour()
        theme.set_palette(theme.PALETTE_COLORBLIND)
        assert list(theme.CHART_SERIES) == std, "the identity palette must not follow set_palette"
        assert theme.best_lap_colour() != std_best, "...but the best-lap hue must"
        assert plots_view.PALETTE is theme.CHART_SERIES
        assert len(plots_view.SERIES_DASH) == len(theme.CHART_SERIES), "one dash cue per slot"
        assert plots_view.SERIES_DASH[0] is None, "slot 0 stays solid: the 1-lap default is unchanged"
    finally:
        theme.set_palette(theme.PALETTE_STANDARD)
    print("test_chart_series_stays_palette_independent OK")


def _brake_items(v, n_laps=4):
    """Push one brake series per identity lap (the state central_view builds) and return the drawn
    ScatterPlotItems."""
    data = [([(300.0 + 40 * k, 0.30)], theme.CHART_SERIES[k], k) for k in range(n_laps)]
    v.set_brake_markers(data)
    for _ in range(4):
        _APP.processEvents()
    return v.plots._brake_items if hasattr(v, "plots") else v._brake_items


def test_brake_glyphs_carry_a_shape_channel_that_survives_deuteranopia():
    """W4-02. The dash channel above reaches the CURVES and the legend, but a filled brake marker
    has no stroke to dash — so on the glyph layer hue was still the only identity cue, and hue is
    exactly what a deuteranope does not have: CHART_SERIES slot 2 vs slot 3 is CIE76 dE 1.27 under
    the repo's Machado-1.0 matrix (26.27 to normal vision), and any 4-lap selection reaches it.

    Shipped, `{it.opts['symbol'] for it in _brake_items}` was exactly `{'t'}`: one triangle in six
    hues. The contract now is the same one the curves have — no two glyph series the panel can draw
    at once may be separated by a sub-JND hue ALONE."""
    v = _view(n=7, best=6, select=range(6))
    items = _brake_items(v, n_laps=6)
    assert len(items) == 6, items
    symbols = [it.opts["symbol"] for it in items]
    colours = [it.opts["brush"].color().name() for it in items]
    # the shape channel is real: as many distinct symbols as there are distinct colours
    assert len(set(symbols)) == len(set(colours)) == 6, (symbols, colours)
    collisions = []
    for (sa, ca), (sb, cb) in itertools.combinations(zip(symbols, colours, strict=True), 2):
        if sa == sb and _dE(_deut(_hx(ca)), _deut(_hx(cb))) < JND:
            collisions.append(f"{sa} {ca} vs {sb} {cb}")
    assert not collisions, f"brake glyphs separated only by a sub-JND hue: {collisions}"
    # ...and the glyphs are OUTLINED, so two overlapping laps' markers cannot fuse into one blob
    # (with pen=None they did, which is what makes the shape unreadable exactly where it matters).
    for it in items:
        pen = it.opts["pen"]
        assert pen is not None and pen.style() != Qt.NoPen, "brake glyphs need a separating outline"
    # the cue is per SLOT, so it is stable across selections rather than per draw order
    assert len(theme.SERIES_SYMBOL) == len(theme.CHART_SERIES), "one glyph shape per identity slot"
    assert theme.SERIES_SYMBOL[0] == "t", "slot 0 keeps the shipped triangle (1-lap default)"
    assert theme.series_symbol(theme.CHART_SERIES[2]) != theme.series_symbol(theme.CHART_SERIES[3]), (
        "the dE 1.27 pair is the one that HAS to be carried by shape")
    v.deleteLater()
    print("test_brake_glyphs_carry_a_shape_channel_that_survives_deuteranopia OK")


def _at_dpr(v, dpr):
    """Pretend the window moved to a screen at `dpr` and deliver Qt's own notification.

    The widget's device-pixel ratio is what the handler reads, so overriding it and firing the real
    QEvent exercises the shipped path rather than a test-only hook."""
    v.devicePixelRatioF = lambda: dpr
    v.event(QEvent(QEvent.Type.DevicePixelRatioChange))
    for _ in range(4):
        _APP.processEvents()


def test_chart_line_weights_are_logical_pixels_not_device_pixels():
    """W5-01. pyqtgraph's mkPen sets `setCosmetic(True)`, so a pen width is in DEVICE pixels and Qt
    never multiplies it by the device-pixel ratio. Shipped, every chart line therefore kept the same
    DEVICE width at any DPR — measured on a fixed 1512x982 LOGICAL screen, the Δ-plot gridlines drew
    1.0 logical px at DPR 1 and 0.5 at DPR 2, and the always-on best-lap trace (width=1, deliberately
    the thinnest line in the app) became a 0.5-logical-px hairline in the window, in the exported
    HTML report and on the shared lap card.

    Every width now goes through theme.line_width, so a design weight of N logical px is N device px
    at DPR 1 and 2N at DPR 2 — the same rendered weight, which is what the Qt widgets beside the
    chart already do.

    The DPR is a property of the SCREEN THE WINDOW IS ON, so this is asserted BOTH ways: dragging
    the window to a denser screen must thicken the pens, and dragging it back must thin them again.
    A pen built once at import could only ever be right on one of the two."""
    try:
        assert theme.line_width(2) == 2.0, "unscaled: a logical width is a device width at DPR 1"
        v = _view(n=7, best=6, select=range(6))
        # constructed on this (DPR 1) screen: the shipped weights, unchanged
        widths = {n: w for n, _c, w, _s, _d in _speed_pens(v)}
        assert {w for n, w in widths.items() if "best" not in n} == {2}, widths
        assert {w for n, w in widths.items() if "best" in n} == {1}, widths
        assert v.p_delta.getAxis("left").pen().widthF() == 1.0
        sizes_at_1 = {sp["size"] for it in _brake_items(v) for sp in it.data}

        _at_dpr(v, 2.0)                                   # dragged onto the Retina panel
        assert theme.pen_scale() == 2.0
        widths = {n: w for n, _c, w, _s, _d in _speed_pens(v)}
        assert {w for n, w in widths.items() if "best" not in n} == {4}, widths
        assert {w for n, w in widths.items() if "best" in n} == {2}, widths
        # the GRIDLINES too — they are drawn with the axis pen (AxisItem falls back from tickPen()
        # to pen()), which is the line the 1.0 -> 0.5 logical px defect was measured on.
        for plot, side in ((v.p_speed, "left"), (v.p_delta, "left"), (v.p_delta, "bottom")):
            assert plot.getAxis(side).pen().widthF() == 2.0, side
        # the scrub cursor is NOT rebuilt by refresh(), so the handler must re-pen it explicitly
        assert v.cur_speed.pen.widthF() == 2.0
        # ...and the glyph SIZE must NOT be scaled: a pxMode=True scatter size is already
        # device-independent (measured: size 18 draws a 19x19 LOGICAL box at both DPRs), so
        # scaling it as well would draw double-size brake markers on a Retina panel.
        assert {sp["size"] for it in _brake_items(v) for sp in it.data} == sizes_at_1

        _at_dpr(v, 1.0)                                   # ...and back to the external monitor
        widths = {n: w for n, _c, w, _s, _d in _speed_pens(v)}
        assert {w for n, w in widths.items() if "best" not in n} == {2}, widths
        assert v.p_delta.getAxis("left").pen().widthF() == 1.0
        assert v.cur_speed.pen.widthF() == 1.0
        v.deleteLater()
    finally:
        theme.set_pen_scale(1.0)
    print("test_chart_line_weights_are_logical_pixels_not_device_pixels OK")


def test_no_chart_or_map_pen_hardcodes_a_device_pixel_width():
    """The guard that keeps W5-01 fixed. A bare `width=<number>` on a pyqtgraph pen is a DEVICE-pixel
    width and is the whole defect, so in the two files that draw the charts and the map every width
    must be a `theme.line_width(...)` call. AST-based: a grep would trip over the comments that
    explain this."""
    offenders = []
    for fn in ("plots_view.py", "map_view.py"):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "studio", fn)
        tree = ast.parse(open(path, encoding="utf-8").read(), fn)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not (isinstance(f, ast.Attribute) and f.attr in ("mkPen", "setPen")):
                continue
            for kw in node.keywords:
                if kw.arg != "width":
                    continue
                v = kw.value
                ok = (isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute)
                      and v.func.attr == "line_width")
                if not ok:
                    offenders.append(f"{fn}:{node.lineno} width={ast.unparse(v)}")
    assert not offenders, (
        "pyqtgraph pen widths are in DEVICE pixels — route them through theme.line_width so they "
        f"keep their weight on a Retina panel: {offenders}")
    print("test_no_chart_or_map_pen_hardcodes_a_device_pixel_width OK")


# ============================================================================ L6-04
def _band_on(v):
    """Turn the pedal band on over one lap's worth of synthetic intensity and settle."""
    xs = v.session._laps[0][0]
    v.set_brake_throttle([(xs, np.sin(np.linspace(0, 6 * np.pi, len(xs))))])
    v.brake_throttle_btn.setChecked(True)
    for _ in range(4):
        _APP.processEvents()
    assert v._bt_band_range is not None, "setup: the band must have reserved its strip"
    return v._bt_band_range


def test_speed_axis_never_ticks_inside_the_estimated_pedal_band():
    """The band is a pedal ESTIMATE, not a speed, so the km/h axis must not label its space.

    On the real app this printed `20` inside a strip spanning 11.66..22.24 km/h on a lap whose true
    minimum was 28.5 km/h. Asserted through `tickValues`, which is what the axis paints from, and in
    BOTH states so the fix cannot work by simply suppressing ticks all the time."""
    v = _view(n=2, best=0, select=[0])
    axis, vb = v.p_speed.getAxis("left"), v.p_speed.getViewBox()

    def tick_values():
        lo, hi = vb.viewRange()[1]
        return sorted({float(t) for _sp, vals in axis.tickValues(lo, hi, vb.height()) for t in vals})

    off = tick_values()
    assert off, "setup: the axis must tick at all with the band off"

    lo_b, hi_b = _band_on(v)
    inside = [t for t in tick_values() if lo_b <= t <= hi_b]
    assert not inside, f"km/h ticks {inside} printed inside the pedal strip ({lo_b:.2f}..{hi_b:.2f})"
    # ...and everything the speed curve actually occupies is still ticked
    assert [t for t in tick_values() if t > hi_b], "the fix must not de-tick the speed range too"
    assert v._speed_axis.band_top == hi_b

    # off again -> pyqtgraph's own choice is restored untouched
    v.brake_throttle_btn.setChecked(False)
    for _ in range(4):
        _APP.processEvents()
    assert v._speed_axis.band_top is None
    assert tick_values() == off
    v.deleteLater()
    print("test_speed_axis_never_ticks_inside_the_estimated_pedal_band OK")


def test_pedal_band_names_itself_on_the_chart():
    """With the axis no longer labelling that space, the strip has to say what it is — in the app's
    ONE canonical estimated marker, and only while it is drawn."""
    v = _view(n=2, best=0, select=[0])

    def captions():
        return [it.toPlainText() for it in v._brake_throttle_items if hasattr(it, "toPlainText")]

    assert captions() == []
    _band_on(v)
    assert captions() == [theme.estimated_label("brake / throttle")], captions()
    assert theme.ESTIMATED_MARK in captions()[0]
    v.brake_throttle_btn.setChecked(False)
    for _ in range(4):
        _APP.processEvents()
    assert captions() == []
    v.deleteLater()
    print("test_pedal_band_names_itself_on_the_chart OK")


# ============================================================================ L6-06
def test_legend_sits_clear_of_the_lap_start_and_says_it_can_be_moved():
    """The plate is anchored to the emptiest measured corner, and its one escape is hinted.

    Top-left is where a lap's trace lives (you cross the line flat out), and there the plate hid 413
    of 2800 plotted samples on F.A, 11.9 % on F.D and 7.8 % on F.C; anchored top-RIGHT those became
    9.6 / 0.0 / 0.0 %. pyqtgraph has always implemented `mouseDragEvent` on LegendItem, but with no
    cursor change and no tooltip nothing said so."""
    v = _view(n=7, best=6, select=range(6))
    leg = v._speed_legend
    plate = leg.sceneBoundingRect()
    box = v.p_speed.getViewBox().sceneBoundingRect()
    assert plate.center().x() > box.center().x(), (
        f"legend centred at x={plate.center().x():.0f}, right of {box.center().x():.0f} expected")
    assert plate.top() < box.center().y(), "it should stay in the TOP half"
    assert plots_view.LEGEND_OFFSET[0] < 0, "a negative x offset is what anchors it to the far edge"
    assert leg.hasCursor() and leg.cursor().shape() == Qt.OpenHandCursor
    assert leg.toolTip(), "the drag affordance must be stated somewhere"
    v.deleteLater()
    print("test_legend_sits_clear_of_the_lap_start_and_says_it_can_be_moved OK")


def test_legend_hide_threshold_sits_at_the_apps_own_ceiling():
    """The hide guard has to be reachable to mean anything.

    The lap table trims a selection to MAX_COMPARE_LAPS = 6 and the panel always adds the best lap,
    so 7 rows is the most the app can produce. The shipped threshold of 8 was one row ABOVE that, so
    it could never fire; 7 keeps the largest legitimate legend and still hides a pathological
    programmatic one. Lowering it further would be self-defeating — a hidden legend is exactly the
    curve-identity loss L6-03 is about."""
    from studio.lap_table import MAX_COMPARE_LAPS
    assert plots_view.LEGEND_MAX_ROWS == MAX_COMPARE_LAPS + 1, (
        f"{plots_view.LEGEND_MAX_ROWS} is not the app's own ceiling of {MAX_COMPARE_LAPS + 1}")
    # the largest legitimate selection (6 laps + the always-on best) still keeps its legend...
    v = _view(n=MAX_COMPARE_LAPS + 1, best=MAX_COMPARE_LAPS, select=range(MAX_COMPARE_LAPS))
    assert len(v._speed_legend.items) == MAX_COMPARE_LAPS + 1
    assert v._speed_legend.isVisible() is True
    # ...and one row past it is dropped rather than blanketing the chart.
    v.session = _StubSession(_laps(MAX_COMPARE_LAPS + 3), best=MAX_COMPARE_LAPS + 2)
    v.set_laps(range(MAX_COMPARE_LAPS + 1))
    for _ in range(4):
        _APP.processEvents()
    assert len(v._speed_legend.items) == MAX_COMPARE_LAPS + 2
    assert v._speed_legend.isVisible() is False
    v.deleteLater()
    print("test_legend_hide_threshold_sits_at_the_apps_own_ceiling OK")


def test_empty_state_offers_a_next_action_and_disables_the_inert_controls():
    """L6-07. The placeholder names the way out, and nothing on the bar latches over a blank page.

    Shipped, all three controls stayed enabled and really did latch: the panel was byte-identical
    after toggling both buttons and switching the axis (0 of 441 077 RGB pixels), with the two
    buttons left reading "on"."""
    v = plots_view.PlotsView(_StubSession({}, best=None))
    v.resize(900, 520)
    v.show()
    v.set_laps([])
    for _ in range(4):
        _APP.processEvents()
    assert v._stack.currentIndex() == 1, "setup: the placeholder page must be up"
    text = v._empty.text()
    assert "start/finish line" in text and "map" in text, text
    for name in ("x_mode_combo", "ideal_btn", "brake_throttle_btn"):
        ctl = getattr(v, name)
        assert ctl.isEnabled() is False, f"{name} is live over a chart that cannot draw"
        assert ctl.toolTip().endswith(plots_view.NO_DATA_TIP), f"{name} never says why it is dead"
    v.ideal_btn.click()
    v.brake_throttle_btn.click()
    for _ in range(4):
        _APP.processEvents()
    assert not v.ideal_btn.isChecked() and not v.brake_throttle_btn.isChecked()

    # ...and they all come back, with their own tooltips, as soon as there is something to plot.
    v.session = _StubSession(_laps(2), best=0)
    v.set_laps([0, 1])
    for _ in range(4):
        _APP.processEvents()
    assert v._stack.currentIndex() == 0
    for name, lead in (("x_mode_combo", None), ("ideal_btn", "Ideal lap:"),
                       ("brake_throttle_btn", "Brake/Throttle band")):
        ctl = getattr(v, name)
        assert ctl.isEnabled() is True, name
        if lead:
            assert ctl.toolTip().startswith(lead), ctl.toolTip()
    v.deleteLater()
    print("test_empty_state_offers_a_next_action_and_disables_the_inert_controls OK")


# ============================================================================ QA-W2R-03
def test_the_delta_axis_names_the_reference_it_is_measured_against():
    """QA-W2R-03. With a cross-recording reference loaded, plots_view.refresh() DOES swap the
    baseline to REFERENCE_ID — and then painted "Δ to best (s)" over it, three inches under a
    legend reading "ref recording 0059 · 3 chapters". The same panel named two different baselines
    with the same word, and "best" was the wrong one.

    The wording is short by necessity: this is a LEFT axis, so pyqtgraph rotates the label and its
    length is spent VERTICALLY, in a Δ plot ~124 px tall at 1280x800. Spelling the recording out
    here measures 211 px and collides with the speed plot's own label above it, so the axis
    abbreviates and the recording lives on the axis's hover (and on the legend, which already
    names it)."""
    v = _view(n=2, best=0, reference="recording 0059 · 3 chapters", select=[0])
    axis = v.p_delta.getAxis("left")
    assert v._delta_baseline_kind == plots_view.DELTA_BASELINE_REFERENCE, v._delta_baseline_kind
    assert axis.labelText == plots_view.DELTA_LABEL_REF, axis.labelText
    assert "best" not in axis.labelText, axis.labelText
    # The abbreviation has the recording behind it, reachable without reading the legend.
    assert "recording 0059 · 3 chapters" in axis.toolTip(), axis.toolTip()
    # It is NARROWER than the label it replaces, so it cannot make the rotated-label collision the
    # reporter measured any worse (measured 73 px -> 63 px).
    fm = QFontMetrics(v.font())
    assert (fm.horizontalAdvance(plots_view.DELTA_LABEL_REF)
            <= fm.horizontalAdvance(plots_view.DELTA_LABEL_BEST)), (
        plots_view.DELTA_LABEL_REF, plots_view.DELTA_LABEL_BEST)
    print(f"test_the_delta_axis_names_the_reference_it_is_measured_against OK "
          f"({axis.labelText!r})")


def test_the_baseline_signal_carries_the_kind_not_a_two_state_flag():
    """The cause behind every mis-named caption: the baseline was plumbed to the header as
    `deltaBaselineChanged(bool)` — best or ideal — so the reference had nowhere to be reported and
    arrived as "not ideal", i.e. "best". It must carry the KIND, and all three must round-trip."""
    seen = []
    v = _view(n=2, best=0, select=[0, 1])
    v.deltaBaselineChanged.connect(seen.append)
    assert v._delta_baseline_kind == plots_view.DELTA_BASELINE_BEST
    v.set_laps([0])                       # best alone -> the P7 ideal swap
    assert seen[-1] == plots_view.DELTA_BASELINE_IDEAL, seen
    v.set_laps([0, 1])
    assert seen[-1] == plots_view.DELTA_BASELINE_BEST, seen

    ref = _view(n=2, best=0, reference="recording 0059 · 3 chapters", select=[0, 1])
    got = []
    ref.deltaBaselineChanged.connect(got.append)
    ref.session._reference = None         # clear the reference under a live view
    ref.refresh()
    assert got == [plots_view.DELTA_BASELINE_BEST], got
    ref.session._reference = "recording 0059 · 3 chapters"
    ref.refresh()
    assert got[-1] == plots_view.DELTA_BASELINE_REFERENCE, got
    print(f"test_the_baseline_signal_carries_the_kind_not_a_two_state_flag OK ({seen} / {got})")


def _run_all():
    test_the_delta_axis_names_the_reference_it_is_measured_against()
    test_the_baseline_signal_carries_the_kind_not_a_two_state_flag()
    test_ideal_toggle_is_live_only_where_it_can_draw()
    test_identity_curves_carry_a_cue_that_survives_deuteranopia()
    test_chart_series_stays_palette_independent()
    test_brake_glyphs_carry_a_shape_channel_that_survives_deuteranopia()
    test_chart_line_weights_are_logical_pixels_not_device_pixels()
    test_no_chart_or_map_pen_hardcodes_a_device_pixel_width()
    test_speed_axis_never_ticks_inside_the_estimated_pedal_band()
    test_pedal_band_names_itself_on_the_chart()
    test_legend_sits_clear_of_the_lap_start_and_says_it_can_be_moved()
    test_legend_hide_threshold_sits_at_the_apps_own_ceiling()
    test_empty_state_offers_a_next_action_and_disables_the_inert_controls()
    print("\nAll charts-panel tests passed.")


if __name__ == "__main__":
    _run_all()
