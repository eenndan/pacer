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
import re
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


def _hue_reads(path):
    """EVERY `C.<swappable>` read in one source file — module level in any expression AND inside
    function bodies — as (lineno, attr, enclosing function) triples.

    The wider half of the guard above. That one walks `tree.body` for bare NAME = C.hue ASSIGNMENTS
    only, which leaves two shapes of the same defect invisible: a hue read inside a FUNCTION (the
    hero Δ readout was pinned to the standard red in both palettes for exactly this reason), and a
    hue wrapped in a CONSTRUCTOR at module level (`_MARKER_RGB = QColor(C.behind)` — an assignment,
    but its value is a Call, not an Attribute).

    Each read is labelled with the thing that OWNS it — the enclosing function, or the module-level
    name it is being bound to — so an exemption names a decision rather than a line number."""
    out = []

    def visit(node, owner):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = node.name
        elif isinstance(node, ast.Assign) and owner is None:
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            owner = names[0] if names else owner
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == "C" and node.attr in _SWAPPABLE):
            out.append((node.lineno, node.attr, owner))
        for ch in ast.iter_child_nodes(node):
            visit(ch, owner)

    visit(ast.parse(open(path, encoding="utf-8").read(), path), None)
    return out


def test_no_bare_palette_hue_is_read_anywhere_in_studio():
    """The WIDE guard. Every meaning-bearing colour must be an accessor CALL resolved at draw time,
    wherever it is read — not only at module level, which is all the sibling guard above can see.

    Each exemption below is a decision with a measured reason, not a silencer."""
    EXEMPT = {
        # The source of truth the accessors read, and the identity palette (which LAP, not who is
        # faster — deliberately palette-independent; see test_chart_series_stays_palette_independent).
        ("theme.py", "_PALETTES"), ("theme.py", "CHART_SERIES"),
        # The QPalette role for framework-drawn chrome (native dialogs, widget bits the QSS misses).
        # BrightText is not a Δ surface: the hue is used as "a bright colour", carries no
        # ahead/behind meaning, and no ENABLED app text reads it. Left alone deliberately —
        # repointing it at behind_colour() would recolour OS chrome for no accessibility gain.
        ("theme.py", "_palette"),
        # map_view.MARKER_COLOR / its _MARKER_RGB brush companion — the video-position marker. The
        # U10-01 audit flagged the first as a frozen constant, but MEASUREMENT says pointing it at
        # behind_colour() would be strictly WORSE: it would then equal rainbow bucket 0 exactly in
        # BOTH palettes (dE 0.0). Frozen, its measured minimum separation across all 16 colour-blind
        # buckets is 16.40 (JND 2.3). The marker needs its OWN token, which is a map_view design
        # change, not a palette-accessor one. Both names are one decision, so both are exempt.
        ("map_view.py", "MARKER_COLOR"), ("map_view.py", "_MARKER_RGB"),
    }
    offenders = []
    for fn in sorted(os.listdir(_STUDIO)):
        if not fn.endswith(".py"):
            continue
        for lineno, attr, owner in _hue_reads(os.path.join(_STUDIO, fn)):
            if (fn, owner) not in EXEMPT:
                offenders.append(f"{fn}:{lineno} C.{attr} (in {owner})")
    assert not offenders, (
        "raw palette-swappable hues read instead of the accessor (ahead_colour / behind_colour / "
        f"best_lap_colour / best_sector_colour), so they cannot follow the palette: {offenders}")
    print("test_no_bare_palette_hue_is_read_anywhere_in_studio OK")


