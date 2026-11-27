### Fixed

- A missing GPS fix near the start/finish line no longer moves the lap time: the trace is smoothed
  in time there, and the lap stays in the bests instead of wearing the dropout ⚠.
- Only a hole right at the line (two or more fixes in a row) or a recording that starts or stops
  there still flags the lap, so scattered rejected fixes no longer make a session read low.
