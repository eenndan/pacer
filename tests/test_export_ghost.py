"""The export's overlay-element seam (`studio/overlay/`) and its first element, the minimap ghost.

WHY A FILE OF ITS OWN. The seam is a new surface: an opt-in element is painted by the exporter
through a registry, so what has to hold is not one painter's pixels but a contract — the Classic
export is byte-identical while no element is asked for, an element is prepared once and painted
once per frame, a failing one fails the export by name, and the ghost obeys its rules. None of that
belongs to an existing file's subject (test_export_video pins the Classic painter itself).

THE FIXTURE is the demo's telemetry (`make_demo`'s seed, laps, origin and habit — a fictional
circuit, verified timing on its built-in track, no personal data) under a flat 960x540 picture, so
a real render's map inset is big enough to read and the only cyan in a frame is the ghost's.
Renders go through the real `Renderer` with the real ffmpeg decode and an encoder stand-in that
keeps each painted frame as the render hands it over (before any encoding); the encoder is pinned
to libx264 with a software decode — the path CI and a Mac without VideoToolbox take.

WHAT IS CHECKED:
  * the ghost's ring is where `lap_race.race_state` puts the best lap at the frame's lap time,
    mapped through the minimap's own projection, and its gap IS the strip's Δ on every frame;
  * it is drawn UNDER the kart dot (where they overlap, the dot's pixels are untouched);
  * BESIDE the kart (a lap close to the best: QA #556) it is a ring ROUND the dot that reads —
    enough cyan round the dot on every such frame — and clear of the dot it is today's ring,
    pixel for pixel;
  * ZERO ghost pixels on the best lap, with a cross-recording reference loaded, under provisional
    timing, with a lap the Lap Race leaves out (dropout), in the full-session scope and with the
    option off — each beside the positive control that the same mask finds the ring otherwise;
  * a real render with the ghost differs from the Classic one only inside the minimap, and the
    Classic render is byte-identical whether the element is absent or present-but-silent;
  * the registry: `prepare` once per export, `paint` once per frame; a raising element fails the
    export with its name, not retried and not silent;
  * the dialog: an "Extras" checkbox, off by default, remembered, disabled with its reason where
    the rules forbid a ghost, and a hint line when a remembered tick cannot apply.

Run: python tests/test_export_ghost.py
"""
import math
import os
import subprocess
import sys
import tempfile
import threading
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

from studio.dev._jail import divert_app_support  # noqa: E402

_JAIL = divert_app_support("pacer-test-export-ghost-")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QLabel,
    QPushButton,
)

_APP = QApplication.instance() or QApplication([])

from studio import chapters, lap_race, overlay, prefs, theme  # noqa: E402
from studio import export_video as ev  # noqa: E402
from studio.dev import make_demo as md  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.export_palette import EXPORT  # noqa: E402
from studio.overlay import ghost  # noqa: E402
from studio.session import Session  # noqa: E402

PIC_W, PIC_H = 960, 540
_FIX: dict = {}


def _flat_picture(path, truth, first_payload, n_payloads, ffmpeg):
    """`synth_gopro.generate(video=)`: a flat dark picture, 29.97 fps, big enough to read a map."""
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
    tmp = tempfile.TemporaryDirectory(prefix="pacer-export-ghost-")
    rec = sg.generate(os.path.join(tmp.name, "rec"), md.DEMO_SEED, md.DEMO_LAPS, chapters=1,
                      origin=md.DEMO_ORIGIN, video=_flat_picture, habit=md.DEMO_HABIT)
    s = Session.load(chapters.discover_siblings(rec.paths[0]))
    assert s.timing_verified, "the demo circuit's timing is not verified on its built-in track"
    best = s.best_lap_id()
    others = [i for i in s.valid_lap_ids() if i != best]
    # The clean lap furthest from the best: the widest ghost gap the fixture offers.
    lap = max(others, key=lambda i: s.lap_time(i) - s.lap_time(best))
    _FIX.update(tmp=tmp, src=rec.paths[0], s=s, best=best, lap=lap,
                out=os.path.join(tmp.name, "never-written.mp4"))
    return _FIX


# ------------------------------------------------------------------------------ painter-level
def _cfg(**kw):
    return ev.OverlayConfig(encoder="libx264", hwaccel_decode=False, **kw)


def _spec(s, lap, **kw):
    return ev.build_lap_spec(s, _fixture()["out"], lap, config=_cfg(**kw))


def _painted(painter, s, spec, t, w=1920, h=1080):
    """One overlay-only frame at media time t from the REAL painter, as an int RGBA array."""
    vals = ev.overlay_values_at(s, float(t), spec)
    dial = painter.advance_and_snapshot(vals)
    raw = ev._paint_alpha_frame(painter, w, h, vals, dial)
    return vals, np.frombuffer(raw, np.uint8).reshape(h, w, 4).astype(int)


