"""Tests for MARKS — the one surface in this app that records a conclusion instead of a measurement.

Four halves:

  * THE STORE (``studio.marks``, no Qt): the schema round-trip and, at length, the PERSISTENCE
    DISCIPLINE. A mark is the most irreplaceable thing pacer holds — a library row comes back the
    moment the footage is re-opened, a tyre pressure at least existed on a gauge once, and "I was
    baulked there" existed only in the driver's head — so every corruption, version and destructive
    path is asserted rather than assumed: corrupt bytes preserved verbatim in the ``.bak`` before
    the first overwrite, a NEWER file's unknown fields surviving a v1 round-trip, an
    older/unstamped file migrated with every mark kept, one malformed mark dropped and the rest
    kept, the atomic write leaving no ``.tmp``, and delete / forget / clear each taking their copy
    first with ``restore`` swapping it back.
  * THE ANCHOR: that a mark stores a CHAPTER-RELATIVE time, and therefore means the same instant
    whether one chapter or the whole chaptered recording is open. This is the half a bare media
    time would have got wrong — the store is keyed by the chapter-invariant library fingerprint, so
    the two opens share one key while their global clocks differ by whole chapters.
  * THE HONESTY RULE: that every derived mark agrees EXACTLY with the surface that already reports
    the same fact. A lap carries a dropout mark iff ``Session.lap_has_dropout`` is True for it; the
    excluded marks are ``Session.excluded_lap_ids`` and nothing else; and no degraded mark is drawn
    over a second the quality strip grades GOOD.
  * THE SURFACES (offscreen Qt): the scrub bar's band placing its ink where the slider's own travel
    says the instant is, and the Marks page's filter / search / empty states / action gating.

CRITICAL: every test points the store at a TEMP directory (monkeypatching
``marks._app_support_dir``, the single seam) — the suite NEVER touches the user's real
``~/Library/Application Support/pacer/``.

Run: QT_QPA_PLATFORM=offscreen python tests/test_marks.py
"""
import json
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()            # module scope, BEFORE any widget: the shipping font stack

from studio import chapters, data_quality, gapfill, marks, theme  # noqa: E402


# --------------------------------------------------------------------------- fixtures
def _store_path(tmp: str) -> str:
    return os.path.join(tmp, "marks.json")


def _mark(chapter="GX010062", t=100.0, **kw) -> dict:
    return marks.new_mark(chapter, t, **kw)


def _chapter_map(*specs) -> chapters.ChapterMap:
    """A ChapterMap over (path, duration) pairs — the real class, so the anchor tests exercise the
    arithmetic the app actually converts through."""
    return chapters.ChapterMap([p for p, _ in specs], [d for _, d in specs])


def _timeline(classes, cell_s=1.0) -> data_quality.QualityTimeline:
    cls = np.asarray(classes, np.int8)
    n = len(cls)
    return data_quality.QualityTimeline(
        cell_s=cell_s, cls=cls, n=np.full(n, 10, np.int32), dropped=np.zeros(n, np.int32),
        dop=np.full(n, 1.5), reports_quality=True)


# ========================================================================= the store
def test_a_mark_round_trips_through_the_file():
    """Save then load returns what went in, field for field — the floor every other test stands on."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _store_path(tmp)
        m = _mark(t=42.5, t_end=48.0, type=marks.TYPE_TRAFFIC, colour="purple",
                  note="baulked out of 4")
        marks.save(marks.put(marks.empty_store(), "GX0062", m), path)
        back = marks.get(marks.load(path), "GX0062")
        assert len(back) == 1
        got = back[0]
        for key in ("id", "chapter", "t", "t_end", "type", "colour", "note", "created"):
            assert got[key] == m[key], f"{key}: {got[key]!r} != {m[key]!r}"
        assert got["kind"] == marks.KIND_MANUAL
    print("test_a_mark_round_trips_through_the_file OK")


def test_the_write_is_atomic_and_leaves_no_temp_file():
    with tempfile.TemporaryDirectory() as tmp:
        path = _store_path(tmp)
        marks.save(marks.put(marks.empty_store(), "GX0062", _mark()), path)
        assert not os.path.exists(path + ".tmp"), "the atomic write left its temp file behind"
        assert json.load(open(path))["version"] == marks.VERSION
    print("test_the_write_is_atomic_and_leaves_no_temp_file OK")


def test_a_newer_file_is_read_best_effort_and_its_unknown_fields_survive():
    """The rule that makes a downgrade non-destructive: a v1 build opening a v2 file must not be
    the reason a v2 field is destroyed, because a hand-typed note cannot be re-derived."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _store_path(tmp)
        future = {"version": marks.VERSION + 7,
                  "recordings": {"GX0062": {"marks": [
                      {"id": "abc", "chapter": "GX010062", "t": 12.0, "type": "note",
                       "colour": "amber", "note": "keep me",
                       "future_field": {"deep": [1, 2, 3]}}],
                      "future_recording_field": "kept"}}}
        with open(path, "w") as f:
            json.dump(future, f)
        store = marks.load(path)
        assert store["recordings"]["GX0062"]["marks"][0]["future_field"] == {"deep": [1, 2, 3]}
        assert store["recordings"]["GX0062"]["future_recording_field"] == "kept"
        marks.save(store, path)                       # the round trip through THIS build
        again = json.load(open(path))["recordings"]["GX0062"]
        assert again["marks"][0]["future_field"] == {"deep": [1, 2, 3]}, \
            "a v1 save destroyed a later schema's per-mark field"
        assert again["future_recording_field"] == "kept"
        # …and the bytes it could not round-trip were copied aside BEFORE that overwrite.
        assert json.load(open(marks.backup_path(path)))["version"] == marks.VERSION + 7
    print("test_a_newer_file_is_read_best_effort_and_its_unknown_fields_survive OK")


