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

Qt-free and pacer-free (numpy only), so the conversion is shared by the pipeline, the exporter and
the player without dragging either dependency anywhere.
"""

from __future__ import annotations

import numpy as np

# --- fit guards. Each one is a fitted map this module REFUSES, falling back to identity (the old
# behaviour) rather than moving a user's video by an amount no camera clock explains. The measured
# values on the two D24 recordings are beside each bound, so the room is visible.
MAX_RATE_DEVIATION = 1e-3     # measured 27 ppm; this is 37x that
MAX_CORRECTION_S = 5.0        # measured max |media − telemetry| 0.22 s
MAX_RESIDUAL_RMS_S = 0.5      # measured 0.028 s (the payload-packing sawtooth); this is 18x it
MIN_SAMPLES = 100             # 10 s of 10 Hz fixes — below this a rate fit is noise
MIN_SPAN_S = 60.0             # …and a rate needs a lever arm: 27 ppm over 60 s is 1.6 ms


class MediaClock:
    """`media = rate * telemetry + offset`, and its exact inverse.

    Built by `fit` from a loaded recording's two time axes; the default is the IDENTITY map, which
    is what every consumer gets when the recording has no true clock to diverge from (a GPS5
    camera), when the fit is refused, or when a caller has no clock at all (the duck-typed sessions
    the tests build). Identity reproduces the app's pre-fix behaviour exactly, so a conversion can
    never be the reason something stops working."""

    __slots__ = ("rate", "offset")

    def __init__(self, rate: float = 1.0, offset: float = 0.0):
        self.rate = float(rate)
        self.offset = float(offset)

    @property
    def is_identity(self) -> bool:
        return self.rate == 1.0 and self.offset == 0.0

    def to_media(self, t):
        """Telemetry (GPS9 true-clock) time -> media time. Scalars and numpy arrays alike."""
        return self.rate * t + self.offset

    def to_telemetry(self, t):
        """Media time -> telemetry time — the exact inverse of `to_media`."""
        return (t - self.offset) / self.rate

    def correction_at(self, t: float) -> float:
        """How far the media clock is ahead of the telemetry clock at telemetry time `t`, in
        seconds. The quantity the provenance/diagnostic surfaces would state; 0.0 for identity."""
        return float(self.to_media(t) - t)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        if self.is_identity:
            return "MediaClock(identity)"
        return (f"MediaClock(rate={self.rate:.9f} [{(self.rate - 1.0) * 1e6:+.2f} ppm], "
                f"offset={self.offset:+.4f}s)")


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
