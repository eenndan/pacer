"""Track-map chrome: the cue, the legend and the sector controls (QA MAP-05/07/09/10 + MAP-11).

Four things the map said (or failed to say) about itself, all offscreen on stub sessions:

  * MAP-05 — the "drag to set start/finish — lap timing provisional" callout is centred on the
    start line, so a line near an edge painted the caption's outer half off-canvas: measured at
    x = −39.8 px in a 1272 px panel, rendering as "o set start/finish".
  * MAP-10 — a re-segmentation left a 2-sample segment whose speed was 43.24 km/h at both ends,
    and the map painted a full red→green ramp under the labels "43" → "43 km/h".
  * MAP-09 — the Elevation legend quoted two ABSOLUTE GPS altitudes to the metre. Across 21 laps
    of one recording its low end ranged 79.9..83.0 m — a 3.2 m disagreement about the same track,
    against a lap profile only ~4.5 m tall. The colours are min/max normalised per lap, i.e. they
    encode the within-lap SHAPE, so that is what the two labels now claim.
  * MAP-07 — "Reset sectors" discarded three hand-placed lines in 59 ms with no dialog, no status
    line and nothing on the map to say it happened (it IS ⌘Z-reversible, which nothing said).
  * MAP-11 — "Add sector" appended at 1/2, then 2/3, then 3/4 of what was LEFT, giving sub-sectors
    of 49.9 / 16.8 / 8.6 / 24.7 % of the lap.
  * W2R-06 — a cross-recording reference whose racing line was refused (too far off, or the wrong
    SIZE) left no ring and said nothing, so "the reference loaded" and "the reference's line is
    untrustworthy" looked identical: the faint line silently reverted to the local best lap.

Run: python tests/test_map_chrome.py
"""
import math
import os
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from _synthetic import bare_session  # noqa: E402

from studio.map_render import ELEVATION_LO_LABEL, rainbow_channel  # noqa: E402
from studio.map_view import MapView, _segs_equal  # noqa: E402


def _session(n=240, y_amp=40.0, start_x=-200.0, speed=None):
    """A bare Session with the read surface MapView touches.

    The loop is deliberately WIDE and flat (y_amp ≪ x) so that in a letterbox map panel the
    aspect-locked fit is bound by x — which is what puts a start line at the trace's x extreme
    hard against the panel edge, the MAP-05 geometry. `start_x` places the start line's midpoint.
    Timing reads PROVISIONAL (no track_name, no confirmation), which is the state that shows the
    on-canvas cue at all."""
    t = np.arange(n) * 0.1
    xs = np.cos(np.linspace(0, 2 * math.pi, n)) * 200.0
    ys = np.sin(np.linspace(0, 2 * math.pi, n)) * y_amp
    sp = np.linspace(20.0, 60.0, n) if speed is None else np.asarray(speed, float)
    s = bare_session(best=0, valid=[0])
    s._best_cache = 0
    s.tx, s.ty, s.tt, s.tv = xs, ys, t, sp
    line = SimpleNamespace(first=SimpleNamespace(x=start_x - 10.0, y=0.0),
                           second=SimpleNamespace(x=start_x + 10.0, y=0.0))
    s.laps = SimpleNamespace(sectors=SimpleNamespace(start_line=line, sector_lines=[]))
    s.lap_trace_segments = lambda lid: [SimpleNamespace(xs=xs, ys=ys, measured=True)]
    s.lap_trace_xy = lambda lid: (xs, ys)
    s.lap_channels = lambda lid: {"t_media_s": t, "x_m": xs, "y_m": ys, "speed_kmh": sp,
                                  "dist_m": np.linspace(0.0, 500.0, n)}
    s.delta = lambda ids, x_mode="distance": (0, {}, {})
    return s


def _sized_map(session, w=900, h=240):
    """A MapView with a REAL px size — the default quadrant's letterbox shape."""
    mv = MapView(session)
    mv.resize(w, h)
    mv.show()
    _APP.processEvents()
    _APP.processEvents()
    return mv


