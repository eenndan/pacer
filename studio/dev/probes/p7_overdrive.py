"""P7 — does entering a corner faster make it slower? The overdriving detector, measured (F4).

F4 proposed a per-corner detector: over the clean laps, the rank correlation of entry speed with
the time spent in the corner, tested against a WITHIN-LAP permutation null (each lap's corner labels
shuffled, so a lap that is fast everywhere stays fast everywhere and only corner identity dies),
family-wise over corners, gated at 8 laps, replicated across both D24 recordings before any UI. A
corner "is overdriven" when faster entry goes with a SLOWER corner at FWER < 0.05.

This probe builds exactly that and then asks what it is worth:

  * THE DETECTOR on five recordings, on the backlog's outcome (time in the corner) and on two
    others a coach would also accept as the cost of overdriving: the corner plus the straight after
    it, and the exit speed. Every number is read through the app's own accessors
    (`Session.corners.lap_corner_stats`, `corners.segment_times` through the memoized warp).
  * NEGATIVE control: entry speeds shuffled across laps, so nothing can be overdriven — how often
    does the detector still name a corner, selection over corners included?
  * POSITIVE control: an overdriving effect of known size planted into each corner in turn — how
    often is THAT corner named?
  * A COVARIATE cross-check: partial rank correlation given the rest of the lap, lap order and stint,
    with its own placebo (a corner's entry speed against a non-adjacent corner's time).
  * REPLICATION: odd/even and first/second half of each session, and the same corner across
    recordings of the same track (corner identity checked by apex position, not by label).
  * THE MEASUREMENT: every entry and exit speed is read at a projected boundary, inside a braking
    or acceleration zone. The probe measures how often each boundary is a spatial match rather than
    interpolated, the longitudinal residual of the projected point against the reference lap's
    point, the speed gradient there, and what the detector says when every boundary is re-read at
    the point level with the reference point instead.

The verdict is written up in `studio/docs/refused-2026-09.md` §6. Every number there comes from
here.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p7_overdrive
    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p7_overdrive 0060 0062

SAFETY, enforced rather than promised:
  * EVERY FOOTAGE FOLDER IS READ-ONLY. Paths under ~/Desktop are only ever opened for reading, and
    every file in each folder read is snapshotted (size, mtime) before and after each load.
    `D24/GX010060.MP4` is the 2.4 MB JSON stub that overwrote the owner's footage; it is never
    listed here and never opened.
  * `Session.load` UPSERTS INTO THE LIBRARY, so `studio.dev._jail` diverts every app-support seam
    BEFORE any studio import resolves one, the probe refuses to load if the seam still resolves to
    the real directory, and that directory is snapshotted before and after the whole run. No
    subprocess loads the app, and the probe writes no file of its own.
"""

from __future__ import annotations

import os
import sys
import time

import numpy as np

DESKTOP = os.path.expanduser("~/Desktop")
# name -> (folder, chapters, restore the saved start line the way the app does on open, track)
RECORDINGS = {
    "0060": ("D24", ["GX020060.MP4", "GX030060.MP4"], False, "D24"),
    "0062": ("D24", ["GX010062.MP4", "GX020062.MP4", "GX030062.MP4"], False, "D24"),
    "Sandown": ("Sandown_09_05_2026", ["GX010059.MP4", "GX020059.MP4", "GX030059.MP4"], True,
                "Sandown"),
    "SD_30_08": ("SD_30_08_26", ["GX010065.MP4", "GX020065.MP4"], True, "Sandown"),
    "Sandown3h": ("Sandown 3h 2026", ["GX010064.MP4", "GX020064.MP4", "GX030064.MP4"], True,
                  "Sandown"),
}
_STUB = "GX010060.MP4"
_REAL_APP_SUPPORT = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "pacer")

