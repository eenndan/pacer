"""Corners ▸ Quickest passes: the driver's own SECTORS, each one click from the video.

The request (2026-11-18): "very easy to navigate and jump to footage to see best sectors … show
fastest time per corner and on which lap it happened … quickly jump to that moment in footage".
0.16-0.17 built it for the detected corners; his word was "sectors". When he has drawn sector lines
(map ▸ Add sector) the view lists S1…Sn below the corners and a sector row plays that lap's sector
through the same pass path a corner row takes. These tests pin:

  * the level step for a split (`stats.SECTOR_LEVEL_S`): the house rule on the split σ of the one
    reference row (`stats.REFERENCE_SIGMAS.split`), printed by `docs/ACCURACY.md`, so the rule
    cannot drift from the figure, and what that σ rests on re-measured on four of its days; and
    `stats.quickest_sectors` (Qt-free): ties under it, laps not counted, and that each
    sector's quickest IS the SPLITS grid's best (one source);
  * the view on the real window over the demo (PACER_NO_MEDIA) with 0, 2 and 4 lines: no line
    leaves the table exactly as it was; the rows, the legend, the clip and zoom windows, the
    subject, the fullscreen readout's pass line, the walk staying among the sectors, the menu
    (export opens the overlay picker on the sector's pass through the real action and dialog;
    compare greyed out with its own reason where level), ⌘K, and a line edit refreshing the rows
    and dropping a studied sector that no longer exists;
  * "Compare with my typical pass" on a sector row: the corners' rule over the SPLITS grid
    (`stats.pass_vs_typical_of`, one code path), offered only where the typical split beats the
    quickest by the split's step — on the demo with 2, 3 and 4 lines, the sectors whose typical pass is truly 0.15 s or more slower — both panes at
    the sector, and `compare_controller.PassAnchor`'s readout (blank in the lead-in, live inside,
    held after, dropped outside the clip, neutral inside the split's 0.11 s); Esc leaves it.

Its own file rather than test_quickest_passes.py (2,277 lines): the sector windows are built with
lines added, which every one of these needs and none of those does.
"""
import math
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QScrollBar  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import prefs, stats, theme  # noqa: E402
from studio.lap_table import GROUP_ROW_ROLE, QUICKEST_PLAY_COL, QUICKEST_STRIP_COL  # noqa: E402

theme.apply_theme(_APP)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ------------------------------------------------------------------ the rule and the data


def _flat(*parts):
    return " ".join(open(os.path.join(_ROOT, *parts), encoding="utf-8").read().split())


def test_the_sector_step_is_the_house_rule_on_the_split_sigma_measured_at_the_one_reference():
    """cool/sector-step-one-reference. SPLIT_SIGMA_S is the one reference row's split column
    (`stats.REFERENCE_SIGMAS.split`), measured on the 40-day grid every other step is
    (studio/dev/probes/sector_split_sigma.py): the worst sector of the worst line placement
    measured, since one step serves every sector a driver can draw. The step is the HOUSE rule
    (2√2·σ, the lap step's own formula). ACCURACY.md prints that σ and every recording's step, and
    §26 records the re-measure against its new-evidence clause. Before this the anchor was truth
    matrix row 2 (the max over three seeds of one recording's sd, 0.1066 s → 0.30 s)."""
    from studio import library
    ref = stats.PUBLISHED_NOISE_SIGMAS[library.REFERENCE_RECORDING]
    assert stats.SPLIT_SIGMA_S == ref.split == stats.REFERENCE_SIGMAS.split
    assert stats.DIFF_2SIGMA_K == 2 * math.sqrt(2)
    assert stats.SECTOR_LEVEL_S == stats.house_step(ref.split) == 0.11
    # the lap step is the same formula on the lap σ (library.PB_PRECISION_S's comment: 0.0247 s)
    assert round(stats.DIFF_2SIGMA_K * 0.0247, 2) == stats.PB_PRECISION_S
    acc = _flat("docs", "ACCURACY.md")
    assert f"one split's σ is {ref.split:.3f} s at recording C's GPS noise" in acc, "σ not printed"
    # §26: the gate it measured (0.30 s) stays as history; the re-measure names today's step
    refused = _flat("studio", "docs", "refused-2026-09.md")
    sec26 = refused.split("## 26.", 1)[1].split("## 27.", 1)[0]
    assert "The gate is then 0.30 s" in sec26
    assert f"the split step is now {stats.SECTOR_LEVEL_S:.2f} s" in sec26
    probe = open(os.path.join(_ROOT, "studio", "dev", "probes", "clip_refusals.py"),
                 encoding="utf-8").read()
    assert re.search(r"^GATE_K = DIFF_2SIGMA_K\b", probe, re.M), "§26's gate is not this constant"
    # wider than a lap's step, and an estimated clock never narrows it below the lap step there
    assert stats.sector_level_floor(False) == stats.SECTOR_LEVEL_S > stats.PB_PRECISION_S
    assert stats.sector_level_floor(True) >= stats.rolling_level_floor(True)


