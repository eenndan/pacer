"""Data export (F11): laps CSV, per-lap channels CSV, and a one-page HTML session report.

PACER-FREE AND QT-FREE BY CONTRACT (stdlib + numpy duck-typing only): the three writers are
fed exclusively by Session accessors (lap_rows / lap_sector_splits / lap_corner_stats /
lap_channels / ...), so this module never imports the compiled `pacer` bindings or Qt — the
studio architecture rule for analysis/IO modules. app.py owns the File ▸ Export submenu, the
QFileDialog save prompts, and the widget→PNG grabs (the only Qt-touching part of the report)
and passes the bytes in. Nothing here ever writes a file without being called explicitly with
a user-chosen path — there is no implicit/automatic export anywhere.

Float formatting policy (three deliberately different precisions):
  * channels.csv — `repr()` of the Python float: the shortest string that round-trips to the
    EXACT same double, so a re-parse with `float()` reproduces the Session arrays
    bit-for-bit (np.array_equal — asserted on the real session at verification).
  * laps.csv — human-readable 3 decimals: sub-ms digits are GPS noise anyway (the validated
    timing floor is ~50–90 ms per lap), and the file is meant to be READ.
  * the report's Laps table — the APP'S precision per kind of value (`_DISPLAY`): a page a person
    reads beside the app must not print a digit the app's own tables never show.

DISCLOSURES TRAVEL WITH THE NUMBER (§5.4). Everything written here leaves the app: there is no
tooltip, no neighbouring tile and no hover behind an exported file. So every value that is an
ORDER STATISTIC rather than a measurement carries what it was taken over, in the file — the ideal
lap's `ideal lap · N laps` and its full sample sentence, both read straight off
`corner_model.IdealSample` (the same object the Stats tile caption reads), never re-composed here.

EVERY WRITE IS ATOMIC (`_atomic_write`: temp file + `os.replace`), the same contract the four
persistence stores hold. These were the app's last non-atomic writes: a writer that raised
mid-file — an ENOSPC on a 60-lap channels CSV, an unreadable accessor a third of the way down the
report — left a truncated file at the user's chosen path that looked like a finished export, and
overwriting a good previous export destroyed it. Now a failure leaves the previous file exactly as
it was and nothing new behind.
"""

from __future__ import annotations

import base64
import contextlib
import csv
import html
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import NamedTuple, TextIO, cast

from . import APP_NAME, coaching, data_quality, story, units
from . import stats as stats_service
from ._signal import (
    DASH,
    DIST_DECIMALS,
    PRINT_DECIMALS,
    SPEED_DECIMALS,
    exclusion_summary,
    fmt_hms,
    fmt_time,
    lap_label,
)

# laps.csv `flag` column value mirroring the lap table's ⚠ low-confidence marker (a GPS
# dropout inside the lap — its time/distance are less reliable). Clean laps carry "".
DROPOUT_FLAG = "gps-dropout"

# laps.csv's per-lap distance column. It is the lap odometer, which reads short of the distance
# driven (`stats.ODOMETER_NOTE`), so the file's trailer carries that note under this label, the
# way it carries INTERPOLATED_NOTE under INTERPOLATED_COLUMN's.
DIST_COLUMN = "dist_m"

# laps.csv's C5 disclosure column: which of a lap's corner cells are interpolated rather than
# measured. One name and one separator, so the writer, the HTML report, the legend under both and
# the test that pins the column set cannot spell it three ways. A space, like `quality`'s, because
# the file is comma-separated and a corner label never contains one.
INTERPOLATED_COLUMN = "corners_interpolated"
INTERPOLATED_SEP = " "
# PURE ASCII, like `data_quality.MARK_MEANING` and for its reason: this sentence is a laps.csv
# trailer row, and that file has been ASCII its whole life (a spreadsheet guessing MacRoman renders
# an em dash as three characters of junk). It shipped with one, and `test_csv_trailer_stays_ascii`
# could not see it: its session had no interpolated cell, so this row was never written there.
INTERPOLATED_NOTE = (
    "corners_interpolated lists the corners this lap did not match on track: their "
    "*_time_s and *_apex_* cells are interpolated between the neighbouring matches, which put "
    "them a median 0.22 s off an independently timed crossing against 0.004 s for a matched one, "
    "measured on one recording before a September 2026 change to corner matching and not "
    "re-measured since. Every value is "
    "still written; the column says which ones the app's own corner tables leave out.")

# laps.csv trailer (the session-summary footer rows mirroring the lap table's footer below
# the table): a labeled section AFTER the lap rows, separated by one blank row, led by its own
# `summary,time_s,over_laps,note` mini-header so the file stays cleanly parseable (split on the
# blank row, or filter the `summary` marker — the rows are FOUR wide, so read them by index, not
# as pairs; see `SummaryRow`). Each pair is (label, Session accessor), so the
# trailer can only ever print what the app's own surfaces print. A None value (no valid laps / no
# corner partition) writes a blank time cell, like the app's em-dash. The LABELS are the file's
# machine-readable contract and stay as they are: "Theoretical best" is the ideal lap, which the
# app's live surfaces call Δideal.
SUMMARY_MARKER = "summary"
SUMMARY_ROWS = (("Theoretical best", "theoretical_best"), ("Best rolling", "best_rolling_lap"))


class SummaryRow(NamedTuple):
    """One laps.csv trailer row: the machine label, the value, and the row's DISCLOSURE.

    §5.4 — the trailer printed "Theoretical best,62.869" bare, while every in-app surface showing
    that same number captions it `ideal lap · N laps`. The ideal is a minimum over the
    session's clean laps, so the count is not decoration: on the owner's D24 three-chapter
    recording the same driving reads 67.917 s over 5 laps and 66.709 s over 65
    (`corner_model.IdealSample`). A CSV row stating the second without the "65" invites a
    comparison against the first that the number cannot support.

    THE LABEL IS UNCHANGED, DELIBERATELY. #212-F4 pinned `SUMMARY_ROWS`' labels as this file's
    machine-readable contract (the module note above says so), and a parser keying on
    "summary: Theoretical best" or reading column 1 as the time must keep working. So the
    disclosure is APPENDED as two new columns rather than folded into either:

      * `over_laps` — the bare integer, for a machine (blank when the row is not a sample);
      * `note` — the human sentence, for the person who opens the file in a spreadsheet.

    Both are empty strings on a row with nothing to disclose, so the trailer stays rectangular.

    WHAT THIS DOES BREAK, stated rather than glossed: the trailer rows are 4 wide where they were
    2, so a consumer that splits on the blank row and feeds the trailer straight to `dict()` —
    which is a shape THIS MODULE'S OWN header comment suggests ("split on the blank row, or filter
    the `summary` marker") — now raises `ValueError: dictionary update sequence element #0 has
    length 4; 2 is required`. Reading `row[0]` / `row[1]`, or `dict((r[0], r[1]) for r in ...)`,
    is unaffected. There are no such consumers in-repo; the note is here because "additive" is
    only true of the columns, not of every way a reader might have unpacked the row."""

    label: str      # the machine contract label, exactly as SUMMARY_ROWS spells it
    value: str      # 3-decimal seconds, or "" when the accessor returned None
    over_laps: str  # the sample size as a bare integer string, or "" (not a sampled target)
    note: str       # the human disclosure sentence, or ""


def _f3(v) -> str:
    """Human 3-decimal float formatting (laps.csv)."""
    return f"{float(v):.3f}"


class _CellFormat(NamedTuple):
    """How `laps_table` prints each KIND of value: the lap time, the lap distance, a speed (already
    in the column's unit), a sector split or corner time, and the text of a missing number."""
    time: Callable[[float], str]
    dist: Callable[[float], str]
    speed: Callable[[float], str]
    split: Callable[[float], str]
    missing: str


#: laps.csv: 3 decimals everywhere and a blank for a missing number — the file's machine contract,
#: byte for byte what it has always written.
_CSV = _CellFormat(_f3, _f3, _f3, _f3, "")
#: The report's Laps table: what the app prints for the same cell. The lap time keeps fmt_time's
#: three decimals (the app's Time column: two laps a thousandth apart must not read level); the
#: distance is the Laps tab's whole metres; every speed the lap/corner tables' one decimal; a split
#: or corner time PRINT_DECIMALS, the resolution the app's ★ and ▼ are decided at; a missing number
#: is the app's dash, never a blank between numbers.
_DISPLAY = _CellFormat(
    time=_f3,
    dist=lambda v: f"{float(v):.{DIST_DECIMALS}f}",
    speed=lambda v: f"{float(v):.{SPEED_DECIMALS}f}",
    split=lambda v: f"{float(v):.{PRINT_DECIMALS}f}",
    missing=DASH)


