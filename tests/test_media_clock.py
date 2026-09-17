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
    # ...and it is the RATE gate that refuses it, not the correction cap standing in for it. Over the
    # 84-minute axis above the same 0.5 % is ~24 s of correction, so `MAX_CORRECTION_S` alone
    # rejects it and a rate gate loosened tenfold still passed. Ten minutes of the same broken
    # stream moves the video by under 4 s, inside the correction cap — only the rate can say no.
    short, _ = _axes(span=600.0)
    assert float(np.abs(short * 0.005).max()) < media_clock.MAX_CORRECTION_S
    assert media_clock.fit(short, short * 1.005).is_identity, (
        "a 0.5 % clock over ten minutes was accepted: the rate gate is not refusing it")


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

    def __init__(self, t0=4634.0421, t1=4704.2956, lap=63, rate=D24_RATE, offset=0.0,
                 gps_lag=0.0):
        self._t0, self._t1, self._lap = t0, t1, lap
        self.clock = media_clock.MediaClock(rate=rate, offset=offset, gps_lag=gps_lag)
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
def _pane(rate=D24_RATE, offset=0.0, gps_lag=0.0):
    cmap = chapters.ChapterMap(
        ["/tmp/GX010062.MP4", "/tmp/GX020062.MP4", "/tmp/GX030062.MP4"],
        [1729.728, 1729.728, 1590.0051],
        media_clock=media_clock.MediaClock(rate=rate, offset=offset, gps_lag=gps_lag))
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


# ================================================== seam 3: the GPS timestamps' own lag (X3)
# The fit above is a RATE problem. This is a different one on the same axis: the GPS receiver's
# timestamp names an instant LATER than the one the picture shows. studio/rotation.py measures it
# against the camera's own gyroscope — which rides the picture — at +0.476 s (D24 0060) and
# +0.459 s (0062), per chapter and per lap, with no step at a chapter seam. Uncorrected, every
# GPS-derived overlay value is painted against a frame ~14 of them late at 30 fps.
D24_LAG = 0.4764


def test_the_lag_moves_the_picture_trace_mapping_by_a_constant():
    """It is a SHIFT, not a rate: both ends of a lap move by the same amount, the inverse stays
    exact (a seek and the lookup that follows it must agree), and the pure map is still there for
    the measurement to run on."""
    pure = media_clock.MediaClock(rate=D24_RATE, offset=-0.0045)
    lagged = pure.with_gps_lag(D24_LAG)
    t = 2000.0
    assert abs((pure.to_media(t) - lagged.to_media(t)) - D24_LAG) < 1e-12
    assert abs(lagged.to_telemetry(lagged.to_media(t)) - t) < 1e-9
    assert abs((lagged.to_media(t + 70.0) - lagged.to_media(t))
               - (pure.to_media(t + 70.0) - pure.to_media(t))) < 1e-12
    # THE MEASUREMENT MUST NOT SEE ITS OWN CORRECTION, or a second build reads ~0 and the
    # correction justifies itself. `without_gps_lag` is what rotation is handed.
    assert lagged.without_gps_lag().gps_lag == 0.0
    assert lagged.without_gps_lag().to_media(t) == pure.to_media(t)
    assert not media_clock.MediaClock(gps_lag=D24_LAG).is_identity


def test_a_lag_that_is_not_a_measurement_is_refused():
    """None means "not measured", never zero; and a value no receiver latency explains must not
    move a user's video. Each refusal keeps the pure map — the behaviour before this existed."""
    pure = media_clock.MediaClock(rate=D24_RATE, offset=0.0)
    assert pure.with_gps_lag(None) is pure
    assert pure.with_gps_lag(float("nan")) is pure
    assert pure.with_gps_lag(float("inf")) is pure
    assert pure.with_gps_lag(media_clock.MAX_GPS_LAG_S + 0.01) is pure
    assert pure.with_gps_lag(-media_clock.MAX_GPS_LAG_S - 0.01) is pure
    # …and installing twice REPLACES rather than compounding (a re-measured recording is not 2x).
    assert abs(pure.with_gps_lag(D24_LAG).with_gps_lag(D24_LAG).gps_lag - D24_LAG) < 1e-12


