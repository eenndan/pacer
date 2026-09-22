"""P14 — engine RPM from the audio track: a spike, not a feature (backlog M3, §6).

The market research proposed reading engine RPM off the audio's tonal fundamental and said itself
that it "would not bet" on it: a GoPro on a kart records wind noise, and the camera rides its own
automatic gain control. This probe asks the question in the only order that can be believed.

  1. A KNOWN SIGNAL FIRST (`synthetic`). An engine-like harmonic tone whose fundamental follows a
     kart-like speed trace, buried in wind noise that grows with speed and gusts, then put through
     an automatic gain control, a hard limiter and the AAC codec the camera records with (ffmpeg,
     pipes only). The estimator must recover the PLANTED fundamental across the realistic range
     (22-300 Hz: a four-stroke single's firing order to a two-stroke's) before anything it says
     about real footage means anything. Its abstention threshold is fixed here, on wind with no
     engine in it, and never re-tuned on footage.
  2. REAL FOOTAGE SECOND (`real`). The same estimator, frozen, on the owner's present recordings,
     against the one thing that must hold if it is hearing a single-speed kart's engine: with the
     clutch locked, engine speed is proportional to road speed. So the tone's frequency over GPS
     speed must be ONE constant per recording, and the audio must sit on the picture's clock.
  3. THE GEARING CHECK. The gearing arithmetic (`studio.gearing`) turns trap speed, sprockets and
     tyre circumference into RPM. It is the only independent check of an audio RPM, and it needs
     a session record — so this probe counts on how many recordings both can be computed, and
     then asks the weaker question it CAN answer: which ordinary sprocket pairs reproduce the
     measured tone under each reading of which engine order the tone is.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p14_rpm_audio synthetic
    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p14_rpm_audio real \\
        [0064 0065 0068 0067] [--dump DIR [--reuse]]

The verdict is `studio/docs/refused-2026-09.md` ("Engine RPM from the audio track").

SAFETY, enforced rather than promised:
  * EVERY FOOTAGE FOLDER IS READ-ONLY. A recording is only ever an ffmpeg INPUT (`-i <path>`), and
    the one output ffmpeg is ever given is `pipe:1` — stdout. `-y` is never passed, so even a
    mistaken output argument could not overwrite anything. Every folder is size/mtime-snapshotted
    before and after each recording is read, and the run aborts if one moved.
  * `studio.dev._jail` diverts every app-support seam before any load, and the real app-support
    directory is snapshotted before and after the run and must come back unchanged. The owner's
    `session_records.json` is READ in place (that is the question in step 3), never written.
  * Nothing is written anywhere except into the directory `--dump DIR` names (a JSON of the
    per-recording numbers, and each recording's extracted arrays so `--reuse` can skip the load).
    No step picks a file name of its own under the shared temp root, and ffmpeg writes no file.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import time

import numpy as np

# ---------------------------------------------------------------------------------------------
# The estimator. Frozen parameters — chosen on the synthetic signal, never tuned on footage.
# ---------------------------------------------------------------------------------------------
SR = 4000            # analysis rate: ffmpeg resamples to this; 2 kHz Nyquist covers every harmonic used
WIN_S = 0.4          # STFT window (Hann). Longer smears the harmonics under braking (~50 %/s)
HOP_S = 0.1          # one estimate per GPS fix
NFFT = 8192          # zero-padded: 0.49 Hz bins, so rounding a harmonic to a bin costs < 0.25 Hz
F0_MIN, F0_MAX = 20.0, 400.0   # search range for the fundamental
F_CAP = 1200.0       # highest harmonic considered
K_MAX = 12           # at most this many harmonics per candidate
SHS_H = 0.9          # subharmonic-summation weight h**(k-1). Chosen on the synthetic set against
#                      0.84 (Hermes 1988) and 0.95: 0.84 lets a strong 2nd harmonic win an octave
#                      up at -10 dB (worst case 48 % within 5 %), 0.95 lets high f0 win an
#                      octave DOWN (worst 68 %); 0.9 holds 83 %
WHITEN_HZ = 40.0     # the log spectrum minus its own running mean over this span: keeps peaks,
#                      removes the broadband slope that wind puts under them
WHITEN_MARGIN_DB = 3.0   # ...and only what stands 3 dB above that mean counts. A noise-only bin's
#                      log power scatters ~5.6 dB about its mean, so without the margin every
#                      odd-harmonic slot of a SUBharmonic candidate collects noise for free
F0_STEP = 0.25       # candidate grid, Hz


def stft_power(x: np.ndarray, sr: int = SR, win_s: float = WIN_S, hop_s: float = HOP_S,
               nfft: int = NFFT):
    """(frame-centre times s, freqs Hz up to F_CAP, power[frames, freqs])."""
    n = int(round(win_s * sr))
    hop = int(round(hop_s * sr))
    if len(x) < n:
        return np.zeros(0), np.zeros(0), np.zeros((0, 0))
    starts = np.arange(0, len(x) - n + 1, hop)
    w = np.hanning(n).astype(np.float32)
    f = np.fft.rfftfreq(nfft, 1.0 / sr)
    keep = f <= F_CAP + 2.0
    out = np.empty((len(starts), int(keep.sum())), dtype=np.float32)
    for a in range(0, len(starts), 2048):
        idx = starts[a:a + 2048, None] + np.arange(n)[None, :]
        spec = np.fft.rfft(x[idx] * w, n=nfft, axis=1)[:, keep]
        out[a:a + 2048] = (spec.real ** 2 + spec.imag ** 2).astype(np.float32)
    return (starts + n / 2) / sr, f[keep], out


def whiten(power: np.ndarray, f: np.ndarray, span_hz: float = WHITEN_HZ) -> np.ndarray:
    """dB above the local mean of the log spectrum, less `WHITEN_MARGIN_DB`, floored at 0. Wind
    is broadband with a steep slope; a tone is narrow. Subtracting the running mean (a cumulative
    sum, so O(n)) flattens the slope, and the floor keeps only what stands out of it."""
    L = 10.0 * np.log10(power + 1e-20)
    df = f[1] - f[0]
    half = max(1, int(round(span_hz / 2 / df)))
    pad = np.pad(L, ((0, 0), (half, half)), mode="edge")
    cs = np.cumsum(pad, axis=1, dtype=np.float64)
    cs = np.concatenate([np.zeros((L.shape[0], 1)), cs], axis=1)
    mean = (cs[:, 2 * half + 1:] - cs[:, :-(2 * half + 1)]) / (2 * half + 1)
    return np.maximum(L - mean[:, :L.shape[1]] - WHITEN_MARGIN_DB, 0.0).astype(np.float32)


def _harmonic_plan(f: np.ndarray):
    grid = np.arange(F0_MIN, F0_MAX + F0_STEP / 2, F0_STEP)
    df = f[1] - f[0]
    idx, wts = [], []
    for k in range(1, K_MAX + 1):
        fk = k * grid
        ok = fk <= F_CAP
        idx.append(np.where(ok, np.rint(fk / df).astype(int), 0))
        wts.append(np.where(ok, SHS_H ** (k - 1), 0.0))
    # A SUM, not a mean. Normalising by the weights present made a candidate that lands only on
    # the strong harmonics beat the true f0 whose odd harmonics are weak: 2nd-harmonic-strongest
    # tones read an octave up in 19 % of frames at 0 dB, against 2 % for the sum.
    return grid, np.array(idx), np.array(wts)


def harmonic_f0(white: np.ndarray, f: np.ndarray):
    """Subharmonic summation on the whitened spectrum: for each candidate f0, the h**(k-1)-weighted
    sum of dB-above-local at its harmonics (up to K_MAX, below F_CAP). Returns (f0 Hz, salience)
    per frame. The salience is the winning score; a frame of wind alone scores what its noise
    scores, which is what the abstention threshold is fixed against (`calibrate`)."""
    grid, idx, wts = _harmonic_plan(f)
    n = white.shape[0]
    f0 = np.full(n, np.nan)
    sal = np.zeros(n)
    for a in range(0, n, 1024):
        blk = white[a:a + 1024]
        score = np.zeros((blk.shape[0], len(grid)), dtype=np.float32)
        for k in range(K_MAX):
            score += blk[:, idx[k]] * wts[k].astype(np.float32)
        j = np.argmax(score, axis=1)
        s0 = score[np.arange(len(j)), j]
        # parabolic refinement on the score around the peak
        jl, jr = np.clip(j - 1, 0, len(grid) - 1), np.clip(j + 1, 0, len(grid) - 1)
        yl, yr = score[np.arange(len(j)), jl], score[np.arange(len(j)), jr]
        den = yl - 2 * s0 + yr
        off = np.where(np.abs(den) > 1e-9, 0.5 * (yl - yr) / np.where(den == 0, 1, den), 0.0)
        f0[a:a + len(j)] = grid[j] + np.clip(off, -1, 1) * F0_STEP
        sal[a:a + len(j)] = s0
    return f0, sal


def estimate(x: np.ndarray, sr: int = SR):
    """The whole estimator: audio at `sr` -> (frame times, f0 Hz, salience dB)."""
    t, f, p = stft_power(np.asarray(x, dtype=np.float32), sr)
    if len(t) == 0:
        return t, np.zeros(0), np.zeros(0)
    f0, sal = harmonic_f0(whiten(p, f), f)
    return t, f0, sal


# ---------------------------------------------------------------------------------------------
# ffmpeg, as a READER only. The argument contract is `ffmpeg [in-opts] -i INPUT [out-opts] OUTPUT`
# and the only OUTPUT this module ever passes is `pipe:1`. No `-y`: nothing can be overwritten.
# ---------------------------------------------------------------------------------------------
def _ffmpeg() -> str:
    from studio import export_video

    return export_video.FFMPEG


def _pipe_only(cmd: list[str]) -> list[str]:
    """Refuse any ffmpeg command whose output is not stdout — the rule, checked in code."""
    assert cmd[-1] == "pipe:1" and "-y" not in cmd, cmd
    ins = {cmd[i + 1] for i, a in enumerate(cmd[:-1]) if a == "-i"}
    desktop = os.path.expanduser("~/Desktop")
    for a in cmd[1:]:
        if a.startswith(desktop) and a not in ins:
            raise AssertionError(f"a Desktop path outside `-i`: {a!r}")
    return cmd


def read_audio(path: str, sr: int = SR) -> np.ndarray:
    """A chapter's whole audio track, mono, at `sr`, as float32 — decoded through a pipe."""
    cmd = _pipe_only([_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-i", path,
                      "-map", "0:a:0", "-ac", "1", "-ar", str(sr), "-f", "f32le", "pipe:1"])
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype="<f4").copy()


