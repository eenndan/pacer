"""Opening a modal and closing it gives back every widget it made — the widget census, per door.

THE LEAK. ⌘K, File ▸ Library…, the saved-tracks manager and the three right-click menus (lap table,
CORNERS table, Library row) were each built parented to a window or a table, run with `exec()` and
then dropped. Dropping is not deleting: a parented widget that its own lambdas keep alive outlives
its Python name, so each open left its whole tree behind for the rest of the session. Measured on the
synthetic demo, in the real window, per open: palette +1 top-level / +23 widgets, Library +11 / +113
(the dialog, its 7 menus, 2 combo popups), track manager +1 / +13, each context menu +1 QMenu. The
help cards, the export pickers and the message boxes measured +0 and are not repeated here.
The video export's progress modal was the seventh (0.19.0 QA, re-measured on 5 real renders of
the demo: +1 top-level / +4 widgets per export, never levelling off; its "export finished" box was
freed all along). The eighth is not modal: "Inspect this number…"'s ProvenancePanel, closed but
kept until the window went (+1 top-level / ~52 widgets per inspection).

THE CHECK. Each door is opened once to warm up (lazy imports, first-build caches), the census taken
— top-level widgets by class, plus every widget — then opened three more times; after the loop is
spun (DeferredDelete + processEvents + gc) the census must be exactly the warm one. The modals run
their REAL `exec()`: a timer closes whatever modal or popup is up, and each open must have been
closed by it, so a door that stopped being modal (and so stopped being exercised) fails too.

THE KEYBOARD (cool/dialogs-work-from-the-keyboard): the same doors, and the rest of the app's
dialogs, driven with real keys — a default button that is never destructive, Esc that closes,
Tab that reaches everything in reading order and leaves every table and text box. See the
section at the bottom; its own planted dialogs prove each rule can fail.

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

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QThread, QTimer, Signal  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
)
from shiboken6 import isValid  # noqa: E402

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

    def __init__(self, progress: str = "reject"):
        # `progress`: what a QProgressDialog gets — "reject" like any modal, "wait" (it is left to
        # the render it reports on, and not counted), or "cancel" (its Cancel, as a user presses it).
        self.closed = 0
        self._progress = progress
        self._timer = QTimer()
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._tick)

    def _tick(self):
        w = QApplication.activePopupWidget() or QApplication.activeModalWidget()
        if w is None:
            return
        if isinstance(w, QProgressDialog) and self._progress != "reject":
            if self._progress == "cancel" and w.isVisible():
                self.closed += 1
                w.cancel()
            return
        self.closed += 1
        w.reject() if isinstance(w, QDialog) else w.close()

    def __enter__(self):
        self._timer.start()
        return self

    def __exit__(self, *exc):
        self._timer.stop()


def _assert_gives_back(name: str, open_once, modals_per_open: int = 1,
                       progress: str = "reject") -> None:
    with _Closer(progress) as closer:
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


class _Inspector:
    """Picks "Inspect this number…" in the right-click menu its real `exec()` shows (the keys a user
    presses), then presses the panel's own Close once it is up. The panel is not modal — it stays
    beside the window — so it is found among the top-levels, not as the active modal."""

    def __init__(self):
        self.opened = self.closed = 0
        self._timer = QTimer()
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._tick)

    def _tick(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QDialogButtonBox

        from studio.provenance_panel import ProvenancePanel
        menu = QApplication.activePopupWidget()
        if menu is not None:
            QTest.keyClick(menu, Qt.Key_Down)
            QTest.keyClick(menu, Qt.Key_Return)
            return
        for w in QApplication.topLevelWidgets():
            if isinstance(w, ProvenancePanel) and w.isVisible():
                self.opened += 1
                w.findChild(QDialogButtonBox).button(QDialogButtonBox.Close).click()
                self.closed += 1

    def __enter__(self):
        self._timer.start()
        return self

    def __exit__(self, *exc):
        self._timer.stop()


def _panels() -> list:
    from studio.provenance_panel import ProvenancePanel
    _spin()
    return [w for w in QApplication.topLevelWidgets() if isinstance(w, ProvenancePanel)]


def test_inspect_this_number_gives_back_its_panel():
    """"Inspect this number…" opened a ProvenancePanel beside the window and its Close only hid it:
    parented to the window, each closed panel (~53 widgets) stayed until the window went (the
    demo, 20 opens: +20 top-level, +1,060 widgets). Through the lap table's real right-click
    menu and the panel's real Close button; then the panel is opened twice at once, and the
    table is closed with one still open, which must take it along and leave nothing dangling."""
    from test_provenance import _session

    from studio.lap_table import _TIME_COL, LapTable

    laps = LapTable(_session())
    laps.refresh()
    laps.show()
    try:
        at = _centre(laps.table, 0, _TIME_COL)

        def inspect_and_close():
            closed = ins.closed
            laps._on_context_menu(at)
            for _ in range(400):                      # the inspector's next tick presses Close
                if ins.closed > closed:
                    break
                _APP.processEvents()
                QThread.msleep(5)

        with _Inspector() as ins:
            inspect_and_close()                       # warm-up
            before = _census()
            for _ in range(_OPENS):
                inspect_and_close()
            after = _census()
        assert ins.opened == ins.closed == _OPENS + 1, (ins.opened, ins.closed)
        assert after == before, (
            f"{_OPENS} inspections left {sum(after[0].values()) - sum(before[0].values())} "
            f"top-level and {after[1] - before[1]} widgets behind — closed but never deleted")
        print(f"ok Inspect this number…: {_OPENS} opens, census unchanged ({before[1]} widgets)")

        # Two at once (a second right-click while the first panel is open), then the first closed:
        # the second is untouched and still answers.
        from studio import provenance_panel
        prov = laps.session.lap_time_provenance(laps.session.best_lap_id())
        one = provenance_panel.open_for(prov, laps)
        two = provenance_panel.open_for(prov, laps)
        assert len(_panels()) == 2
        one.close()
        assert _panels() == [two] and two.isVisible() and two.copy_csv()
        # The table closed and deleted with a panel still open takes the panel along.
        laps.close()
        laps.deleteLater()
        laps = None
        assert _panels() == [], _panels()
    finally:
        if laps is not None:
            _dispose(laps)


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


def test_the_library_with_use_as_reference_gives_back_everything_after_twenty_opens():
    """cool/library-reference: with a session open the Library builds one more button and its row
    menu one more entry (with its hover). Twenty real exec() opens of each leave the census flat."""
    from studio.library_dialog import LibraryDialog

    with tempfile.TemporaryDirectory() as d:
        rows = []
        for stem in ("GX010001", "GX010002"):
            clip = os.path.join(d, f"{stem}.MP4")
            open(clip, "wb").close()
            rows.append({"fingerprint": library.fingerprint(stem), "stem": stem,
                         "track": "Synthetic demo circuit", "date": "2026-11-01", "lap_count": 8,
                         "best": 45.1, "theoretical": 44.8, "verified": True, "degraded": False,
                         "dropout": False, "paths": [clip]})
        index = {"version": library.VERSION, "entries": rows}

        def build():
            return LibraryDialog(index, lambda _p: None, forget_recording=lambda e: index,
                                 edit_record=lambda e: None, use_as_reference=lambda e: None,
                                 reference_refusal=lambda e: library.reference_refusal(e, rows[0]))

        def open_library():
            dlg = build()
            assert not dlg.reference_btn.isHidden()
            dlg.exec()
            dlg.deleteLater()

        with _Closer() as closer:
            open_library()
            before = _census()
            for _ in range(20):
                open_library()
            after = _census()
        assert closer.closed == 21, closer.closed
        assert after == before, (before, after)

        dlg = build()
        dlg.show()
        try:
            at = _centre(dlg.table, 0, 1)
            with _Closer() as closer:
                dlg._on_context_menu(at)
                before = _census()
                for _ in range(20):
                    dlg._on_context_menu(at)
                after = _census()
            assert closer.closed == 21, closer.closed
            assert after == before, (before, after)
        finally:
            _dispose(dlg)
    print("ok Library with Use as reference: 20 opens and 20 row menus, census unchanged")


class _Render(QThread):
    """VideoExportWorker's signal surface with no thread and no ffmpeg: started, it finishes from
    the progress modal's own loop (where a real worker's queued signal lands); cancelled, it
    reports "cancelled" the same way."""

    progress = Signal(int, int)
    finished_export = Signal(bool, str)

    def __init__(self, _session, _spec, _make_renderer=None):
        super().__init__()

    def start(self):
        QTimer.singleShot(0, lambda: self.finished_export.emit(True, ""))

    def cancel(self):
        QTimer.singleShot(0, lambda: self.finished_export.emit(False, "cancelled"))

    def wait(self, *_a, **_k):
        return True


class _Spec:
    """The fields the export queue reads off a spec: where it wrote and its source's cleanup."""

    def __init__(self, out_path):
        self.out_path, self.lap_id = out_path, 0
        self.source = type("_Src", (), {"cleanup": lambda _self: None})()


