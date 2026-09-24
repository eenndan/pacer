"""A lap ends where it started, going the same way (L3): `_signal._classify_laps`' closure test.

A start/finish line long enough to reach a SECOND stretch of track cuts every pass it reaches into
two pieces. The ±10 % bands centre on the MEDIAN piece, so when pieces outnumber laps they are
what gets counted: Sandown chapter 3 opened alone counted a 23.2 s / 320 m piece of a 740 m
circuit as its one lap (found by #322). A real lap's finish crossing is on the stretch its start
crossing was on, travelling the same way; a piece's is not.

Pinned here on REAL `pacer.Laps` segmentations of a synthetic hairpin (two straights 21 m apart,
a line that reaches both on most passes) driven through the real `Session`, and on the four
surfaces that say why a lap was left out (the ⊘ strip, the DATA TRUST card, the exported summary
and the auto mark) — plus the two thresholds against the populations they were measured to
separate on the owner's footage (see the block above `_signal.MAX_LAP_GAP_M`).

Run:  PYTHONPATH=bindings/pacer QT_QPA_PLATFORM=offscreen python tests/test_lap_closure.py
"""
import math
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pacer  # noqa: E402
from studio import _signal, tracks  # noqa: E402
from studio.session import Session  # noqa: E402

_CLAT, _CLON = 52.0, -0.78
_M_PER_DEG_LAT = 111_320.0
_GAP = 21.0              # m between the two straights
_EAST_RUN = 250.0        # m of straight east of the line
_WEST_RUN = 50.0         # m of straight west of it
_SPEED = 20.0            # m/s at 10 Hz: 2 m per fix
_LINE_Y = (-5.0, 23.0)   # the start/finish line along x = 0: reaches y = 21, not y = 26
_WIDE_Y = 26.0           # the return straight on a WIDE lap, out of the line's reach
_ARC = math.pi * _GAP / 2
_LAP_M = 2 * (_EAST_RUN + _WEST_RUN) + 2 * _ARC      # ≈ 666 m on the 21 m hairpin
_LONG_PIECE = 2 * _EAST_RUN + _ARC                    # ≈ 533 m: line -> return straight
_SHORT_PIECE = _LAP_M - _LONG_PIECE                   # ≈ 133 m: return straight -> line


def _hairpin_xy(s, wide_laps=()):
    """The point `s` metres round the hairpin (lap k spans [k·_LAP_M, (k+1)·_LAP_M)), eastbound
    from (0, 0) along y = 0, westbound back along the return straight. On a lap in `wide_laps`
    the return straight runs at _WIDE_Y, beyond the line's end, so that pass crosses the line
    once and is a whole lap; every other pass crosses it twice and is cut into two pieces."""
    k = math.floor(s / _LAP_M)
    s -= k * _LAP_M
    y_ret = _WIDE_Y if k in wide_laps else _GAP
    r = y_ret / 2
    if s <= _EAST_RUN:
        return s, 0.0
    s -= _EAST_RUN
    if s <= _ARC:
        a = math.pi * s / _ARC
        return _EAST_RUN + r * math.sin(a), r - r * math.cos(a)
    s -= _ARC
    if s <= _EAST_RUN + _WEST_RUN:
        return _EAST_RUN - s, y_ret
    s -= _EAST_RUN + _WEST_RUN
    if s <= _ARC:
        a = math.pi * s / _ARC
        return -_WEST_RUN - r * math.sin(a), r + r * math.cos(a)
    return -_WEST_RUN + (s - _ARC), 0.0


def _gps(x, y):
    lon = _CLON + x / (_M_PER_DEG_LAT * math.cos(math.radians(_CLAT)))
    return pacer.GPSSample(lat=_CLAT + y / _M_PER_DEG_LAT, lon=lon, altitude=0.0,
                           full_speed=_SPEED, ground_speed=_SPEED)


