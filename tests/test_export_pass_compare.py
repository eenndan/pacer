"""Export a compare AT ONE PASS ("Export comparison video ▸ This pass", cool/export-pass-compare).

"Compare with my typical pass" (#626) and a Coaching row's Compare put two passes side by side,
each pane playing its own pass clip from its lead-in; File ▸ Export comparison video then rendered
the two WHOLE laps (DECISIONS §19/§20's open item). What is pinned here:

  * THE PICKER: with the compare anchored at a pass it opens on "This pass — C5, lap 6 against
    lap 9" (never remembered), "Both whole laps" one row down; a whole-lap compare (C) has no
    such row, and its dialog is the one it always was;
  * THE WINDOWS ARE THE CLIPS THE PANES PLAY: each pane's export window is its pass clip on the
    media clock, the very clip `compare_pass_with_typical` seeks the pane to and pauses it at;
  * THE Δ PLATE IS THE SEAM'S RULE (`compare_controller.PassAnchor`): no number before pane A's
    entry, the gap since the entry inside it (neutral and arrowless inside the 0.07 s step), and
    after its exit the two passes' printed gap, held and neutral for a typical-pass compare —
    never a verdict the live compare refuses. No lap clock on the plates, no track bar; each plate
    gains its pass time from its own exit on;
  * A REAL RENDER at 360p panes on a synthetic two-chapter GoPro whose picture codes its own
    source frame, pane A's clip straddling the chapter seam and pane B's not: the file holds the
    longer clip's frames, each pane is its own clip frame for frame, and the shorter one holds its
    last frame; its sound is pane A's clip's;
  * THE WHOLE-LAP COMPARE IS UNCHANGED: the same pair through "Both whole laps" builds the
    track-locked spec, plates and bar it always did (and tests/test_export_compare.py passes as
    it was).

Run: python tests/test_export_pass_compare.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QLabel,
    QMessageBox,
)

_APP = QApplication.instance() or QApplication([])
from studio import chapters, theme  # noqa: E402
from studio import export_compare as ec  # noqa: E402
from studio import export_video as ev  # noqa: E402
from studio.compare_controller import PassAnchor  # noqa: E402
from studio.library import PB_PRECISION_S  # noqa: E402
from studio.stats import printed_gap_s  # noqa: E402

theme.apply_theme(_APP)
theme.register_fonts()

_DEMO: dict = {}
ARROWS = ("▲", "▼")


def _settle(seconds=0.1):
    end = time.time() + seconds
    while time.time() < end:
        _APP.processEvents()
        time.sleep(0.005)


def _demo_path() -> str:
    if "path" not in _DEMO:
        from studio.dev import make_demo as md
        _DEMO["dir"] = tempfile.TemporaryDirectory(prefix="pass_compare_demo_")
        _DEMO["path"] = os.path.join(_DEMO["dir"].name, "GX019000.MP4")
        md.make(_DEMO["path"], render=False)
    return _DEMO["path"]


def _window():
    if "win" not in _DEMO:
        from studio.app import StudioWindow
        for k in ("critical", "warning", "information"):
            setattr(QMessageBox, k, staticmethod(lambda *a, **_kw: QMessageBox.Ok))
        win = StudioWindow([_demo_path()])
        deadline = time.time() + 60
        while win.view is None and time.time() < deadline:
            _settle(0.02)
        assert win.view is not None, "the demo did not load"
        win.resize(1280, 800)
        win.show()
        _settle(0.2)
        QTest.keyClick(win, Qt.Key_Escape)   # leave the debrief landing
        _settle(0.1)
        win._share_card_blocked = lambda: False
        _DEMO["win"] = win
    return _DEMO["win"]


def _offered(win):
    """The first (lap, corner) whose pass the app offers to compare with the typical one."""
    s = win.session
    for c in s.corners.corner_list():
        for lap in s.valid_lap_ids():
            if win.view.typical_compare_refusal(lap, c.cid) is None:
                return lap, c.cid
    raise AssertionError("the demo offers no typical-pass compare")


def _typical_compare(win):
    """Leave any compare, then "Compare with my typical pass" on the first offered pass."""
    if win.view.compare.active:
        win.view.compare.exit()
        _settle(0.05)
    lap, cid = _offered(win)
    assert win.view.compare_pass_with_typical(lap, cid)
    _settle(0.05)
    anchor = win.view.compare.anchor
    assert anchor is not None and anchor.key == cid and anchor.held_neutral
    return lap, cid, win.view.compare.lap_b, anchor


def _rows(dlg):
    form = dlg.findChild(QFormLayout)
    rows = {}
    for r in range(form.rowCount()):
        label = form.itemAt(r, QFormLayout.ItemRole.LabelRole)
        field = form.itemAt(r, QFormLayout.ItemRole.FieldRole)
        if label is not None and field is not None:
            rows[label.widget().text()] = field.widget()
    return rows


def _export_through_the_menu(win, act):
    """File ▸ Export comparison video through its menu action's `.trigger()`, the REAL dialog's
    `exec()` answered by `act(dlg, rows)` from a QTimer; the save panel answers a path in the
    demo's temp dir and the render is captured, not run."""
    seen = {"specs": None, "save": None}
    ctl = win.exports
    orig_save, orig_run = ctl._export_save_path, ctl._run_video_export

    def save(_title, suffix, _filt):
        seen["save"] = suffix
        return os.path.join(_DEMO["dir"].name, "out" + suffix)

    def run(specs, **_kw):
        seen["specs"] = specs

    def answer():
        dlg = next((w for w in QApplication.topLevelWidgets()
                    if isinstance(w, QDialog) and w.isVisible()
                    and w.windowTitle() == "Export comparison video"), None)
        if dlg is None:
            QTimer.singleShot(20, answer)
            return
        result = act(dlg, _rows(dlg))
        (dlg.accept if result == QDialog.Accepted else dlg.reject)()

    ctl._export_save_path, ctl._run_video_export = save, run
    QTimer.singleShot(50, answer)
    try:
        ctl.sync_menu()
        assert win._export_compare_action.isEnabled()
        win._export_compare_action.trigger()
    finally:
        ctl._export_save_path, ctl._run_video_export = orig_save, orig_run
    return seen


