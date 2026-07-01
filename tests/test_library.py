"""Tests for the session library (studio.library + studio.library_dialog, F8).

The library is a local index of analyzed recordings — one entry per recording fingerprint
(the CHAPTER-INVARIANT recording identity: GoPro prefix + recording number) with track / date /
lap count / best / theoretical / paths — stored in the macOS app-support dir and surfaced by the
File ▸ Library… dialog with a per-track PB-progression mini-chart.

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
    TRACK_ROLE,
    LibraryDialog,
    _entry_junk,
)


# ------------------------------------------------------------------ helpers
def _entry(stem, *, track="Daytona MK", date="2024-05-01", laps=12,
           best=68.4, theo=67.9, paths=None):
    """Build a valid library entry with a fingerprint derived from the (chapter-invariant) stem.
    (The signature dropped the old per-recording duration arg — the fingerprint no longer uses
    it; tests that need DISTINCT recordings pass distinct stems.)"""
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


# ============================================================ pure index (no Qt)

def test_fingerprint_is_chapter_invariant_recording_identity():
    """The fingerprint is the recording's CHAPTER-INVARIANT identity (GoPro prefix + recording
    number): the per-chapter index is stripped so any chapter of one recording fingerprints the
    same, and the media duration is NOT in the key (it differs between a single-chapter and a full
    chaptered open of the SAME recording, the bug this fixes)."""
    # Every chapter of recording 0062 → the SAME key.
    assert library.fingerprint("GX010062") == "GX0062"
    assert library.fingerprint("GX020062") == "GX0062"
    assert library.fingerprint("GX030062") == "GX0062"
    # Prefix is upper-cased / honoured; a different recording number is a different recording.
    assert library.fingerprint("gx010062") == "GX0062"
    assert library.fingerprint("GH010062") == "GH0062"
    assert library.fingerprint("GX010060") != library.fingerprint("GX010062")
    # A non-GoPro stem (e.g. the bundled sample) keys on itself — never collides with a recording.
    assert library.fingerprint("hero6") == "hero6"
    assert library.fingerprint("") == ""


def test_fingerprint_single_vs_full_chaptered_open_collapse():
    """The CORE dedup contract: opening recording 0060 as a single chapter (GX010060) and as its
    full chaptered chain (first chapter GX010060) produce the SAME fingerprint — so one recording
    is ONE library row, not two (the duration-in-key splitting bug)."""
    single = library.fingerprint("GX010060")            # first stem of a 1-chapter open
    full = library.fingerprint("GX010060")              # first stem of the full chain open
    assert single == full == "GX0060"


def test_save_load_roundtrip_bit_exact():
    """json floats are written with repr (the shortest EXACT double string), so best/theoretical
    survive save→load bit-identically, and a re-save of the loaded index is byte-identical."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        idx = library.empty_index()
        library.upsert(idx, _entry("GX010060", best=68.408, theo=67.901))
        library.save(idx, p)
        back = library.load(p)
        assert back["version"] == 1
        assert len(back["entries"]) == 1
        e = back["entries"][0]
        assert e["best"] == 68.408 and e["theoretical"] == 67.901   # exact float equality
        assert e["fingerprint"] == "GX0060"
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
    # First open: single chapter (first stem GX010062).
    library.upsert(idx, _entry("GX010062", laps=10, best=70.0,
                               paths=["/m/GX010062.MP4"]))
    assert len(idx["entries"]) == 1
    # Re-open the SAME recording as the FULL chaptered chain: the first chapter's stem is still
    # GX010062 → SAME fingerprint, different paths + better best → updates in place, NO duplicate.
    library.upsert(idx, _entry("GX010062", laps=10, best=68.1,
                               paths=["/m/GX010062.MP4", "/m/GX020062.MP4"]))
    assert len(idx["entries"]) == 1, idx["entries"]
    e = idx["entries"][0]
    assert e["best"] == 68.1
    assert e["paths"] == ["/m/GX010062.MP4", "/m/GX020062.MP4"]
    # A genuinely different recording appends.
    library.upsert(idx, _entry("GX010060"))
    assert len(idx["entries"]) == 2
    # And the re-open of the FIRST keeps its position (index 0), not reshuffled to the end.
    library.upsert(idx, _entry("GX010062", best=67.5,
                               paths=["/m/GX010062.MP4", "/m/GX020062.MP4"]))
    assert len(idx["entries"]) == 2
    assert idx["entries"][0]["fingerprint"] == "GX0062"
    assert idx["entries"][0]["best"] == 67.5


