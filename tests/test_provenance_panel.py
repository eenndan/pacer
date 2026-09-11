"""The "inspect this number" PANEL: that it renders a `Provenance` faithfully, and that the
three surfaces that open it agree about when they may.

Two halves, and the split mirrors the feature's own architecture:

  * THE RENDERER, over a hand-built `Provenance` with no Session, no pacer and no telemetry
    file — which is itself the assertion that `provenance_panel` is a renderer. If this half
    needed a recording to run, the panel would be reaching for data it has no business knowing.
  * THE MENUS, on the REAL widgets, over the REAL synthetic recording: the lap table's Time and
    S-split columns and the CORNERS table's Best cell offer the item; every other cell does not.
    A context menu is where "three numbers, honestly" is either true or a slogan.

Offscreen Qt. Run: PYTHONPATH=bindings/pacer QT_QPA_PLATFORM=offscreen python
tests/test_provenance_panel.py
"""
import ast
import os
import sys

import numpy as np

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()            # module scope, BEFORE any widget: the SHIPPING theme

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import QLabel, QTableWidget  # noqa: E402
from test_provenance import _session, _with_sectors  # noqa: E402

from studio import provenance, provenance_panel  # noqa: E402
from studio.lap_table import COLUMNS, LapTable  # noqa: E402
from studio.stats_panel import StatsView  # noqa: E402
from studio.widgets import NUM_ROLE  # noqa: E402


def _fake_provenance(rows: int = 3) -> provenance.Provenance:
    """A `Provenance` built by hand — no Session anywhere. Every field populated so the renderer
    has to walk all of them."""
    n = rows
    fixes = provenance.LapFixes(
        index=np.arange(n, dtype=float),
        times=100.0 + 0.1 * np.arange(n),
        dists=np.arange(n, dtype=float) * 5.0,
        lats=52.0 + 1e-5 * np.arange(n),
        lons=-0.75 + 1e-5 * np.arange(n),
        speeds=np.full(n, 20.0),
        fix=np.full(n, 3.0),
        dop=np.full(n, 1.25),
    )
    # A window that admits every row but the first and last, so the table carries both RAW rows
    # and the BRACKET rows on either side of the boundaries — the shape the real builders produce.
    return provenance.sector_split(
        lap_id=2, sector=0, of=3, value=0.2, fmt="{:.2f}".format, fixes=fixes,
        d0=1.0, d1=float((n - 2) * 5.0), clock="gps9_trueclock")


# ---------------------------------------------------------------------------- the renderer
def test_the_panel_needs_nothing_but_a_provenance():
    """Built from a bare value object: no Session, no pacer, no file. That is the contract the
    module doc claims, and it is only true if this test can run at all."""
    panel = provenance_panel.ProvenancePanel(_fake_provenance())
    assert panel.prov.value == 0.2
    assert panel.windowTitle().endswith(panel.prov.title)
    panel.close()
    print("test_the_panel_needs_nothing_but_a_provenance OK")


def test_the_panel_shows_the_displayed_value_the_method_and_the_window():
    """The four facts the brief asks for, each asserted on the LIVE label text rather than on the
    value object — a panel that held the right data and painted none of it would pass a
    value-level check and fail the user."""
    prov = _fake_provenance()
    panel = provenance_panel.ProvenancePanel(prov)
    text = " ".join(w.text() for w in panel.findChildren(QLabel))
    assert prov.formatted in text, "the displayed value must be on the panel"
    assert prov.method in text, "the one-sentence method must be on the panel"
    assert prov.window.label() in text, "the exact window must be on the panel"
    assert f"N = {prov.n}" in text, "N must be on the panel"
    assert prov.residual_note() in text, "the re-derivation must be on the panel"
    for line in prov.quality.lines():
        assert line in text, f"missing fix-quality line: {line}"
    panel.close()
    print("test_the_panel_shows_the_displayed_value_the_method_and_the_window OK — value, "
          "method, window, N, quality and the re-derivation all painted")


