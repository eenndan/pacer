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


def _have_fixture() -> bool:
    if os.path.exists(FIXTURE):
        return True
    print(f"skip: fixture {FIXTURE} not checked out (submodule)")
    return False


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
def test_is_mp4_container_admits_real_media_and_refuses_junk():
    """The discriminator, on every shape it has to separate. PERMISSIVE by design on the admit
    side: refusing a real chapter would lose footage, admitting a foreign one only defers the
    refusal to the GPMF parser, which is where it belongs."""
    with tempfile.TemporaryDirectory() as root:
        cases = {}
        cases["json"] = os.path.join(root, "GX010060.MP4")
        with open(cases["json"], "wb") as f:
            f.write(_JSON_STUB)
        cases["text"] = os.path.join(root, "NotAVideo.MP4")
        with open(cases["text"], "w", encoding="utf-8") as f:
            f.write("a plain text file the user renamed to .MP4\n" * 40)
        cases["zeros"] = os.path.join(root, "GX010098.MP4")
        with open(cases["zeros"], "wb") as f:
            f.write(b"\x00" * 16)          # the 16-byte name-only stubs other suites build
        cases["empty"] = os.path.join(root, "Empty.MP4")
        open(cases["empty"], "wb").close()
        cases["missing"] = os.path.join(root, "NoSuchFile.MP4")
        cases["folder"] = os.path.join(root, "AFolder.MP4")
        os.makedirs(cases["folder"])
        for key, path in cases.items():
            assert not chapters.is_mp4_container(path), f"{key} was admitted as an MP4"

        # …and the admit side: a real MP4 header, and the truncated-real-chapter shape the load
        # failure table classifies separately (it IS an MP4, only an incomplete one).
        truncated = os.path.join(root, "GX010097.MP4")
        with open(truncated, "wb") as f:
            f.write(b"\x00\x00\x00\x18ftypmp41" + b"\x00" * 4096)
        assert chapters.is_mp4_container(truncated)
        for box in (b"moov", b"mdat", b"free", b"wide"):
            other = os.path.join(root, f"Other{box.decode()}.MP4")
            with open(other, "wb") as f:
                f.write(b"\x00\x00\x00\x18" + box + b"\x00" * 32)
            assert chapters.is_mp4_container(other), box
    if _have_fixture():
        assert chapters.is_mp4_container(FIXTURE), "the committed sample is a real MP4"
    print("test_is_mp4_container_admits_real_media_and_refuses_junk OK")


def test_split_non_mp4_partitions_and_keeps_order():
    if not _have_fixture():
        return
    with tempfile.TemporaryDirectory() as root:
        paths = _recording(root, junk_chapter=2, real_chapters=(1, 3))
        openable, skipped = chapters.split_non_mp4(paths)
        assert [os.path.basename(p) for p in openable] == ["GX010099.MP4", "GX030099.MP4"]
        assert [os.path.basename(p) for p in skipped] == ["GX020099.MP4"]
        # The partition is TOTAL — nothing invented, nothing lost.
        assert sorted(openable + skipped) == sorted(paths)
    assert chapters.split_non_mp4([]) == ([], [])
    print("test_split_non_mp4_partitions_and_keeps_order OK")


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
    print("test_skipped_notice_names_the_files OK")


# ==================================================== discovery is deliberately NOT filtered
def test_discovery_still_lists_the_junk_sibling_and_keeps_the_recording_key():
    """A CONTRACT, not an oversight. `sidecar.sidecar_path` and `library.fingerprint` identify a
    recording by `discover_siblings(...)[0]`'s stem; dropping an unopenable chapter 1 there would
    move the user's saved start/finish line and their library history onto chapter 2's stem. The
    skip belongs to the LOAD, which is what actually cannot read the file."""
    if not _have_fixture():
        return
    with tempfile.TemporaryDirectory() as root:
        paths = _recording(root, junk_chapter=1, real_chapters=(2, 3))
        sibs = chapters.discover_siblings(paths[1])          # opened via an INTACT chapter
        assert [os.path.basename(p) for p in sibs] == [
            "GX010099.MP4", "GX020099.MP4", "GX030099.MP4"], sibs
        # The recording key is unchanged by the junk chapter's presence.
        assert sidecar.sidecar_path(paths[1]) == os.path.join(root, "GX010099.pacer.json")
        assert library.fingerprint("GX010099") == library.fingerprint("GX020099")
    print("test_discovery_still_lists_the_junk_sibling_and_keeps_the_recording_key OK")


# ==================================================== the load skips it, and says so
def test_load_skips_the_junk_sibling_and_records_it():
    """THE BUG, end to end: the full-recording load of a chaptered recording whose chapter 1 is not
    video. It must SUCCEED on the intact chapters and report the one it left out."""
    if not _have_fixture():
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
    if not _have_fixture():
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


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall unreadable-chapter tests passed")


if __name__ == "__main__":
    _run_all()
