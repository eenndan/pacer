"""Corners ▸ Quickest passes: the driver's own SECTORS, each one click from the video.

The request (2026-11-18): "very easy to navigate and jump to footage to see best sectors … show
fastest time per corner and on which lap it happened … quickly jump to that moment in footage".
0.16-0.17 built it for the detected corners; his word was "sectors". When he has drawn sector lines
(map ▸ Add sector) the view lists S1…Sn below the corners and a sector row plays that lap's sector
through the same pass path a corner row takes. These tests pin:

  * the level step for a split (`stats.SECTOR_LEVEL_S`): √2 × the published split σ, read here out
    of `docs/ACCURACY.md` and the truth matrix row it quotes, so the rule cannot drift from the
    figure; and `stats.quickest_sectors` (Qt-free): ties under it, laps not counted, and that each
    sector's quickest IS the SPLITS grid's best (one source);
  * the view on the real window over the demo (PACER_NO_MEDIA) with 0, 2 and 4 lines: no line
    leaves the table exactly as it was; the rows, the legend, the clip and zoom windows, the
    subject, the fullscreen readout's pass line, the walk staying among the sectors, the menu
    (export and compare greyed out, "not for sectors yet"), ⌘K, and a line edit refreshing the rows
    and dropping a studied sector that no longer exists.

Its own file rather than test_quickest_passes.py (2,277 lines): the sector windows are built with
lines added, which every one of these needs and none of those does.
"""
import ast
import math
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import prefs, stats, theme  # noqa: E402

theme.apply_theme(_APP)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ------------------------------------------------------------------ the rule and the data


