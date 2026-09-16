"""CI half of the Session equivalence gate — a SYNTHETIC golden regression (no big file needed).

The byte-identity gate that guards every Session refactor (F1 god-object decomposition, E2, the
#50 delta-engine dedup, ...) is the pair studio.dev.golden_session_dump.fingerprint (a dense
whole-public-API fingerprint of a Session's state) + studio.dev.golden_compare.walk (leaf-by-leaf
compare at eps 1e-9). But the canonical dump loads the real ~11.9 GB ``~/Desktop/D24`` recording,
so that FULL gate is MANUAL and dev-Desktop-only — it never runs in CI.

This test automates the SAME machinery over the deterministic SYNTHETIC session the studio test
suite already uses (tests/test_session_services._synthetic_session — a bare Session with the
stadium() loop + a seeded g-meter, driving REAL corner detection / driving channels / delta /
session bests / consistency, with NO media file). It fingerprints that session with the real
``fingerprint`` (in ``strict=False`` mode, which guards the pure pacer-``Laps`` passthroughs the
bare session can't serve and records a sentinel for them, while fingerprinting every Python
Session-math leaf in full — see golden_session_dump), across three phases mirroring the real dump:

  * ``base``        — the freshly built synthetic session;
  * ``ref``         — after ``set_reference_session`` (a second synthetic, same track, adopted),
                      exercising the reference / delta baseline paths + the ``invalidate_stats()``
                      seam;
  * ``ref_cleared`` — after ``clear_reference()``; asserted byte-identical to ``base`` (the
                      reference clear must revert the per-lap Δ baseline exactly);
  * ``drift_noise`` — a SEPARATE session (tests/_synthetic.drift_noise_session): three laps with
                      GPS speed noise at the measured sigma, one of them drifted 1.0 % with a
                      corner boundary its spatial match cannot find. The stadium fixture above
                      has 0 % drift and noise-free speed, so the drift-gated projection and every
                      noise-sensitive detector were dead code to this gate: reverting #228's
                      one-frame warp or #275's coast window left it green. This phase goes red
                      on both (see that fixture's block for why each ingredient is needed).
  * ``drift_median``— the SAME three geometries with the drift on the MEDIAN-time lap
                      (tests/_synthetic.drift_median_session). ``drift_noise`` drifts the SLOWEST
                      lap and the whole coaching model reads the MEDIAN one, so every coaching
                      corner-window projection was the identity in that phase and a coaching-path
                      defect could not move a leaf of it: #289 moved 15 of 168,664 leaves on the
                      D24 0060 pair and 0 synthetic ones. Measured negative control — reverting
                      #289's `_project_window` wiring moves 2 of this phase's 12,101 leaves (a
                      coaching row's `reason.brake_extra_s` by 10.6 ms, 3.909945 -> 3.899391 s,
                      and the `contribution` it feeds) and 0 of `drift_noise`'s 12,115, or of any
                      leaf in the three phases above it.
  * ``drift_band``  — a LADDER across the SUB-GATE DRIFT BAND
                      (tests/_synthetic.drift_band_session). Every lap of the two phases above is
                      either 0.0000 % line-length drift or 0.995 %, so NONE sits in (0 %, 0.5 %] —
                      the band `corners.NORMALIZED_DRIFT_MAX` governed until #300 removed it. That
                      is why removing it moved 0 of all 24,859 leaves while the real boundary
                      residual fell from a median 1.96 m to 0.10 m on D24. Four laps: lap 0 on the
                      reference line, then rungs at 0.118 / 0.289 / 0.460 % drift, each running
                      wide round turn 1 and tight round turn 2 so 2.4-4.2 m of odometer offset
                      survives the near-cancelling length change. Measured negative control —
                      restoring the 0.5 % gate moves 68 of this phase's 15,451 leaves (coaching
                      rows, per-lap corner stats and segment times; max |Δ| 5.84 on an entry
                      speed) and 0 of the five phases above it.

It then compares the whole fingerprint against a COMMITTED baseline
(tests/golden_synthetic_baseline.json, generated on main in the pixi env) via golden_compare.walk
at eps 1e-9 — so ANY drift in a Session-math leaf FAILS the build. This is the CI-runnable
equivalence gate for every future Session refactor.

Scope note: this COMPLEMENTS, it does NOT replace, the manual D24 gate. The synthetic session has
no pacer ``Laps`` object, so the C++ Laps passthroughs (lap_count, sector geometry, session_date,
lap_rows, ...) fall to the sentinel here; the full, higher-coverage fingerprint over the real
recording (``python -m studio.dev.golden_session_dump`` + ``golden_compare``) stays the canonical,
byte-identical (eps 0) gate and is UNCHANGED by this PR.

Tolerance: eps 1e-9 (not exact 0) — the synthetic delta/cross-lap math produces last-bit float
noise (e.g. |Δ| ~1e-14 on a self-referential lap); 1e-9 tolerates that while catching any real
regression. Determinism: the fixture is pure numpy (seeded g-meter, no timestamps / RNG / clock),
so two builds must produce an identical fingerprint — asserted below before the baseline compare.

Run:  QT_QPA_PLATFORM=offscreen python tests/test_golden_synthetic.py
Regenerate the baseline (only after an INTENTIONAL, reviewed Session-math change):
      python tests/test_golden_synthetic.py --write-baseline
"""
import json
import os
import sys

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # tests/ — for the sibling fixture