def _read_dialog(dlg, rows, opened, pick=None):
    scope = rows.get("Export")
    opened["rows"] = list(rows)
    if isinstance(scope, QComboBox):
        opened["scope"] = scope.currentText()
        opened["items"] = [scope.itemText(i) for i in range(scope.count())]
        if pick is not None:
            scope.setCurrentIndex(pick)
    opened["notes"] = [lab.text() for lab in dlg.findChildren(QLabel)
                       if lab.property("role") in ("Note", "Hint")]
    return QDialog.Accepted


# ------------------------------------------------------------------ the picker and the windows
def test_a_pass_compare_opens_the_picker_on_this_pass_and_exports_both_clips():
    win = _window()
    lap, cid, lap_b, anchor = _typical_compare(win)
    label, pair = anchor.label, f"lap {lap + 1} against lap {lap_b + 1}"
    opened = {}
    seen = _export_through_the_menu(win, lambda d, r: _read_dialog(d, r, opened))
    assert opened["items"] == [f"This pass — {label}, {pair}", f"Both whole laps — {pair}"], opened
    assert opened["scope"] == opened["items"][0], opened
    assert opened["rows"][0] == "Export", opened["rows"]
    notes = " ".join(opened["notes"])
    assert (f"Both passes through {label}, each from its own lead-in to 1 s after its exit, "
            "started together.") in notes, notes
    assert "The shorter pass holds its last frame" in notes, notes
    assert seen["save"] == f"_lap{lap + 1}_vs_lap{lap_b + 1}_{label}_compare.mp4", seen["save"]
    (spec,) = seen["specs"]
    try:
        assert spec.pass_pair is not None and spec.pass_pair.rule is anchor
        s = win.session
        # THE WINDOWS ARE THE CLIPS THE PANES PLAY: the clip each pane was seeked to and pauses
        # at (`compare_pass_with_typical`), on the media clock, entry and exit inside.
        for side, lap_id, (t0, t1) in ((0, lap, (spec.t0, spec.t1)),
                                       (1, lap_b, (spec.t_b0, spec.t_b1))):
            clip = s.stretch_clip(lap_id, cid)
            assert anchor.sides[side][2:4] == tuple(map(float, clip)), (side, anchor.sides)
            assert (t0, t1) == (s.media_time(clip[0]), s.media_time(clip[1])), (side, t0, t1)
            assert (t0, t1) == ev.pass_window_for_export(s, lap_id, cid)[::3]
        pane_end = win.view.video.pane._clip_end
        assert pane_end is None or abs(pane_end - anchor.sides[0][3]) < 1e-9, pane_end
        assert not spec.ends_on_finish and spec.lap_b == lap_b and spec.lap_id == lap
    finally:
        spec.cleanup()
    print(f"ok the pass compare picker opens on {opened['scope']!r}")


