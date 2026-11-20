"""The two export pickers as QA round 3 (2026-09-26) met them on the owner's own prefs.

WHY IT EXISTS. His overlay exports remember "Source", and both pickers got it wrong from there:
  * JOURNEY-3: the comparison picker read the OVERLAY's resolution pref, so his PB compare opened on
    "Source": two 4K panes stacked, 3840x4320, H.264 level 6.0. VideoToolbox's hardware encoder
    refuses that frame and its software fallback took 3:41 and wrote 299 MB against the 1:41 and
    205 MB the picker quoted. The compare now keeps its own pref, opens on 1080p panes, and its
    "Source" row is capped at one 4K frame, which the hint says.
  * REG-4 (EXP-11): the first dialog of a session quoted no size and no time at "Source". The
    footage probe behind it answered after 13.7 s on a loaded Mac, and the dialog had stopped
    waiting at 10 s. It now waits while it is open, and says it is measuring meanwhile.
  * APP-POLISH-3 (2026-09-29): the single-lap "Source" is held to the same 4K frame, and its
    Output line is the renderer's own frame, cap named (watched failing on main @ 63d1925).
  * FOLLOW-TRUTH4 (the W1 close-out's REG-4): a lap cut on the line quoted "2025 frames to render"
    for a 2026-frame file (watched failing on main @ 95694b9).
Each test was watched failing on main @ d7a741d.

The dialogs are the real ones, answered by patching `exec`; the footage probe is stood in for (a
4K frame for a temp file), gated where the test needs it to land late.

Run: python tests/test_export_pickers.py
"""
import os
import re
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PACER_NO_MEDIA"] = "1"

# test_export_gates diverts every store into a temp tree on import, before any window exists.
from PySide6.QtWidgets import QComboBox, QDialog, QLabel  # noqa: E402
from test_export_gates import (  # noqa: E402
    _APP,
    _E5_FRAME,
    FakeSession,
    _clear_export_preset,
    _combo,
    _run_options_dialog,
    _window,
)

from studio import export_compare, export_controller, prefs  # noqa: E402
from studio import export_video as ev  # noqa: E402
from studio.export_controller import ExportController  # noqa: E402

_RES = "Resolution (each pane)"
# The comparison's own resolution pref, by the name it lands under in prefs.json: a key on his disk
# is a contract, so the tests pin the literal rather than whatever constant the app names it by.
_COMPARE_RES_KEY = "export_compare_res_idx"


def _hint(dlg) -> QLabel:
    return next(lb for lb in dlg.findChildren(QLabel) if lb.property("role") == "Hint")


