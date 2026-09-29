"""p19_truth_real: the ideal lap's noise bias on real footage, and one stability check per
headline number (TRUTH-3; the 2026-09-28 review's move 4, outputs a and b).

The ideal lap is a sum of per-segment MINIMA over noisy cells, so GPS noise pulls it fast
(`tests/test_truth_matrix.py`, row 3). That row measures the bias on the synthetic GoPro, where the
truth is known; this probe asks how large it is on the owner's recordings, where it is not, and
whether a per-recording estimate of it can be trusted.

THE ESTIMATOR. A boundary's timing error enters the segment before it with one sign and the
segment after it with the other, so across laps it shows up as a NEGATIVE covariance between the
two: sigma^2_k = max(0, -cov(seg_before, seg_after)) (the start/finish boundary pairs a lap's last
segment with the NEXT lap's first). A cell's noise is its two boundaries' sum, and the bias is the
bootstrap one: the mean over DRAWS draws of min(cells + N(0, sigma^2)) - min(cells), summed over
the segments. Real driving can co-vary adjacent segments positively, which hides the noise: such a
boundary is clamped to 0 and counted. POINT segments (no donor) carry no time and are skipped.

  synthetic  TRUTH-1's grid (its own harness: `_case`, `_ideal`) at 3 seeds x noise {1, 2, 4.5},
             the estimate beside the true bias (app ideal - true ideal) and a PASS/FAIL line: the
             estimator passes only if every one of the 9 cases is within 30 ms or 30 % of the
             truth. Noise 0 is printed too, unscored (its truth is the partition's own offset).
  real       the four working-set recordings, in date order, jailed and tripwired: the estimated
             bias, then for each headline number (the ideal lap, the coaching lead's time lost, the
             time on the brakes, the focus verdict) a bootstrap over the laps (BOOT resamples,
             seeded) and an odd/even holdout. The focus verdict is cross-session, so the jail's
             focus list is built the way the app builds it: the first Sandown recording's
             shortlist is pre-promoted, each later one is measured against it, and "Mark both dry"
             is clicked (in the jail) where the verdict waits on a session record.

Its verdicts are written in `studio/docs/falsification-2026-09.md`.

ARGV CONTRACT: the mode, then optionally some of the four folder NAMES below (not paths). Nothing
else is accepted, nothing is written but stdout and the jail, and there is no output argument.

    pixi run python -m studio.dev.probes.p19_truth_real synthetic
    pixi run python -m studio.dev.probes.p19_truth_real real [folder ...]
"""
from __future__ import annotations

import dataclasses
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")

import numpy as np

from studio import app_support
from studio.dev._jail import divert_app_support

_JAIL = divert_app_support("p19_truth_real_")

from studio import (  # noqa: E402  (after the jail, as every load below must be)
    chapters,
    coaching,
    focus,
    library,
    prefs,
    session_record,
    track_db,
)
from studio.coaching_panel import (  # noqa: E402  (the debrief's shortlist)
    PANEL_TOP_N,
    _ranked_shown,
)
from studio.session import Session  # noqa: E402

DRAWS = 2000          # noise draws per bias estimate
BOOT = 1000           # lap resamples per stability figure
SEED = 20260929       # every resample and draw is seeded from this
ACCEPT_S, ACCEPT_REL = 0.030, 0.30
# The four working-set recordings, in DATE order (the focus list is built in that order). The
# only folders this probe will open; each is an INPUT, read only.
RECORDINGS = (("Sandown 3h 2026", "GX010064.MP4", "2026-07-19"),
              ("SD_30_08_26", "GX010065.MP4", "2026-08-30"),
              ("MK_18_09_26", "GX010067.MP4", "2026-09-18"),
              ("SD_19_09_26", "GX010068.MP4", "2026-09-19"))
_REAL = app_support.real_dir()


# ------------------------------------------------------------------------------------ tripwires
def _seams_jailed() -> None:
    """Every app-support seam resolves inside the jail, never to the owner's directory."""
    for mod in (focus, library, prefs, session_record, track_db):
        where = os.path.abspath(mod._app_support_dir())
        assert where == os.path.abspath(_JAIL.dir), f"TRIPWIRE: {mod.__name__} resolves {where}"


