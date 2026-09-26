"""The primary pane's audio: un-muting must not leave an FFmpeg engine thread waiting for the GIL, and
an un-mute lasts until the app quits (QA 2026-09-26, LIFE-1 and LIFE-4).

WHAT WAS MEASURED (LIFE-1). Un-muted, the primary pane's QAudioOutput is connected to by the engine's
AudioRenderer. An ASYNCHRONOUS teardown — stop(), the dispose() of a reload, setAudioOutput(None) —
destroys that renderer on its own thread, and ~QObject calls the output's disconnectNotify while
holding one of Qt's pooled signal-slot mutexes. For an output PySide constructed, that virtual is the
generated wrapper, which the FIRST time takes the GIL to look for a Python override (and caches the
answer per object). On the owner's MK, with the GIL held for 5 s right after the first stop() or
dispose() of an un-muted pane, the QFFmpeg::AudioRenderer thread sat in take_gil under
QAudioOutputWrapper::disconnectNotify in 4 of 4 runs: a thread waiting for the GIL with a Qt mutex
held. That is the precondition of the hang sampled once on 2026-09-25 (leaving compare), which also
needs the GUI thread, holding the GIL, to want that same mutex from inside C++.

WHAT THIS DOES NOT CLAIM. The hang itself never reproduced on demand: 0 of 300 realistic cycles
(seams, lap clicks, compare, reloads, quits) and 0 of 12 amplified ones on v0.4.2. What the guard
changes is the precondition, measured: 4 of 4 runs parked before, 0 of 4 after.

THE GUARD. PlayerPane runs both override lookups on the GUI thread when it builds the output
(player_pane._resolve_notify_overrides), so the renderer's later call is answered from the cache.
The first test pins that call: the pane's own output sees a connect and a disconnect ON THE GUI
THREAD before the constructor returns, while it is still muted and detached. It pins the call, not
shiboken's cache (measured on PySide 6.11.1); if an upgrade drops the cache, a C++-made output (a
QML AudioOutput, LIFE candidate iii) is the structural fallback.

LIFE-4. Every open rebuilds the view, and with it a fresh, muted pane, so an un-mute reset on every
open. It now carries to the next recording in the same run (VideoView._audio_on), never across a
relaunch: a launch starts muted on purpose, and nothing is persisted.
"""

from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("PACER_NO_MEDIA", None)       # the first test is about the REAL QAudioOutput
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtMultimedia import QAudioOutput  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import player_pane  # noqa: E402
from studio.video_view import VideoView  # noqa: E402


class _SpyOutput(QAudioOutput):
    """Records each connect/disconnect notification and whether it ran on the GUI thread."""

    def __init__(self):
        super().__init__()
        self.seen: list[tuple[str, bool]] = []

    def connectNotify(self, signal):  # noqa: N802 — Qt's virtual
        self.seen.append(("connect", threading.current_thread() is threading.main_thread()))
        super().connectNotify(signal)

    def disconnectNotify(self, signal):  # noqa: N802 — Qt's virtual
        self.seen.append(("disconnect", threading.current_thread() is threading.main_thread()))
        super().disconnectNotify(signal)


def test_the_pane_resolves_its_audio_outputs_notify_overrides_on_the_gui_thread():
    made: list[_SpyOutput] = []

    def factory():
        made.append(_SpyOutput())
        return made[-1]

    real = player_pane.QAudioOutput
    player_pane.QAudioOutput = factory
    try:
        pane = player_pane.PlayerPane(None)
    finally:
        player_pane.QAudioOutput = real
    try:
        assert len(made) == 1 and pane.audio is made[0], "the pane must build its own QAudioOutput"
        seen = made[0].seen
        assert ("connect", True) in seen and ("disconnect", True) in seen, (
            f"the pane's QAudioOutput saw {seen}: its connectNotify/disconnectNotify override lookups "
            "are left for the first engine thread that disconnects from it, which then waits for the "
            "GIL under a Qt signal-slot mutex (LIFE-1)")
        assert seen.index(("connect", True)) < seen.index(("disconnect", True)), seen
        # still muted, still detached: priming must not attach the output (a muted pane never needs
        # the engine's audio renderer)
        assert pane.is_muted() and pane.player.audioOutput() is None
    finally:
        pane.dispose()
    print("test_the_pane_resolves_its_audio_outputs_notify_overrides_on_the_gui_thread OK")


def test_an_unmute_carries_to_the_next_recording_opened_in_this_run():
    """LIFE-4: un-mute, open another recording (a fresh VideoView, as _build_ui makes one): it must
    come up un-muted, its output attached and its button saying so; a mute carries the same way."""
    assert getattr(VideoView, "_audio_on", False) is False, (
        "a launch must start muted: nothing may carry into a new run")
    os.environ["PACER_NO_MEDIA"] = "1"             # the inert triplet: no device is ever opened
    views = []
    try:
        views.append(VideoView(None))
        first = views[-1]
        assert first.pane.is_muted(), "a fresh run's first recording must open muted"
        first.toggle_mute()                        # the mute button / M
        assert not first.pane.is_muted()
        views.append(VideoView(None))              # he opens another recording
        second = views[-1]
        assert not second.pane.is_muted(), (
            "the un-mute reset when another recording was opened in the same window (LIFE-4)")
        assert second.pane._audio_attached, "un-muted but its audio output never attached"
        assert second.mute_btn.toolTip() == "Audio on — click to mute (M)", second.mute_btn.toolTip()
        second.toggle_mute()
        views.append(VideoView(None))
        third = views[-1]
        assert third.pane.is_muted() and not third.pane._audio_attached
        assert third.mute_btn.toolTip() == "Audio muted — click to unmute (M)", third.mute_btn.toolTip()
    finally:
        VideoView._audio_on = False
        os.environ.pop("PACER_NO_MEDIA", None)
        for v in views:
            v.stop_all()
            v.deleteLater()
        _APP.processEvents()
    print("test_an_unmute_carries_to_the_next_recording_opened_in_this_run OK")


if __name__ == "__main__":
    test_the_pane_resolves_its_audio_outputs_notify_overrides_on_the_gui_thread()
    test_an_unmute_carries_to_the_next_recording_opened_in_this_run()
    print("test_player_audio: all OK")
