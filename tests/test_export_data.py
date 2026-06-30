"""Unit tests for studio.export_data — the F11 CSV + session-report writers.

Driven on bare Sessions (tests/_synthetic seeding idiom — no pacer Laps, no telemetry file,
no Qt) with a small FakeLaps duck-type for the lap-level accessors the writers reach through
Session (lap_rows / sector lines / materialized lat-lon points / the GPS9 wall-clock date).
Covered:
  * laps_table / write_laps_csv — header schema (base + S-splits + per-corner columns),
    one row per valid lap, 3-decimal values matching the Session accessors, the ⚠
    GPS-dropout flag, and the no-sectors/no-corners degenerate schema;
  * write_channels_csv — column schema (incl. the g columns appearing iff a g signal
    exists) and the float-repr EXACT round-trip: re-parsing the file with csv+float
    reproduces every session.lap_channels array bit-for-bit (np.array_equal);
  * write_report_html — well-formed (XML-parsed) page containing the lap count + best-time
    strings and the embedded base64 PNGs (magic bytes + nonzero size).
Run:  python tests/test_export_data.py
"""
import base64
import csv
import datetime
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _synthetic import bare_session, odometer, reset_corner_caches, seed_cols  # noqa: E402

from studio import export_data, gmeter  # noqa: E402
from studio._signal import fmt_time  # noqa: E402
from studio.corners import Corner  # noqa: E402

# A real 1x1 PNG (the report writer just embeds bytes; Qt does the grabbing in the app).
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJ"
    "RU5ErkJggg==")
TS_MS = 1768003200000  # 2026-01-10T00:00:00Z — the FakeLaps GPS9 wall-clock timestamp


class FakeLaps:
    """The lap-level pacer.Laps surface the export path reads through Session: per-lap
    scalars (lap_rows), the sector-line geometry (splits), the materialized per-point
    lat/lon (lap_channels), and the first point's GPS9 timestamp (session_date)."""

    def __init__(self, lap_data, sector_lines=(), first_ts_ms=TS_MS):
        self._laps = lap_data  # {lap_id: {time, dist, entry_mps, lat, lon}}
        self.sectors = SimpleNamespace(sector_lines=list(sector_lines))
        self._first_ts_ms = first_ts_ms

    def lap_time(self, i):
        return self._laps[i]["time"]

    def get_lap_distance(self, i):
        return self._laps[i]["dist"]

    def lap_entry_speed(self, i):
        return self._laps[i]["entry_mps"]

    def sector_count(self):
        return len(self.sectors.sector_lines)

    def laps_count(self):
        return max(self._laps) + 1

    def get_lap(self, i):
        pts = [SimpleNamespace(point=SimpleNamespace(lat=float(a), lon=float(b)))
               for a, b in zip(self._laps[i]["lat"], self._laps[i]["lon"], strict=True)]
        return SimpleNamespace(points=pts)

    def point_count(self):
        return 1

    def get_point(self, _row):
        return SimpleNamespace(point=SimpleNamespace(timestamp_ms=self._first_ts_ms))


def _sector_line(x):
    """A vertical sector line crossing the synthetic straight-line lap (ys = 0) at x."""
    return SimpleNamespace(first=SimpleNamespace(x=float(x), y=-5.0),
                           second=SimpleNamespace(x=float(x), y=5.0))