def test_the_split_sigma_is_measured_on_de_drifted_splits_and_the_drift_dominated_it():
    """What the split column rests on, re-measured on four of the grid's days at the reference
    noise (seeds 401-404, the probe's own `day_record`): the app's splits are crossed with each
    lap's rigid receiver shift taken off (`Session._sector_drift`, cool/sector-dedrift), and the
    worst of the slow-line placements sits inside the 40-day worst σ's seed spread; timed as they
    were before (`uncorrected_splits`), the hairpin's worst sector carries well over half again
    as much (the drift a split between two different lines keeps). The 40-day pooled figures are
    in the probe's summary and ACCURACY.md."""
    from studio.dev.probes import sector_split_sigma as probe
    work = tempfile.mkdtemp(prefix="split_sigma_test_")
    try:
        days = [probe.day_record(work, stats.REFERENCE_SIGMAS.noise, 401 + k, k + 1)
                for k in range(4)]
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    slow = max((probe.pair_sigma(days, n) for n in ("hairpin", "maxg", "three_slow", "short_c5")),
               key=lambda p: p["worst"])
    hairpin, before = probe.pair_sigma(days, "hairpin"), probe.pair_sigma(days, "hairpin", app=3)
    assert stats.REFERENCE_SIGMAS.corner_pass < slow["worst"], slow
    # four days sit inside the 40-day worst-placement σ's own seed spread (±25 %)
    assert 0.75 * stats.SPLIT_SIGMA_S <= slow["worst"] <= 1.25 * stats.SPLIT_SIGMA_S, slow
    assert before["worst"] > 1.6 * hairpin["worst"], (before, hairpin)
    print(f"split σ, 4 days: slowest placements' worst {slow['worst']:.4f}, hairpin worst "
          f"{hairpin['worst']:.4f}, as timed before the de-drift {before['worst']:.4f}")


