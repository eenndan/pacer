"""Probe 2 — wheel hop / chatter: is there a COHERENT 5-25 Hz oscillation, or broadband buzz?

The practitioner description of kart hop is grip building and releasing repeatedly — a narrowband
oscillation that should appear in some corners and not others, and repeat across laps. The
falsifiable version:

  1. does the 5-25 Hz band hold a NARROWBAND peak, or is it flat?          (spectral peakiness)
  2. does the peak sit at the same FREQUENCY each time it appears?          (peak-frequency spread)
  3. does band power land at the same PLACE on track, lap after lap?        (split-half by distance)

A "no" to (1) is the broadband-vibration answer and it ends the question, whatever (3) says: a
place-on-track pattern with no narrowband peak is a bump map (probe 3), not chatter.

THE PRIOR THIS HAS TO SURVIVE. The IMU's longitudinal axis is vibration-inflated ~1.5x and only
weakly correlated with the GPS speed derivative, which is why the app reads GPS for braking. That
is a statement that this accelerometer carries a lot of energy which is NOT vehicle motion — so
"there is energy in the 5-25 Hz band" is not evidence of anything. Only the NARROWBAND and
REPEATABILITY tests below separate a resonance from that buzz, which is why each has a null
beside it rather than a threshold.

THE CLOCK. Questions (1) and (2) are single-channel and need no alignment. Question (3) is
cross-clock — it asks where on track an inertial sample was — and goes through `_align`.

    pixi run python -m studio.dev.probes.p2_chatter
"""

from __future__ import annotations

import numpy as np

from studio.rotation import yaw_rate_series

from . import _accel, _align, _cache

NFFT = 256          # 1.28 s at 200 Hz -> 0.78 Hz resolution; wide enough to resolve a 5-25 Hz peak
HOP = 64            # 0.32 s between frames
BAND = (5.0, 25.0)  # the wheel-hop band the question is about
NBINS = 200         # track-distance bins (~5.3 m on a 1058 m lap)


# Baseline window for the prominence statistic, in bins. 9 bins is 7 Hz at this resolution —
# several times wider than a resonance's linewidth would be, and narrow enough that a 1/f slope
# is locally flat across it.
_BASE_W = 9


def _band(freqs, lo, hi):
    return (freqs >= lo) & (freqs <= hi)


def _running_median(P, w: int):
    """Per-row running median across frequency, edges included (reflect-padded)."""
    pad = w // 2
    Q = np.pad(P, ((0, 0), (pad, pad)), mode="reflect")
    return np.median(np.lib.stride_tricks.sliding_window_view(Q, w, axis=1), axis=-1)


def pipeline_check(rec):
    """What the app's own pipeline does to this band, measured rather than assumed."""
    from studio._signal import boxcar
    from studio.gmeter import LAT_SMOOTH_S, OUTPUT_HZ
    fs = (len(rec.accl) - 1) / (rec.accl[-1, 0] - rec.accl[0, 0])
    print(f"raw ACCL {len(rec.accl)} samples at {fs:.2f} Hz; gmeter low-passes {LAT_SMOOTH_S}s "
          f"and resamples to {OUTPUT_HZ:.0f} Hz")
    # A boxcar's transfer is |sin(pi f W)/(N sin(pi f/fs))|. Measure it on a probe tone instead.
    w = max(int(round(LAT_SMOOTH_S * fs)), 1)
    t = np.arange(int(20 * fs)) / fs
    for f in (5.0, 10.0, 15.0, 25.0):
        att = np.std(boxcar(np.sin(2 * np.pi * f * t), w)) / np.std(np.sin(2 * np.pi * f * t))
        print(f"  a {f:.0f} Hz tone survives the g-meter low-pass at {att:.4f} of its amplitude "
              f"({20 * np.log10(max(att, 1e-9)):+.0f} dB)")
    print(f"  ...and {OUTPUT_HZ / 2:.0f} Hz is the Nyquist of the resampled output, so anything "
          f"above it aliases. The raw stream is the only place this band exists.")


def spectra(rec):
    t, av, h1, h2 = _accel.vertical_and_horizontal(rec.accl, rec.grav)
    fs = (len(t) - 1) / (t[-1] - t[0])
    idx, freqs, pv = _accel.stft(av, fs, NFFT, HOP)
    _i, _f, p1 = _accel.stft(h1, fs, NFFT, HOP)
    _i, _f, p2 = _accel.stft(h2, fs, NFFT, HOP)
    ph = p1 + p2          # rotation-invariant horizontal power (see _accel)
    return t[idx], freqs, pv, ph, fs


