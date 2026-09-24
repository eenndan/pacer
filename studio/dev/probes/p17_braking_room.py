"""P17 — a RELATIVE braking-room hint in place of the absolute "brake ~N m later" (L6, board review UX-2).

THE DEFECT IT ANSWERS. The estimated brake-point hint ("Brake ~11 m later into C7 (est)" on a
coaching row, and the Stats ▸ BRAKING "m later" column — one number since #349) is positive at every
corner of every working-set recording. Its optimum is the apex-speed stop at the session's
demonstrated PEAK deceleration held constant from the onset (`driving.optimal_brake_distance` at
`driving.estimate_a_max`); a kart that ramps into and trails off the brake never sustains its peak,
so the optimum lands past the driver's brake point by construction and the hint cannot say WHERE.

THE CANDIDATES — the review's two, and the outcome-conditioned reading of its first:
  R3  "best-quartile onset vs median onset", read literally: the latest-braking quarter's onset
      (q75 of the reference-odometer onset) minus the median onset — an already-achieved reach.
  R2  the absolute hint (the median `metres_later`) minus the recording's across-corner median of it.
  R1  the median onset of the corner's FASTEST quarter of passes (the app's own time-in-corner) minus
      everyone's median onset: the rows' "done it N/37" logic — an outcome the driver achieved —
      with the brake point as the lever.

WHAT EACH MUST CLEAR — the brief's three questions, and a fourth the absolute hint never had to:
  (a) does its sign or magnitude vary across corners;
  (b) does it separate corners beyond its own noise bar: the corner's IQR at `coaching.SPREAD_MARGIN`
      (the repo's actionability standard) or, for a subset statistic, a seeded permutation null of
      random quarters (95 % band);
  (c) does at least one corner read "already on your best braking" (under BRAKE_HINT_MIN_M);
  (d) does the lap outcome back it: do later onsets go with QUICKER passes (Spearman rank correlation
      of onset against the time through [enter, exit], [enter − 60 m, exit] and [enter − 100 m,
      exit], permutation p), and is the latest-braking quarter quicker than a random quarter?
It also prints what the absolute hint tracks across corners: the braking zone's kinetic-energy drop
(v_onset² − v_apex²)/2 per kg, and the driver's own mean deceleration onset → apex over a_max. And
(L8, which relabelled the Stats ▸ BRAKING column as the model's bound rather than removing it) the
absolute figure's 95 % bootstrap band per Sandown corner, and where two days' bands do not overlap.

IT READS THE APP'S OWN LIST. The per-lap rows are rebuilt the way `Session._brake_rows` builds them
(clean laps, cells matched on track, the reference-odometer scale) and asserted EQUAL to
`_brake_rows()` before anything is computed, so the probe measures the list both braking surfaces
medianize, not a copy of it. Outcome windows go onto each lap through the SAME warp
`lap_brake_points` uses (`corners.project_boundaries` with the driving service's memo).

Its verdict is `studio/docs/refused-2026-09.md` §16.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p17_braking_room
    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p17_braking_room 0068 0064

SAFETY, enforced rather than promised (as p15/p16 enforce it):
  * EVERY FOOTAGE FOLDER IS READ-ONLY. Paths under ~/Desktop are only opened for reading, and every
    file in each folder is snapshotted (size, mtime) before and after each load.
  * `studio.dev._jail` diverts every app-support seam BEFORE any studio import that could reach one.
  * The real app-support directory is snapshotted before and after the run and must come back
    unchanged. This probe writes no file of its own. Every permutation null is seeded.
"""

from __future__ import annotations

import os
import sys

import numpy as np

DESKTOP = os.path.expanduser("~/Desktop")
_REAL_APP_SUPPORT = os.path.join(os.path.expanduser("~"), "Library", "Application Support",
                                 "pacer")
# (key, name, folder, first chapter). Every Sandown recording is clockwise; MK is the anticlockwise
# control on another track (12 corners against Sandown's 7).
RECORDINGS = [
    ("0068", "SD_19_09", "SD_19_09_26", "GX010068.MP4"),
    ("0064", "Sandown 3h", "Sandown 3h 2026", "GX010064.MP4"),
    ("0065", "SD_30_08", "SD_30_08_26", "GX010065.MP4"),
    ("0067", "MK_18_09", "MK_18_09_26", "GX010067.MP4"),
]
SANDOWN = ("0068", "0064", "0065")
N_PERM = 4000
# The outcome windows: (label, metres upstream of the corner's enter the window starts; None = the
# app's own time-in-corner). The brake zone starts up to BRAKE_MATCH_LEAD_M (30 m) before the enter,
# so the two longer windows hold every matched onset.
WINDOWS = (("corner", None), ("w60", 60.0), ("w100", 100.0))