def test_hero_ideal_readout_follows_the_palette():
    """U10 / W4-01. `format_ideal_readout` feeds the #DiffBox — the largest text in the window — and
    it read the raw `C.behind`, so the app's biggest number kept the standard palette's red in BOTH
    palettes (a render of the readout was byte-identical between them, max per-channel |Δ| = 0 over
    391x35 px) while the Corners table 130 px below painted the same meaning in the palette's
    orange. The sibling formatter format_delta_speed already routed through delta_colour()."""
    try:
        theme.set_palette(theme.PALETTE_STANDARD)
        std = theme.format_ideal_readout(0.94, 37.0, 2, "mph")[1]
        assert std == theme.behind_colour() == C.behind, std
        theme.set_palette(theme.PALETTE_COLORBLIND)
        cb = theme.format_ideal_readout(0.94, 37.0, 2, "mph")[1]
        assert cb == theme.behind_colour(), cb
        assert cb != std, "the hero readout must MOVE with the palette, like every other Δ surface"
        # ...and it is the SAME hue the other Δ surfaces use, not a third one
        assert cb == theme.delta_colour(0.94)
        # the neutral/no-lap cases are unchanged in both palettes: there is no "ahead of ideal"
        for d in (None, 0.0):
            assert theme.format_ideal_readout(d, 37.0, 2, "mph")[1] is None
    finally:
        theme.set_palette(theme.PALETTE_STANDARD)
    print("test_hero_ideal_readout_follows_the_palette OK")


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
    # The button is a widgets.ToggleButton now and the accessor is HANDED to it uncalled — which is
    # the same contract one level up: `on_colour=theme.best_sector_colour` (no parentheses) is a
    # reference the button resolves every time it repaints its glyph, whereas
    # `on_colour=theme.best_sector_colour()` would freeze the hue at construction exactly the way
    # the old frozen `C.best` did. The missing parentheses are the whole assertion.
    assert "on_colour=theme.best_sector_colour," in src, (
        "the ideal-lap star must be handed the ACCESSOR, not a resolved colour")
    assert "on_colour=theme.best_sector_colour()" not in src
    # refresh_palette must re-tint the icon: it is an icon, not a drawn item, so refresh() misses it
    assert "self._apply_ideal_icon()" in src.split("def refresh_palette")[1].split("def ")[0]
    # ...and that hook has to reach the button, which is the only thing holding the accessor.
    from studio import plots_view as _pv
    body = src.split("def _apply_ideal_icon")[1].split("\n    def ")[0]
    assert "refresh_glyph()" in body, body
    assert hasattr(_pv.ToggleButton, "refresh_glyph")
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
# Declared locally, NOT read from theme, so this table is independent of the code under test —
# the same reason tests/test_pb_toast.py spells its own 24 px hit floor. They are the px sizes the
# QSS renders, and they set the WCAG threshold each style is judged at (large text starts at 18).
BODY, CAPTION, SMALL = 13, 11, 10

# --------------------------------------------------------------- DERIVED: what the QSS renders
# This inventory used to be hand-typed — 33 rows claiming to be "every distinct TEXT style the
# theme renders" — and it had gone stale: seven rendered (fg,bg) pairs were missing, including
# C.text_dim on C.surface_active at 4.80:1 (#MapNotice, #PBToastClose — the thinnest margin in the
# app), and one listed row asserted a pairing Qt cannot produce (PROVISIONAL_COLOR on the selection
# band: QCommonStyle paints a selected item in HighlightedText and ignores its ForegroundRole).
# It went stale for a structural reason: a MISSING row is silent. Nothing about hand-listing 20 of
# 23 pairs looks different from hand-listing all 23.
#
# So the INVENTORY is derived from theme._build_qss() and the JUDGEMENT is not. The thresholds
# (4.5:1, the 18px large-text break), the px sizes above and the WCAG/Lab maths at the top of this
# file all stay declared here, independent of the app — that is the "so the assertion cannot drift
# with the app" principle this file is built on, and it is untouched. What is derived is only the
# LIST OF THINGS TO JUDGE, which is the one part that cannot be right by being remembered.
#
# Two things the stylesheet genuinely does not know, both stated below rather than guessed:
#   * CONTAINMENT (`HOSTS`) — a `transparent` or `rgba()` background composites over whatever
#     widget is behind, which is a layout fact. An unlisted one FAILS rather than defaulting, so
#     this map cannot go quietly stale the way the old table did.
#   * the colours PYTHON paints (`PAINTED_STYLES`) — item foregrounds, pyqtgraph ticks, the
#     palette-swapped hues. No parser can see those.

