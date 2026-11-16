"""The ProRes VideoToolbox probe behind the export dialog: one in flight, and only a fact is kept.

WHY IT EXISTS. `warm_export_probes` started a new probe thread on every dialog open until an
answer was cached, and `prores_videotoolbox_usable` kept whichever probe finished LAST. Opening the
real dialog eight times in quick succession on the demo ran up to eight probes side by side (five
with nothing else running), and a probe whose session did not open would have been kept as "this
Mac has no ProRes VideoToolbox" for the rest of the run, sending every overlay-only export to the
much slower prores_ks. Now (`export_video`, "ONE PROBE AT A TIME"):
  * concurrent callers and dialog opens share ONE probe and its answer;
  * a probe that could not open a session is a MISS: not kept, asked again, settled False only
    after three in a row; a success after a miss is kept;
  * an encoder this ffmpeg does not list is settled False at once and never probed again;
  * the dialog never waits for any of it, and its hint says what is known in each state.
The probe is injected (`export_video._prores_session_probe`), so no test here needs VideoToolbox.

Run: python tests/test_export_probe.py
"""
import os
import sys
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PACER_NO_MEDIA"] = "1"

# test_export_gates diverts every store into a temp tree on import, before any window exists.
from PySide6.QtWidgets import QDialog, QLabel  # noqa: E402
from test_export_gates import (  # noqa: E402
    _APP,
    FakeSession,
    _clear_export_preset,
    _combo,
    _run_options_dialog,
    _window,
)

from studio import export_video as ev  # noqa: E402
from studio.export_controller import ExportController  # noqa: E402

_REAL_PROBE = ev._prores_session_probe


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


def _fresh(probe) -> None:
    """A process that has asked nothing yet, whose probe is `probe`."""
    _settle_warmup()
    ev._PRORES = ev._ProResProbe()
    ev._prores_session_probe = probe


def _settle_warmup() -> None:
    thread = ev._WARM_THREAD
    if thread is not None:
        thread.join(10)
        assert not thread.is_alive(), "a warm-up thread outlived its test"


def _restore() -> None:
    _settle_warmup()
    ev._prores_session_probe = _REAL_PROBE
    ev._PRORES = ev._ProResProbe()


def _warmups() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "export-probe-warmup"]


def test_concurrent_callers_and_dialog_opens_share_one_probe():
    """Eight renders asking and eight dialog warm-ups while the first probe is still running: one
    probe, one warm-up thread, and every caller gets its answer."""
    gate = threading.Event()
    probe = _FakeProbe(True, gate=gate)
    _fresh(probe)
    try:
        answers = []
        callers = [threading.Thread(target=lambda: answers.append(ev.prores_videotoolbox_usable()))
                   for _ in range(8)]
        for t in callers:
            t.start()
        warmed = {ev.warm_export_probes(None) for _ in range(8)}
        time.sleep(0.1)
        assert ev.prores_videotoolbox_state() == "probing"
        assert len(_warmups()) <= 1 and len(warmed) == 1, (
            f"{len(_warmups())} warm-up threads alive, {len(warmed)} distinct ones returned")
        gate.set()
        for t in callers:
            t.join(10)
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


def test_a_miss_is_not_kept_and_a_success_after_it_is():
    """A session that did not open answers THIS caller False (its render goes to prores_ks) but is
    not kept: the next ask probes again, and its True is the answer from then on."""
    probe = _FakeProbe(None, True)
    _fresh(probe)
    try:
        assert ev.prores_videotoolbox_usable() is False
        assert ev.prores_videotoolbox_state() == "missed"
        assert ev.known_alpha_encoder() is None, "a miss was reported as a known encoder"
        assert ev.export_probes_ready(None), "a dialog would wait for ever on a miss"
        assert ev.prores_videotoolbox_usable() is True
        assert ev.prores_videotoolbox_usable() is True
        assert probe.calls == 2, probe.calls
        assert ev.known_alpha_encoder() == ev.VT_PRORES
        assert ev.resolve_alpha_encoder("auto") == ev.VT_PRORES
    finally:
        _restore()
    print("ok: miss, then success -> settled True")


