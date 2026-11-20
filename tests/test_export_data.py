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
import html
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _synthetic import (  # noqa: E402
    bare_session,
    odometer,
    reset_corner_caches,
    seed_cols,
    seed_corner_basis,
    seed_trace,
)

from studio import export_data, gmeter, units  # noqa: E402
from studio import stats as stats_service  # noqa: E402
from studio._signal import DASH, fmt_time  # noqa: E402
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
        self._laps = lap_data  # {lap_id: {time, dist, entry_mps, lat, lon, t0}}
        self.sectors = SimpleNamespace(sector_lines=list(sector_lines))
        self._first_ts_ms = first_ts_ms

    def lap_time(self, i):
        return self._laps[i]["time"]

    def start_timestamp(self, i):
        """The lap's TELEMETRY-clock start — `Session.lap_window`'s first half, which
        `SessionStats.lap_stats` slices the g-meter by. Needed since the HTML report grew the
        Stats groups (N13): the report now reaches the same per-lap g/brake reductions the
        Stats page does, and those go through lap_window."""
        return self._laps[i]["t0"]

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


def make_stitched_session(*, n_sector_lines=0):
    """A 3-lap session whose IDEAL IS GENUINELY STITCHED, for the trailer's summary gate.

    `make_session` below is the opposite case and neither obviously nor accidentally: its lap 1 is
    quickest everywhere, so one lap wins every segment of the corner partition and the "ideal" IS
    that lap. Here the pace CROSSES — lap 1 is fast early / slow late, lap 0 the reverse — so more
    than one lap donates, `ideal_donor_lap_id()` is None, and the ideal is strictly faster than any
    lap driven. `n_sector_lines` exists to prove the summary gate does not read it."""
    def slow_fast(u):
        return 0.8 + 1.4 * np.sin(u / 2)

    def fast_slow(u):
        return 2.2 - 1.4 * np.sin(u / 2)

    t0, d0 = odometer(122, 0.1, 100.0, 1000.0, slow_fast)
    t1, d1 = odometer(120, 0.1, float(t0[-1]), 1000.0, fast_slow)
    t2, d2 = odometer(118, 0.1, float(t1[-1]), 1000.0)
    laps_arr = {0: (t0, d0), 1: (t1, d1), 2: (t2, d2)}
    s = bare_session(laps=laps_arr, best=2, valid=[0, 1, 2])
    for lap_id, (t, d) in laps_arr.items():
        seed_cols(s, lap_id, t, d)
    seed_trace(s, laps_arr)  # SessionStats is wired over tt/tv/tx/ty (the report's stats groups)
    s.laps = FakeLaps(
        {lap_id: {"time": float(t[-1] - t[0]), "dist": float(d[-1]), "t0": float(t[0]),
                  "entry_mps": 10.0 + lap_id, "lat": 52.0 + d * 1e-5 * np.pi,
                  "lon": -0.7 + d * 1.3e-5}
         for lap_id, (t, d) in laps_arr.items()},
        sector_lines=[_sector_line(200.0 + 150.0 * i) for i in range(n_sector_lines)])
    s.track_name = "Daytona MK"
    seed_corner_basis(s, ((200.0, 300.0), (600.0, 750.0)), 1000.0)
    s._gmeter = gmeter._empty()
    return s


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
    seed_trace(s, laps_arr)  # SessionStats is wired over tt/tv/tx/ty (the report's stats groups)
    lap_data = {
        lap_id: {
            "time": spans[lap_id],
            "dist": float(d[-1]),
            "t0": float(t[0]),
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
    # `quality` and, after it, C5's `corners_interpolated` are APPENDED last; every column before
    # them is unchanged, which is the point of appending rather than inserting beside `flag`.
    assert headers == ["lap", "time_s", "dist_m", "entry_kmh", "flag", "S1_s", "S2_s",
                       "C1_time_s", "C1_apex_kmh", "C2_time_s", "C2_apex_kmh",
                       "quality", "corners_interpolated"], headers
    assert [lap_id for lap_id, _ in rows] == [0, 1, 2]  # internal 0-based lap ids (row keys)
    by_id = dict(rows)
    for r in s.lap_rows():
        cells = by_id[r["idx"]]
        # M9: the exported `lap` column is the 1-based lap NUMBER (id + 1), matching the app's
        # Lap column; the row key stays the 0-based id (asserted just above).
        assert cells[0] == str(r["idx"] + 1)
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


def test_the_display_table_prints_the_apps_precision_from_the_raw_values():
    """cool/report-laps-precision: `display=True` (the report's copy) prints each kind of value at
    the app's precision — lap time 3, distance 0, speeds 1, splits and corner times PRINT_DECIMALS —
    and a missing number as the app's dash, while the default (laps.csv's) stays 3 decimals and
    blank. It rounds the RAW value once: a 3.4051 split is "3.405" in the file, and "3.405" read
    back and rounded again would print "3.40" where the app prints "3.41"."""
    s = make_session()
    real_splits, real_stats = s.lap_sector_splits, s.corners.lap_corner_stats
    s.lap_sector_splits = lambda i: [3.4051] if i == 0 else real_splits(i)   # a partial lap
    s.corners.lap_corner_stats = lambda i: [] if i == 0 else real_stats(i)   # no corner stats
    for unit in (None, "mph"):
        headers, csv_rows = export_data.laps_table(s, unit)
        shown_h, shown = export_data.laps_table(s, unit, display=True)
        assert shown_h == headers and [i for i, _ in shown] == [i for i, _ in csv_rows]
        col = {h: k for k, h in enumerate(headers)}
        sfx = "kmh" if unit is None else "mph"
        assert dict(csv_rows)[0][col["S1_s"]] == "3.405" and dict(shown)[0][col["S1_s"]] == "3.41"
        for h in ("S2_s", "C1_time_s", f"C1_apex_{sfx}", "C2_time_s", f"C2_apex_{sfx}"):
            assert dict(csv_rows)[0][col[h]] == "" and dict(shown)[0][col[h]] == DASH, h
        for r in s.lap_rows():
            cells = dict(shown)[r["idx"]]
            assert cells[col["time_s"]] == f"{r['time']:.3f}"
            assert cells[col["dist_m"]] == f"{r['dist']:.0f}"
            assert cells[col[f"entry_{sfx}"]] == f"{units.convert_speed(r['entry'], sfx):.1f}"
            if r["idx"]:
                st = real_stats(r["idx"])
                assert cells[col["C2_time_s"]] == f"{st[1].time:.2f}"
                assert cells[col[f"C2_apex_{sfx}"]] == \
                    f"{units.convert_speed(st[1].apex_speed, sfx):.1f}"
            # The non-numeric cells are the file's own.
            for h in ("lap", "flag", "quality", export_data.INTERPOLATED_COLUMN):
                assert cells[col[h]] == dict(csv_rows)[r["idx"]][col[h]], h
    print("ok: the report's laps cells print the app's precision once, from the raw value")


def test_laps_table_degenerate_schema():
    """No sectors + no corners -> just the base columns (mirrors the app's table)."""
    s = make_session(with_sectors=False, with_corners=False)
    headers, rows = export_data.laps_table(s)
    assert headers == ["lap", "time_s", "dist_m", "entry_kmh", "flag", "quality",
                       "corners_interpolated"]
    assert len(rows) == 3 and all(len(cells) == 7 for _i, cells in rows)
    # …and the appended column still reports the fixture's one dropout lap ([u]), because the
    # marks follow the SESSION's verdicts and not the presence of sector/corner columns.
    assert dict(rows)[2][5] == "[u]" and dict(rows)[0][5] == "" and dict(rows)[1][5] == ""
    # With no corner partition there is nothing that COULD be interpolated, so the C5 column is
    # present (the schema is not conditional) and empty on every row.
    assert all(cells[6] == "" for _i, cells in rows)


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
    # labeled row per laps_summary() value — EXACTLY equal to the Session accessors (3-dec).
    # laps_summary is the writer's own source, and it GATES the theoretical row (see the gate
    # test below), so the expectation is taken from it rather than from SUMMARY_ROWS.
    assert got[1 + n_data] == []  # blank separator between lap rows and trailer
    # The mini-header grew two columns for §5.4's disclosure (over_laps / note); columns 0 and 1
    # are byte-identical to what they always were — see SummaryRow on why the label is pinned.
    assert got[2 + n_data] == [export_data.SUMMARY_MARKER, "time_s", "over_laps", "note"]
    # The trailer holds the summary VALUES and, after them, the quality KEY decoding whatever
    # codes the lap rows carry (this fixture's lap 2 has a GPS dropout, so [u] is in it). Split
    # the two: the key is not a summary row and must not be zipped against one.
    rest = got[3 + n_data:]
    key_rows = [r for r in rest if r[0].startswith(f"{export_data.SUMMARY_MARKER}: quality ")]
    # …and the dist_m column's note (TRUTH2-ODO): the odometer reads short, said once per file.
    dist_label = f"{export_data.SUMMARY_MARKER}: {export_data.DIST_COLUMN}"
    dist_note = [r for r in rest if r[0] == dist_label]
    assert dist_note == [[f"{export_data.SUMMARY_MARKER}: dist_m", "", "",
                          stats_service.ODOMETER_NOTE]], dist_note
    trailer = [r for r in rest if r not in key_rows and r not in dist_note]
    assert [r[0] for r in key_rows] == [f"{export_data.SUMMARY_MARKER}: quality [u]"], key_rows
    summary = export_data.laps_summary(s)
    assert [r.label for r in summary] == ["Best rolling"], (
        "this fixture's lap 1 is quickest in every segment, so its ideal is that lap and the "
        "trailer withholds it")
    assert len(trailer) == len(summary)
    for r, row in zip(summary, trailer, strict=True):
        assert row == [f"{export_data.SUMMARY_MARKER}: {r.label}", r.value, r.over_laps, r.note]
    accessors = dict(export_data.SUMMARY_ROWS)
    for r in summary:
        v = getattr(s, accessors[r.label])()
        assert r.value == (f"{v:.3f}" if v is not None else "")
    # theoretical <= rolling <= best lap time holds on the synthetic session too.
    th, ro = s.theoretical_best(), s.best_rolling_lap()
    best = s.lap_time(s.best_lap_id())
    assert th is not None and ro is not None
    assert th <= ro + 1e-9 and ro <= best + 1e-9


def test_the_csv_discloses_an_interpolated_corner_cell_and_keeps_every_row():
    """C5 — laps.csv is an EXTERNAL FORMAT, so the rule arrives as a column, not as a deletion.

    Since C4 the app's own corner surfaces count only cells matched on track at both edges; the
    exported `C*_time_s` / `C*_apex_*` columns counted every one. Dropping or blanking them would
    break a reader diffing this week's file against last week's, so every value is still written
    and a `corners_interpolated` column names the corners whose window was a guess — with a
    trailer row (and a note on the HTML report) saying what that means, derived from the ROWS so
    it can never appear on a file with nothing to explain.

    Measured on the owner's D24 recordings: 34 of the 0060 pair's 456 exported corner cells are
    interpolated, over 16 of its 38 laps; 0 of 0062's 780."""
    s = make_session()
    n_corners = len(s.corners.corner_list())
    real = s.corners.lap_corner_resolved

    def with_flips(flips):
        """`flips` = {(lap_id, corner index)} planted as interpolated."""
        s.corners.lap_corner_resolved = lambda lap: [
            False if (lap, k) in flips else ok for k, ok in enumerate(real(lap))]

    try:
        # 1. Nothing to disclose: the column is there, empty, and neither legend appears.
        headers, rows = export_data.laps_table(s)
        col = headers.index(export_data.INTERPOLATED_COLUMN)
        assert col == len(headers) - 1, "the disclosure must be the LAST column"
        assert all(cells[col] == "" for _i, cells in rows), rows
        assert not export_data.any_interpolated(headers, rows)

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "clean.csv")
            export_data.write_laps_csv(path, s)
            body = open(path, encoding="utf-8").read()
        assert export_data.INTERPOLATED_COLUMN in body.splitlines()[0]
        assert f"{export_data.SUMMARY_MARKER}: {export_data.INTERPOLATED_COLUMN}" not in body, (
            "a file with no interpolated cell carries a legend for one")

        # 2. Plant lap 0's C2 and lap 2's C1 + C2. Every numeric cell is UNCHANGED — that is the
        # whole promise of a disclosure column — and the new cell names exactly the right corners.
        before = {lap_id: list(cells[:col]) for lap_id, cells in rows}
        with_flips({(0, 1), (2, 0), (2, 1)})
        headers, rows = export_data.laps_table(s)
        by_id = dict(rows)
        assert {lap_id: list(cells[:col]) for lap_id, cells in rows} == before, (
            "a value moved when only the disclosure should have")
        assert by_id[0][col] == "C2", by_id[0][col]
        assert by_id[1][col] == "", by_id[1][col]
        assert by_id[2][col] == "C1 C2", by_id[2][col]
        assert export_data.any_interpolated(headers, rows)
        # The C2 time and apex cells are still written on the planted laps, not blanked.
        c2_time = headers.index("C2_time_s")
        assert by_id[0][c2_time] and by_id[2][c2_time], (by_id[0][c2_time], by_id[2][c2_time])
        assert n_corners == 2

        # 3. …and both writers explain it: a trailer row in the CSV, a note under the HTML table.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "laps.csv")
            export_data.write_laps_csv(path, s)
            got = list(csv.reader(open(path, newline="", encoding="utf-8")))
            html_path = os.path.join(tmp, "report.html")
            export_data.write_report_html(html_path, s)
            page = open(html_path, encoding="utf-8").read()
        legend = [r for r in got
                  if r and r[0] == f"{export_data.SUMMARY_MARKER}: {export_data.INTERPOLATED_COLUMN}"]
        assert len(legend) == 1 and legend[0][3] == export_data.INTERPOLATED_NOTE, legend
        assert len(legend[0]) == 4, "the trailer stopped being rectangular"
        assert export_data.REPORT_INTERPOLATED_HEAD in page and "interpolated between" in page
        assert html.escape(export_data.REPORT_INTERPOLATED_NOTE) in page
        assert "<td>C1 C2</td>" in page, "the report's table lost the disclosure cell"
    finally:
        s.corners.lap_corner_resolved = real
    print("ok laps.csv: every corner value kept, the interpolated ones named, and a legend only "
          "where there is something to explain")


def test_laps_summary_gate_is_the_ideal_not_the_sector_count():
    """The trailer withholds "Theoretical best" exactly when the ideal is a DUPLICATE of a lap the
    driver drove — one lap winning every segment — and not one row earlier or later.

    The gate used to be `sector_count() == 0`, inherited from the era when the theoretical best was
    the sum of the session-best sector splits and a track with no sector line collapsed to a single
    sub-sector whose split is the best lap time. That gate is not merely stale, it was INVERTED on
    the recordings that matter: D24 and Sandown both carry ZERO sector lines, so the export dropped
    the row on exactly the sessions where the ideal is now faster than anything driven — by 1.37 s
    on D24 one chapter and 1.49 s on three.

    So: a stitched ideal publishes the row with NO sector lines, a degenerate one withholds it WITH
    sector lines, and adding lines to either changes nothing."""
    for n_lines in (0, 2):
        stitched = make_stitched_session(n_sector_lines=n_lines)
        assert stitched.ideal_donor_lap_id() is None
        assert stitched.sector_count() == n_lines
        labels = [r.label for r in export_data.laps_summary(stitched)]
        assert labels == ["Theoretical best", "Best rolling"], (n_lines, labels)
        # ...and the value published is the ideal itself, strictly faster than the best lap.
        row = {r.label: r for r in export_data.laps_summary(stitched)}["Theoretical best"]
        assert row.value == f"{stitched.theoretical_best():.3f}"
        assert stitched.theoretical_best() < stitched.lap_time(stitched.best_lap_id()) - 1e-3

    for with_sectors in (False, True):
        one_donor = make_session(with_sectors=with_sectors)
        assert one_donor.ideal_donor_lap_id() is not None, "fixture must be the degenerate case"
        labels = [r.label for r in export_data.laps_summary(one_donor)]
        assert labels == ["Best rolling"], (with_sectors, labels)

    # Cross-check both ends land in the written CSV trailer, not just in laps_summary().
    for session, want in ((make_stitched_session(), 2), (make_session(), 1)):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "laps.csv")
            export_data.write_laps_csv(path, session)
            with open(path, newline="", encoding="utf-8") as f:
                got = list(csv.reader(f))
        n_data = len(session.valid_lap_ids())
        assert got[2 + n_data] == [export_data.SUMMARY_MARKER, "time_s", "over_laps", "note"]
        # The quality KEY and the dist_m column's note also live in the trailer; neither is a
        # summary row (see test_write_laps_csv_matches_table), so this gate counts only those.
        trailer = [r for r in got[3 + n_data:]
                   if not r[0].startswith(f"{export_data.SUMMARY_MARKER}: quality ")
                   and r[0] != f"{export_data.SUMMARY_MARKER}: {export_data.DIST_COLUMN}"]
        assert len(trailer) == want, (want, trailer)
        assert trailer[-1][0] == f"{export_data.SUMMARY_MARKER}: Best rolling"


