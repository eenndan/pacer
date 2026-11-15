"""The Broadcast overlay style (`studio/overlay/broadcast.py`, `studio/overlay/layout.py`) and the
export dialog's Style row.

WHY A FILE OF ITS OWN. Broadcast is a second LOOK for the same figures, so what has to hold is a
set of promises across the two looks rather than one painter's pixels: Classic stays exactly what
it was; every Broadcast figure IS its Classic twin (the tower's clock and Δ the strip's, the big
speed the readout's, the baseline named as the app names it); the honesty stamp is kept, the same
lines at the same size, only moved; no element lands on another or leaves the safe area at any size
the export offers; and the Style row's extras do not leak from one style into the other.

THE FIXTURE is the demo's telemetry (`make_demo`'s seed, laps, origin and habit — a fictional
circuit, verified timing on its built-in track) under a flat 1280x720 picture with a tone as its
sound, so one recording serves the painter-level checks, the real dialog and a real 720p export.

WHAT IS CHECKED:
  * the layout, over EVERY frame the export dialog can produce (its source shapes x Resolution x
    Shape x Source-frame rows through its own `frame_geometry`, plus odd sizes) x a three-line
    honesty stamp off/on x ghost + cards off/on: the tower, the Δ bar, the big speed, the kept
    g-meter and minimap, the moved stamp and the corner card are pairwise clear and inside the
    safe area, and the ghost ring lands inside the (bigger) minimap; a layout with nowhere to go
    fails by name; the table's margin is the Classic margin;
  * truth, on every frame of a lap: the tower's clock and Δ are the Classic strip's strings, the
    big speed the Classic readout's, the clock at the lap's last frame the Laps table's lap time,
    and a Δ inside the printed 0.07 s precision is neutral; the baseline reads Δbest, or Δref with
    a cross-recording reference loaded;
  * the stamp at 1920x1080, 1080x1920, 406x720 and 1080x1080: the Classic lines, the Classic
    rect's size, as much ink as tall as Classic's, clear of the g-meter;
  * a reference loaded: the tower and the bar read Δref; the bar's scale is each lap's own on
    a whole-recording export; outside a lap there is no tower;
  * Classic: `styled(cfg, "classic")` is the config itself, and a Classic render never reaches the
    Broadcast element (the cross-tree frame-hash proof against main is in the PR);
  * the dialog: a Style row opening on Classic; Broadcast turns the ghost and the corner cards ON
    by default, an untick there is remembered for Broadcast only, and switching back to Classic
    restores Classic's own ticks — nothing ticked in Broadcast reaches a Classic export;
  * a real 720p Broadcast export through the real dialog: a playable H.264 file whose sound runs
    to its end.

Run: python tests/test_export_broadcast.py
"""
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import numpy as np  # noqa: E402

from studio.dev._jail import divert_app_support  # noqa: E402

_JAIL = divert_app_support("pacer-test-export-broadcast-")

from PySide6.QtCore import QRectF, QTimer  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QMessageBox,
    QProgressDialog,
)

_APP = QApplication.instance() or QApplication([])

from studio import chapters, data_quality, prefs, theme  # noqa: E402
from studio import export_video as ev  # noqa: E402
from studio._signal import fmt_time  # noqa: E402
from studio.dev import make_demo as md  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.export_palette import EXPORT  # noqa: E402
from studio.overlay import broadcast, layout  # noqa: E402
from studio.session import Session  # noqa: E402

theme.apply_theme(_APP)   # the export's face is Inter: the layout is measured in it

PIC_W, PIC_H = 1280, 720
STAMP_SIZES = ((1920, 1080), (1080, 1920), (406, 720), (1080, 1080))
STAMP_LINES = [data_quality.STAMP_PROVISIONAL, data_quality.STAMP_ESTIMATED,
               data_quality.STAMP_LOW_GPS.format(pct=12)]
_FIX: dict = {}


