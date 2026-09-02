"""Regression tests for the three QA-sweep HIGHs in studio/central_view.py (batch B01).

  * L2-01 — one splitter drag DELETED a whole column. Qt's default childrenCollapsible turns an
    overshoot past a section's minimum into a collapse to 0 px rather than a clamp, so a +740 px
    drag of the main handle took [515, 917] to [1432, 0]; the 400 ms debounce persisted that to
    prefs.json and every relaunch reopened with MAP + CHARTS gone. Two guards now: the splitters
    are non-collapsible, and _apply_grid_sizes refuses a stored zero so a prefs file written by an
    older build cannot resurrect the deletion. The maximize gesture legitimately WANTS a 0, so it
    goes through _collapse_sizes — pinned here too, because making the splitters non-collapsible
    silently clamps a plain setSizes([full, 0]) to [1076, 360] and would leave a "maximized" panel
    still sharing the window with its sibling.

  * MAP-02 — a start/finish drag that left zero valid laps was written to the sidecar (replacing
    the user's last good saved line with 221 bytes that Session.apply_timing_lines_latlon's revert
    guard rejects on every subsequent open) AND pushed onto the undo stack (where
    Session.undo_timing_lines deliberately refuses to consume a snapshot the same guard rejects,
    so Cmd+Z became a permanent no-op). Both writers are now gated on the same predicate the
    loader applies: a segmentation with no valid lap is never recorded anywhere.

  * L3-01 — the Corners table's four speed tooltips said km/h over cells holding mph (wrong by
    1.61x). CentralView.__init__ seeds each sub-view's unit by direct field assignment and then
    re-applies the ONE side effect that the rebuild does not re-run, plots._apply_speed_axis_label;
    CornerTable._apply_corner_tips was never re-applied. VERIFICATION TRAP: the tips self-heal on
    any real unit CHANGE, so a test that toggles units — or that drives the View menu when the
    persisted preference already differs from the unit requested — passes on the broken code. The
    test below therefore exercises the CONSTRUCTOR seam directly, which is where the persisted
    preference actually arrives, and never touches a unit action.

Run: QT_QPA_PLATFORM=offscreen python tests/test_grid_and_timing_guards.py
"""
import json
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

# The real production CentralView over the deterministic stadium-loop synthetic — the same fixture
# the rest of the real-Qt view tests build on.
from test_central_view_realqt import _real_central_view  # noqa: E402

from studio.central_view import CentralView  # noqa: E402


def _settle(n=6):
    for _ in range(n):
        _APP.processEvents()