def _cue_span(mv):
    """(left, right) of the provisional caption and of the plot, in scene px."""
    lbl = mv._provisional_label
    assert lbl is not None, "this state is meant to show the provisional cue"
    box, view = lbl.sceneBoundingRect(), mv.plot.getViewBox().sceneBoundingRect()
    return (box.left(), box.right()), (view.left(), view.right())


# --------------------------------------------------------------- MAP-05: the cue fits
def test_provisional_cue_is_not_clipped_by_the_panel_edge():
    """MAP-05. A start line at either x extreme of the fitted view: the callout must be entirely
    inside the plot, whichever edge it is against. On the shipped code the caption is centred on
    the line, so ~half of it hangs off the canvas and the user reads "o set start/finish"."""
    for start_x, edge in ((-200.0, "left"), (200.0, "right")):
        mv = _sized_map(_session(start_x=start_x))
        (lo, hi), (vlo, vhi) = _cue_span(mv)
        assert lo >= vlo and hi <= vhi, (
            f"the provisional cue is clipped at the {edge} edge: caption {lo:.1f}..{hi:.1f} px "
            f"in a plot spanning {vlo:.1f}..{vhi:.1f} px")
        assert abs(mv._provisional_label.anchor.x() - 0.5) > 1e-3, \
            f"the {edge}-edge cue kept its centred anchor — nothing moved"
    print("test_provisional_cue_is_not_clipped_by_the_panel_edge OK")


def test_a_cue_with_room_keeps_its_centred_anchor():
    """The control: a start line in the middle of the map has room for a centred caption, so the
    clamp must leave it exactly where it was — no gratuitous asymmetry."""
    mv = _sized_map(_session(start_x=0.0))
    (lo, hi), (vlo, vhi) = _cue_span(mv)
    assert lo >= vlo and hi <= vhi
    assert abs(mv._provisional_label.anchor.x() - 0.5) < 1e-3, \
        "a cue with room on both sides was moved anyway"
    print("test_a_cue_with_room_keeps_its_centred_anchor OK")


def test_the_cue_re_fits_when_the_panel_is_reshaped():
    """The clamp is not a one-shot at build: dragging the splitter to a much narrower map re-runs
    it, because the same line is now much closer to the edge in px."""
    mv = _sized_map(_session(start_x=-200.0))
    mv.resize(320, 240)
    _APP.processEvents()
    _APP.processEvents()
    (lo, hi), (vlo, vhi) = _cue_span(mv)
    assert lo >= vlo and hi <= vhi, f"cue {lo:.1f}..{hi:.1f} outside plot {vlo:.1f}..{vhi:.1f}"
    print("test_the_cue_re_fits_when_the_panel_is_reshaped OK")


# --------------------------------------------------------------- MAP-10: no gradient, no ramp
def test_a_flat_speed_channel_reports_one_label_instead_of_a_fake_gradient():
    """MAP-10, at the pure-math seam: both ends rounding to the same number is not a gradient."""
    n = 6
    t, xs, ys = np.arange(n) * 0.1, np.linspace(0, 10, n), np.zeros(n)
    cum = np.linspace(0, 10, n)
    flat = np.full(n, 43.24)
    seg, lo, hi = rainbow_channel("speed", t, xs, ys, flat, cum, None, None)
    assert seg is None, "a flat channel must not paint a red→green ramp"
    assert hi == "", "an empty high label is what hides the colour strip"
    assert "43 km/h" in lo and "no gradient" in lo, lo
    # …and a channel with a real spread is untouched.
    seg, lo, hi = rainbow_channel("speed", t, xs, ys, np.linspace(30.0, 60.0, n), cum, None, None)
    assert seg is not None and lo == "30" and hi == "60 km/h", (lo, hi)
    print("test_a_flat_speed_channel_reports_one_label_instead_of_a_fake_gradient OK")


