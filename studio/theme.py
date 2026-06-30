"""Pacer Studio design system — single source of truth for the dark theme.

Pacer-free (no telemetry imports); font handling degrades gracefully to the system
font stack when the bundled Inter TTFs are unavailable. Public surface: C (colour/scale
tokens), register_fonts, apply_theme, ui_font, mono_font, delta_colour, LAP_SEEK_NUDGE_S.
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


# Categorical lap-curve palette (amber accent first); best lap uses SERIES_BEST green to match
# the lap table.
CHART_SERIES = [
    C.accent,    # amber  — primary / first lap (also the app accent)
    "#5BC8E0",   # cyan
    C.best,      # purple
    "#7FA8F5",   # soft blue
    "#E89B6B",   # coral / soft orange
    "#9FD66B",   # lime-leaning green (distinct from the best-lap C.ahead green)
]

SERIES_BEST = C.ahead


# Track-map current lap coloured by a channel (speed / Δ-vs-best), quantized into MAP_RAINBOW_N
# buckets through the C.behind → C.accent → C.ahead ramp so it matches the Δ readout.
MAP_RAINBOW_N = 16  # rainbow buckets (one PlotCurveItem each); smooth enough, cheap enough


def _hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rainbow_colors(n: int = MAP_RAINBOW_N) -> list[str]:
    """`n` hex colours low→high along the C.behind → C.accent → C.ahead ramp (index 0 = red/slow,
    n-1 = green/fast)."""
    anchors = [_hex_rgb(C.behind), _hex_rgb(C.accent), _hex_rgb(C.ahead)]
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
    """Three-way Δ colour: C.ahead if ahead, C.behind if behind, None (neutral) for no/even delta."""
    if d is None or abs(d) <= DELTA_EVEN_EPS_S:
        return None
    return C.ahead if d < 0 else C.behind


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
ESTIMATED_SUFFIX = " (est)"   # appended to a column/label title carrying an estimated value


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
    """Brake-event peak decel (g) -> marker glyph size (px), clamped to the ramp ends."""
    frac = (float(peak_decel) - BRAKE_DECEL_LO) / max(BRAKE_DECEL_HI - BRAKE_DECEL_LO, 1e-6)
    return BRAKE_MARKER_MIN_PX + min(max(frac, 0.0), 1.0) * (BRAKE_MARKER_MAX_PX - BRAKE_MARKER_MIN_PX)


def format_delta_value(d: float | None) -> str:
    """Δ number alone, no glyph/units: em dash for None, else signed 2dp (e.g. -0.31)."""
    return "—" if d is None else f"{d:+.2f}"


def format_delta_run(d: float | None, *, units: bool = True) -> str:
    """Δ <v> with optional trailing ' s' (units=True live box, False export)."""
    v = format_delta_value(d)
    if d is None:
        return f"Δ {v}"
    return f"Δ {v} s" if units else f"Δ {v}"


def format_speed_run(speed_kmh: float | None, lap: int | None) -> str:
    """<n> km/h while a lap is current, else '— km/h' (no misleading speed outside a lap)."""
    return f"{speed_kmh:.0f} km/h" if (speed_kmh is not None and lap is not None) else "— km/h"


def speed_number(speed_kmh: float | None, lap: int | None) -> str:
    """Speed number alone (no unit), same no-lap gate as format_speed_run: rounded km/h or em dash."""
    return "—" if (speed_kmh is None or lap is None) else f"{speed_kmh:.0f}"


def format_delta_speed(d: float | None, speed_kmh: float | None,
                       lap: int | None) -> tuple[str, str | None]:
    """Combined live readout: (text, colour). text = 'Δ <v> s<5 spaces><n> km/h'; colour = delta_colour(d)."""
    text = f"{format_delta_run(d)}     {format_speed_run(speed_kmh, lap)}"
    return text, delta_colour(d)


# The hero readout LEADS with Δ-to-IDEAL — the product's moat number ("how far off your own
# achievable lap are you, right here") — rather than Δ-to-best. Labelled "Δideal" so it can never be
# read as the plain best-lap Δ; the IDEAL gap is always ≥ 0 (you can't beat the envelope you helped
# form), so it carries the single "behind"/amber colour rather than the two-way ahead/behind ramp.
def format_ideal_readout(d_ideal: float | None, speed_kmh: float | None,
                         lap: int | None) -> tuple[str, str | None]:
    """Hero #DiffBox readout (text, colour): 'Δideal <v> s<5 spaces><n> km/h', leading with the
    Δ-to-ideal scalar. `d_ideal` is `Session.delta_to_ideal_at` (≥ 0 by construction, None outside a
    lap / before an ideal exists). Colour = `C.behind` when there's real time on the table, else
    neutral — there is no "ahead of ideal", so this never goes green."""
    v = format_delta_value(d_ideal)
    delta_run = f"Δideal {v}" + (" s" if d_ideal is not None else "")
    text = f"{delta_run}     {format_speed_run(speed_kmh, lap)}"
    colour = C.behind if (d_ideal is not None and d_ideal > DELTA_EVEN_EPS_S) else None
    return text, colour


# Seek a few ms INTO a lap: an exact-boundary seek rounds down to the previous lap. Shared by the
# lap-table and compare seeks.
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
W_SEMIBOLD = QFont.Weight.DemiBold  # 600

# --- font stacks (used in QSS font-family declarations) ---
UI_STACK = '"Inter","-apple-system","SF Pro Text","Helvetica Neue","sans-serif"'
MONO_STACK = '"SF Mono","JetBrains Mono","Menlo","monospace"'

_FONTS_DIR = os.path.join(os.path.dirname(__file__), "assets", "fonts")
# rsms/inter OFL static TTFs bundled under assets/fonts/.
_INTER_FILES = ("Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf")

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
    Also records Qt tnum support. Call once before apply_theme."""
    global _inter_available, _supports_feature
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
    f.setFamilies(["Inter", "-apple-system", "SF Pro Text", "Helvetica Neue", "sans-serif"])
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
    f.setFamilies(["SF Mono", "JetBrains Mono", "Menlo", "monospace"])
    f.setPixelSize(size)
    return f


