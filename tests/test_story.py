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


def test_a_slow_lap_after_a_left_out_lap_is_tagged():
    """QA W4 (#567): the lap after a pit stop read "spread", true and useless. A slow lap whose
    previous crossing is no lap of the Laps table (a ⊘ lap, or a crossing too brief to be one) is
    tagged with it, in that table's words; a slow lap after a clean lap is not; the first lap keeps
    its own tag. A LABEL only: the category, excess and corners are the untagged note's."""
    planted = {0: ([0.0, 1.5, 0.0, 0.0], 2.0), 7: ([0.0, 1.5, 0.0, 0.0], 2.0),
               11: ([0.0, 0.0, 0.0, 0.0], 2.0), 15: ([0.0, 0.0, 0.0, 0.0], 2.0)}
    times, mx = _grid(planted)
    # Lap 6 (Lap 7 on screen) was left out, lap 10 was a sliver; lap 14 is a clean lap of the grid.
    left_out = {6: story.AFTER_LEFT_OUT, 10: story.AFTER_TOO_BRIEF}
    untagged = _by_lap(story.slow_lap_notes(times, mx, best_lap=1, first_lap_id=0))
    notes = _by_lap(story.slow_lap_notes(times, mx, best_lap=1, first_lap_id=0, left_out=left_out))
    assert sorted(notes) == [0, 7, 11, 15], sorted(notes)
    n7, n11, n15 = notes[7], notes[11], notes[15]
    assert (n7.after_gap, n7.after_reason) == (6, "left out"), n7
    assert n7.text.startswith("Lap 8 (after Lap 7, left out) · "), n7.text
    assert story.row_text(n7).startswith("After a left-out lap · "), story.row_text(n7)
    assert (n11.after_gap, n11.after_reason) == (10, "too brief"), n11
    assert n11.text.startswith("Lap 12 (after a too-brief crossing) · "), n11.text
    assert story.row_text(n11).startswith("After a too-brief crossing · "), story.row_text(n11)
    assert n11.category == "spread"
    assert (n15.after_gap, n15.after_reason) == (None, None) and "after" not in n15.text.lower()
    assert not story.row_text(n15).startswith("After"), story.row_text(n15)
    # The first lap keeps its own tag even with a left-out crossing planted before it.
    first = _by_lap(story.slow_lap_notes(times, mx, best_lap=1, first_lap_id=0,
                                         left_out={-1: story.AFTER_LEFT_OUT}))[0]
    assert first.first_lap and first.after_gap is None, first
    assert first.text.startswith("Lap 1 (first lap) · "), first.text
    assert story.row_text(first).startswith("First lap · "), story.row_text(first)
    # It excuses nothing and removes nothing: every number is the untagged note's.
    for lap, n in notes.items():
        u = untagged[lap]
        assert (n.excess_s, n.category, n.corners, n.also, n.gap_to_best_s, n.rule) == \
            (u.excess_s, u.category, u.corners, u.also, u.gap_to_best_s, u.rule), (n, u)
        assert n.text.split(" · ", 1)[1] == u.text.split(" · ", 1)[1], (n.text, u.text)
    print("test_a_slow_lap_after_a_left_out_lap_is_tagged OK")


