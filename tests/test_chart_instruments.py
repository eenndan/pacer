"""The charts' three instrument gestures: the datum cursor's interval maths, the window
statistics, and the Δ-loss tour — plus the measured slope floor that makes the first one honest.

Two halves, in the house shape:

  * the MATHS half is Qt-free `studio/chart_stats.py` over hand-built arrays, including the
    measurement that decides the floor — synthetic 10 Hz data carrying the D24-measured speed noise
    (sigma = 0.62 km/h), where a slope over one GPS sample really is more than half noise and a
    slope over 1.0 s is not;
  * the VIEW half is a real `PlotsView` over a stub Session rich enough to place a cursor (a lap
    window plus the two plot-x <-> media-time conversions the scrub uses), driving the same public
    methods the window's D / N shortcuts call and reading the answers back off the widgets.

Run: QT_QPA_PLATFORM=offscreen python tests/test_chart_instruments.py
"""
import os
import sys

import numpy as np

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()            # module scope, BEFORE any widget: measure the SHIPPING font stack

from studio import chart_stats, plots_view  # noqa: E402

# The D24 measurement this suite leans on: the speed channel's high-frequency residual against a
# 0.5 s moving average, median over 24 laps (0.54-0.73 across them). Named here so the floor test
# below is reproducing the real recording's noise rather than a number chosen to pass.
D24_SPEED_SIGMA_KMH = 0.619
D24_HZ = 10.0


# ==================================================================== the maths (Qt-free)
def test_interval_stats_measure_the_cursors_not_the_samples():
    """The endpoints are where the user put them, so they are interpolated in — and they count.

    A max that only looked at the samples strictly inside the interval would report a corner's
    entry speed as lower than the number printed one column to its left."""
    xs = np.array([0.0, 10.0, 20.0, 30.0, 40.0])
    ys = np.array([100.0, 80.0, 60.0, 40.0, 20.0])
    iv = chart_stats.interval_stats(xs, ys, 5.0, 35.0, dt=3.0, dd=30.0)
    assert iv is not None
    assert iv.y0 == 90.0 and iv.y1 == 30.0, (iv.y0, iv.y1)   # both interpolated, neither snapped
    assert iv.diff == -60.0
    assert iv.maximum == 90.0 and iv.minimum == 30.0, "the endpoints are part of the interval"
    assert abs(iv.mean - 60.0) < 1e-9, iv.mean               # a straight line's mean is its middle
    assert iv.n == 3, iv.n                                   # the samples strictly inside
    assert iv.dt == 3.0 and iv.dd == 30.0
    # slope is per SECOND, off `dt` — never off the x-span, which here is metres
    assert abs(iv.slope - (-60.0 / 3.0)) < 1e-9, iv.slope
    print("test_interval_stats_measure_the_cursors_not_the_samples OK")


def test_the_interval_does_not_care_which_cursor_you_dropped_first():
    xs = np.linspace(0.0, 100.0, 51)
    ys = 50.0 + 0.5 * xs
    a = chart_stats.interval_stats(xs, ys, 20.0, 80.0, dt=4.0)
    b = chart_stats.interval_stats(xs, ys, 80.0, 20.0, dt=4.0)
    assert a == b, (a, b)
    assert a.x0 == 20.0 and a.x1 == 80.0
    print("test_the_interval_does_not_care_which_cursor_you_dropped_first OK")


def test_the_mean_is_over_the_interval_not_over_the_samples():
    """A trapezoidal mean, so an uneven grid cannot shift it. Four of the six samples here crowd
    into the first 3% of the interval, which drags a plain sample mean 24 units below the truth."""
    xs = np.array([0.0, 1.0, 2.0, 3.0, 50.0, 100.0])
    ys = np.array([0.0, 0.0, 0.0, 0.0, 50.0, 100.0])
    iv = chart_stats.interval_stats(xs, ys, 0.0, 100.0, dt=10.0)
    naive = float(np.mean(ys))          # 25.0 — four zeros out of six samples
    assert abs(iv.mean - 49.25) < 1e-9, iv.mean     # (0·3 + 1175 + 3750) / 100
    assert abs(naive - iv.mean) > 20.0, (naive, iv.mean)
    print(f"test_the_mean_is_over_the_interval_not_over_the_samples OK "
          f"(trapezoid {iv.mean:.1f} vs sample mean {naive:.1f})")


