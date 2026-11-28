"""Every first open says where its best lap stands against the PB (cool/every-session-says-where-it-
stands).

WHY. Between heats the first question is "was I quicker than my best, and by how much?". The app
decided the standing on every first open (`library.pb_standing_for`), but only the debrief's lead
or the celebration card ever said it, and only a first or a beaten PB raises the card. Measured on
synthetic heats of the demo circuit opened one after another in one jailed window: a heat 0.34 s
slower than the PB with nothing ranked landed on the Laps grid with no word of it anywhere. The
session notice (the status bar's untimed line, where first-open notices already go) now says it,
in the debrief's own sentence, when neither the debrief nor the card did.

Pinned here:
  1. the notice's words are the debrief's sentence as a clause, and "level" inside timing precision;
  2. the controller says it only for a standing no landing stated, never for a lap re-timed since
     the load, never for a ⚠ best lap kept out of the bests, and never on a load whose standing the
     trust gates refused (estimated timing, part of a recording, a re-open);
  3. THE REAL WINDOW, jailed: a session with nothing ranked behind the PB lands on the grid with the
     sentence in the notice, no card and no modal; one level with it says "level"; a session that
     lands on its debrief says it there and not in the notice.

Run: python tests/test_every_session_says_where_it_stands.py   (~15 s)
"""
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_debrief_landing import (  # noqa: E402
    _APP,
    _fresh_app_support,
    _open,
    _two_recordings,
)

from studio import data_quality, library  # noqa: E402
from studio._signal import fmt_time  # noqa: E402

_DEMO_CIRCUIT = "Synthetic demo circuit"


def _entry(fp, best, **kw):
    return {"fingerprint": fp, "stem": fp, "track": "Stadium", "date": "2026-09-01", "lap_count": 20,
            "best": best, "theoretical": best - 0.3, "verified": True, "degraded": False,
            "dropout": False, "paths": [f"/nowhere/{fp}.MP4"], **kw}


# ------------------------------------------------------------------------------ 1. the words
def test_the_notice_is_the_debrief_sentence_as_a_clause():
    idx = {"entries": [_entry("GX0001", 47.0)]}
    behind = library.pb_standing_for(True, idx, "Stadium", 47.34, fingerprint_key="GX0002")
    level = library.pb_standing_for(True, idx, "Stadium", 47.03, fingerprint_key="GX0002")
    said = library.pb_standing_notice(behind, fmt_time)
    assert said == "Best lap 0:47.340 at Stadium, 0.34 s off your personal best there (0:47.000)", \
        said
    assert library.pb_standing_text(behind, fmt_time) == said + "."
    flat = library.pb_standing_notice(level, fmt_time)
    assert flat == ("Best lap 0:47.030 at Stadium, level with your personal best there, within "
                    "timing precision (under 0.07 s apart)"), flat
    print("ok words:", said, flat, sep="\n  ")


# ------------------------------------------------------------------------- 2. the controller
def _controller(best_s=47.34, warned=(), verified=True, degraded=False):
    from studio.library_controller import LibraryController
    session = SimpleNamespace(timing_verified=verified,
                              timing_quality=SimpleNamespace(degraded=degraded),
                              best_lap_id=lambda: 2, lap_time=lambda _i: best_s,
                              lap_warnings=lambda: {i: None for i in warned})
    return LibraryController(SimpleNamespace(session=session), 0), session


def test_the_controller_says_it_only_when_nothing_else_did():
    idx = {"entries": [_entry("GX0001", 47.0)]}
    ctl, session = _controller()
    _moment, ctl.pb_standing = ctl._pb_decision(_entry("GX0002", 47.34), idx)
    assert ctl.standing_notice() is None, "said before the landing was decided"
    ctl.leave_standing(said=True)               # the debrief's lead, or the card, said it
    assert ctl.standing_notice() is None
    ctl.leave_standing(said=False)
    assert ctl.standing_notice() == library.pb_standing_notice(ctl.pb_standing, fmt_time)
    # a start-line drag since the load re-timed the best lap: the sentence is about no lap on screen
    session.lap_time = lambda _i: 47.10
    assert ctl.standing_notice() is None
    session.lap_time = lambda _i: 47.34
    # a ⚠ best lap (every valid lap flagged, the best fell back to a lap in doubt) claims nothing
    session.lap_warnings = lambda: {2: None}
    assert 2 in data_quality.not_counted_ids(session)
    assert ctl.standing_notice() is None
    print("ok controller: unsaid only, live best only, never a ⚠ best")


