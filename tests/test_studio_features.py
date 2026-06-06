"""Pure-Python tests for the studio UI features F1–F5 (no pacer, no telemetry file, fast).

These exercise the load-bearing pure logic directly on synthetic data:
  * F1 — the numeric sort key of the lap-table cell (`_NumItem.__lt__`): times/splits sort by
    their underlying float, blanks/NaN sort last.
  * F3 — `Session.nearest_index_in_lap` / `nearest_time_in_lap`: the marker drag is constrained
    to ONE lap's points and clamped to its time window (built on a bare Session, no pacer).
  * F5 — per-sector session-best = the per-column MINIMUM split across valid laps.
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


if __name__ == "__main__":
    test_numeric_sort_key_orders_by_value_not_text()
    test_numeric_sort_key_blanks_sort_last()
    test_best_split_per_sector_is_column_min()
    test_nearest_index_in_lap_stays_in_lap()
    test_nearest_time_in_lap_clamps_to_window()
    print("\nALL STUDIO FEATURE TESTS PASSED")
