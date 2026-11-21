"""The Lap Race core (`studio/lap_race.py` + `Session.race_tracks`) against KNOWN truth.

A NEW SURFACE, so a new file: no test file pinned a race, a ladder or an interval-to-leader before
this. Every session here is `tests/_synthetic.py`'s real bare Session over laps driven round the
stadium (or the synthetic GoPro's circuit) at known speeds, whose `dense` clocks are the truth;
nothing is mocked between the session's warp and the ladder. What is pinned:
  * the interval to the leader IS the compare badge's Δ (`Session.delta_between`) at every tick, and
    `interval_to_best` is the Δ to the best lap, through leader changes;
  * it tracks the true interval (each lap's own clock read at the leader's physical station), and
    the running tie threshold covers that error;
  * the leader is the lap furthest ALONG, not the fastest lap — a field whose leader changes;
  * at the finish the ladder is lap-time order — with its negative control: two lap times swapped
    and the same check fails — and the first pack is exactly the Laps table's "level with best";
  * packs never chain; fewer than two laps → None;
  * timing trust: a provisional start line draws an unranked race, an estimated clock widens ties;
  * every lap not raced is named with its reason (not clean, dropout, unmatched);
  * a tick is cheap and rebuilds nothing (a build counter), and the 36-lap precompute is timed.

Run:  python tests/test_lap_race.py
"""
import os
import re
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _synthetic as syn  # noqa: E402

from studio import data_quality, lap_race, library  # noqa: E402


def _spec(k: int, wide_m: float = 0.0) -> dict:
    """Lap `k` of a field: the stadium's reference line, a little slower per k (corner speed and
    top speed both step), its own noise seed; `wide_m` runs it that far outside the line."""
    vc = 12.5 - 0.03 * k
    vt = 22.0 - 0.02 * k
    return dict(offset=wide_m, vc=vc, straights=((vt, 2.0), (vt, 2.0)), run_wide_m=0.0,
                seed=100 + k)


def _trusted(s):
    """The synthetic sessions have no detected track: confirm the start line, as a user would."""
    s._timing_user_confirmed = True
    return s


def _race_session(n: int = 6, specs=None):
    """(session, laps): a real bare Session over `n` contiguous stadium laps, lap 0 the fastest,
    on a confirmed start line."""
    laps = syn.drift_noise_laps(specs=specs or [_spec(k) for k in range(n)])
    return _trusted(syn._drift_session(laps)), laps


def _circuit_session():
    """The synthetic GoPro's 7-corner circuit, lap 1 run 1 m wide through C3 (`line_change_*`)."""
    s, laps = syn.line_change_session(1.0, 3, "circuit")
    return _trusted(s), laps


# Lap 1 is quicker than lap 0 down the first straight and much slower down the second: it LEADS the
# early race and loses it, so the lap-time best does not lead throughout.
_LEADER_CHANGE = [_spec(0), dict(_spec(1), straights=((24.0, 0.5), (19.0, 4.0))), _spec(2)]


def _true_interval(laps, k: int, lead: int, t: float) -> float:
    """TRUTH: the leader's own clock at the physical station lap `k` is at, `t` s into its lap."""
    u_k, _odo, clock_k = laps[k]["dense"]
    u_l, _odo_l, clock_l = laps[lead]["dense"]
    station = float(np.interp(min(t, clock_k[-1]), clock_k, u_k))
    return min(t, float(clock_k[-1])) - float(np.interp(station, u_l, clock_l))


