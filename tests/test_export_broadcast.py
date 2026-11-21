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
import functools
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
    QLabel,
    QMessageBox,
    QProgressDialog,
)

_APP = QApplication.instance() or QApplication([])

from studio import (  # noqa: E402
    chapters,
    data_quality,
    overlay,  # noqa: E402
    prefs,
    theme,
)
from studio import export_video as ev  # noqa: E402
from studio._signal import fmt_time  # noqa: E402
from studio.dev import make_demo as md  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.export_palette import EXPORT  # noqa: E402
from studio.overlay import broadcast, corner_cards, layout  # noqa: E402
from studio.session import Session  # noqa: E402

theme.apply_theme(_APP)   # the export's face is Inter: the layout is measured in it

PIC_W, PIC_H = 1280, 720
STAMP_SIZES = ((1920, 1080), (1080, 1920), (406, 720), (1080, 1080))
# QA #565/#558: a 9:16 crop of 540p footage (304x540) and one smaller — the export used to fail
# there with "the encoder stopped".
SMALL_FRAMES = ((304, 540), (270, 480))
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


def _once_per_spec(test):
    """Run `test` with each spec's per-frame lookups computed once rather than once per painter.

    An `OverlayPainter` walks every frame of its spec when it is built (`_burned_runs`, and the
    tower's `prepare`) to fit its plates to the widest string the export burns. Those values depend
    on the session, the spec and the frame's time, never on the frame's size, so the matrix's 240
    painters over three specs re-derived the same lookups 240 times: 102 s of this file's 146 s
    (2026-11-18, load ~7). The real functions still compute every value once; a repeat of the same
    (session, spec, time) is served from the first. Nothing the painter measures changes: the
    sizes, fonts, layout and plates are each painter's own. Valid only while the three memoised
    functions are pure in their arguments: if one ever reads painter state (the honesty stamp, a
    frame size), a repeat would hand one painter another's answer, so drop the memo then."""
    @functools.wraps(test)
    def run():
        real_values, real_burned, real_runs = ev.overlay_values_at, ev._burned_runs, ev._strip_runs
        values, burned, runs, alive = {}, {}, {}, []

        def values_at(session, t, spec=None):
            key = (id(session), id(spec), float(t))
            if key not in values:
                alive.append(spec)          # an id stays this object's while the memo lives
                values[key] = real_values(session, t, spec)
            return values[key]

        def burned_runs(session, spec, fps):
            key = (id(session), id(spec), float(fps))
            if key not in burned:
                alive.append(spec)
                burned[key] = real_burned(session, spec, fps)
            return burned[key]

        def strip_runs(session, vals, lap_t0, is_best, palette=None):
            key = (id(session), id(vals), lap_t0, is_best, palette)
            if key not in runs:
                alive.append(vals)
                runs[key] = real_runs(session, vals, lap_t0, is_best, palette)
            return runs[key]
        ev.overlay_values_at, ev._burned_runs, ev._strip_runs = values_at, burned_runs, strip_runs
        try:
            return test()
        finally:
            ev.overlay_values_at, ev._burned_runs, ev._strip_runs = (real_values, real_burned,
                                                                     real_runs)
    return run


@_once_per_spec
def test_a_vertical_frame_keeps_the_classic_plates_out_of_the_apps_bands():
    """Portrait (owner ruling, wave 22) holds the Classic style too: the strip and its stamp, the
    g-meter, the minimap, the speed readout and the corner card stay out of the top 8 % and the
    bottom 18 % of the height, which the apps that play a vertical video cover. (The Broadcast
    elements are held by the matrix below.)"""
    f = _fixture()
    s, lap = f["s"], f["lap"]
    spec = _spec(s, lap, style="classic")
    frames = [(w, h) for w, h in _dialog_frames() if h > w]
    assert (608, 1080) in frames and (1080, 1920) in frames, frames
    for w, h in frames:
        with _Stamped():
            p = _painter(s, spec, w, h)
        boxes = {"strip": p._strip_rect, "stamp": p._stamp_rect, "g-meter": p._g_rect,
                 "minimap": p._map._box, "speed": p._readout_rect}
        card = next((e for e in p.elements if e.name == "corner_cards"), None)
        if card is not None:
            boxes["corner card"] = card.rect
        for name, r in boxes.items():
            assert r.y() >= 0.08 * h - 1e-6 and r.bottom() <= 0.82 * h + 1e-6, (
                f"{w}x{h}: the {name} {r} is in a band the apps cover")