def test_the_rule_names_the_middle_half_as_the_stats_page_does():
    """One name for one number: the margin is "the clean laps' middle half", the σ tile's own word
    (QA W4 EVAL #567); "IQR" is named nowhere in a note."""
    times, mx = _grid({4: ([0.0, 1.5, 0.0, 0.0], 2.0)})
    (note,) = story.slow_lap_notes(times, mx, best_lap=0)
    assert "the larger of the clean laps' middle half (0." in note.rule, note.rule
    assert "IQR" not in note.rule and "IQR" not in note.text, note.rule
    half = {i: 45.0 + (2.0 if i % 2 else 0.0) for i in range(N_LAPS)}
    wide = story.slow_rule_text(*story.slow_threshold(half))
    assert "not typical" in wide and "IQR" not in wide, wide
    print("test_the_rule_names_the_middle_half_as_the_stats_page_does OK")


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
                      "every 5-lap median in Laps 11–15"), m.text
    many = _five(0, 45.0) | _five(10, 45.01) | _five(20, 45.02) | _five(30, 45.03)
    m = story.session_moments(many, None, best_lap=0).best_window
    # QA W4 (#570): the "no single best" row names its stretches (up to three, then "+N more") and
    # keeps the best window's laps for its Jump — it used to name none, with lap_ids ().
    assert m.lap_ids == (0, 1, 2, 3, 4) and len(m.tie) == 4 and story.no_single_best(m), m
    assert m.text == ("No single best 5-lap run: 4 stretches of level 5-lap medians "
                      "(within 0.07 s): Laps 1–5, 11–15, 21–25, +1 more"), m.text
    three = _five(0, 45.02) | _five(10, 45.0) | _five(20, 45.01)
    m = story.session_moments(three, None, best_lap=10).best_window
    assert story.no_single_best(m) and m.lap_ids == (10, 11, 12, 13, 14), m
    # The stretch holding the best window is named FIRST: the row's Jump lands on it.
    assert m.text == ("No single best 5-lap run: 3 stretches of level 5-lap medians "
                      "(within 0.07 s): Laps 11–15, 1–5, 21–25"), m.text
    assert story.moment_row(m).lap_id == 10, story.moment_row(m)
    # Seven stretches, the best window in the sixth: named in session order alone, the Jump went
    # to Lap 51, a stretch the text left out ("Laps 1–5, 11–15, 21–25, +4 more").
    seven = {}
    for k in range(7):
        seven |= _five(10 * k, 45.0 if k == 5 else 45.01 + 0.001 * k)
    m = story.session_moments(seven, None, best_lap=50).best_window
    assert story.no_single_best(m) and m.lap_ids == (50, 51, 52, 53, 54), m
    assert m.text.endswith(": Laps 51–55, 1–5, 11–15, +4 more"), m.text
    assert story.moment_row(m).lap_id == 50, story.moment_row(m)
    flat = {i: 45.0 + 0.001 * i for i in range(10)}
    m = story.session_moments(flat, None, best_lap=0).best_window
    assert [t.lap_ids for t in m.tie] == [tuple(range(10))], m.tie
    assert m.text.endswith("· every 5-lap median in Laps 1–10 is level (within 0.07 s)"), m.text
    holed = {i: 45.0 + (0.5 if i < 4 else 0.0) for i in range(10) if i != 4}
    m = story.session_moments(holed, None, best_lap=5).best_window
    assert m.lap_ids == (5, 6, 7, 8, 9) and m.tie == (), m
    four = {i: 45.0 + 0.01 * i for i in (0, 1, 2, 3, 5, 6, 7, 8)}
    assert story.session_moments(four, None, best_lap=0).best_window is None
    print("test_five_lap_windows_tie_span_and_break OK")


