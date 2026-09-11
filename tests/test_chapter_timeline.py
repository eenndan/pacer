"""Tests for the CHAPTER TIME AXIS: the number a following chapter is placed at.

`tests/test_export_seam.py` already proves the export seeks the frame it asked for across a seam.
It cannot see this bug, and the reason is worth stating because it is the shape of the gap: that
file builds its `ChapterMap` from `ffprobe format=duration` — the CONTAINER's duration — and then
measures picture and audio against the very same clock it seeked with. Production builds the map
somewhere else entirely (`ingest.chain_sources` -> `GPMFSource`), and built it from the **GPMF
metadata track's** duration. A test whose fixture supplies the right answer by hand cannot fail on
the loader supplying the wrong one, however exactly it measures everything downstream of it.

The two tracks are not the same length. GoPro's own parser maintainer states the metadata length
matches the video length in every chapter *except the last*, where the GPMF track ends on its own
payload boundary — and every one of the ten GoPro clips committed in `3rdparty/gpmf-parser/samples`
is a single-chapter recording, so every one of them exercises that exception. Measured here:

    file               video (s)      GPMF (s)   GPMF - video
    hero7.mp4          12.712700     12.012000     -0.700700
    karma.mp4          12.078733     13.013000     +0.934266
    hero6a.mp4         11.678333     11.011000     -0.667334
    max-360mode.mp4    12.137125     11.678000     -0.459125
    hero8.mp4          12.645967     12.679000     +0.033033

So a chapter placed at the preceding chapter's GPMF length rides up to ~0.93 s of phantom offset —
telemetry, IMU and the chapter offset table all shifted together against a picture that did not
move. On the D24 fixtures the same measurement reads +0.000027 s on all three NON-last chapters
(the exception is the last chapter's, exactly as documented), which is why the defect has never
shown on the footage this app was built against and why it needed a fixture that exercises it.

Every assertion below is written against API that predates the fix (`chain_sources(...)[2]`, the
payload spans, `ChapterMap`), so this file fails on the old tree rather than erroring on a missing
method.

Run: python tests/test_chapter_timeline.py
"""
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from studio import chapters as chmod  # noqa: E402
from studio import ingest  # noqa: E402

_SAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "3rdparty", "gpmf-parser", "samples")

# The pair the seam is measured on: hero7's GPMF track is 0.700700 s SHORTER than its video and
# karma's is 0.934266 s LONGER, so a chain built on either quantity is unambiguously distinguishable
# and the sign of the error is covered both ways.
CH0, CH1 = "hero7.mp4", "karma.mp4"


def _in_pixi_env() -> bool:
    return os.path.join(".pixi", "envs") in os.environ.get("CONDA_PREFIX", "")


def _require_sample(name: str) -> str:
    """A committed gpmf-parser sample, which CI checks out with `submodules: recursive`. Missing
    is a LOUD failure, not a skip — the same rule test_ingest_equivalence.py applies."""
    path = os.path.join(_SAMPLES, name)
    assert os.path.exists(path), (
        f"{name}: required gpmf-parser submodule sample missing at {path} — "
        "run `git submodule update --init 3rdparty/gpmf-parser` (CI checks out submodules)")
    return path