@_once_per_spec
def test_every_frame_the_dialog_offers_lays_out_clear_and_safe():
    """The whole matrix: every dialog frame × honesty stamp off / three lines × a lap export with
    ghost + cards off / on, and a WHOLE-RECORDING export (its tower carries the reserved LAST
    sub-plate, drawn at or above the 11 px label floor, or not at all). Each must lay out — no element on another (the moved stamp included, against the
    g-meter and the map), none outside the safe area, the ghost inside the minimap — because a
    layout that cannot is an export that fails AFTER the user pressed Export."""
    f = _fixture()
    s, lap = f["s"], f["lap"]
    assert layout.MARGIN == ev.OverlayConfig().margin_frac, "the table's margin is not Classic's"
    frames = _dialog_frames()
    assert (1080, 1080) in frames and (1080, 1920) in frames and (720, 720) in frames, frames
    frames += list(SMALL_FRAMES)
    n = skipped = wide = lasts = plated = padded = 0
    a = next(i for i in (lap, lap - 1) if i + 1 in s.valid_lap_ids())
    whole = replace(ev.build_session_spec(s, os.path.join(f["out"], "never.mp4"), config=(
        broadcast.styled(ev.OverlayConfig(encoder="libx264", hwaccel_decode=False),
                         broadcast.STYLE_BROADCAST))),
        t0=s.lap_window(a)[0] - 0.5, t1=s.lap_window(a + 1)[1] + 2.5)
    # One spec per extras row, built once: a spec is size-free, the painters are not.
    specs = {(): _spec(s, lap, extras=()), ("ghost", "corner_cards"): _spec(s, lap),
             "whole": whole}
    classic_spec = _spec(s, lap, style="classic", extras=())
    for stamped in (False, True):
        for extras in ((), ("ghost", "corner_cards"), "whole"):
            for w, h in frames:
                spec = specs[extras]
                if stamped:
                    with _Stamped():
                        p = _painter(s, spec, w, h)
                else:
                    p = _painter(s, spec, w, h)
                what = f"{w}x{h} stamp={stamped} extras={extras}"
                ex = () if extras == "whole" else extras
                b = _element(p, "broadcast")
                if extras == "whole":
                    from studio.overlay.broadcast import _LABEL_MIN_PX
                    if b._holds:      # drawn: at the floor or above, in its reserved place
                        assert b.last_px >= _LABEL_MIN_PX, (what, b.last_px)
                        assert abs(b.tower.height() - (b._main_h + b._last_gap + b._last_h)) \
                            < 1e-6, what
                        lasts += 1
                    else:
                        assert b.last_px < _LABEL_MIN_PX, f"{what}: no LAST line above the floor"
                boxes = {**b.boxes, "gmeter": _box(p._g_rect), "map": _box(p._map._box),
                         "stamp": _box(p._stamp_rect)}
                card = next((e for e in p.elements if e.name == "corner_cards"), None)
                if ex and card is not None:
                    boxes["card"] = _box(card.rect)
                elif ex:
                    # A card that cannot fit sits the export out, said so — only on a tiny frame.
                    assert (w, h) in SMALL_FRAMES, f"{what}: the corner cards sat out"
                    assert p.notes == [corner_cards.SKIPPED_TOO_SMALL], (what, p.notes)
                    skipped += 1
                m = layout.MARGIN * min(w, h)
                safe = layout.Box(m, m, w - 2 * m, h - 2 * m)
                edge = layout.Box(0, 0, w, h)      # the stamp's last resort: the frame across
                if h > w:
                    # PORTRAIT (owner ruling, wave 22): nothing in the top 8 % or the bottom 18 %
                    # of the height, which the apps that play a vertical video cover; at the
                    # margin alone the big speed sat 13 px off the bottom of 608x1080.
                    top, bottom = max(m, 0.08 * h), max(m, 0.18 * h)
                    safe = layout.Box(m, top, w - 2 * m, h - top - bottom)
                    edge = layout.Box(0, top, w, h - top - bottom)
                names = sorted(boxes)
                for i, a in enumerate(names):
                    room = safe
                    if a == "stamp" and not safe.contains(boxes[a]):
                        # Only where the Classic stamp itself runs into the margin, and never
                        # further past the frame's edge than the Classic one (cut on a frame
                        # narrower than the stamp, as Classic's is).
                        with _Stamped():
                            classic = _painter(s, classic_spec, w, h)
                        c = _box(classic._stamp_rect)
                        assert not safe.contains(c), what
                        st = boxes[a]
                        assert st.x >= 0 and st.right - w <= max(0.0, c.right - w) + 1e-6, what
                        room = layout.Box(0, edge.y, max(w, st.right), edge.h)
                        wide += 1
                    assert room.contains(boxes[a]), f"{what}: {a} leaves the safe area"
                    for c in names[i + 1:]:
                        assert not boxes[a].intersects(boxes[c]), (
                            f"{what}: {a} {boxes[a]} overlaps {c} {boxes[c]}")
                # The backing plates under the two Classic texts kept (the stamp, the |g| readout):
                # round their text, never over another element, never out of its room — so never
                # over the kart dot, which rides the minimap.
                under = {u: _box(r) for u, r in b.under.items()}
                assert ("stamp" in under) == stamped, what
                assert "gmeter" in under, what
                # The Δ bar's one plate: its box, wider only sideways, inside the safe area.
                dp = _box(b.dbar_plate)
                assert dp.contains(boxes["dbar"]) and safe.contains(dp), what
                assert (dp.y, dp.h) == (boxes["dbar"].y, boxes["dbar"].h), what
                for o, ob in boxes.items():
                    assert o == "dbar" or not dp.intersects(ob), f"{what}: the Δ plate is on {o}"
                for u, pl in under.items():
                    host = boxes[u]
                    if u == "stamp":
                        area = safe if safe.contains(host) else edge
                        held = layout.Box(host.x, host.y, min(host.right, w) - host.x, host.h)
                        assert pl.contains(held), f"{what}: the stamp's plate does not hold it"
                        assert area.contains(pl), f"{what}: the stamp's plate leaves its room"
                        # Its padding is held wherever the frame has room: the left edge is the
                        # tower's, the text inset as far at the right, a margin above and below.
                        if (w, h) not in SMALL_FRAMES:
                            tw = boxes["tower"]
                            assert abs(pl.x - tw.x) < 1e-6, f"{what}: stamp plate {pl} vs {tw}"
                            assert abs((pl.right - host.right) - (host.x - tw.x)) < 1e-6, what
                            assert pl.y < host.y and pl.bottom > host.bottom, what
                            padded += 1
                    else:
                        assert host.contains(pl), f"{what}: the readout plate leaves the g-meter"
                        from studio import gmeter_overlay as gm
                        _cx, cy, r = gm.dial_geom(host.w, host.h)
                        assert pl.y >= host.y + cy + r, f"{what}: the readout plate is on the dial"
                    for o, ob in boxes.items():
                        if o != u:
                            assert not pl.intersects(ob), f"{what}: the {u} plate {pl} is on {o}"
                    plated += 1
                assert (p._stamp_rect.height() > 0) == stamped, what
                assert abs(p._map._box.height() - layout.MAP_H_FRAC * min(w, h)) < 1e-6, what
                if ex:
                    g = _element(p, "ghost")
                    for t in np.linspace(p._spec.lap_t0 + 1, p._spec.lap_t1 - 1, 6):
                        vals = ev.overlay_values_at(s, float(t), p._spec)
                        st = g.state(vals)
                        q = p._map._proj(float(st.x[g._row]), float(st.y[g._row]))
                        assert p._map._box.contains(q), f"{what}: the ghost left the minimap"
                n += 1
    print(f"ok the layout: {n} of {n} combinations lay out ({len(frames)} frames x stamp "
          f"off/3 lines x lap export extras off/on + whole-recording), every element clear and "
          f"safe; {plated} backing plates clear ({padded} stamp plates padded and aligned with "
          f"the tower); the LAST sub-plate in {lasts}; on the tiny frames the stamp takes the Classic "
          f"stamp's own place {wide}x, the cards sit out {skipped}x")