def test_an_older_or_unstamped_file_is_migrated_with_every_mark_kept():
    with tempfile.TemporaryDirectory() as tmp:
        path = _store_path(tmp)
        old = {"version": 0, "recordings": {"GX0062": {"marks": [
            {"id": "a", "chapter": "GX010062", "t": 1.0, "note": "one"},
            {"id": "b", "chapter": "GX010062", "t": 2.0, "note": "two"}]}}}
        with open(path, "w") as f:
            json.dump(old, f)
        store = marks.load(path)
        assert store["version"] == marks.VERSION
        assert [m["note"] for m in marks.get(store, "GX0062")] == ["one", "two"]
        # A migrated (healthy) file is rewritten normally — no backup churn.
        marks.save(store, path)
        assert not os.path.exists(marks.backup_path(path)), \
            "a healthy migrated file took a backup it did not need"
    print("test_an_older_or_unstamped_file_is_migrated_with_every_mark_kept OK")


def test_one_malformed_mark_is_dropped_and_the_rest_are_kept():
    """The prefs failure this store exists not to repeat: one bad row must not cost the file."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _store_path(tmp)
        with open(path, "w") as f:
            json.dump({"version": 1, "recordings": {"GX0062": {"marks": [
                {"id": "a", "chapter": "GX010062", "t": 1.0, "note": "kept"},
                "not a mark at all",
                {"chapter": "GX010062", "t": 2.0, "note": "no id"},
                {"id": "c", "t": 3.0, "note": "no chapter"},
                {"id": "d", "chapter": "GX010062", "t": "twelve", "note": "bad time"},
                {"id": "e", "chapter": "GX010062", "t": 5.0, "note": "also kept"}]}}}, f)
        kept = marks.get(marks.load(path), "GX0062")
        assert [m["note"] for m in kept] == ["kept", "also kept"]
    print("test_one_malformed_mark_is_dropped_and_the_rest_are_kept OK")


def test_genuine_corruption_falls_back_to_empty_and_the_bytes_are_backed_up_verbatim():
    """The corruption path, end to end: unreadable bytes read as an EMPTY store (never a crash,
    never a partial file), and the original bytes preserved VERBATIM in the .bak the first time a
    write would have overwritten them.

    EVERY payload here must be backed up, including the two that are perfectly valid JSON. Those
    two are the ones that found a real hole: a `version` of `"one"`, or a `recordings` that is a
    list, parses as a JSON object, fails `load`'s validation, reads back as an empty store — and,
    under a `_backup_unsafe` that only asked "unreadable, or newer?", was then overwritten with no
    copy taken at all. The condition is enumerated against `load`'s own fall-backs now."""
    for corrupt in (b"{ not json at all", b"[]", b'{"version": "one", "recordings": {}}',
                    b'{"version": 1, "recordings": []}', b"",
                    b'{"version": 99, "recordings": {"GX0062": {"marks": []}}}'):
        with tempfile.TemporaryDirectory() as tmp:
            path = _store_path(tmp)
            with open(path, "wb") as f:
                f.write(corrupt)
            if b"99" not in corrupt:     # the NEWER file is read best-effort, not as empty
                assert marks.load(path) == marks.empty_store(), \
                    f"{corrupt!r} did not read as empty"
            marks.save(marks.put(marks.empty_store(), "GX0062", _mark()), path)
            assert open(marks.backup_path(path), "rb").read() == corrupt, \
                f"the .bak for {corrupt!r} is not the original bytes"
            assert len(marks.get(marks.load(path), "GX0062")) == 1, "the new mark was not written"
    print("test_genuine_corruption_falls_back_to_empty_and_the_bytes_are_backed_up_verbatim OK")


