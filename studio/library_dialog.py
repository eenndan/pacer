"""The session-library dialog (F8): browse analyzed recordings + per-track PB progression.

A self-contained QDialog over a ``studio.library`` index dict (already loaded by the caller —
the dialog does no file I/O of its own, so it shows an EMPTY library cleanly when the index is
missing/corrupt). It is PACER-FREE: it consumes only the plain entry dicts + the pure
``library.pb_series`` helper. Re-opening a recording is delegated to an injected
``open_recording(paths)`` callback (the app passes ``StudioWindow._load``), so this module never
imports the app.

Layout::

    ┌───────────────────────────────────────────────┐
    │  N analyzed recordings  (M of N when filtered) │  ← header count, of what is ON SCREEN
    │  [search…]        [track ▾]  [conditions ▾]    │  ← live filter row (track/date/file/record
    ├───────────────────────────────────────────────┤     substring + a per-track combo with an
    │ Date│Track│Laps│Best│Ideal│Conditions│Tyres    │     Unknown-track bucket + a conditions combo
    │  …      …    …    …    …      …         …       │     with a No-record bucket)
    │  “No recordings match …” when the filter empties│  ← sortable table (one row / recording);
    ├───────────────────────────────────────────────┤     missing-file rows greyed + disabled; an
    │  <selected track> · 12 sessions · best … · …    │     UNTRUSTWORTHY row carries a trust tag
    │  Dry · air 24° · MG Yellow #3, 42 laps · 11/82  │  ← the selected row's SESSION RECORD…
    │  Not like-for-like vs your best here: Dry vs Wet│  ← …and whether it and the row holding the
    │  PB progression — <track>   [best-vs-date plot] │     track's best lap were the same kind of day
    ├───────────────────────────────────────────────┤
    │                [Session record…] [Open] [Close] │
    └───────────────────────────────────────────────┘

SIZE: the TABLE is the reason this dialog exists, so it takes the pixels — the PB chart is held to a
150–200 px band (it yields first when space is tight and stops growing once it has enough), and the
dialog opens tall enough to browse a real library, clamped to the screen and replaced by the user's
own size once they resize it (persisted through ``studio.prefs``). A remembered size is stored as
given but FLOORED on the way back in (``_MIN_BROWSABLE_H``), so one drag to the layout minimum can't
leave every future open showing 0.97 of one row; the privacy paragraph is a ``WrapLabel`` so the
layout's own minimum accounts for the height its text really wraps to.

Date/Laps/Best/Ideal sort numerically via ``_NumItem``; Track sorts as text. The Open button +
a double-click re-open the selected row's recording (disabled for a missing/junk row). Every time
this dialog prints a lap time — the Best/Ideal cells, the summary line, the chart's left axis
(``_LapTimeAxis``) — it goes through ``_signal.fmt_time``, so one frame never carries two formats.
The Ideal-lap column shows an em dash, and says why on hover, in the two states where it would
otherwise reprint the Best-lap cell — see ``_ideal_cell``.

The LAPS column is not decoration: ``Best lap`` and ``Ideal lap`` are both minima over the
session's laps, so both fall as a session gets longer and a ranking of either is partly a ranking
of session length. Showing the sample for the whole ROW is this dialog's answer — see ``_COL_LAPS``
for why that beats refusing to sort the ideal, and the three header tooltips for the measured
rates.

TRUST (library schema v2): the table SHOWS every session, but an untrustworthy one (provisional
start line / estimated timing / GPS dropout — see ``library.trust_label``) gets a muted tag and is
EXCLUDED from the PB chart + progress summary, which read only ``library.pb_series`` /
``library.track_summary`` (the trustworthy subset). The dialog stays pacer-free — it consumes the
plain flags on the entry dicts and those pure helpers.

COMPARABILITY (``studio/session_record.py``): the table has always ranked best laps across a
season, and a ranking of best laps is only a ranking of DRIVING if the sessions were alike. So a
second data source is joined in on the row's fingerprint — the session record, the driver's own
note of the conditions, the tyres and the setup — and it appears in three places: two sortable
columns (``Conditions``, ``Tyres``, the two facts that move a lap time most and take seconds to
record), a conditions filter beside the track one, and the two lines under the table that read the
selected row's whole record and say whether it and the track's best-lap session were even the same
kind of day. Records are DATA here like the index is: the store is handed in, the editor is an
injected callback, and this dialog still writes nothing.
"""

from __future__ import annotations

import datetime
import os
from collections.abc import Callable

import pyqtgraph as pg
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QBrush, QColor, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from . import APP_NAME, prefs, theme
from . import library as _library
from . import session_record as _records
from ._signal import fmt_time
from ._signal import plural as _plural_shared
from .theme import C
from .widgets import NUM_ROLE, EmptyState, WrapLabel
from .widgets import NumItem as _NumItem

# Column layout — index → header. Date/Best/Ideal sort numerically (a key in NUM_ROLE);
# Track sorts as text.
#
# The fourth column was headed "Theoretical" and, on every recording anyone had, printed a value
# byte-identical to the "Best lap" cell beside it: the stored `theoretical` was the sum of the
# session-best sector splits, sector lines default to none, and a lap with no sector line is one
# sub-sector whose split is its lap time. It now carries `Session.theoretical_best` — the ideal lap,
# the quickest time through each corner and each straight stitched together — so it is named for
# what it holds and for what the rest of the app calls that number (the hero's "Δideal", the chart's
# "Ideal lap" toggle). Two states still refuse to print a number rather than print a duplicate; see
# `_ideal_cell_value`.
#
# THE FIFTH COLUMN IS THE SAMPLE, and it is here because the two time columns beside it are both
# MINIMA over the session's laps. A minimum over more laps is never larger, so ranking either one
# across sessions ranks session length as well as pace — and this index holds the demonstration:
# Sandown chapter 1 (23 laps) stores an ideal of 47.933 and Sandown chapters 1–3 (59 laps) stores
# 47.374. 0.56 s apart, same driver, same day, same track, one recording a subset of the other;
# the entire difference is how many laps were loaded.
#
# WHY A COLUMN AND NOT A SORT REFUSAL ON `Ideal lap`. Suppressing that one column's sort was the
# obvious fix and it is the wrong one, measured: over random subsets of the clean laps the BEST
# LAP falls 0.033–0.221 s per doubling of lap count against the ideal's 0.068–0.384 s, and on two
# of the five recordings (D24 1 chapter, SD_30_08) the best lap is the MORE sample-dependent of
# the two. A dialog that refused to rank the ideal while happily ranking the best lap beside it
# would be advertising a distinction the numbers do not support. The confound belongs to the ROW,
# so the disclosure is a per-row count that both columns can be read against — and it is sortable
# itself, which is what makes "these two rows are 0.56 s apart because one is 36 laps longer"
# something a user can check in one click rather than a claim in a tooltip.
#
# It costs no schema change: `lap_count` has been stored on every entry since v1 (it is what
# `_entry_junk` reads to quarantine a no-laps row) and was simply never shown.
# THE SIXTH AND SEVENTH COLUMNS ARE WHETHER THE FIVE BESIDE THEM ARE COMPARABLE AT ALL, and they
# are here because this table's whole purpose is cross-session comparison. Ranking best laps
# across a season silently assumes like-for-like, and a kart coach's answer to a driver who could
# not see 18 months of improvement is that it never is: "I can go to my local track tonight and
# again a month from now and find a difference anything up to 4 seconds purely because of
# different temperature and humidity conditions." Conditions and tyre age are the two the driver
# can record in seconds and the two that move a lap time most, so they get the columns; everything
# else a session record holds (pressures, gearing, chassis, notes) is one selection away on the
# line under the table. Both read `studio/session_record.py`, keyed by the row's own fingerprint.
#
# They sit AFTER the two time columns rather than before them: the times are what the user came
# for, and these qualify them. Tyres sorts NUMERICALLY on the laps-on-the-set count — the age, not
# the name — because "sort by tyre age" is the gesture that answers "am I comparing a fresh set
# against a worn one?" in one click. A session with no record shows an em dash in both, never a
# fabricated value.
_COL_DATE, _COL_TRACK, _COL_LAPS, _COL_BEST, _COL_THEO, _COL_COND, _COL_TYRES = range(7)
_HEADERS = ["Date", "Track", "Laps", "Best lap", "Ideal lap", "Conditions", "Tyres"]

# The two time columns' header hovers — the MECHANISM behind the Laps column beside them. On the
# headers rather than the cells because it is a property of the column, and because the cells'
# hover is already spoken for (every cell names the recording's file, which is the only thing
# telling two same-day sessions apart).
_LAPS_HEADER_TIP = (
    "Valid laps in the recording — the sample the two time columns are a minimum over.\n"
    "Both of them fall as a session gets longer, so sort by this before reading a ranking of "
    "either as a ranking of pace.")
_BEST_HEADER_TIP = (
    "Best lap — the fastest single lap of the recording.\n"
    "A minimum over the session's laps, so it falls as the session gets longer: measured over "
    "random subsets of the owner's recordings, 0.03–0.22 s per doubling of lap count. The Laps "
    "column is the sample it was taken over.")
_THEO_HEADER_TIP = (
    "Ideal lap — the quickest time through each corner and each straight, stitched into one lap.\n"
    "A sum of per-segment minima, so it falls faster than the best lap does as a session gets "
    "longer: 0.07–0.38 s per doubling of lap count on the owner's recordings, with no plateau. "
    "Two rows are comparable on this number only if their Laps are comparable — Sandown chapter 1 "
    "(23 laps) and Sandown chapters 1–3 (59 laps) are 0.56 s apart on the same driving.")
_COND_HEADER_TIP = (
    "Conditions — what you recorded about the day, in File ▸ Session record….\n"
    "pacer never looks the weather up: nothing leaves this Mac. A dry-day best and a wet-day best "
    "are not the same measurement, so filter to one tag before reading the times beside it as a "
    "ranking of pace.")
_TYRES_HEADER_TIP = (
    "Tyre age — laps on the set when the session started, from its session record.\n"
    "Sorts by that count, not by the set's name. A worn set and a fresh one are worth seconds a "
    "lap, so two rows are comparable on Best lap only if they are comparable here.")

