### Fixed

- The map dot glides with the video instead of jumping ten times a second: it moves on every
  frame, between the GPS samples, as the exported map's dot already did (#415)

### Changed

- `pixi run studio` opens 1.5-3.5 s sooner: an unchanged build now takes 0.1 s, and the missing
  "SF Mono" font no longer costs about 60 ms at every launch (#415)
