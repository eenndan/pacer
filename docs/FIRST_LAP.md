# Read your first lap

A 30-second path from a GoPro recording to *"where am I losing time, and what do I do about it?"* —
the reason Pacer exists.

![Pacer — one window on a GoPro recording: synced video, a speed-coloured track map with brake points and corner apexes, the live Δ-to-ideal readout over distance-aligned speed and Δ charts, and the lap table with the session best starred](media/hero.png)

## 1 · Open a recording

Drag a GoPro `.MP4` onto the window, or **File ▸ Open**.

Pacer reads the GPS9 + motion data the camera already recorded — no transponder, no extra sensors.

**A long recording is split into chapters** (`GX01…`, `GX02…`, same 4-digit recording number), and
Pacer lays them on one timeline. **Dropping** a chapter or picking one in **File ▸ Open** opens the
whole recording, its chapters in order. Only the command line can open part of one
(`pixi run studio -- GX010062.MP4` loads that chapter alone), and then the window says so — *"1 of 3
chapters — File ▸ Load full recording to analyse the whole recording"* — and that menu item is live.
Your personal best and focus list wait for the whole recording, so part of one never decides them.

> **No footage of your own? Open the demo.** `pixi run studio -- --demo` downloads, once (about
> 11 MB), and opens a **synthetic** session. **Nothing in it was filmed or driven:** a simulated kart
> laps a fictional circuit placed in the open Atlantic, written by `studio/dev/make_demo.py` in the
> same HERO13-shaped format a real recording has — GPS9, accelerometer, gyroscope, gravity and
> orientation. No person, kart, place or camera footage is in it. Its video is a rendered slate that
> says so on every frame, with a running timecode and a dot at the kart's true position, so you can
> check Pacer's map against the picture yourself. The circuit is one Pacer ships, so step 2 below
> is done for you and the timing opens verified. After that first run the welcome screen offers
> **Open demo** too; offline, `--demo` says plainly that the download failed.

## 2 · Set where a lap begins (only on a new track)

The **first time** you analyse a circuit Pacer doesn't know, it can't know where the start/finish
line is, so it places a **sensible default** (across the main straight) and marks the timing
**provisional** — the lap times show muted/italic and a banner over the map reads *"Lap timing is
unverified — drag the start/finish line on the map to where a lap begins."*

**Drag the dashed start/finish line on the map** to where a lap actually begins. The lap times
snap to it, the banner clears, and the placement is **remembered for that recording**. Want it to
auto-detect next time? **File ▸ Save as track…** turns your line into a named track.

*(On the tracks Pacer already ships — Daytona Milton Keynes, Sandown Park and the demo's synthetic
circuit — and on any track you've saved, this step is done for you.)*

## 3 · Read the lap

The window is four panels — video, map, charts, and a tabbed panel under the video. Any panel
maximises to the whole window (the ⛶ button in its header, or double-click the header), and every
divider between them drags.

- **Track map** — your racing line, with corners, brake points, and the start/sector lines you can
  drag. One dropdown switches what the line is coloured *by*: **Off**, **Speed**, **Δ to best**,
  **Grip (est)** (how much of the available grip you used) or **Elevation**. The key names every
  mark on the plot. Grip is the one to read with care, here and in its Corners and Stats columns:
  compare it lap to lap within the same corner, not corner to corner. A lower reading does not
  mean that corner has more grip to spare.
- **Speed · Δ to ideal charts** — the speed trace and the cumulative time delta, distance-aligned
  so corners line up. The live readout leads with **Δideal**: how far you are off your *theoretical
  ideal* — the best you've driven at each point on track, stitched together (not a single drivable
  lap) — right where you are on track. Two toggles add a **Brake/Throttle** band under the speed
  curve and overlay the **ideal lap** itself.
- **The tabbed panel**, five full-height pages on the digits **1–5**:
  - **Laps** — every lap and its sector splits, sortable, session best starred (★). The **Entry**
    column is the corner-entry speed; toggle **View ▸ Units** for mph.
  - **Corners** — the same corners, for the *one* lap you have selected: time-in-corner, that
    lap's Δ vs the best lap, apex/entry/exit speeds.
  - **Stats** — session totals, the pace distribution, the g-g friction circle, corner/braking/
    straights reports, the **IDEAL LAP** block and the **DATA TRUST** card. **⌘⇧S** opens it
    full-window.
  - **Coaching** — see below.
  - **Marks** — the one page that holds a *conclusion* rather than a measurement: what you wrote
    down against this recording, plus what Pacer detected for you (GPS dropouts, laps left out of
    your times, stretches of degraded GPS). **B** drops a mark at the playhead; **,** and **.**
    jump between them.

## 4 · See where the time goes (the coaching)

The **Coaching** tab (press **4**; the same rows in a resizable window under **Coaching ▸
Opportunities**) ranks your corners by **time lost vs your best**, taken as the *median* over
your clean laps, each with a **reason** (apex speed, braking, coasting, or line) and a **±σ**
consistency badge — one row telling you *how much* time and *how repeatable* it is. It does not
follow your lap selection; the **Corners** tab is the per-lap view. Where your own laps show it,
a row also says which way your braking went with your quicker passes through that corner
("Braking later went with quicker passes here (36 laps)"). That is measured from your laps, and it
is never a distance: Pacer's braking model can't honestly say how many metres.

Stats ▸ **IDEAL LAP** answers the next question — *how much is actually on the table* — and says
what it was built from: which of your laps donated each corner and straight, how many of your laps
have already been that fast there, and how many laps the whole thing was minimised over. That last
number matters: the ideal is a sum of per-segment minima, so it drops the longer you stay out.

## 5 · Race a lap side-by-side

Scrub the lap and the **GoPro video** follows. **Load a reference recording** (**Coaching ▸ Load
reference recording…**) to play your lap **next to** the best lap of *another* recording of the
same track — yours from last month, or a friend's GoPro file.

## 6 · Come back faster

Every session lands in the **Session Library** (**File ▸ Library…**, or **⌘L**), which charts your **personal-
best progression per track** over time. Beat your previous best on a track and Pacer says so.

---

**Trust, honestly labelled.** Timing from a GPS9 camera (a Hero 11 or a Hero 13) is validated
against a real transponder; GPS5 cameras — Hero 5 through Hero 10, and the Max — fall back to the
video clock and are flagged as approximate. A Hero 12 has no GPS receiver at all and cannot be
lap-timed.
Inferred channels (brake/throttle/grip) and braking-point hints carry an `(est)` / `EST` label.
Provisional (unset start line) timing is muted until you place the line. When in doubt, the number
tells you how much to trust it.

Found a bug or a GoPro model that doesn't parse? **Help ▸ Report a problem…**
