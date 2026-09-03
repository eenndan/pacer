"""Lap-panel AFFORDANCES: the keyboard route to sorting, the ★ legend, the Corners click target
and the Corners units (QA U9-02 · IA-07 · L3-07 · L3-10).

Four ways the panel knew something and never said it:

  * sorting was mouse-only. The horizontal header is `Qt.NoFocus`, so the focus ring never reached
    it, and QHeaderView has no key handling of its own: Space/Return/Enter/Right/Home left the
    indicator at (0, Ascending) even with focus forced onto the header, while a mouse press moved
    it in the same process. No menu action and no shortcut offered a way in either (U9-02);
  * the ★ was drawn with an EMPTY tooltip on every cell that carried it, in both tables, and
    spelled out in exactly one header tooltip — the mark meant "session best in this context" and
    the cell wearing it answered nothing (IA-07);
  * every Corners row is a click target that rings a corner on the map and, from a maximized lap
    panel, restores the 2x2 grid on the way (deliberate — the map needs pixels to paint the ring
    on). The panel collapsing 5.2x smaller was the FIRST feedback a click produced: no cursor
    change, no hover, nothing in the headers (L3-07);
  * seven of its eight columns are unit-bearing numbers and the abbreviated headers named not one
    unit, while the Stats page captions the same data on screen (L3-10).

Pure Qt on fake sessions (no pacer, no telemetry file), under the REAL theme — the focus ring and
the hover fill are pixels, and pixels need the app's own palette. Frames are compared as
Format_RGB32 RGB, never sha1(constBits()), whose scanline padding differs between two identical
grabs. Run:
    QT_QPA_PLATFORM=offscreen python tests/test_lap_table_affordances.py
"""
import os
import sys
from types import SimpleNamespace

import numpy as np

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QHoverEvent, QImage, QKeyEvent, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import data_quality, theme  # noqa: E402
from studio import lap_table as LT  # noqa: E402

theme.apply_theme(_APP)

_TIME_COL, _GRIP_COL = 1, len(LT.CORNER_COLUMNS) - 1
# The keyboard sort, top to bottom: the app's own default is column 0 ascending.
_START = (0, Qt.AscendingOrder)

_ALIVE: list = []      # see test_lap_table_empty_states: a collected LapTable tears down under Qt


def _keep(w):
    _ALIVE.append(w)
    return w


def _settle(n=4):
    for _ in range(n):
        _APP.processEvents()


def _rgb(w):
    """RGB planes of a widget grab. Format_RGB32 + drop alpha: the raw buffer carries scanline
    padding whose bytes differ between two pixel-identical grabs."""
    img = w.grab().toImage().convertToFormat(QImage.Format_RGB32)
    a = np.frombuffer(bytes(img.constBits()), np.uint8).reshape(img.height(), img.width(), 4)
    return a[..., :3]


class _FakeLapSession:
    """The LapTable read surface: three laps of different times, two sector lines (so there are
    S-split columns to carry a session-best ★), one GPS-dropout lap."""

    timing_verified = True
    timing_quality = data_quality.TimingQuality()

    def __init__(self, sectors=2, dropout=(0,)):
        self._sectors, self._dropout = sectors, set(dropout)
        self._times = [70.5, 68.4, 69.9]
        self._splits = [[35.0, 20.0, 15.5], [34.0, 19.4, 15.0], [34.6, 19.8, 15.5]]

    def lap_rows(self):
        return [{"idx": i, "time": t, "dist": 1060.0 + i, "entry": 61.0 + i}
                for i, t in enumerate(self._times)]

    def excluded_lap_rows(self):
        return []

    def lap_count(self):
        return len(self._times)

    def sector_count(self):
        return self._sectors

    def lap_sector_splits(self, lap_id):
        return self._splits[lap_id][:self._sectors + 1] if self._sectors else []

    def session_best_splits(self):
        return [min(s[i] for s in self._splits) for i in range(self._sectors + 1)] \
            if self._sectors else []

    def best_lap_id(self):
        return 1

    def dropout_lap_ids(self):
        return set(self._dropout)


