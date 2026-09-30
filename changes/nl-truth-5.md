### Fixed
- Sector times are read where the lap crosses a sector line, not at the nearest fix. Noise-free, the
  worst synthetic split error falls from 85 ms to 9 ms; with GPS noise, the noise dominates (#477).