def test_upsert_and_save_no_duplicate_across_loads():
    """End-to-end through the file: two upsert_and_save of the same fingerprint leave ONE entry
    on disk (the app's per-load call is idempotent for a re-opened recording)."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        library.upsert_and_save(_entry("GX010060", best=70.0), p)
        library.upsert_and_save(_entry("GX010060", best=68.0), p)
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
    good = _entry("GX010060")
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


def test_load_drops_only_malformed_entries_keeps_valid_history():
    """ENTRY-tolerant load (E4): one malformed entry must NOT discard the whole index — the
    valid recordings' history SURVIVES and only the bad row is dropped. Regression for the
    data-loss bug where one corrupt entry reset the file to empty and the next save persisted
    that loss permanently. (FILE-level garbage still resets to empty — covered separately.)"""
    good_a = _entry("GX010060", track="Daytona MK", best=68.4)
    good_b = _entry("GX010061", track="Sonoma", best=71.2)
    good_c = _entry("GX010062", track="Buttonwillow", best=99.9)
    fps = {e["fingerprint"] for e in (good_a, good_b, good_c)}
    # A wire-shaped index with the three valid entries plus ONE structurally-broken row
    # (negative lap_count) sandwiched in the middle.
    bad = {"fingerprint": "GX9999", "stem": "GX019999", "track": "x",
           "date": None, "lap_count": -1, "best": None, "theoretical": None, "paths": []}
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        with open(p, "w") as f:
            json.dump({"version": 1, "entries": [good_a, bad, good_b, good_c]}, f)
        idx = library.load(p)
        survivors = {e["fingerprint"] for e in idx["entries"]}
        # The three valid recordings survive; ONLY the malformed row is dropped.
        assert survivors == fps, survivors
        assert "GX9999" not in survivors
        assert len(idx["entries"]) == 3
        # And a re-save of the healed index keeps exactly the survivors (no resurrection of
        # the bad row, no loss of the good ones) — the loss is NOT persisted.
        library.save(idx, p)
        assert {e["fingerprint"] for e in library.load(p)["entries"]} == fps


def test_load_drops_all_when_every_entry_malformed():
    """The boundary of the entry-tolerant load: if EVERY entry is malformed, the survivors set
    is empty — but this is the empty-entries outcome, NOT a file-level reset. The file stayed a
    valid version-1 dict, so the contract is 'keep the (zero) valid entries', not 'reject file'."""
    bad1 = {"fingerprint": "", "stem": "x", "lap_count": 1, "paths": []}   # empty fingerprint
    bad2 = {"fingerprint": "x", "stem": 7, "lap_count": 1, "paths": []}    # stem not str
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        with open(p, "w") as f:
            json.dump({"version": 1, "entries": [bad1, bad2]}, f)
        assert library.load(p) == {"version": 1, "entries": []}


def test_load_file_level_garbage_still_resets_to_empty():
    """FILE-level corruption (not a dict / wrong version / non-list entries) still resets the
    WHOLE index to empty — the entry-tolerant change is scoped to individual entries only; an
    untrustworthy top-level shape can't be partially salvaged."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        for body in ("{ not json", "[]", '{"version": 2, "entries": []}',
                     '{"version": 1, "entries": 3}'):
            with open(p, "w") as f:
                f.write(body)
            assert library.load(p) == {"version": 1, "entries": []}, body