def _snap(root: str) -> dict:
    """Names, sizes and mtimes under `root` (never contents)."""
    if not os.path.isdir(root):
        return {}
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for n in files:
            st = os.stat(os.path.join(dirpath, n))
            out[os.path.relpath(os.path.join(dirpath, n), root)] = (st.st_size, st.st_mtime_ns)
    return out


# ------------------------------------------------------------------------------------ the ideal
def _segments(sb) -> list[int]:
    """The segments that carry time: every one with a donor (a POINT segment has none)."""
    return [j for j, d in enumerate(sb.donors) if d is not None]


def _mask(sb) -> np.ndarray:
    return np.asarray(sb.admitted, bool) & np.asarray(sb.resolved, bool)


def ideal_of(sb, rows=None) -> float:
    """Sum of the per-segment minima over the admitted and resolved cells of `rows` (all laps when
    None): `SegmentBests.total`'s rule, on a subset or a resample of its laps. NaN when a segment
    has no cell left."""
    t, m = np.asarray(sb.times, float), _mask(sb)
    if rows is not None:
        t, m = t[rows], m[rows]
    total = 0.0
    for j in _segments(sb):
        col = t[m[:, j], j]
        if not len(col):
            return float("nan")
        total += float(col.min())
    return total


def boundary_sigma2(sb, pace: bool = False) -> tuple[dict, int, int]:
    """({segment: its cells' noise variance}, boundaries clamped, boundaries measured). A segment's
    variance is the sum of its two boundaries'; a boundary's is -cov(segment before, segment
    after) over the laps that have both cells, clamped at 0.

    `pace` (POST-HOC, not the plan's estimator): each segment is first regressed on its lap's time,
    which carries no interior boundary's error (they cancel in the sum), so a lap's overall pace no
    longer co-varies its segments."""
    t, m = np.asarray(sb.times, float), _mask(sb)
    segs = _segments(sb)
    if pace:
        lap = t.sum(axis=1)
        t = t.copy()
        for j in segs:
            ok = m[:, j]
            if ok.sum() >= 3:
                b, a = np.polyfit(lap[ok], t[ok, j], 1)
                t[ok, j] -= a + b * lap[ok]
    ids = list(sb.lap_ids)
    pairs = [(a, b, None) for a, b in zip(segs, segs[1:], strict=False)]
    pairs.append((segs[-1], segs[0], "next lap"))
    s2 = dict.fromkeys(segs, 0.0)
    clamped = measured = 0
    for a, b, wrap in pairs:
        if wrap is None:
            ok = m[:, a] & m[:, b]
            x, y = t[ok, a], t[ok, b]
        else:
            nxt = {lid: r for r, lid in enumerate(ids)}
            rr = [(r, nxt[lid + 1]) for r, lid in enumerate(ids)
                  if lid + 1 in nxt and m[r, a] and m[nxt[lid + 1], b]]
            x = np.array([t[r, a] for r, _ in rr])
            y = np.array([t[q, b] for _, q in rr])
        if len(x) < 3:
            continue
        measured += 1
        c = float(np.cov(x, y)[0, 1])
        if c >= 0:
            clamped += 1
        var = max(0.0, -c)
        s2[a] += var
        s2[b] += var
    return s2, clamped, measured


def estimated_bias(sb, seed: int = SEED, pace: bool = False) -> tuple[float, int, int]:
    """(bias s, clamped, measured): the bootstrap bias of the sum of minima under the estimated
    cell noise — negative when the ideal reads fast."""
    t, m = np.asarray(sb.times, float), _mask(sb)
    s2, clamped, measured = boundary_sigma2(sb, pace)
    rng = np.random.default_rng(seed)
    bias = 0.0
    for j in _segments(sb):
        col = t[m[:, j], j]
        if not len(col) or s2[j] <= 0:
            continue
        noisy = col + rng.normal(0.0, np.sqrt(s2[j]), (DRAWS, len(col)))
        bias += float(noisy.min(axis=1).mean()) - float(col.min())
    return bias, clamped, measured


