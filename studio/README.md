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
| [session.py](session.py) | The loaded **session**: trace/lap/delta/sector accessors + timing-line write-back over `pacer.Laps`, with the per-lap caches the 30 Hz tick reads. `Session.load` is the public load entry point — it delegates the pipeline to `load.py` and wraps the result (one of the four modules that may name `pacer`). **`load_reference(paths)`** (F7) loads a SECOND session headless and adopts its best lap as the cross-recording reference baseline for the Δ charts / map overlay / sector guides / per-corner Δ (same-track guarded; see `cross_reference.py`); the delta seam falls back to the local best lap when none is loaded (dormant = byte-identical). |
| [load.py](load.py) | The **load pipeline** behind `Session.load` (one of the four modules that may name `pacer`): single-pass read via `ingest.py`, GPS quality gate + trace cleaning (`_clean`), the **GPS9 true-clock time axis** (`_gps9_times`), load-time boxcar smoothing (`_smooth_track`), and segmentation + start-line placement (`_fit_start_line` on known tracks). Returns the segmented `pacer.Laps` + coordinate system + `ChapterMap` + the IMU streams for the g-meter. |
| [ingest.py](ingest.py) | The GoPro/GPMF **data-loading layer** (one of the four modules that may name `pacer`): builds the `SequentialGPSSource` chain over one or more chapters and reads the raw GPS+IMU streams on the continuous global clock. The load path is **`read_recording`** — single-pass: one shared chain, so each chapter MP4 is opened/GPMF-parsed ONCE for both GPS and IMU (`read_gpmf`/`read_imu` remain for dev scripts). Returns raw samples + per-chapter durations; `load.py` cleans/smooths/segments on top. A data/control-layer module, not a view. |
| [_signal.py](_signal.py) | Pure-numpy **signal/clean helpers** shared by the session pipeline and the g-meter: the edge-corrected boxcar smoother, gap segmentation, the GPS quality gate, the real-lap band filter, and the `fmt_time` lap-time formatter the views use. Pacer-free by contract (no `pacer`, no Qt). |
| [tracks.py](tracks.py) | Registry of known tracks (Daytona MK); detects the track by centroid and gives its fixed start/finish line. One of the four modules that name `pacer` (geometry only). |
| [dev/transponder.py](dev/transponder.py) | Pure-Python (no `pacer`), **dev/validation-only**: defensive parser for a lap-timing **transponder CSV** (the ground truth the GPS9 timing is **validated** against, out of sample, by [`dev/_validate_wallclock.py`](dev/_validate_wallclock.py)). Reads only the `Lap` + `Lap Time` columns (the later columns embed commas/quotes). A reference **input** only — the CSV is never committed. |
| [video_view.py](video_view.py) | The player **shell**: the transport row (play/pause + the **mute/unmute toggle** (🔇/🔊, default muted) + the **g-meter toggle** (`G`) + the compare toggle) and the **global-time slider** around ONE [PlayerPane](player_pane.py) — or, in **compare mode**, TWO equal panes side-by-side (primary drives all telemetry; secondary is video-only, with per-pane lap pickers + Δ badges). Re-exposes the same `seek`/`play`/`positionChanged` API the app drives. |
| [player_pane.py](player_pane.py) | One self-contained single-lap player (extracted from the old `video_view.py`): `QMediaPlayer` + `QVideoWidget` + `QAudioOutput`; emits `positionChanged(s)` in **global session time**, exposes `seek(s)` (global) + `is_playing()`/`pause()`/`play()`. Hosts the **g-meter overlay** as a frameless translucent **top-level window** pinned over the `QVideoWidget`'s **top-right** corner and **scaled to a fraction of the video** (a plain child is painted behind the video's native surface on macOS and never shows; its own native window composites above it) and exposes `set_g(...)` / `set_gmeter_lap(...)` (app-fed at the tick, which also re-pins/re-scales the overlay). For a **chaptered** recording it holds the `ChapterMap`, switches source on a cross-chapter seek, and **auto-advances** to the next chapter at end-of-media — one source at a time, one continuous global clock. |
| [scrub_controller.py](scrub_controller.py) | **ScrubController** — the plot-cursor scrub cluster, extracted from `app.py`'s StudioWindow: owns the scrub state, scopes the drag to the lap captured at grab, and **coalesces** the seeks + cursor/marker/readout refresh to ≤1 per 30 Hz tick (`apply_tick`); in compare mode the drag is distance-locked across both panes. A Qt-free, pacer-free control-layer collaborator. |
| [compare_controller.py](compare_controller.py) | **CompareController** — the dual-lap compare-mode cluster, extracted from StudioWindow: owns the on/off flag + the pinned (A,B) lap ids, the enter/exit orchestration (suspend/restore auto-follow, re-seek both panes to their lap's start line) and the per-tick upkeep (pane times, "Δ vs other" badges, secondary g, and the map **ghost** — lap B's kart placed at `index_at_time(t_b)`, the same `t_b` the Δ badge used, cleared on exit). Qt-free, pacer-free. |
| [theme.py](theme.py) | The **dark "Refined Minimal" design system** — single source of truth: the colour/scale tokens (`C`), Inter font registration, the dark `QPalette` + global QSS, the pyqtgraph background, and `icon(...)` (Phosphor glyphs via `qtawesome`, lazily imported). Pacer-free, LLM-editable. |
| [gmeter.py](gmeter.py) | **Vehicle-frame g** from the GoPro **accelerometer** — pure numpy, pacer-free. Transforms `ACCL`/`GRAV`/`CORI` (camera frame) into kart-frame lateral/longitudinal g (gravity removed via GRAV; rotate by `conj(CORI)`; project horizontal; **per-chapter** align to GPS ENU). Cross-checks against **GPS-derived g** and falls back to it if the IMU is absent/unreliable. Precomputed at load; `at_time(t)` is a cheap lookup. See [docs/gmeter-validation.md](docs/gmeter-validation.md). |
| [gmeter_overlay.py](gmeter_overlay.py) | The subtle **"G meter"** dial (pacer-free Qt): thin 0.5/1.0 g rings + a soft white dot (**no centre-to-dot line**) showing the **felt force** the driver's body feels (**brake→up, accel→down, right→left, left→right**) + a translucent **red max-G envelope** (convex hull) + **amber cardinal peak-g numbers**. The dot is **EMA-low-passed** (chin-mount shake filter) and the peaks/hull are **robust** (high-percentile + clamped) so a helmet shake can't blow them out; envelope/peaks accumulate **per lap** (`set_lap`). A frameless translucent top-level window composited over the video, pinned **top-right** and **scaled to the video**; `set_g((lat,long,total))` + `set_lap(id)` each tick. The dial's paint body is extracted into a module-level **`paint_dial(painter, w, h, DialState)`** (the widget's `paintEvent` snapshots `self` into a `DialState` and delegates) so the **offline video exporter** (`export_video.py`) can burn the SAME dial in — verified **pixel-identical** to the pre-extraction widget. `paint_dial` also takes an opt-in **`export=True`** flag that renders a vivid, **no-box**, white-ringed dial with **big outlined cardinal numbers** + a glowing dot for burning over bright footage (`_paint_dial_export`/`_export_dial_geom`); the **live widget always uses the default** so its on-screen look is unchanged (tested). |
| [chapters.py](chapters.py) | Pure-Python (no `pacer`): the GoPro chaptered-filename parser, sibling **discovery/grouping** (same recording number, same folder, ordered by chapter), and the **`ChapterMap`** global↔chapter time mapping (per-chapter offset table). Unit-tested in [`tests/test_chapters.py`](../tests/test_chapters.py). |
| [map_view.py](map_view.py) | Best lap (faint) + current/playing lap (highlighted) + **freely-draggable** start/sector timing lines + video marker (drag **constrained to the current lap** so it never jumps laps). An **opt-in "Snap" toggle** in the map header (default **off**) snaps just the **released** handle to the nearest trace point (`session.nearest_index`) before the one re-segmentation — the other endpoint never moves. Each lap is drawn as measured (solid) + reconstructed gap-fill (dashed/dimmed) segments. **Corner labels** (`C1…Cn`, from `session.corner_map_markers`) sit at the detected apexes with subtle direction-coloured dots (cyan = left, coral = right) — a self-contained overlay, app-pushed; `highlight_corner(cid)` rings ONE apex in accent (the consistency panel's click-to-locate cue, F6). **Brake-point glyphs (F5)** — a ▼ at each braking-zone onset (from `session.lap_brake_map_markers`), sized by peak decel, in the lap's series colour — are a second self-contained overlay, app-pushed on lap change and showing **both laps in compare** (the Circuit Tools braking-zone comparison). The full all-laps trace is intentionally not drawn (perf + clarity). A **rainbow track map** header toggle (OFF → Speed → Δ-vs-best) paints the **current lap's** line as a channel colour gradient: the channel is quantized into 16 buckets drawn by ≤16 `PlotCurveItem`s (per-bucket pens from `theme.rainbow_colors` — red = slow/losing → green = fast/gaining; NaN breaks + `connect='finite'` hold each bucket's disjoint runs, and dropout gaps are never painted as chords). Speed comes from the cached lap columns; Δ resamples the **existing** 400-grid `delta()` onto the lap's point distances (one `np.interp` — never recomputed). A slim min/max legend shows only while painted; the bucket items rebuild **only** on lap/channel change or re-segment (never on the 30 Hz tick), and OFF restores the exact normal rendering (the overlay items are only hidden — byte-identical). In **compare mode** a second hollow **ghost marker** (smaller ring, lap-B accent `theme.CHART_SERIES[1]`, not draggable) shows the other compared lap's kart at equal elapsed-into-lap — created lazily on the first compare tick, updated by `setPos` only (as cheap as the red marker), and **removed on exit** so the item state returns byte-identical; outside compare the tick path does zero ghost work (instrumented via `ghost_updates`). Unit-tested in [`tests/test_rainbow_map.py`](../tests/test_rainbow_map.py) and [`tests/test_map_ghost.py`](../tests/test_map_ghost.py). |
| [corners.py](corners.py) | **Corner model** — pure numpy, pacer-free, **not map-matching** (everything runs on our own trace; no external centerline). Curvature κ(s) from the unwrapped heading vs arc length, **median-pooled over the session's clean laps** on the best lap's normalized-distance grid; corner spans = |κ| above a threshold **derived from the track's own κ distribution** (log-domain Otsu split) with hysteresis, sign-splitting (S-complexes), merge-within-gap and min-length/min-turn filters; apex = the |κ|-weighted centroid. Projection mirrors `lap_sector_splits` (normalized distance), and corners + straights **partition** each lap so Σ segment Δ == lap Δ exactly (asserted). Session caches `corners()` / `lap_corner_stats()` per segmentation. |
| [consistency.py](consistency.py) | **Consistency statistics** (F6) — pure numpy, pacer-free: sample σ (ddof=1) of lap times / per-sector splits / per-corner times, the per-corner **median time lost** vs that corner's session best, and the **inconsistency ranking** — score = σ × median-loss (the product is the AND: a corner must be BOTH erratic and slow to rank; rationale in the module doc) — plus the running-PB mask for the trend sparkline. Every statistic runs over `Session.consistency_lap_ids()` — the valid, GPS-dropout-free laps (the ⚠ rule). |
| [consistency_panel.py](consistency_panel.py) | **ConsistencyPanel** (F6) — the compact, collapsible strip under the lap table: a lap-time **trend sparkline** (downsample-safe mini-plot; the running session-best/PB laps marked in the best-lap green) + the **top-5 most inconsistent corners** (corner, σ, median Δ). Clicking a corner row **ring-highlights its apex on the map** (`MapView.highlight_corner`) and nothing else — read-only, no lap selection / seek. The σ summary (lap + per-sector) lives on the header strip so it survives collapse. Refreshed on load/re-segment only (zero per-tick cost). |
| [coaching.py](coaching.py) | **Auto coaching summary (F10)** — pure numpy, pacer-free; the capstone that **composes** the corner model (F2), driving channels (F5) and consistency stats (F6) WITHOUT recomputing any of them. Per corner, the **median time lost vs the best lap's same corner** over the consistency laps (the ⚠-clean set) — ranked **biggest realistic gain first**. For the **top-3** it attaches the **dominant MEASURED reason**, chosen deterministically as the largest seconds-of-loss contribution among four signals: **apex** (the typical lap's apex/min speed deficit vs best ⇒ "carry more apex speed"), **braking** (more time on the brakes than best in the corner's approach window ⇒ "brake later/shorter"), **coasting** (a coast inside the corner the best lap lacks ⇒ "back to throttle sooner") and **line** (high cross-lap σ ⇒ "be consistent here"); ties broken by a fixed reason priority. The "typical" lap is the **median-lap-time** lap (deterministic). Everything numbers-only — no ML, no randomness; `summarize()` is byte-identical across calls. Excluded (`enough=False`) under `MIN_LAPS` (3) clean laps. `Session.coaching_opportunities()` owns the pacer-side extraction; `Session.corner_entry_media_time(lap, cid)` gives the jump-to seek target. |
| [coaching_panel.py](coaching_panel.py) | **OpportunitiesDialog (F10)** — a self-contained, read-only `QDialog` over a `coaching.Opportunities` (pacer-free; consumes only the dataclasses + `coaching.reason_sentence`). Three rows — corner (id + direction glyph), **time lost** (`+s`, red), the **reason sentence with numbers**, and a **"Go →"** button that calls an injected `jump_to(cid, entry_dist)`: the app **selects the corner** (map apex ring via `MapView.highlight_corner` + the Corners view on the best lap) and **seeks the video to the best lap's ENTRY** to that corner (`Session.corner_entry_media_time`). Opened from **Coaching ▸ "Opportunities…"**, rebuilt from a fresh summary each open (zero per-tick cost). The `<MIN_LAPS` excluded state and the "no corner loses time" case show a **friendly message** (no table, no crash). Unit-tested in [`tests/test_coaching.py`](../tests/test_coaching.py). |
| [driving.py](driving.py) | **Driving channels (F5)** — pure numpy, pacer-free. Off the **validated vehicle-frame g** (`gmeter.py`, GPS-cross-checked): **brake events** (onset where smoothed `long_g < −θ_b` with release hysteresis; peak decel + duration), **coasting spans** (`\|long_g\| < θ_c` AND `\|lat_g\| < θ_lat` AND ~flat speed), and **per-corner grip utilization** (median `\|g\|` / the lap's own envelope-max inside each F2 corner window, in (0,1]). **θ_b/θ_c/θ_lat are derived from the session's OWN g distribution — no magic constants** (θ_b = the median of the *braking-only* decel, duty-cycle independent; documented with the measured D24 distribution + the load-time `describe()` print). Validated on the real recordings: the ACCL brake onsets correlate **r≈1.00** in track position with the independent **GPS speed-derivative** method (~4–6 m median offset) and yield **zero** false onsets on the full-throttle straight. Session caches `lap_brake_events()` / `lap_coasting_spans()` / `lap_corner_grip()` per segmentation. |
| [gapfill.py](gapfill.py) | **GPS-gap reconstruction (map only)** — pure numpy. Detects interior dropouts and fills them with cross-lap borrow (primary) / reference centerline (fallback) / spline, tagged measured-vs-inferred. No `pacer`. |
| [render_cache.py](render_cache.py) | **LapRenderCache** — the per-lap MAP-RENDERING cache cluster extracted from `Session`: the gap-aware draw segments (`lap_trace_segments`, via `gapfill`) + the lazily-built reference-centerline fallback donor. Pacer-free (Session injects callables over its cached per-lap arrays); invalidated from `Session.set_timing_lines` on re-segment. Session keeps thin delegators, so callers are unchanged. |
| [reference.py](reference.py) + [mk_centerline.json](mk_centerline.json) | Georeferenced Daytona MK centerline (traced from `gmaps_pict.png`, similarity-ICP aligned to the GPS aggregate) — the gap-fill fallback for sections no lap covers. Rebuild via [dev/build_reference.py](dev/build_reference.py). |
| [cross_reference.py](cross_reference.py) | **Cross-recording reference lap (F7)** — pure numpy, pacer-free. A `ReferenceLap` value object holding ANOTHER recording's best lap as the primary session consumes it: its arc-length curves `(dist, speed, elapsed)` for the Δ charts / lap-table per-corner Δ (aligned by **normalized distance**, so the recordings' differing lap lengths/start lines just work — the same machinery `delta()` uses) plus its racing line **pre-aligned into the primary's local frame** via `reference.fit_loop_to_loop` for the map overlay (suppressed if the closed-loop fit RMS exceeds the track-width tolerance; the charts/table are frame-independent and unaffected). `Session.load_reference(paths)` does the one pacer-backed step (load the 2nd Session headless) + the same-track / has-laps guard; everything else lives here. **Dormant when absent** — no reference ⇒ every "vs best" path falls back to the local best lap, byte-identical. |
| [plots_view.py](plots_view.py) | Speed (top) + lap-vs-best delta (bottom) on **one shared, x-linked x-axis** (dist/time toggle drives both; delta aligned by **normalized distance** → endpoint = laptime diff), so the two cursors always align. Downsampled/clipped curves + a synced cursor that is also a **draggable scrubber** + a **hover dot** on the delta curve + subtle **sector boundary guide lines** (`set_sector_lines`, app-fed) + **F5 driving overlays**: **brake-point glyphs** (▼ riding the speed curve at each onset, from `session.lap_brake_plot_positions`) and **shaded coasting bands** (`session.lap_coasting_plot_spans`), both app-fed in the current axis mode and showing **both laps in compare** — pacer-free, it only emits `scrubStarted`/`scrubMoved(x, mode)`/`scrubEnded`/`modeChanged(mode)` (mode = `time`\|`distance`); the `ScrubController` converts + seeks, and app owns the live Δ/speed readout box. |
| [lap_table.py](lap_table.py) | Lap time / dist / entry speed + per-sector split columns (S1…Sn) once sectors are added. Multi-select to compare; **▶** marks the playing lap, blue = selection, green = best, **purple = per-sector session best**, **⚠ = GPS-dropout lap (low-confidence; time/distance/map less reliable, with a row tooltip)**. Base row text is the theme's primary off-white (dark table surface). **Every header is click-to-sort** by the underlying numeric value (asc/desc); highlights and the ⚠ flag follow the laps across a sort. Two summary **footer rows** under the table — **Theoretical best** (sum of the purple session-best splits) and **Best rolling** (fastest start-anywhere full loop) — styled like the purple bests with defining tooltips; they live OUTSIDE the sortable table (never sorted/selected) and update on every refresh, incl. re-segmentation. Also hosts **`CornerTable`** — the panel's "Corners" mode (header toggle in `app.py`): one row per detected corner for the selected lap (time-in-corner, Δ vs best, apex/entry/exit speeds; per-corner **session best in purple**; a **Grip %** column (F5) = friction-circle utilization from `session.lap_corner_grip`, a dash when there's no g signal), stacked with the laps table so Laps mode stays byte-identical. |
| [export_data.py](export_data.py) | **Data export (F11)** — pure Python (no `pacer`, no Qt), fed only by Session accessors: `write_laps_csv` (one row per lap: time/dist/entry + S-splits + per-corner time/apex-speed + the ⚠ flag, human 3-decimals, plus a labeled **summary trailer** — theoretical-best / best-rolling, mirroring the table footer), `write_channels_csv` (per-sample t/elapsed/lat/lon/x/y/dist/speed m/s+km/h + kart-frame g — float-`repr` values, EXACT round-trip vs the Session arrays), `write_report_html` (one self-contained page: session header, laps table, embedded base64-PNG map+chart snapshots; no JS). `app.py`'s **File ▸ Export** submenu owns the save dialogs + widget grabs; nothing is written without an explicit save. Unit-tested in [`tests/test_export_data.py`](../tests/test_export_data.py). |
| [library.py](library.py) | **Session library (F8)** — pure path/JSON, pacer-free (the sidecar's twin): a versioned local index at `~/Library/Application Support/pacer/library.json` (atomic tmp+`os.replace`, app-support dir auto-created). One entry per **recording fingerprint** (`<first-chapter stem>\|<total duration to 0.1s>`, so a single-chapter and a full chaptered open of the same recording share ONE entry — re-opening **updates in place**, no duplicate) carrying track / date (GPS9 wall clock) / lap count / best / theoretical / paths. `load` **self-heals to a safe empty index** on ANY corruption (same philosophy as the sidecar revert guard); `upsert_and_save` is the post-load call; `pb_series(track)` extracts the dated best-lap progression. `_app_support_dir` is the single seam the tests monkeypatch (never the real `~/Library`). The values are fed from **`Session.library_entry(paths)`** (pacer stays on the Session side). |
| [library_dialog.py](library_dialog.py) | **The File ▸ Library… dialog (F8)** — self-contained, pacer-free, over a loaded `library` index. A **sortable** list (date / track / best / theoretical, numeric sort keys so times order by value); an **Open** button + double-click that re-open the selected recording through an injected callback (`app._load`); **missing-file rows greyed + disabled** (not openable); and a per-track **PB-progression mini-chart** (best-lap vs date, a pyqtgraph `DateAxisItem` plot) for the selected row. A missing/empty index just shows an empty library. |
| [export_video.py](export_video.py) | **Video-overlay export (F9)** — pacer-free + **event-loop-free** offline renderer (`ffmpeg` dep): burns the on-screen overlays onto the GoPro footage → a shareable MP4. Two ffmpeg processes bracket a Python compositing loop — **decode** (`-ss t0 -t dur -vf scale,fps -pix_fmt rgb24 -f rawvideo pipe`, optionally `-hwaccel videotoolbox`) trims the selected lap's media window + scales to 1080p; **composite** paints each frame into a `QImage` with QPainter — the **g-meter dial** (via the shared `gmeter_overlay.paint_dial` on a headless dial driven exactly like the live tick), the **Δ/speed box**, a **track-map inset + marker**, and a **lap/sector strip**; **mux** re-encodes H.264 + carries the source **audio** trimmed to the same window (A/V in sync). **GPU offload (Apple Silicon):** by default the encode uses the Apple media engine (`h264_videotoolbox`) and the decode runs on it too (`-hwaccel videotoolbox`), so both heavy stages leave the CPU — with a software **libx264 fallback** if a VideoToolbox session won't open (runtime-probed; mid-render failures retry on libx264). The **pipelined engine** (reader thread + a small paint-worker pool + an ordered writer) overlaps decode/composite/encode, and the default **30 fps cap** halves the frame count of 59.94 fps footage with no perceptible loss for an overlay — together a ~2× faster wall-time and ~3–5× less CPU than the old serial libx264 path (the export was "too slow to use" on a core-starved Mac). The painted values come from the SAME accessors the live readout reads (`overlay_values_at`: `index_at_time`→`tv`, `lap_at_time`+`delta_at_lap`, `g_at_time`), so a frame grab matches the app. **Export-tuned restyle (for burning over BRIGHT footage):** a vivid opaque palette (`EXPORT`) + a dark **outline/shadow** on every text/line/dial element (`_draw_text`/`_stroke_polyline`) so it reads over bright sky AND dark tarmac; the **map inset** drops its box + the full-session trace and draws ONLY the exported lap's haloed white line + a **glowing coral marker with a comet tail**; the **g-meter** drops its box with white rings + **big outlined cardinal numbers** (an `export=True` path in `paint_dial`, so the live overlay stays byte-identical); the **readout** is a hero speed + punchy Δ cue and the **lap strip** a bold label + amber progress fill on slim dark HUD pills; sizes scale with `out_height`. **Selectable quality:** `OverlayConfig` gains `quality` (Standard/High → `quality_params` sets BOTH the VideoToolbox bits-per-pixel and the libx264 CRF) alongside `out_height`. The `Renderer` is pumped frame-by-frame (`run_chunk`) so app.py can run it off the UI thread, cancellable. **v1 = ONE selected lap** (full-session / compare-pair export are Phase 2). Pure-logic parts unit-tested in [`tests/test_export_video.py`](../tests/test_export_video.py) (trim math, per-frame lookup, ffmpeg-cmd construction, encoder/fps/hwaccel/**quality** resolution, the **export-palette + map-no-box + size-scale** logic; subprocess mocked — real VideoToolbox + libx264-fallback + **quality-level** renders gated behind `ffmpeg_available()`). |
| [app.py](app.py) | Assembles panels in splitters and wires the cross-panel signals. **File ▸ "Load reference recording…" / "Clear reference"** (F7) drive `Session.load_reference` / `clear_reference` and refresh the Δ charts / map / lap-table on the new baseline; a permanent status-bar **chip** shows which reference is active (hidden when dormant). **File ▸ "Library…"** (F8) opens the session-library dialog; every successful `_load` **post-UI** upserts the recording into the index (fully guarded — a library-write failure logs a warning and never disrupts the load). **File ▸ "Export overlay video…"** (F9, a separate dormant entry, greyed until a session loads) opens a small **resolution + quality picker** (`_ask_export_options`: 720p/1080p/1440p/Source × Standard/High, remembered on the window) → an `OverlayConfig`, then renders the selected lap via `export_video.Renderer` on a worker `QThread` behind a cancellable `QProgressDialog`. |

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
  `session.delta_at_lap` + the trace speed at the resolved index — green when meaningfully ahead
  of best, red when behind, neutral within ±`theme.DELTA_EVEN_EPS_S` of zero — updating
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
- **Theoretical best + best rolling lap (`session.theoretical_best` / `session.best_rolling_lap`,
  footer rows in `lap_table.py`):** the theoretical best is the EXACT sum of the per-column
  session-best splits (`session.session_best_splits` — hoisted from the table so the purple cells
  and the footer share one computation; with no sector lines it degenerates to the best lap time).
  The best rolling lap is the fastest start-ANYWHERE full loop (the MoTeC/RS3 "rolling lap"):
  per consecutive valid-lap pair, every sample is an anchor whose window ends when the next lap
  passes the **same spatial point** (nearest same-direction point within a narrow arc + chord
  refinement — pure φ- and odometer-alignment were measured optimistically biased by the laps'
  line-length differences and rejected; constants carry the measured WHYs). Windows straddling a
  ⚠ dropout lap are excluded; complete laps always count, so rolling ≤ best lap time. The footer
  rows live outside the sortable table and refresh on selection/sector/start-line changes.
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
`pixi run test` runs them with the C++ suite — 27 CTest entries total: 5 C++ + 22 Python):
`test_gapfill`, `test_scrub_conversion`, `test_studio_features` (the auto-follow edge + numeric
sort / lap-scoped nearest / per-column min), `test_chapters`, `test_lap_timing`,
`test_gps_source_bindings`, `test_ingest_equivalence` (single-pass ingest == two-pass reads),
`test_compare`, `test_controllers` (the extracted scrub/compare controllers on a bare Session +
fake views), `test_map_ghost` (the F4 compare-mode map ghost: controller emit on a real MapView,
exit restores the item state byte-identically, zero ghost work outside compare),
`test_validate_wallclock`, `test_gmeter`, `test_gmeter_overlay`, `test_export_data`,
`test_export_video` (the F9 video-export pure logic: trim math, per-frame overlay-value lookup,
ffmpeg-cmd construction + the mocked render pump — no ffmpeg needed; the real render is gated).

## State & next ideas

Next up: more tracks in `tracks.py` (+ real auto-detection), persist sector/start-line config per
file, and polish (keyboard shortcuts, optional snap toggle).
