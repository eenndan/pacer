"""Pacer Studio design system — the SINGLE source of truth for the dark "Refined Minimal" theme.

This module is deliberately pacer-free (no telemetry imports) and LLM-editable: the design
tokens live as plain hex strings on `class C`, the QSS is assembled from those tokens in clearly
labelled sections, and the font handling degrades gracefully when the bundled Inter TTFs or the
network are unavailable.

Public surface:
    C                 — the colour/scale tokens (use these; do NOT invent new colours).
    register_fonts()  — bundle/register Inter; call once, before apply_theme.
    apply_theme(app)  — set the app font + dark QPalette + global QSS, and the pyqtgraph
                        background/foreground (so charts adopt the dark surface in Phase 1).
    ui_font(size, weight)   — the UI sans face (Inter / system fallback).
    mono_font(size, weight) — a tabular-figures face for numerics (tnum on Inter when Qt ≥ 6.7,
                              else a mono numeric stack).
    delta_colour(d)   — the shared three-way Δ-readout colour (ahead/behind/neutral, with the
                        ±DELTA_EVEN_EPS_S dead band) used by the Δ box and the compare badges.
    LAP_SEEK_NUDGE_S  — the shared seek-into-a-lap nudge (an interaction constant, hosted here
                        because this is the one pacer-free module every control layer imports).
"""

from __future__ import annotations

import os

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPalette


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
    text_dim = "#9AA1AD"        # secondary
    text_muted = "#6B7280"      # disabled / tertiary

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


# --- chart series palette (Phase 2) ---------------------------------------------------------
# A refined categorical list for lap curves on the dark surface — each is a bright, opaque hex
# from the token philosophy (amber accent first, then a cyan/blue/purple/coral spread) so that
# 2–6 compared laps stay distinguishable without muddying. Solid + width-2 + AA-off keeps the
# fast segmented-line path (see plots_view), so these read crisp.
CHART_SERIES = [
    C.accent,    # amber  — primary / first lap (also the app accent)
    "#5BC8E0",   # cyan
    C.best,      # purple
    "#7FA8F5",   # soft blue
    "#E89B6B",   # coral / soft orange
    "#9FD66B",   # lime-leaning green (distinct from the best-lap C.ahead green)
]

# Semantic mapping used by the plots (documented so it stays consistent with the lap table):
#   BEST lap curve     -> C.ahead  (the same green the lap table marks the best lap with)
#   additional laps    -> CHART_SERIES in order (the amber accent is the first categorical entry)
SERIES_BEST = C.ahead              # green — matches the lap table's best-lap colour


# --- track-map rainbow colormap (F3) ---------------------------------------------------------
# The rainbow track map paints the current lap's line by a channel (speed / Δ-vs-best),
# quantized into MAP_RAINBOW_N buckets. The ramp is anchored on the theme's own SEMANTIC
# tokens — C.behind (red, "slow / losing") → C.accent (amber, mid) → C.ahead (green,
# "fast / gaining") — so the map's colour language matches the Δ readout/badges exactly and
# every colour already reads on the dark surface. The three anchors sit at comparable
# lightness, so the red→amber→green hue sweep stays perceptually ORDERED (no bucket pops
# brighter than its neighbours) without needing a heavyweight Lab-space colormap.
MAP_RAINBOW_N = 16  # quantization levels — enough for a smooth-looking gradient, few enough
                    # that one PlotCurveItem per bucket stays trivially cheap (≤16 items)


def _hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rainbow_colors(n: int = MAP_RAINBOW_N) -> list[str]:
    """`n` (≥ 2) hex colours, low→high, piecewise-linearly interpolated through the
    C.behind → C.accent → C.ahead semantic anchors (see the WHY block above). Index 0 is the
    'slow / losing' red end; index n-1 the 'fast / gaining' green end."""
    anchors = [_hex_rgb(C.behind), _hex_rgb(C.accent), _hex_rgb(C.ahead)]
    out = []
    for i in range(n):
        t = i / (n - 1) * (len(anchors) - 1)  # position along the anchor chain [0, 2]
        k = min(int(t), len(anchors) - 2)
        f = t - k
        a, b = anchors[k], anchors[k + 1]
        rgb = (round(a[c] + (b[c] - a[c]) * f) for c in range(3))
        out.append("#{:02X}{:02X}{:02X}".format(*rgb))
    return out