# ------------------------------------------------------------------------- channels CSV
def test_channels_csv_roundtrip_exact():
    s = make_session()
    cols = s.lap_channels(0)
    assert list(cols) == ["t_telemetry_s", "t_video_s", "elapsed_s", "lat_deg", "lon_deg", "x_m",
                          "y_m", "dist_m", "speed_mps", "speed_kmh", "g_long", "g_lat"]
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "channels.csv")
        export_data.write_channels_csv(path, s, 0)
        with open(path, newline="", encoding="utf-8") as f:
            r = csv.reader(f)
            header = next(r)
            data = [[float(v) for v in row] for row in r]
    assert header == list(cols)
    parsed = np.asarray(data, dtype=float)
    assert parsed.shape == (len(cols["t_telemetry_s"]), len(header))
    for j, name in enumerate(header):  # float-repr round-trip: EXACT, not approximate
        assert np.array_equal(parsed[:, j], cols[name]), f"column {name} not exact"


def test_the_channels_csv_names_the_clock_each_time_column_is_on():
    """The channels CSV's time column was headed `t_media_s` and carried TELEMETRY seconds.

    Measured on both D24 recordings through the real `Session.load`, that column sat **+0.415 s
    (0060) / +0.353 s (0062)** — median, range +0.379…+0.450 / +0.291…+0.414 — after the video
    position that shows each row: 12.4 / 10.6 frames at 30 fps. Anyone lining the file up against
    the footage by the column's own name was a third of a second out, and "media" could not have
    said which media map anyway: since #301 the picture map and the camera's stamp map differ by
    the whole GPS lag. So the file names both clocks — `t_telemetry_s` (the GPS9 true clock the lap
    was timed on) and `t_video_s` (`Session.media_time`: the position in the footage whose frame
    shows that sample, the same map the player and the burned export use) — and neither says
    "media"."""
    from studio import chapters, media_clock

    s = make_session()
    clock = media_clock.MediaClock(rate=1.0 + 26.73e-6, offset=0.0256, gps_lag=0.4764)
    s.chapters = chapters.ChapterMap(["GX020060.MP4"], [3000.0], media_clock=clock)
    cols = s.lap_channels(0)
    assert not [k for k in cols if "media" in k], (
        f"a channels-CSV column still says 'media', which names no single clock: {list(cols)}")
    assert "t_telemetry_s" in cols and "t_video_s" in cols, list(cols)
    times = s._lap_columns(0)[0]
    assert np.array_equal(cols["t_telemetry_s"], times[:len(cols["t_telemetry_s"])])
    video = np.asarray([s.media_time(float(t)) for t in cols["t_telemetry_s"]])
    assert np.array_equal(cols["t_video_s"], video), "t_video_s must be Session.media_time"
    # The two columns really are different clocks here: the lag (and the ramp) separate them.
    gap = cols["t_telemetry_s"] - cols["t_video_s"]
    assert np.all(np.abs(gap - (clock.gps_lag - clock.offset)) < 0.01), gap[:3]
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "channels.csv")
        export_data.write_channels_csv(path, s, 0)
        with open(path, newline="", encoding="utf-8") as f:
            header = next(csv.reader(f))
    assert header[:3] == ["t_telemetry_s", "t_video_s", "elapsed_s"], header
    print("ok channels CSV names the telemetry and the video clock, never 'media'")


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


