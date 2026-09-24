"""Your new PB against your previous PB, one gesture from the PB moment (board review PS-B4).

WHY. Every return visit the owner made to a track set a PB, and what he does with a session is
export best-lap overlays. "Where did the 0.10 s come from?" was four steps in three menus — File ▸
Open, Coaching ▸ Load reference…, Coaching ▸ Compare vs reference, File ▸ Export comparison video…
— though every piece had shipped. Measured on the real window, jailed, SD_30_08 (0065) then
SD_19_09 (0068) into a fresh library: the second open lands on its debrief saying "New personal
best at Sandown Park: 0:46.808, 0.10 s faster than your previous best (0:46.912)." and offered
nothing to do about it. Now that line, and the PB card when there is no debrief, carry "Compare with
your previous PB": it loads the library row the new PB beat as the reference and opens the compare
on the two best laps (2.4-2.6 s on that pair), with the export one click further. An ANALYSIS
gesture — the card's primary action used to be the share card, a loop that is a non-goal; share
stays, as a secondary link.

Pinned here:
  1. the row is the one behind the PB line's number (`library.previous_pb` vs `pb_moment`'s
     `prior`): same partition by recording, same trustworthy subset, earliest on a tie;
  2. missing footage (4 of the owner's 8 rows point at files moved or deleted since) is said
     plainly — which file, missing from where — and nothing is loaded;
  3. the card: the compare is the primary action, share a link; without a compare the card is
     what it was; every control clears the pointer floor inside a card no wider than before;
  4. the debrief line carries the button, sized into the page's height budget with the line;
  5. THE JOURNEY on the real StudioWindow, jailed, over two synthetic recordings of the built-in
     demo circuit (verified timing): the debrief's button and the card's, each end to end — pane
     A this session's best lap even with the playhead elsewhere, pane B the previous PB's lap,
     the debrief ended so the video panel is on screen, the export enabled — and the missing file.

Run: python tests/test_pb_compare.py   (~25 s; the pixi env's ffmpeg writes the video trak)
"""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _qtapp import themed_app  # noqa: E402

from studio import library  # noqa: E402
from studio._signal import fmt_time  # noqa: E402

_APP = themed_app()

# The measurement's own floor, independent of the code under test (tests/test_pb_toast.py's rule).
_MIN_HIT = 24


def _row(fp, best, *, track="Stadium", date="2026-09-01", trusted=True, paths=None):
    return {"fingerprint": fp, "stem": fp, "track": track, "date": date, "lap_count": 20,
            "best": best, "theoretical": best - 0.3, "verified": trusted, "degraded": False,
            "dropout": False, "paths": paths if paths is not None else [f"/nowhere/{fp}.MP4"]}


# ------------------------------------------------------------------ 1. the row behind the number
def test_the_previous_pb_is_the_row_behind_the_pb_lines_number():
    cases = {
        "the fastest other recording": [_row("GX0001", 47.2), _row("GX0003", 47.0)],
        # this recording's own faster row (a chapter opened earlier) is not its previous PB
        "never the recording itself": [_row("GX0001", 47.0), _row("GX0002", 46.95)],
        "not an untrustworthy best": [_row("GX0001", 47.0), _row("GX0004", 46.0, trusted=False)],
        "not another track": [_row("GX0001", 47.0), _row("GX0005", 40.0, track="Elsewhere")],
        "the earliest of a tie": [_row("GX0006", 47.0, date="2026-08-30"),
                                  _row("GX0001", 47.0, date="2026-07-19")],
    }
    want = {"the fastest other recording": "GX0003", "never the recording itself": "GX0001",
            "not an untrustworthy best": "GX0001", "not another track": "GX0001",
            "the earliest of a tie": "GX0001"}
    for name, entries in cases.items():
        idx = {"entries": entries}
        moment = library.pb_moment(idx, "Stadium", 46.9, fingerprint_key="GX0002")
        row = library.previous_pb(idx, "Stadium", "GX0002")
        if name == "never the recording itself":
            # its own row already beats the other recordings: no moment, but the row stands
            assert moment is None, moment
        else:
            assert moment["kind"] == "beat" and moment["prior"] == row["best"], (name, moment, row)
        assert row["fingerprint"] == want[name], (name, row)
    assert library.previous_pb({"entries": []}, "Stadium", "GX0002") is None
    assert library.previous_pb({"entries": [_row("GX0002", 47.0)]}, "Stadium", "GX0002") is None
    assert library.previous_pb({"entries": [_row("GX0001", 47.0)]}, None, "GX0002") is None
    print("ok row: the previous PB is the row pb_moment's prior came from, in every shape")


def test_missing_footage_is_said_plainly():
    row = _row("GX0065", 46.912, track="Sandown Park",
               paths=["/Users/me/Desktop/SD_30_08_26/GX010065.MP4"])
    text = library.previous_pb_missing_text(row, row["paths"][0], fmt_time)
    assert text == ("Your previous best at Sandown Park (0:46.912) was recorded on footage that is "
                    "no longer where Pacer saw it: GX010065.MP4 is missing from "
                    "/Users/me/Desktop/SD_30_08_26. If you moved it, load it with Coaching ▸ "
                    "Load reference recording… to compare."), text
    none = library.previous_pb_missing_text(dict(row, paths=[]), None, fmt_time)
    assert none == ("Your previous best at Sandown Park (0:46.912) has no footage on record, so "
                    "there is nothing to compare it with."), none
    print(f"ok missing: {text!r}")


