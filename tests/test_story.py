"""The session's story (`studio/story.py` + `Session.slow_lap_notes` / `session_moments`).

A NEW SURFACE, so a new file: no test pinned "where a slow lap's time went" or a session moment
before this. Two kinds of session, nothing mocked between the grid and the story:
  * PLANTED GRIDS: `stats.corner_matrix` — the CORNERS BY LAP grid's own constructor, so its typical
    pass and ▼ step are the app's — over synthetic corner times with one slow lap per category
    (one / two / partial / spread), a sub-step corner, a corner exactly AT its step, and lap times
    on both sides of the Laps table's 0.07 s tie edge;
  * THE SYNTHETIC DEMO (`studio/dev/make_demo`, generated here, loaded through the real
    `Session.load`): the exact partition, the dropout / excluded rule on the real accessors, the
    memo and its invalidation by a timing edit, and the design lane's STABILITY CLAIM held as a
    test — under seeded jitter (200 draws: lap times ±0.07 s, corner cells ±0.035 s) the biggest
    loss and the longest ▼-less run never change. The best lap's tie list always holds the true
    best when each lap is jittered by up to HALF the floor (±0.035 s, so any GAP moves by up to
    0.07 s, the floor's own definition); at ±0.07 s per lap it loses it in about one draw in nine
    on the demo (lap 4 sits 0.001 s behind lap 13), which is pinned too so the claim cannot be
    restated wider than it holds.
A negative control: swap two laps' corner times and the note moves with them.

Run:  python tests/test_story.py
"""
import os
import re
import sys
import tempfile

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio import stats, story  # noqa: E402

CIDS = [1, 2, 3, 4]
BASE = [3.0, 2.5, 4.0, 3.5]
N_LAPS = 20


def _grid(planted: dict[int, tuple[list[float], float]], noise: float = 0.03, seed: int = 7):
    """(lap_times, matrix) over N_LAPS clean laps: every cell its corner's BASE plus seeded noise
    under `noise`, every lap 45 s plus its corners' noise; `planted[lap] = (corner deltas, lap
    excess)` adds a loss to that lap's cells and its lap time."""
    rng = np.random.default_rng(seed)
    cells, times = [], {}
    for lap in range(N_LAPS):
        jit = rng.uniform(-noise, noise, len(CIDS))
        add, excess = planted.get(lap, ([0.0] * len(CIDS), 0.0))
        cells.append([b + j + a for b, j, a in zip(BASE, jit, add, strict=True)])
        times[lap] = 45.0 + float(jit.sum()) + excess
    resolved = [[True] * len(CIDS) for _ in cells]
    return times, stats.corner_matrix(list(range(N_LAPS)), CIDS, cells, resolved)


def _by_lap(notes):
    return {n.lap_id: n for n in notes}


def test_categories_one_two_partial_spread():
    times, mx = _grid({4: ([0.0, 1.5, 0.0, 0.0], 2.0),     # one corner, 75 % of the excess
                       8: ([0.6, 0.0, 0.6, 0.0], 2.0),     # two corners, 30 % each
                       12: ([0.0, 0.0, 0.0, 0.5], 2.0),    # one ▼, 25 %
                       16: ([0.0, 0.0, 0.0, 0.0], 2.0)})   # nothing beyond a step
    notes = _by_lap(story.slow_lap_notes(times, mx, best_lap=0))
    assert sorted(notes) == [4, 8, 12, 16], f"slow laps {sorted(notes)}, planted 4/8/12/16"
    got = {lap: (n.category, sorted(c.cid for c in n.corners)) for lap, n in notes.items()}
    assert [c.loss_s for c in notes[8].corners] == sorted((c.loss_s for c in notes[8].corners),
                                                          reverse=True), "named worst first"
    assert got == {4: ("one", [2]), 8: ("two", [1, 3]), 12: ("partial", [4]), 16: ("spread", [])}, got
    assert re.search(r"· C2 \+1\.\d\d s over your typical pass \(▼\)$", notes[4].text), notes[4].text
    assert re.search(r"· C4 ▼ \+0\.\d\d s; the rest is spread across the lap$", notes[12].text), \
        notes[12].text
    assert notes[16].text.endswith("Spread across the lap: no corner beyond its ▼ step"), notes[16].text
    assert all(n.rule.startswith("Slow: more than 0.50 s over your median clean lap") for n in notes.values())
    print("test_categories_one_two_partial_spread OK")


