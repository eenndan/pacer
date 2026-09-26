"""Import-ergonomics tests (fix/import-ergonomics):

  1. The last-opened-folder preference round-trips through studio.prefs (LAST_DIR), guarded so a
     stale/missing directory reads as "" — and File ▸ Open starts the dialog in that folder and
     records the picked file's folder on a successful open.
  2. A drag-drop of SEVERAL distinct recordings must NOT fold them onto one clock: _open_recordings
     groups the paths (chapters.group_into_recordings), loads only the FIRST recording, and never
     hands _load a merged path list. A single recording (any number of its chapters) loads unchanged.
  3. That drop counts only the recordings the app could actually OFFER to open. `group_into_recordings`
     parses filenames, so the app's own exports beside the footage came back as extra "recordings";
     `ingest.carries_telemetry` is the loader's own gate and excludes them, while an UNREADABLE file
     still counts (F3).

The app-layer bits run against a StudioWindow built via __new__ + QMainWindow.__init__ (skipping the
heavy __init__ that would trigger a real load), with _load / discover_siblings / getOpenFileName
stubbed — the exact seams the existing offscreen tests use. The (3) tests DO need pacer and a real
telemetry file: they copy the committed `3rdparty/gpmf-parser/samples/hero8.mp4` under synthetic
GoPro names into a temp dir, so the gate is asked about real files rather than stand-ins. Run:
    QT_QPA_PLATFORM=offscreen python tests/test_import_ergonomics.py
"""
import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

# A committed GPMF sample — the fixture the telemetry-gate tests copy under synthetic GoPro names,
# so "this file has telemetry" is answered by the real loader on a real file.
TELEMETRY_FIXTURE = os.path.join(_REPO, "3rdparty", "gpmf-parser", "samples", "hero8.mp4")

from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import chapters, ingest, prefs  # noqa: E402
from studio.app import StudioWindow  # noqa: E402


# ------------------------------------------------------------------- prefs round-trip
def test_last_dir_pref_round_trips():
    with tempfile.TemporaryDirectory() as d:
        prefs_file = os.path.join(d, "prefs.json")
        # A folder that EXISTS on disk (so the guarded accessor returns it).
        folder = os.path.join(d, "footage")
        os.makedirs(folder)
        assert prefs.last_dir(prefs_file) == ""  # unset -> today's fallback
        prefs.set_last_dir(folder, prefs_file)
        assert prefs.last_dir(prefs_file) == folder
        print("test_last_dir_pref_round_trips OK")


def test_last_dir_pref_missing_folder_reads_empty():
    with tempfile.TemporaryDirectory() as d:
        prefs_file = os.path.join(d, "prefs.json")
        gone = os.path.join(d, "unplugged-drive")  # never created
        prefs.set_last_dir(gone, prefs_file)
        # The raw value is stored, but the accessor refuses a non-directory -> "" (fallback).
        assert prefs.get(prefs.LAST_DIR, "", prefs_file) == gone
        assert prefs.last_dir(prefs_file) == ""
        print("test_last_dir_pref_missing_folder_reads_empty OK")


def test_last_dir_pref_ignores_empty_set():
    with tempfile.TemporaryDirectory() as d:
        prefs_file = os.path.join(d, "prefs.json")
        prefs.set_last_dir("", prefs_file)  # a no-op, must not persist ""
        assert prefs.get(prefs.LAST_DIR, None, prefs_file) is None
        print("test_last_dir_pref_ignores_empty_set")


# --------------------------------------------------- Open dialog starts in the last folder
def _bare_window():
    w = StudioWindow.__new__(StudioWindow)
    QMainWindow.__init__(w)
    return w


