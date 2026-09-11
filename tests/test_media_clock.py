"""The telemetry->media clock conversion, and the two consumer seams that apply it.

Pacer times laps on the GPS9 TRUE clock and the video plays on the MEDIA clock; the two drift
apart by up to 0.22 s over a long recording (studio/media_clock.py has the measurements). These
tests pin the fit, the guards that refuse a nonsense one, and — the part that was actually broken —
that the EXPORTER and the PLAYER convert at their seam instead of handing a telemetry time straight
to ffmpeg / QMediaPlayer.

Two things they deliberately also pin:

  * the conversion NEVER reaches the timing maths. `Session.lap_window` and every per-sample series
    stay on the true clock, so `docs/ACCURACY.md`'s transponder validation is untouched.
  * a session/map with no clock converts by IDENTITY, so every duck-typed stand-in in the other
    suites (and a GPS5 camera, which has no true clock to diverge from) behaves exactly as before.

Real widgets for the player half (PACER_NO_MEDIA=1, offscreen). Run:
    python tests/test_media_clock.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import chapters, media_clock  # noqa: E402
from studio import export_video as ev  # noqa: E402
from studio.player_pane import PlayerPane  # noqa: E402

# The measured D24 numbers this module exists for: the media clock runs ~27 ppm fast, so over an
# 84-minute recording the picture and the telemetry drift ~0.17 s apart at the last lap.
D24_RATE = 1.0 + 27.1e-6
D24_SPAN_S = 4676.0


def _axes(rate=D24_RATE, offset=-0.0045, span=D24_SPAN_S, hz=10.0, t0=167.0):
    """A telemetry axis and the media axis it maps to under a known affine clock."""
    tel = t0 + np.arange(0.0, span, 1.0 / hz)
    return tel, rate * tel + offset


# ----------------------------------------------------------------------------- the fit
def test_fit_recovers_a_planted_clock():
    tel, med = _axes()
    c = media_clock.fit(tel, med)
    assert abs(c.rate - D24_RATE) < 1e-12
    assert abs(c.offset - (-0.0045)) < 1e-6
    # …and it is the map, not an approximation of it: the whole 0.13 s of drift is reproduced.
    assert abs(c.to_media(tel[-1]) - med[-1]) < 1e-6
    assert abs(c.correction_at(tel[-1]) - (med[-1] - tel[-1])) < 1e-9


def test_round_trip_is_exact():
    c = media_clock.MediaClock(rate=D24_RATE, offset=-0.0045)
    for t in (0.0, 241.7878, 2471.78, 4704.2956):
        assert abs(c.to_telemetry(c.to_media(t)) - t) < 1e-9
        assert abs(c.to_media(c.to_telemetry(t)) - t) < 1e-9


def test_fit_of_identical_axes_is_identity():
    """A GPS5 camera never leaves the media clock: `_gps9_times` returns the naive times unchanged,
    so there is nothing to convert and the fit must say so rather than return 1+1e-16."""
    tel, _ = _axes()
    c = media_clock.fit(tel, tel.copy())
    assert c.is_identity
    assert c.to_media(1234.5) == 1234.5


def test_fit_refuses_a_short_or_tiny_trace():
    """A rate needs a lever arm. 27 ppm over a few seconds is microseconds of signal under 50 ms of
    payload-packing noise — fitting it would be fitting the noise."""
    tel = np.arange(0.0, 5.0, 0.1)                       # 50 samples, 5 s
    assert media_clock.fit(tel, tel * D24_RATE).is_identity
    tel = np.arange(0.0, 30.0, 0.1)                      # 300 samples but only 30 s
    assert media_clock.fit(tel, tel * D24_RATE).is_identity
    assert media_clock.fit([], []).is_identity
    tel, med = _axes()
    assert media_clock.fit(tel, med[:-1]).is_identity    # mismatched lengths


def test_fit_refuses_an_impossible_rate():
    """0.5% is 185x the measured camera drift — that is a broken stream, not a clock, and the app
    must not move a user's video by 20 s on the strength of it."""
    tel, _ = _axes()
    assert media_clock.fit(tel, tel * 1.005).is_identity


def test_fit_refuses_a_relation_that_is_not_affine():
    """The whole design rests on the relation BEING affine (measured: smoothing the residual over
    300 s leaves 1.5-2.5 ms). A fit whose residual is seconds is describing something else."""
    tel, med = _axes()
    rng = np.random.default_rng(7)
    assert media_clock.fit(tel, med + rng.normal(0.0, 3.0, tel.size)).is_identity


