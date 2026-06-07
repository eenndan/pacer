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
- **Speed + delta plots** — speed (top) and lap-vs-best delta (bottom) on ONE shared, x-linked
  x-axis: the dist/time toggle drives BOTH plots (distance = normalized-distance × best-lap
  distance, in metres; time = time-into-lap), so the same moment lands at the same x on both and
  the two cursors always line up vertically. Delta is aligned by normalized distance so its
  endpoint equals the laptime difference. The charts **auto-follow the current lap**: as playback
  (or a main-slider scrub) crosses a lap boundary they switch to the now-current lap vs best
  (the table `▶`/selection + map overlay stay coherent); a manual lap selection is preserved while
  paused and replaced only once playback moves on. An always-on **Δ/speed readout box** above the
  plots shows the current-moment Δ-to-best (priority) + speed; the delta plot has a **hover dot**
  that rides the curve under the mouse with its Δ value (independent of the playback cursor).
- **Lap table** — time / dist / entry speed, plus per-sector split columns S1…Sn once sectors are
  added. `▶` marks the playing lap; blue row = your selection; best lap shown in green; the
  **session-best split in each sector column is purple** (per-column min across valid laps). **Every
  column header is click-to-sort** by the underlying numeric value (asc/desc toggle); all highlights
  follow the laps across a sort. Default order is by lap number until you click a header.
- **Video** — GoPro `.mp4` with play/pause + a full-video scrub slider + an **audio mute/unmute
  toggle** (default muted); readout shows `t / speed / lap #`; synced both ways. The speed + delta
  **plot cursors are also draggable** — a fine, lap-scoped scrubber that seeks the video within the
  current lap (complements the slider). The map's red marker drag is **constrained to the current
  lap** so it never jumps to another lap across a spatial overlap.

How it works (key decisions, all done & verified):
- **Load/clean** (`session._clean`): trims the stationary GPS-spike lead-in/cool-down and
  bbox-filters off-track fixes.
- **Lap timing — GPS9 true wall clock** (`session._gps9_times`, the load default): lap time is the
  difference of two start/finish crossing instants, each already INTERPOLATED along the chord by
  the C++ core (`pacer::Split`: `t = t0 + f·(t1−t0)`), so accuracy is set by the per-sample TIME
  AXIS. The old `naive` axis spread each GPMF payload's MEDIA span over `i/n`; the GoPro media
  clock for the GPS track runs ~0.1 % fast (9.990 Hz measured), which systematically compressed
  every lap (~30 ms on the best lap). We now time off the GPS9 fix timestamps' true 10.000 Hz
  **wall-clock** spacing (the transponder's clock), re-anchored per contiguous run to the media
  time so video sync / chapter offsets are unchanged; degrades to naive when no GPS9 timestamp is
  present. `--interp` still opts into the C++ gradient-descent fit, validated and auto-rejected
  when it diverges on long sessions (it does — keep it opt-in).
- **Lap timing — transponder-calibrated GPS9 clock rate** (`session.GPS9_RATE_FACTOR = 0.999514`).
  The re-anchored gps9 axis was cross-checked against the kart's REAL lap-timing transponder
  (Daytona 24h 2026, the 0060 recording = CSV stint laps 298–358, parsed by
  `studio/transponder.py`). The app's 57 valid laps aligned 1:1 to the transponder (correlation
  **0.995**, anchored on the mutual best lap + the in/out-lap endpoints; every other sequence
  offset correlates ~0 ⇒ unambiguous). Residual app−transponder over 55 clean racing laps was
  mean **+0.029 s** / median +0.004 s / **std 0.158 s** — a small, consistent ~+0.05 % too-long
  rate on top of ±0.16 s irreducible GPS noise. Scaling the within-run GPS9 SPACING by this one
  clock-rate factor recenters the residual to mean **−0.005 s** and GENERALIZES (10-fold CV
  held-out mean **−0.0001 s**, RMS **0.161 s**). One cross-validated physical parameter, NOT a
  per-lap fudge; applied to spacing only so each run keeps its media-clock anchor (video sync
  intact). Re-derive via `studio/_calib.py` if the camera changes. The session-best lap's ~−0.19 s
  residual is a GPS-noise outlier (it cannot reach the transponder value without overfitting that
  one lap → an implausible 9.978 Hz) and is the irreducible limit of 10 Hz GPS — NOT fudged.
- **Lap distance — gap-aware** (C++ `SegmentDistance` in `pacer/laps/laps.cpp`, feeding both
  `GetLapDistance` and the per-lap `cum_distances`): normal segments use the GPS chord; a segment
  spanning a DROPOUT (point-to-point Δt > 0.35 s) uses the trapezoidal speed integral
  `½(v0+v1)·Δt` instead, clamped to ≥ chord. Chords across dropouts were cutting corners and
  under-counting (one 6 s hole = ~100 m short); this cut the valid-lap distance spread ~91 m → ~35 m
  (std 12.3 → 7.6 m) on the 0060 session, changing only the dropout laps so delta/sectors stay
  consistent.
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
  the delta endpoint == laptime diff; plotted vs s×best_distance (metres) in distance mode, or vs
  time-into-lap in time mode (`delta(ids, x_mode=…)` — same Δ y-values, only the x basis changes).
  The speed plot draws on the SAME x basis, so both plots share one x-axis and stay x-linked.
  `session.delta_at_time(t)` gives the current-moment Δ-to-best for the readout box (same
  normalized-distance alignment, so the box and the on-curve cursor agree).
