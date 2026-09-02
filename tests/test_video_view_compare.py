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
    # Captions surface as the cell-caption TOOLTIP, behind the FULL role word (the label itself
    # drops to "THIS"/"REF" when the strip is too narrow for the long form).
    assert view._cell_a.caption.toolTip() == "THIS LAP — cap A"
    assert view._cell_b.caption.toolTip() == "REFERENCE — cap B"
    assert view._cell_a.caption.text() == "THIS LAP"  # role label unchanged by the spec caption
    # Pickers: each cell selected the spec's lap_id from the spec's choices/labels (no repoint emit).
    assert view._cell_a.picker.currentData() == 0 and view._cell_a.picker.count() == 2
    assert view._cell_b.picker.currentData() == 1 and view._cell_b.picker.count() == 2
    assert view._cell_b.picker.currentText() == "lap 1"
    # reseed_pane(side, spec): repoint pane A to lap 1 — its picker + window follow, B untouched.
    view.reseed_pane(0, _spec(1, (3.0, 8.0), "cap A2", [0, 1], choice_labels=["lap 0", "lap 1"]))
    assert view._cell_a.picker.currentData() == 1
    assert view._cell_a.caption.toolTip() == "THIS LAP — cap A2"
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


# --------------------------------------------------------------- fullscreen-video (⤢) transport button
def test_fullscreen_button_present_and_emits_video_focus_intent():
    """The transport bar carries a checkable ⤢ button whose CLICK emits videoFocusRequested (the
    pure input intent, like compare_btn), and set_video_focus_visual reflects the on/off state back
    WITHOUT re-emitting (the controller→view reflection, mirroring _set_compare_visual). A double-click
    on the primary video content also emits the same intent."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    view = VideoView(_cmap("PRIMARY"))
    assert hasattr(view, "fullscreen_btn"), "the transport bar must carry the ⤢ button"
    assert view.fullscreen_btn.isCheckable()

    intents = []
    view.videoFocusRequested.connect(lambda: intents.append(True))

    # A genuine click emits the intent exactly once.
    view.fullscreen_btn.click()
    assert intents == [True], f"the ⤢ click must emit videoFocusRequested once (got {intents})"

    # The visual-reflection setter must NOT re-emit the intent (no feedback loop).
    intents.clear()
    view.set_video_focus_visual(True)
    assert intents == [], "set_video_focus_visual must not re-emit videoFocusRequested"
    assert view.fullscreen_btn.isChecked() is True
    view.set_video_focus_visual(False)
    assert view.fullscreen_btn.isChecked() is False and intents == []

    # A double-click on the PRIMARY video content emits the same intent (the pane's event filter).
    intents.clear()
    _APP.sendEvent(view.pane.video, QMouseEvent(
        QEvent.MouseButtonDblClick, QPointF(4, 4), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert intents == [True], f"a video-content double-click must emit the intent (got {intents})"
    print("test_fullscreen_button_present_and_emits_video_focus_intent OK")


# --------------------------------------------------------------- B25 transport chrome (QA sweep)
# These need the REAL theme: every number below (label padding, the picker's content width, the
# slider handle's box) comes out of the app's own QSS, and Qt's default palette/style gives
# different ones. apply_theme is global and idempotent — the tests above are geometry-agnostic.
def _themed():
    from studio import theme
    theme.register_fonts()
    theme.apply_theme(_APP)


def _settle(n=8):
    for _ in range(n):
        _APP.processEvents()


# Views built by the tests below are kept alive to the end of the run: a garbage-collected
# VideoView leaves its PlayerPane's event filter installed on a half-destroyed widget, which
# prints a Qt override traceback from an unrelated later test.
_ALIVE = []


def _compare_view(width, labels_a=("lap 42  (1:08.201)  ★",), labels_b=("lap 51  (1:08.384)",)):
    """A shown VideoView in compare mode at `width`, with realistic picker labels + Δ badges."""
    _themed()
    view = VideoView(_cmap("PRIMARY"))
    view.resize(width, 420)
    view.show()
    view.set_compare(_spec(0, (0.0, 10.0), "lap 42 · 1:08.201 ★", [0], choice_labels=list(labels_a)),
                     _spec(0, (20.0, 30.0), "lap 51 · 1:08.384", [0], choice_labels=list(labels_b)))
    view.set_pane_badge(0, "Δ -0.19 s", None)
    view.set_pane_badge(1, "Δ +0.19 s", None)
    _settle()
    _ALIVE.append(view)
    return view


def _overlap(cell):
    """The widest intersection between any two of the strip's three children (0 = they never touch).
    Hidden children are skipped — they paint nothing, and a hidden widget keeps its last geometry."""
    rects = [w.geometry() for w in (cell.caption, cell.picker, cell.badge) if w.isVisibleTo(cell)]
    worst = 0
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            inter = rects[i].intersected(rects[j])
            if not inter.isEmpty():
                worst = max(worst, inter.width())
    return worst


def test_l8_01_compare_strip_never_overlaps_and_keeps_the_lap_time():
    """L8-01: at the app's own default size the compare strip's three children demanded 316 px in a
    243 px box, and QHBoxLayout resolved the shortfall by painting the Δ badge 67 px ON TOP of the
    lap picker — with no tooltip, so the covered lap time was unrecoverable. The strip now budgets
    its width: no two children may overlap at ANY width, and the picker always gets at least the
    width its own content needs, so the lap TIME is never the thing that yields."""
    # 620/520/380 px of VideoView = compare cells of 306/256/186 px, i.e. windows from ~1700 down to
    # ~1050 px wide. 300 is below the width the lap label itself needs (168 px) — there the only
    # promise left is that nothing is painted on top of anything else.
    for width in (620, 520, 380, 300):
        view = _compare_view(width)
        for side, cell in ((0, view._cell_a), (1, view._cell_b)):
            assert _overlap(cell) == 0, (
                f"side {side} at view width {width}: the strip's children overlap by "
                f"{_overlap(cell)} px (cell {cell.width()} px)")
            if width < 380:
                continue
            # The lap text is the one thing that never yields: the picker got at least its own
            # content width (QComboBox elides the current item below that).
            assert cell.picker.width() >= cell.picker.sizeHint().width(), (
                f"side {side} at view width {width}: picker {cell.picker.width()} px < the "
                f"{cell.picker.sizeHint().width()} px its lap label needs -> the time is elided")
            # ... and neither Δ nor the role word is clipped.
            assert cell.badge.width() >= cell.badge.sizeHint().width(), (side, width)
            fm = cell.caption.fontMetrics()
            assert fm.horizontalAdvance(cell.caption.text()) <= cell.caption.width(), (
                f"side {side} at view width {width}: the role caption is clipped")
        _ALIVE.append(view)
    print("test_l8_01_compare_strip_never_overlaps_and_keeps_the_lap_time OK: 0 px overlap at "
          "620/520/380/300 px with the lap time intact")


def test_l8_01_narrow_strip_falls_back_to_the_short_role_word():
    """The width budget's last step, made explicit: when the full role word cannot share a row with
    the Δ badge it drops to its short form (never a mid-word clip), and the FULL word stays in the
    tooltip beside the app's rich lap text."""
    wide = _compare_view(620)
    assert wide._cell_b.caption.text() == "REFERENCE"
    _ALIVE.append(wide)
    narrow = _compare_view(340)
    assert narrow._cell_b.caption.text() == "REF", narrow._cell_b.caption.text()
    assert narrow._cell_b.caption.toolTip().startswith("REFERENCE — "), (
        narrow._cell_b.caption.toolTip())
    assert _overlap(narrow._cell_b) == 0
    _ALIVE.append(narrow)
    print("test_l8_01_narrow_strip_falls_back_to_the_short_role_word OK")


