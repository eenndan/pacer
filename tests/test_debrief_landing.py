"""The first-open debrief (board review R10 / PS-B1): a recording opened for the first time lands on
the Coaching page full-window — its personal-best standing, one ranked total with its top corners
and their Jumps, and those corners already on the focus list: an explicit default, each one click
from gone. Leaving it (Esc) is the grid the driver left, on the Coaching page he was reading
(his remembered tab untouched).

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
     brake-point line stays off it (UX-2: "brake later" at every corner, and no relative figure
     survived measurement — refused-2026-09.md §16);
  3b. one writer of the debrief state (FIRST-OPEN-LOOP-9): only CentralView calls the page's
     `set_debrief`, and a lead decided later (`update_debrief_lead`) is a no-op outside it;
  4. THE JOURNEY on the real StudioWindow over two synthetic recordings of the built-in demo circuit
     (so the timing is verified), jailed, with the driver's remembered tab Corners and a persisted
     grid. It includes the defect this package found in its own first cut: the view's deferred
     first-show grid restores put the grid back on screen under a lap panel still marked maximized.
     Each gesture of the loop writes one INFO line to the session log (FIRST-OPEN-LOOP-3): a first
     open's debrief with its pre-fill marked "Pacer's default", a focus edit and a Mark-dry click
     marked "by hand", and none on a re-open. The pre-fill writes focus.json with no click, so
     without these lines a race-day read could not tell a list he touched from one he left.
  5. THE OTHER WAY ROUND on the real window (cool/pb-per-direction): the demo, then its circuit
     forward, in reverse and faster in reverse: the reverse is a first "this way round" (no PB line
     against the forward lap, no Compare, no card mark) and the faster reverse a PB over it.

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


def test_an_older_faster_first_open_is_the_fastest_not_a_new_pb():
    """REG2-GAP-1 (FIRST-OPEN-LOOP-13): the controller's verdict passes the session's own date, so a
    back-catalogue recording faster than a LATER row is the track's fastest — no "New personal
    best", no "First session", and no "previous PB" pointing forward in time to compare with."""
    from types import SimpleNamespace

    from studio.library_controller import LibraryController

    session = SimpleNamespace(timing_verified=True, timing_quality=SimpleNamespace(degraded=False))
    ctl = LibraryController(SimpleNamespace(session=session), 0)
    later = {**_entry("GX0068", 46.808), "date": "2026-09-19"}
    older = {**_entry("GX0050", 46.700), "date": "2026-06-01"}
    moment, standing = ctl._pb_decision(older, {"entries": [later]})
    assert moment["kind"] == standing["kind"] == "fastest", (moment, standing)
    assert ctl.previous_pb is None and not ctl.offers_pb_compare(standing), ctl.previous_pb
    ctl.pb_standing = standing
    line = ctl.debrief_pb_line()
    assert "New personal best" not in line and "First session" not in line, line
    assert line.startswith("Your fastest session logged at Stadium: best lap 0:46.700"), line
    # The forward order is untouched: the later recording opened after the older row beats it.
    moment, standing = ctl._pb_decision({**later, "best": 46.6}, {"entries": [older]})
    assert moment["kind"] == standing["kind"] == "beat", (moment, standing)
    assert ctl.previous_pb["fingerprint"] == "GX0050", ctl.previous_pb


def test_a_same_day_morning_opened_after_the_afternoon_is_not_a_new_pb():
    """QA W4 REG-1: the controller passes the entry's first-fix clock too, so a faster 09:00 session
    opened after the same day's 15:00 row is the track's fastest — the order the focus block on the
    same page uses — not a "New personal best" with the LATER afternoon offered as its previous."""
    from types import SimpleNamespace

    from studio import focus
    from studio.library_controller import LibraryController

    session = SimpleNamespace(timing_verified=True, timing_quality=SimpleNamespace(degraded=False))
    ctl = LibraryController(SimpleNamespace(session=session), 0)
    day, am_ms, pm_ms = "2026-09-05", 1788598800000, 1788620400000
    pm = {**_entry("GX9302", 45.475), "date": day, "start_ms": pm_ms}
    am = {**_entry("GX9301", 44.963), "date": day, "start_ms": am_ms}
    moment, standing = ctl._pb_decision(am, {"entries": [pm]})
    assert moment["kind"] == standing["kind"] == "fastest", (moment, standing)
    assert ctl.previous_pb is None and not ctl.offers_pb_compare(standing), ctl.previous_pb
    # One page, one order: the focus list set on the afternoon calls the morning older too.
    item = focus.item_from_dict({
        "cid": 7, "direction": 1, "enter_frac": 0.7, "exit_frac": 0.75, "median_s": 4.08,
        "iqr_s": 0.1, "n_laps": 36, "time_lost": 0.2, "reason": "", "reach": "",
        "fingerprint": "GX9302", "date": day, "lap_total": 737.0, "verified": True,
        "degraded": False, "n_of": 36, "method": focus.METHOD_MATCHED, "start_ms": pm_ms})
    assert focus.recorded_before({"fingerprint": "GX9301", "date": day, "start_ms": am_ms}, item)


