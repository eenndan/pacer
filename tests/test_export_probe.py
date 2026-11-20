"""The ProRes VideoToolbox probe behind the export dialog: one in flight, and only a fact is kept.

WHY IT EXISTS. `warm_export_probes` started a new probe thread on every dialog open until an
answer was cached, and `prores_videotoolbox_usable` kept whichever probe finished LAST. Opening the
real dialog fourteen times in quick succession on the demo ran fourteen probes side by side, and a
probe whose session did not open would have been kept as "this Mac has no ProRes VideoToolbox" for
the rest of the run, sending every overlay-only export to the slower prores_ks. Now (`export_video`,
"ONE PROBE AT A TIME"):
  * concurrent callers and dialog opens share ONE probe and its answer, a miss included;
  * a MISS (no session, a failed encode or decode) is never kept: the next ask probes again;
  * a success, an encoder this ffmpeg does not list, a bent alpha and a TIMEOUT are kept;
  * one export action resolves the encoder once (`pin_alpha_encoder`), so a miss costs one probe
    per action and the free-space estimate and the render agree on the encoder;
  * the dialog never waits for any of it, and its hint says what is known in each state.
THE H.264 PROBE (2026-11-20). The first export click of a session froze the window 520-635 ms on the
demo: the hint asked `videotoolbox_usable` on the GUI thread. It now rides the same warm-up and
lock: the dialog comes up at once and says it is measuring, a render started first waits for the
same answer, and the encoder chosen is the one the old probe chose.
The probe is injected (`export_video._prores_session_probe`, or ffmpeg itself through a fake
`subprocess`), so no test here needs VideoToolbox.

Run: python tests/test_export_probe.py
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PACER_NO_MEDIA"] = "1"

# test_export_gates diverts every store into a temp tree on import, before any window exists.
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
    QLabel,
    QMessageBox,
    QProgressDialog,
)
from test_export_gates import (  # noqa: E402
    _APP,
    FakeSession,
    _clear_export_preset,
    _combo,
    _FakeVideoWorker,
    _run_options_dialog,
    _window,
)

from studio import export_controller  # noqa: E402
from studio import export_video as ev  # noqa: E402
from studio.export_controller import ExportController  # noqa: E402

_REAL_PROBE = ev._prores_session_probe
_REAL_H264 = (ev._h264_session_probe, ev._H264)       # settled by test_export_gates on import
_REAL_LISTED = ev.videotoolbox_encoder_available


class _FakeProbe:
    """Stands in for one real probe: answers `outcomes` in turn (the last one repeats), each after
    `gate` is set when there is one, counting calls and the most that ran at once."""

    def __init__(self, *outcomes, gate: threading.Event | None = None):
        self.outcomes, self.gate = list(outcomes), gate
        self.calls = self.running = self.most = 0
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            self.calls += 1
            self.running += 1
            self.most = max(self.most, self.running)
            outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if self.gate is not None:
            self.gate.wait(10)
        with self._lock:
            self.running -= 1
        return outcome


def _fresh(probe, h264=None) -> None:
    """A process that has asked nothing yet, whose ProRes probe is `probe`. The H.264 probe is
    asked afresh too when `h264` is given (its probe), else it keeps the answer test_export_gates
    settled on import."""
    _settle_warmup()
    ev._PRORES = ev._EncoderProbe()
    ev._prores_session_probe = probe
    if h264 is not None:
        ev._H264 = ev._EncoderProbe()
        ev._h264_session_probe = h264


def _settle_warmup() -> None:
    thread = ev._WARM_THREAD
    if thread is not None:
        thread.join(10)
        assert not thread.is_alive(), "a warm-up thread outlived its test"


def _restore() -> None:
    _settle_warmup()
    ev._prores_session_probe = _REAL_PROBE
    ev._PRORES = ev._EncoderProbe()
    ev._h264_session_probe, ev._H264 = _REAL_H264
    ev.videotoolbox_encoder_available = _REAL_LISTED


def _warmups() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "export-probe-warmup"]


def _callers_at_once(n: int, gate: threading.Event) -> list:
    """`n` threads asking while the probe is held, then released; their answers."""
    answers = []
    callers = [threading.Thread(target=lambda: answers.append(ev.prores_videotoolbox_usable()))
               for _ in range(n)]
    for t in callers:
        t.start()
    time.sleep(0.1)
    assert ev.prores_videotoolbox_state() == "probing"
    gate.set()
    for t in callers:
        t.join(10)
    return answers


def test_concurrent_callers_and_dialog_opens_share_one_probe():
    """Eight renders asking and eight dialog warm-ups while the first probe is still running: one
    probe, one warm-up thread, and every caller gets its answer."""
    gate = threading.Event()
    probe = _FakeProbe(True, gate=gate)
    _fresh(probe)
    try:
        warmed = {ev.warm_export_probes(None) for _ in range(8)}
        assert len(_warmups()) <= 1 and len(warmed) == 1, (
            f"{len(_warmups())} warm-up threads alive, {len(warmed)} distinct ones returned")
        answers = _callers_at_once(8, gate)
        _settle_warmup()
        assert probe.calls == 1 and probe.most == 1, (
            f"{probe.calls} probes ran, {probe.most} at once")
        assert answers == [True] * 8, answers
        assert ev.known_alpha_encoder() == ev.VT_PRORES
        assert ev.warm_export_probes(None) is None, "a settled answer was asked again"
    finally:
        gate.set()
        _restore()
    print("ok: 8 callers + 8 dialog warm-ups -> 1 probe, 1 thread")


def test_callers_waiting_on_a_probe_that_misses_take_its_miss():
    """The callers that waited on a probe take ITS answer even when it is a miss: eight renders
    starting together cost one probe, not eight in a row."""
    gate = threading.Event()
    probe = _FakeProbe(None, gate=gate)
    _fresh(probe)
    try:
        answers = _callers_at_once(8, gate)
        assert probe.calls == 1, f"{probe.calls} probes for 8 callers waiting on one miss"
        assert answers == [False] * 8, answers
        assert ev.prores_videotoolbox_state() == "missed"
    finally:
        gate.set()
        _restore()
    print("ok: 8 callers waiting on a miss -> 1 probe, 8 x False")


def test_a_miss_is_never_kept():
    """Five misses in a row, then a success: each ask after a miss probes exactly once and answers
    False for now (its render goes to prores_ks), and the success is kept."""
    probe = _FakeProbe(None, None, None, None, None, True)
    _fresh(probe)
    try:
        for n in range(1, 6):
            assert ev.prores_videotoolbox_usable() is False
            assert probe.calls == n, (n, probe.calls)
            assert ev.prores_videotoolbox_state() == "missed"
            assert ev.known_alpha_encoder() is None, "a miss was reported as a known encoder"
            assert ev.export_probes_ready(None), "a dialog would wait for ever on a miss"
        assert ev.prores_videotoolbox_usable() is True
        assert ev.prores_videotoolbox_usable() is True
        assert probe.calls == 6, probe.calls
        assert ev.known_alpha_encoder() == ev.VT_PRORES
        assert ev.resolve_alpha_encoder("auto") == ev.VT_PRORES
    finally:
        _restore()
    print("ok: 5 misses, then success -> 6 probes, settled True")


# ------------------------------------------------------------- ffmpeg itself, through a fake
_SIDE = 64


def _frame(top, bottom) -> bytes:
    px = np.zeros((_SIDE, _SIDE, 4), np.uint8)
    px[: _SIDE // 2], px[_SIDE // 2:] = top, bottom
    return px.tobytes()


_STRAIGHT = _frame((255, 255, 255, 128), (220, 30, 20, 255))
_PREMULTIPLIED = _frame((128, 128, 128, 128), (220, 30, 20, 255))


class _FakeFfmpeg:
    """`subprocess` as `export_video` sees it, answering the -encoders listing, the encode
    (`pipe:0` in) and the decode (`pipe:1` out) as told, counting the runs."""

    def __init__(self, *, enc_rc=0, dec_rc=0, decoded=_STRAIGHT, timeout=False, listed=True,
                 h264_listed=True, h264_rc=0):
        self.enc_rc, self.dec_rc, self.decoded = enc_rc, dec_rc, decoded
        self.timeout, self.listed, self.runs = timeout, listed, 0
        self.h264_listed, self.h264_rc = h264_listed, h264_rc
        self.SubprocessError = subprocess.SubprocessError
        self.TimeoutExpired = subprocess.TimeoutExpired

    def run(self, args, **_kw):
        self.runs += 1
        if "-encoders" in args:
            names = (("h264_videotoolbox " if self.h264_listed else "")
                     + ("prores_videotoolbox" if self.listed else ""))
            return SimpleNamespace(returncode=0, stdout=names)
        if self.timeout:
            raise subprocess.TimeoutExpired(args, 30)
        if ev.VT_H264 in args:                       # the H.264 session probe's 64x64 encode
            return SimpleNamespace(returncode=self.h264_rc, stdout=b"")
        if "pipe:0" in args:
            return SimpleNamespace(returncode=self.enc_rc, stdout=b"")
        return SimpleNamespace(returncode=self.dec_rc, stdout=self.decoded)


def _with_ffmpeg(fake, body):
    real = (ev.shutil, ev.subprocess)
    ev.shutil = SimpleNamespace(which=lambda _n: "/usr/bin/ffmpeg")
    ev.subprocess = fake
    try:
        return body()
    finally:
        ev.shutil, ev.subprocess = real


def test_the_round_trip_tells_a_miss_from_a_fact():
    """A failed encode, a failed decode and a short decode are MISSES (None: maybe just now); a
    bent alpha and a timeout are facts (False); a straight alpha is True."""
    cases = {
        "encode failed": (_FakeFfmpeg(enc_rc=1), None),
        "decode failed": (_FakeFfmpeg(dec_rc=1), None),
        "decode short": (_FakeFfmpeg(decoded=b"\0" * 16), None),
        "timed out": (_FakeFfmpeg(timeout=True), False),
        "premultiplied": (_FakeFfmpeg(decoded=_PREMULTIPLIED), False),
        "straight": (_FakeFfmpeg(), True),
    }
    for name, (fake, want) in cases.items():
        got = _with_ffmpeg(fake, lambda: ev._alpha_round_trip(ev.VT_PRORES))
        assert got is want, f"{name}: {got!r}, wanted {want!r}"
    print("ok: encode/decode failures are misses; bent alpha and timeout are facts")


def test_a_timeout_or_an_unlisted_encoder_settles_false_without_asking_again():
    """A probe that took the whole timeout is a wedged engine, and an encoder this ffmpeg does not
    list (or no ffmpeg) is the build: False, kept, and the next ask runs nothing. A failed encode,
    by contrast, is asked again."""
    for name, fake in (("timeout", _FakeFfmpeg(timeout=True)),
                       ("unlisted", _FakeFfmpeg(listed=False))):
        _fresh(_REAL_PROBE)
        try:
            def ask_twice(fake=fake):
                assert ev.prores_videotoolbox_usable() is False
                runs = fake.runs
                assert ev.prores_videotoolbox_usable() is False
                return runs
            runs = _with_ffmpeg(fake, ask_twice)
            assert ev.prores_videotoolbox_state() == "no", name
            assert fake.runs == runs, f"{name}: asked ffmpeg again after a settled False"
        finally:
            _restore()
    _fresh(_REAL_PROBE)
    real_which = ev.shutil
    try:
        ev.shutil = SimpleNamespace(which=lambda _n: None)
        assert ev.prores_videotoolbox_usable() is False
        assert ev.prores_videotoolbox_state() == "no"
    finally:
        ev.shutil = real_which
        _restore()
    fake = _FakeFfmpeg(enc_rc=1)
    _fresh(_REAL_PROBE)
    try:
        _with_ffmpeg(fake, ev.prores_videotoolbox_usable)
        runs = fake.runs
        _with_ffmpeg(fake, ev.prores_videotoolbox_usable)
        assert ev.prores_videotoolbox_state() == "missed" and fake.runs > runs, fake.runs
    finally:
        _restore()
    print("ok: timeout / unlisted / no ffmpeg settle False; a failed encode is asked again")


# ------------------------------------------------------------- one export action
def _prores_specs(n: int, folder: str) -> list:
    cfg = ev.OverlayConfig(out_height=1080, overlay_only=True, alpha_codec=ev.ALPHA_PRORES)
    return [ev.ExportSpec(out_path=os.path.join(folder, f"ride_lap{i + 1}.mov"), lap_id=i,
                          t0=10.0 + 30.0 * i, t1=40.0 + 30.0 * i,
                          src_path="/nonexistent/GX010099.MP4", config=cfg) for i in range(n)]


def _export_action(specs) -> list[str]:
    """What the export controller's preflight does, then what each file's Renderer resolves."""
    ev.pin_alpha_encoder(specs)
    ev.guard_free_space(specs, probe=lambda _p: (1920, 1080, 59.94))
    return [ev.resolve_alpha_encoder(spec.config.encoder) for spec in specs]


def test_one_export_action_asks_the_probe_once_and_its_estimate_agrees_with_its_render():
    """A single file and an All-laps batch of 14, with a probe that always misses: ONE probe per
    action. With a probe that misses then succeeds, the space check and every render name the same
    encoder, the one the action resolved."""
    real_free, real_codec = ev.free_bytes, ev.output_codec
    ev.free_bytes = lambda *_a, **_k: None          # the disk is not what is under test
    estimated = []
    ev.output_codec = lambda cfg: estimated.append(real_codec(cfg)) or estimated[-1]
    try:
        with tempfile.TemporaryDirectory(prefix="probe-action-") as td:
            for n in (1, 14):
                probe = _FakeProbe(None)
                _fresh(probe)
                encoders = _export_action(_prores_specs(n, td))
                assert probe.calls == 1, f"{n} file(s): {probe.calls} probes for one export"
                assert encoders == [ev.SW_PRORES] * n, encoders

            probe = _FakeProbe(None, True)
            _fresh(probe)
            estimated.clear()
            encoders = _export_action(_prores_specs(14, td))
            assert probe.calls == 1, probe.calls
            assert estimated == encoders == [ev.SW_PRORES] * 14, (estimated, encoders)
            _fresh(_FakeProbe(True))
            estimated.clear()
            encoders = _export_action(_prores_specs(3, td))
            assert estimated == encoders == [ev.VT_PRORES] * 3, (estimated, encoders)
    finally:
        ev.free_bytes, ev.output_codec = real_free, real_codec
        _restore()
    print("ok: one export action (1 file, 14 files) -> 1 probe; estimate == render encoder")


def test_the_export_queue_resolves_the_encoder_before_its_space_check():
    """The REAL `_run_video_export` on an All-laps batch of 14, its worker faked: the preflight it
    hands the first worker pins the encoder into every spec before the space check, so that check
    and every file's render ask the probe once between them."""
    win = _window(FakeSession())
    made = []
    saved = (export_controller.VideoExportWorker, QDialog.exec, QMessageBox.exec,
             QMessageBox.warning, ev.free_bytes, ev.probe_video_size)

    def _worker(session, spec, make_renderer=None):
        made.append(_FakeVideoWorker(session, spec, make_renderer))
        return made[-1]

    def _exec(dlg):
        if not isinstance(dlg, QProgressDialog):
            return saved[1](dlg)
        made[0].preflight()                          # what the worker runs before its Renderer
        made[0].finished_export.emit(False, "stopped by the test")
        return QDialog.Accepted

    probe = _FakeProbe(None)
    _fresh(probe)
    export_controller.VideoExportWorker = _worker
    QDialog.exec = _exec
    QMessageBox.exec = lambda *_a, **_k: 0
    QMessageBox.warning = staticmethod(lambda *_a, **_k: QMessageBox.Ok)
    ev.free_bytes = lambda *_a, **_k: None
    ev.probe_video_size = lambda _p: (1920, 1080, 59.94)   # so the space check prices each file
    try:
        with tempfile.TemporaryDirectory(prefix="probe-queue-") as td:
            specs = _prores_specs(14, td)
            win.exports._run_video_export(specs, 0)
            assert made and made[0].preflight is not None, "the queue ran no preflight"
            assert probe.calls == 1, f"{probe.calls} probes for one 14-file export"
            assert [s.config.encoder for s in specs] == ["software"] * 14, (
                [s.config.encoder for s in specs])
            assert {ev.resolve_alpha_encoder(s.config.encoder) for s in specs} == {ev.SW_PRORES}
            assert probe.calls == 1, "a render asked the probe again"
    finally:
        (export_controller.VideoExportWorker, QDialog.exec, QMessageBox.exec,
         QMessageBox.warning, ev.free_bytes, ev.probe_video_size) = saved
        _restore()
        win.hide()
    print("ok: the real export queue pins the encoder before its space check (1 probe / 14 files)")


