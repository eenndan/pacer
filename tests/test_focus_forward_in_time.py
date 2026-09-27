"""The focus check runs forward in time only (QA round 3, 2026-09-26: REG-1 / JOURNEY-6).

WHY. The check re-measures a list's corners on the session in front of the driver and says whether
they moved SINCE the list was set. Nothing asked whether that session came after the list:
  * after the owner replaced his Sandown list with 19 Sep's top three (#426's offer) and opened
    30 Aug from the Library to look back, the Coaching page said "C7 — 0.06 s faster than 19 Sep"
    about a session three weeks older, and offered "Replace with today's top 3 (C7, C5, C3)": one
    click put 30 Aug's baselines back, so the next Sandown check measured against the older day;
  * before the records were written, the same look back asked "Both dry?" of that backwards check.
Now a session recorded before a baseline gets no verdict on it, no record prompt and no replace
offer, and one line says why. Sessions are ordered by the recordings' own clocks: the first GPS
fix's wall clock when both sides carry it (two sessions of one day), else the calendar date; a
pair whose order is unknown (one side has no date, or a same-day list stored before this change)
is compared as before.

Pinned here: SD30 and SD19 in both orders; two sessions of one day in both orders, and one whose
list predates the start stamp; the replace offer on a newer list; the store round-trip of the stamp;
the Session stamping it; and, on the real window, the replace and the debrief's default refusing
to put an older session's corners on a newer list.

Run: python tests/test_focus_forward_in_time.py   (~15 s)
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

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()

from studio import focus, session_record  # noqa: E402

SD30 = {"fp": "GX0065", "date": "2026-08-30"}
SD19 = {"fp": "GX0068", "date": "2026-09-19"}
HOUR_MS = 3_600_000
NOON_19 = 1_789_815_600_000     # 2026-09-19 around midday, epoch ms


def _items(day: dict, cids=(7, 5, 3), start_ms=0) -> list[focus.FocusItem]:
    return [focus.item_from_dict({
        "cid": c, "direction": 1, "enter_frac": 0.1 * c, "exit_frac": 0.1 * c + 0.05,
        "median_s": 4.08, "iqr_s": 0.1, "n_laps": 36, "time_lost": 0.2, "reason": "",
        "reach": "", "fingerprint": day["fp"], "date": day["date"], "lap_total": 737.0,
        "verified": True, "degraded": False, "n_of": 36, "method": focus.METHOD_MATCHED,
        "start_ms": start_ms}) for c in cids]


def _ctx(day: dict, start_ms=0) -> dict:
    return {"list_track": "Sandown Park", "track": "Sandown Park", "fingerprint": day["fp"],
            "date": day["date"], "start_ms": start_ms, "lap_total": 737.0, "verified": True,
            "degraded": False}


def _dry(*fps) -> dict:
    store = session_record.empty_store()
    for fp in fps:
        session_record.put(store, fp, {**session_record.blank_record(), "conditions": "dry"})
    return store


def _check(items, ctx, records):
    sample = focus.CornerSample(median=4.02, iqr=0.1, n_laps=37, n_of=37)
    return focus.verdict(items, ctx, [sample] * len(items), records)


# ------------------------------------------------------------------ SD30 and SD19, both orders
def test_a_later_session_is_checked_and_an_older_one_is_not():
    records = _dry(SD30["fp"], SD19["fp"])
    forward = _check(_items(SD30), _ctx(SD19), records)          # 30 Aug's list, on 19 Sep
    assert forward.n_verdicts == 3, [o.blocker for o in forward.outcomes]
    backward = _check(_items(SD19, (1, 5, 7)), _ctx(SD30), records)   # 19 Sep's list, on 30 Aug
    kinds = [(o.kind, o.blocker, o.delta) for o in backward.outcomes]
    assert all(k == (focus.OUTCOME_NO_VERDICT, focus.BLOCK_OLDER, None) for k in kinds), (
        "a session older than the list was graded against it", kinds)
    assert focus.report_lines(backward) == [
        "This session is older than your focus list (set on 19 Sep); verdicts compare later "
        "sessions."], focus.report_lines(backward)
    assert focus.report_headline(backward) == \
        "Focus list · 3 corners · no verdict on an older session", focus.report_headline(backward)
    assert focus.replace_offer(backward, [7, 5, 3]) is None, "offered to put 30 Aug's corners back"
    # With no record on either day the backwards pair must not ask "Both dry?" either (JOURNEY-6).
    unrecorded = _check(_items(SD19, (1, 5, 7)), _ctx(SD30), session_record.empty_store())
    assert focus.mark_dry_prompt(unrecorded) is None, focus.mark_dry_prompt(unrecorded)
    assert all(o.blocker == focus.BLOCK_OLDER for o in unrecorded.outcomes)
    print("ok SD30 → SD19 checked; SD19's list on SD30: no verdict, no Mark, no Replace")


# ------------------------------------------------------------------ one day, two sessions
def test_two_sessions_of_one_day_are_ordered_by_their_start():
    records = _dry("GX0070", "GX0071", "GX0072")
    morning = {"fp": "GX0070", "date": "2026-09-19"}
    afternoon = {"fp": "GX0071", "date": "2026-09-19"}
    later = _check(_items(morning, start_ms=NOON_19), _ctx(afternoon, NOON_19 + 3 * HOUR_MS),
                   records)
    assert later.n_verdicts == 3, [o.blocker for o in later.outcomes]
    earlier = _check(_items(afternoon, start_ms=NOON_19 + 3 * HOUR_MS), _ctx(morning, NOON_19),
                     records)
    assert all(o.blocker == focus.BLOCK_OLDER for o in earlier.outcomes), (
        "the morning was graded against the afternoon's list", [o.blocker for o in earlier.outcomes])
    assert focus.replace_offer(earlier, [2, 4, 6]) is None
    # A same-day list stored before the start stamp existed cannot be ordered: compared as before.
    legacy = _check(_items(morning), _ctx(afternoon, NOON_19), records)
    assert legacy.n_verdicts == 3, [o.blocker for o in legacy.outcomes]
    # Unknown on either side is not "older" either: no date, no start.
    undated = _check(_items({"fp": "GX0072", "date": None}), _ctx(morning), records)
    assert undated.n_verdicts == 3, [o.blocker for o in undated.outcomes]
    print("ok same day: afternoon checked against the morning, never the morning against it")


# ------------------------------------------------------------------ the replace offer (#426)
def test_the_replace_offer_never_swaps_a_newer_list_for_an_older_sessions_corners():
    records = _dry(SD30["fp"], SD19["fp"], "GX0066")
    sep5 = {"fp": "GX0066", "date": "2026-09-05"}
    # REG-1 exactly: 19 Sep's list, 30 Aug re-opened, both days dry, 30 Aug's own top three.
    back = _check(_items(SD19, (1, 5, 7)), _ctx(SD30), records)
    assert focus.replace_offer(back, [7, 5, 3]) is None, "30 Aug's corners offered over 19 Sep's"
    # A list mixing two days: 5 Sep is after 30 Aug's corner and before 19 Sep's, so one verdict
    # and one refusal, and still no offer — replacing would drop the newer baseline.
    mixed = _items(SD30, (7,)) + _items(SD19, (1,))
    report = _check(mixed, _ctx(sep5), records)
    assert [o.kind for o in report.outcomes][0] != focus.OUTCOME_NO_VERDICT, report.outcomes[0]
    assert report.outcomes[1].blocker == focus.BLOCK_OLDER, report.outcomes[1]
    assert "19 Sep" in focus.report_lines(report)[1], focus.report_lines(report)
    assert focus.replace_offer(report, [2, 4, 6]) is None, "offered over a newer baseline"
    # ...while a session after the whole list still gets the offer.
    ahead = _check(mixed, _ctx({"fp": "GX0069", "date": "2026-09-26"}),
                   _dry(SD30["fp"], SD19["fp"], "GX0069"))
    assert focus.replace_offer(ahead, [2, 4, 6]) == [2, 4, 6], [o.blocker for o in ahead.outcomes]
    print("ok replace offer: never from a session older than any baseline on the list")


# ------------------------------------------------------------------ the store and the Session
def test_the_start_stamp_round_trips_and_an_unstamped_list_keeps_its_items():
    with tempfile.TemporaryDirectory(prefix="focusfwd_") as d:
        path = os.path.join(d, "focus.json")
        focus.save_for_track("Sandown Park", _items(SD19, start_ms=NOON_19), path)
        (first, *_rest) = focus.for_track(focus.load(path), "Sandown Park")
        assert first.start_ms == NOON_19, first
        # A list saved before the stamp: every item kept, its order unknown (0).
        with open(path) as fh:
            raw = json.load(fh)
        for item in raw["lists"][0]["items"]:
            del item["start_ms"]
        raw["lists"][0]["items"][1]["start_ms"] = -5      # malformed: that item goes, not the list
        with open(path, "w") as fh:
            json.dump(raw, fh)
        items = focus.for_track(focus.load(path), "Sandown Park")
        assert [i.cid for i in items] == [7, 3] and all(i.start_ms == 0 for i in items), items
    print("ok store: the start stamp round-trips; an unstamped item loads with its order unknown")


def test_the_session_stamps_its_start_on_the_item_and_the_check():
    from test_coaching import _stadium_session

    s = _stadium_session()
    start = s._wall_clock_ms()[0]
    entry = {"fingerprint": "SYNTH1", "track": "Stadium", "date": "2026-01-01",
             "verified": True, "degraded": False}
    (item,) = s.focus_items([s.coaching_opportunities().rows[0].cid], entry)
    assert item.start_ms == start, (item.start_ms, start)
    assert s.focus_context(entry, "Stadium")["start_ms"] == start
    # A baseline recorded after this session is refused on it.
    later = dataclasses.replace(item, fingerprint="SYNTH2", date="2026-01-02")
    report = s.focus_report([later], entry, _dry("SYNTH1", "SYNTH2"), "Stadium")
    assert report.outcomes[0].blocker == focus.BLOCK_OLDER, report.outcomes[0]
    print(f"ok session: items and the check carry the session's first-fix clock ({start} ms "
          "on this stub; the window test holds a real one)")


# ------------------------------------------------------------------ the real window
def test_an_older_session_never_rewrites_a_newer_list_on_the_real_window():
    from test_debrief_landing import _fresh_app_support, _open, _settle, _two_recordings

    from studio.app import StudioWindow

    with tempfile.TemporaryDirectory(prefix="focusfwd_") as folder, _fresh_app_support():
        a, _b = _two_recordings(folder)
        win = StudioWindow([])
        win.resize(1440, 900)
        win.show()
        try:
            _open(win, a)
            ctl, track = win.library_ctl, win.session.track_name
            listed = focus.for_track(focus.load(), track)
            assert listed, "the first open's debrief put today's corners on the list"
            clock = win.session._wall_clock_ms()[0]
            assert clock > 0 and {i.start_ms for i in listed} == {clock}, (
                "a promoted item must carry its recording's first-fix clock", clock, listed)
            # The list as a LATER session left it: every baseline a day after this recording.
            day = win.session.session_date()
            later = [dataclasses.replace(i, fingerprint="GX9999", date=_next_day(day), start_ms=0)
                     for i in listed]
            focus.save_for_track(track, later[:2])
            ctl.update_focus_list()
            block = win.view.opportunities.focus_block
            assert block._replace == [] and not block._mark_fps, (block._replace, block._mark_fps)
            ctl.focus_replace([listed[0].cid])
            assert focus.for_track(focus.load(), track) == later[:2], "an older session replaced it"
            assert "older" in win.statusBar().currentMessage(), win.statusBar().currentMessage()
            assert ctl.pre_promote_focus([listed[-1].cid]) == [], "a default filled a newer list"
            assert focus.for_track(focus.load(), track) == later[:2]
        finally:
            win.close()
            win.deleteLater()
            _settle(0.1)
    print("ok window: an older session's corners never replace or fill a newer list")


def _next_day(date: str) -> str:
    import datetime
    return (datetime.date.fromisoformat(date) + datetime.timedelta(days=1)).isoformat()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} FOCUS FORWARD-IN-TIME TESTS PASSED")
