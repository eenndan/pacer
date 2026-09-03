"""Regression tests for the CHARTS/MAP header width budget (QA-sweep batch B02).

Six findings, one bar. The charts header carries a 391 px hero readout, its reference chip, two
chart toggles, the x-axis combo and the ⛶ button inside a quadrant that is 917 px at the app's own
1440x900 default — over-subscribed by construction — and every finding here is a way that
over-subscription surfaced:

  * L6-01 (REGRESSION of #125) — the bar led with "Δideal" while the chart under it plotted
    Δ-to-BEST, and the label naming the chart's baseline was hidden at every width the app ships
    at. #122 made that label the bar's first casualty while it genuinely was decorative; #125 then
    handed it the baseline naming without moving it up the yield order, so #122's gate won 100 % of
    the time (needed 1040 px against a 917 px bar; the label reappeared only from a ~1633 px
    window, and the commit records verification at 1511x940). Nothing on screen was FALSE — the
    y-axis and the legend both name the baseline — but the header led with a different one.
  * L2-02 (REGRESSION of #125) — with the label hidden the controls still did not fit at 1280x800:
    "Brake/Throttle" (88 px of text) painted into a 60 px content box, "Ideal lap" (52) into 24 and
    "x: distance" (67) into 40, all centre-clipped with no ellipsis, and the combo had no tooltip.
    The file's own comment promised these "elide to their icon (meaning intact in the tooltip)";
    nothing implemented it. NOTE the trap: qa.strings-style PAD estimates put those shortfalls at
    2/2/7 px — the real QStyle content rects put them at 24/24/27, so these tests measure
    SE_PushButtonContents / SC_ComboBoxEditField, never an estimate.
  * L2-03 — the right column's 360 px minimum was below the bar's own minimum, so at that minimum
    QHBoxLayout resolved the shortfall by letting children OVERLAP: the hero ran 39 px past the
    header's right edge and the amber chip painted 307 px inside the hero's rect.
  * L2-04 — the same column minimum squeezed the MAP header's buttons into "ld sect" / "et sec",
    and neither sector button carried a tooltip (the destructive one included).
  * L2-08 — the ⛶ buttons were 26x22, under the 24x24 hit-target floor, and they are the only
    always-visible affordance that restores a maximized panel.
  * IA-03 — the largest text in the window (the hero, 16.05 px cap, 1.70x the lap-table cell font)
    is Δ-to-ideal on the lap the app opens on, which is the BEST lap: the ideal is stitched from
    the driver's own best sections, so it is structurally ~0 there (max |Δ| measured across a whole
    real best lap: 0.08 s). The type scale lives in theme.py's QSS, so the size is not this file's
    to change — what IS fixable here is the honesty: say why the number cannot move.

Run: QT_QPA_PLATFORM=offscreen python tests/test_charts_header_budget.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QStyle,
    QStyleOptionButton,
    QStyleOptionComboBox,
    QWidget,
)

_APP = QApplication.instance() or QApplication([])

# The real production CentralView over the deterministic stadium-loop synthetic — the same fixture
# the rest of the real-Qt view tests build on.
from test_central_view_realqt import _real_central_view  # noqa: E402

from studio import plots_view, theme  # noqa: E402
from studio.central_view import (  # noqa: E402
    _PLOTS_CHIP_BEST,
    _PLOTS_CHIP_REF,
    _PLOTS_LABEL_BEST,
    _PLOTS_LABEL_REF,
)


def _settle(n=6):
    for _ in range(n):
        _APP.processEvents()


class _Themed:
    """A shown, REAL-themed CentralView at `size`. The theme is mandatory, not decoration: every
    number the header budget is made of comes from the QSS's painted fonts (the hero is styled in
    the mono stack at HERO/600), so measuring an unthemed view measures a different app."""

    def __init__(self, size=(1440, 900)):
        self.view, self.session = _real_central_view()[:2]
        self._prior = (_APP.styleSheet(), _APP.font(), _APP.palette())
        theme.apply_theme(_APP)
        self.view.resize(*size)
        self.view.show()
        _settle(8)

    def __enter__(self):
        return self.view

    def __exit__(self, *exc):
        _APP.setStyleSheet(self._prior[0])
        _APP.setFont(self._prior[1])
        _APP.setPalette(self._prior[2])
        self.view.hide()
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
    """Give the charts column exactly `px` (the header spans it), settled."""
    total = sum(view._main_splitter.sizes())
    view._main_splitter.setSizes([total - px, px])
    _settle(6)


def _charts_controls(view):
    return (view.plots.brake_throttle_btn, view.plots.ideal_btn, view.plots.x_mode_combo)


def test_charts_bar_names_the_chart_baseline_at_the_shipped_default():
    """L6-01. At the app's own 1440x900 the bar must name the baseline the LOWER CHART draws, and
    name the same one the y-axis does. Before this fix the label was hidden at 1440 / 1500 / 1600
    and only returned from a ~1633 px window."""
    with _Themed((1440, 900)) as view:
        label, header = view._plots_label, view._plots_header_widget
        assert header.width() >= 900, header.width()   # the shipped quadrant, not a wide window
        assert label.isVisible(), f"baseline naming hidden in a {header.width()}px bar"
        axis = view.plots.p_delta.getAxis("left").labelText
        wanted = "IDEAL" if "ideal" in axis.lower() else "BEST"
        assert wanted in label.text(), (label.text(), axis)
        print(f"test_charts_bar_names_the_chart_baseline_at_the_shipped_default OK "
              f"({label.text()!r} over {axis!r} in a {header.width()}px bar)")


def test_charts_bar_keeps_naming_the_baseline_when_the_toggles_must_yield():
    """L6-01, the other half: the naming outranks control TEXT. At 1280x800 the bar cannot hold
    both, so the toggles drop to their icon and the short chip stays — the inverse of the order
    #122 established and #125 inherited."""
    with _Themed((1280, 800)) as view:
        label = view._plots_label
        assert label.isVisible(), "the short baseline chip must survive a 1280x800 bar"
        assert "Δ" in label.text(), label.text()
        assert [b.text() for b, _n in view._plots_toggles] == ["", ""], "the toggles must yield"
        print(f"test_charts_bar_keeps_naming_the_baseline_when_the_toggles_must_yield OK "
              f"({label.text()!r})")


