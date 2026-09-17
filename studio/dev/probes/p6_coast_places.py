"""P6 — where the coasting is, and whether the order of places is a finding (F5).

For each recording it prints `Session.coast_report()` — the Stats page's COASTING table — and then
asks what that table's order is worth, over EXACTLY the rows the page ranks
(`Session._coast_rows`, not a re-implementation):

  * the verdict on the odd / even laps and the first / second half alone;
  * a NEGATIVE control: each lap's places shuffled among themselves, so no place leads by
    construction — how often does "the leader separates" still fire?
  * a POSITIVE control: the runner-up given enough extra coasting to lead by a planted margin on
    ~60 % of laps — how often is it crowned AND separated?
  * how far the numbers move on the normalized projection instead of the warp;
  * the REFUSED alternative: zones grown from where the laps coast (coverage threshold x merge
    gap) — which leader each knob setting crowns.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p6_coast_places
    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p6_coast_places 0060 Sandown

SAFETY, enforced rather than promised:
  * EVERY FOOTAGE FOLDER IS READ-ONLY. Paths under ~/Desktop are only ever opened for reading, and
    every file in each folder read is snapshotted (size, mtime) before and after each load.
    `D24/GX010060.MP4` is the 2.4 MB JSON stub that overwrote the owner's footage; it is never
    listed here and never opened.
  * `Session.load` UPSERTS INTO THE LIBRARY, so `studio.dev._jail` diverts every app-support seam
    BEFORE any studio import resolves one, and the real app-support directory is snapshotted before
    and after the whole run. No subprocess loads the app.
"""

from __future__ import annotations

import os
import sys
import time

import numpy as np

DESKTOP = os.path.expanduser("~/Desktop")
# name -> (folder, chapters, restore the saved start line the way the app does on open)
RECORDINGS = {
    "0060": ("D24", ["GX020060.MP4", "GX030060.MP4"], False),
    "0062": ("D24", ["GX010062.MP4", "GX020062.MP4", "GX030062.MP4"], False),
    "Sandown": ("Sandown_09_05_2026", ["GX010059.MP4", "GX020059.MP4", "GX030059.MP4"], True),
    "SD_30_08": ("SD_30_08_26", ["GX010065.MP4", "GX020065.MP4"], True),
    "Sandown3h": ("Sandown 3h 2026", ["GX010064.MP4", "GX020064.MP4", "GX030064.MP4"], True),
}
_STUB = "GX010060.MP4"
_REAL_APP_SUPPORT = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "pacer")
NEG_SHUFFLES = 1000
POS_DRAWS = 200
POS_MARGINS = (0.2, 0.3, 0.5)          # s a lap the planted runner-up leads by
ZONE_COVERAGE = (0.05, 0.10, 0.15, 0.20, 0.25)
ZONE_GAP_M = (5.0, 10.0, 20.0)


def _folder_state(path: str) -> dict:
    if not os.path.isdir(path):
        return {}
    return {n: (os.stat(os.path.join(path, n)).st_size, os.stat(os.path.join(path, n)).st_mtime_ns)
            for n in sorted(os.listdir(path)) if not n.startswith(".")}


def _verdict(report) -> str:
    lead = report.places[0]
    tied = [p.label for p in report.places if p.tied]
    return (f"leader {lead.label} {lead.s_per_lap:.3f} s/lap, "
            + ("SEPARATES" if report.lead_separable else f"tied with {len(tied) - 1}: {tied}"))


def _zone_leaders(session) -> list[str]:
    """The refused alternative: zones = runs of the reference odometer where >= `cov` of the clean
    laps coast, merged across gaps <= `gap`, ranked by coast seconds per lap. Spans are placed on
    the reference odometer through each lap's own warp, inverted."""
    basis = session.corners.basis()
    total_ref = float(basis[1])
    ids = session.consistency_lap_ids()
    placed = []
    for i in ids:
        dist, _v, _t = session._lap_arrays(i)
        total_lap = float(dist[-1])
        align = session.corners.lap_alignment(i, total_lap)

        def to_ref(d, align=align, total_lap=total_lap):
            if align is None:
                return float(d) * total_ref / total_lap
            return float(np.interp(d, align[1], align[0]))
        placed.append([(to_ref(sp.start_dist), to_ref(sp.end_dist), sp.duration)
                       for sp in session.driving.lap_coasting_spans(i)])
    grid = np.arange(0.0, total_ref, 1.0)
    occ = np.zeros((len(ids), grid.size), bool)
    for k, spans in enumerate(placed):
        for a, b, _d in spans:
            occ[k, int(a):max(int(np.ceil(b)), int(a) + 1)] = True
    cover = occ.mean(axis=0)
    out = []
    for cov in ZONE_COVERAGE:
        for gap in ZONE_GAP_M:
            zones = []
            on = np.flatnonzero(cover >= cov)
            for x in on:
                if zones and x - zones[-1][1] <= gap:
                    zones[-1][1] = x + 1
                else:
                    zones.append([x, x + 1])
            if not zones:
                out.append(f"cov {cov:.2f} gap {gap:>2.0f}: no zone")
                continue
            secs = np.zeros(len(zones))
            for spans in placed:
                for a, b, d in spans:
                    for z, (za, zb) in enumerate(zones):
                        ov = min(zb, b) - max(za, a)
                        if ov > 0:
                            secs[z] += d * ov / max(b - a, 1e-9)
            z = int(np.argmax(secs))
            cid = next((c.cid for c in session.corners.corner_list()
                        if c.enter <= 0.5 * (zones[z][0] + zones[z][1]) <= c.exit), None)
            out.append(f"cov {cov:.2f} gap {gap:>2.0f}: {len(zones):>2} zones, leader "
                       f"{zones[z][0]:>4}-{zones[z][1]:<4} m (C{cid}) {secs[z] / len(ids):.3f} s/lap")
    return out


