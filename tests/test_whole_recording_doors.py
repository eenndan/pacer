"""Every door opens the whole recording, in chapter order — and part of one decides no verdict
(QA NEW-1 / NEW-2, 2026-09-26).

WHY. Measured on the owner's own footage, in a jail seeded with copies of his library, before the
fix:
  * NEW-1 — File ▸ Open… and the welcome's Open recording… took ONE file and opened only it, so on
    a new recording the first-open debrief, PB line and focus list were decided on chapter 1.
    SD_19_09 announced "0:46.862, 0.05 s faster" for a true 0:46.808, 0.10 s faster, with C7/C5/C2
    on the focus list where the whole session ranks C1/C5/C7; MK_18_09's chapter 1 alone is
    GPS-degraded, so it got no debrief at all. Load full recording, one click later, revisited
    nothing: the partial row made it a re-open, and `library.pb_moment`'s own-best rule silenced
    its PB.
  * NEW-2 — the command line chained its paths verbatim: MK's two chapters given reversed loaded
    18 laps for 19 (the best lap is the seam lap) and overwrote his library row, and the same file
    given twice loaded 22 laps from 11.

Pinned here, on a synthetic two-chapter recording of the built-in demo circuit (so its timing is
verified and the debrief can land), through the REAL StudioWindow, jailed to a fresh library that
holds one other, slower recording of the same circuit:
  1. File ▸ Open… of chapter 1, and the welcome's Open recording…, load every chapter;
  2. the command line: chapters given reversed and twice load once each, in chapter order; two
     recordings open the first and count the other; one explicit chapter still loads alone;
  3. `Session.load`, the backstop for every other route, chains in chapter order whatever it is
     handed, and says so once in the log;
  4. part of a NEW recording (one chapter on the command line) decides nothing — no debrief, no PB
     line or card, no focus promotion, no library row — and says the PB and focus list wait; File ▸
     Load full recording then lands the whole recording's verdict, identical to the drop's, once.

Run: python tests/test_whole_recording_doors.py   (~30 s; the pixi env's ffmpeg writes the video)
"""
import contextlib
import logging
import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()

from PySide6.QtWidgets import QFileDialog, QMessageBox  # noqa: E402

from studio import focus, library  # noqa: E402
from studio.app import StudioWindow  # noqa: E402
from studio.dev import make_demo as md  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.session import Session  # noqa: E402

_SEAM_MODULES = ("demo", "focus", "library", "logsetup", "marks", "prefs", "session_record",
                 "track_db")
_FP = "GX9001"          # synth_gopro's recording number, chapter index stripped
_REC = {}               # the one generated recording, shared: {"ch": [ch1, ch2], "folder": ...}


def _recording() -> list[str]:
    """The two chapters of ONE synthetic recording of the built-in demo circuit, generated once."""
    if not _REC:
        folder = tempfile.mkdtemp(prefix="doors_rec_")
        rec = sg.generate(os.path.join(folder, "0001"), md.DEMO_SEED, md.DEMO_LAPS, chapters=2,
                          origin=md.DEMO_ORIGIN)
        _REC.update(ch=list(rec.paths), folder=folder)
    return _REC["ch"]


@contextlib.contextmanager
def _fresh_app_support(prior_best: float | None = None, track: str | None = None):
    """Every app-support seam in a directory of this test's own, put back afterwards; with
    `prior_best`, the library already holds ONE other recording of `track` at that best."""
    import importlib
    mods = [importlib.import_module(f"studio.{m}") for m in _SEAM_MODULES]
    saved = [m._app_support_dir for m in mods]
    with tempfile.TemporaryDirectory(prefix="doors_") as d:
        for m in mods:
            m._app_support_dir = lambda d=d: d
        try:
            if prior_best is not None:
                library.upsert_and_save({
                    "fingerprint": "GX0042", "stem": "GX010042", "track": track,
                    "date": "2026-09-01", "lap_count": 20, "best": prior_best,
                    "theoretical": prior_best - 0.5, "verified": True, "degraded": False,
                    "dropout": False, "paths": ["/nowhere/GX010042.MP4"]})
            yield d
        finally:
            for m, fn in zip(mods, saved, strict=True):
                m._app_support_dir = fn


@contextlib.contextmanager
def _no_modals():
    """A modal on these paths would block an offscreen run forever; make it fail by name."""
    def _refuse(*args, **_kwargs):
        raise AssertionError(f"a modal opened: {args[1:3]}")
    boxes = {k: getattr(QMessageBox, k) for k in ("critical", "warning", "information", "question")}
    for k in boxes:
        setattr(QMessageBox, k, staticmethod(_refuse))
    try:
        yield
    finally:
        for k, fn in boxes.items():
            setattr(QMessageBox, k, fn)