def _folder_state(path: str) -> dict:
    if not os.path.isdir(path):
        return {}
    return {n: (os.stat(os.path.join(path, n)).st_size, os.stat(os.path.join(path, n)).st_mtime_ns)
            for n in sorted(os.listdir(path)) if not n.startswith(".")}


# ─── statistics ──────────────────────────────────────────────────────────────────────────────────
def _rank(x) -> np.ndarray:
    """0-based ranks, ties given the mean of the ranks they span."""
    x = np.asarray(x, float)
    r = np.empty(len(x))
    r[np.argsort(x, kind="mergesort")] = np.arange(len(x), dtype=float)
    for v in np.unique(x):
        tie = x == v
        if tie.sum() > 1:
            r[tie] = r[tie].mean()
    return r


def spearman_p(a, b, rng) -> tuple[float, float]:
    """Spearman ρ and its two-sided permutation p (N_PERM shuffles of one side)."""
    ra, rb = _rank(a), _rank(b)
    rho = float(np.corrcoef(ra, rb)[0, 1])
    null = np.array([np.corrcoef(ra, rng.permutation(rb))[0, 1] for _ in range(N_PERM)])
    return rho, float(np.mean(np.abs(null) >= abs(rho) - 1e-12))


def quarter_null(values, k: int, rng) -> tuple[float, float]:
    """The 95 % band of median(k values drawn at random) − median(all): what a quarter of the
    laps chosen by nothing at all moves the median by."""
    v = np.asarray(values, float)
    med = float(np.median(v))
    draws = np.array([np.median(rng.choice(v, size=k, replace=False)) - med for _ in range(N_PERM)])
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return float(lo), float(hi)


def median_ci(values, rng) -> tuple[float, float]:
    """The 95 % bootstrap band of the median (N_PERM resamples): the column's own sampling spread."""
    v = np.asarray(values, float)
    meds = np.median(rng.choice(v, size=(N_PERM, len(v)), replace=True), axis=1)
    lo, hi = np.percentile(meds, [2.5, 97.5])
    return float(lo), float(hi)


def _rng(rec_i: int, cid: int, test: int) -> np.random.Generator:
    """One seeded stream per (recording, corner, test), so a number does not depend on which
    recordings the command line asked for."""
    return np.random.default_rng((17, rec_i, cid, test))


# ─── the rows ────────────────────────────────────────────────────────────────────────────────────
def collect(s) -> dict[int, list[dict]]:
    """cid → one dict per clean lap with a matched brake point on a cell matched on track: the
    `_brake_rows` entry plus the lap's outcome times and its braking zone's energy drop.

    The rebuilt `_brake_rows` list is asserted equal to the app's before it is used."""
    from studio import corners

    ids = s.consistency_lap_ids()
    corner_list = s.corners.corner_list()
    ref_total = float(s.corners.basis()[1])
    index = {int(c.cid): k for k, c in enumerate(corner_list)}
    rebuilt: list[dict] = []
    per: dict[int, list[dict]] = {int(c.cid): [] for c in corner_list}
    for i in ids:
        bps = s.driving.lap_brake_points(i)
        if not bps:
            continue
        td = s._lap_time_dist(i)
        lap_total = float(td[1][-1]) if td is not None else 0.0
        scale = ref_total / lap_total if ref_total > 0 and lap_total > 0 else 1.0
        res = s.corners.lap_corner_resolved(i)
        kept = [bp for bp in bps
                if index.get(int(bp.cid)) is not None and index[int(bp.cid)] < len(res)
                and bool(res[index[int(bp.cid)]])]
        row = {bp.cid: (bp.actual_brake_dist * scale,
                        (bp.peak_decel_g / bp.a_max_g) if bp.a_max_g > 0 else None,
                        bp.metres_later, bp.optimal_brake_dist * scale) for bp in kept}
        if not row:
            continue
        rebuilt.append(row)
        # The outcome windows, projected onto this lap through the warp lap_brake_points uses.
        _times, dists, elapsed = s._lap_time_dist_elapsed(i)
        ldists, lspeed, _el = s.driving._lap_arrays(i)
        stats = s.corners.lap_corner_stats(i)
        refs = [max(0.0, min(ref_total, p)) for c in corner_list
                for p in (c.enter - 100.0, c.enter - 60.0, c.exit)]
        proj = corners.project_boundaries(refs, ref_total, float(dists[-1]),
                                          traces=s.driving._corner_traces(i),
                                          alignment=s.driving._corner_align(i, float(dists[-1])))
        at = np.interp(np.asarray(proj, float), dists, elapsed)
        for bp in kept:
            k = index[int(bp.cid)]
            st = stats[k]
            v_on = float(np.interp(bp.actual_brake_dist, ldists, lspeed)) / 3.6
            v_apex = float(st.apex_speed) / 3.6
            zone = float(st.apex_dist) - float(bp.actual_brake_dist)
            ke = (v_on ** 2 - v_apex ** 2) / 2.0
            per[int(bp.cid)].append(dict(
                lap=int(i), onset=bp.actual_brake_dist * scale, ml=float(bp.metres_later),
                corner=float(st.time), w100=float(at[3 * k + 2] - at[3 * k]),
                w60=float(at[3 * k + 2] - at[3 * k + 1]), ke=ke,
                eff=(ke / zone / (bp.a_max_g * 9.80665)) if zone > 0 and ke > 0 else float("nan")))
    assert rebuilt == s._brake_rows(), "the probe's rows are not the app's _brake_rows list"
    return per


