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
  3. THE CROSSING'S SCOPE (T10) — the lap window meeting the naive-second GPS-quality strip is
     harmless at LAP length and not below ~10 s. Guarded as a fact (the strip's axis is the rate
     fit, not the GPS-lag-corrected picture map), as the call shape that published a wrong corner
     figure (a strip index through `media_time`), and as a claim (every place the lap verdict is
     written names where it stops holding).

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


def _synthetic_axes(phase_s: float = 0.0):
    """(samples, naive, telemetry_times) for a D24-shaped recording on two clocks.

    The GPS9 stamps are an exact 10.000 Hz wall clock; `naive` is the media axis the GPMF payload
    layout gives, running D24_MEDIA_PPM fast against it. `_gps9_times` is the REAL load-path
    function, not a re-implementation — a probe that re-derives the rule under test carries a copy
    of the defect.

    `phase_s` starts the fixes off the whole second. At 0 every fix sits on a tenth of a second,
    so a ramp under 0.1 s can never carry one across a one-second cell edge — true of the synthetic
    and of no real recording, whose fixes land at whatever phase the receiver locked on."""
    n = int(SPAN_S * HZ)
    true_t = np.arange(n) / HZ
    naive = 1000.0 + phase_s + true_t * (1.0 + D24_MEDIA_PPM * 1e-6)
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


# ------------------------------------------------ 3. THE CROSSING'S SCOPE (T10, after #306 / #314)
# #306 measured the lap window meeting the MEDIA-second quality strip and recorded it as harmless:
# right at lap length, and read by #314 as wrong for a corner. Measured per kept fix on both D24
# recordings (studio/dev/probes/p5_clock_crossing_scale.py) both halves needed a correction: the
# corner figure had crossed `Session.media_time`, which carries the GPS lag, and how often a window
# flips does not depend on its length at all — what the length decides is how much of a consumer's
# answer the flips are. These guard the fact that settles which map is the strip's, the one call
# shape that got it wrong, and the places the lap-scale verdict is written.

# The GPS lag `_install_gps_lag` measured on the owner's recordings (+0.476 s / +0.459 s).
D24_GPS_LAG_S = 0.47


def test_the_quality_strip_sits_on_the_rate_fit_not_on_the_picture_map():
    """THE FACT. The strip bins every fix by its NAIVE stamp, and `media_clock.fit` is the map from
    a fix's telemetry label to that stamp. The GPS lag is a correction for the PICTURE: a fix's
    telemetry and naive stamps carry it equally, so it is not part of the strip's axis.

    Driven through the real `_gps9_times`, `media_clock.fit` and `build_quality_timeline` on the
    D24-shaped recording above, with one MODERATE second deep in it (where the two labels are
    furthest apart) and every fix asked for the class of its own cell:

      * `without_gps_lag().to_media` — the rate fit alone — places EVERY fix in its own cell;
      * the telemetry label as-is misplaces the fix at each edge (the 27 ppm ramp, ~0.08 s here);
      * `to_media` with the lag — `Session.media_time` — misplaces ~0.47 s of fixes at each edge,
        worse than not converting at all. That is the join #314's corner figure used.

    NEGATIVE CONTROL: make `without_gps_lag` keep the lag (return `self`) and the first assertion
    goes red with ten misplaced fixes."""
    from studio import data_quality

    _samples, naive, times = _synthetic_axes(phase_s=0.05)
    clock = media_clock.fit(times, naive).with_gps_lag(D24_GPS_LAG_S)
    assert clock.gps_lag == D24_GPS_LAG_S, "the lag was refused; this test would measure nothing"
    cell = int(naive[-1]) - 100
    own = np.floor(naive).astype(int)
    dop = np.where(own == cell, 7.0, 1.5)             # 7.0 is MODERATE: past good, inside the gate
    strip = data_quality.build_quality_timeline(naive, [False] * len(naive), dop,
                                                span_s=float(naive[-1]) + 1.0)
    assert strip.cls[cell] == data_quality.MODERATE and strip.cls[cell - 1] == data_quality.GOOD

    near = np.flatnonzero(np.abs(own - cell) <= 2)
    truth = [int(strip.cls[own[i]]) for i in near]

    def misplaced(to_strip):
        return sum(strip.worst_between(float(to_strip(times[i])), float(to_strip(times[i]))) != c
                   for i, c in zip(near, truth, strict=True))

    rate_fit = misplaced(clock.without_gps_lag().to_media)
    label = misplaced(lambda t: t)
    picture = misplaced(clock.to_media)
    assert rate_fit == 0, f"the rate fit misplaced {rate_fit} fixes — it is not the strip's axis"
    assert label >= 1, "the telemetry label is the strip's axis — then there is no crossing to scope"
    assert picture > label, (
        f"the lag-corrected map misplaced {picture} fixes against the label's {label}: if it is "
        f"not worse, the GPS lag has become part of the strip's axis and the rule is wrong")
    print(f"ok strip axis: misplaced — rate fit {rate_fit}, label {label}, with GPS lag {picture}")