def _settle(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        _APP.processEvents()
        time.sleep(0.01)


def _until_loaded(win, start) -> None:
    """Run `start()` (a door) and pump until the window's load finishes; fail by name if it never
    does."""
    done = {"v": False}
    conn = win.loadFinished.connect(lambda: done.__setitem__("v", True))
    start()
    deadline = time.time() + 120
    while not done["v"] and time.time() < deadline:
        _APP.processEvents()
        time.sleep(0.01)
    win.loadFinished.disconnect(conn)
    assert done["v"], "the load never finished"
    _settle(0.4)     # past the view's first show and its deferred grid restores (0 and 120 ms)


def _cli_window(paths: list[str]) -> StudioWindow:
    """`StudioWindow(paths)` — exactly what `main()` builds for `studio -- <paths>` — loaded. The
    constructor starts the load, so the signal is connected after it: the result arrives on a
    later turn of the event loop, never inside the constructor."""
    win = StudioWindow(list(paths))
    win.resize(1440, 900)
    win.show()
    done = {"v": False}
    win.loadFinished.connect(lambda: done.__setitem__("v", True))
    deadline = time.time() + 120
    while not done["v"] and time.time() < deadline:
        _APP.processEvents()
        time.sleep(0.01)
    assert done["v"], f"{[os.path.basename(p) for p in paths]} never finished loading"
    _settle(0.4)
    return win


def _close(win) -> None:
    win.close()
    win.deleteLater()
    _APP.processEvents()


def _chapter_names(session) -> list[str]:
    return [os.path.basename(c.path) for c in session.chapters.chapters]


def _row(fp: str = _FP) -> dict | None:
    return next((e for e in library.load()["entries"] if e["fingerprint"] == fp), None)


def _verdict(win) -> dict:
    """What a first open decided, as the driver sees it and as the stores keep it."""
    track = win.session.track_name
    row = _row()
    return {"debrief": win.view.is_debrief(),
            "pb_line": win.library_ctl.debrief_pb_line(),
            "card": win._pb_toast is not None,
            "focus": [i.cid for i in focus.for_track(focus.load(), track)],
            "row": None if row is None else (row["lap_count"], row["best"],
                                             [os.path.basename(p) for p in row["paths"]])}


# ------------------------------------------------------------------ 1. File ▸ Open, the welcome
def test_file_open_and_the_welcome_open_every_chapter_of_the_picked_file():
    """On main both loaded the ONE file the picker returned: `_paths == [ch1]`, 1 chapter."""
    ch1, ch2 = _recording()
    real_pick = QFileDialog.getOpenFileName
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (ch1, ""))
    try:
        with _fresh_app_support(), _no_modals():
            win = StudioWindow([])
            win.resize(1440, 900)
            win.show()
            try:
                _until_loaded(win, win.centralWidget().open_btn.click)   # the welcome's button
                assert win._paths == [ch1, ch2], ("the welcome opened part of the recording",
                                                  win._paths)
                assert _chapter_names(win.session) == ["GX019001.MP4", "GX029001.MP4"]
                assert win._chapter_subset() is None and not win._full_action.isEnabled()
                _until_loaded(win, win._open_action.trigger)             # File ▸ Open…
                assert win._paths == [ch1, ch2], ("File ▸ Open opened part of the recording",
                                                  win._paths)
                assert _chapter_names(win.session) == ["GX019001.MP4", "GX029001.MP4"]
            finally:
                _close(win)
    finally:
        QFileDialog.getOpenFileName = real_pick
    print("ok open: the welcome and File ▸ Open both load GX019001 + GX029001 from a pick of "
          "GX019001")


# --------------------------------------------------------------------- 2. the command line
def test_the_command_line_opens_one_recording_in_chapter_order_once_each():
    """What `main()` hands the window reaches `_load` grouped, ordered and de-duplicated. On main
    `[ch2, ch1, ch1]` reached it verbatim, and so did two different recordings."""
    ch1, ch2 = _recording()
    other = os.path.join(_REC["folder"], "0002", "GX019002.MP4")
    os.makedirs(os.path.dirname(other), exist_ok=True)
    if not os.path.exists(other):
        shutil.copyfile(ch1, other)     # a second recording that carries telemetry of its own
    seen = []
    real_load = StudioWindow._load
    StudioWindow._load = lambda self, paths, drop_notice=None: seen.append((list(paths),
                                                                           drop_notice))
    try:
        cases = {"reversed + twice": [ch2, ch1, ch1], "one chapter": [ch1],
                 "two recordings": [ch1, other]}
        for label, paths in cases.items():
            seen.clear()
            win = StudioWindow(paths)
            try:
                assert len(seen) == 1, (label, seen)
                got, notice = seen[0]
                if label == "reversed + twice":
                    assert got == [ch1, ch2], (label, [os.path.basename(p) for p in got])
                    assert notice is None, notice
                elif label == "one chapter":
                    assert got == [ch1], ("one explicit chapter must load alone", got)
                else:
                    assert got == [ch1], ("two recordings were chained onto one clock",
                                          [os.path.basename(p) for p in got])
                    assert notice == ("Given 2 recordings — opened recording 9001. Open the "
                                      "others one at a time."), notice
            finally:
                _close(win)
        # --full still chains the siblings of one named chapter
        seen.clear()
        win = StudioWindow([ch2], full=True)
        try:
            assert seen and seen[0][0] == [ch1, ch2], seen
        finally:
            _close(win)
    finally:
        StudioWindow._load = real_load
    print("ok command line: reversed + twice -> [ch1, ch2]; one chapter alone; two recordings -> "
          "the first, the other counted; --full chains")


