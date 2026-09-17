"""Regression tests for the QA-sweep window-chrome findings in studio/app.py (batch B08).

  * L1-02 (HIGH) — Escape did not restore a maximized panel, though FOUR surfaces promised it
    does (the Shortcuts card and all four ⛶ button tooltips). StudioWindow.keyPressEvent gated its
    whole Escape branch on isFullScreen(), which a panel maximize never sets: measured on the real
    app, 12 of 12 (3 window sizes × 4 panels) Escapes moved 0 of 1 296 000 pixels. The gate is now
    three ordered states — video focus, then a maximized panel, then window fullscreen — with
    video focus first because it owns BOTH of the others and either later branch would undo half
    of it. The tests below assert the SPLITTER SIZES come back, not just the flag, at three sizes.

  * L1-06 — on the welcome screen 17 of 25 menu actions stayed enabled and three did literally
    nothing (⌘⇧S Session statistics, Opportunities…, Show excluded laps: view stayed None, the
    status bar stayed empty, no modal appeared). Measured cause: aboutToShow receivers were File 1,
    Edit 1, Coaching 0, View 0, Help 0 — the two menus holding session-only items had no sync at
    all. _sync_coaching_menu / _sync_view_menu now mirror the existing _sync_export_menu /
    _sync_edit_menu, and are seeded at _build_menu (so a disabled action's SHORTCUT is inert too,
    which is what stops ⌘⇧S being a silent no-op before a menu is ever pulled down) and re-run from
    _build_ui.

  * L5-07 — Coaching ▸ Opportunities ▸ Go landed on a 12-row Corners grid with nothing marking the
    row you clicked (the grid is deliberately NoSelection), and silently overwrote the PERSISTED
    lap-panel tab. The jump now makes the matching cid the current cell, scrolls it to the middle
    (it was off-viewport at small window sizes) and names it on the status bar; the tab change is
    flagged as navigation so it is not persisted as a preference.

  * L1-11 — the crash dialog's headline was the app's only lower-case "pacer" rendering, on the
    one surface that appears when the app is already misbehaving. macOS drops a QMessageBox window
    title (asserted below, so nobody "fixes" it with setWindowTitle), so the body is the only
    naming there is.

  * H6 — every window this file built on the synthetic session PASSED while printing two
    tracebacks: a guard in `LibraryController._current_library_entry` swallowed an AttributeError
    from the fixture's `_Laps` double. `_run_all` now fails any test during which a `studio.*`
    guard logs an error, so the next swallowed failure here is a red test, not scrollback.

Run: QT_QPA_PLATFORM=offscreen python tests/test_app_chrome.py
"""
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# The persistence seams, diverted into one temp tree BEFORE any window exists. A real
# StudioWindow reads AND WRITES prefs (the jump test drives a tab change, which persists), so
# without this the suite would rewrite the user's own lap-panel tab / grid layout — the same
# _app_support_dir idiom test_library / test_track_db / test_data_safety already use.
# ALL of them, the rule studio/dev/_jail.py states: every window built on the synthetic session
# reads the session-record store and, now that its library entry resolves (H6), this track's focus
# list — both of which were live against the developer's own app-support dir.
from studio import demo, focus, library, marks, prefs, session_record, sidecar, track_db  # noqa: E402

_SEAMS = tempfile.mkdtemp(prefix="pacer-test-app-chrome-")
for _mod, _name in ((prefs, "prefs"), (library, "library"), (track_db, "track_db"),
                    (demo, "demo"), (focus, "focus"), (marks, "marks"),
                    (session_record, "session_record")):
    _dir = os.path.join(_SEAMS, _name)
    os.makedirs(_dir, exist_ok=True)
    _mod._app_support_dir = (lambda d=_dir: d)
sidecar.sidecar_path = lambda _p, _d=_SEAMS: os.path.join(_d, "test.pacer.json")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from test_central_view_realqt import _studiowindow_with_view  # noqa: E402

from studio import APP_NAME  # noqa: E402
from studio import app as studio_app  # noqa: E402
from studio.app import StudioWindow  # noqa: E402

# Every action the menu bar carries, by the attribute the window keeps it on.
ALL_ACTIONS = [
    "_open_action", "_full_action", "_export_laps_action", "_export_channels_action",
    "_export_report_action", "_share_card_action", "_copy_card_action", "_export_video_action",
    "_library_action", "_reveal_library_action", "_backup_library_action", "_save_track_action",
    "_undo_action", "_ref_action", "_clear_ref_action", "_cross_compare_action",
    "_opportunities_action", "_fullscreen_action", "_stats_action", "_excluded_action",
    "_colorblind_action", "_shortcuts_action", "_privacy_action", "_about_action",
    "_report_action",
]
# The four whose handlers early-return with no session / no view — the L1-06 set.
SESSION_ONLY = ("_stats_action", "_excluded_action", "_ref_action", "_opportunities_action")
# Chrome that genuinely works on the welcome screen and must NOT be swept up by the gate.
ALWAYS_ON = ("_open_action", "_fullscreen_action", "_colorblind_action", "_shortcuts_action",
             "_privacy_action", "_about_action", "_report_action")