def test_open_dialog_starts_in_last_folder_and_records_it(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        prefs_file = os.path.join(d, "prefs.json")
        footage = os.path.join(d, "footage")
        os.makedirs(footage)
        # Point prefs at our temp file via the seam the app reads (prefs.prefs_path()).
        monkeypatch.setattr(prefs, "prefs_path", lambda: prefs_file)
        prefs.set_last_dir(footage, prefs_file)

        w = _bare_window()
        picked = os.path.join(footage, "GX010060.MP4")
        sibling = os.path.join(footage, "GX020060.MP4")
        for p in (picked, sibling):
            with open(p, "wb"):
                pass
        seen_start = {}

        def fake_dialog(parent, title, start_dir, filt):
            seen_start["dir"] = start_dir
            return picked, ""

        monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(fake_dialog))
        loaded = {}
        monkeypatch.setattr(w, "_load", lambda paths, drop_notice=None:
                            loaded.setdefault("paths", paths))

        w._open_file()

        assert seen_start["dir"] == footage, seen_start  # started in the remembered folder
        # The RECORDING the picked file belongs to, as a drop opens it (QA NEW-1) — on main this
        # was the one file, and every first-open verdict was decided on part of the session.
        assert loaded["paths"] == [picked, sibling], loaded
        # The picked file's folder is now the remembered folder.
        assert prefs.last_dir(prefs_file) == footage
        print("test_open_dialog_starts_in_last_folder_and_records_it OK")


