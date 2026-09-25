"""The export findings of the 2026-09-25 hands-on QA (EXP lane), each driven the way the owner meets it.

WHY IT EXISTS. The QA drove his MK recording through the real export flow and found what no test
then in the suite looked at:
  * EXP-1: every export proposed `GX010067_overlay.mp4`, whatever the lap and scope — the lap left
    the name in #262, so a second lap's clip replaced the first or had to be renamed by hand. His
    own files read `GX010065_lap22_overlay.mp4`;
  * EXP-2: an unpadded clip (the default run-up) never showed the lap time: its last frame sat up
    to one frame before the finish and read 1:07.465 against the table's 1:07.479;
  * EXP-4: All laps replaced existing per-lap files without a word, while the save panel asked
    about a base name the batch never wrote;
  * EXP-5: a cancelled PNG sequence left its frames (885 PNGs, 41.2 MB), and a re-export into the
    same folder mixed two exports;
  * EXP-6: the disk refusal's sentence had lost a noun, and Show Details repeated it;
  * EXP-9: a cancelled All-laps batch kept its finished files without saying so;
  * HEALTH-3: an export left no line in the session log — no encoder, frame, rate or time.
Each test below was watched failing on the tree before the fix (main @ de44d8a).

The dialogs are the real ones, answered by patching `exec`; the save panel and the folder chooser
are recorded and answered; renders are replaced by recorders except in the EXP-2 test, which runs
the real `Renderer` over a generated clip (libx264, software decode, so it holds on CI too).

Run: python tests/test_export_polish.py
"""
import logging
import os
import re
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PACER_NO_MEDIA"] = "1"

# test_export_gates diverts every store into a temp tree on import, before any window exists.
from PySide6.QtWidgets import (  # noqa: E402
    QDialog,
    QFileDialog,
    QMessageBox,
    QProgressDialog,
)
from test_export_gates import (  # noqa: E402
    FakeSession,
    _clear_export_preset,
    _combo,
    _FakeSpec,
    _FakeVideoWorker,
    _window,
)
from test_export_pipeline import _moving_clip, _Session  # noqa: E402

from studio import export_controller  # noqa: E402
from studio import export_video as ev  # noqa: E402
from studio._signal import fmt_time  # noqa: E402
from studio.export_controller import ExportController  # noqa: E402
from studio.workers import VideoExportWorker  # noqa: E402

SCOPE = {"this": 0, "best": 1, "all": 2, "session": 3}          # _EXPORT_SCOPE_OPTIONS rows
CONTENT = {"burned": 0, "prores": 1, "png": 2}                  # _EXPORT_CONTENT_OPTIONS rows
THIS_LAP = 2                                                    # lap label 3; the best is lap 0


def _recording(td: str) -> str:
    src = os.path.join(td, "GX010067.MP4")
    open(src, "wb").close()
    return src


def _drive(win, scope_row: int, content_row: int, folder: str, replace: bool | None = None):
    """One File ▸ Export overlay video… through the real entry point, on lap THIS_LAP: the options
    dialog answered with `scope_row`/`content_row`, the save panel recorded and cancelled, the
    folder chooser recorded and answered `folder`, a replace box recorded and answered Replace
    (`replace`) or Cancel, and the render replaced by a recorder of the paths it was handed.
    Returns (prompts, boxes, renders)."""
    prompts, boxes, renders = [], [], []
    saved = {(ev, "ffmpeg_available"): ev.ffmpeg_available,
             (ev, "probe_video_size"): ev.probe_video_size,
             (QFileDialog, "getSaveFileName"): QFileDialog.getSaveFileName,
             (QFileDialog, "getExistingDirectory"): QFileDialog.getExistingDirectory,
             (QDialog, "exec"): QDialog.exec, (QMessageBox, "exec"): QMessageBox.exec,
             (QMessageBox, "clickedButton"): QMessageBox.clickedButton,
             (ExportController, "_run_video_export"): ExportController._run_video_export,
             (ExportController, "_export_lap_id"): ExportController._export_lap_id}
    ev.ffmpeg_available = lambda: True
    ev.probe_video_size = lambda _path: (3840, 2160, 60000 / 1001)
    QDialog.exec = lambda dlg: (_combo(dlg, "Export").setCurrentIndex(scope_row),
                                _combo(dlg, "Contents").setCurrentIndex(content_row),
                                QDialog.Accepted)[-1]
    QFileDialog.getSaveFileName = staticmethod(
        lambda _w, _title, path, filt: (prompts.append(("save", os.path.basename(path))),
                                        ("", ""))[1])
    QFileDialog.getExistingDirectory = staticmethod(
        lambda _w, title, _start: (prompts.append(("folder", title)), folder)[1])
    QMessageBox.exec = lambda box, *_a, **_k: boxes.append(box.text()) or 0
    QMessageBox.clickedButton = lambda box: next(
        (b for b in box.buttons() if replace and b.text() == "Replace"), None)

    def _record(_self, specs, **_k):
        renders.append([s.out_path for s in specs])
        for spec in specs:
            spec.source.cleanup()
    ExportController._run_video_export = _record
    ExportController._export_lap_id = lambda _self: THIS_LAP
    try:
        win.exports.export_overlay_video()
    finally:
        for (owner, name), value in saved.items():
            setattr(owner, name, value)
    return prompts, boxes, renders