def _atomic_write(path: str, body: Callable[[TextIO], object], *, newline: str | None = None) -> None:
    """Write `path` by filling a sibling `.tmp` and `os.replace`-ing it into place — the SAME
    contract prefs/library/track_db/sidecar hold, and for the same reason: a write that fails
    part-way must not be able to damage what was already there.

    `body(f)` does the actual writing into an open UTF-8 text handle; `newline` is passed to
    `open` (the csv module requires `""`, the report wants the default). `os.replace` is atomic
    within a filesystem, and the temp is created in the DESTINATION'S OWN DIRECTORY so it always is
    one — a temp in $TMPDIR would degrade to a cross-device copy and lose the guarantee.

    THE TEMP NAME IS UNIQUE (`mkstemp`), NOT `path + ".tmp"`. This writes wherever the user
    pointed a save dialog — so a fixed name would open, TRUNCATE and then delete (or worse,
    `os.replace` away) a file of the user's called `laps.csv.tmp` that happened to be sitting
    there. `mkstemp` also refuses to clobber, so two exports racing in one folder cannot interleave
    into each other's file. (The persistence stores once argued a fixed `<file>.tmp` could only
    collide with itself; a second writer of the same store is exactly that, and it wiped whole
    stores — RISK-4. They now write through `_jsonstore.write_json` for this reason.)

    On ANY exit that did not consume the temp (an exception from `body`, an ENOSPC in the flush on
    close, a KeyboardInterrupt) the partial file is removed, so a failed export leaves neither a
    truncated document at the user's chosen path nor litter beside it. Removal is itself guarded:
    failing to clean up must not replace the real error with a second one."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".",
                               prefix=f".{os.path.basename(path)}.", suffix=".part")
    try:
        with os.fdopen(fd, "w", newline=newline, encoding="utf-8") as f:
            body(f)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):  # only reachable when os.replace never ran
            with contextlib.suppress(OSError):
                os.remove(tmp)


def _speed_column(unit: str | None):
    """`(header suffix, km/h → column value)` for the table's speed columns.

    ``unit=None`` is the CANONICAL SI mode: km/h headers, km/h values, byte-for-byte what this
    module has always written. That is the CSVs' deliberate machine-readable contract (every
    column self-describing, never following the screen) and `write_laps_csv` must keep passing
    None. A unit id switches the columns to that DISPLAY unit — used ONLY by the report, which
    embeds unit-following chart/map images and so cannot label its table in a different unit from
    the picture 100 px above it (L12-01)."""
    if unit is None:
        return "kmh", lambda v: v
    u = units.normalize_unit(unit)  # the id doubles as the header suffix ("kmh" / "mph")
    return u, lambda v, _u=u: units.convert_speed(v, _u)


def laps_table(session, unit: str | None = None, *, display: bool = False
               ) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """(headers, rows) of the lap table — single-sourced for BOTH `write_laps_csv` and the
    report's HTML table, so the two always agree on rows, ordering and columns. One row per lap
    shown in the app's lap table (`session.lap_rows()` — the valid laps), as `(lap_id, cells)`;
    columns:

      lap, time_s, dist_m, entry_kmh, flag      the table's base columns; `flag` is the ⚠
                                                GPS-dropout marker (DROPOUT_FLAG / "")
      S1_s … SN_s                               per-sub-sector splits — present only when
                                                sector lines exist (like the app's table);
                                                a partial lap's missing splits stay blank
      C1_time_s, C1_apex_kmh, … per corner      time-in-corner + apex (min) speed from
                                                `session.lap_corner_stats` (the F2 corner
                                                model); blank when a lap has no stats
      quality                                   the row's QUALITY MARKERS, space-separated
                                                Analysis Function codes (`data_quality.lap_marks`)
                                                — "" for a row with nothing to disclose
      corners_interpolated                      the corner labels whose window this lap did NOT
                                                match on track (`INTERPOLATED_SEP`-separated) —
                                                "" when every corner was measured

    `corners_interpolated` IS THE FILE'S HALF OF THE ONE RULE (C5), AND IT KEEPS EVERY ROW. Since
    C4 the app's own corner surfaces count only cells matched on track at both edges: an
    interpolated window sat a median 0.22 s off an independent line-crossing time against
    0.004 s for a matched one on D24 before #335 (`CornerModel.lap_corner_resolved`). The CSV is
    an EXTERNAL FORMAT and a reader may be diffing this week's file against last week's, so
    nothing is dropped and no cell is blanked — the `C*_time_s` and `C*_apex_*` columns still
    carry every value they carried before, and this column says which of them are a guess between
    neighbouring matches. A reader that wants the app's numbers filters on it; a reader that wants
    the raw dump ignores it.
    Measured on the owner's D24 recordings: 34 of the 0060 pair's 456 exported cells are
    interpolated (7.5 %, over 16 of its 38 laps) and 0 of 0062's 780.

    THE `quality` COLUMN IS APPENDED, AND `flag` IS UNTOUCHED. `flag` is the file's oldest
    machine contract and a consumer testing `flag == DROPOUT_FLAG` keeps working; widening it
    into a code set would have broken exactly that reader. The new column goes LAST rather than
    beside `flag` because everything past index 4 is already variable in number (the splits and
    the corner pairs), so any consumer that reads that far must read by header name — which makes
    appending the one position that breaks nothing. The redundancy with `flag` is deliberate and
    is the same trade `SummaryRow` made: keep the contract, append the disclosure.

    WHY THE COLUMN EXISTS AT ALL. `flag` could only ever say "GPS dropout". A file exported from a
    PROVISIONAL session — every time measured from an auto-fitted start line the user never
    confirmed — carried a blank flag on every row and said nothing about it anywhere, while the app
    greys the share card out entirely on that same flag and the HTML report leads with it. That is
    §5.4's finding one surface further out, and `data_quality.lap_marks` is what closes it.

    `unit` selects the SPEED columns' unit and header suffix (see `_speed_column`): None — the
    default and the ONLY thing the CSV writer passes — keeps the canonical SI km/h; "mph"/"kmh"
    renders `entry_mph` / `C1_apex_mph` for the report's display-unit copy. Distances and times
    are SI in both modes (the app shows them in the same units on screen).

    `display=True` prints every number at the APP'S precision (`_DISPLAY`) — the report's copy —
    from the same raw values, never by re-rounding the CSV's strings (a 3.4051 split is "3.405" in
    the file and must still print the app's "3.41", not a twice-rounded "3.40"). Off by default,
    and the CSV writer never passes it: the rows, their order and the column set are the same."""
    fmt = _DISPLAY if display else _CSV
    rows_meta = session.lap_rows()
    n_sect = session.sector_count()
    n_splits = n_sect + 1 if n_sect else 0  # N lines -> N+1 sub-sectors; 0 lines -> none
    corner_list = session.corners.corner_list()
    dropout_ids = session.dropout_lap_ids()

    sfx, speed = _speed_column(unit)

    resolved_of = getattr(session.corners, "lap_corner_resolved", None)

    headers = ["lap", "time_s", DIST_COLUMN, f"entry_{sfx}", "flag"]
    headers += [f"S{i + 1}_s" for i in range(n_splits)]
    for c in corner_list:
        headers += [f"{c.label}_time_s", f"{c.label}_apex_{sfx}"]
    headers.append("quality")
    headers.append(INTERPOLATED_COLUMN)

    rows: list[tuple[int, list[str]]] = []
    for r in rows_meta:
        lap_id = r["idx"]
        # `lap` column is the 1-based lap NUMBER (lap_label), matching the app's Lap column;
        # the 0-based lap_id stays the internal key (row tuple / best-lap green class below).
        cells = [lap_label(lap_id), fmt.time(r["time"]), fmt.dist(r["dist"]),
                 fmt.speed(speed(r["entry"])), DROPOUT_FLAG if lap_id in dropout_ids else ""]
        splits = session.lap_sector_splits(lap_id) if n_splits else []
        for i in range(n_splits):
            cells.append(fmt.split(splits[i]) if i < len(splits) else fmt.missing)
        stats = {s.cid: s for s in session.corners.lap_corner_stats(lap_id)}
        for c in corner_list:
            s = stats.get(c.cid)
            cells += ([fmt.split(s.time), fmt.speed(speed(s.apex_speed))] if s is not None
                      else [fmt.missing, fmt.missing])
        # `dropout_ids` is passed in so the per-lap read does not re-fetch the set per row.
        cells.append(" ".join(data_quality.lap_marks(session, lap_id, dropout_ids)))
        cells.append(interpolated_corners(corner_list, resolved_of, lap_id))
        rows.append((lap_id, cells))
    return headers, rows


def interpolated_corners(corner_list, resolved_of, lap_id: int) -> str:
    """The `corners_interpolated` cell: the labels of the corners whose window this lap did not
    match on track at both edges, joined by `INTERPOLATED_SEP`, or "" when every one was matched.

    `resolved_of` is `CornerModel.lap_corner_resolved` (or None). getattr-guarded at the call site
    for the same reason every other optional accessor here is: the writers are driven by duck-typed
    Session doubles in the suite and by cross-recording references in the app, and a model with no
    warp must degrade to "nothing to disclose", never to a failed export. A corner the accessor has
    no flag for is left unmarked — this column's job is to name what is KNOWN to be interpolated,
    and a missing flag is not that."""
    if resolved_of is None:
        return ""
    flags = resolved_of(lap_id)
    return INTERPOLATED_SEP.join(c.label for k, c in enumerate(corner_list)
                                 if k < len(flags) and not flags[k])


def quality_key(headers, rows) -> list[tuple[str, str]]:
    """The KEY decoding every quality code `rows` actually carries — `(code, meaning)` pairs.

    A code with no key beside it is the failure the Analysis Function convention exists to
    prevent, so both writers render this under the table they wrote. It is derived from the ROWS
    rather than from the session, so the key can never list a mark the file does not contain (and
    can never omit one it does) — the two halves of a legend that would otherwise drift.

    The column is located BY NAME rather than as `cells[-1]`: `laps_table` appends it last today,
    and a future column appended after it would silently make this read corner apex speeds.
    `headers` is `laps_table`'s, which always carries the column (both callers pass it), so a
    missing one is a caller bug and raises rather than quietly emitting a file with no key."""
    col = list(headers).index("quality")
    present = {m for _lap_id, cells in rows if len(cells) > col for m in cells[col].split()}
    return data_quality.mark_key(present)


def any_interpolated(headers, rows) -> bool:
    """Does this file actually carry an interpolated corner cell? Located BY NAME for the reason
    `quality_key` gives, and read off the ROWS so the legend can never appear on a file that has
    nothing to explain (or be missing from one that does)."""
    col = list(headers).index(INTERPOLATED_COLUMN)
    return any(cells[col] for _lap_id, cells in rows if len(cells) > col)


def _ideal_sample(session):
    """`session.ideal_sample()` — what the ideal lap was minimised over — or None.

    getattr-guarded because every OTHER ideal read in this module is: the writers are driven by
    duck-typed Session doubles in the suite and by cross-recording references in the app, and a
    missing accessor must degrade to "no disclosure to print", never to a failed export."""
    return getattr(session, "ideal_sample", lambda: None)()


def laps_summary(session) -> list[SummaryRow]:
    """The session-summary footer values for the laps.csv trailer — one `SummaryRow` per
    SUMMARY_ROWS, mirroring the app's two stitched targets (F1): "Theoretical best" (the ideal lap
    — the quickest time through each corner and each straight, stitched together; the same number
    `Session.theoretical_best` feeds the app's hero Δideal) and "Best rolling" (the fastest
    start-anywhere full loop, shown in Stats ▸ PACE). The value is 3-decimal seconds (the same
    `_f3` precision as the lap rows' time_s column) or "" when the accessor returns None (no valid
    laps / no corner partition) — the app's tile shows the em-dash there. Read straight from the
    Session accessors, so the trailer always equals what the app displays.

    THE THEORETICAL ROW CARRIES ITS SAMPLE (§5.4): `over_laps` plus the disclosure sentence, both
    off `IdealSample` — `caption()` / `sentence()`, the same two strings the Stats tile prints — so
    the file and the screen cannot answer "how many laps is this over" differently. "Best rolling"
    gets no sample columns: it is a start-anywhere loop over the trace and the app's own tile
    states no count for it either, so inventing one here would be this file claiming a disclosure
    the rest of the app does not make.

    THE ROW IS DROPPED WHEN THE IDEAL IS A DUPLICATE, AND SECTOR LINES ARE NOT WHAT DECIDES THAT.
    This gate used to be `sector_count() == 0`, from the era when the theoretical best WAS the sum
    of the session-best sector splits and a track with no sector line collapsed to one sub-sector
    whose split is the best lap time. It now reads `ideal_donor_lap_id()`, which is the actual
    degenerate condition: not None means ONE lap won every segment, so the "ideal" is that lap and
    printing it beside the lap rows is a bare duplicate. The old gate is not merely stale, it was
    inverted on the recordings that matter — D24 and Sandown both have ZERO sector lines, so the
    export dropped the row on exactly the sessions where the ideal is now faster than anything
    driven — by 1.37 s on D24 one chapter and 1.49 s on three. (The Stats tile carries the twin
    of this gate.)"""
    degenerate = session.ideal_donor_lap_id() is not None
    sample = _ideal_sample(session)
    out: list[SummaryRow] = []
    for label, accessor in SUMMARY_ROWS:
        if accessor == "theoretical_best" and degenerate:
            continue  # one lap won every segment: the "ideal" IS that lap, so it says nothing
        v = getattr(session, accessor)()
        # Not None exactly on the theoretical-best row of a session with a sample.
        ideal = sample if accessor == "theoretical_best" else None
        out.append(SummaryRow(
            label=label,
            value=_f3(v) if v is not None else "",
            over_laps=str(ideal.laps) if ideal is not None else "",
            # `sentence()`, NOT `caption() + sentence()`. The caption's separator is a MIDDLE DOT
            # and laps.csv has been pure ASCII for its whole life — a machine-contract file that
            # suddenly renders "theoretical best Â· 24 laps" in a spreadsheet guessing MacRoman is
            # a wart this row does not need. The sentence carries the same counts in ASCII prose
            # ("Stitched from 11 of your 24 clean laps…") and `over_laps` carries the number a
            # parser wants, so nothing is lost; the two HUMAN surfaces (report, clipboard) print
            # the caption verbatim.
            note=ideal.sentence() if ideal is not None else ""))
    return out


def write_laps_csv(path: str, session) -> None:
    """One row per (valid) lap — number, time, distance, entry speed, splits, the ⚠ flag,
    and the per-corner time/apex-speed columns — followed by the session-summary TRAILER
    (theoretical-best / best-rolling, see `laps_summary`): a blank separator row, a
    `summary,time_s,over_laps,note` mini-header, then one labeled row per summary value. Human
    3-decimal floats (see module doc); the trailer keeps the file cleanly parseable (split on the
    blank row, or filter the leading `summary` marker).

    THE TRAILER GREW TWO COLUMNS, IT DID NOT CHANGE ONE. Columns 0 and 1 are byte-identical to what
    they always were (the label is #212-F4's pinned machine contract), and `over_laps` / `note`
    were appended to carry §5.4's missing disclosure. A parser reading `row[0]` / `row[1]` is
    unaffected; one that `dict()`s the trailer rows as pairs is NOT, because they are 4 wide now —
    see `SummaryRow` for the exact breakage and why it is stated rather than glossed.

    ALWAYS SI (km/h / m / s), never the app's display unit: this file is the machine-readable
    contract, so `laps_table` is called with no `unit` and every column stays self-describing
    (`entry_kmh`). The display unit belongs to surfaces a HUMAN reads — the app and the HTML
    report.

    THE TRAILER ALSO CARRIES THE QUALITY KEY. The `quality` column emits Analysis Function codes
    and a code with no key is the failure that convention exists to prevent, so every code the
    file actually contains gets a `quality [x]` trailer row decoding it — and a break in series
    gets one more naming WHICH break, because the code alone says only that there is one. They are
    extra LABELLED ROWS inside the existing 4-wide trailer rather than a second blank-separated
    section, so "split on the blank row" still yields exactly two parts and the file stays
    rectangular. Nothing is emitted for a clean session: a key listing four marks on a file that
    carries none teaches the reader that the marks are decoration.

    AND, BY THE SAME RULE, THE `corners_interpolated` KEY (C5). The column is a new one in this
    file's column set — say so wherever this change is announced — and it names corners whose
    cells the app's own tables do not count, so a file that carries one carries a trailer row
    saying what that means. Like the quality key it is derived from the ROWS: a session that
    matched every corner emits nothing.

    AND THE `dist_m` NOTE (TRUTH2-ODO): the column is the lap odometer, which reads short of the
    distance driven, so any file with a lap row carries `stats.ODOMETER_NOTE` under that label."""
    headers, rows = laps_table(session)
    summary = laps_summary(session)
    key = quality_key(headers, rows)
    broke = data_quality.break_in_series(session)
    interpolated = any_interpolated(headers, rows)

    def body(f):
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(cells for _lap_id, cells in rows)
        w.writerow([])  # blank separator: the lap rows end here, the trailer follows
        w.writerow([SUMMARY_MARKER, "time_s", "over_laps", "note"])
        for row in summary:
            w.writerow([f"{SUMMARY_MARKER}: {row.label}", row.value, row.over_laps, row.note])
        if rows:
            w.writerow([f"{SUMMARY_MARKER}: {DIST_COLUMN}", "", "", stats_service.ODOMETER_NOTE])
        for code, meaning in key:
            w.writerow([f"{SUMMARY_MARKER}: quality {code}", "", "", meaning])
        if interpolated:
            w.writerow([f"{SUMMARY_MARKER}: {INTERPOLATED_COLUMN}", "", "", INTERPOLATED_NOTE])
        if broke:
            w.writerow([f"{SUMMARY_MARKER}: break in series", "", "", broke])

    _atomic_write(path, body, newline="")


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

    def body(f):
        w = csv.writer(f)
        w.writerow(names)
        for i in range(n):
            w.writerow([repr(float(a[i])) for a in arrays])

    _atomic_write(path, body, newline="")


# ------------------------------------------------------------------- the stats summary
@dataclass(frozen=True)
class SummarySection:
    """One group of the Stats page, resolved to display strings (N13).

    `rows` are `(label, value)` where the label carries any caption the tile carries — the sample
    ("median lap · 24 clean laps"), the source lap ("top speed · lap 12"), the disclosure
    ("ideal lap · 24 laps") — because an exported line has no tile beneath it to put a
    caption on. An EMPTY value is the em-dash case: the accessor returned None, and the renderers
    print `DASH`, never a 0 (the None-not-zero rule the whole Stats layer is built on).

    `note` is the group's disclosure paragraph, printed under its table. SESSION's is the distance
    note (`_distance_note`: `stats.ODOMETER_NOTE` under a printed distance, or why it is withheld),
    IDEAL LAP's is `IdealSample.sentence()` verbatim, and DRIVING's names its braking and coasting
    instruments; the other groups carry none."""

    title: str
    rows: list[tuple[str, str]]
    note: str = field(default="")


#: The PACE row beside "σ lap" that carries the σ tile's second reading.
MIDDLE_HALF_ROW = "middle half · span"
#: IDEAL LAP's gap row: the figure the report's COACHING pointer quotes (`coaching.totals_pointer`).
IDEAL_GAP_ROW = "on the table · vs your best"


def _sec(v, fmt: str = "{:.2f} s") -> str:
    """A float value as a display string, or "" when the accessor said None. The one place this
    module turns "no signal" into "no value" — never into a zero."""
    return "" if v is None else fmt.format(v)


def _distance_note(tot) -> str:
    """The SESSION group's disclosure about its own `distance` row — `stats_panel._set_distance`'s
    tooltip rule, in prose the export can print.

    The path length is SPEED-GATED in the data layer (a GPS fix that teleports is not distance
    driven), and the page says so in two states: below `stats.MIN_KEPT_FRAC` the value is withheld
    entirely, and from a whole percent of rejected steps up the number stands with the caveat
    attached. Both are disclosures the page makes on hover — so on a surface with no hover they
    have to be printed, which is the same argument §5.4 makes about the ideal's lap count.

    A PRINTED distance also carries `stats.ODOMETER_NOTE`, in both states, as the tile's tooltip
    does: the smoothed trace reads short of the distance driven whether or not the gate dropped a
    step. A withheld one has no number for it to qualify."""
    kept = getattr(tot, "distance_kept_frac", 1.0)
    if tot.distance_m is None:
        return (f"Distance not shown: only {kept * 100:.0f}% of this trace's GPS steps are "
                "physically possible at the speed the same trace reports — the rest are dropped "
                "fixes, so a path length would be a fiction. The recorded time and the lap "
                "statistics are unaffected.")
    # A handful of rejected steps is not worth a caveat that would round to "0%" (a real 26-minute
    # recording rejects 0.02%) — the page uses the same 1% floor.
    if kept >= 0.99:
        return stats_service.ODOMETER_NOTE
    return (f"Distance: {(1 - kept) * 100:.0f}% of the raw GPS steps were rejected as impossible "
            "at the speed the same trace reports (dropped fixes) and are not counted. "
            + stats_service.ODOMETER_NOTE)


def _timing_meta(session, *, where: str = "below") -> str:
    """What every lap time on an exported surface is worth — the report's `Timing` meta row AND the
    clipboard summary's `Timing:` header line, one string so the two cannot disagree.

    Two facts, the same two the app gates on and in the same order it does:

      * `timing_verified` False — the start/finish line was auto-fitted and never confirmed, so
        every lap time and split is measured from an arbitrary point. This is what greys the share
        card out entirely and what puts the amber banner on the map and the Stats page; a document
        that leaves the app stating those times with no such qualifier is the same defect one
        surface further out.
      * `timing_quality.degraded` — the media-clock fallback, a concerning share of rejected GPS
        fixes, or no GPS at all. `concerns()` is the shipped sentence list the in-app data-quality
        banner stacks, joined here rather than re-worded, behind the chip's own word for the state
        (`data_quality.timing_word`).

    Neither wrong ⇒ "verified start line · GPS9 true clock", the plain good case. getattr-guarded
    throughout: a Session double without these is reported as the good case, never as a crash in
    the middle of writing a report."""
    bits = []
    # Not on a recording with no GPS trace: no line was fitted there, and the NO GPS bit below
    # says what is actually wrong (data_quality.no_start_line).
    if (not getattr(session, "timing_verified", True)
            and not data_quality.no_start_line(session)):
        # `where` keeps the sentence true where it stands: "below" under the report's header and
        # the clipboard's, "on this page" where a section repeats it (`_timing_caveat`).
        bits.append("PROVISIONAL — the start/finish line was auto-fitted and not confirmed, so "
                    f"every lap time and split {where} is measured from an arbitrary point.")
    quality = getattr(session, "timing_quality", None)
    concerns = quality.concerns() if quality is not None else []
    if concerns:
        # Led by the lap panel's own chip word (ESTIMATED / GPS LOW / NO GPS), not by
        # "ESTIMATED" for all three: that called a true-clock recording's times estimated, and a
        # recording with no GPS at all "ESTIMATED — No GPS fixes survived", beside a chip saying
        # NO GPS. The burned overlay stamp already used the chip's words.
        bits.append(f"{data_quality.timing_word(quality) or 'ESTIMATED'} — " + " ".join(concerns))
    if not bits:
        bits.append("verified start line · GPS9 true clock")
    return " · ".join(bits)


# The public name, for a reader outside this module: the share card's provenance footer prints this
# string rather than its own. `_timing_meta` stays the name this module's own callers use.
timing_meta = _timing_meta


def stats_summary(session, unit: str | None = None) -> list[SummarySection]:
    """The Stats page's groups as data — for the HTML report and "Copy stats summary" (N13).

    IT READS THE SAME ACCESSORS THE PAGE READS, and that is the whole design: every value here
    comes from `session.stats` (`SessionStats.totals/pace/race_pace/pace_cov/laps_within_pct/
    pace_trend/lap_stats/session_vmax/longest_coast_s/gg_envelope`), from `Session`'s own ideal
    family, or from the lap-set accessors — the identical calls in `stats_panel.refresh`. The
    report cannot drift from the page because there is no second computation to drift.

    GROUPS APPEAR AND VANISH LIKE THE PAGE'S DO. A group whose signal is absent is OMITTED, not
    printed full of dashes: no g-meter means no DRIVING group, exactly as `_refresh_driving` hides
    it; a degenerate ideal (one lap won every segment) drops the IDEAL LAP group, the same
    `ideal_donor_lap_id()` gate `IdealSection.refresh` and `laps_summary` take. Within a shown group an
    individual absent value still reads as an em-dash, because there the dash IS the information.

    `unit` is the reader's display speed unit, matching `laps_table`'s parameter: the report's
    embedded charts already follow it, so its speed tiles must too. None keeps km/h.

    DELIBERATELY NOT MIRRORED, both because a copy is the drift this function exists to prevent:

      * the "median lap · top N fixed" coaching digest — retired from the page as well (R11): the
        Coaching tab is the one answer to where the time is; its rows, shortlist and words are
        `coaching.page_text`'s, for a COACHING section to print rather than a digest to copy.
      * the DATA TRUST card's provenance rows — built inline in `stats_trust.TrustSection.refresh`, so
        they would have to be re-worded here. The two TRUST facts that qualify the exported
        NUMBERS travel instead as the report's own `Timing` meta row (`write_report_html`), off
        `timing_verified` / `timing_quality`. Routing the full card through a shared builder is a
        follow-up, not a copy."""
    import numpy as np  # local: the module's other three writers need no numpy at all

    unit_id = units.normalize_unit(unit)
    u_label = units.speed_label(unit_id)
    st = getattr(session, "stats", None)
    out: list[SummarySection] = []

    # --- SESSION totals. The lap count SPELLS OUT its marks: the page prints "24 · 14 ⊘ · 2 ⚠"
    # under a tooltip that decodes the glyphs, and an exported line has no tooltip (§5.4 again).
    valid = list(session.valid_lap_ids()) if hasattr(session, "valid_lap_ids") else []
    excluded = getattr(session, "excluded_lap_ids", list)() or []
    dropouts = session.dropout_lap_ids() if hasattr(session, "dropout_lap_ids") else set()
    lap_bits = [f"{len(valid)} valid"]
    if excluded:
        # The reason per lap, counted — "distance off the session median" was printed for every
        # excluded lap, including one with a stop and one that does not end where it started.
        why = exclusion_summary(getattr(session, "excluded_lap_reasons", dict)() or {})
        lap_bits.append(f"{len(excluded)} excluded ({why})" if why else f"{len(excluded)} excluded")
    if dropouts:
        lap_bits.append(f"{len(dropouts)} with a GPS dropout")
    rows = [("laps", " · ".join(lap_bits) if valid else "")]
    tot = st.totals() if st is not None else None
    note = ""
    if tot is not None and tot.duration_s > 0:
        rows += [(stats_service.DURATION_CAPTION, fmt_hms(tot.duration_s)),
                 ("moving", fmt_hms(tot.moving_s)),
                 # None below stats.MIN_KEPT_FRAC: the trace was too broken for a path length to
                 # mean anything, and the page dashes it rather than printing a fiction.
                 ("distance", "" if tot.distance_m is None
                  else f"{tot.distance_m / 1000.0:.1f} km"),
                 ("on track", f"{tot.start_clock}–{tot.end_clock}"
                  if tot.start_clock and tot.end_clock else "")]
        note = _distance_note(tot)
    else:
        rows += [(stats_service.DURATION_CAPTION, ""), ("moving", ""), ("distance", ""),
                 ("on track", "")]
    out.append(SummarySection("SESSION", rows, note=note))

    # --- PACE. GATED ON VALID LAPS, NOT ON THE PACE SUMMARY, because that is the page's gate
    # (`refresh` shows the section whenever `valid_lap_ids()` is non-empty and dashes the tiles
    # inside it) — and the difference is not cosmetic. `pace` runs over the CONSISTENCY laps
    # (valid ∧ dropout-free); a session whose every lap carries a GPS dropout has valid laps and no
    # consistency laps, so gating the GROUP on `pace` dropped the whole block from the export while
    # the page rendered it. `best rolling` is the number that made it a real loss: it is a
    # start-anywhere loop off `Session`, not a pace statistic, and the page sets it OUTSIDE the
    # pace branch for exactly that reason — inside the gate it vanished from a session that still
    # had one. So it is read here, outside, like `refresh()` does.
    pace = st.pace() if st is not None else None
    rolling = session.best_rolling_lap() if hasattr(session, "best_rolling_lap") else None
    if valid:
        # EVERY PACE-DERIVED VALUE IS READ ONLY WHEN THERE IS A DISTRIBUTION, which is the page's
        # own structure (`refresh` fills these eight tiles inside `if pace is not None` and dashes
        # all eight in the else). It is not defensive tidiness: `stats.within_pct_of_best` returns
        # 0 — not None — for an empty input, so reading it unconditionally printed "0 / 0" into a
        # report whose page shows an em-dash. A zero manufactured from no laps is exactly the
        # None-not-zero rule inverted, on the surface least able to explain itself.
        median_label = "median lap"
        pace_rows = [("best lap", ""), (median_label, ""),
                     ("race pace · best 3-lap mean", ""), ("σ lap", ""), ("median − best", ""),
                     ("consistency · σ/median", ""), ("within 1% of best", ""), ("trend", "")]
        if pace is not None and st is not None:  # a pace implies stats; the checker needs it said
            count, n_within = st.laps_within_pct(1.0)
            trend = st.pace_trend()
            rp = st.race_pace()
            cov = st.pace_cov()
            # "trend · improving" / "· steady" / "· fading" — the page's caption, off the SHARED
            # verdict (stats.trend_verdict), so the exported row cannot narrate a different story
            # from the tile. Falls back to the page's base caption on too short a sample.
            verdict = stats_service.trend_verdict(trend)
            pace_rows = [
                ("best lap", fmt_time(pace.best)),
                # The median's caption names its own n. Singular at n=1 — the defect this page
                # shipped once as "median · 1 clean laps".
                (f"median lap · {pace.n} clean lap{'' if pace.n == 1 else 's'}",
                 fmt_time(pace.median)),
                ("race pace · best 3-lap mean", "" if rp is None else fmt_time(rp)),
                ("σ lap", _sec(pace.sigma)),
                ("median − best", _sec(pace.spread, "+{:.2f} s")),
                ("consistency · σ/median", _sec(cov, "{:.1f} %")),
                ("within 1% of best", "" if count is None else f"{count} / {n_within}"),
                ("trend" if verdict is None else f"trend · {verdict}",
                 stats_service.fmt_trend(trend) or ""),
            ]
            # The σ tile's caption line, in the page's own words — and, like that line, absent
            # below the five-lap floor rather than a bare dash.
            width = stats_service.fmt_middle_half(pace.middle_half)
            if width is not None:
                pace_rows.insert(4, (MIDDLE_HALF_ROW, width))
        # `best rolling` sits AFTER the pace rows and OUTSIDE the branch above, where the page puts
        # it: a start-anywhere loop off `Session` is not a pace statistic and survives a session
        # that has no distribution at all.
        pace_rows.insert(3, ("best rolling", "" if rolling is None else fmt_time(rolling)))
        out.append(SummarySection("PACE", pace_rows))

    # --- IDEAL LAP. Same three gates as the page (`IdealSection.refresh`): a composite exists,
    # more than one lap donated, and there is a best lap to measure the gap against.
    sb = getattr(session, "ideal_segment_bests", lambda: None)()
    best_id = session.best_lap_id() if hasattr(session, "best_lap_id") else None
    single = (session.ideal_donor_lap_id() if hasattr(session, "ideal_donor_lap_id") else None)
    if sb is not None and single is None and best_id is not None and best_id in sb.lap_ids:
        smp = sb.sample
        # The best lap's time READ OFF THE COMPOSITE, exactly as the tile does it, so
        # `gap == Σ gains` holds by construction rather than by two accessors agreeing.
        gap = float(np.asarray(sb.times[sb.lap_ids.index(best_id)]).sum()) - sb.total
        out.append(SummarySection("IDEAL LAP", [
            (smp.caption(), fmt_time(sb.total)),
            # UNSIGNED, as the tile prints it: time you can find is a magnitude (LOOK-7).
            (IDEAL_GAP_ROW, f"{gap:.2f} s"),
        ], note=smp.sentence()))

    # --- SPEED · G: session peaks over the per-lap stats, the page's own reductions.
    lap_rows = st.lap_stats() if st is not None else []
    if lap_rows and st is not None:  # rows imply stats; the checker needs it said
        vmax = st.session_vmax()
        slow = getattr(st, "slowest_corner", lambda: None)()
        lat_peaks = [r.peak_lat_g for r in lap_rows if r.peak_lat_g is not None]
        brk_peaks = [r.peak_brake_g for r in lap_rows if r.peak_brake_g is not None]
        out.append(SummarySection("SPEED · G", [
            # The lap number is 1-BASED on every user-facing surface, this one included.
            ("top speed" if vmax is None else f"top speed · lap {lap_label(vmax[1])}",
             "" if vmax is None else
             f"{units.convert_speed(vmax[0], unit_id):.1f} {u_label}"),
            # The page's TYPICAL slowest corner, off the same accessor (LOOK-12).
            (stats_service.VMIN_CAPTION,
             "" if slow is None else f"{units.convert_speed(slow[0], unit_id):.1f} {u_label}"),
            ("peak lateral g", _sec(max(lat_peaks) if lat_peaks else None, "{:.2f} g")),
            ("peak braking g", _sec(max(brk_peaks) if brk_peaks else None, "{:.2f} g")),
        ]))

        # --- DRIVING, hidden entirely without a g signal (the page hides the whole group).
        brake = [r.brake_s for r in lap_rows if r.brake_s is not None]
        counts = [r.brake_n for r in lap_rows if r.brake_n is not None]
        coast = [r.coast_s for r in lap_rows if r.coast_s is not None]
        if brake or counts or coast:
            # Two of these five rows are coasting figures, and a coasting figure is only as
            # meaningful as the window / minimum duration / band behind it — those three settings
            # move it by more than 6x on the same recording. So the group carries the instrument
            # as its note, the way IDEAL LAP carries its sample sentence. The braking time gets its
            # own sentence first (LOOK-2): it is the time AT OR PAST the threshold inside each
            # event, not the event's span, and an exported table has no tooltip to say so.
            # getattr-guarded: a Session double without the driving service still exports the
            # numbers, noteless.
            drv = getattr(session, "driving", None)
            note = " ".join(x for x in (
                getattr(drv, "brake_time_instrument", lambda: None)() if drv else None,
                getattr(drv, "coast_instrument", lambda: None)() if drv else None) if x)
            out.append(SummarySection("DRIVING", [
                (stats_service.BRAKE_TILE_CAPTION,
                 _sec(float(np.median(brake)) if brake else None, "{:.1f} s")),
                ("brake events / lap",
                 _sec(float(np.median(counts)) if counts else None, "{:.0f}")),
                ("coasting / lap · median",
                 _sec(float(np.median(coast)) if coast else None, "{:.1f} s")),
                ("longest coast", _sec(st.longest_coast_s(), "{:.1f} s")),
                ("grip envelope · p98", _sec(st.gg_envelope(), "{:.2f} g")),
            ], note=note))
    return out


def stats_summary_text(session, unit: str | None = None, *, title: str = "") -> str:
    """`stats_summary` as pasteable plain text — the "Copy stats summary" clipboard payload.

    Plain text, not markdown or HTML: it is pasted into a chat, a note or a forum box, and the one
    thing it must survive is being read as-is. Values are aligned into a column at a fixed offset
    so a proportional font still lets the eye run down them; an absent value prints `DASH`, the
    same mark the page and the report show.

    IT CARRIES THE TIMING VERDICT, in the header, for the same reason the report does — and more
    urgently. This block lands in a chat as raw text with no page around it and nothing to hover,
    so it is the LEAST qualified surface the numbers reach: measured on a recording whose track is
    unrecognised (`timing_verified` False — the state any unknown circuit loads in), the app
    DISABLES the share-card action outright and the report prints "PROVISIONAL — every lap time and
    split below is measured from an arbitrary point", while this text published a best lap, a
    median, a race pace and an ideal lap over 38 laps with no qualifier anywhere in it. Same
    `_timing_meta` the report row uses, so the two cannot say different things.

    Disclosure, not refusal: the export path's standing choice (the report states the caveat rather
    than withholding the document), so the menu action stays enabled.

    `title` is the recording label the caller has (app.py's `_loaded_label` — the chapters that
    LOADED, not the ones requested); omitted when there is none, rather than printed as an empty
    line."""
    sections = stats_summary(session, unit)
    head = [f"{APP_NAME} — session summary"]
    bits = [b for b in (title, getattr(session, "track_name", "") or "",
                        (session.session_date() if hasattr(session, "session_date") else "") or "")
            if b]
    if bits:
        head.append(" · ".join(bits))
    head.append(f"Timing: {_timing_meta(session)}")
    out = ["\n".join(head)]
    for sec in sections:
        block = [sec.title]
        width = max((len(label) for label, _v in sec.rows), default=0)
        block += [f"  {label.ljust(width)}   {value or DASH}" for label, value in sec.rows]
        if sec.note:
            block.append(f"  {sec.note}")
        out.append("\n".join(block))
    return "\n\n".join(out) + "\n"


# --------------------------------------------------------------------- session report
# ONE SELF-CONTAINED PAGE: inline CSS, PNGs as data URIs, no JavaScript and no external asset, so it
# opens ANYWHERE — mail attachments, archives, file:// — and stays readable as source. Well-formed
# (XML-parseable) markup. Each section is one small function returning its lines, in the app's own
# order (the Stats page's, then the Laps tab's), and each prints what the app's own surface prints,
# read from the SAME function: `stats_summary` (the Stats tiles), `story.shown_moments` /
# `story.moment_row` (MOMENTS), `Session.corner_report` + `stats.marked_corners` (CORNERS and its
# ◆), `Session.slow_lap_notes` (the Laps table's slow-lap notes), `share_lapstrip` (the lap strip,
# rendered by the caller). The honesty rules live in those functions, never here.

#: The app tokens the report's screen style is drawn in, by `theme.C` name. theme.py imports Qt and
#: this module may not (tests/test_layering.py), so the caller passes their values
#: (`export_controller.report_tokens`, off theme) and nothing here types an app colour.
REPORT_TOKENS = ("canvas", "surface", "border", "text", "text_dim", "accent", "ahead", "behind")
#: PAPER: the `@media print` variant, ink on white, and the whole page for a caller that passes no
#: tokens. NOT app tokens — the app has no light theme; these only have to print.
PAPER_TOKENS = {"canvas": "#FFFFFF", "surface": "#FFFFFF", "border": "#C8CCD2",
                "text": "#1A1A1A", "text_dim": "#4A505A", "accent": "#8A5A00",
                "ahead": "#0A7D33", "behind": "#B3261E"}
#: The report's CORNERS columns: the Stats page's (stats_corners.CORNER_COLUMNS) with the σ cell's
#: hover — the middle half — as a column of its own (a page has no hover), and without the
#: estimated Grip column, whose comparison note (theme.GRIP_COMPARE_NOTE) lives behind Qt.
REPORT_CORNER_COLUMNS = ("Corner", "Best (s)", "Median (s)", "σ (s)", "Middle half (s)",
                         "Med loss (s)", "Apex best", "Apex med")
#: The lap-strip card's layout width in the document: half its 1080 px canvas, a 2x image.
LAPSTRIP_LAYOUT_PX = 540

_REPORT_CSS = """
body { margin: 0; background: var(--canvas); color: var(--text); font-size: 14px;
       line-height: 1.45; font-family: -apple-system, Inter, "Helvetica Neue", Arial, sans-serif; }