# ====================================================================== icons
def icon(name: str, color: str | None = None) -> QIcon:
    """Themed QIcon from qtawesome's Phosphor set (e.g. 'ph.play-fill'), tinted to color
    (default C.text) and C.accent when active. Lazy import: returns a blank QIcon if qtawesome
    is missing."""
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
    p.setColor(QPalette.ColorRole.PlaceholderText, muted)
    p.setColor(QPalette.ColorRole.Link, QColor(C.accent))

    # Disabled states read muted everywhere text/foreground is drawn.
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText,
                 QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, muted)
    return p


# ====================================================================== QSS
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
/* checked toggle: amber tint + accent border (glyph also recoloured in code) */
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

/* scrub bar = primary seek target: 8px groove + 18px handle for grabbability and lap-ruler tick room. */
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
/* provisional-timing trust banner: a persistent warning strip over the map while the lap timing
   references an auto-fitted (unconfirmed) start line. Amber left-rule + tint so it reads as a
   call-to-action, not an error; clears once the timing is Verified. */
QLabel#ProvisionalBanner {{
    background-color: {C.accent_tint};
    color: {C.text};
    font-size: {CAPTION}px;
    font-weight: 600;
    padding: 5px 12px;
    border-left: 3px solid {C.accent};
    border-bottom: 1px solid {C.border};
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
/* in-panel empty state: shown when a recording has zero complete laps. Surface bg covers the panel; muted text. */
QLabel[role="EmptyState"] {{
    background-color: {C.surface};
    color: {C.text_muted};
    font-size: {BODY}px;
    padding: 24px;
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
    color: {C.text_muted};
    font-size: {BODY}px;
}}
QLabel[role="WelcomeError"] {{
    background: transparent;
    color: {C.text_muted};
    font-size: {CAPTION}px;
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
