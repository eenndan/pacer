"""The first-open debrief (board review R10 / PS-B1): a recording opened for the first time lands on
the Coaching page full-window — its personal-best standing, one ranked total with its top corners
and their Jumps, and those corners already on the focus list: an explicit default, each one click
from gone. Leaving it (Esc) is the grid the driver left, on the tab he left it on.

WHY. The loop the app is built around — load, see where the time went, pick one to three corners,
next session, did they move? — had never run for its one real user: no focus.json after two race
sessions (UX-7), because the panel reopened on his remembered tab (Laps), the verdict lived on tab 4
and promoting a corner was a click nobody made. Measured on the real window before this change,
SD_30_08 (0065) then SD_19_09 (0068) into a fresh library: both opens landed on Laps, and the focus
store was still empty after both.

Pinned here:
  1. the PB line (`library.pb_standing_for` / `pb_standing_text`): beat and first are the PB
     moment's; a slower first open is "behind" the OTHER recordings' best; no line at all on
     unverified or ESTIMATED timing, and no "behind" for a recording the index already holds;
  2. the lead says the promotion is a default and how to undo it, and follows the list;
  3. while the page is the debrief its table is the headline's shortlist and the ESTIMATED
     brake-point line stays off it (UX-2, "brake later" on 7 of 7 corners, is not settled);
  4. THE JOURNEY on the real StudioWindow over two synthetic recordings of the built-in demo circuit
     (so the timing is verified), jailed, with the driver's remembered tab Corners and a persisted
     grid. It includes the defect this package found in its own first cut: the view's deferred
     first-show grid restores put the grid back on screen under a lap panel still marked maximized.

Run: python tests/test_debrief_landing.py   (~10 s; the pixi env's ffmpeg writes the video trak)
"""
import contextlib
import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _qtapp import themed_app  # noqa: E402

from studio import coaching, library  # noqa: E402
from studio._signal import fmt_time  # noqa: E402
from studio.coaching_panel import _PANEL_COL_REASON, PANEL_TOP_N, debrief_note  # noqa: E402

_APP = themed_app()


def _entry(fp, best, *, verified=True, degraded=False, track="Stadium"):
    return {"fingerprint": fp, "stem": fp, "track": track, "date": "2026-09-01", "lap_count": 20,
            "best": best, "theoretical": best - 0.3, "verified": verified, "degraded": degraded,
            "dropout": False, "paths": [f"/nowhere/{fp}.MP4"]}


# ------------------------------------------------------------------------ 1. the PB line
def test_the_pb_line_is_the_moment_or_where_the_best_lap_stands():
    idx = {"entries": [_entry("GX0001", 47.0)]}
    first = library.pb_standing_for(True, {"entries": []}, "Stadium", 47.5, fingerprint_key="GX0002")
    beat = library.pb_standing_for(True, idx, "Stadium", 46.9, fingerprint_key="GX0002")
    behind = library.pb_standing_for(True, idx, "Stadium", 47.25, fingerprint_key="GX0002")
    assert first["kind"] == "first" and beat["kind"] == "beat", (first, beat)
    assert behind == {"kind": "behind", "track": "Stadium", "best": 47.25, "prior": 47.0,
                      "gap": 0.25}, behind
    # the PB moment's own gates: no line at all on timing the app will not stand behind
    for kw in ({"verified": False}, {"degraded": True}):
        v = kw.get("verified", True)
        got = library.pb_standing_for(v, idx, "Stadium", 47.25, degraded=kw.get("degraded", False),
                                      fingerprint_key="GX0002")
        assert got is None, (kw, got)
    # a recording the index already holds may itself be the PB: never "behind" on a re-open
    mine = {"entries": [_entry("GX0001", 47.0), _entry("GX0002", 46.5)]}
    assert library.pb_standing_for(True, mine, "Stadium", 47.25, fingerprint_key="GX0002") is None
    texts = [library.pb_standing_text(s, fmt_time) for s in (first, beat, behind)]
    assert texts[0] == "First session logged at Stadium: best lap 0:47.500, the time to beat " \
                       "next time.", texts[0]
    assert texts[1] == "New personal best at Stadium: 0:46.900, 0.10 s faster than your " \
                       "previous best (0:47.000).", texts[1]
    assert texts[2] == "Best lap 0:47.250 at Stadium, 0.25 s off your personal best there " \
                       "(0:47.000).", texts[2]
    tie = library.pb_standing_for(True, idx, "Stadium", 47.0, fingerprint_key="GX0002")
    assert library.pb_standing_text(tie, fmt_time).endswith("level with your personal best there.")
    print("ok pb line:", *texts, sep="\n  ")


