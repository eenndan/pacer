"""Regression tests for the QA-sweep export/library findings in studio/app.py (batch B09 + B07).

  * L12-02 (HIGH) — the overlay-MP4 export was the ONE shareable output exempt from the timing-
    trust verdict. Measured on a provisional recording: card_data().blocked True, both lap-card
    actions disabled, "Export overlay video…" still enabled — and it rendered a 40.6 MB / 696-frame
    clip with "LAP 1  0:13.100" burned in and no honesty marker anywhere in the frame. It now reads
    the SAME card_data verdict; it warns rather than refuses (a provisional clip is still useful for
    reviewing your own footage), so the tests assert the QUESTION, its default, and that a Cancel
    stops the export before the options dialog.

  * L1-03 — a recording with zero valid laps still exported: "Lap times (CSV)" wrote a 76-byte
    header-only file and the status bar reported success, in a window whose panels read "No complete
    laps found in this recording." 4 of 7 export actions were enabled, and 3 of 3 disabled actions
    described their feature rather than the reason. All four data exports now gate on has_laps and
    carry a REASON tooltip while off.

  * L12-04 — the report embedded the raw live map grab, so the exported document carried the coral
    video marker and the orange start-line drag handles (436 px from the raw grab, 4298 from the
    card's clean one). It now uses a report-flavoured grab: grab_clean's chrome suppression with the
    "Map key" put BACK (the report's only legend for its own glyphs) minus the "Drag = …" row.

  * L12-07 — the options dialog sold a file-size trade-off in six labels and stated no size at all,
    on a choice measured 3.01x apart. The hint now quantifies both combos.

  * L12-08 — the "remembered" preset was window-instance state (`getattr(self, '_export_res_idx')`),
    so it reset on every relaunch while every other UI choice persisted. Now in prefs.

  * L11-08 — "Reveal in Finder" discarded openUrl()'s bool and said nothing either way, while its
    peer "Back up…" reported both outcomes.

  * PR #153's handoff — track_db refuses to overwrite a different circuit stored under the same
    name; the confirm that turns that refusal into a question lives here.

Fake sessions throughout (the duck-typed surface these entry points reach through), so no pacer, no
telemetry file and no render. Run: QT_QPA_PLATFORM=offscreen python tests/test_export_gates.py
"""
import json
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# The four persistence seams, diverted into one temp tree BEFORE any window exists: these tests
# WRITE prefs (the export preset) and the track DB (save-as-track), so without this they would
# rewrite the user's own preferences and tracks.json.
from studio import library, prefs, sidecar, track_db  # noqa: E402

_SEAMS = tempfile.mkdtemp(prefix="pacer-test-export-gates-")
for _mod, _name in ((prefs, "prefs"), (library, "library"), (track_db, "track_db")):
    _dir = os.path.join(_SEAMS, _name)
    os.makedirs(_dir, exist_ok=True)
    _mod._app_support_dir = (lambda d=_dir: d)
sidecar.sidecar_path = lambda _p, _d=_SEAMS: os.path.join(_d, "test.pacer.json")

from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtGui import QDesktopServices, QImage  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMessageBox,
    QWidget,
)

_APP = QApplication.instance() or QApplication([])

from studio import coaching, data_quality, export_data, export_video  # noqa: E402
from studio.app import StudioWindow  # noqa: E402

# The four File ▸ Export data actions L1-03 is about, by the attribute the window keeps them on.
DATA_EXPORTS = ("_export_laps_action", "_export_channels_action", "_export_report_action",
                "_export_video_action")
CARD_EXPORTS = ("_share_card_action", "_copy_card_action")


