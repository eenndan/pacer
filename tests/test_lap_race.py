"""The Lap Race core (`studio/lap_race.py` + `Session.race_tracks`) against KNOWN truth.

A NEW SURFACE, so a new file: no test file pinned a race, a ladder or an interval-to-leader before
this. Every session here is `tests/_synthetic.py`'s real bare Session over laps driven round the
stadium at known speeds, whose `dense` clocks are the truth; nothing is mocked between the session's
warp and the ladder. What is pinned:
  * the interval to the leader IS the compare badge's Δ (`Session.delta_between`) at every tick;
  * it tracks the true interval (each lap's own clock read at the leader's physical station);
  * at the finish the ladder is lap-time order — with its negative control: two lap times swapped
    and the same check fails;
  * fewer than two laps → None; the tie rule on both sides of both thresholds;
  * a lap with a GPS dropout, or with no on-track match, is left out by name — never a dot at a
    guessed place;
  * a tick is cheap and rebuilds nothing (a build counter), and the 36-lap precompute is timed.

Run:  python tests/test_lap_race.py
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _synthetic as syn  # noqa: E402

from studio import lap_race, library  # noqa: E402


def _spec(k: int, wide_m: float = 0.0) -> dict:
    """Lap `k` of a field: the stadium's reference line, a little slower per k (corner speed and
    top speed both step), its own noise seed; `wide_m` runs it that far outside the line."""
    vc = 12.5 - 0.03 * k
    vt = 22.0 - 0.02 * k
    return dict(offset=wide_m, vc=vc, straights=((vt, 2.0), (vt, 2.0)), run_wide_m=0.0,
                seed=100 + k)


def _race_session(n: int = 6, specs=None):
    """(session, laps): a real bare Session over `n` contiguous stadium laps, lap 0 the fastest."""
    laps = syn.drift_noise_laps(specs=specs or [_spec(k) for k in range(n)])
    return syn._drift_session(laps), laps


def _true_interval(laps, k: int, lead: int, t: float) -> float:
    """TRUTH: the leader's own clock at the physical station lap `k` is at, `t` s into its lap."""
    u_k, _odo, clock_k = laps[k]["dense"]
    u_l, _odo_l, clock_l = laps[lead]["dense"]
    station = float(np.interp(min(t, clock_k[-1]), clock_k, u_k))
    return min(t, float(clock_k[-1])) - float(np.interp(station, u_l, clock_l))


def test_interval_is_the_compare_badge_delta():
    """Every rung's interval equals `delta_between(lap, leader, lap_start + t)` — the number the
    compare badge prints for that pair — at 400 ticks from the start to past the last finish, on
    the stadium field and on the circuit with a lap 1 m wide through one corner (where the warp
    and an equal-fraction place disagree)."""
    worst, checks = 0.0, 0
    for s, _laps in (_race_session(), syn.line_change_session(1.0, 3, "circuit")):
        rt = s.race_tracks()
        assert rt is not None and len(rt.ids) == len(s.valid_lap_ids()) and not rt.excluded
        for t in np.linspace(0.0, rt.duration + 1.0, 400):
            st = lap_race.race_state(rt, float(t))
            lead = st.rungs[0].lap_id
            for r in st.rungs[1:]:
                start = float(rt.clock_starts[rt.index(r.lap_id)])
                db = s.delta_between(r.lap_id, lead, start + float(t))
                assert db is not None
                worst = max(worst, abs(db - r.interval_s))
                checks += 1
    assert worst < 1e-6, f"interval disagrees with delta_between by {worst * 1e3:.4f} ms"
    print(f"test_interval_is_the_compare_badge_delta OK ({checks} checks, max |Δ| {worst:.1e} s)")


