"""Regression tests for the CHARTS/MAP panel chrome (QA-sweep batch B02, restated after the
header/toolbar split).

WHAT THIS FILE USED TO GUARD. Six findings, one bar. The charts header carried a 391 px hero
readout, its reference chip, two chart toggles, the x-axis combo and the ⛶ button inside a quadrant
that is 917 px at the app's own 1440x900 default — over-subscribed by construction — and every
finding was a way that over-subscription surfaced:

  * L6-01 (REGRESSION of #125) — the bar led with "Δideal" while the chart under it plotted
    Δ-to-BEST, and the label naming the chart's baseline was hidden at every width the app ships
    at. #122 made that label the bar's first casualty while it genuinely was decorative; #125 then
    handed it the baseline naming without moving it up the yield order, so #122's gate won 100 % of
    the time (needed 1040 px against a 917 px bar).
  * L2-02 (REGRESSION of #125) — with the label hidden the controls still did not fit at 1280x800:
    "Brake/Throttle" (88 px of text) painted into a 60 px content box, "Ideal lap" (52) into 24 and
    "x: distance" (67) into 40, all centre-clipped with no ellipsis, and the combo had no tooltip.
    NOTE the trap: qa.strings-style PAD estimates put those shortfalls at 2/2/7 px — the real
    QStyle content rects put them at 24/24/27, so these tests measure SE_PushButtonContents /
    SC_ComboBoxEditField, never an estimate.
  * L2-03 — the right column's 360 px minimum was below the bar's own minimum, so at that minimum
    QHBoxLayout resolved the shortfall by letting children OVERLAP: the hero ran 39 px past the
    header's right edge and the amber chip painted 307 px inside the hero's rect.
  * L2-04 — the same column minimum squeezed the MAP header's buttons into "ld sect" / "et sec",
    and neither sector button carried a tooltip (the destructive one included).
  * L2-08 — the ⛶ buttons were 26x22, under the 24x24 hit-target floor, and they are the only
    always-visible affordance that restores a maximized panel.
  * IA-03 — the largest text in the window (the hero) is Δ-to-ideal on the lap the app opens on,
    which is the BEST lap, where the ideal used to be structurally ~0. That was first answered with
    honesty (a hover note saying why the number could not move) and has since been answered at the
    source: the ideal is the corner/straight composite, which is strictly faster than any single
    lap, so the hero moves on the best lap too. The note is gone and the movement is asserted —
    along with the clamp that keeps the same readout from printing a minus sign inside a segment.

WHAT CHANGED, AND WHY THE CONTRACTS SURVIVED THE REWRITE. The fix for all of that was a four-tier
width-budget ladder (`_measure_plots_budget` / `_fit_plots_header` + a resize `eventFilter`) that
chose, at every resize, how much of the panel's IDENTITY to trade away for control text. The
header/toolbar split deletes the ladder: identity + the live hero readout stay in a `PanelHeader` of
declared height, every control moves to a `PanelToolbar` below, and the two stop sharing one width
budget. So the tests below assert the SAME properties — nothing centre-clipped, nothing overlapping,
the baseline always named, every ⛶ over the hit floor — against a structure where they hold by
construction rather than by arithmetic.

ONE CONTRACT IS DELIBERATELY GONE, and it is the one that was about the ladder itself:
`test_charts_bar_keeps_naming_the_baseline_when_the_toggles_must_yield` pinned a YIELD ORDER —
"identity outranks control text when the bar cannot hold both". There is no shortfall to spend any
more (measured below: the label, the hero and every control paint in full at 1440x900, at 1280x800
and at the column minimum), so an assertion about which item gives way first would be asserting the
behaviour of code that no longer exists. Its real content — the naming survives a cramped panel —
is now the stronger claim in `test_charts_header_names_the_baseline_at_every_width_it_can_reach`.

Run: QT_QPA_PLATFORM=offscreen python tests/test_charts_header_budget.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _qtapp import themed_app  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QComboBox,
    QStyle,
    QStyleOptionButton,
    QStyleOptionComboBox,
    QWidget,
)

# THEMED BEFORE THE FIRST WIDGET, which is the order studio/app.py uses and the order this file's
# numbers depend on: every dimension here comes from the painted fonts (the hero is styled in the
# mono stack at HERO/600), so a view built unthemed and measured themed reports a different app.
_APP = themed_app()

# The real production CentralView over the deterministic stadium-loop synthetic — the same fixture
# the rest of the real-Qt view tests build on.
from test_central_view_realqt import _real_central_view  # noqa: E402

from studio import plots_view, theme  # noqa: E402
from studio.central_view import (  # noqa: E402
    _PLOTS_LABEL_BEST,
    _PLOTS_LABEL_IDEAL,
    _PLOTS_LABEL_REF,
)
from studio.widgets import PanelHeader, PanelToolbar  # noqa: E402


def _settle(n=6):
    for _ in range(n):
        _APP.processEvents()


class _Themed:
    """A shown CentralView at `size`, built under the module-scope theme. The theme is mandatory,
    not decoration: every number the panel chrome is made of comes from the painted fonts, so
    measuring an unthemed view measures a different app — and so does measuring a view that was
    BUILT unthemed and themed afterwards, which is what this class used to do."""

    def __init__(self, size=(1440, 900)):
        self.view, self.session = _real_central_view()[:2]
        self.view.resize(*size)
        self.view.show()
        _settle(8)

    def __enter__(self):
        return self.view

    def __exit__(self, *exc):
        self.view.hide()          # the theme stays: it is the module's regime, not this block's
        return False


def _content_px(w):
    """The box the STYLE actually leaves for this control's text, not a padding estimate."""
    if isinstance(w, QComboBox):
        opt = QStyleOptionComboBox()
        w.initStyleOption(opt)
        return w.style().subControlRect(QStyle.CC_ComboBox, opt,
                                        QStyle.SC_ComboBoxEditField, w).width()
    opt = QStyleOptionButton()
    w.initStyleOption(opt)
    box = w.style().subElementRect(QStyle.SE_PushButtonContents, opt, w).width()
    icon = w.iconSize().width() + 4 if not w.icon().isNull() else 0
    return box - icon - 2 * w.style().pixelMetric(QStyle.PM_ButtonMargin, opt, w)