def test_a_slow_lap_without_a_mark_never_names_a_corner():
    """1.9 s over the typical lap, 0.29 s of it in C1 — under C1's 0.30 s ▼ step — and the rest on
    the straights: no straight has a noise gate, so the note names nothing."""
    times, mx = _grid({5: ([0.29, 0.0, 0.0, 0.0], 1.9)}, noise=0.0)
    (note,) = story.slow_lap_notes(times, mx, best_lap=0)
    assert note.category == "spread" and note.corners == (), note
    assert re.search(r"\bC\d", note.text) is None, note.text
    print("test_a_slow_lap_without_a_mark_never_names_a_corner OK")


def test_a_corner_exactly_at_its_step_is_named_and_a_hundredth_under_is_not():
    """The ▼ is `>=` at print resolution (`CornerMatrix.is_behind`): flat cells give C1 a median of
    3.00 and the 0.30 s floor as its step, so 3.30 is ▼ and 3.29 is not."""
    times, mx = _grid({5: ([0.30, 0.0, 0.0, 0.0], 0.55)}, noise=0.0)
    (note,) = story.slow_lap_notes(times, mx, best_lap=0)
    assert (note.category, [c.cid for c in note.corners]) == ("one", [1]), note
    assert note.corners[0].step_s == stats.MATRIX_SCALE_MIN_S, note.corners
    times, mx = _grid({5: ([0.29, 0.0, 0.0, 0.0], 0.55)}, noise=0.0)
    (note,) = story.slow_lap_notes(times, mx, best_lap=0)
    assert note.category == "spread", note
    print("test_a_corner_exactly_at_its_step_is_named_and_a_hundredth_under_is_not OK")


def test_best_lap_ties_on_the_printed_value_at_the_lap_tables_edge():
    """Level with the best is the Laps table's rule: the PRINTED gap under 0.07 s. 0.069 ties,
    0.070 does not — and the copy of its `printed_ms` here is its to the digit."""
    from studio import lap_table
    times = {0: 45.0, 1: 45.069, 2: 45.070, 3: 45.5, 4: 46.0}
    m = story.session_moments(times, None, best_lap=0).best_lap
    assert m is not None and m.lap_ids == (0,) and [t.lap_ids for t in m.tie] == [(1,)], m
    assert m.text == "Best lap: Lap 1 · 0:45.000 · ≈ Lap 2 (within 0.07 s: timing precision)", m.text
    # 45.0004 / 45.0016 print 0:45.000 / 0:45.002: the raw doubles are 0.0012 apart.
    rng = np.random.default_rng(3)
    for t in [45.0004, 45.0016, 59.9996, 60.0004, *rng.uniform(30, 130, 2000)]:
        assert story.printed_ms(float(t)) == lap_table.printed_ms(float(t)), t
    print("test_best_lap_ties_on_the_printed_value_at_the_lap_tables_edge OK")


def test_five_lap_window_tie_and_no_window_under_five_consecutive_laps():
    """Three runs of exactly five clean laps (the gaps are laps that are not clean): medians 45.000,
    45.069 and 45.070 — the second is named with the best, the third is not."""
    times = {}
    for start, med in ((0, 45.0), (10, 45.069), (20, 45.070)):
        for k, d in enumerate((-0.2, -0.1, 0.0, 0.1, 0.3)):
            times[start + k] = med + d
    m = story.session_moments(times, None, best_lap=0).best_window
    assert m is not None and m.lap_ids == (0, 1, 2, 3, 4), m
    assert [t.lap_ids for t in m.tie] == [(10, 11, 12, 13, 14)], m.tie
    four = {i: 45.0 + 0.01 * i for i in (0, 1, 2, 3, 5, 6, 7, 8)}
    assert story.session_moments(four, None, best_lap=0).best_window is None
    print("test_five_lap_window_tie_and_no_window_under_five_consecutive_laps OK")