# ------------------------------------------------------------------------------------ synthetic
def synthetic() -> None:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "tests"))
    import test_truth_matrix as tm  # TRUTH-1's harness: one definition of the true ideal

    print(f"ideal-lap bias: estimate vs truth (app ideal - true ideal), ms; {DRAWS} draws, seed {SEED}")
    print(f"accept: |estimate - truth| <= max({ACCEPT_S * 1e3:.0f} ms, {ACCEPT_REL:.0%} of |truth|); "
          f"'pace' is the post-hoc variant (boundary_sigma2), scored the same way but not the plan's")
    ok, by_noise = {False: True, True: True}, {}
    # TRUTH-1's seeds are the scored grid; 3, 4 and 5 are held out, so a variant fitted on the grid
    # is seen on seeds it was not looked at on.
    for seed in (*tm.SEEDS, 3, 4, 5):
        for noise in (0.0, 1.0, 2.0, 4.5):
            case = tm._case(seed, noise)
            sb = case.s.ideal_segment_bests()
            assert abs(ideal_of(sb) - sb.total) < 1e-9, "the recomputed ideal is not SegmentBests.total"
            truth = tm._ideal(case)[0]
            scored = noise > 0 and seed in tm.SEEDS
            line = (f"  noise {noise:3g} seed {seed:>8}: laps {len(sb.lap_ids):2d}  "
                    f"truth {truth * 1e3:+8.1f}")
            for pace in (False, True):
                est, clamped, measured = estimated_bias(sb, pace=pace)
                within = abs(est - truth) <= max(ACCEPT_S, ACCEPT_REL * abs(truth))
                if scored:
                    ok[pace] &= within
                by_noise.setdefault((noise, pace, seed in tm.SEEDS), []).append((truth, est))
                line += (f" | {'pace' if pace else 'plan'} {est * 1e3:+7.1f} clamped "
                         f"{clamped:2d}/{measured} {'ok ' if within else 'OUT'}")
            print(line + ("" if scored else "  (unscored)"))
    for (noise, pace, grid), v in sorted(by_noise.items()):
        tr, es = np.mean(v, axis=0)
        print(f"  noise {noise:3g} {'pace' if pace else 'plan'} {'grid' if grid else 'held-out'} "
              f"seeds, mean: truth {tr * 1e3:+8.1f}  estimate {es * 1e3:+8.1f}")
    print(f"ESTIMATOR (plan) {'PASS' if ok[False] else 'FAIL'} (9 scored cases, noise 1/2/4.5 x "
          f"TRUTH-1's 3 seeds); post-hoc pace variant {'PASS' if ok[True] else 'FAIL'}")


# ------------------------------------------------------------------------------------ real
def _spread(v) -> str:
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    lo, hi = np.percentile(v, [5, 95])
    return f"sd {v.std() * 1e3:.0f} ms, 90 % {lo:.3f}..{hi:.3f} s (n {len(v)})"


def _coaching_inputs(s: Session) -> dict:
    """`Session.coaching_opportunities`' own extraction, kept so `coaching.summarize` can be run on
    a resample; `_summarize(inputs)` must reproduce the app's rows exactly (checked)."""
    ids = s.consistency_lap_ids()
    corner_list = s.corners.corner_list()
    n = len(corner_list)
    cand, times, ctimes, res = [], [], [], []
    for i in ids:
        st = s.corners.lap_corner_stats(i)
        if len(st) != n:
            continue
        cand.append(i)
        times.append(s.lap_time(i))
        ctimes.append([c.time for c in st])
        res.append(s.corners.lap_corner_resolved(i))
    report = s.phase_report()
    return dict(corners=corner_list, cand=cand, times=times, ctimes=ctimes, res=res,
                cells=s._coaching_lap_inputs(cand), best=s.best_lap_id(),
                sigmas={sp.cid: sp.sigma for sp in s.corner_consistency()},
                phases=dict(zip(report.cids, report.rows, strict=True)) if report else {},
                best_res={i: s.corners.lap_corner_resolved(i) for i in set(cand) | {s.best_lap_id()}},
                best_times={i: [c.time for c in s.corners.lap_corner_stats(i)]
                            for i in {s.best_lap_id()}})