MIN_LAPS = 8            # the backlog's gate
ALPHA = 0.05
PERMS = 20000           # the detector's own null
NEG_OUTER = 300         # shuffled-label replicates (each with INNER_PERMS of its own)
INNER_PERMS = 1000
PLANT_S_PER_KMH = (0.01, 0.02, 0.04, 0.08)  # s lost in the corner per km/h of extra entry speed
PLANT_PERMS = 2000
PLACEBO_OUTER = 200
OUTCOMES = ("corner", "through", "exit")
OUTCOME_LABEL = {"corner": "time in corner", "through": "corner + next straight",
                 "exit": "exit speed (slower = +)"}


def _folder_state(path: str) -> dict:
    if not os.path.isdir(path):
        return {}
    return {n: (os.stat(os.path.join(path, n)).st_size, os.stat(os.path.join(path, n)).st_mtime_ns)
            for n in sorted(os.listdir(path)) if not n.startswith(".")}


# ------------------------------------------------------------------------------------ extraction
def extract(session) -> dict:
    """Every per-(clean lap x corner) number the analysis reads, through the app's own accessors."""
    import pacer
    from studio import corners as corners_alg

    corner_list, total_ref = session.corners.basis()
    n_c = len(corner_list)
    best = session.best_lap_id()
    _bt, ref_xs, ref_ys, _bv, ref_cum = session._lap_columns(best)
    frame = np.asarray([b for c in corner_list for b in (c.enter, c.exit)], float)
    stint_of = {lid: s.index for s in session.stats.stints() for lid in s.lap_ids}
    out = {k: [] for k in ("lap_ids", "lap_time", "stint", "seg", "edges", "knot", "entry_v",
                           "exit_v", "corner_t", "laps")}
    for i in session.consistency_lap_ids():
        dist, speed, elapsed = session._lap_arrays(i)
        total_lap = float(dist[-1])
        align = session.corners.lap_alignment(i, total_lap)
        st = session.corners.lap_corner_stats(i)
        if len(st) != n_c:
            continue
        edges = corners_alg._window_edges(corner_list, total_ref, total_lap, alignment=align)
        seg = corners_alg.segment_times(corner_list, total_ref, dist, elapsed, alignment=align)
        # The warp's knots are exactly the boundaries whose spatial match survived its gates.
        knots = set(np.round(align[0], 9).tolist()) if align is not None else set()
        _lt, lxs, lys, _lv, _lcum = session._lap_columns(i)
        out["lap_ids"].append(i)
        out["lap_time"].append(float(session.lap_time(i)))
        out["stint"].append(stint_of.get(i, 0))
        out["seg"].append(seg)
        out["edges"].append(edges)
        out["knot"].append([round(float(b), 9) in knots for b in frame])
        out["entry_v"].append([s.entry_speed for s in st])
        out["exit_v"].append([s.exit_speed for s in st])
        out["corner_t"].append([s.time for s in st])
        m = len(dist)
        out["laps"].append((np.asarray(dist, float), np.asarray(speed, float),
                            np.asarray(elapsed, float), np.asarray(lxs[:m], float),
                            np.asarray(lys[:m], float)))
    data = {k: (v if k == "laps" else np.asarray(v)) for k, v in out.items()}
    apex = []
    for _label, x, y, _d in session.corners.corner_map_markers():
        g = session.cs.global_(pacer.Vec3f(float(x), float(y), 0.0))
        apex.append((float(g.lat), float(g.lon)))
    data.update(cid=np.asarray([c.cid for c in corner_list]), frame=frame,
                apex_latlon=np.asarray(apex, float),
                ref=(np.asarray(ref_xs, float), np.asarray(ref_ys, float),
                     np.asarray(ref_cum, float)))
    return data


