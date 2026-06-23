"""Real-widget regression tests for the cross-recording compare + layout fixes (f7 phase B).

The PR-#80 tests drove a FAKE VideoView (a recorder that only captured the args to set_compare),
so they never exercised the real VideoView -> PlayerPane -> QMediaPlayer playback and missed three
real-GUI bugs:

  1. enter_cross's set_compare flips the compare TOGGLE button checked, which (signal still live)
     re-entered the app's on_toggled -> same-recording enter() -> set_compare with a pane B spec
     whose source is None, REBUILDING pane B on the PRIMARY recording's source. Pane B then played
     the wrong (original) footage. Fixed by syncing the toggle WITHOUT emitting (_sync_compare_btn).
  2. The compare panes came up unequal and the splitter handle wouldn't drag. Fixed with an
     entry-time 50/50 split from the splitter's real width + a draggable handle (width / no-collapse
     / opaque resize / per-cell size policy / a video-surface inset).
  3. The pane-B lap-start seek could be dropped by an async-load race on a freshly-created secondary
     (a leftover chapter-0 load satisfying the pending cross-chapter seek). Fixed by gating the
     deferred-seek apply on the genuinely-loaded source FILE (PlayerPane._source_is_chapter).

These tests use the REAL VideoView / PlayerPane with PACER_NO_MEDIA=1 (the production widget tree,
an inert media triplet) where a real decoder isn't needed, plus an OPT-IN real-media test on the
D24 footage when present (skipped otherwise) that proves pane B's actual QMediaPlayer source is the
reference file at the reference lap's S/F. Run: python tests/test_video_view_compare.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Build the panes with the inert media triplet (no decoder/audio device) but the FULL production
# widget tree + signal wiring — set BEFORE importing the studio widgets (read once at construction).
os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import chapters  # noqa: E402
from studio.player_pane import PlayerPane  # noqa: E402
from studio.video_view import PaneSpec, VideoView  # noqa: E402


def _spec(lap_id, window, caption, choices, *, source=None, choice_labels=None):
    """F8b helper: build a PaneSpec for one compare pane (was a slab of per-side positional args to
    set_compare). source=None reuses the primary recording; an explicit source is a cross-recording
    pane B."""
    return PaneSpec(lap_id, window, caption, source=source,
                    choices=list(choices), choice_labels=choice_labels)


def _cmap(stem: str, n: int = 3, dur: float = 1700.0) -> chapters.ChapterMap:
    """A synthetic n-chapter ChapterMap whose files carry a distinguishing `stem` so a test can tell
    the PRIMARY source from the REFERENCE source by filename."""
    paths = [f"/tmp/{stem}_ch{i}.MP4" for i in range(n)]
    return chapters.ChapterMap(paths, [dur] * n)


# --------------------------------------------------------------- Issue 1 (re-entrancy)
def test_enter_cross_keeps_pane_b_on_reference_source():
    """The Issue-1 bug, reproduced at the VideoView level: with compareToggled wired to a handler
    (as the app wires it to compare.on_toggled), entering cross compare via set_compare(pane_b_source
    = a DIFFERENT recording's ChapterMap) must leave pane B opened on THAT reference source — the
    button-sync must NOT re-enter the handler and rebuild pane B on the primary source."""
    primary = _cmap("PRIMARY")
    reference = _cmap("REFERENCE")
    view = VideoView(primary)

    # Mimic the app: a USER toggle / a same-recording enter rebuilds pane B on the PRIMARY source.
    # If the programmatic button-sync inside set_compare re-emits, THIS fires and clobbers pane B.
    reentries = []

    def on_toggled(on):
        reentries.append(on)
        if on:  # same-recording -> pane B reuses the PRIMARY source (spec source None)
            view.set_compare(_spec(0, (0.0, 10.0), "A", [0]),
                             _spec(0, (0.0, 10.0), "B", [0]))

    view.compareToggled.connect(on_toggled)

    # Enter the CROSS-recording compare: pane B's spec carries the reference ChapterMap source.
    view.set_compare(_spec(0, (0.0, 10.0), "A", [0]),
                     _spec(0, (1000.0, 1010.0), "ref B", [0],
                           source=reference, choice_labels=["ref B"]))

    assert view.secondary is not None
    assert view._secondary_source is reference, (
        f"pane B must be on the REFERENCE source, got {view._secondary_source}")
    assert reentries == [], (
        f"the button-sync must NOT re-enter the toggle handler (got {reentries})")
    # The compare toggle still ends up checked + reads ON (visual sync preserved).
    assert view.compare_btn.isChecked()
    print("test_enter_cross_keeps_pane_b_on_reference_source OK: pane B stayed on reference, "
          "no re-entrant rebuild")


def test_same_recording_compare_still_uses_primary_source():
    """No-regression: a same-recording compare (pane_b_source=None) opens pane B on the PRIMARY
    recording's source, exactly as before. The button-sync change must not disturb this path."""
    primary = _cmap("PRIMARY")
    view = VideoView(primary)
    view.set_compare(_spec(0, (0.0, 10.0), "A", [0, 1]),
                     _spec(1, (20.0, 30.0), "B", [0, 1]))
    assert view.secondary is not None
    assert view._secondary_source is primary, view._secondary_source
    assert view.compare_btn.isChecked()
    print("test_same_recording_compare_still_uses_primary_source OK: pane B on the primary source")


# --------------------------------------------------------------- F8b (PaneSpec round-trip)
def test_panespec_round_trips_onto_each_pane():
    """F8b: each side's PaneSpec lands on the RIGHT pane — lap_window on the pane, caption as the
    cell's tooltip (the strip's role word stays the label), and the picker selecting the spec's
    lap_id with the spec's choices/labels. Proves set_compare(pane_a, pane_b) fans the bundled
    per-side data to the correct cell, and reseed_pane(side, spec) repoints just that side."""
    view = VideoView(_cmap("PRIMARY"))
    view.set_compare(
        _spec(0, (1.0, 9.0), "cap A", [0, 1], choice_labels=["lap 0", "lap 1"]),
        _spec(1, (20.0, 30.0), "cap B", [0, 1], choice_labels=["lap 0", "lap 1"]))
    # Windows: pane A confined the global slider to its window (ms); both panes hold their lap window.
    assert (view.slider.minimum(), view.slider.maximum()) == (1_000, 9_000)
    # Captions surface as the cell-caption TOOLTIP (the visible label is the fixed ROLE word).
    assert view._cell_a.caption.toolTip() == "cap A"
    assert view._cell_b.caption.toolTip() == "cap B"
    assert view._cell_a.caption.text() == "THIS LAP"  # role label unchanged by the spec caption
    # Pickers: each cell selected the spec's lap_id from the spec's choices/labels (no repoint emit).
    assert view._cell_a.picker.currentData() == 0 and view._cell_a.picker.count() == 2
    assert view._cell_b.picker.currentData() == 1 and view._cell_b.picker.count() == 2
    assert view._cell_b.picker.currentText() == "lap 1"
    # reseed_pane(side, spec): repoint pane A to lap 1 — its picker + window follow, B untouched.
    view.reseed_pane(0, _spec(1, (3.0, 8.0), "cap A2", [0, 1], choice_labels=["lap 0", "lap 1"]))
    assert view._cell_a.picker.currentData() == 1
    assert view._cell_a.caption.toolTip() == "cap A2"
    assert (view.slider.minimum(), view.slider.maximum()) == (3_000, 8_000)
    assert view._cell_b.picker.currentData() == 1, "the other pane must be untouched by the repoint"
    print("test_panespec_round_trips_onto_each_pane OK")


# --------------------------------------------------------------- Issue 2 (splitter)
def test_compare_splitter_equal_and_draggable():
    """Issue 2: the two compare panes are EQUAL on entry and the handle is draggable. After
    set_compare the splitter is configured for a real drag (visible handle, no collapse, opaque
    resize), comes up ~50/50, and moveSplitter actually changes sizes() + resizes both cells."""
    view = VideoView(_cmap("PRIMARY"))
    view.resize(800, 400)
    view.show()
    view.set_compare(_spec(0, (0.0, 10.0), "A", [0, 1]),
                     _spec(1, (20.0, 30.0), "B", [0, 1]))
    _APP.processEvents()
    sp = view._splitter
    assert sp is not None
    # Draggable handle: a visible width, panes can't collapse over it, live (opaque) resize.
    assert sp.handleWidth() >= 6, sp.handleWidth()
    assert sp.childrenCollapsible() is False
    assert sp.opaqueResize() is True
    # Equal on entry (within a couple px of half — the handle eats a little width).
    sizes = sp.sizes()
    assert len(sizes) == 2 and abs(sizes[0] - sizes[1]) <= 4, sizes
    # A handle drag changes the split AND resizes both cells.
    total = sum(sizes)
    sp.moveSplitter(int(total * 0.30), 1)
    _APP.processEvents()
    moved = sp.sizes()
    assert moved != sizes, (sizes, moved)
    assert view._cell_a.width() < view._cell_b.width(), (view._cell_a.width(), view._cell_b.width())
    print(f"test_compare_splitter_equal_and_draggable OK: entry {sizes} -> drag {moved}")


# --------------------------------------------------------------- Issue 3 (deferred-seek gate)
class _GatePlayer:
    """A QMediaPlayer stand-in that records setSource + setPosition and lets a test report a
    `source()` URL, so PlayerPane._source_is_chapter can be exercised: the deferred cross-chapter
    seek must apply ONLY when the genuinely-loaded source is the pending chapter's file."""
    def __init__(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QMediaPlayer
        self._QUrl = QUrl
        self._status = QMediaPlayer.MediaStatus.LoadedMedia
        self.playing = False
        self.positions = []
        self._source = QUrl()

    def playbackState(self):
        from PySide6.QtMultimedia import QMediaPlayer
        return (QMediaPlayer.PlaybackState.PlayingState if self.playing
                else QMediaPlayer.PlaybackState.PausedState)

    def play(self):
        self.playing = True

    def pause(self):
        self.playing = False

    def setPosition(self, ms):
        self.positions.append(ms)

    def setSource(self, url):
        self._source = url

    def source(self):
        return self._source

    def set_loaded_file(self, path):
        self._source = self._QUrl.fromLocalFile(os.path.abspath(path))

    def mediaStatus(self):
        return self._status


def test_deferred_seek_waits_for_the_right_chapter_file():
    """Issue 3 (the async-seek race): a freshly-created secondary's initial chapter-0 load can land
    its LoadedMedia while a cross-chapter seek to a LATER chapter is already pending (and
    _current_chapter is the target). The OLD index-only gate would apply the seek against the
    chapter-0 file (wrong footage / wrong time). The file-matched gate ignores the chapter-0 load
    and applies the seek only once the TARGET chapter's file is genuinely loaded."""
    from PySide6.QtMultimedia import QMediaPlayer
    cmap = _cmap("REFERENCE", n=3, dur=1700.0)
    pane = PlayerPane(cmap)
    player = _GatePlayer()
    pane.player = player
    # A cross-chapter seek to chapter 1 (~local 200 s) is now pending; _current_chapter is 1.
    pane._current_chapter = 1
    pane._pending = (1, 200.0, False)
    pane._switching = False  # gate already opened (a LoadingMedia consumed it)

    # The chapter-0 file is what is actually loaded at this instant (the leftover initial load).
    player.set_loaded_file(cmap.chapters[0].path)
    pane._on_media_status(QMediaPlayer.MediaStatus.LoadedMedia)
    assert pane._pending == (1, 200.0, False), "seek must NOT apply against the chapter-0 file"
    assert player.positions == [], player.positions

    # Now the genuine TARGET (chapter 1) file is loaded — the seek applies exactly once.
    player.set_loaded_file(cmap.chapters[1].path)
    pane._on_media_status(QMediaPlayer.MediaStatus.LoadedMedia)
    assert pane._pending is None, "seek must apply once the target chapter's file is loaded"
    assert player.positions == [200000], player.positions  # 200.0 s -> ms
    print("test_deferred_seek_waits_for_the_right_chapter_file OK: gate matched the loaded file")


def test_source_is_chapter_headless_null_player_is_true():
    """The file-match gate must stay byte-identical on the PACER_NO_MEDIA headless path: the inert
    null player has no source(), so _source_is_chapter reports True (the deferred seek there is
    synchronous + raceless) and the legacy apply path is unchanged."""
    pane = PlayerPane(_cmap("REFERENCE"))  # built with the null player under PACER_NO_MEDIA=1
    assert pane._source_is_chapter(0) is True
    assert pane._source_is_chapter(2) is True
    print("test_source_is_chapter_headless_null_player_is_true OK")


# --------------------------------------------------------------- D6 (slider range vs real video)
def test_d6_slider_range_uses_real_video_when_longer_than_gpmf():
    """D6: the slider RANGE was sized off the GPMF metadata-track total (pane.total_duration) and
    DISCARDED the real QMediaPlayer duration whenever that total was > 0. On GoPro files where the
    telemetry track ends BEFORE the video track, the handle pinned early. Now _on_duration records
    the observed video duration and ranges the slider to the LARGER of the GPMF total and the
    observed video total — so the handle spans the whole playable video."""
    cmap = chapters.ChapterMap(["/tmp/SHORT_GPMF.MP4"], [60.0])  # GPMF says 60 s
    view = VideoView(cmap)
    assert view.pane.total_duration == 60.0
    # The real video track is LONGER (62.5 s) than the telemetry track — QMediaPlayer reports it.
    view._on_duration(62_500)
    assert view.slider.maximum() == 62_500, view.slider.maximum()
    print(f"test_d6_slider_range_uses_real_video_when_longer_than_gpmf OK: max={view.slider.maximum()} ms")


def test_d6_slider_range_keeps_gpmf_when_video_shorter():
    """D6 no-regression: when the telemetry track is LONGER than the video track, the GPMF total
    wins (max of the two), so the readout/range that already matched the telemetry clock is
    unchanged — the fix only ever WIDENS to cover the real video, never shrinks below the GPMF total."""
    cmap = chapters.ChapterMap(["/tmp/LONG_GPMF.MP4"], [90.0])  # GPMF says 90 s
    view = VideoView(cmap)
    view._on_duration(88_000)  # video track only 88 s
    assert view.slider.maximum() == 90_000, view.slider.maximum()
    print(f"test_d6_slider_range_keeps_gpmf_when_video_shorter OK: max={view.slider.maximum()} ms")


def test_d6_chaptered_sums_observed_with_gpmf_fallback():
    """D6 chaptered case: the observed video total sums each chapter's REAL video duration where
    QMediaPlayer has reported it, falling back to the chapter's GPMF duration for any not yet loaded.
    With 3 chapters (GPMF 100 s each = 300 s total) and chapter 0's video observed at 105 s, the
    observed total is 105 + 100 + 100 = 305 s, which exceeds the 300 s GPMF total -> slider 305 s."""
    cmap = chapters.ChapterMap([f"/tmp/CH_{i}.MP4" for i in range(3)], [100.0, 100.0, 100.0])
    view = VideoView(cmap)
    assert view.pane.total_duration == 300.0
    # Chapter 0 is the loaded source (current_chapter() == 0) — its real video is 105 s.
    assert view.pane.current_chapter() == 0
    view._on_duration(105_000)
    assert view.slider.maximum() == 305_000, view.slider.maximum()
    print(f"test_d6_chaptered_sums_observed_with_gpmf_fallback OK: max={view.slider.maximum()} ms")


def test_d6_compare_mode_does_not_widen_lap_window():
    """D6 guard: in compare mode the slider is confined to lap A's window, so a per-chapter duration
    must NOT widen it. _on_duration early-outs while _lap_window is set, leaving the lap-confined
    range intact."""
    view = VideoView(chapters.ChapterMap(["/tmp/CONF.MP4"], [60.0]))
    view.set_compare(_spec(0, (10.0, 20.0), "A", [0]),
                     _spec(0, (10.0, 20.0), "B", [0]))
    lo, hi = view.slider.minimum(), view.slider.maximum()
    assert (lo, hi) == (10_000, 20_000), (lo, hi)  # confined to lap A's window
    view._on_duration(62_500)  # a real video duration arriving mid-compare must not widen it
    assert (view.slider.minimum(), view.slider.maximum()) == (lo, hi), (
        view.slider.minimum(), view.slider.maximum())
    print("test_d6_compare_mode_does_not_widen_lap_window OK: lap window preserved")


# --------------------------------------------------------------- D1 (slider/arrow fan-out)
def test_d1_slider_move_fans_out_to_pane_b_in_compare():
    """D1: in compare mode VideoView._on_slider_moved (the single path the global slider AND the
    ←/→ arrows route through) seeks pane A then calls the injected fan-out hook with the new global
    time, so the app can distance-lock the SAME move to pane B. Before the fix only pane A moved.
    In single-video mode the hook must NOT fire (no pane B)."""
    view = VideoView(_cmap("PRIMARY"))
    fanned = []
    view.set_compare_seek_fanout(lambda t: fanned.append(t))

    # Single mode first: a slider move must NOT fan out (no secondary pane mounted).
    view._on_slider_moved(5_000)
    assert fanned == [], "fan-out must not fire in single-video mode"

    # Enter compare, then move the slider: the hook fires with the clamped global time.
    view.set_compare(_spec(0, (4.0, 9.0), "A", [0, 1]),
                     _spec(1, (20.0, 30.0), "B", [0, 1]))
    fanned.clear()
    view._on_slider_moved(7_000)  # 7 s, inside lap A's [4,9] window
    assert fanned == [7.0], fanned
    # The slider value is clamped to lap A's window before the fan-out (so pane B gets the clamped t).
    fanned.clear()
    view._on_slider_moved(99_000)  # past lap A's end -> clamps to 9.0 s
    assert fanned == [9.0], fanned
    print(f"test_d1_slider_move_fans_out_to_pane_b_in_compare OK: fanned {fanned}")


def test_d1_step_routes_through_fanout():
    """D1: the ←/→ arrow step (VideoView.step) routes through the SAME _on_slider_moved path, so it
    fans out to pane B too — the arrows distance-lock the pair exactly like the slider."""
    view = VideoView(_cmap("PRIMARY"))
    fanned = []
    view.set_compare_seek_fanout(lambda t: fanned.append(t))
    view.set_compare(_spec(0, (4.0, 9.0), "A", [0, 1]),
                     _spec(1, (20.0, 30.0), "B", [0, 1]))
    # Park the primary near lap A's start, then step +1 s; the fan-out must fire (clamped to window).
    view.pane.seek(5.0)
    fanned.clear()
    view.step(1.0)
    assert len(fanned) == 1 and 4.0 <= fanned[0] <= 9.0, fanned
    print(f"test_d1_step_routes_through_fanout OK: step fanned {fanned}")


# --------------------------------------------------------------- Issue 1+3 real media (opt-in)
def _d24():
    """The D24 cross-recording media for the OPT-IN real-media proof. Skipped unless
    PACER_D24_MEDIA=1 is set AND the footage is present: loading two 3-chapter 12 GB recordings
    takes minutes, so the default ctest run (and any machine without the footage) skips it — the
    headless tests above already cover the three fixes' logic; this is the on-hardware proof."""
    if os.environ.get("PACER_D24_MEDIA") != "1":
        return (None, None)
    d = os.path.expanduser("~/Desktop/D24")
    prim = os.path.join(d, "GX010060.MP4")
    ref = os.path.join(d, "GX010062.MP4")
    return (prim, ref) if (os.path.exists(prim) and os.path.exists(ref)) else (None, None)


def test_real_media_pane_b_is_reference_at_lap_start():
    """REAL widgets on REAL media (the whole point — the fakes hid this): build a StudioWindow on
    the D24 primary, load the 0062 reference, enter cross compare via the app path, pump the event
    loop until the async load + deferred seek settle, then assert the SECONDARY pane's actual
    QMediaPlayer source is a REFERENCE file (stem != GX010060) resolved to the reference lap's
    CHAPTER + a global time ≈ the reference lap-window start. Skipped when D24 isn't present."""
    import time

    prim_path, ref_path = _d24()
    if prim_path is None:
        print("test_real_media_pane_b_is_reference_at_lap_start SKIPPED "
              "(set PACER_D24_MEDIA=1 with ~/Desktop/D24 footage to run the on-hardware proof)")
        return
    # The full StudioWindow needs a real decoder for this proof, so DROP the headless flag for it.
    os.environ.pop("PACER_NO_MEDIA", None)
    from studio.app import StudioWindow
    from studio.session import Session  # noqa: F401  (ensures the studio import graph is built)

    prim = chapters.discover_siblings(prim_path)
    ref = chapters.discover_siblings(ref_path)

    def pump(secs):
        end = time.time() + secs
        while time.time() < end:
            _APP.processEvents()
            time.sleep(0.01)

    win = StudioWindow(prim)
    win.resize(1440, 900)
    win.show()
    # C1: Session.load now runs on a worker QThread, so the session isn't ready synchronously after
    # __init__ — pump until it settles (bounded) before touching win.session.
    deadline = time.time() + 60.0
    while win.view is None and time.time() < deadline:
        _APP.processEvents()
        time.sleep(0.01)
    assert win.view is not None, "primary recording load did not complete"
    reason = win.session.load_reference(ref)
    assert reason is None, f"reference refused: {reason}"
    win._update_reference_status()
    assert win.compare.enter_cross() is True

    ref_lap = win.session.reference_lap_id()
    win_b = win.session.reference_session().lap_window(ref_lap)
    sec = win.video.secondary
    assert sec is not None
    # Pump until the deferred seek lands (bounded — not an unbounded sleep).
    landed = False
    for _ in range(25):
        pump(1.0)
        if sec._pending is None and sec.current_global_time() > 1.0:
            landed = True
            break
    assert landed, "the reference pane never resolved its lap-start seek"
    src = os.path.basename(sec.player.source().toLocalFile())
    stem = os.path.splitext(src)[0]
    assert "0062" in stem and "0060" not in stem, f"pane B is not a reference file: {src}"
    gl = sec.current_global_time()
    assert abs(gl - win_b[0]) < 1.0, (gl, win_b[0])
    # Restore the flag for any subsequent tests in the module.
    os.environ["PACER_NO_MEDIA"] = "1"
    print(f"test_real_media_pane_b_is_reference_at_lap_start OK: pane B={src} "
          f"global={gl:.2f}s vs lap start {win_b[0]:.2f}s")


def _run_all():
    test_enter_cross_keeps_pane_b_on_reference_source()
    test_same_recording_compare_still_uses_primary_source()
    test_panespec_round_trips_onto_each_pane()
    test_compare_splitter_equal_and_draggable()
    test_deferred_seek_waits_for_the_right_chapter_file()
    test_source_is_chapter_headless_null_player_is_true()
    # D6: slider range reconciles the GPMF metadata total with the real video duration.
    test_d6_slider_range_uses_real_video_when_longer_than_gpmf()
    test_d6_slider_range_keeps_gpmf_when_video_shorter()
    test_d6_chaptered_sums_observed_with_gpmf_fallback()
    test_d6_compare_mode_does_not_widen_lap_window()
    # D1: the global slider + ←/→ arrows fan the seek out to pane B (distance-lock entry point).
    test_d1_slider_move_fans_out_to_pane_b_in_compare()
    test_d1_step_routes_through_fanout()
    test_real_media_pane_b_is_reference_at_lap_start()
    print("ALL VIDEO-VIEW COMPARE TESTS PASSED")


if __name__ == "__main__":
    _run_all()