def _bar_text(w):
    return w.currentText() if isinstance(w, QComboBox) else w.text()


def _assert_readable(controls):
    """Every control either paints its whole label, or paints none of it and says what it is on
    hover — never a centre-clipped fragment like 'Brake/Thrott'."""
    for c in controls:
        text = _bar_text(c)
        if text:
            room, need = _content_px(c), c.fontMetrics().horizontalAdvance(text)
            assert need <= room, f"{text!r} needs {need}px in a {room}px content box"
        else:
            assert c.toolTip(), f"{c.accessibleName()!r} lost its text with no tooltip"
            assert c.accessibleName(), "a control with no text needs an accessible name"
            assert c.accessibleName() in c.toolTip(), c.toolTip()


def _set_right_column(view, px):
    """Give the charts column exactly `px` (the panel spans it), settled."""
    total = sum(view._main_splitter.sizes())
    view._main_splitter.setSizes([total - px, px])
    _settle(6)


def _charts_controls(view):
    return (view.ideal_readout_btn, view.plots.brake_throttle_btn, view.plots.ideal_btn,
            view.plots.x_mode_combo)


def _map_controls(view):
    return (view.map.rainbow_combo, view.map.snap_btn,
            view.map.add_sector_btn, view.map.reset_sectors_btn)


def _column_floor(view):
    """The narrowest the charts column can be dragged. Derived from the live splitter rather than
    from a constant, because the whole point of the rewrite is that nobody computes this number by
    hand any more — Qt derives it from what the panels actually need."""
    return view._right_splitter.minimumSizeHint().width()


def _chrome_children(bar):
    """The visible child widgets of a header/toolbar, left to right."""
    return sorted([k for k in bar.children() if isinstance(k, QWidget) and k.isVisible()],
                  key=lambda k: k.geometry().x())


