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
├── .github/                   # CI: `build + test + lint` on every push, plus
│                           #   `package-smoke` (.app build-only, on a tag / manual run)
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
│   └── docs/                  #   GPS-accuracy / start-line / g-meter write-ups, and
│                           #   refused-2026-09.md: features MEASURED AND REFUSED (read
│                           #   before re-proposing one)
│
├── bindings/                  # ── PYTHON BINDINGS (litgen-generated, nanobind runtime) ──
│   └── pacer/                 #   the `pacer` Python package (binds the core; used by studio)
│
├── tests/                     # Catch2 C++ suites + pure-Python studio tests (see below)
├── packaging/                 # the .app/.dmg build (pacer.spec, build_macos.sh) — see PACKAGING.md
├── docs/                      # the public pages: ACCURACY.md, FIRST_LAP.md, index.html + media/
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
- **IMU streams** (`ACCL`/`GYRO`/`GRAV`/`CORI`, parsed in
  [gps-source.cpp](pacer/gps-source/gps-source.cpp)) ride the same media clock as GPS; bound as
  `IMUSample`/`QuatSample` with `read_accl`/`read_gyro`/`read_grav`/`read_cori` (and the bulk
  `read_*_columns`). ACCL/GRAV/CORI feed the studio g-meter; `GYRO` (rad/s) feeds `studio/rotation.py`,
  the measured yaw-rate channel. GYRO declares the SAME axis orientation as ACCL on every camera
  measured, but NOT the same sample rate — it runs 2–17x faster on a HERO5/Karma/Max/Fusion, so join
  the two on time, never by row. `DeviceName()` exposes the camera's `DVNM` (e.g. "HERO13 Black"),
  which is what says whether a recording *can* have GPS at all (a HERO12 has no receiver).

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
- **`gps-source`** — `RawGPSSource` (abstract), `GPMFSource` (MP4/GPMF: decodes GPS5+GPSU, GPS9,
  ACCL/GYRO/GRAV/CORI and the `DVNM` device name), `SequentialGPSSource` (chains sources into one
  cumulative timeline — used for chaptered recordings). Depends on `datatypes` + `gpmf::gpmf`.
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
| `pixi run test` | CTest: the C++ Catch2 suites **and** the registered Python studio tests (the pre-PR gate), **four at a time** (`CTEST_PARALLEL_LEVEL=4` in the task's env, like `test-fast`). Append `-j1` for a serial run when you need to rule an interaction out, or `-jN` for another level — never put a `-j` in the task itself: ctest 4 rejects a second one. On the dev Mac that includes the 14 `footage.*` checks, run for real on the Desktop working set (each under its own 3600 s TIMEOUT, not `--timeout 480`, and one at a time under a shared `RESOURCE_LOCK`) — **~321 s** for all 138 with the footage running (one run, 320.9 s, 2026-09-23, under load ~20 from another session; ~13 min serial, summed from its parts, which is why it used to be run in pieces); in CI they are reported Skipped |
| `pixi run test-fast` | the fast inner loop: `test` minus the two slowest suites (`test_export_video`, `test_compare_lifecycle`) — **136 tests, ~143 s** four at a time (one run, 142.9 s on a quiet machine, 2026-09-23 — 420 s serial on the same commit; the full suite registers 138 — the two numbers differ by exactly the two suites this task excludes). Why four and not every core, with the one- and two-lane measurements: `pyproject.toml`. Its 14 `footage.*` checks are reported **Skipped by name**, not run: it sets `PACER_FOOTAGE_DEFAULTS=off`, because running them would add ~140-150 s (+40 % of the serial loop, measured before the loop went parallel). A footage variable you set still runs its checks here. Both ctest tasks run under `caffeinate -si`: a Mac that idle-sleeps mid-suite freezes the in-flight test, and ctest then reports it as a `Timeout` lasting as long as the sleep, far past `--timeout`, on a different test every run. If you see that signature, check `pmset -g log` before hunting a hang — rationale in `pyproject.toml` |
| `pixi run test-footage` | **only** the 14 real-footage checks (`ctest -L footage`, under `caffeinate -si`), on their Desktop working-set defaults or the recordings your variables name — **~139 s** (one run, 139.1 s, 2026-09-23, warm page cache; the slowest is `footage.test_real_render_quality_levels_if_media` at 67 s). Without the footage each is reported Skipped by name |
| `pixi run golden` | run **only** the synthetic core-math equivalence gate (`test_golden_synthetic`) — sub-second |
| `pixi run smoke` | the CI E2E gate: full `StudioWindow` offscreen on the bundled sample (`_smoke --no-video`) |
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
- **MANUAL, full-coverage half** — the dump loads one real recording, the chapter
  `PACER_GOLDEN_MP4` names, by default `MK_18_09_26/GX010067.MP4` (a working-set recording, below),
  and compares at eps 0: 156,659 leaves on that default in ~4 s, 147,184 on
  `Sandown 3h 2026/GX010064.MP4` and 153,014 on `SD_19_09_26/GX010068.MP4` (measured 2026-09-23,
  once Sandown Park became a built-in track; a Sandown dump taken before that is on the auto-fitted
  line and will not compare with one taken after, while the MK default did not move a leaf).
  It is a dev-Desktop-only gate; it does NOT run in CI.
- **CI half** — `test_golden_synthetic` automates the SAME machinery
  (`fingerprint(strict=False)` + `golden_compare.walk`, eps 1e-9) over the deterministic SYNTHETIC
  session (`test_session_services._synthetic_session`: stadium loop + seeded g-meter, REAL
  corner/driving/delta/bests/consistency, no media file), across base/ref/ref_cleared phases, plus a
  `drift_noise` phase over `tests/_synthetic.drift_noise_session` (GPS speed noise at the measured
  sigma, one lap drifted 1 % with an unmatched corner boundary — the stadium laps have neither, so
  an alignment-driven or noise-driven change could not move them) and a `drift_median` phase over
  `tests/_synthetic.drift_median_session` (the same three geometries with the drift on the
  MEDIAN-time lap — coaching reads the median lap, so with the drift on the slowest one every
  coaching corner-window projection was the identity and #289's coaching-alignment fix moved 15
  real leaves and 0 synthetic ones), and a `drift_band` phase over
  `tests/_synthetic.drift_band_session` (a LADDER of three laps at 0.118 / 0.289 / 0.460 %
  line-length drift — the band the removed 0.5 % drift gate governed, which no lap of any other
  fixture sits in, so #300's removal of that gate moved 0 of all 24,859 leaves; restoring it moves
  68 of this phase's 15,451), vs a
  committed baseline (`tests/golden_synthetic_baseline.json`). It runs with no big file, so it
  gates every future Session-math change in CI. Regenerate the baseline only after an intentional,
  reviewed change: `python tests/test_golden_synthetic.py --write-baseline`.

Run the **manual real-footage gate** around a core-math change, on the **working set**: the
recordings on the dev Desktop the owner chose on 2026-09-23 (T16b) — `Sandown 3h 2026` (`GX0*0064`,
the primary), `SD_19_09_26` (`GX0*0068`), `SD_30_08_26` (`GX0*0065`) and `MK_18_09_26`
(`GX0*0067`, the only anticlockwise one, on the same Daytona Milton Keynes circuit as D24). Each is
~12 GB a chapter, **not committed**, and strictly read-only; CI never sees them and runs the
synthetic gate above instead. The dump defaults to chapter 1 of `MK_18_09_26` (G2): the
recording on D24's own circuit, so it fingerprints the 12 corners the D24 gate did, not Sandown's 7.
To use another, point `PACER_GOLDEN_MP4` at ONE chapter, and use the same one for both dumps of a
comparison. D24 left the dev machine on 2026-09-19 (the owner keeps it on an external drive — never
search for it or mount it). The dump's positional argument is its OUTPUT, and it refuses anything
but a new `.json`; the recording comes only from the variable or its default:

```bash
# export PACER_GOLDEN_MP4="$HOME/Desktop/Sandown 3h 2026/GX010064.MP4"  # optional: the INPUT, read-only
pixi run python -m studio.dev.golden_session_dump /tmp/before.json   # BEFORE the change
# … make the change, then: pixi run build …
pixi run python -m studio.dev.golden_session_dump /tmp/after.json    # AFTER
pixi run python -m studio.dev.golden_compare /tmp/before.json /tmp/after.json   # expect max|Δ| = 0
```

No `PYTHONPATH` is needed above — the dump puts the built `bindings/pacer` on `sys.path` itself.
It used to be needed and was not documented, so the command as written **exited 2 on intact
footage**: from the repo root `import pacer` resolved to the C++ `pacer/` source directory (a
namespace package with no `GPMFSource`), and the tool reported that AttributeError as
"not readable as GPMF … a partial copy, or a file some tool overwrote". A refusal now names only
what it measured — missing, unreadable, not an MP4 container, not parseable as GPMF, or bindings
that are not importable in this run — and never guesses at a cause
(`studio.dev.golden_session_dump.preflight`, held by `tests/test_golden_hermetic.py`).

**Real-footage checks.** Fourteen checks re-measure something on a real recording, and each is its own
CTest registration, `footage.<check>`. Without its recording CTest lists it under *"The following
tests did not run: … (Skipped)"* — a skip, never a pass, and never a failure (CI has no footage at
all). **When you report gates, name every `footage.*` that skipped.** Until 2026-09-19 each of them
printed a skip line inside its file and the file reported `Passed`, so when `~/Desktop/D24` went
every real-footage check in the repo became a green no-op. `tests/_footage.py` has the mechanism;
`tests/test_footage_checks.py` holds it, including the negative control.

| Variable | Points at | Checks | Default |
|---|---|---|---|
| Variable | Points at | Checks | Default (G2), and why |
|---|---|---|---|
| `PACER_GOLDEN_MP4` | THE recording (any real one) | the golden dump; `footage.test_real_render_smoke_if_ffmpeg_and_media`, `…_real_chaptered_non_first_chapter_render_if_media`, `…_real_render_quality_levels_if_media`; `footage.test_pedal_mode_paints_the_chart_band`, `footage.test_pedal_band_holds_each_braking_zone_whole`; the primary of `footage.test_real_media_pane_b_is_reference_at_lap_start` | `~/Desktop/MK_18_09_26/GX010067.MP4`: chaptered (the chaptered render needs a lap inside chapter 2), D24's own circuit (the dump covers what it covered on D24), and the smallest chaptered recording. The compare proof's primary defaults to `~/Desktop/SD_19_09_26/GX010068.MP4` instead: MK has no second recording of its track |
| `PACER_GOLDEN_REF_MP4` | a second, DIFFERENT recording of the SAME track (a reference from another track is refused before pane B opens) | the reference of `footage.test_real_media_pane_b_is_reference_at_lap_start` | `~/Desktop/Sandown 3h 2026/GX010064.MP4`: SD_19_09 and Sandown 3h stand where D24 0060 and 0062 stood, and the three-chapter reference makes pane B resolve a later chapter |
| `PACER_IDEAL_TABLE_MP4` | comma-separated chapters of ONE recording, re-measured alone (how a new row is written) | `footage.test_the_table_still_matches_the_app` | every row of the ideal-lap table, on exactly the chapter files `tests/test_ideal_sample_table.ROW_RECORDINGS` names, under `~/Desktop` — the whole table in one run (~10 s) |
| `PACER_MEASURED_FIGURES_DIR` | a folder holding the working set's folders | the seven `footage.test_the_*_footage` in `test_measured_figures` | `~/Desktop`, where `_LAP_SETS` says each table's recordings are: each check loads exactly the lap sets its table names |

The recordings and the reasons live in `studio/dev/footage.py`, shared by the dump and the tests;
`tests/_footage.py` does the lookup. The table checks keep their own variables because their rows
are named recordings (and chapter selections sibling discovery cannot express), not "a recording".
A default that is absent is a skip — for a table, if ANY of its recordings is absent — and **a
variable you set that names something absent is a failure.** Until G2 (2026-09-23) every default
still named D24, deliberately, so all fourteen skipped until T16b had re-measured the published
tables on the working set; now all fourteen run and pass on this Mac.

Where they run: `pixi run test-footage` runs just these (by their `footage` CTest label);
`pixi run test` runs them with everything else; `pixi run test-fast` sets
`PACER_FOOTAGE_DEFAULTS=off` and reports each **Skipped by name**, with a reason naming
`test-footage`, rather than excluding them — an excluded test vanishes from the report. The
recordings are the owner's and strictly read-only: record sizes and mtimes around a run.

```bash
pixi run test-footage                                           # all fourteen, on the working set
PACER_GOLDEN_MP4="$HOME/Desktop/SD_30_08_26/GX010065.MP4" \
  pixi run ctest --test-dir build/Release -R '^footage\.test_real_render' --output-on-failure
```

A new real-footage check goes in its file's `FOOTAGE_CHECKS`, finds its recording through
`tests/_footage.py` (which raises `FootageMissing` rather than printing a skip), and gets
`add_footage_test(<file> <check>)` beside the file's registration. `test_footage_checks` fails the
build when a declaration and a registration disagree or a declared check passes without its
recording, and a check left in its file's ordinary run fails that run with `FootageMissing`.

**Run one test:** `pixi run ctest --test-dir build/Release -R test_<name>` — CTest injects the
`PYTHONPATH=bindings/pacer` + `QT_QPA_PLATFORM=offscreen` env each suite needs (a bare
`pixi run python tests/test_<name>.py` can miss it on a fresh checkout / for the offscreen-Qt
suites). For the whole suite minus its two slowest members, use `pixi run test-fast` — ~143 s four at a time (420 s serial, 2026-09-23) — and `pixi run test` is still the pre-PR gate.

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
  (`ReadSamples`/`ReadAccl`/`ReadGyro`/`ReadGrav`/`ReadCori`) — trampolinable, so Python subclasses can override
  them and feed samples into the engine.
- **Designated initializers** (`{.lat=…}`) used throughout the C++.
- **Units:** angles in degrees; speeds in m/s (×3.6 → km/h only at display).

---

## PR workflow (the CI sequence)

CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs these **in order**, each gating the
next — run them locally first to avoid a red build:

1. **`pixi run build`** — compiles the core and regenerates the bindings.
2. **Bindings drift gate** — any edit to a `pacer/**/*.hpp` header (even a comment) regenerates
   `bindings/pacer/nanobind_pacer.cpp` + `bindings/pacer/pacer/__init__.pyi`; **commit BOTH** or CI's
   `git diff --exit-code -- bindings/` fails.
3. **`pixi run test`** — the full ctest suite (Catch2 C++ + Python studio) must be green.
4. **E2E smoke** — `pixi run python -m studio.dev._smoke --no-video` builds the real app headless.
5. **`pixi run lint`** — `ruff check .` clean.
6. **`pixi run fmt-check`** — `clang-format` non-mutating check (`pixi run fmt` auto-fixes).

Change conventions: **one focused change per PR**; match the surrounding comment density / naming /
idiom (favour "why" comments over restating the code); **add or update a test** for any behaviour
change (pure logic → a Qt-free module with a synthetic-data test in
[tests/_synthetic.py](tests/_synthetic.py); real-widget paths → an offscreen-Qt test); and
**core-math changes (timing / geometry / delta) must preserve the validated numbers** — pin them with
the golden gate above.

**Before proposing a feature, check it was not already measured and refused.**
[studio/docs/refused-2026-09.md](studio/docs/refused-2026-09.md) records the ones whose evidence
would otherwise live only in a closed pull request, each with the numbers that killed it — from the
mistake-hangover detector and the ideal-lap recombination dotplot onwards. Write a new one as the
next free section on your base: `tests/test_measured_figures.py` derives the intro's count, and
fails on a doubled or skipped number and on a citation of a section that moved. They stay
refused unless you bring NEW evidence; "it would be nice to have" is not new evidence.

**Changelog:** a user-visible change (feature, fix, behaviour tweak) gets a line under
`[Unreleased]` in [CHANGELOG.md](CHANGELOG.md) in the same PR — grouped Added/Changed/Fixed, one
scannable line, no per-commit noise. Internal refactors/tests/docs don't.

**Release recipe:** the version lives in **THREE** places that must move together —

| file | role |
|---|---|
| [studio/\_\_init\_\_.py](studio/__init__.py) `__version__` | **canonical** — regex-read by `packaging/pacer.spec`, shown in the About card |
| [pyproject.toml](pyproject.toml) `version` | pip metadata — and `packaging/build_macos.sh` names the `.dmg` from it |
| [bindings/pacer/pyproject.toml](bindings/pacer/pyproject.toml) `version` | the bindings package's own metadata |

Bump ALL THREE, retitle `[Unreleased]` → `[x.y.z] — date` in the changelog and add its compare link
at the foot of the file, then tag. [tests/test_version.py](tests/test_version.py) fails the build on
any of those four steps missed — it reads each site the way *its own consumer* reads it. The recipe
had been documentation-only since it was written, and it still named the wrong number of files.

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
3. **`~/Library/Application Support/pacer` is the owner's data, and no test or tool may resolve it.**
   Every store seam resolves through `studio/app_support.py`: `PACER_APP_SUPPORT_DIR` wins; with
   `PACER_APP_SUPPORT_JAIL` set, or when the entry script is a file in `tests/`, the first resolution
   makes a temp jail, exports it to child processes and removes it at exit. CTest sets the flag on every
   registration (the loop that must stay LAST in `tests/CMakeLists.txt`), and `studio/dev/_jail.py`
   exports the jail it makes. So a test that patches nothing, a `python -c` child, and a test file run by
   hand are all jailed. **An ad-hoc probe script outside `tests/` is not**: call
   `_jail.divert_app_support(...)` before building a window, or run it with `PACER_APP_SUPPORT_JAIL=1`.
   `tests/test_app_support_jail.py` fails the build if any of this stops being true (H8: a `ctest` run
   once wrote a `stadium` fixture row into the owner's real `library.json`).

The studio app layers **ingest → load → session → controllers → views** (only `session.py`,
`load.py`, `ingest.py`, and `tracks.py` may import `pacer`; views stay pacer-free — and, in the
mirror direction, only the view / Qt-infrastructure modules may import Qt, so the data core stays
headless). Both directions are enforced by `tests/test_layering.py`. For the full studio
architecture rules an agent must respect (local-meter coordinate space, those layering contracts,
perf invariants), see [studio/README.md](studio/README.md).

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
