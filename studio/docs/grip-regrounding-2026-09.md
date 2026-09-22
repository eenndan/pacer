# The Grip % column, re-grounded — 2026-09-20

**The August roadmap gated "grip headroom + utilisation trend per corner" on a re-grounding,
because the per-corner grip channel was then described as "pace-blind (~0.6)" and nothing had
re-checked it after its normalisation changed.** This is that re-check, measured on four present
recordings through the app's own `DrivingChannels.lap_corner_grip`.

**The verdict in one line: Grip % is NOT pace-blind — it tracks how fast the corner actually went,
strongly, on every recording, and keeps most of that after the lap's own pace and the apex-speed
column beside it are held. The gate it was under is the BETWEEN-corner reading, and that one is
still not supported.** This is a measurement, not a feature: no surface changed here.

The probe is [`studio/dev/probes/p13_grip_regrounding.py`](../dev/probes/p13_grip_regrounding.py).

## What was measured, and on what

| | Sandown 3h (`GX0*0064`) | `SD_30_08_26` (`GX0*0065`) | `SD_19_09_26` (`GX0*0068`) | `MK_18_09_26` (`GX0*0067`) |
|---|---|---|---|---|
| clean laps × corners | 62 × 7 | 37 × 7 | 36 × 7 | 19 × 12 |
| track | Sandown, clockwise | Sandown, clockwise | Sandown, clockwise | a different track, anticlockwise |
| session grip envelope (p98 \|g\|) | 1.488 g | 1.514 g | 1.513 g | 1.349 g |
| Grip % per corner, median | 70.3 – 82.7 | 71.4 – 82.9 | 72.1 – 83.3 | 46.3 – 75.9 |
| within-corner spread (p90−p10) | 20.0 – 43.4 pts | 7.0 – 13.2 pts | 6.9 – 13.5 pts | 12.1 – 32.4 pts |

D24 is gone (the owner reorganised the Desktop), so none of its published grip figures could be
re-measured here; nothing below is a D24 number. Every folder was snapshotted (size + mtime) before
and after each load and none moved; the app-support seams were diverted throughout.

## The test, fixed before the data was read

The pre-specified statistic is the **pooled mean of the per-corner Spearman ρ of Grip % against
that corner's own time across laps** — pooled because it selects no corner. The null shuffles
Grip % across laps **within each corner**, so the column's own distribution survives exactly and
only its pairing with the time dies (20,000 permutations).

| pooled ρ(Grip %, corner time) | Sandown 3h | SD_30_08 | SD_19_09 | MK_18_09 |
|---|---|---|---|---|
| raw | **−0.707** (p < 0.0001) | **−0.587** (p < 0.0001) | **−0.428** (p < 0.0001) | **−0.605** (p < 0.0001) |
| lap pace removed (#265's confound) | **−0.439** (p < 0.0001) | **−0.327** (p < 0.0001) | **−0.249** (p = 0.0003) | **−0.602** (p < 0.0001) |
| per-lap mean Grip % vs lap time | −0.907 | −0.703 | −0.684 | −0.898 |

The last row is the session-envelope divisor doing exactly what `driving.grip_envelope`'s docstring
says it is for: because the divisor is one number for the whole recording, a slow lap reads
genuinely lower instead of every lap reading ~the same.

**Power.** An association of known rank strength planted into the same matrices: at a true ρ of
−0.2 the pooled test fires **98 % / 85 % / 88 % / 52 %** of the time (200 replicates each); at −0.3,
**100 % / 100 % / 100 % / 90 %**. A silence from this test would have meant something.

**Selection control.** With nothing to find, "some corner at FWER 0.05" fires 2.7–5.3 % (nominal),
while "the TOP corner, read off its own p" fires **22.7–32.0 %** — the same trap #311 measured at
19–21 %. Every per-corner claim here is therefore quoted at its family-wise p, and the verdict
rests on the pooled statistic, which selects nothing.

## Does it add anything to the column beside it?

The CORNERS table already prints the corner's apex speed. Grip % correlates with it
(mean ρ +0.53 / +0.62 / +0.52 / +0.62), so the fair question is what each column keeps when the
other is held:

| mean over corners | Sandown 3h | SD_30_08 | SD_19_09 | MK_18_09 |
|---|---|---|---|---|
| ρ(Grip %, time) | −0.707 | −0.587 | −0.428 | −0.605 |
| …with apex speed held | **−0.569** | **−0.348** | **−0.307** | **−0.400** |
| ρ(apex speed, time) | −0.541 | −0.589 | −0.354 | −0.613 |
| …with Grip % held | −0.298 | −0.364 | −0.167 | −0.413 |

Neither column absorbs the other. They are two readings of how hard the corner was taken — the
minimum speed at one point, the median combined load across the whole window — and the second is
not a restatement of the first.

## What a driver can do with it, stated plainly

**Within one corner, across the laps of one session, Grip % ranks the laps the same way the clock
does.** "This lap you used 71 % at C3 where your good laps use 79 %" is a supported sentence: the
association is strong, it survives removing the lap's own pace, and it replicates on four
recordings including a different track in the opposite direction.

**It is not a tyre-limit reading, and it cannot rank corners against each other.** The between-corner
"headroom" claim — the thing the roadmap actually gated — was tested against the time each corner
loses vs the best lap: ρ = **−0.179** (p 0.72), **−0.607** (p 0.17), **−0.071** (p 0.91),
**−0.143** (p 0.80). And with 6–12 corners that test is nearly powerless: |ρ| has to exceed
**0.75–0.83** to reach 5 %, so this is **unpowered, not refuted**. A corner reading 70 % is not
thereby a corner with 30 % to give — corner radius, entry speed and the length of the window all
move the level, and nothing here separates them.

**So the gate stays on the headroom feature.** What would lift it: a recording set with many more
corners (a longer circuit, or corners pooled across sessions of one track by apex position rather
than label), where the between-corner test has the power it does not have here.

## Direction (#334)

`corner_grip` is built from `hypot(lat, long)`, which no sign can reach, and the split confirms it:
median Grip % left/right is 75.8/76.7 (Sandown 3h, clockwise, 3 left + 4 right) and 64.0/69.4
(MK, anticlockwise, 8 left + 4 right). No direction-dependent asymmetry, and the association above
is as strong on the anticlockwise track as on the clockwise ones.

## Re-running it

```
PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes._m5_cache          # load once
PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p13_grip_regrounding
```
