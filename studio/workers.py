"""Off-UI-thread QThread workers used by StudioWindow: the video-export renderer, the Session.load
pipeline and the demo-clip fetch. Self-contained (DI via constructor args + queued Qt signals) —
no reach into StudioWindow internals."""

from __future__ import annotations

import gc
import logging
import os
import time

from PySide6.QtCore import QThread, Signal

from . import chapters, demo, export_video, ingest, sidecar
from .session import Session

_log = logging.getLogger(__name__)


def start_load(worker: QThread) -> None:
    """Start a recording-loading worker from the GUI thread, after one `gc.collect()` there — the
    one door every Session-loading worker goes through (`StudioWindow._start_load_worker`, the
    reference load's `_start_reference_worker`, Library ▸ Update's `IdealUpdater`), so the guard
    below cannot be forgotten by the next one.

    A MITIGATION, not a cure. Python's cyclic GC runs on whichever thread trips its threshold, and
    Qt widgets held only by reference cycles (LapTable's own closures hold it in some) crash or
    deadlock in ~QTableView when that thread is a load worker's — test_studio_features' reentrant
    load did, 3 runs of 3, with 441 cyclic Qt/studio objects from earlier windows pending; main has
    the same hazard under a forced worker collect. Collecting HERE frees what is pending when a load
    starts, on the GUI thread. Garbage made while a load runs, and the other worker threads (export,
    which collects for itself, demo resolve), stay exposed: the cure is fewer cycles.
    tests/test_load_worker_gc.py pins this collect for all three callers."""
    gc.collect()
    worker.start()