def test_channels_csv_g_long_is_clean_gps_not_raw_imu():
    """HONESTY FIX (#41 leftover): the channels CSV g_long column must carry the CLEAN GPS
    speed-derivative longitudinal (gm.long_g_gps) — the same validated axis the dial / map grip /
    DrivingChannels read — NOT the vibration-inflated raw IMU forward axis (gm.long_g). Construct a
    g-meter where the two differ hugely and assert the CLEAN value is what lands in lap_channels and
    the exported CSV (never the distrusted raw axis)."""
    s = make_session()
    n = len(s._gmeter.times)
    times = s._gmeter.times.copy()
    lat = s._gmeter.lat_g.copy()
    raw_long = np.full(n, 1.7)     # the IMU forward axis grossly vibration-inflated
    clean_long = np.full(n, 0.15)  # the CLEAN GPS-derived longitudinal — the trusted value
    s._gmeter = gmeter.GMeter(times=times, lat_g=lat, long_g=raw_long, cross=None,
                              source="accl", long_g_gps=clean_long)
    cols = s.lap_channels(0)
    # The exported g_long tracks the CLEAN axis (constant 0.15), NOT the raw 1.7.
    assert np.allclose(cols["g_long"], 0.15, atol=1e-9), cols["g_long"][:3]
    assert not np.any(np.isclose(cols["g_long"], 1.7)), "raw inflated IMU long_g leaked into the CSV"
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "channels.csv")
        export_data.write_channels_csv(path, s, 0)
        with open(path, newline="", encoding="utf-8") as f:
            r = csv.reader(f)
            header = next(r)
            col = header.index("g_long")
            g_long_csv = np.asarray([float(row[col]) for row in r])
    assert np.allclose(g_long_csv, 0.15, atol=1e-9), g_long_csv[:3]
    print("ok channels CSV g_long = clean GPS-derived longitudinal (not the raw inflated IMU axis)")


