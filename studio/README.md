# pacer studio

A local **PySide6 + pyqtgraph** desktop app for race-telemetry analysis — a greenfield UI
on top of the existing C++ `pacer` core (reused via its nanobind Python bindings). Chosen
for a single-language, LLM-editable codebase that still nails draggable map handles and
frame-accurate video↔telemetry sync (all in Python — see [the spike](dev/spike_video_sync.py)).

## Run

```bash
pixi run studio                              # short demo clip (hero6 sample, map+video only)
pixi run studio -- /path/to/GX010060.MP4     # one chapter only (DEFAULT — single-file, as before)
pixi run studio -- --full /path/to/GX010060.MP4  # opt-in: discover + chain ALL sibling chapters
pixi run studio -- a.MP4 b.MP4               # explicit chaptered recording (chained in order)
```

A long GoPro recording is split, at a file-size limit, into **chapters** (`GX<CC><NNNN>.MP4`:
prefix, 2-digit chapter `CC`, 4-digit recording `NNNN`). By default opening one chapter loads
only that file. Pass **`--full`** (or `--chaptered`), or use **File ▸ Load full recording** in
the UI, to discover the sibling chapters (same recording `NNNN`, same folder, ordered by `CC`)
and chain them into one continuous session — see [Chaptered sessions](#chaptered-sessions).

First `pixi run studio` resolves the env once (installs `pyside6`/`pyqtgraph` from the
manifest). Equivalent without pixi: `python -m studio [files]`.

Diagnose a file headlessly (sample stats, GPS noise, time axis, lap segmentation):

```bash
pixi run python -m studio.dev.diagnose -- /path/to/file.MP4 [--clean]
```

Render the map offscreen to PNG + print jitter/signal metrics (the GPS-denoise feedback loop;
`--window N` overrides `SMOOTH_WINDOW`, `--window 1` = raw baseline, `--notebook-ref` adds the
upstream notebook's w=9 reference overlay):

```bash
pixi run python -m studio.dev.denoise_check -- /path/to/file.MP4 [--window N] [--tag T] [--notebook-ref] [--gaps]
```

`--gaps` additionally prints per-lap GPS-gap reconstruction metrics (chord metres, borrow vs
reference vs spline fill) and renders the gap-filled map (measured solid + inferred dashed). Pure-Python
gap-fill unit tests live in [`tests/test_gapfill.py`](../tests/test_gapfill.py) (`python tests/test_gapfill.py`).

## Layout

```
┌──────────────┬───────────────────────────┐
│  VideoView   │   MapView (track + lines) │   video ⇄ telemetry sync:
├──────────────┼───────────────────────────┤   • video plays → red map marker sweeps
│  LapTable    │   PlotsView (speed/delta) │   • drag the marker → video seeks
└──────────────┴───────────────────────────┘   • drag a plot cursor → scrub within the lap
                                                • drag start/sector lines → re-segment laps
                                                  (sector lines also drawn on the charts)
                                                • click a lap-table header → sort numerically
                                                • 🔇/🔊 → mute/unmute the clip audio
```

## Modules (one responsibility each)

| File | Role |
|------|------|
| [session.py](session.py) | Orchestrates the load (via `ingest.py`) into `pacer.Laps`; exposes trace/lap/delta arrays + timing-line write-back. Owns the segmentation/analysis pipeline (primary `pacer` user). |
| [ingest.py](ingest.py) | The GoPro/GPMF **data-loading layer** (one of the three modules that may name `pacer`): builds the `SequentialGPSSource` chain over one or more chapters and reads the raw GPS+IMU streams on the continuous global clock. The load path is **`read_recording`** — single-pass: one shared chain, so each chapter MP4 is opened/GPMF-parsed ONCE for both GPS and IMU (`read_gpmf`/`read_imu` remain for dev scripts). Returns raw samples + per-chapter durations; `session.py` cleans/smooths/segments on top. A data/control-layer module, not a view. |
| [_signal.py](_signal.py) | Pure-numpy **signal/clean helpers** shared by the session pipeline and the g-meter: the edge-corrected boxcar smoother, gap segmentation, the GPS quality gate, and the real-lap band filter. Pacer-free by contract (no `pacer`, no Qt). |
| [tracks.py](tracks.py) | Registry of known tracks (Daytona MK); detects the track by centroid and gives its fixed start/finish line. One of the three modules that name `pacer` (geometry only). |
| [dev/transponder.py](dev/transponder.py) | Pure-Python (no `pacer`), **dev/validation-only**: defensive parser for a lap-timing **transponder CSV** (the ground truth the GPS9 timing is **validated** against, out of sample, by [`dev/_validate_wallclock.py`](dev/_validate_wallclock.py)). Reads only the `Lap` + `Lap Time` columns (the later columns embed commas/quotes). A reference **input** only — the CSV is never committed. |
| [video_view.py](video_view.py) | The player **shell**: the transport row (play/pause + the **mute/unmute toggle** (🔇/🔊, default muted) + the **g-meter toggle** (`G`) + the compare toggle) and the **global-time slider** around ONE [PlayerPane](player_pane.py) — or, in **compare mode**, TWO equal panes side-by-side (primary drives all telemetry; secondary is video-only, with per-pane lap pickers + Δ badges). Re-exposes the same `seek`/`play`/`positionChanged` API the app drives. |
| [player_pane.py](player_pane.py) | One self-contained single-lap player (extracted from the old `video_view.py`): `QMediaPlayer` + `QVideoWidget` + `QAudioOutput`; emits `positionChanged(s)` in **global session time**, exposes `seek(s)` (global) + `is_playing()`/`pause()`/`play()`. Hosts the **g-meter overlay** as a frameless translucent **top-level window** pinned over the `QVideoWidget`'s **top-right** corner and **scaled to a fraction of the video** (a plain child is painted behind the video's native surface on macOS and never shows; its own native window composites above it) and exposes `set_g(...)` / `set_gmeter_lap(...)` (app-fed at the tick, which also re-pins/re-scales the overlay). For a **chaptered** recording it holds the `ChapterMap`, switches source on a cross-chapter seek, and **auto-advances** to the next chapter at end-of-media — one source at a time, one continuous global clock. |
| [scrub_controller.py](scrub_controller.py) | **ScrubController** — the plot-cursor scrub cluster, extracted from `app.py`'s StudioWindow: owns the scrub state, scopes the drag to the lap captured at grab, and **coalesces** the seeks + cursor/marker/readout refresh to ≤1 per 30 Hz tick (`apply_tick`); in compare mode the drag is distance-locked across both panes. A Qt-free, pacer-free control-layer collaborator. |
| [compare_controller.py](compare_controller.py) | **CompareController** — the dual-lap compare-mode cluster, extracted from StudioWindow: owns the on/off flag + the pinned (A,B) lap ids, the enter/exit orchestration (suspend/restore auto-follow, re-seek both panes to their lap's start line) and the per-tick upkeep (pane times, "Δ vs other" badges, secondary g). Qt-free, pacer-free. |
| [theme.py](theme.py) | The **dark "Refined Minimal" design system** — single source of truth: the colour/scale tokens (`C`), Inter font registration, the dark `QPalette` + global QSS, the pyqtgraph background, and `icon(...)` (Phosphor glyphs via `qtawesome`, lazily imported). Pacer-free, LLM-editable. |
| [gmeter.py](gmeter.py) | **Vehicle-frame g** from the GoPro **accelerometer** — pure numpy, pacer-free. Transforms `ACCL`/`GRAV`/`CORI` (camera frame) into kart-frame lateral/longitudinal g (gravity removed via GRAV; rotate by `conj(CORI)`; project horizontal; **per-chapter** align to GPS ENU). Cross-checks against **GPS-derived g** and falls back to it if the IMU is absent/unreliable. Precomputed at load; `at_time(t)` is a cheap lookup. See [docs/gmeter-validation.md](docs/gmeter-validation.md). |
| [gmeter_overlay.py](gmeter_overlay.py) | The subtle **"G meter"** dial (pacer-free Qt): thin 0.5/1.0 g rings + a soft white dot (**no centre-to-dot line**) showing the **felt force** the driver's body feels (**brake→up, accel→down, right→left, left→right**) + a translucent **red max-G envelope** (convex hull) + **amber cardinal peak-g numbers**. The dot is **EMA-low-passed** (chin-mount shake filter) and the peaks/hull are **robust** (high-percentile + clamped) so a helmet shake can't blow them out; envelope/peaks accumulate **per lap** (`set_lap`). A frameless translucent top-level window composited over the video, pinned **top-right** and **scaled to the video**; `set_g((lat,long,total))` + `set_lap(id)` each tick. |
| [chapters.py](chapters.py) | Pure-Python (no `pacer`): the GoPro chaptered-filename parser, sibling **discovery/grouping** (same recording number, same folder, ordered by chapter), and the **`ChapterMap`** global↔chapter time mapping (per-chapter offset table). Unit-tested in [`tests/test_chapters.py`](../tests/test_chapters.py). |
| [map_view.py](map_view.py) | Best lap (faint) + current/playing lap (highlighted) + **freely-draggable** start/sector timing lines + video marker (drag **constrained to the current lap** so it never jumps laps). Each lap is drawn as measured (solid) + reconstructed gap-fill (dashed/dimmed) segments. The full all-laps trace is intentionally not drawn (perf + clarity). |
| [gapfill.py](gapfill.py) | **GPS-gap reconstruction (map only)** — pure numpy. Detects interior dropouts and fills them with cross-lap borrow (primary) / reference centerline (fallback) / spline, tagged measured-vs-inferred. No `pacer`. |
| [reference.py](reference.py) + [mk_centerline.json](mk_centerline.json) | Georeferenced Daytona MK centerline (traced from `gmaps_pict.png`, similarity-ICP aligned to the GPS aggregate) — the gap-fill fallback for sections no lap covers. Rebuild via [dev/build_reference.py](dev/build_reference.py). |
| [plots_view.py](plots_view.py) | Speed (top) + lap-vs-best delta (bottom) on **one shared, x-linked x-axis** (dist/time toggle drives both; delta aligned by **normalized distance** → endpoint = laptime diff), so the two cursors always align. Downsampled/clipped curves + a synced cursor that is also a **draggable scrubber** + a **hover dot** on the delta curve + subtle **sector boundary guide lines** (`set_sector_lines`, app-fed) — pacer-free, it only emits `scrubStarted`/`scrubMoved(x, mode)`/`scrubEnded`/`modeChanged(mode)` (mode = `time`\|`distance`); the `ScrubController` converts + seeks, and app owns the live Δ/speed readout box. |
| [lap_table.py](lap_table.py) | Lap time / dist / entry speed + per-sector split columns (S1…Sn) once sectors are added. Multi-select to compare; **▶** marks the playing lap, blue = selection, green = best, **purple = per-sector session best**, **⚠ = GPS-dropout lap (low-confidence; time/distance/map less reliable, with a row tooltip)**. Base row text is the theme's primary off-white (dark table surface). **Every header is click-to-sort** by the underlying numeric value (asc/desc); highlights and the ⚠ flag follow the laps across a sort. |
| [app.py](app.py) | Assembles panels in splitters and wires the cross-panel signals. |

## Gotchas / notes

- **Coordinate space:** the trace *and* the timing lines live in **local meters** (`cs.local`).
  `pick_random_start()` / `update()` must run **after** `set_coordinate_system()`. (See
  `pacer/laps/laps.cpp` — `Update` converts the lines back to GPS internally for crossing tests.)
- **Sectors write-back:** assign wholesale — `laps.sectors = pacer.Sectors(start_line=…, sector_lines=[…])` — then `laps.update()`. (Verified to propagate across the binding.)
- **Data cleaning (`session._clean`):** real GoPro sessions have a stationary lead-in where
  GPS isn't locked and spikes wildly (jumps up to ~80 km). We trim the non-moving lead-in/cool-down
  and drop lone teleport glitches. Without this the trace, map zoom, and lap segmentation all break.
- **GPS quality gating (`session._gate_quality`):** the C++ core surfaces the GoPro **GPS9 DOP +
  fix-type** on `GPSSample` (new `.dop`/`.fix`; parsed in `pacer/gps-source/gps-source.cpp`). At
  load we drop fixes with no 3D lock (`fix<3`) or poor geometry (`dop>10`) — conservative. The
  older GPS5 stream carries neither (sentinels `fix=-1`, `dop=-1.0`) → "unknown" is always KEPT.
- **GPS track smoothing (`session._smooth_track`, `SMOOTH_WINDOW=13`):** the original map plotted
  RAW GPS coords, so ~3 m position jitter made the trace look noisy. We now apply the boxcar moving
  average proven in the upstream interpolation notebook (since removed) — edge-corrected and split at
  time gaps — to lat/lon/alt ONCE at load, *before* the points reach the core. Because the source
  coordinates are smoothed, the trace and every derived quantity (distances, segmentation, delta,
  sector splits) stay consistent. w=13 (~1.3 s @ 10 Hz) cuts the high-frequency jitter ~39% / the
  heading jitter ~91% while preserving genuine lap-to-lap racing-line differences and NOT clipping
  corner apexes (w≥21 starts cutting corners). Tune/measure with `studio/dev/denoise_check.py`.
- **Lap time = two interpolated crossing instants.** The C++ core (`pacer::Split`) already
  interpolates each start/finish (and sector) crossing TIME along the chord between the two GPS
  points that straddle the line — `t = t0 + f·(t1−t0)` for the geometric fraction `f` — so lap
  times are sub-sample accurate, NOT snapped to the nearest fix. Accuracy is therefore set by the
  per-sample TIME AXIS that those crossing times interpolate on (next bullet).
- **Timing uses the GPS9 true wall clock by default** (`session._gps9_times`). The old `naive`
  axis spread each GPMF payload's MEDIA span across `i/n`; the GoPro media clock for the GPS track
  runs ~0.1% fast (~9.990 Hz), which **systematically compressed every lap** (~30 ms on the best
  lap). The GPS9 stream carries the true GPS fix time (`timestamp_ms`) — a clean 10.000 Hz **wall
  clock** (the transponder's clock). We take only its per-sample SPACING and re-anchor each
  contiguous run to that run's media time, so video sync / chapter offsets are unchanged while
  inter-sample spacing is the real wall-clock spacing. Degrades to naive for any sample without a
  GPS9 timestamp (a GPS5-only stream). (A C++ Adam timestamp-fit path was tried here but **diverged**
  on long/noisy sessions and has since been removed — GPS9's true per-fix clock supersedes it.)
- **GPS9 true-clock timing is unbiased — VALIDATED OUT-OF-SAMPLE, no calibration factor** (rate =
  1.0). Validated against the kart's real lap-timing **transponder** on a SECOND, independent
  recording (0062) by `studio/dev/_validate_wallclock.py`: clean-lap residual mean **+0.0015 s /
  ±0.053 s** (0060: +0.0030 s / 0.087 s), each recording's own best-fit rate ≈1.0 (−22 / −46 ppm).
  A previously-committed clock-rate factor (0.999514) was **REMOVED** as an overfit to dropout-tail
  skew (it worsened the clean-lap RMS on both recordings). GPS-dropout laps are inherently ±noisy
  (their dropout is mid-lap) → **flagged** low-confidence, not absorbed into a clock rate. Full
  evidence + reproduction in [docs/upstream-20ms-investigation.md](docs/upstream-20ms-investigation.md)
  and [docs/gps-accuracy-research.md](docs/gps-accuracy-research.md); re-validate any recording with
  `pixi run python -m studio.dev._validate_wallclock -- <rec.MP4> <transponder.csv> --race-start <UTC>`.
- **Lap distance is gap-aware** (C++ `SegmentDistance` in `laps.cpp`, used by both
  `GetLapDistance` and the per-lap `cum_distances`). A normal segment is measured by the GPS chord
  (correct for well-sampled track); across a DROPOUT (a point-to-point time jump > 0.35 s) the
  straight chord cuts the corner and under-measures — one 6 s dropout cost ~100 m on the 0060
  session — so the gap segment uses the trapezoidal speed integral `½(v0+v1)·Δt` instead (the
  vehicle odometer, valid right across the hole), clamped to never fall below the chord. This cut
  the valid-lap distance spread from ~91 m → ~35 m (std 12.3 → 7.6 m) on the 0060 session; only
  the dropout laps change, so the delta / sector math stays consistent.
- **Lap validity is adaptive** (`session.valid_lap_ids`): laps within a band around the median lap
  time, so short double-crossings of the start line don't pollute the "best" lap.
- **Delta-to-best** (`session.delta`) is aligned by **normalized distance fraction** (s∈[0,1]) so a
  lap's delta *ends exactly at its laptime difference*; raw cum-distance alignment did not. The
  speed + delta plots share **one x-axis** driven by the dist/time toggle and kept x-linked
  (`delta(ids, x_mode=…)`: distance = s×best_distance metres, time = time-into-lap), so the two
  cursors always line up vertically at the same moment. `session.delta_at_time(t)` gives the
  current-moment Δ-to-best for the live readout box.
- **Per-sector splits** (`session.lap_sector_splits`) project each sector line to a cum-distance on
  each lap and split the time there — correct (sums to lap time) for every lap, no reliance on
  fragile geometric crossing of short lines.
- **GPS gap reconstruction (`session.lap_trace_segments` → `gapfill.py`, MAP ONLY):** where a lap's
  GPS has an interior dropout (a gate-removed run or a real outage), each lap is drawn as measured
  runs + inferred fills instead of a straight chord across the hole. A gap = an interior point-to-point
  time jump > ~0.35 s (≥3 missing samples @ 10 Hz); the lap's open ends are not gaps. Filled in order:
  (1) **cross-lap borrow** — pin a donor lap's sub-polyline between the gap mouths with a similarity
  transform (the real corner shape); (2) a georeferenced **reference centerline** where no lap covers
  the section (`reference.py`); (3) a **spline** for very short gaps. Inferred segments draw **dashed
  + dimmed**; cached per lap (never per frame). On `GX010060.MP4`: 7 gaps / 222 m → 6 borrow + 1
  spline, 0 reference. **MAP-ONLY guarantee:** `gapfill`/`reference` are pure numpy reading the
  unchanged kept-point arrays — `delta`, `lap_sector_splits`, `cum_distances`, `valid_lap_ids` are
  byte-identical (same JSON MD5 vs the base branch). Inspect via `denoise_check --gaps`.
- **Timing lines are placed freely** (no snap-to-trace); dragging redraws live and re-segments the
  laps once on release.
- **Draggable plot-cursor scrubber** (a fine, lap-scoped scrub; the full-video slider stays as-is):
  the speed + delta cursors are `movable` `InfiniteLine`s. Dragging either seeks the video **within
  the lap the playhead is currently in**, clamped to that lap. `plots_view` stays pacer-free — it
  emits only the raw plot-x + the shared axis mode (`scrubMoved(x, mode)`); `ScrubController`
  (`scrub_controller.py`, extracted from StudioWindow in PR #40) converts to a media time, seeks,
  and re-syncs both cursors + slider + map marker. The x↔media-time mapping lives
  in `session` (pure numpy on cached per-lap `(times, dists)`): `media_time_at_plot_x` /
  `plot_x_at_media_time`. Both plots share ONE x-axis and are permanently **x-linked**, so a given
  moment maps to the **same x** on both and the cursors coincide vertically. Source of truth is the
  media time. **Throttle + pause/resume:** seeks coalesced to **≤1 per 30 Hz tick** (latest target
  wins) so 4K HEVC stays responsive; pause-on-grab / resume-iff-was-playing. **No feedback loop:**
  the drag ignores the playback tick (`_user_dragging`) and programmatic `setValue` is
  `_suppress`-guarded. Tests in `tests/test_scrub_conversion.py` + `tests/test_controllers.py`.
- **Charts auto-follow the current lap (`app._follow_current_lap`, UI-only):** the charts always show
  **whichever lap the playhead is in vs the best lap** — as playback (or a main-slider scrub) crosses
  a lap boundary they switch to the now-current lap, keeping best as the reference overlay; the table
  `▶`/selection + map overlay stay coherent. It's a cheap **edge check** on `session.lap_at_time(t)`
  in the existing 30 Hz path (`_apply_readout`): re-selects only on an **actual lap change** (no
  per-tick thrash), **holds** the last lap through a lead-in/between-laps `None` region, and uses the
  **programmatic `table.select`** (signals blocked) so it emits **no seek** and never fights playback.
  A just-made manual selection (incl. a multi-lap comparison) is **preserved while paused** and
  replaced by `[current, best]` only once playback moves on. Because the current lap is always among
  the displayed laps, the **scrub cursor / Δ box / hover work in the followed lap**.
- **Live Δ/speed readout + delta-plot hover dot:** an always-on box above the plots
  (`app._update_diff_box`) shows the **current-moment Δ-to-best (priority) + speed** from
  `session.delta_at_time` / `speed_at_time` — green when ahead of best, red when behind — updating
  live as the video plays or the cursor scrubs. The delta plot additionally shows a **hover dot**
  (`ScatterPlotItem` + `TextItem` on `scene().sigMouseMoved`) that snaps to the nearest delta-curve
  sample under the mouse and labels its Δ value (+ distance/time there), independent of the playback
  cursor, hidden on mouse-leave. The hover handler is a cheap nearest-index lookup on the cached
  curve arrays (no re-plot); `plots_view` stays pacer-free (the box's values come from `session`
  via `app`; the hover reads only the curve already drawn).
- **Sortable lap table + purple session-best sectors (`lap_table.py`, UI-only):** cells carry a
  numeric sort key in `Qt.UserRole` (a `_NumItem.__lt__` sorts on it), so `"1:08.408"` sorts as
  68.408 s, not lexically; blanks/NaN sort last; every header toggles asc/desc and the chosen sort
  survives refreshes. The **purple** highlight is the per-column MINIMUM split across valid laps
  (motorsport "session best sector"). All visual state (green best lap, purple best-sector cells, the
  `▶` current-lap mark, the **⚠ GPS-dropout** flag) is keyed by **lap id** and re-applied after every
  sort, so it follows the right lap and coexists. Base row text is the theme's primary off-white
  (`theme.C.text`, dark table surface).
- **GPS-dropout low-confidence flag (`session.dropout_lap_ids`, UI-only):** a valid lap whose
  kept-point times have an interior gap > `gapfill.GAP_TIME_S` (0.35 s) had a real dropout, so its
  time/distance/map are less reliable. A pure, read-only helper (changes no analysis value); the
  table shows the ⚠ marker + tooltip.
- **Sector boundaries on the charts (`session.sector_plot_positions`, UI-only):** the sector lines
  draw as subtle dotted vertical guide lines on BOTH plots, labelled `S/F`/`S1`/`S2`…; positions are
  computed in `session` (the same midpoint→best-lap-trace projection the split times use) and mapped
  to the shared axis, so `plots_view` stays pacer-free. They update live on sector add/move/reset and
  reposition on the dist/time toggle, drawn behind the curves. No sectors → no lines.
- **Lap-scoped marker drag (`session.nearest_index_in_lap`, UI-only):** the red map marker resolves
  to the nearest point **within the current lap** (pure numpy on the lap's cached points) and clamps
  to that lap's time window, so dragging never snaps to another lap across a spatial overlap;
  playback still moves the marker across laps normally. Lead-in (no current lap) falls back to the
  whole-trace nearest.
- **Audio mute (`video_view.py`, UI-only):** a `QAudioOutput` (volume 0.6) + a 🔇/🔊 toggle; **default
  muted on launch** (telemetry tool), the button flips `QAudioOutput.setMuted`.
- **Performance:** UI sync runs on a ~30 Hz `QTimer` off the video present path; plot curves are
  downsampled+clipped with antialias off and autorange frozen; the map draws ≤2 laps. 4K HEVC
  decodes ~61 fps via VideoToolbox hardware decode — playback stays smooth, incl. with a lap selected.
  The new handlers are all cheap O(n) lookups (numeric sort, per-column min, lap-scoped nearest,
  sector-line redraw), never per-frame work.
- **Video sync** uses `QMediaPlayer.positionChanged`. For sub-frame precision, `QVideoSink.videoFrameChanged` / `QVideoFrame.startTime()` are available (the spike measured them frame-accurate, ~29 ms vs pacer's clock).

### Chaptered sessions

A long GoPro recording is split at a file-size limit (≈12 GB ≈ 28 min for 4K) into **chapters**
that share a recording number and increment a chapter index, e.g. recording 0060 =
`GX010060.MP4` + `GX020060.MP4` + `GX030060.MP4`. They are contiguous in time (split mid-lap,
not at a lap), so a lap can span a chapter boundary.

- **Opt-in, default unchanged.** Opening one chapter loads only that file (the single-file
  path is byte-identical to before). `--full`/`--chaptered`, or **File ▸ Load full recording**,
  discovers the sibling chapters (`chapters.discover_siblings`: same recording `NNNN` + same
  prefix, same folder, ordered ascending by chapter `CC` — never mixes recordings; a
  single-chapter recording loads gracefully) and reloads them as one session.
- **Telemetry chaining (`ingest.py`).** The per-file `GPMFSource`s are folded into a chain of
  the C++ `SequentialGPSSource`, whose time spans are already **cumulative** — so the trace lands
  on **one continuous, monotonic global clock** with no per-chapter reset, and lap segmentation,
  cum-distances, delta, sectors, smoothing and gap-fill all span boundaries automatically. (A
  lap that crosses a seam is one correct lap; the ~1 s GPS gap at a seam is the same payload
  granularity as in-chapter dropouts and is reconstructed by the existing gap-fill.)
- **Chapter offset table (`chapters.ChapterMap`).** Per-chapter media durations (from the GPMF)
  give each chapter a global `offset` (cumulative prior durations); chapter *i* covers global
  `[offset_i, offset_i+dur_i)`. `to_local(global_t) → (i, local_t)` and `to_global(i, local_t)`
  are the global↔chapter mapping the video layer drives source-switching with.
- **Video across files (`player_pane.py`).** `QMediaPlayer` plays one source, so the pane keeps
  the ordered chapter list + offsets and: the **emitted position (and the shell's slider) is
  global** (spans the whole session); a `seek(global_t)` maps to (chapter, local) and **switches source** if the
  target chapter isn't loaded (the deferred local seek applies on `LoadedMedia`); and at
  `EndOfMedia` it **auto-advances** to the next chapter and keeps playing from 0. **Known
  limitation:** switching source at a seam reopens the file, so a brief hitch there is expected.
- **UI.** The window title and a banner above the video show e.g. `recording 0060 · 3 chapters`
  and the current chapter (`— chapter 2 of 3`); the banner is hidden for a single file.
- **Sources:** GPMF/GoPro `.MP4` only. pacer supplies the telemetry time axis; the app brings its own video player (pacer doesn't decode pixels).
- `dev/_smoke.py` is a headless self-test: `python -m studio.dev._smoke`.

### G-meter overlay (friction circle, from the real accelerometer)

A classic friction-circle g-meter overlaid on the video, driven by the GoPro's **real
accelerometer** (`ACCL`), synced to playback. Toggle with the **`G`** button under the video.

- **Core binding (additive).** `pacer/gps-source` now parses, alongside GPS, three IMU streams on
  the **media clock** (so they sync to the video / span chapters like GPS): `ACCL` (200 Hz, m/s²),
  `GRAV` (60 Hz, gravity unit vector), `CORI` (60 Hz, camera-orientation quaternion). New
  datatypes `IMUSample` (t,x,y,z) and `QuatSample` (t,w,x,y,z); read via
  `read_accl/read_grav/read_cori`. GPS parsing/timing is untouched.
- **Camera→kart transform (`gmeter.py`).** Remove gravity (`ACCL − 9.81·ĝ`, ĝ from `GRAV`
  permuted onto the ACCL axes), rotate to world by `conj(CORI)` (CORI stores world→camera),
  project onto the horizontal plane, then split per-sample into **longitudinal** (along the GPS
  velocity) and **lateral** (perpendicular) g. The one free DOF — CORI-world yaw vs GPS north —
  is fit **per chapter** against the GPS-derived g (CORI resets each chapter; a single global fit
  fails). All precomputed at load; the overlay does a cheap `session.g_at_time(t)` lookup at the
  ~30 Hz tick.
- **GPS cross-check + honest fallback.** The loader also derives g from the GPS trajectory
  (`long = d|v|/dt`, `lat = v²·curvature`) and prints the agreement at startup. On the test
  recording (kart-mounted cam): **lateral r ≈ 0.90, 96.5 % sign agreement**, longitudinal magnitude
  matched. If the lateral correlation is poor (e.g. a head-dominated **helmet cam**) or the IMU is
  absent (older GoPro), the meter **falls back to the GPS-derived g** (`source="gps"`) rather than
  shipping garbage. See [docs/gmeter-validation.md](docs/gmeter-validation.md) for the full numbers.
- **The widget (`gmeter_overlay.py`).** A subtle **"G meter"** dial: a faint see-through backdrop,
  thin 0.5/1.0 g rings, a soft white **dot (no centre-to-dot line)**, a translucent **red max-G
  envelope** (convex hull of the grip used), and **amber peak-g numbers at the four cardinals**
  (max felt-g forward/back/left/right). Two display-only conventions live here (the validated
  `gmeter.py` g values are untouched):
  - **Felt-force pointer** — the dot is the inertial reaction the *driver's body* feels, not the
    acceleration vector: **braking → UP, accelerating → DOWN, turning right → LEFT, turning left →
    RIGHT**. (Screen `dx = +lateral`, `dy = +longitudinal`.)
  - **Chin-mount shake filter** — the GoPro is chin-mounted, so the accel carries helmet/mount
    jitter. The dot is an **EMA low-pass** of the felt g (smooth but responsive); the cardinal
    numbers track a **high percentile** of the recent magnitude and the hull points are **clamped
    to those robust peaks**, so a lone shake spike can't blow out the numbers or balloon the blob.
  The envelope + peaks accumulate over the **current lap** and reset at the lap boundary
  (`set_lap`; change `_RESET_ON_LAP` for other scopes). The dial is pinned to the video's
  **top-right** corner and **scaled to a fraction of the video** (re-pinned/re-scaled on
  move/resize). Implemented as a **frameless translucent top-level window** (not a child widget):
  macOS composites the `QVideoWidget`'s native video surface over ordinary child widgets, so a
  child overlay is invisible on screen even though it renders in an offscreen `widget.grab()`; its
  own native window composites above the video instead. Pacer-free — g values come from `session`
  via `app` (which also feeds the current lap id for the per-lap envelope reset).

## Tests

Pure-Python studio tests live under [`tests/`](../tests/) and are registered with CTest (so
`pixi run test` runs them with the C++ suite — 17 CTest entries total: 5 C++ + 12 Python):
`test_gapfill`, `test_scrub_conversion`, `test_studio_features` (the auto-follow edge + numeric
sort / lap-scoped nearest / per-column min), `test_chapters`, `test_lap_timing`,
`test_gps_source_bindings`, `test_ingest_equivalence` (single-pass ingest == two-pass reads),
`test_compare`, `test_controllers` (the extracted scrub/compare controllers on a bare Session +
fake views), `test_validate_wallclock`, `test_gmeter`, `test_gmeter_overlay`.

## State & next ideas

See [PLAN.md](PLAN.md) for the full project state: shipped features, the **empirically-rejected**
experiments (with the why for each), tech debt, and the near-term backlog. In short, next up: more
tracks in `tracks.py` (+ real auto-detection), persist sector/start-line config per file, and polish
(keyboard shortcuts, optional snap toggle).