def test_the_bar_names_a_cross_recording_reference_at_both_shipped_sizes():
    """QA-W2R-03 + the width judgement it forces. The bar's label had exactly two states, so with a
    reference loaded it painted "SPEED · Δ TO BEST" over a chart measured against ANOTHER
    recording's lap. Adding the third state is the fix; choosing its WORDING is the risk, because
    this bar is over-subscribed by construction and #122/#125 both lost the naming to a caption
    that grew (L6-01 above).

    So the wording is pinned by width, not by taste: the REF pair must be no wider than the BEST
    pair it joins, which makes it impossible for this caption to cost the bar anything the shipped
    one does not already cost. Measured, the alternative "SPEED · Δ TO REFERENCE" needs 1033 px
    against an 815 px bar at 1280x800 and drops the fit pass all the way to its no-label floor
    tier — the naming would VANISH at the smaller of the app's two shipped sizes, which is exactly
    the regression this file exists to catch."""
    for size, want in (((1440, 900), "SPEED · Δ TO REF"), ((1280, 800), "Δ REF")):
        with _Themed(size) as view:
            label = view._plots_label
            budget = view._plots_budget or view._measure_plots_budget()
            view._set_delta_baseline_label(plots_view.DELTA_BASELINE_REFERENCE)
            assert label.isVisible(), f"baseline naming hidden at {size} with a reference loaded"
            assert label.text() == want, (size, label.text())
            assert "BEST" not in label.text(), label.text()
            # Neither REF string may be wider than the BEST string at the same tier.
            for ref_text, best_text in ((_PLOTS_LABEL_REF, _PLOTS_LABEL_BEST),
                                        (_PLOTS_CHIP_REF, _PLOTS_CHIP_BEST)):
                assert budget["labels"][ref_text] <= budget["labels"][best_text], (
                    f"{ref_text!r} ({budget['labels'][ref_text]}px) is wider than "
                    f"{best_text!r} ({budget['labels'][best_text]}px) — the bar cannot afford it")
            # The abbreviation has the recording behind it (the bar has room for no tier that does).
            assert label.toolTip(), "an abbreviated caption must carry its meaning on hover"
            # ...and the naming still outranks the control text, the order L6-01 established.
            assert [b.text() for b, _n in view._plots_toggles] == ["", ""]
            # Back to the local best: the caption follows the baseline in BOTH directions.
            view._set_delta_baseline_label(plots_view.DELTA_BASELINE_BEST)
            assert "BEST" in label.text(), label.text()
    print("test_the_bar_names_a_cross_recording_reference_at_both_shipped_sizes OK")