# ============================================================ EXP-1 — the names say which lap
def test_every_export_proposes_a_name_that_says_which_lap():
    """The owner's convention, `<stem>_lap{N}_overlay.mp4`, with N the lap table's 1-based label,
    for every scope and content; a batch and a PNG sequence are asked for as a folder and their
    files named per lap; the comparison names both laps and the card its lap."""
    _clear_export_preset()
    with tempfile.TemporaryDirectory() as td:
        src = _recording(td)
        win = _window(FakeSession(laps=(0, 1, 2)), paths=(src,))
        got = {key: _drive(win, SCOPE[scope], CONTENT[content], td)
               for key, scope, content in (("this", "this", "burned"), ("best", "best", "burned"),
                                           ("session", "session", "burned"),
                                           ("prores", "this", "prores"), ("png", "this", "png"),
                                           ("all", "all", "burned"))}
        assert got["this"][0] == [("save", "GX010067_lap3_overlay.mp4")], got["this"]
        assert got["best"][0] == [("save", "GX010067_lap1_overlay.mp4")], got["best"]
        assert got["session"][0] == [("save", "GX010067_session_overlay.mp4")], got["session"]
        assert got["prores"][0] == [("save", "GX010067_lap3_overlay_alpha.mov")], got["prores"]
        # A folder is asked for, and the frames go into a fresh subfolder named for the lap.
        assert [p[0] for p in got["png"][0]] == ["folder"], got["png"]
        assert got["png"][2] == [[os.path.join(td, "GX010067_lap3_overlay_png")]], got["png"]
        # All laps: a folder, never a save panel about a base name the batch does not write.
        assert [p[0] for p in got["all"][0]] == ["folder"], got["all"]
        assert got["all"][2] == [[os.path.join(td, f"GX010067_lap{n}_overlay.mp4")
                                  for n in (1, 2, 3)]], got["all"]

        # The comparison and the card, through their own real entry points.
        asked = []
        saved = (QFileDialog.getSaveFileName, ExportController.compare_pair,
                 ExportController._ask_compare_options, ev.ffmpeg_available)
        QFileDialog.getSaveFileName = staticmethod(
            lambda _w, _t, path, filt: (asked.append(os.path.basename(path)), ("", ""))[1])
        ExportController.compare_pair = lambda _self: (0, 2, None, False)
        ExportController._ask_compare_options = lambda _self: object()
        ev.ffmpeg_available = lambda: True
        win._build_share_card = lambda: object()
        try:
            win.exports.export_compare_video()
            win.exports.export_share_card()
        finally:
            (QFileDialog.getSaveFileName, ExportController.compare_pair,
             ExportController._ask_compare_options, ev.ffmpeg_available) = saved
        assert asked == ["GX010067_lap1_vs_lap3_compare.mp4", "GX010067_lap1_card.png"], asked
        win.hide()
    _clear_export_preset()
    print("ok EXP-1: every export's default name says which lap")