def _stat(time=2.5, delta=0.11, apex_delta=-0.9):
    return SimpleNamespace(time=time, delta=delta, apex_speed=44.9, apex_speed_delta=apex_delta,
                           entry_speed=45.7, exit_speed=47.9)


class _FakeCornerSession:
    """The CornerTable read surface. Corner 1 holds the session best for its corner (the ★ cell);
    lap 1 is NOT the Δ baseline, so the Δ columns carry measurements."""

    def __init__(self, n=4, grip=0.77):
        cl = [SimpleNamespace(label=f"C{i + 1}", direction=1 if i % 2 else -1, cid=i)
              for i in range(n)]
        stats = [_stat(time=2.5 + i * 0.4) for i in range(n)]
        bests = [st.time for st in stats]
        bests[1] = stats[1].time - 0.5              # only corner 1 is a session best on this lap
        self.corners = SimpleNamespace(
            corner_list=lambda: cl,
            lap_corner_stats=lambda lap: stats if lap == 1 else [],
            corner_session_bests=lambda: bests)
        self.driving = SimpleNamespace(lap_corner_grip=lambda lap: [grip] * n)
        self._n = n

    def lap_count(self):
        return 3

    def valid_lap_ids(self):
        return [0, 1, 2]

    def best_lap_id(self):
        return 0

    def has_reference(self):
        return False


def _lap_table(**kw):
    """A shown LapTable inside a container, so the header is in a real focus chain."""
    box = _keep(QWidget())
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lt = LT.LapTable(_FakeLapSession(**kw))
    lay.addWidget(lt)
    box.resize(520, 400)
    box.show()
    _settle()
    return lt


def _corner_table(width=457, **kw):
    ct = _keep(LT.CornerTable(_FakeCornerSession(**kw)))
    ct.resize(width, 400)
    ct.show()
    ct.set_lap(1)
    _settle()
    return ct


def _hdr(lt):
    return lt.table.horizontalHeader()


def _lap_order(lt):
    return [lt._lap_id(r) for r in range(lt.table.rowCount())]


def _click_section(hdr, col):
    """A real mouse press+release on a header section — the route that DID work."""
    x = hdr.sectionViewportPosition(col) + hdr.sectionSize(col) // 2
    pos = QPointF(x, hdr.height() / 2)
    for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
        _APP.sendEvent(hdr.viewport(), QMouseEvent(kind, pos, Qt.LeftButton, Qt.LeftButton,
                                                   Qt.NoModifier))
    _settle(2)


def _key(w, key):
    _APP.sendEvent(w, QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))
    _APP.sendEvent(w, QKeyEvent(QEvent.KeyRelease, key, Qt.NoModifier))
    _settle(2)


def _hover(widget, pos):
    _APP.sendEvent(widget, QHoverEvent(QEvent.HoverEnter, QPointF(pos), QPointF(-1, -1)))
    _APP.sendEvent(widget, QHoverEvent(QEvent.HoverMove, QPointF(pos),
                                       QPointF(pos.x() + 1, pos.y())))
    _settle(3)


# ==================================================================== U9-02 keyboard sort
def test_the_sort_header_is_a_tab_stop_that_shows_where_it_is():
    """U9-02, half one: the keyboard has to be able to GET there, and see that it did. One Tab out
    of the read-only grid lands on the header (Qt's cell-wise tab navigation used to swallow every
    Tab press inside the table, so a focusable header alone would still have been unreachable), and
    arriving paints the app's focus ring on the section Space would sort by — not somewhere else."""
    lt = _lap_table()
    hdr = _hdr(lt)
    assert hdr.focusPolicy() != Qt.NoFocus, "the header is not focusable"

    lt.table.setFocus(Qt.TabFocusReason)
    _settle()
    assert _APP.focusWidget() is lt.table
    before = _rgb(hdr)
    _key(lt.table, Qt.Key_Tab)
    assert _APP.focusWidget() is hdr, f"Tab went to {_APP.focusWidget()}, not the header"

    changed = (before != _rgb(hdr)).any(-1)
    assert int(changed.sum()) > 0, "the focused header paints no cue"
    # ...and every changed pixel is inside the section that would be sorted.
    x0 = hdr.sectionViewportPosition(hdr.sortIndicatorSection())
    x1 = x0 + hdr.sectionSize(hdr.sortIndicatorSection())
    xs = np.nonzero(changed.any(0))[0]
    assert x0 <= xs.min() and xs.max() < x1, f"ring spans x {xs.min()}..{xs.max()}, section {x0}..{x1}"
    print(f"test_the_sort_header_is_a_tab_stop_that_shows_where_it_is OK "
          f"({int(changed.sum())} px in section {x0}..{x1})")