def _flat_picture(path, truth, first_payload, n_payloads, ffmpeg):
    subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c=0x1d2330:s={PIC_W}x{PIC_H}:r=30000/1001",
                    "-frames:v", str(n_payloads * sg.FRAMES_PER_PAYLOAD), "-c:v", "libx264",
                    "-preset", "ultrafast", "-tune", "stillimage", "-pix_fmt", "yuv420p",
                    "-x264-params", "keyint=300:min-keyint=300:scenecut=0",
                    "-video_track_timescale", "30000", "-an", "-n", path],
                   check=True, capture_output=True)


def _fixture() -> dict:
    if _FIX:
        return _FIX
    tmp = tempfile.TemporaryDirectory(prefix="pacer-export-broadcast-")
    rec = sg.generate(os.path.join(tmp.name, "rec"), md.DEMO_SEED, md.DEMO_LAPS, chapters=1,
                      origin=md.DEMO_ORIGIN, video=_flat_picture, habit=md.DEMO_HABIT, audio=True)
    s = Session.load(chapters.discover_siblings(rec.paths[0]))
    assert s.timing_verified, "the demo circuit's timing is not verified on its built-in track"
    best = s.best_lap_id()
    others = [i for i in s.valid_lap_ids() if i != best]
    lap = max(others, key=lambda i: s.lap_time(i) - s.lap_time(best))
    out = os.path.join(tmp.name, "out")
    os.makedirs(out)
    _FIX.update(tmp=tmp, src=rec.paths[0], s=s, best=best, lap=lap, out=out)
    return _FIX


def _spec(s, lap, style="broadcast", extras=("ghost", "corner_cards"), **kw):
    cfg = broadcast.styled(ev.OverlayConfig(encoder="libx264", hwaccel_decode=False,
                                            elements=extras, **kw), style)
    return ev.build_lap_spec(s, os.path.join(_fixture()["out"], "never.mp4"), lap, config=cfg,
                             lead_in=1.0)


def _painter(s, spec, w=1920, h=1080):
    return ev.OverlayPainter(s, spec, w, h, 30000 / 1001)


def _element(painter, name):
    return next(e for e in painter.elements if e.name == name)


class _Stamped:
    """The honesty stamp forced on (three lines, the longest the app burns) for a `with` block."""

    def __enter__(self):
        self._real = data_quality.burned_timing_stamp
        data_quality.burned_timing_stamp = lambda _s: list(STAMP_LINES)

    def __exit__(self, *_a):
        data_quality.burned_timing_stamp = self._real


# ------------------------------------------------------------------------------ layout
def _box(r: QRectF) -> layout.Box:
    return layout.Box(r.x(), r.y(), r.width(), r.height())


def _dialog_frames() -> list[tuple[int, int]]:
    """Every output frame the export dialog can produce: the source shapes a GoPro (and a phone)
    records × its Resolution rows × its Shape rows × its Source-frame rows, through the dialog's
    own `frame_geometry` — plus two odd sizes. Unique (w, h)."""
    from studio.export_controller import ExportController as EC
    sources = [(1920, 1080), (1280, 720), (2704, 1520), (3840, 2160), (5312, 2988), (1440, 1080),
               (2704, 2028), (4000, 3000), (1080, 1920), (2160, 3840)]
    frames = set()
    for sw, sh in sources:
        for _l, res in EC._EXPORT_RES_OPTIONS:
            for _a, aspect in EC._EXPORT_ASPECT_OPTIONS:
                for _f, fit in EC._EXPORT_FIT_OPTIONS:
                    geo = ev.frame_geometry(sw, sh, ev.OverlayConfig(out_height=res, aspect=aspect,
                                                                     frame_fit=fit))
                    frames.add((geo.out_w, geo.out_h))
    frames |= {(1000, 562), (406, 720)}
    return sorted(frames)


