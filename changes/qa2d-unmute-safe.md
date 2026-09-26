### Changed

- Sound stays on when you open another recording, until you quit; every launch still starts muted

### Fixed

- Un-muted video could, rarely, freeze the app for good when playback stopped or another recording
  opened; the audio thread no longer waits on Python while it holds a Qt lock