def _summarize(x: dict, rows=None, reselect: bool = False) -> coaching.Opportunities:
    rows = range(len(x["cand"])) if rows is None else rows
    cand = [x["cand"][r] for r in rows]
    best = x["best"]
    if reselect:
        best = min(cand, key=lambda i: x["times"][x["cand"].index(i)])
    bt = (x["best_times"][best] if best in x["best_times"]
          else x["ctimes"][x["cand"].index(best)])
    c = x["cells"]
    return coaching.summarize(
        corners=x["corners"], candidate_lap_ids=cand, lap_times=[x["times"][r] for r in rows],
        corner_times_by_lap=[x["ctimes"][r] for r in rows], best_corner_times=bt,
        sigmas_by_cid=x["sigmas"], brake_time_by_lap=[c[i][0] for i in cand],
        coast_time_by_lap=[c[i][1] for i in cand], apex_by_lap=[c[i][2] for i in cand],
        best_brake_time=c[best][0], best_coast_time=c[best][1], best_apex=c[best][2],
        exit_by_lap=[c[i][3] for i in cand], best_exit=c[best][3], phases_by_cid=x["phases"],
        resolved_by_lap=[x["res"][r] for r in rows], best_resolved=x["best_res"][best])


def _lead(opps) -> tuple[int | None, float, tuple[int, ...]]:
    ranked = _ranked_shown(opps)
    top = tuple(r.cid for r in ranked[:PANEL_TOP_N])
    return (ranked[0].cid, ranked[0].time_lost, top) if ranked else (None, float("nan"), ())


def _lost_of(opps, cid) -> float:
    return next((r.time_lost for r in opps.rows if r.cid == cid), float("nan"))


def _window_times(s: Session, windows) -> tuple[list[int], np.ndarray]:
    """(clean lap ids, laps x windows seconds): each lap's time through each window by the focus
    list's OWN instrument (`Session.focus_samples`, asked one lap at a time; NaN = not matched)."""
    ids = s.consistency_lap_ids()
    out = np.full((len(ids), len(windows)), np.nan)
    try:
        for r, lid in enumerate(ids):
            s.consistency_lap_ids = lambda lid=lid: [lid]  # type: ignore[method-assign]
            for k, smp in enumerate(s.focus_samples(windows)):
                if smp is not None and smp.n_laps == 1:
                    out[r, k] = smp.median
    finally:
        del s.consistency_lap_ids
    for k, smp in enumerate(s.focus_samples(windows)):   # the per-lap cells ARE the app's sample
        again = focus.sample_window(out[:, k])
        assert (smp is None) == (again is None), k
        if smp is not None and again is not None:
            assert (again.median, again.iqr, again.n_laps) == (smp.median, smp.iqr, smp.n_laps), k
    return ids, out


def _verdict_kind(item, base, now, ctx, records) -> str:
    b, n = focus.sample_window(base), focus.sample_window(now)
    if b is None:
        return "no baseline"
    it = dataclasses.replace(item, median_s=b.median, iqr_s=b.iqr, n_laps=b.n_laps)
    o = focus.verdict([it], ctx, [n], records).outcomes[0]
    return o.kind if o.has_verdict else f"{o.kind}:{o.blocker}"


def _load(folder: str, first: str):
    root = os.path.join(os.path.expanduser("~/Desktop"), folder)
    if not os.path.exists(os.path.join(root, first)):
        return root, None, None
    before = _snap(root)
    paths = chapters.discover_siblings(os.path.join(root, first))
    s = Session.load(paths)
    assert _snap(root) == before, f"TRIPWIRE: a file under {folder} changed"
    return root, paths, s


