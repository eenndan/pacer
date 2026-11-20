"""The focus list (studio.focus): the training loop, and the gate that decides whether it may
speak at all.

What is asserted here, on inputs whose answer is known by construction:

  * PERSISTENCE, to the discipline library.py / session_record.py set — round-trip, the
    MAX_ITEMS cap, an empty list REMOVING its row, a corrupt file self-healing to an empty store,
    a NEWER on-disk schema read best-effort and BACKED UP before any overwrite, one malformed item
    dropped rather than the file, and the `_app_support_dir` seam so the suite never touches the
    real store;
  * the MEASUREMENT: a window is a FRACTION of the lap odometer, so the same stretch of track is
    compared across two sessions whose corner partitions differ — the failure this design exists
    to prevent, sized on real data in studio/focus.py (C1's window grew 173.4 m → 179.5 m between
    the two working-set recordings, worth +0.170 s of "you got slower" on a corner the driver took 0.029 s quicker over the same stretch);
    and a stored window with ONE edge on today's corner boundary is timed on every clean lap, as
    the demo against habit-free sessions of its circuit shows (it read "0 clean laps" before);
  * the GATE: no session record on either side, records that disagree, a provisional start line,
    ESTIMATED timing, mismatched lap lengths and too few clean laps each REFUSE a verdict — and a
    refused verdict carries `delta is None`, so no surface can print a number the evidence does not
    support. This is PR #258's rule ("silent when either side has no record") said out loud;
  * the SPREAD test: a change smaller than half the wider of the two sessions' interquartile
    spreads is "no change you can act on", using coaching.SPREAD_MARGIN and coaching's own IQR
    statistic rather than a second notion of significance; and so is one under the timing step
    (library.PB_PRECISION_S), whatever the spread, so a verdict reads the same in both directions;
    and, since cool/focus-cross-session-floor, so is one under the cross-session step
    (focus.CROSS_SESSION_LEVEL_S, 2√2 × the σ of one session's corner bias that
    studio/dev/probes/focus_cross_session.py measures), which is the wider of the two;
  * the LAYOUT: a stored corner whose window today lies on a corner turning the other way (the
    demo's circuit driven as its mirror image) is not compared, and the headline says the layout
    looks different; nor is any corner on the same tarmac driven the other way round, whose
    laps run the other way (half its corners line up with a stored hand); no same-layout session of
    the demo's circuit is refused that way, and a corner near the detection limit that today's
    detector drops is said to be undetected, not called another layout; a reprofiled corner that
    keeps its hand is compared — what the check cannot see — and every corner away from it reads as
    on the unchanged layout;
  * the PANEL: the block is dormant until the app hands it a report, it names the corner its Add
    button would promote, the two gestures are signals (the window owns the store), and it yields
    its height to the ranked list exactly as the theme block does;
  * the WINDOW: every write to the session-record store — the form's save and delete, from
    File ▸ Session record… and from the Library, and the Library's forget, clear and restore —
    re-reads the verdict on the real Coaching page, so writing the two records a refusal asks for
    lifts it without re-opening the recording (board review UX-5).

Run:  QT_QPA_PLATFORM=offscreen python tests/test_focus_list.py
"""
import contextlib
import json
import os
import sys
import tempfile

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")      # the real window's player pane, inert
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _footage  # noqa: E402
from _qtapp import themed_app  # noqa: E402

from studio import coaching as K  # noqa: E402
from studio import focus as F  # noqa: E402
from studio import session_record  # noqa: E402

_APP = themed_app()

# One throwaway app-support dir for the whole module — the seam every store test patches, so the
# suite can never read or write the developer's own focus list.
_TMP = tempfile.TemporaryDirectory()
F._app_support_dir = lambda: _TMP.name


def _item(cid=4, median=4.50, iqr=0.12, n=38, fp="GX0060", date="2026-05-23",
          enter=0.15, exit_=0.20, verified=True, degraded=False, total=1059.2) -> F.FocusItem:
    return F.FocusItem(cid=cid, direction=1, enter_frac=enter, exit_frac=exit_, median_s=median,
                       iqr_s=iqr, n_laps=n, time_lost=0.22, reason=K.REASON_BRAKING,
                       reach=K.REACH_REPEAT, fingerprint=fp, date=date, lap_total=total,
                       verified=verified, degraded=degraded)


def _now(fp="GX0062", track="Daytona MK", total=1066.2, verified=True, degraded=False) -> dict:
    return {"list_track": track, "track": track, "fingerprint": fp, "date": "2026-05-24",
            "lap_total": total, "verified": verified, "degraded": degraded}


def _record(conditions="dry", air=18.0, track_t=24.0, tyres="MG Yellow") -> dict:
    return {**session_record.blank_record(), "conditions": conditions, "air_temp_c": air,
            "track_temp_c": track_t, "tyre_set": tyres}


def _records(*pairs) -> dict:
    store = session_record.empty_store()
    for fp, rec in pairs:
        session_record.put(store, fp, rec)
    return store


def _sample(median, iqr=0.12, n=65) -> F.CornerSample:
    return F.CornerSample(median=median, iqr=iqr, n_laps=n)


# --------------------------------------------------------------------------------- measurement
def test_a_window_is_a_lap_fraction_not_a_corner_id():
    """The whole reason an item stores a WINDOW: two sessions partition the same track into
    slightly different corners, and comparing each session's own window compares two different
    stretches of tarmac. Built so the answer is exact: a lap driven at a constant 20 m/s, once over
    1000 m and once over 1010 m. The same FRACTION is the same time on both (to the lap's own 0.5 %
    stretch); the second session's own 10 %-longer corner is 10 % more time, which is the artifact.
    """
    def lap(total, v=20.0, n=2001):
        dist = np.linspace(0.0, total, n)
        return dist, dist / v

    a, b = lap(1000.0), lap(1010.0)
    same = F.window_times([(0.20, 0.25)], [a, b])[0]
    assert abs(same[0] - 2.5) < 1e-6 and abs(same[1] - 2.525) < 1e-6, same
    # …and the "own window" comparison, where session B's detector drew the corner 10 % longer.
    own_a = F.window_times([(0.20, 0.25)], [a])[0][0]
    own_b = F.window_times([(0.20, 0.255)], [b])[0][0]
    assert own_b - own_a > 0.27, (own_a, own_b)
    assert abs((same[1] - same[0]) - 0.025) < 1e-6, "the fraction window only carries the 0.5 %"
    print(f"ok window: same fraction {same[0]:.3f}/{same[1]:.3f} s, own windows "
          f"{own_a:.3f}/{own_b:.3f} s (+{own_b - own_a:.3f} s of pure partition)")


def test_a_sample_is_the_same_spread_statistic_coaching_uses():
    times = [4.0, 4.1, 4.2, 4.3, 4.4, float("nan")]
    s = F.sample_window(times)
    assert s.n_laps == 5 and abs(s.median - 4.2) < 1e-9
    q25, q75 = np.percentile([4.0, 4.1, 4.2, 4.3, 4.4], [25, 75])
    assert abs(s.iqr - (q75 - q25)) < 1e-12, s.iqr
    assert F.sample_window([]) is None and F.sample_window([float("nan")]) is None
    print(f"ok sample: median {s.median:.2f}, IQR {s.iqr:.2f} over {s.n_laps} laps")


# --------------------------------------------------------------------------------- the gate
def test_no_session_record_on_either_side_refuses_the_verdict_and_carries_no_number():
    """PR #258's rule, said out loud. The corners moved by a whole second here — the refusal is not
    a lack of signal, it is a lack of grounds — and the model carries no delta for a surface to
    print."""
    items = [_item()]
    report = F.verdict(items, _now(), [_sample(5.50)], session_record.empty_store())
    o = report.outcomes[0]
    assert o.kind == F.OUTCOME_NO_VERDICT and o.blocker == F.BLOCK_NO_RECORD, o
    assert o.delta is None, "a blocked verdict must not carry a number at all"
    assert "23 May" in o.detail and "today" in o.detail, o.detail
    sentence = F.outcome_sentence(o)
    assert "1.0" not in sentence and "s faster" not in sentence, sentence
    assert "no session record" in sentence, sentence
    # one side alone is still not like-for-like
    one_sided = F.verdict(items, _now(), [_sample(5.50)], _records(("GX0060", _record())))
    assert one_sided.outcomes[0].blocker == F.BLOCK_NO_RECORD, one_sided.outcomes[0]
    assert report.n_verdicts == 0
    print(f"ok no-record gate: {sentence}")


def test_records_that_disagree_refuse_the_verdict_and_name_the_difference():
    report = F.verdict([_item()], _now(), [_sample(5.50)],
                       _records(("GX0060", _record()),
                                ("GX0062", _record(conditions="damp", air=9.0, track_t=12.0))))
    o = report.outcomes[0]
    assert o.kind == F.OUTCOME_NO_VERDICT and o.blocker == F.BLOCK_CONDITIONS, o
    assert o.delta is None
    assert "Dry vs Damp" in o.detail and "air" in o.detail, o.detail
    print(f"ok conditions gate: {F.outcome_sentence(o)}")


def test_a_change_inside_the_corners_own_spread_is_not_a_change():
    """The same actionability test the coaching gate applies within a session, on the same
    statistic — a driver cannot aim at 0.09 s inside a band whose middle half is 0.20 s wide.
    (0.09 s, not the 0.061 s this test used to take: that is under the timing step, which says
    "no change" whatever the spread — `test_a_difference_under_the_timing_step_is_no_change…`.)"""
    alike = _records(("GX0060", _record()), ("GX0062", _record(air=19.0, track_t=26.0)))
    inside = F.verdict([_item(median=4.537, iqr=0.19)], _now(),
                       [_sample(4.627, iqr=0.20)], alike).outcomes[0]
    assert inside.kind == F.OUTCOME_UNCHANGED, inside
    assert inside.delta is not None and abs(inside.delta - 0.090) < 1e-6
    assert "inside the corner's own 0.20 s spread" in F.outcome_sentence(inside)
    # …and the same numbers with a narrower spread DO clear the bar.
    outside = F.verdict([_item(median=4.537, iqr=0.05)], _now(),
                        [_sample(4.627, iqr=0.05)], alike).outcomes[0]
    assert outside.kind == F.OUTCOME_SLOWER, outside
    assert abs(outside.delta - 0.090) < 1e-6
    # WHOSE spread: the WIDER of the two sessions', in either order — a change is only as aimable
    # as the noisier side. Both pairs above have near-equal spreads, so on their own they cannot
    # tell "wider" from "narrower": a verdict built on the tight side passed them unchanged.
    for then_iqr, now_iqr in ((0.05, 0.20), (0.20, 0.05)):
        lopsided = F.verdict([_item(median=4.537, iqr=then_iqr)], _now(),
                             [_sample(4.627, iqr=now_iqr)], alike).outcomes[0]
        assert lopsided.kind == F.OUTCOME_UNCHANGED, (
            f"0.090 s is inside half of the noisier session's 0.20 s spread, but with IQRs "
            f"then={then_iqr} / now={now_iqr} it was graded {lopsided.kind!r}")
    # the bar itself is coaching's constant, not a second one invented here
    assert F.SPREAD_MARGIN is K.SPREAD_MARGIN
    print(f"ok spread gate: {F.outcome_sentence(inside)}")