def aac_roundtrip(x: np.ndarray, sr_in: int, sr_out: int = SR, kbps: int = 96) -> np.ndarray:
    """Encode `x` with ffmpeg's AAC (the camera's codec, ~95 kbps a channel at 189 kbps stereo)
    and decode it back at `sr_out`, entirely through pipes. The synthetic signal then crosses the
    same codec and the same resampler as the footage does."""
    enc = _pipe_only([_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
                      "-f", "f32le", "-ar", str(sr_in), "-ac", "1", "-i", "pipe:0",
                      "-c:a", "aac", "-b:a", f"{kbps}k", "-f", "adts", "pipe:1"])
    adts = subprocess.run(enc, input=np.asarray(x, "<f4").tobytes(), capture_output=True,
                          check=True).stdout
    dec = _pipe_only([_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
                      "-f", "aac", "-i", "pipe:0", "-ac", "1", "-ar", str(sr_out),
                      "-f", "f32le", "pipe:1"])
    raw = subprocess.run(dec, input=adts, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype="<f4").copy()


def resample(x: np.ndarray, sr_in: int, sr_out: int = SR) -> np.ndarray:
    """The same ffmpeg resampler the footage path uses, through pipes, without the codec."""
    cmd = _pipe_only([_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
                      "-f", "f32le", "-ar", str(sr_in), "-ac", "1", "-i", "pipe:0",
                      "-ar", str(sr_out), "-f", "f32le", "pipe:1"])
    raw = subprocess.run(cmd, input=np.asarray(x, "<f4").tobytes(), capture_output=True,
                         check=True).stdout
    return np.frombuffer(raw, dtype="<f4").copy()


