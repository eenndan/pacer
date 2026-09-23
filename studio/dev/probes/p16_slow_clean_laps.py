"""P16 — are Sandown 3h's 61–90 s "clean" laps mis-segmented, or valid laps run slow? (backlog H10)

#358 re-based the ideal-lap sample table on the Desktop working set and found that 8 of the 17 laps
the app counts as clean on `Sandown 3h 2026` chapter 1 alone run 61–90 s, against a 47.4 s best.
Clean laps feed the ideal lap, every consistency σ, coaching and the published table, so the
question has two halves, answered in this order. Its verdict is `studio/docs/refused-2026-09.md`
§14.

  1. THE CENSUS. What is each slow clean lap? One real `Session.load` per load the app offers (the
     chapter alone, and the whole recording through `chapters.discover_siblings`), then the start
     line saved beside it, as `StudioWindow` opens it. Per lap: its distance against the racing
     laps' (a missed start-line crossing doubles it, an extra one halves it), the closure the
     classifier reads (`Session.lap_closure`), how far it strays from the best lap's line (the pit
     lane on this layout is ~17 m off it), its longest stationary run (the classifier's own
     `_signal._longest_stopped_s`) and how long it spends under 40 km/h (a yellow or a safety car
     is a SUSTAINED slow stretch; a pit stop or a driver change is a stop).
  2. THE SURFACES. How far does each pace-dependent number move without them? The session is
     loaded again with `studio.session._band_lap_ids` wrapped to drop the chosen laps, so every
     number downstream of the valid set is the app's own code: the ideal lap and its sample, the
     sample table's rates (`tests/test_ideal_sample_table`'s stated method), pace σ / CoV / trend,
     corner σ, coaching's per-corner time lost, the brake-point optimizer's demonstrated peak
     braking and the grip envelope. Two variants: without the laps slower than SLOW_X_BEST × the
     best lap (the laps #358 named), and without the laps a band CENTRED ON THE LOWER QUARTILE
     would drop, which is the fix for the median the slow laps drag up. That second set comes from
     a stand-in of `_signal._classify_laps`, asserted to reproduce the app's valid set at the
     median before the centre is varied.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p16_slow_clean_laps
    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p16_slow_clean_laps --surfaces
    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p16_slow_clean_laps --owner-tracks

`--owner-tracks` copies the owner's `tracks.json` into the jail, so the start line is the one the
owner's own app places. Without it, a fresh library's (the loader's), which is what the published
table was measured on.

Everything is on the TELEMETRY clock (the lap columns). Nothing crosses to the media clock or reads
an inertial stream except the g-derived surfaces, which read them through Session.

SAFETY, enforced rather than promised (as p10 and p15 enforce it):
  * EVERY FOOTAGE FOLDER IS READ-ONLY. Paths under ~/Desktop are only opened for reading, and every
    file in each folder is snapshotted (size, mtime) before and after each load.
  * `studio.dev._jail` diverts every app-support seam BEFORE any studio import that could reach one.
  * The real app-support directory is snapshotted before and after the run and must come back
    unchanged. This probe writes no file of its own.
"""

from __future__ import annotations

import math
import os
import shutil
import sys

import numpy as np

DESKTOP = os.path.expanduser("~/Desktop")
_REAL_APP_SUPPORT = os.path.join(os.path.expanduser("~"), "Library", "Application Support",
                                 "pacer")
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
# (name, folder, the chapter files; None = the first chapter's discovered siblings). Every Sandown
# recording is clockwise; MK is the anticlockwise control (common brief §1).
LOADS = [
    ("Sandown 3h ch1", "Sandown 3h 2026", ["GX010064.MP4"]),
    ("Sandown 3h ch2", "Sandown 3h 2026", ["GX020064.MP4"]),
    ("Sandown 3h ch3", "Sandown 3h 2026", ["GX030064.MP4"]),
    ("Sandown 3h", "Sandown 3h 2026", None),
    ("SD_19_09", "SD_19_09_26", None),
    ("SD_30_08", "SD_30_08_26", None),
    ("MK_18_09", "MK_18_09_26", None),
]
FIRST = {"Sandown 3h 2026": "GX010064.MP4", "SD_19_09_26": "GX010068.MP4",
         "SD_30_08_26": "GX010065.MP4", "MK_18_09_26": "GX010067.MP4"}