# ------------------------------------------------------------- what the dialog says
def _hint(dlg) -> QLabel:
    return next(lb for lb in dlg.findChildren(QLabel) if lb.property("role") == "Hint")


def _prores_row() -> int:
    return next(i for i, (_label, value) in enumerate(ExportController._EXPORT_CONTENT_OPTIONS)
                if value == ev.ALPHA_PRORES)


def _open_on_prores(win, seconds: float = 0.0) -> tuple[float, str]:
    """Open the real export picker, choose ProRes 4444, let it run `seconds`, and return how long
    it took to come up and what its hint said as it closed."""
    seen = {}
    t0 = time.monotonic()

    def on_dialog(dlg):
        seen["up"] = time.monotonic() - t0
        _combo(dlg, "Contents").setCurrentIndex(_prores_row())
        deadline = time.monotonic() + seconds
        while True:
            _APP.processEvents()
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        seen["hint"] = _hint(dlg).text()
        return QDialog.Rejected

    _run_options_dialog(win, on_dialog)
    return seen["up"], seen["hint"]


def test_the_dialog_never_waits_and_says_what_it_knows_in_each_state():
    """The picker comes up while the probe is held, names no encoder until one is known, says so
    when the probe missed, and names the encoder once it is settled."""
    _clear_export_preset()
    win = _window(FakeSession())
    gate = threading.Event()
    probe = _FakeProbe(None, None, True, gate=gate)
    _fresh(probe)
    try:
        up, probing = _open_on_prores(win)
        assert up < 1.0, f"the dialog took {up:.2f} s to come up behind a held probe"
        assert ev.prores_videotoolbox_state() == "probing"
        assert "with ProRes 4444." in probing and " via " not in probing, probing
        assert ExportController._PRORES_MISSED_LINE not in probing, probing

        gate.set()                                       # the first probe misses
        _settle_warmup()
        _up, missed = _open_on_prores(win, seconds=0.5)  # this open asks again: a second miss
        assert ev.prores_videotoolbox_state() == "missed" and probe.calls == 2, probe.calls
        assert ExportController._PRORES_MISSED_LINE in missed and " via " not in missed, missed

        _up, settled = _open_on_prores(win, seconds=0.5)  # and again: True, kept
        assert ev.prores_videotoolbox_state() == "yes" and probe.calls == 3, probe.calls
        assert "via prores_videotoolbox" in settled, settled
        assert ExportController._PRORES_MISSED_LINE not in settled, settled

        _fresh(_FakeProbe(False))                         # settled absent: names prores_ks
        _up, absent = _open_on_prores(win, seconds=0.5)
        assert "via prores_ks" in absent and ExportController._PRORES_MISSED_LINE not in absent, (
            absent)
    finally:
        gate.set()
        _restore()
        win.hide()
    print("ok: the dialog comes up at once and its hint matches the probe's state")


