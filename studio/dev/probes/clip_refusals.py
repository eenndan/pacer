"""clip_refusals: four things a design pass proposed for the exported clip and the share card, each
measured on synthetic telemetry. Their verdicts are `studio/docs/refused-2026-09.md` §24, §25, §26
and §27, and `tests/test_measured_figures.py` re-runs `figures` and holds each section to it.

WHY. The Wave 3 design-and-measure pass for the export overlay (2026-11-15) refused four of its
candidates on numbers measured on the owner's footage, which cannot be published. This re-measures
each one where anyone can: the synthetic demo (`make_demo`'s telemetry, its planted C1 habit
included) and `synth_gopro` recordings at the truth matrix's GPS noise levels. Nothing here reads a
recording off disk.

MODES (the last line each prints is its JSON):
  figures   deterministic; what the test re-runs (~1 min):
            * predicted — the clip's "predicted lap time", reference lap time + the live Δ, against
              the lap's real time at 25 / 50 / 75 % of each clean lap's own time: median and 90th
              percentile of |predicted − actual| over the clean laps that are not the best. On the
              demo, and on `synth_gopro` recordings with no planted habit at GPS noise 2 (three
              seeds), whose lap-to-lap differences are spread over seven corners;
            * trace — the scrolling Δ-trace band's geometry: each clean lap's largest |Δ| (what a
              fixed-scale band must hold), the band's pixels per 0.1 s at 1080p, and where a band
              anchored as in landscape lands in a 9:16 frame against the overlay's own rectangles;
            * chips — sector chips coloured only beyond noise: every clean lap's split at the
              golden gate's two sector lines, against the best earlier clean lap in that sector,
              gated at 2√2·σ for the truth matrix's split σ at noise 2 and 4.5 (row 2), on its own
              recordings (3 seeds) and on the demo's telemetry re-generated at the same two noises;
            * cells — the share card's per-corner cells on the demo: the best lap's gap to each
              corner's typical (the CORNERS BY LAP median) against that corner's ▼ step, and its
              gap to the ideal lap per corner, whose largest is set against the card's own
              opportunity.
  paint     machine-dependent: the band's paint per frame at 1080p and 4K on the demo's best lap
            against the shipped overlay's, interleaved over ROUNDS.
  budget    machine-dependent, minutes: a real 4K export of one demo lap, as shipped and with a
            busy-wait of BUSY_MS added to every frame's paint, interleaved; the slope is what one
            more ms of paint costs an export, and BUDGET_SHARE of the shipped export is the budget.

ARGV CONTRACT: only the mode. Every recording is generated into ONE `mkdtemp` under $TMPDIR and
removed before exit; the app-support store is jailed; the sidecar write is stubbed. Output is stdout.

    pixi run python -m studio.dev.probes.clip_refusals figures
    pixi run python -m studio.dev.probes.clip_refusals paint
    pixi run python -m studio.dev.probes.clip_refusals budget
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")

from studio.dev._jail import divert_app_support  # noqa: E402

_JAIL = divert_app_support("clip_refusals_")

import numpy as np  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from studio import (
    sidecar,  # noqa: E402  (after the jail, as every load must be)
    theme,  # noqa: E402
)

_APP = QGuiApplication.instance() or QGuiApplication([])   # the overlay's fonts need one
theme.register_fonts()

sidecar.save = lambda *a, **k: None   # a line edit writes beside the recording: never here
from studio import chapters  # noqa: E402
from studio._signal import lap_label  # noqa: E402
from studio.dev import make_demo  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.session import Session  # noqa: E402
from studio.stats import DIFF_2SIGMA_K  # noqa: E402

# The truth matrix's grid (tests/test_truth_matrix.py SEEDS, SECTOR_STRAIGHTS): the test holds
# these equal to it, so the split σ the chips are gated on is measured on these very recordings.
SEEDS = (sg.DEFAULT_SEED, 1, 2)
SECTOR_STRAIGHTS = (2, 5)
CHIP_NOISES = (2.0, 4.5)
PREDICT_NOISE = 2.0               # recording C's GPS noise in the truth matrix's calibration
FRACTIONS = (0.25, 0.5, 0.75)
GATE_K = DIFF_2SIGMA_K             # 2σ of the difference of two splits, each with noise σ
# The Δ-trace band as the design pass drew it: bottom-centre, 0.50 × 0.085 of the short side, an
# inner inset of 8 × 6 px at 1080p; its scale is the lap's own largest |Δ| × 1.1, floored at 0.3 s.
BAND_W_U = 0.50
BAND_H_U = 0.085
BAND_INSET_Y = 6.0
BAND_HEADROOM = 1.1
BAND_FLOOR_S = 0.3
BAND_STROKE_PX = 2.6              # the trace's own line width at 1080p
FPS = 29.97
ROUNDS = 5
BUSY_MS = 10.0                    # large enough to stand clear of the machine's load
BUDGET_ROUNDS = 3
BUDGET_SHARE = 0.05               # the design pass's rule: no element may add 5 % to a 4K export


# ─── recordings ─────────────────────────────────────────────────────────────────────────────────
def _load(path: str) -> Session:
    return Session.load(chapters.discover_siblings(path))


def _demo(work: str, noise: float | None = None):
    """(session, truth) of the demo's telemetry: the published recipe, or the same seed, laps and
    habit at another GPS noise. Placeholder picture: nothing here decodes a frame."""
    out = os.path.join(work, f"demo_{noise}")
    if noise is None:
        truth = make_demo.make(out + ".mp4", render=False)
        return _load(out + ".mp4"), truth
    rec = sg.generate(out, make_demo.DEMO_SEED, make_demo.DEMO_LAPS, chapters=1,
                      origin=make_demo.DEMO_ORIGIN, habit=make_demo.DEMO_HABIT, gps_noise=noise)
    return _load(rec.paths[0]), rec.truth


_SYNTH: dict = {}


def _synth(work: str, seed: int, noise: float):
    """(session, truth) of the truth matrix's recording at (seed, noise), generated once."""
    if (seed, noise) not in _SYNTH:
        rec = sg.generate(os.path.join(work, f"synth_{seed}_{noise:g}"), seed=seed, gps_noise=noise)
        _SYNTH[seed, noise] = (_load(rec.paths[0]), rec.truth)
    return _SYNTH[seed, noise]