def test_a_layout_that_cannot_fit_says_so_not_that_the_encoder_stopped():
    """A style element with no place in the frame (planted: a Δ bar taller than the frame —
    every frame the dialog offers lays out, the matrix above): the export fails before the
    encoder starts, and the failure box says what did not fit, where — not "the encoder stopped"."""
    from studio.export_controller import ExportController as EC
    f = _fixture()
    s, lap = f["s"], f["lap"]
    err = None
    real = dict(layout.BROADCAST)
    layout.BROADCAST["dbar"] = layout.Slot(("tc",), 2.0, layout.DBAR_W)
    try:
        _painter(s, _spec(s, lap, extras=()), 1920, 1080)
    except overlay.OverlayElementError as exc:
        err = exc
    finally:
        layout.BROADCAST.update(real)
    assert err is not None, "a Δ bar twice the frame's height was laid out"
    said = EC._export_failure_message(str(err), "/nowhere.mp4")
    assert said.startswith("Overlay layout: the ") and "does not fit a 1920×1080 frame" in said, said
    assert "encoder" not in said.casefold(), said
    assert isinstance(err, overlay.OverlayLayoutError), repr(err)
    print(f"ok a layout that cannot fit: {said!r}")


def test_corner_cards_that_cannot_fit_sit_the_export_out_and_say_so():
    """A card with no place clear of every plate (QA: a frame ≲ 300 px wide) is skipped for that
    export with a sentence for the finished box — never a failed export. The place is forced to
    fail here, so the rule is checked whatever sizes the fixture's plates come to."""
    f = _fixture()
    s, lap = f["s"], f["lap"]
    real = corner_cards.place

    def nowhere(*_a, **_k):
        raise ValueError("no room for a card (planted)")
    corner_cards.place = nowhere
    try:
        p = _painter(s, _spec(s, lap, extras=("corner_cards",)), 1080, 1920)
    finally:
        corner_cards.place = real
    assert [e.name for e in p.elements] == ["broadcast", "broadcast_map"], p.elements
    assert p.notes == [corner_cards.SKIPPED_TOO_SMALL], p.notes
    print("ok corner cards that cannot fit sit the export out, said so")


def test_the_finished_box_says_when_the_corner_cards_did_not_fit():
    f = _fixture()
    win = _window()
    seen = []
    real = QMessageBox.exec
    QMessageBox.exec = lambda box: (seen.append(box.text()), 0)[1]
    try:
        win.exports._video_export_finished([os.path.join(f["out"], "x.mp4")],
                                           _spec(f["s"], f["lap"]), f["lap"], [None],
                                           notes=[corner_cards.SKIPPED_TOO_SMALL])
    finally:
        QMessageBox.exec = real
    assert seen and seen[0].endswith(corner_cards.SKIPPED_TOO_SMALL), seen
    print("ok the finished box names the corner cards that did not fit")


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


def _stamp_ink(painter, w, h, rect, exact=False):
    """(amber ink pixels, ink rows) of the frame `painter` paints, inside `rect` grown by 2 px;
    `exact`: only the pixels the fill covers whole (exactly `EXPORT.accent`, opaque)."""
    s = painter._session
    vals = ev.overlay_values_at(s, painter._spec.lap_t0 + 3.0, painter._spec)
    raw = ev._paint_alpha_frame(painter, w, h, vals, painter.advance_and_snapshot(vals))
    img = np.frombuffer(raw, np.uint8).reshape(h, w, 4).astype(int)
    y0, y1 = max(0, int(rect.top()) - 2), min(h, int(rect.bottom()) + 2)
    x0, x1 = max(0, int(rect.left()) - 2), min(w, int(rect.right()) + 2)
    roi = img[y0:y1, x0:x1]
    amber = ((roi[..., 0] > 230) & (roi[..., 1] > 150) & (roi[..., 1] < 200) & (roi[..., 2] < 80)
             & (roi[..., 3] > 200))
    if exact:
        c = theme.qcolor(EXPORT.accent)
        amber = np.all(roi == (c.red(), c.green(), c.blue(), 255), axis=2)
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
        # The ink against Classic's with the stamp's backing plate set aside: over the plate the
        # glyphs' anti-aliased edges blend into dark grey rather than into nothing, which this
        # amber count reads as less ink (262 px against 288 at 406x720) though no glyph changed.
        # The plate's own promise is that: every pixel the fill covers whole is the same with it.
        be = _element(bp, "broadcast")
        plates, be._under_imgs = be._under_imgs, []
        b_px, b_rows = _stamp_ink(bp, w, h, m)
        bare = _stamp_ink(bp, w, h, m, exact=True)
        be._under_imgs = plates
        assert plates and _stamp_ink(bp, w, h, m, exact=True) == bare, (w, h, bare)
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


