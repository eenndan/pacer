"""F4 compare-mode map ghost — controller emit + MapView ghost-item invariants (offscreen).

The ghost is a second, hollow, lap-B-accent marker on the track map shown ONLY while compare
mode is on: CompareController.tick() places it at lap B's trace position for the SAME t_b its
"Δ vs other" badge used (the secondary pane's own clock == equal elapsed-into-lap), via the
REAL session.index_at_time — the same lookup the red video marker's tick path resolves, so no
second time-alignment exists. Driven on a bare Session (tests/_synthetic) + a real MapView +
minimal fake video/plots/table recorders (the test_controllers idiom), checking:
  * tick places the ghost at exactly (tx[i], ty[i]) for i = index_at_time(t_b), across the lap;
  * the scrub bypass drives the ghost from the drag's own clamped SECONDARY target (target_b);
  * per-tick updates are setPos-only on ONE lazily-created item (identity stable — no churn);
  * compare exit REMOVES the item: the plot's item list is byte-identical to pre-compare;
  * outside compare the tick paths do ZERO ghost work (`ghost_updates` == 0, no item ever
    created) — instrumented like the rainbow rebuild counter;
  * the ghost is visually distinct from the video marker: hollow (NoBrush), smaller, not
    movable, lap-B accent pen (theme.CHART_SERIES[1] == map_view.GHOST_COLOR).
Run: python tests/test_map_ghost.py
"""
import os
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from _synthetic import bare_session, odometer  # noqa: E402

from studio import theme  # noqa: E402
from studio.compare_controller import CompareController  # noqa: E402
from studio.map_view import GHOST_COLOR, MARKER_COLOR, MapView  # noqa: E402
from studio.playback_state import PlaybackState  # noqa: E402


# --------------------------------------------------------------------- fixtures / fakes
def _session():
    """A bare Session with TWO laps cached (A id 3: 100→112 s; B id 7, the best: 300→311 s)
    AND the whole-trace arrays (tt/tx/ty/tv) MapView + index_at_time read: tt concatenates both
    laps' time ranges; tx/ty trace an ellipse so every index has a distinct (x, y). The laps
    shape feeds MapView's timing-line build (the test_rainbow_map stub idiom)."""
    a, b = 3, 7
    ta, da = odometer(121, 0.1, 100.0, 520.0)
    tb, db = odometer(111, 0.1, 300.0, 508.0, lambda u: 1.3 + 0.7 * np.sin(u) ** 2)
    s = bare_session({a: (ta, da), b: (tb, db)}, best=b, valid=[a, b])
    s.tt = np.concatenate([ta, tb])
    ang = np.linspace(0.0, 2 * np.pi, len(s.tt))
    s.tx = np.cos(ang) * 50.0
    s.ty = np.sin(ang) * 30.0
    s.tv = np.linspace(40.0, 120.0, len(s.tt))
    line = SimpleNamespace(first=SimpleNamespace(x=-60.0, y=0.0),
                           second=SimpleNamespace(x=-40.0, y=0.0))
    s.laps = SimpleNamespace(sectors=SimpleNamespace(start_line=line, sector_lines=[]))
    s.lap_trace_segments = lambda lid: [
        SimpleNamespace(xs=s.tx[:10], ys=s.ty[:10], measured=True)]
    # The map now opens SPEED-coloured, so set_current_lap paints the speed rainbow; give the stub
    # the per-lap channel surface (the test_rainbow_map idiom) so that harmless paint has data.
    s.lap_channels = lambda lid: {
        "t_media_s": s.tt, "x_m": s.tx, "y_m": s.ty,
        "speed_kmh": s.tv, "dist_m": np.linspace(0.0, 500.0, len(s.tt))}
    return s, a, b


class _FakeVideo:
    """Serves per-pane times to tick() and records the exit-compare side-effect; the g-meter
    reads as hidden so tick() skips the secondary-g feed (not under test here)."""

    def __init__(self):
        self.pane_times = {0: 0.0, 1: 0.0}
        self.badges = []
        self.exited = False

    def current_pane_time(self, side):
        return self.pane_times[side]

    def is_gmeter_visible(self):
        return False

    def set_pane_badge(self, side, text, colour):
        self.badges.append((side, text, colour))

    def exit_compare(self):
        self.exited = True


class _FakePlots:
    def __init__(self):
        self.lap_sets = []

    def set_laps(self, ids):
        self.lap_sets.append(list(ids))


class _FakeTable:
    def selected_lap_ids(self):
        return []


def _wire(session, map_view):
    """A CompareController over the REAL MapView + minimal fakes, wired as StudioWindow does."""
    video, plots, table = _FakeVideo(), _FakePlots(), _FakeTable()
    # F5: the compare controller now reads applied_t / writes followed_lap on a shared PlaybackState
    # instead of the get_applied_t / set_followed_lap callbacks. This test only drives tick() (the
    # ghost path), so a fresh state (applied_t None, followed_lap unused) matches the old lambdas.
    compare = CompareController(
        session, video, plots, table,
        playback=PlaybackState(),
        select_default=lambda: None,
        map_view=map_view,
    )
    return compare, video


def _pin(compare, a, b):
    """Pin the compared pair directly (the test_controllers idiom for driving tick())."""
    compare._compare = True
    compare._compare_a, compare._compare_b = a, b
    compare._compare_last_t = None  # force the first tick to apply


