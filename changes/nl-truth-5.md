### Fixed
- Sector times are read where the lap crosses a sector line, not at the nearest fix. Noise-free, the
  worst synthetic split error drops from 85 to 9 ms; at a real recording's GPS noise, 193 ms (#477).
