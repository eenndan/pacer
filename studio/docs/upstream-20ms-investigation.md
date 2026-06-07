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

Our `notebooks/interpolation.ipynb` is the upstream one, reworked — confirmed by `git log`:
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
fit, analytic gradient, torch-parity tested) and exposed to Python as
`pacer.interpolate_timestamps`. It is wired into `Session.load(..., interpolate=True)` behind the
opt-in `--interp` flag and **validated → rejected back to naive** if the result is non-monotonic
or runs past the video duration (`Session._interpolated_or_naive`).

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

Harness: `/tmp/claude/validate_interp.py` (reuses `studio._validate_wallclock`'s pure
alignment helpers verbatim; the only change is `Session.load(interpolate=True)` vs the default).
Transponder ground truth = the Daytona-24h CSV; alignment = the same duration-correlation lock.

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

```bash
# upstream sources (sandbox-allowed hosts)
curl -sL https://raw.githubusercontent.com/dendi239/pacer/main/notebooks/interpolation.ipynb -o /tmp/claude/upstream_interpolation.ipynb
python3 /tmp/claude/decode_figs.py /tmp/claude/upstream_interpolation.ipynb 11 12 13 15   # decode plotly typed-arrays

# GPS9 baseline (shipping) — per recording
pixi run python -m studio._validate_wallclock -- /path/GX010060.MP4 "<transponder.csv>" \
    --race-start "2026-05-23 12:00:00Z" --dump /tmp/claude/baseline_0060.json

# GPS9-vs-Adam-interpolation comparison, same alignment, BOTH recordings
PYTHONPATH=. pixi run python /tmp/claude/validate_interp.py /path/GX010060.MP4 "<csv>" \
    --race-start "2026-05-23 12:00:00Z" --dump /tmp/claude/interp_cmp_0060.json
PYTHONPATH=. pixi run python /tmp/claude/validate_interp.py /path/GX010062.MP4 "<csv>" \
    --race-start "2026-05-23 12:00:00Z" --dump /tmp/claude/interp_cmp_0062.json

# GPS9-timestamp presence / rate sanity
PYTHONPATH=. pixi run python -c "see §3"
```

Scripts used (kept under `/tmp/claude`, not committed): `validate_interp.py` (the comparison
harness), `decode_figs.py` (decodes the upstream plotly base64 typed-arrays). The transponder CSV
and all `/tmp` dumps are **inputs/scratch only — never committed.**

## Sources
- [Upstream notebook (raw)](https://raw.githubusercontent.com/dendi239/pacer/main/notebooks/interpolation.ipynb)
  and [dat-files.ipynb](https://raw.githubusercontent.com/dendi239/pacer/main/notebooks/dat-files.ipynb)
- [Upstream README](https://github.com/dendi239/pacer/blob/main/README.md) (no timing-accuracy claim)
- [Upstream gps-source.cpp](https://github.com/dendi239/pacer/blob/main/pacer/gps-source/gps-source.cpp)
  (GPS5 = one GPSU per payload; GPS9 = true per-fix `timestamp_ms`)
- Our `studio/docs/gps-accuracy-research.md` (the 1–3 ms / 50–90 ms floor, out-of-sample discipline)
- Our `studio/session.py` `_gps9_times` (GPS9 true-clock) and `_interpolated_or_naive` (opt-in Adam)
- Our `pacer/interpolation/interpolation.{hpp,cpp}` (the C++ port of the author's t2 fit)