def _cyan(rgb, alpha=None):
    """The ghost's ring by colour: the compare cyan (#5BC8E0), and nothing else the overlay paints
    — the white line has red, the coral dot and amber tail have no blue, the colour-blind Δ blue
    (#2E90FF) too little green."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    m = (b > 170) & (g > 150) & (r < 150) & (b - r > 60)
    return m if alpha is None else m & (alpha > 200)


def _ghost_px(painter, el, vals):
    st = el.state(vals)
    if st is None:
        return None
    row = st.ids.index(_fixture()["best"])
    q = painter._map._proj(float(st.x[row]), float(st.y[row]))
    return np.array([q.x(), q.y()])


def _times(spec, n):
    """`n` media times spread over the lap's own window (not on its edges)."""
    return np.linspace(spec.lap_t0 + 0.5, spec.lap_t1 - 0.5, n)


def test_the_ghost_is_where_the_best_lap_was_and_its_gap_is_the_strips_delta():
    f = _fixture()
    s, lap = f["s"], f["lap"]
    spec = _spec(s, lap, elements=("ghost",))
    painter = ev.OverlayPainter(s, spec, 1920, 1080, 30.0)
    assert [e.name for e in painter.elements] == ["ghost"], painter.elements
    el = painter.elements[0]
    # 1. The gap IS the strip's Δ, on every frame of the lap (the same on-track maths).
    worst, n = 0.0, 0
    for t in ev.frame_times(spec.t0, spec.t1, 30.0):
        vals = ev.overlay_values_at(s, float(t), spec)
        st = el.state(vals)
        if st is None or vals.delta_s is None:
            continue
        worst = max(worst, abs(lap_race.interval_to_best(st, lap) - vals.delta_s))
        n += 1
    assert n > 1000 and worst < 1e-9, f"ghost gap vs the strip's Δ: max {worst:.3g} s over {n}"
    # 2. The ring is centred where race_state puts the best lap, through the map's projection.
    checked = 0
    for t in _times(spec, 40):
        vals, img = _painted(painter, s, spec, t)
        want = _ghost_px(painter, el, vals)
        kart, tail = painter._map.marker_and_tail(vals.tt)
        clear_of = [np.array([kart.x(), kart.y()])] + [np.array([q.x(), q.y()]) for q in tail]
        if min(np.hypot(*(want - c)) for c in clear_of) < 16.0:
            continue        # the kart's dot or tail covers part of the ring here: judged below
        ys, xs = np.nonzero(_cyan(img[..., :3], img[..., 3]))
        d = np.hypot(xs - want[0], ys - want[1])
        near = d < 10.0
        assert near.sum() > 30, f"t={t:.2f}: {near.sum()} ring pixels at the expected place {want}"
        assert not (~near).any(), f"t={t:.2f}: {int((~near).sum())} cyan pixels away from the ghost"
        got = np.array([xs[near].mean(), ys[near].mean()])
        assert np.hypot(*(got - want)) < 1.0, f"t={t:.2f}: ring centred {got}, race_state {want}"
        checked += 1
    assert checked >= 10, f"only {checked} frames had the ring clear of the kart"
    spec.source.cleanup()
    print(f"ok the ghost: gap == strip Δ to {worst:.1e} s over {n} frames; ring placed on {checked}")


def test_the_ghost_is_drawn_under_the_kart_dot():
    """Where ring and dot overlap (on the start line, both laps at lap time 0) the dot's own
    pixels are exactly what they are without the ghost."""
    f = _fixture()
    s, lap = f["s"], f["lap"]
    on = _spec(s, lap, elements=("ghost",))
    off = _spec(s, lap)
    p_on = ev.OverlayPainter(s, on, 1920, 1080, 30.0)
    p_off = ev.OverlayPainter(s, off, 1920, 1080, 30.0)
    el = p_on.elements[0]
    seen = 0
    for t in ev.frame_times(on.t0, on.t0 + 1.5, 30.0):
        vals, a = _painted(p_on, s, on, t)
        _vals, b = _painted(p_off, s, off, t)
        kart = p_on._map.marker_and_tail(vals.tt)[0]
        want = _ghost_px(p_on, el, vals)
        if want is None or np.hypot(want[0] - kart.x(), want[1] - kart.y()) > 3.0:
            continue
        # The dot's opaque core (radius 4.6 px at 1080p): every pixel centre within 3.5 px of it.
        yy, xx = np.mgrid[0:a.shape[0], 0:a.shape[1]]
        core = np.hypot(xx + 0.5 - kart.x(), yy + 0.5 - kart.y()) <= 3.5
        assert (a[core] == b[core]).all(), f"t={t:.3f}: the ghost painted over the kart dot"
        assert (a != b).any(), f"t={t:.3f}: no ghost at all to be under the dot"
        seen += 1
    assert seen >= 3, f"only {seen} frames with the ring under the dot"
    on.source.cleanup()
    off.source.cleanup()
    print(f"ok the ghost is under the kart dot ({seen} overlapping frames)")


