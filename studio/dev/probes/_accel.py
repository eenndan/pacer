"""Raw-ACCL helpers shared by the chatter (p2) and bump-map (p3) probes.

The product's g-meter is NOT usable for either question: `gmeter.compute` boxcars over 0.15 s and
resamples to 50 Hz, and a 0.15 s boxcar's first null sits at 6.7 Hz — it deletes exactly the
5-25 Hz band the wheel-hop question is about. So both probes start from the raw 200 Hz stream.

Axis handling is the mount-independent part of the g-meter recipe and nothing more: project onto
GRAV (permuted onto ACCL's element order by `gmeter.GRAV_PERM`) for VERTICAL, and take what is
left as the HORIZONTAL plane. No CORI, no Procrustes yaw fit — a power spectrum summed over the
two in-plane components is invariant under rotation about the vertical, so the yaw the g-meter
works hard to resolve is not needed and its drift is not inherited.

PLACING AN INERTIAL SAMPLE ON THE TRACK IS A CROSS-CLOCK OPERATION. The ACCL stream is on the
media clock; the lap columns that say where the kart was are on the GPS9 telemetry clock and are
~0.46-0.48 s late on top of that. `to_track_fraction` goes through `_align` for both corrections —
uncorrected, every bump lands ~8 m from where it happened at 60 km/h.
"""

from __future__ import annotations

import numpy as np

from studio._signal import G
from studio.gmeter import GRAV_PERM

from . import _align


def vertical_and_horizontal(accl, grav):
    """-> (t, a_vert, a_h1, a_h2) in m/s^2 on ACCL's native ~200 Hz grid.

    `a_vert` is gravity-removed (+ = upward, i.e. against gravity). `a_h1`/`a_h2` span the
    horizontal plane in an arbitrary but CONTINUOUS basis — fine for spectra, meaningless as
    individual axes."""
    accl = np.asarray(accl, float)
    grav = np.asarray(grav, float)
    t = accl[:, 0]
    up = np.column_stack([np.interp(t, grav[:, 0], grav[:, 1 + GRAV_PERM[i]]) for i in range(3)])
    up /= np.maximum(np.linalg.norm(up, axis=1, keepdims=True), 1e-12)
    a = accl[:, 1:4]
    along = np.sum(a * up, axis=1)
    plane = a - along[:, None] * up
    # A continuous in-plane basis: one fixed world-ish seed vector, re-orthogonalised per sample.
    seed = np.array([1.0, 0.0, 0.0])
    e1 = seed - np.sum(up * seed, axis=1)[:, None] * up
    small = np.linalg.norm(e1, axis=1) < 1e-3
    if np.any(small):                       # gravity parallel to the seed: pick another
        alt = np.array([0.0, 1.0, 0.0])
        e1[small] = alt - np.sum(up[small] * alt, axis=1)[:, None] * up[small]
    e1 /= np.maximum(np.linalg.norm(e1, axis=1, keepdims=True), 1e-12)
    e2 = np.cross(up, e1)
    return (t, along - G, np.sum(plane * e1, axis=1), np.sum(plane * e2, axis=1))


def stft(sig, fs: float, nfft: int, hop: int):
    """-> (frame_centre_index, freqs, power) one-sided |FFT|^2 of Hann-windowed frames.

    Plain numpy: this repo has no scipy, and a strided Hann STFT is six lines."""
    sig = np.asarray(sig, float)
    n = (len(sig) - nfft) // hop + 1
    if n < 1:
        return np.empty(0, int), np.empty(0), np.empty((0, 0))
    idx = np.arange(n) * hop
    frames = np.lib.stride_tricks.sliding_window_view(sig, nfft)[idx]
    frames = frames - frames.mean(axis=1, keepdims=True)
    spec = np.fft.rfft(frames * np.hanning(nfft), axis=1)
    return idx + nfft // 2, np.fft.rfftfreq(nfft, 1.0 / fs), np.abs(spec) ** 2


def lap_windows(rec, shift_s: float = 0.0):
    """-> [(t_inertial, frac)] per clean lap: when each lap ran ON THE IMU's OWN CLOCK.

    `frac` is the lap's normalized odometer, so an inertial time inside a window interpolates
    straight to a track position. `shift_s` deliberately mis-aligns the two clocks and exists only
    so a probe can show how much its verdict depends on the alignment being right."""
    out = []
    for _i, (t, _x, _y, _v, d) in rec.laps():
        if len(t) < 8 or d[-1] <= 0:
            continue
        out.append((_align.to_inertial(rec, t) + shift_s, d / d[-1]))
    return out


def to_track_fraction(rec, tq, shift_s: float = 0.0):
    """Track fraction (0..1) and lap row for each INERTIAL time in `tq`; NaN/-1 outside a lap.

    `tq` is on the ACCL/GYRO media clock (that is what `vertical_and_horizontal` returns). The
    lap windows are converted onto that same clock first — see `_align`."""
    tq = np.asarray(tq, float)
    frac = np.full(len(tq), np.nan)
    lap = np.full(len(tq), -1, int)
    for li, (tt, f) in enumerate(lap_windows(rec, shift_s)):
        m = (tq >= tt[0]) & (tq <= tt[-1])
        if np.any(m):
            frac[m] = np.interp(tq[m], tt, f)
            lap[m] = li
    return frac, lap