class FakeSession:
    """The Session surface the export entry points + share_card.card_data reach through."""

    def __init__(self, *, laps=(0, 1), verified=True, degraded=False, track="Daytona MK",
                 centroid=(52.0, -0.78)):
        self.track_name = track
        self.timing_verified = verified
        self.timing_quality = data_quality.TimingQuality(
            clock="media_fallback" if degraded else "gps9_trueclock", dropped_fraction=0.0)
        self._laps = list(laps)
        self._centroid = centroid

    def adopt_track(self, name):
        """Session.adopt_track — the seam File ▸ Save as track… uses instead of assigning
        track_name, so the name records the lines it certifies. This fake's timing_verified is a
        plain flag, so there is nothing else to model."""
        self.track_name = name

    def valid_lap_ids(self):
        return list(self._laps)

    def best_lap_id(self):
        return self._laps[0] if self._laps else None

    def lap_time(self, _lap_id):
        return 23.231

    def lap_window(self, _lap_id):
        return (0.0, 23.231)

    def ideal_total(self):
        return 22.9

    def session_date(self):
        return "2026-09-01"

    def point_count(self):
        return 4096

    def coaching_opportunities(self):
        return coaching.Opportunities(enough=False, n_laps=len(self._laps), median_lap_id=None,
                                      rows=[])

    def track_location(self):
        return self._centroid, None

    def timing_lines_latlon(self):
        lat, lon = self._centroid
        return [[lat, lon], [lat + 0.001, lon + 0.001]], []


def _window(session=None, *, paths=("/nonexistent/GX010099.MP4",)):
    """A real StudioWindow with the real menu bar (so every gated QAction and its tooltip is the
    production one) but no load: the welcome screen, then the fake session dropped in. `session=None`
    leaves the window session-less."""
    win = StudioWindow([])
    win.resize(1200, 800)
    if session is not None:
        win.session = session
        win._paths = list(paths)
    return win


# ============================================================ L1-03 — the has-laps gate
def test_a_zero_lap_recording_disables_every_data_export_with_a_reason():
    """No complete laps ⇒ nothing to write. All four data exports (and both card actions) go off,
    and each one's tooltip states the REASON, not the feature. On main all four stayed enabled."""
    win = _window(FakeSession(laps=()))
    win._sync_export_menu()

    for name in DATA_EXPORTS + CARD_EXPORTS:
        action = getattr(win, name)
        assert not action.isEnabled(), f"{name} ({action.text()!r}) is offered with no valid lap"
        tip = action.toolTip()
        assert tip == StudioWindow._NO_LAPS_REASON, f"{name} tooltip is not the reason: {tip!r}"
        assert "No complete laps" in tip and "start/finish line" in tip, tip

    # And the feature description comes BACK with the laps — the reason must not be sticky.
    win.session = FakeSession()
    win._sync_export_menu()
    for name in DATA_EXPORTS + CARD_EXPORTS:
        action = getattr(win, name)
        assert action.isEnabled(), f"{name} stayed disabled on a session with laps"
        assert action.toolTip() == action.property("featureTip"), name
        assert "No complete laps" not in action.toolTip(), name
    win.hide()
    print("test_a_zero_lap_recording_disables_every_data_export_with_a_reason OK")


def test_a_zero_lap_export_writes_nothing_and_says_why():
    """The click-time backstop: triggering the action anyway must not produce a header-only file
    with a success message. On main this wrote 76 bytes and reported 'exported save.out'."""
    win = _window(FakeSession(laps=()))
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "laps.csv")
        orig = QFileDialog.getSaveFileName
        QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (out, ""))
        try:
            win._export_laps_csv()
            win._export_report()
        finally:
            QFileDialog.getSaveFileName = orig
        assert not os.path.exists(out), "a 0-lap session still wrote an export file"
    message = win.statusBar().currentMessage()
    assert "no complete laps" in message.lower(), repr(message)
    assert "exported" not in message.lower(), repr(message)
    win.hide()
    print("test_a_zero_lap_export_writes_nothing_and_says_why OK")


# ============================================================ L12-02 — one trust verdict
class _Warnings:
    """Records every QMessageBox.warning and answers with a fixed button."""

    def __init__(self, answer):
        self.answer = answer
        self.seen = []

    def __call__(self, _parent, title, text, *_args, **_kw):
        self.seen.append((title, text))
        return self.answer

    @property
    def trust(self):
        return [t for _title, t in self.seen if "provisional" in t]


def _drive_video_export(win, warnings):
    """Run _export_overlay_video with ffmpeg + the source file faked present and the options dialog
    stubbed, returning True iff the export got as far as asking for resolution/quality."""
    reached = []
    orig_warning, orig_available = QMessageBox.warning, export_video.ffmpeg_available
    orig_ask = StudioWindow._ask_export_options
    QMessageBox.warning = staticmethod(warnings)
    export_video.ffmpeg_available = lambda: True
    StudioWindow._ask_export_options = lambda _self, _lap: reached.append(1)
    try:
        win._export_overlay_video()
    finally:
        QMessageBox.warning = orig_warning
        export_video.ffmpeg_available = orig_available
        StudioWindow._ask_export_options = orig_ask
    return bool(reached)


