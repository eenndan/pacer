"""The personal-best celebration: WHOSE best it compares against, and whether it fires more than once.

Two defects from the 2026-09-07 critical review (§3.2, §3.3), both of which shipped green:

§3.2 — A RECORDING CELEBRATED A PB AGAINST ITSELF. `library.pb_moment` compares by TRACK, and a
recording is in the index the moment it has been analysed once. So opening one chapter (22 laps,
1:08.771) and then clicking the status bar's own "Load full recording" (66 laps of the SAME
recording, 1:08.201) read that first entry as the "previous best" and announced "0.57 s faster than
your previous best (1:08.771)" — under a "Share your PB →" button, i.e. a shareable false PB.
Opening a second chapter of one outing did the same: the fingerprint strips the chapter index by
construction (``library.fingerprint``: GX010062 / GX020062 / GX030062 all key to GX0062), which is
exactly why ONE identity guard kills both paths. `_update_library`'s docstring had promised this
could not happen ("the recording being added can't be its own prior PB") on the strength of
deciding the moment BEFORE the upsert — which the partial→full case walks straight through,
because the prior load did the upserting.

§3.3 — AND THE WINDOW ONLY EVER CELEBRATED ONCE. `PBToast.dismiss()` ends in `deleteLater()` while
`StudioWindow._pb_toast` kept the Python wrapper; the next moment's `old.dismiss()` raised
"Internal C++ object (QTimer) already deleted" INSIDE the try, and the blanket except returned
before the new card was built. Measured on the real method: second moment → RuntimeError → zero
toasts on screen. Compounding with §3.2, the false partial→full toast was usually the one that
spent the single slot, so the genuine PB was the one that got eaten.

Pinned here:
  * the identity gate on the decision itself (`library.pb_moment` / `pb_moment_for`), including
    that a DIFFERENT recording still sets a PB and a first-ever one still gets its "first" moment;
  * both §3.2 repro paths through the REAL `StudioWindow._update_library` + the REAL
    `Session.library_entry` (real chapter files on disk, so the fingerprint derivation is real),
    asserting NO moment fires;
  * THE FALSIFIER FOR THE FIX ITSELF, on the same gesture: with a previous day's recording holding
    the track best, the full chain that genuinely beats IT must still celebrate — the review's
    repair (suppress whenever the index already holds this fingerprint) kills that, so the guard
    partitions the index by identity and compares against the OTHERS instead;
  * a second celebration on a REAL StudioWindow after the first card has been dismissed AND its
    deferred delete processed (the exact state §3.3 dies on — asserted as a precondition, so the
    test cannot pass vacuously), the rapid-reload replace it must not regress, and the containment
    that keeps a failing dismiss from stranding the load behind it.

Every test points the library index at a TEMP dir through the single ``library._app_support_dir``
seam, and the module redirects both that seam and ``prefs`` for its whole lifetime — the suite
never reads or writes the user's real app-support dir.

Run: python tests/test_pb_moment_lifecycle.py
"""
import contextlib
import io
import os
import sys
import tempfile
import time
from types import SimpleNamespace

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The toast half builds a REAL StudioWindow; PACER_NO_MEDIA must be set before studio imports
# (PlayerPane reads it once, at construction).
os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()

import shiboken6  # noqa: E402
from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from studio import library, prefs  # noqa: E402
from studio.overlays import PBToast  # noqa: E402

# BOTH persistence seams are redirected MODULE-WIDE, before any window exists — the library index
# (which `_update_library` writes) and prefs (which the window and its dialogs read and write).
# The per-test `_temp_library` below gives each test a fresh index on top of this; the module-wide
# redirect is what makes "this file never touches ~/Library/Application Support/pacer" true for
# every line of it, not just the ones that ask.
# (The TemporaryDirectory objects are held at module scope so their finalizers remove them at exit.)
_LIB_TMP = tempfile.TemporaryDirectory(prefix="pacer-test-pb-lib-")
_PREFS_TMP = tempfile.TemporaryDirectory(prefix="pacer-test-pb-prefs-")
library._app_support_dir = lambda: _LIB_TMP.name
prefs._app_support_dir = lambda: _PREFS_TMP.name

_TRACK = "Sandown Park"


