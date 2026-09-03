"""LapTable / CornerTable EMPTY + BASELINE + EXCLUDED states (QA L3-05 · L3-06 · L3-08 · L3-09).

Four states the panel got wrong by not asking a question it already had the answer to:

  * the Corners page OPENS on the session best (the app's own post-load selection), where
    `corner_model.lap_corner_stats` passes `ref=None` and `corners.lap_corner_stats`'s docstring
    documents the result — "None for the reference lap itself -> deltas 0". On F.B that painted
    24 of 24 Δ cells "+0.00"/"+0.0" — a quarter of the table carrying no measurement, with nothing
    on the page naming the lap it was comparing against (L3-05);
  * on a recording with no valid lap it said "Select a lap to see its corners.", an instruction
    that cannot be followed, beside a sibling placeholder in the same panel that stated the fact
    (L3-08);
  * 24 of 49 laps excluded (49%) was reported by the same muted 28px one-liner as one stray lap,
    with the ratio, the kept-vs-excluded distances and the 50-vs-25+24 count gap nowhere on screen
    (L3-06);
  * expanding that strip listed 6 of 24 laps and spent its 7th line on a dead "+18 more" naming
    rows no surface in the app would ever show, while still costing the grid four lap rows (L3-09).

Pure Qt on fake sessions (no pacer, no telemetry file). Run:
    python tests/test_lap_table_empty_states.py
"""
import os
import sys
from types import SimpleNamespace

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import data_quality, theme  # noqa: E402
from studio import lap_table as LT  # noqa: E402

_DELTA_BEST_COL, _DELTA_APEX_COL = 2, 4


# Every widget these tests build stays referenced for the run. An unparented LapTable collected
# mid-suite has its Python attributes torn down while Qt is still delivering resize events to the
# viewport it filters, which prints (pre-existing, harmless) override tracebacks to stderr.
_ALIVE: list = []


def _keep(widget):
    _ALIVE.append(widget)
    return widget


def _settle(n=4):
    """Qt needs several pumps to re-run a layout after a widget appears/disappears — a single
    processEvents() reads the PREVIOUS geometry (the harness's own #1 source of bogus findings)."""
    for _ in range(n):
        _APP.processEvents()


def _shown(widget, w=457, h=400):
    """Show + settle, so isVisible() means what it says: a child of an unshown parent reports
    False however its own setVisible() went."""
    widget.resize(w, h)
    widget.show()
    _settle()
    return _keep(widget)


class _FakeLapSession:
    """The LapTable read surface, with the three lap populations independently settable: the VALID
    rows the grid draws, the EXCLUDED rows the strip lists, and `lap_count` — which on a real
    recording is larger than both put together (the coarse gate drops brief slivers)."""

    timing_verified = True
    timing_quality = data_quality.TimingQuality()

    def __init__(self, valid=3, excluded=0, detected=None):
        self.valid, self.excluded = valid, excluded
        self.detected = valid + excluded if detected is None else detected

    def lap_rows(self):
        # Kept laps: ~200 m, the F.D shape (the excluded ones below are 2.6x longer).
        return [{"idx": i, "time": 13.0 + i * 0.1, "dist": 203.0, "entry": 50.0}
                for i in range(self.valid)]

    def excluded_lap_rows(self):
        return [{"idx": 100 + i, "time": 34.0 + i * 0.1, "dist": 536.0, "entry": 50.0}
                for i in range(self.excluded)]

    def lap_count(self):
        return self.detected

    def sector_count(self):
        return 0

    def lap_sector_splits(self, lap_id):
        return []

    def session_best_splits(self):
        return []

    def best_lap_id(self):
        return 0 if self.valid else None

    def dropout_lap_ids(self):
        return set()


def _stat(delta=0.0, apex_delta=0.0, time=2.5):
    return SimpleNamespace(time=time, delta=delta, apex_speed=44.9, apex_speed_delta=apex_delta,
                           entry_speed=45.7, exit_speed=47.9)


