"""Pure-Python tests for the studio UI features F1–F5 (no pacer, no telemetry file, fast).

These exercise the load-bearing pure logic directly on synthetic data:
  * F1 — the numeric sort key of the lap-table cell (`_NumItem.__lt__`): times/splits sort by
    their underlying float, blanks/NaN sort last.
  * F3 — `Session.nearest_index_in_lap` / `nearest_time_in_lap`: the marker drag is constrained
    to ONE lap's points and clamped to its time window (built on a bare Session, no pacer).
  * F5 — per-sector session-best = the per-column MINIMUM split across valid laps (now
    `Session.session_best_splits`, hoisted from lap_table so the purple cells and the
    theoretical-best footer share one computation).
  * Theoretical/rolling footer (F1-roadmap; C10 redesign) — a real offscreen LapTable on a
    fake session: the two SESSION-BESTS tiles exist OUTSIDE the sortable table, carry NEUTRAL
    (not purple — the purple is the per-sector best cells') values + defining tooltips, show
    fmt_time'd session values, survive a sort, and update on refresh() after a (simulated)
    timing-line move.
  * Auto-follow — `CentralView._follow_current_lap` (F7: moved off StudioWindow onto the
    session-scoped central view): the speed+delta charts switch to the playhead's lap (vs best)
    only on a lap-change EDGE; None (lead-in) HOLDS the last lap; the table re-select uses the
    programmatic (no-seek) path so it never fights playback.
  * F7 atomic CentralView swap — `StudioWindow._build_ui`: a reload disposes the OLD view (its
    VideoView.stop_all) BEFORE building + swapping in a fresh CentralView, the persistent ~30 Hz
    tick timer is created ONCE and REUSED across the swap (not duplicated), and the window chrome
    (_video_do) resolves to the NEW view's video — proven on a fake-CentralView window with no
    pacer / real panels.
Run: python tests/test_studio_features.py
"""
import faulthandler
import math
import os
import sys
from types import SimpleNamespace

import numpy as np

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

# The bundled GoPro sample as an ABSOLUTE path. ctest runs this file from build/Release/tests, so
# the repo-RELATIVE studio.session.DEFAULT_SAMPLE would not resolve there — the load would fail
# and (pre-fix) hang the headless suite on the modal error dialog. Tests that load it use this.
_BUNDLED_SAMPLE = os.path.join(_REPO, "3rdparty", "gpmf-parser", "samples", "hero6.mp4")

# QTableWidgetItem needs a QApplication for _NumItem; create one offscreen.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from _synthetic import bare_session, seed_cols  # noqa: E402

