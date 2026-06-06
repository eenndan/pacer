# pacer studio

A local **PySide6 + pyqtgraph** desktop app for race-telemetry analysis — a greenfield UI
on top of the existing C++ `pacer` core (reused via its nanobind Python bindings). Chosen
for a single-language, LLM-editable codebase that still nails draggable map handles and
frame-accurate video↔telemetry sync (all in Python — see [the spike](spike_video_sync.py)).

## Run

```bash
pixi run studio                              # short demo clip (hero6 sample, map+video only)
pixi run studio -- /path/to/GX010060.MP4     # your GoPro session (multi-lap)
pixi run studio -- a.MP4 b.MP4               # chaptered recording (chained in order)
pixi run studio -- --interp session.MP4      # opt in to C++ timestamp interpolation
```

First `pixi run studio` resolves the env once (installs `pyside6`/`pyqtgraph` from the
manifest). Equivalent without pixi: `python -m studio [files]`.

Diagnose a file headlessly (sample stats, GPS noise, time axis, lap segmentation):

```bash
pixi run python -m studio.diagnose -- /path/to/file.MP4 [--interp] [--clean]
```

## Layout

```
┌──────────────┬───────────────────────────┐
│  VideoView   │   MapView (track + lines) │   video ⇄ telemetry sync:
├──────────────┼───────────────────────────┤   • video plays → red map marker sweeps
│  LapTable    │   PlotsView (speed/delta) │   • drag the marker → video seeks
└──────────────┴───────────────────────────┘   • drag start/sector lines → re-segment laps
```

## Modules (one responsibility each)

| File | Role |
|------|------|
| [session.py](session.py) | Loads GPMF → `pacer.Laps`; exposes trace/lap/delta arrays + timing-line write-back. Owns the load/segmentation pipeline (primary `pacer` user). |
| [tracks.py](tracks.py) | Registry of known tracks (Daytona MK); detects the track by centroid and gives its fixed start/finish line. The only other module that names `pacer` (geometry only). |
| [video_view.py](video_view.py) | `QMediaPlayer` + `QVideoWidget`; emits `positionChanged(s)`, exposes `seek(s)`. |
| [map_view.py](map_view.py) | Best lap (faint) + current/playing lap (highlighted) + draggable `TargetItem` timing lines + video marker. The full all-laps trace is intentionally not drawn (perf + clarity). |
| [plots_view.py](plots_view.py) | Speed-vs-distance + lap-vs-best delta for the selected laps; downsampled/clipped curves + a time cursor. |
| [lap_table.py](lap_table.py) | Lap times/distances; multi-select rows to choose laps to compare. |
| [app.py](app.py) | Assembles panels in splitters and wires the cross-panel signals. |

## Gotchas / notes

- **Coordinate space:** the trace *and* the timing lines live in **local meters** (`cs.local`).
  `pick_random_start()` / `update()` must run **after** `set_coordinate_system()`. (See
  `pacer/laps/laps.cpp` — `Update` converts the lines back to GPS internally for crossing tests.)
- **Sectors write-back:** assign wholesale — `laps.sectors = pacer.Sectors(start_line=…, sector_lines=[…])` — then `laps.update()`. (Verified to propagate across the binding.)
- **Data cleaning (`session._clean`):** real GoPro sessions have a stationary lead-in where
  GPS isn't locked and spikes wildly (jumps up to ~80 km). We trim the non-moving lead-in/cool-down
  and drop lone teleport glitches. Without this the trace, map zoom, and lap segmentation all break.
- **Timing is naive by default.** The C++ `interpolate_timestamps` diverges on long/noisy sessions
  (compresses lap times, overruns the video). `--interp` opts in, but the result is validated
  (monotonic + within the video duration) and silently falls back to naive if it's bad.
- **Lap validity is adaptive** (`session.valid_lap_ids`): laps within a band around the median lap
  time, so short double-crossings of the start line don't pollute the "best" lap.
- **Video sync** uses `QMediaPlayer.positionChanged`. For sub-frame precision, `QVideoSink.videoFrameChanged` / `QVideoFrame.startTime()` are available (the spike measured them frame-accurate, ~29 ms vs pacer's clock).
- **Sources:** GPMF/GoPro `.MP4` only — the u-blox `.dat` reader isn't bound yet. pacer supplies the telemetry time axis; the app brings its own video player (pacer doesn't decode pixels).
- `_smoke.py` is a headless self-test: `python -m studio._smoke`.

## Next ideas

- Distance/time toggle on the speed plot; per-sector split times in the lap table.
- Snap dragged sector handles onto the nearest trace point.
- Bind a bulk `lap → numpy` accessor in the core to drop the per-point Python loops.
