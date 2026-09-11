"""Playback-rate (slow motion) guard — and the one thing it must not cost: the telemetry lock.

The ask was "let me play the run at 1/2 or 1/4 speed; inputs are too fast to read in real time".
The risk is not the rate itself, it is that pacer's video and telemetry are locked together: a
~30 Hz tick drives the map marker, the chart cursors and the readout from the playhead, and in
compare mode TWO decoders play two laps against each other. Two ways that could break:

  1. THE LOCK COULD BE RATE-DEPENDENT. It is not, and this file proves it two ways rather than
     asserting it: (a) nothing downstream of the player reads a rate — a static sweep over every
     module in the sync path, so the lock CANNOT be a function of it; and (b) the same sequence of
     media positions produces byte-identical marker / cursor / applied-time state at 0.25x, 1x and
     2x, driven through the production signal path on the real CentralView. The mechanism is that
     the tick reads the decoder's OWN reported position (`positionChanged` -> PlaybackState),
     never a wall clock the app integrates, so a rate change moves the same positions past the
     same tick more slowly and every consumer still looks the telemetry up at a media time.

  2. THE TWO COMPARE PANES COULD DISAGREE. A rate on one pane and not the other turns a comparison
     into a lie that still looks like a comparison. So the rate lives on the SHELL: it fans out
     over every live pane, and a lazily-created secondary is born at it (entering compare while
     reviewing at 0.25x is exactly how that would have shipped broken).

Plus the seam: a cross-chapter switch is a new media source and a source carries no rate, so
`PlayerPane._apply_pending` re-applies it — otherwise a chaptered recording silently snaps back to
real time at every seam.

PACER_NO_MEDIA=1: the real widget tree + real signal wiring over an inert media triplet, so what is
asserted here is the state the app holds and hands the decoder. WHAT THIS CANNOT SEE is the decoder
itself — that `h264` really decodes at 0.25x, and that the frames presented match the positions
reported. That needs a real media backend and is verified by hand.
Run: QT_QPA_PLATFORM=offscreen PACER_NO_MEDIA=1 python tests/test_playback_rate.py
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import chapters  # noqa: E402
from studio.player_pane import PlayerPane  # noqa: E402
from studio.video_view import PLAYBACK_RATES, PaneSpec, VideoView, nearest_rate  # noqa: E402

_STUDIO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "studio")


def _settle(n=4):
    for _ in range(n):
        _APP.processEvents()


def _cmap(stem: str, n: int = 2, dur: float = 100.0) -> chapters.ChapterMap:
    return chapters.ChapterMap([f"/tmp/{stem}_ch{i}.MP4" for i in range(n)], [dur] * n)


# ------------------------------------------------------------------ the ladder
def test_the_rate_ladder_is_a_ladder_and_real_time_is_on_it():
    """1.0 must be a member: it is the resting state, and `step_playback_rate` walks BACK to it.
    A ladder that only offered 0.5x and 0.25x would have no way to stop slowing down."""
    assert PLAYBACK_RATES == tuple(sorted(PLAYBACK_RATES)), PLAYBACK_RATES
    assert 1.0 in PLAYBACK_RATES, PLAYBACK_RATES
    assert min(PLAYBACK_RATES) < 1.0 < max(PLAYBACK_RATES), PLAYBACK_RATES
    # nearest_rate is the guard on the door: nothing outside the ladder can land on the picker.
    for asked, want in ((0.3, 0.25), (0.9, 1.0), (99.0, max(PLAYBACK_RATES)), (0.01, 0.25)):
        assert nearest_rate(asked) == want, (asked, nearest_rate(asked))
    print(f"test_the_rate_ladder_is_a_ladder_and_real_time_is_on_it OK ({PLAYBACK_RATES})")


def test_the_picker_the_shell_and_the_decoder_all_carry_one_rate():
    """A pick on the transport combo must reach the QMediaPlayer, and a programmatic set must
    reflect back onto the combo without re-entering the pick handler."""
    view = VideoView(_cmap("RATE"))
    for index, rate in enumerate(PLAYBACK_RATES):
        view.rate_combo.setCurrentIndex(index)          # a user pick
        _settle()
        assert view.playback_rate() == rate, (rate, view.playback_rate())
        assert view.pane.playback_rate() == rate, (rate, view.pane.playback_rate())
        assert view.pane.player.playbackRate() == rate, (rate, view.pane.player.playbackRate())
    for rate in reversed(PLAYBACK_RATES):
        view.set_playback_rate(rate)                    # a programmatic set (a key / the palette)
        _settle()
        assert view.rate_combo.currentIndex() == PLAYBACK_RATES.index(rate)
        assert view.pane.player.playbackRate() == rate
    view.stop_all()
    print("test_the_picker_the_shell_and_the_decoder_all_carry_one_rate OK")


def test_stepping_clamps_at_both_ends():
    """`[` held at the bottom of the ladder must not wrap round to 2x."""
    view = VideoView(_cmap("STEP"))
    for _ in range(len(PLAYBACK_RATES) + 3):
        view.step_playback_rate(-1)
    assert view.playback_rate() == min(PLAYBACK_RATES), view.playback_rate()
    for _ in range(len(PLAYBACK_RATES) + 3):
        view.step_playback_rate(+1)
    assert view.playback_rate() == max(PLAYBACK_RATES), view.playback_rate()
    view.stop_all()
    print("test_stepping_clamps_at_both_ends OK")


# ------------------------------------------------------------------ compare mode
def _enter_compare(view):
    spec_a = PaneSpec(0, (0.0, 40.0), "lap 0", choices=[0, 1])
    spec_b = PaneSpec(1, (40.0, 80.0), "lap 1", choices=[0, 1])
    view.set_compare(spec_a, spec_b)
    _settle()


def test_entering_compare_at_a_slow_rate_seeds_the_second_pane():
    """The failure this exists for: the secondary pane is created LAZILY on first entry, so a rate
    set before entering compare would never reach it and the two laps would run a factor of four
    apart with one number on screen claiming to describe both."""
    view = VideoView(_cmap("COMPARE"))
    view.set_playback_rate(0.25)
    _enter_compare(view)
    assert view.secondary is not None
    assert view.secondary.playback_rate() == 0.25, view.secondary.playback_rate()
    assert view.secondary.player.playbackRate() == 0.25
    view.stop_all()
    print("test_entering_compare_at_a_slow_rate_seeds_the_second_pane OK")


def test_a_rate_change_while_comparing_reaches_both_panes():
    """...and the other order: comparing first, then reaching for slow motion."""
    view = VideoView(_cmap("COMPARE2"))
    _enter_compare(view)
    for rate in PLAYBACK_RATES:
        view.set_playback_rate(rate)
        _settle()
        got = (view.pane.player.playbackRate(), view.secondary.player.playbackRate())
        assert got == (rate, rate), (rate, got)
    view.stop_all()
    print("test_a_rate_change_while_comparing_reaches_both_panes OK")


# ------------------------------------------------------------------ the chapter seam
def test_a_chapter_seam_re_applies_the_rate():
    """A cross-chapter seek REPLACES the media source, and a source carries no playback rate. The
    re-apply lands in `_apply_pending`, which is the single place a genuine load is honoured (the
    normal path and the bounded-resume watchdog both go through it), so one line covers both."""
    pane = PlayerPane(_cmap("SEAM"))
    pane.set_playback_rate(0.5)
    assert pane.player.playbackRate() == 0.5
    # A source switch through the production path: seek into chapter 1, which defers the seek.
    pane.seek(150.0)
    assert pane._pending is not None, "the cross-chapter seek did not defer"
    pane.player.setPlaybackRate(1.0)          # what a real backend does to a new source
    pane._apply_pending(*pane._pending[1:])   # the genuine load lands
    assert pane.playback_rate() == 0.5, pane.playback_rate()
    assert pane.player.playbackRate() == 0.5, pane.player.playbackRate()
    pane.dispose()
    print("test_a_chapter_seam_re_applies_the_rate OK")


# ================================================================== THE TELEMETRY LOCK
def test_nothing_downstream_of_the_player_reads_the_rate():
    """(a) THE STRUCTURAL HALF. The lock cannot be a function of the rate if no module in the sync
    path knows the rate exists.

    Every module between the decoder and the pixels is swept for the names this feature added
    (`playback_rate`, `set_playback_rate`, `setPlaybackRate`, `step_playback_rate`,
    `PLAYBACK_RATES`). The two allowed owners are the pane that sets it on its player and the shell
    that fans it out; `app` binds the keys. Everything else — the tick, the two controllers, the
    session, the map and the charts — must not name it at all, which is what makes "the marker
    stays on the kart at 1/4 speed" a property of the design rather than of a measurement."""
    names = {"playback_rate", "set_playback_rate", "setPlaybackRate", "step_playback_rate",
             "PLAYBACK_RATES", "nearest_rate", "slower_playback", "faster_playback"}
    owners = {"player_pane", "video_view", "app", "help_dialog"}
    downstream = ["central_view", "scrub_controller", "compare_controller", "playback_state",
                  "session", "timeline", "map_view", "plots_view", "lap_table", "export_video"]
    offenders = {}
    for module in downstream:
        src = open(os.path.join(_STUDIO, module + ".py"), encoding="utf-8").read()
        tree = ast.parse(src, module)
        hits = sorted({node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
                       and node.attr in names}
                      | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
                         and node.id in names})
        if hits:
            offenders[module] = hits
    assert not offenders, (
        f"the telemetry-sync path reads the playback rate: {offenders}. The lock holds because "
        f"the tick is driven by the decoder's OWN reported media position, not by a wall clock — "
        f"a module here that knows the rate is a module that can get that wrong. Owners: "
        f"{sorted(owners)}")
    print(f"test_nothing_downstream_of_the_player_reads_the_rate OK "
          f"({len(downstream)} modules swept for {len(names)} names)")