def _truth_errors(s, laps) -> np.ndarray:
    """interval − true interval for every non-leader rung, every 0.25 s while all laps run."""
    rt = s.race_tracks()
    errs = []
    for t in np.arange(0.5, float(rt.lap_times.min()) - 0.5, 0.25):
        st = lap_race.race_state(rt, float(t))
        lead = st.rungs[0].lap_id
        for r in st.rungs[1:]:
            errs.append(r.interval_s - _true_interval(laps, r.lap_id, lead, float(t)))
    return np.asarray(errs)


def test_interval_tracks_the_true_interval():
    """Against the synthetic truth (each lap's dense clock at the leader's physical station): the
    stadium field, every lap on one line, and the synthetic GoPro's 7-corner circuit with one lap
    run 1 m wide through a corner (its extra metres are where a fraction-based place goes wrong)."""
    out = []
    for name, (s, laps), p95_max, worst_max in (
            ("stadium", _race_session(), 0.002, 0.005),
            ("circuit-wide", syn.line_change_session(1.0, 3, "circuit"), 0.003, 0.010)):
        e = _truth_errors(s, laps)
        a = np.abs(e)
        p95, worst = float(np.percentile(a, 95)), float(a.max())
        assert p95 < p95_max and worst < worst_max, (name, p95, worst)
        out.append(f"{name}: n={a.size} σ {np.std(e) * 1e3:.2f} p95 {p95 * 1e3:.2f} "
                   f"max {worst * 1e3:.2f} ms")
    print("test_interval_tracks_the_true_interval OK (" + "; ".join(out) + ")")


def _assert_finish_ladder_is_lap_time_order(s, rt):
    """The check itself, shared with its negative control: after the last finish the ladder is the
    session's lap-time order and each interval is the lap-time difference to the winner."""
    st = lap_race.race_state(rt, rt.duration + 0.5)
    assert st.over
    want = sorted(rt.ids, key=lambda i: (s.lap_time(i), i))
    got = [r.lap_id for r in st.rungs]
    assert got == want, f"finish ladder {got} != lap-time order {want}"
    for r in st.rungs:
        assert abs(r.interval_s - (s.lap_time(r.lap_id) - s.lap_time(want[0]))) < 1e-12


def test_finish_ladder_is_lap_time_order():
    """At the finish, the ladder IS lap-time order, by construction."""
    s, _laps = _race_session()
    _assert_finish_ladder_is_lap_time_order(s, s.race_tracks())
    print("test_finish_ladder_is_lap_time_order OK")


def test_finish_ladder_negative_control_swapped_lap_times():
    """NEGATIVE CONTROL: the same check over a field whose two lap times are swapped (the traces
    untouched) must FAIL — or the check above could not tell a wrong ladder from a right one."""
    s, _laps = _race_session()
    rt = s.race_tracks()
    laps = list(rt.laps)
    a, b = laps[1], laps[3]
    laps[1] = lap_race.RaceLap(**{**vars(a), "lap_time": b.lap_time})
    laps[3] = lap_race.RaceLap(**{**vars(b), "lap_time": a.lap_time})
    planted = lap_race.build(laps, rt.best_total_m, {})
    try:
        _assert_finish_ladder_is_lap_time_order(s, planted)
    except AssertionError as e:
        print(f"test_finish_ladder_negative_control_swapped_lap_times OK (failed as it must: {e})")
        return
    raise AssertionError("the finish-ladder check passed over swapped lap times")


def test_fewer_than_two_laps_is_no_race():
    """One lap is not a race: None, from the accessor and from `build`."""
    s, _laps = _race_session(3)
    assert s.race_tracks([s.best_lap_id()]) is None
    assert s.race_tracks([]) is None
    assert lap_race.build([], 100.0, {}) is None
    assert s.race_tracks([0, 1]) is not None
    print("test_fewer_than_two_laps_is_no_race OK")


