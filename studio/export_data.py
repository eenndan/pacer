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

DISCLOSURES TRAVEL WITH THE NUMBER (§5.4). Everything written here leaves the app: there is no
tooltip, no neighbouring tile and no hover behind an exported file. So every value that is an
ORDER STATISTIC rather than a measurement carries what it was taken over, in the file — the ideal
lap's `theoretical best · N laps` and its full sample sentence, both read straight off
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
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import NamedTuple

from . import APP_NAME, data_quality, units
from . import stats as stats_service
from ._signal import DASH, fmt_hms, fmt_time, lap_label

# laps.csv `flag` column value mirroring the lap table's ⚠ low-confidence marker (a GPS
# dropout inside the lap — its time/distance are less reliable). Clean laps carry "".
DROPOUT_FLAG = "gps-dropout"

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
    that same number captions it `theoretical best · N laps`. The ideal is a minimum over the
    session's clean laps, so the count is not decoration: on the owner's D24 three-chapter
    recording the same driving reads 68.016 s over 5 laps and 66.781 s over 65
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
    """Human 3-decimal float formatting (laps.csv + the report tables)."""
    return f"{float(v):.3f}"


def _atomic_write(path: str, body: Callable[[object], None], *, newline: str | None = None) -> None:
    """Write `path` by filling a sibling `.tmp` and `os.replace`-ing it into place — the SAME
    contract prefs/library/track_db/sidecar hold, and for the same reason: a write that fails
    part-way must not be able to damage what was already there.

    `body(f)` does the actual writing into an open UTF-8 text handle; `newline` is passed to
    `open` (the csv module requires `""`, the report wants the default). `os.replace` is atomic
    within a filesystem, and the temp is created in the DESTINATION'S OWN DIRECTORY so it always is
    one — a temp in $TMPDIR would degrade to a cross-device copy and lose the guarantee.

    THE TEMP NAME IS UNIQUE (`mkstemp`), NOT `path + ".tmp"`, and unlike the four persistence
    stores this one has to be. They write inside an app-support directory the app owns, where a
    predictable `<file>.tmp` can only ever collide with itself. This writes wherever the user
    pointed a save dialog — so a fixed name would open, TRUNCATE and then delete (or worse,
    `os.replace` away) a file of the user's called `laps.csv.tmp` that happened to be sitting
    there. `mkstemp` also refuses to clobber, so two exports racing in one folder cannot interleave
    into each other's file.

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


def laps_table(session, unit: str | None = None) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """(headers, rows) of the lap table — single-sourced for BOTH `write_laps_csv` and the
    report's HTML table, so the two always agree on rows, ordering and precision. One row per lap
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
    are SI in both modes (the app shows them in the same units on screen)."""
    rows_meta = session.lap_rows()
    n_sect = session.sector_count()
    n_splits = n_sect + 1 if n_sect else 0  # N lines -> N+1 sub-sectors; 0 lines -> none
    corner_list = session.corners.corner_list()
    dropout_ids = session.dropout_lap_ids()

    sfx, speed = _speed_column(unit)

    headers = ["lap", "time_s", "dist_m", f"entry_{sfx}", "flag"]
    headers += [f"S{i + 1}_s" for i in range(n_splits)]
    for c in corner_list:
        headers += [f"{c.label}_time_s", f"{c.label}_apex_{sfx}"]
    headers.append("quality")

    rows: list[tuple[int, list[str]]] = []
    for r in rows_meta:
        lap_id = r["idx"]
        # `lap` column is the 1-based lap NUMBER (lap_label), matching the app's Lap column;
        # the 0-based lap_id stays the internal key (row tuple / best-lap green class below).
        cells = [lap_label(lap_id), _f3(r["time"]), _f3(r["dist"]), _f3(speed(r["entry"])),
                 DROPOUT_FLAG if lap_id in dropout_ids else ""]
        splits = session.lap_sector_splits(lap_id) if n_splits else []
        for i in range(n_splits):
            cells.append(_f3(splits[i]) if i < len(splits) else "")
        stats = {s.cid: s for s in session.corners.lap_corner_stats(lap_id)}
        for c in corner_list:
            s = stats.get(c.cid)
            cells += [_f3(s.time), _f3(speed(s.apex_speed))] if s is not None else ["", ""]
        # `dropout_ids` is passed in so the per-lap read does not re-fetch the set per row.
        cells.append(" ".join(data_quality.lap_marks(session, lap_id, dropout_ids)))
        rows.append((lap_id, cells))
    return headers, rows


def quality_key(headers, rows) -> list[tuple[str, str]]:
    """The KEY decoding every quality code `rows` actually carries — `(code, meaning)` pairs.

    A code with no key beside it is the failure the Analysis Function convention exists to
    prevent, so both writers render this under the table they wrote. It is derived from the ROWS
    rather than from the session, so the key can never list a mark the file does not contain (and
    can never omit one it does) — the two halves of a legend that would otherwise drift.

    The column is located BY NAME rather than as `cells[-1]`: `laps_table` appends it last today,
    and a future column appended after it would silently make this read corner apex speeds."""
    try:
        col = list(headers).index("quality")
    except ValueError:
        return []
    present = {m for _lap_id, cells in rows if len(cells) > col for m in cells[col].split()}
    return data_quality.mark_key(present)


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
    export dropped the row on exactly the sessions where the ideal is now 0.94–1.42 s faster than
    anything driven. (The Stats tile carries the twin of this gate.)"""
    degenerate = session.ideal_donor_lap_id() is not None
    sample = _ideal_sample(session)
    out: list[SummaryRow] = []
    for label, accessor in SUMMARY_ROWS:
        if accessor == "theoretical_best" and degenerate:
            continue  # one lap won every segment: the "ideal" IS that lap, so it says nothing
        v = getattr(session, accessor)()
        ideal = accessor == "theoretical_best" and sample is not None
        out.append(SummaryRow(
            label=label,
            value=_f3(v) if v is not None else "",
            over_laps=str(sample.laps) if ideal else "",
            # `sentence()`, NOT `caption() + sentence()`. The caption's separator is a MIDDLE DOT
            # and laps.csv has been pure ASCII for its whole life — a machine-contract file that
            # suddenly renders "theoretical best Â· 24 laps" in a spreadsheet guessing MacRoman is
            # a wart this row does not need. The sentence carries the same counts in ASCII prose
            # ("Stitched from 11 of your 24 clean laps…") and `over_laps` carries the number a
            # parser wants, so nothing is lost; the two HUMAN surfaces (report, clipboard) print
            # the caption verbatim.
            note=sample.sentence() if ideal else ""))
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
    carries none teaches the reader that the marks are decoration."""
    headers, rows = laps_table(session)
    summary = laps_summary(session)
    key = quality_key(headers, rows)
    broke = data_quality.break_in_series(session)

    def body(f):
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(cells for _lap_id, cells in rows)
        w.writerow([])  # blank separator: the lap rows end here, the trailer follows
        w.writerow([SUMMARY_MARKER, "time_s", "over_laps", "note"])
        for row in summary:
            w.writerow([f"{SUMMARY_MARKER}: {row.label}", row.value, row.over_laps, row.note])
        for code, meaning in key:
            w.writerow([f"{SUMMARY_MARKER}: quality {code}", "", "", meaning])
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
    ("theoretical best · 24 laps") — because an exported line has no tile beneath it to put a
    caption on. An EMPTY value is the em-dash case: the accessor returned None, and the renderers
    print `DASH`, never a 0 (the None-not-zero rule the whole Stats layer is built on).

    `note` is the group's disclosure paragraph, printed under its table. Only the IDEAL LAP group
    has one today, and it is `IdealSample.sentence()` verbatim."""

    title: str
    rows: list[tuple[str, str]]
    note: str = field(default="")


