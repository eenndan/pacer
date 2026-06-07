"""Prototype: swap the boxcar position smoother for alternative denoisers (RTS/Kalman,
Doppler-velocity-constrained) and measure the lap-timing residual vs the transponder, on BOTH
recordings, out of sample. Reuses the exact studio load pipeline (gate -> clean -> gps9 times)
and the exact validator alignment + residual stats, changing ONLY the position-smoothing step.

Smoothers (selectable):
  boxcar  : the shipping w=13 boxcar (baseline)
  raw     : no smoothing (w=1)
  rts     : constant-velocity Kalman forward + RTS backward smoother on (x,y), measurement =
            GPS position, process noise tuned; ignores Doppler (pure position smoother).
  rts_dop : constant-velocity Kalman+RTS but the velocity state is ALSO measured by the GPS9
            Doppler SPEED projected onto the instantaneous heading (a velocity pseudo-measurement),
            which is the Doppler-aided idea — speed is more accurate than position differencing.

We rebuild pacer.Laps from the smoothed local-metre track (converting back to lat/lon via the
coordinate system is unnecessary: we can build the CS from the smoothed lat/lon). To keep it
identical to the shipping geometry we smooth in LAT/LON like the boxcar does.
"""
from __future__ import annotations
import argparse, datetime as dt, json, sys
import numpy as np
import pacer
from studio import chapters, transponder
from studio.session import (_read_gpmf, _gate_quality, _clean, _gps9_times,
                            _gap_segments, tracks, _fit_start_line, _widen,
                            MIN_LAP_SAMPLES, MIN_LAP_TIME, LAP_BAND_LO, LAP_BAND_HI,
                            START_WIDEN)
import studio.dev._validate_wallclock as vw


# ----------------------------------------------------------------- smoothers
def boxcar(a, w=13):
    a = np.asarray(a, float)
    if w < 2 or len(a) < w:
        return a
    k = np.ones(w)
    return np.convolve(a, k, "same") / np.convolve(np.ones(len(a)), k, "same")


def rts_cv(z, dt_arr, meas_var, accel_psd, vel_meas=None, vel_var=None):
    """1-D constant-velocity Kalman forward filter + RTS backward smoother.

    State [pos, vel]. Process: white-noise acceleration with PSD `accel_psd` (m^2/s^3).
    Measurement: position `z` (variance `meas_var`). Optionally an extra velocity measurement
    `vel_meas` (variance `vel_var`) at each step (the Doppler-aided term). Returns smoothed pos.
    `dt_arr[i]` = dt from step i-1 to i (dt_arr[0] unused)."""
    n = len(z)
    xs = np.zeros((n, 2))   # filtered means
    Ps = np.zeros((n, 2, 2))
    xp = np.zeros((n, 2))   # predicted means (for smoother)
    Pp = np.zeros((n, 2, 2))
    # init
    x = np.array([z[0], 0.0])
    P = np.array([[meas_var, 0.0], [0.0, 100.0]])
    xs[0] = x; Ps[0] = P; xp[0] = x; Pp[0] = P
    for i in range(1, n):
        dt = dt_arr[i]
        F = np.array([[1.0, dt], [0.0, 1.0]])
        q = accel_psd
        Q = q * np.array([[dt**3/3, dt**2/2], [dt**2/2, dt]])
        x = F @ x
        P = F @ P @ F.T + Q
        xp[i] = x; Pp[i] = P
        # position update
        H = np.array([1.0, 0.0])
        S = H @ P @ H + meas_var
        K = (P @ H) / S
        x = x + K * (z[i] - H @ x)
        P = (np.eye(2) - np.outer(K, H)) @ P
        # optional velocity (Doppler) update
        if vel_meas is not None:
            Hv = np.array([0.0, 1.0])
            Sv = Hv @ P @ Hv + vel_var
            Kv = (P @ Hv) / Sv
            x = x + Kv * (vel_meas[i] - Hv @ x)
            P = (np.eye(2) - np.outer(Kv, Hv)) @ P
        xs[i] = x; Ps[i] = P
    # RTS backward
    xsm = xs.copy(); Psm = Ps.copy()
    for i in range(n - 2, -1, -1):
        dt = dt_arr[i + 1]
        F = np.array([[1.0, dt], [0.0, 1.0]])
        C = Ps[i] @ F.T @ np.linalg.inv(Pp[i + 1])
        xsm[i] = xs[i] + C @ (xsm[i + 1] - xp[i + 1])
        Psm[i] = Ps[i] + C @ (Psm[i + 1] - Pp[i + 1]) @ C.T
    return xsm[:, 0]