from studio import gapfill, theme  # noqa: E402
from studio._signal import fmt_time  # noqa: E402
from studio.lap_table import (  # noqa: E402
    FOOTER_ROWS,
    NUM_ROLE,
    LapTable,
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
def _session_with_splits(splits, n_lines):
    """A bare Session whose `lap_sector_splits` is stubbed per lap and whose sector-line
    count is `n_lines` — the read surface `session_best_splits` touches (plus the seeded
    valid-lap memo), so the hoisted per-column-min runs with no pacer."""
    s = bare_session(valid=sorted(splits))
    s.lap_sector_splits = lambda lid: splits[lid]
    s.laps = SimpleNamespace(sectors=SimpleNamespace(sector_lines=[object()] * n_lines))
    # session_best_splits now excludes GPS-dropout laps (A1), so it reads each lap's trace via
    # lap_has_dropout -> seed a trivial steady (dropout-free) trace per lap so all stay candidates.
    steady_t = np.arange(20) * 0.1
    steady_d = np.linspace(0.0, 100.0, 20)
    for lid in splits:
        seed_cols(s, lid, steady_t, steady_d)
    return s


def test_best_split_per_sector_is_column_min():
    """The purple per-sector session-best (`Session.session_best_splits`) is the per-column
    MINIMUM across valid laps, computed independently per column; a no-data column → None."""
    splits = {
        0: [34.5, 11.0, 22.9],
        1: [34.2, 10.6, 23.1],  # min S1 (34.2) and min S2 (10.6) here
        2: [35.0, 11.2, 22.6],  # min S3 (22.6) here
        3: [34.9],              # partial lap: only S1 present
    }
    s = _session_with_splits(splits, n_lines=2)  # 2 sector lines -> 3 columns
    best = s.session_best_splits()
    assert best == [34.2, 10.6, 22.6], best
    # No-data columns -> None (and theoretical_best is undefined there).
    s2 = _session_with_splits({0: []}, n_lines=1)
    assert s2.session_best_splits() == [None, None]
    assert s2.theoretical_best() is None
    print("test_best_split_per_sector_is_column_min OK")


# -------------------------------------- theoretical / rolling footer rows (F1-roadmap)
class _FakeFooterSession:
    """The full read surface LapTable touches, with adjustable summary values so a refresh()
    after a (simulated) timing-line move shows new footer numbers. 1 sector line -> 2 S-columns;
    lap 1 is the best lap; the seeded splits make the per-column minima [33.8, 34.4]."""

    # Verified timing (a detected/confirmed track) so the footer + purple-best assertions below
    # read the authoritative styling, not the provisional muting (see test_lap_table_provisional_*).
    timing_verified = True

    def __init__(self):
        self.splits = {0: [33.8, 36.2], 1: [34.0, 34.4], 2: [35.5, 35.7]}
        self.theo = 68.2     # = 33.8 + 34.4 (sum of the per-column minima)
        self.rolling = 68.3  # theoretical <= rolling <= best lap (68.4)

    def lap_rows(self):
        return [{"idx": 0, "time": 70.0, "dist": 1001.0, "entry": 51.0},
                {"idx": 1, "time": 68.4, "dist": 998.0, "entry": 52.5},
                {"idx": 2, "time": 71.2, "dist": 1003.0, "entry": 49.0}]

    def sector_count(self):
        return 1

    def lap_sector_splits(self, lap_id):
        return self.splits[lap_id]

    def session_best_splits(self):
        return [min(sp[i] for sp in self.splits.values()) for i in range(2)]

    def theoretical_best(self):
        return self.theo

    def best_rolling_lap(self):
        return self.rolling

    def best_lap_id(self):
        return 1

    def dropout_lap_ids(self):
        return set()


def _footer_texts(table):
    return [label.text() for label in table._footer_values]


def test_lap_table_footer_rows_present_styled_and_valued():
    """The two SESSION-BESTS tiles exist BELOW the table (never as sortable table rows), read the
    session's theoretical/rolling values through fmt_time, and carry a defining tooltip each.

    C10: the values are styled in the NEUTRAL primary text — NOT the C.best purple — so the
    purple is reserved strictly for the per-sector best cells (a former semantic-colour
    collision); the block instead gets hierarchy from a "SESSION BESTS" section header."""
    from PySide6.QtWidgets import QLabel

    sess = _FakeFooterSession()
    table = LapTable(sess)
    # Footer is NOT table rows: the table holds exactly the 3 laps.
    assert table.table.rowCount() == 3
    assert _footer_texts(table) == [fmt_time(68.2), fmt_time(68.3)]
    # Neutral text colour (NOT purple) + a tooltip on every value label.
    for label in table._footer_values:
        assert isinstance(label, QLabel)
        assert theme.C.text in label.styleSheet(), label.styleSheet()
        assert theme.C.best not in label.styleSheet(), \
            "footer value must NOT reuse the per-sector best purple (C10)"
        assert label.toolTip(), "footer tile must carry its defining tooltip"
    # The purple-cell target the table paints is the SAME accessor the footer sums.
    assert table._best_split == sess.session_best_splits()
    # And the fake's numbers respect the sandwich the real session guarantees.
    assert sess.theoretical_best() <= sess.best_rolling_lap() <= 68.4
    print("test_lap_table_footer_rows_present_styled_and_valued OK")


def test_lap_table_footer_survives_sort_and_updates_on_refresh():
    """Sorting any column must not move/change the footer (it lives outside the QTableWidget);
    a refresh() after the session's values changed (what a start-line move triggers via
    app._on_lines -> table.refresh()) rewrites the footer numbers."""
    from PySide6.QtCore import Qt

    sess = _FakeFooterSession()
    table = LapTable(sess)
    before = _footer_texts(table)
    table.table.sortByColumn(1, Qt.DescendingOrder)  # sort by Time, descending
    assert table.table.rowCount() == 3               # footer never became a table row
    assert _footer_texts(table) == before, "sort must not disturb the footer"

    # Simulate a start-line move: the re-segmentation changes the summary values; the app's
    # _on_lines handler then calls table.refresh(), which must rewrite the footer.
    sess.theo, sess.rolling = 67.9, 68.05
    table.refresh()
    assert _footer_texts(table) == [fmt_time(67.9), fmt_time(68.05)]
    # None (e.g. no valid laps after a bad edit) renders as the em-dash.
    sess.theo, sess.rolling = None, None
    table.refresh()
    assert _footer_texts(table) == ["—", "—"]
    print("test_lap_table_footer_survives_sort_and_updates_on_refresh OK")


# ------------------------------------- timing-trust: provisional lap-table treatment
class _TrustSession(_FakeFooterSession):
    """A footer session whose timing-trust is togglable, to drive LapTable's provisional vs
    verified rendering (the rest of the read surface is _FakeFooterSession's)."""

    def __init__(self, verified):
        super().__init__()
        self.timing_verified = verified


def _time_col_items(table):
    """Every Time-column cell (col 1) across the rows."""
    return [table.table.item(r, 1) for r in range(table.table.rowCount())]


def _sector_col_items(table):
    """Every S-split cell (cols after the base COLUMNS) across the rows."""
    from studio.lap_table import COLUMNS
    n = table._n_split_cols()
    return [table.table.item(r, COLUMNS.__len__() + i)
            for r in range(table.table.rowCount()) for i in range(n)]


def _any_purple_or_green(table):
    """True if ANY cell is painted the best-sector purple or the best-lap green (the 'best'
    authority cues that must vanish on provisional timing)."""
    purple = theme.C.best.upper()
    green = theme.C.ahead.upper()
    for r in range(table.table.rowCount()):
        for c in range(table.table.columnCount()):
            it = table.table.item(r, c)
            if it is not None and it.foreground().color().name().upper() in (purple, green):
                return True
    return False


def test_lap_table_provisional_mutes_times_and_drops_bests():
    """PROVISIONAL timing (timing_verified False): the Time + S-split cells are muted (italic +
    the dimmed provisional colour) with the 'Provisional' tooltip, and NEITHER the purple
    session-best splits NOR the green best-lap are painted — a 'best' on an arbitrary start line is
    meaningless. The footer tiles are likewise muted (italic). Dist/Entry stay normal."""
    from studio.lap_table import BASE_COLOR, PROVISIONAL_TOOLTIP

    table = LapTable(_TrustSession(verified=False))
    prov = theme.PROVISIONAL_COLOR.upper()
    # Time + sector cells: muted colour, italic font, provisional tooltip.
    for it in _time_col_items(table) + _sector_col_items(table):
        assert it.foreground().color().name().upper() == prov, "timing cell must be muted"
        assert it.font().italic(), "timing cell must be italic (provisional)"
        assert it.toolTip() == PROVISIONAL_TOOLTIP, it.toolTip()
    # No purple-best / green-best authority anywhere.
    assert not _any_purple_or_green(table), "provisional timing must paint no purple/green bests"
    # Dist column (2) untouched: base colour, not italic.
    dist = table.table.item(0, 2)
    assert dist.foreground().color().name().upper() == BASE_COLOR.name().upper()
    assert not dist.font().italic()
    # Footer tiles muted + italic.
    for label in table._footer_values:
        assert label.font().italic(), "provisional footer tile must be italic"
        assert theme.PROVISIONAL_COLOR in label.styleSheet(), label.styleSheet()
    print("test_lap_table_provisional_mutes_times_and_drops_bests OK")


def test_lap_table_verified_restores_bests_and_unmutes():
    """VERIFIED timing renders exactly as before the trust surface: the purple per-sector best +
    green best lap ARE painted, the Time/S cells are NOT italic and carry no provisional tooltip,
    and the footer tiles are non-italic neutral text. This is the behaviour-preserving Verified
    path; flipping a provisional table to Verified (a confirmed drag) restores it live."""
    # Start Provisional, then flip to Verified and refresh — mirrors a confirming drag.
    sess = _TrustSession(verified=False)
    table = LapTable(sess)
    assert not _any_purple_or_green(table)  # provisional: no bests
    sess.timing_verified = True
    table.refresh()

    # Purple best-sector cells reappear (the per-column minima [33.8, 34.4]).
    assert _any_purple_or_green(table), "verified timing must restore the best highlights"
    # Time + sector cells un-muted: not italic, no provisional tooltip.
    for it in _time_col_items(table) + _sector_col_items(table):
        assert not it.font().italic(), "verified timing cell must not be italic"
        assert it.toolTip() == "", it.toolTip()
    # Footer tiles non-italic.
    for label in table._footer_values:
        assert not label.font().italic(), "verified footer tile must not be italic"
    print("test_lap_table_verified_restores_bests_and_unmutes OK")


def test_lap_table_footer_accessors_are_callables():
    """F8a: each FOOTER_ROWS accessor is a CALLABLE `session -> value` (not a method-NAME string
    resolved via getattr). It must resolve directly off the session and produce the SAME values the
    footer shows — so a renamed Session method is a real reference error, not a silent footer miss.

    Guards both: that the accessors are callable (the regression the string form invited), and that
    calling them maps 1:1 onto the rendered tiles. Also flips the session's numbers to confirm the
    callable re-reads live state (no cached name lookup)."""
    sess = _FakeFooterSession()
    # The accessors are callables — calling each yields the same numbers the rendered footer shows.
    by_call = [acc(sess) for _title, acc, _tip in FOOTER_ROWS]
    assert all(callable(acc) for _t, acc, _tip in FOOTER_ROWS), "FOOTER_ROWS accessors must be callables"
    assert by_call == [sess.theoretical_best(), sess.best_rolling_lap()] == [68.2, 68.3]
    # The rendered tiles equal fmt_time of those callable results (1:1 with FOOTER_ROWS order).
    table = LapTable(sess)
    assert _footer_texts(table) == [fmt_time(v) for v in by_call]
    # The callables read LIVE state: change the session, re-call, the values track.
    sess.theo, sess.rolling = 67.5, 67.9
    assert [acc(sess) for _t, acc, _tip in FOOTER_ROWS] == [67.5, 67.9]
    print("test_lap_table_footer_accessors_are_callables OK")


# ------------------------------------------------ E1: zero-valid-lap empty states
class _FakeEmptySession:
    """A loaded session that reports ZERO valid laps (short clip / no GPS lock). Exposes only the
    read surface LapTable.refresh() touches; lap_rows() is [] so every per-lap accessor is unused."""

    timing_verified = True  # part of the read surface (the footer reads it); no rows to mute anyway

    def lap_rows(self):
        return []

    def sector_count(self):
        return 0

    def session_best_splits(self):
        return [None]  # one entry (sector_count()+1); unused with 0 split columns

    def theoretical_best(self):
        return None

    def best_rolling_lap(self):
        return None

    def best_lap_id(self):
        return None

    def dropout_lap_ids(self):
        return set()


def test_lap_table_shows_empty_state_when_no_laps():
    """E1: a recording with zero valid laps must NOT render a blank grid. refresh() flips the
    stacked widget to the centred, dimmed empty-state placeholder (index 1, the EmptyState role),
    the table holds zero rows, and the summary footer reads em-dashes — an explained state, not a
    broken-looking app. A subsequent refresh with rows would flip back (index 0)."""
    table = LapTable(_FakeEmptySession())
    assert table.table.rowCount() == 0
    assert table._stack.currentIndex() == 1, "lap table must show the empty state, not the grid"
    assert table._empty.property("role") == "EmptyState"
    assert table._empty.text(), "empty-state placeholder must carry a message"
    # The footer survives (outside the table) and reads em-dashes with no laps.
    assert _footer_texts(table) == ["—", "—"]

    # And with laps it flips BACK to the table (no sticky empty state).
    table.session = _FakeFooterSession()
    table.refresh()
    assert table._stack.currentIndex() == 0
    assert table.table.rowCount() == 3
    print("test_lap_table_shows_empty_state_when_no_laps OK")


def test_plots_view_shows_empty_state_when_no_laps():
    """E1: with no laps to plot, PlotsView.refresh() shows the centred empty-state placeholder
    (stack index 1) instead of leaving blank axes; with data it shows the charts (index 0)."""
    from studio.plots_view import PlotsView

    class _Sess:
        def __init__(self, has_laps):
            self._has = has_laps

        def has_reference(self):
            return False

        def best_lap_id(self):
            return 0 if self._has else None

        def delta(self, ids, x_mode):
            # Mirror Session.delta: a falsy result (no baseline / no laps) means nothing to plot.
            if not self._has:
                return None
            best, n = 0, 5
            xs = np.linspace(0, 100, n)
            return best, {0: (xs, xs)}, {0: (xs, xs * 0.0)}

        def lap_time(self, lid):  # used by the curve legend on the has-laps path
            return 60.0

        def lap_window(self, lid):
            return None

    pv = PlotsView(_Sess(has_laps=False))
    pv.refresh()
    assert pv._stack.currentIndex() == 1, "plots must show the empty state with no laps"
    assert pv._empty.property("role") == "EmptyState"
    assert pv._empty.text(), "empty-state placeholder must carry a message"

    # With data it shows the charts panel (index 0), not the placeholder.
    pv2 = PlotsView(_Sess(has_laps=True))
    pv2.refresh()
    assert pv2._stack.currentIndex() == 0
    print("test_plots_view_shows_empty_state_when_no_laps OK")


def test_plots_view_ideal_toggle_adds_labeled_curve():
    """D1: the ideal-lap toggle is OFF by default (no ideal curve, hidden Δ legend); turning it
    on draws ONE extra dashed curve on the Δ plot + reveals the synthetic-labeled legend, and
    turning it off removes it again. Honors both x-modes (it asks the session per mode)."""
    from studio.plots_view import PlotsView

    class _Sess:
        def __init__(self):
            self.ideal_modes = []  # records the x_mode each ideal_delta_to_best call sees

        def has_reference(self):
            return False

        def best_lap_id(self):
            return 0

        def delta(self, ids, x_mode):
            xs = np.linspace(0, 100, 5)
            return 0, {0: (xs, xs)}, {0: (xs, xs * 0.0)}

        def ideal_delta_to_best(self, x_mode="distance"):
            self.ideal_modes.append(x_mode)
            xs = np.linspace(0, 100, 5)
            return xs, xs * -0.01  # ≤ 0, the ideal-vs-best shape

        def lap_time(self, lid):
            return 60.0

        def lap_window(self, lid):
            return None

    sess = _Sess()
    pv = PlotsView(sess)
    pv.set_laps([0])
    base_curves = len(pv._curves)
    assert not pv._show_ideal and not pv._delta_legend.isVisible()  # off by default

    pv.ideal_btn.setChecked(True)  # toggled() → _on_ideal_toggled → refresh
    assert pv._show_ideal
    assert len(pv._curves) == base_curves + 1, "ideal toggle must add exactly one curve"
    assert pv._delta_legend.isVisible(), "ideal legend must reveal when on"
    assert sess.ideal_modes[-1] == "distance"

    # Time mode must also query the ideal (honors both x-modes).
    pv.x_mode_combo.setCurrentIndex(1)
    assert sess.ideal_modes[-1] == "time"

    pv.ideal_btn.setChecked(False)  # back off → curve gone, legend hidden
    assert not pv._show_ideal
    assert len(pv._curves) == base_curves
    assert not pv._delta_legend.isVisible()
    print("test_plots_view_ideal_toggle_adds_labeled_curve OK")


# -------------------------------------- D2: a failed RELOAD must not corrupt _paths/title/session
def test_failed_reload_preserves_paths_title_and_session():
    """D2: File ▸ Open / Load full recording on a missing/corrupt file while a good session is
    loaded must leave the previous session untouched — the error dialog promises exactly that.
    The bug was _load assigning self._paths BEFORE the guarded Session.load, so a failed reload
    desynced _paths (and every export source / title / sync that reads it) from the still-loaded
    session. Drive the path logic directly (no pacer / no real load): seed a good session + _paths
    + title, run the failure handler (the branch _load takes on a caught load error), and assert
    nothing moved. A separate FIRST-load failure (no session yet) must instead SEED _paths."""
    from PySide6.QtWidgets import QMainWindow

    from studio.app import StudioWindow

    # Construct the QMainWindow base (so setWindowTitle / QMessageBox parent work) but SKIP the
    # heavy StudioWindow.__init__ (which would run a real load). __new__ + QMainWindow.__init__.
    w = StudioWindow.__new__(StudioWindow)
    QMainWindow.__init__(w)
    good = ["/Users/x/Desktop/D24/GOOD.MP4"]
    w._paths = list(good)
    w.session = object()  # a stand-in "good session" the failed reload must not replace
    good_session = w.session
    w.setWindowTitle("pacer studio — GOOD")

    # Avoid the modal dialog blocking the headless test (the dialog itself isn't under test). The
    # handler now builds a QMessageBox and calls .exec(), so stub the instance exec().
    from PySide6.QtWidgets import QMessageBox
    orig_exec = QMessageBox.exec
    QMessageBox.exec = lambda self: QMessageBox.Ok
    try:
        # The failed RELOAD: _load catches the load error and calls _on_load_failed. A session is
        # already set, so it must take the "leave the working UI intact" branch.
        w._on_load_failed(["/nonexistent/bad.MP4"], RuntimeError("Failed to open file"))
    finally:
        QMessageBox.exec = orig_exec

    assert w._paths == good, f"_paths corrupted by a failed reload: {w._paths}"
    assert w.windowTitle() == "pacer studio — GOOD", w.windowTitle()
    assert w.session is good_session, "the failed reload replaced the good session"

    # FIRST-load failure (no session yet): _on_load_failed must SEED _paths so readers that stay
    # reachable (the still-enabled "Load full recording" action) always find a value.
    w2 = StudioWindow.__new__(StudioWindow)
    QMainWindow.__init__(w2)
    QMessageBox.exec = lambda self: QMessageBox.Ok
    try:
        w2._on_load_failed(["/nonexistent/first.MP4"], RuntimeError("boom"))
    finally:
        QMessageBox.exec = orig_exec
    assert w2._paths == ["/nonexistent/first.MP4"], w2._paths
    assert not hasattr(w2, "session")
    print("test_failed_reload_preserves_paths_title_and_session OK")


def test_load_failure_message_is_plain_language():
    """The honest-failure-UX fix: _load_failure_message maps the actionable load failures to plain
    sentences and NEVER leaks a raw Python class name (RuntimeError/KeyError/…) into the headline.
    The raw text stays in the dialog details / console log only."""
    from studio.app import StudioWindow
    msg = StudioWindow._load_failure_message

    # A real-but-non-GoPro MP4 (exists on disk): GPMFSource raises "Failed to open file: …".
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".mp4") as tf:
        m = msg([tf.name], RuntimeError(f"Failed to open file: {tf.name}"))
    assert "GoPro" in m and "GPS" in m, m
    assert "RuntimeError" not in m, f"leaked the class name: {m}"

    # A missing file: distinguished from non-GoPro by the path check.
    m_missing = msg(["/nonexistent/gone.MP4"], RuntimeError("Failed to open file: /nonexistent/gone.MP4"))
    assert "moved or deleted" in m_missing.lower(), m_missing
    assert "RuntimeError" not in m_missing, m_missing

    # An unexpected cause (e.g. a numpy/binding KeyError): a generic honest message, still no class
    # name up front.
    m_other = msg(["/nonexistent/x.MP4"], KeyError("GPS9"))
    assert "KeyError" not in m_other, f"leaked the class name: {m_other}"
    assert m_other and m_other[0].isupper(), m_other  # a real sentence, not a traceback fragment
    print("test_load_failure_message_is_plain_language OK")


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
    """A CentralView built without __init__ (no Qt/pacer), with the collaborators the
    auto-follow logic touches stubbed by a recorder, so `_follow_current_lap` runs unchanged.

    F7: the per-frame auto-follow + selection logic moved off StudioWindow onto CentralView (which
    now OWNS the session-scoped panels + the shared cursor); the method body is byte-identical, so
    these tests construct the view that owns it. F5: the auto-follow cursor lives on a shared
    PlaybackState (`w._playback.followed_lap`) — seed one here for the __new__'d view."""
    from studio.central_view import CentralView  # local import: keeps the F1/F5 tests pacer-free
    from studio.playback_state import PlaybackState

    w = CentralView.__new__(CentralView)
    rec = _Recorder()
    w._playback = PlaybackState()  # followed_lap starts None
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
    from studio.central_view import CentralView
    from studio.playback_state import PlaybackState

    starts = [10.0, 80.000005, 150.000166, 220.000714]  # contiguous; non-ms-aligned boundaries
    sess = _bare_session_for_lap_at_time(starts, end=290.0, valid=[0, 1, 2, 3])
    sess.best_lap_id = lambda: 0

    # F7: _on_laps_selected (with its seek-into-lap nudge) is now owned by CentralView.
    w = CentralView.__new__(CentralView)
    w.session = sess
    w._playback = PlaybackState()  # F5: auto-follow cursor lives on the shared PlaybackState
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
        w._playback.followed_lap = None
        w._on_laps_selected([lid], seek=True)
        # The seeded follow lap is the clicked lap (not the previous one).
        assert w._playback.followed_lap == lid, (lid, w._playback.followed_lap)
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
    assert w._playback.followed_lap == 0
    assert rec.lap_sets[-1] == [0, 9] and rec.selected[-1] == [0, 9]
    # Same lap again (another tick) → NO new switch (edge only).
    n_before = len(rec.lap_sets)
    w._follow_current_lap(0, t=11.0)
    assert len(rec.lap_sets) == n_before, "re-switched within the same lap (not an edge)"
    # Cross into lap 1 → switch to [1, 9].
    w._follow_current_lap(1, t=80.0)
    assert w._playback.followed_lap == 1 and rec.lap_sets[-1] == [1, 9]
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
    assert w._playback.followed_lap == 5
    n = len(rec.lap_sets)
    # None region: hold — no set_laps, followed unchanged.
    w._follow_current_lap(None, t=400.0)
    assert w._playback.followed_lap == 5 and len(rec.lap_sets) == n, "blanked/changed in a None region"
    # Next valid lap is picked up on the edge out of the None region.
    w._follow_current_lap(7, t=500.0)
    assert w._playback.followed_lap == 7 and rec.lap_sets[-1] == [7, 9]
    print("test_follow_holds_last_lap_on_none_region OK")


