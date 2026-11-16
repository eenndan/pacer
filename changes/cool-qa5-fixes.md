### Fixed

- One corner pass prints one number everywhere: a slow lap's corner loss no longer reads +0.82 s
  in the Laps table and the report's SLOW LAPS while Stats and the report's CORNERS say +0.83 s.
- Moving through the Laps table with ↑/↓ (or picking a lap elsewhere) after "next ›" takes the
  outline off the slow lap it found, so two rows are never marked at once with motion off.
- The report's map no longer shows the "drag to set start/finish" cue, nor a key row for the video
  position it does not draw; the report still says the timing is provisional.