def test_three_misses_in_a_row_settle_false_and_stop_probing():
    probe = _FakeProbe(None)
    _fresh(probe)
    try:
        for _ in range(6):
            assert ev.prores_videotoolbox_usable() is False
        assert probe.calls == ev._PRORES_MISSES_TO_SETTLE == 3, probe.calls
        assert ev.prores_videotoolbox_state() == "no"
        assert ev.known_alpha_encoder() == ev.SW_PRORES
    finally:
        _restore()
    print("ok: three misses in a row settle False; no fourth probe")


def test_an_encoder_this_ffmpeg_lacks_is_settled_false_without_a_session():
    """Not listed by `ffmpeg -encoders` (or no ffmpeg at all) is a property of the build: False at
    once, kept, and no session is ever tried."""
    real = (ev.shutil, ev.subprocess, ev._alpha_round_trip)

    def no_session(_enc):
        raise AssertionError("a session was tried for an encoder ffmpeg does not list")

    listing = SimpleNamespace(stdout=" V....D h264_videotoolbox  VideoToolbox H.264 Encoder\n")
    try:
        for which, run in ((lambda _n: "/usr/bin/ffmpeg", lambda *a, **k: listing),
                           (lambda _n: None, lambda *a, **k: listing)):
            _fresh(_REAL_PROBE)
            ev.shutil = SimpleNamespace(which=which)
            ev.subprocess = SimpleNamespace(run=run, SubprocessError=Exception)
            ev._alpha_round_trip = no_session
            for _ in range(4):
                assert ev.prores_videotoolbox_usable() is False
            assert ev.prores_videotoolbox_state() == "no"
            assert ev._PRORES.probes == 1, ev._PRORES.probes
    finally:
        ev.shutil, ev.subprocess, ev._alpha_round_trip = real
        _restore()
    print("ok: an unlisted encoder / no ffmpeg -> settled False after one look")


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
    probe = _FakeProbe(None, True, gate=gate)
    _fresh(probe)
    try:
        up, probing = _open_on_prores(win)
        assert up < 1.0, f"the dialog took {up:.2f} s to come up behind a held probe"
        assert ev.prores_videotoolbox_state() == "probing"
        assert "with ProRes 4444." in probing and " via " not in probing, probing
        assert "did not answer" not in probing, probing

        gate.set()                         # the first probe misses
        _settle_warmup()
        assert ev.prores_videotoolbox_state() == "missed"
        _up, missed = _open_on_prores(win, seconds=0.5)   # this open asks again: True
        assert ev.prores_videotoolbox_state() == "yes" and probe.calls == 2, probe.calls
        assert "via prores_videotoolbox" in missed and "did not answer" not in missed, missed

        # A Mac whose sessions keep missing: each open asks once more and says it missed, until
        # the third miss in a row settles on prores_ks, which the hint then names.
        _fresh(_FakeProbe(None))
        ev.prores_videotoolbox_usable()                    # miss 1, e.g. a render's own ask
        _up, missed = _open_on_prores(win, seconds=0.5)    # miss 2
        assert ev.prores_videotoolbox_state() == "missed"
        assert ExportController._PRORES_MISSED_LINE in missed and " via " not in missed, missed
        _up, settled = _open_on_prores(win, seconds=0.5)   # miss 3: settled
        assert ev.prores_videotoolbox_state() == "no"
        assert "via prores_ks" in settled and "did not answer" not in settled, settled
    finally:
        gate.set()
        _restore()
        win.hide()
    print("ok: the dialog comes up at once and its hint matches the probe's state")


def _run_all():
    tests = (test_concurrent_callers_and_dialog_opens_share_one_probe,
             test_a_miss_is_not_kept_and_a_success_after_it_is,
             test_three_misses_in_a_row_settle_false_and_stop_probing,
             test_an_encoder_this_ffmpeg_lacks_is_settled_false_without_a_session,
             test_the_dialog_never_waits_and_says_what_it_knows_in_each_state)
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