def test_the_export_window_and_the_frame_lookups_move_together_by_the_lag():
    """The exporter's two crossings, checked as one: the window seeks `lag` earlier, and the frame
    at that seek is described by the trace sample it is a picture OF. Uncorrected, the same frame
    is described by a sample from half a second earlier — which is the defect, in numbers."""
    plain, lagged = _ClockedSession(), _ClockedSession(gps_lag=D24_LAG)
    t0p, t1p = ev.lap_window_for_export(plain, 63)
    t0l, t1l = ev.lap_window_for_export(lagged, 63)
    assert abs((t0p - t0l) - D24_LAG) < 1e-9, (t0p, t0l)
    assert abs((t1p - t1l) - D24_LAG) < 1e-9, (t1p, t1l)
    assert abs((t1l - t0l) - (t1p - t0p)) < 1e-9, "the lap must not be stretched by a shift"

    ev.overlay_values_at(lagged, t0l)
    assert lagged.asked and all(abs(a - 4634.0421) < 1e-6 for a in lagged.asked), lagged.asked
    ev.overlay_values_at(plain, t0l)          # the SAME frame, through the uncorrected mapping
    assert all(a - 4634.0421 < -0.4 for a in plain.asked), plain.asked


def test_the_player_seeks_the_frame_the_trace_sample_is_a_picture_of():
    """The live seam, through the real pane: the same telemetry instant reaches QMediaPlayer
    `lag` earlier, and the position coming back is the instant that frame shows. The exporter and
    the player read ONE map, so a burned clip and the app cannot disagree."""
    plain, _ = _pane()
    lagged, _ = _pane(gps_lag=D24_LAG)
    sp, sl = _record_positions(plain), _record_positions(lagged)
    plain.seek(1700.0)
    lagged.seek(1700.0)
    assert abs((sp[0] - sl[0]) / 1000.0 - D24_LAG) < 2e-3, (sp, sl)
    lagged._on_position(sl[0])
    assert abs(lagged.current_global_time() - 1700.0) < 2e-3


def test_a_measured_lag_is_installed_on_the_recordings_own_clock():
    """The wiring, through the real method: rotation measures, the session folds it onto the map
    the video layer holds. Every refusal path keeps the pure fit rather than half-applying one."""
    from studio import rotation
    from studio.session import Session

    def _clock_after(cross, rate=D24_RATE, offset=0.02):
        s = Session.__new__(Session)
        s.chapters = chapters.ChapterMap(
            ["GX010060.MP4"], [3000.0],
            media_clock=media_clock.MediaClock(rate=rate, offset=offset))
        if cross is not _NO_ROTATION:
            s._rotation = rotation.Rotation(times=np.zeros(1), yaw_rate=np.zeros(1), cross=cross)
        s._install_gps_lag()
        return s.media_clock

    def _cross(lag):
        return rotation.RotationCheck(
            n=26562, corr=0.87, gain=0.89, corner_n=9000, corner_corr=0.946, corner_gain=0.87,
            straight_n=8000, straight_rms_gyro=0.24, straight_rms_path=0.05,
            straight_mean_gyro=0.026, loop_n=38, loop_ratio_gyro=0.983, loop_ratio_path=1.001,
            ok=True, gps_lag_s=lag, lag_corr=0.917, lag_corr_at_zero=0.854)

    installed = _clock_after(_cross(D24_LAG))
    assert abs(installed.gps_lag - D24_LAG) < 1e-12
    assert abs(installed.rate - D24_RATE) < 1e-15 and abs(installed.offset - 0.02) < 1e-15
    # NOT MEASURED IS NOT ZERO, in all three of its shapes.
    assert _clock_after(_cross(None)).gps_lag == 0.0          # gyro that never tracked the path
    assert _clock_after(None).gps_lag == 0.0                  # no cross-check at all
    assert _clock_after(_NO_ROTATION).gps_lag == 0.0          # no GYRO stream / a failed build
    # …and a measurement past the bound is refused, loudly, instead of moving the video by it.
    assert _clock_after(_cross(media_clock.MAX_GPS_LAG_S + 0.5)).gps_lag == 0.0


