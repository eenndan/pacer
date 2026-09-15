"""CI half of the Session equivalence gate — a SYNTHETIC golden regression (no big file needed).

The byte-identity gate that guards every Session refactor (F1 god-object decomposition, E2, the
#50 delta-engine dedup, ...) is the pair studio.dev.golden_session_dump.fingerprint (a dense
whole-public-API fingerprint of a Session's state) + studio.dev.golden_compare.walk (leaf-by-leaf
compare at eps 1e-9). But the canonical dump loads the real ~11.8 GB ``~/Desktop/D24`` recording,
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

from _synthetic import (  # noqa: E402  (the drift + noise fixture)
    DN_SPEED_SIGMA_MPS,
    drift_noise_laps,
    drift_noise_session,
)
from test_session_services import _synthetic_session  # noqa: E402  (the shared stadium fixture)

from studio import corners  # noqa: E402
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
      2. exactly one lap drifts past corners.NORMALIZED_DRIFT_MAX, at 0.9-1.1 % — below the gate
         the whole spatial projection is skipped;
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
    over = [i for i in ids if drift[i] > corners.NORMALIZED_DRIFT_MAX]
    assert over == [1], f"drift per lap {drift} — exactly lap 1 must be past the gate"
    assert 0.009 <= drift[1] <= 0.011, f"lap 1 drift {drift[1]:.4%}"

    interior = [b for c in s.corners.corner_list() for b in (c.enter, c.exit) if 0 < b < best_total]
    alignment = s.corners.lap_alignment(1, totals[1])
    assert alignment is not None, "lap 1 fell back to the normalized projection"
    unmatched = [b for b in interior if b not in set(alignment[0].tolist())]
    assert len(unmatched) == 1, (
        f"{len(unmatched)} unmatched interior boundaries on lap 1 (want exactly 1): "
        f"boundaries {interior}, warp knots {alignment[0].tolist()}")

    for i, lap in enumerate(drift_noise_laps()):
        sd = float(np.std(lap["cols"][3] - lap["clean_speed"]))
        assert abs(sd - DN_SPEED_SIGMA_MPS) <= 0.15 * DN_SPEED_SIGMA_MPS, f"lap {i} speed sd {sd}"

    donors = s.corners.segment_bests().donors
    assert any(d not in (None, 0) for d in donors), f"every segment donated by the best lap: {donors}"
    print(f"ok drift+noise fixture: lap 1 drift {drift[1]:.3%}, unmatched boundary "
          f"{unmatched[0]:.1f} m, segment donors {donors}")


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
        test_synthetic_fingerprint_matches_baseline,
    ]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} SYNTHETIC-GOLDEN TESTS PASSED "
          f"({_leaf_count(synthetic_fingerprint())} fingerprint leaves)")
