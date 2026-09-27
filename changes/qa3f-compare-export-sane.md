### Changed

- The comparison video keeps its own resolution, opening on 1080p panes; "Source" is capped at one
  4K frame, as two 4K panes (3840×4320) were a 299 MB file phones don't play (#439)

### Fixed

- The comparison video ends on both lap times as the table shows them; it stopped a frame short, at
  0:46.799 / 0:46.903 for 0:46.808 / 0:46.912 (#439)
- The first export dialog of a session states the size and time at "Source" once the footage is
  measured, and says it is measuring until then (#439)
