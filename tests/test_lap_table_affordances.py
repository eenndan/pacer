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

And one way it now says more (cool/slow-lap-why): a slow lap's Lap cell names its culprit corner
("▼ C5") and its Lap/Time tooltips say where its time went, on the shipped demo's real session.

Pure Qt on fake sessions (no pacer, no telemetry file) — except the slow-lap checks, which load the
shipped demo through the real loader — under the REAL theme — the focus ring and
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
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QFontMetrics,
    QHoverEvent,
    QImage,
    QKeyEvent,
    QMouseEvent,
)
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import data_quality, theme  # noqa: E402
from studio import lap_table as LT  # noqa: E402
from studio._signal import lap_label  # noqa: E402

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


# ============================================================ cool/slow-lap-why: where it went
# The shipped demo (`make_demo.make(path, render=False)` -> `Session.load`, as the app's --demo
# opens it): Lap 6 is its one slow lap, 5.50 s over the typical lap, C5 +2.84 s of it.
_DEMO: dict = {}


def _demo():
    if not _DEMO:
        import tempfile

        from studio.dev import make_demo as md
        from studio.session import Session
        tmp = tempfile.TemporaryDirectory(prefix="laptable_demo_")
        path = os.path.join(tmp.name, "pacer-synthetic-demo.mp4")
        md.make(path, render=False)
        _DEMO.update(tmp=tmp, session=Session.load([path]))
    return _DEMO["session"]


class _Over:
    """The demo session with some reads replaced (the rest pass through)."""

    def __init__(self, s, **over):
        self._s = s
        self.__dict__.update(over)

    def __getattr__(self, name):
        return getattr(self._s, name)


_SLOW = 5   # lap id of "Lap 6"


def test_a_slow_lap_says_where_its_time_went():
    """The demo's Lap 6 row: its Time cell and its Lap cell (which wears the "▼ C5" cue) carry the
    note on the row's terms — no "Lap 6 ·" head, the row names the lap — then the rule that called
    it slow. Every other row keeps exactly the tooltips the table gave before the note existed."""
    from studio import story
    s = _demo()
    (note,) = s.slow_lap_notes()
    assert note.lap_id == _SLOW and note.corners[0].cid == 5, note
    lt = _keep(LT.LapTable(s))
    tb, r = lt.table, lt._row_for_lap(_SLOW)
    want = (f"{story.row_text(note)}\n\n{note.rule}")
    assert want.startswith("5.50 s over your typical lap · C5 +2.84 s of it"), want
    assert tb.item(r, _TIME_COL).toolTip() == want, tb.item(r, _TIME_COL).toolTip()
    assert tb.item(r, 0).toolTip() == want, tb.item(r, 0).toolTip()
    assert tb.item(r, 0).text().endswith(f"{lap_label(_SLOW)}  ▼ C5"), tb.item(r, 0).text()
    assert tb.item(r, 0).data(LT.CUE_ROLE) == "▼ C5", tb.item(r, 0).data(LT.CUE_ROLE)
    assert "Lap 6" not in want and note.text.startswith("Lap 6 · ")
    # A lap without a note: byte-for-byte the table without the read.
    bare = _keep(LT.LapTable(_Over(s, slow_lap_notes=lambda: [])))
    for row in range(tb.rowCount()):
        lap = lt._lap_id(row)
        for c in range(lt._n_real_cols()):
            a, b = tb.item(row, c), bare.table.item(bare._row_for_lap(lap), c)
            if lap == _SLOW and c in (0, _TIME_COL):
                continue
            assert (a.text(), a.toolTip()) == (b.text(), b.toolTip()), (lap, c, a.text(),
                                                                        a.toolTip(), b.toolTip())
    print("test_a_slow_lap_says_where_its_time_went OK")


def test_the_cue_follows_its_lap_through_a_sort_and_a_refresh_reads_once():
    """Sorted by Time (slowest first), the cue is on Lap 6's row, wherever that row went. The notes
    are read once per refresh() and once per trust flip — never per sort, hover or paint — and a
    refresh is what moves them when the session's answer changes (a timing edit's path)."""
    s = _demo()
    calls = []
    notes = list(s.slow_lap_notes())

    def counted():
        calls.append(1)
        return notes

    sess = _Over(s, slow_lap_notes=counted)
    lt = _keep(LT.LapTable(sess))
    assert len(calls) == 1, f"{len(calls)} reads to build the table"
    lt.table.sortByColumn(_TIME_COL, Qt.DescendingOrder)
    _settle()
    tb = lt.table
    assert lt._lap_id(0) == _SLOW, "the slowest lap is not on top"
    cued = [lt._lap_id(r) for r in range(tb.rowCount()) if "▼" in tb.item(r, 0).text()]
    assert cued == [_SLOW], cued
    assert len(calls) == 1, f"a sort re-read the notes ({len(calls)} reads)"
    # The session's answer moves (as after a start/finish-line edit): one refresh, one read.
    import dataclasses
    notes[:] = [dataclasses.replace(notes[0], lap_id=2)]
    lt.refresh()
    assert len(calls) == 2, f"{len(calls) - 1} reads for one refresh"
    cued = [lt._lap_id(r) for r in range(tb.rowCount()) if "▼" in tb.item(r, 0).text()]
    assert cued == [2], cued
    lt.refresh_timing_trust()
    assert len(calls) == 3, f"{len(calls) - 2} reads for one trust flip"
    print("test_the_cue_follows_its_lap_through_a_sort_and_a_refresh_reads_once OK")