def make_session(*, with_sectors=True, with_corners=True, with_g=True):
    """A 3-lap bare session: lap 1 is best, lap 2 has an interior GPS dropout (⚠). The
    per-lap arrays come from the shared _synthetic odometer (non-uniform speed, strictly
    increasing), so splits/corner stats are real interpolation, not trivial scaling."""
    spans = {}
    laps_arr = {}
    t0, d0 = odometer(120, 0.1, 10.0, 500.0)
    t1, d1 = odometer(110, 0.1, 25.0, 498.0)
    t2, d2 = odometer(120, 0.1, 40.0, 502.0)
    t2 = t2.copy()
    t2[60:] += 1.0  # interior 1.1 s gap > gapfill.GAP_TIME_S -> a GPS-dropout (⚠) lap
    for lap_id, (t, d) in {0: (t0, d0), 1: (t1, d1), 2: (t2, d2)}.items():
        laps_arr[lap_id] = (t, d)
        spans[lap_id] = float(t[-1] - t[0])

    s = bare_session(laps=laps_arr, best=1, valid=[0, 1, 2])
    for lap_id, (t, d) in laps_arr.items():
        seed_cols(s, lap_id, t, d)
    lap_data = {
        lap_id: {
            "time": spans[lap_id],
            "dist": float(d[-1]),
            "entry_mps": 10.0 + lap_id,
            # Smooth synthetic degrees so float-repr round-trip sees awkward values.
            "lat": 52.0 + d * 1e-5 * np.pi,
            "lon": -0.7 + d * 1.3e-5,
        }
        for lap_id, (t, d) in laps_arr.items()
    }
    s.laps = FakeLaps(lap_data, sector_lines=[_sector_line(250.0)] if with_sectors else [])
    s.track_name = "Daytona MK"
    # Corner caches (CornerModel service, F1): seed the basis (two corners on the best lap's
    # 498 m odometer); the per-lap stats then come from the REAL session.lap_corner_stats
    # projection. reset_corner_caches owns the post-extraction cache slots.
    corner_basis = (
        [Corner(cid=1, enter=100.0, exit=150.0, apex=125.0, direction=1, turn_deg=90.0),
         Corner(cid=2, enter=300.0, exit=380.0, apex=340.0, direction=-1, turn_deg=120.0)],
        float(d1[-1]),
    ) if with_corners else None
    reset_corner_caches(s, basis=corner_basis)
    if with_g:
        gt = np.arange(5.0, 60.0, 0.02)  # covers all three laps at the gmeter's own rate
        s._gmeter = gmeter.GMeter(times=gt, lat_g=np.sin(gt * 1.7) * 1.3,
                                  long_g=np.cos(gt * 0.9) * 0.8, cross=None)
    else:
        s._gmeter = gmeter._empty()
    return s


# ------------------------------------------------------------------------ laps table/CSV
def test_laps_table_schema_and_values():
    s = make_session()
    headers, rows = export_data.laps_table(s)
    assert headers == ["lap", "time_s", "dist_m", "entry_kmh", "flag", "S1_s", "S2_s",
                       "C1_time_s", "C1_apex_kmh", "C2_time_s", "C2_apex_kmh"], headers
    assert [lap_id for lap_id, _ in rows] == [0, 1, 2]
    by_id = dict(rows)
    for r in s.lap_rows():
        cells = by_id[r["idx"]]
        assert cells[1] == f"{r['time']:.3f}"
        assert cells[2] == f"{r['dist']:.3f}"
        assert cells[3] == f"{r['entry']:.3f}"
        splits = s.lap_sector_splits(r["idx"])
        assert cells[5] == f"{splits[0]:.3f}" and cells[6] == f"{splits[1]:.3f}"
        stats = s.corners.lap_corner_stats(r["idx"])
        assert cells[7] == f"{stats[0].time:.3f}"
        assert cells[8] == f"{stats[0].apex_speed:.3f}"
        assert cells[9] == f"{stats[1].time:.3f}"
        assert cells[10] == f"{stats[1].apex_speed:.3f}"
    # The ⚠ flag: exactly the dropout lap (the seeded interior 1.1 s gap in lap 2).
    assert s.dropout_lap_ids() == {2}
    assert by_id[2][4] == export_data.DROPOUT_FLAG
    assert by_id[0][4] == "" and by_id[1][4] == ""


def test_laps_table_degenerate_schema():
    """No sectors + no corners -> just the base columns (mirrors the app's table)."""
    s = make_session(with_sectors=False, with_corners=False)
    headers, rows = export_data.laps_table(s)
    assert headers == ["lap", "time_s", "dist_m", "entry_kmh", "flag"]
    assert len(rows) == 3 and all(len(cells) == 5 for _i, cells in rows)


def test_write_laps_csv_matches_table():
    s = make_session()
    headers, rows = export_data.laps_table(s)
    n_data = len(rows)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "laps.csv")
        export_data.write_laps_csv(path, s)
        with open(path, newline="", encoding="utf-8") as f:
            got = list(csv.reader(f))
    assert got[0] == headers
    # The lap rows: one per lap shown in the table, byte-identical to laps_table().
    assert got[1:1 + n_data] == [cells for _lap_id, cells in rows]
    assert n_data == len(s.valid_lap_ids())
    # The summary trailer: a blank separator row, a `summary,time_s` mini-header, then one
    # labeled row per SUMMARY_ROWS value — EXACTLY equal to the Session accessors (3-dec).
    assert got[1 + n_data] == []  # blank separator between lap rows and trailer
    assert got[2 + n_data] == [export_data.SUMMARY_MARKER, "time_s"]
    trailer = got[3 + n_data:]
    assert len(trailer) == len(export_data.SUMMARY_ROWS)
    for (label, accessor), row in zip(export_data.SUMMARY_ROWS, trailer, strict=True):
        assert row[0] == f"{export_data.SUMMARY_MARKER}: {label}"
        v = getattr(s, accessor)()
        assert row[1] == (f"{v:.3f}" if v is not None else "")
    # theoretical <= rolling <= best lap time holds on the synthetic session too.
    th, ro = s.theoretical_best(), s.best_rolling_lap()
    best = s.lap_time(s.best_lap_id())
    assert th is not None and ro is not None
    assert th <= ro + 1e-9 and ro <= best + 1e-9