# The two loads the surfaces are measured on: the one #358 named, and the whole recording.
SURFACE_LOADS = ("Sandown 3h ch1", "Sandown 3h")
# A census bound, not a proposed rule: "slow" here is 1.3x the recording's best lap, which is
# exactly the 61–90 s laps #358 named on both Sandown 3h loads (1.30–1.89x on chapter 1).
SLOW_X_BEST = 1.3
SLOW_KMH = 40.0        # km/h; time under it is the sustained-slow signature of a yellow / safety car
OFF_LINE_M = 10.0      # m from the best lap's trace; the pit lane runs ~17 m off it here


def _folder_state(path: str) -> dict:
    if not os.path.isdir(path):
        return {}
    return {n: (os.stat(os.path.join(path, n)).st_size, os.stat(os.path.join(path, n)).st_mtime_ns)
            for n in sorted(os.listdir(path)) if not n.startswith(".")}


def _point_to_polyline(px, py, lx, ly) -> np.ndarray:
    """Distance (m) from each point (px, py) to the polyline (lx, ly), in chunks."""
    ax, ay, bx, by = lx[:-1], ly[:-1], lx[1:], ly[1:]
    dx, dy = bx - ax, by - ay
    seg2 = np.maximum(dx * dx + dy * dy, 1e-12)
    out = np.empty(len(px))
    for k in range(0, len(px), 256):
        qx, qy = px[k:k + 256, None], py[k:k + 256, None]
        t = np.clip(((qx - ax) * dx + (qy - ay) * dy) / seg2, 0.0, 1.0)
        out[k:k + 256] = np.min(np.hypot(qx - (ax + t * dx), qy - (ay + t * dy)), axis=1)
    return out


def _time_where(t, mask_mid) -> float:
    return float(np.diff(t)[mask_mid].sum())


def _load(paths: list[str], drop: set[int] | None = None):
    """`Session.load` + the saved start line, as the app opens it; `drop` wraps the valid set."""
    import studio.session as S

    real = S._band_lap_ids
    if drop:
        S._band_lap_ids = lambda laps: [i for i in real(laps) if i not in drop]
    try:
        s = S.Session.load(paths)
        s.restore_saved_timing_lines()
        s.valid_lap_ids()
    finally:
        S._band_lap_ids = real
    return s


def census(s) -> list[dict]:
    """Part 1: the per-lap evidence, for every substantial lap."""
    from studio import _signal

    valid, reasons = set(s.valid_lap_ids()), s.excluded_lap_reasons()
    clean = set(s.corners._clean_lap_ids())
    _t, bx, by, _v, _c = s._lap_columns(s.best_lap_id())
    rows = []
    for i in range(s.lap_count()):
        if s.laps.sample_count(i) < _signal.MIN_LAP_SAMPLES or s.lap_time(i) < _signal.MIN_LAP_TIME:
            continue
        t, xs, ys, v, _c = s._lap_columns(i)
        kmh = np.asarray(v) * 3.6
        off = _point_to_polyline(np.asarray(xs), np.asarray(ys), np.asarray(bx), np.asarray(by))
        gap, turn = s.lap_closure(i)
        rows.append({
            "id": i, "time": float(s.lap_time(i)), "dist": float(s.laps.get_lap_distance(i)),
            "valid": i in valid, "clean": i in clean, "reason": reasons.get(i),
            "gap": gap, "turn": turn, "off_max": float(off.max()),
            "off_s": _time_where(t, 0.5 * (off[1:] + off[:-1]) > OFF_LINE_M),
            "stop_s": _signal._longest_stopped_s(t, v),
            "slow_s": _time_where(t, 0.5 * (kmh[1:] + kmh[:-1]) < SLOW_KMH),
            "vmin": float(kmh.min()), "t0": float(t[0]),
        })
    return rows


