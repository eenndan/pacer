### Fixed
- Sector times are read where the lap crosses the sector line, between two GPS fixes, not at the
  nearest fix: on noise-free synthetic laps the worst split error falls from 85 ms to 9 ms (#477).