def smooth_track(samples, times, method, meas_std=2.0, accel_psd=40.0, vel_std=0.3):
    """Return new GPSSamples with lat/lon replaced per `method`. Works per gap-free segment."""
    if method == "raw":
        return samples
    lat = np.array([s.lat for s in samples])
    lon = np.array([s.lon for s in samples])
    segs = _gap_segments(times)
    out_lat = lat.copy(); out_lon = lon.copy()
    # convert deg->metres scale for the filter so meas_std is in metres
    lat0 = float(np.median(lat))
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * np.cos(np.radians(lat0))
    speeds = np.array([s.full_speed for s in samples])  # m/s, Doppler-derived 3D speed
    for lo, hi in segs:
        if hi - lo < 3:
            continue
        sl = slice(lo, hi)
        t = np.asarray(times[lo:hi], float)
        dt_arr = np.empty(hi - lo); dt_arr[0] = 0.1
        dt_arr[1:] = np.diff(t)
        ylat = (lat[sl] - lat0) * m_per_deg_lat
        ylon = (lon[sl] - float(np.median(lon))) * m_per_deg_lon
        if method == "boxcar":
            out_lat[sl] = boxcar(lat[sl]); out_lon[sl] = boxcar(lon[sl])
            continue
        vlat = vlon = vvar = None
        if method == "rts_dop":
            # velocity components from speed * heading; heading from finite-diff of raw position
            hx = np.gradient(ylon); hy = np.gradient(ylat)
            hn = np.hypot(hx, hy); hn[hn == 0] = 1.0
            vlon = speeds[sl] * hx / hn
            vlat = speeds[sl] * hy / hn
            vvar = vel_std**2
        smlat = rts_cv(ylat, dt_arr, meas_std**2, accel_psd, vlat, vvar)
        smlon = rts_cv(ylon, dt_arr, meas_std**2, accel_psd, vlon, vvar)
        out_lat[sl] = lat0 + smlat / m_per_deg_lat
        out_lon[sl] = float(np.median(lon)) + smlon / m_per_deg_lon
    new = []
    for i, s in enumerate(samples):
        new.append(pacer.GPSSample(lat=float(out_lat[i]), lon=float(out_lon[i]),
                                   altitude=float(s.altitude), full_speed=s.full_speed,
                                   ground_speed=s.ground_speed, timestamp_ms=s.timestamp_ms))
    return new


def build_laps(paths, method, **kw):
    samples, spans, naive, durations = _read_gpmf(paths)
    samples, spans, naive = _gate_quality(samples, spans, naive)
    samples, spans, naive = _clean(samples, spans, naive)
    times = _gps9_times(samples, spans, naive)
    samples = smooth_track(samples, times, method, **kw)
    laps = pacer.Laps()
    for s, t in zip(samples, times):
        laps.add_point(s, float(t))
    mn, mx = laps.min_max()
    clat, clon = (mn.y + mx.y) / 2, (mn.x + mx.x) / 2
    cs = pacer.CoordinateSystem(pacer.GPSSample(lat=clat, lon=clon, altitude=0))
    laps.set_coordinate_system(cs)
    track = tracks.detect_track(clat, clon)
    if track is not None:
        base = tracks.start_line_segment(track, cs)
        _fit_start_line(laps, base)
    else:
        laps.sectors = pacer.Sectors(start_line=_widen(laps.pick_random_start(), START_WIDEN), sector_lines=[])
        laps.update()
    return laps, cs