main { max-width: 70em; margin: 0 auto; padding: 24px 16px 48px; }
header { border-bottom: 2px solid var(--accent); padding-bottom: 12px; margin-bottom: 16px; }
h1 { font-size: 1.5em; font-weight: 600; margin: 0 0 8px; }
/* Upper case by style, not by text: "Laps (14)" stays the string a reader searches for. */
h2 { font-size: 0.8em; font-weight: 600; letter-spacing: 0.08em; color: var(--text-dim);
     margin: 0 0 8px; text-transform: uppercase; }
section { background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
          padding: 12px 16px; margin: 0 0 16px; overflow-x: auto; }
/* A table wider than its card scrolls sideways; these scroll shadows show that it does, with no
   script: the two `local` covers move with the content and hide the `scroll` shadows at an end. */
section.wide { background:
    linear-gradient(to right, var(--surface) 40%, transparent) left / 32px 100% no-repeat local,
    linear-gradient(to left, var(--surface) 40%, transparent) right / 32px 100% no-repeat local,
    linear-gradient(to right, var(--text-dim), transparent) left / 12px 100% no-repeat scroll,
    linear-gradient(to left, var(--text-dim), transparent) right / 12px 100% no-repeat scroll,
    var(--surface); }
table { border-collapse: collapse; margin: 4px 0; }
th, td { border-bottom: 1px solid var(--border); padding: 4px 10px; text-align: right;
         font-variant-numeric: tabular-nums; }
