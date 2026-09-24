"""Dump a real `StatsView`'s whole OBSERVABLE state to JSON — the equivalence proof for a refactor
of the Stats page (ARCH-3 splits it into section modules one PR at a time).

WHY THIS EXISTS. The golden gate (`golden_session_dump`) fingerprints `Session` and never imports a
view, so it cannot prove a view refactor harmless (#323 learned that for `app.py`). This builds the
REAL page over a REAL recording and records what a reader can observe: every widget in the page's
tree, in tree order — kind, visibility, geometry, text, tooltip, font, style — every table header
and cell (text, tooltip, sort keys, colour, font), every chart series (hashed), what each
interactive table emits when a row is clicked and how it orders on every header re-sort, and where
`reveal_trust` lands. At five pane widths (the two quadrants and the three dashboard compositions),
in both speed units and both palettes.

Widgets are keyed by their position in the tree and named by their nearest Qt/pyqtgraph class, so a
module move or a class rename is invisible to it while a widget that moved, vanished or changed is
not. Two runs on one tree must compare EQUIVALENT; check that before trusting a before/after pair.

    pixi run python -m studio.dev.stats_view_dump "$TMPDIR/before.json"     # BEFORE the change
    pixi run python -m studio.dev.stats_view_dump "$TMPDIR/after.json"      # AFTER
    pixi run python -m studio.dev.golden_compare "$TMPDIR/before.json" "$TMPDIR/after.json"

The positional argument is the OUTPUT and must be a new `.json` (the same guard as the golden dump).
The recording comes only from PACER_GOLDEN_MP4 (or its default), with its chapters discovered. Every
app-support seam is jailed first, so the page renders the shipped defaults and writes nothing real.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")

import numpy as np  # noqa: E402

# Imported for the bindings path it sets, its output-path guard and its recording preflight.
from studio.dev.golden_session_dump import REAL, _resolve_out_path, preflight  # noqa: E402

SIZES = ((445, 760), (503, 760), (1260, 900), (1420, 900), (1900, 1200))


def _int(e) -> int:
    try:
        return int(e)
    except TypeError:
        return int(e.value)


def _kind(w) -> str:
    """The nearest Qt / pyqtgraph class: stable across a rename or a move of the app's own."""
    for cls in type(w).__mro__:
        if cls.__module__.startswith(("PySide6", "pyqtgraph")):
            return cls.__name__
    return type(w).__name__


def _font(f) -> list:
    return [f.family(), f.pointSizeF(), _int(f.weight()), f.italic()]


def _plain(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, (float, np.floating)):
        return float(v)
    if isinstance(v, np.integer):
        return int(v)
    return repr(v)


def _digest(a) -> list:
    arr = np.round(np.asarray(a if a is not None else [], dtype=float), 9)
    return [int(arr.size), hashlib.sha1(arr.tobytes()).hexdigest()[:16]]


def _brush(item, role) -> str | None:
    b = item.data(role)
    return None if b is None else b.color().name() if hasattr(b, "color") else b.name()


def _table(t, roles) -> dict:
    from PySide6.QtCore import Qt
    head = t.horizontalHeader()
    heads = [t.horizontalHeaderItem(c) for c in range(t.columnCount())]
    cells = []
    for r in range(t.rowCount()):
        row = []
        for c in range(t.columnCount()):
            it = t.item(r, c)
            row.append(None if it is None else [
                it.text(), it.toolTip(), _int(it.textAlignment()), _font(it.font()),
                _brush(it, Qt.ForegroundRole), _brush(it, Qt.BackgroundRole), it.icon().isNull(),
                [_plain(it.data(role)) for role in roles]])
        cells.append(row)
    return {"heads": [None if h is None else [h.text(), _int(h.textAlignment()), h.toolTip()]
                      for h in heads],
            "cells": cells, "sorting": t.isSortingEnabled(),
            "indicator": [head.sortIndicatorSection(), _int(head.sortIndicatorOrder())],
            "select": [_int(t.selectionMode()), _int(t.selectionBehavior())],
            "content_w": getattr(t, "content_width", lambda: None)()}


def _plot(pw) -> dict:
    plot = pw.getPlotItem()
    items = []
    for it in plot.items:
        rec = [type(it).__name__]
        opts = getattr(it, "opts", {}) or {}
        pen = opts.get("pen") if isinstance(opts, dict) else None
        if pen is not None and hasattr(pen, "color"):
            rec.append([pen.color().name(), pen.widthF(), _int(pen.style())])
        if hasattr(it, "getData"):
            x, y = it.getData()
            rec += [_digest(x), _digest(y)]
        elif type(it).__name__ == "BarGraphItem":
            rec += [_digest(opts.get("x")), _digest(opts.get("height")), _plain(opts.get("width"))]
        elif hasattr(it, "value"):
            rec.append(_plain(it.value()))
        items.append(rec)
    axes = {side: [plot.getAxis(side).labelText, repr(plot.getAxis(side)._tickLevels)]
            for side in ("left", "bottom")}
    return {"items": items, "axes": axes,
            "range": np.round(np.asarray(plot.viewRange(), float), 6).tolist()}