def test_a_degenerate_interval_returns_nothing_rather_than_a_number():
    xs = np.linspace(0.0, 10.0, 11)
    ys = xs * 2.0
    assert chart_stats.interval_stats(xs, ys, 5.0, 5.0, dt=1.0) is None
    assert chart_stats.interval_stats(np.array([1.0]), np.array([2.0]), 0.0, 1.0) is None
    assert chart_stats.interval_stats(np.array([]), np.array([]), 0.0, 1.0) is None
    print("test_a_degenerate_interval_returns_nothing_rather_than_a_number OK")


# ------------------------------------------------------------------------- the slope floor
def test_the_slope_is_refused_below_the_floor_and_given_above_it():
    xs = np.linspace(0.0, 100.0, 101)
    ys = 100.0 - xs
    floor = chart_stats.SLOPE_MIN_INTERVAL_S
    assert chart_stats.interval_stats(xs, ys, 0.0, 50.0, dt=floor - 0.01).slope is None
    assert chart_stats.interval_stats(xs, ys, 0.0, 50.0, dt=floor).slope is not None
    # ...and with NO clock at all, which is what a session that cannot convert hands over
    assert chart_stats.interval_stats(xs, ys, 0.0, 50.0, dt=None).slope is None
    print("test_the_slope_is_refused_below_the_floor_and_given_above_it OK")


def test_the_floor_is_where_the_noise_stops_owning_the_answer():
    """THE MEASUREMENT THE FLOOR IS MADE OF, reproduced on synthetic 10 Hz data.

    Build a lap-like speed trace, add noise of the sigma measured on D24, and compare the slope
    with and without it. Below the floor the noise is a large fraction of the answer; at the floor
    the least-squares fit `interval_stats` uses is under a tenth of it. The real recording's
    numbers are in `chart_stats`' own header (52% at one grid step, 8.7% at 1.0 s); this pins the
    SHAPE — a 1/L decay steep enough that a 0.1 s reading is not a reading."""
    rng = np.random.default_rng(11)
    n = int(60 * D24_HZ)
    t = np.arange(n) / D24_HZ
    clean = 60.0 + 30.0 * np.sin(2 * np.pi * t / 12.0)      # a corner every 12 s
    noisy = clean + rng.normal(0.0, D24_SPEED_SIGMA_KMH, n)
    rel = {}
    for length in (0.1, 0.5, chart_stats.SLOPE_MIN_INTERVAL_S, 3.0):
        errs, trues = [], []
        for x0 in np.arange(0.0, 55.0, 0.5):
            a = chart_stats.interval_stats(t, clean, x0, x0 + length, dt=length)
            b = chart_stats.interval_stats(t, noisy, x0, x0 + length, dt=length)
            if a is None or b is None or a.slope is None or b.slope is None:
                continue
            errs.append(b.slope - a.slope)
            trues.append(a.slope)
        if not errs:
            continue
        rel[length] = (float(np.sqrt(np.mean(np.square(errs))))
                       / float(np.sqrt(np.mean(np.square(trues)))))
    # The two lengths that CAN be measured here (the shorter two are refused outright, which is the
    # point) must straddle the tenth: at the floor the noise is small, three times out it is smaller.
    assert 0.1 not in rel and 0.5 not in rel, (
        "a slope under the floor must not be computed at all, let alone scored")
    at_floor = rel[chart_stats.SLOPE_MIN_INTERVAL_S]
    assert at_floor < 0.12, f"at the floor the noise is {at_floor:.1%} of the answer"
    assert rel[3.0] < at_floor, (rel[3.0], at_floor)
    # ...and the refusal carries the reason, with the floor in it.
    assert f"{chart_stats.SLOPE_MIN_INTERVAL_S:.1f} s" in chart_stats.SLOPE_FLOOR_NOTE
    assert "10 Hz" in chart_stats.SLOPE_FLOOR_NOTE
    print(f"test_the_floor_is_where_the_noise_stops_owning_the_answer OK "
          f"(noise is {at_floor:.1%} of the slope at the {chart_stats.SLOPE_MIN_INTERVAL_S:.1f} s "
          f"floor, {rel[3.0]:.1%} at 3 s)")