def test_interval_is_the_compare_badge_delta():
    """Every rung's interval equals `delta_between(lap, leader, lap_start + t)` — the number the
    compare badge prints for that pair — and `interval_to_best` equals `delta_between(lap, best,
    …)`, at 400 ticks from the start to past the last finish, on the stadium field, the circuit
    with a lap 1 m wide through one corner (where the warp and an equal-fraction place disagree)
    and the leader-change field."""
    worst, worst_best, checks = 0.0, 0.0, 0
    for s, _laps in (_race_session(), _circuit_session(), _race_session(specs=_LEADER_CHANGE)):
        rt = s.race_tracks()
        best = s.best_lap_id()
        assert rt is not None and len(rt.ids) == len(s.valid_lap_ids()) and not rt.excluded
        for t in np.linspace(0.0, rt.duration + 1.0, 400):
            st = lap_race.race_state(rt, float(t))
            lead = st.rungs[0].lap_id
            for r in st.rungs:
                start = float(rt.clock_starts[rt.index(r.lap_id)])
                db = s.delta_between(r.lap_id, best, start + float(t))
                worst_best = max(worst_best, abs(db - lap_race.interval_to_best(st, r.lap_id)))
                if r.lap_id == lead:
                    continue
                db = s.delta_between(r.lap_id, lead, start + float(t))
                assert db is not None
                worst = max(worst, abs(db - r.interval_s))
                checks += 1
    assert worst < 1e-6, f"interval disagrees with delta_between by {worst * 1e3:.4f} ms"
    assert worst_best < 1e-6, f"interval_to_best disagrees by {worst_best * 1e3:.4f} ms"
    print(f"test_interval_is_the_compare_badge_delta OK ({checks} checks, max |Δ| {worst:.1e} s, "
          f"to best {worst_best:.1e} s)")


def test_the_leader_is_the_lap_furthest_along_not_the_fastest():
    """A field whose leader changes: lap 1 (not the lap-time best) leads the early race. At every
    tick the running laps are ordered furthest along first, no interval is negative, and at some
    tick the leader is not the lap-time best."""
    s, _laps = _race_session(specs=_LEADER_CHANGE)
    rt = s.race_tracks()
    best = s.best_lap_id()
    assert best == 0 and rt.ids[0] == 0
    leaders = set()
    for t in np.arange(0.0, rt.duration + 1.0, 0.1):
        st = lap_race.race_state(rt, float(t))
        running = [r for r in st.rungs if not r.finished]
        places = [float(st.place_m[rt.index(r.lap_id)]) for r in running]
        assert places == sorted(places, reverse=True), (t, places)
        assert all(r.interval_s >= -1e-9 for r in st.rungs), (t, [r.interval_s for r in st.rungs])
        leaders.add(st.rungs[0].lap_id)
        if 6.0 < t < 20.0:   # measured: lap 1 leads from 4.2 s to 23.5 s
            assert st.rungs[0].lap_id == 1, (t, st.rungs[0])
    assert leaders >= {0, 1}, leaders
    print("test_the_leader_is_the_lap_furthest_along_not_the_fastest OK")


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


def test_interval_tracks_the_true_interval_inside_the_place_allowance():
    """Against the synthetic truth (each lap's dense clock at the leader's physical station): the
    stadium field, every lap on one line, and the circuit with one lap 1 m wide through a corner
    (its extra metres are where a fraction-based place goes wrong). The place-match error's p95 sits
    inside `PLACE_MATCH_ALLOWANCE_S`, the share of `RACE_TIE_S` above the finish floor."""
    assert lap_race.RACE_TIE_S == round(library.PB_PRECISION_S + lap_race.PLACE_MATCH_ALLOWANCE_S, 2)
    out = []
    for name, (s, laps), p95_max, worst_max in (
            ("stadium", _race_session(), 0.002, 0.005),
            ("circuit-wide", _circuit_session(), 0.003, 0.010)):
        e = _truth_errors(s, laps)
        a = np.abs(e)
        p95, worst = float(np.percentile(a, 95)), float(a.max())
        assert p95 < p95_max and worst < worst_max, (name, p95, worst)
        assert p95 <= lap_race.PLACE_MATCH_ALLOWANCE_S
        out.append(f"{name}: n={a.size} σ {np.std(e) * 1e3:.2f} p95 {p95 * 1e3:.2f} "
                   f"max {worst * 1e3:.2f} ms")
    print("test_interval_tracks_the_true_interval_inside_the_place_allowance OK ("
          + "; ".join(out) + ")")


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
    planted = lap_race.build(laps, rt.best, {})
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
    assert lap_race.build([], _straight_lap(0, 20.0), {}) is None
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


