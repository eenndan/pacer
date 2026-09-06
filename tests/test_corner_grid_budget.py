"""The corner grid's CELL FLOORS are a digit budget, and the budget has no slack in it.

WHY THIS FILE EXISTS. PRs #196/#197 turned on tabular figures, and the design wave's own
regression sweep measured what that did to this table: the eight corner columns' cell floors grew,
and the window band over which the Corners tab shows a horizontal scrollbar grew with them —
973..1210 px of window before the wave, 973..1268 after (GX010062, three chapters). The table's
own budget deliberately tolerates that scrollbar (`lap_table.header_floors` case 2), so the
scrollbar is not the defect; the defect would be nobody noticing the next time it moves.

TWO CLAIMS, and the first is the one that decides whether there is anything to reclaim:

  1. NO SLACK. `CornerTable._column_budget` floors each column at `sizeHintForColumn`. Narrowing
     any one section a single pixel below that floor elides that column's widest VALUE — measured
     from the WINDOW COMPOSITE, not from a child grab and not from a font metric. So the floors
     are not padded: every pixel in them is either a glyph or the style's own box, and a fix that
     wanted to "trim the floors" has nothing to trim. (This is the measurement that refuted one.
     The levers that remain are what a cell PRINTS and `QTableView::item`'s padding in theme.py.)

  2. THE DIGIT COUNT IS A LAYOUT INPUT, so it is pinned. Under tabular figures every digit takes
     the widest advance and the direction inverted — `1` was the NARROWEST digit and is now the
     widest — so a faster circuit's 1-heavy three-digit speeds are the expensive case where they
     used to be the cheap one. D24 will not produce them, so they are CONSTRUCTED here: the same
     table, the same eight columns, one more digit in the speed cells.

Both pins are one-directional (`<=`): removing a column or narrowing a value may not turn the
build red, growing the budget must.

Run: QT_QPA_PLATFORM=offscreen python tests/test_corner_grid_budget.py
"""
import os
import sys
from types import SimpleNamespace

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()            # BEFORE the first widget: a size is a function of its font

from PySide6.QtCore import QPoint, QRect  # noqa: E402
from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QVBoxLayout, QWidget  # noqa: E402

from studio import lap_table as LT  # noqa: E402

_ALIVE: list = []

#: The corner speeds a 2-digit circuit (Daytona MK, the D24 fixture) and a 3-digit one produce.
#: Only the magnitude differs — same format, same decimals, same column set.
_SLOW = dict(apex=44.9, entry=45.7, exit_=47.9)
_FAST = dict(apex=144.9, entry=145.7, exit_=147.9)


def _keep(w):
    _ALIVE.append(w)
    return w


def _settle(n=6):
    for _ in range(n):
        _APP.processEvents()


class _CornerSession:
    """The CornerTable read surface with the speed magnitude as a parameter — the one input this
    file varies. Deltas are real (not the baseline lap's self-zeros), so the two Δ columns carry
    values rather than em-dashes: that is the populated case the panel spends its life in."""

    def __init__(self, n=12, **speeds):
        self._n = n
        self._speeds = speeds or _SLOW
        cl = [SimpleNamespace(label=f"C{i + 1}", direction=1 if i % 2 else -1, cid=i)
              for i in range(n)]
        self.corners = SimpleNamespace(
            corner_list=lambda: cl,
            lap_corner_stats=self._stats,
            corner_session_bests=lambda: [2.5] * n)
        self.driving = SimpleNamespace(lap_corner_grip=lambda lap: [0.77] * n)

    def _stats(self, lap_id):
        if lap_id != 1:
            return []
        return [SimpleNamespace(time=2.48 + i * 0.01, delta=0.05 + i * 0.01,
                                apex_speed=self._speeds["apex"], apex_speed_delta=-0.8,
                                entry_speed=self._speeds["entry"],
                                exit_speed=self._speeds["exit_"])
                for i in range(self._n)]

    def lap_count(self):
        return 2

    def valid_lap_ids(self):
        return [0, 1]

    def best_lap_id(self):
        return 0

    def has_reference(self):
        return False

    def reference_label(self):
        return None

    def reference_is_own_recording(self):
        return False

    def reference_lap_id(self):
        return None


def _table(**speeds):
    ct = _keep(LT.CornerTable(_CornerSession(**speeds)))
    win = _keep(QWidget())
    win.setObjectName("centralwidget")
    lay = QVBoxLayout(win)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(ct)
    win.resize(560, 460)
    win.show()
    ct.refresh()
    ct.set_lap(1)
    _settle(8)
    return ct, win


def _ink_width(win, table, row, col) -> int:
    """The bounding width of the ink in one cell, read from the WINDOW's rendered image.

    Never from `table.grab()`: a child grab reports the QSS rule's colour out of the palette and
    would measure a cell that composited nothing."""
    from collections import Counter
    img = win.grab().toImage()
    rect = table.visualItemRect(table.item(row, col))
    tl = table.viewport().mapTo(win, QPoint(0, 0)) + rect.topLeft()
    box = QRect(tl.x(), tl.y(), rect.width(), rect.height()).intersected(img.rect())
    seen, cnt = {}, Counter()
    for y in range(box.top(), box.bottom() + 1):
        for x in range(box.left(), box.right() + 1):
            v = img.pixel(x, y)
            seen[(x, y)] = v
            cnt[v] += 1
    if not cnt:
        return 0
    bg = cnt.most_common(1)[0][0]
    xs = [x for (x, _y), v in seen.items() if v != bg]
    return (max(xs) - min(xs) + 1) if xs else 0