def test_a_two_point_slope_really_is_worse_than_the_fit():
    """Why `interval_stats` fits instead of subtracting the endpoints — the choice the header's
    table is about, checked rather than asserted."""
    rng = np.random.default_rng(5)
    n = int(60 * D24_HZ)
    t = np.arange(n) / D24_HZ
    clean = 60.0 + 30.0 * np.sin(2 * np.pi * t / 12.0)
    noisy = clean + rng.normal(0.0, D24_SPEED_SIGMA_KMH, n)
    length = 2.0
    fit_err, two_err = [], []
    for x0 in np.arange(0.0, 55.0, 0.5):
        a = chart_stats.interval_stats(t, clean, x0, x0 + length, dt=length)
        b = chart_stats.interval_stats(t, noisy, x0, x0 + length, dt=length)
        fit_err.append(b.slope - a.slope)
        two_err.append((b.y1 - b.y0) / length - (a.y1 - a.y0) / length)
    fit = float(np.sqrt(np.mean(np.square(fit_err))))
    two = float(np.sqrt(np.mean(np.square(two_err))))
    assert fit < two, (fit, two)
    print(f"test_a_two_point_slope_really_is_worse_than_the_fit OK "
          f"(fit {fit:.3f} vs two-point {two:.3f} km/h/s of noise at 2.0 s)")


# ---------------------------------------------------------------------- window statistics
def test_the_six_window_statistics_reduce_the_visible_range():
    xs = np.linspace(0.0, 100.0, 101)
    ys = np.concatenate([np.linspace(0.0, 50.0, 51), np.linspace(49.0, 0.0, 50)])
    ws = chart_stats.window_stat
    assert ws(xs, ys, 20.0, 80.0, chart_stats.STAT_MAX) == 50.0
    assert ws(xs, ys, 20.0, 80.0, chart_stats.STAT_MIN) == 20.0
    assert ws(xs, ys, 20.0, 80.0, chart_stats.STAT_RANGE) == 30.0
    # RANGE AND DELTA ARE NOT THE SAME NUMBER, which is why both are offered: over this window the
    # trace climbs 30 and comes all the way back, so range is 30 and delta is 0 — the difference
    # between "how much did this move" and "where did it end up", which through one corner is the
    # difference between the whole entry-to-apex drop and what you never got back.
    assert abs(ws(xs, ys, 20.0, 80.0, chart_stats.STAT_DELTA)) < 1e-9
    assert ws(xs, ys, 0.0, 0.0, chart_stats.STAT_CURRENT, at=30.0) == 30.0
    assert ws(xs, ys, 0.0, 0.0, chart_stats.STAT_CURRENT) is None, "no playhead, no 'value'"
    mean = ws(xs, ys, 0.0, 100.0, chart_stats.STAT_MEAN)
    assert 20.0 < mean < 30.0, mean
    print("test_the_six_window_statistics_reduce_the_visible_range OK")


def test_the_window_edges_are_interpolated_in():
    """The chart's own left edge is a real position on the trace, not the nearest sample to it."""
    xs = np.array([0.0, 10.0, 20.0])
    ys = np.array([0.0, 100.0, 0.0])
    assert chart_stats.window_stat(xs, ys, 5.0, 6.0, chart_stats.STAT_MIN) == 50.0
    assert chart_stats.window_stat(xs, ys, 5.0, 6.0, chart_stats.STAT_MAX) == 60.0
    print("test_the_window_edges_are_interpolated_in OK")


def test_a_window_off_the_curve_says_nothing():
    xs = np.linspace(0.0, 10.0, 11)
    ys = xs
    assert chart_stats.window_stat(xs, ys, 50.0, 60.0, chart_stats.STAT_MEAN) is None
    assert chart_stats.window_stat(xs, ys, 5.0, 5.0, chart_stats.STAT_MEAN) is None
    assert chart_stats.window_stat(np.array([]), np.array([]), 0.0, 1.0,
                                   chart_stats.STAT_MEAN) is None
    print("test_a_window_off_the_curve_says_nothing OK")