def test_the_dial_and_the_speed_describe_the_same_frame():
    """WHICH LABEL THE PAINTED DIAL ASKS THE G SERIES FOR — and why it is not the frame's own
    media time.

    #301 installed this conversion on the premise that the g series, being stamped with the ACCL
    sample times, is PICTURE-TRUE on its own labels. MEASURED on both D24 recordings against the
    GYRO — the channel PR #291 settled against yaw taken from the frames themselves — that premise
    is false. `gm.lat_g` sits **+0.399 s (0060) / +0.406 s (0062) BEHIND the picture by label**:
    the accelerometer's content arrives carrying very nearly the same delay the GPS timestamps
    carry. #303 measured the same thing from the other side (against the path, where it reads
    +0.011 / −0.047 by label) and refused the matching "fix" in `driving_channels` because of it.

    So a frame's own media time is the WRONG label to ask this series for, and asking for it put
    the dial **+0.386 / +0.393 s behind the speed painted beside it** — measured per frame against
    the picture, with the trace channel beside it reading +0.004 / −0.001 s.

    The accessor therefore crosses the PURE two-clock map: the 27 ppm rate difference between the
    two axes is real and is corrected, while the GPS-timestamp lag is NOT undone here, because the
    g series carries it too. After this the dial reads −0.078 / −0.052 s from the picture.

    The fixture's g VALUE is its own timestamp, so the sample that comes back says which instant
    was asked for."""
    from studio import gmeter
    from studio.session import Session

    clock = media_clock.MediaClock(rate=D24_RATE, offset=0.02, gps_lag=D24_LAG)
    s = Session.__new__(Session)
    s.chapters = chapters.ChapterMap(["GX010060.MP4"], [3000.0], media_clock=clock)
    gt = np.arange(0.0, 3000.0, 0.02)
    s._gmeter = gmeter.GMeter(times=gt, lat_g=gt.copy(), long_g=np.zeros_like(gt), cross=None)

    t_trace = 300.0
    # The label the g series is asked for: the rate fit ALONE, with the lag left in the trace.
    asked = clock.without_gps_lag().to_media(t_trace)
    lat, _lon, _total = s.g_at_time(t_trace)
    assert abs(lat - asked) < 0.02, (lat, asked)
    # The two clocks' RATE is still crossed — this is not a no-op that just returns t_trace.
    # (The series is on a 0.02 s grid and `at_time` picks the nearest sample, so the visible gap
    # is that grid step rather than the map's own 0.028 s at this instant.)
    assert asked != t_trace and abs(lat - t_trace) > 0.01, (lat, t_trace)
    # …and it is NOT the frame's own media time. That gap is the whole GPS-timestamp lag, and it
    # is the direction that left the dial a third of a second behind the speed beside it.
    assert abs(lat - clock.to_media(t_trace)) > 0.4, (lat, clock.to_media(t_trace))


def test_a_session_with_no_chapter_map_still_answers_for_the_dial():
    """WHAT THE SYNTHETIC GOLDEN GATE CAUGHT, kept as a test of its own.

    `g_at_time` crosses the clock now, and a BARE Session — `Session.__new__` with no load behind
    it, which is what the synthetic fixtures and half the suites build — has no `chapters`
    attribute at all. The first version of this fix read `self.chapters` and raised there, and the
    fingerprint's guarded dump recorded `__unsupported__` for the leaf instead of numbers: a clock
    dependency does not move a golden leaf, it DELETES one, which is the quieter failure."""
    from studio import gmeter
    from studio.session import Session

    s = Session.__new__(Session)
    gt = np.arange(0.0, 10.0, 0.02)
    s._gmeter = gmeter.GMeter(times=gt, lat_g=gt.copy(), long_g=np.zeros_like(gt), cross=None)
    assert s.media_clock.is_identity          # no map at all -> no conversion, as before
    assert s.gps_lag_applied_s is None
    assert abs(s.g_at_time(4.0)[0] - 4.0) < 0.02


