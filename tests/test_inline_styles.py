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

So: five checks. Each has an EXEMPT set of `(file, owner)` tuples with prose saying whose decision
it is, in the house idiom (tests/test_contrast.py:_hue_reads, tests/test_design_system.py) — and
every entry in every one of those sets must MATCH SOMETHING, checked in both directions the way
tests/test_layering.py checks its allow-list. A name-keyed exemption that matches nothing is
silently a no-op, so a stale one costs nothing at run time and can never fail: three of them had
drifted onto functions that no longer exist (`coaching_panel._row_face`, twice, and
`app.StudioWindow._build_view_menu`) and the file went on passing.

  1. NO MODULE STYLES ITSELF FROM A STRING, except the handful of PER-DATUM colour merges a
     stylesheet genuinely cannot express — a Δ that changes hue every tick, a bar segment whose
     fill IS the datum, a palette-accessor colour that must resolve after the QSS was built.
  2. EVERY objectName AND role HAS A RULE. The name is a claim; this checks it is true.
  3. THE ICON BUTTONS ARE ONE FAMILY, measured on the real view — two families shipped (26x24 and
     32x30, both of which the stylesheet then quietly stood at 28 high anyway).
  4. THE TOGGLES GO THROUGH ONE FACTORY. `setCheckable(True)` on a button belongs to
     widgets.ToggleButton; seven call sites re-implemented it, and six of the seven disagreed with
     each other about height, off-tint or binding time.
  5. EVERY WIDGET THE THEME GIVES A BOX TO CAN ACTUALLY PAINT IT — check 2's other half. A rule
     matching a widget is not the same as a widget drawing it: a QWidget SUBCLASS ignores the
     stylesheet's background and border unless it sets `WA_StyledBackground`, and all four panel
     headers, both panel toolbars and the excluded-lap strip shipped flat on the canvas because of
     it. This is the FIFTH styling mechanism in this app that reached a widget's rule but not its
     pixels, so it is a rule now rather than a fix.

Offscreen Qt.  Checks 1-2, 4 and 5 are static; check 3 builds the production view over the
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


def _no_dead_exemptions(exempt, found, what):
    """Every exemption must excuse something that still exists.

    The failure mode this closes: these sets are keyed by `(file, owner)` NAME, and a key that
    matches nothing is a no-op — it costs nothing at run time, so a decision that moved (a helper
    folded into a constructor, a menu builder renamed) leaves an entry that can never fail and can
    never be noticed. Checked in the same both-directions shape as tests/test_layering.py's ALLOWED
    list: `offenders` catches an unexcused call, this catches an excuse with nothing under it."""
    dead = sorted(exempt - set(found))
    assert not dead, (
        f"dead {what} exemption(s) — these name no {what} call in the tree, so they excuse nothing "
        f"and can never fail. Delete them (or fix the name if the decision moved):\n  "
        + "\n  ".join(f"{f}::{o}" for f, o in dead))