def _field(laps, **kw):
    return lap_race.build(laps, laps[0], {}, **kw)


def test_tie_rule_both_sides_of_both_thresholds():
    """A lap under the threshold behind its pack's leader is a member ("≈") with no position; just
    over, it leads its own pack. Running: RACE_TIE_S. Both finished: FINISH_TIE_S (PB precision)."""
    t, v = 20.0, 20.0
    for gap, tied in ((lap_race.RACE_TIE_S - 0.002, True), (lap_race.RACE_TIE_S + 0.002, False)):
        rt = _field([_straight_lap(0, v), _straight_lap(1, v * (1 - gap / t))])
        st = lap_race.race_state(rt, t)
        r = st.rungs
        assert abs(r[1].interval_s - gap) < 1e-9, (r[1].interval_s, gap)
        assert r[0].position == 1 and r[1].tied is tied and (r[1].position is None) is tied, r
        assert len(st.packs) == (1 if tied else 2)
    for gap, tied in ((lap_race.FINISH_TIE_S - 0.002, True), (lap_race.FINISH_TIE_S + 0.002, False)):
        lt0 = 600.0 / v
        rt = _field([_straight_lap(0, v), _straight_lap(1, 600.0 / (lt0 + gap))])
        st = lap_race.race_state(rt, lt0 + gap + 1.0)
        r = st.rungs
        assert st.over and abs(r[1].interval_s - gap) < 1e-9
        assert r[1].tied is tied and (r[1].position is None) is tied, r
    assert library.PB_PRECISION_S == lap_race.FINISH_TIE_S < lap_race.RACE_TIE_S
    print("test_tie_rule_both_sides_of_both_thresholds OK")


def test_packs_do_not_chain_and_the_first_pack_is_the_tables_level_set():
    """Finished laps at +0.00/+0.05/+0.10/+0.15 s: the third is 0.10 s behind the pack's LEADER, so
    it leads a second pack (position 3) with the fourth as its member — never one chained pack of
    four. And the first pack is exactly the laps the Laps table calls level with the best
    (`library.within_timing_precision` of each lap time minus the best)."""
    v, lt0 = 20.0, 30.0
    gaps = (0.0, 0.05, 0.10, 0.15)
    laps = [_straight_lap(i, 600.0 / (lt0 + g)) for i, g in enumerate(gaps)]
    st = lap_race.race_state(_field(laps), lt0 + 1.0)
    assert st.over
    assert [p.laps for p in st.packs] == [(0, 1), (2, 3)], st.packs
    assert [p.position for p in st.packs] == [1, 3]
    assert [r.position for r in st.rungs] == [1, None, 3, None]
    assert abs(st.packs[1].interval_s - 0.10) < 1e-9 and abs(st.packs[1].spread_s - 0.05) < 1e-9
    level = {lap.lap_id for lap in laps
             if library.within_timing_precision(lap.lap_time - laps[0].lap_time)}
    assert set(st.packs[0].laps) == level, (st.packs[0], level)
    # Running, the same rule at RACE_TIE_S: 0, 0.05 | 0.14 | 0.5 — positions 1, 3, 4.
    t = 20.0
    laps = [_straight_lap(i, v * (1 - g / t)) for i, g in enumerate((0.0, 0.05, 0.14, 0.5))]
    st = lap_race.race_state(_field(laps), t)
    assert [r.position for r in st.rungs] == [1, None, 3, 4], st.rungs
    assert [p.laps for p in st.packs] == [(0, 1), (2,), (3,)]
    print("test_packs_do_not_chain_and_the_first_pack_is_the_tables_level_set OK")


def test_back_m_is_a_separate_quantity():
    """'m back along the best line' is the leader's place minus the lap's (identity warp: metres
    of odometer) — not the interval × anything."""
    rt = _field([_straight_lap(0, 20.0), _straight_lap(1, 19.0)])
    r = lap_race.race_state(rt, 10.0).rungs
    assert abs(r[1].back_m - 10.0) < 1e-9 and abs(r[1].interval_s - 0.5) < 1e-9
    print("test_back_m_is_a_separate_quantity OK")