def test_beside_the_kart_the_ghost_is_a_ring_round_the_dot():
    """On the clean lap closest to the best the ghost sits within a dot radius of the kart for
    most of the lap; drawn under the dot it vanished there (QA #556). Now every such frame shows a
    cyan ring round the dot (at least half a ring's ink), still centred on the best lap's place,
    the dot's own disc untouched; and a frame where the two are clear of each other is exactly the
    ring it always was."""
    from studio.overlay import ghost
    f = _fixture()
    s, best = f["s"], f["best"]
    close = min((i for i in s.valid_lap_ids() if i != best),
                key=lambda i: s.lap_time(i) - s.lap_time(best))
    # (read with defaults, so the unfixed tree fails on the pixels rather than on a name)
    near_r, ring_r = getattr(ghost, "_NEAR_R", 7.2), getattr(ghost, "_NEAR_RING_R", 9.0)
    ring_px = 2 * math.pi * ring_r * ghost._RING_W     # k = 1 at 1080

    def around(c, r=21):
        """The pixels within `r` of point `c`, as (rows, cols, ys, xs): every mask below is a
        disc of radius 20 or less, so cutting the frame to this square reads exactly the pixels
        the whole-frame grids did, at a 2,000th of the cost (the grids were half this test)."""
        y0, x0 = max(0, int(c[1]) - r), max(0, int(c[0]) - r)
        rows = slice(y0, min(1080, int(c[1]) + r + 1))
        cols = slice(x0, min(1920, int(c[0]) + r + 1))
        ys, xs = np.mgrid[rows, cols]
        return rows, cols, ys, xs
    count = {}
    for lap in (close, f["lap"]):          # the closest lap, and the fixture's slowest
        spec = _spec(s, lap, elements=("ghost",))
        off_spec = _spec(s, lap)
        p = ev.OverlayPainter(s, spec, 1920, 1080, 30.0)
        off = ev.OverlayPainter(s, off_spec, 1920, 1080, 30.0)
        el = p.elements[0]
        near = far = fitted = 0
        for t in _times(spec, 40 if lap != close else 80):
            vals, a = _painted(p, s, spec, t)
            kart = p._map.marker_and_tail(vals.tt)[0]
            want = _ghost_px(p, el, vals)
            d = float(np.hypot(want[0] - kart.x(), want[1] - kart.y()))
            if 1.5 < d < near_r * el._k:
                # The ring is round the BEST LAP'S place, not round the dot: a circle fitted to
                # its cyan pixels (a fit, not a centroid — the arc behind the dot is cut away)
                # is centred on the ghost within half a pixel, and not on the dot.
                rows, cols, yy, xx = around(want)
                w_ = a[rows, cols]
                m = _cyan(w_[..., :3], w_[..., 3]) & (np.hypot(xx + 0.5 - want[0],
                                                              yy + 0.5 - want[1]) < 20)
                px, py = xx[m] + 0.5, yy[m] + 0.5
                sol = np.linalg.lstsq(np.c_[2 * px, 2 * py, np.ones(len(px))],
                                      px ** 2 + py ** 2, rcond=None)[0]
                miss = float(np.hypot(sol[0] - want[0], sol[1] - want[1]))
                assert miss < 0.5 and np.hypot(sol[0] - kart.x(), sol[1] - kart.y()) > 1.0, (
                    f"t={t:.3f}: the ring is centred {miss:.2f} px from the ghost, gap {d:.2f} px "
                    f"(ring at {sol[0]:.1f},{sol[1]:.1f}; dot {kart.x():.1f},{kart.y():.1f})")
                fitted += 1
            if d < 3.0:
                rows, cols, yy, xx = around((kart.x(), kart.y()))
                w_ = a[rows, cols]
                r = np.hypot(xx + 0.5 - kart.x(), yy + 0.5 - kart.y())
                ink = int((_cyan(w_[..., :3], w_[..., 3]) & (r < 20)).sum())
                assert ink >= 0.5 * ring_px, (f"t={t:.3f}: {ink} cyan px round the dot (a ring "
                                              f"is {ring_px:.0f}): the ghost is hidden under the "
                                              "kart")
                _vals, b = _painted(off, s, off_spec, t)
                core = r <= 3.5
                assert (w_[core] == b[rows, cols][core]).all(), \
                    f"t={t:.3f}: the ring covered the kart dot"
                near += 1
            elif d > (near_r + ring_r + 3) * el._k:
                ghost._NEAR_R = 0.0              # never "beside": the ring as it always was
                try:
                    _vals, b = _painted(p, s, spec, t)
                finally:
                    ghost._NEAR_R = near_r
                mb = p._map._box          # (the g-dial's filter moved on: the minimap only)
                y0, y1 = int(mb.top()), int(mb.bottom()) + 1
                x0, x1 = int(mb.left()), int(mb.right()) + 1
                assert (a[y0:y1, x0:x1] == b[y0:y1, x0:x1]).all(), \
                    f"t={t:.3f}: the ring clear of the dot changed"
                far += 1
        count[lap] = (near, far)
        if lap == close:
            assert fitted >= 10, f"only {fitted} frames to fit the ring's centre on"
        spec.source.cleanup()
        off_spec.source.cleanup()
    assert count[close][0] >= 40, f"only {count[close]} frames beside the kart on lap {close + 1}"
    assert count[f["lap"]][1] >= 12, f"only {count[f['lap']]} clear frames on lap {f['lap'] + 1}"
    print(f"ok beside the kart the ghost is a ring round the dot: (beside, clear) frames of 40 "
          f"{ {k + 1: v for k, v in count.items()} }, the clear ones unchanged")