def _pct(xs, q) -> float:
    return float(np.percentile(np.asarray(xs, float), q))


# ─── 1 · predicted lap time ─────────────────────────────────────────────────────────────────────
def predicted_errors(s: Session) -> dict:
    """|reference lap time + Δ(t) − the lap's real time| at each fraction of every clean lap's own
    time, the reference being the best lap (the clip's baseline with no cross-recording one)."""
    best = s.best_lap_id()
    ref = s.lap_time(best)
    out = {f: [] for f in FRACTIONS}
    final = []
    for lap in s.valid_lap_ids():
        if lap == best:
            continue
        ls, le = s.lap_window(lap)
        lt = s.lap_time(lap)
        for f in FRACTIONS:
            out[f].append(abs(ref + s.delta_at_lap(lap, ls + f * (le - ls)) - lt))
        final.append(abs(ref + s.delta_at_lap(lap, le - 1e-3) - lt))
    times = [s.lap_time(i) for i in s.valid_lap_ids()]
    return {"laps": len(final), "lap_sd": float(np.std(times, ddof=1)),
            "final_max": max(final), **{f"err_{f}": v for f, v in out.items()}}


def _summary(errs: dict) -> dict:
    res = {"laps": errs["laps"], "lap_sd": round(errs["lap_sd"], 3),
           "final_max_ms": round(1e3 * errs["final_max"], 3)}
    for f in FRACTIONS:
        v = errs[f"err_{f}"]
        res[f"median_{int(100 * f)}"] = round(float(np.median(v)), 3)
        res[f"p90_{int(100 * f)}"] = round(_pct(v, 90), 3)
    return res


