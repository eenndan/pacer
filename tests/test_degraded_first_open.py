"""A first open on degraded timing decides nothing, says why, and offers no focus pick (QA round 3,
2026-09-26: REG-2).

WHY. A GPS-degraded first open landed on the debrief with a lead that said only "Esc returns to your
usual layout.": no PB line, no reason, an empty status bar. The PB is refused on such timing
(`library.pb_moment_for`), so is the debrief's pre-promotion, and so is #426's Replace, but the
page invited "Focus list · empty — pick up to 3 corners below" and a manual Add stored the corner,
flagged `degraded`, where every later check answers it "no verdict" for good. Measured on the
owner's MK_18_09 with the low-GPS gate lowered 0.08 -> 0.05 in-process (the REG lane's stand-in for a
longer poor-GPS day; the whole recording rejects 6.39 % of fixes): debrief, lead only the Esc line,
Add C5 enabled, and one click wrote a `"degraded": true` item. Its chapter 1 alone (9.75 %) as a
one-chapter recording ranks no corner, so it never reached the debrief, and said nothing either.

Now, like a partial first open (#420), it decides nothing and lands on the usual layout (the map's
banner shows the GPS there); its row is written, flagged; the status bar says why in one line with
the measure; and Add, Replace and the empty-list invitation all refuse a session whose corners can
be no baseline (`focus.baseline_refusal`), with the same words.

Pinned here: the refusal and its words (pure), the focus block on such a report, and the journey on
the real window over a synthetic recording made degraded by the same in-process gate, beside a
negative control: the same recording at the shipped gate still lands on its debrief. And the same at a
circuit Pacer does not know (QA r4 CODE-2): the notice says why the line's drag will decide nothing,
and neither it nor Save as track promises a PB or a focus list.

Run: python tests/test_degraded_first_open.py   (~10 s)
"""
import contextlib
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()

from studio import data_quality, focus  # noqa: E402

REFUSED = "GPS quality low (10% of fixes rejected), so this session's corners can't be a baseline"


def _ctx(**kw) -> dict:
    ctx = {"list_track": "Sandown Park", "track": "Sandown Park", "fingerprint": "GX0067",
           "date": "2026-09-18", "start_ms": 0, "lap_total": 737.0, "verified": True,
           "degraded": False, "untrusted": ""}
    return {**ctx, **kw}


def _item(fp="GX0065", date="2026-08-30") -> focus.FocusItem:
    return focus.item_from_dict({
        "cid": 7, "direction": 1, "enter_frac": 0.7, "exit_frac": 0.75, "median_s": 4.08,
        "iqr_s": 0.1, "n_laps": 36, "time_lost": 0.2, "reason": "", "reach": "",
        "fingerprint": fp, "date": date, "lap_total": 737.0, "verified": True, "degraded": False,
        "n_of": 36, "method": focus.METHOD_MATCHED, "start_ms": 0})


# ------------------------------------------------------------------------ the refusal, pure
def test_a_session_on_untrusted_timing_can_set_no_baseline():
    degraded = _ctx(degraded=True, untrusted="GPS quality low (10% of fixes rejected)")
    refuse = getattr(focus, "baseline_refusal", lambda ctx: "")   # fails on the rule, not an import
    assert refuse(degraded) == REFUSED, refuse(degraded)
    assert "provisional" in refuse(_ctx(verified=False)), refuse(_ctx(verified=False))
    assert refuse(_ctx()) == "", "a trusted session must stay free to set a list"
    # Carried on the report — with a list and without one — and it withdraws the Replace offer.
    sample = focus.CornerSample(median=4.02, iqr=0.1, n_laps=37, n_of=37)
    report = focus.verdict([_item()], degraded, [sample], {})
    assert getattr(report, "baseline_refusal", "") == REFUSED, report
    assert focus.replace_offer(report, [1, 5]) is None, "offered a replace nothing can answer"
    trusted = focus.verdict([_item()], _ctx(), [sample], {})
    assert trusted.baseline_refusal == "" and focus.replace_offer(trusted, [1, 5]) is None, (
        "the control: no record on either day, so no offer either way")
    print(f"ok refusal: {REFUSED!r}")


