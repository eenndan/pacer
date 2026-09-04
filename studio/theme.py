"""Pacer Studio design system — single source of truth for the dark theme.

Pacer-free (no telemetry imports); font handling degrades gracefully to the system
font stack when the bundled Inter TTFs are unavailable. Public surface: C (colour/scale
tokens), register_fonts, apply_theme, ui_font, mono_font, delta_colour, LAP_SEEK_NUDGE_S.
"""

from __future__ import annotations

import os

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPalette

from . import units


# ====================================================================== tokens
class C:
    """Locked design tokens. Hex strings only — do not add ad-hoc colours elsewhere."""

    # --- neutrals ---
    canvas = "#15181E"          # window background (behind everything)
    bg = "#1A1D23"              # behind panels / table viewport
    surface = "#21252E"         # panels, cards, table, plot background
    surface_hover = "#272C36"
    surface_active = "#2E3440"
    surface_alt = "#1E222A"     # table alternating row (a hair off surface)
    border = "#2D323C"          # hairlines, gridlines, panel borders
    border_strong = "#3A414D"   # interactive/hover border, focus base

    text = "#DDE1E8"            # PRIMARY off-white (never pure white)
    text_dim = "#9AA1AD"        # secondary — the dimmest tier still allowed to carry ENABLED text
    # DISABLED / INACTIVE chrome ONLY (disabled buttons + menu items), plus non-text furniture
    # (scrollbar hover, drop glyph). 3.17:1 on `surface` — below WCAG AA, which is legitimate only
    # because WCAG 1.4.3 exempts inactive components. Any ENABLED text reads `text_dim` or better;
    # see the QSS roles below and tests/test_contrast.py, which enforces exactly that split.
    text_muted = "#6B7280"

    # --- accent (amber) ---
    accent = "#F5A623"
    accent_hover = "#FFB838"
    accent_press = "#D98E12"
    accent_tint = "rgba(245,166,35,0.16)"   # menu/combo selection bg / fills
    on_accent = "#15181E"                   # text/icon ON an amber fill
    sel_bg = "#3A3326"                       # subtle warm-amber selected table row (solid)

    # --- semantics ---
    ahead = "#5DD6A0"           # ahead / success / best lap green
    behind = "#E8746B"          # behind / danger red
    best = "#B794F6"            # best-sector purple


# ====================================================================== spatial tokens
# The DIMENSIONAL half of the design system, and the newer half. The colour tokens above were
# always a locked set with a test behind them; every spacing, radius and control height in the app
# was a literal chosen at its own call site. Measured before this block existed, the stylesheet
# alone carried 21 distinct px values, 6 border radii and 12 different padding pairs — among them
# `5px 11px` and `4px 9px`, which nobody chooses. You arrive at those by nudging, and a UI built
# out of nudges reads as assembled rather than designed however good its colours are.
#
# The rule is the same one `class C` has: pick a token, or make the case here for a new step.
# tests/test_design_system.py fails the build on an off-scale value in this file's stylesheet.
#
# THE SCALE IS 4 px BASED WITH ONE 2 px SUB-STEP, and the sub-step is a role, not an escape hatch.
# Four pixels is the least air two SEPARATE things can have and still read as separate at this type
# size, so SPACE_XS is the floor for a gap BETWEEN elements. SPACE_XXS is for the gap WITHIN one
# element — a value and its own caption, a bar and its own segments, a chip's text and its own
# tint — where 4 px already reads as a break and 2 px reads as "these are one thing". Every step
# is a whole multiple of SPACE_XXS.
SPACE_XXS, SPACE_XS, SPACE_S, SPACE_M = 2, 4, 8, 12
SPACE_L, SPACE_XL, SPACE_2XL, SPACE_3XL = 16, 24, 32, 48
#  XXS  2  intra-component: value↔its caption, an accent rule's weight, a chip's vertical padding
#  XS   4  the tightest gap between two separate things; a control's VERTICAL padding
#  S    8  the default gap inside a row or bar; a control's tight horizontal padding
#  M   12  a control's HORIZONTAL padding; the default gutter inside a panel
#  L   16  separation between GROUPS inside one surface
#  XL  24  a page's own breathing room (empty states, dialog bodies)
#  2XL 32  } the two large-surface insets — the welcome drop zone is the only thing wide enough
#  3XL 48  } to need them, and it needs both (48 across, 32 down)

# Corner radii, by what the thing IS rather than by how big it looks: a control, a card, a large
# surface. Three steps replace six, and the mapping is mechanical — if you are styling something
# the pointer can press, it is RADIUS_S.
RADIUS_S, RADIUS_M, RADIUS_L = 4, 8, 16   # controls · cards & chips · large surfaces

# --- sizes ---
# Heights that are DECLARED rather than emergent. Before this, the app had two icon-button size
# families (26x24 and 32x30) and four panel headers whose heights were whatever their tallest
# child happened to be.
BORDER_PX = 1                # the app's hairline: every control border, every panel rule
CTRL_H = 28                  # every button and combo in a header or a toolbar
ICON_BTN = QSize(28, 28)     # square icon button — replaces the 26x24 / 32x30 split (Phase 3)
PANEL_HDR_H = 36             # a panel's identity row, declared not emergent (Phase 2)
TOOLBAR_H = 32               # a panel's control row, where it has one (Phase 2)
HIT_MIN = 24                 # pointer-target floor — nothing clickable may be smaller
SPLITTER_HANDLE_PX = 8       # divider hit area (see the splitter section of the stylesheet)
FOCUS_RING_PX = 2            # keyboard focus ring width (see the focus-ring section)


def ctrl_content_h(total: int = CTRL_H, pad_v: int = SPACE_XS,
                   border_v: int = 2 * BORDER_PX) -> int:
    """A control's QSS `min-height` so that its OUTER height is `total`.

    `border_v` is the rule's TOTAL vertical border, so it reads straight off the rule it belongs
    to: a boxed control (`border: 1px`) charges 2 * BORDER_PX — the default, since most do — a menu
    row charges 0, and a tab, whose only border is its SPACE_XXS underline, charges SPACE_XXS.

    Qt's stylesheet box model reads `min-height` as the CONTENT rectangle and then adds the rule's
    own padding and borders on top, so a rule that wants a 28 px control cannot just say 28.

    This helper is the whole reason CTRL_H can BE 28. A QPushButton in this theme has a 16 px
    content height (Inter at BODY), so padding alone lands it on 24 (SPACE_XS) or 32 (SPACE_S) and
    nothing between — 28 is simply not reachable by choosing a spacing step, which is why the
    shipped button carried an off-scale 6 px padding and stood 30 px tall. Declare the height, then
    derive the stylesheet number from it, and every button, combo and tab shares one measurement.

    tests/test_design_system.py reconstructs the outer height of every min-height rule in the
    stylesheet — value + that rule's own padding + that rule's own border — and requires the result
    to be a declared size token, so a later nudge to EITHER half fails the build."""
    return total - 2 * pad_v - border_v


def focus_pad(v: int) -> int:
    """A padding step minus the pixel the focus ring's extra border takes.

    The ring is FOCUS_RING_PX where the resting border is BORDER_PX, so a `:focus` rule that kept
    its base padding would grow its control by 2 px in each axis every time the keyboard landed on
    it. Handing that pixel back out of the padding keeps the OUTER box identical, which is the
    contract tests/test_focus_cues.py pins ("nothing may change size or position between the two
    states").

    The shipped `5px 11px` and `4px 9px` — the two paddings in the whole stylesheet that look most
    obviously nudged — were exactly this compensation, computed by hand off two different bases.
    They are not off-scale values; they are a scale value minus a known constant, and saying so in
    code is what lets the guard verify the relationship instead of exempting the numbers."""
    return v - (FOCUS_RING_PX - BORDER_PX)


# ====================================================================== accessible palette
# The three semantic colours that carry MEANING BY HUE — ahead/behind on the Δ readout + rainbow,
# and the two "best" cues in the lap table (best lap, best sector) — are the accessibility hazard:
# ~8% of males can't tell the default red/green apart. So they live behind a PALETTE SELECTOR (one
# source of truth) rather than as raw hex sprinkled through the views. The default palette is the
# original red/green/purple (no change for existing users); the "colour-blind-safe" palette swaps
# in a blue/orange deuteranopia-safe axis (+ a distinct teal best-sector). Views read these through
# the accessor functions below (ahead_colour / behind_colour / best_lap_colour / best_sector_colour
# / ramp_mid_colour / delta_colour / rainbow_colors), NEVER C.ahead / C.behind / C.best directly,
# so flipping the palette recolours every surface at once.
#
# THIS IS A CALL-TIME CONTRACT, and a MODULE CONSTANT silently breaks it: a name bound once at
# import (`SERIES_BEST = C.ahead`) captures the palette that was active at import and can never
# move again, so the surface it feeds freezes on the flip while every accessor-fed surface beside
# it changes. That is exactly what happened to the charts' best-lap curve, and
# tests/test_contrast.py::test_no_module_constant_freezes_a_palette_hue now fails the build if any
# module in studio/ re-introduces one.
PALETTE_STANDARD = "standard"
PALETTE_COLORBLIND = "colorblind"

