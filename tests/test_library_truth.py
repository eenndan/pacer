"""The Library tells the truth about its own rows: which build measured an ideal lap, where missing
footage was, and how often it reads its own file.

Three QA findings, each pinned by the thing that was measured (QA round 2, 2026-09-26, package H):

  * NEW-8 / LOOK-4 — THE IDEAL COLUMN MIXED ALGORITHM VERSIONS. His Library read Sandown 19 Sep
    "Ideal lap 0:46.063" while that recording's own Stats page said 0:46.196: a row keeps whatever
    ideal the build that last opened it computed. Re-measured with one build on the four rows
    whose footage is here: the ideal moved +0.133 / +0.017 / +0.004 / 0.000 s, while `best` and
    `lap_count` were bit-identical on all four. So every row now carries the ideal-lap version that
    measured it (`corner_model.IDEAL_VERSION`), and a row from another version shows its number
    MUTED with the reason on hover. Nothing is recomputed without the footage.
  * VIEW-6 — A "(file missing)" ROW COULD NOT BE SELECTED AND SAID NOTHING. Clicking one left the
    selection (and an ENABLED Open) on another recording. It is now selectable, Open is disabled,
    and one line says where the footage was and how to bring it back. Re-opening the recording
    from its new place re-points the row, which the line promises, so that is pinned here too.
  * HEALTH-6 — `library.load()` PARSED THE INDEX FOUR TIMES A LAUNCH, and re-ran (and re-logged) an
    old index's migration each time. One parse per file state now, cached on the file's identity,
    so one launch logs the migration once.

Real Qt, real LibraryDialog, real StudioWindow for the launch count; every store is jailed in a
temp dir. Run: python tests/test_library_truth.py
"""
import json
import logging
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import (  # noqa: E402
    _jsonstore,
    corner_model,
    demo,
    focus,
    library,
    logsetup,
    marks,
    prefs,
    session_record,
    theme,
    track_db,
)
from studio._signal import fmt_time  # noqa: E402
from studio.theme import C  # noqa: E402

theme.register_fonts()
theme.apply_theme(_APP)

# EVERY store seam into one temp dir for the whole file, before any dialog or window exists: the
# dialog reads prefs on construction, and the window reads all of them.
_SEAMS = tempfile.TemporaryDirectory(prefix="pacer-test-library-truth-")
for _mod in (demo, focus, library, logsetup, marks, prefs, session_record, track_db):
    _mod._app_support_dir = lambda d=_SEAMS.name: d
os.environ.pop("PACER_DEMO_MP4", None)

from studio.library_dialog import _COL_DATE, _COL_THEO, FP_ROLE, LibraryDialog  # noqa: E402

# The build's ideal-lap version. getattr so that a tree without the stamp FAILS these tests by
# assertion, with the defect in the message, rather than by an ImportError.
_CURRENT = getattr(corner_model, "IDEAL_VERSION", 1)


def _entry(stem, paths, *, theo=46.196, best=46.808, ideal_version=_CURRENT, track="Sandown Park",
           date="2026-09-19", laps=36):
    return {"fingerprint": library.fingerprint(stem), "stem": stem, "track": track, "date": date,
            "lap_count": laps, "best": best, "theoretical": theo, "verified": True,
            "degraded": False, "dropout": False, "ideal_version": ideal_version,
            "paths": list(paths)}


def _row(dlg, fp) -> int:
    for r in range(dlg.table.rowCount()):
        if dlg.table.item(r, _COL_DATE).data(FP_ROLE) == fp:
            return r
    raise AssertionError(f"no row for {fp}")


def _selected_row(dlg):
    rows = dlg.table.selectionModel().selectedRows()
    return rows[0].row() if rows else None


class _OpenSpy:
    def __init__(self):
        self.calls = []

    def __call__(self, paths):
        self.calls.append(list(paths))


