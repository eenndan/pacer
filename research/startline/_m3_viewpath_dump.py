"""Drive the studio VIEW paths affected by the M3 renames and dump the resulting on-screen
state so BEFORE (M2) and AFTER (M3) can be compared.

Covers:
  - PlotsView playhead setter (set_playhead_time AFTER / set_cursor_time+place_cursors_at_time
    BEFORE): cursor x-positions on both plots at a sweep of media times, incl. force=True (mid-drag).
  - MapView playhead setter (set_playhead_time AFTER / set_marker_time BEFORE): marker (x,y) at the
    same sweep of times.
  - MapView sector-emit single-source (_add_sector / _reset_sectors -> timing_lines_changed): the
    emitted (start, sectors) payload, captured via the signal.
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402

from studio.session import Session  # noqa: E402
from studio.plots_view import PlotsView  # noqa: E402
from studio.map_view import MapView  # noqa: E402

D = "/Users/daniil/Desktop/D24"
PATHS = [f"{D}/GX010060.MP4"]

# Detect which API generation we're on (AFTER has set_playhead_time; BEFORE has set_cursor_time).
AFTER = hasattr(PlotsView, "set_playhead_time")


def plots_cursor(pv, t, force=False):
    if AFTER:
        pv.set_playhead_time(t, force=force)
    else:
        if force:
            pv.place_cursors_at_time(t)
        else:
            pv.set_cursor_time(t)
    return [float(pv.cur_speed.value()), float(pv.cur_delta.value()),
            bool(pv.cur_speed.isVisible()), bool(pv.cur_delta.isVisible())]


def map_marker(mv, t):
    if AFTER:
        mv.set_playhead_time(t)
    else:
        mv.set_marker_time(t)
    p = mv.marker.pos()
    return [float(p.x()), float(p.y())]


def main():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    s = Session.load(PATHS)
    out = {"api": "after" if AFTER else "before"}

    pv = PlotsView(s)
    # show a couple of laps so the cursor has curves to sit on
    best = s.best_lap_id()
    ids = [best] if best is not None else [0]
    pv.set_laps(ids)

    mv = MapView(s)

    # sweep media times across the session
    t0, t1 = float(s.tt[0]), float(s.tt[-1])
    times = list(np.linspace(t0, t1, 25))
    out["plots_distance_mode"] = [plots_cursor(pv, t) for t in times]
    out["plots_force_mid_drag"] = [plots_cursor(pv, t, force=True) for t in times]
    # flip to time mode (exercises the combo + refresh re-place path)
    pv.x_mode_combo.setCurrentIndex(1) if AFTER else pv.x_mode.setCurrentIndex(1)
    out["plots_time_mode"] = [plots_cursor(pv, t) for t in times]

    out["map_marker"] = [map_marker(mv, t) for t in times]

    # Map sector-emit single-source path: capture the timing_lines_changed payload through
    # _add_sector (x3) then _reset_sectors. The renames route these via _emit() now.
    emitted = []

    def on_emit(start, sectors):
        emitted.append([
            [start.x1, start.y1, start.x2, start.y2],
            [[sg.x1, sg.y1, sg.x2, sg.y2] for sg in sectors],
        ])

    mv.timing_lines_changed.connect(on_emit)
    mv._add_sector()
    mv._add_sector()
    mv._add_sector()
    mv._reset_sectors()
    out["map_sector_emits"] = emitted

    json.dump(out, open(sys.argv[1], "w"))
    print("WROTE", sys.argv[1], "api=", out["api"],
          "emits=", len(emitted), "times=", len(times))


if __name__ == "__main__":
    main()
