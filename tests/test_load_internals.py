"""Load-pipeline internals coverage (test/load-pipeline-coverage): the clock-provenance decision
and the no-average-across-a-gap smoothing contract.

These pure functions sit UNDER studio.load.load_recording and are golden-gated only through the
MANUAL D24 dump — the synthetic CI gate builds a bare Session with pre-seeded caches and never runs
the load pipeline — so an agent could resimplify them and ship with green CI. Pinning them here
makes such a regression fail loudly in the fast suite. Pure logic: no telemetry file, no Qt.
Run:  python tests/test_load_internals.py
"""
import os
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio._signal import (  # noqa: E402
    SMOOTH_GAP_S,
    _gap_segments,
    _smooth_segments,
    _smooth_timed,
)
from studio.load import _used_gps9_trueclock  # noqa: E402


def _samples(ts_ms):
    """GPS samples carrying only the timestamp_ms the clock-provenance decision reads (0 = the
    no-wall-clock sentinel — NOT the GPS5 era, whose fixes ARE stamped from GPSU; see
    tests/test_load_pipeline.py for that measurement)."""
    return [SimpleNamespace(timestamp_ms=t) for t in ts_ms]


# ---------------------------------------------- clock provenance (load._used_gps9_trueclock)
def test_used_gps9_trueclock_decision():
    """True IFF a contiguous GPS9 run exists — two consecutive non-sentinel fixes a sane single
    GPS9 step apart (0.02..0.40 s) — the SAME rule _gps9_times uses to re-anchor spacing. It flips
    data_quality.TimingQuality.clock, which drives the whole media-clock (packet-spread) degraded
    banner + the PB-celebration suppression, so it must not silently desync from the axis."""
    # clean 10 Hz GPS9 (dt = 0.1 s) -> a run -> True
    assert _used_gps9_trueclock(_samples([1000, 1100, 1200, 1300])) is True
    # a run that appears LATE among sentinels is still found
    assert _used_gps9_trueclock(_samples([0, 0, 1000, 1100, 0])) is True
    # an all-sentinel stream (no wall clock at all) -> no run -> False (stays on the media clock)
    assert _used_gps9_trueclock(_samples([0, 0, 0, 0])) is False
    # a LONE timed fix among sentinels is not a run (needs two consecutive) -> False
    assert _used_gps9_trueclock(_samples([0, 1100, 0, 0])) is False
    # every pair out of band (dt = 1.0 s > GPS9_MAX_DT_S) -> not a single GPS9 step -> False.
    # THIS PAIR OF CASES IS THE REAL GPS5-ERA ARM, not the all-sentinel one above: GPS5 carries one
    # GPSU stamp per ~1 s payload, so consecutive payloads sit ~1.0 s apart (here) and the fixes
    # inside one payload sit 0 s apart (the sub-minimum case below).
    assert _used_gps9_trueclock(_samples([1000, 2000, 3000])) is False
    # deltas below the minimum (10 ms duplicate/garbage fixes < GPS9_MIN_DT_S) -> False
    assert _used_gps9_trueclock(_samples([1000, 1010, 1020])) is False
    # trivially short input -> False (no pair to inspect)
    assert _used_gps9_trueclock(_samples([1000])) is False
    print("ok _used_gps9_trueclock: GPS9 run -> True; sentinel / lone / out-of-band / sub-min -> False")


# ------------------------------------- no-average-across-gap smoothing (studio._signal)
def test_gap_segments_splits_at_time_discontinuities():
    """_gap_segments returns contiguous runs [lo, hi) split wherever an inter-sample gap exceeds
    SMOOTH_GAP_S, so the load-time boxcar never bridges a chapter break / GPS dropout."""
    # one clean run
    assert _gap_segments([0.0, 0.1, 0.2, 0.3]) == [(0, 4)]
    # a >SMOOTH_GAP_S hole at index 2->3 splits into two runs
    big = SMOOTH_GAP_S + 4.0
    assert _gap_segments([0.0, 0.1, 0.2, 0.2 + big, 0.3 + big, 0.4 + big]) == [(0, 3), (3, 6)]
    # a sub-threshold hop does NOT split
    assert _gap_segments([0.0, 0.1, 0.1 + SMOOTH_GAP_S * 0.5, 0.3 + SMOOTH_GAP_S * 0.5]) == [(0, 4)]
    assert _gap_segments([]) == []
    print("ok _gap_segments: splits only at >SMOOTH_GAP_S discontinuities")


def test_smooth_segments_never_averages_across_a_gap():
    """The load-critical invariant: _smooth_segments smooths INSIDE each run only, so a position on
    one side of a chapter/dropout seam is never pulled toward the other side (bleeding two runs
    together would corrupt corner detection + distances that all follow from the smoothed track)."""
    big = SMOOTH_GAP_S + 4.0
    times = [0.0, 0.1, 0.2, 0.2 + big, 0.3 + big, 0.4 + big]
    segs = _gap_segments(times)                       # [(0, 3), (3, 6)]
    a = [0.0, 0.0, 0.0, 120.0, 120.0, 120.0]          # two constant runs across the seam
    out = _smooth_segments(a, segs, w=3)
    # If the boxcar bridged the seam, out[2] would rise toward 120 and out[3] fall toward 0.
    assert out[2] == 0.0, out[2]                       # last sample of run 0: untouched by run 1
    assert out[3] == 120.0, out[3]                     # first sample of run 1: untouched by run 0
    # a run shorter than the window passes through unchanged (no spurious smoothing)
    assert list(_smooth_segments([5.0, 7.0], [(0, 2)], w=3)) == [5.0, 7.0]
    print("ok _smooth_segments: no cross-gap averaging; sub-window run passthrough")


