"""Probe 1 — sideslip rate: does `beta_dot = omega_path - omega_gyro` measure anything?

    beta = heading of the VELOCITY vector - heading of the BODY
    beta_dot = d(path heading)/dt - body yaw rate

The body yaw rate is measured (GYRO, `studio.rotation`). The path rotation rate is geometry.

THE REFERENCE. `corners.lap_yaw_rate` (PR #259) is the path channel: kappa carried at the trace's
own ds/dt, which closes a lap at 1.0007 x 2*pi. Its predecessor `v * kappa` mixed bases (kappa on
the odometer, v from GPS Doppler) and over-read a lap's rotation by 6.4-10.1 % — CORNER-
CONCENTRATED, which is the worst possible shape for a corner-scale difference channel, so this
probe would have been measuring that defect. A second, fully independent reference is kept beside
it (`path_heading_rate`, the raw trace tangent differentiated in time) precisely so the verdict
does not rest on one construction: both are reported, and they agree.

THE CLOCK IS THE WHOLE DIFFICULTY, AND IT IS NOT FITTED HERE. beta_dot is a DIFFERENCE of two
rotation rates on two different clocks, so an offset delta between them forges it exactly:
omega(t) - omega(t+delta) ~= -delta * d(omega)/dt, which peaks at turn-in and reverses at exit —
the shape a scrub index is supposed to have. An earlier version of this probe FITTED that offset
by maximising the correlation, which quietly absorbs the 27 ppm media-vs-telemetry rate difference
into one constant and leaves the ramp in. This version takes the alignment from `_align` (the
affine clock map + PR #291's measured `gps_lag_s`), then sweeps the RESIDUAL offset as a check
that it lands on zero, and finally reports how much beta_dot a residual offset of the sweep's own
plateau width would forge. None of the numbers below come from a fitted lag.

    pixi run python -m studio.dev.probes.p1_sideslip

Everything this prints is a measurement; the verdict lives in
`studio/docs/measured-channels-2026-09.md`.
"""

from __future__ import annotations

import numpy as np

from studio._signal import boxcar
from studio.corners import lap_yaw_rate
from studio.rotation import yaw_rate_series

from . import _align, _cache

TWO_PI = 2.0 * np.pi


# A materialized lap begins and ends on the INTERPOLATED timing-line crossing, which can land a
# few milliseconds and a few millimetres from the neighbouring real GPS sample (measured: dt down
# to 0.000 s, ds down to 0.0037 m on the D24 0060 pair). Differentiating across that pair divides
# by ~0, and one such sample took a lap's total heading change to 2.56 x 2*pi and its yaw-rate RMS
# from 0.57 to 10.4 rad/s. Anything that differentiates a lap trace has to drop them first.
_MIN_GAP_FRAC = 0.25          # keep a sample only if its step is >= this x the lap's median step


def _dedegenerate(t, x, y, v, d):
    """Drop the near-zero-length steps a materialized lap's interpolated ends create.

    Keeps the FIRST sample of every degenerate pair. A lap that loses its closing sample that way
    is short by the length of the degenerate step — millimetres out of ~1058 m — which is four
    orders of magnitude below anything measured here."""
    t, x, y, v, d = (np.asarray(a, float) for a in (t, x, y, v, d))
    ds, dt = np.diff(d), np.diff(t)
    bad = (ds < _MIN_GAP_FRAC * np.median(ds)) | (dt < _MIN_GAP_FRAC * np.median(dt))
    keep = np.ones(len(t), bool)
    keep[1:] = ~bad
    return t[keep], x[keep], y[keep], v[keep], d[keep]


def path_heading_rate(t, x, y, smooth_s: float = 0.0):
    """d(path heading)/dt (rad/s, + = left) from a lap's local-metre trace — the SECOND,
    independent path reference, differentiating the raw trace tangent in time with no arc-length
    basis anywhere in it. Feed it a `_dedegenerate`d trace."""
    t = np.asarray(t, float)
    psi = np.unwrap(np.arctan2(np.gradient(np.asarray(y, float)),
                               np.gradient(np.asarray(x, float))))
    w = np.gradient(psi, t)
    if smooth_s > 0:
        dt = float(np.median(np.diff(t)))
        w = boxcar(w, max(int(round(smooth_s / max(dt, 1e-9))), 1))
    return w


def path_rate(t, x, y, d, which: str = "kappa"):
    """The path rotation rate a lap trace implies, by either construction."""
    if which == "kappa":
        return lap_yaw_rate(x, y, d, t)
    return path_heading_rate(t, x, y)


def _corr(a, b) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3 or np.std(a) < 1e-12 or np.std(b) < 1e-12:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def _clean_laps(rec):
    for i, cols in rec.laps():
        t, x, y, v, d = _dedegenerate(*cols)
        if len(t) >= 32 and d[-1] > 0:
            yield i, (t, x, y, v, d)