def test_both_whole_laps_is_the_whole_lap_compare_unchanged():
    win = _window()
    lap, _cid, lap_b, _anchor = _typical_compare(win)
    opened = {}
    seen = _export_through_the_menu(win, lambda d, r: _read_dialog(d, r, opened, pick=1))
    assert seen["save"] == f"_lap{lap + 1}_vs_lap{lap_b + 1}_compare.mp4", seen["save"]
    (spec,) = seen["specs"]
    try:
        assert spec.pass_pair is None and spec.ends_on_finish
        assert (spec.t0, spec.t1) == ev.lap_window_for_export(win.session, lap)
        r = ec.CompareRenderer(win.session, spec)
        assert r._painter._progress, "the whole-lap compare keeps its track bar"
        assert not any(f.neutral for f in r.frames)
        assert re.fullmatch(r"LAP \d+   \d:\d\d\.\d{3}", r.frames[0].caption_a), r.frames[0]
    finally:
        spec.cleanup()
    print("ok Both whole laps exports the track-locked whole-lap compare")


def test_a_whole_lap_compare_has_no_scope_row():
    win = _window()
    if win.view.compare.active:
        win.view.compare.exit()
        _settle(0.05)
    win.view.video.compare_btn.click()     # C: the two whole laps
    _settle(0.05)
    assert win.view.compare.active and win.view.compare.anchor is None
    opened = {}
    seen = _export_through_the_menu(win, lambda d, r: _read_dialog(d, r, opened))
    assert "Export" not in opened["rows"], opened["rows"]
    assert opened["notes"][0] == win.exports._COMPARE_LAPS_DESC, opened["notes"]
    (spec,) = seen["specs"]
    spec.cleanup()
    assert spec.pass_pair is None and win.exports.compare_pass_chosen is None
    win.view.compare.exit()
    _settle(0.05)
    print("ok a whole-lap compare's picker has no Export row")


# ------------------------------------------------------------------ what every frame says
def test_the_delta_plate_is_blank_then_live_then_held_neutral():
    win = _window()
    lap, cid, lap_b, anchor = _typical_compare(win)
    s = win.session
    out = os.path.join(_DEMO["dir"].name, "plate.mp4")
    spec = ec.build_pass_compare_spec(s, out, lap, lap_b, anchor,
                                      config=ec.CompareConfig(out_height=360),
                                      label_a=f"LAP {lap + 1}", label_b=f"LAP {lap_b + 1}")
    try:
        r = ec.CompareRenderer(s, spec)
    finally:
        spec.cleanup()
    track, frames = r.lock, r.frames
    n_a, n_b = ec.pass_frame_counts(spec, r.fps)
    assert len(frames) == r.total_frames == max(n_a, n_b) and n_a != n_b, (n_a, n_b)
    # each pane steps through its own clip, then the shorter one holds its last frame
    for t, n_own in ((track.t_a, n_a), (track.t_b, n_b)):
        assert np.all(np.diff(t[:n_own]) > 0) and np.all(t[n_own - 1:] == t[n_own - 1])
    entry, exit_ = anchor.sides[0][:2]
    d0 = s.delta_between(lap, lap_b, entry)
    held = printed_gap_s(*anchor.pass_s)
    label = anchor.label
    kinds = set()
    exit_b = anchor.sides[1][1]
    timed_b = f"LAP {lap_b + 1}   {label} · {anchor.pass_s[1]:.2f} s"
    for f, ta, tb in zip(frames, track.t_a, track.t_b, strict=True):
        assert "0:" not in f.caption_a and "0:" not in f.caption_b, f   # no lap clock
        # pane B's plate gains ITS pass time on its OWN exit frame, never before or after
        assert f.caption_b == (timed_b if tb > exit_b else f"LAP {lap_b + 1}   {label}"), (
            tb, exit_b, f.caption_b)
        timed_a = f"LAP {lap + 1}   {label} · {anchor.pass_s[0]:.2f} s"
        assert f.caption_a == (timed_a if ta > exit_ else f"LAP {lap + 1}   {label}"), f.caption_a
        if ta < entry:
            kinds.add("blank")
            assert f.delta is None and f.delta_run == "Δ —" and f.neutral, f
            assert f.caption_a == f"LAP {lap + 1}   {label}", f.caption_a
        elif ta <= exit_:
            kinds.add("live")
            want = s.delta_between(lap, lap_b, ta) - d0
            assert abs(f.delta - want) < 1e-9, (f.delta, want)
            level = abs(float(theme.format_delta_value(want).replace("−", "-"))) < PB_PRECISION_S
            assert f.neutral == level, f
            assert any(a in f.delta_run for a in ARROWS) != level, f.delta_run
        else:
            kinds.add("held")
            assert f.delta == held and f.neutral, f
            assert not any(a in f.delta_run for a in ARROWS), f.delta_run
            assert f.caption_a == f"LAP {lap + 1}   {label} · {anchor.pass_s[0]:.2f} s", f
    assert kinds == {"blank", "live", "held"}, kinds
    assert not r._painter._progress, "no track bar: a pass has no one track position"
    print(f"ok the Δ plate: blank, live, then held neutral at {theme.format_delta_run(held, arrow=False)}")


