# Pacer Studio — the module map

A local **PySide6 + pyqtgraph** desktop app for race-telemetry analysis — a greenfield UI
on top of the existing C++ `pacer` core (reused via its nanobind Python bindings). Chosen
for a single-language, LLM-editable codebase that still nails draggable map handles and
frame-accurate video↔telemetry sync (all in Python — see [the spike](dev/spike_video_sync.py)).

This file is the map: the rules, where a change goes, and one line per module. Each module's
design notes, rationale and measurement history are in [docs/module-notes.md](docs/module-notes.md),
one section per module in this order — read the one you need, not the whole file.

## Run

```bash
pixi run studio                              # the welcome screen: drop or open a recording
pixi run studio -- /path/to/GX010062.MP4     # one chapter only (DEFAULT — single-file, as before)
pixi run studio -- --full /path/to/GX010062.MP4  # opt-in: discover + chain ALL sibling chapters
pixi run studio -- a.MP4 b.MP4               # explicit chaptered recording (chained in order)
```

A GoPro recording is split into chapters (`GX<CC><NNNN>.MP4`); opening one loads only that file, and
`--full` or **File ▸ Load full recording** chains its siblings (same `NNNN`, same folder, by `CC`).
`--demo` opens the demo clip when one resolves ([`demo.py`](demo.py)). Equivalent without pixi:
`python -m studio [files]`. Dev tools live in [`dev/`](dev/): diagnose a file headlessly with
`pixi run python -m studio.dev.diagnose -- file.MP4 [--clean]`, measure GPS smoothing with
`studio.dev.denoise_check`, and see [`tests/README.md`](../tests/README.md) for the golden dump.

## Layout

```
┌──────────────┬───────────────────────────┐
│  VideoView   │   MapView (track + lines) │   video ⇄ telemetry sync:
├──────────────┼───────────────────────────┤   • video plays → red map marker sweeps
│  Lap panel   │   PlotsView (speed/delta) │   • drag the marker → video seeks
│  Laps·Corners│                           │   • drag a plot cursor → scrub within the lap
│  ·Stats      │                           │   • drag start/sector lines → re-segment laps
│  ·Coaching   │                           │   • digits 1-5 → switch the lap panel's tab
│  ·Marks      │                           │   • ⌘K → the command palette (run anything by name)
└──────────────┴───────────────────────────┘   • ⌘L → the session library (past sessions + PBs)
```

## Architecture rules (do not violate)

1. **Layering (both directions).** Only `session.py`, `load.py`, `ingest.py`, `tracks.py` import
   the `pacer` core — every view / controller / helper is **pacer-free** and goes through
   `Session`. Mirror rule: only the view / Qt-infrastructure modules import **Qt** — the
   analysis / pipeline / persistence layer stays importable headlessly, directly *and* through
   studio's own import graph. All three allow-lists (`ALLOWED`, `ALLOWED_QT`, `QT_REACHING`) are
   pinned by exact equality in [`tests/test_layering.py`](../tests/test_layering.py); a new view
   or pipeline module updates the set in the same PR. Data flows ingest → load → session →
   controllers → views, the order of the tables below.
2. **Local metres.** The trace *and* the timing lines live in **local metres** (`cs.local`), not
   lat/lon; `pacer/laps` converts back to GPS internally for crossing tests. Mixing the two frames
   is the #1 hazard.
3. **Core-math is gated.** Any change to timing (the GPS9 clock) / geometry / delta / segmentation
   must preserve the golden equivalence — `pixi run golden` (synthetic, in CI) and `max|Δ|=0` on the
   manual real-footage dump, on the working-set recording `PACER_GOLDEN_MP4` names — by default
   `MK_18_09_26`, D24's own circuit (see [tests/README.md](../tests/README.md)).
4. **Perf invariants.** UI sync runs on a ~30 Hz `QTimer` (`CentralView.tick`) off the video's
   present path, and each tick is a cheap lookup on cached arrays: nothing is recomputed or
   re-plotted per frame. Derived views rebuild on an event (load, re-segment, a lap or mode
   change), never per tick, and a hidden Stats page defers its render; scrub seeks coalesce to
   ≤ 1 per tick; plot curves are
   downsampled + clipped with antialias off and autorange frozen; the map draws ≤ 2 laps.
