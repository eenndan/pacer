"""Unit tests for the shareable lap card (image) — the one-tap viral output.

Two layers, tested separately (mirroring the module split):
  * share_card.card_data — the PURE data function off Session accessors (no Qt for the numbers):
    the expected fields (best lap, Δ-to-ideal, top opportunity, track, date, unit), the km/h↔mph
    unit carried into the reason sentence, and the HONESTY verdict — a provisional/no-valid-lap
    session is `blocked` (no card); a data-quality-degraded session is `stamp`ed, not blocked.
  * share_card.render_card — the Qt composition: renders to a non-empty QImage of the expected
    CARD_W×CARD_H size, on both palettes, with + without a map thumbnail.
  * The app wiring (offscreen, DI): File ▸ Export ▸ "Lap card (image)…" saves the PNG through a
    monkeypatched QFileDialog; "Copy lap card" puts an image on a monkeypatched clipboard; the
    Export-menu sync greys both out on a blocked session; and the PBToast "Share your PB →"
    button routes to its injected on_share callback.

Runs offscreen (QImage/QPainter need a QApplication). No telemetry file, no pacer Laps — the
Session surface is duck-typed exactly as far as card_data reaches. Run: python tests/test_share_card.py
"""
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import coaching, share_card, theme  # noqa: E402

theme.register_fonts()


# --------------------------------------------------------------------- fakes
def _quality(degraded=False):
    return SimpleNamespace(degraded=degraded)


def _apex_opp(cid=4, direction=-1, time_lost=0.28, deficit=4.0):
    """A real coaching.Opportunity whose dominant reason is an apex-speed deficit (so
    reason_sentence renders "carry more apex speed (−N unit)")."""
    r = coaching.Reason(kind=coaching.REASON_APEX, contribution=0.2, apex_speed_deficit=deficit,
                        brake_extra_s=0.0, coast_extra_s=0.0, sigma=0.1)
    return coaching.Opportunity(cid=cid, direction=direction, time_lost=time_lost,
                                entry_dist=100.0, reason=r)


class FakeSession:
    """The minimal Session surface share_card.card_data reaches through — duck-typed, no pacer."""

    def __init__(self, *, track="Daytona MK", verified=True, degraded=False, best_id=3,
                 best_time=68.42, ideal=67.90, date="2026-06-29", opps=None):
        self.track_name = track
        self.timing_verified = verified
        self.timing_quality = _quality(degraded)
        self._best_id = best_id
        self._best_time = best_time
        self._ideal = ideal
        self._date = date
        self._opps = opps if opps is not None else coaching.Opportunities(
            enough=True, n_laps=5, median_lap_id=3, rows=[_apex_opp()])

    def best_lap_id(self):
        return self._best_id

    def lap_time(self, lap_id):
        return self._best_time

    def ideal_total(self):
        return self._ideal

    def session_date(self):
        return self._date

    def coaching_opportunities(self):
        return self._opps


# --------------------------------------------------------------------- data-layer tests
def test_card_data_carries_the_expected_fields():
    """card_data reads the headline display values off Session accessors: track, date, best lap
    (formatted), the Δ-to-ideal gap (best − ideal ≥ 0), the unit, and the #1 opportunity as a
    display row (corner label + glyph, time lost, reason sentence)."""
    d = share_card.card_data(FakeSession(), unit="kmh")
    assert d.track == "Daytona MK"
    assert d.date == "2026-06-29"
    assert d.best_time == "1:08.420", d.best_time
    assert d.best_lap_id == 3
    assert abs(d.delta_to_ideal_s - (68.42 - 67.90)) < 1e-6, d.delta_to_ideal_s
    assert d.unit == "kmh"
    assert not d.blocked and d.stamp == ""
    assert d.top_opp is not None
    assert d.top_opp.corner_label.startswith("C4"), d.top_opp.corner_label
    assert abs(d.top_opp.time_lost_s - 0.28) < 1e-6
    assert "apex speed" in d.top_opp.reason and "km/h" in d.top_opp.reason
    print("test_card_data_carries_the_expected_fields OK")