def test_the_sector_step_is_root_two_times_the_published_split_sigma():
    """SPLIT_SIGMA_S is the interior split σ ACCURACY.md publishes at recording C's noise, which is
    truth matrix row 2 at noise 2; the step is the noise of a difference of two such splits."""
    text = " ".join(open(os.path.join(_ROOT, "docs", "ACCURACY.md"), encoding="utf-8").read().split())
    got = re.search(r"up to ([0-9.]+) s at recording C's GPS noise", text)
    assert got and float(got.group(1)) == stats.SPLIT_SIGMA_S, got
    measured = None
    tree = ast.parse(open(os.path.join(_ROOT, "tests", "test_truth_matrix.py"),
                          encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Row":
            try:
                stat, level, value = (ast.literal_eval(a) for a in node.args[:3])
            except ValueError:
                continue
            if stat == "sector.interior_sd" and level == 2.0:
                measured = value
    assert measured is not None and round(measured, 2) == stats.SPLIT_SIGMA_S, measured
    assert stats.SECTOR_LEVEL_S == round(math.sqrt(2) * stats.SPLIT_SIGMA_S, 2) == 0.16
    # wider than a lap's step, and an estimated clock never narrows it
    assert stats.sector_level_floor(False) == stats.sector_level_floor(True) == 0.16


def test_a_stretch_key_is_a_corner_cid_or_a_negative_sector_key():
    assert [stats.sector_key(c) for c in range(3)] == [-1, -2, -3]
    assert [stats.sector_col(stats.sector_key(c)) for c in range(3)] == [0, 1, 2]
    assert stats.sector_col(1) is None and stats.sector_col(7) is None


def _splits(rows, ids=None):
    ids = list(range(len(rows))) if ids is None else ids
    return stats.split_matrix(ids, rows, columns=len(rows[0]))


def test_quickest_sectors_names_ties_under_the_split_step_and_leaves_out_blank_laps():
    rows = [[10.00, 20.0], [10.10, 20.5], [10.159, 20.6], [10.160, 20.7], [10.5, 20.8],
            [9.9, float("nan")]]
    qs = stats.quickest_sectors(_splits(rows), lap_ids=range(7))
    s1, s2 = qs.passes
    assert qs.level_s == 0.16 and [p.cid for p in qs.passes] == [-1, -2]
    # lap 5 has a blank split (a nan): not counted anywhere, never named, though its 9.9 is lowest
    assert (s1.time_s, s1.lap_id, s1.n_counted) == (10.00, 0, 5)
    # +0.100 and +0.159 are level; +0.160 (exactly the step) is not
    assert s1.level_laps == (1, 2) and not s1.alone and s1.next_gap_s == 0.1
    assert s2.lap_id == 0 and s2.level_laps == () and s2.alone and s2.next_gap_s == 0.5
    assert qs.not_counted == (5, 6), qs.not_counted
    # under the corners' 0.07 s the same S1 would read clear of everything but nothing: the step
    # IS what decides it
    assert stats.quickest_passes(stats.corner_matrix(
        range(5), [1], [[r[0]] for r in rows[:5]], [[True]] * 5)).passes[0].level_laps == ()
    assert stats.quickest_sectors(None) is None


# ------------------------------------------------------------------ the demo, with lines

_DEMO: dict = {}
CONFIGS = {2: (2, 5), 3: (1, 3, 5), 4: (1, 2, 4, 5)}   # Circuit.straights, mid-straight


def _demo_path() -> str:
    if "path" not in _DEMO:
        from studio.dev import make_demo as md
        _DEMO["dir"] = tempfile.TemporaryDirectory(prefix="quickest_sectors_")
        _DEMO["path"] = os.path.join(_DEMO["dir"].name, "GX019000.MP4")
        _DEMO["truth"] = md.make(_DEMO["path"], render=False)
    return _DEMO["path"]


def _lines(n):
    truth = _DEMO["truth"]
    return [truth.line_at(sum(truth.circuit.straights[j]) / 2.0) for j in CONFIGS[n]]


def _with_lines(s, n):
    """Put `n` sector lines on session `s` as a reload of a saved placement does."""
    start, _ = s.timing_lines_latlon()
    assert s.apply_timing_lines_latlon(start, _lines(n) if n else [], confirmed=True)


def test_each_sectors_quickest_is_the_splits_grids_best_and_plays_its_own_split():
    from studio.session import Session
    s = Session.load([_demo_path()])
    assert s.quickest_sectors() is None, "no sector line, no sector rows"
    for n in CONFIGS:
        _with_lines(s, n)
        m, qs = s.split_matrix(), s.quickest_sectors()
        assert len(qs.passes) == m.columns == n + 1
        for c, qp in enumerate(qs.passes):
            assert qp.time_s == m.bests[c] and qp.lap_id == m.best_lap[c], (n, c, qp)
            row = m.lap_ids.index(qp.lap_id)
            assert m.is_best(row, c)     # the SPLITS cell with the ★ is the lap the row names
            assert s.stretch_label(qp.cid) == f"S{c + 1}" and s.has_stretch(qp.cid)
            # the clip runs from 2 s before the sector's first line to 1 s after its last, the
            # instants the split itself is timed between
            t0 = float(s._lap_columns(qp.lap_id)[0][0])
            splits = s.lap_sector_splits(qp.lap_id)
            a, b = s.stretch_clip(qp.lap_id, qp.cid)
            assert abs(a - (t0 + sum(splits[:c]) - 2.0)) < 1e-9
            assert abs((b - a) - (splits[c] + 3.0)) < 1e-9
            assert s.stretch_time(qp.lap_id, qp.cid) == qp.time_s
        assert not s.has_stretch(stats.sector_key(n + 1))
        # the stats page's grid reads the same matrix
        from studio.stats_panel import StatsView
        assert StatsView._split_matrix(s) == m


def _settle(seconds=0.1):
    end = time.time() + seconds
    while time.time() < end:
        _APP.processEvents()
        time.sleep(0.005)


def _window(n_lines, size=(1440, 900)):
    from studio.app import StudioWindow
    for k in ("critical", "warning", "information"):
        setattr(QMessageBox, k, staticmethod(lambda *a, **_kw: QMessageBox.Ok))
    win = StudioWindow([_demo_path()])
    deadline = time.time() + 60
    while win.view is None and time.time() < deadline:
        _settle(0.02)
    assert win.view is not None, "the demo did not load"
    if n_lines:
        _with_lines(win.session, n_lines)
        win.view.rebuild_derived_views()
    win.resize(*size)
    win.show()
    _settle(0.2)
    QTest.keyClick(win, Qt.Key_Escape)   # leave the debrief landing
    _settle(0.1)
    win.view.select_lap_tab(1)
    _settle()
    return win


def _close(win):
    win.close()
    win.deleteLater()
    _APP.processEvents()


def _record_plays(view):
    plays = []

    def record(start, end):
        plays.append((start, end))
        view.video.pane.set_clip(start, end)
    view.video.play_clip = record
    return plays


def _model(table):
    """Every cell's every role a test can read, and the headers' tooltips."""
    roles = (Qt.DisplayRole, Qt.ToolTipRole, Qt.ForegroundRole, Qt.FontRole, Qt.AccessibleTextRole,
             Qt.TextAlignmentRole)
    m = table.model()

    def say(v):   # by value: a QBrush / QFont repr carries its address
        if hasattr(v, "color"):
            return v.color().name()
        return v.toString() if hasattr(v, "toString") else str(v)
    out = [[str(m.headerData(c, Qt.Horizontal, r)) for r in (Qt.DisplayRole, Qt.ToolTipRole)]
           for c in range(m.columnCount())]
    for r in range(m.rowCount()):
        for c in range(m.columnCount()):
            out.append([say(m.index(r, c).data(role)) for role in roles])
    return out


def test_no_sector_line_leaves_the_view_exactly_as_it_was():
    prefs.set_corners_view("quickest")
    win = _window(0)
    try:
        page = win.view.corners_page
        q = page.quickest
        assert q.sectors is None and q.table.rowCount() == 7
        before = _model(q.table), page.level_label.text(), page.level_label.toolTip()
        win.session.quickest_sectors = lambda: None    # the view as it was without sector rows
        q.refresh()
        page._apply()
        after = _model(q.table), page.level_label.text(), page.level_label.toolTip()
        assert after[1:] == before[1:]
        diff = [(a, b) for a, b in zip(after[0], before[0], strict=True) if a != b]
        assert not diff, diff
        assert page.level_label.text() == "Level = within 0.07 s"
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def _rows(table):
    return [[table.item(r, c).text() for c in range(4)] for r in range(table.rowCount())]


def test_two_and_four_lines_list_their_sectors_below_the_corners():
    prefs.set_corners_view("quickest")
    for n in (2, 4):
        win = _window(n)
        try:
            page = win.view.corners_page
            q, m = page.quickest, win.session.split_matrix()
            rows = _rows(q.table)
            assert len(rows) == 7 + n + 1 and [r[0] for r in rows[:7]] == [f"C{i}" for i in
                                                                          range(1, 8)]
            for c, row in enumerate(rows[7:]):
                assert row[0] == f"S{c + 1}"
                assert row[1] == f"{m.bests[c]:.2f}" and row[2] == str(m.best_lap[c] + 1), row
                assert "★" not in row[1]   # no sector on the demo stands 0.16 s clear
            assert page.level_label.text() == "Level = within 0.07 s · sectors 0.16 s"
            assert "0.11 s" in page.level_label.toolTip() and "tie" in page.level_label.toolTip()
            tip = q.table.item(8, 3).toolTip()
            assert "within 0.16 s" in tip and "timing noise" in tip, tip
            # every row's ▶ is reachable: no scrollbar at 1440×900 docked
            assert not q.table.verticalScrollBar().isVisible()
        finally:
            _close(win)
    prefs.set_corners_view("lap")


def test_a_sector_row_plays_its_split_framed_with_its_subject_and_readout():
    prefs.set_corners_view("quickest")
    win = _window(3)
    try:
        view, s = win.view, win.session
        plays = _record_plays(view)
        q = view.corners_page.quickest
        row = 8                                    # S2
        q.activate(row)
        _settle()
        key, lap = q.playing()
        assert key == stats.sector_key(1) and lap == s.split_matrix().best_lap[1]
        clip = s.stretch_clip(lap, key)
        assert plays[-1] == clip
        # the charts hold the sector's window with a tenth of it either side; the map is zoomed
        x0, x1 = s.stretch_plot_window(key, view.plots.axis_mode())
        lo, hi = view.plots.p_speed.getViewBox().viewRange()[0]
        pad = 0.1 * (x1 - x0)
        assert abs(lo - (x0 - pad)) < 1e-6 and abs(hi - (x1 + pad)) < 1e-6, (lo, hi, x0, x1)
        assert view.pass_zoomed() and view.map.pass_zoomed()
        assert view.map.pinned_corner() is None    # no apex to ring
        assert view.pass_subject() == (lap, key, "S2")
        msg = win.statusBar().currentMessage()
        assert msg.startswith(f"Playing lap {lap + 1} (") and "through S2" in msg, msg
        assert "⌘K: export" not in msg              # a sector pass is not exportable yet
        t = s.stretch_time(lap, key)
        assert view._live_pass_line() == f"S2 · lap {lap + 1} ({t:.2f})"
        # the export picker is not offered this pass
        assert win.exports.pass_subject() is None
        # Esc: back to the whole lap, and the pass is left
        QTest.keyClick(win, Qt.Key_Escape)
        _settle()
        assert not view.pass_zoomed() and view.pass_subject() is None
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_the_walk_stays_among_the_sectors_and_says_where_it_ends():
    prefs.set_corners_view("quickest")
    win = _window(3)
    try:
        view, s = win.view, win.session
        _record_plays(view)
        q = view.corners_page.quickest
        q.activate(8)                              # S2, from Quickest passes
        _settle()
        assert view.step_corner_pass(+1)
        assert q.playing()[0] == stats.sector_key(2)                    # S3's quickest
        assert view.step_corner_pass(+1) and q.playing()[0] == stats.sector_key(3)
        notices = []
        view.statusNotice.connect(notices.append)
        assert not view.step_corner_pass(+1)
        assert notices[-1].startswith("No later sector to play"), notices
        assert view.step_corner_pass(-1) and q.playing()[0] == stats.sector_key(2)
        # from a corner pass the walk is the corners', as before: C7 → nothing later, not S1
        q.activate(6)
        _settle()
        assert not view.step_corner_pass(+1)
        assert notices[-1].startswith("No later corner to play"), notices
        # a lap's own sector walk (a per-lap play): the same lap, the next sector
        lap = s.split_matrix().lap_ids[0]
        assert view.play_lap_pass(lap, stats.sector_key(0))
        assert view.step_corner_pass(+1)
        assert view.pass_subject()[:2] == (lap, stats.sector_key(1))
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_the_menu_plays_a_sector_and_greys_out_export_and_compare_and_cmd_k_lists_it():
    from studio import command_palette
    from studio.central_view import SECTOR_COMPARE_NOT_YET, SECTOR_EXPORT_NOT_YET
    prefs.set_corners_view("quickest")
    win = _window(3)
    try:
        view, s = win.view, win.session
        plays = _record_plays(view)
        q = view.corners_page.quickest
        pos = q.table.visualRect(q.table.model().index(9, 1)).center()   # S3
        menu = view.pass_menu(view._quickest_menu_pass(q.table, pos))
        play, export, compare = menu.actions()
        assert play.isEnabled() and not export.isEnabled() and not compare.isEnabled()
        assert export.toolTip() == SECTOR_EXPORT_NOT_YET
        assert compare.toolTip() == SECTOR_COMPARE_NOT_YET
        play.trigger()
        lap = s.split_matrix().best_lap[2]
        assert plays[-1] == s.stretch_clip(lap, stats.sector_key(2))
        titles = [e.title for e in command_palette.entries(win)]
        quick = [t for t in titles if t.startswith("Quickest ")]
        assert quick[7:] == [f"Quickest S{c + 1} — lap {s.split_matrix().best_lap[c] + 1}"
                             for c in range(4)], quick
        assert not any(t.startswith("Export the S") or " through S" in t for t in titles), titles
        # a lap the SPLITS grid does not count (a blank row) is refused, with the reason
        m = s.split_matrix()
        lap = m.lap_ids[3]
        blank = [[float("nan")] * m.columns if i == lap else s.lap_sector_splits(i)
                 for i in m.lap_ids]
        s.split_matrix = lambda: stats.split_matrix(m.lap_ids, blank, columns=m.columns)
        notices = []
        view.statusNotice.connect(notices.append)
        assert not view.play_lap_pass(lap, stats.sector_key(1))
        assert f"Lap {lap + 1} has no counted S2 split" in notices[-1], notices
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_a_line_edit_refreshes_the_rows_and_drops_a_studied_sector_that_is_gone():
    prefs.set_corners_view("quickest")
    win = _window(3)
    try:
        view, s = win.view, win.session
        _record_plays(view)
        q = view.corners_page.quickest
        q.activate(10)                             # S4
        _settle()
        assert view.pass_subject()[1] == stats.sector_key(3)
        view._save_sidecar = lambda: None          # the edit's sidecar write: not in a test
        # the map's own signal, as a drag or Reset sectors sends it: one line fewer
        view.map.timing_lines_changed.emit(s.start_line, list(s.sector_lines)[:2])
        _settle()
        assert [r[0] for r in _rows(q.table)][7:] == ["S1", "S2", "S3"]
        assert view.pass_subject() is None and q.playing() is None
        # ...and with no line left, the view is the corners' alone again
        view.map.timing_lines_changed.emit(s.start_line, [])
        _settle()
        assert q.table.rowCount() == 7 and q.sectors is None
        assert view.corners_page.level_label.text() == "Level = within 0.07 s"
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_provisional_timing_withholds_a_sectors_star():
    from studio.stats import QuickestPass, QuickestPasses
    prefs.set_corners_view("quickest")
    win = _window(2)
    try:
        s, q = win.session, win.view.corners_page.quickest
        real = s.quickest_sectors()
        lone = QuickestPass(-1, 13.0, 4, (), 0.4, 14, (), ((4, 13.0),), 13.2, ())
        s.quickest_sectors = lambda: QuickestPasses([lone, *real.passes[1:]], real.level_s, ())
        q.refresh()
        assert q.table.item(7, 1).text() == "13.00 ★"
        assert "no other lap took this sector within 0.16 s" in q.table.item(7, 1).toolTip()
        cls = type(s)
        saved = cls.timing_verified
        cls.timing_verified = property(lambda _self: False)
        try:
            q.refresh()
            assert q.table.item(7, 1).text() == "13.00"
        finally:
            cls.timing_verified = saved
    finally:
        _close(win)
        prefs.set_corners_view("lap")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"{name} OK")