# blue = ahead/faster/success, orange = behind/slower — the standard deuteranopia-safe pair (they
# stay distinct under red-green colour blindness AND in greyscale, unlike red/green). Best lap reads
# as "success" so it shares the ahead blue; the best-sector cue needs to differ from the best-lap
# cue, so it takes a distinct teal (also CB-safe against both blue and orange).
#
# "mid" is the map ramp's MIDDLE anchor (see rainbow_colors) and is PER-PALETTE for a measured
# reason. The default ramp red -> amber -> green already separates cleanly, so it keeps the amber
# accent. Reusing that amber in the colour-blind palette killed the ramp's whole lower half: amber
# #F5A623 sits right next to the CB "behind" orange #F0902B, so buckets 0..7 spanned deuteranopic
# CIE76 dE 7.5 with per-bucket steps of 0.90-1.16 — below the ~2.3 JND, i.e. a flat orange bar over
# half the speed range, and 5.4x WORSE than the same half of the DEFAULT ramp (40.3). The CB
# palette therefore diverges through a light warm neutral instead, which is the textbook CB-safe
# orange -> light -> blue diverging scheme: lower half 58.3, upper 69.0, minimum step 7.01 (3x JND).
_PALETTES = {
    PALETTE_STANDARD:  {"ahead": C.ahead, "behind": C.behind, "best": C.best, "mid": C.accent},
    PALETTE_COLORBLIND: {"ahead": "#4C9BFF", "behind": "#F0902B", "best": "#38C7C7",
                         "mid": "#EDE7DC"},
}

# The active palette name. Set once at startup from the persisted pref (see set_palette / the app);
# defaults to STANDARD so nothing changes for an existing user and every not-yet-migrated call site
# renders as before.
_active_palette = PALETTE_STANDARD


def set_palette(name: str) -> None:
    """Select the active semantic palette (PALETTE_STANDARD / PALETTE_COLORBLIND). Unknown names
    fall back to STANDARD. Changes what ahead_colour / behind_colour / best_*_colour /
    ramp_mid_colour / delta_colour / rainbow_colors return, so the caller re-renders the delta
    readout, lap table, charts and rainbow map afterwards. The single toggle for the colour-blind-
    safe option."""
    global _active_palette
    _active_palette = name if name in _PALETTES else PALETTE_STANDARD


def active_palette() -> str:
    """The active palette name — for the app to reflect the current View-menu checkmark / persist."""
    return _active_palette


def ahead_colour() -> str:
    """The 'ahead / faster / success' hue for the ACTIVE palette (green by default, blue in the
    colour-blind palette). The one place the ahead colour is resolved."""
    return _PALETTES[_active_palette]["ahead"]


def behind_colour() -> str:
    """The 'behind / slower' hue for the active palette (red by default, orange colour-blind)."""
    return _PALETTES[_active_palette]["behind"]


def best_lap_colour() -> str:
    """The overall-best-lap foreground for the active palette. Reads as 'success', so it shares the
    ahead hue (green / blue) — matching the lap-table best-lap cells to the chart's best-lap curve."""
    return ahead_colour()


def best_sector_colour() -> str:
    """The per-sector session-best foreground for the active palette (purple by default, teal in the
    colour-blind palette). Distinct from best_lap_colour so the two 'best' cues never collide."""
    return _PALETTES[_active_palette]["best"]


def ramp_mid_colour() -> str:
    """The map ramp's MIDDLE anchor for the active palette (amber by default, a light warm neutral
    in the colour-blind palette — see _PALETTES for the measured reason). Only rainbow_colors reads
    it; it exists as an accessor, not a constant, so the ramp can never freeze mid-flip."""
    return _PALETTES[_active_palette]["mid"]


# Categorical lap-curve palette (amber accent first). These are IDENTITY colours (which lap is
# which), not the ahead/behind SEMANTIC hues, so they are palette-independent — the map already
# carries no red/green meaning here. The best lap is NOT in this list: it is drawn in
# best_lap_colour() at draw time so it always matches the lap table, in either palette.
CHART_SERIES = [
    C.accent,    # amber  — primary / first lap (also the app accent)
    "#5BC8E0",   # cyan
    C.best,      # purple
    "#7FA8F5",   # soft blue
    "#E89B6B",   # coral / soft orange
    "#9FD66B",   # lime-leaning green (distinct from the best-lap C.ahead green)
]

# A THIRD identity channel, for the layers that are filled GLYPHS rather than stroked lines. #156
# gave the curves and the legend a per-slot dash pattern (plots_view.SERIES_DASH), but a brake
# marker has no stroke to dash: it was one filled triangle in six hues, so on that layer hue was
# still the only cue. Two CHART_SERIES pairs do not survive deuteranopia — measured CIE76 under the
# Machado-2009 severity-1.0 matrix tests/test_contrast.py uses (JND 2.3):
#     slot 2 #B794F6 vs slot 3 #7FA8F5 = 1.27   (26.27 to normal vision) — BELOW the JND
#     slot 4 #E89B6B vs slot 5 #9FD66B = 7.90   (60.97 to normal vision) — weak
# Every other pair is >= 20.01, so colour already separates them and the shape budget is spent
# where colour is weakest: slots 2/3 get the most distinct pair in the set (square vs diamond,
# mask distance 1-IoU = 0.56 at the 9 px worst case) and slots 4/5 the next (0.41).
#
# All six are FILLED shapes with the same bounding box, because glyph SIZE is already spent
# encoding peak decel (brake_glyph_size, 9-18 px): pyqtgraph scales every symbol into the same
# box, so extent — which is what the eye reads as "bigger" — is identical across shapes (measured
# 10x10 at size 9 and 19x19 at size 18 for all of them) and the decel ramp still reads. Slot 0
# stays "t", so the one-lap default and the map's single-lap trace draw exactly what they drew.
#
# A SLOT'S SHAPE IS NOT FREE: the brake glyphs share a canvas with marker classes that are not
# laps, and those own shapes of their own (RESERVED_SYMBOLS). The first cut of this list handed
# slot 1 the circle — which is the map's corner-apex dot, in the SAME hue, because
# map_view.CORNER_LEFT_COLOR *is* CHART_SERIES[1]. Hue had never separated those two classes;
# shape was the only thing that did, and giving the lap glyphs a shape channel spent it. Slot 1
# is a star instead: mask distance 1-IoU 0.645 from the circle at the 9 px worst case — more
# separation than the 0.60 the shape-only slot 2/3 pair gets — and >= 0.45 from every other slot,
# above this list's own 0.41 floor. Measured with the same rasterized-mask method as the pairs
# above, on the very QPainterPaths pyqtgraph draws.
SERIES_SYMBOL = [
    "t",     # slot 0  triangle-down (the shipped glyph)
    "star",  # slot 1  5-point star (NOT the circle — see RESERVED_SYMBOLS)
    "s",     # slot 2  square   ┐ the deuteranopic dE 1.27 pair: this is the one that
    "d",     # slot 3  diamond  ┘ has to be carried by shape alone
    "t1",    # slot 4  triangle-up ┐ the dE 7.90 pair
    "p",     # slot 5  pentagon    ┘
]

# Shapes that belong to a NON-lap marker class and are therefore not available to SERIES_SYMBOL.
# A lap glyph that takes one of these is separated from that class by hue alone — and on the map
# the hues are the same value, so it is separated by nothing at all. Both entries are the round
# silhouette: the corner-apex dots and the apex highlight ring are plain ScatterPlotItems (default
# symbol "o"), and the video-position marker is a pg.TargetItem, whose "crosshair" path fills to
# the same circle. tests/test_charts_panel.py fails the build on any overlap.
RESERVED_SYMBOLS = ("o", "crosshair")


def series_slot(colour) -> int:
    """The CHART_SERIES identity slot a lap glyph colour belongs to (0 when it is not an identity
    colour — the always-on BEST lap, which is drawn in best_lap_colour()).

    Derived from the colour rather than plumbed alongside it on purpose: the glyph's shape and its
    hue then come from the same value and can never disagree, and the shape automatically matches
    the dash pattern of the curve it rides (both key off the same slot). The best lap falling back
    to slot 0's triangle is safe by measurement — its hue is >= 17.0 deuteranopic dE from every
    identity colour in BOTH palettes, so colour separates it from a slot-0 lap on its own."""
    try:
        want = QColor(colour).name().upper()
    except Exception:
        return 0
    for i, c in enumerate(CHART_SERIES):
        if QColor(c).name().upper() == want:
            return i
    return 0


def series_symbol(colour) -> str:
    """The pyqtgraph glyph symbol for a lap colour (see SERIES_SYMBOL / series_slot)."""
    return SERIES_SYMBOL[series_slot(colour) % len(SERIES_SYMBOL)]


# Track-map current lap coloured by a channel (speed / Δ-vs-best), quantized into MAP_RAINBOW_N
# buckets through the behind → accent → ahead ramp so it matches the Δ readout. The ramp endpoints
# follow the ACTIVE palette (see rainbow_colors), so the colour-blind option recolours the map too.
MAP_RAINBOW_N = 16  # rainbow buckets (one PlotCurveItem each); smooth enough, cheap enough


