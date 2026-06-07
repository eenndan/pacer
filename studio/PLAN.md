# pacer studio — project state & handoff

`studio/` is a local **PySide6 + pyqtgraph** desktop app on the C++ `pacer` core (via its nanobind
bindings), for analysing GoPro race telemetry. This is the handoff doc; for the module map, run
instructions, and per-feature implementation notes see [README.md](README.md), and for the detailed
evidence behind the timing/g-meter/start-line claims see [`docs/`](docs/).

---

## 1. Current state / progress

**Merged and working.** The studio app is the current product on top of the C++ core; the whole
effort is on `main`. Run it with `pixi run studio -- <file.MP4>` (or `python -m studio [files]`).

Validated end-to-end on two real recordings from the Daytona 24h 2026 (Daytona Milton Keynes), each a
multi-chapter 4K HEVC GoPro recording: `GX0060` (~28 min/chapter, 57 valid laps full / 18 chapter-1)
and `GX0062`. Lap timing is **validated unbiased vs a real lap-timing transponder, out of sample, on
both recordings** (see §"Established conclusion").

The four panels — **Map**, **Speed + Δ-to-best charts**, **Lap table**, **Video** — are wired
together with two-way video↔telemetry sync. The headless self-test `python -m studio._smoke` prints
`SMOKE OK`.

---

## 2. Features (introduced)

All shipped and merged. Per-feature implementation notes live in [README.md](README.md).

- **Track map** — best lap (faint) + current/playing lap (highlighted); **freely-draggable** start +
  sector timing lines (re-segment on release); a red **video marker** whose drag is constrained to
  the current lap. GPS dropouts in a lap draw as measured (solid) + reconstructed gap-fill (dashed).
- **Speed + Δ-to-best charts** — speed (top) + lap-vs-best delta (bottom) on **one shared, x-linked
  x-axis** with a **dist/time toggle**; a synced cursor that is also a **draggable scrubber**; a
  **hover dot** on the delta curve; an always-on **Δ/speed readout box** (green ahead / red behind);
  subtle **sector boundary** guide lines. Δ aligned by normalized distance so its endpoint = the
  laptime diff.
- **Charts auto-follow the current lap** during playback (switch to the now-current lap vs best at a
  lap boundary; a manual selection is preserved while paused).
- **Synced video** — GoPro `.mp4` with play/pause, a full-video scrub slider, two-way sync (video ⇄
  map marker ⇄ plot cursors), and an **audio mute/unmute** toggle (default muted).
- **Sortable lap table** (numeric, not lexical) — time / dist / entry speed + per-sector split
  columns once sectors exist; **▶** playing marker, **green** best lap, **blue** selection, **purple**
  per-sector session-best, **⚠** GPS-dropout low-confidence flag (with tooltip); highlights follow
  the laps across a sort.
- **GPS de-noise** — quality gating (drop `fix<3` / `dop>10` GPS9 fixes; GPS5 "unknown" kept) +
  edge-corrected **boxcar smoothing** (`SMOOTH_WINDOW=13`) applied once at load to the source
  lat/lon/alt, so every derived quantity stays consistent.
- **Gap-aware lap distance** — across a GPS dropout the trapezoidal speed integral replaces the
  corner-cutting chord (the C++ `SegmentDistance`).
- **GPS gap reconstruction (map only)** — interior dropouts filled by cross-lap borrow → reference
  centerline → spline, drawn dashed/dimmed; pure-numpy, byte-identical analysis values.
- **Chaptered multi-file sessions** — opt-in (`--full`/`--chaptered` or *File ▸ Load full recording*):
  discover + chain sibling chapters into one continuous global clock; video switches source on a
  cross-chapter seek and auto-advances at end-of-media.
- **GPS9 true-clock lap timing** (default) — times off the GPS9 fixes' true 10.000 Hz wall-clock
  spacing, re-anchored per run to the media clock; sub-sample crossing interpolation in the core.
  **Validated unbiased vs a transponder** (§"Established conclusion").
