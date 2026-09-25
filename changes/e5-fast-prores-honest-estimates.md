### Changed

- Overlay-only ProRes 4444 renders on VideoToolbox where it keeps a true alpha, with prores_ks as
  the fallback: a 4K MK lap takes 65 s instead of 145 s, a 1080p one 24 s instead of 42 s
- The export picker gives "Source" a size, and every row a time to render naming its encoder; the
  dialog names remembered overlay-only or Source choices and offers "Use defaults"

### Fixed

- ProRes overlays are now converted and labelled Rec. 709, so both encoders give the same colours
