"""The telemetry->media clock conversion: ONE affine map per recording.

Pacer times laps on the GPS9 TRUE clock — the camera's GPS receiver stamps every fix with its own
time, and timing laps on that clock is what `docs/ACCURACY.md` validates against a transponder. The
VIDEO plays on the MEDIA clock. **They are not the same clock**, and the difference is not a
constant: on both D24 recordings the media clock runs ~27 ppm fast against the GPS clock, so a
telemetry instant and the picture of it drift apart by up to 0.15 s over a 49-minute recording and
0.22 s over an 84-minute one. Ask the video for "the moment lap 64 starts" with a telemetry time and
it answers with a frame ~5 frames early at the app's default 30 fps export (~10 at the GoPro's
59.94). That is the app's largest overlay-vs-picture error, and this module is the conversion that
closes it.

NOTHING HERE TOUCHES LAP TIMING. A lap time is still (finish instant − start instant) on the true
clock, to the last bit. This converts only the SEEK: the moment a consumer hands a time to ffmpeg or
to the media player.

WHY AFFINE, AND NOT A PER-SAMPLE LOOKUP. `studio/load.py` has both axes per sample — the true-clock
time and the naive media time the GPMF payload layout gives — so the obvious conversion is to
interpolate one against the other. Measured, that is the worse answer, because the naive per-sample
time is itself quantised:

  * a GPMF payload spans 1.001 s of media and holds whatever GPS fixes arrived during it — 10
    usually, but 9 or 11 often enough (on 0062: 4458 payloads of 10, 84 of 9, 129 of 11);
  * `ingest._read_gps_over` lays a payload's n fixes EVENLY across its span, so it spaces them
    1.001/n apart — 0.0910 s at n=11 and 0.1112 s at n=9, measured as the 1st/99th percentile of the
    naive spacing on both recordings;
  * the fixes are really 0.1000 s apart (the GPS9 spacing's 1st-99th percentile is 0.099-0.101), and
    the two clocks differ by 27 ppm, so in media time they are 0.1000 s apart too. The naive
    per-sample time is therefore wrong by up to ±0.05 s whenever a payload does not hold exactly 10,
    and that error is what the difference between the two axes carries as a ±0.05 s sawtooth on top
    of the ramp (28 ms rms, up to 73 ms, on both recordings).

The ramp underneath it IS affine. Smoothing the affine fit's residual over 300 s — long enough to
average the packing sawtooth away — leaves 1.5 ms rms (range ±4.4 ms) on 0060 and 2.5 ms rms (range
−7.3…+6.9 ms) on 0062; a quadratic term buys 0.4 ms and 6.5 ms of curvature over the whole span,
i.e. nothing. Per-chapter fits differ from the one whole-recording fit by at most 4.2 ms. So one
affine map per recording removes the entire measured error down to a few milliseconds, and a
per-sample lookup would re-import the 28 ms rms of payload packing it was built to avoid.

WHAT IT DOES NOT FIX, SAID PLAINLY. Where a fix sits INSIDE its 1.001 s payload is recorded
nowhere, so no method can place a telemetry instant on the media clock better than about ±0.05 s
(1.5 frames at 30 fps) — and the app has always carried that, because the load anchors the axis on
one sample's naive time. What this removes is the part that GROWS: after the fit the residual is
the same at the last lap of a session as at the first, instead of ramping to 0.22 s. That is the
error a viewer sees, and it is why the fit is on the SPREAD, not on any single instant.

AND THE GPS TIMESTAMPS THEMSELVES ARE LATE. The rate fit above puts a telemetry instant on the
media clock; it does NOT say whether the timestamp on a GPS fix names the instant the picture
shows. Measured (studio/rotation.py, which cross-correlates the camera's own gyroscope against the
path-derived rate): an event's GPS timestamp lands **+0.476 s (0060) / +0.459 s (0062)** after the
same event's gyro timestamp — `rotation.measure_lag`'s WHOLE-RECORDING figure, the one installed
here; `rotation`'s module doc tables it per chapter and per lap (IQR 0.06 s / 0.04 s wide), with no
seam step larger than that spread. The gyro rides the picture (settled against yaw taken from the
frames themselves), so the trace is the late one, and every GPS-derived overlay — speed, Δ, the map
dot — was painted against a frame ~14 of them past the one it belongs to at 30 fps. (The g DIAL is a separate case, and not the one it
looks like; see WHOSE LAG THIS IS below.)

That offset is `gps_lag` here. It is NOT part of the fit and cannot be: `fit` sees only the two
time axes, and both carry it equally. It is measured per recording at load and installed onto this
object afterwards (`Session._install_gps_lag`), so a recording whose gyro cannot be measured — or
whose measurement is refused — keeps the pure two-clock map and the app's older behaviour.

`gps_lag` shifts the picture<->trace mapping and NOTHING else: `to_media` seeks `gps_lag` earlier,
`to_telemetry` asks the trace for the sample that belongs to the frame. Lap TIMES are differences
taken on one clock and cannot move by a constant. The measurement itself must be taken on the PURE
map (`without_gps_lag`) or it would be measuring the correction it produced.

WHOSE LAG THIS IS, AND WHICH CHANNELS IT IS NOT. `gps_lag` describes the GPS TIMESTAMPS. It is
tempting to read it as "the trace is late and the camera is not", and for the GPS-derived channels
that is exactly right. It is NOT right for the g series. Measured against the gyro — the one
channel settled against the frames themselves — `gmeter`'s lateral sits +0.399 s (0060) /
+0.406 s (0062) behind the picture ON ITS OWN LABELS: the accelerometer's CONTENT arrives carrying
very nearly the same delay the GPS timestamps carry, even though the two streams are stamped
together. So the g series must not have this lag taken back out of it. `Session.g_at_time` crosses
`without_gps_lag()` — the rate fit alone — and `driving_channels` joins by label for the same
reason (#303). Undoing the lag there put the dial ~0.39 s behind the speed painted beside it.
What this lag DOES correct is the GPS-derived half: the speed, the Δ, the map dot, the lap clock's
zero, and the seek itself.

WHICH MAP — THE NAMES CANNOT SAY, SO THIS DOES. Both maps land on the same media axis, so a name
that only names the axis ("media time", `to_media`) cannot tell them apart, and it has not: #314
crossed `Session.media_time` to index the GPS-quality strip and published 9 of 456 corner cells
flipping where the truth was 1 (#318). The difference is WHAT is being located:

  * the PICTURE map — `to_media` on the installed clock, `Session.media_time`: the footage position
    whose frame shows the event a telemetry sample describes. Seek the video with it, and read any
    series whose CONTENT rides the picture with it.
  * the STAMP map — `without_gps_lag().to_media`: the camera's own media stamp for that telemetry
    instant. Index anything whose content arrives carrying the GPS delay with it.

Which series is which is a MEASURED property of each stream's content, not of how it is stamped.
Through `rotation.measure_lag` against the path-derived reference (+ = the path runs behind the
channel), 0060 / 0062:

    series (stamped on)                  STAMP map            PICTURE map          so it takes
    GYRO yaw rate (media stamps)         +0.476 / +0.459 s    +0.007 / +0.002 s    the PICTURE map
    g series `lat_g` (ACCL stamps)       +0.072 / +0.040 s    -0.409 / -0.418 s    the STAMP map
    raw |ACCL horizontal| (ACCL stamps)  +0.095 / +0.053 s    -0.382 / -0.406 s    the STAMP map
    GPS-quality strip (naive stamps)     -0.000 / -0.000      +0.476 / +0.459      the STAMP map
                                         (#318's per-fix residual against the stamps the strip binned)

GYRO and ACCL are stamped together on one sample grid and still take different maps, so no name
of a STREAM ("IMU", "inertial", "media-stamped") can choose the map either — the dev probes'
single "inertial" conversion placed every accelerometer sample through the gyro's map until T9.
Correlation cannot arbitrate this: r is identical to three decimals under either map on every row.
Every executable crossing in `studio/` is listed, with the map it takes and why, in
`tests/test_media_clock.py::CROSSINGS`, and a new one fails that test until it is added there.

Qt-free and pacer-free (numpy only), so the conversion is shared by the pipeline, the exporter and
the player without dragging either dependency anywhere.
"""

