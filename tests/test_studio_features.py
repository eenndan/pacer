"""Pure-Python tests for the studio UI features F1–F5 (no pacer, no telemetry file, fast).

These exercise the load-bearing pure logic directly on synthetic data:
  * F1 — the numeric sort key of the lap-table cell (`_NumItem.__lt__`): times/splits sort by
    their underlying float, blanks/NaN sort last.
  * F3 — `Session.nearest_index_in_lap` / `nearest_time_in_lap`: the marker drag is constrained
    to ONE lap's points and clamped to its time window (built on a bare Session, no pacer).
  * F5 — per-sector session-best = the per-column MINIMUM split across valid laps.
  * Auto-follow — `StudioWindow._follow_current_lap`: the speed+delta charts switch to the
    playhead's lap (vs best) only on a lap-change EDGE; None (lead-in) HOLDS the last lap; the
    table re-select uses the programmatic (no-seek) path so it never fights playback.
Run: python tests/test_studio_features.py
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# QTableWidgetItem needs a QApplication for _NumItem; create one offscreen.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio.lap_table import (  # noqa: E402
    NUM_ROLE,
    _best_split_per_sector_impl,
    _NumItem,
)
from studio.session import Session  # noqa: E402


# --------------------------------------------------------------------- F1
def _item(num, text=""):
    it = _NumItem(text)
    it.setData(NUM_ROLE, num)
    return it


def test_numeric_sort_key_orders_by_value_not_text():
    """'1:08.408' (key 68.408) must sort BELOW '1:10.004' (key 70.004), unlike the lexical
    text order. The cell compares on the numeric key in NUM_ROLE."""
    fast = _item(68.408, "1:08.408")
    slow = _item(70.004, "1:10.004")
    assert fast < slow
    assert not (slow < fast)
    # A split "S2 9.9" vs "23.1": 9.9 < 23.1 even though "23.1" < "9.9" lexically.
    assert _item(9.9, "9.90") < _item(23.1, "23.10")
    print("test_numeric_sort_key_orders_by_value_not_text OK")


def test_numeric_sort_key_blanks_sort_last():
    """Blank/NaN-key cells (partial laps with fewer splits) sort to the bottom in BOTH
    directions — never above a real value."""
    real = _item(12.3, "12.30")
    blank = _item(float("nan"), "")
    assert real < blank          # real before blank ascending
    assert not (blank < real)    # blank never sorts before a real value
    # Two blanks compare equal-ish (neither strictly less).
    assert not (_item(float("nan")) < _item(float("nan")))
    print("test_numeric_sort_key_blanks_sort_last OK")


# --------------------------------------------------------------------- F5
def test_best_split_per_sector_is_column_min():
    """The purple per-sector session-best is the per-column MINIMUM across valid laps,
    computed independently per column; a column with no data → None."""
    splits = {
        0: [34.5, 11.0, 22.9],
        1: [34.2, 10.6, 23.1],  # min S1 (34.2) and min S2 (10.6) here
        2: [35.0, 11.2, 22.6],  # min S3 (22.6) here
        3: [34.9],              # partial lap: only S1 present
    }
    best = _best_split_per_sector_impl(splits, n_splits=3)
    assert best == [34.2, 10.6, 22.6], best
    # No-data column -> None.
    assert _best_split_per_sector_impl({0: []}, n_splits=2) == [None, None]
    print("test_best_split_per_sector_is_column_min OK")


# --------------------------------------------------------------------- F3
def _bare_session_with_lap(lap_id=2):
    """A bare Session carrying ONE lap's cached xy + (times,dists), patched so the F3 helpers
    run with no pacer. The lap is a simple curve; a far-away point must still resolve to the
    NEAREST point WITHIN this lap (never escape it) and the time clamps to the lap window."""
    s = Session.__new__(Session)
    n = 50
    t0 = 100.0
    xs = np.linspace(0.0, 100.0, n)
    ys = np.sin(np.linspace(0, math.pi, n)) * 20.0
    ts = t0 + np.arange(n) * 0.1
    dists = np.linspace(0.0, 250.0, n)
    s._dist_cache = {lap_id: (ts, dists)}
    # _lap_xy_t calls _lap_trace_xyt + _lap_time_dist; stub _lap_trace_xyt to our arrays.
    s._lap_trace_xyt = lambda lid: (xs, ys, ts)  # noqa: ARG005
    return s, lap_id, xs, ys, ts


def test_nearest_index_in_lap_stays_in_lap():
    s, lid, xs, ys, ts = _bare_session_with_lap()
    # A query point well outside the lap's x-range resolves to an ENDPOINT index in [0, n).
    i = s.nearest_index_in_lap(lid, 1000.0, 0.0)
    assert i == len(xs) - 1, i  # nearest is the far (max-x) end of the lap
    j = s.nearest_index_in_lap(lid, -1000.0, 0.0)
    assert j == 0, j
    # A point near the middle resolves to a middle index — never out of range.
    k = s.nearest_index_in_lap(lid, 50.0, 25.0)
    assert 0 <= k < len(xs)
    print("test_nearest_index_in_lap_stays_in_lap OK")


def test_nearest_time_in_lap_clamps_to_window():
    s, lid, xs, ys, ts = _bare_session_with_lap()
    t_lo, t_hi = float(ts[0]), float(ts[-1])
    # Far past the end → clamps to the lap's last time; far before → first time.
    assert abs(s.nearest_time_in_lap(lid, 1e6, 0.0) - t_hi) < 1e-9
    assert abs(s.nearest_time_in_lap(lid, -1e6, 0.0) - t_lo) < 1e-9
    # An interior point lands strictly inside the window.
    t_mid = s.nearest_time_in_lap(lid, 50.0, 25.0)
    assert t_lo <= t_mid <= t_hi
    print("test_nearest_time_in_lap_clamps_to_window OK")


# ----------------------------------------------------------- auto-follow (lap-change edge)
class _Recorder:
    """A tiny stand-in for the table / plots / video collaborators, recording the calls the
    follow logic makes so we can assert the EDGE semantics without pacer / a telemetry file."""

    def __init__(self):
        self.selected = []      # every table.select(ids)
        self.lap_sets = []      # every plots.set_laps(ids)
        self.seeks = []         # any video.seek(...) — must stay EMPTY (no playback fight)
        self.placed = []        # plots.place_cursors_at_time(t) while dragging
        self._dragging = False

    # table
    def select(self, ids):
        self.selected.append(list(ids))

    # plots
    def set_laps(self, ids):
        self.lap_sets.append(list(ids))

    def is_dragging(self):
        return self._dragging

    def place_cursors_at_time(self, t):
        self.placed.append(t)

    # video
    def seek(self, t):
        self.seeks.append(t)


def _follow_window(best_lap):
    """A StudioWindow built without __init__ (no Qt/pacer), with the collaborators the
    auto-follow logic touches stubbed by a recorder, so `_follow_current_lap` runs unchanged."""
    from studio.app import StudioWindow  # local import: keeps the F1/F5 tests pacer-free

    w = StudioWindow.__new__(StudioWindow)
    rec = _Recorder()
    w._followed_lap = None
    w.table = rec
    w.plots = rec
    w.video = rec

    class _Sess:
        def best_lap_id(self):
            return best_lap

    w.session = _Sess()
    return w, rec


def test_follow_switches_only_on_lap_edge():
    """Crossing into a new lap switches the charts to [current, best]; staying in the same lap
    does NOT re-switch (edge only); the table re-select uses the no-seek programmatic path."""
    w, rec = _follow_window(best_lap=9)
    # Enter lap 0 → switch to [0, 9].
    w._follow_current_lap(0, t=10.0)
    assert w._followed_lap == 0
    assert rec.lap_sets[-1] == [0, 9] and rec.selected[-1] == [0, 9]
    # Same lap again (another tick) → NO new switch (edge only).
    n_before = len(rec.lap_sets)
    w._follow_current_lap(0, t=11.0)
    assert len(rec.lap_sets) == n_before, "re-switched within the same lap (not an edge)"
    # Cross into lap 1 → switch to [1, 9].
    w._follow_current_lap(1, t=80.0)
    assert w._followed_lap == 1 and rec.lap_sets[-1] == [1, 9]
    # Cross 3 more boundaries → exactly 3 more switches (count == boundaries).
    base = len(rec.lap_sets)
    for lid in (2, 3, 4):
        w._follow_current_lap(lid, t=100.0 + lid)
    assert len(rec.lap_sets) - base == 3
    # No seek was EVER emitted by the follow (it must not fight playback).
    assert rec.seeks == [], rec.seeks
    print("test_follow_switches_only_on_lap_edge OK")


def test_follow_holds_last_lap_on_none_region():
    """A None lap_at_time (lead-in / between laps / cool-down) HOLDS the last followed lap —
    never blanks the charts — and the next valid lap is picked up."""
    w, rec = _follow_window(best_lap=9)
    w._follow_current_lap(5, t=50.0)
    assert w._followed_lap == 5
    n = len(rec.lap_sets)
    # None region: hold — no set_laps, followed unchanged.
    w._follow_current_lap(None, t=400.0)
    assert w._followed_lap == 5 and len(rec.lap_sets) == n, "blanked/changed in a None region"
    # Next valid lap is picked up on the edge out of the None region.
    w._follow_current_lap(7, t=500.0)
    assert w._followed_lap == 7 and rec.lap_sets[-1] == [7, 9]
    print("test_follow_holds_last_lap_on_none_region OK")


def test_follow_current_is_best_shows_single_lap():
    """When the current lap IS the best lap, the charts show just [best] (no duplicate overlay),
    and a drag in progress re-places the cursor in the followed lap (resolves the off-lap caveat)."""
    w, rec = _follow_window(best_lap=9)
    rec._dragging = True
    w._follow_current_lap(9, t=1100.0)
    assert w._followed_lap == 9 and rec.lap_sets[-1] == [9], rec.lap_sets[-1]
    assert rec.placed == [1100.0], "cursor not re-placed mid-drag after the follow switch"
    print("test_follow_current_is_best_shows_single_lap OK")


if __name__ == "__main__":
    test_numeric_sort_key_orders_by_value_not_text()
    test_numeric_sort_key_blanks_sort_last()
    test_best_split_per_sector_is_column_min()
    test_nearest_index_in_lap_stays_in_lap()
    test_nearest_time_in_lap_clamps_to_window()
    test_follow_switches_only_on_lap_edge()
    test_follow_holds_last_lap_on_none_region()
    test_follow_current_is_best_shows_single_lap()
    print("\nALL STUDIO FEATURE TESTS PASSED")