# ===================================================================== ghost placement
def test_tick_places_ghost_at_lap_b_trace_position():
    """At several elapsed-into-lap points the ghost sits EXACTLY at (tx[i], ty[i]) for
    i = index_at_time(t_b) — lap B's trace position at the secondary pane's own time, the same
    t_b the Δ badge was computed for. One item, created lazily on the first tick, then
    setPos-only (object identity stable across every update — zero churn)."""
    s, a, b = _session()
    mv = MapView(s)
    compare, video = _wire(s, mv)
    _pin(compare, a, b)
    assert mv._ghost is None, "ghost must not exist before the first compare tick"
    ta, tb = s._dist_cache[a][0], s._dist_cache[b][0]
    first_id = None
    n_items_after_create = None
    for frac in (0.1, 0.35, 0.6, 0.9):
        t_a = float(ta[0] + frac * (ta[-1] - ta[0]))
        t_b = float(tb[0] + frac * (tb[-1] - tb[0]))
        video.pane_times = {0: t_a, 1: t_b}
        compare.tick()
        i = s.index_at_time(t_b)
        g = mv._ghost
        assert g is not None
        assert (g.pos().x(), g.pos().y()) == (float(s.tx[i]), float(s.ty[i])), \
            (frac, g.pos(), s.tx[i], s.ty[i])
        if first_id is None:
            first_id = id(g)
            n_items_after_create = len(mv.plot.items)
        else:
            assert id(g) == first_id, "ghost item churned between ticks!"
            assert len(mv.plot.items) == n_items_after_create, "tick added/removed items!"
    assert mv.ghost_updates == 4, mv.ghost_updates
    # The badge for the SAME tick used the same t_b (one time-mapping, made visible twice).
    assert video.badges, "tick must still feed the Δ badges"
    print("test_tick_places_ghost_at_lap_b_trace_position OK")


def test_scrub_bypass_drives_ghost_from_secondary_target():
    """While a distance-locked scrub is in flight, tick() bypasses the early-out and reads the
    drag's own clamped targets — the ghost must follow scrub.target_b, not the stale pane time."""
    s, a, b = _session()
    mv = MapView(s)
    compare, video = _wire(s, mv)
    _pin(compare, a, b)
    tb = s._dist_cache[b][0]
    t_b = float(tb[0] + 0.42 * (tb[-1] - tb[0]))
    video.pane_times = {0: 100.0, 1: 300.0}  # stale pre-scrub pane times
    compare.scrub = SimpleNamespace(is_active=True, target=105.0, target_b=t_b)
    compare.tick()
    i = s.index_at_time(t_b)
    assert (mv._ghost.pos().x(), mv._ghost.pos().y()) == (float(s.tx[i]), float(s.ty[i]))
    print("test_scrub_bypass_drives_ghost_from_secondary_target OK")


# ===================================================================== enter / exit state
def test_exit_removes_ghost_and_restores_item_state():
    """Compare exit REMOVES the ghost item: the plot's item list (objects, order) and the red
    marker's visibility return byte-identical to pre-compare. A second exit is a safe no-op."""
    s, a, b = _session()
    mv = MapView(s)
    compare, video = _wire(s, mv)
    items_before = list(mv.plot.items)
    marker_visible_before = mv.marker.isVisible()
    _pin(compare, a, b)
    tb = s._dist_cache[b][0]
    video.pane_times = {0: 105.0, 1: float(tb[50])}
    compare.tick()
    assert mv._ghost is not None and mv._ghost in mv.plot.items
    assert len(mv.plot.items) == len(items_before) + 1
    compare.exit()
    assert video.exited, "exit must still tear down the panes"
    assert mv._ghost is None, "exit must clear the ghost"
    assert mv.plot.items == items_before, "item state must be byte-identical to pre-compare"
    assert mv.marker.isVisible() == marker_visible_before
    mv.clear_ghost()  # idempotent
    assert mv.plot.items == items_before
    print("test_exit_removes_ghost_and_restores_item_state OK")


def test_outside_compare_zero_ghost_work():
    """The non-compare tick paths (current-lap refresh + marker placement + an inactive
    controller tick) perform ZERO ghost work: the counter stays 0 and no item is ever created —
    the same instrumentation idiom as the rainbow rebuild counter."""
    s, a, _b = _session()
    mv = MapView(s)
    compare, _video = _wire(s, mv)  # compare never entered
    for k in range(100):
        mv.set_current_lap(a)
        mv.set_marker_index(k % len(s.tx))
        compare.tick()  # inactive: no pinned pair -> pure no-op
    assert mv.ghost_updates == 0, "inactive path touched the ghost!"
    assert mv._ghost is None, "inactive path created the ghost item!"
    mv.set_ghost_index(None)  # the None guard is also a no-op (no item, no count)
    assert mv.ghost_updates == 0 and mv._ghost is None
    print("test_outside_compare_zero_ghost_work OK")


# ===================================================================== visual distinctness
def test_ghost_visually_distinct_from_marker():
    """Hollow ring (NoBrush), smaller than the video marker, NOT movable (display-only), in the
    lap-B accent (theme.CHART_SERIES[1]) — never confusable with the filled coral marker."""
    s, _a, _b = _session()
    mv = MapView(s)
    mv.set_ghost_index(5)
    g = mv._ghost
    assert g.brush.style() == Qt.NoBrush, "ghost must be hollow"
    assert mv.marker.brush.style() != Qt.NoBrush, "the video marker stays filled"
    assert g.scale < mv.marker.scale, (g.scale, mv.marker.scale)
    assert g.movable is False and mv.marker.movable is True
    assert GHOST_COLOR == theme.CHART_SERIES[1]
    assert g.pen.color().name().upper() == GHOST_COLOR.upper()
    assert g.pen.color().name().upper() != mv.marker.pen.color().name().upper()
    assert GHOST_COLOR.upper() != MARKER_COLOR.upper()
    print("test_ghost_visually_distinct_from_marker OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} MAP GHOST TESTS PASSED")
