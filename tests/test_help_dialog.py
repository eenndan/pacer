"""Help-menu dialogs (fix/qa-help-dialogs): the shortcut card + the two read-only copy cards.

These pin the three failure modes the QA sweep found in studio/help_dialog.py, all of which are
"the card silently says less than it should":

  * L1-01 (a regression of #119) — a wrapping description was given the SINGLE-LINE height, so the
    LAYOUT row painted its second line outside the row, sliced through the letterforms. #119 only
    widened the card (440 -> 560), which changes WHICH strings wrap, never how tall a wrapped row
    is. Pinned at the card's natural size AND at 600 px, because a width bump is not the fix.
  * L1-08 — ⌘O was a live binding with no row on the card that calls itself the single source of
    truth. The set-difference test below is the general guard: every accelerator and QShortcut the
    window binds must appear in SHORTCUT_GROUPS, so the NEXT one cannot go undocumented either.
  * L1-07 — the card hand-typed "⌘⇧S" where Qt paints "⇧⌘S". The rows now hold the QKeySequence,
    so the glyphs come from Qt. NOTE: this asserts the card's text EQUALS the live binding's
    NativeText, never a literal glyph — QKeySequence.FullScreen resolves to F11 under the
    offscreen platform and to ⌃⌘F on a real Mac, and both must pass.
  * L1-05 — About / Privacy could be shrunk below their own copy with no scrollbar and no cue.
  * L1-04 — the privacy card must name every store the app writes, not two of the three.

Real offscreen Qt widgets; no pacer, no telemetry file. The binding half builds a StudioWindow via
__new__ + QMainWindow.__init__ (the seam the other offscreen app tests use), so no session loads.
Run: QT_QPA_PLATFORM=offscreen python tests/test_help_dialog.py
"""
import ast
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
_STUDIO = os.path.join(_REPO, "studio")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeySequence, QShortcut  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QMainWindow,
    QScrollArea,
)

_APP = QApplication.instance() or QApplication([])

from studio import help_dialog, theme  # noqa: E402
from studio.help_dialog import (  # noqa: E402
    PRIVACY_PARAGRAPHS,
    SHORTCUT_GROUPS,
    AboutDialog,
    PrivacyDialog,
    ShortcutsDialog,
)

# The wrapped height of a paragraph depends on the font, so measure against the app's REAL theme —
# without this every height below is Qt's default-palette default-font guess.
theme.register_fonts()
theme.apply_theme(_APP)

# The ⌘⇧S row's DESCRIPTION, read from the card rather than re-typed: it used to name the
# panel-maximize button with a ⛶ that the button does not paint (D1-06) and now names it in words,
# and a description is copy — a test that hard-codes it fails on a wording change that broke
# nothing. What these tests are about is the KEY, so the key is what they assert.
_STATS_DESC = next(desc for _g, rows in SHORTCUT_GROUPS for key, desc in rows
                   if isinstance(key, QKeySequence) and key == QKeySequence("Ctrl+Shift+S"))


def _shown(dialog):
    """Show a dialog offscreen and let its layout settle (wrapping needs a few passes)."""
    dialog.show()
    for _ in range(6):
        _APP.processEvents()
    return dialog


def _overflowing(dialog):
    """Every wrapping label whose wrapped text is taller than the row it was given."""
    return [(lb.text(), lb.width(), lb.height(), lb.heightForWidth(lb.width()))
            for lb in dialog.findChildren(QLabel)
            if lb.wordWrap() and lb.heightForWidth(lb.width()) > lb.height()]


# ------------------------------------------------------- L1-01: wrapped rows get their height
def test_shortcut_rows_are_tall_enough_at_the_natural_size():
    d = _shown(ShortcutsDialog())
    bad = _overflowing(d)
    assert not bad, f"{len(bad)} row(s) paint outside their row at {d.width()}x{d.height()}: {bad}"
    # The row the regression showed: it wraps at 560 px, and wrapping must make it TALLER.
    layout_row = [lb for lb in d.findChildren(QLabel)
                  if lb.text().startswith("Maximize a panel")]
    assert len(layout_row) == 1, layout_row
    lb = layout_row[0]
    assert lb.height() >= lb.heightForWidth(lb.width()) > 0, (lb.height(), lb.width())
    d.close()
    print("test_shortcut_rows_are_tall_enough_at_the_natural_size OK")