# ------------------------------------------------------------------------- channels CSV
def test_channels_csv_roundtrip_exact():
    s = make_session()
    cols = s.lap_channels(0)
    assert list(cols) == ["t_media_s", "elapsed_s", "lat_deg", "lon_deg", "x_m", "y_m",
                          "dist_m", "speed_mps", "speed_kmh", "g_long", "g_lat"]
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "channels.csv")
        export_data.write_channels_csv(path, s, 0)
        with open(path, newline="", encoding="utf-8") as f:
            r = csv.reader(f)
            header = next(r)
            data = [[float(v) for v in row] for row in r]
    assert header == list(cols)
    parsed = np.asarray(data, dtype=float)
    assert parsed.shape == (len(cols["t_media_s"]), len(header))
    for j, name in enumerate(header):  # float-repr round-trip: EXACT, not approximate
        assert np.array_equal(parsed[:, j], cols[name]), f"column {name} not exact"


def test_channels_csv_without_g_signal():
    s = make_session(with_g=False)
    cols = s.lap_channels(1)
    assert "g_long" not in cols and "g_lat" not in cols
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "channels.csv")
        export_data.write_channels_csv(path, s, 1)
        with open(path, newline="", encoding="utf-8") as f:
            header = next(csv.reader(f))
    assert header == list(cols) and header[-1] == "speed_kmh"


# ------------------------------------------------------------------------------- report
def test_report_html_wellformed_with_images():
    s = make_session()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "report.html")
        export_data.write_report_html(
            path, s, source_label="GX010060",
            images=[("Track map", PNG_1PX), ("Speed", PNG_1PX)])
        with open(path, encoding="utf-8") as f:
            text = f.read()
    root = ET.fromstring(text)  # well-formed (the writer emits XML-parseable markup)
    body_text = "".join(root.itertext())
    assert f"Laps ({len(s.valid_lap_ids())})" in body_text
    assert fmt_time(s.lap_time(s.best_lap_id())) in body_text  # the best-time string
    assert "GX010060" in body_text and "Daytona MK" in body_text
    expected_date = datetime.datetime.fromtimestamp(
        TS_MS / 1000.0, tz=datetime.UTC).strftime("%Y-%m-%d")
    assert s.session_date() == expected_date and expected_date in body_text
    # One table row per lap (+1 header row) in the laps table (the second <table>).
    tables = root.findall(".//table")
    assert len(tables[1].findall("tr")) == len(s.valid_lap_ids()) + 1
    # Both images embedded as base64 data URIs that decode back to real PNG bytes.
    imgs = root.findall(".//img")
    assert len(imgs) == 2
    for img in imgs:
        src = img.get("src")
        assert src.startswith("data:image/png;base64,")
        png = base64.b64decode(src.split(",", 1)[1])
        assert png.startswith(b"\x89PNG\r\n\x1a\n") and len(png) > 0
        assert png == PNG_1PX


def test_report_html_no_images_no_corners():
    s = make_session(with_sectors=False, with_corners=False, with_g=False)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "report.html")
        export_data.write_report_html(path, s, source_label="x & y")  # escaping exercised
        with open(path, encoding="utf-8") as f:
            root = ET.fromstring(f.read())
    assert not root.findall(".//img")
    assert "x & y" in "".join(root.itertext())


if __name__ == "__main__":
    test_laps_table_schema_and_values()
    test_laps_table_degenerate_schema()
    test_write_laps_csv_matches_table()
    test_channels_csv_roundtrip_exact()
    test_channels_csv_without_g_signal()
    test_report_html_wellformed_with_images()
    test_report_html_no_images_no_corners()
    print("test_export_data: OK")