def test_space_and_return_sort_the_focused_column():
    """U9-02, half two: the keys actually SORT — the same rule a click follows (same column flips
    the direction, a new column starts ascending), and the rows really move. Home/End and ←/→ walk
    the sortable sections only: the blank trailing spacer holds no cells, so parking the ring there
    would offer a sort that cannot happen."""
    lt = _lap_table()
    hdr = _hdr(lt)
    assert (hdr.sortIndicatorSection(), hdr.sortIndicatorOrder()) == _START
    lap_order = _lap_order(lt)
    hdr.setFocus(Qt.TabFocusReason)
    _settle()

    _key(hdr, Qt.Key_Space)                       # same column -> flip
    assert (hdr.sortIndicatorSection(), hdr.sortIndicatorOrder()) == (0, Qt.DescendingOrder)
    assert _lap_order(lt) == lap_order[::-1]

    _key(hdr, Qt.Key_Right)
    _key(hdr, Qt.Key_Return)                      # new column -> ascending, by lap TIME
    assert (hdr.sortIndicatorSection(), hdr.sortIndicatorOrder()) == (_TIME_COL, Qt.AscendingOrder)
    assert _lap_order(lt) == [1, 2, 0], _lap_order(lt)   # 68.4 < 69.9 < 70.5

    # End walks to the last column that can order something — never the blank spacer.
    _key(hdr, Qt.Key_End)
    _key(hdr, Qt.Key_Space)
    assert hdr.sortIndicatorSection() == lt._n_real_cols() - 1 < lt.table.columnCount() - 1
    _key(hdr, Qt.Key_Home)
    _key(hdr, Qt.Key_Space)
    assert (hdr.sortIndicatorSection(), hdr.sortIndicatorOrder()) == _START
    assert _lap_order(lt) == lap_order
    print("test_space_and_return_sort_the_focused_column OK")


def test_space_is_taken_back_from_the_window_shortcut_only_while_focused():
    """Space is a WINDOW-level QShortcut (video play/pause), and shortcuts are matched BEFORE the
    key reaches the focused widget — so without claiming the ShortcutOverride the press would
    toggle the video instead of sorting. The claim is scoped to having the keyboard: with focus
    elsewhere the video keeps Space."""
    lt = _lap_table()
    hdr = _hdr(lt)

    def claimed(key):
        ev = QKeyEvent(QEvent.ShortcutOverride, key, Qt.NoModifier)
        ev.ignore()
        _APP.sendEvent(hdr, ev)
        return ev.isAccepted()

    lt.table.setFocus(Qt.TabFocusReason)
    _settle()
    assert not claimed(Qt.Key_Space), "the header claims Space without the keyboard"
    hdr.setFocus(Qt.TabFocusReason)
    _settle()
    assert claimed(Qt.Key_Space) and claimed(Qt.Key_Return)
    assert not claimed(Qt.Key_M), "the header must claim only the keys it acts on"
    print("test_space_is_taken_back_from_the_window_shortcut_only_while_focused OK")


def test_the_mouse_route_survives_the_replaced_header():
    """The header is a REPLACEMENT (QTableView builds its own, with `sectionsClickable` and
    `highlightSections` already on), and a replacement that forgets them silently removes the only
    sorting route the app HAD. Green on `main` by construction — it exists to go red if the
    replacement ever drops what it inherited. Both routes, one table, same result."""
    lt = _lap_table()
    hdr = _hdr(lt)
    assert hdr.sectionsClickable() and hdr.isSortIndicatorShown()
    _click_section(hdr, _TIME_COL)
    assert (hdr.sortIndicatorSection(), hdr.sortIndicatorOrder()) == (_TIME_COL, Qt.AscendingOrder)
    assert _lap_order(lt) == [1, 2, 0], _lap_order(lt)
    _click_section(hdr, _TIME_COL)
    assert hdr.sortIndicatorOrder() == Qt.DescendingOrder
    assert _lap_order(lt) == [0, 2, 1], _lap_order(lt)
    print("test_the_mouse_route_survives_the_replaced_header OK")