def _hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def qcolor(token: str, alpha: int | None = None) -> QColor:
    """A `QColor` from a theme hex token, with an optional alpha override (0-255). The one shared
    home for this primitive — the g-meter overlay and the video-export compositor both had a
    byte-identical local `_c`."""
    col = QColor(token)
    if alpha is not None:
        col.setAlpha(alpha)
    return col


def rainbow_colors(n: int = MAP_RAINBOW_N) -> list[str]:
    """`n` hex colours low→high along the behind → mid → ahead ramp (index 0 = slow/losing,
    n-1 = fast/gaining). ALL THREE anchors follow the ACTIVE palette, so the map ramp matches the Δ
    readout in both the default (red→amber→green) and colour-blind (orange→neutral→blue) palettes —
    and the colour-blind ramp keeps a usable lower half, which a shared amber mid anchor destroyed
    (see _PALETTES["mid"])."""
    anchors = [_hex_rgb(behind_colour()), _hex_rgb(ramp_mid_colour()), _hex_rgb(ahead_colour())]
    out = []
    for i in range(n):
        t = i / (n - 1) * (len(anchors) - 1)
        k = min(int(t), len(anchors) - 2)
        f = t - k
        a, b = anchors[k], anchors[k + 1]
        rgb = (round(a[c] + (b[c] - a[c]) * f) for c in range(3))
        out.append("#{:02X}{:02X}{:02X}".format(*rgb))
    return out


# Dead band: |Δ| <= half a displayed centisecond reads as 'even', not ahead/behind.
DELTA_EVEN_EPS_S = 0.005


def delta_colour(d: float | None) -> str | None:
    """Three-way Δ colour for the ACTIVE palette: ahead_colour() if ahead, behind_colour() if
    behind, None (neutral) for no/even delta. Routes through the palette accessors so the colour-
    blind-safe option recolours every Δ surface (readout, chart, corner table) at once."""
    if d is None or abs(d) <= DELTA_EVEN_EPS_S:
        return None
    return ahead_colour() if d < 0 else behind_colour()


# Non-colour redundancy for the Δ ahead/behind cue: a small directional arrow paired with the
# already-signed number so "am I ahead or behind" reads WITHOUT hue (survives greyscale / colour
# blindness). Ahead (faster, Δ < 0) → ▲ "gaining"; behind (slower, Δ > 0) → ▼ "losing"; even → none.
# The sign (−/+) and the arrow agree, so the cue is doubly non-colour.
DELTA_AHEAD_ARROW = "▲"   # ahead / gaining (negative Δ)
DELTA_BEHIND_ARROW = "▼"  # behind / losing (positive Δ)


def delta_arrow(d: float | None) -> str:
    """The non-colour direction glyph for a Δ: ▲ ahead, ▼ behind, '' even/None. Pairs with the
    signed number so ahead-vs-behind never depends on hue alone (the accessibility redundancy)."""
    if d is None or abs(d) <= DELTA_EVEN_EPS_S:
        return ""
    return DELTA_AHEAD_ARROW if d < 0 else DELTA_BEHIND_ARROW


# --- trust tier: how an UNVERIFIED / estimated value reads ---------------------------------
# A value the product can't fully stand behind (provisional lap timing on an unconfirmed start
# line today; grip "est"/estimated channels next) is rendered MUTED + ITALIC so it visibly
# carries less authority than a validated figure, without hiding it. One source so every
# unverified surface (lap table, future estimate labels) reads identically. Apply via
# apply_provisional_style(item) on a QTableWidgetItem, or the [trust="provisional"] QSS role on
# a styled QLabel.
PROVISIONAL_COLOR = C.text_dim  # muted (not muted-most: still legible, just demoted)


def apply_provisional_style(item, on: bool = True) -> None:
    """De-emphasize a QTableWidgetItem as PROVISIONAL (muted + italic) when `on`, else restore a
    non-italic font (the caller sets the real foreground). Font-only here so callers keep owning
    the colour rules (best/dropout/base); colour is applied by the caller via PROVISIONAL_COLOR.
    Pacer-free, Qt-only — the single visual treatment for an unverified table value."""
    font = item.font()
    font.setItalic(bool(on))
    item.setFont(font)


# A separate, lighter trust tier from PROVISIONAL: an ESTIMATED value is a real inferred reading the
# product DOES stand behind as an estimate (grip utilisation, the brake/throttle band, brake-point
# hints), not an unverified one to demote. It carries the same "(est)"/ESTIMATED wording the inferred
# brake/throttle band + brake-point coaching already use, so every estimate reads identically; the
# cell stays full-strength (no muting/italic) because it IS a value to trust as an estimate — the
# label is what signals the tier.
# The ONE canonical short form for an inline "estimated" badge/suffix. Everything user-facing that
# marks a value as estimated uses this (grip column, brake-point hints, the brake/throttle legend),
# so the app never spells it four ways ("(est)"/"(EST)"/"ESTIMATED"/"(est.)"). Longer explanatory
# tooltip PROSE may still say the full word "estimated"; only the short chips/suffixes unify here.
ESTIMATED_MARK = "(est)"      # the bare canonical inline marker
ESTIMATED_SUFFIX = f" {ESTIMATED_MARK}"   # appended to a column/label title carrying an estimated value


def estimated_label(title: str) -> str:
    """`title` with the shared ESTIMATED marker appended — the one place the "(est)" wording lives,
    so the grip column, future estimate labels, etc. all read the same."""
    return f"{title}{ESTIMATED_SUFFIX}"


# Δ/speed text formatters: single source for the live #DiffBox and the burned-in export.
# Composable fragments so the two readouts can't drift.


# --- brake-glyph size ramp (shared by the map + speed-chart brake markers, so the two glyphs
# can't drift): peak decel (g) maps linearly between a floor and a cap. ---
BRAKE_MARKER_MIN_PX = 9      # glyph px at/below BRAKE_DECEL_LO (a light dab)
BRAKE_MARKER_MAX_PX = 18     # glyph px at/above BRAKE_DECEL_HI (a hard stomp)
BRAKE_DECEL_LO = 0.10        # g: floor of the size ramp
BRAKE_DECEL_HI = 0.45        # g: cap of the size ramp


def brake_glyph_size(peak_decel: float) -> float:
    """Brake-event peak decel (g) -> marker glyph size (px), clamped to the ramp ends.

    Returned in LOGICAL px and used as is: a pxMode=True ScatterPlotItem's `size` is already
    device-independent (measured: size=18 draws a 19x19 logical box at DPR 1 and 2 alike), unlike
    the pen widths next to it — see line_width."""
    frac = (float(peak_decel) - BRAKE_DECEL_LO) / max(BRAKE_DECEL_HI - BRAKE_DECEL_LO, 1e-6)
    return BRAKE_MARKER_MIN_PX + min(max(frac, 0.0), 1.0) * (BRAKE_MARKER_MAX_PX - BRAKE_MARKER_MIN_PX)


# ================================================================= HiDPI chart/map line weights
# pyqtgraph's mkPen sets `pen.setCosmetic(True)` unconditionally (0.14.0), and a COSMETIC pen's
# width is in DEVICE pixels — Qt does NOT multiply it by the painter's devicePixelRatio. So a
# `width=1` gridline is one DEVICE pixel at every DPR: full weight on a normal display and HALF
# weight on a Retina panel, while the plain Qt widgets beside it scale correctly. Measured on the
# shipped charts at a fixed 1512x982 LOGICAL screen: the delta plot's gridlines are 1.0 logical px
# at DPR 1 and 0.5 at DPR 2, and the always-on best-lap trace (deliberately the thinnest line in
# the app, width=1) degrades to a 0.5-logical-px hairline — in the window AND in the grabs the
# HTML report and the share card embed.
#
# So every chart/map pen width is a LOGICAL px design value that must be multiplied by the DPR
# before it reaches mkPen. `setCosmetic(False)` is NOT the fix: a non-cosmetic pen's width is in
# SCENE units, so it would then scale with the ViewBox zoom — a plot zoomed 4x would draw
# 4x-fat traces, which is a different behaviour, not a corrected one.
#
# THIS IS A CALL-TIME CONTRACT, exactly like the palette accessors above, and for the same reason:
# the DPR is a property of the SCREEN THE WINDOW IS ON, and it changes when the user drags the
# window from the Retina panel to an external monitor. A pen built once at import freezes the
# ratio that happened to be current at startup. Views therefore build their pens at draw time and
# re-resolve on QEvent.DevicePixelRatioChange (see plots_view/map_view/stats_panel `event` and
# library_dialog `showEvent`), and tests/test_charts_panel.py fails the build if a `width=` literal
# reaches mkPen directly — in EVERY studio module that imports pyqtgraph, found by walking the
# package rather than by a list of file names. The list of names was the bug: it held
# plots_view.py and map_view.py, so the Stats page and the Library's PB chart kept drawing one
# device pixel while the guard reported green.
#
# A BARE COLOUR IS A WIDTH TOO. `axis.setPen(C.border)` looks like it sets no width, but pyqtgraph
# builds a width-1 COSMETIC pen from it — the same half-weight hairline, and the one that draws the
# GRIDLINES (AxisItem falls back from tickPen() to pen()). The guard rejects those as well.
_pen_scale = 1.0


def pen_scale() -> float:
    """The device-pixel ratio chart/map pen widths are currently scaled by."""
    return _pen_scale