from __future__ import annotations

import math

import numpy as np

# --- fit guards. Each one is a fitted map this module REFUSES, falling back to identity (the old
# behaviour) rather than moving a user's video by an amount no camera clock explains. The measured
# values on the two D24 recordings are beside each bound, so the room is visible.
MAX_RATE_DEVIATION = 1e-3     # measured 27 ppm; this is 37x that
MAX_CORRECTION_S = 5.0        # measured max |media − telemetry| 0.22 s
MAX_RESIDUAL_RMS_S = 0.5      # measured 0.028 s (the payload-packing sawtooth); this is 18x it
MIN_SAMPLES = 100             # 10 s of 10 Hz fixes — below this a rate fit is noise
MIN_SPAN_S = 60.0             # …and a rate needs a lever arm: 27 ppm over 60 s is 1.6 ms

# The same posture for the MEASURED GPS lag (see the module doc): a bound past which the number is
# not a receiver's fix latency but a broken measurement, and `with_gps_lag` keeps the pure map
# rather than moving a user's video by it. Measured 0.476 / 0.459 s on the two D24 recordings;
# `rotation.measure_lag` already refuses a peak at the edge of its own ±2 s search.
MAX_GPS_LAG_S = 1.0


class MediaClock:
    """`media = rate * telemetry + offset - gps_lag`, and its exact inverse.

    `rate`/`offset` are the two clocks' affine map, built by `fit` from a loaded recording's two
    time axes. `gps_lag` is the measured latency of the GPS timestamps themselves (module doc),
    installed after the fit by whoever measured it; 0.0 means "not measured", which is also the
    app's older behaviour.

    The default is the IDENTITY map, which is what every consumer gets when the recording has no
    true clock to diverge from (a GPS5 camera), when the fit is refused, or when a caller has no
    clock at all (the duck-typed sessions the tests build). Identity reproduces the app's pre-fix
    behaviour exactly, so a conversion can never be the reason something stops working."""

    __slots__ = ("rate", "offset", "gps_lag")

    def __init__(self, rate: float = 1.0, offset: float = 0.0, gps_lag: float = 0.0):
        self.rate = float(rate)
        self.offset = float(offset)
        self.gps_lag = float(gps_lag)

    @property
    def is_identity(self) -> bool:
        return self.rate == 1.0 and self.offset == 0.0 and self.gps_lag == 0.0

    def to_media(self, t):
        """Telemetry (GPS9 true-clock) time -> the media time whose PICTURE shows that instant.
        Scalars and numpy arrays alike."""
        return self.rate * t + self.offset - self.gps_lag

    def to_telemetry(self, t):
        """Media time -> the telemetry time the frame at `t` is a picture of — the exact inverse
        of `to_media`."""
        return (t + self.gps_lag - self.offset) / self.rate

    def correction_at(self, t: float) -> float:
        """How far this map moves telemetry instant `t` to reach its picture, in seconds — the two
        clocks' drift MINUS the GPS lag. The quantity the provenance/diagnostic surfaces would
        state; 0.0 for identity."""
        return float(self.to_media(t) - t)

    def without_gps_lag(self) -> MediaClock:
        """The pure two-clock map: the rate fit alone, with no GPS-lag correction.

        THE LAG MEASUREMENT MUST RUN ON THIS ONE. `rotation.measure_lag` puts the GPS trace on the
        gyro's clock before correlating, and if that map already carried the correction it would
        report ~0 and the correction would justify itself. It is also what any surface that wants
        to state the two clocks' drift on its own asks for."""
        return MediaClock(rate=self.rate, offset=self.offset)

    def with_gps_lag(self, lag: float | None) -> MediaClock:
        """This map plus a measured GPS lag, or SELF when the lag is not one to apply.

        Refused (returning self, so the caller cannot half-apply it): a `None` — which
        `rotation.measure_lag` uses for "not measured", never for zero — a non-finite value, and
        anything past `MAX_GPS_LAG_S`, which is a broken measurement rather than a receiver's fix
        latency. A lag replaces any previous one rather than compounding with it."""
        if lag is None:
            return self
        try:
            lag = float(lag)
        except (TypeError, ValueError):
            return self
        if not math.isfinite(lag) or abs(lag) > MAX_GPS_LAG_S:
            return self
        return MediaClock(rate=self.rate, offset=self.offset, gps_lag=lag)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        if self.is_identity:
            return "MediaClock(identity)"
        lag = f", gps_lag={self.gps_lag:+.4f}s" if self.gps_lag else ""
        return (f"MediaClock(rate={self.rate:.9f} [{(self.rate - 1.0) * 1e6:+.2f} ppm], "
                f"offset={self.offset:+.4f}s{lag})")


