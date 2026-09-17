"""Probe 3 — track bump map: vertical-accel RMS binned by track distance.

The ambiguity this would settle is one a coach named and nobody resolves: a sawtooth lateral-g
trace means EITHER a rough track OR a rough driver. A bump map separates them — but only if the
signature is a property of the TRACK, and the test for that is whether it reproduces on a
different day.

Four things have to be true, and each has a null beside it:

  1. it repeats lap to lap                 -> split-half (odd vs even laps) within one session
  2. it repeats SESSION to SESSION         -> 0060 vs 0062, different days, same track
  3. it is not just the speed profile      -> partial correlation with log speed removed
  4. it is not an artefact of the alignment -> the same verdict at deliberate mis-alignments

(3) is the one that can quietly ruin this: road excitation grows with speed, so a "bump map" that
is really a speed map would reproduce perfectly and mean nothing new — the app already draws
speed. (4) exists because placing an accelerometer sample on the track is a CROSS-CLOCK operation:
the ACCL stream carries the camera's media stamps and the lap columns are on the GPS9 telemetry
clock, ~27 ppm apart. The GPS timestamps are also ~0.46-0.48 s late against the PICTURE (PR #291),
but the ACCL's content carries very nearly the same delay, so `_align.to_accl_clock` takes out the
rate difference and NOT the lag. Until T9 this probe took the lag out too — the gyro's map — and
every bump sat ~0.4 s (~7 m, one bin) from where it was measured; `_align.check_accl` now measures
the placement on each run, and (4) measures how much a mis-alignment moves the verdict.

    pixi run python -m studio.dev.probes.p3_bumpmap
"""

from __future__ import annotations

import numpy as np

from studio.rotation import yaw_rate_series

from . import _accel, _align, _cache

NBINS = 200        # ~5.3 m per bin on a 1058 m lap
HP_HZ = 3.0        # high-pass: below this is the kart's body motion (corner, brake, crest), not road
WIN_S = 0.25       # RMS window


def _highpass_rms(a, fs):
    """Rolling RMS of `a` above HP_HZ. The high-pass is a boxcar-subtraction — the same primitive
    the rest of the app filters with, so the cutoff is the one this repo can already reason
    about."""
    from studio._signal import boxcar
    w_hp = max(int(round(fs / HP_HZ)), 1)
    hp = a - boxcar(a, w_hp)
    w_rms = max(int(round(WIN_S * fs)), 1)
    return np.sqrt(np.maximum(boxcar(hp * hp, w_rms), 0.0))


def profile(rec, lap_subset=None, shift_s: float = 0.0):
    """-> (bump_rms, speed) per track-distance bin, medians over the selected clean laps."""
    t, av, _h1, _h2 = _accel.vertical_and_horizontal(rec.accl, rec.grav)
    fs = (len(t) - 1) / (t[-1] - t[0])
    rms = _highpass_rms(av, fs)
    frac, lap = _accel.to_track_fraction(rec, t, shift_s)
    on = lap >= 0
    if lap_subset is not None:
        on &= np.isin(lap, lap_subset)
    bins = np.clip((frac[on] * NBINS).astype(int), 0, NBINS - 1)
    vals = rms[on]
    # Speed on the same bins, from the GPS columns. Both are GPS-derived, so this half needs no
    # clock conversion — it is the accelerometer channel that has to be moved onto the track.
    sf, sv = [], []
    for _i, (tt, _x, _y, v, d) in rec.laps():
        if len(tt) < 8 or d[-1] <= 0:
            continue
        sf.append(d / d[-1])
        sv.append(v)
    sf, sv = np.concatenate(sf), np.concatenate(sv)
    sb = np.clip((sf * NBINS).astype(int), 0, NBINS - 1)
    bump = np.full(NBINS, np.nan)
    speed = np.full(NBINS, np.nan)
    for b in range(NBINS):
        m = bins == b
        if int(m.sum()) >= 10:
            bump[b] = np.median(vals[m])
        m2 = sb == b
        if int(m2.sum()) >= 3:
            speed[b] = np.median(sv[m2])
    return bump, speed


def _r(a, b):
    ok = np.isfinite(a) & np.isfinite(b)
    if int(ok.sum()) < 4:
        return float("nan")
    return float(np.corrcoef(a[ok], b[ok])[0, 1])


def _logr(a, b):
    """The same correlation in LOG space — the one every profile comparison here uses, so a
    bump map and its speed control are never compared on different scales."""
    return _r(np.log(np.maximum(a, 1e-9)), np.log(np.maximum(b, 1e-9)))


def _partial_r(a, b, c):
    """Correlation of a and b with c regressed out of both (all in logs)."""
    ok = np.isfinite(a) & np.isfinite(b) & np.isfinite(c)
    if int(ok.sum()) < 4:
        return float("nan")
    A, B, C = (np.log(np.maximum(v[ok], 1e-9)) for v in (a, b, c))
    X = np.column_stack([C, np.ones_like(C)])
    ra = A - X @ np.linalg.lstsq(X, A, rcond=None)[0]
    rb = B - X @ np.linalg.lstsq(X, B, rcond=None)[0]
    return float(np.corrcoef(ra, rb)[0, 1])


