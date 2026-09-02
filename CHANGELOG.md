# Changelog

All notable changes to Pacer are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Everything merged since v0.1.0 (~100 PRs), grouped by theme.

### Added

- **Session Statistics page** — a third page on the lap panel's **Laps | Corners | Stats**
  toggle (⌘⇧S or View ▸ Session statistics opens it as a full-window dashboard): session
  totals (time on track, moving time, distance, wall-clock window), the pace distribution
  (best / median / σ / race pace / consistency rating / laps-within-1% / a robust
  Theil–Sen trend), top-speed & peak-g tiles, a **g-g friction circle** with the
  demonstrated-envelope ring, brake/coast reductions, per-sector best/median/σ, a per-lap
  channel table, a sortable **corner-by-corner report** (worst corners tinted; rows ring
  the corner on the map), the **entry/apex/exit phase-loss** headline ("where the corner
  time goes"), **braking repeatability + commitment** per corner, the **straight-line
  report** with trap speeds and the exit-leverage **FIX FIRST** tile, a coaching digest
  ("fix your top 3 → projected lap"), and a **DATA TRUST card** surfacing the IMU↔GPS
  cross-check that was previously stdout-only.
- **Coaching front door** — the persistent top-3 opportunities panel under the lap table,
  the Δ-to-ideal hero readout, corner time-loss attribution (entry/apex/exit thirds), the
  braking-point optimizer ("you can brake ~N m later"), a synthetic brake/throttle band
  under the speed chart, the continuous ideal-lap delta, and grip-utilization map
  colouring.
- **Trust & honesty surfaces** — provisional-timing chrome for unverified start lines, the
  data-quality signal (media-clock fallback / dropped-fix fraction), honest ESTIMATED
  labelling on derived channels, colour-blind-safe cues, the "new personal best!" moment,
  and 1-based lap numbers everywhere.
- **Sharing** — the one-tap shareable lap-card image (with an honesty gate) and clean map
  exports.
- **Library v2** — trustworthy PB history per track, search, schema-bump backups,
  last-folder memory, and a guard against merging unrelated dropped recordings.
- **Tracks** — the track database (auto-detect + File ▸ Save as track), geometry
  track-match for unknown tracks, the peak-speed start-line heuristic, opt-in
  hill-compensated brake coaching, and an Elevation map channel.
- **Layout** — per-panel maximize (⛶ / double-click a header), window full screen (⌘⌃F),
  the fullscreen-video gesture, the calm-default left column (collapsible coaching +
  View-menu hide toggles), the ⊘ excluded-laps strip, and the welcome drop-zone with
  drag-and-drop import.
- **Speed units** toggle (km/h ↔ mph), persisted preferences, and an undo for start-line
  edits + a privacy disclosure / forget-recording flow.
- **Packaging & infra** — macOS .app/.dmg packaging (PyInstaller) with a build-only CI
  smoke, off-thread cancellable session load, bulk IMU bindings, the synthetic
  golden-equivalence CI gate, and the branded app icon + QApplication identity.

### Changed

- **The lap panel is a real tabbed panel** — one native tab bar (**Laps · Corners · Stats ·
  Coaching**, digits 1–4), every page at the panel's full height. The under-table coaching +
  consistency strips (and their height caps, collapse chevrons and View-menu hide toggles)
  are gone: coaching is a full tab, the consistency content lives in Stats (trend sparkline +
  the corner σ/tint columns). The active tab and any grid-splitter drag now persist across
  reloads and relaunches.
- `Session` decomposed into injected services (`Bests`, `CornerModel`, `DrivingChannels`,
  `Timeline`, `SessionStats`, the map render cache); `app.py` slimmed via extracted
  workers/overlays; compare-mode ownership unified under `CompareController`.
- Docs pivoted to the portfolio/craft showcase: the moat-first README, the GitHub Pages
  landing, `docs/ACCURACY.md` (the transponder-validation story), `AGENTS.md` as the
  single agent front door, and `studio/README.md` as a full module map.
- Contributor-governance boilerplate and dead ImGui-era config removed (the solo-dev +
  agents posture).

### Fixed

- A short mis-segmented lap can no longer be crowned session best (the lap-distance band
  in the real-lap filter); band-excluded laps are surfaced instead of silently vanishing.
- False "GPS quality low" on clean recordings (the dropped-fix denominator counted the
  trimmed stationary lead-in); clock-aware degraded-timing copy.
- `speed_long_g` run-seam NaN that silently dropped brake/coast events; the brake-onset
  seam blip.
- Library wipe on a schema bump (now backs up + reveals); undo/forget seam bugs.
- Share-card overflow, leaked chrome in map grabs, plot-overlay and corner-label
  collisions, and self-contradicting coaching copy.
- Demo-download UI freeze (socket timeout); single-flight loads + a GIL-friendly worker
  drain (CI deadlocks); the drift-gated per-corner loss alignment.
- **Lap and Corners tables now fit their panel.** Content-tight columns left a maximized
  Laps panel 79% empty (382 px of data, 1050 px parked in a blank spacer) and, at the
  default quadrant, pushed the Corners table's "Grip (est)" column and the sector columns
  the map's own "Add sector" button creates clean off the viewport. Spare width is now
  shared across the data columns (capped), a short panel gives the slack back down to each
  column's own values, headers elide to a tooltip instead of centre-clipping, and every lap
  and corner header carries its full text on hover.
- **Provisional timing demotes every start-line-derived column**, not the lap Time alone —
  Dist (the distance between crossings) and Entry (the speed at one) move with the start
  line too, and used to render at full confidence beside a greyed-out time. A degraded
  *clock* still demotes only the durations.
- **The pre-selected best lap is scrolled into view.** The app opens on your best lap and
  draws four panels from it, but the row itself sat below the viewport at every window
  size, so the lap table read as "nothing selected".

## [0.1.0] — 2026-06-22

First public release: a local desktop race-telemetry studio that turns a single GoPro
recording into a full telemetry workstation — no transponder, no extra hardware.

### Added

- **True-clock lap & sector timing** from the GoPro GPS9 stream on the camera's own clock,
  validated unbiased against a real transponder.
- **Speed-coloured track map** with auto-detected corners, brake points, and draggable
  start/sector lines for re-segmentation.
- **Distance-aligned Δ-to-best charts** (speed + cumulative time delta) so corners line up
  across laps.
- **Lap table** with sortable columns, per-sector splits, session bests (purple cells),
  theoretical-best and best-rolling footer rows, and GPS-dropout flags.
- **Synced GoPro video** — scrub the lap and the footage follows; **side-by-side two-lap
  compare**, including the best lap of *another* recording of the same track.
- **G-meter** overlay driven by the camera IMU (per-chapter camera→kart Procrustes fit, with
  an automatic fallback to a GPS-derived signal for helmet cams).
- **Driving channels** — brake, coasting, and grip derived from the trace (brake/coast on the
  GPS speed-derivative; lateral grip from the IMU).
- **Corner coaching** — the top time-loss opportunities with a measured reason and a one-click
  jump to that corner on the best lap.
- **Consistency analysis** — per-corner σ × time-loss ranking over the clean laps.
- **Exports** — burned-in telemetry video overlay (via ffmpeg) and per-lap channel CSV.
- **Session library** — a local index of analysed recordings with per-track PB progression.

### Engineering

- C++ core (GPMF ingest, geometry, GPS9 lap/sector segmentation) exposed to a PySide6 +
  pyqtgraph app via nanobind; reproducible builds via pixi.
- GPS-dropout laps are excluded from the headline best lap, the Δ-baseline, and session-best
  splits (their reconstructed distance / timing is less reliable), while still shown ⚠ in the
  table.
- Crash-safety guards for degenerate input: a co-located reference pair no longer produces a
  NaN start line, and non-finite GPS coordinates are dropped at the quality gate.

[Unreleased]: https://github.com/eenndan/pacer/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/eenndan/pacer/releases/tag/v0.1.0
