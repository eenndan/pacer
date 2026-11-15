"""The session's story (`studio/story.py` + `Session.slow_lap_notes` / `session_moments`).

A NEW SURFACE, so a new file: no test pinned "where a slow lap's time went" or a session moment
before this. Two kinds of session, nothing mocked between the grid and the story:
  * PLANTED GRIDS and lap times: `stats.corner_matrix` — the CORNERS BY LAP grid's own
    constructor, so its typical pass and ▼ step are the app's — over synthetic corner times with
    one slow lap per category (over_lap / one / two / partial / spread), a share of exactly 0.50,
    a sub-step corner, a corner exactly AT its step, the first lap, a half-slow session; lap times
    on both sides of the Laps table's 0.07 s tie edge, 5-lap windows that tie, span and break; and
    a planted 0.001 s NEAR-TIE for the best lap's jitter claim: each lap jittered by up to ±0.035 s
    (any GAP by up to 0.07 s, the floor's own definition) never loses the true best from the tie
    list, ±0.07 s per lap does (33 of 200 draws on this field) — pinned both ways so the claim
    cannot be restated wider than it holds;
  * THE SHIPPED DEMO, built as the app's demo is (`make_demo.make(path, render=False)`: the same
    telemetry, habit included, under the placeholder picture) and loaded through the real
    `Session.load`: the exact partition, its notes and moments verbatim, the dropout / excluded
    rule on the real accessors, the memo and its invalidation by a timing edit, and stability
    under seeded jitter (200 draws: lap times ±0.07 s, corner cells ±0.035 s) — the biggest loss
    never changes, and the best lap (0.215 s clear) never leaves its tie list.
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


def test_categories_and_their_one_baseline_wording():
    times, mx = _grid({4: ([0.0, 1.5, 0.0, 0.4], 2.0),     # one corner, 75 %; C4 ▼ beside it
                       8: ([0.6, 0.0, 0.6, 0.0], 2.0),     # two corners, 30 % each
                       12: ([0.0, 0.0, 0.0, 0.5], 2.0),    # one ▼, 25 %
                       16: ([0.0, 0.0, 0.0, 0.0], 2.0),    # nothing beyond a step
                       18: ([2.0, 0.0, 0.0, 0.0], 0.8)})   # a corner losing more than the lap
    notes = _by_lap(story.slow_lap_notes(times, mx, best_lap=0))
    assert sorted(notes) == [4, 8, 12, 16, 18], f"slow laps {sorted(notes)}, planted 4/8/12/16/18"
    got = {lap: (n.category, sorted(c.cid for c in n.corners)) for lap, n in notes.items()}
    assert got == {4: ("one", [2]), 8: ("two", [1, 3]), 12: ("partial", [4]),
                   16: ("spread", []), 18: ("over_lap", [1])}, got
    assert [c.loss_s for c in notes[8].corners] == sorted((c.loss_s for c in notes[8].corners),
                                                          reverse=True), "named worst first"
    head = r"^Lap \d+ · \d\.\d\d s over your typical lap · "
    for lap, tail in {
            4: r"C2 \+1\.\d\d s of it, over your typical pass \(▼\); also C4 ▼ \+0\.\d\d s$",
            8: r"C\d \+0\.\d\d s and C\d \+0\.\d\d s of it, over your typical passes \(▼\)$",
            12: r"C4 ▼ \+0\.\d\d s; the rest is spread across the lap$",
            16: r"Spread across the lap: no corner beyond its ▼ step$",
            18: r"C1 ▼ \+2\.\d\d s, more than the whole lap: time was gained back elsewhere$"}.items():
        assert re.search(head + tail, notes[lap].text), notes[lap].text
    assert notes[4].text.startswith("Lap 5 · 2.0"), notes[4].text
    assert [c.cid for c in notes[4].also] == [4] and not notes[18].also
    assert all("off your best" not in n.text for n in notes.values())
    assert all(n.gap_to_best_s is not None for n in notes.values())
    assert all(n.rule.startswith("Slow: more than 0.50 s over your typical lap") for n in notes.values())
    assert all("not typical" not in n.rule for n in notes.values())
    print("test_categories_and_their_one_baseline_wording OK")


def test_no_marked_corner_is_dropped_and_the_also_list_is_capped():
    times, mx = _grid({6: ([0.4, 3.0, 0.4, 0.4], 4.0)}, noise=0.0)
    (note,) = story.slow_lap_notes(times, mx, best_lap=0)
    assert note.category == "one" and len(note.also) == 3, note
    assert note.text.endswith("; also C1 ▼ +0.40 s, C3 ▼ +0.40 s, +1 more"), note.text
    print("test_no_marked_corner_is_dropped_and_the_also_list_is_capped OK")


def test_a_share_of_exactly_half_is_not_a_culprit():
    """The culprit share is STRICTLY over CULPRIT_SHARE: 0.50 s of a 1.00 s excess is `partial`."""
    times, mx = _grid({5: ([0.50, 0.0, 0.0, 0.0], 1.0)}, noise=0.0)
    (note,) = story.slow_lap_notes(times, mx, best_lap=0)
    assert (note.excess_s, note.corners[0].loss_s) == (1.0, 0.5), note
    assert note.category == "partial", note
    print("test_a_share_of_exactly_half_is_not_a_culprit OK")


def test_a_slow_lap_without_a_mark_never_names_a_corner():
    """1.9 s over the typical lap, 0.29 s of it in C1 — under C1's 0.30 s ▼ step — and the rest on
    the straights: no straight has a noise gate, so the note names nothing."""
    times, mx = _grid({5: ([0.29, 0.0, 0.0, 0.0], 1.9)}, noise=0.0)
    (note,) = story.slow_lap_notes(times, mx, best_lap=0)
    assert note.category == "spread" and note.corners == () and note.also == (), note
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


def test_the_first_lap_is_tagged():
    times, mx = _grid({0: ([0.0, 1.5, 0.0, 0.0], 2.0), 7: ([0.0, 1.5, 0.0, 0.0], 2.0)})
    notes = _by_lap(story.slow_lap_notes(times, mx, best_lap=1, first_lap_id=0))
    assert notes[0].first_lap and notes[0].text.startswith("Lap 1 (first lap) · "), notes[0].text
    assert not notes[7].first_lap and "(first lap)" not in notes[7].text, notes[7].text
    print("test_the_first_lap_is_tagged OK")


def test_a_half_slow_session_says_its_median_is_not_a_racing_lap():
    """Half the laps 2 s slower (a neutralised session): IQR 2 s, over twice the 0.5 s floor."""
    planted = {i: ([0.0] * 4, 2.0) for i in range(10, 19)} | {19: ([0.0] * 4, 6.0)}
    times, mx = _grid(planted, noise=0.0)
    (note,) = story.slow_lap_notes(times, mx, best_lap=0)
    assert note.lap_id == 19 and "Your laps are not typical in this session" in note.rule, note.rule
    assert "the median is not a racing lap here" in note.rule, note.rule
    print("test_a_half_slow_session_says_its_median_is_not_a_racing_lap OK")


def test_best_lap_ties_on_the_printed_value_at_the_lap_tables_edge():
    """Level with the best is the Laps table's rule: the PRINTED gap under 0.07 s. 0.069 ties,
    0.070 does not — and the copy of its `printed_ms` here is its to the digit, exact binary
    half-millisecond values included (a half-up / half-even divergence would show there)."""
    from studio import lap_table
    times = {0: 45.0, 1: 45.069, 2: 45.070, 3: 45.5, 4: 46.0}
    m = story.session_moments(times, None, best_lap=0).best_lap
    assert m is not None and m.lap_ids == (0,) and [t.lap_ids for t in m.tie] == [(1,)], m
    assert m.text == ("Best lap: Lap 1 · 0:45.000 · Lap 2 is level (0.069 s, under the 0.07 s "
                      "timing precision)"), m.text
    rng = np.random.default_rng(3)
    exact = [45.0625, 45.1875, 45.3125, 45.4375, 45.5625, 45.6875, 45.8125, 45.9375, 59.9375,
             61.0625, 75.4375]
    for t in [45.0004, 45.0016, 59.9996, 60.0004, *exact, *rng.uniform(30, 130, 2000)]:
        assert story.printed_ms(float(t)) == lap_table.printed_ms(float(t)), t
    print("test_best_lap_ties_on_the_printed_value_at_the_lap_tables_edge OK")


def _near_tie_jitter(lap_j: float, draws: int = 200, seed: int = 20261115):
    """How often the TRUE best (lap 0) leaves the best lap's tie list when every lap time is
    jittered by ±`lap_j`: a planted field with lap 1 0.001 s behind it."""
    times = {0: 44.963, 1: 44.964} | {i: 45.2 + 0.05 * i for i in range(2, 14)}
    rng = np.random.default_rng(seed)
    lost = 0
    for _ in range(draws):
        tj = {i: t + rng.uniform(-lap_j, lap_j) for i, t in times.items()}
        best = min(tj, key=lambda i: (story.printed_ms(tj[i]), i))
        m = story.session_moments(tj, None, best).best_lap
        lost += 0 not in {*m.lap_ids, *(t.lap_ids[0] for t in m.tie)}
    return lost


def test_a_near_tie_keeps_the_true_best_under_half_the_floor_and_can_lose_it_at_the_floor():
    assert _near_tie_jitter(story.PB_PRECISION_S / 2) == 0, "lost the true best at ±0.035 s per lap"
    lost = _near_tie_jitter(story.PB_PRECISION_S)
    assert 0 < lost < 60, f"±0.07 s per lap lost the true best in {lost} of 200 draws (33 on this seed)"
    print(f"test_a_near_tie_keeps_the_true_best_... OK (±0.07 per lap: {lost}/200 lost)")


def _five(start: int, med: float) -> dict[int, float]:
    return {start + k: med + d for k, d in enumerate((-0.2, -0.1, 0.0, 0.1, 0.3))}


def test_five_lap_windows_tie_span_and_break():
    """Runs of exactly five clean laps (the gaps are laps that are not clean): 45.069 is level with
    45.000 and named, 45.070 is not. Level windows on consecutive first laps merge into one span;
    more than two spans is no single best; a lap that is not clean breaks a window; fewer than five
    consecutive clean laps is no window at all."""
    times = _five(0, 45.0) | _five(10, 45.069) | _five(20, 45.070)
    m = story.session_moments(times, None, best_lap=0).best_window
    assert m is not None and m.lap_ids == (0, 1, 2, 3, 4), m
    assert [t.lap_ids for t in m.tie] == [(10, 11, 12, 13, 14)], m.tie
    assert m.text == ("Best 5-lap run: Laps 1–5 · median 0:45.000 · also level (within 0.07 s): "
                      "Laps 11–15"), m.text
    many = _five(0, 45.0) | _five(10, 45.01) | _five(20, 45.02) | _five(30, 45.03)
    m = story.session_moments(many, None, best_lap=0).best_window
    assert m.lap_ids == () and len(m.tie) == 4, m
    assert m.text == "No single best 5-lap run: 4 stretches within 0.07 s", m.text
    flat = {i: 45.0 + 0.001 * i for i in range(10)}
    m = story.session_moments(flat, None, best_lap=0).best_window
    assert [t.lap_ids for t in m.tie] == [tuple(range(10))], m.tie
    assert m.text.endswith("· any 5 consecutive laps in Laps 1–10 are level (within 0.07 s)"), m.text
    holed = {i: 45.0 + (0.5 if i < 4 else 0.0) for i in range(10) if i != 4}
    m = story.session_moments(holed, None, best_lap=5).best_window
    assert m.lap_ids == (5, 6, 7, 8, 9) and m.tie == (), m
    four = {i: 45.0 + 0.01 * i for i in (0, 1, 2, 3, 5, 6, 7, 8)}
    assert story.session_moments(four, None, best_lap=0).best_window is None
    print("test_five_lap_windows_tie_span_and_break OK")


def test_timing_trust_one_rule():
    """The grid keeps its ▼ under a provisional line or an estimated clock, so notes exist and say
    so; the Laps table blanks Δbest and withholds the ★ under a provisional line, so the note has
    no gap and there is no best-lap moment."""
    times, mx = _grid({4: ([0.0, 1.5, 0.0, 0.0], 2.0)})
    (prov,) = story.slow_lap_notes(times, mx, 0, provisional=True)
    assert prov.caveat == "provisional" and prov.gap_to_best_s is None and prov.category == "one"
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


def test_the_mark_is_the_grids():
    from studio import theme
    assert story.BEHIND_MARK == theme.DELTA_BEHIND_ARROW
    print("test_the_mark_is_the_grids OK")


# --------------------------------------------------------------------------- the shipped demo
_DEMO: dict = {}


def _demo():
    """The shipped demo — `make_demo.make`, the recording `--demo` opens, telemetry and habit
    included, under the placeholder picture — through the real loader, built once per process (held
    by the module so its temp folder outlives every caller)."""
    if not _DEMO:
        from studio.dev import make_demo as md
        from studio.session import Session
        tmp = tempfile.TemporaryDirectory(prefix="story_demo_")
        path = os.path.join(tmp.name, "pacer-synthetic-demo.mp4")
        md.make(path, render=False)
        _DEMO.update(tmp=tmp, session=Session.load([path]))
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


DEMO_NOTE = ("Lap 6 · 5.50 s over your typical lap · C5 +2.84 s of it, over your typical pass (▼); "
             "also C6 ▼ +0.82 s")
DEMO_MOMENTS = (
    "Best lap: Lap 13 · 0:44.950",
    "Biggest single-corner loss: Lap 6 · C5 +2.84 s over your typical pass (▼)",
    "Best 5-lap run: Laps 9–13 · median 0:45.182 · any 5 consecutive laps in Laps 8–14 are level "
    "(within 0.07 s)")


def test_demo_story():
    """The shipped demo's one slow lap (internal 5) and its three moments, verbatim."""
    s = _demo()
    (note,) = s.slow_lap_notes()
    assert note.text == DEMO_NOTE, note.text
    assert (note.lap_id, note.category, note.gap_to_best_s) == (5, "one", 6.178), note
    m = s.session_moments()
    got = (m.best_lap.text, m.biggest_loss.text, m.best_window.text)
    assert got == DEMO_MOMENTS, got
    assert m.best_lap.tie == () and m.best_window.lap_ids == (8, 9, 10, 11, 12), m
    print("test_demo_story OK")


