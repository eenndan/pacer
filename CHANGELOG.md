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
  ("median lap · top 3 fixed" — the projected lap), and a **DATA TRUST card** surfacing
  the IMU↔GPS cross-check that was previously stdout-only.
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

- **Saving a recording as a track now updates everything it changes.** `File ▸ Save as track…`
  named the circuit and made the lap timing trusted, but only the trust strip over the map
  noticed. In the same frame the map canvas still painted the amber “drag to set start/finish —
  lap timing provisional” callout, the Laps table still showed the lap in provisional italics with
  its ★ best mark withheld, and the Library row still read “unknown track · provisional” — so the
  lap was silently missing from the PB progression of the track it had just created, until you
  happened to re-open the file. The same stale-library gap applied to a start/finish drag, which
  also left the library quoting the pre-drag lap times. Every one of those surfaces now refreshes
  with the trust flag.

- **Opening a second recording over one already loaded no longer bricks the window.** On any
  recording big enough for the "Loading telemetry…" card to appear (about half a second), that card
  replaced the live view — which destroys it — and the swap back to the newly loaded session then
  crashed on the remains. The window stayed on the card **forever**, with the recording it had just
  finished loading unreachable behind it and nothing but a Cancel button, plus a stale reference
  chip still naming the previous recording's reference. The only way out was to quit. The card now
  releases the view it replaces while that view is still alive, and every teardown step is guarded
  so a half-destroyed pane can never strand the window again.
- **Timing-line edits now show a wait cursor while they work.** Dragging the start/finish line,
  Add sector, Reset sectors and ⌘Z all re-segment the whole session synchronously — measured
  456–518 ms on a 66-lap three-chapter recording — and the window simply froze, with no cursor
  change, no status line and nothing on screen moving. The two gestures that *do* post a notice
  posted it after the freeze it was meant to cover. The cost scales with the session, so the
  recordings where you most want to place a line are the ones where the app went most quiet.
- The **overlay-video export** now obeys the same timing-trust verdict as the lap card. On a
  recording whose start line the app auto-fitted, `Lap card (image)…` and `Copy lap card` were
  correctly greyed out — and `Export overlay video…` beside them rendered a 40 MB clip with an
  unverified lap time burned across every frame and nothing anywhere saying it was an estimate.
  It now warns and asks before rendering (a provisional clip is still useful for reviewing your
  own footage; a silent one is not), naming the way out. `Save as track…` says the same thing in
  its prompt before promoting auto-fitted lines into the reusable track database.
- A recording with **no complete laps can no longer "export"**. `Lap times (CSV)` stayed enabled
  on a session whose own panels read "No complete laps found in this recording", wrote a 76-byte
  header-only file and reported success. All four data exports now switch off with the rest of
  the session, and every gated export action's tooltip states the **reason** it is off and how to
  fix it instead of describing a feature you cannot reach.
- The **HTML report** no longer mixes units on one page. Its embedded chart axis read
  `speed (mph)` and its map colour bar `17 … 54 mph` while the lap table 100 px below was headed
  `entry_kmh` — 13 of 29 columns — with `73.589` for the lap the app and the chart both called
  `45.7`. The report follows the unit you are reading on screen. The CSV exports are unchanged:
  they stay canonical SI (`entry_kmh`, `speed_mps` beside `speed_kmh`, every column
  unit-suffixed), which their tooltips now say.
- The report's **map snapshot no longer bakes in the app's editing chrome** — the video-position
  marker and the start-line drag handles were embedded in the exported document. The explanatory
  "Brake point" / "Corner apex" key stays: unlike a share image, a document has nowhere else to
  explain its own glyphs.
- The **video-export options dialog** puts a number on the trade-off it sells. "High — larger
  file" and "Standard — smaller file" quantified nothing, on a choice that spans ~3× (one 23 s
  lap: 40 MB vs 122 MB); the hint now estimates the size, states the exact frame count and names
  the encoder that will run, and refreshes on **both** menus rather than only Resolution.
- The export dialog's resolution and quality now really are **remembered** — they were window
  state that reset to 1080p/High on every relaunch while every other UI choice persisted.
