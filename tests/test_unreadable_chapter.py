"""A sibling chapter that is NOT VIDEO must not take the whole recording down with it.

`chapters.discover_siblings` groups on FILENAMES. So a file that carries a valid GoPro chapter name
while holding something else is handed to the loader alongside the real chapters, `GPMFSource`
raises `Failed to open file`, and the ENTIRE recording fails to open — including the intact
chapters, which need nothing from it.

This is not hypothetical. `~/Desktop/D24/GX010060.MP4` on the owner's machine is 2.4 MB of JSON that
a dev tool wrote over 11.9 GB of footage. Probed read-only against the real folder:
`discover_siblings("GX020060.MP4")` returns all three 0060 chapters INCLUDING the stub, and
`pacer.GPMFSource` on it raises — so **File ▸ Load full recording on that recording was dead**, and
the 24 laps in the two intact chapters were unreachable through that door.

What is pinned here:
  * `chapters.is_mp4_container` — the 8-byte ISO-box-header probe: real MP4 yes; JSON / text /
    zeros / empty / missing / a directory no;
  * `split_non_mp4` / `skipped_notice` — the partition and the sentence that NAMES what was left
    out (a chapter that silently vanishes is not an improvement on a failed load);
  * `discover_siblings` still LISTS the junk sibling — deliberate, and pinned as a contract:
    `sidecar.sidecar_path` and `library.fingerprint` key a recording on `[0]`'s stem, so filtering
    discovery would silently re-key the user's saved timing lines onto chapter 2;
  * `Session.load` on a synthetic recording (a real committed clip + a JSON sibling) loads the real
    chapter, records the skipped one, and the window's untimed session notice names it;
  * a load with NOTHING loadable raises (naming the files) rather than returning an empty session;
  * the failure message for a GoPro-named non-video file no longer tells the user to copy it off
    the SD card again — nothing on the card can repair a file overwritten in place.

Real files on disk under a temp dir + the committed `3rdparty/gpmf-parser/samples/hero6.mp4`
fixture (skipped if the submodule isn't checked out). Offscreen Qt for the notice half; no user
data is read or written.

Run: QT_QPA_PLATFORM=offscreen python tests/test_unreadable_chapter.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Every app-support seam into a temp tree BEFORE a window or a Session exists: this file LOADS a
# session (which upserts the library) and resolves a track DB. Same idiom as test_first_run_path.
from studio import library, prefs, track_db  # noqa: E402

_SEAMS = tempfile.mkdtemp(prefix="pacer-test-unreadable-chapter-")
for _mod, _name in ((prefs, "prefs"), (library, "library"), (track_db, "track_db")):
    _dir = os.path.join(_SEAMS, _name)
    os.makedirs(_dir, exist_ok=True)
    _mod._app_support_dir = (lambda d=_dir: d)

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()

from studio import chapters, sidecar  # noqa: E402
from studio.session import Session  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(_REPO, "3rdparty", "gpmf-parser", "samples", "hero6.mp4")

# The exact shape of the destroyed file: a JSON dump, written over an .MP4, that still parses as a
# GoPro chapter name. Byte-for-byte irrelevant; what matters is that it does not open with an ISO
# box header. (The real one starts `{"base": {"activ…` — a golden_session_dump.)
_JSON_STUB = json.dumps({"base": {"valid_lap_ids": [0, 1, 2]}}).encode() * 40


_SKIPPED: list[str] = []


def _have_fixture(test_name: str) -> bool:
    """Whether the committed clip is checked out — and if not, RECORD the skip by name.

    Loud on purpose. The repo's known false-pass shape is a fixture-gated test that returns
    silently while the runner prints a blanket "all tests passed"; `_run_all` refuses to print
    that line once anything is in `_SKIPPED`."""
    if os.path.exists(FIXTURE):
        return True
    _SKIPPED.append(test_name)
    print(f"SKIP {test_name}: fixture {FIXTURE} not checked out (submodule)")
    return False


def _unreadable(path: str):
    """`path`, chmod'd so its bytes cannot be read — the intact-but-locked chapter. Returns a
    restore callable, because a 000 file inside a TemporaryDirectory breaks its cleanup."""
    os.chmod(path, 0o000)
    return lambda: os.chmod(path, 0o644)


def _recording(root, *, junk_chapter=1, real_chapters=(2,), rec="0099"):
    """A synthetic chaptered recording on disk: `junk_chapter` is the JSON stub, every chapter in
    `real_chapters` is a copy of the committed hero6 clip. Returns the paths in chapter order."""
    paths = []
    for cc in sorted({junk_chapter, *real_chapters}):
        p = os.path.join(root, f"GX{cc:02d}{rec}.MP4")
        if cc == junk_chapter:
            with open(p, "wb") as f:
                f.write(_JSON_STUB)
        else:
            shutil.copyfile(FIXTURE, p)
        paths.append(p)
    return paths


# ==================================================== the probe itself
def test_probe_mp4_separates_read_and_not_video_from_could_not_read():
    """THREE answers, not two. "I read this and it is not video" is permanent and local to one
    file; "I could not read this" says nothing about the contents and is usually temporary. The
    first version of this probe collapsed them into a bool, and both defects that came out of that
    (an intact locked chapter told it had been overwritten; a moved chapter dropped from the
    analysis in silence) are pinned in the two tests below."""
    with tempfile.TemporaryDirectory() as root:
        not_video = {}
        not_video["json"] = os.path.join(root, "GX010060.MP4")
        with open(not_video["json"], "wb") as f:
            f.write(_JSON_STUB)
        not_video["text"] = os.path.join(root, "NotAVideo.MP4")
        with open(not_video["text"], "w", encoding="utf-8") as f:
            f.write("a plain text file the user renamed to .MP4\n" * 40)
        not_video["zeros"] = os.path.join(root, "GX010098.MP4")
        with open(not_video["zeros"], "wb") as f:
            f.write(b"\x00" * 16)          # the 16-byte name-only stubs other suites build
        not_video["empty"] = os.path.join(root, "Empty.MP4")
        open(not_video["empty"], "wb").close()
        for key, path in not_video.items():
            assert chapters.probe_mp4(path) == chapters.MP4_NOT_A_CONTAINER, key
            assert not chapters.is_mp4_container(path), key

        # COULD NOT READ — a different answer, on files whose contents are unknown.
        unreadable = {"missing": os.path.join(root, "NoSuchFile.MP4"),
                      "folder": os.path.join(root, "AFolder.MP4")}
        os.makedirs(unreadable["folder"])
        unreadable["locked"] = os.path.join(root, "GX010096.MP4")
        with open(unreadable["locked"], "wb") as f:
            f.write(b"\x00\x00\x00\x18ftypmp41" + b"\x00" * 64)   # REAL header, then locked
        restore = _unreadable(unreadable["locked"])
        try:
            for key, path in unreadable.items():
                assert chapters.probe_mp4(path) == chapters.MP4_UNREADABLE, key
                assert not chapters.is_mp4_container(path), key
        finally:
            restore()

        # …and the admit side: a real MP4 header, and the truncated-real-chapter shape the load
        # failure table classifies separately (it IS an MP4, only an incomplete one).
        truncated = os.path.join(root, "GX010097.MP4")
        with open(truncated, "wb") as f:
            f.write(b"\x00\x00\x00\x18ftypmp41" + b"\x00" * 4096)
        assert chapters.probe_mp4(truncated) == chapters.MP4_CONTAINER
        for box in (b"moov", b"mdat", b"free", b"wide"):
            other = os.path.join(root, f"Other{box.decode()}.MP4")
            with open(other, "wb") as f:
                f.write(b"\x00\x00\x00\x18" + box + b"\x00" * 32)
            assert chapters.is_mp4_container(other), box
    if _have_fixture("probe_mp4 on the committed clip"):
        assert chapters.probe_mp4(FIXTURE) == chapters.MP4_CONTAINER
    print("test_probe_mp4_separates_read_and_not_video_from_could_not_read OK")


def test_split_non_mp4_partitions_and_keeps_order():
    if not _have_fixture("split_non_mp4 partition"):
        return
    with tempfile.TemporaryDirectory() as root:
        paths = _recording(root, junk_chapter=2, real_chapters=(1, 3))
        kept, skipped = chapters.split_non_mp4(paths)
        assert [os.path.basename(p) for p in kept] == ["GX010099.MP4", "GX030099.MP4"]
        assert [os.path.basename(p) for p in skipped] == ["GX020099.MP4"]
        # The partition is TOTAL — nothing invented, nothing lost.
        assert sorted(kept + skipped) == sorted(paths)
    assert chapters.split_non_mp4([]) == ([], [])
    print("test_split_non_mp4_partitions_and_keeps_order OK")


def test_split_non_mp4_never_skips_a_file_it_could_not_read():
    """D2 — THE SILENT-SUBSET DEFECT. A chapter that is locked, still copying, on a volume that
    went away, or simply MOVED is not junk: skipping it would analyse part of the user's recording
    and label an absent file "not a readable video". Those paths are KEPT and go to the loader,
    which fails loudly on them exactly as it did before any of this existed."""
    if not _have_fixture("split_non_mp4 keeps unreadable paths"):
        return
    with tempfile.TemporaryDirectory() as root:
        paths = _recording(root, junk_chapter=1, real_chapters=(2, 3))
        restore = _unreadable(paths[2])          # chapter 3: intact footage, no read permission
        try:
            kept, skipped = chapters.split_non_mp4(paths)
            assert [os.path.basename(p) for p in skipped] == ["GX010099.MP4"], skipped
            assert paths[2] in kept, "an unreadable chapter must reach the loader, not vanish"
            # …and the load then FAILS, rather than quietly analysing chapter 2 alone.
            try:
                Session.load(paths)
                raised = None
            except Exception as exc:  # noqa: BLE001
                raised = exc
            assert raised is not None, "a locked chapter was silently dropped from the analysis"
        finally:
            restore()

        # A path that is not there at all is likewise kept, so the app keeps saying
        # "Couldn't find that file — it may have been moved, renamed or deleted."
        moved = os.path.join(root, "GX040099.MP4")
        kept, skipped = chapters.split_non_mp4([*paths, moved])
        assert moved in kept and moved not in skipped
    print("test_split_non_mp4_never_skips_a_file_it_could_not_read OK")


def test_skipped_notice_names_the_files():
    """It NAMES them, like the multi-recording drop notice names what it did not open. A count
    alone ("1 file skipped") sends the user hunting through their own folder."""
    assert chapters.skipped_notice([]) is None
    one = chapters.skipped_notice(["/Users/x/Desktop/D24/GX010060.MP4"])
    assert "GX010060.MP4" in one, one
    assert "/Users/x" not in one, "the notice carries basenames, not the user's paths"
    two = chapters.skipped_notice(["/d/GX010060.MP4", "/d/GX040060.MP4"])
    assert "GX010060.MP4" in two and "GX040060.MP4" in two and "2 files" in two, two
    many = chapters.skipped_notice([f"/d/GX{i:02d}0060.MP4" for i in range(1, 7)])
    assert "6 files" in many and "+3 more" in many, many
    # Scoped to another recording — the cross-recording reference (D5).
    ref = chapters.skipped_notice(["/d/GX010060.MP4"], where="the reference recording")
    assert "GX010060.MP4" in ref and "from the reference recording" in ref, ref
    assert chapters.skipped_notice([], where="the reference recording") is None
    print("test_skipped_notice_names_the_files OK")


# ==================================================== discovery is deliberately NOT filtered
def test_discovery_still_lists_the_junk_sibling_and_keeps_the_sidecar_key():
    """A CONTRACT, not an oversight — and it is the SIDECAR that is at stake, not the library.
    `sidecar.sidecar_path` takes `discover_siblings(...)[0]`'s stem VERBATIM, so filtering an
    unopenable chapter 1 out of discovery would re-key the user's hand-placed start/finish line
    onto `GX02….pacer.json` and lose it. `library.fingerprint` STRIPS the chapter index (which is
    exactly why a single-chapter and a full-chain open share one entry), so library history
    survives either way — asserted here so the justification cannot rot into folklore. The skip
    belongs to the LOAD, which is what actually cannot read the file."""
    if not _have_fixture("discovery keeps the sidecar key"):
        return
    with tempfile.TemporaryDirectory() as root:
        paths = _recording(root, junk_chapter=1, real_chapters=(2, 3))
        sibs = chapters.discover_siblings(paths[1])          # opened via an INTACT chapter
        assert [os.path.basename(p) for p in sibs] == [
            "GX010099.MP4", "GX020099.MP4", "GX030099.MP4"], sibs
        # THE ONE THAT WOULD HAVE MOVED: the sidecar stem is chapter 1's, junk or not.
        assert sidecar.sidecar_path(paths[1]) == os.path.join(root, "GX010099.pacer.json")
        # THE ONE THAT WOULD NOT: the library fingerprint is chapter-index-blind.
        assert library.fingerprint("GX010099") == library.fingerprint("GX020099")
    print("test_discovery_still_lists_the_junk_sibling_and_keeps_the_sidecar_key OK")


# ==================================================== the load skips it, and says so
def test_load_skips_the_junk_sibling_and_records_it():
    """THE BUG, end to end: the full-recording load of a chaptered recording whose chapter 1 is not
    video. It must SUCCEED on the intact chapters and report the one it left out."""
    if not _have_fixture("load skips the junk sibling"):
        return
    with tempfile.TemporaryDirectory() as root:
        paths = _recording(root, junk_chapter=1, real_chapters=(2, 3))
        sibs = chapters.discover_siblings(paths[0])
        s = Session.load(sibs)                               # would raise RuntimeError on main
        assert [os.path.basename(p) for p in s.skipped_chapters] == ["GX010099.MP4"]
        assert s.chapters is not None and len(s.chapters) == 2, s.chapters
        assert [os.path.basename(c.path) for c in s.chapters.chapters] == [
            "GX020099.MP4", "GX030099.MP4"]
        # The video layer follows the chapter map, so it never points at the skipped file.
        assert os.path.basename(s.video_path) == "GX020099.MP4", s.video_path
        assert s.point_count() > 0, "the intact chapters carried telemetry"
    print("test_load_skips_the_junk_sibling_and_records_it OK")


def test_an_ordinary_load_reports_nothing_skipped():
    """The negative control: a clean recording's `skipped_chapters` is empty and the notice is
    silent, so the clause can never become background noise."""
    if not _have_fixture("ordinary load reports nothing skipped"):
        return
    s = Session.load([FIXTURE])
    assert s.skipped_chapters == [], s.skipped_chapters
    assert chapters.skipped_notice(s.skipped_chapters) is None
    assert Session(s.laps, s.cs, None).skipped_chapters == [], "a directly-built Session too"
    print("test_an_ordinary_load_reports_nothing_skipped OK")


def test_a_load_with_nothing_loadable_raises_and_names_the_files():
    """Skipping is for a recording that still HAS chapters. When every path is junk there is no
    session to build, and answering with a silently empty one would be the worse failure."""
    with tempfile.TemporaryDirectory() as root:
        junk = os.path.join(root, "GX010099.MP4")
        with open(junk, "wb") as f:
            f.write(_JSON_STUB)
        try:
            Session.load([junk])
            raised = None
        except Exception as exc:  # noqa: BLE001 — mirrors SessionLoadWorker.run
            raised = exc
        assert isinstance(raised, ValueError), raised
        assert "GX010099.MP4" in str(raised), raised
    print("test_a_load_with_nothing_loadable_raises_and_names_the_files OK")


# ==================================================== the user-visible half
def test_the_session_notice_names_the_skipped_chapter():
    """The window's ONE untimed status line states it, alongside (not instead of) the other
    clauses, and is re-decided from live session state like every other one."""
    from test_central_view_realqt import _studiowindow_with_view

    win, _view = _studiowindow_with_view()
    try:
        win._drop_notice = None
        win._timing_restore_failed = False
        win._timing_restore_unreadable = False
        win._tracks_unreadable = False
        win._notice = None
        # The async-load bookkeeping the real __init__ installs; closeEvent drains it (the shared
        # fixture builds the window via __new__). Same seeding as tests/test_first_run_path.
        win._load_token = 0
        win._ref_load_token = 0
        win._load_workers = set()
        win._pending_load = None
        win._placeholder_timer = None
        # Clean session: silent.
        win.session.skipped_chapters = []
        base = win._session_notice() or ""
        assert "Skipped" not in base, base
        # One chapter left out: named, and stated ALONGSIDE whatever the session already said —
        # both facts are true at once, so neither may swallow the other.
        win.session.skipped_chapters = ["/d/D24/GX010060.MP4"]
        notice = win._session_notice()
        assert "GX010060.MP4" in notice, notice
        assert notice.startswith(base) and len(notice) > len(base), (base, notice)

        # …and the LABEL beside it counts the same chapters. `_paths` keeps the full request (so
        # "Load full recording" is not offered as a way to reach a file that cannot be read), so
        # the label has to come from the session, or the title says "· 3 chapters" over a
        # 2-chapter session while the status bar says one was skipped.
        win._paths = [f"/d/D24/GX0{i}0060.MP4" for i in (1, 2, 3)]
        win.session.chapters = chapters.ChapterMap(win._paths[1:], [10.0, 10.0])
        assert win._loaded_label() == "recording 0060 · 2 chapters", win._loaded_label()
        assert win._chapter_subset() is None, "a skipped chapter is not a chainable one"
    finally:
        win.close()
        _APP.processEvents()
    print("test_the_session_notice_names_the_skipped_chapter OK")


def test_the_failure_message_for_an_overwritten_chapter_does_not_blame_the_sd_card():
    """A GoPro-named file whose contents are not video gets its OWN sentence. It used to land on
    "the copy is probably incomplete — copy it off the SD card again", which cannot repair a file
    overwritten in place and sends the user back to a card that may have been reused. A file the
    user merely RENAMED to .MP4 keeps the old, correct answer."""
    from studio.app import StudioWindow

    with tempfile.TemporaryDirectory() as root:
        stub = os.path.join(root, "GX010060.MP4")
        with open(stub, "wb") as f:
            f.write(_JSON_STUB)
        exc = ValueError("none of these files is a readable video: GX010060.MP4")
        msg = StudioWindow._load_failure_message([stub], exc)
        assert "aren't video" in msg, msg
        assert "SD card" not in msg, msg
        assert "another chapter" in msg, msg

        # Negative control 1: a non-GoPro name is not this case.
        renamed = os.path.join(root, "holiday.MP4")
        with open(renamed, "w", encoding="utf-8") as f:
            f.write("not a video\n" * 40)
        other = StudioWindow._load_failure_message(
            [renamed], RuntimeError(f"Failed to open file: {renamed}"))
        assert "doesn't look like a GoPro recording" in other, other

        # Negative control 2: a TRUNCATED real chapter is still an MP4 — keep "copy it again".
        truncated = os.path.join(root, "GX010098.MP4")
        with open(truncated, "wb") as f:
            f.write(b"\x00\x00\x00\x18ftypmp41" + b"\x00" * 4096)
        trunc_msg = StudioWindow._load_failure_message(
            [truncated], RuntimeError(f"Failed to open file: {truncated}"))
        assert "is a GoPro file" in trunc_msg, trunc_msg
        assert "SD card" in trunc_msg, trunc_msg
    print("test_the_failure_message_for_an_overwritten_chapter_does_not_blame_the_sd_card OK")


def test_an_intact_but_unreadable_chapter_is_never_called_overwritten():
    """D1 — THE WORST SENTENCE THIS TABLE COULD PRODUCE, and the first version of this fix produced
    it. A chmod-000 chapter is almost certainly intact footage; the probe could not read it, the
    loader raises its own RuntimeError (never an OSError), and control fell through into "it has
    been overwritten or replaced". The app asserted that the user's footage was destroyed. It must
    land on the permission sentence instead — which the docstring had been advertising all along."""
    if not _have_fixture("unreadable chapter is not called overwritten"):
        return
    from studio.app import StudioWindow

    with tempfile.TemporaryDirectory() as root:
        locked = os.path.join(root, "GX010077.MP4")
        shutil.copyfile(FIXTURE, locked)          # REAL, intact footage…
        restore = _unreadable(locked)             # …that we simply cannot read right now
        try:
            try:
                Session.load([locked])
                exc = None
            except Exception as e:  # noqa: BLE001 — mirrors SessionLoadWorker.run
                exc = e
            assert exc is not None
            msg = StudioWindow._load_failure_message([locked], exc)
            assert "permission" in msg, msg
            assert "overwritten" not in msg and "replaced" not in msg, msg
            assert "aren't video" not in msg, msg
        finally:
            restore()
    print("test_an_intact_but_unreadable_chapter_is_never_called_overwritten OK")


def test_the_failure_blames_the_chapter_the_loader_actually_opened():
    """D3 — MISATTRIBUTION. With a junk chapter 1 beside a real chapter 2, the skipped file never
    reaches the loader, so a failure in chapter 2 must not be reported against chapter 1. On the
    owner's D24 that is the live configuration of every 0060 load."""
    if not _have_fixture("failure blames the chapter the loader opened"):
        return
    from studio.app import StudioWindow

    with tempfile.TemporaryDirectory() as root:
        paths = _recording(root, junk_chapter=1, real_chapters=(2,))
        assert StudioWindow._offending_path(paths) == paths[1], "blamed the skipped chapter"
        # …and the message describes THAT file (a real MP4 whose telemetry failed), not the stub.
        msg = StudioWindow._load_failure_message(
            paths, RuntimeError(f"Failed to open file: {paths[1]}"))
        assert "is a GoPro file" in msg, msg
        assert "overwritten" not in msg, msg
        # All-junk: nothing was handed to the loader, so the first path is the honest subject.
        all_junk = _recording(root, junk_chapter=1, real_chapters=(), rec="0088")
        assert StudioWindow._offending_path(all_junk) == all_junk[0]
    print("test_the_failure_blames_the_chapter_the_loader_actually_opened OK")