# ==================================================================== IA-07 the ★ legend
def test_every_star_carries_its_legend():
    """IA-07: one glyph, one convention — "the session best in this context" — stated wherever the
    mark appears. Not a second glyph: the sweep's "two different meanings" framing was refuted.
    The trust note a cell already carries (here: a GPS-dropout lap that is ALSO the best lap) is
    kept, because both answers are true."""
    def stars(lt):
        got = [(r, c, lt.table.item(r, c))
               for r in range(lt.table.rowCount()) for c in range(lt.table.columnCount())
               if lt.table.item(r, c) is not None and "★" in lt.table.item(r, c).text()]
        assert got, "no ★ in the table — this test would pass vacuously"
        return got

    lt = _lap_table()                              # best lap 1, dropout on lap 0: no other note
    starred = stars(lt)
    for r, c, item in starred:
        assert item.toolTip(), f"★ cell ({r},{c}) has no tooltip"
    assert LT.BEST_LAP_TIP in lt.table.item(lt._row_for_lap(1), 0).toolTip()
    split = next(it for _, c, it in starred if c >= len(LT.COLUMNS))
    assert LT.BEST_SPLIT_TIP in split.toolTip()

    # A lap can be the best one AND a GPS-dropout lap: both answers are true, so both are said.
    both = _lap_table(dropout=(1,))
    lap_cell = both.table.item(both._row_for_lap(1), 0)
    assert LT.BEST_LAP_TIP in lap_cell.toolTip()
    assert LT.DROPOUT_TOOLTIP in lap_cell.toolTip(), "the dropout warning was overwritten"
    # ...and the columns that can carry the mark name it in their header tooltip, the way the Lap
    # column already did (the ⟲/⟳ precedent).
    tips = [lt.table.horizontalHeaderItem(c).toolTip() for c in range(lt._n_real_cols())]
    assert "★" in tips[0] and all("★" in t for t in tips[len(LT.COLUMNS):])

    ct = _corner_table()
    star = next(ct.table.item(r, _TIME_COL) for r in range(ct.table.rowCount())
                if "★" in ct.table.item(r, _TIME_COL).text())
    assert star.toolTip() == LT.BEST_CORNER_TIP
    assert "★" in ct.table.horizontalHeaderItem(_TIME_COL).toolTip()
    print(f"test_every_star_carries_its_legend OK ({len(starred)} lap marks + 1 corner mark)")


# ==================================================================== L3-07 the click target
def test_the_corners_rows_declare_that_they_are_clickable():
    """L3-07: the click is real and deliberate (it rings the corner on the map, restoring the grid
    first so the map has pixels) — it just never announced itself. A pointing hand, the affordance
    the excluded strip already uses, plus a fill on the ROW: any of the eight cells does the same
    one thing, so a cell-wide cue would advertise the wrong target."""
    ct = _corner_table()
    assert ct.table.viewport().cursor().shape() == Qt.PointingHandCursor
    assert "lick" in ct.table.horizontalHeaderItem(0).toolTip(), "the header never mentions the click"

    row = 2
    rect = ct.table.visualRect(ct.table.model().index(row, 1))   # viewport coordinates
    vp = ct.table.viewport()
    before = _rgb(vp)
    _hover(vp, QPoint(rect.center().x(), rect.center().y()))
    changed = (before != _rgb(vp)).any(-1)
    n = int(changed.sum())
    assert n > 0, "hovering a row changes nothing"
    # The fill covers the row, not one cell: at least three quarters of the row's width, and
    # nothing outside its band.
    ys = np.nonzero(changed.any(1))[0]
    xs = np.nonzero(changed.any(0))[0]
    assert xs.max() - xs.min() > 0.75 * vp.width(), (xs.min(), xs.max())
    assert rect.top() - 2 <= ys.min() and ys.max() <= rect.bottom() + 2, (ys.min(), ys.max())

    _APP.sendEvent(vp, QHoverEvent(QEvent.HoverLeave, QPointF(-1, -1), QPointF(rect.center())))
    _settle(3)
    assert int((before != _rgb(vp)).any(-1).sum()) == 0, "the fill outlived the pointer"
    print(f"test_the_corners_rows_declare_that_they_are_clickable OK ({n} px on row {row})")


