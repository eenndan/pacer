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


# --------------------------------------------------------------------- hero Δ-to-ideal copy
def test_hero_delta_line_reads_cleanly_on_both_branches():
    """The Δ-to-ideal hero line reads like a shipped product on BOTH branches. A positive gap keeps
    the "+0.31 s vs your ideal lap" voice; a gap AT the envelope (≈ 0) reads plainly as "level with
    your ideal lap" — NEVER the old doubled "on your ideal lap vs your ideal lap" template bug."""
    pos = share_card.hero_delta_line(0.31)
    assert pos == "+0.31 s vs your ideal lap", pos
    # right on the envelope (and safely inside the even-epsilon): the clean even copy
    even = share_card.hero_delta_line(0.0)
    assert even == "level with your ideal lap", even
    # the garbled doubled label must be gone from the even branch
    assert even.count("vs your ideal lap") == 0
    assert "on your ideal lap vs" not in even
    # a hair below the even-epsilon still reads as level (not a spurious "+0.00 s")
    assert share_card.hero_delta_line(theme.DELTA_EVEN_EPS_S) == "level with your ideal lap"
    # just above the epsilon flips to the positive voice
    just_over = share_card.hero_delta_line(theme.DELTA_EVEN_EPS_S + 0.01)
    assert just_over.startswith("+") and just_over.endswith("vs your ideal lap"), just_over
    print("test_hero_delta_line_reads_cleanly_on_both_branches OK")


def test_even_ideal_card_renders_with_the_clean_copy():
    """A best lap sitting ON the ideal envelope (gap ≈ 0) still renders a valid card, and its hero
    line is the clean even copy — the whole reason for the fix (rendering the real card surfaced
    the garbled string)."""
    d = share_card.card_data(FakeSession(best_time=67.90, ideal=67.90), unit="kmh")
    assert d.delta_to_ideal_s == 0.0, d.delta_to_ideal_s
    assert share_card.hero_delta_line(d.delta_to_ideal_s) == "level with your ideal lap"
    img = share_card.render_card(d, None, palette=theme.PALETTE_STANDARD)
    assert not img.isNull() and img.width() == share_card.CARD_W
    print("test_even_ideal_card_renders_with_the_clean_copy OK")


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


# ---------------------------------------------------------- clean map grab (legend off the card)
def _map_view_session():
    """A bare Session with just enough surface for MapView.__init__ (trace arrays + a start-line
    Seg via the real ``laps.sectors`` shape + the best-lap/reference hooks) — the test_map_ghost
    idiom, trimmed. No pacer, no file. ``start_line``/``sector_lines`` stay the real Session
    properties (they read ``laps.sectors`` through ``Seg.from_pacer``)."""
    import numpy as np

    from studio.session import Session
    s = Session.__new__(Session)
    ang = np.linspace(0.0, 2 * np.pi, 60)
    s.tt = np.linspace(0.0, 6.0, 60)
    s.tx = np.cos(ang) * 50.0
    s.ty = np.sin(ang) * 30.0
    s.tv = np.linspace(40.0, 120.0, 60)
    line = SimpleNamespace(first=SimpleNamespace(x=-60.0, y=0.0),
                           second=SimpleNamespace(x=-40.0, y=0.0))
    s.laps = SimpleNamespace(sectors=SimpleNamespace(start_line=line, sector_lines=[]))
    s._valid_cache = [1]  # one valid lap → the empty-state placeholder stays hidden
    s.reference_overlay_xy = lambda: None
    s.reference_label = lambda: None
    s.best_lap_id = lambda: None
    s.nearest_index = lambda x, y: None
    return s


def test_map_view_grab_clean_hides_the_map_key_legend():
    """MapView.grab_clean() hides the dev 'Map key' legend overlay for the duration of a grab (so it
    never lands on the shareable card) and RESTORES it afterwards — the live map keeps its key. The
    speed rainbow mode (the card's signature visual) is untouched by the clean grab."""
    from studio.map_view import MapView
    mv = MapView(_map_view_session())
    key = mv._map_key
    key.show()
    # isHidden() is the explicit hide flag (isVisible() reads False off-screen because the top-level
    # window isn't shown — the empty-state idiom), so assert on isHidden throughout.
    assert not key.isHidden(), "precondition: the map key is shown on the live map"
    seen = {}
    with mv.grab_clean():
        seen["key_hidden_during_grab"] = key.isHidden()
        # the clean grab must not disturb the speed colouring the card leads with
        seen["rainbow_mode"] = mv._rainbow_mode
    assert seen["key_hidden_during_grab"] is True, "the map key must be hidden during a clean grab"
    assert seen["rainbow_mode"] == "speed", "clean grab must preserve the speed rainbow"
    assert not key.isHidden(), "the map key must be restored after the clean grab"
    print("test_map_view_grab_clean_hides_the_map_key_legend OK")