- **Reveal library in Finder** says whether it worked. It discarded the system handler's result
  and reported nothing either way, while "Back up library…" one row over reported both outcomes.
- **Save as track…** no longer silently overwrites a different circuit that happens to share a
  name. Saving one recording's lines as "My Circuit" and then another recording's, from a track
  **79 km away**, replaced the stored entry in place — start line, sector lines and GPS anchor
  gone, with no confirmation, no undo and a success message byte-identical to a fresh save. A name
  reused for somewhere else is now refused and reported by name and distance; refining the lines
  of the track you are actually at (a built-in included) still saves in one step. The save now
  **asks** rather than refusing outright — the confirm names how far away the circuit it would
  replace is — and the status line afterwards says "replaced", not "saved".
- The **new personal best** card no longer asks you to hit a 20×19 px target against a running
  clock. Its dismiss ✕ (20×19) and "See your progress →" (133×19) both sat under the 24×24
  hit-target floor — on the one card in the app that deletes itself after 6 s — while the
  "Share your PB →" button beside them cleared it at 130×30. Both now stand 24 px, and the
  auto-dismiss **holds while the pointer is on the card**, so a celebration cannot disappear
  from under the click it is asking for.
- A video seek across a chapter boundary no longer renames the chapter banner to a chapter that
  has not loaded. Seeking backwards from chapter 2 relabelled the banner "chapter 1 of 3"
  immediately, with no busy affordance, while the reopen was still in flight — the identical
  reopen reached by playing off the end of a chapter had always shown "loading next chapter…".
  Both routes now show the hint, and both clear it when the destination genuinely presents.
- Compare mode no longer offers to compare a lap with itself. Both lap pickers listed every
  valid lap, so picking the left pane's lap on the right gave two identical pickers, two
  `Δ +0.00 s` badges and a chart overlaying one lap on itself, with nothing anywhere saying so.
  Each picker now drops the lap the other pane holds — in both directions, and again after
  every repoint — and a pair set to one lap twice reads "same lap" rather than a dead-even Δ.
- The compare Δ badges carry the ▲/▼ direction glyph again. They hand-rolled their own format
  and so were the one Δ surface in the app without the non-colour ahead/behind cue: the hero
  readout showed `Δ +1.46 s ▼` beside a badge reading `Δ +1.22 s`. They now route through the
  same formatter as every other Δ.
- The g-meter overlay's "only re-pin on change" guard now actually holds. Its target size was
  computed from an aspect ratio that disagreed with the dial's own minimum height (120×134
  against a 120×140 floor), so the guard was false on every tick and re-issued a `setGeometry`
  Qt clamped straight back — 600 of 600 ticks measured. No rendering change: the overlay
  received no move or resize events from those calls either way.
- The **Ideal lap** toggle no longer lights up and does nothing. With the session best selected
  alone — the state the app opens in — the lower chart is already Δ-to-*ideal*, so the overlay had
  nothing to add and the click changed **0 of 441,077 pixels** while the button latched amber. It
  now goes disabled there, and its tooltip keeps its own description *and* gains the reason.
- The chart's lap curves can be told apart without colour vision. The lap-identity palette is
  deliberately **not** swapped by the colour-blind option (those colours say *which lap*, not who is
  faster), but hue was the only cue, and two of the six collapse under deuteranopia — `#B794F6` vs
  `#7FA8F5` is **CIE76 ΔE 1.27**, half the ~2.3 JND. Each palette slot now also carries a **dash
  pattern**, which pyqtgraph strokes into the legend swatch as well as the curve, so the legend maps
  to a curve with no colour at all. The single-lap default is unchanged: slot 0 stays solid.
- The km/h axis no longer prints a tick through the ESTIMATED brake/throttle strip. The strip gets
  its own reserved space below the speed trace, but the axis kept ticking it: a **`20` km/h label**
  inside a pedal band, on a lap whose true minimum was **28.5 km/h**. Ticks inside the strip are
  suppressed and the strip now names itself on the chart — *brake / throttle (est)*.
