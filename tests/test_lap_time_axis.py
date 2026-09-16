"""THE LAP-TIME AXIS: a lap time is a TELEMETRY second, and no comment may say otherwise.

`pacer/laps/laps.hpp` and `Session._lap_columns` both described a lap's per-point times as
"media-clock seconds", and sixteen comments across the core and the studio repeated it. They are
the GPS9 TRUE-clock (telemetry) axis `studio/load._gps9_times` builds. The two axes differ by an
affine map — measured +26.73 ppm (recording 0060) and +27.11 ppm (0062), a 0.0755 / 0.1267 s ramp
across a recording — so believing the comment and comparing a lap time against an IMU or media
timestamp silently misplaces it by up to 0.097 / 0.167 s, five frames at 30 fps.

The code was right and only the comments were wrong: every interior lap-column time on both D24
recordings (26,486 and 45,313 samples) is a bit-exact member of `Session.tt`. So this file guards
BOTH halves, because either one alone is weak:

  1. THE FACT (`test_the_core_echoes_...`, `test_the_lap_axis_...`) — the C++ core converts no
     clock, it hands back whatever the studio fed it; and what the studio feeds it is the GPS9
     axis, which measurably leaves the media clock. If a future change put lap columns on the
     media clock, the second test's fitted `MediaClock` collapses to IDENTITY and it fails. That
     is its negative control, and it is exactly the mutation the old comment described.
  2. THE CLAIM (`test_no_lap_axis_comment_...`) — a text guard over the comment family itself, in
     both directions like `tests/test_layering.py`: every target must MATCH SOMETHING (a stale
     entry that matches nothing is a silent no-op), must not use the banned wording, and must
     positively name the telemetry clock. A docstring that simply drops the question fails too.

Run: python tests/test_lap_time_axis.py
"""
import ast
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

import pacer  # noqa: E402
from studio import media_clock  # noqa: E402
from studio.load import _gps9_times  # noqa: E402

# The measured media-clock rate on the owner's recordings (studio/media_clock.py): +26.73 ppm on
# 0060 and +27.11 ppm on 0062. The synthetic recording below runs at the larger of the two.
D24_MEDIA_PPM = 27.11
# …over a span in the same class as a real session (0060 is 2,824 s of trace, 0062 is 4,676 s).
SPAN_S = 3000.0
HZ = 10.0
# What the claim is worth at the end of that span: 27.11 ppm x 3000 s = 0.081 s, 2.4 frames at 30
# fps. Guarded loosely on both sides — the point is that it is FRAME-SCALE, not rounding.
MIN_DIVERGENCE_S = 0.05
MAX_DIVERGENCE_S = 0.50


def _sample(ts_ms):
    return pacer.GPSSample(lat=0.0, lon=0.0, altitude=0.0, full_speed=20.0,
                           ground_speed=20.0, timestamp_ms=int(ts_ms))


def _synthetic_axes():
    """(samples, naive, telemetry_times) for a D24-shaped recording on two clocks.

    The GPS9 stamps are an exact 10.000 Hz wall clock; `naive` is the media axis the GPMF payload
    layout gives, running D24_MEDIA_PPM fast against it. `_gps9_times` is the REAL load-path
    function, not a re-implementation — a probe that re-derives the rule under test carries a copy
    of the defect."""
    n = int(SPAN_S * HZ)
    true_t = np.arange(n) / HZ
    naive = 1000.0 + true_t * (1.0 + D24_MEDIA_PPM * 1e-6)
    samples = [_sample(500_000 + i * 100) for i in range(n)]
    times = np.asarray(_gps9_times(samples, list(naive)), float)
    return samples, naive, times


# One stadium loop in local metres — straight, 180-degree arc, return straight, second arc. The
# shape is BORROWED from tests/test_provenance.py rather than invented: a first attempt here swept
# back and forth across a line instead of driving a closed loop, and materialised no laps at all.
RADIUS, STRAIGHT = 30.0, 200.0
ARC = math.pi * RADIUS
TOTAL = 2 * STRAIGHT + 2 * ARC          # ~588 m, so a lap at SPEED_MS takes ~29 s
SPEED_MS = 20.0


