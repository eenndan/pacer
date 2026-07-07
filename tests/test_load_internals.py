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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio._signal import SMOOTH_GAP_S, _gap_segments, _smooth_segments  # noqa: E402
from studio.load import _used_gps9_trueclock  # noqa: E402


def _samples(ts_ms):
    """GPS samples carrying only the timestamp_ms the clock-provenance decision reads (0 = the
    GPS5-era sentinel)."""
    return [SimpleNamespace(timestamp_ms=t) for t in ts_ms]


# ---------------------------------------------- clock provenance (load._used_gps9_trueclock)
def test_used_gps9_trueclock_decision():
    """True IFF a contiguous GPS9 run exists — two consecutive non-sentinel fixes a sane single
    GPS9 step apart (0.02..0.40 s) — the SAME rule _gps9_times uses to re-anchor spacing. It flips
    data_quality.TimingQuality.clock, which drives the whole 'video clock runs ~0.1% fast' degraded
    banner + the PB-celebration suppression, so it must not silently desync from the axis."""
    # clean 10 Hz GPS9 (dt = 0.1 s) -> a run -> True
    assert _used_gps9_trueclock(_samples([1000, 1100, 1200, 1300])) is True
    # a run that appears LATE among sentinels is still found
    assert _used_gps9_trueclock(_samples([0, 0, 1000, 1100, 0])) is True
    # all-sentinel GPS5 stream -> no run -> False (stays on the media clock)
    assert _used_gps9_trueclock(_samples([0, 0, 0, 0])) is False
    # a LONE timed fix among sentinels is not a run (needs two consecutive) -> False
    assert _used_gps9_trueclock(_samples([0, 1100, 0, 0])) is False
    # every pair out of band (dt = 1.0 s > GPS9_MAX_DT_S) -> not a single GPS9 step -> False
    assert _used_gps9_trueclock(_samples([1000, 2000, 3000])) is False
    # deltas below the minimum (10 ms duplicate/garbage fixes < GPS9_MIN_DT_S) -> False
    assert _used_gps9_trueclock(_samples([1000, 1010, 1020])) is False
    # trivially short input -> False (no pair to inspect)
    assert _used_gps9_trueclock(_samples([1000])) is False
    print("ok _used_gps9_trueclock: GPS9 run -> True; GPS5 / lone / out-of-band / sub-min -> False")


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


if __name__ == "__main__":
    test_used_gps9_trueclock_decision()
    test_gap_segments_splits_at_time_discontinuities()
    test_smooth_segments_never_averages_across_a_gap()
    print("\n3 load-internals tests passed")