def test_follow_current_is_best_shows_single_lap():
    """When the current lap IS the best lap, the charts show just [best] (no duplicate overlay),
    and a drag in progress re-places the cursor in the followed lap (resolves the off-lap caveat)."""
    w, rec = _follow_window(best_lap=9)
    rec._dragging = True
    w._follow_current_lap(9, t=1100.0)
    assert w._playback.followed_lap == 9 and rec.lap_sets[-1] == [9], rec.lap_sets[-1]
    assert rec.placed == [1100.0], "cursor not re-placed mid-drag after the follow switch"
    print("test_follow_current_is_best_shows_single_lap OK")


class _StubConsistency:
    """A stand-in ConsistencyPanel that records refresh()/setVisible() + reports isVisible(), so the
    F6 default-hidden + View-toggle handler can be tested without the pyqtgraph panel build."""
    def __init__(self):
        self._visible = True   # built shown like the real widget; _build_ui hides it per the flag
        self.refreshed = 0

    def refresh(self):
        self.refreshed += 1

    def setVisible(self, on):
        self._visible = bool(on)

    def isVisible(self):
        return self._visible


def test_consistency_panel_hidden_by_default_and_toggle_refreshes():
    """F6: the consistency strip is HIDDEN by default and the View toggle shows it (refreshing its
    stats) / hides it again. F7: the show/hide + stats-refresh body moved onto CentralView
    (set_consistency_visible / _apply_consistency_visible); the persistent View toggle on the window
    just records the choice on the window and delegates here. This drives the view method directly:
    the build-time default-hide (refresh=False) then the toggle (refresh iff showing)."""
    from studio.central_view import CentralView

    w = CentralView.__new__(CentralView)
    # The window default (passed into the view at construction): the panel is hidden until the View
    # toggle turns it on.
    w._consistency_visible = False
    panel = _StubConsistency()
    w.consistency = panel
    # __init__ applies the flag to the freshly-built (shown) panel via _apply_consistency_visible:
    w._apply_consistency_visible(refresh=False)
    assert panel.isVisible() is False, "consistency panel must be hidden by default"
    assert panel.refreshed == 0

    # Toggle ON via the real (moved) handler: it refreshes the stats then shows the panel.
    w.set_consistency_visible(True)
    assert w._consistency_visible is True
    assert panel.isVisible() is True
    assert panel.refreshed == 1, "showing must refresh the stats"

    # Toggle OFF: hidden again, no extra refresh.
    w.set_consistency_visible(False)
    assert w._consistency_visible is False
    assert panel.isVisible() is False
    assert panel.refreshed == 1
    print("test_consistency_panel_hidden_by_default_and_toggle_refreshes OK")