def test_timing_trust_one_rule():
    """The grid keeps its ▼ under a provisional line or an estimated clock, so notes exist and say
    so; the Laps table blanks Δbest and withholds the ★ under a provisional line, so the note has
    no gap and there is no best-lap moment."""
    times, mx = _grid({4: ([0.0, 1.5, 0.0, 0.0], 2.0)})
    (prov,) = story.slow_lap_notes(times, mx, 0, provisional=True)
    assert prov.caveat == "provisional" and prov.gap_to_best_s is None and prov.category == "one"
    assert "off your best" not in prov.text, prov.text
    (est,) = story.slow_lap_notes(times, mx, 0, clock_estimated=True)
    assert est.caveat == "estimated" and est.gap_to_best_s is not None, est
    m = story.session_moments(times, mx, 0, provisional=True)
    assert m.best_lap is None and m.biggest_loss is not None and m.biggest_loss.caveat == "provisional"
    print("test_timing_trust_one_rule OK")


def test_negative_control_the_note_moves_with_the_corner_times():
    times, mx = _grid({4: ([0.0, 1.5, 0.0, 0.0], 2.0)})
    cells = [list(r) for r in mx.cells]
    cells[4], cells[9] = cells[9], cells[4]
    times[4], times[9] = times[9], times[4]
    swapped = stats.corner_matrix(mx.lap_ids, mx.cids, cells, mx.resolved)
    notes = story.slow_lap_notes(times, swapped, best_lap=0)
    assert [(n.lap_id, n.category) for n in notes] == [(9, "one")], notes
    print("test_negative_control_the_note_moves_with_the_corner_times OK")


# ------------------------------------------------------------------------- the synthetic demo
_DEMO: dict = {}


def _demo():
    """The synthetic demo through the real loader, generated once per process (held by the module
    so its temp folder outlives every caller)."""
    if not _DEMO:
        from studio.dev import make_demo as md
        from studio.dev import synth_gopro as sg
        from studio.session import Session
        tmp = tempfile.TemporaryDirectory(prefix="story_demo_")
        rec = sg.generate(os.path.join(tmp.name, "rec"), md.DEMO_SEED, md.DEMO_LAPS, chapters=1,
                          origin=md.DEMO_ORIGIN)
        _DEMO.update(tmp=tmp, session=Session.load([rec.paths[0]]))
    return _DEMO["session"]


def test_demo_partition_sums_to_the_lap_time_and_the_grid_reads_its_corner_pieces():
    s = _demo()
    mx, sb = s.corner_matrix(), s.corners.segment_bests()
    for r, lap in enumerate(mx.lap_ids):
        row = sb.times[sb.lap_ids.index(lap)]
        assert len(row) == 2 * len(mx.cids) + 1
        assert abs(float(row.sum()) - s.lap_time(lap)) < 1e-9, (lap, float(row.sum()), s.lap_time(lap))
        assert list(row[1::2]) == mx.cells[r], f"lap {lap}: the grid's cells are not the partition's"
    print("test_demo_partition_sums_to_the_lap_time_and_the_grid_reads_its_corner_pieces OK")


def test_demo_story():
    """The demo's one slow lap (internal 5: the 50.6 s lap) and its four moments."""
    s = _demo()
    (note,) = s.slow_lap_notes()
    assert (note.lap_id, note.category, [c.cid for c in note.corners]) == (5, "one", [5]), note
    m = s.session_moments()
    assert m.best_lap.lap_ids == (12,) and [t.lap_ids for t in m.best_lap.tie] == [(3,)], m.best_lap
    assert (m.biggest_loss.lap_ids, m.biggest_loss.cid) == ((5,), 5), m.biggest_loss
    assert m.clean_run.lap_ids == tuple(range(6, 14)) and m.clean_run.tie == (), m.clean_run
    assert m.best_window is not None and len(m.best_window.lap_ids) == story.RUN_WINDOW
    print("test_demo_story OK")