# ...and the one that works on the welcome screen only when there is something to show. Library…
# is NOT session-gated — browsing what you analysed last week with nothing loaded is the whole
# point of it — but on a genuinely fresh index it opened a 900x520 dialog with a blank table body
# and a chart pane saying "Select a recording…" with none to select (QA D4-07 / D2-03). So it is
# gated on the INDEX, in both directions, below.
LIBRARY_GATED = "_library_action"


def _settle(n=6):
    for _ in range(n):
        _APP.processEvents()


def _escape(win):
    """A real Escape press+release delivered to the window, the way Qt routes an unused key."""
    for kind in (QEvent.KeyPress, QEvent.KeyRelease):
        _APP.sendEvent(win, QKeyEvent(kind, Qt.Key_Escape, Qt.NoModifier))
    _settle()


def _sizes(view):
    return [view._main_splitter.sizes(), view._left_splitter.sizes(),
            view._right_splitter.sizes()]


def _menus(win):
    """The five QMenus, reached through an action's parent(). NOT menuBar().actions(): those
    wrappers get reaped by Shiboken and take the whole menu bar down with them."""
    return {"File": win._open_action.parent(), "Edit": win._undo_action.parent(),
            "Coaching": win._ref_action.parent(), "View": win._fullscreen_action.parent(),
            "Help": win._shortcuts_action.parent()}


# ============================================================ L1-02 — Escape restores the grid
def test_escape_restores_a_maximized_panel_at_three_window_sizes():
    """Maximize each of the four panels at three window sizes and press Escape: the grid must come
    back to the EXACT pre-maximize splitter sizes. On main every one of the 12 combinations left
    the collapsed [full, 0] sizes untouched."""
    win, view = _studiowindow_with_view(build_menu=True)
    win.show()
    for size in ((1440, 900), (1280, 800), (1720, 1080)):
        win.resize(*size)
        _settle()
        for name in ("_video_panel", "_map_panel", "_table_panel", "_plots_panel"):
            panel = getattr(view, name)
            before = _sizes(view)
            view._toggle_panel_maximized(panel)
            _settle()
            assert view._maximized_panel is panel, name
            assert 0 in view._main_splitter.sizes(), f"{name} did not actually maximize"

            _escape(win)
            assert view._maximized_panel is None, f"{size} {name}: Escape left it maximized"
            assert _sizes(view) == before, f"{size} {name}: {_sizes(view)} != {before}"
    view.dispose()
    win.hide()
    print("test_escape_restores_a_maximized_panel_at_three_window_sizes OK")


def test_escape_still_leaves_video_focus_and_window_fullscreen():
    """The two states that already worked must survive the widened gate — and video focus must be
    tested FIRST inside it, since it owns both a maximized panel and a fullscreen window; a fix
    that let the panel branch win would leave the window stuck in fullscreen."""
    win, view = _studiowindow_with_view(build_menu=True)
    win.show()
    _settle()

    view.set_video_focus(True)
    _settle()
    assert view.is_video_focused() and view._maximized_panel is view._video_panel
    _escape(win)
    assert not view.is_video_focused(), "Escape left video focus on"
    assert view._maximized_panel is None, "Escape left the video panel maximized"
    assert not win.isFullScreen(), "Escape left the window in fullscreen"

    win.showFullScreen()
    _settle()
    if win.isFullScreen():          # the offscreen plugin can refuse the state change
        _escape(win)
        assert not win.isFullScreen(), "Escape no longer exits window fullscreen"

    # Nothing maximized, not fullscreen: Escape must be ignored so it can reach anything else.
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    win.keyPressEvent(ev)
    assert not ev.isAccepted(), "Escape is swallowed even with nothing to back out of"
    view.dispose()
    win.hide()
    print("test_escape_still_leaves_video_focus_and_window_fullscreen OK")


# ============================================================ L1-06 — welcome-screen menu gating
def test_session_only_menu_items_are_disabled_before_the_first_load():
    """On the welcome screen every action whose handler early-returns must report isEnabled()
    False after its menu's aboutToShow — and the chrome that really works there must not be swept
    up with it. On main: 17 of 25 enabled, including all four of these."""
    win = StudioWindow([])
    win.resize(1440, 900)
    win.show()
    _settle(8)
    assert win.view is None

    menus = _menus(win)
    for name in ("Coaching", "View"):
        assert menus[name].receivers("2aboutToShow()") >= 1, \
            f"the {name} menu still has no aboutToShow sync"
    for menu in menus.values():
        menu.aboutToShow.emit()
    _settle()

    for name in SESSION_ONLY:
        action = getattr(win, name)
        assert not action.isEnabled(), f"{name} ({action.text()!r}) is still offered with no session"
    for name in ALWAYS_ON:
        action = getattr(win, name)
        assert action.isEnabled(), f"{name} ({action.text()!r}) was disabled but works with no session"

    # QA D4-07: Library… is gated on the INDEX, not on a session. The seam above points at an empty
    # temp tree, so it is off here — and the tooltip has to say why, since a disabled action's
    # tooltip is the only surface it has left.
    library_action = getattr(win, LIBRARY_GATED)
    assert not library_action.isEnabled(), "an EMPTY library must not offer a browse dialog"
    assert "No recordings analysed yet" in library_action.toolTip(), library_action.toolTip()

    enabled = [n for n in ALL_ACTIONS if getattr(win, n).isEnabled()]
    assert len(enabled) <= 13, f"{len(enabled)} of 25 actions still enabled: {enabled}"
    win.hide()
    print("test_session_only_menu_items_are_disabled_before_the_first_load OK")