def test_l8_02_lap_ruler_decimates_instead_of_hatching():
    """L8-02: 66 lap boundaries painted one line each collapsed into a 4 px-pitch hatch (26 % ink)
    where no lap was identifiable. The ruler now decimates to the width it has: at most one tick per
    _MIN_PITCH px, every 5th promoted to a major, and the tooltip says the ticks are lap
    boundaries (and at which step) instead of leaving them unexplained."""
    _themed()
    view = VideoView(_cmap("PRIMARY"))
    view.resize(560, 420)
    view.show()
    _settle()
    sl = view.slider
    sl.setRange(0, 5_330_000)                       # ~89 min, the F.B 3-chapter session
    view.set_lap_ticks([i * 80.0 for i in range(66)])   # 66 boundaries, ~80 s apart
    _settle()
    plan = sl.tick_plan()
    drawn = sorted(plan["minor"] + plan["major"])
    assert len(drawn) <= sl.width() // 8, (
        f"{len(drawn)} ticks drawn in {sl.width()} px — over the 1-per-8px budget "
        f"({sl.width() // 8})")
    pitches = [b - a for a, b in zip(drawn, drawn[1:], strict=False)]
    assert min(pitches) >= sl._MIN_PITCH, f"ticks {min(pitches)} px apart: still a hatch"
    assert plan["step"] > 1, "66 boundaries in 560 px must decimate"
    assert plan["major"], "no major ticks — nothing for the eye to count laps by"
    assert "lap" in sl.toolTip().lower(), f"the tooltip never says the ticks are laps: {sl.toolTip()}"
    # A short session keeps EVERY lap and gets the current-lap bracket (which is suppressed above,
    # where a lap is narrower than the handle and the bracket would hide under it).
    view.set_lap_ticks([0.0, 100.0, 200.0, 300.0])
    sl.setRange(0, 400_000)
    sl.setValue(150_000)
    _settle()
    short = sl.tick_plan()
    assert short["step"] == 1, short["step"]
    assert short["bracketable"] and short["current"] is not None, short
    assert short["current"][0] < sl._x_for(150_000) < short["current"][1], short["current"]
    _ALIVE.append(view)
    print("test_l8_02_lap_ruler_decimates_instead_of_hatching OK: "
          f"66 boundaries -> {len(drawn)} ticks, min pitch {min(pitches)} px")