# The surface each transparent/tinted element actually sits on. A tuple means "any of these", and
# every one is asserted — where the answer is genuinely 'it depends', more candidates make the
# test STRICTER, never laxer.
HOSTS = {
    # A bare QLabel goes anywhere: on the window canvas (overlays, dialogs) or on a panel surface.
    "QLabel": (C.canvas, C.surface),
    # The lap panel's tab bar lives INSIDE a PanelHeader bar, which is C.surface.
    "QTabBar": (C.surface,),
    # Panel chrome: the header/toolbar bars are C.surface, so everything they carry sits on it.
    'QLabel[role="BarLabel"]': (C.surface,),
    "QLabel#DiffBox": (C.surface,),
    "QLabel#PaneBadge": (C.surface,),
    # The video transport's timecode, inline in a PanelToolbar. It used to declare a C.surface fill
    # of its OWN because it was a full-width band under the buttons; it is on the bar now, exactly
    # as #DiffBox is on the charts header, and the bar is what it composites onto.
    "QLabel#Readout": (C.surface,),
    # The trust strip over the map: #TrustStrip is itself transparent, so its two banner lines
    # composite onto the map panel's surface.
    "QLabel#ProvisionalBanner": (C.surface,),
    # The excluded-lap strip sits under the lap grid, on the panel surface.
    "QLabel#LapExcludedBody": (C.surface,),
    'QLabel#LapExcludedHeader[tone="warn"]': (C.surface,),
    # The PB toast is a card with its own C.surface_active fill; everything inside it is on that.
    "QLabel#PBToastTitle": (C.surface_active,),
    "QLabel#PBToastBody": (C.surface_active,),
    "QPushButton#PBToastLink": (C.surface_active,),
    "QPushButton#PBToastClose": (C.surface_active,),
    # The app's ONE empty state (widgets.EmptyState). Its container carries the surface when it
    # replaces a panel's content (`card="true"`) and nothing when it floats on the window canvas,
    # so its two type roles have to clear AA on BOTH — which is the point of the object: the same
    # words at the same size on either surface, rather than a role that meant `C.surface` at five
    # sites and `C.canvas` at a sixth.
    'QLabel[role="EmptyTitle"]': (C.canvas, C.surface),
    'QLabel[role="EmptyBody"]': (C.canvas, C.surface),
    # The welcome / loading overlays fill the window, so their type is on the canvas.
    'QLabel[role="WelcomeSubtitle"]': (C.canvas,),
    'QLabel[role="WelcomeError"]': (C.canvas,),
    'QLabel[role="LoadingTitle"]': (C.canvas,),
    "QPushButton#LoadingCancel": (C.canvas,),
    # The Shortcuts / About cards: dialog prose and key caps, on the dialog's canvas; the same
    # roles are also used on panel surfaces (the library dialog's summary/privacy notes), so both.
    'QLabel[role="KeyCap"]': (C.canvas, C.surface),
    'QLabel[role="Note"]': (C.canvas, C.surface),
    'QLabel[role="Hint"]': (C.canvas, C.surface),
    'QLabel[role="Title"]': (C.canvas, C.surface),
    'QLabel[role="Tagline"]': (C.canvas, C.surface),
    # An interactive chip's amber ON tint: chips ride in panel headers and toolbars (C.surface)
    # and, for the reference chip, the status bar (C.canvas). The ON tint REPLACES the chip's own
    # fill, so it composites onto the bar behind it, not onto the resting pill.
    'QLabel[role="Chip"][tone="warn"]': (C.canvas, C.surface),
    'QPushButton[role="Chip"]': (C.canvas, C.surface),
    # A checked ToggleButton's amber tint, likewise: buttons sit in panel toolbars (C.surface) and
    # on the welcome overlay (C.canvas).
    "QPushButton": (C.canvas, C.surface),
}

_INERT_BG = {"transparent", "none", "inherit"}
_RGBA = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)")


class _Unhosted(Exception):
    pass


def _qss_rules():
    """Every (selector-part, declarations) pair in the shipped stylesheet.

    Comments stripped first, and anchored on nothing — see the note in
    test_the_theme_never_takes_a_widgets_own_font_away for the parse that consumed every other
    rule and hid half this stylesheet from every guard in the repo."""
    qss = re.sub(r"/\*.*?\*/", "", theme._build_qss(), flags=re.S)
    blocks = re.findall(r"([^{}]*)\{([^{}]*)\}", qss, flags=re.S)
    assert len(blocks) > 60, f"the QSS block parse found only {len(blocks)} rules"
    out = []
    for selector, body in blocks:
        d = {}
        for line in body.split(";"):
            k, _, v = line.partition(":")
            if _:
                d[k.strip().lower()] = " ".join(v.split())
        for part in " ".join(selector.split()).split(","):
            part = part.strip()
            if part:
                out.append((part, d))
    return out