# ------------------------------------------------------------------------ the loss search
def _delta_with_losses(bumps, n=400, total=1000.0):
    """A cumulative Δ curve that gives time away at each (centre, size) and is flat between."""
    xs = np.linspace(0.0, total, n)
    ys = np.zeros(n)
    for centre, size in bumps:
        ys += size / (1.0 + np.exp(-(xs - centre) / 6.0))   # a smooth step, i.e. a local loss
    return xs, ys


def test_the_tour_walks_the_losses_worst_first():
    xs, ys = _delta_with_losses([(200.0, 0.30), (500.0, 0.50), (800.0, 0.15)])
    spans = chart_stats.steepest_losses([(3, xs, ys)])
    assert len(spans) >= 3, spans
    assert [round(s.centre / 100) * 100 for s in spans[:3]] == [500, 200, 800], spans
    assert spans[0].loss > spans[1].loss > spans[2].loss
    assert all(s.lap_id == 3 for s in spans)
    assert all(s.x0 < s.centre < s.x1 for s in spans)
    print("test_the_tour_walks_the_losses_worst_first OK")


def test_two_picks_are_never_the_same_corner_twice():
    """The separation constant, checked against the shape it was measured on: ONE loss must not
    fill the tour with five views of itself."""
    xs, ys = _delta_with_losses([(500.0, 1.0)])
    spans = chart_stats.steepest_losses([(0, xs, ys)])
    sep = chart_stats.DEFAULT_LOSS_SEPARATION_FRAC * (xs[-1] - xs[0])
    centres = sorted(s.centre for s in spans)
    for a, b in zip(centres, centres[1:], strict=False):
        assert b - a >= sep, (a, b, sep)
    print(f"test_two_picks_are_never_the_same_corner_twice OK "
          f"({len(spans)} span(s), all ≥ {sep:.0f} m apart)")


def test_the_baseline_lap_has_no_steepest_slope():
    """THE DEGENERATE CASE. Against itself a lap's Δ is identically zero — measured on D24 as
    exactly [0.000000, 0.000000] — and "where did you lose most time to yourself" is not a
    question. It contributes nothing rather than an arbitrary flat span."""
    xs = np.linspace(0.0, 1000.0, 400)
    flat = np.zeros(400)
    assert chart_stats.steepest_losses([(7, xs, flat)]) == []
    # ...and a flat lap alongside a real one leaves only the real one's losses.
    _x, lossy = _delta_with_losses([(400.0, 0.4)])
    spans = chart_stats.steepest_losses([(7, xs, flat), (2, xs, lossy)])
    assert spans and all(s.lap_id == 2 for s in spans), spans
    print("test_the_baseline_lap_has_no_steepest_slope OK")


def test_a_lap_that_only_gained_offers_nothing_to_walk():
    xs = np.linspace(0.0, 1000.0, 400)
    ys = np.linspace(0.0, -1.5, 400)     # ahead all the way round
    assert chart_stats.steepest_losses([(1, xs, ys)]) == []
    print("test_a_lap_that_only_gained_offers_nothing_to_walk OK")


def test_the_search_survives_curves_it_cannot_use():
    xs = np.linspace(0.0, 100.0, 4)
    assert chart_stats.steepest_losses([(0, np.array([1.0]), np.array([1.0]))]) == []
    assert chart_stats.steepest_losses([]) == []
    # a window wider than the curve: nothing, not a crash
    assert chart_stats.steepest_losses([(0, xs, np.linspace(0, 1, 4))], window_frac=2.0) == []
    print("test_the_search_survives_curves_it_cannot_use OK")


def test_the_ranking_is_global_across_the_laps_on_screen():
    xs, small = _delta_with_losses([(300.0, 0.20)])
    _x, big = _delta_with_losses([(700.0, 0.90)])
    spans = chart_stats.steepest_losses([(1, xs, small), (2, xs, big)], count=2)
    assert spans[0].lap_id == 2 and spans[0].loss > spans[1].loss, spans
    print("test_the_ranking_is_global_across_the_laps_on_screen OK")


