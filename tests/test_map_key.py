"""The map's floating key: it must never be cut, and it must stay where the user put it (D5-03,
D5-06) — plus the toolbar-glyph rule the map header broke (D1-10).

Three defects, all measured on the real widgets:

  * D5-03 — the plate is a FIXED 196x106 px pinned by ``host.height() - key.height() - 8``. Drag
    the map/charts splitter up once (Qt clamps it at a 164 px panel = a 72 px canvas) and y went
    to **-42**, so Qt clipped 42 px of a 106 px plate off the TOP — the "Map key" title row and
    the "Video position" row, i.e. exactly the caret and title that are the only sign the plate
    collapses. The window's OWN minimum (973x528, measured) is the same state with no drag at all.
  * D5-06 — the plate is 46.1% of the map canvas's HEIGHT at the shipped 1440x900 default (55.2%
    at 1280x800), the whole plate is the click target, and the only hint was a 4x4 px caret with
    an empty tooltip. The collapse then reset on every launch AND on every recording opened.
  * D1-10 — inside one toolbar some labelled buttons carried a Phosphor glyph and some did not:
    the map header shipped ``[Snap to track + ph.magnet] [Add sector] [Reset sectors]``.

The size tests SWEEP the height 1 px at a time rather than checking one value: a one-size test is
what let a one-pixel elision bug through in an earlier wave. Run:
    QT_QPA_PLATFORM=offscreen python tests/test_map_key.py
"""
import math
import os
import sys
import tempfile
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# The prefs seam FIRST: MapView now reads AND writes the key's collapse, so without this the suite
# would read (and rewrite) the developer's own prefs.json — the _app_support_dir idiom
# test_app_chrome / test_library / test_data_safety already use.
from studio import prefs  # noqa: E402

_SEAM = tempfile.mkdtemp(prefix="pacer-test-map-key-")
prefs._app_support_dir = (lambda d=_SEAM: d)

from _qtapp import themed_app  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QApplication  # noqa: E402

_APP = themed_app()

from _synthetic import bare_session  # noqa: E402

from studio import theme  # noqa: E402
from studio.map_view import _CHROME_INSET, MapView, _MapLegend  # noqa: E402


# --------------------------------------------------------------------- fixture
def _session(n=240):
    """A bare Session with the read surface MapView touches (the shape test_map_chrome uses)."""
    t = np.arange(n) * 0.1
    xs = np.cos(np.linspace(0, 2 * math.pi, n)) * 200.0
    ys = np.sin(np.linspace(0, 2 * math.pi, n)) * 40.0
    sp = np.linspace(20.0, 60.0, n)
    s = bare_session(best=0, valid=[0])
    s.tx, s.ty, s.tt, s.tv = xs, ys, t, sp
    line = SimpleNamespace(first=SimpleNamespace(x=-10.0, y=0.0),
                           second=SimpleNamespace(x=10.0, y=0.0))
    s.laps = SimpleNamespace(sectors=SimpleNamespace(start_line=line, sector_lines=[]))
    s.lap_trace_segments = lambda lid: [SimpleNamespace(xs=xs, ys=ys, measured=True)]
    s.lap_trace_xy = lambda lid: (xs, ys)
    s.lap_channels = lambda lid: {"t_media_s": t, "x_m": xs, "y_m": ys, "speed_kmh": sp,
                                  "dist_m": np.linspace(0.0, 500.0, n)}
    s.delta = lambda ids, x_mode="distance": (0, {}, {})
    return s


def _map(w=900, h=320):
    """A shown MapView at a real px size (the map quadrant's letterbox shape)."""
    mv = MapView(_session())
    mv.resize(w, h)
    mv.show()
    for _ in range(4):
        _APP.processEvents()
    return mv


def _set_height(mv, h):
    """Drive the view to an exact height and let the layout reach the PlotWidget."""
    mv.setFixedHeight(h)
    for _ in range(3):
        _APP.processEvents()
    return mv.widget.height()


