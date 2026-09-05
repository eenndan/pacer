"""LapTable: lap times / distances / entry speed. Multi-select rows to compare laps.

Cells sort by their numeric Qt.UserRole key, not text (so "1:08.408" sorts as 68.408 s), from the
mouse or the keyboard — the header is a tab stop with its own ring (_KeyboardSortHeader).
Row/cell highlights are keyed by lap id so they survive sorts: ▶ playing marker, green best
lap, blue Qt selection, purple per-sector session-best cells, ⚠ GPS-dropout flag. The
SESSION-BESTS footer is plain labels below the table, immune to sort/selection. A ⊘ EXCLUDED
strip below the table lists substantial laps the median band left out of the times/bests (a
mis-segmented short/long lap, an out-lap, or an in-lap) — kept out of the sortable rows so a
short excluded lap can't sort to the top as the "fastest". The strip has TWO tiers: a muted
one-liner for the odd stray lap, and a warning voice once the excluded share crosses
EXCLUDED_WARN_RATIO (QA L3-06 — half a session going missing read exactly like one lap going
missing).
"""

from __future__ import annotations

import math
import statistics
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QItemSelection, QItemSelectionModel, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QFrame,
    QHeaderView,
    QLabel,
    QScrollArea,
    QStackedWidget,
    QStyle,
    QStyleOptionHeader,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme, units
from ._signal import fmt_time, lap_label
from .widgets import NUM_ROLE, NumItem, set_tone

if TYPE_CHECKING:  # the injected session — typed for readers, not imported at runtime
    from .session import Session

BASE_COLOR = QColor(theme.C.text)             # default row text
# The two "best" foregrounds are PALETTE-DEPENDENT (green/purple by default, blue/teal in the colour-
# blind palette), so they're resolved per-refresh via theme.best_lap_colour()/best_sector_colour()
# rather than frozen at import — a palette flip then recolours the cells on the next refresh().
CURRENT_PREFIX = "▶ "  # current (playing) lap marker
DROPOUT_SUFFIX = " ⚠"  # GPS-dropout lap (low-confidence)
# NON-COLOUR redundancy for the "best" cues so they read without the green/purple hue (colour
# blindness / greyscale): a ★ marks the overall best lap's Lap cell and each session-best split
# cell. Paired with the existing bold, the star carries the meaning independent of colour.
BEST_LAP_MARK = "★ "     # prefixes the best lap's Lap cell (after any ▶ current marker)
BEST_SECTOR_MARK = " ★"  # suffixes a session-best split cell's value
# The ★ LEGEND (QA IA-07). ONE glyph, ONE convention — "the session best in this context", the rule
# the two marks above encode — but it was painted with an EMPTY tooltip on every cell that carried
# it, in two tables, and spelled out in exactly one header tooltip. So the cell wearing the mark
# answered nothing on hover, and the Corners table, which wears it too, said nothing anywhere.
# These state the same rule in each place the mark can appear; they are APPENDED to whatever trust
# note the cell already carries (dropout / provisional / estimated) rather than replacing it.
BEST_LAP_TIP = "★ Session best — the fastest complete lap in this recording."
BEST_SPLIT_TIP = "★ Session best — no lap crossed this sector quicker."
BEST_CORNER_TIP = "★ Session best — no lap took this corner quicker."
DROPOUT_TOOLTIP = "GPS dropout in this lap — its time, distance and map are less reliable."
# EXCLUDED laps: substantial laps the median band left OUT of the times / bests (a mis-segmented
# short/long lap, an out-lap, or an in-lap). They're shown in a muted strip BELOW the table rather
# than injected as rows — a short excluded lap would otherwise sort to the top as the "fastest" row
# and re-create the exact confusion the band filter removes. ⊘ reads as "left out" (distinct from
# the ⚠ dropout flag, which marks a lap that IS still counted).
EXCLUDED_MARK = "⊘"
EXCLUDED_TOOLTIP = (
    "These laps were left out of your times, bests and coaching. Their distance is off this "
    "session's median lap — usually a mis-segmented start/finish crossing, an out-lap, or an "
    "in-lap. If a real lap was dropped, drag the start/finish line on the map.")
# How many excluded laps the expanded list shows AT ONCE. It is a VIEWPORT height, not a cap: the
# list scrolls to the rest (QA L3-09 — the old hard cap listed 6 of 24 and spent its 7th line on a
# dead "+18 more" naming rows no surface in the app would ever show, while the expansion still cost
# four lap rows). Bounded height + a full list, instead of a bounded list.
EXCLUDED_MAX_SHOWN = 6
# The share of BANDED laps (valid + excluded) above which the strip stops whispering. Below it the
# excluded set is the odd stray lap and the muted one-liner is the right weight; above it the
# session's timing is substantially wrong (QA L3-06 measured 24 of 49 laps excluded — 49% — reported
# in the same 28px muted line as a single stray lap), so the strip states the ratio, compares the
# kept vs excluded distances and wears the attention amber the rest of the app warns in.
EXCLUDED_WARN_RATIO = 0.20
# ...and a floor on the COUNT, because a share alone would shout on a short recording: one stray
# out-lap of four is 25% but it is still one stray out-lap — the documented common case this strip
# was built for. Both conditions, or the warning voice stops meaning anything.
EXCLUDED_WARN_MIN = 3
# The ZERO-VALID-LAP wording, shared by the Laps grid's placeholder AND the Corners page's (QA
# L3-08: Corners showed the generic "Select a lap to see its corners." on a recording that HAS no
# selectable lap — an instruction that cannot be followed, beside a sibling placeholder in the same
# panel that stated the fact). One string, so the two pages can never drift.
NO_LAPS_TEXT = ("No complete laps in this recording.\n\n"
                "The GPS may not have locked, or the recording is too short to cross the "
                "start/finish line.")
# ...and the one NEXT ACTION both placeholders end on. It is deliberately NOT "drag the start/finish
# line": that call-to-action already lives on the map (the on-canvas cue + the trust strip), and a
# recording with no lap at all is usually the wrong recording, not a misplaced line.
NO_LAPS_ACTION = "Open another recording with ⌘O."
NO_LAPS_PLACEHOLDER = f"{NO_LAPS_TEXT}\n\n{NO_LAPS_ACTION}"


def _with_star(star_tip: str, tip: str) -> str:
    """The ★ meaning above whatever trust note the cell already carries, blank line between, so
    neither answer is lost (a dropout lap that is also the best lap has two things to say)."""
    return f"{star_tip}\n\n{tip}" if tip else star_tip


def _too_brief_note(n: int) -> str:
    """The sentence that accounts for detected laps in NEITHER the valid nor the excluded list —
    crossings the coarse gate rejected as slivers. "" for none, so it drops out of the join."""
    if n <= 0:
        return ""
    one = n == 1
    return (f"{n} other start/finish crossing{'' if one else 's'} "
            f"{'was' if one else 'were'} too brief to count as a lap.")
# L3: the most laps that can be overlaid on the speed/Δ charts at once. Beyond this the speed-plot
# legend silently overflows/truncates (laps past ~13 get no entry) and the curves blanket each other,
# so a larger selection is TRIMMED to the fastest MAX_COMPARE_LAPS — a visible cap in the table (the
# excess rows deselect), never a silent chart-side drop. 6 is the sensible side-by-side count.
MAX_COMPARE_LAPS = 6
PROVISIONAL_COLOR = QColor(theme.PROVISIONAL_COLOR)  # muted text for unverified timing
# A short, non-duplicative hint: the actionable "drag the start/finish line" call-to-action lives
# once on the map (the on-canvas cue + the trust strip), so the table tooltip just points there
# rather than repeating the whole sentence a third time.
PROVISIONAL_TOOLTIP = "Provisional timing — see the map to set the start/finish line."
# Degraded TIMING ACCURACY (the data-quality axis, orthogonal to the start-line trust above): the
# start line is fine but the per-sample clock is estimated (media-clock fallback) or many fixes
# were rejected, so the lap Time / S-split cells are demoted — muted like provisional, but the
# best/purple authority is NOT suppressed (the bests are still valid RELATIVE to each other; only
# the absolute timing accuracy is degraded). The tooltip copy is CLOCK-AWARE and derives from the
# SHARED data_quality.TimingQuality.detail() (the same source the map banner + header chip read),
# so the table and map can never disagree and the wording doesn't overclaim "estimated" on a
# true-clock recording whose only concern is rejected fixes (M3). A blank fallback keeps the lighter
# test doubles (which expose no timing_quality) working.
_ESTIMATED_TIMING_FALLBACK = ("Timing accuracy degraded — these times may be less accurate "
                              "(see the data-quality note over the map).")


def estimated_timing_tooltip(timing_quality) -> str:
    """The degraded-timing Time/footer tooltip for `timing_quality`, from the shared clock-aware
    detail() so the table matches the map banner + header chip. Falls back to a generic line if a
    (test-double) session exposes no detail()/summary()."""
    detail = getattr(timing_quality, "detail", None)
    text = detail() if callable(detail) else ""
    return text or _ESTIMATED_TIMING_FALLBACK
COLUMNS = ["Lap", "Time", "Dist (m)", "Entry (km/h)"]
_ENTRY_COL = len(COLUMNS) - 1  # the Entry-speed column (last base column); its header names the unit


def _lap_col_tips(unit: str | None) -> list[str]:
    """Full meaning + units per base header, shown on hover (1:1 with COLUMNS). A narrow panel
    ELIDES a header (see _fit_columns), so every header must carry its own full text somewhere —
    the same contract the Corners table has had since its headers were abbreviated."""
    u = units.speed_label(unit)
    return [
        "Lap number (▶ playing · ★ session best · ⚠ GPS dropout)",
        "Lap time, measured between start/finish crossings",
        "Lap distance (m), measured between start/finish crossings",
        f"Speed at the start/finish crossing ({u})",
    ]
# COLUMN SIZING (P5). The data columns are CONTENT-TIGHT and one blank trailing SPACER column
# absorbs every leftover pixel, so a wide panel keeps the real columns adjacent + left-packed
# instead of flinging the last one to the far right across a dead band (what setStretchLastSection
# did: on a wide panel the Entry column ballooned past 300px with its values pinned right).
# The spacer holds no cells; it exists purely to eat the slack (and to carry the alternating row
# stripe + row selection across the full table width). It is ALWAYS the last column, so it moves
# as the dynamic S-split columns come and go — see _n_real_cols / _apply_column_sizing.
SPACER_HEADER = ""
# How far the spacer may COLLAPSE when the panel has no slack to give it. Qt's default minimum
# section size (~17px here) is enough to push a table that would otherwise just fit into a
# horizontal scrollbar — the spacer must never be the thing that summons one. It only floors
# hand-dragged sections (the data columns size themselves), so a few pixels is safe.
MIN_SECTION_PX = 4
# The Lap column is Interactive with a fixed start width (the CornerTable precedent below), NOT
# ResizeToContents: its text carries the ▶ current-lap marker, which appears/disappears every lap
# during playback and would make a content-sized column visibly jitter. The width fits the widest
# decorated label — "▶ ★ 100 ⚠" — with room to spare. It is the row IDENTITY column, so the squeeze
# pass below never takes a pixel off it; only the GROW pass lets it share a wide panel's surplus.
LAP_COL_PX = 92
# COLUMN FITTING (QA L2-06 / L3-03 / L3-04). Content-tight columns alone fail at BOTH ends of the
# size range:
#   * too wide — a maximized Laps panel parked 1050 of its 1432px in the blank spacer, leaving a
#     78.6% empty screen (four columns totalling 382px);
#   * too narrow — the default 447px quadrant could not hold the Corners table's 501px of columns
#     (the "Grip (est)" header painted "Gr", 0 of 12 grip cells readable) nor the 609px the app's
#     own "Add sector" button creates (S2 and S3 at ZERO visible pixels).
# So both tables now FIT their columns to the panel: grow into surplus width up to MAX_DATA_COL_PX,
# and give slack back down to each column's own CELL width when the panel is short. Headers elide
# before values do — every header carries its full text as a tooltip (_lap_col_tips /
# _corner_col_tips), a cell's value has nowhere else to go.
# The cap is what keeps P5's finding fixed: the old stretched-last-section ballooned ONE column past
# 300px with its values pinned to the far right. 240px is wide enough to fill a maximized quadrant
# and narrow enough to keep every header over its own number.
MAX_DATA_COL_PX = 240
# How far a caption printed ABOVE or BELOW a grid has to be inset to start on the same pixel as the
# grid's own first column of text. It is not a chosen gap: the table reserves FOCUS_RING_PX of
# border so a keyboard ring can be painted without re-laying the grid out, and the QSS then pads
# every cell and every header section by SPACE_S — so the text a caption has to line up with begins
# at exactly their sum. Shipped as a hand-written 10 in both places that need it, which is that sum,
# and read as a nudge because nothing said so. Written as the derivation (theme.focus_pad's bargain:
# derive the number, keep the scale), it also survives a change to either half.
GRID_TEXT_INSET = theme.FOCUS_RING_PX + theme.SPACE_S


