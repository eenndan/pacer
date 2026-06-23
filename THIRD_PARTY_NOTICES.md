# Third-Party Notices

Pacer's own source code is licensed under **CC BY-NC-SA 4.0** (see [LICENSE](LICENSE)). It builds
on, links against, and (when packaged as a macOS app — see [docs/PACKAGING.md](docs/PACKAGING.md))
**bundles** the third-party components listed below, each of which is governed by **its own license**,
not Pacer's. A redistributed binary must carry these notices.

| Component | Role | License |
|---|---|---|
| [gpmf-parser](https://github.com/gopro/gpmf-parser) (GoPro) | GPMF telemetry parsing (git submodule) | Apache-2.0 |
| [nanobind](https://github.com/wjakob/nanobind) (Wenzel Jakob) | C++ ↔ Python bindings (git submodule) | BSD-3-Clause |
| [PySide6 / Qt](https://www.qt.io/qt-for-python) | GUI + multimedia (dynamically linked; bundled in the app) | LGPL-3.0 |
| [pyqtgraph](https://www.pyqtgraph.org/) | charts / plotting | MIT |
| [NumPy](https://numpy.org/) | numerics | BSD-3-Clause |
| [FFmpeg](https://ffmpeg.org/) | video decode/encode (invoked as a subprocess; **bundled in the app**) | LGPL-2.1+ / GPL-2.0+ (build-dependent) |
| [litgen](https://github.com/pthom/litgen) | binding code generation (**build-time only**, not distributed) | MIT |

Each component's full license text is available in its upstream repository (and, for the git
submodules, under `3rdparty/`).

## Notes for redistribution

- **FFmpeg**: the packaged macOS app bundles `ffmpeg`/`ffprobe` binaries. Ship an FFmpeg build whose
  license you can comply with (an LGPL build is the safest for redistribution) and include its license
  text alongside the binary. The dev/source workflow uses the system/pixi `ffmpeg` on `PATH` and
  bundles nothing.
- **Qt / PySide6 (LGPL-3.0)**: distributed dynamically linked (PyInstaller ships the Qt frameworks).
  LGPL relinking obligations apply to the distributed app.

## A note on the project license

CC BY-NC-SA 4.0 is a content license, not one designed for software (it grants no patent rights, and
its terms predate common software-distribution concerns). It is used here as a **deliberate choice to
keep Pacer NonCommercial**, carried over from the project's origin. This is a known consideration; if
the NonCommercial restriction is ever relaxed, a software-specific license (e.g. a source-available or
GPL-family license) would be the natural replacement. The third-party components above keep their own
(more permissive) terms regardless.