def _ghost_pixels(s, lap, n=6, follow=False, **kw):
    """Cyan pixels over `n` frames of `lap`'s export (painter-level, 1080p, overlay-only)."""
    if follow:
        spec = ev.build_session_spec(s, _fixture()["out"], config=_cfg(**kw))
        times = np.linspace(spec.t0 + 60.0, spec.t0 + 200.0, n)
    else:
        spec = _spec(s, lap, **kw)
        times = _times(spec, n)
    painter = ev.OverlayPainter(s, spec, 1920, 1080, 30.0)
    total = sum(int(_cyan(img[..., :3], img[..., 3]).sum())
                for _v, img in (_painted(painter, s, spec, t) for t in times))
    spec.source.cleanup()
    return total, painter


class _Patched:
    """Set attributes on an object (or class) for a block, and put them back."""

    def __init__(self, obj, **attrs):
        self.obj, self.attrs, self.saved = obj, attrs, {}

    def __enter__(self):
        for k, v in self.attrs.items():
            self.saved[k] = self.obj.__dict__.get(k, _MISSING)
            setattr(self.obj, k, v)

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is _MISSING:
                delattr(self.obj, k)
            else:
                setattr(self.obj, k, v)


_MISSING = object()


def test_no_ghost_where_the_rules_forbid_one():
    f = _fixture()
    s, lap, best = f["s"], f["lap"], f["best"]
    # The render thread reads its own two-lap field and never the app's default one.
    asked = []
    real_race = s.race_tracks
    with _Patched(s, race_tracks=lambda ids=None: (asked.append(ids), real_race(ids))[1]):
        positive, _p = _ghost_pixels(s, lap, elements=("ghost",))
    assert asked and None not in asked, f"the export built the default Lap Race field: {asked}"
    assert positive > 6 * 30, f"the positive control found only {positive} ghost pixels"
    cases = {}
    cases["the best lap itself"] = _ghost_pixels(s, best, elements=("ghost",))
    cases["the option off"] = _ghost_pixels(s, lap)
    with _Patched(s, has_reference=lambda: True):
        cases["a cross-recording reference"] = _ghost_pixels(s, lap, elements=("ghost",))
    with _Patched(Session, timing_verified=property(lambda _self: False)):
        cases["provisional timing"] = _ghost_pixels(s, lap, elements=("ghost",))
    s._race_memo = None
    with _Patched(s, lap_has_dropout=lambda i: i == lap):
        cases["a lap the Lap Race leaves out (dropout)"] = _ghost_pixels(s, lap, elements=("ghost",))
        why = ghost.unavailable_reason(s, lap)
        assert why is not None and "GPS dropout" in why, why
    s._race_memo = None
    cases["the full-session scope"] = _ghost_pixels(s, None, follow=True, elements=("ghost",))
    for what, (count, painter) in cases.items():
        assert count == 0, f"{what}: {count} ghost pixels"
        assert painter.elements == [], f"{what}: the element did not sit the export out"
    assert ghost.unavailable_reason(s, lap) is None
    assert "itself" in ghost.unavailable_reason(s, best)
    print(f"ok no ghost in {len(cases)} forbidden cases (positive control: {positive} px)")


# Machado et al. (2009) deuteranopia, severity 1, on linear RGB: what the colour-blind palette's
# user sees. The kart dot, tail and line do not change with the palette (only the Δ hues do), so
# "distinct in both palettes" means distinct to both kinds of eye.
_DEUTAN = np.array([[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413],
                    [-0.011820, 0.042940, 0.968881]])


def _seen(hex_, deutan):
    q = theme.qcolor(hex_)
    c = np.array([q.red(), q.green(), q.blue()], float) / 255
    if deutan:
        lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
        c = np.clip(_DEUTAN @ lin, 0, 1)
        c = np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)
    return c * 255


def test_the_ghost_colour_is_its_own_in_both_palettes():
    """At least 100 (RGB) from the kart dot, its tail, the line and the accent, to a typical eye and
    to a deuteranope's; and clear (≥ 60) of both palettes' Δ hues, which it never shares the map with."""
    near = {"kart dot": EXPORT.marker, "tail": EXPORT.accent_bright, "line": EXPORT.text,
            "accent": EXPORT.accent}
    far = {}
    for pal in (theme.PALETTE_STANDARD, theme.PALETTE_COLORBLIND):
        far[f"{pal} ahead"], far[f"{pal} behind"] = ev.export_semantic_pair(pal)
    for deutan in (False, True):
        g = _seen(ghost.GHOST_COLOR, deutan)
        for floor, colours in ((100, near), (60, far)):
            for name, c in colours.items():
                d = np.linalg.norm(g - _seen(c, deutan))
                assert d > floor, f"the ghost is {d:.0f} from the {name} {c} (deutan={deutan})"
    print(f"ok the ghost colour stands apart from {len(near) + len(far)} colours, both eyes")


