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
    app_support,
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

from studio.library_dialog import (  # noqa: E402
    _COL_BEST,
    _COL_DATE,
    _COL_THEO,
    FP_ROLE,
    LibraryDialog,
)

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
        # Every row the owner has today was stamped 1. Under 2 (the time-aware smoother) such a row
        # is still listed, keeps its number, is muted and says re-opening updates it: nothing is
        # dropped and nothing claims the number is wrong.
        if _CURRENT == 2:
            assert cells["GX0064"].text() == fmt_time(45.805), cells["GX0064"].text()
            assert "may not compare" in cells["GX0064"].toolTip(), cells["GX0064"].toolTip()
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


def _ink(dlg, row, col) -> tuple[int, int]:
    """(pixels in C.text's band, pixels in C.text_dim's band) in a cell of the rendered table:
    what the eye gets, which the item's ForegroundRole does not say on a selected row."""
    dlg.table.viewport().repaint()
    img = dlg.table.viewport().grab().toImage()
    rect = dlg.table.visualItemRect(dlg.table.item(row, col))
    dpr = img.devicePixelRatio()
    full = dim = 0
    for y in range(int(rect.top() * dpr), int(rect.bottom() * dpr)):
        for x in range(int(rect.left() * dpr), int(rect.right() * dpr)):
            c = img.pixelColor(x, y)
            lum = (c.red() + c.green() + c.blue()) / 3
            full += lum > 200          # C.text #DDE1E8 ≈ 225; text_dim #9AA1AD ≈ 161
            dim += 140 < lum < 185
    return full, dim


def test_a_selected_row_keeps_its_stale_ideal_muted():
    """Qt paints a SELECTED item's text in the selection's own ink and ignores its foreground, so
    the one row the user is looking at drew its stale ideal in full colour (DECISIONS §28): the
    item said muted, the screen did not. Measured on the rendered pixels, selected and not, with
    a current row's ideal as the control; the selected ink is C.text_dim, legible on the band."""
    with tempfile.TemporaryDirectory() as d:
        clip = os.path.join(d, "GX010068.MP4")
        open(clip, "wb").close()
        rows = [_entry("GX010068", [clip], theo=46.196),
                _entry("GX010064", [clip], theo=45.805, ideal_version=_CURRENT - 1)]
        dlg = LibraryDialog({"version": library.VERSION, "entries": rows}, _OpenSpy())
        dlg.resize(900, 500)
        dlg.show()
        _APP.processEvents()
        stale, fresh = _row(dlg, "GX0064"), _row(dlg, "GX0068")
        dlg.table.selectRow(fresh)
        _APP.processEvents()
        assert _ink(dlg, fresh, _COL_THEO)[0] > 20, "the control: a current ideal in full ink"
        assert _ink(dlg, stale, _COL_THEO)[0] == 0, "an unselected stale ideal is not muted"
        dlg.table.selectRow(stale)
        _APP.processEvents()
        full, dim = _ink(dlg, stale, _COL_THEO)
        assert full == 0 and dim > 20, (
            f"the selected row's stale ideal is drawn in full ink ({full} px), {dim} px muted")
        assert _ink(dlg, stale, _COL_BEST)[0] > 20, "the row's other cells keep the band's ink"
        lum = [0.2126 * f(c.redF()) + 0.7152 * f(c.greenF()) + 0.0722 * f(c.blueF())
               for c in (QColor(C.text_dim), QColor(C.sel_bg))
               for f in [lambda v: v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4]]
        assert (lum[0] + 0.05) / (lum[1] + 0.05) >= 4.5, "the muted ink is not legible on the band"
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
    s._lap_rotation = lambda: 0          # no corners on a bare session: way round unknown
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
    # The time-aware smoother: the synthetic GoPro's two cleaner-removed teleports are re-smoothed.
    2: {"base": 32.79511092416341, "drift_band": 35.90774898123084,
        "drift_median": 35.90774898123084, "drift_noise": 35.90774898123084,
        "gopro": 44.68307288382565, "gopro_sectors": 44.68307288325263,
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
                                         best=68.228, theo=None, ideal_version=None,
                                         date="2026-05-23")]})   # the recording's own day
        new = [os.path.join(d, f"GX0{i}0060.MP4") for i in (1, 2, 3)]
        for p in new:
            open(p, "wb").close()
        s = Session.__new__(Session)
        s._valid_cache, s._best_cache, s.track_name = list(range(57)), 0, "Daytona Milton Keynes"
        s.laps = SimpleNamespace(lap_time=lambda i: 68.228)
        s.session_date = lambda: "2026-05-23"
        s.theoretical_best = lambda: 66.95
        s._lap_rotation = lambda: 0          # no corners on a bare session: way round unknown
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


