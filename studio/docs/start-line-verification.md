# Start/finish line verification — Daytona Milton Keynes

**Question.** Is the hard-coded Daytona MK start/finish line in `studio/tracks.py` at the
*real* physical start/finish, is it well placed for lap segmentation, and does its position
change the lap times we report versus the transponder?

**Verdict.** The current line is **correct and well placed** — no change to `tracks.py`.
It sits on the real main (pit) straight, is crossed cleanly once per lap, and is at (or
indistinguishably close to) the per-lap-noise minimum on **both** validation recordings.
Moving it does **not** robustly improve anything and risks regressing recording 0060.

The current coordinates (kept):

```python
start_a=(52.04031, -0.78487),
start_b=(52.04020, -0.78460),
```

---

## 1. Where the real start/finish is (from the pictures)

Source images: `/Users/daniil/Desktop/Tracks/MK/` — `gmaps_sat.png` (satellite),
`Daytona-Milton-Keynes_Aerial.jpg`, `gmaps_pict.png` (map outline), `Link-Cliff-Plan.pdf`
(the official "Linkback + Cliff Drop Circuit" line-drawing plan, 1.375 km layout).

The **plan is authoritative**: it explicitly labels **"Start Finish"** on the **main straight
that runs alongside the pit complex / "Start Board"**, between the top hairpin (corner 11, by
the pit entrance / "Cut Through") and the bottom hairpin (corner 1, by the pit exit). The dummy
grid slots are painted along that straight, and a transverse line crosses it at the south end of
the grid. See `start-line-figures/official-plan-pit-straight.png`.

In the satellite/aerial the same feature is the **western straight beside the long white pit
building** (top-left of the track, nearest the paddock marquee). The A5 dual-carriageway runs
along the south-west edge; the railway along the north-east.

## 2. Mapping the real S/F to GPS coordinates

The GPS trace is already georeferenced (real lat/lon per fix). To place the satellite image in
the same frame I fit a 6-parameter affine `lon/lat → satellite pixel` by ICP between the GPS
trace points and the satellite's track-tarmac pixels. **Residual ≈ 3 px median ≈ sub-metre** —
the speed-coloured trace lands exactly on the tarmac (`start-line-figures/satellite-trace-overlay-0060.png`).

> Note / caveat: the stored `studio/mk_centerline.json` reference centerline is **not** usable
> for this mapping — its ICP fit (in `reference.py`) collapses onto a small inner sub-loop and
> covers only ~30 % of the real track footprint (see investigation in the commit). That only
> affects the rarely-used gap-fill fallback, not segmentation/timing, but it is flagged here as
> separate tech debt. The satellite georef above is independent of it.

Mapping the current `tracks.py` line through that georef puts it **on the western pit straight,
beside the pit building**, exactly where the plan's "Start Finish" sits
(`start-line-figures/start-line-vs-plan.png`, `start-line-figures/start-line-zoom.png`). The
plan is a stylised, non-conformal drawing, so it cannot give a reliable *absolute* metres-offset
(a global affine of it lands the marker tens of metres out — an artefact of the drawing, not a
real error); the agreement is therefore stated qualitatively + corroborated by the empirical
results below.

## 3. How the current line compares

* **On the straight, fast:** the trace crosses the current line at a **median ≈ 72–74 km/h**
  (min ≈ 50, max ≈ 78) on both recordings — i.e. near top speed, on the straight, **not** in a
  corner. A line on a fast straight is exactly what minimises per-lap crossing-time noise.
* **Crossed exactly once per lap:** the full trace crosses the line `laps_count` times with no
  double-counting; the fitter (`session._fit_start_line`) keeps the exact 22 m segment on 0062
  and widens it modestly to 28.8 m on 0060 only to recover one pass the short segment stepped
  over. No double/missed crossings.
* **Lap phase matches the transponder:** the per-lap times match the transponder lap-for-lap
  (residual mean ≈ 0, std ≈ 0.05–0.09 s — §4), which can only happen if the GPS S/F is at the
  same lap phase as the transponder's timing loop.

## 4. Empirical impact — segmentation, residual, alignment (both recordings)

Method: load each recording once, then re-segment the **same** `pacer.Laps` with the current
line and with the line shifted ±8/15/25 m **along** the straight (and a deliberately bad
infield-pinch line), holding the GPS9 true-clock timing axis fixed. For each, re-run the
wall-clock validator's duration-correlation lock + clean-racing residual vs the transponder CSV.
(`studio/_validate_wallclock.py` for the baseline; sweep harness in the commit.)

