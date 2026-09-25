### Fixed

- The exported map's comet tail now ends on the marker and trails it by the last 2.4 s at every
  resolution; it used to sit still elsewhere on the lap, and ran twice as long at 4K (#409)
- The exported map marker now glides every frame instead of stepping at the 10 Hz GPS rate, and
  holds still across a GPS dropout rather than cutting across the infield (#409)
- Overlay-only exports line up in an editor: 29.97 fps off 59.94 footage, the footage's own
  timecode embedded, and the finished message says where in which file the clip starts (#409)