def _derived_text_styles():
    """(selector, fg, bg, px) for every colour the stylesheet declares, over every background it
    can be painted on. Raises `_Unhosted` naming any selector whose background cannot be resolved,
    so the HOSTS map above cannot silently fall behind the sheet."""
    rules = _qss_rules()
    own_bg = {p: (d.get("background-color") or d.get("background"))
              for p, d in rules if ("background-color" in d or "background" in d)}
    own_px = {p: int(d["font-size"][:-2]) for p, d in rules
              if d.get("font-size", "").endswith("px")}

    def ancestor(part, table):
        """The longest STRICT PREFIX of this selector that the table knows. QSS inherits only down
        a prefix chain — `QMenu::item:disabled` from `QMenu`, never from a same-class sibling. A
        naive base-class cascade makes the disabled menu item inherit the SELECTED item's amber
        tint and invents AA failures that do not exist."""
        best = None
        for k in table:
            if part != k and part.startswith(k) and (best is None or len(k) > len(best)):
                best = k
        return best

    def hosts(part):
        key = part.split(":")[0]           # drop pseudo-states and pseudo-elements
        if key not in HOSTS:
            raise _Unhosted(part)
        return HOSTS[key]

    def backgrounds(part, origin=None):
        """Where `part`'s text lands, as one or more opaque colours.

        `origin` is the selector we started from, and it is what a HOST lookup uses. That matters:
        `QLabel { background: transparent }` cascades to every QLabel rule that declares no
        background of its own, and resolving the host under `QLabel` instead of under the rule we
        came from would let one entry — "a label is on the canvas or a panel surface" — silently
        vouch for every label in the app, including the ones on `C.surface_active` whose 4.80:1 is
        the whole reason this derivation exists. Inheritance walks up; containment does not."""
        origin = origin or part
        v = own_bg.get(part)
        if v is None:
            anc = ancestor(part, own_bg)
            return backgrounds(anc, origin) if anc else hosts(origin)
        v = v.strip()
        if v.startswith("#"):
            return (v,)
        head = v.split()[0].lower() if v.split() else ""
        if head in _INERT_BG or not head:
            return hosts(origin)
        m = _RGBA.match(v)
        if m:
            r, g, b, a = m.group(1), m.group(2), m.group(3), m.group(4)
            hexed = f"#{int(r):02X}{int(g):02X}{int(b):02X}"
            # A semi-transparent fill composites over what is BEHIND it, and that differs by kind:
            # a SUB-CONTROL (`QMenu::item:selected`) is drawn on top of its own widget's
            # background, which the sheet knows; a tint on the WIDGET ITSELF
            # (`QPushButton:checked`, `[tone="warn"]`) replaces that widget's background and lands
            # on its parent, which it does not. Backwards, this is how a naive cascade invents AA
            # failures that nothing on screen has.
            under = backgrounds(part.split("::")[0]) if "::" in part else hosts(origin)
            return tuple(_over(hexed, float(a or 1.0), h) for h in under)
        raise _Unhosted(f"{part} (background {v!r} is not a hex, an rgba() or transparent)")

    out, unhosted = [], []
    for part, d in rules:
        fg = d.get("color", "").strip()
        if not fg.startswith("#"):
            continue
        px = own_px.get(part) or own_px.get(ancestor(part, own_px) or "", BODY)
        try:
            for bg in backgrounds(part):
                out.append((part, fg, bg, px))
        except _Unhosted as exc:
            unhosted.append(str(exc))
    assert not unhosted, (
        "these selectors paint text on a background this test cannot resolve — add the surface "
        "they sit on to HOSTS (a layout fact the stylesheet does not carry):\n  "
        + "\n  ".join(sorted(set(unhosted))))
    return out