from _synthetic import (  # noqa: E402  (the drift + noise fixtures)
    DN_SPEED_SIGMA_MPS,
    drift_band_laps,
    drift_band_session,
    drift_median_laps,
    drift_median_session,
    drift_noise_laps,
    drift_noise_session,
)
from test_session_services import _synthetic_session  # noqa: E402  (the shared stadium fixture)

from studio import coaching, corners  # noqa: E402
from studio.dev.golden_compare import EPS, walk  # noqa: E402
from studio.dev.golden_session_dump import fingerprint  # noqa: E402

BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden_synthetic_baseline.json")


def _build():
    """The deterministic synthetic session, with a fixed track name so the fingerprint is stable
    (the bare fixture leaves track_name unset). Same fixture test_session_services /
    test_central_view_realqt drive — reused, not re-derived."""
    s = _synthetic_session()
    s.track_name = "Stadium"
    return s


def _build_drift_noise():
    """The drift + noise session, named like `_build` so its fingerprint is stable too."""
    s = drift_noise_session()
    s.track_name = "Stadium"
    return s


def _build_drift_median():
    """The median-drift session (same geometry, the drift on the lap coaching reads)."""
    s = drift_median_session()
    s.track_name = "Stadium"
    return s


def _build_drift_band():
    """The drift-ladder session (the same reference line, three laps inside the sub-gate band)."""
    s = drift_band_session()
    s.track_name = "Stadium"
    return s


def synthetic_fingerprint() -> dict:
    """The three-phase synthetic fingerprint (base / ref / ref_cleared) — the CI-runnable analogue
    of golden_session_dump.main()'s multi-phase dump, minus the D24 load and the ``reseg`` phase
    (``set_timing_lines`` clears the seeded _cols_cache/_dist_cache that ARE the fixture's only data,
    so a re-segment degrades rather than re-derives on a bare session — the reference seam below
    already exercises the invalidate path)."""
    s = _build()
    result: dict = {}
    result["base"] = fingerprint(s, strict=False)

    # A data-only reference (a second synthetic, same track) — adopted, then cleared.
    ref = _build()
    result["ref_set_reason"] = s.set_reference_session(ref, source_label="ci-ref")
    result["ref"] = fingerprint(s, strict=False)

    s.clear_reference()
    result["ref_cleared"] = fingerprint(s, strict=False)

    # The drift + noise session: the paths the stadium laps cannot reach (see the module docstring).
    result["drift_noise"] = fingerprint(_build_drift_noise(), strict=False)
    # The same geometry with the drift on the MEDIAN lap: the coaching path, which reads that lap
    # and only that lap, and which drift_noise therefore exercises in its identity projection.
    result["drift_median"] = fingerprint(_build_drift_median(), strict=False)
    # The sub-gate drift BAND, as a ladder: the two phases above jump from 0 % drift to 0.995 %,
    # so every lap the removed 0.5 % gate would have kept on the normalized projection is missing
    # from this fingerprint entirely, and a regression in that band moves no leaf of it.
    result["drift_band"] = fingerprint(_build_drift_band(), strict=False)
    return result


