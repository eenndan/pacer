# pacer studio — progress & plan

Working backlog for the `studio/` desktop app. Read this + [README.md](README.md) +
the `pacer-studio-app-direction` memory to resume in a fresh session.

## Status (done)

- Greenfield PySide6 + pyqtgraph app on the C++ `pacer` core (nanobind). Panels: video, track
  map (draggable start/sector lines), speed + lap-vs-best delta plots, lap table. See README.
- Video↔telemetry sync via `QMediaPlayer.positionChanged` (frame-accurate `QVideoSink` available;
  proven by `spike_video_sync.py`).
- **Debugged on real file `GX010060.MP4`** (see `diagnose.py`): default to naive timing (C++
  interpolation diverges on long sessions, auto-rejected via `--interp`), `_clean` trims the
  stationary GPS-spike lead-in, the track-aware start line (B1) widened MODESTLY only to catch a
  pass the short exact segment missed, adaptive (median-band) lap validity. Result: 18 valid
  flying laps @ ~68.4–70.8 s (was 8 garbage laps).

## Open bugs (fix first)

### B1 — track-aware start/finish line (was: mis-placed/over-long regression)
Per the user, the real goal: **detect the track from GPS coordinates and use a FIXED,
track-correct start/finish line** instead of `pick_random_start` + 3× `_widen` (which regressed
placement after `_clean` moved the median point and over-lengthened the line). Focus on **one
track first: Daytona Milton Keynes** (lat ≈ 52.040, lon ≈ −0.785 — see the GPSSample in
notebooks/interpolation.ipynb cell output).

Reference layout/images: `/Users/daniil/Desktop/Tracks/MK/` →
`Daytona-Milton-Keynes_Aerial.jpg`, `Link-Cliff-Plan.pdf` (official circuit plan),
`gmaps_sat.png`, `gmaps_pict.png`. The aerial: the **main outdoor circuit** is centre/right;
**start/finish is on the main straight by the paddock** (centre-left, near the white tent);
a separate indoor/junior serpentine loop (far left) should be ignored.

**Design:**
- New `studio/tracks.py`: a tiny registry. One entry to start — MK Daytona — with a reference
  centroid (for detection) and a **start/finish line as two absolute lat/lon points** (a track
  property, not derived per-session).
- `Session.load`: compute the trace centroid; if within ~1 km of a registry track, set the start
  line from that track's lat/lon (via `cs.local`) instead of `pick_random_start`; else fall back.
  This makes lap timing consistent and correct across any video of the same track.
- Keep the line draggable to fine-tune; drop the blanket 3× `_widen` (or keep a modest
  track-spanning width derived from local track width).

**MK start/finish line — CONFIRMED (user, 2026-06-06; do not re-derive):**
A = (lat 52.04031, lon −0.78487), B = (lat 52.04020, lon −0.78460). Store in `studio/tracks.py`
as the Daytona MK entry; detect MK by trace centroid within ~1.5 km of (52.0403, −0.7847); convert
both points via `cs.local` and set `sectors.start_line`; keep it draggable. Widen modestly only if
some laps miss it. (`session.Session.load`, new `studio/tracks.py`; retire `_widen`/
`pick_random_start` for known tracks.) Auto-detecting *other* tracks is a later expansion.

### B2 — speed/delta show only ~5% for non-best laps
`pacer.Lap.resample` aborts at the first reference timing line the candidate lap misses (its inner
`while` advances the index to the end → all later lines break). Best-onto-best works; others
truncate.
**Fix:** replace `Session.delta`'s timing-line resample with **arc-length interpolation**:
per lap take `(cum_distances, speed)` and `(cum_distances, elapsed_time)` from `get_lap`; build a
common distance grid (best lap's, or uniform 0..max); `np.interp` speed and time onto it; delta =
`time_lap(grid) - time_best(grid)`. Full-length, robust, no dependence on `resample`.
(`session.delta`, `plots_view.refresh`.)

## Requested features

- **F1 — select lap → jump video.** On lap-table selection (or map highlight), seek the video to
  that lap's `start_timestamp`. (`app._on_laps_selected` → `video.seek`; use the primary/first
  selected lap.)
- **F2 — readout under the video:** current time, speed (km/h), lap number. (New label in
  `video_view`; driven by `positionChanged`; speed/lap from `session` at the current time.)
- **F3 — clear "current lap" indication.** As the video plays, find the lap whose time window
  contains `t`; show its number in the readout, highlight that row in the table and/or its trace on
  the map. (`session.lap_at_time(t)`; wire in `app._on_position`.)

## P2 (polish, from earlier)

- Per-sector split times in the lap table (`Laps.sector_time/sector_entry_speed`; needs the
  per-lap↔sector index mapping the C++ table does).
- Snap dragged start/sector handles to the nearest trace point (`session.nearest_index`).
- Distance/time x-axis toggle on the speed plot.

## Beyond / backlog

- Robust start/finish detection for the default start line (autocorrelation of the trace, or
  detect the most-crossed line) instead of `pick_random_start`.
- Persist sector/start-line config per file (sidecar JSON), so dragging survives reloads.
- Multi-file chaptered sessions: verify `SequentialGPSSource` chaining + the combined time axis.
- A bulk `lap → numpy` accessor in the C++ bindings to drop per-point Python loops (perf on long
  sessions; currently fine at ~16k points).
- Tests for `session.py` (lap validity, delta series lengths, clean()) — pure-Python, fast.
- Theming/layout polish; keyboard shortcuts (space=play, ←/→ = step).
- Tune `_clean` thresholds; expose them; handle the trailing cool-down better.

## How to verify

- `pixi run python -m studio.diagnose -- <file.MP4> [--interp] [--clean]` — headless stats.
- `pixi run python -m studio._smoke` — headless build of the full window (offscreen).
- `pixi run studio -- <file.MP4>` — launch (GUI needs a display / non-sandboxed run).

## Key facts / gotchas (for a fresh-session LLM)

- Timing lines + trace are in **local meters** (`cs.local`); `set_coordinate_system` must precede
  `pick_random_start`/`update`. Sectors write-back is wholesale: `laps.sectors = pacer.Sectors(...)`.
- `pacer` is GPMF/GoPro `.MP4` only (`.dat` not bound). It supplies the telemetry time axis; the app
  brings its own video player.
- Interpolation is opt-in and validated; default is naive per-frame timing.
- `session.py` is the only file that touches `pacer`; keep it that way.
- Throwaway `_probe.py` is not committed; `_smoke.py` is the kept self-test.