def test_no_charts_control_is_ever_centre_clipped():
    """L2-02. Measured from the real QStyle content rects at both the app's small default and the
    column minimum. On main: 88px of 'Brake/Throttle' in 60, 52 of 'Ideal lap' in 24, 67 of
    'x: distance' in 40 — and the combo, the one whose meaning is unrecoverable, had no tooltip."""
    with _Themed((1280, 800)) as view:
        assert view.plots.x_mode_combo.toolTip(), "the x-axis combo must say what it switches"
        _assert_readable(_charts_controls(view))
        _set_right_column(view, view._right_splitter.minimumWidth())
        _assert_readable(_charts_controls(view))
        print("test_no_charts_control_is_ever_centre_clipped OK")


def test_charts_header_children_never_overlap_at_the_column_minimum():
    """L2-03. Drag the main handle fully right: the column must clamp at the bar's own tightest
    need, not at a 360 px floor the bar cannot honour. On main the hero ran 39 px past the header
    and the amber chip sat 307 px inside the hero's rect, painting through the live number."""
    with _Themed((1280, 800)) as view:
        header = view._plots_header_widget
        _set_right_column(view, 360)                    # ask for far less than the bar can hold
        kids = sorted([k for k in header.children() if isinstance(k, QWidget) and k.isVisible()],
                      key=lambda k: k.geometry().x())
        prev = None
        for k in kids:
            g = k.geometry()
            assert g.right() < header.width(), (_bar_text(k), g, header.width())
            if prev is not None:
                assert g.x() > prev.geometry().right(), (_bar_text(k), g, prev.geometry())
            prev = k
        # The specific collision the naive "raise the floors" fix produces, pinned by name.
        assert view.ideal_readout_btn.geometry().x() > view.diff_box.geometry().right(), (
            "the 'vs ideal' chip must never paint inside the hero readout")
        # The column's floor IS the bar's tightest tier (no label, icon-only toggles). It sits
        # below the header's live minimumSizeHint, which includes whichever label the current tier
        # is showing — a drag past it drops that tier rather than being refused.
        assert header.width() == view._right_splitter.minimumWidth(), header.width()
        assert view._right_splitter.minimumWidth() == view._plots_header_need(
            view._plots_budget, "", icons=True), view._right_splitter.minimumWidth()
        print(f"test_charts_header_children_never_overlap_at_the_column_minimum OK "
              f"(clamped at {header.width()}px)")


def test_charts_header_budget_matches_the_layouts_own_chrome():
    """The arithmetic itself. QBoxLayout charges spacing only BETWEEN non-empty items and both
    addStretch spacers report empty, so the fixed chrome is margins + spacing x (visible - 1).
    The old pass summed the ⛶ button's 45 px sizeHint against its 26 px setFixedSize and charged
    the label's spacing twice: it demanded 1040 px where the layout needs 1013. Pinned as a
    boundary — one pixel under the computed need must drop a tier, one pixel over must not."""
    with _Themed((1633, 900)) as view:
        header, row = view._plots_header_widget, view._plots_header_widget.layout()
        brake, ideal, combo = _charts_controls(view)
        margins = row.contentsMargins()
        # Derived from the widgets and the layout themselves, NOT from the app's own constant, so
        # this fails on a build whose arithmetic disagrees rather than agreeing with it by
        # construction. Seven non-empty items => six gaps; the ⛶ button contributes its FIXED
        # width, not the larger sizeHint the old pass charged for it.
        need = (margins.left() + margins.right() + row.spacing() * 6
                + view._plots_label.sizeHint().width() + view.diff_box.minimumWidth()
                + view.ideal_readout_btn.sizeHint().width() + brake.sizeHint().width()
                + ideal.sizeHint().width() + combo.sizeHint().width()
                + view._plots_max_btn.width())
        _set_right_column(view, need)
        assert header.width() == need, (header.width(), need)
        assert view._plots_label.isVisible(), f"the label must fit in exactly {need}px"
        assert (brake.text(), ideal.text()) == ("Brake/Throttle", "Ideal lap"), "…with full text"
        _set_right_column(view, need - 1)
        assert (brake.text(), ideal.text()) == ("", ""), "one px under must drop a tier"
        assert view._plots_label.isVisible(), "…and the naming still outranks the control text"
        print(f"test_charts_header_budget_matches_the_layouts_own_chrome OK (need {need}px)")