# ============================================================ identity
def test_charts_header_names_the_baseline_at_every_width_it_can_reach():
    """L6-01, strengthened. The header must name the baseline the LOWER CHART draws, and name the
    same one the y-axis does. Before the fix the label was hidden at 1440 / 1500 / 1600 and only
    returned from a ~1633 px window; after the first fix it survived, but as a four-character chip
    at the app's smaller shipped size.

    Now it is checked at BOTH shipped sizes AND at the narrowest the column can be dragged, and the
    text must be the FULL wording at all three — there is no abbreviated tier left to fall back to,
    because the label no longer competes with anything for width."""
    for size in ((1440, 900), (1280, 800)):
        with _Themed(size) as view:
            label = view._plots_label
            axis = view.plots.p_delta.getAxis("left").labelText
            wanted = "IDEAL" if "ideal" in axis.lower() else "BEST"
            for px in (view._plots_panel.width(), _column_floor(view)):
                _set_right_column(view, px)
                assert label.isVisible(), f"baseline naming hidden in a {px}px panel"
                assert wanted in label.text(), (label.text(), axis)
                assert label.text() in (_PLOTS_LABEL_BEST, _PLOTS_LABEL_IDEAL, _PLOTS_LABEL_REF), (
                    f"the label was abbreviated to {label.text()!r} at {px}px — the degradation "
                    "ladder is supposed to be gone")
                assert label.width() >= label.sizeHint().width(), (
                    f"{label.text()!r} is squeezed below its own width at {px}px")
    print("test_charts_header_names_the_baseline_at_every_width_it_can_reach OK")


def test_the_header_names_a_cross_recording_reference_at_both_shipped_sizes():
    """QA-W2R-03 + the width judgement it still forces. The label had exactly two states, so with a
    reference loaded it painted "SPEED · Δ TO BEST" over a chart measured against ANOTHER
    recording's lap. Adding the third state is the fix; choosing its WORDING is still a risk, but
    for a DIFFERENT reason than before.

    It used to be that a wider wording lost the naming to a control label. Now the label is part of
    the charts column's FLOOR — the panel's minimum width is its identity + the hero's 391 px floor
    + ⛶ — so a wider wording costs the user drag range instead. The constraint is the same shape and
    is pinned the same way: the REF wording must be no wider than the BEST wording it joins, which
    makes it impossible for this caption to cost the layout anything the shipped one does not
    already cost."""
    for size in ((1440, 900), (1280, 800)):
        with _Themed(size) as view:
            label = view._plots_label
            floor_before = _column_floor(view)
            view._set_delta_baseline_label(plots_view.DELTA_BASELINE_REFERENCE)
            _settle(4)
            assert label.text() == _PLOTS_LABEL_REF, (size, label.text())
            assert "BEST" not in label.text(), label.text()
            assert label.isVisible() and label.width() >= label.sizeHint().width()
            assert _column_floor(view) <= floor_before, (
                f"{_PLOTS_LABEL_REF!r} raised the charts column's floor from {floor_before} to "
                f"{_column_floor(view)} — the wording may not cost the user drag range")
            # The abbreviation has the recording behind it (no header-sized wording has room).
            assert label.toolTip(), "an abbreviated caption must carry its meaning on hover"
            # Back to the local best: the caption follows the baseline in BOTH directions.
            view._set_delta_baseline_label(plots_view.DELTA_BASELINE_BEST)
            assert label.text() == _PLOTS_LABEL_BEST, label.text()
    print("test_the_header_names_a_cross_recording_reference_at_both_shipped_sizes OK")


# ============================================================ the controls
def test_no_charts_or_map_control_is_ever_centre_clipped():
    """L2-02 + L2-04. Measured from the real QStyle content rects at both shipped sizes AND at the
    column minimum. On the pre-fix build: 88px of 'Brake/Throttle' in 60, 52 of 'Ideal lap' in 24,
    67 of 'x: distance' in 40, 'Add sector' as 'ld sect' and 'Reset sectors' as 'et sec' — and the
    combo, the one whose meaning is unrecoverable, had no tooltip.

    A PanelToolbar pins every child to its sizeHint width (QSizePolicy.Fixed), so the shortfall that
    used to be resolved by clipping glyphs is now resolved by the splitter refusing the drag. That
    is what makes this checkable at the floor rather than only at the sizes the app opens at."""
    for size in ((1440, 900), (1280, 800)):
        with _Themed(size) as view:
            assert view.plots.x_mode_combo.toolTip(), "the x-axis combo must say what it switches"
            for px in (view._plots_panel.width(), _column_floor(view)):
                _set_right_column(view, px)
                _assert_readable(_charts_controls(view))
                _assert_readable(_map_controls(view))
    print("test_no_charts_or_map_control_is_ever_centre_clipped OK")