# ==================================================================== the view (real Qt)
class _Sess:
    """The Session surface PlotsView reads, over hand-built laps — including the three the datum
    readout needs that the older charts stub does not have (a lap window and the two plot-x <->
    media-time conversions the scrub cursor uses)."""

    TOTAL = 1000.0
    LAP_S = 60.0
    T0 = 100.0          # the media time each lap starts at, spaced LAP_S apart

    def __init__(self, laps, best=0, ideal=True):
        self._laps = {int(k): tuple(np.asarray(a, float) for a in v) for k, v in laps.items()}
        self._best = best
        self._ideal = ideal

    # --- what refresh() reads
    def has_reference(self):
        return False

    def best_lap_id(self):
        return self._best

    def ideal_donor_lap_id(self):
        return None

    def lap_time(self, lid):
        return self.LAP_S + 0.25 * lid

    def active_baseline_total_distance(self):
        return self.TOTAL

    def delta(self, ids, x_mode="distance"):
        sel = [i for i in ids if i in self._laps]
        if not sel:
            return None
        speed, delta = {}, {}
        for i in sel:
            dist, spd, dl = self._laps[i]
            # time mode's x is this lap's own elapsed-into-lap on the same normalized grid, which is
            # exactly what makes the companion lookup a fractional-index one.
            x = dist / self.TOTAL * self.lap_time(i) if x_mode == "time" else dist
            speed[i] = (x, spd)
            delta[i] = (x, dl)
        return self._best, speed, delta

    def delta_to_ideal(self, ids, x_mode="distance"):
        if not self._ideal:
            return None
        base = self.delta(ids, x_mode=x_mode)
        return None if base is None else {i: (x, y + 0.4) for i, (x, y) in base[2].items()}

    def ideal_delta_to_best(self, x_mode="distance"):
        if not self._ideal:
            return None
        xs = self._laps[self._best][0]
        return xs, -0.2 * np.ones(len(xs))

    # --- what the cursor + the datum readout read
    def lap_window(self, lid):
        t0 = self.T0 + lid * (self.LAP_S + 10.0)
        return (t0, t0 + self.lap_time(lid))

    def plot_x_at_media_time(self, lid, t, mode, best_distance=None):
        t0, t1 = self.lap_window(lid)
        frac = min(max((t - t0) / (t1 - t0), 0.0), 1.0)
        return frac * (self.lap_time(lid) if mode == "time" else self.TOTAL)

    def media_time_at_plot_x(self, lid, x, mode, best_distance=None):
        span = self.lap_time(lid) if mode == "time" else self.TOTAL
        frac = min(max(x / span, 0.0), 1.0)
        t0, t1 = self.lap_window(lid)
        return t0 + frac * (t1 - t0)


def _laps(n=2, points=400, losses=((500.0, 0.5), (200.0, 0.3))):
    """n laps on a shared 0..1000 m grid; lap 0 is the flat baseline, the rest carry the losses."""
    xs = np.linspace(0.0, 1000.0, points)
    out = {}
    for lid in range(n):
        spd = 60.0 + 25.0 * np.sin(np.linspace(0.0, 3 * np.pi, points))
        if lid == 0:
            dl = np.zeros(points)
        else:
            _x, dl = _delta_with_losses(losses, n=points)
        out[lid] = (xs, spd, dl)
    return out


def _view(n=2, select=None, ideal=True, size=(920, 520)):
    v = plots_view.PlotsView(_Sess(_laps(n), best=0, ideal=ideal))
    v.resize(*size)
    v.show()
    v.set_laps(range(n) if select is None else select)
    for _ in range(5):
        _APP.processEvents()
    return v


def _park(v, lid, frac):
    """Put the playhead `frac` of the way through lap `lid`, the way the app's tick does."""
    t0, t1 = v.session.lap_window(lid)
    v.set_playhead_time(t0 + frac * (t1 - t0), force=True)
    for _ in range(2):
        _APP.processEvents()


def test_the_datum_drops_at_the_playhead_and_clears_again():
    v = _view(select=[1])
    _park(v, 1, 0.25)
    assert v.datum_x() is None and not v.cur_speed_datum.isVisible()
    assert v.datum_label.isVisible() is False, "no datum, no interval line"
    v.toggle_datum()
    assert v.datum_x() is not None
    assert abs(v.datum_x() - v.cur_speed.value()) < 1e-9, "it lands on the playhead"
    assert v.cur_speed_datum.isVisible() and v.cur_delta_datum.isVisible()
    assert v.datum_label.isVisible() and "A dropped at" in v.datum_label.text()
    v.toggle_datum()
    assert v.datum_x() is None and not v.cur_speed_datum.isVisible()
    assert not v.datum_label.isVisible()
    v.deleteLater()
    print("test_the_datum_drops_at_the_playhead_and_clears_again OK")