# ─── one recording ───────────────────────────────────────────────────────────────────────────────
def measure(rec_i: int, per: dict[int, list[dict]], min_laps: int) -> list[dict]:
    """Every candidate and every test, per corner with at least `min_laps` matched laps."""
    out = []
    for cid, rows in per.items():
        if len(rows) < min_laps:
            continue
        on = np.array([r["onset"] for r in rows])
        ml = np.array([r["ml"] for r in rows])
        n = len(rows)
        k = max(int(round(n / 4)), 2)
        q25, q75 = np.percentile(ml, [25, 75])
        row = dict(cid=cid, n=n, abs=float(np.median(ml)), iqr=float(q75 - q25),
                   abs_ci=median_ci(ml, _rng(rec_i, cid, 30)),
                   reached=int(np.sum(ml <= 0.0)), ke=float(np.median([r["ke"] for r in rows])),
                   eff=float(np.nanmedian([r["eff"] for r in rows])),
                   r3=float(np.percentile(on, 75) - np.median(on)))
        fastest = np.argsort(np.array([r["corner"] for r in rows]), kind="mergesort")[:k]
        row["r1"] = float(np.median(on[fastest]) - np.median(on))
        row["r1_null"] = quarter_null(on, k, _rng(rec_i, cid, 0))
        latest = np.argsort(-on, kind="mergesort")[:k]
        for w, (label, _up) in enumerate(WINDOWS):
            t = np.array([r[label] for r in rows])
            row[f"rho_{label}"] = spearman_p(on, t, _rng(rec_i, cid, 10 + w))
            dt = float(np.median(t[latest]) - np.median(t))
            row[f"late_{label}"] = (dt, *quarter_null(t, k, _rng(rec_i, cid, 20 + w)))
        out.append(row)
    centre = float(np.median([r["abs"] for r in out]))
    for r in out:
        r["r2"] = r["abs"] - centre
    return out