def test_a_moment_row_joins_back_into_the_moments_text():
    """`moment_row` builds a surface's row from the Moment's fields, never by slicing `text`: its
    label and value join with ": " into `text` exactly, across every wording the builders have —
    a lone best and a tied one, a corner loss with ≈ ties, a run with its own span, an "also level"
    span that SHARES laps with the best window (Laps 1–6 holds laps 5 and 6 of Laps 5–9, so only
    holding the whole window marks the own span), and no single best, whose Jump is its span's."""
    many = _five(0, 45.0) | _five(10, 45.01) | _five(20, 45.02) | _five(30, 45.03)
    shared = {0: 45.06, 1: 45.06, 2: 45.3, 3: 45.6, 4: 45.0, 5: 45.0, 6: 45.6, 7: 45.6, 8: 45.03}
    _, mx = _grid({4: ([0.0, 1.5, 0.0, 0.0], 2.0), 7: ([0.0, 1.48, 0.0, 0.0], 2.0)})
    fields = [
        ({0: 45.0, 1: 45.069, 2: 45.070, 3: 45.5, 4: 46.0}, None, 0),
        ({0: 45.0, 1: 45.03, 2: 45.05, 3: 45.5, 4: 46.0, 5: 45.2}, None, 0),
        (_five(0, 45.0) | _five(10, 45.069), None, 0),
        ({i: 45.0 + 0.001 * i for i in range(10)}, None, 0),
        (shared, None, 4),
        (many, None, 0),
        ({i: 45.0 + 0.1 * (i % 3) for i in range(20)}, mx, 0),
    ]
    rows = 0
    for times, matrix, best in fields:
        m = story.session_moments(times, matrix, best)
        for moment in (m.best_lap, m.biggest_loss, m.best_window):
            if moment is None:
                continue
            row = story.moment_row(moment)
            assert f"{row.label}: {row.value}" == moment.text, (row, moment.text)
            if not story.no_single_best(moment):
                assert row.lap_id == moment.lap_ids[0], row
            assert row.cid == moment.cid, row
            rows += 1
    w = story.session_moments(shared, None, 4).best_window
    assert w.lap_ids == (4, 5, 6, 7, 8) and w.text == (
        "Best 5-lap run: Laps 5–9 · median 0:45.030 · also level (within 0.07 s): every 5-lap "
        "median in Laps 1–6"), w.text
    loss = story.session_moments(*fields[-1][:2], 0).biggest_loss
    assert loss is not None and loss.tie and story.moment_row(loss).cid == loss.cid, loss
    none = story.moment_row(story.session_moments(many, None, 0).best_window)
    assert (none.label, none.lap_id) == ("No single best 5-lap run", 0), none
    # The Jump lands on the first lap of the SPAN holding the best window, not the window's own.
    wide = _five(0, 45.03) | {i: 45.0 + 0.001 * (16 - i) for i in range(10, 17)} | \
        _five(20, 45.02) | _five(30, 45.01)
    w = story.session_moments(wide, None, 0).best_window
    assert story.no_single_best(w) and w.lap_ids[0] > 10, w
    assert story.moment_row(w).lap_id == 10, (story.moment_row(w), w.lap_ids)
    print(f"test_a_moment_row_joins_back_into_the_moments_text OK ({rows} rows)")


def test_timing_trust_one_rule():
    """The grid keeps its ▼ under a provisional line or an estimated clock, so notes exist and say
    so; the Laps table blanks Δbest and withholds the ★ under a provisional line, so the note has
    no gap and there is no best-lap moment."""
    times, mx = _grid({4: ([0.0, 1.5, 0.0, 0.0], 2.0)})
    (prov,) = story.slow_lap_notes(times, mx, 0, provisional=True)
    assert prov.caveat == ("provisional",) and prov.gap_to_best_s is None and prov.category == "one"
    (est,) = story.slow_lap_notes(times, mx, 0, clock_estimated=True)
    assert est.caveat == ("estimated",) and est.gap_to_best_s is not None, est
    m = story.session_moments(times, mx, 0, provisional=True)
    assert m.best_lap is None and m.biggest_loss is not None and m.biggest_loss.caveat == ("provisional",)
    print("test_timing_trust_one_rule OK")


def test_an_estimated_clock_widens_every_tie_to_its_bound_and_says_so():
    """Under an estimated clock a lap can read up to ~0.15 s off, so a moment may not call two
    figures level at the 0.07 s timing precision: the best lap's ties, the 5-lap medians' level
    rule and the biggest loss's ≈ list all take the Lap race's 0.15 s bound, and the best lap's
    clause names that bound. Provisional AND estimated carries both states."""
    from studio import lap_race
    assert story.tie_floor(("estimated",)) == lap_race.ESTIMATED_CLOCK_TIE_S == 0.15
    times = {0: 45.0, 1: 45.1, 2: 45.149, 3: 45.15, 4: 46.0}
    trusted = story.session_moments(times, None, 0).best_lap
    assert trusted.tie == (), trusted
    est = story.session_moments(times, None, 0, clock_estimated=True).best_lap
    assert [t.lap_ids for t in est.tie] == [(1,), (2,)] and est.caveat == ("estimated",), est
    assert est.text == ("Best lap: Lap 1 · 0:45.000 · Laps 2, 3 are level (0.100–0.149 s, under "
                        "this clock's 0.15 s bound)"), est.text
    runs = _five(0, 45.0) | _five(10, 45.12)
    assert story.session_moments(runs, None, 0).best_window.tie == ()
    w = story.session_moments(runs, None, 0, clock_estimated=True).best_window
    assert w.text.endswith("also level (within 0.15 s): every 5-lap median in Laps 11–15"), w.text
    _, mx = _grid({4: ([0.0, 1.5, 0.0, 0.0], 2.0), 7: ([0.0, 1.4, 0.0, 0.0], 2.0)}, noise=0.0)
    times = {i: 45.0 for i in mx.lap_ids}
    assert story.session_moments(times, mx, 0).biggest_loss.tie == ()
    assert story.session_moments(times, mx, 0, clock_estimated=True).biggest_loss.tie
    both = story.session_moments(times, mx, 0, provisional=True, clock_estimated=True)
    assert both.best_lap is None and both.biggest_loss.caveat == ("provisional", "estimated")
    assert story.caveat_for(False, False) == ()
    print("test_an_estimated_clock_widens_every_tie_to_its_bound_and_says_so OK")


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
    "Best 5-lap run: Laps 9–13 · median 0:45.182 · every 5-lap median in Laps 8–14 is level "
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