# ======================================================================== NEW-8 / LOOK-4
def test_a_row_measured_by_another_ideal_version_shows_its_number_muted_and_says_why():
    """His index, in miniature: one row written by this build, one written before rows were
    stamped (every row he has today), one stamped by an older ideal. The two not measured by this
    build keep their number — the only ideal a row on a disconnected drive will ever have — but
    muted, with the reason leading the hover; the current row reads exactly as it always did."""
    with tempfile.TemporaryDirectory() as d:
        clip = os.path.join(d, "GX010068.MP4")
        open(clip, "wb").close()
        rows = [_entry("GX010068", [clip], theo=46.196),
                _entry("GX010065", [clip], theo=46.201, ideal_version=None),
                _entry("GX010064", [clip], theo=45.805, ideal_version=_CURRENT - 1),
                # ...and rows a NEWER build wrote (a downgrade): muted too, never told "older".
                _entry("GX010066", [clip], theo=46.3, ideal_version=_CURRENT + 1),
                _entry("GX010067", [clip], theo=None, ideal_version=_CURRENT + 1)]
        dlg = LibraryDialog({"version": library.VERSION, "entries": rows}, _OpenSpy())
        muted = QColor(C.text_muted).name()
        cells = {fp: dlg.table.item(_row(dlg, fp), _COL_THEO)
                 for fp in ("GX0068", "GX0065", "GX0064")}
        for fp in ("GX0065", "GX0064"):
            cell = cells[fp]
            assert cell.foreground().color().name() == muted, (
                f"{fp}'s ideal was measured by another build, yet it is painted like a current one "
                f"({cell.foreground().color().name()}, muted is {muted}): the column mixes versions")
            head = cell.toolTip().splitlines()[0]
            assert "older Pacer" in head and "re-open" in head.lower(), cell.toolTip()
        assert cells["GX0065"].text() == fmt_time(46.201), "a stale ideal keeps its number"
        newer = {fp: dlg.table.item(_row(dlg, fp), _COL_THEO) for fp in ("GX0066", "GX0067")}
        assert newer["GX0066"].foreground().color().name() == muted
        for fp, cell in newer.items():
            # "older Pacer", not "older": the appended file path's "/var/folders/" contains it.
            assert "newer" in cell.toolTip() and "older Pacer" not in cell.toolTip(), \
                (fp, cell.toolTip())
        fresh = cells["GX0068"]
        assert fresh.foreground().color().name() != muted, "a current ideal must not be muted"
        assert "older Pacer" not in fresh.toolTip(), fresh.toolTip()
        assert fresh.text() == fmt_time(46.196)
        dlg.deleteLater()


def test_the_row_carries_the_ideal_version_that_measured_it():
    """The stamp is written where the row is built (`Session.library_entry`) and survives the
    index's own normalisation on save and load, which rewrites only the fields it knows."""
    from studio.session import Session
    s = Session.__new__(Session)
    s._valid_cache, s._best_cache, s.track_name = [0, 1, 2], 0, "Sandown Park"
    s.laps = SimpleNamespace(lap_time=lambda i: 46.808)
    s.session_date = lambda: "2026-09-19"
    s.theoretical_best = lambda: 46.196
    s.dropout_lap_ids = lambda: set()
    with tempfile.TemporaryDirectory() as d:
        clip = os.path.join(d, "GX010068.MP4")
        open(clip, "wb").close()
        entry = Session.library_entry(s, [clip])
        assert entry.get("ideal_version") == _CURRENT, (
            f"library_entry does not stamp the ideal version that measured it: {entry}")
        path = os.path.join(d, "library.json")
        library.save({"entries": [entry]}, path)
        stored = library.load(path)["entries"][0]
        assert stored.get("ideal_version") == _CURRENT, f"the index dropped the stamp: {stored}"
        # A garbage stamp costs the row its "current" status, never the row.
        with open(path) as f:
            raw = json.load(f)
        raw["entries"][0]["ideal_version"] = "v1"
        with open(path, "w") as f:
            json.dump(raw, f)
        again = library.load(path)["entries"]
        assert len(again) == 1 and again[0]["ideal_version"] is None, again


