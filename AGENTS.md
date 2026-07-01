# AGENTS.md — `pacer` repository context

Agent-oriented map of this codebase. `pacer` analyzes **race telemetry** (go-kart / motorsport GPS
data): it ingests GPS samples from GoPro videos (GPMF), segments them into
**laps and sectors**, and visualizes them.

There is **one front-end** on top of the C++ core:

- **`studio/`** — the product: a local **PySide6 + pyqtgraph** desktop app (track map,
  speed/Δ charts, lap table, synced video, accelerometer g-meter). Pure Python on top of the C++
  core via its nanobind bindings. **Start here for app work** — see [studio/README.md](studio/README.md).

The core is C++23; auto-generated Python bindings (`bindings/pacer`) expose its types to Python
(used by `studio/`). An older C++/ImGui `timeline` GUI, a set of analysis notebooks, and a C++ Adam
timestamp-interpolation path used to live here; all were removed once the studio app + GPS9 timing
superseded them.

> **Submodules:** run `git submodule update --init --recursive` if `3rdparty/` is empty. Only two
> remain: `gpmf-parser` (GoPro GPMF parsing, used by gps-source) and `nanobind` (the bindings runtime).

---

## Directory map

```
pacer/                         # repo root
├── CMakeLists.txt             # root CMake (C++23): adds 3rdparty, pacer, tests, bindings
├── pyproject.toml             # project + pixi manifest (deps, tasks, editable binding package)
├── pixi.lock                  # pinned deps (osx-arm64 ONLY; lockfile format v7)
├── .github/                   # CI workflow (build + test + lint on macos-14/arm64)
│
├── pacer/                     # ── CORE C++ LIBRARY (one folder = one static lib pacer::<name>) ──
│   ├── datatypes/             #   value types (GPSSample, IMUSample, QuatSample) + CRTP operator mixins
│   ├── geometry/              #   2D Point/Segment, CoordinateSystem (GPS<->local meters), Interpolate, Split
│   ├── gps-source/            #   ingestion: GoPro GPMF (GPS5/GPS9 + ACCL/GRAV/CORI IMU)
│   └── laps/                  #   lap/sector segmentation + per-lap queries (the data model)
│
├── studio/                    # ── THE STUDIO APP (PySide6 + pyqtgraph; pure Python on the core) ──
│   │                          #   see studio/README.md (modules)
│   ├── dev/                   #   developer / validation scripts (diagnose, _validate_wallclock, …)
│   │   └── research/          #   frozen GPS-accuracy evidence scripts (historical record)
│   └── docs/                  #   GPS-accuracy / start-line / g-meter investigation write-ups
│
├── bindings/                  # ── PYTHON BINDINGS (litgen-generated, nanobind runtime) ──
│   └── pacer/                 #   the `pacer` Python package (binds the core; used by studio)
│
├── tests/                     # Catch2 C++ suites + pure-Python studio tests (see below)
└── 3rdparty/                  # git submodules (gpmf-parser, nanobind)
```

---

## Architecture & data flow

Two independent flows share the same core C++ types.

### 1. Telemetry analysis pipeline

```
 GoPro .MP4 (GPMF: GPS + ACCL/GRAV/CORI)
        │
   GPMFSource / SequentialGPSSource                                 ← pacer/gps-source
        │
              pacer::GPSSample                                       ← pacer/datatypes (universal record)
                          │
                  Laps::AddPoint(sample, time)                       ← pacer/laps
                          │
                  Laps::Update()  ──uses──►  CoordinateSystem + Segment::Intersects + Split
                          │                                          ← pacer/geometry (crossing detection)
                segmented laps_ / sectors_  ──►  studio/ (Python, via pacer bindings)
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
- Generated files (`nanobind_pacer.cpp`, `*.pyi`) are marked `AUTOGENERATED` — **never hand-edit**
  between the `litgen_pydef`/`litgen_glue_code` markers (the `#include` preamble above them is
  hand-kept — e.g. add/remove an `#include` there when a header enters/leaves the codegen list).
  Change the header (and litgen options), then regenerate via `pixi run gen-bindings` and rebuild.

---

## Core modules (each `pacer/<name>/` → static lib `pacer::<name>` via the `add_pacer_library` macro)