- **Accelerometer g-meter overlay** — a felt-force friction-circle dial driven by the GoPro's real
  accelerometer (`ACCL`/`GRAV`/`CORI`), with a per-lap max-G envelope and shake filter, composited
  top-right over the video (toggle `G`). Falls back to GPS-derived g if the IMU is absent/unreliable.

---

## 3. Features tried but empirically REJECTED

Captured here so the negative results aren't re-litigated. Evidence in [`docs/`](docs/).

- **GPS9 clock-rate calibration** (factor 0.999514 / −486 ppm) — an **overfit** to GPS-dropout-tail
  skew, *not* a real clock rate; applying it **worsened** the clean-lap RMS out-of-sample on **both**
  recordings (0062 0.053→0.062 s, 0060 0.087→0.092 s). Removed; both true rates are ≈1.0.
  ([docs/gps-accuracy-research.md](docs/gps-accuracy-research.md))
- **GPS + IMU fusion for lap TIMING** — no leverage: the dropouts are **mid-lap, not at the S/F
  crossing**, and the 10 Hz crossing is already sub-sample-interpolated, so dead-reckoning / Kalman /
  EKF can't change the lap times. (The IMU *is* bound — but only for the g-meter display.)
  ([docs/gps-accuracy-research.md](docs/gps-accuracy-research.md))