def test_map_view_grab_clean_hides_the_marker_and_start_line_handles():
    """H2 regression guard: grab_clean() must ALSO hide the app's editing chrome that used to burn
    into the shareable card — the coral video-position ``marker`` and every timing line's segment +
    drag handles (start line here; sectors iterate the same way) — for the duration of the grab, and
    restore each item's prior visibility on exit. Otherwise the amber "+" crosshairs + coral marker
    circle land on the social brag image (pixel-confirmed on the flagship D24 card)."""
    from studio.map_view import MapView
    mv = MapView(_map_view_session())
    # The pyqtgraph plot items that must vanish for a clean grab (marker + start-line line/handles).
    marker = mv.marker
    start = mv._start
    chrome = [marker, start.line, start.h1, start.h2]
    for it in chrome:
        it.setVisible(True)
        assert it.isVisible(), "precondition: the editing chrome is shown on the live map"
    during = {}
    with mv.grab_clean():
        during["marker"] = marker.isVisible()
        during["line"] = start.line.isVisible()
        during["h1"] = start.h1.isVisible()
        during["h2"] = start.h2.isVisible()
    assert during == {"marker": False, "line": False, "h1": False, "h2": False}, during
    # restored on exit (the live map keeps its marker + draggable start line)
    for it in chrome:
        assert it.isVisible(), "the editing chrome must be restored after the clean grab"
    print("test_map_view_grab_clean_hides_the_marker_and_start_line_handles OK")


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


def test_build_card_grabs_the_map_with_the_legend_hidden():
    """_build_share_card grabs the map thumbnail through the map's grab_clean context, so the dev
    'Map key' legend is hidden AT grab time (never on the card) and restored after. The map is
    faked with a legend + a grab_clean context that records the legend's visibility during the
    grab — mirroring the real MapView contract without a full plot build."""
    from contextlib import contextmanager

    class _FakeLegend(QWidget):
        pass

    class _FakeMap(QWidget):
        def __init__(self):
            super().__init__()
            self.resize(80, 60)
            self.legend = _FakeLegend(self)
            self.legend.setVisible(True)
            self.grab_calls = []

        @contextmanager
        def grab_clean(self):
            self.legend.setVisible(False)
            try:
                yield self
            finally:
                self.legend.setVisible(True)

        def grab(self):
            # record the legend's EXPLICIT hide flag at the moment the pixels are taken. isHidden()
            # (not isVisible()) because an off-screen never-shown widget always reads isVisible()
            # False regardless of hide() — isHidden() is True only when hide()/setVisible(False) ran.
            self.grab_calls.append(self.legend.isHidden())
            return super().grab()

    w = _bare_window(FakeSession())
    fake_map = _FakeMap()
    w.view = SimpleNamespace(map=fake_map)
    img = w._build_share_card()
    assert img is not None and img.width() == share_card.CARD_W
    assert fake_map.grab_calls == [True], \
        f"the map key must be hidden during the card grab, saw {fake_map.grab_calls}"
    assert fake_map.legend.isHidden() is False, "the legend must be restored after the grab"
    print("test_build_card_grabs_the_map_with_the_legend_hidden OK")


def test_build_card_falls_back_to_plain_grab_for_a_bare_widget():
    """A map with no grab_clean (a bare QWidget, as older wiring / tests use) still yields a card —
    _grab_clean_map_png falls back to the plain widget→PNG grab, so the card is never lost."""
    w = _bare_window(FakeSession())  # view.map is a plain QWidget (no grab_clean)
    assert not hasattr(w.view.map, "grab_clean")
    img = w._build_share_card()
    assert img is not None and img.width() == share_card.CARD_W
    print("test_build_card_falls_back_to_plain_grab_for_a_bare_widget OK")


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
    test_hero_delta_line_reads_cleanly_on_both_branches()
    test_even_ideal_card_renders_with_the_clean_copy()
    test_render_card_is_a_nonempty_image_of_the_right_size()
    test_render_card_without_thumbnail_and_on_both_palettes()
    test_render_card_stamped_and_degraded_still_renders()
    test_map_view_grab_clean_hides_the_map_key_legend()
    test_map_view_grab_clean_hides_the_marker_and_start_line_handles()
    test_export_share_card_saves_png_through_the_dialog()
    test_export_share_card_cancel_writes_nothing()
    test_copy_share_card_sets_clipboard_image()
    test_blocked_session_builds_no_card_and_greys_actions()
    test_build_card_grabs_the_map_with_the_legend_hidden()
    test_build_card_falls_back_to_plain_grab_for_a_bare_widget()
    test_pb_toast_share_button_routes_to_on_share()
    test_pb_toast_hides_share_button_when_no_callback()
    print("\nAll shareable-lap-card tests passed.")