def test_library_menu_item_comes_back_the_moment_there_is_a_library():
    """The other direction, which is what keeps the gate from being a session gate in disguise: a
    NON-empty index re-enables Library… on the very same welcome screen, with no session loaded and
    no view built. Driven through the File menu's own aboutToShow, the way a pull-down does it."""
    win = StudioWindow([])
    win.resize(1440, 900)
    win.show()
    _settle(8)
    index = library.load()
    library.upsert(index, {"fingerprint": "fp-app-chrome", "stem": "GX010062",
                           "paths": ["/nowhere/GX010062.MP4"], "track": "Daytona MK",
                           "date": "2026-08-30", "lap_count": 21, "best": 68.201,
                           "theoretical": 67.9})
    library.save(index)
    try:
        assert win.view is None, "still the welcome screen — this is not a session gate"
        _menus(win)["File"].aboutToShow.emit()
        _settle()
        action = getattr(win, LIBRARY_GATED)
        assert action.isEnabled(), "a populated library must be browsable with no session loaded"
        assert "No recordings analysed yet" not in action.toolTip(), action.toolTip()
    finally:
        library.save(library.empty_index())      # leave the shared temp seam as we found it
        win.hide()
    print("test_library_menu_item_comes_back_the_moment_there_is_a_library OK")


def test_the_library_menu_item_names_the_columns_the_dialog_actually_has():
    """F4. `File ▸ Library…`'s tooltip is a description of the dialog's four columns, so it is
    wrong the moment one of them is renamed and nothing else can catch it — the dialog's own tests
    read `_HEADERS`, and this string is on a QAction in another module.

    That is exactly how it broke: #211 renamed the fourth column from "Theoretical" to "Ideal lap"
    (and renamed the noun everywhere else — the chart toggle, the panel header, the chip, the hero,
    the share card) while this tooltip went on offering "theoretical best", a column the dialog no
    longer has. Read structurally off `library_dialog._HEADERS` so the next rename cannot drift
    either.

    The `laps.csv` trailer's "Theoretical best" is deliberately NOT in scope: that label is a
    machine-readable export contract (export_data.SUMMARY_ROWS), not a description of a widget."""
    from studio import library_dialog
    win = StudioWindow([])
    win.resize(1440, 900)
    win.show()
    _settle(4)
    try:
        # The un-gated (feature) text, not the empty-library reason — _gate_action stashes it.
        action = getattr(win, LIBRARY_GATED)
        tip = action.property("featureTip") or action.toolTip()
        low = tip.lower()
        for header in library_dialog._HEADERS:
            assert header.lower() in low, (
                f"the menu item does not name the dialog's {header!r} column: {tip!r}")
        assert "theoretical" not in low, (
            f"the menu item still offers the retired column name: {tip!r}")
    finally:
        win.hide()
    print("test_the_library_menu_item_names_the_columns_the_dialog_actually_has OK")


def test_a_loaded_session_re_enables_them_without_opening_a_menu():
    """The gate must come back UP with the view, at _build_ui time — not on the next pull-down,
    because ⌘⇧S is a shortcut on a disabled action until something re-enables it."""
    win, view = _studiowindow_with_view(build_menu=True)
    _settle()
    for name in SESSION_ONLY:
        action = getattr(win, name)
        assert action.isEnabled(), f"{name} ({action.text()!r}) stayed disabled with a session loaded"
    view.dispose()
    win.hide()
    print("test_a_loaded_session_re_enables_them_without_opening_a_menu OK")


