"""The DIMENSIONAL guard — the sibling of tests/test_contrast.py, which guards the colour half.

WHY THIS FILE EXISTS. Pacer had a locked COLOUR system and no SPATIAL one. `theme.C` was a
documented token set with a test that failed the build if any module froze a hue; every spacing,
radius and control height, meanwhile, was a literal chosen at its own call site. Measured on the
commit before the tokens landed, `theme._build_qss()` alone carried **21 distinct px values, 7
border radii and 17 different padding pairs** — among them `5px 11px` and `4px 9px`. Nobody
chooses those. You arrive at them by nudging, and an interface assembled out of nudges reads as
assembled however good its colours are.

So: three checks, in the same idiom as the colour guard next door.

  1. THE STYLESHEET IS ON THE SCALE. Every padding / margin / border-radius / min-height /
     max-height / min-width / max-width in `theme._build_qss()` is a SPACE_* or RADIUS_* step, or
     — for the two DERIVED shapes the system has — a value the test reconstructs rather than
     hard-codes:
       * a `:focus` rule's padding must be `theme.focus_pad(step)`, i.e. the base step minus the
         pixel the thicker ring costs, which is what keeps the outer box identical when focus
         arrives (the contract tests/test_focus_cues.py measures). `5px 11px` and `4px 9px` WERE
         this compensation — computed by hand, off two different bases;
       * a `min-height` is a CONTENT box in Qt's stylesheet model, so it is checked by
         reconstructing what it actually paints: value + that rule's own padding + that rule's own
         border must equal a declared SIZE token. A nudge to either half fails.

  2. NO MODULE HAND-PICKS A LAYOUT DIMENSION. An AST walk over studio/*.py for literal
     setContentsMargins / setSpacing / setFixedHeight / setFixedSize arguments, each labelled by
     the CLASS AND METHOD that owns it — the shape tests/test_contrast.py:_hue_reads uses, so an
     exemption names a decision rather than a line number. The EXEMPT set is the migration backlog
     and its size is printed: this is the number later phases are measured by.

  3. THE TOKENS AGREE WITH THEMSELVES, AND WITH WHAT QT PAINTS. The size ladder is ordered, every
     spacing step is a whole multiple of the sub-step, the two derivation helpers round-trip — and
     a real QPushButton, QComboBox and QTabBar built under the shipped theme really do stand at
     CTRL_H. A declared height that the box model quietly refuses is worse than no token at all.

  4. THE FOUR PANEL HEADERS ARE ONE HEIGHT. This was the check the token PR deliberately left out,
     because it was not yet true: the app shipped 32 / 38 / 43 / 43, and the tokens alone narrowed
     that to 36 / 36 / 36 / 38 as a SIDE EFFECT of controls landing on one height — not because
     anything declared a header height. The header/toolbar split declares it, so the assertion
     lands with it, measured on the REAL CentralView rather than on a widget built for the test.

Offscreen Qt.  Checks 1-3 need no telemetry and no pacer; check 4 builds the production view over
the deterministic synthetic session.  Run: python tests/test_design_system.py
"""
import ast
import os
import re
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()            # module scope, BEFORE any widget: measure the SHIPPING font stack

from PySide6.QtCore import QRect, Qt  # noqa: E402

from studio import theme  # noqa: E402

_STUDIO = os.path.join(_REPO, "studio")

# The scale, read from the tokens rather than re-typed, so this file can never disagree with the
# thing it guards about WHAT the steps are — only about whether they are used.
SPACE = (theme.SPACE_XXS, theme.SPACE_XS, theme.SPACE_S, theme.SPACE_M,
         theme.SPACE_L, theme.SPACE_XL, theme.SPACE_2XL, theme.SPACE_3XL)
RADII = (theme.RADIUS_S, theme.RADIUS_M, theme.RADIUS_L)
SIZES = (theme.HIT_MIN, theme.CTRL_H, theme.TOOLBAR_H, theme.PANEL_HDR_H,
         theme.SPLITTER_HANDLE_PX, theme.ICON_BTN.height(), theme.ICON_BTN.width())

_SPACE_OK = set(SPACE) | {0}                    # zero is always a legal gap
_FOCUS_OK = {theme.focus_pad(v) for v in SPACE}  # the ring compensation, derived not chosen
_BOX_OK = _SPACE_OK | set(SIZES)                # what a reconstructed outer box may come to
# A CIRCLE is half its own box — a derivation, not a fourth radius step, in exactly the sense
# focus_pad is not a fourth padding step. The stylesheet already contained the relation without
# being able to say so (the scrub knob is SPACE_L across at RADIUS_M, a circle only because
# RADIUS_M happens to be half of SPACE_L); the video scrub bar needed the same shape at HIT_MIN,
# could not spell 12 as a token, and therefore shipped as a hand-written stylesheet string inside
# video_view.py — off the scale AND out of this guard's reach. Admitting the derivation is what
# brought it back in.
_RADIUS_OK = set(RADII) | {theme.pill_radius(s) for s in (*SIZES, *SPACE)}


# ============================================================================ QSS parsing
def _qss_blocks():
    """(selector, body) for EVERY rule in the theme's stylesheet.

    NOTE THE REGEX. tests/test_contrast.py:test_the_theme_never_takes_a_widgets_own_font_away
    parses the same stylesheet with `(?:^|\\})\\s*([^{}@]*?)\\{([^{}]*)\\}`, which CONSUMES the
    previous rule's closing brace as its anchor — so `re.findall` cannot start the next match at
    that brace and silently returns every OTHER rule (44 of the 87 that are there; proved with
    "A{a}B{b}C{c}" -> [(A,a), (C,c)]). A guard that sees half a stylesheet is a guard that passes
    for the wrong reason, so this one anchors on nothing and takes the selector as whatever sits
    between the previous `}` and the next `{`."""
    qss = re.sub(r"/\*.*?\*/", "", theme._build_qss(), flags=re.S)
    return [(" ".join(sel.split()).strip(), body)
            for sel, body in re.findall(r"([^{}]*)\{([^{}]*)\}", qss, flags=re.S)]


def _decls(body):
    """(property, value) pairs of one rule body, lower-cased properties."""
    out = []
    for decl in body.split(";"):
        if ":" in decl:
            prop, _, val = decl.partition(":")
            out.append((prop.strip().lower(), val.strip()))
    return out


def _px(val):
    """Every `<n>px` in a declaration value, in order, as ints."""
    return [int(m) for m in re.findall(r"(-?\d+)px", val)]


def _sides(vals):
    """A CSS 1/2/3/4-value box shorthand expanded to (top, right, bottom, left)."""
    if len(vals) == 1:
        return (vals[0],) * 4
    if len(vals) == 2:
        return (vals[0], vals[1], vals[0], vals[1])
    if len(vals) == 3:
        return (vals[0], vals[1], vals[2], vals[1])
    return tuple(vals[:4])


