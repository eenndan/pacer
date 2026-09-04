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

NOT asserted here yet: that the four PANEL HEADERS share a height. They do not — 32 / 38 / 43 / 43
on the shipped app, because nothing declares one and the height is emergent from whichever control
happens to be tallest. PANEL_HDR_H exists for the phase that fixes that; the assertion belongs
with it.

Offscreen Qt, no telemetry file, no pacer.  Run: python tests/test_design_system.py
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
                if px[0] not in RADII:
                    offenders.append(f"{sel} {{ {prop}: {val} }} — not a RADIUS_* step")
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
_CALLS = ("setContentsMargins", "setSpacing", "setFixedHeight", "setFixedSize")


def _dimension_literals(path):
    """EVERY literal-argument layout-dimension call in one source file, as
    (lineno, call, values, owner) — where `owner` is `Class.method`, or the bare function, or the
    module-level name being bound.

    The same walker shape as tests/test_contrast.py:_hue_reads, and for the same reason: an
    exemption should name the DECISION that made the value (a widget's constructor), not a line
    number that moves the moment someone adds an import."""
    out = []

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
            vals = [a.value for a in node.args
                    if isinstance(a, ast.Constant) and isinstance(a.value, int)]
            if len(vals) == len(node.args):        # all-literal calls only; a token read is fine
                out.append((node.lineno, node.func.attr, tuple(vals), owner))
        for ch in ast.iter_child_nodes(node):
            visit(ch, owner)

    visit(ast.parse(open(path, encoding="utf-8").read(), path), None)
    return out


def test_no_studio_module_hand_picks_a_layout_dimension():
    """Check 2, and the PROGRESS METRIC for the phases after this one.

    Phase 1 owns theme.py and overlays.py; the view files migrate in the phases that redesign them,
    because moving a margin is a visual change and it belongs in the PR whose pixel proof covers
    that surface. So this lands with a real exemption list, each entry naming the constructor that
    owns the decision, and every later phase should delete some of it. The count is printed on
    every run so the direction is visible without reading the diff."""
    EXEMPT = {
        # ---- Phase 2 (header/toolbar) + Phase 3 (control vocabulary) ------------------------
        # The corner table's two header strips and the excluded-lap strip are hand-inset to sit
        # flush with the lap table's own column padding — a 10 px gutter chosen against a
        # QHeaderView padding that this PR just moved onto the scale. They are re-measured, not
        # re-typed, when the panel gets its PanelHeader/PanelToolbar.
        ("lap_table.py", "CornerTable.__init__"),
        ("lap_table.py", "LapTable._build_excluded_strip"),
        # ---- Phase 4 (the two weak surfaces) ------------------------------------------------
        # The Stats page's tile grid and its 1 px value-over-caption gap. The 1 px is the single
        # tightest gap in the app and it is deliberate — the tile is ONE thing — but it is below
        # even SPACE_XXS, so it is a decision to re-take with the tile, not to bless from here.
        # `_Tile` is also the widget Phase 4 promotes into widgets.py, so it moves anyway.
        ("stats_panel.py", "_Tile.__init__"), ("stats_panel.py", "StatsView.__init__"),
        # The coaching PhaseBar: a 6/4 inset, a 2 px bar-to-numbers gap (SPACE_XXS doing exactly
        # its job) and a 1 px gap BETWEEN the three bar segments — which is not spacing at all but
        # a hairline separator, i.e. BORDER_PX wearing a layout's clothes. Re-taken with the bar.
        ("coaching_panel.py", "PhaseBar.__init__"),
        # ---- not owned by any planned phase -------------------------------------------------
        # The three Help/About/Privacy dialogs and the export-options dialog are prose surfaces
        # with their own typographic measure (a 20 px text column inset, an 18 px paragraph lead)
        # rather than instances of the app's control layout. They are off the scale and they are
        # off it consistently; snapping them blind would reflow four dialogs for no stated gain,
        # so they wait for a phase that actually looks at them.
        ("help_dialog.py", "ShortcutsDialog.__init__"), ("help_dialog.py", "AboutDialog.__init__"),
        ("help_dialog.py", "PrivacyDialog.__init__"), ("help_dialog.py", "_copy_column"),
        ("app.py", "StudioWindow._ask_export_options"),
        # The loading placeholder's 18 px lead between title and busy bar — the one surface a user
        # sees for a few seconds and never interacts with; it moves with the dialogs above.
        ("app.py", "StudioWindow._show_loading_placeholder"),
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
    # adds one has to say so out loud, here, in prose.
    assert len(EXEMPT) <= 11, f"the exemption list GREW to {len(EXEMPT)}"
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
    # the promotion, still an alias so the call sites move in one deliberate change
    assert stats_panel.TILE_VALUE_PT == theme.EMPHASIS
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


def _run_all():
    test_every_stylesheet_dimension_is_on_the_scale()
    test_the_focus_ring_is_paid_for_out_of_the_padding_not_the_box()
    test_no_studio_module_hand_picks_a_layout_dimension()
    test_the_spatial_tokens_are_self_consistent()
    test_the_type_scale_has_four_steps_and_four_roles()
    test_the_pointer_target_floor_has_one_home()
    test_a_button_a_combo_and_a_tab_really_paint_at_ctrl_h()
    print("\nAll design-system (spatial + type scale) tests passed.")


if __name__ == "__main__":
    _run_all()