# ─── 2 · the Δ-trace band ───────────────────────────────────────────────────────────────────────
def band_geometry(s: Session, work: str) -> dict:
    """Each clean lap's largest |Δ|, sampled as the export samples it, and the band's resolution."""
    from studio import export_video as ev
    from studio import lap_race
    best = s.best_lap_id()
    maxes, travel, corner_px = [], [], []
    half_px = (BAND_H_U * 1080.0 - 2 * BAND_INSET_Y) / 2.0
    for lap in s.valid_lap_ids():
        if lap == best:
            continue
        spec = ev.build_lap_spec(s, os.path.join(work, "unused.mp4"), lap)
        try:
            ds = [v.delta_s for v in (ev.overlay_values_at(s, float(t), spec)
                                      for t in ev.frame_times(spec.t0, spec.t1, FPS))
                  if v.lap_started and v.delta_s is not None]
        finally:
            spec.source.cleanup()
        maxes.append(float(np.max(np.abs(ds))))
        travel.append(float(np.max(ds) - np.min(ds)))
        # Each corner's rise of Δ on this lap's own band (the Corners table's per-corner Δ, which
        # is the trace's rise through the corner exactly, TRUTH-10), in pixels at 1080p.
        scale = max(BAND_FLOOR_S, BAND_HEADROOM * maxes[-1])
        for c, ok in zip(s.corners.lap_corner_stats(lap), s.corners.lap_corner_resolved(lap),
                         strict=True):
            if ok:
                corner_px.append(abs(c.delta) * half_px / scale)
    scale_med = max(BAND_FLOOR_S, BAND_HEADROOM * float(np.median(maxes)))
    scale_max = max(BAND_FLOOR_S, BAND_HEADROOM * max(maxes))
    return {"laps": len(maxes), "max_abs_median": round(float(np.median(maxes)), 3),
            "max_abs_max": round(max(maxes), 3), "half_px_1080": round(half_px, 1),
            "px_per_tenth_median": round(half_px * 0.1 / scale_med, 1),
            "px_per_tenth_session": round(half_px * 0.1 / scale_max, 1),
            "tie_s": lap_race.RACE_TIE_S, "corner_cells": len(corner_px),
            "corner_px_median": round(float(np.median(corner_px)), 2),
            "corner_under_stroke": int(sum(px < BAND_STROKE_PX for px in corner_px)),
            "stroke_px": BAND_STROKE_PX}


def _band_bottom(w: int, h: int, margin_frac: float) -> float:
    """The band's bottom edge: the overlay's safe area's (`layout.safe_area`)."""
    from studio.overlay import layout
    return layout.safe_area(w, h, margin_frac).bottom


def band_collision(s: Session, work: str) -> dict:
    """Where a bottom-centre band lands at 16:9 and at 9:16, against the shipped overlay's rects."""
    from studio import export_video as ev
    best = s.best_lap_id()
    out = {}
    for name, aspect, (w, h) in (("16:9", ev.ASPECT_SOURCE, (1920, 1080)),
                                 ("9:16", ev.ASPECT_9_16, (1080, 1920))):
        cfg = ev.OverlayConfig(aspect=aspect)
        spec = ev.build_lap_spec(s, os.path.join(work, "unused.mp4"), best, config=cfg)
        try:
            p = ev.OverlayPainter(s, spec, w, h, FPS)
        finally:
            spec.source.cleanup()
        u, m = p._u, cfg.margin_frac * p._u
        bw, bh = BAND_W_U * u, BAND_H_U * u
        # On the overlay's bottom row: the safe area's bottom, which is the margin in landscape
        # and, in portrait, clear of the bottom band the apps cover (`layout.safe_area`) — the
        # place every shipped element is held to, so a band below it would be measured where no
        # element may go.
        band = (0.5 * (w - bw), _band_bottom(w, h, cfg.margin_frac) - bh, bw, bh)
        rects = {"readout": p._readout_rect, "map": p._map._box}
        hits = {}
        for k, r in rects.items():
            ox = max(0.0, min(band[0] + band[2], r.x() + r.width()) - max(band[0], r.x()))
            oy = max(0.0, min(band[1] + band[3], r.y() + r.height()) - max(band[1], r.y()))
            hits[k] = round(100.0 * ox * oy / (band[2] * band[3]), 1)
        # The room the bottom row leaves between the readout and the map, less the overlay's own
        # gap of one margin on each side (`_unstack_row`'s rule).
        ro, mp = rects["readout"], rects["map"]
        free = mp.x() - (ro.x() + ro.width()) - 2 * m
        out[name] = {"band": [round(v, 1) for v in band], "overlap_pct": hits,
                     "free_w": round(free, 1),
                     **{k: [round(v, 1) for v in (r.x(), r.y(), r.width(), r.height())]
                        for k, r in rects.items()}}
    return out


