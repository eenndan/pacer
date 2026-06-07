# AGENTS.md — `pacer` repository context

Agent-oriented map of this codebase. `pacer` analyzes **race telemetry** (go-kart / motorsport GPS
data): it ingests GPS samples from GoPro videos (GPMF) or u-blox `.dat` logs, segments them into
**laps and sectors**, and visualizes them.

There are **two front-ends** on one shared C++ core:

- **`studio/`** — the current product: a local **PySide6 + pyqtgraph** desktop app (track map,
  speed/Δ charts, lap table, synced video, accelerometer g-meter). Pure Python on top of the C++
  core via its nanobind bindings. **Start here for app work** — see [studio/README.md](studio/README.md)
  and [studio/PLAN.md](studio/PLAN.md).
- **`apps/timeline.cpp`** — the original C++/ImGui GUI (map, lap table, delta plots). Still builds and
  runs; the studio app is where new feature work happens.

The core is C++23; auto-generated Python bindings (`bindings/pacer`) expose the same types to Python
(used by both `studio/` and the notebooks).

> **Submodules:** run `git submodule update --init --recursive` if `3rdparty/` is empty. `implot` is
> pinned to `3da8bd3` (v0.16-26-g3da8bd3, IMPLOT_VERSION 0.17-WIP), compatible with the imgui 1.92.x
> hello_imgui bundles — do **not** "update" it to v0.16 or to current master (master's `ImPlotSpec`
> API breaks our display code).

---

## Directory map

```
pacer/                         # repo root
├── CMakeLists.txt             # root CMake (C++23): adds 3rdparty, pacer, apps, examples, tests, bindings
├── pyproject.toml             # project + pixi manifest (deps, tasks, editable binding packages)
├── pixi.lock                  # pinned deps (osx-arm64 ONLY; lockfile format v7)
│
├── pacer/                     # ── CORE C++ LIBRARY (one folder = one static lib pacer::<name>) ──
│   ├── datatypes/             #   value types (GPSSample, IMUSample, QuatSample) + CRTP operator mixins
│   ├── geometry/              #   2D Point/Segment, CoordinateSystem (GPS<->local meters), Split
│   ├── gps-source/            #   ingestion: GoPro GPMF (GPS5/GPS9 + ACCL/GRAV/CORI IMU) + u-blox .dat
│   ├── interpolation/         #   gradient-descent (Adam) GPMF timestamp recovery (no PyTorch)
│   ├── laps/                  #   lap/sector segmentation + per-lap queries (the data model)
│   └── laps-display/          #   ImGui/ImPlot rendering of laps (used by apps/timeline.cpp)
│
├── studio/                    # ── THE STUDIO APP (PySide6 + pyqtgraph; pure Python on the core) ──
│                              #   see studio/README.md (modules) + studio/PLAN.md (state/handoff)
│
├── apps/timeline.cpp          # original C++/ImGui GUI (HelloImGui + ImPlot)
│
├── bindings/                  # ── PYTHON BINDINGS (litgen-generated, nanobind runtime) ──
│   ├── pacer/                 #   the `pacer` Python package (binds the core; used by studio + notebooks)
│   └── imgui/                 #   the `imgui` Python package (gated OFF — stale vs imgui 1.92.x, unused)
│
├── examples/                  # standalone demos of 3rd-party libs (imgui, implot, gpmf, hello_imgui)
├── notebooks/                 # interpolation.ipynb, dat-files.ipynb (analysis hacking, untidy)
├── tests/                     # Catch2 C++ suites + pure-Python studio tests (see below)
└── 3rdparty/                  # git submodules (imgui, implot, gpmf-parser, hello_imgui, nanobind)
```

---

## Architecture & data flow

Two independent flows share the same core C++ types.

### 1. Telemetry analysis pipeline

