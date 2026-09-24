"""Out-of-sample validation of the GPS9 true-clock lap timing against a lap-timing transponder
CSV — and the wall-clock AUTO-DISCOVERY of which CSV laps a recording covers (so a validation can
be re-run for ANY recording without hand-entering a lap range).

This is the evidence that the default GPS9 wall-clock timing (rate = 1.0, no calibration) is
UNBIASED out of sample: on recording 0062 the clean-lap residual is mean +0.0015 s, ±0.053 s. It
also proved that an earlier transponder-fit clock-rate multiplier was a 0060-specific overfit to
GPS-dropout-tail skew (both recordings' true rate is ≈1.0), so that factor was removed; the
optional `--rate` arg (default 1.0 = the shipping behaviour) lets you re-probe any explicit rate to
reproduce that finding. The CSV is a reference INPUT only — never committed.

The matching CSV lap window is reconstructed from FOUR independent signals, which must agree:

  1. ELAPSED-TIME — the CSV is a continuous lap log, so the absolute completion time of CSV lap
     k is `race_start + cumsum(lap_durations[..k])` (INCLUDING the long pit/driver-change laps,
     which are real elapsed time). The footage's first GPS9 wall-clock fix gives its absolute
     start; `race_start + elapsed == that` pins the lap being driven when filming began.
  2. PIT BRACKETS — a pit/driver-change lap is long (>~120 s). A stint is the run of normal
     racing laps bracketed by two long laps; the footage starts right after one and ends at one.
  3. GPS9 UTC — the GPS9 stream carries the true GPS wall-clock; the C++ core already folds
     `days-since-2000 + secs-since-midnight` into `GPSSample.timestamp_ms` as **ms since the Unix
     epoch (UTC)**, so the absolute footage window is read directly (midnight rollover handled in
     the core). The local↔UTC offset is derived EMPIRICALLY from (GPS9 UTC start) vs the stated
     local start — never assumed (BST vs GMT).
  4. DURATION-CORRELATION (the LOCK) — the app's valid-lap DURATION sequence must correlate
     strongly with the CSV's at exactly one integer offset (≈0 at every other), pinning the
     alignment. This is offset-INVARIANT to any constant race-start-to-first-crossing gap (a
     rolling start / grid delay shifts signals 1+3 by a few laps but not the per-lap fingerprint),
     so it is the authoritative signal; 1–3 corroborate and bound it.

Run:
  pixi run python -m studio.dev._validate_wallclock -- <recording.MP4> <transponder.csv> \
      --race-start "2026-05-23 12:00:00Z" [--local-start "2026-05-24 06:54"] [--dump <path>]

`--race-start` is the absolute UTC of CSV lap 1's timing start (the green flag / first line
crossing); `--local-start` (optional) is the stated wall-clock the footage began, used only to
report the derived local↔UTC offset as a sanity check. The positional arguments are INPUTS, read
only; `--dump` is the one output.

LOCK-ONLY MODE (no `--race-start`). A sprint session has no 24-hour log to count elapsed time
through and no pit brackets, and its timing sheet holds every driver of every heat — so signal 4
runs alone, against all of them. The recording's laps are split into STINTS at every pit/stop lap
(≥ `PIT_LAP_S`); each stint's duration fingerprint is correlated with every contiguous window of
every driver row of every heat in the CSV (a Club Speed day, `studio/dev/clubspeed.py`, or a
transponder CSV as a single row), and the verdict is stated before any residual is:

  LOCKED     the best-r candidate is also the one whose residual σ is smallest, its r ≥
             `LOCK_MIN_R`, and every other candidate's residual σ is at least `LOCK_MIN_SEPARATION`×
             the lock's;
  AMBIGUOUS  anything else — and an ambiguous stint's residuals are not a measurement of anything.

The MARGIN (locked r − the best rival's r) is reported as ACCURACY.md reports it, but for a short
sprint fingerprint it is not the discriminator: Pearson r is blind to level AND scale, and every
driver in one race shares its slow opening lap and its yellow flags, so a rival row can correlate
well while missing every lap by seconds. The residual σ is what a rival would have to match, so
the separation is judged on it.

  pixi run python -m studio.dev._validate_wallclock -- <recording.MP4> <laps.csv> [--dump <path>]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

import numpy as np

from studio import chapters
from studio.dev import clubspeed, transponder
from studio.ingest import read_gpmf as _read_gpmf
from studio.session import Session

PIT_LAP_S = 120.0   # a lap at/above this is a pit / driver-change lap, not a racing lap
RACING_MAX_S = 72.0  # the "clean racing lap" cap used for residual stats
DROPOUT_GAP_S = 0.35  # an interior point-to-point time jump above this is a GPS dropout

# Lock-only mode's verdict (module docstring). Fixed BEFORE the first sprint was locked with them.
LOCK_MIN_LAPS = 5            # racing laps a stint needs before its fingerprint can lock at all
LOCK_MIN_R = 0.95            # a true pairing at σ ~0.05-0.09 s over ~1 s of lap spread reads ≥ 0.99
LOCK_MIN_SEPARATION = 3.0    # every rival's residual σ over the lock's


# --------------------------------------------------------------------------- pure helpers
def cumulative_completion(laps: dict[int, float]) -> dict[int, float]:
    """{lap -> cumulative elapsed seconds at the moment that lap COMPLETES}. The CSV is a
    continuous log, so this is exact even across the long pit laps (they are real elapsed time)."""
    out: dict[int, float] = {}
    cum = 0.0
    for i in sorted(laps):
        cum += laps[i]
        out[i] = cum
    return out


def lap_being_driven(completion: dict[int, float], elapsed_s: float) -> int:
    """The CSV lap in progress at `elapsed_s` after race start: the first lap whose COMPLETION
    is at/after that elapsed time. (Used by the elapsed-time + GPS9-UTC corroborators.)"""
    for i in sorted(completion):
        if completion[i] >= elapsed_s:
            return i
    return max(completion)


def pit_brackets(laps: dict[int, float], around: int, span: int = 30) -> tuple[int | None, int | None]:
    """The nearest long (pit/driver-change) laps just BEFORE and just AFTER lap `around` — the
    two ends of the stint the footage covers. Returns (before, after); either may be None."""
    ids = sorted(laps)
    before = next((i for i in reversed([j for j in ids if j <= around and laps[j] >= PIT_LAP_S])), None)
    after = next((i for i in [j for j in ids if j > around and laps[j] >= PIT_LAP_S]), None)
    # `span` keeps the search local; a 24 h log never has a pit gap that large between stints.
    if before is not None and around - before > span * 6:
        before = None
    return before, after


def best_offset(app_t: np.ndarray, laps: dict[int, float], lo: int, hi: int,
                racing_max: float = RACING_MAX_S):
    """The integer CSV start offset in [lo, hi) that maximizes the duration correlation between
    the app's valid-lap durations and a same-length contiguous CSV window. Returns
    (start, corr, all_offsets) where `all_offsets` is [(start, corr, n_racing)] for a uniqueness
    check. Only racing laps (<= racing_max in BOTH) enter the correlation; a window must be
    mostly racing laps to be considered."""
    n = len(app_t)
    ids = sorted(laps)
    last = ids[-1]
    results = []
    best = (-2.0, None)
    for start in range(lo, hi):
        seg_ids = list(range(start, start + n))
        if seg_ids[-1] > last:
            break
        seg = np.array([laps.get(i, np.nan) for i in seg_ids])
        if np.isnan(seg).any():
            continue
        m = (seg <= racing_max) & (app_t <= racing_max)
        if int(m.sum()) < max(5, int(0.7 * n)):
            continue
        corr = float(np.corrcoef(app_t[m], seg[m])[0, 1])
        results.append((start, corr, int(m.sum())))
        if corr > best[0]:
            best = (corr, start)
    return best[1], best[0], results


def residual_stats(r: np.ndarray) -> dict:
    return {
        "mean": float(r.mean()),
        "median": float(np.median(r)),
        "std": float(r.std(ddof=0)),
        "rms": float(np.sqrt(np.mean(r ** 2))),
        "n": int(len(r)),
    }


# --------------------------------------------------------------------------- lock-only mode
def stints(lap_times) -> list[list[int]]:
    """Lap ids split into STINTS: maximal runs of consecutive laps each timed above 0 and under
    `PIT_LAP_S`. A pit / stop / between-heats lap, or the open lap a recording ends on (0 s), ends
    one — the laps either side of it belong to different stretches of the timing sheet."""
    out: list[list[int]] = []
    cur: list[int] = []
    for i, t in enumerate(lap_times):
        if 0.0 < t < PIT_LAP_S:
            cur.append(i)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def lock_all(app_t: np.ndarray, rows: dict, racing_max: float = RACING_MAX_S) -> list[dict]:
    """Every alignment of one stint's lap durations (`app_t`, in order) against every contiguous
    window of every row: [{"row", "start", "r", "sd", "n"}], where `r` and `sd` (the residual
    σ) are over the stint's racing laps (≤ `racing_max`). Only the STINT's laps are masked, never
    the row's: a slow or pitting driver is still a rival, and a window that pairs a racing lap with
    a slow one simply scores badly. A window must lie wholly inside its row."""
    app_t = np.asarray(app_t, float)
    m = app_t <= racing_max
    out = []
    for key, laps in rows.items():
        for start in sorted(laps):
            window = [laps.get(start + k) for k in range(len(app_t))]
            if any(v is None for v in window):
                continue
            a, b = app_t[m], np.array(window, float)[m]
            if len(a) < 2 or np.std(b) == 0.0:
                continue
            out.append({"row": key, "start": start, "r": float(np.corrcoef(a, b)[0, 1]),
                        "sd": float(np.std(a - b)), "n": int(m.sum())})
    return out


def lock_verdict(results: list[dict]) -> dict:
    """The lock and its uniqueness over `lock_all`'s results (module docstring): the best-r
    candidate, the best rival's r (the MARGIN is the difference), the smallest residual σ of every
    other candidate (the SEPARATION is its ratio to the lock's), and `locked` per the fixed rule."""
    if not results:
        return {"locked": False, "why": "no candidate window fits this stint"}
    ranked = sorted(results, key=lambda c: c["r"], reverse=True)
    lock, rivals = ranked[0], ranked[1:]
    out = {"lock": lock, "rivals": len(rivals)}
    if not rivals:
        return {**out, "locked": False, "why": "a single candidate has no rival to be unique against"}
    by_r = rivals[0]
    by_sd = min(rivals, key=lambda c: c["sd"])
    separation = by_sd["sd"] / lock["sd"] if lock["sd"] > 0 else float("inf")
    out.update(margin=lock["r"] - by_r["r"], best_rival_r=by_r, best_rival_sd=by_sd,
               separation=separation)
    if by_sd["sd"] <= lock["sd"]:
        why = "another candidate leaves a smaller residual σ than the best-r one"
    elif lock["r"] < LOCK_MIN_R:
        why = f"the best r is {lock['r']:.3f}, below {LOCK_MIN_R}"
    elif separation < LOCK_MIN_SEPARATION:
        why = f"a rival's residual σ is only {separation:.1f}× the lock's (< {LOCK_MIN_SEPARATION}×)"
    else:
        why = ""
    return {**out, "locked": not why, "why": why}


def load_rows(csv_path: str) -> tuple[dict, dict]:
    """({row key: {lap: seconds}}, {heat: printed start}) from a Club Speed lap CSV
    (`studio/dev/clubspeed.py`; key = (heat, row)) or a transponder CSV (one row, key ("csv", 1))."""
    if clubspeed.is_clubspeed_csv(csv_path):
        return clubspeed.parse_csv(csv_path)
    return {("csv", 1): transponder.parse_csv(csv_path)}, {}


# --------------------------------------------------------------------------- footage window
def footage_gps9(paths: list[str]):
    """(first_utc_ms, last_utc_ms, total_duration_s, median_dop) from the GPS9 stream of a
    (chaptered) recording. `timestamp_ms` is already absolute UTC ms (the core folds
    days-since-2000 + secs-since-midnight, so midnight rollover is handled). Returns ms=None if no
    GPS9 timestamps (e.g. a GPS5-only clip). `median_dop` is over every raw fix that reports one —
    the recording-level "GPS quality" ACCURACY.md's rows quote — or None."""
    samples, spans, naive, durations = _read_gpmf(paths)
    ts = [getattr(s, "timestamp_ms", 0) for s in samples]
    have = [t for t in ts if t > 0]
    first = have[0] if have else None
    last = have[-1] if have else None
    dop = np.array([getattr(s, "dop", -1.0) for s in samples], float)
    dop = dop[np.isfinite(dop) & (dop > 0)]
    return first, last, float(sum(durations)), (float(np.median(dop)) if len(dop) else None)


def footage_gps9_window(paths: list[str]):
    """(first_utc_ms, last_utc_ms, total_duration_s) — `footage_gps9` without the DOP."""
    return footage_gps9(paths)[:3]


def _fmt_utc(ms: int) -> str:
    return (dt.datetime.fromtimestamp(ms / 1000.0, dt.UTC)
            .strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " UTC")


def _parse_when(text: str) -> dt.datetime:
    """Parse '2026-05-23 12:00:00Z' / '...+01:00' / a bare '2026-05-24 06:54' (treated UTC if
    no tz, but for --local-start the tz is irrelevant — only the wall-clock matters)."""
    t = text.strip().replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(t)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                d = dt.datetime.strptime(text.strip(), fmt)
                break
            except ValueError:
                continue
        else:
            raise
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.UTC)
    return d