class _DeltaBand:
    """The design pass's band, rebuilt on export_video's own primitives: a plate, the ±tie band and
    zero line, and the lap's Δ trace up to the current frame, its scale fixed per export. Every
    frame's Δ is known before frame 1 (the export is offline), so the trace is precomputed."""

    def __init__(self, s: Session, spec, out_w: int, out_h: int, u: float, k: float, m: float):
        from PySide6.QtCore import QRectF

        from studio import export_video as ev
        from studio import lap_race
        self.k, self.tie = k, lap_race.RACE_TIE_S
        bw, bh = BAND_W_U * u, BAND_H_U * u
        self.r = QRectF(0.5 * (out_w - bw), _band_bottom(out_w, out_h, m / u) - bh, bw, bh)
        self.win = s.lap_window(spec.lap_id)
        fr, ds = [], []
        for t in ev.frame_times(spec.t0, spec.t1, FPS):
            v = ev.overlay_values_at(s, float(t), spec)
            if v.lap_started and v.delta_s is not None:
                fr.append(min(1.0, max(0.0, (v.tt - self.win[0]) / (self.win[1] - self.win[0]))))
                ds.append(v.delta_s)
        self.fr, self.ds = np.array(fr), np.array(ds)
        self.scale = max(BAND_FLOOR_S, BAND_HEADROOM * float(np.abs(self.ds).max()))

    def paint(self, p, vals) -> None:
        from PySide6.QtCore import QPointF, QRectF, Qt
        from PySide6.QtGui import QPen, QPolygonF

        from studio import export_video as ev
        from studio.export_palette import EXPORT
        k, r = self.k, self.r
        p.setBrush(theme.qcolor(EXPORT.halo, 150))
        p.setPen(QPen(theme.qcolor(EXPORT.text, 40), 1.0 * k))
        p.drawRoundedRect(r, 6 * k, 6 * k)
        inner = r.adjusted(8 * k, BAND_INSET_Y * k, -8 * k, -BAND_INSET_Y * k)
        cy, hh = inner.center().y(), inner.height() / 2
        tb = hh * self.tie / self.scale
        p.setPen(Qt.NoPen)
        p.setBrush(theme.qcolor(EXPORT.text, 46))
        p.drawRect(QRectF(inner.x(), cy - tb, inner.width(), 2 * tb))
        p.setPen(QPen(theme.qcolor(EXPORT.text, 120), 1.0 * k))
        p.drawLine(QPointF(inner.x(), cy), QPointF(inner.right(), cy))
        if not vals.lap_started or vals.tt is None:
            return
        now = (vals.tt - self.win[0]) / (self.win[1] - self.win[0])
        n = int(np.searchsorted(self.fr, now, side="right"))
        if n < 2:
            return
        xs = inner.x() + self.fr[:n] * inner.width()
        ys = cy + np.clip(self.ds[:n] / self.scale, -1, 1) * hh
        poly = QPolygonF([QPointF(float(x), float(y)) for x, y in zip(xs, ys, strict=True)])
        d = float(self.ds[n - 1])
        col = EXPORT.neutral if abs(d) < self.tie else ev.export_delta_colour(d)
        ev._stroke_polyline(p, poly, col, BAND_STROKE_PX * k, halo=1.6 * k)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.qcolor(col))
        p.drawEllipse(QPointF(float(xs[-1]), float(ys[-1])), 3.6 * k, 3.6 * k)
        ev._text_at(p, QRectF(inner.x(), r.y() + 2 * k, inner.width(), r.height() * 0.3),
                    Qt.AlignRight | Qt.AlignTop, f"Δ ±{self.scale:.1f} s",
                    ev._font(r.height() * 0.24, bold=True), EXPORT.text_dim, halo=1.4 * k)


def paint_cost(work: str) -> dict:
    """ms per frame: the shipped overlay, and the band alone, on a lap of the demo that is not the
    best (the best lap has no Δ to draw), at 1080p and 4K, interleaved over ROUNDS."""
    s, _truth = _demo(work)
    best = s.best_lap_id()
    lap = min((i for i in s.valid_lap_ids() if i != best),
              key=lambda i: abs(s.lap_time(i) - s.lap_time(best) - 0.5))
    res = {"lap": f"L{lap_label(lap)}"}     # the app's lap label, never the 0-based id
    for w, h in ((1920, 1080), (3840, 2160)):
        res[f"{h}p"] = _paint_at(s, lap, w, h, work)
    return res