def test_shortcut_rows_are_tall_enough_at_other_widths():
    """Width-independence — the point of the fix. 560 is where the longest description misses its
    box by 5 px today; a wider card only changes which strings wrap, and a narrower one wraps
    more of them. None of those may clip."""
    for width in (520, 560, 600, 700, 900):
        d = _shown(ShortcutsDialog())
        d.resize(width, d.height())
        for _ in range(6):
            _APP.processEvents()
        bad = _overflowing(d)
        assert not bad, f"at {width} px: {bad}"
        d.close()
    print("test_shortcut_rows_are_tall_enough_at_other_widths OK")


# ------------------------------------------------- K10: one key column across every group
def _card_rows(dialog) -> list[tuple[str, str, int]]:
    """(key text, description, description x in dialog coordinates) for every row the card SHOWS,
    read off its grids — the key cell's KeyCap text, as `_key_text` renders the registry's key."""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QGridLayout
    rows = []
    for grid in dialog.findChildren(QGridLayout):
        for r in range(grid.rowCount()):
            key_item, desc_item = grid.itemAtPosition(r, 0), grid.itemAtPosition(r, 1)
            if key_item is None or desc_item is None:
                continue
            caps = [lb for lb in [key_item.widget(), *key_item.widget().findChildren(QLabel)]
                    if isinstance(lb, QLabel) and lb.property("role") == "KeyCap"]
            desc = desc_item.widget()
            # A named cap ("Comma") carries the key it documents (",") as its "key" property.
            rows.append((caps[0].property("key") or caps[0].text(), desc.text(),
                         desc.mapTo(dialog, QPoint(0, 0)).x()))
    return rows


# The rows K10 reworded: a gesture's key cell became the bare verb (or the button's own glyph) and
# its object moved into the description, so no long cell sets the one shared key column. Pinned
# here as (key cell, description); every other row's description is main's, word for word.
_K10_REWORDED = {
    ("Drag", "Drag the chart cursor: scrub through the current lap"),
    ("Drag", "Drag the start/finish line on the map: fix lap timing (key for unknown tracks)"),
    ("Click", "Maximize a panel to fill the window, or double-click its header "
              "(Esc / again to restore)"),
    ("F  ·", "Make the video fill the screen, or double-click the video (Esc / again to restore)"),
    ("Drag", "Drag any splitter: resize the panels (the layout is remembered)"),
}
# What those five rows said before K10, so the test can prove nothing ELSE changed.
_K10_WAS = {"Scrub through the current lap", "Fix lap timing on the map (key for unknown tracks)",
            "Maximize a panel to fill the window (Esc / again to restore)",
            "Make the video fill the screen (Esc / again to restore)",
            "Resize the panels (the layout is remembered)"}