# --------------------------------------------------------------------------- session_date
def test_session_date_uses_local_calendar_day_not_utc():
    """CORRECTNESS FIX: session_date() must be the LOCAL calendar day the driver drove, not the
    UTC day. An evening session west of UTC (here 22:30 local in a UTC-05 zone) is 03:30Z the NEXT
    day; the always-UTC path rolled it onto the wrong day and mis-filed it in the library / the
    per-track PB axis. With a fixed timezone the local day must be the driving day (the 8th), NOT
    the UTC day (the 9th). The GPS5-sentinel guard (ts <= 0 -> None) is unchanged."""
    import time

    # 2026-01-09T03:30:00Z — i.e. 2026-01-08 22:30 local in America/New_York (UTC-05 in January).
    evening_utc_ms = 1767929400000
    old_tz = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        # Sanity: this instant is the 9th in UTC but the 8th locally (the rollover the fix targets).
        assert datetime.datetime.fromtimestamp(
            evening_utc_ms / 1000.0, tz=datetime.UTC).strftime("%Y-%m-%d") == "2026-01-09"
        expected_local = datetime.datetime.fromtimestamp(
            evening_utc_ms / 1000.0).strftime("%Y-%m-%d")
        assert expected_local == "2026-01-08", expected_local

        s = make_session()
        s.laps = FakeLaps(s.laps._laps, first_ts_ms=evening_utc_ms)
        assert s.session_date() == "2026-01-08", s.session_date()  # local day, not the UTC 9th

        # GPS5 sentinel (ts <= 0) still yields None (guard unchanged).
        s.laps = FakeLaps(s.laps._laps, first_ts_ms=0)
        assert s.session_date() is None
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()
    print("ok session_date: evening-UTC timestamp reports the LOCAL driving day (not the UTC roll)")


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
    # session_date() reports the LOCAL calendar day the driver drove (not UTC) — compute the
    # expectation the same naive-local way so this holds in any analysis-machine timezone.
    expected_date = datetime.datetime.fromtimestamp(TS_MS / 1000.0).strftime("%Y-%m-%d")
    assert s.session_date() == expected_date and expected_date in body_text
    # One table row per lap (+1 header row) in the laps table. Located by its own header cell
    # rather than by ORDINAL: the page now carries a `table.meta` and one `table.kv` per stats
    # group (N13) between the two, and "the second <table>" would silently start asserting the
    # SESSION group's row count the next time a group is added.
    laps_tables = [t for t in root.findall(".//table")
                   if [th.text for th in t.findall("tr/th")][:1] == ["Lap"]]
    assert len(laps_tables) == 1, [t.get("class") for t in root.findall(".//table")]
    assert len([tr for tr in laps_tables[0].findall("tr") if tr.find("td") is not None]) == \
        len(s.valid_lap_ids())
    # Both images embedded as base64 data URIs that decode back to real PNG bytes.
    imgs = root.findall(".//img")
    assert len(imgs) == 2
    for img in imgs:
        src = img.get("src")
        assert src.startswith("data:image/png;base64,")
        png = base64.b64decode(src.split(",", 1)[1])
        assert png.startswith(b"\x89PNG\r\n\x1a\n") and len(png) > 0
        assert png == PNG_1PX
        # No stated width when the caller gives none — the browser sizes it from the PNG, which
        # is right for a caller with no display to divide out (and what a 2-tuple has always
        # meant).
        assert img.get("width") is None, img.attrib


def test_report_figures_are_laid_out_at_the_stated_width():
    """W5-02: `QWidget.grab()` renders at the screen's device pixel ratio, so the report embedded
    a 2x PNG from a Retina Mac and — with no width on the tag — the browser laid the figure out at
    its DEVICE width. Measured on one recording exported twice from the same 1512x982 LOGICAL
    screen: figures 917 px wide from DPR 1 and 1120 px from DPR 2 (+22 %, the clamp being the
    stylesheet's own 70em column), the document 168 px longer, which moves a print/PDF page break.
    A third element states the LOGICAL width, so the page describes the session and not the
    machine. On main a 3-tuple raised ValueError from the writer's unpack."""
    s = make_session()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "report.html")
        export_data.write_report_html(
            path, s, source_label="GX010060",
            images=[("Track map", PNG_1PX, 917), ("Speed", PNG_1PX, None)])
        with open(path, encoding="utf-8") as f:
            text = f.read()
    root = ET.fromstring(text)  # still well-formed with the extra attribute
    imgs = root.findall(".//img")
    assert len(imgs) == 2
    assert imgs[0].get("width") == "917", imgs[0].attrib
    assert imgs[0].get("src", "").startswith("data:image/png;base64,")
    assert imgs[1].get("width") is None, imgs[1].attrib  # None ⇒ no attribute, as before
    # height:auto is what keeps the aspect ratio when a stated width (or the 70em clamp) shrinks
    # a figure below its natural pixel size.
    assert "height: auto" in text
    print("ok W5-02: the report states each figure's layout width")