# ------------------------------------------------- which map: the accelerometer is not the gyro
def test_the_accelerometer_is_placed_on_its_own_clock_not_the_gyros():
    """The dev probes had ONE inertial conversion — the gyro's, which takes the GPS lag out — and
    `_accel` placed every accelerometer sample on the track through it.

    GYRO and ACCL are stamped together on one sample grid, and their CONTENT still rides different
    maps. Measured with `rotation.measure_lag` against the path on both D24 recordings, + = the
    path runs behind: the gyro reads +0.007 / +0.002 s through its map; raw |ACCL horizontal|
    against |v · ω_path| reads **+0.095 / +0.053 s through the stamp map and −0.382 / −0.406 s
    through the gyro's** (r 0.905 / 0.921 either way). So P2's and P3's roughness sat ~0.4 s —
    ~7 m at the recordings' median speed, one of P3's 5.3 m bins — from where it was measured, and
    P3's cross-session r moves 0.945 -> 0.952 once it does not.

    NEGATIVE CONTROL, watched: `lap_windows` back on `_align.to_gyro_clock` fails the first assert
    with the lag itself as the gap."""
    from types import SimpleNamespace

    from studio.dev.probes import _accel, _align

    t = np.linspace(1000.0, 1070.0, 701)
    d = np.linspace(0.0, 1058.0, 701)
    zeros = np.zeros_like(t)
    rec = SimpleNamespace(clock_rate=D24_RATE, clock_offset=0.0256, gps_lag_s=D24_LAG,
                          laps=lambda: iter([(0, (t, zeros, zeros, zeros + 15.0, d))]))
    stamp = media_clock.MediaClock(rate=D24_RATE, offset=0.0256).to_media(t)

    (windows, _frac), = _accel.lap_windows(rec)
    gap = float(np.max(np.abs(windows - stamp)))
    assert gap < 1e-9, f"ACCL lap windows are {gap:+.4f} s off the stamp map (the lag is {D24_LAG})"
    # The two probe maps really are the lag apart, so the assert above can tell them apart.
    assert abs(float(np.median(_align.to_gyro_clock(rec, t) - stamp)) + D24_LAG) < 1e-9
    assert np.array_equal(_align.to_accl_clock(rec, t), stamp)
    # And a sample stamped at the moment the lap was half done lands half-way round the lap.
    tq = np.array([float(np.interp(0.5, d / d[-1], stamp))])
    frac, lap = _accel.to_track_fraction(rec, tq)
    assert lap[0] == 0 and abs(float(frac[0]) - 0.5) < 1e-9, (frac, lap)


