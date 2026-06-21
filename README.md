# Pacer

**A race-telemetry analysis studio for track days.** Pacer turns a single GoPro
recording into a full telemetry workstation — track map, lap-by-lap deltas, synced
video, and a g-meter — from the GPS and motion data the camera already records.
No transponder, no extra hardware.

> Local desktop app (macOS / Linux). Open an `.MP4`, get your laps.

<!-- Add a hero screenshot at docs/screenshot.png and it renders here. -->
<!-- ![Pacer studio](docs/screenshot.png) -->

## What it does

- **True-clock lap timing** — lap and sector times from the GoPro GPS9 stream on the
  camera's own clock, validated unbiased against a real transponder.
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

## Architecture

One desktop app on top of a small, fast C++ core:

- **`studio/`** — the product: a **PySide6 + pyqtgraph** desktop app, pure Python on
  top of the core via its bindings. See [studio/README.md](studio/README.md).
- **`pacer/`** — the **C++ core**: GPMF ingest, geometry, lap/sector segmentation,
  and GPS9 true-clock timing, exposed to Python through **nanobind**.
- **`bindings/`** — the nanobind bindings, generated from the C++ headers.

## Quick start

[pixi](https://pixi.sh) manages all external dependencies (`cmake`, `ninja`,
`catch2`, **`ffmpeg`** for video export). Build tooling is `cmake` + `litgen`
(binding codegen) glued via `scikit-build-core`.

```bash
git submodule update --init --recursive   # 3rdparty deps (gpmf-parser, nanobind)
pixi install                              # environment + editable Python bindings
pixi run studio -- /path/to/GX010060.MP4  # build + launch on a recording
```

GoPro chapter siblings (`GX01…`, `GX02…`) are chained automatically.

## Development

```bash
pixi run build   # configure + build everything (cmake + Ninja)
pixi run test    # C++ (Catch2) + Python tests via ctest
pixi run fmt     # clang-format the C/C++ sources
pixi run lint    # ruff
```

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