class VideoExportWorker(QThread):
    """QThread wrapper running export_video.Renderer off the UI thread, forwarding frame progress
    and a final ok/message via queued signals. cancel() cooperatively stops the render; a
    failed/cancelled run drops the partial output."""

    progress = Signal(int, int)              # (frames_done, frames_total)
    finished_export = Signal(bool, str)      # (ok, message)  message="cancelled" / an error text

    # A check to run on THIS thread before the renderer is even built — the export controller sets
    # it on the first worker of a queue to `export_video.guard_free_space` over the WHOLE queue. It
    # is an attribute rather than a constructor argument so it is optional to every stand-in that
    # builds this worker's shape, and it runs here rather than on the UI thread because it asks the
    # disk and ffprobes the source, either of which can block on a slow or network volume.
    preflight = None
    # What the USER asked for, in words ("this lap", "all laps, file 3 of 19"), for the session
    # log's start line. The renderer knows the lap and everything it resolved; only the caller
    # knows the scope row that produced this spec. Optional like `preflight`.
    log_scope = ""

    def __init__(self, session, spec, make_renderer=None):
        """`make_renderer(session, spec)` builds the renderer this worker drives; the default is the
        single-lap `export_video.Renderer`.

        It is injected rather than branched on the spec's type because the CALLER is the only thing
        that knows what a render needs: a distance-locked compare export takes a SECOND session
        (pane B's, which for a cross-recording compare is a different recording entirely), and that
        is not on the spec and has no business being — a spec describes windows and files, not the
        objects a render reads them through. Everything else about running a render off the UI
        thread is identical, which is why this is one argument and not a second worker."""
        super().__init__()
        self._session = session
        self._spec = spec
        self._make_renderer = make_renderer or export_video.Renderer
        self._cancelled = False
        # The finished render's `RenderResult` — read by the GUI thread once `finished_export`
        # says ok, for where an overlay-only file starts in its footage (`result.sync`).
        self.result = None

    def cancel(self):
        self._cancelled = True

    def run(self):
        # THE SESSION LOG GETS ONE LINE WHEN A RENDER STARTS AND ONE WHEN IT ENDS, whatever the
        # ending (HEALTH-3). Before this an export left no trace in pacer.log at all, so "the
        # export was slow" or "it failed" could not be answered from the one file a shipped build
        # keeps: not the encoder, not the decode path, not the frame size, not the time it took.
        out = self._spec.out_path
        # Whether the output was there before this render: a PNG folder the render created goes
        # with its frames on the way out of a cancel; one that was already there stays.
        self._out_existed = os.path.exists(out)
        t_start = time.monotonic()
        renderer = None
        try:
            if self.preflight is not None:
                self.preflight()
            renderer = self._make_renderer(self._session, self._spec)
            describe = getattr(renderer, "describe", None)
            _log.info("export started: %s%s", f"{self.log_scope}: " if self.log_scope else "",
                      describe() if callable(describe) else os.path.abspath(out))
            t_start = time.monotonic()
            self.result = renderer.run(progress=lambda d, t: self.progress.emit(d, t),
                                       cancel=lambda: self._cancelled)
            took = time.monotonic() - t_start
            frames = int(getattr(self.result, "frames", 0) or 0)
            _log.info("export finished: %d frames in %.1f s (%.1f fps), %s -> %s", frames, took,
                      frames / took if took > 0 else 0.0,
                      export_video.fmt_bytes(export_video.output_bytes(out)), os.path.abspath(out))
            self.finished_export.emit(True, "")
        except export_video.InsufficientSpaceError as exc:
            # REFUSED BEFORE A FRAME WAS RENDERED, so there is no partial output to drop — and
            # what IS at the output path (a previous export the user said to replace) must
            # survive a refusal that wrote nothing.
            _log.info("export refused before rendering: %s", exc)
            self.finished_export.emit(False, str(exc))
        except export_video.CancelledError:
            self._log_end("cancelled", renderer, t_start)
            self._cleanup_partial()
            self.finished_export.emit(False, "cancelled")
        except Exception as exc:  # surfaced in a dialog by the GUI thread
            first = (str(exc).strip().splitlines() or [type(exc).__name__])[0][:200]
            self._log_end(f"failed ({first})", renderer, t_start)
            self._cleanup_partial()
            self.finished_export.emit(False, str(exc))

    def _log_end(self, how: str, renderer, t_start: float) -> None:
        """The end line of a render that did not finish: how far it got and how long it ran."""
        written = getattr(renderer, "frames_written", None)
        done, planned = written() if callable(written) else (0, 0)
        _log.info("export %s after %d of %d frames, %.1f s -> %s", how, done, planned,
                  time.monotonic() - t_start, os.path.abspath(self._spec.out_path))

    def _cleanup_partial(self):
        """Drop a partially-written output so cancel/error leaves no broken MP4 — and no frames.

        A PNG sequence's output is a FOLDER, which `os.remove` refuses; the error was swallowed,
        so a cancelled sequence kept every frame it had written (885 PNGs, 41.2 MB: EXP-5). Its
        frames go instead, and the folder with them when this render created it."""
        out = self._spec.out_path
        try:
            if getattr(self._spec, "is_png_sequence", False) or os.path.isdir(out):
                export_video.remove_png_frames(
                    out, remove_folder=not getattr(self, "_out_existed", True))
            elif os.path.exists(out):
                os.remove(out)
        except OSError:
            pass


class SessionLoadWorker(QThread):
    """QThread wrapper running the ~1.4–4 s synchronous Session.load(paths) off the UI thread, so the
    window stays responsive (the "Loading telemetry…" placeholder shows) instead of freezing on every
    open/reload. Session.load is pure compute (numpy + pacer C++; creates no Qt objects) so it is safe
    off-thread; the resulting Session is a plain object handed back via a queued signal.

    Each worker carries the `token` of the _load that started it; the window's completion slots ignore
    any result whose token is stale (a newer _load superseded it), so a second drag-drop can't apply an
    older load destructively. Per-sample ingest is Python/GIL-held; the numpy/g-meter portions release
    the GIL — the win is the non-blocking, cancellable, supersede-safe load, not full parallelism."""

    loaded = Signal(int, list, object)   # (token, paths, session)
    failed = Signal(int, list, object)   # (token, paths, exception)

    def __init__(self, token: int, paths: list[str]):
        super().__init__()
        self._token = token
        self._paths = list(paths)
        self._cancelled = False

    def cancel(self):
        """Stop the read at its next GPS payload (`ingest.cancellable`) instead of waiting it out.
        Only called once the result is unwanted — a newer open superseded it, its loading card's
        Cancel, the window closing — so the token guard drops whatever it then emits; what it buys
        is the single load slot, and the quit, back at once from a read that is slow or stuck."""
        self._cancelled = True

    def run(self):
        try:
            with ingest.cancellable(lambda: self._cancelled):
                session = Session.load(self._paths)
        except Exception as exc:  # noqa: BLE001 - surface ANY load failure to the GUI thread
            self.failed.emit(self._token, self._paths, exc)
            return
        self.loaded.emit(self._token, self._paths, session)


