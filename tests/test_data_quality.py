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
    """An older GPS5 camera (no GPS9) fell back to the ~0.1%-fast media clock → media_clock True,
    degraded, and a banner concern line that names the cause + the ~0.1% drift."""
    q = TimingQuality(clock=dq.MEDIA_CLOCK_FALLBACK)
    assert q.media_clock and q.degraded
    assert not q.low_gps_quality
    concerns = q.concerns()
    assert len(concerns) == 1
    assert "video clock" in concerns[0] and "0.1%" in concerns[0]
    print("test_media_clock_fallback_is_degraded OK")


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
