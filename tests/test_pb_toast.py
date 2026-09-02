"""PB-toast hit targets + dismiss clock (fix/qa-toast-and-trackdb, QA sweep L10-07).

The "new personal best!" card (studio.overlays.PBToast) deletes itself after AUTO_DISMISS_MS, so
it is the one surface in the app where a small control and a running clock compound: two of its
three controls were sized by their QSS text padding alone (the ✕ at 20x19, the progression link
133x19) against the 24x24 pointer-target floor the QA sweep measures every clickable by, while the
sibling share button cleared it at 130x30 only because it carries variant="primary".

Pinned here:
  * all three controls clear MIN_HIT_PX in BOTH shapes of the card (with and without the share
    action, which is created only when its callback is injected) — measured on the real widget
    geometry after a real show + layout, with the real theme QSS supplying the padding;
  * the auto-dismiss HOLDS while the pointer is on the card and restarts on leave, so the card
    cannot vanish out from under a click;
  * the floor is additive: the wording, the callback routing and the dismiss-on-click behaviour
    are unchanged, and the flat buttons still take their focus cue (colour + underline, which the
    theme paints without touching geometry).

Real widgets on the REAL theme (the QSS decides every pixel measured here), so run offscreen.
No pacer, no telemetry file.

Run: python tests/test_pb_toast.py
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect  # noqa: E402
from PySide6.QtGui import QEnterEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from studio import theme  # noqa: E402
from studio.overlays import PBToast  # noqa: E402

_APP = QApplication.instance() or QApplication([])
theme.apply_theme(_APP)  # the toast's sizes come from the QSS padding — measure the real one

# The QA sweep's pointer-target floor, spelled out here rather than read off PBToast so the
# measurement is independent of the code under test (on main these assertions fail with the
# measured 20x19 / 133x19, not with an AttributeError).
_MIN_HIT = 24

_TITLE = "New personal best at Daytona MK!"
_BODY = "1:02.418 — 0.317 s faster than your previous best (12 Aug 2026)."


def _shown_toast(on_share=None):
    """A real PBToast laid out over a real 1440x900 host, positioned by its own show_for()."""
    host = QWidget()
    host.resize(1440, 900)
    host.show()
    toast = PBToast(_TITLE, _BODY, on_progress=lambda: None, on_share=on_share, parent=host)
    toast.show_for(host)
    _APP.processEvents()
    return host, toast


def _controls(toast):
    """(name, widget) for every button on the card — the set qa.interactives() measures."""
    return [(w.objectName(), w) for w in (toast.close_btn, toast.share_btn, toast.link_btn)
            if w is not None]


def test_every_toast_control_clears_the_hit_floor():
    """The 24x24 pointer-target floor, on the card that deletes itself: ✕ was 20x19 and
    "See your progress →" 133x19 (both under it) while the primary share button passed at 130x30 —
    an accident of ONE button's variant. All three now clear it in both shapes of the card."""
    for label, share in (("with share", lambda: None), ("without share", None)):
        host, toast = _shown_toast(on_share=share)
        for name, w in _controls(toast):
            assert w.width() >= _MIN_HIT and w.height() >= _MIN_HIT, (
                f"{label}: {name} is {w.width()}x{w.height()}, under {_MIN_HIT}x{_MIN_HIT}")
        host.close()
    assert PBToast.MIN_HIT_PX == _MIN_HIT, "the widget's own floor must be the 24px rule"
    print("test_every_toast_control_clears_the_hit_floor OK")


def test_toast_controls_are_inside_the_card_they_grew():
    """The floor must not push a control off its own card: every hit rect stays inside the toast,
    which sizes itself to its layout (show_for adjustSize()s before positioning)."""
    host, toast = _shown_toast(on_share=lambda: None)
    card = toast.rect()
    for name, w in _controls(toast):
        r = QRect(w.mapTo(toast, QPoint(0, 0)), w.size())
        assert card.contains(r), f"{name} at {r} is outside the {card} card"
    host.close()
    print("test_toast_controls_are_inside_the_card_they_grew OK")


def test_auto_dismiss_holds_while_the_pointer_is_on_the_card():
    """The 6 s clock pauses on enter and restarts (in full) on leave — a celebration you must
    click before it vanishes must not vanish while you are aiming at it."""
    host, toast = _shown_toast(on_share=lambda: None)
    assert toast._timer.isActive(), "show_for must arm the auto-dismiss"
    QApplication.sendEvent(
        toast, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    assert not toast._timer.isActive(), "the pointer being on the card must hold the clock"
    QApplication.sendEvent(toast, QEvent(QEvent.Leave))
    assert toast._timer.isActive(), "leaving must restart the countdown"
    assert toast._timer.interval() == PBToast.AUTO_DISMISS_MS, "the FULL clock, not a remainder"
    # A dismissed card does not re-arm itself when the pointer leaves the hidden widget.
    toast.dismiss()
    QApplication.sendEvent(toast, QEvent(QEvent.Leave))
    assert not toast._timer.isActive()
    host.close()
    print("test_auto_dismiss_holds_while_the_pointer_is_on_the_card OK")


def test_toast_wording_and_routing_survive_the_floor():
    """Additive only: both actions still carry their words + tooltips and still route to their
    injected callbacks, and the ✕ still dismisses."""
    routed = []
    host = QWidget()
    host.resize(1440, 900)
    host.show()
    toast = PBToast(_TITLE, _BODY, on_progress=lambda: routed.append("progress"),
                    on_share=lambda: routed.append("share"), parent=host)
    toast.show_for(host)
    _APP.processEvents()
    assert "progress" in toast.link_btn.text().lower()
    assert "share" in toast.share_btn.text().lower()
    assert toast.close_btn.toolTip() and toast.link_btn.toolTip() and toast.share_btn.toolTip()
    toast.share_btn.click()
    assert routed == ["share"] and not toast.isVisible()

    host2, toast2 = _shown_toast(on_share=None)
    assert toast2.share_btn is None, "no share callback ⇒ no share button (unchanged)"
    toast2.close_btn.click()
    assert not toast2.isVisible() and not toast2._timer.isActive()
    host.close()
    host2.close()
    print("test_toast_wording_and_routing_survive_the_floor OK")


if __name__ == "__main__":
    test_every_toast_control_clears_the_hit_floor()
    test_toast_controls_are_inside_the_card_they_grew()
    test_auto_dismiss_holds_while_the_pointer_is_on_the_card()
    test_toast_wording_and_routing_survive_the_floor()
    print("\nAll PB-toast hit-target tests passed.")
