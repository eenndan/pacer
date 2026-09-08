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
    asserting NO moment fires — and that a genuinely new recording on the same track still does;
  * a second celebration on a REAL StudioWindow after the first card has been dismissed AND its
    deferred delete processed (the exact state §3.3 dies on — asserted as a precondition, so the
    test cannot pass vacuously), plus the rapid-reload replace it must not regress.

Every test points the library index at a TEMP dir through the single ``library._app_support_dir``
seam — the suite never reads or writes the user's real app-support library.

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

# The toast half builds the REAL window/view tree; PACER_NO_MEDIA must be set before studio imports
# (PlayerPane reads it once, at construction).
os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()

import shiboken6  # noqa: E402
from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from studio import library  # noqa: E402
from studio.overlays import PBToast  # noqa: E402

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
    """Point the library index at a fresh temp dir through its single seam, and put the seam back
    afterwards. The suite NEVER touches ~/Library/Application Support/pacer."""
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


def test_an_identity_already_logged_is_never_a_moment_even_on_a_new_track():
    """The gate is deliberately the RECORDING, not the (recording, track) pair. The same laps
    re-analysed under a freshly-named track are still laps this library has logged — re-announcing
    them is the same self-comparison in a thinner disguise — and the alternative (scope the check
    to the track) would let an entry stored with no track become a "first session" celebration on
    every reload once the track matched."""
    idx = _index(_entry("GX010062", 68.771, 22, track=None))
    assert library.pb_moment(idx, _TRACK, 68.201, "GX0062") is None
    print("test_an_identity_already_logged_is_never_a_moment_even_on_a_new_track OK")


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


# ============================================= C. the toast's lifetime (REAL window; needs pacer)
_M_FIRST = {"kind": "first", "track": _TRACK, "best": 68.771}
_M_BEAT = {"kind": "beat", "track": _TRACK, "best": 67.900, "prior": 68.771, "improvement": 0.871}


def _live_toasts(win):
    """The window's PB cards that are BOTH still alive in C++ and on screen."""
    return [t for t in win.findChildren(PBToast) if shiboken6.isValid(t) and t.isVisible()]


def _pump(predicate, seconds=2.0):
    """Run the event loop until `predicate` (the card's reveal is deferred to the same 120 ms beat
    CentralView restores its splitters on — see PBToast.show_for)."""
    end = time.time() + seconds
    while time.time() < end and not predicate():
        _APP.processEvents()
        time.sleep(0.004)
    return predicate()


def _real_window():
    """A REAL StudioWindow around the synthetic 2-lap CentralView — the same helper
    tests/test_pb_toast.py anchors against, so `_show_pb_moment` runs its whole production path
    (share verdict, anchor, keep-out) rather than a stand-in."""
    from test_central_view_realqt import _studiowindow_with_view

    win, _view = _studiowindow_with_view()
    win.resize(1440, 900)
    win.show()
    _pump(lambda: win.isVisible())
    return win


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
    win = _real_window()
    try:
        win._show_pb_moment(_M_FIRST)
        assert _pump(lambda: len(_live_toasts(win)) == 1), "the first celebration never appeared"
        first = win._pb_toast

        first.dismiss()                                    # what AUTO_DISMISS_MS does
        _APP.processEvents()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert not shiboken6.isValid(first), "the first card's C++ half was not collected"

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            win._show_pb_moment(_M_BEAT)
        assert "not shown" not in out.getvalue(), out.getvalue()
        assert _pump(lambda: len(_live_toasts(win)) == 1), \
            "the genuine PB after a dismissed card was swallowed"
        second = win._pb_toast
        assert second is not first and shiboken6.isValid(second)
        assert "0.87" in second.body_label.text(), second.body_label.text()
    finally:
        win.hide()
        win.deleteLater()
    print("test_a_second_personal_best_still_shows_after_the_first_card_is_gone OK")


def test_a_stale_wrapper_cannot_swallow_the_next_celebration():
    """The same failure, pinned at the guard rather than at the path: hand the window the wrapper
    of an already-deleted card (exactly what `deleteLater` + one event-loop pass used to leave
    behind) and the next moment must still be built. `shiboken6.isValid` is what makes it so."""
    if not _pacer_available():
        print("skip test_a_stale_wrapper_cannot_swallow_the_next_celebration (no pacer)")
        return
    win = _real_window()
    try:
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
    finally:
        win.hide()
        win.deleteLater()
    print("test_a_stale_wrapper_cannot_swallow_the_next_celebration OK")


def test_a_rapid_reload_still_replaces_the_card_rather_than_stacking_them():
    """The behaviour the cleanup exists for, unchanged: a second moment while the first card is
    still UP dismisses it, so the window never shows two celebrations at once."""
    if not _pacer_available():
        print("skip test_a_rapid_reload_still_replaces_the_card_rather_than_stacking_them (no pacer)")
        return
    win = _real_window()
    try:
        win._show_pb_moment(_M_FIRST)
        assert _pump(lambda: len(_live_toasts(win)) == 1)
        first = win._pb_toast
        win._show_pb_moment(_M_BEAT)             # no event-loop pass between the two
        assert _pump(lambda: len(_live_toasts(win)) == 1), "the replacement card never appeared"
        assert not first.isVisible(), "the replaced card is still on screen"
        assert win._pb_toast is not first
        # And the wrapper is dropped when the replaced card is finally collected — without taking
        # the live successor's reference with it (_forget_pb_toast).
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert shiboken6.isValid(win._pb_toast), "collecting the old card cleared the new one"
    finally:
        win.hide()
        win.deleteLater()
    print("test_a_rapid_reload_still_replaces_the_card_rather_than_stacking_them OK")


if __name__ == "__main__":
    test_a_recording_is_not_its_own_previous_best()
    test_a_different_recording_on_the_same_track_still_sets_a_pb()
    test_a_first_ever_recording_still_gets_its_first_moment()
    test_an_identity_already_logged_is_never_a_moment_even_on_a_new_track()
    test_the_decision_without_an_identity_is_unchanged()
    test_load_full_recording_does_not_celebrate_against_its_own_chapter()
    test_a_second_chapter_of_one_outing_does_not_celebrate()
    test_a_genuinely_new_recording_still_celebrates_through_the_app_path()
    test_a_second_personal_best_still_shows_after_the_first_card_is_gone()
    test_a_stale_wrapper_cannot_swallow_the_next_celebration()
    test_a_rapid_reload_still_replaces_the_card_rather_than_stacking_them()
    print("\nAll PB-moment identity + toast-lifetime tests passed.")