# ----------------------------------------- the time-aware smoother (studio._signal._smooth_timed)
def _frozen_boxcar_segments(a, seg_bounds, w):
    """A FROZEN COPY of the load-time smoother as it stood before `_smooth_timed` (v0.25.0:
    `_smooth_segments` over `_boxcar_core`), kept here so the bit-identity below is checked against
    the old operations themselves, not against whatever `_smooth_segments` becomes."""
    a = np.asarray(a, float)
    out = a.copy()
    for lo, hi in seg_bounds:
        seg = a[lo:hi]
        if hi - lo >= 2 and w >= 2 and len(seg) >= w:
            kernel = np.ones(w)
            out[lo:hi] = (np.convolve(seg, kernel, "same")
                          / np.convolve(np.ones(len(seg)), kernel, "same"))
    return out


def _gps9_like(n, period, start=100.0):
    """`n` fix times on a GPS9-like clock: whole milliseconds, `period` apart (18 Hz rounds to
    55/56 ms steps), as `load._gps9_times` builds them."""
    return start + np.round(1000.0 * period * np.arange(n)) / 1000.0 


def test_time_aware_smoother_is_the_boxcar_bit_for_bit_on_a_regular_trace():
    """THE HARD CONSTRAINT of the time-aware smoother: on a trace with no missing fix and no
    interior run edge, every output is the old boxcar's, to the byte — at 10 Hz and at 18 Hz (its
    55/56 ms steps are jitter, not holes), through a chapter-sized run break too (two runs whose
    edges are the trace's own ends and the seam: the seam's fixes take the new path, every other
    one the old). A real recording with no missing fix therefore loads exactly as before."""
    rng = np.random.default_rng(7)
    for period in (0.1, 1.0 / 18.0):
        t = _gps9_like(3000, period)
        for a in (47.0 + 1e-4 * np.cumsum(rng.normal(0, 1, len(t))), rng.normal(0, 5, len(t))):
            segs = _gap_segments(t)
            assert segs == [(0, len(t))]
            assert _smooth_timed(a, t, segs).tobytes() == _frozen_boxcar_segments(a, segs, 13).tobytes()
    t = np.concatenate([_gps9_like(400, 0.1), _gps9_like(400, 0.1, start=150.0)])
    a = rng.normal(0, 5, len(t))
    segs = _gap_segments(t)
    new, old = _smooth_timed(a, t, segs), _frozen_boxcar_segments(a, segs, 13)
    same = new == old
    assert same[:394].all() and same[406:].all() and not same[394:406].all(), np.flatnonzero(~same)
    print("ok _smooth_timed: the boxcar to the byte wherever the window is whole")


def test_time_aware_smoother_keeps_each_value_at_its_own_fix_time():
    """THE DEFECT the old boxcar had (`_signal.SMOOTH_WINDOW`'s block): it counts fixes, so with a
    fix missing in its window the averaged position belongs to the window's mean TIME, not its own
    fix's — and a lap is timed where that position crosses the line. On a kart moving at constant
    speed (position linear in time) every smoothed value must sit exactly on the line at its own
    time, beside one missing fix, a 0.5 s hole and a 1.6 s gap that splits the run alike. The old
    boxcar is off by up to 6/13 of a period beside the single fix, and by most of a window at the
    run edge."""
    t = _gps9_like(1000, 0.1)
    t = np.delete(t, [200] + list(range(500, 505)) + list(range(800, 816)))
    x = 3.0 + 27.0 * (t - t[0])                      # 27 m/s, metres
    segs = _gap_segments(t)
    assert len(segs) == 2, segs
    inner = slice(6, len(t) - 6)                     # the trace's own ends stay one-sided (⚠)
    new = _smooth_timed(x, t, segs)[inner] - x[inner]
    old = _frozen_boxcar_segments(x, segs, 13)[inner] - x[inner]
    assert np.abs(new).max() < 1e-9, np.abs(new).max()
    assert np.abs(old).max() > 27.0 * 0.04, np.abs(old).max()   # > 40 ms worth of travel
    print(f"ok _smooth_timed: at its own time beside holes (old boxcar off by "
          f"{np.abs(old).max() / 27.0 * 1e3:.0f} ms of travel)")


if __name__ == "__main__":
    test_used_gps9_trueclock_decision()
    test_gap_segments_splits_at_time_discontinuities()
    test_smooth_segments_never_averages_across_a_gap()
    test_time_aware_smoother_is_the_boxcar_bit_for_bit_on_a_regular_trace()
    test_time_aware_smoother_keeps_each_value_at_its_own_fix_time()
    print("\n5 load-internals tests passed")
