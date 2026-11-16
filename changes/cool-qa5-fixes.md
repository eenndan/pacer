### Fixed

- A corner pass's gap to its typical pass is now the printed pass minus the printed typical: on the
  demo, lap 6 at C6 reads +0.82 s in the Laps table, Stats and the report (Stats said +0.83 s).
- CORNERS Med loss is the printed Median minus the printed Best beside it (the demo's C4 read +0.07
  beside 3.08 and 3.02; now +0.06), and its sum adds the printed cells.
- Moving through the Laps table with ↑/↓ (or picking a lap elsewhere) after "next ›" takes the
  outline off the slow lap it found, so two rows are never marked at once with motion off.
- The report's map no longer shows the "drag to set start/finish" cue, nor a key row for the video
  position it does not draw; the report still says the timing is provisional.
