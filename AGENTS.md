# AGENTS.md — `pacer` repository context

> Agent-oriented map of this codebase. Optimized for fast navigation: skim the
> [Quick orientation](#quick-orientation) and [Directory map](#directory-map),
> jump to a [Subsystem](#subsystems), and use [Where to look for X](#where-to-look-for-x)
> to find the exact file/symbol for a task. File references are clickable and point at
> verified definition lines.

`pacer` is an early-stage, **work-in-progress** tool for analyzing **race telemetry** (go-kart /
motorsport GPS data). It ingests GPS samples from GoPro videos or `.dat` logs, segments them into
**laps and sectors**, and renders an interactive desktop GUI (map, lap table, lap-vs-best delta
plots). The core is C++23; Python bindings (auto-generated) expose the same types for notebook-based
experimentation.

The README used to warn about "questionable things" (hard-coded paths, latent bugs, broken build).
A tech-debt pass has since resolved most of them: the build is green again, inputs are
config/CLI-driven, the documented latent bugs are fixed, there is a Catch2 test suite, and
timestamp interpolation is implemented in C++. See [Recent changes](#recent-changes) and the
trimmed [Known issues & gotchas](#known-issues--gotchas).

---

## Quick orientation

- **Language / standard:** C++23 (core lib + GUI) and Python 3.13 (auto-generated bindings + notebooks).
- **Two best entry points** (per the README):
  - The **`timeline`** GUI app — [apps/timeline.cpp](apps/timeline.cpp) (`main` at
    [timeline.cpp:218](apps/timeline.cpp#L218)). Has delta/laps/sectors; you must edit hard-coded
    input paths in `ReadInput` ([timeline.cpp:60](apps/timeline.cpp#L60)) to point at your files.
  - The **interpolation notebook** — [notebooks/interpolation.ipynb](notebooks/interpolation.ipynb)
    (gradient-descent timestamp interpolation; uses the Python `pacer`/`imgui` bindings).
- **Core library:** [pacer/](pacer/) — six small modules: `datatypes`, `geometry`, `gps-source`,
  `interpolation`, `laps`, `laps-display`.
- **Build chain:** `pixi` (env) → `cmake`/Ninja (C++23) → `scikit-build-core` (Python glue) →
  `litgen` (binding codegen) → `nanobind` (binding runtime). See [Build, run & test](#build-run--test).
- **Size:** ~3.5k lines of hand-written C++/Python; the large line counts in `bindings/` are
  **generated** code (do not hand-edit).
- **This repo is indexed by `gitnexus`** — you can query the code graph directly. See
  [Using gitnexus](#using-gitnexus).

> ⚠️ **Submodules:** run `git submodule update --init --recursive` if `3rdparty/` is empty.
> `implot` is pinned to commit `3da8bd3` (v0.16-26-g3da8bd3, IMPLOT_VERSION 0.17-WIP) compatible with the imgui 1.92.x that
> hello_imgui bundles — do not "update" it to v0.16 or to current master (master's ImPlotSpec API
> breaks our display code). See [gotchas](#known-issues--gotchas).

---

## Directory map

```
pacer/                         # repo root
├── CMakeLists.txt             # root CMake: C++23; adds 3rdparty, pacer, apps, examples, tests, bindings
├── pyproject.toml             # project + pixi manifest (deps, tasks, editable binding packages)
├── pixi.lock                  # pinned deps (osx-arm64 ONLY)
├── README.md                  # author's overview + getting-started (honest about WIP state)
├── .clangd                    # clangd config (uses build/Release compile_commands.json)
│
├── pacer/                     # ── CORE C++ LIBRARY (one folder = one static lib pacer::<name>) ──
│   ├── CMakeLists.txt         #   defines add_pacer_library() macro (see Conventions)
│   ├── datatypes/             #   core value types + CRTP vector-operator mixins (header-only)
│   ├── geometry/              #   2D Point, Segment, CoordinateSystem (GPS<->local meters), Split
│   ├── gps-source/            #   telemetry ingestion: GoPro GPMF (MP4) + u-blox .dat -> GPSSample
│   ├── interpolation/         #   gradient-descent (Adam) GPMF timestamp recovery (no PyTorch)
│   ├── laps/                  #   lap/sector segmentation + per-lap queries (the data model)
│   └── laps-display/          #   ImGui/ImPlot rendering of laps (map, table, delta plots)
│
├── apps/                      # ── EXECUTABLES ──
│   ├── timeline.cpp           #   MAIN GUI app (HelloImGui + ImPlot); the product
│   └── (scratch datparser.c / destructor_test.cpp were removed in the cleanup)
│
├── bindings/                  # ── PYTHON BINDINGS (litgen-generated, nanobind runtime) ──
│   ├── litgen.cmake           #   vendored litgen CMake helpers (find python/nanobind, deploy .so)
│   ├── pacer/                 #   the `pacer` Python package (binds the 5 core modules)
│   └── imgui/                 #   the `imgui` Python package (binds Dear ImGui)
│
├── examples/                  # standalone demos of 3rd-party libs (imgui, implot, gpmf, hello_imgui)
├── notebooks/                 # interpolation.ipynb, dat-files.ipynb (analysis hacking, untidy)
├── tests/                     # Catch2 tests: ops, geometry, coordinate-system, laps, interpolation
│
└── 3rdparty/                  # git submodules (see .gitmodules) — MUST be init'd before building
    ├── imgui/                 #   Dear ImGui            (empty until submodule init)
    ├── implot/                #   ImPlot plotting       (empty until submodule init) -> implot::implot
    ├── gpmf-parser/           #   GoPro GPMF parser     (empty until submodule init) -> gpmf::gpmf
    ├── hello_imgui/           #   app framework         (empty until submodule init) -> provides imgui target
    └── nanobind/              #   C++<->Python binding lib (checked out)
```

---

## Architecture & data flow

Two independent flows share the same core C++ types.

### 1. Telemetry analysis pipeline (the GUI app)

```
 GoPro .MP4 (GPMF track)            u-blox .dat (UBX-NAV-PVT)
        │                                    │
   GPMFSource / SequentialGPSSource     ReadDatFile()          ← pacer/gps-source
        │                                    │
        └──────────── pacer::GPSSample ──────┘                 ← pacer/datatypes (the universal record)
                          │
                  Laps::AddPoint(sample, time)                 ← pacer/laps  (accumulate points_)
                          │
                  Laps::Update()  ──uses──►  CoordinateSystem (GPS↔meters) + Segment::Intersects + Split
                          │                                    ← pacer/geometry (crossing detection)
                segmented laps_ / sectors_ (LapChunk ranges)
                          │
        LapsDisplay (map/table/telemetry) + DeltaLapsComparison (delta plots)   ← pacer/laps-display
                          │
                  ImGui / ImPlot  ──hosted by──►  HelloImGui::Run(frame_lambda)   ← apps/timeline.cpp
```

Key facts:
- **`GPSSample`** ([datatypes.hpp:10](pacer/datatypes/datatypes.hpp#L10)) is the universal record:
  `double lat, lon, altitude, full_speed, ground_speed; int64_t timestamp_ms`. Speeds are **m/s**
  (UI multiplies by 3.6 to show km/h).
- Sources are **pull/callback-based**: `RawGPSSource::Samples(data, callback)` with templated
  `Samples<F>` / `ReadSamples` lambda adapters ([gps-source.hpp:34-49](pacer/gps-source/gps-source.hpp#L34-L49)).
- **Lap detection is purely geometric**: `Laps::Update` ([laps.cpp:6](pacer/laps/laps.cpp#L6))
  walks consecutive points and calls `Split` ([geometry.hpp:110](pacer/geometry/geometry.hpp#L110)),
  which tests whether the track segment crosses a "timing line" `Segment` and interpolates the
  crossing point + time. No time/distance heuristics.
- **Two coordinate spaces**: GPS lat/lon (degrees) vs. **local meters** via `CoordinateSystem`
  ([geometry.hpp:53](pacer/geometry/geometry.hpp#L53)). `sectors.start_line`/`sector_lines` are in
  *local* coords; `Update` converts them to global before intersecting. Mixing these up is the main
  hazard in lap code.

### 2. C++ → Python binding pipeline

```
 C++ headers (pacer/*.hpp, 3rdparty/imgui/imgui.h)         ← single source of truth
        │
  generate-bindings.py  ──runs──►  litgen  (srcML parse)   ← bindings/<pkg>/generate-bindings.py
        │
        ├──► nanobind_<pkg>.cpp   (generated C++ glue: py_init_module_<pkg>)
        └──► <pkg>/__init__.pyi   (generated type stubs)
        │
  nanobind_add_module(_<pkg> module.cpp nanobind_<pkg>.cpp) → compiles _pacer.so / _imgui.so
        │
  <pkg>/__init__.py  ──►  `from ._<pkg> import *`
        │
  Python:  import pacer   /   import imgui                  ← notebooks/, future apps
```

- C++ `PascalCase` symbols become Python `snake_case` (litgen `python_convert_to_snake_case`); e.g.
  `Local`→`local`, `Global`→`global_`, `ToPoint`→`to_point`.
- Generated files (`nanobind_*.cpp`, `*.pyi`) are marked `AUTOGENERATED` — **never hand-edit**;
  change the header or the litgen options and regenerate.

---

## Subsystems

Each core module lives in `pacer/<name>/` and builds a static lib `pacer::<name>` via the
`add_pacer_library` macro ([pacer/CMakeLists.txt:1](pacer/CMakeLists.txt#L1)).

### `pacer/datatypes` — core value types (header-only)
- **Purpose:** the shared vocabulary every other module uses. Value types + a CRTP operator library.
- **Key symbols:**
  - `GPSSample` — [datatypes.hpp:10](pacer/datatypes/datatypes.hpp#L10) — the telemetry record.
  - `PointInTime<P>` — [datatypes.hpp:15](pacer/datatypes/datatypes.hpp#L15) — `{ P point; double time; }`
    with a `Map<F,U>()` functor. The unit stored by `Laps` (`PointInTime<GPSSample>`).
  - `Vec3f` — [datatypes.hpp:31](pacer/datatypes/datatypes.hpp#L31) — 3D vector (local meter coords).
  - `LinearOperators` / `PointwiseOperators` / `VectorOperators` —
    [ops.hpp:20](pacer/datatypes/ops.hpp#L20), [ops.hpp:98](pacer/datatypes/ops.hpp#L98),
    [ops.hpp:120](pacer/datatypes/ops.hpp#L120) — CRTP mixins giving any indexable type `+ - * / == Scalar Norm`.
- **Note:** `datatypes.cpp` is an (almost) empty TU; all logic is header-only.
- **Depends on:** nothing (std only). **Used by:** every other module.
- ✅ `Norm()` is the true Euclidean length; the squared value is `SquaredNorm()`. `operator!=` is
  `!(*this == rhs)` and `operator==` is const-correct. (These were buggy pre-cleanup.)

### `pacer/geometry` — coordinate math, intersection, interpolation
- **Purpose:** 2D geometry + GPS↔local-meter transform + the lap-splitting primitive.
- **Key symbols:**
  - `Point` — [geometry.hpp:16](pacer/geometry/geometry.hpp#L16) — 2D vector; `Rot()`, implicit `ImPlotPoint`.
  - `Segment` — [geometry.hpp:44](pacer/geometry/geometry.hpp#L44) — two `Point`s (a timing line);
    `Intersects(fst,snd,*ratio)` (impl [geometry.cpp:12](pacer/geometry/geometry.cpp#L12)).
  - `CoordinateSystem` — [geometry.hpp:53](pacer/geometry/geometry.hpp#L53) — `Local()`
    ([geometry.cpp:84](pacer/geometry/geometry.cpp#L84)), `Global()`
    ([geometry.cpp:62](pacer/geometry/geometry.cpp#L62)), `Distance()`
    ([geometry.cpp:136](pacer/geometry/geometry.cpp#L136)). Crude bi-radius ellipsoid model.
  - `ToPoint` (overloads) — [geometry.hpp:34](pacer/geometry/geometry.hpp#L34) — project to 2D.
  - `Interpolate` — [geometry.cpp:40](pacer/geometry/geometry.cpp#L40) (Point),
    [geometry.cpp:44](pacer/geometry/geometry.cpp#L44) (GPSSample).
  - `Split<P>` — [geometry.hpp:110](pacer/geometry/geometry.hpp#L110) — **core of lap detection**.
- **Depends on:** `pacer::datatypes`, `implot` (for `ImPlotPoint`). **Used by:** `laps`, `laps-display`, bindings.
- ✅ `Interpolate(GPSSample)` now interpolates `timestamp_ms` (previously dropped) and uses
  declaration-order initializers. `Norm()` is the true Euclidean length; the squared value is
  `SquaredNorm()`. The old hand-written `geometry-bindings.cpp` has been removed (bindings are
  generated in `bindings/pacer/`).

### `pacer/gps-source` — telemetry ingestion
- **Purpose:** decode GoPro GPMF (inside MP4) and u-blox `.dat` records into a uniform `GPSSample` stream.
- **Key symbols:**
  - `RawGPSSource` (abstract) — [gps-source.hpp:19](pacer/gps-source/gps-source.hpp#L19) — the source interface.
  - `GPMFSource` — [gps-source.hpp:74](pacer/gps-source/gps-source.hpp#L74) — MP4/GPMF track reader
    (impl `Samples` at [gps-source.cpp:104](pacer/gps-source/gps-source.cpp#L104)); decodes GPS5+GPSU and GPS9.
  - `SequentialGPSSource` — [gps-source.hpp:115](pacer/gps-source/gps-source.hpp#L115) — chains
    sources into one timeline.
  - `DatVersion` enum — [gps-source.hpp:142](pacer/gps-source/gps-source.hpp#L142) —
    `JUST_DATA` / `WITH_TIMESTAMP`.
  - `ReadDatFile` — [gps-source-dat.cpp:83](pacer/gps-source/gps-source-dat.cpp#L83) — fread loop over
    `uGnssDecUbxNavPvt_t` ([gps-source-dat.cpp:21](pacer/gps-source/gps-source-dat.cpp#L21)).
- **Depends on:** `pacer::datatypes`, `gpmf::gpmf` (bundled GoPro parser). **Used by:** `apps/timeline.cpp`,
  `laps` (consumes the emitted samples), bindings, notebooks.
- ⚠️ The `.dat` path sets `timestamp_ms` only for `WITH_TIMESTAMP`. The old `apps/datparser.c`
  duplicate of the u-blox struct has been removed. The Python `pacer` package binds the source
  classes but still exposes only `read_samples` (the per-source `samples()`/`read_dat_file` were in
  the disabled per-module binding).

### `pacer/interpolation` — gradient-descent timestamp recovery
- **Purpose:** recover an accurate per-sample timestamp from GPMF, which only provides a coarse
  `[start, end]` frame span. A C++ port of the notebook's PyTorch approach (no torch dependency).
- **Key symbols** (all in [interpolation.hpp](pacer/interpolation/interpolation.hpp)):
  - `InterpolationInput` (`floor`/`ceil`/`di`), `InterpolationResult` (`timestamps`,`phase`,`frequency`,`loss`).
  - `InterpolateTimestamps(input, initial_frequency, AdamOptions)` — fits the parametric model
    `t[i] = phase + (cumsum(di)[i]-1)/frequency` with a hand-rolled Adam (analytic gradient; the
    spacing-variance loss term is identically 0 for this model, so only the `[floor,ceil]` constraint
    drives the fit). An overload takes `(samples, spans, cs)` and derives `di` via `ComputeDi`.
  - `InterpolationLoss` exposes the notebook loss for tests/parity.
- **Depends on:** `pacer::geometry`, `pacer::datatypes`. **Used by:** `apps/timeline.cpp` (GPMF path),
  bindings (`interpolate_timestamps`), `notebooks/interpolation.ipynb`.
- ✅ Verified to match the original torch optimizer to ~1e-15 (tests/test_interpolation_parity.py).
  Adam state is reset per learning rate to mirror the notebook (one fresh `torch.optim.Adam` per rate).

### `pacer/laps` — lap/sector segmentation & queries (the data model)
- **Purpose:** accumulate `PointInTime<GPSSample>` and re-segment into laps/sectors when timing lines move.
- **Key symbols:**
  - `Laps` — [laps.hpp:32](pacer/laps/laps.hpp#L32) — the model. Public `Sectors sectors`; queries
    `LapsCount/LapTime/GetLap/...`; ingestion `AddPoint` ([laps.cpp:198](pacer/laps/laps.cpp#L198)).
  - `Laps::Update` — [laps.cpp:6](pacer/laps/laps.cpp#L6) — the segmentation loop (change-detected via
    `dirty_` copies).
  - `Laps::GetLap` — [laps.cpp:164](pacer/laps/laps.cpp#L164) — materialize one `Lap`.
  - `Lap` — [laps.hpp:10](pacer/laps/laps.hpp#L10) — extracted lap (`points`, `cum_distances`,
    `Resample` at [laps.cpp:225](pacer/laps/laps.cpp#L225), `TimingLine` at [laps.cpp:263](pacer/laps/laps.cpp#L263)).
  - `Sectors` — [laps.hpp:27](pacer/laps/laps.hpp#L27) — mutable `start_line` + `sector_lines` (local coords).
  - `Laps::LapChunk` (private) — [laps.hpp:81](pacer/laps/laps.hpp#L81) — half-open index range + interpolated ends.
- **Depends on:** `pacer::geometry`, `pacer::datatypes`. **Used by:** `laps-display`, `apps/timeline.cpp`, bindings.
- ✅ `SampleCount` returns `finish_index - start_index + 2` (matches `GetLap`); `Update`,
  `PickRandomStart` and `MinMax` now guard empty/short traces. The old `laps-bindings.cpp` was removed.

### `pacer/laps-display` — ImGui/ImPlot rendering
- **Purpose:** pure presentation. Draws the map, lap table, single-lap telemetry, and delta plots.
- **Key symbols:**
  - `LapsDisplay` — [laps-display.hpp:11](pacer/laps-display/laps-display.hpp#L11) — owns a `Laps*` and a
    `CoordinateSystem`. `DisplayMap` ([laps-display.cpp:35](pacer/laps-display/laps-display.cpp#L35)),
    `DisplayTable` ([laps-display.cpp:120](pacer/laps-display/laps-display.cpp#L120)),
    `DisplayLapTelemetry` ([laps-display.cpp:92](pacer/laps-display/laps-display.cpp#L92)).
  - `DeltaLapsComparison` — [laps-display.hpp:30](pacer/laps-display/laps-display.hpp#L30) —
    resamples selected laps onto a reference and plots speed + time-delta-vs-best.
    `Display` at [laps-display.cpp:218](pacer/laps-display/laps-display.cpp#L218).
- **Depends on:** `pacer::laps`, `pacer::geometry`, `implot`/`imgui`. **Used by:** `apps/timeline.cpp`, bindings.
- ⚠️ `DisplayMap` lazily initializes the `CoordinateSystem` and a random start line on first frame
  (detected via inverted `bounds`).

### `apps/` — executables
- [apps/timeline.cpp](apps/timeline.cpp) — the **main GUI**. `main` reads an `InputConfig` via
  `ResolveConfig` (CLI args / `pacer.json` / `$PACER_CONFIG`), then `ReadInput` ingests GPMF (with
  C++ timestamp interpolation) or a `.dat` file by extension, then runs `HelloImGui::Run` with a
  per-frame lambda drawing the Data Subset / Map / Laps / Delta / Lap Telemetry windows. (The old
  scratch executables and the unused `DisplayTelemetry`/`glfw_error_callback` were removed.)

### `tests/` — Catch2 unit tests
- Five suites wired via the `add_pacer_test` macro in [tests/CMakeLists.txt](tests/CMakeLists.txt):
  `test_ops`, `test_geometry`, `test_coordinate_system`, `test_laps`, `test_interpolation`; plus a
  Python C++/torch parity test [tests/test_interpolation_parity.py](tests/test_interpolation_parity.py).
- [tests/test_coordinate_system.cpp](tests/test_coordinate_system.cpp) — verifies
  `CoordinateSystem` GPS↔local round-trips within `1e-6`. Linked against `pacer::geometry` +
  `Catch2::Catch2WithMain` ([tests/CMakeLists.txt](tests/CMakeLists.txt)).

### `bindings/` — Python packages (generated)
- [bindings/pacer/](bindings/pacer/) → the **`pacer`** Python package. Generator
  [generate-bindings.py](bindings/pacer/generate-bindings.py) (`my_litgen_options` at
  [generate-bindings.py:11](bindings/pacer/generate-bindings.py#L11), `autogenerate` at
  [generate-bindings.py:112](bindings/pacer/generate-bindings.py#L112)) scans the 5 core headers →
  [nanobind_pacer.cpp](bindings/pacer/nanobind_pacer.cpp) (`py_init_module_pacer` at
  [nanobind_pacer.cpp:78](bindings/pacer/nanobind_pacer.cpp#L78)) + `pacer/__init__.pyi`.
  Exposes `RawGPSSource` as a Python-overridable virtual (NB_TRAMPOLINE).
- [bindings/imgui/](bindings/imgui/) → the **`imgui`** Python package. Generator entry point is
  named `sandbox()` ([generate-bindings.py:554](bindings/imgui/generate-bindings.py#L554)) — it IS
  the real generator; it processes only `3rdparty/imgui/imgui.h`.
- [bindings/litgen.cmake](bindings/litgen.cmake) — helpers: `litgen_setup_module`
  ([litgen.cmake:61](bindings/litgen.cmake#L61)) links the native module and (when not building a
  wheel) copies the `.so` into the editable folder + site-packages.

### `examples/` — 3rd-party demos
Standalone reference apps for `imgui`, `implot`, `gpmf-parser`, `hello_imgui`
([examples/CMakeLists.txt](examples/CMakeLists.txt)). Useful as copy-paste templates; not part of the
analysis pipeline.

---

## Build, run & test

> **Platform:** `pixi.lock` and every pixi manifest pin **`osx-arm64` only**. The build is not
> configured for Linux/Windows.

### 0. Submodules first (required, not in README)
```bash
git submodule update --init --recursive   # 3rdparty/{imgui,implot,gpmf-parser,hello_imgui} are empty otherwise
```

### 1. Environment (pixi)
```bash
pixi install     # creates the env (cmake, python 3.13, glfw, pytorch, jupyter, ...) AND
                 # installs the editable Python packages bindings/imgui + bindings/pacer
pixi shell       # enter the env
```
`[tool.pixi.tasks]` now provides: `configure`, `build`, `test`, `test-py`, `gen-bindings`,
`timeline`, `fmt`, `lint`, `web` (e.g. `pixi run build`, `pixi run test`).

### 2. C++ build (cmake + Ninja)
```bash
cmake -S . -B build/Release -G Ninja     # configure (root CMakeLists.txt, C++23)
cmake --build build/Release              # builds: 3rdparty, pacer libs, apps, examples, tests, bindings
```
This also runs the binding codegen targets and (non-wheel build) deploys the compiled `.so` modules.
`CMAKE_EXPORT_COMPILE_COMMANDS` is on; [.clangd](.clangd) expects `build/Release/compile_commands.json`.

### 3. Run the GUI app
- Built target: **`timeline`** (via `hello_imgui_add_app`, [apps/CMakeLists.txt](apps/CMakeLists.txt)).
- Inputs are config/CLI-driven (no hard-coded paths). Pass `.MP4`/`.dat`/`.json` on the CLI, set
  `$PACER_CONFIG`, or create `pacer.json` (see `pacer.example.json`). `ReadInput`/`InputConfig` live
  near the top of [timeline.cpp](apps/timeline.cpp). With no input it opens an empty window.

### 4. Tests (Catch2 / CTest)
```bash
ctest --test-dir build/Release           # runs test_coordinate_system
# or run the binary directly: build/Release/tests/test_coordinate_system
```

### 5. Regenerate Python bindings (after changing a bound C++ header)
```bash
python bindings/pacer/generate-bindings.py     # rewrites nanobind_pacer.cpp + pacer/__init__.pyi
python bindings/imgui/generate-bindings.py     # rewrites nanobind_imgui.cpp + imgui/__init__.pyi
```
(The CMake build also triggers these via custom targets.) Then rebuild so the `.so` is recompiled.

### 6. Notebooks
```bash
pixi shell && jupyter lab   # open notebooks/interpolation.ipynb or notebooks/dat-files.ipynb
```

---

## Conventions & patterns

- **One module = one folder = one static lib.** Each `pacer/<name>/` has its own `CMakeLists.txt` and
  is built by the `add_pacer_library` macro ([pacer/CMakeLists.txt:1](pacer/CMakeLists.txt#L1)),
  which builds `STATIC pacer_<name>`, symlinks headers into the build tree so includes read as
  `<pacer/<name>/<file>.hpp>`, and adds a `pacer::<name>` alias. Link via the alias.
- **Naming:** kebab-case folders/files (`gps-source`, `laps-display`); `PascalCase` C++
  functions/methods/types; private members trailing-underscore (`points_`, `index_`).
- **CRTP operator mixins** instead of a concrete vector class: any type with `operator[]` + size `N`
  gets full arithmetic by deriving `VectorOperators<Self,double,N>` ([ops.hpp](pacer/datatypes/ops.hpp)).
- **Callback/pull I/O:** GPS sources expose a C-style `Samples(void*, fn)` wrapped by templated
  lambda adapters; ImPlot uses `*G` getter-callbacks with `this`/struct pointers cast through `void*`
  (recurring `reinterpret_cast<...>(data)` idiom in every plot lambda).
- **Designated initializers** (`{.lat=..., .lon=...}`) used heavily for struct construction.
- **Immediate-mode GUI:** every window is `if (ImGui::Begin("Name")) { ... } ImGui::End();` inside the
  single `HelloImGui::Run` frame lambda.
- **Bindings are generated, never hand-written:** edit the C++ header (and litgen options) and
  regenerate; `PascalCase`→`snake_case`. The native module is `_<name>`; the public package re-exports
  it via `from ._<name> import *`.
- **Config-by-source-editing:** input files are hard-coded (README: "tweak source code to read your files").
- **Units:** angles in degrees; speeds in m/s (×3.6 → km/h only at display); `.dat` ints scaled by
  `1e7` (deg) / `1e3` (mm, mm/s).

---

## Key dependencies

| Dependency | Role | Where |
|---|---|---|
| **pixi** | env + dependency manager (conda-forge, osx-arm64) | [pyproject.toml](pyproject.toml) `[tool.pixi.*]` |
| **CMake ≥3.28 / Ninja** | C++23 build | [CMakeLists.txt](CMakeLists.txt) |
| **scikit-build-core** | PEP 517 backend bridging pip/pixi → CMake | [pyproject.toml](pyproject.toml) `[build-system]` |
| **litgen** (git, unpinned) | C++→Python binding codegen (srcML) | [bindings/*/generate-bindings.py](bindings/pacer/generate-bindings.py) |
| **nanobind ≥1.3.2** | C++/Python binding runtime | [3rdparty/nanobind](3rdparty/nanobind) (submodule) |
| **hello_imgui** | desktop app framework; **provides the `imgui` target** | submodule → [3rdparty/CMakeLists.txt](3rdparty/CMakeLists.txt) |
| **Dear ImGui** | immediate-mode GUI | submodule `3rdparty/imgui` (via hello_imgui) |
| **ImPlot** → `implot::implot` | plotting | submodule `3rdparty/implot` |
| **gpmf-parser** → `gpmf::gpmf` | GoPro GPMF/MP4 metadata parsing | submodule `3rdparty/gpmf-parser` |
| **glfw3 + OpenGL** | windowing / GL context | `find_package` in [CMakeLists.txt](CMakeLists.txt) |
| **Catch2** | unit tests | `find_package`; [tests/](tests/) |
| Python 3.13, pandas, plotly, pytorch, jupyter | analysis / notebooks | [pyproject.toml](pyproject.toml) `[tool.pixi.dependencies]` |

---

## Where to look for X

| I want to… | Go to |
|---|---|
| Change which telemetry files the GUI loads | no longer hard-coded: pass on the CLI / `pacer.json` / `$PACER_CONFIG`; see `InputConfig`/`ResolveConfig`/`ReadInput` in [timeline.cpp](apps/timeline.cpp) |
| Change/port the timestamp interpolation | [pacer/interpolation/interpolation.cpp](pacer/interpolation/interpolation.cpp) (`InterpolateTimestamps`, `ComputeDi`); parity test [tests/test_interpolation_parity.py](tests/test_interpolation_parity.py) |
| Load a `.dat` file instead of GoPro MP4 | pass a `.dat` path on the CLI / in `pacer.json` (dispatched by extension in `ReadInput`); impl [gps-source-dat.cpp:83](pacer/gps-source/gps-source-dat.cpp#L83) |
| Add/modify a telemetry field | `GPSSample` [datatypes.hpp:10](pacer/datatypes/datatypes.hpp#L10); then `Interpolate` [geometry.cpp:44](pacer/geometry/geometry.cpp#L44), the `ostream<<` [datatypes.hpp:24](pacer/datatypes/datatypes.hpp#L24), and regenerate bindings |
| Parse a new telemetry format | add a `RawGPSSource` subclass in [pacer/gps-source/](pacer/gps-source/) (interface [gps-source.hpp:19](pacer/gps-source/gps-source.hpp#L19)) |
| Change MP4/GPMF timestamp decoding | [gps-source.cpp:104](pacer/gps-source/gps-source.cpp#L104) (`GPMFSource::Samples`); GPS9 epoch + GPSU ASCII parsing inside |
| Change `.dat` parsing/units/`WITH_TIMESTAMP` | `ReadDatFile` [gps-source-dat.cpp:83](pacer/gps-source/gps-source-dat.cpp#L83) (struct ~[:21](pacer/gps-source/gps-source-dat.cpp#L21)) |
| Chain multiple recordings into one timeline | `SequentialGPSSource` [gps-source.hpp:115](pacer/gps-source/gps-source.hpp#L115) |
| Change how laps are split (crossing/hysteresis/min-time) | `Laps::Update` [laps.cpp:6](pacer/laps/laps.cpp#L6) + `Split` [geometry.hpp:110](pacer/geometry/geometry.hpp#L110) + `Segment::Intersects` [geometry.cpp:12](pacer/geometry/geometry.cpp#L12) |
| Change sector definition / cycling | sector logic in `Laps::Update` [laps.cpp:19-68](pacer/laps/laps.cpp#L19-L68); `Sectors` [laps.hpp:27](pacer/laps/laps.hpp#L27) |
| Fix lap time / distance / entry-speed math | [laps.cpp:100-190](pacer/laps/laps.cpp#L100-L190) (`Time`, `GetLapDistance`, `Speed`, `LapTime`, `LapEntrySpeed`) |
| Change lap-to-lap alignment for delta | `Lap::Resample` [laps.cpp:225](pacer/laps/laps.cpp#L225), `Lap::TimingLine` [laps.cpp:263](pacer/laps/laps.cpp#L263) |
| Add a vector/point operator or change arithmetic | [ops.hpp](pacer/datatypes/ops.hpp) (`LinearOperators`/`PointwiseOperators`/`VectorOperators`) |
| Change GPS↔meter math / earth radii | `CoordinateSystem` impl [geometry.cpp:62-136](pacer/geometry/geometry.cpp#L62-L136); radii [geometry.hpp:90-91](pacer/geometry/geometry.hpp#L90-L91) |
| Edit the map / draggable timing lines | `LapsDisplay::DisplayMap` [laps-display.cpp:35](pacer/laps-display/laps-display.cpp#L35), `DragTimingLine` [laps-display.cpp:19](pacer/laps-display/laps-display.cpp#L19) |
| Edit the lap table (columns/selection/drag-drop) | `LapsDisplay::DisplayTable` [laps-display.cpp:120](pacer/laps-display/laps-display.cpp#L120) |
| Edit the delta / multi-lap comparison plots | `DeltaLapsComparison::Display` [laps-display.cpp:218](pacer/laps-display/laps-display.cpp#L218) |
| Add/modify a GUI window | the `HelloImGui::Run` lambda [timeline.cpp:240-313](apps/timeline.cpp#L240-L313) |
| Expose a C++ type/function to Python | edit the header, then `python bindings/pacer/generate-bindings.py`; tune `my_litgen_options` [generate-bindings.py:11](bindings/pacer/generate-bindings.py#L11). **Do NOT** edit `pacer/*/*-bindings.cpp` (dead) |
| Add a Python-overridable virtual class | `options.class_override_virtual_methods_in_python__regex` in [bindings/pacer/generate-bindings.py](bindings/pacer/generate-bindings.py) |
| Fix a header litgen/srcML can't parse | `_preprocess_imgui_code` [generate-bindings.py:27](bindings/imgui/generate-bindings.py#L27) / `srcmlcpp_options` |
| Change where `.so` is deployed | `litgen_setup_module` [litgen.cmake:61](bindings/litgen.cmake#L61) + `LITGEN_PATH_*` cache vars in the bindings CMakeLists |
| Add a unit test | follow [tests/CMakeLists.txt](tests/CMakeLists.txt) (`add_executable`+`target_link_libraries`+`add_test`) |
| Change C++ standard / top-level deps | [CMakeLists.txt](CMakeLists.txt), [pyproject.toml](pyproject.toml) |
| See the full app wiring (frame loop) | `main` [timeline.cpp:218](apps/timeline.cpp#L218) |

---

## Recent changes

A tech-debt pass resolved most of the old gotchas:
- **Build fixed.** `implot` was incompatible with the imgui 1.92.x hello_imgui bundles; it is pinned
  to `3da8bd3` (v0.16-26-g3da8bd3, 0.17-WIP). `enable_testing()` was added so CTest registers tests.
- **Latent bugs fixed** (with tests): `operator!=` / `operator==` const-correctness;
  `Norm()` vs `SquaredNorm()`; `Interpolate(GPSSample)` keeps `timestamp_ms`; `Laps::SampleCount`
  `+3`→`+2` and bounds; empty/short-trace guards in `PickRandomStart`/`MinMax`/`main`; `Laps::Update`
  no longer emits a phantom leading lap from an uninitialized `previous`.
- **`DeltaLapsComparision` → `DeltaLapsComparison`** (full rename incl. bindings).
- **Dead code removed**: the three `*-bindings.cpp`, `apps/datparser.c`, `apps/destructor_test.cpp`,
  and `DisplayTelemetry`/`glfw_error_callback` in timeline.
- **Inputs are config/CLI-driven** (no hard-coded paths); notebooks use `$PACER_DATA`.
- **C++ timestamp interpolation** added (`pacer/interpolation`), bound to Python, used by the app.
- **Tooling**: `.clang-format`, `.clang-tidy`, `ruff.toml`, and populated `[tool.pixi.tasks]`.
- **Web**: an `if(EMSCRIPTEN)` build path + `tools/build-web.sh` (groundwork; not compiled here).

## Known issues & gotchas

1. **imgui Python bindings are gated OFF.** `nanobind_imgui.cpp` is stale vs imgui 1.92.x (and the
   package is unused). The CMake option `PACER_BUILD_IMGUI_BINDINGS` (default OFF) skips it; turning it
   on requires regenerating against imgui 1.92.x. The `pacer` bindings are unaffected.
2. **Don't move the implot pin.** v0.16 predates imgui 1.92's `ImTextureRef`; current master's
   `ImPlotSpec` API breaks our display code. Keep `3da8bd3` (v0.16-26-g3da8bd3).
3. **Python binding gap for GPS sources.** The `pacer` package binds the source classes but exposes
   only `read_samples` (no per-source `samples()`/`read_dat_file`).
4. **`imgui` C++ target is implicit** — it comes transitively from `hello_imgui`
   ([3rdparty/CMakeLists.txt](3rdparty/CMakeLists.txt)); if that changes upstream, links break.
5. **Generated files must not be hand-edited** between the `litgen_pydef`/`litgen_glue_code` markers in
   `nanobind_*.cpp` (and the whole `*.pyi`). The include preamble *above* the markers is hand-maintained
   (that's where a newly-bound header's `#include` goes). Regenerate via `pixi run gen-bindings`.
6. **`assert()`-guarded invariants vanish under `NDEBUG`/Release.** Don't rely on them at runtime.
7. **Platform & CI.** `osx-arm64` only; no CI configured. Tests run locally via `pixi run test`.
   `pixi.lock` is format **v7** (needs a recent pixi). `ninja` and `catch2` are now explicit deps —
   the build's Ninja generator and `find_package(Catch2)` require them, but they used to be present
   only implicitly (and an interrupted `pixi add` once pruned them, breaking the build mid-session).

---

## Using gitnexus

This repo is indexed by **gitnexus** (CLI + MCP server; index lives in `.gitnexus/`, currently
up-to-date with `HEAD`). Future agents can navigate the code graph instead of grepping blindly:

```bash
gitnexus status                              # confirm the index matches HEAD
gitnexus query "lap segmentation and delta"  # find symbols/flows for a concept
gitnexus context "Laps::Update"              # callers/callees of a symbol
gitnexus impact "GPSSample"                  # blast radius: what changes if you touch it
gitnexus cypher "MATCH (n:Struct) RETURN n.name, n.filePath, n.startLine"   # raw graph (Kùzu/LadybugDB dialect)
```

Notes: the graph engine is **LadybugDB/Kùzu**, not Neo4j — use `labels(n)` and `(n:Label)` matches;
`type(r)` is unsupported. After significant code changes, refresh with `gitnexus analyze` (writes to
`.gitnexus/`). Node labels present: `Function`, `Method`, `Struct`, `Class`, `Enum`, `Namespace`,
`Macro`, `File`, `Folder`, plus detected `Community` (module clusters) and `Process` (execution flows).

---

*Generated as agent context for the `pacer` repo. When code changes, update the affected subsystem
section, the [Where to look for X](#where-to-look-for-x) table, and re-run `gitnexus analyze`.*