def test_the_focus_block_invites_no_pick_it_could_never_answer():
    from studio.coaching_panel import FOCUS_EMPTY_LINE, FocusBlock
    block = FocusBlock()
    degraded = _ctx(degraded=True, untrusted="GPS quality low (10% of fixes rejected)")
    block.set_report(focus.verdict([], degraded, [], {}), [5, 2, 8])     # an empty list
    block.set_selected_corner(5)
    assert not block.add_button.isEnabled(), "Add stayed on for a corner that can be no baseline"
    assert block.add_button.toolTip() == REFUSED, block.add_button.toolTip()
    assert block.add_state() == (False, REFUSED), block.add_state()
    assert block.empty_line.text() == f"Focus list · empty — {REFUSED}", block.empty_line.text()
    # ...and on a trusted session the invitation and the Add are exactly as they were.
    block.set_report(focus.verdict([], _ctx(), [], {}), [5, 2, 8])
    block.set_selected_corner(5)
    assert block.add_button.isEnabled() and block.empty_line.text() == FOCUS_EMPTY_LINE
    print("ok block: no invitation, Add off with the reason; unchanged on trusted timing")


# ------------------------------------------------------------------------ the real window
@contextlib.contextmanager
def _gate(frac: float):
    """The low-GPS gate, IN THIS PROCESS ONLY, as the REG lane lowered it: at 0.0 every recording
    counts as GPS-degraded, so the synthetic recording stands in for a poor-GPS day."""
    saved = data_quality.DROPPED_FIX_CONCERN_FRAC
    data_quality.DROPPED_FIX_CONCERN_FRAC = frac
    try:
        yield
    finally:
        data_quality.DROPPED_FIX_CONCERN_FRAC = saved


def _first_open(path):
    from test_debrief_landing import _open

    from studio.app import StudioWindow
    win = StudioWindow([])
    win.resize(1440, 900)
    win.show()
    _open(win, path)
    return win


def test_a_degraded_first_open_lands_on_the_usual_layout_and_says_why():
    from test_debrief_landing import _fresh_app_support, _two_recordings

    with tempfile.TemporaryDirectory(prefix="degraded_") as folder, _fresh_app_support():
        a, b = _two_recordings(folder)
        # The negative control first: at the shipped gate this recording lands on its debrief, so
        # the landing below is refused by the degraded timing and by nothing else.
        with _window(b) as control:
            assert control.view.is_debrief(), "the control must land on its debrief"
            # Its default filled the list; empty it, so below an Add is off for one reason only.
            focus.save_for_track(control.session.track_name, [])
        with _gate(0.0), _window(a) as win:     # `degraded` is live: the gate holds throughout
            _check_the_degraded_landing(win)
    print("ok window: usual layout, the line with its measure, no pick offered or stored; the "
          "control lands on its debrief")


@contextlib.contextmanager
def _window(path):
    from test_debrief_landing import _settle
    win = _first_open(path)
    try:
        yield win
    finally:
        win.close()
        win.deleteLater()
        _settle(0.1)