def test_the_trust_gates_leave_nothing_to_say():
    idx = {"entries": [_entry("GX0001", 47.0)]}
    for kw in ({"verified": False}, {"degraded": True}):
        ctl, _session = _controller(**kw)
        _moment, ctl.pb_standing = ctl._pb_decision(_entry("GX0002", 47.34), idx)
        ctl.leave_standing(said=False)
        assert ctl.pb_standing is None and ctl.standing_notice() is None, kw
    # a re-open: the index holds this recording, so it gets no "behind" (its row may be the PB)
    ctl, _session = _controller()
    _moment, ctl.pb_standing = ctl._pb_decision(
        _entry("GX0002", 47.34), {"entries": idx["entries"] + [_entry("GX0002", 46.9)]})
    ctl.leave_standing(said=False)
    assert ctl.standing_notice() is None
    # part of a recording, or an untrusted line, decides no standing at all (`update_library`)
    ctl, _session = _controller()
    ctl.verdict = library.Verdict.WAIT_WHOLE
    ctl.leave_standing(said=False)
    assert ctl.standing_notice() is None
    print("ok gates: nothing on untrusted, estimated, re-open or partial")


# ---------------------------------------------------------------------------- 3. the window
def _window():
    from PySide6.QtWidgets import QMessageBox

    from studio.app import StudioWindow

    def _no_modal(*args, **_kwargs):
        raise AssertionError(f"a modal opened: {args[1:3]}")

    for k in ("critical", "warning", "information", "question"):
        setattr(QMessageBox, k, staticmethod(_no_modal))
    win = StudioWindow([])
    win.resize(1280, 800)
    win.show()
    return win


def _set_pb(fingerprint: str, best: float) -> None:
    """Make the stored row `fingerprint` the track's PB at `best` (the stand-in for a faster heat)."""
    index = library.load()
    row = next(e for e in index["entries"] if e["fingerprint"] == fingerprint)
    library.upsert_and_save({**row, "best": best})


def _card_up(win) -> bool:
    toast = getattr(win, "_pb_toast", None)
    return toast is not None and toast.isVisible()


def test_the_real_window_says_it_once_where_nothing_else_does():
    from studio.dev import make_demo as md
    from studio.dev import synth_gopro as sg
    with tempfile.TemporaryDirectory(prefix="stands_") as folder, _fresh_app_support():
        # The habit-free demo circuit: nothing loses past the step, so no debrief (the existing rule).
        rec = sg.generate(os.path.join(folder, "gen"), md.DEMO_SEED, md.DEMO_LAPS, chapters=1,
                          origin=md.DEMO_ORIGIN)
        paths = []
        for n in ("9101", "9102", "9103"):
            paths.append(os.path.join(folder, n, f"GX01{n}.MP4"))
            os.makedirs(os.path.dirname(paths[-1]))
            shutil.copyfile(rec.paths[0], paths[-1])
        win = _window()
        try:
            _open(win, paths[0])                  # the track's first session: the card says so
            assert _card_up(win) and not win.view.is_debrief()
            assert "personal best" not in (win.statusBar().currentMessage() or "")
            best = win.session.lap_time(win.session.best_lap_id())
            fp, = (e["fingerprint"] for e in library.load()["entries"])
            _set_pb(fp, best - 0.34)              # a faster heat earlier: today is 0.34 s off it
            _open(win, paths[1])
            bar = win.statusBar().currentMessage()
            assert not win.view.is_debrief() and not _card_up(win)
            assert "0.34 s off your personal best there" in bar, bar
            assert bar.count("personal best") == 1, bar
            _set_pb(fp, best - 0.03)              # inside timing precision: level, not "0.03 s off"
            _open(win, paths[2])
            bar = win.statusBar().currentMessage()
            assert "level with your personal best there, within timing precision" in bar, bar
            assert "0.03 s" not in bar, bar
        finally:
            win.close()
            win.deleteLater()
            _APP.processEvents()
    print("ok window: behind and level in the notice; no card, no debrief, no modal")


def test_a_debrief_says_it_and_the_notice_does_not():
    with tempfile.TemporaryDirectory(prefix="stands_debrief_") as folder, _fresh_app_support():
        a, b = _two_recordings(folder)
        win = _window()
        try:
            _open(win, a)
            _open(win, b)                         # a first open with ranked corners: its debrief
            assert win.view.is_debrief()
            lead = win.view.opportunities.debrief_block.headline.text()
            assert "personal best" in lead, lead
            assert "personal best" not in (win.statusBar().currentMessage() or "")
        finally:
            win.close()
            win.deleteLater()
            _APP.processEvents()
    print("ok debrief: the lead says it, the notice does not")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} STANDING TESTS PASSED")