def test_demo_dropout_and_excluded_laps_get_no_note():
    """On the real accessors: the slow lap made a ⚠ dropout lap, then a ⊘ excluded one, is no row
    of the grid and gets no note — and is in no moment."""
    s = _demo()
    assert [n.lap_id for n in s.slow_lap_notes()] == [5]
    orig_drop, orig_valid = s.lap_has_dropout, s.valid_lap_ids
    try:
        s.lap_has_dropout = lambda i: i == 5 or orig_drop(i)
        assert s.slow_lap_notes() == [], s.slow_lap_notes()
        # Lap 5 held the demo's only ▼ cells: without it there is no loss to name at all.
        assert s.session_moments().biggest_loss is None, s.session_moments().biggest_loss
        del s.lap_has_dropout
        s.valid_lap_ids = lambda: [i for i in orig_valid() if i != 5]
        assert s.slow_lap_notes() == [], s.slow_lap_notes()
    finally:
        for name in ("lap_has_dropout", "valid_lap_ids"):
            s.__dict__.pop(name, None)
    assert [n.lap_id for n in s.slow_lap_notes()] == [5]
    print("test_demo_dropout_and_excluded_laps_get_no_note OK")


def test_demo_memo_is_invalidated_by_a_timing_edit():
    s = _demo()
    first = s.slow_lap_notes()
    assert s.slow_lap_notes() is first and s.session_moments() is s.session_moments()
    s.set_timing_lines(s.start_line, list(s.sector_lines), user_confirm=False)
    again = s.slow_lap_notes()
    assert again is not first, "a timing edit re-segmented the session and the story memo hit"
    assert [n.text for n in again] == [n.text for n in first]
    print("test_demo_memo_is_invalidated_by_a_timing_edit OK")


def _jittered(s, lap_j: float, cell_j: float, draws: int = 200, seed: int = 20261115):
    times = {i: s.lap_time(i) for i in s.consistency_lap_ids()}
    mx = s.corner_matrix()
    rng = np.random.default_rng(seed)
    for _ in range(draws):
        tj = {i: t + rng.uniform(-lap_j, lap_j) for i, t in times.items()}
        cj = [[None if v is None else v + rng.uniform(-cell_j, cell_j) for v in row]
              for row in mx.cells]
        best = min(tj, key=lambda i: (story.printed_ms(tj[i]), i))
        yield story.session_moments(tj, stats.corner_matrix(mx.lap_ids, mx.cids, cj, mx.resolved),
                                    best)


def test_demo_moments_are_stable_under_timing_jitter():
    s = _demo()
    base, true_best = s.session_moments(), s.best_lap_id()
    for m in _jittered(s, 0.07, 0.035):
        assert (m.biggest_loss.lap_ids, m.biggest_loss.cid) == \
            (base.biggest_loss.lap_ids, base.biggest_loss.cid), m.biggest_loss
        assert (m.clean_run.lap_ids, m.clean_run.tie) == (base.clean_run.lap_ids, base.clean_run.tie)
    half = list(_jittered(s, story.PB_PRECISION_S / 2, 0.035))
    assert all(true_best in {*m.best_lap.lap_ids, *(t.lap_ids[0] for t in m.best_lap.tie)}
               for m in half), "the best lap's tie list lost the true best under ±0.035 s per lap"
    lost = sum(true_best not in {*m.best_lap.lap_ids, *(t.lap_ids[0] for t in m.best_lap.tie)}
               for m in _jittered(s, 0.07, 0.035))
    assert 0 < lost < 60, f"±0.07 s per lap lost the true best in {lost} of 200 draws (docstring: ~1 in 9)"
    print(f"test_demo_moments_are_stable_under_timing_jitter OK (±0.07 per lap: {lost}/200 lost)")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} STORY TESTS PASSED")
