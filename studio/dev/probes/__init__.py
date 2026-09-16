"""studio.dev.probes — speculative-channel probes (§6 of the market research).

NOT product code, and deliberately not a feature. Each probe asks one falsifiable question about a
channel Pacer does not have, prints every number it computed, and the verdict is written up in
`studio/docs/measured-channels-2026-09.md`. A probe that answers "no usable signal, and here is the
distribution" has done its whole job.

    pixi run python -m studio.dev.probes._cache          # build both recordings' caches first
    pixi run python -m studio.dev.probes.p1_sideslip
    pixi run python -m studio.dev.probes.p2_chatter
    pixi run python -m studio.dev.probes.p3_bumpmap

Every probe here compares a GPS-derived quantity against an inertial one, so every probe goes
through `_align` — read that module before changing any of them.
"""