def test_the_mp4_export_obeys_the_same_trust_verdict_as_the_lap_card():
    """One decision, every shareable output. On a session card_data() blocks, the overlay export
    must not proceed silently: it asks, defaults to Cancel, and a Cancel stops it before the options
    dialog. On main it went straight through to a render."""
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "GX010099.MP4")
        open(src, "wb").close()
        win = _window(FakeSession(verified=False), paths=(src,))
        win._sync_export_menu()
        assert win._share_card_blocked(), "the fixture is not actually blocked"
        for name in CARD_EXPORTS:
            assert not getattr(win, name).isEnabled(), f"{name} is enabled on a blocked session"
        # The video action stays available on purpose — the honesty lives in the confirm.
        assert win._export_video_action.isEnabled()

        cancelled = _Warnings(QMessageBox.Cancel)
        assert not _drive_video_export(win, cancelled), \
            "a blocked session reached the options dialog without asking"
        assert len(cancelled.trust) == 1, cancelled.seen
        text = cancelled.trust[0]
        assert "auto-fitted" in text and "Save it as a track" in text, text
        assert "video export cancelled" in win.statusBar().currentMessage()

        # Confirmed: the driver may still have their provisional clip.
        confirmed = _Warnings(QMessageBox.Yes)
        assert _drive_video_export(win, confirmed), "a confirmed export was still blocked"
        assert len(confirmed.trust) == 1, confirmed.seen

        # Inverse control: a VERIFIED session is never asked.
        win.session = FakeSession(verified=True)
        quiet = _Warnings(QMessageBox.Cancel)
        assert _drive_video_export(win, quiet), "a verified session was stopped"
        assert quiet.trust == [], quiet.seen
        win.hide()
    print("test_the_mp4_export_obeys_the_same_trust_verdict_as_the_lap_card OK")


# ============================================================ L12-01 — the report's unit
def test_the_report_is_written_in_the_display_unit_and_the_csv_is_not():
    """_export_report threads the window's live speed unit into write_report_html; _export_laps_csv
    passes none, so the CSV keeps its canonical SI headers. On main the report took no unit at all."""
    win = _window(FakeSession())
    win.view = SimpleNamespace(map=QWidget(), plots=QWidget())
    win._speed_unit = "mph"
    seen = {}
    orig_report, orig_laps = export_data.write_report_html, export_data.write_laps_csv
    orig_dialog = QFileDialog.getSaveFileName
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "r.html")
        QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (out, ""))
        export_data.write_report_html = lambda *a, **k: seen.update(report=k)
        export_data.write_laps_csv = lambda *a, **k: seen.update(laps=(a[2:], k))
        try:
            win._export_report()
            win._export_laps_csv()
        finally:
            export_data.write_report_html = orig_report
            export_data.write_laps_csv = orig_laps
            QFileDialog.getSaveFileName = orig_dialog
    assert seen["report"].get("unit") == "mph", seen["report"]
    assert seen["laps"] == ((), {}), f"the CSV writer was given a unit: {seen['laps']}"
    win.hide()
    print("test_the_report_is_written_in_the_display_unit_and_the_csv_is_not OK")


# ============================================================ L12-04 — a document's map grab
class _FakeLegend(QWidget):
    """The map key's surface the report grab touches: a row list, a relayout, a visibility flag."""

    _ROWS = (("marker", "Video position"), ("brake", "Brake point"),
             ("corner", "Corner apex (C#)"), ("start", "Drag = start / sector line"))

    def __init__(self):
        super().__init__()
        self.relayouts = 0

    def _relayout(self):
        self.relayouts += 1


class _FakeMap(QWidget):
    """A grab-able widget with the MapView contract the two clean grabs use."""

    def __init__(self):
        super().__init__()
        self.resize(60, 40)
        self._map_key = _FakeLegend()
        self._map_key.setParent(self)
        self._map_key.show()
        self.at_grab = []
        self.repins = 0

    def _reposition_key(self):
        self.repins += 1

    def grab_clean(self):
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            self._map_key.hide()
            try:
                yield self
            finally:
                self._map_key.show()
        return _ctx()

    def grab(self):
        self.at_grab.append((self._map_key.isHidden(), tuple(self._map_key._ROWS)))
        return super().grab()