# ------------------------------------------------------------------------------ real renders
class _KeepingEncoder:
    """Stands in for the encoding ffmpeg: keeps every frame exactly as the render hands it over."""

    def __init__(self, cmd):
        head = cmd[:cmd.index("pipe:0")]
        self.w, self.h = map(int, head[head.index("-s") + 1].split("x"))
        self.bpp = 3 if head[head.index("-pix_fmt") + 1] == "rgb24" else 4
        self.frames, self._buf = [], bytearray()
        self.stdin, self.stderr, self.returncode, self.pid = self, None, None, -1

    def write(self, b):
        self._buf += memoryview(b).cast("B")
        fb = self.w * self.h * self.bpp
        while len(self._buf) >= fb:
            self.frames.append(bytes(self._buf[:fb]))
            del self._buf[:fb]
        return len(b)

    def flush(self):
        pass

    def close(self):
        pass

    def wait(self, timeout=None):
        self.returncode = self.returncode or 0
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        pass


def _render(spec, s):
    """Render `spec` for real (decode included) into the keeping encoder: (frames, renderer, err)."""
    real_popen = ev.subprocess.Popen
    encoders = []

    def popen(cmd, *a, **k):
        if "pipe:0" in cmd:
            encoders.append(_KeepingEncoder(cmd))
            return encoders[-1]
        return real_popen(cmd, *a, **k)

    r = err = None
    ev.subprocess.Popen = popen
    try:
        r = ev.Renderer(s, spec)      # builds the painter: where an element is prepared, as in
        r.run()                       # VideoExportWorker.run, inside the same guard
    except Exception as exc:  # noqa: BLE001 — the caller asserts on it
        err = exc
    finally:
        ev.subprocess.Popen = real_popen
    enc = encoders[-1] if encoders else None
    return (enc.frames if enc else []), r, err, enc, len(encoders)


def _short(s, lap, seconds=3.0, **kw):
    base = ev.build_lap_spec(s, _fixture()["out"], lap, config=_cfg(out_height=PIC_H, **kw),
                             lead_in=1.0)
    return replace(base, t1=base.t0 + seconds)


def _late(s, lap, **kw):
    """Six seconds near the END of `lap`, where its gap to the best has grown: the window starts
    mid-lap (lead_in 0, so every frame is inside the lap) and stops a second before the line."""
    base = ev.build_lap_spec(s, _fixture()["out"], lap, config=_cfg(out_height=PIC_H, **kw))
    end = base.lap_t1
    return replace(base, t0=end - 7.0, t1=end - 1.0, ends_on_finish=False)


def _as_img(frame, enc):
    return np.frombuffer(frame, np.uint8).reshape(enc.h, enc.w, enc.bpp).astype(int)


def _pair(make, s, lap):
    """The same window rendered with and without the ghost: (on, off, renderer_on, encoder)."""
    spec = make(s, lap, elements=("ghost",))
    on, r_on, err, enc, _n = _render(spec, s)
    assert err is None, err
    off, _r, err, _enc, _n = _render(make(s, lap), s)
    assert err is None, err
    assert len(on) == len(off) == r_on.total_frames > 60, (len(on), len(off))
    return on, off, r_on, enc, spec


def test_a_real_render_with_the_ghost_differs_only_inside_the_minimap():
    f = _fixture()
    s, lap = f["s"], f["lap"]
    # The run-up: identical to the Classic render up to the start line.
    on, off, r_on, enc, _spec = _pair(_short, s, lap)
    lead = int(round(1.0 * r_on.fps))
    for i in range(lead - 1):
        assert on[i] == off[i], f"frame {i} is in the run-up and differs from the Classic render"
    # Late in the lap: the ring where race_state puts the best lap, and nothing outside the map.
    on, off, r_on, enc, spec = _pair(_late, s, lap)
    painter = r_on._painter
    el = painter.elements[0]
    box = painter._map._box
    x0, y0 = int(box.x()) - 2, int(box.y()) - 2
    x1, y1 = int(math.ceil(box.right())) + 2, int(math.ceil(box.bottom())) + 2
    with_ghost = 0
    for i, (a, b) in enumerate(zip(on, off, strict=True)):
        ia, ib = _as_img(a, enc), _as_img(b, enc)
        diff = np.argwhere((ia != ib).any(axis=2))
        assert len(diff), f"frame {i}: no ghost drawn inside the lap"
        assert (diff[:, 0] >= y0).all() and (diff[:, 0] < y1).all() and \
            (diff[:, 1] >= x0).all() and (diff[:, 1] < x1).all(), \
            f"frame {i}: the ghost changed pixels outside the minimap"
        vals = ev.overlay_values_at(s, float(r_on._times[i]), spec)
        want = _ghost_px(painter, el, vals)
        kart = painter._map.marker_and_tail(vals.tt)[0]
        if np.hypot(want[0] - kart.x(), want[1] - kart.y()) <= 8.0:
            continue        # under the kart dot: judged by the painter-level test
        ys, xs = np.nonzero(_cyan(ia))
        assert len(xs), f"frame {i}: no ghost in a real render of the lap"
        assert np.hypot(xs - want[0], ys - want[1]).max() < 6.0, \
            f"frame {i}: ghost pixels away from where race_state puts the best lap"
        with_ghost += 1
    assert with_ghost > 60, with_ghost
    print(f"ok a real render: the ghost placed on {with_ghost} frames, nothing outside the map, "
          "run-up identical")