def _paint_at(s: Session, lap: int, w: int, h: int, work: str) -> dict:
    from PySide6.QtGui import QImage, QPainter

    from studio import export_video as ev
    spec = ev.build_lap_spec(s, os.path.join(work, "unused.mp4"), lap)
    try:
        pt = ev.OverlayPainter(s, spec, w, h, FPS)
        vals = [ev.overlay_values_at(s, float(t), spec) for t in ev.frame_times(spec.t0, spec.t1, FPS)]
        dials = [pt.advance_and_snapshot(v) for v in vals]
        band = _DeltaBand(s, spec, w, h, pt._u, pt._k, spec.config.margin_frac * pt._u)
    finally:
        spec.source.cleanup()
    base = QImage(w, h, QImage.Format_RGB888)
    base.fill(0x556677)
    idx = list(range(0, len(vals), 3))

    def today(i):
        img = base.copy()
        t0 = time.perf_counter()
        pt.paint_frame_with_state(img, vals[i], dials[i])
        return time.perf_counter() - t0

    def banded(i):
        img = base.copy()
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        t0 = time.perf_counter()
        band.paint(p, vals[i])
        dt = time.perf_counter() - t0
        p.end()
        return dt

    def both(i):     # what the band ADDS to a frame: the shipped paint, then the band on it
        img = base.copy()
        t0 = time.perf_counter()
        pt.paint_frame_with_state(img, vals[i], dials[i])
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        band.paint(p, vals[i])
        p.end()
        return time.perf_counter() - t0

    rounds: dict[str, list[float]] = {"today": [], "band": [], "both": []}
    for _ in range(ROUNDS):
        for name, fn in (("today", today), ("band", banded), ("both", both)):
            rounds[name].append(1e3 * statistics.median(fn(i) for i in idx))
    res: dict = {k: round(statistics.median(v), 2) for k, v in rounds.items()}
    res["added"] = round(statistics.median(
        b - t for b, t in zip(rounds["both"], rounds["today"], strict=True)), 2)
    res["rounds"] = {k: [round(x, 2) for x in v] for k, v in rounds.items()}
    res["frames"] = len(idx)
    return res


def _picture_4k(path, truth, first_payload, n_payloads, ffmpeg):
    """`synth_gopro.generate(video=)`: a 3840x2160 picture at 29.97, testsrc2's moving pattern (the
    demo's own picture is too small for a 4K export, and the exporter never upscales). Two seconds
    are drawn once and looped, as `vt_gop` does."""
    import subprocess
    loop = os.path.join(os.path.dirname(path), "testsrc2_4k.mkv")
    quiet = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error"]
    if not os.path.exists(loop):
        subprocess.run([*quiet, "-f", "lavfi", "-i", "testsrc2=s=3840x2160:r=30000/1001",
                        "-frames:v", "60", "-c:v", "ffv1", "-pix_fmt", "yuv420p", "-n", loop],
                       check=True)
    subprocess.run([*quiet, "-stream_loop", "-1", "-i", loop,
                    "-frames:v", str(n_payloads * sg.FRAMES_PER_PAYLOAD), "-c:v", "libx264",
                    "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p",
                    "-x264-params", "keyint=30:min-keyint=30:scenecut=0:bframes=0",
                    "-video_track_timescale", "30000", "-an", "-n", path], check=True)