# ------------------------------------------------------------- the H.264 probe, the same way
def _listed_after(gate: threading.Event, calls: list):
    """`videotoolbox_encoder_available` as a slow machine answers it: after `gate` (at most 10 s),
    False, so the session probe behind it runs no ffmpeg. Counts its calls."""
    def listed():
        calls.append(time.monotonic())
        gate.wait(10)
        return False
    return listed


def test_the_first_export_click_opens_at_once_and_the_hint_waits_for_the_h264_probe():
    """File ▸ Export overlay video… through the real action and the real `exec`, on a process
    that has not asked the H.264 encoder yet, its probe held: the dialog is up before the probe
    answers, its hint says it is measuring and quotes no size, and once the probe lands it names
    the encoder `resolve_encoder` picks, with a size and a time. On main the hint asked the probe
    on the GUI thread, so the dialog came up only when it answered (the demo: 520-635 ms)."""
    _clear_export_preset()
    gate, calls = threading.Event(), []
    _fresh(_FakeProbe(True), h264=_REAL_H264[0])
    ev.videotoolbox_encoder_available = _listed_after(gate, calls)
    real_size = ev.probe_video_size
    ev.probe_video_size = lambda _p: (1920, 1080, 30.0)
    seen = {}
    with tempfile.TemporaryDirectory(prefix="probe-click-") as td:
        src = os.path.join(td, "GX010099.MP4")
        with open(src, "wb") as fh:
            fh.write(b"not really a video")
        win = _window(FakeSession(), paths=(src,))
        win.exports.sync_menu()
        t0 = time.monotonic()

        def poll():
            dlg = QApplication.activeModalWidget()
            if not isinstance(dlg, QDialog) or not dlg.isVisible():
                QTimer.singleShot(1, poll)
                return
            if "up" not in seen:
                seen["up"] = time.monotonic() - t0
                seen["state"] = ev.videotoolbox_state()
                seen["first"] = _hint(dlg).text()
            text = _hint(dlg).text()
            if not gate.is_set():
                # Hold the encoder until the hint has redrawn on the measured frame (the
                # warm-up's first answer), so the wait it names is the encoder's alone.
                if ev.known_video_size(src) is None or "Measuring the footage" in text:
                    if time.monotonic() - t0 < 5:
                        QTimer.singleShot(5, poll)
                        return
                seen["frame_known"] = text
                gate.set()
            if "Measuring" in text and time.monotonic() - t0 < 10:
                QTimer.singleShot(5, poll)
                return
            seen["last"] = text
            dlg.reject()
        try:
            QTimer.singleShot(0, poll)
            win._export_video_action.trigger()
        finally:
            gate.set()
            ev.probe_video_size = real_size
            _restore()
            win.hide()
            _clear_export_preset()
    up, first, last = seen["up"], seen["first"], seen["last"]
    assert up < 0.5, f"the first export dialog took {up * 1000:.0f} ms to come up"
    assert seen["state"] in ("unasked", "probing"), seen["state"]
    assert "Measuring" in first, first
    assert "About " not in first and "frames to render" not in first, first
    assert "Measuring this Mac's video encoder" in seen["frame_known"], seen["frame_known"]
    assert len(calls) == 1, f"the encoder was listed {len(calls)} times"
    assert f"with {ev.SW_H264}, about " in last and "Measuring" not in last, last
    print(f"ok: the first export click opens in {up * 1000:.0f} ms; the hint waits for H.264")