# ============================================================================ 1. the strings
def test_no_module_styles_itself_from_a_string():
    """Check 1, and the metric this phase is measured by: 34 inline `setStyleSheet` sites -> 6.

    Every survivor is a PER-DATUM colour, i.e. a value that changes with the data and therefore
    cannot live in a stylesheet built once at startup. Anything that is the same on every recording
    is a ROLE now (see the labels-and-roles section of theme.py)."""
    EXEMPT = {
        # ---- per-tick Δ colour: ahead / behind / neutral, recomputed as the video plays. Both
        # write a QUALIFIED selector (`QLabel#DiffBox { … }`) over the widget's own themed rule, and
        # both are guarded so a stable readout costs no re-parse.
        ("central_view.py", "CentralView._update_diff_box"),
        ("video_view.py", "_PaneCell.set_badge"),
        # ---- the coaching PhaseBar: the three segment FILLS and the two numbers under them are
        # the datum (entry/mid/exit Δt, coloured by which third is losing, plus the "net" line
        # whose ahead case reads the palette ACCESSOR ahead_colour() — a value the stylesheet is
        # built too early to know), all computed per row in the one constructor.
        ("coaching_panel.py", "PhaseBar.__init__"),
        # ---- NOT THIS PHASE'S. stats_panel is Phase 4's surface, and this call carries a
        # load-bearing ORDERING (the sheet must be set before setFont or the repolish drops the
        # italic bit) that a mechanical migration would be very likely to break. Left exactly as it
        # is, deliberately; it is the one exemption here that is a deferral rather than a decision.
        ("stats_panel.py", "StatsView._set_target_tile"),
    }
    offenders, found = [], []
    total = 0
    for fn in _modules():
        if fn == "theme.py":
            continue                      # the stylesheet's own home
        for lineno, _node, owner in _calls(os.path.join(_STUDIO, fn), "setStyleSheet"):
            total += 1
            found.append((fn, owner))
            if (fn, owner) not in EXEMPT:
                offenders.append(f"{fn}:{lineno} setStyleSheet (in {owner})")
    assert not offenders, (
        "widgets styled from a string instead of from a theme role — put the treatment in "
        "theme.py's stylesheet and set a `role`/objectName here (or add an EXEMPT entry saying "
        "why this colour cannot be known until the data is):\n  " + "\n  ".join(offenders))
    _no_dead_exemptions(EXEMPT, found, "setStyleSheet")
    # SIX calls across FOUR owners: coaching_panel.PhaseBar.__init__ makes THREE of them (two loops
    # over the segment datum plus the "net" line, all in the one constructor). The number is pinned
    # so that "one more little stylesheet" has to come here and argue for itself — the count is the
    # migration's whole point. It was seven until the status-bar reference chip's merge went: it
    # set `color: PROVISIONAL_COLOR`, which IS the colour QLabel[role="Chip"] already paints, so
    # the caveat it claimed to draw changed nothing. That is now a `tone="warn"` role flip.
    assert total == 6, (
        f"{total} inline setStyleSheet calls (was 34, is 6 by design): the list above is the "
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
    # the two unqualified ones are both leaf QLabels with no children to cascade onto.
    LEAF = {("coaching_panel.py", "PhaseBar.__init__"),
            ("stats_panel.py", "StatsView._set_target_tile")}
    offenders, found = [], []
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
            found.append((fn, owner))
            qualified = re.search(r"Q[A-Za-z]+#\w+\s*\{", src) or re.search(r"\{\{", src)
            if not qualified and (fn, owner) not in LEAF:
                offenders.append(
                    f"{fn}:{lineno} sets a bare `color:` (in {owner}) — it will cascade to every "
                    f"child; use a theme role, or a `QLabel#Name {{ … }}` selector")
    assert not offenders, "\n  ".join(offenders)
    _no_dead_exemptions(LEAF, found, "bare-colour LEAF")
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


def test_the_reference_chips_unverified_caveat_is_actually_visible():
    """A trust caveat you cannot see is not a caveat.

    The geometry-matched branch of `_update_reference_status` claimed to "flag it with the shared
    PROVISIONAL trust-tier colour" — and PROVISIONAL_COLOR is C.text_dim, which is the colour
    `QLabel[role="Chip"]` already paints. The two branches rendered IDENTICALLY: 0 pixels of
    12,338 differed. The code said tint, the comment said tint, the guard's exemption prose said
    tint, and the screen said nothing; only the chip's TEXT ever changed.

    Driven through the real `StudioWindow._update_reference_status` with the session's three
    reference accessors stubbed, and read out of the WINDOW composite — not the chip's own grab(),
    which renders the widget's palette rather than what the window shows."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage
    from test_central_view_realqt import _studiowindow_with_view

    win, view = _studiowindow_with_view(build_menu=True)
    del win._update_reference_status            # drop the no-op stub; use the production method
    s = win.session
    s.has_reference = lambda: True
    s.reference_session = lambda: None
    s.reference_label = lambda: "Daytona Milton Keynes"
    win.resize(1440, 900)
    win.show()

    def render(geometric):
        s.reference_match_is_geometric = lambda: geometric
        win._update_reference_status()
        for _ in range(8):
            _APP.processEvents()
        chip_w = win._ref_chip
        img = win.grab().toImage().convertToFormat(QImage.Format_RGB32)
        p = chip_w.mapTo(win, QPoint(0, 0))
        return [[img.pixel(p.x() + x, p.y() + y) for x in range(chip_w.width())]
                for y in range(chip_w.height())], chip_w.text()

    confirmed, confirmed_text = render(False)
    caveat, caveat_text = render(True)
    win.hide()
    view.dispose()

    # The two grabs are NOT the same size: the caveat branch appends "— unverified", so the chip
    # is wider in that state. Compare the box they share, explicitly. A bare zip() would do this
    # silently by truncating to the shorter row, which is how the measurement reads as if the two
    # states were the same shape — and confining it to the common box is what keeps this a test of
    # the TINT rather than of the extra text, which the text assertion below covers separately.
    rows = min(len(confirmed), len(caveat))
    cols = min(len(confirmed[0]), len(caveat[0])) if rows else 0
    total = rows * cols
    differ = sum(confirmed[y][x] != caveat[y][x] for y in range(rows) for x in range(cols))
    assert total > 1000, f"the chip rendered {total} px — it is not on screen, so this proves nothing"
    assert "unverified" in caveat_text and "unverified" not in confirmed_text, \
        (confirmed_text, caveat_text)
    # A tenth of the pill is a low bar that the amber fill+border+ink clears by a mile and the
    # shipped no-op missed completely (it changed exactly zero).
    assert differ > total // 10, (
        f"the unverified reference chip is visually IDENTICAL to a confirmed one "
        f"({differ}/{total} px differ). The text says 'unverified' and nothing else does — if that "
        f"is the intent, say so here and drop the colour call rather than leaving a tint that "
        f"tints nothing")
    # …and the cue is REDUNDANT with the text, not a substitute for it: the app's rule for colour.
    assert win._ref_chip.property("tone") == "warn"
    print(f"test_the_reference_chips_unverified_caveat_is_actually_visible OK "
          f"({differ}/{total} px differ; was 0)")


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
        # All three are built in _build_menu; the View menu is a section of it, not a method.
        ("app.py", "StudioWindow._build_menu"),
    }
    offenders, found = [], []
    for fn in _modules():
        if fn == "widgets.py":
            continue                      # the factory's own home
        for lineno, _node, owner in _calls(os.path.join(_STUDIO, fn), "setCheckable"):
            found.append((fn, owner))
            if (fn, owner) not in EXEMPT:
                offenders.append(f"{fn}:{lineno} setCheckable (in {owner})")
    assert not offenders, (
        "a checkable control built by hand instead of with widgets.ToggleButton:\n  "
        + "\n  ".join(offenders))
    _no_dead_exemptions(EXEMPT, found, "setCheckable")
    # ...and the factory really is the one the views use.
    from studio.widgets import ToggleButton
    for mod, attr in (("map_view", "ToggleButton"), ("plots_view", "ToggleButton"),
                      ("video_view", "ToggleButton"), ("central_view", "ToggleButton")):
        m = __import__(f"studio.{mod}", fromlist=[attr])
        assert getattr(m, attr) is ToggleButton, mod
    print("test_only_the_toggle_factory_makes_a_checkable_button OK "
          "(4 view modules, one factory)")


# ================================================= 5. a rule that matches is not a rule that paints
# The Qt condition, spelled out because everything below turns on it. QStyleSheetStyle::polish sets
# WA_StyledBackground for you when — and only when — `w->metaObject() == &QWidget::staticMetaObject`,
# i.e. the widget is a BARE QWidget. Give that widget a Python subclass and the stylesheet's
# background, border and radius stop being painted, with no warning anywhere: the rule still
# matches, `palette()` still reports the rule's colour (which is why a child `widget.grab()` shows
# the "right" colour for a bar that composites the canvas on screen), and only the window composite
# tells the truth. Every class that draws its own box — QLabel, QPushButton, QFrame, QComboBox,
# QAbstractScrollArea and friends — is immune, which is exactly why this stays invisible until it
# lands on the one widget family that isn't.
_SELF_PAINTING = {
    # Qt classes whose own paintEvent/QStyle primitive draws the stylesheet box. Not exhaustive
    # for all of Qt — exhaustive for the base classes studio/ actually subclasses.
    "QLabel", "QPushButton", "QToolButton", "QAbstractButton", "QCheckBox", "QRadioButton",
    "QComboBox", "QLineEdit", "QTextEdit", "QPlainTextEdit", "QFrame", "QGroupBox",
    "QScrollArea", "QAbstractScrollArea", "QTableWidget", "QTableView", "QTreeWidget",
    "QListWidget", "QTabBar", "QTabWidget", "QMenu", "QMenuBar", "QStatusBar", "QToolBar",
    "QProgressBar", "QSlider", "QSpinBox", "QDialog", "QMainWindow", "QSplitter", "QHeaderView",
    "QStackedWidget", "QGraphicsView", "QVideoWidget", "PlotWidget", "GraphicsLayoutWidget",
}
# A declared `background`/`border` that paints NOTHING needs nobody to draw it.
_INERT = {"none", "transparent", "", "inherit", "0", "0px"}


def _box_selectors(qss):
    """Every selector part in the stylesheet that declares a VISIBLE background or border, split
    into the two keys studio/ can carry: `#objectName` and `[property="value"]`.

    Each key remembers whether any rule claiming it would match a plain QWidget — `QLabel[role=
    "PanelHeader"]` dresses the dialogs' label headers and says nothing about a QWidget wearing the
    same role, so a QWidget-rooted widget is only on the hook for a rule qualified `QWidget`, `*`,
    or nothing at all."""
    qss = re.sub(r"/\*.*?\*/", "", qss, flags=re.S)
    blocks = re.findall(r"([^{}]*)\{([^{}]*)\}", qss, flags=re.S)
    assert len(blocks) > 60, f"the QSS block parse found only {len(blocks)} rules"
    names, props = {}, {}
    for selector, body in blocks:
        paints = False
        for line in body.split(";"):
            prop, _, val = line.partition(":")
            prop, val = prop.strip().lower(), " ".join(val.split()).lower()
            if prop.split("-")[0] in ("background", "border") and val not in _INERT \
                    and not re.fullmatch(r"0(px)?( none)?( \S+)?", val):
                paints = True
        if not paints:
            continue
        for part in " ".join(selector.split()).split(","):
            part = part.strip()
            if not part or "::" in part:   # a SUB-CONTROL box (::item, ::handle) is the style's
                continue                   # to draw, not the widget's
            qualifier = re.match(r"[A-Za-z*]\w*", part)
            plain = qualifier is None or qualifier.group(0) in ("QWidget", "*")
            for n in re.findall(r"#(\w+)", part):
                names[n] = names.get(n, False) or plain
            for p, v in re.findall(r'\[(\w+)="([^"]+)"\]', part):
                props[(p, v)] = props.get((p, v), False) or plain
    return names, props


def _widget_classes():
    """Every class in studio/, with its declared bases and whether its body ever sets
    WA_StyledBackground (in any method — the attribute is sticky, not per-call)."""
    out = {}
    for fn in _modules():
        path = os.path.join(_STUDIO, fn)
        src = open(path, encoding="utf-8").read()
        for node in ast.walk(ast.parse(src, path)):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = [b.id if isinstance(b, ast.Name) else
                     b.attr if isinstance(b, ast.Attribute) else "?" for b in node.bases]
            body = ast.unparse(node)
            out[node.name] = (fn, bases, "WA_StyledBackground" in body)
    return out


def _dressed_widgets():
    """Every `setObjectName("X")` / `setProperty("role"/"tone"/…, "Y")` in studio/, resolved to the
    CLASS of the widget it was called on: `("PanelHeader", "self")` for a class dressing itself,
    `("QWidget", "instance")` for a local built by a constructor call. Assignment-tracked, because
    the excluded-lap strip is named by its parent (`strip = _ExcludedStrip(...)`, then
    `strip.setObjectName(...)`) rather than by itself."""
    def visit(fn, node, cls, types, out):
        if isinstance(node, ast.ClassDef):
            cls, types = node.name, {}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            types = dict(types)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            f = node.value.func
            made = f.id if isinstance(f, ast.Name) else (
                f.attr if isinstance(f, ast.Attribute) else None)
            if made and made.lstrip("_")[:1].isupper():          # a CLASS, not a factory function
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        types[t.id] = made
                    elif (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                            and t.value.id == "self"):
                        types[f"self.{t.attr}"] = made
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("setObjectName", "setProperty")):
            recv, args = node.func.value, node.args
            if isinstance(recv, ast.Name):
                key = recv.id
            elif (isinstance(recv, ast.Attribute) and isinstance(recv.value, ast.Name)
                    and recv.value.id == "self"):
                key = f"self.{recv.attr}"
            else:
                key = None
            target = (cls, "self") if key == "self" else (types.get(key), "instance")
            lit = [a.value for a in args if isinstance(a, ast.Constant)
                   and isinstance(a.value, str)]
            if node.func.attr == "setObjectName" and lit:
                out.append((fn, node.lineno, ("name", lit[0]), target))
            elif node.func.attr == "setProperty" and len(lit) == len(args) == 2:
                out.append((fn, node.lineno, ("prop", (lit[0], lit[1])), target))
        for ch in ast.iter_child_nodes(node):
            visit(fn, ch, cls, types, out)

    out = []
    for fn in _modules():
        path = os.path.join(_STUDIO, fn)
        visit(fn, ast.parse(open(path, encoding="utf-8").read(), path), None, {}, out)
    return out


def test_every_widget_the_theme_gives_a_box_to_can_paint_it():
    """Check 5. Cross the stylesheet's BOX rules with the widgets that carry their names.

    This is the fifth time a styling mechanism has reached a widget's rule and not its pixels —
    after the blanket `font-family`, the blanket `font-size`, the blanket `min-height` and the PB
    toast's own card — so it stops being a fix and becomes an invariant. The rule: if the theme
    declares a background or a border for a name/role, every studio widget carrying that name/role
    must be a class that paints its own box, a BARE `QWidget()` (which Qt styles for you), or a
    QWidget subclass that sets `WA_StyledBackground`.

    Deliberately BROAD — keyed on the stylesheet's own box declarations rather than on a list of
    the names we already know about, because a narrow guard is defeated by the next role somebody
    adds, which is precisely how this defect arrived. It is not noisy in practice: Qt's condition
    is exact, so of the 34 dressed widgets in studio/ only the handful rooted at a plain QWidget
    are ever in scope."""
    EXEMPT_TARGETS = set()      # (file, class): none — every dressed widget resolves and complies
    classes = _widget_classes()
    boxed_names, boxed_props = _box_selectors(theme._build_qss())

    def root(name, seen=()):
        """Walk declared bases to the Qt class this one is rooted at."""
        if name is None or name in seen:
            return None
        if name in _SELF_PAINTING or name == "QWidget":
            return name
        entry = classes.get(name)
        if entry is None:
            return None
        for b in entry[1]:
            r = root(b, seen + (name,))
            if r:
                return r
        return None

    def paints_box(name, seen=()):
        """WA_StyledBackground set on this class or any studio ancestor."""
        entry = classes.get(name)
        if entry is None or name in seen:
            return False
        return entry[2] or any(paints_box(b, seen + (name,)) for b in entry[1])

    offenders, unresolved, checked = [], [], []
    for fn, lineno, (kind, value), (target, how) in _dressed_widgets():
        table = boxed_names if kind == "name" else boxed_props
        if value not in table:
            continue                       # the theme gives this name no box; check 2 owns the rest
        label = f"#{value}" if kind == "name" else f'[{value[0]}="{value[1]}"]'
        if target is None:
            unresolved.append(f"{fn}:{lineno} {label} — cannot resolve the widget's class")
            continue
        r = root(target)
        if r is None:
            unresolved.append(f"{fn}:{lineno} {label} on {target} — cannot resolve its Qt base")
            continue
        if r != "QWidget" or not table[value]:
            continue                       # self-painting, or no QWidget-matching rule claims it
        if how == "instance" and target == "QWidget":
            continue                       # a BARE QWidget: Qt sets the attribute itself
        checked.append((fn, target, label))
        if not paints_box(target) and (fn, target) not in EXEMPT_TARGETS:
            offenders.append(
                f"{fn}:{lineno} {target} wears {label}, which the theme gives a background/border "
                f"— but {target} is a QWidget SUBCLASS and never calls "
                f"setAttribute(Qt.WA_StyledBackground, True), so Qt paints none of it")
    assert not unresolved, (
        "check 5 could not type these dressed widgets, so it cannot vouch for them — teach the "
        "walker, or split the construction out:\n  " + "\n  ".join(unresolved))
    assert not offenders, (
        "widgets the theme dresses and Qt will not paint:\n  " + "\n  ".join(offenders))
    _no_dead_exemptions(EXEMPT_TARGETS, [(f, t) for f, t, _l in checked], "WA_StyledBackground")
    assert checked, "check 5 matched nothing — the QSS parse or the AST walk has gone vacuous"
    print(f"test_every_widget_the_theme_gives_a_box_to_can_paint_it OK "
          f"({len(boxed_names)} boxed names + {len(boxed_props)} boxed roles; "
          f"{len(checked)} QWidget subclasses on the hook, all painting)")


def _run_all():
    test_no_module_styles_itself_from_a_string()
    test_no_bare_colour_declaration_creeps_back_in()
    test_every_object_name_and_role_has_a_theme_rule()
    test_every_icon_button_is_one_size_on_the_real_view()
    test_an_absent_reference_chip_costs_the_window_no_height()
    test_the_reference_chips_unverified_caveat_is_actually_visible()
    test_only_the_toggle_factory_makes_a_checkable_button()
    test_every_widget_the_theme_gives_a_box_to_can_paint_it()
    print("\nAll control-vocabulary (inline-style / icon-button / toggle / styled-box) tests "
          "passed.")


if __name__ == "__main__":
    _run_all()
