# Changelog

All notable changes to Pacer are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Everything merged since v0.1.0 (~100 PRs), grouped by theme.

### Added

- **Session Statistics page** — a third page on the lap panel's **Laps | Corners | Stats**
  toggle (⌘⇧S or View ▸ Session statistics opens it as a full-window dashboard): session
  totals (time on track, moving time, distance, wall-clock window), the pace distribution
  (best / median / σ / race pace / consistency rating / laps-within-1% / a robust
  Theil–Sen trend), top-speed & peak-g tiles, a **g-g friction circle** with the
  demonstrated-envelope ring, brake/coast reductions, per-sector best/median/σ, a per-lap
  channel table, a sortable **corner-by-corner report** (worst corners tinted; rows ring
  the corner on the map), the **entry/apex/exit phase-loss** headline ("where the corner
  time goes"), **braking repeatability + commitment** per corner, the **straight-line
  report** with trap speeds and the exit-leverage **FIX FIRST** tile, a coaching digest
  ("fix your top 3 → projected lap"), and a **DATA TRUST card** surfacing the IMU↔GPS
  cross-check that was previously stdout-only.
- **Coaching front door** — the persistent top-3 opportunities panel under the lap table,
  the Δ-to-ideal hero readout, corner time-loss attribution (entry/apex/exit thirds), the
  braking-point optimizer ("you can brake ~N m later"), a synthetic brake/throttle band
  under the speed chart, the continuous ideal-lap delta, and grip-utilization map
  colouring.
- **Trust & honesty surfaces** — provisional-timing chrome for unverified start lines, the
  data-quality signal (media-clock fallback / dropped-fix fraction), honest ESTIMATED
  labelling on derived channels, colour-blind-safe cues, the "new personal best!" moment,
  and 1-based lap numbers everywhere.
- **Sharing** — the one-tap shareable lap-card image (with an honesty gate) and clean map
  exports.
- **Library v2** — trustworthy PB history per track, search, schema-bump backups,
  last-folder memory, and a guard against merging unrelated dropped recordings.
- **Tracks** — the track database (auto-detect + File ▸ Save as track), geometry
  track-match for unknown tracks, the peak-speed start-line heuristic, opt-in
  hill-compensated brake coaching, and an Elevation map channel.
- **Layout** — per-panel maximize (⛶ / double-click a header), window full screen (⌘⌃F),
  the fullscreen-video gesture, the calm-default left column (collapsible coaching +
  View-menu hide toggles), the ⊘ excluded-laps strip, and the welcome drop-zone with
  drag-and-drop import.
- **Speed units** toggle (km/h ↔ mph), persisted preferences, and an undo for start-line
  edits + a privacy disclosure / forget-recording flow.
- **Packaging & infra** — macOS .app/.dmg packaging (PyInstaller) with a build-only CI
  smoke, off-thread cancellable session load, bulk IMU bindings, the synthetic
  golden-equivalence CI gate, and the branded app icon + QApplication identity.

### Changed

- **The lap panel is a real tabbed panel** — one native tab bar (**Laps · Corners · Stats ·
  Coaching**, digits 1–4), every page at the panel's full height. The under-table coaching +
  consistency strips (and their height caps, collapse chevrons and View-menu hide toggles)
  are gone: coaching is a full tab, the consistency content lives in Stats (trend sparkline +
  the corner σ/tint columns). The active tab and any grid-splitter drag now persist across
  reloads and relaunches.
- `Session` decomposed into injected services (`Bests`, `CornerModel`, `DrivingChannels`,
  `Timeline`, `SessionStats`, the map render cache); `app.py` slimmed via extracted
  workers/overlays; compare-mode ownership unified under `CompareController`.
- Docs pivoted to the portfolio/craft showcase: the moat-first README, the GitHub Pages
  landing, `docs/ACCURACY.md` (the transponder-validation story), `AGENTS.md` as the
  single agent front door, and `studio/README.md` as a full module map.
- Contributor-governance boilerplate and dead ImGui-era config removed (the solo-dev +
  agents posture).

### Fixed

- The Stats page no longer presents PROVISIONAL lap statistics as verified. The full-window
  dashboard hides the map — and with it the app's only "Lap timing is unverified" banner — so
  the page now carries its own, and the PER LAP **Time** column mutes exactly like the Laps
  tab (with no ★ best against an auto-fitted start line). The measured speed/g tiles and
  columns keep full authority: only the numbers the start line actually moves are demoted.
- The **DATA TRUST** card now names the session's own trust problems — an unconfirmed
  start/finish line, an unknown track, and how many laps are ⊘ excluded from every statistic
  on the page — instead of printing provenance that read identically on a clean session. It
  also moved directly under SESSION: at the foot of the page it sat below the fold of even a
  1728×1117 dashboard.
- DATA TRUST said *"0% of fixes rejected"* on recordings where the loader rejected hundreds.
  The figure is deliberately measured over the retained **moving** trace, so the card now says
  so ("N% of moving fixes rejected", with the basis in its tooltip); the number is unchanged.
- With no accelerometer the trust card went silent about the g channel exactly when it was
  missing. It now states the absence, and repeats it beside the em-dashed SPEED · G tiles.
- The 0-lap Stats page was 15 em-dashes with no on-page reason. The dash-only groups are
  replaced by one block carrying the explanation and the next action.
- A short mis-segmented lap can no longer be crowned session best (the lap-distance band
  in the real-lap filter); band-excluded laps are surfaced instead of silently vanishing.
- False "GPS quality low" on clean recordings (the dropped-fix denominator counted the
  trimmed stationary lead-in); clock-aware degraded-timing copy.
- `speed_long_g` run-seam NaN that silently dropped brake/coast events; the brake-onset
  seam blip.
- Library wipe on a schema bump (now backs up + reveals); undo/forget seam bugs.
- Unknown-track recordings are no longer quarantined in the Library: a recording with valid
  laps is listed, selectable, openable and offered in Open Recent whether or not its circuit
  is in your track database — it just carries the usual "provisional" tag instead of a
  bogus "(no laps)" beside its own best lap.
- The Library names the recording FILE: every row hovers to its filename + full path, and
  the "forget this recording" confirm leads with it, so two sessions from the same day on
  the same unknown track are told apart before a sidecar is deleted.
- The PB chart's explanatory empty-state sentence is actually visible (it was positioned in
  data coordinates on a pixel-space item, ~1.8e9 px off-screen) and stays centred on resize.