th { color: var(--text-dim); font-weight: 600; }
tr.best td { color: var(--ahead); font-weight: 600; }
/* The laps table is the page's widest: human headers that wrap only BETWEEN words (a corner's
   unit under its "C1", the group heading centred over its pair), and cells a notch tighter. */
table.laps { font-size: 0.85em; }
table.laps th, table.laps td { padding: 3px 4px; }
table.laps th { vertical-align: bottom; word-break: normal; overflow-wrap: normal;
                hyphens: manual; }
table.laps th.grp { text-align: center; }
table.meta th, table.meta td, table.kv th, table.moments th, table.moments td { text-align: left; }
td.mark { color: var(--behind); font-weight: 600; }
p.note { margin: 6px 0 0; max-width: 46em; color: var(--text-dim); font-size: 0.9em; }
ul.notes { margin: 4px 0; padding-left: 1.2em; }
ul.notes li { margin: 4px 0; }
/* height:auto keeps the aspect ratio when the max-width clamp (or a stated width, see
   write_report_html) shrinks a figure below its natural pixel size. */
img { max-width: 100%; height: auto; border: 1px solid var(--border); border-radius: 4px;
      margin: 4px 0; }
/* The opening: the lap card beside COACHING, so the answer is on the first screen; one column on
   a narrow one, the card at most its layout width there. */