def test_the_default_never_tops_up_a_list_that_ran_the_other_way_round():
    """cool/layout-reversed-synth: the debrief's pre-fill puts today's corners into the list's FREE
    slots. A list set on the track driven one way round, topped up on a day driven the other way,
    holds both rotations — and the check refuses one half of it as LAYOUT_REVERSED on either
    layout, for good. So a reversed day's list is not topped up on a forward day (nor the other
    way); the same rotation, or an item stored before rotations were (0), is filled as before."""
    from types import SimpleNamespace

    from studio import focus
    from studio.library_controller import LibraryController

    def item(cid, rotation):
        return focus.FocusItem(cid=cid, direction=-1, enter_frac=0.1 * cid,
                               exit_frac=0.1 * cid + 0.05, median_s=3.0, iqr_s=0.1, n_laps=14,
                               time_lost=0.2, reason="", reach="", fingerprint=f"GX{rotation}",
                               date="2026-09-01", lap_total=961.0, verified=True,
                               degraded=False, rotation=rotation)

    def day(rotation, stored):
        """A controller on a session whose laps run `rotation`, over the list `stored`."""
        session = SimpleNamespace(
            focus_context=lambda _entry, _track: {"rotation": rotation},
            focus_items=lambda cids, _entry: [item(c, rotation) for c in cids])
        ctl = LibraryController(SimpleNamespace(session=session), 0)
        kept: list = []
        ctl._focus_entry = lambda: {"fingerprint": "GX0002", "track": "Demo"}
        ctl._focus_items = lambda _track: list(stored)
        ctl._save_focus = lambda _track, items: kept.extend(items)
        ctl._baseline_refusal = lambda *_a: ""
        ctl.update_focus_list = lambda: None
        return ctl, kept

    # A reversed (clockwise) day first: its debrief puts its one corner on the empty list.
    ctl, kept = day(-1, [])
    assert ctl.pre_promote_focus([7]) == [7] and [i.rotation for i in kept] == [-1], kept
    reversed_list = list(kept)
    # …then a forward (anticlockwise) day: two free slots, and none is filled.
    ctl, kept = day(1, reversed_list)
    assert ctl.pre_promote_focus([5, 3]) == [] and kept == [], \
        ("a forward day topped up a reversed day's list", kept)
    # The same rotation, or an item that stored none, is topped up as before.
    for stored in (reversed_list, [item(7, 0)]):
        ctl, kept = day(-1, stored)
        assert ctl.pre_promote_focus([5, 3]) == [5, 3], stored
        assert [i.cid for i in kept] == [7, 5, 3], kept
    # A day that tells no rotation (no loop in its corners) is not stopped either.
    ctl, kept = day(0, reversed_list)
    assert ctl.pre_promote_focus([5]) == [5], kept
    print("ok default: a list is never topped up from a day run the other way round")


def test_the_lead_says_it_is_a_default_and_how_to_undo_it():
    three, one, none = debrief_note([7, 5, 3]), debrief_note([4]), debrief_note([])
    assert "Pacer put C7, C5 and C3" in three and "a default, not a decision" in three, three
    assert "remove any you won't work on" in three, three
    assert "Pacer put C4," in one and "remove it if you won't work on it" in one, one
    # The synthetic demo's list is never written (QA EVAL-2): the same default, not promised for
    # a next time that will not see it.
    demo = debrief_note([7, 5, 3], saved=False)
    assert "Pacer put C7, C5 and C3" in demo and "a default, not a decision" in demo, demo
    assert "for next time" in three and "for next time" not in demo, demo
    # Release QA of 0.29.0: since Coaching ranks only past the step, the demo ranks ONE corner, and
    # the lead said "Pacer put C1, from 1 Jun's top corners" — a plural for a shortlist of one.
    alone = debrief_note([1], saved=False, ranked=1)
    assert "'s top corner," in alone and "top corners" not in alone, alone
    assert "'s top corners," in debrief_note([4], ranked=3), debrief_note([4], ranked=3)
    for note in (three, one, none, demo, alone):
        assert note.endswith("Esc returns to your usual layout."), note
    print(f"ok lead: {three!r}; the demo's: {demo!r}")


