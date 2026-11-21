"""Unit tests for studio.data_quality.TimingQuality — the timing-ACCURACY axis (PR: data-quality
signal). Pure value object, no Qt / no pacer: classify a recording's per-sample clock provenance
(GPS9 true clock vs media-clock fallback) + the quality gate's dropped-fix fraction into the
UI-facing `degraded` verdict + the human-readable banner concern lines. Orthogonal to the
start-line TRUST surface (Session.timing_verified) — see studio/data_quality.py.

Run:  python tests/test_data_quality.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio import data_quality as dq  # noqa: E402
from studio.data_quality import TimingQuality  # noqa: E402


def test_default_is_high_quality():
    """The default verdict (a from-scratch Session / no paths / empty trace) is GPS9 true-clock,
    no dropped fixes — NOT degraded, so the UI is visually identical to today."""
    q = TimingQuality()
    assert q.clock == dq.GPS9_TRUECLOCK
    assert q.dropped_fraction == 0.0
    assert not q.media_clock
    assert not q.low_gps_quality
    assert not q.degraded
    assert q.concerns() == []
    print("test_default_is_high_quality OK")


def test_media_clock_fallback_is_degraded():
    """An older GPS5 camera (no GPS9) fell back to the media-clock (packet-spread) axis →
    media_clock True, degraded, and a banner concern line that names the cause + what it can cost a
    lap (the one shared bound, MEDIA_CLOCK_LAP_ERROR)."""
    q = TimingQuality(clock=dq.MEDIA_CLOCK_FALLBACK)
    assert q.media_clock and q.degraded
    assert not q.low_gps_quality
    concerns = q.concerns()
    assert len(concerns) == 1
    assert "video clock" in concerns[0] and dq.MEDIA_CLOCK_LAP_ERROR in concerns[0], concerns[0]
    print("test_media_clock_fallback_is_degraded OK")


def test_the_media_clock_warning_quotes_one_bound_and_no_drift():
    """Every media-clock sentence quotes the ONE bound and none of them claims a clock drift.

    The copy said "lap times may drift ~0.1%" (and "runs ~0.1% fast") on five surfaces, and the
    repo's own measurement contradicts it: the video and GPS clocks' RATES agree to ~27 ppm
    (studio/load.py's head comment; 1.8 ms on a 68 s lap, 0.1 % would be 68 ms). What the fallback
    really costs is PLACEMENT — a ~1 s GPS packet's fixes are spread evenly, so each is placed only
    to about ±0.05 s (studio/media_clock.py), and a lap time is two such instants. So the sentence
    is a hedged bound, and it is one constant so the five surfaces cannot drift apart again.

    The exports' [e] key (laps.csv's trailer, the session report) is the same warning in ASCII, and
    it said the clock "runs slightly fast" after the five had stopped — so it is held here too.

    Nor may a sentence call the clock unmeasured on such a camera: the tooltip and the [e] key said
    "not measured on such a camera" while the packet-spread clock IS measured on the nine bundled
    GPS5 clips (tests/test_load_pipeline.py); what no GPS5 lap has met is official timing."""
    bound = dq.MEDIA_CLOCK_LAP_ERROR
    claims = ("0.1%", "drift", "fast", "measured on such a camera")
    for q in (TimingQuality(clock=dq.MEDIA_CLOCK_FALLBACK),
              TimingQuality(clock=dq.MEDIA_CLOCK_FALLBACK, dropped_fraction=0.5)):
        texts = {"concerns[0]": q.concerns()[0], "summary": q.summary(), "detail": q.detail(),
                 "cost": q.cost()}
        for name, text in texts.items():
            assert not any(c in text for c in claims), (
                f"{name} makes a retired claim {[c for c in claims if c in text]}: {text!r}")
            # The combined one-line summary names both concerns and has no room for the bound;
            # its detail/cost/concern lines carry it.
            if not (name == "summary" and q.low_gps_quality):
                assert bound in text, f"{name} does not quote the one bound {bound!r}: {text!r}"
    meaning = dq.MARK_MEANING[dq.MARK_ESTIMATED]
    assert bound in meaning and not any(c in meaning for c in claims), (
        f"the exports' [e] key does not quote the one bound, or makes a retired claim: {meaning!r}")
    # The map banner is setWordWrap(False): its media-clock line may not grow past the 66
    # characters it had, or it clips on a narrow map.
    summary = TimingQuality(clock=dq.MEDIA_CLOCK_FALLBACK).summary()
    assert len(summary) <= 66, f"{len(summary)} chars: {summary!r}"
    print("test_the_media_clock_warning_quotes_one_bound_and_no_drift OK")


def test_low_gps_quality_threshold():
    """The dropped-fix concern fires at/above DROPPED_FIX_CONCERN_FRAC and not below — a few
    rejected fixes on an otherwise clean GPS9 trace is normal and must NOT raise a banner."""
    below = TimingQuality(dropped_fraction=dq.DROPPED_FIX_CONCERN_FRAC - 0.001)
    assert not below.low_gps_quality and not below.degraded and below.concerns() == []
    at = TimingQuality(dropped_fraction=dq.DROPPED_FIX_CONCERN_FRAC)
    assert at.low_gps_quality and at.degraded
    assert "%" in at.concerns()[0] and "rejected" in at.concerns()[0]
    print("test_low_gps_quality_threshold OK")


def test_both_concerns_stack_media_clock_first():
    """When BOTH concerns apply the banner stacks two lines, most-significant first (the
    media-clock/headline-accuracy loss before the fix-rejection note)."""
    q = TimingQuality(clock=dq.MEDIA_CLOCK_FALLBACK, dropped_fraction=0.5)
    assert q.degraded
    concerns = q.concerns()
    assert len(concerns) == 2
    assert "video clock" in concerns[0]            # media-clock line first
    assert "50%" in concerns[1] and "rejected" in concerns[1]
    print("test_both_concerns_stack_media_clock_first OK")


def test_a_recording_with_no_gps_trace_is_its_own_verdict_not_the_default_one():
    """NO GPS AT ALL must not read as the validated headline path.

    `load_recording` builds `TimingQuality()` before it knows whether the trace survives and
    returns early when the gate and the trim leave nothing — so this state used to inherit the
    DEFAULT, whose clock is GPS9_TRUECLOCK. Measured through the real load path on the bundled
    `karma.mp4` (0 GPS fixes): the DATA TRUST card printed "Timing: GPS9 true clock · 0% of moving
    fixes rejected". The card was vouching for the best timing the app has on a file with not one
    satellite fix in it.

    It is also NOT the media-clock fallback: that names a real axis built from the video clock,
    and reusing its "estimated" wording here would promise lap times that do not exist."""
    q = TimingQuality(clock=dq.NO_GPS_TRACE)
    assert q.no_gps, "the no-GPS state must be nameable"
    assert not q.media_clock, "no GPS is not the video-clock fallback — no axis was built at all"
    assert q.degraded, "a recording with no GPS trace is not high-quality timing"

    concerns = q.concerns()
    assert concerns, "a state this severe must give the banner something to say"
    assert "GPS" in concerns[0], concerns
    # It must not borrow the media-clock sentence: there is no clock whose lap error to bound
    # when no axis was built.
    assert dq.MEDIA_CLOCK_LAP_ERROR not in concerns[0], concerns[0]
    for text in (q.summary(), q.detail()):
        assert text, "summary/detail carry the map banner and the tooltips"
        assert "GPS9" not in text, text

    # And the default is untouched — the good case must still read as the good case.
    assert not TimingQuality().no_gps
    assert not TimingQuality(clock=dq.MEDIA_CLOCK_FALLBACK).no_gps
    print("test_a_recording_with_no_gps_trace_is_its_own_verdict_not_the_default_one OK")


def test_the_strip_does_not_blame_the_gps5_era_when_no_fix_ever_arrived():
    """Two different absences, one sentence — until now.

    `reports_quality` is False both when a GPS5-era camera writes fixes with no DOP/fix fields AND
    when no fix arrived at all, and `summary()` printed the GPS5-era explanation for both. Measured
    on `karma.mp4`, which carries NO GPS stream whatsoever, the card said "this camera writes no
    per-sample GPS quality — the GPS5-era stream carries neither a fix type nor a DOP": an
    attribution to a stream that is not there, on a camera we measured nothing about."""
    nothing = dq.build_quality_timeline([], [], [], span_s=12.0)
    assert len(nothing), "a span with no fixes still covers the recording"
    assert not nothing.reports_quality
    text = nothing.summary()
    assert "GPS5" not in text, f"no fix arrived — the GPS5-era stream is not the reason: {text!r}"
    assert "no gps fix" in text.lower(), text

    # The genuine GPS5-era case is unchanged: fixes DID arrive, they just carry no quality fields.
    gps5 = dq.build_quality_timeline([0.5, 1.5], [False, False], [-1.0, -1.0], span_s=12.0)
    assert not gps5.reports_quality
    assert "GPS5" in gps5.summary(), gps5.summary()
    print("test_the_strip_does_not_blame_the_gps5_era_when_no_fix_ever_arrived OK")


def _census(rejected_where):
    """100 s of driving at 10 Hz on the telemetry axis, rejected where `rejected_where(t)`."""
    import numpy as np
    t = np.arange(1000) * 0.1
    return dq.FixCensus(t=t, rejected=rejected_where(t), spacing_s=0.1)


def test_the_share_is_judged_over_the_timed_laps():
    """Fixes rejected before the first crossing time no lap: 20 s of them are 20% of the drive,
    which used to read "GPS quality low" for laps that are untouched. Over the laps (30-90 s) the
    share is 0, and the session notice names the 20 s instead (`outside_laps`)."""
    q = TimingQuality(_census=_census(lambda t: t < 20.0)).over_laps((30.0, 90.0))
    assert q.in_laps and q.dropped_fraction == 0.0 and not q.degraded, q
    assert q.outside_laps() == ("GPS fixes rejected for 20 s of driving outside your laps, so a "
                                "lap there may be missing"), q.outside_laps()
    # A little rejected driving that would not have downgraded the drive is nothing to state.
    small = TimingQuality(_census=_census(lambda t: t < 5.0)).over_laps((30.0, 90.0))
    assert small.outside_laps() == "", small.outside_laps()
    print("test_the_share_is_judged_over_the_timed_laps OK")


def test_rejections_at_the_edge_of_the_laps_keep_the_whole_drive_verdict():
    """A rejected fix within the guard band beyond either edge reaches that crossing through the
    smoother, so the laps are not clear of it and the whole drive decides; just past the band the
    laps decide. The band is the smoother's own reach, doubled, so a change of window moves it."""
    from studio._signal import SMOOTH_WINDOW
    assert dq.EDGE_GUARD_FIXES == 2 * (SMOOTH_WINDOW // 2 + 1)
    band = dq.EDGE_GUARD_FIXES * 0.1
    for rejected in (lambda t: t < 30.0 - 0.5, lambda t: t > 90.0 + 0.4,
                     lambda t: t < 30.0 - band + 0.05):
        q = TimingQuality(_census=_census(rejected)).over_laps((30.0, 90.0))
        assert not q.in_laps and q.low_gps_quality, (q.dropped_pct(), q)
        assert q.untrusted().endswith("% of fixes rejected)"), q.untrusted()
    clear = TimingQuality(_census=_census(lambda t: t < 30.0 - band - 0.15)).over_laps((30.0, 90.0))
    assert clear.in_laps and not clear.degraded, clear
    print("test_rejections_at_the_edge_of_the_laps_keep_the_whole_drive_verdict OK")


def test_rejections_inside_the_laps_still_downgrade_in_plain_words():
    """6 s rejected inside a 60 s span of laps is 10% of the laps' fixes (6% of the whole drive):
    low, and every sentence says the share is of the fixes inside the laps."""
    q = TimingQuality(_census=_census(lambda t: (t >= 40.0) & (t < 46.0))).over_laps((30.0, 90.0))
    assert q.low_gps_quality and q.dropped_pct() == 10, q
    assert q.untrusted() == "GPS quality low (10% of fixes inside your laps rejected)", q.untrusted()
    assert q.summary() == ("GPS quality low — 10% of fixes inside your laps rejected; times may be "
                           "less accurate."), q.summary()
    assert "10% of fixes inside your laps were rejected" in q.detail(), q.detail()
    assert "10% of fixes inside your laps were rejected" in q.concerns()[0], q.concerns()
    assert q.outside_laps() == "", "a low verdict already says the GPS was poor"
    print("test_rejections_inside_the_laps_still_downgrade_in_plain_words OK")


def test_without_two_crossings_the_whole_drive_decides():
    """No timed laps (fewer than two crossings): the share stays the whole drive's, worded as
    before; and a verdict with no census (a stand-in, the default) is returned unchanged."""
    q = TimingQuality(_census=_census(lambda t: t < 20.0)).over_laps(None)
    assert not q.in_laps and abs(q.dropped_fraction - 0.2) < 1e-12, q
    assert q.untrusted() == "GPS quality low (20% of fixes rejected)", q.untrusted()
    assert q.outside_laps() == "", q.outside_laps()
    plain = TimingQuality(dropped_fraction=0.5)
    assert plain.over_laps((30.0, 90.0)) is plain
    print("test_without_two_crossings_the_whole_drive_decides OK")


def test_frozen_value_object():
    """It's an immutable verdict — safe to share one default instance as a class attribute."""
    q = TimingQuality()
    try:
        q.clock = dq.MEDIA_CLOCK_FALLBACK  # type: ignore[misc]
    except Exception:
        print("test_frozen_value_object OK")
        return
    raise AssertionError("TimingQuality must be frozen/immutable")


# ========================================================= the QUALITY-MARKER VOCABULARY
# The house marks ((est) / muted-italic provisional / ⚠ / ⊘) are unchanged and are guarded where
# they live. These pin the EXPORT half: which Analysis Function codes pacer adopted, which it
# refused, and the [b] condition it had no name for at all.
class _FakeMap:
    """A stand-in ChapterMap — only `desynced_chapters` is read."""

    def __init__(self, desynced=()):
        self._d = list(desynced)

    def desynced_chapters(self):
        return list(self._d)


class _FakeSession:
    """A duck-typed session: every field `session_marks` / `break_in_series` reads, nothing else."""

    def __init__(self, *, verified=True, quality=None, skipped=(), chapters=None, dropouts=()):
        self.timing_verified = verified
        self.timing_quality = quality if quality is not None else TimingQuality()
        self.skipped_chapters = list(skipped)
        self.chapters = chapters
        self._dropouts = set(dropouts)

    def dropout_lap_ids(self):
        return set(self._dropouts)


def test_the_vocabulary_adopts_four_codes_and_refuses_the_rest():
    """Exactly the four codes the per-marker table adopts exist as constants — and the five it
    refuses ([x] [z] [r] [f] [c]) are NOT defined, so no surface can emit one by reflex.

    That absence is the load-bearing half. A module exporting all nine would make "we adopted the
    standard" true and "we decided per marker" false, and the next writer would reach for [x]
    where the app already prints an em-dash from one source."""
    adopted = {dq.MARK_ESTIMATED, dq.MARK_PROVISIONAL, dq.MARK_LOW_RELIABILITY,
               dq.MARK_BREAK_IN_SERIES}
    assert adopted == {"[e]", "[p]", "[u]", "[b]"}, adopted
    assert set(dq.MARK_ORDER) == adopted, "MARK_ORDER must cover exactly the adopted codes"
    assert set(dq.MARK_MEANING) == adopted, "every adopted code needs a meaning"
    refused = [v for k, v in vars(dq).items()
               if k.startswith("MARK_") and isinstance(v, str) and v in
               ("[x]", "[z]", "[r]", "[f]", "[c]")]
    assert not refused, f"a refused code became a constant: {refused}"
    print("test_the_vocabulary_adopts_four_codes_and_refuses_the_rest OK (4 adopted, 5 refused)")


def test_marker_meanings_are_ascii_because_they_reach_laps_csv():
    """laps.csv is pure ASCII and pinned that way. These strings are written into its trailer, so
    an em-dash here fails an export test a long way from this file — assert it at the source."""
    for code, meaning in dq.MARK_MEANING.items():
        meaning.encode("ascii")            # raises the moment a typographic mark creeps in
        assert meaning[:1].islower(), f"{code} meaning should read as a clause, got {meaning!r}"
    print("test_marker_meanings_are_ascii_because_they_reach_laps_csv OK")


def test_break_in_series_names_both_instances_and_refuses_a_plain_seam():
    """[b] fires on the two genuine breaks and NOT on an ordinary chaptered recording.

    The negative is the point. load.py measured that a chapter seam does not break a GPS9 run and
    steps the axis by −0.000127 s, so marking every multi-chapter session [b] would fire on both
    D24 recordings and mean nothing — the failure the refused [x]/[z] codes are refused for."""
    clean = _FakeSession(chapters=_FakeMap())
    assert dq.break_in_series(clean) is None, "an in-sync chaptered recording is not a break"
    assert dq.break_in_series(_FakeSession()) is None, "no chapter map is not a break"

    skipped = dq.break_in_series(_FakeSession(skipped=["GX010060.MP4"]))
    assert skipped and "could not be read" in skipped and "closes over the gap" in skipped
    assert "1 chapter " in skipped, f"singular agreement: {skipped!r}"

    desync = dq.break_in_series(_FakeSession(chapters=_FakeMap([("GX020060.MP4", 4.2)])))
    assert desync and "telemetry than video" in desync
    assert "carries" in desync, f"singular agreement: {desync!r}"

    both = dq.break_in_series(
        _FakeSession(skipped=["a.MP4", "b.MP4"], chapters=_FakeMap([("c.MP4", 4.2)])))
    assert both.count(";") == 1, f"two reasons join with one separator: {both!r}"
    assert "2 chapters" in both and "were left out" in both, both
    both.encode("ascii")  # it reaches laps.csv too
    print("test_break_in_series_names_both_instances_and_refuses_a_plain_seam OK")


def test_a_stand_in_chapter_object_never_fails_an_export():
    """A dozen suites hand Session a stand-in `chapters` (a bare string in one). Asking it for a
    break must degrade to "no break", never raise in the middle of writing a file."""
    class _Hostile:
        def desynced_chapters(self):
            raise RuntimeError("nope")

    assert dq.break_in_series(_FakeSession(chapters="GX010060")) is None
    assert dq.break_in_series(_FakeSession(chapters=_Hostile())) is None
    print("test_a_stand_in_chapter_object_never_fails_an_export OK")


def test_session_marks_split_the_estimated_and_low_reliability_verdicts():
    """[e] is the media-clock fallback and [u] the rejected-fix share — the SAME split
    TimingQuality.detail() already makes, not a new one. A true-clock recording with bad fixes
    must not be called "estimated"; that overclaim is exactly what M3 fixed in the tooltip."""
    assert dq.session_marks(_FakeSession()) == []
    media = _FakeSession(quality=TimingQuality(clock=dq.MEDIA_CLOCK_FALLBACK))
    assert dq.session_marks(media) == [dq.MARK_ESTIMATED]
    low = _FakeSession(quality=TimingQuality(dropped_fraction=0.5))
    assert dq.session_marks(low) == [dq.MARK_LOW_RELIABILITY]
    assert dq.session_marks(_FakeSession(verified=False)) == [dq.MARK_PROVISIONAL]
    both = _FakeSession(verified=False,
                        quality=TimingQuality(clock=dq.MEDIA_CLOCK_FALLBACK, dropped_fraction=0.5),
                        skipped=["x.MP4"])
    # Canonical order, worst footing first — not the order the checks happen to run in.
    assert dq.session_marks(both) == ["[p]", "[e]", "[b]", "[u]"], dq.session_marks(both)
    print("test_session_marks_split_the_estimated_and_low_reliability_verdicts OK")


def test_lap_marks_add_the_laps_own_dropout_to_the_sessions():
    """A dropout is a fact about ONE lap; the trust verdicts are facts about the recording. The
    split is what lets an export state a session-wide caveat once and still mark the odd lap."""
    s = _FakeSession(verified=False, dropouts={2})
    assert dq.lap_marks(s, 0) == ["[p]"]
    assert dq.lap_marks(s, 2) == ["[p]", "[u]"]
    # A session ALREADY carrying [u] does not gain a second one from a dropout lap.
    low = _FakeSession(quality=TimingQuality(dropped_fraction=0.5), dropouts={1})
    assert dq.lap_marks(low, 1) == ["[u]"], dq.lap_marks(low, 1)
    # The caller may pass the dropout set in (a writer loops hundreds of rows).
    assert dq.lap_marks(s, 7, {7}) == ["[p]", "[u]"]
    print("test_lap_marks_add_the_laps_own_dropout_to_the_sessions OK")


def test_the_key_lists_only_the_codes_actually_present():
    """A legend naming four marks on a file that carries none teaches the reader that the marks
    are decoration. `mark_key` returns the present ones, in canonical order, with their meanings."""
    assert dq.mark_key([]) == []
    key = dq.mark_key({"[u]", "[p]"})
    assert [c for c, _m in key] == ["[p]", "[u]"], key
    assert all(m == dq.MARK_MEANING[c] for c, m in key)
    print("test_the_key_lists_only_the_codes_actually_present OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} DATA-QUALITY TESTS PASSED")
