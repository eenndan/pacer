# Contributing to Pacer

Thanks for your interest in Pacer! This is a small, focused project — a race-telemetry
analysis studio (a C++ core exposed to a PySide6 desktop app via nanobind). Contributions
of all sizes are welcome: bug reports, fixes, docs, and features.

> **License note:** Pacer is [CC BY-NC-SA 4.0](LICENSE) (NonCommercial, ShareAlike). By
> contributing you agree your contribution is licensed under the same terms.

## Getting set up

Pacer uses [pixi](https://pixi.sh) to manage every external dependency (`cmake`, `ninja`,
`catch2`, `ffmpeg`, the Python toolchain, and the binding codegen). You do **not** need to
install a C++ toolchain or Python separately.

```bash
git clone https://github.com/eenndan/pacer.git
cd pacer
git submodule update --init --recursive   # 3rdparty: gpmf-parser, nanobind
pixi install                              # environment + editable Python bindings
pixi run studio -- /path/to/GX010060.MP4  # build + launch on a recording
```

Platform: macOS on Apple Silicon is the only tested target today (see `pyproject.toml`
`[tool.pixi.workspace] platforms`). Other platforms are unverified — PRs that broaden support
are welcome.

## Development workflow

```bash
pixi run build   # configure + build everything (cmake + Ninja + binding regen)
pixi run test    # C++ (Catch2) + Python tests, all via ctest
pixi run fmt     # clang-format the C/C++ sources
pixi run lint    # ruff
```

`AGENTS.md` documents the architecture, the studio module layout, and the conventions in
much more depth — read it before a non-trivial change.

## Before you open a PR

CI runs these checks **in sequence** (each must pass before the next runs), so run them
locally first to avoid a red build:

1. **`pixi run build`** — compiles the core and regenerates the bindings.
2. **Bindings drift** — any change to a `pacer/**/*.hpp` header (even a comment) regenerates
   `bindings/nanobind_pacer.cpp` and `bindings/pacer/pacer/__init__.pyi`. **Commit both
   regenerated files** — CI fails if `git diff -- bindings/` is non-empty after a build.
3. **`pixi run test`** — the full suite must be green.
4. **E2E smoke** — `python -m studio.dev._smoke --no-video` builds the real app headless.
5. **`pixi run lint`** — `ruff check .` clean.
6. **`pixi run fmt`** — `clang-format` clean (`pixi run fmt` to auto-fix).

## Conventions

- **One focused change per PR.** Keep diffs reviewable.
- **Match the surrounding style** — comment density, naming, and idiom. The codebase favors
  explanatory "why" comments over restating the code.
- **Add or update tests** for behavior changes. Pure analysis logic lives in Qt-free modules
  with synthetic-data tests (see `tests/_synthetic.py`); real-widget paths have offscreen Qt
  tests.
- **Core math changes** (timing, geometry, delta) must preserve the validated numbers — pin
  them with a test.

## Reporting bugs / requesting features

Use the issue templates. For bugs, the GoPro model + firmware and a short telemetry clip (or
its symptoms) help enormously, since the GPMF stream layout varies by camera.