def _straight_lap(lap_id: int, speed: float, length: float = 600.0) -> lap_race.RaceLap:
    """A constant-speed lap along +x, sampled at 10 Hz, on the identity warp: its interval to a
    faster lap at `t` is exactly t × (1 − speed / v_leader)."""
    lt = length / speed
    el = np.linspace(0.0, lt, int(lt * 10) + 1)
    d = el * speed
    return lap_race.RaceLap(lap_id=lap_id, lap_time=lt, clock_start=0.0, elapsed=el, dist=d,
                            xs=d.copy(), ys=np.zeros_like(d),
                            warp=(np.array([0.0, length]), np.array([0.0, length])))


def test_tie_rule_both_sides_of_both_thresholds():
    """A rung under the threshold behind the rung above is "≈" with no position; just over, it
    keeps its position. Running: RACE_TIE_S. Both finished: FINISH_TIE_S (the PB precision)."""
    t, v = 20.0, 20.0
    for gap, tied in ((lap_race.RACE_TIE_S - 0.002, True), (lap_race.RACE_TIE_S + 0.002, False)):
        rt = lap_race.build([_straight_lap(0, v), _straight_lap(1, v * (1 - gap / t))], 600.0, {})
        r = lap_race.race_state(rt, t).rungs
        assert abs(r[1].interval_s - gap) < 1e-9, (r[1].interval_s, gap)
        assert r[0].position == 1 and r[1].tied is tied and (r[1].position is None) is tied, r
    for gap, tied in ((lap_race.FINISH_TIE_S - 0.002, True), (lap_race.FINISH_TIE_S + 0.002, False)):
        lt0 = 600.0 / v
        rt = lap_race.build([_straight_lap(0, v), _straight_lap(1, 600.0 / (lt0 + gap))], 600.0, {})
        st = lap_race.race_state(rt, lt0 + gap + 1.0)
        r = st.rungs
        assert st.over and abs(r[1].interval_s - gap) < 1e-9
        assert r[1].tied is tied and (r[1].position is None) is tied, r
    # A gap in (FINISH_TIE_S, RACE_TIE_S) is a tie while running and a position at the line.
    assert library.PB_PRECISION_S == lap_race.FINISH_TIE_S < lap_race.RACE_TIE_S
    print("test_tie_rule_both_sides_of_both_thresholds OK")


def test_a_tie_never_takes_a_position_and_positions_skip():
    """Three laps level within the threshold read 1, ≈, ≈; a fourth clear of them reads 4."""
    t, v = 20.0, 20.0
    laps = [_straight_lap(i, v * (1 - g / t)) for i, g in enumerate((0.0, 0.05, 0.10 + 0.04, 0.5))]
    r = lap_race.race_state(lap_race.build(laps, 600.0, {}), t).rungs
    assert [x.position for x in r] == [1, None, None, 4], r
    assert [x.tied for x in r] == [False, True, True, False]
    print("test_a_tie_never_takes_a_position_and_positions_skip OK")


def test_back_m_is_a_separate_quantity():
    """'m back along the best line' is the leader's place minus the lap's (identity warp: metres
    of odometer) — not the interval × anything."""
    rt = lap_race.build([_straight_lap(0, 20.0), _straight_lap(1, 19.0)], 600.0, {})
    r = lap_race.race_state(rt, 10.0).rungs
    assert abs(r[1].back_m - 10.0) < 1e-9 and abs(r[1].interval_s - 0.5) < 1e-9
    print("test_back_m_is_a_separate_quantity OK")


def test_a_dropout_lap_is_left_out_by_name():
    """A lap with an interior GPS gap (1.2 s of fixes missing) is not in the field — no dot whose
    place is interpolated across the gap — and `.excluded` names it with its reason."""
    laps = syn.drift_noise_laps(specs=[_spec(k) for k in range(4)])
    times, xs, ys, v, cum = laps[2]["cols"]
    keep = np.ones(times.size, bool)
    keep[100:112] = False
    laps[2]["cols"] = (times[keep], xs[keep], ys[keep], v[keep], cum[keep])
    laps[2]["clean_speed"] = laps[2]["clean_speed"][keep]
    s = syn._drift_session(laps)
    assert s.lap_has_dropout(2)
    rt = s.race_tracks()
    assert 2 not in rt.ids and rt.excluded == {2: lap_race.EXCLUDED_DROPOUT}, rt.excluded
    assert set(rt.ids) == {0, 1, 3}
    print("test_a_dropout_lap_is_left_out_by_name OK")