def _band_at(rows: list[dict], centre) -> list[int]:
    """A stand-in of `_signal._classify_laps` over the census rows, with the time band's centre
    swapped. The caller asserts it reproduces the app at `np.median` before varying it."""
    from studio import _signal as g

    closed = [r for r in rows if not (r["gap"] > g.MAX_LAP_GAP_M or r["turn"] > g.MAX_LAP_TURN_DEG)]
    c = centre(np.array([r["time"] for r in closed]))
    timed = [r for r in closed if g.LAP_BAND_LO * c <= r["time"] <= g.LAP_BAND_HI * c]
    md = float(np.median([r["dist"] for r in timed]))
    banded = [r for r in timed if g.LAP_DIST_BAND_LO * md <= r["dist"] <= g.LAP_DIST_BAND_HI * md]
    return [r["id"] for r in banded if r["stop_s"] < g.MAX_STOPPED_S]


def report_census(name: str, s, rows: list[dict]) -> tuple[set[int], set[int]]:
    """Print the census; return (the slow clean laps, the laps a lower-quartile centre drops)."""
    from studio import _signal as g

    clean = [r for r in rows if r["clean"]]
    best = min(r["time"] for r in clean)
    fast = [r for r in clean if r["time"] <= SLOW_X_BEST * best]
    slow = [r for r in clean if r["time"] > SLOW_X_BEST * best]
    closed = [r["time"] for r in rows
              if not (r["gap"] > g.MAX_LAP_GAP_M or r["turn"] > g.MAX_LAP_TURN_DEG)]
    med = float(np.median(closed))
    app = sorted(r["id"] for r in rows if r["valid"])
    assert _band_at(rows, np.median) == app, "the stand-in no longer reproduces _classify_laps"
    q25 = _band_at(rows, lambda a: float(np.percentile(a, 25)))
    ratios = sorted(r["time"] / best for r in clean)
    print(f"\n=== {name}: {len(rows)} substantial laps, {len(clean)} clean, best {best:.3f} s; "
          f"time band centre (median) {med:.2f} s -> upper edge {1.6 * med:.1f} s")
    print(f"    clean laps / best, slowest six: {' '.join(f'{x:.3f}' for x in ratios[-6:])}")
    print("    lap     time    dist  x best  gap m turn  off-line m (s>10m) stop s  <40km/h s  vmin"
          "  verdict")
    for r in rows:
        if r["clean"] and r["time"] <= SLOW_X_BEST * best:
            continue
        verdict = "CLEAN" if r["clean"] else f"out: {r['reason']}"
        print(f"    {r['id']:3d} {r['time']:8.2f} {r['dist']:7.1f} {r['time'] / best:6.2f} "
              f"{r['gap']:6.1f} {r['turn']:5.1f} {r['off_max']:7.1f} ({r['off_s']:5.1f})"
              f" {r['stop_s']:6.1f} {r['slow_s']:9.1f} {r['vmin']:6.1f}  {verdict}")
    if slow:
        def rng(key, rs):
            return f"{min(r[key] for r in rs):.1f}–{max(r[key] for r in rs):.1f}"
        print(f"    {len(slow)} clean laps > {SLOW_X_BEST}x best: distance {rng('dist', slow)} m "
              f"(the other clean laps {rng('dist', fast)}), closure gap {rng('gap', slow)} m, "
              f"off-line max {rng('off_max', slow)} m (others {rng('off_max', fast)}), "
              f"stationary {rng('stop_s', slow)} s, under {SLOW_KMH:.0f} km/h {rng('slow_s', slow)} s "
              f"(others {rng('slow_s', fast)})")
    else:
        print(f"    no clean lap is slower than {SLOW_X_BEST}x the best")
    gone = sorted(set(app) - set(q25))
    print(f"    a band centred on the lower quartile ({np.percentile(closed, 25):.2f} s) would drop "
          f"{gone or 'nothing'} and admit {sorted(set(q25) - set(app)) or 'nothing'}")
    # The app's other session-relative outlier rule, the Stats sparkline's Tukey fence, off the
    # real static method: which clean laps it already frames out as "slower than the rest".
    from studio.stats_panel import StatsView
    _ceiling, over = StatsView._spark_frame([r["time"] for r in clean])
    print(f"    the sparkline's fence frames out {len(over)} clean lap(s)"
          + (f": {', '.join(f'{t:.2f}' for t in sorted(over))} s" if over else ""))
    return {r["id"] for r in slow}, set(gone)