def test_a_provisional_note_carries_the_tables_provisional_line():
    """The table's rule under a provisional start line: it withholds what is measured FROM the best
    (★, green, Δbest) and keeps every time, muted, under PROVISIONAL_TOOLTIP. A note is measured
    from the typical lap, not the best — so it stays, cue included, with that line under it."""
    import dataclasses
    s = _demo()
    notes = [dataclasses.replace(n, caveat=("provisional",), gap_to_best_s=None)
             for n in s.slow_lap_notes()]
    lt = _keep(LT.LapTable(_Over(s, timing_verified=False, slow_lap_notes=lambda: notes)))
    tb, r = lt.table, lt._row_for_lap(_SLOW)
    want = f"{LT.slow_note_tip(notes[0])}\n\n{LT.PROVISIONAL_TOOLTIP}"
    assert tb.item(r, _TIME_COL).toolTip() == want, tb.item(r, _TIME_COL).toolTip()
    assert tb.item(r, 0).toolTip() == want, tb.item(r, 0).toolTip()
    assert "▼ C5" in tb.item(r, 0).text() and tb.item(r, LT._DELTA_COL).text() == LT.SELF_DELTA
    print("test_a_provisional_note_carries_the_tables_provisional_line OK")


def test_a_provisional_and_estimated_note_says_both_once():
    """A recording both provisional and on an estimated clock: the note's `caveat` names both
    states, and its tooltip carries the provisional line AND the estimated-clock line, once each
    (a bare time cell keeps the one stronger line)."""
    import dataclasses

    from studio import data_quality
    s = _demo()
    quality = data_quality.TimingQuality(clock=data_quality.MEDIA_CLOCK_FALLBACK)
    notes = [dataclasses.replace(n, caveat=("provisional", "estimated"), gap_to_best_s=None)
             for n in s.slow_lap_notes()]
    lt = _keep(LT.LapTable(_Over(s, timing_verified=False, timing_quality=quality,
                                 slow_lap_notes=lambda: notes)))
    tb, r = lt.table, lt._row_for_lap(_SLOW)
    est = LT.estimated_timing_tooltip(quality)
    want = f"{LT.slow_note_tip(notes[0])}\n\n{LT.PROVISIONAL_TOOLTIP}\n\n{est}"
    for c in (0, _TIME_COL):
        tip = tb.item(r, c).toolTip()
        assert tip == want, (c, tip)
        assert tip.count(LT.PROVISIONAL_TOOLTIP) == 1 and tip.count(est) == 1, tip
    other = lt._row_for_lap(_SLOW + 1)
    assert tb.item(other, _TIME_COL).toolTip() == LT.PROVISIONAL_TOOLTIP
    print("test_a_provisional_and_estimated_note_says_both_once OK")


def test_a_dropout_lap_never_wears_a_note():
    """Lap 6 made a ⚠ dropout lap on the real accessors: no cue and no note on its row (the core
    drops it; the table would not show one anyway). The median moves, so the first lap now reads
    slow — `spread`, no culprit, so a note on hover and no cue."""
    s = _demo()
    orig = s.lap_has_dropout
    try:
        s.lap_has_dropout = lambda i: i == _SLOW or orig(i)
        # A patched accessor is no re-segmentation: the story memo (keyed on the corner model's
        # generation and the valid list's identity, QA W4) cannot see it, so drop it as one would.
        s._story_memo = None
        lt = _keep(LT.LapTable(s))
        tb, r = lt.table, lt._row_for_lap(_SLOW)
        assert tb.item(r, 0).text().endswith(LT.DROPOUT_SUFFIX), tb.item(r, 0).text()
        assert tb.item(r, _TIME_COL).toolTip() == LT.DROPOUT_TOOLTIP, tb.item(r, _TIME_COL).toolTip()
        assert "▼" not in tb.item(r, 0).text(), tb.item(r, 0).text()
        first = lt._row_for_lap(0)
        assert "▼" not in tb.item(first, 0).text(), tb.item(first, 0).text()
        assert tb.item(first, _TIME_COL).toolTip().startswith("First lap · "), \
            tb.item(first, _TIME_COL).toolTip()
        # ...and a note handed over for a ⚠ lap anyway is not shown.
        planted = [n for n in _Over(s).slow_lap_notes()]
        import dataclasses
        planted.append(dataclasses.replace(planted[0], lap_id=_SLOW, category="one",
                                           corners=(LT.story.CornerLoss(5, 2.0, 1.0, 0.1),)))
        lt2 = _keep(LT.LapTable(_Over(s, slow_lap_notes=lambda: planted)))
        r2 = lt2._row_for_lap(_SLOW)
        assert "▼" not in lt2.table.item(r2, 0).text(), lt2.table.item(r2, 0).text()
        assert lt2.table.item(r2, _TIME_COL).toolTip() == LT.DROPOUT_TOOLTIP, \
            lt2.table.item(r2, _TIME_COL).toolTip()
    finally:
        s.__dict__.pop("lap_has_dropout", None)
        s._story_memo = None
    print("test_a_dropout_lap_never_wears_a_note OK")


