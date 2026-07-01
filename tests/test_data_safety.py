"""Data-safety tests: timing-line UNDO + the library privacy controls (forget / clear).

Two blind spots the CPO review surfaced, covered here as offscreen/pure tests:

A. UNDO the start/finish-line edit — dragging the line re-segments AND overwrites the sidecar
   with no confirm/history, so a slightly-wrong nudge silently destroys the good lap timing +
   the PB/session-best baseline. Session now keeps a bounded per-session stack of PRIOR timing
   lines (pushed BEFORE each edit) and restores the latest through the SAME
   apply_timing_lines_latlon path, so the segmentation + PB baseline recompute identically.
     * an undo restores the previous lines + segmentation (edit twice, undo, lines/laps match
       the first state);
     * undo is a no-op with no history;
     * a restore of a previously-confirmed state stays confirmed (timing_verified consistency).
   Built on a REAL pacer.Laps (the test_sidecar circle construction) — no Qt, no media file.

B. Library privacy — forget one recording (drop its index entry + delete its .pacer.json
   sidecar) and clear the whole index (media + sidecars untouched):
     * library.remove drops exactly the matching fingerprint (index half of forget);
     * library.clear empties the on-disk index but leaves a co-located sidecar file on disk
       (media/sidecars untouched), via the _app_support_dir seam (never the real dir);
     * the forget FILE op (os.remove behind an existence check) unlinks a temp sidecar and is
       safe when the file is already gone;
     * the offscreen LibraryDialog routes a forget / clear through the injected callbacks and
       re-renders from the returned index (the DI wiring the app relies on).

Run: python tests/test_data_safety.py   (pacer for the undo half; Qt offscreen for the dialog)
"""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _auto_confirm(monkeypatch):
    """Stub QMessageBox.question to auto-answer Yes (the confirm dialogs are modal — a headless run
    must never block on one). Restored by the monkeypatch shim after the test."""
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))

import pacer  # noqa: E402
from studio import library, sidecar  # noqa: E402
from studio.library_dialog import (  # noqa: E402
    _COL_DATE,
    FP_ROLE,
    LibraryDialog,
)
from studio.session import Seg, Session  # noqa: E402

# ============================================================ A. timing-line undo
# A real pacer.Laps driving clean laps around a circle, segmented by a start line — the same
# construction test_sidecar uses (points -> coordinate system -> sectors -> update).
_CLAT, _CLON = 52.0, -0.78
_RADIUS_M = 100.0
_PER_LAP = 314
_N_LAPS = 3
_M_PER_DEG_LAT = 111_320.0
_THETA_START = 2.0 * math.pi * (10.5 / _PER_LAP)
_THETA_ALT = 2.0 * math.pi * (40.5 / _PER_LAP)  # a DIFFERENT start line (still crosses the circle)


def _circle_gps(theta, radius=_RADIUS_M):
    lat = _CLAT + (radius * math.cos(theta)) / _M_PER_DEG_LAT
    lon = _CLON + (radius * math.sin(theta)) / (_M_PER_DEG_LAT * math.cos(math.radians(_CLAT)))
    return pacer.GPSSample(lat=lat, lon=lon, altitude=0.0, full_speed=20.0, ground_speed=20.0)


def _start_line_at(cs, theta) -> Seg:
    a = cs.local(_circle_gps(theta, _RADIUS_M - 40.0))
    b = cs.local(_circle_gps(theta, _RADIUS_M + 40.0))
    return Seg(a[0], a[1], b[0], b[1])


def _make_session() -> Session:
    laps = pacer.Laps()
    n = _N_LAPS * _PER_LAP + 1
    for i in range(n):
        theta = 2.0 * math.pi * (i / _PER_LAP)
        laps.add_point(_circle_gps(theta), i * 0.1)
    mn, mx = laps.min_max()
    cs = pacer.CoordinateSystem(
        pacer.GPSSample(lat=(mn.y + mx.y) / 2, lon=(mn.x + mx.x) / 2, altitude=0))
    laps.set_coordinate_system(cs)
    session = Session(laps, cs, None)
    session.set_timing_lines(_start_line_at(cs, _THETA_START), [])
    return session


def _line_tuple(seg: Seg):
    return (seg.x1, seg.y1, seg.x2, seg.y2)


def test_undo_restores_previous_lines_and_segmentation():
    """Edit the start line twice (each preceded by a history push, as the view does), undo once,
    and the lines + valid laps + lap times must match the FIRST edit's state exactly."""
    s = _make_session()
    # State 1: the first user edit. Snapshot the pre-edit lines, then edit.
    s.push_timing_history()
    s.set_timing_lines(_start_line_at(s.cs, _THETA_START), [])
    lines1 = _line_tuple(s.start_line)
    valid1 = s.valid_lap_ids()
    times1 = [s.lap_time(i) for i in valid1]
    assert len(valid1) >= 2, valid1

    # State 2: a second edit to a DIFFERENT start line (snapshot state 1 first).
    s.push_timing_history()
    s.set_timing_lines(_start_line_at(s.cs, _THETA_ALT), [])
    assert _line_tuple(s.start_line) != lines1  # the edit actually moved the line

    # Undo -> back to state 1: same lines, same laps, same times. The restore round-trips through
    # lat/lon (apply_timing_lines_latlon), so the local-metre endpoints wobble ~µm — pin sub-mm
    # (far below GPS noise), and the derived lap times to µs.
    assert s.undo_timing_lines() is True
    for a, b in zip(_line_tuple(s.start_line), lines1, strict=True):
        assert abs(a - b) < 1e-3, (s.start_line, lines1)
    assert s.valid_lap_ids() == valid1
    for t0, t1 in zip([s.lap_time(i) for i in s.valid_lap_ids()], times1, strict=True):
        assert abs(t0 - t1) < 1e-6, (t0, t1)


