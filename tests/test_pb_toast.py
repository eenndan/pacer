"""PB-toast hit targets + dismiss clock + WHERE THE CARD LANDS (QA sweep L10-07 · plan §C).

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

AND WHERE IT LANDS, which is the Phase-5 half. The card was placed top-centre of the WINDOW at a
fixed 16 px from its top edge — a rule from when the window was one picture rather than four
panels. Measured on the shipped app at 1440x900 that put it 36 px into the MAP panel's header
(over the word "MAP"), across all 32 px of the map's toolbar, and 20 px into the track; with the
lap panel maximized it sat on THAT header, cutting the "Dist (m)" column label in half. Every panel
now DECLARES its header height (theme.PANEL_HDR_H), so an overlay on one is not a near miss.

Pinned here: the card never intersects a PanelHeader or a PanelToolbar, at both shipped window
sizes, in the default grid AND with each of the four panels maximized; it sits inside the lap
panel's body when that panel is on screen; and it PAINTS ITS OWN CARD — the theme has drawn
#PBToast a background, an accent border and a radius since the moment shipped, and a bare QWidget
ignores all three without WA_StyledBackground.

Real widgets on the REAL theme (the QSS decides every pixel measured here), so run offscreen.
No pacer, no telemetry file.

Run: python tests/test_pb_toast.py
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The anchoring tests build the REAL CentralView; PACER_NO_MEDIA must be set before studio imports
# (PlayerPane reads it once, at construction).
os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()  # the toast's sizes come from the QSS padding — measure the SHIPPED font+theme

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QEnterEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from studio import theme  # noqa: E402
from studio.overlays import PBToast  # noqa: E402

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


# ======================================================================== where the card lands
def _hex(px):
    return f"#{px & 0xFFFFFF:06X}"


def test_the_card_paints_the_card_the_theme_draws_it():
    """A #PBToast rule with nobody to paint it. theme.py gives this widget `background-color:
    surface_active`, a `BORDER_PX solid accent` border and a RADIUS_M corner — and Qt runs a
    stylesheet's BOX painting for a plain QWidget subclass only when WA_StyledBackground is set
    (QLabel/QPushButton draw their own box, which is why every other #Name rule in this app just
    works). So the card was transparent: it read as a card only while it sat over the map's empty
    top-left corner, which happens to be flat surface colour. Over the lap grid it was celebration
    text interleaved with lap times.

    Measured on the widget's own render, not on the attribute alone — the attribute is the
    mechanism, the pixels are the claim."""
    host, toast = _shown_toast(on_share=lambda: None)
    assert toast.testAttribute(Qt.WA_StyledBackground), (
        "without WA_StyledBackground the #PBToast background/border/radius are never painted")
    img = toast.grab().toImage()
    mid = img.pixel(toast.width() // 2, toast.height() // 2)
    # The border is BORDER_PX at a RADIUS_M corner, so sample it mid-edge where the corner arc is
    # not in the way. Row 0 of a styled QWidget IS the border.
    edge = img.pixel(toast.width() // 2, 0)
    host.close()
    assert _hex(mid) == theme.C.surface_active.upper(), (
        f"the card's interior is {_hex(mid)}, not the themed {theme.C.surface_active}")
    assert _hex(edge) == theme.C.accent.upper(), (
        f"the card's border is {_hex(edge)}, not the themed accent {theme.C.accent}")
    print(f"test_the_card_paints_the_card_the_theme_draws_it OK "
          f"(fill {_hex(mid)}, border {_hex(edge)})")


def _window_with_toast(size, maximize=None):
    """A REAL StudioWindow + CentralView at `size` with the PB toast up, optionally with one panel
    maximized. Returns (win, view, toast)."""
    from test_central_view_realqt import _studiowindow_with_view

    win, view = _studiowindow_with_view()
    win.resize(*size)
    win.show()
    for _ in range(10):
        _APP.processEvents()
    if maximize is not None:
        view._toggle_panel_maximized(getattr(view, maximize))
        for _ in range(8):
            _APP.processEvents()
    toast = PBToast(_TITLE, _BODY, on_progress=lambda: None, on_share=lambda: None, parent=win)
    toast.show_for(win)
    for _ in range(6):
        _APP.processEvents()
    return win, view, toast


def _chrome_rects(win, view):
    """Every declared panel-chrome row (PanelHeader / PanelToolbar) that is ON SCREEN, in the
    window's coordinates. A maximized panel collapses its siblings' splitter SECTION rather than
    their widgets, so an off-screen header is not a header anyone can collide with."""
    from studio.widgets import PanelHeader, PanelToolbar

    out = []
    for w in [*view.findChildren(PanelHeader), *view.findChildren(PanelToolbar)]:
        if w.isHidden() or w.width() <= 0 or w.height() <= 0:
            continue
        r = QRect(w.mapTo(win, QPoint(0, 0)), w.size()).intersected(win.rect())
        if not r.isEmpty():
            out.append((type(w).__name__, r))
    return out


def test_the_toast_never_lands_on_panel_chrome():
    """§C, the whole point of it: nothing overlays a declared header.

    On main this fails with a 281x36 intersection with the MAP PanelHeader (and 281x32 with the map
    PanelToolbar) at both window sizes, and with the TABLE PanelHeader once the lap panel is
    maximized. Checked in five states per size, because the defect had two of them."""
    for size in ((1440, 900), (1280, 800)):
        for state in (None, "_map_panel", "_table_panel", "_video_panel", "_plots_panel"):
            win, view, toast = _window_with_toast(size, maximize=state)
            card = QRect(toast.mapTo(win, QPoint(0, 0)), toast.size())
            hits = [(kind, r, card.intersected(r)) for kind, r in _chrome_rects(win, view)
                    if not card.intersected(r).isEmpty()]
            assert card.width() > 0 and card.height() > 0, card
            assert win.rect().contains(card), (
                f"{size} {state}: the card at {card} is not inside the {win.rect()} window")
            win.hide()
            assert not hits, (
                f"{size} maximize={state}: the PB toast at {card} overlays panel chrome — "
                + "; ".join(f"{k} {r} by {i.width()}x{i.height()}" for k, r, i in hits))
    print("test_the_toast_never_lands_on_panel_chrome OK (2 sizes x 5 layout states)")


def test_the_toast_sits_in_the_lap_panels_body():
    """WHERE it goes, not just where it does not. The PB moment is a LAP fact with two lap actions
    on it, so it belongs over the grid that holds the ★ session-best row — not over the map, whose
    every pixel is the racing line, the corner markers and the draggable start/finish handles.

    And it must survive that panel being collapsed: maximizing another quadrant drives the lap
    panel's splitter section to 0 while leaving its WIDGETS sized (measured: 280x453 at (-1, -430)),
    so a naive `is it big enough` check would put the card off the top of the window."""
    win, view, toast = _window_with_toast((1440, 900))
    body = QRect(view.table_stack.mapTo(win, QPoint(0, 0)), view.table_stack.size())
    card = QRect(toast.mapTo(win, QPoint(0, 0)), toast.size())
    win.hide()
    assert body.contains(card), f"the card at {card} is not inside the lap panel body {body}"
    # ...at the BOTTOM of it: the top of that body is where the grid's own column headers are.
    assert card.bottom() > body.center().y(), (card, body)

    # A COLLAPSED lap panel must not still be chosen. Maximizing the map drives the lap panel's
    # splitter section to 0 while its widgets keep their size (measured on main: 280x453 sitting at
    # (-1, -430)), so the on-screen check is the one that matters, not the widget's own geometry.
    win2, view2, toast2 = _window_with_toast((1440, 900), maximize="_map_panel")
    stack = QRect(view2.table_stack.mapTo(win2, QPoint(0, 0)), view2.table_stack.size())
    card2 = QRect(toast2.mapTo(win2, QPoint(0, 0)), toast2.size())
    win2.hide()
    assert not win2.rect().intersects(stack) or stack.height() < card2.height(), (
        "this fixture is only meaningful while the lap panel really is off screen", stack)
    assert win2.rect().contains(card2), (
        f"with the map maximized the card landed at {card2}, outside the "
        f"{win2.rect()} window — the collapsed lap panel was still used as the anchor")
    print("test_the_toast_sits_in_the_lap_panels_body OK "
          f"(card {card} in body {body}; collapsed-panel fallback keeps it at {card2})")


if __name__ == "__main__":
    test_every_toast_control_clears_the_hit_floor()
    test_toast_controls_are_inside_the_card_they_grew()
    test_auto_dismiss_holds_while_the_pointer_is_on_the_card()
    test_toast_wording_and_routing_survive_the_floor()
    test_the_card_paints_the_card_the_theme_draws_it()
    test_the_toast_never_lands_on_panel_chrome()
    test_the_toast_sits_in_the_lap_panels_body()
    print("\nAll PB-toast hit-target + anchoring tests passed.")