# ======================================== EXP-4 — All laps asks once about what it will replace
def test_all_laps_asks_once_before_replacing_files_it_will_write():
    _clear_export_preset()
    with tempfile.TemporaryDirectory() as td:
        src = _recording(td)
        win = _window(FakeSession(laps=(0, 1, 2)), paths=(src,))
        old = [os.path.join(td, f"GX010067_lap{n}_overlay.mp4") for n in (1, 3)]
        for path in old:
            with open(path, "wb") as fh:
                fh.write(b"SENTINEL")
        prompts, boxes, renders = _drive(win, SCOPE["all"], CONTENT["burned"], td, replace=False)
        assert boxes == [f"2 of 3 files already exist — replace them?\n\n{td}"], boxes
        assert renders == [], f"Cancel on the replace box still rendered: {renders}"
        assert all(open(p, "rb").read() == b"SENTINEL" for p in old), "a kept file was touched"
        assert win.statusBar().currentMessage() == "video export cancelled"
        _prompts, boxes, renders = _drive(win, SCOPE["all"], CONTENT["burned"], td, replace=True)
        assert len(boxes) == 1 and len(renders) == 1 and len(renders[0]) == 3, (boxes, renders)
        win.hide()
    _clear_export_preset()
    print("ok EXP-4: All laps confirms once, about the files it writes")


# =================================== EXP-5 — a PNG sequence: its own folder, and no leftovers
def test_a_png_sequence_replaces_old_frames_and_a_cancel_takes_its_own_away():
    _clear_export_preset()
    with tempfile.TemporaryDirectory() as td:
        src = _recording(td)
        win = _window(FakeSession(laps=(0, 1, 2)), paths=(src,))
        sub = os.path.join(td, "GX010067_lap3_overlay_png")
        os.makedirs(sub)
        for name in ("overlay_000001.png", "overlay_002025.png", "notes.txt"):
            open(os.path.join(sub, name), "wb").close()
        _p, boxes, renders = _drive(win, SCOPE["this"], CONTENT["png"], td, replace=False)
        assert len(boxes) == 1 and boxes[0].startswith(
            "GX010067_lap3_overlay_png already holds 3 files — replace the frames in it?"), boxes
        assert renders == [] and len(os.listdir(sub)) == 3, "Cancel must leave the folder as it was"
        _p, boxes, renders = _drive(win, SCOPE["this"], CONTENT["png"], td, replace=True)
        assert renders == [[sub]], renders
        # The old export's frames are gone before the new one writes; nothing else is touched.
        assert os.listdir(sub) == ["notes.txt"], os.listdir(sub)
        win.hide()
    _clear_export_preset()

    # The worker's cleanup: a render that created its folder, wrote frames and was cancelled.
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "GX010067_lap3_overlay_png")
        spec = ev.ExportSpec(out_path=out, lap_id=2, t0=0.0, t1=1.0, src_path=os.path.join(td, "x"),
                             config=ev.OverlayConfig(overlay_only=True, alpha_codec=ev.ALPHA_PNG))

        class _CancelledMidway:
            def __init__(self, _session, _spec):
                pass

            def run(self, progress=None, cancel=None):
                os.makedirs(out)
                for i in range(1, 6):
                    open(os.path.join(out, f"overlay_{i:06d}.png"), "wb").close()
                raise ev.CancelledError("cancelled")

        ended = []
        worker = VideoExportWorker(None, spec, make_renderer=_CancelledMidway)
        worker.finished_export.connect(lambda ok, msg: ended.append((ok, msg)))
        worker.run()                          # on this thread: the signal is delivered directly
        assert ended == [(False, "cancelled")], ended
        assert not os.path.exists(out), f"a cancelled sequence left {os.listdir(out)}"
    print("ok EXP-5: a PNG sequence replaces its frames, and a cancel removes what it wrote")