def test_an_all_junk_recording_is_not_told_to_open_another_chapter():
    """D4 — DON'T PRESCRIBE THE IMPOSSIBLE. When every chapter handed to the load is junk there is
    no other chapter to open, so the advice changes; a SINGLE junk file keeps "open another
    chapter", because on disk there usually is one (that is the D24 case exactly)."""
    with tempfile.TemporaryDirectory() as root:
        from studio.app import StudioWindow
        both = []
        for cc in (1, 2):
            p = os.path.join(root, f"GX{cc:02d}0087.MP4")
            with open(p, "wb") as f:
                f.write(_JSON_STUB)
            both.append(p)
        exc = ValueError("none of these files is a readable video: GX010087.MP4, GX020087.MP4")
        msg = StudioWindow._load_failure_message(both, exc)
        assert "different recording" in msg, msg
        assert "another chapter" not in msg, msg
        # One file, opened on its own: another chapter of it may well be on disk.
        solo = StudioWindow._load_failure_message([both[0]], exc)
        assert "another chapter" in solo, solo
    print("test_an_all_junk_recording_is_not_told_to_open_another_chapter OK")


def test_the_reference_recording_is_not_exempt_from_the_notice():
    """D5 — the one surface the honesty rule skipped. Every Δ on screen is measured against the
    cross-recording reference; a reference that lost a chapter reported to the console only."""
    from test_central_view_realqt import _studiowindow_with_view

    win, _view = _studiowindow_with_view()
    try:
        for attr, value in (("_drop_notice", None), ("_timing_restore_failed", False),
                            ("_timing_restore_unreadable", False), ("_tracks_unreadable", False),
                            ("_notice", None), ("_load_token", 0), ("_ref_load_token", 0),
                            ("_load_workers", set()), ("_pending_load", None),
                            ("_placeholder_timer", None)):
            setattr(win, attr, value)
        win.session.skipped_chapters = []

        class _Ref:
            skipped_chapters = ["/d/D24/GX010060.MP4"]

        base = win._session_notice() or ""
        win.session.reference_session = lambda: _Ref()
        notice = win._session_notice()
        assert "GX010060.MP4" in notice, notice
        assert "from the reference recording" in notice, notice
        assert notice.startswith(base), (base, notice)
        # A reference with nothing skipped stays silent.
        _Ref.skipped_chapters = []
        assert win._session_notice() == (base or None)
    finally:
        win.close()
        _APP.processEvents()
    print("test_the_reference_recording_is_not_exempt_from_the_notice OK")


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    # NEVER print a blanket pass line over a silent skip. Half this file is gated on a submodule
    # fixture, and "all tests passed" under a fixture that isn't checked out is the exact false-
    # pass shape this repo has been bitten by before.
    if _SKIPPED:
        print(f"\nunreadable-chapter tests: {len(_SKIPPED)} SKIPPED (fixture absent) — "
              + "; ".join(_SKIPPED))
        print("the rest passed; this run did NOT cover the load path")
        return
    print("\nall unreadable-chapter tests passed")


if __name__ == "__main__":
    _run_all()
