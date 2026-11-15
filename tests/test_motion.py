"""studio/motion.py — the switch, the clock and the one code path every transition runs through.

A NEW SURFACE, so a new file: the module owns what no existing test file covers (the Reduce
motion switch and its three sources, the system probe, the shared timer). The one transition, the
hero Δ roll, is pinned on the real view in tests/test_central_view_realqt.py.

  * offscreen, motion is OFF unless PACER_MOTION=1 (every pixel-grabbing test sees end states);
    PACER_MOTION=0 turns it off anywhere; the user's Reduce motion choice wins over both the
    system and PACER_MOTION=1, and None follows the system;
  * the system probe reads `defaults` once and tolerates an absent key, a missing or failing
    command, a timeout and another OS — it never raises;
  * with motion off a transition's END runs synchronously inside `start`, and nothing is left
    running; with it on, a fake clock steps it through eased intermediate values to its exact end,
    and the shared timer stops with the last transition;
  * a destroyed owner drops its transition without calling into it; `finish_all` ends everything.

Run: python tests/test_motion.py
"""
import contextlib
import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _qtapp import themed_app  # noqa: E402
from PySide6.QtCore import QObject  # noqa: E402

from studio import motion  # noqa: E402

_APP = themed_app()


@contextlib.contextmanager
def _env(value):
    old = os.environ.get("PACER_MOTION")
    if value is None:
        os.environ.pop("PACER_MOTION", None)
    else:
        os.environ["PACER_MOTION"] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("PACER_MOTION", None)
        else:
            os.environ["PACER_MOTION"] = old


@contextlib.contextmanager
def _state(pref=None, system=False):
    saved = (motion._reduce_pref, motion._system_reduce)
    motion.set_reduce_pref(pref)
    motion._system_reduce = system
    try:
        yield
    finally:
        motion._reduce_pref, motion._system_reduce = saved


class _Clock:
    def __init__(self):
        self.now = 50.0

    def __call__(self):
        return self.now


def test_offscreen_is_off_unless_pacer_motion_and_the_users_choice_wins():
    with _state():
        with _env(None):
            assert not motion.enabled(), "offscreen must default to motion OFF"
        with _env("1"):
            assert motion.enabled(), "PACER_MOTION=1 must enable motion offscreen"
        with _env("0"):
            assert not motion.enabled()
    with _env("1"):
        with _state(pref=None, system=True):
            assert motion.reduce_motion() and not motion.enabled(), "None follows the system"
        with _state(pref=False, system=True):
            assert motion.enabled(), "the user's 'no' wins over the system's Reduce Motion"
        with _state(pref=True, system=False):
            assert not motion.enabled(), "the user's Reduce motion wins over PACER_MOTION=1"
    print("ok switch: offscreen off, PACER_MOTION, the pref wins")


def test_the_system_probe_tolerates_every_failure():
    calls = []

    def runner(result=None, exc=None):
        def run(argv, **kw):
            calls.append(argv)
            if exc is not None:
                raise exc
            return result
        return run

    def done(code, out):
        return subprocess.CompletedProcess([], code, stdout=out, stderr="")

    saved = motion._system_reduce
    try:
        with _env("1"):
            if sys.platform == "darwin":
                assert motion.probe_system(runner(done(0, "1\n"))) is True
                assert calls[-1] == ["defaults", "read", "com.apple.universalaccess",
                                     "reduceMotion"], calls[-1]
                assert motion.probe_system(runner(done(0, "0\n"))) is False
                # an absent key: `defaults` exits 1 with "does not exist"
                assert motion.probe_system(runner(done(1, ""))) is False
                assert motion.probe_system(runner(exc=FileNotFoundError("defaults"))) is False
                assert motion.probe_system(runner(
                    exc=subprocess.TimeoutExpired("defaults", 1.0))) is False
            real = sys.platform
            sys.platform = "linux"
            try:
                n = len(calls)
                assert motion.probe_system(runner(done(0, "1\n"))) is False
                assert len(calls) == n, "another OS must not spawn `defaults`"
            finally:
                sys.platform = real
        with _env(None):
            n = len(calls)
            assert motion.probe_system(runner(done(0, "1\n"))) is False
            assert len(calls) == n, "offscreen without PACER_MOTION=1 must not spawn a process"
    finally:
        motion._system_reduce = saved
    print("ok system probe: key absent / command missing / timeout / other OS / headless")


