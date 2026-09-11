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
    to prevent, sized on real data in studio/focus.py (C8's window grew 45.0 m → 56.3 m between the
    two D24 recordings, worth +0.550 s of "you got slower" that the driver did not do);
  * the GATE: no session record on either side, records that disagree, a provisional start line,
    ESTIMATED timing, mismatched lap lengths and too few clean laps each REFUSE a verdict — and a
    refused verdict carries `delta is None`, so no surface can print a number the evidence does not
    support. This is PR #258's rule ("silent when either side has no record") said out loud;
  * the SPREAD test: a change smaller than half the corner's own interquartile spread is
    "no change you can act on", using coaching.SPREAD_MARGIN and coaching's own IQR statistic
    rather than a second notion of significance;
  * the PANEL: the block is dormant until the app hands it a report, it names the corner its Add
    button would promote, the two gestures are signals (the window owns the store), and it yields
    its height to the ranked list exactly as the theme block does.

Run:  QT_QPA_PLATFORM=offscreen python tests/test_focus_list.py
"""
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
    # …and the lap totals the two real D24 recordings actually have (0.65 % apart) do NOT block.
    ok = F.verdict([_item(total=1059.2)], _now(total=1066.2), [_sample(4.2)], alike).outcomes[0]
    assert ok.has_verdict, ok
    print("ok structural gates: track / provisional line / estimated clock / lap length, "
          "and 0.65 % of real lap-total drift still passes")


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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} FOCUS-LIST TESTS PASSED")