```
 GoPro .MP4 (GPMF: GPS + ACCL/GRAV/CORI)     u-blox .dat (UBX-NAV-PVT)
        │                                           │
   GPMFSource / SequentialGPSSource            ReadDatFile()         ← pacer/gps-source
        │                                           │
        └───────────────── pacer::GPSSample ────────┘                ← pacer/datatypes (universal record)
                          │
                  Laps::AddPoint(sample, time)                       ← pacer/laps
                          │
                  Laps::Update()  ──uses──►  CoordinateSystem + Segment::Intersects + Split
                          │                                          ← pacer/geometry (crossing detection)
                segmented laps_ / sectors_  ──►  studio/ (Python)  OR  laps-display (C++/ImGui)
```

Key facts:
- **`GPSSample`** ([datatypes.hpp](pacer/datatypes/datatypes.hpp)) is the universal record: `lat, lon,
  altitude, full_speed, ground_speed`, `int64_t timestamp_ms`, plus GPS9 quality fields `dop`/`fix`
  (sentinels `-1` for the GPS5-era stream). Speeds are **m/s** (UI multiplies by 3.6 for km/h).
- **Lap detection is purely geometric**: `Laps::Update` ([laps.cpp](pacer/laps/laps.cpp)) walks
  consecutive points and calls `Split` ([geometry.hpp](pacer/geometry/geometry.hpp)), which tests
  whether the track segment crosses a "timing line" `Segment` and **interpolates the crossing time**
  along the chord (`t = t0 + f·(t1−t0)`) — so lap times are sub-sample accurate. No time/distance
  heuristics.
- **Two coordinate spaces**: GPS lat/lon (degrees) vs **local meters** via `CoordinateSystem`
  ([geometry.hpp](pacer/geometry/geometry.hpp)). `sectors.start_line`/`sector_lines` are in *local*
  coords; `Update` converts them to global before intersecting. Mixing these up is the main hazard.
- **IMU streams** (`ACCL`/`GRAV`/`CORI`, parsed in
  [gps-source.cpp](pacer/gps-source/gps-source.cpp)) ride the same media clock as GPS; bound as
  `IMUSample`/`QuatSample` with `read_accl`/`read_grav`/`read_cori`. Used only by the studio g-meter.

### 2. C++ → Python binding pipeline

C++ headers → `bindings/<pkg>/generate-bindings.py` runs **litgen** (srcML) → generates
`nanobind_<pkg>.cpp` glue + `<pkg>/__init__.pyi` stubs → `nanobind_add_module` compiles `_<pkg>.so`
→ `<pkg>/__init__.py` does `from ._<pkg> import *`.

- C++ `PascalCase` → Python `snake_case` (litgen); e.g. `Local`→`local`, `Global`→`global_`.
- Generated files (`nanobind_*.cpp`, `*.pyi`) are marked `AUTOGENERATED` — **never hand-edit** between
  the `litgen_pydef`/`litgen_glue_code` markers (the `#include` preamble above them is hand-kept).
  Change the header (and litgen options), then regenerate via `pixi run gen-bindings` and rebuild.

---

## Core modules (each `pacer/<name>/` → static lib `pacer::<name>` via the `add_pacer_library` macro)

- **`datatypes`** (header-only) — `GPSSample`, `PointInTime<P>`, `Vec3f`, `IMUSample`, `QuatSample`,
  and the CRTP operator mixins (`LinearOperators`/`PointwiseOperators`/`VectorOperators` in
  [ops.hpp](pacer/datatypes/ops.hpp)) that give any indexable type `+ - * / == Norm`. Depends on
  nothing; used by everything.
- **`geometry`** — `Point`, `Segment` (`Intersects`), `CoordinateSystem` (`Local`/`Global`/`Distance`,
  crude bi-radius ellipsoid), `Interpolate`, and `Split<P>` (the core of lap detection). Depends on
  `datatypes` + `implot` (for `ImPlotPoint`).