def test_every_frame_the_dialog_offers_lays_out_clear_and_safe():
    """The whole matrix: every dialog frame × honesty stamp off / three lines × ghost + cards
    off / on. Each must lay out — no element on another (the moved stamp included, against the
    g-meter and the map), none outside the safe area, the ghost inside the minimap — because a
    layout that cannot is an export that fails AFTER the user pressed Export."""
    f = _fixture()
    s, lap = f["s"], f["lap"]
    assert layout.MARGIN == ev.OverlayConfig().margin_frac, "the table's margin is not Classic's"
    frames = _dialog_frames()
    assert (1080, 1080) in frames and (1080, 1920) in frames and (720, 720) in frames, frames
    n = 0
    for stamped in (False, True):
        for extras in ((), ("ghost", "corner_cards")):
            for w, h in frames:
                if stamped:
                    with _Stamped():
                        p = _painter(s, _spec(s, lap, extras=extras), w, h)
                else:
                    p = _painter(s, _spec(s, lap, extras=extras), w, h)
                what = f"{w}x{h} stamp={stamped} extras={bool(extras)}"
                b = _element(p, "broadcast")
                boxes = {**b.boxes, "gmeter": _box(p._g_rect), "map": _box(p._map._box),
                         "stamp": _box(p._stamp_rect)}
                if extras:
                    boxes["card"] = _box(_element(p, "corner_cards").rect)
                m = layout.MARGIN * min(w, h)
                safe = layout.Box(m, m, w - 2 * m, h - 2 * m)
                names = sorted(boxes)
                for i, a in enumerate(names):
                    assert safe.contains(boxes[a]), f"{what}: {a} leaves the safe area"
                    for c in names[i + 1:]:
                        assert not boxes[a].intersects(boxes[c]), (
                            f"{what}: {a} {boxes[a]} overlaps {c} {boxes[c]}")
                assert (p._stamp_rect.height() > 0) == stamped, what
                assert abs(p._map._box.height() - layout.MAP_H_FRAC * min(w, h)) < 1e-6, what
                if extras:
                    g = _element(p, "ghost")
                    for t in np.linspace(p._spec.lap_t0 + 1, p._spec.lap_t1 - 1, 6):
                        vals = ev.overlay_values_at(s, float(t), p._spec)
                        st = g.state(vals)
                        q = p._map._proj(float(st.x[g._row]), float(st.y[g._row]))
                        assert p._map._box.contains(q), f"{what}: the ghost left the minimap"
                n += 1
    print(f"ok the layout: {n} of {n} combinations lay out ({len(frames)} dialog frames x stamp "
          f"off/3 lines x extras off/on), every element clear and safe")


def test_a_layout_that_would_overlap_fails_by_name():
    slots = layout.table(1920, 1080)
    keep = {"gmeter": layout.Box(20, 20, 1880, 1040)}      # nowhere left for the Δ bar
    try:
        layout.resolve(slots, ["dbar"], 1920, 1080, {}, keep)
    except layout.LayoutError as exc:
        assert "dbar" in str(exc) and "gmeter" in str(exc), str(exc)
    else:
        raise AssertionError("an overlapping layout was accepted")
    print("ok the layout refuses an overlap, by name")


# ------------------------------------------------------------------------------ truth
def test_every_figure_is_its_classic_twin_on_every_frame():
    f = _fixture()
    s, lap = f["s"], f["lap"]
    spec = _spec(s, lap)
    p = _painter(s, spec)
    b = _element(p, "broadcast")
    times = ev.frame_times(spec.t0, spec.t1, 30000 / 1001)
    n_delta = n_level = 0
    max_diff = 0.0
    last_clock = None
    for t in times:
        vals = ev.overlay_values_at(s, float(t), spec)
        head, clock, delta, colour, _frac, bar, speed = b.state(vals)
        label, tail, strip_colour, _f = ev._strip_runs(s, vals, spec.lap_t0, False, None)
        assert f"{head}   {clock}" == label, (label, head, clock)
        assert speed == ev._readout_runs(vals, None)[0], (speed, vals.speed_kmh)
        if tail:
            assert delta == "Δbest" + tail[1:], (delta, tail)
            max_diff = max(max_diff, abs(float(delta[len("Δbest "):].replace("−", "-"))
                                         - float(tail[2:].replace("−", "-"))))
            n_delta += 1
            level = broadcast.is_level(vals.delta_s)
            n_level += level
            assert colour == (EXPORT.neutral if level else strip_colour), (vals.delta_s, colour)
            assert bar == vals.delta_s
        else:
            assert delta == "" and bar is None
        if vals.lap_finished:
            last_clock = last_clock or clock
    assert n_delta > 1000 and 0 < n_level < n_delta, (n_delta, n_level)
    assert max_diff == 0.0, max_diff
    # Level is decided on the PRINTED value: 0.0651 prints "+0.07" (not level), 0.0649 "+0.06".
    assert not broadcast.is_level(0.0651) and not broadcast.is_level(-0.0651)
    assert broadcast.is_level(0.0649) and broadcast.is_level(-0.0649)
    # The render's finish frame (`with_finish_frame`) lands ON the line.
    vals = ev.overlay_values_at(s, spec.lap_t1, spec)
    assert vals.lap_finished
    last_clock = b.state(vals)[1]
    assert last_clock == fmt_time(s.lap_time(lap)), (last_clock, s.lap_time(lap))
    # The baseline, named as the hero names it.
    assert broadcast.baseline_name(s) == "Δbest"

    class _Ref:
        def has_reference(self):
            return True
    assert broadcast.baseline_name(_Ref()) == "Δref"
    # On the best lap: the ★ BEST mark, and no bar.
    best_spec = _spec(s, f["best"])
    bp = _element(_painter(s, best_spec), "broadcast")
    vals = ev.overlay_values_at(s, best_spec.lap_t0 + 5.0, best_spec)
    st = bp.state(vals)
    assert st[2] == "★ BEST" and st[5] is None, st
    print(f"ok truth: {len(times)} frames; Δ on {n_delta} (level {n_level}), max |diff| "
          f"{max_diff}; the last clock is the Laps table's {last_clock}")