def test_a_real_improvement_is_stated_with_its_number_and_its_sample():
    alike = _records(("GX0060", _record()), ("GX0062", _record()))
    o = F.verdict([_item(median=4.537, iqr=0.129)], _now(),
                  [_sample(4.237, iqr=0.120)], alike).outcomes[0]
    assert o.kind == F.OUTCOME_IMPROVED and o.delta < 0
    text = F.outcome_sentence(o)
    assert "0.30 s faster than 23 May" in text, text
    assert "4.54 → 4.24 s" in text, "a verdict must show both halves, not only the difference"
    assert "38 → 65 laps" in text, "a verdict must carry the sample each median came from"
    print(f"ok improvement: {text}")


def test_the_structural_gates_fire_before_the_conditions_ones():
    """Fixed priority, most fundamental first — an arbitrary odometer origin is not a conditions
    question, and reporting the conditions objection would send the driver to fix the wrong thing.
    Every one of these carries no number."""
    alike = _records(("GX0060", _record()), ("GX0062", _record()))
    cases = [
        (F.BLOCK_TRACK, [_item()], {**_now(), "track": "Somewhere else"}),
        (F.BLOCK_UNVERIFIED, [_item(verified=False)], _now()),
        (F.BLOCK_DEGRADED, [_item()], _now(degraded=True)),
        (F.BLOCK_GEOMETRY, [_item()], _now(total=1400.0)),
    ]
    for expected, items, now in cases:
        o = F.verdict(items, now, [_sample(4.2)], alike).outcomes[0]
        assert o.kind == F.OUTCOME_NO_VERDICT and o.blocker == expected, (expected, o)
        assert o.delta is None, expected
        assert F.outcome_sentence(o).startswith("C4 — can't say"), F.outcome_sentence(o)
    # …and the lap totals the working-set pair in focus.py actually has (0.93 % apart) do NOT block.
    ok = F.verdict([_item(total=730.6)], _now(total=737.3), [_sample(4.2)], alike).outcomes[0]
    assert ok.has_verdict, ok
    print("ok structural gates: track / provisional line / estimated clock / lap length, "
          "and 0.93 % of real lap-total drift still passes")


def test_too_few_clean_laps_through_the_window_refuses_the_verdict():
    alike = _records(("GX0060", _record()), ("GX0062", _record()))
    thin = F.verdict([_item()], _now(), [_sample(4.2, n=2)], alike).outcomes[0]
    assert thin.kind == F.OUTCOME_NO_VERDICT and thin.blocker == F.BLOCK_FEW_LAPS, thin
    assert thin.delta is None
    none_at_all = F.verdict([_item()], _now(), [None], alike).outcomes[0]
    assert none_at_all.blocker == F.BLOCK_FEW_LAPS and none_at_all.now is None
    print(f"ok thin sample: {F.outcome_sentence(thin)}")


def test_the_session_a_list_came_from_is_never_graded_against_itself():
    o = F.verdict([_item(fp="GX0062")], _now(fp="GX0062"), [_sample(4.6)],
                  _records(("GX0062", _record()))).outcomes[0]
    assert o.kind == F.OUTCOME_SET_HERE and o.delta is None, o
    assert "Next time you're here" in F.outcome_sentence(o)
    print(f"ok self-compare: {F.outcome_sentence(o)}")


def test_one_shared_refusal_is_said_once_and_names_the_fix():
    """Three corners, one reason: three copies of the same 200-character explanation bury the one
    thing the driver can act on, and spend the whole height budget restating one fact."""
    blocked = F.verdict([_item(cid=12), _item(cid=4), _item(cid=2)], _now(),
                        [_sample(6.7), _sample(4.6), _sample(2.4)], session_record.empty_store())
    lines = F.report_lines(blocked)
    assert len(lines) == 1, lines
    assert "C12, C4 and C2" in lines[0] and "can't say" in lines[0], lines[0]
    assert "Session record" in lines[0], "a refusal must name the fix"
    # …but a MIXED report still speaks per corner: one reason does not cover the others.
    alike = _records(("GX0060", _record()), ("GX0062", _record()))
    mixed = F.verdict([_item(cid=4, iqr=0.05), _item(cid=7, verified=False)], _now(),
                      [_sample(4.2, iqr=0.05), _sample(3.0)], alike)
    assert len(F.report_lines(mixed)) == 2, F.report_lines(mixed)
    # …and every individual sentence is still available (the tooltip, and this suite, read them).
    assert all(F.outcome_sentence(o).startswith("C") for o in blocked.outcomes)
    print(f"ok collapsed refusal: {lines[0]}")


def test_the_headline_counts_the_refusals_rather_than_hiding_them():
    alike = _records(("GX0060", _record()), ("GX0062", _record()))
    mixed = F.verdict([_item(cid=4, median=4.537, iqr=0.05), _item(cid=7, verified=False)],
                      _now(), [_sample(4.237, iqr=0.05), _sample(3.0)], alike)
    head = F.report_headline(mixed)
    assert "1 of 2" in head and "without enough evidence" in head, head
    blocked = F.verdict([_item()], _now(), [_sample(4.2)], session_record.empty_store())
    assert "no verdict yet" in F.report_headline(blocked), F.report_headline(blocked)
    assert F.report_headline(F.Report(track="x")) == ""
    print(f"ok headline: {head!r} / {F.report_headline(blocked)!r}")


# ------------------------------------------------- the timing step and the layout (cool/focus-verdict-gates)
def test_a_difference_under_the_timing_step_is_no_change_whatever_the_spread():
    """A claim must beat the timing: under `library.PB_PRECISION_S` (0.07 s) two medians are level
    by the app's one rule for two times, even where the corner's spread is tight enough that the
    spread test alone would have called it. On main the demo against habit-free sessions of its
    circuit said "0.05 s slower" at +0.0517 s and "0.02 s faster" at −0.0203 s. At the step and
    over it the spread test decides, unchanged; on an estimated clock nothing gets that far."""
    from studio import library
    alike = _records(("GX0060", _record()), ("GX0062", _record()))
    # Since cool/focus-cross-session-floor the step a verdict must clear is the cross-session one,
    # wider than the timing's (`test_the_cross_session_step_…`): under the timing step is under it.
    step = F.level_step()
    assert step == F.CROSS_SESSION_LEVEL_S > library.PB_PRECISION_S
    tight = 0.02                      # a spread whose half (0.01 s) every gap below clears
    for gap in (0.020, 0.0517, library.PB_PRECISION_S - 1e-4, step - 1e-4):
        for sign in (1, -1):
            o = F.verdict([_item(median=4.0, iqr=tight)], _now(),
                          [_sample(4.0 + sign * gap, iqr=tight)], alike).outcomes[0]
            assert o.kind == F.OUTCOME_UNCHANGED, (gap, sign, o)
            text = F.outcome_sentence(o)
            assert text.startswith(f"C4 — no change you can act on: under {step:.2f} s apart, "
                                   "within what two recordings of the same driving differ by ("), text
            assert "s faster" not in text and "s slower" not in text, text
    # AT the step is not under it. Around 0.25 s both 0.25 ± 0.08 (and ± 0.081) difference back
    # to at least the gap in floating point (around 1.0 s neither sign of 0.081 does).
    for gap in (step, step + 1e-3, 0.30):
        for sign, kind in ((1, F.OUTCOME_SLOWER), (-1, F.OUTCOME_IMPROVED)):
            o = F.verdict([_item(median=0.25, iqr=tight)], _now(),
                          [_sample(0.25 + sign * gap, iqr=tight)], alike).outcomes[0]
            assert abs(o.delta) >= step and o.kind == kind, (gap, sign, o)
    # …and the boundary itself, exactly: a delta of the step to the last bit is a claim (a `<=` in
    # the rule would call it level).
    exact = F.verdict([_item(median=0.0, iqr=tight)], _now(), [_sample(step, iqr=tight)],
                      alike).outcomes[0]
    assert exact.delta == step and exact.kind == F.OUTCOME_SLOWER, exact
    # The step-only line prints its medians to the millisecond: at two decimals 2.8448 → 2.9097 s
    # (0.0649 s apart) read "2.84 → 2.91 s", a 0.07 s difference beside a step it is under.
    near = F.verdict([_item(median=2.8448, iqr=tight)], _now(), [_sample(2.9097, iqr=tight)],
                     alike).outcomes[0]
    assert near.kind == F.OUTCOME_UNCHANGED, near
    assert F.outcome_sentence(near) == (
        "C4 — no change you can act on: under 0.08 s apart, within what two recordings of the same "
        "driving differ by (2.845 → 2.910 s, 38 → 65 laps)."), F.outcome_sentence(near)
    # Under the step AND inside the spread: the spread sentence, as before (its number is the why).
    inside = F.verdict([_item(median=4.0, iqr=0.13)], _now(), [_sample(4.05, iqr=0.13)],
                       alike).outcomes[0]
    assert "inside the corner's own 0.13 s spread" in F.outcome_sentence(inside)
    # Over the step but inside the spread: still the spread test's "no change", unchanged.
    wide = F.verdict([_item(median=4.0, iqr=0.30)], _now(), [_sample(4.10, iqr=0.30)],
                     alike).outcomes[0]
    assert wide.kind == F.OUTCOME_UNCHANGED and "0.30 s spread" in F.outcome_sentence(wide)
    # An estimated clock: refused before any step, under it, at it or over it — no number at all.
    for gap in (step - 1e-4, step, 0.30):
        o = F.verdict([_item(median=4.0, iqr=tight)], _now(degraded=True),
                      [_sample(4.0 + gap, iqr=tight)], alike).outcomes[0]
        assert o.blocker == F.BLOCK_DEGRADED and o.delta is None, (gap, o)
    print(f"ok timing step: {text}")


def test_the_cross_session_step_is_the_house_rule_on_the_measured_sigma():
    """`focus.CROSS_SESSION_LEVEL_S` is derived, not picked: 2·√2 (`stats.DIFF_2SIGMA_K`, the lap
    step's and the sector step's own factor) × the sd of ONE session's corner-typical bias that
    `studio/dev/probes/focus_cross_session.py` measures at GPS noise 2.5. That σ is published in
    docs/ACCURACY.md with the step; the probe prints the step it measures beside the app's and
    measures at the noise the constant's comment names."""
    import math
    import re

    from studio import library, stats
    assert stats.DIFF_2SIGMA_K == 2 * math.sqrt(2)
    assert F.CROSS_SESSION_LEVEL_S == round(stats.DIFF_2SIGMA_K * F.CORNER_DAY_SIGMA_S, 2) == 0.08
    # wider than the timing step, so the timing step is never the one a verdict reads
    assert F.level_step() == F.CROSS_SESSION_LEVEL_S > library.PB_PRECISION_S
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "docs", "ACCURACY.md"), encoding="utf-8") as f:
        accuracy = " ".join(f.read().split())
    m = re.search(r"at GPS noise ([0-9.]+), whose per-lap σ is ([0-9.]+) s, one session's corner "
                  r"typical carries a bias of σ ([0-9.]+) s .*? closer than ([0-9.]+) s \(2√2σ\)",
                  accuracy)
    assert m, "docs/ACCURACY.md no longer publishes the cross-session step"
    assert float(m.group(3)) == F.CORNER_DAY_SIGMA_S, m.groups()
    assert float(m.group(4)) == F.CROSS_SESSION_LEVEL_S, m.groups()
    # …measured at a per-lap σ inside the band the same page publishes against official timing
    assert 0.025 <= float(m.group(2)) <= 0.087, m.groups()
    with open(os.path.join(root, "studio", "dev", "probes", "focus_cross_session.py"),
              encoding="utf-8") as f:
        probe = f.read()
    assert re.search(rf"^REALISTIC_NOISE = {re.escape(m.group(1))}$", probe, re.M), m.group(1)
    assert "from studio.stats import DIFF_2SIGMA_K" in probe
    assert '"step": round(DIFF_2SIGMA_K * sigma_one, 2)' in probe
    assert 'out["step"] == focus.CROSS_SESSION_LEVEL_S' in probe
    print(f"ok cross-session step: 2√2 × {F.CORNER_DAY_SIGMA_S} s = {F.CROSS_SESSION_LEVEL_S} s")


