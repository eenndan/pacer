"""Speed-units toggle (km/h ↔ mph) unit tests.

Covers the single source of truth + every display boundary the toggle routes through:
  * studio.units — the pure km/h↔mph conversion / label / format helpers (no Qt, no pacer);
  * studio.prefs — the persisted-choice round-trip + safe default (monkeypatched app-support seam);
  * studio.theme — the readout formatters honour the unit (default stays km/h → byte-identical);
  * studio.coaching.reason_sentence — the apex-deficit converts (default km/h);
  * studio.map_render.rainbow_channel — the speed legend label converts;
  * studio.export_video — the OverlayConfig carries the unit and the burned readout uses it;
  * the LapTable Entry header + value flip and the CornerTable speed cells (offscreen Qt).

Default is ALWAYS km/h → existing behaviour/tests are unchanged unless they set mph explicitly.
Run: QT_QPA_PLATFORM=offscreen python tests/test_units.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import coaching, map_render, prefs, theme, units  # noqa: E402


# ============================================================= studio.units (pure)
def test_convert_and_label():
    """1 km/h = 0.621371 mph; km/h is the identity. Labels are 'km/h' / 'mph'."""
    assert units.convert_speed(100.0, units.KMH) == 100.0
    assert abs(units.convert_speed(100.0, units.MPH) - 62.1371) < 1e-4
    assert units.speed_label(units.KMH) == "km/h"
    assert units.speed_label(units.MPH) == "mph"
    print("test_convert_and_label OK")


def test_format_speed_both_units():
    """format_speed rounds + appends the label; the brief's 100 km/h → '62 mph'."""
    assert units.format_speed(100.0, units.MPH) == "62 mph"
    assert units.format_speed(100.0, units.KMH) == "100 km/h"
    assert units.format_speed(88.0, units.KMH, decimals=1) == "88.0 km/h"
    # 88 km/h ≈ 54.68 mph → 54.7 at 1dp.
    assert units.format_speed(88.0, units.MPH, decimals=1) == "54.7 mph"
    print("test_format_speed_both_units OK")


def test_normalize_unit_defaults_kmh():
    """Any stale / garbage unit id falls back to km/h (never crashes a formatter)."""
    for bad in (None, "", "MPH", "mp/h", "furlongs"):
        assert units.normalize_unit(bad) == units.KMH
    assert units.normalize_unit(units.MPH) == units.MPH
    assert units.DEFAULT_UNIT == units.KMH
    print("test_normalize_unit_defaults_kmh OK")


# ============================================================= studio.prefs (persistence)
def test_prefs_roundtrip_and_default(tmp_path=None):
    """The persisted speed unit round-trips through the JSON store; a missing file defaults to
    km/h; a corrupt file self-heals to the default. Uses an explicit path (the app-support seam)."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "prefs.json")
        # Missing file → km/h default.
        assert prefs.speed_unit(path) == units.KMH
        # Set + read back.
        prefs.set_speed_unit(units.MPH, path)
        assert prefs.speed_unit(path) == units.MPH
        assert os.path.exists(path)
        # A garbage stored value normalizes back to km/h on read.
        prefs.set(prefs.SPEED_UNIT, "bogus", path)
        assert prefs.speed_unit(path) == units.KMH
        # A corrupt file → safe empty dict → default.
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not json")
        assert prefs.speed_unit(path) == units.KMH
    print("test_prefs_roundtrip_and_default OK")


def test_prefs_app_support_seam_matches_library():
    """prefs stores next to the library (same app-support dir), so both survive a relaunch the
    same way — the persistence mechanism the toggle mirrors."""
    from studio import library

    assert os.path.dirname(prefs.prefs_path()) == os.path.dirname(library.library_path())
    assert prefs.prefs_path().endswith("prefs.json")
    print("test_prefs_app_support_seam_matches_library OK")


# ============================================================= studio.theme (readout)
def test_theme_readout_default_is_kmh():
    """Default (no unit) keeps the exact km/h strings the live #DiffBox + export shipped with —
    the existing tests' byte-identity must not move."""
    assert theme.format_delta_speed(0.0, 73.4, 2)[0] == "Δ +0.00 s     73 km/h"
    assert theme.format_speed_run(88.0, 2) == "88 km/h"
    assert theme.speed_number(73.4, 2) == "73"
    print("test_theme_readout_default_is_kmh OK")


