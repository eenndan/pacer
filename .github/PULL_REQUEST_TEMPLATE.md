<!-- Thanks for contributing to Pacer! Keep PRs focused — one logical change each. -->

## What & why

<!-- What does this change, and what problem does it solve? Link any related issue (#123). -->

## How it was verified

<!-- Check what you ran locally. CI runs these in sequence. -->

- [ ] `pixi run build` (and committed regenerated `bindings/` if a `pacer/**/*.hpp` changed)
- [ ] `pixi run test` — full suite green
- [ ] `pixi run lint` (ruff) clean
- [ ] `pixi run fmt` (clang-format) clean
- [ ] Added/updated tests for the behavior change

## Notes for the reviewer

<!-- Anything non-obvious: trade-offs, follow-ups, screenshots for UI changes. -->
