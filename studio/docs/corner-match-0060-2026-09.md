# Why D24 0060 matches only 48 % of its corners on track — 2026-09 (M6)

#329 found that on the D24 0060 pair, **236 of 456** corner cells have an edge interpolated rather
than matched on track (0062: **4 of 780**). Interpolated cells are a median **0.22 s** wrong. #330
found C9's entry matched on only **26 %** of 0060's laps. C4 stops the Corners table counting those
cells. This note is about the **cause**: why the spatial match fails so much more often on 0060.

**Verdict: there is no code defect, and no recent PR changed the match.** The failure has two
causes, and they compound:

1. **0060's GPS positions scatter about twice as far from lap to lap as 0062's.** Part of that
   scatter is one rigid shift of the whole lap. A driving line cannot shift a whole closed circuit
   by one vector, and the shift drifts slowly from lap to lap. The altitude, which no driving line
   can move, scatters **3.9×** as much.
2. **The corner match is anchored on the fastest lap, and on 0060 the fastest lap is one of the
   most displaced.** Lap 17 sits **1.75 m** from the session's typical line (rank 34 of 38). It is
   **4.5-4.8 m** off at C8's exit and C9's entry, which are the two worst boundaries.

Every number below is printed by
[`studio/dev/probes/p6_corner_match_cause.py`](../dev/probes/p6_corner_match_cause.py), measured
through the real `Session.load` on `origin/main` 9a3d8b4 (and identically on 3f2220a, before #329
merged), with every app-support seam jailed. The
exception is the history table, whose method is given with it.

---

## 1. Which gate fails

A boundary is matched by `corners._spatial_matches` and becomes a knot of the lap's warp only if it
passes four checks:
- the search arc: ±2 % of the lap around the same fraction;
- the heading gate;
- the 3 m distance gate (`SPATIAL_MATCH_MAX_M`);
- monotonicity against its neighbours.

The probe relaxes one check at a time on the **real** function, then attributes each failure.
Counts are interior boundaries (22 per lap) on every clean lap except the reference. C1's entry and
C12's exit sit on the timing line on both recordings, where the warp is anchored exactly.

| | 0060 (37 × 22) | 0062 (64 × 22) |
|---|---|---|
| matched | **503 (61.8 %)** | 1,404 (99.7 %) |
| failed the search arc | 0 | 0 |
| failed the heading gate | 1 | 0 |
| **failed the 3 m distance gate** | **310 (38.1 %)** | 4 (0.3 %) |
| dropped as non-monotonic | 0 | 0 |
| closest approach the gate judges, median / p90 | **2.44 / 4.98 m** | 0.56 / 1.42 m |