# How many of its OWN glyphs a header must keep once it elides. Two, because that is the number at
# which "Δbest" and "Δapex" stop being the same string — the pair this whole budget was re-tuned
# around (#163) — and it is the same threshold tests/test_lap_table_columns.py's elide guard has
# always used to decide when its own check applies.
HEADER_STEM_CHARS = 2
ELLIPSIS = "…"


def header_keeps_stem(fm: QFontMetrics, text: str, px: int,
                      chars: int = HEADER_STEM_CHARS) -> bool:
    """Does `text`, elided into a `px`-wide label box, still show `chars` of its OWN glyphs?

    The question `header_stem_px` answers, asked of Qt directly instead of predicted — the two are
    a pair, and the second is what makes the first's claim checkable rather than asserted."""
    shown = fm.elidedText(text, Qt.ElideRight, px)
    kept = shown[:-1] if shown.endswith(ELLIPSIS) else shown
    return len(kept) >= min(chars, len(text))


def header_stem_px(fm: QFontMetrics, text: str, chars: int = HEADER_STEM_CHARS) -> int:
    """The narrowest label width at which `text` still shows `chars` of its own glyphs.

    THIS WAS ONE PIXEL SHORT, and the pixel was not a rounding slip — it was a strict inequality
    read as a loose one. The docstring here used to claim the answer "is exactly the advance of
    `text[:k] + '…'` — no search, and exact against the same metrics Qt will elide with". Qt's
    ElideRight (QTextEngine::elidedText) walks the string accumulating glyph advances and stops at
    the first prefix whose width is NOT LESS THAN `width - ellipsisWidth`, then returns the prefix
    BEFORE it. So `advance(text[:k] + "…")` is the widest box at which the k-th glyph is LOST, not
    the narrowest at which it survives: the survivor needs one more pixel. `header_floors` asked
    for that width, the column was granted exactly it, and "Δbest" painted a bare "Δ…" beside a
    km/h column reading "Δa…" — A52's symptom, one pixel wide, over a 30 px band of window width
    (QA W14-02).

    Both terms are also ROUNDED integers of a fixed-point (1/64 px) metric, so "advance + 1" is
    guaranteed sufficient but can be a pixel generous. So the arithmetic is the STARTING POINT and
    `header_keeps_stem` — Qt's own elide, the one the header will run — is the answer: step up
    while the stem is not there yet, down while the pixel below still holds it. Monotone in `px`,
    so the walk terminates at the true minimum and normally does not step at all. Never more than
    a hair over the full string's advance (for a short label the ellipsis can cost more than the
    letters it replaces, and a rounded-down advance can still elide)."""
    if not text:
        return 0
    full = fm.horizontalAdvance(text)
    if len(text) <= chars:
        px = full
    else:
        px = min(full, fm.horizontalAdvance(text[:chars] + ELLIPSIS) + 1)
    # `full + 2` clears the rounding in either direction, so the ceiling always shows the whole
    # string and the upward walk cannot run away.
    lo, hi = 1, max(1, full + 2)
    px = max(lo, min(px, hi))
    while px < hi and not header_keeps_stem(fm, text, px, chars):
        px += 1
    while px > lo and header_keeps_stem(fm, text, px - 1, chars):
        px -= 1
    return px


def _header_pad_px(hdr) -> int:
    """The chrome a header section spends on everything that is not its text, from the STYLE.

    Read as `section - SE_HeaderLabel` off a nominal section rather than from the QSS literal:
    the padding is a stylesheet value, the sort indicator is a style metric, and the column budget
    has to reason about the box the text actually gets. Measured at 16 px on the shipped theme."""
    probe = 200
    opt = QStyleOptionHeader()
    hdr.initStyleOption(opt)
    opt.section = 0
    opt.rect = QRect(0, 0, probe, max(hdr.height(), 1))
    label = hdr.style().subElementRect(QStyle.SE_HeaderLabel, opt, hdr)
    return max(0, probe - label.width())


def header_floors(table, floors: list[int], natural: list[int], avail: int) -> list[int]:
    """`floors` raised so that every header still NAMES its column — while the table has the room.

    WHAT THIS CLOSES. This budget deliberately lets a squeezed header elide into its tooltip, and
    #163 made that safe for the one pair it is not safe for by naming them "Δbest"/"Δapex" — two
    labels whose elisions, "Δb…" and "Δa…", still differ. What was never true is that the table
    HAD those pixels: at 1280x800 the eight corner columns sit above their cell floors with 118 px
    of collective slack, and `_shrink_to` hands that slack out in PROPORTION. So the "b" in "Δb…"
    was never allocated to anything — it was change from a division. The design wave spent 2 px of
    table viewport (443 -> 441) on chrome, the division came out 1 px differently, and the column
    carrying lap-time deltas started painting a bare "Δ…": a header with no information in it
    beyond "this is a delta", next to a km/h column still reading "Δa…".

    So the stem is ASKED FOR instead of hoped for. Each column's floor is raised to the width its
    own header needs to keep HEADER_STEM_CHARS of itself (a font metric — see header_stem_px —
    plus the style's header chrome), capped at what the column naturally wants. The pixels come
    from the other columns' slack ABOVE THEIR OWN CELL FLOORS, which `_shrink_to` already refuses
    to cross, so no VALUE is squeezed to buy a header: this table's contract is unchanged.

    AND IT YIELDS RATHER THAN OVERFLOWS. A panel genuinely too narrow to honour every stem would
    otherwise push the sum past the viewport and summon the horizontal scrollbar that QA L3-03 was
    about. So the guarantee is "while there is room", stated, rather than "always", broken — and
    what "room" means is the three cases below.

    THE YIELD USED TO BE ALL-OR-NOTHING, AND THAT IS THE HALF THIS BUDGET GOT WRONG. One column
    the viewport could not afford dropped the stems of all EIGHT, which is how 973-1191 px of
    window — 174 px of it at and below the app's own minimum — painted a seconds column and a
    km/h column as the same bare "…". Measured on the shipped corner table (GX010062, dark theme,
    1 dpr): only three of the eight columns cost anything at all (Δbest +9, Δapex +8, "Grip (est)"
    +2 over their cell floors — the other five already stand above their own stems), the raised set
    wants 19 px more than the cell floors, and the viewport hands out between -2 and +105 px of
    slack across the band. So the yield is now GRADED, in three stated cases:

      1. Every stem fits — grant them all. Unchanged.
      2. The CELL floors alone already exceed the viewport. The horizontal scrollbar this budget
         yields to avoid is up ALREADY, for reasons that have nothing to do with headers (at 973 px
         the corner table's cell floors want 394 px of a 392 px viewport), so withholding the stems
         buys nothing whatsoever — it only costs every column its name. Grant them all; the scroll
         range grows by those 19 px and the scrollbar that was going to be there is there.
      3. There is slack, just not enough. Spend it, AMBIGUITY FIRST: a header whose label shares a
         prefix with another column's is the one whose elision can be mistaken for its neighbour's
         ("Δbest"/"Δapex" — the pair this whole budget was re-tuned around), so it is served before
         a header that is unmistakable however far it elides; among equals, the cheapest first, so
         the slack names as many columns as it can buy.

    WHAT CASE 3 STILL CANNOT DO, stated rather than papered over. Sweeping the corner table over
    every window width the app permits, 973 to 1440: the two Δ headers now paint the same string
    over 1147-1168 px and nowhere else — 22 px of window, down from 227 — because there the
    viewport's slack is 0-7 px and the cheaper of the two Δ stems costs 8. Closing that band would
    mean summoning the horizontal scrollbar ~50 px of window earlier than today to buy two glyphs,
    which is L3-03's defect traded for A52's, and the scrollbar is the more expensive of the two.
    Above it, 1169-1193 keeps the two apart ("…"/"Δa…") while Δbest is still short of its own stem,
    and from 1200 px up EVERY header in the table keeps its two glyphs. Below it — the 174 px from
    the app's own minimum to 1146 — case 2 now names all eight, where the old all-or-nothing yield
    named none. The scrollbar band is unchanged by all of this: measured before and after, the
    corner table's horizontal scrollbar appears over exactly 973-1146 either way."""
    hdr = table.horizontalHeader()
    fm = QFontMetrics(hdr.font())
    pad = _header_pad_px(hdr)
    labels, raised = [], []
    for c, floor in enumerate(floors):
        item = table.horizontalHeaderItem(c)
        text = item.text() if item is not None else ""
        labels.append(text)
        stem = (header_stem_px(fm, text) + pad) if text else 0
        raised.append(min(max(floor, stem), natural[c]))
    if sum(raised) <= avail or sum(floors) > avail:
        return raised                                   # cases 1 and 2
    budget = avail - sum(floors)                        # case 3
    out = list(floors)
    for c in sorted(range(len(floors)),
                    key=lambda c: (-_shared_prefix_chars(labels, c), raised[c] - floors[c])):
        cost = raised[c] - floors[c]
        if 0 < cost <= budget:
            out[c] = raised[c]
            budget -= cost
    return out


def _shared_prefix_chars(labels: list[str], c: int) -> int:
    """How many leading characters column `c`'s label shares with the LONGEST-matching other label
    in the same table — the priority key for a partial yield.

    Two headers can only be confused for each other once BOTH have eroded past what tells them
    apart, and the longer the prefix they share the sooner that happens: "Δbest"/"Δapex" become one
    string at one glyph, "Corner"/"Time" never do at any width. So this ranks the columns by how
    much they need the pixels, and it is a property of the LABELS — no widths, no layout, nothing
    circular."""
    best = 0
    for i, other in enumerate(labels):
        if i == c or not other or not labels[c]:
            continue
        n = 0
        for a, b in zip(labels[c], other, strict=False):   # a shared PREFIX: stop at the shorter
            if a != b:
                break
            n += 1
        best = max(best, n)
    return best


def _shrink_to(widths: list[int], floors: list[int], excess: int):
    """Take `excess` px off `widths` in place, proportionally to each column's slack above its
    floor, never below the floor. Stops early when every column is at its floor (the panel is
    genuinely too narrow — the horizontal scrollbar covers the rest)."""
    while excess > 0:
        idx = [i for i in range(len(widths)) if widths[i] > floors[i]]
        if not idx:
            return
        pool = sum(widths[i] - floors[i] for i in idx)
        moved = 0
        for i in idx:
            take = min(widths[i] - floors[i], max(1, round(excess * (widths[i] - floors[i]) / pool)),
                       excess - moved)
            widths[i] -= take
            moved += take
            if moved == excess:
                break
        if not moved:
            return
        excess -= moved