# ---------------------------------------------------------------------------------------------
# The known signal.
# ---------------------------------------------------------------------------------------------
SYN_SR = 48000       # the camera's own rate; the synthetic signal crosses the same resampler
CTRL = 200           # control rate of the planted trajectories, Hz


def _lowpass_noise(n: int, sr: float, fc: float, rng) -> np.ndarray:
    """Unit-variance Gaussian noise band-limited to `fc` (zero-phase, FFT domain)."""
    spec = np.fft.rfft(rng.standard_normal(n))
    spec[np.fft.rfftfreq(n, 1.0 / sr) > fc] = 0.0
    out = np.fft.irfft(spec, n)
    return out / (out.std() + 1e-12)


def speed_trace(duration: float, rng) -> np.ndarray:
    """A kart-like speed trace at `CTRL` Hz, as a fraction of top speed: exponential acceleration
    towards the top, braking at ~0.9 g equivalent down to 45-75 % for a 0.4-1.5 s corner,
    straights of 2.5-8 s. The shape of the owner's Sandown laps: 47 s, ~10 corners, the slowest
    corner at about half of top speed."""
    n = int(duration * CTRL)
    v = np.empty(n)
    x, i = 0.55, 0
    while i < n:
        straight = rng.uniform(2.5, 8.0)
        tau = rng.uniform(2.5, 4.0)          # s, towards the top speed
        for _ in range(int(straight * CTRL)):
            if i >= n:
                break
            x += (1.0 - x) / tau / CTRL * 1.6
            v[i] = x
            i += 1
        target = rng.uniform(0.45, 0.75)
        rate = rng.uniform(0.30, 0.40)       # fraction of top speed per second (~0.9 g at 87 km/h)
        while x > target and i < n:
            x -= rate / CTRL
            v[i] = x
            i += 1
        for _ in range(int(rng.uniform(0.4, 1.5) * CTRL)):
            if i >= n:
                break
            v[i] = x
            i += 1
    return np.clip(v, 0.05, 1.0)


PROFILES = {
    # harmonic amplitudes, k = 1..12
    "1/k": [1.0 / k for k in range(1, 13)],
    "2nd-strongest": [0.5, 1.0, 0.35, 0.3, 0.25, 0.2, 0.15, 0.12, 0.1, 0.08, 0.06, 0.05],
    "flat": [1.0] * 12,
}


def engine_tone(f0_ctrl: np.ndarray, rng, profile: str) -> np.ndarray:
    """A harmonic tone at SYN_SR following the planted fundamental, with the cycle-to-cycle
    wobble of a real engine: 0.3 % frequency jitter (30 Hz band) and 20 % amplitude wobble per
    harmonic (10 Hz band). Harmonics above 2 kHz are left out."""
    n = int(len(f0_ctrl) / CTRL * SYN_SR)
    t_ctrl = np.arange(len(f0_ctrl)) / CTRL
    f0 = np.interp(np.arange(n) / SYN_SR, t_ctrl, f0_ctrl)
    f0 = f0 * (1.0 + 0.003 * _lowpass_noise(n, SYN_SR, 30.0, rng))
    phase = 2 * np.pi * np.cumsum(f0) / SYN_SR
    out = np.zeros(n)
    for k, a in enumerate(PROFILES[profile], start=1):
        wobble = 1.0 + 0.2 * _lowpass_noise(n, SYN_SR, 10.0, rng)
        out += a * wobble * np.sin(k * phase + rng.uniform(0, 2 * np.pi)) * (k * f0 < 2000)
    return out


def wind_noise(v_ctrl: np.ndarray, rng, slope: float = 2.0) -> np.ndarray:
    """Wind on a small microphone: broadband pressure noise with a steep spectrum (power ∝
    1/f**slope above 15 Hz, so most of it sits under the engine's harmonics), an amplitude that
    grows with the square of road speed (pressure ∝ ρU²), and gusts — a 0.7 Hz log-normal
    modulation of about ±3 dB."""
    n = int(len(v_ctrl) / CTRL * SYN_SR)
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1.0 / SYN_SR)
    spec *= 1.0 / np.maximum(f, 15.0) ** (slope / 2)
    w = np.fft.irfft(spec, n)
    t = np.arange(n) / SYN_SR
    speed = np.interp(t, np.arange(len(v_ctrl)) / CTRL, v_ctrl)
    gust = np.exp(0.35 * _lowpass_noise(n, SYN_SR, 0.7, rng))
    return w * speed ** 2 * gust


def band_power(x: np.ndarray, sr: int, lo: float = 20.0, hi: float = F_CAP) -> float:
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1.0 / sr)
    m = (f >= lo) & (f <= hi)
    return float(np.sum(np.abs(spec[m]) ** 2))


def high_pass(x: np.ndarray, sr: int, fc: float) -> np.ndarray:
    """A camera wind filter: 2nd-order Butterworth magnitude, zero phase (FFT domain)."""
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1.0 / sr)
    spec *= (f / fc) ** 2 / np.sqrt(1 + (f / fc) ** 4)
    return np.fft.irfft(spec, len(x))