def set_pen_scale(dpr: float) -> bool:
    """Point the chart/map line weights at a new device-pixel ratio. Returns True when it actually
    moved, so a caller can skip an expensive re-draw on the (common) no-op."""
    global _pen_scale
    new = float(dpr) if dpr and float(dpr) > 0 else 1.0
    if abs(new - _pen_scale) < 1e-6:
        return False
    _pen_scale = new
    return True


def line_width(logical_px: float) -> float:
    """A design line weight in LOGICAL px -> the DEVICE-px width a cosmetic pyqtgraph pen needs.

    Every `pg.mkPen(..., width=...)` in the charts and the map goes through this. Dash patterns do
    NOT need it: Qt specifies a dash pattern in units of the PEN WIDTH, so scaling the width scales
    the pattern with it (measured: the [5,3] pattern draws 5/3 device px at width 1 and 12/4 at
    width 2 — multiplying the pattern too would double-scale it)."""
    return float(logical_px) * _pen_scale


def format_delta_value(d: float | None) -> str:
    """Δ number alone, no glyph/units: em dash for None, else signed 2dp (e.g. -0.31).

    A Δ inside the DELTA_EVEN_EPS_S dead band is snapped to +0.0 FIRST, so float noise can never
    print the meaningless `-0.00`: `f"{-1.8e-15:+.2f}"` is `-0.00`, which reads as "you are behind"
    on a lap where you are dead level, and the export burns it into the delivered MP4 where the
    recipient cannot correct it. The same dead band already drives delta_colour() and delta_arrow(),
    so all three now agree on what counts as 'even'."""
    if d is None:
        return "—"
    return f"{0.0 if abs(d) <= DELTA_EVEN_EPS_S else d:+.2f}"


def format_delta_run(d: float | None, *, units: bool = True, arrow: bool = True) -> str:
    """Δ <v> with an optional trailing ' s' (units=True live box, False export) and an optional
    trailing direction arrow (▲ ahead / ▼ behind). The arrow is the NON-COLOUR redundancy so the
    ahead/behind meaning survives greyscale / colour blindness; the signed value (−/+) already
    agrees with it. `arrow=False` for plain-number contexts (tooltips that name the direction in
    words)."""
    v = format_delta_value(d)
    a = f" {delta_arrow(d)}" if (arrow and delta_arrow(d)) else ""
    if d is None:
        return f"Δ {v}"
    return f"Δ {v} s{a}" if units else f"Δ {v}{a}"


def format_speed_run(speed_kmh: float | None, lap: int | None,
                     unit: str | None = None) -> str:
    """<n> <unit> while a lap is current, else '— <unit>' (no misleading speed outside a lap).
    `unit` (km/h default) converts at the DISPLAY boundary only — `speed_kmh` stays km/h."""
    label = units.speed_label(unit)
    if speed_kmh is None or lap is None:
        return f"— {label}"
    return f"{units.convert_speed(speed_kmh, unit):.0f} {label}"


def speed_number(speed_kmh: float | None, lap: int | None,
                 unit: str | None = None) -> str:
    """Speed number alone (no unit), same no-lap gate as format_speed_run: the rounded speed in
    `unit` (km/h default) or an em dash."""
    if speed_kmh is None or lap is None:
        return "—"
    return f"{units.convert_speed(speed_kmh, unit):.0f}"


def format_delta_speed(d: float | None, speed_kmh: float | None,
                       lap: int | None, unit: str | None = None) -> tuple[str, str | None]:
    """Combined live readout: (text, colour). text = 'Δ <v> s<5 spaces><n> <unit>'; colour =
    delta_colour(d). `unit` (km/h default) applies to the speed number only."""
    text = f"{format_delta_run(d)}     {format_speed_run(speed_kmh, lap, unit)}"
    return text, delta_colour(d)


# The hero readout LEADS with Δ-to-IDEAL — the product's moat number ("how far off your own
# achievable lap are you, right here") — rather than Δ-to-best. Labelled "Δideal" so it can never be
# read as the plain best-lap Δ; the IDEAL gap is always ≥ 0 (you can't beat the envelope you helped
# form), so it carries the single "behind"/amber colour rather than the two-way ahead/behind ramp.
def format_ideal_readout(d_ideal: float | None, speed_kmh: float | None,
                         lap: int | None, unit: str | None = None) -> tuple[str, str | None]:
    """Hero #DiffBox readout (text, colour): 'Δideal <v> s<5 spaces><n> <unit>', leading with the
    Δ-to-ideal scalar. `d_ideal` is `Session.delta_to_ideal_at` (≥ 0 by construction, None outside a
    lap / before an ideal exists). Colour = `C.behind` when there's real time on the table, else
    neutral — there is no "ahead of ideal", so this never goes green. `unit` (km/h default) applies
    to the speed number only."""
    v = format_delta_value(d_ideal)
    delta_run = f"Δideal {v}" + (" s" if d_ideal is not None else "")
    text = f"{delta_run}     {format_speed_run(speed_kmh, lap, unit)}"
    # behind_colour(), NOT the raw C.behind token: this is the app's LARGEST text and it carries
    # the ahead/behind meaning, so it must follow the colour-blind palette like every other Δ
    # surface. Read as a constant it stayed the standard red in BOTH palettes (max per-channel
    # |Δ| = 0 over the whole readout) while the Corners table 130 px below painted the same
    # meaning in the palette's orange.
    colour = behind_colour() if (d_ideal is not None and d_ideal > DELTA_EVEN_EPS_S) else None
    return text, colour


# Seek a few ms INTO a lap: an exact-boundary seek rounds down to the previous lap. Shared by the
# lap-table and compare seeks.
LAP_SEEK_NUDGE_S = 0.010


# --- type scale (px) ---
# FOUR steps, FOUR roles: 11 / 13 / 15 / 22. It used to be 11/12/13/22, with three of the four
# sizes inside two pixels of each other and the 12 doing no job the 11 or the 13 was not already
# doing — so what actually separated a value from the label about it was colour, not size, and the
# hierarchy vanished for anyone reading in greyscale. CAPTION moved 12 -> 11 (it joins the small-
# caps chrome it always sat beside) and EMPHASIS 15 was promoted out of stats_panel, which had
# discovered on its own that a tile value needs a step between BODY and HERO.
HERO = 22          # the one live number read at a glance while driving (the Δideal readout)
EMPHASIS = 15      # a VALUE that must outrank its own label (the Stats tiles)
BODY = 13          # prose, table cells, button labels — the app default (apply_theme's setFont)
TABLE = 13         # table cell text — BODY's role inside a grid
CAPTION = 11       # a label ABOUT a value, and running notes (banners, readouts, tile captions)
PANEL_HEADER = 11  # small-caps panel identity — CAPTION's step, its own role (Phase 2 may move it)
TABLE_HEADER = 11  # column headers and badges — likewise

# --- weights ---
W_REGULAR = QFont.Weight.Normal     # 400
W_SEMIBOLD = QFont.Weight.DemiBold  # 600

# --- font stacks ---
# ONE list per face, in fallback order, used BOTH by the QFont builders below (setFamilies) and by
# the QSS font-family declarations (*_STACK). They were two hand-kept copies; nothing painted the
# UI stack from QSS any more (see the base rule in _build_qss), so a drift between them would have
# been invisible.
UI_FAMILIES = ("Inter", "-apple-system", "SF Pro Text", "Helvetica Neue", "sans-serif")
MONO_FAMILIES = ("SF Mono", "JetBrains Mono", "Menlo", "monospace")
UI_STACK = ",".join(f'"{f}"' for f in UI_FAMILIES)
MONO_STACK = ",".join(f'"{f}"' for f in MONO_FAMILIES)

_FONTS_DIR = os.path.join(os.path.dirname(__file__), "assets", "fonts")
# rsms/inter OFL static TTFs bundled under assets/fonts/.
_INTER_FILES = ("Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf")

# Set by register_fonts() so a second call (apply_theme's) is a cheap no-op.
_fonts_registered = False
# Set by register_fonts(): True once Inter is registered with the Qt font DB.
_inter_available = False
# Set by register_fonts(): does the installed Qt support per-feature tags (tnum)? Qt ≥ 6.7.
_supports_feature = False


# ====================================================================== fonts
def _qt_supports_feature() -> bool:
    """QFont.setFeature (OpenType feature tags such as 'tnum') landed in Qt/PySide6 6.7."""
    try:
        major, minor = (int(p) for p in PYSIDE_VERSION.split(".")[:2])
    except ValueError:
        return False
    return (major, minor) >= (6, 7) and hasattr(QFont, "setFeature")