def test_the_video_export_gives_back_its_progress_dialog():
    """Each export's progress modal stayed parented to the window after its render: one
    QProgressDialog and its 3 children per export, every export (the demo, 5 real renders: +5
    top-level, +20 widgets; 0.19.0 QA). The REAL queue, its progress modal and its "export
    finished" box running their real `exec()`, the worker stood in for. Finished (the box is
    closed, the progress modal left to its render) and cancelled (its Cancel pressed)."""
    from studio import export_controller

    win = _window()
    win.session, win._load_workers, win._video_worker = None, set(), None
    real = export_controller.VideoExportWorker
    export_controller.VideoExportWorker = _Render
    try:
        with tempfile.TemporaryDirectory() as d:
            spec = _Spec(os.path.join(d, "lap.mp4"))
            _assert_gives_back("video export, finished",
                               lambda: win.exports._run_video_export([spec], 0), progress="wait")
            _assert_gives_back("video export, cancelled",
                               lambda: win.exports._run_video_export([spec], 0), progress="cancel")
    finally:
        export_controller.VideoExportWorker = real
        _dispose(win)


# ------------------------------------------------------------------ every dialog, from the keyboard
# THE SECOND CENSUS: what the keys do. File ▸ Library… shipped with "Clear library" as its DEFAULT
# button — Qt makes the first auto-default push button in the focus chain the default of a dialog
# that names none — so Return in the search field or on a selected row offered to wipe the
# library, and its table kept every Tab for itself (40 presses, Shift+Tab too: Open, Use as
# reference and Close unreachable). The session record's Notes box ate Tab the same way ("wet" then
# Tab x3 gave "wet\t\t\t", Save unreachable). So every dialog the app opens is driven here with
# REAL key events to the focused widget, inside its real `exec()`, and must pass four rules:
#   1. it has a default button, or a reason in its door's `no_default` for having none;
#   2. at no Tab stop does Return press a destructive button (`_DESTRUCTIVE`) — neither the
#      dialog's default nor an auto-default button the focus has landed on;
#   3. Tab from where it opens visits every enabled tab stop and comes back: it never stays put
#      (a text box or a table keeping the key) and never leaves the window — and it walks the
#      dialog as it reads, top to bottom and left to right, climbing back only once a round;
#   4. Esc closes it, rejected.
_DESTRUCTIVE = ("clear", "forget", "delete", "remove", "reset", "discard", "overwrite", "replace",
                "erase", "wipe", "restore", "trash")