def test_an_export_started_before_the_h264_probe_answers_waits_for_its_answer():
    """A render that resolves its encoder while the dialog's warm-up still probes waits on the
    probe's lock and takes ITS answer: one probe, never a second one or a different encoder."""
    for answer, encoder in ((True, ev.VT_H264), (False, ev.SW_H264)):
        gate = threading.Event()
        probe = _FakeProbe(answer, gate=gate)
        _fresh(_FakeProbe(True), h264=probe)
        got = []
        try:
            ev.warm_export_probes(None)
            render = threading.Thread(
                target=lambda got=got: got.append(ev.resolve_encoder("auto")))
            render.start()
            time.sleep(0.2)
            assert render.is_alive() and not got, "the render did not wait for the probe"
            assert ev.videotoolbox_state() == "probing" and ev.known_encoder() is None
            gate.set()
            render.join(10)
            _settle_warmup()
            assert got == [encoder], (answer, got)
            assert probe.calls == 1, f"{probe.calls} H.264 probes for one dialog and one render"
            assert ev.known_encoder() == ev.resolve_encoder("auto") == encoder
            assert probe.calls == 1, "a settled H.264 answer was asked again"
        finally:
            gate.set()
            _restore()
    print("ok: an export started early waits for the H.264 probe and takes its answer")