# ================================================================ Library ▸ Update (cool/...ideals)
# v0.26.0 moved IDEAL_VERSION 1 -> 2, and every older row stays muted until its recording is opened
# again, one by one. Library ▸ Update re-measures them in the background, the way a re-open would
# write each row, and touches nothing else. Real window, real loader, synthetic recordings only.
_UPD: dict = {}


def _update_world() -> dict:
    """Once per process: one generated two-chapter recording, copied under five recording numbers,
    each with a CONFIRMED start line in its sidecar (what a line drag or Save as track leaves, so
    its timing is trusted), and each measured once by the real loader as a re-open measures it."""
    if _UPD:
        return _UPD
    import shutil

    from studio import sidecar
    from studio.dev import synth_gopro as sg
    from studio.session import Session
    gen = tempfile.TemporaryDirectory(prefix="w24-ideal-gen-")
    src = sg.generate(os.path.join(gen.name, "gen"), laps=8, chapters=2).paths
    root = tempfile.TemporaryDirectory(prefix="w24-ideal-recordings-")
    recs = {}
    for n in ("9101", "9102", "9103", "9104", "9105"):
        folder = os.path.join(root.name, n)
        os.makedirs(folder)
        recs[n] = [os.path.join(folder, f"GX0{c}{n}.MP4") for c in (1, 2)]
        for s, d in zip(src, recs[n], strict=True):
            shutil.copyfile(s, d)
    s = Session.load(recs["9101"])
    start, sectors = s.timing_lines_latlon()
    measured = {}
    for n, paths in recs.items():
        sidecar.save(sidecar.sidecar_path(paths[0]), None, start, sectors, confirmed=True)
        s = Session.load(paths)
        assert s.restore_saved_timing_lines(sidecar.sidecar_path(paths[0])) is True
        measured[n] = s.library_entry(paths)
        assert measured[n]["verified"] and not measured[n]["degraded"], measured[n]
    junk = os.path.join(root.name, "junk", "GX019106.MP4")
    os.makedirs(os.path.dirname(junk))
    with open(junk, "wb") as f:
        f.write(b"not a recording" * 64)
    _UPD.update(gen=gen, root=root, recs=recs, measured=measured, junk=junk)
    return _UPD


def _v1_rows(world: dict) -> list[dict]:
    """The index 0.25.0 left: three present rows (9101-9103) with an older ideal — 9103 marked
    degraded, as 0.25.0 marked a session with scattered rejected fixes — one row whose drive is
    gone, and one whose file is there but is not a recording. 9104 is not stored: it is the
    session the window has open, and 9105 stays unknown."""
    rows = []
    for n in ("9101", "9102", "9103"):
        e = dict(world["measured"][n], ideal_version=1,
                 theoretical=world["measured"][n]["theoretical"] + 0.021)
        e["degraded"] = n == "9103"
        rows.append(e)
    gone = ["/Volumes/pacer-test-gone-drive/GX019107.MP4"]
    rows.append(_entry("GX019107", gone, ideal_version=1, date="2026-05-23"))
    rows.append(_entry("GX019106", [world["junk"]], ideal_version=1, date="2026-05-24"))
    return rows