def test_undo_restores_sector_lines():
    """A sector line added in the second edit is gone after undoing back to the no-sector state."""
    s = _make_session()
    s.push_timing_history()  # snapshot the no-sector state
    theta_sector = 2.0 * math.pi * ((_PER_LAP // 2) + 0.5) / _PER_LAP
    a = s.cs.local(_circle_gps(theta_sector, _RADIUS_M - 40.0))
    b = s.cs.local(_circle_gps(theta_sector, _RADIUS_M + 40.0))
    s.set_timing_lines(s.start_line, [Seg(a[0], a[1], b[0], b[1])])
    assert s.sector_count() == 1
    assert s.undo_timing_lines() is True
    assert s.sector_count() == 0


def test_undo_noop_with_no_history():
    """Undo with an empty stack is a no-op (returns False), leaving the segmentation untouched."""
    s = _make_session()
    assert s.can_undo_timing() is False
    valid = s.valid_lap_ids()
    lines = _line_tuple(s.start_line)
    assert s.undo_timing_lines() is False
    assert s.valid_lap_ids() == valid
    assert _line_tuple(s.start_line) == lines


def test_undo_walks_back_one_edit_at_a_time():
    """Two pushes -> two undos available; the restore itself is not re-pushed (repeated undo walks
    the history back, then stops)."""
    s = _make_session()
    s.push_timing_history()
    s.set_timing_lines(_start_line_at(s.cs, _THETA_ALT), [])
    assert s.can_undo_timing() is True
    assert s.undo_timing_lines() is True   # consumes the one snapshot
    assert s.can_undo_timing() is False    # the restore was NOT re-pushed
    assert s.undo_timing_lines() is False  # now a no-op


def test_undo_keeps_confirmed_state():
    """Restoring a previously-confirmed state stays confirmed (timing_verified consistency): a
    manual edit confirms the timing; undoing back to a confirmed snapshot must not demote it."""
    s = _make_session()
    s.track_name = None  # unknown track: trust rides purely on the user-confirmation flag
    s.confirm_timing()
    assert s.timing_verified is True
    s.push_timing_history()  # snapshots confirmed=True
    s.set_timing_lines(_start_line_at(s.cs, _THETA_ALT), [])  # a fresh user edit stays confirmed
    assert s.timing_verified is True
    assert s.undo_timing_lines() is True
    assert s.timing_verified is True  # the restored confirmed state is preserved


def test_undo_history_is_bounded():
    """The stack can't grow without bound: > _UNDO_DEPTH pushes keep only the newest _UNDO_DEPTH."""
    s = _make_session()
    for _ in range(Session._UNDO_DEPTH + 5):
        s.push_timing_history()
    assert len(s._timing_history()) == Session._UNDO_DEPTH


# ============================================================ B. library privacy: forget / clear
def _entry(stem, *, track="Daytona MK", date="2024-05-01", laps=12,
           best=68.4, theo=67.9, paths=None):
    return {
        "fingerprint": library.fingerprint(stem),
        "stem": stem,
        "track": track,
        "date": date,
        "lap_count": laps,
        "best": best,
        "theoretical": theo,
        "paths": paths if paths is not None else [f"/media/{stem}.MP4"],
    }


def test_remove_drops_matching_fingerprint_only():
    """library.remove drops exactly the matching fingerprint (the index half of forget); a
    non-matching key is a no-op returning False."""
    index = {"version": library.VERSION,
             "entries": [_entry("GX010001"), _entry("GX010002"), _entry("GX010003")]}
    fp = library.fingerprint("GX010002")
    assert library.remove(index, fp) is True
    fps = [e["fingerprint"] for e in index["entries"]]
    assert fp not in fps and len(fps) == 2
    # Removing a fingerprint that isn't present changes nothing.
    assert library.remove(index, "GX9999") is False
    assert len(index["entries"]) == 2


def test_remove_persists_via_save(monkeypatch):
    """The forget index write survives a round trip: remove -> save -> load has the entry gone."""
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(library, "_app_support_dir", lambda: d)
        library.save({"version": library.VERSION,
                      "entries": [_entry("GX010001"), _entry("GX010002")]})
        index = library.load()
        assert library.remove(index, library.fingerprint("GX010001")) is True
        library.save(index)
        reloaded = library.load()
        stems = [e["fingerprint"] for e in reloaded["entries"]]
        assert stems == [library.fingerprint("GX010002")], stems


def test_clear_empties_index_but_leaves_sidecar(monkeypatch):
    """library.clear wipes the on-disk index to empty, but a real .pacer.json sidecar sitting next
    to a video is UNTOUCHED (clear only removes the app-support history, never media/sidecars)."""
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(library, "_app_support_dir", lambda: d)
        library.save({"version": library.VERSION, "entries": [_entry("GX010001")]})
        assert library.load()["entries"]  # non-empty first
        # A sidecar file elsewhere on disk (stands in for one next to a user's MP4).
        side = os.path.join(d, "GX010001.pacer.json")
        with open(side, "w") as f:
            f.write("{}")
        library.clear()
        assert library.load()["entries"] == []          # index wiped
        assert os.path.exists(side)                      # sidecar left untouched


def test_forget_sidecar_unlink_is_guarded():
    """The forget FILE op mirrors the app: os.remove behind an existence check, safe when the file
    is already gone (never raises). Uses a real temp sidecar file, asserts it's unlinked."""
    with tempfile.TemporaryDirectory() as d:
        # Resolve the sidecar the way the app does (sidecar_path off the first chapter path).
        media = os.path.join(d, "GX010042.MP4")
        side = sidecar.sidecar_path(media)
        with open(side, "w") as f:
            f.write("{}")
        assert os.path.exists(side)

        # The app's guarded deletion body.
        def _delete(path):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass

        _delete(side)
        assert not os.path.exists(side)  # unlinked
        _delete(side)                     # idempotent — no raise when already gone
        assert not os.path.exists(side)


# ------------------------------------------------ the LibraryDialog forget / clear wiring (offscreen)
def test_dialog_forget_routes_through_callback_and_rerenders(monkeypatch):
    """Right-click forget: the dialog's forget path confirms, calls the injected callback with the
    row's entry, and re-renders from the returned (shrunken) index. Drives _forget_row directly
    (the context-menu handler just resolves the row then calls it)."""
    _auto_confirm(monkeypatch)
    index = {"version": library.VERSION,
             "entries": [_entry("GX010001", date="2024-05-01"),
                         _entry("GX010002", date="2024-05-02")]}
    forgotten = []

    def forget(entry):
        forgotten.append(entry["fingerprint"])
        library.remove(index, entry["fingerprint"])
        return index

    dlg = LibraryDialog(index, open_recording=lambda _p: None,
                        forget_recording=forget, clear_library=lambda: index)
    assert dlg.table.rowCount() == 2
    # Forget the newest row (row 0 after the Descending date sort).
    date_item = dlg.table.item(0, _COL_DATE)
    fp = date_item.data(FP_ROLE)
    dlg._forget_row(date_item)
    # The callback fired for exactly that fingerprint and the table shrank to 1 row.
    assert forgotten == [fp], forgotten
    assert dlg.table.rowCount() == 1


def test_dialog_forget_declined_leaves_index(monkeypatch):
    """Declining the forget confirm (No) is a no-op: the callback never fires, the row stays."""
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.No))
    index = {"version": library.VERSION, "entries": [_entry("GX010001")]}
    forgotten = []
    dlg = LibraryDialog(index, open_recording=lambda _p: None,
                        forget_recording=lambda e: forgotten.append(e) or index,
                        clear_library=lambda: index)
    dlg._forget_row(dlg.table.item(0, _COL_DATE))
    assert forgotten == []
    assert dlg.table.rowCount() == 1