# ---------------------------------------------------------------- 2-3. the page's debrief mode
def _layout_fixtures():
    import test_coaching_panel_layout as layout
    return layout


def test_the_debrief_is_the_shortlist_and_keeps_the_braking_line_off_its_rows():
    layout = _layout_fixtures()
    rows = layout._rows(8)
    # C1's laps separate a direction, so the ordinary page prints its braking line under the reason.
    # (The ESTIMATED "Brake ~N m later" this line replaced never reached the debrief either; L7 took
    # it off the ordinary page too.)
    rows[0] = coaching.Opportunity(cid=1, direction=1, time_lost=0.30, entry_dist=972.4,
                                   reason=layout._reason(coaching.REASON_BRAKING),
                                   phases=rows[0].phases, evidence=rows[0].evidence)
    line = "Braking later went with quicker passes here (16 laps)"
    p = layout._panel(rows, (1400, 800),
                      directions={1: coaching.BrakeDirection(cid=1, n_laps=16, rho=-0.74, p=0.0011,
                                                             family=12, p_holm=0.0132)})
    cell = lambda: p.table.item(0, _PANEL_COL_REASON).text()  # noqa: E731
    assert cell().endswith(line), cell()
    assert p.table.rowCount() > PANEL_TOP_N, "a tall page shows more than the shortlist"
    assert p.shortlist_cids() == [1, 2, 3], p.shortlist_cids()

    p.set_debrief(True, "First session logged at Stadium: best lap 0:47.500.", [1, 2, 3])
    for _ in range(4):
        _APP.processEvents()
    assert p.table.rowCount() == PANEL_TOP_N, p.table.rowCount()
    assert "Braking" not in cell() and "Brake ~" not in cell(), cell()
    assert "longer on the brakes" in cell(), cell()
    assert not p.debrief_block.isHidden(), "the lead is the first thing on the debrief"
    assert "First session logged" in p.debrief_block.headline.text()
    assert p.debrief_block.full_text() in p.summary_label.toolTip(), "demoted, never deleted"

    p.set_debrief(False)
    for _ in range(4):
        _APP.processEvents()
    assert cell().endswith(line), "the braking line is back once the debrief ends"
    assert p.table.rowCount() > PANEL_TOP_N and p.debrief_block.isHidden()
    print(f"ok page: debrief = {PANEL_TOP_N} rows, no braking line; after it the ranking and the "
          "line are back")


# ------------------------------------------------------------- 3b. one writer of the debrief
_DEBRIEF_WRITERS = {"show_debrief", "_end_debrief", "update_debrief_lead"}


def test_only_the_view_writes_the_page_debrief_state():
    """FIRST-OPEN-LOOP-9 (QA r4 CODE-5): the view and the page each hold "this is the debrief", and
    `LibraryController._land_named_verdict` reached past the view into
    `view.opportunities.set_debrief` — a panel-only on would leave a page `_end_debrief` never
    ends. In studio/, `OpportunitiesPanel.set_debrief` is called only by CentralView's three
    debrief methods."""
    import ast

    studio_dir = os.path.join(_REPO, "studio")
    strays = []
    for root, _dirs, files in os.walk(studio_dir):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
            rel = os.path.relpath(path, _REPO)
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(fn):
                    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and node.func.attr == "set_debrief"):
                        continue
                    if rel == os.path.join("studio", "central_view.py") \
                            and fn.name in _DEBRIEF_WRITERS:
                        continue
                    strays.append(f"{rel}:{node.lineno} ({fn.name})")
    assert not strays, f"a writer of the page's debrief state outside the view: {strays}"
    print("ok one writer: set_debrief is called only by CentralView's debrief methods")


def test_a_lead_outside_the_debrief_is_a_no_op():
    """`CentralView.update_debrief_lead` re-leads an OPEN debrief in place (Save as track naming
    the circuit) and does nothing otherwise — the card is the caller's to raise then."""
    from types import SimpleNamespace

    from studio.central_view import CentralView

    calls = []
    page = SimpleNamespace(set_debrief=lambda *a: calls.append(a))
    view = SimpleNamespace(_debrief=False, opportunities=page)
    view.is_debrief = lambda: view._debrief
    assert CentralView.update_debrief_lead(view, "line", [1], True, False) is False
    assert calls == [], calls
    view._debrief = True
    assert CentralView.update_debrief_lead(view, "line", [1], True, False) is True
    assert calls == [(True, "line", [1], True, False)], calls
    print("ok lead: a no-op outside the debrief; in place, saved carried, while it is up")


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