def _residual_lag_sweep(rec, gt, gyaw, lags, which: str = "kappa"):
    """Correlation of the two rotation rates vs an EXTRA offset added on top of `_align`.

    The peak must sit at 0.000 s: that is the statement that the alignment is already right.
    Precomputes the path channel once and only re-interpolates the gyro per lag — recomputing a
    curvature profile inside a 201-offset x 100-lap loop is what made an earlier version of this
    probe take minutes instead of seconds."""
    ref = [(_align.to_inertial(rec, t), path_rate(t, x, y, d, which))
           for _i, (t, x, y, _v, d) in _clean_laps(rec)]
    b = np.concatenate([w for _t, w in ref])
    out = []
    for lag in lags:
        a = np.concatenate([np.interp(t + lag, gt, gyaw) for t, _w in ref])
        out.append((float(lag), _corr(a, b)))
    return out


def _loop_ratios(rec, gt, gyaw):
    """Closed-lap rotation of the gyro and of BOTH path references, as multiples of 2*pi."""
    g, pk, ph = [], [], []
    for _i, (t, x, y, _v, d) in _clean_laps(rec):
        ti = _align.to_inertial(rec, t)
        m = (gt >= ti[0]) & (gt <= ti[-1])
        if int(m.sum()) < 32:
            continue
        g.append(float(np.trapezoid(gyaw[m], gt[m])) / TWO_PI)
        pk.append(float(np.trapezoid(lap_yaw_rate(x, y, d, t), t)) / TWO_PI)
        ph.append(float(np.trapezoid(path_heading_rate(t, x, y), t)) / TWO_PI)
    return np.asarray(g), np.asarray(pk), np.asarray(ph)


def analyse(key: str, extra_lag: float = 0.0, match_bw_s: float = 1.3,
            which: str = "kappa", quiet: bool = False):
    rec = _cache.load(key)
    gt, gyaw = yaw_rate_series(rec.gyro, rec.grav)
    say = (lambda *a, **k: None) if quiet else print
    say(f"\n=== {key}  device={rec.device}  clean laps={len(rec)}  "
        f"corners={len(rec.corner_cid)}  reference={which} ===")
    if not quiet:
        say("  " + _align.describe(rec))
        _align.check(rec, gt, gyaw)

    # --- 0. both references' own closed-loop check -----------------------------------------
    if not quiet:
        gl, pk, ph = _loop_ratios(rec, gt, gyaw)
        say(f"closed-lap rotation over {len(gl)} laps (exact target 1.0000): "
            f"gyro {np.median(gl):.4f}, lap_yaw_rate {np.median(pk):.4f} "
            f"[{pk.min():.4f}, {pk.max():.4f}], trace-tangent {np.median(ph):.4f} "
            f"[{ph.min():.4f}, {ph.max():.4f}]")

        # --- 1. the alignment, checked rather than fitted ----------------------------------
        sweep = _residual_lag_sweep(rec, gt, gyaw, np.round(np.arange(-1.0, 1.001, 0.01), 3),
                                    which)
        best_lag, best_r = max(sweep, key=lambda kv: kv[1])
        r0 = dict(sweep)[0.0]
        near = [lg for lg, r in sweep if r >= best_r - 0.002]
        say(f"residual lag sweep (0.000 = the alignment is right): best {best_lag:+.3f}s "
            f"r={best_r:+.4f}, at 0 lag r={r0:+.4f}; lags within 0.002 of the peak span "
            f"[{min(near):+.2f}, {max(near):+.2f}]s")

    # --- 2. the channel, at matched bandwidth ----------------------------------------------
    # The GPS trace is boxcar-smoothed over 13 samples (~1.3 s) BEFORE lap columns exist, so its
    # heading rate is band-limited to that. Differencing it against a 0.30 s-smoothed gyro
    # measures the bandwidth gap, not sideslip — so both channels get the same window here.
    rows = []
    for i, (t, x, y, v, d) in _clean_laps(rec):
        dt = float(np.median(np.diff(t)))
        w = max(int(round(match_bw_s / max(dt, 1e-9))), 1)
        wp = boxcar(path_rate(t, x, y, d, which), w)
        wg = boxcar(np.interp(_align.to_inertial(rec, t) + extra_lag, gt, gyaw), w)
        rows.append(dict(i=i, t=t, frac=d / d[-1], v=v, wp=wp, wg=wg, beta=wp - wg,
                         dwp=np.gradient(wp, t)))
    return rec, rows


