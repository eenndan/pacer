"""Export ONE CORNER PASS as a clip (`export_video.SCOPE_PASS`, W12 D1).

Corners ▸ Quickest passes plays a pass from its lead-in to 1 s after the corner's exit; this scope
renders that same window, with an overlay that cannot be misread. Before it, the exporter had lap
and session scopes only (discovery 6: market #25, workflow candidate 8). What is pinned:

  * THE WINDOW IS THE PLAYED CLIP: `pass_window_for_export` is `CornerModel.pass_clip` (the one
    definition) on the media clock, for every quickest pass of the demo, with the corner's entry
    and exit inside it — and the spec's timed span is that entry and exit;
  * THE FILE NAMES THE LAP AND THE CORNER: `GX…_lap13_C3_overlay.mp4`, like a lap export names
    its lap;
  * WHAT THE FRAME SAYS, before / in / after the corner: "LAP 13   C3", then from the exit frame
    "LAP 13   C3 · 2.32 s" (the row's own time). No lap clock (mid-lap it reads as a lap time),
    no lap Δ (it would read as this corner's gap), no `★ BEST`, never "fastest" — so a pass level
    with others claims nothing. In Classic and in Broadcast; the corner cards keep to this
    corner, and the ghost (whose gap is the lap's running Δ) sits a pass out;
  * LAP AND SESSION EXPORTS ARE UNTOUCHED: their strip runs are `_strip_runs`' own on every frame
    (the release A/B is a painter digest; the PR carries it);
  * THE DIALOG: opened from the pass on "This pass — C3, lap 13", the run-up row off (a pass has
    its own lead-in), the ghost off with its reason, never remembered as the next export's scope;
    from File ▸ Export the row is offered while Quickest passes names a pass; ⌘K lists
    "Export the C3 pass — lap 13…" only then;
  * THE CHAPTER SEAM: a real render of a pass whose lead-in straddles a two-chapter recording's
    seam (synth_gopro, a picture that codes its own source frame): every output frame is the
    source frame at its media time, across the seam; the corner time appears on exactly the
    planned exit frame; the file's frame count and duration are the plan's and its sound starts
    with its picture.

Run: python tests/test_export_pass.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QLabel,
    QMessageBox,
)

_APP = QApplication.instance() or QApplication([])
from studio import chapters, prefs, theme  # noqa: E402
from studio import export_video as ev  # noqa: E402
from studio.overlay import broadcast, corner_cards, ghost  # noqa: E402

theme.apply_theme(_APP)
theme.register_fonts()

_DEMO: dict = {}
FPS = 30.0


def _demo_path() -> str:
    if "path" not in _DEMO:
        from studio.dev import make_demo as md
        _DEMO["dir"] = tempfile.TemporaryDirectory(prefix="pass_export_demo_")
        _DEMO["path"] = os.path.join(_DEMO["dir"].name, "GX019000.MP4")
        md.make(_DEMO["path"], render=False)
    return _DEMO["path"]


def _session():
    if "session" not in _DEMO:
        from studio.session import Session
        _DEMO["session"] = Session.load([_demo_path()])
    return _DEMO["session"]


def _c3():
    """The demo's C3 quickest pass: (lap, cid). Level with six other laps (a tie, §32)."""
    s = _session()
    qp = next(q for q in s.quickest_passes().passes if q.cid == 3)
    assert qp.level_laps, "the demo's C3 was a tie when this test was written"
    return qp.lap_id, 3


def _spec(lap, cid, style=broadcast.STYLE_CLASSIC, elements=()):
    cfg = broadcast.styled(ev.OverlayConfig(out_height=720, elements=elements), style)
    return ev.build_pass_spec(_session(), os.path.join(_DEMO["dir"].name, "p.mp4"), lap, cid,
                              config=cfg)


