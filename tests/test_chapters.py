"""Pure-Python tests for studio.chapters: the GoPro chaptered-filename parser, sibling
discovery/grouping, and the global<->chapter time mapping (ChapterMap).

No pacer build / telemetry file needed — chapters.py is pure filename parsing + offset
arithmetic. Discovery is tested against a tmp directory of touch-ed files (real os.listdir),
including a mix of recordings so the grouping never crosses recording numbers. Run:
    python tests/test_chapters.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from studio import chapters  # noqa: E402


# ----------------------------------------------------------------- name parsing
def test_parse_basic():
    info = chapters.parse_gopro_name("/some/dir/GX010060.MP4")
    assert info is not None
    assert info.prefix == "GX"
    assert info.chapter == 1
    assert info.recording == 60


def test_parse_chapter_and_recording():
    info = chapters.parse_gopro_name("GX030062.MP4")
    assert info.chapter == 3
    assert info.recording == 62


def test_parse_other_prefixes_and_case():
    assert chapters.parse_gopro_name("GH021234.MP4").prefix == "GH"
    assert chapters.parse_gopro_name("GP021234.MP4").prefix == "GP"
    # lowercase extension still parses
    assert chapters.parse_gopro_name("gx010060.mp4").recording == 60


def test_parse_non_gopro_returns_none():
    assert chapters.parse_gopro_name("hero6.mp4") is None
    assert chapters.parse_gopro_name("random.MP4") is None
    # too many digits in the numeric block -> not a valid chaptered name
    assert chapters.parse_gopro_name("GX0100600.MP4") is None
    assert chapters.parse_gopro_name("GX10060.MP4") is None  # only 5 digits


# ----------------------------------------------------------- sibling discovery
def _touch(d, name):
    open(os.path.join(d, name), "w").close()


def test_discover_groups_and_orders():
    with tempfile.TemporaryDirectory() as d:
        # Two recordings interleaved + noise. 0060 chapters out of order on disk.
        for n in ["GX030060.MP4", "GX010060.MP4", "GX020060.MP4",
                  "GX010062.MP4", "GX020062.MP4", "GX030062.MP4",
                  "notes.txt", "hero6.mp4"]:
            _touch(d, n)
        sibs = chapters.discover_siblings(os.path.join(d, "GX010060.MP4"))
        assert [os.path.basename(p) for p in sibs] == [
            "GX010060.MP4", "GX020060.MP4", "GX030060.MP4"]


def test_discover_never_mixes_recordings():
    with tempfile.TemporaryDirectory() as d:
        for n in ["GX010060.MP4", "GX020060.MP4", "GX030060.MP4",
                  "GX010062.MP4", "GX020062.MP4", "GX030062.MP4"]:
            _touch(d, n)
        sibs = chapters.discover_siblings(os.path.join(d, "GX020062.MP4"))
        assert [os.path.basename(p) for p in sibs] == [
            "GX010062.MP4", "GX020062.MP4", "GX030062.MP4"]
        assert all("0060" not in os.path.basename(p) for p in sibs)


def test_discover_single_chapter_no_siblings():
    with tempfile.TemporaryDirectory() as d:
        _touch(d, "GX010099.MP4")  # the only chapter of recording 0099
        sibs = chapters.discover_siblings(os.path.join(d, "GX010099.MP4"))
        assert [os.path.basename(p) for p in sibs] == ["GX010099.MP4"]


def test_discover_non_gopro_returns_self():
    with tempfile.TemporaryDirectory() as d:
        _touch(d, "hero6.mp4")
        p = os.path.join(d, "hero6.mp4")
        assert chapters.discover_siblings(p) == [p]


def test_discover_does_not_mix_prefixes():
    with tempfile.TemporaryDirectory() as d:
        # Same recording number but different prefix = a different stream — must not chain.
        for n in ["GX010060.MP4", "GX020060.MP4", "GH010060.MP4"]:
            _touch(d, n)
        sibs = chapters.discover_siblings(os.path.join(d, "GX010060.MP4"))
        assert [os.path.basename(p) for p in sibs] == ["GX010060.MP4", "GX020060.MP4"]


# ---------------------------------------------------------- order_chapters
def test_order_chapters_sorts_and_dedupes():
    # Out-of-order + a duplicate (the union of dropped chapters ∪ discovered siblings).
    out = chapters.order_chapters(
        ["/f/GX020060.MP4", "/f/GX010060.MP4", "/f/GX020060.MP4", "/f/GX030060.MP4"])
    assert [os.path.basename(p) for p in out] == [
        "GX010060.MP4", "GX020060.MP4", "GX030060.MP4"]


def test_order_chapters_non_gopro_kept():
    out = chapters.order_chapters(["/f/hero6.mp4"])
    assert out == ["/f/hero6.mp4"]


# --------------------------------------------------- grouping into recordings
def test_group_chapters_of_one_recording_together():
    # GX01/GX02 of recording 0060 (same folder) group into ONE recording, ordered by CC.
    groups = chapters.group_into_recordings(
        ["/foot/GX020060.MP4", "/foot/GX010060.MP4"])
    assert len(groups) == 1
    assert [os.path.basename(p) for p in groups[0]] == ["GX010060.MP4", "GX020060.MP4"]


def test_group_keeps_distinct_recordings_separate():
    # Two different recording numbers must stay as SEPARATE recordings (never merged onto one clock).
    groups = chapters.group_into_recordings(
        ["/foot/GX010060.MP4", "/foot/GX010062.MP4", "/foot/GX020060.MP4"])
    assert len(groups) == 2
    bases = [[os.path.basename(p) for p in g] for g in groups]
    # First-appearance order: 0060 first (it appeared first), then 0062.
    assert bases == [["GX010060.MP4", "GX020060.MP4"], ["GX010062.MP4"]]


def test_group_separates_by_folder_and_prefix():
    # Same recording NUMBER but a different folder OR different prefix = a different recording.
    groups = chapters.group_into_recordings(
        ["/a/GX010060.MP4", "/b/GX010060.MP4", "/a/GH010060.MP4"])
    assert len(groups) == 3


def test_group_non_gopro_names_are_singletons():
    groups = chapters.group_into_recordings(
        ["/x/hero6.mp4", "/x/random.MP4", "/x/GX010060.MP4"])
    # Each non-GoPro name is its own single-file recording; the GoPro one is its own group.
    assert len(groups) == 3
    assert all(len(g) == 1 for g in groups)


def test_group_orders_chapters_by_cc():
    groups = chapters.group_into_recordings(
        ["/f/GX030060.MP4", "/f/GX010060.MP4", "/f/GX020060.MP4"])
    assert len(groups) == 1
    assert [os.path.basename(p) for p in groups[0]] == [
        "GX010060.MP4", "GX020060.MP4", "GX030060.MP4"]


def test_group_dedupes_repeated_path():
    groups = chapters.group_into_recordings(
        ["/f/GX010060.MP4", "/f/GX010060.MP4"])
    assert len(groups) == 1
    assert groups[0] == ["/f/GX010060.MP4"]


def test_group_empty_and_single():
    assert chapters.group_into_recordings([]) == []
    single = chapters.group_into_recordings(["/f/GX010060.MP4"])
    assert single == [["/f/GX010060.MP4"]]


# ------------------------------------------------------------------ the label
def test_recording_label():
    assert chapters.recording_label(["GX010060.MP4", "GX020060.MP4", "GX030060.MP4"]) == \
        "recording 0060 · 3 chapters"
    assert chapters.recording_label(["GX010060.MP4"]) == "recording 0060"
    assert chapters.recording_label(["hero6.mp4"]) == "hero6.mp4"
    assert chapters.recording_label([]) == ""


# --------------------------------------------------- global<->chapter mapping
def _map_0060():
    # The real recording-0060 per-chapter durations (seconds), from the GPMF media.
    return chapters.ChapterMap(
        ["GX010060.MP4", "GX020060.MP4", "GX030060.MP4"],
        [1729.728, 1729.728, 1195.26],
    )


def test_map_offsets_and_total():
    m = _map_0060()
    assert m.is_multi
    assert m.chapters[0].offset == 0.0
    assert abs(m.chapters[1].offset - 1729.728) < 1e-9
    assert abs(m.chapters[2].offset - (1729.728 * 2)) < 1e-9
    assert abs(m.total_duration - (1729.728 * 2 + 1195.26)) < 1e-9


def test_map_chapter_at_boundaries():
    m = _map_0060()
    assert m.chapter_at(0.0) == 0
    assert m.chapter_at(100.0) == 0
    assert m.chapter_at(1729.0) == 0          # just before the 1->2 seam
    assert m.chapter_at(1729.728) == 1        # exactly at the seam -> chapter 2
    assert m.chapter_at(1729.728 + 10) == 1
    assert m.chapter_at(1729.728 * 2 + 5) == 2  # into chapter 3
    # at/after the very end -> last chapter (so a seek to the end still resolves)
    assert m.chapter_at(m.total_duration + 100) == 2
    assert m.chapter_at(-5.0) == 0            # below 0 -> first chapter


def test_map_to_local_roundtrip():
    m = _map_0060()
    for g in [0.0, 500.0, 1729.0, 1729.728, 1800.0, 3459.456 + 1.0, m.total_duration]:
        i, local = m.to_local(g)
        # local is within the chosen chapter
        assert 0.0 <= local <= m.chapters[i].duration + 1e-9
        # round-trip back to global (except where clamped at the very end)
        back = m.to_global(i, local)
        if g <= m.total_duration:
            assert abs(back - min(g, m.total_duration)) < 1e-6


def test_map_local_clamped_into_chapter():
    m = _map_0060()
    # 10 s into chapter 2 (global = offset_1 + 10)
    i, local = m.to_local(1729.728 + 10.0)
    assert i == 1
    assert abs(local - 10.0) < 1e-9


def test_map_single_chapter_is_identity():
    m = chapters.ChapterMap(["GX010099.MP4"], [600.0])
    assert not m.is_multi
    i, local = m.to_local(123.4)
    assert i == 0
    assert abs(local - 123.4) < 1e-9
    assert abs(m.to_global(0, 123.4) - 123.4) < 1e-9


def test_map_unknown_duration_does_not_clamp_seek():
    # A lone file with an UNKNOWN media duration (0.0) must NOT clamp every seek to 0 — global
    # time should pass straight through as the local time (the single-file VideoView fallback).
    m = chapters.ChapterMap(["GX010099.MP4"], [0.0])
    i, local = m.to_local(457.8)
    assert i == 0
    assert abs(local - 457.8) < 1e-9


# ------------------------------------------- chapters missing from the folder
def _names(missing):
    return [(n, os.path.basename(p)) for n, p in missing]


def test_missing_chapters_names_the_chapters_before_the_last_one_on_disk():
    """A recording whose chapter 1 (or a middle chapter) is not in the folder used to open as
    whole, in silence: chapter_subset only compares against what IS on disk. Each missing chapter
    comes with its number and the name the camera would have written, in the opened file's folder;
    a missing LAST chapter cannot be told from the names, so none is invented."""
    with tempfile.TemporaryDirectory() as d:
        for n in ["GX020068.MP4", "GX030068.MP4", "GX050068.MP4", "GX010069.MP4", "GH010068.MP4"]:
            _touch(d, n)
        missing = chapters.missing_chapters([os.path.join(d, "GX030068.MP4")])
        assert _names(missing) == [(1, "GX010068.MP4"), (4, "GX040068.MP4")], missing
        assert all(os.path.dirname(p) == d for _, p in missing), missing
        # Whole, a lone chapter 1, a non-GoPro clip, an unreadable folder: nothing is missing.
        assert chapters.missing_chapters([os.path.join(d, "GX010069.MP4")]) == []
        assert chapters.missing_chapters([os.path.join(d, "hero6.mp4")]) == []
        assert chapters.missing_chapters([os.path.join(d, "nowhere", "GX020001.MP4")]) == []
        assert chapters.missing_chapters([]) == []


def test_missing_chapters_numbers_the_legacy_gopr_naming():
    """Before HERO6 the first chapter is GOPRnnnn.MP4 and GP01nnnn.MP4 is chapter 2: numbering
    GP02 as "chapter 2" would name the wrong chapter."""
    with tempfile.TemporaryDirectory() as d:
        _touch(d, "GP020070.MP4")
        missing = chapters.missing_chapters([os.path.join(d, "GP020070.MP4")])
        assert _names(missing) == [(1, "GOPR0070.MP4"), (2, "GP010070.MP4")], missing
        _touch(d, "GOPR0070.MP4")
        missing = chapters.missing_chapters([os.path.join(d, "GP020070.MP4")])
        assert _names(missing) == [(2, "GP010070.MP4")], missing


def test_gap_joins_names_the_chapters_the_chain_jumps_across():
    """Where the CHAINED files skip chapters — the clock's fact a lap's ⚠ is read from: the index
    of the chapter after the jump and the chapters missing from it, named as the camera would.
    Names only (no folder listing). Consecutive chapters, a lone file, a chain that starts late
    (missing FIRST chapters: nothing is chained across them), another recording's file and a
    non-GoPro clip are no gap; the legacy GOPR naming is numbered as `missing_chapters` numbers it."""
    j = chapters.gap_joins(["/r/GX010068.MP4", "/r/GX030068.MP4", "/r/GX040068.MP4",
                            "/r/GX070068.MP4"])
    assert [(k, _names(m)) for k, m in j] == [
        (1, [(2, "GX020068.MP4")]), (3, [(5, "GX050068.MP4"), (6, "GX060068.MP4")])], j
    assert chapters.missing_names(j[1][1]) == "chapters 5 and 6, GX050068.MP4, GX060068.MP4"
    assert all(os.path.dirname(p) == "/r" for _k, m in j for _n, p in m)
    for chain in (["/r/GX010068.MP4", "/r/GX020068.MP4"], ["/r/GX020068.MP4", "/r/GX030068.MP4"],
                  ["/r/GX010068.MP4"], [], ["/r/GX010068.MP4", "/r/GX030069.MP4"],
                  ["/r/hero6.mp4", "/r/GX030068.MP4"]):
        assert chapters.gap_joins(chain) == [], chain
    legacy = chapters.gap_joins(["/r/GOPR0070.MP4", "/r/GP020070.MP4"])
    assert [(k, _names(m)) for k, m in legacy] == [(1, [(2, "GP010070.MP4")])], legacy
    assert chapters.gap_joins(["/r/GOPR0070.MP4", "/r/GP010070.MP4"]) == []


def test_only_a_gap_makes_part_of_a_recording():
    """A recording that only lacks its first chapters is truncated, not wrong: each of its laps is
    timed right, so it stays a whole recording for the verdict (loop recording deletes early
    chapters). A gap in the middle times a lap across it, so that one is part of a recording."""
    with tempfile.TemporaryDirectory() as d:
        for n in ["GX020068.MP4", "GX030068.MP4", "GX010071.MP4", "GX030071.MP4"]:
            _touch(d, n)
        assert not chapters.part_of_recording([os.path.join(d, "GX020068.MP4"),
                                               os.path.join(d, "GX030068.MP4")])
        assert chapters.part_of_recording([os.path.join(d, "GX010071.MP4"),
                                           os.path.join(d, "GX030071.MP4")])


def _m(*pairs):
    return [(n, f"/d/{name}") for n, name in pairs]


def test_missing_notice_says_where_the_recording_starts_or_jumps():
    first = chapters.missing_notice(_m((1, "GX010068.MP4")))
    assert first == ("This recording starts at chapter 2: chapter 1 (GX010068.MP4) is not in the "
                     "folder, so the first minutes are missing"), first
    two = chapters.missing_notice(_m((1, "GX010068.MP4"), (2, "GX020068.MP4")))
    assert two == ("This recording starts at chapter 3: chapters 1 and 2 (GX010068.MP4, "
                   "GX020068.MP4) are not in the folder, so the first minutes are missing"), two
    middle = chapters.missing_notice(_m((2, "GX020068.MP4")))
    assert middle == ("chapter 2 (GX020068.MP4) is not in the folder, so the recording jumps "
                      "across it and a lap timed across the gap is wrong"), middle
    both = chapters.missing_notice(_m((1, "GX010068.MP4"), (3, "GX030068.MP4"), (5, "GX050068.MP4")))
    assert both == ("This recording starts at chapter 2 and skips chapters 3 and 5 (GX010068.MP4, "
                    "GX030068.MP4, GX050068.MP4 are not in the folder): a lap timed across a gap "
                    "is wrong"), both
    assert chapters.missing_notice([]) is None


def test_gap_wait_names_what_ends_the_wait():
    """The verdict waits for the GAP's chapters only: with them back, a recording that still lacks
    its first chapters is decided (it is whole for the verdict)."""
    assert chapters.gap_wait(_m((2, "GX020068.MP4"))) == \
        "; your PB and focus list wait until chapter 2 is in this folder"
    assert chapters.gap_wait(_m((1, "GX010068.MP4"), (3, "GX030068.MP4"), (5, "GX050068.MP4"))) \
        == "; your PB and focus list wait until chapters 3 and 5 are in this folder"
    assert chapters.gap_wait(_m((1, "GX010068.MP4"))) == ""
    assert chapters.gap_wait([]) == ""

def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} chapter tests passed")


if __name__ == "__main__":
    _run_all()