def _corner_masks(rec, rows):
    """Per-lap boolean masks for each detected corner, by normalized best-lap odometer."""
    # Corner boundaries are in BEST-lap odometer metres; every lap is projected onto them by
    # normalized distance, which is what `corners.lap_corner_stats` does for a well-matched lap.
    ref_total = rec.lap(int(np.where(rec.lap_ids == rec.best_lap_id)[0][0]))[4][-1]
    lo = rec.corner_enter / ref_total
    hi = rec.corner_exit / ref_total
    masks = []
    for r in rows:
        masks.append([(r["frac"] >= a) & (r["frac"] <= b) for a, b in zip(lo, hi, strict=True)])
    corner_any = [np.any(np.vstack(m), axis=0) for m in masks]
    return masks, corner_any


def report(key: str, which: str = "kappa"):
    rec, rows = analyse(key, which=which)
    masks, corner_any = _corner_masks(rec, rows)

    allbeta = np.concatenate([r["beta"] for r in rows])
    allwp = np.concatenate([r["wp"] for r in rows])
    allwg = np.concatenate([r["wg"] for r in rows])
    alldwp = np.concatenate([r["dwp"] for r in rows])
    inc = np.concatenate(corner_any)
    # The straights are the NOISE FLOOR: the kart is going straight, so both channels should read
    # ~0 there and beta_dot should be ~0. Whatever beta_dot does on the straights is what the two
    # channels' noise does, and a corner reading has to beat it to mean anything.
    print(f"beta_dot rms: corners {np.sqrt(np.mean(allbeta[inc]**2)):.4f} rad/s, "
          f"STRAIGHTS (noise floor) {np.sqrt(np.mean(allbeta[~inc]**2)):.4f} rad/s "
          f"-> corner/floor {np.sqrt(np.mean(allbeta[inc]**2) / np.mean(allbeta[~inc]**2)):.2f}x")
    print(f"  for scale: omega_path rms corners {np.sqrt(np.mean(allwp[inc]**2)):.4f} / "
          f"straights {np.sqrt(np.mean(allwp[~inc]**2)):.4f} rad/s; beta_dot is "
          f"{np.sqrt(np.mean(allbeta[inc]**2)) / np.sqrt(np.mean(allwp[inc]**2)):.3f} of "
          f"omega_path in corners")

    # --- the confound that decides it ------------------------------------------------------
    # A pure SCALE error between the two channels produces beta_dot = (1-g)*omega_path exactly.
    # If beta_dot is mostly that, it is not sideslip: it is the gyro's 2 % under-read wearing a
    # physical name. Regress beta_dot on omega_path and see what survives.
    g = float(np.sum(allwg * allwp) / np.sum(allwp**2))
    resid = allbeta - (1.0 - g) * allwp
    print(f"gain(gyro on path) = {g:.4f} -> a pure scale error explains "
          f"{1 - np.var(resid) / np.var(allbeta):.3f} of beta_dot's variance; "
          f"residual rms {np.sqrt(np.mean(resid[inc]**2)):.4f} rad/s in corners")
    print(f"r(beta_dot, omega_path) = {_corr(allbeta, allwp):+.3f}")

    # ...and the OTHER forgery: a residual clock offset delta, which produces
    # beta_dot = -delta * d(omega)/dt. Fit both at once and see how much of beta_dot survives
    # a model that contains no sideslip at all.
    A = np.column_stack([allwp, alldwp])
    coef, *_ = np.linalg.lstsq(A, allbeta, rcond=None)
    r2 = A @ coef
    print(f"scale + residual-lag model (no sideslip in it): scale {coef[0]:+.4f}, "
          f"implied residual offset {-coef[1] * 1000:+.0f} ms -> explains "
          f"{1 - np.var(allbeta - r2) / np.var(allbeta):.3f} of beta_dot's variance; "
          f"what is left rms {np.sqrt(np.mean((allbeta - r2)[inc] ** 2)):.4f} rad/s in corners "
          f"vs a {np.sqrt(np.mean(allbeta[~inc] ** 2)):.4f} noise floor")
    # How much beta_dot a residual misalignment could forge on its own — the honest error bar on
    # the alignment, now that it is measured rather than fitted.
    for delta in (0.02, 0.05, 0.08):
        forged = delta * alldwp
        print(f"  a {delta * 1000:.0f} ms error in that alignment alone forges "
              f"{np.sqrt(np.mean(forged[inc] ** 2)):.4f} rad/s of beta_dot in corners")

    # --- does it repeat, corner by corner, across laps? ------------------------------------
    # The test a coaching channel has to pass: per-corner mean must separate CORNERS by more
    # than it wanders between LAPS of the same corner.
    per = np.full((len(rows), len(rec.corner_cid)), np.nan)
    perr = np.full_like(per, np.nan)
    for li, r in enumerate(rows):
        for ci, m in enumerate(masks[li]):
            if int(m.sum()) >= 4:
                per[li, ci] = float(np.mean(r["beta"][m]))
                perr[li, ci] = float(np.mean((r["beta"] - (1.0 - g) * r["wp"])[m]))
    for name, arr in (("beta_dot", per), ("scale-corrected", perr)):
        cm = np.nanmean(arr, axis=0)
        within = np.nanmean(np.nanstd(arr, axis=0))
        between = np.nanstd(cm)
        print(f"per-corner {name}: between-corner sd {between:.4f} vs within-corner "
              f"(across-lap) sd {within:.4f}  ->  separation ratio {between / within:.2f}")
    print(f"  per-corner mean beta_dot (rad/s), C1..C{len(rec.corner_cid)}:")
    print("   ", " ".join(f"{v:+.3f}" for v in np.nanmean(per, axis=0)))
    print("    dir:", " ".join(f"{d:+d}   " for d in rec.corner_dir))

    # --- does it separate LAPS? ------------------------------------------------------------
    # ...with the null beside it: the SAME statistic taken on the straights, where there is no
    # scrub to measure. If the straights track lap time just as well, the corner number is
    # measuring how noisy a lap was, not how much the driver scrubbed.
    lap_beta = np.nanmean(np.abs(per), axis=1)
    lap_null = np.asarray([np.sqrt(np.mean(r["beta"][~m] ** 2))
                           for r, m in zip(rows, corner_any, strict=True)])
    lap_t = np.asarray([rec.lap_times[r["i"]] for r in rows])
    print(f"r(per-lap mean |beta_dot| IN CORNERS, lap time) = {_corr(lap_beta, lap_t):+.3f}; "
          f"NULL r(same on STRAIGHTS, lap time) = {_corr(lap_null, lap_t):+.3f} "
          f"over {len(lap_t)} laps")
    return rec, per, perr


