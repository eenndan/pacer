"""Sector-boundary integrity (QA MAP-08-ESC / MAP-11) — pure fakes, no Qt, no telemetry file.

The defect these pin. The sector collapse tolerance is a FRACTION of the lap odometer
(`Session._SECTOR_DEDUPE_FRAC` = 0.2 %, ~2.1 m on a 1068 m lap), but every lap projects the
sector lines onto its OWN samples. In a narrow band a second line therefore lands on the same
sample as its neighbour on SOME laps and one sample away on others. While the collapse decision
was made per lap, those laps returned a DIFFERENT number of boundaries — so an S column stopped
meaning the same stretch of track from row to row, `session_best_splits()` took a per-column min
across incomparable pieces (on the 21-lap D24 fixture: a 0.199 s sliver as the S2 session best)
and `theoretical_best()` summed them.

`make_partial_session` distils that to two laps: the SAME two lines are 0 m apart on the coarse
lap's odometer and 2 m apart on the fine lap's. Every test below asserts on the SESSION-wide
consequence, not on an internal, so they stay valid if the collapse rule is re-tuned:
  * every lap returns the same boundary count at every offset across the band (the mixed state
    is unreachable) — including the exact offsets where it used to be mixed;
  * a collapsed line is reported (`collapsed_sector_lines`) and excluded from `sector_count`'s
    effective companion, so the split-column count is one number for the whole session;
  * the session bests carry no sliver and the theoretical best never exceeds the fastest real
    lap (the tell-tale of pieces that do not tile a lap);
  * WELL-SEPARATED lines are byte-identically unaffected (the no-regression control);
  * `suggest_sectors(n)` subdivides the lap evenly (MAP-11) while `suggest_sector(existing)`
    keeps its append fractions bit-for-bit.
"""
import os
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _synthetic import bare_session, seed_cols, seed_lap  # noqa: E402

from studio.session import Session  # noqa: E402

LAP_A, LAP_B = 0, 1
TOTAL_M = 500.0            # both laps cover the same 500 m -> collapse tolerance 1.000 m
TIME_A, TIME_B = 60.0, 62.0
FIRST_LINE_X = 250.0


def _seg(x1, y1, x2, y2):
    """A SimpleNamespace timing line — the sector code reads only .first/.second.x/.y."""
    return SimpleNamespace(first=SimpleNamespace(x=x1, y=y1),
                           second=SimpleNamespace(x=x2, y=y2))


def _lap_arrays(n, t0, laptime):
    """A straight-line lap of `n` evenly spaced samples over TOTAL_M metres (so the sample
    PITCH is TOTAL_M/(n-1) — the knob that decides whether two nearby lines share a sample),
    with a slow-fast-slow clock so distance<->time is a real non-linear interpolation."""
    dists = np.linspace(0.0, TOTAL_M, n)
    speed = 1.0 + np.sin(np.linspace(0.0, np.pi, n)) ** 2
    cum = np.cumsum(speed)
    times = t0 + (cum - cum[0]) / (cum[-1] - cum[0]) * laptime
    return times, dists


def make_partial_session(second_line_x):
    """Two valid laps + two sector lines, at x=250.0 and x=`second_line_x`.

    Lap A samples every 10 m and lap B every 1 m, so the same pair of lines projects 0 m apart
    on A's odometer and up to (second_line_x - 250) m apart on B's — the per-lap disagreement
    that used to produce ragged S columns."""
    ta, da = _lap_arrays(51, 0.0, TIME_A)         # coarse: 10 m sample pitch
    tb, db = _lap_arrays(501, 100.0, TIME_B)      # fine:    1 m sample pitch
    s = bare_session({LAP_A: (ta, da), LAP_B: (tb, db)}, best=LAP_A, valid=[LAP_A, LAP_B])
    seed_lap(s, LAP_A, ta, da)
    seed_lap(s, LAP_B, tb, db)
    seed_cols(s, LAP_A, ta, da)
    seed_cols(s, LAP_B, tb, db)
    lines = [_seg(FIRST_LINE_X, -5.0, FIRST_LINE_X, 5.0),
             _seg(second_line_x, -5.0, second_line_x, 5.0)]
    s.laps = SimpleNamespace(
        lap_time=lambda lid: TIME_A if lid == LAP_A else TIME_B,
        sector_count=lambda: len(lines),
        sectors=SimpleNamespace(sector_lines=lines),
    )
    s.lap_has_dropout = lambda lid: False
    return s