# --- shared Δ-readout semantics -------------------------------------------------------------
# A Δ readout is displayed to 0.01 s, so a |Δ| at/below half a displayed centisecond is "even" —
# neither ahead nor behind. Without the dead band an exact 0.00 coloured GREEN (the old `d <= 0`
# branch), which misread "dead even with best" as "ahead".
DELTA_EVEN_EPS_S = 0.005


def delta_colour(d: float | None) -> str | None:
    """The ONE three-way Δ colour rule, shared by the always-on Δ box (app._update_diff_box)
    and the compare panes' Δ badges (CompareController._set_pane_badge): C.ahead (green) when
    meaningfully ahead (d < -DELTA_EVEN_EPS_S), C.behind (red) when behind (d > +eps), and
    None — "no semantic colour, use the widget's neutral foreground" — for no delta at all or
    a dead-even |d| <= eps."""
    if d is None or abs(d) <= DELTA_EVEN_EPS_S:
        return None
    return C.ahead if d < 0 else C.behind


# --- shared Δ/speed readout TEXT (single source of truth) ------------------------------------
# The hero Δ/speed readout is shown in TWO substrates that must never drift: the live #DiffBox
# QLabel (app._update_diff_box) and the burned-in export readout (export_video._paint_readout,
# raw QPainter). These tiny Qt-free formatters are the ONE place the readout's number/units/
# no-lap rules live, so the live label and the shareable MP4 always say the same thing. They are
# composable FRAGMENTS — a delta value, a delta run, a speed run — plus `format_delta_speed`,
# which assembles them into the exact one-line live string. The export, which paints the speed
# number and its unit as SEPARATE runs (different fonts/colours; a layout this string-level helper
# can't express), composes from the same fragments rather than the combined string.


def format_delta_value(d: float | None) -> str:
    """The Δ NUMBER alone, no leading glyph/units: an em dash for no delta, else a signed
    2-decimal seconds value (e.g. "+0.00", "-0.31"). The atomic source both readouts format Δ
    from — the live box wraps it as "Δ <v> s", the export as "Δ <v>"."""
    return "—" if d is None else f"{d:+.2f}"


def format_delta_run(d: float | None, *, units: bool = True) -> str:
    """The Δ run as drawn: "Δ —" / "Δ +0.00 s" with `units` (the live #DiffBox form), or
    "Δ —" / "Δ +0.00" without (the export readout form, which omits the trailing " s" to keep its
    hero layout tight). Both share `format_delta_value`, so the number itself can't drift."""
    v = format_delta_value(d)
    if d is None:
        return f"Δ {v}"
    return f"Δ {v} s" if units else f"Δ {v}"


def format_speed_run(speed_kmh: float | None, lap: int | None) -> str:
    """The live speed run: "{n} km/h" while a lap is current, else the HONEST "— km/h" (the Phase-0
    no-lap rule — outside a valid lap we show no misleading lead-in speed). Used by the live
    #DiffBox; the export paints the number and unit as separate runs but applies the SAME
    speed-known gate via `speed_number`."""
    return f"{speed_kmh:.0f} km/h" if (speed_kmh is not None and lap is not None) else "— km/h"


def speed_number(speed_kmh: float | None, lap: int | None) -> str:
    """The speed NUMBER alone (no unit) under the SAME no-lap gate as `format_speed_run`: the
    rounded km/h while a lap is current, else an em dash. The export's hero readout draws this
    number and a small "km/h" unit separately, but shares this one gate so the two readouts agree on
    when a speed is honestly known."""
    return "—" if (speed_kmh is None or lap is None) else f"{speed_kmh:.0f}"