def test_every_group_shares_one_key_column():
    """K10 (cool/card-and-keys): every group was its own grid sizing its own key column, so the
    descriptions started at six different x (42…227 px offscreen) and the card read as seven
    tables. Now every description starts at ONE x — at the natural size and at the card's minimum
    width — and that x is the widest key cell's, measured, not a constant: every group's key column
    is exactly as wide as the widest cell on the whole card.

    AND THE COLUMN IS NARROW: a gesture's key cell is a bare verb, so the widest cell is a real
    key ("Shift + ← / →"), and at the card's minimum width no more descriptions wrap than main's
    four. Nothing else moves: the card lists the registry's rows in the registry's order, only the
    five reworded rows read differently (pinned), and no description is cut (L1-01's guard)."""
    want = [(help_dialog._key_text(k), d) for _g, rows in SHORTCUT_GROUPS for k, d in rows]
    assert _K10_REWORDED <= set(want), sorted(_K10_REWORDED - set(want))
    assert not _K10_WAS & {d for _k, d in want}, "a K10 row still reads its old description"
    # 33: + Return, Corners ▸ Quickest passes (cool/quickest-passes); 34: + L, loop the pass
    # (cool/pass-loop-slowmo)
    assert len(want) == 34, len(want)
    for size in (None, "minimum"):
        d = _shown(ShortcutsDialog())
        if size:
            d.resize(d.minimumWidth(), 200)
            for _ in range(6):
                _APP.processEvents()
        rows = _card_rows(d)
        assert [(k, desc) for k, desc, _x in rows] == want, (
            "the card no longer lists exactly the registry's shortcuts, in order")
        xs = {x for _k, _d, x in rows}
        assert len(xs) == 1, f"descriptions start at {sorted(xs)} at {d.width()}x{d.height()}"
        cells = [(g.itemAtPosition(r, 0).widget().sizeHint().width(), k)
                 for g in d._grids for r in range(g.rowCount())
                 for k in [help_dialog._key_text(SHORTCUT_GROUPS[d._grids.index(g)][1][r][0])]]
        widest, widest_key = max(cells)
        assert all(g.columnMinimumWidth(0) == widest for g in d._grids), widest
        assert widest_key == "Shift + ← / →", (widest_key, widest)
        wrapped = [lb.text() for _k, desc, _x in rows for lb in d.findChildren(QLabel)
                   if lb.text() == desc and lb.height() > 1.5 * lb.fontMetrics().lineSpacing()]
        assert len(wrapped) <= 4, (d.width(), wrapped)
        assert not _overflowing(d), _overflowing(d)
        d.close()
    print(f"test_every_group_shares_one_key_column OK (descriptions at x={xs.pop()}, "
          f"key column {widest} px, {len(wrapped)} wrapped at {d.width()} px, {len(want)} rows)")


# ------------------------------------- L1-08 + L1-07: the card IS the single source of truth
def _live_bindings():
    """Every key the real window binds: the menu accelerators + the window-level QShortcuts.
    Built off the same seam the other offscreen app tests use (__new__ + QMainWindow.__init__),
    so no session is loaded and no media is touched."""
    from studio import library, units
    from studio.app import StudioWindow
    win = StudioWindow.__new__(StudioWindow)
    QMainWindow.__init__(win)
    # The three view-state fields _build_menu seeds its checkable actions from; __init__ (which we
    # skip, since it would load a recording) normally reads them out of prefs.
    win._excluded_visible = True
    win._colorblind = False
    win._speed_unit = units.KMH
    # ...and the export controller, because _build_menu wires the File ▸ Export actions straight to
    # its methods (§7.1). The real __init__ builds it before the menu for exactly this reason.
    from studio.app import STATUS_MS
    from studio.export_controller import ExportController
    win.exports = ExportController(win, STATUS_MS)
    # ...and the library controller, which _build_menu wires File ▸ Library… / Open Recent /
    # Session record… to — and whose Open Recent seed is the library read diverted just below.
    from studio.library_controller import LibraryController
    win.library_ctl = LibraryController(win, STATUS_MS)
    with tempfile.TemporaryDirectory() as tmp:                      # never read the real library
        library._app_support_dir, real = (lambda: tmp), library._app_support_dir
        try:
            win._build_menu()
        finally:
            library._app_support_dir = real
    win._build_shortcuts()
    keys = {}
    for name in dir(win):
        if not name.endswith("_action"):
            continue
        action = getattr(win, name, None)
        seq = getattr(action, "shortcut", None)
        if seq is not None and callable(seq) and not action.shortcut().isEmpty():
            keys[action.shortcut().toString(QKeySequence.NativeText)] = action.text()
    for sc in win.findChildren(QShortcut):
        keys.setdefault(sc.key().toString(QKeySequence.NativeText), "QShortcut")
    return win, keys


def _documented() -> set[str]:
    """Every key text the card documents, with the multi-key cells split out ("1 · 2 · 3 · 4",
    "F1  ·  ?", and the Layout row whose cell reads "F  ·  double-click video  ·")."""
    card = {help_dialog._key_text(key) for _group, rows in SHORTCUT_GROUPS for key, _d in rows}
    out = set(card)
    for row in card:
        out.update(part.strip() for part in row.split("·"))
    return out