# ============================================================ H6 — the guard behind the tracebacks
def test_the_synthetic_window_resolves_its_own_library_entry_through_the_guard():
    """The record chip, the focus list, File ▸ Session record… and the focus buttons all start from
    `LibraryController._current_library_entry`, and that returns None for two different reasons:
    there is honestly no entry, or building one RAISED and the guard logged it. On main it was the
    second, on every window built here: `Session.session_date` asked the fixture's `_Laps` double
    for `point_count()`, which the real `pacer.Laps` serves and the double did not, so those
    surfaces were only ever exercised on "no library entry".

    Measured before changing anything, so this is a stand-in gap and not a product defect: over a
    real `Session.load` on all ten bundled samples, both D24 recordings, Sandown and SD, the guard
    logged nothing and `library_entry` completed every time.

    The UNGUARDED call comes first, so a double missing an accessor again fails here with that
    accessor's own AttributeError instead of a None. Then the GUARDED call must hand back the same
    entry: the tripwire in `_run_all` catches a guard that logs, and this catches one that returns
    None."""
    win, view = _studiowindow_with_view(build_menu=True)
    try:
        _settle()
        direct = win.session.library_entry(win._paths)
        guarded = win.library_ctl._current_library_entry()
        assert guarded == direct, f"the guard did not pass the entry through: {guarded!r}"
        assert direct["fingerprint"] == library.fingerprint("stadium"), direct
        assert (direct["track"], direct["lap_count"]) == ("StadiumLoop", 2), direct
        assert direct["date"], f"the double's fixes carry no wall clock, so no date: {direct}"
    finally:
        if win._tick_timer is not None:
            win._tick_timer.stop()
        view.dispose()
        win.hide()
    print("test_the_synthetic_window_resolves_its_own_library_entry_through_the_guard OK")


# ============================================================ L5-07 — where Jump lands
def _jump(win, view):
    """Drive a real jump to a corner and return (cid, its row)."""
    cids = list(view.corner_table._cids)
    assert cids, "the synthetic session detected no corners"
    cid = cids[-1]          # the LAST corner: the one a short panel scrolls off
    win._jump_to_opportunity(cid, 0.0)
    _settle(8)
    return cid, view.corner_table._cids.index(cid)


def test_jump_marks_and_reveals_the_corner_row_it_landed_on():
    """The Corners grid is NoSelection by design, so the destination needs the CURRENT cell, a
    scroll, and a sentence. On main: currentRow() == -1, an empty status bar, and at 1100x620 the
    target row's centre outside the viewport."""
    win, view = _studiowindow_with_view(build_menu=True)
    win.show()
    win.resize(1100, 620)
    _settle(8)
    view.select_lap_tab(1)
    _settle()
    inner = view.corner_table.table
    inner.scrollToTop()
    _settle()

    cid, row = _jump(win, view)
    assert inner.currentRow() == row, f"current row {inner.currentRow()} != the clicked cid's {row}"
    rect = inner.visualItemRect(inner.item(row, 0))
    assert inner.viewport().rect().contains(rect.center()), \
        f"row {row} for cid {cid} is off-viewport after the jump: {rect} in {inner.viewport().rect()}"
    msg = win.statusBar().currentMessage()
    assert msg, "the jump said nothing about where it landed"
    assert inner.item(row, 0).text().split()[0] in msg, f"{msg!r} does not name the corner"
    view.dispose()
    win.hide()
    print("test_jump_marks_and_reveals_the_corner_row_it_landed_on OK")


def test_jump_does_not_overwrite_the_persisted_lap_panel_tab():
    """Navigating to Corners for the user is not the user choosing Corners: on main the jump wrote
    tab 1 into prefs, so quitting from a jump reopened the app on a page nobody picked."""
    win, view = _studiowindow_with_view(build_menu=True)
    win.show()
    _settle()
    win._lap_panel_tab = 3            # the user is on Coaching
    written = []
    real_set = studio_app.prefs.set_lap_panel_tab
    studio_app.prefs.set_lap_panel_tab = written.append
    try:
        _jump(win, view)
    finally:
        studio_app.prefs.set_lap_panel_tab = real_set
    assert view.tab_bar.currentIndex() == 1, "the jump did not navigate to Corners"
    assert win._lap_panel_tab == 3, f"the jump overwrote the tab preference with {win._lap_panel_tab}"
    assert written == [], f"the jump persisted a tab preference: {written}"

    # ...and a REAL tab choice still persists.
    view.select_lap_tab(2)
    _settle()
    assert win._lap_panel_tab == 2, "a real tab change stopped being remembered"
    view.dispose()
    win.hide()
    print("test_jump_does_not_overwrite_the_persisted_lap_panel_tab OK")


# ============================================================ L1-11 — the crash dialog's naming
def test_the_crash_dialog_names_the_product():
    """The one surface shown when the app is already misbehaving must call the app what everything
    else calls it. Also pins WHY the body has to carry it: macOS gives a QMessageBox no window
    title, so a setWindowTitle() "fix" would change nothing a user sees."""
    try:
        raise ValueError("lap 7 has no GPS fix in the timing window")
    except ValueError:
        et, ev, tb = sys.exc_info()
    saved = QMessageBox.exec
    QMessageBox.exec = lambda self, *a, **k: 0
    try:
        box = studio_app._show_error_report(et, ev, tb)
    finally:
        QMessageBox.exec = saved
    body = box.text()
    assert APP_NAME in body, f"the crash dialog never names {APP_NAME}: {body!r}"
    assert "pacer " not in body, f"the crash dialog still renders a lower-case 'pacer': {body!r}"
    assert box.windowTitle() == "", \
        "macOS started keeping QMessageBox window titles — the body-carries-the-name note is stale"
    box.close()
    print("test_the_crash_dialog_names_the_product OK")


