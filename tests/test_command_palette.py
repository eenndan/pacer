"""Command-palette guard (⌘K) — and the anti-drift contract that is the point of it.

The palette exists because pacer's capability outgrew its menu bar: five menus, thirty-odd actions,
plus a dozen keyboard-only toggles documented on a card most users never open. The DANGER in adding
it is a second hand-written list of the same commands, drifting from the ? card the first time one
of them gains a row. So neither list is hand-written any more:

  * `help_dialog.COMMANDS` is an ACTION registry — group, key, description, and the name of the
    StudioWindow attribute that runs it.
  * `help_dialog.SHORTCUT_GROUPS` (the ? card) is a VIEW of it, asserted here.
  * `command_palette.entries()` is the menu bar walked live PLUS the registry rows that carry a
    `run` and are not already menu actions.

What this file pins, in the spirit of the existing "every live binding has a card row" guard:

  1. EVERY MENU ACTION IS REACHABLE from the palette — the requested guard. The only rows dropped
     are placeholders (`(none)` under an empty Open Recent), and check 2 pins that this is the ONLY
     exception rather than leaving "reachable" to mean whatever the walker happens to do.
  2. EVERY `run` STRING RESOLVES on a real window. The registry is stringly-typed by necessity
     (help_dialog is a leaf and must not import app), so this is what stops it rotting.
  3. THE CARD AND THE PALETTE AGREE, command by command — same title, same key text.
  4. The palette is LIVE: it primes each menu's own `aboutToShow` before reading it, so the
     enablement it paints is the enablement the menu would show.
  5. A disabled command is listed (so you learn it exists) and cannot be run.

Real offscreen Qt widgets over the same bare-StudioWindow seam tests/test_help_dialog.py uses (no
session loads, no media). The library + prefs stores are BOTH diverted to a temp dir for the whole
module — the palette primes Open Recent, which reads the real index otherwise.
Run: QT_QPA_PLATFORM=offscreen python tests/test_command_palette.py
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from PySide6.QtWidgets import QApplication, QMainWindow, QMenu  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import command_palette, help_dialog, library, prefs, theme, units  # noqa: E402
from studio.command_palette import CommandPalette, entries  # noqa: E402
from studio.help_dialog import COMMANDS, SHORTCUT_GROUPS  # noqa: E402

theme.register_fonts()
theme.apply_theme(_APP)

# BOTH stores, for the whole module: the palette primes every menu, which populates Open Recent
# from the library index — the harness diverts `library` and NOT `prefs`, and this needs both.
_TMP = tempfile.TemporaryDirectory()
library._app_support_dir = prefs._app_support_dir = (lambda: _TMP.name)


def _window():
    """A real StudioWindow with a real menu bar and real shortcuts, no session — the seam
    tests/test_help_dialog.py uses (`__new__` + `QMainWindow.__init__`, so `__init__` never runs
    and no recording loads)."""
    from studio.app import STATUS_MS, StudioWindow
    from studio.export_controller import ExportController
    win = StudioWindow.__new__(StudioWindow)
    QMainWindow.__init__(win)
    win._excluded_visible = True
    win._colorblind = False
    win._speed_unit = units.KMH
    win.exports = ExportController(win, STATUS_MS)
    win._build_menu()
    win._build_shortcuts()
    return win


def _menu_actions(win):
    """Every leaf action on the window's menu bar, as (menu title, action) — an INDEPENDENT read,
    so "reachable" is measured against the menu bar rather than against the palette's own walker.

    It is findChildren-based for the reason `command_palette._menu_entries` documents at length and
    `test_opening_the_palette_does_not_destroy_the_menu_bar` pins: `QAction.menu()` hands this
    binding's Python ownership of a QMenu to the wrapper it returns, so the obvious recursive walk
    deletes the menus it descends into. A test that used the obvious walk would corrupt the window
    it is measuring — and would then agree with a production walker that did the same thing."""
    menus = win.menuBar().findChildren(QMenu)
    openers = [m.menuAction() for m in menus]
    opener_ids = {id(a) for a in openers}
    return [(m.title().replace("&", ""), a) for m in menus for a in m.actions()
            if not a.isSeparator() and id(a) not in opener_ids and a.text().strip()]


# ------------------------------------------------- 1. every menu action is in the palette
def test_every_menu_action_is_reachable_from_the_palette():
    """THE GUARD THIS FILE WAS ASKED FOR, in the shape of test_help_dialog's binding check: a menu
    item added tomorrow is in the palette tomorrow, or the build goes red.

    Matched on the ACTION, not on its text — two menus could legitimately hold the same word, and
    what "reachable" has to mean is that activating the palette row triggers THAT action."""
    win = _window()
    rows = entries(win)
    reachable = {id(e.action) for e in rows if e.action is not None}
    missing = [(menu, a.text()) for menu, a in _menu_actions(win)
               if id(a) not in reachable and not command_palette._is_placeholder(a)]
    assert not missing, (
        f"{len(missing)} menu action(s) cannot be reached from ⌘K: {missing}. "
        f"command_palette.entries() walks the live menu bar — a filter added there has to be "
        f"argued here first.")
    win.deleteLater()   # never shown; close() would run closeEvent's session teardown
    print(f"test_every_menu_action_is_reachable_from_the_palette OK "
          f"({len(_menu_actions(win))} menu actions, {len(rows)} palette rows)")


def test_the_only_menu_rows_the_palette_drops_are_placeholders():
    """Check 1's other half: "reachable except X" is only a contract if X is pinned.

    The one legitimate drop is a PLACEHOLDER — a disabled, unaccelerated, parenthesised row that is
    a sentence about an empty menu rather than a command. On a fresh library that is `(none)` under
    Open Recent, and a palette offering a command called "(none)" is the defect."""
    win = _window()
    rows = entries(win)
    reachable = {id(e.action) for e in rows if e.action is not None}
    dropped = [a for _menu, a in _menu_actions(win) if id(a) not in reachable]
    for action in dropped:
        assert command_palette._is_placeholder(action), (
            f"{action.text()!r} is a real command the palette silently drops")
    # ...and the predicate must not be vacuous: it has to REFUSE a live command.
    live = next(a for _m, a in _menu_actions(win) if a.text().startswith("Open"))
    assert not command_palette._is_placeholder(live), live.text()
    win.deleteLater()
    print(f"test_the_only_menu_rows_the_palette_drops_are_placeholders OK "
          f"({len(dropped)} dropped: {[a.text() for a in dropped]})")


# ------------------------------------------------- 2. the registry's run strings resolve
def test_every_registry_run_resolves_on_a_real_window():
    """`Command.run` names a StudioWindow attribute by STRING, because help_dialog is a leaf that
    must not import app (importing it would drag the whole view stack into a Help dialog). This is
    what makes that safe: a typo, a rename or a deleted method fails the build here rather than
    producing a palette row that does nothing when you press Return."""
    win = _window()
    for cmd in COMMANDS:
        if cmd.run is None:
            continue
        target = getattr(win, cmd.run, None)
        assert target is not None, (
            f"help_dialog.COMMANDS row {cmd.title!r} names run={cmd.run!r}, which StudioWindow "
            f"does not have")
        assert callable(target) or hasattr(target, "trigger"), (cmd.run, type(target).__name__)
    runs = [c.run for c in COMMANDS if c.run]
    assert len(runs) == len(set(runs)), f"two registry rows share a run: {runs}"
    win.deleteLater()
    print(f"test_every_registry_run_resolves_on_a_real_window OK ({len(runs)} runnable rows)")


# ------------------------------------------------- 3. the card IS the registry
def test_the_shortcut_card_is_a_view_of_the_registry():
    """SHORTCUT_GROUPS must be exactly COMMANDS regrouped — no row that only the card has, none
    that only the registry has, and the groups in first-appearance order. This is the sentence
    "the palette and the sheet cannot drift" written as an assertion."""
    assert SHORTCUT_GROUPS == help_dialog.shortcut_groups()
    flat = [(group, key, desc) for group, rows in SHORTCUT_GROUPS for key, desc in rows]
    assert flat == [(c.group, c.key, c.title) for c in COMMANDS], (
        "the card and the registry disagree — SHORTCUT_GROUPS is derived, so this can only mean "
        "somebody wrote a second list")
    titles = [c.title for c in COMMANDS]
    assert len(titles) == len(set(titles)), f"two rows share a description: {titles}"
    print(f"test_the_shortcut_card_is_a_view_of_the_registry OK "
          f"({len(COMMANDS)} commands over {len(SHORTCUT_GROUPS)} groups)")


def test_the_palette_and_the_card_show_the_same_key_for_the_same_command():
    """Check 3, at the pixel the user reads: a registry command's palette row must carry the key
    the card carries. The one row that legitimately differs is the video-fullscreen one, whose CARD
    cell documents the pointer gesture beside the key (`F · double-click video`) and whose PALETTE
    cell is the keystroke — declared as `palette_key`, so the difference is a decision rather than
    a drift."""
    win = _window()
    rows = {e.title: e for e in entries(win)}
    checked = 0
    for cmd in COMMANDS:
        if cmd.run is None or cmd.title not in rows:
            continue
        want = cmd.palette_key or help_dialog._key_text(cmd.key)
        assert rows[cmd.title].key == want, (cmd.title, rows[cmd.title].key, want)
        assert rows[cmd.title].group == cmd.group, (cmd.title, rows[cmd.title].group, cmd.group)
        checked += 1
        if cmd.palette_key:
            # the override must actually be an override, or it is dead weight pretending to be one
            assert cmd.palette_key != help_dialog._key_text(cmd.key), cmd
    assert checked >= 8, f"only {checked} registry commands reached the palette"
    win.deleteLater()
    print(f"test_the_palette_and_the_card_show_the_same_key_for_the_same_command OK "
          f"({checked} commands)")


# ------------------------------------------------- 4. the palette is LIVE
def test_the_palette_primes_each_menu_before_reading_it():
    """Four of this window's menus fill or enable themselves on `aboutToShow` — Open Recent's
    contents, File ▸ Export's enablement, Edit's undo/revert, View's session-dependent items — and
    a palette shows all of them at once without opening any. Without the priming emit it would list
    last session's recents and grey out commands that are live."""
    win = _window()
    fired = []
    menus = win.menuBar().findChildren(QMenu)   # findChildren, for the ownership reason above
    for menu in menus:
        menu.aboutToShow.connect(lambda m=menu: fired.append(m.title()))
    entries(win)
    assert len(fired) == len(menus), (
        f"only {sorted(set(fired))} of {len(menus)} menus were primed")
    win.deleteLater()
    print(f"test_the_palette_primes_each_menu_before_reading_it OK "
          f"({len(fired)} menus primed incl. submenus)")