# ----------------------------------------------------- F3: the single rebuild-derived-views seam
class _ViewSpy:
    """A stand-in for a derived-view collaborator (table / corner_table / map / consistency /
    plots) that records which of its refresh methods were invoked, so we can assert the rebuild
    seam touches the UNION of views — without building any real pyqtgraph/Qt panels."""

    def __init__(self):
        self.calls = []  # ordered method-name log, e.g. ["refresh", "set_corners", ...]

    def __getattr__(self, name):
        # Any method the seam calls on a view is recorded and treated as a harmless no-op. Guard
        # the dunder/private lookups so the recorder itself stays well-behaved.
        if name.startswith("_"):
            raise AttributeError(name)

        def _rec(*_a, **_k):
            self.calls.append(name)
        return _rec


def _rebuild_window(comparing=False):
    """A CentralView built WITHOUT __init__ (no Qt/pacer), with every derived-view collaborator
    replaced by a _ViewSpy and the two leaf refresh helpers (_refresh_driving_channels /
    _refresh_sector_lines) + _select_default replaced by call counters, so the SHARED rebuild seam
    (rebuild_derived_views) runs UNCHANGED on top of these spies.

    F7: the rebuild seam + the derived-view panels moved off StudioWindow onto CentralView (which
    now owns them); the seam body is byte-identical. _apply_reference_change still lives on the
    WINDOW (a persistent Coaching-menu action) but now delegates the refresh to self.view's seam — so
    the two reference tests below wrap the returned spied view in a StudioWindow via _ref_window."""
    from studio.central_view import CentralView

    w = CentralView.__new__(CentralView)
    w.table = _ViewSpy()
    w.corner_table = _ViewSpy()
    w.map = _ViewSpy()
    w.consistency = _ViewSpy()
    w.plots = _ViewSpy()

    # _comparing() reads self.compare; mimic its on/off via the real predicate's contract.
    w.compare = SimpleNamespace(active=comparing)

    # session.corners.corner_map_markers is the one session read the seam makes directly
    # (set_corners arg); stub the corners service so no pacer is needed.
    w.session = SimpleNamespace(corners=SimpleNamespace(corner_map_markers=lambda: []))

    # Replace the two leaf helpers + the selection step with counters so we can assert each was
    # invoked exactly through the seam (the real bodies push to plots/map and are tested elsewhere).
    rec = SimpleNamespace(driving=0, sector=0, select=0, update_ref=0)

    def _driving():
        rec.driving += 1

    def _sector(mode=None):  # noqa: ARG001 — matches the real signature
        rec.sector += 1

    def _select():
        rec.select += 1

    w._refresh_driving_channels = _driving
    w._refresh_sector_lines = _sector
    w._select_default = _select
    return w, rec