- The library's progress line no longer counts your FIRST session on a track as a personal
  best ("1 session · 1 PB"); PBs now count only sessions that beat the running best.
- Share-card overflow, leaked chrome in map grabs, plot-overlay and corner-label
  collisions, and self-contradicting coaching copy.
- Demo-download UI freeze (socket timeout); single-flight loads + a GIL-friendly worker
  drain (CI deadlocks); the drift-gated per-corner loss alignment.
- The colour-blind-safe option now actually reaches the charts and the map ramp. The
  best-lap curve was pinned to the default green by a module constant, so it disagreed
  with the lap table's recoloured cue; it resolves the palette at draw time now (as do
  the brake-point glyphs and the ideal-lap star). And the map's speed ramp no longer goes
  flat over its lower half in that palette — it shared the amber mid anchor with the
  colour-blind "behind" orange, which made half the ramp indistinguishable (adjacent
  buckets stepped 0.90–1.16 in deuteranopia-simulated CIE76 ΔE, worse than leaving the
  option off); the palette has its own mid anchor now, with a minimum step of 7.01.
- Text contrast: four roles that borrowed the disabled-chrome grey (the empty-state body,
  the welcome subtitle and error, and placeholder text) were below WCAG AA at 3.17–3.68:1
  and now clear it at 5.90–9.35:1.
- A failed load no longer whispers: the message was the exact colour of the marketing
  subtitle one pixel smaller — on the "Open demo" path, the only response to the click.
  It now reads in the warning amber at body size with the ⚠ glyph.
- `Δ -0.00` is gone. Float noise inside the even dead band printed a negative zero, which
  reads as "behind" on a level lap — and it was burned into 14.3 % of the frames of an
  exported overlay video, where the recipient cannot correct it.
- **Lap and Corners tables now fit their panel.** Content-tight columns left a maximized
  Laps panel 79% empty (382 px of data, 1050 px parked in a blank spacer) and, at the
  default quadrant, pushed the Corners table's "Grip (est)" column and the sector columns
  the map's own "Add sector" button creates clean off the viewport. Spare width is now
  shared across the data columns (capped), a short panel gives the slack back down to each
  column's own values, headers elide to a tooltip instead of centre-clipping, and every lap
  and corner header carries its full text on hover.
- **Provisional timing demotes every start-line-derived column**, not the lap Time alone —
  Dist (the distance between crossings) and Entry (the speed at one) move with the start
  line too, and used to render at full confidence beside a greyed-out time. A degraded
  *clock* still demotes only the durations.
- **The pre-selected best lap is scrolled into view.** The app opens on your best lap and
  draws four panels from it, but the row itself sat below the viewport at every window
  size, so the lap table read as "nothing selected".
- **Video transport chrome** — the compare panes' Δ badge no longer paints on top of the
  lap picker (it overlapped the lap time by 67 px at the default window size); the strip
  now budgets its width across the role caption, the lap picker and the Δ. The scrub bar
  moved to its own full-width row with a 24 px handle, and its lap ruler decimates to
  every 2nd/5th/10th... lap instead of collapsing into a 4 px hatch on a long session, and
  its tooltip now says the ticks are lap boundaries. The ⤢ fullscreen-video button no
  longer latches ON when compare refuses the gesture (it is disabled there, with a reason),
  gained the **F** shortcut — its first keyboard route anywhere — and the compare toggle
  gained a visible "Compare" label, the app's first visible use of the word.
