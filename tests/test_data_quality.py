"""Unit tests for studio.data_quality.TimingQuality — the timing-ACCURACY axis (PR: data-quality
signal). Pure value object, no Qt / no pacer: classify a recording's per-sample clock provenance
(GPS9 true clock vs media-clock fallback) + the quality gate's dropped-fix fraction into the
UI-facing `degraded` verdict + the human-readable banner concern lines. Orthogonal to the
start-line TRUST surface (Session.timing_verified) — see studio/data_quality.py.

Run:  python tests/test_data_quality.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio import data_quality as dq  # noqa: E402
from studio.data_quality import TimingQuality  # noqa: E402


def test_default_is_high_quality():
    """The default verdict (a from-scratch Session / no paths / empty trace) is GPS9 true-clock,
    no dropped fixes — NOT degraded, so the UI is visually identical to today."""
    q = TimingQuality()
    assert q.clock == dq.GPS9_TRUECLOCK
    assert q.dropped_fraction == 0.0
    assert not q.media_clock
    assert not q.low_gps_quality
    assert not q.degraded
    assert q.concerns() == []
    print("test_default_is_high_quality OK")


def test_media_clock_fallback_is_degraded():
    """An older GPS5 camera (no GPS9) fell back to the ~0.1%-fast media clock → media_clock True,
    degraded, and a banner concern line that names the cause + the ~0.1% drift."""
    q = TimingQuality(clock=dq.MEDIA_CLOCK_FALLBACK)
    assert q.media_clock and q.degraded
    assert not q.low_gps_quality
    concerns = q.concerns()
    assert len(concerns) == 1
    assert "video clock" in concerns[0] and "0.1%" in concerns[0]
    print("test_media_clock_fallback_is_degraded OK")


def test_low_gps_quality_threshold():
    """The dropped-fix concern fires at/above DROPPED_FIX_CONCERN_FRAC and not below — a few
    rejected fixes on an otherwise clean GPS9 trace is normal and must NOT raise a banner."""
    below = TimingQuality(dropped_fraction=dq.DROPPED_FIX_CONCERN_FRAC - 0.001)
    assert not below.low_gps_quality and not below.degraded and below.concerns() == []
    at = TimingQuality(dropped_fraction=dq.DROPPED_FIX_CONCERN_FRAC)
    assert at.low_gps_quality and at.degraded
    assert "%" in at.concerns()[0] and "rejected" in at.concerns()[0]
    print("test_low_gps_quality_threshold OK")


def test_both_concerns_stack_media_clock_first():
    """When BOTH concerns apply the banner stacks two lines, most-significant first (the
    media-clock/headline-accuracy loss before the fix-rejection note)."""
    q = TimingQuality(clock=dq.MEDIA_CLOCK_FALLBACK, dropped_fraction=0.5)
    assert q.degraded
    concerns = q.concerns()
    assert len(concerns) == 2
    assert "video clock" in concerns[0]            # media-clock line first
    assert "50%" in concerns[1] and "rejected" in concerns[1]
    print("test_both_concerns_stack_media_clock_first OK")


def test_frozen_value_object():
    """It's an immutable verdict — safe to share one default instance as a class attribute."""
    q = TimingQuality()
    try:
        q.clock = dq.MEDIA_CLOCK_FALLBACK  # type: ignore[misc]
    except Exception:
        print("test_frozen_value_object OK")
        return
    raise AssertionError("TimingQuality must be frozen/immutable")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} DATA-QUALITY TESTS PASSED")
