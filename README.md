# Pacer

*The case study as a web page: [eenndan.github.io/pacer](https://eenndan.github.io/pacer/)*

**A macOS race-telemetry workstation built from a single GoPro file, with its lap timing validated
against a real transponder — and the engineering case study behind it.**

Open a GoPro `.MP4`. Pacer reads the 10 Hz GPS and 200 Hz IMU the camera already recorded and gives
you what a dedicated data logger would: lap and sector times, the racing line, an **ideal lap**
stitched from your own best segments, and a ranked list of the corners where you give the most
away. No transponder, no logger, no cloud, no extra hardware.

It is a real, working app — and it is a **portfolio piece**, so this page is written as a case
study: what it claims, how each claim was measured, and the gates that keep it true. **Source is
the only distribution** — no download, no `.dmg`, no signed build (see [Non-goals](#non-goals)).
Building it is one command; reading it is the other intended use.

<img src="docs/media/hero.png" width="880" alt="Pacer's four-panel window on Sandown 3h: synced GoPro video, the speed-coloured track map with brake points and seven named corners, the speed and Δ-to-ideal charts reading Δideal +0.26 s, and the Laps · Corners · Stats · Coaching · Marks panel with lap 31 starred as the best at 0:47.076">

*Every screenshot here is Pacer on Sandown 3h — three hours at Sandown Park, July 2026 — captured
by one command, [`studio/dev/media_capture.py`](studio/dev/media_capture.py). In motion:
[20 seconds of the best lap as the app exports it](docs/media/best-lap.mp4) (MP4, no sound).*

---

## Accuracy — the claim everything else rests on

Pacer's lap times are validated **out-of-sample against official timing** — a real transponder log,
the ground truth a race series scores a session with, and a circuit's own published timing sheet.
Over **121 clean laps** across three recordings:

- essentially **unbiased** — mean error within **±0.003 s**;
- **σ 0.0247 s**, **0.0527 s** and **0.0871 s** — the worst about **0.13 %** of a ~68 s kart lap,
  quoting the worst of the three on purpose;
- 14 clean laps of 15 aligned, 48 of 57, and 59 of 65. A modest sample, and the honest one.

Two recordings are D24's — the Daytona 24-hour race at Milton Keynes, May 2026, that Pacer was
built on — checked against the race's transponder log in June 2026 and reported as recorded: that
footage has left the development machine. The third is a sprint race at the same
circuit on 18 September 2026, re-validated in September 2026 against the circuit's Club Speed timing
on footage that is on the machine, by one command a real-footage check re-runs. The Sandown
recordings, the other circuit, wait for their timing sheets, which sit behind a Club Speed sign-in.

<img src="docs/media/accuracy.png" width="880" alt="Lap-time error against official timing: recording A (D24, transponder) mean +0.0030 s, σ 0.0871 s, 48 clean of 57 aligned laps, median DOP 2.4; recording B (D24, transponder) mean +0.0015 s, σ 0.0527 s, 59 clean of 65 aligned, median DOP 1.4; recording C (MK sprint, Club Speed, September 2026) mean +0.0010 s, σ 0.0247 s, 14 clean of 15 aligned, median DOP 1.25">

No lap is hand-matched: the session's per-lap *duration* sequence is correlated against every
candidate window of the timing. Because the winning window is *chosen* to maximise `r`, that `r` is
not an accuracy statistic — the **margin** is. Against the 24-hour transponder log the fingerprint
matches at **r ≥ 0.99 at exactly one offset and below 0.29 at every other**. Against the sprint's
sheet — every driver of every heat that day, 99 rows — it matches one window at r 0.9998, and every
other window leaves a residual at least **44×** larger. The session Pacer timed is provably the
session the official timing timed.

Why it works: timing runs on the camera's own **GPS9 true clock**, not the video/sample clock that
consumer tools use, which drifts on the order of 0.1 % — enough to quietly bias every lap in a
session. Where the remaining error comes from, and the sensor fusion, Kalman/RTS smoothing,
Doppler-aided positioning and map-matching that were tried and **rejected on evidence**, are in
**[docs/ACCURACY.md](docs/ACCURACY.md)**.

That clock is also what lets a **delta** be measured rather than estimated. Without a per-sample
clock, the only way to price a stretch of track is to integrate `∫ds/v` along it — and `1/v`
amplifies any speed error exactly where the car is slowest, which is exactly where a corner's time
is largest. Pacer's own corner phase bars were still doing that, and auditing them against the
clock found them wrong on **every one of the 12 corners** of the D24 best lap, by up to
**+0.493 s** on the slowest (`studio/coaching.py::_span_clock`). Every delta the app shows — the Δ
trace, sector and corner splits, the ideal lap, the coaching breakdown, the exports — is a
difference of two clock readings, and a test fails if an estimator comes back.

---

## What it does

**Lap and sector timing you can audit.** True-clock timing needs the GPS9 stream, and by GoPro's
own metadata spec that means a **Hero 11 or a Hero 13**: GPS9 arrived with the Hero 11, the Hero 12
has no GPS receiver at all and cannot be lap-timed, and GPS returned with the Hero 13. Every earlier
GPS-equipped model — Hero 5 through Hero 10, and the Max — carries GPS5 only, which has no
per-sample clock, so Pacer times those recordings on the video clock, mutes every duration it
derives and labels it estimated. Nothing is keyed off the model name: the loader looks for the
stream and reports which clock it actually built. On an unknown track it fits a start/finish line
for you, calls the timing *provisional*, and demotes what depends on it until you drag the line into
place — `⌘Z` undoes.

**An ideal lap that states its own sample.** The target is a composite of your own fastest corners
and straights, and the Stats page shows the decomposition: what each segment gives away, how many
of your clean laps have already matched it, and which lap set the mark. The gain column sums to the
headline. It is an *order statistic*, so it falls as a session gets longer — the app says how many
laps it was minimised over rather than letting you read it as a floor.

<img src="docs/media/ideal-lap.png" width="610" alt="Stats ▸ IDEAL LAP on Sandown 3h: 0:45.809 theoretical best over 62 laps, −1.27 s on the table vs your best, stitched from 13 of your 62 clean laps across 7 corners and 8 straights, with a per-segment gain table whose nine rows hold 1.13 s of the 1.27 s">

**A racing line that is a data channel.** Colour it by speed, Δ to best, grip or elevation. Brake
points, corner apexes and draggable start/sector lines sit on it, and every mark is named in the
key — drag a line and the session re-segments.

<img src="docs/media/map.png" width="620" alt="The map panel maximised on Sandown 3h: the racing line coloured by speed from 35 to 84 km/h over every lap's trace, seven named corner apexes, brake-point markers, the draggable start-line handle, and the map key naming every glyph">

**Charts, video, and the gap between two laps.** Speed and cumulative Δ to ideal, distance-aligned
so corners line up. Scrub the chart and the footage follows; play two laps side by side — including
the best lap of *another* recording of the same track. A g-meter and the brake / coast / grip
channels ride along.

**Coaching.** A ranked shortlist of the corners where your *typical* lap gives away the most to
your *best* one, each with a dominant reason chosen from four measured signals — apex speed,
braking, coasting, line — and a button that jumps the video to your best lap through that corner.
No model, no ML: every number is a reduction of a validated channel.

**Session statistics.** A full page — PACE, IDEAL LAP, SECTORS, CORNERS, BRAKING, STRAIGHTS,
DRIVING, SPEED · G and **DATA TRUST**, which states what the timing was derived from, how many
fixes were rejected, and the IMU↔GPS cross-check with its **gain** (correlation alone cannot catch
a mis-scaled channel).

<img src="docs/media/data-trust.png" width="660" alt="Stats ▸ DATA TRUST on Sandown 3h: 62 of 70 laps used, GPS9 true clock with 0% of moving fixes rejected, video sync corrected for a measured 0.46 s GPS lag, g-meter from IMU lateral and GPS-derived longitudinal, an IMU↔GPS cross-check reading lateral r=+0.96, lateral gain ×1.09, longitudinal r=+0.77 over 767,312 samples, and a gyroscope rotation cross-check — above the friction circle, with a 1.50 g grip envelope">

**Exports.** Lap times and per-lap channels as CSV, a session report as HTML, a shareable lap card
as an image, and the telemetry burned onto the footage as an MP4 (via ffmpeg) —
[20 seconds of one](docs/media/best-lap.mp4), the best lap's, cut for the web.

<img src="docs/media/overlay.png" width="880" alt="A frame of a real exported overlay video on Sandown 3h: LAP 23, its elapsed 0:19.799 and a live Δ +0.04 burned into the top-left of the GoPro footage, a g-meter reading 0.3 g top-right, a mini track map bottom-right and 57 km/h bottom-left">

**And the window is yours.** Four resizable panels with a tabbed lap panel (Laps · Corners · Stats ·
Coaching · Marks, digits `1`–`5`), a maximize button on every panel header, `⌘⌃F` for full screen,
km/h ↔ mph, colour-blind-safe cues, a local session library, and privacy controls under
`Help ▸ Your data & privacy`.

What shipped when is in **[CHANGELOG.md](CHANGELOG.md)**.

---

## How it's built

One desktop app on a small C++ core, with the correctness moved out of code review and into gates.

- **A 2,199-line C++23 core** (`pacer/`) does the load-bearing work: GPMF ingest, geometry,
  lap/sector segmentation, and GPS9 true-clock timing. It is a clean-room, independently authored
  implementation (see [Acknowledgements](#acknowledgements)).
- **Bindings that cannot drift.** The nanobind glue and stubs are litgen-generated, and CI runs
  `git diff --exit-code -- bindings/` after the build: a header edit that isn't regenerated and
  committed fails the pipeline.
- **A golden fingerprint at eps 0.** Every core-math change is compared leaf-by-leaf against a dense
  fingerprint of a real recording's *whole* analysis API — 120k+ float leaves, `max|Δ| = 0`, no
  tolerance. The same machinery runs in CI over a deterministic synthetic session, so the gate never
  sleeps on a machine without the footage.
- **Design guards that fail the build.** Every spacing, radius and control height in the stylesheet
  must be a declared token (`tests/test_design_system.py`); no module may hand-pick a layout
  dimension or an inline style; every user-visible glyph must render in the app's own face rather
  than an OS fallback; and colour is checked for WCAG AA contrast plus a colour-blind ramp measured
  against a deuteranopia simulation.
- **A QA harness that drives the real app.** `studio/dev/ui_capture.py` builds the real
  `StudioWindow` offscreen on real footage, so a UI finding arrives as a measurement rather than an
  opinion — *"in a 447 px quadrant these 8 columns wanted 501 px, so `Grip (est)` started at x=422
  and 0 of 12 grip cells rendered a readable value"* is the comment above the fix. A companion
  guard proves the app never writes into its own source tree, from a tripwire on every write path
  Python and Qt expose.
- **140+ CTest registrations** — Catch2 over the C++ core, plus offscreen Qt suites that build real
  widgets and measure them. CI runs all of it on every pull request, four tests at a time, in about
  six minutes (its test step on the last three `main` runs, September 2026: 322–370 s), plus an
  end-to-end offscreen smoke — except the fifteen `footage.*` checks, which need a real recording
  CI does not have, and one crash soak that runs on every push to `main` instead; CTest lists those
  by name as *Skipped*. `pixi run golden`, the gate you actually run after every maths change,
  takes about a second.

Depth: **[AGENTS.md](AGENTS.md)** (the authoritative developer reference) and
**[studio/README.md](studio/README.md)** (the module map).

## Honesty as a design rule

The hard part of a tool like this is not computing a number, it is refusing to present one better
than it is. So that is a rule with an owner in the code:

- **one canonical `(est)`.** `theme.ESTIMATED_MARK` is the only place the wording lives, so the app
  cannot end up spelling it four ways — `(est)` here, `(EST)` there, `ESTIMATED` and `(est.)`
  elsewhere.
- **provisional timing demotes what depends on it** — an auto-fitted start line means no
  personal-best celebration and a lap table that says why.
- **the shareable outputs are gated.** The lap card and the overlay MP4 read the same trust verdict
  the UI does; the card refuses on provisional timing and the video warns before it burns a number
  into a file someone else will see.
- **the ideal lap discloses its sample**, because a sum of per-segment minima falls the longer you
  stay out. Same driving, same recording (Sandown 3h, three hours at Sandown Park):
  46.801 s over 5 laps, 45.809 s over 62.
- **DATA TRUST** puts the accuracy story inside the product, not only on this page.
- **a citable marker vocabulary on the way out.** In the app a caveat has hover, colour and weight
  to carry it, so Pacer keeps its own marks — `(est)`, the muted provisional demotion, ⚠, ⊘. An
  exported table has none of those, so `laps.csv` and the HTML report use the **UK Government
  Analysis Function's standard table symbols** instead — `[e]` estimated, `[p]` provisional, `[u]`
  low reliability, `[b]` break in series — each one decoded by a key in the same file. The choice is
  per marker and written down: `[x]`, `[z]`, `[r]`, `[f]` and `[c]` are refused with reasons in
  [studio/data_quality.py](studio/data_quality.py), and ⊘ EXCLUDED is named as having no standard
  equivalent at all — a lap that was measured, is shown, and is deliberately not counted is neither
  "not available" nor "not applicable". Adopting four of nine and saying why the rest do not fit is
  the point; a vocabulary forced onto the last two cases would be worse than the house style.

## Built with LLM coding agents

Pacer is developed solo, with the implementation handed to LLM coding agents under a strict
discipline: one focused pull request per change, gated by the golden-equivalence check on real
footage and the full suite before it merges. The rigour above is what makes that workflow safe —
the guardrails do the trusting so the agents can do the typing. 203 merged pull requests went into
the v0.2.0 cycle alone.

## Built, measured, not shipped

An idea here gets built far enough to measure, and the measurement is allowed to say no.
**[Features measured and refused](studio/docs/refused-2026-09.md)** is the record of every one
that said no, each with the number that killed it, so whoever proposes it next starts from the
evidence. The longer investigations are kept the same way:

- [GPS lap-timing accuracy: research and an empirical evaluation](studio/docs/gps-accuracy-research.md)
- [An upstream "~20 ms vs transponder" claim, investigated](studio/docs/upstream-20ms-investigation.md)
- [Brake-release detection from the friction circle: measured, not shipped](studio/docs/friction-circle-release-investigation.md)
- [Sideslip rate, wheel hop and a track bump map, probed as channels](studio/docs/measured-channels-2026-09.md)
- [The g-meter: camera-to-kart frame, and the accelerometer against GPS](studio/docs/gmeter-validation.md)
- [The Grip (est) column, re-grounded](studio/docs/grip-regrounding-2026-09.md)
- [Why one recording matched far fewer of its corners on track](studio/docs/corner-match-0060-2026-09.md)
- [Start/finish line verification](studio/docs/start-line-verification.md)

Most were measured on D24 and say so at the top.

## Non-goals

Stating what Pacer deliberately *isn't* is part of the design.

- **Source is the only distribution.** No signed build, no notarization, no `.dmg`, no App Store, no
  Homebrew. `packaging/` can build an unsigned `.app` locally ([docs/PACKAGING.md](docs/PACKAGING.md))
  and CI build-verifies the bundle on every release tag, but nothing is published and nothing is
  planned to be.
- **macOS Apple Silicon only.** No Windows, no Linux.
- **Not a mobile app** — an offline desktop deep-analysis tool, for sitting down with a session
  afterwards.
- **Not a live or in-car system.** No real-time HUD, no OBD-II or CAN. Pacer reads recorded footage.

## Run it from source

A Mac (Apple Silicon) and a GoPro recording. [pixi](https://pixi.sh) manages every external
dependency — `cmake`, `ninja`, `catch2`, and `ffmpeg` for video export — pinned by its lockfile.
The C++ compiler is the one Apple's Xcode command-line tools provide, the same install that
provides `git`.

```bash
git clone --recursive https://github.com/eenndan/pacer && cd pacer
pixi install                                # environment + editable Python bindings
pixi run studio -- /path/to/GX010062.MP4    # build + launch on a recording
pixi run studio -- --demo                   # no footage? a synthetic session, generated not filmed
```

Cloned without `--recursive`? `git submodule update --init --recursive` fetches `3rdparty/`.

Then drag any `.MP4` onto the window, or `File ▸ Open`. Chapter siblings (`GX01…`, `GX02…`) are
chained on request via `--full` or `File ▸ Load full recording`. The
**[first-lap walkthrough](docs/FIRST_LAP.md)** is the 30-second path from footage to "where am I
losing time?"; for a code change, start at [AGENTS.md](AGENTS.md).

**No GoPro footage?** `pixi run studio -- --demo` (above) downloads a synthetic session once and
opens it: generated, not filmed, on a circuit Pacer ships, so its laps open with verified timing.
`pixi run smoke` needs no download: it builds the real app headless on a bundled sample clip and
ends in `SMOKE OK`. That clip holds no complete lap, so it proves the build and the load, not the
analysis.

## Acknowledgements

Pacer began as a fork of [dendi239/pacer](https://github.com/dendi239/pacer) by
Denys Smirnov, whose original C++ core seeded the project. It has since been substantially
rewritten as a clean-room, independent implementation and is now developed independently. Thanks to
Denys for the foundation.

GPS/IMU parsing uses GoPro's [gpmf-parser](https://github.com/gopro/gpmf-parser).

## License

Pacer © 2025-2026 eenndan, licensed under
[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) — see
[LICENSE](LICENSE). NonCommercial use only; derivatives must be shared alike.

This applies to Pacer's own code. Bundled and linked third-party components (GoPro gpmf-parser,
nanobind, Qt/PySide6, FFmpeg, …) keep their own licenses — see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), which a redistributed app must carry.