def _user_drag(splitter, index, dx=0, dy=0, steps=12):
    """A FAITHFUL handle drag. QSplitterHandle.mouseMoveEvent reads e.globalPosition(), so the
    6-arg QMouseEvent form is mandatory — the 5-arg form leaves globalPosition() at the origin and
    the divider snaps to the top/left, manufacturing a collapse that no user could produce. >=10
    move steps, because a single jump can be undone by the next layout pass."""
    handle = splitter.handle(index)
    loc0 = QPointF(handle.width() / 2.0, handle.height() / 2.0)
    gp0 = QPointF(handle.mapToGlobal(QPoint(int(loc0.x()), int(loc0.y()))))
    _APP.sendEvent(handle, QMouseEvent(QEvent.MouseButtonPress, loc0, gp0, Qt.LeftButton,
                                       Qt.LeftButton, Qt.NoModifier))
    for k in range(1, steps + 1):
        off = QPointF(dx * k / steps, dy * k / steps)
        _APP.sendEvent(handle, QMouseEvent(
            QEvent.MouseMove, loc0 + off, gp0 + off, Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        _APP.processEvents()
    _APP.sendEvent(handle, QMouseEvent(
        QEvent.MouseButtonRelease, loc0 + QPointF(dx, dy), gp0 + QPointF(dx, dy),
        Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    _settle()
    return splitter.sizes()


def _shown_view():
    view, _s, _t0, _t1 = _real_central_view()
    view.resize(1440, 900)
    view.show()
    _settle(8)
    return view


# ------------------------------------------------------------------ L2-01: the drag
def test_no_drag_can_delete_a_panel():
    view = _shown_view()
    for name in ("_main_splitter", "_left_splitter", "_right_splitter"):
        splitter = getattr(view, name)
        assert splitter.childrenCollapsible() is False, name

    # The measured defect: +740 was the threshold, and everything above it deleted the column.
    for dx in (700, 740, 800, 900):
        view._main_splitter.setSizes([515, 917])
        _settle()
        sizes = _user_drag(view._main_splitter, 1, dx=dx, steps=1)
        assert sizes[1] > 0, f"+{dx} px deleted the right column: {sizes}"
        assert sizes[1] >= 360, f"+{dx} px pushed the right column under its 360 px floor: {sizes}"

    # ...and the same gesture on the left column's handle, which used to delete VIDEO.
    view._left_splitter.setSizes([390, 450])
    _settle()
    sizes = _user_drag(view._left_splitter, 1, dy=-700)
    assert sizes[0] > 0, f"the upward drag deleted the video panel: {sizes}"
    assert view._video_panel.height() > 0 and view._video_panel.y() >= 0, \
        f"video panel off screen at {view._video_panel.geometry().getRect()}"
    view.hide()
    print("test_no_drag_can_delete_a_panel OK")


# ------------------------------------------- L2-01: maximize still needs a REAL zero
def test_maximize_still_fills_the_window_and_leaves_the_grid_uncollapsible():
    view = _shown_view()
    full_w = sum(view._main_splitter.sizes())
    for panel_attr, column_attr in (("_map_panel", "_right_splitter"),
                                    ("_table_panel", "_left_splitter"),
                                    ("_video_panel", "_left_splitter"),
                                    ("_plots_panel", "_right_splitter")):
        panel = getattr(view, panel_attr)
        view._toggle_panel_maximized(panel)
        _settle()
        main = view._main_splitter.sizes()
        column = getattr(view, column_attr).sizes()
        assert 0 in main, f"{panel_attr}: the other column was not collapsed ({main})"
        assert 0 in column, f"{panel_attr}: the sibling panel was not collapsed ({column})"
        assert panel.width() >= full_w - 8, \
            f"{panel_attr} is {panel.width()}px wide in a {full_w}px window — not maximized"

        # The collapse must survive a window resize (Qt re-runs doResize on every one of them)
        # AND must not have left the grid collapsible behind it.
        view.resize(1441, 900)
        _settle()
        assert 0 in view._main_splitter.sizes(), f"{panel_attr}: the resize un-maximized the panel"
        view.resize(1440, 900)
        _settle()
        for name in ("_main_splitter", "_left_splitter", "_right_splitter"):
            assert getattr(view, name).childrenCollapsible() is False, \
                f"{panel_attr} left {name} collapsible — a drag could delete a panel again"

        view._toggle_panel_maximized(panel)   # restore
        _settle()
        assert view._maximized_panel is None
        assert all(s > 0 for s in view._main_splitter.sizes()), view._main_splitter.sizes()
        assert all(s > 0 for s in view._left_splitter.sizes()), view._left_splitter.sizes()
        assert all(s > 0 for s in view._right_splitter.sizes()), view._right_splitter.sizes()
    view.hide()
    print("test_maximize_still_fills_the_window_and_leaves_the_grid_uncollapsible OK")


# ------------------------------------------------- L2-01: the persisted-layout half
def test_apply_grid_sizes_refuses_a_stored_deleted_panel():
    view = _shown_view()
    default = ([515, 917], [390, 450], [320, 520])
    for splitter, sizes in zip((view._main_splitter, view._left_splitter, view._right_splitter),
                               default, strict=True):
        splitter.setSizes(sizes)
    _settle()

    # Qt rescales a setSizes list to the splitter's real extent, so compare RATIOS, not pixels.
    def ratio(sizes):
        return sizes[0] / float(sum(sizes))

    # Exactly what the pre-fix build wrote to prefs.json after one +900 px drag. The main list is
    # rejected; the other two are independently sound and must still restore.
    view._apply_grid_sizes([[1432, 0], [393, 453], [322, 524]])
    _settle()
    assert view._main_splitter.sizes()[1] > 0, \
        f"a stored zero resurrected the deleted column: {view._main_splitter.sizes()}"
    assert abs(ratio(view._main_splitter.sizes()) - ratio([515, 917])) < 0.02, \
        f"the rejected list should leave the default in place: {view._main_splitter.sizes()}"
    assert abs(ratio(view._left_splitter.sizes()) - ratio([393, 453])) < 0.02, \
        view._left_splitter.sizes()
    assert abs(ratio(view._right_splitter.sizes()) - ratio([322, 524])) < 0.02, \
        view._right_splitter.sizes()

    # A sound layout still applies — the guard must not have become "reject everything".
    view._apply_grid_sizes([[600, 832], [400, 446], [330, 516]])
    _settle()
    assert abs(ratio(view._main_splitter.sizes()) - ratio([600, 832])) < 0.02, \
        view._main_splitter.sizes()

    # And the other malformed shapes the guard has always rejected.
    for bad in ([[1432, 0, 7], None, None], [["a", "b"], None, None], [[-5, 900], None, None]):
        before = view._main_splitter.sizes()
        view._apply_grid_sizes(bad)
        _settle()
        assert view._main_splitter.sizes() == before, f"{bad} was applied"
    view.hide()
    print("test_apply_grid_sizes_refuses_a_stored_deleted_panel OK")


# --------------------------------------------------------------- MAP-02: both writers
def _fake_view(valid_lap_ids, path):
    """The duck-typed slice of CentralView that _save_sidecar / _on_lines actually touch, so the
    two guards can be driven with no widget tree at all."""
    calls = SimpleNamespace(pushed=0, resegmented=0, rebuilt=0, emitted=0)
    session = SimpleNamespace(
        valid_lap_ids=lambda: list(valid_lap_ids),
        track_name="Daytona MK",
        timing_user_confirmed=True,
        timing_lines_latlon=lambda: ([[51.376, -0.360], [51.377, -0.361]], []),
        push_timing_history=lambda: setattr(calls, "pushed", calls.pushed + 1),
    )

    def set_timing_lines(_start, _sectors):
        calls.resegmented += 1

    session.set_timing_lines = set_timing_lines
    view = SimpleNamespace(
        session=session, _sidecar_path=path, calls=calls,
        _comparing=lambda: False,
        video=SimpleNamespace(set_compare_enabled=lambda _on: None),
        rebuild_derived_views=lambda reselect=False: setattr(calls, "rebuilt", calls.rebuilt + 1),
        timingEdited=SimpleNamespace(emit=lambda: setattr(calls, "emitted", calls.emitted + 1)),
    )
    view._save_sidecar = lambda: CentralView._save_sidecar(view)
    return view


def test_a_zero_lap_placement_is_never_written_to_the_sidecar():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "GX010099.pacer.json")

        # A placement that leaves at least one lap is persisted, as it always was.
        CentralView._save_sidecar(_fake_view([0, 1], path))
        assert os.path.exists(path)
        good = open(path, "rb").read()
        assert json.loads(good)["confirmed"] is True

        # One that leaves none is refused — and, critically, the last good file is left INTACT
        # rather than overwritten with 221 bytes the loader's revert guard always throws away.
        CentralView._save_sidecar(_fake_view([], path))
        assert open(path, "rb").read() == good, "the zero-lap placement overwrote the good sidecar"

        # With no sidecar on disk, a zero-lap placement must not create one either.
        os.remove(path)
        CentralView._save_sidecar(_fake_view([], path))
        assert not os.path.exists(path), "a zero-lap placement created a sidecar the loader rejects"
    print("test_a_zero_lap_placement_is_never_written_to_the_sidecar OK")


def test_a_zero_lap_state_is_never_pushed_onto_the_undo_stack():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "GX010099.pacer.json")

        # Edit 1 of a two-handle drag: the PRE-edit state still has a lap, so it is snapshotted —
        # that snapshot is the one Cmd+Z restores.
        view = _fake_view([0], path)
        CentralView._on_lines(view, None, [])
        assert view.calls.pushed == 1, view.calls
        assert view.calls.resegmented == 1 and view.calls.rebuilt == 1 and view.calls.emitted == 1

        # Edit 2: the first release already emptied the lap set, so the pre-edit state is itself
        # unrestorable. Pushing it is what made Undo a permanent no-op (Session.undo_timing_lines
        # peeks it, the revert guard refuses it, and it is deliberately never consumed).
        view = _fake_view([], path)
        CentralView._on_lines(view, None, [])
        assert view.calls.pushed == 0, "a zero-lap state was pushed onto the undo stack"
        # The edit itself still applies on screen — only the RECORD of it is refused.
        assert view.calls.resegmented == 1 and view.calls.rebuilt == 1 and view.calls.emitted == 1
    print("test_a_zero_lap_state_is_never_pushed_onto_the_undo_stack OK")


# ------------------------------------------------------------------------- L3-01
def _unit_tips(corner_table):
    """The Corners header tooltips that name a speed unit, in column order."""
    table = corner_table.table
    return [table.horizontalHeaderItem(c).toolTip()
            for c in range(table.columnCount())
            if "km/h" in table.horizontalHeaderItem(c).toolTip()
            or "mph" in table.horizontalHeaderItem(c).toolTip()]


def test_a_persisted_mph_preference_reaches_the_corner_tooltips():
    view, session, _t0, _t1 = _real_central_view()
    view.hide()

    # The km/h default: unchanged, and the baseline for "these tips name a unit at all".
    kmh_tips = _unit_tips(view.corner_table)
    assert len(kmh_tips) == 4, kmh_tips
    assert all("km/h" in t for t in kmh_tips), kmh_tips

    # The defect's own path: the unit arrives through the CONSTRUCTOR (a persisted mph preference),
    # so no unit-changed signal ever fires and nothing re-applies the tips.
    mph_view = CentralView(session, ["/tmp/stadium.MP4"], sidecar_path=None, speed_unit="mph")
    _settle()
    assert mph_view.corner_table._speed_unit == "mph"
    mph_tips = _unit_tips(mph_view.corner_table)
    assert len(mph_tips) == 4, mph_tips
    bad = [t for t in mph_tips if "km/h" in t]
    assert not bad, f"{len(bad)} of 4 Corners tooltips say km/h over mph cells: {bad}"

    # ...and the sibling side effect on the same seam stays applied (the charts y-axis label).
    assert "mph" in mph_view.plots.p_speed.getAxis("left").labelText, \
        mph_view.plots.p_speed.getAxis("left").labelText
    mph_view.dispose()
    mph_view.hide()
    print("test_a_persisted_mph_preference_reaches_the_corner_tooltips OK")


def _run_all():
    test_a_persisted_mph_preference_reaches_the_corner_tooltips()
    test_no_drag_can_delete_a_panel()
    test_maximize_still_fills_the_window_and_leaves_the_grid_uncollapsible()
    test_apply_grid_sizes_refuses_a_stored_deleted_panel()
    test_a_zero_lap_placement_is_never_written_to_the_sidecar()
    test_a_zero_lap_state_is_never_pushed_onto_the_undo_stack()
    print("ALL OK")


if __name__ == "__main__":
    _run_all()