def _pacer_available() -> bool:
    """The app/session halves need the built bindings; the pure-decision half does not. Same gate
    tests/test_library.py uses so this file stays runnable in a pacer-free checkout."""
    try:
        import pacer  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 — any import failure means "no built bindings here"
        return False


@contextlib.contextmanager
def _temp_library():
    """Point the library index at a FRESH temp dir for the duration of one test, and put the seam
    back afterwards (to the module-wide temp redirect above — never to the real app-support dir)."""
    orig = library._app_support_dir
    with tempfile.TemporaryDirectory(prefix="pacer-test-pb-") as d:
        library._app_support_dir = lambda: d
        try:
            yield d
        finally:
            library._app_support_dir = orig


def _entry(stem, best, laps, track=_TRACK, date="2026-09-05"):
    """A valid library entry for `stem`, keyed by its chapter-invariant fingerprint."""
    return {"fingerprint": library.fingerprint(stem), "stem": stem, "track": track, "date": date,
            "lap_count": laps, "best": best, "theoretical": None, "verified": True,
            "degraded": False, "dropout": False, "paths": [f"/m/{stem}.MP4"]}


def _index(*entries):
    return {"version": library.VERSION, "entries": list(entries)}


# ================================================== A. the decision (pure; no Qt, no pacer)
def test_a_recording_is_not_its_own_previous_best():
    """§3.2's core: the partial load's own entry must not be the bar the full load beats.

    The chapter is in the index at 68.771; the full recording arrives at 68.201 under the SAME
    fingerprint. Without the identity the comparison finds 68.771 and celebrates (the shipped
    defect, asserted here so the mechanism stays pinned); with it, nothing is celebrated."""
    idx = _index(_entry("GX010062", 68.771, 22))
    blind = library.pb_moment(idx, _TRACK, 68.201)
    assert blind is not None and blind["kind"] == "beat" and blind["prior"] == 68.771, blind
    assert library.pb_moment(idx, _TRACK, 68.201, "GX0062") is None, \
        "the full recording celebrated a PB over the chapter it just chained"
    # Every chapter of one outing keys to that same identity, which is why one guard is enough.
    for stem in ("GX010062", "GX020062", "GX030062"):
        assert library.pb_moment(idx, _TRACK, 68.201, library.fingerprint(stem)) is None, stem
    # The trust-gated entry point forwards it (that is the one the app calls).
    assert library.pb_moment_for(True, idx, _TRACK, 68.201, fingerprint_key="GX0062") is None
    # And the index itself is untouched: prior_best still reports what the track has recorded.
    assert library.prior_best(idx, _TRACK) == 68.771
    print("test_a_recording_is_not_its_own_previous_best OK")


def test_a_different_recording_on_the_same_track_still_sets_a_pb():
    """The guard is about IDENTITY, not about suppressing PBs: a genuinely different recording on
    the same track beats the stored best exactly as before."""
    idx = _index(_entry("GX010062", 68.771, 22))
    m = library.pb_moment(idx, _TRACK, 68.201, "GX0063")
    assert m is not None and m["kind"] == "beat", m
    assert m["prior"] == 68.771 and abs(m["improvement"] - 0.57) < 1e-9, m
    assert library.pb_moment_for(True, idx, _TRACK, 68.201, fingerprint_key="GX0063") == m
    # Slower / tied still don't celebrate, identity or not.
    assert library.pb_moment(idx, _TRACK, 68.771, "GX0063") is None
    assert library.pb_moment(idx, _TRACK, 70.0, "GX0063") is None
    print("test_a_different_recording_on_the_same_track_still_sets_a_pb OK")


def test_a_first_ever_recording_still_gets_its_first_moment():
    """An identity the index has never seen is a new session: the gentle "first lap logged here"
    acknowledgement is unchanged, including on a completely empty library."""
    m = library.pb_moment(_index(), _TRACK, 68.771, "GX0062")
    assert m == {"kind": "first", "track": _TRACK, "best": 68.771}, m
    other = library.pb_moment(_index(_entry("GX010059", 61.0, 9, track="Todd Road")),
                              _TRACK, 68.771, "GX0062")
    assert other is not None and other["kind"] == "first", other
    print("test_a_first_ever_recording_still_gets_its_first_moment OK")