def test_report_table_follows_the_display_unit(): # noqa: E301
    """QA L12-01: the report embeds the app's chart + map images, which already read in the display
    unit ("speed (mph)", "17 … 54 mph"), so its lap table must too — a km/h table 100 px under an
    mph chart put two different numbers for the same lap on one page. Passing a unit renames AND
    converts every speed column; nothing else on the page moves."""
    s = make_session()
    si_headers, si_rows = export_data.laps_table(s)
    mph_headers, mph_rows = export_data.laps_table(s, "mph")

    assert [h for h in si_headers if h.endswith("_kmh")] == \
        ["entry_kmh", "C1_apex_kmh", "C2_apex_kmh"], si_headers
    assert not [h for h in mph_headers if h.endswith("_kmh")], mph_headers
    assert [h for h in mph_headers if h.endswith("_mph")] == \
        ["entry_mph", "C1_apex_mph", "C2_apex_mph"], mph_headers
    # Only the speed columns move, and they move by exactly the one conversion constant. The
    # tolerance carries BOTH cells' 3-decimal rounding (0.0005 + 0.0005 x the factor).
    speed_cols = [i for i, h in enumerate(mph_headers) if h.endswith("_mph")]
    converted = 0
    for (_i, si), (_j, mph) in zip(si_rows, mph_rows, strict=True):
        for col in range(len(si)):
            if col in speed_cols and si[col]:
                assert abs(float(mph[col]) - float(si[col]) * units.KMH_TO_MPH) < 2e-3, col
                converted += 1
            else:
                assert mph[col] == si[col], col
    assert converted == len(speed_cols) * len(si_rows), converted
    # An explicit "kmh" is the SI table again (the app passes its live unit, usually this one).
    assert export_data.laps_table(s, "kmh") == (si_headers, si_rows)
    # And the report writer threads it through — the km/h names are gone from the whole page.
    with tempfile.TemporaryDirectory() as tmp:
        pages = {}
        for unit in (None, "mph"):
            path = os.path.join(tmp, f"report-{unit}.html")
            export_data.write_report_html(path, s, source_label="GX010099", unit=unit)
            with open(path, encoding="utf-8") as f:
                pages[unit] = f.read()
            ET.fromstring(pages[unit])  # still well-formed
    # (in the page's own words — "Entry (km/h)", "apex (km/h)" — see report_laps_header)
    assert "Entry (km/h)" in pages[None] and "Entry (mph)" not in pages[None]
    assert "Entry (mph)" in pages["mph"] and "apex (mph)" in pages["mph"]
    assert "Entry (km/h)" not in pages["mph"] and "apex (km/h)" not in pages["mph"]
    print("ok L12-01: the report table follows the display unit, the SI default is unchanged")


def test_write_laps_csv_stays_si_whatever_the_report_does():
    """The CSVs are the MACHINE-readable contract (`speed_mps` beside `speed_kmh`, every column
    unit-suffixed) and must NOT follow the screen. Pinned as a byte comparison, because the unit
    thread runs through the function the CSV writer calls."""
    s = make_session()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "laps.csv")
        export_data.write_laps_csv(path, s)
        with open(path, "rb") as f:
            written = f.read()
    # Rebuilt from the no-unit table the writer is contracted to use: identical, byte for byte.
    headers, rows = export_data.laps_table(s)
    assert written.split(b"\r\n")[0].decode() == ",".join(headers)
    assert b"entry_kmh" in written and b"entry_mph" not in written
    assert b"_apex_kmh" in written and b"_apex_mph" not in written
    assert len(written.split(b"\r\n")[1].decode().split(",")) == len(headers)
    print(f"ok L12-03/L12-01: laps.csv stays SI ({len(written)} B, {len(headers)} columns)")


def test_report_html_no_images_no_corners():
    s = make_session(with_sectors=False, with_corners=False, with_g=False)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "report.html")
        export_data.write_report_html(path, s, source_label="x & y")  # escaping exercised
        with open(path, encoding="utf-8") as f:
            root = ET.fromstring(f.read())
    assert not root.findall(".//img")
    assert "x & y" in "".join(root.itertext())


def _meta_rows(root):
    """{label: value} for the report's meta table."""
    return {tr.findtext("th"): tr.findtext("td")
            for tr in root.findall('.//table[@class="meta"]/tr')}


def test_provisional_timing_withholds_the_best_lap_cue_the_app_withholds():
    """PROVISIONAL timing (an auto-fitted start line nobody confirmed — the state every
    unrecognised circuit loads in) makes "the best lap" an assertion about an arbitrary point,
    and the app says so on every surface: `lap_table._apply_highlights` drops BOTH authority
    cues (the green row and the ★), `stats_panel._refresh_lap_table` drops the ★, and
    `share_card.card_data` refuses to render the card at all.

    The report painted `class="best"` on that lap's row and named it in the meta table, under a
    comment claiming it read "like the app's table". A document is the surface furthest from the
    app's own caveats, so it is the last place to assert an authority the app withholds. The
    TIME still prints — the Stats page's `best lap` tile prints it too, unmuted; what goes is
    the cue and the lap attribution.
    """
    s = make_session()
    verified = export_data.write_report_html
    with tempfile.TemporaryDirectory() as tmp:
        ok_path = os.path.join(tmp, "verified.html")
        verified(ok_path, s, source_label="GX010060")
        with open(ok_path, encoding="utf-8") as f:
            ok_root = ET.fromstring(f.read())
        # A verified session is unchanged: exactly one green row, and the meta row names the lap.
        ok_rows = [tr for tr in ok_root.findall(".//tr") if tr.get("class") == "best"]
        assert len(ok_rows) == 1, [tr.get("class") for tr in ok_root.findall(".//tr")]
        ok_meta = _meta_rows(ok_root)
        assert f"lap {s.best_lap_id() + 1}" in ok_meta["Best lap"], ok_meta["Best lap"]

        # ...and now the same session with the line unconfirmed on an unknown track.
        s.track_name = None
        s._timing_user_confirmed = False
        assert not s.timing_verified
        prov_path = os.path.join(tmp, "provisional.html")
        verified(prov_path, s, source_label="GX010060")
        with open(prov_path, encoding="utf-8") as f:
            text = f.read()
    root = ET.fromstring(text)
    green = [tr for tr in root.findall(".//tr") if tr.get("class") == "best"]
    assert not green, f"{len(green)} row(s) still carry the best-lap cue on provisional timing"
    meta = _meta_rows(root)
    assert "lap" not in (meta["Best lap"] or ""), meta["Best lap"]
    # The number itself survives — this is a suppressed CUE, not a suppressed measurement.
    assert fmt_time(s.lap_time(s.best_lap_id())) in (meta["Best lap"] or ""), meta["Best lap"]
    assert "PROVISIONAL" in (meta["Timing"] or ""), meta["Timing"]


# ------------------------------------------------------------- the report's story (report-story)
# The report tells what the app tells: MOMENTS, CORNERS (◆), the slow-lap notes and the lap strip,
# each printed from the function the app's surface reads. The story is PLANTED through those same
# functions (`stats.corner_matrix` / `story.*` / `stats.corner_report`) over synthetic corner
# times, and attached to a bare session as its accessors, so the page can be held to their strings.
_STORY_CIDS = [1, 2, 3, 4]