def test_an_unmatched_lap_is_left_out_by_name():
    """A lap no corner boundary could be matched on (driven 8 m outside the line: every boundary
    fails the spatial gate) has only the equal-fraction place — a guess — so it is left out."""
    s, _laps = _race_session(specs=[_spec(0), _spec(1), _spec(2, wide_m=8.0), _spec(3)])
    assert len(s._warp_to_best(2)[0]) <= 2, "fixture: lap 2 was meant to be unmatched"
    rt = s.race_tracks()
    assert rt.excluded == {2: lap_race.EXCLUDED_UNMATCHED} and 2 not in rt.ids, rt.excluded
    print("test_an_unmatched_lap_is_left_out_by_name OK")


def test_no_corner_basis_is_no_race():
    """With no corner partition no lap but the best has a place on track: no race, not a race on
    equal fractions."""
    s, _laps = _race_session(3)
    syn.reset_corner_caches(s, basis=None)
    assert s.race_tracks() is None
    print("test_no_corner_basis_is_no_race OK")


def test_shown_rungs_and_colours():
    """The ladder shows the top LADDER_TOP plus the selected lap below them; the fastest
    COLOURED_LAPS laps are the coloured ones."""
    laps = [_straight_lap(i, 20.0 - 0.3 * i) for i in range(12)]
    rt = lap_race.build(laps, 600.0, {})
    st = lap_race.race_state(rt, 15.0)
    assert rt.coloured == frozenset(range(lap_race.COLOURED_LAPS))
    assert [r.lap_id for r in lap_race.shown_rungs(st)] == list(range(lap_race.LADDER_TOP))
    assert [r.lap_id for r in lap_race.shown_rungs(st, 3)] == list(range(lap_race.LADDER_TOP))
    assert [r.lap_id for r in lap_race.shown_rungs(st, 10)] == list(range(lap_race.LADDER_TOP)) + [10]
    assert [r.coloured for r in st.rungs] == [i < lap_race.COLOURED_LAPS for i in range(12)]
    print("test_shown_rungs_and_colours OK")


def test_36_laps_precompute_and_tick_cost():
    """36 laps: the cold precompute (warps included) is timed and bounded; a tick costs under
    0.2 ms (median of 300, best of three passes) and builds NOTHING — the build counter and the
    memoized object are unchanged across every tick and a repeat accessor read."""
    s, _laps = _race_session(36)
    t0 = time.perf_counter()
    rt = s.race_tracks()
    cold_ms = (time.perf_counter() - t0) * 1e3
    assert rt is not None and len(rt.ids) == 36, rt and rt.excluded
    builds = lap_race.BUILD_COUNT
    ts = np.linspace(0.0, rt.duration + 1.0, 300)
    best = float("inf")
    for _ in range(3):
        per = []
        for t in ts:
            a = time.perf_counter()
            lap_race.race_state(rt, float(t))
            per.append(time.perf_counter() - a)
        best = min(best, float(np.median(per)))
    assert s.race_tracks() is rt
    assert lap_race.BUILD_COUNT == builds, "a tick or a repeat read rebuilt the race"
    assert best < 2e-4, f"race_state {best * 1e6:.0f} µs per tick for 36 laps"
    assert cold_ms < 2000.0, cold_ms
    print(f"test_36_laps_precompute_and_tick_cost OK (cold {cold_ms:.1f} ms, "
          f"tick {best * 1e6:.1f} µs median)")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} LAP RACE TESTS PASSED")
