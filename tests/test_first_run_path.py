"""The FIRST-RUN PATH — launch, drag, drop, load, and telling the truth about what you loaded.

Six QA findings from the design-wave measurements, each pinned by the thing that was measured:

  * D4-02 (HIGH) — THE TWO FRONT DOORS DISAGREED BY 44 LAPS AND NEITHER SAID SO. Dropping
    GX010062.MP4 loads 66 laps across three chapters; picking the SAME file in File ▸ Open… loads
    22, because dropEvent goes through _open_recordings -> chapters.discover_siblings and _open_file
    does not. The window title's "· 3 chapters" suffix only appears in the affirmative case and the
    status bar said nothing in either. The behaviour is deliberate (see _open_file's docstring for
    why chaining there would kill File ▸ Load full recording); the SILENCE was not.
  * D4-01 (HIGH) — THE BUSY CARD FROZE FOR 1.5 s UNDER A HEADLINE THAT HAD STOPPED BEING TRUE.
    Session.load is off-thread, _build_ui is not; the card must NAME the stage that is about to
    block, on the card already on screen, with a forced paint.
  * D4-03 (HIGH) — A DRAG OVER THE WINDOW CHANGED ZERO PIXELS. Asserted from the WINDOW composite,
    never from drop_zone.grab(): a child grab reads the QSS colour back out of the palette and
    reports success when nothing composited.
  * D2-04 (HIGH) — A CORRUPT SIDECAR SILENTLY DISCARDED HAND-PLACED TIMING LINES, then the app
    asked the user to place them again. Absent must stay silent (the negative control); unusable
    must reach the notice channel.
  * D2-09 / D4-06 (MED) — THE FAILURE FRAME AND THE FIRST-RUN FRAME WERE DIFFERENT SIZES. The error
    grew the card 403x239 -> 727x303 and moved both buttons 48 px; clicking "Open demo" moved the
    PRIMARY button 39 px. Both frames must be geometrically identical.
  * D2-16 (LOW) — an unreadable tracks.json said nothing at load.

Real Qt, real StudioWindow, real files on disk (a 16-byte .MP4 is enough for chapters/sidecar path
resolution — nothing here decodes). Run offscreen:
    QT_QPA_PLATFORM=offscreen python tests/test_first_run_path.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# The persistence seams into one temp tree BEFORE any window exists — this file loads sessions
# (which upsert the library), reads the track DB and resolves sidecars. Same idiom as
# tests/test_app_chrome.py. sidecar.sidecar_path is deliberately NOT redirected: the chapter-1-stem
# rule is part of what is under test, and the recordings it resolves against live in a temp dir.
from studio import library, prefs, track_db  # noqa: E402

_SEAMS = tempfile.mkdtemp(prefix="pacer-test-first-run-")
for _mod, _name in ((prefs, "prefs"), (library, "library"), (track_db, "track_db")):
    _dir = os.path.join(_SEAMS, _name)
    os.makedirs(_dir, exist_ok=True)
    _mod._app_support_dir = (lambda d=_dir: d)

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()            # module scope, BEFORE any widget: measure the SHIPPING font stack

import numpy as np  # noqa: E402
from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QImage  # noqa: E402
from PySide6.QtWidgets import QLabel, QProgressBar, QPushButton, QStatusBar  # noqa: E402
from test_central_view_realqt import _studiowindow_with_view  # noqa: E402

from studio import chapters, sidecar  # noqa: E402
from studio.app import (  # noqa: E402
    SIDECAR_UNREADABLE_NOTICE,
    TRACKS_UNREADABLE_NOTICE,
    StudioWindow,
)
from studio.overlays import BUSY_DEMO_LABEL, WelcomeView  # noqa: E402

# PySide6 does NOT take ownership of a QMimeData or a QDragEnterEvent you construct: letting either
# fall out of scope segfaults the process the instant the handler reads event.mimeData(). Held for
# the life of the module rather than trusted to a local.
_KEEP = []

_START = [[52.04031, -0.78487], [52.04020, -0.78460]]


def _settle(n=6):
    for _ in range(n):
        _APP.processEvents()


def _rgb(w):
    """A widget's painted pixels as (h, w, 3) uint8 — Format_RGB32 first, so scanline padding and
    alpha bytes cannot manufacture a difference. The same helper shape as tests/test_focus_cues.py."""
    img = w.grab().toImage().convertToFormat(QImage.Format_RGB32)
    a = np.frombuffer(bytes(img.constBits()), np.uint8).reshape(img.height(), img.width(), 4)
    return a[..., :3]


def _rect_in(child, root):
    tl = child.mapTo(root, QPoint(0, 0))
    return tl.x(), tl.y(), child.width(), child.height()


def _chaptered(root, rec="0062", n=3):
    """`n` GoPro chapter files of one recording, on disk. 16 bytes each — chapters.discover_siblings
    and sidecar.sidecar_path resolve on NAMES, and nothing in this file decodes."""
    paths = []
    for i in range(1, n + 1):
        p = os.path.join(root, f"GX{i:02d}{rec}.MP4")
        with open(p, "wb") as f:
            f.write(b"\x00" * 16)
        paths.append(p)
    return paths


def _window():
    """A real StudioWindow over the synthetic session, plus the async-load bookkeeping the real
    __init__ installs (the shared fixture builds the window via __new__)."""
    win, view = _studiowindow_with_view(build_menu=True)
    # The fixture stubs the "Load full recording" sync out (it has no menu by default); this file
    # is testing exactly that item's agreement with the notice, so the real one goes back.
    del win._sync_full_recording_action
    win._load_token = 0
    win._load_worker = None
    win._load_workers = set()
    win._pending_load = None
    win._loading_token = None
    win._placeholder_timer = None
    win._demo_worker = None
    win._drop_notice = None
    win._timing_restore_failed = False
    win._timing_restore_unreadable = False
    win._tracks_unreadable = False
    win._notice = None
    win._loading_card = None
    win._loading_headline = None
    win._ref_load_token = 0
    win._ref_load_worker = None
    return win, view


# ==================================================== D4-02 · the two front doors, and the notice
def test_a_partial_recording_says_so_and_names_the_way_out():
    """A session that is a strict SUBSET of its recording's chapters states that on the status bar
    and names the control that fixes it — and the control agrees, because both read
    _chapter_subset. On main both were silent: `_session_notice` never mentioned chapters."""
    with tempfile.TemporaryDirectory() as root:
        paths = _chaptered(root)
        win, _view = _window()
        try:
            # File ▸ Open… — one chapter of three.
            win._paths = [paths[0]]
            assert win._chapter_subset() == (1, 3), win._chapter_subset()
            notice = win._session_notice()
            assert "1 of 3 chapters" in notice, notice
            assert "Load full recording" in notice, notice
            # …and the item it names is the one that is enabled.
            win._sync_full_recording_action()
            assert win._full_action.isEnabled()

            # The DROP path loads all three, so there is nothing partial to report and nothing to
            # chain — the two must flip together, or the sentence advertises a dead menu item.
            win._paths = list(paths)
            assert win._chapter_subset() is None
            assert "chapters" not in (win._session_notice() or "")
            win._sync_full_recording_action()
            assert not win._full_action.isEnabled()

            # A recording with no siblings at all (the demo clip, a non-GoPro name) is never partial.
            solo = os.path.join(root, "holiday.mp4")
            with open(solo, "wb") as f:
                f.write(b"\x00" * 16)
            win._paths = [solo]
            assert win._chapter_subset() is None
        finally:
            win.close()
            _settle()
    print("test_a_partial_recording_says_so_and_names_the_way_out OK")


def test_the_partial_notice_survives_alongside_the_other_clauses():
    """The chapter clause is stated ALONGSIDE the lap/trust clause, not instead of it: a chapter
    with no valid laps is exactly the case where "1 of 3 chapters" is the explanation, and folding
    it into the elif chain would drop it there."""
    with tempfile.TemporaryDirectory() as root:
        paths = _chaptered(root)
        win, _view = _window()
        try:
            win._paths = [paths[0]]
            win.session.valid_lap_ids = lambda: []
            notice = win._session_notice()
            assert "No complete laps" in notice, notice
            assert "1 of 3 chapters" in notice, notice
            assert notice.index("No complete laps") < notice.index("1 of 3 chapters"), notice
        finally:
            win.close()
            _settle()
    print("test_the_partial_notice_survives_alongside_the_other_clauses OK")


# ==================================================== D2-04 · the sidecar that could not be read
def test_an_absent_sidecar_is_silent_and_a_damaged_one_is_not():
    """THE NEGATIVE CONTROL IS HALF THE TEST. `sidecar.load` answered the same None for "there is
    nothing saved" and "there is something saved and I cannot read it", and the window read both as
    "nothing to restore" — so a damaged sidecar discarded the user's hand-placed start/finish line
    with no modal, no status change and no console line, and the app then told them to drag it into
    place. Absent must STAY silent; every damaged shape must reach the notice."""
    with tempfile.TemporaryDirectory() as root:
        paths = _chaptered(root)
        side = sidecar.sidecar_path(paths[0])
        assert side == os.path.join(root, "GX010062.pacer.json"), side
        win, _view = _window()
        try:
            # 1. ABSENT — the ordinary case, and it says nothing.
            assert win.session.restore_saved_timing_lines(side) is None
            win._timing_restore_unreadable = False
            assert SIDECAR_UNREADABLE_NOTICE not in (win._session_notice() or "")

            # 2. DAMAGED, in every shape sidecar.load refuses.
            for body in ('{"version": 1, "start": [[52.04',           # truncated mid-write
                         "not json at all",                           # not JSON
                         "[]",                                        # not an object
                         json.dumps({"version": 99, "start": _START}),  # a version we cannot read
                         json.dumps({"version": 1, "sectors": []})):  # no start line
                with open(side, "w", encoding="utf-8") as f:
                    f.write(body)
                got = win.session.restore_saved_timing_lines(side)
                assert got == sidecar.UNREADABLE, f"{body!r} -> {got!r}"
                win._timing_restore_unreadable = True
                notice = win._session_notice()
                assert SIDECAR_UNREADABLE_NOTICE in notice, notice
                # It names the FILE KIND and one action, not a Python class name.
                assert ".pacer.json" in notice and "place the start/finish line again" in notice

            # 3. …and a VALID sidecar still parses, so the new refusals did not eat the feature.
            # (The full restore round trip needs a real coordinate system and is pinned in
            # tests/test_cross_reference.py::test_restore_saved_timing_lines_is_the_shared_seam.)
            sidecar.save(side, None, _START, [])
            data = sidecar.load(side)
            assert data is not None and data["start"] == _START, data
        finally:
            win.close()
            _settle()
    print("test_an_absent_sidecar_is_silent_and_a_damaged_one_is_not OK")


def test_the_damaged_sidecar_notice_reaches_the_status_bar_through_the_real_load_slot():
    """End to end through the production completion slot — `_on_session_loaded` — rather than by
    setting the flag: the finding was a WIRING one (sidecar.load -> restore_saved_timing_lines ->
    _on_session_loaded all collapsed onto one None), so the test has to walk all three."""
    with tempfile.TemporaryDirectory() as root:
        paths = _chaptered(root)
        side = sidecar.sidecar_path(paths[0])
        with open(side, "w", encoding="utf-8") as f:
            f.write('{"version": 1, "start": [[52.04')
        win, _view = _window()
        try:
            # The synthetic Laps stub has no point_count(); the completion slot logs one line with
            # it. Everything else on this path is production code.
            win.session.point_count = lambda: 0
            win._on_session_loaded(win._load_token, [paths[0]], win.session)
            _settle()
            assert win._timing_restore_unreadable is True
            bar = win.statusBar().currentMessage()
            assert SIDECAR_UNREADABLE_NOTICE in bar, bar
            # …and the partial-recording clause rides along in the same line (D4-02).
            assert "1 of 3 chapters" in bar, bar
        finally:
            win.close()
            _settle()
    print("test_the_damaged_sidecar_notice_reaches_the_status_bar_through_the_real_load_slot OK")


# ==================================================== D2-16 · the track DB that could not be read
def test_an_unreadable_track_database_is_reported_and_a_repairable_one_is_not():
    """`track_db.load` answers empty_db() for a corrupt FILE — correct, and save() keeps a .bak —
    but nothing said so, so every recording opened as "unknown track" indistinguishably from a
    genuinely new circuit. The predicate is deliberately NARROWER than _lossy_to_overwrite: one bad
    entry among good ones is repaired-on-read, not unreadable, and claiming otherwise is a lie."""
    with tempfile.TemporaryDirectory() as root:
        db = os.path.join(root, "tracks.json")
        assert track_db.unreadable(db) is False, "an ABSENT DB is not an unreadable one"
        good = {"version": track_db.VERSION,
                "tracks": [track_db.make_entry("MK", (52.0, -0.78), _START, [])]}
        with open(db, "w", encoding="utf-8") as f:
            json.dump(good, f)
        assert track_db.unreadable(db) is False
        # One malformed entry among the good ones: still readable, still repaired on the next save.
        mixed = dict(good, tracks=[*good["tracks"], {"name": "broken"}])
        with open(db, "w", encoding="utf-8") as f:
            json.dump(mixed, f)
        assert track_db.unreadable(db) is False, "a repairable entry must not read as file corruption"
        assert len(track_db.load(db)["tracks"]) == 1
        # File-level corruption, in each of load()'s three empty_db() shapes.
        for body in ('{"version": 1, "tracks": [',            # truncated
                     '{"tracks": []}',                        # no version
                     '{"version": 1, "tracks": {}}'):         # tracks is not a list
            with open(db, "w", encoding="utf-8") as f:
                f.write(body)
            assert track_db.unreadable(db) is True, body
            assert track_db.load(db)["tracks"] == []

        win, _view = _window()
        try:
            win._tracks_unreadable = True
            assert TRACKS_UNREADABLE_NOTICE in win._session_notice()
            win._tracks_unreadable = False
            assert TRACKS_UNREADABLE_NOTICE not in (win._session_notice() or "")
        finally:
            win.close()
            _settle()
    print("test_an_unreadable_track_database_is_reported_and_a_repairable_one_is_not OK")


# ==================================================== D4-03 · the drag that changed zero pixels
def _drag_enter(win, pos, paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    _KEEP.append(mime)
    ev = QDragEnterEvent(QPoint(*pos), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    _KEEP.append(ev)
    win.dragEnterEvent(ev)
    _settle()
    return ev.isAccepted()


def test_a_drag_over_the_window_lights_the_drop_zone_in_the_composite():
    """PROVEN FROM THE WINDOW, NOT THE CHILD. QStyleSheetStyle writes a rule's colour into the
    widget's palette even when nothing composites, so drop_zone.grab() would report a pass for a
    stylesheet that never reached the pixels — the failure mode that has bitten six times in this
    campaign. Measured on main: 0 of 1,296,000 px changed for a valid .MP4 over the dashed rect AND
    for the same file 400 px outside it (the whole window is the drop target)."""
    with tempfile.TemporaryDirectory() as root:
        mp4 = os.path.join(root, "GX010077.MP4")
        txt = os.path.join(root, "notes.txt")
        for p in (mp4, txt):
            with open(p, "wb") as f:
                f.write(b"\x00" * 16)
        win = StudioWindow([])
        win.resize(1440, 900)
        win.show()
        _settle(8)
        try:
            zone = win.centralWidget().drop_zone
            x, y, w, h = _rect_in(zone, win)
            before = _rgb(win)

            def changed_in_zone():
                after = _rgb(win)
                return int((before[y:y + h, x:x + w] != after[y:y + h, x:x + w]).any(-1).sum())

            # Over the dashed rect, and 400 px outside it: same answer, because the WINDOW is the
            # target and a highlight that only fires over the rect advertises the wrong one.
            for pos in ((720, 450), (60, 820)):
                assert _drag_enter(win, pos, [mp4]), pos
                assert zone.property("dragover") == "true"
                lit = changed_in_zone()
                assert lit > 0, f"the drop zone painted no drag feedback at {pos}"
                # The BORDER is what changes, so the count is of that order, not a full repaint.
                assert lit >= 2 * (w + h), f"only {lit} px changed at {pos} — not a whole border"
                win.dragLeaveEvent(QDragLeaveEvent())
                _settle()
                assert zone.property("dragover") is None
                assert changed_in_zone() == 0, "the highlight outlived the drag"

            # A payload the window REFUSES must not light anything — the cursor saying no is the
            # correct answer there and a lit zone would contradict it.
            assert not _drag_enter(win, (720, 450), [txt])
            assert zone.property("dragover") is None
            assert changed_in_zone() == 0

            # A drop clears it too (Qt sends drop OR leave, never both).
            _drag_enter(win, (720, 450), [mp4])
            assert zone.property("dragover") == "true"
            win._set_dragover(False)
            _settle()
            assert changed_in_zone() == 0
        finally:
            win.close()
            _settle()
    print("test_a_drag_over_the_window_lights_the_drop_zone_in_the_composite OK")


# ==================================================== D2-09 / D4-06 · the welcome frames
_FAILURE_MESSAGES = [
    "That's a folder, not a recording — open the .MP4 files inside it.",
    "Couldn't find that file — it may have been moved, renamed or deleted. Open it again from "
    "where it is now.",
    "That file is empty (0 bytes) — copy it off the camera's SD card again.",
    "Couldn't read that file — check it has finished copying and that you have permission to "
    "open it.",
    "This is a GoPro file, but its telemetry track couldn't be read — the copy is probably "
    "incomplete. Copy it off the SD card again.",
    "This doesn't look like a GoPro recording with GPS metadata — open the original .MP4 the "
    "camera wrote.",
    "Couldn't read telemetry from this recording — it may be corrupt or unsupported. Try copying "
    "it off the SD card again.",
    "Demo clip unavailable — check your connection and retry, or drop your own GoPro .mp4 to get "
    "your laps.",
]


def test_the_failure_frame_and_the_first_run_frame_are_the_same_screen():
    """`_show_welcome` DESTROYS and rebuilds this view to show an error, so the two are consecutive
    frames of one screen — and on main the error was appended below the buttons, unbounded, which
    grew the drop zone 403x239 -> 727x303 and moved both buttons 48 px up, at the moment the user's
    next click is aimed at one of them. The card and both buttons must be byte-identical between
    the two frames."""
    clean = WelcomeView(lambda: None, lambda: None)
    failed = WelcomeView(lambda: None, lambda: None, error=_FAILURE_MESSAGES[5],
                         error_path="/Users/someone/Desktop/track day 2026-08-30/holiday.mp4")
    for v in (clean, failed):
        v.resize(1440, 900)
        v.show()
    _settle()
    try:
        assert clean.drop_zone.size() == failed.drop_zone.size(), \
            f"{clean.drop_zone.size()} vs {failed.drop_zone.size()}"
        for attr in ("drop_zone", "open_btn", "demo_btn"):
            a = _rect_in(getattr(clean, attr), clean)
            b = _rect_in(getattr(failed, attr), failed)
            assert a == b, f"{attr} moved between the two frames: {a} -> {b}"
        # The slot is reserved, not absent: the label exists in BOTH, hidden in one.
        assert clean.error_label.isHidden() and not failed.error_label.isHidden()
        assert clean.error_label.sizePolicy().retainSizeWhenHidden()
        # The PATH is a basename with the absolute path on the tooltip, not 627 px of prose.
        assert "holiday.mp4" in failed.error_label.text()
        assert "/Users/someone" not in failed.error_label.text()
        assert failed.error_label.toolTip() == \
            "/Users/someone/Desktop/track day 2026-08-30/holiday.mp4"
        assert failed.error_label.text().startswith("⚠")
    finally:
        for v in (clean, failed):
            v.close()
            v.deleteLater()
        _settle()
    print("test_the_failure_frame_and_the_first_run_frame_are_the_same_screen OK")


def test_every_production_failure_message_fits_the_reserved_error_slot():
    """A ONE-MESSAGE TEST IS WHAT LETS A ONE-LINE OVERFLOW THROUGH. The reserved slot's height is a
    constant (WelcomeView.ERROR_LINES), so every sentence the app can actually put in it is swept —
    all seven of app._load_failure_message's plus the demo's — each with the longest plausible
    basename appended, at both shipped window widths.

    MEASURED ON THE REAL LABEL AS LAID OUT, not on a detached probe at the width the label was
    ASKED for. The first version of this test read `maximumWidth()` (427 px) and passed, while the
    shipped label was granted its wrapping `sizeHint` (206 px), needed five lines there and was
    clipped top and bottom in the window composite. `width()` is the only honest question."""
    longest = "GX010062-hockenheim-session-2.MP4"
    for width in (1440, 1280):
        for message in _FAILURE_MESSAGES:
            v = WelcomeView(lambda: None, lambda: None, error=message,
                            error_path=f"/Users/someone/Desktop/track day/{longest}")
            v.resize(width, 900)
            v.show()
            _settle()
            got = v.error_label.width()
            line = v.error_label.fontMetrics().height()
            assert v.error_label.minimumHeight() == WelcomeView.ERROR_LINES * line
            # The label really is the card's measure wide — not the narrower hint Qt would give a
            # centred wrapping label left to itself.
            assert got == v.drop_zone.width(), (got, v.drop_zone.width())
            need = v.error_label.heightForWidth(got)
            assert need <= v.error_label.height(), (
                f"{need}px of text in a {v.error_label.height()}px slot at {got}px "
                f"({round(need / line)} lines vs {WelcomeView.ERROR_LINES}): {message[:48]}…")
            v.close()
            v.deleteLater()
            _settle()
    print("test_every_production_failure_message_fits_the_reserved_error_slot OK")


def test_clicking_open_demo_does_not_move_the_primary_button():
    """D4-06: `_set_demo_busy` swaps the label to BUSY_DEMO_LABEL, which grew the button 98 -> 177 px
    in a centred row — so the PRIMARY "Open recording…" slid 39 px left in response to a click on
    the OTHER button. The button is floored at its busy width, so nothing moves."""
    win = StudioWindow([])
    win.resize(1440, 900)
    win.show()
    _settle(8)
    try:
        view = win.centralWidget()
        before = {a: _rect_in(getattr(view, a), win) for a in ("open_btn", "demo_btn", "drop_zone")}
        win._set_demo_busy(True)
        _settle()
        assert view.demo_btn.text() == BUSY_DEMO_LABEL
        after = {a: _rect_in(getattr(view, a), win) for a in ("open_btn", "demo_btn", "drop_zone")}
        assert before == after, f"the busy label moved the row: {before} -> {after}"
        win._set_demo_busy(False)
        _settle()
        assert {a: _rect_in(getattr(view, a), win)
                for a in ("open_btn", "demo_btn", "drop_zone")} == before
    finally:
        win.close()
        _settle()
    print("test_clicking_open_demo_does_not_move_the_primary_button OK")


# ==================================================== D4-10 · the status bar that appeared late
def test_the_status_bar_exists_before_the_first_message():
    """QMainWindow.statusBar() CREATES the bar, so the first showMessage() used to grow a 22 px bar
    under the welcome state and shove its centred content 11 px up — at the exact moment the app is
    telling the user something."""
    win = StudioWindow([])
    win.resize(1440, 900)
    win.show()
    _settle(8)
    try:
        assert win.findChildren(QStatusBar), "no status bar exists on a cold launch"
        zone = win.centralWidget().drop_zone
        before = _rect_in(zone, win)
        win.statusBar().showMessage("anything")
        _settle()
        assert _rect_in(zone, win) == before, "the first message still moved the welcome content"
    finally:
        win.close()
        _settle()
    print("test_the_status_bar_exists_before_the_first_message OK")


# ==================================================== D4-01 / D4-05 · the loading card
def test_the_loading_card_names_the_stage_that_is_about_to_block():
    """D4-01. Session.load runs off-thread; _build_ui does not, and it costs 647-674 ms warm and
    ~1.5 s cold with the event loop shut. The card cannot be made faster — Qt widgets are
    main-thread-only — so it has to be HONEST: rename the headline on the card already on screen,
    force one paint, and only then block. `_announce_stage` must also be a no-op (returning False)
    when no card is up, so a reload fast enough to keep its live session on screen is never
    repainted to say "building"."""
    win = StudioWindow([])
    win.resize(1440, 900)
    win.show()
    _settle(8)
    try:
        assert win._announce_stage("nope") is False, "there is no card yet — nothing to rename"
        win._show_loading_placeholder(["/nowhere/GX010062.MP4"], on_cancel=lambda: None)
        _settle()
        card = win.centralWidget()
        headline = next(q for q in card.findChildren(QLabel) if q.property("role") == "Title")
        assert headline.text() == "Loading telemetry…"
        assert win._announce_stage("Building the session view…") is True
        assert headline.text() == "Building the session view…"
        # The subject line is its own muted label, not fused into the headline by "\n\n" (D4-05).
        subject = [q for q in card.findChildren(QLabel) if q.property("role") == "LoadingTitle"]
        assert subject and subject[0].text() == "recording 0062", [q.text() for q in subject]
        assert "\n" not in headline.text()
    finally:
        win.close()
        _settle()
    print("test_the_loading_card_names_the_stage_that_is_about_to_block OK")


def test_the_loading_card_is_the_welcome_screens_second_frame():
    """D4-05: welcome and loading are consecutive frames two milliseconds apart, and they shared no
    structure — the container went to nothing and the headline dropped from 22 px `role="Title"` to
    a 13 px muted `role="LoadingTitle"`. The headline must keep the welcome's size and role."""
    win = StudioWindow([])
    win.resize(1440, 900)
    win.show()
    _settle(8)
    try:
        welcome_title = next(q for q in win.centralWidget().findChildren(QLabel)
                             if q.property("role") == "Title")
        want = welcome_title.font().pixelSize()
        win._show_loading_placeholder(["/nowhere/GX010062.MP4"], on_cancel=lambda: None)
        _settle()
        card_title = next(q for q in win.centralWidget().findChildren(QLabel)
                          if q.property("role") == "Title")
        assert card_title.font().pixelSize() == want, (card_title.font().pixelSize(), want)
        assert want > 13, "the welcome headline is supposed to be the display step"
    finally:
        win.close()
        _settle()
    print("test_the_loading_card_is_the_welcome_screens_second_frame OK")