def test_an_unchanged_day_between_the_old_bar_and_the_step_is_no_change():
    """The probe's unchanged pairs at GPS noise 2.5 (one driver, nothing changed): the bar before
    called 22 of 315 corners "faster"/"slower", every one 0.07-0.11 s on a true change of
    0.00-0.05 s. Two of them, C5 at +0.072 s and C6 at −0.077 s, with spreads whose half sits under
    that: a claim on main, level now, and the line keeps both days' figures and says why once.
    (Seven of the 22 are under the step; the 15 at 0.08-0.11 s still read as calls, the 2σ rule's
    share of 315.)"""
    alike = _records(("GX0060", _record()), ("GX0062", _record()))
    for then, now, iqr in ((3.452, 3.524, 0.09), (2.861, 2.784, 0.06)):
        o = F.verdict([_item(median=then, iqr=iqr)], _now(), [_sample(now, iqr=iqr)],
                      alike).outcomes[0]
        assert abs(o.delta) >= _spread_half(iqr) and abs(o.delta) >= 0.07, o
        assert o.kind == F.OUTCOME_UNCHANGED, (then, now, o.kind)
        text = F.outcome_sentence(o)
        assert text == (f"C4 — no change you can act on: under 0.08 s apart, within what two "
                        f"recordings of the same driving differ by ({then:.3f} → {now:.3f} s, "
                        f"38 → 65 laps)."), text
        assert text.count("recordings") == 1 and "faster" not in text and "slower" not in text
    # …and a true change past the step still reads as one, with its sign and both figures.
    for then, now, word in ((3.140, 3.040, "0.10 s faster"), (3.140, 2.990, "0.15 s faster"),
                            (3.140, 3.340, "0.20 s slower")):
        o = F.verdict([_item(median=then, iqr=0.06)], _now(), [_sample(now, iqr=0.06)],
                      alike).outcomes[0]
        assert o.kind == (F.OUTCOME_IMPROVED if now < then else F.OUTCOME_SLOWER), o
        assert F.outcome_sentence(o) == (f"C4 — {word} than 23 May ({then:.2f} → {now:.2f} s, "
                                         f"38 → 65 laps)."), F.outcome_sentence(o)
    print("ok cross-session step: unchanged days level, true changes still called")


def _spread_half(iqr: float) -> float:
    return K.SPREAD_MARGIN * iqr


def _layout_item(cid, direction, enter, exit_) -> F.FocusItem:
    return F.FocusItem(**{**vars(_item(cid=cid, enter=enter, exit_=exit_)), "direction": direction})


def test_a_corner_that_turns_the_other_way_today_is_not_compared():
    """Another layout of one circuit has its name and, driven the other way round, its lap length,
    so neither the track test nor the lap-length allowance refuses it: the demo's list re-measured
    on its mirrored twin said "C1 — 0.35 s faster" between a right-hander and a left-hander. The
    stored window is read on today's corners; the one overlapping it most must turn the same way."""
    alike = _records(("GX0060", _record()), ("GX0062", _record()))
    # today's corners, as lap fractions: a right-hander at 0.15-0.20 and a left-hander at 0.40-0.46
    today = [(0.14, 0.205, -1), (0.40, 0.46, 1)]
    now = {**_now(), "corners": today}
    same = F.verdict([_layout_item(4, -1, 0.15, 0.20)], now, [_sample(4.2)], alike).outcomes[0]
    assert same.blocker == F.BLOCK_NONE and same.has_verdict, same
    other = F.verdict([_layout_item(4, +1, 0.15, 0.20)], now, [_sample(4.2)], alike).outcomes[0]
    assert other.blocker == F.BLOCK_LAYOUT and other.delta is None, other
    assert F.outcome_sentence(other) == ("C4 — can't say: the corner there today turns the other "
                                         "way — a different layout?"), F.outcome_sentence(other)
    none = F.verdict([_layout_item(5, -1, 0.70, 0.75)], now, [_sample(4.2)], alike).outcomes[0]
    assert none.blocker == F.BLOCK_LAYOUT and none.delta is None, none
    assert F.outcome_sentence(none) == ("C5 — can't say: no corner was picked out there today — "
                                        "a different layout?"), F.outcome_sentence(none)
    # The corner overlapping MOST decides: a window straddling both reads the left-hander here.
    straddle = F.verdict([_layout_item(6, +1, 0.19, 0.45)], now, [_sample(4.2)], alike).outcomes[0]
    assert straddle.has_verdict, straddle
    # An item that never stored a hand, or a caller with no corners, compares as it always did.
    unknown = F.item_from_dict({k: v for k, v in F.item_to_dict(_layout_item(4, 1, 0.15, 0.20))
                                .items() if k != "direction"})
    assert unknown.direction == 0
    assert F.verdict([unknown], now, [_sample(4.2)], alike).outcomes[0].has_verdict
    assert F.verdict([_layout_item(4, +1, 0.15, 0.20)], _now(), [_sample(4.2)],
                     alike).outcomes[0].has_verdict
    # …nor does a session whose best lap has no detected corner at all: an EMPTY list says
    # nothing about the hand of the corner under the window, so it is not "a different layout".
    bare = F.verdict([_layout_item(4, +1, 0.15, 0.20)], {**_now(), "corners": []},
                     [_sample(4.2)], alike)
    assert bare.outcomes[0].has_verdict, bare.outcomes[0]
    assert "layout" not in F.report_headline(bare), F.report_headline(bare)
    # It is a structural test: it fires before the record gate, after the start-line one.
    assert F.verdict([_layout_item(4, +1, 0.15, 0.20)], now, [_sample(4.2)],
                     session_record.empty_store()).outcomes[0].blocker == F.BLOCK_LAYOUT
    assert F.verdict([_layout_item(4, +1, 0.15, 0.20)], {**now, "verified": False},
                     [_sample(4.2)], alike).outcomes[0].blocker == F.BLOCK_UNVERIFIED
    # Every corner refused that way: one line, and the headline names the layout.
    whole = F.verdict([_layout_item(4, +1, 0.15, 0.20), _layout_item(7, -1, 0.41, 0.45)], now,
                      [_sample(4.2), _sample(3.0)], alike)
    assert F.report_lines(whole) == ["C4 and C7 — can't say: the corners there today turn the "
                                     "other way — a different layout of the track?"], \
        F.report_lines(whole)
    assert F.report_headline(whole) == ("Focus list · 2 corners · no verdict — the layout looks "
                                        "different today"), F.report_headline(whole)
    print(f"ok layout: {F.outcome_sentence(other)} / {F.report_headline(whole)}")


# --------------------------------------------------------------------------------- persistence
def test_store_round_trips_caps_at_three_and_an_empty_list_removes_its_row():
    path = os.path.join(_TMP.name, "rt.json")
    items = [_item(cid=c, enter=0.1 * c, exit_=0.1 * c + 0.03) for c in (1, 2, 3, 4)]
    F.save_for_track("Daytona MK", items, path)
    back = F.for_track(F.load(path), "Daytona MK")
    assert [i.cid for i in back] == [1, 2, 3], [i.cid for i in back]
    assert back[0].median_s == items[0].median_s and back[0].date == "2026-05-23"
    assert F.for_track(F.load(path), "Another track") == []
    F.save_for_track("Daytona MK", [], path)
    assert F.load(path)["lists"] == [], F.load(path)
    print("ok store: round-trip, capped at 3, empty list removes the row")


def test_a_corrupt_or_newer_store_never_destroys_the_users_list_silently():
    path = os.path.join(_TMP.name, "corrupt.json")
    with open(path, "w") as fh:
        fh.write("{not json at all")
    assert F.load(path) == F.empty_store()
    F.save_for_track("T", [_item()], path)          # overwriting corruption backs it up first
    assert os.path.exists(F.backup_path(path)), "the unreadable file must be kept"
    # a NEWER schema is read best-effort and backed up before it is overwritten
    with open(path, "w") as fh:
        json.dump({"version": F.VERSION + 5,
                   "lists": [{"track": "T", "items": [F.item_to_dict(_item(cid=9))],
                              "unknown_field": 1}]}, fh)
    assert [i.cid for i in F.for_track(F.load(path), "T")] == [9]
    F.save_for_track("T", [_item(cid=1)], path)
    with open(F.backup_path(path)) as fh:
        assert json.load(fh)["version"] == F.VERSION + 5
    print("ok store: corruption self-heals, a newer file is read best-effort and backed up")


def test_one_malformed_item_is_dropped_not_the_whole_list():
    path = os.path.join(_TMP.name, "partial.json")
    good = F.item_to_dict(_item(cid=2))
    with open(path, "w") as fh:
        json.dump({"version": F.VERSION, "lists": [
            {"track": "T", "items": [{"cid": 1, "enter_frac": 0.9, "exit_frac": 0.2},  # backwards
                                     {"cid": 3, "enter_frac": 0.1},                    # no window
                                     good]}]}, fh)
    kept = F.for_track(F.load(path), "T")
    assert [i.cid for i in kept] == [2], [i.cid for i in kept]
    print("ok store: a bad item is dropped, its neighbours survive")


def test_the_store_lives_behind_the_app_support_seam():
    assert F.focus_path().startswith(_TMP.name), F.focus_path()
    assert F.backup_path().endswith("focus.json.bak")
    print(f"ok seam: {os.path.basename(F.focus_path())} under the test dir")


# --------------------------------------------------------------------------------- the Session
def _stadium_session():
    """The same bare stadium Session the coaching tests use (4 clean laps + 1 dropout)."""
    from test_coaching import _stadium_session as build
    return build()


def test_session_promotes_a_corner_into_a_window_and_re_measures_it():
    s = _stadium_session()
    entry = {"fingerprint": "SYNTH1", "track": "Stadium", "date": "2026-01-01",
             "verified": True, "degraded": False}
    cids = [r.cid for r in s.coaching_opportunities().rows[:1]]
    items = s.focus_items(cids, entry)
    assert len(items) == 1, items
    item = items[0]
    total = float(s.corners.basis()[1])
    corner = next(c for c in s.corners.corner_list() if c.cid == item.cid)
    assert abs(item.enter_frac - corner.enter / total) < 1e-12
    assert item.n_laps == len(s.consistency_lap_ids()) and item.median_s > 0
    assert item.verified is True and item.lap_total == total
    # Re-measuring the SAME session over the stored window reproduces the stored baseline exactly —
    # which is the property that makes a later session's number comparable to it.
    again = s.focus_samples([(item.enter_frac, item.exit_frac)])[0]
    assert abs(again.median - item.median_s) < 1e-12, (again, item)
    # …and the report on that same recording refuses to grade it against itself.
    report = s.focus_report(items, entry, session_record.empty_store(), "Stadium")
    assert report.outcomes[0].kind == F.OUTCOME_SET_HERE, report.outcomes[0]
    print(f"ok session: C{item.cid} stored as {item.enter_frac:.3f}-{item.exit_frac:.3f} "
          f"({item.median_s:.3f} s over {item.n_laps} laps), re-measured identically")


