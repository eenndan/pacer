# pacer studio — status & handoff

`studio/` is a local **PySide6 + pyqtgraph** desktop app on the C++ `pacer` core (nanobind), for
analysing GoPro race telemetry. This is the handoff doc: current state, how to run/verify, the
architecture an agent must respect, and the prioritized backlog. Read it + [README.md](README.md)
+ the `pacer-studio-app-direction` memory to take over.

**Branch:** `better-app` (local, not pushed). **Run:** `pixi run studio -- <file.MP4>`.
Validated end-to-end on `/Users/daniil/Desktop/D24/GX010060.MP4` (Daytona MK, ~28 min 4K HEVC →
18 valid laps @ ~69 s), user-confirmed.

## Current state — feature-complete for the initial scope

Panels (module map in README):
- **Map** — best lap (faint) + current/playing lap (highlighted); freely-draggable start + sector
  timing lines; red video marker. The full all-laps trace is intentionally not drawn.
- **Speed + delta plots** — speed-vs-distance (x-axis toggle to time-into-lap) and lap-vs-best
  delta. Delta is aligned by normalized distance so its endpoint equals the laptime difference.
- **Lap table** — time / dist / entry speed, plus per-sector split columns S1…Sn once sectors are
  added. `▶` marks the playing lap; blue row = your selection; best lap shown in green.
- **Video** — GoPro `.mp4` with play/pause + scrub; readout shows `t / speed / lap #`; synced both ways.

How it works (key decisions, all done & verified):
- **Load/clean** (`session._clean`): trims the stationary GPS-spike lead-in/cool-down and
  bbox-filters off-track fixes. Default **naive** per-frame timing; `--interp` opts into the C++
  gradient-descent fit but it is validated and auto-rejected when it diverges on long sessions.
- **Track-aware start/finish** (`tracks.py`): detects the track by trace centroid and sets a fixed
  start/finish line from absolute lat/lon. One entry — **Daytona Milton Keynes**
  (A=52.04031,−0.78487 · B=52.04020,−0.78460 · centroid ≈52.0403,−0.7847). Unknown tracks fall
  back to `pick_random_start`.
- **Lap validity** — adaptive: a lap counts if its time is within [0.5, 1.6]× the median lap time
  (drops partials / out-laps).
- **Delta-to-best** (`session.delta`) — aligns laps by **normalized distance fraction** s∈[0,1] so
  the delta endpoint == laptime diff; plotted vs s×best_distance (metres), plain-seconds y-axis.
- **Per-sector splits** (`session.lap_sector_splits`) — projects each sector line to a cum-distance
  on each lap and splits the lap time there → sums to the lap time for **every** lap (no dependence
  on fragile geometric crossing; no blanks/oversized values).
- **Timing-line edit** — handles are placed **freely** (no snap); dragging redraws live and
  re-segments the laps **once on release**.
- **Performance** — 4K HEVC decodes ~61 fps (VideoToolbox HW). UI sync runs on a ~30 Hz `QTimer`
  off the video present path; plot curves are downsampled + clipped, antialias off, autorange
  frozen after refresh; the map draws ≤2 laps. Smooth incl. with a lap selected (cursor 56.5→1.1 ms).

## Run & verify
- `pixi run studio -- <file.MP4>` (or `python -m studio [files]`; `--interp` to try interpolation).
- `pixi run python -m studio.diagnose -- <file.MP4> [--interp] [--clean]` — headless stats / root-causing.
- `pixi run python -m studio._smoke` — headless full-window build (offscreen); prints `SMOKE OK`.
- The GUI needs a display / non-sandboxed run; use `QT_QPA_PLATFORM=offscreen` for headless checks.

## Architecture an agent MUST respect
- Trace + timing lines live in **local metres** (`cs.local`); `set_coordinate_system` precedes
  `pick_random_start`/`update`. Sectors write-back is wholesale: `laps.sectors = pacer.Sectors(...)`.
- **`session.py` is the only module that drives the pacer pipeline; `tracks.py` is the only other
  file that names `pacer` (pure geometry).** Keep `map_view`/`plots_view`/`lap_table`/`app` free of `pacer`.
- `pacer` is GPMF/GoPro `.MP4` only (`.dat` reader is not bound). It supplies the telemetry time
  axis; the app brings its own video player.
- **Perf invariants — do not regress:** the 30 Hz tick decouple (`app._on_position` only stores the
  time; `app._tick` applies); plot curves downsampled+clipped + antialias off + autorange frozen
  after `refresh`; map draws only best+current lap; clear per-lap caches in `set_timing_lines`.
- Module map: `session.py` (data/analysis — only pacer user) · `tracks.py` (track registry) ·
  `map_view.py` · `plots_view.py` · `lap_table.py` · `video_view.py` · `app.py` (wiring) ·
  `diagnose.py` / `_smoke.py` (tools). `_probe.py` / `_bench_cursor.py` are untracked scratch.

## Next steps / backlog (prioritized for a fresh agent)
1. **More tracks** — `tracks.py` has only Daytona MK; add entries and/or real auto-detection
   (the user flagged other-track support as the planned next expansion).
2. **Persist sector/start-line config per file** — a sidecar JSON so edits survive reloads.
3. **Tests** — pure-Python unit tests for `session.py`: `_clean`, `valid_lap_ids`, delta
   endpoint==laptime-diff, `lap_sector_splits` sum==lap-time. Fast, no GUI; a real regression guard.
4. **Multi-file chaptered sessions** — verify `SequentialGPSSource` chaining + the combined time
   axis on a real chaptered GoPro recording.
5. **Polish** — keyboard shortcuts (space=play, ←/→ step), theming/layout, an optional snap-to-track
   *toggle* (default is now free), trailing-cooldown trimming, expose `_clean` thresholds in UI.
6. **Perf headroom (only if needed on longer sessions)** — a bulk `lap→numpy` accessor in the C++
   bindings to drop per-point Python loops; `useOpenGL` for the pyqtgraph views.
7. **Housekeeping** — delete scratch `studio/_probe.py` + `studio/_bench_cursor.py` (`rm` is blocked
   in the agent sandbox, so the user must); decide whether to push `better-app` / open a PR.

## How work is done here
Autonomous background **Workflows** (full-autonomy perms already in `.claude/settings.local.json`):
each phase implements → verifies headlessly (often driving the app via handlers + measuring numbers)
→ adversarially reviews → commits to `better-app`. Agents can launch the GUI (non-sandboxed) for a
crash-smoke but cannot perceive smoothness/visuals — the final visual confirmation is the human's.
Keep that loop: define numeric pass criteria so a fix isn't "done" until they hold.