# ------------------------------------------------------------------ the window
def test_the_pass_window_is_the_clip_the_quickest_row_plays():
    s = _session()
    for qp in s.quickest_passes().passes:
        for lap in qp.play_order():
            clip = s.corners.pass_clip(lap, qp.cid)
            win = ev.pass_window_for_export(s, lap, qp.cid)
            assert clip is not None and win is not None, (lap, qp.cid)
            t0, t_in, t_out, t1 = win
            assert (t0, t1) == (ev._media_time(s, clip[0]), ev._media_time(s, clip[1])), (win, clip)
            assert t_in == ev._media_time(s, s.corners.corner_edge_media_time(lap, qp.cid, "enter"))
            assert t_out == ev._media_time(s, s.corners.corner_edge_media_time(lap, qp.cid, "exit"))
            assert t0 < t_in < t_out < t1 and abs((t1 - t_out) - 1.0) < 0.01, win
    lap, cid = _c3()
    spec = _spec(lap, cid)
    t0, t_in, t_out, t1 = ev.pass_window_for_export(s, lap, cid)
    assert (spec.t0, spec.t1, spec.lap_id) == (t0, t1, lap)
    assert abs(spec.lap_t0 - t_in) < 1e-9 and abs(spec.lap_t1 - t_out) < 1e-9
    # no finish frame: the clip is exactly the played window's frames
    assert ev.with_finish_frame(spec, FPS) == spec and not spec.ends_on_finish
    assert ev.scope_lap_ids(s, ev.SCOPE_PASS, lap) == [lap]
    (built,) = ev.build_scope_specs(s, spec.out_path, ev.SCOPE_PASS, lap_id=lap, cid=cid, lead=5.0)
    assert (built.t0, built.t1, built.pass_subject) == (spec.t0, spec.t1, spec.pass_subject)
    spec.source.cleanup()
    built.source.cleanup()


def test_the_file_names_the_lap_and_the_corner():
    cfg = ev.OverlayConfig()
    assert ev.overlay_output_name("GX019000", 12, cfg, "C3") == "GX019000_lap13_C3_overlay.mp4"
    alpha = ev.OverlayConfig(overlay_only=True, alpha_codec=ev.ALPHA_PRORES)
    assert ev.overlay_output_name("GX019000", 12, alpha, "C3") == \
        "GX019000_lap13_C3_overlay_alpha.mov"
    png = ev.OverlayConfig(overlay_only=True, alpha_codec=ev.ALPHA_PNG)
    assert ev.overlay_output_name("GX019000", 12, png, "C3") == "GX019000_lap13_C3_overlay_png"
    # a lap export's name is unchanged
    assert ev.overlay_output_name("GX019000", 12, cfg) == "GX019000_lap13_overlay.mp4"


# ------------------------------------------------------------------ what the frame says
def _marks(spec):
    """Frame times before the entry, inside the corner and from the exit frame on."""
    times = ev.frame_times(spec.t0, spec.t1, FPS)
    i_out = int(np.searchsorted(times, spec.lap_t1 - ev.ON_LINE_S))
    return {"before": times[2], "in": (spec.lap_t0 + spec.lap_t1) / 2,
            "last_in": times[i_out - 1], "exit": times[i_out], "after": times[-1]}


def test_the_strip_names_the_corner_and_its_time_from_the_exit_frame_only():
    s = _session()
    lap, cid = _c3()
    spec = _spec(lap, cid)
    st = next(x for x in s.corners.lap_corner_stats(lap) if x.cid == cid)
    text = f"{st.time:.2f} s"
    m = _marks(spec)
    runs = {k: ev._frame_strip_runs(s, ev.overlay_values_at(s, t, spec), spec, None)
            for k, t in m.items()}
    head = f"LAP {lap + 1}   C3"
    assert runs["before"] == (head, "", "", 0.0), runs["before"]
    assert runs["in"][0] == head and 0.0 < runs["in"][3] < 1.0, runs["in"]
    assert runs["last_in"][0] == head, runs["last_in"]
    assert runs["exit"] == (f"{head} · {text}", "", "", 1.0), runs["exit"]
    assert runs["after"][0] == f"{head} · {text}"
    for t in ev.frame_times(spec.t0, spec.t1, FPS):
        vals = ev.overlay_values_at(s, float(t), spec)
        label, tail, _c, _f = ev._frame_strip_runs(s, vals, spec, None)
        # no lap clock, no lap Δ, no ★ BEST (the demo's lap 13 is its best lap), no "fastest"
        assert ":" not in label and "Δ" not in label + tail and "★" not in label + tail, label
        assert "fastest" not in label.lower() and vals.delta_s is None
    # Broadcast's tower reads the same runs: "LAP 13" over "C3 · 2.32 s", no Δ row, no Δ bar
    assert broadcast.tower_texts(runs["exit"][0], "", "Δbest") == (f"LAP {lap + 1}",
                                                                     f"C3 · {text}", "")
    spec_b = _spec(lap, cid, broadcast.STYLE_BROADCAST)
    painter = ev.OverlayPainter(s, spec_b, 1280, 720, FPS)
    tower = next(e for e in painter.elements if e.name == broadcast.BroadcastElement.name)
    st_exit = tower.state(ev.overlay_values_at(s, m["exit"], spec_b))
    assert st_exit[:3] == (f"LAP {lap + 1}", f"C3 · {text}", "") and st_exit[5] is None, st_exit
    spec.source.cleanup()
    spec_b.source.cleanup()


