# pacer studio — status & handoff

`studio/` is a local **PySide6 + pyqtgraph** desktop app on the C++ `pacer` core (nanobind), for
analysing GoPro race telemetry. This is the handoff doc: current state, how to run/verify, the
architecture an agent must respect, and the prioritized backlog. Read it + [README.md](README.md)
+ the `pacer-studio-app-direction` memory to take over.

**Branch:** `fill-gps-gaps` (off `removing-gps-noise`, off `better-app`; local, not pushed).
**Run:** `pixi run studio -- <file.MP4>`.
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
- **Video** — GoPro `.mp4` with play/pause + a full-video scrub slider; readout shows
  `t / speed / lap #`; synced both ways. The speed + delta **plot cursors are also draggable** — a
  fine, lap-scoped scrubber that seeks the video within the current lap (complements the slider).

How it works (key decisions, all done & verified):
- **Load/clean** (`session._clean`): trims the stationary GPS-spike lead-in/cool-down and
  bbox-filters off-track fixes. Default **naive** per-frame timing; `--interp` opts into the C++
  gradient-descent fit but it is validated and auto-rejected when it diverges on long sessions.
- **GPS quality gating** (`session._gate_quality`): the C++ core now surfaces the GoPro **GPS9
  DOP + fix-type** on `GPSSample` (`pacer/datatypes/datatypes.hpp`, parsed in
  `pacer/gps-source/gps-source.cpp`, bound as `.dop`/`.fix`). At load we drop fixes with no 3D
  lock (`fix<3`) or poor geometry (`dop>10`) — on the real session ~12% of raw fixes, but ~69%
  of those are the stationary lead-in trimmed anyway, so only ~4% of driving data. The GPS5
  stream carries neither field → sentinels (`fix=-1`, `dop=-1.0`) mean "unknown" and are KEPT.
- **GPS track smoothing** (`session._smooth_track`, window `SMOOTH_WINDOW=13`): an edge-correct
  boxcar moving average on lat/lon/alt — the notebook's denoiser (`notebooks/interpolation.ipynb`
  `_smooth`, proven in `noise-investigation.ipynb`), tuned up from w=9. Applied ONCE at load to
  the SOURCE coordinates so the trace AND every C++-derived quantity (cum_distances, segmentation,
  delta, sector splits) use the same smoothed track. Smoothed within gap-free runs only (never
  across chapter/dropout gaps). Verified (`studio/denoise_check.py`): ~39% less HF cross-track
  jitter, ~91% less heading jitter, lap-to-lap racing-line signal preserved, corner apexes not
  clipped (`w>=21` starts cutting corners; w=13 tracks the raw apex). O(n), never per-frame.
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
- **Draggable plot-cursor scrub** (`plots_view` cursors → `session` conversion → `app` seek): both
  plot cursors are `movable` `InfiniteLine`s; dragging either seeks the video **within the lap the
  playhead is in**, clamped to that lap. `plots_view` stays pacer-free — it emits the raw plot-x +
  axis (`scrubStarted`/`scrubMoved(x, mode)`/`scrubEnded`, `mode` ∈ `time|distance|delta`); `app`
  converts via `session.media_time_at_plot_x` / `plot_x_at_media_time` (pure numpy on cached per-lap
  `(times, dists)`: time `t=lap_start+x`; distance `interp(x; dists,times)`; delta
  `s=x/best_dist → dist_in_lap=s·lap_total → interp`). Source of truth is the media time; both
  cursors + slider + map marker are placed from it ("two lines, one truth"). Seeks **coalesced to
  ≤1/30 Hz tick** (latest target wins), **pause on grab / resume iff was playing**; the feedback
  loop is gated (drag ignores the playback tick; `setValue` `_suppress`-guarded). Measured
  ~0.12 ms/move + ~0.12 ms/tick → live seeking kept (no fall-back). Round-trip/clamp tests in
  `tests/test_scrub_conversion.py`; analysis numbers proven byte-identical (UI-only).
- **Performance** — 4K HEVC decodes ~61 fps (VideoToolbox HW). UI sync runs on a ~30 Hz `QTimer`
  off the video present path; plot curves are downsampled + clipped, antialias off, autorange
  frozen after refresh; the map draws ≤2 laps. Smooth incl. with a lap selected (cursor 56.5→1.1 ms).
- **GPS gap reconstruction (MAP ONLY)** (`session.lap_trace_segments` → `gapfill.py`): where a
  lap's GPS has an interior DROPOUT (a run dropped by the quality gate, or a genuine outage), the
  trace used to draw a straight CHORD across the hole. Now each lap is drawn as MEASURED runs +
  reconstructed INFERRED fills. A gap is an interior point-to-point time jump > ~0.35 s (≥3 missing
  samples @ 10 Hz); the lap's open start/finish ends are not gaps. Each gap is filled by, in order:
  (1) **cross-lap borrow** (PRIMARY) — the track is identical every lap, so take a donor lap's
  sub-polyline between the points nearest the two gap mouths and pin it with a similarity transform
  (rotation+uniform scale, both endpoints exact) → the real corner shape, connected continuously;
  the donor with the smallest endpoint error (and a sane arc-length ratio) wins. (2) **reference
  centerline** (FALLBACK, only if NO lap covers the section) — a georeferenced Daytona MK centerline
  (`reference.py` + `mk_centerline.json`, traced from `gmaps_pict.png`, similarity-ICP aligned to the
  GPS aggregate; fit residual ~1 m mean). (3) **spline** for very short gaps / when borrow misses.
  Inferred segments draw **dashed + dimmed** (`map_view._inferred_pen`) so real GPS vs reconstruction
  is always distinguishable. Per-lap segments are cached (`_seg_cache`) — built once, never per frame,
  cleared on re-segment. On `GX010060.MP4`: 7 gaps / 222 m of chord across 5 laps → 6 borrow + 1
  spline, 0 reference needed, 0 unfilled. **MAP-ONLY guarantee proven byte-identical** (same JSON
  MD5) for valid_lap_ids, lap times, delta endpoints, sector splits, cum-distances vs the base
  branch — `gapfill`/`reference` are pure numpy and `lap_trace_segments` reads the unchanged
  kept-point arrays; no analysis path is touched.

