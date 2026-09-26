"""LOOK-8 + VIEW-8 (QA 2026-09-26): what the owner's own layout clipped, crowded or covered.

Measured on his state (a 1440x831 window, his `grid_sizes`, Retina) and at 1280x800, on MK_18_09:

  * the Stats CORNERS table asked 728 px of a 683 px pane (599 at 1280x800), so "Grip (est)" was a
    lone "G" behind a scrollbar — 28 px of every one of its eight headers was the sort arrow's room,
    and only one column ever shows the arrow;
  * the Marks "What" sentence ended in "…" on all seven GPS rows;
  * six ⌘K rows ended in "…", four of them mid-way through the reason the command is off;
  * at 1280x800 the speed legend sat on the lap's top-speed stretch;
  * the map framed 253 m of trace around a 208 m circuit (acquisition junk), and its corner labels
    were separated as 22 x 16 px boxes while each draws 23-30 x 22 px — C1 on C11, both under the
    start line and the playhead, labels under brake glyphs.

(CORNERS BY LAP, which must scroll at these widths, is pinned by
tests/test_stats.py::test_the_corner_grid_keeps_the_lap_and_its_time_in_view_while_it_scrolls.)

Every test here was watched FAIL on the unfixed tree. Run: python tests/test_fits_his_screen.py
"""
import math
import os
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import theme  # noqa: E402

theme.apply_theme(_APP)

# The owner's Stats pane at 1280x800 (the narrower of his two sizes): a report table's viewport.
PANE_1280_PX = 599


def _settle(n=6):
    for _ in range(n):
        _APP.processEvents()


def test_the_corners_table_fits_the_pane_and_only_the_sorted_column_pays_for_the_arrow():
    from studio.stats import CornerReport
    from studio.stats_corners import CornersSection
    rows = [CornerReport(cid=c, direction=1 if c % 2 else -1, n=19, best_s=2.0 + 0.31 * c,
                         median_s=2.2 + 0.31 * c, sigma_s=0.12, median_loss_s=0.13,
                         apex_best_kmh=45.3, apex_median_kmh=43.1, grip_median=0.83,
                         score=0.02 * c, n_laps=19) for c in range(1, 13)]
    sec = CornersSection(lambda _cid: None)
    sec.refresh(SimpleNamespace(corner_report=lambda: rows, phase_report=lambda: None),
                "kmh", "km/h")
    t = sec.table
    t.show()
    _settle()
    assert t.content_width() <= PANE_1280_PX, (
        f"CORNERS asks {t.content_width()} px; the owner's pane at 1280x800 is {PANE_1280_PX}",
        [(t.horizontalHeaderItem(c).text(), t.columnWidth(c)) for c in range(t.columnCount())])
    before = [t.columnWidth(c) for c in range(t.columnCount())]
    t.horizontalHeader().setSortIndicator(5, Qt.DescendingOrder)      # sort by "Apex best"
    _settle()
    after = [t.columnWidth(c) for c in range(t.columnCount())]
    assert after[5] > before[5] and after[0] < before[0], (
        "the sort arrow's room did not follow the sort", before, after)
    assert t.content_width() <= PANE_1280_PX, t.content_width()
    t.hide()
    print(f"test_the_corners_table_fits_the_pane OK ({before} -> {after})")


def test_a_marks_sentence_wraps_instead_of_ending_in_an_ellipsis():
    from studio import chapters, marks
    from studio.marks_panel import COL_NOTE, MarksPanel
    notes = ["GPS poor for 185 s — every fix here was rejected — no 3D lock, or DOP above 10",
             "GPS moderate for 83 s — usable but degrading — DOP above 5, or some fixes here "
             "rejected"]
    stored = [marks._norm_mark({"id": f"q{i}", "chapter": "c", "t": 10.0 * (i + 1),
                                "type": marks.TYPE_TRAFFIC, "colour": "purple", "note": note})
              for i, note in enumerate(notes)]
    panel = MarksPanel()
    panel.set_marks(marks.merge([], marks.resolve(stored, chapters.ChapterMap(["/d/c.MP4"],
                                                                               [500.0]))))
    panel.resize(719, 300)            # the owner's lap panel is 719 px wide
    panel.show()
    _settle()
    t = panel.table
    assert t.rowCount() == 2
    for r in range(t.rowCount()):
        item = t.item(r, COL_NOTE)
        fm = QFontMetrics(item.font())
        width = t.columnWidth(COL_NOTE) - 2 * theme.SPACE_S
        assert fm.horizontalAdvance(item.text()) > width, "the fixture is meant to need wrapping"
        need = fm.boundingRect(QRect(0, 0, width, 10_000), int(Qt.TextWordWrap), item.text())
        assert t.wordWrap() and t.rowHeight(r) >= need.height(), (
            f"row {r} is {t.rowHeight(r)} px for a sentence that needs {need.height()} px wrapped "
            f"(word wrap {t.wordWrap()}): it ends in an ellipsis", item.text())
    panel.hide()
    print("test_a_marks_sentence_wraps_instead_of_ending_in_an_ellipsis OK")