def test_the_report_map_keeps_its_key_and_loses_the_interaction_chrome():
    """The report grab must sit between the two existing ones: grab_clean's chrome suppression, but
    with the explanatory key present — minus the row describing a drag. Everything is restored
    afterwards, so the live map is untouched. On main _grab_report_map_png did not exist and the
    report embedded the raw grab."""
    win = _window(FakeSession())
    fake = _FakeMap()
    png = win._grab_report_map_png(fake)
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    assert len(fake.at_grab) == 1, fake.at_grab
    hidden, rows = fake.at_grab[0]
    assert not hidden, "the report grab hid the map key a document needs"
    labels = [label for _kind, label in rows]
    assert "Brake point" in labels and "Corner apex (C#)" in labels, labels
    assert not any(kind == "start" for kind, _label in rows), labels

    # Restored: the class rows are back (no instance shadow), the plate was re-laid out and
    # re-pinned, and the key's live visibility is whatever grab_clean's own finally set.
    assert "_ROWS" not in fake._map_key.__dict__, "the row shadow leaked onto the live legend"
    assert fake._map_key._ROWS == _FakeLegend._ROWS, fake._map_key._ROWS
    assert fake._map_key.relayouts == 2 and fake.repins == 2, \
        (fake._map_key.relayouts, fake.repins)
    assert not fake._map_key.isHidden(), "the live map key stayed hidden after the grab"
    win.hide()
    print("test_the_report_map_keeps_its_key_and_loses_the_interaction_chrome OK")


def test_the_report_map_grab_survives_a_map_without_the_contract():
    """A bare widget (no grab_clean, no key) still exports — chrome must never fail an export."""
    win = _window(FakeSession())
    bare = QWidget()
    bare.resize(20, 20)
    assert win._grab_report_map_png(bare)[:8] == b"\x89PNG\r\n\x1a\n"
    win.hide()
    print("test_the_report_map_grab_survives_a_map_without_the_contract OK")


# ============================================================ W5-02 — a document, not a screen
def _png_bytes(w, h):
    """A real PNG of a known pixel size (what a grab hands the width helper)."""
    image = QImage(w, h, QImage.Format_RGB32)
    image.fill(0x202020)
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    image.save(buf, "PNG")
    return bytes(buf.data())


def test_the_report_figure_width_divides_out_the_screens_pixel_ratio():
    """`QWidget.grab()` renders at the screen's device pixel ratio, so the same panel comes back
    917 px wide on a non-Retina Mac and 1834 px on a Retina one; embedded with no width, the
    browser laid the figure out at that DEVICE width and the exported document described the
    machine (+22 % figures, 168 px longer page). The helper states the LOGICAL width instead —
    always <= the PNG's own, so a browser only ever downsamples. On main it did not exist."""
    win = _window(FakeSession())
    png = _png_bytes(1834, 558)
    win.devicePixelRatioF = lambda: 1.0
    assert win._report_image_width(png) == 1834
    win.devicePixelRatioF = lambda: 2.0
    assert win._report_image_width(png) == 917
    win.devicePixelRatioF = lambda: 0.0          # never divide by a nonsense ratio
    assert win._report_image_width(png) == 1834
    win.devicePixelRatioF = lambda: 1.0
    assert win._report_image_width(b"not a png at all") is None  # ⇒ no attribute, as before
    win.hide()
    print("test_the_report_figure_width_divides_out_the_screens_pixel_ratio OK")


