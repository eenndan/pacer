# AGENTS.md — `pacer` repository context

`pacer` analyzes **race telemetry** (go-kart / motorsport GPS data) from GoPro videos (GPMF): it
segments **laps and sectors** and visualizes them. The product is **`studio/`**, a local
**PySide6 + pyqtgraph** desktop app (track map, speed/Δ charts, lap table, synced video, g-meter),
pure Python on a **C++23 core** (`pacer/`) through litgen-generated nanobind bindings
(`bindings/pacer`).

This file is loaded into every session; the rest is read on demand:
- **App work:** [studio/README.md](studio/README.md) — module map, layering, perf invariants.
- **C++ core and bindings:** [pacer/README.md](pacer/README.md) — data flow, modules, codegen.
- **Tests, the golden gate, real footage:** [tests/README.md](tests/README.md).
- **Working rules:** [docs/AGENT-PLAYBOOK.md](docs/AGENT-PLAYBOOK.md) — gates, evidence, delivery.
- **Before proposing a feature:** [studio/docs/refused-2026-09.md](studio/docs/refused-2026-09.md)
  — the ones measured and refused, with the numbers that killed them.

---

## Directory map

```
pacer/                # repo root: CMakeLists.txt (C++23), pyproject.toml (pixi manifest: deps + tasks),
│                     #   pixi.lock (osx-arm64 ONLY), .github/ (CI; package-smoke on a tag or manual run)
├── pacer/            # C++ core — one folder = one static lib pacer::<name>: datatypes, geometry,
│                     #   gps-source (GPMF: GPS5/GPS9 + ACCL/GYRO/GRAV/CORI), laps
├── studio/           # THE APP; dev/ = probes and validation scripts; docs/ = write-ups + refusals
├── bindings/pacer/   # the `pacer` Python package (litgen-generated, nanobind runtime)
├── tests/            # Catch2 C++ suites + Python studio tests, all CTest registrations
├── packaging/        # the .app/.dmg build (pacer.spec, build_macos.sh) — see PACKAGING.md
├── docs/             # the PUBLIC pages: ACCURACY.md, FIRST_LAP.md, index.html + media/
└── 3rdparty/         # submodules gpmf-parser + nanobind: `git submodule update --init --recursive`
```

---

## Build, run & test

`osx-arm64` only. `pixi install` builds the env and the editable `bindings/pacer` package.

| task | does |
|---|---|
| `pixi run build` | configure + build everything (cmake + Ninja → `build/Release`), binding codegen included |
| `pixi run test` | **the pre-PR gate**: every CTest registration — ~281 s on the dev Mac with the 16 real-footage checks and the 6 `videotoolbox.*` export checks, which CI reports Skipped; the crash soak is reported Skipped (see `test-soak`) |
| `pixi run test-fast` | the inner loop: `test` minus `test_export_video` and `test_compare_lifecycle`, footage and the soak reported Skipped by name; the `videotoolbox.*` export checks run (CI reports them Skipped) — ~143 s |
| `pixi run test-footage` | only the 16 real-footage checks (`ctest -L footage`) — ~139 s |
| `pixi run test-soak` | only the soaks (`ctest -L soak` with `PACER_SOAK=1`): the compare-toggle crash soak, ~146 s. CI runs it on pushes to main and tags |
| `pixi run golden` | only the core-math equivalence gate (`test_golden_synthetic`: seeded sessions + the synthetic GoPro through the real loader) — ~8 s |
| `pixi run smoke` | the CI E2E gate: full `StudioWindow` offscreen on the bundled sample |
| `pixi run studio [-- files]` | the app, on the recordings you name |
| `pixi run gen-bindings` | regenerate the bindings |
| `pixi run lint` · `fmt` · `fmt-check` | `ruff check .` · clang-format in place · its non-mutating CI check (both skip the generated `nanobind_pacer.cpp`) |
| `pixi run typecheck` | pyright (basic) over the allow-listed Qt-free core modules in `pyrightconfig.json` — ~4 s, no build needed. A module joins the list in the PR that makes it clean, a new Qt-free module in the PR that creates it; `pixi run typecheck studio/<m>.py` lists one module's errors |