def _edge(decls, shorthand, which):
    """The width Qt charges on one edge for `border` / `padding`, honouring both the shorthand and
    the per-edge override that may follow it (`border: none; border-bottom: 2px solid …`)."""
    i = {"top": 0, "right": 1, "bottom": 2, "left": 3}[which]
    w = 0
    for prop, val in decls:
        px = _px(val)
        if prop == shorthand:
            w = _sides(px)[i] if px else 0        # `border: none` / a colour-only value costs 0
        elif prop == f"{shorthand}-{which}":
            w = px[0] if px else 0
    return w


def _chrome(decls, axis):
    """A rule's own padding+border on one axis — what Qt adds AROUND a min-height/min-width."""
    a, b = ("top", "bottom") if axis == "v" else ("left", "right")
    return sum(_edge(decls, s, e) for s in ("padding", "border") for e in (a, b))


# ============================================================================ 1. the stylesheet
# Properties whose values are DIMENSIONS the scale owns. `width` / `height` on a sub-control (the
# scrollbar track, the slider knob, the combo chevron) are deliberately NOT in this list: they are
# artwork sizes rather than layout, they are already all on the scale, and pinning them here would
# make the guard fight the next honest artwork change.
_BOX_PROPS = ("padding", "margin")
_RADIUS_PROPS = ("border-radius",)
_MIN_PROPS = {"min-height": "v", "max-height": "v", "min-width": "h", "max-width": "h"}


def test_every_stylesheet_dimension_is_on_the_scale():
    """Check 1. The 21 px values / 7 radii / 17 padding pairs the stylesheet shipped with are the
    single clearest evidence that the app was nudged rather than designed. Every one of them is now
    a token or a stated derivation of tokens, and this fails the build on the next literal.

    EXEMPT is empty, and that is the point: the file this guards is the one file the tokens landed
    in. The migration backlog lives in the SOURCE check below, not here."""
    EXEMPT = set()      # (selector, property) — none. theme.py is fully migrated.
    offenders = []
    checked = 0
    for sel, body in _qss_blocks():
        decls = _decls(body)
        focus = ":focus" in sel
        for prop, val in decls:
            px = _px(val)
            if not px or (sel, prop) in EXEMPT:
                continue
            base = prop.split("-")[0]
            if base in _BOX_PROPS:
                checked += 1
                # A negative margin is a deliberate overlap (the slider knob sitting down over its
                # groove); its MAGNITUDE still has to be a step.
                bad = [v for v in px
                       if abs(v) not in _SPACE_OK and not (focus and v in _FOCUS_OK)]
                if bad:
                    offenders.append(f"{sel} {{ {prop}: {val} }} — {bad} off the SPACE scale")
            elif prop in _RADIUS_PROPS:
                checked += 1
                if px[0] not in _RADIUS_OK:
                    offenders.append(
                        f"{sel} {{ {prop}: {val} }} — not a RADIUS_* step, and not "
                        f"theme.pill_radius() of a declared size")
            elif prop in _MIN_PROPS:
                checked += 1
                outer = px[0] + _chrome(decls, _MIN_PROPS[prop])
                if outer not in _BOX_OK:
                    offenders.append(
                        f"{sel} {{ {prop}: {val} }} — paints {outer}px "
                        f"(value + this rule's own padding/border), which is not a size token")
    assert checked >= 25, f"the dimension scan only found {checked} declarations to check"
    assert not offenders, (
        "stylesheet dimensions that are not on the spatial scale (theme.SPACE_* / RADIUS_* / the "
        "size tokens, or theme.focus_pad / ctrl_content_h derived from them):\n  "
        + "\n  ".join(offenders))
    print(f"test_every_stylesheet_dimension_is_on_the_scale OK ({checked} declarations, "
          f"{len(_qss_blocks())} rules)")


def test_the_focus_ring_is_paid_for_out_of_the_padding_not_the_box():
    """The relationship behind `5px 11px`, asserted instead of exempted.

    Arriving on a control must RECOLOUR pixels, never move them — the ring is FOCUS_RING_PX where
    the resting border is BORDER_PX, so a `:focus` rule keeps its outer box only by handing that
    difference back out of its padding. tests/test_focus_cues.py measures the consequence on the
    live widgets; this measures the arithmetic in the stylesheet, which is where it is easy to
    lose during a refactor and hard to see afterwards."""
    pairs = 0
    for sel, body in _qss_blocks():
        if ":focus" not in sel:
            continue
        decls = dict(_decls(body))
        if "padding" not in decls or "border" not in decls:
            continue
        ring = _px(decls["border"])[0]
        for v in _px(decls["padding"]):
            base = v + (ring - theme.BORDER_PX)
            assert base in _SPACE_OK, (
                f"{sel}: padding {v}px does not come back to a scale step at the resting "
                f"border ({base}px)")
            assert v == theme.focus_pad(base), f"{sel}: {v} != focus_pad({base})"
            pairs += 1
    assert pairs >= 4, f"only {pairs} focus paddings found — the focus rules changed shape"
    print(f"test_the_focus_ring_is_paid_for_out_of_the_padding_not_the_box OK ({pairs} paddings)")


# ============================================================================ 2. the source
# setHorizontalSpacing / setVerticalSpacing are here for the reason the sweep of this wave gave for
# rewriting its own equality pins: a guard that checks `setSpacing` and not its QGridLayout /
# QFormLayout siblings is excusing itself. They set exactly the same quantity — the gap between two
# things in a layout — and leaving them out is how the Shortcuts card kept a 6 px row gap through a
# phase whose entire subject was gaps. EXTENT calls (setMinimumWidth / setFixedWidth) are
# deliberately NOT here: a dialog's measure, a busy bar's length and a card's floor are sizes of
# things, not spaces between them, and the SPACE scale has nothing to say about 560 or 220. The
# defect an extent can carry is a different assertion, and it has its own check below (4b).
_CALLS = ("setContentsMargins", "setSpacing", "setFixedHeight", "setFixedSize",
          "setHorizontalSpacing", "setVerticalSpacing",
          # ...and the two the walker could not see, added when the video lane found six off-scale
          # numbers in one file that this check reported as clean. `setHandleWidth` sets a
          # DIVIDER's width, which is the same quantity `theme.SPLITTER_HANDLE_PX` declares and one
          # view was duplicating as a literal 8. `setMinimumWidth` is the extent that hurt: a
          # combo's floor of `max(150, min(hint, 260))` is honoured OVER the space the layout has,
          # so 260 px of picker painted 21 px past a 254 px pane. Both are sizes a call site had
          # picked; both are now on the hook.
          "setHandleWidth", "setMinimumWidth")


