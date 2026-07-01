"""Import-ergonomics tests (fix/import-ergonomics):

  1. The last-opened-folder preference round-trips through studio.prefs (LAST_DIR), guarded so a
     stale/missing directory reads as "" — and File ▸ Open starts the dialog in that folder and
     records the picked file's folder on a successful open.
  2. A drag-drop of SEVERAL distinct recordings must NOT fold them onto one clock: _open_recordings
     groups the paths (chapters.group_into_recordings), loads only the FIRST recording, and never
     hands _load a merged path list. A single recording (any number of its chapters) loads unchanged.

All app-layer bits run against a StudioWindow built via __new__ + QMainWindow.__init__ (skipping the
heavy __init__ that would trigger a real load), with _load / discover_siblings / getOpenFileName
stubbed — the exact seams the existing offscreen tests use. No pacer / telemetry file needed. Run:
    QT_QPA_PLATFORM=offscreen python tests/test_import_ergonomics.py
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import chapters, prefs  # noqa: E402
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
        seen_start = {}

        def fake_dialog(parent, title, start_dir, filt):
            seen_start["dir"] = start_dir
            return picked, ""

        monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(fake_dialog))
        loaded = {}
        monkeypatch.setattr(w, "_load", lambda paths: loaded.setdefault("paths", paths))

        w._open_file()

        assert seen_start["dir"] == footage, seen_start  # started in the remembered folder
        assert loaded["paths"] == [picked]               # single-file Open unchanged (no auto-chain)
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
    monkeypatch.setattr(w, "_load", lambda paths: calls.append(list(paths)))
    # Keep discovery deterministic + off-disk: a recording expands to just its dropped chapters.
    monkeypatch.setattr(
        chapters, "discover_siblings",
        lambda p: [p] if not p.endswith("GX010060.MP4")
        else ["/foot/GX010060.MP4", "/foot/GX020060.MP4"])
    messages = []
    monkeypatch.setattr(w, "statusBar", lambda: type("B", (), {
        "showMessage": lambda self, m: messages.append(m)})())

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
    print("test_multi_recording_drop_loads_only_first_and_does_not_merge OK")


def test_single_recording_drop_is_unchanged(monkeypatch):
    w = _bare_window()
    calls = []
    monkeypatch.setattr(w, "_load", lambda paths: calls.append(list(paths)))
    monkeypatch.setattr(
        chapters, "discover_siblings",
        lambda p: ["/foot/GX010060.MP4", "/foot/GX020060.MP4"])
    messages = []
    monkeypatch.setattr(w, "statusBar", lambda: type("B", (), {
        "showMessage": lambda self, m: messages.append(m)})())

    # The two chapters of ONE recording -> one load of the chained siblings, NO multi-drop message.
    w._open_recordings(["/foot/GX010060.MP4", "/foot/GX020060.MP4"])
    assert len(calls) == 1, calls
    assert calls[0] == ["/foot/GX010060.MP4", "/foot/GX020060.MP4"], calls[0]
    assert not messages, messages  # single recording: no "dropped N recordings" notice
    print("test_single_recording_drop_is_unchanged OK")


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
    assert app_mod.VideoExportWorker is VideoExportWorker
    assert app_mod.PBToast is PBToast
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
