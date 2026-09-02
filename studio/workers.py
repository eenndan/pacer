"""Off-UI-thread QThread workers used by StudioWindow: the video-export renderer, the Session.load
pipeline and the demo-clip fetch. Self-contained (DI via constructor args + queued Qt signals) —
no reach into StudioWindow internals."""

from __future__ import annotations

import os

from PySide6.QtCore import QThread, Signal

from . import demo, export_video
from .session import Session


class VideoExportWorker(QThread):
    """QThread wrapper running export_video.Renderer off the UI thread, forwarding frame progress
    and a final ok/message via queued signals. cancel() cooperatively stops the render; a
    failed/cancelled run drops the partial output."""

    progress = Signal(int, int)              # (frames_done, frames_total)
    finished_export = Signal(bool, str)      # (ok, message)  message="cancelled" / an error text

    def __init__(self, session, spec):
        super().__init__()
        self._session = session
        self._spec = spec
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            renderer = export_video.Renderer(self._session, self._spec)
            renderer.run(progress=lambda d, t: self.progress.emit(d, t),
                         cancel=lambda: self._cancelled)
            self.finished_export.emit(True, "")
        except export_video.CancelledError:
            self._cleanup_partial()
            self.finished_export.emit(False, "cancelled")
        except Exception as exc:  # surfaced in a dialog by the GUI thread
            self._cleanup_partial()
            self.finished_export.emit(False, str(exc))

    def _cleanup_partial(self):
        """Drop a partially-written output so cancel/error leaves no broken MP4."""
        try:
            if os.path.exists(self._spec.out_path):
                os.remove(self._spec.out_path)
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

    def run(self):
        try:
            session = Session.load(self._paths)
        except Exception as exc:  # noqa: BLE001 - surface ANY load failure to the GUI thread
            self.failed.emit(self._token, self._paths, exc)
            return
        self.loaded.emit(self._token, self._paths, session)


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
        except Exception as exc:  # noqa: BLE001 — a demo fetch must never take the app down
            print(f"demo: resolve failed ({exc!r}); showing the welcome state.", flush=True)
            path = None
        self.resolved.emit(self._token, path)