def test_null_track_date_best_roundtrip():
    """An unknown-track / GPS5 (no date) / no-valid-lap recording stores nulls and round-trips —
    the entry is still valid and listable."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        e = _entry("hero6", track=None, date=None, laps=0, best=None, theo=None)
        library.upsert_and_save(e, p)
        back = library.load(p)["entries"][0]
        assert back["track"] is None and back["date"] is None
        assert back["best"] is None and back["theoretical"] is None
        assert back["lap_count"] == 0


def test_pb_series_per_track_sorted_and_filtered():
    """pb_series returns (date, best) for ONE track, sorted ascending by date, dropping entries
    with no date or no best, and excluding other tracks."""
    idx = library.empty_index()
    library.upsert(idx, _entry("A", track="MK", date="2024-06-01", best=69.0))
    library.upsert(idx, _entry("B", track="MK", date="2024-05-01", best=70.0))
    library.upsert(idx, _entry("C", track="MK", date="2024-07-01", best=68.0))
    library.upsert(idx, _entry("D", track="MK", date=None, best=60.0))     # no date → drop
    library.upsert(idx, _entry("E", track="MK", date="2024-08-01", best=None))  # no best
    library.upsert(idx, _entry("F", track="OtherTrack", date="2024-06-01", best=50.0))
    series = library.pb_series(idx, "MK")
    assert series == [("2024-05-01", 70.0), ("2024-06-01", 69.0), ("2024-07-01", 68.0)]
    assert library.pb_series(idx, "Unknown") == []


def test_app_support_path_uses_patched_seam(monkeypatch):
    """library_path() resolves through _app_support_dir — patching that seam (the test idiom)
    fully diverts reads/writes away from the user's real ~/Library."""
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(library, "_app_support_dir", lambda: d)
        assert library.library_path() == os.path.join(d, "library.json")
        library.upsert_and_save(_entry("GX010060"))   # no explicit path → patched dir
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
    present = _entry("GX010060", track="MK", date="2024-05-01", best=70.0,
                     paths=tmp_present_paths)
    missing = _entry("GX010062", track="MK", date="2024-06-01", best=68.0,
                     paths=["/definitely/missing/GX010062.MP4"])
    library.upsert(idx, present)
    library.upsert(idx, missing)
    return idx, present["fingerprint"], missing["fingerprint"]


def _row_with_date(dlg, date_text):
    """The table row index whose DATE cell renders `date_text` (the table is sorted, so insertion
    order ≠ row order — find by value)."""
    return next(r for r in range(dlg.table.rowCount())
               if dlg.table.item(r, _COL_DATE).text() == date_text)


def test_dialog_lists_both_entries_sorted():
    """The dialog lists every entry; sorted DESCENDING by date (newest first) the missing
    (2024-06-01) row is above the present (2024-05-01) row."""
    with tempfile.NamedTemporaryFile(suffix=".MP4") as real:
        idx, _, _ = _two_entry_index([real.name])
        dlg = LibraryDialog(idx, _OpenSpy())
        assert dlg.table.rowCount() == 2
        # Row 0 under the default newest-first date sort is the LATER date.
        assert dlg.table.item(0, _COL_DATE).text() == "2024-06-01"
        assert dlg.table.item(1, _COL_DATE).text() == "2024-05-01"
        # Best column carries the numeric sort key (seconds), so it orders by value.
        assert dlg.table.item(1, _COL_BEST).data(NUM_ROLE) == 70.0
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
        # Select the present row (the 2024-05-01 one — found by value, not a fixed row index).
        dlg.table.selectRow(_row_with_date(dlg, "2024-05-01"))
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
        present_row = _row_with_date(dlg, "2024-05-01")   # the present row (found by value)
        dlg.table.selectRow(present_row)
        dlg.table.itemDoubleClicked.emit(dlg.table.item(present_row, _COL_DATE))
        assert spy.calls == [[real.name]]
        dlg.deleteLater()


