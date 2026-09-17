"""studio.dev.probes — speculative-channel probes (§6 of the market research).

NOT product code, and deliberately not a feature. Each probe asks one falsifiable question about a
channel Pacer does not have, prints every number it computed, and the verdict is written up in
`studio/docs/measured-channels-2026-09.md`. A probe that answers "no usable signal, and here is the
distribution" has done its whole job.

    pixi run python -m studio.dev.probes._cache          # build both recordings' caches first
    pixi run python -m studio.dev.probes.p1_sideslip
    pixi run python -m studio.dev.probes.p2_chatter
    pixi run python -m studio.dev.probes.p3_bumpmap

Probes p1-p3 each compare a GPS-derived quantity against an inertial one, so each of them goes
through `_align` — read that module before changing any of them.

`p4_corner_gps_quality` is the odd one out and needs neither the cache nor `_align`: it asks whether
the GPS-quality signal can support a PER-CORNER coaching abstain, which is a question about one
channel rather than two, so it loads each recording itself and reads the strip the loader already
built. Its verdict is `studio/docs/refused-2026-09.md` §4.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p4_corner_gps_quality

`p5_clock_crossing_scale` measures the clock crossing p4 asks about at every window length rather
than one: the strip's naive-second cells indexed by telemetry windows, each join checked per kept fix
against the stamps the strip binned. Its rule is written in `Session.quality_timeline`.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p5_clock_crossing_scale
"""