class _FakeCornerSession:
    """The CornerTable read surface. `best` is the session-best lap id (the Δ baseline), and the
    stats it hands back for that lap carry the model's ref=None self-zeros — the exact pairing the
    real corner model produces."""

    def __init__(self, n=4, best=0, valid=(0, 1), reference=False, reference_of_self=False):
        # reference_of_self: the reference was loaded from THIS recording, so its lap IS the local
        # best and every Δ against it is a self-zero again (QA-W2R-04). set_reference_session
        # refuses that state; the table must not depend on the refusal existing.
        self.best, self._valid = best, list(valid)
        self._reference = reference or reference_of_self
        self._reference_of_self = reference_of_self
        cl = [SimpleNamespace(label=f"C{i + 1}", direction=1 if i % 2 else -1, cid=i)
              for i in range(n)]
        self.corners = SimpleNamespace(
            corner_list=lambda: cl,
            lap_corner_stats=self._stats,
            corner_session_bests=lambda: [2.5] * n)
        self.driving = SimpleNamespace(lap_corner_grip=lambda lap: [0.77] * n)
        self._n = n

    def _stats(self, lap_id):
        if lap_id is None or lap_id not in self._valid:
            return []
        # The baseline lap's own stats are all-zero deltas unless a cross-recording reference is
        # loaded, in which case even it is measured against that (corner_model.lap_corner_stats)
        # — UNLESS that reference is this same recording's own lap, when the zeros are back.
        if lap_id == self.best and (self._reference_of_self or not self._reference):
            return [_stat() for _ in range(self._n)]
        return [_stat(delta=0.11 + i * 0.01, apex_delta=-0.9) for i in range(self._n)]

    def lap_count(self):
        return max(self._valid, default=-1) + 1

    def valid_lap_ids(self):
        return list(self._valid)

    def best_lap_id(self):
        return self.best

    def has_reference(self):
        return self._reference

    def reference_label(self):
        return "recording 0059 · 3 chapters" if self._reference else None

    def reference_is_own_recording(self):
        return self._reference_of_self

    def reference_lap_id(self):
        return self.best if self._reference_of_self else 99


def test_corner_deltas_dash_on_the_lap_they_would_compare_with_itself():
    """L3-05: the Δ columns on the BASELINE lap are the model's documented self-zeros, not
    measurements — so they must read as "no value" and the page must name the lap. Selecting any
    other lap brings the numbers straight back."""
    sess = _FakeCornerSession()
    ct = _shown(LT.CornerTable(sess))
    ct.set_lap(0)                                     # == best_lap_id: the Δ baseline
    tb = ct.table
    for c in (_DELTA_BEST_COL, _DELTA_APEX_COL):
        got = [tb.item(r, c).text() for r in range(tb.rowCount())]
        assert got == [LT.SELF_DELTA] * tb.rowCount(), (c, got)
    # ...and the page SAYS so, naming the lap in the table's own 1-based numbering.
    assert ct.baseline_note.isVisible()
    assert "Lap 1" in ct.baseline_note.text() and ct.baseline_note.toolTip()
    # Everything that IS a measurement on this lap still renders.
    assert tb.item(0, 1).text().startswith("2.50"), tb.item(0, 1).text()

    ct.set_lap(1)
    assert not ct.baseline_note.isVisible()
    assert tb.item(0, _DELTA_BEST_COL).text() == "+0.11", tb.item(0, _DELTA_BEST_COL).text()
    assert tb.item(0, _DELTA_APEX_COL).text() == "-0.9", tb.item(0, _DELTA_APEX_COL).text()
    print("test_corner_deltas_dash_on_the_lap_they_would_compare_with_itself OK")


def test_a_cross_recording_reference_keeps_every_local_delta():
    """The baseline is the model's, not "the best lap": with a cross-recording reference loaded
    (F7) even the local best is measured against IT, so nothing is compared with itself and no
    cell may be dashed."""
    ct = _shown(LT.CornerTable(_FakeCornerSession(reference=True)))
    ct.set_lap(0)                                     # still the local best
    assert not ct.baseline_note.isVisible()
    assert ct.table.item(0, _DELTA_BEST_COL).text() == "+0.11"
    print("test_a_cross_recording_reference_keeps_every_local_delta OK")


