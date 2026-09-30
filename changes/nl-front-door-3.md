### Added
- `pixi run verify` times a synthetic GoPro recording against its known truth (noise-free laps
  within 0.41 ms at a line mid-straight), then runs the golden gate: a timing check with no footage.

### Changed
- The published accuracy gives each recording's mean error as a point estimate, +0.001 to
  +0.003 s, no longer as a bound; the accuracy chart's caption says the same.