def test_deleting_forgetting_and_clearing_each_take_a_copy_first_and_restore_swaps_it_back():
    with tempfile.TemporaryDirectory() as tmp:
        path = _store_path(tmp)
        a, b = _mark(t=1.0, note="first"), _mark(t=2.0, note="second")
        store = marks.put(marks.put(marks.empty_store(), "GX0062", a), "GX0062", b)
        marks.save(store, path)

        # DELETE one — backup first, restore puts it back.
        marks.remove_and_save("GX0062", a["id"], path)
        assert [m["note"] for m in marks.get(marks.load(path), "GX0062")] == ["second"]
        summary = marks.backup_summary(path)
        assert summary is not None and summary["marks"] == 2
        marks.restore(path)
        assert len(marks.get(marks.load(path), "GX0062")) == 2, "restore did not put the mark back"
        # …and the restore is itself reversible (the store it replaced became the new backup).
        assert marks.backup_summary(path)["marks"] == 1

        # A NO-OP delete must not churn the one backup slot out from under a real backup.
        marks.remove_and_save("GX0062", "no-such-id", path)
        assert marks.backup_summary(path)["marks"] == 1

        # FORGET the whole recording.
        marks.save(store, path)
        marks.forget_and_save("GX0062", path)
        assert marks.get(marks.load(path), "GX0062") == []
        assert marks.backup_summary(path)["marks"] == 2

        # CLEAR everything.
        marks.save(store, path)
        marks.clear(path)
        assert marks.count(marks.load(path)) == 0
        assert marks.backup_summary(path)["marks"] == 2
        marks.restore(path)
        assert marks.count(marks.load(path)) == 2
    print("test_deleting_forgetting_and_clearing_each_take_a_copy_first_and_restore_swaps_it_back "
          "OK")


def test_restore_refuses_an_empty_or_missing_backup():
    """Replacing a live notebook with nothing is the data loss restore exists to undo."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _store_path(tmp)
        marks.save(marks.put(marks.empty_store(), "GX0062", _mark()), path)
        assert marks.count(marks.restore(path)) == 1, "restore wiped a store with no backup"
        marks.save(marks.empty_store(), marks.backup_path(path))
        assert marks.count(marks.restore(path)) == 1, "restore honoured an EMPTY backup"
    print("test_restore_refuses_an_empty_or_missing_backup OK")


def test_a_hand_edited_auto_kind_is_forced_back_to_manual():
    """The file holds hand-authored marks only. A stored `kind` that claimed a mark was DERIVED
    would let an edited file present a detector's verdict that no detector ever produced — the
    two-surfaces-disagree defect the auto/manual split exists to make impossible."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _store_path(tmp)
        with open(path, "w") as f:
            json.dump({"version": 1, "recordings": {"GX0062": {"marks": [
                {"id": "a", "kind": marks.KIND_AUTO, "chapter": "GX010062", "t": 1.0,
                 "type": marks.TYPE_DROPOUT, "note": "I am not a detector"}]}}}, f)
        got = marks.get(marks.load(path), "GX0062")[0]
        assert got["kind"] == marks.KIND_MANUAL
        assert got["type"] == marks.TYPE_NOTE, "an AUTO type was admitted into the manual file"
    print("test_a_hand_edited_auto_kind_is_forced_back_to_manual OK")


def test_an_empty_recording_is_dropped_from_the_file_rather_than_written_as_a_shell():
    with tempfile.TemporaryDirectory() as tmp:
        path = _store_path(tmp)
        m = _mark()
        store = marks.put(marks.empty_store(), "GX0062", m)
        marks.remove(store, "GX0062", m["id"])
        marks.save(store, path)
        assert json.load(open(path))["recordings"] == {}
    print("test_an_empty_recording_is_dropped_from_the_file_rather_than_written_as_a_shell OK")


def test_a_degenerate_range_reads_as_a_moment_rather_than_costing_the_note():
    for bad_end in (None, 100.0, 50.0, float("nan"), -1.0, True):
        m = marks._norm_mark({"id": "a", "chapter": "c", "t": 100.0, "t_end": bad_end,
                              "note": "the words are the point"})
        assert m["t_end"] is None, f"t_end={bad_end!r} was kept as a range"
        assert m["note"] == "the words are the point"
    print("test_a_degenerate_range_reads_as_a_moment_rather_than_costing_the_note OK")


def test_put_replaces_on_the_id_so_an_edit_is_not_a_duplicate():
    m = _mark(note="before")
    store = marks.put(marks.empty_store(), "GX0062", m)
    marks.put(store, "GX0062", {**m, "note": "after"})
    got = marks.get(store, "GX0062")
    assert len(got) == 1 and got[0]["note"] == "after"
    print("test_put_replaces_on_the_id_so_an_edit_is_not_a_duplicate OK")


