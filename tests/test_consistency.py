"""Synthetic unit tests for studio.consistency + the ConsistencyPanel (F6).

The statistics must equal direct numpy on the same arrays EXACTLY (σ = np.std(ddof=1),
median loss = np.median − np.min, score = the documented σ × median-loss product) and the
ranking must follow the score. The Session wiring runs on a bare Session (tests/_synthetic
seeding idiom — no pacer Laps, no telemetry file) and must EXCLUDE GPS-dropout laps (the
⚠ rule). The panel + the map's corner-highlight ring run offscreen on stubs: populate,
click→signal, collapse/expand round-trip.
Run:  QT_QPA_PLATFORM=offscreen python tests/test_consistency.py
"""
import math
import os
import sys
from types import SimpleNamespace

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from studio import consistency as Y  # noqa: E402

RNG = np.random.default_rng(7)


# ------------------------------------------------------------------ pure statistics
def test_sigma_is_sample_std_exactly():
    vals = RNG.normal(70.0, 0.8, size=11)
    assert Y.sigma(vals) == float(np.std(vals, ddof=1))
    # non-finite entries are excluded — equals np.std on the filtered array
    with_nan = np.concatenate((vals, [np.nan, np.inf]))
    assert Y.sigma(with_nan) == float(np.std(vals, ddof=1))
    # a spread needs >= 2 samples
    assert Y.sigma([]) is None
    assert Y.sigma([68.4]) is None
    assert Y.sigma([68.4, np.nan]) is None
    print("ok sigma: == np.std(ddof=1), NaN-filtered, None under 2 samples")


def test_sector_sigmas_per_column():
    full = [list(RNG.normal(20.0, 0.5, size=3)) for _ in range(6)]
    partial = list(RNG.normal(20.0, 0.5, size=2))  # a partial lap: only 2 of 3 splits
    splits = [*full, partial]
    got = Y.sector_sigmas(splits)
    assert len(got) == 3  # widest lap defines the column count
    for k in range(3):
        col = [sp[k] for sp in splits if k < len(sp)]
        assert got[k] == float(np.std(col, ddof=1)), (k, got[k])
    assert Y.sector_sigmas([]) == []
    print("ok sector sigmas: per-column == np.std, partial lap only in its columns")


def test_corner_spreads_match_numpy_and_ranking_weighting():
    # 8 laps x 4 corners with engineered shapes:
    #   C1 erratic around a GOOD time (high sigma, tiny median loss)
    #   C2 consistently slow (tiny sigma, big loss is impossible when consistent -> small)
    #   C3 BOTH erratic and slow -> must rank first under the sigma x median-loss product
    #   C4 metronomic (low sigma, low loss)
    base = np.array([5.0, 6.0, 7.0, 4.0])
    times = np.tile(base, (8, 1))
    times[:, 0] += np.array([-0.4, 0.5, -0.5, 0.4, -0.3, 0.3, 0.0, 0.0])  # spread, median ~base
    times[:, 1] += 0.02 * np.arange(8)                                    # slow drift, tiny sigma
    times[:, 2] += np.array([0.0, 1.0, 1.2, 0.1, 1.4, 0.9, 1.1, 1.3])     # erratic AND slow
    times[:, 3] += 0.01 * np.array([1, -1, 1, -1, 1, -1, 1, -1])          # metronomic
    spreads = Y.corner_spreads([1, 2, 3, 4], [list(row) for row in times])
    assert [s.cid for s in spreads] == [1, 2, 3, 4]
    for k, s in enumerate(spreads):
        col = times[:, k]
        assert s.sigma == float(np.std(col, ddof=1)), (s.cid, s.sigma)
        assert s.median_loss == float(np.median(col) - np.min(col)), (s.cid, s.median_loss)
        assert s.median_loss >= 0.0
        assert s.score == s.sigma * s.median_loss
        assert s.n == 8
    ranked = Y.rank_corners(spreads)
    assert [s.cid for s in ranked] == sorted([s.cid for s in spreads],
                                             key=lambda c: -spreads[c - 1].score)
    # the documented weighting: the both-erratic-AND-slow corner outranks the merely
    # erratic (C1) and the merely drifting (C2) ones
    assert ranked[0].cid == 3, ranked
    assert ranked[-1].cid == 4, ranked
    # a corner with < 2 finite times is dropped (sigma undefined): column 2 has one value
    # in the first case (a short row) and zero finite values in the second (NaNs)
    short = Y.corner_spreads([1, 2], [[1.0, 2.0], [1.1]])
    assert [s.cid for s in short] == [1] and short[0].n == 2
    only = Y.corner_spreads([1, 2], [[1.0, math.nan], [1.1, math.nan]])
    assert [s.cid for s in only] == [1]
    print("ok corner spreads: sigma/median/score == numpy, product ranks both-bad first")