- The speed legend moved off the trace. Pinned top-left, it sat exactly where a lap's data is (you
  cross the line flat out) and hid **413 of 2,800 plotted samples (14.8%)** at a six-lap selection;
  anchored to the measured-emptiest corner that is **268 (9.6%)**, and 0.0% on two other fixtures.
  It has always been draggable — now the cursor and a tooltip say so, and the hide threshold sits at
  the largest legend the app can actually produce instead of one row above it.
- The charts empty state says what to do next — *drag the start/finish line on the map* — and the
  three chart controls no longer stay live and latching over a page that cannot draw anything.
- **A GPS fix that teleports no longer inflates the session distance.** The SESSION "distance"
  total summed every chord between consecutive fixes with no sanity check, so one dropped fix —
  177.8 m across 56 ms, an implied **11,500 km/h** — put **2.3 km** on a 9.6-second clip whose own
  speed channel caps the distance at **72 m** (a 31× overstatement). Each chord is now weighed
  against what that same speed channel allows over the same interval, and when too little of the
  trace survives to mean anything the tile shows a dash with the reason rather than a number. On
  real recordings this changes nothing: measured across four sessions on two tracks it kept
  **100.00 / 100.00 / 100.00 / 99.98 %** of the chord length.
- **The g-meter's trust gate can now see a mis-scaled channel.** It judged the accelerometer by
  its *correlation* with the GPS-derived g — and Pearson r is scale-invariant by construction, so
  halving the g channel left the correlation, the verdict and the whole DATA TRUST card
  **byte-identical** while the dial, the peak-g tiles and the friction-circle envelope all halved.
  The verdict now also weighs **magnitude** (the lateral RMS gain, which must sit near ×1), the
  DATA TRUST card states that gain beside the correlation, and a channel that fails falls back to
  the GPS-derived g as it always did for a bad correlation. Measured gains on four real
  recordings: **1.077–1.114**, comfortably inside the band. Where there is too little cornering to
  weigh a magnitude against, the gain is reported but not gated on.
- **The single-lap honesty rule is applied by every tile that describes a distribution.** With one
  clean lap, σ, consistency, trend and race pace correctly dashed while "median − best" printed a
  measured-looking **+0.00 s**, "within 1% of best" printed **1 / 1**, and the median tile read
  "1 clean **laps**". Spread and the within-1% count now carry σ's own minimum-sample gate in the
  data layer, so the tiles can no longer drift apart, and the caption is singular.
- **The friction circle says what it plots.** Both axes were unnamed and unitless — the only
  labels were the ticks `-2.0 / +0.0 / +2.0` — and nothing distinguished the dashed p98 grip-
  envelope ring from the fixed 0.5 g reference rings. The axes are now named and directed
  ("lateral g − right · + left", "longitudinal g − braking · + accelerating"), a one-line key names
  both kinds of ring and carries the envelope's own value, the section header names the unit like
  its peers, and the origin tick is unsigned.
- **The friction circle is the same size on every screen.** Its width came from pyqtgraph's
  `sizeHint`, which moves with the device pixel ratio, so the identical 1440×900 window laid it
  out **440×220 at DPR 1 and 300×220 at DPR 2**. It is now pinned in both axes.
- **The ⌘⇧S dashboard uses more of the window it takes over.** The tile grid was capped at 4
  columns at every width; above a dashboard-width pane the cap now rises to 6 and the friction
  circle grows with it, so the same content occupies fewer, wider rows (data content reaches
  **611 → 742 px** of a 1700 px pane; page height 1063 → 1026 px). The 2–4 column reflow in a
  normal quadrant is unchanged. A related ordering bug is fixed too: the reflow measured the
  scroll viewport from inside its own resize handler, where it still holds the *previous* width,
  so a page sized before it was first shown kept the narrow layout until resized by hand.
- **Esc now restores a maximized panel.** The Shortcuts card and all four ⛶ tooltips promised
  "Esc / click again to restore", but the key was ignored: its handler was gated on the window
  being full screen, which maximizing a panel never makes it. Measured across 3 window sizes ×
  4 panels, Escape moved **0 of 1,296,000 pixels**; it now backs out of whichever "one thing
  fills the frame" state is on — video focus, a maximized panel, then window full screen.