# What the Ideal-lap cell hovers with when it has no number to show, appended to the row's own file
# identity. Two causes, one em dash, and the user is told which:
#
# ...EXCEPT that the FIRST of them is not one cause but two, and the entry carries no field that
# separates them. This tip used to assert the v2->v3 migration ("It was analyzed before pacer
# built the ideal lap … Open this recording again to fill it in"), keyed on `theoretical is None`.
# But `Session.library_entry` writes None whenever `theoretical_best()` is None, which includes a
# session written by TODAY's app that has no corner partition — proven at the code path: a fresh
# v3 entry with no corner detected reads back identically to a migrated v2 one. That row was being
# told a story about its own history that may be false, and given an instruction (re-open it) that
# cannot change the value. The stored keys are `best date degraded dropout fingerprint lap_count
# paths stem theoretical track verified` — nothing distinguishes "retired by the migration" from
# "never had one" — so the tip states the FACT and names the two possible causes without asserting
# either, and the instruction is conditional on the cause that re-opening can actually fix.
# Telling them apart needs a stored flag on the migrated entries (studio/library.py `_migrate` /
# `_norm_entry`); see this PR's hand-off note.
_IDEAL_STALE_TIP = (
    "Ideal lap: not stored for this recording.\nEither it was analyzed before pacer built the "
    "ideal lap from your corners and straights (the old value was a copy of the best lap, so the "
    "migration retired it), or pacer found no corners here to stitch one from. Re-opening the "
    "recording fills it in if there are corners to find.")
_IDEAL_ONE_DONOR_TIP = (
    "Ideal lap: same as the best lap for this recording.\nOne lap was quickest through every "
    "corner and every straight, so the ideal IS that lap — there is nothing stitched to show.")
# "Same as the best lap" means same to the LAST DIGIT THIS TABLE PRINTS: `fmt_time` renders
# `m:ss.mmm`, so half a displayed millisecond is the width of a tie. The same shape as the Δ
# surfaces' `theme.DELTA_EVEN_EPS_S` (half a displayed centisecond), for the same reason — a
# threshold that says "these would render the same string" needs no other justification, and one
# picked in float-noise units (1e-9) would still let this column print two identical cells.
_IDEAL_SAME_S = 0.0005

# NUM_ROLE is studio.widgets' (it owns the shared numeric-sort cell `_NumItem` reads); the two
# files each declared their own `Qt.UserRole` literal for the same job. `_NumItem` keeps its local
# name — this dialog never sets the sort DIRECTION flag, so it wants the base class's "blanks last
# ascending, first descending", which is exactly what its old None-as-+inf trick produced.
PATHS_ROLE = Qt.UserRole + 1    # the entry's file path list (on the Date cell)
TRACK_ROLE = Qt.UserRole + 2    # the entry's track name, raw (on the Date cell)
MISSING_ROLE = Qt.UserRole + 3  # True if the recording's file(s) are missing (on the Date cell)
FP_ROLE = Qt.UserRole + 4       # the entry's fingerprint key (on the Date cell), for forget/remove
FILTER_ROLE = Qt.UserRole + 5   # lower-cased "track date" haystack for the search box (on Date)
COND_ROLE = Qt.UserRole + 6     # the row's session-record conditions tag, "" if untagged (on Date)
HAS_RECORD_ROLE = Qt.UserRole + 7  # True when the row HAS a session record at all (on Date)

# The track-filter combo's two sentinels (a real track name never equals either): "all tracks" at
# index 0, and an UNKNOWN-TRACK bucket appended when some recording's circuit isn't in the track
# registry. The registry ships with about one circuit, so those rows are the common case — without
# the bucket the combo simply cannot reach them (2 of 3 on the QA index).
_ALL_TRACKS = "All tracks"
_UNKNOWN_TRACK = "Unknown track"

# The conditions filter's two sentinels, beside the four real tags. Unlike the track combo this
# vocabulary is CLOSED (session_record.CONDITIONS), so it is built once and never rebuilt — but
# "No record" is not a fifth condition, it is the absence of one, and it needs to be reachable:
# on a library that has just gained this feature it is every row, and it is the exact filter for
# "which of my sessions have I not written up yet?".
_ALL_CONDITIONS = "All conditions"
_NO_RECORD = "No record"

# The mark over the FIRST-RUN library state — a Phosphor name for theme.icon(), never a literal
# Unicode character (tests/test_glyph_vocabulary.py). It is the one icon in the app's empty states:
# this is the only one of them that is about the app rather than about a recording.
EMPTY_LIBRARY_ICON = "ph.folder-open"

# What a Track cell reads when the registry doesn't know the circuit — shown in the cell, matched by
# the search box, and the label the unknown-track filter bucket stands for.
_UNKNOWN_LABEL = "unknown track"

# Privacy disclosure — a calm, factual note of what pacer stores locally and where. Surfaced in the
# Library dialog (this is where a user browsing their recorded history would look) and by
# Help ▸ Your data & privacy. Everything is on-disk and offline; nothing is uploaded — say so.
PRIVACY_NOTE = (
    "Everything pacer analyzes stays on this Mac — nothing is uploaded or shared, and the "
    "conditions in a session record are typed by you, never looked up online. "
    "It stores your start/finish + sector lines in a small \"<name>.pacer.json\" file next to "
    "each video, and under ~/Library/Application Support/pacer it keeps this library index (file "
    # THE THIRD STORE COSTS A CLAUSE, NOT A LINE. This note is a WrapLabel inside the dialog, so
    # every extra wrapped line comes off the ROW BUDGET of the list below it: naming marks the long
    # way took the re-opened dialog from 5.0 to 4.82 visible rows and failed
    # tests/test_library.py::test_dialog_never_reopens_too_small_to_show_the_list. The two clauses
    # that used to enumerate the stores by name ("that takes its session record with it", "the
    # whole index and every session record with it") pay for it.
    "paths, track names and GPS dates), your session records (session_records.json — the setup "
    "and conditions you write up), your marks (marks.json — your own notes on a recording) and "
    "your saved tracks (tracks.json — each circuit's name and coordinates). Right-click a "
    "recording to forget it — its record and marks go too — or use \"Clear library\" to wipe all "
    "three. A copy of each is kept beside it (library.json.bak, session_records.json.bak, "
    "marks.json.bak), so \"Restore…\" puts them back. Your saved tracks are separate: "
    "\"Clear library\" leaves tracks.json untouched, and \"Back up…\" does not copy it."
)

# A PlotDataItem pen/brush for the PB line + its markers (amber accent, the app's primary).
# The pens are ACCESSORS, not constants: a pyqtgraph pen width is in DEVICE pixels
# (theme.line_width), so a module-level `mkPen(..., width=2)` freezes whatever device-pixel ratio
# was current at import — which on a Retina panel draws the PB line at half the weight the charts
# and the map draw theirs. The brush has no width and stays a constant.
_PB_BRUSH = pg.mkBrush(C.accent)


def _pb_pen():
    """The PB progression line — the same 2 logical px the chart traces use."""
    return pg.mkPen(C.accent, width=theme.line_width(2))


def _pb_symbol_pen():
    """The marker outline: surface-coloured, so overlapping session dots stay countable."""
    return pg.mkPen(C.surface, width=theme.line_width(1))


def _pb_axis_pen():
    """The axis line — and the grid, since pyqtgraph's AxisItem falls back from tickPen() to pen().
    A bare colour here would let pyqtgraph build its own one-DEVICE-pixel pen (W5-01)."""
    return pg.mkPen(C.border, width=theme.line_width(1))

# The progress-summary trend word per library.track_summary["trend"]. "single"/"none" add nothing
# (there's no trend to read from one/zero sessions) so they map to no word.
_TREND_WORD = {"improving": "improving", "stalled": "off your PB"}


# The size the dialog opens at when the user has never resized it. The old 720x600 left the table a
# 139 px viewport — 4.6 rows of a 201-recording library, 2.3% of it — with the PB chart on its 150 px
# floor and a 4-line privacy paragraph, a filter row and a button row taking the rest: at 600 px
# everything is on a minimum and the layout's stretch factors never get to apply at all. 880x860
# gives the table 349 px (11.6 rows, 2.5x) with the chart at the top of its band, and it is the
# tallest round number that still opens UNCLAMPED on the smallest Mac this app targets (a 13" Air
# has ~931 px of available height; _SCREEN_MARGIN leaves 871). Anything smaller than that — an old
# 1280x800 panel, a half-height external display — is handled by _fit_to_screen rather than by
# opening a dialog taller than the screen.
_DEFAULT_SIZE = (880, 860)
# The PB chart's ceiling (its floor is setMinimumHeight(150) at the widget). It reads a handful of
# best-vs-date points and one empty-state sentence, so it has no use for more; without a ceiling it
# grew with every pixel the dialog gained, at the list's expense.
_PB_PLOT_MAX_H = 200
# The FLOOR under the height the dialog OPENS at. A remembered size is stored verbatim, and Qt lets
# the user drag the dialog all the way to the layout's own minimum — where the table's viewport is
# 29 px, 0.97 of ONE row of a 201-recording library, and (since the size is remembered) every future
# open comes back that way. 710 px is the measured height at which the table shows 5 rows at the
# dialog's NARROWEST width, 581 px, where the privacy note wraps tallest and so leaves the list
# least; a wider dialog gets more (6.4 rows at 719, the width the shipping dialog's button row now
# imposes). It was 680 until the session-record lines landed: the two lines under the table
# (the selected row's record + its like-for-like verdict) cost the list 1.4 rows at that width —
# 5.0 became 4.25, measured — so the floor moved with them rather than the guarantee quietly
# lapsing. That is the rule this constant is for: anything added between the table and the buttons
# re-measures this number in the same PR. It is deliberately far below the 860 px default
# — a user is allowed to want a small window — and exists only to rule out the sizes at which a list
# dialog stops showing a list. 5 rows is the bound this dialog already argued for when it rejected a
# 4.6-row default as too little: the library should never OPEN showing less list than the size that
# was called broken. The screen still overrules it (_fit_to_screen runs after), and it is applied to
# the size being OPENED, never to the size being stored — see _apply_geometry.
_MIN_BROWSABLE_H = 710
# The width _MIN_BROWSABLE_H was measured at — and therefore the premise the height floor RESTS on:
# height alone cannot buy rows at a width where the privacy note (a WrapLabel, so its wrapped height
# is part of the layout minimum) and the PB plot's 150 px floor eat everything the floor adds.
# 581 px is not a constraint this dialog applies; it is the layout minimum the BUTTON ROW happens to
# impose once the app wires all six file-op callbacks (see ``_wired_dialog`` in tests/test_library.py
# — an unwired two-button dialog's minimum is 234 px, and at 234x680 the table shows 1.23 rows, which
# is what QA W11-02 measured). So the number is named here and the test asserts the button row still
# grants it, rather than the comment asserting it and nothing checking. Shrink the button row below
# this and the guard fails instead of the library quietly re-opening 1 row tall.
_BROWSABLE_H_MEASURED_AT_W = 581
# Left over after clamping to the screen: room for the menu bar, the Dock and the window frame.
_SCREEN_MARGIN = 60
# Floors the clamp will not go below, so a screen that reports something tiny/bogus can never
# collapse the dialog (Qt then honours the layout's own minimum anyway).
_MIN_SIZE = (480, 420)