def test_an_identity_already_logged_is_not_a_first_session_on_a_newly_named_track():
    """The partition is by RECORDING, not by (recording, track): an entry stored with no track —
    the shape File ▸ Save as track… leaves behind before it rewrites the row — is still this
    recording, so a reload that now matches the track finds no OTHER recording's best and stays
    silent rather than announcing "your first session here" about laps already in the library."""
    idx = _index(_entry("GX010062", 68.771, 22, track=None))
    assert library.pb_moment(idx, _TRACK, 68.201, "GX0062") is None
    print("test_an_identity_already_logged_is_not_a_first_session_on_a_newly_named_track OK")


def test_a_better_reload_still_beats_ANOTHER_recordings_best():
    """THE FALSIFIER for the fix. Suppressing whenever the fingerprint is already in the index is
    the obvious repair and it is WRONG: with a previous day's recording holding the track best at
    68.500, this outing's chapter (68.771, correctly silent — it is slower) followed by the full
    chain at 68.201 is a genuine 0.299 s personal best over THAT recording. The prior comes from
    the other recordings, so it still fires, with the right prior and the right improvement."""
    idx = _index(_entry("GX010059", 68.500, 30, date="2026-09-01"),
                 _entry("GX010062", 68.771, 22))
    m = library.pb_moment(idx, _TRACK, 68.201, "GX0062")
    assert m is not None and m["kind"] == "beat", m
    assert m["prior"] == 68.500 and abs(m["improvement"] - 0.299) < 1e-9, m
    # …and the chapter itself, opened first, was correctly silent against that same prior.
    assert library.pb_moment(_index(_entry("GX010059", 68.500, 30)),
                             _TRACK, 68.771, "GX0062") is None
    print("test_a_better_reload_still_beats_ANOTHER_recordings_best OK")


def test_a_recording_that_already_beat_the_prior_does_not_celebrate_twice():
    """The other half of the partition: when this recording's OWN stored best already beats the
    other recordings' best, the improvement has been announced — chaining one more chapter onto it
    must not re-announce a smaller version of the same news. Only a TRUSTWORTHY own entry may
    silence it, since a provisional/degraded/dropout "best" is not in the PB set at all."""
    idx = _index(_entry("GX010059", 68.500, 30), _entry("GX010062", 68.400, 22))
    assert library.pb_moment(idx, _TRACK, 68.201, "GX0062") is None
    # An own entry that only TIED the prior was never announced, so it does not silence this one.
    tied = _index(_entry("GX010059", 68.500, 30), _entry("GX010062", 68.500, 22))
    assert library.pb_moment(tied, _TRACK, 68.201, "GX0062") is not None
    # An untrustworthy own best is not in the PB set, so it cannot silence a real celebration.
    for flag in ({"verified": False}, {"degraded": True}, {"dropout": True}):
        e = _entry("GX010062", 68.400, 22) | flag
        m = library.pb_moment(_index(_entry("GX010059", 68.500, 30), e), _TRACK, 68.201, "GX0062")
        assert m is not None and m["prior"] == 68.500, (flag, m)
    print("test_a_recording_that_already_beat_the_prior_does_not_celebrate_twice OK")


def test_the_decision_without_an_identity_is_unchanged():
    """`fingerprint_key` defaults to None and callers that pass nothing keep the old behaviour
    exactly — the gate is opt-in at the call site, and the trust gates still come first."""
    idx = _index(_entry("GX010062", 68.771, 22))
    assert library.pb_moment(idx, _TRACK, 68.201) is not None
    assert library.pb_moment_for(True, idx, _TRACK, 68.201) is not None
    # Both trust gates still win over the identity gate (an unseen identity, provisional/degraded).
    assert library.pb_moment_for(False, idx, _TRACK, 68.201, fingerprint_key="GX0063") is None
    assert library.pb_moment_for(True, idx, _TRACK, 68.201, degraded=True,
                                 fingerprint_key="GX0063") is None
    # No track / no best still report None with an identity in hand.
    assert library.pb_moment(idx, None, 68.201, "GX0063") is None
    assert library.pb_moment(idx, _TRACK, None, "GX0063") is None
    print("test_the_decision_without_an_identity_is_unchanged OK")


# ============================= B. the two repro paths through the REAL app (needs pacer + files)
def _chapter_files(folder, count=3, number="0062"):
    """`count` real GoPro chapter files on disk — `chapters.discover_siblings` (which
    Session.library_entry derives the fingerprint through) reads the DIRECTORY, so the repro needs
    the siblings to exist. Empty files: nothing here opens them."""
    paths = []
    for i in range(1, count + 1):
        p = os.path.join(folder, f"GX{i:02d}{number}.MP4")
        with open(p, "wb"):
            pass
        paths.append(p)
    return paths