def _dimension_literals(path):
    """EVERY hand-picked layout-dimension call in one source file, as
    (lineno, call, values, owner) — where `owner` is `Class.method`, or the bare function, or the
    module-level name being bound.

    The same walker shape as tests/test_contrast.py:_hue_reads, and for the same reason: an
    exemption should name the DECISION that made the value (a widget's constructor), not a line
    number that moves the moment someone adds an import.

    IT RESOLVES MODULE-LEVEL CONSTANTS, and that is the half that was missing. The walker used to
    take all-literal calls only — a `Name` argument made `len(vals) != len(args)` and the call was
    skipped as "a token read", which is exactly what a token read looks like AND exactly what
    `setContentsMargins(_PANE_INSET, 0, _PANE_INSET, 0)` looked like with `_PANE_INSET = 5` five
    lines up. A name that resolves to an integer literal in the SAME module is not a token; it is a
    hand-picked number with a nicer spelling, and this file's whole subject is that those are the
    same thing. `theme.SPACE_S` is an ATTRIBUTE, not a Name, so a real token read is still skipped.
    """
    src = ast.parse(open(path, encoding="utf-8").read(), path)
    # module-level `NAME = <int literal>` — the spelling `_PANE_INSET = 5` hid behind
    consts = {t.id: n.value.value
              for n in src.body if isinstance(n, ast.Assign)
              for t in n.targets
              if isinstance(t, ast.Name) and isinstance(n.value, ast.Constant)
              and isinstance(n.value.value, int) and not isinstance(n.value.value, bool)}
    out = []

    def value_of(arg):
        """The integer this argument is, or None if it is a token / an expression / not an int."""
        if isinstance(arg, ast.Constant) and isinstance(arg.value, int) \
                and not isinstance(arg.value, bool):
            return arg.value
        if isinstance(arg, ast.Name) and arg.id in consts:
            return consts[arg.id]
        return None

    def visit(node, owner):
        if isinstance(node, ast.ClassDef):
            owner = node.name
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = f"{owner}.{node.name}" if owner else node.name
        elif isinstance(node, ast.Assign) and owner is None:
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            owner = names[0] if names else owner
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _CALLS and node.args):
            vals = [value_of(a) for a in node.args]
            if all(v is not None for v in vals):   # a token read anywhere and the call is fine
                out.append((node.lineno, node.func.attr, tuple(vals), owner))
        for ch in ast.iter_child_nodes(node):
            visit(ch, owner)

    visit(src, None)
    return out


def test_no_studio_module_hand_picks_a_layout_dimension():
    """Check 2, and the PROGRESS METRIC for the phases after this one.

    Phase 1 owns theme.py and overlays.py; the view files migrate in the phases that redesign them,
    because moving a margin is a visual change and it belongs in the PR whose pixel proof covers
    that surface. So this lands with a real exemption list, each entry naming the constructor that
    owns the decision, and every later phase should delete some of it. The count is printed on
    every run so the direction is visible without reading the diff.

    THE BACKLOG IS EMPTY, and what emptied it was reading the last exemption's own prose. Six of
    the final eight were excused as "prose surfaces with their own typographic measure — a 20 px
    text column inset, an 18 px paragraph lead — off the scale and off it CONSISTENTLY". The
    consistency was the load-bearing half of that argument and it was false: the copy column was
    20/18/20/16, the Shortcuts card 12/10/12/12 and the export dialog 16/14/16/14 with a 10 px block
    gap — three different insets for one job, which is what a nudge looks like from the inside. The
    18 was not a paragraph lead either; it was a top inset, while the actual paragraph gap was a
    parameter passed as 8 from one card and 10 from the other.

    Nothing bought a PROSE_* scale in the end. The step a reading column wants is already declared —
    SPACE_XL is documented as "a page's own breathing room (empty states, dialog bodies)" — so a
    second system would have been a ninth spacing value bought to keep numbers nobody chose. What
    IS stated per surface is which kind of surface it is: the two copy cards take the reading inset,
    the Shortcuts reference and the export dialog take control spacing because they are a table and
    a form, and the loading card takes SPACE_L between its three groups because it is glanced at and
    clicked, not read.

    AND THEN THE WALKER GREW TWO EYES, so the empty set is not empty any more. It was reporting a
    file clean while that file hand-picked six dimensions: `setContentsMargins(_PANE_INSET, 0,
    _PANE_INSET, 0)` was skipped because a `Name` argument read like a token, and neither
    `setHandleWidth` nor `setMinimumWidth` was on the list at all. Resolving module-level int
    constants and adding those two calls is what makes "the backlog is empty" mean something — and
    it immediately surfaced seven call sites in FOUR files this lane does not own, so each is
    exempted BY NAME below rather than fixed in a PR whose pixel proof does not cover them.

    Six of the seven are the same shape and the shape is argued in the `_CALLS` preamble above: a
    DIALOG'S MEASURE (400 / 720 / 560 / 380 / 460 px) and a button's 88 px floor are extents, and
    the SPACE scale has nothing to say about them. They are listed here because the check cannot
    tell an extent from a gap, not because anyone thinks they are wrong. The seventh
    (`SPARK_HEIGHT = 96`) is a genuine off-scale literal, in the stats lane's file."""
    EXEMPT: set[tuple[str, str]] = {
        # --- extents, not gaps: a dialog's own measure (see the paragraph above). Not this lane's.
        ("app.py", "StudioWindow._ask_export_options"),        # 400 px export-dialog measure
        ("coaching_panel.py", "OpportunitiesDialog.__init__"),  # 720 px dialog measure
        ("coaching_panel.py", "OpportunitiesDialog._go_button"),  # 88 px button floor
        ("help_dialog.py", "ShortcutsDialog.__init__"),        # 560 px reading measure
        ("help_dialog.py", "AboutDialog.__init__"),            # 380 px card measure
        ("help_dialog.py", "PrivacyDialog.__init__"),          # 460 px reading measure
        # --- a real one, owned by the stats lane: SPARK_HEIGHT = 96, a sparkline's height.
        ("stats_panel.py", "StatsView.__init__"),
    }
    offenders = []
    total = onscale = 0
    for fn in sorted(os.listdir(_STUDIO)):
        if not fn.endswith(".py"):
            continue
        for lineno, call, vals, owner in _dimension_literals(os.path.join(_STUDIO, fn)):
            total += 1
            if all(v in _SPACE_OK for v in vals):
                onscale += 1
            elif (fn, owner) not in EXEMPT:
                offenders.append(f"{fn}:{lineno} {call}{vals} (in {owner})")
    assert not offenders, (
        "layout dimensions picked at the call site instead of taken from the scale — use "
        "theme.SPACE_* (or add an EXEMPT entry saying whose decision it is):\n  "
        + "\n  ".join(offenders))
    # The backlog may only shrink. A phase that migrates a surface deletes its entry; a phase that
    # adds one has to say so out loud, here, in prose. 11 after Phase 1-3, 9 after Phase 4, 8 after
    # 5, ZERO after Phase 6 — and 7 again the moment the walker could SEE two more calls and a
    # constant behind a name, which is the honest reading: the set was empty because the check was
    # short-sighted, not because the app had run out of hand-picked numbers. A ratchet, `<=` and
    # never `==`, so deleting one of these later cannot turn the build red. Every new off-scale
    # literal still has to be argued in this file's prose, or written as a derivation of the scale
    # the way theme.focus_pad, theme.pill_radius, widgets.space_at_least and
    # lap_table.GRID_TEXT_INSET already are.
    assert len(EXEMPT) <= 7, f"the exemption list GREW to {len(EXEMPT)}: {sorted(EXEMPT)}"
    print(f"test_no_studio_module_hand_picks_a_layout_dimension OK "
          f"({onscale}/{total} literal calls on the scale, {len(EXEMPT)} exempted surfaces)")