def _files(*roots) -> dict:
    """Every file under `roots` with its size and mtime — what a run wrote is the difference."""
    out = {}
    for root in roots:
        for d, _dirs, names in os.walk(root):
            for name in names:
                p = os.path.join(d, name)
                st = os.stat(p)
                out[p] = (st.st_size, st.st_mtime_ns)
    return out


def _read_only(root: str, on: bool) -> None:
    for d, dirs, names in os.walk(root):
        for name in names:
            os.chmod(os.path.join(d, name), 0o444 if on else 0o644)
        for name in dirs:
            os.chmod(os.path.join(d, name), 0o555 if on else 0o755)
    os.chmod(root, 0o555 if on else 0o755)


def _spin_until(cond, seconds: float = 120.0) -> None:
    import time
    deadline = time.time() + seconds
    while not cond() and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert cond(), "timed out"


def _in_library(win, drive) -> None:
    """Run `drive(dialog)` inside File ▸ Library…'s real modal, which it must close."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication as _A
    caught = []

    def run():
        dlg = _A.activeModalWidget()
        try:
            drive(dlg)
        except BaseException as exc:  # noqa: BLE001 — re-raised outside the modal
            caught.append(exc)
        finally:
            dlg.reject()
    QTimer.singleShot(0, run)
    win.library_ctl.open_library()
    if caught:
        raise caught[0]


def _open_window(paths):
    from studio.app import StudioWindow
    win = StudioWindow([])
    win.resize(1440, 900)
    done = {"v": False}
    win.loadFinished.connect(lambda: done.__setitem__("v", True))
    win._load(list(paths))
    _spin_until(lambda: done["v"])
    return win


def test_update_remeasures_the_older_ideals_and_touches_nothing_else():
    """The line counts the rows whose ideal an older Pacer measured and whose files are here (the
    gone drive in its hover); Update re-stamps each as re-opening it would — 9103, which 0.25.0
    marked degraded, now reads as trusted, and the line says so — leaves the gone row and the
    unreadable one exactly as they were (the latter named), and writes NOTHING but library.json:
    the recordings' folders are read-only for the whole run. The open session, its view and Open
    Recent's order are untouched, and no PB card is raised."""
    from studio.library_dialog import _ideal_cell
    world = _update_world()
    library.save({"entries": _v1_rows(world)})
    before_rows = {e["fingerprint"]: e for e in library.load()["entries"]}
    win = _open_window(world["recs"]["9104"])
    try:
        session, paths, view, toast = win.session, list(win._paths), win.view, win._pb_toast
        recent = [r["paths"] for r in library.recent_rows(library.load(), 50)]
        jail = {_SEAMS.name, app_support.resolve()}
        _read_only(world["root"].name, True)
        seen = {}

        def drive(dlg):
            seen["at rest"] = (dlg.update_line.isVisible(), dlg.update_line.text(),
                               dlg.update_line.toolTip(), dlg.update_btn.isVisible())
            # What the RUN writes, from the click to its end (the dialog's own size pref aside).
            seen["files"] = _files(world["root"].name, *jail)
            dlg.update_btn.click()
            seen["running"] = dlg.update_line.text()
            _spin_until(lambda: not win.library_ctl.ideal_updater().running())
            seen["written"] = sorted(p for p, st in _files(world["root"].name, *jail).items()
                                     if seen["files"].get(p) != st)
            seen["done"] = (dlg.update_line.text(), dlg.update_line.toolTip(),
                            dlg.update_btn.isVisible())
        _in_library(win, drive)
        _spin_until(lambda: not win._load_workers)
    finally:
        _read_only(world["root"].name, False)
    try:
        shown, text, tip, button = seen["at rest"]
        assert shown and button, seen["at rest"]
        assert text == "4 ideal laps were measured by an older Pacer.", text
        assert "1 recording is not on this Mac" in tip, tip
        assert seen["running"].startswith("Updating 1 of 4"), seen["running"]
        line, hover, button = seen["done"]
        assert line == "3 updated · 1 now reads as trusted · 1 could not be measured", line
        assert not button, "nothing is left to update, yet the button is still offered"
        assert "GX019103.MP4: estimated → trusted" in hover, hover
        assert "GX019106.MP4:" in hover, hover
        rows = {e["fingerprint"]: e for e in library.load()["entries"]}
        for n in ("9101", "9102", "9103"):
            row, want = rows[f"GX{n}"], world["measured"][n]
            assert row["ideal_version"] == _CURRENT, row
            assert row["theoretical"] == want["theoretical"] and row["best"] == want["best"], row
            assert _ideal_cell(row)[2] is None, f"{n}'s ideal is still muted"
        assert not rows["GX9103"]["degraded"]
        for fp in ("GX9107", "GX9106"):
            assert rows[fp] == before_rows[fp], f"{fp} was touched: {rows[fp]}"
        assert win.session is session and win._paths == paths and win.view is view
        assert win._pb_toast is toast, "the update raised a PB card"
        assert [r["paths"] for r in library.recent_rows(library.load(), 50)] == recent
        written = seen["written"]
        assert [os.path.basename(p) for p in written] == ["library.json"], written
        print("files written by the Update run:", written)
        # THE SAME ROW A RE-OPEN WRITES: open 9101 by hand now, and its row does not move.
        updated = rows["GX9101"]
        win._load(list(world["recs"]["9101"]))
        _spin_until(lambda: win._paths == world["recs"]["9101"] and win._loading_token is None)
        again = next(e for e in library.load()["entries"] if e["fingerprint"] == "GX9101")
        assert again == updated, (again, updated)
    finally:
        win.close()
        win.deleteLater()
        QApplication.processEvents()
        library.save(library.empty_index())