def register_fonts() -> None:
    """Register the bundled Inter TTFs (assets/fonts/); skip to the system font fallback if absent.
    Also records Qt tnum support.

    Idempotent, and called by apply_theme() — the two belong together (the QSS and ui_font() both
    name "Inter", which only exists once the TTFs are in the font DB), and six test files proved
    they can drift apart: they called apply_theme alone, so Qt substituted a family for the one the
    theme names and they measured a layout 7 px narrower than the shipped one."""
    global _fonts_registered, _inter_available, _supports_feature
    if _fonts_registered:
        return
    _fonts_registered = True
    _supports_feature = _qt_supports_feature()

    have_files = all(os.path.exists(os.path.join(_FONTS_DIR, f)) for f in _INTER_FILES)
    if not have_files:
        _inter_available = False
        print("theme: Inter not bundled — using system font fallback "
              f"({UI_STACK}).", flush=True)
        return

    registered = 0
    for f in _INTER_FILES:
        fid = QFontDatabase.addApplicationFont(os.path.join(_FONTS_DIR, f))
        if fid != -1:
            registered += 1
    _inter_available = registered > 0
    if _inter_available:
        print(f"theme: Inter registered (bundled, {registered}/{len(_INTER_FILES)} faces); "
              f"tabular figures via {'tnum feature' if _supports_feature else 'mono stack'}.",
              flush=True)
    else:
        print("theme: Inter TTFs present but failed to register — using system fallback.",
              flush=True)


def ui_font(size: int = BODY, weight: QFont.Weight = W_REGULAR) -> QFont:
    """The UI sans face. Prefers bundled Inter; otherwise the first available system fallback."""
    family = "Inter" if _inter_available else "-apple-system"
    f = QFont(family, size)
    f.setWeight(weight)
    # Fallback families for when `family` itself is missing (Qt walks substitutes).
    f.setFamilies(list(UI_FAMILIES))
    f.setPixelSize(size)
    return f


def mono_font(size: int = TABLE, weight: QFont.Weight = W_REGULAR) -> QFont:
    """Tabular-figures face for column-aligning digits: Inter+tnum on Qt≥6.7, else the mono stack."""
    if _supports_feature:
        f = ui_font(size, weight)
        try:
            f.setFeature("tnum", 1)  # tabular figures (Qt ≥ 6.7 accepts a str tag)
        except Exception:
            pass
        return f
    f = QFont("SF Mono", size)
    f.setWeight(weight)
    f.setFamilies(list(MONO_FAMILIES))
    f.setPixelSize(size)
    return f


# ====================================================================== icons
def icon(name: str, color: str | None = None) -> QIcon:
    """Themed QIcon from qtawesome's Phosphor set (e.g. 'ph.play-fill'), tinted to color
    (default C.text). ACTIVE tint: when the caller chose an explicit color, keep it for the
    active state too — the old unconditional C.accent turned an explicitly-tinted glyph
    invisible on an accent background the moment its button was focused/default (B10: the
    coaching Jump arrow — C.on_accent on the amber primary — went amber-on-amber). The
    default-tinted case keeps the accent active state. Lazy import: blank QIcon if qtawesome
    is missing."""
    try:
        import qtawesome as qta
    except Exception as exc:  # missing dep / font load failure — degrade, don't crash
        print(f"theme: qtawesome unavailable ({exc}); icon '{name}' will be blank. "
              "Install it via `pixi install` (the qtawesome pypi dependency).", flush=True)
        return QIcon()
    return qta.icon(name, color=color or C.text, color_active=color or C.accent)


_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
# Cached path of the generated combobox-chevron PNG (set on first _caret_down_asset() success).
_caret_asset_path: str | None = None


def _caret_down_asset() -> str | None:
    """Render ph.caret-down tinted to C.text_dim to a cached PNG for QComboBox::down-arrow,
    because QSS has no transform so the old border arrow renders as an L-bracket. Returns None →
    native arrow (qtawesome missing / render fails)."""
    global _caret_asset_path
    if _caret_asset_path is not None:
        return _caret_asset_path
    try:
        import qtawesome as qta
        from PySide6.QtCore import QSize
        # @2x source so the down-scaled 12px arrow stays crisp on HiDPI displays.
        px = qta.icon("ph.caret-down", color=C.text_dim).pixmap(QSize(24, 24))
        os.makedirs(_ASSETS_DIR, exist_ok=True)
        path = os.path.join(_ASSETS_DIR, "caret-down.png")
        if not px.save(path, "PNG"):
            return None
    except Exception as exc:  # missing dep / render / IO — degrade to the native arrow
        print(f"theme: caret-down asset unavailable ({exc}); using native combo arrow.",
              flush=True)
        return None
    _caret_asset_path = path
    return path


# ====================================================================== palette
def _palette() -> QPalette:
    """A dark QPalette so framework-drawn chrome (native dialogs, default widget bits not covered
    by the QSS) matches the theme rather than the OS light defaults."""
    p = QPalette()
    window = QColor(C.canvas)
    base = QColor(C.surface)
    text = QColor(C.text)
    surface = QColor(C.surface)
    muted = QColor(C.text_muted)

    p.setColor(QPalette.ColorRole.Window, window)
    p.setColor(QPalette.ColorRole.Base, base)
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(C.surface_alt))
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.Button, surface)
    p.setColor(QPalette.ColorRole.BrightText, QColor(C.behind))
    p.setColor(QPalette.ColorRole.Highlight, QColor(C.sel_bg))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(C.text))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(C.surface_hover))
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    # Placeholder text is ENABLED prose in an enabled field, so it takes text_dim (5.90:1), not
    # text_muted (3.17:1, WCAG-exempt only because it is reserved for DISABLED chrome below).
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(C.text_dim))
    p.setColor(QPalette.ColorRole.Link, QColor(C.accent))

    # Disabled states read muted everywhere text/foreground is drawn.
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText,
                 QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, muted)
    return p


# ====================================================================== QSS
# Every dimension below comes from the spatial-token block at the top of this file (SPACE_*,
# RADIUS_*, CTRL_H, ctrl_content_h, focus_pad). SPLITTER_HANDLE_PX and FOCUS_RING_PX used to be
# declared right here, beside the two sections that read them; they moved up so that all of the
# app's dimensional tokens live in one place, the way all of its colours do.


def _splitter_grip(orientation: str, colour: str) -> str:
    """`background` value painting a thin `colour` grip bar centred across an otherwise-canvas
    splitter handle. A gradient paints INSIDE the handle box, so unlike a border/margin grip it
    adds nothing to the handle's size hint."""
    axis = "x2:1, y2:0" if orientation == "horizontal" else "x2:0, y2:1"
    return (f"qlineargradient(x1:0, y1:0, {axis}, "
            f"stop:0 {C.canvas}, stop:0.374 {C.canvas}, stop:0.375 {colour}, "
            f"stop:0.625 {colour}, stop:0.626 {C.canvas}, stop:1 {C.canvas})")