- **C++ Adam `interpolate_timestamps`** (the notebook's parametric `t2` fit) — **diverges** on
  long/noisy sessions (compresses lap times; broke a lap to ~64 s on 0060). Kept opt-in (`--interp`,
  auto-rejected) with GPS9 the default, since GPS9 carries the true per-fix clock and supersedes it.
  ([docs/upstream-20ms-investigation.md](docs/upstream-20ms-investigation.md))
- **Doppler-aided position smoothing** (Doppler-velocity-pseudo-measurement RTS) — an overfit: best
  on 0062, **worst** on 0060. Rejected on the same out-of-sample principle as the clock-rate factor.
  ([docs/gps-accuracy-research.md](docs/gps-accuracy-research.md))
- **Map-matching to a centerline** — would **erase the lap-to-lap racing-line signal** the app exists
  to visualize, and a centerline carries no per-lap timing. ([docs/gps-accuracy-research.md](docs/gps-accuracy-research.md))
- **Snap-to-trace timing lines** (an early version) — removed in favour of **free** placement (user
  preference). An optional snap *toggle* remains a possible future nicety.

---

## 4. Tech debt / known limitations

- **`mk_centerline.json` reference ICP is broken** — its similarity-ICP fit (in `reference.py`)
  collapses onto an inner sub-loop and covers only ~30% of the real track footprint. It only feeds
  the **rarely-used** gap-fill reference *fallback* (never timing/segmentation), so it's low-impact,
  but it should be re-fit. ([docs/start-line-verification.md](docs/start-line-verification.md) §2 caveat)
- **Pre-existing ruff warnings** in a few `studio/*.py` (`UP037` quoted annotations in `tracks.py` /
  `session.py`, a `B905` zip-without-`strict` and a `B023` loop-binding in `spike_video_sync.py`) —
  cosmetic, unrelated to the analysis path.
- **nanobind shutdown "leaked function" warnings** on the GPS/IMU read callbacks at exit — harmless,
  a codebase convention.
- **G-meter longitudinal per-lap correlation is modest** (lateral r ≈ 0.90 / 96.5% sign agreement is
  the headline; longitudinal r ≈ 0.37 — magnitudes match, but forward g is small and the 10 Hz GPS
  reference is noisy). Expected, not a bug. ([docs/gmeter-validation.md](docs/gmeter-validation.md))
- **G-meter full-scale ring is 1.6 g** (`_FULL_SCALE_G`) — the hardest corners clamp to the rim;
  tunable.
- **Multi-chapter g-meter sync** is verified by wiring (IMU rides the `SequentialGPSSource` chain like
  GPS) but has not been separately live-captured across a seam.
- **GPS-dropout laps are inherently ±noisy** — they are *flagged* low-confidence (⚠), not fixable:
  no positional/gap-bridging method can change a lap time whose dropout is mid-lap.
  ([docs/gps-accuracy-research.md](docs/gps-accuracy-research.md))

---

## 5. Future features (wishlist)

- **More tracks** in `tracks.py` (only Daytona MK today) + **real track auto-detection**.
- **Persist sector / start-line config per file** (a sidecar JSON so edits survive reloads).
- **Fix the MK reference centerline ICP** (re-run `build_reference.py`; tighten the infield).
- **Expose the `_clean` / quality-gate thresholds in the UI** (and an optional snap-to-track toggle).
- **Tune the g-meter full-scale** and verify multi-chapter g-sync live.
- **More pure-Python `session.py` tests** (`_clean`, `valid_lap_ids`, delta-endpoint == laptime-diff,
  `lap_sector_splits` sum == lap-time, `sector_plot_positions`).
- **Perf headroom (only if needed on longer sessions)** — a bulk `lap→numpy` accessor in the C++
  bindings to drop per-point Python loops; `useOpenGL` for the pyqtgraph views.

---

## 6. Upcoming / near-term (most likely next steps)

1. **More tracks + auto-detection** — the user flagged other-track support as the planned next
   expansion.
2. **Persist sector/start-line config per file** (sidecar JSON).
3. **Keyboard shortcuts** (space = play/pause, ←/→ step) and small UX polish.

---

## Architecture an agent MUST respect

- Trace + timing lines live in **local metres** (`cs.local`); `set_coordinate_system` precedes
  `pick_random_start`/`update`. Sectors write-back is wholesale: `laps.sectors = pacer.Sectors(...)`,
  then `laps.update()`.
- **`session.py` is the only module that drives the pacer pipeline; `tracks.py` is the only other
  file that names `pacer` (pure geometry).** Keep `map_view`/`plots_view`/`lap_table`/`video_view`/
  `app`/`gapfill`/`reference`/`gmeter`/`gmeter_overlay`/`chapters`/`transponder` free of `pacer`.
- `pacer` is GPMF/GoPro **`.MP4` only** (the `.dat` reader isn't bound to Python). It supplies the
  telemetry time axis; the app brings its own video player (pacer doesn't decode pixels).
- **Perf invariants — do not regress:** the 30 Hz tick decouple (`_on_position` only stores the time;
  `_tick` applies); plot curves downsampled+clipped, antialias off, autorange frozen after refresh;
  the map draws only best+current lap; clear per-lap caches in `set_timing_lines`; plot-cursor scrub
  seeks coalesced to ≤1 per tick (the drag↔`positionChanged` feedback loop is gated).

## How work is done here

Autonomous background workflows (full-autonomy perms in `.claude/settings.local.json`): each phase
implements → verifies **headlessly** (driving the app via handlers + measuring numbers) → adversarially
reviews → commits. Agents can launch the GUI (non-sandboxed) for a crash-smoke but cannot perceive
smoothness/visuals — the final visual confirmation is the human's. Define numeric pass criteria so a
fix isn't "done" until they hold.

## Established conclusion (timing accuracy)

GPS lap timing is **unbiased and at the ~10 Hz GPS noise floor** — validated **out of sample** against
a real lap-timing transponder on both recordings: clean-lap residual mean **+0.0015 s** (0062) /
**+0.0030 s** (0060), std **0.053 s** / **0.087 s**, each recording's own best-fit clock rate ≈1.0
(−22 / −46 ppm), no calibration factor. The start/finish line is verified correct (on the real pit
straight, crossed once per lap, at the per-lap-noise minimum). The upstream "~20 ms" interpolation
claim is **superseded by GPS9** (which is at 1–3 ms mean bias). Full evidence:
- [docs/gps-accuracy-research.md](docs/gps-accuracy-research.md) — why we're at the floor; rejected techniques.
- [docs/upstream-20ms-investigation.md](docs/upstream-20ms-investigation.md) — the "20 ms" claim + Adam-vs-GPS9.
- [docs/start-line-verification.md](docs/start-line-verification.md) — start/finish line is correct.
- [docs/gmeter-validation.md](docs/gmeter-validation.md) — ACCL→kart-frame transform + GPS cross-check.
