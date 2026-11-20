"""Opening a modal and closing it gives back every widget it made — the widget census, per door.

THE LEAK. ⌘K, File ▸ Library…, the saved-tracks manager and the three right-click menus (lap table,
CORNERS table, Library row) were each built parented to a window or a table, run with `exec()` and
then dropped. Dropping is not deleting: a parented widget that its own lambdas keep alive outlives
its Python name, so each open left its whole tree behind for the rest of the session. Measured on the
synthetic demo, in the real window, per open: palette +1 top-level / +23 widgets, Library +11 / +113
(the dialog, its 7 menus, 2 combo popups), track manager +1 / +13, each context menu +1 QMenu. The
help cards, the export pickers and the message boxes measured +0 and are not repeated here.

THE CHECK. Each door is opened once to warm up (lazy imports, first-build caches), the census taken
— top-level widgets by class, plus every widget — then opened three more times; after the loop is
spun (DeferredDelete + processEvents + gc) the census must be exactly the warm one. The modals run
their REAL `exec()`: a timer closes whatever modal or popup is up, and each open must have been
closed by it, so a door that stopped being modal (and so stopped being exercised) fails too.

Real offscreen widgets over the bare-StudioWindow seam tests/test_command_palette.py uses (no
session, no media) and the synthetic recording tests/test_provenance_panel.py uses for the tables.
The library + prefs stores are diverted to a temp dir for the whole module.
Run: QT_QPA_PLATFORM=offscreen python tests/test_dialog_census.py
"""
import gc
import os
import sys
import tempfile
from collections import Counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()            # module scope, BEFORE any widget: the SHIPPING theme

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow  # noqa: E402

from studio import library, prefs, units  # noqa: E402

_TMP = tempfile.TemporaryDirectory()
library._app_support_dir = prefs._app_support_dir = (lambda: _TMP.name)

_OPENS = 3


def _spin() -> None:
    """Run every deferred delete to completion: two passes, because deleting a dialog posts the
    deletes of what it owned."""
    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        _APP.processEvents()
        gc.collect()


def _census() -> tuple[Counter, int]:
    _spin()
    return (Counter(type(w).__name__ for w in QApplication.topLevelWidgets()),
            len(QApplication.allWidgets()))


class _Closer:
    """Closes the modal or popup on screen, from the event loop that `exec()` runs. Counts what it
    closed, so a door that opened nothing modal cannot pass by default."""

    def __init__(self):
        self.closed = 0
        self._timer = QTimer()
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._tick)

    def _tick(self):
        w = QApplication.activePopupWidget() or QApplication.activeModalWidget()
        if w is None:
            return
        self.closed += 1
        w.reject() if isinstance(w, QDialog) else w.close()

    def __enter__(self):
        self._timer.start()
        return self

    def __exit__(self, *exc):
        self._timer.stop()


def _assert_gives_back(name: str, open_once, modals_per_open: int = 1) -> None:
    with _Closer() as closer:
        open_once()                                    # warm-up
        before = _census()
        for _ in range(_OPENS):
            open_once()
        after = _census()
    assert closer.closed == modals_per_open * (_OPENS + 1), (
        f"{name}: the closer closed {closer.closed} modals over {_OPENS + 1} opens — the door did "
        "not run a real exec(), so this census measured nothing")
    grew = {k: after[0][k] - before[0][k] for k in set(after[0]) | set(before[0])
            if after[0][k] != before[0][k]}
    assert after == before, (
        f"{name}: {_OPENS} opens left {sum(after[0].values()) - sum(before[0].values())} top-level "
        f"and {after[1] - before[1]} widgets behind {grew} — closed but never deleted")
    print(f"ok {name}: {_OPENS} opens, census unchanged ({before[1]} widgets)")


def _window():
    """A real StudioWindow with a real menu bar, no session — tests/test_command_palette.py's seam
    (`__new__` + `QMainWindow.__init__`, so `__init__` never runs and no recording loads)."""
    from studio.app import STATUS_MS, StudioWindow
    from studio.export_controller import ExportController
    from studio.library_controller import LibraryController
    win = StudioWindow.__new__(StudioWindow)
    QMainWindow.__init__(win)
    win._excluded_visible = True
    win._colorblind = False
    win._speed_unit = units.KMH
    win.exports = ExportController(win, STATUS_MS)
    win.library_ctl = LibraryController(win, STATUS_MS)
    win._build_menu()
    win._build_shortcuts()
    return win


def _dispose(*widgets) -> None:
    # deleteLater alone: the bare window's closeEvent reads state its skipped __init__ never set.
    for w in widgets:
        w.deleteLater()
    _spin()


def test_the_command_palette_gives_back_everything_it_made():
    win = _window()
    try:
        _assert_gives_back("⌘K command palette", win.show_command_palette)
    finally:
        _dispose(win)


def test_the_library_gives_back_everything_it_made():
    """The worst of them: the dialog, its seven menus and two combo popups, every open."""
    win = _window()
    try:
        _assert_gives_back("File ▸ Library…", win.library_ctl.open_library)
    finally:
        _dispose(win)


def test_the_saved_tracks_manager_gives_back_everything_it_made():
    win = _window()
    try:
        _assert_gives_back("saved-tracks manager", win.library_ctl._open_track_manager)
    finally:
        _dispose(win)


def _centre(table, row: int, col: int) -> QPoint:
    pos = table.visualItemRect(table.item(row, col)).center()
    assert table.itemAt(pos) is not None and table.itemAt(pos).column() == col
    return pos


def test_the_table_context_menus_give_back_their_menu():
    """The lap table's Time cell and the CORNERS table's Best cell — the two right-clicks that
    open the inspect menu — dismissed each time, so the provenance panel never opens."""
    from test_provenance import _session, _with_sectors

    from studio.lap_table import _TIME_COL, LapTable
    from studio.stats_corners import _CORNER_BEST_COL
    from studio.stats_panel import StatsView

    s = _with_sectors(_session())
    laps = LapTable(s)
    laps.refresh()
    laps.show()
    stats = StatsView(s)
    stats.refresh()
    stats.show()
    try:
        at = _centre(laps.table, 0, _TIME_COL)
        _assert_gives_back("lap table right-click", lambda: laps._on_context_menu(at))
        corners = stats.corners
        at_best = _centre(corners.table, 0, _CORNER_BEST_COL)
        _assert_gives_back("CORNERS right-click", lambda: corners._on_context_menu(at_best))
    finally:
        _dispose(laps, stats)


def test_the_library_row_menu_gives_back_its_menu():
    """Right-click a Library row and dismiss: the menu is the dialog's child, so it piled up for
    as long as the Library stayed open."""
    from studio.library_dialog import LibraryDialog

    with tempfile.TemporaryDirectory() as d:
        clip = os.path.join(d, "GX010001.MP4")
        open(clip, "wb").close()
        row = {"fingerprint": library.fingerprint("GX010001"), "stem": "GX010001",
               "track": "Synthetic demo circuit", "date": "2026-11-01", "lap_count": 8,
               "best": 45.1, "theoretical": 44.8, "verified": True, "degraded": False,
               "dropout": False, "paths": [clip]}
        dlg = LibraryDialog({"version": library.VERSION, "entries": [row]}, lambda _p: None,
                            forget_recording=lambda e: {"entries": [row]},
                            edit_record=lambda e: None)
        dlg.show()
        try:
            at = _centre(dlg.table, 0, 1)
            _assert_gives_back("Library row right-click", lambda: dlg._on_context_menu(at))
        finally:
            _dispose(dlg)


if __name__ == "__main__":
    for _name, _fn in list(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