def test_the_classic_export_is_byte_identical_without_an_element():
    """Option off (no element named) and an element that sits the export out (the ghost on the
    best lap) produce the same bytes, composite and overlay-only — and neither reaches the
    package's per-frame painting at all. The cross-tree proof (this branch == main) is in the PR."""
    f = _fixture()
    s, best = f["s"], f["best"]
    calls = []
    real_paint = overlay.paint_layer
    overlay.paint_layer = lambda *a, **k: (calls.append(1), real_paint(*a, **k))[1]
    try:
        for overlay_only in (False, True):
            got = []
            for elements in ((), ("ghost",)):
                frames, r, err, _enc, _n = _render(
                    _short(s, best, elements=elements, overlay_only=overlay_only,
                           alpha_codec=ev.ALPHA_PRORES), s)
                assert err is None, err
                assert r._painter.elements == []
                got.append(frames)
            assert len(got[0]) == len(got[1]) > 60
            diff = [i for i, (a, b) in enumerate(zip(*got, strict=True)) if a != b]
            assert not diff, f"overlay_only={overlay_only}: frames differ at {diff[:10]}"
    finally:
        overlay.paint_layer = real_paint
    assert calls == [], f"the overlay package painted {len(calls)} times with no element"
    print("ok the Classic export is byte-identical with no element, composite and overlay-only")


class _Counting:
    name, layer = "test-counting", overlay.LAYER_TOP
    prepared = painted = 0

    def prepare(self, ctx):
        type(self).prepared += 1
        return True

    def paint(self, p, vals):
        type(self).painted += 1


class _Boom:
    name, layer = "test-boom", overlay.LAYER_MAP

    def prepare(self, ctx):
        self.n = 0
        return True

    def paint(self, p, vals):
        self.n += 1
        if self.n == 5:
            raise ValueError("planted")


class _BoomAtPrepare:
    name, layer = "test-boom-prepare", overlay.LAYER_TOP

    def prepare(self, ctx):
        raise KeyError("planted")


def test_the_registry_prepares_once_paints_once_per_frame_and_fails_loudly():
    f = _fixture()
    s, lap = f["s"], f["lap"]
    added = {c.name: c for c in (_Counting, _Boom, _BoomAtPrepare)}
    overlay.REGISTRY.update(added)
    try:
        frames, r, err, _enc, n_enc = _render(_short(s, lap, elements=(_Counting.name,)), s)
        assert err is None, err
        assert _Counting.prepared == 1, f"prepare ran {_Counting.prepared} times"
        assert _Counting.painted == len(frames) == r.total_frames, (_Counting.painted, len(frames))
        for name, where in ((_Boom.name, "paint the frame"), (_BoomAtPrepare.name, "prepare")):
            frames, r, err, _enc, n_enc = _render(_short(s, lap, elements=(name,)), s)
            assert isinstance(err, overlay.OverlayElementError), repr(err)
            assert name in str(err) and where in str(err) and "planted" in str(err), str(err)
            assert n_enc <= 1, f"{name}: the render was retried ({n_enc} encoders) on a paint defect"
            left = [t.name for t in threading.enumerate()
                    if t.is_alive() and t.name in ("export-read", "export-write")]
            assert not left, f"{name}: threads left running {left}"
        with_unknown = _render(_short(s, lap, elements=("no-such-element",)), s)[2]
        assert isinstance(with_unknown, overlay.OverlayElementError), repr(with_unknown)
    finally:
        for name in added:
            overlay.REGISTRY.pop(name, None)
    print("ok the registry: prepare x1, paint x frames; a raising element fails the export by name")


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
    """Open the real options dialog for `lap`; `act(dlg, rows)` drives it and returns the button."""
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


def _ghost_box(rows):
    """The ghost's checkbox in the Extras row (the row holds every opt-in element's box)."""
    return next(b for b in rows["Extras"].findChildren(QCheckBox)
                if b.text() == "Ghost of your best lap")


def _hint(dlg):
    return " ".join(lab.text() for lab in dlg.findChildren(QLabel) if lab.property("role") == "Hint")