def snapshot(view) -> dict:
    """Every widget under `view`, keyed by its position in the tree."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtWidgets import QLabel, QTableWidget, QWidget

    from studio.lap_table import NUM_ROLE
    roles = (NUM_ROLE, NUM_ROLE + 1)   # the sort key and the map-ring cid (RING_ROLE)
    out = {}

    def visit(w, path):
        rec = {"kind": _kind(w), "hidden": w.isHidden(), "visible": w.isVisibleTo(view),
               "name": w.objectName(), "role": str(w.property("role") or ""),
               "highlight": str(w.property("highlight") or ""), "tip": w.toolTip(),
               "a11y": [w.accessibleName(), w.accessibleDescription()], "style": w.styleSheet(),
               "min": [w.minimumWidth(), w.minimumHeight()],
               "max": [w.maximumWidth(), w.maximumHeight()]}
        if rec["visible"]:
            p = w.mapTo(view, QPoint(0, 0))
            rec["geo"] = [p.x(), p.y(), w.width(), w.height()]
        if isinstance(w, QLabel):
            rec["text"] = [w.text(), w.wordWrap(), _int(w.alignment()), _font(w.font())]
        if isinstance(w, QTableWidget):
            rec["table"] = _table(w, roles)
        if hasattr(w, "getPlotItem"):
            rec["plot"] = _plot(w)
        out[path] = rec
        kids = w.findChildren(QWidget, options=Qt.FindDirectChildrenOnly)
        for i, k in enumerate(kids):
            visit(k, f"{path}.{i}")

    visit(view, "0")
    out["page"] = {"layout": repr(view._layout), "tile_cols": repr(view._tile_cols_by_group),
                   "tables": [sorted(t.content_width() for t in g) for g in view._column_tables]}
    return out


def interact(view, app) -> dict:
    """What the page DOES: row clicks → `corner_clicked`, header re-sorts, `reveal_trust`."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QAbstractItemView, QTableWidget

    fired: list = []
    view.corner_clicked.connect(lambda cid: fired.append(_plain(cid)))
    tables = view.findChildren(QTableWidget)
    out = {"clicks": [], "sorts": []}
    for t in tables:
        if t.selectionMode() == QAbstractItemView.NoSelection:
            continue
        got = []
        for r in range(t.rowCount()):
            del fired[:]
            t.selectRow(r)
            got.append(list(fired))
        del fired[:]
        t.clearSelection()
        got.append(list(fired))
        out["clicks"].append(got)
    for t in tables:
        if not t.isSortingEnabled():
            continue
        orders = []
        for c in range(t.columnCount()):
            for order in (Qt.DescendingOrder, Qt.AscendingOrder):
                t.horizontalHeader().setSortIndicator(c, order)
                orders.append([t.item(r, 0).text() if t.item(r, 0) else None
                               for r in range(t.rowCount())])
        t.horizontalHeader().setSortIndicator(0, Qt.AscendingOrder)
        out["sorts"].append(orders)
    from studio.stats_trust import TIMING_TERM
    view.reveal_trust(TIMING_TERM)
    app.processEvents()
    focus = app.focusWidget()
    out["reveal"] = {"scroll": view._scroll.verticalScrollBar().value(),
                     "focus": _kind(focus) if focus is not None else None,
                     "focus_a11y": focus.accessibleName() if focus is not None else None,
                     "snapshot": snapshot(view)}
    view.hide()
    app.processEvents()
    view.show()
    app.processEvents()
    out["after_leave"] = snapshot(view)
    return out


def main():
    out_path = _resolve_out_path(sys.argv)
    fatal = preflight(REAL)
    if fatal:
        print(fatal, file=sys.stderr)
        sys.exit(2)
    from studio.dev import _jail
    jail = _jail.divert_app_support("pacer-statsdump-")
    from PySide6.QtWidgets import QApplication

    from studio import chapters, theme, units
    from studio.session import Session
    from studio.stats_panel import StatsView
    app = QApplication.instance() or QApplication([])
    theme.register_fonts()
    theme.apply_theme(app)
    paths = chapters.discover_siblings(REAL)
    session = Session.load(paths)

    def settle():
        for _ in range(8):
            app.processEvents()

    result: dict = {"recording": [os.path.basename(p) for p in paths],
                    "laps": len(session.valid_lap_ids()), "jail": jail.created}
    view = StatsView(session)
    for unit in (units.KMH, units.MPH):
        view.set_speed_unit(unit)
        for palette in (theme.PALETTE_STANDARD, theme.PALETTE_COLORBLIND):
            theme.set_palette(palette)
            view.refresh()
            for w, h in SIZES:
                view.resize(w, h)
                view.show()
                settle()
                result[f"{unit}|{palette}|{w}x{h}"] = snapshot(view)
    theme.set_palette(theme.PALETTE_STANDARD)
    view.set_speed_unit(units.KMH)
    view.resize(1420, 900)
    settle()
    result["interact"] = interact(view, app)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, sort_keys=True)
    n = sum(1 for k in result if "|" in k)
    print(f"stats_view_dump: {result['recording']} · {result['laps']} laps · {n} states -> {out_path}")


if __name__ == "__main__":
    main()