- **Per-sector splits** (`session.lap_sector_splits`) — projects each sector line to a cum-distance
  on each lap and splits the lap time there → sums to the lap time for **every** lap (no dependence
  on fragile geometric crossing; no blanks/oversized values).
- **Timing-line edit** — handles are placed **freely** (no snap); dragging redraws live and
  re-segments the laps **once on release**.
- **Draggable plot-cursor scrub** (`plots_view` cursors → `session` conversion → `app` seek): both
  plot cursors are `movable` `InfiniteLine`s; dragging either seeks the video **within the lap the
  playhead is in**, clamped to that lap. `plots_view` stays pacer-free — it emits the raw plot-x +
  the SHARED axis mode (`scrubStarted`/`scrubMoved(x, mode)`/`scrubEnded`, `mode` ∈ `time|distance`;
  `delta` is kept as a readable alias of `distance` in the conversion helpers — same math); `app`
  converts via `session.media_time_at_plot_x` / `plot_x_at_media_time` (pure numpy on cached per-lap
  `(times, dists)`: time `t=lap_start+x`; **shared distance** `s=x/best_dist → dist_in_lap=s·lap_total
  → interp`). The two plots share ONE x-axis and are permanently x-linked, so the same media moment
  maps to the SAME x on both → the cursors always coincide (verified `|x_speed−x_delta|≈0`). Source
  of truth is the media time; both cursors + slider + map marker are placed from it ("two lines, one
  truth"). Seeks **coalesced to ≤1/30 Hz tick** (latest target wins), **pause on grab / resume iff
  was playing**; the feedback loop is gated (drag ignores the playback tick; `setValue`
  `_suppress`-guarded). Round-trip/clamp + cursor-coincide tests in `tests/test_scrub_conversion.py`;
  analysis numbers proven byte-identical (UI-only, same MD5 as the pre-change baseline).
- **Charts auto-follow the current lap** (`app._follow_current_lap`, UI-only): the speed + delta
  charts always show **whichever lap the playhead is in vs best**. The follow is an **edge check**
  on `session.lap_at_time(t)` inside the existing 30 Hz position path (`_apply_readout`, which runs
  on playback AND main-slider scrub) — it re-selects only on an **actual lap change** (O(1)/tick, a
  plot refresh only on the edge → no thrash), **holds** the last lap through a lead-in/between-laps
  `None` region (never blanks the charts), and re-selects through the **programmatic `table.select`**
  (signals blocked) so it emits **no seek** and never fights playback (the genuine-click→seek gate
  is untouched). A just-made manual selection (incl. a multi-lap comparison) is **preserved while
  paused** (`_followed_lap` is seeded to the seek's landing lap, so the static jump isn't an edge)
  and is replaced by `[current, best]` only **once playback moves on** into a different lap. Because
  the current lap is now always among the displayed laps, the **scrub cursor / Δ box / hover work in
  the followed lap** — superseding the earlier "scrub only works when the current lap is displayed"
  caveat. Verified: switch-count == boundaries crossed, zero extra seeks, lead-in hold, real-GUI
  play-through a boundary, two before/after PNGs; analysis MD5 byte-identical (UI-only). Pure-Python
  edge tests in `tests/test_studio_features.py`.
- **Live Δ/speed readout + hover dot**: an always-on box above the plots shows the
  **current-moment Δ-to-best (priority) + speed** (`app._update_diff_box` ← `session.delta_at_time`
  / `speed_at_time`), green when ahead of best / red when behind, updating live on playback and
  scrub. The delta plot has a **hover dot** (`ScatterPlotItem` + `TextItem` driven by
  `scene().sigMouseMoved`) that snaps to the nearest delta-curve sample under the mouse and labels
  its Δ value (+ distance/time there) — independent of the playback cursor, hidden on mouse-leave.
  The hover handler is a cheap nearest-index lookup on the cached curve arrays (no re-plot).
- **Lap-table sorting + session-best sectors** (`lap_table.py`, UI-only): the table uses a numeric
  sort key on every cell — a `_NumItem(QTableWidgetItem)` whose `__lt__` compares `Qt.UserRole`
  floats (so `"1:08.408"` sorts as 68.408 s, splits by their seconds, blanks/NaN last), with
  `setSortingEnabled(True)` and per-header asc/desc toggle; the chosen sort is remembered and
  re-applied across refreshes. The **purple per-sector session-best** is the per-column MINIMUM
  split across valid laps (`_best_split_per_sector_impl`); all visual state (green best lap, purple
  best-sector cells, the `▶` current-lap marker + bold) is keyed by **lap id** and re-applied after
  every sort/refresh, so highlights always follow the right lap and coexist (a purple cell inside the
  green best-lap row still reads purple). The blue selection stays Qt's own row background.
- **Sector lines on the charts** (`session.sector_plot_positions` → `plots_view.set_sector_lines` →
  `app._refresh_sector_lines`, UI-only): the sector BOUNDARIES (start/finish + each sector line) draw
  as subtle dotted vertical guide lines on BOTH the speed and delta plots, labelled `S/F`/`S1`/`S2`…
  near the top of the speed plot. Positions come from `session` (so `plots_view` stays pacer-free):
  each sector line's midpoint is projected onto the best lap's trace the SAME way the split times are
  measured (`sector_boundary_distances`), then mapped to the shared axis — `s×best_distance` (metres)
  in distance mode, time-into-best-lap (seconds) in time mode. They update LIVE as sectors are
  added/moved/reset and reposition on the dist/time toggle (`plots_view.modeChanged` → app re-pushes);
  drawn behind the curves + cursor (`zValue=-5`) so they never obscure them. No sectors → no lines.
- **Lap-scoped marker drag** (`session.nearest_index_in_lap`/`nearest_time_in_lap` → `map_view`,
  UI-only): the red map marker's drag resolves to the nearest point WITHIN the current lap (pure
  numpy on the lap's cached local-metre points) and clamps to that lap's time window, so it scrubs
  smoothly inside the one lap and never snaps to another lap where laps overlap spatially. Outside a
  valid lap (lead-in) it falls back to the whole-trace nearest; playback-driven marker movement still
  crosses laps normally.
- **Audio mute toggle** (`video_view.py`, UI-only): a `QAudioOutput` (volume 0.6) with a mute/unmute
  button (🔇/🔊). **Default = muted on launch** (telemetry tool — no surprise 4K audio); the button
  flips `QAudioOutput.setMuted`.
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
  `transponder.py` (lap-timing-CSV parser, pure-Python, no pacer — the GPS9 rate calibration's
  ground-truth) · `gapfill.py` (GPS-gap reconstruction, pure numpy) · `reference.py` + `mk_centerline.json` /
  `build_reference.py` (georeferenced fallback centerline) · `map_view.py` · `plots_view.py` ·
  `lap_table.py` · `video_view.py` · `app.py` (wiring) · `diagnose.py` / `denoise_check.py`
  (`--gaps` renders the filled map + prints gap metrics) / `_smoke.py` / `_analysis_dump.py`
  (dumps every analysis value + an MD5, the UI-only byte-identity proof) (tools). Tests:
  `tests/test_gapfill.py` + `tests/test_scrub_conversion.py` + `tests/test_studio_features.py` (all
  pure-Python, fast; the last covers the F1 numeric sort key, F3 lap-scoped nearest, F5 per-column
  session-best min, and the **chart auto-follow lap-change edge** — switch only on the edge, hold on
  a `None` region, no seek emitted). The two studio Python tests are now also registered with CTest (`tests/
  CMakeLists.txt`), so `pixi run test` runs them with the C++ suite. `_probe.py` / `_bench_cursor.py`
  are untracked scratch.

## Next steps / backlog (prioritized for a fresh agent)
1. **More tracks** — `tracks.py` has only Daytona MK; add entries and/or real auto-detection
   (the user flagged other-track support as the planned next expansion).
2. **Persist sector/start-line config per file** — a sidecar JSON so edits survive reloads.
3. **Tests** — `tests/test_gapfill.py` (gap detection / borrow / spline / continuity),
   `tests/test_scrub_conversion.py` (cursor x↔media-time round-trip + clamp, every mode),
   `tests/test_studio_features.py` (F1 sort key / F3 lap-scoped nearest / F5 per-column min) and
   `tests/test_chapters.py` (GoPro filename parse, sibling grouping/ordering, global↔chapter map)
   exist. Still TODO: pure-Python tests for the rest of `session.py` (`_clean`, `valid_lap_ids`,
   delta endpoint==laptime-diff, `lap_sector_splits` sum==lap-time, `sector_plot_positions`).
4. **Multi-file chaptered sessions — DONE.** Opt-in (`--full`/`--chaptered` + File ▸ Load full
   recording) discovers + chains sibling chapters (`chapters.py`); telemetry rides one continuous
   global clock via `SequentialGPSSource` (verified on recording 0060: 57 valid laps vs 18 for
   chapter 1 alone, monotonic axis, the seam lap is one correct ~68.8 s / 1073 m lap); video
   switches source on a cross-chapter seek and **auto-advances** at end-of-media (`video_view.py`),
   with a global slider/position. Default single-file load stays byte-identical to main. Known
   limitation: a brief reopen hitch when the video source switches at a seam.
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