def _frame_anchors(win):
    """(headline rect, primary-control rect, card rect) of whichever frame is on screen, in the
    WINDOW's coordinates — the only space in which "did it move" is a question about the eye."""
    from PySide6.QtCore import QPoint, QRect

    def rect(w):
        return QRect(w.mapTo(win, QPoint(0, 0)), w.size())

    central = win.centralWidget()
    if isinstance(central, WelcomeView):
        title = next(q for q in central.findChildren(QLabel) if q.text() == "Pacer")
        return rect(title), rect(central.open_btn), rect(central.drop_zone)
    title = win._loading_headline
    cancel = next(b for b in central.findChildren(QPushButton)
                  if b.objectName() == "LoadingCancel")
    return rect(title), rect(cancel), rect(title.parentWidget())


def test_the_two_frames_of_one_wait_are_anchored_to_each_other():
    """SW2-02: PR #191 made the loading card the welcome screen's second frame in MATERIAL — same
    margins, same 16 px rhythm, same 22 px role="Title" headline — and left it in a different
    PLACE. Measured two milliseconds apart at 1440x900, after that fix: the headline's centre
    jumped 57.0 px and the one button on screen 108.7 px, both FURTHER than before it (13.5 and
    62.9), because three correct repairs each moved one frame without re-measuring the other.

    Both anchors must now coincide EXACTLY, and swept 1 px at a time rather than sampled at two
    sizes: the columns are vertically centred, so the failure mode is an odd/even parity in
    `(window - column) / 2` that a two-size test walks straight past."""
    win = StudioWindow([])
    win.show()
    try:
        worst = {"headline": (0, None), "button": (0, None)}
        sizes = ([(1440, h) for h in range(700, 901)]
                 + [(w, 900) for w in range(973, 1441)])
        for size in sizes:
            win._show_welcome()
            win.resize(*size)
            _settle(6)
            w_head, w_btn, w_card = _frame_anchors(win)
            win._show_loading_placeholder(["/nowhere/GX010062.MP4"], on_cancel=lambda: None)
            _settle(4)
            l_head, l_btn, l_card = _frame_anchors(win)
            for name, a, b in (("headline", w_head, l_head), ("button", w_btn, l_btn)):
                dx = b.center().x() - a.center().x()
                dy = b.center().y() - a.center().y()
                jump = (dx * dx + dy * dy) ** 0.5
                if jump > worst[name][0]:
                    worst[name] = (jump, (size, a, b))
            assert w_card == l_card, (
                f"{size}: the two frames' cards are {w_card} and {l_card}")
        for name, (jump, where) in worst.items():
            assert jump == 0, f"the {name} moves {jump:.1f} px between the two frames at {where}"
    finally:
        win.close()
        _settle()
    print(f"test_the_two_frames_of_one_wait_are_anchored_to_each_other OK "
          f"({len(sizes)} sizes swept at 1 px, headline and button both 0.0 px)")