def test_fit_survives_the_payload_packing_sawtooth():
    """THE REASON THIS IS A FIT AND NOT A PER-SAMPLE LOOKUP.

    A GPMF payload spans 1.001 s and holds 9, 10 or 11 GPS fixes; ingest lays them EVENLY across
    that span, so the naive media time of a fix is quantised and wrong by up to ±0.05 s whenever a
    payload does not hold exactly 10 (measured on both D24 recordings: 28 ms rms, up to 73 ms).
    Reproduced here — the fit must see through it to the real 27 ppm rate, which a lookup on the
    same axis cannot."""
    hz, payload = 10.0, 1.001
    tel, true_med = _axes(hz=hz, t0=0.0)
    naive = np.empty_like(true_med)
    i, start = 0, 0.0
    while i < len(naive):
        n = int(np.searchsorted(true_med, start + payload) - i)   # fixes landing in this payload
        n = max(min(n, len(naive) - i), 1)
        naive[i:i + n] = start + payload * np.arange(n) / n       # ingest's even layout
        start += payload
        i += n
    sawtooth = naive - true_med
    assert 0.01 < sawtooth.std() < 0.05          # the real recordings measure 0.028 s rms
    assert np.abs(sawtooth).max() > 0.04         # …and up to ±0.05 s on a single fix

    # WHAT THE FIT IS ALLOWED TO KEEP is a CONSTANT: where a fix sits inside its 1.001 s payload is
    # not recorded anywhere, so no method can place it better than ±0.05 s, and that half-payload
    # phase is the same at the start of a recording as at the end. What must not survive is the
    # part that GROWS — the 27 ppm ramp, which is the whole defect. So the test is on the SPREAD of
    # the error, not its value: the fit must be wrong by the same amount everywhere.
    c = media_clock.fit(tel, naive)
    err = c.to_media(tel) - true_med
    assert not c.is_identity
    assert err.max() - err.min() < 0.005, err.max() - err.min()
    # A per-sample lookup on the same axis keeps the whole sawtooth — an error that varies by a
    # tenth of a second between one lap and the next, i.e. 3 frames of jitter at 30 fps.
    lookup_err = np.interp(tel, tel, naive) - true_med
    assert lookup_err.max() - lookup_err.min() > 0.08


def test_clock_of_duck_types_a_missing_clock():
    assert media_clock.clock_of(None) is media_clock.IDENTITY
    assert media_clock.clock_of(object()) is media_clock.IDENTITY

    class Odd:
        media_clock = "not a clock"
    assert media_clock.clock_of(Odd()) is media_clock.IDENTITY


# ----------------------------------------------------------------- it rides on the ChapterMap
def test_chapter_map_defaults_to_identity_and_carries_a_clock():
    plain = chapters.ChapterMap(["GX010060.MP4"], [60.0])
    assert plain.media_clock.is_identity
    c = media_clock.MediaClock(rate=D24_RATE, offset=0.0)
    carried = chapters.ChapterMap(["GX010060.MP4"], [60.0], media_clock=c)
    assert carried.media_clock is c
    # The offsets are MEDIA times and the clock never moves them (that is the video layer's own map).
    assert carried.chapters[0].offset == 0.0
    assert carried.to_local(30.0) == (0, 30.0)


# ----------------------------------------------------------------------- seam 1: the exporter
class _ClockedSession:
    """The minimum an export needs: one lap on the TELEMETRY clock plus the conversion."""

    def __init__(self, t0=4634.0421, t1=4704.2956, lap=63, rate=D24_RATE, offset=0.0):
        self._t0, self._t1, self._lap = t0, t1, lap
        self.clock = media_clock.MediaClock(rate=rate, offset=offset)
        self.tt = np.linspace(t0, t1, 200)
        self.tv = np.full(200, 60.0)
        self.has_gmeter = False
        self.asked = []                       # every time this session was asked ABOUT

    def media_time(self, t):
        return float(self.clock.to_media(float(t)))

    def telemetry_time(self, t):
        return float(self.clock.to_telemetry(float(t)))

    def lap_window(self, lap_id):
        return (self._t0, self._t1) if lap_id == self._lap else None

    def lap_at_time(self, t):
        self.asked.append(t)
        return self._lap if self._t0 <= t < self._t1 else None

    def index_at_time(self, t):
        self.asked.append(t)
        return int(np.clip(np.searchsorted(self.tt, t), 0, len(self.tt) - 1))

    def delta_at_lap(self, lap_id, t):
        self.asked.append(t)
        return 0.25


def test_export_window_is_converted_to_media_time():
    """The bug, in one assertion: the render window ffmpeg seeks with is the lap window ON THE
    MEDIA CLOCK. On the last lap of the 84-minute D24 recording that is ~0.13 s later than the
    telemetry time the exporter used to hand over — 4 frames at the default 30 fps, 8 at 59.94."""
    s = _ClockedSession()
    t0, t1 = ev.lap_window_for_export(s, 63)
    assert t0 == s.media_time(4634.0421)
    assert t1 == s.media_time(4704.2956)
    shift = t0 - 4634.0421
    assert 0.12 < shift < 0.14, shift
    # …and the LAP is not stretched by the conversion: a 70.253 s lap stays 70.253 s to 2 ms.
    assert abs((t1 - t0) - (4704.2956 - 4634.0421)) < 2e-3


def test_export_window_is_unchanged_without_a_clock():
    """Every duck-typed session in the other suites has no `media_time`; the funnel must then be
    exactly what it always was."""
    class NoClock:
        def lap_window(self, _):
            return (100.0, 160.0)
    assert ev.lap_window_for_export(NoClock(), 0) == (100.0, 160.0)