def test_cancelling_an_update_mid_read_leaves_every_row_whole():
    """Cancel while the second recording is being read: the first row stays updated, every other
    row is byte for byte what it was, the read stops, and the line says it stopped."""
    world = _update_world()
    library.save({"entries": _v1_rows(world)})
    before_rows = {e["fingerprint"]: e for e in library.load()["entries"]}
    from PySide6.QtCore import QTimer

    from studio.library_controller import LibraryController
    win = SimpleNamespace(_load_workers=set())     # no window load in flight: rows start at once
    ctl = LibraryController(win, 0)
    updater = ctl.ideal_updater()
    present, _absent = library.ideal_update_rows(library.load(), _CURRENT)
    got = []
    updater.finished.connect(got.append)
    updater.progress.connect(
        lambda done, _total: done == 1 and QTimer.singleShot(0, updater.cancel))
    updater.start(present)
    _spin_until(lambda: got and not win._load_workers)
    (summary,) = got
    assert summary["cancelled"] and len(summary["updated"]) == 1, summary
    rows = {e["fingerprint"]: e for e in library.load()["entries"]}
    first = summary["updated"][0]["fingerprint"]
    assert rows[first]["ideal_version"] == _CURRENT
    for fp, row in before_rows.items():
        if fp != first:
            assert rows[fp] == row, f"{fp} changed after the cancel: {rows[fp]}"
    library.save(library.empty_index())