def _window(best, laps, track=_TRACK):
    """A bare StudioWindow (no __init__ — the same idiom tests/test_library.py uses for
    _update_library) whose session reports `best`/`laps` on verified, undegraded timing and builds
    its entry through the REAL Session.library_entry, so the fingerprint is the shipped one."""
    from studio import app as studio_app
    from studio.session import Session

    s = Session.__new__(Session)          # bare; seed only what library_entry reads
    s._valid_cache = list(range(laps))
    s._best_cache = 0
    s.track_name = track
    s.laps = type("L", (), {"lap_time": staticmethod(lambda i: best)})()
    s.session_date = lambda: "2026-09-05"
    s.theoretical_best = lambda: best - 1.0
    s.dropout_lap_ids = lambda: set()

    win = studio_app.StudioWindow.__new__(studio_app.StudioWindow)
    win.session = SimpleNamespace(
        valid_lap_ids=lambda: list(range(laps)),
        timing_verified=True,
        timing_quality=SimpleNamespace(degraded=False),
        library_entry=lambda paths: Session.library_entry(s, paths),
    )
    return studio_app, win


def test_load_full_recording_does_not_celebrate_against_its_own_chapter():
    """§3.2 REPRO 1, end to end: open GX010062 (22 laps, 68.771) then "Load full recording"
    (all three chapters, 66 laps, 68.201). On main the second call returns
    {'kind': 'beat', 'prior': 68.771, 'improvement': 0.57} — the recording beating itself."""
    if not _pacer_available():
        print("skip test_load_full_recording_does_not_celebrate_against_its_own_chapter (no pacer)")
        return
    with _temp_library(), tempfile.TemporaryDirectory(prefix="pacer-test-media-") as media:
        ch = _chapter_files(media)
        studio_app, win = _window(68.771, 22)
        first = studio_app.StudioWindow._update_library(win, [ch[0]])
        assert first is not None and first["kind"] == "first", first

        studio_app, win = _window(68.201, 66)
        moment = studio_app.StudioWindow._update_library(win, ch)
        assert moment is None, f"the full recording celebrated against its own chapter: {moment}"
        # The library still learned the better number — only the celebration was wrong.
        entries = library.load()["entries"]
        assert len(entries) == 1 and entries[0]["fingerprint"] == "GX0062", entries
        assert entries[0]["best"] == 68.201 and entries[0]["lap_count"] == 66, entries[0]
    print("test_load_full_recording_does_not_celebrate_against_its_own_chapter OK")


def test_a_second_chapter_of_one_outing_does_not_celebrate():
    """§3.2 REPRO 2: two chapters of ONE outing opened one after the other (the review drove
    GX030060 then GX020060). They share a fingerprint, so this is the same self-comparison."""
    if not _pacer_available():
        print("skip test_a_second_chapter_of_one_outing_does_not_celebrate (no pacer)")
        return
    with _temp_library(), tempfile.TemporaryDirectory(prefix="pacer-test-media-") as media:
        ch = _chapter_files(media, number="0060")
        studio_app, win = _window(68.771, 21)
        assert studio_app.StudioWindow._update_library(win, [ch[2]]) is not None   # first logged

        studio_app, win = _window(68.201, 22)
        moment = studio_app.StudioWindow._update_library(win, [ch[1]])
        assert moment is None, f"chapter 2 celebrated a PB over chapter 3 of the same outing: {moment}"
    print("test_a_second_chapter_of_one_outing_does_not_celebrate OK")


def test_a_genuinely_new_recording_still_celebrates_through_the_app_path():
    """The other half of the fix: a DIFFERENT recording on the same track must still produce the
    beat moment (with the real improvement), or the repair would have deleted the feature."""
    if not _pacer_available():
        print("skip test_a_genuinely_new_recording_still_celebrates_through_the_app_path (no pacer)")
        return
    with _temp_library(), tempfile.TemporaryDirectory(prefix="pacer-test-media-") as media:
        ch = _chapter_files(media, count=1)
        other = _chapter_files(media, count=1, number="0063")
        studio_app, win = _window(68.771, 22)
        studio_app.StudioWindow._update_library(win, ch)

        studio_app, win = _window(67.900, 20)
        moment = studio_app.StudioWindow._update_library(win, other)
        assert moment is not None and moment["kind"] == "beat", moment
        assert moment["prior"] == 68.771 and abs(moment["improvement"] - 0.871) < 1e-9, moment
        assert len(library.load()["entries"]) == 2
    print("test_a_genuinely_new_recording_still_celebrates_through_the_app_path OK")