def _stamp_ink(painter, w, h, rect):
    """(amber ink pixels, ink rows) of the frame `painter` paints, inside `rect` grown by 2 px."""
    s = painter._session
    vals = ev.overlay_values_at(s, painter._spec.lap_t0 + 3.0, painter._spec)
    raw = ev._paint_alpha_frame(painter, w, h, vals, painter.advance_and_snapshot(vals))
    img = np.frombuffer(raw, np.uint8).reshape(h, w, 4).astype(int)
    y0, y1 = max(0, int(rect.top()) - 2), min(h, int(rect.bottom()) + 2)
    x0, x1 = max(0, int(rect.left()) - 2), min(w, int(rect.right()) + 2)
    roi = img[y0:y1, x0:x1]
    amber = ((roi[..., 0] > 230) & (roi[..., 1] > 150) & (roi[..., 1] < 200) & (roi[..., 2] < 80)
             & (roi[..., 3] > 200))
    rows = np.flatnonzero(amber.any(axis=1))
    return int(amber.sum()), (int(rows[-1] - rows[0] + 1) if len(rows) else 0)


def test_the_honesty_stamp_is_kept_at_its_classic_size_at_every_shape():
    """Every line, at the Classic size, in landscape, 9:16 (incl. the 406x720 where the g-meter is
    in the way and the stamp drops below the top row), and square: the lines are the Classic
    painter's, the rect is the Classic rect's size, and the painted ink is as tall and as much."""
    f = _fixture()
    s, lap = f["s"], f["lap"]
    for w, h in STAMP_SIZES:
        with _Stamped():
            classic = _painter(s, _spec(s, lap, style="classic", extras=()), w, h)
            bp = _painter(s, _spec(s, lap), w, h)
        c, m = classic._stamp_rect, bp._stamp_rect
        assert bp._stamp_lines == classic._stamp_lines == STAMP_LINES, (w, h)
        assert c.height() > 0 and (c.width(), c.height()) == (m.width(), m.height()), (w, h, c, m)
        tower = _element(bp, "broadcast").tower
        assert m.top() > tower.bottom() and m.left() > tower.left(), (w, h, m, tower)
        assert not m.intersects(bp._g_rect), f"{w}x{h}: the stamp is under the g-meter"
        c_px, c_rows = _stamp_ink(classic, w, h, c)
        b_px, b_rows = _stamp_ink(bp, w, h, m)
        assert abs(b_rows - c_rows) <= 1 and c_rows > 0, (w, h, c_rows, b_rows)
        assert abs(b_px - c_px) <= 0.05 * c_px, (w, h, c_px, b_px)
        print(f"ok the stamp at {w}x{h}: {len(STAMP_LINES)} lines, {m.width():.0f}x"
              f"{m.height():.0f} as Classic, ink {b_rows} rows / {b_px} px (Classic {c_rows} / "
              f"{c_px})")