class RemeasureWorker(QThread):
    """One Library row's recording measured again off the UI thread, for Library ▸ Update
    (`LibraryController.IdealUpdater`): the steps a re-open takes before it writes the row —
    `Session.load` on the row's paths in chapter order (`StudioWindow._load`), the user's saved
    timing lines restored from the sidecar beside the recording, READ only
    (`Session.restore_saved_timing_lines`, `_on_session_loaded`), and `Session.library_entry` — with
    no window, no view, no notice and no PB decision. `measured` carries the entry, or None and why
    (a recording with no valid lap writes no row on a re-open either). The session is dropped here:
    nothing outlives the measurement but the dict. Cancellable like `SessionLoadWorker`."""

    measured = Signal(int, str, object, str)    # (token, fingerprint, entry | None, error)

    def __init__(self, token: int, fingerprint: str, paths: list[str]):
        super().__init__()
        self._token, self._fingerprint = token, fingerprint
        self._paths = chapters.load_order(list(paths))
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            with ingest.cancellable(lambda: self._cancelled):
                session = Session.load(self._paths)
            session.restore_saved_timing_lines(sidecar.sidecar_path(self._paths[0]))
            if not session.valid_lap_ids():
                self.measured.emit(self._token, self._fingerprint, None, "no valid laps")
                return
            entry = session.library_entry(self._paths)
        except Exception as exc:  # noqa: BLE001 — a row that fails to load is left as it was
            first = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
            # The whole path goes to the log; the Library's hover names the file only.
            _log.warning("library update: %s not measured: %s", self._fingerprint, first)
            for p in self._paths:
                for spelt in (os.path.realpath(p), os.path.abspath(p), p):
                    first = first.replace(spelt, os.path.basename(p))
            self.measured.emit(self._token, self._fingerprint, None, first[:200])
            return
        self.measured.emit(self._token, self._fingerprint, entry, "")


class DemoResolveWorker(QThread):
    """QThread wrapper running demo.resolve_demo_recording() off the UI thread.

    Resolution is a path lookup that FALLS THROUGH TO THE NETWORK: a first run with no cache does
    urllib.request.urlopen() + a streaming shutil.copyfileobj of the release asset. Called from the
    welcome button's slot it froze the whole window — 0 of ~125 expected 16 ms timer ticks were
    delivered, with no busy affordance of any kind (QA L10-03) — and demo._DEMO_TIMEOUT_S bounds
    each SOCKET OP, not the fetch, so the freeze had no useful upper bound. Same reason the
    ~1.4-4 s Session.load runs on SessionLoadWorker.

    `token` is the window's load token at the moment the button was clicked: the window drops the
    result if anything else started loading meanwhile (the user opened their own recording while
    the fetch ran), matching how a stale SessionLoadWorker result is dropped."""

    resolved = Signal(int, object)   # (token, path str | None)

    def __init__(self, token: int):
        super().__init__()
        self._token = token

    def run(self):
        try:
            path = demo.resolve_demo_recording()
        except Exception:  # noqa: BLE001 — a demo fetch must never take the app down
            _log.warning("demo resolve failed; showing the welcome state", exc_info=True)
            path = None
        self.resolved.emit(self._token, path)