# ============================================================ U3 — menu truth on macOS
# WHY A TOOLTIP IS NOT AN EXPLANATION HERE. Measured on this machine, on the real screen (cocoa,
# Built-in Retina Display 1512x982 @2x): `menuBar().isNativeMenuBar()` is True and the bar's
# in-window height is 0 px — the items the user clicks are NSMenuItems outside the window, so
# QMenu's own tooltip handler never runs for them. And that handler is switched off anyway: all
# eight of this app's menus report `toolTipsVisible() == False`, and a live QHelpEvent on a real
# popup showed nothing at False and the full sentence at True. So an explanation that lives only in
# `setToolTip` is unreachable — 19 of the 20 items disabled on the welcome screen relied on one.
_REASON_SEP = " — "


def _readable_actions(win):
    """Every action a user can READ in the menu bar: the leaves, plus a submenu OPENER that is
    itself disabled. File ▸ Export greys the whole submenu, which takes its six items (and their
    six reasons) off the screen with it, so the opener has to carry a reason of its own.

    findChildren, never `action.menu()`, for the ownership reason `_menus` documents."""
    from PySide6.QtWidgets import QMenu
    menus = win.menuBar().findChildren(QMenu)
    openers = {id(m.menuAction()) for m in menus}
    out = []
    for menu in menus:
        for action in menu.actions():
            if action.isSeparator() or not action.text().strip():
                continue
            if id(action) in openers and action.isEnabled():
                continue            # an open submenu explains itself through its own items
            out.append((menu.title().replace("&", ""), action))
    return out


def _welcome_window():
    """A real StudioWindow on the welcome screen with every menu's aboutToShow fired, i.e. the
    enablement a pull-down would paint."""
    win = StudioWindow([])
    win.resize(1440, 900)
    win.show()
    _settle(8)
    for menu in _menus(win).values():
        menu.aboutToShow.emit()
    _settle()
    return win


def test_every_disabled_menu_item_says_why_in_the_words_the_menu_shows():
    """THE U3 GUARD. A greyed item with no reason is a silent refusal, which is the defect class
    this app fixes rather than ships. The reason has to be in the item's own TEXT, because on macOS
    that is the only part of a menu item a user can read.

    Each disabled item's text must carry the CONDITION half of its reason — the clause before the
    remedy — and an enabled item must carry none of it, or the gate would be shouting at a user who
    is not blocked."""
    win = _welcome_window()
    try:
        silent, shouting = [], []
        for title, action in _readable_actions(win):
            text = action.text()
            if action.isEnabled():
                if _REASON_SEP in text:
                    shouting.append((title, text))
                continue
            if text.strip().startswith("("):
                continue            # the "(none)" placeholder IS its own explanation
            head = action.toolTip().split(_REASON_SEP)[0].strip().rstrip(".:")
            if not head or head.lower() not in text.lower():
                silent.append((title, text, action.toolTip()[:70]))
        assert not silent, (
            f"{len(silent)} disabled menu item(s) explain themselves nowhere the macOS menu shows. "
            f"A tooltip is not a surface here (see the block above this test):\n" +
            "\n".join(f"  {m:<10} {t!r}  tooltip={tip!r}" for m, t, tip in silent))
        assert not shouting, f"an ENABLED item carries a gate clause: {shouting}"
        # ...and the guard must not be vacuous: the welcome screen really does gate things.
        gated = [t for _m, a in _readable_actions(win) if not a.isEnabled()
                 and _REASON_SEP in (t := a.text())]
        assert len(gated) >= 15, f"only {len(gated)} gated items found: {gated}"
    finally:
        win.hide()
    print(f"test_every_disabled_menu_item_says_why_in_the_words_the_menu_shows OK "
          f"({len(_readable_actions(win))} readable actions)")


def test_the_reason_comes_back_off_the_item_when_the_gate_opens():
    """The other direction, and the idempotence that makes it safe to re-run on every aboutToShow:
    gating twice must not append twice, and opening the gate must restore the command's own name
    exactly — a menu item that keeps a stale "no complete laps" clause after a load would be a
    worse lie than the silence it replaced."""
    win = _welcome_window()
    try:
        action = win._library_action
        reason = "No recordings analysed yet — open a GoPro recording and it is remembered here."
        win._gate_action(action, False, reason)
        off = action.text()
        assert "no recordings analysed yet" in off.lower(), off
        win._gate_action(action, False, reason)
        assert action.text() == off, f"the reason was appended twice: {action.text()!r}"
        win._gate_action(action, True, reason)
        assert action.text() == "Library…", action.text()
        assert _REASON_SEP not in action.text(), action.text()
        assert action.isEnabled()
    finally:
        win.hide()
    print("test_the_reason_comes_back_off_the_item_when_the_gate_opens OK")