def test_clicking_the_cue_rings_the_culprit_corner():
    """A click on Lap 6's Lap cell selects the lap, as every click does, and names C5 for the map's
    ring; a click on a lap without a cue names nothing."""
    from PySide6.QtTest import QTest
    s = _demo()
    lt = _keep(LT.LapTable(s))
    lt.resize(460, 600)
    lt.show()
    _settle()
    rang, picked = [], []
    lt.slow_corner_clicked.connect(rang.append)
    lt.laps_selected.connect(picked.append)
    tb = lt.table
    for lap in (_SLOW, 0):
        r = lt._row_for_lap(lap)
        tb.scrollToItem(tb.item(r, 0))
        _settle()
        QTest.mouseClick(tb.viewport(), Qt.LeftButton, Qt.NoModifier,
                         tb.visualItemRect(tb.item(r, 0)).center())
        _settle()
    assert rang == [5], rang
    assert picked[-2:] == [[_SLOW], [0]], picked
    print("test_clicking_the_cue_rings_the_culprit_corner OK")


def _widths(lt, w):
    lt.resize(w, 420)
    lt.show()
    _settle()
    return tuple(lt.table.columnWidth(c) for c in range(lt.table.columnCount()))


def test_a_cue_never_moves_a_column():
    """The demo HAS a slow lap: its table's column widths equal the same table's with the notes
    disabled, at a squeezed panel, the default quadrant and a wide one. (Measured on d70177f: at
    300 px the Lap column came out 1 px wider with the cue, `sizeHintForColumn` and the floor's
    `painted` rounding the cue's text differently, and every data column shifted.) And a session
    with no slow lap renders pixel for pixel the table with the cue path switched off."""
    s = _demo()
    assert s.slow_lap_notes(), "the demo lost its slow lap — this test would pass vacuously"
    for w in (300, 447, 900):
        cued = _widths(_keep(LT.LapTable(s)), w)
        plain = _widths(_keep(LT.LapTable(_NoRead(s))), w)
        assert cued == plain, f"at {w} px the cue moved the columns: {cued} vs {plain}"
    orig = LT.LapTable._read_slow_notes
    try:
        LT.LapTable._read_slow_notes = lambda self: setattr(self, "_slow_notes", {})
        refs = {}
        for w in (300, 447):
            ref = _keep(LT.LapTable(s))
            _widths(ref, w)
            refs[w] = _rgb(ref)
    finally:
        LT.LapTable._read_slow_notes = orig
    for w in (300, 447):
        lt = _keep(LT.LapTable(_Over(s, slow_lap_notes=lambda: [])))
        _widths(lt, w)
        img = _rgb(lt)
        assert img.shape == refs[w].shape and (img == refs[w]).all(), (
            w, int((img != refs[w]).any(axis=2).sum()))
    print("test_a_cue_never_moves_a_column OK")


def test_a_squeezed_cue_never_costs_the_lap_number():
    """Provisional timing (no ★ to widen the floor), the note planted on the lap with the widest
    label, that lap playing, a 300 px panel: its row fills the Lap column's floor exactly. Qt's
    right-elision would have eaten the number first ("▶ …"); the cue is the delegate's to shorten,
    so the lap's own text paints whole and the cue gets only the room that is left."""
    import dataclasses
    s = _demo()
    probe = _keep(LT.LapTable(_Over(s, timing_verified=False)))
    bold = probe.table.font()
    bold.setBold(True)
    fm = QFontMetrics(bold)
    lap = max(probe._lap_id(r) for r in range(probe.table.rowCount())
              if fm.horizontalAdvance(f"{LT.CURRENT_PREFIX}{lap_label(probe._lap_id(r))}") ==
              max(fm.horizontalAdvance(f"{LT.CURRENT_PREFIX}{lap_label(probe._lap_id(q))}")
                  for q in range(probe.table.rowCount())))
    (note,) = s.slow_lap_notes()
    two = dataclasses.replace(note, lap_id=lap, category="two",
                              corners=(note.corners[0], note.also[0]), also=())
    frames = {}
    for key, notes in (("cue", [two]), ("plain", [])):
        lt = _keep(LT.LapTable(_Over(s, timing_verified=False, slow_lap_notes=lambda n=notes: n)))
        lt.set_current_lap(lap)
        _widths(lt, 300)
        r = lt._row_for_lap(lap)
        item = lt.table.item(r, 0)
        # In view in both: the cue table's slow-lap finder footer takes 24 px of its height, which
        # put the bottom row below the viewport and cropped the grab under the comparison.
        lt.table.scrollToItem(item)
        _settle()
        rect = lt.table.visualItemRect(item)
        img = _rgb(lt.table.viewport())
        frames[key] = (lt, item, rect, img)
    lt, item, rect, img = frames["cue"]
    want = f"{LT.CURRENT_PREFIX}{lap_label(lap)}"
    # The lap's own text, painted: the cell's left part matches the render with no cue at all.
    _, _, prect, pimg = frames["plain"]
    x1 = rect.left() + QFontMetrics(item.font()).horizontalAdvance(want) + 8
    a = img[rect.top():rect.bottom(), rect.left():x1]
    b = pimg[prect.top():prect.bottom(), prect.left():x1]
    assert a.shape == b.shape and (a == b).all(), "the lap number did not paint whole"
    assert (a != a[0, 0]).any(), "no glyphs in the number's region — nothing was compared"
    assert item.text() == f"{want}  ▼ C5 C6", item.text()
    assert LT.cueless(item.text(), item.data(LT.CUE_ROLE)) == want
    form = lt._delegate.last_cue.get(lt._row_for_lap(lap))
    assert form in ("", "▼"), f"the cue claimed room the column does not have: {form!r}"
    # ...and with room, the forms step down in order, names first.
    assert LT.cue_forms("▼ C5 C6") == ["▼ C5 C6", "▼ C5…", "▼"]
    assert LT.cue_forms("▼ C5") == ["▼ C5", "▼"]
    print(f"test_a_squeezed_cue_never_costs_the_lap_number OK (lap {lap_label(lap)}, cue {form!r})")