# ===================================== EXP-9 — a cancelled batch names the files it kept
def test_a_cancelled_batch_says_which_finished_files_it_kept():
    win = _window(FakeSession())
    specs = [_FakeSpec(f"/tmp/pacer-kept/ride_lap{i}.mp4") for i in (1, 2, 3)]
    made, boxes = [], []
    orig = (export_controller.VideoExportWorker, QDialog.exec, QMessageBox.exec)

    def _exec(dlg):
        if not isinstance(dlg, QProgressDialog):
            return orig[1](dlg)
        made[0].finished_export.emit(True, "")          # file 1 finishes; file 2 starts
        dlg.canceled.emit()                             # the user presses Cancel on file 2
        made[1].finished_export.emit(False, "cancelled")
        return QDialog.Accepted

    export_controller.VideoExportWorker = lambda s, sp, mk=None: (
        made.append(_FakeVideoWorker(s, sp, mk)) or made[-1])
    QDialog.exec = _exec
    QMessageBox.exec = lambda box, *_a, **_k: boxes.append(box.text()) or 0
    try:
        win.exports._run_video_export(specs)
    finally:
        export_controller.VideoExportWorker, QDialog.exec, QMessageBox.exec = orig
    assert len(made) == 2, "the queue must stop at the cancelled file"
    assert len(boxes) == 1 and "after 1 of 3 files" in boxes[0], boxes
    assert "ride_lap1.mp4" in boxes[0] and "ride_lap2.mp4" not in boxes[0], boxes
    assert win.statusBar().currentMessage() == "video export cancelled — kept 1 finished file"
    win.hide()
    print("ok EXP-9: a cancelled batch names what it kept")


# ======================================== EXP-6 — the refusal is one sentence, said once
def test_the_space_refusal_reads_as_one_sentence_and_is_not_repeated_behind_details():
    with tempfile.TemporaryDirectory() as td:
        spec = ev.ExportSpec(out_path=os.path.join(td, "lap.mp4"), lap_id=0, t0=0.0, t1=60.0,
                             src_path="/a.MP4", config=ev.OverlayConfig(out_height=1080))
        orig_free = ev.free_bytes
        # 1 MB, not 20: the refusal must not depend on which H.264 encoder this machine resolves.
        # The guard's floor is per encoder (VideoToolbox 0.6 of its estimate, libx264 far lower,
        # since a CRF stream's size varies most), so 20 MB refused a minute of 1080p here and let it
        # through on the CI runner, which has no VideoToolbox session to open.
        ev.free_bytes = lambda _p, purgeable=True: 1_000_000
        try:
            ev.guard_free_space([spec], probe=lambda _p: (1920, 1080, 30.0))
            raise AssertionError("the guard let 1 MB hold a minute of 1080p")
        except ev.InsufficientSpaceError as exc:
            message = str(exc)
        finally:
            ev.free_bytes = orig_free
    assert re.search(r"has 1 MB free; even the smallest this export could be is [\d.]+ MB\.$",
                     message), message
    assert ev.is_refused_for_space(message), message

    win = _window(FakeSession())
    made, boxes = [], []
    orig = (export_controller.VideoExportWorker, QDialog.exec, QMessageBox.exec)

    def _exec(dlg):
        if not isinstance(dlg, QProgressDialog):
            return orig[1](dlg)
        made[0].finished_export.emit(False, message)
        return QDialog.Accepted

    export_controller.VideoExportWorker = lambda s, sp, mk=None: (
        made.append(_FakeVideoWorker(s, sp, mk)) or made[-1])
    QDialog.exec = _exec
    QMessageBox.exec = lambda box, *_a, **_k: boxes.append((box.text(), box.detailedText())) or 0
    try:
        win.exports._run_video_export([_FakeSpec("/tmp/pacer-unwritten/lap.mp4")],
                                      scope_text="this lap")
    finally:
        export_controller.VideoExportWorker, QDialog.exec, QMessageBox.exec = orig
    assert len(boxes) == 1 and message in boxes[0][0], boxes
    assert boxes[0][1] == "", f"Show Details repeats the body: {boxes[0][1]!r}"
    # The scope row reaches the worker for its session-log start line (HEALTH-3).
    assert made[0].log_scope == "this lap", made[0].log_scope
    win.hide()
    print("ok EXP-6: the refusal is one sentence, said once")