# ------------------------------------------------------------------ a real render, the seam
SW, SH = 640, 360
CYCLE = 12          # the picture codes its global source frame modulo this, as a flat grey level


def _level(k):
    return 24 + 12 * (k % CYCLE)


def _coded_picture(path, truth, first_payload, n_payloads, ffmpeg):
    """`synth_gopro.generate(video=)`: one chapter whose every frame is flat grey at
    `_level(global frame)`, 29.97 fps (tests/test_export_pass.py's picture)."""
    g0 = first_payload * 30
    raw = np.concatenate([np.full((SH, SW), _level(g0 + i), np.uint8).ravel()
                          for i in range(CYCLE)])
    src = path + ".gray"
    raw.tofile(src)
    subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-stream_loop", "-1",
                    "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{SW}x{SH}",
                    "-framerate", "30000/1001", "-i", src, "-frames:v", str(n_payloads * 30),
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-x264-params", "keyint=30:min-keyint=30:scenecut=0",
                    "-video_track_timescale", "30000", "-an", "-n", path],
                   check=True, capture_output=True)


def _anchor(s, a, b, cid):
    sides = []
    for lap in (a, b):
        subject = ev.pass_subject(s, lap, cid)
        sides.append((*s.stretch_edges(lap, cid), *s.stretch_clip(lap, cid), subject.time_s))
    anchor = PassAnchor.between(s, a, b, f"C{cid}", "", *sides, key=cid, held_neutral=True)
    assert anchor is not None
    return anchor