def real(only: list[str]) -> None:
    owner_before = _snap(_REAL)
    rng = np.random.default_rng(SEED)
    baselines: dict = {}          # (track, cid) -> per-lap baseline window times, from its promoter
    all_windows: dict = {}        # track -> (7 corner windows, {date: (laps x windows, best row)})
    for folder, first, date in RECORDINGS:
        if only and folder not in only:
            continue
        t0 = time.time()
        _seams_jailed()
        root, paths, s = _load(folder, first)
        if s is None:
            print(f"{folder}: SKIPPED, not on this machine")
            continue
        entry = s.library_entry(paths)
        assert entry.get("date") == date, (folder, entry.get("date"))
        library.upsert_and_save(entry)                     # the jail's library, in date order
        track = entry.get("track")
        sb = s.ideal_segment_bests()
        L = len(sb.lap_ids)
        print(f"\n{folder} ({date}, track {track!r}, verified {entry.get('verified')}, "
              f"{len(s.valid_lap_ids())} valid laps, {L} in the ideal's matrix; "
              f"loaded in {time.time() - t0:.0f} s)")

        # 1 · the ideal lap
        assert abs(ideal_of(sb) - sb.total) < 1e-9
        bias, clamped, measured = estimated_bias(sb)
        boot = np.array([ideal_of(sb, rng.integers(0, L, L)) for _ in range(BOOT)])
        odd, even = ideal_of(sb, np.arange(0, L, 2)), ideal_of(sb, np.arange(1, L, 2))
        print(f"  IDEAL {sb.total:.3f} s; estimated noise bias {bias * 1e3:+.1f} ms "
              f"({clamped} of {measured} boundaries clamped)")
        print(f"    bootstrap ({BOOT}, seed {SEED}): minus the full ideal, median "
              f"{(np.nanmedian(boot) - sb.total) * 1e3:+.0f} ms; {_spread(boot)}")
        print(f"    holdout: odd laps {odd:.3f} s, even laps {even:.3f} s, apart "
              f"{(odd - even) * 1e3:+.0f} ms (full {sb.total:.3f} s)")

        # 2 · the coaching lead's time lost
        x = _coaching_inputs(s)
        mine, app = _summarize(x), s.coaching_opportunities()
        assert [(r.cid, r.time_lost, r.evidence.ranked) for r in mine.rows] == \
            [(r.cid, r.time_lost, r.evidence.ranked) for r in app.rows], "summarize replica drifted"
        lead, lost, top = _lead(app)
        n = len(x["cand"])
        print(f"  TIME LOST: lead C{lead} {lost:.3f} s, shortlist {[f'C{c}' for c in top]} "
              f"({n} laps)")
        for reselect in (False, True):
            got = [_summarize(x, rng.integers(0, n, n), reselect) for _ in range(BOOT)]
            leads = [_lead(o) for o in got]
            print(f"    bootstrap, {'re-selected' if reselect else 'fixed'} best: lead kept in "
                  f"{np.mean([g[0] == lead for g in leads]):.0%}, shortlist recurs in "
                  f"{np.mean([set(g[2]) == set(top) for g in leads]):.0%}; C{lead}'s time lost "
                  f"{_spread([_lost_of(o, lead) for o in got])}")
        halves = [_summarize(x, range(k, n, 2)) for k in (0, 1)]
        print("    holdout: " + "; ".join(
            f"{w} laps lead C{_lead(h)[0]} {_lead(h)[1]:.3f} s, C{lead} {_lost_of(h, lead):.3f} s"
            for w, h in zip(("odd", "even"), halves, strict=True)))

        # 3 · the time on the brakes (Stats: "on the brakes / lap · median")
        brake = np.array([r.brake_s for r in s.stats.lap_stats() if r.brake_s is not None])
        if len(brake):
            med, shown = float(np.median(brake)), f"{np.median(brake):.1f} s"
            bb = np.array([np.median(brake[rng.integers(0, len(brake), len(brake))])
                           for _ in range(BOOT)])
            print(f"  BRAKING {med:.3f} s (tile {shown!r}, {len(brake)} laps); bootstrap: tile "
                  f"unchanged in {np.mean([f'{v:.1f} s' == shown for v in bb]):.0%}, {_spread(bb)}; "
                  f"holdout odd {np.median(brake[0::2]):.3f} s, even {np.median(brake[1::2]):.3f} s")
        else:
            print("  BRAKING: no g signal, no tile")

        # 4 · the focus verdict, the app's way, in the jail
        items = focus.for_track(focus.load(), track) if track else []
        if items:
            ctx_entry = entry
            report = s.focus_report(items, ctx_entry, session_record.load(), track)
            print("  FOCUS, as the app says it: " + "; ".join(
                f"C{o.item.cid} {o.kind}{':' + o.blocker if o.blocker else ''}" for o in report.outcomes))
            if report.unrecorded:
                rows = {e.get("fingerprint"): e for e in library.load().get("entries", [])}
                dry = {**session_record.blank_record(), "conditions": "dry"}
                session_record.put_if_blank_and_save(
                    {fp: session_record.stamp_context(dry, rows.get(fp)) for fp, _ in report.unrecorded})
                report = s.focus_report(items, ctx_entry, session_record.load(), track)
                print("    after 'Mark both dry' (jail): " + "; ".join(
                    f"C{o.item.cid} {o.kind}"
                    + (f" {o.delta:+.3f} s" if o.delta is not None else f":{o.blocker}")
                    for o in report.outcomes))
            ctx, records = s.focus_context(ctx_entry, track), session_record.load()
            ids, now = _window_times(s, [(i.enter_frac, i.exit_frac) for i in items])
            for k, (item, o) in enumerate(zip(items, report.outcomes, strict=True)):
                base = baselines.get((track, item.cid, item.fingerprint))
                if base is None or not o.has_verdict:
                    continue
                kinds = [_verdict_kind(item, base[rng.integers(0, len(base), len(base))],
                                       now[rng.integers(0, len(now), len(now)), k], ctx, records)
                         for _ in range(BOOT)]
                hold = [_verdict_kind(item, base[h::2], now[h::2, k], ctx, records) for h in (0, 1)]
                print(f"    C{item.cid} {o.kind}: bootstrap same verdict in "
                      f"{np.mean([q == o.kind for q in kinds]):.0%} "
                      f"({', '.join(f'{q} {kinds.count(q)}' for q in sorted(set(kinds)))}); "
                      f"holdout odd {hold[0]}, even {hold[1]}")
        # the debrief's pre-promotion (LibraryController.pre_promote_focus), into free slots only
        if track and entry.get("verified") and not entry.get("degraded") \
                and not focus.newer_than(items, s.focus_context(entry, track)):
            taken = {i.cid for i in items}
            wanted = [c for c in top if c not in taken][:max(focus.MAX_ITEMS - len(items), 0)]
            added = s.focus_items(wanted, entry) if wanted else []
            if added:
                focus.save_for_track(track, items + added)
                _ids, base = _window_times(s, [(i.enter_frac, i.exit_frac) for i in added])
                for k, it in enumerate(added):
                    baselines[(track, it.cid, it.fingerprint)] = base[:, k]
                print(f"  FOCUS: pre-promoted {[f'C{i.cid}' for i in added]} onto {track!r} "
                      f"(the verdict is measured on a later session)")
        elif not items:
            print("  FOCUS: nothing to promote onto (no verified track)")

        # ADV-7: the best lap's cell against the median, on the first session's windows
        cl = s.corners.corner_list()
        basis = s.corners.basis()
        if track and basis and track not in all_windows:
            all_windows[track] = ([(c.enter / basis[1], c.exit / basis[1]) for c in cl], {})
        if track in all_windows:
            ids, cells = _window_times(s, all_windows[track][0])
            best = s.best_lap_id()
            all_windows[track][1][date] = (cells, ids.index(best) if best in ids else None)
        del s
        _seams_jailed()
    for track, (_w, days) in all_windows.items():
        dates = sorted(days)
        if len(dates) < 2:
            continue
        db, dm = [], []
        for a, b in zip(dates, dates[1:], strict=False):
            (ca, ra), (cb, rb) = days[a], days[b]
            if ra is None or rb is None:
                continue
            db.extend(np.abs(cb[rb] - ca[ra]))
            dm.extend(np.abs(np.nanmedian(cb, axis=0) - np.nanmedian(ca, axis=0)))
        print(f"\nADV-7 ({track!r}, {len(dates)} sessions, {len(db)} corner-steps on the first "
              f"session's windows): best-lap cell moved mean |d| {np.nanmean(db):.3f} s, the "
              f"median {np.nanmean(dm):.3f} s")
    assert _snap(_REAL) == owner_before, "TRIPWIRE: the owner's app-support directory changed"
    print("\ntripwires: footage folders unchanged, every seam jailed, owner app-support unchanged")


if __name__ == "__main__":
    mode, rest = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    allowed = {r[0] for r in RECORDINGS}
    if mode not in ("synthetic", "real") or any(a not in allowed for a in rest) or \
            (mode == "synthetic" and rest):
        sys.exit(__doc__)
    synthetic() if mode == "synthetic" else real(rest)
