"""Tests for the session library (studio.library + studio.library_dialog, F8).

The library is a local index of analyzed recordings — one entry per recording fingerprint
(first-chapter stem + total duration) with track / date / lap count / best / theoretical / paths
— stored in the macOS app-support dir and surfaced by the File ▸ Library… dialog with a per-track
PB-progression mini-chart.

CRITICAL: every test here points the index at a TEMP directory by monkeypatching
``library._app_support_dir`` (the single seam) — the suite NEVER touches the user's real
``~/Library/Application Support/pacer/``.

Covered:
  * pure index (no Qt): schema round-trip + float-repr bit-exactness; the fingerprint identity
    + upsert-replaces-not-duplicates rule; corrupt/invalid index → a safe empty index (self-heal),
    then a clean write heals it; atomic write creates the app-support dir; pb_series extraction
    (per-track, dated bests, sorted) and its drop-undated/no-best filtering;
  * the dialog (offscreen Qt, synthetic index dicts — the dialog is pacer-free): lists every
    entry sorted; selecting a row enables Open and routes through the injected open callback (a
    spy); a missing-file row is greyed + disabled + not openable; double-click opens; and the PB
    mini-chart plots best-vs-date for the selected row's track.

Run: python tests/test_library.py   (no pacer, no telemetry file)
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The dialog half needs a QApplication (QTableWidget / pyqtgraph); create one offscreen so the
# whole file runs headless. The pure-index half doesn't touch Qt.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import library  # noqa: E402
from studio.library_dialog import (  # noqa: E402
    _COL_BEST,
    _COL_DATE,
    _COL_TRACK,
    MISSING_ROLE,
    NUM_ROLE,
    LibraryDialog,
)


# ------------------------------------------------------------------ helpers
def _entry(stem, dur, *, track="Daytona MK", date="2024-05-01", laps=12,
           best=68.4, theo=67.9, paths=None):
    """Build a valid library entry with a fingerprint derived from (stem, dur)."""
    return {
        "fingerprint": library.fingerprint(stem, dur),
        "stem": stem,
        "track": track,
        "date": date,
        "lap_count": laps,
        "best": best,
        "theoretical": theo,
        "paths": paths if paths is not None else [f"/media/{stem}.MP4"],
    }


# ============================================================ pure index (no Qt)

def test_fingerprint_rounds_duration_and_is_canonical():
    """The fingerprint is ``stem|duration-to-0.1s``: 275.44 and 275.41 collapse to the same key
    (sub-0.1s jitter in a recomputed duration can't fork the identity), and it's a fixed
    one-decimal string (no float repr drift)."""
    assert library.fingerprint("GX010062", 275.44) == "GX010062|275.4"
    assert library.fingerprint("GX010062", 275.41) == "GX010062|275.4"
    assert library.fingerprint("GX010062", 275.0) == "GX010062|275.0"
    # A different stem or a >0.1s-different duration is a DIFFERENT recording.
    assert library.fingerprint("GX010060", 275.4) != library.fingerprint("GX010062", 275.4)
    assert library.fingerprint("GX010062", 275.4) != library.fingerprint("GX010062", 280.4)


def test_save_load_roundtrip_bit_exact():
    """json floats are written with repr (the shortest EXACT double string), so best/theoretical
    survive save→load bit-identically, and a re-save of the loaded index is byte-identical."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        idx = library.empty_index()
        library.upsert(idx, _entry("GX010060", 1100.25, best=68.408, theo=67.901))
        library.save(idx, p)
        back = library.load(p)
        assert back["version"] == 1
        assert len(back["entries"]) == 1
        e = back["entries"][0]
        assert e["best"] == 68.408 and e["theoretical"] == 67.901   # exact float equality
        assert e["fingerprint"] == "GX010060|1100.2"
        assert e["paths"] == ["/media/GX010060.MP4"]
        # A second save of the loaded index is byte-identical on disk (fully stable).
        p2 = os.path.join(d, "again.json")
        library.save(back, p2)
        with open(p) as f1, open(p2) as f2:
            assert f1.read() == f2.read()


def test_save_creates_app_support_dir():
    """save() creates a missing app-support directory (lazily, only on a write)."""
    with tempfile.TemporaryDirectory() as d:
        nested = os.path.join(d, "Library", "Application Support", "pacer", "library.json")
        assert not os.path.exists(os.path.dirname(nested))
        library.save(library.empty_index(), nested)
        assert os.path.exists(nested)


def test_upsert_replaces_same_fingerprint_in_place():
    """The NO-DUPLICATE rule: re-opening the same recording (same fingerprint) UPDATES its entry
    in place — count stays 1, position is kept, values are replaced. A different fingerprint
    appends."""
    idx = library.empty_index()
    # First open: single chapter.
    library.upsert(idx, _entry("GX010062", 275.4, laps=10, best=70.0,
                               paths=["/m/GX010062.MP4"]))
    assert len(idx["entries"]) == 1
    # Re-open the SAME recording (same stem+duration) as the FULL chaptered chain: same
    # fingerprint, different paths + better best → updates in place, NO duplicate.
    library.upsert(idx, _entry("GX010062", 275.4, laps=10, best=68.1,
                               paths=["/m/GX010062.MP4", "/m/GX020062.MP4"]))
    assert len(idx["entries"]) == 1, idx["entries"]
    e = idx["entries"][0]
    assert e["best"] == 68.1
    assert e["paths"] == ["/m/GX010062.MP4", "/m/GX020062.MP4"]
    # A genuinely different recording appends.
    library.upsert(idx, _entry("GX010060", 1100.0))
    assert len(idx["entries"]) == 2
    # And the re-open of the FIRST keeps its position (index 0), not reshuffled to the end.
    library.upsert(idx, _entry("GX010062", 275.4, best=67.5,
                               paths=["/m/GX010062.MP4", "/m/GX020062.MP4"]))
    assert len(idx["entries"]) == 2
    assert idx["entries"][0]["fingerprint"] == "GX010062|275.4"
    assert idx["entries"][0]["best"] == 67.5


def test_upsert_and_save_no_duplicate_across_loads():
    """End-to-end through the file: two upsert_and_save of the same fingerprint leave ONE entry
    on disk (the app's per-load call is idempotent for a re-opened recording)."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        library.upsert_and_save(_entry("GX010060", 1100.0, best=70.0), p)
        library.upsert_and_save(_entry("GX010060", 1100.0, best=68.0), p)
        idx = library.load(p)
        assert len(idx["entries"]) == 1
        assert idx["entries"][0]["best"] == 68.0


def test_load_missing_is_empty_index():
    """A missing file → a fresh empty index (NOT an error) — a first-ever run shows an empty
    library."""
    idx = library.load("/nonexistent/dir/library.json")
    assert idx == {"version": 1, "entries": []}


def test_load_corrupt_returns_empty_then_heals():
    """Every malformed shape → a safe EMPTY index (self-heal, same philosophy as the sidecar's
    revert guard); a fresh write over the garbage then heals it to a real index."""
    good = _entry("GX010060", 1100.0)
    fp = good["fingerprint"]
    bad_bodies = [
        "{ not json",                                            # not JSON at all
        "[]",                                                     # not an object
        '{"version": 2, "entries": []}',                         # unknown version
        '{"version": 1}',                                        # no entries list
        '{"version": 1, "entries": 3}',                          # entries not a list
        '{"version": 1, "entries": [{"stem": "x"}]}',            # entry has no fingerprint
        '{"version": 1, "entries": [{"fingerprint": "", "stem": "x", "lap_count": 1,'
        ' "paths": []}]}',                                       # empty fingerprint
        '{"version": 1, "entries": [{"fingerprint": "x", "stem": 7, "lap_count": 1,'
        ' "paths": []}]}',                                       # stem not a string
        '{"version": 1, "entries": [{"fingerprint": "x", "stem": "x", "lap_count": -1,'
        ' "paths": []}]}',                                       # negative lap count
        '{"version": 1, "entries": [{"fingerprint": "x", "stem": "x", "lap_count": true,'
        ' "paths": []}]}',                                       # bool lap count
        '{"version": 1, "entries": [{"fingerprint": "x", "stem": "x", "lap_count": 1,'
        ' "best": "fast", "paths": []}]}',                       # best not numeric
        '{"version": 1, "entries": [{"fingerprint": "x", "stem": "x", "lap_count": 1,'
        ' "best": NaN, "paths": []}]}',                          # best non-finite
        '{"version": 1, "entries": [{"fingerprint": "x", "stem": "x", "lap_count": 1,'
        ' "track": 7, "paths": []}]}',                           # track not str/null
        '{"version": 1, "entries": [{"fingerprint": "x", "stem": "x", "lap_count": 1,'
        ' "paths": "/m/x.MP4"}]}',                               # paths not a list
        '{"version": 1, "entries": [{"fingerprint": "x", "stem": "x", "lap_count": 1,'
        ' "paths": [7]}]}',                                      # path not a string
    ]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        for body in bad_bodies:
            with open(p, "w") as f:
                f.write(body)
            assert library.load(p) == {"version": 1, "entries": []}, body
        # Heal: a fresh upsert+save over the (last) garbage yields a clean, loadable index.
        library.upsert_and_save(good, p)
        idx = library.load(p)
        assert len(idx["entries"]) == 1 and idx["entries"][0]["fingerprint"] == fp
        # And the on-disk file is valid JSON with exactly the schema keys.
        with open(p) as f:
            raw = json.load(f)
        assert set(raw) == {"version", "entries"}
        assert set(raw["entries"][0]) == {
            "fingerprint", "stem", "track", "date", "lap_count", "best", "theoretical", "paths"}


def test_null_track_date_best_roundtrip():
    """An unknown-track / GPS5 (no date) / no-valid-lap recording stores nulls and round-trips —
    the entry is still valid and listable."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        e = _entry("hero6", 30.0, track=None, date=None, laps=0, best=None, theo=None)
        library.upsert_and_save(e, p)
        back = library.load(p)["entries"][0]
        assert back["track"] is None and back["date"] is None
        assert back["best"] is None and back["theoretical"] is None
        assert back["lap_count"] == 0


def test_pb_series_per_track_sorted_and_filtered():
    """pb_series returns (date, best) for ONE track, sorted ascending by date, dropping entries
    with no date or no best, and excluding other tracks."""
    idx = library.empty_index()
    library.upsert(idx, _entry("A", 100.0, track="MK", date="2024-06-01", best=69.0))
    library.upsert(idx, _entry("B", 200.0, track="MK", date="2024-05-01", best=70.0))
    library.upsert(idx, _entry("C", 300.0, track="MK", date="2024-07-01", best=68.0))
    library.upsert(idx, _entry("D", 400.0, track="MK", date=None, best=60.0))     # no date → drop
    library.upsert(idx, _entry("E", 500.0, track="MK", date="2024-08-01", best=None))  # no best
    library.upsert(idx, _entry("F", 600.0, track="OtherTrack", date="2024-06-01", best=50.0))
    series = library.pb_series(idx, "MK")
    assert series == [("2024-05-01", 70.0), ("2024-06-01", 69.0), ("2024-07-01", 68.0)]
    assert library.pb_series(idx, "Unknown") == []


def test_app_support_path_uses_patched_seam(monkeypatch):
    """library_path() resolves through _app_support_dir — patching that seam (the test idiom)
    fully diverts reads/writes away from the user's real ~/Library."""
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(library, "_app_support_dir", lambda: d)
        assert library.library_path() == os.path.join(d, "library.json")
        library.upsert_and_save(_entry("GX010060", 1100.0))   # no explicit path → patched dir
        assert os.path.exists(os.path.join(d, "library.json"))
        assert len(library.load()["entries"]) == 1            # default-path load sees it


# ============================================================ dialog (offscreen Qt)

class _OpenSpy:
    """Records the paths passed to the dialog's open callback (the app's _load)."""

    def __init__(self):
        self.calls = []

    def __call__(self, paths):
        self.calls.append(list(paths))


def _two_entry_index(tmp_present_paths):
    """An index with two entries: one PRESENT (paths exist on disk) and one MISSING. Returns
    (index, present_fingerprint, missing_fingerprint)."""
    idx = library.empty_index()
    present = _entry("GX010060", 1100.0, track="MK", date="2024-05-01", best=70.0,
                     paths=tmp_present_paths)
    missing = _entry("GX010062", 275.4, track="MK", date="2024-06-01", best=68.0,
                     paths=["/definitely/missing/GX010062.MP4"])
    library.upsert(idx, present)
    library.upsert(idx, missing)
    return idx, present["fingerprint"], missing["fingerprint"]


def test_dialog_lists_both_entries_sorted():
    """The dialog lists every entry; sorted ascending by date the present (2024-05-01) row is
    above the missing (2024-06-01) row."""
    with tempfile.NamedTemporaryFile(suffix=".MP4") as real:
        idx, _, _ = _two_entry_index([real.name])
        dlg = LibraryDialog(idx, _OpenSpy())
        assert dlg.table.rowCount() == 2
        # Row 0 after the default ascending date sort is the earlier date.
        assert dlg.table.item(0, _COL_DATE).text() == "2024-05-01"
        assert dlg.table.item(1, _COL_DATE).text() == "2024-06-01"
        # Best column carries the numeric sort key (seconds), so it orders by value.
        assert dlg.table.item(0, _COL_BEST).data(NUM_ROLE) == 70.0
        dlg.deleteLater()


def test_dialog_sort_by_best_orders_numerically():
    """Sorting the Best column ascending puts the fastest lap first (68.0 < 70.0) — numeric, not
    lexical."""
    with tempfile.NamedTemporaryFile(suffix=".MP4") as real:
        idx, _, _ = _two_entry_index([real.name])
        dlg = LibraryDialog(idx, _OpenSpy())
        dlg.table.sortItems(_COL_BEST, Qt.AscendingOrder)
        assert dlg.table.item(0, _COL_BEST).data(NUM_ROLE) == 68.0
        assert dlg.table.item(1, _COL_BEST).data(NUM_ROLE) == 70.0
        dlg.deleteLater()


def test_dialog_open_routes_through_callback():
    """Selecting a PRESENT row enables Open; clicking it calls the injected open callback with
    that recording's paths (the app passes _load → re-loads the recording)."""
    with tempfile.NamedTemporaryFile(suffix=".MP4") as real:
        idx, _, _ = _two_entry_index([real.name])
        spy = _OpenSpy()
        dlg = LibraryDialog(idx, spy)
        # Select the present row (the 2024-05-01 one — row 0 after the date sort).
        dlg.table.selectRow(0)
        assert dlg.open_btn.isEnabled()
        dlg.open_btn.click()
        assert spy.calls == [[real.name]], spy.calls
        dlg.deleteLater()


def test_dialog_missing_file_row_greyed_and_not_openable():
    """A missing-file row is greyed + disabled (not selectable), so Open never fires for it."""
    with tempfile.NamedTemporaryFile(suffix=".MP4") as real:
        idx, _, _ = _two_entry_index([real.name])
        spy = _OpenSpy()
        dlg = LibraryDialog(idx, spy)
        # Find the MISSING row (its date cell carries MISSING_ROLE True).
        missing_row = next(
            r for r in range(dlg.table.rowCount())
            if dlg.table.item(r, _COL_DATE).data(MISSING_ROLE))
        date_item = dlg.table.item(missing_row, _COL_DATE)
        # Greyed + not enabled/selectable across the row.
        assert not (date_item.flags() & Qt.ItemIsEnabled)
        assert not (date_item.flags() & Qt.ItemIsSelectable)
        assert "(file missing)" in dlg.table.item(missing_row, _COL_TRACK).text()
        # Even forcing the open path on the missing row is a no-op (guard in _open_selected).
        dlg.table.clearSelection()
        dlg.table.selectRow(missing_row)            # disabled rows don't actually select…
        dlg._open_selected()                        # …and the explicit guard blocks it anyway
        assert spy.calls == []
        dlg.deleteLater()


def test_dialog_double_click_opens_present_row():
    """Double-clicking a present row opens it (same path as the Open button)."""
    with tempfile.NamedTemporaryFile(suffix=".MP4") as real:
        idx, _, _ = _two_entry_index([real.name])
        spy = _OpenSpy()
        dlg = LibraryDialog(idx, spy)
        dlg.table.selectRow(0)                       # the present row
        dlg.table.itemDoubleClicked.emit(dlg.table.item(0, _COL_DATE))
        assert spy.calls == [[real.name]]
        dlg.deleteLater()


def test_dialog_pb_chart_plots_best_vs_date():
    """The PB mini-chart plots best-vs-date for the selected row's track. Two MK sessions →
    two points, x ascending by date, y the best laps."""
    idx = library.empty_index()
    library.upsert(idx, _entry("A", 100.0, track="MK", date="2024-05-01", best=70.0,
                               paths=[]))
    library.upsert(idx, _entry("B", 200.0, track="MK", date="2024-06-01", best=68.0,
                               paths=[]))
    dlg = LibraryDialog(idx, _OpenSpy())
    # Force the PB chart to the MK track and read the plotted series back.
    dlg._show_pb("MK")
    xs, ys = dlg._pb_curve.getData()
    assert list(ys) == [70.0, 68.0]                  # best laps in date order
    assert xs[0] < xs[1]                             # dates ascending on the time axis
    assert len(xs) == 2
    dlg.deleteLater()


def test_dialog_empty_index_shows_empty_library():
    """A missing/empty index → an empty dialog (no rows, Open disabled, PB chart empty) — the
    dormant/safe default."""
    dlg = LibraryDialog(library.empty_index(), _OpenSpy())
    assert dlg.table.rowCount() == 0
    assert not dlg.open_btn.isEnabled()
    xs, ys = dlg._pb_curve.getData()
    assert (xs is None or len(xs) == 0)
    dlg.deleteLater()


# ------------------------------------------------------------------ runner
def _run_all():
    import inspect
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        sig = inspect.signature(fn)
        if "monkeypatch" in sig.parameters:
            _run_with_monkeypatch(fn)
        else:
            fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} library tests passed")


def _run_with_monkeypatch(fn):
    """Minimal monkeypatch shim so the file runs standalone (no pytest needed) — sets attrs and
    restores them after, matching pytest's monkeypatch.setattr for the one test that uses it."""
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


if __name__ == "__main__":
    _run_all()
