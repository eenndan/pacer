"""motion_refusals: the two transitions the motion PR (#557) built, measured and removed — the lap
you left fading out on the map, and the Lap race card's rows sliding when they reorder. Their
verdict is `studio/docs/refused-2026-09.md` §28, and `tests/test_measured_figures.py` re-runs this
probe and holds the section to it.

WHY. #557 measured both on the synthetic demo and published the numbers only in its PR body. This
re-measures each one where anyone can, on the demo (`make_demo`'s telemetry, nobody's driving):
  * map — how much of the map a lap change changes at all. For each pair of consecutive clean laps
    (lap n → n + 1, what playback crossing the line does) it grabs the REAL window's map at 2x on
    the outgoing lap and on the incoming one, the marker and the editing chrome hidden
    (`MapView.grab_clean`), and counts the pixels that differ. That is everything a fade between
    the two lines could ever show, whatever its curve or layer: at its first frame a fade above the
    new line shows exactly the outgoing line where it is not under the new one. Every Line mode in
    `map_view._RAINBOW_ORDER`, as a share of the map's pixels: median and largest over the pairs;
  * race — what a slide would have to slide. The whole race at the app's ~30 Hz tick, speed 1
    (`lap_race.race_state` as `RaceController` reads it, `race_controller.pack_rows` as the card
    draws it, nothing selected): the card snapshots, the rows shown and the packs hidden, how often
    two rows still on the card swapped order (a row is its pack's leading lap), how often the set
    of rows changed instead (a merge, a split, a row in or out), and, for scale, how often the laps
    themselves changed order on the ladder underneath.
The fade's frame cost (#557: +2.3 to +7.3 ms over a playback repaint) needed the fade itself, which
is not in the tree, so it is not re-measured here; §28 says so.

ARGV CONTRACT: nothing names an input or an output. The demo is generated (`make_demo`, a
placeholder picture: nothing here decodes a frame) into ONE `mkdtemp` under $TMPDIR, removed
before exit; the app-support store is jailed; the sidecar write is stubbed. Output is stdout; the
last line is the JSON.

    pixi run python -m studio.dev.probes.motion_refusals
"""
from __future__ import annotations

import json
import os
import shutil
import statistics
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")

from studio.dev._jail import divert_app_support  # noqa: E402

_JAIL = divert_app_support("motion_refusals_")

import numpy as np  # noqa: E402

from studio import sidecar  # noqa: E402  (after the jail, as every load must be)

sidecar.save = lambda *a, **k: None   # a line edit writes beside the recording: never here
from studio import lap_race, race_controller  # noqa: E402
from studio.dev import make_demo  # noqa: E402
from studio.dev import media_capture as mc  # noqa: E402
from studio.map_view import _RAINBOW_ORDER  # noqa: E402

TICK_HZ = 30.0          # the app's playback tick, which advances the race (RaceController.tick)
MAP_WINDOW = mc.WINDOW  # 1440x900 logical, grabbed at mc.DPR


def _pixels(img) -> np.ndarray:
    """A QImage as an (h, w, 4) uint8 array (a copy: the QImage's buffer is not ours to keep)."""
    assert not img.isNull(), "an empty grab: the map is not laid out"
    img = img.convertToFormat(img.Format.Format_RGBA8888)
    buf = np.frombuffer(bytes(img.constBits()), dtype=np.uint8)
    return buf.reshape(img.height(), img.bytesPerLine())[:, :img.width() * 4] \
        .reshape(img.height(), img.width(), 4).copy()


def map_figures(app, w) -> dict:
    """{mode: {pairs, px, median_pct, max_pct}} — the share of the map a lap change repaints."""
    m, s = w.view.map, w.session
    w.view.tab_bar.setCurrentIndex(0)               # the map's page, in the default layout
    mc.settle(app)
    laps = sorted(s.valid_lap_ids())
    pairs = list(zip(laps, laps[1:], strict=False))
    out: dict = {}
    for mode in _RAINBOW_ORDER:
        m.set_rainbow_mode(mode)
        mc.settle(app, 3)
        if m._rainbow_mode != mode:                 # a channel this session cannot draw
            continue
        shares, px = [], 0
        for a, b in pairs:
            frames = []
            for lap in (a, b):
                m.set_current_lap(lap)
                mc.settle(app, 3)
                with m.grab_clean():
                    frames.append(_pixels(mc.grab(m.widget)))
            diff = np.any(frames[0] != frames[1], axis=2)
            px = diff.size
            shares.append(100.0 * float(diff.sum()) / px)
        out[mode] = {"pairs": len(pairs), "px": px, "median_pct": round(statistics.median(shares), 2),
                     "max_pct": round(max(shares), 2)}
    return out


def race_figures(session) -> dict:
    """The card over a whole race at the tick: what a slide between snapshots would move."""
    tracks = session.race_tracks()
    assert tracks is not None and tracks.ranked, "the demo must field a ranked race"
    coloured = frozenset(tracks.coloured)
    n = int(np.ceil(tracks.duration * TICK_HZ)) + 1
    prev_rows = prev_ladder = None
    snaps = reorders = reforms = ladder_changes = 0
    shown, hidden = [], []
    for k in range(n):
        st = lap_race.race_state(tracks, min(k / TICK_HZ, tracks.duration))
        rows, left_out = race_controller.pack_rows(st, coloured, None)
        lead = [r.laps[0] for r in rows]
        ladder = [r.lap_id for r in st.rungs]
        snaps += 1
        shown.append(len(rows))
        hidden.append(left_out)
        if prev_rows is not None:
            kept = set(lead) & set(prev_rows)
            if [x for x in prev_rows if x in kept] != [x for x in lead if x in kept]:
                reorders += 1
            if set(lead) != set(prev_rows):
                reforms += 1
            if ladder != prev_ladder:
                ladder_changes += 1
        prev_rows, prev_ladder = lead, ladder
    return {"laps": len(tracks.ids), "duration_s": round(float(tracks.duration), 1),
            "snapshots": snaps, "rows_min": min(shown), "rows_max": max(shown),
            "hidden_max": max(hidden), "reorders": reorders, "reforms": reforms,
            "ladder_changes": ladder_changes}


def main(argv=None) -> int:
    work = tempfile.mkdtemp(prefix="motion_refusals_")
    try:
        recording = os.path.join(work, "demo.mp4")
        make_demo.make(recording, render=False)
        app = mc.boot_app()
        w = mc.open_window(app, recording, size=MAP_WINDOW)
        mc._settle_ui(app, w)
        res = {"map": map_figures(app, w), "race": race_figures(w.session)}
        w.close()
        mc.settle(app, 2)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    for mode, r in res["map"].items():
        print(f"map  {mode:15s} {r['pairs']} lap changes, {r['px']} px: median {r['median_pct']:.2f} %, "
              f"largest {r['max_pct']:.2f} %")
    print("race " + ", ".join(f"{k} {v}" for k, v in res["race"].items()))
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
