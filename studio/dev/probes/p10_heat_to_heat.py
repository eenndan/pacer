"""P10 — heat-to-heat: does "comparable" ever pass on the owner's sessions, and is the delta aimable (F8).

F8 proposed storing each session's median lap, σ and top speed in the library so it could say
"this session against the last comparable one", gated by `focus.verdict`'s refusal rules. This
probe answers the two questions that decide whether that surface would ever show anything. Its
verdict is `studio/docs/refused-2026-09.md` §8.

  1. THE GATE. Every pair of the owner's present Sandown recordings goes through `focus.verdict`
     itself — not a copy of its rules — with the WHOLE LAP as the window (enter 0, exit 1), which
     is exactly the backlog's "reuse focus.verdict": the item's baseline is the earlier session's
     clean-lap median and IQR, the sample is the later one's. It runs twice:
       a. against the owner's REAL session-record store, read in place and never written, because
          its state IS the answer the app would give today;
       b. against a planted pair of records that agree ("dry" on both), to show what writing a
          record would unlock — and what the verdict then says.
  2. THE SIZE. For every pair: the median-lap delta against each side's within-session spread
     (IQR and σ over the clean laps), against `coaching.SPREAD_MARGIN` × the wider IQR — the bar
     `focus.verdict` applies — and against the standard error of the two medians (the normal
     approximation `focus.verdict`'s own comment uses, 1.2533 × IQR / 1.349 / √laps).

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p10_heat_to_heat

SAFETY, enforced rather than promised (as p4/p6/p8/p9 enforce it):
  * EVERY FOOTAGE FOLDER IS READ-ONLY. Paths under ~/Desktop are only opened for reading, and every
    file in each folder is snapshotted (size, mtime) before and after each load.
  * `studio.dev._jail` diverts every app-support seam BEFORE any load. The owner's `tracks.json`
    is COPIED into the jail (read from the real dir, written only into the jail) so track
    detection and the start line are the ones the owner's own app used; without it Sandown is an
    unknown track with an auto-fitted provisional line, and every pair would be refused on that
    instead of on what the owner's app actually says.
  * The real app-support directory is snapshotted before and after the whole run and must come
    back unchanged. The only file this probe writes is its row dump, inside the jail.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import sys
import time

DESKTOP = os.path.expanduser("~/Desktop")
# The owner's present Sandown recordings (common brief §1), oldest first — the order a "previous
# comparable session" is looked for in.
RECORDINGS = {
    "0064": ("Sandown 3h 2026", "GX010064.MP4"),
    "0065": ("SD_30_08_26", "GX010065.MP4"),
    "0068": ("SD_19_09_26", "GX010068.MP4"),
}
_REAL_APP_SUPPORT = os.path.join(os.path.expanduser("~"), "Library", "Application Support",
                                 "pacer")


def _folder_state(path: str) -> dict:
    if not os.path.isdir(path):
        return {}
    return {n: (os.stat(os.path.join(path, n)).st_size, os.stat(os.path.join(path, n)).st_mtime_ns)
            for n in sorted(os.listdir(path)) if not n.startswith(".")}


def _se(iqr: float, n: int) -> float:
    """Standard error of a median under the normal approximation — the one `focus.verdict`'s
    comment quotes (1.2533 × IQR / 1.349 / √laps)."""
    return 1.2533 * iqr / 1.349 / math.sqrt(n)


def measure(key: str) -> dict:
    """Load one recording (all chapters) and reduce it to the facts the gate and the delta read."""
    from studio import chapters, focus
    from studio.session import Session

    folder, first = RECORDINGS[key]
    root = os.path.join(DESKTOP, folder)
    before = _folder_state(root)
    paths = chapters.discover_siblings(os.path.join(root, first))
    t0 = time.time()
    s = Session.load(paths)
    entry = s.library_entry(paths)
    clean = [float(s.laps.lap_time(i)) for i in s.consistency_lap_ids()]
    sample = focus.sample_window(clean)
    pace = s.stats.pace()
    vmax = s.stats.session_vmax()
    basis = s.corners.basis()
    out = {
        "key": key, "chapters": len(paths), "load_s": time.time() - t0, "entry": entry,
        "clean": len(clean), "median": sample.median, "iqr": sample.iqr,
        "sigma": pace.sigma if pace else None, "vmax": vmax[0] if vmax else None,
        "lap_total": float(basis[1]) if basis else 0.0,
        "stints": [(st.n, st.median, st.sigma) for st in s.stats.stints()],
    }
    # The pace tile and the focus sample are one statistic over one lap set; say so if they split.
    assert pace is not None and abs(pace.median - sample.median) < 1e-12, (pace, sample)
    if _folder_state(root) != before:
        raise SystemExit(f"ABORT: {root} changed during the load")
    return out


def verdict(then: dict, now: dict, records: dict):
    """`focus.verdict` on the whole lap: `then` is the baseline, `now` the session in front."""
    from studio import focus

    e0, e1 = then["entry"], now["entry"]
    item = focus.FocusItem(
        cid=0, direction=1, enter_frac=0.0, exit_frac=1.0, median_s=then["median"],
        iqr_s=then["iqr"], n_laps=then["clean"], time_lost=0.0, reason="", reach="",
        fingerprint=e0["fingerprint"], date=e0["date"], lap_total=then["lap_total"],
        verified=e0["verified"], degraded=e0["degraded"])
    # The same context `Session.focus_report` builds for the session in front.
    ctx = {"list_track": e0["track"], "track": e1["track"], "fingerprint": e1["fingerprint"],
           "date": e1["date"], "lap_total": now["lap_total"], "verified": e1["verified"],
           "degraded": e1["degraded"]}
    now_sample = focus.CornerSample(median=now["median"], iqr=now["iqr"], n_laps=now["clean"])
    return focus.verdict([item], ctx, [now_sample], records).outcomes[0]


def main() -> None:
    from studio.dev import _jail

    real_before = _folder_state(_REAL_APP_SUPPORT)
    jail = _jail.divert_app_support("pacer-p10-")
    print(f"app-support seams diverted to {jail.dir}")
    real_tracks = os.path.join(_REAL_APP_SUPPORT, "tracks.json")
    if os.path.isfile(real_tracks):
        shutil.copyfile(real_tracks, os.path.join(jail.dir, "tracks.json"))
        print("owner's tracks.json copied INTO the jail (only read on the real side)")

    from studio import focus, library, session_record
    from studio.coaching import SPREAD_MARGIN

    real_records_path = os.path.join(_REAL_APP_SUPPORT, "session_records.json")
    real_records = session_record.load(real_records_path)
    print(f"owner's session_records.json: "
          f"{'present' if os.path.exists(real_records_path) else 'ABSENT'}, "
          f"{len(real_records.get('records', {}))} record(s)")
    real_index = library.load(os.path.join(_REAL_APP_SUPPORT, "library.json"))
    print(f"owner's library.json: {len(real_index['entries'])} entries; Sandown rows: "
          + ", ".join(f"{e['fingerprint']} {e['date']} "
                      f"{'present' if any(os.path.exists(p) for p in e['paths']) else 'GONE'}"
                      for e in real_index["entries"] if e.get("track") == "Sandown Park"))

    rows = {k: measure(k) for k in RECORDINGS}
    print("\nrec   date        laps clean  median   IQR    σ      best     vmax   lap m   stints "
          "(laps @ median)")
    for k, m in rows.items():
        e = m["entry"]
        stints = "  ".join(f"{n} @ {med:.3f}" for n, med, _sig in m["stints"])
        print(f"{k}  {e['date']}  {e['lap_count']:>4} {m['clean']:>5}  {m['median']:.3f}  "
              f"{m['iqr']:.3f}  {m['sigma']:.3f}  {e['best']:.3f}  {m['vmax']:.1f}  "
              f"{m['lap_total']:.1f}   {stints}   verified={e['verified']} "
              f"degraded={e['degraded']} track={e['track']!r}")

    alike = session_record.empty_store()
    for m in rows.values():
        session_record.put(alike, m["entry"]["fingerprint"], {"conditions": "dry"})

    print("\npair         previous?  today (owner's records)        with alike records   "
          "Δmedian  bar    ×bar  SE(Δ)  ×SE   Δbest   Δvmax  drift")
    keys = list(RECORDINGS)
    for a in range(len(keys)):
        for b in range(a + 1, len(keys)):
            then, now = rows[keys[a]], rows[keys[b]]
            today = verdict(then, now, real_records)
            planted = verdict(then, now, alike)
            d_med = now["median"] - then["median"]
            bar = SPREAD_MARGIN * max(then["iqr"], now["iqr"])
            se = math.hypot(_se(then["iqr"], then["clean"]), _se(now["iqr"], now["clean"]))
            drift = abs(now["lap_total"] / then["lap_total"] - 1.0)
            today_s = (f"{today.kind}/{today.blocker} ({today.detail})" if today.blocker
                       else today.kind)
            print(f"{keys[a]} → {keys[b]}  {'yes' if b == a + 1 else 'no ':<9}  "
                  f"{today_s:<30} {planted.kind + ('/' + planted.blocker if planted.blocker else ''):<20} "
                  f"{d_med:+.3f}  {bar:.3f}  {abs(d_med) / bar:.2f}  {se:.3f}  "
                  f"{abs(d_med) / se:.1f}  "
                  f"{now['entry']['best'] - then['entry']['best']:+.3f}  "
                  f"{now['vmax'] - then['vmax']:+.1f}  {100 * drift:.2f} % "
                  f"(limit {100 * focus.MAX_LAP_TOTAL_DRIFT:.0f} %)")
            assert today.delta is None, "a refused verdict must never carry a number"
            print(f"      today's line: {focus.outcome_sentence(today)}")
            print(f"      with records: {focus.outcome_sentence(planted)}")

    print("\n0065 vs 0068 stint by stint (the one pair with the same shape of day):")
    for (n0, m0, _), (n1, m1, _) in zip(rows["0065"]["stints"], rows["0068"]["stints"],
                                        strict=False):
        print(f"  {n0} laps @ {m0:.3f}  →  {n1} laps @ {m1:.3f}   Δ {m1 - m0:+.3f} s")

    if _folder_state(_REAL_APP_SUPPORT) != real_before:
        raise SystemExit("ABORT: the real app-support directory changed during the run")
    print("\nreal app-support directory unchanged; every footage folder unchanged")
    out = os.path.join(jail.dir, "p10_rows.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=1)
    print(f"rows written to {out}")


if __name__ == "__main__":
    sys.exit(main())