def test_the_report_export_states_a_layout_width_for_every_figure():
    """End to end: _export_report hands the writer (title, png, width) per figure, and at DPR 1
    that width is the panel's own logical width — so the same export from a Retina machine lays
    out identically. On main the images were bare (title, png) pairs."""
    win = _window(FakeSession())
    map_w, plots_w = QWidget(), QWidget()
    map_w.resize(240, 100)
    plots_w.resize(240, 180)
    win.view = SimpleNamespace(map=map_w, plots=plots_w)
    seen = {}
    orig_report, orig_dialog = export_data.write_report_html, QFileDialog.getSaveFileName
    with tempfile.TemporaryDirectory() as td:
        QFileDialog.getSaveFileName = staticmethod(
            lambda *a, **k: (os.path.join(td, "r.html"), ""))
        export_data.write_report_html = lambda *a, **k: seen.update(k)
        try:
            win._export_report()
        finally:
            export_data.write_report_html = orig_report
            QFileDialog.getSaveFileName = orig_dialog
    images = seen["images"]
    assert [t for t, *_ in images] == ["Track map", "Speed · Δ to best"], images
    for (title, png, width), widget in zip(images, (map_w, plots_w), strict=True):
        assert png[:8] == b"\x89PNG\r\n\x1a\n", title
        natural = QImage.fromData(png, "PNG").width()
        assert width == round(natural / win.devicePixelRatioF()) == widget.width(), \
            (title, width, natural, widget.width())
    win.hide()
    print("test_the_report_export_states_a_layout_width_for_every_figure OK")


# ============================================================ L12-07 — put a number on it
def test_the_options_hint_quantifies_the_size_and_the_work():
    """The dialog sold "larger file"/"smaller file" and stated no size. The hint now names a size,
    the frame count and the resolved encoder — and the presets differ by a real multiple."""
    win = _window(FakeSession())
    high = win._export_size_hint(23.231, 1080, "high")
    standard = win._export_size_hint(23.231, 720, "standard")
    for text in (high, standard):
        assert "MB" in text and "frames to render" in text, text
        assert any(enc in text for enc in (export_video.VT_H264, export_video.SW_H264)), text
    mb = [float(t.split()[1]) for t in (high, standard)]
    assert mb[0] > mb[1] * 2, f"1080p/High is not measurably bigger than 720p/Standard: {mb}"
    # Frames are exact (ceil(duration x fps)), not an estimate.
    assert f"{int(23.231 * 30) + 1} frames" in high, high
    # Nothing honest to say without a pixel count or a duration: say nothing.
    assert win._export_size_hint(23.231, 99999, "high") == ""
    assert win._export_size_hint(0.0, 1080, "high") == ""
    win.hide()
    print("test_the_options_hint_quantifies_the_size_and_the_work OK")


def _clear_export_preset():
    """Drop the persisted export preset so a guard can start from the app's own defaults.

    The preset is a REAL preference (redirected to this file's temp tree at the top), shared by
    every test in the process and by every run on a machine where the user has ever chosen one.
    A test that assumes an unstored state has to say so — and clear it."""
    data = prefs.load()
    for key in (StudioWindow._PREF_EXPORT_RES, StudioWindow._PREF_EXPORT_QUALITY):
        data.pop(key, None)
    prefs.save(data)


def _run_options_dialog(win, on_dialog):
    """Open the real _ask_export_options with QDialog.exec replaced by `on_dialog`."""
    orig = QDialog.exec
    QDialog.exec = on_dialog
    try:
        return win._ask_export_options(0)
    finally:
        QDialog.exec = orig


def test_the_hint_refreshes_on_the_quality_combo_too():
    """Only Resolution was wired to _update_hint, so changing the quality — the choice the copy
    sells hardest — left the estimate stale.

    W10-06 — this used to open the dialog and go straight to `setCurrentIndex(1)`, assuming it
    started at High. The preset is a real persisted preference (studio.prefs), so once ANY earlier
    test (or any user who has ever picked "Standard") has stored index 1, that call is a no-op and
    the guard asserts a change nothing requested: green in the file's hand-written order, red in
    any other, and blind to the regression it exists for on a machine where the preference is
    already Standard. It now clears the two preset keys and drives each combo off a KNOWN index,
    so the verdict does not depend on what ran before it."""
    _clear_export_preset()
    win = _window(FakeSession())
    texts = []

    def on_dialog(dlg):
        hint = [w for w in dlg.findChildren(QLabel) if "Output:" in w.text()][0]
        combos = dlg.findChildren(QComboBox)
        # The starting state is asserted, not assumed — the default preset is the top of each list.
        assert (combos[0].currentIndex(), combos[1].currentIndex()) == (1, 0), (
            "with no stored preset the dialog must open at 1080p/High, got "
            f"{(combos[0].currentIndex(), combos[1].currentIndex())}")
        texts.append(hint.text())
        combos[1].setCurrentIndex(1)          # Quality: High -> Standard (a real transition)
        assert combos[1].currentIndex() == 1
        texts.append(hint.text())
        combos[0].setCurrentIndex(0)          # Resolution: 1080p -> 720p (a real transition)
        assert combos[0].currentIndex() == 0
        texts.append(hint.text())
        return QDialog.Rejected

    _run_options_dialog(win, on_dialog)
    assert texts[0] != texts[1], f"the quality combo left the hint unchanged: {texts[0]!r}"
    assert texts[1] != texts[2], f"the resolution combo left the hint unchanged: {texts[1]!r}"
    win.hide()
    print("test_the_hint_refreshes_on_the_quality_combo_too OK")