5. **One quantity, one source.** A number two surfaces show comes from one accessor on `Session`
   or its services; views and exports read it and never re-derive it, and a statistic whose
   signal is absent is `None`, never a fake 0.
6. **Stores.** Every store under app-support resolves its directory through its `_app_support_dir`
   seam → [`app_support.resolve()`](app_support.py), which jails tests, and every store reads,
   writes and locks through [`_jsonstore`](_jsonstore.py): a unique temp + `os.replace`, and every
   load-modify-save under `_jsonstore.locked`. A new one copies `library.py`'s discipline (schema
   version, `.bak`).

## Common changes → files to touch

| I want to… | Edit | Pin it with |
|---|---|---|
| Add / refine a track | `track_db.py` (store + seeds) ← `tracks.py` (detect → segment) | `test_track_db` |
| Rename / delete a saved track | `track_db.py` + `track_dialog.py` + `LibraryController._rename_track` (a rename re-keys library, focus and records; a delete, none) | `test_track_db` |
| Tune GPS smoothing | `_signal.SMOOTH_WINDOW` + `load._smooth_track`; measure with `dev/denoise_check.py` | `test_session_pure` |
| Add / tune a driving channel | `driving.py` → `driving_channels.py` → overlays in `plots_view.py` / `map_view.py` | `test_driving` |
| Add a Stats-page statistic | `stats.py` (reducer + `SessionStats`) → its section's `stats_*.py` | `test_stats` |
| Show the ideal lap somewhere new | read `session.ideal_total` / `delta_to_ideal`, gate on `ideal_donor_lap_id() is None`, caption with `IdealSample.caption()` | `test_session_pure`, `test_export_disclosures` |
| Change what coaching says or claims | `coaching.py` (`reason_sentence`, `corner_evidence`, `session_theme`); surfaces never re-derive | `test_coaching` |
| Change a cross-session verdict | `focus.verdict` (a refusal carries `delta=None`) | `test_focus_list` |
| Add a lap-table column | `lap_table.py` | `test_studio_features` |
| Add a session-record field | `session_record.py` (fields, normalizer, `_migrate`) → `session_record_dialog.py` | `test_session_record` |
| Add a mark type or auto-detector | `marks.py` (an auto mark reads an existing detector) → `theme.mark_colour` | `test_marks` |
| Add an export / overlay field | `export_video.py` + `gmeter_overlay.py` (compare: `export_compare.py`) | `test_export_video` |
| Add a shortcut or command | `help_dialog.COMMANDS` (the `?` card and ⌘K derive from it) + the method in `app.py` | `test_help_dialog` |
| Change km/h ↔ mph handling | `units.py` + its call sites | `test_units` |
| Add a studio module | a row below (a Stats section: a link in its row); `ALLOWED_QT` / `QT_REACHING` if it reaches Qt | `test_layering` |