def test_a_lap_kept_out_of_the_bests_burns_neutral_and_says_not_counted():
    """Discovery 11: a lap out of the bests (the ⚠ set: here one that left the line) exported as a
    clip burned "LAP 10 · 0:44.227 · Δbest −0.73" in GREEN with a full green bar, its minimap drew
    the lap's own broken line, and the dialog said only "Selected lap 10 · 0:44.227". Now, in
    both styles: every frame names it "LAP n · ⚠ not counted", its Δ (and Broadcast's bar, and its
    corner cards) are the neutral white, the inset draws the best lap's line (the kart dot still
    follows this lap), and the dialog's lap line carries the reason. A whole-recording clip keeps
    the refusal (no tag) but draws the lap's Δ neutral; a clean lap is untouched."""
    f = _fixture()
    s, lap, best = f["s"], f["lap"], f["best"]
    tag = "⚠ not counted"                     # data_quality.FRAME_NOT_COUNTED, checked last
    s.lap_warnings = lambda: {lap: f"Lap {lap + 1} left the line your other laps drive by 34 m."}
    try:
        for style in (broadcast.STYLE_CLASSIC, broadcast.STYLE_BROADCAST):
            best_map = _painter(s, _spec(s, best, style=style, extras=()))._map._layer
            spec = _spec(s, lap, style=style, extras=())
            p = _painter(s, spec)
            tails = 0
            for t in ev.frame_times(spec.t0, spec.t1, 30000 / 1001)[::10]:
                vals = ev.overlay_values_at(s, float(t), spec)
                label, tail, colour, _frac = ev._frame_strip_runs(s, vals, spec, None)
                assert label.startswith(f"LAP {lap + 1} · {tag}   "), label
                if tail:
                    tails += 1
                    assert colour == EXPORT.neutral, (t, tail, colour)
            assert tails > 50 and spec.not_counted == {lap}, (tails, spec.not_counted)
            assert p._map._layer == best_map, "the inset drew the ⚠ lap's own line"
            if style == broadcast.STYLE_BROADCAST:
                st = _element(p, "broadcast").state(
                    ev.overlay_values_at(s, spec.lap_t0 + 20.0, spec))
                assert st[0] == f"LAP {lap + 1} · {tag}" and st[3] == EXPORT.neutral, st
                assert st[5] is not None, "the bar went with the colour"
            # The tagged name is the widest the top-left plate gets: it stays inside the portrait
            # safe area (`layout.safe_area`, #710) at the narrowest shapes the dialog offers.
            for w, h in ((608, 1080), (1080, 1920)):
                pp = _painter(s, spec, w, h)
                plate = (_element(pp, "broadcast").tower if style == broadcast.STYLE_BROADCAST
                         else pp._strip_rect)
                sa = layout.safe_area(w, h)
                assert sa.x <= plate.x() and plate.right() <= sa.x + sa.w + 1e-6 and \
                    sa.y <= plate.y() and plate.bottom() <= sa.y + sa.h + 1e-6, (style, w, h, plate)
        cards = corner_cards.card_rows(s, lap, None, None)
        assert cards and all(c.colour in (None, EXPORT.neutral) for c in cards), cards
        # The whole recording: no tag (the refusal), the ⚠ lap's Δ still neutral.
        cfg = broadcast.styled(ev.OverlayConfig(encoder="libx264", hwaccel_decode=False),
                               broadcast.STYLE_BROADCAST)
        whole = ev.build_session_spec(s, os.path.join(f["out"], "never.mp4"), config=cfg)
        vals = ev.overlay_values_at(s, s.lap_window(lap)[0] + 20.0, whole)
        label, tail, colour, _frac = ev._frame_strip_runs(s, vals, whole, None)
        assert vals.lap_id == lap and tag not in label and tail and colour == EXPORT.neutral
        # The dialog: the line names what each Export row WILL render (`_scope_line`), and the
        # reason comes from THOSE laps: This lap on the ⚠ lap carries it in data_quality's words,
        # Best lap never, All laps counts them, the full session says nothing.
        seen = {}

        def look(dlg, rows):
            for i in range(rows["Export"].count()):
                rows["Export"].setCurrentIndex(i)
                seen[rows["Export"].currentText()] = next(
                    lb.text() for lb in dlg.findChildren(QLabel)
                    if lb.text().startswith(("Selected lap", "Best lap", "All laps", "Full")))
            return QDialog.Rejected
        _open_dialog(lap, look)
        why = "⚠ left the line the other laps drive — not counted in your bests"
        lines = list(seen.values())
        assert lines[0].startswith(f"Selected lap {lap + 1}") and lines[0].endswith(why), lines
        assert lines[1].startswith(f"Best lap {best + 1}") and "⚠" not in lines[1], lines
        assert lines[2].endswith("one file each  ·  ⚠ 1 not counted in your bests"), lines
        assert lines[3].startswith("Full session") and "⚠" not in lines[3], lines
    finally:
        del s.lap_warnings
        s.__dict__.pop("_race_memo", None)   # the dialog's ghost read memoized the patched field
    clean = _spec(s, lap, style="classic", extras=())
    assert not clean.not_counted
    vals = ev.overlay_values_at(s, clean.lap_t0 + 20.0, clean)
    label, tail, colour, _frac = ev._frame_strip_runs(s, vals, clean, None)
    assert tag not in label and colour == ev.burned_delta_colour(vals.delta_s, None)
    assert data_quality.FRAME_NOT_COUNTED == tag
    print("ok a ⚠ lap's clip: tagged, Δ/bar/cards neutral, the best lap's line, the dialog says why")


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