def test_a_palette_row_too_long_for_one_line_takes_two():
    from test_command_palette import _window

    from studio import command_palette
    from studio.command_palette import CommandPalette
    win = _window()                 # no session: most rows are gated and carry their reason
    dlg = CommandPalette(win)
    dlg.show()
    _settle()
    t = dlg.table
    fm = t.fontMetrics()
    col = t.columnWidth(0)
    long_rows = [r for r in range(t.rowCount())
                 if fm.horizontalAdvance(t.item(r, 0).text()) + 2 * theme.SPACE_S > col]
    assert long_rows, "the fixture has no row wider than the title column"
    for r in long_rows:
        text = t.item(r, 0).text()
        assert t.rowHeight(r) >= 2 * fm.height(), (
            f"{text!r} needs {fm.horizontalAdvance(text)} px of a {col} px column and its row is "
            f"one line tall ({t.rowHeight(r)} px): it ends in an ellipsis")
        head, detail = command_palette.split_title(text, fm, col - 2 * theme.SPACE_S)
        assert detail and fm.horizontalAdvance(head) <= col - 2 * theme.SPACE_S, (text, head)
        if " — " in text:
            assert head == text.split(" — ")[0], ("the name did not keep the first line", head)
    dlg.close()
    win.deleteLater()
    print(f"test_a_palette_row_too_long_for_one_line_takes_two OK ({len(long_rows)} rows)")


def _legend_samples_under(v) -> int:
    leg = v._speed_legend
    plate = leg.sceneBoundingRect()
    vb = v.p_speed.getViewBox()
    n = 0
    for xs, ys in v._speed_curves.values():
        for x, y in zip(np.asarray(xs, float), np.asarray(ys, float), strict=True):
            if plate.contains(vb.mapViewToScene(QPointF(x, y))):
                n += 1
    return n


def test_the_speed_legend_never_sits_on_the_trace():
    from test_charts_panel import _StubSession

    from studio import plots_view
    xs = np.linspace(0.0, 1000.0, 400)
    profiles = {
        # flat-out at the END of the lap: the measured-emptiest top-right corner is the full one
        "fast finish": 50.0 + 40.0 * (xs / 1000.0) ** 2,
        # every corner of the plot crossed by the trace: only headroom can clear it
        "everywhere": 60.0 + 30.0 * np.sin(xs / 1000.0 * 24 * math.pi),
    }
    for name, speed in profiles.items():
        laps = {0: (xs, speed, np.linspace(0.0, 0.3, 400))}
        v = plots_view.PlotsView(_StubSession(laps, best=0))
        v.resize(640, 420)              # the charts pane at 1280x800 is about this
        v.show()
        v.set_laps([0])
        _settle(10)
        assert v._speed_legend.isVisible()
        under = _legend_samples_under(v)
        assert under == 0, f"{name}: the legend plate covers {under} of the lap's speed samples"
        v.hide()
        v.deleteLater()
    print("test_the_speed_legend_never_sits_on_the_trace OK")


def _circuit_session(junk=True):
    """A loop circuit + (optionally) a short run of acquisition junk 180 m above it — 2 % of the
    fixes, the MK_18_09 shape — with one valid lap over the loop and the start line on its left."""
    from _synthetic import bare_session
    n = 400
    a = np.linspace(0, 2 * math.pi, n)
    lx, ly = np.cos(a) * 200.0, np.sin(a) * 120.0
    jx, jy = np.linspace(-40.0, 40.0, 8), np.full(8, 300.0)
    xs = np.concatenate([jx, lx]) if junk else lx
    ys = np.concatenate([jy, ly]) if junk else ly
    t = np.arange(len(xs)) * 0.1
    s = bare_session(best=0, valid=[0])
    s._best_cache = 0
    s.tx, s.ty, s.tt, s.tv = xs, ys, t, np.full(len(xs), 50.0)
    line = SimpleNamespace(first=SimpleNamespace(x=-215.0, y=0.0),
                           second=SimpleNamespace(x=-185.0, y=0.0))
    s.laps = SimpleNamespace(sectors=SimpleNamespace(start_line=line, sector_lines=[]))
    s.lap_trace_xy = lambda lid: (lx, ly)
    s.lap_trace_segments = lambda lid: [SimpleNamespace(xs=lx, ys=ly, measured=True)]
    s.lap_channels = lambda lid: {"t_telemetry_s": t[-n:], "x_m": lx, "y_m": ly,
                                  "speed_kmh": np.full(n, 50.0),
                                  "dist_m": np.linspace(0.0, 1000.0, n)}
    s.delta = lambda ids, x_mode="distance": (0, {}, {})
    return s, (lx, ly)


