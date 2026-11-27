### Fixed

- Library ▸ Update keeps your sort, selected row and scroll when it ends, and its hover stops
  saying "muted until" once nothing it could measure is left muted.
- Forget, Clear library or Restore during an Update stops it first: its line counts only the rows
  still there at the version it wrote, never a removed one as "could not be measured".
- A row whose file is now another recording says so in its own Ideal-cell hover, not "re-open".