def test_an_update_never_writes_back_a_row_forgotten_meanwhile():
    """`library.remeasure_and_save` writes only over the row it measured: a Forget or a Clear that
    landed while the recording was being read is not undone by the write that follows."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "library.json")
        clip = os.path.join(d, "GX010068.MP4")
        library.save({"entries": [_entry("GX010068", [clip], ideal_version=1)]}, path)
        fresh = _entry("GX010068", [clip], theo=46.0)
        before, after = library.remeasure_and_save("GX0068", fresh, path)
        assert before["ideal_version"] == 1 and after["ideal_version"] == _CURRENT
        assert after["theoretical"] == 46.0
        # Another outing under that number (a second camera, 3 days on) is not this row's: said so.
        other = {**_entry("GX010068", [clip], theo=45.0, date="2026-09-22"),
                 "start_ms": 1_790_000_000_000}
        library.save({"entries": [{**_entry("GX010068", [clip], ideal_version=1),
                                   "start_ms": 1_789_740_000_000}]}, path)
        assert library.remeasure_and_save("GX0068", other, path) == library.REMEASURE_OTHER
        assert library.load(path)["entries"][0]["ideal_version"] == 1
        library.save(library.empty_index(), path)
        assert library.remeasure_and_save("GX0068", fresh, path) == library.REMEASURE_GONE
        assert library.load(path)["entries"] == []


def test_an_update_says_when_it_moved_a_personal_best_and_only_then():
    """A row 0.25.0 marked degraded that re-measures trusted can become the track's best: the PB
    chart is read off the rows, so the finish line says it once, by the Library's own summary
    (`library.track_summary`). A run that moved no best says nothing about one."""
    from studio.library_dialog import _update_summary_text
    a = {**_entry("GX010061", ["/x/GX010061.MP4"], best=45.0, date="2026-09-01"),
         "track": "Track T"}
    b = {**_entry("GX010062", ["/x/GX010062.MP4"], best=44.928, date="2026-09-02"),
         "track": "Track T", "degraded": True}
    b_now = {**b, "degraded": False}
    before, after = {"entries": [a, b]}, {"entries": [a, b_now]}
    assert library.pb_changes(before, after, [b, b_now]) == [("Track T", 0, 44.928)]
    line, hover = _update_summary_text({"updated": [b_now], "changed": [(b, b_now)], "failed": [],
                                        "pbs": library.pb_changes(before, after, [b, b_now])})
    assert line == "1 updated · 1 now reads as trusted · your Track T best is now 0:44.928", line
    assert "Your Track T best is now 0:44.928." in hover, hover
    slower = {**b, "best": 45.5}
    assert library.pb_changes({"entries": [a, slower]},
                              {"entries": [a, {**slower, "degraded": False}]}, [slower]) == []


def test_an_unreadable_files_reason_names_the_file_not_its_folder():
    """The Library's hover says which FILE could not be read; its folder goes to the log only."""
    from studio import workers
    path = "/Volumes/pacer-test-drive/Race day/GX017003.MP4"
    saved = workers.Session.load

    def refuse(paths):
        raise ValueError(f"Failed to open file: {paths[0]}")
    workers.Session.load = staticmethod(refuse)
    try:
        w = workers.RemeasureWorker(1, "GX7003", [path])
        got = []
        w.measured.connect(lambda *a: got.append(a))
        w.run()                     # on this thread: the reason, not the threading, is the test
    finally:
        workers.Session.load = saved
    assert got == [(1, "GX7003", None, "Failed to open file: GX017003.MP4")], got


def test_closing_the_window_stops_an_update_read():
    """The window's close cancels Library ▸ Update's read like its own two loads (a read stuck on a
    damaged file must not hold quit for the drain's 60 s), and the drain then joins it."""
    import time

    from PySide6.QtCore import QThread, Signal

    from studio import library_controller
    from studio.app import StudioWindow

    class _Stuck(QThread):
        measured = Signal(int, str, object, str)

        def __init__(self, token, fingerprint, paths):
            super().__init__()
            self._token, self.cancelled = token, False

        def cancel(self):
            self.cancelled = True

        def run(self):
            end = time.time() + 30
            while not self.cancelled and time.time() < end:
                time.sleep(0.01)

    with tempfile.TemporaryDirectory() as d:
        clip = os.path.join(d, "GX010068.MP4")
        open(clip, "wb").close()
        saved = library_controller.RemeasureWorker
        library_controller.RemeasureWorker = _Stuck
        try:
            win = StudioWindow([])
            updater = win.library_ctl.ideal_updater()
            updater.start([_entry("GX010068", [clip], ideal_version=1)])
            worker = updater._worker
            assert worker is not None and worker.isRunning()
            t = time.time()
            win.close()
            took = time.time() - t
        finally:
            library_controller.RemeasureWorker = saved
        assert worker.cancelled, "closing the window left Library ▸ Update reading"
        assert took < 10, f"quit waited {took:.1f} s on the update's read"
        assert not updater.running()
        win.deleteLater()
        QApplication.processEvents()


# ------------------------------------------------------------------ runner
if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok  {_name}")