- **`gps-source`** — `RawGPSSource` (abstract), `GPMFSource` (MP4/GPMF: decodes GPS5+GPSU, GPS9, and
  ACCL/GRAV/CORI), `SequentialGPSSource` (chains sources into one cumulative timeline — used for
  chaptered recordings), `ReadDatFile` (u-blox). Depends on `datatypes` + `gpmf::gpmf`.
- **`interpolation`** — `InterpolateTimestamps` (hand-rolled Adam fit of the notebook's parametric
  `t[i] = phase + (cumsum(di)-1)/frequency` model; analytic gradient, torch-parity tested to ~1e-15).
  Recovers per-sample timestamps for GPS5-era data; **superseded by the GPS9 true clock** (the studio
  app uses it only opt-in via `--interp`, auto-rejected on divergence). See
  [studio/docs/upstream-20ms-investigation.md](studio/docs/upstream-20ms-investigation.md).
- **`laps`** — the data model: `Laps` (`AddPoint`, `Update`, `GetLap`, `LapTime`, `Sectors`), `Lap`
  (`points`, `cum_distances`, `Resample`, `TimingLine`). Lap **distance is gap-aware** (`SegmentDistance`
  uses the trapezoidal speed integral across GPS dropouts instead of the corner-cutting chord).
- **`laps-display`** — ImGui/ImPlot rendering (`LapsDisplay`, `DeltaLapsComparison`) for the C++
  `timeline` app only. The studio app does its own rendering in Python.

---

## Build, run & test

> **Platform:** `osx-arm64` only (every pixi manifest + `pixi.lock` pin it). No CI; tests run locally.

```bash
git submodule update --init --recursive   # 3rdparty/ are empty otherwise
pixi install                              # env (cmake, python 3.13, glfw, pyside6, pyqtgraph, pytorch…)
                                          #   + installs editable bindings/pacer + bindings/imgui
```

Pixi tasks (`[tool.pixi.tasks]` in [pyproject.toml](pyproject.toml)):

| task | does |
|---|---|
| `pixi run build` | configure + build everything (cmake + Ninja → `build/Release`) |
| `pixi run test` | CTest: the C++ Catch2 suites **and** the registered Python studio tests |
| `pixi run test-py` | C++ vs PyTorch interpolation parity check |
| `pixi run studio [-- files]` | the studio app (PySide6) — depends on `build` |
| `pixi run timeline` | the C++/ImGui GUI app |
| `pixi run gen-bindings` | regenerate the `pacer` Python bindings |
| `pixi run fmt` / `pixi run lint` | clang-format the C/C++ / `ruff check .` |
| `pixi run web` | WASM/Emscripten build (groundwork only; not built here) |

The C++ build also runs the binding codegen targets and deploys the compiled `.so`.
`CMAKE_EXPORT_COMPILE_COMMANDS` is on; [.clangd](.clangd) expects `build/Release/compile_commands.json`.

**Tests** (wired in [tests/CMakeLists.txt](tests/CMakeLists.txt)):
- C++ Catch2: `test_ops`, `test_geometry`, `test_coordinate_system`, `test_laps`, `test_interpolation`.
- Python studio (pure-Python, fast, registered with CTest): `test_gapfill`, `test_scrub_conversion`,
  `test_studio_features`, `test_chapters`, `test_lap_timing`, `test_validate_wallclock`, `test_gmeter`,
  `test_gmeter_overlay`. Plus `tests/test_interpolation_parity.py` (the `pixi run test-py` parity check).

**Inputs:** the C++ `timeline` app is config/CLI-driven (CLI args / `pacer.json` / `$PACER_CONFIG`;
see `pacer.example.json`). The studio app takes file paths on the CLI (`pixi run studio -- a.MP4`).

---

## Conventions

- **One module = one folder = one static lib.** The `add_pacer_library` macro
  ([pacer/CMakeLists.txt](pacer/CMakeLists.txt)) builds `STATIC pacer_<name>`, symlinks headers so
  includes read `<pacer/<name>/<file>.hpp>`, and adds a `pacer::<name>` alias. Link via the alias.
