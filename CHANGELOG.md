# Changelog

All notable changes to Pacer are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Everything merged since v0.1.0 (~100 PRs), grouped by theme.

### Added

- **A control vocabulary in `studio/widgets.py` + `studio/theme.py`** (mostly developer-facing) —
  one way to build each of the three things the app clicks on, replacing nine hand-rolled copies.
  `icon_button()` is the single square glyph button (it replaces two undeclared size families,
  26x24 with a 15 px glyph and 32x30 with an 18 px glyph, *neither of which painted what it said*:
  a stylesheet `min-height` on a blanket selector REPLACES a widget's own minimum, so the four ⛶
  panel buttons stood at 26x28 and the five video-transport buttons at 32x28). `ToggleButton`
  is the single checkable control — the "setCheckable + recolour the glyph in a `toggled` handler"
  pattern appeared seven times in four files, and six of the seven disagreed with the others about
  the height an iconed toggle ends up at, whether the OFF glyph is tinted or left at the icon
  helper's default, or whether the ON colour is a token or a palette ACCESSOR resolved at paint
  time. `chip()` + the new `[role="Chip"]` rule are the single pill. New theme roles retire the
  fourteen labels that each spelled `color: <text_dim>` out for themselves: `Note`, `Hint`,
  `Title`, `Tagline` and `KeyCap`, plus rules for the four `objectName`s that had a name and no
  rule at all (`LoadingCancel`, `LapExcludedStrip`, `LapExcludedList`, `PBToastShare`) and for the
  map's action notice and the video scrub bar, which were being styled from strings inside their
  view files.