def test_a_whole_recording_tower_shows_each_finished_laps_table_time():
    """QA #565: on a whole-recording export no frame lands on a crossing, so the running clock
    never shows a lap's final time. For two seconds after each crossing the tower carries a LAST
    line: exactly the Laps table's Time and Δbest cells for the lap just finished (★ on the best),
    under the new lap's running clock — and none on a single-lap export."""
    from studio import lap_table
    f = _fixture()
    s, best = f["s"], f["best"]
    ids = s.valid_lap_ids()
    a = best - 1 if best - 1 in ids else next(i for i in ids if i + 1 in ids)
    b = a + 1
    cfg = broadcast.styled(ev.OverlayConfig(encoder="libx264", hwaccel_decode=False),
                           broadcast.STYLE_BROADCAST)
    base = ev.build_session_spec(s, os.path.join(f["out"], "never.mp4"), config=cfg)
    spec = replace(base, t0=s.lap_window(a)[0] - 0.5, t1=s.lap_window(b)[1] + 2.5)
    p = _painter(s, spec)
    el = _element(p, "broadcast")
    table = lap_table.LapTable(s)
    cells = {}
    for r in range(table.table.rowCount()):
        cells[table._lap_id(r)] = (table.table.item(r, lap_table._TIME_COL).text(),
                                   table.table.item(r, lap_table._DELTA_COL).text())
    seen: dict[int, list] = {a: [], b: []}
    times = ev.frame_times(spec.t0, spec.t1, 30000 / 1001)
    for t in times:
        vals = ev.overlay_values_at(s, float(t), spec)
        got = getattr(el, "last_lap", lambda _v: None)(vals)
        for lap in (a, b):
            cross = s.lap_window(lap)[1]
            inside = vals.tt is not None and 0.0 <= vals.tt - cross < 2.0
            if inside:
                assert got is not None and got[0] == lap, (t, lap, got)
                seen[lap].append(got[1][:2])
                head, clock = el.state(vals)[:2]
                if lap == a:      # the new lap's clock runs on above it
                    assert head == f"LAP {b + 1}" and clock.startswith("0:0"), (head, clock)
        if got is not None:     # (the lap before `a` crosses 0.5 s into the clip: its own hold)
            assert any(0.0 <= vals.tt - s.lap_window(x)[1] < 2.0 for x in (a - 1, a, b)), (t, got)
    for lap in (a, b):
        time_cell, delta_cell = cells[lap]
        want = (time_cell, "★" if lap == best else delta_cell)
        assert len(seen[lap]) >= 55, f"lap {lap + 1}: {len(seen[lap])} frames of LAST"
        assert set(seen[lap]) == {want}, (lap, set(seen[lap]), want)
    # Painted: the sub-plate's ink under the tower during a hold, none just before the crossing
    # (its lower half: the tower's Δ row haloes a few pixels past the main plate).
    half = (el.tower.height() - el._main_h) / 2
    sub = QRectF(el.tower.x(), el.tower.bottom() - half, el.tower.width(), half)

    def ink(t):
        vals = ev.overlay_values_at(s, t, spec)
        raw = ev._paint_alpha_frame(p, 1920, 1080, vals, p.advance_and_snapshot(vals))
        img = np.frombuffer(raw, np.uint8).reshape(1080, 1920, 4)
        return int(img[int(sub.top()):int(sub.bottom()), int(sub.left()):int(sub.right()),
                       3].max())
    first = next(float(t) for t in times
                 if (el.last_lap(ev.overlay_values_at(s, float(t), spec)) or (None,))[0] == a)
    assert ink(first + 0.5) > 0 and ink(first - 0.5) == 0, "the LAST line is not painted"
    single = _element(_painter(s, _spec(s, a)), "broadcast")
    assert single._holds == [], "a single-lap export carries a LAST line"
    print(f"ok the LAST line: lap {a + 1} {seen[a][0]} on {len(seen[a])} frames, lap {b + 1} "
          f"{seen[b][0]} on {len(seen[b])} — the Laps table's cells")


def test_the_last_line_names_its_baseline_and_stands_without_the_tower():
    """Review of #571: the LAST line's Δ is always to the BEST lap, so it says "Δbest" — also
    under a tower reading "Δref" with a reference loaded. And it is painted whether or not the
    tower is: after the recording's last lap (ids[-1]) and over a lap followed by an excluded
    crossing (no lap on the frame), it stands under the finished lap's header strip."""
    from dataclasses import replace as dc_replace
    f = _fixture()
    s, best = f["s"], f["best"]
    ids = s.valid_lap_ids()
    cfg = broadcast.styled(ev.OverlayConfig(encoder="libx264", hwaccel_decode=False),
                           broadcast.STYLE_BROADCAST)
    base = ev.build_session_spec(s, os.path.join(f["out"], "never.mp4"), config=cfg)

    def window(a, b):
        spec = replace(base, t0=s.lap_window(a)[0] - 0.5, t1=s.lap_window(b)[1] + 2.5)
        p = _painter(s, spec)
        return spec, p, _element(p, "broadcast")

    def hold_frames(spec, el, lap):
        out = []
        for t in ev.frame_times(spec.t0, spec.t1, 30000 / 1001):
            vals = ev.overlay_values_at(s, float(t), spec)
            got = el.last_lap(vals)
            if got is not None and got[0] == lap:
                out.append(vals)
        return out

    def line(el, lap):
        return " ".join(t for t, _f, _c in el._last_runs(el._last_rows[lap]))

    def sub_ink(p, el, vals):
        raw = ev._paint_alpha_frame(p, 1920, 1080, vals, p.advance_and_snapshot(vals))
        img = np.frombuffer(raw, np.uint8).reshape(1080, 1920, 4)
        half = (el.tower.height() - el._main_h) / 2
        y0, x0 = int(el.tower.bottom() - half), int(el.tower.left())
        head_y = int(el.tower.y() + el._main_h - el._head_h)
        return (int(img[y0:int(el.tower.bottom()), x0:int(el.tower.right()), 3].max()),
                int(img[head_y:head_y + int(el._head_h), x0:int(el.tower.right()), 3].max()))

    # 1. A reference loaded: the tower reads Δref, the LAST line Δbest.
    a = next(i for i in ids if i + 2 in ids and best not in (i + 1, i + 2))
    s.has_reference = lambda: True
    try:
        spec, p, el = window(a, a + 1)
        vals = hold_frames(spec, el, a)[10]
        assert el.state(vals)[2].startswith("Δref"), el.state(vals)
        gap = el._last_rows[a][1]
        assert line(el, a) == f"LAST {el._last_rows[a][0]} Δbest {gap}", line(el, a)
    finally:
        del s.has_reference
    # 2. After the recording's last lap: no tower, the LAST line and its header strip painted.
    last = ids[-1]
    spec, p, el = window(ids[-2], last)
    held = hold_frames(spec, el, last)
    assert len(held) >= 55, f"lap {last + 1}: {len(held)} frames of LAST after the final crossing"
    vals = held[10]
    assert el.state(vals)[0] is None, "the tower is up after the final crossing"
    plate, header = sub_ink(p, el, vals)
    assert plate > 0 and header > 0, f"after lap {last + 1}: plate ink {plate}, header {header}"
    # 3. A lap followed by an excluded crossing: no lap on the frame (as Session reports one).
    spec, p, el = window(a, a + 1)
    vals = dc_replace(hold_frames(spec, el, a)[10], lap_id=None)
    assert el.last_lap(vals) is not None and el.state(vals)[0] is None
    plate, header = sub_ink(p, el, vals)
    assert plate > 0 and header > 0, f"before an excluded crossing: {plate}, {header}"
    print(f"ok the LAST line: \"{line(el, a)}\" under a Δref tower reads Δbest; it stands alone "
          f"after lap {last + 1} ({len(held)} frames) and before an excluded crossing")


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