@contextlib.contextmanager
def _loop_log():
    """The INFO lines the training loop's gestures write (FIRST-OPEN-LOOP-3), as a list of
    messages. The level is set HERE: only `main()` configures the session log at INFO, so under
    ctest the 'studio' logger inherits the root's WARNING and every line would be dropped before
    reaching a handler — a capture that saw nothing would read as "no line was written"."""
    import logging

    class _Lines(logging.Handler):
        def __init__(self):
            super().__init__(logging.INFO)
            self.lines: list[str] = []

        def emit(self, record):
            self.lines.append(f"{record.levelname} {record.getMessage()}")

    log, lines = logging.getLogger("studio"), _Lines()
    level = log.level
    log.addHandler(lines)
    log.setLevel(logging.INFO)
    try:
        yield lines.lines
    finally:
        log.removeHandler(lines)
        log.setLevel(level)


def _said(lines: list[str], part: str) -> list[str]:
    """The captured lines naming `part`, each asserted INFO: a gesture is not a warning."""
    hits = [ln for ln in lines if part in ln]
    assert all(ln.startswith("INFO ") for ln in hits), ("a loop line not at INFO", hits)
    return hits


def _corners(cids) -> str:
    return ", ".join(f"C{c}" for c in cids)


# The generated demo recording, ONCE PER PROCESS (GATES-3): its arguments -> (the private directory
# holding it, its path). A generation costs 1.5-2 s, and the journey files built from this helper
# called it up to five times each, for byte-identical bytes. The directory object sits in the value
# so it lives as long as this module; a local would be finalised, and its file deleted, under the
# next caller. Per process only: sharing across CTest processes would need a fixed temp name,
# which tests/test_temp_isolation.py forbids.
_GEN: dict[tuple, tuple[tempfile.TemporaryDirectory, str]] = {}


def three_habits():
    """The demo's planted C1 habit plus two more, at C5 and C6 (grip × 0.88 on the same laps), so a
    journey's debrief ranks THREE corners past the timing step: C1 +0.39 s, C5 +0.15 s, C6 +0.13 s.
    The habit-free demo circuit these journeys drove until cool/coaching-ranks-past-the-step ranked
    C7, C5 and C3 at under 0.07 s each — no claim at all once Coaching ranks only past the step."""
    from studio.dev import make_demo as md
    from studio.dev import synth_gopro as sg
    return (md.DEMO_HABIT,) + tuple(sg.Habit(cid=c, grip=0.88, brake=1.0,
                                             clean_laps=md.DEMO_HABIT.clean_laps) for c in (5, 6))