def test_map_sector_buttons_carry_their_labels_on_hover():
    """L2-04's other half — the DESTRUCTIVE button must never be an unhoverable non-word, whatever
    the layout does to it."""
    with _Themed((1280, 800)) as view:
        for btn, name in ((view.map.add_sector_btn, "Add sector"),
                          (view.map.reset_sectors_btn, "Reset sectors")):
            assert btn.toolTip(), f"{name} has no tooltip"
            assert name.lower() in btn.toolTip().lower(), btn.toolTip()
    print("test_map_sector_buttons_carry_their_labels_on_hover OK")


# ============================================================ the layout
def test_no_panel_chrome_children_ever_overlap_at_the_column_minimum():
    """L2-03, extended to the toolbars. Drag the main handle fully right: the column must clamp at
    what the panels HONESTLY need, not at a floor they cannot honour. On the pre-fix build the hero
    ran 39 px past the header and the amber chip sat 307 px inside the hero's rect, painting through
    the live number.

    Checked for every header AND every toolbar in the view, because the split created two new rows
    that can be over-subscribed and it would be an odd kind of progress to fix the old one by
    shipping two more."""
    with _Themed((1280, 800)) as view:
        _set_right_column(view, 100)                    # ask for far less than the panels can hold
        bars = view.findChildren(PanelHeader) + view.findChildren(PanelToolbar)
        assert len(bars) >= 6, f"expected 4 headers + 2 toolbars, found {len(bars)}"
        for bar in bars:
            if not bar.isVisible():
                continue
            prev = None
            for k in _chrome_children(bar):
                g = k.geometry()
                assert g.right() < bar.width(), (type(bar).__name__, _bar_text(k), g, bar.width())
                assert g.top() >= 0 and g.bottom() < bar.height(), (
                    f"{_bar_text(k)!r} paints outside its {bar.height()}px row: {g}")
                if prev is not None:
                    assert g.x() > prev.geometry().right(), (_bar_text(k), g, prev.geometry())
                prev = k
        # The specific collision the naive "raise the floors" fix produces, pinned by name. The
        # 'vs ideal' chip is no longer even in the same ROW as the hero, which is the structural
        # version of this assertion — but the hero must still clear whatever IS beside it.
        hero = view.diff_box
        assert view.ideal_readout_btn.parentWidget() is view._plots_toolbar, (
            "'vs ideal' is a control and belongs in the toolbar, not beside the live number")
        assert hero.geometry().right() < view._plots_max_btn.geometry().x(), (
            "the hero readout must never paint into the ⛶ button")
        print("test_no_panel_chrome_children_ever_overlap_at_the_column_minimum OK "
              f"(clamped at {view._plots_panel.width()}px)")


def test_the_charts_column_floor_is_the_headers_own_honest_need():
    """The arithmetic, restated. This test used to reconstruct a hand-computed width BUDGET and
    check the app's own constant against it — necessary while a fit pass was choosing tiers from
    that budget, and a place two different off-by-a-spacing bugs had already lived (the old pass
    demanded 1040 px where the layout needed 1013).

    Nobody computes it any more. `_layout_panels` sets NO explicit minimum on the charts column, so
    the floor is whatever Qt derives from the panels themselves, and this test proves that number is
    the charts header's real need — margins + two gaps + identity + the hero's floor + ⛶ — and that
    a drag one pixel past it is REFUSED rather than absorbed by the children.

    It is also the measurement that says the split cost the user nothing horizontally: the ladder
    computed floors of 675 px (1440x900) and 759 px (1280x800); this is 555."""
    with _Themed((1280, 800)) as view:
        header, row = view._plots_header, view._plots_header.layout()
        margins = row.contentsMargins()
        # Three non-empty items (identity · hero · ⛶) => two gaps. Derived from the widgets and the
        # layout themselves, NOT from an app constant, so this fails on a build whose arithmetic
        # disagrees rather than agreeing with it by construction.
        need = (margins.left() + margins.right() + row.spacing() * 2
                + view._plots_label.sizeHint().width() + view.diff_box.minimumWidth()
                + view._plots_max_btn.width())
        floor = _column_floor(view)
        assert floor == need, (
            f"the charts column's floor is {floor}px but its header needs {need}px")
        assert floor < 675, f"the header/toolbar split must not raise the column floor ({floor})"
        # Ask for one pixel less than the floor: the splitter clamps, it does not squeeze.
        _set_right_column(view, need - 1)
        assert view._plots_panel.width() == need, (view._plots_panel.width(), need)
        assert header.width() == need, (header.width(), need)
        assert view._plots_label.isVisible(), f"the label must fit in exactly {need}px"
        assert (view.plots.brake_throttle_btn.text(), view.plots.ideal_btn.text()) == (
            "Brake/Throttle", "Ideal lap"), "…and no control gives up its text to get there"
        print(f"test_the_charts_column_floor_is_the_headers_own_honest_need OK ({need}px)")