def _leaf_count(o) -> int:
    if isinstance(o, dict):
        return sum(_leaf_count(v) for v in o.values())
    if isinstance(o, list):
        return sum(_leaf_count(v) for v in o)
    return 1


def _compare(a: dict, b: dict) -> tuple[list[str], dict]:
    diffs: list[str] = []
    stats = {"n": 0, "max": 0.0, "max_path": ""}
    walk(a, b, "root", diffs, stats)
    return diffs, stats


def test_synthetic_fingerprint_is_deterministic():
    """The fixture is pure (no RNG/clock), so two independent builds must fingerprint identically
    (well within eps) — a flaky gate is worse than none."""
    diffs, stats = _compare(synthetic_fingerprint(), synthetic_fingerprint())
    assert not diffs, f"non-deterministic synthetic fingerprint: {diffs[:5]}"
    assert stats["max"] <= EPS, f"two builds drifted by {stats['max']:g} at {stats['max_path']}"
    print(f"ok determinism: two builds match ({stats['n']} leaves, max |Δ|={stats['max']:g})")


def test_reference_clear_reverts_to_base():
    """clear_reference() must revert the per-lap Δ baseline byte-for-byte — ref_cleared == base
    (the invalidate_stats() seam, pinned here at the fingerprint level)."""
    fp = synthetic_fingerprint()
    diffs, stats = _compare(fp["base"], fp["ref_cleared"])
    assert not diffs, f"ref_cleared drifted from base: {diffs[:5]}"
    assert stats["max"] <= EPS
    print(f"ok revert: ref_cleared == base (max |Δ|={stats['max']:g})")


def test_drift_noise_fixture_reaches_the_paths_it_exists_for():
    """A golden phase is only as good as its fixture, so pin the properties that make this one
    able to fail — each is one a plausible edit to the fixture would quietly remove:

      1. the seeded best lap IS the fastest (the memo is not lying to every best-derived leaf);
      2. exactly one lap drifts past 0.5 % of line length, at 0.9-1.1 % — the drift that made this
         fixture's projection non-trivial back when a 0.5 % gate decided whether to warp at all;
      3. on that lap exactly one interior corner boundary has no spatial match, so its warp
         INTERPOLATES a knot — the case #228 repaired; with every boundary matched, the old
         per-boundary projection and the warp agree and reverting #228 moves nothing;
      4. the GPS speed column carries the measured noise (sample sd within 15 % of
         DN_SPEED_SIGMA_MPS on every lap) — the coast detector differentiates exactly that column;
      5. the ideal lap is not just the best lap (some segment is donated by another lap)."""
    s = _build_drift_noise()
    ids = s.valid_lap_ids()
    times = [s.lap_time(i) for i in ids]
    assert ids[int(np.argmin(times))] == s.best_lap_id() == 0, (times, s.best_lap_id())

    best_total = s.best_lap_total_distance()
    totals = {i: float(s._dist_cache[i][1][-1]) for i in ids}
    drift = {i: corners.line_length_drift(totals[i], best_total) for i in ids}
    over = [i for i in ids if drift[i] > 0.005]
    assert over == [1], f"drift per lap {drift} — exactly lap 1 must be the drifted one"
    assert 0.009 <= drift[1] <= 0.011, f"lap 1 drift {drift[1]:.4%}"

    # Asked of the spatial MATCHER, not of the warp built from it: this pins a property of the
    # fixture, so it must hold whatever a projection does with the miss (reverting #228 changes that,
    # and it is the golden baseline's job, not this test's, to notice).
    interior = [b for c in s.corners.corner_list() for b in (c.enter, c.exit) if 0 < b < best_total]
    ref_cols, lap_cols = s._cols_cache[0], s._cols_cache[1]
    matched = corners._spatial_matches(np.asarray(interior), best_total,
                                       ref_cols[1], ref_cols[2], ref_cols[4],
                                       lap_cols[1], lap_cols[2], lap_cols[4])
    unmatched = [b for b, m in zip(interior, matched, strict=True) if not np.isfinite(m)]
    assert len(unmatched) == 1, (
        f"{len(unmatched)} unmatched interior boundaries on lap 1 (want exactly 1): "
        f"boundaries {interior}, matches {matched.tolist()}")
    assert s.corners.lap_alignment(1, totals[1]) is not None, "lap 1 kept the normalized projection"

    for i, lap in enumerate(drift_noise_laps()):
        sd = float(np.std(lap["cols"][3] - lap["clean_speed"]))
        assert abs(sd - DN_SPEED_SIGMA_MPS) <= 0.15 * DN_SPEED_SIGMA_MPS, f"lap {i} speed sd {sd}"

    donors = s.corners.segment_bests().donors
    assert any(d not in (None, 0) for d in donors), f"every segment donated by the best lap: {donors}"
    print(f"ok drift+noise fixture: lap 1 drift {drift[1]:.3%}, unmatched boundary "
          f"{unmatched[0]:.1f} m, segment donors {donors}")