def _planted_story(times_by_lap, cells_by_lap, *, provisional=False, estimated=False):
    """(moments, notes, corner_report) over planted lap and corner times (laps 10…)."""
    from studio import story
    ids = sorted(times_by_lap)
    best = ids[0]  # lap 10, the quickest by construction
    mx = stats_service.corner_matrix(ids, _STORY_CIDS, [cells_by_lap[i] for i in ids],
                                     [[True] * len(_STORY_CIDS) for _ in ids])
    kw = dict(provisional=provisional, clock_estimated=estimated)
    moments = story.session_moments(times_by_lap, mx, best, **kw)
    notes = story.slow_lap_notes(times_by_lap, mx, best, first_lap_id=ids[0], **kw)
    report = stats_service.corner_report(
        _STORY_CIDS, [1, -1, 1, -1], [cells_by_lap[i] for i in ids],
        [[60.0] * len(_STORY_CIDS) for _ in ids], [[float("nan")] * len(_STORY_CIDS) for _ in ids],
        [[True] * len(_STORY_CIDS) for _ in ids], lap_ids=ids)
    return moments, notes, report


def _story_field(*, tie=False, slow=True, erratic=True):
    """20 planted laps (ids 10-29): corner 1 erratic (two lines, ◆), lap 15 slow at corner 2,
    the best lap 10 — with `tie`, lap 20 within 0.03 s of it."""
    rng = np.random.default_rng(3)
    times, cells = {}, {}
    for k, lap in enumerate(range(10, 30)):
        c = [3.0, 2.5, 4.0, 3.5] + rng.uniform(-0.02, 0.02, 4)
        if erratic and k % 2:
            c[0] += 0.4
        if slow and lap == 15:
            c[1] += 1.5
        cells[lap] = [float(x) for x in c]
        times[lap] = 46.0 + float(sum(c) - 13.0) + (1.6 if slow and lap == 15 else 0.0)
    times[10] = 45.0
    if tie:
        times[20] = 45.03
    return times, cells


def _with_story(s, moments, notes, report):
    s.session_moments = lambda: moments
    s.slow_lap_notes = lambda: notes
    s.corner_report = lambda: report
    return s


def _report_text(s, **kw):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "report.html")
        export_data.write_report_html(path, s, source_label="GX010060", **kw)
        with open(path, encoding="utf-8") as f:
            return f.read()


def _h2s(text):
    return ["".join(h.itertext()) for h in ET.fromstring(text).iter("h2")]


def _css_rules(css):
    """Every style rule of `css` as `(enclosing @-rules, [selectors], body)`, comments dropped:
    a rule's media context is read from the braces, not from where a line happens to sit."""
    import re
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules, stack, pos = [], [], 0
    while True:
        m = re.compile(r"[{}]").search(css, pos)
        if m is None:
            return rules
        prelude = " ".join(css[pos:m.start()].split())
        if m.group() == "}":
            stack.pop()
            pos = m.end()
        elif prelude.startswith("@"):
            stack.append(prelude)
            pos = m.end()
        else:   # a style rule: its body holds no braces
            end = css.index("}", m.end())
            rules.append((tuple(stack), [s.strip() for s in prelude.split(",")],
                          css[m.end():end]))
            pos = end + 1


def test_the_report_tells_the_apps_story_in_its_order_and_words():
    """MOMENTS, CORNERS, SLOW LAPS and the lap strip are on the page, in the Stats page's order
    then the Laps tab's, and every sentence is the app function's own string: `story.moment_row`,
    `SlowLapNote.text` / `.rule`, `stats.corner_mark_rule`, the ◆ on `stats.marked_corners`."""
    from studio import story
    times, cells = _story_field()
    moments, notes, report = _planted_story(times, cells)
    s = _with_story(make_session(), moments, notes, report)
    text = _report_text(s, images=[("Track map", PNG_1PX)], lapstrip=PNG_1PX)
    heads = _h2s(text)
    # The passed figure comes first, right under the front page (cool/report-pictures-up).
    want = ["Track map", "SESSION", "PACE", "MOMENTS", "SPEED · G", "CORNERS · speeds in km/h",
            "SLOW LAPS", "LAP STRIP", f"Laps ({len(s.valid_lap_ids())})"]
    assert [h for h in heads if h in want] == want, heads
    body = "".join(ET.fromstring(text).itertext())
    shown = story.shown_moments(moments)
    assert len(shown) == 3, shown
    for m in shown:
        row = story.moment_row(m)
        assert row.label in body and row.value in body, row
    assert story.MOMENTS_NOTE in body and "Jump" not in story.MOMENTS_NOTE
    (note,) = notes
    assert note.lap_id == 15 and note.text in body and note.rule in body, note
    # The ◆ sits on exactly the corners the Stats page marks, and its rule is printed.
    marked = {r.cid for r in stats_service.marked_corners(report, stats_service.CORNER_MARK_N)}
    assert marked == {1}, marked
    corner_rows = {tr.findtext("td"): [td.text for td in tr.findall("td")]
                   for t in ET.fromstring(text).iter("table")
                   if [th.text for th in t.findall("tr/th")][:1] == ["Corner"]
                   for tr in t.findall("tr")[1:]}
    assert [c for c, cs in corner_rows.items() if cs[5].startswith("◆ ")] == ["C1"], corner_rows
    by_cid = {r.cid: r for r in report}
    assert corner_rows["C1"][4] == stats_service.fmt_middle_half(
        by_cid[1].middle_half_s, stats_service.CORNER_MIDDLE_HALF_FLOOR_S).removesuffix(" s"), \
        corner_rows["C1"]
    assert stats_service.corner_mark_rule(hover=False) in body
    assert "hover" not in stats_service.corner_mark_rule(hover=False)
    # PACE carries the σ tile's hover, "middle half spans" in the page's words.
    assert stats_service.SIGMA_TOOLTIP in body and "middle half spans" in body
    print(f"ok report-story: {len(heads)} sections in the app's order, the app's own strings")


def test_a_tie_is_named_level_and_a_lone_untied_best_is_no_moments_section():
    """Ties within the timing precision are named level, in `moment_row`'s words; a session whose
    only moment is an untied best lap gets no MOMENTS section (it repeats the PACE best tile)."""
    from studio import story
    times, cells = _story_field(tie=True)
    moments, notes, report = _planted_story(times, cells)
    assert [t.lap_ids for t in moments.best_lap.tie] == [(20,)], moments.best_lap
    text = _report_text(_with_story(make_session(), moments, notes, report))
    value = story.moment_row(moments.best_lap).value
    assert "is level (0.030 s, under the 0.07 s timing precision)" in value, value
    assert value in "".join(ET.fromstring(text).itertext())

    lone = story.SessionMoments(best_lap=story.session_moments({0: 45.0, 1: 46.0}, None, 0)
                                .best_lap, biggest_loss=None, best_window=None)
    assert lone.best_lap is not None and not lone.best_lap.tie
    text = _report_text(_with_story(make_session(), lone, [], []))
    assert "MOMENTS" not in _h2s(text) and "CORNERS" not in " ".join(_h2s(text))
    print("ok report-story: a tie is named level; a lone untied best has no MOMENTS section")