def test_card_data_unit_flips_reason_to_mph():
    """The apex-speed deficit in the reason sentence honours the active km/h↔mph unit (the shared
    coaching.reason_sentence conversion), and the unit id is carried on the card data."""
    d = share_card.card_data(FakeSession(), unit="mph")
    assert d.unit == "mph"
    assert "mph" in d.top_opp.reason and "km/h" not in d.top_opp.reason, d.top_opp.reason
    # 4.0 km/h ≈ 2.49 mph — the value is converted, not just relabelled.
    assert "2.5 mph" in d.top_opp.reason, d.top_opp.reason
    print("test_card_data_unit_flips_reason_to_mph OK")


def test_card_data_unknown_track_and_no_ideal():
    """A session with no detected track name shows 'Unknown track'; with no ideal buildable the
    Δ-to-ideal is None (the card just omits that line). Track name None but user-confirmed timing
    still counts verified (not blocked)."""
    s = FakeSession(track=None, ideal=None)
    s.timing_verified = True  # user-confirmed start line on an unknown track
    d = share_card.card_data(s, unit="kmh")
    assert d.track == "Unknown track"
    assert d.delta_to_ideal_s is None
    assert not d.blocked
    print("test_card_data_unknown_track_and_no_ideal OK")


def test_card_data_blocks_provisional_and_no_lap():
    """HONESTY: a provisional (unverified start line) session yields a blocked card (an unverified
    lap time is never a brag), and so does a session with no valid best lap."""
    prov = share_card.card_data(FakeSession(track=None, verified=False), unit="kmh")
    assert prov.blocked, "provisional timing must block the shareable card"
    no_lap = share_card.card_data(FakeSession(best_id=None), unit="kmh")
    assert no_lap.blocked and no_lap.best_time == "—"
    print("test_card_data_blocks_provisional_and_no_lap OK")


def test_card_data_stamps_degraded_timing():
    """A data-quality-degraded (media-clock / low-GPS) session still renders a card, but STAMPED so
    the number is shown honestly as estimated — never blocked, never presented as exact."""
    d = share_card.card_data(FakeSession(degraded=True), unit="kmh")
    assert not d.blocked, "a degraded but verified session should stamp, not block"
    assert d.stamp == "estimated timing", d.stamp
    print("test_card_data_stamps_degraded_timing OK")


def test_card_data_no_opportunity_when_too_few_laps():
    """Under MIN_LAPS clean laps the coaching model has no rows → the card's top opportunity is
    None (the card shows a gentle 'drive more laps' line instead of a fabricated tip)."""
    few = coaching.Opportunities(enough=False, n_laps=1, median_lap_id=None, rows=[])
    d = share_card.card_data(FakeSession(opps=few), unit="kmh")
    assert d.top_opp is None
    assert not d.blocked  # a verified best lap is still shareable without a coaching tip
    print("test_card_data_no_opportunity_when_too_few_laps OK")


def test_card_data_survives_coaching_error():
    """A hiccup in coaching_opportunities degrades to 'no opportunity' — the card is never broken
    by the coaching layer (the top opportunity is optional)."""
    class Boom(FakeSession):
        def coaching_opportunities(self):
            raise RuntimeError("coaching blew up")
    d = share_card.card_data(Boom(), unit="kmh")
    assert d.top_opp is None and not d.blocked
    print("test_card_data_survives_coaching_error OK")


# --------------------------------------------------------------------- render-layer tests
def _one_px_png() -> bytes:
    """A tiny real PNG to stand in for the grabbed MapView thumbnail."""
    img = QImage(8, 8, QImage.Format_ARGB32)
    img.fill(0xFF334455)
    return share_card.card_to_png(img)