def test_every_maximize_button_clears_the_hit_target_floor():
    """L2-08. 26x22 is under the 24x24 floor, and these are the only always-visible way back from
    a maximized panel."""
    with _Themed((1440, 900)) as view:
        for name in ("video", "table", "map", "plots"):
            btn = getattr(view, f"_{name}_max_btn")
            assert btn.width() >= theme.HIT_MIN and btn.height() >= theme.HIT_MIN, (name, btn.size())
        print("test_every_maximize_button_clears_the_hit_target_floor OK")


# ============================================================ the hero readout
def test_the_hero_is_no_longer_a_structural_zero_on_the_best_lap():
    """IA-03, INVERTED. This test used to require the hero to APOLOGISE on hover ("Δideal stays
    near zero here by construction"), because the ideal was the pointwise minimum of the laps'
    cumulative-elapsed curves — a curve whose value at the flag is identically the best lap time,
    so the app's largest text was a structural null in the state the app always opens in.

    The ideal is now the corner/straight partition composite and is strictly faster than any single
    lap, so the apology would be false. What is asserted instead is the property that made it
    false: on the BEST lap — the default selection — the hero moves by a real amount, and the
    Δ-to-best number it promises to keep one hover away is still there, now with nothing appended.
    (On the real recordings the same sweep reads +0.94 s on D24 one chapter and +1.64 s on three.)"""
    with _Themed((1440, 900)) as view:
        s = view.session
        best = s.best_lap_id()
        assert best is not None, "the synthetic session must have a best lap"
        assert view.ideal_readout_btn.isChecked(), "the hero leads with Δ-to-ideal by default"
        assert s.ideal_donor_lap_id() is None, (
            "fixture must have a STITCHED ideal (>1 donor), or this asserts nothing")

        lo, hi = s.lap_window(best)
        swept = [s.delta_to_ideal_at(best, lo + (hi - lo) * k / 200) for k in range(201)]
        swept = [v for v in swept if v is not None]
        span = max(swept) - min(swept)
        assert span > 0.5, (
            f"Δideal moved only {span:.4f} s across the BEST lap — if the ideal has gone back to "
            f"being the best lap, that is the defect this test replaced, not a copy problem")

        view._update_diff_box(0.0, 42.0, best)
        tip = view.diff_box.toolTip()
        assert "best lap here" in tip, "the Δ-to-best number must stay one hover away"
        assert "by construction" not in tip and "near zero" not in tip, (
            f"the hero no longer has a structural zero to excuse on the best lap: {tip!r}")
        print("test_the_hero_is_no_longer_a_structural_zero_on_the_best_lap OK "
              f"(Δideal spans {span:.3f} s on the best lap)")