def test_the_lead_says_it_is_a_default_and_how_to_undo_it():
    three, one, none = debrief_note([7, 5, 3]), debrief_note([4]), debrief_note([])
    assert "Pacer put C7, C5 and C3" in three and "a default, not a decision" in three, three
    assert "remove any you won't work on" in three, three
    assert "Pacer put C4," in one and "remove it if you won't work on it" in one, one
    for note in (three, one, none):
        assert note.endswith("Esc returns to your usual layout."), note
    print(f"ok lead: {three!r}")


# ---------------------------------------------------------------- 2-3. the page's debrief mode
def _layout_fixtures():
    import test_coaching_panel_layout as layout
    return layout


def test_the_debrief_is_the_shortlist_and_keeps_the_estimate_off_its_rows():
    layout = _layout_fixtures()
    rows = layout._rows(8)
    # C1 on the approach, so its ESTIMATED "Brake ~7 m later" line is shown on the ordinary page
    rows[0] = coaching.Opportunity(cid=1, direction=1, time_lost=0.30, entry_dist=972.4,
                                   reason=layout._reason(coaching.REASON_BRAKING),
                                   phases=rows[0].phases, evidence=rows[0].evidence)
    p = layout._panel(rows, (1400, 800),
                      brake_points={1: layout._bp(cid=1, actual=973.7, optimal=980.7)})
    cell = lambda: p.table.item(0, _PANEL_COL_REASON).text()  # noqa: E731
    assert "Brake ~7 m later into C1" in cell(), cell()
    assert p.table.rowCount() > PANEL_TOP_N, "a tall page shows more than the shortlist"
    assert p.shortlist_cids() == [1, 2, 3], p.shortlist_cids()

    p.set_debrief(True, "First session logged at Stadium: best lap 0:47.500.", [1, 2, 3])
    for _ in range(4):
        _APP.processEvents()
    assert p.table.rowCount() == PANEL_TOP_N, p.table.rowCount()
    assert "Brake ~" not in cell() and "longer on the brakes" in cell(), cell()
    assert not p.debrief_block.isHidden(), "the lead is the first thing on the debrief"
    assert "First session logged" in p.debrief_block.headline.text()
    assert p.debrief_block.full_text() in p.summary_label.toolTip(), "demoted, never deleted"

    p.set_debrief(False)
    for _ in range(4):
        _APP.processEvents()
    assert "Brake ~7 m later into C1" in cell(), "the estimate is back once the debrief ends"
    assert p.table.rowCount() > PANEL_TOP_N and p.debrief_block.isHidden()
    print(f"ok page: debrief = {PANEL_TOP_N} rows, no estimate; after it the ranking and the "
          "estimate are back")


# ------------------------------------------------------------------------ 4. the journey
_SEAM_MODULES = ("demo", "focus", "library", "logsetup", "marks", "prefs", "session_record",
                 "track_db")


@contextlib.contextmanager
def _fresh_app_support():
    """Every app-support seam in a directory of this test's own — a FRESH library, whatever jail
    the run is in — put back afterwards."""
    import importlib
    mods = [importlib.import_module(f"studio.{m}") for m in _SEAM_MODULES]
    saved = [m._app_support_dir for m in mods]
    with tempfile.TemporaryDirectory(prefix="debrief_") as d:
        for m in mods:
            m._app_support_dir = lambda d=d: d
        try:
            yield d
        finally:
            for m, fn in zip(mods, saved, strict=True):
                m._app_support_dir = fn