def test_the_interval_readout_reports_the_stretch_between_the_cursors():
    v = _view(select=[1])
    _park(v, 1, 0.20)
    v.toggle_datum()
    _park(v, 1, 0.60)
    text = v.datum_label.text()
    # 40% of a 60.25 s lap = 24.1 s and 400 m of the shared distance axis
    assert "Δt 24.10 s" in text, text
    assert "Δd 400.0 m" in text, text
    for word in ("A→B", "speed", "Δ", "mean", "min", "max", "slope"):
        assert word in text, (word, text)
    # ...and the Δ over the interval is the delta curve's own rise across it
    xs, ys = next((x, y) for lid, x, y in v._delta_curves if lid == 1)
    want = float(np.interp(600.0, xs, ys) - np.interp(200.0, xs, ys))
    assert f"{want:+.3f} s" in text, (want, text)
    v.deleteLater()
    print("test_the_interval_readout_reports_the_stretch_between_the_cursors OK")


def test_the_ui_refuses_a_slope_it_cannot_stand_behind():
    """The floor, on the real widget: a short interval prints the reason and NO number."""
    v = _view(select=[1])
    _park(v, 1, 0.50)
    v.toggle_datum()
    _park(v, 1, 0.505)                      # 0.30 s apart, under the 1.0 s floor
    text = v.datum_label.text()
    assert "slope — needs ≥ 1.0 s" in text, text
    assert "km/h/s" not in text, f"no confident number may survive the refusal: {text}"
    # ...and the same pair, far enough apart, does print one.
    _park(v, 1, 0.60)
    text = v.datum_label.text()
    assert "km/h/s" in text and "needs ≥" not in text, text
    # the reason is reachable from the surface itself, not only from a commit message
    assert "10 Hz" in v.datum_label.toolTip()
    v.deleteLater()
    print("test_the_ui_refuses_a_slope_it_cannot_stand_behind OK")


def test_the_cursors_are_capped_left_to_right():
    """A and B name POSITIONS, so the chart and the "A→B" readout can never disagree."""
    v = _view(select=[1])
    _park(v, 1, 0.50)
    v.toggle_datum()
    _park(v, 1, 0.80)                       # the datum is now on the LEFT
    assert v.cur_speed_datum.label.format == "A" and v._b_label.format == "B"
    _park(v, 1, 0.20)                       # ...and now on the right
    assert v.cur_speed_datum.label.format == "B" and v._b_label.format == "A"
    v.deleteLater()
    print("test_the_cursors_are_capped_left_to_right OK")


def test_the_datum_survives_the_axis_flip_by_moving_with_the_track():
    """A datum at 620 m must not become a datum at 620 SECONDS."""
    v = _view(select=[1])
    _park(v, 1, 0.5)
    v.toggle_datum()
    before = v.datum_x()
    assert 400.0 < before < 600.0, before
    v.x_mode_combo.setCurrentIndex(1)       # -> time
    for _ in range(4):
        _APP.processEvents()
    after = v.datum_x()
    # the same track position, now in seconds into the lap (half of a 60.25 s lap)
    assert 25.0 < after < 35.0, (before, after)
    v.deleteLater()
    print("test_the_datum_survives_the_axis_flip_by_moving_with_the_track OK")