# The golden baseline's ideal-lap leaves, per `corner_model.IDEAL_VERSION` they were cut under.
_IDEAL_GOLDEN = {
    1: {"base": 32.79511092416341, "drift_band": 35.90774898123084,
        "drift_median": 35.90774898123084, "drift_noise": 35.90774898123084,
        "gopro": 44.68304687294224, "gopro_sectors": 44.68304687236758,
        "ref": 32.79511092416341, "ref_cleared": 32.79511092416341},
}


def test_the_ideal_version_moves_with_the_golden_ideal_leaves():
    """`IDEAL_VERSION` is only as good as the discipline of bumping it, so the golden gate
    enforces it: the synthetic sessions' `theoretical_best` leaves are pinned to the version they
    were cut under. A re-cut that moves any of them fails HERE until the version is bumped (and
    every Library row measured before it reads as older) and the new leaves are pinned above. The
    fixture cannot reach every change to the ideal's maths — a bump without a moved leaf is still
    owed by hand, and pins the same leaves under the new number."""
    assert hasattr(corner_model, "IDEAL_VERSION"), "the ideal lap's maths carries no version"
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "golden_synthetic_baseline.json")
    with open(base) as f:
        golden = json.load(f)
    leaves = {phase: tree["theoretical_best"] for phase, tree in sorted(golden.items())
              if isinstance(tree, dict) and "theoretical_best" in tree}
    assert leaves, "the golden baseline carries no theoretical_best leaf any more"
    assert _IDEAL_GOLDEN.get(corner_model.IDEAL_VERSION) == leaves, (
        f"the golden ideal leaves are not the ones corner_model.IDEAL_VERSION "
        f"{corner_model.IDEAL_VERSION} was cut under. If the ideal lap's maths changed, bump "
        f"IDEAL_VERSION and pin these under it in _IDEAL_GOLDEN:\n{json.dumps(leaves, indent=1)}")


