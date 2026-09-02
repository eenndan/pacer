"""Pure-Python tests for the compare-video feature's Δ-badge math (no pacer, no telemetry file).

`Session.delta_between(lap_a, lap_b, t_in_a)` is the time delta of lap_a vs an ARBITRARY lap_b
at the same normalized track position lap_a is at time `t_in_a`. It drives the per-pane
"Δ vs other" badge in compare mode. These tests exercise it directly on synthetic cached per-lap
(times, dists) arrays (built via the shared tests/_synthetic bare_session factory), checking:
  * antisymmetry at the finish: delta_between(A,B, finish_A) == −delta_between(B,A, finish_B) and
    equals lap_a_time − lap_b_time;
  * a zero self-delta (delta_between(A,A,t) ≈ 0) at every point;
  * the CROSS-CHECK the design calls out: for lap_b == the global best lap, delta_between(A,best,t)
    matches the existing hardcoded-vs-best `delta_at_time(t)` (so the new method is consistent);
  * graceful None on a degenerate lap / outside-window times clamp to the lap edges.
Run: python tests/test_compare.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# PlayerPane (Qt widget) needs a QApplication; offscreen so there's no display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from _synthetic import bare_session, odometer, seed_lap  # noqa: E402

from studio import theme  # noqa: E402
from studio.player_pane import PlayerPane  # noqa: E402


def make_two_lap_session(best_is_b=True):
    """A bare Session with TWO laps cached. Lap A is slower (longer time) than lap B by design,
    with DIFFERENT total distances (different racing lines) so the normalized-distance alignment
    is exercised non-trivially. Returns (session, lap_a, lap_b)."""
    lap_a, lap_b = 3, 7
    # Lap A: slow-fast-slow, 120 samples @ 0.1 s = 11.9 s span, 520 m.
    ta, da = odometer(120, 0.1, 100.0, 520.0)
    # Lap B: faster overall (shorter span) and a slightly different line/length (508 m).
    tb, db = odometer(110, 0.1, 300.0, 508.0, lambda u: 1.3 + 0.7 * np.sin(u) ** 2)
    s = bare_session({lap_a: (ta, da), lap_b: (tb, db)},
                     best=lap_b if best_is_b else lap_a)
    return s, lap_a, lap_b


def test_finish_delta_equals_laptime_difference():
    """At each lap's finish (s=1) delta_between is exactly that lap's time minus the other's."""
    s, a, b = make_two_lap_session()
    ta = s._dist_cache[a][0]
    tb = s._dist_cache[b][0]
    a_time = float(ta[-1] - ta[0])
    b_time = float(tb[-1] - tb[0])
    d_ab = s.delta_between(a, b, float(ta[-1]))  # A vs B at A's finish
    d_ba = s.delta_between(b, a, float(tb[-1]))  # B vs A at B's finish
    assert abs(d_ab - (a_time - b_time)) < 1e-6, (d_ab, a_time - b_time)
    assert abs(d_ba - (b_time - a_time)) < 1e-6, (d_ba, b_time - a_time)
    # A is slower than B, so A-vs-B at the finish is POSITIVE (behind) and the reverse negative.
    assert d_ab > 0 and d_ba < 0
    print("test_finish_delta_equals_laptime_difference OK")


def test_self_delta_is_zero():
    """A lap compared to ITSELF is 0 at every track position (s aligns to s exactly)."""
    s, a, _ = make_two_lap_session()
    ta = s._dist_cache[a][0]
    for t in np.linspace(ta[0], ta[-1], 25):
        d = s.delta_between(a, a, float(t))
        assert abs(d) < 1e-9, (t, d)
    print("test_self_delta_is_zero OK")