def test_a_reference_names_the_baseline_in_the_tower_and_on_the_bar():
    """With a cross-recording reference loaded the export's Δ is against it: the tower and the
    bar say Δref (never a hard-coded Δbest)."""
    f = _fixture()
    s, lap = f["s"], f["lap"]
    s.has_reference = lambda: True
    try:
        p = _painter(s, _spec(s, lap))
        b = _element(p, "broadcast")
        vals = ev.overlay_values_at(s, p._spec.lap_t0 + 20.0, p._spec)
        st = b.state(vals)
        assert st[2].startswith("Δref "), st
        assert b.bar_labels and all(mid == "Δref" for _l, mid, _r in b.bar_labels.values()), \
            b.bar_labels
    finally:
        del s.has_reference
    assert broadcast.baseline_name(s) == "Δbest"
    print("ok a reference loaded: the tower and the bar read Δref")


def test_the_bar_scale_is_each_laps_own():
    """A whole-recording export over a slow lap and a quick one: each lap's bar carries its OWN
    scale at its ends, so the slow lap does not flatten the quick one."""
    f = _fixture()
    s, slow, best = f["s"], f["lap"], f["best"]
    cfg = broadcast.styled(ev.OverlayConfig(encoder="libx264", hwaccel_decode=False),
                           broadcast.STYLE_BROADCAST)
    base = ev.build_session_spec(s, os.path.join(f["out"], "never.mp4"), config=cfg)
    quick = next(i for i in (slow - 1, slow + 1) if i in s.valid_lap_ids() and i != best)
    a, b_ = sorted((slow, quick))
    spec = replace(base, t0=s.lap_window(a)[0] - 0.5, t1=s.lap_window(b_)[1] + 0.5)
    p = _painter(s, spec)
    el = _element(p, "broadcast")
    want = {}
    for lap in (slow, quick):
        ls, le = s.lap_window(lap)
        d = [abs(broadcast.printed_delta(s.delta_at_lap(lap, t)))
             for t in np.linspace(ls + 0.05, le - 0.05, 400)]
        want[lap] = broadcast.delta_scale(max(d))
    assert want[slow] > want[quick], want
    for lap in (slow, quick):
        sc = el.scale_of(lap)
        assert sc == want[lap], (lap, sc, want)
        left, _mid, right = el.bar_labels[sc]
        assert (left, right) == (f"−{sc:g} s", f"+{sc:g} s"), (left, right)
    print(f"ok the bar scale per lap: slow lap ±{el.scale_of(slow):g} s, quick lap "
          f"±{el.scale_of(quick):g} s")


def test_outside_a_lap_the_tower_draws_nothing():
    """Pit time on a whole-recording export: no lap to name, so no tower (as Classic)."""
    f = _fixture()
    s = f["s"]
    cfg = broadcast.styled(ev.OverlayConfig(encoder="libx264", hwaccel_decode=False),
                           broadcast.STYLE_BROADCAST)
    spec = ev.build_session_spec(s, os.path.join(f["out"], "never.mp4"), config=cfg)
    p = _painter(s, spec)
    el = _element(p, "broadcast")
    t = spec.t0 + 0.2
    vals = ev.overlay_values_at(s, t, spec)
    assert vals.lap_id is None, "the recording's first instant is inside a lap"
    raw = ev._paint_alpha_frame(p, 1920, 1080, vals, p.advance_and_snapshot(vals))
    img = np.frombuffer(raw, np.uint8).reshape(1080, 1920, 4)
    r = el.tower
    roi = img[int(r.top()):int(r.bottom()), int(r.left()):int(r.right()), 3]
    assert int(roi.max()) == 0, f"tower ink outside a lap (alpha up to {roi.max()})"
    print("ok outside a lap: no tower")