def test_the_same_positions_produce_the_same_telemetry_at_every_rate():
    """(b) THE BEHAVIOURAL HALF, on the REAL CentralView through the production signal path.

    The same sequence of media positions is pushed through `PlayerPane.positionChanged` at 0.25x,
    1x and 2x, ticking the real ~30 Hz work between each, and the resulting cursor state — the
    applied playhead, the map marker's scene position, the chart cursor's cached time — must be
    IDENTICAL, value for value. That is what "the marker stays on the kart in slow motion" means
    operationally: the marker is a function of the media time, and of nothing else.

    Positions, not wall time, are what a rate changes the SPACING of. So this drives the same
    positions at every rate deliberately: if the pipeline had any dependence on the rate (a
    predicted next position, an extrapolation between ticks, a seconds-per-tick assumption) the
    three runs would differ despite identical input, which is exactly the defect to catch."""
    from test_central_view_realqt import _real_central_view

    _view, session, t0, _t1 = _real_central_view()[:4]
    view = _view
    view.resize(1200, 800)
    view.show()
    _settle(6)
    # A dozen positions spread across lap 0, on the media clock the session actually serves.
    stops = [float(t0[i]) for i in range(0, len(t0), max(1, len(t0) // 12))][:12]
    runs = {}
    for rate in (1.0, 0.25, 2.0):
        view.video.set_playback_rate(rate)
        _settle(2)
        trace = []
        for t in stops:
            # THE PRODUCTION PATH: the pane's own position handler -> positionChanged ->
            # CentralView._on_position -> the tick. Not a direct call to _apply_position.
            view.video.pane._on_position(int(round(t * 1000)))
            view.tick()
            _settle(1)
            marker = view.map.marker.pos()
            trace.append((round(view._playback.applied_t, 9),
                          round(float(marker.x()), 9), round(float(marker.y()), 9),
                          None if view.plots._cursor_t is None
                          else round(float(view.plots._cursor_t), 9),
                          session.lap_at_time(view._playback.applied_t)))
        runs[rate] = trace
    view.hide()
    base = runs[1.0]
    for rate, trace in runs.items():
        assert trace == base, (
            f"at {rate}x the telemetry landed somewhere else for the SAME media positions:\n"
            f"  1x:    {base}\n  {rate}x: {trace}")
    # ...and the trace has to be non-trivial, or "identical" would be free.
    assert len({row[1] for row in base}) > 1, f"the marker never moved: {base}"
    print(f"test_the_same_positions_produce_the_same_telemetry_at_every_rate OK "
          f"({len(stops)} positions x {len(runs)} rates, marker moved through "
          f"{len({row[1] for row in base})} places)")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} PLAYBACK-RATE TESTS PASSED", flush=True)