# ========================================================================= the anchor
def test_one_instant_has_one_anchor_however_the_recording_is_opened():
    """THE REASON A MARK IS NOT STORED AS A MEDIA TIME.

    `library.fingerprint` strips the chapter index, so a single-chapter open and a full chaptered
    open of one recording share ONE store key. Their global clocks do not: the same instant of
    chapter 2 is global 5 s opened alone and global 1735 s opened as the trio. A stored global
    second would therefore mean two different moments under one key."""
    full = _chapter_map(("/d/GX010062.MP4", 1730.0), ("/d/GX020062.MP4", 1730.0),
                        ("/d/GX030062.MP4", 1591.0))
    alone = _chapter_map(("/d/GX020062.MP4", 1730.0))

    anchor_full = marks.anchor_from_global(full, 1735.0)
    anchor_alone = marks.anchor_from_global(alone, 5.0)
    assert anchor_full == anchor_alone == ("GX020062", 5.0), (anchor_full, anchor_alone)

    # …and the anchor resolves back to each open's OWN global second.
    assert marks.global_from_anchor(full, *anchor_full) == 1735.0
    assert marks.global_from_anchor(alone, *anchor_alone) == 5.0

    # A RE-MEASURED chapter duration moves the global clock and must not move the mark: the anchor
    # is relative to its own chapter's start, so the mark stays on the same frame of the same file.
    remeasured = _chapter_map(("/d/GX010062.MP4", 1729.0), ("/d/GX020062.MP4", 1730.0),
                              ("/d/GX030062.MP4", 1591.0))
    assert marks.global_from_anchor(remeasured, *anchor_full) == 1734.0
    print("test_one_instant_has_one_anchor_however_the_recording_is_opened OK")


def test_a_mark_in_a_chapter_this_open_lacks_is_listed_but_never_placed():
    """Not relocated, not clamped, not dropped: a note about turn 4 of chapter 3 must not end up on
    the start/finish straight of chapter 1, and must not silently vanish either."""
    alone = _chapter_map(("/d/GX010062.MP4", 1730.0))
    stored = [marks._norm_mark({"id": "a", "chapter": "GX030062", "t": 900.0, "note": "elsewhere"}),
              marks._norm_mark({"id": "b", "chapter": "GX010062", "t": 100.0, "note": "here"})]
    got = marks.resolve(stored, alone)
    by_id = {m["id"]: m for m in got}
    assert by_id["a"]["placed"] is False and by_id["a"]["t"] is None
    assert by_id["b"]["placed"] is True and by_id["b"]["t"] == 100.0
    assert got[-1]["id"] == "a", "an unplaceable mark must sort last, not first"
    # A range keeps its LENGTH through the resolve, not its raw endpoints.
    ranged = marks.resolve(
        [marks._norm_mark({"id": "c", "chapter": "GX010062", "t": 10.0, "t_end": 25.0})], alone)
    assert ranged[0]["t_end"] - ranged[0]["t"] == 15.0
    print("test_a_mark_in_a_chapter_this_open_lacks_is_listed_but_never_placed OK")


def test_the_jump_keys_reach_only_placed_marks_and_never_stick_on_the_current_one():
    placed = [{"id": "a", "kind": marks.KIND_MANUAL, "t": 10.0, "placed": True},
              {"id": "b", "kind": marks.KIND_MANUAL, "t": 20.0, "placed": True},
              {"id": "c", "kind": marks.KIND_MANUAL, "t": None, "placed": False}]
    assert marks.neighbour(placed, 0.0, +1)["id"] == "a"
    assert marks.neighbour(placed, 10.0, +1)["id"] == "b", \
        "the jump stuck on the mark it was standing on"
    assert marks.neighbour(placed, 20.0, +1) is None
    assert marks.neighbour(placed, 20.0, -1)["id"] == "a"
    assert marks.neighbour(placed, 0.0, -1) is None
    assert marks.neighbour([placed[2]], 0.0, +1) is None, "an unplaceable mark was reachable"
    print("test_the_jump_keys_reach_only_placed_marks_and_never_stick_on_the_current_one OK")