def test_session_report_reads_the_stored_window_even_when_the_corner_moved():
    """The stored window travels; the corner id does not. Re-measuring with a DIFFERENT corner
    partition (the next session's detector) must still measure the stored stretch of track."""
    s = _stadium_session()
    entry = {"fingerprint": "SYNTH1", "track": "Stadium", "date": "2026-01-01",
             "verified": True, "degraded": False}
    item = s.focus_items([r.cid for r in s.coaching_opportunities().rows[:1]], entry)[0]
    # pretend a later session whose detector started this corner 40 % of its length earlier
    width = item.exit_frac - item.enter_frac
    stretched = F.FocusItem(**{**vars(item),
                               "enter_frac": max(item.enter_frac - 0.4 * width, 0.0)})
    a = s.focus_samples([(item.enter_frac, item.exit_frac)])[0]
    b = s.focus_samples([(stretched.enter_frac, stretched.exit_frac)])[0]
    assert b.median > a.median, (a, b)
    print(f"ok window travel: the stored window reads {a.median:.3f} s where a 40 %-longer one "
          f"reads {b.median:.3f} s on the identical laps")


# ------------------------------------------- a stored edge on today's corner boundary (cool/focus-mixed-edge)
# Corner boundaries sit on a 0.75 m grid (corners.GRID_STEP_M), so an edge of a window stored on one
# day lands EXACTLY on a boundary of the next day's partition often. When one edge did and the other
# did not, `focus_samples` read the window off a warp built only from the off-grid edges: the
# on-grid one was never a knot, every lap was skipped, and the debrief said "C1 — can't say: 0 clean
# laps through it today" on a session where all 14 laps drove C1 0.36 s faster (the demo's planted
# habit, gone). 16 of the 112 windows over the demo and seeds +1..+8, both orders, all of them that
# kind. The sessions: the demo, then habit-free recordings of the demo circuit at DEMO_SEED + k.
_SWEEP_SEEDS = (2, 3, 4, 5, 6, 7)      # +3, +6, +7 hold such windows; the others hold none
_FOCUS_SESSIONS: dict = {}


# The demo circuit rebuilt (`synth_gopro.reprofiled`, every other segment where it was): "t<k>" its
# C6 tightened from 26 m to 12 m, same hand; "c<k>" its C3 a chicane, a 30° right then an 80°
# left; "k<k>" (at DEMO_SEED + 100 + k) a 32.5° right-hand kink 45 m before C4, which the detector
# picks out on 6 of 12 seeds (k0, k3, k4, k6, k8, k10).
_KINK_DEG = 32.5


def _layout(name: str):
    from studio.dev import synth_gopro as sg
    return {"t": lambda: sg.reprofiled(6, ("C", 70.0, 12.0)),
            "c": lambda: sg.reprofiled(3, ("C", -30.0, 12.0), ("S", 6.0), ("C", 80.0, 12.0)),
            "k": lambda: sg.reprofiled(4, ("C", -_KINK_DEG, 20.0), ("S", 45.0),
                                       ("C", -(90.0 - _KINK_DEG), 18.0))}.get(name[0], lambda: None)()


def _focus_session(name: str):
    """The demo ("demo") or the habit-free demo-circuit recording DEMO_SEED + k ("s<k>"), or that
    recording driven on the circuit's mirror image, anticlockwise ("m<k>"), or on the same circuit
    the other way round ("r<k>"), or on a rebuilt layout (`_layout`), generated once per process
    (the TemporaryDirectory is kept alive with them: sessions read lazily)."""
    if name not in _FOCUS_SESSIONS:
        from studio.dev import make_demo as md
        from studio.dev import synth_gopro as sg
        from studio.session import Session
        tmp = _FOCUS_SESSIONS.setdefault("_tmp", tempfile.TemporaryDirectory(prefix="focus_edge_"))
        if name == "demo":
            path = os.path.join(tmp.name, "GX019000.MP4")
            md.make(path, render=False)
            paths = [path]
        else:
            seed = md.DEMO_SEED + int(name[1:]) + (100 if name[0] == "k" else 0)
            paths = sg.generate(os.path.join(tmp.name, name), seed, md.DEMO_LAPS, chapters=1,
                                origin=md.DEMO_ORIGIN, mirror=name.startswith("m"),
                                reverse=name.startswith("r"), layout=_layout(name)).paths
        _FOCUS_SESSIONS[name] = Session.load(paths)
    return _FOCUS_SESSIONS[name]


def _corner_windows(s) -> list[tuple[float, float]]:
    """Every corner of `s` as the stored window `focus_items` would write for it."""
    corner_list, total = s.corners.basis()
    return [(c.enter / total, c.exit / total) for c in corner_list]


def _on_grid(s, windows) -> list[int]:
    """Per window, how many of its edges land exactly on one of `s`'s corner boundaries."""
    corner_list, total = s.corners.basis()
    frame = [b for c in corner_list for b in (c.enter, c.exit)]
    return [sum(min(abs(b - f * total) for b in frame) <= 1e-6 for f in w) for w in windows]


def _crossing_times(s, window) -> list[float]:
    """An INDEPENDENT time through `window` on each clean lap of `s`: between the lap's closest
    approaches to the reference lap's points at the two edges, a plain nearest-segment projection
    on the lap's own (x, y, t) with only the receiver's rigid shift taken out — no warp, no knots."""
    rx, ry, rcum = s.corners._best_trace()
    total = float(s.corners.basis()[1])
    out = []
    for lid in s.consistency_lap_ids():
        t, xs, ys, _v, cum = (np.asarray(z, float) for z in s.corners._lap_columns(lid))
        sx, sy = s.corners.lap_shift(lid)
        xs, ys = xs - float(sx), ys - float(sy)
        at = []
        for f in window:
            px, py = np.interp(f * total, rcum, rx), np.interp(f * total, rcum, ry)
            guess = f * float(cum[-1])
            j0, j1 = np.searchsorted(cum, [guess - 60.0, guess + 60.0])
            best = (np.inf, np.nan)
            for j in range(max(int(j0), 0), min(int(j1), len(cum) - 1)):
                vx, vy = xs[j + 1] - xs[j], ys[j + 1] - ys[j]
                u = float(np.clip(((px - xs[j]) * vx + (py - ys[j]) * vy)
                                  / max(vx * vx + vy * vy, 1e-12), 0.0, 1.0))
                d2 = (xs[j] + u * vx - px) ** 2 + (ys[j] + u * vy - py) ** 2
                if d2 < best[0]:
                    best = (d2, t[j] + u * (t[j + 1] - t[j]))
            at.append(best[1])
        out.append(at[1] - at[0])
    return out


def test_a_window_with_one_edge_on_a_boundary_is_timed_on_every_clean_lap():
    """The discovery case: the demo's C1, C2 and C7 on the focus list, re-measured on a habit-free
    session at the same circuit. C1's stored window has its exit on a boundary of today's partition
    and its entry off it; on main it came back None and the verdict said "0 clean laps". Now it is
    timed on all 14 clean laps, it reads the 0.36 s the habit cost, and an instrument that shares
    nothing with the warp agrees with it inside a millisecond."""
    demo, today = _focus_session("demo"), _focus_session("s7")
    windows = _corner_windows(demo)
    kinds = _on_grid(today, windows)
    assert kinds[0] == 1, f"C1's stored window no longer has exactly one edge on a boundary: {kinds}"
    then = demo.focus_samples(windows)
    now = today.focus_samples(windows)
    n = len(today.consistency_lap_ids())
    for k, (kind, sample) in enumerate(zip(kinds, now, strict=True)):
        assert sample is not None, f"C{k + 1} ({kind} edge(s) on a boundary): no sample, {now}"
        assert sample.n_laps == sample.n_of == n, (k + 1, sample, n)
    c1 = now[0].median - then[0].median
    assert -0.40 < c1 < -0.32, f"C1 moved {c1:+.3f} s; the demo's habit costs it ~0.36 s"
    for k in (k for k, kind in enumerate(kinds) if kind == 1):
        independent = float(np.median(_crossing_times(today, windows[k])))
        assert abs(independent - now[k].median) < 1e-3, (k + 1, independent, now[k])
    print(f"ok mixed edge: C1 timed on {now[0].n_laps}/{n} laps, {then[0].median:.3f} → "
          f"{now[0].median:.3f} s ({c1:+.3f} s)")


# main (f80f9a4)'s samples on the demo's windows measured on DEMO_SEED + 6, which holds all three
# kinds: C1/C6 neither edge on a boundary, C2/C4 both, C3/C5/C7 one (None on main).
_MAIN_DEMO_ON_S6 = {1: (3.034518341222463, 0.029242716256802748),
                    2: (2.6650932584884295, 0.045809000825011825),
                    4: (3.0765045337356263, 0.020535470394976052),
                    6: (2.8190525979986276, 0.0421673931264408)}


def test_windows_with_both_or_neither_edge_on_a_boundary_read_what_they_read_before():
    """The fix gives one-edge windows a warp of their own; the other two kinds keep theirs, so
    their numbers are main's bit for bit — measured with the one-edge windows in the same call
    and without them."""
    today = _focus_session("s6")
    windows = _corner_windows(_focus_session("demo"))
    kinds = _on_grid(today, windows)
    assert kinds == [0, 2, 1, 2, 1, 0, 1], kinds
    together = today.focus_samples(windows)
    alone = today.focus_samples([windows[cid - 1] for cid in _MAIN_DEMO_ON_S6])
    for (cid, (median, iqr)), single in zip(_MAIN_DEMO_ON_S6.items(), alone, strict=True):
        got = together[cid - 1]
        assert (got.median, got.iqr, got.n_laps) == (median, iqr, 14), (cid, got)
        assert single == got, (cid, single, got)
    print(f"ok unchanged: {len(_MAIN_DEMO_ON_S6)} windows identical to main, alone or together")


def test_a_session_re_measured_against_itself_is_level():
    """Every corner of a session stored as a baseline, then re-measured on that SAME recording under
    another identity: every verdict is "no change", by exactly 0."""
    for name in ("demo", "s7"):
        s = _focus_session(name)
        then = {"fingerprint": "SELF_THEN", "track": "Demo", "date": "2026-01-01",
                "verified": True, "degraded": False}
        now = {**then, "fingerprint": "SELF_NOW", "date": "2026-01-08"}
        items = [i for c in s.corners.corner_list() for i in s.focus_items([c.cid], then)]
        assert len(items) == len(s.corners.corner_list()), items
        store = _records(("SELF_THEN", _record()), ("SELF_NOW", _record()))
        report = s.focus_report(items, now, store, "Demo")
        for o in report.outcomes:
            assert o.kind == F.OUTCOME_UNCHANGED and o.delta == 0.0, (name, o)
    print("ok self: every corner of two sessions level against itself")