def report(key: str, name: str, rows: list[dict], a_max: float, all_eff: list[float],
           margin: float, floor: float) -> None:
    centre = float(np.median([r["abs"] for r in rows]))
    e25, e50, e75 = np.nanpercentile(all_eff, [25, 50, 75])
    print(f"\n=== {name} ({key}): {len(rows)} corners; a_max {a_max:.3f} g; the driver's own mean "
          f"decel onset → apex is {e50:.2f} × a_max (IQR {e25:.2f}–{e75:.2f}); across-corner "
          f"median of the hint {centre:+.1f} m")
    print("    C   n | hint m  IQR m   laps at/past | R2 m  |R2|/IQR | R3 m | R1 m  null 95 %      |"
          " KE J/kg  decel/a_max")
    for r in rows:
        lo, hi = r["r1_null"]
        print(f"  {r['cid']:>3d} {r['n']:>3d} | {r['abs']:>+6.1f} {r['iqr']:>5.1f} {r['reached']:>5d}/{r['n']:<3d}"
              f"     | {r['r2']:>+5.1f} {abs(r['r2']) / r['iqr'] if r['iqr'] > 0 else float('inf'):>7.2f}"
              f"{'*' if abs(r['r2']) >= margin * r['iqr'] else ' '} | {r['r3']:>4.1f}"
              f"{'' if r['r3'] >= floor else '·'}{' ' if r['r3'] >= floor else ''}"
              f"| {r['r1']:>+5.1f} [{lo:>+5.1f}, {hi:>+5.1f}]{'*' if not lo <= r['r1'] <= hi else ' '} |"
              f" {r['ke']:>7.0f}  {r['eff']:>10.2f}")
    print("    C | ρ(onset, time) and permutation p      | latest quarter's median time − everyone's (s)")
    print("      |  corner       w60          w100        |  corner                  w60")
    for r in rows:
        parts = " ".join(f"{r[f'rho_{w}'][0]:>+5.2f} {r[f'rho_{w}'][1]:.3f}{'*' if r[f'rho_{w}'][1] < 0.05 else ' '}"
                         for w, _u in WINDOWS)
        late = " ".join(
            f"{r[f'late_{w}'][0]:>+6.3f} [{r[f'late_{w}'][1]:>+6.3f},{r[f'late_{w}'][2]:>+6.3f}]"
            f"{'Q' if r[f'late_{w}'][0] < r[f'late_{w}'][1] else 'S' if r[f'late_{w}'][0] > r[f'late_{w}'][2] else ' '}"
            for w in ("corner", "w60"))
        print(f"  {r['cid']:>3d} | {parts} | {late}")
    a = np.array([r["abs"] for r in rows])
    print(f"  the hint vs the zone's energy drop across corners: Spearman ρ {spearman_p(a, [r['ke'] for r in rows], np.random.default_rng(0))[0]:+.2f}")


def summary(results: dict[str, list[dict]], margin: float, floor: float) -> None:
    allr = [(k, r) for k, rows in results.items() for r in rows]
    n = len(allr)
    print(f"\n=== ACROSS {len(results)} RECORDINGS, {n} CORNERS")
    pos = sum(1 for _k, r in allr if r["abs"] > 0)
    print(f"  the absolute hint: positive at {pos}/{n}, at or above the {floor:g} m floor at "
          f"{sum(1 for _k, r in allr if r['abs'] >= floor)}/{n}; no clean lap braked at or past its "
          f"optimum at {sum(1 for _k, r in allr if r['reached'] == 0)}/{n}")
    for key, rows in results.items():
        a = np.array([r["abs"] for r in rows])
        rho = spearman_p(a, [r["ke"] for r in rows], np.random.default_rng(0))[0]
        print(f"    {key}: hint {a.min():+.1f}..{a.max():+.1f} m, Spearman ρ(hint, energy drop) {rho:+.2f}")
    sd = [(k, r) for k, r in allr if k in SANDOWN]
    mk = [(k, r) for k, r in allr if k not in SANDOWN]
    clears = [f"{k} C{r['cid']}" for k, r in allr if abs(r["r2"]) >= margin * r["iqr"]]
    print(f"  R2: clears {margin:g} × its own IQR at "
          f"{sum(1 for k, r in sd if abs(r['r2']) >= margin * r['iqr'])}/{len(sd)} Sandown and "
          f"{sum(1 for k, r in mk if abs(r['r2']) >= margin * r['iqr'])}/{len(mk)} MK corners "
          f"({', '.join(clears)})")
    if all(k in results for k in SANDOWN):
        by = {k: {r["cid"]: r["r2"] for r in results[k]} for k in SANDOWN}
        cids = sorted(set.intersection(*(set(v) for v in by.values())))
        same = [c for c in cids if len({np.sign(by[k][c]) for k in SANDOWN}) == 1]
        print("    R2 per Sandown corner (0068 / 0064 / 0065): " + "; ".join(
            f"C{c} {' / '.join(f'{by[k][c]:+.1f}' for k in SANDOWN)}" for c in cids)
            + f" — one sign on all three at {len(same)}/{len(cids)}")
        # L8: whether the ABSOLUTE figure — Stats ▸ BRAKING's "Bound m (est)" — says anything about
        # the day rather than the track: at one corner, do two days' medians sit outside each other's
        # bootstrap bands? (It is the question that decided relabelling the column over removing it.)
        at = {k: {r["cid"]: r for r in results[k]} for k in SANDOWN}
        moves = [c for c in cids if any(
            a["abs_ci"][1] < b["abs_ci"][0] or b["abs_ci"][1] < a["abs_ci"][0]
            for i, a in enumerate(at[k][c] for k in SANDOWN)
            for b in [at[k][c] for k in SANDOWN][i + 1:])]
        print("  the absolute figure per Sandown corner, median [95 % bootstrap] on 0068 / 0064 / 0065: "
              + "; ".join(f"C{c} " + " / ".join(
                  f"{at[k][c]['abs']:+.1f} [{at[k][c]['abs_ci'][0]:+.1f}, {at[k][c]['abs_ci'][1]:+.1f}]"
                  for k in SANDOWN) for c in cids)
              + f" — two days' bands do not overlap at {len(moves)}/{len(cids)} "
              f"({', '.join(f'C{c}' for c in moves) or 'none'})")
    print(f"  R3: never negative by construction; at or above {floor:g} m at "
          f"{sum(1 for _k, r in allr if r['r3'] >= floor)}/{n}, under it ('already on your best') at "
          f"{sum(1 for _k, r in allr if r['r3'] < floor)}/{n}")
    out1 = [f"{k} C{r['cid']}" for k, r in allr if not r["r1_null"][0] <= r["r1"] <= r["r1_null"][1]]
    edge = [f"{k} C{r['cid']}" for k, r in allr
            if abs(r["r1"] - r["r1_null"][1]) < 0.05 or abs(r["r1"] - r["r1_null"][0]) < 0.05]
    print(f"  R1: outside its permutation null at {len(out1)}/{n} ({', '.join(out1) or 'none'}); "
          f"on the band's edge at {', '.join(edge) or 'none'}")
    for w, _u in WINDOWS:
        neg = [f"{k} C{r['cid']}" for k, r in allr if r[f"rho_{w}"][0] < 0 and r[f"rho_{w}"][1] < 0.05]
        posc = sum(1 for _k, r in allr if r[f"rho_{w}"][0] > 0 and r[f"rho_{w}"][1] < 0.05)
        quick = sum(1 for _k, r in allr if r[f"late_{w}"][0] < r[f"late_{w}"][1])
        slow = [f"{k} C{r['cid']} {r[f'late_{w}'][0]:+.3f} s" for k, r in allr
                if r[f"late_{w}"][0] > r[f"late_{w}"][2]]
        print(f"  window {w:>6}: later onset ↔ quicker at p<0.05 {len(neg)}/{n} ({', '.join(neg)}), "
              f"↔ slower {posc}/{n}; the latest-braking quarter quicker than a random quarter "
              f"{quick}/{n}, slower {len(slow)}/{n} ({', '.join(slow) or 'none'})")


