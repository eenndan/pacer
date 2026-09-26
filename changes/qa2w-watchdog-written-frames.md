### Fixed

- A video export whose encoder hangs as it starts is stopped after 10 s; that wait could grow to 30
  times the export's start-up, a minute when starting took 2 s