# --------------------------------------------------------------------------------- main
def run(recording: str, csv_path: str, race_start: dt.datetime,
        local_start: dt.datetime | None = None, dump: str | None = None,
        rate: float = 1.0) -> dict:
    paths = chapters.discover_siblings(recording)
    print(f"recording: {chapters.recording_label(paths)} ({len(paths)} chapter(s))")

    laps = transponder.parse_csv(csv_path)
    completion = cumulative_completion(laps)

    # --- SIGNAL 3: GPS9 UTC window ---
    first_ms, last_ms, total_dur = footage_gps9_window(paths)
    if first_ms is None:
        print("no GPS9 timestamps in this recording — cannot validate wall-clock.")
        return {}
    first_utc = dt.datetime.fromtimestamp(first_ms / 1000.0, dt.UTC)
    last_utc = dt.datetime.fromtimestamp(last_ms / 1000.0, dt.UTC)
    print(f"GPS9 UTC start  = {_fmt_utc(first_ms)}")
    print(f"GPS9 UTC end    = {_fmt_utc(last_ms)}")
    print(f"footage duration= {total_dur:.1f} s = {total_dur / 3600:.4f} h  "
          f"(GPS9 wall-clock span {(last_ms - first_ms) / 1000:.1f} s)")
    if local_start is not None:
        # Empirical local<->UTC offset: same wall-clock instant, two labels.
        off_h = (local_start.replace(tzinfo=dt.UTC) - first_utc).total_seconds() / 3600.0
        print(f"derived local-UTC offset = {off_h:+.2f} h  "
              f"(stated local start {local_start.strftime('%H:%M')} vs GPS9 UTC "
              f"{first_utc.strftime('%H:%M')})")

    # --- SIGNAL 1: elapsed-time -> which CSV lap when filming began ---
    elapsed_start = (first_utc - race_start).total_seconds()
    elapsed_end = (last_utc - race_start).total_seconds()
    drv_start = lap_being_driven(completion, elapsed_start)
    drv_end = lap_being_driven(completion, elapsed_end)
    print(f"\n[elapsed-time] footage start = {elapsed_start / 3600:.4f} h after race start "
          f"-> CSV lap ~{drv_start};  end = {elapsed_end / 3600:.4f} h -> CSV lap ~{drv_end}")

    # --- SIGNAL 2: pit brackets ---
    pit_before, pit_after = pit_brackets(laps, drv_start)
    pit_before_end, pit_after_end = pit_brackets(laps, drv_end)
    if pit_before is not None:
        print(f"[pit brackets] stint opens after CSV lap {pit_before} "
              f"({laps[pit_before]:.1f} s pit/driver-change); "
              f"closes at CSV lap {pit_after_end} ({laps.get(pit_after_end, float('nan')):.1f} s)")

    # --- app valid laps on the shipping GPS9 true-clock axis (rate = 1.0, no calibration) ---
    sess = Session.load(paths)
    valid = sess.valid_lap_ids()
    app = np.array([sess.laps.lap_time(i) for i in valid])
    # Optional explicit rate probe: a lap inside one contiguous run scales linearly with the
    # within-run spacing, so a hypothetical `rate` axis is `rate * app`. `rate == 1.0` (default,
    # the shipping behaviour) makes `scaled == app` and the before/after columns coincide.
    scaled = rate * app
    print(f"\napp valid laps: n={len(valid)} best={app.min():.4f}s median={np.median(app):.4f}s "
          f"(rate={rate})")

    # --- SIGNAL 4: duration-correlation LOCK (search a window around the elapsed-time guess) ---
    lo = max(min(laps), drv_start - 12)
    hi = drv_start + 12
    start, corr, offsets = best_offset(app, laps, lo, hi)
    if start is None:
        print("could not lock an alignment (no high-correlation offset found).")
        return {}
    csv_ids = [start + k for k in range(len(valid))]
    csv_t = np.array([laps[i] for i in csv_ids])
    print(f"[duration-corr LOCK] app valid laps <-> CSV {csv_ids[0]}..{csv_ids[-1]}  corr={corr:.4f}")
    print("  uniqueness (corr vs offset):")
    for s, c, nrac in offsets:
        mark = "  <== LOCKED" if s == start else ""
        print(f"    start={s}: corr={c:+.4f} (n_racing={nrac}){mark}")

    # --- residuals: the default (rate=1.0) axis, plus the explicit-rate probe if rate != 1.0 ---
    r_def = app - csv_t
    r_scaled = scaled - csv_t
    m = (app <= RACING_MAX_S) & (csv_t <= RACING_MAX_S)
    # Per-lap GPS dropout flag (a dropout near S/F distorts the crossing instant).
    dropout = np.array([_has_dropout(sess, i) for i in valid])
    clean = m & ~dropout

    def report(mask, label):
        sd = residual_stats(r_def[mask])
        print(f"\n{label} (n={int(mask.sum())}):")
        print(f"  default (rate=1.0):  mean={sd['mean']:+.4f} median={sd['median']:+.4f} "
              f"std={sd['std']:.4f} RMS={sd['rms']:.4f}")
        if rate != 1.0:
            ss = residual_stats(r_scaled[mask])
            print(f"  probe (rate={rate}): mean={ss['mean']:+.4f} "
                  f"median={ss['median']:+.4f} std={ss['std']:.4f} RMS={ss['rms']:.4f}")
        return sd

    sd_all = report(np.ones(len(valid), bool), "ALL aligned laps")
    sd_rac = report(m, "CLEAN racing laps (<=72 s both)")
    sd_cln = report(clean, "CLEAN racing laps, GPS-dropout laps excluded")

    # This recording's own best-fit clock rate on the clean laps — the proof the true rate is ≈1.0
    # (a calibration factor far from 1.0 would be an overfit; this is exactly why one was removed).
    a, c = app[clean], csv_t[clean]
    k_fit = float(np.sum(a * c) / np.sum(a * a))
    print(f"\nthis recording's best-fit clock rate (clean laps): k={k_fit:.6f} "
          f"((k-1)={(k_fit - 1) * 1e6:+.0f} ppm) — ≈1.0 confirms the default axis needs no factor")

    # Per-lap table.
    print(f"\n{'k':>3} {'app':>4} {'csv':>4} {'csv_s':>8} {'app_s':>8} {'r_def':>8} {'flag':>10}")
    rows = []
    for k, lid in enumerate(valid):
        flag = "racing" if clean[k] else ("dropout" if dropout[k] else "pit/slow")
        print(f"{k:>3} {lid:>4} {csv_ids[k]:>4} {csv_t[k]:>8.3f} {app[k]:>8.3f} "
              f"{r_def[k]:>+8.3f} {flag:>10}")
        rows.append({"k": k, "app_lap": int(lid), "csv_lap": csv_ids[k],
                     "csv_s": float(csv_t[k]), "app_s": float(app[k]),
                     "r_def": float(r_def[k]), "flag": flag})

    result = {
        "recording": chapters.recording_label(paths),
        "gps9_utc_start": _fmt_utc(first_ms), "gps9_utc_end": _fmt_utc(last_ms),
        "footage_duration_s": total_dur,
        "csv_lap_range": [csv_ids[0], csv_ids[-1]],
        "pit_brackets": [pit_before, pit_after_end],
        "duration_corr": corr, "alignment_offsets": offsets,
        "rate": rate, "k_fit_this_recording": k_fit,
        "default_cal": {"all": sd_all, "racing": sd_rac, "clean": sd_cln},
        "per_lap": rows,
    }
    if dump:
        with open(dump, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nwrote {dump}")
    return result


def run_lock_only(recording: str, csv_path: str, dump: str | None = None) -> dict:
    """Lock-only mode (module docstring): lock each stint of `recording` against every row of the
    timing sheet `csv_path`, and measure the residual on every stint that LOCKS. Returns (and
    `--dump`s) the whole result; a stint that does not lock carries its verdict and no residual."""
    paths = chapters.discover_siblings(recording)
    print(f"recording: {chapters.recording_label(paths)} ({len(paths)} chapter(s))")
    rows, starts = load_rows(csv_path)
    heats = sorted({key[0] for key in rows}, key=str)
    print(f"timing sheet: {len(rows)} driver row(s) across {len(heats)} heat(s)")

    first_ms, last_ms, total_dur, dop = footage_gps9(paths)
    if first_ms is None:
        print("no GPS9 timestamps in this recording — cannot validate its timing.")
        return {}
    print(f"GPS9 UTC {_fmt_utc(first_ms)} .. {_fmt_utc(last_ms)}  ({total_dur:.1f} s of footage), "
          f"median DOP {dop:.2f}")

    sess = Session.load(paths)
    times = [float(sess.laps.lap_time(i)) for i in range(sess.laps.laps_count())]
    valid = set(sess.valid_lap_ids())
    result = {"recording": chapters.recording_label(paths), "gps9_utc_start": _fmt_utc(first_ms),
              "gps9_utc_end": _fmt_utc(last_ms), "footage_duration_s": total_dur,
              "median_dop": dop, "rows": len(rows), "heats": heats, "stints": []}

    for ids in stints(times):
        app = np.array([times[i] for i in ids])
        n_racing = int((app <= RACING_MAX_S).sum())
        first_fix = sess.laps.get_lap(ids[0]).points[0].point.timestamp_ms
        entry = {"app_laps": [ids[0], ids[-1]], "n": len(ids), "n_racing": n_racing,
                 "first_crossing_utc": _fmt_utc(first_fix)}
        result["stints"].append(entry)
        print(f"\nstint: app laps {ids[0]}..{ids[-1]} ({len(ids)} laps, {n_racing} racing), "
              f"first crossing {entry['first_crossing_utc']}")
        if n_racing < LOCK_MIN_LAPS:
            entry.update(locked=False, why=f"{n_racing} racing laps — a fingerprint needs "
                                           f"{LOCK_MIN_LAPS}")
            print(f"  NOT LOCKABLE: {entry['why']}")
            continue

        cands = lock_all(app, rows)
        v = lock_verdict(cands)
        print(f"  {len(cands)} candidate windows; best by r:")
        for c in sorted(cands, key=lambda c: c["r"], reverse=True)[:5]:
            print(f"    row {c['row']} from lap {c['start']}: r={c['r']:+.4f}  "
                  f"residual σ={c['sd']:.4f} s  (n={c['n']})")
        entry.update(locked=v["locked"], why=v.get("why", ""), candidates=len(cands))
        if "lock" in v and "margin" in v:
            entry.update(lock_r=v["lock"]["r"], lock_sd=v["lock"]["sd"], margin=v["margin"],
                         best_rival_r=v["best_rival_r"]["r"], nearest_rival_sd=v["best_rival_sd"]["sd"],
                         separation=v["separation"])
            print(f"  margin (r) = {v['margin']:+.4f} over the best rival's r "
                  f"{v['best_rival_r']['r']:+.4f};  separation (σ) = {v['separation']:.1f}× "
                  f"(nearest rival σ {v['best_rival_sd']['sd']:.4f} s vs {v['lock']['sd']:.4f} s)")
        if not v["locked"]:
            print(f"  AMBIGUOUS: {v['why']} — no residual is reported for this stint")
            continue

        key, start = v["lock"]["row"], v["lock"]["start"]
        csv_t = np.array([rows[key][start + k] for k in range(len(ids))])
        r_def = app - csv_t
        racing = (app <= RACING_MAX_S) & (csv_t <= RACING_MAX_S)
        dropout = np.array([_has_dropout(sess, i) for i in ids])
        clean = racing & ~dropout & np.array([i in valid for i in ids])
        print(f"  LOCKED: row {key}, sheet laps {start}..{start + len(ids) - 1}"
              + (f" (heat printed {starts[key[0]]})" if key[0] in starts else ""))
        stats = {name: residual_stats(r_def[mask]) for name, mask in
                 (("all", np.ones(len(ids), bool)), ("racing", racing), ("clean", clean))}
        for name, s in stats.items():
            print(f"  {name:>6} (n={s['n']}): mean={s['mean']:+.4f} median={s['median']:+.4f} "
                  f"std={s['std']:.4f} RMS={s['rms']:.4f}")
        rc = r_def[clean]
        a, c = app[clean], csv_t[clean]
        entry.update(
            row=list(key), sheet_laps=[start, start + len(ids) - 1],
            heat_printed_start=starts.get(key[0]), stats=stats,
            k_fit=float(np.sum(a * c) / np.sum(a * a)),
            percentiles={str(q): float(np.percentile(rc, q)) for q in (0, 10, 25, 50, 75, 90, 100)},
            per_lap=[{"app_lap": int(i), "sheet_lap": start + k, "sheet_s": float(csv_t[k]),
                      "app_s": float(app[k]), "r_def": float(r_def[k]),
                      "flag": "racing" if clean[k] else ("dropout" if dropout[k] else "pit/slow")}
                     for k, i in enumerate(ids)])
        pct = "  ".join(f"p{q}={x:+.3f}" for q, x in entry["percentiles"].items())
        print(f"  clean residual percentiles (s): {pct};  clock rate k={entry['k_fit']:.6f}")
        for p in entry["per_lap"]:
            print(f"    app {p['app_lap']:>3}  sheet {p['sheet_lap']:>3}  {p['sheet_s']:8.3f}  "
                  f"{p['app_s']:8.3f}  {p['r_def']:+.3f}  {p['flag']}")

    if dump:
        with open(dump, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nwrote {dump}")
    return result


def _has_dropout(sess: Session, lap_id: int) -> bool:
    """True if the lap has an interior GPS dropout (a point-to-point time jump above
    DROPOUT_GAP_S) — such a hole near the start/finish line distorts the interpolated crossing
    instant and makes the lap a timing outlier independent of any clock-rate."""
    lap = sess.laps.get_lap(lap_id)
    ts = np.array([p.time for p in lap.points])
    return bool(len(ts) > 1 and np.diff(ts).max() > DROPOUT_GAP_S)


def main(argv) -> int:
    ap = argparse.ArgumentParser(prog="studio.dev._validate_wallclock")
    ap.add_argument("recording", help="the recording's first chapter (INPUT, read only)")
    ap.add_argument("csv", help="the timing sheet: a transponder CSV or a Club Speed lap CSV "
                                "(INPUT, read only)")
    ap.add_argument("--race-start", default=None,
                    help="absolute UTC of CSV lap-1 timing start, e.g. '2026-05-23 12:00:00Z'. "
                         "Omit it for LOCK-ONLY mode (a sprint, or a sheet of many drivers)")
    ap.add_argument("--local-start", default=None,
                    help="stated wall-clock the footage began (sanity check of the UTC offset)")
    ap.add_argument("--dump", default=None, help="write the full result JSON to this path")
    ap.add_argument("--rate", type=float, default=1.0,
                    help="optional explicit clock-rate multiplier to PROBE against the default "
                         "rate=1.0 axis (the shipping behaviour); e.g. 0.999514 reproduces the "
                         "removed-overfit finding. Default 1.0 = no probe.")
    args = ap.parse_args([a for a in argv if a != "--"])
    if args.dump:
        # The one output. Never an input, never beside the footage (AGENTS.md rule 1).
        dump = os.path.abspath(os.path.expanduser(args.dump))
        if dump in {os.path.abspath(args.recording), os.path.abspath(args.csv)}:
            ap.error("--dump names one of the inputs")
        if dump.startswith(os.path.expanduser("~/Desktop") + os.sep):
            ap.error("--dump is under ~/Desktop, which holds the owner's footage and is never written")
    if args.race_start is None:
        if args.local_start or args.rate != 1.0:
            ap.error("--local-start and --rate need --race-start (lock-only mode takes neither)")
        run_lock_only(args.recording, args.csv, args.dump)
        return 0
    run(args.recording, args.csv, _parse_when(args.race_start),
        _parse_when(args.local_start) if args.local_start else None, args.dump, args.rate)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