def _two_recordings(folder: str) -> tuple[str, str]:
    """Two recordings of the built-in demo circuit, byte-identical telemetry under two recording
    numbers — two library identities (GX9001, GX9002), so the second open is a first open too."""
    from studio.dev import make_demo as md
    from studio.dev import synth_gopro as sg
    rec = sg.generate(os.path.join(folder, "gen"), md.DEMO_SEED, md.DEMO_LAPS, chapters=1,
                      origin=md.DEMO_ORIGIN)
    out = []
    for n in ("9001", "9002"):
        os.makedirs(os.path.join(folder, n))
        out.append(os.path.join(folder, n, f"GX01{n}.MP4"))
        shutil.copyfile(rec.paths[0], out[-1])
    return out[0], out[1]


def _open(win, path: str) -> None:
    done = {"v": False}
    conn = win.loadFinished.connect(lambda: done.__setitem__("v", True))
    win._open_recordings([path])
    deadline = time.time() + 120
    while not done["v"] and time.time() < deadline:
        _APP.processEvents()
        time.sleep(0.01)
    win.loadFinished.disconnect(conn)
    assert done["v"], f"{path} did not load"
    _settle(0.4)     # past the view's first show and its deferred grid restores (0 and 120 ms)


def _settle(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        _APP.processEvents()
        time.sleep(0.01)


def _visible(buttons, prefix):
    return [b for b in buttons if not b.isHidden() and b.text().startswith(prefix)]


def test_the_journey_on_the_real_window():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMessageBox

    from studio import focus, prefs
    from studio.app import StudioWindow

    def _no_modal(*args, **_kwargs):
        raise AssertionError(f"a modal opened on the debrief journey: {args[1:3]}")

    boxes = {k: getattr(QMessageBox, k) for k in ("critical", "warning", "information", "question")}
    for k in boxes:
        setattr(QMessageBox, k, staticmethod(_no_modal))
    grid = [[700, 732], [400, 446], [320, 526]]
    with tempfile.TemporaryDirectory(prefix="debrief_rec_") as folder, _fresh_app_support():
        a, b = _two_recordings(folder)
        prefs.set_lap_panel_tab(1)          # the driver's remembered tab: Corners
        prefs.set_grid_sizes(grid)          # ...and a grid he has dragged
        win = StudioWindow([])
        win.resize(1440, 900)
        win.show()
        try:
            _open(win, a)
            view, panel = win.view, win.view.opportunities
            track = win.session.track_name
            short = panel.shortlist_cids()
            listed = lambda: focus.for_track(focus.load(), track)  # noqa: E731
            assert len(short) == PANEL_TOP_N, short
            assert view.is_debrief() and view.tab_bar.currentIndex() == 3, "no debrief landing"
            # Still maximized AFTER the deferred first-show restores: the defect of the first cut.
            assert view._maximized_panel is view._table_panel, "not maximized"
            assert view._main_splitter.sizes()[1] == 0 and view._left_splitter.sizes()[0] == 0, (
                "the grid is back on screen under a maximized lap panel",
                view._main_splitter.sizes(), view._left_splitter.sizes())
            assert prefs.lap_panel_tab() == 1, "the landing overwrote the remembered tab"
            assert [i.cid for i in listed()] == short, (listed(), short)
            assert win._pb_toast is None, "the debrief carries the PB; no card on top of it"
            assert panel.table.rowCount() == len(short)
            lead = panel.debrief_block.full_text()
            assert f"First session logged at {track}" in lead, lead
            assert debrief_note(short) in lead, lead

            # One click drops a pre-promoted corner, and the lead stops naming it.
            gone = short[-1]
            (drop,) = _visible(panel.focus_block.drop_buttons, f"Remove C{gone}")
            drop.click()
            _settle(0.1)
            assert [i.cid for i in listed()] == short[:-1], listed()
            assert f"C{gone}" not in panel.debrief_block.note.text(), panel.debrief_block.note.text()

            # Esc: the grid he left, on the tab he left it on; the estimate is the page's again.
            saved = view._saved_splitter_sizes
            assert saved[0] == grid[0], ("Esc would not return to his grid", saved)
            QTest.keyClick(win, Qt.Key_Escape)
            _settle(0.2)
            assert not view.is_debrief() and view._maximized_panel is None
            assert view.tab_bar.currentIndex() == 1 and prefs.lap_panel_tab() == 1
            assert [view._main_splitter.sizes(), view._left_splitter.sizes(),
                    view._right_splitter.sizes()] == list(saved)

            # The second recording is a first open too: the free slot is filled, the kept
            # corners keep the FIRST session's baselines, and the verdict is one click away.
            kept = {i.cid: focus.item_to_dict(i) for i in listed()}
            _open(win, b)
            view, panel = win.view, win.view.opportunities
            assert view.is_debrief() and view._maximized_panel is view._table_panel
            now = {i.cid: focus.item_to_dict(i) for i in listed()}
            assert sorted(now) == sorted(short), now.keys()
            assert all(now[c] == kept[c] for c in kept), "a kept corner was re-based"
            assert now[gone]["fingerprint"] == "GX9002", now[gone]
            assert f"Pacer put C{gone}," in panel.debrief_block.note.text()
            assert "level with your personal best" in panel.debrief_block.headline.text()
            refusal = panel.focus_block.full_text()
            assert "no session record for" in refusal, refusal
            (mark,) = _visible([panel.focus_block.mark_button], "Mark ")
            mark.click()
            _settle(0.1)
            verdict = panel.focus_block.full_text()
            assert view.is_debrief(), "the verdict must arrive without leaving the debrief"
            for cid in kept:
                line = next(ln for ln in verdict.splitlines() if ln.startswith(f"C{cid} — "))
                assert "no change you can act on" in line, verdict

            # A re-open is not a first open: the remembered tab, the grid, nothing promoted.
            before = {i.cid: focus.item_to_dict(i) for i in listed()}
            _open(win, b)
            view = win.view
            assert not view.is_debrief() and view._maximized_panel is None
            assert view.tab_bar.currentIndex() == 1 and prefs.lap_panel_tab() == 1
            assert {i.cid: focus.item_to_dict(i) for i in listed()} == before
        finally:
            win.close()
            win.deleteLater()
            _APP.processEvents()
            for k, fn in boxes.items():
                setattr(QMessageBox, k, fn)
    print(f"ok journey: {short} promoted, C{gone} dropped in one click, Esc to Corners, the "
          f"second open filled the slot with C{gone} and reached a verdict in one click")


def test_esc_returns_a_driver_who_never_dragged_a_splitter_to_the_default_grid():
    """No persisted grid, so no first-show restore writes the grid Esc returns to: the snapshot the
    maximize takes IS that grid. Taken at the load's tail it would be the view's 100x30 placeholder
    split, and Esc would restore that ratio. Compared with the same recording re-opened, which is
    not a first open and lands on the ordinary grid."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from studio.app import StudioWindow
    with tempfile.TemporaryDirectory(prefix="debrief_rec_") as folder, _fresh_app_support():
        a, _b = _two_recordings(folder)
        win = StudioWindow([])
        win.resize(1440, 900)
        win.show()
        try:
            _open(win, a)
            assert win.view.is_debrief() and win.view._maximized_panel is not None
            QTest.keyClick(win, Qt.Key_Escape)
            _settle(0.2)
            view = win.view
            after_esc = [s.sizes() for s in (view._main_splitter, view._left_splitter,
                                             view._right_splitter)]
            assert view.tab_bar.currentIndex() == 0, "Laps is the default remembered tab"
            _open(win, a)
            view = win.view
            assert not view.is_debrief()
            plain = [s.sizes() for s in (view._main_splitter, view._left_splitter,
                                         view._right_splitter)]
        finally:
            win.close()
            win.deleteLater()
            _APP.processEvents()
    assert after_esc == plain, ("Esc did not return to the grid a plain open lands on",
                                after_esc, plain)
    print(f"ok default grid: Esc → {after_esc}, a plain open → {plain}")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} DEBRIEF TESTS PASSED")
