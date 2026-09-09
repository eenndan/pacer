"""AN AXIS TITLE IS TEXT, and this file measures it as text — on the pixels that reach the screen.

`speed (km/h)`, `Δ to best (s)` and `distance (m)` shipped painting rgb(45,50,60) on the
rgb(33,37,46) plot background: **1.19:1**, against 5.7:1 for the tick labels 20 px away and 8.5:1
for the legend plate. Functionally invisible at every window size the app has ever opened at — the
charts panel had three unnamed axes and nothing in the suite could tell.

WHY IT SURVIVED, and why the obvious fix would not have held. pyqtgraph gives THREE calls a claim
on one colour. `AxisItem.labelString` bakes `self.labelStyle` into the label's HTML, and BOTH
`setPen` (the axis LINE) and `setTextPen` (the tick TEXT) end with

    self.labelStyle['color'] = <that pen>.color().name()
    self._updateLabel()

so whichever ran LAST owns the title. `plots_view` styled the text pen in its constructor and then
called `_apply_axis_pens()` — the line pen, a HAIRLINE — one line later, and again on every
device-pixel-ratio change. Passing `color=` at the four `setLabel` sites is therefore only half a
fix: correct on the frame it runs, undone by the next palette or screen change. Both halves are in
`studio/plots_view._axis_title_style`, and the same door was open on the Stats friction circle,
whose two titles were repainted in the hairline colour by `StatsView._apply_pen_scale`.

So the assertions here are made against a RENDERED FRAME rather than against the style string: the
review's own measurement was pixels, the defect is a colour arriving from an unexpected writer, and
a source-level check would have passed on the shipped build. A negative control re-opens the defect
on a live widget and proves the measurement can still see it.

Run: QT_QPA_PLATFORM=offscreen python tests/test_chart_axis_legibility.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pyqtgraph as pg  # noqa: E402
from _qtapp import themed_app  # noqa: E402
from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402

_APP = themed_app()      # the THEME is load-bearing: it sets pyqtgraph's own fg/bg config

from studio import theme  # noqa: E402
from studio.theme import C  # noqa: E402

# WCAG AA for body text. An axis title is small text on a chart, and the app holds all 33 of its
# enabled text styles to this floor already (tests/test_contrast.py) — a title painted INSIDE a
# chart is not a lower tier of writing than one painted beside it.
AA_FLOOR = 4.5
#: How far a painted stroke may sit from the colour it was asked for, summed over RGB. Text is
#: antialiased, so the assertion is on the EXTREME pixel of a glyph (its interior), which reaches
#: the pure colour; this tolerance is for rounding, not for blending.
INK_TOLERANCE = 12


def _lum(rgb):
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(float(c)) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    """WCAG 2.x relative-contrast ratio between two (r, g, b) triples."""
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _rgb(widget):
    """The widget's painted frame as an (h, w, 3) uint8 array — a COPY, never a view onto Qt's
    buffer (the QImage dies with the temporary QPixmap and a numpy view onto it would then be
    reading freed memory, which is how a previous sweep's pixel comparisons passed vacuously)."""
    img = widget.grab().toImage().convertToFormat(QImage.Format_RGB32)
    h, stride = img.height(), img.bytesPerLine() // 4
    arr = np.frombuffer(bytes(img.constBits()), dtype=np.uint8).reshape(h, stride, 4)
    return np.ascontiguousarray(arr[:, :img.width(), 2::-1])      # BGRA -> RGB


def _title_ink(view, axis):
    """(ink, background) for one axis TITLE, read off a rendered frame.

    The label's own bounding box, mapped into scene coordinates — the same rectangle
    test_design_system's "no axis title is painted outside its chart" measures, so the two checks
    cannot disagree about where a title is. `ink` is the pixel in that box furthest from the
    modal colour of the whole viewport, i.e. the darkest/brightest stroke the reader can see; if
    the title paints nothing at all, ink == background and the ratio comes out 1.00."""
    viewport = view.viewport()
    frame = _rgb(viewport)
    flat = frame.reshape(-1, 3)
    vals, counts = np.unique(flat, axis=0, return_counts=True)
    bg = tuple(int(c) for c in vals[counts.argmax()])
    r = axis.label.mapRectToScene(axis.label.boundingRect())
    x0, y0 = max(int(r.left()), 0), max(int(r.top()), 0)
    box = frame[y0:min(int(r.bottom()) + 1, frame.shape[0]),
                x0:min(int(r.right()) + 1, frame.shape[1])]
    assert box.size, f"the {axis.labelText!r} title has no pixels inside the viewport"
    px = box.reshape(-1, 3)
    return tuple(int(c) for c in px[np.abs(px.astype(int) - np.array(bg)).sum(axis=1).argmax()]), bg


def _hex_rgb(name):
    name = name.lstrip("#")
    return tuple(int(name[i:i + 2], 16) for i in (0, 2, 4))


def _plots(size):
    from test_charts_panel import _view
    return _view(size=size)


def _plot_titles(v):
    return ((v.p_speed, "left"), (v.p_delta, "left"), (v.p_delta, "bottom"))


def _check_titles(v, view, titles, where):
    """Assert every title in `titles` clears the floor and paints in its axis's own text colour."""
    seen = 0
    for plot, side in titles:
        axis = plot.getAxis(side)
        assert axis.labelText, f"{where}: the {side} axis lost its title"
        ink, bg = _title_ink(view, axis)
        ratio = contrast(ink, bg)
        assert ratio >= AA_FLOOR, (
            f"{where}: the axis title {axis.labelText!r} paints rgb{ink} on rgb{bg} = "
            f"{ratio:.2f}:1, under the {AA_FLOOR}:1 floor every other enabled text style in this "
            f"app clears — the reader loses the NAME of the axis, not a decoration")
        # ...and it is the same colour the TICK LABELS are drawn in. The floor alone would accept a
        # title in some third colour nobody chose; this pins it to the axis's own text pen.
        want = _hex_rgb(axis.textPen().color().name())
        drift = sum(abs(a - b) for a, b in zip(ink, want, strict=True))
        assert drift <= INK_TOLERANCE, (
            f"{where}: {axis.labelText!r} paints rgb{ink}, but its tick labels are drawn in "
            f"rgb{want} — one axis, two text colours")
        seen += 1
    return seen


def test_every_charts_panel_axis_title_clears_the_text_floor():
    """The three titles, on painted pixels, at both shipped window sizes.

    Sizes matter here for the reason the "painted outside its chart" guard gives: pyqtgraph's
    title placement is arithmetic on the viewport, so a title can be legible at 1440x900 and
    half-outside the frame at 1280x800 — and a pixel that is not inside the viewport has no
    contrast at all."""
    checked = 0
    for size in ((900, 520), (1224, 641)):
        v = _plots(size)
        checked += _check_titles(v, v.glw, _plot_titles(v), f"at {size}")
        v.hide()
    assert checked == 6, checked
    print(f"test_every_charts_panel_axis_title_clears_the_text_floor OK ({checked} titles)")


def test_the_measurement_can_still_see_the_defect():
    """THE NEGATIVE CONTROL. Re-open the bug on a live widget and watch the check fail.

    A pixel assertion that cannot fail is decoration. This calls `setPen` with the hairline the
    panel draws its axis lines in — exactly what `_apply_axis_pens` does, and what used to run
    LAST — and asserts the title collapses to the measured 1.19:1. Then it restores the panel's
    own order and the title comes back, which is the fix stated as an experiment."""
    v = _plots((900, 520))
    axis = v.p_delta.getAxis("left")
    before = contrast(*_title_ink(v.glw, axis))
    assert before >= AA_FLOOR, before

    axis.setPen(pg.mkPen(C.border, width=theme.line_width(1)))   # the pre-fix order
    for _ in range(4):
        _APP.processEvents()
    broken = contrast(*_title_ink(v.glw, axis))
    assert broken < 2.0, (
        f"the hairline pen no longer reaches the axis title ({broken:.2f}:1) — either pyqtgraph "
        f"changed or this control is measuring the wrong pixels, and either way the check above "
        f"is no longer known to have teeth")

    v._apply_axis_pens()                                          # the shipped order
    for _ in range(4):
        _APP.processEvents()
    after = contrast(*_title_ink(v.glw, axis))
    assert after >= AA_FLOOR, after
    v.hide()
    print(f"test_the_measurement_can_still_see_the_defect OK "
          f"({before:.2f}:1 -> {broken:.2f}:1 -> {after:.2f}:1)")


def test_the_titles_survive_the_re_pen_paths():
    """A unit flip, a palette flip and a SCREEN CHANGE all re-issue these pens.

    The device-pixel-ratio path is the one that made "pass a colour at setLabel" insufficient:
    `_apply_axis_pens` runs again when the window moves to a display with another DPR, and it is
    a plain `setPen`, so the title took the hairline again on a machine with two monitors. Driven
    through Qt's OWN notification, the way the app receives it."""
    v = _plots((1224, 641))
    v.set_speed_unit("mph")
    v.refresh_palette()
    for _ in range(4):
        _APP.processEvents()
    assert _check_titles(v, v.glw, _plot_titles(v), "after a unit + palette flip") == 3

    v.devicePixelRatioF = lambda: 2.0
    v.event(QEvent(QEvent.Type.DevicePixelRatioChange))
    for _ in range(4):
        _APP.processEvents()
    assert _check_titles(v, v.glw, _plot_titles(v), "after a DPR change") == 3
    v.hide()
    print("test_the_titles_survive_the_re_pen_paths OK")


def test_the_friction_circles_titles_survive_the_re_pen_too():
    """The same defect, the same door, one page away.

    `StatsView._apply_pen_scale` re-pens the sparkline and the friction circle on a screen change
    — with `setPen`, which owns the title colour — so the g-g chart's two axis names were one
    monitor drag away from the charts panel's 1.19:1. They are set with an explicit `labelStyle`
    at construction, which is exactly why only a RENDERED check catches this."""
    from test_stats import _fake_view_session

    from studio.stats_panel import StatsView

    v = StatsView(_fake_view_session())
    v.resize(1900, 900)
    v.show()
    for _ in range(8):
        _APP.processEvents()
    plot = v.gg.getPlotItem()
    titles = ((plot, "left"), (plot, "bottom"))
    assert _check_titles(v, v.gg, titles, "friction circle") == 2

    v.devicePixelRatioF = lambda: 2.0
    v.event(QEvent(QEvent.Type.DevicePixelRatioChange))
    for _ in range(8):
        _APP.processEvents()
    assert _check_titles(v, v.gg, titles, "friction circle after a DPR change") == 2
    v.hide()
    print("test_the_friction_circles_titles_survive_the_re_pen_too OK")


def test_wrap_label_measures_itself_from_a_cleared_minimum():
    """widgets.WrapLabel: a label's own answer must not become the floor of its next answer.

    `QLabel.heightForWidth` is clamped by the widget's own minimum (QLabelPrivate::sizeForWidth
    ends in `expandedTo(minimumSize())`), and WrapLabel INSTALLS that minimum on every resize — so
    measuring without clearing it first can only ever ratchet upwards. Measured on the shipped
    widget before the fix: 1178 px -> 14 px tall, narrowed to 258 -> 28, widened back to 1178 ->
    still 28. That is the Stats DATA TRUST card's third row "wrapping at any width": 421 px of ink
    in a 470 px column, laid out two lines tall and painted vertically centred in them.

    Lives in this file because it is the same shape as the axis titles above — a value written by
    one call surviving into a frame that should have overwritten it."""
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from studio.widgets import WrapLabel

    host = QWidget()
    lay = QVBoxLayout(host)
    label = WrapLabel("agree · lateral r=+0.96 · lateral gain ×1.09 · longitudinal r=+0.77 · "
                      "346,713 samples")
    label.setProperty("role", "Note")
    label.setFont(theme.ui_font(theme.CAPTION))
    lay.addWidget(label)
    host.resize(1200, 200)
    host.show()

    def settle():
        for _ in range(6):
            _APP.processEvents()

    # The MINIMUM is the quantity, not the laid-out height: a QLabel's vertical policy lets a
    # QVBoxLayout stretch it to whatever room it has, so `height()` reads the host either way.
    # `minimumHeight` is what WrapLabel installs and what a real layout is bound by.
    settle()
    one_line = label.minimumHeight()
    assert one_line > 0, "setup: WrapLabel must have claimed a height at all"
    host.resize(280, 200)
    settle()
    assert label.minimumHeight() > one_line, "setup: 280px must be too narrow for this line"
    host.resize(1200, 200)
    settle()
    assert label.minimumHeight() == one_line == label.heightForWidth(label.width()), (
        f"the label kept {label.minimumHeight()}px of height for a {one_line}px line after the "
        f"pane went back — a measure it took of itself became its own floor")
    host.hide()
    print(f"test_wrap_label_measures_itself_from_a_cleared_minimum OK ({one_line}px line)")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} CHART-AXIS-LEGIBILITY TESTS PASSED")
