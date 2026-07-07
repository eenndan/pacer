# Pacer

**A race-telemetry analysis studio for track days.** Pacer turns a single GoPro
recording into a full telemetry workstation — track map, lap-by-lap deltas, synced
video, and a g-meter — from the GPS and motion data the camera already records.
No transponder, no extra hardware.

> Local desktop app (macOS, Apple Silicon). Open an `.MP4`, get your laps.

![Pacer — speed-coloured track map with corners and brake points, dual-lap speed and Δ-to-best charts, lap table with session bests, and synced GoPro video](docs/screenshot.png)

## What it does

- **True-clock lap timing** — on a **GPS9 camera (Hero 9 and newer)**, lap and sector times
  come from the GPS9 stream on the camera's own clock, validated unbiased against a real
  transponder. Older GPS5 cameras (Hero 5–7) carry no per-sample clock, so Pacer falls back to
  the video clock (approximate — it runs ~0.1% fast); the app flags this so the times read as
  estimates.
- **Track map** — the racing line coloured by speed, with brake points and the
  start/sector lines you can drag to re-segment.
- **Δ-to-best charts** — speed and cumulative time delta against your best lap,
  distance-aligned so corners line up.
- **Lap table** — every lap and its sector splits, sortable, best-lap highlighted.
- **Synced GoPro video** — scrub the lap and the footage follows; play two laps
  **side by side**, including the best lap of *another* recording of the same track
  ("race a friend's GoPro file").
- **G-meter** — a live accelerometer overlay driven by the camera's IMU.
- **Driving channels** — brake, coasting and grip derived from the trace.
- **Video overlay export** — burn the telemetry overlay onto the footage (via
  ffmpeg) for sharing.
- **Session library** — a local index of everything you've analysed.

## Get Pacer

**A Mac (Apple Silicon) and a GoPro is all you need.**

1. **Download** the latest **`Pacer Studio.app`** (`.dmg`) from the
   [**Releases**](https://github.com/eenndan/pacer/releases) page — no pixi, Python, or build
   tools required on your Mac. *(The build is currently unsigned, so on first launch **right-click
   the app ▸ Open** to get past Gatekeeper — see [docs/PACKAGING.md](docs/PACKAGING.md#gatekeeper).
   No release binary yet? Build one yourself in one command — see [Build from source](#build-from-source).)*
2. **Open a GoPro `.MP4`** — drag it onto the window, or `File ▸ Open`.
3. **Read your first lap** — the [**First lap walkthrough**](docs/FIRST_LAP.md) shows you the
   30-second path from footage to "where am I losing time?"

> On a track Pacer doesn't know yet, it places a sensible start/finish line for you and flags the
> timing as *provisional* — drag the line on the map to where a lap begins and it's remembered for
> that recording. See the walkthrough.

## Architecture

One desktop app on top of a small, fast C++ core:

- **`studio/`** — the product: a **PySide6 + pyqtgraph** desktop app, pure Python on
  top of the core via its bindings. See [studio/README.md](studio/README.md).
- **`pacer/`** — the **C++ core**: GPMF ingest, geometry, lap/sector segmentation,
  and GPS9 true-clock timing, exposed to Python through **nanobind**.
- **`bindings/`** — the nanobind bindings, generated from the C++ headers.

## Build from source

*For developers, or to produce your own `.dmg`.* [pixi](https://pixi.sh) manages all external
dependencies (`cmake`, `ninja`, `catch2`, **`ffmpeg`** for video export). Build tooling is
`cmake` + `litgen` (binding codegen) glued via `scikit-build-core`.

```bash
git submodule update --init --recursive   # 3rdparty deps (gpmf-parser, nanobind)
pixi install                              # environment + editable Python bindings
pixi run studio -- /path/to/GX010060.MP4  # build + launch on a recording
```

GoPro chapter siblings (`GX01…`, `GX02…`) are chained automatically.

To explore without your own footage, `pixi run studio -- --demo` opens a real demo lapping
recording (fetched once at runtime — nothing large is committed). See
[docs/PACKAGING.md](docs/PACKAGING.md#demo-data).

## Install / Packaging

To build a standalone, double-clickable **`Pacer Studio.app`** + a drag-to-install `.dmg` (no
pixi / Python needed on the target Mac), see **[docs/PACKAGING.md](docs/PACKAGING.md)** —
`packaging/build_macos.sh` produces an unsigned app via PyInstaller; that doc also covers the
codesign + notarize + staple steps for distribution.

## Development

**[AGENTS.md](AGENTS.md)** is the authoritative developer reference — the full build / test / lint
workflow, the architecture and studio module maps, the conventions, and the core-math golden gate.
Start there for any code change.

## Acknowledgements

Pacer began as a fork of [dendi239/pacer](https://github.com/dendi239/pacer) by
Denys Smirnov, whose original C++ core seeded the project. It has since been
substantially rewritten and is now developed independently. Thanks to Denys for
the foundation.

GPS/IMU parsing uses GoPro's [gpmf-parser](https://github.com/gopro/gpmf-parser).

## License

Pacer © 2025-2026 eenndan, licensed under
[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) — see
[LICENSE](LICENSE). NonCommercial use only; derivatives must be shared alike.

This applies to Pacer's own code. Bundled and linked third-party components (GoPro gpmf-parser,
nanobind, Qt/PySide6, FFmpeg, …) keep their own licenses — see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), which a redistributed app must carry.