def _fit_to_screen(width: int, height: int, avail_w: int, avail_h: int) -> tuple[int, int]:
    """Clamp a desired dialog size to what a screen `avail_w` x `avail_h` can actually show. Pure
    (the caller supplies the screen's available geometry) so it is testable without a display, and
    applied to BOTH the default and a restored size — a size remembered on an external monitor must
    not open off-screen on the laptop panel. A non-positive available dimension (no screen) leaves
    that axis alone."""
    if avail_w > 0:
        width = min(width, max(_MIN_SIZE[0], avail_w - _SCREEN_MARGIN))
    if avail_h > 0:
        height = min(height, max(_MIN_SIZE[1], avail_h - _SCREEN_MARGIN))
    return int(width), int(height)


def _plural(n: int, noun: str) -> str:
    """"1 session" / "3 sessions" — the summary line's one pluralization helper.

    Delegates to `_signal.plural`, the shared definition (the ideal-lap disclosure is now built by
    a Qt view AND by the Qt-free export writer, so the rule had to move somewhere both can reach).
    Kept as a module-local name because this file calls it ten times."""
    return _plural_shared(n, noun)


def _backup_when(mtime: float | None) -> str:
    """" taken 2026-09-03 00:12" for a ``library.backup_summary`` mtime, or "" when it has none —
    the clause that dates the backup in the Restore confirm. Formatting lives here, not in the
    pacer-free/display-agnostic library module, which hands back a raw POSIX timestamp."""
    if not mtime:
        return ""
    try:
        return " taken " + datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, OverflowError):
        return ""


class _LapTimeAxis(pg.AxisItem):
    """The PB chart's left axis, rendering its seconds values as LAP TIMES through the app's one
    time formatter. A bare numeric axis printed "69" / "70.5" while the Best lap column and the
    progress summary in the SAME frame read "1:09.905" — two formats for one quantity."""

    def tickStrings(self, values, scale, spacing):  # noqa: N802 (pyqtgraph hook)
        return [fmt_time(v) for v in values]


def _entry_missing(entry: dict) -> bool:
    """True iff none of the recording's path(s) exist on disk (any one surviving chapter is enough
    to re-open); no recorded paths counts as missing."""
    paths = entry.get("paths") or []
    return not any(os.path.exists(p) for p in paths)


def _entry_junk(entry: dict) -> bool:
    """True iff `entry` has no valid laps — nothing to time, chart or open, so the dialog greys +
    quarantines it. An UNKNOWN TRACK is NOT junk: the track registry ships with about one circuit,
    so a recording it doesn't recognise is the COMMON case, and that recording still has real laps,
    a real best and a real file to re-open. It renders as "unknown track" with the row's usual trust
    tag (``library.trust_label`` → "provisional" while the start line is auto-fitted)."""
    return not entry.get("lap_count")


def _entry_name(entry: dict) -> str:
    """The recording's FILENAME — its first chapter's basename, i.e. what the user sees in Finder.
    Falls back to the stored first-chapter stem when the entry recorded no paths."""
    paths = entry.get("paths") or []
    if paths:
        return os.path.basename(paths[0])
    return entry.get("stem") or "this recording"


def _entry_tooltip(entry: dict) -> str:
    """Row hover text naming WHICH recording a row is: filename, full path, and the extra-chapter
    count for a multi-chapter recording. None of the four columns names a file (two same-day
    sessions on the same unknown track otherwise read as the same row) though the index carries
    both — this is the affordance Open Recent already gives its entries."""
    paths = entry.get("paths") or []
    lines = [_entry_name(entry)]
    if paths:
        lines.append(paths[0])
        if len(paths) > 1:
            lines.append(f"+ {_plural(len(paths) - 1, 'more chapter')}")
    return "\n".join(lines)


# What a row with NO session record says, in the two places it has to be said. Neither is a dash:
# nothing is missing from the data, there is simply a note nobody has written, and the difference
# between those two matters on a screen whose whole subject is what was and was not recorded.
_NO_RECORD_TIP = (
    "No session record for this recording.\n"
    "Select it and use “Session record…” to write down the conditions, the tyres and the setup — "
    "it is what makes this row comparable with the others.")
_NO_RECORD_LINE = (
    "No session record — nothing written down about the conditions, the tyres or the kart.")


def _record_cell_tip(record: dict | None) -> str:
    """The hover for the Conditions / Tyres cells: the WHOLE record, one clause per line, or the
    invitation when there is none. The two cells show one value each and the record holds seven
    more; a hover that repeated the cell would be the column saying its own name twice."""
    if not record:
        return _NO_RECORD_TIP
    lines = [line for line in (_records.conditions_text(record), _records.tyre_text(record),
                               _records.pressure_text(record), _records.kart_text(record)) if line]
    notes = (record.get("notes") or "").strip()
    if notes:
        lines.append(notes)
    return "\n".join(lines) if lines else _NO_RECORD_TIP


def _ideal_cell(entry: dict) -> tuple[float | None, str | None]:
    """(value, why-there-is-none) for the Ideal-lap cell.

    A number when the entry holds a real stitched ideal. Otherwise ``(None, reason)`` for the two
    states where printing one would be a lie rather than a lap time:

      * the entry has no stored ``theoretical`` — either it predates schema v3 and the migration
        retired the value (it held a copy of ``best`` under the old definition — see
        studio/library.py), or it was written by a v3 writer for a recording with no corner
        partition. **The entry carries nothing that tells those two apart**, so the tip names both
        rather than guessing on read (see ``_IDEAL_STALE_TIP``);
      * the ideal came out equal to the best lap, which means one lap won every segment.

    The dialog is PACER-FREE and reads a plain dict, so it cannot call ``ideal_donor_lap_id()``
    the way export_data and the Stats tile do. Equality with ``best`` is that state's signature:
    the composite is a sum of per-segment minima over the clean laps, so it ties the best lap
    exactly when one lap supplied every one of them, and any genuinely stitched ideal is strictly
    faster (0.22–1.64 s on the recordings measured). A degenerate session ALSO writes the tie
    through ``Session.library_entry``, which is why this is checked on read."""
    theo, best = entry.get("theoretical"), entry.get("best")
    if theo is None:
        return None, _IDEAL_STALE_TIP
    if best is not None and abs(float(theo) - float(best)) <= _IDEAL_SAME_S:
        return None, _IDEAL_ONE_DONOR_TIP
    return float(theo), None


def _date_sort_key(date: str | None) -> float | None:
    """A sortable numeric key for a "YYYY-MM-DD" date string: its ordinal (days). Lexical order
    of an ISO date already equals chronological order, but a numeric key keeps the _NumItem path
    uniform with the time columns. None (no date) → None (sorts last)."""
    if not date:
        return None
    try:
        y, m, d = (int(x) for x in date.split("-"))
        return float(datetime.date(y, m, d).toordinal())
    except (ValueError, TypeError):
        return None


def _epoch_seconds(date: str) -> float | None:
    """UTC epoch SECONDS at midnight of a "YYYY-MM-DD" date — the x value for the PB chart's
    DateAxisItem (which expects POSIX timestamps). None on a malformed date."""
    try:
        y, m, d = (int(x) for x in date.split("-"))
        dt = datetime.datetime(y, m, d, tzinfo=datetime.UTC)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


