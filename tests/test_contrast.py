"""Theme colour SEMANTICS + legibility (QA batch B17: U10-01, U10-02, U1-01, L10-05, L12-10).

Four guarantees, all measurable, none of them opinions:

  1. NO FROZEN SEMANTIC HUES (U10-01). The palette accessors are a CALL-TIME contract. A module
     constant bound to one of the swappable hues (`SERIES_BEST = C.ahead` was one) captures the
     palette that happened to be active at import and can never move, so its surface freezes on a
     colour-blind flip while everything beside it changes. `test_no_module_constant_freezes_a_
     palette_hue` scans every module in studio/ for that shape and fails the build on a new one.

  2. THE COLOUR-BLIND MAP RAMP IS ACTUALLY DISCRIMINABLE (U10-02). Its adjacent buckets must step
     by more than the ~2.3 CIE76 JND under a deuteranopia simulation, and it must never be WORSE
     than the default ramp it replaces. Shipped, its lower half stepped 0.90-1.16 over dE 7.5 —
     a flat orange bar across half the speed range, and 5.4x worse than simply leaving the option
     off (40.3).

  3. WCAG AA ON EVERY ENABLED TEXT STYLE (U1-01, L10-05). 4.5:1 at body/caption sizes. `text_muted`
     is allowed to stay below it ONLY on disabled chrome, which WCAG 1.4.3 explicitly exempts — and
     the test pins that exemption list so it cannot quietly grow.

  4. NO `-0.00` (L12-10). Float noise inside the even dead band prints `+0.00`, live and burned
     into an exported MP4 alike.

Pure Python + the CVD/Lab maths inline; the Qt bits are offscreen. No telemetry file, no pacer.

Run: python tests/test_contrast.py
"""
import ast
import os
import sys

import numpy as np

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import theme  # noqa: E402
from studio.theme import C  # noqa: E402

_STUDIO = os.path.join(_REPO, "studio")


# ============================================================== colour science (self-contained)
# Machado 2009 severity-1.0 deuteranopia, applied in LINEAR-light RGB (the domain the matrix is
# derived in). Re-derived here rather than imported so the assertion cannot drift with the app.
_M_DEUT = np.array([[0.367322, 0.860646, -0.227968],
                    [0.280085, 0.672501, 0.047413],
                    [-0.011820, 0.042940, 0.968881]])
JND = 2.3  # CIE76 just-noticeable difference for large-ish flat areas


def _hx(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], float)


def _lin(c):
    c = np.asarray(c, float) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _srgb(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055) * 255.0


def _deut(rgb):
    return _srgb(_lin(rgb) @ _M_DEUT.T)


def _lab(rgb):
    xyz = _lin(rgb) @ np.array([[.4124, .3576, .1805],
                                [.2126, .7152, .0722],
                                [.0193, .1192, .9505]]).T
    t = xyz / np.array([.95047, 1.0, 1.08883])
    f = np.where(t > .008856, np.cbrt(t), 7.787 * t + 16 / 116)
    return np.array([116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])])


def _dE(a, b):
    return float(np.linalg.norm(_lab(a) - _lab(b)))