def test_the_cue_paints_in_muted_ink():
    """The cue is an annotation, not a mark: the lap number keeps the row's ink and the "▼ C5"
    beside it paints in PROVISIONAL_COLOR (theme.C.text_dim) — no pixel of the cue's span is the
    full text colour."""
    s = _demo()
    lt = _keep(LT.LapTable(s))
    _widths(lt, 447)
    r = lt._row_for_lap(_SLOW)
    item = lt.table.item(r, 0)
    rect = lt.table.visualItemRect(item)
    assert lt._delegate.last_cue.get(r) == "▼ C5", lt._delegate.last_cue
    cell = _rgb(lt.table.viewport())[rect.top():rect.bottom(), rect.left():rect.right()]
    plain = _keep(LT.LapTable(_NoRead(s)))
    _widths(plain, 447)
    prect = plain.table.visualItemRect(plain.table.item(plain._row_for_lap(_SLOW), 0))
    bare = _rgb(plain.table.viewport())[prect.top():prect.bottom(), prect.left():prect.right()]

    def near(px, hexcol):           # `_rgb` planes are the buffer's B, G, R byte order
        c = QColor(hexcol)
        return int((np.abs(px.astype(int) - (c.blue(), c.green(), c.red())).sum(axis=-1)
                    < 30).sum())
    assert near(cell, theme.C.text) > 0, "the lap number lost its ink"
    assert near(cell, theme.C.text) == near(bare, theme.C.text), (
        "the cue added full-ink pixels", near(cell, theme.C.text), near(bare, theme.C.text))
    assert near(cell, theme.PROVISIONAL_COLOR) > 10 + near(bare, theme.PROVISIONAL_COLOR), \
        "the cue is not in the muted ink"
    print("test_the_cue_paints_in_muted_ink OK")