def test_the_map_hides_the_colour_ramp_when_there_is_no_gradient_to_label():
    """MAP-10 in the widget: the legend keeps ONE explanatory label and drops the strip — exactly
    what the Δ channel already does on the best lap. Measured before: a full ramp under '43' →
    '43 km/h'."""
    mv = _sized_map(_session(speed=np.full(240, 43.24)))
    mv.set_current_lap(0)
    mv.set_rainbow_mode("speed")
    _APP.processEvents()
    lg = mv._legend
    assert lg.isVisibleTo(mv), "the explanation must still be shown"
    assert not lg._strip.isVisibleTo(lg), "a colour ramp is painted for a channel with no gradient"
    assert lg.hi_label.text() == "" and "43" in lg.lo_label.text(), \
        (lg.lo_label.text(), lg.hi_label.text())
    assert lg.lo_label.text() != lg.hi_label.text(), "two ends labelled the same"
    print("test_the_map_hides_the_colour_ramp_when_there_is_no_gradient_to_label OK")


# --------------------------------------------------------------- MAP-09: relative elevation
def test_elevation_legend_is_relative_to_the_lap_not_an_absolute_altitude():
    """MAP-09. GPS altitude carries a drifting bias, so the same lap shape recorded 3 m 'higher'
    is the same lap: the legend must read identically. The colours are unchanged — this is what
    the two end labels claim about them."""
    n = 20
    t, xs, ys = np.arange(n) * 0.1, np.linspace(0, 10, n), np.zeros(n)
    cum = np.linspace(0, 10, n)
    # A curved climb (not a linear ramp): the segment means then sit clear of the bucket edges,
    # where a floor() is one ulp from flipping.
    profile = 10.0 + 30.0 * np.sin(np.linspace(0.0, math.pi / 2, n))
    seg, lo, hi = rainbow_channel("elevation", t, xs, ys, np.full(n, 50.0), cum, None, None,
                                  elevation=profile)
    assert lo == ELEVATION_LO_LABEL and hi == "+30 m", (lo, hi)
    assert "40 m" not in hi, "the absolute altitude is back in the legend"
    # The same shape, 3.2 m of GPS drift later (the measured cross-lap spread on the D24 fixture).
    seg2, lo2, hi2 = rainbow_channel("elevation", t, xs, ys, np.full(n, 50.0), cum, None, None,
                                     elevation=profile + 3.2)
    assert (lo2, hi2) == (lo, hi), (
        f"the same lap 3.2 m of GPS drift later labelled itself {lo2!r} → {hi2!r} "
        f"instead of {lo!r} → {hi!r}")
    assert seg2.tolist() == seg.tolist(), "the drift must not change the painted colours either"
    print("test_elevation_legend_is_relative_to_the_lap_not_an_absolute_altitude OK")


def test_a_flat_lap_reports_no_elevation_gradient():
    """MAP-10's other channel: a rise under half a metre is GPS noise, not relief."""
    n = 20
    t, xs, ys = np.arange(n) * 0.1, np.linspace(0, 10, n), np.zeros(n)
    cum = np.linspace(0, 10, n)
    seg, lo, hi = rainbow_channel("elevation", t, xs, ys, np.full(n, 50.0), cum, None, None,
                                  elevation=np.full(n, 81.0))
    assert seg is None and hi == "" and "no gradient" in lo, (seg, lo, hi)
    print("test_a_flat_lap_reports_no_elevation_gradient OK")


def test_the_channel_dropdown_documents_elevation():
    """The dropdown's tooltip described Speed, Δ and Grip and said nothing at all about the fifth
    channel — including that its altitudes are only meaningful relative to the lap."""
    mv = _sized_map(_session())
    tip = mv.rainbow_combo.toolTip()
    assert "Elevation" in tip, "the Elevation channel is undocumented in its own control"
    assert "RELATIVE" in tip or "relative" in tip, tip
    print("test_the_channel_dropdown_documents_elevation OK")