- The **g-meter dial burned into an exported video** now carries the provenance tag the
  exporter always set ("IMU lat · GPS long") — it was snapshotted and never painted, so a
  shared clip showed four bare numbers with nothing saying where they came from. The dial
  also states its unit (labelled 0.5 g / 1.0 g rings) and names all four cardinal peaks
  (BRAKE / ACCEL / TURN R / TURN L, in the dial's felt-force convention), on screen and in
  the export alike; the live dial's source tag has its own reserved band so it can no
  longer overprint the bottom peak number, and reads at 5.2:1 instead of 2.2:1.
- The track map is no longer a one-way door: a scroll-wheel zoom or a drag left it at an
  arbitrary view — up to fully blank — that nothing in the app could undo. A **Fit** button
  now appears over the map the moment the view is moved (double-clicking the canvas does the
  same), and the map re-frames itself after a redraw or a panel resize.
- The map now draws the **whole recording's trace** as a faint layer under the two lap
  overlays, so a recording with no complete laps finally has a track to drag the
  start/finish line onto — the exact state where the app asks you to do that — and the view
  is framed on the trace and the lap overlays together, which also keeps the draggable video
  marker on canvas when the complete laps cover only part of the drive.
- One drag of a grid splitter could **delete a whole column** (past ~740 px the panels
  collapsed to 0 px instead of clamping at their stated minimum), and the deletion was
  persisted, so every relaunch reopened with the map and charts — or the video — gone,
  recoverable only through the 8 px handle left against the window edge. Drags now clamp;
  a prefs file already holding a deleted panel falls back to the default layout. Maximizing
  a panel still fills the window.
- A start/finish drag that left **no complete lap** was saved to the recording's sidecar,
  replacing the last placement that worked with one the loader always rejects — so the
  recording reopened as provisional with a saved line nothing on screen could show or
  clear. It was also pushed onto the undo stack, where the same rejection made **⌘Z a
  permanent no-op**. Neither store records an unsegmentable placement now: the edit still
  applies on screen, ⌘Z reverts it, and quitting reopens on the last placement that worked.
- The Corners table's four speed tooltips said km/h over cells holding mph (wrong by
  1.61×) whenever mph was the remembered preference rather than a change made in-session.
- **A failed reload no longer strands the window on an endless "Loading telemetry…" card** —
  the loaded session's UI is handed back before the error dialog claims it is unchanged.
  Relatedly, a reload no longer blanks a working session at all unless the load runs past
  400 ms, and the dialog's reassurance is now stated only where it is true.
- **Load errors name the actual problem and a next step** — a folder, a missing path, a
  0-byte file, an unreadable file, a truncated *real* GoPro chapter and a non-GoPro file
  each get their own message (a truncated GoPro chapter used to read "this doesn't look
  like a GoPro recording").
- **The multi-recording drop warning survives the load it starts** and stays discoverable
  for the session, instead of being overwritten mid-load; a multi-recording drop now opens
  the recording that was dropped first rather than the alphabetically first.
- **The "start/finish line was auto-fitted — drag it into place" status line retracts when
  you place the line**, re-decided from the same seam that rebuilds the derived views (it
  used to survive byte-identical across the very drag that answered it).
- **"Open demo" no longer freezes the window while it fetches.** Resolving the demo clip
  falls through to a download, and it ran inline in the button's own slot: the welcome
  screen stayed painted, the button stayed enabled, and not one timer tick was delivered
  for the whole fetch — nothing on screen distinguished "working" from "ignored your
  click". It runs on a worker thread now; the button says it is fetching and stops taking
  clicks, and the loading card comes up if the fetch outruns the same 400 ms grace period
  a reload gets.
- **The loading card has a Cancel.** The app's longest routine wait carried zero controls
  while its own video export has offered a Cancel all along. Cancelling hands your open
  session straight back (or the welcome screen, on a first load) and drops the in-flight
  load's result.
- **Dropping a FOLDER of GoPro chapters works.** A camera hands you a folder and the
  welcome screen invites "a GoPro recording", but a dropped folder was a total no-op — the
  drag was never even accepted. A dropped folder is now expanded to the .MP4 files inside
  it. (A folder holding no recordings is still refused, which is the correct answer.)
- **One sentence for "no complete laps".** The status bar authored a fourth phrasing of the
  fact and restated the lap table's reason almost verbatim, so a 0-lap recording said the
  same thing four ways in one frame. The wording is single-sourced now; the bar states the
  headline and leaves the reason and the "drag the start/finish line" next action to the
  panels that have room for them.
- Coaching's "~N s longer on the brakes" no longer counts whole brake events by their onset
  alone: the time each application spends *inside* the corner window is integrated on the
  lap's own clock, so a brake that begins a few metres before the window is no longer scored
  as zero. On the D24 session the C3 advice drops from "~0.90 s" to "~0.67 s" against a
  corner losing 0.109 s.
- Every ranked corner in the Opportunities list now carries a measured reason; rows below
  the top three used to print "find time here" because they were never analysed.
- Sector splits that meant a different stretch of track on different rows. A sector line
  dropped within the collapse tolerance of another used to be fused **per lap**, so in a
  ~2 m band some laps kept the pair and some fused it — one lap's S2 was a 17 s sector
  beside another's 0.2 s sliver, the purple session best could land on a sliver, and the
  theoretical best was summed from pieces that never tiled a lap. The decision is now made
  once for the whole session, so every lap reports the same boundaries and every S column
  is the same stretch of track on every row.
- The Coaching tab now states its scope. Its numbers are the whole session's (a median
  over your clean laps), but its sibling Corners tab re-scopes to the lap you select and
  both tooltips promised the same "vs the best lap" — so the coaching headline read as the
  selected lap's number and understated it. The headline and both tab tooltips now name
  which question each page answers.