def _two_recordings(folder: str) -> tuple[str, str]:
    """Two recordings of the built-in demo circuit, byte-identical telemetry under two recording
    numbers — two library identities (GX9001, GX9002), so the second open is a first open too.
    Two FRESH copies in `folder` on every call: journeys write sidecars and library rows beside
    them, so only the generation is shared. Driven with `three_habits`, so the debrief has three
    corners to rank."""
    from studio.dev import make_demo as md
    from studio.dev import synth_gopro as sg
    key = (md.DEMO_SEED, md.DEMO_LAPS, 1, md.DEMO_ORIGIN, "three_habits")
    if key not in _GEN:
        gen = tempfile.TemporaryDirectory(prefix="debrief_gen_")
        rec = sg.generate(os.path.join(gen.name, "gen"), key[0], key[1], chapters=key[2],
                          origin=key[3], habit=three_habits())
        _GEN[key] = (gen, rec.paths[0])
    out = []
    for n in ("9001", "9002"):
        os.makedirs(os.path.join(folder, n))
        out.append(os.path.join(folder, n, f"GX01{n}.MP4"))
        shutil.copyfile(_GEN[key][1], out[-1])
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
    with tempfile.TemporaryDirectory(prefix="debrief_rec_") as folder, _fresh_app_support(), \
            _loop_log() as said:
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
            # Its top corners named by the session's own day (`focus.open_day`), never "today's"
            # on a recording made another day.
            day = panel.focus_block.now_day
            assert day != "today" and debrief_note(short, day=day, ranked=len(short)) in lead, (day, lead)
            # The session log tells the pre-fill (Pacer's default, no click) from a hand edit, so a
            # race-day read can count the edits (FIRST-OPEN-LOOP-3).
            shown = _said(said, "debrief shown")
            assert len(shown) == 1 and "first open GX9001" in shown[0], said
            assert f"pre-filled with {_corners(short)} (Pacer's default)" in shown[0], shown
            assert not _said(said, "by hand"), said

            # One click drops a pre-promoted corner, and the lead stops naming it.
            gone = short[-1]
            (drop,) = _visible(panel.focus_block.drop_buttons, f"Remove C{gone}")
            drop.click()
            _settle(0.1)
            assert [i.cid for i in listed()] == short[:-1], listed()
            assert f"C{gone}" not in panel.debrief_block.note.text(), panel.debrief_block.note.text()
            (edit,) = _said(said, "by hand")
            assert edit.startswith("INFO focus list for GX9001"), edit
            assert f"removed C{gone} by hand -> {_corners(short[:-1])}" in edit, edit

            # Esc: the grid he left, on the Coaching page he was reading (the debrief ranked
            # corners: wave 27), his remembered tab untouched; the estimate is the page's again.
            saved = view._saved_splitter_sizes
            assert saved[0] == grid[0], ("Esc would not return to his grid", saved)
            QTest.keyClick(win, Qt.Key_Escape)
            _settle(0.2)
            assert not view.is_debrief() and view._maximized_panel is None
            assert view.tab_bar.currentIndex() == 3 and prefs.lap_panel_tab() == 1
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
            shown = _said(said, "debrief shown")
            assert len(shown) == 2 and "first open GX9002" in shown[1], shown
            assert f"pre-filled with C{gone} (Pacer's default)" in shown[1], shown
            refusal = panel.focus_block.full_text()
            assert "no session record for" in refusal, refusal
            (mark,) = _visible([panel.focus_block.mark_button], "Mark ")
            mark.click()
            _settle(0.1)
            (dry,) = _said(said, "marked dry by hand")
            assert dry == "INFO session records: 2 marked dry by hand (GX9001, GX9002)", dry
            verdict = panel.focus_block.full_text()
            assert view.is_debrief(), "the verdict must arrive without leaving the debrief"
            for cid in kept:
                line = next(ln for ln in verdict.splitlines() if ln.startswith(f"C{cid} — "))
                assert "no change you can act on" in line, verdict

            # A re-open is not a first open: the remembered tab, the grid, nothing promoted. The one
            # real-window pin of the rule, beside test_library's verdict table (FIRST-OPEN-LOOP-17).
            before = {i.cid: focus.item_to_dict(i) for i in listed()}
            _open(win, b)
            view = win.view
            assert not view.is_debrief() and view._maximized_panel is None
            assert view.tab_bar.currentIndex() == 1 and prefs.lap_panel_tab() == 1
            assert {i.cid: focus.item_to_dict(i) for i in listed()} == before
            assert len(_said(said, "debrief shown")) == 2, "a re-open logged a first open"
            assert len(_said(said, "by hand")) == 2, _said(said, "by hand")
        finally:
            win.close()
            win.deleteLater()
            _APP.processEvents()
            for k, fn in boxes.items():
                setattr(QMessageBox, k, fn)
    print(f"ok journey: {short} promoted, C{gone} dropped in one click, Esc to Coaching, the "
          f"second open filled the slot with C{gone} and reached a verdict in one click")


def test_a_first_open_with_no_corner_past_the_step_opens_the_normal_view():
    """cool/coaching-ranks-past-the-step (review S5): a first open whose corners all lose under the
    timing step lands on no debrief — the existing rule, nothing ranked — and opens the ordinary
    grid on the driver's tab, with the personal-best card the debrief would otherwise carry. The
    Coaching tab says no corner stands out, and its sentence points elsewhere. The habit-free demo
    circuit is that session (its corners lose +0.04 s and less)."""
    from PySide6.QtWidgets import QLabel

    from studio import coaching
    from studio.app import StudioWindow
    from studio.dev import make_demo as md
    from studio.dev import synth_gopro as sg
    with tempfile.TemporaryDirectory(prefix="debrief_level_") as folder, _fresh_app_support():
        rec = sg.generate(os.path.join(folder, "gen"), md.DEMO_SEED, md.DEMO_LAPS, chapters=1,
                          origin=md.DEMO_ORIGIN)
        path = os.path.join(folder, "9101", "GX019101.MP4")
        os.makedirs(os.path.dirname(path))
        shutil.copyfile(rec.paths[0], path)
        win = StudioWindow([])
        win.resize(1440, 900)
        win.show()
        try:
            _open(win, path)
            opps = win.session.coaching_opportunities()
            assert not coaching.shortlist(opps) and opps.rows, opps.rows
            assert not win.view.is_debrief(), "a session with nothing ranked landed on a debrief"
            assert win.library_ctl.landing() is None
            toast = win._pb_toast
            assert toast is not None and toast.isVisible(), "no personal-best card on a first open"
            words = " ".join(lab.text() for lab in toast.findChildren(QLabel))
            assert "First session logged" in words, words
            page = win.view.opportunities
            assert page.summary_label.text() == coaching.headline(opps)
            assert "no corner stands out" in page.summary_label.text()
            assert page.theme_block.headline.text() == coaching.no_focus_sentence(opps) != ""
        finally:
            win.close()
            win.deleteLater()
            _APP.processEvents()
    print("ok level first open: no debrief, the grid, the PB card, 'no corner stands out'")


