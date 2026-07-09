"""Data export (F11): laps CSV, per-lap channels CSV, and a one-page HTML session report.

PACER-FREE AND QT-FREE BY CONTRACT (stdlib + numpy duck-typing only): the three writers are
fed exclusively by Session accessors (lap_rows / lap_sector_splits / lap_corner_stats /
lap_channels / ...), so this module never imports the compiled `pacer` bindings or Qt — the
studio architecture rule for analysis/IO modules. app.py owns the File ▸ Export submenu, the
QFileDialog save prompts, and the widget→PNG grabs (the only Qt-touching part of the report)
and passes the bytes in. Nothing here ever writes a file without being called explicitly with
a user-chosen path — there is no implicit/automatic export anywhere.

Float formatting policy (two deliberately different precisions):
  * channels.csv — `repr()` of the Python float: the shortest string that round-trips to the
    EXACT same double, so a re-parse with `float()` reproduces the Session arrays
    bit-for-bit (np.array_equal — asserted on the real session at verification).
  * laps.csv + the report — human-readable 3 decimals: sub-ms digits are GPS noise anyway
    (the validated timing floor is ~50–90 ms per lap), and the file is meant to be READ.
"""

from __future__ import annotations

import base64
import csv
import html

from . import APP_NAME
from ._signal import fmt_time

# laps.csv `flag` column value mirroring the lap table's ⚠ low-confidence marker (a GPS
# dropout inside the lap — its time/distance are less reliable). Clean laps carry "".
DROPOUT_FLAG = "gps-dropout"

# laps.csv trailer (the session-summary footer rows mirroring the lap table's footer below
# the table): a labeled section AFTER the lap rows, separated by one blank row, led by its own
# `summary,time_s` mini-header so the file stays cleanly parseable (split on the blank row, or
# filter the `summary` marker). Each (label, Session accessor) pair matches lap_table's
# FOOTER_ROWS so the CSV trailer and the app's footer can never disagree. A None value (no
# valid laps / a sector column with no data) writes a blank time cell, like the app's em-dash.
SUMMARY_MARKER = "summary"
SUMMARY_ROWS = (("Theoretical best", "theoretical_best"), ("Best rolling", "best_rolling_lap"))


def _f3(v) -> str:
    """Human 3-decimal float formatting (laps.csv + the report tables)."""
    return f"{float(v):.3f}"


def laps_table(session) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """(headers, rows) of the lap table — single-sourced for BOTH `write_laps_csv` and the
    report's HTML table, so the two always agree. One row per lap shown in the app's lap
    table (`session.lap_rows()` — the valid laps), as `(lap_id, cells)`; columns:

      lap, time_s, dist_m, entry_kmh, flag      the table's base columns; `flag` is the ⚠
                                                GPS-dropout marker (DROPOUT_FLAG / "")
      S1_s … SN_s                               per-sub-sector splits — present only when
                                                sector lines exist (like the app's table);
                                                a partial lap's missing splits stay blank
      C1_time_s, C1_apex_kmh, … per corner      time-in-corner + apex (min) speed from
                                                `session.lap_corner_stats` (the F2 corner
                                                model); blank when a lap has no stats
    """
    rows_meta = session.lap_rows()
    n_sect = session.sector_count()
    n_splits = n_sect + 1 if n_sect else 0  # N lines -> N+1 sub-sectors; 0 lines -> none
    corner_list = session.corners.corner_list()
    dropout_ids = session.dropout_lap_ids()

    headers = ["lap", "time_s", "dist_m", "entry_kmh", "flag"]
    headers += [f"S{i + 1}_s" for i in range(n_splits)]
    for c in corner_list:
        headers += [f"{c.label}_time_s", f"{c.label}_apex_kmh"]

    rows: list[tuple[int, list[str]]] = []
    for r in rows_meta:
        lap_id = r["idx"]
        cells = [str(lap_id), _f3(r["time"]), _f3(r["dist"]), _f3(r["entry"]),
                 DROPOUT_FLAG if lap_id in dropout_ids else ""]
        splits = session.lap_sector_splits(lap_id) if n_splits else []
        for i in range(n_splits):
            cells.append(_f3(splits[i]) if i < len(splits) else "")
        stats = {s.cid: s for s in session.corners.lap_corner_stats(lap_id)}
        for c in corner_list:
            s = stats.get(c.cid)
            cells += [_f3(s.time), _f3(s.apex_speed)] if s is not None else ["", ""]
        rows.append((lap_id, cells))
    return headers, rows


def laps_summary(session) -> list[tuple[str, str]]:
    """The session-summary footer values for the laps.csv trailer — `(label, value_str)` per
    SUMMARY_ROWS, mirroring the lap table's footer (F1): "Theoretical best" (the sum of the
    session-best sector splits) and "Best rolling" (the fastest start-anywhere full loop). The
    value is 3-decimal seconds (the same `_f3` precision as the lap rows' time_s column) or ""
    when the accessor returns None (no valid laps / an all-partial sector column) — the app's
    footer shows the em-dash there. Read straight from `Session.theoretical_best` /
    `best_rolling_lap`, so the trailer always equals what the app's footer displays."""
    out: list[tuple[str, str]] = []
    for label, accessor in SUMMARY_ROWS:
        v = getattr(session, accessor)()
        out.append((label, _f3(v) if v is not None else ""))
    return out


