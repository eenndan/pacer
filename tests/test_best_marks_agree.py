"""ONE "SESSION BEST" RULE, ON EVERY SURFACE THAT MARKS ONE.

Three surfaces put the SAME ★ on the SAME quantity and answered "is this the best?" three
different ways. Nothing failed; the app simply marked different cells on different pages.

  * THE SPLIT ★. `stats.SplitMatrix.is_best` compares what the reader is SHOWN — both values
    rounded to `MATRIX_DECIMALS` — because a sector split is a difference of two GPS sample
    times on a ~0.0998 s grid and its column minimum is routinely tied at print. The Laps tab
    printed the same split to the same 2 dp and compared the raw doubles at 1e-9.
    MEASURED on the owner's D24 0062 (65 valid laps): with five sector lines the Stats grid
    stars 18 cells and the Laps tab stars 13 — five cells whose printed text is IDENTICAL to a
    starred neighbour's wear the mark on one page and nothing on the other (S4 prints 11.40 on
    laps 20, 42, 43, 46 and 51; only lap 51's double is 11.399). With three lines it is 8 vs 6.
    On the 0060 pair the two agree at 1, 3 and 5 lines — one recording alone would have
    "proved" the exact compare safe.

  * THE CORNER ★. The Corners page marks a session-best corner time the same exact-float way
    and prints it to the same 2 dp. MEASURED on 0062: C1's best is 2.7478 and laps 34, 42 and
    51 all print 2.75, but only lap 34 is starred; C6's best is 5.5595 and laps 44 and 53 both
    print 5.56, only 53 starred. The page shows one lap at a time, so the contradiction is
    served to the reader as they step through laps.

  * THE EXPORTED REPORT'S BEST-LAP CUE. On PROVISIONAL timing (an auto-fitted, unconfirmed
    start line — the state every unknown circuit loads in) the app withholds the best-lap
    authority everywhere: `lap_table._apply_highlights` drops the green row AND the ★,
    `stats_panel._refresh_lap_table` drops the ★, and the share card refuses to render at all.
    `export_data.write_report_html` painted `class="best"` on that row and named the lap in its
    meta table, with a comment claiming it read "like the app's table". Three surfaces withhold
    it, one asserts it.

Pure Qt / pure Python on fake sessions (no pacer, no telemetry file).
Run: python tests/test_best_marks_agree.py
"""
import os
import sys
from types import SimpleNamespace

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _qtapp import themed_app  # noqa: E402

_APP = themed_app()

from studio import data_quality  # noqa: E402
from studio import stats as stats_service  # noqa: E402
from studio.lap_table import (  # noqa: E402
    BEST_SECTOR_MARK,
    COLUMNS,
    LAP_ROLE,
    CornerTable,
    LapTable,
)

# Two laps tie at print in S1 and two more in S2, each pair split by a thousandth — the shape the
# 10 Hz split grid produces on a real recording (see the module docstring's D24 numbers). Five
# laps is stats.MATRIX_MIN_LAPS, the fewest the Stats grid will render.
_SPLITS = {
    0: [33.800, 36.200],
    1: [33.801, 36.500],
    2: [34.200, 36.201],
    3: [34.500, 36.900],
    4: [34.900, 37.000],
}


class _FakeSplitSession:
    """The read surface LapTable.refresh() touches, with one sector line -> 2 S-columns."""

    timing_verified = True
    timing_quality = data_quality.TimingQuality()

    def lap_rows(self):
        return [{"idx": i, "time": 70.0 + i, "dist": 1000.0, "entry": 50.0}
                for i in sorted(_SPLITS)]

    def sector_count(self):
        return 1

    def effective_sector_count(self):
        return 1

    def consistency_lap_ids(self):
        return sorted(_SPLITS)

    def lap_sector_splits(self, lap_id):
        return list(_SPLITS[lap_id])

    def session_best_splits(self):
        n = len(_SPLITS[0])
        return [min(sp[i] for sp in _SPLITS.values()) for i in range(n)]

    def theoretical_best(self):
        return 68.2

    def best_rolling_lap(self):
        return 68.3

    def best_lap_id(self):
        return 0

    def dropout_lap_ids(self):
        return set()


def _table_star_cells(table, n_splits):
    """{(lap_id, column)} for every S-split cell the Laps tab has marked ★."""
    tb = table.table
    out = set()
    for r in range(tb.rowCount()):
        lap_id = int(tb.item(r, 0).data(LAP_ROLE))
        for i in range(n_splits):
            item = tb.item(r, len(COLUMNS) + i)
            if item is not None and item.text().endswith(BEST_SECTOR_MARK.strip()):
                out.add((lap_id, i))
    return out


def test_the_laps_tab_and_the_stats_grid_star_the_same_split_cells():
    """The two surfaces render the same laps x sectors grid of the same numbers. Whatever the
    tie rule is, it has to be ONE rule — a cell printed 11.40 beside a starred 11.40 either
    earns the mark on both pages or on neither."""
    sess = _FakeSplitSession()
    ids = sess.consistency_lap_ids()
    matrix = stats_service.split_matrix(
        ids, [sess.lap_sector_splits(i) for i in ids],
        columns=sess.effective_sector_count() + 1)
    assert matrix is not None
    grid = {(matrix.lap_ids[r], c)
            for r in range(len(matrix.lap_ids))
            for c in range(matrix.columns)
            if matrix.is_best(r, c)}
    # The fixture is only meaningful if it actually contains ties at print.
    assert len(grid) > matrix.columns, grid

    table = LapTable(sess)
    table.resize(900, 320)
    table.show()
    _APP.processEvents()
    table.refresh()
    _APP.processEvents()
    tab = _table_star_cells(table, 2)

    assert tab == grid, f"Laps tab starred {sorted(tab)}, Stats grid starred {sorted(grid)}"
    # ...and every starred cell's printed text really is the column's printed best, so the
    # assertion above cannot be satisfied by marking nothing.
    for lap_id, c in sorted(grid):
        shown = f"{_SPLITS[lap_id][c]:.2f}"
        best = f"{min(sp[c] for sp in _SPLITS.values()):.2f}"
        assert shown == best, (lap_id, c, shown, best)
    print("test_the_laps_tab_and_the_stats_grid_star_the_same_split_cells OK")


