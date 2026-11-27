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
-- this COMPLEMENTS, it does NOT replace, that gate. The two tests at the foot read all nine bundled
GPS5-era clips: their wall clock, and the bound the media-clock warning quotes against what that
clock measurably costs a lap on each (data_quality.MEDIA_CLOCK_LAP_ERROR). Run:
    QT_QPA_PLATFORM=offscreen python tests/test_load_pipeline.py
Regenerate the baseline (only after an INTENTIONAL, reviewed load-pipeline change):
    python tests/test_load_pipeline.py --write-baseline
"""
import datetime
import json
import os
import re
import sys

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

import _samples  # noqa: E402

from studio import data_quality, ingest, load, stats  # noqa: E402
from studio.dev.golden_compare import EPS, walk  # noqa: E402
from studio.dev.golden_session_dump import fingerprint  # noqa: E402
from studio.session import Session  # noqa: E402

# The committed clip, which CI checks out with the submodule. Missing fails the file here, by name:
# each test used to print "skip" and return, and the file PASSED having loaded nothing.
FIXTURE = _samples.require("hero6.mp4")
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
    diffs, stats = _compare(_load_fingerprint(), _load_fingerprint())
    assert not diffs, f"load pipeline is non-deterministic: {diffs[:5]}"
    assert stats["max"] <= EPS, f"two loads drifted by {stats['max']:g} at {stats['max_path']}"
    print(f"ok determinism: two loads match ({stats['n']} leaves)")


def test_load_pipeline_media_clock_invariants():
    """The categorical invariants the numeric fingerprint doesn't name: cleaning kept a real trace,
    the clip is media-clock fallback (no GPS9 true clock), no fixes dropped, and it is not a known
    track. Drives load_recording directly for the timing_quality the Session wraps."""
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


def test_the_media_clock_bound_covers_every_gps5_era_clip():
    """THE BOUND IS MEASURED ON THE CAMERAS IT IS SHOWN FOR (QA3C-CLOCK-BOUND, qa-w3c EVAL-1).

    Every clip above loads on the media-clock fallback, and the app tells it "a lap may read
    <data_quality.MEDIA_CLOCK_LAP_ERROR> off". Its times are `ingest`'s packet-spread clock: each
    ~1 s payload's 17-20 fixes spread evenly over the payload's media span. A receiver delivers its
    fixes on a fixed-rate grid, so with none dropped the true time of fix k is t0 + k/r, and the
    residual of that line fitted to the clock's times is how far the clock put each fix from it
    (`studio/dev/probes/p20_packet_spread.py` checks the method against each payload's GPSU stamp,
    and against a GPS9 chapter's own per-fix stamps). A lap time is the difference of two instants,
    so the most the clock can cost a lap inside a clip is that residual's peak to peak.

    The bound said "up to ~0.1 s" while hero7.mp4 spreads 138 ms: it must cover every clip, and
    data_quality's rationale must quote the widest spread as measured here."""
    bound = re.fullmatch(r"up to ~(\d+(?:\.\d+)?) s", data_quality.MEDIA_CLOCK_LAP_ERROR)
    assert bound, f"the bound is no longer 'up to ~X s': {data_quality.MEDIA_CLOCK_LAP_ERROR!r}"
    bound_ms = float(bound.group(1)) * 1e3
    spread = {}
    for name in GPS5_ERA_CLIPS:
        samples, _spans, naive, _durations = ingest.read_gpmf([os.path.join(SAMPLES_DIR, name)])
        assert not load._used_gps9_trueclock(samples), f"{name}: expected the media-clock fallback"
        t = np.asarray(naive, float)
        k = np.arange(len(t), dtype=float)
        spread[name] = float(np.ptp(t - np.polyval(np.polyfit(k, t, 1), k))) * 1e3
    widest = max(spread, key=spread.__getitem__)
    table = ", ".join(f"{n} {ms:.0f} ms" for n, ms in spread.items())
    assert spread[widest] <= bound_ms, (
        f"the packet-spread clock moves a fix {spread[widest]:.0f} ms peak to peak on {widest}, so a "
        f"lap there can read further off than the {data_quality.MEDIA_CLOCK_LAP_ERROR!r} the app "
        f"shows it ({table})")
    source = open(data_quality.__file__, encoding="utf-8").read()
    quoted = re.search(r"(\d+) ms peak to peak \((\S+\.mp4)", " ".join(source.replace("#", " ").split()))
    assert quoted and (int(quoted.group(1)), quoted.group(2)) == (round(spread[widest]), widest), (
        f"data_quality.py's rationale quotes {quoted.group(0) if quoted else 'no spread'!r}; measured "
        f"{spread[widest]:.0f} ms peak to peak ({widest}): {table}")
    print(f"ok media-clock bound: {data_quality.MEDIA_CLOCK_LAP_ERROR!r} covers {table}")


def _load_with_2d_fixes(paths, bad):
    """`Session.load(paths)` through the real loader, with every raw fix whose naive (media) time
    `bad(t)` holds turned into a 2D fix (fix 2, DOP 3, its true position): the quality gate rejects
    it, exactly as it rejects a receiver still acquiring. Nothing else about the recording moves."""
    import pacer

    real = load.read_recording

    def read_recording(p):
        out = real(p)
        samples = [s if not bad(t) else pacer.GPSSample(
            lat=s.lat, lon=s.lon, altitude=s.altitude, full_speed=s.full_speed,
            ground_speed=s.ground_speed, timestamp_ms=s.timestamp_ms, dop=3.0, fix=2)
            for s, t in zip(out[0], out[2], strict=True)]
        return (samples, *out[1:])

    load.read_recording = read_recording
    try:
        return Session.load(paths)
    finally:
        load.read_recording = real


def test_the_gps_verdict_is_judged_over_the_timed_laps():
    """THE GPS VERDICT IS ABOUT THE LAPS, so fixes rejected where no lap is timed do not decide it.

    It was the share of every MOVING fix, so a receiver that held only a 2D fix through the grid
    and the out-lap downgraded the whole session: on the synthetic demo circuit with 164 s of 2D
    fixes it read "GPS quality low (20% of fixes rejected)", decided no PB, focus list or debrief
    and de-emphasised 11 lap times that each equalled the clean recording's to the millisecond.
    The share is now taken from the first start/finish crossing to the last.

    Both halves on one real recording through the real loader: 2D fixes until just before the
    second crossing (the first lap is lost — it has no crossing to start from — and the session
    notice says so), then 2D fixes for 30 s inside the timed laps, which must still downgrade."""
    import tempfile

    from studio.dev import make_demo as md
    from studio.dev import synth_gopro as sg
    from studio.dev._jail import divert_app_support

    divert_app_support("pacer-test-quality-laps-")
    with tempfile.TemporaryDirectory(prefix="quality_laps_") as tmp:
        rec = sg.generate(os.path.join(tmp, "rec"), md.DEMO_SEED, 5, chapters=1,
                          origin=md.DEMO_ORIGIN)
        clean = Session.load(rec.paths)
        n = clean.lap_count()
        clean_times = [clean.lap_time(i) for i in range(n - 1)]
        # The naive clock runs ~0.5 s from the telemetry axis (the GPS lag): 3 s of margin, clear
        # of that and of the edge guard band (the test below works inside it).
        second = clean.laps.start_timestamp(1)
        out = _load_with_2d_fixes(rec.paths, lambda t: t < second - 3.0)
        q = out.timing_quality
        assert q.dropped_fraction == 0.0 and not q.degraded, (q.dropped_fraction, q.summary())
        assert q.in_laps, "five timed laps: the share is the laps' own"
        assert q._moving_fraction >= data_quality.DROPPED_FIX_CONCERN_FRAC, (
            f"no teeth: the whole drive's share {q._moving_fraction:.3f} would not have downgraded")
        got = [out.lap_time(i) for i in range(out.lap_count() - 1)]
        assert len(got) == len(clean_times) - 1, (got, clean_times)
        assert max(abs(a - b) for a, b in zip(got, clean_times[1:], strict=True)) < 1e-6, (
            got, clean_times)
        assert "s of driving outside your laps, so a lap there may be missing" in q.outside_laps(), (
            q.outside_laps())
        # A start-line edit moves the laps' ends, and the verdict is judged again (same line here).
        out.set_timing_lines(out.start_line, out.sector_lines, user_confirm=False)
        assert out.timing_quality == q, out.timing_quality

        # Inside the laps: 30 s of the third lap's span onwards, rejected — still GPS quality low,
        # in plain words, at a share over the laps above the whole drive's.
        third = clean.laps.start_timestamp(2)
        inside = _load_with_2d_fixes(rec.paths, lambda t: third + 5.0 < t < third + 35.0)
        qi = inside.timing_quality
        assert qi.in_laps and qi.low_gps_quality, (qi.dropped_fraction, qi.summary())
        assert qi.dropped_fraction > qi._moving_fraction, (qi.dropped_fraction, qi._moving_fraction)
        assert f"({qi.dropped_pct()}% of fixes inside your laps rejected)" in qi.untrusted(), (
            qi.untrusted())
        assert qi.outside_laps() == "", qi.outside_laps()
    print(f"ok verdict over the timed laps: outside {q._moving_fraction:.1%} of the drive -> "
          f"{q.dropped_fraction:.0%}; inside -> {qi.dropped_fraction:.1%}")


def test_rejected_fixes_at_the_edge_of_the_laps_keep_the_whole_drive_verdict():
    """REVIEW OF #709: a stretch of rejected fixes that ENDS just before the first counted crossing,
    or STARTS just after the last, reaches that crossing through the smoother (its 13-fix window
    averages one-sidedly at a run's edge): on the demo circuit the first timed lap read +282 ms at
    0.35-0.40 s of margin, +180 ms at 0.45-0.50 s, exact only from 0.65 s; the last, +216 ms at
    0.40-0.45 s. Judged over the laps alone that session read clean. Inside the guard band
    (`data_quality.EDGE_GUARD_FIXES` fixes beyond either edge) the whole drive decides instead, so
    it stays "GPS quality low"; 3 s of margin is clear of the smoother and is cleared."""
    import tempfile

    from studio.dev import make_demo as md
    from studio.dev import synth_gopro as sg
    from studio.dev._jail import divert_app_support

    divert_app_support("pacer-test-quality-edge-")
    with tempfile.TemporaryDirectory(prefix="quality_edge_") as tmp:
        rec = sg.generate(os.path.join(tmp, "rec"), md.DEMO_SEED, 5, chapters=1,
                          origin=md.DEMO_ORIGIN)
        clean = Session.load(rec.paths)
        n = clean.lap_count()
        # The bad stretches are cut on the NAIVE clock the samples carry; the crossings are on the
        # telemetry axis. Map each crossing back through the clean load's own kept fixes.
        seen = {}
        real = load._gps9_times

        def times(samples, naive):
            t = real(samples, naive)
            seen["t"], seen["n"] = np.asarray(t, float), np.asarray(naive, float)
            return t

        load._gps9_times = times
        try:
            Session.load(rec.paths)
        finally:
            load._gps9_times = real
        second, last = np.interp([clean.laps.start_timestamp(1), clean.laps.start_timestamp(n - 1)],
                                 seen["t"], seen["n"])
        for m in (0.4, 0.5):
            before = _load_with_2d_fixes(rec.paths, lambda t, m=m: t < second - m).timing_quality
            after = _load_with_2d_fixes(rec.paths, lambda t, m=m: t > last + m).timing_quality
            for where, q in (("before the first", before), ("after the last", after)):
                assert q.low_gps_quality and not q.in_laps, (
                    f"rejected fixes {m} s {where} crossing reach it through the smoother, yet the "
                    f"session read {q.dropped_pct()}% ({q.summary() or 'clean'})")
        for t_bad in (lambda t: t < second - 3.0, lambda t: t > last + 3.0):
            q = _load_with_2d_fixes(rec.paths, t_bad).timing_quality
            assert q.in_laps and not q.degraded, (q.dropped_pct(), q.summary())
    print(f"ok edge band: {data_quality.EDGE_GUARD_FIXES} fixes either side keep the whole drive")


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
              test_a_gps5_era_clip_is_rejected_by_spacing_and_still_publishes_a_wall_clock,
              test_the_media_clock_bound_covers_every_gps5_era_clip,
              test_the_gps_verdict_is_judged_over_the_timed_laps,
              test_rejected_fixes_at_the_edge_of_the_laps_keep_the_whole_drive_verdict):
        t()
    print("\nALL LOAD-PIPELINE TESTS PASSED")