def test_opening_the_palette_does_not_destroy_the_menu_bar():
    """THE HAZARD THIS FEATURE ALMOST SHIPPED, and the reason `_menu_entries` is shaped the way it
    is. In this PySide binding a QMenu built by `addMenu` is PYTHON-owned, and `QAction.menu()`
    hands that ownership to the wrapper it returns — so the obvious recursive walk of the menu bar
    deletes every menu it descends into the moment those temporary wrappers are collected.
    Measured on the first draft: one walk plus one event-loop turn, and 29 of 31 menu actions
    reported "Internal C++ object (QAction) already deleted"; `menuBar().actions()[0].menu()`
    raised. The user-visible failure would have been "press ⌘K once, and the File menu is empty".

    So: build the palette three times, let the garbage collector and the event loop run in between,
    and read the whole menu bar back each time. Nothing may vanish, and every stored row must still
    be able to name its own action."""
    import gc

    win = _window()
    before = [(m, a.text()) for m, a in _menu_actions(win)]
    counts = []
    for _ in range(3):
        rows = entries(win)
        counts.append(len(rows))
        gc.collect()
        for _ in range(4):
            _APP.processEvents()
        # every held row still resolves — a dead wrapper raises RuntimeError on .text()
        for row in rows:
            if row.action is not None:
                row.action.text()
        del rows
        gc.collect()
        for _ in range(4):
            _APP.processEvents()
    after = [(m, a.text()) for m, a in _menu_actions(win)]
    assert after == before, (
        f"the menu bar changed under the palette: {len(before)} rows -> {len(after)}\n"
        f"  gone: {sorted(set(before) - set(after))}")
    assert len(set(counts)) == 1, f"the palette's own row count moved between opens: {counts}"
    win.deleteLater()
    print(f"test_opening_the_palette_does_not_destroy_the_menu_bar OK "
          f"(3 opens, {len(after)} menu rows intact)")


