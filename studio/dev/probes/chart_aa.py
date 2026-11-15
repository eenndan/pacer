"""chart_aa: what does always-on antialiasing cost the speed/Δ charts, per cursor tick and per lap
switch? (cool/compare-live-delta; the Wave 2 design lane's item D.)

WHY. `plots_view` turns pyqtgraph's antialiasing OFF ("the cursor re-renders every visible curve
each ~30 Hz tick"), so the lap curves are drawn aliased. At 2x they look a little stepped, and
"just turn it on" is the obvious fix. This measures what that costs on the real window, and the
verdict is `studio/docs/refused-2026-09.md` §23.

WHAT IT DOES, on the synthetic demo (nobody's driving, so the numbers can be published and anyone
can re-run them):
  1. opens the demo in the REAL `StudioWindow`, offscreen at 1728x1117, jailed, the sidecar write
     stubbed, and draws the charts with a lap and the best lap — the default two-curve overlay;
  2. a CURSOR SWEEP: `PlotsView.set_playhead_time` over 120 places in the best lap, each followed
     by a repaint of the charts widget and the event loop — the work the ~30 Hz tick asks of the
     charts — timed per step, with antialiasing OFF (shipped), ON for every curve, and ON for the
     selected lap's two curves only (speed + Δ);
  3. a LAP SWITCH: `PlotsView.set_laps([lap, best])` round six laps, repaint included, OFF and ON
     (ON = pyqtgraph's global option, which is what a new curve is drawn with);
  4. ROUNDS passes of all five, interleaved, so the machine's load lands on every variant alike;
     each variant's figure is the median over rounds of the per-round median step.
With `--png-dir DIR` it also saves the charts panel at 2x, OFF and ON, for the look.

ARGV CONTRACT: nothing names an input. The demo comes from PACER_DEMO_MP4 (read only), else it is
generated (`make_demo`) into ONE `mkdtemp` under $TMPDIR, removed before exit. `--png-dir` is the
only output, refused under ~/Desktop. The results are printed; the last line is their JSON.

    pixi run python -m studio.dev.probes.chart_aa
    pixi run python -m studio.dev.probes.chart_aa --png-dir /tmp/chart_aa_png
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")

from studio.dev._jail import divert_app_support  # noqa: E402

_JAIL = divert_app_support("chart_aa_")

import numpy as np  # noqa: E402
import pyqtgraph as pg  # noqa: E402

from studio import sidecar  # noqa: E402  (after the jail, as every load must be)

sidecar.save = lambda *a, **k: None   # the app writes timing lines beside the recording: never here
from studio.dev import make_demo  # noqa: E402
from studio.dev import media_capture as mc  # noqa: E402

DESKTOP = os.path.realpath(os.path.expanduser("~/Desktop"))
WINDOW = (1728, 1117)
SWEEP_STEPS = 120
SWITCH_STEPS = 24
ROUNDS = 5


def _timed(app, fn, n):
    """Median ms of `fn(k)` + one event-loop pass, over k = 0..n-1."""
    out = []
    for k in range(n):
        t0 = time.perf_counter()
        fn(k)
        app.processEvents()
        out.append(time.perf_counter() - t0)
    return float(np.median(out) * 1e3)


def _curves(pv):
    return [it for p in (pv.p_speed, pv.p_delta) for it in p.listDataItems() if hasattr(it, "curve")]


def _set_aa(pv, on: bool, selected_only: bool = False):
    """Antialiasing per curve item. `selected_only`: the selected lap's curves only — the first
    item on each plot, which is the selected lap (the best lap is appended after it)."""
    items = _curves(pv)
    first = {id(p.listDataItems()[0]) for p in (pv.p_speed, pv.p_delta) if p.listDataItems()}
    for it in items:
        flag = on and (not selected_only or id(it) in first)
        it.opts["antialias"] = flag
        it.curve.opts["antialias"] = flag
        it.curve.update()


def measure(recording: str, png_dir: str | None) -> dict:
    app = mc.boot_app()
    w = mc.open_window(app, recording, size=WINDOW)
    mc._settle_ui(app, w)
    if hasattr(w, "_escape_out"):
        w._escape_out()
    mc.settle(app, 10)
    v, s = w.view, w.session
    pv = v.plots
    best = s.best_lap_id()
    others = [i for i in sorted(s.valid_lap_ids(), key=s.lap_time) if i != best][:6]
    t0b, t1b = s.lap_window(best)
    ts = np.linspace(t0b + 1, t1b - 1, SWEEP_STEPS)

    def sweep(k):
        pv.set_playhead_time(float(ts[k % len(ts)]))
        pv.update()

    def switch(k):
        pv.set_laps([others[k % len(others)], best])
        pv.update()

    rounds: dict[str, list[float]] = {k: [] for k in (
        "sweep_off", "sweep_on", "sweep_on_selected", "switch_off", "switch_on")}
    for _r in range(ROUNDS):
        pg.setConfigOptions(antialias=False)
        pv.set_laps([others[0], best])
        _set_aa(pv, False)
        mc.settle(app, 3)
        rounds["sweep_off"].append(_timed(app, sweep, SWEEP_STEPS))
        rounds["switch_off"].append(_timed(app, switch, SWITCH_STEPS))
        pg.setConfigOptions(antialias=True)       # what every curve a switch builds is drawn with
        pv.set_laps([others[0], best])
        _set_aa(pv, True)
        mc.settle(app, 3)
        rounds["sweep_on"].append(_timed(app, sweep, SWEEP_STEPS))
        rounds["switch_on"].append(_timed(app, switch, SWITCH_STEPS))
        pg.setConfigOptions(antialias=False)
        pv.set_laps([others[0], best])
        _set_aa(pv, True, selected_only=True)
        mc.settle(app, 3)
        rounds["sweep_on_selected"].append(_timed(app, sweep, SWEEP_STEPS))
        _set_aa(pv, False)
    res = {k: round(statistics.median(vals), 2) for k, vals in rounds.items()}
    res["rounds"] = {k: [round(x, 2) for x in vals] for k, vals in rounds.items()}
    if png_dir:
        pv.set_laps([others[0], best])
        pv.set_playhead_time(float(ts[40]))
        for tag, on in (("off", False), ("on", True)):
            _set_aa(pv, on)
            mc.settle(app, 4)
            mc.save(mc.grab(v._plots_panel), os.path.join(png_dir, f"chart_aa_{tag}.png"))
        _set_aa(pv, False)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--png-dir", help="where to save the 2x charts panel, AA off and on (output)")
    args = ap.parse_args(argv)
    png_dir = os.path.realpath(args.png_dir) if args.png_dir else None
    if png_dir and (png_dir == DESKTOP or png_dir.startswith(DESKTOP + os.sep)):
        raise SystemExit(f"REFUSED: {png_dir} is under ~/Desktop, which holds footage: read-only")
    if png_dir:
        os.makedirs(png_dir, exist_ok=True)
    recording = os.environ.get("PACER_DEMO_MP4")
    work = None
    if not recording:
        work = tempfile.mkdtemp(prefix="chart_aa_")
        recording = os.path.join(work, "demo.mp4")
        make_demo.make(recording)
    try:
        res = measure(recording, png_dir)
    finally:
        if work is not None:
            for name in os.listdir(work):
                os.remove(os.path.join(work, name))
            os.rmdir(work)
    for k in ("sweep_off", "sweep_on", "sweep_on_selected", "switch_off", "switch_on"):
        print(f"{k:20s} {res[k]:7.2f} ms   rounds {res['rounds'][k]}")
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
