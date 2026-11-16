"""Motion: the one place that owns the app's few transitions — their durations, easing and switch.

The app animates two things, because the motion carries meaning (W2 design, section C): the hero Δ
readout counts to its new value (`DELTA_ROLL_MS`) on a discrete jump of at least `ROLL_MIN_JUMP_S`
— a seek within a lap, under an unchanged baseline — never during playback. The design's other
two were measured and not built (PR #557, `studio/docs/refused-2026-09.md` §28): the lap you left
fading on the map cost +2.3 to +7.3 ms a frame against a ~2 ms budget and needed a second
colour-bucket pool; the Lap race rows never swap order at the playback tick, so a slide has
nothing to move. The second, outside playback: the Laps table's slow-lap finder fades the outline
round the row its "next ›" landed on (`FINDER_OUTLINE_MS`; static while motion is off). Nothing
runs on the 30 Hz playback path: with no transition running there is no
timer and no per-tick work (`active_count() == 0`, `frames` does not move).

THE SWITCH (`enabled()`) is False when any of these says so, in this order:
  1. the user's View ▸ Reduce motion choice (`set_reduce_pref`; None = follow the system);
  2. macOS's own Reduce Motion (`probe_system`, read once at startup);
  3. a headless Qt platform (offscreen / minimal) unless `PACER_MOTION=1`, so every pixel-grabbing
     test, capture harness and export sees the END state at once, exactly as before motion existed.
`PACER_MOTION=0` turns it off anywhere.

ONE CODE PATH. A transition started while motion is off runs its `end` synchronously inside
`start` — the same callback a finished animation runs — so the disabled behaviour cannot drift
from the animated one's final state. A transition can be finished early (`finish`: its end state,
now) or cancelled (`cancel`: dropped, its end NOT run — the caller is about to set a newer state).
`finish_all()` ends everything at once (the Reduce motion toggle calls it).

One shared QTimer steps every running transition and stops when the last one ends. Each
transition names an `owner` QObject; when the owner is destroyed (a window closed, another
recording loaded) the transition is dropped without calling into it. Tests drive time with
`set_clock` + `pump()`.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QGuiApplication

_log = logging.getLogger(__name__)

#: The hero Δ readout's count from the old value to the new one (cubic ease-out).
DELTA_ROLL_MS = 180
#: The smallest Δ jump (s) that rolls; anything smaller is set directly, as before.
ROLL_MIN_JUMP_S = 0.05
#: The slow-lap finder's outline round the row it landed on, fading out (lap_table: it marks the
#: row "next ›" found and then gets out of the way; with motion off it stays until the next move).
FINDER_OUTLINE_MS = 1200
#: The shared timer's period: ~60 Hz, only while a transition runs.
FRAME_MS = 16

_HEADLESS = ("offscreen", "minimal")
_reduce_pref: bool | None = None       # View ▸ Reduce motion (None = follow the system)
_system_reduce: bool = False           # macOS Reduce Motion, read once by probe_system
_clock: Callable[[], float] = time.monotonic
_active: list[Transition] = []
_timer: QTimer | None = None
#: Frames stepped since import — a test reads it to prove the idle tick does no motion work.
frames = 0
#: Transitions started since import (disabled ones included: they ran their end synchronously).
starts = 0


def ease_out_cubic(p: float) -> float:
    """Fast start, gentle landing — the curve every transition here uses."""
    p = min(max(p, 0.0), 1.0)
    return 1.0 - (1.0 - p) ** 3


def set_reduce_pref(value: bool | None) -> None:
    """The user's View ▸ Reduce motion choice: True / False, or None to follow the system."""
    global _reduce_pref
    _reduce_pref = None if value is None else bool(value)


def system_reduce() -> bool:
    """macOS's own Reduce Motion as `probe_system` last read it."""
    return _system_reduce


def reduce_motion() -> bool:
    """Whether motion is reduced by the user's choice, or by the system's when they made none."""
    return _system_reduce if _reduce_pref is None else _reduce_pref


def _headless() -> bool:
    app = QGuiApplication.instance()
    name = QGuiApplication.platformName() if app is not None else ""
    return name in _HEADLESS or (app is None and os.environ.get("QT_QPA_PLATFORM") in _HEADLESS)


def enabled() -> bool:
    """True when transitions animate; False means every one applies its end state at once."""
    env = os.environ.get("PACER_MOTION", "")
    if env == "0" or reduce_motion():
        return False
    return env == "1" or not _headless()


