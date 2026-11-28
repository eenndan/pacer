"""The PB sentence also says how the best lap stands against the PREVIOUS session (cool/quicker-than-
last-heat).

WHY. Between heats the driver asks two things: than my best, and than last heat. The PB sentence
answered only the first: on synthetic heats of the demo circuit opened in one jailed window, a
12:20 heat 0.21 s quicker than the 11:20 one read "0.19 s off your personal best there" and
nothing else. The sentence (one function for the card, the debrief and the session notice) now
carries "· 0.21 s quicker than your 11:20 session (0:45.395)" when the session that STARTED last
before this one, at this track and this way round, is not the PB session itself.

Best lap against best lap, as the PB line: the session-median comparison stays refused
(studio/docs/refused-2026-09.md §8).

Pinned here:
  1. "previous" is by recording start, never by the order rows were opened; the latest earlier one;
     never this recording, a later session, another track, the other way round, or an untrusted
     best; none when which one came last cannot be told;
  2. the words: quicker / slower / level inside 0.07 s; the clock on the same day, the day
     otherwise; no clause when the previous session IS the PB, nor for a first session;
  3. the clause reaches the card's body, the debrief's sentence and the session notice.

Run: python tests/test_quicker_than_last_heat.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio import library  # noqa: E402
from studio._signal import fmt_time  # noqa: E402

DAY = "2026-06-01"
# 10:00, 10:40, 11:20 and 12:00 UTC on 1 Jun 2026
T10, T1040, T1120, T12 = 1780308000000, 1780310400000, 1780312800000, 1780315200000


def _row(fp, best, start_ms=None, date=DAY, **kw):
    e = {"fingerprint": fp, "stem": fp, "track": "Stadium", "date": date, "lap_count": 12,
         "best": best, "theoretical": best - 0.3, "verified": True, "degraded": False,
         "dropout": False, "paths": [f"/nowhere/{fp}.MP4"], **kw}
    if start_ms is not None:
        e["start_ms"] = start_ms
    return e


def _hhmm(ms):
    return library.clock_hhmm(ms)


# ------------------------------------------------------------------- 1. which session is previous
def test_previous_is_the_latest_session_that_started_before_this_one():
    rows = [_row("H2", 45.40, T1040), _row("H1", 45.00, T10), _row("H4", 44.80, T12)]
    # opened in any order: H4 (12:00) is later, so never "previous" of an 11:20 session
    got = library.previous_session({"entries": rows}, "Stadium", "H3", DAY, T1120)
    assert got["fingerprint"] == "H2", got
    # never this recording's own row, another track, the other way round, or an untrusted best
    for bad in (_row("H3", 45.1, T1120 - 1), _row("X", 45.1, T1120 - 1, track="Elsewhere"),
                _row("R", 45.1, T1120 - 1, rotation=-1), _row("P", 45.1, T1120 - 1, verified=False),
                _row("D", 45.1, T1120 - 1, dropout=True)):
        got = library.previous_session({"entries": rows + [bad]}, "Stadium", "H3", DAY, T1120,
                                       rotation=1)
        assert got["fingerprint"] == "H2", (bad["fingerprint"], got)
    # an earlier day, no clock on it: still earlier
    old = _row("D0", 45.2, date="2026-05-25")
    assert library.previous_session({"entries": [old]}, "Stadium", "H1", DAY, T10)["stem"] == "D0"
    # two rows of the latest day, one with no clock: which came last cannot be told
    legacy = _row("L", 45.3, date="2026-05-25")
    assert library.previous_session({"entries": [old, legacy]}, "Stadium", "H1", DAY, T10) is None
    print("ok previous: by start, latest earlier, trusted, this track and way only")


def test_previous_is_ordered_by_the_start_clock_not_a_stored_day():
    """Review F2: a row stored under another time zone's local day ("2026-06-02" for a 13:00 UTC
    start) outranked a later-starting row stored "2026-06-01" (13:30 UTC), and the clause named
    "your 2 Jun session" on a 1 Jun session. Two clocks order two rows; the day only without one."""
    t13, t1330, t14 = T10 + 3 * 3_600_000, T10 + 3 * 3_600_000 + 1_800_000, T10 + 4 * 3_600_000
    rows = [_row("TZ", 45.4, t13, date="2026-06-02"), _row("B", 45.5, t1330)]
    got = library.previous_session({"entries": rows}, "Stadium", "N", DAY, t14)
    assert got["fingerprint"] == "B", got
    print("ok order: the start clock, not the stored day")


def test_a_same_day_row_with_no_clock_makes_previous_unknowable():
    """Review F3: a same-day row with no start clock was dropped by the ordering filter before the
    "can't tell which came last" check saw it, so the clause named "your 31 May session" though a
    heat of today may be the previous one. Now it makes the previous session unknowable: no clause."""
    rows = [_row("P", 44.9, date="2026-05-20"), _row("M", 45.4, T10 - 86_400_000, date="2026-05-31"),
            _row("L", 45.5, date=DAY)]
    assert library.previous_session({"entries": rows}, "Stadium", "N", DAY, T12) is None
    print("ok ambiguity: a clockless row of today means no clause")


def test_a_session_starting_within_a_minute_is_the_same_heat():
    """Review F4: two cameras on one heat start seconds apart; neither is the other's previous."""
    rows = [_row("H2", 45.4, T1040), _row("CAM2", 45.1, T1120 - 20_000)]
    got = library.previous_session({"entries": rows}, "Stadium", "CAM1", DAY, T1120)
    assert got["fingerprint"] == "H2", got
    print("ok same heat: a start within 60 s is skipped")