def test_the_h264_encoder_chosen_is_the_one_it_always_was():
    """The probe's own logic, unchanged, through ffmpeg faked: VideoToolbox when it is listed AND
    its 64x64 session opens; libx264 when it is unlisted, fails or times out. Every answer is kept
    for the run, a failure included (asked once), as the old cache kept it."""
    cases = {"opens": (_FakeFfmpeg(), ev.VT_H264),
             "fails": (_FakeFfmpeg(h264_rc=1), ev.SW_H264),
             "unlisted": (_FakeFfmpeg(h264_listed=False), ev.SW_H264),
             "times out": (_FakeFfmpeg(timeout=True), ev.SW_H264)}
    for name, (fake, want) in cases.items():
        _fresh(_FakeProbe(True), h264=_REAL_H264[0])
        ev.videotoolbox_encoder_available = _REAL_LISTED
        listed = vars(_REAL_LISTED).pop("_cached", None)
        try:
            def ask_twice(fake=fake):
                first = ev.resolve_encoder("auto")
                runs = fake.runs
                return first, ev.resolve_encoder("auto"), runs
            first, again, runs = _with_ffmpeg(fake, ask_twice)
            assert first == again == want, (name, first, again)
            assert fake.runs == runs, f"{name}: the H.264 probe was asked again"
            assert ev.videotoolbox_state() == ("yes" if want == ev.VT_H264 else "no"), name
        finally:
            if listed is not None:
                _REAL_LISTED._cached = listed            # type: ignore[attr-defined]
            else:
                vars(_REAL_LISTED).pop("_cached", None)
            _restore()
    print("ok: the H.264 encoder is the one the old probe chose, each answer kept")