def test_no_stored_window_is_left_without_laps_where_the_corners_table_has_passes():
    """The sweep: the demo against six habit-free sessions, both orders. Every stored window that
    overlaps a corner the Stats ▸ CORNERS table timed today is timed on at least as many laps as
    that corner, so "0 clean laps through it today" is never said of a stretch the laps drove."""
    mixed = 0
    for k in _SWEEP_SEEDS:
        for a, b in (("demo", f"s{k}"), (f"s{k}", "demo")):
            then, now = _focus_session(a), _focus_session(b)
            windows = _corner_windows(then)
            corner_list, total = now.corners.basis()
            passes = {r.cid: r.n for r in now.corner_report()}
            mixed += _on_grid(now, windows).count(1)
            for w, sample in zip(windows, now.focus_samples(windows), strict=True):
                over = [passes.get(c.cid, 0) for c in corner_list
                        if c.enter < w[1] * total and c.exit > w[0] * total]
                if not over or max(over) == 0:
                    continue
                assert sample is not None and sample.n_laps >= min(over), \
                    f"{a} → {b}: window {w} timed on {sample} where CORNERS has {over} passes"
    assert mixed >= 10, f"the sweep holds only {mixed} one-edge windows; it has lost its teeth"
    print(f"ok sweep: {len(_SWEEP_SEEDS) * 2} pairs, {mixed} one-edge windows, all timed")


def _every_corner_report(a: str, b: str) -> F.Report:
    """Every corner of session `a` on the focus list (1 Jun), re-measured on `b` (8 Jun), both days
    recorded dry: the path every surface reads (`Session.focus_report`)."""
    then, now = _focus_session(a), _focus_session(b)
    ea = {"fingerprint": f"FP_{a}", "track": "Demo", "date": "2026-06-01", "verified": True,
          "degraded": False}
    eb = {**ea, "fingerprint": f"FP_{b}", "date": "2026-06-08"}
    items = [i for c in then.corners.corner_list() for i in then.focus_items([c.cid], ea)]
    store = _records((ea["fingerprint"], _record()), (eb["fingerprint"], _record()))
    return now.focus_report(items, eb, store, "Demo")


def test_the_mirrored_circuit_is_another_layout_and_gets_no_verdict():
    """The demo's corners on the same circuit driven as its mirror image (anticlockwise): same
    track, same lap length, every corner turning the other way. On main all seven read a verdict,
    "C1 — 0.35 s faster than 1 Jun" among them, comparing a right-hander with a left-hander. Its
    laps run anticlockwise where the demo's run clockwise, so that is the refusal said (each
    corner's hand is refused too: `test_a_corner_that_turns_the_other_way_today_is_not_compared`)."""
    for a, b in (("demo", "m7"), ("m7", "demo")):
        report = _every_corner_report(a, b)
        assert report.outcomes and report.n_verdicts == 0, \
            [F.outcome_sentence(o) for o in report.outcomes if o.has_verdict]
        assert all(
            o.blocker == F.BLOCK_LAYOUT and o.detail == F.LAYOUT_REVERSED and o.delta is None
            for o in report.outcomes), [(o.item.label, o.kind, o.blocker) for o in report.outcomes]
        for o in report.outcomes:
            assert F.outcome_sentence(o) == (f"{o.item.label} — can't say: today's laps run the "
                                             f"other way round — a different layout?")
        n = len(report.outcomes)
        assert F.report_headline(report) == (f"Focus list · {n} corners · no verdict — the layout "
                                             f"looks different today"), F.report_headline(report)
        assert len(F.report_lines(report)) == 1 and "a different layout" in F.report_lines(report)[0]
    print(f"ok mirrored: {F.report_headline(report)} / {F.report_lines(report)[0]}")


def test_same_layout_sessions_are_never_refused_and_claim_only_past_the_step():
    """The demo against habit-free sessions of its own circuit, both orders: the layout test
    refuses none of their corners; no "faster"/"slower" stands on a difference under the timing
    step (on main, 0.020-0.052 s claims at s4, s6 and s7 among these); A → B "faster" exactly when
    B → A "slower" (on main C5 read "0.05 s slower" demo → s6 and "no change" back); and the
    demo's planted habit still reads as before on s7. The step is the verdict's own, the
    cross-session one since cool/focus-cross-session-floor (`F.level_step`)."""
    flip = {F.OUTCOME_IMPROVED: F.OUTCOME_SLOWER, F.OUTCOME_SLOWER: F.OUTCOME_IMPROVED}
    claims = 0
    for k in _SWEEP_SEEDS:
        there = _every_corner_report("demo", f"s{k}")
        back = {o.item.cid: o for o in _every_corner_report(f"s{k}", "demo").outcomes}
        for o in [*there.outcomes, *back.values()]:
            assert o.has_verdict, (k, o.item.label, o.blocker, F.outcome_sentence(o))
            if o.kind != F.OUTCOME_UNCHANGED:
                claims += 1
                assert abs(o.delta) >= F.level_step(), (k, F.outcome_sentence(o))
        for o in there.outcomes:
            r = back.get(o.item.cid)
            if r is not None and (o.kind in flip or r.kind in flip):
                assert r.kind == flip.get(o.kind), (k, F.outcome_sentence(o), F.outcome_sentence(r))
        if k == 7:
            assert F.outcome_sentence(there.outcomes[0]) == \
                "C1 — 0.36 s faster than 1 Jun (3.38 → 3.02 s, 14 → 14 laps).", \
                F.outcome_sentence(there.outcomes[0])
    assert claims >= 6, f"only {claims} claims over the sweep; it has lost its teeth"
    print(f"ok same layout: {len(_SWEEP_SEEDS) * 2} pairs, no refusal, {claims} claims all past "
          f"the step and symmetric")


# ------------------------------------------- reversed, reprofiled, marginal (cool/layout-reversed-synth)
def test_which_way_round_a_lap_runs_is_read_off_its_corners_and_stored():
    """`focus.lap_rotation`: the sign of the corners' net turn, or 0 when it does not add up to a
    loop. An item carries it; one stored before it carries none and is not checked for it."""
    assert F.lap_rotation([-100, -60, 50, -90, -130, 70, -100]) == -1
    assert F.lap_rotation([100, 60, -50, 90, 130, -70, 100]) == 1
    assert F.lap_rotation([]) == 0 and F.lap_rotation([90, -90, 120, -110]) == 0   # a figure of 8
    alike = _records(("GX0060", _record()), ("GX0062", _record()))
    now = {**_now(), "corners": [(0.14, 0.205, -1)], "rotation": 1}
    clockwise = F.FocusItem(**{**vars(_layout_item(4, -1, 0.15, 0.20)), "rotation": -1})
    o = F.verdict([clockwise], now, [_sample(4.2)], alike).outcomes[0]
    assert (o.blocker, o.detail, o.delta) == (F.BLOCK_LAYOUT, F.LAYOUT_REVERSED, None), o
    assert F.outcome_sentence(o) == ("C4 — can't say: today's laps run the other way round — a "
                                     "different layout?"), F.outcome_sentence(o)
    # Either side not telling is not checked: an item stored before the field, a lap that did not.
    legacy = F.item_from_dict({k: v for k, v in F.item_to_dict(clockwise).items()
                               if k != "rotation"})
    assert legacy.rotation == 0
    assert F.verdict([legacy], now, [_sample(4.2)], alike).outcomes[0].has_verdict
    assert F.verdict([clockwise], {**now, "rotation": 0}, [_sample(4.2)],
                     alike).outcomes[0].has_verdict
    assert F.item_from_dict(F.item_to_dict(clockwise)) == clockwise
    assert not F._valid_item({**F.item_to_dict(clockwise), "rotation": 2})
    assert not F._valid_item({**F.item_to_dict(clockwise), "rotation": True})
    print("ok rotation: read, stored, and checked only when both sides tell it")


def test_the_same_tarmac_driven_the_other_way_round_gets_no_verdict():
    """The demo's circuit driven in reverse: the same track, the same start line, the same lap
    length to under a metre, every corner reversed in order and in hand. The hand test alone lets
    through every stored window that lands on a reversed corner turning its way — on main the
    demo's list read "C5 — 0.99 s faster" and "C3 — 0.30 s slower than 1 Jun" on r7, and
    "C3 — 1.35 s faster" the other way, each between two different corners. Every corner is now
    refused, once, for the reason measured."""
    for a, b in (("demo", "r7"), ("r7", "demo")):
        report = _every_corner_report(a, b)
        now = _focus_session(b)
        ctx = now.focus_context({"fingerprint": f"FP_{b}", "track": "Demo", "verified": True})
        hand_only = [o for o in report.outcomes if not F.layout_check(o.item, ctx["corners"])]
        assert len(hand_only) >= 3, f"{a} → {b}: the hand alone refuses all but " \
            f"{len(hand_only)}; the case has lost its teeth"
        assert report.outcomes and report.n_verdicts == 0, \
            [F.outcome_sentence(o) for o in report.outcomes if o.has_verdict]
        assert {(o.blocker, o.detail) for o in report.outcomes} == \
            {(F.BLOCK_LAYOUT, F.LAYOUT_REVERSED)}, [F.outcome_sentence(o) for o in report.outcomes]
        assert F.report_lines(report) == [
            f"{F._corner_list(report.outcomes)} — can't say: today's laps run the other way round "
            f"— a different layout of the track?"], F.report_lines(report)
        n = len(report.outcomes)
        assert F.report_headline(report) == (f"Focus list · {n} corners · no verdict — the layout "
                                             f"looks different today"), F.report_headline(report)
    print(f"ok reversed: {len(hand_only)} windows the hand passed, all refused / "
          f"{F.report_lines(report)[0]}")


def _deltas(report: F.Report) -> dict:
    return {o.item.cid: (o.kind, o.delta) for o in report.outcomes}


def test_a_reprofiled_corner_is_compared_and_the_rest_read_as_before():
    """One corner rebuilt, the rest of the circuit where it was (`synth_gopro.reprofiled`). WHAT
    THE CHECK CANNOT SEE, pinned so a change to it is a decision: a corner that keeps its hand is
    compared, so the demo's C6 tightened from 26 m to 12 m reads ~0.6 s "slower", and its C3 made
    a chicane (a 30° right the detector does not pick out, then an 80° left) ~0.7 s "slower" —
    the time is the tarmac's, not the driver's. Every corner away from the change reads what the
    unchanged layout on the same seed reads, within the timing step: same kind, Δ under 0.07 s
    apart. (The chicane's next corner, C4, is left out: its approach is the chicane's exit.)"""
    from studio import library
    base = _deltas(_every_corner_report("demo", "s7"))
    said = []
    for name, changed, near in (("t7", 6, ()), ("c7", 3, (4,))):
        got = _deltas(_every_corner_report("demo", name))
        kind, delta = got[changed]
        assert kind == F.OUTCOME_SLOWER and delta > 0.5, (name, changed, got[changed])
        said.append(f"{name} C{changed} {delta:+.2f} s")
        for cid, (kind, delta) in got.items():
            if cid in (changed, *near):
                continue
            assert kind == base[cid][0] and abs(delta - base[cid][1]) < library.PB_PRECISION_S, \
                (name, cid, got[cid], base[cid])
    print(f"ok reprofiled: {', '.join(said)} compared; the rest as on s7")