# ============================================================================ 3. the tokens
def test_the_spatial_tokens_are_self_consistent():
    """Check 3a. A scale is only a scale if it holds together: the size ladder is ordered, every
    spacing step is a whole multiple of the sub-step, the radii ascend, and the two derivation
    helpers round-trip. Cheap, and it is what stops a later "just bump CTRL_H to 30" from quietly
    putting a control below the pointer-target floor or above its own header."""
    assert theme.HIT_MIN <= theme.CTRL_H <= theme.TOOLBAR_H <= theme.PANEL_HDR_H, (
        "a control must clear the hit floor, fit its toolbar, and fit the header above it: "
        f"{theme.HIT_MIN} / {theme.CTRL_H} / {theme.TOOLBAR_H} / {theme.PANEL_HDR_H}")
    assert list(SPACE) == sorted(SPACE) and len(set(SPACE)) == len(SPACE), SPACE
    for v in SPACE:
        assert v % theme.SPACE_XXS == 0 and v > 0, f"SPACE step {v} is not a multiple of the sub-step"
    for v in SPACE[1:]:
        assert v % theme.SPACE_XS == 0, f"only the sub-step may sit off the 4px base: {v}"
    assert list(RADII) == sorted(RADII) and len(set(RADII)) == len(RADII), RADII
    assert theme.ICON_BTN.width() == theme.ICON_BTN.height() == theme.CTRL_H, (
        "an icon button is a square control at the control height", theme.ICON_BTN)
    assert theme.ICON_BTN.height() >= theme.HIT_MIN
    # ctrl_content_h(total, pad, border) is the number that PAINTS `total`, by definition.
    for total in (theme.CTRL_H, theme.TOOLBAR_H, theme.PANEL_HDR_H):
        for pad in (theme.SPACE_XXS, theme.SPACE_XS, theme.SPACE_S):
            for border in (0, theme.SPACE_XXS, 2 * theme.BORDER_PX):
                assert theme.ctrl_content_h(total, pad, border) + 2 * pad + border == total
    # focus_pad(v) under a FOCUS_RING_PX border occupies exactly what v does under BORDER_PX.
    for v in SPACE:
        assert theme.focus_pad(v) + theme.FOCUS_RING_PX == v + theme.BORDER_PX
    print(f"test_the_spatial_tokens_are_self_consistent OK "
          f"(SPACE {SPACE}, RADII {RADII}, CTRL_H {theme.CTRL_H})")


def test_the_type_scale_has_four_steps_and_four_roles():
    """Check 3b. It was 11 / 12 / 13 / 22 — three sizes inside two pixels of each other, so what
    actually separated a value from the label about it was colour, and the hierarchy disappeared in
    greyscale. It is 11 / 13 / 15 / 22 now, and EMPHASIS is the step stats_panel had already
    discovered it needed and defined privately."""
    from studio import stats_panel
    steps = sorted({theme.CAPTION, theme.BODY, theme.EMPHASIS, theme.HERO})
    assert steps == [11, 13, 15, 22], steps
    assert theme.CAPTION < theme.BODY < theme.EMPHASIS < theme.HERO
    # the small-caps chrome shares CAPTION's step; they are roles, not sizes of their own
    assert theme.PANEL_HEADER == theme.TABLE_HEADER == theme.CAPTION == 11
    assert theme.TABLE == theme.BODY
    # The promotion is FINISHED: the page-local alias is gone, and so is the page-local tile that
    # needed it. A second name for a scale step is how a scale acquires a fifth step.
    assert not hasattr(stats_panel, "TILE_VALUE_PT"), "the alias outlived the phase that owned it"
    from studio.widgets import Tile
    tile = Tile("best lap")      # bound, not inlined: a dropped Tile deletes its own QLabels
    assert tile.value.font().pixelSize() == theme.EMPHASIS
    assert tile.caption.font().pixelSize() == theme.CAPTION
    # no step may sit within 1px of another — that is the defect this scale was cut to fix
    assert all(b - a >= 2 for a, b in zip(steps, steps[1:], strict=False)), steps
    print(f"test_the_type_scale_has_four_steps_and_four_roles OK ({steps})")


def test_the_pointer_target_floor_has_one_home():
    """Check 3c. PBToast declared its own 24 px floor because it was the first widget to need one.
    The rule is app-wide, so the number lives with the other size tokens and the widget reads it —
    while KEEPING its name, which tests/test_pb_toast.py asserts against its own local 24."""
    from studio.overlays import PBToast
    assert PBToast.MIN_HIT_PX == theme.HIT_MIN == 24
    print("test_the_pointer_target_floor_has_one_home OK")


def test_a_button_a_combo_and_a_tab_really_paint_at_ctrl_h():
    """Check 3d — the LIVE one, and the only one that can catch a token Qt refuses to honour.

    CTRL_H is not reachable by choosing a padding: a QPushButton in this theme has a 16 px content
    height, so a scale-step vertical padding lands it on 24 or 32 and nothing between. The height
    is therefore DECLARED and the stylesheet number derived from it (see theme.ctrl_content_h) —
    which is exactly the kind of arithmetic that can be perfectly self-consistent and still paint
    something else, because Qt's stylesheet box model is not obliged to agree with it. So build
    the three real controls under the shipped theme and measure them.

    Shipped, these were 30 / 30 / 30 by coincidence of three DIFFERENT paddings (6px, 5px, 6px)."""
    from PySide6.QtWidgets import QComboBox, QPushButton, QTabBar, QWidget

    host = QWidget()
    btn = QPushButton("Reset sectors", host)
    combo = QComboBox(host)
    combo.addItems(["Line: Speed", "Line: Δ vs best"])
    tabs = QTabBar(host)
    for t in ("Laps", "Corners", "Stats", "Coaching"):
        tabs.addTab(t)
    host.show()
    for _ in range(4):
        _APP.processEvents()
    got = {"QPushButton": btn.sizeHint().height(), "QComboBox": combo.sizeHint().height(),
           "QTabBar": tabs.sizeHint().height()}
    host.hide()
    assert set(got.values()) == {theme.CTRL_H}, (
        f"every control in a header or toolbar must paint at CTRL_H={theme.CTRL_H}: {got}")
    print(f"test_a_button_a_combo_and_a_tab_really_paint_at_ctrl_h OK ({got})")


