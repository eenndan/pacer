"""Where the first-open debrief lands, and when (QA round 2, 2026-09-26: LOOK-1, NEW-3b, NEW-4, NEW-6;
round 3: EVAL-2).

WHY, finding by finding, each measured on the owner's own recordings:
  * LOOK-1: leaving the debrief by a TAB click ended the debrief but kept the lap panel maximized,
    so "Laps" gave a full-window table with no video, map or charts, and the "Esc returns…" line
    had gone with the lead. A tab click now leaves it the way Esc does.
  * NEW-3b: at a circuit Pacer does not know, the loader fits the start line itself. On his MK
    layout that fit sat where the track crosses itself (laps off by up to ±2.6 s, a 1:49.5 "lap"
    valid), and the first open still landed maximized over the one map that could fix it. The
    verdict now waits for trusted timing, as it waits for the whole recording (#420): the map and
    its cue stay on screen, and the status bar says what waits.
  * NEW-4: once he placed the line and saved the track, nothing was decided again: no PB line, no
    "first session", no focus list, so the between-sessions loop never started at a new circuit.
    The drag is now the verdict's moment (the debrief lands, once), and the name completes it (the
    PB standing against the OTHER recordings at that track, and the focus promotion) without a
    second landing. A re-open lands nowhere.
  * NEW-6: Open demo wrote "First session logged at Synthetic demo circuit", a Library row, a focus
    list and an Open Recent entry. The demo is not his driving.
  * EVAL-2: that fix (#424) returned before the first-open flag was set, so the demo never showed the
    debrief, the loop an evaluator opens it to see (measured on v0.5.0: tab "Laps", both doors). It
    lands there on every open now and still keeps nothing: its lead says so, its focus list lives
    in memory, and his own list at the same circuit is neither read nor written.

The journeys drive the real StudioWindow over synthetic recordings of the built-in demo circuit,
jailed in a fresh app-support directory; "a circuit Pacer does not know" is that circuit taken out
of `track_db.SEED` in this process only, which is how the QA lane simulated a new track.

Run: python tests/test_debrief_lands_right.py   (~25 s)
"""
import contextlib
import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_debrief_landing import (  # noqa: E402
    _APP,
    _fresh_app_support,
    _open,
    _settle,
    _two_recordings,
)

_DEMO_CIRCUIT = "Synthetic demo circuit"


@contextlib.contextmanager
def _window():
    from PySide6.QtWidgets import QMessageBox

    from studio.app import StudioWindow

    def _no_modal(*args, **_kwargs):
        raise AssertionError(f"a modal opened: {args[1:3]}")

    boxes = {k: getattr(QMessageBox, k) for k in ("critical", "warning", "information", "question")}
    for k in boxes:
        setattr(QMessageBox, k, staticmethod(_no_modal))
    win = StudioWindow([])
    win.resize(1440, 900)
    win.show()
    try:
        yield win
    finally:
        win.close()
        win.deleteLater()
        _APP.processEvents()
        for k, fn in boxes.items():
            setattr(QMessageBox, k, fn)


@contextlib.contextmanager
def _unknown_circuit():
    """The demo circuit taken out of the built-in seed: its recordings open on the loader's own
    auto-fitted start line, as a circuit Pacer has never seen does."""
    from studio import track_db
    seed = list(track_db.SEED)
    line = next(e["start"] for e in seed if e["name"] == _DEMO_CIRCUIT)
    track_db.SEED[:] = [e for e in seed if e["name"] != _DEMO_CIRCUIT]
    try:
        yield line
    finally:
        track_db.SEED[:] = seed


def _grid_visible(view) -> bool:
    """Video, map and charts all have pixels: nothing is maximized and no splitter is collapsed."""
    sizes = [view._main_splitter.sizes(), view._left_splitter.sizes(), view._right_splitter.sizes()]
    return view._maximized_panel is None and all(s > 0 for row in sizes for s in row)


def _rows():
    from studio import library
    return {e["fingerprint"]: e for e in library.load().get("entries", [])}


def _drag_start_line(win, line) -> None:
    """Place the start line the way a drag's release commits it: the map's own handles moved onto
    `line` (lat/lon), then the map emits its current lines (CentralView._on_lines)."""
    import pacer
    from studio.session import Seg
    pts = []
    for lat, lon in line:
        v = win.session.cs.local(pacer.GPSSample(lat=lat, lon=lon, altitude=0))
        pts.append((float(v[0]), float(v[1])))
    (x1, y1), (x2, y2) = pts
    win.view.map._rebuild(Seg(x1, y1, x2, y2), [])
    win.view.map._emit()
    _settle(0.3)


def _load(win, path: str) -> None:
    """Load one file the way Open demo does (`StudioWindow._load`, not the GoPro-recording door)."""
    done = {"v": False}
    conn = win.loadFinished.connect(lambda: done.__setitem__("v", True))
    win._load([path])
    for _ in range(12000):
        if done["v"]:
            break
        _settle(0.01)
    win.loadFinished.disconnect(conn)
    assert done["v"], f"{path} did not load"
    _settle(0.4)