# ------------------------------------------------- HAND-KEPT: the colours PYTHON paints
# Not derivable: these are QBrush/QColor foregrounds set on items and pyqtgraph, and the
# palette-swapped hues, none of which appear in the stylesheet at all.
PAINTED_STYLES = [
    ("pyqtgraph axis ticks", C.text_dim, C.surface, 11),
    ("Stats sparkline ticks (SMALLEST TYPE)", C.text_dim, C.surface, SMALL),
    ("QPalette PlaceholderText", C.text_dim, C.surface, BODY),
    ("PROVISIONAL_COLOR (lap grid, unselected)", theme.PROVISIONAL_COLOR, C.surface, BODY),
    ("#DiffBox behind", C.behind, C.surface, 22),
    ("#DiffBox ahead", C.ahead, C.surface, 22),
    ("best-lap cell (colour-blind)", "#4C9BFF", C.surface, BODY),
    ("best-sector cell (standard)", C.best, C.surface, BODY),
    ("best-sector cell (colour-blind)", "#38C7C7", C.surface, BODY),
    ("Δ behind (colour-blind)", "#F0902B", C.surface, BODY),
    # Item text on the SELECTION band: Qt paints a selected item in QPalette::HighlightedText and
    # ignores the item's own ForegroundRole, so C.text is the only ink that lands here. (The old
    # table listed PROVISIONAL_COLOR on this background; measured on the real grid, a provisional
    # lap paints #9AA1AD unselected and C.text selected — the demotion is Qt's to drop.)
    ("selected item text", C.text, C.sel_bg, BODY),
    ("table cell (alt row)", C.text, C.surface_alt, BODY),
]

# WCAG 1.4.3 exempts text in INACTIVE user-interface components. C.text_muted is the ONLY token
# allowed below AA, and only on genuinely disabled states — matched against the derived selectors
# by substring, so a new sub-AA style cannot join by being forgotten.
DISABLED_EXEMPT = (":disabled",)


def test_every_enabled_text_style_clears_wcag_aa():
    """U1-01: six styles shared one token, C.text_muted at 3.17:1 / 3.68:1. The token stayed (it is
    right for disabled chrome); the four ENABLED roles that were borrowing it moved to C.text_dim,
    and the failure message moved to the accent it should have had all along.

    The inventory is now derived from the sheet (see the note above `HOSTS`), so a style cannot
    escape this by never being typed into a list."""
    fails, exempt = [], []
    styles = [(p, fg, bg, px) for p, fg, bg, px in _derived_text_styles()] + PAINTED_STYLES
    for name, fg, bg, px in styles:
        if any(k in name for k in DISABLED_EXEMPT):
            exempt.append((name, fg, bg))
            continue
        need = 3.0 if px >= 18 else 4.5     # WCAG "large text" starts at 18.66px bold / 24px
        r = contrast(fg, bg)
        if r < need:
            fails.append(f"{name}: {fg} on {bg} = {r:.2f}:1 (needs {need})")
    assert not fails, "styles below WCAG AA: " + "; ".join(fails)
    pairs = {(fg.upper(), bg.upper()) for _n, fg, bg, _p in styles}
    worst = min(styles, key=lambda s: contrast(s[1], s[2]) if not any(
        k in s[0] for k in DISABLED_EXEMPT) else 99)
    print(f"test_every_enabled_text_style_clears_wcag_aa OK ({len(styles)} styles, {len(pairs)} "
          f"distinct fg/bg pairs, {len(exempt)} disabled-exempt; thinnest = {worst[0]} at "
          f"{contrast(worst[1], worst[2]):.2f}:1)")