def test_every_live_binding_is_on_the_card():
    """The guard for the whole L1-08 class: a binding added in app.py with no row here fails the
    build. Matching is on Qt's own NativeText, so it is platform-correct by construction."""
    win, live = _live_bindings()
    documented = _documented()
    missing = {k: v for k, v in live.items() if k not in documented}
    assert not missing, f"bound but undocumented: {missing}\ncard rows: {sorted(documented)}"
    assert "⌘O" in documented or "Ctrl+O" in documented, sorted(documented)
    win.deleteLater()   # never shown; close() would run closeEvent's session teardown
    print(f"test_every_live_binding_is_on_the_card OK ({len(live)} bindings)")


# ---------------------------------------------------- the L1-08 hole: a QShortcut built in a VIEW
def _dotted(node) -> str | None:
    """`Qt.Key_F` / `QKeySequence.StandardKey.Open` as a dotted string, or None for anything that
    is not a plain attribute chain."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _static_key(node) -> str | None:
    """The NativeText of the key a `QShortcut(...)` first argument names, or None when the argument
    is not resolvable from source (a local variable — see `_LIVE_HARVESTED`)."""
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name != "QKeySequence" or not node.args:
            return None
        node = node.args[0]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return QKeySequence(node.value).toString(QKeySequence.NativeText)
    dotted = _dotted(node)
    if dotted is None:
        return None
    root, *rest = dotted.split(".")
    obj = {"Qt": Qt, "QKeySequence": QKeySequence}.get(root)
    for attr in rest:
        obj = getattr(obj, attr, None)
    return None if obj is None else QKeySequence(obj).toString(QKeySequence.NativeText)


# The modules whose QShortcuts the LIVE half above genuinely covers. It builds a StudioWindow and
# reads `win.findChildren(QShortcut)`, so it sees exactly the window's own — `app`. A QShortcut
# built anywhere else escapes it, which is how `F` (video_view, the ⤢ video-focus key) was live and
# undocumented while a test called itself the guard for undocumented bindings. Adding a module here
# is a claim that something instantiates it and harvests its shortcuts; the default is to make the
# key STATICALLY resolvable instead, which costs nothing and needs no harness.
_LIVE_HARVESTED = {"app"}


def test_every_qshortcut_in_the_source_is_on_the_card():
    """L1-08's OTHER HALF, and the hole the original guard shipped with.

    `_live_bindings` builds a bare StudioWindow — no session, so no CentralView, so no VideoView —
    and harvests the QShortcuts parented to the WINDOW. `video_view.py` binds `F` on the view
    itself, so it never appeared, and neither would any future key a panel binds. This closes it
    without needing to instantiate every view: an AST sweep over the whole package for `QShortcut(`
    constructions, each key resolved from source and looked up on the card.

    A site whose key comes from a local variable cannot be resolved statically (app.py's
    `_build_shortcuts` helper is one call site for eleven keys). Those are allowed ONLY in modules
    the live harvest above genuinely covers — `_LIVE_HARVESTED` — so a new view binding a key from
    a variable fails here instead of quietly escaping both halves."""
    documented = _documented()
    sites, undocumented, unresolvable = 0, {}, []
    for fn in sorted(os.listdir(_STUDIO)):
        if not fn.endswith(".py"):
            continue
        module = fn[:-3]
        tree = ast.parse(open(os.path.join(_STUDIO, fn), encoding="utf-8").read(), fn)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and node.args
                    and (getattr(node.func, "id", None) == "QShortcut"
                         or getattr(node.func, "attr", None) == "QShortcut")):
                continue
            sites += 1
            key = _static_key(node.args[0])
            if key is None:
                if module not in _LIVE_HARVESTED:
                    unresolvable.append(f"{fn}:{node.lineno}")
            elif key not in documented:
                undocumented[f"{fn}:{node.lineno}"] = key
    assert not undocumented, (
        f"QShortcut(s) bound in the source with no row on the card: {undocumented}\n"
        f"card rows: {sorted(documented)}")
    assert not unresolvable, (
        f"{unresolvable} build a QShortcut from a value this sweep cannot read, in a module the "
        f"live-window harvest does not cover ({sorted(_LIVE_HARVESTED)}) — so the key would be "
        f"bound and documented by nobody. Name the key inline, or extend _LIVE_HARVESTED and the "
        f"harness that justifies it.")
    # ...and the sweep must actually be finding things, or "none undocumented" is free.
    assert sites >= 2, f"the QShortcut sweep found only {sites} construction sites"
    print(f"test_every_qshortcut_in_the_source_is_on_the_card OK "
          f"({sites} construction sites across studio/)")


def test_card_glyphs_match_the_live_bindings():
    """L1-07: the card's modifier rows read exactly what Qt paints in the menu bar. Asserted
    against the LIVE QAction, never a literal glyph — QKeySequence.FullScreen is F11 offscreen
    and ⌃⌘F on macOS, and this must hold on both."""
    win, _live = _live_bindings()
    rows = {desc: help_dialog._key_text(key)
            for _group, group_rows in SHORTCUT_GROUPS for key, desc in group_rows}
    for handle, desc in (("_open_action", "Open a recording"),
                         ("_undo_action", "Undo the last timing-line edit"),
                         ("_stats_action", _STATS_DESC),
                         ("_fullscreen_action", "Enter / exit full screen")):
        live = getattr(win, handle).shortcut().toString(QKeySequence.NativeText)
        assert rows[desc] == live, f"{handle}: card says {rows[desc]!r}, Qt paints {live!r}"
    # And the specific drift the sweep measured: Qt's order is ⇧⌘S, not ⌘⇧S.
    stats = rows[_STATS_DESC]
    assert stats == QKeySequence("Ctrl+Shift+S").toString(QKeySequence.NativeText), stats
    win.deleteLater()   # never shown; close() would run closeEvent's session teardown
    print("test_card_glyphs_match_the_live_bindings OK")


# ------------------------------------------------- L1-05: the copy cards refuse to be truncated
def test_copy_cards_refuse_to_shrink_below_their_copy():
    for cls, ask in ((AboutDialog, (380, 200)), (PrivacyDialog, (460, 260))):
        d = _shown(cls())
        natural = d.height()
        d.resize(*ask)
        for _ in range(6):
            _APP.processEvents()
        assert d.height() >= natural, (cls.__name__, natural, d.height())
        assert d.minimumSize().height() >= natural, (cls.__name__, d.minimumSize())
        d.close()
    print("test_copy_cards_refuse_to_shrink_below_their_copy OK")


def test_a_copy_card_opens_at_the_height_its_copy_needs():
    """A card that FITS must open without a scrollbar — measured on the first open.

    `_fit_to_copy` lays the body out at the width the card will really give it and then reads the
    height back off the layout. The resize was being undone before the read: `widgetResizable`
    means the scroll area owns the body's width, so the paragraphs measured at the viewport's
    412 px rather than the 400 px asked for, came out one wrapped line short, and the privacy card
    opened 16 px under its own copy with a scrollbar it did not need — 643 px where its copy wants
    659.

    IT WAS INVISIBLE UNTIL A BUG WAS FIXED. widgets.WrapLabel used to ratchet its minimum height
    upwards and never back down, so a paragraph that had once measured taller kept the extra line,
    and the 16 px this arithmetic was losing were being supplied by that. The two are only
    separable from the outside by asserting the card's height against its own content, which is
    what this does.

    Shortcuts is deliberately not in the list: it is the one card whose copy genuinely outgrows
    its cap, so its scrollbar is the feature (see test_copy_cards_scroll_rather_than_clip)."""
    for cls in (AboutDialog, PrivacyDialog):
        d = _shown(cls())
        scroll = d.findChildren(QScrollArea)[0]
        body = scroll.widget()
        short = body.height() - scroll.viewport().height()
        assert short <= 0, (
            f"{cls.__name__} opened {short}px short of its own copy — the body is "
            f"{body.height()}px inside a {scroll.viewport().height()}px viewport")
        assert not scroll.verticalScrollBar().isVisible(), (
            f"{cls.__name__} opened with a scrollbar over copy that fits")
        d.close()
    print("test_a_copy_card_opens_at_the_height_its_copy_needs OK")


def test_copy_cards_scroll_rather_than_clip():
    """The backstop for a card whose copy outgrows the display: the paragraphs live in a scroll
    area, so even a screen-capped height keeps every line reachable."""
    for cls in (AboutDialog, PrivacyDialog):
        d = _shown(cls())
        scrolls = d.findChildren(QScrollArea)
        assert len(scrolls) == 1, (cls.__name__, scrolls)
        d.close()
    print("test_copy_cards_scroll_rather_than_clip OK")


# ----------------------------------------------- W14-03: EVERY Help card, not two of the three
def test_every_help_card_is_capped_to_the_display():
    """The two tests above cover the two COPY cards. The card this menu exists for was the one
    that had neither of the things they check.

    ShortcutsDialog is the longest card in the app — seven groups, thirty-odd rows — and it was
    built straight into its own root layout: no scroll area, so nothing could be reached once a
    height was refused, and no `_fit_to_copy`, so no cap on the display. Measured: it opened at
    560x733 with a hard floor of 717 (a resize to 200 px came back 717), while its siblings capped
    themselves at 85% of the screen and adapted — on a 695 px display Privacy opened 642 and
    Shortcuts still demanded 733, with the Close button as the row that went off the bottom
    (QA W14-03; #187 grew this band by 18 px, it did not open it).

    So the rule is stated once, for all three, and it is the rule the two copy cards already
    obeyed: a Help card has exactly one scroll column, and that column never asks the screen for
    more than `_fit_to_copy`'s share of it. On main this fails on ShortcutsDialog with zero scroll
    areas."""
    room = int(_APP.primaryScreen().availableGeometry().height() * 0.85)
    for cls in (ShortcutsDialog, AboutDialog, PrivacyDialog):
        d = _shown(cls())
        scrolls = d.findChildren(QScrollArea)
        assert len(scrolls) == 1, (
            f"{cls.__name__} has {len(scrolls)} scroll columns — a Help card that cannot scroll "
            f"has no way to show copy the display is too short for")
        assert scrolls[0].minimumHeight() <= room, (
            f"{cls.__name__}'s copy column demands {scrolls[0].minimumHeight()}px of a display "
            f"that offers {room}px after the 85% cap")
        d.close()
    print(f"test_every_help_card_is_capped_to_the_display OK (cap {room}px)")


def test_the_shortcuts_card_fits_the_smallest_13_inch_display():
    """The measurement the cap is FOR, taken on a display the card did not fit.

    A screen's size is fixed when QGuiApplication is constructed, so this one is taken in a child
    process against Qt's offscreen `configfile` screen — 1024x615, which is the smaller of the two
    scaled modes macOS offers on a 13-inch panel (the other is 1152x720, ~695 px available). Both
    are inside the band where the card was taller than the desk.

    Three things are read: the height the card OPENS at, the floor Qt will accept, and whether the
    Close button — the row that was going off the bottom — is on the card at all. On main the child
    reports 733/717 against a 615 px screen; here it must fit."""
    import json
    import subprocess

    with tempfile.TemporaryDirectory() as tmp:
        cfg = os.path.join(tmp, "screen.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"screens": [{"name": "small", "x": 0, "y": 0, "width": 1024, "height": 615,
                                    "logicalDpi": 96, "logicalBaseDpi": 96, "dpr": 1}]}, fh)
        child = """
import json, sys
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QDialogButtonBox
app = QApplication([])
from studio import theme
theme.register_fonts(); theme.apply_theme(app)
from studio.help_dialog import ShortcutsDialog
avail = app.primaryScreen().availableGeometry().height()
d = ShortcutsDialog()
d.show()
for _ in range(8):
    app.processEvents()
opened = d.height()
d.resize(560, 200)
for _ in range(8):
    app.processEvents()
bb = d.findChild(QDialogButtonBox)
box = QRect(bb.mapTo(d, QPoint(0, 0)), bb.size())
print("RESULT " + json.dumps(dict(avail=avail, opened=opened, floor=d.height(),
                                  close_on_card=d.rect().contains(box))))
"""
        env = dict(os.environ, PYTHONPATH=_REPO,
                   QT_QPA_PLATFORM="offscreen:configfile=" + cfg)
        run = subprocess.run([sys.executable, "-c", child], env=env,
                             capture_output=True, text=True, timeout=180)
    line = next((ln for ln in run.stdout.splitlines() if ln.startswith("RESULT ")), None)
    assert line, f"the child produced no measurement:\n{run.stdout}\n{run.stderr}"
    got = json.loads(line[len("RESULT "):])
    assert got["avail"] <= 615, f"the child did not get the small screen: {got}"
    assert got["opened"] <= got["avail"], (
        f"the shortcuts card opens {got['opened']}px tall on a {got['avail']}px display")
    assert got["floor"] <= got["avail"], (
        f"the shortcuts card refuses every height below {got['floor']}px on a "
        f"{got['avail']}px display — there is no size at which it fits")
    assert got["close_on_card"], "the Close button is off the bottom of the card"
    print(f"test_the_shortcuts_card_fits_the_smallest_13_inch_display OK "
          f"(opens {got['opened']}px / floor {got['floor']}px on a {got['avail']}px display)")


# --------------------------------------------------- L1-04: the privacy card names every store
def test_privacy_card_names_every_store_it_writes():
    from studio import focus, library, logsetup, marks, prefs, session_record, track_db
    copy = " ".join(PRIVACY_PARAGRAPHS)
    # session_records.json and focus.json joined when the Library's own long privacy paragraph
    # became one line and a link to this card (board review UX-9d): the card had never named them.
    for store in (os.path.basename(prefs.prefs_path()),
                  os.path.basename(track_db.db_path()),
                  os.path.basename(library.library_path()),
                  os.path.basename(session_record.records_path()),
                  os.path.basename(marks.marks_path()),
                  os.path.basename(focus.focus_path())):
        assert store in copy, f"{store} is written by the app but not disclosed"
    # E2: the session log is a file the app writes too, and it holds file paths. Named in the
    # removal route, as the path under the folder that route deletes.
    log_rel = f"{logsetup.LOG_DIR_NAME}/{logsetup.LOG_FILENAME}"
    assert log_rel in copy, f"the session log ({log_rel}) is written by the app but not disclosed"
    assert ".pacer.json" in copy or "pacer.json" in copy, copy
    # The sidecar bullet's "only" was false at app scope (the app also stores GPS coordinates in
    # tracks.json and a filesystem path in prefs.json), under a card that frames the list as
    # exhaustive — "Everything below stays on this computer".
    assert "contains only the line coordinates" not in copy, copy
    # Every documented removal route must reach every store.
    removal = [p for p in PRIVACY_PARAGRAPHS if p.startswith("How to remove")]
    assert len(removal) == 1, removal
    assert "Application Support/pacer" in removal[0], removal[0]
    # DEMO-4 / LEFT-24: the welcome offers "Get demo · N MB" on a fresh launch, so the demo's cache
    # is one first-screen click away — a file of the pinned `demo._DEMO_BYTES` the app writes, under
    # the folder that route deletes. Its folder is derived from the store's own path, never typed here.
    from studio import demo
    cache_dir = os.path.relpath(demo.demo_cache_path(), demo._app_support_dir()).split(os.sep)[0]
    assert f"({cache_dir}/)" in removal[0], f"the demo cache ({cache_dir}/) is not disclosed"
    # …and that click is the app's one network fetch, so the card may not open by denying it.
    opening = PRIVACY_PARAGRAPHS[0]
    assert "no network," not in opening and "no network." not in opening, opening
    assert "no network unless you ask for the demo" in opening, opening
    print("test_privacy_card_names_every_store_it_writes OK")


def _row_cells(dialog):
    """(key cell, description) widgets of every row on the shortcuts card, group by group."""
    for g in dialog._grids:
        for r in range(g.rowCount()):
            key_item, desc_item = g.itemAtPosition(r, 0), g.itemAtPosition(r, 1)
            if key_item is not None and desc_item is not None:
                yield g, key_item.widget(), desc_item.widget()


def test_every_key_cap_sits_on_its_descriptions_baseline():
    """Discovery 6 (craft, P3): both cells are top-aligned, and the KeyCap mono face has a shorter
    ascent than the description's, so every cap stood 3 px above the text it labels. Each cap's
    baseline (its text top + ascent) is now its description's, to the pixel, glyph rows included."""
    from PySide6.QtCore import QPoint
    d = _shown(ShortcutsDialog())
    off = []
    for _g, kw, desc in _row_cells(d):
        db = desc.mapTo(d, QPoint(0, desc.contentsRect().top())).y() + desc.fontMetrics().ascent()
        for cap in [kw, *kw.findChildren(QLabel)]:
            if isinstance(cap, QLabel) and cap.property("role") == "KeyCap":
                cb = cap.mapTo(d, QPoint(0, cap.contentsRect().top())).y() \
                    + cap.fontMetrics().ascent()
                if cb != db:
                    off.append((cap.text(), cb - db))
    d.close()
    assert not off, f"caps off their description's baseline (cap - text, px): {off}"
    print("test_every_key_cap_sits_on_its_descriptions_baseline OK")


def test_comma_and_period_caps_are_named_keys():
    """"," and "." painted 1.5 x 2 px of ink in the KeyCap face: specks beside "Jump to the
    previous / next mark". They read "Comma" and "Period", the way Space and Return are named; the
    key they document (Qt's own text, which the palette and the binding guards read) stays theirs."""
    d = _shown(ShortcutsDialog())
    caps = {desc.text(): kw for _g, kw, desc in _row_cells(d)}
    for desc, name, key in (("Jump to the previous mark", "Comma", ","),
                            ("Jump to the next mark", "Period", ".")):
        cap = caps[desc]
        assert cap.text() == name and cap.property("key") == key, (cap.text(), cap.property("key"))
        assert cap.fontMetrics().horizontalAdvance(cap.text()) > 3 * cap.fontMetrics().averageCharWidth()
    assert {help_dialog._key_text(k) for _g, rows in SHORTCUT_GROUPS for k, _d in rows} >= {",", "."}
    d.close()
    print("test_comma_and_period_caps_are_named_keys OK")


def test_the_card_opens_on_whole_rows():
    """Capped under its rows, the card's scroll column used to end wherever 85 % of the screen fell
    — at 1280x800 263 px into ANALYSIS, through a row. It now opens with the column ending in the
    gap after a whole row (never more than one row shorter than the cap), so no line is sliced at
    the foot and the scrollbar says the rest is below."""
    from PySide6.QtCore import QPoint
    d = _shown(ShortcutsDialog())
    scroll = d._scroll
    room = int(_APP.primaryScreen().availableGeometry().height() * 0.85)
    vp = scroll.viewport()
    body = scroll.widget()
    if body.height() > vp.height():           # only a capped card has a foot to cut
        foot = vp.height()
        sliced = [desc.text() for _g, kw, desc in _row_cells(d)
                  for top, bottom in [(min(kw.mapTo(body, QPoint(0, 0)).y(),
                                           desc.mapTo(body, QPoint(0, 0)).y()),
                                       max(kw.mapTo(body, QPoint(0, kw.height())).y(),
                                           desc.mapTo(body, QPoint(0, desc.height())).y()))]
                  if top < foot < bottom]
        assert not sliced, f"the card opens with its foot ({foot} px) through {sliced}"
        tallest = max(desc.height() for _g, _kw, desc in _row_cells(d))
        assert room - foot <= tallest + 2 * theme.SPACE_M, (room, foot)
    assert scroll.minimumHeight() <= room
    d.close()
    print(f"test_the_card_opens_on_whole_rows OK (column {vp.height()} of {body.height()} px)")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} HELP-DIALOG TESTS PASSED", flush=True)