def test_the_hero_never_prints_a_minus_sign_against_the_ideal():
    """The clamp in `theme.format_ideal_run`, measured on a real view rather than in the abstract.

    At every partition EDGE the ideal took the minimum over the clean laps, so Δideal >= 0 there
    and at the flag. INSIDE a segment it replays its donor's pace, and a lap that carries more
    speed through the same corner is transiently ahead of it: this fixture's lap 1 reaches
    -0.077 s, and on the owner's recordings the floor is -0.159 s on 1.3 % of samples. Negative is
    real information about a donor's pace, and it is NOT "you beat your ideal" — over any segment,
    and over the lap, that cannot happen. So the app's largest text clamps it at zero rather than
    flashing an ahead-coloured minus sign a driver would read as an achievement."""
    with _Themed((1440, 900)) as view:
        s = view.session
        floor, printed_minus = 0.0, []
        for lap in s.valid_lap_ids():
            lo, hi = s.lap_window(lap)
            for k in range(201):
                t = lo + (hi - lo) * k / 200
                raw = s.delta_to_ideal_at(lap, t)
                if raw is None:
                    continue
                floor = min(floor, raw)
                view._update_diff_box(t, 42.0, lap)
                text = view.diff_box.text()
                if "-" in text.split("     ")[0]:
                    printed_minus.append((lap, round(t, 3), raw, text))
        assert floor < -1e-3, (
            f"fixture floor is {floor:.5f} s — it must go genuinely negative or the clamp is "
            f"untested here")
        assert not printed_minus, printed_minus[:3]
        # ...and the raw session value is UNCLAMPED, so the chart still sees the real excursion.
        assert min(s.delta_to_ideal_at(lap, t) or 0.0
                   for lap in s.valid_lap_ids()
                   for t in (lambda w: [w[0] + (w[1] - w[0]) * k / 200 for k in range(201)])(
                       s.lap_window(lap))) == floor
        print("test_the_hero_never_prints_a_minus_sign_against_the_ideal OK "
              f"(raw floor {floor:.5f} s, 0 minus signs printed)")


def test_the_hero_withholds_the_ideal_when_the_ideal_is_a_lap_you_drove():
    """THE SIXTH SURFACE. #211 gave five surfaces one gate — `ideal_donor_lap_id() is not None`,
    i.e. one lap was quickest through every corner and every straight, so the "ideal" IS that lap
    — and left the app's LARGEST TEXT ungated.

    Measured on the owner's real one-valid-lap recording (Sandown chapter 3, where ideal == best ==
    23.231227933627977 exactly), the hero printed `Δideal +0.00 s` for 2 324 of 2 324 samples of
    the lap, under a `vs ideal` chip whose tooltip still described a lap "stitched together … (not
    a single lap you have driven)" and above a header reading `SPEED · Δ TO BEST`, a greyed `Ideal
    lap` toggle and a Stats page with no IDEAL LAP section. `+0.00` against the ideal is the app
    saying "you are level with your ideal lap" — the sentence the share card was rewritten to stop
    printing. #211's PR body claimed the hero "already explains why on hover"; it does not, and
    that claim is what this test replaces.

    Forced here on the real view rather than mocked at the widget: the session's donor accessor is
    the one thing changed, and the state is then pushed through `rebuild_derived_views` — the same
    seam a start-line drag goes through — so this pins that the chip is re-synced by the real
    rebuild and not just by a helper called from a test."""
    with _Themed((1440, 900)) as view:
        s = view.session
        best = s.best_lap_id()
        assert s.ideal_donor_lap_id() is None, "fixture must START stitched"
        base_tip = view.ideal_readout_btn.toolTip()
        assert view.ideal_readout_btn.isEnabled() and view.ideal_readout_btn.isChecked()

        real = s.ideal_donor_lap_id
        s.ideal_donor_lap_id = lambda: best          # one lap won every segment
        try:
            view.rebuild_derived_views(reselect=False)
            btn = view.ideal_readout_btn
            assert not btn.isEnabled(), "the chip must go dead with its five siblings"
            assert not btn.isChecked(), "…and stop claiming the hero is measured against an ideal"
            assert btn.toolTip().startswith(base_tip), (
                "the reason is APPENDED — the chip must keep saying what it is")
            assert "quickest through every corner" in btn.toolTip(), btn.toolTip()

            # The hero, over the WHOLE best lap: not one sample may print Δideal.
            lo, hi = s.lap_window(best)
            ideal_frames = []
            for k in range(201):
                t = lo + (hi - lo) * k / 200
                view._update_diff_box(t, 42.0, best)
                if "ideal" in view.diff_box.text().lower():
                    ideal_frames.append((round(t, 3), view.diff_box.text()))
            assert not ideal_frames, ideal_frames[:3]
            assert view.diff_box.text().startswith("Δ "), view.diff_box.text()
            # ...and the tooltip does not print it one level down either — which is what
            # `theme.format_ideal_run` would have rendered here (`Δideal +0.00 s`).
            tip = view.diff_box.toolTip()
            assert "Δideal" not in tip, tip
            assert "IS that lap" in tip, tip

            # THE OTHER FIVE SURFACES AGREE, in the same frame.
            assert view.plots.ideal_btn.isEnabled() is False
            assert "IDEAL" not in view._plots_label.text(), view._plots_label.text()
            assert view.stats_view.t_theoretical.isHidden()
        finally:
            s.ideal_donor_lap_id = real
        # ...and it all comes back when the ideal is stitched again.
        view.rebuild_derived_views(reselect=False)
        assert view.ideal_readout_btn.isEnabled() and view.ideal_readout_btn.isChecked()
        assert view.ideal_readout_btn.toolTip() == base_tip
        view._update_diff_box(s.lap_window(best)[0], 42.0, best)
        assert view.diff_box.text().startswith("Δideal"), view.diff_box.text()
        print("test_the_hero_withholds_the_ideal_when_the_ideal_is_a_lap_you_drove OK "
              "(201 samples, 0 printed Δideal in the one-donor state)")