def test_drift_median_fixture_puts_the_drift_where_coaching_reads():
    """The median-drift phase exists because `drift_noise` drifts the SLOWEST lap while the whole
    coaching model reads the MEDIAN one, so pin the properties that make this fixture able to fail
    where that one cannot — each is one a plausible speed tweak would quietly remove:

      1. the lap coaching reads (`coaching.median_lap_id` over the consistency laps) is the
         DRIFTING one, and is not the best lap (whose window projection is the identity by
         definition — projecting the corner basis onto the lap it was built from);
      2. it is the ONLY lap past 0.5 % of line-length drift, at 0.9-1.1 %, and it has a real warp
         (`lap_alignment` is not None) rather than the normalized projection;
      3. exactly one interior corner boundary on it has no spatial match, so its warp INTERPOLATES
         a knot — the case a bare normalized scale cannot reproduce;
      4. the two projections actually SEPARATE: the gated window and the un-gated
         `lap_total/corner_dist_total` scale sit >= 1 m apart at some corner edge. This is the
         magnitude of the defect #289 fixed (6.5 m on the D24 0060 pair) and the reason a coaching
         window defect can move a leaf here;
      5. coaching runs (`enough`) on the median lap and its rows carry the window-sensitive
         evidence (brake/coast extra seconds) that the moved window feeds."""
    s = _build_drift_median()
    ids = s.valid_lap_ids()
    best = s.best_lap_id()
    best_total = s.best_lap_total_distance()
    totals = {i: float(s._dist_cache[i][1][-1]) for i in ids}
    cons = s.consistency_lap_ids()
    med = coaching.median_lap_id(cons, [s.lap_time(i) for i in cons])
    assert med is not None and med != best, (
        f"median lap {med} is the best lap {best} — its window projection is the identity")

    drift = {i: corners.line_length_drift(totals[i], best_total) for i in ids}
    over = [i for i in ids if drift[i] > 0.005]
    assert over == [med], f"drift per lap {drift} — exactly the median lap {med} must be drifted"
    assert 0.009 <= drift[med] <= 0.011, f"median lap drift {drift[med]:.4%}"

    align = s.corners.lap_alignment(med, totals[med])
    assert align is not None, "the median lap kept the normalized projection — nothing to see"

    corner_list, corner_total = s.corners.basis()
    interior = [b for c in corner_list for b in (c.enter, c.exit) if 0 < b < best_total]
    ref_cols, med_cols = s._cols_cache[best], s._cols_cache[med]
    matched = corners._spatial_matches(np.asarray(interior), best_total,
                                       ref_cols[1], ref_cols[2], ref_cols[4],
                                       med_cols[1], med_cols[2], med_cols[4])
    unmatched = [b for b, m in zip(interior, matched, strict=True) if not np.isfinite(m)]
    assert len(unmatched) == 1, (
        f"{len(unmatched)} unmatched interior boundaries on the median lap (want exactly 1): "
        f"boundaries {interior}, matches {matched.tolist()}")

    # The gated window vs the bare normalized scale the un-gated projection used, on the SAME lap.
    traces = (ref_cols[1], ref_cols[2], ref_cols[4], med_cols[1], med_cols[2], med_cols[4])
    frame = [b for c in corner_list for b in (float(c.enter), float(c.exit))]
    scale = totals[med] / corner_total
    gaps = []
    for c in corner_list:
        w0, w1 = coaching._project_window(float(c.enter), float(c.exit), corner_total, totals[med],
                                          traces=traces, frame=frame, alignment=align)
        gaps += [abs(w0 - float(c.enter) * scale), abs(w1 - float(c.exit) * scale)]
    assert max(gaps) >= 1.0, (
        f"gated and normalized coaching windows are only {max(gaps):.3f} m apart — a window "
        f"defect of the size #289 fixed would not move a leaf of this phase")

    opp = s.coaching_opportunities()
    assert opp.enough and opp.median_lap_id == med, (opp.enough, opp.median_lap_id, med)
    assert any(r.reason.brake_extra_s > 0 or r.reason.coast_extra_s > 0 for r in opp.rows), (
        "no coaching row carries brake/coast evidence — the window feeds nothing measurable")

    for i, lap in enumerate(drift_median_laps()):
        sd = float(np.std(lap["cols"][3] - lap["clean_speed"]))
        assert abs(sd - DN_SPEED_SIGMA_MPS) <= 0.15 * DN_SPEED_SIGMA_MPS, f"lap {i} speed sd {sd}"
    print(f"ok median-drift fixture: coaching reads lap {med} at {drift[med]:.3%} drift, unmatched "
          f"boundary {unmatched[0]:.1f} m, window gap {max(gaps):.2f} m")