# ============================================================================ 4. the panels
def test_all_four_panel_headers_are_one_height():
    """Check 4 — THE measurement this design system was cut to move, on the REAL CentralView.

    Shipped, the four panels stood at 32 / 38 / 43 / 43. All four already went through ONE shared
    header builder, which is what made the defect so hard to see in the diff: the builder set
    margins and never a HEIGHT, so each bar came out as tall as whichever control it happened to
    hold — a QTabBar, a QPushButton, the 30 px hero #DiffBox. The spatial tokens alone narrowed the
    spread to 36 / 36 / 36 / 38 by landing every CONTROL on CTRL_H, but that is a coincidence of
    contents, not a declaration: adding one taller widget to any header would have moved it again.

    So: every PanelHeader in the view stands at exactly PANEL_HDR_H and every PanelToolbar at
    exactly TOOLBAR_H, at both shipped window sizes, and the count is pinned too — four headers
    (one per quadrant) and exactly two toolbars, because a panel with no controls does not get an
    empty control row.

    Measured on the production view over the deterministic synthetic session, not on widgets built
    for the test: a header is only the right height when it holds the app's real contents."""
    from test_central_view_realqt import _real_central_view

    from studio.widgets import PanelHeader, PanelToolbar

    for size in ((1440, 900), (1280, 800)):
        view = _real_central_view()[0]
        view.resize(*size)
        view.show()
        for _ in range(8):
            _APP.processEvents()
        panels = {"VIDEO": view._video_panel, "TABLE": view._table_panel,
                  "MAP": view._map_panel, "CHARTS": view._plots_panel}
        heights = {}
        for name, panel in panels.items():
            # The header is layout item 0 of every panel, by construction (CentralView._headered).
            header = panel.layout().itemAt(0).widget()
            assert isinstance(header, PanelHeader), (name, type(header).__name__)
            heights[name] = header.height()
        view.hide()
        assert set(heights.values()) == {theme.PANEL_HDR_H}, (
            f"the four panel headers must be ONE declared height "
            f"(PANEL_HDR_H={theme.PANEL_HDR_H}) at {size}: {heights}")
        # WHICH panels have a toolbar, not HOW MANY. This was `len(toolbars) == 2` — a count, in
        # a file whose other backlog numbers are one-directional ratchets, and it fails the same
        # way whether a fifth panel grows a control row (a design change worth arguing) or MAP
        # loses one (a regression). Neither is a direction, so this is not a ratchet to loosen: it
        # is a fact to state precisely. Asked as an identity it names the offender either way.
        # WHICH panels have a toolbar, not HOW MANY (see the note above). VIDEO joined MAP and
        # CHARTS when its transport became one: it is a row of five controls, and it was the only
        # control zone in the window that was not on a bar — three rows at 26/28/21 px on the
        # window canvas with a 0 px gutter, against six bars that agreed to the pixel.
        owners = {name for name, panel in panels.items() if panel.findChildren(PanelToolbar)}
        assert owners == {"MAP", "CHARTS", "VIDEO"}, (
            f"only MAP, CHARTS and VIDEO have controls, so only they get a toolbar: "
            f"{sorted(owners)}")
        toolbars = view.findChildren(PanelToolbar)
        assert len(toolbars) == len(owners), (
            f"a panel grew a SECOND toolbar: {len(toolbars)} rows across {sorted(owners)}")
        for t in toolbars:
            assert t.height() == theme.TOOLBAR_H, (t.height(), theme.TOOLBAR_H)
            # leading + controls: a toolbar may now hold a group on either side of its stretch, and
            # "every control in a toolbar shares one height" has to hold for all of them.
            for c in (*t.leading, *t.controls):
                assert c.height() == theme.CTRL_H, (
                    f"every control in a toolbar shares one height: {c!r} is {c.height()}")
    print(f"test_all_four_panel_headers_are_one_height OK "
          f"(4 headers @ {theme.PANEL_HDR_H}, 3 toolbars @ {theme.TOOLBAR_H})")


def _arrows(bar):
    """The tab bar's two scroll buttons as (visible, [rects]), whatever Qt calls them."""
    from PySide6.QtWidgets import QToolButton
    btns = sorted(bar.findChildren(QToolButton), key=lambda b: b.x())
    return [b for b in btns if b.isVisible()], [b.geometry() for b in btns]