# ------------------------------------------------------------------------------- 3. the card
def _card(on_compare, on_share=lambda: None):
    from PySide6.QtWidgets import QWidget

    from studio.overlays import PBToast
    host = QWidget()
    host.resize(1440, 900)
    host.show()
    toast = PBToast("New personal best!", "Sandown Park — 0:46.808, 0.10 s faster than your "
                    "previous best (0:46.912).", on_progress=lambda: None, on_share=on_share,
                    on_compare=on_compare, parent=host)
    toast.show_for(host)
    _APP.processEvents()
    return host, toast


def test_the_card_makes_the_compare_its_primary_action():
    from PySide6.QtCore import QPoint, QRect
    routed = []
    host, toast = _card(lambda: routed.append("compare"))
    plain_host, plain = _card(None)
    try:
        c, s = toast.compare_btn, toast.share_btn
        assert c is not None and "previous PB" in c.text(), c
        assert (c.objectName(), c.property("variant")) == ("PBToastPrimary", "primary")
        assert (s.objectName(), s.property("variant")) == ("PBToastLink", None), \
            "share stays on the card, as a secondary link"
        # without a compare the card is what it always was: share is the primary
        assert plain.compare_btn is None
        assert (plain.share_btn.objectName(), plain.share_btn.property("variant")) == \
            ("PBToastPrimary", "primary")
        for card in (toast, plain):
            for w in (card.close_btn, card.compare_btn, card.share_btn, card.link_btn):
                if w is None:
                    continue
                assert w.width() >= _MIN_HIT and w.height() >= _MIN_HIT, (w.text(), w.size())
                assert card.rect().contains(QRect(w.mapTo(card, QPoint(0, 0)), w.size())), w
        # A second row, not a wider card: the lap panel it sits over is ~700 px across.
        assert toast.width() <= plain.width() + 8, (toast.width(), plain.width())
        c.click()
        assert routed == ["compare"] and not toast.isVisible(), "the click routes, then dismisses"
    finally:
        host.close()
        plain_host.close()
    print(f"ok card: compare is the primary, share a link; {toast.width()} px wide as before")


# ------------------------------------------------------------------------ 4. the debrief line
def test_the_debrief_line_carries_the_compare_within_its_budget():
    from studio.coaching_panel import DEBRIEF_COMPARE, DebriefBlock
    line = "New personal best at Stadium: 0:46.900, 0.10 s faster than your previous best (0:47.000)."
    block = DebriefBlock()
    asked = []
    block.compare_requested.connect(lambda: asked.append(True))
    block.resize(1400, 300)
    block.show()
    block.set_lines(line, "Esc returns to your usual layout.", compare=True)
    used = block.fit_into(1400, 300)
    _APP.processEvents()
    btn = block.compare_btn
    assert btn.text() == DEBRIEF_COMPARE and btn.isVisible(), "offered beside a new PB's line"
    assert used == block._needed_px(1400, 2) > 0
    # Narrow, the line wraps into what the button leaves it, and the budget knows.
    narrow_with = block._needed_px(420, 1)
    block.set_lines(line, "Esc returns to your usual layout.", compare=False)
    assert not btn.isVisible() and narrow_with > block._needed_px(420, 1), \
        "the button's width must come out of the line's"
    # The button goes with the line: a budget with room for neither hides both.
    block.set_lines(line, "Esc returns to your usual layout.", compare=True)
    assert block.fit_into(1400, 5) == 0 and block.isHidden() and btn.isHidden()
    block.fit_into(1400, 300)
    btn.click()
    assert asked == [True]
    block.close()
    print("ok debrief line: the button sits beside the PB line, inside the page's budget")


# ------------------------------------------------------------------------------ 5. the journey
_SLOW_SEED = 7   # true best 45.099 s at the built-in line; the demo seed's is 44.956 s


def _recordings(folder: str) -> tuple[str, str]:
    """Two DIFFERENT recordings of the built-in demo circuit (verified timing, one lap length): a
    slower one, then a faster one — so the second open is a new personal best over the first.
    Different seeds, so the reference guard's identity check sees two recordings, not one twice."""
    from studio.dev import make_demo as md
    from studio.dev import synth_gopro as sg
    out = []
    for n, seed in (("9001", _SLOW_SEED), ("9002", md.DEMO_SEED)):
        rec = sg.generate(os.path.join(folder, f"gen{n}"), seed, md.DEMO_LAPS, chapters=1,
                          origin=md.DEMO_ORIGIN)
        os.makedirs(os.path.join(folder, n))
        out.append(os.path.join(folder, n, f"GX01{n}.MP4"))
        os.replace(rec.paths[0], out[-1])
    return out[0], out[1]


