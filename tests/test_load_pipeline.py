"""End-to-end load-pipeline integration gate (test/load-pipeline-integration) on a committed fixture.

studio.load.load_recording is the single timing-critical orchestration entry point (read -> quality
gate -> clean -> GPS9 true-clock axis -> smooth -> segment -> start-line fit). Every helper is unit-
tested in isolation, but NOTHING ran the ASSEMBLED pipeline in CI: the synthetic golden gate builds
a bare Session with pre-seeded caches and never calls load_recording, and the E2E smoke asserts only
point_count>0 / valid_laps==0. This runs the real pipeline via Session.load on the committed
3rdparty/gpmf-parser/samples/hero6.mp4 and pins the WHOLE Session fingerprint to a committed baseline
(golden_compare.walk, eps 1e-9), plus two-run determinism and the media-clock invariants.

SCOPE: hero6 is a GPS5-era clip -> media-clock fallback (_used_gps9_trueclock == False), so this
pins the MEDIA-CLOCK path only. The GPS9 true-clock run-anchoring stays gated by the manual D24 dump
-- this COMPLEMENTS, it does NOT replace, that gate. Run:
    QT_QPA_PLATFORM=offscreen python tests/test_load_pipeline.py
Regenerate the baseline (only after an INTENTIONAL, reviewed load-pipeline change):
    python tests/test_load_pipeline.py --write-baseline
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from studio import load  # noqa: E402
from studio.dev.golden_compare import EPS, walk  # noqa: E402
from studio.dev.golden_session_dump import fingerprint  # noqa: E402
from studio.session import Session  # noqa: E402

FIXTURE = os.path.join(_REPO, "3rdparty", "gpmf-parser", "samples", "hero6.mp4")
BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "load_hero6_baseline.json")


def _have_fixture() -> bool:
    if os.path.exists(FIXTURE):
        return True
    print(f"skip: fixture {FIXTURE} not checked out (submodule)")
    return False


def _load_fingerprint() -> dict:
    """Fingerprint a Session built by the REAL load pipeline. strict=False: hero6 is a short test
    clip with 0 valid laps, so the valid-lap-only accessors record a sentinel while every reachable
    load-pipeline leaf (point/lap counts, trace, sectors, track, provenance) is pinned in full."""
    return fingerprint(Session.load([FIXTURE]), strict=False)


def _compare(a: dict, b: dict) -> tuple[list, dict]:
    diffs, stats = [], {"n": 0, "max": 0.0, "max_path": ""}
    walk(a, b, "root", diffs, stats)
    return diffs, stats


def test_load_pipeline_is_deterministic():
    """Two full Session.load runs of the same file must fingerprint identically — a flaky gate is
    worse than none, and non-determinism here would mean the load pipeline depends on wall-clock/RNG."""
    if not _have_fixture():
        return
    diffs, stats = _compare(_load_fingerprint(), _load_fingerprint())
    assert not diffs, f"load pipeline is non-deterministic: {diffs[:5]}"
    assert stats["max"] <= EPS, f"two loads drifted by {stats['max']:g} at {stats['max_path']}"
    print(f"ok determinism: two loads match ({stats['n']} leaves)")


def test_load_pipeline_media_clock_invariants():
    """The categorical invariants the numeric fingerprint doesn't name: cleaning kept a real trace,
    the clip is media-clock fallback (no GPS9 true clock), no fixes dropped, and it is not a known
    track. Drives load_recording directly for the timing_quality the Session wraps."""
    if not _have_fixture():
        return
    laps, cs, video_path, chapter_map, imu, track_name, tq = load.load_recording([FIXTURE])
    assert laps.point_count() > 0, "cleaning dropped the whole trace"
    assert track_name is None, f"a bundled test clip is not a known track: {track_name}"
    assert tq.clock == "media_clock_fallback", f"GPS5-era clip must fall back to the media clock: {tq.clock}"
    assert float(tq.dropped_fraction) == 0.0, tq.dropped_fraction
    assert os.path.basename(video_path) == "hero6.mp4", video_path
    print(f"ok invariants: point_count={laps.point_count()}, clock={tq.clock}, track=None")


def test_load_pipeline_matches_baseline():
    """The gate: the whole-pipeline Session fingerprint must match the committed baseline within
    eps 1e-9 — any drift in cleaning / smoothing / segmentation / start-line-fit FAILS CI."""
    if not _have_fixture():
        return
    assert os.path.exists(BASELINE), (
        f"missing baseline {BASELINE} — regenerate with "
        f"`python tests/test_load_pipeline.py --write-baseline`")
    with open(BASELINE) as f:
        baseline = json.load(f)
    diffs, stats = _compare(baseline, _load_fingerprint())
    if diffs:
        msg = "\n".join("  " + d for d in diffs[:40])
        raise AssertionError(
            f"load-pipeline golden MISMATCH: {len(diffs)} differing leaves "
            f"(max |Δ|={stats['max']:g} at {stats['max_path']}):\n{msg}\n"
            f"If this is an INTENTIONAL load-pipeline change, regenerate with "
            f"`python tests/test_load_pipeline.py --write-baseline` and review the diff.")
    print(f"ok baseline: {stats['n']} leaves match within eps {EPS}")


def _write_baseline():
    with open(BASELINE, "w") as f:
        json.dump(_load_fingerprint(), f, sort_keys=True, indent=1)
        f.write("\n")
    print(f"wrote {BASELINE}")


if __name__ == "__main__":
    if "--write-baseline" in sys.argv:
        _write_baseline()
        sys.exit(0)
    for t in (test_load_pipeline_is_deterministic,
              test_load_pipeline_media_clock_invariants,
              test_load_pipeline_matches_baseline):
        t()
    print("\nALL LOAD-PIPELINE TESTS PASSED")