def test_cross_check_vs_delta_at_time():
    """THE design cross-check: for lap_b == the GLOBAL best lap, delta_between(A, best, t) must
    match the existing hardcoded-vs-best delta_at_time(t) at the same media time. The factory
    seeds the best-lap memo (so the REAL best_lap_id() serves b); lap_at_time is monkey-patched
    on the bare session so delta_at_time runs with no pacer, then compare across lap A."""
    s, a, b = make_two_lap_session(best_is_b=True)  # b is the best (fastest) lap
    ta = s._dist_cache[a][0]
    # delta_at_time needs: best_lap_id() (factory-seeded), lap_at_time(t)->the lap containing t.
    s.lap_at_time = lambda t: a if ta[0] <= t <= ta[-1] else None
    pairs = []
    for t in np.linspace(ta[0] + 1e-6, ta[-1] - 1e-6, 31):
        d_new = s.delta_between(a, b, float(t))
        d_old = s.delta_at_time(float(t))
        assert d_old is not None
        assert abs(d_new - d_old) < 1e-9, (t, d_new, d_old)
        pairs.append((float(t), d_new, d_old))
    # Print a few sample numbers for the report's cross-check evidence.
    mid = pairs[len(pairs) // 2]
    print(f"  cross-check sample @ t={mid[0]:.3f}: delta_between={mid[1]:+.5f} s  "
          f"delta_at_time={mid[2]:+.5f} s  (max |diff| over 31 pts < 1e-9)")
    print("test_cross_check_vs_delta_at_time OK")


def test_outside_window_clamps_and_degenerate_none():
    """Times before/after lap_a's window clamp to its edge values (np.interp clamps), and a
    degenerate (uncached / <2-point) lap returns None rather than raising."""
    s, a, b = make_two_lap_session()
    ta = s._dist_cache[a][0]
    # A time well before the lap start clamps to the start fraction (s=0) -> delta == −b_at_0 == 0
    # only coincidentally; assert it simply returns a finite number equal to the start-clamped val.
    d_before = s.delta_between(a, b, float(ta[0]) - 100.0)
    d_at_start = s.delta_between(a, b, float(ta[0]))
    assert d_before is not None and abs(d_before - d_at_start) < 1e-9, (d_before, d_at_start)
    d_after = s.delta_between(a, b, float(ta[-1]) + 100.0)
    d_at_finish = s.delta_between(a, b, float(ta[-1]))
    assert d_after is not None and abs(d_after - d_at_finish) < 1e-9, (d_after, d_at_finish)
    # A degenerate (<2-point) lap -> _lap_time_dist returns None -> delta_between None (no crash),
    # whether it's the primary or the "other" side of the comparison.
    degen = 11
    seed_lap(s, degen, np.array([ta[0]]), np.array([0.0]))
    assert s.delta_between(a, degen, float(ta[0])) is None
    assert s.delta_between(degen, b, float(ta[0])) is None
    print("test_outside_window_clamps_and_degenerate_none OK")


# --------------------------------------------------------- P2: PlayerPane lap window
class _FakePlayer:
    """Minimal stand-in for the QMediaPlayer in a PlayerPane so the lap-window AND the chapter-seam
    state machine can be driven without a real decoder: tracks play/pause/setSource + records calls.
    `mediaStatus()`/`setSource()` let the seam tests drive the gate that the real Qt/FFmpeg backend
    exercises (the spurious LoadedMedia/EndOfMedia burst + the genuine LoadingMedia transition)."""
    def __init__(self):
        self.playing = True
        self.pause_calls = 0
        self.play_calls = 0
        self.positions = []
        self.sources = []  # QUrls passed to setSource (seam-switch tracking)
        from PySide6.QtMultimedia import QMediaPlayer
        self._status = QMediaPlayer.MediaStatus.LoadedMedia

    def playbackState(self):
        from PySide6.QtMultimedia import QMediaPlayer
        return (QMediaPlayer.PlaybackState.PlayingState if self.playing
                else QMediaPlayer.PlaybackState.PausedState)

    def play(self):
        self.playing = True
        self.play_calls += 1

    def pause(self):
        self.playing = False
        self.pause_calls += 1

    def setPosition(self, ms):
        self.positions.append(ms)

    def setSource(self, url):
        self.sources.append(url)

    def mediaStatus(self):
        return self._status


def _pane_with_fake_player():
    """A PlayerPane with no media (so _chapters is None -> _offset()==0, global==local) and its
    QMediaPlayer swapped for a fake. Emitted positions are captured off positionChanged."""
    pane = PlayerPane(None)
    pane.player = _FakePlayer()
    emitted = []
    pane.positionChanged.connect(lambda g: emitted.append(g))
    return pane, emitted


def test_lap_window_stops_and_clamps_at_end():
    """A set lap window pauses + clamps the EMITTED position at the lap end; before the end it
    plays through untouched and never pauses."""
    pane, emitted = _pane_with_fake_player()
    pane.set_lap_window(10.0, 20.0)  # global [10, 20] s
    # Inside the window: pass through, no pause, emitted == input.
    for ms in (10_000, 12_500, 19_990):  # 10.0, 12.5, 19.99 s — all before the 20.0 end
        pane._on_position(ms)
    assert pane.player.pause_calls == 0, pane.player.pause_calls
    assert abs(emitted[-1] - 19.990) < 1e-6, emitted[-1]
    # Step to/just past the end: pause once, emit the clamped end (exactly 20.0).
    pane._on_position(20_005)  # 20.005 s — a frame past the 20.0 end
    assert pane.player.pause_calls == 1
    assert abs(emitted[-1] - 20.0) < 1e-9, emitted[-1]
    assert abs(pane.current_global_time() - 20.0) < 1e-9
    # A stray late position after the stop re-clamps and doesn't re-pause (idempotent).
    pane.player.playing = False
    pane._on_position(20_100)
    assert pane.player.pause_calls == 1
    assert abs(emitted[-1] - 20.0) < 1e-9
    print("test_lap_window_stops_and_clamps_at_end OK")


def test_lap_window_tolerance_catches_sub_ms_short_fall():
    """The end fires within _WINDOW_STOP_TOL_S so a position that lands a hair BELOW the end (ms
    quantization / a 60 fps frame just shy of it) still stops — no under-resolved overshoot."""
    pane, emitted = _pane_with_fake_player()
    pane.set_lap_window(0.0, 5.0)
    pane._on_position(4_999)  # 4.999 s — within the 2 ms tolerance of 5.0
    assert pane.player.pause_calls == 1
    assert abs(emitted[-1] - 5.0) < 1e-9
    print("test_lap_window_tolerance_catches_sub_ms_short_fall OK")


def test_clear_lap_window_restores_whole_session():
    """clear_lap_window() drops the confinement: positions beyond the old end pass through and
    nothing pauses — byte-identical to a pane that never had a window."""
    pane, emitted = _pane_with_fake_player()
    pane.set_lap_window(0.0, 5.0)
    pane.clear_lap_window()
    for ms in (4_000, 6_000, 9_000):  # well past the dropped 5.0 end
        pane._on_position(ms)
    assert pane.player.pause_calls == 0
    assert abs(emitted[-1] - 9.0) < 1e-6, emitted[-1]
    print("test_clear_lap_window_restores_whole_session OK")


def test_normal_mode_no_window_unchanged():
    """With NO window set (normal single-pane mode) _on_position is a pure local->global emit:
    never pauses, emits the input position verbatim."""
    pane, emitted = _pane_with_fake_player()
    for ms in (0, 1_000, 50_000, 120_000):
        pane._on_position(ms)
    assert pane.player.pause_calls == 0
    assert [round(e, 6) for e in emitted] == [0.0, 1.0, 50.0, 120.0]
    print("test_normal_mode_no_window_unchanged OK")


# ----------------------------------------------- P3: chapter-seam state machine (regression #1/#7)
from PySide6.QtMultimedia import QMediaPlayer  # noqa: E402

from studio import chapters  # noqa: E402

_LOADING = QMediaPlayer.MediaStatus.LoadingMedia
_LOADED = QMediaPlayer.MediaStatus.LoadedMedia
_END = QMediaPlayer.MediaStatus.EndOfMedia


def _two_chapter_pane():
    """A PlayerPane over a TWO-chapter ChapterMap (each 100 s) with its player swapped for a fake,
    parked in chapter 0. The fake player records setSource/setPosition/play/pause so the seam gate
    can be driven with the exact status burst the real backend emits."""
    cmap = chapters.ChapterMap(["GX010000.MP4", "GX020000.MP4"], [100.0, 100.0])
    pane = PlayerPane(cmap)
    pane.player = _FakePlayer()
    # Land us in chapter 0, playing, nothing pending.
    pane._current_chapter = 0
    pane._switching = False
    pane._pending = None
    pane.player.playing = True
    return pane


def test_seam_same_chapter_seek_midswitch_preserves_pending():
    """REGRESSION #1: a switch to chapter 1 is armed (deferred seek+resume). The backend emits the
    SPURIOUS LoadedMedia+EndOfMedia burst (ignored by the gate). A same-chapter seek issued
    mid-switch must FOLD into _pending (NOT clear it / fire setPosition immediately), so the
    deferred seam seek is not clobbered and still resolves on the genuine load."""
    pane = _two_chapter_pane()
    # Arm a cross-chapter switch: seek into chapter 1 (global 150 -> chapter 1, local 50), playing.
    pane.seek(150.0)
    assert pane._switching is True, "switch should be armed"
    assert pane._pending == (1, 50.0, True), pane._pending
    assert len(pane.player.sources) == 1, "setSource called once for the switch"
    setpos_before = len(pane.player.positions)

    # The spurious burst the real backend re-emits synchronously inside setSource (old source's
    # leftover statuses) — the gate must ignore both, _pending untouched.
    pane._on_media_status(_LOADED)
    pane._on_media_status(_END)
    assert pane._pending == (1, 50.0, True), "spurious burst clobbered _pending"
    assert pane._switching is True

    # MID-SWITCH same-chapter seek (chapter 1 is current via _set_source): must fold into _pending,
    # NOT clear it nor fire setPosition on the not-yet-loaded media.
    pane.seek(170.0)  # global 170 -> chapter 1, local 70
    assert pane._switching is True, "a same-chapter mid-switch seek must not open the gate"
    assert pane._pending == (1, 70.0, True), f"seek mid-switch clobbered the deferred seek: {pane._pending}"
    assert len(pane.player.positions) == setpos_before, "no setPosition fired on the in-flight media"

    # The GENUINE load now begins (LoadingMedia opens the gate) then completes — the deferred seek
    # (folded to local 70) + resume must fire exactly once.
    pane._on_media_status(_LOADING)
    assert pane._switching is False, "LoadingMedia opens the gate"
    pane._on_media_status(_LOADED)
    assert pane._pending is None, "deferred seek not applied on the genuine load"
    assert pane.player.positions[-1] == 70_000, pane.player.positions
    assert pane.player.play_calls == 1, "resume not fired on the genuine load"
    assert pane.player.playing is True
    print("test_seam_same_chapter_seek_midswitch_preserves_pending OK")


def test_seam_pause_during_reopen_not_overridden_by_apply():
    """REGRESSION #7: the user PAUSES during a seam reopen. The deferred resume captured the
    play-state from BEFORE the pause, so honouring it on the genuine load would resume playback the
    user deliberately stopped. The pane must skip the resume and stay paused."""
    pane = _two_chapter_pane()
    pane.seek(150.0)  # arm switch to chapter 1, resume=True (was playing)
    assert pane._pending == (1, 50.0, True)
    # User pauses mid-reopen.
    pane.pause()
    assert pane.player.playing is False
    assert pane._user_paused_during_reopen is True
    # Genuine load: gate opens, then completes — the resume must be SKIPPED (deliberate pause wins).
    pane._on_media_status(_LOADING)
    pane._on_media_status(_LOADED)
    assert pane._pending is None
    assert pane.player.positions[-1] == 50_000, "deferred seek must still apply"
    assert pane.player.play_calls == 0, "watchdog/apply must NOT resume a deliberately-paused seam"
    assert pane.player.playing is False, "the pause must survive the seam"
    assert pane._user_paused_during_reopen is False, "the flag must clear after the reopen resolves"
    print("test_seam_pause_during_reopen_not_overridden_by_apply OK")


def test_seam_pause_during_reopen_not_overridden_by_watchdog():
    """REGRESSION #7 (watchdog path): a pause during reopen also survives the bounded-resume
    watchdog force-apply (a slow/hiccuping reopen) — it must not resume the deliberate pause."""
    pane = _two_chapter_pane()
    pane.seek(150.0)
    pane.pause()
    assert pane._user_paused_during_reopen is True
    # The watchdog fires (reopen took too long) and force-applies the pending seek.
    pane._on_seam_watchdog()
    assert pane._pending is None
    assert pane.player.positions[-1] == 50_000
    assert pane.player.play_calls == 0, "the watchdog must not resume a deliberately-paused seam"
    assert pane.player.playing is False
    print("test_seam_pause_during_reopen_not_overridden_by_watchdog OK")


def test_seam_normal_reopen_still_resumes():
    """Control: with NO pause and NO mid-switch seek, a normal auto-advance reopen resumes playing
    on the genuine load (the seam fix from main is intact)."""
    pane = _two_chapter_pane()
    # Simulate the auto-advance: end of chapter 0 -> load chapter 1 at local 0, keep playing.
    pane._on_end_of_media()
    assert pane._pending == (1, 0.0, True)
    assert pane._switching is True
    # Spurious burst ignored, then genuine load.
    pane._on_media_status(_LOADED)
    pane._on_media_status(_END)
    assert pane._pending == (1, 0.0, True), "spurious burst clobbered the auto-advance"
    pane._on_media_status(_LOADING)
    pane._on_media_status(_LOADED)
    assert pane._pending is None
    assert pane.player.positions[-1] == 0
    assert pane.player.play_calls == 1 and pane.player.playing is True
    print("test_seam_normal_reopen_still_resumes OK")


def test_seam_pause_then_play_during_reopen_ends_up_playing():
    """PASS-2 FIX #3: the user PAUSES during a seam reopen, then explicitly PLAYS — the latest user
    action wins, so the pane must RESUME on the genuine load. The fix clears
    _user_paused_during_reopen at the top of play(); without it the stale pause flag survived an
    explicit play and _apply_pending skipped the resume, leaving the pane stuck paused."""
    pane = _two_chapter_pane()
    pane.seek(150.0)  # arm switch to chapter 1, resume=True (was playing)
    assert pane._pending == (1, 50.0, True)
    # User pauses mid-reopen, THEN changes their mind and presses play.
    pane.pause()
    assert pane._user_paused_during_reopen is True and pane.player.playing is False
    pane.play()
    assert pane._user_paused_during_reopen is False, "explicit play must clear the pause-mid-reopen flag"
    assert pane.player.playing is True
    play_calls_after_play = pane.player.play_calls
    # Genuine load completes: the deferred seek applies AND the resume is honoured (play wins).
    pane._on_media_status(_LOADING)
    pane._on_media_status(_LOADED)
    assert pane._pending is None
    assert pane.player.positions[-1] == 50_000, "deferred seek must still apply"
    assert pane.player.play_calls > play_calls_after_play, "resume must fire on the genuine load"
    assert pane.player.playing is True, "play-after-pause-during-reopen must end up PLAYING"
    print("test_seam_pause_then_play_during_reopen_ends_up_playing OK")


def test_seam_pause_then_play_survives_watchdog():
    """PASS-2 FIX #3 (watchdog path): pause then play during a reopen also resumes via the
    bounded-resume watchdog (a slow/hiccuping reopen) — the explicit play wins there too."""
    pane = _two_chapter_pane()
    pane.seek(150.0)
    pane.pause()
    pane.play()
    assert pane._user_paused_during_reopen is False
    # The watchdog fires before the genuine load; it must honour the live PLAY intent.
    pane._on_seam_watchdog()
    assert pane._pending is None
    assert pane.player.positions[-1] == 50_000
    assert pane.player.playing is True, "the watchdog must resume after an explicit play"
    print("test_seam_pause_then_play_survives_watchdog OK")


def test_set_source_initial_load_does_not_arm_gate_but_replacement_does():
    """PASS-2 FIX #4: _set_source(index, switching=False) is the INITIAL load and must NOT arm the
    _switching gate; the default (switching=True) — a source REPLACEMENT — must. The initial load
    has no leftover statuses to suppress and (with no deferred seek) no watchdog to un-stick it, so
    arming the gate there would HANG the pane forever if the first load skipped the LoadingMedia
    transition that disarms it. Driven on the fake so the flag plumbing is deterministic."""
    pane = _two_chapter_pane()
    pane.player = _FakePlayer()
    # Initial-load semantics: gate stays OPEN.
    pane._switching = True  # pre-dirty to prove _set_source(switching=False) clears it
    pane._set_source(0, switching=False)
    assert pane._switching is False, "initial load (switching=False) must not arm the gate"
    # Replacement semantics: gate arms (the seam-switch behaviour is preserved).
    pane._set_source(1)  # default switching=True
    assert pane._switching is True, "a source replacement must still arm the gate"
    print("test_set_source_initial_load_does_not_arm_gate_but_replacement_does OK")


def test_initial_load_skipping_loadingmedia_is_not_gated():
    """PASS-2 FIX #4 (the exact hang): after the INITIAL load the gate is open, so a first
    LoadedMedia that arrives WITHOUT a preceding LoadingMedia is a clean no-op and the pane stays
    usable. With the old code the gate armed on the initial _set_source stayed armed (LoadedMedia is
    ignored while switching, and only LoadingMedia disarms it) and the pane hung. We reproduce the
    initial-load state deterministically (switching=False, nothing pending) on the fake, feed the
    sole LoadedMedia, then prove a same-chapter seek applies DIRECTLY rather than folding into a
    _pending that never resolves."""
    pane = _two_chapter_pane()
    pane.player = _FakePlayer()
    pane._current_chapter = 0
    pane._switching = False   # the post-initial-load state with FIX #4 (gate never armed)
    pane._pending = None
    pane._on_media_status(_LOADED)  # first (and only) status — no LoadingMedia ever emitted
    assert pane._pending is None and pane.player.positions == []
    # A same-chapter seek must seek DIRECTLY (gate open, nothing pending) — proof the pane isn't hung.
    pane.seek(30.0)  # chapter 0, local 30
    assert pane._pending is None, "same-chapter seek on a non-switching pane must not defer"
    assert pane.player.positions[-1] == 30_000, "seek must apply directly -> pane is live, not gated"
    print("test_initial_load_skipping_loadingmedia_is_not_gated OK")


def test_play_at_window_end_seeks_to_start_first():
    """FIX #4: a compare pane parked AT its lap-window end resumes from the window START on Play
    (a bare play() there is a dead no-op — it re-pauses on the next tick)."""
    cmap = chapters.ChapterMap(["GX010000.MP4", "GX020000.MP4"], [100.0, 100.0])
    pane = PlayerPane(cmap)
    pane.player = _FakePlayer()
    pane.player.playing = False
    pane.set_lap_window(120.0, 150.0)   # lap spans into chapter 1
    pane._latest_global = 150.0          # parked exactly at the window end
    pane._current_chapter = 1            # already in the chapter the end falls in
    pane._switching = False
    pane._pending = None
    pane.play()
    # Seek to the window start (global 120 -> chapter 1, local 20) THEN play.
    assert pane.player.positions[-1] == 20_000, pane.player.positions
    assert pane.player.play_calls == 1 and pane.player.playing is True
    print("test_play_at_window_end_seeks_to_start_first OK")


def test_play_mid_window_does_not_reseek():
    """FIX #4 control: Play while mid-window (not parked at the end) must NOT reseek — it just
    resumes from where it is."""
    pane = PlayerPane(None)
    pane.player = _FakePlayer()
    pane.player.playing = False
    pane.set_lap_window(10.0, 20.0)
    pane._latest_global = 14.0  # mid-window
    pane.play()
    assert pane.player.positions == [], "must not reseek mid-window"
    assert pane.player.play_calls == 1 and pane.player.playing is True
    print("test_play_mid_window_does_not_reseek OK")


def test_l8_04_cross_chapter_seek_flags_the_seam():
    """QA L8-04: a cross-chapter SEEK reopens the source exactly like the EndOfMedia auto-advance,
    but only the auto-advance told the shell about it — so a seek renamed the chapter banner to a
    chapter that had NOT loaded yet ("chapter 1 of 3", `_pending=(0, 300.0, False)`,
    `_switching=True`) with no busy affordance, while the identical reopen one path over showed
    "loading next chapter…". The seek must raise seamLoading(True) BEFORE _set_source, so the
    shell's hint is up (and its chapterChanged rename suppressed) until the destination genuinely
    loads and _apply_pending clears it."""
    pane = _two_chapter_pane()
    order = []
    pane.seamLoading.connect(lambda on: order.append(("seam", bool(on))))
    pane.chapterChanged.connect(lambda i: order.append(("chapter", i)))

    pane.seek(150.0)  # global 150 -> chapter 1, local 50: a CROSS-CHAPTER seek
    assert ("seam", True) in order, f"cross-chapter seek raised no seam hint: {order}"
    # Order matters: the shell gates its chapterChanged rename on the hint, so the hint must be up
    # BEFORE the rename fires, not after.
    assert order.index(("seam", True)) < order.index(("chapter", 1)), order
    assert pane._pending == (1, 50.0, True) and pane._switching is True

    # The destination genuinely loads: the hint comes down exactly once, with the deferred seek.
    pane._on_media_status(_LOADING)
    pane._on_media_status(_LOADED)
    assert pane._pending is None
    assert order[-1] == ("seam", False), order
    assert [v for k, v in order if k == "seam"] == [True, False], order

    # A SAME-chapter seek is not a reopen and must stay silent (no hint for a plain setPosition).
    quiet = _two_chapter_pane()
    seen = []
    quiet.seamLoading.connect(lambda on: seen.append(bool(on)))
    quiet.seek(30.0)
    assert seen == [], f"a same-chapter seek must not raise the seam hint: {seen}"
    print("test_l8_04_cross_chapter_seek_flags_the_seam OK")


# --------------------------------------------- B27: compare pickers + Δ badges (QA L8-05 / L8-06)
class _RecordingVideo:
    """Minimal VideoView stand-in recording the compare surfaces CompareController drives: the two
    PaneSpecs (whose `choices` ARE the pickers' contents) and the two Δ badges."""
    def __init__(self):
        self.specs = {}
        self.badges = {}
        self.gmeter_laps = {}

    def set_compare(self, spec_a, spec_b):
        self.specs[0], self.specs[1] = spec_a, spec_b

    def reseed_pane(self, side, spec):
        self.specs[side] = spec

    def set_pane_badge(self, side, text, colour):
        self.badges[side] = (text, colour)

    def set_pane_gmeter_lap(self, side, lap_id):
        self.gmeter_laps[side] = lap_id

    def is_gmeter_visible(self):
        return False

    def current_pane_time(self, side):
        return 0.0

    def pause_if_playing(self):
        pass

    def seek_pane(self, side, t):
        pass


class _RecordingPlots:
    def __init__(self):
        self.lap_ids = []

    def set_laps(self, ids):
        self.lap_ids = list(ids)

    def selected_lap_ids(self):
        return list(self.lap_ids)


class _EmptyTable:
    def selected_lap_ids(self):
        return []


def _three_lap_compare():
    """A CompareController over a bare 3-lap Session + recorders. Three laps so a picker can offer
    a lap that is neither pane's current one. Returns (controller, video, plots, [ids…])."""
    from studio.compare_controller import CompareController
    from studio.playback_state import PlaybackState

    ids = [3, 7, 11]
    spans = {3: (121, 100.0, 520.0), 7: (111, 300.0, 508.0), 11: (131, 600.0, 531.0)}
    laps = {lid: odometer(n, 0.1, t0, dist) for lid, (n, t0, dist) in spans.items()}
    s = bare_session(laps, best=7, valid=ids)
    windows = {lid: (float(t[0]), float(t[-1])) for lid, (t, _d) in laps.items()}
    s.lap_window = lambda lid: windows.get(lid)
    s.lap_time = lambda lid: windows[lid][1] - windows[lid][0]
    s.lap_at_time = lambda t: next((lid for lid, (w0, w1) in windows.items() if w0 <= t <= w1), None)
    video, plots = _RecordingVideo(), _RecordingPlots()
    c = CompareController(s, video, plots, _EmptyTable(), PlaybackState(), lambda: None)
    return c, video, plots, ids


def test_l8_05_pickers_cross_exclude_the_other_panes_lap():
    """QA L8-05: compare mode let a lap be compared with ITSELF — both pickers listed every valid
    lap, so picking pane A's lap in pane B gave lap_a == lap_b, two `Δ +0.00 s` badges and a chart
    overlay of [41, 41] (the same series twice), with no warning anywhere. Each picker must drop the
    lap the OTHER pane holds — in BOTH directions, and again after every repoint (the verifier
    confirmed the missing cross-exclusion was symmetric)."""
    c, video, plots, ids = _three_lap_compare()
    c.enter()
    a, b = c.lap_a, c.lap_b
    assert a != b and a in ids and b in ids
    assert b not in video.specs[0].choices, f"A's picker still offers B's lap: {video.specs[0].choices}"
    assert a not in video.specs[1].choices, f"B's picker still offers A's lap: {video.specs[1].choices}"
    # ids and labels stay parallel (video_view zips them strict=True).
    for side in (0, 1):
        spec = video.specs[side]
        assert len(spec.choices) == len(ids) - 1 == len(spec.choice_labels)
    assert sorted(plots.selected_lap_ids()) == sorted({a, b}), plots.selected_lap_ids()

    # Repoint pane A to the third lap: pane B's picker must now drop THAT lap and re-offer the one
    # A just left — the direction the original fix missed.
    third = next(lid for lid in ids if lid not in (a, b))
    c.on_pane_repoint(0, third)
    assert (c.lap_a, c.lap_b) == (third, b)
    assert b not in video.specs[0].choices, video.specs[0].choices
    assert third not in video.specs[1].choices, f"B still offers A's new lap: {video.specs[1].choices}"
    assert a in video.specs[1].choices, f"B never got back the lap A left: {video.specs[1].choices}"
    assert len(set(plots.selected_lap_ids())) == 2, plots.selected_lap_ids()

    # And the same in the other direction, from pane B.
    c.on_pane_repoint(1, a)
    assert (c.lap_a, c.lap_b) == (third, a)
    assert a not in video.specs[0].choices and third not in video.specs[1].choices
    assert len(set(plots.selected_lap_ids())) == 2, plots.selected_lap_ids()
    print("test_l8_05_pickers_cross_exclude_the_other_panes_lap OK")


def test_l8_05_same_lap_pair_badges_say_so():
    """QA L8-05 (the second half): if the pair is ever set to one lap twice — which the pickers can
    no longer produce — the badges must say SO. Two bare `Δ +0.00 s` badges read as a dead-even
    comparison of two laps, not as one lap measured against itself."""
    from studio.compare_controller import CompareController

    c, video, _plots, _ids = _three_lap_compare()
    c.enter()
    c._compare_b = c._compare_a
    c._compare_last_t = None
    c.tick()
    assert video.badges[0][0] == "same lap", video.badges
    assert video.badges[1][0] == "same lap", video.badges
    assert video.badges[0][0] == CompareController.SAME_LAP_BADGE  # the app's one spelling of it
    assert video.badges[0][1] is None and video.badges[1][1] is None, "same lap is not an ahead/behind"
    print("test_l8_05_same_lap_pair_badges_say_so OK")


def test_l8_06_pane_badges_use_the_shared_delta_formatter():
    """QA L8-06: the compare Δ badges hand-rolled `f"Δ {d:+.2f} s"` and so dropped the ▲/▼ direction
    glyph every other Δ surface carries — one frame showed hero `Δ +1.46 s ▼` beside badge
    `Δ +1.22 s`. The badge must be exactly theme.format_delta_run(d), the app's single Δ formatter,
    for behind, ahead, dead-even and None alike."""
    from studio.compare_controller import CompareController

    c = CompareController.__new__(CompareController)
    c.video = _RecordingVideo()
    for d in (1.2225, -0.8604, 0.0, None, 1e-9):
        c._set_pane_badge(0, d)
        assert c.video.badges[0] == (theme.format_delta_run(d), theme.delta_colour(d)), (d, c.video.badges[0])
    # The glyph is the point: behind carries ▼, ahead ▲ — and it is NOT colour-only.
    c._set_pane_badge(0, 1.2225)
    assert c.video.badges[0][0] == "Δ +1.22 s ▼", c.video.badges[0]
    c._set_pane_badge(1, -0.8604)
    assert c.video.badges[1][0] == "Δ -0.86 s ▲", c.video.badges[1]
    c._set_pane_badge(0, None)
    assert c.video.badges[0] == ("Δ —", None), c.video.badges[0]
    print("test_l8_06_pane_badges_use_the_shared_delta_formatter OK")


def test_compare_captions_and_picker_labels_are_one_based():
    """UI-scrutiny C2: the compare pickers/captions numbered laps from 0 — 'lap 36' beside the
    chart legend's 'lap 37' for the SAME lap on one screen. All three formatters must render
    through _signal.lap_label (1-based) while keeping 0-based ids as the API arguments."""
    from types import SimpleNamespace

    from studio.compare_controller import CompareController

    c = CompareController.__new__(CompareController)
    c.session = SimpleNamespace(
        best_lap_id=lambda: 36,
        lap_time=lambda i: 68.228 if i == 36 else 70.0,
        reference_label=lambda: "ref 0059",
    )
    assert c._lap_caption(36) == "lap 37 · 1:08.228 ★"
    assert c._lap_caption(0).startswith("lap 1 · ")
    labels = c._lap_choice_labels([0, 36])
    assert labels[0].startswith("lap 1  (") and "lap 37  (" in labels[1] and "★" in labels[1]
    ref_sess = SimpleNamespace(lap_time=lambda i: 65.5)
    assert c._cross_caption_b(ref_sess, 0) == "ref 0059 · lap 1 · 1:05.500"
    print("test_compare_captions_and_picker_labels_are_one_based OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nALL {len(tests)} COMPARE TESTS PASSED")