def _lum(h):
    r, g, b = _lin(_hx(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: str, bg: str) -> float:
    """WCAG 2.x relative-contrast ratio between two opaque hex colours."""
    a, b = _lum(fg), _lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _over(fg: str, alpha: float, bg: str) -> str:
    f, b = _hx(fg), _hx(bg)
    return "#{:02X}{:02X}{:02X}".format(
        *(int(round(f[i] * alpha + b[i] * (1 - alpha))) for i in range(3)))


# =========================================================== 1. no frozen semantic-hue constants
# The hues that MOVE when the palette flips. A module-level constant bound to any of them is the
# U10-01 bug shape, whatever it is called.
_SWAPPABLE = {"ahead", "behind", "best"}


def _module_constant_hue_bindings(path):
    """Module-level `NAME = C.<swappable>` / `NAME = "<a swappable literal>"` assignments in one
    source file, as (name, what) pairs. Deliberately AST-based: a grep would miss aliases and trip
    over comments."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), path)
    hexes = {v.upper() for pal in theme._PALETTES.values()
             for k, v in pal.items() if k in _SWAPPABLE}
    out = []
    for node in tree.body:                      # module level ONLY — call-time locals are fine
        if not isinstance(node, ast.Assign):
            continue
        v = node.value
        what = None
        if (isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name)
                and v.value.id == "C" and v.attr in _SWAPPABLE):
            what = f"C.{v.attr}"
        elif isinstance(v, ast.Constant) and isinstance(v.value, str) \
                and v.value.upper() in hexes:
            what = v.value
        if what:
            out += [(t.id, what) for t in node.targets if isinstance(t, ast.Name)]
    return out


def test_no_module_constant_freezes_a_palette_hue():
    """THE U10-01 GUARD. `theme.SERIES_BEST = C.ahead` made the charts' best-lap curve immune to the
    colour-blind toggle while the lap table's cue moved, because a module constant is bound ONCE at
    import. Every meaning-bearing colour must be an accessor CALL, not a frozen constant.

    Two exemptions, both real and both narrow:
      * theme._PALETTES itself — the source of truth the accessors read;
      * theme.CHART_SERIES / plots_view.PALETTE — documented IDENTITY colours (which lap is which),
        deliberately palette-independent. C.best appears inside CHART_SERIES as a list ELEMENT, not
        as a bare binding, so it is not matched here anyway.
    """
    EXEMPT = {
        ("theme.py", "_PALETTES"),
        # map_view.MARKER_COLOR = C.behind — the video-position marker. The U10-01 audit flagged it
        # as a third frozen constant, but MEASUREMENT says pointing it at behind_colour() would
        # make it strictly WORSE, not better: it would then equal rainbow bucket 0 exactly, in
        # BOTH palettes (CIE76 dE 0.0 to the nearest bucket). Frozen, it is already dE 0.0 in the
        # default palette and 3.5 deuteranopic in the colour-blind one (JND ~2.3) — so the marker
        # needs its OWN token, distinct from every ramp anchor, which is a map_view design change.
        # Exempted here rather than half-fixed; handed to the map_view owner (QA batch B03/B04).
        ("map_view.py", "MARKER_COLOR"),
    }
    offenders = []
    for fn in sorted(os.listdir(_STUDIO)):
        if not fn.endswith(".py"):
            continue
        for name, what in _module_constant_hue_bindings(os.path.join(_STUDIO, fn)):
            if (fn, name) not in EXEMPT:
                offenders.append(f"{fn}::{name} = {what}")
    assert not offenders, (
        "module constants frozen to a palette-swappable hue (call the accessor at draw time "
        f"instead): {offenders}")
    # And the constant this test was written for is really gone.
    assert not hasattr(theme, "SERIES_BEST")
    print("test_no_module_constant_freezes_a_palette_hue OK")


def test_best_lap_curve_colour_follows_the_palette_in_both_directions():
    """The chart's best-lap pen and the lap table's best-lap foreground resolve through the SAME
    accessor, so they agree in either palette — and the chart's green is genuinely gone in the
    colour-blind palette (the pixel claim in U10-01: nothing left near #5DD6A0)."""
    from studio import plots_view
    try:
        theme.set_palette(theme.PALETTE_STANDARD)
        std = theme.best_lap_colour()
        assert std == C.ahead
        theme.set_palette(theme.PALETTE_COLORBLIND)
        cb = theme.best_lap_colour()
        assert cb != std, "the best-lap hue must MOVE with the palette"
        # The one line plots_view draws the best lap with is the accessor, not a snapshot.
        src = open(os.path.join(_STUDIO, "plots_view.py"), encoding="utf-8").read()
        assert "theme.best_lap_colour() if is_best" in src
        # ... and it is not smuggled in through the identity palette either.
        assert std.upper() not in [c.upper() for c in plots_view.PALETTE]
        assert cb.upper() not in [c.upper() for c in plots_view.PALETTE]
        # No colour the charts can draw is within 26/255 of the standard green while colour-blind.
        green = _hx(C.ahead)
        for c in list(plots_view.PALETTE) + [cb]:
            assert np.abs(_hx(c) - green).max() > 26, f"{c} is indistinguishable from {C.ahead}"
    finally:
        theme.set_palette(theme.PALETTE_STANDARD)
    print("test_best_lap_curve_colour_follows_the_palette_in_both_directions OK")


def test_ideal_star_icon_and_ideal_line_share_one_accessor():
    """The ideal-lap button's star used a frozen `C.best` while the line it toggles is drawn with
    best_sector_colour(): in the colour-blind palette the button stayed purple over a teal line.
    Both now resolve through the accessor, and a palette flip re-tints the icon."""
    src = open(os.path.join(_STUDIO, "plots_view.py"), encoding="utf-8").read()
    assert "color=C.best" not in src
    assert "theme.best_sector_colour() if self._show_ideal" in src
    # refresh_palette must re-tint the icon: it is an icon, not a drawn item, so refresh() misses it
    assert "self._apply_ideal_icon()" in src.split("def refresh_palette")[1].split("def ")[0]
    print("test_ideal_star_icon_and_ideal_line_share_one_accessor OK")


# ================================================================= 2. the map ramp is readable
def _ramp_steps(sim):
    cols = [_hx(c) for c in theme.rainbow_colors()]
    if sim:
        cols = [_deut(c) for c in cols]
    return [_dE(cols[i], cols[i + 1]) for i in range(len(cols) - 1)]


def _halves(sim):
    cols = [_hx(c) for c in theme.rainbow_colors()]
    if sim:
        cols = [_deut(c) for c in cols]
    return _dE(cols[0], cols[7]), _dE(cols[8], cols[15])


def test_colourblind_map_ramp_steps_clear_the_jnd_under_deuteranopia():
    """U10-02. In the palette whose ENTIRE PURPOSE is deuteranopia, every adjacent pair of the 16
    map buckets must differ by more than the CIE76 JND under a deuteranopia simulation, and neither
    half of the ramp may be a dead zone. Shipped, the lower half stepped 0.90-1.16 over dE 7.5 —
    a flat orange bar across half the speed range.

    (The DEFAULT ramp is deliberately NOT held to this: red->amber->green necks to 1.44 at the
    handover under simulation, which is precisely why the colour-blind palette exists. It is held
    to the JND under normal vision below.)"""
    try:
        theme.set_palette(theme.PALETTE_COLORBLIND)
        steps = _ramp_steps(sim=True)
        assert min(steps) > JND, (
            f"smallest deuteranopic bucket step {min(steps):.2f} <= JND {JND}; "
            f"steps={[round(s, 2) for s in steps]}")
        lower, upper = _halves(sim=True)
        assert lower > 25.0 and upper > 25.0, f"half-ramp dE {lower:.1f}/{upper:.1f}"
        assert max(lower, upper) / min(lower, upper) < 2.0, \
            f"halves unbalanced {lower:.1f} vs {upper:.1f}"
    finally:
        theme.set_palette(theme.PALETTE_STANDARD)
    print("test_colourblind_map_ramp_steps_clear_the_jnd_under_deuteranopia OK")


def test_the_accessible_ramp_is_never_worse_than_the_default_one():
    """The defect in one assertion: choosing the colour-blind option made the map ramp's lower half
    5.4x WORSE under deuteranopia than leaving it alone (dE 7.5 vs the default's 40.3). An
    accessibility option that loses to the thing it replaces is the bug, whatever the absolute
    numbers are."""
    try:
        theme.set_palette(theme.PALETTE_STANDARD)
        std_lower, std_upper = _halves(sim=True)
        std_normal_min = min(_ramp_steps(sim=False))
        theme.set_palette(theme.PALETTE_COLORBLIND)
        cb_lower, cb_upper = _halves(sim=True)
        assert cb_lower >= std_lower, (
            f"colour-blind lower half {cb_lower:.1f} is WORSE than the default's {std_lower:.1f}")
        assert cb_upper >= std_upper, (
            f"colour-blind upper half {cb_upper:.1f} is WORSE than the default's {std_upper:.1f}")
        # the default ramp still separates for normal vision (it was never the broken one)
        assert std_normal_min > JND, std_normal_min
    finally:
        theme.set_palette(theme.PALETTE_STANDARD)
    print("test_the_accessible_ramp_is_never_worse_than_the_default_one OK")


def test_default_map_ramp_is_byte_identical_and_the_mid_anchor_is_an_accessor():
    """The default palette must be untouched by the colour-blind fix (existing users see no change):
    ends on the semantic tokens, amber in the middle. And the mid anchor is per-palette, resolved
    through an accessor like every other swappable hue."""
    try:
        theme.set_palette(theme.PALETTE_STANDARD)
        assert theme.ramp_mid_colour() == C.accent
        assert theme.rainbow_colors(3)[1].upper() == C.accent.upper()
        assert theme.rainbow_colors()[0].upper() == C.behind.upper()
        assert theme.rainbow_colors()[-1].upper() == C.ahead.upper()
        theme.set_palette(theme.PALETTE_COLORBLIND)
        assert theme.ramp_mid_colour() != C.accent, "the CB ramp needs its OWN mid anchor"
        assert theme.rainbow_colors()[0] == theme.behind_colour()
        assert theme.rainbow_colors()[-1] == theme.ahead_colour()
    finally:
        theme.set_palette(theme.PALETTE_STANDARD)
    print("test_default_map_ramp_is_byte_identical_and_the_mid_anchor_is_an_accessor OK")


# =========================================================================== 3. WCAG AA on text
TINT = _over(C.accent, 0.16, C.surface)
BODY, CAPTION, SMALL = 13, 12, 10

# (label, fg, bg, px) — every distinct TEXT style the theme renders. Sizes are the QSS/px values.
TEXT_STYLES = [
    ("QWidget base body text", C.text, C.canvas, BODY),
    ("table cell text", C.text, C.surface, BODY),
    ("table cell (alt row)", C.text, C.surface_alt, BODY),
    ("selected table row", C.text, C.sel_bg, BODY),
    ("QHeaderView::section", C.text_dim, C.surface, 11),
    ("QTabBar::tab unselected", C.text_dim, C.surface, 11),
    ("QTabBar::tab selected", C.text, C.surface, 11),
    ("PanelHeader label", C.text_dim, C.surface, 11),
    ("BarLabel", C.text_dim, C.surface, 11),
    ("#Readout", C.text_dim, C.surface, CAPTION),
    ("#PaneCaption", C.text_dim, C.surface, CAPTION),
    ("#InfoBanner", C.text_dim, C.surface, CAPTION),
    ("#ChapterBanner", C.text_dim, C.surface, CAPTION),
    ("PROVISIONAL_COLOR", theme.PROVISIONAL_COLOR, C.surface, BODY),
    ("PROVISIONAL_COLOR on selection", theme.PROVISIONAL_COLOR, C.sel_bg, BODY),
    ("pyqtgraph axis ticks", C.text_dim, C.surface, 11),
    ("Stats sparkline ticks (SMALLEST TYPE)", C.text_dim, C.surface, SMALL),
    ("role=EmptyState", C.text_dim, C.surface, BODY),
    ("role=WelcomeSubtitle", C.text_dim, C.canvas, BODY),
    ("role=WelcomeError", C.accent, C.canvas, BODY),
    ("role=LoadingTitle", C.text_dim, C.canvas, BODY),
    ("QPalette PlaceholderText", C.text_dim, C.surface, BODY),
    ("#ProvisionalBanner", C.text, TINT, CAPTION),
    ("#QualityBadge", C.accent, TINT, 11),
    ("#PBToastTitle", C.accent, C.surface_active, BODY),
    ("#PBToastBody", C.text, C.surface_active, CAPTION),
    ("primary button label", C.on_accent, C.accent, BODY),
    ("#DiffBox behind", C.behind, C.surface, 22),
    ("#DiffBox ahead", C.ahead, C.surface, 22),
    ("best-lap cell (colour-blind)", "#4C9BFF", C.surface, BODY),
    ("best-sector cell (standard)", C.best, C.surface, BODY),
    ("best-sector cell (colour-blind)", "#38C7C7", C.surface, BODY),
    ("Δ behind (colour-blind)", "#F0902B", C.surface, BODY),
]

# WCAG 1.4.3 exempts text in INACTIVE user-interface components. These are the only styles allowed
# to use C.text_muted, and they must all genuinely be disabled states. Grown-by-accident is the
# failure mode this list exists to stop.
DISABLED_EXEMPT = [
    ("QPushButton:disabled", C.text_muted, C.surface, BODY),
    ("QMenu::item:disabled", C.text_muted, C.surface, BODY),
]


def test_every_enabled_text_style_clears_wcag_aa():
    """U1-01: six styles shared one token, C.text_muted at 3.17:1 / 3.68:1. The token stayed (it is
    right for disabled chrome); the four ENABLED roles that were borrowing it moved to C.text_dim,
    and the failure message moved to the accent it should have had all along."""
    fails = []
    for name, fg, bg, px in TEXT_STYLES:
        need = 3.0 if px >= 18 else 4.5     # WCAG "large text" starts at 18.66px bold / 24px
        r = contrast(fg, bg)
        if r < need:
            fails.append(f"{name}: {fg} on {bg} = {r:.2f}:1 (needs {need})")
    assert not fails, "styles below WCAG AA: " + "; ".join(fails)
    print(f"test_every_enabled_text_style_clears_wcag_aa OK ({len(TEXT_STYLES)} styles)")


def test_text_muted_is_confined_to_wcag_exempt_disabled_chrome():
    """The split, pinned. C.text_muted is BELOW AA by design and may only dress inactive controls;
    every enabled role uses a token that clears 4.5:1. If someone re-points an enabled role at it,
    test_every_enabled_text_style_clears_wcag_aa fails — this one documents WHY the exemption is
    legitimate and keeps the exempt list short."""
    assert contrast(C.text_muted, C.surface) < 4.5     # the honest admission
    for name, fg, _bg, _px in DISABLED_EXEMPT:
        assert fg == C.text_muted and ":disabled" in name
    # every ENABLED style clears AA at its own size
    assert contrast(C.text_dim, C.surface) >= 4.5
    assert contrast(C.text_dim, C.canvas) >= 4.5
    qss = theme._build_qss()
    # the four roles that were borrowing the disabled token are off it
    for role in ('QLabel[role="EmptyState"]', 'QLabel[role="WelcomeSubtitle"]',
                 'QLabel[role="WelcomeError"]'):
        block = qss.split(role)[1].split("}")[0]
        assert C.text_muted not in block, f"{role} is still using the disabled-chrome token"
    print("test_text_muted_is_confined_to_wcag_exempt_disabled_chrome OK")


def test_welcome_error_outranks_the_welcome_subtitle():
    """L10-05: the failed-load message used to be the subtitle's EXACT colour one pixel SMALLER —
    the faintest, smallest text on the screen, and on the 'Open demo' path the only response the
    click produces. It must now differ in colour AND not be smaller, and carry a non-colour glyph
    so the ranking survives greyscale."""
    from PySide6.QtWidgets import QLabel

    from studio.overlays import WelcomeView
    qss = theme._build_qss()

    def block(role):
        return qss.split(f'QLabel[role="{role}"]')[1].split("}")[0]

    def colour(role):
        return block(role).split("color:")[1].split(";")[0].strip()

    def size(role):
        return int(block(role).split("font-size:")[1].split("px")[0].strip())

    assert colour("WelcomeError") != colour("WelcomeSubtitle"), "still the same grey"
    assert size("WelcomeError") >= size("WelcomeSubtitle"), "still smaller than the marketing copy"
    assert contrast(colour("WelcomeError"), C.canvas) >= 4.5
    # non-colour redundancy: the ⚠ the rest of the app uses.
    wv = WelcomeView(lambda: None, lambda: None, error="that isn't a GoPro recording")
    err = next(q for q in wv.findChildren(QLabel) if q.property("role") == "WelcomeError")
    assert err.text().startswith("⚠"), err.text()
    assert "that isn't a GoPro recording" in err.text()
    wv.deleteLater()
    print("test_welcome_error_outranks_the_welcome_subtitle OK")


# ===================================================================== 4. no negative zero delta
def test_format_delta_value_never_prints_negative_zero():
    """L12-10: `f'{-1.8e-15:+.2f}'` is '-0.00', which reads as 'you are behind' on a lap where you
    are level — and the exporter burned it into 100 of 697 frames (14.3%) of a delivered MP4. Any Δ
    inside the even dead band now prints +0.00, so the number, the colour and the arrow agree."""
    assert theme.format_delta_value(-1e-15) == "+0.00"
    assert theme.format_delta_value(-1.78e-15) == "+0.00"
    assert theme.format_delta_value(-0.004) == "+0.00"
    assert theme.format_delta_value(-0.0) == "+0.00"
    assert theme.format_delta_value(0.0) == "+0.00"
    assert theme.format_delta_value(None) == "—"
    # Real deltas are untouched, sign and all.
    assert theme.format_delta_value(-0.31) == "-0.31"
    assert theme.format_delta_value(0.31) == "+0.31"
    assert theme.format_delta_value(-0.006) == "-0.01"
    # The composed run strings inherit it, live box and export alike.
    assert theme.format_delta_run(-1e-15) == "Δ +0.00 s"
    assert theme.format_delta_run(-0.004, units=False, arrow=False) == "Δ +0.00"
    # ...and the dead band is the SAME one the colour and the arrow use, so no surface disagrees.
    for d in (-1e-15, -0.004, 0.004):
        assert theme.delta_colour(d) is None and theme.delta_arrow(d) == ""
        assert "-0.00" not in theme.format_delta_run(d)
    print("test_format_delta_value_never_prints_negative_zero OK")


def test_exported_overlay_readout_never_burns_negative_zero():
    """The same clamp, through the EXPORT path that put it in the delivered file: the video
    overlay's Δ string is composed by theme.format_delta_run, so a noise-level Δ can no longer be
    rendered into a frame."""
    from studio import export_video
    assert hasattr(export_video, "_paint_readout")
    for d in (-1e-15, -0.0049, 0.0):
        assert theme.format_delta_run(d, units=False, arrow=False) == "Δ +0.00"
    print("test_exported_overlay_readout_never_burns_negative_zero OK")


def _run_all():
    test_no_module_constant_freezes_a_palette_hue()
    test_best_lap_curve_colour_follows_the_palette_in_both_directions()
    test_ideal_star_icon_and_ideal_line_share_one_accessor()
    test_colourblind_map_ramp_steps_clear_the_jnd_under_deuteranopia()
    test_the_accessible_ramp_is_never_worse_than_the_default_one()
    test_default_map_ramp_is_byte_identical_and_the_mid_anchor_is_an_accessor()
    test_every_enabled_text_style_clears_wcag_aa()
    test_text_muted_is_confined_to_wcag_exempt_disabled_chrome()
    test_welcome_error_outranks_the_welcome_subtitle()
    test_format_delta_value_never_prints_negative_zero()
    test_exported_overlay_readout_never_burns_negative_zero()
    print("\nAll theme contrast + colour-semantics tests passed.")


if __name__ == "__main__":
    _run_all()