def _ref_window(comparing=False):
    """A StudioWindow built WITHOUT __init__ whose `.view` is a spied CentralView (from
    _rebuild_window), so the WINDOW's _apply_reference_change — which delegates the derived-view
    refresh to self.view.rebuild_derived_views and ends in self._update_reference_status — runs
    unchanged. _update_reference_status is replaced by a counter (its real body drives the
    persistent menu + chip, tested via the smoke harness)."""
    from studio.app import StudioWindow

    view, rec = _rebuild_window(comparing=comparing)
    w = StudioWindow.__new__(StudioWindow)
    w.view = view
    # has_reference()/reference_*() back the real _update_reference_status; the window keeps `session`.
    w.session = SimpleNamespace(
        has_reference=lambda: False,
        reference_session=lambda: None,
    )

    def _update_ref():
        rec.update_ref += 1

    w._update_reference_status = _update_ref
    return w, rec


def test_rebuild_derived_views_refreshes_the_union_of_views():
    """rebuild_derived_views(reselect=True) must refresh the FULL union of session-derived views:
    table.refresh, map.refresh_overlays, map.set_corners, corner_table.refresh,
    consistency.refresh, the driving-channel refresh, the default re-selection and the sector-line
    refresh — the single seam every refresh path now routes through."""
    w, rec = _rebuild_window(comparing=False)

    w.rebuild_derived_views(reselect=True)

    assert "refresh" in w.table.calls, "table not refreshed"
    assert "refresh_overlays" in w.map.calls, "map overlays not refreshed"
    assert "set_corners" in w.map.calls, "map corners not re-pushed"
    assert "refresh" in w.corner_table.calls, "corner table not refreshed"
    assert "refresh" in w.consistency.calls, "consistency strip not refreshed"
    assert rec.driving == 1, "driving channels not refreshed"
    assert rec.sector == 1, "sector lines not refreshed"
    # reselect=True picks the default selection and does NOT redraw the (absent) compare overlay.
    assert rec.select == 1, "default selection not made"
    assert "refresh" not in w.plots.calls, "plots.refresh ran despite reselect=True"

    # Ordering invariants that matter: map overlays + corners before the corner consumers, and the
    # sector lines after the selection (they re-derive against the now-current selection/axis).
    mc = w.map.calls
    assert mc.index("refresh_overlays") < mc.index("set_corners"), mc
    print("test_rebuild_derived_views_refreshes_the_union_of_views OK")