# ------------------------------------------------------------------------------ legibility
# Flat sky, grass, white, mid-grey and black, and per-pixel grey noise (a busy, high-frequency
# picture): what a burned graphic has to read over.
BACKGROUNDS = {"white": (255, 255, 255), "grey": (128, 128, 128), "grass": (0x5A, 0x8F, 0x3C),
               "sky": (0x8E, 0xC5, 0xFF), "black": (0, 0, 0), "noise": None}
# Texts this test does not hold, each with its reason ({(module, role): why}); any OTHER text a
# frame draws that the test cannot measure fails it.
_LEGIBILITY_EXEMPT = {
    # An Extra shared with Classic, on its own lighter plate (alpha 165): 1.87:1 against that
    # plate alone over white, 3.2:1 by the ring metric at 720p. Its own change, not this one.
    ("studio.overlay.corner_cards", None): "corner card (Classic's Extra, its own plate)",
    # The Classic dial's ring tick label ("1.0"): 8-10 px, a thin halo, no plate. No pixel of it
    # is fully covered by its fill at these sizes, so it cannot be measured here.
    ("studio.gmeter_overlay", "caption"): "dial caption (Classic's, too small to measure)",
}
# The texts with NO plate under them, whose legibility rests on their outline alone: the
# minimap's C-labels (a plate would hide the line they name). The tower's header sits on its
# amber band; every other text on a `_plate`.
_OUTLINED = {("studio.overlay.broadcast", "prepare")}
_BAND = {("studio.overlay.broadcast", "_head")}


def _exempt(key) -> str | None:
    return _LEGIBILITY_EXEMPT.get((key[3], key[4])) or _LEGIBILITY_EXEMPT.get((key[3], None))


def _contrast(la: float, lb: float) -> float:
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _plate_only(key, bg) -> float:
    """The PLATE-ONLY contrast: the text's fill against its backing (a `_plate`, or the header's
    amber band) blended over the flat background `bg` — no halo counted."""
    from studio.overlay.broadcast import _PLATE_ALPHA
    backing, alpha = ((EXPORT.accent, 240) if (key[3], key[4]) in _BAND
                      else (EXPORT.halo, _PLATE_ALPHA))
    c, t = theme.qcolor(backing), theme.qcolor(key[5])
    a = alpha / 255.0
    comp = np.round(a * np.array([c.red(), c.green(), c.blue()]) + (1 - a) * np.array(bg))
    return _contrast(float(_wcag_lum([t.red(), t.green(), t.blue()])), float(_wcag_lum(comp)))


def _background(name, w, h) -> bytearray:
    rgb = BACKGROUNDS[name]
    if rgb is None:
        g = np.random.default_rng(7).integers(0, 256, size=(h, w), dtype=np.uint8)
        return bytearray(np.repeat(g[:, :, None], 3, axis=2).tobytes())
    return bytearray(np.tile(np.array(rgb, np.uint8), w * h).tobytes())


def _wcag_lum(rgb) -> np.ndarray:
    c = np.asarray(rgb, float) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return lin @ np.array([0.2126, 0.7152, 0.0722])


def _grow(m: np.ndarray, r: int) -> np.ndarray:
    out = m.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx * dx + dy * dy <= r * r:
                out |= np.roll(np.roll(m, dy, 0), dx, 1)
    return out