def test_render_card_is_a_nonempty_image_of_the_right_size():
    """render_card produces a non-null QImage of exactly CARD_W×CARD_H, and card_to_png encodes it
    to non-empty PNG bytes — with a map thumbnail composited in."""
    d = share_card.card_data(FakeSession(), unit="kmh")
    img = share_card.render_card(d, _one_px_png(), palette=theme.PALETTE_STANDARD)
    assert not img.isNull()
    assert img.width() == share_card.CARD_W and img.height() == share_card.CARD_H
    png = share_card.card_to_png(img)
    assert len(png) > 1000 and png[:8] == b"\x89PNG\r\n\x1a\n", len(png)
    print("test_render_card_is_a_nonempty_image_of_the_right_size OK")


def test_render_card_without_thumbnail_and_on_both_palettes():
    """The card renders cleanly with no map thumbnail (map_png=None) and on the colour-blind
    palette (which recolours the semantic hues) — and restores the previously-active palette."""
    theme.set_palette(theme.PALETTE_STANDARD)
    d = share_card.card_data(FakeSession(), unit="kmh")
    img = share_card.render_card(d, None, palette=theme.PALETTE_COLORBLIND)
    assert not img.isNull()
    assert img.width() == share_card.CARD_W and img.height() == share_card.CARD_H
    # render_card must restore the caller's active palette (it only swaps for the render).
    assert theme.active_palette() == theme.PALETTE_STANDARD, theme.active_palette()
    print("test_render_card_without_thumbnail_and_on_both_palettes OK")


def test_render_card_stamped_and_degraded_still_renders():
    """A stamped (degraded) card still renders to a valid image (the stamp is drawn, not blocked)."""
    d = share_card.card_data(FakeSession(degraded=True), unit="kmh")
    assert d.stamp == "estimated timing"
    img = share_card.render_card(d, None, palette=theme.PALETTE_STANDARD)
    assert not img.isNull() and img.width() == share_card.CARD_W
    print("test_render_card_stamped_and_degraded_still_renders OK")


# --------------------------------------------------------------------- app-wiring tests (DI)
def _bare_window(session):
    """A StudioWindow with just enough state for the share-card actions (no heavy __init__): the
    session, a view whose .map is a real grab-able QWidget, the display unit, and a captured
    statusbar. QMainWindow.__init__ so statusBar()/QMessageBox parenting work."""
    from PySide6.QtWidgets import QMainWindow

    from studio.app import StudioWindow
    w = StudioWindow.__new__(StudioWindow)
    QMainWindow.__init__(w)
    w.session = session
    w._speed_unit = "kmh"
    w._paths = ["/Users/x/Desktop/D24/GX010060.MP4"]
    map_widget = QWidget()
    map_widget.resize(80, 60)
    w.view = SimpleNamespace(map=map_widget)
    return w


def test_export_share_card_saves_png_through_the_dialog():
    """File ▸ Export ▸ 'Lap card (image)…' renders the card and writes a real PNG to the path the
    (monkeypatched) save dialog returns."""
    from PySide6.QtWidgets import QFileDialog
    w = _bare_window(FakeSession())
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "my_lap_card.png")
        orig = QFileDialog.getSaveFileName
        QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (out, "PNG images (*.png)"))
        try:
            w._export_share_card()
        finally:
            QFileDialog.getSaveFileName = orig
        assert os.path.exists(out), "the lap card PNG was not written"
        with open(out, "rb") as f:
            head = f.read(8)
        assert head == b"\x89PNG\r\n\x1a\n", head
    print("test_export_share_card_saves_png_through_the_dialog OK")


def test_export_share_card_cancel_writes_nothing():
    """Cancelling the save dialog (empty path) writes no file."""
    from PySide6.QtWidgets import QFileDialog
    w = _bare_window(FakeSession())
    written = []
    w._save_card_png = lambda img, path: written.append(path)  # spy — must not be called
    orig = QFileDialog.getSaveFileName
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
    try:
        w._export_share_card()
    finally:
        QFileDialog.getSaveFileName = orig
    assert written == [], "a cancelled save must write nothing"
    print("test_export_share_card_cancel_writes_nothing OK")