def _stadium(ds: float = 2.0):
    s = np.arange(0.0, TOTAL, ds)
    xs, ys = np.empty_like(s), np.empty_like(s)
    for i, si in enumerate(s):
        if si < STRAIGHT:
            xs[i], ys[i] = si, 0.0
        elif si < STRAIGHT + ARC:
            th = (si - STRAIGHT) / RADIUS
            xs[i] = STRAIGHT + RADIUS * math.sin(th)
            ys[i] = RADIUS - RADIUS * math.cos(th)
        elif si < 2 * STRAIGHT + ARC:
            xs[i] = STRAIGHT - (si - STRAIGHT - ARC)
            ys[i] = 2 * RADIUS
        else:
            th = (si - 2 * STRAIGHT - ARC) / RADIUS
            xs[i] = -RADIUS * math.sin(th)
            ys[i] = RADIUS + RADIUS * math.cos(th)
    return xs, ys, s


def _laps_over(times):
    """A real `pacer.Laps` lapping the stadium at a constant speed, sampled on the clock `times`.

    The geometry is deliberately dull and the pace constant — the subject here is the CLOCK, and
    a lap only has to EXIST for the question to be asked of it."""
    xs, ys, cum = _stadium()
    cs = pacer.CoordinateSystem(pacer.GPSSample(lat=52.0, lon=-0.75, altitude=60.0))
    laps = pacer.Laps()
    times = np.asarray(times, float)
    s = (SPEED_MS * (times - times[0])) % TOTAL
    for x, y, t in zip(np.interp(s, cum, xs), np.interp(s, cum, ys), times, strict=True):
        laps.add_point(cs.global_(pacer.Vec3f(float(x), float(y), 0.0)), float(t))
    laps.set_coordinate_system(cs)
    a, b = pacer.Point(), pacer.Point()
    a.x, a.y, b.x, b.y = 100.0, -20.0, 100.0, 20.0     # across the bottom straight, clear of both arcs
    seg = pacer.Segment()
    seg.first, seg.second = a, b
    laps.sectors = pacer.Sectors(start_line=seg, sector_lines=[])
    laps.update()
    return laps


# --------------------------------------------------------------------------- 1. THE FACT
def test_the_core_echoes_the_clock_it_was_fed():
    """`pacer::Laps` converts NO clock: `lap_columns().times` is the number the studio passed to
    AddPoint, to the bit. That is what makes the axis a studio decision rather than a core one —
    and what made the old header comment ("times  media-clock seconds") a claim the core could
    not have honoured even if it were true."""
    fed = 1000.0 + np.arange(1800) / 10.0          # 180 s at 10 Hz ≈ six stadium laps
    laps = _laps_over(fed)
    assert laps.laps_count() >= 4, laps.laps_count()
    cols = laps.lap_columns(0)
    interior = np.asarray(cols.times, float)[1:-1]
    assert len(interior) > 10, len(interior)
    # Bit-exact membership, not "close": a core that resampled or re-anchored would still be
    # close, and close is what hid this for as long as it was hidden.
    assert np.all(np.isin(interior, fed)), "the core did not hand back the times it was fed"


def test_the_lap_axis_is_the_telemetry_axis_not_the_media_one():
    """The load path's axis LEAVES the media clock, and the lap columns carry it unconverted.

    NEGATIVE CONTROL, which is the mutation the old comment asserted: if `_gps9_times` returned
    the naive media times (i.e. lap times really were media-clock seconds), the two axes would be
    one array, `media_clock.fit` would return IDENTITY by its own `np.any(diff)` guard, and both
    the rate and the divergence assertions below go red."""
    _samples, naive, times = _synthetic_axes()

    clock = media_clock.fit(times, naive)
    assert not clock.is_identity, (
        "the lap axis and the media axis are the same array — the lap columns are on the media "
        "clock, which is exactly what this guard exists to catch")
    ppm = (clock.rate - 1.0) * 1e6
    assert abs(ppm - D24_MEDIA_PPM) < 1.0, f"fitted {ppm:+.2f} ppm, planted {D24_MEDIA_PPM:+.2f}"

    # The size of the false claim, at the end of a session-length recording.
    divergence = abs(clock.to_media(times[-1]) - times[-1])
    assert MIN_DIVERGENCE_S < divergence < MAX_DIVERGENCE_S, divergence

    # …and the lap the core cut out of it carries the telemetry axis, bit for bit.
    laps = _laps_over(times)
    assert laps.laps_count() >= 10, laps.laps_count()
    last = laps.laps_count() - 2          # -2: the trailing partial lap is not the subject
    interior = np.asarray(laps.lap_columns(last).times, float)[1:-1]
    assert len(interior) > 10, len(interior)
    assert np.all(np.isin(interior, times)), "lap columns are not the load path's telemetry axis"
    # The lap sits late in the recording, so its own times are far from their media twins.
    assert abs(clock.to_media(interior[-1]) - interior[-1]) > MIN_DIVERGENCE_S