def test_the_lap_panels_identity_is_never_squeezed_and_its_fallback_clears_the_hit_floor():
    """Check 4b — AN EXTENT, which is the other way a hand-picked dimension does damage.

    Check 2 above governs the space BETWEEN things and deliberately says nothing about the size OF
    one: 560 px of dialog measure or 220 px of busy bar are not on any spacing scale and should not
    be. The harm an extent can do is different, and this is it, in the one place the app had it.

    THE DEFECT. `CentralView._layout_panels` pinned the left column at `setMinimumWidth(280)`
    directly under a comment explaining, for the RIGHT column, exactly why that is the wrong
    instrument — "qSmartMinSize takes a widget's EXPLICIT minimum over its minimumSizeHint, so a
    number set here does not merge with what the children actually need, it REPLACES it, and any
    shortfall comes out of the children's glyphs" — and then asserting the left column was
    "unrelated: nothing in it is over-subscribed". It was over-subscribed by 12 px. The lap panel's
    honest minimum is 292 (SPACE_S + the tab bar's own 240 + SPACE_S + the 28 px ⛶ + SPACE_S), so at
    the floor the header handed a 240 px identity 228 px, Qt raised the QTabBar's two scroll arrows
    and put part of "Coaching" behind them. Measured on the shipped tree: two 21x28 arrows
    OVERLAPPING BY 11x28, both under the 24 px pointer floor, and the only route to a tab the
    reader could no longer see. Present identically before the design wave — this is older than any
    of it.

    So, two assertions, and they are deliberately different in kind:

      * THE MECHANISM. At every size the app can be driven to — including its own minimumSizeHint,
        and under a splitter drag that asks for a 1 px lap panel — the tab bar gets at least its
        sizeHint, every tab rect is inside it, and NO arrow is visible. Four tabs at ElideNone in a
        narrow panel is a real constraint; a panel narrower than its own contents was not one.
      * THE FALLBACK. Forced narrow anyway (a fifth tab, a translation, a font stack that resolves
        wider — the same reason widgets.budget_plot_gutters measures instead of choosing), each
        arrow clears HIT_MIN and the two overlap by no more than Qt's own declared
        PM_TabBar_ScrollButtonOverlap, which is the single pixel two adjacent buttons share. That is
        what theme's QTabBar::scroller rule buys: the metric came back 16 unstyled while the buttons
        painted 21."""
    from PySide6.QtWidgets import QStyle
    from test_central_view_realqt import _real_central_view

    view = _real_central_view()[0]
    view.show()
    for _ in range(8):
        _APP.processEvents()
    bar = view.tab_bar
    hint = view.minimumSizeHint()
    checked = 0
    for size in ((1440, 900), (1280, 800), (max(hint.width(), 1), max(hint.height(), 1))):
        view.resize(*size)
        for _ in range(8):
            _APP.processEvents()
        # ...and then ASK for the panel to be narrower than anything a drag could reach. A
        # QSplitter clamps at its section's minimum, so this measures the floor itself rather than
        # a size a test happened to pick.
        total = sum(view._main_splitter.sizes())
        for ask in (total // 3, 1):
            view._main_splitter.setSizes([ask, total - ask])
            for _ in range(6):
                _APP.processEvents()
            checked += 1
            vis, _rects = _arrows(bar)
            assert not vis, (
                f"at {size} with the lap column asked down to {ask} px the tab bar is "
                f"{bar.width()} px for a {bar.sizeHint().width()} px identity, so Qt raised "
                f"{len(vis)} scroll arrow(s) — the panel is narrower than its own contents")
            assert bar.width() >= bar.sizeHint().width(), (
                size, ask, bar.width(), bar.sizeHint().width())
            for i in range(bar.count()):
                r = bar.tabRect(i)
                assert r.left() >= 0 and r.right() <= bar.width(), (
                    f"at {size}/{ask} tab {i} ({bar.tabText(i)!r}) paints {r} outside a "
                    f"{bar.width()} px bar")
        view._main_splitter.setSizes([total // 2, total - total // 2])
        for _ in range(4):
            _APP.processEvents()

    # THE FALLBACK, forced. Resize the bar itself: its parent's layout is what normally refuses
    # this, which is the point — the question here is only what the arrows look like once Qt has
    # decided to draw them.
    bar.resize(bar.sizeHint().width() // 2, bar.height())
    for _ in range(6):
        _APP.processEvents()
    vis, rects = _arrows(bar)
    assert len(vis) == 2, f"a half-width tab bar must show both arrows, got {len(vis)}"
    seam = bar.style().pixelMetric(QStyle.PM_TabBar_ScrollButtonOverlap, None, bar)
    for r in rects:
        assert r.width() >= theme.HIT_MIN and r.height() >= theme.HIT_MIN, (
            f"a tab-bar scroll arrow is {r.width()}x{r.height()}, under the "
            f"HIT_MIN={theme.HIT_MIN} pointer floor")
    hit = rects[0].intersected(rects[1])
    assert hit.width() <= seam, (
        f"the two scroll arrows overlap by {hit.width()}x{hit.height()} px, more than the "
        f"{seam} px Qt's own PM_TabBar_ScrollButtonOverlap makes two adjacent buttons share — "
        f"so one of them is partly un-clickable")
    view.hide()
    print(f"test_the_lap_panels_identity_is_never_squeezed_and_its_fallback_clears_the_hit_floor "
          f"OK ({checked} squeezes, arrows {rects[0].width()}x{rects[0].height()} sharing "
          f"{hit.width()} px)")


# ============================================================================ 5. the charts
def test_no_chart_axis_title_is_painted_outside_its_chart():
    """Check 5 — the charts panel's own version of "declared, not emergent", measured on pixels.

    The panel shipped with `speed (km/h)` and `Δ to ideal (s)` sliced down their left edge and
    `distance (m)` cut through its descenders. Not under pressure: 2 px and 5.8 px at 1440x900, the
    SAME 2 px and 5.8 px at 1280x800, and again at the window's own 845x414 minimum — because the
    cause is arithmetic, not a squeeze. pyqtgraph reserves `0.8 *` a title's bounding height and
    then places the title `nudge = 5` px further OUT than it reserved, and this app's 2 px
    focus-ring border on every QGraphicsView leaves the scene's last rows outside the viewport
    (studio/widgets.py::budget_plot_gutters carries the full account).

    So: measured in SCENE coordinates against what the viewport can SHOW, on the real CentralView
    under the shipped theme — the border is a themed rule, so an unthemed app cannot see half of
    this defect — at both shipped window sizes AND at the app's own minimum, which is where a
    reserve chosen by eye at 1440x900 would be expected to fail first.

    The gutter it reserves is MEASURED, so this also pins that the measurement stayed on the
    spatial scale: a derived number that quietly stops being a step is a fifth kind of spacing."""
    from test_central_view_realqt import _real_central_view

    view = _real_central_view()[0]
    view.show()
    for _ in range(8):
        _APP.processEvents()
    hint = view.minimumSizeHint()
    # EVERY EDGE AT EVERY SIZE, INCLUDING THE MINIMUM. There used to be an exemption here: the TOP
    # edge was asserted only at the two shipped sizes, on the stated grounds that the minimum "is a
    # size the app cannot be driven at". That was false — `minimumSizeHint()` is exactly what Qt
    # honours a resize to, this test asks the view for it and then resizes to it — and the thing
    # the exemption waved through was not a near-miss: at 845x414 the two ROTATED left-axis titles
    # were centred on axes shorter than themselves and painted ON TOP OF EACH OTHER, sharing
    # 24 x 49.5 px of one gutter, with 26.5 px of `speed (km/h)` above the viewport entirely.
    # studio/widgets.py::budget_plot_min_height fixes that by declaring the height two stacked
    # labelled charts need, which is why the minimum is now ~528 px tall rather than 414 — so the
    # honest form of this check is simply to assert everything everywhere and let the minimum be
    # whatever the charts can actually be named in.
    sizes = [(1440, 900), (1280, 800), (max(hint.width(), 1), max(hint.height(), 1))]
    checked = 0
    for size in sizes:
        view.resize(*size)
        for _ in range(8):
            _APP.processEvents()
        plots = view.plots
        viewport = plots.glw.viewport()
        for plot, side in ((plots.p_speed, "left"), (plots.p_delta, "left"),
                           (plots.p_delta, "bottom")):
            axis = plot.getAxis(side)
            assert axis.isVisible() and axis.labelText and axis.label.isVisible(), (size, side)
            checked += 1
            r = axis.label.mapRectToScene(axis.label.boundingRect())
            over = {"left": -r.left(), "right": r.right() - viewport.width(),
                    "bottom": r.bottom() - viewport.height(), "top": -r.top()}
            bad = {k: round(px, 1) for k, px in over.items() if px > 0.5}
            assert not bad, (
                f"at {size} the {side} axis title {axis.labelText!r} is painted outside the "
                f"chart by {bad} — the reader loses the name of the axis, not a decoration")
        # ...and no title may be painted on ANOTHER title. Staying inside the viewport is not the
        # same contract: two rotated labels can both be fully inside it and still overprint, which
        # is exactly what the minimum used to do — `Δ to ideal (` with `peed` struck through it.
        boxes = [(p, p.getAxis("left").label.mapRectToScene(
            p.getAxis("left").label.boundingRect())) for p in (plots.p_speed, plots.p_delta)]
        for i, (pa, ra) in enumerate(boxes):
            for pb, rb in boxes[i + 1:]:
                hit = ra.intersected(rb)
                assert hit.isEmpty(), (
                    f"at {size} {pa.getAxis('left').labelText!r} and "
                    f"{pb.getAxis('left').labelText!r} are painted over each other in "
                    f"{round(hit.width(), 1)} x {round(hit.height(), 1)} px of one gutter")
        margins = plots.glw.ci.layout.getContentsMargins()
        assert all(round(m) in _SPACE_OK for m in margins), (
            f"the measured axis gutter left the spatial scale at {size}: {margins}")
    view.hide()
    assert checked == 3 * len(sizes), checked
    print(f"test_no_chart_axis_title_is_painted_outside_its_chart OK "
          f"({checked} titles over {len(sizes)} sizes incl. the {sizes[-1]} minimum)")


# ============================================================================ 6. the tables
def _h_side(align, default):
    """The HORIZONTAL half of a Qt alignment, as a name. 0 means "unset", and what that means
    differs by surface — a header falls back to its view's defaultAlignment, a cell to left — so
    the caller passes the fallback rather than this guessing."""
    a = int(align)
    for name, flag in (("right", Qt.AlignRight), ("centre", Qt.AlignHCenter),
                       ("left", Qt.AlignLeft)):
        if a & int(flag):
            return name
    return default


def _painted_headers(table):
    """Each header as the user SEES it: elided against the style's own SE_HeaderLabel rect.

    The rect, not the section width and not an estimate of the QSS padding. A header that reads
    "Δ…" does so because the label box is 28 px inside a 44 px section, and the 16 px difference is
    a stylesheet padding plus a style metric — the two things a hand-written estimate gets wrong.
    Returns [(column, full text, painted text)] for the columns that carry a label."""
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QStyle, QStyleOptionHeader

    hdr = table.horizontalHeader()
    fm = QFontMetrics(hdr.font())
    out = []
    for c in range(table.columnCount()):
        item = table.horizontalHeaderItem(c)
        text = item.text() if item is not None else ""
        if not text:
            continue
        opt = QStyleOptionHeader()
        hdr.initStyleOption(opt)
        opt.section = c
        opt.rect = QRect(0, 0, hdr.sectionSize(c), max(hdr.height(), 1))
        label = hdr.style().subElementRect(QStyle.SE_HeaderLabel, opt, hdr)
        out.append((c, text, fm.elidedText(text, Qt.ElideRight, label.width())))
    return out


def test_no_table_header_elides_away_its_own_name():
    """Check 6b. A header may abbreviate. It may not stop NAMING the column.

    THE DEFECT, and why the guard that already existed could not see it. #163 fixed "Δ best" and
    "Δ apex" both painting "Δ …" at 1280x800 by closing them up, so their elisions read "Δb…" and
    "Δa…" — different stems. Its guard sweeps LABEL WIDTHS against the font: given a width, do the
    two strings elide differently? That is a property of the two strings, and it stayed true. What
    it cannot see is the width the table actually HANDS the header, which is the output of a
    proportional squeeze across eight columns — and once the design wave moved 2 px of table
    viewport into chrome, the division came out 1 px differently and the seconds column started
    painting a bare "Δ…" beside a km/h column reading "Δa…". Both "distinct" and both, for the
    reader, a delta of nothing in particular.

    So this measures the RENDERED SECTION on the real view, at four sizes, and asks the thing the
    reader asks: is there a letter left? HEADER_STEM_CHARS of the label's own glyphs, which is the
    same two-character threshold #163's guard uses to decide when its own check applies — and, as a
    consequence rather than an assumption, no two headers in one table painting the same string.

    WHICH SIZES ARE EXEMPT MOVED, because the budget's yield did (QA W14-02, lap_table.header_floors
    cases 1-3). This used to skip any table whose columns no longer FIT their viewport, on the
    grounds that it was "out of pixels altogether". That was the exemption swallowing the defect:
    the corner table overflows its viewport at every window width from the app's own 973 px minimum
    up to 1146 px — 174 px of the range, the part a small display actually sits in — and the
    all-or-nothing yield painted BOTH Δ headers as a bare "…" through all of it, unchecked, with a
    horizontal scrollbar already up so the yield was buying nothing. The budget grants every stem
    there now, so the overflow case is CHECKED rather than excused.

    What is exempt instead is the narrow band in between, where the columns fit but the stems do
    not all fit with them, and header_floors spends the slack on the ambiguous headers first. That
    is a real "there are no pixels" case rather than a category, and it is detected here the same
    way the budget decides it: per column, against what that header ASKED FOR. A section at least
    as wide as the ask must show the stem — that is the promise, and the one pixel by which it was
    false is what this sweep now catches."""
    from PySide6.QtGui import QFontMetrics
    from test_central_view_realqt import _real_central_view

    from studio.lap_table import _header_pad_px, header_stem_px

    # Two, spelled out here rather than imported from the widget it governs: this is the reader's
    # threshold, not an implementation's, and it is the same two characters #163's own guard uses
    # to decide when its check applies. A test that reads its expectation off the code under test
    # agrees with that code by construction.
    HEADER_STEM_CHARS = 2

    view = _real_central_view()[0]
    view.show()
    offenders, checked, skipped = [], 0, []
    # The last two sizes are not decoration: 1100x700 is where the corner table's squeeze bites
    # hardest, and 1200x800 is where the columns were granted EXACTLY the stem width they asked for
    # and the seconds column still painted a bare "Δ…" — the one-pixel defect, at a size a user has.
    for size in ((1440, 900), (1280, 800), (1200, 800), (1100, 700)):
        view.resize(*size)
        for page in range(view.tab_bar.count()):
            view.tab_bar.setCurrentIndex(page)
            for _ in range(6):
                _APP.processEvents()
        for name, table in (("LAPS", view.table.table),
                            ("CORNERS", view.corner_table.table)):
            hdr = table.horizontalHeader()
            fm = QFontMetrics(hdr.font())
            pad = _header_pad_px(hdr)
            painted = _painted_headers(table)
            granted = {c: hdr.sectionSize(c) for c, _t, _s in painted}
            asked = {c: header_stem_px(fm, text) + pad for c, text, _s in painted}
            short = [c for c, text, shown in painted
                     if len(shown.rstrip("…")) < min(HEADER_STEM_CHARS, len(text))]
            # The stated exemption: a column that was NOT given the width its header asked for is
            # in the partial band, and the budget already says so out loud.
            if short and all(granted[c] < asked[c] for c in short):
                skipped.append(f"{name}@{size}(short by "
                               + ",".join(f"{asked[c] - granted[c]}px" for c in short) + ")")
                continue
            for c, text, shown in painted:
                checked += 1
                stem = shown.rstrip("…")
                if len(stem) < min(HEADER_STEM_CHARS, len(text)):
                    offenders.append(
                        f"{name}@{size} col {c}: {text!r} paints {shown!r} — "
                        f"{len(stem)} of its own glyphs, so the column is unnamed")
            seen = {}
            for c, text, shown in painted:
                twin = seen.get(shown)
                if twin is not None:
                    offenders.append(
                        f"{name}@{size} col {twin[0]} ({twin[1]!r}) and col {c} ({text!r}) "
                        f"both paint {shown!r} — two columns, one label")
                seen[shown] = (c, text)
    view.hide()
    assert not offenders, (
        "table headers that elided away the column they name:\n  " + "\n  ".join(offenders))
    assert checked >= 40, f"only {checked} headers measured — the sweep stopped seeing tables"
    print(f"test_no_table_header_elides_away_its_own_name OK ({checked} rendered headers"
          + (f"; {len(skipped)} out of room: {skipped}" if skipped else "") + ")")


def test_no_table_header_floats_off_its_data():
    """Check 6. A HEADER IS A LABEL FOR THE COLUMN UNDER IT, and Qt's default centres every one.

    This rule was already written down, in exactly one place: the coaching table aligns each header
    to its own column and tests/test_coaching_panel_layout.py:223 pins it with "a centred header
    floats off its data". The lap and corner tables did not, so the app disagreed with itself.
    Measured from the pixels of the shipped lap panel at 1440x900, the header's ink ended 39 / 40 /
    42 px short of its section while the digits it names ended 13 / 12 / 12 px short — the label
    floating 26-30 px to the left of its own numbers, and further on every extra pixel of column
    width (a maximized lap panel gives each data column MAX_DATA_COL_PX).

    THIS IS WHERE THE RULE LIVES, and that is the point of putting it here rather than in a fourth
    private helper. The four tables build their headers four different ways — a per-column dict
    keyed by header TEXT (coaching), a numeric-column boundary (laps, corners), nothing at all
    (library) — and no shared function could have caught the ones that never called it. What they
    can share is a MEASUREMENT, over the real widgets, that enumerates every table the app ships.

    Anything with cells and a header is in: a fifth table has to come here and either pass or say
    why not."""
    from PySide6.QtWidgets import QHeaderView
    from test_central_view_realqt import _real_central_view
    from test_coaching_panel_layout import _panel as _coaching_panel
    from test_coaching_panel_layout import _rows as _coaching_rows

    from studio.library_dialog import LibraryDialog

    # (table, why it is exempt) — prose, in the house idiom, and the list may only shrink.
    EXEMPT = {
        # The Library dialog's NUMBERS are not right-aligned either: "Best lap" and "Theoretical"
        # are lap times rendered as left-aligned text (studio/library_dialog.py::_fill_rows sets no
        # alignment at all), so its headers do not float off their data — they sit centred over
        # columns that are themselves unaligned. Making that table read like the other three is a
        # CELL change first and a header change second, in a surface this phase does not touch. It
        # is listed here, rather than left out, so the decision is visible and the next phase to
        # open that file inherits the check.
        "LIBRARY",
    }
    view = _real_central_view()[0]
    view.resize(1440, 900)
    view.show()
    # Visit every page: the Corners and Coaching tables fill on the tab switch, and a table with no
    # rows has no cell to compare a header against — i.e. it would pass by not being looked at.
    for page in range(view.tab_bar.count()):
        view.tab_bar.setCurrentIndex(page)
        for _ in range(6):
            _APP.processEvents()
    view.tab_bar.setCurrentIndex(0)
    for _ in range(8):
        _APP.processEvents()
    # The Coaching page needs a ranked model to have rows at all, and the two-lap synthetic session
    # has none — so it comes from the fixture in the test that already owns this contract for it
    # (test_coaching_panel_layout::test_every_header_sits_over_its_own_column), which is the point:
    # the rule is asserted once, over every table, in one place.
    coach = _coaching_panel(_coaching_rows(6), (1200, 800))
    dlg = LibraryDialog({"entries": [
        {"track": "Daytona MK", "date": "2026-08-12", "best": 68.42, "theoretical": 67.9,
         "paths": ["/tmp/a.MP4"], "verified": True}]}, open_recording=lambda _p: None)
    tables = {"LAPS": view.table.table, "CORNERS": view.corner_table.table,
              "COACHING": coach.table, "LIBRARY": dlg.table}
    offenders, checked = [], {}
    for name, table in tables.items():
        checked[name] = 0
        default = _h_side(table.horizontalHeader().defaultAlignment(), "left")
        for c in range(table.columnCount()):
            item = table.horizontalHeaderItem(c)
            cell = next((table.item(r, c) for r in range(table.rowCount())
                         if table.item(r, c) is not None), None)
            # A column with no header text or no cells labels nothing (the lap grid's blank
            # trailing spacer is exactly this) — there is no alignment to agree with.
            if item is None or not item.text() or cell is None:
                continue
            checked[name] += 1
            want = _h_side(cell.textAlignment(), "left")
            got = _h_side(item.textAlignment(), default)
            if (got != want or got == "centre") and name not in EXEMPT:
                offenders.append(
                    f"{name} col {c} {item.text()!r}: header is {got}, its cells are {want}"
                    + ("  (a centred header floats off its data)" if got == "centre" else ""))
    view.hide()
    coach.hide()
    dlg.deleteLater()
    assert not offenders, (
        "table headers that do not sit over the column they name:\n  " + "\n  ".join(offenders))
    assert min(checked.values()) >= 3 and sum(checked.values()) >= 20, (
        f"every table must contribute labelled columns with cells under them: {checked}")
    # ...and the rule is only worth having if the app's headers CAN be centred: prove the default
    # this fights is still Qt's, so a future Qt that changed it does not silently pass this test.
    probe = QHeaderView(Qt.Horizontal)
    assert _h_side(probe.defaultAlignment(), "left") == "centre", probe.defaultAlignment()
    print(f"test_no_table_header_floats_off_its_data OK ({sum(checked.values())} labelled "
          f"columns over {len(tables)} tables {checked}, {len(EXEMPT)} exempt)")


def _run_all():
    test_every_stylesheet_dimension_is_on_the_scale()
    test_the_focus_ring_is_paid_for_out_of_the_padding_not_the_box()
    test_no_studio_module_hand_picks_a_layout_dimension()
    test_the_spatial_tokens_are_self_consistent()
    test_the_type_scale_has_four_steps_and_four_roles()
    test_the_pointer_target_floor_has_one_home()
    test_a_button_a_combo_and_a_tab_really_paint_at_ctrl_h()
    test_all_four_panel_headers_are_one_height()
    test_the_lap_panels_identity_is_never_squeezed_and_its_fallback_clears_the_hit_floor()
    test_no_chart_axis_title_is_painted_outside_its_chart()
    test_no_table_header_elides_away_its_own_name()
    test_no_table_header_floats_off_its_data()
    print("\nAll design-system (spatial + type scale) tests passed.")


if __name__ == "__main__":
    _run_all()