def test_load_full_recording_still_beats_a_previous_days_recording():
    """THE FALSIFIER, on the very gesture §3.2 is about, driven end to end. Day 1's GX010059 holds
    the track at 68.500. Day 2: the chapter (68.771) is correctly silent — it is slower — and then
    "Load full recording" (66 laps, 68.201) is a genuine 0.299 s PB over day 1. A guard that
    suppressed on the fingerprint merely being present returns None here and kills the flagship
    moment; partitioning the index by identity keeps it."""
    if not _pacer_available():
        print("skip test_load_full_recording_still_beats_a_previous_days_recording (no pacer)")
        return
    with _temp_library(), tempfile.TemporaryDirectory(prefix="pacer-test-media-") as media:
        day1 = _chapter_files(media, count=1, number="0059")
        ch = _chapter_files(media)
        studio_app, win = _window(68.500, 30)
        studio_app.StudioWindow._update_library(win, day1)          # day 1 sets the track best

        studio_app, win = _window(68.771, 22)
        assert studio_app.StudioWindow._update_library(win, [ch[0]]) is None, "the chapter is slower"

        studio_app, win = _window(68.201, 66)
        moment = studio_app.StudioWindow._update_library(win, ch)
        assert moment is not None and moment["kind"] == "beat", \
            f"the full recording's genuine PB over another recording was swallowed: {moment}"
        assert moment["prior"] == 68.500 and abs(moment["improvement"] - 0.299) < 1e-9, moment
    print("test_load_full_recording_still_beats_a_previous_days_recording OK")


def test_two_chapters_opened_separately_still_beat_a_previous_days_recording():
    """The same falsifier on the cross-chapter path: day 1 at 68.500, then chapter 3 (68.771,
    silent) and chapter 2 (68.201) of one outing. The second chapter must still celebrate over
    day 1 — only the self-comparison with chapter 3 is suppressed."""
    if not _pacer_available():
        print("skip test_two_chapters_opened_separately_still_beat_a_previous_days_recording (no pacer)")
        return
    with _temp_library(), tempfile.TemporaryDirectory(prefix="pacer-test-media-") as media:
        day1 = _chapter_files(media, count=1, number="0059")
        ch = _chapter_files(media, number="0060")
        studio_app, win = _window(68.500, 30)
        studio_app.StudioWindow._update_library(win, day1)

        studio_app, win = _window(68.771, 21)
        assert studio_app.StudioWindow._update_library(win, [ch[2]]) is None, "chapter 3 is slower"

        studio_app, win = _window(68.201, 22)
        moment = studio_app.StudioWindow._update_library(win, [ch[1]])
        assert moment is not None and moment["kind"] == "beat", moment
        assert moment["prior"] == 68.500 and abs(moment["improvement"] - 0.299) < 1e-9, moment
    print("test_two_chapters_opened_separately_still_beat_a_previous_days_recording OK")


# ============================================= C. the toast's lifetime (REAL window; needs pacer)
_M_FIRST = {"kind": "first", "track": _TRACK, "best": 68.771}
_M_BEAT = {"kind": "beat", "track": _TRACK, "best": 67.900, "prior": 68.771, "improvement": 0.871}


def _live_toasts(win):
    """The window's PB cards that are BOTH still alive in C++ and on screen."""
    return [t for t in win.findChildren(PBToast) if shiboken6.isValid(t) and t.isVisible()]


def _collect(obj):
    """Run the deferred delete `obj` has queued on itself — and NOTHING else.

    `deleteLater()` posts a DeferredDelete event that `processEvents()` will not run (it needs the
    loop to unwind to the level deleteLater was called at), so a test that wants the card's C++
    half really gone has to flush it. The flush is TARGETED at the card: the all-receivers form
    also collects whatever else the window has queued, which is not this file's business.
    One receiver, one event type, no collateral."""
    QApplication.sendPostedEvents(obj, QEvent.DeferredDelete)