# --------------------------------------------------------------------------- 2. THE CLAIM
# Every accessor whose times ARE the lap axis. Each must name the telemetry clock and must not
# call it a media one. Checked in both directions: a target that matches nothing fails too.
LAP_AXIS_FUNCTIONS = [
    ("studio/session.py", "_lap_columns"),
    ("studio/session.py", "_lap_trace_xyt"),
    ("studio/session.py", "_lap_time_dist"),
    ("studio/session.py", "_lap_point_times"),
    ("studio/session.py", "_track_times"),
    ("studio/session.py", "_lap_curve"),
    ("studio/session.py", "lap_at_time"),
    ("studio/session.py", "lap_quality"),
    ("studio/session.py", "delta_at_time"),
    ("studio/session.py", "delta_at_lap"),
    ("studio/session.py", "delta_to_ideal_at"),
    ("studio/timeline.py", "media_time_at_plot_x"),
    ("studio/timeline.py", "plot_x_at_media_time"),
    ("studio/timeline.py", "lap_at_time"),
    ("studio/timeline.py", "_lap_xy_t"),
    ("studio/gapfill.py", "reconstruct_lap"),
]

# Files whose lap-axis wording lives in COMMENTS or attribute annotations, which no docstring
# walk can see, plus the one user-facing surface that printed the claim on screen.
LAP_AXIS_TEXT = [
    "studio/render_cache.py",
    "studio/provenance.py",
    "studio/provenance_panel.py",
    "pacer/laps/laps.hpp",
]

# The wording that was wrong. Deliberately narrow: "media clock" is a real and correct term all
# over this repo (the IMU streams, the quality strip, the chapter table), so only the phrasings
# that attach it to a TIME AXIS are banned.
BANNED = ("media-clock second", "media clock second", "media-clock time",
          "media-clock axis", "media-clock times")
REQUIRED = "telemetry"


def _docstrings(path):
    """{function name: docstring} for every def in a module, nested ones included."""
    with open(os.path.join(_REPO, path), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(node.name, ast.get_docstring(node) or "")
    return out


def test_no_lap_axis_comment_calls_a_lap_time_a_media_clock_second():
    """The guard on the claim itself — the half a fact-test cannot cover, because a comment can go
    false without any behaviour changing. That is precisely what happened here: the code has been
    right since the GPS9 axis landed, and the comments were wrong for as long."""
    missing, banned, silent = [], [], []
    for path, name in LAP_AXIS_FUNCTIONS:
        docs = _docstrings(path)
        if name not in docs:                     # the both-directions half
            missing.append(f"{path}::{name}")
            continue
        text = docs[name].lower()
        for phrase in BANNED:
            if phrase in text:
                banned.append(f"{path}::{name} says {phrase!r}")
        if REQUIRED not in text:
            silent.append(f"{path}::{name}")
    assert not missing, f"guarded symbols that no longer exist (stale targets): {missing}"
    assert not banned, f"the corrected claim came back: {banned}"
    assert not silent, (
        f"these carry the lap axis but say nothing about which clock it is: {silent}")

    for path in LAP_AXIS_TEXT:
        with open(os.path.join(_REPO, path), encoding="utf-8") as f:
            text = f.read().lower()
        for phrase in BANNED:
            assert phrase not in text, f"{path} says {phrase!r}"
        assert REQUIRED in text or "true-clock" in text, (
            f"{path} carries the lap axis but names no clock")
    print(f"ok claim: {len(LAP_AXIS_FUNCTIONS)} docstrings + {len(LAP_AXIS_TEXT)} files")


def test_the_panel_states_the_axis_it_measures_on():
    """The one user-facing surface in the family: the provenance panel's MEASURED OVER line. It
    printed "media-clock seconds" for a window that is telemetry seconds."""
    with open(os.path.join(_REPO, "studio/provenance_panel.py"), encoding="utf-8") as f:
        src = f.read()
    assert "telemetry seconds (GPS9 true clock)" in src, (
        "the panel no longer states the telemetry axis for a TIME window")
    assert "media-clock seconds" not in src


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nALL {len(tests)} LAP-TIME-AXIS TESTS PASSED")