IDENTITY = MediaClock()


def clock_of(obj) -> MediaClock:
    """The `MediaClock` carried by a ChapterMap / session-ish object, or IDENTITY.

    Duck-typed on purpose: the exporter and the video layer are driven by stand-in objects in
    dozens of tests, and a missing clock must mean "no conversion", never an AttributeError."""
    clock = getattr(obj, "media_clock", None) if obj is not None else None
    return clock if isinstance(clock, MediaClock) else IDENTITY


def fit(telemetry, media) -> MediaClock:
    """Least-squares affine fit of `media` against `telemetry`, or IDENTITY when it is refused.

    Fits the DIFFERENCE (media − telemetry), which is O(0.1 s) over a 5000 s recording, rather than
    media against telemetry directly: the slope we are after is 27 parts per million of a number
    near 1, and fitting the difference puts the whole of it in the fitted coefficient instead of the
    ninth significant figure of one.

    Returns IDENTITY — the app's behaviour before this module existed — when the two axes are the
    same array (a GPS5 camera never leaves the media clock, so there is nothing to convert), when
    there is too little trace to fit a rate from, or when the fit trips one of the guards above."""
    tel = np.asarray(telemetry, dtype=float)
    med = np.asarray(media, dtype=float)
    if tel.ndim != 1 or tel.shape != med.shape or tel.size < MIN_SAMPLES:
        return IDENTITY
    if float(tel[-1] - tel[0]) < MIN_SPAN_S:
        return IDENTITY
    diff = med - tel
    if not np.any(diff):
        return IDENTITY  # the true clock IS the media clock (GPS5 fallback) — nothing to convert
    if not (np.all(np.isfinite(tel)) and np.all(np.isfinite(med))):
        return IDENTITY
    slope, intercept = (float(v) for v in np.polyfit(tel, diff, 1))
    rate = 1.0 + slope
    resid = diff - (slope * tel + intercept)
    correction = np.abs(slope * tel + intercept).max()
    if (abs(slope) > MAX_RATE_DEVIATION or correction > MAX_CORRECTION_S
            or float(resid.std()) > MAX_RESIDUAL_RMS_S or rate <= 0.0):
        return IDENTITY
    return MediaClock(rate=rate, offset=intercept)