def test_open_dialog_falls_back_to_current_recording_folder(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        prefs_file = os.path.join(d, "prefs.json")
        monkeypatch.setattr(prefs, "prefs_path", lambda: prefs_file)  # unset -> ""
        w = _bare_window()
        w._paths = ["/some/where/GX010099.MP4"]
        assert w._open_start_dir() == "/some/where"
        print("test_open_dialog_falls_back_to_current_recording_folder OK")


# --------------------------------------------------- multi-recording drop never merges
def test_multi_recording_drop_loads_only_first_and_does_not_merge(monkeypatch):
    w = _bare_window()
    calls = []
    notices = []
    # _load also takes the drop_notice carried THROUGH the load (QA L10-02).
    monkeypatch.setattr(w, "_load", lambda paths, drop_notice=None: (
        calls.append(list(paths)), notices.append(drop_notice)))
    # Keep discovery deterministic + off-disk: a recording expands to just its dropped chapters.
    monkeypatch.setattr(
        chapters, "discover_siblings",
        lambda p: [p] if not p.endswith("GX010060.MP4")
        else ["/foot/GX010060.MP4", "/foot/GX020060.MP4"])
    messages = []
    monkeypatch.setattr(w, "statusBar", lambda: type("B", (), {
        "showMessage": lambda self, m, *_a: messages.append(m)})())

    # Three distinct recordings dropped at once (two different NNNN + a non-GoPro clip).
    dropped = ["/foot/GX010060.MP4", "/foot/GX010062.MP4", "/foot/hero6.mp4"]
    w._open_recordings(dropped)

    # Exactly ONE load, and it is the FIRST recording's chapters — NOT the merged 3-path list.
    assert len(calls) == 1, calls
    assert calls[0] == ["/foot/GX010060.MP4", "/foot/GX020060.MP4"], calls[0]
    # The merged/unrelated paths were never passed to _load.
    assert "/foot/GX010062.MP4" not in calls[0]
    assert "/foot/hero6.mp4" not in calls[0]
    # A clear, non-modal message names how many recordings + what was opened.
    assert messages and "3 recordings" in messages[0], messages
    assert "one at a time" in messages[0].lower(), messages
    # ... and the SAME warning is handed to the load, so _on_session_loaded restates it untimed
    # instead of overwriting it mid-load (QA L10-02).
    assert notices == [messages[0]], notices
    print("test_multi_recording_drop_loads_only_first_and_does_not_merge OK")


def test_single_recording_drop_is_unchanged(monkeypatch):
    w = _bare_window()
    calls = []
    notices = []
    # _load also takes the drop_notice carried THROUGH the load (QA L10-02).
    monkeypatch.setattr(w, "_load", lambda paths, drop_notice=None: (
        calls.append(list(paths)), notices.append(drop_notice)))
    monkeypatch.setattr(
        chapters, "discover_siblings",
        lambda p: ["/foot/GX010060.MP4", "/foot/GX020060.MP4"])
    messages = []
    monkeypatch.setattr(w, "statusBar", lambda: type("B", (), {
        "showMessage": lambda self, m, *_a: messages.append(m)})())

    # The two chapters of ONE recording -> one load of the chained siblings, NO multi-drop message.
    w._open_recordings(["/foot/GX010060.MP4", "/foot/GX020060.MP4"])
    assert len(calls) == 1, calls
    assert calls[0] == ["/foot/GX010060.MP4", "/foot/GX020060.MP4"], calls[0]
    assert not messages, messages  # single recording: no "dropped N recordings" notice
    assert notices == [None], notices  # ... and nothing carried into the load either
    print("test_single_recording_drop_is_unchanged OK")


# ------------------------------------------ the drop counts only what it could actually open
# A REAL MP4 container with no GPMF track — the shape of an overlay clip Pacer itself rendered
# next to the footage (`_export_default` saves as `<stem>…_overlay.mp4` in the recording's own
# folder). `chapters.probe_mp4` calls this a container, so nothing upstream of the telemetry gate
# can tell it apart from a recording; only the loader can.
_CONTAINER_NO_TELEMETRY = b"\x00\x00\x00\x18ftypmp41" + b"\x00" * 4096
# The `~/Desktop/D24/GX010060.MP4` shape: a perfect GoPro NAME over bytes that are not video.
_NOT_A_CONTAINER = b'{"this": "is json, not video"}' + b"\x00" * 64


def _write(path: str, blob: bytes) -> str:
    with open(path, "wb") as f:
        f.write(blob)
    return path


def _sample_copy(dst: str) -> str:
    """A committed GPMF sample copied under a synthetic GoPro name, so the fixture is a REAL
    telemetry-bearing file the loader's own gate accepts — not a stand-in for one."""
    shutil.copyfile(TELEMETRY_FIXTURE, dst)
    return dst


def test_carries_telemetry_separates_no_telemetry_from_unreadable():
    """`ingest.carries_telemetry` must give three answers, not two. "I read it and pacer found no
    telemetry" is permanent and local to one file; "I could not read it" says nothing about the
    contents and must never be reported as a verdict on them."""
    with tempfile.TemporaryDirectory() as d:
        overlay = _write(os.path.join(d, "GX010001_lap22_overlay.mp4"), _CONTAINER_NO_TELEMETRY)
        stub = _write(os.path.join(d, "GX010002.MP4"), _NOT_A_CONTAINER)
        real = _sample_copy(os.path.join(d, "GX010003.MP4"))
        missing = os.path.join(d, "never_copied.MP4")

        assert ingest.carries_telemetry(real) == ingest.TELEMETRY_PRESENT
        # A container the loader opens and finds nothing in -> a POSITIVE "not a recording".
        assert ingest.carries_telemetry(overlay) == ingest.TELEMETRY_ABSENT
        # Read it, it is not video at all -> also positive; chapters.probe_mp4 already drew that line.
        assert ingest.carries_telemetry(stub) == ingest.TELEMETRY_ABSENT
        # Bytes that never arrived -> NOT a verdict.
        assert ingest.carries_telemetry(missing) == ingest.TELEMETRY_UNKNOWN

        # A RECORDING is readable if ANY chapter is: Session.load drops the ones it can't parse.
        # This is exactly D24 recording 0060, whose chapter 1 is 2.4 MB of JSON.
        assert ingest.recording_carries_telemetry([stub, real]) == ingest.TELEMETRY_PRESENT
        # ... and the unreadable case PROPAGATES, so a locked or still-copying chapter can never
        # turn a real recording into "this isn't a recording".
        assert ingest.recording_carries_telemetry([overlay, missing]) == ingest.TELEMETRY_UNKNOWN
        assert ingest.recording_carries_telemetry([overlay, stub]) == ingest.TELEMETRY_ABSENT
    print("test_carries_telemetry_separates_no_telemetry_from_unreadable OK")


def _drop(monkeypatch, paths):
    """Drive the REAL `_open_recordings` over `paths`; return (loaded_path_lists, status messages,
    notices carried into the load). Only `_load` and the status bar are stubbed — the grouping,
    the sibling expansion and the telemetry gate all run for real against the files on disk."""
    w = _bare_window()
    calls, notices, messages = [], [], []
    monkeypatch.setattr(w, "_load", lambda p, drop_notice=None: (
        calls.append(list(p)), notices.append(drop_notice)))
    monkeypatch.setattr(w, "statusBar", lambda: type("B", (), {
        "showMessage": lambda self, m, *_a: messages.append(m)})())
    w._open_recordings(paths)
    return calls, messages, notices


def test_drop_does_not_count_the_apps_own_exports_as_recordings(monkeypatch):
    """THE REGRESSION. A folder holding one recording plus the overlay clips Pacer rendered from
    it is ONE recording, and the drop must say nothing at all — exactly as if the user had dropped
    the recording on its own.

    Before the offerable-recording count, this drop said "Dropped 3 recordings — opened recording
    0001. Open the others one at a time.", naming as recordings two files the app itself had
    written beside the footage and which `Session.load` refuses in 0.00 s. Measured on the owner's
    own `~/Desktop/SD_30_08_26` and `~/Desktop/Sandown 3h 2026`, where that is the live case."""
    with tempfile.TemporaryDirectory() as d:
        real = _sample_copy(os.path.join(d, "GX010001.MP4"))
        overlay_a = _write(os.path.join(d, "GX010001_lap22_overlay.mp4"), _CONTAINER_NO_TELEMETRY)
        overlay_b = _write(os.path.join(d, "GX010001_lap23_overlay.mp4"), _CONTAINER_NO_TELEMETRY)
        # Drop order is the sorted listing a dropped FOLDER produces (see _dropped_mp4s).
        calls, messages, notices = _drop(monkeypatch, [real, overlay_a, overlay_b])

    assert len(calls) == 1, calls
    assert calls[0] == [real], calls[0]          # the one real recording, and only it
    assert not messages, messages                # ... and NO "dropped N recordings" sentence
    assert notices == [None], notices            # ... nothing carried into the load either
    print("test_drop_does_not_count_the_apps_own_exports_as_recordings OK")


def test_drop_of_three_synthetic_recordings_still_counts_three(monkeypatch):
    """The other direction, and the guard against fixing the over-count by under-counting: three
    genuine recordings dropped at once still report three, and the window still opens the first."""
    with tempfile.TemporaryDirectory() as d:
        first = _sample_copy(os.path.join(d, "GX010001.MP4"))
        second = _sample_copy(os.path.join(d, "GX010002.MP4"))
        third = _sample_copy(os.path.join(d, "GX010003.MP4"))
        calls, messages, notices = _drop(monkeypatch, [first, second, third])

    assert len(calls) == 1, calls
    assert calls[0] == [first], calls[0]
    assert messages and "3 recordings" in messages[0], messages
    assert "one at a time" in messages[0].lower(), messages
    assert notices == [messages[0]], notices
    print("test_drop_of_three_synthetic_recordings_still_counts_three OK")


def test_drop_counts_an_unreadable_recording_rather_than_dropping_it(monkeypatch):
    """A file whose bytes never arrived is still the user's recording. One real recording plus one
    path that does not exist must report TWO — the telemetry gate excludes only a positive "read
    it, no telemetry in it", never a file it could not read."""
    with tempfile.TemporaryDirectory() as d:
        real = _sample_copy(os.path.join(d, "GX010001.MP4"))
        vanished = os.path.join(d, "GX010002.MP4")   # never written
        calls, messages, notices = _drop(monkeypatch, [real, vanished])

    assert len(calls) == 1, calls
    assert calls[0] == [real], calls[0]
    assert messages and "2 recordings" in messages[0], messages
    assert notices == [messages[0]], notices
    print("test_drop_counts_an_unreadable_recording_rather_than_dropping_it OK")


def test_extracted_worker_and_overlay_modules_import_without_cycle():
    """The self-contained worker/overlay classes were extracted out of the app.py god-object into
    studio.workers / studio.overlays. Those leaf modules must import standalone (no reach back into
    studio.app — an import cycle would raise here), and studio.app must re-use the same class
    objects (its use-sites bind the extracted classes, not shadow copies)."""
    import importlib
    import sys

    # Evict app + the leaf modules, then import ONLY the leaves. If either reached back into
    # studio.app it would re-import it here — so studio.app staying absent from sys.modules is the
    # import-cycle guard.
    for mod in ("studio.app", "studio.workers", "studio.overlays"):
        sys.modules.pop(mod, None)
    importlib.import_module("studio.workers")
    importlib.import_module("studio.overlays")
    assert "studio.app" not in sys.modules, "leaf modules must not import studio.app (import cycle)"

    # Now import app; it binds the SAME (already-cached) leaf classes, not shadow re-declarations.
    import studio.app as app_mod
    from studio.overlays import PBToast, WelcomeView
    from studio.workers import SessionLoadWorker, VideoExportWorker
    for cls in (SessionLoadWorker, VideoExportWorker):
        assert cls.__module__ == "studio.workers", cls.__module__
    for cls in (PBToast, WelcomeView):
        assert cls.__module__ == "studio.overlays", cls.__module__
    assert app_mod.SessionLoadWorker is SessionLoadWorker
    # VideoExportWorker moved to `export_controller` with the export cluster (§7.1). The point of
    # this assertion is unchanged — the importer binds the leaf class rather than re-declaring a
    # shadow of it — only which module does the binding.
    import studio.export_controller as export_ctl
    # By MODULE, not by identity: this test purges sys.modules to prove the leaves import without a
    # cycle, so a freshly-imported controller can legitimately hold a different module OBJECT. What
    # must hold — and what the loop above checks the same way — is that it binds the leaf class
    # rather than re-declaring a shadow of it.
    assert export_ctl.VideoExportWorker.__module__ == "studio.workers", (
        export_ctl.VideoExportWorker.__module__)
    assert not hasattr(app_mod, "VideoExportWorker"), (
        "app.py should no longer name the video worker — the export flow owns it now")
    # PBToast moved to `library_controller` with the PB moment it shows (§7.1, the library half).
    # Same point, same by-module check, for the same sys.modules-purge reason as the worker above.
    import studio.library_controller as library_ctl
    assert library_ctl.PBToast.__module__ == "studio.overlays", library_ctl.PBToast.__module__
    assert not hasattr(app_mod, "PBToast"), (
        "app.py should no longer name the PB card — the library controller shows it now")
    assert app_mod.WelcomeView is WelcomeView
    print("test_extracted_worker_and_overlay_modules_import_without_cycle OK")


# ------------------------------------------------------------------------ runner
def _run_all():
    import inspect
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        params = inspect.signature(fn).parameters
        if "monkeypatch" in params:
            _MonkeyPatch = _make_monkeypatch()
            mp = _MonkeyPatch()
            try:
                fn(mp)
            finally:
                mp.undo()
        else:
            fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} import-ergonomics tests passed")


def _make_monkeypatch():
    """A tiny standalone monkeypatch (setattr + undo) so this file runs under plain python too, not
    only pytest — every other studio test here is a plain-python runner."""
    _MISSING = object()

    class _MP:
        def __init__(self):
            self._undo = []

        def setattr(self, target, name, value):
            old = getattr(target, name, _MISSING)
            self._undo.append((target, name, old))
            setattr(target, name, value)

        def undo(self):
            for target, name, old in reversed(self._undo):
                if old is _MISSING:
                    delattr(target, name)
                else:
                    setattr(target, name, old)
            self._undo.clear()
    return _MP


if __name__ == "__main__":
    _run_all()