# ------------------------------------------------- 5. typing, running, and refusing
def test_typing_filters_and_return_runs_the_selected_command():
    """The whole gesture: ⌘K, type, Return. Ranked so the thing you typed the start of comes
    first — "slow" must find slow motion, not a substring match somewhere down the list."""
    win = _window()
    ran = []
    win.slower_playback = lambda: ran.append("slower")   # bound at entry-build time, so patch first
    dialog = CommandPalette(win)
    dialog.search.setText("slow")
    for _ in range(4):
        _APP.processEvents()
    assert dialog._shown, "no command matched 'slow'"
    assert "slow" in dialog._shown[0].title.lower(), [e.title for e in dialog._shown]
    assert dialog.selected() is not None, "nothing was selected, so Return would do nothing"
    dialog._activate()
    assert ran == ["slower"], ran
    # multi-token search reaches across the title AND the menu it lives in
    dialog2 = CommandPalette(win)
    dialog2.search.setText("export csv")
    for _ in range(4):
        _APP.processEvents()
    assert any("CSV" in e.title for e in dialog2._shown), [e.title for e in dialog2._shown]
    # a query that matches nothing says so rather than showing an empty box
    dialog2.search.setText("zzzznotacommand")
    for _ in range(4):
        _APP.processEvents()
    assert not dialog2._shown
    assert "No command matches" in dialog2.hint.text(), dialog2.hint.text()
    dialog.close()
    dialog2.close()
    win.deleteLater()
    print("test_typing_filters_and_return_runs_the_selected_command OK")


