### Fixed

- Reopening the export dialog in quick succession ran up to 14 hardware ProRes checks at once;
  it now runs one, and every dialog and export shares its answer.
- A hardware ProRes check that fails once is no longer remembered for the rest of the run (a
  guard: no such failure was seen in 1,912 checks); each export asks it at most once.