def _build_qss() -> str:
    """Assemble the global stylesheet from tokens, in editable sections.

    GOTCHA: a QPushButton/QToolButton custom `background` only renders if `border` is ALSO set —
    every button rule below sets border explicitly.

    NOTE: QVideoWidget is intentionally NOT styled here. A global opaque background on its native
    video surface can blank the frame on macOS; we leave it to the palette.
    """
    # Down-chevron rule for QComboBox (see _caret_down_asset); falls back to native arrow when the
    # PNG is unavailable.
    caret = _caret_down_asset()
    if caret:
        caret_url = caret.replace(os.sep, "/")  # QSS url() wants forward slashes on every OS
        caret_arrow_rule = f"""QComboBox::down-arrow {{
    image: url({caret_url});
    width: {SPACE_M}px; height: {SPACE_M}px;
    margin-right: {SPACE_XS}px;
}}
QComboBox::down-arrow:on {{  /* open: nudge so it reads as pressed, no flip */
    top: {BORDER_PX}px;
}}"""
    else:
        caret_arrow_rule = "/* QComboBox::down-arrow: native arrow (asset unavailable) */"
    return f"""
/* ---------------------------------------------------------------- base
   COLOURS ONLY — the default FONT is set once, in apply_theme, with app.setFont(ui_font(BODY)).

   NEVER put `font-family` / `font-size` back on this blanket QWidget rule. A stylesheet font is
   resolved OVER the widget's own font on every polish (QStyleSheetStyle::updateStyleSheetFont
   does `rule.font.resolve(w->font())`), so a rule that matches every widget silently discards the
   size and family of EVERY programmatic setFont in the app — a call that looks like it works,
   reads like it works, and paints 13px. That is exactly what it did: the Stats page's 29 tiles
   asked for mono_font(15, semibold) over ui_font(12) and painted (13, 13) — value and caption the
   same size, hierarchy gone — while three caption labels asked for 12 and got 13, the coaching
   PhaseBar numbers asked for 12 and got 13, and the Library dialog's summary/privacy notes asked
   for 11 and got 13. Nothing could see it: setFont() reported success and font() echoed back the
   font that was thrown away. #DiffBox below is the scar tissue from the first time this bit —
   the hero Δ readout had to re-declare its own setFont in QSS to survive.

   app.setFont gives the identical default (Inter/UI_STACK at BODY px) and LOSES to setFont, which
   is the whole point; a widget that wants a different size may still take a rule of its own (the
   role/objectName rules further down), it just no longer has to. */
QWidget {{
    background-color: {C.canvas};
    color: {C.text};
}}
QMainWindow, QWidget#centralwidget {{
    background-color: {C.canvas};
}}

/* ---------------------------------------------------------------- menus */
QMenuBar {{
    background-color: {C.surface};
    color: {C.text};
    border-bottom: {BORDER_PX}px solid {C.border};
}}
QMenuBar::item {{
    background: transparent;
    padding: {SPACE_XS}px {SPACE_M}px;
}}
QMenuBar::item:selected {{
    background-color: {C.accent_tint};
    color: {C.text};
}}
QMenu {{
    background-color: {C.surface};
    color: {C.text};
    border: {BORDER_PX}px solid {C.border};
    padding: {SPACE_XS}px;
}}
/* a menu row is a control: same CTRL_H as every button and combo, declared the same way (see
   ctrl_content_h — the row has no border of its own, so all of its chrome is the padding). */
QMenu::item {{
    padding: {SPACE_XS}px {SPACE_L}px;
    min-height: {ctrl_content_h(CTRL_H, SPACE_XS, border_v=0)}px;
    border-radius: {RADIUS_S}px;
}}
QMenu::item:selected {{
    background-color: {C.accent_tint};
    color: {C.text};
}}
QMenu::item:disabled {{
    color: {C.text_muted};
}}
QMenu::separator {{
    height: {BORDER_PX}px;
    background: {C.border};
    margin: {SPACE_XS}px {SPACE_S}px;
}}

/* ---------------------------------------------------------------- splitter
   An {SPLITTER_HANDLE_PX}px hit area with a grip bar centred in it, so every divider — the
   video/table split, the map/plots split, the left/right split — reads as draggable (the user
   "couldn't resize the video" partly because the old 6px hairline was hard to grab). Hover turns
   the grip amber.

   NEVER give a handle rule a `margin` or `padding`. Qt's stylesheet box model adds them to BOTH
   axes of the handle's sizeHint, and QSplitter lays the handle out at that sizeHint — not at
   handleWidth(). The previous rule used `margin: 24px 3px` purely to inset a short grip
   vertically; those 48px landed in the WIDTH as well and turned the left/right divider into a
   67px dead band down the middle of the window. Nothing in code could see it: handleWidth() and
   PM_SplitterWidth both kept reporting 19. Draw the grip with a gradient instead (_splitter_grip)
   — it paints inside the box and costs no size. Pinned by
   tests/test_central_view_realqt.py::test_splitter_handles_stay_thin_under_the_theme. */
QSplitter {{
    background-color: {C.canvas};
}}
QSplitter::handle:horizontal {{
    width: {SPLITTER_HANDLE_PX}px;
    border: none; margin: 0; padding: 0;
    background: {_splitter_grip("horizontal", C.border_strong)};
}}
QSplitter::handle:vertical {{
    height: {SPLITTER_HANDLE_PX}px;
    border: none; margin: 0; padding: 0;
    background: {_splitter_grip("vertical", C.border_strong)};
}}
QSplitter::handle:horizontal:hover {{
    background: {_splitter_grip("horizontal", C.accent)};
}}
QSplitter::handle:vertical:hover {{
    background: {_splitter_grip("vertical", C.accent)};
}}

/* ---------------------------------------------------------------- scrollbars */
/* The track is SPACE_M wide with a SPACE_XXS inset, so the grip itself is SPACE_S — the same
   weight as the splitter grip and the scrub groove, which is what makes them read as one family.
   RADIUS_S on an 8 px grip is exactly a pill (the old 5 px radius on a 6 px grip was one too). */
QScrollBar:vertical {{
    background: transparent;
    width: {SPACE_M}px;
    margin: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: {SPACE_M}px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {C.border_strong};
    min-height: {CTRL_H}px;
    border-radius: {RADIUS_S}px;
    margin: {SPACE_XXS}px;
}}
QScrollBar::handle:horizontal {{
    background: {C.border_strong};
    min-width: {CTRL_H}px;
    border-radius: {RADIUS_S}px;
    margin: {SPACE_XXS}px;
}}
QScrollBar::handle:hover {{
    background: {C.text_muted};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0; height: 0; background: none; border: none;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* ---------------------------------------------------------------- buttons
   Height is DECLARED (CTRL_H), not inferred from whatever padding looked right: see
   ctrl_content_h for why a `min-height` is the only way to reach 28 px with a scale-step padding,
   and why every button, combo, tab and menu row in the app can therefore be the same height.

   A `min-height` ON A BLANKET SELECTOR IS THE SIZE VERSION OF THE FONT TRAP AT THE TOP OF THIS
   FILE, and it is worth knowing before you add another one. QStyleSheetStyle::setGeometry pushes
   the rule's height straight into the widget with `w->setMinimumHeight(...)` and marks it with a
   `_q_stylesheet_minh` property — it does not merge with the widget's own minimum, it REPLACES
   it, and it does not consult the widget's MAXIMUM at all. Measured under this rule:

       setMinimumSize(24, 24)  ->  minimumHeight 28   (the widget's own 24 is gone)
       setFixedSize(26, 24)    ->  minimumHeight 28 with maximumHeight 24 — and it paints 26x28
       setFixedSize(32, 30)    ->  unaffected (30 already clears 28)

   So a widget that needs to be SHORTER than a control has to say so in the stylesheet, where this
   rule can lose to it, and not in Python, where it cannot. The PB toast's two flat buttons are
   exactly that case and take their HIT_MIN in a rule of their own further down. The four ⛶ panel
   buttons (`central_view._maximize_button`, setFixedSize(26, 24)) are the other case and are NOT
   fixed here: they are hand-sized below both CTRL_H and ICON_BTN, the theme now stands them at
   26x28, and giving them one square icon-button size is the control-vocabulary phase's job. */
QPushButton {{
    background-color: {C.surface};
    color: {C.text};
    border: {BORDER_PX}px solid {C.border};
    border-radius: {RADIUS_S}px;
    padding: {SPACE_XS}px {SPACE_M}px;
    min-height: {ctrl_content_h()}px;
}}
QPushButton:hover {{
    background-color: {C.surface_hover};
    border-color: {C.border_strong};
}}
QPushButton:pressed {{
    background-color: {C.surface_active};
}}
QPushButton:disabled {{
    color: {C.text_muted};
    border-color: {C.border};
    background-color: {C.surface};
}}
/* checked toggle: amber tint + accent border (glyph also recoloured in code) */
QPushButton:checked {{
    background-color: {C.accent_tint};
    border: {BORDER_PX}px solid {C.accent};
    color: {C.accent};
}}
/* PRIMARY variant via dynamic property: setProperty("variant","primary") */
QPushButton[variant="primary"] {{
    background-color: {C.accent};
    color: {C.on_accent};
    border: {BORDER_PX}px solid {C.accent};
}}
QPushButton[variant="primary"]:hover {{
    background-color: {C.accent_hover};
    border-color: {C.accent_hover};
}}
QPushButton[variant="primary"]:pressed {{
    background-color: {C.accent_press};
    border-color: {C.accent_press};
}}

/* ---------------------------------------------------------------- combo box */
QComboBox {{
    background-color: {C.surface};
    color: {C.text};
    border: {BORDER_PX}px solid {C.border};
    border-radius: {RADIUS_S}px;
    padding: {SPACE_XS}px {SPACE_M}px;
    min-height: {ctrl_content_h()}px;
}}
QComboBox:hover {{
    border-color: {C.border_strong};
}}
/* SPACE_L for the chevron well (SPACE_M of caret + SPACE_XS of air), which with the SPACE_M text
   padding either side costs a combo EXACTLY the 40 px of chrome the shipped 20/10/10 did — so the
   scale migration buys a combo no width at all. That is not a coincidence, it is a budget: this
   theme's combos live in the charts and map header bars, which are already over-subscribed, and
   the first cut of this rule (a SPACE_XL well) spent 8 px there and dropped the charts bar past
   the tier that still NAMES its baseline at 1280x800. tests/test_charts_header_budget.py caught
   it; do not widen this without re-running that file. */
QComboBox::drop-down {{
    border: none;
    width: {SPACE_L}px;
}}
{caret_arrow_rule}
QComboBox QAbstractItemView {{
    background-color: {C.surface};
    color: {C.text};
    border: {BORDER_PX}px solid {C.border};
    selection-background-color: {C.accent_tint};
    selection-color: {C.text};
    outline: none;
}}

/* ---------------------------------------------------------------- focus ring
   ONE focus language for every place the keyboard can land: a {FOCUS_RING_PX}px {C.accent_hover}
   ring. It lives here, after the button/combo sections, so it also wins over :hover. Two
   properties of it are load-bearing and easy to lose again:

   (1) THE RING IS RESERVED IN BOTH STATES — each rule below either takes the extra border pixel
       back out of the padding (buttons, combos) or reserves an invisible border in the resting
       state (tables, plot canvases), so arriving only ever RECOLOURS pixels. The obvious
       one-liner — a bare `:focus {{ border: … }}` over a `border: none` base — is not a fix: on
       the lap table it painted exactly 189 px down the LEFT EDGE ONLY (the header covers the
       top, the scrollbar the right, and the frame is only 1 px wide) and it resized the content
       box under the user every time focus arrived.

   (2) IT IS {FOCUS_RING_PX}px {C.accent_hover}, NOT the 1px {C.accent} that `QPushButton:checked`
       already owns. While the two matched, all four checkable toggles in the app changed by
       exactly ZERO pixels on focus — the controls that are ON were the one class a keyboard user
       could not see they had landed on, while the same buttons unchecked moved 222-512 px.

   tests/test_focus_cues.py pins both halves: every tab stop must change pixels when focused,
   checked included, and nothing may change size or position between the two states. */
QPushButton:focus, QPushButton:checked:focus {{
    border: {FOCUS_RING_PX}px solid {C.accent_hover};
    /* the base padding minus the extra border px — identical outer box; see focus_pad */
    padding: {focus_pad(SPACE_XS)}px {focus_pad(SPACE_M)}px;
}}
/* an amber ring on the amber primary fill is invisible, so that one variant rings in its own
   ink colour instead (the same dark the label already uses). */
QPushButton[variant="primary"]:focus {{
    border: {FOCUS_RING_PX}px solid {C.on_accent};
    padding: {focus_pad(SPACE_XS)}px {focus_pad(SPACE_M)}px;
}}
/* flat text buttons (the PB toast) have no border to ring — they take the hover treatment. */
QPushButton#PBToastLink:focus, QPushButton#PBToastClose:focus {{
    color: {C.accent_hover};
    text-decoration: underline;
}}
QComboBox:focus {{
    border: {FOCUS_RING_PX}px solid {C.accent_hover};
    padding: {focus_pad(SPACE_XS)}px {focus_pad(SPACE_M)}px;   /* same trade */
}}
QTableView:focus, QTableWidget:focus {{
    border: {FOCUS_RING_PX}px solid {C.accent_hover};
}}
/* the pyqtgraph canvases (charts, map, the stats spark/g-g, the library PB plot) are QGraphicsView
   subclasses and full tab stops, and they painted nothing at all when focused. They reserve the
   ring in {C.surface} rather than `transparent`: pyqtgraph paints its background INSIDE the
   viewport, so a transparent frame would expose the {C.canvas} window colour as a dark gutter.
   The reservation costs each canvas {FOCUS_RING_PX}px of viewport per side — paid once, in the
   resting state, so that focus never re-lays the plot out. */
QGraphicsView {{
    border: {FOCUS_RING_PX}px solid {C.surface};
}}
QGraphicsView:focus {{
    border: {FOCUS_RING_PX}px solid {C.accent_hover};
}}

/* scrub bar = primary seek target: a SPACE_S groove (the app's one bar weight, shared with the
   splitter grip, the scrollbar grip and the load bar) under a SPACE_L knob, for grabbability and
   lap-ruler tick room. The knob's numbers are all derived from those two: RADIUS_M is exactly half
   of SPACE_L so it is a circle, and the negative margin is half the difference so the circle sits
   centred on the groove. Shipped it was 18/9/-5 — the same three relations, off the scale. */
QSlider::groove:horizontal {{
    height: {SPACE_S}px;
    background: {C.border};
    border-radius: {RADIUS_S}px;
}}
QSlider::sub-page:horizontal {{
    background: {C.accent};
    border-radius: {RADIUS_S}px;
}}
QSlider::add-page:horizontal {{
    background: {C.border};
    border-radius: {RADIUS_S}px;
}}
QSlider::handle:horizontal {{
    background: {C.text};
    width: {SPACE_L}px;
    height: {SPACE_L}px;
    margin: -{(SPACE_L - SPACE_S) // 2}px 0;
    border-radius: {RADIUS_M}px;
}}
QSlider::handle:horizontal:hover {{
    background: {C.accent};
}}

/* ---------------------------------------------------------------- header view */
QHeaderView::section {{
    background-color: {C.surface};
    color: {C.text_dim};
    font-size: {TABLE_HEADER}px;
    font-weight: 600;
    /* the HORIZONTAL step is load-bearing and unchanged: every column's fitted width is measured
       through it (tests/test_lap_table_columns.py). Only the vertical padding moved onto the
       scale, which brings the header row down beside the rows it labels. */
    padding: {SPACE_XS}px {SPACE_S}px;
    border: none;
    border-bottom: {BORDER_PX}px solid {C.border};
}}
QHeaderView::section:hover {{
    color: {C.text};
}}

/* ---------------------------------------------------------------- tables */
QTableView, QTableWidget {{
    background-color: {C.surface};
    alternate-background-color: {C.surface_alt};
    gridline-color: {C.border};
    color: {C.text};
    selection-background-color: {C.sel_bg};
    selection-color: {C.text};
    /* the focus ring, reserved: transparent shows the table's own background through it, so the
       resting table looks exactly as it did while the :focus rule in the focus-ring section above
       has a frame to colour in. Was `border: none`, which left the ring nowhere to paint. */
    border: {FOCUS_RING_PX}px solid transparent;
    /* Qt's dotted current-item rect is unreadable on the dark palette, so it is replaced rather
       than merely suppressed: a hairline in the accent, drawn INSIDE the cell (negative offset)
       so it can't overlap the row above. It only paints while the view has focus, which is what
       makes it a current-CELL cue on top of the whole-table ring. */
    outline: {BORDER_PX}px solid {C.accent};
    outline-offset: -{BORDER_PX}px;
}}
QTableView::item, QTableWidget::item {{
    padding: {SPACE_XS}px {SPACE_S}px;
}}
QTableView::item:selected, QTableWidget::item:selected {{
    background-color: {C.sel_bg};
    color: {C.text};
}}
QTableCornerButton::section {{
    background-color: {C.surface};
    border: {BORDER_PX}px solid {C.border};
}}

/* ---------------------------------------------------------------- tooltip */
QToolTip {{
    background-color: {C.surface_hover};
    color: {C.text};
    border: {BORDER_PX}px solid {C.border};
    border-radius: {RADIUS_S}px;
    padding: {SPACE_XS}px {SPACE_S}px;
}}

/* ---------------------------------------------------------------- labels & roles */
QLabel {{
    background: transparent;
    color: {C.text};
}}
/* panel section header: small uppercase dimmed label flush above each panel */
QLabel.PanelHeader, QLabel[role="PanelHeader"] {{
    background-color: {C.surface};
    color: {C.text_dim};
    font-size: {PANEL_HEADER}px;
    font-weight: 600;
    padding: {SPACE_XS}px {SPACE_M}px;
    border-bottom: {BORDER_PX}px solid {C.border};
}}
/* a header-strip container holding widgets (map header / charts' consolidated bar): same
   surface bg + bottom hairline as a text PanelHeader, but it lays out child widgets itself. */
QWidget[role="PanelHeader"] {{
    background-color: {C.surface};
    border-bottom: {BORDER_PX}px solid {C.border};
}}
/* the lap panel's tab bar (Laps · Corners · Stats · Coaching) — lives INSIDE a PanelHeader
   bar, so no base/bg of its own; tabs use the header type scale with the selected tab lifted
   to full text + an accent underline (the app's one selected-state cue). Functional styling:
   Qt's default tab chrome is illegible on the dark palette. */
QTabBar {{
    background: transparent;
}}
/* A tab takes SPACE_S horizontally, not the SPACE_M a button takes, because the two paddings do
   different jobs: a button's separates its label from its own BORDER, while a borderless tab's is
   really the GAP to the next tab, and SPACE_S is the gap step. It also stands at CTRL_H like every
   other control in a header bar — the underline is the tab's only border, so it is all the chrome
   ctrl_content_h has to allow for. */
QTabBar::tab {{
    background: transparent;
    color: {C.text_dim};
    font-size: {PANEL_HEADER}px;
    font-weight: 600;
    padding: {SPACE_XS}px {SPACE_S}px;
    min-height: {ctrl_content_h(CTRL_H, SPACE_XS, border_v=SPACE_XXS)}px;
    border: none;
    border-bottom: {SPACE_XXS}px solid transparent;
    margin-right: {SPACE_XXS}px;
}}
QTabBar::tab:hover {{
    color: {C.text};
}}
QTabBar::tab:selected {{
    color: {C.text};
    border-bottom: {SPACE_XXS}px solid {C.accent};
}}
/* section label that sits INSIDE a widget header bar — the dimmed small header type, but no
   bg/border of its own (the parent bar already provides them). */
QLabel[role="BarLabel"] {{
    background: transparent;
    color: {C.text_dim};
    font-size: {PANEL_HEADER}px;
    font-weight: 600;
}}
/* hero Δ/speed readout — emphasized centre element of the charts' consolidated header bar
   (mono/tabular, hero size). No bg/border of its own: the bar provides them, so the readout
   sits inline between the section label and the x-mode toggle. Only the Δ-value COLOUR is
   driven per-tick (a merged `color:` rule); everything else is set once here. */
QLabel#DiffBox {{
    background: transparent;
    color: {C.text};
    font-family: {MONO_STACK};
    font-size: {HERO}px;
    font-weight: 600;
    padding: {SPACE_XXS}px {SPACE_S}px;
}}
/* trust strip over the map — one strip, two tiers. The ACTIONABLE line (place the start/finish
   line, #ProvisionalBanner) wears the amber call-to-action treatment: amber left-rule + tint so it
   reads as "do this to fix it". The INFORMATIONAL line (data-quality FYI, #InfoBanner) is a CALMER
   style — no amber, a quiet surface tint + a muted left-rule + dimmed text — so a pure "for your
   information" note never mis-signals a call to action. Single-sourced here; both clear once their
   concern resolves. The container (#TrustStrip) carries only the shared bottom hairline so the two
   lines read as one strip. */
QWidget#TrustStrip {{
    background: transparent;
    border-bottom: {BORDER_PX}px solid {C.border};
}}
/* SPACE_XXS is the app's ACCENT-RULE weight — it is what the selected tab underlines itself with
   — so the two trust lines and the chapter banner below now share it. Shipped, the same cue was
   drawn at three different weights (3 px, 3 px, 2 px) for no reason anyone could state. */
QLabel#ProvisionalBanner {{
    background-color: {C.accent_tint};
    color: {C.text};
    font-size: {CAPTION}px;
    font-weight: 600;
    padding: {SPACE_XS}px {SPACE_M}px;
    border-left: {SPACE_XXS}px solid {C.accent};
}}
QLabel#InfoBanner {{
    background-color: {C.surface};
    color: {C.text_dim};
    font-size: {CAPTION}px;
    font-weight: 500;
    padding: {SPACE_XS}px {SPACE_M}px;
    border-left: {SPACE_XXS}px solid {C.border_strong};
}}
/* "new personal best!" celebration toast — a transient, tasteful card overlaid on the window when a
   freshly-analysed session beats the track's prior PB (verified timing only). Amber accent so it
   reads as a positive moment, not an error; auto-dismisses. The link inside opens the PB progression. */
QWidget#PBToast {{
    background-color: {C.surface_active};
    border: {BORDER_PX}px solid {C.accent};
    border-radius: {RADIUS_M}px;
}}
QLabel#PBToastTitle {{
    background: transparent;
    color: {C.accent};
    font-size: {BODY}px;
    font-weight: 700;
}}
QLabel#PBToastBody {{
    background: transparent;
    color: {C.text};
    font-size: {CAPTION}px;
}}
/* the "see your progress" link + the dismiss ×: flat text buttons that don't look like the chunky
   panel QPushButtons — so they take HIT_MIN rather than CTRL_H, and they must take it HERE.
   A stylesheet min-height REPLACES the widget's own minimum (see the note on QPushButton above):
   these two call setMinimumSize(MIN_HIT_PX, MIN_HIT_PX) in overlays.py and were silently getting
   22 px from the base rule's CTRL_H derivation instead — under the pointer floor, on the one card
   in the app you have to hit before it deletes itself. tests/test_pb_toast.py caught it. */
QPushButton#PBToastLink, QPushButton#PBToastClose {{
    background: transparent;
    border: none;
    color: {C.accent};
    font-size: {CAPTION}px;
    font-weight: 600;
    padding: {SPACE_XXS}px {SPACE_S}px;
    min-height: {ctrl_content_h(HIT_MIN, SPACE_XXS, border_v=0)}px;
}}
QPushButton#PBToastLink:hover {{
    color: {C.accent_hover};
    text-decoration: underline;
}}
QPushButton#PBToastClose {{
    color: {C.text_dim};
}}
QPushButton#PBToastClose:hover {{
    color: {C.text};
}}
/* slim multi-chapter banner strip */
QLabel#ChapterBanner {{
    background-color: {C.surface};
    color: {C.text_dim};
    font-size: {CAPTION}px;
    font-weight: 500;
    padding: {SPACE_XS}px {SPACE_M}px;
    border-left: {SPACE_XXS}px solid {C.accent};
    border-bottom: {BORDER_PX}px solid {C.border};
}}
/* video time/speed/lap readout (caption, dimmed, tabular) */
QLabel#Readout {{
    background-color: {C.surface};
    color: {C.text_dim};
    font-family: {MONO_STACK};
    font-size: {CAPTION}px;
    padding: {SPACE_XS}px {SPACE_S}px;
}}
/* per-pane caption strip in compare mode: "lap N  m:ss.mmm" (tabular, dimmed, surface bg). */
QLabel#PaneCaption {{
    background-color: {C.surface};
    color: {C.text_dim};
    font-family: {MONO_STACK};
    font-size: {CAPTION}px;
    font-weight: 600;
    padding: {SPACE_XS}px {SPACE_S}px;
}}
/* per-pane "Δ vs other" badge in compare mode: tabular, transparent so it sits inline in the
   caption strip; only its Δ-value COLOUR is driven per-tick (a merged `color:` rule). */
QLabel#PaneBadge {{
    background: transparent;
    color: {C.text_dim};
    font-family: {MONO_STACK};
    font-size: {CAPTION}px;
    font-weight: 600;
    padding: {SPACE_XS}px {SPACE_S}px;
}}
/* in-panel empty state: shown when a recording has zero complete laps. Surface bg covers the panel.
   text_dim, NOT text_muted: this is the panel's ONLY content, so it is enabled prose and has to
   clear WCAG AA (5.90:1 here; text_muted was 3.17:1). text_muted is for disabled chrome only. */
QLabel[role="EmptyState"] {{
    background-color: {C.surface};
    color: {C.text_dim};
    font-size: {BODY}px;
    padding: {SPACE_XL}px;
}}
/* first-run welcome DROP ZONE: a dashed-border rounded rect framing the wordmark/invitation/
   buttons, so the drag-and-drop affordance is VISIBLE (a user reads "drop a file here"). Restrained
   — a dashed hairline over the canvas, not a heavy box; the buttons inside keep their own styling. */
QFrame#WelcomeDropZone {{
    background: transparent;
    border: {SPACE_XXS}px dashed {C.border_strong};
    border-radius: {RADIUS_L}px;
}}
/* first-run welcome empty state (no recording loaded): a large wordmark + a muted invitation. */
QLabel[role="WelcomeTitle"] {{
    background: transparent;
    color: {C.text};
    font-size: {HERO}px;
    font-weight: 700;
}}
QLabel[role="WelcomeSubtitle"] {{
    background: transparent;
    color: {C.text_dim};
    font-size: {BODY}px;
}}
/* the failed-load message. It used to be the marketing subtitle's EXACT colour one pixel smaller,
   i.e. the faintest, smallest text on a screen where (on the "Open demo" path) it is the only
   response the click produces. It now outranks the invitation instead of hiding under it: the
   attention amber the rest of the app warns in, at BODY size, semibold, paired with the ⚠ glyph
   WelcomeView prefixes — so the ranking survives greyscale and colour blindness, not just hue.
   Amber is deliberately NOT a palette-swapped semantic hue: this text is styled once at startup by
   the QSS and would freeze if it were (the SERIES_BEST failure mode). 9.35:1 on canvas. */
QLabel[role="WelcomeError"] {{
    background: transparent;
    color: {C.accent};
    font-size: {BODY}px;
    font-weight: 600;
}}
/* load busy state: a muted "Loading telemetry…" title over an INDETERMINATE bar (QProgressBar
   range 0,0) — a multi-second GoPro ingest reads as "working", not frozen. The bar self-animates,
   so there's no timer to leak; it dies when _build_ui swaps in the real UI. */
QLabel[role="LoadingTitle"] {{
    background: transparent;
    color: {C.text_dim};
    font-size: {BODY}px;
}}
QProgressBar#LoadingBar {{
    background-color: {C.surface};
    border: none;
    border-radius: {RADIUS_S}px;
    max-height: {SPACE_S}px;   /* the app's one bar weight: groove, splitter grip, scrollbar grip */
}}
QProgressBar#LoadingBar::chunk {{
    background-color: {C.accent};
    border-radius: {RADIUS_S}px;
}}
/* ESTIMATED data-quality badge (central_view QualityBadge): a small padded/rounded/tinted chip
   next to the LAPS label when the timing quality is degraded — so it reads as a chip, not plain
   text. Amber-tinted like the other trust affordances (the provisional banner / accent), sized
   small. Only shown (setVisible) when Session.timing_quality is degraded. */
QLabel#QualityBadge {{
    background-color: {C.accent_tint};
    color: {C.accent};
    font-size: {TABLE_HEADER}px;
    font-weight: 700;
    padding: {SPACE_XXS}px {SPACE_S}px;
    border: {BORDER_PX}px solid {C.accent};
    border-radius: {RADIUS_M}px;
}}
"""


# ====================================================================== apply
def apply_theme(app) -> None:
    """Apply the full theme to a QApplication: default font, dark palette, global QSS, and the
    pyqtgraph background/foreground (the latter MUST run before any plot widget is created so the
    charts adopt the dark surface). Does not otherwise style pyqtgraph internals — that's Phase 2.
    """
    # The pair, never one alone: ui_font() and the QSS both name "Inter", which is only a real
    # family once the bundled TTFs are in the font DB. Idempotent, so an app (or a test) that
    # already registered them pays nothing.
    register_fonts()
    # The app-wide DEFAULT font — deliberately here and not in the QSS, so that any widget's own
    # setFont still wins. See the base rule in _build_qss for what a blanket QSS font costs.
    app.setFont(ui_font(BODY, W_REGULAR))
    app.setPalette(_palette())
    app.setStyleSheet(_build_qss())

    # Charts adopt the dark surface bg + dimmed foreground (Phase 1). Set before any PlotWidget.
    try:
        import pyqtgraph as pg
        pg.setConfigOption("background", C.surface)
        pg.setConfigOption("foreground", C.text_dim)
    except Exception as exc:
        print(f"theme: pyqtgraph config skipped ({exc}).", flush=True)