def test_overlay_values_are_read_at_the_frames_own_instant():
    """The other half of the fix. Once the window is media time, every frame time is too — so the
    per-frame lookups must convert BACK, or the error simply moves from the seek into the numbers
    burned over the picture."""
    s = _ClockedSession()
    media_t = s.media_time(4650.0)
    vals = ev.overlay_values_at(s, media_t)
    assert vals.t == media_t                                   # the frame keeps its media stamp
    assert all(abs(a - 4650.0) < 1e-6 for a in s.asked), s.asked


def test_strip_clock_reaches_the_lap_time_at_the_flag():
    """The burned-in lap clock counts on the TELEMETRY clock — the one the lap was timed on — so
    the last frame of an unpadded clip reads the lap time, not the lap time times 1.000027."""
    s = _ClockedSession()
    t0, t1 = ev.lap_window_for_export(s, 63)
    vals = ev.overlay_values_at(s, t1 - 1e-6)
    runs = ev._strip_runs(s, vals, t0, False, None)
    assert runs is not None
    _label, _tail, _colour, frac = runs
    assert abs(frac - 1.0) < 1e-6, frac


# -------------------------------------------------------------------------- seam 2: the player
def _pane(rate=D24_RATE, offset=0.0):
    cmap = chapters.ChapterMap(
        ["/tmp/GX010062.MP4", "/tmp/GX020062.MP4", "/tmp/GX030062.MP4"],
        [1729.728, 1729.728, 1590.0051],
        media_clock=media_clock.MediaClock(rate=rate, offset=offset))
    return PlayerPane(cmap), cmap


def test_player_seek_converts_to_media_time():
    """A lap seek arrives on the telemetry clock (that is what `Session.lap_window` says) and must
    reach QMediaPlayer as a MEDIA position."""
    pane, cmap = _pane()
    pane.seek(4704.2956)                       # lap 64's start, telemetry
    expected = cmap.media_clock.to_media(4704.2956) - cmap.chapters[2].offset
    # a cross-chapter target is deferred until the new source loads
    assert pane._pending is not None
    index, local, _resume = pane._pending
    assert index == 2
    assert abs(local - expected) < 1e-9
    assert local - (4704.2956 - cmap.chapters[2].offset) > 0.12   # ~0.13 s later than before


def test_player_position_converts_back_to_telemetry():
    """…and the position the app syncs telemetry to comes back on the telemetry clock, so the
    readout, the playhead and the lap highlight index the series they belong to."""
    pane, cmap = _pane()
    seen = []
    pane.positionChanged.connect(seen.append)
    pane._on_position(1_244_000)               # 1244.0 s into chapter 0
    assert seen and abs(seen[-1] - cmap.media_clock.to_telemetry(1244.0)) < 1e-9
    assert abs(pane.current_global_time() - seen[-1]) < 1e-12


def test_player_seek_and_position_are_inverses():
    """The round trip a scrub drag depends on: seek(t) then the player reporting that position must
    hand back t, not t plus a tenth of a second."""
    pane, cmap = _pane()
    for t in (12.5, 900.0, 1729.0):
        pane.seek(t)                                   # chapter 0 for all three
        ms = int(round(cmap.media_clock.to_media(t) * 1000))
        pane._on_position(ms)
        assert abs(pane.current_global_time() - t) < 2e-3


def _record_positions(pane) -> list:
    """The ms values the pane pushes into the media player (the inert stand-in keeps none)."""
    seen = []
    pane.player.setPosition = seen.append
    return seen


def test_player_without_a_clock_is_unchanged():
    """A single-file pane, a map built before this existed, a GPS5 recording: identity throughout."""
    cmap = chapters.ChapterMap(["/tmp/GX010060.MP4"], [600.0])
    pane = PlayerPane(cmap)
    seen = _record_positions(pane)
    pane.seek(123.4)
    assert seen == [123_400]
    pane._on_position(123_400)
    assert abs(pane.current_global_time() - 123.4) < 1e-9


def test_player_same_chapter_seek_lands_on_the_media_time():
    """The in-chapter path (no source switch) converts too — it is the one a scrub drag hits 30
    times a second."""
    pane, cmap = _pane()
    seen = _record_positions(pane)
    pane.seek(1700.0)
    assert seen == [int(cmap.media_clock.to_media(1700.0) * 1000)]
    assert seen[0] > 1_700_000                       # later than the telemetry time, by ~46 ms


def test_cross_recording_pane_uses_its_own_recordings_clock():
    """Pane B in a cross-recording compare plays the REFERENCE footage, so it owes the reference
    recording's clock — which is why the clock rides on the ChapterMap the pane is opened with."""
    primary, _ = _pane(rate=D24_RATE)
    reference, ref_map = _pane(rate=1.0 + 60e-6)
    assert primary._clock() is not reference._clock()
    assert reference._clock() is ref_map.media_clock
    primary.seek(3000.0)
    reference.seek(3000.0)
    assert primary._pending[1] != reference._pending[1]


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} media-clock tests passed")


if __name__ == "__main__":
    _run_all()
