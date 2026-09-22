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
through `_align` — read that module before changing any of them. It has TWO maps: the gyro's
content rides the picture and the accelerometer's rides its stamps, so a probe that places ACCL
through the gyro's map puts it ~0.4 s from where it was measured (T9 found P2 and P3 doing that).

`p4_corner_gps_quality` is the odd one out and needs neither the cache nor `_align`: it asks whether
the GPS-quality signal can support a PER-CORNER coaching abstain, which is a question about one
channel rather than two, so it loads each recording itself and reads the strip the loader already
built. Its verdict is `studio/docs/refused-2026-09.md` §4.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p4_corner_gps_quality

`p5_clock_crossing_scale` measures the clock crossing p4 asks about at every window length rather
than one: the strip's naive-second cells indexed by telemetry windows, each join checked per kept fix
against the stamps the strip binned. Its rule is written in `Session.quality_timeline`.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p5_clock_crossing_scale

`p6_corner_match_cause` also loads each recording itself. It asks why 0060's corner boundaries fail
the spatial match far more often than 0062's. It relaxes one gate of the real
`corners._spatial_matches` at a time, and tests GNSS scatter, the reference lap, the threshold and
chapter seams against each other. Its verdict is `studio/docs/corner-match-0060-2026-09.md`.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p6_corner_match_cause

`p8_corner_anchor` is the only one here that measures a SHIPPED change rather than a channel the
app does not have: it drives `corners.session_geometry` and the real `_spatial_matches` on both D24
recordings, prints the fitted receiver drift, plants a translation and a wider racing line on the
same lap to show the fit separates them, and scores the result against a CURVATURE witness that a
rigid translation cannot move. Its numbers are the evidence for M7.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p8_corner_anchor

`p10_heat_to_heat` loads the three present Sandown recordings and puts every pair through the real
`focus.verdict`, with the whole lap as the window. It checks once against the owner's own
session-record store (read, never written) and once against a planted pair of records that agree.
It asks whether a session-level "better than last time" would ever be allowed, and whether the
change it would print is bigger than the spread inside one session. Its verdict is
`studio/docs/refused-2026-09.md` §8.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p10_heat_to_heat

`p11_hesitation`, `p12_median_polish` and `p13_grip_regrounding` are the M5 set: the three LATER
analytics ideas, each with a null it has to beat and a planted effect that says what its silence is
worth. They share `_m5_cache` (one `Session.load` per recording, everything the three read packed
into one pickle) and `_m5_stats` (the permutation machinery, the max-statistic correction and the
selection control that a max-statistic correction does NOT cover). Verdicts: `refused-2026-09.md`
§9 (hesitation — the gap's median IS the instrument's floor) and §10 (the learning/evolution
decomposition — the corner-specific half never beats a shuffled lap order), and
`grip-regrounding-2026-09.md` (the one that passed: Grip % is not pace-blind within a corner,
and still cannot rank corners against each other).

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes._m5_cache
    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p11_hesitation
    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p12_median_polish
    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p13_grip_regrounding

`p15_gps_gap_census` loads all four present recordings itself and asks whether any of them has a
GPS dropout for the map's gap bridge to fill (L2, a gyro-yaw bridge). It wraps the loader's own
read, quality gate and clean stages to attribute every hole, counts the gaps the map would bridge
with the app's own `gapfill.find_gaps`, and then PLANTS gaps in memory to score today's bridge
against the fixes it replaced. Its verdict is `studio/docs/refused-2026-09.md` §12.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p15_gps_gap_census
"""