def test_a_lap_s_rigid_receiver_shift_does_not_move_its_splits_and_its_time_never_moves():
    """cool/sector-dedrift on one synthetic recording at the reference noise, golden placement:
    PLANT a rigid 1.5 m shift on one counted lap's trace (`Session._lap_columns`, what a receiver
    offset does) and its splits stay within a few ms, because `_sector_drift` takes that lap's
    shift onto the consensus line off before the lines are crossed; held at (0, 0) (as the splits
    were timed before) the same plant moves the interior split by tens of ms. Every counted lap's
    splits sum to its lap time, which a translation cannot move (the core times the lap)."""
    import shutil

    from studio import chapters
    from studio.dev import make_demo
    from studio.dev import synth_gopro as sg
    from studio.dev.probes import sector_split_sigma as probe
    from studio.dev.probes.focus_cross_session import _generate
    from studio.session import Session
    work = tempfile.mkdtemp(prefix="split_dedrift_test_")
    try:
        s = Session.load(chapters.discover_siblings(
            _generate(work, stats.REFERENCE_SIGMAS.noise, 401, 1, None)))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    truth = sg.simulate(401, 14, origin=make_demo.DEMO_ORIGIN)
    start, _ = s.timing_lines_latlon()
    # the lines at C1's and C5's apexes, the slowest crossings (the most time per metre)
    apex = {k.cid: k.apex_s for k in truth.circuit.corners}
    assert s.apply_timing_lines_latlon(start, [truth.line_at(apex[1]), truth.line_at(apex[5])],
                                       confirmed=True)
    ids = s.consistency_lap_ids()
    for i in ids:
        assert abs(sum(s.lap_sector_splits(i)) - s.lap_time(i)) < 1e-9, i
    lap = ids[len(ids) // 2]
    times = {i: s.lap_time(i) for i in ids}
    kept, kept_before = s.lap_sector_splits(lap), probe.uncorrected_splits(s, lap)
    orig = s._lap_columns

    def planted(lid):
        t, xs, ys, v, cum = orig(lid)
        return (t, xs + 1.2, ys - 0.9, v, cum) if lid == lap else (t, xs, ys, v, cum)

    # the corner model holds its own handle on the lap columns (Session DI), so both see the plant
    s._lap_columns = s.corners._lap_columns = planted
    s.corners.invalidate_stats()
    s._sector_collapse_cache = None
    try:
        moved = max(abs(a - b) for a, b in zip(s.lap_sector_splits(lap), kept, strict=True))
        moved_before = max(abs(a - b) for a, b in
                           zip(probe.uncorrected_splits(s, lap), kept_before, strict=True))
        assert {i: s.lap_time(i) for i in ids} == times
    finally:
        del s._lap_columns
        s.corners._lap_columns = orig
        s.corners.invalidate_stats()
    assert moved < 0.005 and moved_before > 0.04, (moved, moved_before)
    print(f"a planted 1.5 m receiver shift moves the lap's splits by {moved * 1000:.1f} ms "
          f"(as timed before: {moved_before * 1000:.1f} ms)")


def test_a_stretch_key_is_a_corner_cid_or_a_negative_sector_key():
    assert [stats.sector_key(c) for c in range(3)] == [-1, -2, -3]
    assert [stats.sector_col(stats.sector_key(c)) for c in range(3)] == [0, 1, 2]
    assert stats.sector_col(1) is None and stats.sector_col(7) is None


def _level_reason(lap, label, v):
    """The greyed compare's reason for a quickest pass level with its typical one: the step alone,
    or the step plus the quickest's luck where only the luck makes it level (central_view.typical_luck_reason)."""
    from studio.central_view import typical_luck_reason
    if v.gap_s >= v.level_s:
        return typical_luck_reason(str(lap + 1), label, v)
    return f"Lap {lap + 1}'s pass through {label} is within {v.level_s:.2f} s of your typical pass as timed"


def _splits(rows, ids=None):
    ids = list(range(len(rows))) if ids is None else ids
    return stats.split_matrix(ids, rows, columns=len(rows[0]))


def test_quickest_sectors_names_ties_under_the_split_step_and_leaves_out_blank_laps():
    rows = [[10.00, 20.0], [10.08, 20.5], [10.109, 20.6], [10.110, 20.7], [10.5, 20.8],
            [9.9, float("nan")]]
    qs = stats.quickest_sectors(_splits(rows), lap_ids=range(7))
    s1, s2 = qs.passes
    assert qs.level_s == stats.SECTOR_LEVEL_S == 0.11 and [p.cid for p in qs.passes] == [-1, -2]
    # lap 5 has a blank split (a nan): not counted anywhere, never named, though its 9.9 is lowest
    assert (s1.time_s, s1.lap_id, s1.n_counted) == (10.00, 0, 5)
    # +0.080 and +0.109 are level; +0.110 (exactly the step) is not
    assert s1.level_laps == (1, 2) and not s1.alone and s1.next_gap_s == 0.08
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
# the sector rows that offer "Compare with my typical pass" on the demo (cool/sector-dedrift)
DEMO_OFFERS = {2: ["S1", "S2"], 3: ["S1", "S2", "S3"], 4: ["S1", "S3"]}


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


# A deferred layout pass is an owned single-shot QTimer of a few ms (the grid restores, the legend
# placement, the debrief hero's settle, a table's re-stretch: 0-120 ms); status lines and toasts
# run for seconds and are not layout. The app never uses the static QTimer.singleShot (§3.9), so
# every deferred pass is a child of the window.
_LAYOUT_TIMER_MS = 500


def _settle_until(win, done, what, timeout=60.0):
    """Process events until `done()` holds AND no deferred layout pass is still queued — the
    condition `_window`'s old fixed 0.2 s + 0.1 s + 0.1 s sleeps stood for, which a fast machine
    overpaid and a slow runner could outrun. Fails loudly, naming what it waited for."""
    deadline = time.monotonic() + timeout
    while True:
        _APP.processEvents()
        if done() and not any(t.isActive() and t.isSingleShot() and t.interval() < _LAYOUT_TIMER_MS
                              for t in win.findChildren(QTimer)):
            _APP.processEvents()   # what the last pass posted
            return
        assert time.monotonic() < deadline, f"waited {timeout:.0f} s for {what}"
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
    # The view's first show maximizes the debrief landing (CentralView.showEvent) — only then does
    # Esc have a landing to leave — and its deferred layout passes are queued behind it.
    _settle_until(win, lambda: not getattr(win.view, "_debrief_pending", False),
                  "the view's first show (the debrief landing maximized)")
    QTest.keyClick(win, Qt.Key_Escape)   # leave the debrief landing
    _settle_until(win, lambda: not win.view.is_debrief() and win.view._maximized_panel is None,
                  "Esc to leave the debrief landing for the grid")
    win.view.select_lap_tab(1)
    _settle_until(win, lambda: win.view.tab_bar.currentIndex() == 1, "the Corners tab")
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
                assert "★" not in row[1]   # no sector on the demo stands 0.11 s clear
            assert page.level_label.text() == "Level = within 0.07 s · sectors 0.11 s"
            tip = page.level_label.toolTip()
            # the split's σ (mid-straight, and the worst placement, with the measured limit), the
            # step once, and what a noisier recording would need (the split column's B and A)
            assert ("σ 0.02 s with lines mid-straight, up to 0.04 s with a line at the measured "
                    "track's slowest corners") in tip, tip
            assert tip.count("within 0.11 s") == 1, tip
            assert stats.split_covers_note() in tip and "up to 0.23 s" in tip \
                and "0.37 s at the noisiest" in tip, tip
            assert "tie" in tip
            tip = q.table.item(8, 3).toolTip()
            assert "within 0.11 s" in tip and "timing noise" in tip, tip
            # the group's rule above S1, and ▶ says "sector"
            assert q.table.item(7, 0).data(GROUP_ROW_ROLE) and not q.table.item(6, 0).data(
                GROUP_ROW_ROLE)
            assert "the sector" in q.table.cellWidget(8, QUICKEST_PLAY_COL).toolTip()
            assert "the corner" in q.table.cellWidget(0, QUICKEST_PLAY_COL).toolTip()
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
        assert msg.endswith(" · ⌘K: export this pass"), msg    # exported as a corner's is
        t = s.stretch_time(lap, key)
        assert view._live_pass_line() == f"S2 · lap {lap + 1} ({t:.2f})"
        # the export picker offers this pass
        assert win.exports.pass_subject() == (lap, key, "S2")
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


def test_the_menu_plays_and_exports_a_sector_and_greys_out_a_level_compare_and_cmd_k_lists_it():
    from studio import command_palette
    prefs.set_corners_view("quickest")
    win = _window(3)
    try:
        view, s = win.view, win.session
        plays = _record_plays(view)
        q = view.corners_page.quickest
        pos = q.table.visualRect(q.table.model().index(10, 1)).center()   # S4
        menu = view.pass_menu(view._quickest_menu_pass(q.table, pos))
        play, export, compare = menu.actions()
        assert play.isEnabled() and export.isEnabled() and not compare.isEnabled()
        # S4's quickest is level with its typical split under the split's step (plus the luck a
        # quickest pass carries, where only that makes it level): the true reason
        lap = s.split_matrix().best_lap[3]
        assert compare.toolTip() == _level_reason(lap, "S4", s.pass_vs_typical(
            lap, stats.sector_key(3))), compare.toolTip()
        play.trigger()
        lap = s.split_matrix().best_lap[3]
        assert plays[-1] == s.stretch_clip(lap, stats.sector_key(3))
        titles = [e.title for e in command_palette.entries(win)]
        quick = [t for t in titles if t.startswith("Quickest ")]
        assert quick[7:] == [f"Quickest S{c + 1} — lap {s.split_matrix().best_lap[c] + 1}"
                             for c in range(4)], quick
        # ⌘K exports the sector pass being studied, as it does a corner's; no compare row for it,
        # S4's quickest being level with its typical split (the others' are: the compare test)
        assert f"Export the S4 pass — lap {lap + 1}…" in titles, titles
        assert not any(" through S" in t for t in titles), titles
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


def test_a_sector_pass_beside_its_typical_follows_the_corners_rule():
    """`SplitMatrix.pass_vs_typical` is `CornerMatrix.pass_vs_typical` over a column of splits:
    the same typical lap, gap, tie and is-typical on the same numbers (one rule,
    `stats.pass_vs_typical_of`), keyed by the sector's stretch key, under the step it is given."""
    rows = [[10.00, 20.0], [10.50, 20.1], [10.40, 20.2], [10.20, 20.3], [10.30, 20.4],
            [float("nan"), 20.5]]
    m = _splits(rows)
    corners = stats.corner_matrix(range(6), [1], [[r[0]] for r in rows], [[True]] * 6)
    for lap in range(5):
        v, c = m.pass_vs_typical(0, lap, 0.30), corners.pass_vs_typical(1, lap, 0.30)
        assert (v.lap_id, v.pass_s, v.typical_lap_id, v.typical_pass_s, v.gap_s, v.level,
                v.is_typical) == (c.lap_id, c.pass_s, c.typical_lap_id, c.typical_pass_s,
                                  c.gap_s, c.level, c.is_typical), (lap, v, c)
        assert v.cid == stats.sector_key(0) and v.level_s == 0.30
    # lap 0's 10.00 against the typical 10.30 (lap 4). Lap 0 is the QUICKEST of 5, so the step
    # carries the quickest's luck (`stats.quickest_luck_s`, 0.020 s for a sector at n = 5, fitted
    # at the split step's own placement, cool/sector-dedrift): 0.30 apart is level under
    # 0.30 + 0.020, and exactly the step plus the luck apart is NOT level
    v = m.pass_vs_typical(0, 0, 0.30)
    assert (v.typical_lap_id, v.gap_s, v.luck_s, v.level) == (4, 0.3, 0.02, True), v
    assert not m.pass_vs_typical(0, 0, 0.280).level            # 0.280 + 0.020 = 0.300
    assert m.pass_vs_typical(0, 0, 0.281).level                # the step decides it
    # a pass that is not the quickest carries no luck: lap 3's 10.20, 0.10 under the typical
    assert m.pass_vs_typical(0, 3, 0.10).luck_s == 0.0 and not m.pass_vs_typical(0, 3, 0.10).level
    assert m.pass_vs_typical(0, 4, 0.30).is_typical
    assert m.pass_vs_typical(0, 5, 0.30) is None, "a blank row is not counted"
    assert m.pass_vs_typical(2, 0, 0.30) is None, "no such column"


def _quickest_compare(view, q, row):
    pos = q.table.visualRect(q.table.model().index(row, 1)).center()
    return view.pass_menu(view._quickest_menu_pass(q.table, pos)).actions()[2]


def test_compare_with_the_typical_pass_is_offered_on_a_sector_row_only_past_the_split_step():
    """The demo with 2, 3 and 4 lines: each sector row's Compare item is enabled exactly where its
    quickest split and its typical split are NOT level under 0.11 s plus the luck (2 lines: S1 and
    S2; 3 lines: S1-S3; 4 lines: S1 and S3; every one a true gap of 0.15-0.52 s on the demo's truth), and greyed with its own computed reason
    everywhere else. On main every sector row was greyed "Not for sectors yet"."""
    prefs.set_corners_view("quickest")
    win = _window(2)
    try:
        view, s = win.view, win.session
        _record_plays(view)
        q = view.corners_page.quickest
        for n in CONFIGS:
            _with_lines(s, n)
            view.rebuild_derived_views()
            _settle()
            m = s.split_matrix()
            enabled = []
            for c in range(n + 1):
                key, lap = stats.sector_key(c), m.best_lap[c]
                compare = _quickest_compare(view, q, q.row_of(key))
                v = s.pass_vs_typical(lap, key)
                assert v.level_s == stats.SECTOR_LEVEL_S and v.typical_lap_id != lap
                assert compare.isEnabled() == (not v.level), (n, c, v)
                if compare.isEnabled():
                    enabled.append(f"S{c + 1}")
                    assert v.gap_s >= stats.SECTOR_LEVEL_S, v      # typical − quickest
                    assert compare.toolTip() == (
                        f"Lap {lap + 1} and your typical pass through S{c + 1}, lap "
                        f"{v.typical_lap_id + 1}, side by side from the lead-in")
                else:
                    assert compare.toolTip() == _level_reason(lap, f"S{c + 1}", v), \
                        compare.toolTip()
            assert enabled == DEMO_OFFERS[n], (n, enabled)
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def _panes_at(view, t_a, t_b):
    """Both compare panes at telemetry times (t_a, t_b), the tick run: (seam, badge A, badge B,
    caption, seam colour)."""
    view.video.pane.current_global_time = lambda: t_a
    view.video.secondary.current_global_time = lambda: t_b
    view.compare._compare_last_t = None
    view.compare.tick()
    v = view.video
    return (v.seam.value.text(), v._cell_a.badge.text(), v._cell_b.badge.text(),
            v.seam.caption.text())


def test_a_sector_compare_lands_both_panes_at_the_sector_and_anchors_its_readout():
    from studio.central_view import typical_compare_status
    from studio.stats import printed_gap_s
    prefs.set_corners_view("quickest")
    win = _window(3)
    try:
        view, s = win.view, win.session
        _record_plays(view)
        seeks = []
        real_seek = view.video.seek_pane
        view.video.seek_pane = lambda side, t: (seeks.append((side, t)), real_seek(side, t))
        q = view.corners_page.quickest
        key = stats.sector_key(0)
        a = s.split_matrix().best_lap[0]
        v = s.pass_vs_typical(a, key)
        b = v.typical_lap_id
        compare = _quickest_compare(view, q, q.row_of(key))
        assert compare.isEnabled()
        compare.trigger()
        _settle()
        assert view._comparing() and (view.compare.lap_a, view.compare.lap_b) == (a, b)
        # both panes at their own lead-in into S1, each pausing at its own clip end
        clip_a, clip_b = s.stretch_clip(a, key), s.stretch_clip(b, key)
        assert seeks[-2:] == [(0, clip_a[0]), (1, clip_b[0])], seeks[-2:]
        assert view.video.pane.clip_end() == clip_a[1]
        assert view.video.secondary.clip_end() == clip_b[1]
        assert view.pass_zoomed()
        assert view.compare.pass_anchor() == "S1"
        cap = f"lap {a + 1} ({v.pass_s:.2f}) vs typical lap {b + 1} ({v.typical_pass_s:.2f}) · S1"
        assert view.video.seam.caption.text() == cap
        msg = win.statusBar().currentMessage()
        assert msg == typical_compare_status(v, "S1") and " quicker through S1 as timed " in msg
        assert "fastest" not in msg.lower()
        tip = view.video.seam.toolTip()
        assert "±0.11 s" in tip and "the whole sector" in tip, tip   # the sector's own step
        # the held figure is a measurement of these two passes, not a verdict: the tip says so
        assert "the difference between these two passes as timed" in tip, tip
        assert "partly luck" in tip and "Coloured only beyond timing precision" not in tip, tip
        # the readout rule at the sector's two lines (`Session.stretch_edges`)
        (ea, xa), (eb, xb) = s.stretch_edges(a, key), s.stretch_edges(b, key)
        assert abs((xa - ea) - v.pass_s) < 1e-9 and abs((xb - eb) - v.typical_pass_s) < 1e-9
        seam, ba, bb, cap_now = _panes_at(view, clip_a[0] + 0.05, clip_b[0] + 0.05)
        assert (seam, ba, bb) == ("Δ —", "Δ —", "Δ —") and cap_now == cap, (seam, ba, bb)
        seam, ba, bb, _ = _panes_at(view, ea, eb)
        assert "0.00" in seam and "0.00" in ba and "0.00" in bb, (seam, ba, bb)
        held = printed_gap_s(v.pass_s, v.typical_pass_s)
        assert held == -v.gap_s and abs(held) >= stats.SECTOR_LEVEL_S
        for ta, tb in ((xa + 0.2, xb + 0.2), (clip_a[1] + 0.03, clip_b[1] + 0.05)):
            seam, ba, bb, _ = _panes_at(view, ta, tb)
            # held after the exit: the printed gap as measured, neutral, without ▲/▼
            assert ba == theme.format_delta_run(held, arrow=False), ba
            assert bb == theme.format_delta_run(-held, arrow=False), bb
            assert seam == theme.format_delta_run(held, arrow=False), seam
            assert (view.video._cell_a._badge_colour, view.video._cell_b._badge_colour,
                    view.video.seam.value.colour()) == (None, None, None)
        # inside the sector a Δ under the split's step is shown neutral, without its arrow
        anchor = view.compare._anchor
        assert anchor.level_s == stats.SECTOR_LEVEL_S and anchor.noun == "sector"
        view.compare._set_pane_badge(0, 0.1)
        view.compare._set_seam(0.1)
        assert view.video._cell_a.badge.text() == theme.format_delta_run(0.1, arrow=False)
        assert view.video._cell_a._badge_colour is None, "neutral: no colour, not just no arrow"
        assert view.video.seam.value.text() == theme.format_delta_run(0.1, arrow=False)
        assert view.video.seam.value.colour() is None
        # a pane scrubbed out of its clip: the anchor goes, the whole-lap Δ under the plain caption
        seam, ba, _bb, cap_now = _panes_at(view, clip_a[1] + 10.0, xb + 0.2)
        assert view.compare.pass_anchor() is None and cap_now == f"lap {a + 1} vs lap {b + 1}"
        # Esc leaves it: lap a alone again
        view.video.compare_btn.click()
        _settle()
        assert compare.isEnabled() and view.compare_pass_with_typical(a, key)
        _settle()
        assert view.compare.pass_anchor() == "S1"
        QTest.keyClick(win, Qt.Key_Escape)
        _settle()
        assert not view._comparing() and view.table.selected_lap_ids() == [a]
        assert view.compare.pass_anchor() is None and win.statusBar().currentMessage() == ""
        # an ordinary lap-vs-lap compare (C) keeps its colour rule and its wording
        win.toggle_compare()
        _settle()
        assert view._comparing() and view.compare.pass_anchor() is None
        tip = view.video.seam.toolTip()
        assert "Coloured only beyond timing precision (±0.07 s)" in tip and "luck" not in tip, tip
        view.compare._set_pane_badge(0, 0.5)
        assert view.video._cell_a._badge_colour == theme.delta_colour(0.5) is not None
        win.toggle_compare()
        _settle()
        # ⌘K: with S1's quickest pass studied, the compare is a row beside its export, and runs
        q.activate(q.row_of(key))
        _settle()
        rows = {t: c for t, c in win.quickest_pass_commands() if t.startswith("Compare")}
        title = f"Compare lap {a + 1} through S1 with typical lap {b + 1}"
        assert list(rows) == [title], list(rows)
        rows[title]()
        _settle()
        assert view._comparing() and view.compare.pass_anchor() == "S1"
        assert (view.compare.lap_a, view.compare.lap_b) == (a, b)
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_the_highlighted_row_is_the_pass_being_compared_from_the_menu_or_cmd_k():
    """A compare with the typical pass highlights the row whose pass it compares, whatever was
    highlighted before (DECISIONS §21: S4 stayed highlighted while S1 was compared). Two doors:
    ⌘K's row for the pass studied after ↓ moved the highlight off it, and the row's menu opened
    with no press on the row first (as an accessibility "show menu" opens it). A pass that is no
    row's quickest or level lap leaves no row highlighted; Esc keeps the compared row."""
    prefs.set_corners_view("quickest")
    win = _window(3)
    try:
        view = win.view
        _record_plays(view)
        q = view.corners_page.quickest
        s1, s4 = q.row_of(stats.sector_key(0)), q.row_of(stats.sector_key(3))

        def selected():
            return [i.row() for i in q.table.selectionModel().selectedRows()]
        # ⌘K: S1 played from its row, then ↓ ↓ moves the highlight; the compare row is S1's
        q.activate(s1)
        _settle()
        q.table.setFocus()
        QTest.keyClick(q.table, Qt.Key_Down)
        QTest.keyClick(q.table, Qt.Key_Down)
        assert selected() == [s1 + 2]
        rows = [c for t, c in win.quickest_pass_commands() if t.startswith("Compare")]
        assert len(rows) == 1
        rows[0]()
        _settle()
        assert view._comparing() and view.compare.pass_anchor() == "S1"
        assert selected() == [s1], (selected(), s1)
        QTest.keyClick(win, Qt.Key_Escape)
        _settle()
        assert not view._comparing() and selected() == [s1]
        # the menu: S4 played and highlighted, S1's Compare fired with no press on S1's row
        q.activate(s4)
        _settle()
        assert selected() == [s4]
        _quickest_compare(view, q, s1).trigger()
        _settle()
        assert view._comparing() and view.compare.pass_anchor() == "S1"
        assert selected() == [s1], (selected(), s1)
        # a lap that is not the row's quickest nor level with it: no row names that pass
        lap, cid = next((lap, qp.cid) for qp in q.passes()
                        for lap in win.session.valid_lap_ids() if lap not in qp.play_order()
                        and view.typical_compare_refusal(lap, qp.cid) is None)
        assert view.compare_pass_with_typical(lap, cid)
        _settle()
        assert selected() == [], selected()
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_export_this_pass_on_a_sector_row_opens_the_picker_on_that_sector_and_exports_it():
    """The S3 row's right-click "Export this pass…", fired as the real QAction: the overlay picker
    opens under its real exec() (a timer reads it, then accepts), on "This pass — S3, lap N", with
    the sector's own words and no corner cards; the save panel proposes `_lapN_S3_overlay.mp4` and
    the render is handed the sector's pass (captured, not run)."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QCheckBox, QDialog, QFormLayout, QLabel

    from studio import export_video as ev
    from studio.overlay import corner_cards
    prefs.set_corners_view("quickest")
    win = _window(3)
    ctl = win.exports
    orig = ctl._export_save_path, ctl._run_video_export
    try:
        view, s = win.view, win.session
        win._share_card_blocked = lambda: False    # the trust prompt is not what is under test
        q = view.corners_page.quickest
        pos = q.table.visualRect(q.table.model().index(9, 1)).center()   # S3
        target = view._quickest_menu_pass(q.table, pos)
        lap, key = target[0], target[1]
        assert key == stats.sector_key(2)
        export = view.pass_menu(target).actions()[1]
        assert export.text() == "Export this pass…" and export.isEnabled(), export.toolTip()
        seen, opened = {}, []

        def read_and_accept():
            dlg = QApplication.activeModalWidget()
            if not isinstance(dlg, QDialog) or opened:
                return
            form = dlg.findChild(QFormLayout)
            rows = {form.itemAt(r, QFormLayout.ItemRole.LabelRole).widget().text():
                    form.itemAt(r, QFormLayout.ItemRole.FieldRole).widget()
                    for r in range(form.rowCount())
                    if form.itemAt(r, QFormLayout.ItemRole.LabelRole) is not None}
            cards = next(b for b in rows["Extras"].findChildren(QCheckBox)
                         if b.text() == ctl._CARDS_LABEL)
            opened.append({
                "title": dlg.windowTitle(), "scope": rows["Export"].currentText(),
                "notes": [w.text() for w in dlg.findChildren(QLabel) if w.property("role") == "Note"],
                "hint": " ".join(w.text() for w in dlg.findChildren(QLabel)
                                 if w.property("role") == "Hint"),
                "cards": (cards.isEnabled(), cards.toolTip())})
            dlg.accept()

        def save(_title, suffix, _filt):
            seen["save"] = suffix
            return os.path.join(_DEMO["dir"].name, "out" + suffix)

        def run(specs, **_kw):
            seen["specs"] = specs
            for spec in specs:
                spec.source.cleanup()
        ctl._export_save_path, ctl._run_video_export = save, run
        timer = QTimer()
        timer.setInterval(20)
        timer.timeout.connect(read_and_accept)
        timer.start()
        try:
            export.trigger()
            _settle(0.1)
        finally:
            timer.stop()
        (got,) = opened
        split = s.stretch_time(lap, key)
        assert got["title"] == "Export overlay video" and \
            got["scope"] == f"This pass — S3, lap {lap + 1}", got
        assert f"Lap {lap + 1}  ·  S3  ·  {split:.2f} s" in got["notes"], got["notes"]
        assert any("the lap and the sector, with the sector's time" in n for n in got["notes"])
        assert f"lap {lap + 1}'s pass through S3" in got["hint"] and "sector's time" in got["hint"]
        assert got["cards"] == (False, f"Off here: {corner_cards.SECTOR_PASS_REASON}"), got["cards"]
        assert seen["save"] == f"_lap{lap + 1}_S3_overlay.mp4", seen
        (spec,) = seen["specs"]
        assert spec.pass_subject == ev.PassSubject(key, "S3", split), spec.pass_subject
        assert (spec.t0, spec.t1) == ev.pass_window_for_export(s, lap, key)[::3]
    finally:
        ctl._export_save_path, ctl._run_video_export = orig
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
        assert "no other lap took this sector within 0.11 s" in q.table.item(7, 1).toolTip()
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


def _strip_state(win):
    t = win.view.corners_page.quickest.table
    return t.isColumnHidden(QUICKEST_STRIP_COL), t.columnWidth(QUICKEST_STRIP_COL)


def test_drawing_lines_never_takes_the_corner_rows_strip_or_cuts_their_words():
    """The corners' Every lap strip and their words are measured off the CORNER rows: lines added
    change neither, at 1280×800 and 1440×900. A sector row too narrow for its cycled lap's time
    leaves the time out (the status line names it), so no sector word is cut either."""
    prefs.set_corners_view("quickest")
    try:
        for size in ((1280, 800), (1440, 900)):
            widths = {}
            for n in (0, 2, 3, 4):
                win = _window(n, size)
                try:
                    q = win.view.corners_page.quickest
                    widths[n] = _strip_state(win)
                    _record_plays(win.view)
                    for row in range(q.table.rowCount()):     # every row, cycled to every lap
                        for _ in q.passes()[row].play_order():
                            q.activate(row)
                            item = q.table.item(row, 3)
                            need = q._level_px([item.text()])
                            assert need <= q.table.columnWidth(3), (size, n, row, item.text())
                    assert _strip_state(win) == widths[n]
                finally:
                    _close(win)
            print("strip (hidden, px)", size, widths)
            assert len(set(widths.values())) == 1, (size, widths)
            assert not widths[0][0], "the demo's corner strip shows at this size"
    finally:
        prefs.set_corners_view("lap")


def test_the_walk_from_s1_backwards_stays_on_s1():
    """M7: the walk never crosses from the sectors into the corners."""
    prefs.set_corners_view("quickest")
    win = _window(3)
    try:
        view = win.view
        _record_plays(view)
        q = view.corners_page.quickest
        notices = []
        view.statusNotice.connect(notices.append)
        q.activate(7)                              # S1
        _settle()
        assert not view.step_corner_pass(-1)
        assert notices[-1].startswith("No earlier sector to play"), notices
        assert q.playing()[0] == stats.sector_key(0) and view.pass_subject()[1] == -1
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_a_moved_line_leaves_the_studied_sector_whose_clip_it_moved():
    prefs.set_corners_view("quickest")
    win = _window(3)
    try:
        view, s = win.view, win.session
        _record_plays(view)
        q = view.corners_page.quickest
        q.activate(8)                              # S2: between the first and second line
        _settle()
        lap, key, _label = view.pass_subject()
        old = view.video.pane.pass_clip()
        truth = _DEMO["truth"]
        mid = truth.circuit.straights[3]
        lines = _lines(3)
        lines[1] = truth.line_at(mid[0] + 0.35 * (mid[1] - mid[0]))   # S2's end line, moved
        start, _ = s.timing_lines_latlon()
        assert s.apply_timing_lines_latlon(start, lines, confirmed=True)
        assert s.stretch_clip(lap, key) != old
        view.rebuild_derived_views()
        _settle()
        assert view.pass_subject() is None and view.video.pane.pass_clip() is None
        assert q.playing() is None
        # ...while a pass the edit did not move stays the subject (S1 starts and ends where it did)
        q.activate(7)
        _settle()
        held = view.pass_subject()
        lines[2] = truth.line_at(sum(truth.circuit.straights[5]) / 2.0 + 10.0)  # S3/S4's line
        assert s.apply_timing_lines_latlon(start, lines, confirmed=True)
        view.rebuild_derived_views()
        _settle()
        assert view.pass_subject() == held
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_a_sector_under_the_fold_is_scrolled_into_view_when_it_plays():
    prefs.set_corners_view("quickest")
    win = _window(4, (1280, 800))
    try:
        view = win.view
        _record_plays(view)
        q = view.corners_page.quickest
        t = q.table
        # 4 lines at 1280×800 docked overflow; no bar takes the strip's width, the rows scroll
        # (the overlaid bar says so: test_every_row_is_reachable_and_a_bar_says_when_some_are_below)
        assert t.verticalScrollBar().maximum() > 0 and not t.verticalScrollBar().isVisible()
        t.scrollToTop()
        last = t.rowCount() - 1
        q.activate(last - 1)                       # S4 …
        assert view.step_corner_pass(+1)           # … ⌥→ to S5, the last row
        _settle()
        rect, port = t.visualRect(t.model().index(last, 0)), t.viewport().rect()
        assert port.contains(rect), (rect, port)
        btn = t.cellWidget(last, QUICKEST_PLAY_COL)
        assert btn.isVisible() and port.contains(btn.geometry().center())
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def _shown_vertical_bars(table):
    """Every vertical scrollbar the driver can SEE on the table: its own, or one laid over it."""
    return [b for b in table.findChildren(QScrollBar)
            if b.orientation() == Qt.Vertical and b.isVisible()]


def test_every_row_is_reachable_and_a_bar_says_when_some_are_below():
    """0.18.0 QA: with the table's bar hidden for the strip (#631), 3 lines at 1280×800 docked cut
    the last sector row to a sliver with nothing saying there was more. Now, at both sizes and
    0/2/3/4 lines: a scrollbar shows exactly when the rows overflow, it takes no width from the
    columns (the strip and the words keep the widths they have with no line), its grip runs clear
    of every ▶, and dragging it to the end brings the last row and its ▶ fully into view."""
    prefs.set_corners_view("quickest")
    try:
        for size in ((1280, 800), (1440, 900)):
            base = None
            for n in (0, 2, 3, 4):
                win = _window(n, size)
                try:
                    t = win.view.corners_page.quickest.table
                    over = t.verticalScrollBar().maximum() > 0
                    bars = _shown_vertical_bars(t)
                    print("rows overflow / bars shown", size, n, over, len(bars))
                    assert len(bars) == int(over), (size, n, over, bars)
                    cols = (t.viewport().width(), _strip_state(win), t.columnWidth(3))
                    base = base or cols
                    assert cols == base, (size, n, cols, base)
                    if not over:
                        continue
                    bar = bars[0]
                    # no ▶ pixel under the bar's HIT AREA (it would take that pixel's click), not
                    # merely under its painted grip: the region the bar takes input on, and the
                    # widget Qt's own hit test finds at every ▶ edge pixel, which is the ▶
                    hit = bar.mask().boundingRect() if not bar.mask().isEmpty() else bar.rect()
                    hit_x = t.viewport().mapFrom(t, bar.pos() + hit.topLeft()).x()   # bar: a table child
                    # ...and the grip keeps the theme's SPACE_S, all of it inside the hit area
                    img = bar.grab().toImage()
                    grip = [x for x in range(img.width())
                            if img.pixelColor(x, theme.CTRL_H // 2).alpha() > 0]
                    assert len(grip) >= theme.SPACE_S and grip[0] >= hit.left(), (grip, hit)
                    for r in range(t.rowCount()):
                        btn = t.cellWidget(r, QUICKEST_PLAY_COL)
                        if not t.viewport().rect().intersects(btn.geometry()):
                            continue
                        assert btn.geometry().right() < hit_x, (size, n, r, btn.geometry(), hit_x)
                        edge = btn.geometry().topRight() + QPoint(0, btn.height() // 2)
                        at = t.childAt(t.viewport().mapTo(t, edge))
                        assert at is btn or btn.isAncestorOf(at), (size, n, r, at)
                    assert bar.geometry().right() < t.width()
                    own = t.verticalScrollBar()
                    assert bar.pageStep() == own.pageStep(), (size, n, bar.pageStep(),
                                                              own.pageStep())
                    bar.setValue(bar.maximum())
                    _settle()
                    last = t.rowCount() - 1
                    port = t.viewport().rect()
                    assert port.contains(t.visualRect(t.model().index(last, 0))), (size, n)
                    btn = t.cellWidget(last, QUICKEST_PLAY_COL)
                    assert port.contains(btn.geometry()), (size, n, btn.geometry(), port)
                    t.scrollToTop()                   # ...and the bar follows the table back
                    _settle()
                    assert bar.value() == 0
                    # the page follows the table's after a resize and a rebuild (it was copied
                    # one layout behind: 12 for 8 after a resize, 7 for 10 after a rebuild)
                    w, h = size
                    for change in ((win.resize, w, h - 60), (win.resize, w, h),
                                   (win.view.rebuild_derived_views,)):
                        change[0](*change[1:])
                        _settle(0.2)
                        assert bar.pageStep() == own.pageStep(), (size, n, bar.pageStep(),
                                                                  own.pageStep())
                finally:
                    _close(win)
            # the demo's corner strip, which the bar may not narrow (75 has 3 px over STRIP_MIN_PX)
            assert base[1] == (False, {(1280, 800): 75, (1440, 900): 92}[size]), (size, base)
    finally:
        prefs.set_corners_view("lap")


def test_the_splits_note_points_to_where_the_level_laps_are_counted():
    from studio.stats_panel import SPLITS_NOTE
    note = SPLITS_NOTE.format(n=14, c=3, scale="0.30 s")
    assert "Quickest passes, on the Corners tab, says how many laps are level" in note, note


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"{name} OK")