_TAB_LIMIT = 80


def _destructive(text: str) -> bool:
    words = "".join(c if c.isalnum() else " " for c in text.replace("&", "").lower()).split()
    return any(w in _DESTRUCTIVE for w in words)


def _label(w) -> str:
    from PySide6.QtWidgets import QAbstractButton
    if w is None:
        return "nothing"
    text = w.text() if isinstance(w, QAbstractButton) else ""
    return f"{type(w).__name__}{f' {text!r}' if text else ''}"


def _tab_stops(dlg) -> list:
    """The controls Tab should reach: shown, enabled, accepting tab focus, in this window (a combo's
    popup is another), and not standing in for another widget."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget
    return [w for w in dlg.findChildren(QWidget)
            if w.isVisibleTo(dlg) and w.isEnabled() and w.window() is dlg
            and (w.focusPolicy() & Qt.TabFocus) == Qt.TabFocus and w.focusProxy() is None]


def _scrolled_by(w, dlg) -> list:
    """The scroll areas `w` sits inside, outermost first."""
    from PySide6.QtWidgets import QAbstractScrollArea
    out, p = [], w.parentWidget()
    while p is not None and p is not dlg:
        if isinstance(p, QAbstractScrollArea):
            out.insert(0, p)
        p = p.parentWidget()
    return out


def _reads_after(dlg, a, b) -> bool:
    """True when `b` comes after `a` in reading order: lower down the dialog, or on the same line
    (their heights overlap) and to its right. Into a scroll area from the area itself reads on;
    otherwise what a scroll area holds is read where the area is, whatever its scrolled offset."""
    from PySide6.QtCore import QPoint
    sa, sb = _scrolled_by(a, dlg), _scrolled_by(b, dlg)
    if a in sb:
        return True
    shared = 0
    while shared < min(len(sa), len(sb)) and sa[shared] is sb[shared]:
        shared += 1
    a = sa[shared] if shared < len(sa) else a
    b = sb[shared] if shared < len(sb) else b
    ra = a.rect().translated(a.mapTo(dlg, QPoint(0, 0)))
    rb = b.rect().translated(b.mapTo(dlg, QPoint(0, 0)))
    if rb.top() >= ra.bottom() - 2:
        return True
    same_line = rb.top() < ra.bottom() and rb.bottom() > ra.top()
    return same_line and rb.left() > ra.left()


def _return_presses(dlg):
    """The push button Return would press now: a focused auto-default one, else the default."""
    from PySide6.QtWidgets import QPushButton
    fw = QApplication.focusWidget()
    if isinstance(fw, QPushButton) and fw.window() is dlg and fw.autoDefault():
        return fw
    return next((b for b in dlg.findChildren(QPushButton)
                 if b.isDefault() and b.isVisibleTo(dlg) and b.window() is dlg), None)


def keyboard_defects(dlg, no_default: str | None = None) -> list[str]:
    """Drive `dlg` (shown, focused) with real keys and list what breaks the four rules above. Ends
    by pressing Esc, so the dialog is closed when this returns."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPushButton
    _APP.processEvents()
    out: list[str] = []
    if not any(b.isDefault() for b in dlg.findChildren(QPushButton)) and not no_default:
        out.append("no default button")
    start = QApplication.focusWidget()
    if start is None or start.window() is not dlg:
        out.append(f"opens with the focus on {_label(start)}, outside the dialog")
    else:
        stops, seen, cur, climbs = _tab_stops(dlg), [start], start, []
        for _ in range(_TAB_LIMIT):
            target = _return_presses(dlg)
            if target is not None and _destructive(target.text()):
                say = f"Return on {_label(cur)} presses {target.text()!r}"
                if say not in out:
                    out.append(say)
            QTest.keyClick(cur, Qt.Key_Tab)
            nxt = QApplication.focusWidget()
            if nxt is None or nxt.window() is not dlg:
                out.append(f"Tab from {_label(cur)} leaves the dialog ({_label(nxt)})")
                break
            if nxt is cur:
                out.append(f"Tab is kept by {_label(cur)}")
                break
            if not _reads_after(dlg, cur, nxt):
                climbs.append(f"from {_label(cur)} to {_label(nxt)}")
            if nxt is start:
                missed = sorted({_label(w) for w in stops if w not in seen})
                if missed:
                    out.append(f"Tab never reaches {', '.join(missed)}")
                # A round in reading order climbs back exactly once: last control to first.
                if len(climbs) > 1:
                    out.append(f"Tab goes back up the dialog {len(climbs)} times in one round "
                               f"({'; '.join(climbs)})")
                break
            seen.append(nxt)
            cur = nxt
        else:
            out.append(f"Tab did not come back to {_label(start)} in {_TAB_LIMIT} presses")
    QTest.keyClick(QApplication.focusWidget() or dlg, Qt.Key_Escape)
    for _ in range(100):           # a QMessageBox answers Esc with an ANIMATED click on macOS
        _APP.processEvents()
        if not isValid(dlg) or not dlg.isVisible():
            break
        QThread.msleep(10)
    if not isValid(dlg):
        return out                                       # closed, and freed on close
    if dlg.isVisible():
        out.append("Esc does not close it")
        QDialog.reject(dlg)                              # past an override that ignores it
    elif isinstance(dlg, QMessageBox):                   # its result is the button's code
        if dlg.buttonRole(dlg.clickedButton()) not in (QMessageBox.RejectRole, QMessageBox.NoRole):
            out.append(f"Esc answers {dlg.clickedButton().text()!r}")
    elif dlg.result() != QDialog.Rejected:
        out.append("Esc closes it ACCEPTED")
    return out


