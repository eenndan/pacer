# pacer

This is my project for analysing telemetry data from races.
It's still work-in-progress, but the worst of the early-days jank has been cleaned
up: input paths are no longer baked into the binary, there's a small test suite, and
the build is green again.

The product is **one front-end** on top of a small C++ core:

- **`studio/`** — a local **PySide6 + pyqtgraph** desktop app (track map, speed/Δ-to-best charts,
  lap table, synced GoPro video, accelerometer g-meter overlay), written in pure Python on top of
  the core via its Python bindings. See [studio/README.md](studio/README.md).

The C++ core (`pacer/`) does the heavy lifting — GPMF ingest, geometry, lap/sector segmentation,
GPS9 true-clock lap timing — and is exposed to Python via nanobind. (An older C++/ImGui GUI and a
set of Jupyter notebooks used to live here; both were removed once the studio app superseded them.)

## getting started

### env

I use [pixi](https://pixi.sh) for external dependency management.
It is much better than `conda`/`mamba`/`micromamba` while leveraging the same infrastructure.
I use `cmake` for building binary stuff, `litgen` for code-generation of bindings and `scikit-build` to glue two together via `pyproject.toml`.
Is it the best setup? No. Does it work? Somewhat.

```bash
git submodule update --init --recursive  # 3rdparty/ deps (gpmf-parser, nanobind)
pixi install                              # env + editable python bindings
```

After that, there are pixi tasks for the common stuff (no need to memorize commands):

```bash
pixi run build      # configure + build everything (cmake + Ninja)
pixi run test       # run the C++ (Catch2) + Python studio tests via ctest
pixi run studio     # build + launch the studio app (PySide6); pass files: pixi run studio -- a.MP4
pixi run fmt        # clang-format the C/C++ sources
pixi run lint       # ruff check
```

### what to do?

Good places to start:

- the **studio app** — `pixi run studio -- /path/to/GX010060.MP4` (the GoPro chapter siblings can be
  chained with `--full`). Map + speed/Δ charts + lap table + synced video + g-meter. The full
  orientation doc is [studio/README.md](studio/README.md).

## components

Some components:

- `pacer/`: bread and butter --- the C++ core library (datatypes, geometry, gps-source, laps);
- `studio/`: the PySide6 + pyqtgraph desktop app (the product) on top of the core;
- `studio/dev/`: developer / validation / research scripts (not part of the shipping app);
- `bindings/`: Python bindings semi-automatically generated from the C++ source.

## future ideas

Still in progress:

- more tracks (the studio app currently hard-codes one) + real track auto-detection;
- persisting sector / start-line edits per file;
- a more on-line approach, e.g. to use live in session for comments like:
  - too much wheelspin on exit;
  - too little braking on entry;
  - shorter line is better;
  - keep minimum speed higher;
  - etc.
- keep chipping away at the code (it's much better, but still WIP).

See [studio/PLAN.md](studio/PLAN.md) for the studio app's full state, shipped features, the
empirically-rejected experiments, and the near-term backlog.

Wow, something already done:

- the **studio app** — map + speed/Δ charts + lap table + **synced GoPro video** + accelerometer
  g-meter overlay, with GPS9 true-clock lap timing (validated unbiased vs a real transponder);
- lap segmentation, comparison between laps with delta;
- nanobind-based Python bindings to rapidly experiment in Python;
- integration with 3rd-party GPS data (GoPro GPMF GPS5/GPS9);
- config/CLI-driven inputs (no more hard-coded paths), formatting/lint config,
  pixi tasks, and a test suite (C++ Catch2 + pure-Python studio tests).

## credits

It's ain't much, but it's honest work.

pacer © 2025 by Denys Smirnov is licensed under CC BY-NC-SA 4.0.
To view a copy of this license, visit <https://creativecommons.org/licenses/by-nc-sa/4.0/>