def load(name: str) -> dict | None:
    from studio.session import Session

    folder, files, saved, _track = RECORDINGS[name]
    root = os.path.join(DESKTOP, folder)
    paths = [os.path.join(root, f) for f in files]
    assert folder != "D24" or _STUB not in files, "the destroyed stub is never opened"
    if not all(os.path.isfile(p) for p in paths):
        print(f"{name}: footage missing under {root} — skipped")
        return None
    before = _folder_state(root)
    t0 = time.time()
    session = Session.load(paths)
    restored = session.restore_saved_timing_lines() if saved else None
    data = extract(session)
    if _folder_state(root) != before:
        raise SystemExit(f"REFUSING TO CONTINUE: a file in {root} changed during the load.")
    print(f"{name}: {folder}/{', '.join(files)} loaded in {time.time() - t0:.0f} s; saved line "
          f"restored: {restored}; {len(data['lap_ids'])} clean laps x {len(data['cid'])} corners; "
          f"{folder} unchanged (size + mtime)")
    return data


# ------------------------------------------------------------------------------------ statistics
def ranks(a) -> np.ndarray:
    """Column-wise average ranks (ties share their mean rank)."""
    a = np.asarray(a, float)
    col = a.ndim == 1
    a2 = a[:, None] if col else a
    out = np.empty_like(a2)
    for j in range(a2.shape[1]):
        v = a2[:, j]
        order = np.argsort(v, kind="stable")
        r = np.empty(len(v))
        r[order] = np.arange(len(v), dtype=float)
        _u, inv = np.unique(v, return_inverse=True)
        out[:, j] = (np.bincount(inv, r) / np.bincount(inv))[inv]
    return out[:, 0] if col else out


def _unit_cols(m: np.ndarray) -> np.ndarray:
    m = m - m.mean(axis=0)
    norm = np.linalg.norm(m, axis=0)
    norm[norm == 0] = 1.0
    return m / norm


def outcome_matrix(seg, corner_t, exit_v, which) -> np.ndarray:
    """(laps x corners) outcome, signed so that + is the overdriving direction (slower)."""
    if which == "corner":
        return np.asarray(corner_t, float)
    if which == "through":
        return seg[:, 1::2] + seg[:, 2::2]      # corner k + the straight after it
    if which == "exit":
        return -np.asarray(exit_v, float)
    raise ValueError(which)


def detect_within_lap(x, y, perms: int, rng) -> tuple[np.ndarray, np.ndarray]:
    """THE BACKLOG'S DETECTOR. Spearman rho of x and y per corner; the null shuffles corner labels
    of y independently within each lap (every lap keeps its own values, so lap-level pace, tyre and
    traffic stay in the null); family-wise p = share of null draws whose max |rho| over corners
    reaches this corner's |rho|. Returns (rho per corner, p_fwer per corner)."""
    zx, zy = _unit_cols(ranks(x)), _unit_cols(ranks(y))
    rho = (zx * zy).sum(axis=0)
    n, k = zx.shape
    null_max = np.empty(perms)
    block = 250
    for s in range(0, perms, block):
        m = min(block, perms - s)
        idx = np.argsort(rng.random((m, n, k)), axis=2)
        zyp = np.take_along_axis(np.broadcast_to(zy, (m, n, k)), idx, axis=2)
        null_max[s:s + m] = np.abs((zx[None] * zyp).sum(axis=1)).max(axis=1)
    p = (1 + (null_max[None, :] >= np.abs(rho)[:, None] - 1e-12).sum(axis=1)) / (1 + perms)
    return rho, p


def _resid(y: np.ndarray, z: np.ndarray) -> np.ndarray:
    design = np.column_stack([np.ones(len(z)), z])
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    return y - design @ beta


