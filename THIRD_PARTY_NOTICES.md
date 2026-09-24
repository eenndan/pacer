# Third-Party Notices

Pacer's own source code is licensed under **CC BY-NC-SA 4.0** (see [LICENSE](LICENSE)). It builds
on, links against, and (when packaged as a macOS app — see [docs/PACKAGING.md](docs/PACKAGING.md))
**bundles** the third-party components listed below, each of which is governed by **its own license**,
not Pacer's. A redistributed binary must carry these notices. Nothing is redistributed today: no
release carries a build of the app (`demo-data-v1` holds only the synthetic demo recording, which
is data).

| Component | Role | License |
|---|---|---|
| [gpmf-parser](https://github.com/gopro/gpmf-parser) (GoPro) | GPMF telemetry parsing (git submodule) | Apache-2.0 OR MIT, at your option (`LICENSE-APACHE`, `LICENSE-MIT`) |
| [nanobind](https://github.com/wjakob/nanobind) (Wenzel Jakob) | C++ ↔ Python bindings (git submodule) | BSD-3-Clause |
| [PySide6 / Qt](https://www.qt.io/qt-for-python) | GUI + multimedia (dynamically linked; bundled in the app) | LGPL-3.0 |
| [pyqtgraph](https://www.pyqtgraph.org/) | charts / plotting | MIT |
| [qtawesome](https://github.com/spyder-ide/qtawesome) (and the [QtPy](https://github.com/spyder-ide/qtpy) shim it imports) | the UI's icons; bundled whole, with the [twelve icon fonts](#the-icon-fonts-qtawesome-bundles) below | MIT |
| [NumPy](https://numpy.org/) | numerics | BSD-3-Clause |
| [Python](https://www.python.org/) | the interpreter and standard library (bundled in the app) | Python-2.0 (PSF) |
| [FFmpeg](https://ffmpeg.org/) | video decode/encode (a separate program the app runs as a subprocess; **bundled in the app**) | **GPL-3.0-or-later**: the locked `gpl_h670d5b4_111` build ([below](#ffmpeg-read-this-before-redistributing-the-app)) |
| [x264](https://www.videolan.org/developers/x264.html) and [x265](https://www.videolan.org/developers/x265.html) | the H.264 and HEVC encoders that ffmpeg build links (bundled with it) | GPL-2.0-or-later |
| [litgen](https://github.com/pthom/litgen) | binding code generation (**build-time only**, not distributed) | MIT |

Each component's full license text is available in its upstream repository (and, for the git
submodules, under `3rdparty/`).

## FFmpeg: read this before redistributing the app

- **What is locked.** `pixi.lock` pins conda-forge's `ffmpeg-7.1.1-gpl_h670d5b4_111`, and that is
  the binary `packaging/pacer.spec` bundles (the first `ffmpeg` on `PATH` in the pixi env). Its own
  `ffmpeg -L` says **GPL, version 3 or any later version**: it is configured `--enable-gpl
  --enable-version3` and links libx264 and libx265 (both GPL-2.0-or-later). The conda package
  records GPL-2.0-or-later; `--enable-version3` is what makes the binary version 3.
- **x264 is not a passenger.** libx264 is the export's software encoder: `studio/export_video.py`
  (`SW_H264`) falls back to it whenever a VideoToolbox session will not open.
- **What a redistributed `.app` owes.** The GPLv3 text beside the binaries, and the complete
  corresponding source of that ffmpeg, x264 and x265: shipped with it, or a written offer valid
  for at least three years (GPLv3 section 6). ffmpeg runs as its own process; Pacer does not link
  it. The binary also links 58 more libraries from about 40 conda-forge packages (openh264, libvpx,
  dav1d, aom, opus, lame, OpenSSL and others; `otool -L`, measured 2026-09-24), each under its own
  license, which its package records in `.pixi/envs/default/conda-meta/<package>.json`. Their
  notices ship too.
- **The alternative: the `lgpl_*` variant.** conda-forge publishes the same ffmpeg for osx-arm64 as
  an LGPL build (`ffmpeg-7.1.1-lgpl_hd3b5383_11`: LGPL-2.1-or-later, no x264 or x265). Pinning it
  leaves libopenh264 (BSD-2-Clause, compiled into both variants) as the only software H.264
  encoder, so the fallback has to move to it, and that is a code change, not a swap: the fallback
  passes libx264's own `-preset` and `-crf` options, which openh264 does not take. An LGPL build
  still owes its license text and its source.
- **Building from source bundles nothing:** the dev workflow runs the pixi `ffmpeg` on `PATH`.

## The icon fonts qtawesome bundles

`packaging/pacer.spec` collects qtawesome whole (`collect_all("qtawesome")`), so the `.app` carries
all twelve fonts qtawesome 1.4.2 ships. The app draws only **Phosphor** (every `theme.icon()` name is
`ph.*`), but the other eleven cannot be left out: on its first icon qtawesome checks and loads every
font it bundles, and a copy holding only the Phosphor files fails there (`FileNotFoundError` on
`fontawesome5-regular-webfont-5.15.4.ttf`, measured 2026-09-24). Licenses as qtawesome's own README
states them (of the files themselves, only Phosphor's names one: MIT):

| Font (qtawesome prefix) | Files | License |
|---|---|---|
| Phosphor 1.3.0 (`ph`): the one the app draws | `phosphor-1.3.0.ttf` | MIT |
| Font Awesome Free 5.15.4 (`fa5`, `fa5s`, `fa5b`) | `fontawesome5-regular-webfont-5.15.4.ttf`, `fontawesome5-solid-webfont-5.15.4.ttf`, `fontawesome5-brands-webfont-5.15.4.ttf` | SIL OFL 1.1 |
| Font Awesome Free 6.7.2 (`fa6`, `fa6s`, `fa6b`) | `fontawesome6-regular-webfont-6.7.2.ttf`, `fontawesome6-solid-webfont-6.7.2.ttf`, `fontawesome6-brands-webfont-6.7.2.ttf` | SIL OFL 1.1 |
| Elusive Icons 2.0 (`ei`) | `elusiveicons-webfont-2.0.ttf` | SIL OFL 1.1 |
| Material Design Icons 5.9.55 and 6.9.96 (`mdi`, `mdi6`) | `materialdesignicons5-webfont-5.9.55.ttf`, `materialdesignicons6-webfont-6.9.96.ttf` | Apache-2.0 |
| Remix Icon 2.5.0 (`ri`) | `remixicon-2.5.0.ttf` | Apache-2.0 |
| Codicons 0.0.36 (`msc`) | `codicon-0.0.36.ttf` | CC BY 4.0 |

## Other notes for redistribution

- **Qt / PySide6 (LGPL-3.0)**: distributed dynamically linked (PyInstaller ships the Qt frameworks).
  LGPL relinking obligations apply to the distributed app.
- **PyInstaller** is not a dependency (packaging is opt-in), but its bootloader becomes the app's
  launcher. Its license carries an exception for exactly that; read it in the PyInstaller version
  you build with.

## A note on the project license

CC BY-NC-SA 4.0 is a content license, not one designed for software (it grants no patent rights, and
its terms predate common software-distribution concerns). It is used here as a **deliberate choice to
keep Pacer NonCommercial**, carried over from the project's origin. This is a known consideration; if
the NonCommercial restriction is ever relaxed, a software-specific license (e.g. a source-available or
GPL-family license) would be the natural replacement. The third-party components above keep their own
terms regardless.
