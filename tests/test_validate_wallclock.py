"""Pure-Python tests for studio.dev._validate_wallclock — the reusable wall-clock auto-discovery
helpers that reconstruct which transponder-CSV laps a recording covers (so a GPS9-timing
validation can be re-run for any recording without hand-entering a lap range).

The four signals are exercised on a SYNTHETIC continuous lap log (no telemetry file needed):
the cumulative-completion clock, the elapsed-time -> lap lookup, the pit-bracket detector, and
the duration-correlation LOCK (the authoritative per-lap-shape alignment). The lock is the
fragile bit — it must peak at exactly the right integer offset and be ~0 elsewhere, the same
property the real 0060/0062 alignments relied on.

Run: python tests/test_validate_wallclock.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from studio.dev import _validate_wallclock as vw  # noqa: E402


def _make_log(seed=3):
    """A synthetic continuous lap log: 1000 racing laps ~69 s with a long pit lap every ~70 laps,
    so it has the same structure as the real 24 h CSV (racing stints bracketed by pit laps)."""
    rng = np.random.default_rng(seed)
    laps = {}
    for i in range(1, 1001):
        if i % 70 == 0:  # a pit / driver-change lap
            laps[i] = float(rng.uniform(200.0, 260.0))
        else:
            laps[i] = float(69.0 + rng.normal(0.0, 0.6))
    return laps


def test_cumulative_completion_and_lap_being_driven():
    laps = {1: 70.0, 2: 71.0, 3: 200.0, 4: 69.0}
    comp = vw.cumulative_completion(laps)
    assert abs(comp[1] - 70.0) < 1e-9
    assert abs(comp[2] - 141.0) < 1e-9
    assert abs(comp[3] - 341.0) < 1e-9     # the long pit lap is REAL elapsed time
    assert abs(comp[4] - 410.0) < 1e-9
    # The lap in progress at an elapsed time is the first whose COMPLETION is at/after it.
    assert vw.lap_being_driven(comp, 0.0) == 1
    assert vw.lap_being_driven(comp, 70.5) == 2
    assert vw.lap_being_driven(comp, 200.0) == 3   # mid pit lap
    assert vw.lap_being_driven(comp, 409.0) == 4
    print("test_cumulative_completion_and_lap_being_driven OK")


def test_pit_brackets():
    laps = _make_log()
    # Lap 105 is mid-stint (pit laps at 70 and 140). Brackets should be those two.
    before, after = vw.pit_brackets(laps, 105)
    assert before == 70, before
    assert after == 140, after
    print("test_pit_brackets OK")


def test_best_offset_locks_unique_alignment():
    """The duration-correlation must peak sharply at the true offset and be ~0 elsewhere — the
    property that pins the CSV lap range. Take a contiguous racing window out of the log, add a
    little GPS-noise (so it's not a trivial identity match), and confirm the lock recovers the
    exact start offset with high corr and a clear margin over every neighbour."""
    laps = _make_log()
    true_start = 211  # a racing window between pit laps at 210 and 280
    n = 60
    rng = np.random.default_rng(11)
    app = np.array([laps[true_start + k] + rng.normal(0.0, 0.12) for k in range(n)])

    start, corr, offsets = vw.best_offset(app, laps, true_start - 8, true_start + 8)
    assert start == true_start, (start, true_start)
    assert corr > 0.9, corr
    # Uniqueness: every OTHER offset's corr is far below the locked one.
    others = [c for s, c, _ in offsets if s != true_start]
    assert max(others) < corr - 0.4, (corr, max(others))
    print("test_best_offset_locks_unique_alignment OK")


def test_residual_stats():
    r = np.array([0.1, -0.1, 0.2, -0.2, 0.0])
    s = vw.residual_stats(r)
    assert s["n"] == 5
    assert abs(s["mean"]) < 1e-9
    assert abs(s["median"]) < 1e-9
    assert abs(s["rms"] - float(np.sqrt(np.mean(r ** 2)))) < 1e-12
    print("test_residual_stats OK")


def test_parse_when_handles_z_and_naive():
    a = vw._parse_when("2026-05-23 12:00:00Z")
    assert a.utcoffset().total_seconds() == 0
    assert a.hour == 12
    b = vw._parse_when("2026-05-24 06:54")  # naive -> treated UTC
    assert b.hour == 6 and b.minute == 54
    print("test_parse_when_handles_z_and_naive OK")


if __name__ == "__main__":
    test_cumulative_completion_and_lap_being_driven()
    test_pit_brackets()
    test_best_offset_locks_unique_alignment()
    test_residual_stats()
    test_parse_when_handles_z_and_naive()
    print("\nALL WALLCLOCK-VALIDATION TESTS PASSED")