def detect_covariate(data, x, y, perms: int, rng, pairs=None):
    """CROSS-CHECK. Partial Spearman of x_k and y_m given rank(the rest of the lap), lap order and
    stint; the null permutes laps of the x residuals jointly across the family; max |r| FWER.
    `pairs` = [(k, m)] (default: each corner with itself). The rest of the lap removes the
    approach straight, the corner and the straight after it (and corner m's own window)."""
    seg = data["seg"]
    n = len(data["lap_ids"])
    k_n = x.shape[1]
    pairs = pairs or [(k, k) for k in range(k_n)]
    stint = data["stint"]
    base = [np.arange(n, dtype=float)] + [(stint == s).astype(float) for s in np.unique(stint)[1:]]
    rx, ry = [], []
    for k, m in pairs:
        rest = data["lap_time"] - seg[:, 2 * k] - seg[:, 2 * k + 1] - seg[:, 2 * k + 2]
        if m != k:
            rest = rest - seg[:, 2 * m + 1] - seg[:, 2 * m + 2]
        z = np.column_stack([ranks(rest)] + base)
        rx.append(_resid(ranks(x[:, k]), z))
        ry.append(_resid(ranks(y[:, m]), z))
    rx, ry = _unit_cols(np.column_stack(rx)), _unit_cols(np.column_stack(ry))
    r = (rx * ry).sum(axis=0)
    order = np.argsort(rng.random((perms, n)), axis=1)
    null_max = np.abs(np.einsum("pnk,nk->pk", rx[order], ry)).max(axis=1)
    p = (1 + (null_max[None, :] >= np.abs(r)[:, None] - 1e-12).sum(axis=1)) / (1 + perms)
    return r, p


def flagged(cids, r, p, sign=+1) -> list[str]:
    return [f"C{c}" for c, rr, pp in zip(cids, r, p, strict=True) if pp < ALPHA and sign * rr > 0]


# ------------------------------------------------------------------------------------ measurement
def boundary_readings(data) -> dict:
    """Per lap and boundary: the speed gradient (km/h per m, +/-2 m), the signed longitudinal
    residual of the projected point against the reference lap's point (m, + = lands ahead), and
    the lap odometer of the point LEVEL with the reference point (iterated along its tangent) —
    with entry/exit speeds, segment times and window path lengths re-read there."""
    from studio import corners as corners_alg

    ref_xs, ref_ys, ref_cum = data["ref"]
    frame = data["frame"]
    tx, ty = corners_alg._unit_tangents(ref_xs, ref_ys)
    ax, ay = np.interp(frame, ref_cum, ref_xs), np.interp(frame, ref_cum, ref_ys)
    atx, aty = np.interp(frame, ref_cum, tx), np.interp(frame, ref_cum, ty)
    n, nb = len(data["lap_ids"]), len(frame)
    out = {k: np.zeros((n, nb)) for k in ("grad", "resid", "level")}
    k_n = nb // 2
    out.update({k: np.zeros((n, k_n))
                for k in ("entry_v", "exit_v", "corner_t", "path_m", "app_path_m")})
    out["level_seg"] = np.zeros((n, nb + 1))
    for i, (dist, speed, elapsed, xs, ys) in enumerate(data["laps"]):
        s = data["edges"][i, 1:-1]

        def long_resid(pos, xs=xs, ys=ys, dist=dist):
            return (np.interp(pos, dist, xs) - ax) * atx + (np.interp(pos, dist, ys) - ay) * aty
        out["resid"][i] = long_resid(s)
        out["grad"][i] = (np.interp(s + 2.0, dist, speed) - np.interp(s - 2.0, dist, speed)) / 4.0
        level = s.copy()
        for _ in range(6):
            level = np.clip(level - long_resid(level), 0.0, float(dist[-1]))
        level = np.maximum.accumulate(level)
        out["level"][i] = level
        seg = np.diff(np.interp(np.concatenate(([0.0], level, [dist[-1]])), dist, elapsed))
        out["level_seg"][i] = seg
        out["entry_v"][i] = np.interp(level[0::2], dist, speed)
        out["exit_v"][i] = np.interp(level[1::2], dist, speed)
        out["corner_t"][i] = seg[1::2]
        out["path_m"][i] = level[1::2] - level[0::2]
        out["app_path_m"][i] = s[1::2] - s[0::2]
    return out