def agc(x: np.ndarray, sr: int, target_db: float = -14.0, attack_s: float = 0.02,
        release_s: float = 0.5, max_gain_db: float = 30.0) -> np.ndarray:
    """An automatic gain control of the kind the footage shows (its 100 ms level moves by only
    2.8 dB between the 5th and 95th percentile across a racing minute): a 10 ms RMS follower,
    fast attack, slow release, then a tanh limiter at full scale."""
    blk = int(0.01 * sr)
    nb = len(x) // blk
    rms = np.sqrt(np.mean(x[:nb * blk].reshape(nb, blk) ** 2, axis=1) + 1e-12)
    want = np.minimum(10 ** (target_db / 20) / rms, 10 ** (max_gain_db / 20))
    g = np.empty(nb)
    a_att, a_rel = math.exp(-0.01 / attack_s), math.exp(-0.01 / release_s)
    cur = want[0]
    for i in range(nb):
        a = a_att if want[i] < cur else a_rel
        cur = a * cur + (1 - a) * want[i]
        g[i] = cur
    gain = np.interp(np.arange(len(x)), (np.arange(nb) + 0.5) * blk, g)
    return np.tanh(x * gain * 1.2) / math.tanh(1.2)


def synth_case(f_top: float, snr_db: float | None, rng, profile: str = "2nd-strongest",
               chain: str = "agc+aac", duration: float = 60.0, hpf: float | None = None):
    """One synthetic clip through `chain` -> (audio at SR, planted f0 at CTRL). `snr_db` None
    is wind alone (the abstention calibration). The SNR is engine vs wind power in 20-1200 Hz,
    measured over the whole clip BEFORE the gain control."""
    v = speed_trace(duration, rng)
    f0 = f_top * v
    wind = wind_noise(v, rng)
    if snr_db is None:
        mix = wind
    else:
        tone = engine_tone(f0, rng, profile)
        scale = math.sqrt(band_power(tone, SYN_SR) / band_power(wind, SYN_SR)
                          / 10 ** (snr_db / 10))
        mix = tone + wind * scale
    if hpf:
        mix = high_pass(mix, SYN_SR, hpf)
    mix = mix / (np.abs(mix).max() + 1e-12) * 0.3
    if "agc" in chain:
        mix = agc(mix, SYN_SR)
    x = aac_roundtrip(mix, SYN_SR) if "aac" in chain else resample(mix, SYN_SR)
    return x, f0


def planted_at(t: np.ndarray, f0_ctrl: np.ndarray) -> np.ndarray:
    """The planted fundamental averaged over each analysis window — what a perfect estimator
    would read."""
    tc = np.arange(len(f0_ctrl) + 1) / CTRL
    cs = np.concatenate([[0.0], np.cumsum(f0_ctrl)]) / CTRL
    a = np.clip(t - WIN_S / 2, 0, tc[-1])
    b = np.clip(t + WIN_S / 2, 0, tc[-1])
    return (np.interp(b, tc, cs) - np.interp(a, tc, cs)) / np.maximum(b - a, 1e-9)


OCTAVES = (0.5, 2.0, 1 / 3, 3.0, 2 / 3, 1.5)


def score(f_hat: np.ndarray, f_true: np.ndarray) -> dict:
    """Relative-error classes of the estimate against the truth."""
    r = f_hat / f_true
    e = np.abs(r - 1)
    octave = np.zeros(len(r), bool)
    for m in OCTAVES:
        octave |= np.abs(r / m - 1) < 0.05
    n = max(len(r), 1)
    return {"n": len(r), "within2": float(np.sum(e < 0.02) / n),
            "within5": float(np.sum(e < 0.05) / n),
            "octave": float(np.sum(octave & (e >= 0.05)) / n),
            "other": float(np.sum(~octave & (e >= 0.05)) / n),
            "median_abs_pct": float(np.median(e) * 100) if len(e) else float("nan")}


CAL_SEED = 20260922


def calibrate(n_clips: int = 6, duration: float = 30.0) -> float:
    """The abstention threshold: the 99th percentile of the salience of WIND ALONE, through the
    same gain control, codec and resampler. A frame below it is one that pure wind would produce
    1 % of the time, and the estimator abstains there. Fixed here, on a signal with no engine in
    it, and never moved on footage."""
    rng = np.random.default_rng(CAL_SEED)
    sal = np.concatenate([estimate(synth_case(200.0, None, rng, duration=duration)[0])[2]
                          for _ in range(n_clips)])
    return float(np.percentile(sal, 99))


def synthetic() -> dict:
    """Step 1: recover a planted fundamental across the realistic range, then say where it stops."""
    t0 = time.time()
    thr = calibrate()
    print(f"abstention threshold (99th pct of wind-alone salience): {thr:.2f}")
    rng = np.random.default_rng(CAL_SEED + 1)
    out: dict = {"threshold": thr, "grid": [], "chains": []}
    snrs = (10, 0, -10, -15, -20)
    print("\nTHE GRID — every harmonic profile pooled, chain = wind + AGC + limiter + AAC")
    print("f0 range (Hz)   SNR dB  covered  within 2 %  within 5 %  octave  other  "
          "median |err|  salience p50")
    for f_top in (50, 100, 200, 300):
        for snr in snrs:
            cov, rows, sal_all = [], [], []
            for prof in PROFILES:
                x, f0 = synth_case(f_top, snr, rng, profile=prof, duration=30.0)
                t, fh, sal = estimate(x)
                ft = planted_at(t, f0)
                keep = sal >= thr
                cov.append(keep.mean())
                rows.append(score(fh[keep], ft[keep]))
                sal_all.append(sal)
            n = sum(r["n"] for r in rows)
            agg = {k: sum(r[k] * r["n"] for r in rows) / max(n, 1)
                   for k in ("within2", "within5", "octave", "other")}
            med = float(np.median([r["median_abs_pct"] for r in rows if r["n"]])) if n else math.nan
            row = {"f_lo": 0.45 * f_top, "f_top": f_top, "snr": snr,
                   "covered": float(np.mean(cov)), **agg, "median_abs_pct": med,
                   "worst_within5": min(r["within5"] for r in rows if r["n"]) if n else 0.0,
                   "salience_p50": float(np.median(np.concatenate(sal_all)))}
            out["grid"].append(row)
            print(f"{0.45 * f_top:5.0f}-{f_top:<4d}      {snr:+4d}   {row['covered']:6.0%}   "
                  f"{row['within2']:8.0%}   {row['within5']:8.0%}   {row['octave']:5.0%}  "
                  f"{row['other']:5.0%}   {med:8.2f} %    {row['salience_p50']:6.1f}")
        sys.stdout.flush()

    print("\nTHE CHAIN — what each stage costs (profile '2nd-strongest', 45-100 % of 200 Hz)")
    print("chain                 SNR dB  covered  within 5 %  octave")
    for chain, hpf in (("none", None), ("agc", None), ("agc+aac", None), ("agc+aac", 100.0)):
        for snr in (0, -10, -20):
            x, f0 = synth_case(200.0, snr, rng, chain=chain, hpf=hpf, duration=30.0)
            t, fh, sal = estimate(x)
            keep = sal >= thr
            s = score(fh[keep], planted_at(t, f0)[keep])
            name = chain + (f" + {hpf:.0f} Hz high-pass" if hpf else "")
            out["chains"].append({"chain": name, "snr": snr, "covered": float(keep.mean()), **s})
            print(f"{name:<32s}{snr:+4d}   {keep.mean():6.0%}   {s['within5']:8.0%}   "
                  f"{s['octave']:5.0%}")
    print(f"\n({time.time() - t0:.0f} s)")
    return out