def _audit_modal(name: str, open_modal, no_default: str | None = None) -> list[str]:
    """`open_modal()` runs a real `exec()`; the first modal it puts up is audited from inside its
    own event loop (where a user's keys land) and closed by the audit's Esc."""
    got: dict = {}
    timer = QTimer()
    timer.setInterval(20)

    def tick():
        dlg = QApplication.activeModalWidget()
        if dlg is None or "defects" in got:
            return
        timer.stop()
        got["defects"] = keyboard_defects(dlg, no_default)

    timer.timeout.connect(tick)
    timer.start()
    try:
        open_modal()
    finally:
        timer.stop()
    assert "defects" in got, f"{name}: nothing modal opened, so nothing was audited"
    return got["defects"]


def _library_dialog(clip: str, **extra):
    """File ▸ Library… with every control it can build: two present rows, every callback wired
    (none of them is ever pressed), a session open (so Use as reference is shown)."""
    from studio.library_dialog import LibraryDialog
    rows = [{"fingerprint": library.fingerprint(stem), "stem": stem,
             "track": "Synthetic demo circuit", "date": date, "lap_count": 8, "best": 45.1,
             "theoretical": 44.8, "verified": True, "degraded": False, "dropout": False,
             "paths": [clip]} for stem, date in (("GX010001", "2026-11-01"),
                                                 ("GX010002", "2026-11-02"))]
    index = {"version": library.VERSION, "entries": rows}
    return LibraryDialog(
        index, extra.pop("open_recording", lambda _p: None),
        forget_recording=lambda e: index, clear_library=lambda: index,
        reveal_library=lambda: None, backup_library=lambda: None,
        restore_library=lambda: index,
        backup_info=lambda: {"entries": 2, "mtime": 0.0, "path": clip},
        edit_record=lambda e: None, manage_tracks=lambda *_a: None, show_privacy=lambda: None,
        use_as_reference=lambda e: None, reference_refusal=lambda e: None, **extra)


