"""The focus list follows the session (QA round 2, 2026-09-26: NEW-5, LOOK-10).

WHY, finding by finding, each measured on the owner's own recordings:
  * LOOK-10: the debrief quoted MK_18_09's focus baselines as "5.20 s, 2.39 s, 2.61 s over 19 laps"
    beside the Stats page's CORNERS medians for the same corners, 5.18 / 2.43 / 2.61 s, and the
    Coaching row for C2 said only 16 of the 19 laps count there. The baseline was a third
    measurement: every clean lap over the window's fractions of its own odometer, no gate on an
    edge the lap could not be matched at. It is now the CORNERS table's own measurement, and the
    text says how many laps each baseline stands on. Because a baseline is compared with a later
    session's re-measure, BOTH halves use it; focus.json is v2, and a v1 list keeps its meaning.
  * NEW-5: after the between-sessions check, today's biggest corner (the one the debrief says to
    "Start with") was not on the list and nothing offered to swap it in, so the next check would
    re-measure the old corners against the old baselines. The focus block now offers "Replace with
    today's top 3 (…)" once the check has run; one click replaces the list, measured on this session.

Run: python tests/test_focus_list_follows.py   (~20 s)
"""
import dataclasses
import json
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_debrief_landing import _fresh_app_support, _open, _settle, _two_recordings  # noqa: E402

from studio import focus  # noqa: E402


def _item(cid, n=19, n_of=19, fp="GX0001", method=focus.METHOD_MATCHED, median=2.43):
    return focus.FocusItem(cid=cid, direction=1, enter_frac=0.1 * cid, exit_frac=0.1 * cid + 0.05,
                           median_s=median, iqr_s=0.1, n_laps=n, time_lost=0.2, reason="",
                           reach="", fingerprint=fp, date="2026-09-18", lap_total=1000.0,
                           verified=True, degraded=False, n_of=n_of, method=method)


# ------------------------------------------------------------------------------------ LOOK-10
def test_the_set_here_line_says_how_many_laps_each_baseline_stands_on():
    same = focus.verdict([_item(5), _item(8)], {"fingerprint": "GX0001"}, [None, None])
    assert "(2.43 s, 2.43 s over 19 laps)" in focus.report_lines(same)[0], focus.report_lines(same)
    mixed = focus.verdict([_item(5), _item(2, n=16), _item(8)], {"fingerprint": "GX0001"},
                          [None] * 3)
    line = focus.report_lines(mixed)[0]
    assert "2.43 s over 16 of 19 laps" in line and "2.43 s over 19 laps" in line, line
    one = focus.verdict([_item(2, n=16)], {"fingerprint": "GX0001"}, [None])
    assert "over 16 of 19 laps" in focus.report_lines(one)[0], focus.report_lines(one)
    print("ok LOOK-10 text: one count when shared, one per corner when not ('16 of 19')")


def test_a_v1_list_keeps_its_meaning():
    """A v1 store's items were measured by the fraction instrument; they load stamped with it, and
    a save writes v2 with every item and its method kept."""
    v1 = {"version": 1, "lists": [{"track": "Stadium", "items": [
        {k: v for k, v in focus.item_to_dict(_item(4)).items() if k not in ("n_of", "method")}]}]}
    with tempfile.TemporaryDirectory(prefix="focusv1_") as d:
        path = os.path.join(d, "focus.json")
        with open(path, "w") as fh:
            json.dump(v1, fh)
        (item,) = focus.for_track(focus.load(path), "Stadium")
        assert item.method == focus.METHOD_FRACTION and item.n_of == 0, item
        assert item.count_text == "19 laps", item.count_text
        focus.save_for_track("Stadium", [item], path)
        with open(path) as fh:
            stored = json.load(fh)
    assert stored["version"] == focus.VERSION == 2, stored["version"]
    assert stored["lists"][0]["items"][0]["method"] == focus.METHOD_FRACTION, stored
    print("ok LOOK-10 store: a v1 item loads as the fraction instrument and saves as v2 unchanged")