def _ffprobe_video_duration(path: str) -> float | None:
    """The VIDEO track's own duration, straight off the container, in exact rational form.

    ffprobe is the INDEPENDENT witness here: it is a different parser family from gpmf-parser, so
    agreement between them is evidence about the file rather than about one library's arithmetic.
    Returns None when ffprobe is absent outside the pixi env (inside it, it is a locked dependency
    and its absence fails loudly)."""
    exe = shutil.which("ffprobe")
    if exe is None:
        assert not _in_pixi_env(), (
            "ffprobe not found on PATH inside the pixi env, where it is a LOCKED dependency "
            "(pyproject.toml) — this chapter-axis test must run in CI, not skip.")
        return None
    out = subprocess.run(
        [exe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=duration_ts,time_base", "-of", "json", path],
        check=True, capture_output=True, text=True).stdout
    stream = json.loads(out)["streams"][0]
    num, _, den = str(stream["time_base"]).partition("/")
    return int(stream["duration_ts"]) * float(num) / float(den)


def test_the_chapter_durations_the_loader_builds_are_the_video_tracks():
    """THE REGRESSION. `chain_sources` hands `ChapterMap` one number per chapter, and that number
    is what every later chapter is offset by and what the export's concat span declares. It must
    be the VIDEO track's duration, to the tick, on real GoPro media from several camera
    generations — not the GPMF track's, which is a different length in all ten sample clips."""
    truths, gpmf_gaps = [], []
    for name in sorted(os.listdir(_SAMPLES)):
        if not name.lower().endswith(".mp4"):
            continue
        path = os.path.join(_SAMPLES, name)
        truth = _ffprobe_video_duration(path)
        if truth is None:
            print("skip test_the_chapter_durations_the_loader_builds_are_the_video_tracks "
                  "(no ffprobe; not in the pixi env)")
            return
        _head, _owners, durations, meta = ingest.chain_sources([path])
        assert abs(durations[0] - truth) < 1e-9, (
            f"{name}: the loader's chapter duration is {durations[0]:.6f} s against the video "
            f"track's own {truth:.6f} s — {durations[0] - truth:+.6f} s")
        truths.append(name)
        gpmf_gaps.append((name, meta[0] - truth))
    assert truths, "no sample clips found — nothing was verified"
    # …and the test is not vacuous: on this fixture set the GPMF track really is a different
    # length, by most of a second, so "video duration" and "GPMF duration" cannot be confused for
    # each other by a passing assertion.
    worst = max(gpmf_gaps, key=lambda t: abs(t[1]))
    assert abs(worst[1]) > 0.5, (
        f"the sample set no longer distinguishes the two tracks (worst gap {worst[1]:+.6f} s on "
        f"{worst[0]}) — this test would pass on either quantity")
    print(f"test_the_chapter_durations_the_loader_builds_are_the_video_tracks OK "
          f"({len(truths)} clips exact; GPMF differs by up to {worst[1]:+.6f} s on {worst[0]})")


def test_the_chain_places_the_next_chapter_where_the_picture_ends():
    """The same number, seen from the OTHER side: the C++ `SequentialGPSSource` shifts chapter
    k+1's payload times, and the shift has to be the same quantity `ChapterMap` accumulates or the
    telemetry and the picture are on two different axes.

    Measured on the payload spans themselves — the first span after the break is chapter 1's first
    payload, and where it lands IS the shift. On the old behaviour it landed at hero7's GPMF length
    (12.012000 s); the picture it is stamped on runs to 12.712700 s."""
    p0, p1 = _require_sample(CH0), _require_sample(CH1)
    video0 = _ffprobe_video_duration(p0)
    if video0 is None:
        print("skip test_the_chain_places_the_next_chapter_where_the_picture_ends (no ffprobe)")
        return
    head, _owners, durations, meta = ingest.chain_sources([p0, p1])
    spans = []
    head.seek(0)
    while not head.is_end():
        spans.append(head.current_time_span())
        head.next()
    assert len(spans) > 4, f"expected a walkable chain, got {len(spans)} payload spans"

    # The first payload whose start is not the previous payload's end is chapter 1's first.
    seam = next((i for i in range(1, len(spans)) if abs(spans[i][0] - spans[i - 1][1]) > 1e-9),
                None)
    assert seam is not None, "the chained spans never hand over — no second chapter was walked"
    got = spans[seam][0]
    assert abs(got - video0) < 1e-9, (
        f"chapter 1's telemetry starts at {got:.6f} s; chapter 0's picture ends at "
        f"{video0:.6f} s — {got - video0:+.6f} s of phantom seam (its GPMF track ends at "
        f"{meta[0]:.6f} s, which is where the old chain put it)")

    # …and the offset table the video layer seeks with agrees with the shift the telemetry got.
    cmap = chmod.ChapterMap([p0, p1], durations, meta)
    assert abs(cmap.chapters[1].offset - got) < 1e-9, (
        f"the chapter offset table puts chapter 1 at {cmap.chapters[1].offset:.6f} s while its "
        f"telemetry landed at {got:.6f} s — the two axes disagree by "
        f"{cmap.chapters[1].offset - got:+.6f} s")
    print(f"test_the_chain_places_the_next_chapter_where_the_picture_ends OK "
          f"(seam at {got:.6f} s = the video track; the GPMF track ends {meta[0] - video0:+.6f} s "
          "from it)")


def test_a_chapter_whose_telemetry_does_not_cover_its_video_is_reported():
    """The load-time invariant, on the map: a NON-last chapter whose GPMF track misses its video
    length by more than one payload is the real camera fault, and it is named. The LAST chapter is
    exempt because GoPro's contract exempts it — flagging it would fire on every recording."""
    tol = chmod.CHAPTER_SYNC_TOLERANCE_S
    # in sync: the difference is well inside one payload (the measured D24 case is +0.000027 s)
    clean = chmod.ChapterMap(["/v/GX010001.MP4", "/v/GX020001.MP4", "/v/GX030001.MP4"],
                             [100.0, 100.0, 100.0], [100.000027, 100.0, 95.0])
    assert clean.desynced_chapters() == [], clean.desynced_chapters()
    assert chmod.desync_notice(clean) is None, "a last-chapter-only difference must not be flagged"

    # a genuinely short SECOND chapter: 4.5 s of telemetry missing, everything after it is adrift
    bad = chmod.ChapterMap(["/v/GX010001.MP4", "/v/GX020001.MP4", "/v/GX030001.MP4"],
                           [100.0, 100.0, 100.0], [100.0, 95.5, 40.0])
    assert [os.path.basename(p) for p, _ in bad.desynced_chapters()] == ["GX020001.MP4"]
    assert abs(bad.desynced_chapters()[0][1] + 4.5) < 1e-9
    notice = chmod.desync_notice(bad)
    assert notice and "GX020001.MP4" in notice and "4.5 s" in notice, notice
    assert "GX030001.MP4" not in notice, "the last chapter must never appear in the notice"

    # the threshold is a payload wide and is applied to the magnitude, both signs
    for delta in (tol + 0.01, -(tol + 0.01)):
        m = chmod.ChapterMap(["/a.MP4", "/b.MP4"], [50.0, 50.0], [50.0 + delta, 50.0])
        assert len(m.desynced_chapters()) == 1, delta
    for delta in (tol - 0.01, -(tol - 0.01)):
        m = chmod.ChapterMap(["/a.MP4", "/b.MP4"], [50.0, 50.0], [50.0 + delta, 50.0])
        assert m.desynced_chapters() == [], delta

    # a map built without GPMF durations (every duck-typed caller and test) reports nothing
    assert chmod.ChapterMap(["/a.MP4", "/b.MP4"], [50.0, 50.0]).desynced_chapters() == []
    # a single-chapter recording has no non-last chapter and so cannot be flagged
    assert chmod.ChapterMap(["/a.MP4"], [50.0], [40.0]).desynced_chapters() == []
    print("test_a_chapter_whose_telemetry_does_not_cover_its_video_is_reported OK")


def test_a_desynced_real_chapter_is_reported_through_the_loaders_own_map():
    """End to end on real media, through the numbers the loader itself produces: chaining hero7
    (GPMF 0.700700 s short of its video) in front of karma builds a two-chapter map whose FIRST
    chapter is in sync — 0.70 s is less than one payload — while a hypothetical chapter missing
    several payloads is not. The point of doing it on real media is that the map is constructed
    exactly as `load_recording` constructs it, from `chain_sources`, not from hand-written
    numbers."""
    p0, p1 = _require_sample(CH0), _require_sample(CH1)
    _head, _owners, durations, meta = ingest.chain_sources([p0, p1])
    cmap = chmod.ChapterMap([p0, p1], durations, meta)
    assert cmap.desynced_chapters() == [], (
        "hero7's GPMF track is 0.7007 s short of its video, which is inside one payload and must "
        f"not be reported: {cmap.desynced_chapters()}")
    assert abs(cmap.total_duration - (durations[0] + durations[1])) < 1e-9
    # every chapter's own duration is its video duration, and the map is built on those
    assert [c.duration for c in cmap.chapters] == list(durations)
    assert [c.meta_duration for c in cmap.chapters] == list(meta)
    print("test_a_desynced_real_chapter_is_reported_through_the_loaders_own_map OK "
          f"(total {cmap.total_duration:.6f} s from video tracks, "
          f"{sum(meta) - sum(durations):+.6f} s from the GPMF tracks)")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok  {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"FAIL {t.__name__}: {exc}")
    if failed:
        print(f"\n{failed}/{len(tests)} chapter-timeline tests FAILED")
        sys.exit(1)
    print(f"\nALL {len(tests)} chapter-timeline tests passed")