def _printed_minus_printed(a: float, b: float) -> float:
    """What a reader computes from the two figures a surface prints beside the gap."""
    return round(round(a, 2) - round(b, 2), 2)


def test_one_pass_at_one_corner_prints_one_number_everywhere():
    """QA w5: lap 6 at C6 on the demo is 3.6540 against a typical 2.8289. The slow-lap note (Laps ▼
    tooltip, report SLOW LAPS) printed "+0.82 s" and the σ hover and the report's CORNERS note
    "+0.83 s". Every surface now prints the PRINTED pass minus the PRINTED typical
    (`stats.pass_vs_typical_s`), the number a reader checks against "[▼ 3.65] … your typical C6
    (2.83 s)" on the same line: +0.82 s, everywhere."""
    # The arithmetic, on the demo's own pass: both other orders, for the record.
    assert stats.pass_vs_typical_s(3.654, 2.8289) == 0.82
    assert round(3.654 - 2.8289, 2) == 0.83                 # subtract-then-round (#586 v1)
    assert stats.fmt_pass_vs_typical(stats.pass_vs_typical_s(3.654, 2.8289)) == "+0.82 s"
    assert stats.pass_vs_typical_s(None, 2.8) is None and stats.pass_vs_typical_s(3.0, None) is None
    # The demo, on the real accessors: the note, the σ sentence, the grid's gap — one number, and
    # it is the printed pass minus the printed median.
    s = _demo()
    (note,) = s.slow_lap_notes()
    mx = s.corner_matrix()
    r = mx.lap_ids.index(note.lap_id)
    for cl in note.corners + note.also:
        c = mx.cids.index(cl.cid)
        want = _printed_minus_printed(mx.cells[r][c], mx.medians[c])
        assert cl.loss_s == want, (cl, want)
        assert f"C{cl.cid} {stats.fmt_pass_vs_typical(want)}" in note.text.replace("▼ ", ""), \
            (want, note.text)
        assert stats.pass_vs_typical_s(mx.cells[r][c], mx.medians[c]) == want
    (c6,) = [c for c in note.corners + note.also if c.cid == 6]
    assert c6.loss_s == 0.82 and "C6 ▼ +0.82 s" in note.text, note.text
    for row in s.corner_report():
        sentence = stats.corner_one_pass_note(row)
        if not sentence:
            continue
        cell = mx.cells[mx.lap_ids.index(row.worst_pass_lap)][mx.cids.index(row.cid)]
        assert row.median_s == mx.medians[mx.cids.index(row.cid)]
        want = _printed_minus_printed(cell, row.median_s)
        lap = f"L{row.worst_pass_lap + 1}: "
        assert f"{lap}{stats.fmt_pass_vs_typical(want)} against the median" in sentence, \
            (row.cid, want, sentence)
    (row6,) = [x for x in s.corner_report() if x.cid == 6]
    assert "L6: +0.82 s against the median" in stats.corner_one_pass_note(row6)
    m = s.session_moments()
    assert m.biggest_loss.cid == 5 and m.biggest_loss.value == _printed_minus_printed(
        mx.cells[r][mx.cids.index(5)], mx.medians[mx.cids.index(5)])
    # CORNERS Med loss: the printed Median minus the printed Best beside it (C4: 3.08 − 3.02), and
    # the note's sum is the sum of those cells.
    rows = s.corner_report()
    for x in rows:
        assert stats.printed_med_loss(x) == _printed_minus_printed(x.median_s, x.best_s), x.cid
    (c4,) = [x for x in rows if x.cid == 4]
    assert stats.printed_med_loss(c4) == 0.06, stats.printed_med_loss(c4)
    total = sum(stats.printed_med_loss(x) for x in rows)
    assert f"Summed, that is {total:.2f} s." in stats.corner_count_note(rows)[0]
    print("test_one_pass_at_one_corner_prints_one_number_everywhere OK")