def test_a_corner_the_detector_drops_today_is_said_to_be_undetected():
    """A 32.5° kink, the same layout both days: k0's detector picks it out and k1's does not (6
    of 12 seeds do; in all 36 such ordered pairs main said "there's no corner there today — a
    different layout?" beside the rest of the list measured level). The kink is still refused —
    nothing measured it today — and the line says only what was measured, no cause; the
    other corners keep their verdicts, and none is a claim. The other way round, k1's list has no
    kink on it, and every corner gets a verdict."""
    def kink(s):
        return [c.cid for c in s.corners.corner_list()
                if c.direction == -1 and c.turn_deg < 35.0 and 0.42 < c.enter / s.corners.basis()[1]
                < 0.44]

    assert kink(_focus_session("k0")) and not kink(_focus_session("k1")), \
        "the kink is no longer detected on k0 alone; the case has lost its teeth"
    (cid,) = kink(_focus_session("k0"))
    report = _every_corner_report("k0", "k1")
    for o in report.outcomes:
        if o.item.cid == cid:
            assert (o.blocker, o.detail, o.delta) == (F.BLOCK_LAYOUT, F.LAYOUT_NO_CORNER, None), o
            assert F.outcome_sentence(o) == (f"C{cid} — can't say: no corner was picked out "
                                             f"there today — a different layout?"), \
                F.outcome_sentence(o)
        else:
            assert o.kind == F.OUTCOME_UNCHANGED, F.outcome_sentence(o)
    n = len(report.outcomes)
    assert F.report_headline(report) == (f"Focus list · {n - 1} of {n} corners re-measured, 1 "
                                         f"without enough evidence"), F.report_headline(report)
    back = _every_corner_report("k1", "k0")
    assert all(o.kind == F.OUTCOME_UNCHANGED for o in back.outcomes), \
        [F.outcome_sentence(o) for o in back.outcomes]
    # Every corner of a list undetected today: the headline says that, not "a different layout".
    alone = F.Report(track="Demo", outcomes=[o for o in report.outcomes if o.item.cid == cid])
    assert F.report_headline(alone) == ("Focus list · 1 corner · no verdict — no corner was "
                                        "picked out there today"), F.report_headline(alone)
    print(f"ok marginal: {F.outcome_sentence(next(o for o in report.outcomes if o.item.cid == cid))}")


def test_a_list_refused_for_mixed_layout_reasons_says_each_once():
    """A list refused for SEVERAL layout reasons collapses to one line that says each reason once:
    no "the layout looks different today — a different layout of the track?" (the same thing
    twice) when some items stored a rotation and the rest found a corner turning the other way,
    and no "aren't there" for a corner nothing picked out today."""
    alike = _records(("GX0060", _record()), ("GX0062", _record()))
    # today: anticlockwise, a right-hander at 0.14-0.205, nothing at 0.70-0.75
    now = {**_now(), "corners": [(0.14, 0.205, -1), (0.40, 0.46, 1)], "rotation": 1}
    reversed_ = F.FocusItem(**{**vars(_layout_item(2, -1, 0.40, 0.46)), "rotation": -1})
    other_way = _layout_item(4, +1, 0.15, 0.20)          # stored no rotation: the hand test
    missing = _layout_item(5, -1, 0.70, 0.75)

    def line(*items):
        report = F.verdict(list(items), now, [_sample(4.2)] * len(items), alike)
        assert {o.blocker for o in report.outcomes} == {F.BLOCK_LAYOUT}, report.outcomes
        (got,) = F.report_lines(report)
        return got

    assert line(reversed_, other_way) == (
        "C2 and C4 — can't say: today's laps run the other way round, or the corners there today "
        "turn the other way — a different layout of the track?")
    assert line(reversed_, missing) == (
        "C2 and C5 — can't say: today's laps run the other way round, or the corners there today "
        "weren't picked out — a different layout of the track?")
    assert line(other_way, missing) == (
        "C4 and C5 — can't say: the corners there today turn the other way or weren't picked "
        "out — a different layout of the track?")
    assert line(reversed_, other_way, missing) == (
        "C2, C4 and C5 — can't say: today's laps run the other way round, or the corners there "
        "today turn the other way or weren't picked out — a different layout of the track?")
    print(f"ok mixed: {line(reversed_, other_way, missing)}")


# --------------------------------------------------------------------------------- the panel
def _panel_with(report, size=(900, 800)):
    """A real panel laid out at `size`, settled. The explicit 1x1 minimums stand in for the grid
    splitter (test_coaching_panel_layout._panel's note: a free-standing widget cannot shrink past
    the table's own minimumSizeHint, but in the app this page really is squeezed to 280x196)."""
    from studio.coaching_panel import OpportunitiesPanel
    p = OpportunitiesPanel(_stadium_session())
    for w in (p, p.body, p.table):
        w.setMinimumSize(1, 1)
    p.resize(*size)
    p.show()
    p.set_focus_report(report)
    for _ in range(6):
        _APP.processEvents()
    return p


def _report(*outcomes) -> F.Report:
    return F.Report(track="Stadium", outcomes=list(outcomes))


def test_the_panel_is_unchanged_until_the_app_hands_it_a_report():
    from studio.coaching_panel import OpportunitiesPanel
    p = OpportunitiesPanel(_stadium_session())
    p.resize(900, 800)
    p.show()
    for _ in range(6):
        _APP.processEvents()
    assert p.focus_block.isHidden(), "no store, no block — the page is exactly what it was"
    assert p.focus_block.full_text() == ""
    p.set_focus_report(None)   # a recording with no detected track: still dormant
    for _ in range(4):
        _APP.processEvents()
    assert p.focus_block.isHidden()
    print("ok panel: dormant without a report, and on an untracked recording")


def test_the_panel_states_the_refusal_rather_than_a_number():
    blocked = F.verdict([_item()], _now(), [_sample(5.5)], session_record.empty_store())
    p = _panel_with(blocked)
    assert not p.focus_block.isHidden()
    shown = [lb.text() for lb in p.focus_block.lines if not lb.isHidden()]
    assert shown and "can't say" in shown[0], shown
    assert "1.0" not in shown[0], shown
    # the whole block is also on the header hover, like the theme
    assert p.focus_block.full_text() in p.summary_label.toolTip()
    print(f"ok panel: {shown[0]}")


def test_the_add_button_names_the_corner_and_the_gestures_are_signals():
    p = _panel_with(_report())
    # UX-3: the empty list is ONE line beside its Add button — no headline row, no invitation
    # paragraph (it is the line's hover), and no Remove button, which an empty list can never use.
    fb = p.focus_block
    assert fb.empty_line.isVisible() and "Focus list · empty" in fb.empty_line.text()
    assert fb.headline.isHidden() and all(b.isHidden() for b in fb.drop_buttons)
    assert not [lb for lb in fb.lines if not lb.isHidden()], "the invitation is not a second row"
    assert "Pick up to 3 corners" in fb.empty_line.toolTip()
    assert not p.focus_block.add_button.isEnabled(), "nothing selected yet"
    p.table.selectRow(0)
    for _ in range(4):
        _APP.processEvents()
    cid = p._selected_cid()
    assert p.focus_block.add_button.text() == f"Add C{cid} to focus list"
    assert p.focus_block.add_button.isEnabled()
    seen = []
    p.focus_add_requested.connect(seen.append)
    p.focus_block.add_button.click()
    assert seen == [cid], seen
    # …and once it is on the list it has its OWN Remove (PS-B1: one click per corner), which needs
    # no selection — a corner kept from another day need not be anywhere near today's top rows.
    p.set_focus_report(_report(F.Outcome(item=_item(cid=cid), kind=F.OUTCOME_SET_HERE,
                                         now=None, delta=None)))
    p.table.selectRow(0)
    for _ in range(4):
        _APP.processEvents()
    assert not p.focus_block.add_button.isEnabled()
    assert p.focus_block.empty_line.isHidden() and not p.focus_block.headline.isHidden()
    p.table.clearSelection()
    for _ in range(4):
        _APP.processEvents()
    shown = [b for b in p.focus_block.drop_buttons if not b.isHidden()]
    assert [b.text() for b in shown] == [f"Remove C{cid}"] and shown[0].isEnabled(), \
        [b.text() for b in shown]
    dropped = []
    p.focus_remove_requested.connect(dropped.append)
    shown[0].click()
    assert dropped == [cid], dropped
    print(f"ok panel: Add C{cid} → signal, then its own Remove C{cid} → signal")


def test_the_focus_block_yields_its_height_to_the_ranking():
    """The vertical twin of the theme block's budget, and now they must yield to EACH OTHER too:
    two leading blocks each entitled to a third of the page leave the ranked list the last third of
    a page that is about the ranked list."""
    alike = _records(("GX0060", _record()), ("GX0062", _record()))
    full = F.verdict([_item(cid=2, iqr=0.05), _item(cid=4, iqr=0.05), _item(cid=7, iqr=0.05)],
                     _now(), [_sample(4.2, iqr=0.05)] * 3, alike)
    p = _panel_with(full, size=(280, 196))       # the app's own minimum
    assert p.focus_block.isHidden(), "the focus block must not displace the ranking"
    assert p.table.viewport().height() > 0 and p.table.rowCount() >= 1, (
        p.table.viewport().height(), p.table.rowCount())
    assert p.focus_block.full_text() in p.summary_label.toolTip(), "shed, not deleted"
    p.resize(900, 800)
    for _ in range(8):
        _APP.processEvents()
    assert not p.focus_block.isHidden(), "and it comes back when the page grows (no ratchet)"
    assert len([lb for lb in p.focus_block.lines if not lb.isHidden()]) == 3
    from studio.coaching_panel import BLOCKS_MAX_FRACTION
    both = p.focus_block.height() + (0 if p.theme_block.isHidden() else p.theme_block.height())
    assert both <= 800 * BLOCKS_MAX_FRACTION + 2, (p.focus_block.height(), p.theme_block.height())
    print(f"ok panel budget: hidden at the minimum, 3 lines at 800 px, two blocks {both} px")


def test_a_stored_date_that_is_not_a_calendar_day_is_not_printed_as_one():
    """The verdict names the day a baseline was set ("no session record for 23 May"), read off the
    item's stored date — a string the store only checks IS a string, so a hand-edited or damaged
    focus.json can carry anything. The fallback for an unreadable one is "last time".

    It caught a month of 13 (an IndexError) but not a month of 00: `_MONTHS[0 - 1]` is December,
    so "2026-00-15" was stated as a baseline set on "15 Dec", and a day of 00 as "0 May"."""
    assert F._when("2026-05-23") == "23 May"
    assert F._when("2026-12-01") == "1 Dec"
    for bad in ("2026-00-15", "2026-13-01", "2026-05-00", "2026-02-30", "2026-05-2x", "yesterday!"):
        assert F._when(bad) == "last time", (bad, F._when(bad))
    o = F.verdict([_item(date="2026-00-15")], _now(), [None], _records()).outcomes[0]
    assert o.blocker == F.BLOCK_NO_RECORD, o
    assert o.detail == "last time and today", o.detail
    print("ok dates: a stored date that is not a calendar day reads 'last time', never '15 Dec'")


