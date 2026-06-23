# Changelog

All notable changes to Pacer are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_Nothing yet._

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