def _hairpin_session(s_from, s_to, wide_laps=()):
    """A real `Session` over a real `pacer.Laps` of the hairpin from `s_from` to `s_to` metres,
    segmented on the line along x = 0 from y = _LINE_Y[0] to _LINE_Y[1]."""
    laps = pacer.Laps()
    n = int((s_to - s_from) / (_SPEED * 0.1))
    for i in range(n + 1):
        laps.add_point(_gps(*_hairpin_xy(s_from + i * _SPEED * 0.1, wide_laps)), i * 0.1)
    mn, mx = laps.min_max()
    cs = pacer.CoordinateSystem(
        pacer.GPSSample(lat=(mn.y + mx.y) / 2, lon=(mn.x + mx.x) / 2, altitude=0.0))
    laps.set_coordinate_system(cs)
    a, b = cs.local(_gps(0.0, _LINE_Y[0])), cs.local(_gps(0.0, _LINE_Y[1]))
    laps.sectors = pacer.Sectors(start_line=tracks.make_segment(a[0], a[1], b[0], b[1]),
                                 sector_lines=[])
    laps.update()
    return Session(laps, cs, None)


def _mixed_session():
    """Seven passes: laps 0 and 1 run wide (two whole laps), laps 2-6 are each cut in two. That is
    2 laps, 5 long pieces and 5 short ones — the long piece is the median, exactly the arrangement
    in which the bands keep the pieces and throw the laps away."""
    return _hairpin_session(-40.0, 7 * _LAP_M + 40.0, wide_laps=(0, 1))


def _dists(s, ids):
    return [round(float(s.laps.get_lap_distance(i))) for i in ids]


def test_pieces_that_outnumber_the_laps_are_not_counted_and_the_laps_are():
    s = _mixed_session()
    substantial = [i for i in range(s.lap_count()) if s.laps.lap_time(i) >= _signal.MIN_LAP_TIME]
    whole = [i for i in substantial if abs(s.laps.get_lap_distance(i) - _LAP_M) < 0.05 * _LAP_M]
    pieces = [i for i in substantial if i not in whole]
    # Non-vacuous: the fixture really is two laps among ten pieces of one.
    assert len(whole) == 2 and len(pieces) == 10, (_dists(s, whole), _dists(s, pieces))

    valid = s.valid_lap_ids()
    assert valid == whole, (
        f"counted {len(valid)} 'laps' of {_dists(s, valid)} m on a {_LAP_M:.0f} m circuit; the "
        f"{len(whole)} whole laps ({_dists(s, whole)} m) should be the ones counted, and the "
        f"pieces, which run from one straight to the other {_GAP:.0f} m away, should not")
    assert s.excluded_lap_ids() == pieces
    assert s.excluded_lap_reasons() == {i: _signal.EXCLUDED_OPEN for i in pieces}
    print(f"test_pieces_that_outnumber_the_laps_are_not_counted_and_the_laps_are OK "
          f"(counts {_dists(s, valid)} m; excludes {len(pieces)} pieces)")


def test_a_recording_holding_only_a_piece_has_no_laps():
    """Sandown chapter 3 opened alone, in miniature: the trace holds one crossing of the start
    straight, one of the return straight and nothing else, so its only substantial 'lap' is a
    piece. With nothing to band against, the median of one is itself, and it was counted."""
    s = _hairpin_session(-40.0, _LONG_PIECE + 40.0)
    substantial = [i for i in range(s.lap_count()) if s.laps.lap_time(i) >= _signal.MIN_LAP_TIME]
    assert len(substantial) == 1 and abs(s.laps.get_lap_distance(substantial[0]) - _LONG_PIECE) < 5
    assert s.valid_lap_ids() == [], (
        f"a {s.laps.get_lap_distance(substantial[0]):.0f} m piece of a {_LAP_M:.0f} m lap is "
        f"counted as the recording's only lap")
    assert s.excluded_lap_ids() == substantial, "the piece must be SHOWN as excluded, not dropped"
    assert s.excluded_lap_reasons() == {substantial[0]: _signal.EXCLUDED_OPEN}
    print("test_a_recording_holding_only_a_piece_has_no_laps OK")