def _keyboard_doors(clip: str):
    """(name, open_modal, no_default) for every dialog the app opens. The pickers Qt owns (the save
    and folder panels, QInputDialog) and the export progress modal (its one button is Cancel, which
    Esc presses) are not repeated here."""
    from test_provenance import _session

    from studio import help_dialog
    from studio.marks_panel import MarkDialog
    from studio.session_record_dialog import SessionRecordDialog
    from studio.track_dialog import TrackManagerDialog

    def run(build):
        def go():
            dlg = build()
            try:
                dlg.exec()
            finally:
                dlg.deleteLater()
        return go

    def clear_confirmation():
        dlg = _library_dialog(clip)
        try:
            dlg._on_clear_library()                   # its QMessageBox.question is audited
        finally:
            dlg.deleteLater()

    def recalled_export():
        # The row "Opened on your last export's resolution: Source  [Use defaults]" above the form.
        key, source = win.exports._PREF_EXPORT_RES, win.exports._EXPORT_RES_SOURCE
        prefs.set(key, source)
        try:
            win.exports._ask_export_options(win.session.best_lap_id())
        finally:
            prefs.set(key, win.exports._EXPORT_DEFAULT_INDEX[key])

    win = _window()
    win.session, win._paths = _session(), []
    rows = [{"name": "Daytona Milton Keynes", "builtin": True, "editable": True, "sectors": 0},
            {"name": "Whilton Mill", "builtin": False, "editable": True, "sectors": 2}]
    return win, [
        ("⌘K command palette", win.show_command_palette,
         "a search box over a list: Return runs the highlighted row"),
        ("File ▸ Library…", run(lambda: _library_dialog(clip)), None),
        ("Library ▸ Clear library confirmation", clear_confirmation, None),
        ("Saved tracks…", run(lambda: TrackManagerDialog(
            rows, rename_track=lambda *_a: rows, delete_track=lambda _n: rows,
            restore_tracks=lambda: rows, backup_info=lambda: {"tracks": 2, "mtime": 0.0})), None),
        ("Session record… (a new one)", run(lambda: SessionRecordDialog({}, name="GX010001")),
         None),
        ("Session record… (editing one)", run(lambda: SessionRecordDialog(
            {"conditions": "dry", "notes": "wet"}, name="GX010001", is_new=False)), None),
        ("Marks ▸ add a mark", run(lambda: MarkDialog({"type": "note", "note": ""})), None),
        ("Export overlay video…", lambda: win.exports._ask_export_options(
            win.session.best_lap_id()), None),
        ("Export overlay video… (opened on a remembered Source resolution)",
         recalled_export, None),
        ("Help ▸ keyboard shortcuts", run(lambda: help_dialog.ShortcutsDialog(win)), None),
        ("Help ▸ About", run(lambda: help_dialog.AboutDialog(win)), None),
        ("Help ▸ Your data & privacy", run(lambda: help_dialog.PrivacyDialog(win)), None),
    ]