def test_a_reference_of_this_same_recording_still_dashes_the_self_deltas():
    """QA-W2R-04. `_shows_the_baseline` returned False the moment `has_reference()` was true, on
    the stated assumption that a reference is always a DIFFERENT recording — "then no local lap is
    ever compared with itself". A recording loaded as its OWN reference falsified that: the
    dashes and the caption switched off and twelve rows of exact zeros rendered as "+0.00"/"+0.0",
    which is the very defect the dashes exist to prevent, reached through another door.

    The state is refused at load now, but the table must not depend on that: it asks the session
    whether the reference is its own recording rather than assuming it is not."""
    sess = _FakeCornerSession(reference_of_self=True)
    ct = _shown(LT.CornerTable(sess))
    ct.set_lap(0)                                     # == best == the reference lap
    tb = ct.table
    for c in (_DELTA_BEST_COL, _DELTA_APEX_COL):
        got = [tb.item(r, c).text() for r in range(tb.rowCount())]
        assert got == [LT.SELF_DELTA] * tb.rowCount(), (c, got)
    # ...and the caption names the REFERENCE as the baseline, not "the session best" — the reason
    # the Δ is against itself is different, so the sentence is.
    assert ct.baseline_note.isVisible()
    assert "reference lap" in ct.baseline_note.text(), ct.baseline_note.text()
    assert "Lap 1" in ct.baseline_note.text(), ct.baseline_note.text()
    assert ct.baseline_note.toolTip() == LT.SELF_REFERENCE_TOOLTIP
    print("test_a_reference_of_this_same_recording_still_dashes_the_self_deltas OK")


def test_zero_valid_laps_never_asks_for_a_lap():
    """L3-08: three states, three messages. "nothing selected yet" and "there is nothing
    selectable" are different recordings, and only one of them can act on "Select a lap"."""
    ct = _shown(LT.CornerTable(_FakeCornerSession(valid=())))  # no valid lap at all
    ct.refresh()
    assert not ct.table.isVisible() and ct.empty.isVisible()
    assert "Select a lap" not in ct.empty.text(), ct.empty.text()
    assert ct.empty.text() == LT.NO_LAPS_PLACEHOLDER

    ct = _keep(LT.CornerTable(_FakeCornerSession()))            # laps exist, none selected
    ct.refresh()
    assert "Select a lap" in ct.empty.text(), ct.empty.text()

    ct = _keep(LT.CornerTable(_FakeCornerSession(n=0)))         # a lap, but no corners
    ct.set_lap(0)
    assert "No corners detected" in ct.empty.text(), ct.empty.text()
    print("test_zero_valid_laps_never_asks_for_a_lap OK")


def test_both_zero_lap_placeholders_end_on_a_next_action():
    """Both pages of the panel share one wording AND one thing to do about it — the recording is
    the problem, so the action is to open another (the map already owns "drag the start/finish
    line", and repeating it here would be its third appearance in the same window)."""
    corners = _keep(LT.CornerTable(_FakeCornerSession(valid=())))
    corners.refresh()
    laps = _keep(LT.LapTable(_FakeLapSession(valid=0)))
    for text in (corners.empty.text(), laps._empty.text()):
        assert LT.NO_LAPS_TEXT in text, text
        assert text.endswith(LT.NO_LAPS_ACTION), text
        assert "⌘O" in text, text
    print("test_both_zero_lap_placeholders_end_on_a_next_action OK")


def test_excluded_strip_escalates_when_the_ratio_crosses_the_threshold():
    """L3-06: one stray lap and half the session must not read alike. Below the threshold the strip
    keeps its muted one-liner; above it, it states the share in WORDS (so the escalation survives
    greyscale), takes the attention amber, and puts the kept-vs-excluded distance comparison on
    screen instead of only in a tooltip."""
    calm = _shown(LT.LapTable(_FakeLapSession(valid=21, excluded=1)))
    assert calm._excluded_strip.isVisible()
    assert calm._excluded_header.text() == f"{LT.EXCLUDED_MARK} 1 excluded of 22 laps ▸"
    assert "%" not in calm._excluded_header.text()
    assert not calm._excluded_note.isVisible()
    assert theme.C.accent not in calm._excluded_header.styleSheet()

    loud = _shown(LT.LapTable(_FakeLapSession(valid=25, excluded=24, detected=50)))
    assert loud._excluded_header.text() == f"{LT.EXCLUDED_MARK} 24 excluded of 49 laps (49%) ▸"
    assert theme.C.accent in loud._excluded_header.styleSheet()
    assert loud._excluded_note.isVisible()
    note = loud._excluded_note.text()
    assert "203 m" in note and "536 m" in note, note          # BOTH medians, on screen
    assert "start/finish line" in note, note                  # ...and the way out
    # BOTH conditions are real boundaries, not "any exclusion at all": exactly the ratio is still
    # calm, and so is a short session where a big share is only one or two stray laps.
    edge = _shown(LT.LapTable(_FakeLapSession(valid=80, excluded=20)))  # exactly 20%
    assert not edge._excluded_note.isVisible(), edge._excluded_header.text()
    short = _shown(LT.LapTable(_FakeLapSession(valid=2, excluded=2)))   # 50%, but 2 laps
    assert not short._excluded_note.isVisible(), short._excluded_header.text()
    print("test_excluded_strip_escalates_when_the_ratio_crosses_the_threshold OK")


