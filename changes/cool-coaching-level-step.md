### Fixed

- Coaching no longer ranks a corner for "consistency" when its middle half is under 0.07 s, the
  level step Stats ▸ CORNERS calls inside the noise: such a corner is listed as level, not ranked.
- The consistency reason is picked on the middle half of your passes, not σ, so one slow pass can
  no longer make a corner a consistency corner.