def _way_recordings(folder: str) -> list[str]:
    """The demo's circuit forward (seed 1), then in reverse twice (seeds 4 and 6),
    each with `three_habits` planted and under its own recording number: every synthetic recording
    is GX019001, and the Library would see three as one."""
    from studio.dev import make_demo as md
    from studio.dev import synth_gopro as sg
    out = []
    for n, (off, rev) in zip(("9301", "9302", "9303"), ((1, False), (4, True), (6, True)),
                             strict=True):
        key = (md.DEMO_SEED + off, md.DEMO_LAPS, 1, md.DEMO_ORIGIN, rev, "three_habits")
        if key not in _GEN:
            gen = tempfile.TemporaryDirectory(prefix="debrief_gen_")
            rec = sg.generate(os.path.join(gen.name, "gen"), key[0], key[1], chapters=key[2],
                              origin=key[3], reverse=rev, habit=three_habits())
            _GEN[key] = (gen, rec.paths[0])
        os.makedirs(os.path.join(folder, n))
        out.append(os.path.join(folder, n, f"GX01{n}.MP4"))
        shutil.copyfile(_GEN[key][1], out[-1])
    return out


def test_a_personal_best_is_never_compared_with_the_other_way_round():
    """cool/pb-per-direction, on the real window, jailed: the shipped demo (lands, keeps nothing,
    as before), then the demo's circuit forward, then in reverse, then a faster reverse. Before, the
    first reverse read "0.18 s off your personal best there" against the forward lap (and the other
    order "New personal best … 0.16 s faster"). Now: a first this way round, no Compare, no mark on
    its lap card; and the faster reverse IS a personal best, over the reverse lap."""
    from PySide6.QtWidgets import QMessageBox

    from studio import share_card
    from studio.app import StudioWindow
    from studio.dev import make_demo as md
    from studio.library_controller import DEMO_DEBRIEF_LINE

    boxes = {k: getattr(QMessageBox, k) for k in ("critical", "warning", "information", "question")}
    for k in boxes:
        setattr(QMessageBox, k, staticmethod(lambda *a, **_kw: (_ for _ in ()).throw(
            AssertionError(f"a modal opened: {a[1:3]}"))))
    saved_env = os.environ.get("PACER_DEMO_MP4")
    with tempfile.TemporaryDirectory(prefix="debrief_rec_") as folder, _fresh_app_support():
        demo = os.path.join(folder, "demo", "GX019000.MP4")
        os.makedirs(os.path.dirname(demo))
        md.make(demo, render=False)
        os.environ["PACER_DEMO_MP4"] = demo
        fwd, rev, faster = _way_recordings(folder)
        win = StudioWindow([])
        win.resize(1440, 900)
        win.show()

        def said():
            ctl = win.library_ctl
            card = share_card.card_data(win.session, pb_standing=ctl.pb_standing,
                                        prior_date=(ctl.previous_pb or {}).get("date"))
            return (ctl.debrief_pb_line(), card.pb, (ctl.previous_pb or {}).get("fingerprint"),
                    ctl.offers_pb_compare(ctl.pb_standing))

        try:
            _open(win, demo)
            assert win.library_ctl.debrief_pb_line() == DEMO_DEBRIEF_LINE
            assert library.load()["entries"] == [], "the demo kept a row"
            _open(win, fwd)
            track, t = win.session.track_name, fmt_time(win.session.lap_time(
                win.session.best_lap_id()))
            assert said() == (f"First session logged at {track}: best lap {t}, the time to beat "
                              "next time.", "", None, False), said()
            _open(win, rev)
            slow = fmt_time(win.session.lap_time(win.session.best_lap_id()))
            assert win.view.is_debrief()
            assert said() == (f"First session logged at {track} this way round: best lap {slow}, "
                              "the time to beat next time.", "", None, False), said()
            assert "personal best" not in win.view.opportunities.debrief_block.full_text()
            _open(win, faster)
            fast = win.session.lap_time(win.session.best_lap_id())
            line, mark, prev, compare = said()
            assert line == (f"New personal best at {track} this way round: {fmt_time(fast)}, "
                            f"0.11 s faster than your previous best ({slow})."), line
            assert mark == "NEW PB · −0.11 s vs 1 Jun" and prev == "GX9302" and compare, said()
            rows = {e["fingerprint"]: e.get("rotation") for e in library.load()["entries"]}
            assert rows == {"GX9301": -1, "GX9302": 1, "GX9303": 1}, rows
        finally:
            win.close()
            win.deleteLater()
            _APP.processEvents()
            for k, fn in boxes.items():
                setattr(QMessageBox, k, fn)
            if saved_env is None:
                os.environ.pop("PACER_DEMO_MP4", None)
            else:
                os.environ["PACER_DEMO_MP4"] = saved_env
    print(f"ok other way round: a first this way round at {slow}, then a PB at {fmt_time(fast)}")