def _cols(gap_m, turn_deg):
    """Point columns for a lap that leaves (0, 0) heading +x and finishes `gap_m` away travelling
    `turn_deg` from that heading. Only the two chords at each end carry the closure."""
    t = math.radians(turn_deg)
    end = (0.0, gap_m)
    xs = [0.0, 2.0, 100.0, end[0] - 2.0 * math.cos(t), end[0]]
    ys = [0.0, 0.0, 50.0, end[1] - 2.0 * math.sin(t), end[1]]
    return SimpleNamespace(xs=xs, ys=ys)


def test_the_thresholds_sit_between_the_populations_measured_on_the_owners_footage():
    """Each extreme below is a lap measured on the owner's recordings (all four, every chapter,
    the loader's, track-DB, sidecar and heuristic lines, and 60 dragged lines each)."""
    is_open = _signal._is_open_lap
    # The worst real laps: a 30 m line in a D24 corner, ends 8.45 m apart; a grazing line, 97.4 deg.
    assert not is_open(_cols(8.45, 4.0))
    assert not is_open(_cols(7.03, 97.4))
    # A 935 m D24 piece between the two sides of a hairpin: ends only 6.48 m apart, but it turned
    # round. A gap threshold alone cannot drop it without dropping the real lap above.
    assert is_open(_cols(6.48, 178.0))
    # A piece that did NOT turn round (a third stretch, crossed the same way): 21.6 m at the least.
    assert is_open(_cols(21.6, 1.0))
    # The boundaries are strict: exactly at a threshold is still closed.
    assert not is_open(_cols(_signal.MAX_LAP_GAP_M, 0.0))
    assert is_open(_cols(_signal.MAX_LAP_GAP_M + 0.01, 0.0))
    assert not is_open(_cols(0.0, _signal.MAX_LAP_TURN_DEG - 0.01))
    assert is_open(_cols(0.0, _signal.MAX_LAP_TURN_DEG + 0.01))
    # No evidence, no verdict: too few points, a zero-length chord, a double without positions.
    assert _signal._lap_closure([0.0, 30.0], [0.0, 0.0]) == (0.0, 0.0)
    assert _signal._lap_closure([0.0, 0.0, 5.0, 5.0], [0.0, 0.0, 0.0, 0.0])[1] == 0.0
    assert not is_open(SimpleNamespace(times=[0.0, 1.0], full_speed=[30.0, 30.0]))
    gap, turn = _signal._lap_closure(*(lambda c: (c.xs, c.ys))(_cols(12.0, 150.0)))
    assert abs(gap - 12.0) < 1e-9 and abs(turn - 150.0) < 1e-6, (gap, turn)
    print("test_the_thresholds_sit_between_the_populations_measured_on_the_owners_footage OK")


