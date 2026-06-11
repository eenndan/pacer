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

from _synthetic import bare_session  # noqa: E402

from studio import gapfill  # noqa: E402
from studio.lap_table import (  # noqa: E402
    NUM_ROLE,
    _best_split_per_sector_impl,
    _NumItem,
)


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
    directions — never above a real value. Qt reverses the `<` result for a descending column, so
    _NumItem flips the blank ordering to the active direction (_NumItem._descending) to keep blanks
    LAST either way; LapTable sets that flag before each sort."""
    _NumItem._descending = False  # ascending
    try:
        real = _item(12.3, "12.30")
        blank = _item(float("nan"), "")
        assert real < blank          # real before blank ascending
        assert not (blank < real)    # blank never sorts before a real value
        # Two blanks compare equal-ish (neither strictly less).
        assert not (_item(float("nan")) < _item(float("nan")))

        # DESCENDING: Qt reverses the comparator result, so for blanks to STILL land last the
        # comparator must report blank as the SMALLEST (blank < real True, real < blank False).
        _NumItem._descending = True
        assert blank < real, "descending: blank must compare as smallest so Qt's reversal puts it last"
        assert not (real < blank)
        assert not (_item(float("nan")) < _item(float("nan")))
    finally:
        _NumItem._descending = False
    print("test_numeric_sort_key_blanks_sort_last OK")


def test_lap_table_blanks_sort_last_both_directions_end_to_end():
    """End-to-end through a real QTableWidget + LapTable's sort path: a column with some blank
    (partial-lap) cells keeps the blanks at the BOTTOM whether the column is sorted ascending or
    descending. Drives the actual sortByColumn + _on_sorted re-sort, not just the comparator."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTableWidget

    from studio.lap_table import _NumItem

    tbl = QTableWidget(4, 1)
    tbl.setSortingEnabled(True)
    keys = [30.0, 10.0, float("nan"), 20.0]   # one blank among three real values
    for r, k in enumerate(keys):
        it = _NumItem("" if (isinstance(k, float) and math.isnan(k)) else f"{k:.1f}")
        it.setData(NUM_ROLE, k)
        tbl.setItem(r, 0, it)

    def keys_in_order():
        out = []
        for r in range(tbl.rowCount()):
            v = tbl.item(r, 0).data(NUM_ROLE)
            out.append("blank" if (v is None or (isinstance(v, float) and math.isnan(v)))
                       else round(float(v), 1))
        return out

    # Ascending: 10, 20, 30, blank-last.
    _NumItem._descending = False
    tbl.sortByColumn(0, Qt.AscendingOrder)
    asc = keys_in_order()
    assert asc == [10.0, 20.0, 30.0, "blank"], asc

    # Descending: 30, 20, 10, blank STILL last (not floated to the top).
    _NumItem._descending = True
    tbl.sortByColumn(0, Qt.DescendingOrder)
    desc = keys_in_order()
    assert desc == [30.0, 20.0, 10.0, "blank"], desc
    _NumItem._descending = False
    print("test_lap_table_blanks_sort_last_both_directions_end_to_end OK")


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
    """A bare Session (tests/_synthetic factory) carrying ONE lap's cached xy + (times,dists),
    patched so the F3 helpers run with no pacer. The lap is a simple curve; a far-away point must
    still resolve to the NEAREST point WITHIN this lap (never escape it) and the time clamps to
    the lap window."""
    n = 50
    t0 = 100.0
    xs = np.linspace(0.0, 100.0, n)
    ys = np.sin(np.linspace(0, math.pi, n)) * 20.0
    ts = t0 + np.arange(n) * 0.1
    dists = np.linspace(0.0, 250.0, n)
    s = bare_session({lap_id: (ts, dists)})
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


# ----------------------------------------------------- dropout-lap low-confidence flag
def _bare_session_with_times(times_by_lap, valid):
    """A bare Session whose per-lap kept-point times are stubbed (the valid-lap set is seeded
    through the factory's memo), so the read-only dropout helpers run with no pacer /
    telemetry file."""
    s = bare_session(valid=valid)
    s._lap_point_times = lambda lid: times_by_lap[lid]  # noqa: ARG005
    return s