# Every executable clock crossing under `studio/` (dev probes included), keyed by (file, enclosing
# function, callee), with the MAP it takes and why. Both maps land on the same media axis, so no
# name can say which one a call needs (`studio/media_clock.py`, "WHICH MAP"): #301 put the dial on
# the wrong one, #314 put the quality strip on the wrong one, and the probes put the accelerometer
# on the wrong one. A new crossing fails `test_every_clock_crossing_is_declared_with_the_map_it_takes`
# until it is written here with its reason — which is the point: the question gets asked.
#   "picture" — the footage position whose frame shows the event (the lag is in it)
#   "stamp"   — the camera's own media stamp (the rate fit alone)
_P, _S = ("picture",), ("stamp",)
CROSSINGS = {
    # --- the maps themselves
    ("studio/media_clock.py", "MediaClock.correction_at", "to_media"):
        (_P, "states the installed map's own correction"),
    ("studio/session.py", "Session.media_time", "to_media"): (_P, "THE picture map"),
    ("studio/session.py", "Session.telemetry_time", "to_telemetry"): (_P, "its exact inverse"),
    # --- seeking and painting the picture
    ("studio/player_pane.py", "PlayerPane.seek", "to_media"): (_P, "a seek lands on a frame"),
    ("studio/player_pane.py", "PlayerPane._on_position", "to_telemetry"):
        (_P, "the frame on screen -> the trace sample it shows"),
    ("studio/export_video.py", "lap_window_for_export", "_media_time"):
        (_P, "the exported window is footage"),
    ("studio/export_video.py", "overlay_values_at", "_telemetry_time"):
        (_P, "a burned frame -> the trace sample it shows; g crosses back itself (g_at_time)"),
    ("studio/export_video.py", "_strip_runs", "_telemetry_time"):
        (_P, "a burned frame's lap clock, like the live one"),
    ("studio/export_compare.py", "lock_to_track", "_telemetry_time"): (_P, "pane A's frame"),
    ("studio/export_compare.py", "lock_to_track", "_media_time"):
        (_P, "pane B's frame, on pane B's own recording's map"),
    ("studio/session.py", "Session.lap_channels", "to_media"):
        (_P, "the channels CSV's t_video_s: where in the footage each row is"),
    # --- indexing a series stamped on the camera's clock whose content carries the GPS delay
    ("studio/session.py", "Session.g_at_time", "without_gps_lag"):
        (_S, "ACCL content: +0.072 / +0.040 s stamp vs -0.409 / -0.418 s picture (#309, T9)"),
    ("studio/session.py", "Session.g_at_time", "to_media"): (_S, "the call on that map"),
    ("studio/session.py", "Session._build_rotation", "without_gps_lag"):
        (_S, "the lag is measured on the map it is not yet in, or it measures itself"),
    ("studio/rotation.py", "_cross_check", "to_media"): (_S, "the map `_build_rotation` hands in"),
    ("studio/stats_panel.py", "video_sync_row", "without_gps_lag"):
        (_S, "states the rate fit on its own, apart from the lag"),
    # --- dev probes: two streams, two maps (`studio/dev/probes/_align.py`)
    ("studio/dev/probes/_align.py", "to_gyro_clock", "to_media"):
        (_S, "the stamp map, then the lag subtracted: the gyro's picture map, spelled out"),
    ("studio/dev/probes/_align.py", "from_gyro_clock", "to_telemetry"):
        (_S, "its inverse, lag added back first"),
    ("studio/dev/probes/_align.py", "to_accl_clock", "to_media"):
        (_S, "ACCL content: +0.095 / +0.053 s stamp vs -0.382 / -0.406 s picture"),
    ("studio/dev/probes/_align.py", "residual_lag", "to_gyro_clock"):
        (_P, "gyro content: +0.007 / +0.002 s picture vs +0.476 / +0.459 s stamp"),
    ("studio/dev/probes/_align.py", "residual_lag", "to_media"):
        (_S, "the uncorrected control reading beside it"),
    ("studio/dev/probes/_accel.py", "lap_windows", "to_accl_clock"):
        (_S, "places ACCL samples on the track (P2, P3)"),
    ("studio/dev/probes/p1_sideslip.py", "_residual_lag_sweep", "to_gyro_clock"):
        (_P, "gyro vs path"),
    ("studio/dev/probes/p1_sideslip.py", "_loop_ratios", "to_gyro_clock"): (_P, "gyro vs path"),
    ("studio/dev/probes/p1_sideslip.py", "analyse", "to_gyro_clock"): (_P, "gyro vs path"),
    ("studio/dev/probes/p4_corner_gps_quality.py", "probe_recording", "without_gps_lag"):
        (_S, "the quality strip's own axis; crossing media_time here was #314's 9 of 456 (#318)"),
    ("studio/dev/probes/p4_corner_gps_quality.py", "probe_recording", "to_media"):
        (_S, "the call on that map"),
    ("studio/dev/probes/p5_clock_crossing_scale.py", "_joins", "without_gps_lag"):
        (_S, "the 'pure' join, which the per-fix residual shows is the strip's axis"),
    ("studio/dev/probes/p5_clock_crossing_scale.py", "_joins", "to_media"):
        (("stamp", "picture"), "compares both maps against the fixes' own stamps, on purpose"),
}
# Callees whose NAME fixes the map, checked against the declaration so the table cannot drift
# from the code it describes.
_PICTURE_BY_NAME = {"media_time", "_media_time", "telemetry_time", "_telemetry_time",
                    "to_gyro_clock"}