# --------------------------------------------------------------- MAP-07: say what you did
def test_reset_sectors_says_what_it_did_and_names_the_way_back():
    """MAP-07. The click is allowed to be immediate — it is fully undoable — but not silent."""
    mv = _sized_map(_session())
    mv.add_sector_btn.click()
    mv.add_sector_btn.click()
    _APP.processEvents()
    assert len(mv._sectors) == 2
    raised = []
    for name in ("question", "warning", "information", "critical"):
        setattr(QMessageBox, name, lambda *a, _n=name, **k: raised.append(_n))
    try:
        mv.reset_sectors_btn.click()
        _APP.processEvents()
    finally:
        for name in ("question", "warning", "information", "critical"):
            delattr(QMessageBox, name)
    assert mv._sectors == [], "the reset must still clear the lines"
    assert raised == [], f"a confirmation dialog was added: {raised}"
    notice = getattr(mv, "_notice", None)
    assert notice is not None and notice.isVisibleTo(mv.widget), \
        "nothing on the map says the sector lines were cleared"
    text = notice.text()
    assert "2 sector lines cleared" in text, text
    assert "⌘Z" in text or "Undo" in text, f"the notice does not name the way back: {text!r}"
    # …and it is laid out over the plot, not left at an unsized default at the origin.
    g, host = notice.geometry(), mv.widget.rect()
    assert host.contains(g), f"the notice is outside the map: {g} in {host}"
    assert g.x() == 8 and g.y() == 8 and g.height() > 0, g
    print("test_reset_sectors_says_what_it_did_and_names_the_way_back OK")


def test_reset_with_no_sectors_says_so_instead_of_re_segmenting():
    """The empty case was a 74 ms no-op re-segmentation that pushed an undo entry and told the
    user nothing. Now it reports, and emits nothing."""
    mv = _sized_map(_session())
    emitted = []
    mv.timing_lines_changed.connect(lambda *a: emitted.append(a))
    mv.reset_sectors_btn.click()
    _APP.processEvents()
    assert emitted == [], "clearing zero sector lines re-segmented the session"
    assert "No sector lines to clear" in mv._notice.text(), mv._notice.text()
    print("test_reset_with_no_sectors_says_so_instead_of_re_segmenting OK")


def test_a_reference_whose_line_cannot_be_drawn_is_explained_not_silent():
    """QA-W2R-06's other half. When the cross-recording reference's spatial fit is refused (too
    far off, or — the new half — the wrong SIZE), `reference_overlay_xy()` is None and the map
    silently falls back to the local best-lap ghost: two different states that looked identical.
    The map now says which one it is, once, on the canvas the line is missing from."""
    s = _session()
    s.reference_overlay_xy = lambda: None
    s.reference_label = lambda: "recording 0059 · 3 chapters"
    mv = _sized_map(s)
    mv._refresh_best()
    _APP.processEvents()
    notice = mv._notice
    assert notice.isVisibleTo(mv.widget), \
        "a reference whose racing line cannot be drawn vanished without a word"
    text = notice.text()
    assert "recording 0059 · 3 chapters" in text, text
    assert "not drawn" in text and "size" in text, text
    assert "your own best lap" in text, f"the notice must say what the faint line IS: {text!r}"
    assert "lap table" in text or "charts" in text, \
        f"the notice must say the Δ side still works: {text!r}"
    # …and the plate is big enough to SHOW it. adjustSize() sizes a word-wrapped QLabel to its
    # one-line hint, which sliced this four-line notice through the middle (measured: 70 px of
    # plate for 5 wrapped lines) — the message was there and unreadable.
    need = notice.heightForWidth(notice.width())
    assert notice.height() >= need, \
        f"the notice plate is {notice.height()} px for {need} px of wrapped text — it clips"
    assert mv.widget.rect().contains(notice.geometry()), \
        f"the notice is outside the map: {notice.geometry()} in {mv.widget.rect()}"
    # Idempotent: the 30 Hz tick re-runs this path and must not re-post (the plate would never
    # time out). Hide it by hand and check nothing puts it back while the state is unchanged.
    notice.hide()
    for _ in range(3):
        mv.set_current_lap(0)
        mv.refresh_overlays()
    _APP.processEvents()
    assert not notice.isVisibleTo(mv.widget), "the notice re-posts on every tick"

    # A reference that DOES draw says nothing at all — no notice on the happy path.
    s2 = _session()
    ring = np.column_stack([np.cos(np.linspace(0, 2 * math.pi, 80)) * 200.0,
                            np.sin(np.linspace(0, 2 * math.pi, 80)) * 40.0])
    s2.reference_overlay_xy = lambda: ring
    s2.reference_label = lambda: "recording 0059 · 3 chapters"
    mv2 = _sized_map(s2)
    mv2._refresh_best()
    _APP.processEvents()
    assert not mv2._notice.isVisibleTo(mv2.widget), "a drawn reference must not post a notice"
    print("test_a_reference_whose_line_cannot_be_drawn_is_explained_not_silent OK")