def test_demo_dropout_and_excluded_laps_get_no_note():
    """On the real accessors: the slow lap made a ⚠ dropout lap, then a ⊘ excluded one, is no row
    of the grid and gets no note — and is in no moment."""
    s = _demo()
    assert [n.lap_id for n in s.slow_lap_notes()] == [5]
    orig_drop, orig_valid = s.lap_has_dropout, s.valid_lap_ids
    try:
        s.lap_has_dropout = lambda i: i == 5 or orig_drop(i)
        # Lap 5 gone moves the median: lap 0 (the first lap) now reads slow, and that is right.
        assert 5 not in [n.lap_id for n in s.slow_lap_notes()], s.slow_lap_notes()
        # Lap 5 held the demo's only ▼ cells: without it there is no loss to name at all.
        assert s.session_moments().biggest_loss is None, s.session_moments().biggest_loss
        del s.lap_has_dropout
        s.valid_lap_ids = lambda: [i for i in orig_valid() if i != 5]
        assert 5 not in [n.lap_id for n in s.slow_lap_notes()], s.slow_lap_notes()
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


def test_demo_moments_are_stable_under_timing_jitter():
    s = _demo()
    base, true_best = s.session_moments(), s.best_lap_id()
    times = {i: s.lap_time(i) for i in s.consistency_lap_ids()}
    mx = s.corner_matrix()
    rng = np.random.default_rng(20261115)
    for _ in range(200):
        tj = {i: t + rng.uniform(-0.07, 0.07) for i, t in times.items()}
        cj = [[None if v is None else v + rng.uniform(-0.035, 0.035) for v in row]
              for row in mx.cells]
        best = min(tj, key=lambda i: (story.printed_ms(tj[i]), i))
        m = story.session_moments(tj, stats.corner_matrix(mx.lap_ids, mx.cids, cj, mx.resolved),
                                  best)
        assert (m.biggest_loss.lap_ids, m.biggest_loss.cid) == \
            (base.biggest_loss.lap_ids, base.biggest_loss.cid), m.biggest_loss
        assert true_best in {*m.best_lap.lap_ids, *(t.lap_ids[0] for t in m.best_lap.tie)}
    print("test_demo_moments_are_stable_under_timing_jitter OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} STORY TESTS PASSED")