def probe_system(run=subprocess.run) -> bool:
    """Read macOS's Reduce Motion once (the app calls this at startup) and remember it.

    `defaults read com.apple.universalaccess reduceMotion` prints 1 or 0; an absent key exits
    non-zero, and a failed or missing command, a timeout or another OS all read as "not reduced".
    Skipped on a headless platform (motion is off there anyway), so no test spawns a process.
    Timed and logged: it must never be what makes startup slow."""
    global _system_reduce
    if sys.platform != "darwin" or (_headless() and os.environ.get("PACER_MOTION") != "1"):
        _system_reduce = False
        return _system_reduce
    t0 = time.perf_counter()
    try:
        out = run(["defaults", "read", "com.apple.universalaccess", "reduceMotion"],
                  capture_output=True, text=True, timeout=1.0, check=False)
        _system_reduce = out.returncode == 0 and out.stdout.strip() == "1"
    except (OSError, subprocess.SubprocessError, ValueError):
        _system_reduce = False
    _log.info("system Reduce Motion: %s (read in %.1f ms)", _system_reduce,
              (time.perf_counter() - t0) * 1000.0)
    return _system_reduce


def set_clock(clock: Callable[[], float] | None) -> None:
    """Tests: drive transitions from a fake clock (seconds); None restores the real one."""
    global _clock
    _clock = clock or time.monotonic


class Transition:
    """One running transition: `step(eased_progress)` per frame, then `end()` once."""

    # __weakref__: Qt holds the destroyed-signal connection to a bound method weakly.
    __slots__ = ("owner", "duration", "step", "end", "t0", "done", "_conn", "__weakref__")

    def __init__(self, owner: QObject | None, duration_ms: int,
                 step: Callable[[float], None], end: Callable[[], None]):
        self.owner = owner
        self.duration = max(duration_ms, 1) / 1000.0
        self.step = step
        self.end = end
        self.t0 = _clock()
        self.done = False
        self._conn = None

    @property
    def running(self) -> bool:
        return not self.done

    def progress(self) -> float:
        """Eased progress at the current clock, 0..1."""
        return ease_out_cubic((_clock() - self.t0) / self.duration)

    def finish(self) -> None:
        """Jump to the end state now (no-op if already done)."""
        if self.done:
            return
        self._drop()
        self.end()

    def cancel(self) -> None:
        """Drop without running `end` (the caller sets a newer state itself)."""
        if not self.done:
            self._drop()

    def _drop(self) -> None:
        self.done = True
        if self in _active:
            _active.remove(self)
        if self._conn is not None and self.owner is not None:
            try:
                self.owner.destroyed.disconnect(self._conn)
            except (RuntimeError, TypeError):
                pass
        self._conn = None
        if not _active and _timer is not None:
            _timer.stop()

    def _owner_gone(self, *_args) -> None:
        # The owner's C++ side is going: never call back into it.
        self.done = True
        if self in _active:
            _active.remove(self)
        if not _active and _timer is not None:
            _timer.stop()


def start(owner: QObject | None, duration_ms: int, step: Callable[[float], None],
          end: Callable[[], None]) -> Transition:
    """Run one transition. With motion off, `end` runs NOW and the returned transition is done."""
    global starts
    starts += 1
    tr = Transition(owner, duration_ms, step, end)
    if not enabled():
        tr.done = True
        end()
        return tr
    _active.append(tr)
    if owner is not None:
        tr._conn = tr._owner_gone
        owner.destroyed.connect(tr._conn)
    _ensure_timer().start()
    return tr


def _ensure_timer() -> QTimer:
    global _timer
    if _timer is None:
        _timer = QTimer()
        _timer.setInterval(FRAME_MS)
        _timer.timeout.connect(pump)
    return _timer


def pump() -> None:
    """Step every running transition at the current clock; finish the ones whose time is up."""
    global frames
    for tr in list(_active):
        if tr.done:
            continue
        frames += 1
        if _clock() - tr.t0 >= tr.duration:
            tr.finish()
        else:
            tr.step(tr.progress())


def finish_all() -> None:
    """Every running transition to its end state now — a capture or export calls this first."""
    for tr in list(_active):
        tr.finish()


def active_count() -> int:
    return len(_active)


def timer_active() -> bool:
    return _timer is not None and _timer.isActive()
