"""Gearing arithmetic: engine RPM from road speed, for a kart with ONE ratio (backlog M3).

A single-speed kart (a centrifugal-clutch two-stroke of the TaG classes, a direct-drive 100 cc, or a
four-stroke) has one fixed ratio between its rear axle and its crank once the clutch is locked. The
rear tyre turns once per circumference travelled, the axle sprocket with it, and the chain turns the
engine sprocket rear/front times as fast::

    RPM = speed [m/s] / tyre circumference [m] × rear teeth / front teeth × 60

WHY THE CLASS GATE IS STRUCTURAL. A shifter (KZ, KZ2, ICC: six gears) has six ratios, and a speed
alone cannot say which gear the kart was in, so any number this formula gave for one would be wrong
by up to the gearbox's spread with nothing on screen to say so. A shifter must therefore yield NO
number, not a caveated one. `single_speed` is the only door: it returns None for every class but
`SINGLE_SPEED`, including an unstated one, and the `rpm_at` that makes a number exists only on the
object it returns. There is no function here that takes a class and a speed and returns an RPM.

WHAT THIS IS NOT. A slipping clutch is not a locked ratio: below lock-up the engine turns faster
than this says. `studio/dev/probes/p14_rpm_audio.py` measured that on all four of the owner's present
recordings (the engine's tone sits 3.5-12 % above the locked constant below 60 % of top speed), so
this is a number for the END of a straight (trap speed), which is where the backlog asked for it.
Tyre growth at speed and rear-tyre slip move it by a few per cent as well.

NOT WIRED TO ANY SURFACE, DELIBERATELY. The inputs live in the session record, and on 2026-09-22 the
owner has no `session_records.json` at all, so a gearing RPM would show on 0 of his 4 present
recordings. The record fields and the readout were refused on that measurement
(`studio/docs/refused-2026-09.md`, "Engine RPM"); what is kept is the arithmetic and its class
gate, which the p14 probe uses to read the audio's measured tone-per-speed constant as gearing.

Pure: no Qt, no pacer core, no I/O.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# The class vocabulary, closed like `session_record.CONDITIONS`. Only one member yields a number.
SINGLE_SPEED = "single-speed"
SHIFTER = "shifter"
KART_CLASSES = (SINGLE_SPEED, SHIFTER)

# Input bounds. A kart's rear tyre is ~0.85-0.95 m round; anything outside 0.5-2 m is a unit
# mistake (millimetres, inches, a diameter) and gets no number rather than one off by 25x or pi.
CIRCUMFERENCE_MIN_M = 0.5
CIRCUMFERENCE_MAX_M = 2.0


def _teeth(v) -> int | None:
    """A sprocket's tooth count: a positive int, never a bool (an int subclass) or a float."""
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None


@dataclass(frozen=True)
class SingleSpeed:
    """One locked drivetrain ratio. Built through `single_speed`, which checks the class."""

    front: int                # engine (clutch) sprocket teeth
    rear: int                 # axle sprocket teeth
    circumference_m: float    # rear tyre rolling circumference

    @property
    def ratio(self) -> float:
        """Engine turns per axle turn."""
        return self.rear / self.front

    def rpm_at(self, speed_kmh: float) -> float | None:
        """Engine RPM at road speed `speed_kmh`, clutch locked. None for a speed that is not a
        finite, non-negative number."""
        if isinstance(speed_kmh, bool) or not isinstance(speed_kmh, (int, float)):
            return None
        if not math.isfinite(speed_kmh) or speed_kmh < 0:
            return None
        return speed_kmh / 3.6 / self.circumference_m * self.ratio * 60.0


def single_speed(kart_class: str, front, rear, circumference_m) -> SingleSpeed | None:
    """The locked drivetrain for a SINGLE-SPEED kart, or None.

    None for any class but `SINGLE_SPEED` — a shifter, an unstated class, a typo — and for any
    input that is not a gearing: a missing or non-positive tooth count, or a circumference outside
    `CIRCUMFERENCE_MIN_M`-`CIRCUMFERENCE_MAX_M`. A missing number is the honest answer in every one
    of those cases; a wrong one is not."""
    if kart_class != SINGLE_SPEED:
        return None
    f, r = _teeth(front), _teeth(rear)
    if f is None or r is None:
        return None
    if isinstance(circumference_m, bool) or not isinstance(circumference_m, (int, float)):
        return None
    c = float(circumference_m)
    if not (math.isfinite(c) and CIRCUMFERENCE_MIN_M <= c <= CIRCUMFERENCE_MAX_M):
        return None
    return SingleSpeed(front=f, rear=r, circumference_m=c)