def test_the_baseline_is_the_corners_table_row():
    """On a real (synthetic) session: each promoted corner's baseline IS its CORNERS row — median
    and lap count — and a v1 (fraction) item of the same window is re-measured by its own method."""
    from studio import chapters
    from studio.session import Session
    with tempfile.TemporaryDirectory(prefix="focusrow_") as folder, _fresh_app_support():
        a, _b = _two_recordings(folder)
        s = Session.load(chapters.discover_siblings(a))
        entry = s.library_entry([a])
        rows = {r.cid: r for r in s.corner_report()}
        cids = [r.cid for r in s.coaching_opportunities().ranked_rows()[:3]] or sorted(rows)[:3]
        items = s.focus_items(cids, entry)
        assert [i.cid for i in items] == cids, (items, cids)
        for it in items:
            row = rows[it.cid]
            assert it.method == focus.METHOD_MATCHED
            assert it.median_s == row.median_s, (it.cid, it.median_s, row.median_s)
            assert (it.n_laps, it.n_of) == (row.n, row.n_laps), (it.cid, it.n_laps, it.n_of, row)
        legacy = [dataclasses.replace(i, method=focus.METHOD_FRACTION) for i in items]
        report = s.focus_report(legacy, {**entry, "fingerprint": "GX-OTHER"}, None,
                                entry["track"])
        want = s.focus_samples([(i.enter_frac, i.exit_frac) for i in items],
                               focus.METHOD_FRACTION)
        assert [o.now for o in report.outcomes] == want, "a v1 item re-measured by another method"
    print(f"ok LOOK-10 instrument: {cids} baselines equal their CORNERS rows")


# -------------------------------------------------------------------------------------- NEW-5
def test_the_replace_offer_waits_for_the_check_and_a_difference():
    then = [_item(7, fp="GX0065"), _item(5, fp="GX0065"), _item(3, fp="GX0065")]
    ctx = {"fingerprint": "GX0068", "track": "T", "list_track": "T", "verified": True,
           "lap_total": 1000.0}
    sample = focus.CornerSample(median=2.4, iqr=0.1, n_laps=19, n_of=19)
    no_record = focus.verdict(then, ctx, [sample] * 3, {})
    assert focus.replace_offer(no_record, [1, 5, 7]) is None, "offered before the Mark both dry"
    from studio import session_record
    dry = session_record.empty_store()
    for fp in ("GX0065", "GX0068"):
        rec = session_record.blank_record()
        rec["conditions"] = "dry"
        session_record.put(dry, fp, rec)
    checked = focus.verdict(then, ctx, [sample] * 3, dry)
    assert checked.n_verdicts == 3, [o.blocker for o in checked.outcomes]
    assert focus.replace_offer(checked, [1, 5, 7]) == [1, 5, 7]
    assert focus.replace_label([1, 5, 7]) == "Replace with today's top 3 (C1, C5, C7)"
    assert focus.replace_offer(checked, [3, 5, 7]) is None, "offered today's own list"
    here = focus.verdict([_item(1, fp="GX0068")], ctx, [sample], dry)
    assert focus.replace_offer(here, [1, 5, 7]) is None, "offered over a list set today"
    print("ok NEW-5 offer: after the check, only when today's top corners differ")


def test_one_click_replaces_the_list_with_todays_top_corners():
    """The real window, two synthetic recordings: a list the driver edited on the first, the check
    answered on the second with Mark both dry, then the offer's one click."""
    from PySide6.QtWidgets import QMessageBox

    from studio.app import StudioWindow
    boxes = {k: getattr(QMessageBox, k) for k in ("critical", "warning", "information", "question")}
    for k in boxes:
        setattr(QMessageBox, k, staticmethod(lambda *a, **k2: QMessageBox.Ok))
    with tempfile.TemporaryDirectory(prefix="focusrep_") as folder, _fresh_app_support():
        a, b = _two_recordings(folder)
        win = StudioWindow([])
        win.resize(1440, 900)
        win.show()
        try:
            _open(win, a)
            panel, track = win.view.opportunities, win.session.track_name
            short = panel.shortlist_cids()
            other = next(c.cid for c in win.session.corners.corner_list() if c.cid not in short)
            win.library_ctl.focus_remove(short[-1])      # the driver's own edit of the list
            win.library_ctl.focus_add(other)
            _open(win, b)
            panel = win.view.opportunities
            block = panel.focus_block
            assert block.replace_button.isHidden(), "offered before the check"
            block.mark_button.click()
            _settle(0.2)
            assert not block.replace_button.isHidden(), block.full_text()
            assert block.replace_button.text() == focus.replace_label(short), \
                block.replace_button.text()
            block.replace_button.click()
            _settle(0.2)
            items = focus.for_track(focus.load(), track)
            assert [i.cid for i in items] == short, (items, short)
            assert {i.fingerprint for i in items} == {"GX9002"}, "baselines not from this session"
            assert block.replace_button.isHidden(), "still offered after the replace"
        finally:
            win.close()
            win.deleteLater()
            _settle(0.1)
            for k, fn in boxes.items():
                setattr(QMessageBox, k, fn)
    print(f"ok NEW-5 journey: C{other} for C{short[-1]} edited in, checked, one click → {short}")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} FOCUS-FOLLOWS TESTS PASSED")