# =================================== HEALTH-3 — one log line at the start, one at the end
class _Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def test_an_export_logs_one_line_when_it_starts_and_one_when_it_ends():
    log = logging.getLogger("studio.workers")
    records, level = _Records(), log.level
    log.addHandler(records)
    log.setLevel(logging.INFO)
    try:
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "GX010067_lap3_overlay.mp4")

            class _Renders:
                def __init__(self, _session, _spec):
                    pass

                def describe(self):
                    return "burned-in, lap 3, 1920x1080@30 fps, encoder libx264"

                def frames_written(self):
                    return 5, 20

                def run(self, progress=None, cancel=None):
                    with open(out, "wb") as fh:
                        fh.write(b"\0" * 2_000_000)
                    return SimpleNamespace(frames=20)

            class _Cancels(_Renders):
                def run(self, progress=None, cancel=None):
                    raise ev.CancelledError("cancelled")

            for maker in (_Renders, _Cancels):
                worker = VideoExportWorker(None, SimpleNamespace(out_path=out), make_renderer=maker)
                worker.log_scope = "this lap"
                worker.run()
    finally:
        log.removeHandler(records)
        log.setLevel(level)
    lines = records.lines
    assert len(lines) == 4, lines
    assert lines[0] == "export started: this lap: burned-in, lap 3, 1920x1080@30 fps, encoder libx264"
    assert re.fullmatch(r"export finished: 20 frames in [\d.]+ s \([\d.]+ fps\), 2(\.0)? MB -> .+"
                        r"GX010067_lap3_overlay\.mp4", lines[1]), lines[1]
    assert lines[2] == lines[0], lines[2]
    assert re.fullmatch(r"export cancelled after 5 of 20 frames, [\d.]+ s -> .+\.mp4", lines[3]), \
        lines[3]
    print("ok HEALTH-3: an export logs where it started and how it ended")


# ======================================= EXP-2 — an unpadded clip ends ON its finish line
def test_an_unpadded_clip_ends_on_its_finish_and_reads_the_lap_time():
    """The real Renderer over a generated clip: a 2.99 s lap at 30 fps is 89.7 frames, so the
    half-open plan's last frame sat at 2.967 s and read `LAP 2   0:02.967`. It now ends on the
    finish frame (3.000 s), which reads the lap time exactly, and the frame before still runs."""
    if not ev.ffmpeg_available():
        print("skip EXP-2 render (no ffmpeg)")
        return
    with tempfile.TemporaryDirectory(prefix="pacer-exp2-") as td:
        src = os.path.join(td, "src.mp4")
        _moving_clip(src, 3.5)
        session = _Session(2.99)
        # Through `build_lap_spec`, the one place a lap becomes an export (and the app's path).
        spec = ev.build_lap_spec(session, os.path.join(td, "lap.mp4"), 1, src_path=src,
                                 config=ev.OverlayConfig(out_height=360, encoder="libx264",
                                                         hwaccel_decode=False, workers=1))
        assert (spec.t0, spec.t1) == (0.0, 2.99), spec
        seen = []
        orig = ev.overlay_values_at
        renderer = ev.Renderer(session, spec)      # (its pill budget walks every frame here)
        ev.overlay_values_at = lambda *a, **k: (seen.append(orig(*a, **k)), seen[-1])[1]
        try:
            result = renderer.run()
        finally:
            ev.overlay_values_at = orig
        last, before = seen[-1], seen[-2]
        label = ev._strip_runs(session, last, 0.0, False, None)[0]
        assert label == f"LAP 2   {fmt_time(2.99)}", f"the clip ends on {label!r}"
        assert result.frames == ev.frame_count(0.0, 2.99, 30.0) + 1 == 91, result.frames
        assert last.lap_finished and last.t >= 2.99 - 1e-9, (last.t, last.lap_finished)
        assert not before.lap_finished, before.t
        described = renderer.describe()
    # The session log's facts are the resolved ones.
    for part in ("burned-in, lap 2, run-up 0.00 s / run-off 0.03 s", "source src.mp4",
                 "640x360@30 fps", "91 frames", "encoder libx264", "software decode",
                 "relay off"):
        assert part in described, (part, described)
    print(f"ok EXP-2: the unpadded clip ends on {label!r} ({result.frames} frames)")


if __name__ == "__main__":
    test_every_export_proposes_a_name_that_says_which_lap()
    test_all_laps_asks_once_before_replacing_files_it_will_write()
    test_a_png_sequence_replaces_old_frames_and_a_cancel_takes_its_own_away()
    test_a_cancelled_batch_says_which_finished_files_it_kept()
    test_the_space_refusal_reads_as_one_sentence_and_is_not_repeated_behind_details()
    test_an_export_logs_one_line_when_it_starts_and_one_when_it_ends()
    test_an_unpadded_clip_ends_on_its_finish_and_reads_the_lap_time()
    print("all export-polish tests passed")