- **`test` and `test-fast` run four tests at once** (`CTEST_PARALLEL_LEVEL=4` in the task env).
  Append `-j1` for a serial run to rule an interaction between tests in or out; never put a `-j`
  inside a task — ctest 4 rejects a second one. Why four: `pyproject.toml`.
- **A `Timeout` far past `--timeout` is the Mac sleeping mid-run**, not a hang (both tasks run under
  `caffeinate -si`; check `pmset -g log`). One AT the limit while other lanes are busy is
  contention: check `uptime` and re-run the test alone.
- **Run one test:** `pixi run ctest --test-dir build/Release -R '^test_x$' --output-on-failure` —
  CTest injects the `PYTHONPATH=bindings/pacer` + `QT_QPA_PLATFORM=offscreen` a bare
  `python tests/test_x.py` can miss. **One test function:** `pixi run python -m pytest
  tests/test_x.py -k <part>` (jailed by `tests/conftest.py`; for iterating, not the gate).
- **Real-footage checks** (`footage.<check>`) re-measure published figures on the owner's Desktop
  recordings. Without its recording a check is reported *Skipped* by name — a skip is not a pass,
  so **name every skipped `footage.*` when you report gates.** Variables, defaults and how to add
  one: [tests/README.md](tests/README.md).
- **Golden equivalence gate:** a timing / geometry / delta / Session change must keep the validated
  numbers. CI half: `pixi run golden` (re-cut its baseline only after a reviewed, intentional
  change: `python tests/test_golden_synthetic.py --write-baseline`). Manual half, on real footage:

  ```bash
  pixi run python -m studio.dev.golden_session_dump /tmp/before.json   # BEFORE the change
  # … make the change, then: pixi run build …
  pixi run python -m studio.dev.golden_session_dump /tmp/after.json    # AFTER
  pixi run python -m studio.dev.golden_compare /tmp/before.json /tmp/after.json   # expect max|Δ| = 0
  ```

  No `PYTHONPATH` needed. The dump's positional argument is its OUTPUT (it refuses anything but a
  new `.json`); the recording comes only from `PACER_GOLDEN_MP4` or its default.

---

## PR workflow (the CI sequence)

CI (macos-14) runs these in order on every push and PR, each gating the next — run them
locally first:

1. `pixi run build` — compiles the core and regenerates the bindings.
2. **Bindings drift** — any `pacer/**/*.hpp` edit (even a comment) regenerates
   `bindings/pacer/nanobind_pacer.cpp` + `bindings/pacer/pacer/__init__.pyi`: **commit BOTH**, or
   `git diff --exit-code -- bindings/` fails.
3. `pixi run lint`.
4. `pixi run fmt-check` (`pixi run fmt` fixes it).
5. `pixi run typecheck`.
6. `pixi run test` — green (on a push to main or a tag it includes the soak).
7. `pixi run smoke`.

- **One focused change per PR.** Match the surrounding comment density, naming and idiom; favour
  "why" comments over restating the code.
- **Add or update a test for any behaviour change** — pure logic: a Qt-free module with a
  synthetic-data test ([tests/_synthetic.py](tests/_synthetic.py)); real-widget paths: an
  offscreen-Qt test. A new `tests/test_<name>.py` registers itself (offscreen Qt + the bindings
  on PYTHONPATH) and its "why" goes in its module docstring; `tests/CMakeLists.txt` is edited
  only for its exceptions table ([tests/README.md](tests/README.md)).
- **Refused features stay refused** unless you bring NEW evidence. A new refusal is the next free
  section of `studio/docs/refused-2026-09.md` on your base; `tests/test_measured_figures.py`
  checks the numbering and every citation of it.