.front { display: grid; gap: 16px; align-items: start; margin: 0 0 16px; }
.front > section { margin: 0; }
.front.with-card { grid-template-columns: minmax(200px, 30%) minmax(0, 1fr); }
figure.hero { margin: 0; }
figure.hero img.card { display: block; width: 100%; margin: 0; }
@media (max-width: 640px) {
  .front.with-card { grid-template-columns: minmax(0, 1fr); }
  figure.hero { max-width: 360px; }
}
p.lead { font-size: 1.15em; font-weight: 600; margin: 0 0 6px; }
p.body { margin: 0 0 6px; max-width: 46em; }
ul.actions { margin: 4px 0 8px; padding-left: 1.2em; }
ul.actions li { margin: 2px 0; }
/* The page's columns: numbers right, the corner and its reason left; a muted row is not ranked. */
table.coaching { width: 100%; }
table.coaching th:first-child, table.coaching td:first-child, table.coaching td.why,
table.coaching th:last-child { text-align: left; }
table.coaching th, table.coaching td { white-space: nowrap; vertical-align: top; }
table.coaching td.why { width: 100%; white-space: normal; }
td.lost { color: var(--behind); }
tr.muted td, td.dim { color: var(--text-dim); }
tbody.more tr:first-child td { border-top: 2px solid var(--border); }
@media print {
  body { font-size: 10pt; }
  main { max-width: none; padding: 0; }
  /* Paper cannot scroll: every column must be on the page, so nothing clips, and a tall section
     may break across pages (only small blocks are kept whole, or page 1 ends half blank). */
  section, section.wide { overflow: visible; border-radius: 0; break-inside: auto;
                          background: var(--surface); }
  h2 { break-after: avoid; }
  tr, li, p.note, img, table.kv, table.meta, table.moments { break-inside: avoid; }
  /* The laps table carries 5 + 2 per corner + split + 2 disclosure columns: small type and tight
     cells fit it to the paper's width rather than cut it at C3. The headers wrap between words
     only (the screen rule); breaking anywhere cut "C1_apex_kmh" mid-word when they were CSV
     names. */
  table.laps { width: 100%; font-size: 6.5pt; }
  table.laps th, table.laps td { padding: 1px 2px; }
  /* The lap strip and the lap card are dark cards on white paper: a hairline says each is one. */
  img.card { border: 1px solid var(--text); border-radius: 0; }
  /* The opening stays side by side on paper, the card a third of the width, kept whole. */
  .front.with-card { grid-template-columns: 58mm minmax(0, 1fr); }
  figure.hero { max-width: none; break-inside: avoid; }
}
"""


def _token_block(tokens) -> str:
    """`--name: #hex;` for every REPORT_TOKENS entry. Values must be plain #RRGGBB colours:
    anything else would be a token source writing CSS (or markup) into the page."""
    out = []
    for name in REPORT_TOKENS:
        value = tokens[name]
        if not (value.startswith("#") and len(value) == 7
                and all(ch in "0123456789abcdefABCDEF" for ch in value[1:])):
            raise ValueError(f"report token {name!r} is not a #RRGGBB colour: {value!r}")
        out.append(f"--{name.replace('_', '-')}: {value};")
    return " ".join(out)


def report_css(tokens=None) -> str:
    """The page's style: the app's dark `tokens` on screen, PAPER_TOKENS in print."""
    screen = {**PAPER_TOKENS, **(tokens or {})}
    return (f":root {{ {_token_block(screen)} }}\n"
            f"@media print {{ :root {{ {_token_block(PAPER_TOKENS)} }} }}\n{_REPORT_CSS}")


def _note(text: str) -> str:
    return f'<p class="note">{html.escape(text)}</p>'


def _section(title: str, body: list[str]) -> list[str]:
    return ["<section>", f"<h2>{html.escape(title)}</h2>", *body, "</section>"]


def _kv_table(rows, cls: str = "kv") -> list[str]:
    """Label left, value right. An absent value prints the em-dash the app prints, never a 0 and
    never a blank cell that reads as "we forgot" (None-not-zero, out to the exported document)."""
    esc = html.escape
    return [f'<table class="{cls}">',
            *(f"<tr><th>{esc(label)}</th><td>{esc(value or DASH)}</td></tr>"
              for label, value in rows),
            "</table>"]


def _timing_caveat(session, caveat) -> list[str]:
    """Under a moment or a note that carries a timing state (`story` caveat), the report's own
    honesty stamp: what every lap time on the page is worth (`_timing_meta`, worded for a
    section in the middle of the page rather than its header)."""
    return [_note(f"Timing: {_timing_meta(session, where='on this page')}")] if caveat else []


def _summary_sections(session, unit, groups=None) -> list[list[str]]:
    """The Stats page's groups (`stats_summary`), one section each, in its order. PACE also
    prints the σ tile's hover (`stats.SIGMA_TOOLTIP`: what σ counts, what the middle half spans)
    when σ holds a number — a page has no hover. Not in the clipboard text: its "< 0.07 s"."""
    out = []
    for sec in stats_summary(session, unit) if groups is None else groups:
        notes = [sec.note] if sec.note else []
        if sec.title == "PACE" and dict(sec.rows).get(stats_service.SIGMA_CAPTION):
            notes.append(stats_service.SIGMA_TOOLTIP)
        out.append(_section(sec.title, _kv_table(sec.rows) + [_note(n) for n in notes]))
    return out


def _moments_section(session) -> list[str]:
    """Stats ▸ MOMENTS: the moments the page prints (`story.shown_moments`: none when the only one
    is an untied best lap, which repeats the PACE row), each in `story.moment_row`'s words — ties
    named level within the timing precision, or the estimated clock's wider bound."""
    read = getattr(session, "session_moments", None)
    sm = cast("story.SessionMoments | None", read() if callable(read) else None)
    moments = story.shown_moments(sm)
    if not moments:
        return []
    rows = [(r.label, r.value) for r in map(story.moment_row, moments)]
    caveat = tuple(dict.fromkeys(c for m in moments for c in m.caveat))
    return _section("MOMENTS", _kv_table(rows, "moments") + [_note(story.MOMENTS_NOTE)]
                    + _timing_caveat(session, caveat))