_STAMP_BY_NAME = {"to_accl_clock", "without_gps_lag"}
_CROSSING_NAMES = _PICTURE_BY_NAME | _STAMP_BY_NAME | {"to_media", "to_telemetry",
                                                       "from_gyro_clock"}


def _clock_crossings():
    """-> {(relpath, scope, callee): [ast.Call, ...]} over every module under `studio/`."""
    import ast

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    found: dict = {}
    for root, _dirs, files in os.walk(os.path.join(repo, "studio")):
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, repo).replace(os.sep, "/")
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())

            def walk(node, scope, rel=rel):
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        walk(child, [*scope, child.name])
                        continue
                    if isinstance(child, ast.Call):
                        fn = child.func
                        callee = (fn.attr if isinstance(fn, ast.Attribute)
                                  else fn.id if isinstance(fn, ast.Name) else None)
                        if callee in _CROSSING_NAMES:
                            key = (rel, ".".join(scope) or "<module>", callee)
                            found.setdefault(key, []).append(child)
                    walk(child, scope)

            walk(tree, [])
    return found


def test_every_clock_crossing_is_declared_with_the_map_it_takes():
    """THE GUARD A NAME COULD NOT BE. Every call that crosses between the telemetry clock and the
    media axis is in `CROSSINGS` with the map it takes — picture or stamp — and the measured reason.

    Both directions, like `tests/test_layering.py`: a crossing the table does not list fails, and so
    does an entry that no longer matches any call, so the table cannot quietly describe a codebase
    that has moved. Where the code itself fixes the map — a callee NAMED for one, or a call chained
    through `without_gps_lag()` — the declaration must agree with it.

    NEGATIVE CONTROL, watched: `_accel.lap_windows` put back on `_align.to_gyro_clock` (the defect
    T9 fixed) fails with that call undeclared AND the `to_accl_clock` entry stale."""
    import ast

    found = _clock_crossings()
    undeclared = sorted(k for k in found if k not in CROSSINGS)
    stale = sorted(k for k in CROSSINGS if k not in found)
    assert not undeclared and not stale, (
        "every clock crossing must be declared with the map it takes (see CROSSINGS and "
        f"studio/media_clock.py 'WHICH MAP'):\n  undeclared {undeclared}\n  stale {stale}")
    disagree = []
    for key, calls in found.items():
        maps, _why = CROSSINGS[key]
        callee = key[2]
        if callee in _PICTURE_BY_NAME and "picture" not in maps:
            disagree.append((key, "named for the picture map"))
        if callee in _STAMP_BY_NAME and "stamp" not in maps:
            disagree.append((key, "named for the stamp map"))
        for call in calls:
            chain = call.func.value if isinstance(call.func, ast.Attribute) else None
            through_stamp = chain is not None and any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "without_gps_lag" for n in ast.walk(chain))
            if through_stamp and "stamp" not in maps:
                disagree.append((key, f"line {call.lineno} is chained through without_gps_lag()"))
    assert not disagree, f"a declaration contradicts its own call: {disagree}"
    print(f"ok {len(found)} clock crossings, each declared with its map")


class _NO_ROTATION:   # noqa: N801 — a sentinel, not a class anyone instantiates
    """`Session` with no `_rotation` attribute at all: `_build_rotation` sets it inside its try."""


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} media-clock tests passed")


if __name__ == "__main__":
    _run_all()
