# Upstream "~20 ms vs transponder" claim — investigation & verdict

**Branch:** `investigate-20ms-claim` (off `studio-gps-accuracy-and-polish`). **Date:** 2026-06.
**Question (from the brief):** the original author of the upstream repo we forked
([`dendi239/pacer`](https://github.com/dendi239/pacer)) is said to claim, in
`notebooks/interpolation.ipynb`, a **~20 ms difference between GPS-derived lap times and the
real lap times measured by a transponder**. Is the claim true; what does it mean (mean bias or
per-lap); what method produces it; and is THIS repo missing something the author did?

---

## TL;DR verdict

1. **The literal "~20 ms vs transponder" claim does not exist in the upstream notebook, its
   outputs, the upstream README, or anywhere in the upstream repo's git history.** I read the
   raw `.ipynb` JSON (source + rendered outputs), the second notebook `dat-files.ipynb`, the
   README at HEAD and at the interpolation commit, and grepped every blob in the upstream
   history. There is **no** `transponder`, no `20 ms`/`20ms`, no `0.02`, no "real/actual/measured
   lap time", and **no markdown cells at all** in the upstream notebook. The "20 ms" is the
   brief's paraphrase, not a verbatim upstream statement. So the claim **as stated cannot be
   verified against an upstream source — because the author never made it in writing.**

2. **The upstream notebook has no transponder ground truth.** It cannot be a transponder
   comparison: the author never had transponder data. What the notebook actually does is compare
   GPS-derived laps **against each other** (a delta-vs-reference-lap curve) and eyeball the
   *noise* in that delta. The only quantitative timing artefacts in the upstream notebook are the
   six lap times (68.85–71.48 s, one incomplete) and an inter-lap delta whose per-point **std is
   ~0.15 s** (decoded from the saved plotly figure, cell 12) — i.e. ~150 ms scatter, an order of
   magnitude **larger** than "20 ms", and it's lap-to-lap, not lap-to-truth.

3. **The upstream "interpolation" is a per-sample-timestamp RECOVERY technique for data that has
   no per-sample clock — the GPS5-era situation.** It fits `{phase, frequency}` so that
   `t[i] = phase + (cumsum(di)-1)/frequency` stays inside each **video-payload time span**
   `[in,out]` (≈ 1 s holding ~10–18 GPS samples). It is needed precisely because the only timing
   the author trusts is the coarse 1-second payload bound, and the per-fix times must be invented.

4. **Our GPS9 stream carries the true per-fix wall-clock directly** — verified: **100.0 % of fixes
   on BOTH recordings carry a GPS9 `timestamp_ms`, at a clean 10.000 Hz.** So the recovery the fit
   exists to do is already done by the hardware. GPS9 **supersedes** the interpolation.

5. **Tested empirically, out of sample, against the transponder on BOTH recordings:** the Adam
   interpolation **matches GPS9 on the clean recording (0062) and diverges catastrophically on
   the noisier one (0060)** — the exact single-dataset-instability failure mode the team has been
   burned by twice. **It never beats GPS9 on either recording.** We are not missing anything;
   dropping it from the default path was correct.

6. **Ran the notebook's OWN lap-timing code (not just our port), lap-by-lap vs GPS9 vs the
   transponder, on BOTH recordings (§4b).** Our C++ port is a **bit-faithful** copy of the
   notebook's parametric **t2** fit (parity max |Δt| = 3.6e-15 s). With the lap segmentation held
   constant so only the time axis varies, the notebook's t2 **matches GPS9 on 0062 (clean std
   0.0527 s = GPS9's) and diverges on 0060 (std 0.264 s; breaks one lap to 64.4 s)** — identical to
   the port, as it must be. The notebook's other variant, the free per-sample **t1** (which the
   port never implemented), is more robust on 0060 (std 0.098 s) but is **still noisier than GPS9
   (0.087 s)**. **Neither notebook method beats GPS9 on either recording.** The author's `GH010251`
   is unavailable, so this is the notebook's *method* on our files — the only place transponder
   truth exists.

**Apples-to-apples:** our validated GPS9 timing is **mean +0.0015–0.0030 s (≈1–3 ms), median
≈+0.001 s, std 0.053–0.087 s** vs the real transponder. If "20 ms" were a *mean/median bias*
claim, **we already beat it by ~10×** (we're at 1–3 ms). If it were a *per-lap* figure, no method
including the author's gets near 20 ms per lap on this 10 Hz data — the per-lap std floor is
50–90 ms, set by GPS positional noise (DOP), and the author's own inter-lap scatter is ~150 ms.

---

## 1. What the upstream notebook actually contains (read verbatim, not guessed)

Sources fetched (sandbox curl, host `raw.githubusercontent.com` / `api.github.com`):
`raw.githubusercontent.com/dendi239/pacer/main/notebooks/interpolation.ipynb`,
`.../dat-files.ipynb`, `.../README.md`, the GitHub commits API, and a shallow clone
(`git grep` over `git rev-list --all`).

**Data:** three GoPro **Hero** chapters `GH010251 / GH020251 / GH030251.MP4`
(`/Users/denys/Pictures/…`), only the first is loaded (the multi-file source is commented out).
Samples are filtered to `full_speed > 3` (moving only). The session yields **6 laps** (cell 11
output, verbatim):

```
   lap   lap_time
0    0  71.481822
1    1  70.374183
2    2  69.889058
3    3  69.039946
4    4  68.854761
5    5  70.615707
6    6   0.000000   # incomplete trailing lap
```

**No transponder.** **No markdown.** The "comparison" cells (12, 13) compute, for each lap, the
**delta vs a reference GPS lap** on a common distance grid (`reference_lap.resample(...)`), and
plot it. Decoding the saved plotly typed-arrays from the committed outputs:

| upstream cell | what it plots | decoded stats |
|---|---|---|
| 12 | per-point Δt of each lap vs reference | mean ≈ −0.055 s, **std ≈ 0.151 s**, |Δ| p99 ≈ 0.32 s |
| 13 | Δt vs lap-5 reference (different ref) | std 0.42–0.75 s (resample artefacts on slow laps) |
| 15 | histogram of all Δt | spread −0.84 … +1.96 s |
| 40–43 | Δt first-difference, quantised at `c=0.1 s`, "cumulative noise" | (figures not re-executed in the saved file; the `c=0.1` step is the author's eyeballed noise grain) |

So the only "≈ms"-scale number the author works with is a **noise grain of ~0.1 s** (cell 41,
`c = 0.1`) and an inter-lap **delta std of ~0.15 s**. Nothing is 20 ms, and nothing is measured
against a transponder.

**The method (cells 4–10), faithfully:**
- `rough_frequency = #samples / #distinct payload-spans` (≈ samples per 1-s video payload ≈ 10–18).
- `di[i] = round( distance(s[i-1],s[i]) / avg_speed × rough_frequency )` — expected #sample-steps
  between consecutive GPS fixes from how far the kart moved.
- `floor/ceil = ` the **video payload time span** `gpmf.current_time_span()` for each sample's
  payload (this is `GetPayloadTime()` — the MP4 chunk's `[in,out]`, **not** a per-fix clock).
- **t1** = free per-sample optimisation (Adam on every `t[i]`), loss = spacing-variance +
  `[floor,ceil]` violation.
- **t2** = the **parametric** fit: `t = phase + (cumsum(di)-1)/frequency`, Adam on just
  `{phase, frequency}`. This is what our C++ `pacer::InterpolateTimestamps` reimplements.

The whole construction exists to **assign a timestamp to each GPS sample when the only timing
you have is the ~1-second video payload bound.** That is the classic GoPro **GPS5** problem: GPS5
carries one ASCII `GPSU` time per payload, not per sample (confirmed in upstream
`gps-source.cpp` — the `GPS5` branch stamps every sample in a payload with the *same* `GPSU`
timestamp; only the `GPS9` branch computes a true per-fix `days-since-2000 + secs-since-midnight`).

## 2. Our notebook vs upstream (the fork diff) + how it entered our repo

Our `notebooks/interpolation.ipynb` (since removed) was the upstream one, reworked — confirmed by `git log`:
the lineage is upstream `22c03f9 add interpolation using gradient descent` → our
`c2eb048 run against a local GoPro clip` → `30f8ee8 fix all cells` → `43476f8 reasonable output`
→ `ed08ed6 reduce GPS measurement noise in the lap graphs`. Substantive changes ours made:

- **`import bindings.pacer` → `from pacer import …`** (our packaged bindings).
- **Data swapped** to our `…/D24/GX010060.MP4` (GPS9), single file.
- **`di` clamped to ≥ 1** (`np.maximum(di, 1)`) — upstream divided by `di` with possible 0 → NaN.
- **Added `_smooth` (boxcar window 9)** and a distance-grid `delta_table` with `np.interp`
  alignment + a "clean lap" band filter and a where-time-is-lost map — none of which is upstream.
- Otherwise the t1/t2 Adam machinery is the same.

The author's interpolation was also ported to C++ in our repo (`pacer/interpolation/`,
commits `d34cbf0 / e2f7ce9 / bd2380e`) as `pacer::InterpolateTimestamps` (the **t2** parametric
fit, analytic gradient, torch-parity tested) and exposed to Python as a timestamp-fit binding.
At the time it was wired into `Session.load(..., interpolate=True)` behind an opt-in `--interp`
flag and **validated → rejected back to naive** if the result was non-monotonic or ran past the
video duration. **That entire path (module, binding, and `--interp` plumbing) has since been
removed** — GPS9's true per-fix clock supersedes it; what follows is the evidence that led to that.

## 3. The crux: GPS5-era recovery vs our GPS9 true clock (verified)

The brief's key hypothesis — *the interpolation is a GPS5-era technique made unnecessary by GPS9*
— is **confirmed empirically**:

```
GX010060: have_GPS9_ts = 100.0%   median dt = 0.1000 s -> 10.000 Hz
GX010062: have_GPS9_ts = 100.0%   median dt = 0.1000 s -> 10.000 Hz
```

Both recordings carry the **true per-fix GPS wall-clock on every sample** at a dead-clean
10.000 Hz. Our `session._gps9_times` uses that spacing directly (re-anchored per contiguous run
to the media clock for video sync). The Adam fit's entire job — invent per-sample times inside a
1-second payload box — is moot when each sample already states its own GPS time to the
millisecond. **GPS9 supersedes the interpolation; the author was solving a problem we don't have.**

(For completeness: the residual media-clock-vs-GPS9 rate over a whole session is only **+17…+21
ppm ≈ 1.2–1.5 ms per 69-s lap** end-to-end here; the larger "~30 ms" figure in `session.py`'s note
is the *within-run* drift before per-run re-anchoring. Either way it is a systematic *bias* that
GPS9 removes — it is not "20 ms of transponder error".)

## 4. Empirical test — the author's interpolation vs GPS9 vs the transponder, BOTH recordings

Harness (as run at the time): a `validate_interp.py` that reused `studio.dev._validate_wallclock`'s
pure alignment helpers verbatim; the only change was `Session.load(interpolate=True)` vs the default.
Transponder ground truth = the Daytona-24h CSV; alignment = the same duration-correlation lock.
**NOTE:** the C++ Adam interpolation path (and `validate_interp.py`) have since been removed — this
section is the historical record of why. The numbers below are unchanged.

| recording | timing | align corr | clean n | mean | median | **std** | RMS | k_fit |
|---|---|---|---:|---:|---:|---:|---:|---:|
| **0060** | GPS9 (ship) | **0.9917** | 48 | +0.0030 | +0.0009 | **0.0871** | 0.0872 | 0.99995 |
| **0060** | Adam interp | **0.6816** | 39 | −0.6079 | −0.6833 | **0.3895** | 0.7220 | 1.00881 |
| **0062** | GPS9 (ship) | **0.9965** | 59 | +0.0015 | +0.0010 | **0.0527** | 0.0527 | 0.99998 |
| **0062** | Adam interp | **0.9956** | 59 | +0.0093 | +0.0077 | **0.0564** | 0.0571 | 0.99987 |

**Reading it:**
- **0060 (noisier GPS — 4.4 % fixes gated, dropouts):** the interpolation **diverges**. It
  *compresses* lap times by ~0.9 % (k_fit 1.0088), drives the clean-lap mean to **−0.61 s**, the
  std to **0.39 s** (4.5× worse than GPS9), and the transponder-alignment correlation **collapses
  0.99 → 0.68**. Concretely it **breaks lap 3 to 64.09 s** (truth 68.94 s; GPS9 gives 68.80 s):
  a **−4.85 s** error vs GPS9's −0.14 s. This is exactly the "broke lap 3 → ~64 s / compresses
  lap times" behaviour the brief recalled.
- **0062 (cleaner GPS — 1.0 % gated):** the interpolation **engages** (its axis differs from both
  naive and GPS9 by ≤0.29 s; it is *not* silently rejected) and **converges to essentially GPS9**
  — clean std 0.0564 vs 0.0527, median +0.0077 vs +0.0010. It matches but does **not beat** GPS9.

**This is the classic single-dataset-instability trap.** Had we only looked at 0062 we might have
said "the interpolation is fine / equivalent." Cross-checking on 0060 shows it is unstable: the
constant-frequency parametric model fits a clean 10 Hz stream well but cannot cope with the
noisier stream's dropouts/gated fixes, where it warps the timeline. GPS9 true-clock is stable on
**both**. (Same out-of-sample discipline that killed the clock-rate factor and the Doppler-RTS
smoother in `gps-accuracy-research.md`.)

## 4b. Side-by-side: the notebook's OWN lap-timing pipeline vs ours

§4 tested *our C++ port* (`Session.load(interpolate=True)`). It did **not** run the notebook's
own Python code. This section closes that gap: it runs the **notebook's literal PyTorch
optimizers** and lines the notebook-generated lap times up against our GPS9 timing and the
transponder, lap by lap, on **both** recordings.

**Harness:** `studio/dev/research/notebook_vs_gps9.py`. The `notebook_t1` (free
per-sample Adam) and `notebook_t2` (parametric `{phase, frequency}` Adam) functions are copied
**verbatim** from `notebooks/interpolation.ipynb` cells `4c1dba4b` and `31c96b74` — same loss
(`spacing + [floor,ceil]` constraints), same `[1e-1,1e-2,1e-3]` LR schedule, fresh `torch.optim.Adam`
per rate, same `di = max(round(dist/avg_speed·rough_freq), 1)` and the `floor/ceil = payload span`.
GH010251 (the author's file) is unavailable, so we run the notebook's **method** on our
recordings — the only place transponder truth exists anyway.

**Apples-to-apples (only the time axis varies):** one common cleaned+smoothed sample set and one
common shipping track-aware start/finish line are built once; the SAME samples are added to three
`pacer.Laps` whose only difference is the per-sample TIME (notebook-t1 / notebook-t2 / GPS9). The
script asserts the per-lap **sample membership is identical** across all three (it is —
`pacer.Laps` segments by *geometry*, crossing the line, not by time), so `lap_time(i)` — a
difference of two crossing instants — is the only thing that moves. The transponder alignment is
the same duration-correlation lock as §4 (`corr 0.9917` on 0060 → CSV 302–358; `0.9965` on 0062 →
CSV 856–920).

### Faithfulness verdict: YES, our C++ port == the notebook's t2

The C++ `pacer::InterpolateTimestamps` is a **bit-faithful** re-implementation of the notebook's
**t2** parametric fit. `tests/test_interpolation_parity.py` (re-run here) reproduces the notebook's
exact torch t2 in float64 and the C++ result agrees to **max |Δt| = 3.6e-15 s**, phase/frequency to
< 1e-6. Same model `t[i]=phase+(cumsum(di)−1)/freq`, same loss, same per-rate Adam reset; the C++
gradient is analytic (the spacing term is identically zero under this model, so only the
`[floor,ceil]` term drives the fit). The one notebook detail — `di.long().cumsum()` truncates to
int before the cumsum — is a **no-op** because `di` is already `round(...)≥1` (whole numbers), so
parity holds on real data too. **Difference from the notebook: the C++ port implements only t2.**
The notebook *also* has **t1** (the free per-sample optimiser), which the port never exposed; t1 is
the more robust of the two but is not in the shipping path.

### Per-lap residuals vs the transponder, SAME segmentation (clean racing laps)

| recording | timing | clean n | mean | median | **std** | rms |
|---|---|---:|---:|---:|---:|---:|
| **0060** | notebook **t1** (free) | 48 | +0.0080 | −0.0086 | 0.0979 | 0.0982 |
| **0060** | notebook **t2** (parametric ≡ our C++ port) | 48 | −0.1216 | −0.1963 | **0.2644** | 0.2911 |
| **0060** | **our GPS9** (ship) | 48 | +0.0030 | +0.0009 | **0.0871** | 0.0872 |
| **0062** | notebook **t1** (free) | 59 | +0.0024 | −0.0089 | 0.0645 | 0.0645 |
| **0062** | notebook **t2** (parametric ≡ our C++ port) | 59 | −0.0071 | −0.0076 | **0.0527** | 0.0532 |
| **0062** | **our GPS9** (ship) | 59 | +0.0015 | +0.0010 | **0.0527** | 0.0527 |

Example rows (per-lap, seconds; `Δ = method − transponder`):

```
0060   csv  transp   nb_t1   nb_t2    gps9   t1-tr   t2-tr   g9-tr   flag
       302  70.538  70.468  71.527  70.464  -0.070  +0.989  -0.074  clean
       305  68.937  68.816  64.434  68.801  -0.121  -4.503  -0.136  dropout  <- t2 BREAKS this lap
       314  69.743  69.920  69.808  69.938  +0.177  +0.065  +0.195  clean
       358  76.832  76.794  76.564  76.817  -0.038  -0.268  -0.015  pit/slow

0062   csv  transp   nb_t1   nb_t2    gps9   t1-tr   t2-tr   g9-tr   flag
       856  69.726  69.697  69.718  69.727  -0.029  -0.008  +0.001  clean
       861  71.406  71.487  71.407  71.416  +0.081  +0.001  +0.010  clean
       877  68.789  68.848  69.305  68.877  +0.059  +0.516  +0.088  dropout
       920  68.829  68.881  68.804  68.812  +0.052  -0.025  -0.017  clean
```

### Reading it — the decisive answers

- **Do the notebook lap times differ from ours, and by how much?** On **0062 (clean GPS)** they
  are **the same to ~1 ms**: notebook-t2 clean std 0.0527 s vs GPS9 0.0527 s (identical to 4 dp),
  notebook-t1 0.0645 s; per-lap the three agree within a few ms on every racing lap. On **0060
  (noisier GPS)** the notebook's **t2 diverges**: it compresses laps (fit frequency 10.03 vs the
  true 10.0 Hz), pushing the clean median to −0.196 s and the std to **0.264 s (3× GPS9)**, and it
  **breaks the lap at CSV 305 to 64.4 s** (transponder 68.94, GPS9 68.80) — a **−4.5 s** error.
  This is the *same* divergence §4's C++ `--interp` showed, as it must be (t2 ≡ the C++ port).
  The notebook's **t1** is far more robust on 0060 (clean std 0.0979 s) but is still **noisier
  than GPS9** (0.0871 s).
- **Is the notebook's method CLOSER to the transponder than our GPS9 timing?** **No — on neither
  recording.** Best case (0062) t2 *ties* GPS9 (0.0527 = 0.0527); t1 is slightly worse. Worst case
  (0060) both notebook axes are worse than GPS9 and t2 diverges. **GPS9 has the lowest std on both
  recordings.** Out of sample, the notebook's own code never beats our shipping timing.
- **What was actually executed, on what data:** the notebook's literal t1+t2 PyTorch optimizers,
  on both `GX0060` and `GX0062` (`--full`, all 3 chapters each), with the lap segmentation held
  constant. The author's `GH010251` is **unavailable**, so the author's exact 6-lap numbers cannot
  be reproduced; this is the notebook's *method* on our files (where transponder truth exists). For
  honesty the script also prints the notebook's **NATIVE** output (its own raw `full_speed>3`
  preprocessing + its own `pick_random_start()`, no gate/clean/smooth) — it runs end-to-end on our
  data and yields a plausible ~69-s lap list; that native t2 list is *not* transponder-aligned (a
  short random start line mis-segments), which is exactly why we hold a good shipping segmentation
  constant for the scored comparison.

**Net:** running the notebook's own pipeline confirms §4 rather than overturning it. Because the
notebook's t2 *is* our C++ port (parity 3.6e-15), the side-by-side is equivalent to the prior
`--interp`-vs-GPS9 result — t2 matches GPS9 on 0062 and diverges on 0060. The notebook's *t1*
variant (not ported) is more stable than t2 on 0060 but still does not beat GPS9 on either file.

## 5. Verdict

- **Is the "20 ms vs transponder" claim true?** It is **not a real upstream statement** — the
  author never wrote it and never had a transponder. Taken charitably as "how close is GPS lap
  timing to truth":
  - **As a mean/median bias:** we are at **1–3 ms** vs the real transponder on both recordings —
    we already beat a hypothetical 20 ms by ~10×, with GPS9, no interpolation needed.
  - **As a per-lap figure:** nobody reaches 20 ms/lap on 10 Hz consumer GPS; the floor is the
    **50–90 ms** positional-noise std (DOP-set), and the author's own *inter-lap* scatter is
    **~150 ms**. 20 ms per lap is not achievable from this data by any method here.
- **Are we missing something the author did?** **No.** The author's interpolation is a
  per-sample-timestamp *recovery* method for coarse payload-bounded (GPS5-style) data. Our GPS9
  stream provides the true per-fix clock on 100 % of fixes at 10.000 Hz, so the recovery is
  unnecessary — and when forced on, it **matches GPS9 at best (0062) and diverges badly at worst
  (0060)**. We did not wrongly abandon a beneficial technique; **GPS9 supersedes it.** Keeping it
  opt-in (`--interp`, auto-rejected on divergence) is the right call; it stays useful only as a
  fallback for a genuinely GPS5-only clip with no per-sample timestamps.

## 6. Recommendation

**Adopt nothing new; GPS9 already matches/supersedes the upstream technique — with evidence.**
- Keep GPS9 true-clock as the default (mean 1–3 ms vs transponder, stable on both recordings).
- Keep the Adam interpolation **opt-in only** as the GPS5-only fallback it actually is; the
  `_interpolated_or_naive` reject-on-divergence guard is appropriate but note it did **not** catch
  the 0060 divergence (the warped axis is still monotonic and within duration) — so it must never
  be on by default for GPS9 data. (Optional hardening, low priority: also reject interp when its
  median lap time deviates > a few % from the GPS9 median, since divergence shows up as
  compression, not as non-monotonicity.)
- No change to shipped timing code is warranted by this investigation.

## 7. Reproduce

> Historical: the `interpolate=True` / `validate_interp.py` / `test_interpolation_parity.py` steps
> below exercised the C++ Adam interpolation path, which has since been removed. They are kept to
> document how the §4/§4b numbers were produced. The GPS9 baseline step still runs.

```bash
# upstream sources (sandbox-allowed hosts)
curl -sL https://raw.githubusercontent.com/dendi239/pacer/main/notebooks/interpolation.ipynb -o /tmp/claude/upstream_interpolation.ipynb
python3 /tmp/claude/decode_figs.py /tmp/claude/upstream_interpolation.ipynb 11 12 13 15   # decode plotly typed-arrays

# GPS9 baseline (shipping) — per recording
pixi run python -m studio.dev._validate_wallclock -- /path/GX010060.MP4 "<transponder.csv>" \
    --race-start "2026-05-23 12:00:00Z" --dump /tmp/claude/baseline_0060.json

# GPS9-vs-our-C++-port comparison (Session.load interpolate=True): the validate_interp.py validator
# that produced §4's numbers was removed together with the C++ interpolation experiment (see AGENTS.md).

# §4b — the NOTEBOOK's OWN t1/t2 PyTorch pipeline vs GPS9 vs transponder, segmentation held
# constant (only the time axis varies). OMP_NUM_THREADS=1 avoids a torch OpenMP tmp stall.
OMP_NUM_THREADS=1 PYTHONPATH=. pixi run python -u \
    studio/dev/research/notebook_vs_gps9.py /path/GX010060.MP4 "<csv>" \
    --race-start "2026-05-23 12:00:00Z" --dump /tmp/claude/notebook_vs_gps9_0060.json
OMP_NUM_THREADS=1 PYTHONPATH=. pixi run python -u \
    studio/dev/research/notebook_vs_gps9.py /path/GX010062.MP4 "<csv>" \
    --race-start "2026-05-23 12:00:00Z" --dump /tmp/claude/notebook_vs_gps9_0062.json

# faithfulness: C++ port == the notebook's t2 (max |Δt| ~3.6e-15 s)
pixi run python tests/test_interpolation_parity.py

# GPS9-timestamp presence / rate sanity
PYTHONPATH=. pixi run python -c "see §3"
```

Scripts (committed under `studio/dev/research/`): **`notebook_vs_gps9.py`** (§4b — the notebook's own
t1/t2 PyTorch optimizers vs GPS9 vs transponder, segmentation held constant) and
`decode_upstream_figs.py` (decodes the upstream plotly base64 typed-arrays). `validate_interp.py`
(§4 — GPS9 vs our C++ port) was **removed** along with the interpolation path it tested. The
transponder CSV and all `/tmp` dumps are **inputs/scratch only — never committed.**

## Sources
- [Upstream notebook (raw)](https://raw.githubusercontent.com/dendi239/pacer/main/notebooks/interpolation.ipynb)
  and [dat-files.ipynb](https://raw.githubusercontent.com/dendi239/pacer/main/notebooks/dat-files.ipynb)
- [Upstream README](https://github.com/dendi239/pacer/blob/main/README.md) (no timing-accuracy claim)
- [Upstream gps-source.cpp](https://github.com/dendi239/pacer/blob/main/pacer/gps-source/gps-source.cpp)
  (GPS5 = one GPSU per payload; GPS9 = true per-fix `timestamp_ms`)
- Our `studio/docs/gps-accuracy-research.md` (the 1–3 ms / 50–90 ms floor, out-of-sample discipline)
- Our `studio/session.py` `_gps9_times` (GPS9 true-clock) and `_interpolated_or_naive` (opt-in Adam)
- Our `pacer/interpolation/interpolation.{hpp,cpp}` (the C++ port of the author's t2 fit)