# ================================================================== the honesty rule
def test_a_lap_carries_a_dropout_mark_exactly_when_the_app_says_it_has_one():
    """The rule this feature is held to: an auto mark asserts the app detected something, so it has
    to agree EXACTLY with the number the app already shows. Driven through the real Session method
    over a synthetic session with a hand-placed gap."""
    from test_session_services import _synthetic_session

    s = _synthetic_session()
    # Punch a 2 s hole into lap 1's kept-point times — the same thing a receiver dropout does.
    t1, x1, y1, sp1, c1 = s._cols_cache[1]
    holed = t1.copy()
    holed[len(holed) // 2:] += 2.0
    s._cols_cache[1] = (holed, x1, y1, sp1, c1)
    s._dist_cache = {}
    s._quality_timeline = data_quality.empty_timeline()
    s._excluded_cache = []

    auto, _suppressed = s.auto_marks()
    marked_laps = {m["lap"] for m in auto if m["type"] == marks.TYPE_DROPOUT}
    flagged = {lid for lid in s.valid_lap_ids() if s.lap_has_dropout(lid)}
    assert marked_laps == flagged == {1}, (marked_laps, flagged)
    assert marked_laps == s.dropout_lap_ids(), "the marks and dropout_lap_ids disagree"
    # …and the mark covers the gap the detector found, not an approximation of it.
    gap = gapfill.find_gaps(s._lap_point_times(1))[0]
    hit = next(m for m in auto if m["type"] == marks.TYPE_DROPOUT)
    assert (hit["t"], hit["t_end"]) == (holed[gap["i"]], holed[gap["j"]])
    print("test_a_lap_carries_a_dropout_mark_exactly_when_the_app_says_it_has_one OK")


def test_the_excluded_marks_are_the_excluded_laps_and_nothing_else():
    from test_session_services import _synthetic_session

    s = _synthetic_session()
    s._quality_timeline = data_quality.empty_timeline()
    s._excluded_cache = [1]
    s.lap_window = lambda lid: (300.0, 360.0) if lid == 1 else None
    auto, _ = s.auto_marks()
    excluded = [m for m in auto if m["type"] == marks.TYPE_EXCLUDED]
    assert [m["lap"] for m in excluded] == s.excluded_lap_ids() == [1]
    assert (excluded[0]["t"], excluded[0]["t_end"]) == (300.0, 360.0)
    print("test_the_excluded_marks_are_the_excluded_laps_and_nothing_else OK")


def test_no_degraded_mark_covers_a_second_the_quality_strip_grades_good():
    """A mark may not claim a stretch is degraded where the strip beside it paints GOOD, and it may
    not mark UNREPORTED either — a camera that writes no per-sample quality gives the app nothing to
    be degraded ABOUT."""
    G, M, P, U = (data_quality.GOOD, data_quality.MODERATE, data_quality.POOR,
                  data_quality.UNREPORTED)
    tl = _timeline([P] * 10 + [G] * 50 + [M] * 8 + [G] * 5 + [U] * 20)
    auto, suppressed = marks.auto_marks(timeline=tl)
    spans = [(m["t"], m["t_end"]) for m in auto if m["type"] == marks.TYPE_DEGRADED]
    assert spans == [(0.0, 10.0), (60.0, 68.0)], spans
    assert suppressed == 0
    for t0, t1 in spans:
        worst = tl.worst_between(t0, t1 - 0.001)
        assert worst in data_quality.CONCERN_CLASSES, (t0, t1, worst)
    # Every concerning second the strip shows is either inside a mark or counted as suppressed.
    assert not any(int(c) == U for c in tl.cls[int(spans[0][0]):int(spans[-1][1])]
                   if False), "UNREPORTED must never be marked"
    print("test_no_degraded_mark_covers_a_second_the_quality_strip_grades_good OK")


def test_the_short_degraded_stretches_are_suppressed_and_counted_never_silently_dropped():
    """MEASURED on the owner's own recordings, the maximal concern runs are 23 one-to-four-second
    DOP flecks (0060) against one 49 s POOR block (0062), with nothing in between anywhere — which
    is why the cut is 5 s. Suppression must be REPORTED, or this page would show fewer degraded
    stretches than the strip and say nothing about the difference."""
    G, M = data_quality.GOOD, data_quality.MODERATE
    flecks = _timeline(([M] * 2 + [G] * 100) * 23)
    auto, suppressed = marks.auto_marks(timeline=flecks)
    assert [m for m in auto if m["type"] == marks.TYPE_DEGRADED] == []
    assert suppressed == 23, suppressed

    block = _timeline([M] * 49 + [G] * 100)
    auto, suppressed = marks.auto_marks(timeline=block)
    assert len(auto) == 1 and suppressed == 0
    assert (auto[0]["t"], auto[0]["t_end"]) == (0.0, 49.0)
    print("test_the_short_degraded_stretches_are_suppressed_and_counted_never_silently_dropped OK")


def test_a_derived_mark_is_never_written_to_the_file():
    """The auto/manual split is STRUCTURAL, not a convention, and it turns out to be doubly so.

    A derived mark carries no anchor — it is re-derived from the detectors on every load, so it has
    nothing to survive and holds no chapter. Writing one therefore produces a record with no
    chapter, which `_valid_mark` refuses on the way back in; and even a hand-edited file that
    supplied one would come back `kind=manual`, `type=note` (`_kind` / `_type`). Either way the
    file cannot hold a detector's verdict that has outlived its detector."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _store_path(tmp)
        auto, _ = marks.auto_marks(dropouts=[(3, 10.0, 12.0)])
        assert auto[0]["chapter"] == "" and auto[0]["kind"] == marks.KIND_AUTO
        marks.save(marks.put(marks.empty_store(), "GX0062", auto[0]), path)
        assert marks.get(marks.load(path), "GX0062") == [], \
            "a derived mark survived a round trip through the store"
        # …and the second barrier, reached only by a hand-edit that supplies a chapter.
        forged = {**auto[0], "chapter": "GX010062"}
        marks.save(marks.put(marks.empty_store(), "GX0062", forged), path)
        got = marks.get(marks.load(path), "GX0062")[0]
        assert got["kind"] == marks.KIND_MANUAL and got["type"] == marks.TYPE_NOTE
    print("test_a_derived_mark_is_never_written_to_the_file OK")


def test_merge_puts_the_drivers_own_words_first_at_the_same_instant():
    auto, _ = marks.auto_marks(dropouts=[(1, 100.0, 101.0)])
    manual = marks.resolve([marks._norm_mark({"id": "m", "chapter": "c", "t": 100.0,
                                              "note": "that is where"})],
                           _chapter_map(("/d/c.MP4", 500.0)))
    order = [m["kind"] for m in marks.merge(auto, manual)]
    assert order[0] == marks.KIND_MANUAL, order
    print("test_merge_puts_the_drivers_own_words_first_at_the_same_instant OK")


# ==================================================================== the colour channel
def test_every_stored_colour_name_resolves_to_a_distinct_real_hue():
    """A mark stores a colour NAME so it follows the colour-blind palette; the theme resolves it.
    Every name in the stored vocabulary must resolve, and to a DIFFERENT hue — two names that paint
    the same pixel are one colour wearing two labels."""
    for palette in (theme.PALETTE_STANDARD, theme.PALETTE_COLORBLIND):
        theme.set_palette(palette)
        resolved = {name: theme.mark_colour(name) for name in marks.COLOURS}
        for name, hue in resolved.items():
            assert isinstance(hue, str) and hue.startswith("#"), (palette, name, hue)
        # The collision this test was written for: `amber` (CHART_SERIES slot 0) and `warn`
        # (ramp_mid_colour) are the SAME #F5A623 in the standard palette, which is why slot 0 is
        # not in the vocabulary at all.
        assert len(set(resolved.values())) == len(marks.COLOURS), (palette, resolved)
        assert theme.C.accent not in resolved.values() or palette == theme.PALETTE_STANDARD
    theme.set_palette(theme.PALETTE_STANDARD)
    assert theme.mark_colour("not-a-colour") == theme.C.text_dim, "an unknown name must not raise"
    # Every type's DEFAULT colour is in the vocabulary (a default outside it would be stored and
    # then silently rewritten to amber on the next load).
    for type_, colour in marks.TYPE_COLOUR.items():
        assert colour in marks.COLOURS, (type_, colour)
    print("test_every_stored_colour_name_resolves_to_a_distinct_real_hue OK")


# ======================================================================== the surfaces
def test_the_band_puts_its_ink_where_the_sliders_own_travel_says_the_instant_is():
    """The band shares `_LapRulerSlider._travel()` with the ruler ticks, the playhead and the
    quality strip, so a pin sits under the instant it annotates rather than under an approximation
    that drifts by half a handle at the ends."""
    from PySide6.QtCore import Qt

    from studio.video_view import _LapRulerSlider, _MarksBand

    slider = _LapRulerSlider(Qt.Horizontal)
    slider.setRange(0, 100_000)          # 100 s
    slider.resize(520, 26)
    band = _MarksBand(slider)
    band.resize(520, _MarksBand.INK_H)

    band.set_marks([
        {"id": "mid", "kind": marks.KIND_MANUAL, "type": marks.TYPE_NOTE, "colour": "amber",
         "note": "half way", "t": 50.0, "t_end": None, "placed": True},
        {"id": "span", "kind": marks.KIND_AUTO, "type": marks.TYPE_DEGRADED, "colour": "warn",
         "note": "bad", "t": 0.0, "t_end": 10.0, "placed": True},
        {"id": "gone", "kind": marks.KIND_MANUAL, "type": marks.TYPE_NOTE, "colour": "amber",
         "note": "other chapter", "t": None, "t_end": None, "placed": False},
    ])
    assert len(band.marks) == 2, "an unplaceable mark reached the band"
    runs = {m["id"]: (x, w) for x, w, m in band.runs()}
    x0, span, _handle = slider._travel()
    mid_x, _ = runs["mid"]
    assert abs((mid_x + _MarksBand._PIN_W / 2) - (x0 + span / 2)) <= 1.0, runs
    # A moment is a pin; a 10 % range is a tenth of the travel.
    assert runs["mid"][1] == _MarksBand._PIN_W
    assert abs(runs["span"][1] - span * 0.10) <= 1.5, runs

    # A one-second range on a 100 s bar would paint sub-pixel: it is floored at the pin width, so a
    # mark can never be invisible because it was short.
    band.set_marks([{"id": "tiny", "kind": marks.KIND_AUTO, "type": marks.TYPE_DROPOUT,
                     "colour": "bad", "note": "", "t": 40.0, "t_end": 40.4, "placed": True}])
    assert band.runs()[0][1] >= _MarksBand._PIN_W

    # …and the hover names what is under the pixel.
    text = band.describe_at(band.runs()[0][0])
    assert "GPS dropout" in text, text
    print("test_the_band_puts_its_ink_where_the_sliders_own_travel_says_the_instant_is OK")


def test_the_band_follows_the_slider_range_so_compare_mode_rescales_it():
    """Entering compare re-ranges the bar from the whole session to ONE lap. The band reads the
    slider's range, so it re-scales for free — and a mark outside the shown lap is not painted at
    the edge of it."""
    from PySide6.QtCore import Qt

    from studio.video_view import _LapRulerSlider, _MarksBand

    slider = _LapRulerSlider(Qt.Horizontal)
    slider.resize(520, 26)
    band = _MarksBand(slider)
    band.resize(520, _MarksBand.INK_H)
    band.set_marks([{"id": "in", "kind": marks.KIND_MANUAL, "type": marks.TYPE_NOTE,
                     "colour": "amber", "note": "", "t": 130.0, "t_end": None, "placed": True},
                    {"id": "out", "kind": marks.KIND_MANUAL, "type": marks.TYPE_NOTE,
                     "colour": "amber", "note": "", "t": 900.0, "t_end": None, "placed": True}])
    slider.setRange(0, 1_000_000)
    assert {m["id"] for _x, _w, m in band.runs()} == {"in", "out"}
    slider.setRange(100_000, 160_000)                 # compare: one 60 s lap
    assert {m["id"] for _x, _w, m in band.runs()} == {"in"}, "a mark outside the shown lap was drawn"
    print("test_the_band_follows_the_slider_range_so_compare_mode_rescales_it OK")


def _panel_marks() -> list[dict]:
    auto, _ = marks.auto_marks(dropouts=[(2, 300.0, 302.0)])
    manual = marks.resolve(
        [marks._norm_mark({"id": "m1", "chapter": "c", "t": 100.0, "type": marks.TYPE_TRAFFIC,
                           "colour": "purple", "note": "baulked out of 4"}),
         marks._norm_mark({"id": "m2", "chapter": "elsewhere", "t": 10.0,
                           "type": marks.TYPE_GOOD, "colour": "lime", "note": "that was the one"})],
        _chapter_map(("/d/c.MP4", 500.0)))
    return marks.merge(auto, manual)


def test_the_marks_page_filters_searches_and_gates_its_verbs():
    from studio.marks_panel import COL_NOTE, FILTER_AUTO, FILTER_MINE, MarksPanel

    panel = MarksPanel()
    panel.set_marks(_panel_marks(), suppressed=3)
    assert panel.table.rowCount() == 3
    assert panel.stack.currentIndex() == 0

    panel.filter_combo.setCurrentIndex(
        [panel.filter_combo.itemData(i) for i in range(panel.filter_combo.count())]
        .index(FILTER_MINE))
    assert {m["id"] for m in panel.visible_marks()} == {"m1", "m2"}
    panel.filter_combo.setCurrentIndex(
        [panel.filter_combo.itemData(i) for i in range(panel.filter_combo.count())]
        .index(FILTER_AUTO))
    assert [m["type"] for m in panel.visible_marks()] == [marks.TYPE_DROPOUT]

    panel.filter_combo.setCurrentIndex(0)
    panel.search.setText("baulked")
    assert [m["id"] for m in panel.visible_marks()] == ["m1"]
    assert panel.table.item(0, COL_NOTE).text() == "baulked out of 4"
    panel.search.setText("nothing matches this")
    assert panel.visible_marks() == [] and panel.stack.currentIndex() == 1
    assert "filter" in panel._empty.text().lower(), panel._empty.text()
    panel.search.setText("")

    # The three row verbs act on a hand-authored mark only — a derived one is re-derived on every
    # load and has nothing to save, so offering Delete on it would promise a change the next load
    # undoes. And Extend additionally needs a mark this open can place.
    assert not panel.edit_btn.isEnabled(), "a verb was live with no selection"
    assert panel.select_mark("m1")
    assert panel.edit_btn.isEnabled() and panel.delete_btn.isEnabled()
    assert panel.extend_btn.isEnabled()
    assert panel.select_mark("m2")                       # hand-authored, but NOT placed
    assert panel.edit_btn.isEnabled() and not panel.extend_btn.isEnabled()
    auto_id = next(m["id"] for m in panel.marks if m["kind"] == marks.KIND_AUTO)
    assert panel.select_mark(auto_id)
    assert not panel.edit_btn.isEnabled() and not panel.delete_btn.isEnabled()

    # The suppressed-stretch line is stated, and points at the surface that DOES draw them.
    assert "quality strip" in panel.suppressed_note.text()
    panel.set_marks(_panel_marks(), suppressed=0)
    assert not panel.suppressed_note.isVisible()
    print("test_the_marks_page_filters_searches_and_gates_its_verbs OK")


def test_the_empty_page_says_what_a_mark_is_and_how_to_make_one():
    from studio.marks_panel import MarksPanel

    panel = MarksPanel()
    panel.set_marks([])
    assert panel.stack.currentIndex() == 1
    body = panel._empty.text()
    assert "B" in body and "," in body and "." in body, body
    print("test_the_empty_page_says_what_a_mark_is_and_how_to_make_one OK")


def test_the_editor_edits_what_a_mark_says_and_never_where_it_is():
    from studio.marks_panel import MarkDialog

    m = marks.new_mark("GX010062", 42.0, note="first words")
    dlg = MarkDialog({**m, "placed": True}, title="Edit mark")
    dlg.type_combo.setCurrentIndex(marks.MANUAL_TYPES.index(marks.TYPE_TRAFFIC))
    dlg.note_edit.setPlainText("  baulked here  ")
    out = dlg.result_mark()
    assert out["type"] == marks.TYPE_TRAFFIC
    assert out["note"] == "baulked here", "the note was not stripped"
    assert out["id"] == m["id"] and out["chapter"] == m["chapter"]
    # Changing the type re-picks the colour only while the colour is still the OLD type's default.
    assert out["colour"] == marks.TYPE_COLOUR[marks.TYPE_TRAFFIC]
    dlg.colour_combo.setCurrentIndex(dlg.colour_combo.findData("lime"))
    dlg.type_combo.setCurrentIndex(marks.MANUAL_TYPES.index(marks.TYPE_KART))
    assert dlg.result_mark()["colour"] == "lime", "a deliberate colour was overwritten"
    # The two SEMANTIC hues belong to the derived marks and are not offered.
    offered = {dlg.colour_combo.itemData(i) for i in range(dlg.colour_combo.count())}
    assert offered.isdisjoint({"warn", "bad"}), offered
    print("test_the_editor_edits_what_a_mark_says_and_never_where_it_is OK")


def test_the_scrub_row_pays_for_exactly_the_two_bands_it_carries():
    """The row's height is a DERIVATION of the scale, not a number: the groove plus each band's own
    ink plus one sub-step per gap. Measured on the shipped theme it is 48 px — 38 before the marks
    band — and the window's minimum HEIGHT is unchanged by it (the video column is not what sets
    that floor), which is what makes the band affordable."""
    from studio.video_view import _MarksBand, _QualityStrip

    expected = theme.TOOLBAR_H + 2 * theme.SPACE_XXS + _MarksBand.INK_H + _QualityStrip.INK_H
    assert expected == 48, expected
    assert _MarksBand.INK_H == theme.SPACE_S
    assert _QualityStrip.INK_H == theme.SPACE_XS
    print("test_the_scrub_row_pays_for_exactly_the_two_bands_it_carries OK")


def test_the_real_video_view_stacks_marks_groove_quality_in_that_order():
    """The groove sits BETWEEN its two annotations: what the driver concluded above it, what the
    data says about the same second below. Measured on the production widget tree."""
    from studio.video_view import VideoView

    view = VideoView(None)
    view.scrub_row.resize(520, view.scrub_row.height())
    view.scrub_row.layout().activate()
    ys = [(w.objectName() or type(w).__name__, w.y())
          for w in (view.marks_band, view.slider, view.quality_strip)]
    assert [n for n, _ in sorted(ys, key=lambda p: p[1])] == \
        ["_MarksBand", "ScrubBar", "_QualityStrip"], ys
    assert view.scrub_row.height() == 48, view.scrub_row.height()
    view.stop_all()
    print("test_the_real_video_view_stacks_marks_groove_quality_in_that_order OK")


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()


if __name__ == "__main__":
    _run_all()
    print("\nall marks tests passed")