def test_the_loading_cards_reserved_lines_are_the_welcome_columns_own():
    """The two reservations the anchor rests on, checked against the thing they are copied from —
    so a copy edit or a type-step change goes RED here instead of silently un-anchoring the seam.

      * `SECONDARY_LINES` is the number of lines the welcome tagline actually takes at the card's
        own measure. The loading card's one-line recording label reserves the same, which is what
        makes the two columns the same height under a centred layout.
      * `column_metrics().primary_w` is what "Open recording…" asks for, and Cancel is floored at
        it, so the one button on screen lands in the primary's slot rather than in the centred
        pair's middle (D4-06 floored the demo button, which slid that pair 40 px left)."""
    from studio.overlays import SECONDARY_LINES, column_metrics

    win = StudioWindow([])
    win.resize(1440, 900)
    win.show()
    _settle(8)
    try:
        welcome = win.centralWidget()
        subtitle = next(q for q in welcome.findChildren(QLabel)
                        if q.property("role") == "WelcomeSubtitle")
        m = column_metrics()
        assert subtitle.height() == m.secondary_h, (
            f"the tagline is {subtitle.height()} px but the loading card reserves "
            f"{m.secondary_h} ({SECONDARY_LINES} lines)")
        assert welcome.open_btn.width() == m.primary_w, (welcome.open_btn.width(), m.primary_w)
        assert welcome.demo_btn.width() == m.secondary_w, (welcome.demo_btn.width(), m.secondary_w)
        assert welcome.error_label.height() == m.error_h, (welcome.error_label.height(), m.error_h)
        assert welcome.drop_icon.height() == m.glyph_h, (welcome.drop_icon.height(), m.glyph_h)
    finally:
        win.close()
        _settle()
    print("test_the_loading_cards_reserved_lines_are_the_welcome_columns_own OK "
          f"(glyph {m.glyph_h} · error {m.error_h} · secondary {m.secondary_h} · "
          f"primary {m.primary_w} · secondary action {m.secondary_w})")