- **Changelog:** a user-visible change (feature, fix, behaviour tweak) adds its own fragment,
  `changes/<branch-slug>.md` (branch `f1/foo` → `changes/f1-foo.md`), and never edits
  [CHANGELOG.md](CHANGELOG.md): a `### Added`, `### Changed` or `### Fixed` heading over `- `
  bullets of at most 2 lines × 100 characters, ending in `(#PR)` if you know it (the fold finds it
  otherwise). Internal refactors/tests/docs get none. `tests/test_version.py` parses every fragment
  and holds each section after 0.2.0 to Highlights/Added/Changed/Fixed entries of at most 3 lines.
  CI fails a PR that edits CHANGELOG.md unless its title starts with "release" or it deletes the
  fragments it folds.
- **Release:** `pixi run changelog` previews the fold; `pixi run changelog --write --release x.y.z`
  folds every fragment under `## [x.y.z] — today` with its compare link and deletes them. Write its
  Highlights (5–8 bullets), bump the version in its THREE places — `studio/__init__.py`
  `__version__` (canonical: read by `packaging/pacer.spec` and the About card), `pyproject.toml`
  (names the `.dmg`) and `bindings/pacer/pyproject.toml` — then tag. `tests/test_version.py` fails
  on any step missed. Run `pixi run test-soak` before tagging; CI runs it again on the tag.

---

## Rules and gotchas

1. **Everything under `~/Desktop` is the owner's footage and strictly read-only** — never a path
   you pass as an output. D24 now lives on an external drive: never search for it or mount it.
2. **`~/Library/Application Support/pacer` is the owner's data, and no test or tool may resolve
   it.** Every store resolves through `studio/app_support.py`: CTest jails every registration (the
   loop that must stay LAST in `tests/CMakeLists.txt`), and a test file run by hand is jailed too.
   **An ad-hoc probe outside `tests/` is not** — call `studio/dev/_jail.py`'s
   `divert_app_support(...)` before building a window, or run it with `PACER_APP_SUPPORT_JAIL=1`.
   `tests/test_app_support_jail.py` holds this (H8: a `ctest` run once wrote a fixture row into the
   owner's real `library.json`).
3. **Never hand-edit generated bindings** (`bindings/pacer/nanobind_pacer.cpp` between the litgen
   markers, `*.pyi`) — change the header or litgen options and `pixi run gen-bindings`. The
   `#include` preamble at the top of `nanobind_pacer.cpp` is the one hand-kept region.
4. **Layering:** ingest → load → session → controllers → views. Only `session.py`, `load.py`,
   `ingest.py` and `tracks.py` may import `pacer`; views stay pacer-free; and only the view /
   Qt-infrastructure modules may import Qt, so the data core stays headless.
   `tests/test_layering.py` enforces both directions; the other studio rules (local-meter
   coordinates, perf invariants) are in studio/README.md.
5. **Units and coordinates:** speeds in m/s (×3.6 only at display), angles in degrees. Lap timing
   is purely geometric — the start/finish crossing interpolated along the chord — and the timing
   lines are in LOCAL metres: mixing local and GPS coordinates is the core's main hazard.
6. `assert()`-guarded invariants vanish under `NDEBUG`/Release — never rely on them at runtime.

---

## Key dependencies

pixi (conda-forge, osx-arm64) · CMake ≥ 3.28 + Ninja · scikit-build-core · litgen (git) →
nanobind ≥ 1.3.2 · gpmf-parser (submodule) · Catch2 · PySide6 + pyqtgraph + qtawesome (Phosphor
icons, `studio/theme.py`) · Python 3.13 + numpy. `ninja` and `catch2` are **explicit** pixi deps:
an interrupted `pixi add` once pruned them and broke the build.

## gitnexus (optional code-graph index)

Index in `.gitnexus/`, not always current: `gitnexus status` first, `gitnexus analyze` to refresh;
then `gitnexus query "<concept>"`, `gitnexus context "<symbol>"`, `gitnexus impact "<symbol>"`.
The engine is LadybugDB/Kùzu, not Neo4j — use `labels(n)` and `(n:Label)`; `type(r)` is unsupported.