# ================================================================================ VIEW-6
def test_a_missing_footage_row_is_selectable_not_openable_and_says_where_it_was():
    """His D24 row: the footage is on a drive that is not connected. A click selects it (it used to
    leave the selection, and an ENABLED Open, on another recording), Open is disabled, and one line
    names the folder the footage was in and the way back. The present row still opens."""
    with tempfile.TemporaryDirectory() as d:
        clip = os.path.join(d, "GX010068.MP4")
        open(clip, "wb").close()
        gone = "/Volumes/pacer-test-no-such-drive/D24"
        missing = [os.path.join(gone, f"GX0{i}0060.MP4") for i in (1, 2, 3)]
        rows = [_entry("GX010068", [clip]),
                _entry("GX010060", missing, track="Daytona Milton Keynes", date="2026-05-23",
                       best=68.228, theo=None, laps=57)]
        spy = _OpenSpy()
        dlg = LibraryDialog({"version": library.VERSION, "entries": rows}, spy)
        dlg.show()
        QApplication.processEvents()
        r = _row(dlg, "GX0060")
        assert _selected_row(dlg) == _row(dlg, "GX0068"), "opens on the newest present row"
        rect = dlg.table.visualRect(dlg.table.model().index(r, 1))
        QTest.mouseClick(dlg.table.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
        QApplication.processEvents()
        assert _selected_row(dlg) == r, (
            f"clicking the missing row left the selection on row {_selected_row(dlg)}; it must "
            f"select row {r}")
        assert not dlg.open_btn.isEnabled(), "Open would load a recording that is not there"
        line = getattr(dlg, "missing_line", None)
        assert line is not None and line.isVisibleTo(dlg), "nothing explains the missing footage"
        text = line.text()
        assert gone in text and "File ▸ Open" in text and "GX010060.MP4" in text, text
        QTest.mouseDClick(dlg.table.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
        dlg._open_selected()
        assert spy.calls == [], f"a missing row opened: {spy.calls}"
        # Back on a present row: the line goes, Open comes back.
        dlg.table.selectRow(_row(dlg, "GX0068"))
        QApplication.processEvents()
        assert not line.isVisibleTo(dlg) and dlg.open_btn.isEnabled()
        dlg.close()
        dlg.deleteLater()


def test_right_click_session_record_on_a_missing_row_edits_that_row():
    """The row's right-click "Session record…" selects the row, then edits the selection. On a
    row that could not be selected, `selectRow` CLEARED the selection instead, so the menu item did
    nothing at all (measured on main: no editor, and the 19 Sep row deselected). A missing row's
    session record is still his to write — the footage is elsewhere, the session happened."""
    from PySide6.QtWidgets import QMenu

    from studio import library_dialog

    class _PicksSessionRecord(QMenu):
        # A Python override, because assigning QMenu.exec on the class does not take (its exec
        # has static and instance overloads), and the real popup then blocks the run.
        def exec(self, *a):
            return next(x for x in self.actions() if x.text() == "Session record…")

    with tempfile.TemporaryDirectory() as d:
        clip = os.path.join(d, "GX010068.MP4")
        open(clip, "wb").close()
        rows = [_entry("GX010068", [clip]),
                _entry("GX010060", ["/Volumes/pacer-test-no-such-drive/D24/GX010060.MP4"],
                       track="Daytona Milton Keynes", date="2026-05-23")]
        edited = []
        dlg = LibraryDialog({"version": library.VERSION, "entries": rows}, _OpenSpy(),
                            forget_recording=lambda e: {"entries": rows},
                            edit_record=lambda e: edited.append(e["fingerprint"]))
        dlg.show()
        QApplication.processEvents()
        library_dialog.QMenu = _PicksSessionRecord
        try:
            rect = dlg.table.visualRect(dlg.table.model().index(_row(dlg, "GX0060"), 1))
            dlg._on_context_menu(rect.center())
        finally:
            library_dialog.QMenu = QMenu
        assert edited == ["GX0060"], f"right-click on the D24 row edited {edited}"
        dlg.close()
        dlg.deleteLater()


def test_reopening_the_recording_from_its_new_folder_repoints_the_row():
    """The line's promise, through the app's own load-time path: the drive comes back under a new
    mount point, File ▸ Open picks a chapter (the door expands it to the whole recording), and
    `LibraryController.update_library` replaces the row — paths and all — because the SAME
    chapters are a re-measurement (`library._keeps`). The row is then present, and openable."""
    from studio import app as studio_app
    from studio.library_controller import LibraryController
    from studio.library_dialog import _entry_missing
    from studio.session import Session

    with tempfile.TemporaryDirectory() as d:
        library_json = library.library_path()
        old = [f"/Volumes/pacer-test-old-drive/D24/GX0{i}0060.MP4" for i in (1, 2, 3)]
        library.save({"entries": [_entry("GX010060", old, track="Daytona Milton Keynes",
                                         best=68.228, theo=None, ideal_version=None)]})
        new = [os.path.join(d, f"GX0{i}0060.MP4") for i in (1, 2, 3)]
        for p in new:
            open(p, "wb").close()
        s = Session.__new__(Session)
        s._valid_cache, s._best_cache, s.track_name = list(range(57)), 0, "Daytona Milton Keynes"
        s.laps = SimpleNamespace(lap_time=lambda i: 68.228)
        s.session_date = lambda: "2026-05-23"
        s.theoretical_best = lambda: 66.95
        s.dropout_lap_ids = lambda: set()
        win = studio_app.StudioWindow.__new__(studio_app.StudioWindow)
        win.session = SimpleNamespace(
            valid_lap_ids=lambda: list(range(57)), timing_verified=True,
            timing_quality=SimpleNamespace(degraded=False),
            library_entry=lambda p: Session.library_entry(s, p))
        LibraryController(win, studio_app.STATUS_MS).update_library(new)
        (row,) = library.load(library_json)["entries"]
        assert row["paths"] == new, f"the row still points at the old drive: {row['paths']}"
        assert not _entry_missing(row)
        assert row["theoretical"] == 66.95 and row.get("ideal_version") == _CURRENT, row
        library.save(library.empty_index())


# ============================================================================== HEALTH-6
class _Reads:
    """Counts the parses of ONE store file and the library's migration lines."""

    def __init__(self, path):
        self.path, self.reads, self.migrations = os.path.abspath(path), 0, []
        self._orig = _jsonstore.read_object

    def __enter__(self):
        def counted(p):
            if os.path.abspath(p) == self.path:
                self.reads += 1
            return self._orig(p)
        _jsonstore.read_object = counted
        spy = self

        class _H(logging.Handler):
            def emit(self, rec):
                if "migrating index" in rec.getMessage():
                    spy.migrations.append(rec.getMessage())
        self._h = _H(level=logging.WARNING)
        logging.getLogger("studio.library").addHandler(self._h)
        return self

    def __exit__(self, *exc):
        _jsonstore.read_object = self._orig
        logging.getLogger("studio.library").removeHandler(self._h)
        return False


def _v3_index(path, paths):
    """A version-3 index as an older Pacer left it: current keys, so its migration merges nothing
    and nothing is backed up — the file a launch that saves nothing keeps re-reading."""
    e = _entry("GX010068", paths)
    e.pop("ideal_version")
    with open(path, "w") as f:
        json.dump({"version": 3, "entries": [e]}, f)


def test_a_launch_parses_the_index_once_and_logs_its_migration_once():
    """HEALTH-6, on the real window: building a StudioWindow on an old index read and migrated it
    at the Open Recent seed AND at the Library action's gate (two "migrating index" lines at window
    build in his 09-24 log), then again for every menu sync. One parse per file state now, and the
    File menu opening twice more reads nothing new."""
    from studio.app import StudioWindow
    path = library.library_path()
    with tempfile.TemporaryDirectory() as d:
        clip = os.path.join(d, "GX010068.MP4")
        open(clip, "wb").close()
        _v3_index(path, [clip])
        with _Reads(path) as seen:
            win = StudioWindow([])
            QApplication.processEvents()
            for _ in range(2):                           # the File menu opened twice
                win.library_ctl.sync_recent_menu()
                win._has_library()
            QApplication.processEvents()
        assert seen.reads == 1, (
            f"one launch parsed library.json {seen.reads} times; one parse per file state")
        assert len(seen.migrations) == 1, (
            f"one launch logged the migration {len(seen.migrations)} times: {seen.migrations}")
        win.close()
        win.deleteLater()
        QApplication.processEvents()
        library.save(library.empty_index())


def test_the_cache_follows_the_file_not_the_clock():
    """What makes one parse safe: the cached index is keyed on the file's identity (inode, size,
    mtime, ctime), so a save — ours, which primes it, or another process's, which replaces the
    inode — and an in-place rewrite of the same size are all seen, and a caller that mutates what
    `load` returned cannot reach the cache."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "library.json")
        clip = os.path.join(d, "GX010068.MP4")
        library.save({"entries": [_entry("GX010068", [clip])]}, path)
        with _Reads(path) as seen:
            first = library.load(path)
            first["entries"].clear()                      # a caller's mutation…
            assert len(library.load(path)["entries"]) == 1   # …is not the cache's
            library.upsert_and_save(_entry("GX010065", [clip]), path)
            assert [e["fingerprint"] for e in library.load(path)["entries"]] == \
                ["GX0068", "GX0065"]
        assert seen.reads == 0, f"our own save was re-read {seen.reads} times"
        # Another writer, in place and the same size: the bytes change, the inode does not.
        with open(path) as f:
            text = f.read()
        with open(path, "w") as f:
            f.write(text.replace("Sandown Park", "Sandown Parc"))
        assert {e["track"] for e in library.load(path)["entries"]} == {"Sandown Parc"}


# ------------------------------------------------------------------ runner
if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok  {_name}")