def _pump(predicate, seconds=2.0):
    """Run the event loop until `predicate` (the card's reveal is deferred to the same 120 ms beat
    CentralView restores its splitters on — see PBToast.show_for)."""
    end = time.time() + seconds
    while time.time() < end and not predicate():
        _APP.processEvents()
        time.sleep(0.004)
    return predicate()


@contextlib.contextmanager
def _real_window():
    """A REAL, SHOWN StudioWindow with no central view — `StudioWindow.__new__` +
    `QMainWindow.__init__`, the idiom tests/test_studio_features.py uses to drive real window
    methods without a real `Session.load`. The methods under test are the shipped ones
    (`_show_pb_moment` / `_clear_pb_toast` / `_forget_pb_toast`), and every collaborator they reach
    behaves as it does with no view yet: `_share_card_blocked` says "not shareable" (no session),
    `_pb_card_keepout` returns None, and `PBToast.anchor_region` takes its documented
    whole-window fallback.

    NOT the real CentralView, deliberately, and measured: building the window around the synthetic
    view and then DISMISSING a card over it SIGSEGVs offscreen in pyqtgraph's scene teardown in
    roughly 1 run in 8 (bisected stage by stage; the crash appears the moment `dismiss()` enters
    the sequence and is present on main). WHERE the card lands on the real view is already pinned
    exhaustively by tests/test_pb_toast.py; WHEN it may exist is this file's question, and it does
    not need a chart to answer. The seams stay redirected for the duration and the window is hidden
    on the way out."""
    from PySide6.QtWidgets import QMainWindow

    from studio.app import StudioWindow  # pacer-backed; imported here, not at module scope

    with _temp_library():
        win = StudioWindow.__new__(StudioWindow)
        QMainWindow.__init__(win)
        win.resize(1000, 700)
        win.show()
        _pump(lambda: win.isVisible())
        try:
            yield win
        finally:
            win.hide()


def test_a_second_personal_best_still_shows_after_the_first_card_is_gone():
    """§3.3: celebrate, let the card auto-dismiss, celebrate again — the second card must appear.

    The precondition is asserted, not assumed: after the dismiss the deferred delete is flushed
    (processEvents alone does NOT run DeferredDelete — it needs the loop to unwind to the level
    deleteLater was called at), so the window really is holding the wrapper of a deleted object,
    which is the state that used to raise. On main this test fails with zero visible cards and
    "studio: personal-best moment not shown (RuntimeError('… QTimer already deleted'))"."""
    if not _pacer_available():
        print("skip test_a_second_personal_best_still_shows_after_the_first_card_is_gone (no pacer)")
        return
    with _real_window() as win:
        win._show_pb_moment(_M_FIRST)
        assert _pump(lambda: len(_live_toasts(win)) == 1), "the first celebration never appeared"
        first = win._pb_toast

        first.dismiss()                                    # what AUTO_DISMISS_MS does
        _APP.processEvents()
        _collect(first)
        assert not shiboken6.isValid(first), "the first card's C++ half was not collected"
        assert win._pb_toast is None, "the destroyed card left its wrapper on the window"

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            win._show_pb_moment(_M_BEAT)
        assert "not shown" not in out.getvalue(), out.getvalue()
        assert _pump(lambda: len(_live_toasts(win)) == 1), \
            "the genuine PB after a dismissed card was swallowed"
        second = win._pb_toast
        assert second is not first and shiboken6.isValid(second)
        assert "0.87" in second.body_label.text(), second.body_label.text()
    print("test_a_second_personal_best_still_shows_after_the_first_card_is_gone OK")


def test_a_stale_wrapper_cannot_swallow_the_next_celebration():
    """The same failure, pinned at the guard rather than at the path: hand the window the wrapper
    of an already-deleted card (exactly what `deleteLater` + one event-loop pass used to leave
    behind) and the next moment must still be built. `shiboken6.isValid` is what makes it so."""
    if not _pacer_available():
        print("skip test_a_stale_wrapper_cannot_swallow_the_next_celebration (no pacer)")
        return
    with _real_window() as win:
        win._show_pb_moment(_M_FIRST)
        assert _pump(lambda: len(_live_toasts(win)) == 1)
        corpse = win._pb_toast
        shiboken6.delete(corpse)                 # the C++ half goes, the Python wrapper stays
        win._pb_toast = corpse                   # …and the window is holding it (main's state)

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            win._show_pb_moment(_M_BEAT)
        assert "not shown" not in out.getvalue(), out.getvalue()
        assert _pump(lambda: len(_live_toasts(win)) == 1), "a dead wrapper swallowed the next PB"
        assert win._pb_toast is not corpse and shiboken6.isValid(win._pb_toast)
    print("test_a_stale_wrapper_cannot_swallow_the_next_celebration OK")