# --------------------------------------------------------------------- 3. the Session.load backstop
class _Records(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def test_session_load_chains_the_chapters_in_order_whatever_it_is_handed():
    """Every route (a reference load, the golden dump, a dev script) comes through Session.load.
    On main `[ch2, ch1, ch2]` chained exactly that — chapter 2 first, then twice."""
    ch1, ch2 = _recording()
    want = Session.load([ch1, ch2])
    log = logging.getLogger("studio.session")
    records = _Records()
    log.addHandler(records)
    try:
        got = Session.load([ch2, ch1, ch2])
    finally:
        log.removeHandler(records)
    assert _chapter_names(got) == ["GX019001.MP4", "GX029001.MP4"], _chapter_names(got)
    assert got.valid_lap_ids() == want.valid_lap_ids(), (got.valid_lap_ids(), want.valid_lap_ids())
    assert [got.lap_time(i) for i in got.valid_lap_ids()] == \
        [want.lap_time(i) for i in want.valid_lap_ids()]
    said = [m for m in records.messages if "chapter order" in m]
    assert len(said) == 1 and "given: GX029001.MP4, GX019001.MP4, GX029001.MP4" in said[0], \
        records.messages
    print(f"ok backstop: {len(want.valid_lap_ids())} laps either way; logged once: {said[0]!r}")


# ------------------------------------------------ 4. part of a new recording decides nothing
def test_part_of_a_new_recording_decides_nothing_until_the_whole_recording_loads():
    """The command line's one chapter, on a recording the library has never seen, beside a slower
    previous best on the same circuit. On main that open landed the debrief on chapter 1's own
    numbers, wrote its row, and Load full recording then decided nothing."""
    ch1, ch2 = _recording()
    whole = Session.load([ch1, ch2])
    track, best = whole.track_name, whole.lap_time(whole.best_lap_id())
    prior = round(best + 0.5, 3)
    assert whole.timing_verified, "the demo circuit is built in, so its timing must be verified"

    with _no_modals():
        # THE REFERENCE: the same recording dropped on the same library.
        with _fresh_app_support(prior, track):
            win = StudioWindow([])
            win.resize(1440, 900)
            win.show()
            try:
                _until_loaded(win, lambda: win._open_recordings([ch1]))
                dropped = _verdict(win)
            finally:
                _close(win)
        assert dropped["debrief"] and dropped["pb_line"] and dropped["focus"], dropped
        assert "faster than your previous best" in dropped["pb_line"], dropped

        with _fresh_app_support(prior, track):
            win = _cli_window([ch1])
            try:
                part = _verdict(win)
                ctl = win.library_ctl
                assert win._chapter_subset() == (1, 2), win._chapter_subset()
                assert part == {"debrief": False, "pb_line": None, "card": False, "focus": [],
                                "row": None}, ("part of the recording decided a verdict", part)
                assert not ctl.opened_new and ctl.pb_standing is None and ctl.waiting_for_whole
                notice = win.statusBar().currentMessage()
                assert notice == ("1 of 2 chapters — File ▸ Load full recording to analyse the "
                                  "whole recording; your PB and focus list wait for it"), notice
                # A drag's library refresh must not write the row behind the notice's back.
                ctl.refresh_library_entry()
                assert _row() is None, "a refresh wrote the row the verdict is waiting on"

                # File ▸ Load full recording, in this window: the whole recording's verdict, once.
                assert win._full_action.isEnabled(), "nothing offers the whole recording"
                _until_loaded(win, win._full_action.trigger)
                full = _verdict(win)
                assert full == dropped, ("the whole recording's verdict differs from the drop's",
                                         full, dropped)
                assert not ctl.waiting_for_whole
                assert "wait for it" not in (win.statusBar().currentMessage() or "")

                # ...and only once: the same recording again is a re-open.
                _until_loaded(win, lambda: win._open_recordings([ch2]))
                again = _verdict(win)
                assert not again["debrief"] and not again["card"] and not ctl.opened_new, again
                assert again["focus"] == full["focus"] and again["row"] == full["row"], again
            finally:
                _close(win)
    print(f"ok verdict: chapter 1 alone decided nothing and wrote no row; the full load said "
          f"{full['pb_line']!r} and put {full['focus']} on the list, as the drop did; a re-open "
          "said nothing")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    try:
        for t in tests:
            t()
    finally:
        if _REC:
            shutil.rmtree(_REC["folder"], ignore_errors=True)
    print(f"\nALL {len(tests)} WHOLE-RECORDING DOOR TESTS PASSED")