class LibraryDialog(QDialog):
    """The File ▸ Library… dialog. `index` is a loaded ``studio.library`` index dict;
    `open_recording` is called with an entry's `paths` list to re-open it (the app passes its
    guarded `_load`). The dialog closes itself before re-opening so the reload happens against
    the main window, not behind a modal.

    Every control that touches the FILESYSTEM is dependency-injected and optional — forget / clear /
    reveal / back up / restore, plus the `backup_info` query behind Restore… — so the dialog itself
    stays pacer-free and file-op-free (and therefore hermetic in tests), and any control whose
    callback is absent simply isn't built."""

    def __init__(self, index: dict, open_recording: Callable[[list[str]], None],
                 parent=None,
                 forget_recording: Callable[[dict], dict] | None = None,
                 clear_library: Callable[[], dict] | None = None,
                 reveal_library: Callable[[], None] | None = None,
                 backup_library: Callable[[], None] | None = None,
                 restore_library: Callable[[], dict] | None = None,
                 backup_info: Callable[[], dict | None] | None = None,
                 records: dict | None = None,
                 edit_record: Callable[[dict], dict] | None = None,
                 reload_records: Callable[[], dict] | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — session library")
        self._index = index
        self._open_recording = open_recording
        # SESSION RECORDS — the same data + action split as `index` + `open_recording`, one level
        # out: `records` is a loaded ``studio.session_record`` store (DATA, read here and never
        # written), `edit_record` opens the editor for one library entry and returns the fresh
        # store (the ACTION, owned by the app so this dialog stays file-op-free and hermetic), and
        # `reload_records` re-reads the store after a mutation this dialog did not make — a clear,
        # a restore or a forget, all three of which touch the records as well as the index. Absent
        # callbacks degrade cleanly: the columns still render from whatever `records` holds, and
        # the controls that would write simply aren't built.
        self._records = records if isinstance(records, dict) else _records.empty_store()
        self._edit_record = edit_record
        self._reload_records = reload_records
        # Privacy controls (optional — the dialog degrades to browse-only when not injected, e.g. in
        # a bare test). Each callback OWNS the destructive act (index write + sidecar delete / index
        # wipe, all guarded in the app) and RETURNS the fresh index so the dialog re-renders from it.
        self._forget_recording = forget_recording
        self._clear_library = clear_library
        # Data-portability controls (optional). Reveal opens the app-support folder in Finder; back
        # up copies library.json to a chosen path. The app OWNS both file ops (dialog stays
        # pacer-free / file-op-free); neither mutates the index, so no re-render is needed.
        self._reveal_library = reveal_library
        self._backup_library = backup_library
        # RESTORE — the other half of "Back up…": `restore_library` puts the automatic
        # ``library.json.bak`` back (the app owns the file op and returns the fresh index, like
        # clear does), and `backup_info` reports what that backup holds (a ``library.backup_summary``
        # dict, or None when there is nothing restorable) so the confirm can name BOTH sides of the
        # swap. Data + action, the same split as `index` + `open_recording`; the dialog stays
        # file-op-free, which is also what keeps it hermetic in tests.
        self._restore_library = restore_library
        self._backup_info = backup_info
        self._backup = self._read_backup_info()
        self._entries = list(index.get("entries", []))

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._title = QLabel(_plural(len(self._entries), "analyzed recording"))
        self._title.setProperty("role", "PanelHeader")
        root.addWidget(self._title)

        # ----- filter row: live search (track/date substring) + a per-track combo. Makes the
        # library usable at 50–200 sessions (the 4-column sortable table alone doesn't).
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search track or date…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.search, 1)
        self.track_filter = QComboBox()
        self.track_filter.addItem(_ALL_TRACKS)
        for name in self._distinct_tracks():
            self.track_filter.addItem(name)
        self.track_filter.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self.track_filter)
        # …and the CONDITIONS filter beside it, which is the one this whole feature exists for:
        # "was I comparing like for like?" is answered by narrowing the table to one kind of day
        # and reading the times that survive. A closed vocabulary, so unlike the track combo it is
        # built once (see _ALL_CONDITIONS / _NO_RECORD).
        self.condition_filter = QComboBox()
        self.condition_filter.addItem(_ALL_CONDITIONS, "")
        for tag in _records.CONDITIONS:
            self.condition_filter.addItem(_records.CONDITION_LABELS[tag], tag)
        self.condition_filter.addItem(_NO_RECORD, _NO_RECORD)
        self.condition_filter.setToolTip(
            "Show only sessions you recorded as this kind of day — or the ones with no session "
            "record yet. Conditions are typed in File ▸ Session record…; pacer never fetches them.")
        self.condition_filter.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self.condition_filter)
        root.addLayout(filter_row)

        # ----- the sortable recordings table
        self.table = QTableWidget(len(self._entries), len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        # theme.GRID_ROW_H, not Qt's default 30. This grid's ROW is the click target (SelectRows +
        # SingleSelection, and a double-click OPENS the recording), so its height is the app's
        # control height by the same argument the Laps and Corners grids take it. The 30 it stood
        # at was not a decision anyone made — it is QHeaderView's stock default, and it was the
        # last of the app's three grid heights still written by nobody (#193 named this exact
        # one-line change in its own guard's prose and deliberately did not assert it, so `main`
        # did not depend on this lane landing; the assertion goes in with the fix).
        self.table.verticalHeader().setDefaultSectionSize(theme.GRID_ROW_H)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(_COL_TRACK, QHeaderView.Stretch)
        for col in (_COL_DATE, _COL_LAPS, _COL_BEST, _COL_THEO, _COL_COND, _COL_TYRES):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        # The sample's mechanism, on the columns it is about (see the header-tip constants).
        for col, tip in ((_COL_LAPS, _LAPS_HEADER_TIP), (_COL_BEST, _BEST_HEADER_TIP),
                         (_COL_THEO, _THEO_HEADER_TIP), (_COL_COND, _COND_HEADER_TIP),
                         (_COL_TYRES, _TYRES_HEADER_TIP)):
            self.table.horizontalHeaderItem(col).setToolTip(tip)
        self._fill_rows()
        self.table.setSortingEnabled(True)
        # Newest-first so the auto-selected (first usable) row is the most recent recording.
        self.table.sortItems(_COL_DATE, Qt.DescendingOrder)
        self.table.itemSelectionChanged.connect(self._on_selection)
        self.table.itemDoubleClicked.connect(lambda _it: self._open_selected())
        # Right-click a row → "Forget this recording" (removes it from the index + deletes its
        # sidecar). Only wired when the forget callback is injected.
        if self._forget_recording is not None:
            self.table.setContextMenuPolicy(Qt.CustomContextMenu)
            self.table.customContextMenuRequested.connect(self._on_context_menu)
        root.addWidget(self.table, 3)

        # THE ONE "there is nothing in this table" SURFACE, for both of the ways that happens.
        #
        # A filter that matches nothing hides every row, and a table of hidden rows is just blank
        # space — say so, and name the way back out. An EMPTY INDEX had no state at all: on a
        # genuinely fresh library the dialog opened 900x520 with "0 analyzed recordings", a search
        # box, and a four-column table whose body was an entirely blank 336 px void, over a chart
        # pane reading "Select a recording to see its track's PB progression" — instructing an
        # action that cannot be performed, while the neighbouring "Back up library…" answered
        # honestly (QA D4-07 / D2-03). Both states now REPLACE the table (same stretch), because
        # leaving an empty grid on screen beside the sentence explaining it is the void again.
        # Both senses go through the app's ONE empty-state object, so this dialog's states are the
        # same object as the four panels' — headline in its own slot at EMPHASIS, body at the app's
        # one measure. Only the EMPTY-INDEX sense carries the icon: it is the app's first-run
        # answer, the one state that is about the LIBRARY rather than about a filter.
        self._empty_note = EmptyState("", icon=EMPTY_LIBRARY_ICON)
        self._empty_note.setVisible(False)
        root.addWidget(self._empty_note, 3)
        self._show_empty_note(len(self._entries), "", _ALL_TRACKS)

        # ----- light cross-session progress summary for the selected track (the 2nd/3rd-visit
        # hook: "N sessions · best … · M PBs · improving"). Reads library.track_summary (trustworthy
        # subset); honest — it never counts a provisional/estimated/dropout best as the best.
        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        self._summary.setFont(theme.mono_font(11))
        self._summary.setProperty("role", "Note")
        root.addWidget(self._summary)

        # ----- the selected row's SESSION RECORD, in two lines, because they answer two different
        # questions and only the second one is the reason this feature exists.
        #
        #   * `_record_line` — WHAT was recorded (conditions, tyres, pressures, kart), or the one
        #     sentence saying nothing was. A row that has no record must read as an invitation, not
        #     as a grid of dashes: dashes look like missing DATA, and there is no data missing —
        #     there is a note nobody has written yet.
        #   * `_compare_line` — WHETHER this row and the row holding the track's best lap were the
        #     same kind of day on the same kind of kart (``session_record.comparable``). The table
        #     above ranks best laps across a season; this is the only thing on screen that says
        #     whether that ranking is a ranking of driving. It is silent when there is nothing to
        #     compare (one of the two has no record) rather than claiming either answer, and silent
        #     when the selected row IS the track's best — a session cannot be unlike itself.
        self._record_line = QLabel("")
        self._record_line.setWordWrap(True)
        self._record_line.setFont(theme.mono_font(11))
        self._record_line.setProperty("role", "Note")
        root.addWidget(self._record_line)
        self._compare_line = QLabel("")
        self._compare_line.setWordWrap(True)
        self._compare_line.setFont(theme.mono_font(11))
        self._compare_line.setProperty("role", "Hint")
        root.addWidget(self._compare_line)

        # ----- per-track PB-progression mini-chart (best lap vs recording date)
        self._pb_title = QLabel("PB progression")
        self._pb_title.setProperty("role", "PanelHeader")
        root.addWidget(self._pb_title)
        self.pb_plot = pg.PlotWidget(axisItems={
            "bottom": pg.DateAxisItem(orientation="bottom"),
            # Lap times, not decimal seconds — the same formatter the Best lap column uses, so the
            # axis and the table two rows above it read the same way. Hence no "(s)" in the label.
            "left": _LapTimeAxis(orientation="left")})
        self.pb_plot.setBackground(C.surface)
        self.pb_plot.setMinimumHeight(150)
        # …and a CEILING, so the chart lives in a fixed 150–200 px band. The floor alone decided the
        # whole layout: at 600 px tall everything was on its minimum (the stretch factors never got
        # to apply, and the table's share was 139 px = 4.6 rows), while every pixel the dialog gained
        # grew the chart too (234 px at 860, 370 px at 1200) to draw the same handful of dots. With
        # the ceiling in place the chart still yields FIRST when space is tight (stretch 2 vs the
        # table's 3) and stops growing once it has enough, so the list — the reason this dialog
        # exists — takes everything else: 11.6 rows at the new default, ~23 at 1200 px.
        self.pb_plot.setMaximumHeight(_PB_PLOT_MAX_H)
        self.pb_plot.setLabel("left", "best lap")
        self.pb_plot.getAxis("left").enableAutoSIPrefix(False)
        self.pb_plot.showGrid(x=True, y=True, alpha=0.12)
        # No pyqtgraph chrome on a read-only mini-chart: the hover "A" auto-range button and the
        # right-click plot menu are developer affordances, not part of this dialog.
        self.pb_plot.getPlotItem().hideButtons()
        self.pb_plot.setMenuEnabled(False)
        for side in ("left", "bottom"):
            ax = self.pb_plot.getAxis(side)
            ax.setPen(_pb_axis_pen())
            ax.setTextPen(C.text_dim)
            ax.setTickFont(theme.mono_font(11))
        # ONE reusable curve item (line + markers); its data is swapped per selected track.
        self._pb_curve = pg.PlotDataItem(
            pen=_pb_pen(), symbol="o", symbolSize=7,
            symbolBrush=_PB_BRUSH, symbolPen=_pb_symbol_pen())
        self.pb_plot.addItem(self._pb_curve)
        # Centred in-chart empty-state label, shown when <2 points to plot (see _show_pb). It is a
        # CHILD of the ViewBox, so it is positioned in the box's PIXEL space (_centre_pb_empty) and
        # re-centred on every resize — a data-space position would put it ~1.8e9 px off-screen on a
        # date axis, and a one-shot pixel position drifts ~150 px the first time the dialog resizes.
        vb = self.pb_plot.getPlotItem().getViewBox()
        self._pb_empty = pg.TextItem(color=C.text_dim, anchor=(0.5, 0.5))
        self._pb_empty.setParentItem(vb)
        self._pb_empty.setVisible(False)
        vb.sigResized.connect(lambda *_: self._centre_pb_empty())
        root.addWidget(self.pb_plot, 2)

        # ----- privacy disclosure (calm, factual: it's all local/offline)
        # A WrapLabel, not a wrapped QLabel: this paragraph is the tallest thing in the dialog that
        # a layout cannot measure on its own (see studio/widgets.py). As a plain QLabel it was
        # given ONE line's worth of room in the layout's minimum, so at the dialog's own minimum
        # the note painted 45 px past its box — through the button row, taking the two sentences
        # about tracks.json with it. The wrapper makes the dialog's minimum height include the
        # note's REAL wrapped height at whatever width it is being shown at.
        privacy = WrapLabel(PRIVACY_NOTE)
        privacy.setFont(theme.mono_font(11))
        privacy.setProperty("role", "Note")
        root.addWidget(privacy)

        # ----- buttons
        buttons = QHBoxLayout()
        # Clear the whole index (media + sidecars untouched) — left-aligned, away from Open/Close so
        # a destructive wipe isn't next to the everyday Open. Only shown when the callback is wired.
        if self._clear_library is not None:
            self.clear_btn = QPushButton("Clear library")
            self.clear_btn.setToolTip(
                "Forget every recording in this list (wipes the app-support index only; your video "
                "files and their .pacer.json sidecars are left untouched). A copy of the index is "
                "kept as library.json.bak first")
            self.clear_btn.clicked.connect(self._on_clear_library)
            self.clear_btn.setEnabled(bool(self._entries))
            buttons.addWidget(self.clear_btn)
        # The other half of "Back up…": put the automatic library.json.bak back. Sits beside the wipe
        # it undoes. Disabled (not hidden) when there's no backup yet, so the way back is visible
        # BEFORE it is needed rather than appearing only once history is gone.
        if self._restore_library is not None:
            self.restore_btn = QPushButton("Restore…")
            self.restore_btn.clicked.connect(self._on_restore_library)
            buttons.addWidget(self.restore_btn)
            self._sync_restore_btn()
        # Data portability: reveal the index folder / back up library.json. Non-destructive, so no
        # confirm and always enabled when wired (there's always a folder to reveal, and back-up
        # informs the user when there's nothing to copy yet). Injected callbacks only.
        if self._reveal_library is not None:
            self.reveal_btn = QPushButton("Reveal in Finder")
            self.reveal_btn.setToolTip(
                "Open the folder that holds your library index (library.json)")
            self.reveal_btn.clicked.connect(lambda: self._reveal_library())
            buttons.addWidget(self.reveal_btn)
        if self._backup_library is not None:
            self.backup_btn = QPushButton("Back up…")
            self.backup_btn.setToolTip("Save a copy of your library index to a location you choose")
            self.backup_btn.clicked.connect(lambda: self._backup_library())
            buttons.addWidget(self.backup_btn)
        buttons.addStretch(1)
        # Write up the selected session: conditions + tyres + setup. An everyday action ON THE
        # SELECTED ROW, so it sits with Open rather than with the destructive controls on the left.
        # Also on the row's right-click menu, since that is where "do something to THIS recording"
        # already lives.
        if self._edit_record is not None:
            self.record_btn = QPushButton("Session record…")
            self.record_btn.setToolTip(
                "Write up this session: conditions, tyres and kart setup. It is what makes two "
                "sessions comparable — and it is typed by you, never fetched.")
            self.record_btn.setEnabled(False)
            self.record_btn.clicked.connect(self._edit_selected_record)
            buttons.addWidget(self.record_btn)
        self.open_btn = QPushButton("Open")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_selected)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(self.open_btn)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

        # Auto-select the most recent usable recording (none if all quarantined), then apply the
        # (initially empty) filter so the summary line + row visibility are in sync from the start.
        self._select_first_usable_row()
        self._apply_filter()
        # Size last, with the widget tree complete so the layout's own minimum is settled.
        self._apply_geometry()

    # ------------------------------------------------------------------ size
    def _apply_geometry(self):
        """Open at the user's remembered size when they have one, else at the default — floored so
        the list is still a list, then clamped to the screen this dialog is opening on. Records the
        size it opened at so ``done`` only persists a size the user actually CHANGED: a dialog that
        stores its own default on first close would freeze that default forever, and every future
        user of a never-resized library would be pinned to whatever this build shipped. Guarded
        end-to-end — a prefs failure must never stop the library opening.

        The floor is applied to the size being OPENED, not to the size being STORED. prefs keeps
        what the user asked for and each open applies the constraints of the moment — exactly the
        contract ``_fit_to_screen`` already has in the other direction (a size remembered on a big
        monitor is cut down on the laptop panel and comes back in full when the monitor does).
        Refusing to STORE a too-small size would instead discard the user's request, and discard it
        silently: they would resize, close, re-open, and find the app had quietly forgotten. Only
        the height is floored — the width's minimum is already set by the button row below, at the
        _BROWSABLE_H_MEASURED_AT_W the height floor was measured at, and at that width all four
        columns are on screen. That is a DEPENDENCY, not an observation: see the constant, and
        tests/test_library.py::test_the_browsable_height_floor_still_gets_the_width_it_assumes."""
        try:
            remembered = prefs.library_size()
        except Exception as exc:  # noqa: BLE001 — an unreadable pref just means "use the default"
            print(f"studio: library size not restored ({exc!r}).", flush=True)
            remembered = None
        width, height = remembered or _DEFAULT_SIZE
        height = max(height, _MIN_BROWSABLE_H)
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            width, height = _fit_to_screen(width, height, avail.width(), avail.height())
        self.resize(width, height)
        self._opened_size = (self.width(), self.height())

    # ------------------------------------------------------------------ HiDPI
    def showEvent(self, event):
        """Re-pen the PB chart once the dialog HAS a window — that is when its device-pixel ratio
        is real, and a cosmetic pyqtgraph pen's width is in device pixels (theme.line_width).

        Deliberately not in ``__init__``: ``theme.set_pen_scale`` is a process-wide setting the main
        window owns, and an unshown dialog reports the PRIMARY screen's ratio, not its parent's — so
        constructing the library on a second monitor would re-point the charts' pen scale at the
        wrong screen. Built at the app's current scale (which is already correct), then corrected
        here, and again on ``DevicePixelRatioChange`` if the user drags the dialog to another
        display."""
        super().showEvent(event)
        self._apply_pen_scale()

    def event(self, ev):
        """Follow the window to a screen with a different device-pixel ratio (see ``showEvent``)."""
        if ev.type() == QEvent.Type.DevicePixelRatioChange:
            self._apply_pen_scale()
        return super().event(ev)

    def _apply_pen_scale(self):
        """Point theme at this dialog's device-pixel ratio and re-issue the PB chart's pens."""
        if not theme.set_pen_scale(self.devicePixelRatioF()):
            return                                  # already at this ratio — nothing to redraw
        for side in ("left", "bottom"):
            self.pb_plot.getAxis(side).setPen(_pb_axis_pen())
        self._pb_curve.setPen(_pb_pen())
        self._pb_curve.setSymbolPen(_pb_symbol_pen())

    def done(self, result: int):
        """Remember a size the user changed on the way out — both Open (accept) and Close/Escape
        (reject) route through here — so a library enlarged to browse 200 recordings does not shrink
        back to the default on the next open. Stored VERBATIM, including a size below the browsable
        floor: what the user asked for is theirs to keep, and ``_apply_geometry`` is where the floor
        (and the screen clamp) are applied to what actually opens. ``prefs.set_library_size`` is
        itself fully guarded."""
        if (self.width(), self.height()) != getattr(self, "_opened_size", None):
            prefs.set_library_size(self.width(), self.height())
        super().done(result)

    def _select_first_usable_row(self):
        """Select the first row (in the current sort order) whose DATE cell is NOT flagged disabled
        (MISSING_ROLE) — i.e. a present, non-junk recording. No-op (leaves nothing selected) when
        every row is quarantined, so the PB chart + Open button stay in their empty/disabled state.
        Called once at construction; the PB chart's <2-point empty-state covers the no-selection."""
        for r in range(self.table.rowCount()):
            date_item = self.table.item(r, _COL_DATE)
            if date_item is not None and not bool(date_item.data(MISSING_ROLE)):
                self.table.selectRow(r)
                return
        # Nothing usable: refresh the chart explicitly to its empty-state (no selection signal
        # fires when no row gets selected).
        self._on_selection()

    # ------------------------------------------------------------------ filter
    def _distinct_tracks(self) -> list[str]:
        """The filter combo's track list: the sorted distinct track names, plus an UNKNOWN-TRACK
        bucket when any entry's circuit isn't in the registry. Those rows have no name to sort in,
        but they are real, openable recordings (see ``_entry_junk``) and were the only rows the
        combo could not reach at all."""
        names = sorted({e["track"] for e in self._entries if e.get("track")})
        if any(not e.get("track") for e in self._entries):
            names.append(_UNKNOWN_TRACK)
        return names

    def _apply_filter(self):
        """Hide rows that don't match the search text (track/date substring) AND the selected track
        filter. Live — wired to both the search box and the combo. Uses ``setRowHidden`` so the sort
        order / selection model stay intact; re-selects the first visible usable row afterward so the
        PB chart + summary track the visible set."""
        query = self.search.text().strip().lower() if hasattr(self, "search") else ""
        chosen = self.track_filter.currentText() if hasattr(self, "track_filter") else _ALL_TRACKS
        cond = (self.condition_filter.currentData()
                if hasattr(self, "condition_filter") else "") or ""
        visible = 0
        for r in range(self.table.rowCount()):
            date_item = self.table.item(r, _COL_DATE)
            if date_item is None:
                continue
            hay = date_item.data(FILTER_ROLE) or ""
            track = date_item.data(TRACK_ROLE)
            if chosen == _ALL_TRACKS:
                track_ok = True
            elif chosen == _UNKNOWN_TRACK:
                track_ok = not track          # the bucket stands for every unnamed circuit
            else:
                track_ok = track == chosen
            # The conditions bucket: a real tag matches rows carrying it; "No record" matches rows
            # with no session record at all (which is NOT the same as a record left untagged — a
            # driver who wrote down his pressures but not the weather has a record, and hiding it
            # under "No record" would tell him he never wrote one).
            if not cond:
                cond_ok = True
            elif cond == _NO_RECORD:
                cond_ok = not bool(date_item.data(HAS_RECORD_ROLE))
            else:
                cond_ok = (date_item.data(COND_ROLE) or "") == cond
            hidden = (bool(query) and query not in hay) or not track_ok or not cond_ok
            self.table.setRowHidden(r, hidden)
            visible += not hidden
        # The header and the empty-state describe what's ON SCREEN: a header still claiming "3
        # analyzed recordings" over a table filtered down to nothing is the dialog contradicting
        # itself, and blank space is not a "no matches" message.
        self._update_title(visible)
        # The empty-state names the term the user typed, or — when the search box is empty — the
        # combo that emptied the table. Conditions is checked first only because it is the newer
        # and narrower of the two; either way the sentence names something on screen.
        if cond:
            self._show_empty_note(visible, query, self.condition_filter.currentText(),
                                  _ALL_CONDITIONS)
        else:
            self._show_empty_note(visible, query, chosen)
        # Keep a sensible selection: if the selected row got hidden (or none is selected), land on
        # the first VISIBLE usable row so the chart/summary reflect what's on screen.
        self._reselect_visible()

    def _update_title(self, visible: int | None = None):
        """The header count. Names the FILTERED subset ("0 of 3 analyzed recordings") whenever the
        filter is hiding rows, so the header can never assert a count the table doesn't show."""
        total = len(self._entries)
        whole = _plural(total, "analyzed recording")
        self._title.setText(whole if visible is None or visible == total
                            else f"{visible} of {whole}")

    def _show_empty_note(self, visible: int, query: str, chosen: str,
                         reset: str = _ALL_TRACKS):
        """The "this table has nothing in it" state, in its two DIFFERENT senses — and each gets its
        own sentence, because the way out of them is different:

          * NO LIBRARY AT ALL — nothing has been analysed yet. Says what this list is FOR and the
            one gesture that fills it, which is the whole first-run answer this dialog was missing;
          * FILTERED TO NOTHING — rows exist and the search/track combo is hiding them. Names the
            term and the way back to all of them.

        The table is hidden while either is up, so the sentence stands where the rows would be
        instead of under an empty grid."""
        if not self._entries:
            self._empty_note.set_state(
                "No recordings analysed yet.",
                "Drop a GoPro .MP4 on the main window and it is remembered here — track, date and "
                "best lap.")
            show = True
        else:
            # `chosen`/`reset` are whichever FILTER COMBO is narrowing the table (track, or
            # conditions), so the way back the sentence names is the one that will actually work.
            filtering = bool(query) or chosen != reset
            show = filtering and not visible
            if show:
                term = self.search.text().strip() or chosen
                self._empty_note.set_state(
                    f"No recordings match “{term}”.",
                    f"Clear the search or pick “{reset}” to see all "
                    f"{_plural(len(self._entries), 'recording')}.")
        # The mark belongs to the EMPTY-INDEX sense only: it is the app's first-run answer, about
        # the library itself. A filter that matched nothing is about the filter, and a folder glyph
        # over it would say the library is empty when it is not. `Forget all…` can flip between the
        # two while the dialog is open, so this is decided here rather than at construction — the
        # icon's gap is its own margin, so it leaves nothing behind (see widgets.EmptyState).
        self._empty_note.set_icon_visible(not self._entries)
        self._empty_note.setVisible(show)
        self.table.setVisible(not show)

    def _reselect_visible(self):
        """Select the first VISIBLE, non-disabled row; clear the selection (→ empty chart/summary)
        when the filter leaves nothing usable on screen."""
        cur = self._selected_date_item()
        if cur is not None and not self.table.isRowHidden(cur.row()):
            return                                   # current selection still visible — keep it
        for r in range(self.table.rowCount()):
            if self.table.isRowHidden(r):
                continue
            date_item = self.table.item(r, _COL_DATE)
            if date_item is not None and not bool(date_item.data(MISSING_ROLE)):
                self.table.selectRow(r)
                return
        self.table.clearSelection()
        self._on_selection()                         # nothing visible/usable → empty state

    # ------------------------------------------------------------------ table build
    def _fill_rows(self):
        """Populate one row per entry. The DATE cell carries the row's metadata (paths / track /
        missing flag) in its data roles; a missing-file row is disabled + greyed across all
        columns. Sorting is OFF here (re-enabled by the caller) so insertion order is preserved
        while filling."""
        dim = QBrush(QColor(C.text_muted))
        for r, e in enumerate(self._entries):
            missing = _entry_missing(e)
            junk = _entry_junk(e)
            disabled = missing or junk
            date = e.get("date")
            track = e.get("track")
            best = e.get("best")
            theo, theo_reason = _ideal_cell(e)

            date_item = _NumItem(date or "—")
            date_item.setData(NUM_ROLE, _date_sort_key(date))
            date_item.setData(PATHS_ROLE, list(e.get("paths") or []))
            date_item.setData(TRACK_ROLE, track)
            date_item.setData(FP_ROLE, e.get("fingerprint"))
            # MISSING_ROLE doubles as the "not openable / not auto-selectable" flag — set for a
            # file-missing OR a quarantined junk row, so _on_selection / _open_selected guard both.
            date_item.setData(MISSING_ROLE, disabled)
            # The search haystack: lower-cased "track date FILENAME" so the box matches any of the
            # three. An unknown-track row is keyed on the label it SHOWS, so typing what's on
            # screen reaches it (its `track` is null — there is nothing else to match).
            #
            # THE FILENAME IS IN IT BECAUSE THE DIALOG LEADS WITH THE FILENAME (§6.3b). Every row's
            # tooltip and the forget-confirmation name the recording by its first chapter's
            # basename — "GX010060.MP4" — while the search box matched track and date only. So
            # typing the one identifier the dialog had just shown the user HID the row it names.
            # Searching what is on screen has to find what is on screen.
            # ...and the session record's own words are in the haystack too, so typing "wet" or
            # "MG Yellow" reaches the rows that say so. Same rule as the filename: what the row
            # SHOWS has to be findable by typing it, and the two new columns show these.
            record = self._record_for(e)
            date_item.setData(
                FILTER_ROLE,
                " ".join(p for p in (track or _UNKNOWN_LABEL, date or "", _entry_name(e),
                                     _records.conditions_text(record),
                                     _records.tyre_text(record)) if p).strip().lower())
            # The conditions TAG the filter combo matches on — "" for a row with no record, which
            # is what the "No record" bucket selects.
            date_item.setData(COND_ROLE, (record or {}).get("conditions") or "")
            date_item.setData(HAS_RECORD_ROLE, record is not None)

            # A junk row says so; a present-but-missing-file row keeps its established label. An
            # UNTRUSTWORTHY-but-openable row gets a muted trust tag (provisional/estimated/dropout)
            # so the user can see WHICH sessions the PB chart excludes — reusing the theme's trust
            # tier (italic + PROVISIONAL_COLOR, palette-safe).
            trust = None if disabled else _library.trust_label(e)
            suffix = ("  (no laps)" if junk else "  (file missing)" if missing
                      else f"  · {trust}" if trust else "")
            track_text = f"{track or _UNKNOWN_LABEL}{suffix}"

            track_item = QTableWidgetItem(track_text)

            # The SAMPLE both time cells beside it are a minimum over (see _COL_LAPS). Numeric
            # like them, so "sort by Laps" answers "is this ranking pace or session length?" in
            # one click. A junk row's 0 is printed rather than dashed: it is the true count and it
            # is why that row is quarantined.
            laps = e.get("lap_count")
            laps_item = _NumItem(str(laps) if isinstance(laps, int) else "—")
            laps_item.setData(NUM_ROLE, None if not isinstance(laps, int) else float(laps))
            best_item = _NumItem(fmt_time(best) if best is not None else "—")
            best_item.setData(NUM_ROLE, best)
            theo_item = _NumItem(fmt_time(theo) if theo is not None else "—")
            theo_item.setData(NUM_ROLE, theo)

            # The two comparability columns. Conditions prints the TAG only (the temperatures are
            # in the cell's hover and on the line under the table) so the column stays one word
            # wide; Tyres prints the AGE and sorts on it, with the set's name in the hover — the
            # name is what tells two sets apart, the age is what moves a lap time.
            cond = (record or {}).get("conditions") or ""
            cond_item = QTableWidgetItem(_records.CONDITION_LABELS.get(cond, "—"))
            tyre_laps = (record or {}).get("tyre_laps")
            tyres_item = _NumItem(str(tyre_laps) if tyre_laps is not None else "—")
            tyres_item.setData(NUM_ROLE, None if tyre_laps is None else float(tyre_laps))

            items = (date_item, track_item, laps_item, best_item, theo_item, cond_item, tyres_item)
            tooltip = _entry_tooltip(e)
            record_tip = _record_cell_tip(record)
            for col, it in enumerate(items):
                # Every cell hovers to the recording's file identity — the columns show only track +
                # date, so hovering anywhere on the row is what tells two same-day sessions apart.
                # Track is the one STRETCH column, so it is the one that elides (31 px of overflow
                # at the dialog's own 489 px minimum width): its tooltip LEADS with its own full
                # label, so the clipped tail is readable rather than merely truncated.
                # ...and the Ideal-lap cell LEADS with why it is empty when it is, for the same
                # reason: an em dash beside a real lap time reads as missing data, and one of the
                # two causes is "re-open this recording", which the user can act on.
                if col == _COL_TRACK:
                    it.setToolTip(f"{track_text}\n\n{tooltip}")
                elif col == _COL_THEO and theo_reason:
                    it.setToolTip(f"{theo_reason}\n\n{tooltip}")
                elif col in (_COL_COND, _COL_TYRES):
                    # The two comparability cells hover with the WHOLE record, not just their own
                    # value: a one-word "Dry" is the summary of four numbers the driver typed, and
                    # the hover is where the rest of them live (see _record_cell_tip).
                    it.setToolTip(f"{record_tip}\n\n{tooltip}")
                else:
                    it.setToolTip(tooltip)
                if disabled:
                    it.setForeground(dim)
                    it.setFlags(it.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable)
                elif col == _COL_TRACK and trust:
                    # Muted + italic across the row's Track cell so the tag reads as demoted, not an
                    # error. The row stays fully selectable/openable — it's just marked, not blocked.
                    it.setForeground(QBrush(QColor(theme.PROVISIONAL_COLOR)))
                    theme.apply_provisional_style(it, True)
                self.table.setItem(r, col, it)

    # ------------------------------------------------------------------ selection
    def _selected_date_item(self) -> QTableWidgetItem | None:
        """The DATE cell of the current selection (the metadata-bearing cell), or None."""
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        return self.table.item(rows[0].row(), _COL_DATE)

    def _on_selection(self):
        """A row was selected: refresh the PB chart + progress summary for its track; enable Open
        only for a usable (present, non-junk) recording."""
        item = self._selected_date_item()
        if item is None:
            self.open_btn.setEnabled(False)
            self._sync_record_btn(None)
            self._show_pb(None)
            self._show_summary(None)
            self._show_record(None)
            return
        missing = bool(item.data(MISSING_ROLE))
        self.open_btn.setEnabled(not missing)
        entry = self._selected_entry()
        self._sync_record_btn(entry)
        track = item.data(TRACK_ROLE)
        self._show_pb(track)
        self._show_summary(track)
        self._show_record(entry)

    # ------------------------------------------------------------------ session record
    def _record_for(self, entry: dict | None) -> dict | None:
        """The session record for a library `entry`, or None when it has none. The two are joined
        on the library FINGERPRINT — the chapter-invariant recording identity — so one chapter and
        the full chaptered open of the same outing share one record, exactly as they share one
        library row."""
        fp = (entry or {}).get("fingerprint")
        return _records.get(self._records, fp) if fp else None

    def _selected_entry(self) -> dict | None:
        """The full library entry behind the current selection, matched by fingerprint. The table
        carries only what it displays; everything else about a row comes from here."""
        item = self._selected_date_item()
        fp = item.data(FP_ROLE) if item is not None else None
        if not fp:
            return None
        return next((e for e in self._entries if e.get("fingerprint") == fp), None)

    def _sync_record_btn(self, entry: dict | None) -> None:
        """Enable "Session record…" only for a row that HAS an entry to write up, and say which of
        the two things the click will do — starting one, or editing the one that exists."""
        btn = getattr(self, "record_btn", None)
        if btn is None:
            return
        btn.setEnabled(entry is not None)
        if entry is not None and self._record_for(entry) is not None:
            btn.setText("Session record…")
            btn.setToolTip("Edit what you recorded about this session — conditions, tyres, setup")
        else:
            btn.setText("Session record…")
            btn.setToolTip(
                "Write up this session: conditions, tyres and kart setup. It is what makes two "
                "sessions comparable — and it is typed by you, never fetched.")

    def _show_record(self, entry: dict | None) -> None:
        """Set the selected row's two record lines (see where they are built for what each is for).
        Both blank when nothing is selected; the first becomes the invitation when the selected row
        has no record."""
        if entry is None:
            self._record_line.setText("")
            self._compare_line.setText("")
            return
        record = self._record_for(entry)
        self._record_line.setText(_records.summary_line(record) or _NO_RECORD_LINE)
        self._compare_line.setText(self._comparability_text(entry, record))

    def _comparability_text(self, entry: dict, record: dict | None) -> str:
        """"Not like-for-like vs your best here (2026-06-14): Dry vs Wet · track 38° vs 22°" — or
        the reassuring converse, or "" when the question cannot be answered.

        THIS IS THE SENTENCE THE WHOLE FEATURE EXISTS FOR. The table above ranks best laps across a
        season and, without this, every such ranking silently assumes the sessions were comparable.
        It stays SILENT rather than guessing in the three cases where it has no answer: the
        selected row has no record, the track's best-lap session has no record, or the selected row
        IS that session (nothing is unlike itself). Reporting "comparable" from two blank records
        would be the worst of the four outcomes — a reassurance backed by nothing."""
        track = entry.get("track")
        if not track or record is None:
            return ""
        best = _library.best_entry(self._index, track)
        if not best or best.get("fingerprint") == entry.get("fingerprint"):
            return ""
        other = self._record_for(best)
        if other is None:
            return ""
        when = f" ({best['date']})" if best.get("date") else ""
        diffs = _records.comparable(record, other)
        if diffs:
            return f"Not like-for-like vs your best here{when}:  " + "  ·  ".join(diffs)
        return f"Comparable with your best here{when} on everything you recorded."

    def _edit_selected_record(self) -> None:
        """Open the session-record editor for the selected row through the injected callback, then
        re-read the store and re-render. The dialog never writes: the app owns the editor and the
        file op, which is what keeps this one hermetic (see the constructor)."""
        entry = self._selected_entry()
        if entry is None or self._edit_record is None:
            return
        try:
            store = self._edit_record(entry)
        except Exception as exc:  # noqa: BLE001 — a record write must never break the library
            print(f"studio: session record not saved ({exc!r}).", flush=True)
            store = None
        if isinstance(store, dict):
            self._records = store
        elif self._reload_records is not None:
            self._reload_records_guarded()
        self._rerender()

    def _show_summary(self, track: str | None):
        """Set the light cross-session progress line for `track` from ``library.track_summary``
        (trustworthy subset). Blank when there's no track selected; otherwise a compact honest read:
        ``"<track> · 12 sessions · best 68.42 (2026-06-14) · 3 PBs · improving"``. A track with no
        trustworthy dated best just reports its session count (nothing to boast yet)."""
        summary = _library.track_summary(self._index, track) if track else None
        if not summary:
            self._summary.setText("")
            return
        parts = [summary["track"], _plural(summary["sessions"], "session")]
        if summary["best"] is not None:
            best = fmt_time(summary["best"])
            date = f" ({summary['best_date']})" if summary["best_date"] else ""
            parts.append(f"best {best}{date}")
            # Only claim PBs once a later session has actually beaten one: the first session on a
            # track sets the bar rather than clearing it, and "0 PBs" would read as a failure.
            if summary["pb_count"]:
                parts.append(_plural(summary["pb_count"], "PB"))
            trend = _TREND_WORD.get(summary["trend"])
            if trend:
                parts.append(trend)
        self._summary.setText("  ·  ".join(parts))

    def _show_pb(self, track: str | None):
        """Plot best-lap-vs-date for `track`: line for >=2 dated bests, a framed single marker for
        1, empty-state for 0. No track means one of THREE different states — an empty library,
        nothing selected, or a selected recording whose circuit the track database doesn't know
        (the common case for a new user, and now a selectable row) — so each gets its own sentence
        instead of asking the user to select what they already selected, or to select from a list
        with nothing in it (QA D2-15: "Select a recording…" was shown over an empty library, an
        instruction that cannot be carried out)."""
        if not track:
            self._pb_curve.setData([], [])
            self._pb_title.setText("PB progression")
            self._set_pb_axes(False)
            if not self._entries:
                message = "Analyse two sessions at the same track to see your progression"
            elif self._selected_date_item() is not None:
                message = ("This recording's track isn't in your database yet, so there's nothing "
                           "to chart")
            else:
                message = "Select a recording to see its track's PB progression"
            self._set_pb_empty(message)
            return
        series = _library.pb_series(self._index, track)
        xs, ys = [], []
        for date, best in series:
            x = _epoch_seconds(date)
            if x is not None:
                xs.append(x)
                ys.append(best)
        self._pb_curve.setData(xs, ys)
        self._set_pb_axes(bool(ys))
        if len(ys) >= 2:
            self._pb_title.setText(
                f"PB progression — {track}  ({fmt_time(min(ys))} best over {len(ys)} sessions)")
            self._set_pb_empty(None)
            self.pb_plot.enableAutoRange()
            self.pb_plot.autoRange()
        elif len(ys) == 1:
            # ONE SESSION IS DATA, so the empty state has no business here (§6.3a, found
            # independently by three review lanes): this branch drew the amber point AND put
            # "Not enough sessions on this track yet…" across the middle of the plot, so the
            # sentence was painted THROUGH the datum it was denying. The title already carries the
            # count and the time — "PB progression — <track>  (1 session: 1:08.201)" — which is the
            # same fact stated where it does not overlap the mark. Empty state and data layer are
            # mutually exclusive now, in both directions.
            self._pb_title.setText(f"PB progression — {track}  (1 session: {fmt_time(ys[0])})")
            self._frame_single_point(xs[0], ys[0])
            self._set_pb_empty(None)
        else:
            self._pb_title.setText(f"PB progression — {track}  (no dated best laps)")
            self._set_pb_empty("Not enough sessions on this track yet to chart progression")

    def _set_pb_axes(self, plotted: bool):
        """Label the axes only while something is plotted, and drop the range when nothing is.
        ``_frame_single_point`` disables autorange, so a de-selected row otherwise leaves ITS
        numbers (67.771–69.771 s) ticking an empty grid — an axis describing a recording the dialog
        is no longer showing. The empty-state sentence is then the only thing in the plot."""
        for side in ("left", "bottom"):
            self.pb_plot.getAxis(side).setStyle(showValues=plotted)
        if not plotted:
            # A hard unit range, not autoRange(): with no data pyqtgraph's autorange KEEPS the old
            # bounds (67.771–69.771 → merely re-padded to 67.386–70.156). The plotting branches
            # each set their own range back, so nothing has to be restored here.
            self.pb_plot.getPlotItem().getViewBox().setRange(
                xRange=(0.0, 1.0), yRange=(0.0, 1.0), padding=0)

    def _set_pb_empty(self, message: str | None):
        """Show (or hide on None) the centred empty-state label."""
        if not message:
            self._pb_empty.setVisible(False)
            return
        self._pb_empty.setText(message)
        self._pb_empty.setVisible(True)
        self._centre_pb_empty()

    def _centre_pb_empty(self):
        """Put the empty-state label in the middle of the plot. Its pos() is read in the PARENT
        ITEM's (the ViewBox's) coordinates — PIXELS — so it must come from ``boundingRect()``, never
        from the data-space ``viewRect()``. Also wired to the ViewBox's ``sigResized`` so the label
        follows the box when the dialog is resized."""
        vb = self.pb_plot.getPlotItem().getViewBox()
        self._pb_empty.setPos(vb.boundingRect().center())

    def _frame_single_point(self, x: float, y: float):
        """Set a small PADDED axis range around a single (x, y) point so it's framed centrally (a
        bare ``setData`` of one point with autorange leaves a degenerate zero-width range)."""
        self.pb_plot.disableAutoRange()
        day = 86400.0
        self.pb_plot.setXRange(x - day, x + day, padding=0)
        self.pb_plot.setYRange(y - 1.0, y + 1.0, padding=0)

    # ------------------------------------------------------------------ privacy: forget / clear
    def _on_context_menu(self, pos):
        """Right-click on a row → a small menu with "Forget this recording". No menu on empty space
        or when the forget callback isn't wired."""
        if self._forget_recording is None:
            return
        item = self.table.itemAt(pos)
        if item is None:
            return
        date_item = self.table.item(item.row(), _COL_DATE)
        if date_item is None:
            return
        menu = QMenu(self)
        record_act = None
        if self._edit_record is not None:
            # "Do something to THIS recording" already lives on this menu, and writing up its
            # session is now one of the two things there are to do to a row.
            record_act = menu.addAction("Session record…")
            record_act.setToolTip(
                "Write up (or edit) this session's conditions, tyres and kart setup")
            menu.addSeparator()
        act = menu.addAction("Forget this recording…")
        act.setToolTip(
            "Remove this recording from the library index and delete its .pacer.json timing-line "
            "sidecar and its session record. Your video file is not touched.")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is act:
            self._forget_row(date_item)
        elif record_act is not None and chosen is record_act:
            self.table.selectRow(date_item.row())
            self._edit_selected_record()

    def _forget_row(self, date_item: QTableWidgetItem):
        """Confirm, then forget the row: the injected callback removes the index entry + deletes its
        sidecar (guarded in the app) and returns the fresh index, from which the table re-renders."""
        fp = date_item.data(FP_ROLE)
        if not fp:
            return
        entry = next((e for e in self._entries if e.get("fingerprint") == fp), None)
        if entry is None:
            return
        track = entry.get("track") or "unknown track"
        date = entry.get("date") or "no date"
        # Lead with the FILENAME: track + date alone can't tell two same-day unknown-track sessions
        # apart, and this confirm deletes that recording's sidecar.
        ok = QMessageBox.question(
            self, "Forget this recording",
            f"Forget “{_entry_name(entry)}” — {track} ({date})?\n\n"
            "This removes it from the library and deletes its .pacer.json timing-line "
            "sidecar" + (
                # NAME THE RECORD when there is one to lose. Its sidecar and its library row come
                # back the moment the recording is re-opened; a hand-typed record does not — the
                # footage does not know what tyres were on the kart — so a confirm that did not
                # mention it would be hiding the only irreversible half of this gesture.
                " and the session record you wrote for it (a copy of your records is kept as "
                "session_records.json.bak)"
                if self._record_for(entry) is not None else "") +
            # …and the MARKS, named UNCONDITIONALLY where the record is named only when there is
            # one. "any marks" is true whether there are none or ten, and this dialog has no seam
            # to count them through — where it has one for records (`_record_for`). Naming a
            # possibility costs a clause; leaving the irreversible half of the gesture unmentioned
            # because the count was inconvenient to obtain costs the user their notes.
            ", along with any marks you wrote against it (kept as marks.json.bak)"
            ". Your video file is not touched.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        self._index = self._forget_recording(entry)
        self._reload_records_guarded()
        self._rerender()

    def _reload_records_guarded(self) -> None:
        """Re-read the session-record store after a mutation this dialog did not make (a clear, a
        restore, a forget — all three touch the records as well as the index). Guarded: a failing
        read leaves the store as it was rather than emptying the two columns, because an empty
        column here reads as "you never wrote these up"."""
        if self._reload_records is None:
            return
        try:
            store = self._reload_records()
        except Exception as exc:  # noqa: BLE001 — a record read must never break the library
            print(f"studio: session records not re-read ({exc!r}).", flush=True)
            return
        if isinstance(store, dict):
            self._records = store

    def _read_backup_info(self) -> dict | None:
        """What the automatic backup holds (a ``library.backup_summary`` dict) via the injected
        query, or None when it isn't wired / there's nothing restorable. Guarded: a failing query
        just means "no restore offered", never a broken dialog."""
        if self._backup_info is None:
            return None
        try:
            info = self._backup_info()
        except Exception as exc:  # noqa: BLE001 — a backup query must never break the library
            print(f"studio: library backup not readable ({exc!r}).", flush=True)
            return None
        return info if isinstance(info, dict) and info.get("entries") else None

    def _sync_restore_btn(self):
        """Enable Restore… only when there IS something to restore, and say which state it's in —
        the tooltip carries the backup's size + date so the button explains itself before it is
        clicked (and explains its own greyed-out state when there is no backup yet)."""
        btn = getattr(self, "restore_btn", None)
        if btn is None:
            return
        info = self._backup
        btn.setEnabled(info is not None)
        if info is None:
            btn.setToolTip(
                "No library backup yet — one is kept automatically as library.json.bak whenever "
                "the library is cleared")
        else:
            btn.setToolTip(
                f"Put back the automatic backup{_backup_when(info.get('mtime'))} "
                f"({_plural(int(info['entries']), 'recording')}). The library you have now is kept "
                "as the backup, so you can swap back")

    def _on_clear_library(self):
        """Confirm, then wipe the whole index via the injected callback (media + sidecars left
        untouched) and re-render to the empty state. The confirm names what SURVIVES (video files,
        sidecars) and — since this is the one destructive control in the app — where the copy of the
        index itself goes, plus the way back: Restore… when it's wired, otherwise the .bak file the
        Reveal in Finder button two along opens the folder for."""
        if self._clear_library is None or not self._entries:
            return
        recovery = ("You can put it back with “Restore…”."
                    if self._restore_library is not None else
                    "“Reveal in Finder” opens the folder that holds it.")
        ok = QMessageBox.question(
            self, "Clear library",
            f"Forget all {_plural(len(self._entries), 'recording')} from the library?\n\n"
            "This wipes the library index, every session record you have written and every mark "
            "you have made — your video files and their .pacer.json sidecars are left "
            "untouched.\n\n"
            f"A copy of each is kept first (library.json.bak, session_records.json.bak, "
            f"marks.json.bak). "
            f"{recovery}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        self._index = self._clear_library()
        self._backup = self._read_backup_info()   # the wipe just created one
        self._reload_records_guarded()            # …and it wiped the session records too
        self._rerender()

    def _on_restore_library(self):
        """Confirm, then put the automatic backup back via the injected callback and re-render. The
        confirm names BOTH sides — what is about to be replaced and what replaces it — because a
        restore is destructive in the other direction; it also says the current library becomes the
        backup, which is what makes this reversible."""
        if self._restore_library is None or not self._backup:
            return
        info = self._backup
        ok = QMessageBox.question(
            self, "Restore library",
            f"Replace this library ({_plural(len(self._entries), 'recording')}) with the backup"
            f"{_backup_when(info.get('mtime'))} "
            f"({_plural(int(info['entries']), 'recording')})?\n\n"
            "Your session records are put back with it. The library you have now is kept as the "
            "backup, so you can swap back; your video files and their .pacer.json sidecars are "
            "not touched either way.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        self._index = self._restore_library()
        self._backup = self._read_backup_info()   # the swap replaced it with what we just left
        self._reload_records_guarded()            # …and the records were swapped back with it
        self._rerender()

    def _rerender(self):
        """Rebuild the table + chart from ``self._index`` after a forget/clear. Rebuilds rather than
        surgically deleting one QTableWidget row so the sort keys / role data stay consistent."""
        self._entries = list(self._index.get("entries", []))
        self._update_title()                         # re-run with the visible count by _apply_filter
        self.table.setSortingEnabled(False)
        self.table.clearContents()
        self.table.setRowCount(len(self._entries))
        self._fill_rows()
        self.table.setSortingEnabled(True)
        self.table.sortItems(_COL_DATE, Qt.DescendingOrder)
        # Rebuild the track-filter combo (a forget/clear can change the distinct-track set); keep the
        # current pick if it still exists, else fall back to "All tracks". Block the change signal so
        # the rebuild doesn't re-trigger _apply_filter mid-render.
        prev = self.track_filter.currentText()
        self.track_filter.blockSignals(True)
        self.track_filter.clear()
        self.track_filter.addItem(_ALL_TRACKS)
        for name in self._distinct_tracks():
            self.track_filter.addItem(name)
        idx = self.track_filter.findText(prev)
        self.track_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.track_filter.blockSignals(False)
        if getattr(self, "clear_btn", None) is not None:
            self.clear_btn.setEnabled(bool(self._entries))
        self._sync_restore_btn()
        self._select_first_usable_row()
        self._apply_filter()

    # ------------------------------------------------------------------ open
    def _open_selected(self):
        """Re-open the selected recording via the injected callback (the app's `_load`). Closes
        the dialog first so the reload runs against the main window. No-op for a missing-file row
        (Open is disabled there, and double-click is guarded here too)."""
        item = self._selected_date_item()
        if item is None or bool(item.data(MISSING_ROLE)):
            return
        paths = item.data(PATHS_ROLE)
        if not paths:
            return
        self.accept()
        self._open_recording(list(paths))