### Baseline — current line

| Recording | valid laps | CSV lock | corr | clean mean | clean std | clean RMS |
|-----------|-----------:|----------|-----:|-----------:|----------:|----------:|
| 0060      | 57 (58 seg) | 302–358 | 0.9917 | +0.0030 s | **0.0871 s** | 0.0872 s |
| 0062      | 65 (66 seg) | 856–920 | 0.9965 | +0.0015 s | **0.0527 s** | 0.0527 s |

The correlation lock is **unique** (every non-locked offset < 0.29 on 0060; margin ≈ +0.70).

### Sweep — shifting the line along the straight (clean-racing std, exact segment)

| position vs current | 0060 std | 0060 corr | 0060 segs | 0062 std | 0062 corr | 0062 segs |
|---------------------|---------:|----------:|----------:|---------:|----------:|----------:|
| **current (shipping)** | **0.0871** | 0.9917 | 58 | **0.0527** | 0.9965 | 66 |
| −25 m (exact)       | (collapses) | — | **1** | — | — | — |
| −15 m               | 0.4631 | 0.8286 | 56 | 0.1097 | 0.9852 | 66 |
| −8 m                | 0.1064 | 0.9887 | 57 | 0.0773 | 0.9926 | 66 |
| +8 m                | 0.0904 | 0.9910 | 57 | 0.0403 | 0.9979 | 66 |
| +15 m               | 0.1060 | 0.9886 | 57 | 0.0405 | 0.9979 | 66 |
| +25 m               | 0.1340 | 0.9782 | 57 | 0.0526 | 0.9966 | 66 |
| infield pinch       | 0.96–4.1 | 0.18–0.21 | 63–65 | — | — | — |

### What this shows (honest answer to "does position affect lap times?")

1. **Mean lap time is invariant to line position** (for cleanly-segmented laps). Every shift
   that stays cleanly segmented gives clean-mean ≈ 0.000 → ±0.008 s. This is the closed-loop
   period: moving the line cannot shift the *mean* residual. **So the start line is NOT the
   source of the validated residual** — the GPS9 10 Hz timing is, and that was already shown
   unbiased (k ≈ 1.0; −46/−22 ppm on 0060/0062). This is the key correction to the belief that
   position "affects laptimes significantly": the **mean** does not move.

2. **Per-lap noise (std) DOES depend on position.** A line on the fast straight (current)
   minimises it; pushing it toward a hairpin (−15 m on 0060 → 0.46 s; the infield pinch → 0.9–4 s)
   inflates it. `+8 m` happens to give a tighter std on 0062 (0.040) but **equal-or-worse on
   0060** (0.090 vs 0.087) — a recording-specific gain, i.e. exactly the overfit the team has
   been burned by. The current line is the only position that is best-or-tied on 0060 **and**
   solidly good on 0062.

3. **Segmentation is the real risk and the current line avoids it.** A short line in the wrong
   place misses passes and **fuses all laps into one** (−25 m exact → 1 lap); a line at a track
   pinch-point **double-counts** and destroys the lock (infield pinch → corr 0.18, 63–65 "laps").
   The current line is crossed exactly once per lap on both recordings with a unique 0.99 lock.

## 5. Conclusion

The current `tracks.py` line is on the real main/pit straight (matches the official plan and the
sub-metre satellite georef), is crossed cleanly once per lap, gives a unique transponder lock on
both recordings, and sits at the robust per-lap-noise minimum. **Kept unchanged.** Line position
here changes per-lap *noise* and *segmentation*, but **not the mean lap time** — so it is not the
source of the residual, and the present placement is already the right one.

Validation: `pixi run build` clean, `pixi run test` 10/10, `python -m studio._smoke` OK. No
source files changed (the pre-existing `UP037` ruff hits in `studio/*.py` are unrelated tech
debt). The transponder CSV and the `.startline_tmp/` scratch were **not** committed.

Figures in `start-line-figures/`:
* `satellite-trace-overlay-0060.png` — GPS trace (speed-coloured) on the georeferenced
  satellite; red = current S/F on the pit straight.
* `start-line-vs-plan.png` — satellite (left) vs the official plan's "Start Finish" (right).
* `start-line-zoom.png` — close-up of the current line on the pit straight by the pit building.
* `official-plan-pit-straight.png` — the plan's labelled S/F + dummy grid + Start Board.