def test_a_failing_dismiss_cannot_strand_the_load():
    """CONTAINMENT. `_clear_pb_toast` runs OUTSIDE `_show_pb_moment`'s try (that is what stopped a
    stale card eating the next celebration), and `_show_pb_moment` is called unguarded from
    `_on_session_loaded` immediately before `loadFinished.emit()` — so anything escaping the
    cleanup strands a completed load with the loading card still up (the review's §3.4 shape).
    `dismiss()` runs Python of its own (`hide()` reaches the host's event filter), so the guard is
    blanket, not RuntimeError-only: a card whose dismiss raises ValueError must still be replaced
    and must not propagate."""
    if not _pacer_available():
        print("skip test_a_failing_dismiss_cannot_strand_the_load (no pacer)")
        return
    with _real_window() as win:
        win._show_pb_moment(_M_FIRST)
        assert _pump(lambda: len(_live_toasts(win)) == 1)
        first = win._pb_toast
        first.dismiss = lambda: (_ for _ in ()).throw(ValueError("dismiss blew up"))

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            win._show_pb_moment(_M_BEAT)          # must not raise
        assert "not dismissed" in out.getvalue(), out.getvalue()
        assert _pump(lambda: len(_live_toasts(win)) >= 1), "the new card was never built"
        assert win._pb_toast is not first and shiboken6.isValid(win._pb_toast)
    print("test_a_failing_dismiss_cannot_strand_the_load OK")


def test_a_rapid_reload_still_replaces_the_card_rather_than_stacking_them():
    """The behaviour the cleanup exists for, unchanged: a second moment while the first card is
    still UP dismisses it, so the window never shows two celebrations at once."""
    if not _pacer_available():
        print("skip test_a_rapid_reload_still_replaces_the_card_rather_than_stacking_them (no pacer)")
        return
    with _real_window() as win:
        win._show_pb_moment(_M_FIRST)
        assert _pump(lambda: len(_live_toasts(win)) == 1)
        first = win._pb_toast
        win._show_pb_moment(_M_BEAT)             # no event-loop pass between the two
        assert _pump(lambda: len(_live_toasts(win)) == 1), "the replacement card never appeared"
        assert not first.isVisible(), "the replaced card is still on screen"
        assert win._pb_toast is not first
        # And the wrapper is dropped when the replaced card is finally collected — without taking
        # the live successor's reference with it (_forget_pb_toast).
        _collect(first)
        assert shiboken6.isValid(win._pb_toast), "collecting the old card cleared the new one"
    print("test_a_rapid_reload_still_replaces_the_card_rather_than_stacking_them OK")


if __name__ == "__main__":
    test_a_recording_is_not_its_own_previous_best()
    test_a_different_recording_on_the_same_track_still_sets_a_pb()
    test_a_first_ever_recording_still_gets_its_first_moment()
    test_an_identity_already_logged_is_not_a_first_session_on_a_newly_named_track()
    test_a_better_reload_still_beats_ANOTHER_recordings_best()
    test_a_recording_that_already_beat_the_prior_does_not_celebrate_twice()
    test_the_decision_without_an_identity_is_unchanged()
    test_load_full_recording_does_not_celebrate_against_its_own_chapter()
    test_a_second_chapter_of_one_outing_does_not_celebrate()
    test_a_genuinely_new_recording_still_celebrates_through_the_app_path()
    test_load_full_recording_still_beats_a_previous_days_recording()
    test_two_chapters_opened_separately_still_beat_a_previous_days_recording()
    test_a_second_personal_best_still_shows_after_the_first_card_is_gone()
    test_a_stale_wrapper_cannot_swallow_the_next_celebration()
    test_a_failing_dismiss_cannot_strand_the_load()
    test_a_rapid_reload_still_replaces_the_card_rather_than_stacking_them()
    print("\nAll PB-moment identity + toast-lifetime tests passed.")