def _codes(frames, rows):
    mid = frames[:, rows, SW // 2 - 20:SW // 2 + 20, 1].mean(axis=(1, 2))
    return np.round((mid - 24) / 12).astype(int) % CYCLE


def _off(got, want):
    off = (got - want) % CYCLE
    return np.where(off > CYCLE // 2, off - CYCLE, off)


def test_a_real_render_plays_each_clip_and_holds_the_shorter_across_the_seam():
    if not ev.ffmpeg_available():
        assert os.path.join(".pixi", "envs") not in os.environ.get("CONDA_PREFIX", ""), \
            "ffmpeg is a locked dependency inside the pixi env"
        return
    from studio.dev import synth_gopro as sg
    from studio.session import Session
    tmp = tempfile.TemporaryDirectory(prefix="pass_compare_seam_")
    rec = sg.generate(os.path.join(tmp.name, "rec"), laps=3, chapters=2, video=_coded_picture,
                      audio=True)
    s = Session.load(chapters.discover_siblings(rec.paths[0]))
    seam = s.chapters.chapters[1].offset
    laps = s.valid_lap_ids()
    fps = 30000 / 1001
    seamed, longest = [], []
    for c in s.corners.corner_list():
        wins = {lap: ev.pass_window_for_export(s, lap, c.cid) for lap in laps}
        if any(w is None or ev.pass_subject(s, lap, c.cid) is None for lap, w in wins.items()):
            continue
        for a in laps:
            for b in laps:
                if a == b:
                    continue
                over = [wins[x][0] < seam < wins[x][3] for x in (a, b)]
                diff = abs(ev.frame_count(wins[a][0], wins[a][3], fps)
                           - ev.frame_count(wins[b][0], wins[b][3], fps))
                if over == [True, False]:
                    seamed.append((diff, a, b, c.cid))
                elif not any(over):
                    longest.append((diff, a, b, c.cid))
    assert seamed, "no pass of the synthetic recording straddles the seam in one pane only"
    # Both ways round: the seam in pane A and pane B's clip held, then the seam in pane B and
    # pane A's own decoder running out first, its frame held. The seam pairs' clips differ by one
    # frame on this fixture, so the pair whose clips differ the most (3 frames, measured
    # 2026-11-20) is rendered both ways too: a hold of several frames, in each pane.
    _diff, across, other, cid = max(seamed)
    for a, b in ((across, other), (other, across)):
        _render_one_way(s, a, b, cid, tmp.name, seam=(a == across, a != across))
    diff, one, two, cid = max(longest)
    assert diff >= 3, f"no pair of clips differs by several frames ({diff})"
    for a, b in ((one, two), (two, one)):
        _render_one_way(s, a, b, cid, tmp.name, seam=(False, False))
    tmp.cleanup()


def _render_one_way(s, a, b, cid, folder, seam):
    out = os.path.join(folder, f"pass_compare_{a}_{b}_{cid}.mp4")
    cfg = ec.CompareConfig(out_height=SH, encoder="libx264", hwaccel_decode=False)
    spec = ec.build_pass_compare_spec(s, out, a, b, _anchor(s, a, b, cid), config=cfg,
                                      label_a=f"LAP {a + 1}", label_b=f"LAP {b + 1}")
    spans = (bool(spec.source.concat_list_path), bool(spec.source_b.concat_list_path))
    assert spans == seam, f"which pane's clip is the two-chapter span: {spans}, not {seam}"
    try:
        r = ec.CompareRenderer(s, spec)
        res = r.run()
    finally:
        spec.cleanup()
    fps = 30000 / 1001
    frame = 1001 / 30000
    n_a, n_b = ec.pass_frame_counts(spec, fps)
    n = max(n_a, n_b)
    assert n_a != n_b, "the two clips happen to be the same length; the hold is untested"
    assert res.frames == n, (res.frames, n_a, n_b)
    streams = json.loads(subprocess.run(
        [ev.FFPROBE, "-v", "error", "-show_entries", "stream=codec_type,duration,nb_frames",
         "-of", "json", res.out_path], capture_output=True, text=True, check=True).stdout)["streams"]
    video = next(x for x in streams if x["codec_type"] == "video")
    audio = next(x for x in streams if x["codec_type"] == "audio")
    assert int(video["nb_frames"]) == n, (video, n)
    # pane A's sound, for pane A's clip
    assert abs(float(audio["duration"]) - n_a * frame) <= frame, (audio, n_a)
    raw = subprocess.run([ev.FFMPEG, "-nostdin", "-v", "error", "-i", res.out_path, "-f",
                          "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                         capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, 2 * SH, SW, 3)
    assert len(frames) == n
    # Each pane is its OWN clip, frame for frame, then held. Frame i — the first one included — is
    # the source frame SHOWING at its media time (floor), exactly: both panes go through
    # `build_decode_cmd`, as every export does. Until cool/export-first-frame this allowed the
    # whole clip one source frame ahead, which a pane B clip starting 0.88 of a frame in was
    # (tests/test_export_pass.py's sweep has every start).
    i = np.arange(n)
    for name, rows, t0, n_own in (("A", slice(140, 180), spec.t0, n_a),
                                  ("B", slice(SH + 180, SH + 220), spec.t_b0, n_b)):
        got = _codes(frames, rows)
        at = (t0 + np.minimum(i, n_own - 1) / fps) * fps
        off = _off(got, np.floor(at + 1e-6).astype(int))
        assert not np.any(off), (
            f"pane {name} (clip start {at[0] % 1:.2f} of a frame in): frames off their clip at "
            f"{np.flatnonzero(off)[:6]} by {off[np.flatnonzero(off)][:6]}")
        steps = np.diff(got[:n_own]) % CYCLE
        assert np.all(steps == 1), f"pane {name}: a frame dropped or held inside its clip"
        if n_own < n:
            # held: the clip's LAST frame, the very picture, for the whole hold
            assert np.all(got[n_own - 1:] == got[n_own - 1]), f"pane {name} did not hold"
            last = frames[n_own - 1, rows].astype(int)
            worst = max(int(np.abs(frames[k, rows].astype(int) - last).max())
                        for k in range(n_own, n))
            assert worst <= 2, f"pane {name}'s held frames are not its last frame ({worst})"
    assert r.held_frames == 0 and r._held_a == 0
    where = "A" if seam[0] else "B" if seam[1] else "neither pane"
    print(f"ok a real render: {n} frames, pane A {n_a}, pane B {n_b}, the seam in {where}; "
          f"held {abs(n_a - n_b)}; clip starts {spec.t0 * fps % 1:.2f} / "
          f"{spec.t_b0 * fps % 1:.2f} of a frame in")


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {exc!r}")
            import traceback
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