def export_budget(work: str) -> dict:
    """A real 4K export of one lap, as shipped and with BUSY_MS of Python spin added to every
    frame's paint (it holds the GIL, as QPainter does: refusal §19), interleaved BUDGET_ROUNDS
    times; each variant's wall is its quickest round, the one the machine's load touched least.
    The recording is `synth_gopro`'s circuit, two laps, under a 4K picture."""
    from studio import export_video as ev
    rec = sg.generate(os.path.join(work, "rec4k"), laps=2, chapters=1, video=_picture_4k)
    s = _load(rec.paths[0])
    lap = s.best_lap_id()
    shipped = ev.OverlayPainter.paint_frame_with_state

    def spun(self, img, vals, dial):
        shipped(self, img, vals, dial)
        end = time.perf_counter() + BUSY_MS / 1e3
        while time.perf_counter() < end:
            pass

    walls = {"shipped": [], "busy": []}
    frames = None
    for k in range(BUDGET_ROUNDS):
        for name in ("shipped", "busy"):
            ev.OverlayPainter.paint_frame_with_state = shipped if name == "shipped" else spun
            out = os.path.join(work, f"export_{name}_{k}.mp4")
            t0 = time.perf_counter()
            r = ev.render_lap(s, rec.paths[0], out, lap, config=ev.OverlayConfig(out_height=2160))
            walls[name].append(time.perf_counter() - t0)
            assert (r.out_w, r.out_h) == (3840, 2160), (r.out_w, r.out_h)
            frames = r.frames
            os.remove(out)
    ev.OverlayPainter.paint_frame_with_state = shipped
    base, busy = min(walls["shipped"]), min(walls["busy"])
    slope = (busy - base) / (BUSY_MS * frames)          # s of export per ms of paint per frame
    return {"lap": f"L{lap_label(lap)}", "frames": frames, "walls": {k: [round(x, 2) for x in v] for k, v in walls.items()},
            "export_s": round(base, 2), "per_ms_s": round(slope * frames, 3),
            "share_per_ms": round(slope * frames / base, 4),
            "budget_ms": round(BUDGET_SHARE * base / (slope * frames), 2)}


# ─── 3 · sector chips ───────────────────────────────────────────────────────────────────────────
def _sector_lines(s: Session, truth) -> list:
    lines = [truth.line_at(sum(truth.circuit.straights[j]) / 2.0) for j in SECTOR_STRAIGHTS]
    start, _ = s.timing_lines_latlon()
    assert s.apply_timing_lines_latlon(start, lines, confirmed=True), "sector lines refused"
    return lines


def _true_splits(s: Session, truth, lines) -> dict[int, np.ndarray]:
    """Each clean app lap's TRUE splits, matched to its true lap by start time (as the truth
    matrix matches them)."""
    tl = truth.crossings(s.timing_lines_latlon()[0])
    starts = {i: float(s._lap_columns(i)[0][0]) for i in s.valid_lap_ids()}
    offset = float(np.median([t0 - tl[int(np.argmin(np.abs(tl - t0)))] for t0 in starts.values()]))
    xs = [truth.crossings(ln) for ln in lines]
    out = {}
    for i, t0 in starts.items():
        k = int(np.argmin(np.abs(tl - (t0 - offset))))
        if abs(tl[k] - (t0 - offset)) < 0.25 and k + 1 < len(tl):
            lo, hi = tl[k], tl[k + 1]
            b = sorted(float(x[(x > lo) & (x < hi)][0]) for x in xs)
            out[i] = np.diff([lo, *b, hi])
    return out


def chip_margins(s: Session, truth) -> dict:
    """Every clean lap after the first, every sector: how far its split beats the best earlier
    clean lap's in that sector (> 0 is a new best, the chip a broadcast would colour), as the app
    measures it and as the truth has it."""
    lines = _sector_lines(s, truth)
    true = _true_splits(s, truth, lines)
    laps = [i for i in s.valid_lap_ids() if i in true]
    app = {i: np.asarray(s.lap_sector_splits(i), float) for i in laps}
    app_m, true_m = [], []
    for k in range(1, len(laps)):
        prev_app = np.min([app[j] for j in laps[:k]], axis=0)
        prev_true = np.min([true[j] for j in laps[:k]], axis=0)
        app_m += (prev_app - app[laps[k]]).tolist()
        true_m += (prev_true - true[laps[k]]).tolist()
    return {"app": app_m, "true": true_m}