def _sec(v, fmt: str = "{:.2f} s") -> str:
    """A float value as a display string, or "" when the accessor said None. The one place this
    module turns "no signal" into "no value" — never into a zero."""
    return "" if v is None else fmt.format(v)


def _distance_note(tot) -> str:
    """The SESSION group's disclosure about its own `distance` row, or "" when there is nothing to
    disclose — `stats_panel._set_distance`'s tooltip rule, in prose the export can print.

    The path length is SPEED-GATED in the data layer (a GPS fix that teleports is not distance
    driven), and the page says so in two states: below `stats.MIN_KEPT_FRAC` the value is withheld
    entirely, and from a whole percent of rejected steps up the number stands with the caveat
    attached. Both are disclosures the page makes on hover — so on a surface with no hover they
    have to be printed, which is the same argument §5.4 makes about the ideal's lap count."""
    kept = getattr(tot, "distance_kept_frac", 1.0)
    if tot.distance_m is None:
        return (f"Distance not shown: only {kept * 100:.0f}% of this trace's GPS steps are "
                "physically possible at the speed the same trace reports — the rest are dropped "
                "fixes, so a path length would be a fiction. The recorded time and the lap "
                "statistics are unaffected.")
    # A handful of rejected steps is not worth a caveat that would round to "0%" (a real 26-minute
    # recording rejects 0.02%) — the page uses the same 1% floor.
    if kept >= 0.99:
        return ""
    return (f"Distance: {(1 - kept) * 100:.0f}% of the raw GPS steps were rejected as impossible "
            "at the speed the same trace reports (dropped fixes) and are not counted.")