def _wait(predicate, seconds=60.0) -> bool:
    end = time.time() + seconds
    while time.time() < end and not predicate():
        _APP.processEvents()
        time.sleep(0.01)
    return predicate()


def _park_playhead_off_the_best_lap(win) -> int:
    """Put the playhead in the middle of a lap that is NOT the best, the way the view's own poster
    seek places it (the seek, then both halves of the playback state), so a compare that follows
    the playhead rather than opening on the best lap is caught."""
    s, v = win.session, win.view
    other = next(i for i in s.valid_lap_ids() if i != s.best_lap_id())
    t0, t1 = s.lap_window(other)
    mid = (t0 + t1) / 2
    v.video.seek(mid)
    v._playback.latest_t = v._playback.applied_t = mid
    v._apply_position(mid)
    _settle_for(0.2)
    assert s.lap_at_time(v._playback.applied_t) == other, (v._playback.applied_t, t0, t1)
    return other


def _settle_for(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        _APP.processEvents()
        time.sleep(0.01)


def _assert_compare_on_the_two_best_laps(win, slow_path, standing, label):
    s, v = win.session, win.view
    ok = _wait(lambda: v.compare.active and v.compare.cross)
    assert ok, f"{label}: no cross compare opened"
    ref = s.reference_session()
    assert v.compare.lap_a == s.best_lap_id(), (label, v.compare.lap_a, s.best_lap_id())
    assert v.compare.lap_b == ref.best_lap_id() == s.reference_lap_id(), label
    # The lap in pane B is the lap the PB line quoted as the previous best.
    assert abs(ref.lap_time(v.compare.lap_b) - standing["prior"]) < 1e-6, \
        (label, ref.lap_time(v.compare.lap_b), standing["prior"])
    assert os.path.realpath(ref.video_path) == os.path.realpath(slow_path), ref.video_path
    # The compare plays in the video panel: no panel may still be maximized over it.
    assert v._maximized_panel is None and not v.is_debrief(), label
    win.exports.sync_menu()      # what opening the File menu does
    assert win._export_compare_action.isEnabled(), f"{label}: the export is not one click away"


def test_the_journey_on_the_real_window():
    from PySide6.QtWidgets import QMessageBox

    from studio.app import StudioWindow
    from test_debrief_landing import _fresh_app_support, _open

    shown = []
    real_info = QMessageBox.information
    QMessageBox.information = staticmethod(lambda *a, **_k: shown.append(a[1:3]))
    with tempfile.TemporaryDirectory(prefix="pb_compare_") as folder, _fresh_app_support():
        slow, fast = _recordings(folder)
        win = StudioWindow([])
        win.resize(1440, 900)
        win.show()
        try:
            _open(win, slow)
            panel = win.view.opportunities
            assert win.view.is_debrief() and panel.debrief_block.compare_btn.isHidden(), \
                "a first session has no previous PB to compare with"

            _open(win, fast)
            view, panel = win.view, win.view.opportunities
            standing = win.library_ctl.pb_standing
            assert standing["kind"] == "beat", standing
            assert view.is_debrief(), "a first open lands on its debrief"
            assert "New personal best" in panel.debrief_block.headline.text()
            assert win.library_ctl.previous_pb["fingerprint"] == "GX9001"
            btn = panel.debrief_block.compare_btn
            assert btn.isVisible(), "the debrief's PB line offers no compare"

            # (a) the debrief's button, with the playhead parked on another lap
            _park_playhead_off_the_best_lap(win)
            btn.click()
            _assert_compare_on_the_two_best_laps(win, slow, standing, "debrief")
            assert not shown, shown

            # (b) the card, which is what a PB gets when there is no debrief to carry it
            win._clear_reference()
            assert not win.view.compare.active and not win.session.has_reference()
            win.library_ctl.show_pb_moment(standing)
            assert _wait(lambda: win._pb_toast is not None and win._pb_toast.isVisible(), 3)
            card = win._pb_toast
            assert card.compare_btn.objectName() == "PBToastPrimary", "compare is the primary"
            _park_playhead_off_the_best_lap(win)
            card.compare_btn.click()
            _assert_compare_on_the_two_best_laps(win, slow, standing, "card")
            assert not shown, shown

            # (c) the previous PB's footage has moved since: said plainly, nothing loaded
            win._clear_reference()
            moved = os.path.join(folder, "moved")
            os.replace(os.path.dirname(slow), moved)
            token = win._ref_load_token
            win._compare_with_previous_pb()
            assert len(shown) == 1, shown
            title, text = shown[0]
            assert title.endswith("previous PB not found"), title
            assert "GX019001.MP4 is missing from" in text and os.path.dirname(slow) in text, text
            assert win._ref_load_token == token, "a load was started for a file that is gone"
            assert not win.session.has_reference()
        finally:
            QMessageBox.information = real_info
            win.close()
            win.deleteLater()
            _APP.processEvents()
    print("ok journey: the debrief's button and the card each opened the compare on the two best "
          "laps (A = this best, B = the previous PB's lap), the export enabled; a moved file said so")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} PB COMPARE TESTS PASSED")