def write_laps_csv(path: str, session) -> None:
    """One row per (valid) lap — number, time, distance, entry speed, splits, the ⚠ flag,
    and the per-corner time/apex-speed columns — followed by the session-summary TRAILER
    (theoretical-best / best-rolling, see `laps_summary`): a blank separator row, a
    `summary,time_s` mini-header, then one labeled row per summary value. Human 3-decimal
    floats (see module doc); the trailer keeps the file cleanly parseable (split on the blank
    row, or filter the leading `summary` marker)."""
    headers, rows = laps_table(session)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(cells for _lap_id, cells in rows)
        w.writerow([])  # blank separator: the lap rows end here, the trailer follows
        w.writerow([SUMMARY_MARKER, "time_s"])
        for label, value in laps_summary(session):
            w.writerow([f"{SUMMARY_MARKER}: {label}", value])


def write_channels_csv(path: str, session, lap_id: int) -> None:
    """Per-sample channel export for ONE lap: the column set and order are exactly
    `session.lap_channels(lap_id)`'s keys (t/elapsed/lat/lon/x/y/dist/speed m/s + km/h, plus
    the kart-frame g_long/g_lat when the session has a g signal). Every value is written as
    the float's `repr` — the shortest exact round-trip form — so re-parsing the file with
    `float()` reproduces the Session arrays bit-for-bit."""
    cols = session.lap_channels(lap_id)
    names = list(cols)
    arrays = [cols[k] for k in names]
    n = min((len(a) for a in arrays), default=0)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(names)
        for i in range(n):
            w.writerow([repr(float(a[i])) for a in arrays])


# --------------------------------------------------------------------- session report
# Plain inline CSS only (no JS, no external assets): the report must open ANYWHERE — mail
# attachments, archives, file:// — and stay readable as source. Kept out of an f-string so
# the braces need no escaping.
_REPORT_CSS = """
body { font-family: -apple-system, Helvetica, Arial, sans-serif; margin: 2em auto;
       max-width: 70em; color: #1a1a1a; }
h1 { font-size: 1.4em; } h2 { font-size: 1.1em; margin-top: 1.5em; }
table { border-collapse: collapse; margin: 0.5em 0; }
th, td { border: 1px solid #ccc; padding: 3px 10px; text-align: right;
         font-variant-numeric: tabular-nums; }
th { background: #f2f2f2; }
tr.best td { color: #0a7d33; font-weight: 600; }
table.meta th, table.meta td { text-align: left; }
img { max-width: 100%; border: 1px solid #ccc; margin: 0.5em 0; }
"""


def write_report_html(path: str, session, source_label: str = "",
                      images: list[tuple[str, bytes]] = ()) -> None:
    """One SELF-CONTAINED page: the session header (recording, track, date, lap count,
    best lap), the laps table (same rows/columns as `write_laps_csv`, via `laps_table`),
    and the passed PNG snapshots embedded as base64 data URIs. `images` is an iterable of
    `(title, png_bytes)` — app.py grabs the map/plots widgets (QWidget.grab → QImage →
    PNG bytes), so this module stays Qt-free. Deliberately dead-simple, WELL-FORMED
    (XML-parseable) markup with a little inline CSS and NO JavaScript."""
    esc = html.escape
    headers, rows = laps_table(session)
    best = session.best_lap_id()
    best_txt = "—"
    if best is not None:
        best_txt = f"{fmt_time(session.lap_time(best))} (lap {best})"
    meta = [
        ("Recording", source_label or "—"),
        ("Track", session.track_name or "unknown"),
        ("Date", session.session_date() or "—"),
        ("Laps", str(len(rows))),
        ("Best lap", best_txt),
    ]

    out = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8"/>',
        f"<title>{APP_NAME} — {esc(source_label) or 'session report'}</title>",
        f"<style>{_REPORT_CSS}</style></head><body>",
        f"<h1>{APP_NAME} — session report</h1>",
        '<table class="meta">',
        *(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in meta),
        "</table>",
        f"<h2>Laps ({len(rows)})</h2>",
        "<table><tr>" + "".join(f"<th>{esc(h)}</th>" for h in headers) + "</tr>",
    ]
    for lap_id, cells in rows:  # the best lap reads green, like the app's table
        cls = ' class="best"' if lap_id == best else ""
        out.append(f"<tr{cls}>" + "".join(f"<td>{esc(c)}</td>" for c in cells) + "</tr>")
    out.append("</table>")
    for title, png in images:
        b64 = base64.b64encode(png).decode("ascii")
        out.append(f"<h2>{esc(title)}</h2>")
        out.append(f'<img alt="{esc(title)}" src="data:image/png;base64,{b64}"/>')
    out.append("</body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