def test_classic_never_reaches_the_broadcast_element():
    f = _fixture()
    s, lap = f["s"], f["lap"]
    cfg = ev.OverlayConfig(elements=("ghost",))
    assert broadcast.styled(cfg, broadcast.STYLE_CLASSIC) is cfg
    styled = broadcast.styled(cfg, broadcast.STYLE_BROADCAST)
    assert styled.elements == ("broadcast", "broadcast_map", "ghost"), styled.elements
    calls = []
    real = broadcast.BroadcastElement.prepare
    broadcast.BroadcastElement.prepare = lambda *a: (calls.append(1), real(*a))[1]
    try:
        p = _painter(s, _spec(s, lap, style="classic"))
    finally:
        broadcast.BroadcastElement.prepare = real
    assert calls == [] and [e.name for e in p.elements] == ["ghost", "corner_cards"]
    assert p._map._box.height() == ev.OverlayConfig().map_h_frac * 1080
    print("ok Classic: the config untouched, the Broadcast element never prepared")


# ------------------------------------------------------------------------------ the dialog
def _window():
    from studio.app import StudioWindow
    f = _fixture()
    if "win" not in f:
        win = StudioWindow([])
        win.session = f["s"]
        win._paths = [f["src"]]
        f["win"] = win
    return f["win"]


def _open_dialog(lap, act):
    win = _window()
    orig = QDialog.exec

    def on_exec(dlg):
        form = dlg.findChild(QFormLayout)
        rows = {}
        for r in range(form.rowCount()):
            label = form.itemAt(r, QFormLayout.ItemRole.LabelRole)
            field = form.itemAt(r, QFormLayout.ItemRole.FieldRole)
            if label is not None and field is not None:
                rows[label.widget().text()] = field.widget()
        return act(dlg, rows)
    QDialog.exec = on_exec
    try:
        return win.exports._ask_export_options(lap)
    finally:
        QDialog.exec = orig


def _ticks(rows):
    return tuple(b.isChecked() for b in rows["Extras"].findChildren(QCheckBox))


def test_the_style_row_turns_the_extras_on_in_broadcast_only():
    f = _fixture()
    lap = f["lap"]
    data = prefs.load()
    for key in ("export_style_idx", "export_ghost", "export_corner_cards",
                "export_broadcast_ghost", "export_broadcast_corner_cards", "export_scope_idx"):
        data.pop(key, None)
    prefs.save(data)
    seen = {}

    def first(dlg, rows):
        style = rows["Style"]
        assert isinstance(style, QComboBox)
        seen["opened"] = (style.currentText(), _ticks(rows))
        style.setCurrentIndex(1)
        seen["broadcast"] = _ticks(rows)
        rows["Extras"].findChildren(QCheckBox)[0].setChecked(False)    # untick the ghost
        style.setCurrentIndex(0)
        seen["back"] = _ticks(rows)
        style.setCurrentIndex(1)
        seen["again"] = _ticks(rows)
        return QDialog.Accepted

    choice = _open_dialog(lap, first)
    assert seen["opened"] == ("Classic", (False, False)), seen["opened"]
    assert seen["broadcast"] == (True, True), seen["broadcast"]
    assert seen["back"] == (False, False), f"Broadcast's ticks leaked into Classic: {seen['back']}"
    assert seen["again"] == (False, True), seen["again"]
    assert choice.config.elements == ("broadcast", "broadcast_map", "corner_cards"), \
        choice.config.elements
    assert choice.config.map_h_frac == layout.MAP_H_FRAC
    assert prefs.get("export_style_idx") == 1
    assert prefs.get("export_broadcast_ghost") is False
    assert prefs.get("export_broadcast_corner_cards") is True
    assert prefs.get("export_ghost", None) is None and prefs.get("export_corner_cards", None) is None
    again = {}

    def second(dlg, rows):
        again["opened"] = (rows["Style"].currentText(), _ticks(rows))
        rows["Style"].setCurrentIndex(0)
        again["classic"] = _ticks(rows)
        return QDialog.Accepted

    choice = _open_dialog(lap, second)
    assert again["opened"][0].startswith("Broadcast") and again["opened"][1] == (False, True)
    assert again["classic"] == (False, False)
    assert choice.config.elements == () and choice.config.map_h_frac == 0.22, choice.config
    assert prefs.get("export_broadcast_ghost") is False, "a Classic export rewrote Broadcast's ticks"
    print("ok the dialog: Style opens on Classic; Broadcast ticks the extras; nothing carries over")