def test_pb_mask_running_minimum():
    assert Y.pb_mask([]) == []
    assert Y.pb_mask([70.0, 71.2, 69.8, 69.8, 69.5]) == [True, False, True, False, True]
    print("ok pb mask: strict running minima, first lap counts")


# ------------------------------------------------------------------- Session wiring
def _stadium_session():
    """The test_corners bare-Session idiom: two clean stadium laps + a THIRD lap whose
    kept-point times contain an interior gap > gapfill.GAP_TIME_S (a GPS dropout), to
    drive the ⚠ exclusion. Lap times come from a stub `laps` (lap_time + no sectors)."""
    from _synthetic import bare_session
    from test_corners import elapsed_for, speed_profile, stadium

    from studio.session import _UNSET
    s = bare_session(valid=[0, 1, 2], best=0)
    s._cols_cache = {}
    s._corner_cache = _UNSET
    s._corner_stats_cache = {}
    s._corner_bests = _UNSET
    xs, ys, cum = stadium()
    sp_a = speed_profile(cum, 0.7)
    sp_b = speed_profile(cum, 2.1)
    t_a = 100.0 + elapsed_for(cum, sp_a)
    t_b = 300.0 + elapsed_for(cum, sp_b)
    s._cols_cache[0] = (t_a, xs, ys, sp_a, cum)
    s._cols_cache[1] = (t_b, xs, ys, sp_b, cum)
    # lap 2: same trace but with an interior time gap (dropout) — must be excluded
    t_c = 500.0 + elapsed_for(cum, sp_b)
    t_c[len(t_c) // 2:] += 1.0  # a 1.0 s jump between consecutive samples (> 0.35 s)
    s._cols_cache[2] = (t_c, xs, ys, sp_b, cum)
    lap_times = {0: float(t_a[-1] - t_a[0]), 1: float(t_b[-1] - t_b[0]),
                 2: float(t_c[-1] - t_c[0])}
    s.laps = SimpleNamespace(lap_time=lambda i: lap_times[i],
                             sectors=SimpleNamespace(sector_lines=[]))
    return s


def test_session_wiring_excludes_dropouts_and_matches_numpy():
    s = _stadium_session()
    assert s.consistency_lap_ids() == [0, 1], "the dropout lap (2) must be excluded"
    trend = s.lap_time_trend()
    assert [i for i, _t in trend] == [0, 1]
    assert all(t == s.laps.lap_time(i) for i, t in trend), "trend times == the table's"
    assert s.sector_sigmas() == []  # no sector lines -> no split columns
    ranked = s.corner_consistency()
    cs = s.corners()
    assert len(cs) == 2 and {sp.cid for sp in ranked} == {c.cid for c in cs}
    # exact vs direct numpy over the SAME per-lap corner times (laps 0 and 1 only)
    times = np.array([[st.time for st in s.lap_corner_stats(i)] for i in (0, 1)])
    by_cid = {sp.cid: sp for sp in ranked}
    for k, c in enumerate(cs):
        sp = by_cid[c.cid]
        assert sp.sigma == float(np.std(times[:, k], ddof=1))
        assert sp.median_loss == float(np.median(times[:, k]) - np.min(times[:, k]))
        assert sp.score == sp.sigma * sp.median_loss and sp.n == 2
    assert [sp.score for sp in ranked] == sorted((sp.score for sp in ranked), reverse=True)
    print("ok session wiring: dropout excluded, trend == lap_time, stats == numpy, ranked")


# ----------------------------------------------------------------------- UI (offscreen)
def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _StubSession:
    """Duck-typed stand-in for ConsistencyPanel: the four accessors it reads."""

    def __init__(self, trend, sector_sigs, ranked, corner_list):
        self._trend = trend
        self._sigs = sector_sigs
        self._ranked = ranked
        self._corners = corner_list

    def lap_time_trend(self):
        return self._trend

    def sector_sigmas(self):
        return self._sigs

    def corner_consistency(self):
        return self._ranked

    def corners(self):
        return self._corners


def _stub_panel():
    from studio.consistency_panel import TOP_N, ConsistencyPanel
    trend = [(0, 70.0), (1, 71.2), (2, 69.8), (4, 69.5), (5, 70.4)]  # lap 3 invalid (gap in x)
    sigs = [0.11, 0.32]
    # 7 ranked corners -> the panel must show only TOP_N
    ranked = [Y.CornerSpread(cid=c, sigma=0.5 - 0.05 * i, median_loss=0.4 - 0.04 * i,
                             score=(0.5 - 0.05 * i) * (0.4 - 0.04 * i), n=5)
              for i, c in enumerate((3, 7, 1, 5, 2, 4, 6))]
    corner_list = [SimpleNamespace(cid=c, direction=1 if c % 2 else -1)
                   for c in range(1, 8)]
    panel = ConsistencyPanel(_StubSession(trend, sigs, ranked, corner_list))
    return panel, trend, ranked, TOP_N


def test_panel_populates_trend_and_top5():
    _qapp()
    panel, trend, ranked, top_n = _stub_panel()
    # trend curve carries exactly the (lap id, time) series; PB dots = running minima
    xs, ys = panel._curve.getData()
    assert list(xs) == [i for i, _t in trend] and list(ys) == [t for _i, t in trend]
    pb = Y.pb_mask([t for _i, t in trend])
    px, py = panel._pb_dots.getData()
    assert list(px) == [i for (i, _t), on in zip(trend, pb, strict=True) if on]
    assert list(py) == [t for (_i, t), on in zip(trend, pb, strict=True) if on]
    # σ summary: lap σ == np.std(ddof=1) of the trend times + one entry per sector column
    times = [t for _i, t in trend]
    assert f"{np.std(times, ddof=1):.2f}" in panel.sigma_label.text()
    assert "S1 0.11" in panel.sigma_label.text() and "S2 0.32" in panel.sigma_label.text()
    # top-N rows in ranked order with the documented formats
    assert panel.table.rowCount() == top_n
    for r in range(top_n):
        sp = ranked[r]
        assert panel.table.item(r, 0).text().startswith(f"C{sp.cid}")
        assert panel.table.item(r, 1).text() == f"{sp.sigma:.2f}"
        assert panel.table.item(r, 2).text() == f"{sp.median_loss:+.2f}"
    print("ok panel: trend series + PB dots + sigma summary + top-5 ranked rows")


def test_panel_click_emits_cid_and_only_cid():
    _qapp()
    panel, _trend, ranked, _top_n = _stub_panel()
    got = []
    panel.corner_clicked.connect(got.append)
    panel.table.selectRow(1)
    assert got == [ranked[1].cid], got
    panel.table.selectRow(3)
    assert got == [ranked[1].cid, ranked[3].cid], got
    panel.table.clearSelection()
    assert got[-1] is None, "deselect must clear (emit None)"
    # refresh keeps quiet: rebuilding rows must not emit stale clicks
    n = len(got)
    panel.refresh()
    assert len(got) == n
    print("ok panel clicks: row -> cid, deselect -> None, refresh emits nothing")


def test_panel_collapse_roundtrip():
    _qapp()
    panel, _trend, _ranked, _top_n = _stub_panel()
    assert panel.body.isVisibleTo(panel) and panel.collapse_btn.text() == "▾"
    panel.collapse_btn.setChecked(True)
    assert not panel.body.isVisibleTo(panel) and panel.collapse_btn.text() == "▸"
    panel.collapse_btn.setChecked(False)
    assert panel.body.isVisibleTo(panel) and panel.collapse_btn.text() == "▾"
    print("ok collapse: body hides/shows, chevron flips, round-trip clean")


def test_map_corner_highlight_ring():
    _qapp()
    import pyqtgraph as pg

    from studio.map_view import _CornerMarkers
    widget = pg.PlotWidget()  # keep referenced — it owns the ViewBox
    cm = _CornerMarkers(widget.getPlotItem())
    markers = [("C1", 0.0, 0.0, 1), ("C2", 10.0, 5.0, -1), ("C3", -4.0, 8.0, 1)]
    cm.set_corners(markers)
    assert cm.highlighted is None
    cm.set_highlight("C2")
    assert cm.highlighted == "C2" and cm._highlight_item is not None
    x, y = cm._highlight_item.getData()
    assert float(x[0]) == 10.0 and float(y[0]) == 5.0, "ring sits on the apex marker"
    cm.set_highlight("C3")  # moving the highlight replaces the ring
    assert cm.highlighted == "C3"
    cm.set_highlight("C99")  # stale/unknown label just clears
    assert cm.highlighted is None and cm._highlight_item is None
    cm.set_highlight("C1")
    cm.set_corners(markers)  # a new corner set clears any active highlight
    assert cm.highlighted is None and cm._highlight_item is None
    cm.set_highlight(None)  # idempotent clear
    assert cm.highlighted is None
    print("ok map highlight: ring on apex, move/clear/stale-label/reset all clean")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nALL {len(tests)} CONSISTENCY TESTS PASSED")