def test_a_stored_window_that_runs_backwards_or_off_the_lap_is_dropped():
    """The window IS the item's identity, so an item whose window cannot be measured is not
    repairable. `test_one_malformed_item_is_dropped_not_the_whole_list` names a backwards window but
    that item also lacks its median, so it is rejected before the window is ever looked at; these
    items are complete in every other field, so only the window check can refuse them."""
    good = F.item_to_dict(_item(cid=2))
    backwards = {**F.item_to_dict(_item(cid=1)), "enter_frac": 0.9, "exit_frac": 0.2}
    empty = {**F.item_to_dict(_item(cid=3)), "enter_frac": 0.4, "exit_frac": 0.4}
    off_lap = {**F.item_to_dict(_item(cid=5)), "enter_frac": 0.8, "exit_frac": 1.2}
    before = {**F.item_to_dict(_item(cid=6)), "enter_frac": -0.1, "exit_frac": 0.2}
    assert F._valid_item(good)
    for bad in (backwards, empty, off_lap, before):
        assert not F._valid_item(bad), bad
    path = os.path.join(_TMP.name, "windows.json")
    with open(path, "w") as fh:
        json.dump({"version": F.VERSION, "lists": [
            {"track": "T", "items": [backwards, empty, off_lap, before, good]}]}, fh)
    assert [i.cid for i in F.for_track(F.load(path), "T")] == [2]
    print("ok store: a backwards, empty or off-lap window is dropped on its own account")


# ------------------------------------------------------------ UX-6: the offer, and the kart
def test_a_refusal_for_want_of_a_record_names_exactly_the_sessions_it_waits_on():
    """`Report.unrecorded` is what "Mark both dry" may write: the sessions a no_record refusal waits
    on — the earlier day(s) first, today last, each once — and never one that has a record, nor
    any when the refusal is about something a record cannot fix."""
    items, samples = [_item(cid=4), _item(cid=7)], [_sample(4.5)] * 2
    none = F.verdict(items, _now(), samples, session_record.empty_store())
    assert none.unrecorded == (("GX0060", "23 May"), ("GX0062", "today")), none.unrecorded
    assert F.mark_dry_prompt(none)[:2] == ("Both dry?", "Mark both dry")
    assert "for 23 May and today saying the conditions were Dry" in F.mark_dry_prompt(none)[2]
    then_only = F.verdict(items, _now(), samples, _records(("GX0060", _record())))
    assert then_only.unrecorded == (("GX0062", "today"),)
    assert F.mark_dry_prompt(then_only)[:2] == ("Dry today?", "Mark today dry")
    now_only = F.verdict(items, _now(), samples, _records(("GX0062", _record())))
    assert F.mark_dry_prompt(now_only)[:2] == ("Dry on 23 May?", "Mark 23 May dry")
    mixed = F.verdict([_item(cid=4), _item(cid=7, fp="GX0058", date="2026-04-11")], _now(),
                      samples, session_record.empty_store())
    assert [label for _fp, label in mixed.unrecorded] == ["23 May", "11 Apr", "today"]
    assert F.mark_dry_prompt(mixed)[:2] == ("All 3 dry?", "Mark all 3 dry")
    both = F.verdict(items, _now(), samples, _records(("GX0060", _record()),
                                                      ("GX0062", _record())))
    assert both.unrecorded == () and F.mark_dry_prompt(both) is None
    unverified = F.verdict(items, _now(verified=False), samples, session_record.empty_store())
    assert unverified.unrecorded == () and F.mark_dry_prompt(unverified) is None, \
        "a record cannot lift a provisional start line, so none may be offered"
    print("ok offer: names exactly the unrecorded sessions, and only for a record refusal")


def test_the_offer_sits_under_the_refusal_and_its_click_is_a_signal():
    """The block shows the offer only while a record refusal stands, and the click writes nothing
    itself: it emits the fingerprints the button named, for the app (which owns the store)."""
    p = _panel_with(F.verdict([_item()], _now(), [_sample(4.5)], session_record.empty_store()))
    blk = p.focus_block
    assert not blk._mark_row.isHidden(), "a record refusal with no offer beside it"
    assert (blk.mark_question.text(), blk.mark_button.text()) == ("Both dry?", "Mark both dry")
    got = []
    p.focus_mark_dry_requested.connect(got.append)
    blk.mark_button.click()
    assert got == [["GX0060", "GX0062"]], got
    p.set_focus_report(F.verdict([_item()], _now(), [_sample(4.5)],
                                 _records(("GX0060", _record()), ("GX0062", _record()))))
    _APP.processEvents()
    assert blk._mark_row.isHidden(), "nothing waits on a record any more — the offer must go"
    p.set_focus_report(_report())
    _APP.processEvents()
    assert blk._mark_row.isHidden(), "an empty list has nothing to mark"
    print("ok panel: the offer comes and goes with the refusal; its click is a signal")


def test_a_verdict_across_two_karts_says_so_and_is_still_a_verdict():
    """UX-6 (4): two sessions on different fleet karts are compared — refusing would refuse every
    pair an arrive-and-drive driver has — but not silently: the verdict names both karts, in the
    then → now grammar of its other two pairs. Same kart, or one unrecorded: nothing is said."""
    then, now = {**_record(), "kart_no": "12"}, {**_record(), "kart_no": "7"}
    r = F.verdict([_item()], _now(), [_sample(4.1, n=36)],
                  _records(("GX0060", then), ("GX0062", now)))
    o = r.outcomes[0]
    assert o.has_verdict and o.delta is not None and o.karts == ("12", "7"), o
    line = F.report_lines(r)[0]
    assert line == "C4 — 0.40 s faster than 23 May (4.50 → 4.10 s, 38 → 36 laps, kart 12 → 7).", line
    for other in ({**now, "kart_no": "12"}, _record()):
        same = F.verdict([_item()], _now(), [_sample(4.1, n=36)],
                         _records(("GX0060", then), ("GX0062", other)))
        assert same.outcomes[0].karts is None and "kart" not in F.report_lines(same)[0]
    print(f"ok kart: {line}")


# ------------------------------------------------------------------ the window: record writes
# The session the list was promoted in, on "another day" — its fingerprint and date are all the
# verdict reads of it; the numbers are this session's own, so every structural gate passes.
_THEN_FP, _THEN_DATE = "GX0065", "2025-08-23"      # "23 Aug" in the refusal


@contextlib.contextmanager
def _own_stores():
    """Every store a record gesture touches, in a directory of this test's own: the verdict reads
    the focus list AND the records, and the Library's forget / clear / restore also write the index
    and the marks. The seams are put back afterwards, so the module-level focus seam the other
    tests share is left exactly as it was."""
    from studio import library, marks
    mods = (F, library, session_record, marks)
    saved = [m._app_support_dir for m in mods]
    with tempfile.TemporaryDirectory() as d:
        for m in mods:
            m._app_support_dir = lambda d=d: d
        try:
            yield d
        finally:
            for m, fn in zip(mods, saved, strict=True):
                m._app_support_dir = fn


def _window_with_a_corner_from_another_day():
    """The REAL StudioWindow (its real `_build_ui`, so the real Coaching page and the real library
    controller) over the synthetic stadium session, with one corner on this track's focus list
    promoted on ANOTHER day. The item is measured on this session's own window and lap length, so
    the like-for-like record gate is the one that decides — the SD_30_08 → SD_19_09 shape the
    board review drove on real footage (UX-5)."""
    import test_central_view_realqt as realqt
    win, view = realqt._studiowindow_with_view()
    entry = win.library_ctl._current_library_entry()
    first = win.session.corners.corner_list()[0]
    item = win.session.focus_items([first.cid], entry)[0]
    F.save_for_track(entry["track"], [F.FocusItem(**{**vars(item), "fingerprint": _THEN_FP,
                                                     "date": _THEN_DATE})])
    win.library_ctl.update_focus_list()      # what the load does: `_build_ui` ends with this call
    return win, view, entry, item.label


def _then_entry(track: str, folder: str) -> dict:
    """The other day's Library row — what the Library dialog hands the record editor for it. Its
    file has to exist: the dialog greys out, and will not select, a row whose footage is gone."""
    media = os.path.join(folder, "GX010065.MP4")
    if not os.path.exists(media):
        with open(media, "wb") as fh:
            fh.write(b"not a video; the row only needs its file to exist")
    return {"fingerprint": _THEN_FP, "stem": "GX010065", "track": track, "date": _THEN_DATE,
            "lap_count": 37, "best": 47.1, "theoretical": 46.5, "verified": True,
            "degraded": False, "dropout": False, "paths": [media]}


def _focus_text(view) -> str:
    _APP.processEvents()
    return view.opportunities.focus_block.full_text()


def _dispose(win, view):
    win._tick_timer.stop()          # a leaked tick timer has hung a later test before
    view.dispose()
    win.deleteLater()
    _APP.processEvents()


@contextlib.contextmanager
def _the_record_form_answers(*, delete: bool = False):
    """`SessionRecordDialog.exec()` answered the way the driver answers it — pick Dry and press
    Save, or press Delete record and confirm — on the REAL form, so what gets written is the real
    `result_record()`. Only the modal loop an offscreen test cannot sit in is replaced."""
    from PySide6.QtWidgets import QMessageBox

    from studio import session_record_dialog as srd

    class _ConfirmYes:
        Yes, No = QMessageBox.Yes, QMessageBox.No

        @staticmethod
        def question(*_a, **_k):
            return QMessageBox.Yes

    def _exec(dlg):
        if delete:
            dlg.delete_btn.click()
        else:
            dlg.conditions.setCurrentIndex(dlg.conditions.findData("dry"))
            dlg.save_btn.click()
        return dlg.result()

    orig_exec, orig_box = srd.SessionRecordDialog.exec, srd.QMessageBox
    srd.SessionRecordDialog.exec, srd.QMessageBox = _exec, _ConfirmYes
    try:
        yield
    finally:
        srd.SessionRecordDialog.exec, srd.QMessageBox = orig_exec, orig_box


@contextlib.contextmanager
def _the_library_edits_the_record_of(fingerprint: str):
    """File ▸ Library… answered by selecting `fingerprint`'s row and pressing its Session record…
    button (`_edit_selected_record`, the dialog's own handler), then closing — the board review's
    second step, through the real dialog and its injected editor callback."""
    from studio.library_dialog import LibraryDialog

    def _exec(dlg):
        for row in range(dlg.table.rowCount()):
            dlg.table.selectRow(row)
            chosen = dlg._selected_entry()
            if chosen and chosen.get("fingerprint") == fingerprint:
                dlg._edit_selected_record()
                return LibraryDialog.Rejected
        raise AssertionError(f"the Library has no row for {fingerprint}")

    orig = LibraryDialog.exec
    LibraryDialog.exec = _exec
    try:
        yield
    finally:
        LibraryDialog.exec = orig


def test_writing_both_records_the_refusal_asks_for_lifts_it_without_a_reload():
    """UX-5, the owner's own sequence. The Coaching page says "no session record for 23 Aug and
    today … File ▸ Session record… writes one"; he writes today's through that menu item and the
    other day's through the Library — and the page used to keep the refusal word for word until he
    re-opened the recording, because nothing re-read the verdict after a record write (only the
    lap panel's chip was refreshed). Each write must move the verdict at once: after today's the
    refusal names only 23 Aug, after both it is gone."""
    from studio import library
    with _own_stores() as folder:
        win, view, entry, label = _window_with_a_corner_from_another_day()
        try:
            library.upsert_and_save(_then_entry(entry["track"], folder))
            before = _focus_text(view)
            assert f"{label} — can't say. There's no session record for 23 Aug and today" \
                in before, before

            with _the_record_form_answers():
                win.library_ctl.edit_current_record()                    # File ▸ Session record…
            assert session_record.get(session_record.load(), entry["fingerprint"]), \
                "today's record was not written — the test drove nothing"
            after_today = _focus_text(view)
            assert "no session record for 23 Aug," in after_today, (
                f"after today's record the refusal must name only the other day: {after_today!r}")

            with _the_record_form_answers(), _the_library_edits_the_record_of(_THEN_FP):
                win.library_ctl.open_library()                          # File ▸ Library… ▸ row
            assert session_record.get(session_record.load(), _THEN_FP), \
                "the other day's record was not written — the test drove nothing"
            after_both = _focus_text(view)
            assert "no session record" not in after_both, (
                f"both records are written and the page still refuses for want of one: "
                f"{after_both!r}")
            assert f"{label} — " in after_both, f"the corner fell off the list: {after_both!r}"
        finally:
            _dispose(win, view)
    print(f"ok window: refusal → {after_today.splitlines()[-1][:60]!r} → "
          f"{after_both.splitlines()[-1][:60]!r}")