def _corner_cells(r, marked: bool, unit_id: str) -> str:
    """One CORNERS row, at the Stats table's precision; a marked corner's loss cell carries ◆.
    Seconds are bare numbers under "(s)" headers, the middle half's " s" included."""
    def speed(v):
        return _sec(None if v is None else units.convert_speed(v, unit_id), "{:.1f}")
    loss = _sec(stats_service.printed_med_loss(r), "+{:.2f}")
    if marked and loss:
        loss = f"{stats_service.CORNER_MARK} {loss}"
    cells = [f"C{r.cid}", _sec(r.best_s, "{:.2f}"), _sec(r.median_s, "{:.2f}"),
             _sec(r.sigma_s, "{:.2f}"),
             (stats_service.fmt_middle_half(r.middle_half_s,
                                            stats_service.CORNER_MIDDLE_HALF_FLOOR_S)
              or "").removesuffix(" s"),
             loss, speed(r.apex_best_kmh), speed(r.apex_median_kmh)]
    tds = [f'<td class="mark">{html.escape(c)}</td>' if k == 5 and marked
           else f"<td>{html.escape(c or DASH)}</td>" for k, c in enumerate(cells)]
    return "<tr>" + "".join(tds) + "</tr>"


def _corners_section(session, unit) -> list[str]:
    """Stats ▸ CORNERS: `Session.corner_report` rows, the ◆ on `stats.marked_corners`' corners
    (the page's rule and glyph), the middle half printed, and the page's notes in its words."""
    report = getattr(session, "corner_report", list)() or []
    if not report:
        return []
    unit_id = units.normalize_unit(unit)
    marked = {r.cid for r in stats_service.marked_corners(report, stats_service.CORNER_MARK_N)}
    head = "".join(f"<th>{html.escape(h)}</th>" for h in REPORT_CORNER_COLUMNS)
    body = ["<table>", f"<tr>{head}</tr>",
            *(_corner_cells(r, r.cid in marked, unit_id) for r in report), "</table>"]
    # The Stats CORNERS note's reconciliation, in the report's words (K6): what the COACHING
    # section totals over the same corners against your best lap, the ideal lap's gap, and why
    # three per-corner answers differ — said here, below both of the other two answers.
    notes = stats_service.corner_count_note(report, page=False)
    counted = [(r.n, r.n_laps) for r in report if getattr(r, "n_laps", None) is not None]
    read = getattr(session, "coaching_opportunities", None)
    opps = cast("coaching.Opportunities | None", read() if callable(read) else None)
    coach = coaching.corners_reconciliation(opps, sum(of - n for n, of in counted), page=False)
    no_mark = stats_service.corner_no_mark_note(report)
    gap = coaching.ideal_gap_s(session)
    notes += [x for x in (coach, no_mark, None if gap is None else coaching.ideal_gap_sentence(gap),
                          coaching.BASELINES_SENTENCE) if x]
    body.append(_note(" ".join(notes)))
    if not no_mark:
        body.append(_note(stats_service.corner_mark_rule(hover=False)))
    body += [_note(f"C{r.cid}: {stats_service.corner_one_pass_note(r)}")
             for r in report if stats_service.corner_one_pass_note(r)]
    return _section(f"CORNERS · speeds in {units.speed_label(unit_id)}", body)