class _Tagged:
    """While `on`, every burned text (the export's `_draw_text` and the dial's outlined text) is
    filled in a colour of its own — (255, id, 254), a colour no overlay uses — so a second render
    of the same frame tells each text's glyph pixels apart: where the two renders differ, a fill
    touched. The halo, shadow and plates are left exactly as they are."""

    def __init__(self):
        from studio import gmeter_overlay
        self.gm, self.on, self.texts = gmeter_overlay, False, {}
        self._draw, self._outl = ev._draw_text, gmeter_overlay._draw_text_outlined

    def _tag(self, text, font, colour, module, role, halo=0.0):
        # A glyph's halo pass (the tower clock's: drawn in the halo colour, then filled again over
        # it) is not a text of its own.
        if not (self.on and text.strip()) or (colour == EXPORT.halo and halo > 0):
            return colour
        key = (text, font.pixelSize(), font.bold(), module, role, colour)
        i = next((i for i, v in self.texts.items() if v == key), None)
        if i is None:
            i = len(self.texts) + 1
            assert i < 250, "more texts than tag colours"
            self.texts[i] = key
        return f"#ff{i:02x}fe"

    def __enter__(self):
        def draw(p, pos, text, font, colour, **kw):
            fr = sys._getframe(1)
            tag = self._tag(text, font, colour, fr.f_globals["__name__"], fr.f_code.co_name,
                            kw.get("halo", 2.2))
            return self._draw(p, pos, text, font, tag, **kw)

        def outl(p, rect, flags, text, font, colour, halo=2.2):
            role = "readout" if font.bold() else "caption"
            return self._outl(p, rect, flags, text, font,
                              self._tag(text, font, colour, "studio.gmeter_overlay", role), halo)
        ev._draw_text, self.gm._draw_text_outlined = draw, outl
        return self

    def __exit__(self, *_a):
        ev._draw_text, self.gm._draw_text_outlined = self._draw, self._outl


def _text_contrasts(a: np.ndarray, b: np.ndarray, texts) -> dict:
    """{text key: (ring, far)} for every tagged text in render `b` (its twin `a` untagged): two
    WCAG contrasts of the glyph's fill. RING: against the mean luminance of every pixel within
    0.12 em (at least 2 px) of a pixel the fill touched, so a halo or a plate counts exactly as far
    as it reaches. FAR: against the pixels 2-4 px away, mostly past a thin halo."""
    diff = np.any(a != b, axis=2)
    tag = (b[..., 0] == 255) & (b[..., 2] == 254) & diff
    out = {}
    for i in np.unique(b[..., 1][tag]):
        key = texts.get(int(i))
        if key is None:
            continue
        core = tag & (b[..., 1] == i)
        r = max(2, round(0.12 * key[1]))
        ys, xs = np.nonzero(core)
        y0, y1 = max(0, ys.min() - r - 3), ys.max() + r + 4
        x0, x1 = max(0, xs.min() - r - 3), xs.max() + r + 4
        c, d = core[y0:y1, x0:x1], diff[y0:y1, x0:x1]
        glyph = c | (d & _grow(c, 2))
        ring = _grow(glyph, r) & ~d
        lg = float(np.median(_wcag_lum(a[y0:y1, x0:x1][c])))
        lb = float(_wcag_lum(a[y0:y1, x0:x1][ring]).mean())
        far = _grow(glyph, 4) & ~_grow(glyph, 1) & ~d
        lf = float(_wcag_lum(a[y0:y1, x0:x1][far]).mean())
        out[key] = (_contrast(lg, lb), _contrast(lg, lf))
    return out


def test_every_broadcast_text_reads_over_sky_grass_white_and_noise():
    """Every text a Broadcast frame burns — the tower, the Δ bar's scale and baseline, the big
    speed, the minimap's C-labels, and the Classic g readout and honesty stamp it keeps — reaches
    WCAG's 4.5:1 (3:1 for a bold face of 24 px or more, stricter than WCAG's large text) at 720p
    landscape and 9:16, by TWO metrics:
      * PLATE-ONLY, for every text on a plate (or the header's band): its fill against that
        backing blended over each flat background (white, sky, grass, grey, black), no halo
        counted — so a plate's legibility does not lean on the glyph's outline;
      * RING (`_text_contrasts`), for every text, rendered over six backgrounds (the five and
        per-pixel noise): the halo and the plate count as far as they reach. For the C-labels,
        which have no plate, this is the only measure held: their legibility rests on the
        outline. Their FAR contrast (the background 2-4 px off, past the outline) is printed,
        not held.
    Every text a frame draws must be measured, or be named in `_LEGIBILITY_EXEMPT` with its reason.
    Before the plates: the bar's ends measured 1.4:1 and the stamp 1.2:1 (ring) over white at
    720p."""
    f = _fixture()
    s, lap = f["s"], f["lap"]
    worst: dict = {}
    measured: set = set()
    far: dict = {}
    with _Stamped(), _Tagged() as tg:
        for w, h in ((1280, 720), (1080, 1920)):
            tg.on = False
            pa = _painter(s, _spec(s, lap), w, h)
            tg.on = True
            pb = _painter(s, _spec(s, lap), w, h)
            spec = pa._spec
            for frac in (0.3, 0.7):
                t = spec.lap_t0 + frac * (spec.lap_t1 - spec.lap_t0)
                for name in BACKGROUNDS:
                    renders = []
                    for on, p in ((False, pa), (True, pb)):
                        tg.on = on
                        vals = ev.overlay_values_at(s, t, spec)
                        raw = ev._paint_packed_frame(p, w, h, _background(name, w, h), vals,
                                                     p.advance_and_snapshot(vals))
                        renders.append(np.frombuffer(raw, np.uint8).reshape(h, w, 3))
                    for key, (cr, cf) in _text_contrasts(*renders, tg.texts).items():
                        measured.add(key)
                        if _exempt(key):
                            continue
                        need = 3.0 if key[1] >= 24 and key[2] else 4.5
                        role = (key[3], key[4])
                        what = "outlined" if role in _OUTLINED else "plated"
                        checks = [("ring", cr, name)]
                        if role not in _OUTLINED:
                            checks += [("plate-only", _plate_only(key, BACKGROUNDS[n]), n)
                                       for n in BACKGROUNDS if BACKGROUNDS[n] is not None]
                        else:
                            far[(w, h)] = min(far.get((w, h), (99.0, ""))[0], cf), key[0]
                        for metric, c, bg in checks:
                            k = (what, metric, w, h)
                            if k not in worst or c - need < worst[k][0] - worst[k][1]:
                                worst[k] = (c, need, bg, key[0], key[1])
    drawn = {k for k in tg.texts.values() if not _exempt(k)}
    unmeasured = sorted({(k[0], k[3], k[4]) for k in drawn - measured})
    assert not unmeasured, f"texts drawn but never measured (name them in _LEGIBILITY_EXEMPT " \
                           f"with a reason, or measure them): {unmeasured}"
    assert {(k[3], k[4]) for k in measured} & _OUTLINED, "no C-label measured"
    for (what, metric, w, h), (cr, need, name, text, px) in sorted(worst.items()):
        print(f"   {w}x{h} {what:8s} {metric:10s} worst {cr:5.2f}:1 (needs {need}) over {name}: "
              f"{text!r} {px} px")
    for (w, h), (cf, text) in sorted(far.items()):
        print(f"   {w}x{h} C-labels: fill vs the background 2-4 px off {cf:.2f}:1 ({text!r}) — "
              f"their legibility rests on the outline")
    bad = {k: v for k, v in worst.items() if v[0] < v[1]}
    assert not bad, f"texts below WCAG contrast: {bad}"
    print(f"ok legibility: every Broadcast text at 720p and 9:16 reaches its WCAG contrast "
          f"({len(measured)} texts measured; exempt by name: "
          f"{sorted(set(_LEGIBILITY_EXEMPT.values()))})")


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