def test_corner_cards_keep_to_the_pass_and_the_ghost_sits_it_out():
    s = _session()
    lap, cid = 3, 4      # not the best lap: its card carries a Δ to the best lap's same corner
    spec = _spec(lap, cid, elements=(ghost.GhostElement.name, corner_cards.CornerCardsElement.name))
    painter = ev.OverlayPainter(s, spec, 1280, 720, FPS)
    names = [e.name for e in painter.elements]
    assert ghost.GhostElement.name not in names, names
    cards = next(e for e in painter.elements if e.name == corner_cards.CornerCardsElement.name)
    assert [c.cid for c in cards.cards] == [cid], cards.cards
    # the previous corner's card would have opened the clip: its exit is at or before frame 0
    every = corner_cards.card_rows(s, lap, None, None)
    prev = [c for c in every if c.cid == cid - 1]
    assert prev and ev._media_time(s, prev[0].t_exit) <= spec.t0 + 1e-6
    spec.source.cleanup()


def _strip_ink(img: QImage, rect) -> int:
    """White glyph pixels inside the strip pill."""
    a = np.frombuffer(bytes(img.constBits()), np.uint8).reshape(img.height(), img.width(), 3)
    x, y, w, h = (int(round(v)) for v in (rect.x(), rect.y(), rect.width(), rect.height()))
    return int((a[y:y + h, x:x + w].min(axis=2) > 200).sum())


def test_the_painted_strip_gains_the_corner_time_on_the_exit_frame():
    s = _session()
    lap, cid = _c3()
    spec = _spec(lap, cid)
    painter = ev.OverlayPainter(s, spec, 1280, 720, FPS)
    ink = {}
    for k, t in _marks(spec).items():
        vals = ev.overlay_values_at(s, t, spec)
        img = QImage(1280, 720, QImage.Format_RGB888)
        img.fill(QColor(96, 110, 90))
        painter.paint_frame_with_state(img, vals, painter.advance_and_snapshot(vals))
        ink[k] = _strip_ink(img, painter._strip_rect)
    # (the corner's fill moves under the label: a pixel or two of its edge may cross the threshold)
    assert abs(ink["before"] - ink["last_in"]) <= 4 and ink["exit"] > 1.5 * ink["last_in"], ink
    spec.source.cleanup()