def test_a_deselecting_click_rings_nothing():
    """⌘-clicking a selected cued lap to DESELECT it is not a request to look at its corner."""
    from PySide6.QtTest import QTest
    s = _demo()
    lt = _keep(LT.LapTable(s))
    lt.resize(460, 600)
    lt.show()
    _settle()
    rang = []
    lt.slow_corner_clicked.connect(rang.append)
    tb = lt.table
    r = lt._row_for_lap(_SLOW)
    tb.scrollToItem(tb.item(r, 0))
    _settle()
    at = tb.visualItemRect(tb.item(r, 0)).center()
    QTest.mouseClick(tb.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    _settle()
    QTest.mouseClick(tb.viewport(), Qt.LeftButton, Qt.ControlModifier, at)
    _settle()
    assert lt.selected_lap_ids() == [], lt.selected_lap_ids()
    assert rang == [5], f"the deselecting click rang a corner: {rang}"
    print("test_a_deselecting_click_rings_nothing OK")


def test_the_cue_ring_leaves_with_its_lap_and_stays_off_a_race():
    """On a real CentralView: the cue's ring is THAT lap's — the next lap the user selects takes
    it down (a ring a Corners row pinned since is that row's and stays); under a Lap Race the cue
    rings nothing, as corner rings are off on the race's canvas (#561)."""
    from test_central_view_realqt import _real_central_view
    view, s, _t0, _t1 = _real_central_view()
    laps = [r["idx"] for r in s.lap_rows()]
    view.table.slow_corner_clicked.emit(2)
    assert view.map.pinned_corner() == "C2", view.map.pinned_corner()
    view.table.laps_selected.emit([laps[-1]])
    assert view.map.pinned_corner() is None, view.map.pinned_corner()
    view.table.slow_corner_clicked.emit(2)
    view._ring_corner(1)                     # a Corners/Stats row click, after the cue
    view.table.laps_selected.emit([laps[0]])
    assert view.map.pinned_corner() == "C1", view.map.pinned_corner()
    view._ring_corner(None)
    view.race.tracks = object()              # racing (RaceController.active), no canvas needed
    try:
        assert view.is_racing()
        view.table.slow_corner_clicked.emit(2)
        assert view.map.pinned_corner() is None, view.map.pinned_corner()
    finally:
        view.race.tracks = None
    print("test_the_cue_ring_leaves_with_its_lap_and_stays_off_a_race OK")


def test_the_cue_and_a_moment_jump_share_one_owned_ring():
    """The slow-lap cue and a Stats MOMENTS Jump pin the same owned ring: a moment Jump without a
    corner takes the cue's ring down, and a cue click on another lap after a moment's loss Jump
    leaves the ring the cue's (the lap change drops the moment's, the cue pins its own)."""
    from test_central_view_realqt import _real_central_view
    view, s, _t0, _t1 = _real_central_view()
    laps = [r["idx"] for r in s.lap_rows()]
    view.table.slow_corner_clicked.emit(2)
    assert view.map.pinned_corner() == "C2", view.map.pinned_corner()
    view.stats_view.moment_jump.emit(laps[0], None)
    assert view.map.pinned_corner() is None, "a moment Jump left the cue's ring up"
    view.stats_view.moment_jump.emit(laps[0], 1)
    assert view.map.pinned_corner() == "C1", view.map.pinned_corner()
    view.table.laps_selected.emit([laps[-1]])    # the cue's click selects its lap first...
    view.table.slow_corner_clicked.emit(2)       # ...then pins its own corner
    assert view.map.pinned_corner() == "C2", view.map.pinned_corner()
    view.stats_view.moment_jump.emit(laps[0], None)
    assert view.map.pinned_corner() is None, "the cue's ring outlived the next Jump"
    print("test_the_cue_and_a_moment_jump_share_one_owned_ring OK")


# ===================================================== cool/slow-lap-finder: find the cue rows
# The table opens centred on the best lap, so on a long session the cue rows can all sit out of
# view (planted below: four cue rows, a short grid on its last lap, 0 of 4 in view). The footer counts the rows wearing a cue and "next ›" scrolls to each, wrapping, and
# selects nothing (a Laps pick moves the charts and the video, and leaves a comparison, #575).
def _planted(s, cued=(0, 1, 2, 5), uncued=()):
    """The demo's one note copied onto `cued` lap ids (a culprit, so a ▼ cue) and onto `uncued`
    ones as `spread` (slow, no single culprit, so no cue)."""
    import dataclasses
    (note,) = s.slow_lap_notes()
    return ([dataclasses.replace(note, lap_id=i) for i in cued]
            + [dataclasses.replace(note, lap_id=i, category="spread", corners=())
               for i in uncued])


def _visible_laps(lt) -> set:
    tb = lt.table
    vp = tb.viewport().rect()
    return {lt._lap_id(r) for r in range(tb.rowCount())
            if vp.contains(tb.visualRect(tb.model().index(r, 0)))}


def test_the_finder_counts_the_cue_rows_and_hides_without_one():
    """The demo's one cue row: "1 slow lap marked ▼", the rule in story.py's own words under the
    tooltip. Four cue rows and two cueless slow laps: "4 slow laps marked ▼", and the two left out
    are named as left out. No note, no rows: no footer at all."""
    s = _demo()
    (note,) = s.slow_lap_notes()
    lt = _keep(LT.LapTable(s))
    lt.resize(460, 600)
    lt.show()
    _settle()
    assert lt._slow_finder.isVisible(), "the demo's cue row has no finder"
    assert lt._slow_finder_label.text() == "1 slow lap marked ▼", lt._slow_finder_label.text()
    tip = lt._slow_finder_next.toolTip()
    assert tip.endswith(f"\n\n{note.rule}") and "Not counted" not in tip, tip
    assert lt._slow_finder_label.toolTip() == tip
    # Notes for laps no row holds (99, 98) count for nothing: the count is the walk's own rows.
    many = _keep(LT.LapTable(_Over(s, slow_lap_notes=lambda: _planted(
        s, cued=(0, 1, 2, 5, 99), uncued=(4, 7, 98)))))
    many.show()
    _settle()
    assert many._slow_finder_label.text() == "4 slow laps marked ▼", many._slow_finder_label.text()
    assert "Not counted: 2 other slow laps, whose time no one or two corners hold; they have no cue" \
        in many._slow_finder_next.toolTip(), many._slow_finder_next.toolTip()
    one_left = LT.slow_finder_tip(note.rule, 1)
    assert "Not counted: 1 other slow lap, whose time no one or two corners hold; it has no cue" \
        in one_left, one_left
    # Cueless slow laps alone: nothing to land on, no footer.
    spread = _keep(LT.LapTable(_Over(s, slow_lap_notes=lambda: _planted(s, cued=(), uncued=(4,)))))
    bare = _keep(LT.LapTable(_Over(s, slow_lap_notes=lambda: [])))
    no_laps = _FakeLapSession(sectors=0, dropout=())
    no_laps._times = []
    no_laps.slow_lap_notes = lambda: _planted(s)      # notes for laps no row holds: none shown
    empty = _keep(LT.LapTable(no_laps))
    for t in (spread, bare, empty):
        t.show()
        _settle()
        assert not t._slow_finder.isVisible(), t._slow_finder_label.text()
    print("test_the_finder_counts_the_cue_rows_and_hides_without_one OK")


def test_next_reaches_every_cue_row_wraps_and_selects_nothing():
    """A short grid scrolled to the last lap (as the app opens a long session: centred on its best,
    far from the cue rows): no cue row in view. Each "next ›" lands the next cue row in view, in
    table order, and the fifth wraps to the first; the selection, and so the charts, the video and
    any comparison, never moves, and no `laps_selected` is emitted."""
    s = _demo()
    lt = _keep(LT.LapTable(_Over(s, slow_lap_notes=lambda: _planted(s))))
    lt.resize(460, 230)
    lt.show()
    _settle()
    last = lt._lap_id(lt.table.rowCount() - 1)
    lt.select([last])
    _settle()
    cued = {0, 1, 2, 5}
    assert not (cued & _visible_laps(lt)), sorted(_visible_laps(lt))
    picked = []
    lt.laps_selected.connect(picked.append)
    landed = []
    for _ in range(5):
        lt._slow_finder_next.click()
        _settle()
        landed.append(lt._finder_lap)
        assert lt._finder_lap in _visible_laps(lt), (lt._finder_lap, sorted(_visible_laps(lt)))
    assert landed == [0, 1, 2, 5, 0], landed
    assert lt.selected_lap_ids() == [last] and picked == [], (lt.selected_lap_ids(), picked)
    # Sorted slowest first, "next" walks the rows as they now stand, not by lap number.
    lt.table.sortByColumn(_TIME_COL, Qt.DescendingOrder)
    _settle()
    lt.table.scrollToBottom()
    _settle()
    order = [lt._lap_id(r) for r in lt._cue_rows()]
    walk = []
    for _ in range(len(order)):
        lt._slow_finder_next.click()
        _settle()
        walk.append(lt._finder_lap)
    assert walk == order, (walk, order)
    print(f"test_next_reaches_every_cue_row_wraps_and_selects_nothing OK ({landed})")


def test_the_finder_follows_the_notes_through_a_trust_flip():
    """story.py keeps its notes under a provisional line and an estimated clock (they carry a
    caveat), so the finder stays; when a refresh takes the notes away, it goes."""
    import dataclasses
    s = _demo()
    notes = [dataclasses.replace(n, caveat=("provisional",), gap_to_best_s=None)
             for n in _planted(s)]
    lt = _keep(LT.LapTable(_Over(s, timing_verified=False, slow_lap_notes=lambda: notes)))
    lt.show()
    _settle()
    assert lt._slow_finder_label.text() == "4 slow laps marked ▼", lt._slow_finder_label.text()
    notes[:] = []
    lt.refresh_timing_trust()
    _settle()
    assert not lt._slow_finder.isVisible()
    print("test_the_finder_follows_the_notes_through_a_trust_flip OK")


def test_the_finder_is_a_named_tab_stop_that_fits_a_narrow_panel():
    """"next ›" is reached by Tab out of the grid (which gives Tab back), says what it does to a
    screen reader, and is a HIT_MIN target; the footer costs less than one lap row; at the
    narrowest lap panel the two-digit count and the button both show uncut."""
    s = _demo()
    lt = _keep(LT.LapTable(_Over(s, slow_lap_notes=lambda: _planted(s, cued=tuple(range(12))))))
    lt.resize(300, 400)
    lt.show()
    _settle()
    btn = lt._slow_finder_next
    assert btn.focusPolicy() & Qt.TabFocus, btn.focusPolicy()
    assert btn.accessibleName() == LT.SLOW_FINDER_NEXT_NAME
    assert btn.accessibleDescription().startswith("12 slow laps marked ▼. "), \
        btn.accessibleDescription()
    lt.table.setFocus()
    _settle()
    path = []
    for _ in range(4):               # the grid, its sort header (a tab stop of its own), the finder
        if btn.hasFocus():
            break
        lt.table.window().focusNextChild()
        _settle()
        path.append(type(_APP.focusWidget()).__name__)
    assert btn.hasFocus(), path
    assert btn.height() >= theme.HIT_MIN, btn.height()
    assert lt._slow_finder.height() < theme.GRID_ROW_H, lt._slow_finder.height()
    lab = lt._slow_finder_label
    need = QFontMetrics(lab.font()).horizontalAdvance(lab.text())
    assert lab.width() >= need, (lab.width(), need)
    assert btn.geometry().right() <= lt._slow_finder.width(), (btn.geometry(), lt._slow_finder.width())
    assert btn.width() >= btn.sizeHint().width(), (btn.width(), btn.sizeHint())
    print(f"test_the_finder_is_a_named_tab_stop_that_fits_a_narrow_panel OK "
          f"(footer {lt._slow_finder.height()} px, label {lab.width()}/{need} px)")


def _outline_ink(lt) -> int:
    """Pixels of the landed row's top edge painted in the outline's ink (C.text), off a grab."""
    rect = lt._landed.row_rect()
    if rect.isEmpty():
        return 0
    img = _rgb(lt.table.viewport())
    want = np.array(QColor(theme.C.text).getRgb()[:3][::-1])   # _rgb's planes are B, G, R
    band = img[rect.top():rect.top() + theme.FOCUS_RING_PX, :]
    return int((np.abs(band.astype(int) - want).sum(axis=2) < 12).sum())


def test_next_outlines_the_row_it_found_even_when_nothing_scrolls():
    """The shipped demo: every row fits, so the scroll is a no-op and "next ›" used to do nothing
    visible. It now outlines Lap 6's row (the overlay, in C.text, on the row's own pixels) and
    selects nothing. Motion is off offscreen, so the outline is the static one, and it holds until
    the reader moves: a scroll, a press on the grid, a sort or a refresh each clear it."""
    from PySide6.QtTest import QTest
    s = _demo()
    lt = _keep(LT.LapTable(s))
    lt.resize(460, 600)
    lt.show()
    _settle()
    assert lt.table.verticalScrollBar().maximum() == 0, "the demo grid no longer fits: retarget"
    picked = []
    lt.laps_selected.connect(picked.append)
    sel = lt.selected_lap_ids()
    assert _outline_ink(lt) == 0
    lt._slow_finder_next.click()
    _settle()
    assert lt._landed.isVisible() and lt._landed.row == lt._row_for_lap(_SLOW), lt._landed.row
    ink = _outline_ink(lt)
    assert ink > 100, f"{ink} px of outline on the landed row"
    assert lt.selected_lap_ids() == sel and picked == [], (lt.selected_lap_ids(), picked)
    for clear in ("refresh", "press", "sort"):
        lt._slow_finder_next.click()
        _settle()
        assert lt._landed.isVisible(), clear
        if clear == "refresh":
            lt.refresh()
        elif clear == "press":
            QTest.mousePress(lt.table.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(5, 5))
            QTest.mouseRelease(lt.table.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(5, 5))
        else:
            lt.table.sortByColumn(_TIME_COL, Qt.DescendingOrder)
        _settle()
        assert not lt._landed.isVisible() and _outline_ink(lt) == 0, clear
    # ...and a scroll the reader makes, on a grid that can scroll.
    tall = _keep(LT.LapTable(_Over(s, slow_lap_notes=lambda: _planted(s))))
    tall.resize(460, 230)
    tall.show()
    _settle()
    tall._slow_finder_next.click()
    _settle()
    assert tall._landed.isVisible(), "the finder's own scroll cleared its outline"
    bar = tall.table.verticalScrollBar()
    bar.setValue(bar.maximum() if bar.value() != bar.maximum() else 0)
    _settle()
    assert not tall._landed.isVisible()
    print(f"test_next_outlines_the_row_it_found_even_when_nothing_scrolls OK ({ink} px of outline)")


def test_with_motion_on_the_outline_fades_and_ends():
    """PACER_MOTION=1 on a fake clock: the outline starts at full strength, is part-way through its
    fade mid-transition, and is gone at FINDER_OUTLINE_MS, with no transition left running."""
    s = _demo()
    lt = _keep(LT.LapTable(s))
    lt.resize(460, 600)
    lt.show()
    _settle()
    saved = (os.environ.get("PACER_MOTION"), LT.motion._reduce_pref, LT.motion._system_reduce)
    now = [100.0]
    LT.motion.set_clock(lambda: now[0])
    os.environ["PACER_MOTION"] = "1"
    LT.motion._reduce_pref, LT.motion._system_reduce = None, False
    try:
        assert LT.motion.enabled()
        lt._slow_finder_next.click()
        assert lt._landed.isVisible() and lt._landed.strength == 1.0
        now[0] += LT.motion.FINDER_OUTLINE_MS / 2000
        LT.motion.pump()
        assert 0.0 < lt._landed.strength < 1.0, lt._landed.strength
        now[0] += LT.motion.FINDER_OUTLINE_MS / 1000
        LT.motion.pump()
        assert not lt._landed.isVisible() and lt._landed_tr is None
        assert LT.motion.active_count() == 0
    finally:
        LT.motion.finish_all()
        LT.motion.set_clock(None)
        if saved[0] is None:
            os.environ.pop("PACER_MOTION", None)
        else:
            os.environ["PACER_MOTION"] = saved[0]
        LT.motion._reduce_pref, LT.motion._system_reduce = saved[1], saved[2]
    print("test_with_motion_on_the_outline_fades_and_ends OK")


def test_a_click_on_next_leaves_the_keyboard_walking_laps_from_the_row_it_found():
    """"next ›" is a TAB stop, not a click-focus one: a mouse click leaves the focus on the grid,
    and the grid's CURRENT index (not its selection) is now the landed row, so ↓ picks the lap
    after it — the one keyboard pick the reader makes, from where the finder left them. The click
    itself emits no selection."""
    from PySide6.QtTest import QTest
    s = _demo()
    box = _keep(QWidget())
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lt = LT.LapTable(_Over(s, slow_lap_notes=lambda: _planted(s)))
    lay.addWidget(lt)
    box.resize(460, 230)
    box.show()
    box.activateWindow()
    _settle()
    last = lt._lap_id(lt.table.rowCount() - 1)
    lt.select([last])
    lt.table.setFocus()
    _settle()
    picked = []
    lt.laps_selected.connect(picked.append)
    btn = lt._slow_finder_next
    QTest.mouseClick(btn, Qt.LeftButton, Qt.NoModifier, btn.rect().center())
    _settle()
    assert lt._finder_lap == 0 and picked == [], (lt._finder_lap, picked)
    assert not btn.hasFocus() and lt.table.hasFocus(), _APP.focusWidget()
    assert lt.table.currentRow() == lt._row_for_lap(0) and lt.selected_lap_ids() == [last]
    QTest.keyClick(lt.table, Qt.Key_Down)
    _settle()
    assert lt.selected_lap_ids() == [1] and picked[-1:] == [[1]], (lt.selected_lap_ids(), picked)
    print("test_a_click_on_next_leaves_the_keyboard_walking_laps_from_the_row_it_found OK")


def test_a_key_move_after_next_takes_the_outline_off_the_old_row():
    """QA w5: with motion off (headless, View ▸ Reduce motion), "next ›" outlined Lap 6 and ↓
    selected Lap 7 while Lap 6 kept its outline — two rows marked, only a scroll or a press on the
    grid cleared it. Any move of the current row or the selection ends it now, with motion off
    (the static outline) and on (mid-fade), checked on the grid's own pixels; a pick made from
    elsewhere (`select`, the charts' path) ends it too."""
    from PySide6.QtTest import QTest
    s = _demo()
    lt = _keep(LT.LapTable(s))
    lt.resize(460, 600)
    lt.show()
    lt.activateWindow()
    _settle()
    lt.table.setFocus()
    saved = (os.environ.get("PACER_MOTION"), LT.motion._reduce_pref, LT.motion._system_reduce)
    now = [100.0]
    LT.motion.set_clock(lambda: now[0])
    try:
        for on in (False, True):
            os.environ["PACER_MOTION"] = "1" if on else "0"
            LT.motion._reduce_pref, LT.motion._system_reduce = None, False
            assert LT.motion.enabled() is on
            for move in ("down", "select"):
                lt._slow_finder_next.click()
                _settle()
                landed = lt._row_for_lap(_SLOW)
                assert lt._landed.row == landed and lt._landed.isVisible(), (on, move)
                if on:
                    now[0] += LT.motion.FINDER_OUTLINE_MS / 4000   # a quarter into the fade
                    LT.motion.pump()
                    assert 0.0 < lt._landed.strength < 1.0, lt._landed.strength
                else:
                    assert _outline_ink(lt) > 100, (on, move)
                if move == "down":
                    QTest.keyClick(lt.table, Qt.Key_Down)
                    _settle()
                    assert lt.table.currentRow() == landed + 1, (on, lt.table.currentRow())
                else:
                    lt.select([_SLOW + 2])
                    _settle()
                assert lt._landed.row == -1 and not lt._landed.isVisible(), (on, move)
                assert lt._landed_tr is None and _outline_ink(lt) == 0, (on, move)
                if on:
                    assert LT.motion.active_count() == 0
    finally:
        LT.motion.finish_all()
        LT.motion.set_clock(None)
        if saved[0] is None:
            os.environ.pop("PACER_MOTION", None)
        else:
            os.environ["PACER_MOTION"] = saved[0]
        LT.motion._reduce_pref, LT.motion._system_reduce = saved[1], saved[2]
    print("test_a_key_move_after_next_takes_the_outline_off_the_old_row OK")


def test_the_finder_tooltip_carries_the_rows_trust_lines():
    """The footer speaks of the same laps the cue cells do, so it carries their trust lines, from
    the one source (`note_trust_lines`): the provisional line while the start line is unconfirmed,
    the estimated-clock line when the notes say so — and neither on trusted timing."""
    import dataclasses
    s = _demo()
    plain = _keep(LT.LapTable(s))
    tip = plain._slow_finder_next.toolTip()
    assert LT.PROVISIONAL_TOOLTIP not in tip, tip
    est = LT.estimated_timing_tooltip(s.timing_quality)
    assert est not in tip, tip
    prov = [dataclasses.replace(n, caveat=("provisional",), gap_to_best_s=None)
            for n in s.slow_lap_notes()]
    lt = _keep(LT.LapTable(_Over(s, timing_verified=False, slow_lap_notes=lambda: prov)))
    tip = lt._slow_finder_next.toolTip()
    assert tip.endswith(f"\n\n{LT.PROVISIONAL_TOOLTIP}"), tip
    assert tip == lt._slow_finder_label.toolTip()
    r = lt._row_for_lap(_SLOW)
    assert lt.table.item(r, 0).toolTip().endswith(LT.PROVISIONAL_TOOLTIP)
    from studio import story
    estd = [dataclasses.replace(n, caveat=(story.CAVEAT_ESTIMATED,)) for n in s.slow_lap_notes()]
    lt2 = _keep(LT.LapTable(_Over(s, slow_lap_notes=lambda: estd)))
    tip2 = lt2._slow_finder_next.toolTip()
    assert tip2.endswith(f"\n\n{est}") and LT.PROVISIONAL_TOOLTIP not in tip2, tip2
    assert lt2.table.item(lt2._row_for_lap(_SLOW), 0).toolTip().endswith(est)
    # A degraded recording (#581: `timing_quality.degraded` stamps every note, through
    # `story.note_trust_states`) reaches the footer too, with notes that carry no caveat at all.
    quality = data_quality.TimingQuality(clock=data_quality.MEDIA_CLOCK_FALLBACK)
    deg_est = LT.estimated_timing_tooltip(quality)
    lt3 = _keep(LT.LapTable(_Over(s, timing_quality=quality)))
    tip3 = lt3._slow_finder_next.toolTip()
    assert tip3.endswith(f"\n\n{deg_est}"), tip3
    assert lt3.table.item(lt3._row_for_lap(_SLOW), 0).toolTip().endswith(deg_est)
    print("test_the_finder_tooltip_carries_the_rows_trust_lines OK")


class _NoRead:
    """The demo session WITHOUT `slow_lap_notes` (the table before the feature reads nothing)."""

    def __init__(self, s):
        self._s = s

    def __getattr__(self, name):
        if name == "slow_lap_notes":
            raise AttributeError(name)
        return getattr(self._s, name)


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} LAP-TABLE AFFORDANCE TESTS PASSED", flush=True)


if __name__ == "__main__":
    _run_all()