def test_the_derived_inventory_covers_the_whole_stylesheet():
    """The derivation's own honesty check: every rule that declares a text colour must produce at
    least one judged (fg,bg) pair, and the pairs must OUTNUMBER what the hand table used to list.

    The old table claimed to be complete at 20 distinct pairs while the sheet rendered more, and
    nothing could tell — a hand list has no way to know what it is missing. This pins the direction
    of travel: the derived set may grow, but it may not quietly shrink back to a subset."""
    derived = _derived_text_styles()
    coloured = {p for p, d in _qss_rules() if d.get("color", "").strip().startswith("#")}
    covered = {p for p, _fg, _bg, _px in derived}
    assert coloured == covered, f"rules with a colour but no judged pair: {sorted(coloured - covered)}"
    pairs = {(fg.upper(), bg.upper()) for _p, fg, bg, _x in derived + PAINTED_STYLES}
    assert len(pairs) >= 19, (
        f"only {len(pairs)} distinct pairs judged — the parse or the resolver has regressed; the "
        f"hand table this replaced already listed 19")
    # The two specific corrections, pinned so neither can come back. The first is the pair the old
    # table missed and the thinnest margin in the app; the second is the row it listed that Qt
    # cannot produce (a selected item is painted in HighlightedText, its ForegroundRole ignored).
    assert (C.text_dim.upper(), C.surface_active.upper()) in pairs, (
        "C.text_dim on C.surface_active is not being judged — that is #MapNotice and "
        "#PBToastClose, at 4.80:1 the app's narrowest margin, and it was invisible to the hand "
        "table for exactly as long as it was un-typed")
    assert (theme.PROVISIONAL_COLOR.upper(), C.sel_bg.upper()) not in pairs, (
        "PROVISIONAL_COLOR on the selection band is back — measured on the real lap grid, a "
        "provisional row paints #9AA1AD unselected and C.text selected; this pairing is never "
        "rendered")
    # …and every HOSTS entry is load-bearing: an unused one is the stale-ledger shape again, which
    # is the failure this whole change is about (see tests/test_inline_styles.py:_no_dead_exemptions).
    used = {p.split(":")[0] for p in coloured}
    dead = sorted(k for k in HOSTS if k not in used)
    assert not dead, f"HOSTS entries that host nothing — delete them: {dead}"
    print(f"test_the_derived_inventory_covers_the_whole_stylesheet OK "
          f"({len(coloured)} coloured rules -> {len(derived)} judged styles, "
          f"{len(pairs)} distinct pairs, {len(HOSTS)} stated hosts)")