def test_the_pb_card_ends_with_the_session_it_celebrated():
    """cool/pb-card-ends-with-its-session, on the real window, jailed: the PB card lived on its own
    6 s clock and was replaced only by the next card, so a recording opened inside those 6 s kept
    "Level with your personal best — …" over a DIFFERENT session (a no-laps one too, on a synthetic
    season). Now a load starting takes it down — before the next session's verdict, which is then
    its own (here: the first open's debrief, this way round) — and so does a transient status-line
    confirmation about the outgoing session; the session going (the welcome) takes the card too."""
    import shiboken6
    from PySide6.QtWidgets import QMessageBox

    from studio.app import STATUS_MS, StudioWindow
    from studio.overlays import PBToast

    def cards(win):
        return [t for t in win.findChildren(PBToast) if shiboken6.isValid(t) and t.isVisible()]

    boxes = {k: getattr(QMessageBox, k) for k in ("critical", "warning", "information", "question")}
    for k in boxes:
        setattr(QMessageBox, k, staticmethod(lambda *a, **_kw: (_ for _ in ()).throw(
            AssertionError(f"a modal opened: {a[1:3]}"))))
    with tempfile.TemporaryDirectory(prefix="debrief_rec_") as folder, _fresh_app_support():
        fwd, rev, _faster = _way_recordings(folder)
        win = StudioWindow([])
        win.resize(1440, 900)
        win.show()
        try:
            _open(win, fwd)
            track = win.session.track_name
            # The card A raises (what a re-open level with the PB shows), and a confirmation
            # about A on the status line.
            win.library_ctl.show_pb_moment({"kind": "beat", "track": track, "best": 44.9,
                                            "prior": 45.2, "improvement": 0.3})
            _settle(0.3)
            assert len(cards(win)) == 1, "the card for the first session never appeared"
            win.statusBar().showMessage("lap card copied — paste it into a chat", STATUS_MS)
            win._open_recordings([rev])               # B, at once: well inside the card's 6 s
            assert not cards(win), "the first session's PB card is still up as the next loads"
            assert win._pb_toast is None
            assert win.statusBar().currentMessage() == "", win.statusBar().currentMessage()
            done = {"v": False}
            win.loadFinished.connect(lambda: done.__setitem__("v", True))
            deadline = time.time() + 120
            while not done["v"] and time.time() < deadline:
                _APP.processEvents()
                time.sleep(0.01)
            assert done["v"], "the second recording did not load"
            _settle(0.4)
            assert not cards(win), [c.title_label.text() for c in cards(win)]
            line = win.library_ctl.debrief_pb_line()
            assert line.startswith(f"First session logged at {track} this way round"), line
            assert win.view.is_debrief()
            # The session going takes its card with it.
            win.library_ctl.show_pb_moment({"kind": "first", "track": track, "best": 45.1})
            _settle(0.3)
            assert len(cards(win)) == 1
            # ...and the status line's confirmation about it (w19 QA: it stayed over the welcome).
            win.statusBar().showMessage("lap card copied — paste it into a chat", STATUS_MS)
            win._show_welcome()
            assert not cards(win) and win._pb_toast is None
            assert win.statusBar().currentMessage() == "", win.statusBar().currentMessage()
            # A welcome re-read over the welcome keeps what a caller just said there.
            win.statusBar().showMessage("Load cancelled.", STATUS_MS)
            win._refresh_welcome()
            assert win.statusBar().currentMessage() == "Load cancelled."
        finally:
            win.close()
            win.deleteLater()
            _APP.processEvents()
            for k, fn in boxes.items():
                setattr(QMessageBox, k, fn)
    print("ok PB card: gone when the next load starts, the next session's own verdict follows")