def test_rebuild_derived_views_compare_branch_refreshes_plots_not_reselect():
    """reselect=False (compare mode) must refresh the pinned [A,B] overlay via plots.refresh()
    instead of re-selecting the two fastest laps (which would tear the comparison down) — while
    still refreshing every other derived view."""
    w, rec = _rebuild_window(comparing=True)

    w.rebuild_derived_views(reselect=False)

    assert rec.select == 0, "re-selected in compare mode (would collapse the comparison)"
    assert "refresh" in w.plots.calls, "compare overlay (plots.refresh) not refreshed"
    # The rest of the union is still refreshed regardless of the selection branch.
    assert "refresh" in w.table.calls and "set_corners" in w.map.calls
    assert "refresh" in w.consistency.calls and rec.driving == 1 and rec.sector == 1
    print("test_rebuild_derived_views_compare_branch_refreshes_plots_not_reselect OK")


def test_apply_reference_change_now_refreshes_corners_and_driving_channels():
    """THE FIX: _apply_reference_change previously OMITTED map.set_corners() and
    _refresh_driving_channels(), so a reference load/clear left the per-corner map markers and the
    brake/coast glyphs stale even though the reference changes the per-corner Δ baseline. Routing
    it through rebuild_derived_views means both are now invoked — proven here on the spied view."""
    w, rec = _ref_window(comparing=False)

    w._apply_reference_change()

    # The drift fix: these two were NOT called by the old reference path; they must be now.
    assert "set_corners" in w.view.map.calls, "FIX REGRESSED: reference path skips map.set_corners"
    assert rec.driving == 1, "FIX REGRESSED: reference path skips _refresh_driving_channels"
    # And it still does everything the old path did (plus updates the reference status chip last).
    assert "refresh" in w.view.table.calls and "refresh_overlays" in w.view.map.calls
    assert "refresh" in w.view.corner_table.calls and "refresh" in w.view.consistency.calls
    assert rec.select == 1 and rec.sector == 1
    assert rec.update_ref == 1, "_update_reference_status not called after the rebuild"
    print("test_apply_reference_change_now_refreshes_corners_and_driving_channels OK")


def test_apply_reference_change_keeps_pinned_pair_while_comparing():
    """While comparing, a reference change must refresh the pinned [A,B] overlay (plots.refresh),
    NOT re-select — reselect is gated on not self.view._comparing()."""
    w, rec = _ref_window(comparing=True)

    w._apply_reference_change()

    assert rec.select == 0, "reference change re-selected while comparing"
    assert "refresh" in w.view.plots.calls, "compare overlay not refreshed on reference change"
    # The drift fix still holds in the compare branch.
    assert "set_corners" in w.view.map.calls and rec.driving == 1
    assert rec.update_ref == 1
    print("test_apply_reference_change_keeps_pinned_pair_while_comparing OK")


# ----------------------------------------------------- F7: atomic CentralView swap
class _FakeView:
    """A stand-in CentralView for the swap test: records dispose() (the pre-swap teardown) and
    carries a `video` so _video_do has something to resolve to. No Qt/pacer — a plain object the
    fake setCentralWidget below just stores."""
    n_built = 0

    def __init__(self, *_a, **_k):
        type(self).n_built += 1
        self.disposed = False
        self.video = SimpleNamespace(name=f"video#{type(self).n_built}")

    def dispose(self):
        self.disposed = True


