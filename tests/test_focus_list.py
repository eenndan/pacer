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
    the two working-set recordings, worth +0.062 s of "you got slower" on a corner the driver took
    0.139 s quicker over the same stretch);
  * the GATE: no session record on either side, records that disagree, a provisional start line,
    ESTIMATED timing, mismatched lap lengths and too few clean laps each REFUSE a verdict — and a
    refused verdict carries `delta is None`, so no surface can print a number the evidence does not
    support. This is PR #258's rule ("silent when either side has no record") said out loud;
  * the SPREAD test: a change smaller than half the wider of the two sessions' interquartile
    spreads is "no change you can act on", using coaching.SPREAD_MARGIN and coaching's own IQR
    statistic rather than a second notion of significance;
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
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
    statistic — a driver cannot aim at 0.06 s inside a band whose middle half is 0.13 s wide."""
    alike = _records(("GX0060", _record()), ("GX0062", _record(air=19.0, track_t=26.0)))
    inside = F.verdict([_item(median=4.537, iqr=0.129)], _now(),
                       [_sample(4.598, iqr=0.134)], alike).outcomes[0]
    assert inside.kind == F.OUTCOME_UNCHANGED, inside
    assert inside.delta is not None and abs(inside.delta - 0.061) < 1e-6
    assert "no change you can act on" in F.outcome_sentence(inside)
    # …and the same numbers with a spread half as wide DO clear the bar.
    outside = F.verdict([_item(median=4.537, iqr=0.05)], _now(),
                        [_sample(4.598, iqr=0.05)], alike).outcomes[0]
    assert outside.kind == F.OUTCOME_SLOWER, outside
    assert abs(outside.delta - 0.061) < 1e-6
    # WHOSE spread: the WIDER of the two sessions', in either order — a change is only as aimable
    # as the noisier side. Both pairs above have near-equal spreads, so on their own they cannot
    # tell "wider" from "narrower": a verdict built on the tight side passed them unchanged.
    for then_iqr, now_iqr in ((0.05, 0.20), (0.20, 0.05)):
        lopsided = F.verdict([_item(median=4.537, iqr=then_iqr)], _now(),
                             [_sample(4.598, iqr=now_iqr)], alike).outcomes[0]
        assert lopsided.kind == F.OUTCOME_UNCHANGED, (
            f"0.061 s is inside half of the noisier session's 0.20 s spread, but with IQRs "
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
    assert "Focus list · empty" in p.focus_block.headline.text()
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
    # …and once it is on the list the button flips to the remove gesture
    p.set_focus_report(_report(F.Outcome(item=_item(cid=cid), kind=F.OUTCOME_SET_HERE,
                                         now=None, delta=None)))
    p.table.selectRow(0)
    for _ in range(4):
        _APP.processEvents()
    assert not p.focus_block.add_button.isEnabled()
    assert p.focus_block.drop_button.isEnabled()
    dropped = []
    p.focus_remove_requested.connect(dropped.append)
    p.focus_block.drop_button.click()
    assert dropped == [cid], dropped
    print(f"ok panel: Add C{cid} → signal, then Remove C{cid} → signal")


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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} FOCUS-LIST TESTS PASSED")