- The welcome screen no longer offers menu items that do nothing. **17 of 25** actions stayed
  enabled with no recording open, and three were completely inert (⌘⇧S Session statistics,
  Coaching ▸ Opportunities…, View ▸ Show excluded laps left the window unchanged, said nothing
  and opened nothing). The Coaching and View menus now grey their session-only items out — and
  do it from the first frame, so ⌘⇧S is inert rather than silently ignored.
- A coaching **Jump** now says where it landed. It opened the Corners tab on a 12-row grid with
  nothing marking the corner you clicked; the matching row is now the current cell, scrolled
  into view (at 1100×620 it was off-screen entirely), and named on the status bar together with
  the lap the table is showing. Jumping also stopped overwriting your saved lap-panel tab — quit
  after a jump and the panel reopens on the page you chose, not on Corners.
- The crash dialog calls the app **Pacer Studio**, the name every other surface uses. It was the
  one place rendering a lower-case "pacer", on the surface that only appears when something has
  already gone wrong (macOS shows no window title on that dialog, so the body is the only naming
  it has).
- The Coaching page now uses the room it is given. Maximized it used to be **3 rows in 808 px**
  — 78% dead canvas — while the ranking behind it held **11** corners; it now shows as many
  ranked corners as the page can hold (still at least the top 3 the headline sums). And every
  column header sits over its own data instead of being centred: "How to find it" floated
  **611 px** away from the sentences it labels.
- At the app's own minimum window the Coaching page no longer starves the one column carrying
  words. The three numeric columns took 198 of the 270 px available and the reason column fell
  back to 100 px, overflowing into a **horizontal scrollbar** with the header hard-clipped to
  "How to find". ±σ now yields first (its value is spelled out in the reason sentence anyway),
  the reason column keeps what it frees (**100 → 128 px**), a shortened header says so with an
  ellipsis, and all four headers — which carried none — explain themselves on hover.
- The **±σ** column said `±0.12` while the sentence in the same row said `σ 0.12 s`. It is
  seconds, and now says so, matching the `+0.13 s` cell beside it.
- The ESTIMATED brake-point hint no longer recommends braking from inside the corner. The
  optimum is constant-decel, straight-line physics, which only holds on the approach — on one
  D24 corner it landed **59 m into a 79.6 m corner window, 19.4 m before the apex**, and the
  cell asked to "Brake ~50 m later" next to its own measured "~0.36 s longer on the brakes". A
  target more than one brake zone past turn-in now shows no metres, and the hints that remain
  name their target against the corner's turn-in instead of a bare lap-odometer reading.
- The CHARTS header no longer leads with a different Δ baseline from the chart underneath it.
  It used to say `Δideal` while the lower chart plotted Δ-to-best, with the one label that
  reconciles them hidden at every window size the app ships at (it needed a ~1633 px window;
  the default is 1440). The bar now spends its width on meaning first: the baseline naming
  survives, and the **Brake/Throttle** and **Ideal lap** toggles fall back to their icon —
  with the full label on hover and in their accessible name — instead of being centre-clipped
  into `Brake/Thrott` and `Ideal la`. The x-axis combo, which had no tooltip at all, keeps its
  full text at every width and now says what it switches.
- The MAP/CHARTS column can no longer be dragged narrower than its own header. At the old
  360 px minimum the header's children overlapped: the hero readout ran past the panel edge and
  the amber "vs ideal" chip painted straight through the live Δ number, while the map's buttons
  clipped at both ends into `ld sect` / `et sec`. **Add sector** and **Reset sectors** also
  gained the tooltips they never had — the destructive one included.
- The ⛶ panel-maximize buttons are 26×24, clearing the 24×24 hit-target floor. They are the only
  always-visible way back from a maximized panel.
- The hero Δideal readout — the largest text in the window — now explains on hover why it cannot
  move on the lap the app opens on: that lap is your best, and the theoretical ideal is stitched
  from its own sections, so the gap is near zero by construction.