def misalignment_sensitivity(key: str, which: str = "kappa"):
    """What the verdict would have been at other alignments — including the one this probe used
    to fit for itself. If beta_dot's corner RMS moves a lot across this row, the channel is a
    clock measurement; if it barely moves, the clock is not what is limiting it."""
    print(f"\n--- {key} ({which}): beta_dot vs a DELIBERATE misalignment ---")
    for extra in (-0.50, -0.25, -0.05, 0.0, +0.05, +0.25, +0.50):
        rec, rows = analyse(key, extra_lag=extra, which=which, quiet=True)
        _m, corner_any = _corner_masks(rec, rows)
        b = np.concatenate([r["beta"] for r in rows])
        inc = np.concatenate(corner_any)
        print(f"  extra lag {extra:+.2f}s  corners {np.sqrt(np.mean(b[inc]**2)):.4f}  "
              f"straights {np.sqrt(np.mean(b[~inc]**2)):.4f}  "
              f"ratio {np.sqrt(np.mean(b[inc]**2) / np.mean(b[~inc]**2)):.2f}x")


def bandwidth_sweep(key: str, which: str = "kappa"):
    """Is the verdict an artefact of the 1.3 s matched window? Re-measure corner-vs-floor at
    every bandwidth from the gyro's own 0.30 s up to the GPS trace's 1.3 s and past it."""
    print(f"\n--- {key} ({which}): bandwidth sensitivity "
          f"(corner beta_dot rms vs straight-line floor) ---")
    for bw in (0.3, 0.5, 0.8, 1.3, 2.0):
        rec, rows = analyse(key, match_bw_s=bw, which=which, quiet=True)
        _m, corner_any = _corner_masks(rec, rows)
        b = np.concatenate([r["beta"] for r in rows])
        inc = np.concatenate(corner_any)
        print(f"  bw={bw:.1f}s  corners {np.sqrt(np.mean(b[inc]**2)):.4f}  "
              f"straights {np.sqrt(np.mean(b[~inc]**2)):.4f}  "
              f"ratio {np.sqrt(np.mean(b[inc]**2) / np.mean(b[~inc]**2)):.2f}x")


if __name__ == "__main__":
    out = {}
    for k in ("0060", "0062"):
        out[k] = report(k)
        report(k, which="tangent")   # the same verdict off the independent reference
    # Cross-recording reproducibility of the per-corner signature (same track, different days).
    a, b = out["0060"], out["0062"]
    n = min(len(a[0].corner_cid), len(b[0].corner_cid))
    for name, ia in (("beta_dot", 1), ("scale-corrected", 2)):
        pa = np.nanmean(a[ia], axis=0)[:n]
        pb = np.nanmean(b[ia], axis=0)[:n]
        print(f"\ncross-recording per-corner {name}: r={_corr(pa, pb):+.3f} over {n} corners")
        print("  0060:", " ".join(f"{v:+.3f}" for v in pa))
        print("  0062:", " ".join(f"{v:+.3f}" for v in pb))
    for k in ("0060", "0062"):
        misalignment_sensitivity(k)
        bandwidth_sweep(k)