def test_the_action_notice_never_reaches_the_share_card():
    """The notice is interaction chrome, like the Fit button: grab_clean must hide it."""
    mv = _sized_map(_session())
    mv.add_sector_btn.click()
    _APP.processEvents()
    assert mv._notice.isVisibleTo(mv.widget)
    with mv.grab_clean():
        assert not mv._notice.isVisibleTo(mv.widget), "the notice burned into the share image"
    assert mv._notice.isVisibleTo(mv.widget), "grab_clean must restore what it hid"
    print("test_the_action_notice_never_reaches_the_share_card OK")


# --------------------------------------------------------------- MAP-11: even sectors
def test_three_add_sector_clicks_divide_the_lap_evenly():
    """MAP-11. Appending one line at a time can only bisect what is LEFT, so three clicks gave
    49.9 / 16.8 / 8.6 / 24.7 % of the lap — the third click carving an 8.6 % sliver. While the
    lines are still the app's own suggestions, the whole set is re-spaced instead."""
    mv = _sized_map(_session())
    for _ in range(3):
        mv.add_sector_btn.click()
        _APP.processEvents()
    got = [tl.seg() for tl in mv._sectors]
    assert len(got) == 3
    assert _segs_equal(got, mv.session.suggest_sectors(3)), \
        "three clicks did not leave an evenly-spaced set"
    # …and that set really is the quarters: each line sits at k/4 of the trace.
    xs, _ys = mv.session.lap_trace_xy(0)
    want = [mv.session._sector_seg_at(k / 4) for k in (1, 2, 3)]
    assert _segs_equal(got, want)
    print("test_three_add_sector_clicks_divide_the_lap_evenly OK")


def test_adding_a_sector_never_moves_a_line_the_user_placed():
    """The safety half of the same change: re-spacing is only for lines nobody has touched. Once
    a line is dragged, Add appends and leaves every existing line exactly where it was — the map
    must not silently undo a placement (which is MAP-07's concern, one click earlier)."""
    mv = _sized_map(_session())
    mv.add_sector_btn.click()
    mv.add_sector_btn.click()
    _APP.processEvents()
    dragged = mv._sectors[0]
    seg = dragged.seg()
    dragged.h1.setPos((seg.x1 + 37.0, seg.y1 + 11.0))     # the user drags one line
    dragged.h2.setPos((seg.x2 + 37.0, seg.y2 + 11.0))
    _APP.processEvents()
    placed = [tl.seg() for tl in mv._sectors]
    mv.add_sector_btn.click()
    _APP.processEvents()
    after = [tl.seg() for tl in mv._sectors]
    assert len(after) == 3, "the click must still add a line"
    assert _segs_equal(after[:2], placed), \
        "adding a sector moved lines the user had already placed"
    assert "left where you put them" in mv._notice.text(), mv._notice.text()
    print("test_adding_a_sector_never_moves_a_line_the_user_placed OK")


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} MAP CHROME TESTS PASSED")


if __name__ == "__main__":
    _run_all()