def test_build_ui_atomic_swap_disposes_old_reuses_timer_and_rechromes():
    """F7: _build_ui must, on a reload, (1) DISPOSE the outgoing view before building the new one,
    (2) install a FRESH CentralView as self.view via setCentralWidget, (3) create the ~30 Hz tick
    timer ONCE and REUSE it across the swap (never a second timer), and (4) leave the window chrome
    (_video_do) resolving to the NEW view's video. Driven on a StudioWindow.__new__'d window with
    CentralView + the chrome-seed helpers stubbed, so no pacer / real panels / event loop are
    needed — only the swap MECHANICS are under test."""
    from PySide6.QtWidgets import QMainWindow

    import studio.app as appmod
    from studio.app import StudioWindow

    # QMainWindow.__init__ is needed so QTimer(self) (parented to the window) constructs; we still
    # SKIP StudioWindow.__init__ (no real load / menus / shortcuts).
    w = StudioWindow.__new__(StudioWindow)
    QMainWindow.__init__(w)
    w._tick_timer = None
    w.session = object()
    w._paths = ["/x/REC.MP4"]
    w._sidecar_path = None
    w._consistency_visible = False
    # Stub the chrome-seed methods _build_ui calls after the swap (they touch the real menu / status
    # bar, irrelevant to the swap mechanics).
    w._sync_full_recording_action = lambda: None
    w._update_reference_status = lambda: None
    # Record setCentralWidget so we can assert the NEW view is what gets installed each build.
    installed = []
    w.setCentralWidget = lambda widget: installed.append(widget)

    # Swap the real CentralView for the fake, and count QTimer constructions so we can prove the
    # timer is built exactly once across both builds.
    orig_view_cls, orig_timer_cls = appmod.CentralView, appmod.QTimer
    timers_made = []

    class _CountingTimer(orig_timer_cls):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            timers_made.append(self)

    appmod.CentralView = _FakeView
    appmod.QTimer = _CountingTimer
    _FakeView.n_built = 0
    try:
        # FIRST build: no old view to dispose; a new view installed; the timer created once + started.
        w._build_ui()
        v1 = w.view
        assert isinstance(v1, _FakeView) and installed[-1] is v1, "first view not installed"
        assert _FakeView.n_built == 1
        assert len(timers_made) == 1 and w._tick_timer is timers_made[0], "timer not created once"
        assert w._tick_timer.isActive(), "tick timer not started"
        t1 = w._tick_timer
        # _video_do resolves to v1's video.
        seen = {}
        w._video_do(lambda vid: seen.__setitem__("v", vid))
        assert seen["v"] is v1.video, "_video_do did not resolve to the first view's video"

        # RELOAD: the old view is disposed BEFORE the new is built, a fresh view is installed, and
        # the SAME timer is reused (no second QTimer).
        w._build_ui()
        v2 = w.view
        assert v2 is not v1, "reload reused the same view object"
        assert v1.disposed, "old view NOT disposed before the swap"
        assert not v2.disposed, "new view was disposed"
        assert installed[-1] is v2 and _FakeView.n_built == 2, "new view not installed"
        assert len(timers_made) == 1 and w._tick_timer is t1, "tick timer was REPLACED on reload"
        # _video_do now resolves to v2's video (the new view) — the chrome re-homed atomically.
        seen.clear()
        w._video_do(lambda vid: seen.__setitem__("v", vid))
        assert seen["v"] is v2.video, "_video_do still resolves to the disposed view's video"
    finally:
        appmod.CentralView, appmod.QTimer = orig_view_cls, orig_timer_cls
    print("test_build_ui_atomic_swap_disposes_old_reuses_timer_and_rechromes OK")


# F7: _tick on the window is a thin delegate to the live view's tick() — assert the delegation +
# the defensive no-view guard, without a real view.
def test_tick_delegates_to_live_view_and_guards_no_view():
    """The persistent window timer's slot (_tick) must delegate to self.view.tick() and be a safe
    no-op before the first successful load (no view yet)."""
    from studio.app import StudioWindow

    w = StudioWindow.__new__(StudioWindow)
    # No view yet (failed/absent first load): _tick must not raise.
    w._tick()  # no exception == pass
    # With a view, it delegates exactly once per call.
    calls = {"n": 0}
    w.view = SimpleNamespace(tick=lambda: calls.__setitem__("n", calls["n"] + 1))
    w._tick()
    w._tick()
    assert calls["n"] == 2, calls
    print("test_tick_delegates_to_live_view_and_guards_no_view OK")


# ---------------------------------------------------- B3: welcome empty state + drag-and-drop
class _FakeDropEvent:
    """The drag/drop event surface StudioWindow's handlers touch: mimeData() + acceptProposedAction()."""

    def __init__(self, mime):
        self._mime = mime
        self.accepted = False

    def mimeData(self):
        return self._mime

    def acceptProposedAction(self):
        self.accepted = True


def test_welcome_state_when_no_recording():
    """Launched with no path, StudioWindow shows the welcome empty state (not a blank/auto-demo
    window); its "Open demo" button loads a RESOLVED demo recording through the guarded _load
    path. It must NOT fall back to the lapless bundled sample when the demo can't be resolved —
    that produced a blank-looking studio (the honest-first-run-UX fix)."""
    from PySide6.QtWidgets import QLabel

    from studio import app as app_mod
    from studio.app import StudioWindow, _WelcomeView
    w = StudioWindow([])
    try:
        cw = w.centralWidget()
        assert isinstance(cw, _WelcomeView), type(cw)
        assert cw.open_btn is not None and cw.demo_btn is not None
        loaded = []
        w._load = lambda paths: loaded.append(list(paths))

        # Demo RESOLVES: "Open demo" loads exactly that path through the guarded _load.
        app_mod.demo.resolve_demo_recording = lambda: "/demo/pacer-demo-lap.mp4"
        cw.demo_btn.click()
        assert loaded == [["/demo/pacer-demo-lap.mp4"]], loaded

        # Demo UNAVAILABLE (offline / download failed): no load, and the welcome state is re-shown
        # with an honest message — NOT a silent fall back to the lapless bundled sample.
        loaded.clear()
        app_mod.demo.resolve_demo_recording = lambda: None
        w._open_demo()
        assert loaded == [], "must not load anything when the demo can't be resolved"
        cw2 = w.centralWidget()
        assert isinstance(cw2, _WelcomeView), type(cw2)
        err = [lab for lab in cw2.findChildren(QLabel)
               if lab.property("role") == "WelcomeError"]
        assert err and "unavailable" in err[0].text().lower(), "honest demo-unavailable message"
    finally:
        w.deleteLater()
    print("test_welcome_state_when_no_recording OK")