def format_delta_speed(d: float | None, speed_kmh: float | None,
                       lap: int | None) -> tuple[str, str | None]:
    """The COMBINED hero Δ/speed readout, single-sourced for the live #DiffBox and the export:
    returns (text, colour) where `text` is the exact live one-line string
    ("Δ +0.00 s     73 km/h" — five spaces between the runs, the honest "— km/h" with no lap) and
    `colour` is `delta_colour(d)` (None = "no semantic colour; use the widget's neutral
    foreground"). The live box renders this verbatim; the export reuses the colour + the fragment
    helpers (it paints the runs separately) so neither can drift from the other."""
    text = f"{format_delta_run(d)}     {format_speed_run(speed_kmh, lap)}"
    return text, delta_colour(d)


# --- shared interaction constants ------------------------------------------------------------
# Seek a few ms INTO a lap rather than onto its exact start: laps are contiguous (lap N's finish
# == lap N+1's start) and the player quantizes seeks to whole ms, so a seek to the exact boundary
# can land a few tenths of a ms BELOW it and resolve to the PREVIOUS lap. Far smaller than a
# frame; invisible in a ~70 s lap. Shared by the lap-table seek (app) and the compare panes'
# seek-to-lap-start (compare_controller) — hosted here, the neutral pacer-free module both
# already import, to avoid an app<->controller import cycle.
LAP_SEEK_NUDGE_S = 0.010


# --- type scale (px) ---
HERO = 22
PANEL_HEADER = 11
BODY = 13
TABLE = 13
TABLE_HEADER = 11
CAPTION = 12

# --- weights ---
W_REGULAR = QFont.Weight.Normal     # 400
W_MEDIUM = QFont.Weight.Medium      # 500
W_SEMIBOLD = QFont.Weight.DemiBold  # 600

# --- font stacks (used in QSS font-family declarations) ---
UI_STACK = '"Inter","-apple-system","SF Pro Text","Helvetica Neue","sans-serif"'
MONO_STACK = '"SF Mono","JetBrains Mono","Menlo","monospace"'

_FONTS_DIR = os.path.join(os.path.dirname(__file__), "assets", "fonts")
# rsms/inter OFL static TTFs we try to bundle (and download if missing).
_INTER_FILES = ("Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf")
_INTER_URL = ("https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip")

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


def _try_download_inter() -> bool:
    """Best-effort: download the Inter release zip and extract the three static TTFs into
    _FONTS_DIR. Returns True if the files are present afterwards. Network failures are swallowed
    (caller falls back to the system stack)."""
    import urllib.request
    import zipfile

    os.makedirs(_FONTS_DIR, exist_ok=True)
    tmp_zip = os.path.join(_FONTS_DIR, "_inter_download.zip")
    try:
        urllib.request.urlretrieve(_INTER_URL, tmp_zip)  # noqa: S310 (trusted GitHub release)
        with zipfile.ZipFile(tmp_zip) as zf:
            for name in zf.namelist():
                base = os.path.basename(name)
                if base in _INTER_FILES:
                    with zf.open(name) as src, open(os.path.join(_FONTS_DIR, base), "wb") as dst:
                        dst.write(src.read())
    except Exception as exc:  # network/zip/IO — degrade gracefully
        print(f"theme: Inter download failed ({exc}); using system font fallback.", flush=True)
        return False
    finally:
        if os.path.exists(tmp_zip):
            os.remove(tmp_zip)
    return all(os.path.exists(os.path.join(_FONTS_DIR, f)) for f in _INTER_FILES)


