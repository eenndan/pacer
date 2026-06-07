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
"""

from __future__ import annotations

import os

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette


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
    caution = "#D9A441"         # ⚠ caution


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
/* checked state (the G overlay toggle) */
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
QComboBox::down-arrow {{
    width: 8px; height: 8px;
    /* simple chevron via a rotated border square (no image asset in Phase 1) */
    border-left: 1px solid {C.text_dim};
    border-bottom: 1px solid {C.text_dim};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {C.surface};
    color: {C.text};
    border: 1px solid {C.border};
    selection-background-color: {C.accent_tint};
    selection-color: {C.text};
    outline: none;
}}

/* ---------------------------------------------------------------- slider */
QSlider::groove:horizontal {{
    height: 4px;
    background: {C.border};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {C.accent};
    border-radius: 2px;
}}
QSlider::add-page:horizontal {{
    background: {C.border};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {C.text};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
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
/* hero Δ/speed readout card */
QLabel#DiffBox {{
    background-color: {C.surface};
    color: {C.text};
    font-family: {MONO_STACK};
    font-size: {HERO}px;
    font-weight: 600;
    padding: 10px 12px;
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