- **`datatypes`** (header-only) — `GPSSample`, `PointInTime<P>`, `Vec3f`, `IMUSample`, `QuatSample`,
  and the CRTP operator mixins (`LinearOperators`/`PointwiseOperators`/`VectorOperators` in
  [ops.hpp](pacer/datatypes/ops.hpp)) that give any indexable type `+ - * / == Norm`. Depends on
  nothing; used by everything.
- **`geometry`** — `Point`, `Segment` (`Intersects`), `CoordinateSystem` (`Local`/`Global`/`Distance`,
  crude bi-radius ellipsoid), `Interpolate` (point/GPSSample lerp), and `Split<P>` (the core of lap
  detection). Depends on `datatypes` only — no plotting/display deps (it was decoupled from implot when
  the C++ GUI was removed).
- **`gps-source`** — `RawGPSSource` (abstract), `GPMFSource` (MP4/GPMF: decodes GPS5+GPSU, GPS9, and
  ACCL/GRAV/CORI), `SequentialGPSSource` (chains sources into one cumulative timeline — used for
  chaptered recordings). Depends on `datatypes` + `gpmf::gpmf`.
- **`laps`** — the data model: `Laps` (`AddPoint`, `Update`, `GetLap`, `LapTime`, `Sectors`), `Lap`
  (`points`, `cum_distances`, `FillDistances`). Lap **distance is gap-aware** (`SegmentDistance`
  uses the trapezoidal speed integral across GPS dropouts instead of the corner-cutting chord). Lap
  *timing* interpolates the start/finish-line crossing instant along the chord (sub-sample accurate).

> A C++ Adam timestamp-fit module (`pacer/interpolation`, `interpolate_timestamps`) used to recover
> per-sample times for GPS5-era data. It **diverged on long/noisy sessions** and was superseded by the
> GPS9 true clock, so it was removed. The investigation is preserved in
> [studio/docs/upstream-20ms-investigation.md](studio/docs/upstream-20ms-investigation.md).

---

## Build, run & test

> **Platform:** `osx-arm64` only (every pixi manifest + `pixi.lock` pin it). CI
> ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs `pixi run build` + `pixi run test`
> (ctest) + `pixi run lint` (ruff) + `pixi run fmt-check` (clang-format) on macos-14
> (Apple-silicon arm64), on every push and PR.

```bash
git submodule update --init --recursive   # 3rdparty/ (gpmf-parser, nanobind) are empty otherwise
pixi install                              # env (cmake, python 3.13, pyside6, pyqtgraph…)
                                          #   + installs the editable bindings/pacer package
```

Pixi tasks (`[tool.pixi.tasks]` in [pyproject.toml](pyproject.toml)):

| task | does |
|---|---|
| `pixi run build` | configure + build everything (cmake + Ninja → `build/Release`) |
| `pixi run test` | CTest: the C++ Catch2 suites **and** the registered Python studio tests |
| `pixi run studio [-- files]` | the studio app (PySide6) — depends on `build` |
| `pixi run gen-bindings` | regenerate the `pacer` Python bindings |
| `pixi run fmt` / `pixi run fmt-check` | clang-format (env-pinned) the hand-maintained C/C++ in place / non-mutating check (CI gate). Both exclude the litgen-generated `nanobind_pacer.cpp` — it must stay byte-identical to the generator's output (regen-drift gate) |
| `pixi run lint` | `ruff check .` |

The C++ build also runs the binding codegen target and deploys the compiled `.so`.
`CMAKE_EXPORT_COMPILE_COMMANDS` is on; [.clangd](.clangd) expects `build/Release/compile_commands.json`.

**Tests** (wired in [tests/CMakeLists.txt](tests/CMakeLists.txt)):
- C++ Catch2 (5): `test_ops`, `test_geometry`, `test_coordinate_system`, `test_laps`,
  `test_gps_source`.
- Python studio (the rest; pure-Python, fast, registered with CTest — offscreen Qt where they
  import the studio widget tree): the studio-feature / controller / analytics suites incl.
  `test_scrub_conversion`, `test_lap_timing`, `test_chapters`, `test_gapfill`,
  `test_gps_source_bindings`, `test_ingest_equivalence`, `test_studio_features`, `test_compare`,
  `test_controllers`, `test_validate_wallclock`, `test_gmeter`, `test_gmeter_overlay`,
  `test_session_services`, `test_corners`, `test_driving`, `test_consistency`, `test_coaching`, ….

