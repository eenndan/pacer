"""The exported report's Laps table prints what the app prints (cool/report-laps-precision).

The table was laps.csv's rows verbatim, so every number carried the file's three decimals: on the
shipped demo lap 2 read Dist 960.427 / Entry 93.089 / C1 3.407 · 47.196 where the app's own tables
print 960 / 93.1 / 3.41 / 47.2 — 224 of the demo report's 252 numbers carried digits the app never
shows, and a millimetre distance sat right above the report's own note that the odometer reads
short. `laps_table(..., display=True)` now prints each kind of value at the app's precision, from
the raw values; laps.csv (`display=False`, the default) is byte for byte what it was.

WHAT IS HELD, on real Sessions loaded from synthetic GoPro recordings (the shipped demo; seed 7 of
the demo circuit with three sector lines, for the Splits columns):

  * the demo report's lap 2 row reads the app's figures, and none of the CSV's;
  * CELL BY CELL, in km/h and mph, the report's Laps table against the app's own widgets for the
    same session and unit — the Laps tab (`lap_table.LapTable`: Time, Dist, Entry, the splits) and
    the Corners page (`lap_table.CornerTable`, one lap at a time: each corner's Time and Apex) — so
    the two cannot drift again; the lap time is compared through `fmt_time`, the app's m:ss.mmm;
  * the CSV half is untouched: `display=False` is the 3-decimal table it always was (a byte pin of
    the CSV writer against it is test_export_data's `test_write_laps_csv_matches_table`).

Run: python tests/test_report_laps_precision.py
"""
import os
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()

from studio import export_data  # noqa: E402
from studio._signal import DASH, fmt_time  # noqa: E402
from studio.lap_table import BEST_SECTOR_MARK, LAP_ROLE, CornerTable, LapTable  # noqa: E402

_SESSIONS: dict = {}


def _sessions() -> dict:
    """demo (the shipped demo, no sector lines) and sectors (seed 7, three suggested sector lines).
    Generated once per process; the TemporaryDirectory stays alive with them."""
    if not _SESSIONS:
        from studio.dev import make_demo as md
        from studio.dev import synth_gopro as sg
        from studio.session import Session
        tmp = tempfile.TemporaryDirectory(prefix="report_laps_")
        _SESSIONS["_tmp"] = tmp
        demo = os.path.join(tmp.name, "GX019000.MP4")
        md.make(demo, render=False)
        _SESSIONS["demo"] = Session.load([demo])
        rec = sg.generate(os.path.join(tmp.name, "seed7"), 7, 8, chapters=1, origin=md.DEMO_ORIGIN)
        s = Session.load(rec.paths)
        s.set_timing_lines(s.start_line, s.suggest_sectors(3), user_confirm=False)
        _SESSIONS["sectors"] = s
    return _SESSIONS


def _report_rows(s, unit: str) -> list[list[str]]:
    """The Laps table's body cells, read back out of the written page."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "report.html")
        export_data.write_report_html(path, s, source_label="demo", unit=unit)
        with open(path, encoding="utf-8") as f:
            root = ET.fromstring(f.read())
    (table,) = [t for t in root.iter("table") if t.get("class") == "laps"]
    return [[td.text or "" for td in tr.findall("td")] for tr in table.findall("tr")
            if tr.findall("td")]


def test_the_demo_reports_lap_2_reads_the_apps_figures():
    s = _sessions()["demo"]
    for unit, entry, apex in (("kmh", "93.1", "47.2"), ("mph", "57.8", "29.3")):
        row = dict(zip(export_data.laps_table(s, unit)[0],
                       _report_rows(s, unit)[1], strict=True))
        assert row["lap"] == "2", row
        assert (row["time_s"], row["dist_m"], row[f"entry_{unit}"]) == ("45.806", "960", entry), row
        assert (row["C1_time_s"], row[f"C1_apex_{unit}"]) == ("3.41", apex), row
        text = " ".join(row.values())
        for csv_digits in ("960.427", "93.089", "3.407", "47.196", "57.843", "29.326"):
            assert csv_digits not in text, (csv_digits, row)
    print("ok: the demo report's lap 2 prints 960 / 93.1 / 3.41 · 47.2, not the CSV's digits")


def _app_laps(s, unit: str) -> dict[int, list[str]]:
    """The Laps tab's text per lap id: Time, Dist, Entry, then each split (★ stripped)."""
    t = LapTable(s)
    t.set_speed_unit(unit)
    t.refresh()
    heads = [t.table.horizontalHeaderItem(c).text() for c in range(t.table.columnCount())]
    split_cols = [c for c, h in enumerate(heads) if h[:1] == "S" and h[1:].isdigit()]
    out = {}
    for r in range(t.table.rowCount()):
        lap_id = t.table.item(r, 0).data(LAP_ROLE)
        cells = [t.table.item(r, c).text() for c in (1, 3, 4)]
        cells += [t.table.item(r, c).text().removesuffix(BEST_SECTOR_MARK) for c in split_cols]
        out[lap_id] = cells
    return out