def donations(s, slow: set[int]) -> None:
    """What the slow laps give the ideal ON THE PARTITION THE APP CUT — the segments they win, by
    how much against the best other clean lap, and how each one's segment times compare with the
    other clean laps' median (≈1.0 is race pace; the stretch it was neutralised is well above)."""
    sb = s.ideal_segment_bests()
    times = np.asarray(sb.times, float)
    may = np.asarray(sb.admitted, bool) & np.asarray(sb.resolved, bool)
    rows = {lid: k for k, lid in enumerate(sb.lap_ids)}
    others = [k for lid, k in rows.items() if lid not in slow]
    for j, (lab, d) in enumerate(zip(sb.labels, sb.donors, strict=True)):
        if d in slow:
            rest = np.where(may[others, j], times[others, j], np.inf).min()
            print(f"    lap {d} wins {lab}: {sb.bests[j]:.3f} s against {rest:.3f} s for the best "
                  f"other clean lap ({sb.bests[j] - rest:+.3f} s to the ideal)")
    med = np.nanmedian(np.where(may[others], times[others], np.nan), axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        for lid in sorted(slow):
            ratio = times[rows[lid]] / med
            print(f"    lap {lid:3d} segment / others' median: " + " ".join(
                "   —" if not np.isfinite(x) else f"{x:4.2f}" for x in ratio))
    print(f"    segments: {' '.join(sb.labels)}")


def surfaces(s) -> dict:
    """Part 2: every pace-dependent number, off the real Session."""
    sys.path.insert(0, os.path.join(_REPO, "tests"))
    import test_ideal_sample_table as table

    out: dict = {"clean": len(s.consistency_lap_ids()), "ideal": s.ideal_total()}
    smp = s.ideal_sample()
    out["sample"] = f"{smp.donors} donors / {smp.laps} laps / {smp.corners} corners" if smp else None
    sb = s.ideal_segment_bests()
    times = np.asarray(sb.times, float)
    admitted = np.asarray(sb.admitted, bool)
    may = admitted & np.asarray(sb.resolved, bool)
    lt = np.array([s.lap_time(i) for i in sb.lap_ids], float)
    n = len(lt)
    assert abs(table._full_set_ideal(times, may, admitted) - out["ideal"]) < 1e-9
    five = table._subset_means(times, may, admitted, lt, 5)
    out["rate"] = (five[0] - out["ideal"]) / math.log2(n / 5)
    out["best_rate"] = (five[1] - float(lt.min())) / math.log2(n / 5)
    out["donors"] = {lab: (d, float(s.lap_time(d)), b) for lab, d, b
                     in zip(sb.labels, sb.donors, sb.bests, strict=True) if d is not None}
    p = s.stats.pace()
    out.update(median=p.median, sigma=p.sigma, cov=s.stats.pace_cov(), trend=s.stats.pace_trend(),
               stints=s.stats.stint_count(), a_max=s.driving._a_max(), env=s.stats.gg_envelope())
    out["corner_sigma"] = {c.cid: c.sigma for c in s.corner_consistency()}
    op = s.coaching_opportunities()
    out["lost"] = {r.cid: r.time_lost for r in op.rows}
    out["ranked"] = len(op.ranked_rows())
    out["coach_median_lap"] = op.median_lap_id
    return out


def report_surfaces(name: str, variants: list[tuple[str, dict]]) -> None:
    base = variants[0][1]
    print(f"\n--- {name}: pace-dependent surfaces")
    keys = [("clean", "clean laps", "{:.0f}"), ("ideal", "ideal lap s", "{:.3f}"),
            ("rate", "ideal per doubling s", "{:.3f}"), ("best_rate", "best per doubling s", "{:.3f}"),
            ("median", "median lap s", "{:.3f}"), ("sigma", "σ lap s", "{:.2f}"),
            ("cov", "CoV %", "{:.1f}"), ("trend", "trend s/lap", "{:+.3f}"),
            ("stints", "runs", "{:.0f}"), ("a_max", "a_max g", "{:.3f}"), ("env", "grip ring g", "{:.3f}"),
            ("ranked", "coaching ranked rows", "{:.0f}")]
    print("    " + f"{'':24s}" + "".join(f"{v[0]:>22s}" for v in variants))
    for k, label, fmt in keys:
        print("    " + f"{label:24s}" + "".join(f"{fmt.format(v[1][k]):>22s}" for v in variants))
    print("    " + f"{'ideal sample':24s}" + "".join(f"{v[1]['sample']:>34s}" for v in variants))
    for label, v in variants[1:]:
        cs = [(c, base["corner_sigma"][c], v["corner_sigma"].get(c)) for c in base["corner_sigma"]]
        ls = [(c, base["lost"][c], v["lost"].get(c)) for c in base["lost"]]
        print(f"    [{label}] corner σ s: " + ", ".join(
            f"C{c} {a:.2f}->{'—' if b is None else f'{b:.2f}'}" for c, a, b in cs))
        print(f"    [{label}] coaching time lost s: " + ", ".join(
            f"C{c} {a:.3f}->{'—' if b is None else f'{b:.3f}'}" for c, a, b in ls))
        moved = {lab: d for lab, d in base["donors"].items() if v["donors"].get(lab, (None,))[0] != d[0]}
        print(f"    [{label}] segments whose donor changes: " + (", ".join(
            f"{lab} lap {d[0]} ({d[1]:.2f} s lap) {d[2]:.3f} s -> lap {v['donors'][lab][0]} "
            f"{v['donors'][lab][2]:.3f} s" for lab, d in moved.items() if lab in v["donors"]) or "none"))


def main() -> None:
    from studio.dev import _jail

    real_before = _folder_state(_REAL_APP_SUPPORT)
    jail = _jail.divert_app_support("pacer-p16-")
    print(f"app-support seams diverted to {jail.dir}")
    if "--owner-tracks" in sys.argv:
        shutil.copyfile(os.path.join(_REAL_APP_SUPPORT, "tracks.json"),
                        os.path.join(jail.dir, "tracks.json"))
        print("owner's tracks.json copied INTO the jail (only read on the real side)")
    from studio import chapters

    for name, folder, files in LOADS:
        root = os.path.join(DESKTOP, folder)
        if not os.path.isfile(os.path.join(root, FIRST[folder])):
            print(f"\n=== {name}: SKIPPED — {root} is not present")
            continue
        paths = ([os.path.join(root, f) for f in files] if files
                 else chapters.discover_siblings(os.path.join(root, FIRST[folder])))
        before = _folder_state(root)
        s = _load(paths)
        slow, q25_drop = report_census(name, s, census(s))
        if slow:
            donations(s, slow)
        if "--surfaces" in sys.argv and name in SURFACE_LOADS:
            variants = [("as the app counts", surfaces(s))]
            variants.append((f"without {len(slow)} slow", surfaces(_load(paths, slow))))
            if q25_drop:
                variants.append((f"q25 centre (-{len(q25_drop)})", surfaces(_load(paths, q25_drop))))
            report_surfaces(name, variants)
        if _folder_state(root) != before:
            raise SystemExit(f"ABORT: {root} changed during the load")

    if _folder_state(_REAL_APP_SUPPORT) != real_before:
        raise SystemExit("ABORT: the real app-support directory changed during the run")
    print("\nreal app-support directory unchanged; every footage folder unchanged")


if __name__ == "__main__":
    sys.exit(main())