def test_a_provisional_start_line_draws_the_race_unranked():
    """On an unconfirmed start line every lap time is referenced to an arbitrary point: the race
    keeps every dot but has no rungs, packs, positions or intervals, and says why."""
    s, _laps = _race_session()
    ranked = s.race_tracks()
    s._timing_user_confirmed = False
    assert not s.timing_verified
    rt = s.race_tracks()
    assert rt is not ranked and not rt.ranked and rt.unranked_reason == lap_race.UNRANKED_PROVISIONAL
    st = lap_race.race_state(rt, 12.0)
    assert not st.ranked and st.rungs == [] and st.packs == []
    assert st.unranked_reason == lap_race.UNRANKED_PROVISIONAL
    assert lap_race.interval_to_best(st, rt.ids[1]) is None
    ref = lap_race.race_state(ranked, 12.0)
    assert np.array_equal(st.x, ref.x) and np.array_equal(st.place_m, ref.place_m)
    print("test_a_provisional_start_line_draws_the_race_unranked OK")


def test_an_estimated_clock_widens_both_ties_to_its_published_bound():
    """On the media-clock fallback a lap time can read up to ~0.15 s off: both thresholds become
    that bound, the number `data_quality.MEDIA_CLOCK_LAP_ERROR` publishes."""
    bound = float(re.search(r"(\d+\.\d+)\s*s", data_quality.MEDIA_CLOCK_LAP_ERROR).group(1))
    assert lap_race.ESTIMATED_CLOCK_TIE_S == bound
    s, _laps = _race_session()
    assert s.race_tracks().race_tie_s == lap_race.RACE_TIE_S
    s._timing_quality = data_quality.TimingQuality(clock=data_quality.MEDIA_CLOCK_FALLBACK)
    rt = s.race_tracks()
    assert rt.clock_estimated and rt.race_tie_s == rt.finish_tie_s == bound
    # A 0.12 s gap at the line: two positions on a true clock, one pack on an estimated one.
    laps = [_straight_lap(0, 20.0), _straight_lap(1, 600.0 / 30.12)]
    for est, packs in ((False, 2), (True, 1)):
        st = lap_race.race_state(_field(laps, clock_estimated=est), 31.0)
        assert len(st.packs) == packs, (est, st.packs)
    t = 20.0  # and 0.12 s while running: a position on a true clock, a member on an estimated one
    laps = [_straight_lap(0, 20.0), _straight_lap(1, 20.0 * (1 - 0.12 / t))]
    for est, packs in ((False, 2), (True, 1)):
        assert len(lap_race.race_state(_field(laps, clock_estimated=est), t).packs) == packs
    print("test_an_estimated_clock_widens_both_ties_to_its_published_bound OK")


def test_a_dropout_lap_is_left_out_by_name():
    """A lap with an interior GPS gap (1.2 s of fixes missing) is not in the field — no dot whose
    place is interpolated across the gap — and `.excluded` names it with its reason."""
    laps = syn.drift_noise_laps(specs=[_spec(k) for k in range(4)])
    times, xs, ys, v, cum = laps[2]["cols"]
    keep = np.ones(times.size, bool)
    keep[100:112] = False
    laps[2]["cols"] = (times[keep], xs[keep], ys[keep], v[keep], cum[keep])
    laps[2]["clean_speed"] = laps[2]["clean_speed"][keep]
    s = _trusted(syn._drift_session(laps))
    assert s.lap_has_dropout(2)
    rt = s.race_tracks()
    assert 2 not in rt.ids and rt.excluded == {2: lap_race.EXCLUDED_DROPOUT}, rt.excluded
    assert set(rt.ids) == {0, 1, 3}
    print("test_a_dropout_lap_is_left_out_by_name OK")