# ============================================================ L12-08 — remember it for real
def test_the_export_preset_survives_a_new_window():
    """The picker claimed to remember the choice; it was window-instance state that died with the
    window. It is a preference now, like the unit and the palette."""
    picked = _window(FakeSession())
    _run_options_dialog(picked, lambda dlg: (dlg.findChildren(QComboBox)[0].setCurrentIndex(0),
                                             dlg.findChildren(QComboBox)[1].setCurrentIndex(1),
                                             QDialog.Accepted)[-1])
    picked.hide()
    stored = json.load(open(prefs.prefs_path(), encoding="utf-8"))
    assert stored.get(StudioWindow._PREF_EXPORT_RES) == 0, stored
    assert stored.get(StudioWindow._PREF_EXPORT_QUALITY) == 1, stored

    seen = {}

    def on_reopen(dlg):
        combos = dlg.findChildren(QComboBox)
        seen["idx"] = (combos[0].currentIndex(), combos[1].currentIndex())
        return QDialog.Rejected

    fresh = _window(FakeSession())          # a different StudioWindow, as after a relaunch
    _run_options_dialog(fresh, on_reopen)
    assert seen["idx"] == (0, 1), f"a fresh window reopened on {seen['idx']}, not the saved preset"
    fresh.hide()
    print("test_the_export_preset_survives_a_new_window OK")


def test_a_garbage_stored_preset_falls_back_to_the_default():
    """Guarded accessor: an out-of-range index from an older build opens on the default rather than
    raising out of a dialog the user just asked for."""
    win = _window(FakeSession())
    prefs.set(StudioWindow._PREF_EXPORT_RES, 99)
    prefs.set(StudioWindow._PREF_EXPORT_QUALITY, "high")
    seen = {}

    def on_dialog(dlg):
        combos = dlg.findChildren(QComboBox)
        seen["idx"] = (combos[0].currentIndex(), combos[1].currentIndex())
        return QDialog.Rejected

    _run_options_dialog(win, on_dialog)
    assert seen["idx"] == (1, 0), seen
    prefs.set(StudioWindow._PREF_EXPORT_RES, 1)
    prefs.set(StudioWindow._PREF_EXPORT_QUALITY, 0)
    win.hide()
    print("test_a_garbage_stored_preset_falls_back_to_the_default OK")


# ============================================================ L11-08 — say something either way
def test_reveal_in_finder_reports_both_outcomes():
    """The button asks a system handler that can decline, and nothing else on screen changes when it
    does. On main the bool was discarded and the status bar stayed empty in both directions."""
    win = _window(FakeSession())
    orig = QDesktopServices.openUrl
    try:
        for answer, expected in ((True, "revealed"), (False, "could not open")):
            QDesktopServices.openUrl = staticmethod(lambda _url, a=answer: a)
            win.statusBar().clearMessage()
            win._reveal_library()
            message = win.statusBar().currentMessage()
            assert expected in message, f"openUrl -> {answer}: {message!r}"
            assert os.path.dirname(library.library_path()) in message, message
    finally:
        QDesktopServices.openUrl = orig
    win.hide()
    print("test_reveal_in_finder_reports_both_outcomes OK")


# ============================================================ PR #153's handoff — the confirm
class _Questions:
    def __init__(self, answer):
        self.answer = answer
        self.seen = []

    def __call__(self, _parent, title, text, *_args, **_kw):
        self.seen.append((title, text))
        return self.answer