def test_text_muted_is_confined_to_wcag_exempt_disabled_chrome():
    """The split, pinned. C.text_muted is BELOW AA by design and may only dress inactive controls;
    every enabled role uses a token that clears 4.5:1. If someone re-points an enabled role at it,
    test_every_enabled_text_style_clears_wcag_aa fails — this one documents WHY the exemption is
    legitimate and keeps the exempt list short."""
    assert contrast(C.text_muted, C.surface) < 4.5     # the honest admission
    # Both directions, over the DERIVED inventory rather than a remembered list: every style that
    # uses the sub-AA token must be a disabled state, and every style the exemption lets past must
    # be using that token — so the exemption can neither grow to cover an enabled role nor be left
    # excusing something that has moved off it.
    styles = _derived_text_styles() + PAINTED_STYLES
    muted = {n for n, fg, _bg, _px in styles if fg.upper() == C.text_muted.upper()}
    excused = {n for n, _fg, _bg, _px in styles if any(k in n for k in DISABLED_EXEMPT)}
    assert muted, "nothing uses C.text_muted any more — drop the exemption with the token"
    assert muted <= excused, f"C.text_muted on an ENABLED style: {sorted(muted - excused)}"
    assert excused <= muted, (
        f"a :disabled style not on the sub-AA token — it should simply be held to AA: "
        f"{sorted(excused - muted)}")
    # every ENABLED style clears AA at its own size
    assert contrast(C.text_dim, C.surface) >= 4.5
    assert contrast(C.text_dim, C.canvas) >= 4.5
    qss = theme._build_qss()
    # the four roles that were borrowing the disabled token are off it
    for role in ('QLabel[role="EmptyBody"]', 'QLabel[role="WelcomeSubtitle"]',
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


_BLANKET_SELECTORS = ("QWidget", "*", "QLabel", "QFrame")


def test_the_theme_never_takes_a_widgets_own_font_away():
    """W10-01. A stylesheet font is resolved OVER a widget's own font on every polish, so a font
    declaration on a selector that matches everything silently discards the size and family of
    every setFont() in the app — a call that reports success, echoes the font back from font(),
    and paints something else. The theme's base rule used to carry `font-family` + `font-size`,
    and the Stats page's 29 tiles asked for 15/600 over 12 and painted 13 over 13: value and
    caption the same size, the hierarchy gone. It is invisible from inside the app, so it is
    pinned from both ends here — the shape of the rule, and the behaviour it produces.

    The app-wide default belongs in apply_theme's app.setFont(), which LOSES to setFont. A widget
    that genuinely wants a fixed size still takes a rule of its own, keyed on objectName or a role
    property (#DiffBox, [role="EmptyBody"] …) — those match one surface, not every widget."""
    from PySide6.QtWidgets import QLabel, QWidget

    # 1. the shape: no blanket selector may declare a font. Comments go first — a QSS comment can
    # carry braces and selector-looking text, and this rule is about what Qt PARSES.
    qss = re.sub(r"/\*.*?\*/", "", theme._build_qss(), flags=re.S)
    # The old pattern anchored on `(?:^|\})` and CONSUMED the previous rule's closing brace, so
    # findall could not start the next match there and returned every OTHER rule — 44 of the 87
    # that are in the stylesheet. Proved with "A{a}B{b}C{c}" -> [(A,a), (C,c)]. This one anchors on
    # nothing: a selector is whatever sits between the last `}` and the next `{`.
    blocks = re.findall(r"([^{}]*)\{([^{}]*)\}", qss, flags=re.S)
    assert len(blocks) > 60, f"the QSS block parse found only {len(blocks)} rules"
    for selector, body in blocks:
        sel = " ".join(selector.split()).strip()
        # A selector is "blanket" when a bare class name matches with no #id / [prop] / ::sub-
        # control narrowing it. Those are the ones that reach every widget in the window.
        parts = [p.strip() for p in sel.split(",") if p.strip()]
        blanket = [p for p in parts if p in _BLANKET_SELECTORS]
        if blanket and "font" in body:
            raise AssertionError(
                f"blanket selector {blanket} declares a font — it will outrank every setFont "
                f"in the app:\n{sel} {{{body}}}")

    # 2. the behaviour: a widget's own font survives the theme, at the size it asked for.
    theme.apply_theme(_APP)
    host = QWidget()
    lab = QLabel("1:08.771", host)
    lab.setFont(theme.mono_font(theme.HERO, theme.W_SEMIBOLD))
    small = QLabel("best lap", host)
    small.setFont(theme.ui_font(theme.CAPTION))
    plain = QLabel("body text", host)                    # no setFont: takes the app default
    host.show()
    for _ in range(4):
        _APP.processEvents()
    assert lab.fontInfo().pixelSize() == theme.HERO, lab.fontInfo().pixelSize()
    assert small.fontInfo().pixelSize() == theme.CAPTION, small.fontInfo().pixelSize()
    assert plain.fontInfo().pixelSize() == theme.BODY, plain.fontInfo().pixelSize()
    assert lab.fontInfo().pixelSize() > plain.fontInfo().pixelSize() > small.fontInfo().pixelSize()
    host.hide()
    print("test_the_theme_never_takes_a_widgets_own_font_away OK "
          f"({lab.fontInfo().pixelSize()} / {plain.fontInfo().pixelSize()} / "
          f"{small.fontInfo().pixelSize()} px)")


def test_apply_theme_registers_the_fonts_its_qss_names():
    """W10-04. theme.UI_FAMILIES leads with "Inter", which only exists as a family once the bundled
    TTFs are in the font DB — and six test files called apply_theme() WITHOUT register_fonts(), so
    Qt substituted a family and they measured a layout up to 13 % off the shipped advances (the
    charts column's asserted minimum: 752 px in-test against 759 shipped). The pair is now one
    call, so the two can no longer drift."""
    theme._fonts_registered = False
    theme._inter_available = False
    theme.apply_theme(_APP)
    assert theme._fonts_registered, "apply_theme must register the fonts its QSS names"
    assert theme._inter_available, "the bundled Inter TTFs must be in the font DB after apply_theme"
    assert theme.ui_font(theme.BODY).families()[0] == "Inter"
    # ...and the family list the QFont builders use IS the one the QSS names (one source, two uses).
    assert theme.UI_STACK == ",".join(f'"{f}"' for f in theme.UI_FAMILIES)
    assert theme.MONO_STACK == ",".join(f'"{f}"' for f in theme.MONO_FAMILIES)
    # mono_font is Inter+tnum where Qt can apply the feature and the mono stack below it — either
    # way its family list is one of the two module tuples, never a third hand-kept copy.
    # NOTE this assertion is about the FAMILY only. It passed for the whole life of the bug that
    # `test_mono_font_actually_delivers_tabular_figures` now covers: the family was right and the
    # digits were still proportional.
    assert tuple(theme.mono_font(11).families()) in (theme.UI_FAMILIES, theme.MONO_FAMILIES)
    print("test_apply_theme_registers_the_fonts_its_qss_names OK")


def test_mono_font_actually_delivers_tabular_figures():
    """`mono_font` must produce digits that all advance the SAME width — measured, not declared.

    The bug this pins: `_qt_supports_feature` checked the PySide version and
    `hasattr(QFont, "setFeature")` — an API PRESENCE check — and `mono_font` then called
    `setFeature("tnum", 1)` inside a bare `except: pass`. On PySide6 6.11.1 that call raises
    `ValueError` (only `QFont.Tag("tnum")` binds), so the feature never applied, `featureTags()`
    stayed empty, and `register_fonts()` printed "tabular figures via tnum feature" while every lap
    time, split, sector and delta in the app rendered PROPORTIONAL.

    Measured on Inter 13 px before the fix: NINE distinct digit advances, `1` at 5.281 px against
    `4` at 8.391 px, so two same-length lap times differed by 12 px in a right-aligned column
    (`1:08.201` 50.06 px vs `1:11.111` 38.03 px). That is the whole reason this face exists.

    The assertion is on the RENDERED metric rather than on `featureTags()`, so it also holds on a
    build that reaches tabular figures through the mono stack instead — the contract is the
    alignment, not the mechanism."""
    from PySide6.QtGui import QFontMetricsF

    theme.register_fonts()  # _APP is already live at module scope; this is the font DB half
    for size in (theme.CAPTION, theme.TABLE, theme.HERO):
        fm = QFontMetricsF(theme.mono_font(size))
        widths = {d: round(fm.horizontalAdvance(d), 3) for d in "0123456789"}
        assert len(set(widths.values())) == 1, (
            f"mono_font({size}) digits are proportional, not tabular: "
            f"{len(set(widths.values()))} distinct advances {sorted(set(widths.values()))} "
            f"(per digit {widths}) — a right-aligned numeric column cannot line up")

    # The consequence, stated as the thing a user would see: equal-length times measure equal.
    fm = QFontMetricsF(theme.mono_font(theme.TABLE))
    a, b = fm.horizontalAdvance("1:08.201"), fm.horizontalAdvance("1:11.111")
    assert a == b, f"equal-length lap times measure {a} vs {b} px"
    print("test_mono_font_actually_delivers_tabular_figures OK")


def _run_all():
    test_the_theme_never_takes_a_widgets_own_font_away()
    test_apply_theme_registers_the_fonts_its_qss_names()
    test_mono_font_actually_delivers_tabular_figures()
    test_no_module_constant_freezes_a_palette_hue()
    test_no_bare_palette_hue_is_read_anywhere_in_studio()
    test_hero_ideal_readout_follows_the_palette()
    test_best_lap_curve_colour_follows_the_palette_in_both_directions()
    test_ideal_star_icon_and_ideal_line_share_one_accessor()
    test_colourblind_map_ramp_steps_clear_the_jnd_under_deuteranopia()
    test_the_accessible_ramp_is_never_worse_than_the_default_one()
    test_default_map_ramp_is_byte_identical_and_the_mid_anchor_is_an_accessor()
    test_every_enabled_text_style_clears_wcag_aa()
    test_the_derived_inventory_covers_the_whole_stylesheet()
    test_text_muted_is_confined_to_wcag_exempt_disabled_chrome()
    test_welcome_error_outranks_the_welcome_subtitle()
    test_format_delta_value_never_prints_negative_zero()
    test_exported_overlay_readout_never_burns_negative_zero()
    print("\nAll theme contrast + colour-semantics tests passed.")


if __name__ == "__main__":
    _run_all()