def test_the_dialog_offers_the_ghost_off_by_default_and_says_where_it_cannot_apply():
    f = _fixture()
    lap = f["lap"]
    key = "export_ghost"
    data = prefs.load()
    data.pop(key, None)
    for k in ("export_scope_idx", "export_res_idx"):
        data.pop(k, None)
    prefs.save(data)
    seen = {}

    def first(dlg, rows):
        box, scope = _ghost_box(rows), rows["Export"]
        assert isinstance(box, QCheckBox) and isinstance(scope, QComboBox)
        seen["default"] = box.isChecked()
        seen["enabled"] = box.isEnabled()
        for i in range(scope.count()):
            scope.setCurrentIndex(i)
            seen[scope.itemText(i)] = (box.isEnabled(), box.toolTip())
        scope.setCurrentIndex(0)
        box.setChecked(True)
        return QDialog.Accepted

    choice = _open_dialog(lap, first)
    assert seen["default"] is False, "the ghost must open OFF on a first export"
    assert seen["enabled"] is True, "the ghost is disabled on a lap that can have one"
    assert seen["This lap"][0] and "best lap" in seen["This lap"][1]
    assert not seen["Best lap"][0] and "itself" in seen["Best lap"][1], seen["Best lap"]
    assert seen["All laps — one file per lap"][0]
    assert not seen["Full session — the whole recording"][0]
    assert choice.config.elements == ("ghost",), choice.config.elements
    assert prefs.get(key) is True, "the tick was not remembered"
    # Reopened: the tick is back; on the best lap's scope it is kept but cannot apply, said so.
    again = {}

    def second(dlg, rows):
        again["checked"] = _ghost_box(rows).isChecked()
        rows["Export"].setCurrentIndex(2)        # All laps: the best lap's own file has none
        again["all"] = _hint(dlg)
        rows["Export"].setCurrentIndex(1)        # Best lap
        again["hint"] = _hint(dlg)
        return QDialog.Accepted

    choice = _open_dialog(lap, second)
    assert again["checked"] is True
    n = len(f["s"].valid_lap_ids())
    best_name = f"Lap {f['best'] + 1} is your best lap"
    assert f"No ghost in 1 file of {n}:" in again["all"] and best_name in again["all"], again["all"]
    assert "No ghost in this export" in again["hint"], again["hint"]
    assert choice.config.elements == (), "a disabled ghost still reached the render"
    # Use defaults puts it back off — only offered when the dialog opens on Source.
    prefs.set("export_res_idx", 3)

    def third(dlg, rows):
        reset = [b for b in dlg.findChildren(QPushButton) if b.text() == "Use defaults"]
        assert reset, "no Use defaults button"
        reset[0].click()
        again["after_reset"] = _ghost_box(rows).isChecked()
        return QDialog.Rejected

    _open_dialog(lap, third)
    assert again["after_reset"] is False
    print("ok the dialog: Extras ▸ ghost off by default, remembered, disabled with its reason")


def _past_finish(s, lap, **kw):
    """The end of `lap` after the best lap's own lap time, then 2 s of its run-off (lead_in 0, so
    every frame before the line is inside the lap; `lead_out` keeps the finish where it is)."""
    base = ev.build_lap_spec(s, _fixture()["out"], lap, config=_cfg(out_height=PIC_H, **kw),
                             lead_out=2.0)
    late = min(6.0, s.lap_time(lap) - s.lap_time(_fixture()["best"]) - 0.5)
    return replace(base, t0=base.lap_t1 - late)


def test_the_ring_parks_on_the_finish_and_goes_when_the_lap_ends():
    """Once the best lap has finished, the ring PARKS on its finish (race_state holds a finished
    lap there, as the in-app Lap Race parks its dots); when the exported lap crosses the line the
    ring goes — the run-off frames are the Classic ones, byte for byte."""
    f = _fixture()
    s, lap, best = f["s"], f["lap"], f["best"]
    assert s.lap_time(lap) - s.lap_time(best) > 3.0, "the fixture's laps are too close to park"
    on, off, r_on, enc, spec = _pair(_past_finish, s, lap)
    painter = r_on._painter
    el = painter.elements[0]
    # Where the best lap ends: its position at any lap time past its own lap time.
    end = lap_race.race_state(el._tracks, float(s.lap_time(best)) + 10.0)
    row = end.ids.index(best)
    q = painter._map._proj(float(end.x[row]), float(end.y[row]))
    park = np.array([q.x(), q.y()])
    inside = after = 0
    for i, (a, b) in enumerate(zip(on, off, strict=True)):
        t = float(r_on._times[i])
        ia = _as_img(a, enc)
        if t >= spec.lap_t1:
            assert a == b, f"frame {i} ({t - spec.lap_t1:+.3f} s past the line) still has a ghost"
            assert not _cyan(ia).any()
            after += 1
        elif t < spec.lap_t1 - 2 * ev.ON_LINE_S:
            vals = ev.overlay_values_at(s, t, spec)
            assert np.hypot(*(_ghost_px(painter, el, vals) - park)) < 1e-6, \
                f"frame {i}: the ring is not parked on the best lap's finish"
            kart = painter._map.marker_and_tail(vals.tt)[0]
            if np.hypot(kart.x() - park[0], kart.y() - park[1]) < 20.0:
                continue        # the kart arriving at the line covers the parked ring
            ys, xs = np.nonzero(_cyan(ia))
            assert len(xs) and np.hypot(xs - park[0], ys - park[1]).max() < 6.0, \
                f"frame {i}: no ring at the parked place {park}"
            inside += 1
    assert inside > 40 and after > 40, (inside, after)
    print(f"ok the ring parks on the finish ({inside} frames) and goes after the line ({after})")