def _press(widget, y=8):
    """A real left press on the plate (the gesture that toggles it)."""
    pos = QPoint(widget.width() // 2, y)
    widget.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, pos, widget.mapToGlobal(pos),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    for _ in range(3):
        _APP.processEvents()


def _drop(mv):
    mv.hide()
    mv.deleteLater()
    _APP.processEvents()


# ------------------------------------------------------------------- D5-03
def test_the_map_key_is_never_cut_off_the_top_of_a_short_canvas():
    """D5-03, swept 1 px at a time from 0 to 240 px of view height.

    Two claims, at EVERY height: the plate's top-left corner never leaves the canvas (the shipped
    bug drove y to -42, and Qt clips a child at its parent's edge), and wherever the canvas can
    hold the plate at all, the whole plate is inside it with its inset intact."""
    mv = _map()
    key = mv._map_key
    off_top, off_bottom = [], []
    for h in range(0, 241):
        canvas = _set_height(mv, h)
        if key.x() < 0 or key.y() < 0:
            off_top.append((h, canvas, key.x(), key.y(), key.height()))
        fits = canvas >= key.height() + 2 * _CHROME_INSET
        inside = (key.x() >= _CHROME_INSET and key.y() >= _CHROME_INSET
                  and key.y() + key.height() <= canvas - _CHROME_INSET + 1
                  and key.x() + key.width() <= mv.widget.width())
        if fits and not inside:
            off_bottom.append((h, canvas, key.x(), key.y(), key.height()))
    assert not off_top, (
        f"the map key hangs off the top/left of its canvas at {len(off_top)} of 241 heights, "
        f"e.g. view={off_top[0][0]} px -> canvas {off_top[0][1]} px, plate at "
        f"({off_top[0][2]},{off_top[0][3]}) {off_top[0][4]} px tall")
    assert not off_bottom, (
        f"the map key leaves a canvas that HAS room for it at {len(off_bottom)} heights, "
        f"e.g. {off_bottom[0]}")
    _drop(mv)
    print("test_the_map_key_is_never_cut_off_the_top_of_a_short_canvas OK "
          "— 241 heights, 0 clipped")


def test_the_key_falls_back_to_its_title_at_exactly_one_height():
    """The fallback has ONE boundary and it is the arithmetic one: the plate paints in full while
    the canvas can hold it plus both insets, and its title-only form the pixel below. Asserted on
    the pixel either side, not on a single sampled size."""
    mv = _map()
    key = mv._map_key
    boundary = key.expanded_height() + 2 * _CHROME_INSET
    assert (key.expanded_height(), key.collapsed_height(), boundary) == (106, 34, 122), (
        f"the plate's own arithmetic moved: expanded={key.expanded_height()} "
        f"collapsed={key.collapsed_height()} boundary={boundary}")

    seen, flips = [], []
    prev = None
    for h in range(0, 241):
        canvas = _set_height(mv, h)
        state = key.painted_collapsed()
        seen.append((canvas, state, key.height()))
        if prev is not None and prev[1] != state:
            flips.append((prev[0], canvas))
        prev = (canvas, state)
    assert len(flips) == 1, f"the plate changed form {len(flips)} times, not once: {flips}"
    assert flips[0][1] == boundary, (
        f"the plate opened at a canvas of {flips[0][1]} px, not at "
        f"expanded_height + 2*inset = {boundary} px")
    below = [s for c, s, _h in seen if c == boundary - 1]
    above = [s for c, s, _h in seen if c == boundary]
    assert below == [True] and above == [False], (
        f"canvas {boundary - 1} px -> collapsed={below}, canvas {boundary} px -> collapsed={above}")
    # and the plate's height always agrees with the form it is painting
    wrong = [(c, s, h) for c, s, h in seen
             if h != (key.collapsed_height() if s else key.expanded_height())]
    assert not wrong, f"plate height disagrees with its painted form at {wrong[:3]}"
    _drop(mv)
    print("test_the_key_falls_back_to_its_title_at_exactly_one_height OK "
          f"— one flip, at a {boundary} px canvas")


# ------------------------------------------------------------------- D5-06
def test_the_short_canvas_fallback_never_overwrites_the_users_own_choice():
    """The fallback is a display state, not a decision: shrinking the panel must not rewrite what
    the user chose, and growing it back must restore exactly that. And while there is no room, the
    plate must not offer a click that changes nothing on screen but something on disk."""
    prefs.set_map_key_collapsed(False)
    mv = _map()
    key = mv._map_key
    assert (key.collapsed(), key.painted_collapsed()) == (False, False)

    _set_height(mv, 100)                        # canvas well under the boundary
    assert key.painted_collapsed() and not key.collapsed(), (
        "a short canvas must paint the title-only plate WITHOUT touching the user's choice: "
        f"painted={key.painted_collapsed()} user={key.collapsed()}")
    assert key.cursor().shape() == Qt.ArrowCursor, (
        "the plate still offers a pointing hand for a click it cannot honour")
    _press(key)
    assert not key.collapsed() and prefs.map_key_collapsed() is False, (
        f"a click with no room to expand changed the stored choice to {prefs.map_key_collapsed()}")

    _set_height(mv, 320)
    assert not key.painted_collapsed(), "the plate did not come back when the room did"

    _press(key)                                  # now the user really does collapse it
    assert key.collapsed() and prefs.map_key_collapsed() is True, (
        f"the click did not persist: user={key.collapsed()} prefs={prefs.map_key_collapsed()}")
    _set_height(mv, 100)
    _set_height(mv, 320)
    assert key.collapsed() and key.painted_collapsed(), (
        "a trip through the short-canvas fallback lost the user's own collapse")
    _press(key)
    assert not key.collapsed() and prefs.map_key_collapsed() is False
    _drop(mv)
    print("test_the_short_canvas_fallback_never_overwrites_the_users_own_choice OK")


def test_the_collapse_survives_the_next_recording_and_the_next_launch():
    """D5-06. The collapse used to be per-MapView state, so a user who put a 196x106 plate away
    got it back on the next recording they opened and on the next launch. It is a preference now,
    read by the view that draws it (studio/prefs.py)."""
    prefs.set_map_key_collapsed(True)
    mv = _map()
    assert mv._map_key.collapsed() and mv._map_key.height() == mv._map_key.collapsed_height(), (
        f"a stored collapse did not reach a fresh MapView: user={mv._map_key.collapsed()} "
        f"h={mv._map_key.height()}")
    _drop(mv)

    prefs.set_map_key_collapsed(False)
    mv = _map()
    assert not mv._map_key.collapsed() and mv._map_key.height() == mv._map_key.expanded_height()
    _drop(mv)

    # A corrupt/garbage stored value must read as the default, never crash the map.
    prefs.set(prefs.MAP_KEY_COLLAPSED, "yes-please")
    assert prefs.map_key_collapsed() is True     # bool("yes-please") — coerced, not crashed
    prefs.set(prefs.MAP_KEY_COLLAPSED, None)
    assert prefs.map_key_collapsed() is False
    prefs.set_map_key_collapsed(False)
    print("test_the_collapse_survives_the_next_recording_and_the_next_launch OK")


def test_the_plate_says_what_clicking_it_does():
    """D5-06. The plate is its own only control and its tooltip was the empty string — the only
    hint was a caret. All three states now name the gesture (or say why there isn't one), and no
    two states say the same thing."""
    prefs.set_map_key_collapsed(False)
    mv = _map()
    key = mv._map_key
    tips = {}
    tips["expanded"] = key.toolTip()
    _press(key)
    tips["collapsed"] = key.toolTip()
    _press(key)
    _set_height(mv, 100)
    tips["no room"] = key.toolTip()
    for state, tip in tips.items():
        assert tip.strip(), f"the {state} plate has no tooltip at all"
        assert "Map key" in tip, f"the {state} tooltip does not name the plate: {tip!r}"
    assert len(set(tips.values())) == 3, f"two states share a tooltip: {tips}"
    assert "collapse" in tips["expanded"].lower() and "expand" in tips["collapsed"].lower(), tips
    assert "too short" in tips["no room"].lower(), tips["no room"]
    _drop(mv)
    print("test_the_plate_says_what_clicking_it_does OK")


# ------------------------------------------------------------------- D1-10
def test_every_labelled_control_in_a_panel_toolbar_carries_a_glyph():
    """D1-10, as a rule rather than a line number.

    The app has two button vocabularies and they are internally consistent apart from one toolbar:
    a labelled control mounted on PANEL CHROME carries a Phosphor glyph (Snap to track, Compare,
    Brake/Throttle, Ideal lap, the map's Fit, coaching's Jump), while a dialog's or card's button
    row is words (Open / Close / Cancel / Clear library / Back up…). The map header shipped
    "Add sector" and "Reset sectors" bare, beside a glyphed "Snap to track".

    CHIPS ARE NOT BUTTONS and are exempt by name: `role="Chip"` is a pill that QUALIFIES the thing
    beside it (theme.py's chip rule), a look shared with two QLabels that have no icon slot at all,
    so `vs ideal` is not the charts toolbar's third button — it is the hero readout's label.

    Reported by the attribute that OWNS each control, so a future exemption names a decision."""
    from test_central_view_realqt import _real_central_view

    cv, _s, _t0, _t1 = _real_central_view()
    owners = {id(w): name for name, w in
              (("map.rainbow_combo", cv.map.rainbow_combo), ("map.snap_btn", cv.map.snap_btn),
               ("map.add_sector_btn", cv.map.add_sector_btn),
               ("map.reset_sectors_btn", cv.map.reset_sectors_btn),
               ("central.ideal_readout_btn", cv.ideal_readout_btn),
               ("plots.brake_throttle_btn", cv.plots.brake_throttle_btn),
               ("plots.ideal_btn", cv.plots.ideal_btn),
               ("plots.x_mode_combo", cv.plots.x_mode_combo))}
    bare, chips = [], []
    for toolbar in (cv._map_toolbar, cv._plots_toolbar):
        for c in toolbar.controls:
            if not isinstance(c, QAbstractButton) or not c.text():
                continue          # combo boxes carry their own affordance (the chevron)
            owner = owners.get(id(c), f"<unowned {type(c).__name__} {c.text()!r}>")
            if c.property("role") == "Chip":
                chips.append(owner)
            elif c.icon().isNull():
                bare.append(f"{owner} ({c.text()!r})")
    assert not bare, (
        "these labelled controls sit in a panel toolbar beside glyphed ones and carry no glyph — "
        f"give each one a theme.icon() name, or make the case for the whole toolbar: {bare}")
    assert chips == ["central.ideal_readout_btn"], (
        f"the chip exemption is meant to name exactly one control, not {chips}")
    # …and the exemption is a LOOK, not a loophole: a chip carries no glyph anywhere in the app.
    assert cv.ideal_readout_btn.icon().isNull(), (
        "a chip grew a glyph — then it is a button, and the rule above applies to it")
    print("test_every_labelled_control_in_a_panel_toolbar_carries_a_glyph OK "
          f"— {len(owners)} controls, 1 chip exempt")


def test_the_map_toolbars_glyphs_cost_the_window_no_minimum_width():
    """PanelToolbar pins every child at QSizePolicy.Fixed / sizeHint, so a glyph widens the
    toolbar AND the panel's minimum width. Measured: the map toolbar asks 544 px with the two new
    glyphs (504 without), which is also the map panel's minimum — still under the 557 px the
    CHARTS panel already sets for that column, so the window's own minimum does not move."""
    from test_central_view_realqt import _real_central_view

    cv, _s, _t0, _t1 = _real_central_view()
    map_need = cv._map_toolbar.sizeHint().width()
    map_min = cv._map_panel.minimumSizeHint().width()
    plots_min = cv._plots_panel.minimumSizeHint().width()
    assert map_min <= plots_min, (
        f"the map toolbar ({map_need} px) now sets the right column's minimum width at "
        f"{map_min} px, past the charts panel's {plots_min} px — a glyph in the map header is "
        f"pushing the window's own minimum size up")
    print("test_the_map_toolbars_glyphs_cost_the_window_no_minimum_width OK "
          f"— map toolbar {map_need} px, map panel min {map_min} <= charts {plots_min}")


# ------------------------------------------------------------------- plate arithmetic
def test_the_plates_two_heights_come_from_its_own_rows():
    """The two heights are derived, not typed: add a key row and both the plate and the fallback
    boundary follow it. Guards the class methods the geometry above is asserted against."""
    assert _MapLegend.collapsed_height() == 2 * 8 + 18
    assert (_MapLegend.expanded_height()
            == _MapLegend.collapsed_height() + 18 * len(_MapLegend._ROWS))
    assert _CHROME_INSET == theme.SPACE_S, (
        f"the floating-chrome inset drifted off the spacing scale: {_CHROME_INSET}")
    print("test_the_plates_two_heights_come_from_its_own_rows OK")


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n{len(tests)} map-key tests passed")


if __name__ == "__main__":
    _run_all()
    _ = QApplication