def test_window_statistics_follow_the_zoom():
    v = _view(select=[1])
    _park(v, 1, 0.5)
    stats = chart_stats.WINDOW_STATS
    v.stat_combo.setCurrentIndex(stats.index(chart_stats.STAT_MAX))
    for _ in range(3):
        _APP.processEvents()
    whole = v.window_label.text()
    assert "whole lap" in whole, whole
    sx, spd = v._speed_curves[1]
    assert f"{spd.max():,.1f}" in whole, (spd.max(), whole)
    # Zoom into a stretch that does NOT contain the lap's peak: the max must fall to that stretch's.
    # The bounds land ON samples so the expected value is a plain slice max — put them between two
    # samples and the honest answer is the INTERPOLATED edge, which can sit above every sample
    # inside (measured here: 56.1 at x = 400 against 55.9 for the first sample past it).
    lo, hi = float(sx[160]), float(sx[260])
    v.p_speed.setXRange(lo, hi, padding=0)
    for _ in range(3):
        _APP.processEvents()
    zoomed = v.window_label.text()
    assert "whole lap" not in zoomed and "window" in zoomed, zoomed
    local = float(spd[160:261].max())
    assert local < spd.max(), "setup: the window must not contain the lap's maximum"
    assert f"{local:,.1f}" in zoomed, (local, zoomed)
    v.deleteLater()
    print("test_window_statistics_follow_the_zoom OK")


def test_every_statistic_is_reachable_and_says_which_one_it_is():
    v = _view(select=[1])
    _park(v, 1, 0.5)
    seen = set()
    for i, stat in enumerate(chart_stats.WINDOW_STATS):
        v.stat_combo.setCurrentIndex(i)
        for _ in range(2):
            _APP.processEvents()
        assert v.window_stat() == stat
        text = v.window_label.text()
        assert f"· {chart_stats.STAT_SHORT[stat]}   " in text, (stat, text)
        seen.add(text)
    assert len(seen) == len(chart_stats.WINDOW_STATS), "two statistics printed the same line"
    v.deleteLater()
    print("test_every_statistic_is_reachable_and_says_which_one_it_is OK")


def test_the_readout_never_paints_outside_its_label():
    """The house standard: a readout that does not fit is a finding, not a shipped clip. Checked at
    the app's default charts width and at the narrowest the charts panel can be dragged to."""
    from PySide6.QtGui import QFontMetrics
    for width in (920, 600, 560):
        v = _view(select=[1], size=(width, 520))
        _park(v, 1, 0.3)
        v.toggle_datum()
        _park(v, 1, 0.7)
        for _ in range(4):
            _APP.processEvents()
        for label in (v.window_label, v.datum_label):
            fm = QFontMetrics(label.font())
            for line in label.text().split("\n"):
                need = fm.horizontalAdvance(line)
                assert need <= label.width(), (
                    f"at {width} px the readout wants {need} px in a {label.width()} px "
                    f"label: {line!r}")
        v.deleteLater()
    print("test_the_readout_never_paints_outside_its_label OK")


def test_the_loss_tour_moves_the_cursor_the_charts_and_the_app():
    v = _view(select=[1])
    _park(v, 1, 0.05)
    seen = []
    v.scrubStarted.connect(lambda: seen.append("start"))
    v.scrubMoved.connect(lambda x, m: seen.append(("move", round(x, 3), m)))
    v.scrubEnded.connect(lambda: seen.append("end"))
    spans = v.loss_spans()
    assert len(spans) >= 2, spans
    assert spans[0].loss > spans[1].loss

    v.jump_to_next_loss()
    for _ in range(3):
        _APP.processEvents()
    assert v.loss_index() == 0
    # the whole scrub gesture went out, so ScrubController converts, seeks and fans out
    assert seen[0] == "start" and seen[-1] == "end", seen
    assert seen[1] == ("move", round(spans[0].x0, 3), "distance"), seen
    # the charts zoomed to the span with a span's width of context either side
    lo, hi = v.p_speed.getViewBox().viewRange()[0]
    assert lo <= spans[0].x0 and hi >= spans[0].x1, (lo, hi, spans[0])
    assert (hi - lo) < 0.5 * 1000.0, "a 'zoom' that shows half the lap is not a zoom"
    # ...and the datum marks the far end, so the readout describes the loss
    assert abs(v.datum_x() - spans[0].x1) < 1e-9
    assert f"Δ loss 1 of {len(spans)}" in v.datum_label.text(), v.datum_label.text()

    v.jump_to_next_loss()
    for _ in range(3):
        _APP.processEvents()
    assert v.loss_index() == 1
    assert abs(v.datum_x() - spans[1].x1) < 1e-9
    v.deleteLater()
    print("test_the_loss_tour_moves_the_cursor_the_charts_and_the_app OK")