## Run & verify
- `pixi run studio -- <file.MP4>` (or `python -m studio [files]`; `--interp` to try interpolation).
- `pixi run python -m studio.diagnose -- <file.MP4> [--interp] [--clean]` — headless stats / root-causing.
- `pixi run python -m studio.denoise_check -- <file.MP4> [--window N] [--tag T] [--notebook-ref]` —
  offscreen render of the map (best / selected / overlaid laps) to PNG + numeric jitter/signal
  metrics; the feedback loop for tuning `SMOOTH_WINDOW`. `--window 1` = raw baseline.
- `pixi run python -m studio._smoke` — headless full-window build (offscreen); prints `SMOKE OK`.
- The GUI needs a display / non-sandboxed run; use `QT_QPA_PLATFORM=offscreen` for headless checks.

## Architecture an agent MUST respect
- Trace + timing lines live in **local metres** (`cs.local`); `set_coordinate_system` precedes
  `pick_random_start`/`update`. Sectors write-back is wholesale: `laps.sectors = pacer.Sectors(...)`.
- **`session.py` is the only module that drives the pacer pipeline; `tracks.py` is the only other
  file that names `pacer` (pure geometry).** Keep `map_view`/`plots_view`/`lap_table`/`app` free of
  `pacer`. The gap-fill helpers `gapfill.py` and `reference.py` are **pure numpy** (no `pacer`) —
  `session.lap_trace_segments` feeds them the cached per-lap arrays; `map_view` calls only that.
- `pacer` is GPMF/GoPro `.MP4` only (`.dat` reader is not bound). It supplies the telemetry time
  axis; the app brings its own video player.
- **Perf invariants — do not regress:** the 30 Hz tick decouple (`app._on_position` only stores the
  time; `app._tick` applies); plot curves downsampled+clipped + antialias off + autorange frozen
  after `refresh`; map draws only best+current lap; clear per-lap caches in `set_timing_lines`.
  **Plot-cursor scrub seeks are coalesced to ≤1 per tick** (latest target wins) — never seek
  per-mouse-move; the drag↔`positionChanged` feedback loop is gated (`_user_dragging`/`_suppress`).
- Module map: `session.py` (data/analysis — only pacer user) · `tracks.py` (track registry) ·
  `gapfill.py` (GPS-gap reconstruction, pure numpy) · `reference.py` + `mk_centerline.json` /
  `build_reference.py` (georeferenced fallback centerline) · `map_view.py` · `plots_view.py` ·
  `lap_table.py` · `video_view.py` · `app.py` (wiring) · `diagnose.py` / `denoise_check.py`
  (`--gaps` renders the filled map + prints gap metrics) / `_smoke.py` (tools). Tests:
  `tests/test_gapfill.py` + `tests/test_scrub_conversion.py` (both pure-Python, fast — the latter
  round-trips the cursor x↔media-time conversions for every mode + clamping). `_probe.py` /
  `_bench_cursor.py` are untracked scratch.

## Next steps / backlog (prioritized for a fresh agent)
1. **More tracks** — `tracks.py` has only Daytona MK; add entries and/or real auto-detection
   (the user flagged other-track support as the planned next expansion).
2. **Persist sector/start-line config per file** — a sidecar JSON so edits survive reloads.
3. **Tests** — `tests/test_gapfill.py` (gap detection / borrow / spline / continuity) and
   `tests/test_scrub_conversion.py` (cursor x↔media-time round-trip + clamp, every mode) exist.
   Still TODO: pure-Python tests for the rest of `session.py` (`_clean`, `valid_lap_ids`, delta
   endpoint==laptime-diff, `lap_sector_splits` sum==lap-time). Fast, no GUI; a real regression guard.
4. **Multi-file chaptered sessions** — verify `SequentialGPSSource` chaining + the combined time
   axis on a real chaptered GoPro recording.
5. **Polish** — keyboard shortcuts (space=play, ←/→ step), theming/layout, an optional snap-to-track
   *toggle* (default is now free), trailing-cooldown trimming, expose `_clean` thresholds in UI.
   Also: the MK reference centerline's INFIELD switchbacks (`mk_centerline.json`) are an approximate
   hand-trace — fine for the fallback (outer-loop corners, where long gaps happen, fit ~1 m), but
   tighten the infield if the reference is ever actually needed there (re-run `build_reference.py`).
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