# ------------------------------------------------------------------------------------ 2. words
def test_the_clause_says_quicker_slower_or_level_and_names_the_session():
    pb = _row("H1", 45.00, T10)
    h3 = _row("H3", 45.395, T1120)
    idx = {"entries": [pb, _row("H2", 45.40, T1040), h3]}
    quicker = library.pb_standing_for(True, idx, "Stadium", 45.187, fingerprint_key="H4",
                                      date=DAY, start_ms=T12)
    text = library.pb_standing_text(quicker, fmt_time)
    assert text == (f"Best lap 0:45.187 at Stadium, 0.19 s off your personal best there (0:45.000) "
                    f"· 0.21 s quicker than your {_hhmm(T1120)} session (0:45.395)."), text
    slower = library.pb_standing_for(True, {"entries": [pb, _row("H2", 45.10, T1040)]}, "Stadium",
                                     45.395, fingerprint_key="H3", date=DAY, start_ms=T1120)
    assert library.previous_clause(slower, fmt_time) == \
        f"0.30 s slower than your {_hhmm(T1040)} session (0:45.100)"
    level = library.pb_standing_for(True, {"entries": [pb, _row("H2", 45.40, T1040)]}, "Stadium",
                                    45.36, fingerprint_key="H3", date=DAY, start_ms=T1120)
    assert library.previous_clause(level, fmt_time) == \
        f"level with your {_hhmm(T1040)} session (0:45.400)"
    # another day: the day, not a clock
    other = library.pb_standing_for(True, {"entries": [_row("P", 45.0, date="2026-05-20"),
                                                 _row("D0", 45.6, date="2026-05-25")]},
                                    "Stadium", 45.3, fingerprint_key="N", date="2026-06-02",
                                    start_ms=T12 + 86_400_000)
    assert library.previous_clause(other, fmt_time) == \
        "0.30 s quicker than your 25 May session (0:45.600)"
    # the previous session IS the PB: the PB half already says it
    is_pb = library.pb_standing_for(True, {"entries": [_row("H2", 45.00, T1040)]}, "Stadium",
                                    45.3, fingerprint_key="H3", date=DAY, start_ms=T1120)
    assert library.previous_clause(is_pb, fmt_time) == "" and " · " not in \
        library.pb_standing_text(is_pb, fmt_time)
    # a first session has no previous one to stand against
    first = library.pb_standing_for(True, {"entries": []}, "Stadium", 45.3, fingerprint_key="H1",
                                    date=DAY, start_ms=T10)
    assert library.previous_clause(first, fmt_time) == ""
    print("ok words:", text)


# ------------------------------------------------------------------------- 3. every surface
def test_the_card_the_debrief_and_the_notice_carry_it():
    idx = {"entries": [_row("D0", 45.50, date="2026-05-25"), _row("H2", 45.70, T1040)]}
    beat = library.pb_moment_for(True, idx, "Stadium", 45.395, fingerprint_key="H3", date=DAY,
                                 start_ms=T1120)
    title, body = library.pb_moment_text(beat, fmt_time)
    assert title == "New personal best!", title
    assert body.endswith(f"· 0.30 s quicker than your {_hhmm(T1040)} session (0:45.700)."), body
    standing = library.pb_standing_for(True, idx, "Stadium", 45.395, fingerprint_key="H3",
                                       date=DAY, start_ms=T1120)
    assert library.pb_standing_text(standing, fmt_time).endswith(
        f"· 0.30 s quicker than your {_hhmm(T1040)} session (0:45.700).")
    assert library.pb_standing_notice(standing, fmt_time).endswith("session (0:45.700)")
    print("ok surfaces:", body)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} PREVIOUS-SESSION TESTS PASSED")