def test_the_library_has_a_key_and_the_card_and_the_palette_agree_on_it():
    """⌘L. The Library is the app's front door to everything analysed before today and was the one
    top-level surface with no key at all. The key is asserted against the LIVE action and both
    generated surfaces — help_dialog's card (which test_help_dialog would fail on its own if the
    row were missing) and the ⌘K palette row — so the three cannot drift."""
    from PySide6.QtGui import QKeySequence

    from studio import command_palette, help_dialog
    win = _welcome_window()
    try:
        want = QKeySequence("Ctrl+L").toString(QKeySequence.NativeText)
        got = win._library_action.shortcut().toString(QKeySequence.NativeText)
        assert got == want, f"File ▸ Library… is bound to {got!r}, not {want!r}"
        keys = [a.shortcut().toString(QKeySequence.NativeText)
                for _t, a in _readable_actions(win) if not a.shortcut().isEmpty()]
        assert keys.count(want) == 1, f"{want} is bound to more than one menu item: {keys}"
        documented = {help_dialog._key_text(k)
                      for _g, rows in help_dialog.SHORTCUT_GROUPS for k, _d in rows}
        assert want in documented, f"the shortcut card does not document {want}: {sorted(documented)}"
        row = next(e for e in command_palette.entries(win) if e.title.startswith("Library"))
        assert row.key == want, (row.title, row.key)
    finally:
        win.hide()
    print("test_the_library_has_a_key_and_the_card_and_the_palette_agree_on_it OK")


def test_opportunities_and_the_copy_that_points_at_it_spell_it_the_same_way():
    """The app's recorded ellipsis convention (app.py, above the Help menu): a trailing "…" means
    the command needs MORE INFORMATION before it can run. Opportunities asks for nothing — it opens
    a ranking — so it carries none, and the in-app copy that names the menu path is asserted
    against the ACTION's own text rather than a second literal, which is how the pair went out of
    step in the first place."""
    from studio import coaching_panel
    win = _welcome_window()
    try:
        action = win._opportunities_action
        win._gate_action(action, True, "")      # read the command's own name, not its gate clause
        text = action.text()
        assert not text.endswith("…"), (
            f"{text!r} carries an ellipsis, but it asks the user for nothing")
        assert f"Coaching ▸ {text}" in coaching_panel._SCOPE_TOOLTIP, (
            f"the coaching page points at 'Coaching ▸ …' with different words than the menu item "
            f"{text!r}: {coaching_panel._SCOPE_TOOLTIP}")
    finally:
        win.hide()
    print("test_opportunities_and_the_copy_that_points_at_it_spell_it_the_same_way OK")


# ================================================== U4 — the window's own size and place persist
# Measured on the real app before this shipped (jailed prefs, offscreen): a window resized to
# 1100x720 at (240,160) and closed left prefs.json holding `{}`, and the next window opened at
# 1440x900, (0,0). Nothing about the window itself was remembered, while the Shortcuts card's
# "the layout is remembered" row told the user their layout was.
#
# NOTE ON POSITIONS: the offscreen platform reports a 2 px frame inset (a window moved to (240,160)
# reports normalGeometry (242,162)), so positions are asserted within a few px while SIZES — which
# round-trip exactly — are asserted exactly.
_FRAME_SLACK_PX = 4


def _armed_window():
    """A real StudioWindow with geometry persistence armed the way `main` arms it (and only `main`:
    a bare StudioWindow must never write the developer's prefs — asserted below)."""
    win = StudioWindow([])
    win.restore_window_geometry()
    win.show()
    _settle(8)
    return win


def test_fit_window_to_screens_never_opens_a_window_you_cannot_see():
    """The restore policy, on INJECTED screens so nothing depends on the machine running the suite
    (the library dialog's `_fit_to_screen` is pinned the same way). A window restored off-screen is
    a running app with no visible window and no way to reach it, so the position is honoured only
    while the window's centre is still on a live screen."""
    fit = studio_app._fit_window_to_screens
    bar = studio_app._TITLE_BAR_PX
    laptop = (0, 38, 1470, 893)         # a 13" Air's available area, menu bar excluded
    external = (-1920, 0, 1920, 1080)   # a monitor to the LEFT of it — negative coordinates

    # It already fits where it is: left exactly alone.
    assert fit((120, 100, 1180, 760), [laptop]) == (120, 100, 1180, 760)
    # With that monitor attached, a window living on it stays on it.
    assert fit((-1700, 120, 1180, 760), [laptop, external]) == (-1700, 120, 1180, 760)
    # THE ONE THAT MATTERS: the monitor is gone. The SIZE survives; the position is dropped and the
    # window is centred on the primary, instead of opening 1920 px to the left of everything.
    x, y, w, h = fit((-1700, 120, 1180, 760), [laptop])
    assert (w, h) == (1180, 760), (w, h)
    assert laptop[0] <= x and x + w <= laptop[0] + laptop[2], (x, w)
    assert laptop[1] + bar <= y and y + h <= laptop[1] + laptop[3], (y, h)
    # Bigger than the screen it lands on: clamped, and the title bar stays below the menu bar —
    # geometry() excludes the frame, so a y at the very top of the available area hides the bar.
    assert fit((0, 0, 3000, 2000), [laptop]) == (0, laptop[1] + bar, 1470, 893 - bar)
    # Hanging off the bottom-right, centre still on the screen: nudged fully inside, NOT shrunk.
    assert fit((600, 400, 1180, 760), [laptop]) == (1470 - 1180, 38 + 893 - 760, 1180, 760)
    # Hanging off so far that the CENTRE has left the screen (a rearranged desk, a screen that
    # shrank): that is not a position worth keeping, so it is re-centred like a vanished display
    # rather than nudged back by a whole window's width.
    x, y, w, h = fit((1400, 800, 1180, 760), [laptop])
    assert (w, h) == (1180, 760) and 0 <= x and y >= laptop[1] + bar, (x, y, w, h)
    # No screen at all — nothing can be decided, so the caller keeps its built-in default.
    assert fit((0, 0, 1180, 760), []) is None
    print("test_fit_window_to_screens_never_opens_a_window_you_cannot_see OK")