# Offsets across and past the tolerance. Up to 1.5 m the pair fuses on BOTH laps; from 2.0 m to
# just under 5.0 m it fused on the coarse lap only — the MIXED state (measured on the pre-fix
# tree: lap A 1 boundary, lap B 2); at 6 m and beyond neither lap fuses it.
SWEEP_M = (0.0, 0.5, 1.0, 1.5, 2.0, 2.4, 3.0, 4.0, 6.0, 10.0)
MIXED_BEFORE_M = (2.0, 2.4, 3.0, 4.0)


def test_no_offset_leaves_the_laps_disagreeing_on_boundary_count():
    """The headline: sweeping the second line across (and well past) the collapse tolerance,
    EVERY lap returns the same number of boundaries at EVERY offset. The partial band — where
    one lap fused the pair and the other did not — is unreachable, so an S column is the same
    stretch of track on every row of the table."""
    for gap in SWEEP_M:
        s = make_partial_session(FIRST_LINE_X + gap)
        counts = {len(s.sector_boundary_distances(lid)) for lid in (LAP_A, LAP_B)}
        assert len(counts) == 1, (gap, counts)
        # …and the split lists that follow from them are the same width, which is what makes
        # the per-column session bests comparable.
        widths = {len(s.lap_sector_splits(lid)) for lid in (LAP_A, LAP_B)}
        assert widths == {counts.pop() + 1}, (gap, widths)
    print("test_no_offset_leaves_the_laps_disagreeing_on_boundary_count OK")


def test_collapsed_line_is_reported_and_gives_one_effective_count():
    """At an offset that collapses on only ONE lap, the session still reports exactly one
    answer: the later line is flagged collapsed, `sector_count()` keeps counting the lines the
    user PLACED, and `effective_sector_count()` is the number that actually divides the lap."""
    for gap in MIXED_BEFORE_M:
        s = make_partial_session(FIRST_LINE_X + gap)
        assert s.collapsed_sector_lines() == [1], (gap, s.collapsed_sector_lines())
        assert s.sector_count() == 2, gap                 # both lines are still placed…
        assert s.effective_sector_count() == 1, gap       # …but only one divides the lap
    # A healthy pair collapses nothing and the two counts agree.
    ok = make_partial_session(FIRST_LINE_X + 10.0)
    assert ok.collapsed_sector_lines() == []
    assert ok.effective_sector_count() == ok.sector_count() == 2
    print("test_collapsed_line_is_reported_and_gives_one_effective_count OK")


def test_session_bests_carry_no_sliver_and_theoretical_stays_achievable():
    """`session_best_splits()` must not take a sub-second sliver from a mis-projected lap as a
    sector best, and `theoretical_best()` — the SUM of those cells — must stay <= the fastest
    real lap. Summing incomparable pieces broke both (a 0.331 s S2 best and a 61.359 s
    'theoretical' best on this fixture, above the 60.000 s fastest lap)."""
    fastest = min(TIME_A, TIME_B)
    for gap in SWEEP_M:
        s = make_partial_session(FIRST_LINE_X + gap)
        bests = s.session_best_splits()
        assert bests and all(b is not None for b in bests), (gap, bests)
        # Every column is filled by every lap, so the bests span the whole lap exactly once.
        assert len(bests) == s.effective_sector_count() + 1, (gap, bests)
        theo = s.theoretical_best()
        assert theo is not None, gap
        assert abs(theo - float(sum(bests))) < 1e-9, (gap, theo, bests)
        assert theo <= fastest + 1e-9, (gap, theo, fastest)
        # At the offsets that used to go mixed the collapsed line contributes no column at all,
        # so no cell can be the fragment between two lines one lap fused (0.331 s on main).
        if gap in MIXED_BEFORE_M:
            assert min(bests) > 10.0, (gap, bests)
    print("test_session_bests_carry_no_sliver_and_theoretical_stays_achievable OK")