def test_a_lap_that_left_the_line_is_left_out_by_name():
    """The race field is the bests' clean set (`Session.lap_out_of_bests`), so the caption "each
    clean lap on its own clock" is true: a lap that cut across the infield (`off_line_metres`) does
    not race — release QA, wave 21: the cut lap led the pack and finished 0.73 s ahead of the best
    lap — and `.excluded` names it with its own reason. The same goes for a caller's own field (the
    export ghost's two laps)."""
    s, _laps = _race_session(5)
    from studio.session import _UNSET
    s.off_line_metres = lambda: {0: 30.0}     # lap 0, the quickest, cut: never the best
    s._best_cache = _UNSET
    assert s.best_lap_id() != 0
    rt = s.race_tracks()
    assert 0 not in rt.ids and rt.excluded == {0: lap_race.EXCLUDED_OFF_LINE}, rt.excluded
    assert set(rt.ids) == {1, 2, 3, 4}
    assert s.race_tracks([0, s.best_lap_id()]) is None
    # A lap timed across a missing chapter (#700) is out under ITS kind, never "off the line".
    s.chapter_gap_notes = lambda: {2: "Lap 3 was timed across a missing chapter (…)"}
    s._race_memo = None
    s._best_cache = _UNSET
    rt = s.race_tracks()
    assert rt.excluded == {0: lap_race.EXCLUDED_OFF_LINE, 2: lap_race.EXCLUDED_CHAPTER_GAP}, \
        rt.excluded
    assert set(rt.ids) == {1, 3, 4}
    print("test_a_lap_that_left_the_line_is_left_out_by_name OK")


def test_an_unmatched_lap_is_left_out_by_name():
    """A lap no corner boundary could be matched on (driven 8 m outside the line: every boundary
    fails the spatial gate) has only the equal-fraction place — a guess — so it is left out."""
    s, _laps = _race_session(specs=[_spec(0), _spec(1), _spec(2, wide_m=8.0), _spec(3)])
    assert len(s._warp_to_best(2)[0]) <= 2, "fixture: lap 2 was meant to be unmatched"
    rt = s.race_tracks()
    assert rt.excluded == {2: lap_race.EXCLUDED_UNMATCHED} and 2 not in rt.ids, rt.excluded
    print("test_an_unmatched_lap_is_left_out_by_name OK")


def test_a_lap_that_is_not_clean_cannot_be_raced():
    """A lap the band left out (not in `valid_lap_ids`) is never raced — not by default, not when a
    caller names it — and `.excluded` lists it as not clean."""
    s, _laps = _race_session(5)
    s._valid_cache = [0, 1, 2, 4]
    rt = s.race_tracks()
    assert 3 not in rt.ids and rt.excluded == {3: lap_race.EXCLUDED_NOT_CLEAN}, rt.excluded
    named = s.race_tracks([0, 1, 3])
    assert set(named.ids) == {0, 1} and named.excluded == {3: lap_race.EXCLUDED_NOT_CLEAN}
    print("test_a_lap_that_is_not_clean_cannot_be_raced OK")


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
    rt = _field(laps)
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


def test_a_read_of_its_own_field_leaves_the_default_memo_alone():
    """The export ghost reads a two-lap field (`race_tracks([lap, best])`) on the render thread.
    That read once REPLACED the session's one memo, so the app's next default read rebuilt the
    whole field and was no longer the object it held. Only the default field is memoized now."""
    s, _laps = _race_session(6)
    rt = s.race_tracks()
    best = s.best_lap_id()
    other = next(i for i in rt.ids if i != best)
    pair = s.race_tracks([other, best])
    assert pair is not None and set(pair.ids) == {other, best}, pair
    builds = lap_race.BUILD_COUNT
    assert s.race_tracks() is rt, "a two-lap read evicted the default field's memo"
    assert lap_race.BUILD_COUNT == builds, "the default field was rebuilt after a two-lap read"
    print("test_a_read_of_its_own_field_leaves_the_default_memo_alone OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} LAP RACE TESTS PASSED")