def probe_recording(name: str) -> None:
    from studio import coaching, stats
    from studio import corners as corners_alg
    from studio.session import Session

    folder, files, saved = RECORDINGS[name]
    root = os.path.join(DESKTOP, folder)
    paths = [os.path.join(root, f) for f in files]
    assert _STUB not in files or folder != "D24", "the destroyed stub is never opened"
    if not all(os.path.isfile(p) for p in paths):
        print(f"\n{name}: footage missing under {root} — skipped")
        return
    print(f"\n{'=' * 78}\n{name}: {folder}/{', '.join(files)}\n{'=' * 78}")
    before = _folder_state(root)
    t0 = time.time()
    session = Session.load(paths)
    restored = session.restore_saved_timing_lines() if saved else None
    if _folder_state(root) != before:
        raise SystemExit(f"REFUSING TO CONTINUE: a file in {root} changed during the load.")
    print(f"loaded in {time.time() - t0:.0f} s; saved line restored: {restored}; "
          f"{folder} unchanged (size + mtime)")

    report = session.coast_report()
    cids, rows = session._coast_rows()
    m = np.asarray(rows, float)
    assert stats.coast_report(cids, rows) == report, "the probe's rows are not the page's rows"
    n = report.n_laps
    print(f"{n} clean laps; coasting {report.per_lap_s:.2f} s a lap (mean); "
          f"{len(report.places)} places with any")
    print("  place       s/lap  share  laps   tied")
    for p in report.places:
        print(f"  {p.label:<10} {p.s_per_lap:6.3f} {p.share * 100:5.1f}% {p.laps:>3}/{n:<3} "
              f"{'tied' if p.tied else ''}")
    print(f"VERDICT: {_verdict(report)}")
    listed = [p for p in report.places if p.s_per_lap >= 0.05]
    print(f"listed at >= 0.05 s/lap: {len(listed)} rows holding "
          f"{sum(p.share for p in listed) * 100:.1f} % of the coasting")
    opp = session.coaching_opportunities()
    coast_rows = [(f"C{r.cid}", round(r.reason.coast_extra_s, 2), r.evidence.ranked)
                  for r in opp.rows if r.reason.kind == coaching.REASON_COASTING]
    print(f"today's surface: Coaching names coasting as the reason on {len(coast_rows)} of "
          f"{len(opp.rows)} rows {coast_rows}")

    for label, idx in (("odd laps", slice(0, None, 2)), ("even laps", slice(1, None, 2)),
                       ("first half", slice(0, n // 2)), ("second half", slice(n // 2, None))):
        sub = stats.coast_report(cids, m[idx])
        print(f"  {label:<11} ({sub.n_laps} laps): {_verdict(sub)}")

    rng = np.random.default_rng(6)
    live = np.flatnonzero(m.mean(axis=0) > 0)
    fires = 0
    t0 = time.time()
    for _ in range(NEG_SHUFFLES):
        s = m.copy()
        for k in range(n):
            s[k, live] = s[k, rng.permutation(live)]
        fires += stats.coast_report(cids, s).lead_separable
    print(f"NEGATIVE control ({NEG_SHUFFLES} within-lap shuffles over {live.size} places, "
          f"{time.time() - t0:.0f} s): 'the leader separates' fired {fires / NEG_SHUFFLES * 100:.1f} %")

    lead = report.places[0].index
    ru = report.places[1].index
    mean = m.mean(axis=0)
    for margin in POS_MARGINS:
        need = mean[lead] - mean[ru] + margin
        hits = 0
        for _ in range(POS_DRAWS):
            s = m.copy()
            on = rng.random(n) < 0.6
            s[on, ru] += need / 0.6
            got = stats.coast_report(cids, s)
            hits += got.places[0].index == ru and got.lead_separable
        print(f"POSITIVE control: {report.places[1].label} planted to lead by {margin:.1f} s/lap "
              f"-> crowned and separated in {hits / POS_DRAWS * 100:.0f} % of {POS_DRAWS}")

    # The normalized projection instead of the warp: how much would the table move?
    basis = session.corners.basis()
    interior = [b for c in session.corners.corner_list() for b in (c.enter, c.exit)]
    norm = []
    for i in session.consistency_lap_ids():
        dist, _v, elapsed = session._lap_arrays(i)
        edges = [0.0, *corners_alg.project_boundaries(interior, float(basis[1]), float(dist[-1]),
                                                      alignment=None).tolist(), float(dist[-1])]
        norm.append(stats.coast_seconds_by_piece(session.driving.lap_coasting_spans(i), edges,
                                                 dist, elapsed))
    moved = np.abs(np.asarray(norm).mean(axis=0) - mean).max()
    nrep = stats.coast_report(cids, norm)
    print(f"normalized projection: max |d s/lap| {moved:.4f}; verdict {_verdict(nrep)}")

    print("REFUSED alternative, zones grown from the coverage:")
    for line in _zone_leaders(session):
        print(f"  {line}")


def main() -> None:
    from studio.dev import _jail

    real_before = _folder_state(_REAL_APP_SUPPORT)
    jail = _jail.divert_app_support("pacer-p6-")
    print(f"app-support seams diverted to {jail.dir}")
    for name in sys.argv[1:] or list(RECORDINGS):
        probe_recording(name)
    if _folder_state(_REAL_APP_SUPPORT) != real_before:
        raise SystemExit("THE REAL APP-SUPPORT DIRECTORY CHANGED during this run — stop and look.")
    print("\nreal app-support directory unchanged (size + mtime)")


if __name__ == "__main__":
    main()