def _save_as_track(win, name, answer=None):
    """Drive File ▸ Save as track… with the name dialog + any replace confirm stubbed."""
    asked = _Questions(answer if answer is not None else QMessageBox.No)
    prompts = []
    orig_text, orig_question = QInputDialog.getText, QMessageBox.question
    QInputDialog.getText = staticmethod(
        lambda _p, _t, label, *a, **k: (prompts.append(label), (name, True))[-1])
    QMessageBox.question = staticmethod(asked)
    try:
        win._save_as_track()
    finally:
        QInputDialog.getText = orig_text
        QMessageBox.question = orig_question
    return asked, prompts[0]


def test_save_as_track_asks_before_it_replaces_a_different_circuit():
    """PR #153 made track_db REFUSE a silent overwrite and left the confirm here. A name reused for
    a circuit far away must ask, keep the stored lines on No, and say which happened."""
    db = track_db.db_path()
    if os.path.exists(db):
        os.remove(db)
    first = _window(FakeSession(track=None, centroid=(52.0, -0.78)))
    asked, _prompt = _save_as_track(first, "My Circuit")
    assert asked.seen == [], "saving a brand-new name asked a question"
    stored = track_db.load()["tracks"][0]["start"]
    assert "saved track 'My Circuit'" in first.statusBar().currentMessage()
    first.hide()

    # A different circuit (~0.7 deg of latitude ≈ 78 km) under the SAME name.
    other = _window(FakeSession(track=None, centroid=(52.7, -0.78)))
    asked, _prompt = _save_as_track(other, "My Circuit", QMessageBox.No)
    assert len(asked.seen) == 1, asked.seen
    title, text = asked.seen[0]
    assert title == "Replace track" and "km from here" in text, (title, text)
    assert track_db.load()["tracks"][0]["start"] == stored, "a declined confirm still overwrote"
    assert "kept the track already saved" in other.statusBar().currentMessage()

    asked, _prompt = _save_as_track(other, "My Circuit", QMessageBox.Yes)
    assert len(asked.seen) == 1, asked.seen
    assert track_db.load()["tracks"][0]["start"] != stored, "a confirmed replace did not write"
    assert "replaced track 'My Circuit'" in other.statusBar().currentMessage()
    other.hide()
    os.remove(db)
    print("test_save_as_track_asks_before_it_replaces_a_different_circuit OK")


def test_save_as_track_names_the_provisional_line_in_its_prompt():
    """Promoting auto-fitted lines is the documented remedy for provisional timing, so this must not
    block — but the save makes them the trusted line for every future recording here, which is worth
    one sentence. A verified session gets the plain prompt."""
    db = track_db.db_path()
    if os.path.exists(db):
        os.remove(db)
    win = _window(FakeSession(verified=False, track=None, centroid=(41.0, 2.0)))
    _asked, prompt = _save_as_track(win, "Provisional Place")
    assert "auto-fitted" in prompt and "check it on the map" in prompt, prompt
    win.session = FakeSession(verified=True, track=None, centroid=(41.0, 2.0))
    _asked, prompt = _save_as_track(win, "Provisional Place")
    assert prompt == "Track name:", prompt
    win.hide()
    os.remove(db)
    print("test_save_as_track_names_the_provisional_line_in_its_prompt OK")


def _run_all():
    test_a_zero_lap_recording_disables_every_data_export_with_a_reason()
    test_a_zero_lap_export_writes_nothing_and_says_why()
    test_the_mp4_export_obeys_the_same_trust_verdict_as_the_lap_card()
    test_the_report_is_written_in_the_display_unit_and_the_csv_is_not()
    test_the_report_map_keeps_its_key_and_loses_the_interaction_chrome()
    test_the_report_map_grab_survives_a_map_without_the_contract()
    test_the_report_figure_width_divides_out_the_screens_pixel_ratio()
    test_the_report_export_states_a_layout_width_for_every_figure()
    test_the_options_hint_quantifies_the_size_and_the_work()
    test_the_hint_refreshes_on_the_quality_combo_too()
    test_the_export_preset_survives_a_new_window()
    test_a_garbage_stored_preset_falls_back_to_the_default()
    test_reveal_in_finder_reports_both_outcomes()
    test_save_as_track_asks_before_it_replaces_a_different_circuit()
    test_save_as_track_names_the_provisional_line_in_its_prompt()
    print("ALL OK")


if __name__ == "__main__":
    _run_all()
