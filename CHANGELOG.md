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
- Unknown-track recordings are no longer quarantined in the Library: a recording with valid
  laps is listed, selectable, openable and offered in Open Recent whether or not its circuit
  is in your track database — it just carries the usual "provisional" tag instead of a
  bogus "(no laps)" beside its own best lap.
- The Library names the recording FILE: every row hovers to its filename + full path, and
  the "forget this recording" confirm leads with it, so two sessions from the same day on
  the same unknown track are told apart before a sidecar is deleted.
- The PB chart's explanatory empty-state sentence is actually visible (it was positioned in
  data coordinates on a pixel-space item, ~1.8e9 px off-screen) and stays centred on resize.
- The library's progress line no longer counts your FIRST session on a track as a personal
  best ("1 session · 1 PB"); PBs now count only sessions that beat the running best.
- Share-card overflow, leaked chrome in map grabs, plot-overlay and corner-label
  collisions, and self-contradicting coaching copy.
- Demo-download UI freeze (socket timeout); single-flight loads + a GIL-friendly worker
  drain (CI deadlocks); the drift-gated per-corner loss alignment.
- **A failed reload no longer strands the window on an endless "Loading telemetry…" card** —
  the loaded session's UI is handed back before the error dialog claims it is unchanged.
  Relatedly, a reload no longer blanks a working session at all unless the load runs past
  400 ms, and the dialog's reassurance is now stated only where it is true.
- **Load errors name the actual problem and a next step** — a folder, a missing path, a
  0-byte file, an unreadable file, a truncated *real* GoPro chapter and a non-GoPro file
  each get their own message (a truncated GoPro chapter used to read "this doesn't look
  like a GoPro recording").
- **The multi-recording drop warning survives the load it starts** and stays discoverable
  for the session, instead of being overwritten mid-load; a multi-recording drop now opens
  the recording that was dropped first rather than the alphabetically first.
- **The "start/finish line was auto-fitted — drag it into place" status line retracts when
  you place the line**, re-decided from the same seam that rebuilds the derived views (it
  used to survive byte-identical across the very drag that answered it).
- Coaching's "~N s longer on the brakes" no longer counts whole brake events by their onset
  alone: the time each application spends *inside* the corner window is integrated on the
  lap's own clock, so a brake that begins a few metres before the window is no longer scored
  as zero. On the D24 session the C3 advice drops from "~0.90 s" to "~0.67 s" against a
  corner losing 0.109 s.
- Every ranked corner in the Opportunities list now carries a measured reason; rows below
  the top three used to print "find time here" because they were never analysed.
- Sector splits that meant a different stretch of track on different rows. A sector line
  dropped within the collapse tolerance of another used to be fused **per lap**, so in a
  ~2 m band some laps kept the pair and some fused it — one lap's S2 was a 17 s sector
  beside another's 0.2 s sliver, the purple session best could land on a sliver, and the
  theoretical best was summed from pieces that never tiled a lap. The decision is now made
  once for the whole session, so every lap reports the same boundaries and every S column
  is the same stretch of track on every row.

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