def chip_figures(work: str, sigma: dict[float, float]) -> dict:
    res = {}
    for noise in CHIP_NOISES:
        gate = GATE_K * sigma[noise]
        for label, sessions in (("synth", [_synth(work, seed, noise) for seed in SEEDS]),
                                ("demo", [_demo(work, noise)])):
            app, true = [], []
            for s, truth in sessions:
                m = chip_margins(s, truth)
                app += m["app"]
                true += m["true"]
            app, true = np.asarray(app), np.asarray(true)
            res[f"{label}_{noise:g}"] = {
                "sigma": sigma[noise], "gate": round(gate, 3), "chips": int(len(app)),
                "new_best": int((app > 0).sum()), "clear_gate": int((app > gate).sum()),
                "true_new_best": int((true > 0).sum()), "true_clear_gate": int((true > gate).sum()),
                "wrong_colour": int(((app > 0) != (true > 0)).sum()),
                "true_best_margin": round(float(true.max()), 3),
                "app_best_margin": round(float(app.max()), 3)}
    return res


# ─── 4 · the share card's corner cells ──────────────────────────────────────────────────────────
def card_cells(s: Session) -> dict:
    from studio import share_card
    best = s.best_lap_id()
    mat = s.corner_matrix()
    row = mat.lap_ids.index(best)
    vs_typical = {}
    for c, cid in enumerate(mat.cids):
        if mat.medians[c] is None or not mat.resolved[row][c]:
            continue
        vs_typical[cid] = (round(mat.cells[row][c] - mat.medians[c], 3), round(mat.scales[c], 3))
    sb = s.ideal_segment_bests()
    li = sb.lap_ids.index(best)
    vs_ideal = {}
    for cid in sb.cids:
        j = sb.labels.index(f"C{cid}")
        vs_ideal[cid] = round(float(sb.times[li][j] - sb.bests[j]), 3)
    top = share_card.card_data(s).top_opp
    biggest = max(vs_ideal, key=vs_ideal.get)
    # The lap as the app names it (L13), beside the 0-based id the tie re-derives it from: the
    # section once printed the id as the lap number (QA EVAL-2).
    return {"best_lap": f"L{lap_label(best)}", "best_lap_id": best, "vs_typical": vs_typical, "vs_ideal": vs_ideal,
            "biggest_vs_ideal": biggest, "card_opp": top.corner_label if top else None,
            "card_opp_loss": round(top.time_lost_s, 3) if top else None,
            "inside_step": sum(abs(g) < st for g, st in vs_typical.values()),
            "corners": len(vs_typical)}


# ─── the split σ the chips are gated on ───────────────────────────────────────────────────────────
# Truth matrix row 2's interior split σ at each chip noise AS IT WAS MEASURED for §26, on the splits
# as timed before cool/sector-dedrift (each lap's receiver shift is now taken off before the sector
# lines are crossed, and row 2 reads 0.024 / 0.051 s). Held here, not read from the matrix, so §26's
# gated counts cannot move silently with that row: the chips were NOT re-run at a de-drifted gate,
# and doing so is what §26's new-evidence paragraph asks for next.
ROW2_SIGMA_BEFORE_DEDRIFT = {2.0: 0.106567, 4.5: 0.241378}


def _row2_sigma() -> dict[float, float]:
    """The gate's σ at each chip noise (`ROW2_SIGMA_BEFORE_DEDRIFT`)."""
    return {lv: ROW2_SIGMA_BEFORE_DEDRIFT[lv] for lv in CHIP_NOISES}


def figures(work: str) -> dict:
    demo, _truth = _demo(work)
    res = {"predicted": {"demo": _summary(predicted_errors(demo))}}
    pooled: dict = {}
    for seed in SEEDS:
        e = predicted_errors(_synth(work, seed, PREDICT_NOISE)[0])
        for k, v in e.items():
            pooled[k] = pooled.get(k, []) + (v if isinstance(v, list) else [v])
    pooled = {"laps": sum(pooled["laps"]), "lap_sd": float(np.median(pooled["lap_sd"])),
              "final_max": max(pooled["final_max"]),
              **{f"err_{f}": pooled[f"err_{f}"] for f in FRACTIONS}}
    res["predicted"]["synth"] = _summary(pooled)
    res["trace"] = band_geometry(demo, work)
    res["trace"]["collision"] = band_collision(demo, work)
    res["chips"] = chip_figures(work, _row2_sigma())
    res["cells"] = card_cells(demo)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", choices=("figures", "paint", "budget"))
    args = ap.parse_args(argv)
    work = tempfile.mkdtemp(prefix="clip_refusals_")
    try:
        res = {"figures": figures, "paint": paint_cost, "budget": export_budget}[args.mode](work)
    finally:
        shutil.rmtree(work)
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