def test_the_hero_says_on_screen_that_its_ideal_readout_is_floored():
    """F3. `theme.format_ideal_run` clamps the DISPLAYED Δideal at 0 and the Δ chart 130 px below
    draws the same quantity UNCLAMPED — deliberately, both of them. But the clamp was stated only
    in a source comment, so on the default screen (best lap selected → the chart is baselined to
    the ideal) the two surfaces visibly disagree: on SD_30_08 the curve sits 8.9 px below its own
    y = 0 line on 297 of 1 308 frames while the hero above it prints `+0.00`.

    The clause appears exactly where the clamp is doing visible work, in BOTH toggle states, and
    is silent otherwise — a note on every frame would be noise about a number that is usually
    positive. The negative control is a positive sample from the same lap."""
    with _Themed((1440, 900)) as view:
        s = view.session
        worst = (0.0, None, None)
        for lap in s.valid_lap_ids():
            lo, hi = s.lap_window(lap)
            for k in range(201):
                t = lo + (hi - lo) * k / 200
                raw = s.delta_to_ideal_at(lap, t)
                if raw is not None and raw < worst[0]:
                    worst = (raw, lap, t)
        raw, lap, t = worst
        assert raw < -theme.DELTA_EVEN_EPS_S, (
            f"fixture floor is {raw:.5f} s — it must clear the dead band or this asserts nothing")

        for checked in (True, False):
            view.ideal_readout_btn.setChecked(checked)
            view._update_diff_box(t, 42.0, lap)
            tip = view.diff_box.toolTip()
            assert "floors at +0.00" in tip, (checked, tip)
            assert f"{abs(raw):.2f} s up on the lap that donated" in tip, (checked, tip)
            assert "dip below its own zero line" in tip, (checked, tip)
        # The hero itself still prints no minus sign — the clause explains the clamp, it does not
        # undo it.
        view.ideal_readout_btn.setChecked(True)
        view._update_diff_box(t, 42.0, lap)
        assert "-" not in view.diff_box.text().split("     ")[0], view.diff_box.text()

        # NEGATIVE CONTROL: a sample where the raw value is comfortably positive says nothing.
        pos = next(tt for tt in (s.lap_window(lap)[0] + (s.lap_window(lap)[1]
                                                         - s.lap_window(lap)[0]) * k / 200
                                 for k in range(201))
                   if (s.delta_to_ideal_at(lap, tt) or 0.0) > 0.05)
        for checked in (True, False):
            view.ideal_readout_btn.setChecked(checked)
            view._update_diff_box(pos, 42.0, lap)
            assert "floors at +0.00" not in view.diff_box.toolTip(), (checked,
                                                                      view.diff_box.toolTip())
        view.ideal_readout_btn.setChecked(True)
        print("test_the_hero_says_on_screen_that_its_ideal_readout_is_floored OK "
              f"(clause shown at raw {raw:.4f} s, silent at +{s.delta_to_ideal_at(lap, pos):.4f} s)")