def test_a_marked_pass_never_prints_below_its_own_step():
    """The ▼, the heat shade, the note and the σ sentence take ONE gap. With the median unrounded
    in the ▼ rule, a median sitting on a half-hundredth could mark a cell whose printed gap reads a
    hundredth under the printed step, or (here) refuse one whose printed gap reaches it: 4.1354
    prints 4.14, a median of 3.8449999999999998 (the double the sweep produced, just under 3.845)
    prints 3.84, the step 0.30 — the printed gap is +0.30, a ▼, where the old rule's 4.14 − 3.845
    came to 0.29499… and refused it. One of the 3 sweep cells (of 144,400) whose ▼ moved."""
    val, med, step = 4.1354, 3.8449999999999998, 0.30
    assert f"{val:.2f}" == "4.14" and f"{med:.2f}" == "3.84"
    assert stats.pass_vs_typical_s(val, med) == 0.30 == _printed_minus_printed(val, med)
    assert round(round(val, 2) - med, 2) == 0.29             # the old ▼ rule's gap
    mx = stats.CornerMatrix(lap_ids=[0], cids=[1], cells=[[val]], resolved=[[True]],
                            medians=[med], scales=[step], n_resolved=[9])
    assert mx.is_behind(0, 0) and mx.heat(0, 0) == 2, (mx.is_behind(0, 0), mx.heat(0, 0))
    # The σ sentence prints the same gap from the pass's own time and the row's median.
    from types import SimpleNamespace
    row = SimpleNamespace(one_pass_sigma=True, worst_pass_lap=0, median_s=med,
                          worst_pass_vs_median_s=val - med, sigma_without_worst_s=0.05)
    assert "L1: +0.30 s against the median" in stats.corner_one_pass_note(row), \
        stats.corner_one_pass_note(row)
    print("test_a_marked_pass_never_prints_below_its_own_step OK")


def test_on_hundredth_edges_every_printed_loss_is_its_printed_pass_minus_its_printed_typical():
    """A seeded sweep of sessions whose every corner time sits on (or a hair off) a half-hundredth,
    where any two rounding orders disagree most (the review's 4,000-session generator, 400 here; the
    full run: 9,096 printed losses, 0 off, 0 below their step). For every note: each printed
    corner loss IS the printed pass minus the printed typical, never under its own printed ▼ step;
    "one"/"two" never name more than the printed excess; and every cell's ▼ is its deepest shade."""
    import random
    rnd = random.Random(1)

    def edge(x):
        return round(x * 100) / 100 + 0.005 + rnd.choice((-1, 1)) * rnd.choice((1e-9, 1e-6, 4e-4, 0.0))
    checked = 0
    for _ in range(400):
        n_laps, n_c = 8, rnd.randint(3, 6)
        base = [rnd.uniform(2.0, 4.0) for _ in range(n_c)]
        cells = [[edge(b + rnd.gauss(0, 0.05)) for b in base] for _ in range(n_laps)]
        slow = rnd.randrange(n_laps)
        for c in rnd.sample(range(n_c), rnd.randint(1, n_c)):
            cells[slow][c] = edge(cells[slow][c] + rnd.uniform(0.05, 1.5))
        mx = stats.corner_matrix(list(range(n_laps)), list(range(1, n_c + 1)), cells,
                                 [[True] * n_c for _ in range(n_laps)])
        times = {lap: 45.0 + rnd.gauss(0, 0.15) for lap in range(n_laps)}
        times[slow] = 45.0 + sum(cells[slow]) - sum(base) + rnd.uniform(0.6, 3.0)
        for note in story.slow_lap_notes(times, mx, best_lap=None):
            r = mx.lap_ids.index(note.lap_id)
            for cl in note.corners + note.also:
                c = mx.cids.index(cl.cid)
                want = _printed_minus_printed(mx.cells[r][c], mx.medians[c])
                assert cl.loss_s == want, (cl, want, note.text)
                assert want >= round(mx.scales[c], 2), (cl, mx.scales[c], note.text)
                checked += 1
            named = [cl.loss_s for cl in note.corners]
            if note.category == "one":
                assert named[0] <= note.excess_s, note.text
            if note.category == "two":
                assert named[0] + named[1] <= note.excess_s, note.text
        for r in range(n_laps):
            for c in range(n_c):
                assert mx.is_behind(r, c) == (mx.heat(r, c) == 2), (r, c)
    assert checked > 800, checked
    print(f"test_on_hundredth_edges_every_printed_loss_is_its_printed_pass_minus_its_printed_typical "
          f"OK ({checked} printed losses)")