def test_provisional_timing_carries_through_moments_and_notes():
    """Under a provisional line the app has no best-lap moment and the notes carry the caveat
    (`story`'s one rule); the report prints neither more nor less, and stamps both sections with
    its own Timing sentence. The strip's gate is the caller's: it passes no strip."""
    times, cells = _story_field(tie=True)
    moments, notes, report = _planted_story(times, cells, provisional=True)
    assert moments.best_lap is None and notes and notes[0].caveat == ("provisional",)
    s = _with_story(make_session(), moments, notes, report)
    s.track_name = None
    s._timing_user_confirmed = False
    text = _report_text(s)
    body = "".join(ET.fromstring(text).itertext())
    assert "Best lap:" not in body and "Best lap" in _meta_rows(ET.fromstring(text))
    # The repeated stamp is worded for where it stands ("on this page", not the header's
    # "below"), and the PROVISIONAL sentence ends with its full stop.
    stamp = f"Timing: {export_data.timing_meta(s, where='on this page')}"
    assert "PROVISIONAL" in stamp and body.count(stamp) == 2, body.count(stamp)
    assert "split on this page is measured from an arbitrary point." in stamp, stamp
    assert "split below is measured" in _meta_rows(ET.fromstring(text))["Timing"]
    assert "LAP STRIP" not in _h2s(text)
    print("ok report-story: provisional timing — no best moment, both sections stamped")


def test_the_report_is_one_self_contained_page_in_the_given_tokens():
    """No network fetch and no external asset: no http(s) anywhere, every src a data: URI, no
    script, link or CSS url(). Screen colours are the passed tokens; print is paper; a token that
    is not a plain #RRGGBB colour is refused rather than written into the page."""
    times, cells = _story_field()
    s = _with_story(make_session(), *_planted_story(times, cells))
    tokens = {name: "#15181E" for name in export_data.REPORT_TOKENS}
    text = _report_text(s, images=[("Track map", PNG_1PX, 400)], lapstrip=PNG_1PX, tokens=tokens)
    root = ET.fromstring(text)
    assert "http" not in text.lower() and "url(" not in text and "@import" not in text
    assert not list(root.iter("script")) and not list(root.iter("link"))
    srcs = [el.get("src") for el in root.iter() if el.get("src") is not None]
    assert len(srcs) == 2 and all(x.startswith("data:image/png;base64,") for x in srcs), srcs
    css = "".join(root.iter("style").__next__().itertext())
    screen, paper = css.split("@media print", 1)
    assert "--canvas: #15181E;" in screen and "--text-dim: #15181E;" in screen
    assert f"--canvas: {export_data.PAPER_TOKENS['canvas']};" in paper
    try:
        export_data.report_css({**tokens, "text": "red; } body { x"})
    except ValueError:
        pass
    else:
        raise AssertionError("a non-colour token was written into the page")
    print("ok report-story: one self-contained page, tokens on screen, paper in print")


def test_a_degraded_recording_stamps_the_slow_lap_notes_like_the_laps_table():
    """The Laps table shows its estimated-timing note under every slow-lap note on a degraded
    recording (GPS low here), whatever the note's own caveat (`story.note_trust_states`). The
    report stamped SLOW LAPS only from the note's caveat, so a GPS-low session's notes went out
    unqualified. MOMENTS follows the Stats page, which qualifies a moment by its own caveat only."""
    from studio import data_quality, story
    times, cells = _story_field()
    moments, notes, report = _planted_story(times, cells)
    assert notes and notes[0].caveat == (), notes
    s = _with_story(make_session(), moments, notes, report)
    s._timing_quality = data_quality.TimingQuality(dropped_fraction=0.4)
    assert s.timing_quality.degraded and not s.timing_quality.media_clock
    assert story.note_trust_states((), verified=True, degraded=True) == ("estimated",)
    assert story.note_trust_states((), verified=False, degraded=False) == ("provisional",)
    body = "".join(ET.fromstring(_report_text(s)).itertext())
    stamp = f"Timing: {export_data._timing_meta(s, where='on this page')}"
    assert "GPS" in stamp and body.count(stamp) == 1, (stamp, body.count(stamp))
    start = body.index("SLOW LAPS")
    slow = body[start:body.index("Laps (", start)]
    assert stamp in slow, slow
    print("ok report-story: a degraded recording stamps SLOW LAPS as the Laps table does")


def test_the_page_keeps_what_the_slow_laps_and_corners_sections_must_say():
    """What a reader of the page cannot get any other way: no note for a ⚠ dropout lap (the Laps
    table holds that at the surface), a note's text escaped, the page's own counting wording
    (it has no CORNERS BY LAP grid to mute cells in), and each one-pass σ sentence."""
    import dataclasses
    times, cells = _story_field()
    moments, notes, report = _planted_story(times, cells)
    s = make_session()
    dropout = next(iter(s.dropout_lap_ids()))
    planted = [dataclasses.replace(notes[0], text="Lap 15 · a < b & c"),
               dataclasses.replace(notes[0], lap_id=dropout, text="DROPOUT LAP NOTE")]
    # Two passes of C3 interpolated, so the counting sentence is written.
    left = [dataclasses.replace(r, n_laps=r.n + 2) if r.cid == 3 else r for r in report]
    text = _report_text(_with_story(s, moments, planted, left))
    body = "".join(ET.fromstring(text).itertext())   # parses: the "<" and "&" were escaped
    assert "Lap 15 · a < b & c" in body and "a &lt; b &amp; c" in text
    assert "DROPOUT LAP NOTE" not in body, "a ⚠ dropout lap was given a note"
    page = " ".join(stats_service.corner_count_note(left, page=False))
    assert page in body and f"{export_data.REPORT_INTERPOLATED_HEAD} column" in page
    assert "shown muted lap by lap" not in body
    one_pass = [(r.cid, stats_service.corner_one_pass_note(r)) for r in report
                if stats_service.corner_one_pass_note(r)]
    assert one_pass, "the planted field must carry a one-pass corner"
    for cid, sentence in one_pass:
        assert f"C{cid}: {sentence}" in body, sentence
    # The page's ◆ rule names the column; the app's tooltip keeps its own words, byte for byte.
    assert "the Med loss column's three largest" in stats_service.corner_mark_rule(hover=False)
    assert "simply this column's three largest" in stats_service.corner_mark_rule()
    print("ok report-story: dropout filter, escaping, page wording and one-pass notes")


