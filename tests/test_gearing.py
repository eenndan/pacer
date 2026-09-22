"""Gearing arithmetic (M3): engine RPM from road speed for a single-speed kart, and nothing for a
shifter.

Every expected number below is worked by hand in its comment, not computed by the code under test,
so a formula that inverts the ratio, forgets the 3.6 or the 60, or divides by a diameter instead of
a circumference fails a literal.

THE CLASS GATE IS STRUCTURAL (`studio/gearing.py`): a shifter, an unstated class or a typo gets NO
drivetrain object, so there is nothing to ask for an RPM. The tests assert the None, not a caveat.

Negative controls, watched on this tree:
  * `SingleSpeed.ratio` returning front/rear instead of rear/front fails
    `test_hand_worked_rpm` (8000 -> 125.0).
  * `single_speed` with its `kart_class != SINGLE_SPEED` check deleted fails
    `test_a_shifter_or_an_unstated_class_gets_no_number` (a SingleSpeed came back for 'shifter').

Pure: no Qt, no pacer core, no files. Run: python tests/test_gearing.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio import gearing  # noqa: E402


def _close(a, b, tol=1e-9):
    return a is not None and abs(a - b) <= tol * max(1.0, abs(b))


def test_hand_worked_rpm():
    """Three cases worked on paper."""
    # 60 km/h = 16.6667 m/s; a 1.000 m tyre turns 16.6667 rev/s; 80/10 = 8 engine turns per axle
    # turn -> 133.333 rev/s -> x60 = 8000 rpm exactly.
    g = gearing.single_speed(gearing.SINGLE_SPEED, 10, 80, 1.0)
    assert _close(g.rpm_at(60.0), 8000.0), g.rpm_at(60.0)
    # 86.4 km/h = 24 m/s (the owner's Sandown top speed is 86-87 km/h); 24 / 0.88 = 27.272727
    # rev/s at the axle; 76/11 = 6.9090909 -> 188.42975 rev/s -> x60 = 11305.785 rpm.
    g = gearing.single_speed(gearing.SINGLE_SPEED, 11, 76, 0.88)
    assert _close(g.rpm_at(86.4), 11305.785124, tol=1e-9), g.rpm_at(86.4)
    # 36 km/h = 10 m/s; 10 / 0.9 = 11.1111 rev/s; 90/12 = 7.5 -> 83.3333 rev/s -> 5000 rpm.
    g = gearing.single_speed(gearing.SINGLE_SPEED, 12, 90, 0.9)
    assert _close(g.rpm_at(36.0), 5000.0), g.rpm_at(36.0)
    # Standing still is 0 rpm through a locked drivetrain (idle is the clutch's business).
    assert g.rpm_at(0.0) == 0.0
    print("test_hand_worked_rpm OK")


def test_the_arithmetic_scales_the_way_the_drivetrain_does():
    """One more tooth on the engine sprocket lowers the RPM by the ratio of the teeth; a tyre twice
    as far round halves it; twice the speed doubles it."""
    base = gearing.single_speed(gearing.SINGLE_SPEED, 10, 80, 0.88).rpm_at(80.0)
    assert _close(gearing.single_speed(gearing.SINGLE_SPEED, 11, 80, 0.88).rpm_at(80.0),
                  base * 10 / 11)
    assert _close(gearing.single_speed(gearing.SINGLE_SPEED, 10, 80, 1.76).rpm_at(80.0), base / 2)
    assert _close(gearing.single_speed(gearing.SINGLE_SPEED, 10, 80, 0.88).rpm_at(160.0), base * 2)
    assert _close(gearing.single_speed(gearing.SINGLE_SPEED, 10, 80, 0.88).ratio, 8.0)
    print("test_the_arithmetic_scales_the_way_the_drivetrain_does OK")


def test_a_shifter_or_an_unstated_class_gets_no_number():
    """KZ / shifter / anything not exactly `single-speed` yields no drivetrain, so no RPM. A
    wrong number is the failure this gate exists to prevent: in six gears a speed names no
    ratio."""
    assert gearing.KART_CLASSES == ("single-speed", "shifter")
    for klass in (gearing.SHIFTER, "", None, "KZ", "kz2", "Single-speed", "single speed", 1):
        assert gearing.single_speed(klass, 11, 76, 0.88) is None, klass
    # ...and there is no function taking a class and a speed at all: the number lives only on the
    # object the gate hands out.
    own = sorted(n for n, v in vars(gearing).items()
                 if not n.startswith("_") and callable(v)
                 and getattr(v, "__module__", None) == gearing.__name__)
    assert own == ["SingleSpeed", "single_speed"], own
    print("test_a_shifter_or_an_unstated_class_gets_no_number OK")


def test_inputs_that_are_not_a_gearing_get_no_number():
    """A missing, zero, negative, fractional or boolean tooth count, and a circumference in the
    wrong unit (mm, inches, a diameter in cm), give no drivetrain; a speed that is not a finite
    non-negative number gives no RPM."""
    ok = gearing.SINGLE_SPEED
    for front, rear in ((None, 76), (11, None), (0, 76), (11, 0), (-11, 76), (11.0, 76),
                        (True, 76), (11, False)):
        assert gearing.single_speed(ok, front, rear, 0.88) is None, (front, rear)
    for circ in (None, 880, 34.6, 28.0, 0.28, 0.0, -0.88, math.nan, math.inf, True, "0.88"):
        assert gearing.single_speed(ok, 11, 76, circ) is None, circ
    # The bounds themselves are accepted.
    assert gearing.single_speed(ok, 11, 76, gearing.CIRCUMFERENCE_MIN_M) is not None
    assert gearing.single_speed(ok, 11, 76, gearing.CIRCUMFERENCE_MAX_M) is not None
    g = gearing.single_speed(ok, 11, 76, 0.88)
    for v in (-1.0, math.nan, math.inf, None, "86", True):
        assert g.rpm_at(v) is None, v
    print("test_inputs_that_are_not_a_gearing_get_no_number OK")


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()


if __name__ == "__main__":
    _run_all()
    print("\nall gearing tests passed")