def register_fonts() -> None:
    """Register the bundled Inter TTFs with the Qt font database so the UI looks identical across
    machines. If the TTFs aren't bundled, try a one-time download; if that fails (offline), skip
    and rely on the system fallback stack. Logs which path was taken. Also records whether the
    installed Qt supports OpenType feature tags (for tabular figures)."""
    global _inter_available, _supports_feature
    _supports_feature = _qt_supports_feature()

    have_files = all(os.path.exists(os.path.join(_FONTS_DIR, f)) for f in _INTER_FILES)
    if not have_files:
        have_files = _try_download_inter()

    if not have_files:
        _inter_available = False
        print("theme: Inter not bundled and unavailable — using system font fallback "
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
    f.setFamilies(["Inter", "-apple-system", "SF Pro Text", "Helvetica Neue", "sans-serif"])
    f.setPixelSize(size)
    return f


def mono_font(size: int = TABLE, weight: QFont.Weight = W_REGULAR) -> QFont:
    """A tabular-figures face for numerics so digits column-align. On Qt ≥ 6.7 we keep the Inter/UI
    face and enable the 'tnum' OpenType feature; otherwise we fall back to a monospaced numeric
    stack (SF Mono / JetBrains Mono / Menlo)."""
    if _supports_feature:
        f = ui_font(size, weight)
        try:
            f.setFeature("tnum", 1)  # tabular figures (Qt ≥ 6.7 accepts a str tag)
        except Exception:
            pass
        return f
    f = QFont("SF Mono", size)
    f.setWeight(weight)
    f.setFamilies(["SF Mono", "JetBrains Mono", "Menlo", "monospace"])
    f.setPixelSize(size)
    return f


# ====================================================================== icons
def icon(name: str, color: str | None = None) -> QIcon:
    """A themed QIcon from an icon font (qtawesome bundles Phosphor under the `ph` prefix, e.g.
    "ph.play-fill"). The glyph is tinted to `color` (default C.text), and to C.accent for its
    active/on state, so e.g. a checkable toolbar button lights up amber.

    qtawesome is imported LAZILY here so a missing dependency degrades gracefully — we log a clear
    message and return an empty QIcon (the button still works, just without a glyph) rather than
    crashing the whole app at import time.
    """
    try:
        import qtawesome as qta
    except Exception as exc:  # missing dep / font load failure — degrade, don't crash
        print(f"theme: qtawesome unavailable ({exc}); icon '{name}' will be blank. "
              "Install it via `pixi install` (the qtawesome pypi dependency).", flush=True)
        return QIcon()
    return qta.icon(name, color=color or C.text, color_active=C.accent)


_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
# Cached path of the generated combobox-chevron PNG (set on first _caret_down_asset() success).
_caret_asset_path: str | None = None


def _caret_down_asset() -> str | None:
    """Render the Phosphor `ph.caret-down` glyph (tinted to C.text_dim) to a small PNG under
    studio/assets/ and return its path — the bundled image QComboBox::down-arrow references via
    `image: url(...)`. We use a real glyph instead of the old QSS border hack: Qt QSS has no
    `transform`, so a "rotated border square" actually renders as an L-shaped corner bracket on
    every combo. A bundled PNG renders identically across machines (no per-machine font metrics).

    Generated once per process and cached; returns None if qtawesome is missing or rendering
    fails, in which case _build_qss() simply omits the rule (Qt falls back to its native arrow —
    plain, but never the broken L)."""
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
    p.setColor(QPalette.ColorRole.PlaceholderText, muted)
    p.setColor(QPalette.ColorRole.Link, QColor(C.accent))

    # Disabled states read muted everywhere text/foreground is drawn.
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText,
                 QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, muted)
    return p