def _count_landings(win) -> list:
    """Wrap the view's show_debrief so every landing is counted (the view is rebuilt per load)."""
    calls = []
    real = win.view.show_debrief

    def counted(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)
    win.view.show_debrief = counted
    return calls


# ------------------------------------------------------------------------------------ LOOK-1
def test_a_tab_click_leaves_the_debrief_for_the_grid():
    with tempfile.TemporaryDirectory(prefix="landsright_") as folder, _fresh_app_support(), \
            _window() as win:
        a, _b = _two_recordings(folder)
        _open(win, a)
        view = win.view
        assert view.is_debrief() and view._maximized_panel is view._table_panel, "no landing"
        view.tab_bar.setCurrentIndex(0)             # a click on "Laps"
        _settle(0.2)
        assert not view.is_debrief(), "the tab click did not end the debrief"
        assert _grid_visible(view), (
            "a tab click left the lap panel full-window: no video, map or charts",
            view._maximized_panel, view._main_splitter.sizes(), view._left_splitter.sizes())
        assert view.tab_bar.currentIndex() == 0, "the page the driver chose"
    print("ok LOOK-1: a tab click leaves the debrief for the grid, on the tab clicked")


# --------------------------------------------------------------------------- NEW-3b / NEW-4
def test_an_unknown_circuit_waits_for_the_line_then_lands_once():
    from PySide6.QtWidgets import QInputDialog

    from studio import focus
    from studio.library_controller import NAME_WAIT_LINE
    name = "Test circuit"
    ask = QInputDialog.getText
    QInputDialog.getText = staticmethod(lambda *a, **k: (name, True))
    try:
        with tempfile.TemporaryDirectory(prefix="landsright_") as folder, _fresh_app_support(), \
                _unknown_circuit() as line, _window() as win:
            a, _b = _two_recordings(folder)
            _open(win, a)
            view = win.view
            landings = _count_landings(win)
            assert not win.session.timing_verified and win.session.track_name is None
            # 1. The first open decides nothing on the loader's own line: the grid, the map and its
            #    cue, and a notice that says what waits.
            assert not view.is_debrief() and _grid_visible(view), (
                "the first open on an auto-fitted line landed over the map", view._maximized_panel)
            assert "your PB and debrief wait for it" in win.statusBar().currentMessage(), \
                win.statusBar().currentMessage()
            assert win.library_ctl.waiting_for_line and not _rows(), _rows()
            assert win._pb_toast is None
            # 2. The drag that places the line is the verdict's moment: it lands, once.
            _drag_start_line(win, line)
            assert win.session.timing_verified and win.session.track_name is None
            assert len(landings) == 1 and view.is_debrief(), (landings, view.is_debrief())
            assert view._maximized_panel is view._table_panel
            lead = view.opportunities.debrief_block.full_text()
            assert NAME_WAIT_LINE in lead, lead
            (row,) = _rows().values()
            assert row["verified"] and row["track"] is None, row
            assert row["best"] == win.session.lap_time(win.session.best_lap_id()), row
            assert "unnamed circuit" in win.statusBar().currentMessage()
            assert focus.load()["lists"] == [], "a focus list with no track to keep it under"
            short = view.opportunities.shortlist_cids()
            # A second edit is not a second verdict.
            _drag_start_line(win, line)
            assert len(landings) == 1, landings
            # 3. Save as track names it: the PB standing and the focus list, no second landing.
            win._save_as_track()
            _settle(0.2)
            assert len(landings) == 1, ("Save as track landed a second time", landings)
            lead = view.opportunities.debrief_block.full_text()
            assert f"First session logged at {name}" in lead, lead
            assert [i.cid for i in focus.for_track(focus.load(), name)] == short, short
            assert _rows()[row["fingerprint"]]["track"] == name
            # 4. A re-open is not a first open.
            _open(win, a)
            assert not win.view.is_debrief() and win._pb_toast is None
    finally:
        QInputDialog.getText = ask
    print(f"ok NEW-3b/NEW-4: grid + cue on the fitted line, one landing on the drag, "
          f"'First session logged at {name}' and {short} on the name, none on a re-open")


def test_save_as_track_on_the_fitted_line_decides_it_all_at_once():
    """Naming the circuit without a drag trusts the line the loader fitted (the prompt says so), so
    it is the verdict's moment on its own: one landing, with the PB line and the focus list."""
    from PySide6.QtWidgets import QInputDialog

    from studio import focus
    name = "Named at once"
    ask = QInputDialog.getText
    QInputDialog.getText = staticmethod(lambda *a, **k: (name, True))
    try:
        with tempfile.TemporaryDirectory(prefix="landsright_") as folder, _fresh_app_support(), \
                _unknown_circuit(), _window() as win:
            a, _b = _two_recordings(folder)
            _open(win, a)
            landings = _count_landings(win)
            assert not win.view.is_debrief()
            win._save_as_track()
            _settle(0.2)
            assert len(landings) == 1 and win.view.is_debrief(), landings
            lead = win.view.opportunities.debrief_block.full_text()
            assert f"First session logged at {name}" in lead, lead
            assert focus.for_track(focus.load(), name), "no focus list at the named circuit"
            assert not win.library_ctl.waiting_for_name
    finally:
        QInputDialog.getText = ask
    print("ok NEW-4: Save as track on the fitted line lands once, PB line and focus list included")


