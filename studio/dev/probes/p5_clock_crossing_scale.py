"""P5 — at what WINDOW LENGTH does the strip's clock crossing stop being harmless?

`QualityTimeline` cells are seconds of the NAIVE media clock the loader built them on; every window
the app indexes them with is TELEMETRY (GPS9 true-clock) seconds. #306 measured that crossing at LAP
scale and found it harmless (0 of 38 / 0 of 65 laps change class). #314 measured it at CORNER scale
and reported 9 of 456 cells flipping. This probe asks the question both of those answer one point
of: the flip rate as a function of window length, and then for every real consumer of the crossing
at the window length that consumer actually uses.

WHAT IT FOUND (both D24 recordings; the rule is written in `Session.quality_timeline`):
  * #314's 9 of 456 crossed `media_time`, which carries the GPS lag. Against the fixes' own stamps
    the label gets 1 of 456 corner cells wrong (0 of 780 on 0062), and the rate fit gets none.
  * How OFTEN a window flips does not depend on its length (~0.1 % of windows on 0060 from 0.1 s to
    20 s). What does is the share of below-good verdicts that are wrong: 0.07 % at 70 s, 1.1 % at
    10 s, 4.6 % at 1 s. Under ~10 s a consumer that acts on the class should cross
    `media_clock.without_gps_lag()` first.
  * No consumer in the app acts on a window that short. The scrub-bar strip paints one, and moves
    by at most 2 px.

WHICH AXIS IS RIGHT IS MEASURED PER FIX, NOT ARGUED. The loader holds both labels for every kept
fix — its telemetry time and the naive media stamp the strip binned it by — so the residual of any
candidate join is directly observable, with no correlation involved:

  * `label` — index the strip with the telemetry time as-is (what every consumer does today);
  * `pure`  — `media_clock.without_gps_lag().to_media` (the two clocks' affine fit alone);
  * `lag`   — `Session.media_time`, which since #301 also carries the GPS lag. That lag is a fact
    about the PICTURE; a fix's telemetry and naive stamps carry it equally, so it is not part of the
    strip's axis. #314's probe crossed with this one.
  * `truth` — each window edge placed on the strip's axis through the kept fixes' OWN naive stamps
    (`np.interp` over the per-fix pairs), which is how the strip actually binned them.

The per-fix pairs come from the REAL load: `load.read_recording` is wrapped (not reimplemented) to
keep what it returned, and the kept trace is re-derived with the loader's own `_gate_quality`,
`_clean` and `_gps9_times` — then asserted bit-identical to `Session.tt`, so the pairs cannot
describe a different trace from the one the app segmented.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p5_clock_crossing_scale
    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p5_clock_crossing_scale 0062

TWO SAFETY PROPERTIES, both enforced rather than promised, as in `p4_corner_gps_quality`:
  * `~/Desktop/D24` IS READ-ONLY. No path under it is ever passed as an output argument, and every
    chapter's (size, mtime) is snapshotted before and after each load and compared.
  * `Session.load` UPSERTS INTO THE USER'S LIBRARY, so `studio.dev._jail` diverts every app-support
    seam to a throwaway dir BEFORE any studio import resolves one.
"""

from __future__ import annotations

import os
import sys

import numpy as np

from studio.dev.probes.p4_corner_gps_quality import D24, RECORDINGS, _d24_state, _windows

# The window lengths the sweep slides across each recording's kept trace. They bracket the
# consumers: a compare-mode pixel column (~0.1 s), a corner (3-6 s), a whole-session pixel column
# (~3 s), a degraded mark (>= 5 s), a lap (~70 s).
SWEEP_W = (0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0, 30.0, 45.0,
           70.0)
SWEEP_STEP_S = 0.1      # one window start per fix interval
# Scrub-bar widths the real strip widget is laid out at: ~500 px is the app's default (the strip's
# own docstring: "~6 seconds" a column on a 2,900 s recording), 1400 px a maximised panel.
BAR_WIDTHS = (500, 1000, 1400)


def _capture_read_recording():
    """Wrap the loader's own `read_recording` so its return value is kept. The wrapped call IS the
    load's call — nothing is re-read, and nothing about the result changes."""
    from studio import load

    real = load.read_recording
    kept: dict = {}

    def spy(paths):
        out = real(paths)
        kept["out"] = out
        return out

    load.read_recording = spy
    return kept, lambda: setattr(load, "read_recording", real)