def test_the_hero_never_recommends_the_reference_that_cannot_move():
    """D4-08. The census says this readout is the ONLY surface above 13 px in the whole first
    painted frame (22 px, against 167 at 13 and 8 at 11). The DEFAULT reference no longer needs a
    note at all (see the test above — the ideal moves on the best lap now), but the OTHER one,
    reachable in one click from the same header, still hands the user a permanent 0.00: on the best
    lap Δ-to-best is not near zero, it IS zero, because that lap is the reference.

    So the advice is measured here rather than reviewed: the note that survives must name an action
    this session's own numbers say produces a number that moves, and must not name the one that
    cannot. Measured on the real recording over the whole default lap, 400 samples: Δ-to-best
    prints a non-zero value on 0.0 % of it."""
    with _Themed((1440, 900)) as view:
        s = view.session
        best = s.best_lap_id()
        others = [i for i in s.valid_lap_ids() if i != best]
        assert best is not None and others, "need a best lap and at least one other"

        def sweep(fn, lap, n=200):
            lo, hi = s.lap_window(lap)
            return max(abs(fn(lap, lo + (hi - lo) * k / n) or 0.0) for k in range(n + 1))

        # (a) the action the note MUST NOT name: it produces a number that never moves.
        cannot_move = sweep(s.delta_at_lap, best)
        assert cannot_move <= theme.DELTA_EVEN_EPS_S, (
            f"Δ-to-best moved {cannot_move:.4f} s on the BEST lap — if that is now real, the note "
            f"below may name it again, but re-measure on a real recording first")
        # (b) the reference that DOES move — on any lap, including the best one, which is why the
        # default branch carries no note at all any more.
        can_move = sweep(s.delta_to_ideal_at, others[0])
        can_move_here = sweep(s.delta_to_ideal_at, best)
        assert can_move > theme.DELTA_EVEN_EPS_S, (
            f"picking another lap only moved Δideal {can_move:.4f} s — the note's advice is no "
            f"longer true on this fixture and the copy needs rewriting, not the test")
        assert can_move_here > theme.DELTA_EVEN_EPS_S, (
            f"Δideal moved {can_move_here:.4f} s on the BEST lap — if that is now ~0 the ideal has "
            f"regressed to the best lap and the deleted apology was right after all")

        # The DEFAULT reference needs no advice: it is not stuck, so it does not apologise.
        view._update_diff_box(0.0, 42.0, best)
        tip = view.diff_box.toolTip()
        assert "Pick another lap" not in tip, (
            f"Δideal moves {can_move_here:.4f} s on this very lap — there is nothing to excuse: "
            f"{tip!r}")

        # The other reference, one click away, DOES explain itself — and never by recommending a
        # switch back to the reference measured above as incapable of moving on this lap.
        view.ideal_readout_btn.setChecked(False)
        view._update_diff_box(0.0, 42.0, best)
        flipped = view.diff_box.toolTip()
        assert "reference" in flipped and "Pick another lap" in flipped, flipped
        assert "Δ-to-best" not in flipped, flipped
        view._update_diff_box(0.0, 42.0, others[0])
        assert "Pick another lap" not in view.diff_box.toolTip(), view.diff_box.toolTip()
        view.ideal_readout_btn.setChecked(True)
        print("test_the_hero_never_recommends_the_reference_that_cannot_move OK "
              f"(Δ-to-best max {cannot_move:.4f} s on the best lap; "
              f"Δideal max {can_move:.4f} s on lap {others[0]})")


def _run_all():
    test_charts_header_names_the_baseline_at_every_width_it_can_reach()
    test_the_header_names_a_cross_recording_reference_at_both_shipped_sizes()
    test_no_charts_or_map_control_is_ever_centre_clipped()
    test_map_sector_buttons_carry_their_labels_on_hover()
    test_no_panel_chrome_children_ever_overlap_at_the_column_minimum()
    test_the_charts_column_floor_is_the_headers_own_honest_need()
    test_every_maximize_button_clears_the_hit_target_floor()
    test_the_hero_is_no_longer_a_structural_zero_on_the_best_lap()
    test_the_hero_never_prints_a_minus_sign_against_the_ideal()
    test_the_hero_withholds_the_ideal_when_the_ideal_is_a_lap_you_drove()
    test_the_hero_says_on_screen_that_its_ideal_readout_is_floored()
    test_the_hero_never_recommends_the_reference_that_cannot_move()
    print("ALL CHARTS/MAP PANEL-CHROME TESTS OK")


if __name__ == "__main__":
    _run_all()