# ---------------------------------------------------------------------------------------------
# Real footage.
# ---------------------------------------------------------------------------------------------
DESKTOP = os.path.expanduser("~/Desktop")
# The owner's present recordings (common brief §1). Three Sandown sessions and one Milton Keynes.
RECORDINGS = {
    "0064": ("Sandown 3h 2026", "GX010064.MP4"),
    "0065": ("SD_30_08_26", "GX010065.MP4"),
    "0068": ("SD_19_09_26", "GX010068.MP4"),
    "0067": ("MK_18_09_26", "GX010067.MP4"),
}
_REAL_APP_SUPPORT = os.path.join(os.path.expanduser("~"), "Library", "Application Support",
                                 "pacer")


def _folder_state(path: str) -> dict:
    if not os.path.isdir(path):
        return {}
    return {n: (os.stat(os.path.join(path, n)).st_size, os.stat(os.path.join(path, n)).st_mtime_ns)
            for n in sorted(os.listdir(path)) if not n.startswith(".")}


def extract(key: str) -> dict:
    """Load one recording through the real `Session.load` (all chapters), and run the frozen
    estimator over every chapter's audio. Everything the analysis reads, as plain arrays."""
    from studio import chapters
    from studio.session import Session

    folder, first = RECORDINGS[key]
    root = os.path.join(DESKTOP, folder)
    before = _folder_state(root)
    paths = chapters.discover_siblings(os.path.join(root, first))
    t0 = time.time()
    s = Session.load(paths)
    entry = s.library_entry(paths)
    # The STAMP map: the camera's own media stamp for each fix. The picture map would already
    # hold the installed GPS lag, and the audio's own lag is what `analyse` measures — against
    # the stamps, so it can be compared with the lag the gyro measured.
    stamp = s.media_clock.without_gps_lag().to_media(np.asarray(s.tt, dtype=float))
    laps = [(i, *s.lap_window(i)) for i in s.valid_lap_ids()]
    vmax = s.stats.session_vmax()
    straights = [(st.label, st.trap_best_kmh, st.trap_median_kmh) for st in s.straights_report()]
    load_s = time.time() - t0
    frames_t, frames_f0, frames_sal, frames_lvl = [], [], [], []
    for c in s.chapters.chapters:
        x = read_audio(c.path)
        t, f0, sal = estimate(x)
        # The 100 ms level around each frame centre, in dBFS: the gain control's fingerprint.
        blk = int(HOP_S * SR)
        centre = np.clip((t * SR).astype(int), 0, len(x) - 1)
        lvl = np.array([np.sqrt(np.mean(x[max(0, i - blk // 2):i + blk // 2] ** 2) + 1e-12)
                        for i in centre])
        frames_t.append(t + c.offset)
        frames_f0.append(f0)
        frames_sal.append(sal)
        frames_lvl.append(20 * np.log10(lvl))
    if _folder_state(root) != before:
        raise SystemExit(f"ABORT: {root} changed while it was read")
    return {
        "key": key, "track": entry.get("track"), "date": entry.get("date"),
        "fingerprint": entry.get("fingerprint"), "chapters": len(paths), "load_s": load_s,
        "tt": np.asarray(s.tt, float), "tv": np.asarray(s.tv, float), "stamp": stamp,
        "gps_lag": s.gps_lag_applied_s, "laps": laps, "vmax": vmax[0] if vmax else None,
        "straights": straights,
        "t": np.concatenate(frames_t), "f0": np.concatenate(frames_f0),
        "sal": np.concatenate(frames_sal), "lvl": np.concatenate(frames_lvl),
    }


def _speed_at(rec: dict, t_media: np.ndarray, lag: float) -> np.ndarray:
    """Road speed (km/h) describing media instant `t_media`, when the GPS stamps run `lag` s late
    against it."""
    return np.interp(t_media + lag, rec["stamp"], rec["tv"], left=np.nan, right=np.nan)


def _modal_ratio(r: np.ndarray) -> float:
    """The mode of a ratio distribution, on 0.5 % log bins, refined by the median of the bin's
    ±3 % neighbourhood."""
    lr = np.log(r[np.isfinite(r) & (r > 0)])
    if not len(lr):
        return math.nan
    bins = np.arange(lr.min(), lr.max() + 0.005, 0.005)
    if len(bins) < 2:
        return float(np.exp(np.median(lr)))
    h, e = np.histogram(lr, bins)
    c = 0.5 * (e[np.argmax(h)] + e[np.argmax(h) + 1])
    near = lr[np.abs(lr - c) < 0.03]
    return float(np.exp(np.median(near)))


def _accel_brake_gap(rec: dict, lag: float, conf: np.ndarray, vmax: float) -> float:
    """Median tone/speed ratio under braking minus under acceleration, in % of the constant,
    above 60 % of top speed, at `lag`. Zero when the two series are aligned (and no slip)."""
    v = _speed_at(rec, rec["t"], lag)
    dv = np.gradient(np.nan_to_num(v), HOP_S)
    m = conf & np.isfinite(v) & (v > 0.6 * vmax)
    q = rec["f0"] / v
    k = _modal_ratio(q[m])
    ok = m & (np.abs(q / k - 1) < 0.08)
    return float(100 * (np.median(q[ok & (dv < -3)]) - np.median(q[ok & (dv > 3)])) / k)


def analyse(rec: dict, thr: float) -> dict:
    """Step 2: does the tone behave like a single-speed kart's engine?"""
    t, f0, sal, lvl = rec["t"], rec["f0"], rec["sal"], rec["lvl"]
    vmax = rec["vmax"] or float(np.nanmax(rec["tv"]))
    conf = sal >= thr
    res: dict = {"key": rec["key"], "frames": len(t), "confident": float(conf.mean())}

    # The audio's own lag against the GPS speed's stamps, measured two independent ways on frames
    # where the clutch is surely locked (above 60 % of top speed):
    #   (a) the lag at which the most frames sit within 2 % of one tone/speed constant;
    #   (b) the peak of the cross-correlation of the two series' frame-to-frame CHANGES, which
    #       only transitions (braking, acceleration) can move — a constant offset cannot fake it.
    # And the symptom a wrong lag leaves: under braking the tone reads HIGH against the speed and
    # under acceleration LOW (or the reverse), so the two medians split. `gap` is that split.
    lags = np.arange(-0.5, 1.0001, 0.02)
    lf = np.where(conf, np.log(np.where(f0 > 0, f0, 1.0)), np.nan)
    frac, xc = [], []
    for lag in lags:
        v = _speed_at(rec, t, lag)
        m = conf & np.isfinite(v) & (v > 0.6 * vmax)
        q = f0[m] / v[m]
        frac.append(np.mean(np.abs(q / _modal_ratio(q) - 1) < 0.02) if m.sum() > 50 else np.nan)
        dlf, dlv = np.diff(lf), np.diff(np.log(np.where(v > 1, v, np.nan)))
        mm = (np.isfinite(dlf) & np.isfinite(dlv) & (np.abs(dlf) < 0.05)
              & (np.nan_to_num(v[1:]) > 0.5 * vmax))
        xc.append(np.corrcoef(dlf[mm], dlv[mm])[0, 1] if mm.sum() > 50 else np.nan)
    frac, xc = np.array(frac), np.array(xc)
    lag = float(lags[int(np.nanargmax(frac))])
    res.update(lag=lag, lag_xcorr=float(lags[int(np.nanargmax(xc))]),
               xcorr_r=float(np.nanmax(xc)), gps_lag=rec["gps_lag"],
               gap_pct=_accel_brake_gap(rec, lag, conf, vmax),
               gap_pct_at_gps_lag=(_accel_brake_gap(rec, rec["gps_lag"], conf, vmax)
                                   if rec["gps_lag"] is not None else None))
    v = _speed_at(rec, t, lag)
    # Above 60 % of top speed a single-speed kart's centrifugal clutch is surely locked, so there
    # the tone MUST be one constant times the road speed. Below it the clutch may slip, and the
    # bands show whether it does.
    locked = np.isfinite(v) & (v > 0.6 * vmax)
    m = conf & locked
    ratio = f0[m] / v[m]                   # Hz per km/h
    k = _modal_ratio(ratio)
    q = ratio / k
    e = np.abs(q - 1)
    octave = np.zeros(len(q), bool)
    for o in OCTAVES:
        octave |= np.abs(q / o - 1) < 0.03
    res.update(k_hz_per_kmh=k, locked_frames=int(locked.sum()),
               covered=float(m.sum() / max(locked.sum(), 1)),
               within3=float(np.mean(e < 0.03)), within5=float(np.mean(e < 0.05)),
               octave=float(np.mean(octave & (e >= 0.03))),
               other=float(np.mean(~octave & (e >= 0.05))))
    # By speed band. Wind grows with speed, so a wind-dominated estimate degrades towards the
    # TOP; a slipping clutch shows at the BOTTOM as a tone above the locked constant (q > 1).
    bands = []
    for lo, hi in ((0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)):
        b = np.isfinite(v) & (v >= lo * vmax) & (v < hi * vmax)
        bb = b & conf
        qq = f0[bb] / v[bb] / k
        bands.append({"band": f"{lo:.0%}-{min(hi, 1.0):.0%}", "frames": int(b.sum()),
                      "covered": float(bb.sum() / max(b.sum(), 1)),
                      "within3": float(np.mean(np.abs(qq - 1) < 0.03)) if len(qq) else math.nan,
                      "q_p50": float(np.median(qq)) if len(qq) else math.nan,
                      "salience_p50": float(np.median(sal[b])) if b.any() else math.nan,
                      "level_p50_dbfs": float(np.median(lvl[b])) if b.any() else math.nan})
    res["bands"] = bands
    stopped = np.isfinite(v) & (v < 3.0)
    res["stopped"] = {"frames": int(stopped.sum()),
                      "covered": float(np.mean(conf[stopped])) if stopped.any() else math.nan,
                      "f0_p25_p50_p75": ([float(x) for x in np.percentile(f0[stopped & conf],
                                                                          [25, 50, 75])]
                                         if (stopped & conf).sum() > 10 else None),
                      "level_p50_dbfs": float(np.median(lvl[stopped])) if stopped.any()
                      else math.nan}
    res["level_p5_p50_p95_dbfs"] = [float(x) for x in np.percentile(lvl, [5, 50, 95])]
    # Per lap: the ratio's median on each valid lap (a gearing constant must be constant).
    per_lap = []
    for lap_id, a, b in rec["laps"]:
        ma, mb = np.interp([a, b], rec["tt"], rec["stamp"])
        w = m & (t + res["lag"] >= ma) & (t + res["lag"] < mb)
        if w.sum() >= 20:
            qq = f0[w] / v[w]
            near = qq[np.abs(qq / k - 1) < 0.05]
            per_lap.append((lap_id, float(np.median(near)) if len(near) else math.nan,
                            float(np.mean(np.abs(qq / k - 1) < 0.03))))
    res["per_lap"] = per_lap
    meds = np.array([p[1] for p in per_lap if np.isfinite(p[1])])
    res["lap_ratio_spread_pct"] = (float((np.percentile(meds, 75) - np.percentile(meds, 25))
                                         / np.median(meds) * 100) if len(meds) > 3 else math.nan)
    # Top of the straight: the fundamental at each lap's own top speed.
    tops = []
    for _lap_id, a, b in rec["laps"]:
        i = np.where((rec["tt"] >= a) & (rec["tt"] < b))[0]
        if not len(i):
            continue
        j = i[np.argmax(rec["tv"][i])]
        at = rec["stamp"][j] - res["lag"]
        fr = np.argmin(np.abs(t - at))
        if conf[fr]:
            tops.append((float(rec["tv"][j]), float(f0[fr])))
    res["tops"] = tops
    return res


def gearing_check(recs: dict) -> dict:
    """Step 3: on how many recordings can the gearing arithmetic be computed at all? It needs
    the session record's sprockets, tyre circumference and class. The owner's store is READ in
    place — its state is the answer — and never written."""
    from studio import session_record

    path = os.path.join(_REAL_APP_SUPPORT, "session_records.json")
    store = session_record.load(path)
    rows = {}
    for key, rec in recs.items():
        r = session_record.get(store, rec["fingerprint"])
        # The record has sprockets but no tyre circumference and no class (#258's schema), so even
        # a filled-in record could not feed `gearing.single_speed` today.
        rows[key] = {"record": r is not None,
                     "sprockets": (r["sprocket_front"], r["sprocket_rear"]) if r else None,
                     "rpm": None}
    return {"store_present": os.path.exists(path),
            "records": len(store.get("records", {})), "rows": rows}


# The two readings of a tone at the crank's own rate or twice it, and the sprockets that fit each.
# A kart tone at ~187 Hz at the end of a straight is 11,200 rpm if it is the crank rate (a two-stroke
# fires once a turn) and 5,600 rpm if it is twice the crank rate (a four-stroke single's strong 2nd
# order). Read as a four-stroke's FIRING rate it would be 22,400 rpm, which no kart engine turns.
READINGS = (("tone = crank rate", 1.0), ("tone = 2 x crank rate", 2.0))
NOMINAL_CIRCUMFERENCE_M = 0.88     # an 11 x 7.10-5 rear tyre, ~280 mm across
FRONT_TEETH = range(9, 21)
REAR_TEETH = range(50, 101)


def implied_gearing(k_hz_per_kmh: float, speed_kmh: float, tol: float = 0.0075) -> list[dict]:
    """For each reading of the tone, the engine RPM it implies at `speed_kmh` and every sprocket
    pair on a nominal tyre whose GEARING RPM (`studio.gearing`) lands within `tol` of it. The count
    is the point: if both readings are matched by ordinary sprockets, the audio cannot say which
    kart this is, and only a record can."""
    from studio import gearing

    out = []
    for name, order in READINGS:
        rpm = k_hz_per_kmh * speed_kmh * 60.0 / order
        pairs = []
        for f in FRONT_TEETH:
            for r in REAR_TEETH:
                g = gearing.single_speed(gearing.SINGLE_SPEED, f, r, NOMINAL_CIRCUMFERENCE_M)
                if abs(g.rpm_at(speed_kmh) / rpm - 1) <= tol:
                    pairs.append(f"{f}/{r}")
        out.append({"reading": name, "rpm": rpm, "pairs": pairs})
    return out


_ARRAYS = ("tt", "tv", "stamp", "t", "f0", "sal", "lvl")


def _save_extract(rec: dict, path: str) -> None:
    meta = {k: v for k, v in rec.items() if k not in _ARRAYS}
    np.savez(path, meta=json.dumps(meta, default=float), **{k: rec[k] for k in _ARRAYS})


def _load_extract(path: str) -> dict:
    z = np.load(path)
    rec = json.loads(str(z["meta"]))
    rec.update({k: z[k] for k in _ARRAYS})
    return rec


def real(keys: list[str], dump: str | None, reuse: bool = False) -> None:
    from studio.dev import _jail

    real_before = _folder_state(_REAL_APP_SUPPORT)
    jail = _jail.divert_app_support("pacer-p14-")
    print(f"app-support seams diverted to {jail.dir}")
    real_tracks = os.path.join(_REAL_APP_SUPPORT, "tracks.json")
    if os.path.isfile(real_tracks):
        shutil.copyfile(real_tracks, os.path.join(jail.dir, "tracks.json"))
        print("owner's tracks.json copied INTO the jail (only read on the real side)")
    thr = calibrate()
    print(f"abstention threshold (fixed on wind alone): {thr:.2f}")
    recs, results = {}, {}
    for key in keys:
        cached = os.path.join(dump, f"p14_{key}_extract.npz") if dump else None
        if reuse and cached and os.path.exists(cached):
            rec = _load_extract(cached)
            print(f"[{key}] reusing {cached}")
        else:
            rec = extract(key)
            if cached:
                os.makedirs(dump, exist_ok=True)
                _save_extract(rec, cached)
        recs[key] = rec
        res = analyse(rec, thr)
        results[key] = res
        _print_result(rec, res)
    print("\nTHE CONSTANT ACROSS RECORDINGS (tone per km/h, clutch locked; one rear tooth on a "
          "76 is 1.3 %):")
    keys_done = list(results)
    for key in keys_done:
        meds = np.array([p[1] for p in results[key]["per_lap"] if np.isfinite(p[1])])
        se = 1.2533 * np.std(meds) / math.sqrt(len(meds)) / np.median(meds) * 100
        print(f"  {key} {recs[key]['track']:<24} k {results[key]['k_hz_per_kmh']:.4f}  per-lap "
              f"median {np.median(meds):.4f} over {len(meds)} laps (SE of the median {se:.2f} %)")
    for a in range(len(keys_done)):
        for b in range(a + 1, len(keys_done)):
            ka, kb = (results[keys_done[a]]["k_hz_per_kmh"], results[keys_done[b]]["k_hz_per_kmh"])
            print(f"  {keys_done[a]} vs {keys_done[b]}: {100 * (kb / ka - 1):+.2f} %")
    g = gearing_check(recs)
    print(f"\nGEARING CHECK: owner's session_records.json "
          f"{'present' if g['store_present'] else 'ABSENT'}, {g['records']} record(s)")
    for key, row in g["rows"].items():
        print(f"  {key}: record {'yes' if row['record'] else 'NO'}  gearing RPM "
              f"{row['rpm'] if row['rpm'] is not None else '— (cannot be computed)'}")
    print("\nWHAT THE TONE ALONE IMPLIES at each recording's median top-of-lap speed, on a nominal "
          f"{NOMINAL_CIRCUMFERENCE_M} m tyre (sprocket pairs within 0.75 %):")
    for key, res in results.items():
        if not res["tops"]:
            continue
        v_top = float(np.median([s for s, _f in res["tops"]]))
        for row in implied_gearing(res["k_hz_per_kmh"], v_top):
            res.setdefault("implied", []).append(row)
            print(f"  {key} at {v_top:.1f} km/h, {row['reading']:<22}: {row['rpm']:7.0f} rpm, "
                  f"{len(row['pairs'])} pairs fit: {' '.join(row['pairs'][:8])}"
                  f"{' ...' if len(row['pairs']) > 8 else ''}")
    if _folder_state(_REAL_APP_SUPPORT) != real_before:
        raise SystemExit("ABORT: the real app-support directory changed during the run")
    print("\nreal app-support directory unchanged; every footage folder unchanged")
    if dump:
        os.makedirs(dump, exist_ok=True)
        out = os.path.join(dump, "p14_results.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"results": results, "gearing": g}, f, indent=1, default=float)
        print(f"results written to {out}")


def _print_result(rec: dict, res: dict) -> None:
    print(f"\n=== {rec['key']}  {rec['track']}  {rec['date']}  chapters {rec['chapters']}  "
          f"laps {len(rec['laps'])}  vmax {rec['vmax']:.1f} km/h  (load {rec['load_s']:.0f} s)")
    print(f"frames {res['frames']}  confident {res['confident']:.0%}   audio level dBFS "
          f"p5/p50/p95 {' / '.join(f'{x:.1f}' for x in res['level_p5_p50_p95_dbfs'])}")
    gl = res["gps_lag"]
    print(f"GPS speed stamps behind the AUDIO: {res['lag']:+.2f} s by the 2 % count, "
          f"{res['lag_xcorr']:+.2f} s by the change cross-correlation (r {res['xcorr_r']:.3f}); "
          f"braking-minus-acceleration ratio gap {res['gap_pct']:+.2f} % there. The installed "
          f"lag (gyro vs GPS path) is "
          + (f"{gl:+.3f} s, where the gap is {res['gap_pct_at_gps_lag']:+.2f} %" if gl is not None
             else "none"))
    print(f"tone / speed above 60 % of top (clutch locked): k = {res['k_hz_per_kmh']:.4f} Hz per "
          f"km/h;  covered {res['covered']:.0%} of {res['locked_frames']} frames;  of those "
          f"within 3 % {res['within3']:.0%}, within 5 % {res['within5']:.0%}, octave "
          f"{res['octave']:.0%}, other {res['other']:.0%}")
    for b in res["bands"]:
        print(f"   speed {b['band']:>9}: frames {b['frames']:6d} covered {b['covered']:5.0%} "
              f"within 3 % {b['within3']:5.0%}  tone/(k·speed) p50 {b['q_p50']:.3f}  "
              f"salience p50 {b['salience_p50']:5.1f}  level p50 {b['level_p50_dbfs']:6.1f} dBFS")
    st = res["stopped"]
    print(f"   stopped (<3 km/h): frames {st['frames']}  covered {st['covered']:.0%}  "
          f"f0 p25/p50/p75 {st['f0_p25_p50_p75']}  level p50 {st['level_p50_dbfs']:.1f} dBFS")
    meds = [p[1] for p in res["per_lap"]]
    print(f"per-lap ratio: {len(meds)} laps, median {np.nanmedian(meds):.4f}, IQR/median "
          f"{res['lap_ratio_spread_pct']:.2f} %, min {np.nanmin(meds):.4f} max {np.nanmax(meds):.4f}")
    if res["tops"]:
        tv, tf = np.array(res["tops"]).T
        print(f"top of each lap: speed {np.median(tv):.1f} km/h (range {tv.min():.1f}-{tv.max():.1f}),"
              f" fundamental {np.median(tf):.1f} Hz (range {tf.min():.1f}-{tf.max():.1f}), "
              f"{len(tv)} of {len(rec['laps'])} laps confident")
    print(f"straights (label, trap best, trap median): {rec['straights'][:3]} ...")


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("synthetic", "real"):
        print(__doc__)
        return 2
    if argv[0] == "synthetic":
        synthetic()
        return 0
    dump = None
    if "--dump" in argv:
        i = argv.index("--dump")
        dump = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    reuse = "--reuse" in argv
    keys = [a for a in argv[1:] if a != "--reuse"] or list(RECORDINGS)
    real(keys, dump, reuse)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