# ------------------------------------------------------------------------------ NEW-6 / EVAL-2
def _files(root: str) -> list[str]:
    """Every file under `root`, relative: the jail's whole state, not just the stores we thought of."""
    return sorted(os.path.relpath(os.path.join(d, n), root)
                  for d, _dirs, names in os.walk(root) for n in names)


@contextlib.contextmanager
def _demo_env(path: str):
    saved = os.environ.get("PACER_DEMO_MP4")
    os.environ["PACER_DEMO_MP4"] = path
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("PACER_DEMO_MP4", None)
        else:
            os.environ["PACER_DEMO_MP4"] = saved


def test_the_demo_lands_on_its_debrief_and_keeps_nothing():
    """Both places the demo resolves from, the app-support cache and PACER_DEMO_MP4: every open
    lands on the debrief, and the jail holds nothing afterwards but the demo it was given."""
    from studio import demo, focus
    from studio import library_controller as lc
    with tempfile.TemporaryDirectory(prefix="landsright_") as folder, \
            _fresh_app_support() as support:
        a, b = _two_recordings(folder)
        cached = demo.demo_cache_path()
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        shutil.copyfile(a, cached)
        given = _files(support)
        with _demo_env(b):
            for path in (cached, b):
                with _window() as win:
                    _load(win, path)
                    view, opp = win.view, win.view.opportunities
                    short = opp.shortlist_cids()
                    assert short and view.is_debrief(), ("the demo did not land on its debrief",
                                                         path, view.tab_bar.currentIndex())
                    assert view._maximized_panel is view._table_panel
                    lead = opp.debrief_block.full_text()
                    assert lead.startswith(lc.DEMO_DEBRIEF_LINE), lead
                    assert "logged" not in lead and "for next time" not in lead, lead
                    assert opp.focus_block.cids() == short, (opp.focus_block.cids(), short)
                    assert win._pb_toast is None
                    # The list's gestures work on the demo, in memory, and the lead follows them.
                    win.library_ctl.focus_remove(short[0])
                    assert opp.focus_block.cids() == short[1:], opp.focus_block.cids()
                    assert f"C{short[0]}" not in opp.debrief_block.full_text()
                    win.library_ctl.sync_record_action()
                    assert not win._record_action.isEnabled()
                    assert win._record_action.toolTip() == lc.LibraryController._DEMO_RECORD_REASON
                    # A re-open is a first open again, from nothing: the Remove above is gone.
                    _load(win, path)
                    assert win.view.is_debrief(), "a re-open of the demo did not land"
                    assert win.view.opportunities.focus_block.cids() == short
                    assert _files(support) == given, ("the demo wrote into app-support",
                                                      _files(support))
                    assert not _rows() and win.library_ctl._recent_entries() == []
                    assert focus.load()["lists"] == [], focus.load()
    print(f"ok EVAL-2: the demo (cache and env) lands on its debrief, {short} in memory, again on a "
          "re-open; no row, PB, focus list, record or Open Recent entry")


def test_the_demo_never_reads_or_writes_his_focus_list():
    """His own recording of the same circuit first: its debrief writes his list and he keeps two of
    the three. The demo then promotes its own three, reading nothing of his, and a gesture on its
    list leaves his store byte for byte; back on his recording, the list is his two."""
    from studio import demo, focus
    with tempfile.TemporaryDirectory(prefix="landsright_") as folder, _fresh_app_support():
        mine, other = _two_recordings(folder)
        cached = demo.demo_cache_path()
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        shutil.copyfile(other, cached)
        with _window() as win:
            _load(win, mine)
            short = win.view.opportunities.shortlist_cids()
            assert win.view.is_debrief() and len(short) > 1, short
            win.library_ctl.focus_remove(short[0])
            his = [i.cid for i in focus.for_track(focus.load(), _DEMO_CIRCUIT)]
            assert his == short[1:], his
            with open(focus.focus_path(), "rb") as f:
                stored = f.read()
            _load(win, cached)
            assert win.view.is_debrief(), "the demo did not land on its debrief"
            assert win.view.opportunities.focus_block.cids() == short, (
                "the demo's list is not its own three: it read his",
                win.view.opportunities.focus_block.cids())
            win.library_ctl.focus_remove(short[1])
            with open(focus.focus_path(), "rb") as f:
                assert f.read() == stored, "a gesture on the demo's list wrote his focus.json"
            _load(win, mine)
            assert not win.view.is_debrief(), "his re-open landed"
            assert win.view.opportunities.focus_block.cids() == his, (
                "his list after the demo", win.view.opportunities.focus_block.cids(), his)
    print(f"ok EVAL-2: his list {his} at the demo's circuit, untouched by the demo's own {short}")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} LANDING TESTS PASSED")