def test_excluded_counts_reconcile_with_the_detected_lap_count():
    """L3-06's cleanest item: the panel showed 25 rows and "24 excluded" on a recording whose
    lap_count() is 50, so its two visible numbers did not add up to the third and the 50th lap was
    unexplained. Every detected lap is now accounted for."""
    lt = LT.LapTable(_FakeLapSession(valid=25, excluded=24, detected=50))
    assert "24 excluded of 49 laps" in lt._excluded_header.text()
    assert "1 other start/finish crossing was too brief" in lt._excluded_note.text(), \
        lt._excluded_note.text()
    # Nothing left over -> no phantom sentence.
    exact = LT.LapTable(_FakeLapSession(valid=25, excluded=24, detected=49))
    assert "too brief" not in exact._excluded_note.text(), exact._excluded_note.text()
    print("test_excluded_counts_reconcile_with_the_detected_lap_count OK")


def test_expanded_excluded_list_is_complete_and_height_bounded():
    """L3-09: the expansion listed 6 of 24 and a dead "+18 more" — 18 laps named by a line no
    surface in the app would ever show — and still cost the lap grid four rows. Now the LIST is
    complete and the VIEWPORT is what is capped."""
    lt = _shown(LT.LapTable(_FakeLapSession(valid=25, excluded=24, detected=50)))
    collapsed_h = lt._excluded_strip.height()
    assert not lt._excluded_scroll.isVisible()

    lt._toggle_excluded_collapsed()
    _settle()
    lines = lt._excluded_body.text().split("\n")
    assert len(lines) == 24, len(lines)                       # every excluded lap, not 6 + a stub
    assert not any("more" in ln for ln in lines), lines
    assert lines[0].startswith("Lap 101 —") and lines[-1].startswith("Lap 124 —"), lines[:1]
    # The strip grows by at most the capped viewport, whatever the list length...
    line_px = QFontMetrics(lt._excluded_body.font()).lineSpacing()
    cap = line_px * LT.EXCLUDED_MAX_SHOWN + 4
    assert lt._excluded_scroll.maximumHeight() == cap
    assert lt._excluded_strip.height() <= collapsed_h + cap, lt._excluded_strip.height()
    # ...and the overflow is REACHABLE rather than dropped.
    assert lt._excluded_scroll.verticalScrollBar().maximum() > 0

    lt._toggle_excluded_collapsed()
    _settle()
    assert lt._excluded_strip.height() == collapsed_h and not lt._excluded_scroll.isVisible()
    print("test_expanded_excluded_list_is_complete_and_height_bounded OK")


def test_a_clean_session_shows_no_strip_at_all():
    """The common case is untouched: no excluded laps, no strip, no chrome — and the View-menu
    toggle still hides it independently of the collapse state. The ONE test here that is green on
    `main` too, deliberately: it is the no-regression guard for the seven that are not."""
    lt = _shown(LT.LapTable(_FakeLapSession(valid=21, excluded=0)))
    assert not lt._excluded_strip.isVisible() and lt._excluded_header.text() == ""
    lt = _shown(LT.LapTable(_FakeLapSession(valid=21, excluded=6)))
    assert lt._excluded_strip.isVisible()
    lt.set_excluded_visible(False)
    assert not lt._excluded_strip.isVisible()
    lt.set_excluded_visible(True)
    assert lt._excluded_strip.isVisible()
    print("test_a_clean_session_shows_no_strip_at_all OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} LAP-TABLE EMPTY-STATE TESTS PASSED", flush=True)