def _slow_laps_section(session) -> list[str]:
    """The Laps table's slow-lap notes (`Session.slow_lap_notes`): each note's own text — where
    its time went, corner by corner against the typical pass — and the rule that called it slow.
    A ⚠ dropout lap gets none, held at the surface as the Laps table holds it."""
    read = getattr(session, "slow_lap_notes", None)
    dropouts = session.dropout_lap_ids() if hasattr(session, "dropout_lap_ids") else set()
    every = cast("list[story.SlowLapNote]", read() if callable(read) else [])
    notes = [n for n in every if n.lap_id not in dropouts]
    if not notes:
        return []
    # Stamped under every state the Laps table shows the notes under (`story.note_trust_states`):
    # a GPS-low or degraded recording too, whose notes' own caveat does not say so.
    caveat = story.note_trust_states(
        tuple(dict.fromkeys(c for n in notes for c in n.caveat)),
        verified=bool(getattr(session, "timing_verified", True)),
        degraded=bool(getattr(getattr(session, "timing_quality", None), "degraded", False)))
    return _section("SLOW LAPS", [
        '<ul class="notes">', *(f"<li>{html.escape(n.text)}</li>" for n in notes), "</ul>",
        *(_note(rule) for rule in dict.fromkeys(n.rule for n in notes)),
        *_timing_caveat(session, caveat)])


def _img(title: str, png: bytes, width: int | None, cls: str = "") -> str:
    b64 = base64.b64encode(png).decode("ascii")
    attr = f' width="{int(width)}"' if width else ""
    attr += f' class="{cls}"' if cls else ""
    return f'<img alt="{html.escape(title)}"{attr} src="data:image/png;base64,{b64}"/>'


def _lapstrip_section(lapstrip: bytes | None) -> list[str]:
    """The lap-strip card (`share_lapstrip`), as the caller rendered it — under the lap card's own
    gate, so a provisional session or one with under two clean laps has no strip here either."""
    if not lapstrip:
        return []
    return _section("LAP STRIP", [_img("Lap strip", lapstrip, LAPSTRIP_LAYOUT_PX, "card")])


#: The Coaching page's four always-shown columns (coaching_panel.OpportunitiesPanel._COLUMNS): the
#: report prints its rows under the same words. Their meanings are `coaching.COLUMN_TIPS`.
REPORT_COACHING_COLUMNS = ("Corner", "Time lost", "Done it?", "How to find it")
#: The columns whose meaning a reader needs and the table cannot carry (the other two say it).
_COACHING_COLUMN_NOTES = ("Time lost", "Done it?")


def _timing_clean(session) -> bool:
    """Verified start line and no degraded clock: the one state the report stamps nothing for."""
    return (bool(getattr(session, "timing_verified", True))
            and not bool(getattr(getattr(session, "timing_quality", None), "degraded", False)))


def _coaching_row(r: coaching.RowText) -> str:
    """One Coaching row in the page's cells; a reason's braking line on a line of its own, and an
    abstained row muted (its number is shown, not claimed: the panel's dim tone)."""
    esc = html.escape
    reason = "<br/>".join(esc(line) for line in r.reason.split("\n"))
    cls = "" if r.ranked else ' class="muted"'
    lost = "lost" if r.ranked else "lost dim"
    return (f"<tr{cls}><td>{esc(r.corner)}</td><td class=\"{lost}\">{esc(r.lost)}</td>"
            f"<td>{esc(r.done_it)}</td><td class=\"why\">{reason}</td></tr>")


def _coaching_section(session, unit, ideal_gap: str | None = None,
                      corners_note: bool = False) -> list[str]:
    """The Coaching page's answer — `coaching.page_text`, never re-derived: the headline, the theme
    and its "Start with …" action, then every shown row in the page's order (the shortlist the
    headline sums first, then the rest, ranked and not ranked) under the page's column words. The
    notes under it are the page's hovers a document has no hover for (`coaching.scope_note`,
    `medians_note`, `COLUMN_TIPS`, `BRAKE_DIRECTION_CAVEAT`), and the report's Timing stamp when the
    timing is not clean — the rows are measured against a best lap that timing qualifies.

    The page's own empty state (`coaching.empty_state_copy`) when it shows no rows, never a blank
    section; nothing for a session double with no Coaching model."""
    read = getattr(session, "coaching_opportunities", None)
    if not callable(read):
        return []
    opps = cast("coaching.Opportunities", read())
    dirs = getattr(session, "coaching_brake_direction", None)
    # The ordinary page's rows (not the debrief's): they carry the braking-direction line.
    directions = cast(dict, (dirs() if callable(dirs) else None) or {})
    text = coaching.page_text(opps, directions, units.normalize_unit(unit))
    esc = html.escape
    if not text.headline:
        title, body = coaching.empty_state_copy(opps, session)
        return _section("COACHING", [f'<p class="lead">{esc(title)}</p>',
                                     f'<p class="body">{esc(body)}</p>'])
    out = [f'<p class="lead">{esc(text.headline)}</p>']
    if text.theme:
        out.append(f'<p class="body">{esc(text.theme)}</p>')
    if text.actions:
        out += ['<ul class="actions">', *(f"<li>{esc(a)}</li>" for a in text.actions), "</ul>"]
    if text.rows:
        head = "".join(f"<th>{esc(h)}</th>" for h in REPORT_COACHING_COLUMNS)
        # The shortlist the headline sums leads the order (ranked rows first); the rows after it
        # sit in their own body, under a rule, so "your top 3" is visibly those three.
        k = len(text.shortlist)
        lead = k if [r.cid for r in text.rows[:k]] == [r.cid for r in text.shortlist] else 0
        out += ['<table class="coaching">', f"<thead><tr>{head}</tr></thead>",
                "<tbody>", *map(_coaching_row, text.rows[:lead]), "</tbody>"]
        if text.rows[lead:]:
            out += ['<tbody class="more">', *map(_coaching_row, text.rows[lead:]), "</tbody>"]
        out.append("</table>")
    n = len(text.shortlist)
    notes = [f"{coaching.scope_note(page=False, n=n)} {coaching.medians_note(bars=False)}",
             *(f"{h} — {coaching.COLUMN_TIPS[h]}" for h in _COACHING_COLUMN_NOTES)]
    braked = [r.corner for r in text.rows if r.ranked
              and coaching.brake_direction_line(directions.get(r.cid))]
    if braked:
        notes.append(f"The braking line under {' and '.join(braked)} "
                     f"{coaching.BRAKE_DIRECTION_CAVEAT}")
    out += [_note(n) for n in notes]
    out += _timing_caveat(session, not _timing_clean(session))
    # LAST: the headline's total and IDEAL LAP's gap both read "vs your best" one screen apart and
    # are not one quantity. Only when both are printed: a ranked shortlist and an IDEAL LAP section.
    if n and ideal_gap:
        total = coaching.shown_total(r for r in coaching.shortlist(opps))
        out.append(_note(coaching.totals_pointer(total, n, ideal_gap, corners_note=corners_note)))
    return _section("COACHING", out)


#: The lap card's width in the report's opening: its 1080 px canvas at a third, beside COACHING.
CARD_LAYOUT_PX = 360


def _front(session, unit, card: bytes | None, ideal_gap: str | None = None,
           corners_note: bool = False) -> list[str]:
    """The report's opening, the app's debrief in a page: the lap card the app exports (`card`,
    rendered by the caller under the card's own gate — None on provisional timing, and then there
    is no picture here, not a stand-in) beside the COACHING section, which is what a reader opens
    a session report to find: what to work on. Laid side by side so both are on the first screen;
    one column on a narrow one."""
    sec = _coaching_section(session, unit, ideal_gap, corners_note)
    if not card:
        return sec
    return ['<div class="front with-card">',
            f'<figure class="hero">{_img("Lap card", card, CARD_LAYOUT_PX, "card")}</figure>',
            *sec, "</div>"]


# The report's Laps table speaks the app's words, not laps.csv's: "Lap", "Time (s)", "Dist (m)",
# "Entry (km/h)" are the Laps tab's own headers (with the unit the page's cells are in), and a
# corner's pair sits under ONE "C1" over "time (s)" / "apex (km/h)". The machine names stay the
# CSV's (`laps_table`), and every one of them maps to exactly one column here (`report_laps_header`).
REPORT_QUALITY_HEAD = "Quality"
REPORT_INTERPOLATED_HEAD = "Interpolated"   # the app's word for a corner edge not matched on track
# INTERPOLATED_NOTE in the page's column words (the CSV trailer keeps its own, ASCII, verbatim).
REPORT_INTERPOLATED_NOTE = (
    INTERPOLATED_NOTE
    .replace(f"{INTERPOLATED_COLUMN} lists", f"{REPORT_INTERPOLATED_HEAD} lists", 1)
    .replace("*_time_s and *_apex_* cells", "time and apex cells", 1))


def report_laps_header(headers) -> list[tuple[str | None, str]]:
    """`(group, label)` for each of `laps_table`'s machine headers, in order: group None is a
    column of its own, a group ("C1", "Splits (s)") spans its run of columns over their labels.
    The speed unit is read off the header's own suffix, so it is the cells' unit by construction.
    An unknown header keeps its machine name rather than lose its column."""
    out: list[tuple[str | None, str]] = []
    for h in headers:
        stem, _, last = h.rpartition("_")
        if h == "lap":
            out.append((None, "Lap"))
        elif h == "time_s":
            out.append((None, "Time (s)"))
        elif h == DIST_COLUMN:
            out.append((None, "Dist (m)"))
        elif stem == "entry" and last in units.UNITS:
            out.append((None, f"Entry ({units.speed_label(last)})"))
        elif h == "flag":
            out.append((None, "GPS"))
        elif h == "quality":
            out.append((None, REPORT_QUALITY_HEAD))
        elif h == INTERPOLATED_COLUMN:
            out.append((None, REPORT_INTERPOLATED_HEAD))
        elif last == "s" and stem[:1] == "S" and stem[1:].isdigit():
            out.append(("Splits (s)", stem))
        elif h.endswith("_time_s"):
            out.append((h.removesuffix("_time_s"), "time (s)"))
        elif "_apex_" in h and last in units.UNITS:
            out.append((h.split("_apex_", 1)[0], f"apex ({units.speed_label(last)})"))
        else:
            out.append((None, h))
    return out


