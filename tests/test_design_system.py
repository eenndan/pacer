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

from PySide6.QtCore import Qt  # noqa: E402

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
        # (Phase 5 deleted `lap_table.CornerTable.__init__`. Its two caption strips were inset by a
        # hand-written 10 px to start on the same pixel as the grid's first column of text — which
        # turned out not to be a nudge at all but FOCUS_RING_PX + SPACE_S, the table's reserved
        # ring plus the QSS cell padding. It is written as that sum now, so the number follows both
        # halves instead of restating one value they happen to add up to.)
        # The excluded-lap strip is the same inset with a 6 px lead above it that IS a chosen gap,
        # off the scale, on a surface this phase has no measurement for. It moves with whichever
        # phase looks at that strip.
        ("lap_table.py", "LapTable._build_excluded_strip"),
        # (Phase 4 deleted its two — `stats_panel._Tile.__init__` and `StatsView.__init__`. The
        # tile is `widgets.Tile` now and its value-to-caption gap is SPACE_XXS, the sub-step that
        # exists for exactly that job; the page's 6 px block gap is SPACE_XS.)
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
    # adds one has to say so out loud, here, in prose. 11 after Phase 1-3, 9 after Phase 4, 8 after 5.
    assert len(EXEMPT) <= 8, f"the exemption list GREW to {len(EXEMPT)}"
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
        toolbars = [t for t in view.findChildren(PanelToolbar)]
        assert len(toolbars) == 2, (
            f"only MAP and CHARTS have controls, so only they get a toolbar: {len(toolbars)}")
        for t in toolbars:
            assert t.height() == theme.TOOLBAR_H, (t.height(), theme.TOOLBAR_H)
            for c in t.controls:
                assert c.height() == theme.CTRL_H, (
                    f"every control in a toolbar shares one height: {c!r} is {c.height()}")
    print(f"test_all_four_panel_headers_are_one_height OK "
          f"(4 headers @ {theme.PANEL_HDR_H}, 2 toolbars @ {theme.TOOLBAR_H})")


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
    SHIPPED = [(1440, 900), (1280, 800)]
    hint = view.minimumSizeHint()
    sizes = [*SHIPPED, (max(hint.width(), 1), max(hint.height(), 1))]
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
                    "bottom": r.bottom() - viewport.height()}
            # The TOP edge is deliberately not in that dict, and only at the window's own minimum
            # does the distinction matter. A left-axis title is ROTATED, so its length is spent
            # along the axis and pyqtgraph centres it: once the axis is shorter than the title
            # (the 845x368 minimum leaves the speed plot ~67 px against a 93 px `speed (km/h)`)
            # it overflows both ends at once. That is a title-vs-axis LENGTH problem, not a
            # gutter, and no margin can fix it — it is out of this phase's scope and it is a size
            # the app cannot be driven at. The gutters are asserted at every size, including there.
            if tuple(size) in SHIPPED:
                over["top"] = -r.top()
            bad = {k: round(px, 1) for k, px in over.items() if px > 0.5}
            assert not bad, (
                f"at {size} the {side} axis title {axis.labelText!r} is painted outside the "
                f"chart by {bad} — the reader loses the name of the axis, not a decoration")
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
    test_no_chart_axis_title_is_painted_outside_its_chart()
    test_no_table_header_floats_off_its_data()
    print("\nAll design-system (spatial + type scale) tests passed.")


if __name__ == "__main__":
    _run_all()