def test_print_puts_every_laps_column_on_paper():
    """Printed, the page cannot scroll: the laps table's card must not clip (it stopped at
    C3_time_s on A4, losing the quality and corners_interpolated disclosures), and only small
    blocks are kept whole, never a tall section (half of page 1 went blank)."""
    assert export_data._REPORT_CSS in export_data.report_css()
    screen, paper = export_data._REPORT_CSS.split("@media print {")
    assert "overflow-x: auto" in screen and "section.wide" in screen
    assert "overflow: visible" in paper and "break-inside: auto" in paper
    # Kept whole: small blocks only — a figure with its caption and a Stats group are (each well
    # under a page, cool/report-pictures-up); a bare `section` or `section.wide` (COACHING,
    # CORNERS, the laps table) never is. Every rule that reaches paper counts — the print block
    # AND the rules outside any `@media screen` one (a global `section { break-inside: avoid }`
    # prints too); only a screen-only rule may keep more whole.
    on_paper = [sels for media, sels, body in _css_rules(export_data._REPORT_CSS)
                if "break-inside: avoid" in body
                and not any(m.startswith("@media screen") for m in media)]
    selectors = [sel for sels in on_paper for sel in sels]
    assert on_paper and not {"section", "section.wide"} & set(selectors), selectors
    assert all(sel in {"tr", "li", "p.note", "img", "table.kv", "table.meta", "table.moments",
                       "section.figure", ".groups > section", "figure.hero"}
               for sel in selectors), selectors
    assert "table.laps" in paper and "img.card" in paper
    s = make_session()
    text = _report_text(s, lapstrip=PNG_1PX)
    root = ET.fromstring(text)
    laps = [t for t in root.iter("table") if t.get("class") == "laps"]
    # Every column on paper, under its page header: the two disclosures last, as in laps.csv.
    headers, _rows = export_data.laps_table(s)
    named = [th.text for th in laps[0].findall("tr/th")]
    assert len(laps) == 1 and named[:1] == ["Lap"] and [
        label for _g, label in export_data.report_laps_header(headers)][-2:] == [
        export_data.REPORT_QUALITY_HEAD, export_data.REPORT_INTERPOLATED_HEAD]
    assert export_data.REPORT_INTERPOLATED_HEAD in named and "break-all" not in paper
    assert [i.get("class") for i in root.iter("img")] == ["card"]
    print("ok report-story: print shows every laps column; the strip is a bordered card")


def test_the_report_laps_table_speaks_the_apps_words_and_keeps_every_column():
    """Discovery 3, K4: the report's Laps table printed laps.csv's machine names — `time_s`,
    `dist_m`, `entry_kmh`, `C1_time_s`, `C1_apex_kmh`, `quality`, `corners_interpolated` — so it
    scrolled sideways on screen (1981 px of table in a 978 px card on the demo) and its headers
    broke mid-word on A4 (`break-all`). It now reads like the Laps tab ("Lap", "Time (s)", "Dist
    (m)", "Entry (km/h)") with each corner's pair under ONE "C1" over "time (s)" / "apex (km/h)",
    in the page's speed unit; every column is still there, and laps.csv keeps its names."""
    s = make_session()
    for unit, label in ((None, "km/h"), ("mph", "mph")):
        headers, rows = export_data.laps_table(s, unit)
        cols = export_data.report_laps_header(headers)
        assert len(cols) == len(headers), (headers, cols)       # one page column per CSV column
        assert not [lb for _g, lb in cols if "_" in lb], cols    # no machine name left
        by = dict(zip(headers, cols, strict=True))
        assert by["lap"] == (None, "Lap") and by["time_s"] == (None, "Time (s)")
        assert by[export_data.DIST_COLUMN] == (None, "Dist (m)")
        sfx = "kmh" if unit is None else "mph"
        assert by[f"entry_{sfx}"] == (None, f"Entry ({label})")
        assert by["C1_time_s"] == ("C1", "time (s)")
        assert by[f"C1_apex_{sfx}"] == ("C1", f"apex ({label})")
        assert by["S1_s"] == ("Splits (s)", "S1")
        assert by["quality"] == (None, export_data.REPORT_QUALITY_HEAD)
        assert by[export_data.INTERPOLATED_COLUMN] == (None, export_data.REPORT_INTERPOLATED_HEAD)
        # The page: two header rows whose spans cover every column exactly once.
        root = ET.fromstring(_report_text(s, unit=unit))
        (table,) = [t for t in root.iter("table") if t.get("class") == "laps"]
        top, sub = table.findall("tr")[:2]
        spans = sum(int(th.get("colspan", 1)) for th in top.findall("th"))
        assert spans == len(headers), (spans, len(headers))
        assert [th.text for th in top.findall("th") if th.get("rowspan") == "2"] == \
            [lb for g, lb in cols if g is None], "a column of its own changed place or went missing"
        assert [th.text for th in sub.findall("th")] == [lb for g, lb in cols if g is not None]
        assert [th.text for th in top.findall("th") if th.get("colspan")] == \
            list(dict.fromkeys(g for g, _lb in cols if g is not None))
        assert all(len(tr.findall("td")) == len(headers) for tr in table.findall("tr")[2:])
        page = "".join(ET.tostring(table, encoding="unicode"))
        assert "time_s" not in page and "_kmh" not in page and "_mph" not in page, page[:200]
    # An unknown column keeps its machine name rather than lose its place.
    assert export_data.report_laps_header(["lap", "new_thing"])[1] == (None, "new_thing")
    # laps.csv is untouched: its header row is still laps_table's machine names.
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "laps.csv")
        export_data.write_laps_csv(path, s)
        with open(path, encoding="utf-8") as f:
            first = f.readline().strip()
    assert first == ",".join(export_data.laps_table(s)[0]) and "C1_apex_kmh" in first, first
    # On paper the headers wrap between words only.
    assert "break-all" not in export_data._REPORT_CSS
    # The interpolated note is laps.csv's sentence with the column words swapped (str.replace):
    # a wording change upstream must not silently bring a CSV name back onto the page.
    note = export_data.REPORT_INTERPOLATED_NOTE
    assert note != export_data.INTERPOLATED_NOTE and note.startswith(
        f"{export_data.REPORT_INTERPOLATED_HEAD} lists"), note
    for machine in (export_data.INTERPOLATED_COLUMN, "_time_s", "_apex_", "*_", "_kmh", "_mph"):
        assert machine not in note, (machine, note)
    print("ok K4: the report's laps table has the app's headers, every column, the CSV its names")


if __name__ == "__main__":
    test_laps_table_schema_and_values()
    test_the_display_table_prints_the_apps_precision_from_the_raw_values()
    test_laps_table_degenerate_schema()
    test_write_laps_csv_matches_table()
    test_the_csv_discloses_an_interpolated_corner_cell_and_keeps_every_row()
    test_laps_summary_gate_is_the_ideal_not_the_sector_count()
    test_channels_csv_roundtrip_exact()
    test_the_channels_csv_names_the_clock_each_time_column_is_on()
    test_channels_csv_without_g_signal()
    test_channels_csv_g_long_is_clean_gps_not_raw_imu()
    test_session_date_uses_local_calendar_day_not_utc()
    test_report_html_wellformed_with_images()
    test_report_figures_are_laid_out_at_the_stated_width()
    test_report_table_follows_the_display_unit()
    test_write_laps_csv_stays_si_whatever_the_report_does()
    test_report_html_no_images_no_corners()
    test_provisional_timing_withholds_the_best_lap_cue_the_app_withholds()
    test_the_report_tells_the_apps_story_in_its_order_and_words()
    test_a_tie_is_named_level_and_a_lone_untied_best_is_no_moments_section()
    test_provisional_timing_carries_through_moments_and_notes()
    test_the_report_is_one_self_contained_page_in_the_given_tokens()
    test_a_degraded_recording_stamps_the_slow_lap_notes_like_the_laps_table()
    test_the_page_keeps_what_the_slow_laps_and_corners_sections_must_say()
    test_print_puts_every_laps_column_on_paper()
    test_the_report_laps_table_speaks_the_apps_words_and_keeps_every_column()
    print("test_export_data: OK")