The long form of each row is in [module-notes.md](docs/module-notes.md#common-changes-in-full).

## Modules

One row per `studio/*.py`; each Stats section's `stats_*.py` is a one-word link in `stats_panel`'s.
**Imports** is the layer in one word, checked against the code by
`tests/test_layering.py`: `pacer` = imports the C++ core (these four only) · `Qt` = imports Qt ·
`→Qt` = no Qt of its own, but a studio module it imports loads it · `—` = neither, so it imports
headless. **Test** is the file under [`tests/`](../tests/) that pins the module (`—`: none).
A change to what a module does goes in its docstring (and its notes section, if that now says
otherwise); this map changes when a module is added, removed or renamed, or changes layer.

Note the four **Session-bound service twins** that pair a pure algorithm module with its
Session-caching service: `corners.py`→`corner_model.py`, `driving.py`→`driving_channels.py`,
plus `bests.py` and `timeline.py` (extracted straight off the Session facade). Edit the
algorithm; the service just caches + delegates.

### Pipeline: ingest → load → session

| Module | Responsibility | Imports | Test |
|---|---|---|---|
| [session.py](session.py) | The `Session` facade: trace/lap/delta/sector accessors, timing-line write-back, per-lap caches | pacer | `test_session_pure` |
| [load.py](load.py) | `Session.load`'s pipeline: quality gate, cleaning, GPS9 true clock, smoothing, segmentation, start-line and media-clock fits | pacer | `test_load_pipeline` |
| [ingest.py](ingest.py) | GPMF reading on one chapter chain: GPS, ACCL/GRAV/CORI/GYRO and DVNM on one global clock | pacer | `test_ingest_equivalence` |
| [tracks.py](tracks.py) | Track detection: matches a trace to `track_db`, builds the start/sector `pacer.Segment`s | pacer | `test_track_db` |

### Analysis and Session services

| Module | Responsibility | Imports | Test |
|---|---|---|---|
| [_signal.py](_signal.py) | Signal helpers: boxcar smoother, gap split, GPS quality gate, real-lap band, `fmt_time` | — | `test_session_pure` |
| [corners.py](corners.py) | Corners from the session's own curvature, no map-matching; corners + straights partition a lap | — | `test_corners` |
| [corner_model.py](corner_model.py) | Service over `corners`: corner list, per-lap stats, segment bests (the ideal lap), resolved cells | — | `test_session_services` |
| [driving.py](driving.py) | Brake and coast events on GPS long-g, the brake/throttle band, brake-point optimizer, corner grip | — | `test_driving` |
| [driving_channels.py](driving_channels.py) | Service over `driving`: caches events, spans, grip and the session thresholds | — | `test_session_services` |
| [consistency.py](consistency.py) | Lap/sector/corner σ, median loss vs best, the inconsistency ranking, running-PB mask | — | `test_consistency` |
| [coaching.py](coaching.py) | Per-corner loss, its measured reason, reach/abstain evidence, the session theme, brake habits | — | `test_coaching` |
| [focus.py](focus.py) | The per-track focus list (`focus.json`) and its gated cross-session verdict | — | `test_focus_list` |
| [stats.py](stats.py) | Stats-page reducers + `SessionStats`: totals, per-lap stats, bands, stints, split/corner grids | — | `test_stats` |
| [bests.py](bests.py) | Service: best lap, best splits, theoretical best (= the ideal lap), best rolling lap | — | `test_session_pure` |
| [timeline.py](timeline.py) | Cursor/plot/video conversions: plot-x ↔ media time, lap at time, nearest point | — | `test_timeline` |
| [render_cache.py](render_cache.py) | Per-lap map-draw cache: gap-aware trace segments, the reference-centerline donor | — | `test_reference` |
| [gapfill.py](gapfill.py) | Map-only GPS-gap fill: cross-lap borrow, reference centerline or spline, tagged inferred | — | `test_gapfill` |
| [reference.py](reference.py) | The georeferenced MK centerline ([`mk_centerline.json`](mk_centerline.json)), loop-to-loop fit | — | `test_reference` |
| [cross_reference.py](cross_reference.py) | Another recording's best lap as the reference, aligned by normalized distance | — | `test_cross_reference` |
| [track_match.py](track_match.py) | Same-circuit test from two GPS footprints: the cross-recording gate | — | `test_track_match` |
| [gmeter.py](gmeter.py) | Kart-frame g from ACCL/GRAV/CORI (de-drifted yaw fit): IMU lateral, GPS longitudinal | — | `test_gmeter` |
| [rotation.py](rotation.py) | Measured yaw rate from GYRO, its check against the path, the GPS-behind-gyro lag | — | `test_rotation` |
| [media_clock.py](media_clock.py) | One affine telemetry→media clock map per recording, applied where the app seeks and exports | — | `test_media_clock` |
| [chapters.py](chapters.py) | Chapter names, sibling discovery, the not-video guard, `ChapterMap` global ↔ chapter time | — | `test_chapters` |
| [data_quality.py](data_quality.py) | Timing-accuracy verdict, per-second `QualityTimeline`, the export quality markers | — | `test_data_quality` |
| [provenance.py](provenance.py) | What produced a displayed number: window, raw fixes, method, the re-derived value | — | `test_provenance` |
| [chart_stats.py](chart_stats.py) | The charts' instruments: datum interval, visible-window stats, Δ-loss ranking | — | `test_chart_instruments` |
| [map_render.py](map_render.py) | The map's numpy core: rainbow channels, bucketing, Δ resampling, Δ rate | →Qt | `test_map_render` |
| [units.py](units.py) | km/h ↔ mph at the display boundary: the single source of truth | — | `test_units` |
| [gearing.py](gearing.py) | Single-speed kart RPM from road speed; wired to no surface on purpose | — | `test_gearing` |

### Persistence

| Module | Responsibility | Imports | Test |
|---|---|---|---|
| [app_support.py](app_support.py) | Where every store and the log live, and the jail that keeps tests out | — | `test_app_support_jail` |
| [_jsonstore.py](_jsonstore.py) | Every store's reader, atomic unique-temp write and cross-process lock | — | `test_jsonstore` |
| [logsetup.py](logsetup.py) | The app log: stderr plus a rotating `logs/pacer.log` under app-support | — | `test_session_log` |
| [library.py](library.py) | The session library `library.json`: one entry per recording fingerprint, PB series | — | `test_library` |
| [track_db.py](track_db.py) | `tracks.json` + built-in circuits; rename/delete with refusals and a `.bak` | — | `test_track_db` |
| [session_record.py](session_record.py) | Per-recording conditions, kart no., tyres and setup (`session_records.json`), `prefill` | — | `test_session_record` |
| [marks.py](marks.py) | Manual marks (`marks.json`) and derived auto marks, anchored to chapter time | — | `test_marks` |
| [sidecar.py](sidecar.py) | Timing lines beside the MP4 (`<stem>.pacer.json`), in absolute lat/lon | — | `test_sidecar` |
| [prefs.py](prefs.py) | UI preferences `prefs.json`: units, palette, active tab, splitter sizes | — | `test_prefs` |
| [demo.py](demo.py) | Resolves the `--demo` clip (env → cache → release asset); `demo_available()` is offline | — | `test_demo` |

### Controllers

| Module | Responsibility | Imports | Test |
|---|---|---|---|
| [playback_state.py](playback_state.py) | The one owner of the per-frame playback / scrub / auto-follow cursor | — | `test_controllers` |
| [scrub_controller.py](scrub_controller.py) | Plot-cursor scrub: lap-scoped drag, ≤ 1 seek per tick | — | `test_controllers` |
| [compare_controller.py](compare_controller.py) | Dual-lap compare: pinned pair, enter/exit, pane times, Δ badges, map ghost | →Qt | `test_compare` |
| [export_controller.py](export_controller.py) | The Qt side of every export: File ▸ Export, writers, share card, video flow | Qt | `test_export_gates` |
| [library_controller.py](library_controller.py) | Library + PB moment, session records, focus list, saved tracks, Open Recent | Qt | `test_library` |

### Views

| Module | Responsibility | Imports | Test |
|---|---|---|---|
| [app.py](app.py) | `StudioWindow`: menus, shortcuts, session notice, async load, overlays, crash reporter | Qt | `test_studio_features` |
| [central_view.py](central_view.py) | One recording's panels, tabbed lap panel, 30 Hz `tick()`, maximize, saved layout | Qt | `test_central_view_realqt` |
| [video_view.py](video_view.py) | Player shell: transport, playback rate, slider + quality strip + marks band, compare panes | Qt | `test_video_view_compare` |
| [player_pane.py](player_pane.py) | One player: global time across chapters, the telemetry ↔ media seam, the g-meter window | Qt | `test_compare` |
| [map_view.py](map_view.py) | Track map: laps, draggable timing lines, marker, corner/brake glyphs, rainbow, ghost | Qt | `test_rainbow_map` |
| [plots_view.py](plots_view.py) | Speed + Δ charts on one linked x-axis: scrub cursor, ideal and driving overlays, instruments | Qt | `test_charts_panel` |
| [lap_table.py](lap_table.py) | The sortable Laps table (splits, ⚠ dropout) and the Corners table | Qt | `test_lap_table_columns` |
| [stats_panel.py](stats_panel.py) · [common](stats_common.py) [braking](stats_braking.py) [ideal](stats_ideal.py) [straights](stats_straights.py) [trust](stats_trust.py) | The Stats page: `StatsView` shell (layout, tiles, charts, unsplit sections); one module per split section | Qt | `test_stats` `test_stats_ideal` |
| [coaching_panel.py](coaching_panel.py) | Coaching page + Opportunities dialog: theme and focus blocks, per-corner rows | Qt | `test_coaching` |
| [marks_panel.py](marks_panel.py) | The Marks page and mark editor; emits intents, owns no store | Qt | `test_marks` |
| [provenance_panel.py](provenance_panel.py) | The read-only "inspect this number" panel over one `Provenance` | Qt | `test_provenance_panel` |
| [gmeter_overlay.py](gmeter_overlay.py) | The g-meter dial, live and burned into exports (`paint_dial`) | Qt | `test_gmeter_overlay` |
| [overlays.py](overlays.py) | The welcome state (Open demo only when one resolves) and the PB toast | Qt | `test_first_run_path` |
| [help_dialog.py](help_dialog.py) | `COMMANDS`, the one command registry, and the Help-menu dialogs | Qt | `test_help_dialog` |
| [command_palette.py](command_palette.py) | ⌘K, generated from the live menu bar and `COMMANDS` | Qt | `test_command_palette` |
| [library_dialog.py](library_dialog.py) | File ▸ Library…: sessions, PB chart, session records; writes nothing | Qt | `test_library` |
| [session_record_dialog.py](session_record_dialog.py) | The session-record form; no store I/O (remembers its "Own kart…" fold in prefs) | Qt | `test_session_record` |
| [track_dialog.py](track_dialog.py) | Saved-tracks manager: rename/delete through injected callbacks | Qt | `test_track_db` |
| [widgets.py](widgets.py) | Shared Qt primitives: `PanelHeader`, `PanelToolbar`, `WrapLabel` | Qt | `test_design_system` |
| [theme.py](theme.py) | The design system: tokens `C`, fonts, palette + QSS, icons, brand mark | Qt | `test_design_system` |
| [workers.py](workers.py) | QThread workers for the load and the video export | Qt | `test_export_gates` |

### Export

| Module | Responsibility | Imports | Test |
|---|---|---|---|
| [export_video.py](export_video.py) | Offline overlay-video renderer: ffmpeg decode → QPainter → VideoToolbox / libx264 | Qt | `test_export_video` |
| [export_compare.py](export_compare.py) | The distance-locked two-lap compare export (a `Renderer` subclass) | Qt | `test_export_compare` |
| [export_palette.py](export_palette.py) | `EXPORT`, the burn-over-footage colours the renderer and the dial share | — | `test_export_video` |
| [export_data.py](export_data.py) | Laps/channels CSV, the HTML report, the stats summary; atomic writes | — | `test_export_data` |
| [share_card.py](share_card.py) | The shareable lap-card PNG; blocked while timing is provisional | Qt | `test_share_card` |

### Package

| Module | Responsibility | Imports | Test |
|---|---|---|---|
| [\_\_init\_\_.py](__init__.py) | `__version__` (canonical) and `APP_NAME`; a leaf that imports no submodule | — | `test_version` |
| [\_\_main\_\_.py](__main__.py) | `python -m studio [files]`, which `pixi run studio` runs | →Qt | — |

## Tests

Pure-Python studio tests live under [`tests/`](../tests/), each registered with CTest, so
`pixi run test` runs them alongside the C++ Catch2 suites. [tests/README.md](../tests/README.md)
has the inventory, the golden gate and the real-footage checks; AGENTS.md, how to run one.