def test_drift_band_fixture_covers_the_sub_gate_band():
    """The band phase exists because the two phases above jump straight from 0.0000 % drift to
    0.995 %, leaving the (0 %, 0.5 %] band `corners.NORMALIZED_DRIFT_MAX` governed empty — so pin
    the properties that make a LADDER across it able to fail, each one a plausible tweak would
    quietly remove:

      1. the seeded best lap IS the fastest, and it is the UNDRIFTED reference line — the rungs are
         measured against a lap that is not one of them;
      2. every comparison lap sits strictly inside the band (0 < drift <= 0.005) and the rungs are
         SPREAD across it (lowest under 0.15 %, highest over 0.4 %), so a gate reintroduced anywhere
         at or above the lowest rung moves at least one lap of this phase — which a single rung
         could not promise;
      3. every comparison lap has a REAL warp (`lap_alignment` is not None): precisely what the old
         gate switched off for laps like these;
      4. every interior corner boundary MATCHES spatially on every lap, so this phase's warp is
         measured end to end — deliberately unlike `drift_noise` / `drift_median`, whose one
         unmatched boundary makes the warp interpolate a knot;
      5. warp and normalized projection SEPARATE by >= 1 m on every comparison lap. That is what
         makes the gate visible at all: line-length drift is a weak predictor of odometer
         misalignment (r = +0.38 on D24), so a lap that drifts in band but projects identically
         would pin nothing;
      6. the GPS speed column carries the family's measured noise."""
    s = _build_drift_band()
    ids = s.valid_lap_ids()
    times = [s.lap_time(i) for i in ids]
    assert ids[int(np.argmin(times))] == s.best_lap_id() == 0, (times, s.best_lap_id())

    best_total = s.best_lap_total_distance()
    totals = {i: float(s._dist_cache[i][1][-1]) for i in ids}
    drift = {i: corners.line_length_drift(totals[i], best_total) for i in ids}
    assert drift[0] == 0.0, f"the reference lap drifts {drift[0]:.4%} from itself"
    rungs = [drift[i] for i in ids if i != 0]
    assert all(0.0 < d <= 0.005 for d in rungs), (
        f"drift per lap {drift} — every rung must sit inside the (0, 0.5 %] band")
    assert min(rungs) < 0.0015 and max(rungs) > 0.004, (
        f"rungs {[f'{d:.4%}' for d in rungs]} do not span the band — a gate between them would be "
        f"invisible to this phase")

    corner_list, total_ref = s.corners.basis()
    frame = np.array([b for c in corner_list for b in (float(c.enter), float(c.exit))])
    interior = frame[(frame > 0) & (frame < total_ref)]
    ref_cols = s._cols_cache[s.best_lap_id()]
    seps = {}
    for i in (i for i in ids if i != 0):
        align = s.corners.lap_alignment(i, totals[i])
        assert align is not None, f"lap {i} kept the normalized projection — the gate's own case"
        lap_cols = s._cols_cache[i]
        matched = corners._spatial_matches(interior, total_ref, ref_cols[1], ref_cols[2],
                                           ref_cols[4], lap_cols[1], lap_cols[2], lap_cols[4])
        assert np.all(np.isfinite(matched)), (
            f"lap {i} has an unmatched interior boundary ({matched.tolist()}) — this phase's warp "
            f"is meant to be measured at every knot")
        warped = corners.project_boundaries(frame, total_ref, totals[i], frame=frame,
                                            alignment=align)
        seps[i] = float(np.max(np.abs(warped - frame * (totals[i] / total_ref))))
        assert seps[i] >= 1.0, (
            f"lap {i} projects only {seps[i]:.3f} m from the normalized frame — putting it back on "
            f"the normalized projection would barely move a leaf")

    for i, lap in enumerate(drift_band_laps()):
        sd = float(np.std(lap["cols"][3] - lap["clean_speed"]))
        assert abs(sd - DN_SPEED_SIGMA_MPS) <= 0.15 * DN_SPEED_SIGMA_MPS, f"lap {i} speed sd {sd}"
    print("ok drift-band ladder: " + ", ".join(
        f"lap {i} {drift[i]:.3%} drift ({seps[i]:.2f} m off normalized)" for i in seps))