def _widest_row(table, col, fm) -> tuple[int, str]:
    best, text, px = -1, "", -1
    for r in range(table.rowCount()):
        item = table.item(r, col)
        s = item.text() if item is not None else ""
        if fm.horizontalAdvance(s) > px:
            best, text, px = r, s, fm.horizontalAdvance(s)
    return best, text


# =========================================================================== 1. no slack
def test_no_corner_column_can_lose_a_pixel_of_its_cell_floor():
    """`CornerTable._column_budget`. Claim 1, from the pixels: at its floor every column paints
    its widest value whole, and one pixel narrower it does not."""
    ct, win = _table()
    table = ct.table
    hdr = table.horizontalHeader()
    fm = QFontMetrics(table.font())
    _natural, floors, _caps = ct._column_budget()
    elide_at_floor, over_charged = [], []
    for c, floor in enumerate(floors):
        row, text = _widest_row(table, c, fm)
        if row < 0 or not text:
            continue
        hdr.resizeSection(c, floor + 8)
        _settle(3)
        whole = _ink_width(win, table, row, c)
        hdr.resizeSection(c, floor)
        _settle(3)
        at_floor = _ink_width(win, table, row, c)
        hdr.resizeSection(c, floor - 1)
        _settle(3)
        below = _ink_width(win, table, row, c)
        if at_floor < whole:
            elide_at_floor.append(f"c{c} {text!r}: {at_floor} px of ink at its own {floor} px "
                                  f"floor, {whole} px with room")
        if below >= whole:
            over_charged.append(f"c{c} {text!r}: still whole at {floor - 1} px — the floor "
                                f"{floor} charges for a pixel the value does not use")
        hdr.resizeSection(c, floor + 8)
    assert not elide_at_floor, (
        "a corner VALUE elides at its own cell floor — the one thing this table's budget "
        "promises never happens (a header may elide into its tooltip; a value has nowhere to "
        "go):\n  " + "\n  ".join(elide_at_floor))
    assert not over_charged, (
        "the cell floors are padded — trim them rather than paying for them in scrollbar:\n  "
        + "\n  ".join(over_charged))
    print(f"test_no_corner_column_can_lose_a_pixel_of_its_cell_floor OK "
          f"({len(floors)} columns, {sum(floors)} px of floors, 0 px of slack in any of them)")


# =========================================================================== 2. the digit budget
#: What the eight cell floors want on the session above, measured. `<=` on purpose: this may only
#: ever be re-pinned DOWNWARD without a decision. Two digits is D24's magnitude; three is the
#: constructed faster circuit, which no fixture on this machine produces.
#:
#: These are the FAKE's numbers, not the shipped fixture's — this session marks two corners with
#: the best-corner ★ and prints a wider Δbest than D24 does, so its floors sit ~17 px above the
#: 429 px GX010062 wants with a lap selected. That is deliberate: a pin measured on the wider case
#: cannot be satisfied by a fixture getting luckier.
MAX_FLOORS_2_DIGIT = 446
MAX_FLOORS_3_DIGIT = 473


def test_the_corner_grid_floors_stay_inside_their_measured_budget():
    """Claim 2. The pin the design wave did not have: #196 widened these floors by 12 px and
    nothing went red. A typographic change that moves them again has to move this number too."""
    slow, _ = _table()
    fast, _ = _table(**_FAST)
    _settle(4)
    _n, slow_floors, _c = slow._column_budget()
    _n, fast_floors, _c = fast._column_budget()
    cost = sum(fast_floors) - sum(slow_floors)
    assert sum(slow_floors) <= MAX_FLOORS_2_DIGIT, (
        f"the corner grid's eight cell floors want {sum(slow_floors)} px on a 2-digit circuit, "
        f"over the measured {MAX_FLOORS_2_DIGIT} — per column {slow_floors}")
    assert sum(fast_floors) <= MAX_FLOORS_3_DIGIT, (
        f"the corner grid's eight cell floors want {sum(fast_floors)} px once the three speed "
        f"columns carry a third digit, over the measured {MAX_FLOORS_3_DIGIT} — per column "
        f"{fast_floors}")
    assert cost > 0, (
        "a third digit in three speed columns cost 0 px — the constructed case is not being "
        "constructed (check that the cells really carry the wider strings)")
    print(f"test_the_corner_grid_floors_stay_inside_their_measured_budget OK "
          f"(2-digit {sum(slow_floors)} px <= {MAX_FLOORS_2_DIGIT}, 3-digit {sum(fast_floors)} "
          f"px <= {MAX_FLOORS_3_DIGIT}, a third digit costs {cost} px over 3 columns)")


def test_header_floors_never_moves_the_scrollbar_band():
    """`lap_table.header_floors`' own stated invariant, which its docstring's other numbers have
    outlived: granting a header its stem may never SUMMON the horizontal scrollbar. The bar is
    decided by the CELL floors alone, so raising a floor is only ever allowed to spend slack that
    already exists above them."""
    ct, _win = _table()
    table = ct.table
    _natural, floors, _caps = ct._column_budget()
    natural, _f, _c = ct._column_budget()
    summoned = []
    for avail in range(sum(floors) - 40, sum(natural) + 40):
        raised = LT.header_floors(table, list(floors), list(natural), avail)
        if sum(raised) > avail and sum(floors) <= avail:
            summoned.append((avail, sum(floors), sum(raised)))
    assert not summoned, (
        "header_floors raised the floors past a viewport the CELL floors fit — it summoned the "
        f"scrollbar it exists to yield to, at {len(summoned)} widths, first {summoned[:3]}")
    print("test_header_floors_never_moves_the_scrollbar_band OK "
          f"({sum(natural) + 80 - sum(floors)} viewport widths swept at 1 px, "
          f"cell floors {sum(floors)} px)")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} CORNER-GRID-BUDGET TESTS PASSED", flush=True)