def _run_all():
    tests = (test_concurrent_callers_and_dialog_opens_share_one_probe,
             test_callers_waiting_on_a_probe_that_misses_take_its_miss,
             test_a_miss_is_never_kept,
             test_the_round_trip_tells_a_miss_from_a_fact,
             test_a_timeout_or_an_unlisted_encoder_settles_false_without_asking_again,
             test_one_export_action_asks_the_probe_once_and_its_estimate_agrees_with_its_render,
             test_the_export_queue_resolves_the_encoder_before_its_space_check,
             test_the_dialog_never_waits_and_says_what_it_knows_in_each_state,
             test_the_first_export_click_opens_at_once_and_the_hint_waits_for_the_h264_probe,
             test_an_export_started_before_the_h264_probe_answers_waits_for_its_answer,
             test_the_h264_encoder_chosen_is_the_one_it_always_was)
    failed = []
    for test in tests:
        try:
            test()
        except Exception:  # noqa: BLE001 — run every test, then fail by name
            import traceback
            traceback.print_exc()
            failed.append(test.__name__)
            print(f"FAIL {test.__name__}")
    _clear_export_preset()
    if failed:
        print(f"{len(failed)}/{len(tests)} export-probe tests FAILED: {failed}")
        sys.exit(1)
    print("all export-probe tests passed")


if __name__ == "__main__":
    _run_all()