def test_dialog_pb_chart_plots_best_vs_date():
    """The PB mini-chart plots best-vs-date for the selected row's track. Two MK sessions →
    two points, x ascending by date, y the best laps."""
    idx = library.empty_index()
    library.upsert(idx, _entry("A", track="MK", date="2024-05-01", best=70.0,
                               paths=[]))
    library.upsert(idx, _entry("B", track="MK", date="2024-06-01", best=68.0,
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
    dormant/safe default. The PB chart shows its empty-state message rather than bare axes."""
    dlg = LibraryDialog(library.empty_index(), _OpenSpy())
    assert dlg.table.rowCount() == 0
    assert not dlg.open_btn.isEnabled()
    xs, ys = dlg._pb_curve.getData()
    assert (xs is None or len(xs) == 0)
    assert dlg._pb_empty.isVisible()                 # empty-state shown, not bare placeholder axes
    dlg.deleteLater()


# --------------------------------------------------- junk-row quarantine + auto-select + empty-state

def test_entry_junk_classification():
    """A row is JUNK (quarantined) iff it has no track OR no laps; a real recording
    (track + laps) is not."""
    assert _entry_junk(_entry("hero6", track=None, laps=0))          # no track AND no laps
    assert _entry_junk(_entry("GX010060", track=None))               # no track
    assert _entry_junk(_entry("GX010060", laps=0))                   # no laps
    assert not _entry_junk(_entry("GX010060", track="MK", laps=5))   # a real recording


def test_dialog_quarantines_junk_row_and_does_not_select_it():
    """A user's existing library.json may carry a JUNK row (null track / 0 laps — e.g. the legacy
    bundled-sample row). The dialog greys + disables it, never auto-selects it, and the auto-selected
    row is instead the real recording — so the dialog renders cleanly without manual cleanup."""
    with tempfile.NamedTemporaryFile(suffix=".MP4") as real:
        idx = library.empty_index()
        # A real recording (present file) + a junk row (null track, 0 laps).
        library.upsert(idx, _entry("GX010060", track="MK", date="2024-05-01", best=70.0,
                                   laps=8, paths=[real.name]))
        library.upsert(idx, _entry("hero6", track=None, date=None, best=None, theo=None,
                                   laps=0, paths=[real.name]))   # present file, but no track/laps
        dlg = LibraryDialog(idx, _OpenSpy())
        junk_row = next(r for r in range(dlg.table.rowCount())
                        if dlg.table.item(r, _COL_TRACK).text().startswith("unknown track"))
        junk_date = dlg.table.item(junk_row, _COL_DATE)
        # Quarantined: greyed + not selectable/enabled, labelled "(no laps)", flagged MISSING_ROLE.
        assert not (junk_date.flags() & Qt.ItemIsEnabled)
        assert not (junk_date.flags() & Qt.ItemIsSelectable)
        assert bool(junk_date.data(MISSING_ROLE))
        assert "(no laps)" in dlg.table.item(junk_row, _COL_TRACK).text()
        # Auto-selection landed on the REAL recording (track MK), NOT the junk row.
        sel = dlg._selected_date_item()
        assert sel is not None and sel.data(TRACK_ROLE) == "MK"
        assert dlg.open_btn.isEnabled()                     # the selected row is openable
        dlg.deleteLater()


def test_dialog_autoselects_most_recent_usable_row():
    """Auto-selection picks the most recent USABLE recording (newest-first sort, first non-junk
    present row) — so the PB chart opens with data, never on the earliest/legacy junk row."""
    with tempfile.NamedTemporaryFile(suffix=".MP4") as real:
        idx = library.empty_index()
        library.upsert(idx, _entry("GX010060", track="MK", date="2024-05-01", best=71.0,
                                   paths=[real.name]))
        library.upsert(idx, _entry("GX010062", track="MK", date="2024-07-01", best=68.0,
                                   paths=[real.name]))   # the LATER session
        dlg = LibraryDialog(idx, _OpenSpy())
        sel = dlg._selected_date_item()
        assert sel is not None and sel.text() == "2024-07-01"   # newest usable row selected
        dlg.deleteLater()


def test_dialog_all_junk_selects_nothing_and_shows_empty_state():
    """If EVERY row is junk/quarantined, nothing is auto-selected, Open stays disabled, and the PB
    chart shows its empty-state (not bare placeholder axes)."""
    idx = library.empty_index()
    library.upsert(idx, _entry("hero6", track=None, date=None, best=None, theo=None,
                               laps=0, paths=["/definitely/missing.MP4"]))
    dlg = LibraryDialog(idx, _OpenSpy())
    assert dlg._selected_date_item() is None
    assert not dlg.open_btn.isEnabled()
    assert dlg._pb_empty.isVisible()
    dlg.deleteLater()


def test_dialog_pb_empty_state_when_fewer_than_two_points():
    """The PB chart shows an in-chart empty-state (NOT bare axes) for a track with <2 dated bests,
    and HIDES it once there are >=2 points to chart."""
    idx = library.empty_index()
    library.upsert(idx, _entry("A", track="MK", date="2024-05-01", best=70.0, paths=[]))
    dlg = LibraryDialog(idx, _OpenSpy())
    dlg._show_pb("MK")                       # exactly 1 dated best → empty-state visible
    assert dlg._pb_empty.isVisible()
    xs, _ = dlg._pb_curve.getData()
    assert len(xs) == 1                      # the lone marker IS drawn (framed), not cleared
    # A null track also shows the empty-state.
    dlg._show_pb(None)
    assert dlg._pb_empty.isVisible()
    # Add a second MK session → the empty-state hides and the line draws.
    library.upsert(idx, _entry("B", track="MK", date="2024-06-01", best=68.0, paths=[]))
    dlg._show_pb("MK")
    assert not dlg._pb_empty.isVisible()
    xs2, _ = dlg._pb_curve.getData()
    assert len(xs2) == 2
    dlg.deleteLater()


# ===================================== Session.library_entry + app skip (pacer; skipped without it)
# These exercise the REAL Session.library_entry (absolute paths) and the app's _update_library skip
# (0-lap / bundled-sample rows are NOT indexed). They import the pacer-backed studio.session /
# studio.app, so they run under CTest (pacer on PYTHONPATH) and no-op in the standalone, pacer-free
# runner — keeping the pure-index + dialog tests above importable anywhere.
def _pacer_available() -> bool:
    try:
        import pacer  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 — any import failure means "no built bindings here"
        return False


def test_library_entry_stores_absolute_paths():
    """Session.library_entry stores ABSOLUTE chapter paths (os.path.abspath), so the dialog's
    file-exists check is independent of the process cwd. A 0062 recording fingerprints to GX0062."""
    if not _pacer_available():
        print("skip test_library_entry_stores_absolute_paths (no pacer)")
        return
    from studio.session import Session
    s = Session.__new__(Session)        # bare; seed only what library_entry reads
    s._valid_cache = [0, 1, 2]
    s._best_cache = 1
    s.track_name = "Daytona MK"
    s.laps = type("L", (), {"lap_time": staticmethod(lambda i: 68.4)})()
    s.session_date = lambda: "2024-05-01"
    s.theoretical_best = lambda: 67.9
    # A relative path → the entry must store it absolute.
    rel = os.path.join("subdir", "GX010062.MP4")
    entry = Session.library_entry(s, [rel])
    assert entry["fingerprint"] == "GX0062"          # chapter-invariant identity
    assert entry["paths"] == [os.path.abspath(rel)]  # absolute, cwd-independent
    assert os.path.isabs(entry["paths"][0])
    assert entry["track"] == "Daytona MK" and entry["best"] == 68.4


def test_update_library_skips_zero_lap_and_bundled_sample(monkeypatch):
    """The app's _update_library does NOT index a 0-lap open or the bundled DEFAULT_SAMPLE — so a
    no-file launch (or an unsegmented recording) can't leave a permanent junk row in the library."""
    if not _pacer_available():
        print("skip test_update_library_skips_zero_lap_and_bundled_sample (no pacer)")
        return
    from studio import app as studio_app
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(library, "_app_support_dir", lambda: d)
        upserts = []
        monkeypatch.setattr(library, "upsert_and_save",
                            lambda entry, *a, **k: upserts.append(entry))
        win = studio_app.StudioWindow.__new__(studio_app.StudioWindow)

        # A 0-lap session → skipped (no valid laps).
        win.session = type("S", (), {"valid_lap_ids": staticmethod(lambda: [])})()
        studio_app.StudioWindow._update_library(win, ["/m/GX010060.MP4"])
        assert upserts == []

        # The bundled sample → skipped even with laps (it's not a real analysis recording).
        win.session = type("S", (), {
            "valid_lap_ids": staticmethod(lambda: [0, 1]),
            "library_entry": staticmethod(lambda paths: _entry("hero6", track=None, laps=0)),
        })()
        studio_app.StudioWindow._update_library(win, [studio_app.DEFAULT_SAMPLE])
        assert upserts == []

        # A real recording WITH laps → indexed. Both timing axes are read by _update_library for the
        # PB-moment gate (a provisional/unverified start line OR a data-quality-degraded clock never
        # celebrates); Verified + not-degraded here so the index path runs normally.
        from types import SimpleNamespace
        win.session = type("S", (), {
            "valid_lap_ids": staticmethod(lambda: [0, 1]),
            "timing_verified": True,
            "timing_quality": SimpleNamespace(degraded=False),
            "library_entry": staticmethod(
                lambda paths: _entry("GX010060", track="MK", laps=2)),
        })()
        studio_app.StudioWindow._update_library(win, ["/m/GX010060.MP4"])
        assert len(upserts) == 1 and upserts[0]["fingerprint"] == "GX0060"


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