def test_dialog_clear_routes_through_callback_and_empties(monkeypatch):
    """Clear library: the dialog confirms, calls the injected clear callback, and re-renders to the
    empty state (0 rows), the Clear button disabling itself when empty."""
    _auto_confirm(monkeypatch)
    index = {"version": library.VERSION, "entries": [_entry("GX010001")]}
    cleared = []

    def clear():
        cleared.append(True)
        return library.empty_index()

    dlg = LibraryDialog(index, open_recording=lambda _p: None,
                        forget_recording=lambda e: index, clear_library=clear)
    assert dlg.clear_btn.isEnabled() is True
    dlg._on_clear_library()
    assert cleared == [True]
    assert dlg.table.rowCount() == 0
    assert dlg.clear_btn.isEnabled() is False


def test_dialog_without_privacy_callbacks_is_browse_only():
    """Injecting no forget/clear callbacks degrades cleanly: no Clear button, browse-only (the
    bare construction the pre-existing test_library uses must keep working)."""
    index = {"version": library.VERSION, "entries": [_entry("GX010001")]}
    dlg = LibraryDialog(index, open_recording=lambda _p: None)
    assert getattr(dlg, "clear_btn", None) is None
    assert dlg.table.rowCount() == 1


# ============================================================ runner
def _run_with_monkeypatch(fn):
    saved = []

    class _MP:
        def setattr(self, obj, name, value):
            saved.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

    try:
        fn(_MP())
    finally:
        for obj, name, old in reversed(saved):
            setattr(obj, name, old)


def _run_all():
    import inspect
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        if "monkeypatch" in inspect.signature(fn).parameters:
            _run_with_monkeypatch(fn)
        else:
            fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} data-safety tests passed")


if __name__ == "__main__":
    _run_all()