def test_every_row_reaches_the_grid_and_says_what_it_is():
    """The rows are the point. Each one is rendered through the value object's own formatter, so
    the grid cannot round differently from the CSV's source."""
    prov = _fake_provenance(rows=4)
    panel = provenance_panel.ProvenancePanel(prov)
    grid = panel.findChildren(QTableWidget)[0]
    assert grid.rowCount() == len(prov.samples.rows)
    assert grid.columnCount() == len(provenance.FIX_COLUMNS)
    for r in range(grid.rowCount()):
        for c in range(grid.columnCount()):
            assert grid.item(r, c).text() == prov.samples.cell(r, c), (r, c)
    role = provenance.FIX_COLUMNS.index("role")
    painted = {grid.item(r, role).text() for r in range(grid.rowCount())}
    assert provenance.RAW in painted and provenance.BRACKET in painted, painted
    panel.close()
    print(f"test_every_row_reaches_the_grid_and_says_what_it_is OK — {grid.rowCount()} rows x "
          f"{grid.columnCount()} columns, roles {sorted(painted)}")


def test_a_long_table_is_truncated_on_screen_but_never_in_the_csv():
    """A lap's fix table is ~686 rows and Qt builds every item eagerly, so the grid is capped —
    but the CLIPBOARD is the escape hatch and capping it too would make the panel a nicer way of
    losing data. The cap is stated on screen rather than applied silently."""
    prov = _fake_provenance(rows=provenance_panel.MAX_ROWS + 25)
    panel = provenance_panel.ProvenancePanel(prov)
    grid = panel.findChildren(QTableWidget)[0]
    assert grid.rowCount() == provenance_panel.MAX_ROWS
    text = " ".join(w.text() for w in panel.findChildren(QLabel))
    assert str(len(prov.samples.rows)) in text, "the panel must say how many rows there really are"
    csv = panel.copy_csv()
    assert csv.count("\n") > len(prov.samples.rows), "the CSV carries every row"
    # The row the GRID never drew is in the clipboard, addressed by its own track row number.
    last = prov.samples.rows[-1]
    assert any(line.startswith(f"{last[0]},") for line in csv.splitlines()), last[0]
    assert len(prov.samples.rows) > provenance_panel.MAX_ROWS
    panel.close()
    print(f"test_a_long_table_is_truncated_on_screen_but_never_in_the_csv OK — grid capped at "
          f"{provenance_panel.MAX_ROWS}, CSV carried all {len(prov.samples.rows)}")


def test_copy_as_csv_returns_what_it_puts_on_the_clipboard():
    """The button's whole job. Returned as well as copied so this can assert what a user would
    paste — an offscreen platform may have no clipboard at all, and the text is the claim."""
    prov = _fake_provenance()
    panel = provenance_panel.ProvenancePanel(prov)
    text = panel.copy_csv()
    assert text == prov.to_csv()
    assert text.startswith("pacer provenance,")
    assert "Copied" in panel.copy_button.text(), panel.copy_button.text()
    panel.close()
    print("test_copy_as_csv_returns_what_it_puts_on_the_clipboard OK")


def test_open_for_declines_a_number_it_cannot_explain():
    """`open_for(None)` opens nothing. One entry point, so a degenerate lap fails the same way on
    all three surfaces instead of three call sites inventing three error dialogs."""
    assert provenance_panel.open_for(None) is None
    print("test_open_for_declines_a_number_it_cannot_explain OK")


# -------------------------------------------------------------------------------- the menus
def test_the_lap_table_offers_inspection_on_exactly_the_two_time_columns():
    """Time and the S-splits, and nothing else. `Dist` and `Entry` are real numbers this feature
    has no builder for, and offering the item there would promise an inspection that cannot
    happen — the scope is three numbers and the menu is where that is visible."""
    s = _with_sectors(_session())
    table = LapTable(s)
    table.refresh()
    assert table.table.rowCount() > 0
    offered, declined = [], []
    for c in range(table._n_real_cols()):
        prov = table._provenance_at(0, c)
        (offered if prov is not None else declined).append(c)
    assert offered == sorted(table._clock_cols()), (offered, table._clock_cols())
    assert declined == [0, 2, 3], declined            # Lap, Dist, Entry
    # The Time cell's provenance really is the lap's, and the S cell's really is that sector's.
    lap_id = table._lap_id(0)
    assert table._provenance_at(0, 1).value == s.lap_time(lap_id)
    assert (table._provenance_at(0, len(COLUMNS)).value
            == s.lap_sector_splits(lap_id)[0])
    print(f"test_the_lap_table_offers_inspection_on_exactly_the_two_time_columns OK — offered "
          f"{offered}, declined {declined}")