def test_the_arrows_move_the_selection_while_the_caret_stays_in_the_field():
    """A palette where you have to tab to the results is not a palette. QLineEdit leaves Up/Down
    unhandled, which means "the parent chain gets them", NOT "the table gets them" — so the dialog
    routes them explicitly, and the selection SKIPS disabled rows (Return must always mean
    something). Driven with real QKeyEvents through the field the caret is in."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    win = _window()
    dialog = CommandPalette(win)
    dialog.show()          # shown, so the __init__ setFocus really lands on the field
    for _ in range(6):
        _APP.processEvents()

    def press(key):
        _APP.sendEvent(dialog.search,
                       QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))
        for _ in range(2):
            _APP.processEvents()

    first = dialog.table.currentRow()
    press(Qt.Key_Down)
    second = dialog.table.currentRow()
    assert second > first, (first, second)
    assert dialog._shown[second].enabled, "the arrows landed on a row Return cannot run"
    assert dialog.search.hasFocus(), "the caret left the search field"
    press(Qt.Key_Up)
    assert dialog.table.currentRow() == first, (first, dialog.table.currentRow())
    # ...and Up at the top clamps rather than wrapping to the bottom of a 40-row list.
    press(Qt.Key_Up)
    assert dialog.table.currentRow() == first, dialog.table.currentRow()
    dialog.close()
    win.deleteLater()
    print("test_the_arrows_move_the_selection_while_the_caret_stays_in_the_field OK")


def test_a_disabled_command_is_listed_but_cannot_be_run():
    """A command you cannot run yet is worth SEEING — that is how you learn the app has it — but a
    palette that runs it anyway is worse than one that hides it. Disabled rows carry Qt's own
    disabled item flags: the theme already says what un-actionable chrome looks like, and an
    unselectable row cannot be activated by a stray Return."""
    win = _window()          # no session: every export is disabled
    dialog = CommandPalette(win)
    dialog.search.setText("Lap times")
    for _ in range(4):
        _APP.processEvents()
    assert dialog._shown, "the disabled export was hidden instead of greyed"
    assert not any(e.enabled for e in dialog._shown), [e.title for e in dialog._shown]
    assert dialog.selected() is None, "a disabled command was selected — Return would run it"
    dialog._activate()       # must be a no-op, not a crash
    dialog.close()
    win.deleteLater()
    print("test_a_disabled_command_is_listed_but_cannot_be_run OK")


def test_a_palette_row_triggers_the_menu_action_it_names():
    """The other end of check 1: reachable has to mean it RUNS."""
    win = _window()
    dialog = CommandPalette(win)
    dialog.search.setText("Keyboard shortcuts")
    for _ in range(4):
        _APP.processEvents()
    entry = dialog._shown[0]
    fired = []
    action = next(a for _m, a in _menu_actions(win) if a.text() == "Keyboard shortcuts")
    action.triggered.disconnect()            # don't open the modal card in a headless run
    action.triggered.connect(lambda: fired.append(1))
    entry.run()
    assert fired == [1], "the palette row did not trigger its menu action"
    dialog.close()
    win.deleteLater()
    print("test_a_palette_row_triggers_the_menu_action_it_names OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} COMMAND-PALETTE TESTS PASSED", flush=True)