The shipped `CornerModel.lap_corner_resolved` (#329) gives **220 / 456** cells resolved on 0060
and **776 / 780** on 0062. The probe's per-boundary knot rule agrees with it cell for cell, and the
probe refuses to report if it does not.

**The failure is entirely the distance gate.** 0060's laps simply lie farther from the reference
lap's trace. The question is why.

## 2. Candidates tested

| Candidate | Measured | Verdict |
|---|---|---|
| **Fix rate / dropouts** | Both recordings: fix interval median 0.100 s, max 0.101 s; **0** interior gaps > 0.35 s in any clean lap; **0 %** of moving fixes rejected. | **Ruled out.** The two recordings are identical here. |
| **GPS quality (DOP)** | Median DOP **2.13** (p90 2.78) on 0060 vs **1.34** (p90 1.65) on 0062. | **Only at recording level.** Within a recording, DOP near a boundary does not predict its failure. On 0060 the failure rate is 34.7 / 39.4 / 42.2 / **24.5 %** across DOP bands [1.5,2) / [2,2.5) / [2.5,3.5) / ≥3.5. In the [2,2.5) band 0060 fails 39.4 % and 0062 fails 0.0 %. DOP is a symptom of the worse sky, not a usable per-boundary key, which agrees with [`refused-2026-09.md`](refused-2026-09.md) §4. |
| **GNSS position error** | See §3. | **Cause 1.** |
| **The reference lap** | See §4. | **Cause 2.** |
| **Chapter seams** | Seam laps (found from the chapter offsets): 0060 → **24**; 0062 → **21, 46**. Laps within ±1 of a seam match **80.3 %** on 0060 against 60.2 % for the rest, and **100 %** on 0062. | **Ruled out.** Laps next to a seam match *better*. 0060's laps in GX030060 are worse (50.0 %, against 68.4 % in GX020060), but that follows the slow drift in §3, not the seam. |
| **A different racing line** | A rigid whole-lap shift and the altitude cannot be a driving line, yet both scatter 2-4× more on 0060 (§3). | **Not the main cause.** A line component cannot be excluded at single spots (§4). |
| **The 3 m threshold** | §5: 0060 needs **8 m** to reach 98.4 % of cells matched with its own reference. | **0060 is not just outside the gate.** Its distribution is several metres wide. |
| **#300 / #322 / #325** | §6: byte-identical inputs and **591 / 912** boundaries matched in all six trees. | **Ruled out.** |

## 3. Cause 1 — the recording's GNSS error

The **consensus line** is, at every 5 m of the reference lap, the median signed offset of all clean
laps from that reference. Each lap's offsets from the consensus are then fitted with **one rigid 2D
translation**. A shift by one vector in every heading around a closed circuit is a positioning
error, not a line.

| | 0060 | 0062 | ratio |
|---|---|---|---|
| lap vs consensus, RMS (median lap) | **1.66 m** | 0.80 m | 2.1× |
| one rigid translation per lap, \|T\| median / max | **1.34 / 3.65 m** | 0.61 / 1.23 m | 2.2× |
| variance that translation explains | 40 % | 34 % | |
| residual after it, RMS (median lap) | **1.16 m** | 0.61 m | 1.9× |
| **altitude**: per-lap offset from the consensus, sd | **3.23 m** (−6.65…+7.70) | 0.82 m (−1.43…+2.37) | **3.9×** |
| **altitude**: within-lap wander after that offset, median | **2.40 m** | 0.57 m | 4.2× |
| persistence: lag-1 correlation of consecutive laps' translation | +0.41 (shuffle p < 0.001) | +0.45 (p < 0.001) | |
| persistence: lag-1 of consecutive laps' altitude offset | +0.52 (p < 0.001) | +0.60 (p < 0.001) | |

- **The shift persists from lap to lap.** It is a slowly drifting bias, the signature of GNSS error.
  0062 has the same kind of error, only half as large.
- **The altitude is the independent witness.** A kart's line cannot move it by metres, and it
  scatters about four times as much on 0060, both between and within laps.
- **Removing each lap's rigid translation relative to the reference lifts 0060 from 61.8 % to
  85.0 %** of interior boundaries matched (§5).
- **The laps that fail most are the ones shifted farthest from the reference.** Per-lap failure
  share against |T_lap − T_reference| gives r = **+0.76** on 0060. The five worst laps (35, 33, 34,
  29, 32) match 4-8 of 22 interior boundaries and sit 3.2-5.9 m from the reference by that shift
  alone.

## 4. Cause 2 — the reference lap is the fastest lap, and on 0060 it is displaced

`CornerModel` anchors every lap's match on the **best** lap's trace, because the corner windows
are expressed in that lap's odometer. On 0060 that is **lap 17** (68.228 s).

| | 0060 | 0062 |
|---|---|---|
| reference lap | 17 | 41 |
| its median \|offset\| from the consensus | **1.75 m** (rank **34 / 38**) | 0.27 m (rank 3 / 65) |
| its own rigid translation | **2.31 m** | 0.13 m |
| cells matched with each clean lap as reference, min / median / max | 35.6 / 65.7 / 77.5 % | 86.5 / 98.0 / 99.5 % |
| **the app's reference ranks** | **35 / 38 (45.5 %)** | 3 / 65 (99.3 %) |

**The two worst boundaries on 0060 are the reference lap's own excursion.**
- C8's exit fails on **81 %** of laps and C9's entry on **76 %**. With the reference lap itself
  counted, C9's entry matches on 10 of 38 laps, which is #330's 26 %.
- The lap-to-lap scatter there is ordinary: 2.03 and 1.92 m, against 1.93 m for the whole lap.
- Lap 17, however, sits **+4.8 m / +4.5 m** from the consensus at those two boundaries:

| boundary | offset | rigid translation | local | altitude vs consensus (whole lap +4.5 m) | speed vs consensus |
|---|---|---|---|---|---|
| C8 exit | +4.8 m | +2.2 m | +2.5 m | −2.5 m | +2.9 km/h |
| C9 entry | +4.5 m | +2.3 m | +2.3 m | −2.3 m | +3.9 km/h |

**What this cannot separate:** whether the local ~2.4 m is lap 17's GNSS error, or the fastest
lap taking a different line out of C8.
- *GNSS:* its altitude swings about 7 m within the lap.
- *A different line:* it carries 3-4 km/h more speed through there.

Either way, one lap's position there decides the match for every other lap.

## 5. Counterfactuals on the shipped 3 m gate

Interior boundaries matched, every clean lap except the reference:

| | 0060 | 0062 |
|---|---|---|
| as shipped | **61.8 %** | 99.7 % |
| each lap's rigid translation relative to the reference removed | **85.0 %** | 100.0 % |
| reference = the lap nearest the consensus (0060 lap 4, 0062 lap 30) | **85.9 %** | 99.6 % |
| both | **92.5 %** | 100.0 % |

Cells with both edges matched, against the distance threshold:

| threshold | 2 m | **3 m** | 4 m | 5 m | 6 m | 8 m |
|---|---|---|---|---|---|---|
| 0060, reference lap 17 (shipped) | 19.8 % | **45.5 %** | 68.9 % | 84.7 % | 92.3 % | 98.4 % |
| 0060, reference lap 4 | 57.7 % | 77.5 % | 91.4 % | 95.9 % | 98.0 % | 99.3 % |
| 0062, reference lap 41 (shipped) | 90.9 % | **99.3 %** | 100 % | 100 % | 100 % | 100 % |

These are **match rates, not accuracy**:
- a rate is not evidence that the matched edges are right;
- nothing here was scored against an independent timing truth;
- none of it is built.

## 6. #300, #322 and #325 changed nothing about the match

The `studio/` tree was exported (`git archive <commit> studio`) at each commit below. Each tree
loaded both recordings through its own `Session.load`, against the current compiled bindings. The
only change under `pacer/` or `bindings/` since before #300 is 4c1cf15, which touches comments and
docstrings only.

Each tree ran with `HOME` pointed at a throwaway directory, and its own `_jail`, whose diversion was
asserted before loading. Matches use the current `corners._spatial_matches`. Its logic and constants
are unchanged across these trees.

| tree | 0060 best / clean | 0060 boundaries matched | 0060 cells on warp knots | 0060 laps with no warp | 0062 boundaries matched |
|---|---|---|---|---|---|
| before #300 (ac3e22d) | 17 / 38 | 591 / 912 | 84 / 456 | **22** | 1,555 / 1,560 |
| after #300 (2251f8a) | 17 / 38 | 591 / 912 | 220 / 456 | 0 | 1,555 / 1,560 |
| before #322 (48f330d) | 17 / 38 | 591 / 912 | 220 / 456 | 0 | 1,555 / 1,560 |
| after #322 (ed31e1c) | 17 / 38 | 591 / 912 | 220 / 456 | 0 | 1,555 / 1,560 |
| before #325 (e6dbe10) | 17 / 38 | 591 / 912 | 220 / 456 | 0 | 1,555 / 1,560 |
| after #325 (c117967) | 17 / 38 | 591 / 912 | 220 / 456 | 0 | 1,555 / 1,560 |

**What stayed identical in every tree, on both recordings:**
- the lap columns (x, y, odometer), byte for byte;
- the corner partition;
- the clean-lap set, the best lap and the start line.

**#300 changed only whether a lap's matches are used, never whether they succeed.** Before it, 22
of 0060's laps (54 of 0062's) skipped the warp and kept the normalized projection outright. #322 and #325 moved nothing on
either D24 recording.

## 7. What would change this, and what was not done

- **Raising `SPATIAL_MATCH_MAX_M` is not supported by this evidence.** 0060 needs 8 m to reach the
  rate 0062 gets at 3 m, and nothing here measures whether an edge matched at 4-8 m is still
  accurate. #329's line-crossing timing check is the test such a change would have to pass.
- **A spatial anchor other than the fastest lap** is the largest single lever measured: the lap
  nearest the consensus lifts 0060 from 61.8 % to **85.9 %** and leaves 0062 where it is (99.7 → 99.6 %). It is not a
  one-line change, for two reasons:
  - the corner partition lives in the best lap's odometer, so the anchor lap needs its own mapping
    back to it;
  - it would move every corner, ideal-lap and coaching figure on 0060.
  It needs its own measured package, scored against timing truth.
- **Compensating each lap's rigid shift before the gate** gives 85.0 %, and **92.5 %** combined with
  the anchor change. The same caveat applies.
- **Nothing in any shipped recording separates a GNSS excursion from a line change at one spot**
  (§4). A recording with an independent position reference, such as RTK, could.

## Reproduce

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p6_corner_match_cause
    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p6_corner_match_cause 0060