def test_synthetic_fingerprint_matches_baseline():
    """The equivalence gate: the synthetic Session-math fingerprint must match the committed
    baseline within eps 1e-9. Any drift in a corner / driving / delta / consistency / bests leaf
    FAILS CI — an automated Session-refactor guard needing no big file."""
    assert os.path.exists(BASELINE), (
        f"missing baseline {BASELINE} — regenerate with "
        f"`python tests/test_golden_synthetic.py --write-baseline`")
    with open(BASELINE) as f:
        baseline = json.load(f)
    fp = synthetic_fingerprint()
    diffs, stats = _compare(baseline, fp)
    if diffs:
        msg = "\n".join("  " + d for d in diffs[:40])
        raise AssertionError(
            f"synthetic golden MISMATCH: {len(diffs)} differing leaves "
            f"(max |Δ|={stats['max']:g} at {stats['max_path']}):\n{msg}\n"
            f"If this is an INTENTIONAL Session-math change, regenerate the baseline with "
            f"`python tests/test_golden_synthetic.py --write-baseline` and review the diff.")
    print(f"ok baseline: {stats['n']} leaves match within eps {EPS} (max |Δ|={stats['max']:g})")


def _write_baseline():
    fp = synthetic_fingerprint()
    with open(BASELINE, "w") as f:
        json.dump(fp, f, sort_keys=True, indent=1)
        f.write("\n")
    print(f"wrote {BASELINE} ({_leaf_count(fp)} leaf values)")


if __name__ == "__main__":
    if "--write-baseline" in sys.argv:
        _write_baseline()
        sys.exit(0)
    tests = [
        test_synthetic_fingerprint_is_deterministic,
        test_reference_clear_reverts_to_base,
        test_drift_noise_fixture_reaches_the_paths_it_exists_for,
        test_drift_median_fixture_puts_the_drift_where_coaching_reads,
        test_drift_band_fixture_covers_the_sub_gate_band,
        test_synthetic_fingerprint_matches_baseline,
    ]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} SYNTHETIC-GOLDEN TESTS PASSED "
          f"({_leaf_count(synthetic_fingerprint())} fingerprint leaves)")