def test_theme_readout_mph():
    """With mph, the speed number converts and the label flips; the Δ scalar is untouched."""
    # 73.4 km/h → 45.6 mph → "46".
    assert theme.format_delta_speed(0.0, 73.4, 2, units.MPH)[0] == "Δ +0.00 s     46 mph"
    assert theme.format_speed_run(88.0, 2, units.MPH) == "55 mph"   # 88 km/h → 54.68 → 55
    assert theme.speed_number(100.0, 2, units.MPH) == "62"
    # No lap → honest em-dash + the (now mph) label.
    assert theme.format_speed_run(73.4, None, units.MPH) == "— mph"
    ideal, _ = theme.format_ideal_readout(0.5, 100.0, 2, units.MPH)
    assert ideal.endswith("62 mph")
    print("test_theme_readout_mph OK")


# ============================================================= studio.coaching (advice text)
def _apex_opportunity(deficit_kmh: float) -> coaching.Opportunity:
    reason = coaching.Reason(kind=coaching.REASON_APEX, contribution=0.3,
                             apex_speed_deficit=deficit_kmh, brake_extra_s=0.0,
                             coast_extra_s=0.0, sigma=0.0)
    return coaching.Opportunity(cid=1, direction=1, time_lost=0.3, entry_dist=0.0,
                                reason=reason)


def test_reason_sentence_units():
    """The apex-deficit in the coaching sentence converts to the display unit; default km/h keeps
    the exact string test_coaching pins ('5.0 km/h')."""
    opp = _apex_opportunity(5.0)
    assert "5.0 km/h" in coaching.reason_sentence(opp)              # default
    assert "5.0 km/h" in coaching.reason_sentence(opp, units.KMH)
    # 5 km/h → 3.1 mph.
    assert "3.1 mph" in coaching.reason_sentence(opp, units.MPH)
    print("test_reason_sentence_units OK")


# ============================================================= studio.map_render (rainbow legend)
def test_rainbow_speed_legend_units():
    """The speed rainbow legend end-labels convert; the COLOURS (bucket ids) are km/h-invariant."""
    n = 20
    t = np.linspace(0.0, 1.0, n)
    xs = np.linspace(0.0, 100.0, n)
    ys = np.zeros(n)
    speed = np.linspace(20.0, 60.0, n)   # km/h
    cum = np.linspace(0.0, 100.0, n)

    seg_k, lo_k, hi_k = map_render.rainbow_channel(
        "speed", t, xs, ys, speed, cum, None, None, units.KMH)
    seg_m, lo_m, hi_m = map_render.rainbow_channel(
        "speed", t, xs, ys, speed, cum, None, None, units.MPH)

    assert hi_k == "60 km/h" and lo_k == "20"
    assert hi_m == "37 mph" and lo_m == "12"          # 60 km/h → 37.3, 20 km/h → 12.4
    # Bucket ids identical → colours don't move with the unit (scale-invariant).
    assert np.array_equal(seg_k, seg_m)
    # Default (no unit arg) == km/h.
    _, _, hi_default = map_render.rainbow_channel("speed", t, xs, ys, speed, cum, None, None)
    assert hi_default == "60 km/h"
    print("test_rainbow_speed_legend_units OK")


# ============================================================= studio.export_video (overlay)
def test_export_config_default_and_unit():
    """OverlayConfig defaults to km/h; a caller can burn mph into the export."""
    from studio import export_video

    assert export_video.OverlayConfig().speed_unit == units.KMH
    assert export_video.OverlayConfig(speed_unit=units.MPH).speed_unit == units.MPH
    print("test_export_config_default_and_unit OK")