- A Coaching row no longer drops its "(est)" braking line at the default window size: row
  heights were measured at a width 16 px wider than the one the cell is painted into, so a
  sentence that fit the measurement wrapped past the paint and lost a whole line.
- A corner whose typical lap is *faster* than best through all three thirds now shows it —
  the entry/apex/exit bar painted those thirds at 1.19:1 against the row, so the only cue
  reconciling them with the row's cross-lap "time lost" was a tooltip. Faster thirds take
  the ahead colour, segments are sized by |Δt|, and the window's net is on the row face.
- **Help ▸ Keyboard shortcuts** no longer slices a wrapped row in half (the LAYOUT row's
  second line painted outside its row and over the row below); ⌘O has a row at last, and
  every accelerator's glyphs now come from its own `QKeySequence`, so the card reads what
  the menu bar paints (⇧⌘S, not ⌘⇧S) instead of hand-typed text that could drift.
- **Help ▸ Your data & privacy** now discloses all four stores, not two: the preferences
  file (`prefs.json`, which holds the last folder you opened) and the saved-track database
  (`tracks.json`, which holds GPS coordinates) were undisclosed and unreachable by either
  documented removal route.
- **About / Your data & privacy** can no longer be shrunk below their own copy — both cards
  refuse the shrink and scroll if the text ever outgrows the display.

## [0.1.0] — 2026-06-22

First public release: a local desktop race-telemetry studio that turns a single GoPro
recording into a full telemetry workstation — no transponder, no extra hardware.

### Added

- **True-clock lap & sector timing** from the GoPro GPS9 stream on the camera's own clock,
  validated unbiased against a real transponder.
- **Speed-coloured track map** with auto-detected corners, brake points, and draggable
  start/sector lines for re-segmentation.
- **Distance-aligned Δ-to-best charts** (speed + cumulative time delta) so corners line up
  across laps.
- **Lap table** with sortable columns, per-sector splits, session bests (purple cells),
  theoretical-best and best-rolling footer rows, and GPS-dropout flags.
- **Synced GoPro video** — scrub the lap and the footage follows; **side-by-side two-lap
  compare**, including the best lap of *another* recording of the same track.
- **G-meter** overlay driven by the camera IMU (per-chapter camera→kart Procrustes fit, with
  an automatic fallback to a GPS-derived signal for helmet cams).
- **Driving channels** — brake, coasting, and grip derived from the trace (brake/coast on the
  GPS speed-derivative; lateral grip from the IMU).
- **Corner coaching** — the top time-loss opportunities with a measured reason and a one-click
  jump to that corner on the best lap.
- **Consistency analysis** — per-corner σ × time-loss ranking over the clean laps.
- **Exports** — burned-in telemetry video overlay (via ffmpeg) and per-lap channel CSV.
- **Session library** — a local index of analysed recordings with per-track PB progression.

### Engineering

- C++ core (GPMF ingest, geometry, GPS9 lap/sector segmentation) exposed to a PySide6 +
  pyqtgraph app via nanobind; reproducible builds via pixi.
- GPS-dropout laps are excluded from the headline best lap, the Δ-baseline, and session-best
  splits (their reconstructed distance / timing is less reliable), while still shown ⚠ in the
  table.
- Crash-safety guards for degenerate input: a co-located reference pair no longer produces a
  NaN start line, and non-finite GPS coordinates are dropped at the quality gate.

[Unreleased]: https://github.com/eenndan/pacer/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/eenndan/pacer/releases/tag/v0.1.0