def test_l8_03_fullscreen_button_is_disabled_while_comparing():
    """L8-03: in compare mode CentralView refuses the ⤢ gesture, but the button is checkable, so Qt
    had already latched it ON — a checked tint pixel-identical to a genuinely-on toggle, with an
    unchanged tooltip. The button is now DISABLED while the compare stage is mounted, with a
    tooltip that says why, so its checked state can never disagree with the app's."""
    view = _compare_view(620)
    intents = []
    view.videoFocusRequested.connect(lambda: intents.append(True))
    assert not view.fullscreen_btn.isEnabled(), "the ⤢ must be disabled while comparing"
    assert not view.fullscreen_btn.isChecked()
    assert "comparing" in view.fullscreen_btn.toolTip(), view.fullscreen_btn.toolTip()
    view.fullscreen_btn.click()          # the gesture the user makes anyway
    _settle()
    assert not view.fullscreen_btn.isChecked(), "a refused ⤢ click must not latch the button"
    assert intents == [], "a disabled ⤢ must not emit the intent"
    # ... and the video double-click, the same intent by another route, is gated the same way.
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    _APP.sendEvent(view.pane.video, QMouseEvent(
        QEvent.MouseButtonDblClick, QPointF(4, 4), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    _settle()
    assert intents == [], "the double-click gesture must be refused in compare too"
    # Leaving compare gives the gesture back.
    view.exit_compare()
    _settle()
    assert view.fullscreen_btn.isEnabled()
    assert "comparing" not in view.fullscreen_btn.toolTip(), view.fullscreen_btn.toolTip()
    view.fullscreen_btn.click()
    assert intents == [True] and view.fullscreen_btn.isChecked()
    _ALIVE.append(view)
    print("test_l8_03_fullscreen_button_is_disabled_while_comparing OK")


def test_l8_07_scrub_slider_clears_the_hit_target_floor():
    """L8-07: the scrub bar was 325x20 with an 18x18 handle — the only interactive in the video
    panel under the 24 px floor, at 16.4 s per pixel. It now has its own full-width row, a >=24 px
    widget and a >=24x24 handle."""
    from PySide6.QtWidgets import QStyle, QStyleOptionSlider
    _themed()
    view = VideoView(_cmap("PRIMARY"))
    view.resize(560, 420)
    view.show()
    _settle()
    sl = view.slider
    assert sl.height() >= 24, f"the scrub slider is {sl.height()} px tall"
    opt = QStyleOptionSlider()
    sl.initStyleOption(opt)
    handle = sl.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, sl)
    assert handle.width() >= 24 and handle.height() >= 24, (handle.width(), handle.height())
    # Its own row: the slider spans the panel instead of sharing the row with five buttons.
    assert sl.width() > view.width() - 120, (
        f"the slider is only {sl.width()} px in a {view.width()} px panel — it is still sharing "
        f"the button row")
    _ALIVE.append(view)
    print("test_l8_07_scrub_slider_clears_the_hit_target_floor OK")


def test_ia_06_compare_button_carries_a_visible_label():
    """IA-06: no visible string anywhere in the app contained the word "compare" — the capability
    lived behind an icon-only button whose only text was a tooltip. The button is labelled."""
    _themed()
    view = VideoView(_cmap("PRIMARY"))
    assert "compare" in view.compare_btn.text().lower(), (
        f"the compare toggle has no visible label (text={view.compare_btn.text()!r})")
    assert "compare" in view.compare_btn.toolTip().lower()
    _ALIVE.append(view)
    print("test_ia_06_compare_button_carries_a_visible_label OK")


def test_u9_04_f_key_reaches_the_video_focus_gesture():
    """U9-04: ⤢ "make the video fill the screen" had NO keyboard route anywhere in the app — only a
    click or a double-click on the video. F is now bound, routed through the button (like app.py's
    G / C) so the disabled state gates the key too."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeySequence, QShortcut
    _themed()
    view = VideoView(_cmap("PRIMARY"))
    keys = [sc for sc in view.findChildren(QShortcut) if sc.key() == QKeySequence(Qt.Key_F)]
    assert len(keys) == 1, f"expected exactly one F binding on the video view, got {keys}"
    intents = []
    view.videoFocusRequested.connect(lambda: intents.append(True))
    keys[0].activated.emit()
    assert intents == [True], "F must reach the ⤢ intent"
    assert "(F" in view.fullscreen_btn.toolTip(), view.fullscreen_btn.toolTip()
    # In compare the button is disabled, so the key is a no-op rather than a silent refusal.
    view.set_compare(_spec(0, (0.0, 10.0), "A", [0]), _spec(0, (20.0, 30.0), "B", [0]))
    intents.clear()
    keys[0].activated.emit()
    assert intents == [], "F must be inert while the gesture is unavailable"
    _ALIVE.append(view)
    print("test_u9_04_f_key_reaches_the_video_focus_gesture OK")


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
    test_fullscreen_button_present_and_emits_video_focus_intent()
    # B25 (QA sweep 2026-09-01): the transport chrome's layout, state and keyboard reach. These
    # apply the real theme, so they run after the geometry-agnostic wiring tests above.
    test_l8_01_compare_strip_never_overlaps_and_keeps_the_lap_time()
    test_l8_01_narrow_strip_falls_back_to_the_short_role_word()
    test_l8_02_lap_ruler_decimates_instead_of_hatching()
    test_l8_03_fullscreen_button_is_disabled_while_comparing()
    test_l8_07_scrub_slider_clears_the_hit_target_floor()
    test_ia_06_compare_button_carries_a_visible_label()
    test_u9_04_f_key_reaches_the_video_focus_gesture()
    test_real_media_pane_b_is_reference_at_lap_start()
    print("ALL VIDEO-VIEW COMPARE TESTS PASSED")


if __name__ == "__main__":
    _run_all()