def _app_corners(s, unit: str, lap_id: int) -> list[tuple[str, str]]:
    """The Corners page for one lap: (Time, Apex) per corner, in corner order (★ stripped)."""
    t = CornerTable(s)
    t.set_speed_unit(unit)
    t.set_lap(lap_id)
    return [(t.table.item(r, 1).text().removesuffix(BEST_SECTOR_MARK), t.table.item(r, 3).text())
            for r in range(t.table.rowCount())]


def test_every_cell_is_what_the_apps_tables_print():
    checked = 0
    for name in ("demo", "sectors"):
        s = _sessions()[name]
        for unit in ("kmh", "mph"):
            # The PAGE's cells (read back out of the written report), in laps_table's lap order.
            headers, csv_rows = export_data.laps_table(s, unit)
            rows = list(zip([lap for lap, _c in csv_rows], _report_rows(s, unit), strict=True))
            app = _app_laps(s, unit)
            assert sorted(app) == sorted(lap for lap, _c in rows), (name, unit)
            col = {h: k for k, h in enumerate(headers)}
            splits = [h for h in headers if h[:1] == "S" and h.endswith("_s") and h[1:-2].isdigit()]
            corners = [h.removesuffix("_time_s") for h in headers if h.endswith("_time_s")]
            assert name != "sectors" or len(splits) == 4, headers
            for lap_id, cells in rows:
                time, dist, entry, *app_splits = app[lap_id]
                assert fmt_time(float(cells[col["time_s"]])) == time, (name, lap_id)
                assert cells[col["dist_m"]] == dist, (name, lap_id, cells, dist)
                assert cells[col[f"entry_{unit}"]] == entry, (name, lap_id, cells, entry)
                assert [cells[col[h]] for h in splits] == [x or DASH for x in app_splits], \
                    (name, lap_id)
                page = _app_corners(s, unit, lap_id)
                assert [(cells[col[f"{c}_time_s"]], cells[col[f"{c}_apex_{unit}"]])
                        for c in corners] == page, (name, unit, lap_id, page)
                checked += 3 + len(splits) + 2 * len(corners)
    assert checked > 500, checked
    print(f"ok: {checked} report cells equal the app's Laps tab and Corners page, km/h and mph")


def test_the_csv_table_keeps_three_decimals():
    s = _sessions()["demo"]
    headers, rows = export_data.laps_table(s)
    shown_h, shown = export_data.laps_table(s, None, display=True)
    assert headers == shown_h and [lap for lap, _c in rows] == [lap for lap, _c in shown]
    row = dict(zip(headers, rows[1][1], strict=True))
    assert (row["dist_m"], row["entry_kmh"], row["C1_time_s"], row["C1_apex_kmh"]) == (
        "960.427", "93.089", "3.407", "47.196"), row
    print("ok: laps.csv's table is the 3-decimal one it always was; same rows and columns")


if __name__ == "__main__":
    test_the_demo_reports_lap_2_reads_the_apps_figures()
    test_every_cell_is_what_the_apps_tables_print()
    test_the_csv_table_keeps_three_decimals()
    print("test_report_laps_precision: OK")
