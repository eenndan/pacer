"""Side-by-side: the NOTEBOOK's own lap-timing pipeline vs our shipping GPS9 timing,
both against the SAME transponder CSV, with the lap SEGMENTATION held constant so the
ONLY variable is the per-sample TIME AXIS.

WHY THIS SCRIPT EXISTS
----------------------
The prior investigation (studio/docs/upstream-20ms-investigation.md) tested our C++ port
`pacer.interpolate_timestamps` (via Session.load(interpolate=True)) against GPS9. It did NOT
run the *notebook's own PyTorch code*. This script does: the t1 (free) and t2 (parametric)
Adam optimizers below are copied VERBATIM from notebooks/interpolation.ipynb (cells
`4c1dba4b` and `31c96b74`), down to the loss, the [1e-1,1e-2,1e-3] LR schedule, the fresh
optimizer per LR, and the di clamp. So the "notebook" lap times here are produced by the
notebook's literal algorithm, not a paraphrase.

APPLES-TO-APPLES
----------------
We do NOT have the author's GH010251 footage, so we can't reproduce the author's 6-lap
numbers. We run the notebook's METHOD on OUR recordings (0060, 0062 --full), which is the
only place transponder ground truth exists. To make the comparison fair we:

  * load ONE common cleaned sample set (the shipping Session pipeline: quality gate + _clean),
  * build ONE common geometric start/finish line,
  * add those SAME samples to THREE pacer.Laps objects whose ONLY difference is the per-point
    TIME (notebook-t1 / notebook-t2 / GPS9), and assert the per-lap sample MEMBERSHIP is
    identical across all three (it is, because pacer segments by GEOMETRY — crossing the start
    line — not by time). So lap_time(i), a difference of two crossing instants, is the only
    thing that moves. That isolates the time axis exactly as the brief asks.

For honesty we ALSO report the notebook's NATIVE numbers: its own preprocessing (raw
full_speed>3, no gate/clean/smooth) + its own pick_random_start() — i.e. what the notebook
prints if you just swap the file. Those are NOT aligned to the transponder (different
segmentation), they only show the notebook runs end to end on our data.

The transponder alignment reuses studio._validate_wallclock's helpers verbatim.

Run:
  PYTHONPATH=. pixi run python studio/docs/gps_research_scripts/notebook_vs_gps9.py <REC.MP4> <CSV> \
      --race-start "2026-05-23 12:00:00Z" [--dump out.json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

import numpy as np
import torch

import pacer
from studio import chapters, tracks, transponder
from studio._validate_wallclock import (
    RACING_MAX_S,
    _parse_when,
    best_offset,
    cumulative_completion,
    footage_gps9_window,
    lap_being_driven,
    residual_stats,
)
from studio.session import (
    SMOOTH_WINDOW,
    _clean,
    _fit_start_line,
    _gate_quality,
    _gps9_times,
    _read_gpmf,
    _smooth_track,
)

DROPOUT_GAP_S = 0.35


# ===========================================================================================
#  THE NOTEBOOK'S OWN INTERPOLATION (copied verbatim from notebooks/interpolation.ipynb)
# ===========================================================================================
def notebook_build_di_floor_ceil(samples, spans, cs):
    """Notebook cells 6a0ff025 / 21ef2ee9 / 016d83d7, verbatim algorithm.

    rough_frequency = #samples / #distinct payload spans;
    di[i] = round( distance(s_{i-1}, s_i) / avg_speed * rough_frequency ), di[0]=1, clamped>=1;
    floor/ceil = the payload [in,out] span of each sample.
    """
    rough_frequency = len(samples) / len(set(spans))
    floor = torch.Tensor([b for (b, _) in spans])
    ceil = torch.Tensor([e for (_, e) in spans])
    di = np.round(
        np.array(
            [
                cs.distance(a, b)
                / (0.5 * a.full_speed + 0.5 * b.full_speed)
                * rough_frequency
                for a, b in zip(samples[:-1], samples[1:], strict=False)
            ]
        )
    )
    # notebook clamp: "clamp zero-distance steps so the loss never divides by zero"
    di = torch.Tensor(np.concatenate([[1], np.maximum(di, 1)]))
    assert ceil.shape == floor.shape == di.shape
    return di, floor, ceil, rough_frequency


def _notebook_loss(x, di, floor, ceil):
    """Notebook cell 016d83d7 loss(x), verbatim."""
    my_diffs = x[1:] - x[:-1]
    my_diffs = my_diffs / di[1:]
    spacing = ((my_diffs - my_diffs.mean()) ** 2).mean()
    constraints = (((floor - x).clip(min=0) + (x - ceil).clip(min=0)) ** 2).mean()
    return spacing + constraints


def notebook_t1(di, floor, ceil):
    """Notebook cell 4c1dba4b VERBATIM: free per-sample Adam (every t[i] is a parameter)."""
    t1 = (floor + ceil) / 2
    t1 = t1.clone().detach().requires_grad_(True)
    for lr in [1e-1, 1e-2, 1e-3]:
        optimizer = torch.optim.Adam([t1], lr=lr)
        for _ in range(100):
            optimizer.zero_grad()
            loss_value = _notebook_loss(t1, di, floor, ceil)
            loss_value.backward()
            optimizer.step()
    return t1.detach().numpy()


def notebook_t2(di, floor, ceil, rough_frequency):
    """Notebook cell 31c96b74 VERBATIM: parametric {phase, frequency} Adam fit.
    t2 = phase + 1/frequency * (cumsum(di)-1)."""
    phase = torch.tensor(float(floor[0]), requires_grad=True)
    frequency = torch.tensor(float(rough_frequency), requires_grad=True)
    di_cum = di.long().cumsum(0).float() - 1
    t2 = phase + 1 / frequency * di_cum
    for lr in [1e-1, 1e-2, 1e-3]:
        optimizer = torch.optim.Adam([phase, frequency], lr=lr)
        for _ in range(100):
            optimizer.zero_grad()
            loss_value = _notebook_loss(t2, di, floor, ceil)
            loss_value.backward(retain_graph=True)
            optimizer.step()
            t2 = phase + 1 / frequency * di_cum
    return t2.detach().numpy(), float(phase), float(frequency)


# ===========================================================================================
#  Lap segmentation held constant; only the time axis varies
# ===========================================================================================
def _segment(samples, times, cs, start_line):
    """Build a pacer.Laps from the SAME samples + SAME start line, with the given per-point
    time axis. Returns the Laps object."""
    laps = pacer.Laps()
    laps.set_coordinate_system(cs)
    for s, t in zip(samples, times, strict=True):
        laps.add_point(s, float(t))
    laps.sectors = pacer.Sectors(start_line=start_line, sector_lines=[])
    laps.update()
    return laps


def _lap_membership(laps):
    """A fingerprint of which samples landed in each lap: per-lap (n_points, start_ts).
    If this is identical across two time axes, the segmentation is the same and only the
    lap TIME differs."""
    return [
        (laps.sample_count(i), round(laps.start_timestamp(i), 6))
        for i in range(laps.laps_count())
    ]


def _valid_band(laps):
    """Real laps = within [0.5,1.6]x median, >=20 samples, >=5 s — the shipping band rule."""
    basic = [
        (i, laps.lap_time(i))
        for i in range(laps.laps_count())
        if laps.sample_count(i) >= 20 and laps.lap_time(i) >= 5.0
    ]
    if not basic:
        return []
    med = float(np.median([t for _, t in basic]))
    return [i for i, t in basic if 0.5 * med <= t <= 1.6 * med]


def _has_dropout_times(laps, lid):
    lap = laps.get_lap(lid)
    ts = np.array([p.time for p in lap.points])
    return bool(len(ts) > 1 and np.diff(ts).max() > DROPOUT_GAP_S)


# ===========================================================================================
#  Transponder alignment (reused verbatim from _validate_wallclock)
# ===========================================================================================
def align(app, valid, laps_csv, completion, first_utc, race_start):
    elapsed_start = (first_utc - race_start).total_seconds()
    drv_start = lap_being_driven(completion, elapsed_start)
    lo = max(min(laps_csv), drv_start - 12)
    hi = drv_start + 12
    start, corr, offsets = best_offset(app, laps_csv, lo, hi)
    if start is None:
        return None
    csv_ids = [start + k for k in range(len(valid))]
    csv_t = np.array([laps_csv[i] for i in csv_ids])
    return csv_ids, csv_t, corr


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("recording")
    ap.add_argument("csv")
    ap.add_argument("--race-start", required=True)
    ap.add_argument("--dump", default=None)
    args = ap.parse_args([a for a in argv if a != "--"])
    race_start = _parse_when(args.race_start)

    paths = chapters.discover_siblings(args.recording)
    label = chapters.recording_label(paths)
    print(f"recording: {label} ({len(paths)} chapter(s))")

    laps_csv = transponder.parse_csv(args.csv)
    completion = cumulative_completion(laps_csv)
    first_ms, last_ms, _ = footage_gps9_window(paths)
    first_utc = dt.datetime.fromtimestamp(first_ms / 1000.0, dt.UTC)

    # ----- common cleaned sample set + naive/gps9 time axes (SHIPPING preprocessing) -----
    # Read the media ONCE; keep the raw full_speed>3 set for the notebook-native run too, so we
    # never re-open the files (a second nanobind load of the same source segfaults at teardown).
    raw_samples, raw_spans, raw_naive, _dur = _read_gpmf(paths)
    native_raw = [(s, sp) for s, sp in zip(raw_samples, raw_spans, strict=True) if s.full_speed > 3.0]
    samples, spans, naive = _gate_quality(raw_samples, raw_spans, raw_naive)
    samples, spans, naive = _clean(samples, spans, naive)
    gps9 = _gps9_times(samples, spans, naive)

    # Smooth the GPS positions exactly as Session.load does, so the SEGMENTATION here is the
    # shipping one — same smoothed track => same lap boundaries the app produces. The notebook
    # interpolation below is then fitted on these SAME (smoothed) samples + payload spans, so
    # the ONLY thing that differs between the three axes is the per-sample TIME.
    samples = _smooth_track(samples, gps9, SMOOTH_WINDOW)
    print(f"common cleaned+smoothed samples: n={len(samples)}")

    # Coordinate system centred on the track + the SHIPPING track-aware start/finish line
    # (tracks.detect_track -> fixed S/F line, widened only to recover missed passes), the same
    # line Session.load picks. Used for ALL THREE time axes so the boundaries are identical.
    laps0 = pacer.Laps()
    for s, t in zip(samples, gps9, strict=True):
        laps0.add_point(s, float(t))
    mn, mx = laps0.min_max()
    clat, clon = (mn.y + mx.y) / 2, (mn.x + mx.x) / 2
    cs = pacer.CoordinateSystem(pacer.GPSSample(lat=clat, lon=clon, altitude=0))
    laps0.set_coordinate_system(cs)
    track = tracks.detect_track(clat, clon)
    if track is not None:
        base = tracks.start_line_segment(track, cs)
        _fit_start_line(laps0, base)  # sets laps0.sectors on the chosen (possibly widened) line
        start_line = laps0.sectors.start_line
        print(f"start line: shipping track-aware ({track.name if hasattr(track,'name') else track})")
    else:
        start_line = laps0.pick_random_start()
        print("start line: pick_random_start (no known track)")

    # ----- the notebook's OWN interpolation on the common (smoothed) samples -----
    di, floor, ceil, rough_freq = notebook_build_di_floor_ceil(samples, spans, cs)
    t1 = notebook_t1(di, floor, ceil)
    t2, p2, f2 = notebook_t2(di, floor, ceil, rough_freq)
    print(f"notebook t2 fit: phase={p2:.4f} frequency={f2:.5f} Hz (rough={rough_freq:.4f})")

    # ----- three Laps, SAME samples + SAME start line, differing ONLY in the time axis -----
    L_t1 = _segment(samples, t1, cs, start_line)
    L_t2 = _segment(samples, t2, cs, start_line)
    L_g9 = _segment(samples, gps9, cs, start_line)

    # Verify the segmentation is held constant (sample membership identical).
    m_t1, m_t2, m_g9 = _lap_membership(L_t1), _lap_membership(L_t2), _lap_membership(L_g9)
    counts_t1 = [c for c, _ in m_t1]
    counts_t2 = [c for c, _ in m_t2]
    counts_g9 = [c for c, _ in m_g9]
    same_seg = counts_t1 == counts_t2 == counts_g9
    print(f"segmentation held constant (identical per-lap sample counts): {same_seg}")
    if not same_seg:
        print(f"  counts t1={counts_t1}\n  counts t2={counts_t2}\n  counts g9={counts_g9}")

    # Valid laps from the GPS9 axis (the reference); use the SAME lap ids for all three so the
    # boundaries are identical. (If counts match, the same lap id is the same physical lap.)
    valid = _valid_band(L_g9)
    app_g9 = np.array([L_g9.lap_time(i) for i in valid])
    app_t1 = np.array([L_t1.lap_time(i) for i in valid])
    app_t2 = np.array([L_t2.lap_time(i) for i in valid])

    # Align the GPS9 axis to the transponder (the lock); reuse the SAME csv ids for all axes.
    al = align(app_g9, valid, laps_csv, completion, first_utc, race_start)
    if al is None:
        print("could not lock transponder alignment")
        return 1
    csv_ids, csv_t, corr = al
    print(f"transponder lock: app valid laps <-> CSV {csv_ids[0]}..{csv_ids[-1]} corr={corr:.4f}")

    dropout = np.array([_has_dropout_times(L_g9, i) for i in valid])
    racing = (app_g9 <= RACING_MAX_S) & (csv_t <= RACING_MAX_S)
    clean = racing & ~dropout

    def stats(app):
        r = app - csv_t
        return {
            "all": residual_stats(r),
            "racing": residual_stats(r[racing]),
            "clean": residual_stats(r[clean]),
        }

    s_t1, s_t2, s_g9 = stats(app_t1), stats(app_t2), stats(app_g9)

    def show(name, s):
        c = s["clean"]
        print(f"  {name:14s} CLEAN n={c['n']} mean={c['mean']:+.4f} median={c['median']:+.4f} "
              f"std={c['std']:.4f} rms={c['rms']:.4f}")

    print("\nResiduals vs transponder (lap_time - csv), SAME segmentation:")
    show("notebook t1", s_t1)
    show("notebook t2", s_t2)
    show("our GPS9", s_g9)

    # ----- the explicit per-lap table -----
    print(f"\n{'lap':>3} {'csv_lap':>7} {'transp':>8} {'nb_t1':>8} {'nb_t2':>8} {'gps9':>8} "
          f"{'t1-tr':>8} {'t2-tr':>8} {'g9-tr':>8} {'flag':>8}")
    rows = []
    for k, lid in enumerate(valid):
        flag = "clean" if clean[k] else ("dropout" if dropout[k] else "pit/slow")
        print(f"{lid:>3} {csv_ids[k]:>7} {csv_t[k]:>8.3f} {app_t1[k]:>8.3f} {app_t2[k]:>8.3f} "
              f"{app_g9[k]:>8.3f} {app_t1[k]-csv_t[k]:>+8.3f} {app_t2[k]-csv_t[k]:>+8.3f} "
              f"{app_g9[k]-csv_t[k]:>+8.3f} {flag:>8}")
        rows.append({
            "lap_id": int(lid), "csv_lap": csv_ids[k], "transponder": float(csv_t[k]),
            "notebook_t1": float(app_t1[k]), "notebook_t2": float(app_t2[k]),
            "gps9": float(app_g9[k]),
            "t1_minus_transp": float(app_t1[k] - csv_t[k]),
            "t2_minus_transp": float(app_t2[k] - csv_t[k]),
            "gps9_minus_transp": float(app_g9[k] - csv_t[k]),
            "clean": bool(clean[k]), "dropout": bool(dropout[k]),
        })

    # ----- the notebook's NATIVE run (its own preprocessing + its own pick_random_start) -----
    native = notebook_native(native_raw)

    result = {
        "recording": label,
        "common_n_samples": len(samples),
        "segmentation_held_constant": bool(same_seg),
        "per_lap_sample_counts": counts_g9,
        "transponder_csv_range": [csv_ids[0], csv_ids[-1]],
        "duration_corr": float(corr),
        "notebook_t2_fit": {"phase": p2, "frequency": f2, "rough_frequency": float(rough_freq)},
        "stats": {"notebook_t1": s_t1, "notebook_t2": s_t2, "gps9": s_g9},
        "per_lap": rows,
        "notebook_native": native,
    }
    if args.dump:
        with open(args.dump, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nwrote {args.dump}")
    return 0


def notebook_native(native_raw):
    """Run the notebook's pipeline UNMODIFIED in spirit: its own preprocessing (raw
    full_speed>3, no quality gate / _clean / smoothing), its own pick_random_start(), and
    print the resulting t1 & t2 lap times — exactly what the notebook outputs if you swap in
    our file. Not transponder-aligned (different segmentation); shows the notebook runs end
    to end on our data and what its raw lap list looks like.

    `native_raw` is the already-loaded list of (sample, span) with full_speed>3 — the
    notebook's cell 02045983 filter applied to the SAME single media read as the main run, so
    we never re-open the files (a second nanobind load of the same source segfaults)."""
    samples = [s for s, _ in native_raw]
    spans = [sp for _, sp in native_raw]
    cs = pacer.CoordinateSystem(samples[0])
    di, floor, ceil, rough = notebook_build_di_floor_ceil(samples, spans, cs)
    t1 = notebook_t1(di, floor, ceil)
    t2, p2, f2 = notebook_t2(di, floor, ceil, rough)

    def native_laps(times):
        laps = pacer.Laps()
        laps.set_coordinate_system(cs)
        for s, t in zip(samples, times, strict=True):
            laps.add_point(s, float(t))
        laps.sectors = pacer.Sectors(start_line=laps.pick_random_start(), sector_lines=[])
        laps.update()
        lt = np.array([laps.lap_time(i) for i in range(laps.laps_count())])
        med = np.median(lt[lt > 1])
        return [
            {"lap": i, "lap_time": round(float(laps.lap_time(i)), 3),
             "clean": bool(0.7 * med < laps.lap_time(i) < 1.3 * med)}
            for i in range(laps.laps_count())
        ]

    out = {"n_samples": len(samples), "rough_frequency": float(rough),
           "t2_phase": p2, "t2_frequency": f2,
           "t1_laps": native_laps(t1), "t2_laps": native_laps(t2)}
    print(f"\n[notebook NATIVE — its own preprocessing + pick_random_start, n={len(samples)}]")
    print("  t1 clean lap times:",
          [r["lap_time"] for r in out["t1_laps"] if r["clean"]])
    print("  t2 clean lap times:",
          [r["lap_time"] for r in out["t2_laps"] if r["clean"]])
    return out


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