def _fix_pairs(raw, session):
    """(telemetry, naive) per KEPT fix, through the loader's own gate, clean and GPS9 axis —
    asserted bit-identical to the trace the Session segmented."""
    from studio import load

    samples, spans, naive = raw[0], raw[1], raw[2]
    samples, spans, naive, _d, _f = load._gate_quality(samples, spans, naive,
                                                       moving_speed=load.MIN_START_SPEED)
    samples, spans, naive = load._clean(samples, spans, naive)
    tel = np.asarray(load._gps9_times(samples, naive), float)
    naive = np.asarray(naive, float)
    if not np.array_equal(tel, np.asarray(session.tt, float)):
        raise SystemExit(f"REFUSING TO CONTINUE: the re-derived telemetry axis ({len(tel)} fixes) "
                         f"is not bit-identical to Session.tt ({len(session.tt)}), so these pairs "
                         f"would describe a different trace from the one the app segmented.")
    return tel, naive


def _joins(session, tel, naive):
    pure = session.media_clock.without_gps_lag()
    return {
        "label": lambda t: np.asarray(t, float),
        "pure": lambda t: pure.to_media(np.asarray(t, float)),
        # `Session.media_time` is `float(media_clock.to_media(t))`; the same map, vectorised.
        "lag": lambda t: session.media_clock.to_media(np.asarray(t, float)),
        "truth": lambda t: np.interp(np.asarray(t, float), tel, naive),
    }


def _residuals(strip, tel, naive, joins) -> None:
    print("\nPER-FIX RESIDUAL of each join against the strip's own axis (naive − join(telemetry)):")
    cell = strip.cell_s
    own = np.floor(naive / cell)
    for name in ("label", "pure", "lag"):
        r = naive - joins[name](tel)
        same = float(np.mean(np.floor(joins[name](tel) / cell) == own))
        print(f"  {name:<6} median {np.median(r):+.4f}s  p1 {np.percentile(r, 1):+.4f}  "
              f"p99 {np.percentile(r, 99):+.4f}  max|r| {np.max(np.abs(r)):.4f}  "
              f"-> fix lands in its own cell: {100 * same:.2f}%")


def _classes(strip, joins, t0s, t1s, names=("label", "pure", "lag", "truth")):
    out = {}
    for name in names:
        a, b = joins[name](t0s), joins[name](t1s)
        out[name] = np.array([strip.worst_between(float(x), float(y))
                              for x, y in zip(a, b, strict=True)], dtype=object)
    return out


def _flip_line(cls, n, *, pairs=(("label", "truth"), ("pure", "truth"), ("lag", "truth"),
                                  ("label", "lag"))) -> str:
    return "  ".join(f"{a}≠{b} {int(np.sum(cls[a] != cls[b]))}/{n}" for a, b in pairs)


def _sweep(strip, tel, joins) -> None:
    from studio import data_quality

    print("\nWINDOW-LENGTH SWEEP — every window of length W sliding over the kept trace, one start "
          f"per {SWEEP_STEP_S:g} s. A flip is a window whose inherited class differs from TRUTH; "
          "each join shows flips, their share of ALL windows, and their share of the windows TRUTH "
          "grades below GOOD (the verdicts a consumer would act on):")
    print(f"  {'W (s)':>6}  {'windows':>8}  {'<GOOD':>6}  {'label':>21}  {'pure':>21}  "
          f"{'lag (#314)':>21}")
    for w in SWEEP_W:
        t0s = np.arange(tel[0], tel[-1] - w, SWEEP_STEP_S)
        t1s = t0s + w
        cls = _classes(strip, joins, t0s, t1s)
        n = len(t0s)
        bad = int(sum(c is not None and c < data_quality.GOOD for c in cls["truth"]))

        def rate(name, cls=cls, n=n, bad=bad):
            k = int(np.sum(cls[name] != cls["truth"]))
            share = f"{100 * k / bad:6.2f}%" if bad else "     -"
            return f"{k:>5} {100 * k / n:6.3f}% {share}"

        print(f"  {w:>6g}  {n:>8}  {bad:>6}  {rate('label')}  {rate('pure')}  {rate('lag')}")


