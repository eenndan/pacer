"""Club Speed heat pages → one lap CSV, the ground truth `_validate_wallclock.py --lock-only` reads.
Pure stdlib, no pacer.

Both circuits Pacer's footage comes from time ordinary sessions on Club Speed, whose public
`sp_center/HeatDetails.aspx?HeatNo=N` page carries every lap of every driver in the heat: one
`<table class='LapTimes'>` per driver, a `<th>` with the driver's NAME, then one
`<tr class='LapTimesRow…'><td>lap</td><td>seconds [position]</td></tr>` per lap. There is no CSV
or PDF export, so a page saved from a browser (or fetched once by hand) is the input here.

NAMES NEVER LEAVE THE PAGE. A driver is identified by where their table sits on the page (`row`
1..N, in page order), never by the name in its header: the parser does not read `<th>` text at
all, so a name cannot reach the CSV, a log line or a report. The pages and the CSV still belong
outside the repo — they are other people's lap times — exactly like the transponder CSV.

Run (positional arguments are the INPUT pages; the OUTPUT is `--out`, a new `.csv`):
  pixi run python -m studio.dev.clubspeed heat_90189.html heat_90190.html --out 18-09.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from html.parser import HTMLParser

from studio.dev import transponder

HEADER = ("heat", "heat_start_local", "row", "lap", "lap_s")
_HEAT_NO = re.compile(r"HeatNo=(\d+)", re.I)
# "81.865 [1]" (seconds, then the driver's running position); a lap over 100 s may be M:SS.mmm.
_LAP_CELL = re.compile(r"^\s*(\d+(?::\d{1,2})?(?:\.\d+)?)\s*(?:\[\d+\])?\s*$")


def parse_lap_cell(text: str) -> float | None:
    """`'77.03 [1]'` → 77.03, `'1:45.2 [3]'` → 105.2, anything else → None."""
    m = _LAP_CELL.match(text)
    if not m:
        return None
    value = m.group(1)
    return transponder.parse_lap_time(value) if ":" in value else float(value)


class _HeatPage(HTMLParser):
    """Collects `rows` (one {lap: seconds} per LapTimes table, in page order) and the heat's
    `start` label. Text is only ever read inside a lap row's `<td>` or the date `<span>`."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[int, float]] = []
        self.start: str | None = None
        self._in_lap_row = False
        self._in_td = False
        self._cells: list[str] = []
        self._in_date = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if tag == "table" and "LapTimes" in cls:
            self.rows.append({})
        elif tag == "tr":
            self._in_lap_row = bool(self.rows) and any(c.startswith("LapTimesRow") for c in cls)
            self._cells = []
        elif tag == "td" and self._in_lap_row:
            self._in_td = True
            self._cells.append("")
        elif tag == "span" and a.get("id") == "lblDate":
            self._in_date = True

    def handle_endtag(self, tag):
        if tag == "td":
            self._in_td = False
        elif tag == "span":
            self._in_date = False
        elif tag == "tr" and self._in_lap_row:
            self._in_lap_row = False
            if len(self._cells) == 2 and self._cells[0].strip().isdigit():
                seconds = parse_lap_cell(self._cells[1])
                if seconds is not None:
                    self.rows[-1][int(self._cells[0])] = seconds

    def handle_data(self, data):
        if self._in_td and self._cells:
            self._cells[-1] += data
        elif self._in_date:
            self.start = (self.start or "") + data.strip()


def parse_page(html: str) -> tuple[str | None, list[dict[int, float]]]:
    """(the heat's start label as printed, e.g. '18/09/2026 20:10'; one {lap: seconds} per driver
    table, in page order). A table with no timed lap is kept as an empty dict so `row` numbers stay
    the page's own order."""
    page = _HeatPage()
    page.feed(html)
    page.close()
    return page.start, page.rows


def heat_number(path: str, html: str) -> int:
    """The heat number: from the page's own `HeatNo=` (its form action), else the first run of
    digits in the file name."""
    m = _HEAT_NO.search(html) or re.search(r"(\d+)", os.path.basename(path))
    if not m:
        raise ValueError(f"{path}: no heat number in the page or its file name")
    return int(m.group(1))


def write_csv(pages: list[str], out: str) -> int:
    """Write every page's laps to `out`; returns the number of lap rows written."""
    rows = []
    for path in pages:
        with open(path, encoding="utf-8", errors="replace") as f:
            html = f.read()
        start, drivers = parse_page(html)
        heat = heat_number(path, html)
        if not drivers:
            raise ValueError(f"{path}: no Club Speed lap tables — not a HeatDetails page?")
        for row, laps in enumerate(drivers, 1):
            rows += [(heat, start or "", row, lap, f"{t:.3f}") for lap, t in sorted(laps.items())]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    return len(rows)


def parse_csv(path: str) -> tuple[dict[tuple[int, int], dict[int, float]], dict[int, str]]:
    """Read a CSV `write_csv` wrote → ({(heat, row): {lap: seconds}}, {heat: start label})."""
    rows: dict[tuple[int, int], dict[int, float]] = {}
    starts: dict[int, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        if tuple(next(reader, ())) != HEADER:
            raise ValueError(f"{path}: not a Club Speed lap CSV (header must be {','.join(HEADER)})")
        for heat, start, row, lap, secs in reader:
            rows.setdefault((int(heat), int(row)), {})[int(lap)] = float(secs)
            starts[int(heat)] = start
    return rows, starts


def is_clubspeed_csv(path: str) -> bool:
    with open(path, newline="", encoding="utf-8") as f:
        return tuple(next(csv.reader(f), ())) == HEADER


def main(argv) -> int:
    ap = argparse.ArgumentParser(prog="studio.dev.clubspeed")
    ap.add_argument("pages", nargs="+", help="saved HeatDetails.aspx pages (INPUTS, read only)")
    ap.add_argument("--out", required=True, help="the lap CSV to write (a new .csv)")
    args = ap.parse_args([a for a in argv if a != "--"])
    out = os.path.abspath(os.path.expanduser(args.out))
    if not out.endswith(".csv"):
        ap.error("--out must name a .csv")
    if any(os.path.abspath(p) == out for p in args.pages):
        ap.error("--out names one of the input pages")
    if out.startswith(os.path.expanduser("~/Desktop") + os.sep):
        ap.error("--out is under ~/Desktop, which holds the owner's footage and is never written")
    if os.path.exists(out) and not is_clubspeed_csv(out):
        ap.error(f"{out} exists and is not a lap CSV this tool wrote — refusing to overwrite it")
    n = write_csv(args.pages, out)
    print(f"wrote {n} laps from {len(args.pages)} page(s) to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
