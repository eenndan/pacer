"""The CONTROL-VOCABULARY guard — the third sibling of tests/test_contrast.py (colour) and
tests/test_design_system.py (dimension).

WHY THIS FILE EXISTS. The app had a locked colour system and, after the spatial phase, a locked
scale — and it still styled itself from thirty-four `setStyleSheet` strings scattered across nine
view files. Fourteen of them were the SAME line: `color: <text_dim>` on a label, re-typed once per
surface. That is not a stylesheet, it is a habit, and it has already cost the app real defects:

  * the export dialog's hint quietly used C.text_muted — the 3.17:1 token the colour contract
    reserves for DISABLED chrome — because nobody reviewing one line of CSS in one dialog could see
    the rule it broke;
  * the Shortcuts reference re-typed the whole mono font stack by hand, a literal copy of
    theme.MONO_STACK that could never be told it had drifted;
  * the lap table's excluded strip re-spelled PROVISIONAL_COLOR instead of reading it;
  * four widgets carried an `objectName` — the app's own signal that "this thing has a theme rule"
    — with NO rule anywhere, so the name promised styling the theme never provided.

So: four checks. Each has an EXEMPT set of `(file, owner)` tuples with prose saying whose decision
it is, in the house idiom (tests/test_contrast.py:_hue_reads, tests/test_design_system.py).

  1. NO MODULE STYLES ITSELF FROM A STRING, except the handful of PER-DATUM colour merges a
     stylesheet genuinely cannot express — a Δ that changes hue every tick, a bar segment whose
     fill IS the datum, a palette-accessor colour that must resolve after the QSS was built.
  2. EVERY objectName AND role HAS A RULE. The name is a claim; this checks it is true.
  3. THE ICON BUTTONS ARE ONE FAMILY, measured on the real view — two families shipped (26x24 and
     32x30, both of which the stylesheet then quietly stood at 28 high anyway).
  4. THE TOGGLES GO THROUGH ONE FACTORY. `setCheckable(True)` on a button belongs to
     widgets.ToggleButton; seven call sites re-implemented it, and six of the seven disagreed with
     each other about height, off-tint or binding time.

Offscreen Qt.  Checks 1-2 and 4 are static; check 3 builds the production view over the
deterministic synthetic session.  Run: python tests/test_inline_styles.py
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


def _calls(path, name):
    """Every `<x>.<name>(...)` call in one source file as (lineno, node, owner), where `owner` is
    `Class.method` / the bare function / the module-level name being bound.

    The walker shape tests/test_contrast.py:_hue_reads and tests/test_design_system.py both use, and
    for the same reason: an exemption should name the DECISION that made the call, not a line number
    that moves the moment someone adds an import."""
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
                and node.func.attr == name):
            out.append((node.lineno, node, owner))
        for ch in ast.iter_child_nodes(node):
            visit(ch, owner)

    visit(ast.parse(open(path, encoding="utf-8").read(), path), None)
    return out


def _modules():
    return [fn for fn in sorted(os.listdir(_STUDIO)) if fn.endswith(".py")]


# ============================================================================ 1. the strings
def test_no_module_styles_itself_from_a_string():
    """Check 1, and the metric this phase is measured by: 34 inline `setStyleSheet` sites -> 7.

    Every survivor is a PER-DATUM colour, i.e. a value that changes with the data and therefore
    cannot live in a stylesheet built once at startup. Anything that is the same on every recording
    is a ROLE now (see the labels-and-roles section of theme.py)."""
    EXEMPT = {
        # ---- per-tick Δ colour: ahead / behind / neutral, recomputed as the video plays. Both
        # write a QUALIFIED selector (`QLabel#DiffBox { … }`) over the widget's own themed rule, and
        # both are guarded so a stable readout costs no re-parse.
        ("central_view.py", "CentralView._update_diff_box"),
        ("video_view.py", "_PaneCell.set_badge"),
        # ---- the status bar's reference chip: a caveat TINT (PROVISIONAL_COLOR) applied when the
        # reference was matched by GPS location rather than by a confirmed track name. Per-session
        # rather than per-tick, but the same shape: a merge over the chip role, cleared by an empty
        # sheet when the caveat does not apply.
        ("app.py", "StudioWindow._update_reference_status"),
        # ---- the coaching PhaseBar: the three segment FILLS and the three numbers under them are
        # the datum (entry/mid/exit Δt, coloured by which third is losing), computed per row.
        ("coaching_panel.py", "PhaseBar.__init__"),
        # ---- the same row's "net" line: ahead_colour() is a palette ACCESSOR and the stylesheet is
        # built once at startup, so a QSS rule here would freeze this label in the standard green
        # while every other ahead/behind surface followed the colour-blind flip. The muted default
        # IS a role; only the ahead case merges.
        ("coaching_panel.py", "_row_face"),
        # ---- NOT THIS PHASE'S. stats_panel is Phase 4's surface, and this call carries a
        # load-bearing ORDERING (the sheet must be set before setFont or the repolish drops the
        # italic bit) that a mechanical migration would be very likely to break. Left exactly as it
        # is, deliberately; it is the one exemption here that is a deferral rather than a decision.
        ("stats_panel.py", "StatsView._set_target_tile"),
    }
    offenders = []
    total = 0
    for fn in _modules():
        if fn == "theme.py":
            continue                      # the stylesheet's own home
        for lineno, _node, owner in _calls(os.path.join(_STUDIO, fn), "setStyleSheet"):
            total += 1
            if (fn, owner) not in EXEMPT:
                offenders.append(f"{fn}:{lineno} setStyleSheet (in {owner})")
    assert not offenders, (
        "widgets styled from a string instead of from a theme role — put the treatment in "
        "theme.py's stylesheet and set a `role`/objectName here (or add an EXEMPT entry saying "
        "why this colour cannot be known until the data is):\n  " + "\n  ".join(offenders))
    # SEVEN calls across SIX owners: PhaseBar.__init__ makes two (the segment fills and the numbers
    # under them are two loops over the same datum). The number is pinned so that "one more little
    # stylesheet" has to come here and argue for itself — the count is the migration's whole point.
    assert total == 7, (
        f"{total} inline setStyleSheet calls (was 34, is 7 by design): the list above is the "
        f"complete set, so a change to this number is a decision, not a detail")
    print(f"test_no_module_styles_itself_from_a_string OK ({total} calls in "
          f"{len(EXEMPT)} owners, all per-datum)")


def test_no_bare_colour_declaration_creeps_back_in():
    """Check 1b, aimed at the exact line that was copied fourteen times.

    A `color:` in a Python string in a view file is the shape of the defect, whether or not it is
    also on the exemption list above — and the ones that ARE exempt must still write a QUALIFIED
    selector or set a colour the theme cannot know. An UNQUALIFIED `color:` in an inline sheet also
    cascades to the widget's CHILDREN, which is a second, quieter bug: it is why a container that
    "just tints its own label" can repaint an entire subtree."""
    # The exempt merges, and what each is allowed to say. A qualified selector is checked by name;
    # the four unqualified ones are all leaf QLabels with no children to cascade onto.
    LEAF = {("app.py", "StudioWindow._update_reference_status"),
            ("coaching_panel.py", "PhaseBar.__init__"),
            ("coaching_panel.py", "_row_face"),
            ("stats_panel.py", "StatsView._set_target_tile")}
    offenders = []
    hits = 0
    for fn in _modules():
        if fn == "theme.py":
            continue
        path = os.path.join(_STUDIO, fn)
        for lineno, node, owner in _calls(path, "setStyleSheet"):
            src = ast.unparse(node)
            if "color" not in src:
                continue
            hits += 1
            qualified = re.search(r"Q[A-Za-z]+#\w+\s*\{", src) or re.search(r"\{\{", src)
            if not qualified and (fn, owner) not in LEAF:
                offenders.append(
                    f"{fn}:{lineno} sets a bare `color:` (in {owner}) — it will cascade to every "
                    f"child; use a theme role, or a `QLabel#Name {{ … }}` selector")
    assert not offenders, "\n  ".join(offenders)
    assert hits >= 5, f"only {hits} colour merges found — the exemption list needs re-reading"
    print(f"test_no_bare_colour_declaration_creeps_back_in OK ({hits} colour merges, "
          f"{len(LEAF)} of them leaf labels)")


# ============================================================================ 2. names and roles
def _string_args(path, name, argi=0):
    """Every literal string passed as argument `argi` to `<x>.<name>(...)`, with its file/line."""
    out = []
    for lineno, node, _owner in _calls(path, name):
        if len(node.args) > argi and isinstance(node.args[argi], ast.Constant):
            v = node.args[argi].value
            if isinstance(v, str):
                out.append((lineno, v, node))
    return out


def test_every_object_name_and_role_has_a_theme_rule():
    """Check 2. An `objectName` (or a `role` property) is this app's way of saying "the stylesheet
    dresses this"; four of them said it and were not true — `LoadingCancel`, `LapExcludedStrip`,
    `LapExcludedList` and `PBToastShare` had names and no rule anywhere, so three of them were
    styled from Python strings instead and the fourth simply took the generic button chrome in the
    middle of a typographic card."""
    EXEMPT_NAMES = set()      # none: every name the app sets is dressed by the theme
    # Properties that are NOT theme selectors — a plain Qt property being used as state.
    EXEMPT_PROPS = {"_fitting_reason", "featureTip"}
    qss = theme._build_qss()
    missing = []
    names = roles = 0
    for fn in _modules():
        if fn == "theme.py":
            continue
        path = os.path.join(_STUDIO, fn)
        for lineno, value, _node in _string_args(path, "setObjectName"):
            names += 1
            if value not in EXEMPT_NAMES and f"#{value}" not in qss:
                missing.append(f"{fn}:{lineno} objectName {value!r} has no rule in theme._build_qss")
        for lineno, prop, node in _string_args(path, "setProperty"):
            if prop in EXEMPT_PROPS or len(node.args) < 2:
                continue
            if not isinstance(node.args[1], ast.Constant) or not isinstance(node.args[1].value, str):
                continue      # a computed value (a `tone` toggled at runtime); the rule is checked
                              # through its static twin at the other call site
            roles += 1
            if f'[{prop}="{node.args[1].value}"]' not in qss:
                missing.append(f"{fn}:{lineno} {prop}={node.args[1].value!r} has no rule")
    assert not missing, "names/roles the theme does not dress:\n  " + "\n  ".join(missing)
    print(f"test_every_object_name_and_role_has_a_theme_rule OK "
          f"({names} objectNames, {roles} role/variant values, all dressed)")


# ============================================================================ 3. the icon buttons
def test_every_icon_button_is_one_size_on_the_real_view():
    """Check 3, LIVE, because this is the assertion a static read cannot make.

    Two families shipped — `central_view._HDR_ICON_BTN` 26x24 with a 15 px glyph and
    `video_view._ICON_BTN` 32x30 with an 18 px glyph — and NEITHER painted what it said: the base
    QPushButton rule's `min-height` REPLACES a widget's own minimum, so the four ⛶ buttons stood at
    26x28 and the five transport buttons at 32x28. Three declared sizes, two painted, none of them
    a token. Measured here on the production view, at both shipped window sizes."""
    from test_central_view_realqt import _real_central_view

    for size in ((1440, 900), (1280, 800)):
        view = _real_central_view()[0]
        view.resize(*size)
        view.show()
        for _ in range(8):
            _APP.processEvents()
        buttons = {
            "⛶ video": view._video_max_btn, "⛶ table": view._table_max_btn,
            "⛶ map": view._map_max_btn, "⛶ charts": view._plots_max_btn,
            "play": view.video.play_btn, "mute": view.video.mute_btn,
            "g-meter": view.video.gmeter_btn, "fullscreen": view.video.fullscreen_btn,
        }
        got = {k: (b.width(), b.height()) for k, b in buttons.items()}
        glyphs = {k: b.iconSize().width() for k, b in buttons.items()}
        view.hide()
        want = (theme.ICON_BTN.width(), theme.ICON_BTN.height())
        assert set(got.values()) == {want}, (
            f"every icon button is theme.ICON_BTN={want} at {size}: {got}")
        assert set(glyphs.values()) == {theme.ICON_PX}, (
            f"every icon button's glyph is theme.ICON_PX={theme.ICON_PX}: {glyphs}")
        # and it still clears the pointer-target floor it was hand-sized under before (26x22).
        for k, (w, h) in got.items():
            assert w >= theme.HIT_MIN and h >= theme.HIT_MIN, (k, w, h)
    print(f"test_every_icon_button_is_one_size_on_the_real_view OK "
          f"(8 buttons @ {theme.ICON_BTN.width()}x{theme.ICON_BTN.height()}, "
          f"glyph {theme.ICON_PX})")


# ============================================================================ 3b. the chips
def test_an_absent_reference_chip_costs_the_window_no_height():
    """A hidden permanent status-bar widget is NOT free, and turning a label into a chip is exactly
    where that bites.

    QStatusBar sizes itself from its children's SIZE HINTS and counts a permanent widget that is
    merely hidden. The status bar's cross-recording reference chip is invisible on every session
    that has no reference — nearly all of them — so promoting it from a bare BarLabel to a real
    CHIP (a pill with padding and a border: 20 px of hint against 14) silently stood the bar at 25
    where it had been 22, and the four panels came back 391/452/321/522 against 393/453/322/524.
    Three pixels of content, spent on something no user can see. It was found by diffing the
    before/after renders of this phase, not by any test — which is why there is now a test.

    The fix is that the chip is MOUNTED, not merely shown: `removeWidget` takes it out of the bar's
    LAYOUT as well. Measured under the SHIPPED theme (this module's `themed_app`), because that is
    the only regime in which the chip has the padding and border that cost the pixels — a bare
    QApplication draws it as plain text and cannot see the defect at all."""
    from PySide6.QtWidgets import QStatusBar
    from test_central_view_realqt import _studiowindow_with_view

    from studio.widgets import chip

    def settle(n=4):
        for _ in range(n):
            _APP.processEvents()

    # (i) THE QT BEHAVIOUR, demonstrated rather than remembered, on a bare bar so the claim does
    #     not depend on whatever else the app's own bar is holding.
    probe = QStatusBar()
    probe.show()
    settle(2)
    empty = probe.sizeHint().height()
    hidden = chip("▶ reference: Daytona Milton Keynes")
    hidden.setVisible(False)
    probe.addPermanentWidget(hidden)
    settle(2)
    cost = probe.sizeHint().height() - empty
    probe.hide()
    assert cost > 0, (
        f"a HIDDEN permanent widget cost nothing here ({empty}px either way). If Qt has changed, "
        f"this test can go — but check that before weakening it, because the app's mount/unmount "
        f"dance exists only for this")

    # (ii) SO THE APP MUST NOT MOUNT ONE it has nothing to say in.
    win, view = _studiowindow_with_view(build_menu=True)
    settle(6)
    assert win.session.reference_session() is None, "the fixture must have no reference"
    assert win._ref_chip.property("role") == "Chip"
    # isHidden(), not isVisible(): the second is False for every widget in an unshown window, so it
    # would pass here for the wrong reason. isHidden() is the widget's OWN explicit flag.
    assert win._ref_chip.isHidden()
    assert not win._ref_chip_mounted, (
        "the reference chip is in the status bar with no reference to name — it is sizing the bar")
    # ...and the mount path is idempotent: _update_reference_status runs on every load and every
    # reference change, so a double mount would stack two chips in the bar.
    for on in (False, True, True, False, False):
        win._mount_reference_chip(on)
        settle(1)
    assert not win._ref_chip_mounted and win._ref_chip.isHidden()
    win._mount_reference_chip(True)
    settle(2)
    assert not win._ref_chip.isHidden(), "a real reference must put the chip back on the bar"
    win._mount_reference_chip(False)
    settle(2)
    view.dispose()
    win.hide()
    print(f"test_an_absent_reference_chip_costs_the_window_no_height OK "
          f"(a hidden permanent chip would cost {cost}px, so it is not mounted)")


# ============================================================================ 4. the toggles
def test_only_the_toggle_factory_makes_a_checkable_button():
    """Check 4. The "setCheckable(True) + recolour the glyph in a `toggled` handler" pattern
    appeared SEVEN times, in four files, and six of the seven disagreed with the others about
    something: the height an iconed toggle ends up at, whether the OFF glyph is tinted `C.text` or
    left at theme.icon's default, and — the one that was a real defect — whether the ON colour is a
    token or a palette ACCESSOR resolved at paint time.

    QAction.setCheckable is a different thing (a menu item, not a control) and stays where it is."""
    EXEMPT = {
        # the three View-menu checkmarks: QActions, not buttons — no glyph, no size, no state tint.
        ("app.py", "StudioWindow._build_menu"),
        ("app.py", "StudioWindow._build_view_menu"),
    }
    offenders = []
    for fn in _modules():
        if fn == "widgets.py":
            continue                      # the factory's own home
        for lineno, _node, owner in _calls(os.path.join(_STUDIO, fn), "setCheckable"):
            if (fn, owner) not in EXEMPT:
                offenders.append(f"{fn}:{lineno} setCheckable (in {owner})")
    assert not offenders, (
        "a checkable control built by hand instead of with widgets.ToggleButton:\n  "
        + "\n  ".join(offenders))
    # ...and the factory really is the one the views use.
    from studio.widgets import ToggleButton
    for mod, attr in (("map_view", "ToggleButton"), ("plots_view", "ToggleButton"),
                      ("video_view", "ToggleButton"), ("central_view", "ToggleButton")):
        m = __import__(f"studio.{mod}", fromlist=[attr])
        assert getattr(m, attr) is ToggleButton, mod
    print("test_only_the_toggle_factory_makes_a_checkable_button OK "
          "(4 view modules, one factory)")


def _run_all():
    test_no_module_styles_itself_from_a_string()
    test_no_bare_colour_declaration_creeps_back_in()
    test_every_object_name_and_role_has_a_theme_rule()
    test_every_icon_button_is_one_size_on_the_real_view()
    test_an_absent_reference_chip_costs_the_window_no_height()
    test_only_the_toggle_factory_makes_a_checkable_button()
    print("\nAll control-vocabulary (inline-style / icon-button / toggle) tests passed.")


if __name__ == "__main__":
    _run_all()