def main() -> int:
    from studio.dev import _jail

    real_before = _folder_state(_REAL_APP_SUPPORT)
    jail = _jail.divert_app_support("pacer-p17-")
    print(f"app-support seams diverted to {jail.dir}")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("PACER_NO_MEDIA", "1")
    from studio import chapters, coaching, coaching_panel
    from studio.session import Session

    wanted = set(sys.argv[1:])
    margin, floor = coaching.SPREAD_MARGIN, coaching_panel.BRAKE_HINT_MIN_M
    results: dict[str, list[dict]] = {}
    for rec_i, (key, name, folder, first) in enumerate(RECORDINGS):
        if wanted and key not in wanted:
            continue
        root = os.path.join(DESKTOP, folder)
        if not os.path.isfile(os.path.join(root, first)):
            print(f"\n=== {name} ({key}): SKIPPED — {root} is not present")
            continue
        paths = chapters.discover_siblings(os.path.join(root, first))
        before = _folder_state(root)
        s = Session.load(paths)
        per = collect(s)
        rows = measure(rec_i, per, coaching.MIN_BRAKE_LAPS)
        habits = s.coaching_brake_points()
        assert all(abs(habits[r["cid"]].metres_later - r["abs"]) < 1e-9 for r in rows), \
            "the hint column is not the app's BrakeHabit median"
        report(key, name, rows, s.driving._a_max(), [x["eff"] for v in per.values() for x in v],
               margin, floor)
        results[key] = rows
        if _folder_state(root) != before:
            raise SystemExit(f"ABORT: {root} changed during the load")
    if results:
        summary(results, margin, floor)
    if _folder_state(_REAL_APP_SUPPORT) != real_before:
        raise SystemExit("ABORT: the real app-support directory changed during the run")
    print("\nreal app-support directory unchanged; every footage folder unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