def test_every_dialog_works_from_the_keyboard():
    """The four rules above, on every door, in one table (printed whole, so a failure shows every
    dialog's state and not just the first). Inspect this number…'s panel is not modal and is
    audited as it opens beside the window."""
    from test_provenance import _session

    from studio import provenance_panel
    from studio.lap_table import LapTable

    table: dict[str, list[str]] = {}
    with tempfile.TemporaryDirectory() as d:
        clip = os.path.join(d, "GX010001.MP4")
        open(clip, "wb").close()
        win, doors = _keyboard_doors(clip)
        try:
            for name, door, no_default in doors:
                table[name] = _audit_modal(name, door, no_default)
        finally:
            _dispose(win)
    laps = LapTable(_session())
    try:
        prov = laps.session.lap_time_provenance(laps.session.best_lap_id())
        panel = provenance_panel.open_for(prov, laps)
        panel.activateWindow()
        _APP.processEvents()
        table["Inspect this number…"] = keyboard_defects(panel)
    finally:
        _dispose(laps)
    for name, defects in table.items():
        print(f"  {'ok ' if not defects else 'BAD'} {name}: {'; '.join(defects) or 'fine'}")
    bad = {k: v for k, v in table.items() if v}
    assert not bad, f"{len(bad)} dialogs fail the keyboard rules: {bad}"
    print(f"ok every dialog works from the keyboard ({len(table)} dialogs)")


def test_the_keyboard_audit_has_teeth():
    """Each rule, planted alone in a dialog of this test's own, is caught; the same dialog with
    nothing planted passes — so the table above is clean because the dialogs are, not because
    the audit cannot see."""
    from PySide6.QtWidgets import (
        QDialogButtonBox,
        QLineEdit,
        QPlainTextEdit,
        QPushButton,
        QTableWidget,
        QVBoxLayout,
    )

    class _NoEsc(QDialog):
        def reject(self):                                # planted: Esc does nothing
            pass

    def build(plant: str):
        dlg = (_NoEsc if plant == "esc ignored" else QDialog)()
        col = QVBoxLayout(dlg)
        col.addWidget(QLineEdit())
        if plant == "Tab goes back up":
            lower, upper = QLineEdit(dlg), QLineEdit(dlg)  # made bottom-first: Tab climbs
            col.insertWidget(0, upper)
            col.addWidget(lower)
        if plant == "text box keeps Tab":
            col.addWidget(QPlainTextEdit())             # tabChangesFocus False: Tab types a tab
        if plant == "table keeps Tab":
            grid = QTableWidget(2, 2)                   # tabKeyNavigation on: Tab walks the cells
            col.addWidget(grid)
        if plant == "focused Clear is auto-default":
            col.addWidget(QPushButton("Clear library"))  # auto-default: Return on it wipes
        if plant == "no default":                       # (a button box always makes OK one)
            for text, slot in (("OK", dlg.accept), ("Cancel", dlg.reject)):
                b = QPushButton(text)
                b.setAutoDefault(False)
                b.clicked.connect(slot)
                col.addWidget(b)
            return dlg
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        if plant == "Delete is the default":
            box.button(QDialogButtonBox.Ok).setText("Delete")
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        col.addWidget(box)
        return dlg

    expect = {"nothing": None,
              "Delete is the default": "presses 'Delete'",
              "focused Clear is auto-default": "presses 'Clear library'",
              "no default": "no default button",
              "text box keeps Tab": "Tab is kept by QPlainTextEdit",
              "table keeps Tab": "Tab is kept by QTableWidget",
              "Tab goes back up": "Tab goes back up the dialog",
              "esc ignored": "Esc does not close it"}
    for plant, needle in expect.items():
        def go(plant=plant):
            dlg = build(plant)
            try:
                dlg.exec()
            finally:
                if dlg.isVisible():
                    QDialog.reject(dlg)
                dlg.deleteLater()
        defects = _audit_modal(plant, go)
        if needle is None:
            assert defects == [], f"a clean dialog was flagged: {defects}"
        else:
            assert any(needle in d for d in defects), f"{plant}: not caught, got {defects}"
    _spin()
    print(f"ok the keyboard audit catches each of {len(expect) - 1} planted defects")


if __name__ == "__main__":
    for _name, _fn in list(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