def test_demo_dropout_and_excluded_laps_get_no_note():
    """On the real accessors: the slow lap made a ⚠ dropout lap, then a ⊘ excluded one, is no row
    of the grid and gets no note — and is in no moment."""
    s = _demo()
    assert [n.lap_id for n in s.slow_lap_notes()] == [5]
    orig_drop, orig_valid = s.lap_has_dropout, s.valid_lap_ids
    try:
        s.lap_has_dropout = lambda i: i == 5 or orig_drop(i)
        # A patched accessor is no re-segmentation, so the memo (keyed on the corner model's
        # generation and the valid list's identity) cannot see it: drop it, as a re-segment would.
        s._story_memo = None
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
        s._story_memo = None
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


def test_demo_a_slow_lap_after_an_excluded_lap_says_so():
    """On the real accessors: the lap before the demo's slow lap made a ⊘ excluded lap — gone
    from `valid_lap_ids`, in `excluded_lap_ids` — and the slow lap's note says so, in the Laps
    table's words, its numbers unchanged. Back to clean, the tag goes."""
    s = _demo()
    (plain,) = s.slow_lap_notes()
    assert plain.after_gap is None and plain.text == DEMO_NOTE, plain.text
    orig_valid, orig_excl = s.valid_lap_ids, s.excluded_lap_ids
    try:
        s.valid_lap_ids = lambda: [i for i in orig_valid() if i != 4]
        s.excluded_lap_ids = lambda: sorted({*orig_excl(), 4})
        s._story_memo = None
        note = {n.lap_id: n for n in s.slow_lap_notes()}[5]
        assert (note.after_gap, note.after_reason) == (4, "left out"), note
        assert note.category == "one" and note.corners[0].cid == 5, note
        assert note.text.startswith("Lap 6 (after Lap 5, left out) · "), note.text
    finally:
        for name in ("valid_lap_ids", "excluded_lap_ids"):
            s.__dict__.pop(name, None)
        s._story_memo = None
    assert [n.text for n in s.slow_lap_notes()] == [DEMO_NOTE]
    print("test_demo_a_slow_lap_after_an_excluded_lap_says_so OK")


def test_demo_a_dropout_lap_before_a_slow_lap_does_not_tag_it():
    """A ⚠ dropout lap is a lap the Laps table counts, and a GPS dropout does not slow the kart on
    the next lap: the lap before the demo's slow lap made a dropout lap (on the real accessors) is
    no gap, so the slow lap's note carries no tag — only the median moved."""
    s = _demo()
    orig_drop = s.lap_has_dropout
    try:
        s.lap_has_dropout = lambda i: i == 4 or orig_drop(i)
        s._story_memo = None
        assert 4 in s.valid_lap_ids() and 4 not in s.consistency_lap_ids()
        note = {n.lap_id: n for n in s.slow_lap_notes()}[5]
        assert (note.after_gap, note.after_reason) == (None, None), note
        assert note.text.startswith("Lap 6 · ") and "after" not in note.text, note.text
        assert note.category == "one" and note.corners[0].cid == 5, note
    finally:
        s.__dict__.pop("lap_has_dropout", None)
        s._story_memo = None
    print("test_demo_a_dropout_lap_before_a_slow_lap_does_not_tag_it OK")