def test_well_separated_lines_are_untouched():
    """The no-regression control: with the lines 10 m apart nothing collapses, so both laps
    keep BOTH boundaries at exactly their projected odometers, all splits stay positive and
    sum to the lap time, and the bests are the plain per-column minima."""
    s = make_partial_session(FIRST_LINE_X + 10.0)
    for lid, laptime in ((LAP_A, TIME_A), (LAP_B, TIME_B)):
        bounds = s.sector_boundary_distances(lid)
        assert len(bounds) == 2, (lid, bounds)
        assert bounds[0] < bounds[1], (lid, bounds)
        # The boundaries are the lines' own odometers (nearest-sample snapped), not re-derived.
        assert abs(bounds[0] - FIRST_LINE_X) <= TOTAL_M / 50, (lid, bounds)
        splits = s.lap_sector_splits(lid)
        assert len(splits) == 3 and all(sp > 0 for sp in splits), (lid, splits)
        assert abs(sum(splits) - laptime) < 1e-9, (lid, sum(splits), laptime)
    per_lap = [s.lap_sector_splits(lid) for lid in (LAP_A, LAP_B)]
    assert s.session_best_splits() == [min(col) for col in zip(*per_lap, strict=True)]
    print("test_well_separated_lines_are_untouched OK")


def test_boundaries_survive_a_lap_that_cannot_be_projected():
    """A lap too short to project onto returns no boundaries (and no splits) instead of
    raising — the same tolerance the table already has for a partial lap's blank cells."""
    s = make_partial_session(FIRST_LINE_X + 10.0)
    one = np.array([0.0])
    # A ONE-sample lap: seeded straight into the columns cache (seed_cols needs >= 2 to take a
    # speed gradient), which is the shape a re-segmentation sliver leaves behind.
    s._cols_cache[9] = (one, one.copy(), np.zeros_like(one), one.copy(), one.copy())
    assert s.sector_boundary_distances(9) == []
    assert s.lap_sector_splits(9) == []
    print("test_boundaries_survive_a_lap_that_cannot_be_projected OK")


def _straight_trace_session(total_m=1000.0, n=1001):
    """A bare Session carrying only what the sector SUGGESTION geometry reads: a straight
    `total_m` trace and no best lap (so it takes the documented full-trace fallback)."""
    s = Session.__new__(Session)
    s.tx = np.linspace(0.0, total_m, n)
    s.ty = np.zeros(n)
    s._best_cache = None
    return s


def _fractions(segs, total_m=1000.0):
    """The lap fractions a list of suggested lines sits at. On a straight x-axis trace the
    perpendicular line is vertical, so both endpoints share the line's x."""
    return sorted(float(seg.x1) / total_m for seg in segs)


def test_suggest_sectors_evenly_subdivides_the_lap():
    """MAP-11: `suggest_sectors(n)` places the k-th line at k/(n+1), so n=3 gives four equal
    quarters — not the 50/17/8/25 % the one-at-a-time append produces."""
    s = _straight_trace_session()
    fr = _fractions(s.suggest_sectors(3))
    assert len(fr) == 3, fr
    edges = [0.0, *fr, 1.0]
    sub = [b - a for a, b in zip(edges, edges[1:], strict=False)]
    assert all(abs(x - 0.25) <= 0.005 for x in sub), sub
    # …and the general shape: n lines -> n+1 equal sub-sectors, for a few n.
    for n in (1, 2, 4, 5):
        edges = [0.0, *_fractions(s.suggest_sectors(n)), 1.0]
        sub = [b - a for a, b in zip(edges, edges[1:], strict=False)]
        assert all(abs(x - 1.0 / (n + 1)) <= 0.005 for x in sub), (n, sub)
    assert s.suggest_sectors(0) == [] and s.suggest_sectors(-1) == []
    print("test_suggest_sectors_evenly_subdivides_the_lap OK")


def test_suggest_sector_append_is_unchanged():
    """`suggest_sector(existing)` still returns the (existing+1)/(existing+2) line, IDENTICAL
    to `suggest_sectors(existing + 1)[-1]` — the appending caller's geometry did not move."""
    s = _straight_trace_session()
    for existing in range(4):
        one = s.suggest_sector(existing)
        assert abs(one.x1 / 1000.0 - (existing + 1) / (existing + 2)) <= 0.005, existing
        whole_last = s.suggest_sectors(existing + 1)[-1]
        assert (one.x1, one.y1, one.x2, one.y2) == \
               (whole_last.x1, whole_last.y1, whole_last.x2, whole_last.y2), existing
    print("test_suggest_sector_append_is_unchanged OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} SECTOR-INTEGRITY TESTS PASSED")