def test_the_demo_card_offers_the_same_cancel_the_file_card_does():
    """D2-13: the demo fetch showed the SAME card in the SAME place for the SAME kind of wait, and
    shipped without the one control that card has — so the only multi-second wait in the app you
    could not back out of was the one that reaches the network on a first run."""
    win = StudioWindow([])
    win.resize(1440, 900)
    win.show()
    _settle(8)
    try:
        class _Running:
            @staticmethod
            def isRunning():
                return True

        win._demo_worker = _Running()
        win._on_demo_placeholder_due(win._load_token)
        _settle()
        card = win.centralWidget()
        assert card.findChild(QProgressBar) is not None
        cancel = [b for b in card.findChildren(QPushButton) if b.objectName() == "LoadingCancel"]
        assert cancel, "the demo card must carry the same Cancel the file card does"
        # And the cancel really hands the welcome screen back, dropping the in-flight result by
        # token (the download itself is one uninterruptible call, exactly like Session.load).
        token_before = win._load_token
        cancel[0].click()
        _settle()
        assert win._load_token > token_before, "a cancelled fetch's result must go stale"
        assert isinstance(win.centralWidget(), WelcomeView)
        assert "cancelled" in win.statusBar().currentMessage().lower()
    finally:
        win._demo_worker = None
        win.close()
        _settle()
    print("test_the_demo_card_offers_the_same_cancel_the_file_card_does OK")


def _run_all():
    test_a_partial_recording_says_so_and_names_the_way_out()
    test_the_partial_notice_survives_alongside_the_other_clauses()
    test_an_absent_sidecar_is_silent_and_a_damaged_one_is_not()
    test_the_damaged_sidecar_notice_reaches_the_status_bar_through_the_real_load_slot()
    test_an_unreadable_track_database_is_reported_and_a_repairable_one_is_not()
    test_a_drag_over_the_window_lights_the_drop_zone_in_the_composite()
    test_the_failure_frame_and_the_first_run_frame_are_the_same_screen()
    test_every_production_failure_message_fits_the_reserved_error_slot()
    test_clicking_open_demo_does_not_move_the_primary_button()
    test_the_status_bar_exists_before_the_first_message()
    test_the_loading_card_names_the_stage_that_is_about_to_block()
    test_the_loading_card_is_the_welcome_screens_second_frame()
    test_the_two_frames_of_one_wait_are_anchored_to_each_other()
    test_the_loading_cards_reserved_lines_are_the_welcome_columns_own()
    test_the_demo_card_offers_the_same_cancel_the_file_card_does()
    print("ALL FIRST-RUN-PATH TESTS OK")
    _ = chapters


if __name__ == "__main__":
    _run_all()