- **Naming:** kebab-case folders/files; `PascalCase` C++ functions/types; trailing-underscore private
  members. Bindings map `PascalCase`→`snake_case`.
- **CRTP operator mixins** instead of a concrete vector class (any indexable type with size `N`).
- **Callback/pull I/O:** GPS sources expose `Samples(void*, fn)` wrapped by templated lambda adapters;
  ImPlot uses getter-callbacks with `this`/struct pointers cast through `void*`.
- **Designated initializers** (`{.lat=…}`) and immediate-mode GUI (`if (ImGui::Begin(...)) {…}`) used
  throughout the C++.
- **Units:** angles in degrees; speeds in m/s (×3.6 → km/h only at display); `.dat` ints scaled by
  `1e7` (deg) / `1e3` (mm, mm/s).

---

## Key dependencies

| Dependency | Role |
|---|---|
| **pixi** | env + dependency manager (conda-forge, osx-arm64) |
| **CMake ≥3.28 / Ninja** | C++23 build |
| **scikit-build-core** | PEP 517 backend bridging pip/pixi → CMake |
| **litgen** (git) → **nanobind ≥1.3.2** | C++→Python binding codegen / runtime |
| **hello_imgui** | desktop app framework; **provides the `imgui` C++ target** transitively |
| **Dear ImGui / ImPlot / gpmf-parser** | GUI / plotting / GoPro GPMF parsing (submodules) |
| **glfw3 + OpenGL** | windowing / GL context |
| **Catch2** | C++ unit tests |
| **PySide6 + pyqtgraph** | the studio app |
| Python 3.13, numpy, pandas, plotly, pytorch, jupyter | studio / analysis / notebooks |

`ninja` and `catch2` are **explicit** pixi deps (the Ninja generator and `find_package(Catch2)` need
them; an interrupted `pixi add` once pruned them and broke the build mid-session).

---

## Known issues & gotchas

1. **imgui Python bindings are gated OFF** (`PACER_BUILD_IMGUI_BINDINGS` default OFF): `nanobind_imgui.cpp`
   is stale vs imgui 1.92.x and the package is unused. The `pacer` bindings are unaffected.
2. **Don't move the implot pin** (`3da8bd3`) — see the top note.
3. **Python binding gap for GPS sources:** the `pacer` package binds the source classes but exposes
   only `read_samples` / `read_accl` / `read_grav` / `read_cori` (no per-source `read_dat_file`), so
   the **`.dat` reader is not reachable from Python** — the studio app is GPMF/`.MP4` only.
4. **`imgui` C++ target is implicit** (transitive from `hello_imgui`); if that changes upstream, links break.
5. **`assert()`-guarded invariants vanish under `NDEBUG`/Release** — don't rely on them at runtime.
6. The C++ `timeline`'s `DisplayMap` lazily inits the `CoordinateSystem` + a random start line on the
   first frame (detected via inverted `bounds`).

For studio-specific architecture rules an agent must respect (local-meter coordinate space, the
"only `session.py`/`tracks.py` touch `pacer`" rule, perf invariants), see
[studio/PLAN.md](studio/PLAN.md) and [studio/README.md](studio/README.md).

---

## gitnexus (optional code-graph index)

This repo can be indexed by **gitnexus** (CLI + MCP; index in `.gitnexus/`). It is **not always
current** — run `gitnexus status` first and `gitnexus analyze` to refresh after code changes.

```bash
gitnexus status                              # check the index vs HEAD
gitnexus query "lap segmentation and delta"  # find symbols/flows for a concept
gitnexus context "Laps::Update"              # callers/callees of a symbol
gitnexus impact "GPSSample"                  # blast radius
```

The graph engine is **LadybugDB/Kùzu**, not Neo4j — use `labels(n)` and `(n:Label)` matches; `type(r)`
is unsupported.