def report(key: str):
    rec = _cache.load(key)
    print(f"\n=== {key}  device={rec.device}  clean laps={len(rec)} ===")
    print("  " + _align.describe(rec))
    gt, gyaw = yaw_rate_series(rec.gyro, rec.grav)
    _align.check(rec, gt, gyaw)
    pipeline_check(rec)

    tq, freqs, pv, ph, fs = spectra(rec)
    frac, lap = _accel.to_track_fraction(rec, tq)
    on = lap >= 0
    print(f"STFT {pv.shape[0]} frames of {NFFT / fs:.2f}s ({freqs[1] - freqs[0]:.2f} Hz bins); "
          f"{int(on.sum())} land inside a clean lap")

    # The SHAPE of the mean spectrum first: max-over-median inside a band is a measure of
    # spectral SLOPE whenever the spectrum falls steeply, and it reports a "peak" pinned to the
    # band's lower edge. Print the shape so that reading is not available by accident.
    shape = pv[on].mean(axis=0)
    ref = shape[np.argmin(np.abs(freqs - 5.0))]
    print("mean vertical spectrum, dB re 5 Hz: " + "  ".join(
        f"{f:g}Hz {10 * np.log10(shape[np.argmin(np.abs(freqs - f))] / ref):+.1f}"
        for f in (2, 5, 8, 12, 16, 20, 25, 35, 50, 80)))

    bm = _band(freqs, *BAND)
    for name, P in (("vertical", pv), ("horizontal", ph)):
        Pb = P[on][:, bm]
        fb = freqs[bm]
        # (1) PROMINENCE over the LOCAL baseline, not over the band median: each bin divided by a
        # running median of its neighbours, so a steep 1/f slope contributes nothing and only a
        # narrowband excess scores. This is the statistic the question actually needs.
        base = _running_median(Pb, _BASE_W)
        prom = Pb / np.maximum(base, 1e-30)
        best = np.argmax(prom, axis=1)
        pk_p = prom[np.arange(len(best)), best]
        pk_f = fb[best]
        # The null: the same statistic on a band that is white by construction (a periodogram bin
        # of Gaussian noise is chi-square with 2 dof), so "how peaky does chance look?" is
        # measured, not assumed.
        null = np.random.default_rng(0).chisquare(2, size=(8000, bm.sum()))
        null_p = (null / np.maximum(_running_median(null, _BASE_W), 1e-30)).max(axis=1)
        print(f"{name}: 5-25 Hz local prominence median {np.median(pk_p):.2f}x, "
              f"p90 {np.percentile(pk_p, 90):.2f}x, p99 {np.percentile(pk_p, 99):.2f}x  "
              f"vs a WHITE-NOISE null of {np.median(null_p):.2f} / "
              f"{np.percentile(null_p, 90):.2f} / {np.percentile(null_p, 99):.2f}")
        print(f"  frames beating the null's p99 ({np.percentile(null_p, 99):.2f}x): "
              f"{100 * np.mean(pk_p > np.percentile(null_p, 99)):.1f}%")
        print(f"  peak frequency: median {np.median(pk_f):.1f} Hz, "
              f"IQR [{np.percentile(pk_f, 25):.1f}, {np.percentile(pk_f, 75):.1f}] Hz, "
              f"sd {np.std(pk_f):.1f} Hz over a {BAND[1] - BAND[0]:.0f} Hz band "
              f"(uniform-random sd would be {(BAND[1] - BAND[0]) / np.sqrt(12):.1f})")
        # (2) PERSISTENCE: a resonance lasts longer than one frame. But adjacent frames overlap by
        # 1 - HOP/NFFT = 75 % of their samples, so agreement between them is mostly the window
        # agreeing with itself. The honest lag is NFFT/HOP frames — the first that shares no
        # samples at all — with the overlapping number printed beside it to show the difference.
        tol = 1.5 * (fb[1] - fb[0])
        k = NFFT // HOP
        print(f"  next frame picks the same peak (+-1 bin) "
              f"{100 * np.mean(np.abs(np.diff(pk_f)) < tol):.1f}% of the time, but those frames "
              f"share {100 * (1 - HOP / NFFT):.0f}% of their samples; at lag {k} frames "
              f"(NO overlap) it is {100 * np.mean(np.abs(pk_f[k:] - pk_f[:-k]) < tol):.1f}% "
              f"against a {100 * 3 / len(fb):.1f}% chance rate")
        # (3) does band POWER repeat by track position? Split-half over laps.
        _split_half(frac[on], lap[on], Pb.sum(axis=1), f"  {name} band power")

    # For context: where the accelerometer's energy actually is.
    tot = pv[on].sum(axis=0)
    edges = [(0.5, 5), (5, 25), (25, 50), (50, 100)]
    s = tot.sum()
    print("vertical energy by band: " + ", ".join(
        f"{a:g}-{b:g} Hz {100 * tot[_band(freqs, a, b)].sum() / s:.1f}%" for a, b in edges))


def _split_half(frac, lap, value, label):
    """Does `value` land at the same track position lap after lap? Odd laps vs even laps."""
    laps = np.unique(lap)
    bins = np.clip((frac * NBINS).astype(int), 0, NBINS - 1)
    prof = []
    for half in (laps[::2], laps[1::2]):
        m = np.isin(lap, half)
        p = np.full(NBINS, np.nan)
        for b in range(NBINS):
            sel = m & (bins == b)
            if int(sel.sum()) >= 3:
                p[b] = np.median(value[sel])
        prof.append(p)
    ok = np.isfinite(prof[0]) & np.isfinite(prof[1])
    r = np.corrcoef(np.log(prof[0][ok]), np.log(prof[1][ok]))[0, 1] if int(ok.sum()) > 3 else np.nan
    print(f"{label}: split-half (odd vs even laps) by track position r={r:+.3f} "
          f"over {int(ok.sum())} of {NBINS} bins")
    return r


if __name__ == "__main__":
    for k in ("0060", "0062"):
        report(k)