def test_lap_and_session_exports_keep_the_lap_strip():
    s = _session()
    best = s.best_lap_id()
    other = next(i for i in s.valid_lap_ids() if i != best)
    for spec in (ev.build_lap_spec(s, "x.mp4", other, lead_in=5.0, lead_out=5.0),
                 ev.build_lap_spec(s, "x.mp4", best), ev.build_session_spec(s, "x.mp4")):
        assert spec.pass_subject is None
        times = ev.frame_times(spec.t0, spec.t1, FPS)
        for t in times[:: max(1, len(times) // 40)]:
            vals = ev.overlay_values_at(s, float(t), spec)
            assert ev._frame_strip_runs(s, vals, spec, None) == ev._strip_runs(
                s, vals, spec.lap_t0, spec.is_best_at(vals.lap_id), None)
        spec.source.cleanup()


# ------------------------------------------------------------------ the dialog, ⌘K
def _settle(seconds=0.1):
    end = time.time() + seconds
    while time.time() < end:
        _APP.processEvents()
        time.sleep(0.005)


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
        win.resize(1440, 900)
        win.show()
        _settle(0.2)
        QTest.keyClick(win, Qt.Key_Escape)   # leave the debrief landing
        _settle(0.1)
        win._share_card_blocked = lambda: False
        win.view.video.play_clip = lambda start, end: None
        _DEMO["win"] = win
    return _DEMO["win"]


def _play_c3(win):
    page = win.view.corners_page
    page.set_view("quickest", remember=False)
    page.quickest.play_named(page.quickest.row_of(3))
    _settle(0.05)
    return page.quickest.playing()


def _rows(dlg):
    form = dlg.findChild(QFormLayout)
    rows = {}
    for r in range(form.rowCount()):
        label = form.itemAt(r, QFormLayout.ItemRole.LabelRole)
        field = form.itemAt(r, QFormLayout.ItemRole.FieldRole)
        if label is not None and field is not None:
            rows[label.widget().text()] = field.widget()
    return rows


def _export(win, call, act):
    """Run an export entry with the options dialog answered by `act(dlg, rows)`; the save panel
    answers a path under the demo's temp dir, and the render is captured, not run."""
    seen = {"specs": None, "save": None}
    orig_exec = QDialog.exec
    ctl = win.exports
    orig_save, orig_run = ctl._export_save_path, ctl._run_video_export

    def save(_title, suffix, _filt):
        seen["save"] = suffix
        return os.path.join(_DEMO["dir"].name, "out" + suffix)

    def run(specs, **_kw):
        seen["specs"] = specs
        for spec in specs:
            spec.source.cleanup()
    QDialog.exec = lambda dlg: act(dlg, _rows(dlg))
    ctl._export_save_path, ctl._run_video_export = save, run
    try:
        call()
    finally:
        QDialog.exec = orig_exec
        ctl._export_save_path, ctl._run_video_export = orig_save, orig_run
    return seen


def test_the_pass_command_opens_the_dialog_on_this_pass_and_exports_it():
    win = _window()
    lap, _cid = _c3()
    prefs.set(win.exports._PREF_EXPORT_SCOPE, 1)          # the owner's last scope: Best lap
    assert _play_c3(win) == (3, lap)
    opened = {}

    def act(dlg, rows):
        scope = rows["Export"]
        opened["scope"] = scope.currentText()
        opened["items"] = [scope.itemText(i) for i in range(scope.count())]
        opened["lead"] = rows["Run-up / run-off"].isEnabled()
        box = next(b for b in rows["Extras"].findChildren(QCheckBox)
                   if b.text() == win.exports._GHOST_LABEL)
        opened["ghost"] = (box.isEnabled(), box.toolTip())
        opened["hint"] = " ".join(lab.text() for lab in dlg.findChildren(QLabel)
                                  if lab.property("role") == "Hint")
        return QDialog.Accepted
    seen = _export(win, lambda: win.exports.export_pass_video(lap, 3, "C3"), act)
    assert opened["scope"] == f"This pass — C3, lap {lap + 1}", opened
    assert opened["items"][-1] == opened["scope"] and len(opened["items"]) == 5
    assert opened["lead"] is False and opened["ghost"][0] is False
    assert opened["ghost"][1] == f"Off here: {ghost.PASS_REASON}", opened["ghost"]
    assert f"lap {lap + 1}'s pass through C3" in opened["hint"], opened["hint"]
    assert seen["save"] == f"_lap{lap + 1}_C3_overlay.mp4", seen["save"]
    (spec,) = seen["specs"]
    assert spec.pass_subject is not None and spec.pass_subject.cid == 3 and spec.lap_id == lap
    assert (spec.t0, spec.t1) == ev.pass_window_for_export(win.session, lap, 3)[::3]
    # a pass is the subject of one export: the remembered scope is still Best lap
    assert prefs.get(win.exports._PREF_EXPORT_SCOPE, None) == 1


def test_file_export_offers_the_named_pass_and_the_palette_lists_it():
    win = _window()
    lap, _cid = _c3()
    prefs.set(win.exports._PREF_EXPORT_SCOPE, 0)
    _play_c3(win)
    titles = [t for t, _call in win.quickest_pass_commands()]
    assert f"Export the C3 pass — lap {lap + 1}…" in titles, titles
    opened = {}

    def act(dlg, rows):
        scope: QComboBox = rows["Export"]
        opened["scope"] = scope.currentText()
        opened["last"] = scope.itemText(scope.count() - 1)
        return QDialog.Rejected
    _export(win, win.exports.export_overlay_video, act)
    assert opened == {"scope": "This lap", "last": f"This pass — C3, lap {lap + 1}"}, opened
    # the command runs the pass's export
    called = []
    orig = win.exports.export_pass_video
    win.exports.export_pass_video = lambda *a: called.append(a)
    try:
        dict(win.quickest_pass_commands())[f"Export the C3 pass — lap {lap + 1}…"]()
    finally:
        win.exports.export_pass_video = orig
    assert called == [(lap, 3, "C3")], called
    # another lap picked: the view forgets the pass, and so do the dialog and the palette
    win.view.corners_page.quickest.forget_cycle()
    assert win.exports.pass_subject() is None
    assert not any(t.startswith("Export the") for t, _c in win.quickest_pass_commands())
    _export(win, win.exports.export_overlay_video, act)
    assert "pass" not in opened["last"], opened


# ------------------------------------------------------------------ the chapter seam, real render
SW, SH = 640, 360
CYCLE = 12          # the picture codes its global source frame modulo this, as a flat grey level


def _level(k):
    # 12 levels apart: the gray -> yuv420p -> rgb24 round trip moves a flat level by up to ~3
    return 24 + 12 * (k % CYCLE)


def _coded_picture(path, truth, first_payload, n_payloads, ffmpeg):
    """`synth_gopro.generate(video=)`: one chapter whose every frame is flat grey at
    `_level(global frame)`, 29.97 fps, so a decoded frame says which source frame it is."""
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


def test_a_pass_that_straddles_the_chapter_seam_stays_in_sync():
    if not ev.ffmpeg_available():
        assert os.path.join(".pixi", "envs") not in os.environ.get("CONDA_PREFIX", ""), \
            "ffmpeg is a locked dependency inside the pixi env"
        return
    from studio.dev import synth_gopro as sg
    from studio.session import Session
    tmp = tempfile.TemporaryDirectory(prefix="pass_export_seam_")
    rec = sg.generate(os.path.join(tmp.name, "rec"), laps=3, chapters=2, video=_coded_picture,
                      audio=True)
    s = Session.load(chapters.discover_siblings(rec.paths[0]))
    seam = s.chapters.chapters[1].offset
    found = [(lap, c.cid) for lap in s.valid_lap_ids() for c in s.corners.corner_list()
             if (w := ev.pass_window_for_export(s, lap, c.cid)) and w[0] < seam < w[3]]
    assert found, "no pass of the synthetic recording straddles its chapter seam"
    lap, cid = found[0]
    out = os.path.join(tmp.name, "pass.mp4")
    cfg = ev.OverlayConfig(out_height=SH, encoder="libx264", hwaccel_decode=False)
    spec = ev.build_pass_spec(s, out, lap, cid, config=cfg)
    assert spec.source.concat_list_path, "the pass's source is not the two-chapter span"
    try:
        res = ev.Renderer(s, spec).run()
    finally:
        spec.source.cleanup()
    times = ev.frame_times(spec.t0, spec.t1, 30000 / 1001)
    probe = json.loads(subprocess.run(
        [ev.FFPROBE, "-v", "error", "-show_entries",
         "stream=codec_type,start_time,duration,nb_frames", "-of", "json", res.out_path],
        capture_output=True, text=True, check=True).stdout)["streams"]
    video = next(x for x in probe if x["codec_type"] == "video")
    audio = next(x for x in probe if x["codec_type"] == "audio")
    assert int(video["nb_frames"]) == len(times), (video, len(times))
    frame = 1001 / 30000
    assert abs(float(video["duration"]) - len(times) * frame) < frame, video
    assert abs(float(audio["start_time"]) - float(video["start_time"])) < frame / 2, probe
    assert abs(float(audio["duration"]) - float(video["duration"])) <= frame, probe
    raw = subprocess.run([ev.FFMPEG, "-nostdin", "-v", "error", "-i", res.out_path, "-f",
                          "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                         capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, SH, SW, 3)
    assert len(frames) == len(times)
    # THE PICTURE: each frame is the source frame at its media time — the one showing at it
    # (floor), or for the very first frame the first one at or after t0, the accurate seek's snap
    # that every lap export makes too (measured 2026-11-19 on this fixture: a lap whose window
    # holds the seam reads the same, 0 on every frame but its first, +1) — and on both sides of
    # the seam the picture advances exactly one source frame per frame: none dropped, none held.
    mid = frames[:, SH // 2 - 20:SH // 2 + 20, SW // 2 - 20:SW // 2 + 20, 1].mean(axis=(1, 2))
    got = np.round((mid - 24) / 12).astype(int) % CYCLE
    shown = np.floor(times * 30000 / 1001 + 1e-6).astype(int)
    off = (got - shown) % CYCLE
    off = np.where(off > CYCLE // 2, off - CYCLE, off)
    i_seam = int(np.searchsorted(times, seam))
    assert 0 < i_seam < len(times) - 1
    assert off[0] in (0, 1) and not np.any(off[1:]), (
        f"frames showing another source frame (seam at frame {i_seam}): "
        f"{np.flatnonzero(off[1:]) + 1} by {off[np.flatnonzero(off)][:6]}")
    steps = np.diff(got[1:]) % CYCLE
    assert np.all(steps == 1), f"a source frame dropped or held at {np.flatnonzero(steps != 1) + 1}"
    # THE OVERLAY: the corner time lands on exactly the planned exit frame
    painter = ev.OverlayPainter(s, spec, SW, SH, 30000 / 1001)
    r = painter._strip_rect
    x, y, w, h = (int(round(v)) for v in (r.x(), r.y(), r.width(), r.height()))
    ink = (frames[:, y:y + h, x:x + w].min(axis=3) > 200).sum(axis=(1, 2))
    i_exit = int(np.searchsorted(times, spec.lap_t1 - ev.ON_LINE_S))
    jump = int(np.argmax(np.diff(ink))) + 1
    assert jump == i_exit, (jump, i_exit, ink[i_exit - 3:i_exit + 3].tolist())
    tmp.cleanup()


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