class _Plates:
    """A fake style element: replaces the Classic lap strip and records the plates it was shown."""
    name, layer, replaces = "test-plates", overlay.LAYER_TOP, ("strip",)
    seen = None

    def prepare(self, ctx):
        type(self).seen = ctx
        return True

    def paint(self, p, vals):
        pass


class _Stamp:
    name, layer, replaces = "test-stamp", overlay.LAYER_TOP, ("stamp",)

    def prepare(self, ctx):
        return True

    def paint(self, p, vals):
        pass


def test_an_element_can_replace_a_classic_plate_and_reads_every_plates_rect():
    f = _fixture()
    s, lap = f["s"], f["lap"]
    overlay.REGISTRY.update({_Plates.name: _Plates, _Stamp.name: _Stamp})
    try:
        spec = _spec(s, lap, elements=(_Plates.name,))
        classic = _spec(s, lap)
        p_on = ev.OverlayPainter(s, spec, 1920, 1080, 30.0)
        p_off = ev.OverlayPainter(s, classic, 1920, 1080, 30.0)
        ctx = _Plates.seen
        # The rects are the painter's own, read-only, plus the safe area.
        want = {"gmeter": p_off._g_rect, "map": p_off._map._box, "readout": p_off._readout_rect,
                "strip": p_off._strip_rect, overlay.STAMP: p_off._stamp_rect}
        assert dict(ctx.plates) == want, (dict(ctx.plates), want)
        try:
            ctx.plates["strip"] = None
            raise AssertionError("the plates mapping is writable")
        except TypeError:
            pass
        m = classic.config.margin_frac * 1080
        assert ctx.safe.adjusted(-m, -m, m, m) == ev.QRectF(0, 0, 1920, 1080), ctx.safe
        # The replaced strip is not painted; every other pixel is the Classic frame's; and what
        # the Classic frame painted there lies inside the strip's rect.
        r = p_off._strip_rect
        for t in _times(spec, 4):
            _v, a = _painted(p_on, s, spec, t)
            _v, b = _painted(p_off, s, classic, t)
            diff = np.argwhere((a != b).any(axis=2))
            assert len(diff), f"t={t:.2f}: the replaced strip was still painted"
            assert (diff[:, 1] >= r.left() - 2).all() and (diff[:, 1] <= r.right() + 2).all() \
                and (diff[:, 0] >= r.top() - 2).all() and (diff[:, 0] <= r.bottom() + 2).all(), \
                f"t={t:.2f}: replacing the strip changed pixels outside its rect {r}"
            y0, y1 = int(r.top()) + 2, int(r.bottom()) - 2
            x0, x1 = int(r.left()) + 2, int(r.right()) - 2
            assert (a[y0:y1, x0:x1, 3] == 0).all(), "something painted where the strip was"
            assert (b[y0:y1, x0:x1, 3] > 0).mean() > 0.9, "the Classic strip does not fill its rect"
        # The honesty stamp is not a plate: no element may replace it.
        err = None
        try:
            ev.OverlayPainter(s, _spec(s, lap, elements=(_Stamp.name,)), 1920, 1080, 30.0)
        except overlay.OverlayElementError as exc:
            err = exc
        assert err is not None and "stamp" in str(err), repr(err)
        for sp in (spec, classic):
            sp.source.cleanup()
    finally:
        overlay.REGISTRY.pop(_Plates.name, None)
        overlay.REGISTRY.pop(_Stamp.name, None)
    print("ok an element replaces the strip (nothing else moves) and reads every plate's rect")


def _run_all():
    for fn in (test_the_ghost_is_where_the_best_lap_was_and_its_gap_is_the_strips_delta,
               test_the_ring_parks_on_the_finish_and_goes_when_the_lap_ends,
               test_an_element_can_replace_a_classic_plate_and_reads_every_plates_rect,
               test_the_ghost_is_drawn_under_the_kart_dot,
               test_beside_the_kart_the_ghost_is_a_ring_round_the_dot,
               test_no_ghost_where_the_rules_forbid_one,
               test_the_ghost_colour_is_its_own_in_both_palettes,
               test_a_real_render_with_the_ghost_differs_only_inside_the_minimap,
               test_the_classic_export_is_byte_identical_without_an_element,
               test_the_registry_prepares_once_paints_once_per_frame_and_fails_loudly,
               test_the_dialog_offers_the_ghost_off_by_default_and_says_where_it_cannot_apply):
        fn()
    print("test_export_ghost: all ok")


if __name__ == "__main__":
    _run_all()