def _best_shift(a, b):
    """Circular-shift alignment: the two sessions fit their own start lines, so a common track
    feature need not land in the same bin. Returns (best r, shift in bins)."""
    best = (-2.0, 0)
    for s in range(NBINS):
        r = _logr(a, np.roll(b, s))
        if np.isfinite(r) and r > best[0]:
            best = (r, s)
    return best


def _surrogate_null(a, b, n: int = 500, seed: int = 0) -> float:
    """|r| p95 of phase-randomised surrogates of `a` against the real `b`.

    A bump profile is a smooth 1-D signal, and two smooth signals correlate by chance far more
    often than n-independent-samples intuition says. The surrogates keep `a`'s power spectrum —
    so its smoothness — and destroy only its phase, which is exactly the "same shape, no shared
    features" null this needs. Both series are taken on the SAME finite mask, so the null and the
    measurement are computed over identical bins."""
    ok = np.isfinite(a) & np.isfinite(b)
    if int(ok.sum()) < 8:
        return float("nan")
    A = np.log(np.maximum(a[ok], 1e-9))
    B = np.log(np.maximum(b[ok], 1e-9))
    A = A - A.mean()
    rng = np.random.default_rng(seed)
    F = np.fft.rfft(A)
    out = []
    for _ in range(n):
        phase = np.exp(1j * rng.uniform(0, 2 * np.pi, len(F)))
        phase[0] = 1.0
        sur = np.fft.irfft(F * phase, n=len(A))
        out.append(abs(float(np.corrcoef(sur, B)[0, 1])))
    return float(np.percentile(out, 95))


def report():
    profs = {}
    for key in ("0060", "0062"):
        rec = _cache.load(key)
        n = len(rec)
        print(f"\n=== {key}  clean laps={n} ===")
        print("  " + _align.describe(rec))
        gt, gyaw = yaw_rate_series(rec.gyro, rec.grav)
        _align.check(rec, gt, gyaw)
        _align.check_accl(rec)      # the stream this probe actually places on the track
        bump, speed = profile(rec)
        profs[key] = (bump, speed, rec)
        odd, even = np.arange(0, n, 2), np.arange(1, n, 2)
        b_odd, _ = profile(rec, odd)
        b_even, _ = profile(rec, even)
        print(f"bump RMS over the lap: median {np.nanmedian(bump):.3f} m/s^2, "
              f"range [{np.nanmin(bump):.3f}, {np.nanmax(bump):.3f}] "
              f"(x{np.nanmax(bump) / np.nanmin(bump):.1f} between the smoothest and roughest bin)")
        print(f"(1) WITHIN session, split-half odd vs even laps: r={_logr(b_odd, b_even):+.3f} "
              f"over {NBINS} bins  <- the reproducibility CEILING")
        print(f"    null for that split-half (phase-randomised surrogates): "
              f"|r| p95 = {_surrogate_null(b_odd, b_even):.3f}")
        # (3) the speed confound, inside one session
        print(f"(3) r(bump, speed) = {_logr(bump, speed):+.3f}; "
              f"split-half with speed regressed out: "
              f"r={_partial_r(b_odd, b_even, speed):+.3f}")
        # (4) how much did the clock correction matter, within one session?
        print("(4) the SAME within-session split-half at deliberate mis-alignments:")
        for shift in (-0.50, -0.25, 0.0, +0.25, +0.50):
            so, _ = profile(rec, odd, shift_s=shift)
            se, _ = profile(rec, even, shift_s=shift)
            print(f"      shift {shift:+.2f}s -> r={_logr(so, se):+.3f}")

    # (2) THE TEST THAT DECIDES IT ---------------------------------------------------------
    a, sa, _ra = profs["0060"]
    b, sb, _rb = profs["0062"]
    r0 = _logr(a, b)
    rbest, shift = _best_shift(a, b)
    print(f"\n(2) ACROSS sessions (different days, same track): r={r0:+.3f} as-is; "
          f"best circular shift {shift} bins -> r={rbest:+.3f}")
    # Is the two sessions' start line even in the same place? The speed profile answers that
    # independently, and it is the control: speed MUST reproduce across sessions.
    sr0 = _logr(sa, sb)
    srbest, sshift = _best_shift(sa, sb)
    print(f"    CONTROL, the speed profile the app already draws: r={sr0:+.3f} as-is, "
          f"best shift {sshift} bins -> r={srbest:+.3f}")
    print(f"    with speed regressed out of both sessions' bump profiles: "
          f"r={_partial_r(a, b, np.sqrt(sa * sb)):+.3f}")
    print(f"    NULL (phase-randomised surrogates with the same spectrum): "
          f"|r| p95 = {_surrogate_null(a, b):.3f}")


if __name__ == "__main__":
    report()
