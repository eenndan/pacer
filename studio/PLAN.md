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
together with two-way video↔telemetry sync. The headless self-test `python -m studio.dev._smoke` prints
`SMOKE OK`.

---

## 2. Features (introduced)

All shipped and merged. Per-feature implementation notes live in [README.md](README.md).

- **Track map** — best lap (faint) + current/playing lap (highlighted); **freely-draggable** start +
  sector timing lines (re-segment on release); a red **video marker** whose drag is constrained to
  the current lap. GPS dropouts in a lap draw as measured (solid) + reconstructed gap-fill (dashed).
- **Rainbow track map** — a map-header toggle (OFF → Speed → Δ-vs-best) paints the current lap's
  line as a colour gradient (red = slow/losing → green = fast/gaining; 16-bucket polylines, theme
  ramp, slim min/max legend). Δ reuses the existing 400-grid delta resampled onto the lap's points;
  the buckets rebuild only on lap/channel change or re-segment (never on the 30 Hz tick) and OFF
  restores the exact normal rendering.
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
- **Theoretical best + best rolling lap** — two footer rows under the lap table: the exact sum of
  the purple session-best splits (== best lap time before any sectors exist) and the fastest
  start-anywhere full loop (same-spatial-point windows across consecutive laps; ⚠-dropout
  straddles excluded; complete laps always count, so rolling ≤ best). Styled like the purple
  bests, outside the sortable rows, live across sorts and re-segmentation.
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
- **Compare videos (dual-lap side-by-side)** — a toggle shows two equal video panes playing "time
  into lap" from S/F at 1× (the faster pulls ahead); the primary (left) pane keeps driving all
  telemetry, the secondary is video-only; per-pane lap pickers + "Δ vs other" badges; every repoint
  re-aligns both panes at the start line.
- **Corner model + per-corner analysis** (`corners.py`, pure numpy — NOT map-matching: everything
  runs on our own trace) — corners detected from the **median curvature profile** of the session's
  clean laps with a threshold **derived from the track's own κ distribution** (log-domain Otsu);
  corner windows live in best-lap normalized-distance space and **partition** every lap with the
  complementary straights (Σ segment Δ == lap Δ exactly). UI: **C1…Cn labels at the apexes on the
  map** (direction-coloured dots) + a **"Corners" toggle** on the lap-table panel — rows = corners
  for the selected lap: time-in-corner, Δ vs best, apex (min) speed + Δ, entry/exit speeds, with
  the per-corner **session best in purple**. Cross-recording stability verified on 0060 vs 0062:
  same 12 corners, apexes within 3.5 m.
- **Dark "Refined Minimal" theme** (`theme.py`) — single-source design tokens + dark `QPalette` +
  global QSS, Inter fonts, Phosphor icon buttons (`qtawesome`); charts, table, map and video chrome
  all adopt the dark surface.

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
- **C++ Adam timestamp interpolation** (the upstream notebook's parametric `t2` fit) — **diverges** on
  long/noisy sessions (compresses lap times; broke a lap to ~64 s on 0060). **Removed** — GPS9 carries
  the true per-fix clock and supersedes it; the C++ `pacer/interpolation` module, its bindings, and the
  `--interp` plumbing are gone. ([docs/upstream-20ms-investigation.md](docs/upstream-20ms-investigation.md))
- **Doppler-aided position smoothing** (Doppler-velocity-pseudo-measurement RTS) — an overfit: best
  on 0062, **worst** on 0060. Rejected on the same out-of-sample principle as the clock-rate factor.
  ([docs/gps-accuracy-research.md](docs/gps-accuracy-research.md))
- **Map-matching to a centerline** — would **erase the lap-to-lap racing-line signal** the app exists
  to visualize, and a centerline carries no per-lap timing. ([docs/gps-accuracy-research.md](docs/gps-accuracy-research.md))
- **Snap-to-trace timing lines** (an early version) — removed in favour of **free** placement (user
  preference). Only snap-as-DEFAULT stays rejected; the sanctioned *optional* form shipped as the
  opt-in **Snap** toggle in the map header (default off — free placement is still the default).

---

## 4. Tech debt / known limitations

- **`mk_centerline.json` reference fit — FIXED** (was: free-scale ICP against the unordered point
  cloud collapsing onto an inner sub-loop, ~30% footprint coverage / RMS ≈ 47 m on session 0060).
  `reference.py` now fits by cyclic arc-length correspondence against the session's best clean
  lap, and the stored polyline — a hand trace that turned out to be a poor rendition of the
  layout — was rebuilt from a measured best-lap loop (`dev/build_reference.py`). Cross-session
  (built from 0062, fit on 0060): **RMS 2.8 m, 100% of best-lap points within 10 m**. Still feeds
  only the **rarely-used** gap-fill reference *fallback* (never timing/segmentation).
  ([docs/start-line-verification.md](docs/start-line-verification.md) §2 note)
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
- **Fix the MK reference centerline ICP** (re-run `studio/dev/build_reference.py`; tighten the infield).
- **Expose the `_clean` / quality-gate thresholds in the UI** (the optional snap-to-track toggle
  half of this wish shipped — the Snap button in the map header).
- **Tune the g-meter full-scale** and verify multi-chapter g-sync live.
- **More pure-Python `session.py`/`load.py` tests** (`load._clean`, `valid_lap_ids`,
  delta-endpoint == laptime-diff, `lap_sector_splits` sum == lap-time, `sector_plot_positions`).
- **Perf headroom (only if needed on longer sessions)** — `useOpenGL` for the pyqtgraph views:
  evaluated and deliberately NOT adopted; revisit only with a measured >33 ms/tick paint time.
  (The bulk `lap→numpy` accessor — `Laps::LapColumns`, bound as `lap_columns` — has shipped,
  as has its full-trace sibling `Laps::TrackColumns` / `track_columns`.)

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
- **`session.py`, `load.py`, `tracks.py`, and `ingest.py` are the only modules that may touch the
  `pacer` bindings.** `session.py` owns the loaded session (lap/delta/sector accessors + timing-line
  write-back); `load.py` is the load pipeline behind `Session.load` (GPS9 true-clock time axis,
  trace clean/smooth, segmentation + start-line fit); `tracks.py` is pure geometry; `ingest.py` is
  the GoPro/GPMF data-loading layer (the `SequentialGPSSource` chain build + the raw GPS/IMU stream
  readers). **Every other studio module stays pacer-free** — the views
  (`map_view`/`plots_view`/`lap_table`/`video_view`/`player_pane`/`gmeter_overlay`/`app`), the
  controllers (`scrub_controller`/`compare_controller`), and the pure helpers
  (`gapfill`/`reference`/`gmeter`/`chapters`/`theme`/`_signal`/`transponder`).
- `pacer` is GPMF/GoPro **`.MP4` only**. It supplies the
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
fix isn't "done" until they hold. CI ([.github/workflows/ci.yml](../.github/workflows/ci.yml))
gates every push/PR: pixi build + ctest + ruff on macos-14 (arm64).

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