def test_dropout_detection_interior_gap_over_threshold():
    """A lap is a dropout iff its KEPT-point times have an interior delta > 0.35 s (the gap
    threshold). A steady ~10 Hz trace is clean; a >0.35 s hole flags it. The lap's open
    start/finish ends are NOT interior gaps."""
    clean = [round(0.1 * i, 3) for i in range(50)]         # steady 0.1 s steps — no dropout
    # An interior 0.6 s hole after the 20th sample (last clean t=1.9; next jumps to 2.5) →
    # one dropout gap of 0.6 s.
    dropped = clean[:20] + [round(2.5 + 0.1 * i, 3) for i in range(20)]
    # A delta just UNDER the threshold (0.3 s): last clean t=0.9; next is 1.2 (delta 0.3 s) →
    # jitter, not a dropout.
    jitter = clean[:10] + [round(1.2 + 0.1 * i, 3) for i in range(10)]
    assert gapfill.find_gaps(clean) == []
    assert len(gapfill.find_gaps(dropped)) == 1
    assert gapfill.find_gaps(jitter) == []

    s = _bare_session_with_times({0: clean, 1: dropped, 2: jitter, 3: clean},
                                 valid=[0, 1, 2])  # lap 3 is INVALID — excluded from the flag
    assert s.lap_has_dropout(1) is True
    assert s.lap_has_dropout(0) is False and s.lap_has_dropout(2) is False
    # Only the VALID dropout lap (1) is flagged; the invalid lap 3 is never considered.
    assert s.dropout_lap_ids() == {1}
    print("test_dropout_detection_interior_gap_over_threshold OK")


# ------------------------------------------------- lap_at_time boundary (bug: lap-click jump)
class _FakeLaps:
    """Minimal stand-in for the pacer laps object exposing the two methods lap_at_time needs.
    `starts` are the per-lap start timestamps; laps are CONTIGUOUS (each lap ends exactly where
    the next begins) — the arrangement that exposed the inclusive-boundary jump."""

    def __init__(self, starts, end):
        self._starts = starts
        self._end = end  # finish timestamp of the last lap

    def laps_count(self):
        # lap_at_time now builds its window table through Session.lap_window, which bounds-checks
        # the id against laps_count() (the [start, start+lap_time) window definition is single-
        # sourced there). The fake exposes it so the half-open boundary test still drives the
        # real code path; the assertions below are unchanged.
        return len(self._starts)

    def start_timestamp(self, lid):
        return self._starts[lid]

    def lap_time(self, lid):
        nxt = self._starts[lid + 1] if lid + 1 < len(self._starts) else self._end
        return nxt - self._starts[lid]


def _bare_session_for_lap_at_time(starts, end, valid):
    s = bare_session(valid=valid)
    s.laps = _FakeLaps(starts, end)
    return s


def test_lap_at_time_boundary_resolves_to_starting_lap():
    """A `t` exactly on a (contiguous) lap boundary must resolve to the lap that STARTS there,
    not the one that ends there. This is the time `start_timestamp(lap)` produced when a lap is
    selected, so an inclusive upper bound jumped the highlight/charts back one lap. The window is
    half-open `[start, end)` so each lap's exact start belongs to THAT lap."""
    starts = [10.0, 80.0, 150.0, 220.0]  # 4 contiguous laps, each 70 s
    s = _bare_session_for_lap_at_time(starts, end=290.0, valid=[0, 1, 2, 3])
    # Every lap's exact start resolves to itself — no off-by-one jump.
    for lid, st in enumerate(starts):
        assert s.lap_at_time(st) == lid, (lid, st, s.lap_at_time(st))
    # Interior points still resolve normally.
    assert s.lap_at_time(45.0) == 0
    assert s.lap_at_time(115.0) == 1
    # Just below a boundary stays in the previous lap (the half-open lower edge of the next lap).
    assert s.lap_at_time(80.0 - 1e-6) == 0
    # The exact finish of the LAST lap falls outside (a between-laps None that follow HOLDS).
    assert s.lap_at_time(290.0) is None
    # Before the first lap / after the session → None.
    assert s.lap_at_time(0.0) is None and s.lap_at_time(1000.0) is None
    print("test_lap_at_time_boundary_resolves_to_starting_lap OK")