# ====================================================================== QSS
def _build_qss() -> str:
    """Assemble the global stylesheet from tokens. Organised in sections so it stays editable.

    GOTCHA: a QPushButton/QToolButton custom `background` only renders if `border` is ALSO set —
    every button rule below sets border explicitly.

    NOTE: QVideoWidget is intentionally NOT styled here. A global opaque background on its native
    video surface can blank the frame on macOS; we leave it to the palette.
    """
    # Real down-chevron asset for QComboBox::down-arrow (see _caret_down_asset). When it can't be
    # generated (no qtawesome) we omit the override so Qt draws its native arrow — never the old
    # broken L-bracket. QSS url() wants forward slashes even on Windows.
    caret = _caret_down_asset()
    # caret_arrow_rule is an f-string, so its literal CSS braces are DOUBLED ({{ }} → { }); the
    # resulting VALUE has single braces and is substituted into the outer QSS f-string via a plain
    # {caret_arrow_rule} placeholder (inserted verbatim, not re-parsed). When the asset can't be
    # generated we emit only a comment, so Qt keeps its native arrow — never the old broken L.
    if caret:
        caret_url = caret.replace(os.sep, "/")  # QSS url() wants forward slashes on every OS
        caret_arrow_rule = f"""QComboBox::down-arrow {{
    image: url({caret_url});
    width: 12px; height: 12px;
    margin-right: 6px;
}}
QComboBox::down-arrow:on {{  /* open: nudge so it reads as pressed, no flip */
    top: 1px;
}}"""
    else:
        caret_arrow_rule = "/* QComboBox::down-arrow: native arrow (asset unavailable) */"
    return f"""
/* ---------------------------------------------------------------- base */
QWidget {{
    background-color: {C.canvas};
    color: {C.text};
    font-family: {UI_STACK};
    font-size: {BODY}px;
}}
QMainWindow, QWidget#centralwidget {{
    background-color: {C.canvas};
}}

/* ---------------------------------------------------------------- menus */
QMenuBar {{
    background-color: {C.surface};
    color: {C.text};
    border-bottom: 1px solid {C.border};
}}
QMenuBar::item {{
    background: transparent;
    padding: 4px 10px;
}}
QMenuBar::item:selected {{
    background-color: {C.accent_tint};
    color: {C.text};
}}
QMenu {{
    background-color: {C.surface};
    color: {C.text};
    border: 1px solid {C.border};
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 16px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: {C.accent_tint};
    color: {C.text};
}}
QMenu::item:disabled {{
    color: {C.text_muted};
}}
QMenu::separator {{
    height: 1px;
    background: {C.border};
    margin: 4px 8px;
}}

/* ---------------------------------------------------------------- splitter */
QSplitter {{
    background-color: {C.canvas};
}}
QSplitter::handle {{
    background-color: {C.canvas};
}}
QSplitter::handle:horizontal {{
    width: 6px;
    /* a thin visible hairline centred in the 6px hit area */
    border-left: 1px solid {C.border};
}}
QSplitter::handle:vertical {{
    height: 6px;
    border-top: 1px solid {C.border};
}}
QSplitter::handle:hover {{
    border-color: {C.border_strong};
}}

/* ---------------------------------------------------------------- scrollbars */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {C.border_strong};
    min-height: 28px;
    border-radius: 5px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {C.border_strong};
    min-width: 28px;
    border-radius: 5px;
    margin: 2px;
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

/* ---------------------------------------------------------------- buttons */
QPushButton {{
    background-color: {C.surface};
    color: {C.text};
    border: 1px solid {C.border};
    border-radius: 6px;
    padding: 6px 12px;
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
QPushButton:focus {{
    border: 1px solid {C.accent};
}}
/* checked state — the icon transport toggles (g-meter overlay + the compare toggle): a subtle
   amber tint + accent border so an active toggle reads clearly without breaking the icon-button
   vocabulary (the glyph itself is also recoloured to the accent in code). */
QPushButton:checked {{
    background-color: {C.accent_tint};
    border: 1px solid {C.accent};
    color: {C.accent};
}}
/* PRIMARY variant via dynamic property: setProperty("variant","primary") */
QPushButton[variant="primary"] {{
    background-color: {C.accent};
    color: {C.on_accent};
    border: 1px solid {C.accent};
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
    border: 1px solid {C.border};
    border-radius: 6px;
    padding: 5px 10px;
}}
QComboBox:hover {{
    border-color: {C.border_strong};
}}
QComboBox:focus {{
    border: 1px solid {C.accent};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
{caret_arrow_rule}
QComboBox QAbstractItemView {{
    background-color: {C.surface};
    color: {C.text};
    border: 1px solid {C.border};
    selection-background-color: {C.accent_tint};
    selection-color: {C.text};
    outline: none;
}}

/* ---------------------------------------------------------------- slider
   The transport scrub bar is the primary seek target, so the groove is a substantial 8px (was a
   thin 4px hairline that read accidental and was fiddly to grab) and the handle a 18px grippable
   dot. The taller groove also gives the lap-ruler tick marks (drawn in _LapRulerSlider) room to
   read. radius = height/2 keeps both groove and handle fully rounded. */
QSlider::groove:horizontal {{
    height: 8px;
    background: {C.border};
    border-radius: 4px;
}}
QSlider::sub-page:horizontal {{
    background: {C.accent};
    border-radius: 4px;
}}
QSlider::add-page:horizontal {{
    background: {C.border};
    border-radius: 4px;
}}
QSlider::handle:horizontal {{
    background: {C.text};
    width: 18px;
    height: 18px;
    margin: -5px 0;
    border-radius: 9px;
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
    padding: 6px 8px;
    border: none;
    border-bottom: 1px solid {C.border};
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
    border: none;
    outline: none;
}}
QTableView::item, QTableWidget::item {{
    padding: 4px 8px;
}}
QTableView::item:selected, QTableWidget::item:selected {{
    background-color: {C.sel_bg};
    color: {C.text};
}}
QTableCornerButton::section {{
    background-color: {C.surface};
    border: 1px solid {C.border};
}}

/* ---------------------------------------------------------------- tooltip */
QToolTip {{
    background-color: {C.surface_hover};
    color: {C.text};
    border: 1px solid {C.border};
    border-radius: 4px;
    padding: 4px 8px;
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
    padding: 6px 12px;
    border-bottom: 1px solid {C.border};
}}
/* a header-strip container holding widgets (map header / charts' consolidated bar): same
   surface bg + bottom hairline as a text PanelHeader, but it lays out child widgets itself. */
QWidget[role="PanelHeader"] {{
    background-color: {C.surface};
    border-bottom: 1px solid {C.border};
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
    padding: 2px 8px;
}}
/* slim multi-chapter banner strip */
QLabel#ChapterBanner {{
    background-color: {C.surface};
    color: {C.text_dim};
    font-size: {CAPTION}px;
    font-weight: 500;
    padding: 4px 12px;
    border-left: 2px solid {C.accent};
    border-bottom: 1px solid {C.border};
}}
/* video time/speed/lap readout (caption, dimmed, tabular) */
QLabel#Readout {{
    background-color: {C.surface};
    color: {C.text_dim};
    font-family: {MONO_STACK};
    font-size: {CAPTION}px;
    padding: 4px 8px;
}}
/* per-pane caption strip in compare mode: "lap N  m:ss.mmm" (tabular, dimmed, surface bg). */
QLabel#PaneCaption {{
    background-color: {C.surface};
    color: {C.text_dim};
    font-family: {MONO_STACK};
    font-size: {CAPTION}px;
    font-weight: 600;
    padding: 3px 8px;
}}
/* per-pane "Δ vs other" badge in compare mode: tabular, transparent so it sits inline in the
   caption strip; only its Δ-value COLOUR is driven per-tick (a merged `color:` rule). */
QLabel#PaneBadge {{
    background: transparent;
    color: {C.text_dim};
    font-family: {MONO_STACK};
    font-size: {CAPTION}px;
    font-weight: 600;
    padding: 3px 8px;
}}
/* centred dimmed in-panel EMPTY STATE (E1): shown over the lap table / charts when a recording
   loaded but has zero complete laps, so a blank panel reads as an explained state, not a broken
   app. Surface bg so it fully covers the panel content it overlays; generous padding centres the
   wrapped message. Themed via the existing muted-text + surface tokens. */
QLabel[role="EmptyState"] {{
    background-color: {C.surface};
    color: {C.text_muted};
    font-size: {BODY}px;
    padding: 24px;
}}
"""


# ====================================================================== apply
def apply_theme(app) -> None:
    """Apply the full theme to a QApplication: default font, dark palette, global QSS, and the
    pyqtgraph background/foreground (the latter MUST run before any plot widget is created so the
    charts adopt the dark surface). Does not otherwise style pyqtgraph internals — that's Phase 2.
    """
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