# ==================================================================== L3-10 the units
def test_the_corners_table_names_its_units_on_screen():
    """L3-10: seven columns of unit-bearing numbers, no unit anywhere on the page — while the
    Laps header says "Entry (km/h)" and the Stats page captions the same corner data. The caption
    follows the display unit (including through the CONSTRUCTOR seam central_view uses, where no
    unit-changed signal fires) and grip carries its own %."""
    ct = _corner_table()
    assert ct.unit_note.isVisible()
    cap = ct.unit_note.text()
    # All three unit families the eight columns use — times, speeds, grip — named once each.
    assert "seconds" in cap and "km/h" in cap and "grip %" in cap, cap

    ct.set_speed_unit("mph")
    _settle()
    assert "mph" in ct.unit_note.text() and "km/h" not in ct.unit_note.text()

    # The constructor seam: central_view writes _speed_unit and re-applies the tips, no signal.
    fresh = _keep(LT.CornerTable(_FakeCornerSession()))
    fresh._speed_unit = "mph"
    fresh._apply_corner_tips()
    assert "mph" in fresh.unit_note.text()

    # No grid, nothing to caption — the placeholder owns the pane.
    ct.set_lap(0)                                   # a lap with no corner stats
    _settle()
    assert not ct.table.isVisible() and not ct.unit_note.isVisible()
    print("test_the_corners_table_names_its_units_on_screen OK")


def test_naming_the_units_costs_the_columns_nothing():
    """The no-regression guard for L3-03, and the reason the grip % is in the caption instead of
    the cells. The caption is a LABEL: it cannot widen a column. Writing the unit into the cells
    can and does — "77 %" raises that column's floor from 38 to 55 px (+45 %), the widest single
    column cost in the table. So: the columns fit the default quadrant, and the same table with
    the suffix in its cells demonstrably wants more room for the same data.

    W10-04 — this used to end `and sum(suffixed) > vp`, i.e. "the suffixed table would overflow
    the quadrant". It would not. That claim was measured in a font the app does not ship: the file
    themed without registering the bundled Inter, so Qt substituted a family and the columns came
    out 453 px in a 453 px viewport (0 px of slack, and the suffix put them 8 px over). In the
    shipped font the same table is 414 px in 453 (39 px of slack) and the suffixed one 431 — still
    fitting. The COST is real and is what this guard now pins; the overflow was an artefact."""
    ct = _corner_table(width=457)                   # the default lap-panel quadrant
    _settle()
    vp = ct.table.viewport().width()
    total = sum(ct.table.columnWidth(c) for c in range(ct.table.columnCount()))
    assert total <= vp, (total, vp)
    assert ct.table.horizontalScrollBar().maximum() == 0
    assert all(ct.table.columnWidth(c) > 0 for c in range(ct.table.columnCount()))

    _, floors, _ = ct._column_budget()
    for r in range(ct.table.rowCount()):            # the rejected alternative, measured
        it = ct.table.item(r, _GRIP_COL)
        it.setText(it.text() + " %")
    _, suffixed, _ = ct._column_budget()
    assert suffixed[_GRIP_COL] >= floors[_GRIP_COL] * 1.3, (floors, suffixed)
    assert sum(suffixed) > sum(floors), (floors, suffixed)
    # ...and every other column is untouched: the cost is the grip column's alone.
    assert [w for c, w in enumerate(suffixed) if c != _GRIP_COL] == \
           [w for c, w in enumerate(floors) if c != _GRIP_COL], (floors, suffixed)
    print(f"test_naming_the_units_costs_the_columns_nothing OK ({total} px in {vp} px; "
          f"in-cell % would want {sum(suffixed)} px, grip column "
          f"{floors[_GRIP_COL]} -> {suffixed[_GRIP_COL]} px)")


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} LAP-TABLE AFFORDANCE TESTS PASSED", flush=True)


if __name__ == "__main__":
    _run_all()