def test_copy_share_card_sets_clipboard_image(monkeypatched=None):
    """'Copy lap card' renders the card and puts an image on the clipboard (monkeypatched), so a
    user can paste it straight into a chat."""
    from studio import app as app_mod
    w = _bare_window(FakeSession())
    captured = {}

    class FakeClip:
        def setImage(self, image):
            captured["image"] = image

    orig = app_mod.QApplication.clipboard
    app_mod.QApplication.clipboard = staticmethod(lambda: FakeClip())
    try:
        w._copy_share_card()
    finally:
        app_mod.QApplication.clipboard = orig
    assert "image" in captured, "no image was placed on the clipboard"
    assert isinstance(captured["image"], QImage)
    assert captured["image"].width() == share_card.CARD_W
    print("test_copy_share_card_sets_clipboard_image OK")


def test_blocked_session_builds_no_card_and_greys_actions():
    """A blocked (provisional) session: _build_share_card returns None, the save/copy actions do
    nothing, and _share_card_blocked reports True so _sync_export_menu greys them out."""
    w = _bare_window(FakeSession(track=None, verified=False))
    assert w._build_share_card() is None
    assert w._share_card_blocked() is True
    # copy on a blocked session must not touch the clipboard
    from studio import app as app_mod
    touched = []
    orig = app_mod.QApplication.clipboard
    app_mod.QApplication.clipboard = staticmethod(
        lambda: SimpleNamespace(setImage=lambda im: touched.append(im)))
    try:
        w._copy_share_card()
    finally:
        app_mod.QApplication.clipboard = orig
    assert touched == [], "a blocked session must not copy a card"
    print("test_blocked_session_builds_no_card_and_greys_actions OK")


def test_pb_toast_share_button_routes_to_on_share():
    """The PBToast's 'Share your PB →' primary button routes to its injected on_share callback
    (the one-tap card save), and 'See your progress →' still routes to on_progress."""
    from studio.overlays import PBToast
    shared, progressed = [], []
    toast = PBToast("New personal best! 🏁", "MK — 1:08.42, 0.31 s faster.",
                    on_progress=lambda: progressed.append(True),
                    on_share=lambda: shared.append(True))
    assert toast.share_btn is not None
    assert "share" in toast.share_btn.text().lower()
    toast.share_btn.click()
    assert shared == [True], "the share button must route to on_share"
    # a fresh toast for the progress link (the first click dismissed the toast)
    toast2 = PBToast("New personal best! 🏁", "MK — 1:08.42.",
                     on_progress=lambda: progressed.append(True),
                     on_share=lambda: shared.append(True))
    toast2.link_btn.click()
    assert progressed == [True], "the progress link must still route to on_progress"
    print("test_pb_toast_share_button_routes_to_on_share OK")


def test_pb_toast_hides_share_button_when_no_callback():
    """With no on_share callback (a session that can't make a card), the toast shows no share
    button — only the progression link (backwards-compatible with the pre-share toast)."""
    from studio.overlays import PBToast
    toast = PBToast("New personal best!", "MK.", on_progress=lambda: None, on_share=None)
    assert toast.share_btn is None
    assert toast.link_btn is not None
    print("test_pb_toast_hides_share_button_when_no_callback OK")


if __name__ == "__main__":
    test_card_data_carries_the_expected_fields()
    test_card_data_unit_flips_reason_to_mph()
    test_card_data_unknown_track_and_no_ideal()
    test_card_data_blocks_provisional_and_no_lap()
    test_card_data_stamps_degraded_timing()
    test_card_data_no_opportunity_when_too_few_laps()
    test_card_data_survives_coaching_error()
    test_render_card_is_a_nonempty_image_of_the_right_size()
    test_render_card_without_thumbnail_and_on_both_palettes()
    test_render_card_stamped_and_degraded_still_renders()
    test_export_share_card_saves_png_through_the_dialog()
    test_export_share_card_cancel_writes_nothing()
    test_copy_share_card_sets_clipboard_image()
    test_blocked_session_builds_no_card_and_greys_actions()
    test_pb_toast_share_button_routes_to_on_share()
    test_pb_toast_hides_share_button_when_no_callback()
    print("\nAll shareable-lap-card tests passed.")