def test_lap_at_time_skips_invalid_lap_gap():
    """When an invalid lap sits between two valid laps, the invalid lap's time span resolves to
    None (not silently to a neighbour) — the half-open fix must not bridge that gap."""
    starts = [10.0, 80.0, 150.0, 220.0]
    # Lap 1 is INVALID (excluded) → its [80,150) span must be None, not lap 0 or lap 2.
    s = _bare_session_for_lap_at_time(starts, end=290.0, valid=[0, 2, 3])
    assert s.lap_at_time(100.0) is None       # inside the invalid lap → None
    assert s.lap_at_time(80.0) is None        # invalid lap's start → None
    assert s.lap_at_time(150.0) == 2          # valid lap 2's start resolves to 2
    assert s.lap_at_time(45.0) == 0
    print("test_lap_at_time_skips_invalid_lap_gap OK")


# ----------------------------------------------------------- auto-follow (lap-change edge)
class _Recorder:
    """A tiny stand-in for the table / plots / video collaborators, recording the calls the
    follow logic makes so we can assert the EDGE semantics without pacer / a telemetry file."""

    def __init__(self):
        self.selected = []      # every table.select(ids)
        self.lap_sets = []      # every plots.set_laps(ids)
        self.seeks = []         # any video.seek(...) — must stay EMPTY (no playback fight)
        self.placed = []        # plots.set_playhead_time(t, force=True) while dragging
        self._dragging = False

    # table
    def select(self, ids):
        self.selected.append(list(ids))

    # plots
    def set_laps(self, ids):
        self.lap_sets.append(list(ids))

    def is_dragging(self):
        return self._dragging

    def set_playhead_time(self, t, *, force=False):
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


def test_select_lap_seeks_into_lap_despite_ms_quantization():
    """Selecting a lap must seek a hair INTO it so the player's whole-ms seek quantization can't
    land just before the (contiguous) boundary and resolve to the previous lap. Asserts the
    seeded `_followed_lap` and the lap resolved from the QUANTIZED seek position both equal the
    clicked lap — the fix for 'clicking a lap selects a different lap'."""
    from studio.app import StudioWindow

    starts = [10.0, 80.000005, 150.000166, 220.000714]  # contiguous; non-ms-aligned boundaries
    sess = _bare_session_for_lap_at_time(starts, end=290.0, valid=[0, 1, 2, 3])
    sess.best_lap_id = lambda: 0

    w = StudioWindow.__new__(StudioWindow)
    w.session = sess
    rec = _Recorder()
    w.table = rec
    w.plots = rec

    class _Vid:
        def __init__(self):
            self.pos = None

        def seek(self, t):
            # mimic QMediaPlayer.setPosition(int(seconds*1000)) — truncate to whole ms
            self.pos = int(t * 1000) / 1000.0

    vid = _Vid()
    w.video = vid

    for lid in (1, 2, 3):
        w._followed_lap = None
        w._on_laps_selected([lid], seek=True)
        # The seeded follow lap is the clicked lap (not the previous one).
        assert w._followed_lap == lid, (lid, w._followed_lap)
        # And the lap resolved from the ACTUAL (ms-quantized) seek position is also the clicked
        # lap — i.e. when the post-seek tick runs lap_at_time(position) it won't jump back.
        assert sess.lap_at_time(vid.pos) == lid, (lid, vid.pos, sess.lap_at_time(vid.pos))
    print("test_select_lap_seeks_into_lap_despite_ms_quantization OK")


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
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nALL {len(tests)} STUDIO FEATURE TESTS PASSED")