# Three laps whose C1 time prints 2.75 but whose doubles differ in the third decimal — the C1
# shape measured on D24 0062, where the best is 2.7478 and two other laps print the same 2.75.
_CORNER_TIMES = {0: 2.7478, 1: 2.7503, 2: 2.7461, 3: 2.9}


class _FakeCornerSession:
    """One corner, four laps; the session best ties at print with two other laps."""

    def __init__(self):
        self._cl = [SimpleNamespace(label="C1", direction=1, cid=0)]

        def stats_for(lap):
            t = _CORNER_TIMES.get(lap)
            if t is None:
                return []
            return [SimpleNamespace(time=t, delta=t - min(_CORNER_TIMES.values()),
                                    apex_speed=44.9, apex_speed_delta=0.4,
                                    entry_speed=45.7, exit_speed=47.9)]

        self.corners = SimpleNamespace(
            corner_list=lambda: self._cl,
            lap_corner_stats=stats_for,
            corner_session_bests=lambda: [min(_CORNER_TIMES.values())])
        self.driving = SimpleNamespace(lap_corner_grip=lambda lap: [0.77])

    def lap_count(self):
        return len(_CORNER_TIMES)

    def has_reference(self):
        return False

    def reference_label(self):
        return None


def test_a_corner_time_printed_as_the_best_is_marked_as_the_best():
    """The Corners page shows ONE lap, so the reader meets this defect by stepping through laps:
    2.75 wears the ★ on lap 0 and not on lap 2, with nothing on either page to tell them apart."""
    sess = _FakeCornerSession()
    table = CornerTable(sess)
    table.resize(700, 320)
    table.show()
    best = min(_CORNER_TIMES.values())
    marked = []
    for lap, t in sorted(_CORNER_TIMES.items()):
        table.set_lap(lap)
        _APP.processEvents()
        text = table.table.item(0, 1).text()
        assert text.startswith(f"{t:.2f}"), (lap, text)
        if text.endswith(BEST_SECTOR_MARK.strip()):
            marked.append(lap)
    ties = [lap for lap, t in _CORNER_TIMES.items() if f"{t:.2f}" == f"{best:.2f}"]
    assert len(ties) > 1, ties  # the fixture must actually tie at print
    assert sorted(marked) == sorted(ties), f"starred {marked}, printed as the best {ties}"
    print("test_a_corner_time_printed_as_the_best_is_marked_as_the_best OK")


class _FakeExcludedSession:
    """A session whose start/finish line mis-segments: some laps are valid, some are banded out,
    and some crossings never cleared the coarse gate at all. Modelled on the D24 0060 pair with
    the line dragged 15 % round the lap — 81 crossings found, 33 valid, 36 excluded."""

    timing_verified = True
    timing_quality = data_quality.TimingQuality()

    N_FOUND, N_VALID, N_EXCLUDED = 81, 33, 36

    def lap_rows(self):
        return [{"idx": i, "time": 70.0, "dist": 1000.0, "entry": 50.0}
                for i in range(self.N_VALID)]

    def excluded_lap_rows(self):
        return [{"idx": self.N_VALID + i, "time": 59.0, "dist": 536.0, "entry": 40.0}
                for i in range(self.N_EXCLUDED)]

    def excluded_lap_ids(self):
        return [self.N_VALID + i for i in range(self.N_EXCLUDED)]

    def valid_lap_ids(self):
        return list(range(self.N_VALID))

    def lap_count(self):
        return self.N_FOUND

    def sector_count(self):
        return 0

    def lap_sector_splits(self, lap_id):
        return []

    def session_best_splits(self):
        return []

    def theoretical_best(self):
        return None

    def best_rolling_lap(self):
        return None

    def best_lap_id(self):
        return 0

    def dropout_lap_ids(self):
        return set()


def test_both_surfaces_count_the_excluded_laps_out_of_the_same_total():
    """The Laps strip and the Stats DATA TRUST card state the same fact about the same
    segmentation. The strip divided by valid+excluded and the card by the laps the segmenter
    FOUND, so on every D24 start-line placement that produced excluded laps the two printed
    different totals — 69 against 81 at the worst, on one recording, at the same instant."""
    sess = _FakeExcludedSession()
    table = LapTable(sess)
    table.resize(900, 320)
    table.show()
    table.refresh()
    _APP.processEvents()
    headline = table._excluded_header.text()
    assert str(sess.N_EXCLUDED) in headline, headline
    # The card's denominator, verbatim from stats_panel._refresh_trust.
    assert str(sess.N_FOUND) in headline, (
        f"the Laps strip says {headline!r}; the DATA TRUST card says "
        f"'{sess.N_VALID} of the {sess.N_FOUND} laps found'")
    assert str(sess.N_VALID + sess.N_EXCLUDED) not in headline, headline
    print("test_both_surfaces_count_the_excluded_laps_out_of_the_same_total OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} BEST-MARK AGREEMENT TESTS PASSED", flush=True)