# ------------------------------------------------------------------------------------ report
def report(name: str, data: dict, rng) -> dict:
    cids = data["cid"]
    n, k_n = data["entry_v"].shape
    print(f"\n{'=' * 100}\n{name}: {n} clean laps x {k_n} corners"
          + ("" if n >= MIN_LAPS else f" — BELOW THE {MIN_LAPS}-LAP GATE"))
    x = data["entry_v"]
    ys = {w: outcome_matrix(data["seg"], data["corner_t"], data["exit_v"], w) for w in OUTCOMES}
    b = boundary_readings(data)
    lvys = {w: outcome_matrix(b["level_seg"], b["corner_t"], b["exit_v"], w) for w in OUTCOMES}
    res = {"n": n, "rho": {}, "p": {}}

    print("\nTHE DETECTOR (within-lap null, FWER over corners)")
    for reading, xx, yy in (("app", x, ys), ("level", b["entry_v"], lvys)):
        for w in OUTCOMES:
            r, p = detect_within_lap(xx, yy[w], PERMS, rng)
            res["rho"][(reading, w)], res["p"][(reading, w)] = r, p
            j = int(np.argmax(r))
            print(f"  {reading:<5} {OUTCOME_LABEL[w]:<24} overdriven {flagged(cids, r, p) or '—'}; "
                  f"faster in, faster through {flagged(cids, r, p, -1) or '—'}; "
                  f"strongest + C{cids[j]} rho {r[j]:+.2f} p_fwer {p[j]:.3f}")
    r = res["rho"][("app", "corner")]
    print("  per corner (app, time in corner): "
          + " ".join(f"C{c}:{rr:+.2f}" for c, rr in zip(cids, r, strict=True)))

    print(f"\nNEGATIVE control: entry speeds shuffled across laps, {NEG_OUTER} replicates x "
          f"{INNER_PERMS} permutations")
    for w in ("corner", "exit"):
        any_flag = od = 0
        for _ in range(NEG_OUTER):
            rr, pp = detect_within_lap(x[rng.permutation(n)], ys[w], INNER_PERMS, rng)
            any_flag += bool((pp < ALPHA).any())
            od += bool(((pp < ALPHA) & (rr > 0)).any())
        print(f"  app {OUTCOME_LABEL[w]:<24}: names any corner {any_flag / NEG_OUTER:.1%}, "
              f"names an overdriven corner {od / NEG_OUTER:.1%}")

    print("\nPOSITIVE control: planted into each corner in turn (app, time in corner)")
    for beta in PLANT_S_PER_KMH:
        hits, induced = 0, []
        for k in range(k_n):
            y = ys["corner"].copy()
            y[:, k] += beta * (x[:, k] - x[:, k].mean())
            rr, pp = detect_within_lap(x, y, PLANT_PERMS, rng)
            hits += bool(pp[k] < ALPHA and rr[k] > 0)
            induced.append(rr[k])
        print(f"  {beta:.2f} s per km/h: named {hits} of {k_n} corners; planted corner's rho "
              f"median {np.median(induced):+.2f} [{min(induced):+.2f}, {max(induced):+.2f}]")

    print(f"\nCOVARIATE cross-check (app, time in corner) and its placebo ({PLACEBO_OUTER} draws)")
    r, p = detect_covariate(data, x, ys["corner"], PERMS, rng)
    print(f"  overdriven {flagged(cids, r, p) or '—'}; faster in, faster through "
          f"{flagged(cids, r, p, -1) or '—'}")
    od = other = 0
    for _ in range(PLACEBO_OUTER):
        pairs = []
        for k in range(k_n):
            far = [m for m in range(k_n) if (m - k) % k_n not in (0, 1, k_n - 1)]
            pairs.append((k, int(rng.choice(far))))
        rr, pp = detect_covariate(data, x, ys["corner"], INNER_PERMS, rng, pairs)
        od += bool(((pp < ALPHA) & (rr > 0)).any())
        other += bool(((pp < ALPHA) & (rr < 0)).any())
    print(f"  placebo (a corner's entry vs a non-adjacent corner's time): names an overdriven "
          f"corner {od / PLACEBO_OUTER:.1%}, names one the other way {other / PLACEBO_OUTER:.1%}")

    print("\nREPLICATION inside the session (app readings)")
    for label, idx in (("odd laps", slice(0, None, 2)), ("even laps", slice(1, None, 2)),
                       ("first half", slice(0, n // 2)), ("second half", slice(n // 2, None))):
        cells = []
        for w in OUTCOMES:
            rr, pp = detect_within_lap(x[idx], ys[w][idx], 5000, rng)
            cells.append(f"{w}: {flagged(cids, rr, pp) or '—'}")
        print(f"  {label:<11} ({len(range(n)[idx])} laps): " + "; ".join(cells))

    print("\nTHE MEASUREMENT at each corner's boundaries")
    print("  corner  enter: matched  resid sd m  grad km/h/m  x sd => km/h | exit: matched  "
          "resid sd m | entry sd km/h | window path m, app (sd) | level (sd, range)")
    for k in range(k_n):
        e, xk = 2 * k, 2 * k + 1
        g_e = float(np.median(b["grad"][:, e]))
        rs = float(b["resid"][:, e].std(ddof=1))
        print(f"  C{cids[k]:<5} {data['knot'][:, e].mean():6.0%}  {rs:9.2f}  {g_e:+10.2f}  "
              f"{abs(g_e) * rs:12.2f} | {data['knot'][:, xk].mean():6.0%}  "
              f"{b['resid'][:, xk].std(ddof=1):9.2f} | {x[:, k].std(ddof=1):9.2f}     | "
              f"{b['app_path_m'][:, k].mean():5.1f} ({b['app_path_m'][:, k].std(ddof=1):4.2f})"
              f"            | {b['path_m'][:, k].mean():5.1f} "
              f"({b['path_m'][:, k].std(ddof=1):4.2f}, {np.ptp(b['path_m'][:, k]):4.1f})")
    res["boundary"] = b
    return res


def _rho(a, c) -> float:
    return float(np.corrcoef(ranks(a), ranks(c))[0, 1])


def explain_flags(name: str, data: dict, res: dict) -> None:
    """For every corner any variant flagged in the overdriving direction: where it comes from."""
    cids = data["cid"]
    b = res["boundary"]
    cells = sorted({int(k) for key, r in res["rho"].items()
                    for k in np.flatnonzero((res["p"][key] < ALPHA) & (r > 0))})
    for k in cells:
        by = [f"{rd}/{w} rho {res['rho'][(rd, w)][k]:+.2f} p_fwer {res['p'][(rd, w)][k]:.3f}"
              for rd, w in res["rho"]
              if res["p"][(rd, w)][k] < ALPHA and res["rho"][(rd, w)][k] > 0]
        print(f"\nFLAGGED {name} C{cids[k]} by " + ", ".join(by))
        for rd, x, ct, ex, path in (
                ("app", data["entry_v"][:, k], data["corner_t"][:, k], data["exit_v"][:, k],
                 b["app_path_m"][:, k]),
                ("level", b["entry_v"][:, k], b["corner_t"][:, k], b["exit_v"][:, k],
                 b["path_m"][:, k])):
            z = ranks(path)[:, None]
            partial_t = float(np.corrcoef(_resid(ranks(x), z), _resid(ranks(ct), z))[0, 1])
            partial_x = float(np.corrcoef(_resid(ranks(x), z), _resid(ranks(-ex), z))[0, 1])
            print(f"  {rd:<5}: rho(entry, time) {_rho(x, ct):+.2f}, rho(entry, slower exit) "
                  f"{_rho(x, -ex):+.2f}; window path {path.mean():.1f} m sd {path.std(ddof=1):.2f} "
                  f"range {path.min():.1f}-{path.max():.1f}; rho(entry, path) {_rho(x, path):+.2f}, "
                  f"rho(path, time) {_rho(path, ct):+.2f}; with path held: time {partial_t:+.2f}, "
                  f"slower exit {partial_x:+.2f}")
        e, xk = 2 * k, 2 * k + 1
        print(f"  enter boundary matched on {data['knot'][:, e].mean():.0%} of laps, exit on "
              f"{data['knot'][:, xk].mean():.0%}; rho(app entry speed, enter residual) "
              f"{_rho(data['entry_v'][:, k], b['resid'][:, e]):+.2f}; rho(app exit speed, enter "
              f"residual) {_rho(data['exit_v'][:, k], b['resid'][:, e]):+.2f}; rho(app exit speed, "
              f"exit residual) {_rho(data['exit_v'][:, k], b['resid'][:, xk]):+.2f}; rho(app entry, "
              f"level entry) {_rho(data['entry_v'][:, k], b['entry_v'][:, k]):+.2f}")


def cross_recording(results: dict, datas: dict) -> None:
    print(f"\n{'=' * 100}\nREPLICATION ACROSS RECORDINGS OF ONE TRACK (app readings)")
    by_track: dict[str, list[str]] = {}
    for name in results:
        by_track.setdefault(RECORDINGS[name][3], []).append(name)
    for track, names in by_track.items():
        for a_i, a in enumerate(names):
            for c in names[a_i + 1:]:
                la, lc = datas[a]["apex_latlon"], datas[c]["apex_latlon"]
                if len(la) != len(lc):
                    print(f"  {a} vs {c}: {len(la)} vs {len(lc)} corners — not comparable")
                    continue
                m_per_deg = 111_320.0
                off = np.hypot((la[:, 0] - lc[:, 0]) * m_per_deg,
                               (la[:, 1] - lc[:, 1]) * m_per_deg * np.cos(np.radians(la[:, 0])))
                for w in OUTCOMES:
                    ra, rc = results[a]["rho"][("app", w)], results[c]["rho"][("app", w)]
                    agree = int((np.sign(ra) == np.sign(rc)).sum())
                    print(f"  {track}: {a} vs {c} (same corners: apexes within {off.max():.1f} m) "
                          f"{w:<8} per-corner rho agree r = {np.corrcoef(ra, rc)[0, 1]:+.2f}, "
                          f"same sign on {agree} of {len(ra)}")
                # Every corner either recording named, read on the other one, both readings.
                for src, dst in ((a, c), (c, a)):
                    named = sorted({int(k) for key, r in results[src]["rho"].items()
                                    for k in np.flatnonzero((results[src]["p"][key] < ALPHA)
                                                            & (r > 0))})
                    for k in named:
                        cells = [f"{rd}/{w} {results[dst]['rho'][(rd, w)][k]:+.2f} "
                                 f"(p_fwer {results[dst]['p'][(rd, w)][k]:.3f})"
                                 for rd in ("app", "level") for w in OUTCOMES]
                        print(f"  C{datas[src]['cid'][k]} named on {src}; on {dst}: "
                              + ", ".join(cells))


def main() -> None:
    from studio.dev import _jail

    real_before = _folder_state(_REAL_APP_SUPPORT)
    jail = _jail.divert_app_support("pacer-p7-")
    from studio import library
    if os.path.abspath(library._app_support_dir()) == os.path.abspath(_REAL_APP_SUPPORT):
        raise SystemExit("the app-support seam still resolves to the real library — refusing to load")
    print(f"app-support seams diverted to {jail.dir}")
    results, datas = {}, {}
    for name in sys.argv[1:] or list(RECORDINGS):
        data = load(name)
        if data is None:
            continue
        datas[name] = data
        # Seeded per recording, so a recording's numbers do not depend on which others ran first.
        rng = np.random.default_rng([7, list(RECORDINGS).index(name)])
        results[name] = report(name, data, rng)
        explain_flags(name, data, results[name])
    if len(results) > 1:
        cross_recording(results, datas)
    if _folder_state(_REAL_APP_SUPPORT) != real_before:
        raise SystemExit("THE REAL APP-SUPPORT DIRECTORY CHANGED during this run — stop and look.")
    print("\nreal app-support directory unchanged (size + mtime)")


if __name__ == "__main__":
    main()