def test_every_other_write_to_the_record_store_moves_the_verdict_too():
    """The same staleness through each of the store's other writers, all of which the verdict reads
    through: the Library's Clear (records wiped, backed up), its Restore… (records back), Forget on
    the other day's row (its record goes with it) and the form's Delete record. Driven through the
    controller callbacks the Library dialog is handed, one assertion per write."""
    from studio import library
    with _own_stores() as folder:
        win, view, entry, _label = _window_with_a_corner_from_another_day()
        ctl = win.library_ctl
        try:
            library.upsert_and_save(_then_entry(entry["track"], folder))
            with _the_record_form_answers():
                ctl.edit_current_record()
                ctl._edit_session_record(_then_entry(entry["track"], folder))
            assert "no session record" not in _focus_text(view), _focus_text(view)

            ctl._clear_library()
            assert "no session record for 23 Aug and today" in _focus_text(view), \
                f"Clear wiped both records and the page still reads: {_focus_text(view)!r}"
            ctl._restore_library()
            assert "no session record" not in _focus_text(view), \
                f"Restore… put both records back and the page still reads: {_focus_text(view)!r}"
            ctl._forget_recording(_then_entry(entry["track"], folder))
            assert "no session record for 23 Aug," in _focus_text(view), \
                f"Forget took 23 Aug's record and the page still reads: {_focus_text(view)!r}"
            with _the_record_form_answers(delete=True):
                ctl.edit_current_record()
            assert session_record.get(session_record.load(), entry["fingerprint"]) is None
            assert "no session record for 23 Aug and today" in _focus_text(view), \
                f"today's record was deleted and the page still reads: {_focus_text(view)!r}"
        finally:
            _dispose(win, view)
    print("ok window: clear, restore, forget and delete each re-read the verdict")


# ------------------------------------------------------- the window: two clicks, no form (UX-6)
@contextlib.contextmanager
def _no_record_form():
    """Every `SessionRecordDialog` the journey opens, by title — answered Cancel, so a form that did
    open cannot hang an offscreen test in its modal loop. The journey's claim is that it stays []."""
    from studio import session_record_dialog as srd
    opened: list[str] = []
    orig = srd.SessionRecordDialog.exec

    def _exec(dlg):
        opened.append(dlg.windowTitle())
        return srd.SessionRecordDialog.Rejected

    srd.SessionRecordDialog.exec = _exec
    try:
        yield opened
    finally:
        srd.SessionRecordDialog.exec = orig


class _Hands:
    """The driver's hands on the real window. A click lands only on a widget that is ON SCREEN and
    ENABLED, through QTest (a real mouse press and release), and every click is counted — the
    journey's cost is this count, not a claim about it."""

    def __init__(self, view):
        self.view, self.clicks = view, 0

    def click(self, widget, pos=None) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        _APP.processEvents()
        assert widget.isVisible() and widget.isEnabled(), f"not on screen to click: {widget!r}"
        QTest.mouseClick(widget, Qt.LeftButton, Qt.NoModifier,
                         widget.rect().center() if pos is None else pos)
        self.clicks += 1
        _APP.processEvents()

    def open_tab(self, name: str) -> None:
        bar = self.view.tab_bar
        idx = next((i for i in range(bar.count()) if name in bar.tabText(i)), None)
        assert idx is not None, [bar.tabText(i) for i in range(bar.count())]
        self.click(bar, bar.tabRect(idx).center())
        assert bar.currentIndex() == idx, f"the {name} tab did not open"

    def buttons(self) -> list:
        """Every button the driver can see and press on the Coaching page."""
        from PySide6.QtWidgets import QAbstractButton
        _APP.processEvents()
        return [b for b in self.view.opportunities.findChildren(QAbstractButton)
                if b.isVisible() and b.isEnabled()]

    def button(self, prefix: str):
        hits = [b for b in self.buttons() if b.text().startswith(prefix)]
        return hits[0] if len(hits) == 1 else None


_NO_INLINE_ACTION = (
    "the Coaching page refuses for want of two session records and offers no way to write them "
    "from here: the only route is File ▸ Session record… for today, then File ▸ Library… ▸ the "
    "other day's row ▸ Session record… — the record form, twice. On-screen buttons: {}")


def test_two_clicks_from_the_coaching_page_lift_the_record_refusal_without_the_form():
    """UX-6 on the real window, CI half. The driver lands on the Laps tab (the owner's remembered
    one); the Coaching page refuses for want of a record on either day; ONE click on the page's own
    "Mark both dry" writes the two minimal records the refusal asks for — conditions Dry, nothing
    else — and the refusal lifts. Two clicks in all (the tab, the button), and the record form
    never opens. The synthetic session has two laps, so what speaks next is the lap-count gate (the
    footage check below reaches a verdict on two real Sandown sessions)."""
    from studio import library
    with _own_stores() as folder:
        win, view, entry, label = _window_with_a_corner_from_another_day()
        try:
            library.upsert_and_save(_then_entry(entry["track"], folder))
            win.resize(1440, 900)
            win.show()
            view.tab_bar.setCurrentIndex(0)
            hands = _Hands(view)
            with _no_record_form() as forms:
                hands.open_tab("Coaching")
                before = _focus_text(view)
                assert "no session record for 23 Aug and today" in before, before
                mark = hands.button("Mark ")
                assert mark is not None, _NO_INLINE_ACTION.format(
                    [b.text() for b in hands.buttons()])
                hands.click(mark)
            after = _focus_text(view)
            store = session_record.load()
        finally:
            _dispose(win, view)
    assert hands.clicks <= 2 and not forms, (hands.clicks, forms)
    assert "no session record" not in after, f"the refusal survived the click: {after!r}"
    assert f"{label} — " in after, f"the corner fell off the list: {after!r}"
    assert sorted(store["records"]) == sorted([_THEN_FP, entry["fingerprint"]]), store["records"]
    for fp, rec in store["records"].items():
        assert rec["conditions"] == "dry" and session_record.filled_fields(rec) == 1, (fp, rec)
    print(f"ok window: {hands.clicks} clicks, no form → {after.splitlines()[-1][:70]!r}")


def _open_and_wait(win, path: str, timeout: float = 300.0) -> None:
    """Drop `path` on the real window (chapters expanded, as a drop does) and pump until it lands."""
    import time
    done = {"v": False}
    win.loadFinished.connect(lambda: done.__setitem__("v", True))
    win._open_recordings([path])
    deadline = time.time() + timeout
    while not done["v"] and time.time() < deadline:
        _APP.processEvents()
        time.sleep(0.01)
    assert done["v"], f"{path} did not finish loading within {timeout:.0f} s"
    for _ in range(20):
        _APP.processEvents()


def _folder_state(folders) -> dict:
    """(size, mtime) of every file beside the footage — the Desktop is read-only for a test."""
    return {os.path.join(d, f): (os.stat(os.path.join(d, f)).st_size,
                                 os.stat(os.path.join(d, f)).st_mtime_ns)
            for d in sorted(folders) for f in sorted(os.listdir(d)) if not f.startswith(".")}


def test_two_sandown_sessions_reach_a_verdict_from_the_debrief():
    """UX-6/UX-7 + PS-B1 on real footage: the loop the owner has never run, run the way he now
    would. A FRESH library; the earlier Sandown session opened in the real window lands on its
    debrief with Coaching's top three already on the focus list — no click; the later one opened in
    the SAME window lands on its debrief too, with the refusal and "Mark both dry" on it. ONE click
    must put a VERDICT on every promoted corner, on the debrief, without the record form ever
    opening. (Before the debrief it was two clicks after three manual promotions: the Coaching tab,
    then the button.) The pair is `_footage.pair()` (default 0068 now, Sandown 3h 0064 then —
    focus.py's measured pair, both on the built-in Sandown Park line)."""
    from PySide6.QtWidgets import QMessageBox

    from studio.app import StudioWindow
    now_path, then_path = _footage.pair()
    folders = {os.path.dirname(os.path.abspath(p)) for p in (now_path, then_path)}
    before_files = _folder_state(folders)

    def _no_modal(*args, **_kwargs):
        raise AssertionError(f"a modal opened during the journey: {args[1:3]}")

    boxes = {k: getattr(QMessageBox, k) for k in ("critical", "warning", "information", "question")}
    for k in boxes:
        setattr(QMessageBox, k, staticmethod(_no_modal))
    try:
        with _own_stores():
            win = StudioWindow([])
            win.resize(1440, 900)
            win.show()
            try:
                _open_and_wait(win, then_path)
                then_entry = win.library_ctl._current_library_entry()
                top = win.view.opportunities.shortlist_cids()
                assert win.view.is_debrief() and len(top) == 3, (win.view.is_debrief(), top)
                assert "baselines measured on this session" in _focus_text(win.view), \
                    _focus_text(win.view)

                _open_and_wait(win, now_path)
                view = win.view
                assert view.is_debrief(), "the later session's first open is its debrief too"
                hands = _Hands(view)
                with _no_record_form() as forms:
                    refusal = _focus_text(view)
                    assert f"no session record for {F._when(then_entry['date'])} and today" \
                        in refusal, refusal
                    mark = hands.button("Mark ")
                    assert mark is not None, _NO_INLINE_ACTION.format(
                        [b.text() for b in hands.buttons()])
                    hands.click(mark)
                verdict = _focus_text(view)
                on_debrief = view.is_debrief()
            finally:
                win.close()
                win.deleteLater()
                _APP.processEvents()
    finally:
        for k, fn in boxes.items():
            setattr(QMessageBox, k, fn)
    assert _folder_state(folders) == before_files, "a file beside the footage changed"
    assert hands.clicks == 1 and not forms and on_debrief, (hands.clicks, forms, on_debrief)
    assert "can't say" not in verdict, f"two clicks and still no verdict: {verdict!r}"
    lines = verdict.splitlines()
    for cid in top:
        line = next((ln for ln in lines if ln.startswith(f"C{cid} — ")), "")
        assert any(w in line for w in ("no change you can act on", " faster than ",
                                       " slower than ")), f"C{cid} has no verdict: {verdict!r}"
    print(f"ok footage: {len(top)} corners, {hands.clicks} clicks, no form:\n{verdict}")


# Its own CTest registration, `footage.<name>` (tests/_footage.py): reported SKIPPED by name without
# its recordings, and left out of the ordinary run below.
FOOTAGE_CHECKS = (test_two_sandown_sessions_reach_a_verdict_from_the_debrief,)


if __name__ == "__main__":
    if _footage.requested():
        sys.exit(_footage.run(FOOTAGE_CHECKS))
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and v not in FOOTAGE_CHECKS]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} FOCUS-LIST TESTS PASSED")