def test_map_sector_buttons_carry_their_labels_on_hover():
    """L2-04. 'Add sector' and 'Reset sectors' painted as 'ld sect' / 'et sec' at the column
    minimum with empty tooltips — so the DESTRUCTIVE one was an unhoverable non-word."""
    with _Themed((1280, 800)) as view:
        for btn, name in ((view.map.add_sector_btn, "Add sector"),
                          (view.map.reset_sectors_btn, "Reset sectors")):
            assert btn.toolTip(), f"{name} has no tooltip"
            assert name.lower() in btn.toolTip().lower(), btn.toolTip()
        _set_right_column(view, view._right_splitter.minimumWidth())
        _assert_readable((view.map.snap_btn, view.map.add_sector_btn, view.map.reset_sectors_btn))
        print("test_map_sector_buttons_carry_their_labels_on_hover OK")


def test_every_maximize_button_clears_the_hit_target_floor():
    """L2-08. 26x22 is under the 24x24 floor, and these are the only always-visible way back from
    a maximized panel."""
    with _Themed((1440, 900)) as view:
        for name in ("video", "table", "map", "plots"):
            btn = getattr(view, f"_{name}_max_btn")
            assert btn.width() >= 24 and btn.height() >= 24, (name, btn.size())
        print("test_every_maximize_button_clears_the_hit_target_floor OK")


def test_hero_readout_explains_its_structural_zero_on_the_best_lap():
    """IA-03. The window's largest text is Δ-to-ideal, and on the best lap it cannot move: the
    ideal is stitched from that lap's own sections. Say so on hover — without dropping the
    Δ-to-best number the box promises to keep one hover away."""
    with _Themed((1440, 900)) as view:
        best = view.session.best_lap_id()
        assert best is not None, "the synthetic session must have a best lap"
        others = [i for i in view.session.valid_lap_ids() if i != best]
        assert view.ideal_readout_btn.isChecked(), "the hero leads with Δ-to-ideal by default"

        view._update_diff_box(0.0, 42.0, best)
        tip = view.diff_box.toolTip()
        assert "by construction" in tip, tip
        assert "best lap here" in tip, "the Δ-to-best number must survive the explanation"

        view._update_diff_box(0.0, 42.0, others[0])
        assert "by construction" not in view.diff_box.toolTip(), view.diff_box.toolTip()
        print("test_hero_readout_explains_its_structural_zero_on_the_best_lap OK")


def _run_all():
    test_charts_bar_names_the_chart_baseline_at_the_shipped_default()
    test_charts_bar_keeps_naming_the_baseline_when_the_toggles_must_yield()
    test_the_bar_names_a_cross_recording_reference_at_both_shipped_sizes()
    test_no_charts_control_is_ever_centre_clipped()
    test_charts_header_children_never_overlap_at_the_column_minimum()
    test_charts_header_budget_matches_the_layouts_own_chrome()
    test_map_sector_buttons_carry_their_labels_on_hover()
    test_every_maximize_button_clears_the_hit_target_floor()
    test_hero_readout_explains_its_structural_zero_on_the_best_lap()
    print("ALL CHARTS-HEADER BUDGET TESTS OK")


if __name__ == "__main__":
    _run_all()