def _laps_head(headers) -> str:
    """The table's header rows: one when nothing is grouped, else a group row over a label row,
    the ungrouped columns spanning both."""
    esc = html.escape
    cols = report_laps_header(headers)
    if all(g is None for g, _ in cols):
        return "<tr>" + "".join(f"<th>{esc(label)}</th>" for _g, label in cols) + "</tr>"
    top, sub, k = [], [], 0
    while k < len(cols):
        group, label = cols[k]
        if group is None:
            top.append(f'<th rowspan="2">{esc(label)}</th>')
            k += 1
            continue
        run = k
        while run < len(cols) and cols[run][0] == group:
            sub.append(f"<th>{esc(cols[run][1])}</th>")
            run += 1
        top.append(f'<th colspan="{run - k}" class="grp">{esc(group)}</th>')
        k = run
    return f"<tr>{''.join(top)}</tr><tr>{''.join(sub)}</tr>"


def _laps_section(session, headers, rows, cue_lap) -> list[str]:
    """The Laps table (`laps_table`'s rows at the app's precision, `display=True`, under
    `report_laps_header`'s words) and the notes that decode it."""
    esc = html.escape
    out = ['<section class="wide">', f"<h2>Laps ({len(rows)})</h2>",
           '<table class="laps">' + _laps_head(headers)]
    for lap_id, cells in rows:  # the best lap reads green, like the app's table — and, like the
        cls = ' class="best"' if lap_id == cue_lap else ""  # app's table, not while provisional
        out.append(f"<tr{cls}>" + "".join(f"<td>{esc(c)}</td>" for c in cells) + "</tr>")
    out.append("</table>")
    # The distance column's note, as laps.csv's trailer carries it: the odometer reads short.
    if rows:
        out.append(_note(stats_service.ODOMETER_NOTE))
    # The quality column's KEY, under the table it decodes — the Analysis Function convention, and
    # the reason those codes are usable on a page with no hover. Suppressed entirely on a clean
    # session (see `quality_key`), so a spotless recording's report gains nothing to read past.
    key = quality_key(headers, rows)
    if key:
        out.append(f'<p class="note">Quality markers in the {REPORT_QUALITY_HEAD} column '
                   "(UK Government Analysis Function symbols): "
                   + "; ".join(f"<b>{esc(code)}</b> {esc(meaning)}" for code, meaning in key)
                   + ".</p>")
    # …and the same for the C5 disclosure column, for the same reason and on the same terms: a
    # reader of the page has no other way to know that a corner cell beside it is a guess.
    if any_interpolated(headers, rows):
        out.append(_note(REPORT_INTERPOLATED_NOTE))
    broke = data_quality.break_in_series(session)
    if broke:  # WHICH break — the [b] code alone says only that there is one
        out.append(_note(f"Break in series: {broke}."))
    return [*out, "</section>"]


def best_lap_cue(session) -> int | None:
    """The lap the report may NAME as the best one: the best lap on verified timing, None on
    provisional timing (the app's own trust gate — see write_report_html). ONE rule for every part
    of the page that names the best lap: the header, the Laps table's green row, the figures'
    captions and the legends drawn inside them (app.py's report figures read it too)."""
    if not bool(getattr(session, "timing_verified", True)):
        return None
    return session.best_lap_id()


def write_report_html(path: str, session, source_label: str = "",
                      images: Sequence[tuple[str, bytes] | tuple[str, bytes, int | None]
                                       | tuple[str, bytes, int | None, str | None]] = (),
                      unit: str | None = None, *, lapstrip: bytes | None = None,
                      card: bytes | None = None,
                      tokens: Mapping[str, str] | None = None) -> None:
    """One SELF-CONTAINED page telling the session the way the app does, in the app's order:
    the header (recording, track, date, lap count, best lap, and the `Timing` stamp), then the
    debrief's answer — the lap card beside the COACHING section (`_front`) — then the Stats
    page's groups (SESSION, PACE, MOMENTS, IDEAL LAP, SPEED · G, DRIVING, then CORNERS), the
    slow-lap notes, the lap strip, the laps table (same rows/columns as `write_laps_csv`, via
    `laps_table`) and the passed snapshots. `images` is an iterable of `(title, png_bytes)` or
    `(title, png_bytes, layout_width_px)` — app.py grabs the map/plots widgets (QWidget.grab →
    QImage → PNG bytes), so this module stays Qt-free; `lapstrip` is the `share_lapstrip` card's
    PNG bytes for the same reason (None: no strip, which is what its gate says on a provisional
    session). `card` is the lap card's PNG bytes (`share_card`, the image File ▸ Export ▸ "Lap
    card" saves), the page's opening picture, under the same rule: None on provisional timing, and
    the page then opens on COACHING with no picture. `tokens` maps REPORT_TOKENS to the app's
    colours; without them the page is paper.

    `layout_width_px` is the width the DOCUMENT lays that image out at, in CSS pixels, and it is
    what makes the page reproducible: a HiDPI grab arrives at 2x the pixels, and with no width
    the browser sizes the figure from the PNG, so the same session exported from a Retina Mac
    laid its figures out 22 % larger than from a non-Retina one (see app.py's
    _report_image_width, which computes it by dividing the grab's device pixel ratio out). None
    or absent emits no attribute — right for any caller that has no display to divide out.

    `unit` is the reader's DISPLAY speed unit, passed straight to `laps_table`. The app passes its
    live choice because the embedded images already follow it: the grabbed chart axis reads
    "speed (mph)" and the map colour bar "17 … 54 mph", so a km/h table under them put two
    different numbers for the same lap on one page (L12-01). None keeps the SI headers — the shape
    the CSV writer uses, and the right default for a caller with no display unit.

    THE SESSION'S STORY IS IN IT. N13 brought the Stats groups (the page used to be meta + laps
    table + snapshots); MOMENTS, CORNERS and the slow-lap notes followed, so the document a driver
    mails to a coach says what the app says. Every one is read from the function the app's surface
    reads, so the honesty rules travel with it — ties named level, the middle half only from five
    laps, the ◆ ranking, MOMENTS absent when it would only repeat the best lap.

    IT ALSO SAYS WHAT THE TIMING IS WORTH. The meta table carries a `Timing` row off
    `timing_verified` / `timing_quality` — the two facts that qualify every number below it — and
    MOMENTS and the slow-lap notes repeat it under themselves when they carry a timing state."""
    esc = html.escape
    # display=True: the table a person reads prints the app's precision, laps.csv keeps its own.
    headers, rows = laps_table(session, unit, display=True)
    best = session.best_lap_id()
    # THE BEST-LAP CUE FOLLOWS THE APP'S OWN TRUST GATE. On PROVISIONAL timing — an auto-fitted
    # start line nobody confirmed, the state every unrecognised circuit loads in — the app
    # withholds the "best lap" AUTHORITY everywhere it appears: `lap_table._apply_highlights`
    # drops the green row and the ★, `stats_panel._refresh_lap_table` drops the ★, and the share
    # card refuses to render at all, because a best measured from an arbitrary point names a lap
    # nothing distinguishes. The TIME still prints: the Stats page's `best lap` tile prints it
    # unmuted too, and a measurement is not what is in doubt. What goes is the cue and the lap.
    cue_lap = best_lap_cue(session)
    best_txt = DASH
    if best is not None:
        best_txt = fmt_time(session.lap_time(best))
        if cue_lap is not None:
            best_txt = f"{best_txt} (lap {lap_label(best)})"
    meta = [
        ("Recording", source_label or DASH),
        ("Track", session.track_name or "unknown"),
        ("Date", session.session_date() or DASH),
        ("Laps", str(len(rows))),
        ("Best lap", best_txt),
        ("Timing", _timing_meta(session)),
    ]
    groups = stats_summary(session, unit)
    summary = _summary_sections(session, unit, groups)
    # What the COACHING pointer quotes: IDEAL LAP's printed gap, when that section is printed, and
    # whether CORNERS prints the reconciliation it points to.
    ideal_gap = next((dict(g.rows).get(IDEAL_GAP_ROW) for g in groups if g.title == "IDEAL LAP"),
                     None)
    corners = _corners_section(session, unit)
    read = getattr(session, "coaching_opportunities", None)
    opps = cast("coaching.Opportunities | None", read() if callable(read) else None)
    corners_note = bool(corners) and bool(coaching.corners_reconciliation(opps, 0, page=False))
    # The Stats page's order: MOMENTS sits under PACE (CORNERS follows every group, below).
    after_pace = next((k + 1 for k, sec in enumerate(summary) if sec[1] == "<h2>PACE</h2>"),
                      len(summary))
    summary.insert(after_pace, _moments_section(session))
    out = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8"/>',
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>',
        f"<title>{APP_NAME} — {esc(source_label) or 'session report'}</title>",
        f"<style>{report_css(tokens)}</style></head><body><main>",
        "<header>",
        f"<h1>{APP_NAME} — session report</h1>",
        *_kv_table(meta, "meta"),
        "</header>",
        *_front(session, unit, card, ideal_gap, corners_note),
        *(line for sec in summary for line in sec),
        *corners,
        *_slow_laps_section(session),
        *_lapstrip_section(lapstrip),
        *_laps_section(session, headers, rows, cue_lap),
    ]
    for item in images:  # (title, png[, width[, caption: what the figure draws]])
        caption = [_note(item[3])] if len(item) > 3 and item[3] else []
        # No PNG: the figure could not be drawn, and its caption says so where it would have been.
        img = [_img(item[0], item[1], item[2] if len(item) > 2 else None)] if item[1] else []
        out += _section(item[0], [*img, *caption])
    out.append("</main></body></html>")
    _atomic_write(path, lambda f: f.write("\n".join(out)))
