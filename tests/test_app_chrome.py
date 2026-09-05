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

Run: QT_QPA_PLATFORM=offscreen python tests/test_app_chrome.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# The four persistence seams, diverted into one temp tree BEFORE any window exists. A real
# StudioWindow reads AND WRITES prefs (the jump test drives a tab change, which persists), so
# without this the suite would rewrite the user's own lap-panel tab / grid layout — the same
# _app_support_dir idiom test_library / test_track_db / test_data_safety already use.
from studio import library, prefs, sidecar, track_db  # noqa: E402

_SEAMS = tempfile.mkdtemp(prefix="pacer-test-app-chrome-")
for _mod, _name in ((prefs, "prefs"), (library, "library"), (track_db, "track_db")):
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


def _run_all():
    test_escape_restores_a_maximized_panel_at_three_window_sizes()
    test_escape_still_leaves_video_focus_and_window_fullscreen()
    test_session_only_menu_items_are_disabled_before_the_first_load()
    test_library_menu_item_comes_back_the_moment_there_is_a_library()
    test_a_loaded_session_re_enables_them_without_opening_a_menu()
    test_jump_marks_and_reveals_the_corner_row_it_landed_on()
    test_jump_does_not_overwrite_the_persisted_lap_panel_tab()
    test_the_crash_dialog_names_the_product()
    print("ALL OK")


if __name__ == "__main__":
    _run_all()