**The Session equivalence gate (two halves).** Every Session refactor (F1 god-object
decomposition, E2, the #50 delta-engine dedup) is held to WHOLE-public-API numerical equivalence
via [studio/dev/golden_session_dump.py](studio/dev/golden_session_dump.py) (a dense fingerprint of
a Session's whole public analysis API) + [studio/dev/golden_compare.py](studio/dev/golden_compare.py)
(leaf-by-leaf compare):
- **MANUAL, full-coverage half** — the canonical dump loads the real ~11.8 GB `~/Desktop/D24`
  recording (120k+ leaves, eps 0) and is a dev-Desktop-only gate; it does NOT run in CI.
- **CI half** — `test_golden_synthetic` automates the SAME machinery
  (`fingerprint(strict=False)` + `golden_compare.walk`, eps 1e-9) over the deterministic SYNTHETIC
  session (`test_session_services._synthetic_session`: stadium loop + seeded g-meter, REAL
  corner/driving/delta/bests/consistency, no media file), across base/ref/ref_cleared phases, vs a
  committed baseline (`tests/golden_synthetic_baseline.json`). It runs with no big file, so it
  gates every future Session-math change in CI. Regenerate the baseline only after an intentional,
  reviewed change: `python tests/test_golden_synthetic.py --write-baseline`.

**Inputs:** the studio app takes file paths on the CLI (`pixi run studio -- a.MP4`).

---

## Conventions

- **One module = one folder = one static lib.** The `add_pacer_library` macro
  ([pacer/CMakeLists.txt](pacer/CMakeLists.txt)) builds `STATIC pacer_<name>`, symlinks headers so
  includes read `<pacer/<name>/<file>.hpp>`, and adds a `pacer::<name>` alias. Link via the alias.
- **Naming:** kebab-case folders/files; `PascalCase` C++ functions/types; trailing-underscore private
  members. Bindings map `PascalCase`→`snake_case`.
- **CRTP operator mixins** instead of a concrete vector class (any indexable type with size `N`).
- **Callback/pull I/O:** GPS sources expose `std::function` reader virtuals
  (`ReadSamples`/`ReadAccl`/`ReadGrav`/`ReadCori`) — trampolinable, so Python subclasses can override
  them and feed samples into the engine.
- **Designated initializers** (`{.lat=…}`) used throughout the C++.
- **Units:** angles in degrees; speeds in m/s (×3.6 → km/h only at display).

---

## Key dependencies

| Dependency | Role |
|---|---|
| **pixi** | env + dependency manager (conda-forge, osx-arm64) |
| **CMake ≥3.28 / Ninja** | C++23 build |
| **scikit-build-core** | PEP 517 backend bridging pip/pixi → CMake |
| **litgen** (git) → **nanobind ≥1.3.2** | C++→Python binding codegen / runtime |
| **gpmf-parser** | GoPro GPMF parsing (submodule) |
| **Catch2** | C++ unit tests |
| **PySide6 + pyqtgraph** | the studio app |
| **qtawesome** | icon fonts (Phosphor glyphs) for the studio theme ([studio/theme.py](studio/theme.py)) |
| Python 3.13, numpy | studio runtime |

`ninja` and `catch2` are **explicit** pixi deps (the Ninja generator and `find_package(Catch2)` need
them; an interrupted `pixi add` once pruned them and broke the build mid-session).

---

## Known issues & gotchas

1. **`assert()`-guarded invariants vanish under `NDEBUG`/Release** — don't rely on them at runtime.
2. **Never hand-edit the autogenerated bindings body** (`bindings/pacer/nanobind_pacer.cpp` between the
   litgen markers) — change the header + litgen options and regenerate. The `#include` preamble at the
   very top of that file is the one hand-kept region.

For studio-specific architecture rules an agent must respect (local-meter coordinate space, the
"only `session.py`, `load.py` (the load pipeline), `tracks.py`, and `ingest.py` (the GoPro/GPMF
data-loading layer) touch `pacer`; views stay pacer-free" rule, perf invariants), see
[studio/README.md](studio/README.md).

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
