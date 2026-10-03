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

`p6_coast_places` prints the Stats page's COASTING table (`Session.coast_report`) for each
recording and asks what its order is worth, over exactly the rows the page ranks
(`Session._coast_rows`): the verdict on odd/even laps and on each half, a shuffled negative control,
a planted positive control, and the normalized projection beside the warp. Its verdict is the
comment above `stats.COAST_LEAD_ALPHA`: the order is claimed only where the laps separate it.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p6_coast_places

`p7_overdrive` builds the overdriving detector F4 proposed (per corner, entry speed against the time
in the corner, under a within-lap permutation null) and asks what it is worth: shuffled and planted
controls, a covariate cross-check, replication across halves and recordings, and how often each
entry and exit speed was read at a boundary matched on track. Its verdict is
`studio/docs/refused-2026-09.md` §6.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p7_overdrive

`p8_corner_anchor` measures a SHIPPED change rather than a channel the app does not have: it
drives `corners.session_geometry` and the real `_spatial_matches` on both D24 recordings, prints
the fitted receiver drift, plants a translation and a wider racing line on the same lap to show the
fit separates them, and scores the result against a CURVATURE witness that a rigid translation
cannot move. Its numbers are the evidence for M7.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p8_corner_anchor

`p9_interpolated_consumers` measures, through the real accessors on both D24 recordings, what
#331's rule (a corner time needs both its edges matched on track) does to the consumers that still
counted every cell: coaching, the ideal lap, braking, coasting and the per-lap CSV. Its numbers are
the evidence for #339, which applied the rule to coaching, the ideal lap and braking and gave the
CSV a disclosure column; the note under `CornerModel.lap_corner_resolved` gives the measurement
that kept coasting counting every cell.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p9_interpolated_consumers

`p6_coast_places`, `p7_overdrive` and `p9_interpolated_consumers` skip a recording whose footage is
missing. D24 and Sandown 09-05 are no longer on the development machine, so p6 and p7 run on two
of their five recordings, and p9, which reads only D24, on none.

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

`p14_rpm_audio` asks whether the audio track carries the engine (M3). It checks its pitch estimator
against a KNOWN signal first: an engine-like tone in speed-dependent wind, through an automatic gain
control and the AAC codec, with ffmpeg used only through pipes. It then runs the frozen estimator on
the four present recordings, against the one law a single-speed kart must obey above clutch
lock-up: the tone is proportional to road speed. `studio.gearing`, the arithmetic an RPM number
would have to agree with, reads the measured constant as sprocket pairs. Its verdict is
`refused-2026-09.md` §11. The tone is real and wind does not drown it, but it is not an RPM.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p14_rpm_audio synthetic
    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p14_rpm_audio real

`p15_gps_gap_census` loads all four present recordings itself and asks whether any of them has a
GPS dropout for the map's gap bridge to fill (L2, a gyro-yaw bridge). It wraps the loader's own
read, quality gate and clean stages to attribute every hole, counts the gaps the map would bridge
with the app's own `gapfill.find_gaps`, and then PLANTS gaps in memory to score today's bridge
against the fixes it replaced. Its verdict is `studio/docs/refused-2026-09.md` §13.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p15_gps_gap_census

`p16_slow_clean_laps` loads every working-set recording itself, each Sandown 3h chapter alone and
every recording whole, and asks what the 61–90 s laps #358 found among chapter 1's clean laps are:
mis-segmented, pit laps or valid laps driven slowly. It reads each lap's distance, closure, distance
from the best lap's line, stop and time under 40 km/h, then reloads with the valid set wrapped to
measure what each pace surface does without them. Its verdict is `studio/docs/refused-2026-09.md`
§14.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p16_slow_clean_laps --surfaces

`p17_braking_room` loads the four working-set recordings and asks whether a RELATIVE braking-room
figure can replace the absolute "brake ~N m later", which is positive at every corner. It rebuilds
the per-lap list Stats ▸ BRAKING medianizes, asserts it equal to the app's, and holds each
candidate to its own noise bar and to the lap outcome (do later onsets go with quicker passes?). Its
verdict is `studio/docs/refused-2026-09.md` §16.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p17_braking_room

`p18_corner_kernel` asks whether the corner model's curvature window should be centred, as #383
centred the GPS-lag reference's. It swaps the kernel only inside `corners.pooled_curvature` and
measures it three ways: a uniformly sampled symmetric corner, the synthetic GoPro's known corner
geometry through the real loader (with an odd-window, a constant-speed and a no-boxcar control),
and the four working-set recordings, jailed and tripwired. Its verdict is
`studio/docs/refused-2026-09.md` §17.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p18_corner_kernel symmetric
    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p18_corner_kernel synthetic --controls
    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p18_corner_kernel real

`p19_truth_real` asks what would prove the four headline numbers wrong. Its `synthetic` mode holds
a per-recording estimate of the ideal lap's noise bias (the negative covariance of adjacent
segments) to the true bias on `tests/test_truth_matrix.py`'s synthetic grid, with a PASS/FAIL
line. Its `real` mode loads the four working-set recordings in date order, jailed and tripwired,
and checks the ideal lap, the coaching lead's time lost, the time on the brakes and the focus
verdict against a seeded bootstrap over the laps and an odd/even holdout. It takes only its mode
and folder names, and writes nothing but stdout and its jail. Its verdicts are written in
`studio/docs/falsification-2026-09.md`: the estimator fails its synthetic check, so the bias is not
estimable from a recording.

    pixi run python -m studio.dev.probes.p19_truth_real synthetic
    pixi run python -m studio.dev.probes.p19_truth_real real

`p20_packet_spread` measures the clock a GPS5-era camera is timed on, `ingest`'s packet-spread times,
on the nine bundled GPS5 clips: each fix's residual off the receiver's fixed-rate grid, checked
against each payload's GPSU stamp, and with `--control` against a GPS9 chapter's own per-fix stamps.
It needs no footage but the control, which it only reads, and writes nothing but stdout. Its numbers
are the rationale above `data_quality.MEDIA_CLOCK_LAP_ERROR`, the bound the app shows those cameras,
and `tests/test_load_pipeline.py` re-measures the nine clips against that bound on every run.

    pixi run python -m studio.dev.probes.p20_packet_spread
    pixi run python -m studio.dev.probes.p20_packet_spread --control <a GPS9 chapter .MP4>

`vt_gop` asks whether a 2 s keyframe interval would buy VideoToolbox's H.264 exports quality per
byte. It renders one 20 s window through the real `Renderer` into a lossless intermediate, encodes
that with `_video_codec_args`' exact VideoToolbox argv at four intervals, and scores each encode's
yield, keyframes, SSIM and PSNR and the cost of an accurate hardware seek. Everything it writes goes
into one `mkdtemp` under $TMPDIR; the recording is only read. Its verdict is
`studio/docs/refused-2026-09.md` §22.

    pixi run python -m studio.dev.probes.vt_gop synthetic
    pixi run python -m studio.dev.probes.vt_gop mk
    pixi run python -m studio.dev.probes.vt_gop verdict SYNTH.json MK.json
"""