def test_the_tour_walks_back_out_to_the_whole_lap():
    """One key in, one key out: the step after the last span is the resting state, so a driver is
    never stranded zoomed in with no way back but the mouse."""
    v = _view(select=[1])
    _park(v, 1, 0.05)
    n = len(v.loss_spans())
    assert n >= 2
    for _ in range(n):
        v.jump_to_next_loss()
        for _ in range(2):
            _APP.processEvents()
    assert v.loss_index() == n - 1
    v.jump_to_next_loss()
    for _ in range(3):
        _APP.processEvents()
    assert v.loss_index() == -1
    assert v.datum_x() is None, "leaving the tour takes its datum with it"
    lo, hi = v.p_speed.getViewBox().viewRange()[0]
    assert (hi - lo) > 900.0, (lo, hi)
    # ...and pressing on starts the tour again from the worst one
    v.jump_to_next_loss()
    for _ in range(2):
        _APP.processEvents()
    assert v.loss_index() == 0
    v.deleteLater()
    print("test_the_tour_walks_back_out_to_the_whole_lap OK")


def test_the_tour_is_silent_on_a_lap_with_nothing_to_show():
    """The degenerate case, in the app: the baseline lap's Δ against itself is flat. With the ideal
    turned off too there is nothing to walk, and the gesture must stay at rest rather than zoom
    somewhere arbitrary."""
    v = _view(n=2, select=[0], ideal=False)
    _park(v, 0, 0.4)
    assert v.loss_spans() == []
    before = v.p_speed.getViewBox().viewRange()[0]
    v.jump_to_next_loss()
    for _ in range(3):
        _APP.processEvents()
    assert v.loss_index() == -1
    assert v.p_speed.getViewBox().viewRange()[0] == before, "nothing to find, nothing to move"
    assert v.datum_x() is None
    v.deleteLater()
    print("test_the_tour_is_silent_on_a_lap_with_nothing_to_show OK")


def test_the_best_lap_alone_still_has_losses_to_walk():
    """...but only because the chart has ALREADY re-referenced itself to the ideal there (P7). The
    flat-zero degenerate case and the useful one are the same selection with a different baseline,
    and the tour must follow the baseline the chart is actually drawing."""
    v = _view(n=2, select=[0], ideal=True)
    _park(v, 0, 0.4)
    assert v._delta_baseline_kind == plots_view.DELTA_BASELINE_IDEAL
    assert v.loss_spans() == [], "a CONSTANT Δ-to-ideal offset is still flat: nothing to walk"
    v.deleteLater()
    # ...and with a real ideal gap that varies, the same selection does have somewhere to go.
    sess = _Sess(_laps(2), best=0, ideal=True)
    xs = np.linspace(0.0, 1000.0, 400)
    _x, rising = _delta_with_losses([(600.0, 0.8)], n=400)
    sess.delta_to_ideal = lambda ids, x_mode="distance": {0: (xs, rising)}
    v2 = plots_view.PlotsView(sess)
    v2.resize(920, 520)
    v2.show()
    v2.set_laps([0])
    for _ in range(5):
        _APP.processEvents()
    _park(v2, 0, 0.1)
    spans = v2.loss_spans()
    assert spans and abs(spans[0].centre - 600.0) < 60.0, spans
    v2.deleteLater()
    print("test_the_best_lap_alone_still_has_losses_to_walk OK")


def test_the_chart_controls_include_the_new_selector_when_dead():
    """L6-07's contract, extended: a control is live exactly when clicking it would change the
    chart, and the new one is no exception."""
    v = plots_view.PlotsView(_Sess({}, best=None))
    v.resize(920, 520)
    v.show()
    v.set_laps([])
    for _ in range(4):
        _APP.processEvents()
    assert v.stat_combo.isEnabled() is False
    assert v.stat_combo.toolTip().endswith(plots_view.NO_DATA_TIP)
    assert v.window_label.text() == plots_view.READOUT_HINT
    assert not v.datum_label.isVisible()
    v.deleteLater()
    print("test_the_chart_controls_include_the_new_selector_when_dead OK")


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ALL CHART-INSTRUMENT TESTS OK")


if __name__ == "__main__":
    _run_all()