def test_the_dialog_follows_the_style_and_shows_an_unavailable_extra_unticked():
    """QA #565: the description line follows the Style; the "Opened on your last export's…" note
    names a remembered non-default style; a ticked extra that cannot apply here is shown unticked
    AND disabled (it was a bright tick on a disabled box), its saved tick kept and restored."""
    f = _fixture()
    lap = f["lap"]
    data = prefs.load()
    for key in ("export_style_idx", "export_broadcast_ghost", "export_broadcast_corner_cards",
                "export_scope_idx", "export_res_idx"):
        data.pop(key, None)
    prefs.save(data)
    prefs.set("export_style_idx", 1)
    seen = {}

    def labels(dlg):
        return [w.text() for w in dlg.findChildren(QLabel)]

    def act(dlg, rows):
        texts = labels(dlg)
        seen["note"] = next((t for t in texts if t.startswith("Opened on your last")), None)
        seen["desc_b"] = next(t for t in texts if t.startswith("Burns the overlays"))
        ghost_box = rows["Extras"].findChildren(QCheckBox)[0]
        rows["Export"].setCurrentIndex(1)                # Best lap: no ghost of itself
        seen["best"] = (ghost_box.isChecked(), ghost_box.isEnabled())
        seen["hint"] = " ".join(t for t in labels(dlg) if "No ghost" in t)
        rows["Export"].setCurrentIndex(0)
        seen["back"] = (ghost_box.isChecked(), ghost_box.isEnabled())
        rows["Style"].setCurrentIndex(0)
        seen["desc_c"] = next(t for t in labels(dlg) if t.startswith("Burns the overlays"))
        rows["Style"].setCurrentIndex(1)
        rows["Export"].setCurrentIndex(1)
        return QDialog.Accepted

    choice = _open_dialog(lap, act)
    failed = [what for what, ok in (
        ("a disabled ghost shows a tick", seen["best"] == (False, False)),
        ("the note does not name the style", "style: Broadcast" in (seen["note"] or "")),
        ("the description lists the Classic plates in Broadcast",
         "tower" in seen["desc_b"] and "lap strip" not in seen["desc_b"]),
        ("the Classic description changed",
         "lap strip" in seen["desc_c"] and "tower" not in seen["desc_c"])) if not ok]
    assert not failed, f"{failed}: {seen}"
    assert "No ghost in this export" in seen["hint"], seen["hint"]
    assert seen["back"] == (True, True), f"the saved tick did not come back: {seen['back']}"
    assert "ghost" not in choice.config.elements, choice.config.elements
    assert prefs.get("export_broadcast_ghost") is True, "the saved tick was lost"
    print("ok the dialog: the description and the note follow the style; an unavailable extra "
          "reads unticked, and its tick comes back")


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
    for fn in (test_a_vertical_frame_keeps_the_classic_plates_out_of_the_apps_bands,
               test_every_frame_the_dialog_offers_lays_out_clear_and_safe,
               test_a_layout_that_would_overlap_fails_by_name,
               test_a_layout_that_cannot_fit_says_so_not_that_the_encoder_stopped,
               test_corner_cards_that_cannot_fit_sit_the_export_out_and_say_so,
               test_the_finished_box_says_when_the_corner_cards_did_not_fit,
               test_every_figure_is_its_classic_twin_on_every_frame,
               test_the_honesty_stamp_is_kept_at_its_classic_size_at_every_shape,
               test_every_broadcast_text_reads_over_sky_grass_white_and_noise,
               test_a_reference_names_the_baseline_in_the_tower_and_on_the_bar,
               test_a_lap_kept_out_of_the_bests_burns_neutral_and_says_not_counted,
               test_the_bar_scale_is_each_laps_own,
               test_a_whole_recording_tower_shows_each_finished_laps_table_time,
               test_the_last_line_names_its_baseline_and_stands_without_the_tower,
               test_outside_a_lap_the_tower_draws_nothing,
               test_classic_never_reaches_the_broadcast_element,
               test_the_style_row_turns_the_extras_on_in_broadcast_only,
               test_the_dialog_follows_the_style_and_shows_an_unavailable_extra_unticked,
               test_a_real_broadcast_export_through_the_dialog_plays_with_sound_to_the_end):
        fn()
    print("test_export_broadcast: all ok")


if __name__ == "__main__":
    _run_all()