def valid_lap_ids(laps):
    basic = [(i, laps.lap_time(i)) for i in range(laps.laps_count())
             if laps.sample_count(i) >= MIN_LAP_SAMPLES and laps.lap_time(i) >= MIN_LAP_TIME]
    if not basic:
        return []
    med = float(np.median([t for _, t in basic]))
    lo, hi = LAP_BAND_LO * med, LAP_BAND_HI * med
    return [i for i, t in basic if lo <= t <= hi]


def evaluate(rec, csv, race_start, methods, **kw):
    paths = chapters.discover_siblings(rec)
    laptbl = transponder.parse_csv(csv)
    first_ms, last_ms, _ = vw.footage_gps9_window(paths)
    race_dt = vw._parse_when(race_start)
    completion = vw.cumulative_completion(laptbl)
    first_utc = dt.datetime.fromtimestamp(first_ms / 1000.0, dt.UTC)
    drv_start = vw.lap_being_driven(completion, (first_utc - race_dt).total_seconds())

    results = {}
    for method in methods:
        laps, cs = build_laps(paths, method, **kw)
        valid = valid_lap_ids(laps)
        app = np.array([laps.lap_time(i) for i in valid])
        lo = max(min(laptbl), drv_start - 12); hi = drv_start + 12
        start, corr, _ = vw.best_offset(app, laptbl, lo, hi)
        csv_ids = [start + k for k in range(len(valid))]
        csv_t = np.array([laptbl[i] for i in csv_ids])
        r = app - csv_t
        m = (app <= vw.RACING_MAX_S) & (csv_t <= vw.RACING_MAX_S)
        dropout = np.array([_has_dropout(laps, i) for i in valid])
        clean = m & ~dropout
        results[method] = {
            "corr": corr, "csv_range": [csv_ids[0], csv_ids[-1]], "n_valid": len(valid),
            "racing": vw.residual_stats(r[m]),
            "clean": vw.residual_stats(r[clean]),
            "dropout": vw.residual_stats(r[m & dropout]) if int((m & dropout).sum()) else None,
            "r": r.tolist(), "app": app.tolist(), "csv_t": csv_t.tolist(),
            "valid": valid, "csv_ids": csv_ids, "dropout_mask": dropout.tolist(), "clean_mask": clean.tolist(),
        }
    return results


def _has_dropout(laps, lid):
    lap = laps.get_lap(lid)
    ts = np.array([p.time for p in lap.points])
    return bool(len(ts) > 1 and np.diff(ts).max() > vw.DROPOUT_GAP_S)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("recording")
    ap.add_argument("csv")
    ap.add_argument("--race-start", default="2026-05-23 12:00:00Z")
    ap.add_argument("--methods", default="boxcar,raw,rts,rts_dop")
    ap.add_argument("--meas-std", type=float, default=2.0)
    ap.add_argument("--accel-psd", type=float, default=40.0)
    ap.add_argument("--vel-std", type=float, default=0.3)
    ap.add_argument("--dump", default=None)
    a = ap.parse_args([x for x in argv if x != "--"])
    res = evaluate(a.recording, a.csv, a.race_start, a.methods.split(","),
                   meas_std=a.meas_std, accel_psd=a.accel_psd, vel_std=a.vel_std)
    print(f"\n==== {a.recording} ====")
    for method, d in res.items():
        print(f"\n[{method}] csv {d['csv_range']} corr={d['corr']:.4f} n={d['n_valid']}")
        for k in ("racing", "clean", "dropout"):
            s = d[k]
            if s is None:
                print(f"  {k:8s}: (none)"); continue
            print(f"  {k:8s}: n={s['n']:3d} mean={s['mean']:+.4f} median={s['median']:+.4f} std={s['std']:.4f} rms={s['rms']:.4f}")
    if a.dump:
        json.dump(res, open(a.dump, "w"), indent=2)
        print(f"\nwrote {a.dump}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