def test_export_readout_paints_unit_label():
    """The burned-in readout uses the config's unit: paint the readout twice (km/h vs mph) and
    assert the rendered pixels differ — a proxy for 'the label/number changed'. Same OverlayValues,
    only the unit varies."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    from studio import export_video

    vals = export_video.OverlayValues(t=1.0, lap_id=2, speed_kmh=100.0, delta_s=0.0,
                                      g=None, marker_index=0)
    box = QRectF(0.0, 0.0, 320.0, 44.0)

    def _render(unit):
        img = QImage(360, 60, QImage.Format_RGB888)
        img.fill(0)
        p = QPainter(img)
        export_video._paint_readout(p, box, vals, unit)
        p.end()
        return bytes(img.constBits())

    km = _render(units.KMH)
    mph = _render(units.MPH)
    assert km != mph, "the km/h and mph readouts must render differently (label + number change)"
    # Default (no unit) matches km/h byte-for-byte.
    img = QImage(360, 60, QImage.Format_RGB888)
    img.fill(0)
    p = QPainter(img)
    export_video._paint_readout(p, box, vals)
    p.end()
    assert bytes(img.constBits()) == km, "default readout must be identical to km/h"
    print("test_export_readout_paints_unit_label OK")


# ============================================================= LapTable / CornerTable (offscreen)
class _FakeCorners:
    """Just enough corner model for CornerTable.refresh over one lap."""

    def __init__(self):
        from studio.corners import Corner, CornerStat
        self._stats = [CornerStat(cid=1, time=2.0, delta=0.05, apex_speed=80.0,
                                  apex_speed_delta=-4.0, apex_dist=10.0,
                                  entry_speed=100.0, exit_speed=90.0)]
        self._corner = Corner(cid=1, enter=0.0, exit=20.0, apex=10.0,
                              direction=1, turn_deg=90.0)

    def lap_corner_stats(self, lid):
        return self._stats

    def corner_list(self):
        return [self._corner]

    def corner_session_bests(self):
        return [2.0]


class _FakeDriving:
    def lap_corner_grip(self, lid):
        return [0.9]


class _FakeQuality:
    degraded = False


class _FakeSession:
    """Minimal session for a LapTable/CornerTable refresh — one lap, no sectors."""

    timing_verified = True
    timing_quality = _FakeQuality()

    def __init__(self):
        self.corners = _FakeCorners()
        self.driving = _FakeDriving()

    def lap_rows(self):
        return [{"idx": 0, "time": 60.0, "dist": 1000.0, "entry": 100.0}]

    def sector_count(self):
        return 0

    def lap_sector_splits(self, lid):
        return []

    def session_best_splits(self):
        return []

    def dropout_lap_ids(self):
        return set()

    def theoretical_best(self):
        return 59.0

    def best_rolling_lap(self):
        return 60.0

    def best_lap_id(self):
        return 0

    def lap_count(self):
        return 1


def test_lap_table_entry_header_and_value_flip():
    """LapTable: default → 'Entry (km/h)' with the raw km/h value; set mph → header + value flip."""
    from studio.lap_table import LapTable

    tbl = LapTable(_FakeSession())
    # Entry is the last base column (index 3).
    entry_col = 3
    assert tbl.table.horizontalHeaderItem(entry_col).text() == "Entry (km/h)"
    assert tbl.table.item(0, entry_col).text() == "100.0"

    tbl.set_speed_unit(units.MPH)
    assert tbl.table.horizontalHeaderItem(entry_col).text() == "Entry (mph)"
    # 100 km/h → 62.1 mph.
    assert tbl.table.item(0, entry_col).text() == "62.1"

    # Flipping back restores km/h.
    tbl.set_speed_unit(units.KMH)
    assert tbl.table.horizontalHeaderItem(entry_col).text() == "Entry (km/h)"
    assert tbl.table.item(0, entry_col).text() == "100.0"
    print("test_lap_table_entry_header_and_value_flip OK")


def test_corner_table_speed_cells_flip():
    """CornerTable: apex/entry/exit + apex-Δ speed cells convert on a unit flip; the tooltips name
    the unit. Columns: 3 Apex, 4 Δapex, 5 Entry, 6 Exit."""
    from studio.lap_table import CornerTable

    ct = CornerTable(_FakeSession())
    ct.set_lap(0)
    assert ct.table.item(0, 3).text() == "80.0"     # apex km/h
    assert ct.table.item(0, 5).text() == "100.0"    # entry km/h
    assert "km/h" in ct.table.horizontalHeaderItem(3).toolTip()

    ct.set_speed_unit(units.MPH)
    assert ct.table.item(0, 3).text() == "49.7"     # 80 km/h → 49.71
    assert ct.table.item(0, 5).text() == "62.1"     # 100 km/h → 62.14
    # Δ apex is a difference: -4 km/h → -2.5 mph (sign preserved).
    assert ct.table.item(0, 4).text() == "-2.5"
    assert "mph" in ct.table.horizontalHeaderItem(5).toolTip()
    print("test_corner_table_speed_cells_flip OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} UNITS TESTS PASSED")