def _sized_map(session, w=713, h=205):
    """The owner's map canvas: 713 x 205 px at his layout."""
    from studio.map_view import MapView
    mv = MapView(session)
    mv.resize(w, h + 30)
    mv.show()
    _settle()
    return mv


def test_the_map_frames_the_circuit_not_the_acquisition_junk():
    s, (lx, ly) = _circuit_session()
    mv = _sized_map(s)
    r = mv.plot.getViewBox().viewRect()
    fill = max((lx.max() - lx.min()) / r.width(), (ly.max() - ly.min()) / r.height())
    # "Within ~10 % margins": the frame is the circuit plus 2 % and a corner label's room a side.
    assert fill >= 0.8 and r.top() + r.height() < 300.0, (
        f"the circuit fills {fill:.0%} of the map's limiting axis — the fit framed the junk too "
        f"(view {r})")
    # ...and a drive that is NOT mostly circuit keeps the whole trace (MAP-04, tests/test_map_fit).
    s.lap_trace_xy = lambda lid: (lx[:40], ly[:40])
    mv._circuit_memo = (None, None)
    mv._fit_view()
    r = mv.plot.getViewBox().viewRect()
    assert r.top() <= s.ty.min() and r.bottom() >= s.ty.max(), "a partial lap dropped the trace"
    mv.hide()
    print(f"test_the_map_frames_the_circuit_not_the_acquisition_junk OK (fill {fill:.0%})")


def test_no_corner_label_covers_another_label_the_start_line_the_playhead_or_a_brake_glyph():
    s, (lx, ly) = _circuit_session(junk=False)
    mv = _sized_map(s)
    # MK_18_09's crowd: C1 and C11 either side of the start line, the playhead parked on it, and a
    # brake glyph where C2's label would naturally go; the rest spread round the loop.
    k = len(lx)
    apex = {"C1": k // 2 + 6, "C11": k // 2 - 6, "C2": k // 2 + 40, "C3": 3 * k // 4,
            "C4": k - 30, "C5": 30, "C6": k // 4}
    markers = [(name, float(lx[i]), float(ly[i]), 1 if j % 2 else -1)
               for j, (name, i) in enumerate(apex.items())]
    mv.set_corners(markers)
    i2 = apex["C2"]
    out = np.array([lx[i2], ly[i2]]) / np.hypot(lx[i2], ly[i2])
    brake = (float(lx[i2] + out[0] * 14.0), float(ly[i2] + out[1] * 14.0), 1.2)
    mv.set_brake_markers([([brake], theme.CHART_SERIES[0])])
    mv.marker.setPos(-200.0, 0.0)
    _settle()
    mv.widget.grab()                           # TextItems refresh their transform at paint
    vb = mv.plot.getViewBox()
    labels = [(t.textItem.toPlainText(), t.sceneBoundingRect()) for t in mv._corner_markers._texts]
    obstacles = [("start handle", mv._start.h1.sceneBoundingRect()),
                 ("start handle", mv._start.h2.sceneBoundingRect()),
                 ("playhead", mv.marker.sceneBoundingRect())]
    for item in mv._brake_markers._items:
        for spot in item.points():
            p, half = vb.mapViewToScene(spot.pos()), spot.size() / 2.0
            obstacles.append(("brake glyph", type(labels[0][1])(p.x() - half, p.y() - half,
                                                                2 * half, 2 * half)))
    p1, p2 = vb.mapViewToScene(mv._start.h1.pos()), vb.mapViewToScene(mv._start.h2.pos())
    hits = []
    for i, (name, rect) in enumerate(labels):
        hits += [(name, other) for other, r in labels[i + 1:] if rect.intersects(r)]
        hits += [(name, what) for what, r in obstacles if rect.intersects(r)]
        if any(rect.contains(QPointF(p1.x() + (p2.x() - p1.x()) * f, p1.y() + (p2.y() - p1.y()) * f))
               for f in np.linspace(0.0, 1.0, 41)):
            hits.append((name, "start line"))
    assert not hits, f"corner labels collide: {hits}"
    mv.hide()
    print(f"test_no_corner_label_covers_another_label... OK ({len(labels)} labels)")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} FITS-HIS-SCREEN TESTS PASSED", flush=True)
