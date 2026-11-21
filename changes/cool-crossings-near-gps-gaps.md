### Fixed

- A lap with a GPS fix missing just before or after its start/finish line now wears the dropout ⚠
  and stays out of the bests: the missing fix could move its time by tens of ms, unflagged.

### Changed

- When most laps wear the GPS-dropout ⚠, the session reads "GPS quality low" and says why; no PB,
  focus list or debrief is judged on it, and its Library row is marked degraded.