def _check_the_degraded_landing(win):
    from studio import library

    quality, ctl, track = win.session.timing_quality, win.library_ctl, win.session.track_name
    assert quality.degraded and ctl.opened_new, (quality, ctl.opened_new)
    assert not win.view.is_debrief(), (
        "a first open on degraded timing landed on a debrief it can fill with nothing: "
        f"{win.view.opportunities.debrief_block.full_text()!r}")
    line = (f"GPS quality low ({quality.dropped_pct()}% of fixes rejected): too uncertain to judge "
            "a PB or set a focus list, so no debrief")
    assert line in (win.statusBar().currentMessage() or ""), win.statusBar().currentMessage()
    assert focus.for_track(focus.load(), track) == [], "a default was promoted from this session"
    # Add refuses in the words the page shows, and the same gesture through the controller (the
    # menu's path) stores nothing and says why.
    panel = win.view.opportunities
    panel.table.selectRow(0)
    block = panel.focus_block
    assert not block.add_button.isEnabled(), "Add was offered on degraded timing"
    refusal = block.add_button.toolTip()
    assert refusal.startswith(f"GPS quality low ({quality.dropped_pct()}% of fixes"), refusal
    ctl.focus_add(panel.shortlist_cids()[0])
    assert focus.for_track(focus.load(), track) == [], "a degraded baseline was stored"
    assert "can't be a baseline" in win.statusBar().currentMessage(), (
        win.statusBar().currentMessage())
    # The recording is whole, so its row IS written, flagged.
    row = next(e for e in library.load()["entries"] if e.get("fingerprint") == "GX9001")
    assert row["degraded"] is True, row


# --------------------------------------------------- the same, at a circuit Pacer does not know
def test_a_degraded_first_open_at_an_unnamed_circuit_promises_nothing():
    """QA r4 CODE-2 (2026-09-28). At a circuit with no name the verdict waits for the start line
    and then for the name (#433). On degraded timing neither can bring anything, yet after the
    drag the status bar still said "your PB and focus list wait for that" beside the clause saying
    there would be none, and before it the notice gave no reason the drag would decide nothing."""
    from PySide6.QtWidgets import QInputDialog
    from test_debrief_landing import _fresh_app_support, _open, _settle, _two_recordings
    from test_debrief_lands_right import _drag_start_line, _unknown_circuit
    from test_debrief_lands_right import _window as _blank_window  # this file has its own

    from studio import library

    name = "Test circuit"
    ask = QInputDialog.getText
    QInputDialog.getText = staticmethod(lambda *a, **k: (name, True))
    try:
        with tempfile.TemporaryDirectory(prefix="degraded_") as folder, _fresh_app_support(), \
                _gate(0.0), _unknown_circuit() as line, _blank_window() as win:
            a, _b = _two_recordings(folder)
            _open(win, a)
            ctl, quality = win.library_ctl, win.session.timing_quality
            # The measure's prefix only: at gate 0.0 it reads "0% of fixes rejected".
            why = f"GPS quality low ({quality.dropped_pct()}% of fixes rejected)"
            clause = f"{why}: too uncertain to judge a PB or set a focus list, so no debrief"
            # 1. Before the drag: the line still needs placing (lap times depend on it), and the
            #    notice says why placing it will decide nothing.
            assert quality.degraded and ctl.waiting_for_line, (quality.degraded, ctl.waiting_for_line)
            notice = win._session_notice() or ""
            assert "drag it into place" in notice and clause in notice, notice
            assert "wait for" not in notice, notice
            # 2. The drag: the row is written, flagged, and no name is waited for.
            _drag_start_line(win, line)
            assert win.session.timing_verified and win.session.track_name is None
            assert ctl.degraded_first_open and not win.view.is_debrief(), ctl.degraded_first_open
            notice = win._session_notice() or ""
            assert notice == clause, notice
            assert win.statusBar().currentMessage() == clause, win.statusBar().currentMessage()
            assert not ctl.waiting_for_name, "a name was promised a PB and focus list"
            (row,) = (e for e in library.load()["entries"] if e.get("fingerprint") == "GX9001")
            assert row["degraded"] is True and row["track"] is None, row
            # 3. Save as track names it and, as the notice said, brings nothing.
            win._save_as_track()
            _settle(0.2)
            assert win.session.track_name == name
            assert ctl.pb_standing is None, ctl.pb_standing
            assert focus.for_track(focus.load(), name) == [], "a focus list came of degraded laps"
            assert not win.view.is_debrief()
            assert win._session_notice() == clause, win._session_notice()
    finally:
        QInputDialog.getText = ask
    print("ok unnamed: the pre-drag notice says why, no name is waited for, Save as track brings "
          "nothing")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} DEGRADED-FIRST-OPEN TESTS PASSED")