def _laps(session, strip, joins) -> None:
    ids = session.valid_lap_ids()
    wins = [session.lap_window(i) for i in ids]
    t0s = np.array([w[0] for w in wins])
    t1s = np.array([w[1] for w in wins])
    cls = _classes(strip, joins, t0s, t1s)
    app = np.array([session.lap_quality(i) for i in ids], dtype=object)
    assert np.all(app == cls["label"]), "Session.lap_quality is not the label join"
    lens = t1s - t0s
    print(f"\nLAP SCALE — Session.lap_quality over {len(ids)} valid laps "
          f"(window {lens.min():.1f}-{lens.max():.1f} s, median {np.median(lens):.1f}):")
    print("  " + _flip_line(cls, len(ids)))


def _corners(session, strip, joins) -> None:
    from studio import corners as corners_mod

    clean = session.consistency_lap_ids()
    corner_list = session.corners.corner_list()
    basis = session.corners.basis()
    best = session.best_lap_id()
    if not clean or not corner_list or basis is None or best is None:
        print("\nCORNER SCALE — no corner model here")
        return
    _bt, b_xs, b_ys, _bv, b_cum = session._lap_columns(best)
    from studio import data_quality

    t0s, t1s, cells = [], [], []
    for lid in clean:
        for i, (_d0, _d1, t0, t1, _n, _g) in enumerate(
                _windows(session, corners_mod, lid, corner_list, float(basis[1]),
                         (b_xs, b_ys, b_cum))):
            t0s.append(t0)
            t1s.append(t1)
            cells.append((lid, corner_list[i].cid))
    t0s, t1s = np.array(t0s), np.array(t1s)
    cls = _classes(strip, joins, t0s, t1s)
    lens = t1s - t0s
    print(f"\nCORNER SCALE — p4's (clean lap x corner) cells, {len(t0s)} of them "
          f"(window {lens.min():.2f}-{lens.max():.2f} s, median {np.median(lens):.2f}):")
    print("  " + _flip_line(cls, len(t0s)))
    below = {name: int(sum(c is not None and c < data_quality.GOOD for c in v))
             for name, v in cls.items()}
    print(f"  cells below GOOD by join: {below}")
    for k in np.flatnonzero(cls["label"] != cls["truth"]):
        lid, cid = cells[k]
        print(f"  label flip: lap {lid} C{cid} window {t0s[k]:.2f}-{t1s[k]:.2f} s — label "
              f"{data_quality.QUALITY_LABEL[cls['label'][k]]}, truth "
              f"{data_quality.QUALITY_LABEL[cls['truth'][k]]}")