def _pump_until(pred, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def _reset_prefs(**stored):
    """Start from the app's defaults plus `stored` ({pref key: row}): the owner's overlay pref is
    a REAL stored preference in this file's temp tree, shared by every test in the process."""
    _clear_export_preset()
    data = prefs.load()
    for key in (_COMPARE_RES_KEY, ExportController._PREF_COMPARE_LAYOUT):
        data.pop(key, None)
    data.update(stored)
    prefs.save(data)


def _compare_dialog(win, on_dialog):
    """The real `_ask_compare_options` for a pinned pair (laps 1 and 3 of the fake session), its
    `exec` answered by `on_dialog`."""
    saved = (ExportController.compare_pair, QDialog.exec)
    ExportController.compare_pair = lambda _self: (0, 2, None, False)
    QDialog.exec = on_dialog
    try:
        return win.exports._ask_compare_options()
    finally:
        ExportController.compare_pair, QDialog.exec = saved


def test_the_compare_opens_on_its_own_1080p_whatever_the_overlay_remembers():
    """His overlay pref is "Source" (row 3). The comparison picker used to open there too, and to
    write its own choice back over it. It now opens on 1080p panes (1920x2160) and remembers its
    row under its own key, and neither picker moves the other's."""
    _reset_prefs(**{ExportController._PREF_EXPORT_RES: ExportController._EXPORT_RES_SOURCE})
    win = _window(FakeSession(laps=(0, 1, 2)))
    seen = []

    def pick_720p(dlg):
        res = _combo(dlg, _RES)
        seen.append((res.currentText(), _hint(dlg).text()))
        res.setCurrentIndex(0)
        return QDialog.Accepted

    cfg = _compare_dialog(win, pick_720p)
    opened, hint = seen[0]
    assert opened == "1080p", f"the comparison opened on {opened!r}, the overlay's remembered row"
    assert "Output: 1920x2160, two panes of 1920x1080 one above the other." in hint, hint
    assert cfg.out_height == 720, cfg
    assert prefs.get(_COMPARE_RES_KEY) == 0
    assert prefs.get(ExportController._PREF_EXPORT_RES) == ExportController._EXPORT_RES_SOURCE, (
        "the comparison's choice was written over the overlay's")
    _compare_dialog(win, lambda dlg: (seen.append((_combo(dlg, _RES).currentText(), "")),
                                      QDialog.Rejected)[1])
    assert seen[-1][0] == "720p", f"the comparison forgot its own choice: {seen[-1][0]!r}"
    win.hide()
    print("ok JOURNEY-3: the comparison opens on its own 1080p and keeps its own choice")


def test_the_compare_source_row_is_capped_at_one_4k_frame_and_says_so():
    """On his 4K footage "Source" was 3840x4320. The row is now the largest pair of panes that fits
    one 4K frame, the hint names that frame and why, and the size is priced on it, finish frame
    included. 1440p panes (2560x2880) already fit, so that row is untouched and says nothing."""
    _reset_prefs(**{_COMPARE_RES_KEY: 3})
    real_probe = ev.probe_video_size
    ev.probe_video_size = lambda _p: _E5_FRAME
    try:
        with tempfile.TemporaryDirectory(prefix="pickers-src-") as td:
            src = os.path.join(td, "GX010099.MP4")
            with open(src, "wb") as fh:
                fh.write(b"not really a video")
            ev.remember_video_size(src)                   # the frame is known: no wait here
            win = _window(FakeSession(laps=(0, 1, 2)), paths=(src,))
            texts = []

            def read(dlg):
                res = _combo(dlg, _RES)
                texts.append((res.currentText(), _hint(dlg).text()))
                res.setCurrentIndex(2)
                texts.append((res.currentText(), _hint(dlg).text()))
                return QDialog.Rejected
            _compare_dialog(win, read)
            win.hide()
    finally:
        ev.probe_video_size = real_probe
    (row, source), (_row, p1440) = texts
    assert row == "Source — up to a 4K frame", row
    assert ("Output: 2714x3052, two panes of 2714x1526 one above the other — capped at one 4K "
            "frame") in source, source
    codec = ev.resolve_encoder("auto")
    frames = ev.frame_count(0.0, 23.231, 30.0) + 1                     # + the finish frame
    size = ev.fmt_bytes(ev.estimate_output_bytes(2714, 3052, 30.0, frames / 30.0, "high", codec))
    assert f"About {size} — {frames} frames to render at 30 fps with {codec}" in source, source
    assert "Output: 2560x2880, two panes of 2560x1440 one above the other." in p1440, p1440
    assert "capped" not in p1440, p1440
    assert export_compare.MAX_FRAME_PIXELS == 3840 * 2160
    print("ok JOURNEY-3: the comparison's Source is one 4K frame, and says so")


def test_the_single_lap_source_row_names_the_4k_cap_exactly_when_it_applies():
    """APP-POLISH-3. Off 5.3K footage the Output line said "5312x2988, the footage's own
    resolution", straight from the probe; off his 4K fitted whole into 9:16 it said "source
    resolution" and priced a 9:16 CROP, while the render wrote 3840x6828. The line is now the
    renderer's own frame (`frame_geometry`, the Crop/Fit row included), names the one-4K-frame cap
    exactly when it applies, and the size is priced on that frame. Overlay-only is never capped.
    The encoder is pinned both ways: the size depends on it."""
    frames = {"GX010053.MP4": (5312, 2988, 60000 / 1001), "GX010099.MP4": _E5_FRAME}
    real = (ev.probe_video_size, ev.resolve_encoder)
    ev.probe_video_size = lambda p: frames[os.path.basename(p)]
    seen, clip = {}, {}
    _reset_prefs(**{ExportController._PREF_EXPORT_RES: ExportController._EXPORT_RES_SOURCE})
    try:
        with tempfile.TemporaryDirectory(prefix="pickers-cap-") as td:
            for name in frames:
                src = os.path.join(td, name)
                with open(src, "wb") as fh:
                    fh.write(b"not really a video")
                ev.remember_video_size(src)             # the frame is known: no wait here
                win = _window(FakeSession(laps=(0, 1, 2)), paths=(src,))
                clip["s"] = win.exports._export_clip_seconds(0, 0.0)
                for codec in (ev.VT_H264, ev.SW_H264):
                    ev.resolve_encoder = lambda _choice="auto", c=codec: c

                    def read(dlg, key=(name, codec)):
                        seen[key] = {"source": _hint(dlg).text()}
                        _combo(dlg, "Shape").setCurrentIndex(1)                 # 9:16
                        _combo(dlg, "Source frame").setCurrentIndex(1)          # fit, with bars
                        seen[key]["fit"] = _hint(dlg).text()
                        _combo(dlg, "Contents").setCurrentIndex(1)              # ProRes 4444
                        seen[key]["prores"] = _hint(dlg).text()
                        return QDialog.Rejected
                    _run_options_dialog(win, read)
                win.hide()
    finally:
        ev.probe_video_size, ev.resolve_encoder = real
        _clear_export_preset()

    def about(text):
        return text.split("About ")[1].split(" —")[0]
    for codec in (ev.VT_H264, ev.SW_H264):
        k53, k4k = seen[("GX010053.MP4", codec)], seen[("GX010099.MP4", codec)]
        cap = " — capped at one 4K frame, the biggest H.264 picture phones and messaging apps play."
        assert f"Output: 3840x2160, source aspect{cap}" in k53["source"], k53["source"]
        assert about(k53["source"]) == about(k4k["source"]), "5.3K was not priced on its 4K frame"
        assert "Output: 3840x2160, the footage's own resolution." in k4k["source"], k4k["source"]
        for text in (k4k["source"], k4k["prores"], k53["prores"]):
            assert "capped" not in text, text
        size = ev.fmt_bytes(ev.estimate_output_bytes(2160, 3840, 30.0, clip["s"], "high", codec))
        for k in (k53, k4k):
            assert f"Output: 2160x3840, the whole picture, with bars{cap}" in k["fit"], k["fit"]
            assert f"About {size} — " in k["fit"], (size, k["fit"])
        assert "Output: 3840x6828, the whole picture, with bars." in k4k["prores"], k4k["prores"]
    print("ok APP-POLISH-3: the single-lap Output line names the 4K cap exactly when it applies")


def test_the_lap_picker_quotes_the_frames_the_render_writes():
    """REG-4 (W1 close-out QA): at 1080p, cut on the line, the picker said "2025 frames to render"
    and the file held 2026: the finish frame `with_finish_frame` adds, which the compare hint
    counted and this one did not. Every Run-up / run-off row of the real dialog is read against the
    frames the export plans for the spec the same choices build (`_spec_plan`, the Renderer's own
    `with_finish_frame` + `frame_count`; test_export_ends_on_finish holds the Renderer to it). The
    count does not depend on the encoder."""
    real = (ev.probe_video_size, ev.probe_source_duration, ev.resolve_encoder)
    ev.probe_video_size = lambda _p: (1920, 1080, 30.0)
    ev.probe_source_duration = lambda _s: 1.0e9
    ev.resolve_encoder = lambda _choice="auto": ev.SW_H264
    _reset_prefs()
    quoted, rendered = {}, {}
    try:
        with tempfile.TemporaryDirectory(prefix="pickers-frames-") as td:
            src = os.path.join(td, "GX010064.MP4")
            with open(src, "wb") as fh:
                fh.write(b"not really a video")
            ev.remember_video_size(src)                 # the frame is known: no wait here
            win = _window(FakeSession(laps=(0, 1, 2)), paths=(src,))

            def read(dlg):
                _combo(dlg, "Resolution").setCurrentIndex(1)                        # 1080p
                for row, (_label, lead) in enumerate(ExportController._EXPORT_LEAD_OPTIONS):
                    _combo(dlg, "Run-up / run-off").setCurrentIndex(row)
                    said = re.search(r"(\d+) frames to render at ([\d.]+) fps", _hint(dlg).text())
                    quoted[lead] = (int(said[1]), float(said[2])) if said else _hint(dlg).text()
                return QDialog.Rejected
            _run_options_dialog(win, read)
            cfg = ev.OverlayConfig(out_height=1080, encoder="libx264", hwaccel_decode=False)
            for lead in quoted:
                spec = ev.build_lap_spec(win.session, os.path.join(td, "lap.mp4"), 0, config=cfg,
                                         src_path=src, lead_in=lead, lead_out=lead)
                plan = ev._spec_plan(spec, ev.probe_video_size)
                rendered[lead] = (plan.frames, spec.output_frame(ev.probe_video_size)[2])
            win.hide()
    finally:
        ev.probe_video_size, ev.probe_source_duration, ev.resolve_encoder = real
        _clear_export_preset()
    assert quoted == rendered, f"the picker quoted {quoted}, the render plans {rendered}"
    assert rendered[0.0][0] == ev.frame_count(0.0, 23.231, 30.0) + 1 == 698, rendered
    print(f"ok REG-4: the lap picker quotes the frames the render writes ({quoted})")


def _ink_top(label: QLabel) -> int:
    """The y of the label's first row of painted text, read off its own pixels (the commonest
    colour is its background): where the user SEES the text start, alignment and margins and all."""
    img = label.grab().toImage()
    counts: dict[int, int] = {}
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            counts[img.pixel(x, y)] = counts.get(img.pixel(x, y), 0) + 1
    bg = max(counts, key=counts.__getitem__)
    return next((y for y in range(img.height())
                 if any(img.pixel(x, y) != bg for x in range(img.width()))), -1)


def _late_probe(open_dialog, stored: dict, landed=("3840x", "2714x"),
                geometry: list | None = None, fails: bool = False) -> list[str]:
    """Open a picker at "Source" on a recording whose footage probe answers only when released,
    with the dialog's clock already past the 10 s it used to wait. Returns the hint as it read
    when the dialog opened, after that clock jump, and once the probe landed (its Output line
    names one of `landed`; with `fails`, the probe raises instead, as on a file ffprobe cannot
    read, and "landed" is the hint no longer measuring). With `geometry`, the dialog is SHOWN and
    each of the three moments appends (the hint's height, the y of every combo, the dialog's
    size, the y of the hint's first row of ink) to it."""
    _reset_prefs(**stored)
    gate = threading.Event()
    real_probe, real_time = ev.probe_video_size, export_controller.time
    skew = {"s": 0.0}
    export_controller.time = SimpleNamespace(
        monotonic=lambda: time.monotonic() + skew["s"], time=time.time, sleep=time.sleep)

    def probe(_p):
        gate.wait(30.0)
        if fails:
            raise RuntimeError("ffprobe could not read video stream size")
        return _E5_FRAME
    ev.probe_video_size = probe
    texts = []
    try:
        with tempfile.TemporaryDirectory(prefix="pickers-late-") as td:
            src = os.path.join(td, "GX010099.MP4")
            with open(src, "wb") as fh:
                fh.write(b"not really a video")
            win = _window(FakeSession(laps=(0, 1, 2)), paths=(src,))

            def on_dialog(dlg):
                hint = _hint(dlg)

                def note():
                    texts.append(hint.text())
                    if geometry is not None:
                        _pump_until(lambda: False, 0.1)
                        geometry.append((hint.height(), [c.mapTo(dlg, c.rect().topLeft()).y()
                                                         for c in dlg.findChildren(QComboBox)],
                                         dlg.size().toTuple(),
                                         hint.mapTo(dlg, hint.rect().topLeft()).y()
                                         + _ink_top(hint)))
                if geometry is not None:
                    dlg.show()
                note()
                skew["s"] = 11.0             # REG-4's probe answered 13.7 s after the dialog opened
                _pump_until(lambda: False, 0.4)
                note()
                gate.set()
                _pump_until((lambda: ev.export_probes_ready(src)) if fails
                            else (lambda: any(x in hint.text() for x in landed)), 20.0)
                _pump_until(lambda: False, 0.2)      # the poll's redraw, 50 ms after the answer
                note()
                dlg.hide()
                return QDialog.Rejected
            open_dialog(win, on_dialog)
            win.hide()
    finally:
        gate.set()
        ev.probe_video_size, export_controller.time = real_probe, real_time
    return texts


def test_the_first_dialog_states_the_size_once_a_slow_probe_lands():
    """REG-4: with his prefs, the first overlay dialog of a session at "Source" showed no size and
    no time, and the second one did: the probe landed after the dialog had stopped waiting. The
    hint now says it is measuring, and states the size and the time when the probe lands, however
    late."""
    texts = _late_probe(lambda win, on: _run_options_dialog(win, on),
                        {ExportController._PREF_EXPORT_RES: ExportController._EXPORT_RES_SOURCE})
    opened, waited, landed = texts
    assert "Output: 3840x2160, the footage's own resolution." in landed, (
        f"the probe landed and the hint never said so: {landed!r}")
    assert "About " in landed and "to render" in landed, landed
    assert "Measuring the footage" not in landed, landed
    for text in (opened, waited):
        assert "Measuring the footage for the size and the time to render" in text, text
        assert "About " not in text, text
    print("ok REG-4: the first overlay dialog states the size once the probe lands")


def test_the_compare_dialog_waits_for_a_slow_probe_too():
    """The comparison picker shares the wait, and its "Source" row has nothing to price until the
    frame is known: it says it is measuring, then states the capped frame and its size."""
    texts = _late_probe(lambda win, on: _compare_dialog(win, on),
                        {_COMPARE_RES_KEY: 3})
    opened, _waited, landed = texts
    assert "Measuring the footage" in opened and "About " not in opened, opened
    assert "Output: 2714x3052" in landed and "About " in landed and "to render" in landed, landed
    print("ok REG-4: the comparison dialog states the size once the probe lands")


def test_a_fixed_resolution_quotes_no_guessed_size_before_the_footage_is_measured():
    """Discovery 6 (craft, P3): at a fixed row (1080p, the default) the overlay picker opened on a
    size priced from a 16:9 guess — on the demo "About 26 MB — 1350 frames … at 30 fps", stated
    as if final — and 0.4-3 s later silently became the measured "320x180 … About 9 MB … 29.97
    fps". Until the footage is measured the line now says it is measuring, at every row; the
    number arrives once. And the rows do not move when the hint is re-written."""
    geometry: list = []
    texts = _late_probe(lambda win, on: _run_options_dialog(win, on),
                        {ExportController._PREF_EXPORT_RES: 1}, landed=("1920x1080",),
                        geometry=geometry)
    opened, waited, landed = texts
    for text in (opened, waited):
        assert "Measuring the footage for the size and the time to render" in text, text
        assert "About " not in text and "frames to render" not in text, text
    assert "Output: 1920x1080" in landed and "About " in landed and "to render" in landed, landed
    assert "Measuring the footage" not in landed, landed
    (h0, ys0, d0, ink0), _mid, (h1, ys1, d1, ink1) = geometry
    assert ys1 == ys0, f"the rows moved when the measured hint landed: {geometry}"
    assert h1 >= h0, (h0, h1)
    assert ink1 == ink0, f"the hint's text moved when the measured line landed: {geometry}"

    print("ok craft P3: no guessed size before the footage is measured; the rows hold still")


# Where the reserve is what holds the rows (FakeSession, offscreen, measured 2026-11-19): at All
# laps in the Broadcast style at 1080p the measuring line wraps to 75 px and the estimate to 90 px;
# without the reserve the hint opened at 75 and grew when an estimate landed. The whole session has
# no size to quote on this session (no reserve), so it shows the alignment alone.
_ALL_LAPS_BROADCAST = {ExportController._PREF_EXPORT_SCOPE: 2, ExportController._PREF_EXPORT_STYLE: 1}
_SESSION = {ExportController._PREF_EXPORT_SCOPE: 3}


def _steady_while_measured(stored: dict, fails: bool = False):
    """The overlay picker at a fixed row (1080p) and `stored`, shown, its probe landing late (or
    failing): (texts, geometry) as `_late_probe` reads them."""
    geometry: list = []
    texts = _late_probe(lambda win, on: _run_options_dialog(win, on),
                        {ExportController._PREF_EXPORT_RES: 1, **stored},
                        landed=("1920x1080",), geometry=geometry, fails=fails)
    return texts, geometry


def _held_still(name: str, geometry: list) -> tuple[int, int]:
    (h0, ys0, _d0, ink0), _mid, (h1, ys1, _d1, ink1) = geometry
    assert ys1 == ys0, f"{name}: the rows moved when the hint was re-written: {geometry}"
    assert h1 == h0, f"{name}: the hint changed height when the number landed: {geometry}"
    assert ink1 == ink0, f"{name}: the hint's text moved when the number landed: {geometry}"
    return h0, ink0


def test_the_hint_holds_its_text_and_rows_still_when_the_number_lands():
    """Review of cool/stats-and-dialog-polish: the hint kept QLabel's vertical centring, so in the
    box it keeps while measuring "Measuring…" sat ~7 px down and jumped up when the number landed.
    At all laps and the whole session, from open to measured, the hint's first row of ink, its
    height and every combo stay where they were."""
    seen = {}
    for name, stored in (("all laps", _ALL_LAPS_BROADCAST), ("session", _SESSION)):
        texts, geometry = _steady_while_measured(stored)
        opened, _waited, landed = texts
        assert "Measuring the footage" in opened and "About " not in opened, (name, opened)
        assert "Output: 1920x1080" in landed and "Measuring the footage" not in landed, (
            name, landed)
        seen[name] = _held_still(name, geometry)
    print(f"ok craft P3: the hint's text and rows hold still at all laps and session ({seen})")


def test_a_failed_probe_drops_measuring_and_states_the_estimate_in_place():
    """A probe that FAILS (a file ffprobe cannot read) is an answer too: the hint stops saying it
    is measuring — a stale "Measuring…" would promise a number that never comes — and states the
    estimate, the one size there will be. The box kept for that estimate while measuring (the
    reserve) is what lets it land without the hint growing or the rows moving: at all laps the
    estimate wraps one line more than the measuring line."""
    seen = {}
    for name, stored in (("all laps", _ALL_LAPS_BROADCAST), ("session", _SESSION)):
        texts, geometry = _steady_while_measured(stored, fails=True)
        opened, _waited, landed = texts
        assert "Measuring the footage" in opened, (name, opened)
        assert "Measuring the footage" not in landed, (
            f"{name}: still measuring after the probe failed: {landed}")
        assert "Output: up to 1080p tall" in landed, (name, landed)
        assert ("About " in landed) == (name == "all laps"), (name, landed)
        seen[name] = _held_still(name, geometry)
    print(f"ok craft P3: a failed probe states the estimate in place ({seen})")


def test_a_probe_that_never_answers_gives_up_and_states_the_estimate():
    """`probe_video_size` had no timeout: an ffprobe that never answers left the hint "Measuring…"
    for as long as the dialog stayed open. The picker's ask (`remember_video_size`) now runs it
    under `HINT_PROBE_TIMEOUT_S`: the child is killed, the file is remembered as unreadable, and
    the hint states its estimate. A REAL hanging ffprobe stands in (a script that sleeps), the
    limit shortened for the test; the renderer's own probes keep no limit."""
    _reset_prefs(**{ExportController._PREF_EXPORT_RES: 1})
    real = (ev.FFPROBE, ev.HINT_PROBE_TIMEOUT_S)
    with tempfile.TemporaryDirectory(prefix="pickers-hang-") as td:
        hang = os.path.join(td, "ffprobe")
        with open(hang, "w") as fh:
            fh.write("#!/bin/sh\nexec sleep 30\n")
        os.chmod(hang, 0o755)
        src = os.path.join(td, "GX010098.MP4")
        with open(src, "wb") as fh:
            fh.write(b"not really a video")
        ev.FFPROBE, ev.HINT_PROBE_TIMEOUT_S = hang, 0.5
        texts = []
        try:
            win = _window(FakeSession(laps=(0, 1, 2)), paths=(src,))

            def on_dialog(dlg):
                hint = _hint(dlg)
                texts.append(hint.text())
                start = time.monotonic()
                _pump_until(lambda: "Measuring the footage" not in hint.text(), 15.0)
                texts.append((hint.text(), time.monotonic() - start))
                return QDialog.Rejected
            _run_options_dialog(win, on_dialog)
            win.hide()
            thread = ev._WARM_THREAD
            if thread is not None:
                thread.join(5.0)
        finally:
            ev.FFPROBE, ev.HINT_PROBE_TIMEOUT_S = real
        assert ev._PROBE_TIMEOUT.get() is None, "the picker's limit leaked out of its ask"
    opened, (landed, waited) = texts
    assert "Measuring the footage" in opened, opened
    assert "Measuring the footage" not in landed, f"still measuring after {waited:.1f} s: {landed}"
    assert "About " in landed and "Output: up to 1080p tall" in landed, landed
    assert waited < 10.0, waited
    print(f"ok craft P3: a hung probe is given up after its limit ({waited:.1f} s), the estimate shown")


def test_the_hint_never_gets_shorter_while_its_dialog_is_open():
    """`SteadyHint`: a wrapped hint that loses a line keeps its height at that width, so the form
    above it does not shift; a wider dialog gets its own floor."""
    hint = export_controller.SteadyHint("")
    long = "a few words that wrap " * 12
    hint.setText(long)
    tall = hint.heightForWidth(300)
    hint.setText("short")
    assert hint.heightForWidth(300) == tall, (hint.heightForWidth(300), tall)
    assert QLabel("short").heightForWidth(300) < tall
    assert hint.heightForWidth(2000) < tall           # its own floor at another width
    assert hint.property("role") == "Hint" and hint.wordWrap()
    print("ok craft P3: the hint keeps its height while the dialog is open")


def _run_all():
    tests = (test_the_compare_opens_on_its_own_1080p_whatever_the_overlay_remembers,
             test_the_compare_source_row_is_capped_at_one_4k_frame_and_says_so,
             test_the_single_lap_source_row_names_the_4k_cap_exactly_when_it_applies,
             test_the_lap_picker_quotes_the_frames_the_render_writes,
             test_the_first_dialog_states_the_size_once_a_slow_probe_lands,
             test_the_compare_dialog_waits_for_a_slow_probe_too,
             test_a_fixed_resolution_quotes_no_guessed_size_before_the_footage_is_measured,
             test_the_hint_never_gets_shorter_while_its_dialog_is_open,
             test_the_hint_holds_its_text_and_rows_still_when_the_number_lands,
             test_a_failed_probe_drops_measuring_and_states_the_estimate_in_place,
             test_a_probe_that_never_answers_gives_up_and_states_the_estimate)
    failed = []
    for test in tests:
        try:
            test()
        except Exception:  # noqa: BLE001 — run every test, then fail by name
            import traceback
            traceback.print_exc()
            failed.append(test.__name__)
            print(f"FAIL {test.__name__}")
    _clear_export_preset()
    if failed:
        print(f"{len(failed)}/{len(tests)} export-picker tests FAILED: {failed}")
        sys.exit(1)
    print("all export-picker tests passed")


if __name__ == "__main__":
    _run_all()