def test_drag_and_drop_loads_mp4s():
    """Dropping GoPro MP4s on the window loads them (sorted, so chapter siblings chain in order);
    a drag with no MP4 is not accepted."""
    from PySide6.QtCore import QMimeData, QUrl

    from studio.app import StudioWindow
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile("/x/GX020060.MP4"),
                  QUrl.fromLocalFile("/x/GX010060.MP4"),
                  QUrl.fromLocalFile("/x/notes.txt")])
    # The pure extractor: only .mp4, sorted; no URLs -> [].
    assert StudioWindow._dropped_mp4s(mime) == ["/x/GX010060.MP4", "/x/GX020060.MP4"]
    assert StudioWindow._dropped_mp4s(QMimeData()) == []

    w = StudioWindow([])
    try:
        loaded = []
        w._load = lambda paths: loaded.append(list(paths))
        enter = _FakeDropEvent(mime)
        w.dragEnterEvent(enter)
        assert enter.accepted, "an MP4 drag should be accepted"
        drop = _FakeDropEvent(mime)
        w.dropEvent(drop)
        assert drop.accepted and loaded == [["/x/GX010060.MP4", "/x/GX020060.MP4"]], loaded
        # A drag without any MP4 is ignored (not accepted, no load).
        txt = QMimeData()
        txt.setUrls([QUrl.fromLocalFile("/x/a.txt")])
        ignored = _FakeDropEvent(txt)
        w.dragEnterEvent(ignored)
        assert not ignored.accepted
    finally:
        w.deleteLater()
    print("test_drag_and_drop_loads_mp4s OK")


# ------------------------------------------------ C1: async (off-UI-thread) Session.load
def _pump_until(pred, secs=30.0):
    """Bounded event-loop pump (no raw sleeps held): processEvents until `pred()` or the deadline.
    Returns whether the predicate became true — the pattern the real-widget tests use to wait for an
    async (worker-QThread) completion."""
    import time
    end = time.time() + secs
    while not pred() and time.time() < end:
        _APP.processEvents()
        time.sleep(0.01)
    return pred()


def test_async_load_settles_off_ui_thread():
    """C1: Session.load runs on a worker QThread, so the window is responsive during it and the
    session/view aren't ready synchronously after __init__. Construct on the bundled sample, pump the
    loop until the load settles, then assert the session + central view are wired up."""
    os.environ.setdefault("PACER_NO_MEDIA", "1")
    from PySide6.QtWidgets import QMessageBox

    from studio.app import StudioWindow
    # A load failure must surface as a fast assertion, never the modal QMessageBox.critical that
    # spins a nested event loop and blocks the headless suite forever (the original C1 CI hang).
    orig_critical = QMessageBox.critical
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)
    w = StudioWindow([_BUNDLED_SAMPLE])
    try:
        assert _pump_until(lambda: w.view is not None), "async load never completed"
        assert getattr(w, "session", None) is not None
        assert w.session.point_count() > 0, w.session.point_count()
        assert w.view is not None
        assert list(w._paths) == [_BUNDLED_SAMPLE], w._paths
    finally:
        w._drain_load_workers()  # finish any in-flight worker WITHOUT a GIL-deadlocking wait()
        w.close()
        w.deleteLater()
        QMessageBox.critical = orig_critical
    print("test_async_load_settles_off_ui_thread OK")


def test_reentrant_load_applies_only_latest():
    """C1 reentrancy/supersede: two quick _load calls in a row must apply ONLY the latest result
    (the earlier worker's result is dropped by token), with no crash and a single coherent session.
    Both _load calls target the same sample here; the token guard is what makes the second supersede
    the first regardless of which worker finishes first."""
    os.environ.setdefault("PACER_NO_MEDIA", "1")
    from PySide6.QtWidgets import QMessageBox

    from studio.app import StudioWindow
    orig_critical = QMessageBox.critical  # never block the headless suite on a load-failure dialog
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)
    w = StudioWindow([])  # welcome state, no in-flight load
    try:
        applied_tokens = []
        _orig = w._on_session_loaded

        def _spy(token, paths, session):
            # Record only tokens that pass the staleness guard (i.e. actually APPLIED), by replaying
            # the guard before delegating — a stale (superseded) result must not be applied.
            if token == w._load_token:
                applied_tokens.append(token)
            _orig(token, paths, session)
        w._on_session_loaded = _spy

        w._load([_BUNDLED_SAMPLE])
        first_token = w._load_token
        w._load([_BUNDLED_SAMPLE])  # supersede before the first worker finishes
        latest_token = w._load_token
        assert latest_token == first_token + 1, (first_token, latest_token)

        assert _pump_until(lambda: w.view is not None), "no load settled"
        # Only the LATEST token may have been applied; the stale (first) worker's result was dropped
        # by the token guard. No crash, one coherent session.
        assert applied_tokens == [latest_token], applied_tokens
        assert first_token not in applied_tokens, applied_tokens
        assert getattr(w, "session", None) is not None
        assert w.view is not None
    finally:
        w._drain_load_workers()  # drain BOTH workers (incl. the superseded one) before teardown
        w.close()
        w.deleteLater()
        QMessageBox.critical = orig_critical
    print("test_reentrant_load_applies_only_latest OK")


if __name__ == "__main__":
    # Hang watchdog (permanent CI hardening): this file is full of Qt/event-loop tests, where a
    # regression can hang the whole run, and ctest buffers a test's stdout/stderr until it ENDS —
    # so a bare 20-min job cancel surfaces nothing. Dump every thread's stack at 180 s (clear of the
    # ~110 s legit test_export_video, so zero noise on healthy runs); the dump lands in the buffer
    # that `ctest --timeout` flushes when it kills the hang, pinpointing the stuck test + thread.
    # exit=False: a pure no-op on the fast path. (This is what root-caused the original C1 hang.)
    faulthandler.enable()
    faulthandler.dump_traceback_later(180, repeat=True, exit=False)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"START {t.__name__}", flush=True)  # breadcrumb: the last START before a hang names it
        t()
        print(f"ok  {t.__name__}", flush=True)
    print(f"\nALL {len(tests)} STUDIO FEATURE TESTS PASSED", flush=True)