- A discarded lap/corner table could raise `AttributeError` out of its Qt event filter during
  teardown (the filter outlives the object's Python attributes), turning an unrelated widget
  construction into a crash.
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
- Stats and Coaching stated **different totals for the same three corners** (0.31 s vs
  0.32 s): Stats summed the raw losses while the Coaching headline sums the 2-dp rows you
  can add up by eye — and the Stats tile then subtracted 0.3134 while printing 0.31. The
  digest now runs the Coaching panel's own arithmetic (its rows, its count, its rounding),
  so the two pages agree and the tile agrees with itself.
- The Stats coaching-digest tile now names its base — **"median lap · top 3 fixed"**. It is
  the median lap rebased (deliberately: best − losses would overclaim, because your best lap
  already banks some of those corners), so it can read slower than the "best lap" tile a row
  above; uncaptioned that looked like a target you had already beaten. Its tooltip says so.
- That tile also stopped painting a "→" it could not honour: it had no click handler, no
  pointing cursor and no focus, so pressing it did nothing. It points at the Coaching tab in
  words instead.
- A short mis-segmented lap can no longer be crowned session best (the lap-distance band
  in the real-lap filter); band-excluded laps are surfaced instead of silently vanishing.
- The Corners page no longer opens on a quarter-table of `+0.00`. It opens on the session
  best — the lap the Δ columns measure *against* — so both Δ columns were the model's
  documented self-zeros rather than measurements. They now read "—", with a caption naming
  the lap ("Lap 42 is the session best — Δ is against itself"). A loaded cross-recording
  reference is the baseline instead, so every local lap keeps its numbers.
- On a recording with **no** complete lap the Corners page asked you to "Select a lap" —
  an instruction that cannot be followed. It now states the fact and the reason, in the
  Laps grid's own words, and both placeholders end on one next action.
- The ⊘ excluded-laps strip scales with the problem. Half a session going missing was
  reported by the same muted one-liner as one stray out-lap: past a fifth of the laps it
  now takes the warning voice, states the share in words, and puts the kept-vs-excluded
  distance comparison on screen instead of only in a tooltip. Its count also reconciles
  ("24 excluded of 49 laps", with any brief sub-lap crossings accounted for) — the panel
  used to show 25 rows and "24 excluded" on a 50-lap recording.
- Expanding that strip lists **every** excluded lap in a height-bounded scroll, instead of
  6 of 24 plus a dead "+18 more" naming laps no surface in the app would show.
- **Lap columns can be sorted from the keyboard.** Sorting was mouse-only: the header could
  not take focus, so no Tab press ever reached it and Space/Return left the indicator where
  it was — with no menu action and no shortcut offering a way in. One Tab out of the grid now
  lands on the header, ←/→ (and Home/End) walk the sortable columns, and Space/Return sorts by
  the focused one, which wears the app's focus ring so you can see what you are about to sort
  by. While it has the keyboard it also takes Space back from the video's play/pause.
- **The ★ says what it means.** The mark for "session best in this context" was drawn with an
  empty tooltip on every cell that carried it, in both tables, and explained in exactly one
  column header. Every ★ cell and every column that can wear one now carries the legend — on
  top of, not instead of, the GPS-dropout or provisional note the cell already had.
- **The Corners rows admit they are clickable.** Clicking one rings that corner on the map and,
  from a maximized lap panel, restores the grid on the way so the map has pixels to paint on —
  and the panel shrinking 5.2× was the first feedback the click produced. The rows now take the
  pointing-hand cursor and fill on hover, and the Corner column's tooltip names the click.
- **The Corners table names its units.** Seven columns of unit-bearing numbers carried no unit
  anywhere on screen (they were in the header tooltips, which is hover-only) while the Laps
  header says "Entry (km/h)" and the Stats page captions the same data. A caption above the grid
  now states times, speeds and grip, and follows the km/h ↔ mph setting.
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
- The PB chart's axis reads lap times, not decimal seconds — it printed "69" / "70.5" under
  "best lap (s)" while the Best lap column 40 px above it read "1:09.905". Both now come from
  the app's one time formatter.
- A library search that matches nothing no longer blanks the dialog. It says which term matched
  none and how to get back, the header counts what is on screen ("0 of 3 analyzed recordings"
  rather than still claiming 3), and the chart drops the de-selected recording's axis range
  instead of leaving its numbers labelling an empty grid.
- The track filter can reach unknown-track recordings. It listed only named circuits, so on a
  typical library — where the registry knows about one track — most rows could not be filtered
  to at all; there is now an "Unknown track" bucket, and the search box matches the label those
  rows actually show.
- A Track cell that is too narrow for its label now hovers to the whole thing (it elides by 31 px
  at the dialog's own minimum width, and its tooltip previously named only the file).
- The library header and the Clear-library confirm say "3 analyzed recordings" / "Forget all 3
  recordings" instead of the "(s)" placeholder plural, matching the summary line below them.
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
- Keyboard focus is visible again. Four of the app's fifteen tab stops painted **nothing** when
  the keyboard arrived — the lap table (0 changed pixels of 213,725), any toggle that happened to
  be **checked** (`:focus` and `:checked` both drew the same 1 px amber border, so all four
  checkable buttons showed a 0-pixel difference), and both plot canvases. They all share one
  focus ring now — 2 px, in the brighter accent the checked state does not use — and it is
  reserved in the resting state, so arriving recolours pixels instead of resizing the control.
  The lap table also gets its current-cell marker back, replacing the dotted rectangle the
  stylesheet had suppressed as illegible.
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
- **The map's "drag to set start/finish — lap timing provisional" callout stays on the
  canvas.** Centred on the start line, it painted its outer half off the panel whenever that
  line sat near an edge — 39.8 px off a 1272 px map, where the only readable words were "o
  set start/finish". The caption now slides its anchor to stay inside the plot, and a line
  with room on both sides is still centred.
- **The map no longer paints a full red→green gradient under two identical labels.** A
  re-segmentation that left a 2-sample segment (43.24 km/h at both ends) produced a complete
  colour ramp legended "43" → "43 km/h". A channel whose two ends round to the same number
  now shows one sentence instead — "speed is 43 km/h for this whole lap — no gradient" —
  the same treatment the Δ channel already gives the best lap.
- **The Elevation channel's legend is stated relative to the lap.** It quoted two absolute
  GPS altitudes to the metre, and GPS altitude drifts: across 21 laps of one recording the
  low end ranged 79.9–83.0 m, a 3.2 m disagreement about the same track against a lap
  profile only 4.5 m tall. The colours were always the *within-lap* shape (min/max normalised
  per lap), so the legend now says exactly that — "lowest" → "+5 m" — and the channel
  dropdown, which never mentioned Elevation at all, explains the caveat.
- **"Reset sectors" says what it did.** Clearing three hand-placed sector lines took one
  click, 59 ms and produced no dialog, no status line and nothing on the map. It stays
  immediate (it is fully ⌘Z-reversible) but now posts a notice over the map naming the count
  and the way back; clearing zero lines says so instead of silently re-segmenting.
- **"Add sector" divides the lap evenly and reports the new split.** Each click bisected only
  what was left, so three clicks gave sectors of 49.9 / 16.8 / 8.6 / 24.7 % of the lap — the
  third carving an 8.6 % sliver. The set is re-spaced as a whole (24.5 / 25.4 / 25.4 / 24.7 %)
  for as long as the lines are still the app's own suggestions; once you drag one, a click
  appends and leaves your placements alone.
- The developer gates hold their own weight again. `pixi run smoke` works in a clean
  checkout: it now sets the same `PYTHONPATH` every Python test already gets, because the
  build drops the compiled module into a `pacer/` directory with no `__init__.py`, so a bare
  `import pacer` resolved to an empty namespace package and the run died inside `Session.load`
  with "module 'pacer' has no attribute 'Laps'". That failure then took ~11 minutes to
  surface — the smoke harness suppressed only the *static* `QMessageBox` helpers, so the load
  error's `box.exec()` on a message-box INSTANCE sat on an undismissable modal until CI killed
  the step; it now exits in ~1 s printing what the dialog said. And the reentrant-load test
  waits for both load workers to report in rather than for a view to appear, so its "a
  superseded result is never applied" assertion can no longer run against a half-settled
  window.

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