def _timing_meta(session) -> str:
    """What every lap time on an exported surface is worth — the report's `Timing` meta row AND the
    clipboard summary's `Timing:` header line, one string so the two cannot disagree.

    Two facts, the same two the app gates on and in the same order it does:

      * `timing_verified` False — the start/finish line was auto-fitted and never confirmed, so
        every lap time and split is measured from an arbitrary point. This is what greys the share
        card out entirely and what puts the amber banner on the map and the Stats page; a document
        that leaves the app stating those times with no such qualifier is the same defect one
        surface further out.
      * `timing_quality.degraded` — the media-clock fallback and/or a concerning share of rejected
        GPS fixes. `concerns()` is the shipped sentence list the in-app data-quality banner stacks,
        joined here rather than re-worded.

    Neither wrong ⇒ "verified start line · GPS9 true clock", the plain good case. getattr-guarded
    throughout: a Session double without these is reported as the good case, never as a crash in
    the middle of writing a report."""
    bits = []
    if not getattr(session, "timing_verified", True):
        bits.append("PROVISIONAL — the start/finish line was auto-fitted and not confirmed, so "
                    "every lap time and split below is measured from an arbitrary point")
    quality = getattr(session, "timing_quality", None)
    concerns = quality.concerns() if quality is not None else []
    if concerns:
        bits.append("ESTIMATED — " + " ".join(concerns))
    if not bits:
        bits.append("verified start line · GPS9 true clock")
    return " · ".join(bits)


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
    `ideal_donor_lap_id()` gate `_refresh_ideal` and `laps_summary` take. Within a shown group an
    individual absent value still reads as an em-dash, because there the dash IS the information.

    `unit` is the reader's display speed unit, matching `laps_table`'s parameter: the report's
    embedded charts already follow it, so its speed tiles must too. None keeps km/h.

    DELIBERATELY NOT MIRRORED, both because a copy is the drift this function exists to prevent:

      * the "median lap · top N fixed" coaching digest — its arithmetic (`_shown_rows`,
        `PANEL_TOP_N`, the sum of the ROUNDED cells) lives in `coaching_panel`, a Qt module this
        one cannot import. Reproducing it here would be the second implementation whose penny of
        rounding disagrees with the page, which is the exact defect `_set_digest` records.
      * the DATA TRUST card's provenance rows — built inline in `stats_panel._refresh_trust`, so
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
        lap_bits.append(f"{len(excluded)} excluded (distance off the session median)")
    if dropouts:
        lap_bits.append(f"{len(dropouts)} with a GPS dropout")
    rows = [("laps", " · ".join(lap_bits) if valid else "")]
    tot = st.totals() if st is not None else None
    note = ""
    if tot is not None and tot.duration_s > 0:
        rows += [("recorded", fmt_hms(tot.duration_s)), ("moving", fmt_hms(tot.moving_s)),
                 # None below stats.MIN_KEPT_FRAC: the trace was too broken for a path length to
                 # mean anything, and the page dashes it rather than printing a fiction.
                 ("distance", "" if tot.distance_m is None
                  else f"{tot.distance_m / 1000.0:.1f} km"),
                 ("on track", f"{tot.start_clock}–{tot.end_clock}"
                  if tot.start_clock and tot.end_clock else "")]
        note = _distance_note(tot)
    else:
        rows += [("recorded", ""), ("moving", ""), ("distance", ""), ("on track", "")]
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
                     ("race pace · best 3-lap run", ""), ("σ lap", ""), ("median − best", ""),
                     ("consistency · σ/median", ""), ("within 1% of best", ""), ("trend", "")]
        if pace is not None:
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
                ("race pace · best 3-lap run", "" if rp is None else fmt_time(rp)),
                ("σ lap", _sec(pace.sigma)),
                ("median − best", _sec(pace.spread, "+{:.2f} s")),
                ("consistency · σ/median", _sec(cov, "{:.1f} %")),
                ("within 1% of best", "" if count is None else f"{count} / {n_within}"),
                ("trend" if verdict is None else f"trend · {verdict}",
                 stats_service.fmt_trend(trend) or ""),
            ]
        # `best rolling` sits AFTER the pace rows and OUTSIDE the branch above, where the page puts
        # it: a start-anywhere loop off `Session` is not a pace statistic and survives a session
        # that has no distribution at all.
        pace_rows.insert(3, ("best rolling", "" if rolling is None else fmt_time(rolling)))
        out.append(SummarySection("PACE", pace_rows))

    # --- IDEAL LAP. Same three gates as the page (`_refresh_ideal`): a composite exists, more than
    # one lap donated, and there is a best lap to measure the gap against.
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
            ("on the table · vs your best", f"{-gap:+.2f} s"),
        ], note=smp.sentence()))

    # --- SPEED · G: session peaks over the per-lap stats, the page's own reductions.
    lap_rows = st.lap_stats() if st is not None else []
    if lap_rows:
        vmax = st.session_vmax()
        vmins = [r.vmin_kmh for r in lap_rows if r.vmin_kmh is not None]
        lat_peaks = [r.peak_lat_g for r in lap_rows if r.peak_lat_g is not None]
        brk_peaks = [r.peak_brake_g for r in lap_rows if r.peak_brake_g is not None]
        out.append(SummarySection("SPEED · G", [
            # The lap number is 1-BASED on every user-facing surface, this one included.
            ("top speed" if vmax is None else f"top speed · lap {lap_label(vmax[1])}",
             "" if vmax is None else
             f"{units.convert_speed(vmax[0], unit_id):.1f} {u_label}"),
            ("slowest point", f"{units.convert_speed(min(vmins), unit_id):.1f} {u_label}"
             if vmins else ""),
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
            # as its note, the way IDEAL LAP carries its sample sentence. getattr-guarded: a
            # Session double without the driving service still exports the numbers, noteless.
            drv = getattr(session, "driving", None)
            note = (getattr(drv, "coast_instrument", lambda: None)() or "") if drv else ""
            out.append(SummarySection("DRIVING", [
                ("braking / lap · median",
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
    median, a race pace and `theoretical best · 38 laps` with no qualifier anywhere in it. Same
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
/* The stats groups: label left, value right (they are numbers), one table per group. */
table.kv th { text-align: left; font-weight: 600; }
p.note { margin: 0.25em 0 1em; max-width: 46em; color: #444; font-size: 0.9em; }
/* height:auto keeps the aspect ratio when the max-width clamp (or a stated width, see
   write_report_html) shrinks a figure below its natural pixel size. */
img { max-width: 100%; height: auto; border: 1px solid #ccc; margin: 0.5em 0; }
"""


def write_report_html(path: str, session, source_label: str = "",
                      images: list[tuple[str, bytes] | tuple[str, bytes, int | None]] = (),
                      unit: str | None = None) -> None:
    """One SELF-CONTAINED page: the session header (recording, track, date, lap count,
    best lap), the laps table (same rows/columns as `write_laps_csv`, via `laps_table`),
    and the passed PNG snapshots embedded as base64 data URIs. `images` is an iterable of
    `(title, png_bytes)` or `(title, png_bytes, layout_width_px)` — app.py grabs the map/plots
    widgets (QWidget.grab → QImage → PNG bytes), so this module stays Qt-free. Deliberately
    dead-simple, WELL-FORMED (XML-parseable) markup with a little inline CSS and NO JavaScript.

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

    THE STATS GROUPS ARE IN IT (N13). The page used to be meta + laps table + snapshots, so the
    document a driver mails to a coach carried 40 lap rows and not one of the session numbers the
    app leads with — no pace distribution, no ideal lap, no peaks. `stats_summary` supplies them,
    reading the SAME accessors the Stats page reads, and the IDEAL LAP group brings its
    `theoretical best · N laps` caption and its sample sentence with it (§5.4).

    IT ALSO SAYS WHAT THE TIMING IS WORTH. The meta table gains a `Timing` row off
    `timing_verified` / `timing_quality` — the two facts that qualify every number below it. The
    app mutes and annotates its target tiles on exactly these two flags and greys the share card
    out on the first; the exported page used to state the same lap times with no such qualifier
    anywhere on it."""
    esc = html.escape
    headers, rows = laps_table(session, unit)
    best = session.best_lap_id()
    best_txt = DASH
    if best is not None:
        best_txt = f"{fmt_time(session.lap_time(best))} (lap {lap_label(best)})"
    meta = [
        ("Recording", source_label or DASH),
        ("Track", session.track_name or "unknown"),
        ("Date", session.session_date() or DASH),
        ("Laps", str(len(rows))),
        ("Best lap", best_txt),
        ("Timing", _timing_meta(session)),
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
    ]
    for sec in stats_summary(session, unit):
        out.append(f"<h2>{esc(sec.title)}</h2>")
        out.append('<table class="kv">')
        # An absent value prints the em-dash the app prints, never a 0 and never a blank cell that
        # reads as "we forgot" (None-not-zero, all the way out to the exported document).
        out += [f"<tr><th>{esc(label)}</th><td>{esc(value or DASH)}</td></tr>"
                for label, value in sec.rows]
        out.append("</table>")
        if sec.note:
            out.append(f'<p class="note">{esc(sec.note)}</p>')
    out += [
        f"<h2>Laps ({len(rows)})</h2>",
        "<table><tr>" + "".join(f"<th>{esc(h)}</th>" for h in headers) + "</tr>",
    ]
    for lap_id, cells in rows:  # the best lap reads green, like the app's table
        cls = ' class="best"' if lap_id == best else ""
        out.append(f"<tr{cls}>" + "".join(f"<td>{esc(c)}</td>" for c in cells) + "</tr>")
    out.append("</table>")
    # The quality column's KEY, under the table it decodes — the Analysis Function convention, and
    # the reason those codes are usable on a page with no hover. Suppressed entirely on a clean
    # session (see `quality_key`), so a spotless recording's report gains nothing to read past.
    key = quality_key(headers, rows)
    if key:
        out.append('<p class="note">Quality markers in the <code>quality</code> column '
                   "(UK Government Analysis Function symbols): "
                   + "; ".join(f"<b>{esc(code)}</b> {esc(meaning)}" for code, meaning in key)
                   + ".</p>")
    broke = data_quality.break_in_series(session)
    if broke:  # WHICH break — the [b] code alone says only that there is one
        out.append(f'<p class="note">Break in series: {esc(broke)}.</p>')
    for item in images:
        title, png = item[0], item[1]
        width = item[2] if len(item) > 2 else None
        b64 = base64.b64encode(png).decode("ascii")
        attr = f' width="{int(width)}"' if width else ""
        out.append(f"<h2>{esc(title)}</h2>")
        out.append(f'<img alt="{esc(title)}"{attr} src="data:image/png;base64,{b64}"/>')
    out.append("</body></html>")
    _atomic_write(path, lambda f: f.write("\n".join(out)))