def test_demo_slivers_before_the_first_timed_lap_leave_it_the_first_lap():
    """Crossings too brief to be laps before the first TIMED lap do not make it "(after a
    too-brief crossing)": it is the session's first lap, and that tag wins. Planted on the real
    accessors: laps 0-4 out of both lap lists (slivers), so lap 5 — the demo's slow lap — is the
    first timed lap."""
    s = _demo()
    orig_valid, orig_excl = s.valid_lap_ids, s.excluded_lap_ids
    try:
        s.valid_lap_ids = lambda: [i for i in orig_valid() if i > 4]
        s.excluded_lap_ids = lambda: [i for i in orig_excl() if i > 4]
        s._story_memo = None
        note = {n.lap_id: n for n in s.slow_lap_notes()}[5]
        assert note.first_lap and note.after_gap is None, note
        assert note.text.startswith("Lap 6 (first lap) · "), note.text
        assert story.row_text(note).startswith("First lap · "), story.row_text(note)
    finally:
        for name in ("valid_lap_ids", "excluded_lap_ids"):
            s.__dict__.pop(name, None)
        s._story_memo = None
    assert [n.text for n in s.slow_lap_notes()] == [DEMO_NOTE]
    print("test_demo_slivers_before_the_first_timed_lap_leave_it_the_first_lap OK")


def test_demo_memo_hit_is_constant_time_and_misses_on_every_input_change():
    """QA W4 (#568): a memo HIT cost 1.2-2.2 ms because its key rebuilt every clean lap's corner
    list. A hit now reads no lap at all (pinned by counting `lap_corner_stats` calls, so it cannot
    pass on a fast machine alone) and takes under 0.1 ms; and the memo still misses on a sector
    edit, its undo, a timing-trust flip and a reference change's stats invalidation."""
    import time

    from studio.session import Seg
    s = _demo()
    first = s.session_moments()
    assert s.session_moments() is first
    calls = [0]
    orig = s.corners.lap_corner_stats

    def counting(i):
        calls[0] += 1
        return orig(i)
    s.corners.lap_corner_stats = counting
    ts = []
    try:
        for _ in range(200):
            a = time.perf_counter()
            got = s.session_moments()
            ts.append(time.perf_counter() - a)
            assert got is first
    finally:
        del s.corners.lap_corner_stats
    hit_ms = float(np.median(ts)) * 1000
    assert calls[0] == 0, f"a memo hit read {calls[0]} laps' corner stats"
    assert hit_ms < 0.1, f"memo hit {hit_ms:.3f} ms"
    start, sectors = s.start_line, list(s.sector_lines)
    texts = [n.text for n in s.slow_lap_notes()]
    st = start
    extra = Seg(st.x1 + 40.0, st.y1 + 40.0, st.x2 + 40.0, st.y2 + 40.0)
    s.set_timing_lines(start, sectors + [extra], user_confirm=False)     # a sector edit
    edited = s.session_moments()
    assert edited is not first, "a sector edit and the story memo hit"
    s.set_timing_lines(start, sectors, user_confirm=False)               # its undo
    undone = s.session_moments()
    assert undone is not edited and [n.text for n in s.slow_lap_notes()] == texts
    saved = (s.track_name, s._timing_user_confirmed)
    assert s.timing_verified and undone.best_lap is not None
    try:
        s.track_name, s._timing_user_confirmed = None, False             # a trust flip
        assert not s.timing_verified
        flipped = s.session_moments()
        assert flipped is not undone and flipped.best_lap is None, flipped
    finally:
        s.track_name, s._timing_user_confirmed = saved
    again = s.session_moments()
    assert again is not undone and again.best_lap is not None
    s.corners.invalidate_stats()                                         # a reference change
    assert s.session_moments() is not again
    print(f"test_demo_memo_hit_is_constant_time_and_misses_on_every_input_change OK "
          f"(hit {hit_ms * 1000:.1f} µs)")


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