class _Laps:
    """The pacer.Laps read surface `_classify_laps` touches, with positions and speeds."""

    def __init__(self, rows):
        self._rows = rows  # [(time_s, dist_m, gap_m, turn_deg, stop_s)]

    def laps_count(self):
        return len(self._rows)

    def lap_time(self, i):
        return self._rows[i][0]

    def sample_count(self, i):
        return 500

    def get_lap_distance(self, i):
        return self._rows[i][1]

    def lap_columns(self, i):
        t, _d, gap, turn, stop = self._rows[i]
        c = _cols(gap, turn)
        n = int(round(t / 0.1))
        speed = [30.0] * n
        for k in range(int(round(stop / 0.1))):
            speed[n // 2 + k] = 0.0
        c.times = [k * 0.1 for k in range(n)]
        c.full_speed = speed
        return c


def test_every_excluded_lap_carries_the_first_test_it_failed():
    laps = _Laps([
        (60.0, 1000.0, 0.5, 2.0, 0.0),     # 0 real
        (61.0, 1002.0, 1.0, 3.0, 0.0),     # 1 real
        (60.5, 998.0, 0.2, 1.0, 0.0),      # 2 real
        (40.0, 700.0, 21.0, 180.0, 0.0),   # 3 a piece: open (and off the median too — open wins)
        (30.0, 500.0, 0.4, 2.0, 0.0),      # 4 a closed but short lap: off the median
        (66.0, 1001.0, 0.3, 1.0, 6.0),     # 5 a closed lap with a 6 s stop
        (62.0, 999.0, 0.6, 2.0, 0.0),      # 6 real
    ])
    valid, reasons = _signal._classify_laps(laps)
    assert valid == [0, 1, 2, 6], valid
    assert reasons == {3: _signal.EXCLUDED_OPEN, 4: _signal.EXCLUDED_BAND,
                       5: _signal.EXCLUDED_STOPPED}, reasons
    assert sorted(reasons) == _signal._banded_out_lap_ids(laps), "the reasons must cover exactly the ⊘ laps"
    assert _signal._band_lap_ids(laps) == valid
    assert _signal.exclusion_summary(reasons) == (
        "1 doesn't end where it started, 1 off the session median, 1 with a stop")
    assert _signal.exclusion_summary({1: "open", 2: "open", 3: "band"}) == (
        "2 don't end where they started, 1 off the session median")
    assert _signal.exclusion_summary({}) == ""
    assert _signal.exclusion_detail("open", 21.4, 178.0) == "ends 21 m from its start, heading the other way"
    assert _signal.exclusion_detail("open", 22.0, 3.0) == "ends 22 m from its start"
    assert _signal.exclusion_detail("band") == "off the session median"
    assert _signal.exclusion_detail("stopped") == "the kart stopped during it"
    print("test_every_excluded_lap_carries_the_first_test_it_failed OK")


def test_every_surface_names_the_same_reason():
    """The ⊘ strip, the DATA TRUST card, the exported summary and the auto marks all said an
    excluded lap's "distance is off the session median" (or said nothing), which was false for a
    lap with a stop and is false for a piece. Driven over the real Session and the real widgets."""
    from PySide6.QtWidgets import QApplication

    from studio import export_data
    from studio.lap_table import LapTable
    from studio.stats_panel import StatsView

    _app = QApplication.instance() or QApplication([])  # noqa: F841 — keeps the widgets alive
    s = _mixed_session()
    why = "ends 21 m from its start, heading the other way"

    table = LapTable(s)
    assert "10 excluded of 13 laps found" in table._excluded_header.text(), table._excluded_header.text()
    table._toggle_excluded_collapsed()
    lines = table._excluded_body.text().split("\n")
    assert len(lines) == 10 and all(line.endswith(f"· {why}") for line in lines), lines

    card = dict((t, v) for t, v, _c in StatsView(s).trust.card.rows())
    assert "10 ⊘ excluded: 10 don't end where they started" in card["Statistics use"], card

    laps_row = dict(next(sec for sec in export_data.stats_summary(s) if sec.title == "SESSION").rows)
    assert laps_row["laps"] == "2 valid · 10 excluded (10 don't end where they started)", laps_row

    notes = [m["note"] for m in s.auto_marks()[0] if m["type"] == "excluded"]
    assert notes == [f"lap left out of the times — {why}"] * 10, notes
    print("test_every_surface_names_the_same_reason OK")


if __name__ == "__main__":
    test_pieces_that_outnumber_the_laps_are_not_counted_and_the_laps_are()
    test_a_recording_holding_only_a_piece_has_no_laps()
    test_the_thresholds_sit_between_the_populations_measured_on_the_owners_footage()
    test_every_excluded_lap_carries_the_first_test_it_failed()
    test_every_surface_names_the_same_reason()
    print("\n5 lap-closure tests passed")