# ------------------------------------------------------------------------------ end to end
def test_a_real_broadcast_export_through_the_dialog_plays_with_sound_to_the_end():
    f = _fixture()
    win = _window()
    prefs.set("export_style_idx", 1)
    prefs.set("export_res_idx", 0)                        # 720p
    prefs.set("export_lead_idx", 0)
    prefs.set("export_scope_idx", 0)
    opened, boxes, saves = {}, [], []

    def tick():
        w = QApplication.activeModalWidget()
        if w is None or not w.isVisible() or isinstance(w, QProgressDialog):
            return
        if isinstance(w, QMessageBox):
            boxes.append(w.text())
            done = next((b for b in w.buttons() if b.text() == "Done"), None)
            (done or w.escapeButton() or w.buttons()[0]).click()
        elif isinstance(w, QDialog) and not opened:
            form = w.findChild(QFormLayout)
            for r in range(form.rowCount()):
                label = form.itemAt(r, QFormLayout.ItemRole.LabelRole)
                field = form.itemAt(r, QFormLayout.ItemRole.FieldRole)
                if label is not None and hasattr(field.widget(), "currentText"):
                    opened[label.widget().text()] = field.widget().currentText()
            w.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()

    real_save = QFileDialog.getSaveFileName

    def save_panel(_parent, title, default, filt, *_a, **_k):
        saves.append(default)
        return os.path.join(f["out"], os.path.basename(default)), filt
    QFileDialog.getSaveFileName = staticmethod(save_panel)
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(tick)
    timer.start()
    try:
        win.exports.export_overlay_video()
    finally:
        timer.stop()
        QFileDialog.getSaveFileName = real_save
    assert opened.get("Style", "").startswith("Broadcast"), opened
    assert saves, f"no save panel; boxes: {boxes}"
    out = os.path.join(f["out"], os.path.basename(saves[-1]))
    assert os.path.isfile(out) and len(boxes) == 1 and "exported" in boxes[0], (out, boxes)
    ffprobe = ev.FFPROBE
    info = json.loads(subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format",
                                      "-of", "json", out], check=True, capture_output=True,
                                     text=True).stdout)
    video = [x for x in info["streams"] if x["codec_type"] == "video"]
    audio = [x for x in info["streams"] if x["codec_type"] == "audio"]
    assert len(video) == 1 and len(audio) == 1, info["streams"]
    assert (video[0]["codec_name"], video[0]["height"]) == ("h264", 720), video[0]
    vd, ad = float(video[0]["duration"]), float(audio[0]["duration"])
    assert vd > 30 and abs(ad - vd) < 0.1, f"the sound stops at {ad:.2f} s of {vd:.2f} s"
    # Decodes to the end (playable), and the tower's amber header is burned in.
    ffmpeg = ev.FFMPEG
    frame = subprocess.run([ffmpeg, "-v", "error", "-sseof", "-0.5", "-i", out, "-frames:v", "1",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], check=True,
                           capture_output=True).stdout
    img = np.frombuffer(frame, np.uint8).reshape(720, 1280, 3).astype(int)
    head = img[18:40, 20:120]
    assert ((head[..., 0] > 200) & (head[..., 2] < 90)).mean() > 0.5, "no tower header in frame"
    print(f"ok a real 720p Broadcast export: {vd:.2f} s of H.264, sound to {ad:.2f} s")


def _run_all():
    for fn in (test_every_frame_the_dialog_offers_lays_out_clear_and_safe,
               test_a_layout_that_would_overlap_fails_by_name,
               test_every_figure_is_its_classic_twin_on_every_frame,
               test_the_honesty_stamp_is_kept_at_its_classic_size_at_every_shape,
               test_a_reference_names_the_baseline_in_the_tower_and_on_the_bar,
               test_the_bar_scale_is_each_laps_own,
               test_outside_a_lap_the_tower_draws_nothing,
               test_classic_never_reaches_the_broadcast_element,
               test_the_style_row_turns_the_extras_on_in_broadcast_only,
               test_a_real_broadcast_export_through_the_dialog_plays_with_sound_to_the_end):
        fn()
    print("test_export_broadcast: all ok")


if __name__ == "__main__":
    _run_all()