- **`tests/test_inline_styles.py`** — the control half of the guard, beside the colour
  (`test_contrast.py`) and dimensional (`test_design_system.py`) ones: inline `setStyleSheet` sites
  outside `theme.py` are down from 34 to 7 and every survivor is named in prose as a PER-DATUM
  colour a stylesheet cannot express; no new bare `color:` may creep back (and an exempt merge must
  write a qualified selector, or be a leaf label, because an unqualified `color:` cascades to a
  widget's children); every `objectName` and `role` really has a rule; all eight icon buttons are
  one `theme.ICON_BTN` with one `theme.ICON_PX` glyph, measured on the real view at both shipped
  window sizes; and `setCheckable(True)` on a button belongs to `ToggleButton` alone.

- **A spatial design system in `studio/theme.py`** (developer-facing plumbing) — the dimensional
  half of the token set the colours already had: a 4 px spacing scale with one 2 px sub-step
  (`SPACE_XXS`…`SPACE_3XL`), three radii by role (`RADIUS_S/M/L` — controls, cards, large
  surfaces), declared sizes (`CTRL_H`, `ICON_BTN`, `PANEL_HDR_H`, `TOOLBAR_H`, `HIT_MIN`,
  `BORDER_PX`, plus the pre-existing `SPLITTER_HANDLE_PX` / `FOCUS_RING_PX` folded in), and two
  helpers that DERIVE the awkward numbers instead of nudging them — `ctrl_content_h` (a QSS
  `min-height` is a content box, so a control that must paint at `CTRL_H` computes what to declare)
  and `focus_pad` (the padding a `:focus` rule gives back so the thicker ring cannot move the outer
  box). The theme's own stylesheet is migrated onto the scale: 21 distinct px values → 16, 7 border
  radii → 3, 17 padding pairs → 7, and the two hand-computed `5px 11px` / `4px 9px` focus paddings
  are now generated. The type scale gains a fourth defined step (`EMPHASIS` 15, promoted from
  `stats_panel.TILE_VALUE_PT`) and `CAPTION` moves 12 → 11, so the four sizes are 11 / 13 / 15 / 22
  instead of three sitting within two pixels of each other.
- **`tests/test_design_system.py`** — the dimensional guard beside the colour one: every
  padding / margin / border-radius / min-height in the theme's stylesheet must be a token or a
  stated derivation of tokens (a `min-height` is checked by reconstructing the outer box it
  actually paints); an AST walk over `studio/` for hand-picked `setContentsMargins` / `setSpacing`
  / `setFixedHeight` / `setFixedSize` values, labelled by the class and method that owns each, with
  the not-yet-migrated view surfaces in a prose-justified exemption list; and a live check that a
  real button, combo box and tab bar all paint at `CTRL_H`.

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

- **Every surface in the app is on the spacing scale** (mostly internal). The dimensional guard
  shipped with a migration backlog of eight exempt surfaces; it is now empty, and the exemption set
  is pinned at zero so the next off-scale literal has to be argued for rather than excused. The last
  eight were the Help/About/Privacy cards, the export-options dialog, the loading card, the
  excluded-lap strip and the coaching phase bar, all excused together as "prose surfaces with their
  own typographic measure … off the scale and off it *consistently*". They were not consistent —
  20/18/20/16, 12/10/12/12 and 16/14/16/14 for the same job — so instead of a second scale each
  surface now states which kind it is: the two copy cards take the reading inset `SPACE_XL`, the
  Shortcuts reference and the export dialog take control spacing because they are a table and a
  form, and the loading card takes `SPACE_L` between its three groups because it is glanced at and
  clicked, not read. Visibly, the copy cards gain a little air (the About card is 22 px taller, the
  privacy card 52) and the excluded-lap strip loses 6 px, giving those pixels back to the lap grid.
  The guard also now watches `setHorizontalSpacing` / `setVerticalSpacing`, which it had never
  looked at — that is how the Shortcuts card kept a 6 px row gap through a phase about gaps.
- **DATA TRUST is a list of facts, not a paragraph.** The card was a single word-wrapping label
  holding up to seven `·`-separated sentences joined by newlines — the densest block on a page
  otherwise made entirely of values-with-names, and the only thing on it you had to read rather
  than scan. Each fact is now its own row: a dim term on the left ("Timing", "g-meter", "IMU↔GPS
  cross-check") and its value on the right, wrapping inside the pane so nothing can be cut again.
  The trust-BREAKING facts — an unconfirmed start line, an unknown track, laps left out of every
  statistic, in-lap GPS dropouts — lead the card and are marked ⚠, so it can no longer read the
  same on a session where three of them are wrong and one where none are. Every sentence is the
  one that shipped; nothing moved into a tooltip, and in particular the IMU↔GPS lateral GAIN is
  still stated on the surface.
- **The Stats page's spacing is on the design scale.** The tile grid's 18 px columns and 1 px
  value-to-caption gap, and the page's 6 px block gap, were the last off-scale dimensions on this
  surface; they are now `SPACE_L`, `SPACE_XXS` and `SPACE_XS`, and the stat tile itself has moved
  into `studio/widgets.py` as `Tile` so the Library and Coaching pages can use the same object.

- **Visible control changes from the control-vocabulary pass.** Icon buttons are one 28x28 size
  with a 16 px glyph, so the four ⛶ panel buttons grew 2 px wider and the five video-transport
  buttons lost 4 px of width and 2 px of glyph — the transport row now agrees with the "Compare"
  button beside it, which the old fixed height had been standing 30 px tall against every other
  control's 28. The charts toolbar's "vs ideal" is a real amber CHIP rather than a plain button
  borrowing the generic checked state, so it reads as the reference the hero number is measured
  against rather than as a third overlay switch. The About card's name and the privacy card's
  heading share one `Title` style at the type scale's 22 px (the privacy heading was an 18 px step
  no scale declared). The PB toast's "Share your PB →" now stands at the same 24 px hit floor as
  the two buttons beside it instead of 4 px taller. The export dialog's size/quality hint ranks
  below the description by SIZE rather than by `C.text_muted`, the 3.17:1 token the colour contract
  reserves for disabled chrome.

- **Every panel now wears the same header, and its controls have a row of their own.** The four
  panels used to stand at four different heights — nothing declared one, so each header came out as
  tall as whichever control it happened to hold. They are now one declared height, with the panel's
  name (or its tabs) on the left, its live readout beside it and the ⛶ maximize button on the right,
  in all four. The map's line/snap/sector controls and the charts' toggles moved to a toolbar
  underneath. The visible payoff is on the **charts panel**: its header used to be so crowded that
  it hid things to survive — at the app's default window the two chart toggles showed as bare icons,
  and on a narrower window the panel lost its own title entirely, leaving a Δ number over an unnamed
  chart. Nothing hides any more. **Brake/Throttle** and **Ideal lap** keep their labels at every
  window size, the chart's Δ baseline is always named, and you can drag the lap table **≈200 px
  wider** than before. The map and charts panels each give up ~32 px of height for their toolbar.
  The Coaching page's summary strip matches the header above it, which costs it one listed corner at
  small window sizes (the full ranking is still under **Coaching ▸ Opportunities…**).
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

- **Both Δ columns on the Corners tab keep their names in a narrow panel.** A "Δbest" (seconds)
  column and a "Δapex" (km/h) column could paint the same bare `…`, leaving a reader no way to tell
  which was which. Two causes, both closed. The width the budget buys for a header was computed as
  the advance of `Δb…`, which is the widest box at which the `b` is *lost* — Qt keeps the prefix
  that fits strictly inside the box minus the ellipsis — so a column granted exactly what it asked
  for still painted `Δ…`; the width is now derived and then checked against Qt's own elide. And the
  budget's fallback was all-or-nothing: one column the panel could not afford dropped the stems of
  all eight, which is why the app's own minimum window size showed both Δ headers as `…` while a
  horizontal scrollbar was already up and the fallback was buying nothing. It now grants every stem
  when the table already overflows, and otherwise spends what slack there is on the headers that
  can be mistaken for each other first. Swept across every window width from 973 to 1440 px, the
  two Δ headers are now distinct at all but 22 of them, where they were identical at 227 — and the
  scrollbar appears at exactly the same widths as before.
- **The personal-best celebration no longer jumps across the window.** On every real load the card
  was shown at the bottom centre of the *window*, over the Δ-to-ideal chart, painted there six or
  seven times across ~130 ms, and then moved 462 px onto the lap panel where it belongs. The card
  was being shown before the newly-built view had been laid out, so the first placement — and the
  one on the next turn of the event loop — both fell back to the whole window. The card now waits
  for a placement it can trust before it appears (the anchor genuinely is not final until the grid
  splitters restore, 120 ms in), so it is drawn once, in the right place, and its dismiss clock
  starts when it becomes visible.
- **Help ▸ Keyboard shortcuts fits a small display.** The longest card in the app opened 733 px
  tall and refused any height below 717, with no scrollbar — so on the two smallest 13-inch scaled
  modes (1152x720 and 1024x640) the Close button and the HELP group sat off the bottom of the
  screen with no way to reach them. It was the one Help card built without the scroll column and
  85%-of-the-display cap that About and Your data & privacy already had. It now has both: measured
  on 615 / 695 / 775 / 900 px displays it opens at 570 / 638 / 706 / 717 px and scrolls for the
  rest.
- **The lap panel always shows all four of its tabs.** Dragged narrow, the panel used to hide part
  of `Coaching` behind a pair of scroll arrows — two 21x28 px buttons **overlapping each other by
  11x28**, both under the app's own 24 px pointer floor, and the only way to reach a tab you could
  no longer see. The cause was not the arrows: the left column carried a hand-written 280 px
  minimum width, and Qt takes an explicit minimum *instead of* what a widget's contents need rather
  than merging the two — so at that floor the header handed a 240 px row of tabs 228 px and Qt
  raised the arrows to cover the shortfall. The column's floor is now the one Qt derives from the
  panels themselves (292 px), which costs the window 12 px of minimum width and means the arrows
  are never needed at any size the app can be driven to. They are still there as the fallback for a
  future fifth tab or a wider font, and they are now 25x28 each, sharing only the single pixel Qt
  makes two adjacent buttons share.
- **"Share your PB →" shows the keyboard where it is again.** The personal-best card's primary
  action — the one that saves the shareable lap card — painted **zero** changed pixels when the
  keyboard landed on it, while the ✕ and the progression link beside it changed 36 and 656. Its new
  `objectName` rule declared a `border`, and in Qt's stylesheet cascade an ID selector outranks the
  shared `[variant="primary"]:focus` ring, so the ring was never drawn: a keyboard user could not
  tell they were on the button the card exists for, on a card that deletes itself after six
  seconds. The ring is back (512 changed pixels, with the control's box unmoved). The same trap had
  taken the **load card's Cancel button** — its only control — down to 0 changed pixels of 186x28;
  that one is fixed here too, and a new guard now fails the build for any future `#Name` rule that
  borders a focusable control without declaring its own `:focus`.
- **The personal-best card lands on the lap grid, not across the Δ chart.** It was supposed to
  appear at the bottom of the lap panel's body. On the app's own load path it never did: the window
  builds the new view and celebrates in the same breath, before Qt has shown that view, so the card
  fell back to "somewhere in the window" and landed at (571, 792) — 449 px from the lap panel,
  covering 298x86 px of the Δ-to-ideal chart, on first load and on every reload. The card now
  re-asks where it belongs once the layout has settled, and follows its panel if the window is
  resized while it is up.
- **The Corners table's `Δbest` header no longer elides to a bare `Δ…`.** At 1280x800 the column
  carrying lap-time deltas painted a delta sign and an ellipsis — no letter — next to a km/h column
  reading `Δa…`. Both Δ columns now ask for the width their header needs to keep naming them, taken
  from the slack the other columns are holding above their own values, so nothing a column *shows*
  is squeezed to pay for it. The same rule keeps the lap grid's sector columns from painting `…`
  where an `S2` should be.

- **The panel headers and toolbars are visible again.** All four quadrant headers (VIDEO, the lap
  tab bar, MAP, SPEED · Δ TO IDEAL), both panel toolbars and the excluded-lap strip were painting
  the flat window canvas instead of the surface-coloured bar and 1 px separator the theme has
  declared for them since the app had panels — so the four quadrants ran into each other with no
  chrome between them, and the "double-click the header to maximize" target had no visible extent.
  Qt hands a stylesheet's background and border to a plain `QWidget` automatically but *not* to a
  `QWidget` subclass, which needs `WA_StyledBackground`; these bars used to be plain `QWidget`s and
  became subclasses when the panel chrome was consolidated. Measured from the window composite at
  1440x900: fill `#15181E` → `#21252E` and hairline `#15181E` → `#2D323C` on all six bars, 22,127
  changed pixels in the top 120 rows alone (19,201 at 1280x800).
- **The status-bar reference chip now shows its "unverified" caveat.** When a cross-recording
  reference is matched by GPS location rather than a confirmed track name, the chip is meant to be
  tinted as well as labelled — but the tint it applied was the exact colour the chip already
  painted, so the two states rendered identically (0 of 12,338 pixels differed). It wears the
  app's amber trust tint now, redundantly with the "— unverified" text it already carried.

- **The "new personal best" card no longer lands on the MAP panel's header — and it is a card
  now.** It was placed top-centre of the WINDOW, 16 px from its top edge: a rule from when the
  window was one picture rather than four panels. Measured on the shipped app at 1440x900 it sat at
  (579, 16, 281x96) — 36 px deep into the map's header (across the word "MAP"), over all 32 px of
  the map's toolbar and 20 px into the track itself; with the lap panel maximized it sat on THAT
  header instead, cutting the "Dist (m)" column label in half. It now sits in the LAP panel's body,
  bottom-centre: the panel whose ★ session-best row is the lap the card is announcing, and whose
  rows scroll, rather than the one canvas where every pixel is the racing line, the corner markers
  and the draggable start/finish handles. A collapsed lap panel (any other quadrant maximized)
  hands the card to whichever panel IS on screen, so a six-second celebration can never be placed
  off it. And it finally paints the card the theme has always drawn it: `#PBToast` has had a
  background, an amber border and a rounded corner since the moment shipped, and a bare `QWidget`
  honours none of that without `WA_StyledBackground` — so the "card" was transparent, which read as
  a card only while it happened to be over the map's empty top-left corner.
- **The lap and corner tables' numeric headers now sit over their own numbers.** `Time`,
  `Dist (m)` and `Entry (km/h)` — and all seven numeric corner columns — were CENTRED over
  right-aligned digits. Measured from the pixels at 1440x900, each header's ink ended 39 / 40 /
  42 px short of its column while the digits it names ended 13 / 12 / 12 px short: a label floating
  26-30 px to the left of its own data, and further on every extra pixel of column width (a
  maximized lap panel gives each data column 240 px). The app already enforced the opposite for the
  Coaching table. Column widths are unchanged to the pixel at both shipped window sizes. The lap
  grid's sort indicator moves out of the way rather than over the label: under a stylesheet, Qt
  stops subtracting the arrow's width from a header's text rect, so a right-aligned "Time" would
  have painted straight through the ▲.

- **The Stats page no longer clips DATA TRUST mid-word, and no longer scrolls sideways in its own
  quadrant.** Two widgets pinned themselves WIDER than the pane they live in — the widest report
  table to the exact width of its nine columns (730 px) and the friction circle to 2:1 around a
  fixed 220 px height (440 px) — and the larger of those became the scroll body's minimum. So in
  the 503 px quadrant the app opens at, the whole page was laid out 742 px wide and then had to be
  scrolled to: every section heading, every tile row and the entire DATA TRUST card was wrapping at
  a width the reader could not see, and the card's longest line ran 61 px past the right edge and
  stopped mid-number ("…longitudinal r=+0.82 · 3468"). At 1280x800 it was 119 px. Both widgets now
  size themselves from the pane — a report table takes `min(pane, its own columns)` and grows its
  OWN horizontal scrollbar when the pane is narrower, so no column is ever hidden without a bar
  saying so, and the friction circle shrinks with the pane instead of forcing the page wider than
  itself. Measured after: body width equals the viewport, no page-level horizontal scrollbar, and
  0 px of the trust card off-screen at 1440x900 and 1280x800, in the quadrant and maximized, in
  both palettes.
- **The charts panel's axis names are no longer sliced.** `speed (km/h)` and `Δ to ideal (s)` lost
  their left 2 px and `distance (m)` its bottom 5.8 px — at every window size, from 1440x900 down
  to the app's own 845x414 minimum, because the cause was arithmetic rather than a squeeze:
  pyqtgraph reserves `0.8 ×` an axis title's bounding height and then places the title 5 px further
  OUT than it reserved, and Pacer's 2 px focus-ring border on every chart leaves the last rows of
  the scene outside the viewport. The panel now MEASURES what its titles need and reserves it,
  rounded up to the spacing scale, so the budget follows the font instead of a constant chosen
  against one machine's. Measured after: 0 px of overflow on every title at both window sizes and
  at the window minimum.
- **The friction circle can state which way is braking again.** Its rotated y-axis title
  ("longitudinal g (− braking · + accelerating)") is a 304 px box and the axis is 173 px tall in
  the quadrant, so pyqtgraph centred it and cut 88 px off BOTH ends — including the word
  "accelerating". Both axis titles are now stacked over two or three short lines, which costs
  thickness the axis has to spare and saves length it does not, and both get the same measured
  gutter as the charts panel (the x title was losing 7.4 px through its descenders).

- **The status bar no longer spends window height on a chip nobody can see.** `QStatusBar` sizes
  itself from its children's size hints and counts a permanent widget that is merely HIDDEN, so the
  cross-recording reference chip — invisible on any session without a reference, which is nearly
  all of them — was costing the four panels 3 px between them (391/452/321/522 against
  393/453/322/524, measured on the real window). It is now added to and removed from the bar rather
  than shown and hidden.

- **A brake point on the map can no longer be mistaken for a corner apex, and the map key
  draws the glyphs that are actually on the map.** Giving each lap's brake glyphs their own
  SHAPE handed identity slot 1 the circle — which is the corner-apex dot, in the identical
  hue, so in compare mode two different marker classes drew the same mark. Slot 1 is a star
  now (mask distance 0.645 from the circle), the circle is reserved to the apex dots, and the
  key's "Brake point" row paints the real per-lap glyphs instead of one fixed triangle in the
  video marker's colour.
- **The Stats page's charts and the Library's PB progression keep their line weight on a
  Retina display.** A pyqtgraph pen width is in DEVICE pixels, so the sparkline, the friction
  circle's rings and grip envelope, and the PB line and its axes all drew half their design
  weight on the screens they are usually read on, while the speed/Δ charts beside them had
  already been fixed. The guard that was meant to prevent this walked a hard-coded list of two
  file names; it now finds every module that draws with pyqtgraph, and rejects a bare colour
  handed to `setPen` (an implicit one-device-pixel gridline pen) as well as a literal width.
- **Every stat tile paints its value larger than its caption again**, and the Stats page's
  notes, the coaching phase-bar numbers and the Library dialog's summary/privacy text are back
  at the sizes they were written for. The theme's base stylesheet rule carried a `font-size`
  that matched *every* widget, and a stylesheet font outranks a programmatic `setFont` — so all
  29 tiles painted a 13 px value over a 13 px caption where 15 over 12 was intended, and only
  colour separated "1:08.771" from "best lap". The app-wide default font now comes from
  `app.setFont()`, which a widget's own font wins against. Same cause: the Stats page's muted
  "provisional timing" target tiles only rendered italic after a *second* refresh, so the cue
  was missing on every single-refresh path.
- **The bundled track centerline is no longer armed as a gap-fill donor on circuits it has
  nothing to do with.** `studio/mk_centerline.json` traces one circuit (Daytona Milton Keynes),
  but the closed-loop fit that places it computed a residual, printed it, and handed the ring
  back regardless — so `LapRenderCache.donors_for` offered it on every session, and on a
  recording whose only valid lap has a dropout it was the *only* fill source. Measured on real
  recordings: on Sandown it fitted at 39 % of the size it takes on its own track (RMS 9.4 m,
  72 % coverage) and on an unnamed kart track at 30 % (RMS 6.0 m, 85 %). The fit is now an
  admission test — the ring is returned only when its shape actually matches (coverage ≥ 98 %
  and RMS ≤ 3 m, thresholds bracketing 65 real Daytona MK laps against 26 laps of two other
  circuits) — and a refusal simply removes the donor, leaving `gapfill`'s own dashed spline
  bridge. The Daytona MK behaviour is unchanged.
- **An exported HTML report is the same document from any Mac.** The report's figures are
  `QWidget.grab()` snapshots, which render at the screen's device pixel ratio, and they were
  embedded with no stated width — so the browser laid each figure out at its *device* width and
  the exported document silently described the machine that wrote it. Measured on one recording
  exported twice from the same 1512 × 982 logical screen: figures 917 px wide from a non-Retina
  screen and 1120 px from a Retina one (+22 %), the page 168 px longer, moving the page break in
  a print or PDF. Each figure now states its logical width, so the layout is identical
  everywhere while the extra Retina pixels stay in the file and keep the figure crisp when
  zoomed or printed.
- **Changing units or turning on colour-blind cues no longer kills the app after ordinary
  lap-panel use.** The Statistics page re-places its tiles into their grid whenever the column
  count changes — which its own scrollbar appearing makes happen the first time the page is
  shown — and handed each tile straight back to `QGridLayout.addWidget` while the grid still
  held it. Qt reacts to that by deleting the tile's existing layout item from *inside* the
  `addWidget` call, and one such pass over the page's ~30 tiles left the process in a state
  where the next burst of Qt-object destruction segfaulted: a View ▸ Units or View ▸
  Colour-blind-safe cues toggle, after nothing but tab switching, took the window down with no
  dialog and no chance to save. Each tile is now taken out of the grid before it goes back in.
  Measured on the reporting sequence: 7 crashes in 10 runs before, 0 in 12 after.
- **Turning colour-blind-safe cues on now recolours the brake-point glyphs too.** The glyphs on
  the speed chart and on the map trace are drawn from a cached colour, so they alone did not
  follow the flip: the best lap's curve turned colour-blind blue while its own markers stayed
  the standard palette's green — a hue that palette exists to remove — until you happened to
  click a lap. The palette refresh now re-pushes them.
- **Opening a recording no longer leaves a timer and a worker thread behind.** Every load left
  its loading-card `QTimer` and its finished loader `QThread` attached to the window for the
  rest of the session — one more of each per recording opened, with no plateau. Both are
  released now, the thread only ever from its own `finished` signal, so a cancelled or
  superseded load (neither of which stops the read) can never free one that is still running.
- **Chart and map lines are their designed weight on a Retina display, and the two
  colour-blind cues that stopped short now reach the surfaces they missed.** pyqtgraph
  builds every pen `cosmetic`, so its width is in DEVICE pixels and Qt never scales it:
  measured on a fixed 1512x982 logical screen, the Δ-plot gridlines drew 1.0 logical px at
  DPR 1 but 0.5 at DPR 2, the always-on best-lap trace and its legend swatch degraded to
  near-invisible hairlines, and the map's rainbow ribbon halved from 3 to 1.5 logical px so
  its parallel strands stopped merging — in the window, in the exported HTML report and on
  the shared lap card. Every chart/map pen width now goes through `theme.line_width` and is
  re-resolved when the window moves to a screen with a different ratio. Separately, the
  hero Δideal readout — the largest text in the window — read the raw `C.behind` token, so
  it kept the standard palette's red in BOTH palettes (a render of it was byte-identical
  between them) while the Corners table below painted the same meaning in the colour-blind
  orange; and the brake-point glyphs were one filled triangle in six hues, two of which are
  a single colour for a deuteranope (CIE76 dE 1.27), so they now carry a per-slot SHAPE plus
  an outline that stops adjacent laps' markers fusing into one blob.

- **A reference recording is read the way its owner saved it, and a copy of the open
  recording can no longer be its own reference.** Loading another recording as a
  cross-recording reference went through `Session.load` alone, which returns telemetry cut at
  the loader's auto-fitted start line — the line the owner had dragged and confirmed lives in
  that recording's sidecar and was applied only when the recording was opened in a window. So
  the same recording measured 740 m laps when opened and 199 m fragments as a reference, and
  the lap-length band refused it (0.27x) with advice to drag a start line that was already
  saved on disk. The restore is now one shared seam
  (`Session.restore_saved_timing_lines`) that both the primary open and the reference
  adoption call, so a reference is segmented exactly as opening it would segment it.
  Separately, "is this reference my own footage?" was decided purely by comparing file paths,
  so a byte-identical copy under another name (a duplicated folder, an external-drive backup,
  a re-download) was accepted as a reference against itself and every corner Δ printed
  "+0.00" as though it were a measurement. Identity now also asks an intrinsic question —
  the GPS wall clock at both ends of the recording plus its kept point count — and the
  downstream `reference_is_own_recording()` fallback no longer shares a predicate with the
  refusal: it can tell from the adopted lap's own numbers alone, so the Δ dashes hold even if
  both provenance checks are wrong.

- **A mis-dragged start/finish line always has a way back, and a saved track no longer
  vouches for a line that isn't its own.** Re-opening a recording used to lock a bad drag
  in for good — the undo stack died with the session, and nothing else in the app offered
  the auto-fitted line back (a 12 m nudge moved the session best 4.2%, silently). The
  history now carries across a re-open of the same recording, and Edit gained "Revert
  start/finish line", enabled only when it would move the line and itself undoable.
  Separately, `File ▸ Save as track…` followed by one Undo left every "verified" surface
  lit on the auto-fitted line the app had just called provisional: a track name now
  certifies only the geometry it was attached to. Undo also says which kind of line it
  restored, instead of naming the start/finish line after a sector-only edit, and the
  map's "⌘Z puts them back" plate retracts the moment ⌘Z is pressed.
- **The Library dialog now fits the privacy note it ships with, and never re-opens too small
  to show the library.** Naming `tracks.json` in that note made it 194 characters longer, but
  a word-wrapped label reports a *one-line* height to the layout — so the dialog's minimum
  size never grew, and at the smallest size a drag can reach the note needed 128 px in the
  83 px it was given: 45 px of it painted through the button row, taking both new sentences
  about `tracks.json` with it. The paragraph is now a `WrapLabel` (the wrapper the Help cards
  already use, promoted to `studio/widgets.py`), so the dialog's own minimum includes the
  height its text really wraps to. Separately, the size the dialog remembers had no floor: one
  drag to the corner stored a size showing 0.97 of one row of a 201-recording library, and
  every future open came back that way with nothing to undo it. What is *stored* is still
  exactly what the user left — the floor is applied to what *opens*, so the app is never
  caught silently forgetting a resize — and the library now opens showing at least five
  recordings, or as many as the screen allows.

- **Every surface that names the Δ baseline now names the one actually in use, and a
  recording can no longer be its own reference.** With a cross-recording reference loaded,
  the charts header, the Δ chart's y-axis and the Corners Δ column all still read "best"
  while the numbers under them were measured against another recording's lap — the chart
  legend, three inches away, named it correctly, so one panel gave two different baselines
  the same word. The baseline is now plumbed as a *kind* (best · ideal · reference), so each
  caption follows it (`Δ TO REF` / `Δ to ref (s)` / `Δref`), with the recording spelled out on
  hover. Separately, the reference picker happily accepted the recording already open:
  every other guard passes there, and the result was twelve corner rows of `+0.00` and a
  cross-compare pinning the same lap of the same file in both panes, all presented as
  measurements. That is refused now ("that is the recording you already have open"), and the
  two surfaces that had assumed a reference is always a *different* recording — the Corners
  self-Δ dashes and the compare same-lap badge — ask instead of assuming. Clearing the
  reference also ends the cross-recording compare it was the entire point of, instead of
  leaving a "REFERENCE" pane naming a recording the rest of the app says is gone.
- **The map no longer draws a reference racing line at the wrong size.** The cross-recording
  reference ring was accepted on its fit RESIDUAL alone, but that fit is a *similarity* fit —
  it is free to resize the loop — so a mis-sized reference was simply shrunk onto your track
  until the residual looked good: a real 190.6 x 124.8 m reference lap was drawn at
  54.2 x 72.1 m (scale 0.40) for an RMS of 4.33 m, a third of the 12 m tolerance. Both
  recordings are already measured in metres, so the fit is now gated on SCALE as well —
  the same +-10 % band the session already demands of a reference lap's length, applied
  symmetrically so the verdict cannot flip when you swap which recording is the reference
  (measured: every one of 163 real laps across two circuits fits at 0.964-1.035). And a
  reference whose line cannot be drawn is no longer silent: the map says so, says the faint
  line you can see is still your own best lap, and says the delta charts and lap table do
  still use the reference. The map's notice plate also grew to fit multi-line text instead
  of slicing through it.

- **The two ways back from a destroyed library or track database are now reachable, and the app
  says when it uses one.** Rewriting a `tracks.json` pacer could not fully read now warns and
  names the copy it kept, instead of reporting a successful save while the circuit list quietly
  got shorter; the Library's `Restore…` — the other half of `Back up…` — is now built and wired;
  and the privacy note names `tracks.json`, the file holding every saved circuit's coordinates,
  which `Clear library` leaves alone and `Back up…` does not copy.
- **Clearing the library can be undone.** “Clear library” wiped the whole analyzed history —
  every recording, track, best lap and PB progression — and kept no copy of it. The index
  backup only ever ran for a file the app couldn’t read or a newer one it couldn’t migrate, so
  the one destructive button you can reach from the UI was the one write with no way back — two
  buttons along from a “Back up…” that had no “Restore”. The wipe now copies the index to
  `library.json.bak` first, and the confirm says so and names the folder “Reveal in Finder”
  opens, so a mis-click is recoverable.
- **The library opens big enough to browse.** With 200 analyzed recordings the list was given a
  139-pixel viewport — 4.6 rows, 2.3% of the library — while the PB chart sat on its 150-pixel
  floor and then took a share of every pixel the window gained (260 px at 860 tall) to draw the
  same handful of dots; and a window you enlarged opened back at 720×600 the next time. The
  library now opens at 880×860 (clamped to your screen), the PB chart is held to a 150–200
  pixel band so the list keeps the rest, and a size you change is remembered.
- **Saving a recording as a track now updates everything it changes.** `File ▸ Save as track…`
  named the circuit and made the lap timing trusted, but only the trust strip over the map
  noticed. In the same frame the map canvas still painted the amber “drag to set start/finish —
  lap timing provisional” callout, the Laps table still showed the lap in provisional italics with
  its ★ best mark withheld, and the Library row still read “unknown track · provisional” — so the
  lap was silently missing from the PB progression of the track it had just created, until you
  happened to re-open the file. The same stale-library gap applied to a start/finish drag, which
  also left the library quoting the pre-drag lap times. Every one of those surfaces now refreshes
  with the trust flag.

- **A reference recording must now hold a lap the same LENGTH as yours.** "Load reference
  recording…" only ever checked that the other file was the same *track* — so a recording of the
  same circuit whose start line was in a different place, cutting it into laps 3.7× longer, was
  adopted as the Δ baseline anyway. Every "vs best" surface then reported the length difference as
  time: the session's own best lap plotted at **−35.4 s** on a chart whose x-axis had silently
  stretched to the reference's 740 m, and the Corners tab showed **7.37 s gained in one corner of
  a 13-second lap**, in green. The reference lap now has to sit within ±10% of your session's
  median lap distance — the identical band the app already uses to decide which of your own laps
  count — and a mismatch is refused with both lengths named ("counted laps here run ~203 m; the
  reference lap runs ~740 m"), the local best lap left untouched, and the fix pointed at: drag the
  start/finish line onto the right place and load the reference again.
- **Saving a track can no longer wipe every circuit you had already saved.** If `tracks.json`
  couldn't be read — a crash mid-write, a hand-edit through Reveal in Finder, or a file written by
  a newer build of Pacer — Pacer fell back to an empty database, and the very next ordinary
  **File ▸ Save as track…** rewrote the file from that empty view: three saved circuits went to
  one, with no copy kept, no warning, and a status bar that reported success. Every start/finish
  line, sector line and location anchor in that file was gone, and the recordings that used to
  auto-detect their track went back to "lap timing provisional". Now a save that is about to
  overwrite a database Pacer could not read in full copies the original to `tracks.json.bak`
  first, so nothing is ever lost silently — the same protection the session library has had since
  its own schema bump. A database written by a **newer** version of Pacer is no longer treated as
  corrupt either: its circuits are read as far as this build understands them and survive the
  downgrade, and dropping a single malformed circuit to repair the file now keeps the original
  alongside it.
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