def _grow_to(widths: list[int], caps: list[int], surplus: int):
    """Share `surplus` px across `widths` in place, evenly, each column capped at its own cap.
    Leftover (every column capped) is the caller's — the lap table parks it in the blank spacer."""
    while surplus > 0:
        idx = [i for i in range(len(widths)) if widths[i] < caps[i]]
        if not idx:
            return
        moved = 0
        for i in idx:
            give = min(caps[i] - widths[i], max(1, surplus // len(idx)), surplus - moved)
            widths[i] += give
            moved += give
            if moved == surplus:
                break
        if not moved:
            return
        surplus -= moved


def fit_columns(natural: list[int], floors: list[int], caps: list[int], avail: int) -> list[int]:
    """The widths `natural` columns should take in `avail` px: grown (capped) when there is spare
    room, squeezed toward `floors` when there is not, unchanged when it already fits. Pure
    arithmetic — no Qt — so the layout contract is unit-testable."""
    widths = list(natural)
    total = sum(widths)
    if total > avail:
        _shrink_to(widths, floors, total - avail)
    elif total < avail:
        _grow_to(widths, caps, avail - total)
    return widths


def _columns(unit: str | None) -> list[str]:
    """The base column headers with the Entry column named in the current speed unit
    ("Entry (km/h)" / "Entry (mph)"). Length is invariant, so every len(COLUMNS) offset holds."""
    cols = list(COLUMNS)
    cols[_ENTRY_COL] = f"Entry ({units.speed_label(unit)})"
    return cols
# Columns 1.. (everything but the Lap column) hold numerics: right-align + tabular font so the
# digits column-align. The Lap column stays left/default.
NUMERIC_COL_START = 1


def align_headers_over_their_columns(table: QTableWidget, numeric_from: int = NUMERIC_COL_START):
    """Give every header the alignment of the COLUMN it labels — numbers right, identity left.

    Qt's ``QHeaderView.defaultAlignment`` is ``AlignCenter``, and a centred header over
    right-aligned digits does not label them. Measured FROM THE PIXELS of the shipped lap panel at
    1440x900: the ink of "Time" / "Dist (m)" / "Entry (km/h)" ended 39 / 40 / 42 px short of its
    section while the digits under it ended 13 / 12 / 12 px short — 26, 28 and 30 px of drift. And
    the drift is half the slack, so it GROWS with the column: at a maximized lap panel every data
    column takes MAX_DATA_COL_PX and each label floats ~100 px from its own numbers.

    The app already knew this and only half-applied it: the coaching table
    aligns each header to its own column and tests/test_coaching_panel_layout.py pins it with
    "a centred header floats off its data", so these two tables were disagreeing with a rule the
    app had already written down.

    Only the SIDE is taken from the column; the vertical centring is the header's own. Deliberately
    derived from the same ``numeric_from`` boundary the CELLS use rather than from a per-column
    table: the two tables here already right-align every cell from column 1 on, so a new column
    cannot arrive with its header and its values disagreeing.

    (tests/test_design_system.py::test_no_table_header_floats_off_its_data is where the rule lives
    for the app as a whole — it measures the lap, corner, coaching and library tables on the real
    view, which is the only place a FOURTH table's drift could be caught. A shared helper here
    would not have caught it: each of those tables builds its headers differently.)"""
    for c in range(table.columnCount()):
        item = table.horizontalHeaderItem(c)
        if item is None:
            continue
        side = Qt.AlignRight if c >= numeric_from else Qt.AlignLeft
        item.setTextAlignment(side | Qt.AlignVCenter)
# NUM_ROLE is imported from studio.widgets (which owns the shared numeric-sort cell) rather than
# re-declared: the role a cell STORES its key under and the role the comparison READS have to be
# the same value, and they were two independent `Qt.UserRole` literals in two files.
LAP_ROLE = Qt.UserRole + 1  # the lap id (stable across sorts), stored on the Lap cell

# The two stitched targets that used to sit in a SESSION-BESTS footer below this table
# (theoretical best / best rolling) now live on the Stats page, each beside the data it is derived
# from: `stats_panel.t_theoretical` inside SECTORS (whose bests it sums) and `t_rolling` in PACE.
# The footer cost this grid 63px — two lap rows — on every recording, could not be collapsed or
# hidden, and repeated numbers the Stats page was already the home for.


class _NumItem(NumItem):
    """The lap grid's numeric-sort cell: `widgets.NumItem`, subclassed for one reason.

    `_descending` is a CLASS attribute that LapTable writes before every sort, and the Library
    dialog's table sorts the same kind of cell. Sharing one class would have let the lap table's
    last sort direction decide where the Library put its blanks; a subclass gives each table its
    own copy of the flag and no other difference. The comparison itself — blanks last in BOTH
    directions — lives once, in widgets.NumItem, where it used to live twice.

    The module-level `_is_blank` moved with it (`widgets.is_blank`); tests/test_studio_features.py
    reads both this name and its `_descending` flag."""


# The keys that sort the focused column, i.e. the two a focused control is expected to activate on.
_SORT_KEYS = (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter)


class _KeyboardSortHeader(QHeaderView):
    """The Laps grid's horizontal header, usable from the KEYBOARD (QA U9-02).

    Sorting was a mouse-only feature: QHeaderView is `Qt.NoFocus`, so the focus ring never reached
    the header, and it has no key handling of its own — Space/Return/Enter/Right/Home left the
    indicator at (0, Ascending) even with focus forced onto it, while a real mouse press moved it
    in the same process. Nothing else in the app offered a way in either: no menu action mentions
    sort order and the header has no context menu.

    So the header becomes a real tab stop after the grid: ←/→ (and Home/End) walk the sortable
    sections, Space/Return/Enter sorts by the focused one — same column flips the direction, a new
    column starts ascending, exactly what `QHeaderView::mouseReleaseEvent` does — and the focused
    section paints the app's own focus ring, so a keyboard user can see which column they are about
    to sort by before they commit to it.

    `sortable(i)` is injected by the owner: the blank trailing SPACER column holds no cells, so
    walking onto it would park the ring on a header that cannot order anything (the same case
    `LapTable._on_sorted` bounces a mouse click out of)."""

    #: Which half of the split repaint is in flight (see paintSection), or None. Read by
    #: initStyleOptionForIndex, which is the only hook Qt offers for "draw this section without
    #: its sort arrow / without its label".
    _paint_pass = None

    def __init__(self, sortable):
        super().__init__(Qt.Horizontal)
        self._sortable = sortable
        self._focus_section = 0
        # QTableView builds its own header with both of these on; a replacement must carry them or
        # the MOUSE route (clickable sections) and the selected-column emphasis quietly disappear.
        self.setSectionsClickable(True)
        self.setHighlightSections(True)
        self.setFocusPolicy(Qt.StrongFocus)

    def _sections(self) -> list[int]:
        """The sections the keyboard may land on: visible, and able to order something."""
        return [c for c in range(self.count())
                if not self.isSectionHidden(c) and self._sortable(c)]

    def focusInEvent(self, event):
        # Arrive on the column that IS sorted, so the first Space flips the direction the user is
        # looking at instead of jumping to wherever the ring was left last time.
        if self.sortIndicatorSection() in self._sections():
            self._focus_section = self.sortIndicatorSection()
        super().focusInEvent(event)
        self.viewport().update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.viewport().update()

    def _move_focus(self, to):
        """Walk the ring to `to` ('first'/'last'/±1), clamped to the sortable sections."""
        cols = self._sections()
        if not cols:
            return
        if to == "first":
            i = 0
        elif to == "last":
            i = len(cols) - 1
        else:
            here = cols.index(self._focus_section) if self._focus_section in cols else 0
            i = min(max(here + to, 0), len(cols) - 1)
        self._focus_section = cols[i]
        self.viewport().update()

    def _sort_focused(self):
        """Sort by the focused section, exactly as a click on it would: the same column flips
        direction, a new one starts ascending. `setSortIndicator` is what actually re-orders the
        rows (QTableView listens to `sortIndicatorChanged`); `sectionClicked` follows so anything
        wired to a header click sees the keyboard route as one."""
        if self._focus_section not in self._sections():
            return
        flip = (self.sortIndicatorSection() == self._focus_section
                and self.sortIndicatorOrder() == Qt.AscendingOrder)
        self.setSortIndicator(self._focus_section,
                              Qt.DescendingOrder if flip else Qt.AscendingOrder)
        self.sectionClicked.emit(self._focus_section)

    def event(self, event):
        # Space is a WINDOW-level QShortcut (video play/pause). Shortcuts are matched BEFORE the
        # key event reaches the focused widget, so without claiming the ShortcutOverride the press
        # would toggle the video instead of sorting the column the ring is on. A focused control
        # owning its own activation key is the standard Qt answer to exactly this.
        if (event.type() == QEvent.ShortcutOverride and self.hasFocus()
                and event.key() in _SORT_KEYS):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        key = event.key()
        if key in _SORT_KEYS:
            self._sort_focused()
        elif key in (Qt.Key_Left, Qt.Key_Right):
            self._move_focus(-1 if key == Qt.Key_Left else 1)
        elif key in (Qt.Key_Home, Qt.Key_End):
            self._move_focus("first" if key == Qt.Key_Home else "last")
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def _section_option(self, rect, logicalIndex) -> QStyleOptionHeader:
        """A style option describing ONE section of this header, carrying the live sort indicator.
        (Qt maps AscendingOrder to SortDown — the arrow points at where the smallest value went.)"""
        opt = QStyleOptionHeader()
        self.initStyleOption(opt)
        opt.rect = rect
        opt.section = logicalIndex
        opt.orientation = Qt.Horizontal
        opt.state |= QStyle.State_Horizontal
        opt.sortIndicator = (QStyleOptionHeader.SortIndicator.SortDown
                             if self.sortIndicatorOrder() == Qt.AscendingOrder
                             else QStyleOptionHeader.SortIndicator.SortUp)
        return opt

    def _crowded_sort_arrow(self, rect, logicalIndex):
        """The sort indicator's rect when it would be painted ON TOP of this section's label —
        else None, which is every section this header had before the labels moved.

        THE DEFECT, in the two style paths that make it. Qt's own header keeps the arrow and the
        label apart in QCommonStyle::subElementRect(SE_HeaderLabel), which subtracts the arrow's
        width from the label's rect whenever a section carries the indicator. That subtraction is
        NOT REACHED under a stylesheet: QStyleSheetStyle answers SE_HeaderLabel with the section's
        CONTENTS rect the moment the section rule has a box, and this app's QHeaderView::section
        rule has padding and a bottom border. Centred labels never noticed — they are nowhere near
        either edge. A RIGHT-aligned label ends exactly where the arrow starts: measured on the
        shipped theme, "Time" in a 100 px section painted 161..191 with the arrow at 182..195, so
        the glyph landed across the last third of the word.

        Only the sorted section can collide, and only when its label is right-aligned; everything
        else takes the untouched path below."""
        if not self.isSortIndicatorShown() or logicalIndex != self.sortIndicatorSection():
            return None
        model = self.model()
        align = (model.headerData(logicalIndex, Qt.Horizontal, Qt.TextAlignmentRole)
                 if model is not None else None)
        if align is None or not int(align) & int(Qt.AlignRight):
            return None
        arrow = self.style().subElementRect(
            QStyle.SE_HeaderArrow, self._section_option(rect, logicalIndex), self)
        return arrow if arrow.isValid() else None

    def initStyleOptionForIndex(self, option, logicalIndex):
        """Qt's hook for "what does this section look like", used here to draw it in two passes."""
        super().initStyleOptionForIndex(option, logicalIndex)
        if self._paint_pass is not None:
            # The arrow is ours to place in this repaint; the style must not also draw it.
            option.sortIndicator = QStyleOptionHeader.SortIndicator.None_
            if self._paint_pass == "chrome":
                option.text = ""

    def paintSection(self, painter, rect, logicalIndex):
        arrow = self._crowded_sort_arrow(rect, logicalIndex)
        if arrow is None:
            super().paintSection(painter, rect, logicalIndex)
        else:
            # TWO PASSES OVER ONE SECTION, so that nothing here re-types a colour, a font or a
            # border that theme.py owns. Pass 1 draws the section with NO label: full-width
            # background and the bottom hairline, exactly as the stylesheet paints them. Pass 2
            # draws the same section into a rect that stops short of the arrow, which is what moves
            # the right-aligned label — and it re-paints identical background pixels on the way, so
            # the overdraw is invisible by construction rather than by luck. Then the arrow itself,
            # through the style's own PE_IndicatorHeaderArrow at the rect the style chose for it:
            # the indicator does not move, the label does.
            label = QRect(rect)
            label.setRight(arrow.left() - 1 - theme.SPACE_XXS)
            try:
                self._paint_pass = "chrome"
                super().paintSection(painter, rect, logicalIndex)
                self._paint_pass = "label"
                # An invalid (negative-width) rect is a no-op in Qt: a section too narrow to hold
                # both loses the label, never the indicator that says how the grid is ordered.
                super().paintSection(painter, label, logicalIndex)
            finally:
                self._paint_pass = None
            opt = self._section_option(rect, logicalIndex)
            opt.rect = arrow
            self.style().drawPrimitive(QStyle.PE_IndicatorHeaderArrow, opt, painter, self)
        # The app's one focus language (theme's FOCUS_RING_PX accent_hover ring), drawn on the
        # SECTION rather than the whole header: which column Space would sort by is the thing a
        # keyboard user needs to see. Inset so the ring sits inside the section it marks.
        if self.hasFocus() and logicalIndex == self._focus_section:
            painter.save()
            painter.setPen(QPen(QColor(theme.C.accent_hover), theme.FOCUS_RING_PX))
            half = theme.FOCUS_RING_PX // 2
            painter.drawRect(rect.adjusted(half, half, -half - 1, -half - 1))
            painter.restore()


class _ExcludedStrip(QWidget):
    """The ⊘ excluded-laps strip container whose WHOLE surface is a click target: a left-click
    toggles it between the collapsed one-liner and the full list (via the injected ``on_click``).
    A plain, muted info strip — deliberately NOT a sortable lap row (a short excluded lap injected
    as a row would sort to the top as the 'fastest')."""

    def __init__(self, on_click):
        super().__init__()
        # The strip's only chrome is the top hairline QWidget#LapExcludedStrip declares, and a
        # QWidget SUBCLASS does not paint the QSS box unless told to (see the note in
        # widgets.PanelHeader.__init__): the rule was matching and the separator was simply absent,
        # so the strip ran into the lap grid above it with nothing between them.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._on_click = on_click
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_click()
            event.accept()
            return
        super().mousePressEvent(event)


class LapTable(QWidget):
    laps_selected = Signal(object)  # list[int]

    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self._current_lap = None  # the lap on the video (independent of selection)
        # Speed display unit (km/h default); the app pushes the persisted choice via set_speed_unit.
        # Drives the Entry column header + the Entry value conversion (a display-only concern —
        # session.lap_rows still returns km/h).
        self._speed_unit = units.DEFAULT_UNIT
        # Highlight caches filled by refresh(): per-column best splits + dropout lap ids + the
        # overall-best lap id (so the ★ best-lap mark on the Lap cell survives sorts/current-lap
        # rewrites, which go through _lap_cell_text).
        self._best_split: list = []
        self._dropout_ids: set = set()
        self._best_lap_id = None
        # How many S-split columns the last refresh() drew, so a NEW one can be scrolled into view
        # once (L3-04) without every other refresh yanking the horizontal scroll.
        self._n_splits_shown = 0

        self.table = QTableWidget(0, len(COLUMNS))
        # U9-02: the sort header is a KEYBOARD control too (see _KeyboardSortHeader). Installed
        # before anything else configures the header, so every setting below lands on this one;
        # `sortable` keeps the keyboard walk off the blank trailing spacer column.
        self.table.setHorizontalHeader(_KeyboardSortHeader(lambda c: c < self._n_real_cols()))
        # ...and Tab must be able to LEAVE the grid to reach it. Qt's default cell-wise Tab
        # navigation swallowed every Tab press inside the table, which is why a focusable header
        # alone would still have been unreachable. A read-only grid (NoEditTriggers) has nothing to
        # tab between cells for; the arrow keys still walk the rows.
        self.table.setTabKeyNavigation(False)
        self.table.setHorizontalHeaderLabels(_columns(self._speed_unit))
        align_headers_over_their_columns(self.table)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # P5: no stretched last section — the data columns are content-tight and the blank trailing
        # spacer column takes the slack (see SPACER_HEADER / _apply_column_sizing). The Lap column's
        # start width is set once here so a later user drag survives every refresh().
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setMinimumSectionSize(MIN_SECTION_PX)
        # QHeaderView defaults to ElideNone, so a squeezed header CENTRE-CLIPS its own label
        # ("Dist (m)" -> "ist (m", "Grip (est)" -> "rip (est") with no cue that anything is
        # missing — the exact silent-truncation shape L3-03 filed. Elide it instead; the full text
        # is on the header tooltip (_lap_col_tips / _corner_col_tips).
        self.table.horizontalHeader().setTextElideMode(Qt.ElideRight)
        # Qt falls back to the DEFAULT section size for a Stretch section when there's nothing left
        # to stretch (a table already wide enough to scroll), which would append a phantom 100px of
        # empty scroll range past the last data column. Every data column sizes itself, so the
        # default only ever applies to the spacer — make it collapse instead.
        self.table.horizontalHeader().setDefaultSectionSize(MIN_SECTION_PX)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.horizontalHeader().resizeSection(0, LAP_COL_PX)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self._num_font = theme.mono_font(theme.TABLE)
        # Default sort = lap# ascending; remembered across refreshes, re-applied after each sort.
        self._sort_col = 0
        self._sort_order = Qt.AscendingOrder
        self.table.setSortingEnabled(True)
        hdr = self.table.horizontalHeader()
        hdr.setSortIndicatorShown(True)
        hdr.setSortIndicator(self._sort_col, self._sort_order)
        hdr.sortIndicatorChanged.connect(self._on_sorted)
        self.table.itemSelectionChanged.connect(self._on_selection)
        self.table.viewport().installEventFilter(self)   # re-fit on every real width change

        # Empty state: zero valid laps would show a blank grid, so stack a placeholder and flip to
        # it in refresh().
        self._empty = QLabel(NO_LAPS_PLACEHOLDER)
        self._empty.setProperty("role", "EmptyState")
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setWordWrap(True)
        self._stack = QStackedWidget()
        self._stack.addWidget(self.table)   # index 0: the populated table
        self._stack.addWidget(self._empty)  # index 1: the empty-state placeholder

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        # Stretch 1 on the lap grid: the excluded strip keeps its compact size and every extra
        # pixel of panel height becomes visible lap rows (without this, Qt split spare height
        # between them and the grid never grew).
        lay.addWidget(self._stack, 1)
        lay.addWidget(self._build_excluded_strip())
        self.refresh()

    # ------------------------------------------------------------------ build
    def _build_excluded_strip(self) -> QWidget:
        """A muted strip listing laps LEFT OUT of the times/bests by the median band (see
        EXCLUDED_MARK). COLLAPSED by default to a single muted one-liner ("⊘ N excluded ▸"); a
        click on the header expands it to the full per-lap list and back. Hidden entirely when there
        are none (the clean, common case), so it adds no chrome to a normal recording. Kept OUT of
        the sortable table on purpose — a short excluded lap injected as a row would sort to the top
        as the 'fastest' and re-create the very confusion the band filter removes.

        The header is a click target (a plain muted info strip, NOT a sortable lap row): clicking it
        toggles the collapse. The whole strip's VISIBILITY is a separate, orthogonal concern — the
        View ▸ Show excluded laps menu toggle (set_excluded_visible) and the auto-hide when there
        are no excluded laps — so a menu-hidden strip stays hidden regardless of collapse state."""
        strip = _ExcludedStrip(self._toggle_excluded_collapsed)
        strip.setObjectName("LapExcludedStrip")   # the top hairline is the theme's rule now
        box = QVBoxLayout(strip)
        # GRID_TEXT_INSET horizontally, so "⊘ N excluded" starts on the same pixel as the lap
        # numbers directly above it — the same derivation the corner captions take, and the reason
        # the shipped 10 was never really a nudge.
        #
        # SPACE_XS vertically, top and bottom. It shipped 6/8, a chosen gap with nothing choosing
        # it, and the obvious tidy — one symmetric SPACE_S inset — was measured before it was
        # believed: it grows the strip 28 -> 30 px and the lap grid loses a visible row (14 -> 13 at
        # a 465 px panel). Every pixel this strip takes is a lap row, and it is a collapsed one-line
        # footnote about laps that are NOT in the table, so the tightest step between two separate
        # things is the right one: the strip goes 28 -> 22 px and the grid 437 -> 443, which keeps
        # its 14 rows with 6 px to spare.
        box.setContentsMargins(GRID_TEXT_INSET, theme.SPACE_XS, GRID_TEXT_INSET, theme.SPACE_XS)
        box.setSpacing(theme.SPACE_XXS)
        # The collapsed one-liner header ("⊘ N excluded of M laps ▸"): muted, uppercase section
        # type, with the ▸/▾ chevron glyph telling which way a click goes. Text (and, above
        # EXCLUDED_WARN_RATIO, its amber warning colour) is set live by _refresh_excluded.
        self._excluded_header = QLabel("")
        self._excluded_header.setObjectName("LapExcludedHeader")
        self._excluded_header.setProperty("role", "BarLabel")
        self._excluded_header.setToolTip(EXCLUDED_TOOLTIP)
        # The WARNING-tier note (hidden below the ratio threshold): the kept-vs-excluded distance
        # comparison and the count reconciliation, ON the strip rather than only in its tooltip —
        # at 49% excluded the one number that explains the session is not hover-only detail.
        self._excluded_note = QLabel("")
        self._excluded_note.setWordWrap(True)
        self._excluded_note.setToolTip(EXCLUDED_TOOLTIP)
        self._excluded_note.setVisible(False)
        self._excluded_body = QLabel("")
        self._excluded_body.setWordWrap(True)
        self._excluded_body.setAlignment(Qt.AlignTop)
        self._excluded_body.setToolTip(EXCLUDED_TOOLTIP)
        # Muted + italic — the provisional/degraded treatment used everywhere else for
        # de-emphasised timing, so "not counted" reads consistently. It is the theme's
        # QLabel#LapExcludedBody rule, which reads PROVISIONAL_COLOR instead of re-spelling it.
        self._excluded_body.setObjectName("LapExcludedBody")
        # The expanded list SCROLLS (L3-09): every excluded lap is listed, but the strip's height
        # stays bounded at EXCLUDED_MAX_SHOWN lines so expanding it costs the lap grid the same few
        # rows whether 6 laps were excluded or 60. Frameless + no horizontal bar: it must read as
        # part of the strip, not as a widget dropped into it.
        self._excluded_scroll = QScrollArea()
        self._excluded_scroll.setObjectName("LapExcludedList")
        self._excluded_scroll.setWidget(self._excluded_body)
        self._excluded_scroll.setWidgetResizable(True)
        self._excluded_scroll.setFrameShape(QFrame.NoFrame)
        self._excluded_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._excluded_scroll.setSizeAdjustPolicy(QAbstractScrollArea.AdjustToContents)
        self._excluded_scroll.viewport().setAutoFillBackground(False)
        box.addWidget(self._excluded_header)
        box.addWidget(self._excluded_note)
        box.addWidget(self._excluded_scroll)
        self._excluded_strip = strip
        # Two orthogonal flags: the median band produced excluded laps AND the user hasn't hidden the
        # strip via the View menu. Default collapsed (the one-liner) + shown (when there are any).
        self._excluded_collapsed = True     # the ⊘ N excluded ▸ one-liner (click to expand)
        self._excluded_menu_visible = True  # the View ▸ Show excluded laps toggle
        strip.setVisible(False)
        return strip

    def _toggle_excluded_collapsed(self):
        """Header-click handler: flip the excluded strip between the collapsed one-liner and the
        full per-lap list, then re-render. A plain info-strip affordance — it never selects a lap
        or touches the sortable table."""
        self._excluded_collapsed = not self._excluded_collapsed
        self._refresh_excluded()

    def set_excluded_visible(self, on: bool):
        """View ▸ Show excluded laps: fully show/hide the ⊘ excluded strip (header included). Kept
        distinct from the header's own collapse: this is the "hide it entirely" menu toggle, so a
        hidden strip stays hidden regardless of collapse state (and the strip is still auto-hidden
        when the session has no excluded laps). Driven by the window's persisted choice."""
        self._excluded_menu_visible = bool(on)
        self._refresh_excluded()

    def _unbanded_count(self, banded: int) -> int:
        """Detected laps that are in NEITHER list: start/finish crossings too brief to clear the
        coarse gate (`_signal._banded_out_lap_ids`: fewer than MIN_LAP_SAMPLES points or shorter
        than MIN_LAP_TIME). QA L3-06: the panel showed 25 valid rows and "24 excluded" on a
        recording whose lap_count() is 50, so the two visible numbers did not add up to the third
        and the missing one was unexplained. getattr-guarded for the lighter test doubles."""
        lap_count = getattr(self.session, "lap_count", None)
        return max(0, lap_count() - banded) if callable(lap_count) else 0

    def _excluded_headline(self, n: int, banded: int, warn: bool) -> str:
        """The one-liner: the count RECONCILED against the banded total (so "24 excluded" is visibly
        24 of 49, not 24 of the 25 rows above it), the share once the strip escalates, and the
        chevron saying which way a click goes (▸ expand / ▾ collapse)."""
        chevron = "▸" if self._excluded_collapsed else "▾"
        share = f" ({n / banded:.0%})" if warn else ""
        return f"{EXCLUDED_MARK} {n} excluded of {banded} laps{share} {chevron}"

    def _excluded_warning(self, kept: list, rows: list, unbanded: int) -> str:
        """The warning-tier note: the kept-vs-excluded DISTANCE comparison — the one measurement
        that says whether the start/finish line is in the wrong place (QA L3-06 measured a kept
        median of 203 m against an excluded median of 536 m, 2.6x, nowhere on screen and not even
        in the tooltip) — plus the leftover brief crossings, so every detected lap is accounted for.
        Ends on the map's own recovery action, the wording this strip's tooltip already uses."""
        parts = []
        if kept:
            parts.append(f"Counted laps run ~{statistics.median([r['dist'] for r in kept]):.0f} m; "
                         f"these run ~{statistics.median([r['dist'] for r in rows]):.0f} m.")
        parts.append(_too_brief_note(unbanded) if unbanded else "")
        parts.append("If a real lap was dropped, drag the start/finish line on the map.")
        return " ".join(p for p in parts if p)

    def _refresh_excluded(self, kept: list | None = None):
        """Populate / hide the excluded-laps strip from Session.excluded_lap_rows (getattr-guarded
        so the lighter test doubles, which don't expose it, simply show no strip). COLLAPSED (the
        default) shows just the "⊘ N excluded of M laps ▸" one-liner; EXPANDED adds one line per
        excluded lap ("Lap 47 — 0:59.091 · 921 m") in a scroll viewport EXCLUDED_MAX_SHOWN lines
        tall. Past the warning tier (EXCLUDED_WARN_MIN + EXCLUDED_WARN_RATIO) the header switches
        to the warning voice and the note below it becomes visible in BOTH states. The whole strip hides when there are none OR when the
        View-menu toggle hid it. `kept` is refresh()'s already-fetched valid-lap rows (the ratio's
        denominator); None re-reads them."""
        rows = getattr(self.session, "excluded_lap_rows", lambda: [])()
        # Shown only when there ARE excluded laps AND the user hasn't hidden the strip via the menu.
        self._excluded_strip.setVisible(bool(rows) and self._excluded_menu_visible)
        if not rows:
            self._excluded_header.setText("")
            self._excluded_note.setVisible(False)
            self._excluded_body.clear()
            self._excluded_scroll.setVisible(False)
            return
        kept = self.session.lap_rows() if kept is None else kept
        banded = len(kept) + len(rows)
        unbanded = self._unbanded_count(banded)
        # TIER: the odd stray lap whispers; a session losing several laps AND more than
        # EXCLUDED_WARN_RATIO of them warns. The share is ALWAYS in the words as well as the colour,
        # so the escalation survives greyscale and colour blindness.
        warn = len(rows) >= EXCLUDED_WARN_MIN and len(rows) / banded > EXCLUDED_WARN_RATIO
        self._excluded_header.setText(self._excluded_headline(len(rows), banded, warn))
        # A STATE, so a dynamic property + a re-polish rather than a stylesheet swapped in and out:
        # see the theme's QLabel#LapExcludedHeader[tone="warn"] rule.
        set_tone(self._excluded_header, "warn" if warn else None)
        # The header carries the amber; the note stays PRIMARY text, following #ProvisionalBanner's
        # convention (the container signals, the sentence reads) — three amber lines would shout
        # over the map's actual call-to-action.
        self._excluded_note.setText(self._excluded_warning(kept, rows, unbanded) if warn else "")
        self._excluded_note.setVisible(warn)
        # The full list shows only when expanded; collapsed, the header (+ any warning) is all.
        # Every excluded lap is listed — the viewport height, not the list, is what is capped.
        self._excluded_scroll.setVisible(not self._excluded_collapsed)
        if self._excluded_collapsed:
            self._excluded_body.clear()
            return
        # 1-based lap number (lap_label) so the excluded strip matches the table's Lap column.
        self._excluded_body.setText("\n".join(
            f"Lap {lap_label(r['idx'])} — {fmt_time(r['time'])} · {r['dist']:.0f} m"
            for r in rows))
        # Bound the strip to EXCLUDED_MAX_SHOWN lines; a shorter list keeps its natural height, so
        # the scrollbar only appears when there is genuinely more to reach.
        line = QFontMetrics(self._excluded_body.font()).lineSpacing()
        self._excluded_scroll.setMaximumHeight(line * EXCLUDED_MAX_SHOWN + 4)


    def _n_split_cols(self) -> int:
        """Number of S-split columns: sector_count()+1 if any sector lines, else 0."""
        n = self.session.sector_count()
        return n + 1 if n else 0

    def _n_real_cols(self) -> int:
        """The DATA columns: the base COLUMNS + today's S-splits. The blank spacer column sits at
        exactly this index (it's always last), so every caller stays right as sectors change."""
        return len(COLUMNS) + self._n_split_cols()

    def _apply_column_sizing(self):
        """Panel-fitted data columns + a blank spacer holding whatever they leave. Re-applied after
        every column-count change: Qt gives newly-added sections the header's default mode, so a
        fresh S-column (or a shifted spacer) would otherwise keep the old sizing.

        EVERY section is Interactive, spacer included, so _fit_columns can set an explicit width (a
        ResizeToContents section refuses one, and a Stretch spacer falls back to its own size hint
        — 42-53px of dead scroll range — exactly when the data columns are already overflowing)."""
        hdr = self.table.horizontalHeader()
        for c in range(self._n_real_cols() + 1):
            hdr.setSectionResizeMode(c, QHeaderView.Interactive)
        self._fit_columns()

    def _column_budget(self) -> tuple[list[int], list[int], list[int]]:
        """(natural, floors, caps) for the data columns.

        `natural` is what a column needs to show its header AND its values uncut;
        `floors` is what its VALUES alone need (a squeezed header elides and falls back to the
        tooltip _lap_col_tips gives it — a number has no such fallback);
        `caps` bound the grow pass. The Lap column is the row identity: pinned at LAP_COL_PX so
        the ▶/★/⚠ markers never jitter or clip, and never squeezed."""
        hdr = self.table.horizontalHeader()
        real = self._n_real_cols()
        cells = [self.table.sizeHintForColumn(c) if self.table.rowCount() else 0
                 for c in range(real)]
        natural = [max(hdr.sectionSizeHint(c), cells[c]) for c in range(real)]
        floors = [max(MIN_SECTION_PX, cells[c]) for c in range(real)]
        natural[0] = floors[0] = LAP_COL_PX
        return natural, floors, [max(n, MAX_DATA_COL_PX) for n in natural]

    def _fit_columns(self):
        """Size the data columns to the panel (see MAX_DATA_COL_PX). Runs after every refresh and
        on every viewport resize, so maximizing the panel widens the DATA instead of the blank
        spacer (L2-06) and a narrow panel keeps its S-split columns on screen (L3-04)."""
        hdr = self.table.horizontalHeader()
        real = self._n_real_cols()
        # MIN_SECTION_PX is reserved for the spacer: it must never be the section that summons a
        # horizontal scrollbar, so the data columns are fitted to the width that is left after it.
        width = self.table.viewport().width()
        avail = width - MIN_SECTION_PX
        if avail <= 0 or real <= 0:
            return
        natural, floors, caps = self._column_budget()
        # ...with every column at least wide enough for its header to keep naming it, while the
        # panel can afford that (header_floors). The spacer is not in these lists and has no label.
        fitted = fit_columns(natural, header_floors(self.table, floors, natural, avail), caps, avail)
        for c, w in enumerate(fitted):
            hdr.resizeSection(c, w)
        # The spacer takes EXACTLY the slack the capped data columns left (P5: keep them adjacent
        # and left-packed instead of flinging the last one across a dead band), and collapses to
        # MIN_SECTION_PX when there is none.
        hdr.resizeSection(real, max(MIN_SECTION_PX, width - sum(fitted)))

    def eventFilter(self, obj, event):
        # The table's VIEWPORT, not this widget: the Corners/Laps pages are laid out inside a tab
        # stack, so a page can be given its real width without this container ever seeing a
        # resizeEvent — which is how the corner columns came to be fitted to Qt's stock 640px
        # default and overflowed the 447px quadrant by MORE than before the fit existed.
        # getattr, not self.table: a dropped LapTable can be FINALIZED (its Python attributes
        # cleared) while its C++ widget is still alive and still installed as this filter, and the
        # AttributeError that follows surfaces inside whatever override Qt was dispatching.
        table = getattr(self, "table", None)
        if table is not None and obj is table.viewport() and event.type() == QEvent.Resize:
            self._fit_columns()
            self._keep_selection_visible()
        return super().eventFilter(obj, event)

    def set_speed_unit(self, unit: str):
        """Switch the Entry-speed display unit live: re-header + re-fill (converts the Entry cells).
        No-op if unchanged."""
        unit = units.normalize_unit(unit)
        if unit == self._speed_unit:
            return
        self._speed_unit = unit
        self.refresh()

    def refresh(self):
        rows = self.session.lap_rows()

        # E1: flip to the centred empty-state placeholder when there are no laps to show (else the
        # populated table). Done first so the panel never flashes a blank grid; the footer below
        # the stack refreshes to em-dashes on its own (every accessor returns None with no laps).
        self._stack.setCurrentIndex(1 if not rows else 0)

        # N sector lines split each lap into N+1 sub-sectors; show one split column per
        # sub-sector (none by default = today's 4 columns). Column count depends on this,
        # so set the headers here — refresh() runs on selection and after sectors change.
        # The blank SPACER column is appended LAST (after any S-splits) — it absorbs the panel's
        # leftover width so the data columns stay adjacent and left-packed (P5).
        n_splits = self._n_split_cols()
        headers = [*_columns(self._speed_unit),
                   *(f"S{i + 1}" for i in range(n_splits)), SPACER_HEADER]

        # Per-lap splits + per-column session-best (same accessor the footer sums, so cells/footer agree).
        splits_by_lap = {row["idx"]: self.session.lap_sector_splits(row["idx"]) for row in rows}
        best_split = self.session.session_best_splits()

        # Sorting must be OFF while we populate (else rows reorder mid-fill and setItem(r,…)
        # lands on the wrong row); re-enabled after, preserving the user's chosen sort.
        self.table.setSortingEnabled(False)
        self.table.blockSignals(True)
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        # setHorizontalHeaderLabels REPLACES the header items, so the alignment (like the tooltips
        # below) has to be re-applied on every refresh, not only at construction.
        align_headers_over_their_columns(self.table)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            lap_id = row["idx"]
            splits = splits_by_lap[lap_id]
            # (text, numeric-sort-key) per column. The Lap cell DISPLAYS the 1-based lap number
            # (lap_label) but KEEPS the 0-based lap id as its numeric sort key, so the column
            # still sorts by true lap order and the ★/▶/⚠ markers (keyed on lap_id) are unchanged.
            cells: list[tuple[str, float]] = [
                (lap_label(lap_id), float(lap_id)),
                (fmt_time(row["time"]), float(row["time"])),
                (f"{row['dist']:.0f}", float(row["dist"])),
                # Entry speed: convert km/h → the display unit for BOTH the shown text and the
                # numeric sort key so ordering matches what's on screen (identity for km/h).
                (f"{units.convert_speed(row['entry'], self._speed_unit):.1f}",
                 units.convert_speed(float(row["entry"]), self._speed_unit)),
            ]
            for i in range(n_splits):
                if i < len(splits):
                    cells.append((f"{splits[i]:.2f}", float(splits[i])))
                else:  # a partial lap may have fewer splits than columns — blank (NaN key),
                    cells.append(("", float("nan")))  # sorts LAST in both directions (_NumItem)
            for c, (text, key) in enumerate(cells):
                item = _NumItem(text)
                item.setData(NUM_ROLE, key)
                if c >= NUMERIC_COL_START:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    item.setFont(self._num_font)
                self.table.setItem(r, c, item)
            # Stash the lap id on the Lap cell so row<->lap stays correct across any sort.
            self.table.item(r, 0).setData(LAP_ROLE, lap_id)
        # Losing sector lines SHRINKS the column count, and setColumnCount only drops the TRAILING
        # sections — so the old S-column items can survive in what is now the blank spacer column
        # (the fill above writes the data columns only). Empty it, or stale splits would show under
        # a blank header.
        spacer = self._n_real_cols()
        for r in range(self.table.rowCount()):
            if self.table.item(r, spacer) is not None:
                self.table.takeItem(r, spacer)
        self.table.blockSignals(False)
        # Full header text lives on the header itself (a narrow panel elides the label — L3-04).
        for c, tip in enumerate(_lap_col_tips(self._speed_unit)):
            self.table.horizontalHeaderItem(c).setToolTip(tip)
        for i in range(n_splits):
            self.table.horizontalHeaderItem(len(COLUMNS) + i).setToolTip(
                f"Sector {i + 1} split (s) — time between this lap's sector lines. "
                f"{BEST_SPLIT_TIP}")
        # Set the section RESIZE MODES now that the rows are in; the widths themselves are fitted
        # at the end of refresh(), once _apply_highlights has finished rewriting the S-split text.
        self._apply_column_sizing()
        # Re-apply the user's chosen sort (lap-ascending by default) on the freshly-filled rows.
        # A remembered S-column can VANISH when sector lines are removed (and must never be the
        # blank spacer) — fall back to lap order rather than sorting a gone/blank column.
        if self._sort_col >= self._n_real_cols():
            self._sort_col, self._sort_order = 0, Qt.AscendingOrder
        # Tell _NumItem the direction first so blanks land LAST after any descending reversal.
        _NumItem._descending = self._sort_order == Qt.DescendingOrder
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(self._sort_col, self._sort_order)
        self._best_split = best_split  # cached so re-highlight after a sort needn't recompute
        # dropout lap ids, keyed by lap id so the ⚠ flag follows the lap across sorts
        self._dropout_ids = self.session.dropout_lap_ids()
        self._apply_highlights()
        # Fit AFTER the highlights: they append the ★ best-sector mark to S-split cells, which is
        # part of the width those columns need.
        self._fit_columns()
        if n_splits > self._n_splits_shown:
            self._reveal_last_split()
        self._n_splits_shown = n_splits
        # The excluded-laps strip follows every refresh — i.e. also after a timing-line edit
        # re-segments the laps (which shifts both the valid and the excluded sets). The valid rows
        # are handed over rather than re-read: they are the excluded SHARE's denominator.
        self._refresh_excluded(rows)

    # ------------------------------------------------------------- highlights
    def _lap_id(self, r: int) -> int:
        return int(self.table.item(r, 0).data(LAP_ROLE))

    def _row_for_lap(self, lap_id) -> int:
        if lap_id is None:
            return -1
        for r in range(self.table.rowCount()):
            if self._lap_id(r) == lap_id:
                return r
        return -1

    # Two DIFFERENT column sets, for the two orthogonal trust axes (they used to be one, which is
    # how Dist and Entry came to render at full confidence on a session whose start line was a
    # guess — QA L3-02):
    #
    #  * _start_line_cols — every value the start/finish line places: the lap Time AND the S-splits
    #    (durations between crossings), Dist (the distance BETWEEN crossings) and Entry (the speed
    #    AT a crossing). Move the line and all four change. So PROVISIONAL timing demotes all four.
    #  * _clock_cols — the DURATIONS only. The degraded-clock axis is about how well time itself is
    #    measured; it has no bearing on an odometer distance or a GPS speed sample, so it must not
    #    demote Dist/Entry.
    def _start_line_cols(self) -> set[int]:
        return set(range(1, self._n_real_cols()))

    def _clock_cols(self) -> set[int]:
        return {1, *(len(COLUMNS) + i for i in range(self._n_split_cols()))}

    def _apply_highlights(self):
        """Re-apply ALL row/cell highlights keyed by lap id, so they survive any sort:
          * green foreground on every cell of the overall best lap,
          * purple foreground+bold on each per-column session-best split cell (F5),
          * the ▶ prefix + bold Lap cell for the current (playing) lap.
        The blue selection is Qt's own row background and is left to the selection model.

        TIMING TRUST: when the session's timing is PROVISIONAL (start line auto-fitted, not
        user-confirmed — see Session.timing_verified) EVERY start-line-derived cell — lap Time, the
        S-splits, Dist and Entry (see _start_line_cols) — is de-emphasized (muted + italic, with the
        'provisional' tooltip) and BOTH "best" authority cues are suppressed — no purple
        session-best splits and no green best-lap — since a 'best' measured against an arbitrary
        start line is meaningless. Only the Lap number, which the line cannot move, stays normal.
        Verified timing renders as before.

        DATA QUALITY (orthogonal — Session.timing_quality): a media-clock-fallback recording or one
        whose GPS quality gate rejected many fixes ALSO mutes the DURATION cells (_clock_cols: Time
        + the S-splits) with an 'estimated' tooltip, but leaves Dist/Entry alone (an odometer
        distance and a GPS speed sample don't get worse when the clock does) and does NOT suppress
        the bests — the start line is trusted, so the bests stay valid RELATIVE to each other; only
        the absolute timing accuracy is degraded. A normal GPS9, clean-fix recording (the common
        case) leaves both axes untouched."""
        rows = self.table.rowCount()
        if not rows:
            return
        verified = self.session.timing_verified
        degraded = self.session.timing_quality.degraded
        # The degraded-timing cell tooltip, from the shared clock-aware copy (matches the map banner
        # + header chip); computed once — it's the same for every estimated cell.
        estimated_note = estimated_timing_tooltip(self.session.timing_quality)
        # Overall best lap = the valid lap with the min time (foreground green on all cells) —
        # suppressed entirely while the timing is provisional (but NOT for a merely-degraded clock).
        best_lap = self.session.best_lap_id() if verified else None
        # Cache the best-lap id so the ★ mark on the Lap cell (applied via _lap_cell_text) tracks it;
        # None while provisional so no ★ paints a meaningless "best" against an arbitrary start line.
        self._best_lap_id = best_lap
        n_splits = self._n_split_cols()
        best_split = self._best_split
        start_line_cols = self._start_line_cols()
        clock_cols = self._clock_cols()
        # Palette-dependent "best" foregrounds, resolved per-refresh so a colour-blind-palette flip
        # recolours the cells (green→blue best lap, purple→teal best sector) on the next refresh().
        best_color = QColor(theme.best_lap_colour())
        best_sector_color = QColor(theme.best_sector_colour())

        dropout_ids = self._dropout_ids
        self.table.blockSignals(True)
        for r in range(rows):
            lap_id = self._lap_id(r)
            is_best = lap_id == best_lap
            is_dropout = lap_id in dropout_ids
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c)
                if item is None:
                    continue
                provisional_cell = not verified and c in start_line_cols
                # A degraded-clock DURATION cell mutes too, but only when NOT already provisional
                # (provisional is the stronger demotion + suppresses the bests; degraded keeps them).
                estimated_cell = verified and degraded and c in clock_cols
                muted_cell = provisional_cell or estimated_cell
                # base off-white; green (best lap) / muted (provisional or estimated timing) / purple.
                # BEST-LAP wins the COLOUR over degraded muting: on a degraded (but verified)
                # recording the estimated grey would otherwise repaint the best lap's Time cell —
                # the one cell answering "which lap was fastest" — so it reads like every other row.
                # Keep the best-lap green there (the italic + estimated tooltip below still carry the
                # accuracy cue). Provisional muting still wins: it suppresses the bests entirely, so
                # `is_best` is already False under provisional (best_lap is None while unverified).
                if muted_cell and not (estimated_cell and is_best):
                    item.setForeground(PROVISIONAL_COLOR)
                else:
                    item.setForeground(best_color if is_best else BASE_COLOR)
                # Muted+italic on any demoted timing cell (the best-lap cell stays green but still
                # italic, keeping the estimated cue); the dropout tooltip wins (it flags a per-lap
                # issue), else the provisional note, else the estimated-timing note, else clear.
                theme.apply_provisional_style(item, muted_cell)
                tip = (DROPOUT_TOOLTIP if is_dropout
                       else PROVISIONAL_TOOLTIP if provisional_cell
                       else estimated_note if estimated_cell else "")
                # IA-07: the ★ this row's Lap cell wears is a glyph with no legend anywhere unless
                # the cell carrying it says what it means. Set every pass (the mark moves with the
                # best lap), and above — never instead of — the trust note.
                item.setToolTip(_with_star(BEST_LAP_TIP, tip) if is_best and c == 0 else tip)
            # per-sector best → purple+bold + a ★ mark (outranks green for this cell) — but ONLY on
            # verified timing; a "validated best" on an arbitrary start line would mislead. The ★ is
            # the NON-COLOUR redundancy (bold alone is weak); the split text is rebuilt from the
            # stored numeric key each pass so the mark toggles cleanly across sorts (no double-★).
            for i in range(n_splits):
                c = len(COLUMNS) + i
                item = self.table.item(r, c)
                if item is None:
                    continue
                key = item.data(NUM_ROLE)
                target = best_split[i] if i < len(best_split) else None
                font = item.font()
                is_best_split = (verified and target is not None and key is not None
                                 and math.isfinite(float(key))
                                 and abs(float(key) - target) < 1e-9)
                if key is not None and math.isfinite(float(key)):
                    base = f"{float(key):.2f}"
                    item.setText(base + BEST_SECTOR_MARK if is_best_split else base)
                if is_best_split:
                    item.setForeground(best_sector_color)
                    font.setBold(True)
                    # ...and the same legend on the same glyph (IA-07). The loop above has just
                    # rewritten this cell's tooltip, so there is nothing stale to append to.
                    item.setToolTip(_with_star(BEST_SPLIT_TIP, item.toolTip()))
                else:
                    font.setBold(False)
                item.setFont(font)
        self.table.blockSignals(False)
        self._apply_current_lap()

    def refresh_timing_trust(self):
        """Re-render everything this table keys on the session's TRUST/QUALITY flags — the muted
        italic start-line cells, the ★ best-lap mark, the green best lap and the purple session-best
        splits — without re-reading the laps.

        The rows themselves cannot have moved: the trust flags say how authoritative the existing
        times are, not what they are. So this is _apply_highlights (which rewrites every cell's
        foreground/font/tooltip AND, via _apply_current_lap, every Lap cell's text), not refresh().

        Exists because the flags CAN flip with no re-segmentation behind it: File ▸ Save as track…
        promotes the current lines into a named track, which makes the session Verified while the
        table's rows stay identical. That path left the lap in provisional italics with the ★
        withheld, contradicting the map's already-cleared trust strip (QA W7-03). Driven by
        CentralView.refresh_timing_trust."""
        self._apply_highlights()

    def _lap_cell_text(self, lap_id, on: bool) -> str:
        """The Lap-cell text for `lap_id`: a '▶ ' prefix when it's the current (playing) lap, a '★ '
        mark when it's the overall best lap (the NON-COLOUR redundancy for the green best-lap row —
        reads without hue), and a trailing ' ⚠' low-confidence marker on a GPS-dropout lap. The ▶
        current marker leads the ★ so the playing lap is always identifiable first."""
        prefix = CURRENT_PREFIX if on else ""
        best = BEST_LAP_MARK if lap_id == self._best_lap_id else ""
        suffix = DROPOUT_SUFFIX if lap_id in self._dropout_ids else ""
        return f"{prefix}{best}{lap_label(lap_id)}{suffix}"  # 1-based display number

    def _set_row_current(self, r: int, on: bool):
        """Apply/clear the ▶ prefix + bold on ONE row's Lap cell (the only per-lap-change cue)."""
        if r < 0:
            return
        item = self.table.item(r, 0)
        if item is None:
            return
        item.setText(self._lap_cell_text(self._lap_id(r), on))
        font = item.font()
        font.setBold(on)
        item.setFont(font)

    def _apply_current_lap(self):
        """Full-rebuild path: rewrite every Lap cell's ▶ prefix/bold for the current lap (after
        refresh/sort, where row identities may have changed). set_current_lap has the per-tick
        two-row fast path."""
        target = self._row_for_lap(self._current_lap)
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            self._set_row_current(r, r == target)
        self.table.blockSignals(False)

    def _on_sorted(self, col, order):
        # The blank trailing SPACER column holds no cells, so a click on it can't order anything —
        # it would just park the sort indicator on an empty header and forget the user's sort.
        # Bounce the indicator back to the live sort column (which re-applies that sort).
        if col >= self._n_real_cols():
            self.table.horizontalHeader().setSortIndicator(self._sort_col, self._sort_order)
            return
        # A header click re-ordered the rows; remember the chosen column/direction so a later
        # refresh() (e.g. a sector edit) keeps the user's sort, and re-apply the highlights
        # keyed by lap id so they follow the laps to their new rows.
        self._sort_col = col
        self._sort_order = order
        # Qt's header-click sort ran with the PREVIOUS direction flag, which can mis-place blank
        # cells (they must stay LAST in both directions). Set the flag to the new direction and
        # re-sort so blanks land at the bottom whichever way the column is now ordered. Guarded so
        # the re-sort's own sortIndicatorChanged (same col/order) doesn't recurse.
        descending = order == Qt.DescendingOrder
        if _NumItem._descending != descending:
            _NumItem._descending = descending
            self.table.sortByColumn(col, order)
        self._apply_highlights()

    def set_current_lap(self, lap_id):
        """Mark the lap playing on the video (no effect on selection). Fast path: only the old and
        new current-lap rows are touched."""
        if lap_id == self._current_lap:
            return
        old_row = self._row_for_lap(self._current_lap)
        self._current_lap = lap_id
        new_row = self._row_for_lap(lap_id)
        self.table.blockSignals(True)
        if old_row != new_row:
            self._set_row_current(old_row, False)  # clear the prefix/bold off the previous lap row
        self._set_row_current(new_row, True)       # mark the new current lap row
        self.table.blockSignals(False)

    def select(self, idxs: list[int]):
        # Build ONE QItemSelection over every matching row and apply it in a single model call.
        # A per-row selectRow() loop would REPLACE the selection each call under ExtendedSelection
        # (each acts like a plain click), leaving only the last row selected — so a multi-lap
        # select() must go through the selection model at once (L3: the cap re-applies via this).
        want = set(idxs)
        model = self.table.model()
        sel = QItemSelection()
        first = None
        for r in range(self.table.rowCount()):
            if self._lap_id(r) in want:
                idx = model.index(r, 0)
                sel.select(idx, idx)
                if first is None:
                    first = idx
        self.table.blockSignals(True)
        sm = self.table.selectionModel()
        sm.clearSelection()
        if not sel.isEmpty():
            sm.select(sel, QItemSelectionModel.Select | QItemSelectionModel.Rows)
        self.table.blockSignals(False)
        self._keep_selection_visible()

    def _reveal_last_split(self):
        """L3-04: the map's "Add sector" button silently created columns that landed ENTIRELY off
        the default quadrant (S2 and S3 at zero visible pixels), with nothing but a 1.55:1
        scrollbar to say so. The fit above claws most of that back; scroll only when the newest S
        column is STILL completely off screen, because every pixel of that scroll comes off the Lap
        column — trading the row's identity for a split that is already partly readable is a bad
        deal. Fires once per column-count increase: a refresh from a sort or a selection must never
        yank the horizontal scroll."""
        last = self._n_real_cols() - 1
        if not self.table.rowCount() or last < 0:
            return
        x = self.table.columnViewportPosition(last)
        if x < self.table.viewport().width():
            return                                          # already at least partly on screen
        v = self.table.verticalScrollBar().value()          # a purely HORIZONTAL scroll
        self.table.scrollTo(self.table.model().index(0, last), QAbstractItemView.EnsureVisible)
        self.table.verticalScrollBar().setValue(v)

    def _keep_selection_visible(self):
        """IA-02: a PROGRAMMATIC selection has to be scrolled to, or it isn't a selection the user
        can see. The app pre-selects the best lap at launch and draws four panels from it, but on a
        21-lap session that row sat 150px BELOW the viewport at every window size, with the vertical
        scrollbar still at 0 — the panel that OWNS the selection painted no highlighted row at all.

        Centre it: the laps either side are the context that makes the selected one mean something.
        Only when it is not already fully visible, so re-fitting or a resize never yanks a table the
        user has scrolled deliberately — and the horizontal offset is restored afterwards, because
        scrolling to a column-0 cell would otherwise undo the S-column scroll refresh() just made."""
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if not rows:
            return
        idx = self.table.model().index(rows[0], 0)
        rect = self.table.visualRect(idx)
        if rect.isValid() and self.table.viewport().rect().contains(rect):
            return
        h = self.table.horizontalScrollBar().value()
        self.table.scrollTo(idx, QAbstractItemView.PositionAtCenter)
        self.table.horizontalScrollBar().setValue(h)

    def selected_lap_ids(self) -> list[int]:
        """The lap ids of the currently-selected rows (sorted). Read-only — used to restore the
        chart overlay to the table's selection when compare mode is turned off."""
        return sorted({self._lap_id(idx.row())
                       for idx in self.table.selectionModel().selectedRows()})

    def _capped_selection(self, ids: list[int]) -> list[int]:
        """L3: trim an over-large selection to the fastest MAX_COMPARE_LAPS laps so the charts never
        overlay more than can legibly draw (the legend truncates past ~13, and the curves blanket
        each other). Fewer than the cap passes through unchanged. Falls back to a plain head-slice if
        a (test-double) session exposes no lap_time."""
        if len(ids) <= MAX_COMPARE_LAPS:
            return ids
        lap_time = getattr(self.session, "lap_time", None)
        if callable(lap_time):
            # Keep the fastest cap laps (ties broken by lap id for a stable, deterministic pick).
            ranked = sorted(ids, key=lambda lid: (lap_time(lid), lid))
            return sorted(ranked[:MAX_COMPARE_LAPS])
        return ids[:MAX_COMPARE_LAPS]

    def _on_selection(self):
        ids = self.selected_lap_ids()
        capped = self._capped_selection(ids)
        if capped != ids:
            # Re-apply the trimmed selection so the deselected rows visibly clear (no silent
            # chart-side drop). select() blocks signals, so re-emit the capped set ourselves.
            self.select(capped)
        self.laps_selected.emit(capped)


# ===================================================================== Corners mode
# Rows = detected corners (track order), cols = the selected lap's per-corner metrics vs the best
# lap (session.lap_corner_stats). A separate widget stacked with LapTable; shares only the module
# display constants. Headers are abbreviated so all 8 columns fit the narrow panel — dropped units
# move to per-column header tooltips (_corner_col_tips).
# The two Δ headers are written CLOSED ("Δbest", not "Δ best"), matching the app's own "Δideal".
# Not cosmetic: this budget lets a squeezed header elide into its tooltip, which is sound for a
# label nothing can be confused with — but these two shared a "Δ " prefix, and Qt elides the TAIL,
# so the words that told them apart were the first thing destroyed. Measured at 1280×800 (below
# the 1440×900 the budget was tuned for) both painted "Δ …": a column of seconds and a column of
# km/h rendered as the same string, separable only by a hover tooltip a keyboard user cannot
# reach. Closed, they elide to "Δb…" and "Δa…" — still abbreviated at 1280, but no longer the SAME
# abbreviation, which is the whole defect. (Whole is not on offer there: at 1280 the eight columns
# sum to the viewport exactly, and rendering both labels in full would cost 26 px this table does
# not have. At 1440 they render in full, as they always did.)
#
# Flooring the two columns at their header width was the obvious alternative and does not work:
# all eight are already at their floors at 1280 (they sum to the viewport exactly), so the width
# has to come from a floor, and every remaining floor is a CELL width. This table's contract is
# that headers elide and values never do.
CORNER_COLUMNS = ["Corner", "Time", "Δbest", "Apex", "Δapex", "Entry", "Exit",
                  theme.estimated_label("Grip")]
# Column 2's header when a cross-recording REFERENCE is the Δ baseline (QA-W2R-03). The whole
# column is then measured against another recording's lap and "Δbest" is simply false — the legend
# on the chart beside it already spells the recording out, and this table's own header tooltip
# names it (see _corner_col_tips). Written CLOSED for the reason above, and it elides to "Δr…"
# against "Δapex"'s "Δa…", so it keeps the property the elide guard exists to protect; it is also
# 8 px NARROWER than "Δbest", so it cannot squeeze the seven columns beside it.
CORNER_DELTA_COL = 2
CORNER_DELTA_REF_HEADER = "Δref"


def _corner_col_tips(unit: str | None, ref_label: str | None = None) -> list[str]:
    """Full meaning + units per header, shown on hover (1:1 with CORNER_COLUMNS). The four speed
    tips name the current display unit ("km/h" / "mph"); the rest are unit-independent.

    `ref_label` is the cross-recording reference's source label when one is the Δ baseline: the two
    Δ tips then name THAT recording, which is the only place in this table with room to spell it
    out (the headers abbreviate to "Δref"/"Δapex")."""
    u = units.speed_label(unit)
    # The two Δ tips, per baseline. Written out rather than interpolated so the no-reference
    # wording stays exactly what it has always been, and the reference wording reads as English.
    if ref_label:
        delta_tip = (f"Δ vs the same corner on the reference recording's lap — {ref_label} "
                     f"(seconds; − is faster)")
        apex_tip = (f"Δ apex speed vs the reference recording's lap — {ref_label} "
                    f"({u}; + is faster)")
    else:
        delta_tip = "Δ vs the best lap's same corner (seconds; − is faster)"
        apex_tip = f"Δ apex speed vs the best lap ({u}; + is faster)"
    return [
        "Detected corner in track order (⟲ left / ⟳ right). Click a row to ring that corner "
        "on the map.",
        f"Time spent in the corner (seconds). {BEST_CORNER_TIP}",
        delta_tip,
        f"Apex (minimum) speed through the corner ({u})",
        apex_tip,
        f"Corner entry speed ({u})",
        f"Corner exit speed ({u})",
        # ESTIMATED, not measured: the friction circle mixes the noisier longitudinal axis, so this is
        # lateral-dominant. Numerator and divisor share the SAME validated axes (clean GPS-derived
        # longitudinal + IMU lateral). Normalised to the SESSION envelope (not each lap's own peak) so a
        # slow lap reads genuinely lower; ~100% means at this session's grip limit (it can read a little
        # over when a corner sits just past the robust p98 envelope).
        "Grip utilisation (ESTIMATED): median combined |g| in the corner vs the session friction-circle "
        "envelope (%). Estimated from the clean GPS-derived longitudinal + IMU lateral g; ~100% = at the "
        "session's grip limit. Normalised session-wide so a slower lap reads lower.",
    ]


def _corner_unit_caption(unit: str | None) -> str:
    """The Corners table's UNIT LINE (QA L3-10). Seven of its eight columns are unit-bearing
    numbers and the abbreviated headers name not one of them — the units live in the header
    tooltips above, which is hover-only, while the Stats page captions the very same data on
    screen ("CORNERS · speeds in mph"). This borrows that solution, for the reason the Stats page
    has it: a caption follows the display unit for free and never elides, where naming the unit in
    the headers ("Apex (mph)") would widen four columns that are already squeezed at the default
    quadrant width and then elide the unit away first (see MAX_DATA_COL_PX / _fit_columns).

    Grip's % is here rather than in its cells for the same measured reason: "77 %" instead of "77"
    raises that column's FLOOR by 16 px (39 -> 55 px), and L3-03 is the finding that these eight
    columns already wanted 501 px in a 447 px quadrant with 0 of 12 grip cells readable. One
    caption line states all three unit families and costs the columns nothing."""
    return f"Times in seconds · speeds in {units.speed_label(unit)} · grip %"
CORNER_DIR_GLYPH = {1: "⟲", -1: "⟳"}  # left / right (turn sense), shown after the C-label
# The Δ columns when the shown lap IS the Δ baseline (QA L3-05). `corner_model.lap_corner_stats`
# passes ref=None for the baseline and `corners.lap_corner_stats`'s docstring documents the result
# — "None for the reference lap itself -> deltas 0" — so on the app's own post-load selection (the
# best lap) 24 of 24 Δ cells read "+0.00"/"+0.0": 2 of 8 columns, a quarter of the table, carrying
# no measurement and nothing on the page saying why. A dash reads as "no value here" the way it does
# on the Stats page; the caption below names the lap.
SELF_DELTA = "—"
# The caption that names the baseline. Shown ONLY in that state, so it costs a normal lap nothing.
SELF_DELTA_TOOLTIP = ("The Δ columns compare each corner against the session-best lap. This IS "
                      "that lap, so select another one to see a Δ.")
# ...and the same state reached through the REFERENCE path (QA-W2R-04): a reference loaded from the
# recording already open makes the baseline THIS lap, so every Δ is again a self-zero rather than a
# measurement. set_reference_session refuses that today; this wording exists because the dashes must
# not depend on the refusal being there.
SELF_REFERENCE_TOOLTIP = ("The Δ columns compare each corner against the reference recording's lap "
                          "— but that reference is this same recording, so this lap is being "
                          "compared with itself. Load a different recording as the reference.")
# Corner identity column start width: "C12 ⟳" + the "Corner" header, fully readable (C3 —
# the old Stretch mode crushed this row-identity column to a 42px sliver at default width).
CORNER_NAME_COL_PX = 88


class CornerTable(QWidget):
    """Corners-mode table: one row per detected corner for the selected lap.

    Session-best corner time is purple+bold; Δ columns use the shared delta colour.
    Read-only/unsorted — track order is the meaning. A row click emits ``corner_clicked``
    so the app can ring that corner's apex on the map (B4: the Stats CORNERS and Coaching
    rows already did this — these rows were the odd surface out)."""

    # Clicked corner cid -> the map apex-ring highlight (wired in central_view).
    corner_clicked = Signal(object)

    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self._lap_id: int | None = None
        self._cids: list[int] = []   # row -> corner cid, set in refresh() (B4 map-ring click)
        # Speed display unit (km/h default); app pushes the persisted choice via set_speed_unit.
        # Drives the Apex/Δ apex/Entry/Exit value conversion + the per-column tooltips' unit name.
        self._speed_unit = units.DEFAULT_UNIT
        self._hover_row = -1         # the row under the pointer (L3-07), -1 for none
        self.table = QTableWidget(0, len(CORNER_COLUMNS))
        self.table.setHorizontalHeaderLabels(CORNER_COLUMNS)
        # Once: this table never re-labels its headers (see _apply_corner_tips, which rewrites the
        # Δ column's TEXT on the existing item precisely to keep the tooltips), so the alignment
        # set here survives every refresh.
        align_headers_over_their_columns(self.table)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # Column sizing (UI-scrutiny C3+B5): the old col-0 Stretch let Qt crush the row's
        # IDENTITY column to a 42px "orne" sliver at the default panel width (the "all 8 fit"
        # assumption rotted as columns grew) and balloon it to a 959px void when maximized.
        # Now: col 0 starts at a readable CORNER_NAME_COL_PX (fits "C12 ⟳" + the header) and every
        # column is Interactive so _fit_columns can size the set to the panel.
        # QA L3-03: leaving the overflow to the h-scrollbar was not enough — at the DEFAULT 447px
        # quadrant these 8 columns wanted 501px, so "Grip (est)" started at x=422 and 0 of 12 grip
        # cells rendered a readable value, behind a scrollbar handle at 1.55:1 contrast. The fit
        # gives the slack back (headers elide to their tooltips; values never do).
        hdr = self.table.horizontalHeader()
        for c in range(len(CORNER_COLUMNS)):
            hdr.setSectionResizeMode(c, QHeaderView.Interactive)
        hdr.resizeSection(0, CORNER_NAME_COL_PX)
        hdr.setTextElideMode(Qt.ElideRight)   # never centre-clip a squeezed header — see LapTable
        self.table.cellClicked.connect(self._on_cell_clicked)
        # L3-07: every one of these rows is a click target — it rings the corner on the map and,
        # from a maximized lap panel, restores the 2x2 grid on the way (deliberate: the map the
        # ring paints on needs pixels, see central_view._on_stats_corner_clicked). That was
        # invisible: no cursor change, no hover, nothing in the header, and the panel collapsing
        # 5.2x smaller was the first feedback a click produced. The strip below the lap grid
        # already declares itself with a pointing hand; the same declaration here, plus a row-wide
        # hover fill so the ROW (not the cell) reads as the target it is.
        self.table.viewport().setCursor(Qt.PointingHandCursor)
        self.table.viewport().setAttribute(Qt.WA_Hover, True)   # else Qt sends no hover events
        # ...and one filter for both: the real width changes AND the hovered row.
        self.table.viewport().installEventFilter(self)
        # B6: the table is deliberately unsortable (track order IS the meaning) — no pressed
        # feedback on headers that do nothing.
        hdr.setSectionsClickable(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self._num_font = theme.mono_font(theme.TABLE)
        # Empty state (was a bare header grid — the one surface without one): says WHY there
        # are no rows (no lap selected vs no corners detected) instead of a silent void.
        self.empty = QLabel("")
        self.empty.setProperty("role", "EmptyState")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setVisible(False)
        # The Δ-baseline caption (L3-05) — visible only on the lap the Δ columns measure against,
        # where every Δ is a dash. Muted, one line, above the grid it explains.
        self.baseline_note = QLabel("")
        self.baseline_note.setProperty("role", "BarLabel")
        self.baseline_note.setWordWrap(True)
        self.baseline_note.setContentsMargins(GRID_TEXT_INSET, theme.SPACE_XS,
                                              GRID_TEXT_INSET, theme.SPACE_XXS)
        self.baseline_note.setToolTip(SELF_DELTA_TOOLTIP)
        self.baseline_note.setVisible(False)
        # The UNIT caption (L3-10) — see _corner_unit_caption. Directly above the grid it names,
        # muted, one line, and only while there is a grid to name; text comes from
        # _apply_corner_tips so the CONSTRUCTOR seam (central_view writes _speed_unit and re-tips
        # without a unit-changed signal) sets it as surely as the View ▸ Units action does.
        self.unit_note = QLabel("")
        self.unit_note.setProperty("role", "BarLabel")
        self.unit_note.setContentsMargins(GRID_TEXT_INSET, theme.SPACE_XXS,
                                          GRID_TEXT_INSET, theme.SPACE_XXS)
        self.unit_note.setVisible(False)
        self._apply_corner_tips()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.baseline_note)
        lay.addWidget(self.unit_note)
        lay.addWidget(self.table)
        lay.addWidget(self.empty, 1)

    def _on_cell_clicked(self, row: int, _col: int):
        """B4: ring the clicked row's corner on the map — the same corner_clicked pathway the
        Stats CORNERS and Coaching rows use, so all three surfaces behave identically."""
        if 0 <= row < len(self._cids):
            self.corner_clicked.emit(self._cids[row])

    def _column_budget(self) -> tuple[list[int], list[int], list[int]]:
        """(natural, floors, caps) for the corner columns — see LapTable._column_budget. Column 0
        is the row identity, so it opens at CORNER_NAME_COL_PX; unlike the lap number it carries no
        moving marker, so it may be squeezed back to its own cell width ("C12 ⟳") when the panel
        is short — 24 of the 54px the default quadrant is missing come from there, the rest from
        the "Grip (est)" header, whose full wording is already in its tooltip. That trade needs
        every header to stay unambiguous once elided, which is why the two Δ columns are named
        without a space — see CORNER_COLUMNS."""
        hdr = self.table.horizontalHeader()
        n = self.table.columnCount()
        cells = [self.table.sizeHintForColumn(c) if self.table.rowCount() else 0 for c in range(n)]
        natural = [max(hdr.sectionSizeHint(c), cells[c]) for c in range(n)]
        floors = [max(MIN_SECTION_PX, cells[c]) for c in range(n)]
        if natural:
            natural[0] = max(natural[0], CORNER_NAME_COL_PX)
        return natural, floors, [max(x, MAX_DATA_COL_PX) for x in natural]

    def _fit_columns(self):
        """Size the 8 corner columns to the panel — every column on screen at the default quadrant,
        every column sharing the width when the panel is maximized."""
        hdr = self.table.horizontalHeader()
        avail = self.table.viewport().width()
        if avail <= 0 or not self.table.columnCount():
            return
        natural, floors, caps = self._column_budget()
        # ...with every header's stem asked for rather than left to the rounding of the squeeze —
        # this is the table the two Δ columns live in. See header_floors.
        floors = header_floors(self.table, floors, natural, avail)
        for c, w in enumerate(fit_columns(natural, floors, caps, avail)):
            hdr.resizeSection(c, w)

    def eventFilter(self, obj, event):
        # See LapTable.eventFilter: this page lives in a tab stack, so the container's own
        # resizeEvent is not a reliable signal that the table finally has its real width — and the
        # same finalized-instance guard applies.
        table = getattr(self, "table", None)
        if table is not None and obj is table.viewport():
            if event.type() == QEvent.Resize:
                self._fit_columns()
            elif event.type() in (QEvent.HoverMove, QEvent.HoverEnter):
                self._set_hover_row(table.indexAt(event.position().toPoint()).row())
            elif event.type() == QEvent.HoverLeave:
                self._set_hover_row(-1)
        return super().eventFilter(obj, event)

    def _set_hover_row(self, row: int):
        """Fill the hovered row (L3-07). Qt's own :hover state on an item view is per-CELL, and a
        cell-wide fill would advertise the wrong target: any of the eight cells does the same one
        thing. Painted through the items' BackgroundRole, which clears back to the view's
        alternating colours — no per-row widgets, nothing for refresh() to unwind beyond the reset
        below (it rebuilds every item, so the old row's fill goes with it)."""
        if row == self._hover_row:
            return
        fill = QColor(theme.C.surface_hover)
        for r in (self._hover_row, row):
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c) if r >= 0 else None
                if item is not None:
                    item.setData(Qt.BackgroundRole, fill if r == row else None)
        self._hover_row = row

    def _reference_label(self) -> str | None:
        """The active cross-recording reference's source label, or None when the Δ baseline is this
        session's own best lap. getattr-guarded for the lighter test doubles."""
        has_reference = getattr(self.session, "has_reference", None)
        if not (callable(has_reference) and has_reference()):
            return None
        label = getattr(self.session, "reference_label", None)
        return (label() if callable(label) else None) or "reference recording"

    def _apply_corner_tips(self):
        """(Re)apply the baseline- and unit-dependent chrome: the Δ column's HEADER, the per-column
        header tooltips AND the unit caption above the grid. Called from set_speed_unit, from
        refresh() (the reference can change under a live table), and, for the constructor seam that
        writes `_speed_unit` directly, from central_view. setText on the existing header item, never
        setHorizontalHeaderLabels — the latter replaces the items and drops the tooltips below."""
        ref_label = self._reference_label()
        self.table.horizontalHeaderItem(CORNER_DELTA_COL).setText(
            CORNER_DELTA_REF_HEADER if ref_label else CORNER_COLUMNS[CORNER_DELTA_COL])
        for c, tip in enumerate(_corner_col_tips(self._speed_unit, ref_label)):
            if tip:
                self.table.horizontalHeaderItem(c).setToolTip(tip)
        self.unit_note.setText(_corner_unit_caption(self._speed_unit))

    def set_speed_unit(self, unit: str):
        """Switch the corner speed display unit live: re-tooltip + re-fill the speed cells. No-op
        if unchanged."""
        unit = units.normalize_unit(unit)
        if unit == self._speed_unit:
            return
        self._speed_unit = unit
        self._apply_corner_tips()
        self.refresh()

    def set_lap(self, lap_id: int | None):
        """Show the corners of `lap_id` (None clears). No-op when unchanged — called per
        selection change AND from the auto-follow edge, so it must be cheap when idle."""
        if lap_id == self._lap_id:
            return
        self._lap_id = lap_id
        self.refresh()

    def _has_selectable_laps(self) -> bool:
        """Whether this recording has ANY lap a user could select. getattr-guarded so the lighter
        test doubles (which expose no valid_lap_ids) keep their "nothing selected yet" wording."""
        valid = getattr(self.session, "valid_lap_ids", None)
        return bool(valid()) if callable(valid) else True

    def _shows_the_baseline(self, stats: list) -> bool:
        """True when the shown lap IS the Δ baseline, so every delta in `stats` is a self-zero
        rather than a measurement.

        Both halves of that sentence are checked. The lap rule mirrors corner_model's baseline
        choice and nothing else: the CROSS-RECORDING reference lap when one is loaded, else the
        session best. The exact-zero test is `ref=None`'s own signature
        (`corners.lap_corner_stats`: "None for the reference lap itself -> deltas 0"), so a caller
        that hands us a real measurement always keeps it. Both are getattr-guarded for the lighter
        test doubles.

        QA-W2R-04: this used to return False the moment `has_reference()` was true, on the stated
        assumption that a reference is always a DIFFERENT recording — "then no local lap is ever
        compared with itself". That is an assumption, not a guard, and a reference loaded from this
        session's own footage falsified it: the dashes and the caption switched off and twelve rows
        of "+0.00" rendered as real numbers. So ask the session whether its reference is its own
        recording (Session.reference_is_own_recording) instead of assuming it is not."""
        if self._lap_id is None or not stats:
            return False
        if not all(st.delta == 0.0 and st.apex_speed_delta == 0.0 for st in stats):
            return False
        has_reference = getattr(self.session, "has_reference", None)
        if callable(has_reference) and has_reference():
            return self._reference_lap_is_this_lap()
        best_lap_id = getattr(self.session, "best_lap_id", None)
        return callable(best_lap_id) and self._lap_id == best_lap_id()

    def _reference_lap_is_this_lap(self) -> bool:
        """True when the active cross-recording reference is a lap of THIS recording and it is the
        lap on screen — i.e. the reference baseline is this very lap. False (the shipped state) for
        a reference from another recording, so nothing here is dashed."""
        own = getattr(self.session, "reference_is_own_recording", None)
        if not (callable(own) and own()):
            return False
        ref_lap_id = getattr(self.session, "reference_lap_id", None)
        return callable(ref_lap_id) and ref_lap_id() == self._lap_id

    def refresh(self):
        """Rebuild the rows from the session's corner model (e.g. after a timing-line edit
        re-segmented the laps and the corner set/stats were recomputed)."""
        # Range-guard the lap id: a re-segmentation can shrink the lap count while this view
        # still holds the previous selection (app re-selects right after; until then, empty).
        ok = self._lap_id is not None and 0 <= self._lap_id < self.session.lap_count()
        stats = self.session.corners.lap_corner_stats(self._lap_id) if ok else []
        # Name the Δ baseline on the column header itself: "Δbest" is false while a cross-recording
        # reference is the baseline (QA-W2R-03). Re-applied every refresh because a reference can be
        # loaded or cleared under a live table, and it re-tooltips with it so the header's
        # abbreviation always has the recording behind it.
        self._apply_corner_tips()
        # Every Δ on the baseline lap is a self-zero (see SELF_DELTA) — dash the two Δ columns and
        # caption which lap they would have compared against.
        baseline = self._shows_the_baseline(stats)
        self.baseline_note.setVisible(baseline)
        if baseline:
            self_ref = self._reference_lap_is_this_lap()
            self.baseline_note.setText(
                f"Lap {lap_label(self._lap_id)} is the reference lap — Δ is against itself."
                if self_ref else
                f"Lap {lap_label(self._lap_id)} is the session best — Δ is against itself.")
            self.baseline_note.setToolTip(
                SELF_REFERENCE_TOOLTIP if self_ref else SELF_DELTA_TOOLTIP)
        # Empty state: name the reason — a bare grid reads as broken. THREE cases, because "nothing
        # is selected" and "there is nothing selectable" are different recordings: on a session with
        # no valid lap at all, "Select a lap" is an instruction that cannot be followed (L3-08), so
        # that case borrows the Laps grid's own wording + reason. The table hides so the message
        # owns the pane.
        self.table.setVisible(bool(stats))
        # The unit caption belongs to the GRID: no grid, nothing to caption — the placeholder owns
        # the pane. (The rows are about to be rebuilt, so any hovered row's fill goes with them.)
        self.unit_note.setVisible(bool(stats))
        self._hover_row = -1
        self.empty.setVisible(not stats)
        if not stats:
            self.empty.setText(
                NO_LAPS_PLACEHOLDER if not self._has_selectable_laps() else
                "Select a lap to see its corners." if not ok else
                "No corners detected for this session yet — corner analysis needs a few "
                "clean laps of track shape.")
        corner_list = self.session.corners.corner_list() if stats else []
        bests = self.session.corners.corner_session_bests() if stats else []
        # Per-corner grip utilisation (%); [] when there's no g signal → the column shows a dash.
        grip = self.session.driving.lap_corner_grip(self._lap_id) if stats else []
        self.table.setRowCount(len(stats))
        self._cids = [c.cid for c in corner_list]  # row -> cid for the map-ring click (B4)
        for r, st in enumerate(stats):
            c = corner_list[r]
            grip_pct = f"{grip[r] * 100:.0f}" if r < len(grip) else "–"
            # Speeds convert km/h → the display unit at the cell boundary (identity for km/h);
            # apex Δ is a speed difference so it scales by the same factor. Δ COLOURS keep the raw
            # km/h delta (sign/magnitude threshold is unit-agnostic — a factor never flips it).
            u = self._speed_unit
            conv = units.convert_speed
            cells: list[tuple[str, str | None]] = [
                (f"{c.label} {CORNER_DIR_GLYPH.get(c.direction, '')}", None),
                (f"{st.time:.2f}", None),
                ((SELF_DELTA, theme.PROVISIONAL_COLOR) if baseline else
                 (f"{st.delta:+.2f}", theme.delta_colour(st.delta))),
                (f"{conv(st.apex_speed, u):.1f}", None),
                # Apex-speed Δ: FASTER through the corner is better, so the shared Δ colour
                # rule (negative = green) is applied to the NEGATED speed delta.
                ((SELF_DELTA, theme.PROVISIONAL_COLOR) if baseline else
                 (f"{conv(st.apex_speed_delta, u):+.1f}",
                  theme.delta_colour(-st.apex_speed_delta))),
                (f"{conv(st.entry_speed, u):.1f}", None),
                (f"{conv(st.exit_speed, u):.1f}", None),
                (grip_pct, None),
            ]
            is_best = bool(bests) and r < len(bests) and abs(st.time - bests[r]) < 1e-9
            for col, (text, colour) in enumerate(cells):
                # session-best corner time also carries the ★ non-colour mark (matches the lap
                # table's session-best split cells) so "this is the best" reads without the hue.
                if col == 1 and is_best:
                    text = text + BEST_SECTOR_MARK
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if col >= NUMERIC_COL_START:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    item.setFont(self._num_font)
                # session-best corner time: palette best-sector colour + bold, outranks the Δ colour
                if col == 1 and is_best:
                    item.setForeground(QColor(theme.best_sector_colour()))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    item.setToolTip(BEST_CORNER_TIP)   # the ★'s legend, on the ★ (IA-07)
                elif colour:
                    item.setForeground(QColor(colour))
                else:
                    item.setForeground(BASE_COLOR)
                self.table.setItem(r, col, item)
        # Fit once the rows are in — the column widths depend on the values just written.
        self._fit_columns()