def test_disabled_runs_the_end_synchronously_and_leaves_nothing_running():
    seen = []
    with _state(), _env(None):
        tr = motion.start(None, 200, step=lambda p: seen.append(("step", p)),
                          end=lambda: seen.append("end"))
    assert seen == ["end"] and tr.done, seen
    assert motion.active_count() == 0 and not motion.timer_active()
    print("ok disabled: end runs inside start, nothing left running")


def test_a_fake_clock_steps_eased_values_to_the_exact_end_and_the_timer_stops():
    clock = _Clock()
    seen = []
    motion.set_clock(clock)
    try:
        with _state(), _env("1"):
            tr = motion.start(None, 200, step=seen.append, end=lambda: seen.append("end"))
            assert motion.timer_active() and motion.active_count() == 1
            for _ in range(3):
                clock.now += 0.05
                motion.pump()
            want = [motion.ease_out_cubic(x / 0.2) for x in (0.05, 0.1, 0.15)]
            assert all(abs(a - b) < 1e-9 for a, b in zip(seen[:3], want, strict=True)), seen
            assert all(0.0 < v < 1.0 for v in seen[:3]) and seen[0] < seen[1] < seen[2]
            assert seen[0] > 0.05 / 0.2, "cubic ease-out starts fast"
            clock.now += 0.06
            motion.pump()
            assert seen[-1] == "end" and tr.done
            assert motion.active_count() == 0 and not motion.timer_active()
            motion.pump()
            assert seen.count("end") == 1
    finally:
        motion.set_clock(None)
    print("ok fake clock: eased steps, exact end once, timer stopped")


def test_finish_cancel_and_a_destroyed_owner():
    clock = _Clock()
    motion.set_clock(clock)
    try:
        with _state(), _env("1"):
            seen = []
            a = motion.start(None, 200, seen.append, lambda: seen.append("a-end"))
            b = motion.start(None, 200, seen.append, lambda: seen.append("b-end"))
            b.cancel()
            motion.finish_all()
            assert seen == ["a-end"] and a.done and b.done, seen
            owner = QObject()
            gone = []
            motion.start(owner, 200, lambda p: gone.append(p), lambda: gone.append("end"))
            assert motion.active_count() == 1
            owner.deleteLater()
            del owner
            from PySide6.QtCore import QCoreApplication, QEvent
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            clock.now += 1.0
            motion.pump()
            assert gone == [] and motion.active_count() == 0 and not motion.timer_active(), gone
    finally:
        motion.set_clock(None)
    print("ok finish / cancel / destroyed owner dropped without a callback")


def test_the_capture_harnesses_finish_every_transition_before_they_grab():
    """media_capture and ui_capture count on motion being off offscreen; under PACER_MOTION=1 a
    grab could catch a rolling readout, so each grab ends every transition first."""
    import tempfile

    from PySide6.QtWidgets import QLabel

    from studio.dev import media_capture, ui_capture
    calls = []
    real = motion.finish_all
    motion.finish_all = lambda: calls.append(1)
    try:
        label = QLabel("x")
        media_capture.grab(label)
        assert calls == [1], "media_capture.grab did not finish transitions first"
        with tempfile.TemporaryDirectory() as d:
            ui_capture._grab(label, os.path.join(d, "x.png"))
        assert calls == [1, 1], "ui_capture._grab did not finish transitions first"
    finally:
        motion.finish_all = real
    print("ok capture harnesses finish transitions before grabbing")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} MOTION TESTS PASSED")