def _strip_index_calls(tree):
    """Every `.worst_between(...)` / `.stats_between(...)` call — the two ways into the strip."""
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("worst_between", "stats_between")]


def _crosses_the_picture_map(call):
    """Does a strip-index call reach its window through the lag-carrying map? `x.media_time(...)`
    and `x.media_clock.to_media(...)` do; `x.media_clock.without_gps_lag().to_media(...)`, or a
    map bound from it first, does not."""
    for arg in [*call.args, *(k.value for k in call.keywords)]:
        for node in ast.walk(arg):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr == "media_time":
                return True
            owner = node.func.value
            if (node.func.attr == "to_media" and isinstance(owner, ast.Attribute)
                    and owner.attr == "media_clock"):
                return True
    return False


def test_nothing_indexes_the_quality_strip_through_the_picture_map():
    """THE CALL SHAPE THAT GOT IT WRONG. `studio/dev/probes/p4_corner_gps_quality.py` asked whether
    a corner cell's class survives the other clock by indexing the strip with `session.media_time`,
    and published the answer — 9 of 456 — in `studio/docs/refused-2026-09.md` §4. Measured against
    the fixes' own stamps the crossing moves 1 of them; the other eight were the GPS lag.

    Scans every module under `studio/`, dev probes included, because a probe's printed number is
    what gets written down. Both directions: the scan must find the strip's real consumers, so a
    renamed accessor cannot turn this into a guard over nothing.

    NEGATIVE CONTROL, watched: on the tree before T10, p4's `worst_between(session.media_time(t0),
    session.media_time(t1))` fails this."""
    found, bad = 0, []
    for root, _dirs, files in os.walk(os.path.join(_REPO, "studio")):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for call in _strip_index_calls(tree):
                found += 1
                if _crosses_the_picture_map(call):
                    bad.append(f"{os.path.relpath(path, _REPO)}:{call.lineno}")
    # Session.lap_quality, the strip's hover, and p4's class + stats + clock comparison.
    assert found >= 5, f"only {found} strip-index calls found — the scan has lost its subject"
    assert not bad, (
        f"these index the GPS-quality strip through the GPS-lag-corrected picture map; the strip's "
        f"axis is `media_clock.without_gps_lag()`: {bad}")
    print(f"ok strip index: {found} calls, none through the picture map")


# Where #306's lap-scale verdict is written. Each must also carry its SCOPE: the measurement that
# says where it stops holding, and the map a shorter window crosses with. Both directions: a target
# that matches nothing fails too.
SCOPE_DOCSTRINGS = [
    ("studio/session.py", "quality_timeline"),
    ("studio/session.py", "lap_quality"),
    ("studio/marks.py", "auto_marks"),
]
SCOPE_CLASSES = [
    ("studio/video_view.py", "_QualityStrip"),     # the one short-window consumer in the app
]
SCOPE_TEXT = [
    "studio/docs/refused-2026-09.md",               # §4, where #314 wrote the corner-scale reading
    "studio/dev/probes/p4_corner_gps_quality.py",
    "CHANGELOG.md",
]
SCOPE_REQUIRED = ("p5_clock_crossing_scale", "without_gps_lag")


def _class_docstrings(path):
    with open(os.path.join(_REPO, path), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    return {n.name: ast.get_docstring(n) or "" for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}


def test_the_lap_scale_verdict_states_where_it_stops_holding():
    """THE CLAIM. "Harmless — converting would buy nothing" is true of a lap and was already being
    read as true, or as false, of windows it was never measured on. So every place that states it
    must name the measurement that scopes it and the map a shorter window has to cross with."""
    missing, silent = [], []
    targets = []
    for path, name in SCOPE_DOCSTRINGS:
        targets.append((f"{path}::{name}", _docstrings(path).get(name)))
    for path, name in SCOPE_CLASSES:
        targets.append((f"{path}::{name}", _class_docstrings(path).get(name)))
    for path in SCOPE_TEXT:
        with open(os.path.join(_REPO, path), encoding="utf-8") as f:
            targets.append((path, f.read()))
    for where, text in targets:
        if text is None:
            missing.append(where)
            continue
        silent += [f"{where} lacks {req!r}" for req in SCOPE_REQUIRED if req not in text]
    assert not missing, f"guarded symbols that no longer exist (stale targets): {missing}"
    assert not silent, f"the lap-scale verdict is stated without its scope: {silent}"
    print(f"ok scope: {len(targets)} places carry the window-length rule")


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
