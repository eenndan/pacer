"""StatsView (the Stats page): the session-statistics dashboard behind the Laps|Corners|Stats
header toggle.

A read-only, scrollable column of stat groups over studio/stats.py's SessionStats service +
the existing Session accessors — SESSION totals, the DATA TRUST card (what the page's numbers
are worth: the start-line/track/exclusion caveats, the timing clock, the g provenance and the
IMU↔GPS cross-check), PACE distribution, the IDEAL LAP and its decomposition (the theoretical
best, what it says is on the table, and which segments that time lives in), SPEED & G peaks, the
g-g friction circle, DRIVING (brake/coast reductions), per-SECTOR best/median/σ, and a per-lap
statistics table. Compact in the quadrant; the panel-maximize button turns it into a full-window
dashboard.
(It paints ph.corners-out — "fill this window quadrant". The transport's ph.arrows-out button is
a different action, "fill the SCREEN with the video", and this line used to name that one.)

HONESTY RULES. The maximized dashboard hides the map, so the page carries its OWN unverified-
timing banner rather than leaning on the map's. Unverified timing mutes the PER LAP Time column
(the same cells the Laps tab mutes) and the stitched target tiles, and suppresses the ★ best —
never the measured tiles beside them, which ARE laps you drove. A statistic that cannot exist
(no accelerometer, no complete lap) says so in words next to the em-dash.

Pacer-free; refreshed on load / re-segmentation, never on the 30 Hz tick. Numbers render in
the mono stack (tabular figures); a signal-absent statistic shows an em-dash, never a fake 0."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import data_quality, gmeter, theme, units
from . import stats as stats_service
from ._signal import fmt_hms, fmt_time, plural

# The Coaching panel's OWN row filter and top-N, imported (not re-implemented) so the digest tile
# and the coaching headline can never state different totals for the same three corners — L5-02.
from .coaching_panel import PANEL_TOP_N, _ranked_shown
from .consistency import pb_mask
from .lap_table import (
    BEST_LAP_MARK,
    DROPOUT_MARK,
    DROPOUT_SUFFIX,
    DROPOUT_TOOLTIP,
    EXCLUDED_MARK,
    NUM_ROLE,
    NUMERIC_COL_START,
    PROVISIONAL_COLOR,
    PROVISIONAL_TOOLTIP,
    _NumItem,
    align_headers_over_their_columns,
    estimated_timing_tooltip,
    set_corner_direction,
)
from .theme import C
from .widgets import DASH, Tile, WrapLabel, budget_plot_gutters

if TYPE_CHECKING:  # the injected session — typed for readers, not imported at runtime
    from .session import Session

# DASH ("—", the "no signal" cell/tile — never a fake 0) and the stat TILE are imported from
# studio.widgets: both were this page's private inventions and both are now app vocabulary.
# The page's OWN unverified-timing banner. View ▸ Session statistics maximizes the lap panel, which
# hides the MAP — and with it the app's one prominent "Lap timing is unverified" strip — exactly on
# the surface that then paints a full page of bold statistics. So the CTA is repeated here, above
# SESSION, where it is read before any number. Same opening sentence as the map strip (one wording,
# two places); the page-specific half names what it invalidates.
PROVISIONAL_BANNER = ("Lap timing is unverified — every lap time, split and “best” on this page is "
                      "measured from an auto-fitted start/finish line. Drag it on the map to where "
                      "a lap begins.")
# The 0-lap page: the app's shared copy plus the one clause only this page can add. Without it the
# PACE/SPEED groups render as a wall of em-dashes whose only explanation is a status-bar line
# outside the maximized panel.
#
# EVERY WORD BUT THE MIDDLE CLAUSE IS data_quality's. This page's private copy had drifted furthest
# of the four (QA D2-02): it said "If the track looks right on the map, drag the start/finish line
# to where a lap begins" where the map itself said "If this is the right track, drag the
# start/finish line on the map to set where a lap begins" — the same instruction, re-typed, and it
# omitted the second way out entirely.
NO_LAPS_STATS_CLAUSE = "There are no lap statistics to show."
NO_LAPS_TEXT = (f"{data_quality.NO_LAPS_HEADLINE} {NO_LAPS_STATS_CLAUSE} "
                f"{data_quality.no_laps_body()}")
# ...and the two HALVES that render it, because one banner strip is the wrong home for all 308
# characters. `#ProvisionalBanner` is an 11 px semibold amber CALL-TO-ACTION LINE (theme.py: the
# map trust strip's actionable tier), and it was handed a paragraph: 491 px of 11 px type, the
# app's longest prose in its shortest step, while the same sentences on the four sibling panels in
# the same frame are 13 px inside a 440 px measure. So the STATEMENT stays in the banner — the one
# line the role is built for, and the line that says which page this is about — and the WHY/WHAT
# NEXT becomes prose in the app's prose step at the app's prose measure. Both halves are still
# NO_LAPS_TEXT concatenated, which is the string tests/test_state_surfaces.py holds every zero-lap
# surface to.
NO_LAPS_BANNER = f"{data_quality.NO_LAPS_HEADLINE} {NO_LAPS_STATS_CLAUSE}"
NO_LAPS_PROSE = data_quality.no_laps_body()
# The absent-accelerometer sentence — used BOTH in the DATA TRUST card and under the SPEED · G
# tiles, so the dashes and the trust card explain themselves in the same words.
NO_GMETER_NOTE = ("g-meter: no accelerometer in this recording — lateral g, braking g and grip "
                  "are unavailable.")
# (The local TILE_VALUE_PT alias is gone: the step it named is theme.EMPHASIS, the tile that used
# it is widgets.Tile, and the two call sites left in this file read the token directly.)
TILES_PER_ROW = 4         # tile-grid max columns in a normal (quadrant-width) pane
TILE_MIN_PX = 148         # reflow threshold: columns = viewport width // this, clamped
#                           2..the cap (C6 — the hard-coded 4 pushed the 4th tile column,
#                           incl. the coaching digest, off-pane at the default quadrant width)
# ⌘⇧S maximizes this page into the whole window, where the 4-column cap left the tile rows
# ending ~1000 px short of the right edge. Above WIDE_PANE_PX the reflow's ceiling rises, so a
# dashboard-width pane packs the same tiles into fewer, wider rows. The 2..4 quadrant behaviour
# is untouched — this only lifts a ceiling that a quadrant never reaches.
TILES_PER_ROW_WIDE = 6
WIDE_PANE_PX = 1200       # column width from which a tile grid may run to TILES_PER_ROW_WIDE
# ...and the reflow that ceiling was standing in for. Raising the tile cap widened the ROWS; it
# could not change the fact that this page is ONE COLUMN, so at 1920x1200 the maximized dashboard
# was a 700 px strip of content with ~70 % of the canvas empty beside it — every table capped at
# its own content width, and the trend sparkline stretching 24 samples across 1850 px because it
# was the one widget with nothing to cap it. The demo's marquee keystroke produced an empty page
# with a ribbon down its left edge.
#
# So above two columns' worth of pane the page COMPOSES: the same sections, in the same order,
# dealt into 2 and then 3 columns (StatsView._place_columns). Below that — every quadrant the app
# opens at, and the whole 845x414 minimum — nothing changes at all: one column, one scroll.
# THE FLOOR FOR A COLUMN THAT HOLDS ONLY PROSE, TILES AND CHARTS — all of which reflow. It is the
# app's own prose measure: the width this page was designed at (the 1280x800 quadrant gives it 445,
# and 421 inside its gutters), what the zero-lap paragraphs and the ideal-lap notes are capped to,
# and wide enough for three tiles and the DATA TRUST card's term/value split.
#
# IT IS A FLOOR AND NOT THE ANSWER, and that distinction is this page's P1. A column that holds a
# REPORT TABLE cannot be set at a prose measure: the tables are content-sized and do not reflow —
# they scroll (see _ReportTable) — so a column narrower than one of them does not wrap it, it HIDES
# its rightmost columns behind an inner scrollbar. Measured on D24 the five tables want
# 345 / 532 / 541 / 610 / 718 px, against the 440 this constant declares. Composing on this number
# alone lost `Apex best · Apex med · Grip %` off CORNERS, `Trap med · Exit Δ` off STRAIGHTS,
# `Brake s · Coast s` off PER LAP and `m later` off BRAKING at the app's DEFAULT 1440x900 window,
# and truncated CORNERS mid-digit ("73.6 | 67.") at 1920x1200 — data the single-column page shows.
#
# So the packer asks each section group what its widest NON-REFLOWING member needs
# (_group_min_width) and only composes an arrangement every column of which can be given it.
PAGE_COL_MIN_PX = theme.EMPTY_MEASURE_PX
PAGE_COL_GAP = theme.SPACE_XL   # between two section columns: the page's own large-surface step
# The candidate compositions, in preference order. Each entry is the GRID COLUMNS; each grid column
# lists the section groups stacked in it, top to bottom. Read down and then across, every one of
# them is the same page in the same order — that is what makes them interchangeable at all.
#
# THE TWO 2-COLUMN FORMS ARE NOT REDUNDANT. Group 1 carries the 718 px CORNERS table, so a
# composition that gives it a column of its own needs 718 + 610 + gutters = 1376 px of pane, which
# the 1440x900 window has (1420) and the 1280x800 one (1260) does not. The second form stacks
# groups 1 and 2 in ONE column — the widest, since both live there — and needs only 440 + 718 =
# 1206, so the smaller window still composes instead of falling back to a 1260 px page with a
# 700 px strip of content on it.
#
# The balanced form is preferred where both fit, and "balanced" is measured rather than assumed:
# on D24 at the 718/718/654 px columns 1440x900 gives, the three groups stand 983 / 907 / 1338 px
# tall — group 3 is the TALLEST, not the middle one. So 1+2 beside 3 is 1914 | 1338 (1.43x) where
# 1 beside 2+3 is 983 | 2269 (2.31x).
#
# ...and preferred is not the same as always chosen. Group 3 holds PER LAP, one row per clean lap
# and uncapped, so its height is a function of the RECORDING: at 65 laps (the owner's 0062) it is
# 2334 px against a 1894 px stack, and the form that spans it opens a 130 px band in the column
# beside it. `_span_fits` measures that per session and falls through to form #3 — which stacks
# both growing groups together and spans the one that does not grow, so it cannot band by
# construction. Form order is a preference; the fit is a measurement.
PAGE_LAYOUTS = (
    ((0,), (1,), (2,)),      # three columns
    ((0, 1), (2,)),          # two, the balanced pairing
    ((0,), (1, 2)),          # two, when the widest table cannot be given a column to itself
    ((0, 1, 2),),            # one — the quadrant page, and untouched by any of this
)
PAGE_COLS_MAX = max(len(layout) for layout in PAGE_LAYOUTS)
GG_GROUP = 1              # the section group the friction circle lives in (it sizes from ITS column)
GG_HEIGHT = 220           # px; the friction-circle plot's height in a normal pane
GG_HEIGHT_WIDE = 300      # …and in a dashboard-width one (it is the page's only chart)
# The plot's width is set EXPLICITLY (2:1 around the aspect-locked circle, leaving the axis
# labels their gutter) rather than left to pyqtgraph's sizeHint, which is devicePixelRatio-
# dependent: the identical 1440x900 logical window laid the plot out 440 px wide at DPR 1 and
# 300 px at DPR 2. A fixed logical width renders the same at both.
GG_ASPECT = 2.0
# …and the heights above are a CEILING, not the answer: 220 px at 2:1 is 440 px of pinned width,
# which is wider than this page's own pane at 1280x800 (445 px less the gutters) and 196 px wider
# than it at the app's 845x414 minimum. Pinned in both axes and wider than its pane, the friction
# circle was the last thing on the page still forcing a horizontal scroll once the report tables
# stopped. So _set_gg_size takes min(ceiling, what the pane can give), which keeps the plot square
# with its own axes, keeps both dimensions EXPLICIT (the DPR contract above), and lets the one
# chart on the page be the thing that yields — the section is never hidden, only sized.
GG_MIN_HEIGHT = 120       # below this the cloud stops being readable; the page h-scrolls instead
# The PACE trend sparkline's height (absorbed from the retired ConsistencyPanel — its content lives
# here now). A DERIVATION, not a picked 96, and the derivation is the same move theme.py makes for
# ICON_PX (= SPACE_L) and GRID_ROW_H (= CTRL_H): an emergent number becomes a declared one by being
# spelled as the step it already was, with the sentence it never had.
#
# WHY TWO STEPS OF SPACE_3XL. SPACE_3XL is what the scale spends when a surface is large enough to
# need room of its own, and this strip is two of them: measured on the shipped widget, the 96 is a
# 71.5 px DATA BAND plus a 23 px bottom axis plus the 4 px of border every QGraphicsView in this app
# reserves so a focus ring costs no re-layout (see the QGraphicsView rule in theme.py). The band is
# the part that has to be big enough — the y axis prints the fastest and slowest lap at its two ends
# and the curve has to show a slope between them — and one step under a bottom axis would leave it
# 24 px, two label rows, which is a line rather than a trend.
#
# It was the last genuine off-scale literal in `studio/` (tests/test_design_system.py listed it by
# name as the stats lane's, the one entry of seven that was not an extent excuse). The rendered
# height is unchanged: this is the same 96 px, said in the app's own units.
SPARK_HEIGHT = 2 * theme.SPACE_3XL
SPARK_AXIS_FONT = 10      # tabular tick font for the sparkline's min/max + first/last labels
SPARK_Y_PAD_FRAC = 0.12   # vertical headroom so extreme dots/labels aren't clipped
# The left axis's fixed width, so the curve doesn't jump across sessions — it holds an "m:ss.mmm"
# label. Named because the width cap below is measured from it.
SPARK_AXIS_W = 58
# A SAMPLE'S SHARE OF THE PLOT. The sparkline was the one widget on this page with no width of its
# own: every table caps itself at its content and the friction circle is pinned in both axes, so on
# the maximized dashboard 24 laps were stretched across 1850 px of chart — a slope drawn at 77 px
# per lap, which is not a trend line, it is a mountain range. A step, not a picked number: SPACE_XL
# is ~3x the 7 px PB dot, so consecutive PB laps read as two dots rather than a chain, and 24 laps
# then ask for 634 px — about the column this page now composes into.
SPARK_PX_PER_LAP = theme.SPACE_XL
# Tukey's upper fence — q3 + this x IQR — above which a lap is not the top of the session's range,
# it is an outlier. A FENCE and not a percentile because a fence is SELF-LIMITING: on a session
# with no traffic lap it sits above the slowest lap and nothing is clipped at all, which no fixed
# percentile can promise (p95 of 24 clean laps lands at 1:11.556 here — under the maximum, so it
# would clip the slowest lap of every session ever recorded, outlier or not).
#
# 3.0, WHICH IS TUKEY'S "FAR OUT", NOT HIS 1.5. Both were measured on D24's 24 clean laps
# (q1 1:08.822, q3 1:09.855, IQR 1.033): 1.5 fences at 1:11.405 and withholds TWO laps — including
# a 1:11.786 that is merely a slow lap — for a 2.025 s frame; 3.0 fences at 1:12.955 and withholds
# exactly the one 1:17.136 traffic lap for a 3.558 s frame, against 8.908 s unfenced. Anything
# outside the frame is data the reader is not being shown, so the multiplier that recovers 60 % of
# the vertical range while hiding ONE lap beats the one that recovers 77 % by hiding two.
SPARK_OUTLIER_IQR = 3.0
SPARK_MIN_FOR_FENCE = 6   # quartiles of fewer laps than this describe nothing; show them all
SPARK_TOOLTIP = ("Lap-time trend over the clean laps (GPS-dropout ⚠ laps excluded). "
                 "Highlighted dots mark session-best (PB) laps; the dashed line is the "
                 "session best (the floor). Y labels: fastest / slowest lap.")
# ...and what the y axis says when the frame is fenced, appended to the tooltip. The labels stop
# being "fastest / slowest lap" the moment a lap is left out of the frame, and a chart whose axis
# quietly stops meaning what its tooltip says is worse than an unreadable one.
SPARK_OUTLIER_TIP = ("\n\n{n} slower than the rest of the session and left out of the frame "
                     "(up to {slowest}) — marked at the top. The y labels are the fastest and "
                     "slowest lap IN the frame, so the other {kept} keep the full height.")
GG_DOT_ALPHA = 90         # scatter alpha (0-255): a cloud, not 4000 opaque dots
GG_RING_STEP = 0.5        # g; concentric reference rings every half g
# Every report table's row height. It was a bare 22, documented here as "the consistency-table
# convention" — a convention inherited from the ConsistencyPanel, which PR #111 DELETED, so the
# number outlived its only argument. Three of the five tables below are genuine row click targets
# (SelectRows + SingleSelection + ClickFocus → corner_clicked → the map's apex ring), and 35 of
# their rows therefore shipped two pixels under the pointer-target floor theme.py declares. This is
# that floor, spelled as the token: a report grid may be denser than a control, never denser than
# the floor. See theme.GRID_ROW_DENSE_H for why this is not a new density scale.
ROW_HEIGHT = theme.GRID_ROW_DENSE_H
# Speed units live in the PER-LAP section label (one place), keeping the columns narrow
# enough that the whole table fits the quadrant with no clipped column.
LAP_COLUMNS = ["Lap", "Time", "Vmax", "Avg", "Min", "Lat g", "Brk g", "Brake s", "Coast s"]
# "Med loss", not "Med loss vs best corner": MEASURED, not chosen. The header section is 100 px
# and the face needs 56 px for "Med loss", 104 px for "…vs best" and 148 px for "…vs best corner",
# while the header already asks for 800 px inside a 636 px viewport — so both longer labels elide
# to a "Med loss vs…" that names nothing. The baseline is named where there IS room: the caption
# under the table (`CORNERS_NOTE`) and the table tooltip.
CORNER_COLUMNS = ["Corner", "Best", "Median", "σ (s)", "Med loss", "Apex best", "Apex med",
                  "Grip %"]
WORST_TINT_N = 3          # the top-N inconsistency-score corners get the loss cell marked
# ...and MARKED, not merely tinted. The cue used to be hue and nothing else — tinted and plain
# cells were identical in size, weight, family, alignment and format, and carried the same tooltip
# — while the ranking is by σ × median-loss, a PRODUCT that is not a column on screen. So the
# column read as if it were ordered by its own numbers and was not: on D24 a tinted +0.09 (C11) sat
# directly under a plain +0.11 (C10), and a plain +0.10 (C7) beat the tinted +0.09. A reader with
# no colour, or with the colour and no explanation, was given a contradiction either way.
#
# THE MARK IS ▲, NOT ⚠, AND THAT IS THE WHOLE POINT. ⚠ is the app's DISTRUST glyph: the lap grid
# hangs it on a GPS-dropout lap, the DATA TRUST card on a caveated term, and both mean "this
# number may not be sound". These three cells mean the opposite — they are the corners with the
# most time available, the three the driver should practise FIRST. Marking the app's best news
# with its "don't trust this" glyph told a reader the loudest advertised gains were the flagged
# ones. ▲ is upside, points at the number, and costs nothing to adopt: the reason the old mark
# reused ⚠ was font coverage ("no new codepoint arrives"), and ▲ is in the same measured ledger
# (tests/test_glyph_vocabulary.py's _IN_THE_FACE, asserted against the SHIPPED face).
#
# It is a PREFIX, deliberately: this column is
# fixed-decimal and right-aligned, so right alignment IS decimal alignment (a property measured and
# kept), and a trailing mark would push three of twelve numbers out of the decimal column. Prefixed,
# it hangs to the left of an untouched right edge. The character stays TEXT rather than becoming a
# theme.icon() pixmap because Inter draws it (tests/test_glyph_vocabulary.py measures exactly that)
# and because a cell's icon slot paints at the cell's LEFT edge, a whole column away from the
# right-aligned number it would be marking.
WORST_LOSS_MARK = "▲ "
CORNERS_TOOLTIP = ("Corner-by-corner over the clean laps: session-best / median / σ "
                   "time-in-corner, the median loss VS THE BEST ANYONE DID IN THAT CORNER "
                   "(this column's own Best cell — not your best lap's corner, which is what the "
                   "Coaching page measures against and why its numbers are smaller), apex speeds "
                   "and median grip utilization. "
                   f"The worst 3 loss cells are marked {WORST_LOSS_MARK.strip()} and "
                   "tinted — ranked by σ × median-loss (erratic AND slow), which is why the marked "
                   "cells are not simply this column's three largest numbers; hover one for its "
                   "own score. That's where practice pays first. Click a row to ring "
                   "the corner's apex on the map; click a column header to sort.")
BRAKE_COLUMNS = ["Corner", "n", "Onset σ m", "Span m", "Commit %", "m later"]
STRAIGHT_COLUMNS = ["Straight", "Best", "Median", "σ (s)", "Trap best", "Trap med", "Exit Δ"]
RING_ROLE = NUM_ROLE + 1   # the map-ring corner cid stored on a straight row's label item
STRAIGHTS_TOOLTIP = ("Straight-by-straight over the clean laps (the corner/straight "
                     "partition — segments sum to the lap time exactly): best/median/σ "
                     "time, the trap speed at the straight's END, and Exit Δ — the "
                     "preceding corner's median exit speed vs your best lap's (+ is "
                     "faster). A slow exit ahead of a long straight is the costliest "
                     "mistake on track: the FIX FIRST tile ranks exit deficit × straight "
                     "time spread. Trap speed doubles as a gearing/engine-health proxy. "
                     "Click a row to ring the corner feeding that straight.")
BRAKING_TOOLTIP = ("Braking repeatability per corner, over the clean laps: the cross-lap "
                   "scatter of your brake-onset POINT (σ and max−min span, metres, compared "
                   "in the reference lap's odometer) plus commitment — the median event's "
                   "peak decel as a % of the session's demonstrated maximum — and the "
                   "ESTIMATED median metres you could brake later (the D4 brake-point "
                   "model). Corners with no matched brake event are omitted. Honesty floor: "
                   "10 Hz GPS quantizes the onset by ~1.5 m — a σ at or below that is "
                   "measurement, not driving. Click a row to ring the corner on the map.")
# The pace-trend verdict band moved to `stats.TREND_STEADY_BAND`, beside the statistic it
# qualifies: the exported report and the clipboard summary print the same verdict off the same
# slope, and export_data is Qt-free by contract so it cannot reach into this module.
SECTOR_COLUMNS = ["Sector", "Best", "Median", "σ (s)"]

GG_TOOLTIP = ("The friction circle: every g-meter sample on the valid laps — lateral g across, "
              "longitudinal g up (accelerating) / down (braking). A driver using the tyre "
              "fills the rim of the circle; rings every 0.5 g. Longitudinal is the validated "
              "GPS-derived signal (the IMU forward axis is vibration-inflated), smoothed over "
              f"{gmeter.LONG_SMOOTH_S:g} s — so the cloud's top and bottom are SUSTAINED "
              "braking and acceleration, not the instantaneous spikes a raw derivative shows.")
# The plot ships two kinds of ring and no way to tell them apart from the picture: the solid ones
# are a fixed 0.5 g rule, the dashed one is a MEASURED result. Both axes now carry a name and a
# unit too (they read "-2.0 / +0.0 / +2.0" and nothing else before).
# STACKED, and that is a measurement rather than a style choice. pyqtgraph rotates a left-axis
# title, so its LENGTH is consumed vertically: set on one line, "longitudinal g  (− braking · +
# accelerating)" is a 304 px box inside an axis 173 px tall in the quadrant (253 maximized), and
# 88 px of it — both ends, including the word "accelerating" — was painted outside the plot at
# every size the page has ever shipped. A <br> costs thickness, which this axis has to spare, and
# buys length, which it does not: the longest line is now ~95 px against 173. The x title is
# stacked to match, so the two axes read as one pair rather than one wrapped and one not.
GG_AXIS_X = "lateral g<br>− right · + left"
GG_AXIS_Y = "longitudinal g<br>− braking<br>+ accelerating"
GG_KEY_RINGS = "solid rings: 0.5 g steps"
LAP_TABLE_TOOLTIP = ("Per-lap statistics over the valid laps. Vmax/Avg from the lap's own GPS "
                     "speed; peak g from the g-meter (lateral IMU, longitudinal GPS-derived); "
                     "Brake/Coast are the summed detected events — the same events the map "
                     "glyphs and coaching read. ★ marks the session-best lap.")
PACE_TOOLTIP = ("Lap-time distribution over the clean laps (valid, no GPS dropout — the same "
                "set every σ statistic uses). Spread = median − best: what the typical lap "
                "gives away to your demonstrated pace.")
# The two stitched TARGETS (moved here from the Laps tab's SESSION-BESTS footer, which cost the
# lap grid 63px — two lap rows — on every recording). Each now sits with the data it is derived
# from: the rolling best beside the other lap-time paces, the theoretical best in the IDEAL LAP
# block beside the gap it explains (it was inside SECTORS when this line was written),
# whose per-sector bests it literally sums.
ROLLING_TOOLTIP = ("Best rolling — the fastest single complete loop regardless of where it "
                   "starts: the minimum time from passing any track position to passing it "
                   "again one lap later (windows spanning a GPS-dropout ⚠ lap are excluded). "
                   "A reference target, not a lap you drove.")
# Every clause of the old copy — "the sum of the session-best sector splits", "the purple cells on
# the Laps tab", "shown only with sector lines", "without them it degenerates to the best lap time"
# — described a number that no longer exists. Sector lines do not touch the ideal any more (driven
# through set_timing_lines at 0/1/2/3 lines, ideal_total is byte-identical), and the ideal is
# strictly faster than the best lap whenever two laps donate. The one clause that was true and
# load-bearing is kept verbatim: A REFERENCE TARGET, NOT A LAP YOU DROVE.
#
# ...and BOTH ideal tooltips now close with the same paragraph, because the thing they were both
# missing is the same fact about both numbers: they are ORDER STATISTICS. The line under the tiles
# states the sample; this states why the sample is part of the answer, which is the half a caption
# cannot carry — the brief for this disclosure was "the number where a reader sees it, the
# mechanism on hover".
#
# Every figure is measured, not asserted: `ideal_total` over random subsets of each recording's
# clean laps (200 draws per N) falls 0.068 / 0.174 / 0.194 / 0.241 / 0.384 s per doubling of lap
# count on the owner's five, and on D24's three chapters the decrement GROWS with N (0.31 s over
# 8→15 laps, 0.42 s over 30→65) rather than shrinking — nothing is being approached. Over six
# start-line positions per recording the detected corner count moves 11↔12 on D24 and 7↔8 on
# Sandown, and the headline gap by up to +69 %. The full table and its sources are in
# corner_model.IdealSample; this is the version a reader gets on hover.
IDEAL_SAMPLE_TOOLTIP = (
    "\n\nIt is a MINIMUM over the clean laps counted under the tiles, so it is partly a measure "
    "of how many laps you recorded: measured on real recordings it falls 0.07–0.38 s per doubling "
    "of lap count and keeps falling — there is no floor it settles on. It also moves when the "
    "corners are re-detected, which happens every time you drag the start/finish line. Compare it "
    "with another session only when the two have a similar lap count and corner count.")
THEORETICAL_TOOLTIP = ("Theoretical best — your quickest time through each corner and each "
                       "straight, stitched into one lap. A reference target, not a lap you "
                       "drove: no single lap was this fast all the way round, but every piece "
                       "of it is a piece you drove. The table below says where it lives."
                       + IDEAL_SAMPLE_TOOLTIP)
IDEAL_GAP_TOOLTIP = ("What the theoretical best says is still on the table: your best lap minus "
                     "the stitched ideal. It is time you have already demonstrated, one segment "
                     "at a time, on laps you drove — not a simulation and not a lap record."
                     + IDEAL_SAMPLE_TOOLTIP)
# "As fast as this lap", not "Laps as fast" (§5.6). The count is laps that drove the segment at
# least as fast as THE SUBJECT LAP — the tooltip always said so, the header did not, and a reader
# comparing "20" against a gain naturally reads it as "20 laps already matched the ideal, so why is
# this a gain?". Measured before changing it: this table's columns are ResizeToContents, so the
# longer header widens its own column (96 -> 131 px) and the table still fits its pane (367 px of
# header in a 369 px viewport).
IDEAL_COLUMNS = ["Segment", "Gain (s)", "As fast as this lap", "Best on lap"]
# A row under this is not advice — it is a rounding difference between two laps of the same
# corner, and a plan is not 25 rows long. The remainder is never hidden: the note under the table
# states how many segments were left out and what they are worth, so the rows on screen and the
# tile above them still add up (see _refresh_ideal).
IDEAL_GAIN_FLOOR = 0.05
IDEAL_TOOLTIP = (
    "Where the theoretical best lives: per segment of the corner/straight partition, the time "
    "your BEST lap gives away against your quickest time through it, the lap that set that "
    "quickest time, and how many of your clean laps drove that segment at least as fast as your "
    "best lap did. The gains over EVERY segment sum exactly to the gap under the tile above.\n\n"
    "Ranked by gain × the share of laps that already matched it — so a smaller gain you make "
    "routinely sits above a bigger one you made once. Both factors are columns, so you can check "
    "the order by eye. Click a row to ring that corner on the map.")


# `_plural` and `_fmt_hms` used to live here. Both moved to `_signal` (plural / fmt_hms) when the
# exported session report became a second caller: export_data is Qt-free by contract and cannot
# import this module, and a second copy of "1 corner"/"7 corners" or of the h:mm:ss rounding is
# exactly how the exported page starts disagreeing with the page it was exported from.


# --------------------------------------------------------------- pyqtgraph pen accessors
# CALL-TIME, never module constants: a pyqtgraph pen width is in DEVICE pixels (theme.line_width),
# so a pen built once at import freezes whatever device-pixel ratio happened to be current then and
# draws half weight on a Retina panel. Same contract, and the same accessor shape, as plots_view.
def _axis_pen():
    """The pen for an axis line — and therefore for the GRIDLINES, since pyqtgraph's AxisItem falls
    back from tickPen() to pen(). Handed a bare colour it would build this pen itself at width 1,
    i.e. one DEVICE pixel; that is the half-weight grid W5-01 measured."""
    return pg.mkPen(C.border, width=theme.line_width(1))


def _spark_curve_pen():
    return pg.mkPen(C.text_dim, width=theme.line_width(1))


def _spark_baseline_pen(colour):
    return pg.mkPen(colour, width=theme.line_width(1), style=Qt.DashLine)


def _glyph_outline_pen():
    """The canvas-coloured outline that keeps overlapping scatter dots readable as separate dots."""
    return pg.mkPen(C.canvas, width=theme.line_width(1))


def _gg_ring_pen():
    return pg.mkPen(C.border, width=theme.line_width(1))


def _gg_envelope_pen():
    return pg.mkPen(C.accent, width=theme.line_width(1), style=Qt.DashLine)


def _repen(item, logical_px: float = 1.0):
    """Re-issue `item`'s pen at the CURRENT device-pixel ratio, keeping its colour and dash style.

    For the items whose colour is decided per refresh (the spark baseline takes the palette's
    best-lap hue, the g-g rings are rebuilt around the measured cloud): a DPR change must not wait
    for the next refresh, and re-deriving the colour here would duplicate that logic."""
    pen = item.opts.get("pen") if hasattr(item, "opts") else getattr(item, "pen", None)
    if pen is None:
        return
    item.setPen(pg.mkPen(pen.color(), width=theme.line_width(logical_px), style=pen.style()))


class _TrustCard(QWidget):
    """DATA TRUST as a list of FACTS — one labelled row each — instead of a paragraph.

    WHAT IT REPLACES, and why the shape had to change. The card shipped as a single word-wrapping
    QLabel holding up to seven `·`-separated sentences joined by newlines. Three things were wrong
    with that, and only one of them was the clipping:

      * it CLIPPED. The label wrapped at the scroll BODY's width — which the content-sized report
        tables had pushed to 742 px inside a 503 px quadrant — so the longest line ran 61 px past
        the right edge of the viewport and stopped mid-number ("…longitudinal r=+0.82 · 3468").
        Measured at 1280x800 it was 119 px. Nothing about the label was wrong; it was being asked
        to lay out at a width nobody could see.
      * it read as PROSE in a page made of tiles. Every other group on this page is a value with a
        name under it; the densest, most technical block on the surface was the one thing with no
        structure at all, and the `·` separators made a fact list look like a sentence.
      * it could not be SCANNED. "Is the timing verified? what is the g source?" are lookups, and a
        lookup wants a column of terms, not three lines of running text.

    So each fact is a ROW: a dim CAPTION term on the left, its value on the right. That is the
    tile's own type pair — the dim name and the value it names — turned through ninety degrees,
    which is what makes it survive a ~500 px quadrant where a tile grid of seven captions would not.
    The value WRAPS (WrapLabel, so the layout is actually told the height it needs), so no fact can
    ever be cut again however long it gets.

    THE CAVEATS LEAD. The trust-BREAKING facts — an unconfirmed start line, an unknown track, laps
    left out of every statistic, in-lap GPS dropouts — appear only when they apply, and appear
    FIRST, marked `⚠`, so the card cannot read the same on a session where three of them are wrong
    and one where none are. That ordering was already the shipped behaviour; a row of its own and a
    marked term is what makes it visible at a glance rather than on a careful read.

    WHY THE CAVEATS ARE NOT PAINTED AMBER. The app's amber call-to-action treatment
    (`#ProvisionalBanner`) is already on this page, as its own strip, ~100 px above this card and
    stating the first of these caveats in the same words. A second amber block for the same fact is
    noise rather than emphasis, and the other three caveats have no single action to offer. So the
    alarm here is carried by MARKING and by ORDER; the amber is spent once, where the action is.

    `text()` is the whole card as one string, and it is not a test affordance: a composite widget
    announces as nothing to assistive tech, so it is also the card's accessible description.
    Deliberately `f"{term}: {value}"` per row — the same sentences the paragraph printed, so this
    change is provably presentational."""

    #: What marks a caveat row's term. The glyph the Laps tab already uses for a flagged lap.
    CAVEAT_MARK = "⚠ "

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple[str, str, bool]] = []
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(theme.SPACE_M)
        self._grid.setVerticalSpacing(theme.SPACE_XS)
        # The TERM column takes exactly what its longest term needs; the VALUE column takes
        # everything else and wraps inside it. A stretch on the value column (and none on the term)
        # is what stops a long value from widening the card past its pane — the defect that put the
        # old paragraph 61 px off-screen.
        self._grid.setColumnStretch(0, 0)
        self._grid.setColumnStretch(1, 1)
        self._widgets: list[tuple[QLabel, WrapLabel]] = []

    def rows(self) -> list[tuple[str, str, bool]]:
        """The facts currently shown, as (term, value, is_caveat)."""
        return list(self._rows)

    def text(self) -> str:
        """The card as text: one "term: value" line per fact (also its accessible description)."""
        return "\n".join(f"{term}: {value}" for term, value, _caveat in self._rows)

    def set_rows(self, rows) -> None:
        """Re-render the card from (term, value, is_caveat) triples.

        Widgets are REUSED and only the surplus is hidden, rather than deleted and rebuilt: this
        runs on every refresh() — a unit flip, a palette flip, a re-segmentation — and tearing down
        QLabels inside a live QGridLayout on every one of those is the same re-entrancy the tile
        reflow had to be taught to avoid (see _place_tiles)."""
        self._rows = [(str(t), str(v), bool(c)) for t, v, c in rows]
        while len(self._widgets) < len(self._rows):
            term = QLabel()
            term.setFont(theme.ui_font(theme.CAPTION))
            term.setProperty("role", "Note")
            # Top-aligned: a one-word term must sit level with the FIRST line of a value that
            # wraps to three, not float in the middle of it.
            term.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            value = WrapLabel()
            value.setProperty("role", "Note")
            value.setFont(theme.ui_font(theme.CAPTION))
            r = len(self._widgets)
            self._grid.addWidget(term, r, 0)
            self._grid.addWidget(value, r, 1)
            self._widgets.append((term, value))
        for i, (term_w, value_w) in enumerate(self._widgets):
            if i >= len(self._rows):
                term_w.setVisible(False)
                value_w.setVisible(False)
                continue
            term, value, caveat = self._rows[i]
            term_w.setText(f"{self.CAVEAT_MARK}{term}" if caveat else term)
            value_w.setText(value)
            term_w.setVisible(True)
            value_w.setVisible(True)
        self.setAccessibleDescription(self.text())


class _ReportTable(QTableWidget):
    """A content-sized statistics table that SCROLLS ITSELF when the pane is too narrow for it.

    THE PAGE'S HORIZONTAL SCROLLBAR WAS THIS WIDGET. Each report table pinned itself to the exact
    width of its own columns (`_fit_table`), and the widest of them — PER LAP, nine columns — asks
    for 730 px. In the 503 px quadrant that is the app's default the table's fixed width became the
    scroll body's minimum, so the WHOLE page was laid out 742 px wide and then scrolled sideways
    inside a 503 px viewport: every section heading, every tile row and the DATA TRUST card were
    being wrapped at a width 239 px larger than anything the reader could see. The one widget that
    genuinely did not fit made the eight that did fit stop fitting.

    The honesty rule that put the scrollbar there in the first place still holds — a statistics
    table must never silently clip its rightmost column — so the scrolling is not removed, it is
    MOVED to the widget that actually overflows. The table takes `min(pane, its content)`: at
    dashboard width it is exactly as wide as its columns and reads left-packed as before; in a
    quadrant it takes the pane and grows its own horizontal scrollbar. The page never scrolls
    sideways again, and no column is ever hidden without a bar saying so.

    The HEIGHT has to follow, which is why this is a class and not two more lines in `_fit_table`:
    the outer column owns vertical scrolling, so each table is pinned to its content height — and
    the moment an in-table scrollbar appears it would eat the last row out of that pinned height.
    `_apply_height` re-pays for the bar when it is showing and takes the pixels back when it is
    not, on every resize."""

    def __init__(self, columns: list[str], row_height: int):
        super().__init__(0, len(columns))
        self._row_height = row_height
        self._content_w = 0
        self.setHorizontalHeaderLabels(columns)
        # ...and then give every header the SIDE of the column it labels. Qt's
        # QHeaderView.defaultAlignment is AlignCenter, these five tables never overrode it, and
        # every cell from NUMERIC_COL_START on is AlignRight — so each label floated over the
        # middle of a column whose digits sit at its right edge, by up to 34 px of ink-centre drift
        # on the widest column (BRAKING "Commit %", 108 px, measured on the window composite at
        # 1440x900). The rule is the app's, already written down for the lap / corner / coaching
        # grids; these tables were simply never brought to it, and the guard that exists for
        # exactly this defect (tests/test_design_system.py::test_no_table_header_floats_off_its_data)
        # enumerated four tables and not these five.
        #
        # Applied HERE, in the shared table, rather than at the five call sites, because unlike the
        # lap and corner grids all five build their headers the same way — through this one
        # constructor — so a SIXTH report table cannot arrive without it. The boundary is the same
        # NUMERIC_COL_START the cells use (column 0 is the row's identity: "C7" / "S2" / a lap
        # number, left; everything after it is a number, right), which is what stops a new column
        # arriving with its header and its values disagreeing.
        align_headers_over_their_columns(self, NUMERIC_COL_START)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(row_height)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setAlternatingRowColors(True)
        self.setFocusPolicy(Qt.NoFocus)
        # Vertical scrolling belongs to the outer page (each table is pinned to its content
        # height); horizontal scrolling belongs HERE, and only when the pane is too narrow.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        # Preferred (not Fixed) horizontally: the table may shrink to the pane. Its MAXIMUM is its
        # content width, so a wide pane never stretches it — the left-packed reading is unchanged.
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        # ...and its layout MINIMUM must not be its content: a QTableWidget's minimumSizeHint is
        # generous enough to re-create the very overflow this class exists to remove.
        self.setMinimumWidth(0)

    def fit(self) -> None:
        """Re-measure after a refill: columns to their content, width capped there, height pinned."""
        self.resizeColumnsToContents()
        self._content_w = (sum(self.columnWidth(c) for c in range(self.columnCount()))
                           + 2 * self.frameWidth() + 2)
        self.setMaximumWidth(self._content_w)
        self._apply_height()

    def content_width(self) -> int:
        """The width at which this table shows every column — what it would LIKE to be.

        Its layout minimum is deliberately 0 (below) and its maximum is this, so between the two it
        takes whatever the pane gives and scrolls the difference. That is right for a quadrant and
        wrong for a page CHOOSING its own columns: the chooser has to know the number before it
        commits, or it composes a column that hides three of CORNERS' eight columns. `fit()` has
        always computed it; this is the read the page's packer needs (see _group_min_width)."""
        return self._content_w

    def minimumSizeHint(self):
        """Zero-width, full-height. Qt's own hint for a scroll area is wide enough to reserve room
        for content that this table is explicitly willing to scroll instead."""
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint

    def _needs_bar(self) -> bool:
        return self.viewport().width() < self._content_w - 2 * self.frameWidth() - 2

    def _apply_height(self) -> None:
        h = (self.horizontalHeader().height() + self._row_height * self.rowCount()
             + 2 * self.frameWidth())
        if self._needs_bar():
            h += self.horizontalScrollBar().sizeHint().height()
        if h != self.height() or self.minimumHeight() != h:
            self.setFixedHeight(h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_height()


class StatsView(QWidget):
    """The Stats page (see the module docstring). Contract: refresh() on load/re-segment,
    refresh_palette() after a palette flip, set_speed_unit() from the View ▸ Units toggle."""

    # Clicked CORNERS-table row's cid (None on deselect) -> the map apex ring, via the
    # maximize-aware CentralView handler (restore the grid first, then ring).
    corner_clicked = Signal(object)

    def __init__(self, session: Session):
        super().__init__()
        # Before ANY pen is built: pyqtgraph pen widths are in device pixels, so point theme at
        # this widget's device-pixel ratio first (see theme.line_width and `event` below). The
        # same first line PlotsView and MapView carry — this page has its own charts.
        theme.set_pen_scale(self.devicePixelRatioF())
        self.session = session
        self._speed_unit = getattr(self, "_speed_unit", units.DEFAULT_UNIT)
        # C6 responsive tiles: every _grid registers here; _reflow_tiles re-places them when
        # the pane crosses a column threshold. Built at max columns, reflowed on first resize.
        self._tile_grids: list = []
        self._tile_cols = TILES_PER_ROW      # the WIDEST section column's tile columns
        self._tile_cols_by_group = {}        # ...and each column's own, which is what is applied
        self._group = 0             # the section group being built (see the COLUMN 2/3 markers)
        self._wide = False          # composed into columns? (a dashboard; drives the g-g size)
        self._layout = PAGE_LAYOUTS[-1]      # the composition in force; the single column to start
        self._column_tables: list[list] = [[] for _ in range(PAGE_COLS_MAX)]
        self._scroll = None

        body = QWidget()
        page = QVBoxLayout(body)
        # On the scale, and on it deliberately: SPACE_M of gutter, SPACE_S under the panel chrome,
        # and SPACE_XS between blocks — the 6 px that used to sit here was the page's only
        # off-scale gap, and it is the rhythm every section heading and tile row is measured from.
        # The GROUP separation is paid for by the tile grids' own bottom margin (see _grid), so a
        # tighter step here tightens the rows without letting the sections run together.
        page.setContentsMargins(theme.SPACE_M, theme.SPACE_S, theme.SPACE_M, theme.SPACE_M)
        page.setSpacing(theme.SPACE_XS)

        # --- the page's own trust banner + empty state, above everything they qualify.
        # These stay FULL WIDTH at every column count: they qualify the whole page, and a caveat
        # dealt into one column of three is a caveat about that column.
        self.provisional_banner = QLabel(PROVISIONAL_BANNER)
        # The map trust strip's amber call-to-action style, by object name — one QSS rule, so the
        # two surfaces can never drift apart visually.
        self.provisional_banner.setObjectName("ProvisionalBanner")
        self.provisional_banner.setWordWrap(True)
        self.provisional_banner.setToolTip(PROVISIONAL_TOOLTIP)
        self.provisional_banner.setVisible(False)
        page.addWidget(self.provisional_banner)
        # NOT AN EMPTY STATE — a BANNER, and that distinction is the whole of QA D2-06. It wore
        # `role="EmptyState"`, the same role five centred, card-backed placeholders wore, while
        # this one rendered LEFT-aligned; the reason it did is that the page BELOW it keeps
        # rendering (SESSION totals and the DATA TRUST card are real with or without a lap), so it
        # qualifies content rather than replacing it. One role producing two presentations is not a
        # role. It takes the amber call-to-action treatment its own sibling two lines up already
        # owns — the two are mutually exclusive by construction (see refresh: the provisional
        # banner needs `valid`, this needs `not valid`), so they are one banner slot with two
        # messages, never a stack of two amber strips.
        # A BANNER IS ONE LINE, and this one now carries one line: what happened, and what it
        # costs on THIS page. See NO_LAPS_BANNER / NO_LAPS_PROSE for the measured reason the
        # paragraph moved out from under an 11 px semibold call-to-action strip.
        self.no_laps_note = QLabel(NO_LAPS_BANNER)
        self.no_laps_note.setObjectName("ProvisionalBanner")
        self.no_laps_note.setWordWrap(True)
        self.no_laps_note.setVisible(False)
        page.addWidget(self.no_laps_note)
        # The WHY and BOTH ways out, in the app's prose step (`role="EmptyBody"` — BODY/13, the
        # exact rule its four siblings' bodies wear) at the app's prose measure
        # (theme.EMPTY_MEASURE_PX, "the widest a column of PROSE may be set"). No new token, and
        # the same 62 characters per line the ported states set.
        #
        # THE MEASURE IS THE LABEL'S MAXIMUM AND THE ITEM IS NOT ALIGNMENT-FLAGGED, which is the
        # whole of how it avoids widgets.EmptyState's ratchet: an un-aligned QWidgetItem takes
        # `min(cell, maximumWidth)`, so the label is handed the pane up to the cap and its
        # MINIMUM stays its own (the longest word). An AlignLeft flag here would hand it
        # `sizeHint` instead — QLabel's roughly-square wrap heuristic — and a `setFixedWidth`
        # would pin the page's minimum to the measure.
        self.no_laps_prose = WrapLabel(NO_LAPS_PROSE)
        self.no_laps_prose.setProperty("role", "EmptyBody")
        self.no_laps_prose.setMaximumWidth(theme.EMPTY_MEASURE_PX)
        self.no_laps_prose.setVisible(False)
        # MOUNT, THEN FILL (§3.9). Safe as written — the child is fresh and unparented — but
        # `QLayout::addWidget` on a FREE-STANDING layout does half a move: it drops the widget from
        # its old layout and cannot reparent it, because the layout it is being added to has no
        # widget yet. That is the #200 SIGSEGV, and it costs two lines in the right order to make
        # the shape safe for the first caller who hands over a mounted widget.
        self._no_laps_prose_row = QHBoxLayout()
        page.addLayout(self._no_laps_prose_row)
        self._no_laps_prose_row.addWidget(self.no_laps_prose)
        self._show_no_laps_prose(False)

        # --- THE SECTION COLUMNS. Three widgets, each an ordinary vertical column of the sections
        # below; ONE grid deals them into 1, 2 or 3 places (_place_columns) as the pane allows.
        #
        # THREE COLUMNS AND NOT SIX, AND THE GROUPS ARE CONTIGUOUS, because the single-column page
        # is not a fallback — it is what every quadrant renders, and it must come out in exactly
        # the order it always has. Stacked, these three concatenate back into the shipped page,
        # section for section; any non-contiguous grouping (say "the two charts on the right")
        # would silently re-order the quadrant to buy a dashboard.
        #
        # No widget ever moves between layouts. A column keeps its sections for the life of the
        # page and only its CELL changes, which is why the grid can be re-dealt on a resize at all
        # — see _place_tiles for what re-adding a widget to a live layout costs.
        self._column_grid = QGridLayout()
        self._column_grid.setContentsMargins(0, 0, 0, 0)
        self._column_grid.setHorizontalSpacing(PAGE_COL_GAP)
        # Vertically the columns keep the page's own block rhythm: stacked, the seam between two
        # of them has to be indistinguishable from the seam between two sections.
        self._column_grid.setVerticalSpacing(theme.SPACE_XS)
        page.addLayout(self._column_grid)
        # The page's slack lives HERE, not in the grid. With it, the grid takes its own height and
        # each row is its column's; without it the rows would share out every spare pixel of a
        # short page and open gaps between the stacked columns.
        page.addStretch(1)
        self._columns: list[QWidget] = []
        for _ in range(PAGE_COLS_MAX):
            holder = QWidget()
            lay = QVBoxLayout(holder)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(theme.SPACE_XS)
            self._columns.append(holder)
        self._place_columns(self._layout)
        col, col_mid, col_right = (c.layout() for c in self._columns)

        # --- SESSION totals
        col.addWidget(self._section("SESSION"))
        self.t_laps = Tile("laps")
        self.t_laps.setToolTip(f"Valid laps · {EXCLUDED_MARK} band-excluded · "
                               f"{DROPOUT_MARK} laps with a GPS dropout")
        self.t_duration = Tile("recorded")
        self.t_moving = Tile("moving")
        self.t_distance = Tile("distance")
        self.t_clock = Tile("on track")
        col.addLayout(self._grid(self.t_laps, self.t_duration, self.t_moving,
                                 self.t_distance, self.t_clock))

        # --- DATA TRUST (the start-line/track/exclusion caveats + the timing-quality,
        # g-provenance and IMU↔GPS cross-check card). It sits SECOND, right under the session
        # totals: at the foot of the page it was ~1200px down — below the fold of even a
        # 1728x1117 maximized dashboard — so the caveats that say how much every number below is
        # worth were only reachable by scrolling past all of them.
        col.addWidget(self._section("DATA TRUST"))
        self.trust_card = _TrustCard()
        col.addWidget(self.trust_card)

        # --- PACE distribution
        self._pace_section = self._section("PACE")
        self._pace_section.setToolTip(PACE_TOOLTIP)
        col.addWidget(self._pace_section)
        self.t_best = Tile("best lap")
        self.t_median = Tile("median lap")
        self.t_race_pace = Tile("race pace · best 3-lap run")
        self.t_race_pace.setToolTip(
            "The best average of 3 CONSECUTIVE clean laps — your sustained pace, next to "
            "the single glory lap.")
        self.t_rolling = Tile("best rolling")
        self.t_rolling.setToolTip(ROLLING_TOOLTIP)
        # IA-04: the caption names the BASE. This tile is the median lap rebased, so it routinely
        # reads slower than the "best lap" tile two cells away — uncaptioned that looks like a
        # target you have already beaten. L4-08: no "→" — the tile is not clickable (the Coaching
        # tab is a tab away, and a painted arrow that does nothing is a broken affordance); the
        # tooltip points there in words instead.
        self.t_digest = Tile(f"median lap · top {PANEL_TOP_N} fixed")
        self.t_sigma = Tile("σ lap")
        self.t_spread = Tile("median − best")
        self.t_cov = Tile("consistency · σ/median")
        self.t_cov.setToolTip(
            "Coefficient of variation: sample σ of the clean lap times over the median, as "
            "a percent. Scale-free, so it is comparable across tracks — lower is steadier.")
        self.t_within = Tile("within 1% of best")
        self.t_trend = Tile("trend")
        self.t_trend.setToolTip(
            "Robust lap-time trend over the session (Theil–Sen median slope — one traffic "
            "lap can't fake it). Negative = getting faster. Shown from 6 clean laps up.")
        col.addLayout(self._grid(self.t_best, self.t_median, self.t_race_pace, self.t_rolling,
                                 self.t_digest, self.t_sigma, self.t_spread, self.t_cov,
                                 self.t_within, self.t_trend))
        # The lap-time trend sparkline (PB dots + session-best baseline) — absorbed from the
        # retired ConsistencyPanel strip; hidden with <2 clean laps.
        self.spark = pg.PlotWidget()
        self.spark.setToolTip(SPARK_TOOLTIP)
        spark_plot = self.spark.getPlotItem()
        for side in ("left", "bottom"):
            ax = spark_plot.getAxis(side)
            ax.setPen(_axis_pen())
            ax.setTextPen(C.text_dim)
            ax.setTickFont(theme.mono_font(SPARK_AXIS_FONT))
            ax.setStyle(maxTickLevel=0, tickLength=3)
        # Fixed left-axis width for an "m:ss.mmm" label so the curve doesn't jump across sessions.
        spark_plot.getAxis("left").setWidth(SPARK_AXIS_W)
        spark_plot.setMouseEnabled(x=False, y=False)
        spark_plot.setMenuEnabled(False)
        spark_plot.hideButtons()
        self.spark.setBackground(None)
        self.spark.setFixedHeight(SPARK_HEIGHT)
        self._spark_baseline = pg.InfiniteLine(angle=0, movable=False)
        spark_plot.addItem(self._spark_baseline)
        self._spark_curve = self.spark.plot([], [], pen=_spark_curve_pen())
        self._spark_curve.setDownsampling(auto=True)
        self._spark_curve.setClipToView(True)
        self._spark_dots = pg.ScatterPlotItem(size=4, pen=None,
                                              brush=pg.mkBrush(C.text_muted), pxMode=True)
        self._spark_pb_dots = pg.ScatterPlotItem(size=7, pen=_glyph_outline_pen(), pxMode=True)
        # ...and the laps the frame CANNOT hold: an up-triangle pinned at the top of the band at
        # each fenced lap's x (see SPARK_OUTLIER_IQR). The standard off-scale mark — the curve
        # visibly leaves the chart there, and this says the leaving is the chart's decision rather
        # than a gap in the data.
        self._spark_over_dots = pg.ScatterPlotItem(size=7, symbol="t1", pen=None,
                                                   brush=pg.mkBrush(C.text_muted), pxMode=True)
        spark_plot.addItem(self._spark_dots)
        spark_plot.addItem(self._spark_pb_dots)
        spark_plot.addItem(self._spark_over_dots)
        col.addWidget(self.spark)

        # --- the IDEAL LAP, and where it lives
        #
        # IT SITS HERE, DIRECTLY UNDER PACE, because the two numbers it must not contradict are
        # in the row above it: "best lap" (which it is derived from) and "median lap · top 3
        # fixed" (the OTHER synthesized target on this page). Measured on all five of the owner's
        # recordings the three are strictly ordered — ideal < best < projected — because they
        # answer different questions from different anchors, and the tooltips now say which is
        # which rather than leaving a reader to guess why one target reads slower than the other.
        #
        # It also moved OUT of the SECTORS group, where the tile used to inherit that section's
        # 0-sector hide. The ideal is no longer a sum of sector splits, so that gate was hiding
        # the number on precisely the recordings it is now worth showing on: sector_count() == 0
        # on ALL FIVE (D24 1ch and 3ch, Sandown 1ch and 3ch, SD_30_08), so the corrected ideal —
        # 0.22 to 1.64 s under the best lap — was invisible everywhere it had been fixed.
        self._ideal_section = self._section("IDEAL LAP")
        col.addWidget(self._ideal_section)
        self.t_theoretical = Tile("theoretical best")
        self.t_theoretical.setToolTip(THEORETICAL_TOOLTIP)
        self.t_ideal_gap = Tile("on the table · vs your best")
        self.t_ideal_gap.setToolTip(IDEAL_GAP_TOOLTIP)
        col.addLayout(self._grid(self.t_theoretical, self.t_ideal_gap))
        # WHAT THE TWO TILES ABOVE WERE MINIMISED OVER — the line this block was missing.
        #
        # Both numbers are order statistics: `-1.64 s from 65 laps` and `-0.84 s from 5 laps` are
        # the SAME DRIVING on D24's three chapters, measured over random subsets. Without the
        # counts a reader has no way to know that, and the app was inviting exactly that mistake —
        # the Library's Ideal-lap column holds two rows 0.56 s apart for no reason but lap count.
        #
        # IT SITS BETWEEN THE TILES AND THE TABLE, not in a tooltip and not only in the remainder
        # note under the table: the tiles are the surface a reader takes the number off, and the
        # note is ~10 rows further down a page that is already ~1400 px tall here. The remainder
        # note keeps the arithmetic ("these N segments hold X of the Y"); this keeps the sample, so
        # neither says the other's sentence twice.
        #
        # A WrapLabel at the app's prose measure, same construction as `ideal_note` — a paragraph
        # the column has to make room for, capped so a maximized 1728 px dashboard does not set it
        # to 130 characters a line, and NOT a longer tile caption: at 1280x800 the Stats body's
        # minimum width is 444 px against a 445 px viewport, so a caption that grows its grid
        # column by a single pixel puts a horizontal scrollbar on the page.
        self.ideal_sample = WrapLabel("")
        self.ideal_sample.setProperty("role", "Note")
        self.ideal_sample.setFont(theme.ui_font(theme.CAPTION))
        self.ideal_sample.setMaximumWidth(theme.EMPTY_MEASURE_PX)
        col.addWidget(self.ideal_sample)
        self.ideal_table = self._make_table(IDEAL_COLUMNS)
        self.ideal_table.setToolTip(IDEAL_TOOLTIP)
        # Interactive like CORNERS / BRAKING / STRAIGHTS — a row of a table headed "where it
        # lives" that cannot show you where is half an answer. Same corner_clicked pathway
        # (maximize-aware in CentralView); a STRAIGHT row rings the corner FEEDING it, which is
        # the rule the STRAIGHTS table already follows.
        #
        # NOT SORTABLE, and that is the one place it departs from those three. The row order IS
        # the content here — it is the ranking the tooltip explains — and a header click would
        # silently replace an argued order with an arbitrary one while the tooltip kept claiming
        # the first.
        self.ideal_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.ideal_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.ideal_table.setFocusPolicy(Qt.ClickFocus)
        self.ideal_table.itemSelectionChanged.connect(self._on_ideal_row_selected)
        col.addWidget(self.ideal_table)
        # The arithmetic the table cannot show: a top-N list under a total is a contradiction
        # unless the remainder is named. Filled per refresh, hidden with the section.
        #
        # A WrapLabel (not QLabel + setWordWrap) because it is a PARAGRAPH the column has to make
        # room for — the trap widgets.WrapLabel exists for, and the reason three other surfaces
        # painted their second line outside their own box. Capped at the app's prose measure and
        # left UN-ALIGNED in the layout, the pair of moves the zero-lap prose above documents: a
        # maximized dashboard is 1728 px wide and an uncapped note would set ~130 characters to
        # the line.
        self.ideal_note = WrapLabel("")
        self.ideal_note.setProperty("role", "Note")
        self.ideal_note.setFont(theme.ui_font(theme.CAPTION))
        self.ideal_note.setMaximumWidth(theme.EMPTY_MEASURE_PX)
        col.addWidget(self.ideal_note)

        # ====================== COLUMN 2 — the g story, and the corner report it explains
        # The boundary is where the page stops being about LAP TIMES and starts being about how
        # the car was driven. Column 1 is the session's identity (what it is, what it is worth,
        # how fast, how much is left); this column is the measured g and the corner-by-corner
        # report those peaks are the summary of, with the friction circle — the page's one big
        # chart — leading it.
        col, self._group = col_mid, 1

        # --- SPEED & G peaks
        self._speed_section = self._section("SPEED · G")
        col.addWidget(self._speed_section)
        self.t_vmax = Tile("top speed")
        self.t_vmax.setToolTip("Max 3D GPS speed across the valid laps (10 Hz).")
        self.t_vmin = Tile("slowest point")
        self.t_vmin.setToolTip(
            "The slowest on-lap speed across the valid laps — typically the tightest "
            "corner (a traffic or off-line lap can dip lower).")
        self.t_peak_lat = Tile("peak lateral g")
        self.t_peak_lat.setToolTip(
            "Peak |lateral g| over the valid laps — IMU lateral, the GPS-cross-checked axis "
            "(see DATA TRUST).")
        self.t_peak_brake = Tile("peak braking g")
        # §4.3: "smoothed" was in this string and the WINDOW was not, and a window is the whole
        # story for a MAXIMUM. Measured on the D24 0060 pair (38 valid laps): the per-lap peak
        # runs a median 0.862 g here against 1.081 g on the same signal unsmoothed, and the
        # session max this tile prints reads 1.27 g where the instantaneous peak was 1.94 g.
        # The smoothing is right — a raw d|v|/dt peak is a GPS spike, and the repo's rule is
        # percentiles-not-raw-max — but a number that is 20-35% under the instantaneous one
        # has to say which it is. The window is read from the constant, not typed, so it
        # cannot drift from the signal it describes.
        self.t_peak_brake.setToolTip(
            f"Peak SUSTAINED deceleration — the GPS speed derivative (the validated "
            f"longitudinal; the raw IMU forward axis is vibration-inflated), smoothed over "
            f"{gmeter.LONG_SMOOTH_S:g} s. A {gmeter.LONG_SMOOTH_S:g} s window lowers a peak, "
            f"so this reads under the instantaneous spike on purpose: the spike is GPS "
            f"quantization noise, not grip. 10 Hz GPS also quantizes brake onsets by ~1.5 m.")
        col.addLayout(self._grid(self.t_vmax, self.t_vmin, self.t_peak_lat,
                                 self.t_peak_brake))
        # Without an accelerometer two of those four tiles can only ever be em-dashes — say why
        # WHERE the dashes are, not only in the trust card (the DRIVING and FRICTION CIRCLE
        # sections hide themselves entirely, so these are the g surfaces left visible).
        self.no_gmeter_note = QLabel(NO_GMETER_NOTE)
        self.no_gmeter_note.setWordWrap(True)
        self.no_gmeter_note.setProperty("role", "Note")
        self.no_gmeter_note.setFont(theme.ui_font(theme.CAPTION))
        self.no_gmeter_note.setVisible(False)
        col.addWidget(self.no_gmeter_note)

        # --- the g-g friction circle
        # Named unit in the header, the convention its peers already follow ("CORNERS · speeds
        # in km/h") — the axes carry the detail, this carries the scan.
        self._gg_section = self._section("FRICTION CIRCLE · g")
        col.addWidget(self._gg_section)
        self.gg = pg.PlotWidget()
        self.gg.setToolTip(GG_TOOLTIP)
        plot = self.gg.getPlotItem()
        plot.setAspectLocked(True)  # a circle must render round, whatever the pane shape
        for side in ("left", "bottom"):
            ax = plot.getAxis(side)
            ax.setPen(_axis_pen())
            ax.setTextPen(C.text_dim)
            ax.setTickFont(theme.mono_font(10))
            ax.setStyle(maxTickLevel=0, tickLength=3)
        # Both axes are named and united. Without this the friction circle was the one chart on
        # the page whose numbers ("-2.0 / +0.0 / +2.0") stated neither what they measured nor in
        # what — while CORNERS and PER LAP name their units in their own headers.
        label_style = {"color": C.text_dim, "font-size": f"{theme.CAPTION}pt"}
        plot.setLabel("bottom", GG_AXIS_X, **label_style)
        plot.setLabel("left", GG_AXIS_Y, **label_style)
        plot.setMouseEnabled(x=False, y=False)
        plot.setMenuEnabled(False)
        plot.hideButtons()
        self.gg.setBackground(None)
        # Compact + left-aligned (like the tiles/tables): a maximized panel widens the pane and
        # (from WIDE_PANE_PX) the plot with it, but the circle stays a circle — the size is set
        # EXPLICITLY in both axes so it cannot vary with the device pixel ratio (see GG_ASPECT).
        self._set_gg_size(GG_HEIGHT)
        # Reference geometry (rings + axes) is drawn per-refresh, sized to the cloud.
        self._gg_rings: list = []
        self._gg_dots = pg.ScatterPlotItem(size=3, pen=None, pxMode=True)
        plot.addItem(self._gg_dots)
        col.addWidget(self.gg, 0, Qt.AlignLeft)
        # The chart's key: which ring is the 0.5 g rule and which is the measured p98 envelope.
        # Filled per refresh (the envelope's value goes in it), hidden with the section.
        self.gg_key = QLabel("")
        self.gg_key.setWordWrap(True)
        self.gg_key.setProperty("role", "Note")
        self.gg_key.setFont(theme.ui_font(theme.CAPTION))
        col.addWidget(self.gg_key)

        # --- DRIVING reductions (hidden without a g signal)
        self._driving_section = self._section("DRIVING")
        col.addWidget(self._driving_section)
        self.t_brake = Tile("braking / lap · median")
        self.t_brake_n = Tile("brake events / lap")
        self.t_coast = Tile("coasting / lap · median")
        self.t_longest_coast = Tile("longest coast")
        self.t_grip_ceiling = Tile("grip envelope · p98")
        self.t_grip_ceiling.setToolTip(
            "The session's demonstrated combined-g ceiling: the 98th percentile of "
            "hypot(lateral, longitudinal) over the valid laps — the dashed ring on the "
            "friction circle, and the same robust convention the per-corner grip "
            "normalises to. ESTIMATED (lateral-dominant).")
        self._driving_grid = self._grid(self.t_brake, self.t_brake_n, self.t_coast,
                                        self.t_longest_coast, self.t_grip_ceiling)
        col.addLayout(self._driving_grid)

        # --- per-SECTOR best/median/σ (hidden without sector lines)
        self._sector_section = self._section("SECTORS")
        col.addWidget(self._sector_section)
        # (The "theoretical best" tile used to live here, summing this section's best splits and
        # inheriting its 0-sector hide. It is neither of those things now — see the IDEAL LAP
        # block above for where it went and why.)
        self.sector_table = self._make_table(SECTOR_COLUMNS)
        col.addWidget(self.sector_table)

        # --- the corner-by-corner session report (hidden without detected corners)
        self._corners_section = self._section("CORNERS")
        col.addWidget(self._corners_section)
        # The phase-loss headline: where the session's corner time goes (entry/apex/exit),
        # from the per-lap drift-gated thirds decomposition — coach-grade, and computed, not
        # modeled. Hidden with the section / without phase data.
        phase_tip = ("Every clean lap's Δt-vs-best through each corner, split into "
                     "equal-distance entry / apex / exit thirds (the same decomposition the "
                     "coaching reasons use), medianed per corner, positive parts summed. "
                     "Seconds = what a typical lap gives away in that phase across the whole "
                     "track; hover a corner's loss cell for its own triple.")
        self.t_phase_entry = Tile("lost on entry")
        self.t_phase_apex = Tile("lost at apex")
        self.t_phase_exit = Tile("lost on exit")
        for t in (self.t_phase_entry, self.t_phase_apex, self.t_phase_exit):
            t.setToolTip(phase_tip)
        col.addLayout(self._grid(self.t_phase_entry, self.t_phase_apex, self.t_phase_exit))
        self.corners_table = self._make_table(CORNER_COLUMNS)
        self.corners_table.setToolTip(CORNERS_TOOLTIP)
        # The corner-direction arrow in column 0 paints at the app's ICON_PX rather than at the
        # style's PM_SmallIconSize (see lap_table.CornerTable for the same statement). It fits the
        # ROW_HEIGHT with 4 px either side.
        self.corners_table.setIconSize(QSize(theme.ICON_PX, theme.ICON_PX))
        # Unlike the other stats tables this one is interactive: row-select → map ring,
        # header-click → sort (numeric via _NumItem, the lap-table idiom).
        self.corners_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.corners_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.corners_table.setFocusPolicy(Qt.ClickFocus)
        self.corners_table.itemSelectionChanged.connect(self._on_corner_row_selected)
        self.corners_table.horizontalHeader().sortIndicatorChanged.connect(
            self._on_corner_sort)
        # Explicit initial indicator: TRACK ORDER (corner id ascending). Without this, Qt's
        # untouched default indicator is column-0 DESCENDING and the first fill's
        # setSortingEnabled(True) would silently reverse the track.
        self.corners_table.horizontalHeader().setSortIndicator(0, Qt.AscendingOrder)
        col.addWidget(self.corners_table)
        # THE RECONCILIATION LINE. This page and the Coaching tab both print a per-corner "loss"
        # and they are 3.8x apart in total on the owner's own recording (D24 0060: 3.93 s here,
        # 1.02 s there, and corner by corner from 1.3x to 2450x), because they answer different
        # questions: this column is measured against each corner's own Best — the quickest anyone
        # went through it, usually not your best lap — and Coaching is measured against your best
        # lap. Both are correct; neither said which it was, one tab apart, under the same word.
        # The header has no room to say it (see CORNER_COLUMNS), and a tooltip is not the face, so
        # it is said here, with the numbers computed live rather than baked.
        self.corners_note = WrapLabel()
        self.corners_note.setProperty("role", "TableNote")
        col.addWidget(self.corners_note)

        # ====================== COLUMN 3 — the three remaining report tables
        # BRAKING, STRAIGHTS and PER LAP are the page's tallest, narrowest content — three grids
        # of numbers that cap themselves at their own columns and so leave the most empty canvas
        # beside them. Measured on the laid-out D24 page the three groups run 983 / 907 / 1338 px,
        # so this is the TALLEST of them and the one the 2-column forms hang a whole grid column
        # on. Its widest member (STRAIGHTS, 610 px) is also what this column has to be GIVEN —
        # see _group_min_width, and PAGE_COL_MIN_PX for what happens when a packer forgets to ask.
        col, self._group = col_right, 2

        # --- braking repeatability + commitment (hidden without corners / a g signal)
        self._braking_section = self._section("BRAKING")
        col.addWidget(self._braking_section)
        self.braking_table = self._make_table(BRAKE_COLUMNS)
        self.braking_table.setToolTip(BRAKING_TOOLTIP)
        self.braking_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.braking_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.braking_table.setFocusPolicy(Qt.ClickFocus)
        self.braking_table.itemSelectionChanged.connect(self._on_brake_row_selected)
        self.braking_table.horizontalHeader().sortIndicatorChanged.connect(
            self._on_corner_sort)
        self.braking_table.horizontalHeader().setSortIndicator(0, Qt.AscendingOrder)
        col.addWidget(self.braking_table)

        # --- the straight-line report (hidden without corners / a best lap)
        self._straights_section = self._section("STRAIGHTS")
        col.addWidget(self._straights_section)
        self.t_fix_first = Tile("fix first")
        self.t_fix_first.setToolTip(
            "The corner whose exit deficit costs the most down the following straight "
            "(exit-speed deficit × the straight's median−best time spread) — measured, "
            "not modeled. Fix this one before chasing apex speed elsewhere.")
        col.addLayout(self._grid(self.t_fix_first))
        self.straights_table = self._make_table(STRAIGHT_COLUMNS)
        self.straights_table.setToolTip(STRAIGHTS_TOOLTIP)
        self.straights_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.straights_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.straights_table.setFocusPolicy(Qt.ClickFocus)
        self.straights_table.itemSelectionChanged.connect(self._on_straight_row_selected)
        self.straights_table.horizontalHeader().sortIndicatorChanged.connect(
            self._on_corner_sort)
        self.straights_table.horizontalHeader().setSortIndicator(0, Qt.AscendingOrder)
        col.addWidget(self.straights_table)

        # --- per-lap statistics table
        self._laps_section = self._section("PER LAP")
        col.addWidget(self._laps_section)
        self.lap_table = self._make_table(LAP_COLUMNS)
        self.lap_table.setToolTip(LAP_TABLE_TOOLTIP)
        col.addWidget(self.lap_table)
        # Each column packs its sections to the TOP and absorbs its own slack. Side by side the
        # grid gives all three the tallest one's height, and without this the two shorter columns
        # would hand that difference to whichever of their sections happened to be stretchable.
        for holder in self._columns:
            holder.layout().addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # STILL AsNeeded, and it no longer fires at either shipped window size.
        #
        # The policy was never the bug. The bug was that two widgets pinned themselves WIDER than
        # the pane and made the whole page pay: the widest report table fixed itself at 730 px
        # (PER LAP, nine columns) and the friction circle at 440 (220 px at 2:1). In the 503 px
        # quadrant the app opens at, the larger of those became the scroll body's minimum, so every
        # heading, every tile row and the DATA TRUST card were laid out 239 px wider than the
        # viewport and then had to be scrolled to. Two widgets that did not fit stopped eight that
        # did.
        #
        # Both now size themselves from the pane (_ReportTable, which grows its OWN horizontal bar
        # instead; _set_gg_size, which shrinks the circle), so the body's minimum is the viewport
        # and this bar has nothing to show. AsNeeded is kept rather than turned off because the
        # rule behind it still holds — a statistics page must h-scroll rather than silently clip —
        # and it remains the honest fallback below the friction circle's readable floor.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setWidget(body)
        self._scroll = scroll
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(scroll)
        self.refresh()

    # ------------------------------------------------------------------ scaffolding
    @staticmethod
    def _section(title: str) -> QLabel:
        lab = QLabel(title)
        lab.setProperty("role", "BarLabel")
        return lab

    def _grid(self, *tiles: Tile) -> QGridLayout:
        g = QGridLayout()
        # The tile grid, on the scale. It was `0,0,0,4` / 18 / 8 — one step, one nudge and one
        # step. The bottom margin is now the GROUP separator (SPACE_S under a block of tiles, on
        # top of the column's own SPACE_XS), the columns are SPACE_L apart — the widest gap that
        # still reads as one row at the 148 px tile floor — and the rows keep SPACE_S.
        g.setContentsMargins(0, 0, 0, theme.SPACE_S)
        g.setHorizontalSpacing(theme.SPACE_L)
        g.setVerticalSpacing(theme.SPACE_S)
        self._place_tiles(g, list(tiles), self._tile_cols)
        # Registered for the responsive reflow (C6) WITH THE SECTION GROUP IT SITS IN: a narrow
        # quadrant re-places every grid at fewer columns instead of pushing the 4th column
        # off-pane, and a composed page gives each grid its OWN column's answer — the three
        # columns are no longer equal (see _place_columns), so one number for all of them would
        # have to be the narrowest column's and would waste the widest.
        self._tile_grids.append((g, list(tiles), self._group))
        return g

    @staticmethod
    def _place_tiles(g: QGridLayout, tiles: list, cols: int):
        """(Re-)place `tiles` into `g` at `cols` columns, left-packed.

        removeWidget() FIRST — always, even though the first call has nothing to remove (it is a
        documented no-op for a widget the layout does not hold).

        WHY, and it is not tidiness: this runs AGAIN whenever the reflow changes the column count,
        and QGridLayout.addWidget is not an idempotent "move" for a widget the same layout already
        holds. Qt reacts to it inside QLayout::addChildWidget by DELETING that widget's existing
        layout item — removeWidgetRecursively -> `delete lay->takeAt(i)` — i.e. it mutates, through
        the layout's own virtuals, the very layout addWidget is midway through inserting into. One
        such pass over the page's ~30 tiles was enough to leave the process in a state where the
        next burst of Qt-object destruction died: a View ▸ Units or View ▸ Colour-blind-safe cues
        toggle, after nothing more than ordinary tab use, SIGSEGV'd inside Shiboken::Object::destroy
        in whichever table refill or pyqtgraph re-plot happened to run first (QA W8-01 — the fatal
        frame moved between five tables in three modules, which is why it never looked like one
        table's bug).

        Taking each tile out first makes the add a plain insert and addChildWidget finds nothing to
        unpick. MEASURED on the reporter's sequence, Laps<->Stats churn then 30 toggles: 8/8 clean
        runs with the removeWidget, 5 deaths in 11 without it. The deeper PySide6 6.11 defect that
        turns the re-entrant item delete into a crash is NOT root-caused; what is proven is that
        this call is what arms it (disabling _place_tiles alone: 8/8 clean)."""
        for t in tiles:
            g.removeWidget(t)
        for i, t in enumerate(tiles):
            g.addWidget(t, i // cols, i % cols)
        for c in range(TILES_PER_ROW_WIDE + 1):
            g.setColumnStretch(c, 0)
        g.setColumnStretch(cols, 1)  # left-pack the tiles; slack stays right

    def _group_min_width(self, group: int) -> int:
        """The narrowest a column carrying `group` may be SET — its widest non-reflowing member.

        Everything else on this page yields to its column: prose wraps, tiles re-place, the
        friction circle shrinks, the sparkline caps. A REPORT TABLE does neither — it is
        content-sized and scrolls (see _ReportTable) — so it, and only it, can turn a narrow column
        into hidden data. Hidden tables are excluded: a session with no corners has no CORNERS
        table and must not be laid out around one."""
        need = PAGE_COL_MIN_PX
        for table in self._column_tables[group]:
            if not table.isHidden():
                need = max(need, table.content_width())
        return need

    def _grid_column_min(self, groups) -> int:
        """What one GRID column must be given to carry `groups` stacked in it."""
        return max(self._group_min_width(g) for g in groups)

    def _group_height(self, group: int) -> int:
        """How tall one section group's own content is, independent of the band it is dealt into.

        The holder's layout hint rather than the widget's height: a spanning holder has already
        been stretched to its band, so its `height()` is the answer to the question this is asked
        in order to decide."""
        return self._columns[group].layout().sizeHint().height()

    def _span_fits(self, layout) -> bool:
        """Would the column that SPANS the band fit inside the column that stacks beside it?

        This is the invariant _place_columns' shape depends on, and it used to be argued in prose
        from one recording's numbers instead of being asked. A grid column holding one group spans
        every row; if that group is TALLER than the two stacked beside it, Qt grows both of those
        rows to fit it and the difference is paid out as an EMPTY BAND inside the stack — a gap
        between two sections, next to a full column.

        It is not hypothetical and it is not exotic. Group 2 holds PER LAP, which takes one row per
        clean lap and is uncapped, while groups 0 and 1 gain nothing per lap: measured on D24 (38
        laps) the three stand 983 / 907 / 1338 px, so the stack leads by 626 px — about 26 rows at
        the 24 px grid row. The owner's own 0062 recording has 65 clean laps, which puts group 2 at
        2334 px against a 1894 px stack and opens a 130 px band in the LEFT column of the default
        1440x900 window under ⌘⇧S. The layout that was correct for one recording was wrong for the
        next one on the same disk.

        So the fit is measured, per session, at refresh: the widths decide which compositions the
        pane can pay for, and this decides which of those the CONTENT can. Form #3 stacks the two
        growing groups together and spans the group that does not grow, so it is safe by
        construction and remains available underneath."""
        rows = max(len(groups) for groups in layout)
        if rows < 2:
            return True                       # one row: nothing spans, nothing can band
        for gcol, groups in enumerate(layout):
            if len(groups) != 1:
                continue
            stack = [g for other, gs in enumerate(layout) if other != gcol for g in gs]
            if not stack:
                continue
            room = (sum(self._group_height(g) for g in stack)
                    + (len(stack) - 1) * theme.SPACE_XS)
            if self._group_height(groups[0]) > room:
                return False
        return True

    def _choose_layout(self):
        """The widest composition this pane can pay for AND this session's content can fill.

        Falls through PAGE_LAYOUTS in preference order, asking two questions of each: can every
        column be given its widest non-reflowing member (_grid_column_min), and does the column
        that spans the band fit beside the one that stacks (_span_fits). The single column is the
        floor and is always legal, which is what keeps every quadrant (and the app's 845x414
        minimum) on exactly the page it has always rendered."""
        room = self._pane_width() - 2 * theme.SPACE_M
        for layout in PAGE_LAYOUTS:
            if len(layout) == 1:
                return layout
            need = (sum(self._grid_column_min(gc) for gc in layout)
                    + (len(layout) - 1) * PAGE_COL_GAP)
            if need <= room and self._span_fits(layout):
                return layout
        return PAGE_LAYOUTS[-1]

    def _column_count(self) -> int:
        """How many section columns the page is composed into RIGHT NOW — the arrangement in
        force, not a re-derivation of it (the two could disagree between a resize and its
        layout pass, and every reader of this wants the one on screen)."""
        return len(self._layout)

    def _place_columns(self, layout) -> None:
        """(Re-)deal the three section columns into `layout`'s grid columns.

        removeWidget() FIRST, always, for the reason _place_tiles sets out at length — this runs
        again whenever the pane crosses a threshold, and QGridLayout.addWidget is not a "move" for
        a widget its own layout already holds.

        A GRID COLUMN HOLDING ONE GROUP SPANS THE WHOLE BAND. A grid ROW is shared by every column
        in it, so a column with one section beside a column with two would band the page: the lone
        section's top would sit level with the first of the pair, and the gap under the shorter of
        them would be the difference. Spanning gives every band exactly one item, and there is no
        gap anywhere.

        A SPANNING ITEM TALLER THAN THE ROWS IT SPANS IS THE ONE WAY THIS SHAPE CAN STILL OPEN A
        GAP, because Qt grows both rows to fit it. This used to be argued away in prose here, from
        one recording's measured heights — and the recording on the next line of the same disk
        broke it (65 clean laps, a 130 px band at the app's default window). It is `_span_fits`
        now: a question the packer asks of every candidate, per session, rather than a claim this
        docstring makes on their behalf.

        THE MINIMUMS ARE INSTALLED ONLY WHEN COMPOSED. Left to equal stretch alone Qt would hand
        three EQUAL columns of 609 px to groups needing 440/718/610, which is exactly the P1 this
        packer exists to stop. On the single column they are NOT installed: there the contract is
        the quadrant's — a table too wide for the pane scrolls itself rather than making the whole
        page scroll — and a 718 px floor would put a horizontal scrollbar under a 445 px page."""
        for holder in self._columns:
            self._column_grid.removeWidget(holder)
        rows = max(len(groups) for groups in layout)
        for gcol, groups in enumerate(layout):
            span = rows // len(groups)
            for row, group in enumerate(groups):
                self._column_grid.addWidget(self._columns[group], row * span, gcol, span, 1)
        composed = len(layout) > 1
        for c in range(PAGE_COLS_MAX):
            live = c < len(layout)
            self._column_grid.setColumnStretch(c, 1 if live else 0)
            self._column_grid.setColumnMinimumWidth(
                c, self._grid_column_min(layout[c]) if (live and composed) else 0)

    def _planned_widths(self, layout) -> list[int]:
        """What each grid column of `layout` will be LAID OUT at, computed rather than read back.

        Read back is the obvious way and it does not work: the widgets carry the PREVIOUS
        arrangement's widths until the layout pass that follows this one, so the friction circle
        would be sized for the composition the page just left. So this reproduces the rule
        QGridLayout applies to equal stretches over unequal minimums — hand every column an equal
        share; any column whose minimum exceeds that share takes its minimum instead and leaves the
        pool; repeat until the rest fit. Verified against the real laid-out geometry on D24 at all
        three composed panes: 494/718 at 1260, 718/654 at 1420, 500/718/610 at 1900, exact in
        every column.

        Under- and over-reading are NOT symmetric here, which is why this is worth computing:
        under-read only costs the friction circle some diameter, while an over-read pins a fixed-
        width chart wider than the column that has to hold it."""
        mins = [self._grid_column_min(groups) for groups in layout]
        room = (self._pane_width() - 2 * theme.SPACE_M
                - (len(layout) - 1) * PAGE_COL_GAP)
        out = [0] * len(mins)
        pending = list(range(len(mins)))
        while pending:
            share = room // len(pending)
            over = [i for i in pending if mins[i] > share]
            if not over:
                for i in pending:
                    out[i] = share
                break
            for i in over:
                out[i] = mins[i]
                room -= mins[i]
                pending.remove(i)
        return out

    def _column_width(self, group: int = 0) -> int:
        """The width the grid column carrying `group` is laid out in.

        The whole body on a single column — what it has always been — and that column's planned
        share once the page composes (see _planned_widths)."""
        layout = self._layout
        if len(layout) == 1:
            return max(1, self._pane_width() - 2 * theme.SPACE_M)
        planned = self._planned_widths(layout)
        for gcol, groups in enumerate(layout):
            if group in groups:
                return max(1, planned[gcol])
        return PAGE_COL_MIN_PX

    def _budget_gg_gutters(self):
        """Give the friction circle's axis TITLES the room they measure.

        The same pyqtgraph arithmetic that clipped the charts panel's `distance (m)` clipped this
        chart's `lateral g` by 7.4 px through its descenders, at every size — see
        widgets.budget_plot_gutters for the mechanism and for why the number is measured. This is a
        bare PlotWidget rather than a GraphicsLayoutWidget, so the margins that position the plot
        are the PlotItem's OWN internal layout's rather than a central layout's; the measurement is
        identical either way because it is taken against what the viewport can show."""
        plot = self.gg.getPlotItem()
        budget_plot_gutters(self.gg, plot.layout, (plot,), inset=theme.SPACE_XXS)

    def _set_gg_size(self, height: int):
        """Pin the friction circle in BOTH axes (see GG_ASPECT / U8-01), at the largest size the
        pane can actually give it. Fixed, not maximum: a maximum still lets pyqtgraph's
        DPR-dependent sizeHint pick the actual width below the cap.

        `height` is the CEILING for this pane class (GG_HEIGHT / GG_HEIGHT_WIDE); the pane decides
        whether it gets it. 220 px at 2:1 is 440 px wide, against a 445 px pane at 1280x800 less
        24 px of gutters — so left pinned it was the page's last horizontal overflow, and being
        wider than the pane it was ALSO the one thing on the page a horizontal scroll could not
        help you read (a circle you have to scroll is not a circle). Sizing it from the pane is
        the section yielding gracefully rather than being hidden."""
        room = self._column_width(GG_GROUP)   # its own section column, not the whole page
        height = max(GG_MIN_HEIGHT, min(int(height), int(room / GG_ASPECT)))
        width = int(height * GG_ASPECT)
        self.gg.setFixedHeight(height)
        self.gg.setFixedWidth(width)
        # APPLY THE SIZE NOW, then force the plot's own graphics layout to catch up, THEN measure.
        # setFixedWidth/Height only ask the parent layout for a size; the widget itself changes
        # later, and pyqtgraph sizes its SCENE from the widget. Measuring in between reads a plot
        # laid out for the new height inside a viewport still reporting the old one, and the gutter
        # comes out of that mixture — 24 px of reserve where 2 was right, on the maximize.
        self.gg.resize(width, height)
        self.gg.getPlotItem().layout.activate()
        self._budget_gg_gutters()

    def _pane_width(self) -> int:
        """The width the tiles actually get, derived from THIS widget rather than read off the
        scroll viewport. Inside a resizeEvent the viewport still carries its previous width (the
        child layout has not been applied yet), so measuring it there reflows the page to the
        size it used to be — visible when a window is sized before it is first shown. The scroll
        area fills this widget with no margins, so its viewport is our width less the scrollbar."""
        if self._scroll is None:
            return self.width()
        bar = self._scroll.verticalScrollBar()
        return self.width() - (bar.width() if bar is not None and bar.isVisible() else 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow_tiles()

    def showEvent(self, event):
        """Reflow once the page is on screen too — the scrollbar's visibility (and so the usable
        width) is only settled then. Idempotent: the reflow early-returns when nothing changed."""
        super().showEvent(event)
        self._reflow_tiles()

    def event(self, ev):
        """Re-pen when the window moves to a screen with a different device-pixel ratio.

        A pyqtgraph pen width is in DEVICE pixels (theme.line_width), so the right width depends on
        the screen the window is on right now. Without this the sparkline and the friction circle
        kept the ratio that was current when the page was built, while the charts one tab away
        followed the move — two surfaces in one window disagreeing about the same design weight."""
        if ev.type() == QEvent.Type.DevicePixelRatioChange:
            if theme.set_pen_scale(self.devicePixelRatioF()):
                self._apply_pen_scale()
        return super().event(ev)

    def _apply_pen_scale(self):
        """Re-issue every pyqtgraph pen on this page at the current device-pixel ratio.

        In place rather than through refresh(): the page must re-pen on a screen change whether or
        not a session is loaded, and the rings/baseline carry per-refresh colours that _repen keeps.
        The scatter BRUSHES and the pxMode glyph SIZES are device-independent already and are
        deliberately left alone (scaling a size would draw double-size dots on a Retina panel)."""
        if not hasattr(self, "_gg_rings"):
            return                      # a DPR event landed mid-construction; __init__ will pen
        for plot in (self.spark.getPlotItem(), self.gg.getPlotItem()):
            for side in ("left", "bottom"):
                ax = plot.getAxis(side)
                ax.setPen(_axis_pen())
                # ...and the TEXT pen after it, because AxisItem.setPen also owns the axis TITLE's
                # colour: it ends by writing its own pen's colour into `labelStyle` and re-rendering
                # the label. Without this line, moving the window to a screen with another
                # device-pixel ratio repainted the friction circle's two axis titles in the HAIRLINE
                # colour — 1.19:1, the charts panel's defect arriving here by a different door.
                ax.setTextPen(C.text_dim)
        self._spark_curve.setPen(_spark_curve_pen())
        self._spark_pb_dots.setPen(_glyph_outline_pen())
        for item in (self._spark_baseline, *self._gg_rings):
            _repen(item)

    def _reflow_tiles(self):
        """Fit the page to the actual pane, in two steps.

        FIRST the page's own COLUMNS: how many PAGE_COL_MIN_PX columns the pane carries, re-dealt
        only when the count changes. THEN, inside one of them, C6's tile reflow — tile columns =
        the column's share of the pane // TILE_MIN_PX, clamped 2..the cap, the cap rising to
        TILES_PER_ROW_WIDE once a single column is dashboard-width on its own; the friction circle
        sizes from the same share. Re-places widgets only when something actually changes (cheap;
        a resize otherwise costs nothing).

        THE TILE THRESHOLD IS MEASURED AGAINST THE PANE'S SHARE, gutters included, because that is
        what TILE_MIN_PX has always been calibrated against: the 1280x800 quadrant is a 445 px pane
        holding 421 px of body, and it packs THREE tiles into it. Comparing 421 against a "minimum"
        of 148 would drop that quadrant to two columns — a shipped surface changed to make an
        arithmetic tidier.

        AND IT IS ASKED PER SECTION GROUP, because the composed columns are not equal: they are
        each their own content's minimum plus a share of the slack, so at 1920x1200 the three run
        roughly 500 / 718 / 610 px. One count for all of them would have to be the narrowest
        column's, which would leave the widest with four tiles' worth of empty gutter."""
        layout = self._choose_layout()
        if layout != self._layout:
            self._layout = layout
            self._place_columns(layout)
        # A page dealt into columns is a DASHBOARD; a single column is a quadrant, whatever its
        # width. That is the same distinction WIDE_PANE_PX drew when the page had one column and
        # the pane was the column — it is drawn on the column now that those differ.
        wide = len(layout) > 1
        self._wide = wide
        cols_by_group = {}
        for group in range(PAGE_COLS_MAX):
            share = self._column_width(group) + 2 * theme.SPACE_M
            cols_by_group[group] = max(2, min(
                TILES_PER_ROW_WIDE if share - 2 * theme.SPACE_M >= WIDE_PANE_PX else TILES_PER_ROW,
                share // TILE_MIN_PX))
        # UNCONDITIONALLY, not only when the pane crosses a threshold: the friction circle's size
        # is a function of its column's actual width (see _set_gg_size), so a resize INSIDE a
        # class still changes it. Cheap — setFixedHeight/Width on an unchanged value is a no-op.
        self._set_gg_size(GG_HEIGHT_WIDE if wide else GG_HEIGHT)
        # The page's headline number stays what it has always been: the widest column's answer.
        self._tile_cols = max(cols_by_group.values())
        if cols_by_group == self._tile_cols_by_group:
            return
        self._tile_cols_by_group = cols_by_group
        for g, tiles, group in self._tile_grids:
            self._place_tiles(g, tiles, cols_by_group[group])

    def _make_table(self, columns: list[str]) -> QTableWidget:
        """One report table (see _ReportTable): content-sized, scrolling itself when it must.

        Registered against the section group being built, because a table is the one thing on this
        page that cannot yield to a narrow column — so the packer has to be able to ask a group
        what its tables need before it composes (_group_min_width)."""
        table = _ReportTable(columns, ROW_HEIGHT)
        self._column_tables[self._group].append(table)
        return table

    @staticmethod
    def _fit_table(t: QTableWidget):
        """Re-measure a table after a refill — columns to content, width capped there, height
        pinned so the OUTER column keeps owning the vertical scroll."""
        t.fit()

    def _num_item(self, text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        item.setFont(theme.mono_font(theme.TABLE))
        return item

    def _set_target_tile(self, tile: Tile, value, tip: str, text: str | None = None,
                         caption: str | None = None):
        """Render a stitched TARGET tile (theoretical best / best rolling / the ideal's gap).

        These are not laps anyone drove — they are composed from the session's best splits and
        loops — so they share the lap timing's authority: while the timing is PROVISIONAL (an
        arbitrary start line) OR the clock is DEGRADED (media-clock / low-GPS estimate) the value
        is muted + italic and carries the explaining note, restored to the normal tile once
        Verified AND high-quality. Kept byte-for-byte in spirit with the Laps footer this moved
        from; the measured PACE tiles beside it are unmuted because they ARE laps you drove.

        `text` overrides the m:ss.mmm formatting for a target that is a DIFFERENCE rather than a
        lap time ("-1.64 s"). It is still a synthesized number and still takes the mute — the rule
        is about where the number came from, not about how it is printed.

        `caption` re-labels the tile per refresh, for a target whose SAMPLE belongs on it — the
        ideal's "theoretical best · 65 laps", the same shape the measured `median · 65 clean laps`
        beside it already uses. It goes through this method rather than a bare `tile.set()` after
        it, because a second `set()` re-runs `_claim_ink_height` on a value this method has just
        styled, and the colour-then-font ordering below exists precisely because that path is
        order-sensitive."""
        session = self.session
        tile.set((text if text is not None else fmt_time(value)) if value is not None else None,
                 caption)
        provisional = not getattr(session, "timing_verified", True)
        quality = getattr(session, "timing_quality", None)
        muted = provisional or bool(quality is not None and quality.degraded)
        # COLOUR FIRST, THEN FONT — not cosmetic ordering. setStyleSheet on a NEW string repolishes
        # the label, and the repolish re-resolves its font, dropping the italic bit a setFont set a
        # moment earlier. The other way round the first refresh of a fresh view painted the muted
        # target tile upright, and only a SECOND refresh() made it italic: the app happened to get
        # that second call from CentralView after a load, so the cue shipped — but nothing on the
        # single-refresh paths (a tab switch, a unit flip, a bare StatsView) did. Setting the
        # stylesheet first means the repolish is already spent when the font lands.
        tile.value.setStyleSheet(
            f"color: {theme.PROVISIONAL_COLOR if muted else C.text};")
        font = theme.mono_font(theme.EMPHASIS, theme.W_SEMIBOLD)
        font.setItalic(muted)
        tile.value.setFont(font)
        if not muted:
            tile.setToolTip(tip)
            return
        note = PROVISIONAL_TOOLTIP if provisional else estimated_timing_tooltip(quality)
        tile.setToolTip(f"{note}\n\n{tip}")

    # ------------------------------------------------------------------ contract
    def refresh(self):
        """Rebuild every group from the session (load / re-segmentation / unit or palette
        flip — the reads are cached on the service side, so a re-render is cheap)."""
        session = self.session
        st = getattr(session, "stats", None)
        unit = self._speed_unit
        u_label = units.speed_label(unit)

        # SESSION totals
        valid = session.valid_lap_ids()
        # The page's own trust banner: the maximized dashboard has no map to carry the app's
        # amber "drag the start/finish line" CTA. Pointless with no laps — the empty-state block
        # below already tells that story (and names the same line as its next action).
        self.provisional_banner.setVisible(
            bool(valid) and not getattr(session, "timing_verified", True))
        self._set_no_laps_state(bool(valid))
        # The absent-accelerometer note sits under the SPEED · G tiles whose dashes it explains,
        # so it hides with them on a lapless recording (the empty-state block tells that story).
        self.no_gmeter_note.setVisible(
            bool(valid) and not getattr(session, "has_gmeter", False))
        excluded = getattr(session, "excluded_lap_ids", list)() or []
        dropouts = session.dropout_lap_ids() if hasattr(session, "dropout_lap_ids") else set()
        # SPACE BETWEEN THE COUNT AND ITS MARK, and it is not a nudge (D1-04). This tile shipped
        # "24⊘": measured on the live composite the ⊘ and the 4 merged into ONE 40x19 ink run with
        # no gap, and at EMPHASIS=15 the ⊘ fills the whole 19 px box against 13 px digits — 1.46x
        # the height of the number it is qualifying. The tile's own legend (setToolTip above) and
        # the DATA TRUST row below both already space it; this was the one of the three that did
        # not, so the same fact printed two ways on one page.
        lap_bits = [str(len(valid))]
        if excluded:
            lap_bits.append(f"{len(excluded)} {EXCLUDED_MARK}")
        if dropouts:
            lap_bits.append(f"{len(dropouts)} {DROPOUT_MARK}")
        self.t_laps.set(" · ".join(lap_bits) if valid else None)
        tot = st.totals() if st is not None else None
        if tot is not None and tot.duration_s > 0:
            # COLLAPSE THE TWINS WHEN THEY AGREE (§5.6). "recorded 28:50" beside "moving 28:50"
            # is two tiles asserting one fact and inviting the reader to hunt for the difference
            # between them; the pair only says something when the kart actually stopped. Compared
            # at the resolution the tiles PRINT (h:mm:ss), because two values that differ by 400 ms
            # render identically and would leave a "difference" nobody can see.
            recorded, moving = fmt_hms(tot.duration_s), fmt_hms(tot.moving_s)
            same = recorded == moving
            self.t_duration.set(recorded, "recorded · all moving" if same else "recorded")
            self.t_moving.setVisible(not same)
            self.t_moving.set(None if same else moving)
            self._set_distance(tot)
            clock = (f"{tot.start_clock}–{tot.end_clock}"
                     if tot.start_clock and tot.end_clock else None)
            self.t_clock.set(clock)
        else:
            self.t_moving.setVisible(True)          # a later refresh may need the twin back
            for t in (self.t_duration, self.t_moving, self.t_distance, self.t_clock):
                t.set(None)

        # PACE
        pace = st.pace() if st is not None else None
        if pace is not None:
            self.t_best.set(fmt_time(pace.best))
            # Singular at n=1: "median · 1 clean laps" was the caption on a session where the
            # median IS the only lap.
            self.t_median.set(fmt_time(pace.median),
                              f"median · {pace.n} clean lap{'' if pace.n == 1 else 's'}")
            self.t_sigma.set(f"{pace.sigma:.2f} s" if pace.sigma is not None else None)
            # spread and the within-1% count carry σ's minimum-sample gate in the DATA layer
            # (stats.MIN_DIST_LAPS), so all three dash together instead of two of them printing
            # "+0.00 s" and "1 / 1" off the same single lap.
            self.t_spread.set(f"+{pace.spread:.2f} s" if pace.spread is not None else None)
            rp = st.race_pace()
            self.t_race_pace.set(fmt_time(rp) if rp is not None else None)
            cov = st.pace_cov()
            self.t_cov.set(f"{cov:.1f} %" if cov is not None else None)
            count, n = st.laps_within_pct(1.0)
            self.t_within.set(f"{count} / {n}" if count is not None else None)
            self._set_trend(st.pace_trend())
        else:
            for t in (self.t_best, self.t_median, self.t_sigma, self.t_spread,
                      self.t_race_pace, self.t_cov, self.t_within, self.t_trend):
                t.set(None)
        # The rolling best is a stitched target, not a measured lap — it reads straight off
        # Session (never the pace summary) so it survives a session with no clean-lap stats.
        rolling = (session.best_rolling_lap()
                   if hasattr(session, "best_rolling_lap") else None)
        self._set_target_tile(self.t_rolling, rolling, ROLLING_TOOLTIP)
        self._set_digest(session, pace)
        self._refresh_spark(session)
        self._refresh_ideal(session)

        # SPEED · G — session peaks over the per-lap stats
        rows = st.lap_stats() if st is not None else []
        vmax = st.session_vmax() if st is not None else None
        if vmax is not None:
            self.t_vmax.set(f"{units.convert_speed(vmax[0], unit):.1f} {u_label}",
                            f"top speed · lap {vmax[1] + 1}")
        else:
            self.t_vmax.set(None, "top speed")
        vmins = [r.vmin_kmh for r in rows if r.vmin_kmh is not None]
        self.t_vmin.set(f"{units.convert_speed(min(vmins), unit):.1f} {u_label}"
                        if vmins else None)
        lat_peaks = [r.peak_lat_g for r in rows if r.peak_lat_g is not None]
        brk_peaks = [r.peak_brake_g for r in rows if r.peak_brake_g is not None]
        self.t_peak_lat.set(f"{max(lat_peaks):.2f} g" if lat_peaks else None)
        self.t_peak_brake.set(f"{max(brk_peaks):.2f} g" if brk_peaks else None)

        self._refresh_gg(st)
        self._refresh_driving(st, rows)
        self._refresh_sectors(session)
        self._refresh_corners(session, unit, u_label)
        self._refresh_braking(session)
        self._refresh_straights(session, unit, u_label)
        self._refresh_trust(session)
        self._refresh_lap_table(session, rows, unit, u_label)
        # RE-PACK, because the packer's inputs are what this method just changed. A composition is
        # legal only while every column can still be given its widest table's width, and a refresh
        # is exactly when those move: a unit flip re-measures every speed column, a re-segmentation
        # adds corners (and rows), and a session with no corners hides two tables outright. Without
        # this the page would keep a 3-column arrangement decided for a narrower CORNERS table.
        self._reflow_tiles()

    def _set_distance(self, tot):
        """The SESSION distance tile. The path length is speed-gated in the data layer (a GPS fix
        that teleports is not distance driven), so this also has to be able to say "the trace was
        too broken to measure": below stats.MIN_KEPT_FRAC the service returns None and the tile
        dashes rather than printing a number nobody can trust. When a smaller share was rejected
        the number stands and the tooltip says so — silently dropping metres would be its own
        kind of dishonesty."""
        kept = getattr(tot, "distance_kept_frac", 1.0)
        if tot.distance_m is None:
            self.t_distance.set(None)
            self.t_distance.setToolTip(
                f"Not shown: only {kept * 100:.0f}% of this trace's GPS steps are physically "
                "possible at the speed the same trace reports — the rest are dropped fixes, so a "
                "path length would be a fiction. The recorded time and the lap statistics are "
                "unaffected.")
            return
        self.t_distance.set(f"{tot.distance_m / 1000.0:.1f} km")
        # A handful of rejected steps is not worth a caveat that would round to "0%" — the note
        # appears from a whole percent up (a real 26-minute recording rejects 0.02%).
        self.t_distance.setToolTip(
            "Path length of the recorded trace (the sum of its GPS steps)."
            if kept >= 0.99 else
            f"Path length of the recorded trace. {(1 - kept) * 100:.0f}% of the raw steps were "
            "rejected as impossible at the speed the same trace reports (dropped GPS fixes) and "
            "are not counted.")

    @staticmethod
    def _spark_frame(times: list[float]) -> tuple[float, list[float]]:
        """The sparkline's y CEILING and the laps left above it.

        Tukey's upper fence over the clean lap times (q3 + SPARK_OUTLIER_IQR x IQR): the ceiling is
        the slowest lap AT OR UNDER the fence, so both y labels stay REAL LAP TIMES — a chart that
        prints a percentile as if it were a lap is a worse lie than a flat line. Self-limiting by
        construction: with no outlier the fence sits above every lap, the ceiling is the maximum
        and this is exactly the range the sparkline has always drawn.

        Below SPARK_MIN_FOR_FENCE laps, or with a degenerate IQR (every lap the same time), no lap
        is fenced — quartiles of five numbers are not a description of a session."""
        hi = max(times)
        if len(times) < SPARK_MIN_FOR_FENCE:
            return hi, []
        q1, q3 = (float(v) for v in np.percentile(times, (25, 75)))
        if q3 - q1 <= 0:
            return hi, []
        fence = q3 + SPARK_OUTLIER_IQR * (q3 - q1)
        over = [t for t in times if t > fence]
        if not over:
            return hi, []
        return max(t for t in times if t <= fence), over

    def _refresh_spark(self, session):
        """The PACE trend sparkline: lap time per clean lap (x = the 1-BASED lap number, the
        same number every table shows), PB laps in the best-lap hue, the session best as a
        dashed baseline. Hidden with <2 clean laps (a one-dot trend is noise).

        THE FRAME IS ROBUST (see _spark_frame) and the WIDTH IS CAPPED BY THE SAMPLE COUNT (see
        SPARK_PX_PER_LAP) — the two ways this chart used to be decided by its single worst lap and
        by its pane rather than by its content."""
        trend = getattr(session, "lap_time_trend", list)() or []
        visible = len(trend) >= 2
        self.spark.setVisible(visible)
        if not visible:
            return
        laps = [i + 1 for i, _t in trend]   # 1-based, the app-wide display rule
        times = [t for _i, t in trend]
        pb = pb_mask(times)
        best_colour = QColor(theme.best_lap_colour())  # palette-aware at render time
        # A chart is as wide as it has data for. Un-flagged in the layout, so the item takes
        # min(cell, maximumWidth) — a narrower column still narrows it (see the no-laps prose for
        # the same construction, and for what an alignment flag would cost here).
        self.spark.setMaximumWidth(max(theme.EMPTY_MEASURE_PX,
                                       SPARK_AXIS_W + len(times) * SPARK_PX_PER_LAP))
        self._spark_curve.setData(laps, times)
        self._spark_dots.setData([n for n, on in zip(laps, pb, strict=True) if not on],
                                 [t for t, on in zip(times, pb, strict=True) if not on])
        self._spark_pb_dots.setBrush(pg.mkBrush(best_colour))
        self._spark_pb_dots.setData([n for n, on in zip(laps, pb, strict=True) if on],
                                    [t for t, on in zip(times, pb, strict=True) if on])
        lo = min(times)
        hi, over = self._spark_frame(times)
        self._spark_baseline.setPen(_spark_baseline_pen(best_colour))
        self._spark_baseline.setValue(lo)
        plot = self.spark.getPlotItem()
        pad = max((hi - lo) * SPARK_Y_PAD_FRAC, 1e-3)
        # A fenced frame pays for its marks: the off-scale triangles sit a pad above the ceiling,
        # so they need a second pad of ceiling above THEM or they paint half-clipped.
        plot.setYRange(lo - pad, hi + pad * (2 if over else 1), padding=0)
        plot.setXRange(laps[0], laps[-1], padding=0.04)
        self._spark_over_dots.setData([n for n, t in zip(laps, times, strict=True) if t > hi],
                                      [hi + pad] * len(over))
        plot.getAxis("left").setTicks([[(lo, fmt_time(lo)), (hi, fmt_time(hi))]])
        plot.getAxis("bottom").setTicks([[(laps[0], str(laps[0])),
                                          (laps[-1], str(laps[-1]))]])
        tip = SPARK_TOOLTIP
        if over:
            tip += SPARK_OUTLIER_TIP.format(n=plural(len(over), "lap"),
                                            slowest=fmt_time(max(over)),
                                            kept=len(times) - len(over))
        self.spark.setToolTip(tip)

    def _set_trend(self, slope: float | None):
        """The trend tile: signed s/lap + a plain-language verdict caption. A slope inside
        ±`stats.TREND_STEADY_BAND` reads "steady" (don't narrate noise); None (short session) is
        a dash with the base caption. Both the verdict and the value formatting come from the
        stats layer (`trend_verdict` / `fmt_trend`), so the exported summary's row says exactly
        what this tile says."""
        verdict = stats_service.trend_verdict(slope)
        if verdict is None:
            self.t_trend.set(None, "trend")
            return
        self.t_trend.set(stats_service.fmt_trend(slope), f"trend · {verdict}")

    def _set_digest(self, session, pace):
        """The coaching digest tile: the projected lap if the top-N corner losses were fixed,
        anchored to the MEDIAN lap (the honesty rule — the best lap already banks some of
        those corners, so best − losses would overclaim). Dash without enough clean laps /
        no coaching data.

        L5-02 — the saving is the Coaching panel's ARITHMETIC, not a parallel one: its rows
        (`_ranked_shown`: sub-resolution losses dropped, abstained corners excluded), its count
        (`PANEL_TOP_N`) and its
        rounding (the 2-dp cells the user can add up by eye, summed and re-rounded). Summing the
        raw floats instead made the two surfaces disagree by a rounding penny for the same three
        corners — 0.31 s here against 0.32 s on the Coaching page — and made this tile disagree
        with its OWN tooltip, which printed 0.31 while subtracting 0.3134."""
        opp_fn = getattr(session, "coaching_opportunities", None)
        opp = opp_fn() if opp_fn is not None else None
        has_rows = getattr(opp, "enough", False) and getattr(opp, "rows", None)
        # RANKED rows, the same shortlist the Coaching headline totals — the per-corner evidence
        # gate sinks the corners whose claim is inside their own lap-to-lap spread, and a tile that
        # summed those would state a saving the page beside it refuses to.
        rows = _ranked_shown(opp)[:PANEL_TOP_N] if has_rows else []
        if pace is None or not rows:
            self.t_digest.set(None)
            self.t_digest.setToolTip("")
            return
        saved = round(sum(round(r.time_lost, 2) for r in rows), 2)
        projected = pace.median - saved
        # THE RANGE THAT USED TO BE TYPED HERE ("measured on the owner's recordings the ideal is
        # 0.33 to 2.67 s the faster of the two") was right on the recordings it was measured on and
        # silent about every other one — on the reviewed screen the two tiles were 5.0 s apart while
        # this sentence promised at most 2.67. An empirical constant baked into honesty copy rots
        # the moment the data moves; this reads the two numbers it is comparing.
        ideal = getattr(session, "ideal_total", lambda: None)()
        spread = ""
        if ideal is not None:
            d = projected - float(ideal)
            spread = (f" — here the ideal is {abs(d):.2f} s "
                      f"{'faster' if d > 0 else 'slower'} than this projection")
        self.t_digest.set(fmt_time(projected), f"median lap · top {len(rows)} fixed")
        self.t_digest.setToolTip(
            f"Projected from your MEDIAN lap ({fmt_time(pace.median)}) minus the top-"
            f"{len(rows)} corner losses ({saved:.2f} s, measured vs your best lap's "
            "corners). Anchored to the typical lap, not best-minus-losses: your best lap "
            "already banks some of those corners — so this target can read SLOWER than your "
            "best lap and still be the honest one. The Coaching tab lists the corners.\n\n"
            "It is not the IDEAL LAP below and cannot be compared with it directly: this one is "
            "a TYPICAL lap with three corners fixed, that one is your quickest time through "
            f"every segment stitched together. Different anchors, different questions{spread}.")

    def _refresh_ideal(self, session):
        """The IDEAL LAP block: the theoretical best, what it says is on the table, and the
        DECOMPOSITION — which segments that gap lives in, on which lap you drove each one, and
        how repeatable it is.

        THE GATE IS NOT THE OLD ONE, and that is the point of this block. The tile used to hide
        when `sector_count() == 0`, which was right while the "theoretical best" was a sum of
        best SECTOR splits — with no sector line a lap is one sector and the sum degenerated to
        the best lap time. It is a corner/straight composite now, so sector lines have nothing to
        do with it (driven through set_timing_lines at 0/1/2/3 lines the total is byte-identical),
        and `sector_count()` is 0 on every recording the owner has: the old gate hid the corrected
        number on all five of them. The condition that actually means "this would be a duplicate
        of a lap you already see" is `ideal_donor_lap_id()` — one lap won every segment, so the
        ideal IS that lap — and that is what hides it now, along with the no-composite case
        (`ideal_total() is None`: no corner detected, so the "partition" is the whole lap).

        The tiles take `_set_target_tile`'s PROVISIONAL/DEGRADED mute: both are synthesized, so
        both share the lap timing's authority. The TABLE does not, for the same reason the
        CORNERS and STRAIGHTS reports beside it do not — its cells are measured segment times
        and differences between them, and the page's own trust banner already qualifies the page.
        """
        sb = getattr(session, "ideal_segment_bests", lambda: None)()
        best_id = session.best_lap_id() if hasattr(session, "best_lap_id") else None
        single = (session.ideal_donor_lap_id()
                  if hasattr(session, "ideal_donor_lap_id") else None)
        rows = sb.decomposition(best_id) if (sb is not None and best_id is not None) else None
        has = sb is not None and single is None and rows is not None
        self._set_ideal_visible(has)
        if not has:
            self.ideal_table.setRowCount(0)
            self.ideal_note.setText("")
            self.ideal_sample.setText("")
            return
        total = sb.total
        # THE SAMPLE, ON BOTH TILES AND IN THE LINE UNDER THEM. `sample` is the one accessor
        # (corner_model.IdealSample) so this block and the hero's `vs ideal` chip cannot answer
        # "how many laps is this over" differently.
        #
        # The CAPTION carries the lap count and the tooltip carries the mechanism, which is the
        # split this page already uses for its other sampled target: `median · 65 clean laps`
        # names its own n on the tile and explains it on hover. Only the theoretical tile's
        # caption grows — `on the table · vs your best` is at the width the grid column already
        # affords, and the line below carries the count for both.
        # BOTH STRINGS COME OFF `IdealSample` ITSELF (caption / sentence), not from a local
        # f-string. The laps.csv trailer and the exported HTML report print the same two, and a
        # leaving-the-app surface has no tooltip to fall back on when it drifts — so the disclosure
        # is defined on the value object that owns the counts (§5.4).
        smp = sb.sample
        self._set_target_tile(self.t_theoretical, total, THEORETICAL_TOOLTIP,
                              caption=smp.caption())
        self.ideal_sample.setText(smp.sentence())
        # The best lap's time READ OFF THE COMPOSITE, not off `session.lap_time`. They are the
        # same number — `corners.segment_times` asserts a lap's segments sum exactly to its lap
        # time, which is the guarantee the whole composite stands on — and taking it from the same
        # matrix the gains come from makes `gap == Σ gains` true by construction rather than by
        # agreement between two accessors. The tile and the table cannot drift.
        gap = float(sb.times[sb.lap_ids.index(best_id)].sum()) - total
        # SIGNED, and negative, because that is the direction the number moves your lap time —
        # the same convention the hero's `vs ideal` chip and the trend tile use. Formatted with
        # the ASCII sign f-strings produce, not a typographic minus: this is a MONO tile and the
        # app's one measured mark-in-a-mono-face defect is exactly that substitution.
        self._set_target_tile(self.t_ideal_gap, gap, IDEAL_GAP_TOOLTIP, text=f"{-gap:+.2f} s")

        shown = [r for r in rows if r.gain >= IDEAL_GAIN_FLOOR]
        rest = [r for r in rows if r.gain < IDEAL_GAIN_FLOOR]
        mono = theme.mono_font(theme.TABLE)

        def num(text: str) -> QTableWidgetItem:
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item.setFont(mono)
            return item

        t = self.ideal_table
        t.blockSignals(True)
        t.clearSelection()
        t.setRowCount(len(shown))
        for r, row in enumerate(shown):
            name = QTableWidgetItem(row.label)
            name.setData(RING_ROLE, row.ring_cid)
            cells = [name, num(f"{row.gain:.2f}"), num(f"{row.beat} / {row.n}"),
                     # 1-BASED, the app-wide display rule for a lap number (the ★ best lap, the
                     # Vmax tile's caption, the sparkline's x axis). None only on a POINT segment,
                     # which carries no gain and therefore never clears the floor above.
                     num(DASH if row.donor is None else str(row.donor + 1))]
            # WHY THIS ROW IS HERE, on the row. Neither visible column is sorted — the order is
            # their PRODUCT — and the app has been here before: the CORNERS table's loss column is
            # marked by σ × median-loss and read "as if it were ordered by its own numbers and was
            # not", which is recorded there as a defect a reader is given no way out of. The way
            # out is the arithmetic, per row, in the reader's own numbers.
            tip = (f"{row.label}: your best lap gave away {row.gain:.2f} s here. "
                   f"{row.beat} of {row.n} clean laps drove it at least as fast as your best lap "
                   f"did"
                   + ("" if row.donor is None else f"; the quickest was lap {row.donor + 1}")
                   + f". Ranked {r + 1} of {len(shown)} by {row.gain:.2f} × {row.beat}/{row.n} = "
                     f"{row.priority:.3f}.")
            for c, cell in enumerate(cells):
                cell.setToolTip(tip)
                t.setItem(r, c, cell)
        t.blockSignals(False)
        self._fit_table(t)
        # The remainder, always — the tile above states the WHOLE gap and the table shows part of
        # it, so without this line the page would print a total and a list that do not add up
        # (the same class of defect the digest tile's rounding note records at _set_digest).
        #
        # AND IT ADDS UP IN THE READER'S OWN NUMBERS, which is the rule `_set_digest` (L5-02)
        # already writes down 190 lines above and this line was breaking. The cells are printed at
        # 2 dp; summing the RAW floats made the note disagree with the column above it on 4 of 4
        # real recordings — D24 3 chapters printed 0.21 0.21 0.13 0.12 0.08 0.17 0.13 0.10 0.11
        # 0.14 (= 1.40) over a sentence saying 1.39, so a reader adding the visible cells and the
        # stated remainder got 1.65 under a tile printing -1.64 s. Sandown chapter 1 was off the
        # other way (0.92 printed, 0.94 stated). The three numbers here are therefore:
        #   shown_s — the SUM OF THE PRINTED CELLS, each rounded exactly as the table rounds it;
        #   gap_s   — the TILE's own number, rounded exactly as _set_target_tile rounds it;
        #   rest_s  — whatever closes the arithmetic, BY CONSTRUCTION rather than by agreement.
        # `rest_s` is a difference, not a second sum, for that last reason: summing the sub-floor
        # rows at 2 dp would give a number that happens to close on today's data and would not on
        # tomorrow's. A rounding penny can therefore land in the remainder — that is where it is
        # cheapest, since those rows are not on screen to contradict it.
        shown_s = round(sum(round(r.gain, 2) for r in shown), 2)
        gap_s = round(gap, 2)
        rest_s = round(gap_s - shown_s, 2)
        # The "Stitched from N of your M clean laps" sentence used to open this note and now opens
        # `ideal_sample` above the table, where it sits with the tiles it qualifies instead of
        # under ten rows of decomposition. One sentence, one place: printing the sample twice on
        # one block is how two surfaces drift apart.
        self.ideal_note.setText(
            f"These {len(shown)} segments hold {shown_s:.2f} s of the {gap_s:.2f} s; the other "
            f"{len(rest)} hold {rest_s:.2f} s between them, under "
            f"{IDEAL_GAIN_FLOOR:.2f} s each. Ranked by gain × how often you have already matched "
            "your best lap there.")

    def _set_ideal_visible(self, on: bool) -> None:
        """Show/hide the IDEAL LAP block as a UNIT — heading, both tiles, the sample line, the
        table and the remainder note. A tile left behind under a hidden heading is the defect this
        exists to prevent (it is what the old SECTORS placement would have produced the moment the
        two gates disagreed), and a sample line describing a composite that is not on screen is
        the same defect one widget over."""
        self._ideal_section.setVisible(on)
        self.t_theoretical.setVisible(on)
        self.t_ideal_gap.setVisible(on)
        self.ideal_sample.setVisible(on)
        self.ideal_table.setVisible(on)
        self.ideal_note.setVisible(on)

    def _on_ideal_row_selected(self):
        """A decomposition row rings the corner it names — the corner itself for a corner row,
        the corner FEEDING it for a straight (SegmentBests.ring_cid). Same corner_clicked
        pathway as the other three tables."""
        rows = self.ideal_table.selectionModel().selectedRows()
        if rows:
            item = self.ideal_table.item(rows[0].row(), 0)
            self.corner_clicked.emit(item.data(RING_ROLE) if item else None)
        else:
            self.corner_clicked.emit(None)

    def refresh_palette(self):
        """Re-render after a colour-blind-palette flip: the best-lap ★ row tint + the purple
        best-sector cells go through the palette accessors, so a re-render recolours them.
        Cheap — every service read is cached."""
        self.refresh()

    def set_speed_unit(self, unit: str):
        """Re-render the speed-bearing tiles/columns in the new display unit (View ▸ Units)."""
        unit = units.normalize_unit(unit)
        if unit == self._speed_unit:
            return
        self._speed_unit = unit
        self.refresh()

    # ------------------------------------------------------------------ groups
    def _show_no_laps_prose(self, on: bool) -> None:
        """Show/hide the zero-lap prose AND ITS OWN AIR, so neither can outlive the other.

        The indent is the layout's, not the label's: a QSS rule that reaches a QLabel rewrites
        that label's contents margins from the rule's own box (widgets.EmptyState carries the
        measurement), and nothing can reach a layout. But a layout's margins survive its only
        child being hidden — an empty row still asks for 2*SPACE_XS of page — so the margins go
        with the slot, which is the same rule widgets.EmptyState._show_slot follows and the same
        defect (a gap nobody could see and nobody had removed) it was written for.

        Indented by the banner's own accent rule plus its padding so the prose starts on the same
        pixel as the line above it and the two halves read as one block."""
        self.no_laps_prose.setVisible(on)
        if on:
            self._no_laps_prose_row.setContentsMargins(
                theme.SPACE_XXS + theme.SPACE_M, theme.SPACE_XS, theme.SPACE_M, theme.SPACE_XS)
        else:
            self._no_laps_prose_row.setContentsMargins(0, 0, 0, 0)

    def _set_no_laps_state(self, has_laps: bool):
        """With zero valid laps every PACE and SPEED · G tile can only render an em-dash, so hide
        both groups behind ONE explanatory block carrying the status bar's copy and the next
        action. SESSION stays (its recorded time/distance/clock are real, lap or no lap) and so
        does DATA TRUST — it is the diagnostic for why no lap was found. Reversible: a
        re-segmentation that finds laps restores every tile.

        The IDEAL LAP block is deliberately NOT listed here. It is not a tile-per-statistic group
        with a lap gate; it has its own composite gate in `_refresh_ideal`, which runs after this
        and which zero laps already fail (no best lap → no corner basis → no composite). Adding
        it here would be a second gate that agrees with the first only by luck."""
        self.no_laps_note.setVisible(not has_laps)
        self._show_no_laps_prose(not has_laps)
        for section in (self._pace_section, self._speed_section):
            section.setVisible(has_laps)
        for t in (self.t_best, self.t_median, self.t_race_pace, self.t_rolling, self.t_digest,
                  self.t_sigma, self.t_spread, self.t_cov, self.t_within, self.t_trend,
                  self.t_vmax, self.t_vmin, self.t_peak_lat, self.t_peak_brake):
            t.setVisible(has_laps)

    def _refresh_gg(self, st):
        cloud = st.gg_cloud() if st is not None else None
        plot = self.gg.getPlotItem()
        for ring in self._gg_rings:
            plot.removeItem(ring)
        self._gg_rings = []
        has = cloud is not None and len(cloud[0]) > 0
        self._gg_section.setVisible(has)
        self.gg.setVisible(has)
        self.gg_key.setVisible(has)
        if not has:
            self._gg_dots.setData([], [])
            return
        lat, lon = cloud
        # Identity (non-semantic) cloud colour — the first chart-series hue, translucent.
        colour = pg.mkColor(theme.CHART_SERIES[0])
        colour.setAlpha(GG_DOT_ALPHA)
        self._gg_dots.setData(np.asarray(lat), np.asarray(lon), brush=pg.mkBrush(colour))
        # Reference rings every 0.5 g out to the cloud's envelope, + hairline axes.
        r_max = float(np.ceil(max(np.max(np.abs(lat)), np.max(np.abs(lon))) / GG_RING_STEP)
                      ) * GG_RING_STEP
        r_max = max(r_max, GG_RING_STEP)
        angles = np.linspace(0.0, 2.0 * np.pi, 90)
        ring_pen = _gg_ring_pen()
        r = GG_RING_STEP
        while r <= r_max + 1e-9:
            ring = plot.plot(r * np.cos(angles), r * np.sin(angles), pen=ring_pen)
            self._gg_rings.append(ring)
            r += GG_RING_STEP
        for angle in (0, 90):
            line = pg.InfiniteLine(pos=(0, 0), angle=angle, pen=ring_pen, movable=False)
            plot.addItem(line)
            self._gg_rings.append(line)
        # The demonstrated grip envelope (p98 of combined g): a dashed accent ring — the
        # "ceiling you actually reached", vs the neutral 0.5 g reference rings.
        env = st.gg_envelope() if st is not None else None
        if env:
            env_pen = _gg_envelope_pen()
            ring = plot.plot(env * np.cos(angles), env * np.sin(angles), pen=env_pen)
            self._gg_rings.append(ring)
        # The key: the dashed ring is a MEASURED result, the solid ones a fixed rule — and the
        # picture alone cannot say which is which.
        self.gg_key.setText(
            f"dashed ring: your grip envelope, {env:.2f} g (p98 of combined g) · {GG_KEY_RINGS}"
            if env else GG_KEY_RINGS)
        # +0.0 for the origin reads as a signed measurement of nothing; the ends keep their sign
        # because on this plot the sign IS the direction (see the axis labels).
        ticks = [(v, f"{v:+.1f}" if v else "0") for v in (-r_max, 0.0, r_max)]
        plot.getAxis("left").setTicks([ticks])
        plot.getAxis("bottom").setTicks([ticks])
        pad = 0.1 * r_max
        plot.setXRange(-r_max - pad, r_max + pad, padding=0)
        plot.setYRange(-r_max - pad, r_max + pad, padding=0)

    def _refresh_driving(self, st, rows):
        brake = [r.brake_s for r in rows if r.brake_s is not None]
        counts = [r.brake_n for r in rows if r.brake_n is not None]
        coast = [r.coast_s for r in rows if r.coast_s is not None]
        has = bool(brake or counts or coast)
        self._driving_section.setVisible(has)
        for t in (self.t_brake, self.t_brake_n, self.t_coast, self.t_longest_coast,
                  self.t_grip_ceiling):
            t.setVisible(has)
        if not has:
            return
        self.t_brake.set(f"{np.median(brake):.1f} s" if brake else None)
        self.t_brake_n.set(f"{np.median(counts):.0f}" if counts else None)
        self.t_coast.set(f"{np.median(coast):.1f} s" if coast else None)
        longest = st.longest_coast_s() if st is not None else None
        self.t_longest_coast.set(f"{longest:.1f} s" if longest is not None else None)
        env = st.gg_envelope() if st is not None else None
        self.t_grip_ceiling.set(f"{env:.2f} g" if env is not None else None)

    def _refresh_sectors(self, session):
        sigmas = session.sector_sigmas() if hasattr(session, "sector_sigmas") else []
        has = bool(sigmas)
        self._sector_section.setVisible(has)
        self.sector_table.setVisible(has)
        if not has:
            self.sector_table.setRowCount(0)
            return
        bests = session.session_best_splits()
        medians = (session.sector_medians()
                   if hasattr(session, "sector_medians") else [None] * len(sigmas))
        best_colour = QColor(theme.best_sector_colour())
        self.sector_table.setRowCount(len(sigmas))
        for k in range(len(sigmas)):
            name = QTableWidgetItem(f"S{k + 1}")
            self.sector_table.setItem(k, 0, name)
            best = bests[k] if k < len(bests) else None
            best_item = self._num_item(fmt_time(best) if best is not None else DASH)
            if best is not None:
                # The purple session-best hue — and DELIBERATELY WITHOUT the ★ the same meaning
                # carries elsewhere (lap_table's best-lap cell and best-split cells, and this
                # page's own PACE list). The ★ exists where a tint picks ONE cell out of a column
                # of comparable ones: without a mark, "which of these 21 laps is the best" is
                # carried by hue alone and is lost in greyscale. Here the tint covers the WHOLE
                # column — every cell in it is a session best, because that is what the column IS —
                # so the meaning is already in the header, no cell is being distinguished from its
                # neighbours, and a ★ on all four rows would mark a tautology and devalue the mark
                # on the surfaces where it does work. What WAS missing is the sentence, so the cell
                # now says what it is on hover.
                # (tests/test_accessible_cues.py::test_lap_table_best_cells_carry_non_colour_star_marks
                # holds the column-wide/row-wise distinction, and asserts this column really is
                # column-wide rather than taking the claim on trust.)
                best_item.setForeground(best_colour)
                best_item.setToolTip(
                    f"Session-best S{k + 1} split — the fastest this sector was driven, in the "
                    "same purple the Laps tab paints on the lap that set it. The theoretical "
                    "best above is this column summed.")
            self.sector_table.setItem(k, 1, best_item)
            med = medians[k] if k < len(medians) else None
            self.sector_table.setItem(
                k, 2, self._num_item(fmt_time(med) if med is not None else DASH))
            sig = sigmas[k]
            self.sector_table.setItem(
                k, 3, self._num_item(f"{sig:.2f}" if sig is not None else DASH))
        self._fit_table(self.sector_table)

    def _corners_note_text(self, session, report) -> str:
        """The one line that connects this page's answers to each other, live.

        Every number here is READ, never baked: the same fix as the digest tile's, for the same
        reason — an empirical range typed into shipping copy is right on the recording it was
        measured on and quietly wrong on the next one. The Coaching total costs one
        `coaching_opportunities()` (~3-6 ms on the 38-lap D24 pair, on a refresh path that runs on
        load / re-segment / undo, never per tick); the alternative is printing a number this page
        cannot check, which is how two surfaces drift apart in the first place.
        """
        total = sum(r.median_loss_s for r in report if r.median_loss_s is not None)
        parts = [f"Med loss is measured against each corner's own Best above — the quickest "
                 f"anyone went through it, which is usually not your best lap's corner. "
                 f"Summed, that is {total:.2f} s."]
        opp_fn = getattr(session, "coaching_opportunities", None)
        opp = opp_fn() if opp_fn is not None else None
        # The Coaching tab's own totals are over its RANKED rows (the corners that survived its
        # per-corner evidence gate), so this reconciliation quotes the same set — a "totals X s"
        # here that included abstained corners would not reconcile with the page it names.
        rows = _ranked_shown(opp) if getattr(opp, "enough", False) else []
        if rows:
            top = rows[:PANEL_TOP_N]
            parts.append(
                f"The Coaching tab measures the SAME corners against your best lap and totals "
                f"{sum(r.time_lost for r in rows):.2f} s, "
                f"{sum(round(r.time_lost, 2) for r in top):.2f} s of it in its top {len(top)}.")
        gap = self._ideal_gap(session)
        if gap is not None:
            parts.append(f"Your ideal lap is {gap:.2f} s under your best.")
        parts.append("Different baselines, different questions — not three estimates of one.")
        return " ".join(parts)

    @staticmethod
    def _ideal_gap(session) -> float | None:
        """best lap − ideal total, or None when either half is missing (the honesty rule: no
        number rather than a 0.00 that reads as a measurement)."""
        ideal_fn = getattr(session, "ideal_total", None)
        best_fn = getattr(session, "best_lap_id", None)
        time_fn = getattr(session, "lap_time", None)
        if ideal_fn is None or best_fn is None or time_fn is None:
            return None
        ideal = ideal_fn()
        best_id = best_fn()
        if ideal is None or best_id is None:
            return None
        best = time_fn(best_id)
        return None if best is None else float(best) - float(ideal)

    def _refresh_corners(self, session, unit, u_label):
        report = getattr(session, "corner_report", list)() or []
        has = bool(report)
        self._corners_section.setVisible(has)
        self.corners_table.setVisible(has)
        self.corners_note.setVisible(has)
        phase = (getattr(session, "phase_report", lambda: None)() if has else None)
        phase_rows = (dict(zip(phase.cids, phase.rows, strict=True))
                      if phase is not None else {})
        self._refresh_phase_tiles(phase)
        if not has:
            self.corners_table.setRowCount(0)
            self.corners_note.setText("")
            return
        self._corners_section.setText(f"CORNERS · speeds in {u_label}")
        self.corners_note.setText(self._corners_note_text(session, report))
        # The worst corners by σ × median-loss get their loss cell MARKED and tinted in the
        # "behind" hue — erratic AND slow is where practice pays first. Capped at WORST_TINT_N and
        # at half the field: a tint that covers every row highlights nothing.
        k = min(WORST_TINT_N, max(1, len(report) // 2))
        ranked = sorted(report, key=lambda r: -r.score)[:k]
        worst = {r.cid: r for r in ranked if r.score > 0}
        behind = QColor(theme.behind_colour())
        mono = theme.mono_font(theme.TABLE)

        def cell(val, fmtstr):
            item = _NumItem(fmtstr.format(val) if val is not None else DASH)
            item.setData(NUM_ROLE, val)
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item.setFont(mono)
            return item

        t = self.corners_table
        t.setSortingEnabled(False)   # Qt requirement: never fill a live-sorting table
        t.blockSignals(True)
        t.clearSelection()
        t.setRowCount(len(report))
        for r, cr in enumerate(report):
            # The direction goes in the cell's ICON slot (lap_table.set_corner_direction), so the
            # sort key and the text stay the bare corner number.
            name = set_corner_direction(_NumItem(f"C{cr.cid}"), cr.direction)
            name.setData(NUM_ROLE, cr.cid)   # numeric key: C10 must not sort before C2
            t.setItem(r, 0, name)
            t.setItem(r, 1, cell(cr.best_s, "{:.2f}"))
            t.setItem(r, 2, cell(cr.median_s, "{:.2f}"))
            t.setItem(r, 3, cell(cr.sigma_s, "{:.2f}"))
            loss = cell(cr.median_loss_s, "+{:.2f}")
            # The tooltip is built in the SAME branch as the cue, so the reason can never be
            # missing from a cell that carries the mark. Two independent lines when both apply:
            # WHY THIS CELL IS MARKED (the ranking score, which is not a column on screen — a
            # reader comparing the marked +0.09 with the plain +0.11 above it has no other way to
            # find out) and the corner's own phase triple.
            tips = []
            wr = worst.get(cr.cid)
            if wr is not None:
                loss.setForeground(behind)
                loss.setText(WORST_LOSS_MARK + loss.text())
                tips.append(
                    f"One of the {len(worst)} worst corners to practise — ranked by "
                    f"σ × median loss = {wr.sigma_s:.2f} × {wr.median_loss_s:.2f} = "
                    f"{wr.score:.3f} s², not by this column alone.")
            tri = phase_rows.get(cr.cid)
            if tri is not None:
                # The corner's own phase matrix, on hover — where INSIDE this corner the
                # typical lap loses (positive = slower than best over that third).
                tips.append(f"Median vs best — entry {tri[0]:+.2f} · "
                            f"apex {tri[1]:+.2f} · exit {tri[2]:+.2f} s")
            if tips:
                loss.setToolTip("\n".join(tips))
            t.setItem(r, 4, loss)
            t.setItem(r, 5, cell(units.convert_speed(cr.apex_best_kmh, unit)
                                 if cr.apex_best_kmh is not None else None, "{:.1f}"))
            t.setItem(r, 6, cell(units.convert_speed(cr.apex_median_kmh, unit)
                                 if cr.apex_median_kmh is not None else None, "{:.1f}"))
            t.setItem(r, 7, cell(cr.grip_median * 100.0
                                 if cr.grip_median is not None else None, "{:.0f}"))
        t.blockSignals(False)
        t.setSortingEnabled(True)
        self._fit_table(t)

    def _refresh_phase_tiles(self, phase):
        """The where-the-time-goes headline tiles: percent of the lost corner time per phase
        + the seconds behind it. Hidden when there is no phase data (no corners / no best /
        nothing lost)."""
        tiles = (self.t_phase_entry, self.t_phase_apex, self.t_phase_exit)
        share = getattr(phase, "share", None)
        fr = share.fracs() if share is not None else None
        if fr is None:
            for t in tiles:
                t.setVisible(False)
            return
        secs = (share.entry_s, share.apex_s, share.exit_s)
        caps = ("lost on entry", "lost at apex", "lost on exit")
        for t, f, s, cap in zip(tiles, fr, secs, caps, strict=True):
            t.setVisible(True)
            t.set(f"{f * 100.0:.0f} %", f"{cap} · {s:.1f} s")

    def _refresh_braking(self, session):
        """The BRAKING table: one row per corner WITH a matched brake event (an unbraked
        kink adds noise, not signal). Same sort/click idiom as the CORNERS table."""
        report = [r for r in (getattr(session, "brake_report", list)() or []) if r.n > 0]
        has = bool(report)
        self._braking_section.setVisible(has)
        self.braking_table.setVisible(has)
        if not has:
            self.braking_table.setRowCount(0)
            return
        mono = theme.mono_font(theme.TABLE)

        def cell(val, fmtstr):
            item = _NumItem(fmtstr.format(val) if val is not None else DASH)
            item.setData(NUM_ROLE, val)
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item.setFont(mono)
            return item

        t = self.braking_table
        t.setSortingEnabled(False)
        t.blockSignals(True)
        t.clearSelection()
        t.setRowCount(len(report))
        for r, bc in enumerate(report):
            name = _NumItem(f"C{bc.cid}")
            name.setData(NUM_ROLE, bc.cid)
            t.setItem(r, 0, name)
            t.setItem(r, 1, cell(bc.n, "{:d}"))
            t.setItem(r, 2, cell(bc.sigma_m, "{:.1f}"))
            t.setItem(r, 3, cell(bc.span_m, "{:.1f}"))
            t.setItem(r, 4, cell(bc.commit_pct, "{:.0f}"))
            t.setItem(r, 5, cell(bc.metres_later_med, "{:+.1f}"))
        t.blockSignals(False)
        t.setSortingEnabled(True)
        self._fit_table(t)

    def _refresh_straights(self, session, unit, u_label):
        report = getattr(session, "straights_report", list)() or []
        # B8: a start line inside a corner section produces ~0-duration S/F stubs — noise
        # rows with no driving content (BRAKING already omits unmatched corners the same way).
        full_n = len(report)
        report = [st for st in report
                  if max(st.best_s or 0.0, st.median_s or 0.0) >= 0.05]
        stubs = full_n - len(report)
        has = bool(report)
        self._straights_section.setVisible(has)
        self.straights_table.setVisible(has)
        if not has:
            self.t_fix_first.setVisible(False)
            self.straights_table.setRowCount(0)
            return
        # SAY HOW MANY ARE NOT LISTED (§5.6). The ideal-lap disclosure on this same page counts
        # the PARTITION ("across the 12 corners and 13 straights pacer found here") while this
        # table silently drops the ~0-duration S/F stubs a start line inside a corner section
        # produces — so one page said 13 and showed 11, with nothing anywhere reconciling them.
        # The count is derived from the same list, so the two can never drift apart again.
        dropped = f" · {stubs} too short to list" if stubs else ""
        self._straights_section.setText(f"STRAIGHTS · speeds in {u_label}{dropped}")
        # The FIX FIRST tile: the biggest exit-deficit × straight-spread product.
        top = max(report, key=lambda s: s.leverage)
        if top.leverage > 0:
            self.t_fix_first.setVisible(True)
            self.t_fix_first.set(
                f"C{top.ring_cid}",
                f"fix first · exit {units.convert_speed(top.exit_delta_kmh, unit):+.1f} "
                f"{u_label} → +{top.median_s - top.best_s:.2f} s straight")
        else:
            self.t_fix_first.setVisible(False)
        mono = theme.mono_font(theme.TABLE)

        def cell(val, fmtstr):
            item = _NumItem(fmtstr.format(val) if val is not None else DASH)
            item.setData(NUM_ROLE, val)
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item.setFont(mono)
            return item

        t = self.straights_table
        t.setSortingEnabled(False)
        t.blockSignals(True)
        t.clearSelection()
        t.setRowCount(len(report))
        for r, st in enumerate(report):
            name = _NumItem(st.label)
            name.setData(NUM_ROLE, st.index)      # sort key: track order
            name.setData(RING_ROLE, st.ring_cid)  # the corner feeding this straight
            t.setItem(r, 0, name)
            t.setItem(r, 1, cell(st.best_s, "{:.2f}"))
            t.setItem(r, 2, cell(st.median_s, "{:.2f}"))
            t.setItem(r, 3, cell(st.sigma_s, "{:.2f}"))
            t.setItem(r, 4, cell(units.convert_speed(st.trap_best_kmh, unit)
                                 if st.trap_best_kmh is not None else None, "{:.1f}"))
            t.setItem(r, 5, cell(units.convert_speed(st.trap_median_kmh, unit)
                                 if st.trap_median_kmh is not None else None, "{:.1f}"))
            t.setItem(r, 6, cell(units.convert_speed(st.exit_delta_kmh, unit)
                                 if st.exit_delta_kmh is not None else None, "{:+.1f}"))
        t.blockSignals(False)
        t.setSortingEnabled(True)
        self._fit_table(t)

    def _on_straight_row_selected(self):
        """A straight row rings the CORNER FEEDING it (its exit sets the straight's story);
        same corner_clicked pathway as the other tables."""
        rows = self.straights_table.selectionModel().selectedRows()
        if rows:
            item = self.straights_table.item(rows[0].row(), 0)
            self.corner_clicked.emit(item.data(RING_ROLE) if item else None)
        else:
            self.corner_clicked.emit(None)

    def _on_brake_row_selected(self):
        """A BRAKING-table row is a corner too — emit the same corner_clicked the CORNERS
        table does (one map-ring pathway, maximize-aware in CentralView)."""
        rows = self.braking_table.selectionModel().selectedRows()
        if rows:
            item = self.braking_table.item(rows[0].row(), 0)
            self.corner_clicked.emit(item.data(NUM_ROLE) if item else None)
        else:
            self.corner_clicked.emit(None)

    def _on_corner_row_selected(self):
        """Emit the selected row's corner cid (None on deselect) — read from the row's own
        item (sorting reorders rows, so a row→cid list would go stale)."""
        rows = self.corners_table.selectionModel().selectedRows()
        if rows:
            item = self.corners_table.item(rows[0].row(), 0)
            self.corner_clicked.emit(item.data(NUM_ROLE) if item else None)
        else:
            self.corner_clicked.emit(None)

    @staticmethod
    def _on_corner_sort(_index, order):
        """Keep _NumItem's blanks-last convention through descending sorts (the lap-table
        idiom: the class flag flips before Qt reverses the order)."""
        _NumItem._descending = order == Qt.DescendingOrder

    def _refresh_trust(self, session):
        """The DATA TRUST card: what the numbers on this page are worth, one labelled FACT per row.

        The TRUST-BREAKING facts LEAD — an unconfirmed start line, an unknown track, laps left
        out of every statistic, in-lap GPS dropouts. Without them the card printed provenance only,
        and read identically on a session where all three were wrong and one where all three were
        fine. The provenance facts (clock, g source, cross-check) follow.

        EVERY SENTENCE HERE IS THE SHIPPED ONE. Each row is a (term, value) split of a line the
        card already printed — at the line's own colon where it had one, and at its verb where it
        did not ("Statistics use | 21 of the 22 laps found …") — so `_TrustCard.text()` re-joins
        into what the paragraph said. This change is the card's SHAPE, never its claims. Nothing
        was moved into a tooltip, and in particular the lateral GAIN stays on the surface — r is
        scale-invariant, so halving the g channel left the old card byte-identical while every g
        the app shows halved, and the gain is the number that moves."""
        rows: list[tuple[str, str, bool]] = []
        tips: list[str] = []
        valid = session.valid_lap_ids() if hasattr(session, "valid_lap_ids") else []
        # Gated on having laps, like the banner: with none, "every lap time below" refers to
        # nothing, and the empty-state block already makes placing the line the next action.
        if valid and not getattr(session, "timing_verified", True):
            rows.append(("Start/finish line",
                         "auto-fitted, not confirmed — every lap time and split below is "
                         "measured from an arbitrary point. Drag it on the map.", True))
        # "" (not None) as the getattr default: a test double that models no track at all must
        # not be reported as a recording whose track lookup FAILED.
        if getattr(session, "track_name", "") is None:
            rows.append(("Track",
                         "unknown — not in the track database, so the start/finish line "
                         "could not be placed for you.", True))
        excluded = getattr(session, "excluded_lap_ids", list)() or []
        if excluded:
            # Denominator = the laps the segmenter FOUND, not valid+excluded: a recording can
            # also carry slivers that never reached the ⊘ band at all, and "24 of 49" would be
            # arithmetic invented to make the two numbers meet. State both true counts instead.
            count = getattr(session, "lap_count", None)
            total = count() if callable(count) else len(valid) + len(excluded)
            rows.append(("Statistics use",
                         f"{len(valid)} of the {total} laps found — "
                         f"{len(excluded)} {EXCLUDED_MARK} excluded, their distance off the "
                         "session median (see the Laps tab).", True))
        # In-lap GPS dropouts: the ⚠ rule made visible — the count AND what it means for the
        # statistics on this page (those laps feed no best/σ/pace number). It moved UP here, with
        # the other three caveats: it is one, and it was the only one printed among the provenance.
        dropouts = session.dropout_lap_ids() if hasattr(session, "dropout_lap_ids") else set()
        if dropouts:
            rows.append(("GPS dropout",
                         f"inside {len(dropouts)} of {len(valid)} laps — "
                         f"flagged {DROPOUT_MARK} and left out of bests, σ and pace", True))
        quality = getattr(session, "timing_quality", None)  # a Session @property
        if quality is not None:
            clock = ("video clock (estimated)" if quality.media_clock
                     else "GPS9 true clock")
            # "of MOVING fixes" is not padding: the fraction is judged over the RETAINED MOVING
            # trace, deliberately (load.py:266-272 — the raw count includes the stationary
            # GPS-acquisition lead-in the pipeline trims, which flagged clean footage as
            # degraded purely on how many chapters were opened). Naming the population is the
            # fix; the number itself is the shipped one.
            rows.append(("Timing",
                         f"{clock} · {quality.dropped_pct()}% of moving fixes rejected", False))
            tips.append("The rejected-fix share is measured over the fixes taken WHILE MOVING. "
                        "The stationary lead-in before you drive off is trimmed by the loader "
                        "and left out of the verdict, so opening one chapter or all of them "
                        "gives the same answer.")
        if getattr(session, "has_gmeter", False):
            src = {"accl": "IMU", "gps": "GPS"}
            lat_src = src.get(session.gmeter_source(), session.gmeter_source())
            long_src = src.get(session.gmeter_long_source(), session.gmeter_long_source())
            rows.append(("g-meter",
                         f"{lat_src} lateral · {long_src}-derived longitudinal", False))
        else:
            # The card used to go SILENT about the g channel exactly when it is missing — while
            # the peak-g tiles, the per-lap g columns and the corner Grip % all render em-dashes
            # with no stated reason anywhere on the window. Split on NO_GMETER_NOTE's own "term:
            # value" colon so the constant stays the single source of that sentence.
            term, _, value = NO_GMETER_NOTE.partition(": ")
            rows.append((term, value, True))
        cross = session.gmeter_cross() if hasattr(session, "gmeter_cross") else None
        if cross is not None:
            verdict = "agree" if cross.ok else "DISAGREE"
            gain = getattr(cross, "lat_gain", None)
            gain_bit = f" · lateral gain ×{gain:.2f}" if gain is not None else ""
            rows.append(("IMU↔GPS cross-check",
                         f"{verdict} · lateral r={cross.lat_corr:+.2f}{gain_bit} · "
                         # Grouped: the cross-check's sample count is the only six-figure number
                         # the app prints, and "346713" is read digit by digit where "346,713" is
                         # read at a glance — the same reason every number on this page is set in
                         # the tabular stack.
                         f"longitudinal r={cross.long_corr:+.2f} · {cross.n:,} samples",
                         not cross.ok))
            tips.append(cross.summary())
            tips.append("Lateral gain is the IMU's lateral magnitude over the GPS-derived one: "
                        "×1 means the g you read is scaled right. The correlation beside it "
                        "cannot tell you that — Pearson r is unchanged by a scale error, so a "
                        "channel reading half would still correlate perfectly.")
        self.trust_card.set_rows(rows or [(DASH, DASH, False)])
        # Set unconditionally (both ways): a stale cross-check summary must not survive a
        # re-render onto a session that has none.
        self.trust_card.setToolTip("\n\n".join(tips))

    def _refresh_lap_table(self, session, rows, unit, u_label):
        has = bool(rows)
        self._laps_section.setVisible(has)
        self.lap_table.setVisible(has)
        self._laps_section.setText(f"PER LAP · speeds in {u_label}")
        # These Time cells are literally the laps the Laps tab already demotes, so demote them
        # identically here: muted + italic + the explaining tooltip while the start line is
        # PROVISIONAL or the clock is DEGRADED. Only the Time column — Vmax/Avg/g/brake are
        # measured whatever the start line is, and muting the whole table would tell the reader
        # nothing about which numbers the unverified line actually moves.
        verified = getattr(session, "timing_verified", True)
        quality = getattr(session, "timing_quality", None)
        timing_note = (PROVISIONAL_TOOLTIP if not verified
                       else estimated_timing_tooltip(quality)
                       if quality is not None and quality.degraded else "")
        best = session.best_lap_id() if hasattr(session, "best_lap_id") else None
        # The Laps tab suppresses the ★/best colour entirely while provisional (a "best" against
        # an arbitrary start line is meaningless). Match it, or this page would keep vouching for
        # a fastest lap in the very column it just muted.
        if not verified:
            best = None
        # C7: the page's DATA TRUST card says dropout laps are "flagged ⚠" — flag them HERE
        # too (the Laps tab already does), or the promise reads as broken.
        dropouts = session.dropout_lap_ids() if hasattr(session, "dropout_lap_ids") else set()
        self.lap_table.setRowCount(len(rows))
        best_colour = QColor(theme.best_lap_colour())
        for r, s in enumerate(rows):
            mark = BEST_LAP_MARK if s.idx == best else ""
            warn = DROPOUT_SUFFIX if s.idx in dropouts else ""
            lap_item = QTableWidgetItem(f"{mark}{s.idx + 1}{warn}")  # 1-based, the app-wide rule
            if warn:
                lap_item.setToolTip(DROPOUT_TOOLTIP)
            if s.idx == best:
                lap_item.setForeground(best_colour)
            self.lap_table.setItem(r, 0, lap_item)

            def num(v, fmtstr):
                return self._num_item(fmtstr.format(v) if v is not None else DASH)
            time_item = self._num_item(fmt_time(s.time))
            if timing_note:
                time_item.setForeground(PROVISIONAL_COLOR)
                theme.apply_provisional_style(time_item)
                time_item.setToolTip(timing_note)
            self.lap_table.setItem(r, 1, time_item)
            for c, kmh in ((2, s.vmax_kmh), (3, s.avg_kmh), (4, s.vmin_kmh)):
                self.lap_table.setItem(
                    r, c, num(units.convert_speed(kmh, unit)
                              if kmh is not None else None, "{:.1f}"))
            self.lap_table.setItem(r, 5, num(s.peak_lat_g, "{:.2f}"))
            self.lap_table.setItem(r, 6, num(s.peak_brake_g, "{:.2f}"))
            self.lap_table.setItem(r, 7, num(s.brake_s, "{:.1f}"))
            self.lap_table.setItem(r, 8, num(s.coast_s, "{:.1f}"))
        self._fit_table(self.lap_table)