def test_a_mixed_library_never_names_a_way_its_figures_do_not_have():
    """cool/pb-per-direction's review, on the real window, jailed: two forward rows written WITHOUT
    a direction (as the build before writes them: seeds 1 and 2), a forward row that knows it
    (seed 3, clockwise), then the circuit in reverse (seed 1). The reverse is still compared with
    the two old rows — they may be either way, and are compared as before — so its line and the
    Library must not say "this way round" or "anticlockwise" beside a figure, 0:44.929, that is the
    forward seed-1 lap. Before the fix: "Best lap 0:45.094 at Synthetic demo circuit this way
    round, 0.16 s off your personal best there (0:44.929)." and "…, anticlockwise · 4 sessions"."""
    from PySide6.QtWidgets import QMessageBox

    from studio import session as studio_session
    from studio.app import StudioWindow
    from studio.dev import make_demo as md
    from studio.dev import synth_gopro as sg
    from studio.library_dialog import _COL_DATE, FP_ROLE, LibraryDialog

    boxes = {k: getattr(QMessageBox, k) for k in ("critical", "warning", "information", "question")}
    for k in boxes:
        setattr(QMessageBox, k, staticmethod(lambda *a, **_kw: (_ for _ in ()).throw(
            AssertionError(f"a modal opened: {a[1:3]}"))))
    rotation = studio_session.Session._lap_rotation
    with tempfile.TemporaryDirectory(prefix="debrief_rec_") as folder, _fresh_app_support():
        paths = []
        for n, (off, rev) in zip(("9311", "9312", "9313", "9314"),
                                 ((1, False), (2, False), (3, False), (1, True)), strict=True):
            key = (md.DEMO_SEED + off, md.DEMO_LAPS, 1, md.DEMO_ORIGIN, rev, "three_habits")
            if key not in _GEN:
                gen = tempfile.TemporaryDirectory(prefix="debrief_gen_")
                rec = sg.generate(os.path.join(gen.name, "gen"), key[0], key[1], chapters=key[2],
                                  origin=key[3], reverse=rev, habit=three_habits())
                _GEN[key] = (gen, rec.paths[0])
            os.makedirs(os.path.join(folder, n))
            paths.append(os.path.join(folder, n, f"GX01{n}.MP4"))
            shutil.copyfile(_GEN[key][1], paths[-1])
        win = StudioWindow([])
        win.resize(1440, 900)
        win.show()
        dlg = None
        try:
            # The build before this one stores no direction: a Session that cannot tell writes none.
            studio_session.Session._lap_rotation = lambda self: 0
            _open(win, paths[0])
            track = win.session.track_name
            pb = fmt_time(win.session.lap_time(win.session.best_lap_id()))
            _open(win, paths[1])
            studio_session.Session._lap_rotation = rotation
            _open(win, paths[2])
            _open(win, paths[3])
            ctl = win.library_ctl
            t = win.session.lap_time(win.session.best_lap_id())
            rows = {e["fingerprint"]: e.get("rotation") for e in library.load()["entries"]}
            assert rows == {"GX9311": None, "GX9312": None, "GX9313": -1, "GX9314": 1}, rows
            line = ctl.debrief_pb_line()
            mine = next(e for e in library.load()["entries"] if e["fingerprint"] == "GX9314")
            prev = (library.previous_pb(library.load(), track, "GX9314", mine.get("date"),
                                        mine.get("start_ms"), rotation=1) or {}).get("fingerprint")
            assert line == (f"Best lap {fmt_time(t)} at {track}, 0.16 s off your personal best "
                            f"there ({pb})."), line
            assert prev == "GX9311", prev            # the comparison itself is unchanged
            dlg = LibraryDialog(library.load(), lambda _p: None)
            dlg.resize(740, 740)
            for r in range(dlg.table.rowCount()):
                if dlg.table.item(r, _COL_DATE).data(FP_ROLE) in ("GX9313", "GX9314"):
                    dlg.table.selectRow(r)
                    said = (dlg._summary.text(), dlg._pb_title.text())
                    assert not any("clockwise" in s for s in said), said
                    assert said[0].startswith(f"{track}  ·  "), said
        finally:
            studio_session.Session._lap_rotation = rotation
            if dlg is not None:
                dlg.deleteLater()
            win.close()
            win.deleteLater()
            _APP.processEvents()
            for k, fn in boxes.items():
                setattr(QMessageBox, k, fn)
    print(f"ok mixed library: {line!r}")


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
            assert view.tab_bar.currentIndex() == 3, "Esc leaves a ranked debrief on Coaching"
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