def test_the_window_reopens_at_the_size_it_was_left_and_only_the_app_arms_that():
    """The feature, end to end on a real window: resize, close, reopen the same size. And the other
    half of the contract — a StudioWindow built by a harness (every test file here, ui_capture,
    _smoke, the probes) neither moves itself nor writes to prefs, because the persistence is armed
    by `main` alone."""
    prefs.save({})
    first = _armed_window()
    assert (first.width(), first.height()) == (1440, 900), "nothing stored yet: the default stands"
    first.setGeometry(60, 80, 640, 560)
    _settle()
    first.close()
    _settle()
    stored = prefs.window_geometry()
    assert stored is not None, "closing an armed window stored nothing"
    assert stored[2:] == (640, 560), stored
    assert abs(stored[0] - 60) <= _FRAME_SLACK_PX and abs(stored[1] - 80) <= _FRAME_SLACK_PX, stored

    again = _armed_window()
    assert (again.width(), again.height()) == (640, 560), (again.width(), again.height())
    assert abs(again.x() - stored[0]) <= _FRAME_SLACK_PX, (again.x(), stored)
    assert abs(again.y() - stored[1]) <= _FRAME_SLACK_PX, (again.y(), stored)
    again.close()
    _settle()

    # A harness window: never restored (it keeps the built-in default) and never persisted.
    bare = StudioWindow([])
    bare.show()
    _settle(8)
    assert (bare.width(), bare.height()) == (1440, 900), (bare.width(), bare.height())
    bare.setGeometry(70, 70, 700, 600)
    _settle()
    bare.close()
    _settle()
    assert prefs.window_geometry() == stored, (
        "an un-armed StudioWindow wrote its own size into the user's prefs")
    print("test_the_window_reopens_at_the_size_it_was_left_and_only_the_app_arms_that OK")


def test_quitting_from_full_screen_remembers_the_window_not_the_whole_display():
    """⌘⌃F — and the ⤢ video focus, which puts the WINDOW into full screen — make geometry() the
    entire display. Persisting THAT would reopen the app screen-filling on every future launch, and
    grow the stored size to every screen it is ever full-screened on. normalGeometry keeps the
    window's own frame through both full screen and zoom."""
    prefs.save({})
    win = _armed_window()
    win.setGeometry(70, 90, 660, 520)
    _settle()
    win.showFullScreen()
    _settle(8)
    assert win.isFullScreen()
    screen = _APP.primaryScreen().geometry()
    assert (win.width(), win.height()) == (screen.width(), screen.height()), (
        "the harness did not actually go full screen, so this proves nothing")
    win.close()
    _settle()
    stored = prefs.window_geometry()
    assert stored is not None and stored[2:] == (660, 520), (
        f"quitting from full screen persisted {stored}, not the window's own 660x520 frame")

    back = _armed_window()
    assert (back.width(), back.height()) == (660, 520), (back.width(), back.height())
    back.close()
    _settle()
    print("test_quitting_from_full_screen_remembers_the_window_not_the_whole_display OK")


def test_a_window_stored_on_a_display_that_is_gone_opens_where_it_can_be_seen():
    """The unplugged-monitor case, driven through the REAL window rather than the pure helper: a
    rect from a screen that no longer exists must not reach setGeometry as it stands. The size is
    kept; the window comes back on a screen that is actually there."""
    prefs.save({})
    prefs.set_window_geometry(-3400, -1200, 700, 560)
    win = _armed_window()
    avail = _APP.primaryScreen().availableGeometry()
    rect = win.geometry()
    assert (rect.width(), rect.height()) == (700, 560), (rect.width(), rect.height())
    assert rect.x() >= avail.x() - _FRAME_SLACK_PX, (rect, avail)
    assert rect.y() >= avail.y() - _FRAME_SLACK_PX, (rect, avail)
    assert rect.x() + rect.width() <= avail.x() + avail.width() + _FRAME_SLACK_PX, (rect, avail)
    assert rect.y() + rect.height() <= avail.y() + avail.height() + _FRAME_SLACK_PX, (rect, avail)
    win.close()
    _settle()
    print("test_a_window_stored_on_a_display_that_is_gone_opens_where_it_can_be_seen OK")