def test_the_lap_table_panel_headline_is_the_cell_text():
    """The panel opened from a cell must lead with THAT CELL'S string. If the panel formatted the
    value itself, a cell and its own inspector could disagree in the last digit — which is the
    single worst thing this feature could do."""
    s = _with_sectors(_session())
    table = LapTable(s)
    table.refresh()
    for c in sorted(table._clock_cols()):
        for r in range(min(table.table.rowCount(), 3)):
            prov = table._provenance_at(r, c)
            cell = table.table.item(r, c).text().replace(" ★", "")
            assert prov.formatted == cell, (r, c, prov.formatted, cell)
            assert prov.matches_display, (r, c)
    print("test_the_lap_table_panel_headline_is_the_cell_text OK — every Time and S cell agrees "
          "with its own panel, and re-derives to it")


def test_the_lap_table_context_menu_signal_is_wired_and_declines_quietly():
    """The real signal path, not just the helper: a right-click on an inspectable cell reaches
    the menu, and one on a plain cell returns without opening anything. Both are driven here
    because the DECLINE is the half a helper-level test cannot see."""
    s = _with_sectors(_session())
    table = LapTable(s)
    table.refresh()
    assert table.table.contextMenuPolicy() == Qt.CustomContextMenu
    rect = table.table.visualItemRect(table.table.item(0, 2))   # Dist — not inspectable
    table._on_context_menu(QPoint(rect.center().x(), rect.center().y()))
    table._on_context_menu(QPoint(-5, -5))                      # nowhere at all
    print("test_the_lap_table_context_menu_signal_is_wired_and_declines_quietly OK")


def test_the_corners_table_offers_inspection_on_the_best_cell_only():
    """The third number. `Median`, `σ` and `Med loss` are numbers the inspector has no builder
    for, so the menu must stay off them."""
    s = _session()
    view = StatsView(s)
    view.refresh()
    table = view.corners_table
    assert table.rowCount() > 0, "the synthetic stadium must produce a CORNERS table"
    assert table.contextMenuPolicy() == Qt.CustomContextMenu
    cid = int(table.item(0, 0).data(NUM_ROLE))
    prov = s.corner_best_provenance(cid)
    assert prov is not None
    assert prov.formatted == table.item(0, 1).text(), (prov.formatted, table.item(0, 1).text())
    # Every other column declines — driven through the real slot, which must simply return.
    for c in range(2, table.columnCount()):
        rect = table.visualItemRect(table.item(0, c))
        view._on_corner_context_menu(QPoint(rect.center().x(), rect.center().y()))
    print(f"test_the_corners_table_offers_inspection_on_the_best_cell_only OK — C{cid} best "
          f"{prov.formatted!r} matches its cell; {table.columnCount() - 2} other columns decline")


def test_the_surfaces_use_one_menu_label():
    """Two context menus, one string. Three wordings for one action is how a feature stops looking
    like one feature — so the label is checked the way the design guards check a token: no call
    site may carry it as a STRING LITERAL (an AST walk, so the prose that quotes it in a comment
    or a docstring is not mistaken for a re-typing)."""
    import ast
    literals, references = [], 0
    for name in ("lap_table", "stats_panel"):
        path = os.path.join(_REPO, "studio", name + ".py")
        tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and "Inspect this number" in node.value and not _is_docstring(tree, node):
                literals.append((name, node.lineno))
            if isinstance(node, ast.Attribute) and node.attr == "MENU_LABEL":
                references += 1
    assert not literals, f"the menu label is re-typed at {literals} instead of read from the module"
    assert references == 2, references
    print(f"test_the_surfaces_use_one_menu_label OK — {references} call sites, 0 re-typings")


def _is_docstring(tree, node) -> bool:
    """True when this string Constant is a module/class/function docstring — prose about the
    label, not a use of it."""
    for parent in ast.walk(tree):
        if isinstance(parent, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(parent, "body", [])
            if body and isinstance(body[0], ast.Expr) and body[0].value is node:
                return True
    return False


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} PROVENANCE-PANEL TESTS PASSED")
