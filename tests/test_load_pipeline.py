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
import datetime
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from studio import data_quality, ingest, load, stats  # noqa: E402
from studio.dev.golden_compare import EPS, walk  # noqa: E402
from studio.dev.golden_session_dump import fingerprint  # noqa: E402
from studio.session import Session  # noqa: E402

FIXTURE = os.path.join(_REPO, "3rdparty", "gpmf-parser", "samples", "hero6.mp4")
BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "load_hero6_baseline.json")

# --- the GPS5-era wall-clock claim (see the test at the foot of this file) ---------------------
SAMPLES_DIR = os.path.dirname(FIXTURE)
# The nine GPS5-era clips this repo ships (HERO5-8, Max, Fusion)...
GPS5_ERA_CLIPS = ("Fusion.mp4", "hero5.mp4", "hero6.mp4", "hero6+ble.mp4", "hero6a.mp4",
                  "hero7.mp4", "hero8.mp4", "max-360mode.mp4", "max-heromode.mp4")
# ...and the one that carries no GPS stream at all — the sentinel's REAL trigger.
NO_GPS_CLIP = "karma.mp4"
# hero6's first KEPT fix, measured. Pinned as an EPOCH (timezone-independent); what it renders to
# is computed locally in the test, the same LOCAL convention session_date/clock_hhmm use.
HERO6_FIRST_FIX_MS = 1516822091654


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
    laps, cs, video_path, chapter_map, imu, track_name, tq, strip = load.load_recording([FIXTURE])
    assert laps.point_count() > 0, "cleaning dropped the whole trace"
    assert track_name is None, f"a bundled test clip is not a known track: {track_name}"
    assert tq.clock == "media_clock_fallback", f"GPS5-era clip must fall back to the media clock: {tq.clock}"
    assert float(tq.dropped_fraction) == 0.0, tq.dropped_fraction
    assert os.path.basename(video_path) == "hero6.mp4", video_path
    # THE HONEST DEGRADED STATE, on the one real GPS5-era recording this repo ships. The clip that
    # falls back to the media clock is also the clip that carries no per-sample fix type and no
    # DOP, so there is nothing to grade — and the quality strip must say so rather than paint a
    # confident green over it. Every covered cell is UNREPORTED and NOT ONE is GOOD.
    assert len(strip), "the strip must cover the recording even with nothing to grade"
    assert not strip.reports_quality, "a GPS5 clip reports no per-sample quality"
    counts = strip.counts()
    assert counts.get(data_quality.GOOD, 0) == 0, f"GPS5 must never grade as good: {counts}"
    assert counts.get(data_quality.UNREPORTED, 0) > 0, counts
    assert "no per-sample GPS quality" in strip.summary(), strip.summary()
    print(f"ok invariants: point_count={laps.point_count()}, clock={tq.clock}, track=None, "
          f"strip={ {data_quality.QUALITY_LABEL[k]: v for k, v in counts.items()} }")


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


def test_a_gps5_era_clip_is_rejected_by_spacing_and_still_publishes_a_wall_clock():
    """THE CLAIM THIS PINS, because four surfaces stated its opposite: a GPS5-era camera does NOT
    "report ts == 0". GPS5 carries no per-sample fix time, but pacer's ParseGPS5 stamps every fix
    in a payload with that payload's GPSU — one UTC stamp per ~1 s, repeated onto each fix inside
    it — so every fix is non-zero while the DISTINCT stamps are a small fraction of the fix count.

    Two halves, both with teeth:
      * `_used_gps9_trueclock` must keep rejecting these clips on SPACING. The "simplification"
        the old docstring invited, `any(ts > 0)`, promotes all nine to the GPS9 true clock; this
        fails on every one of them, because `any(ts > 0)` is True for all nine.
      * the wall-clock surfaces must PUBLISH that clock rather than dash it — Session.session_date
        and _wall_clock_ms, stats.clock_hhmm, and the share card's date (which is session_date).
        A "fix" that made them None on a media-clock recording fails here.

    The last block is the sentinel's real trigger: a recording with no GPS stream at all."""
    if not _have_fixture():
        return
    rows = []
    for name in GPS5_ERA_CLIPS:
        path = os.path.join(SAMPLES_DIR, name)
        if not os.path.exists(path):
            continue
        samples, _spans, _naive, _durations = ingest.read_gpmf([path])
        ts = [int(getattr(s, "timestamp_ms", 0)) for s in samples]
        n = len(ts)
        nonzero = sum(1 for t in ts if t > 0)
        distinct = len({t for t in ts if t > 0})
        assert n > 0, f"{name}: no GPS fixes read"
        assert nonzero == n, (
            f"{name}: the retired claim was that a GPS5-era clip reports 0 on every fix; measured "
            f"{nonzero}/{n} NON-zero. If this ever flips, the docstrings in session.py / stats.py "
            f"/ share_card.py that now say the opposite must move with it")
        assert distinct * 4 <= n, (
            f"{name}: {distinct} distinct stamps over {n} fixes — GPSU repeats ONE stamp per ~1 s "
            f"payload, so the distinct count must stay far below the fix count")
        # The whole point: `any(ts > 0)` says yes, and the SPACING rule still says no.
        assert any(t > 0 for t in ts), f"{name}: expected a stamped stream"
        assert not load._used_gps9_trueclock(samples), (
            f"{name}: a GPS5-era clip must stay on the media clock — it is the SPACING that "
            f"rejects it (0 s inside a payload, ~1.0 s across one), never a zero timestamp")
        rows.append((name, n, distinct))
    assert rows, "no GPS5-era sample clip present"

    # ...and those surfaces publish a real wall clock for one. hero6 is the committed fixture.
    session = Session.load([FIXTURE])
    assert session.timing_quality.clock == data_quality.MEDIA_CLOCK_FALLBACK, (
        session.timing_quality.clock)
    w0, w1 = session._wall_clock_ms()
    assert w0 == HERO6_FIRST_FIX_MS, (w0, HERO6_FIRST_FIX_MS)
    assert w1 > 0, w1
    expected_date = datetime.datetime.fromtimestamp(w0 / 1000.0).strftime("%Y-%m-%d")
    assert session.session_date() == expected_date, (session.session_date(), expected_date)
    assert stats.clock_hhmm(w0) is not None and stats.clock_hhmm(w1) is not None, (w0, w1)
    assert session._recording_identity() is not None, (
        "a GPS5-era clip IS identifiable — coarsely (the ~1 s GPSU), but it never fingerprints "
        "(0, 0, n)")

    # THE SENTINEL'S REAL TRIGGER: no GPS stream at all, which is a different camera fact.
    no_gps = os.path.join(SAMPLES_DIR, NO_GPS_CLIP)
    if os.path.exists(no_gps):
        blank = Session.load([no_gps])
        assert blank.timing_quality.clock == data_quality.NO_GPS_TRACE, blank.timing_quality.clock
        assert blank.session_date() is None, blank.session_date()
        assert stats.clock_hhmm(blank._wall_clock_ms()[0]) is None
        assert blank._recording_identity() is None
    print(f"ok gps5 wall clock: {rows}; hero6 -> date={expected_date} "
          f"clock={stats.clock_hhmm(w0)}-{stats.clock_hhmm(w1)}")


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
              test_load_pipeline_matches_baseline,
              test_a_gps5_era_clip_is_rejected_by_spacing_and_still_publishes_a_wall_clock):
        t()
    print("\nALL LOAD-PIPELINE TESTS PASSED")