def test_main_restores_before_it_shows_and_saves_on_the_quit_that_sends_no_close_event():
    """`main` is the only place persistence is armed, so main's BODY is the contract — and two
    measured facts pin its shape. (a) The restore runs BEFORE `show`: a top-level window takes
    setGeometry before it is shown and keeps it, so the first frame is the user's own window rather
    than the default resized a beat later (what the grid-splitter restore had to fix for the panels
    inside it). (b) `QApplication.quit()` delivered ZERO close events to a shown window where an
    explicit `close()` delivered one — so closeEvent alone is not a quit path, and aboutToQuit is
    wired too."""
    import ast
    import inspect
    import textwrap

    fn = ast.parse(textwrap.dedent(inspect.getsource(studio_app.main))).body[0]
    order = [node.func.attr for stmt in fn.body for node in ast.walk(stmt)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
    assert "restore_window_geometry" in order, "main never restores the window's geometry"
    assert "show" in order
    assert order.index("restore_window_geometry") < order.index("show"), (
        "main shows the window before restoring its geometry — the user watches the default paint "
        "first and then jump")
    connects = [n for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "connect" and isinstance(n.func.value, ast.Attribute)
                and n.func.value.attr == "aboutToQuit"]
    assert len(connects) == 1, f"aboutToQuit is wired {len(connects)} times in main"
    handler = connects[0].args[0]
    assert isinstance(handler, ast.Attribute) and handler.attr == "persist_window_geometry", (
        ast.dump(handler))
    print("test_main_restores_before_it_shows_and_saves_on_the_quit_that_sends_no_close_event OK")


class _GuardedErrors(logging.Handler):
    """Every ERROR a `studio.*` logger records while one test runs.

    An `except Exception: _log.exception(...)` guard is right for the app, because a menu item or a
    decorative chip must never raise into the UI. In a test it is wrong: the test passes, the
    traceback scrolls past, and everyone learns to ignore it. That is how a missing accessor on a
    test double printed 142 tracebacks across ten files and failed nothing (H6)."""

    def __init__(self):
        super().__init__(logging.ERROR)
        self.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        self.records = []

    def emit(self, record):
        self.records.append(self.format(record))


def _run_failing_on_guarded_errors(test):
    """Run one test with a recorder on the `studio` logger, the ancestor of every studio module's
    logger, and fail THAT test by name if anything was logged at ERROR. A handler also stops
    logging's last-resort stderr print, so the traceback is carried in the failure instead."""
    recorder = _GuardedErrors()
    studio_log = logging.getLogger("studio")
    studio_log.addHandler(recorder)
    try:
        test()
    finally:
        studio_log.removeHandler(recorder)
    assert not recorder.records, (
        f"{test.__name__} passed while a guard swallowed {len(recorder.records)} error(s):\n"
        + "\n".join(recorder.records))


def _run_all():
    for test in (
        test_escape_restores_a_maximized_panel_at_three_window_sizes,
        test_escape_still_leaves_video_focus_and_window_fullscreen,
        test_session_only_menu_items_are_disabled_before_the_first_load,
        test_library_menu_item_comes_back_the_moment_there_is_a_library,
        test_the_library_menu_item_names_the_columns_the_dialog_actually_has,
        test_a_loaded_session_re_enables_them_without_opening_a_menu,
        test_the_synthetic_window_resolves_its_own_library_entry_through_the_guard,
        test_every_disabled_menu_item_says_why_in_the_words_the_menu_shows,
        test_the_reason_comes_back_off_the_item_when_the_gate_opens,
        test_the_library_has_a_key_and_the_card_and_the_palette_agree_on_it,
        test_opportunities_and_the_copy_that_points_at_it_spell_it_the_same_way,
        test_jump_marks_and_reveals_the_corner_row_it_landed_on,
        test_jump_does_not_overwrite_the_persisted_lap_panel_tab,
        test_the_crash_dialog_names_the_product,
        test_fit_window_to_screens_never_opens_a_window_you_cannot_see,
        test_the_window_reopens_at_the_size_it_was_left_and_only_the_app_arms_that,
        test_quitting_from_full_screen_remembers_the_window_not_the_whole_display,
        test_a_window_stored_on_a_display_that_is_gone_opens_where_it_can_be_seen,
        test_main_restores_before_it_shows_and_saves_on_the_quit_that_sends_no_close_event,
    ):
        _run_failing_on_guarded_errors(test)
    print("ALL OK")


if __name__ == "__main__":
    _run_all()