def _strip_widget(session, strip, joins) -> None:
    """The scrub bar's quality strip, through the REAL widget: `runs()` and `describe_at` off a real
    slider. The slider's ms are telemetry seconds (`PlayerPane` emits `to_telemetry`); the strip's
    cells are naive. An AFFINE join is exactly a change of the slider's range, so the pure / lag
    variants are the same widget with the range mapped — no reimplementation of the fold."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from studio import theme, video_view

    app = QApplication.instance() or QApplication([])
    theme.apply_theme(app)

    def columns(lo_s, hi_s, width):
        slider = video_view._LapRulerSlider(Qt.Horizontal)
        slider.setObjectName("ScrubBar")
        slider.setRange(int(round(lo_s * 1000)), int(round(hi_s * 1000)))
        slider.resize(width, theme.HIT_MIN + theme.SPACE_XXS)
        widget = video_view._QualityStrip(slider)
        widget.resize(width, video_view._QualityStrip.INK_H)
        widget.set_timeline(strip)
        x0, span, _h = slider._travel()
        col = np.full(span, -1, dtype=int)
        for x, w, c in widget.runs():
            col[x - x0:x - x0 + w] = c
        hover = [widget.describe_at(x0 + k) for k in range(span)]
        return col, hover, span

    def widest(changed):
        """The longest run of adjacent changed columns — how far one class edge moved, in px."""
        best = run = 0
        for c in changed:
            run = run + 1 if c else 0
            best = max(best, run)
        return best

    def compare(lo, hi, width):
        base, base_hover, span = columns(lo, hi, width)
        out = {}
        for name in ("pure", "lag"):
            col, hover, _ = columns(float(joins[name](lo)), float(joins[name](hi)), width)
            out[name] = (int(np.sum(col != base)),
                         int(sum(a != b for a, b in zip(hover, base_hover, strict=True))),
                         widest(col != base))
        return span, out

    lo, hi = float(session.tt[0]), float(session.tt[-1])
    print("\nSCRUB-BAR QUALITY STRIP — whole session (the default bar): columns that change / hover "
          "texts that change / widest edge move (px) when the slider's telemetry seconds are put "
          "on the strip's axis first:")
    for width in BAR_WIDTHS:
        span, out = compare(lo, hi, width)
        print(f"  bar {width} px ({span} px travel, {(hi - lo) / span:.2f} s/px): "
              f"pure {out['pure'][0]} cols / {out['pure'][1]} hovers / {out['pure'][2]} px · "
              f"lag {out['lag'][0]} cols / {out['lag'][1]} hovers / {out['lag'][2]} px")

    clean = session.consistency_lap_ids()
    print(f"\nSCRUB-BAR QUALITY STRIP — compare mode (the bar confined to ONE lap), over "
          f"{len(clean)} clean laps:")
    for width in BAR_WIDTHS:
        tot = {"pure": [0, 0, 0, 0], "lag": [0, 0, 0, 0]}
        spp, hover_px = [], 0
        for lid in clean:
            w = session.lap_window(lid)
            span, out = compare(float(w[0]), float(w[1]), width)
            spp.append((w[1] - w[0]) / span)
            hover_px += span
            for name in tot:
                tot[name][0] += out[name][0]
                tot[name][1] += out[name][1]
                tot[name][2] += int(out[name][0] > 0)
                tot[name][3] = max(tot[name][3], out[name][2])
        print(f"  bar {width} px ({np.median(spp):.3f} s/px, {hover_px} hover px): "
              f"pure {tot['pure'][0]} cols / {tot['pure'][1]} hovers on {tot['pure'][2]} laps, "
              f"widest {tot['pure'][3]} px · "
              f"lag {tot['lag'][0]} cols / {tot['lag'][1]} hovers on {tot['lag'][2]} laps, "
              f"widest {tot['lag'][3]} px")


def _marks(session, joins) -> None:
    marks, suppressed = session.auto_marks()
    print(f"\nAUTO MARKS — {len(marks)} marks, {suppressed} degraded stretches suppressed:")
    for m in marks:
        t = float(m["t"])
        shift = float(joins["pure"](t)) - t
        print(f"  {m['type']:<10} t={t:9.3f}s  end={m['t_end']}  "
              f"telemetry<->strip offset at its start {shift:+.4f}s")


def probe_recording(key: str) -> None:
    from studio.session import Session

    paths = [os.path.join(D24, n) for n in RECORDINGS[key]]
    print(f"\n{'=' * 78}\n{key}: {', '.join(os.path.basename(p) for p in paths)}\n{'=' * 78}")
    kept, restore = _capture_read_recording()
    before = _d24_state(paths)
    try:
        session = Session.load(paths)
    finally:
        restore()
    after = _d24_state(paths)
    if after != before:
        moved = [os.path.basename(p) for p in paths if after[p] != before[p]]
        raise SystemExit(f"REFUSING TO CONTINUE: a D24 chapter changed during the load "
                         f"({', '.join(moved)}). That directory is read-only.")
    print(f"D24 read-only check: {len(paths)} chapters unchanged (size + mtime)")

    tel, naive = _fix_pairs(kept["out"], session)
    strip = session.quality_timeline
    clock = session.media_clock
    print(f"kept fixes: {len(tel)} (bit-identical to Session.tt); strip: {len(strip)} cells of "
          f"{strip.cell_s:g} s; clock: {clock!r}")
    joins = _joins(session, tel, naive)
    _residuals(strip, tel, naive, joins)
    _laps(session, strip, joins)
    _corners(session, strip, joins)
    _strip_widget(session, strip, joins)
    _marks(session, joins)
    _sweep(strip, tel, joins)


def main() -> None:
    from studio.dev import _jail

    jail = _jail.divert_app_support("pacer-p5-")
    print(f"app-support seams diverted to {jail.dir}")
    for key in sys.argv[1:] or ["0060", "0062"]:
        probe_recording(key)


if __name__ == "__main__":
    main()
