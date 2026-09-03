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
    mini-chart plots best-vs-date for the selected row's track;
  * the dialog's honesty/consistency surfaces: the chart's axis renders lap times (not decimal
    seconds) through the same fmt_time the table uses; a filter matching nothing says so, counts
    what is on screen and drops the de-selected row's axis range; the Unknown-track filter bucket
    reaches null-track rows; a Track cell's tooltip carries its own elided label + the filename;
    and the header/clear-confirm plurals come from the module's own _plural.

Run: python tests/test_library.py   (no pacer, no telemetry file)
"""
import json
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The dialog half needs a QApplication (QTableWidget / pyqtgraph); create one offscreen so the
# whole file runs headless. The pure-index half doesn't touch Qt.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import library, prefs, theme  # noqa: E402
from studio._signal import fmt_time  # noqa: E402

# The dialog's SIZE tests measure a wrapped paragraph's height and a table's row height, and both
# are functions of the FONT — so measure against the app's real theme rather than Qt's default
# stack, or every number below describes a dialog that never ships. (At the dialog's own minimum
# the untuned stack shows 2.3 table rows where the shipped one shows 0.97.) Same reason
# tests/test_help_dialog.py registers the fonts before it measures a wrapped paragraph.
theme.register_fonts()
theme.apply_theme(_APP)

# The dialog now READS its remembered size from studio.prefs on construction and WRITES it back on
# close, so redirect that seam too, module-wide and before any dialog exists — same rule as the
# library index above: the suite never reads or writes the user's real app-support dir. (The
# TemporaryDirectory object is held at module scope so its finalizer removes the dir at exit.)
_PREFS_TMP = tempfile.TemporaryDirectory(prefix="pacer-test-prefs-")
prefs._app_support_dir = lambda: _PREFS_TMP.name

from studio.library_dialog import (  # noqa: E402
    _ALL_TRACKS,
    _COL_BEST,
    _COL_DATE,
    _COL_TRACK,
    _DEFAULT_SIZE,
    _MIN_BROWSABLE_H,
    _PB_PLOT_MAX_H,
    _SCREEN_MARGIN,
    _UNKNOWN_TRACK,
    MISSING_ROLE,
    NUM_ROLE,
    PRIVACY_NOTE,
    TRACK_ROLE,
    LibraryDialog,
    _entry_junk,
    _fit_to_screen,
)


# ------------------------------------------------------------------ helpers
def _entry(stem, *, track="Daytona MK", date="2024-05-01", laps=12,
           best=68.4, theo=67.9, paths=None,
           verified=True, degraded=False, dropout=False):
    """Build a valid library entry with a fingerprint derived from the (chapter-invariant) stem.
    (The signature dropped the old per-recording duration arg — the fingerprint no longer uses
    it; tests that need DISTINCT recordings pass distinct stems.) The v2 trust flags default to
    TRUSTWORTHY (verified, not degraded, no dropout) so most tests get a PB-eligible entry; a test
    that wants an EXCLUDED entry flips one flag."""
    return {
        "fingerprint": library.fingerprint(stem),
        "stem": stem,
        "track": track,
        "date": date,
        "lap_count": laps,
        "best": best,
        "theoretical": theo,
        "verified": verified,
        "degraded": degraded,
        "dropout": dropout,
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
        assert back["version"] == library.VERSION
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
    assert idx == {"version": library.VERSION, "entries": []}


def test_load_corrupt_returns_empty_then_heals():
    """Every malformed shape → a safe EMPTY index (self-heal, same philosophy as the sidecar's
    revert guard); a fresh write over the garbage then heals it to a real index."""
    good = _entry("GX010060")
    fp = good["fingerprint"]
    bad_bodies = [
        "{ not json",                                            # not JSON at all
        "[]",                                                     # not an object
        '{"entries": []}',                                       # missing version (untrustworthy)
        '{"version": "one", "entries": []}',                     # non-int version (untrustworthy)
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
            assert library.load(p) == {"version": library.VERSION, "entries": []}, body
        # Heal: a fresh upsert+save over the (last) garbage yields a clean, loadable index.
        library.upsert_and_save(good, p)
        idx = library.load(p)
        assert len(idx["entries"]) == 1 and idx["entries"][0]["fingerprint"] == fp
        # And the on-disk file is valid JSON with exactly the schema keys.
        with open(p) as f:
            raw = json.load(f)
        assert set(raw) == {"version", "entries"}
        assert set(raw["entries"][0]) == {
            "fingerprint", "stem", "track", "date", "lap_count", "best", "theoretical",
            "verified", "degraded", "dropout", "paths"}


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
        assert library.load(p) == {"version": library.VERSION, "entries": []}


def test_load_file_level_garbage_still_resets_to_empty():
    """FILE-level corruption (not a dict / missing-or-non-int version / non-list entries) still
    resets the WHOLE index to empty — the entry-tolerant change is scoped to individual entries
    only; an untrustworthy top-level shape can't be partially salvaged. (A version MISMATCH is a
    DIFFERENT case — migrated / best-effort, NOT wiped — covered in the migration tests below.)"""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        for body in ("{ not json", "[]", '{"entries": []}',
                     '{"version": true, "entries": []}',
                     '{"version": 1, "entries": 3}'):
            with open(p, "w") as f:
                f.write(body)
            assert library.load(p) == {"version": library.VERSION, "entries": []}, body


# ------------------------------------------------------- version-safe migration + backup (data loss)
def test_older_version_is_migrated_not_wiped():
    """THE LANDMINE: an OLDER on-disk version must NOT be treated as corruption. Its analyzed
    history is MIGRATED forward (entries preserved) and re-stamped to the current VERSION — not
    silently discarded behind a warning no one sees."""
    good_a = _entry("GX010060", track="Daytona MK", best=68.4)
    good_b = _entry("GX010061", track="Sonoma", best=71.2)
    fps = {e["fingerprint"] for e in (good_a, good_b)}
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        with open(p, "w") as f:
            json.dump({"version": 0, "entries": [good_a, good_b]}, f)  # an older schema
        idx = library.load(p)
        # Every entry survived and the index is re-stamped to the current version.
        assert idx["version"] == library.VERSION
        assert {e["fingerprint"] for e in idx["entries"]} == fps
        # And a subsequent save writes the migrated (current-version) file back — no data lost.
        library.save(idx, p)
        assert {e["fingerprint"] for e in library.load(p)["entries"]} == fps


def test_newer_version_is_loaded_best_effort_and_backed_up_before_overwrite():
    """A NEWER on-disk version (a downgrade) is loaded BEST-EFFORT (keep valid entries, ignore
    unknown fields) rather than wiped, AND the original newer bytes are copied to a .bak sidecar
    BEFORE any save would overwrite them — a downgrade can never silently destroy history."""
    good = _entry("GX010060", track="Daytona MK", best=68.4)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        # A future schema (version 99) with an unknown extra field the current build ignores.
        newer = {"version": 99, "entries": [good], "future_field": {"a": 1}}
        original_bytes = json.dumps(newer, indent=2).encode("utf-8")
        with open(p, "wb") as f:
            f.write(original_bytes)
        # Best-effort load: the valid entry survives, re-stamped down to the current version.
        idx = library.load(p)
        assert idx["version"] == library.VERSION
        assert [e["fingerprint"] for e in idx["entries"]] == [good["fingerprint"]]
        # Now a save (as the app would do post-load) must BACK UP the newer file first.
        library.save(idx, p)
        bak = p + ".bak"
        assert os.path.exists(bak), "the newer file must be backed up before overwrite"
        with open(bak, "rb") as f:
            assert f.read() == original_bytes  # the .bak holds the ORIGINAL newer bytes verbatim


def test_unparseable_file_is_backed_up_before_overwrite():
    """Genuine corruption (unparseable JSON) still resets to empty on load, but the corrupt bytes
    are preserved to a .bak sidecar before the next save overwrites them — nothing is silently
    lost even in the true-corruption path."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        corrupt = b"{ this is not valid json at all "
        with open(p, "wb") as f:
            f.write(corrupt)
        assert library.load(p) == {"version": library.VERSION, "entries": []}  # corruption -> empty
        library.upsert_and_save(_entry("GX010060"), p)           # the heal write
        bak = p + ".bak"
        assert os.path.exists(bak)
        with open(bak, "rb") as f:
            assert f.read() == corrupt  # the corrupt original is preserved verbatim


def test_healthy_save_does_not_create_a_backup():
    """A normal save over a healthy current-version file must NOT churn out a .bak (backup is only
    for the un-round-trippable corrupt/newer cases) — the everyday upsert stays backup-free."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        library.upsert_and_save(_entry("GX010060"), p)  # first write (no prior file)
        library.upsert_and_save(_entry("GX010061"), p)  # overwrite a healthy file
        assert not os.path.exists(p + ".bak")


def test_clear_backs_up_the_index_before_wiping_it():
    """QA W7-04: "Clear library" is the ONE destructive act a user can reach from the UI, and it was
    the one write path with no copy — save()'s backup hook fires only for an unparseable/newer file,
    which a deliberate wipe of a healthy index is not. clear() must copy the index to library.json.bak
    FIRST, so a mis-click is recoverable."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        library.upsert_and_save(_entry("GX010060", track="MK", best=68.4), p)
        library.upsert_and_save(_entry("GX010061", track="MK", best=69.1), p)
        before = library.load(p)["entries"]
        assert len(before) == 2

        library.clear(p)

        assert library.load(p)["entries"] == []                  # the wipe still happens
        bak = library.backup_path(p)
        assert os.path.exists(bak), "the wipe kept no copy of the index"
        assert library.load(bak)["entries"] == before            # …and the copy is the whole history


def test_clear_with_no_index_yet_is_a_clean_no_op():
    """Clearing a library that was never written must not crash and must not manufacture an empty
    .bak (a backup of nothing would make the Restore control offer to restore nothing)."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        library.clear(p)
        assert library.load(p)["entries"] == []
        assert not os.path.exists(library.backup_path(p))
        assert library.backup_summary(p) is None


def test_restore_brings_back_a_cleared_library():
    """The other half of "Back up…": restore() puts the .bak back, so the whole analyzed history
    survives a mis-clicked Clear library. Also proves the everyday upsert AFTER a clear does not
    clobber the backup — the way back stays open once the user carries on working."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        for stem in ("GX010060", "GX010061", "GX010062"):
            library.upsert_and_save(_entry(stem, track="MK"), p)
        original = library.load(p)["entries"]
        library.clear(p)
        library.upsert_and_save(_entry("GX010070", track="MK"), p)   # a healthy save must not churn
        assert library.load(library.backup_path(p))["entries"] == original

        restored = library.restore(p)

        assert [e["fingerprint"] for e in restored["entries"]] == \
               [e["fingerprint"] for e in original]
        assert library.load(p)["entries"] == original            # …and it is on disk, not just returned


def test_restore_swaps_so_it_can_itself_be_taken_back():
    """A restore is destructive in the other direction, so it SWAPS: the index it replaces becomes
    the new backup. Restoring twice therefore returns to where you started — the control can't strand
    a user who clicked it by mistake."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        library.upsert_and_save(_entry("GX010060", track="MK"), p)
        library.clear(p)
        library.upsert_and_save(_entry("GX010099", track="MK"), p)   # a NEW, different library
        current = library.load(p)["entries"]

        library.restore(p)                                       # -> the old one-entry library
        assert [e["fingerprint"] for e in library.load(p)["entries"]] == ["GX0060"]
        assert library.load(library.backup_path(p))["entries"] == current   # the replaced one is kept

        library.restore(p)                                       # …and swapping back works
        assert [e["fingerprint"] for e in library.load(p)["entries"]] == ["GX0099"]


def test_restore_refuses_when_there_is_nothing_to_restore():
    """restore() must never replace a live library with nothing — that would BE the data loss it
    exists to undo. A missing backup, and an empty one, both leave the current index untouched."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        library.upsert_and_save(_entry("GX010060", track="MK"), p)
        kept = library.load(p)["entries"]

        assert library.restore(p)["entries"] == kept             # no .bak at all
        library.save(library.empty_index(), library.backup_path(p))
        assert library.restore(p)["entries"] == kept             # an EMPTY .bak
        assert library.load(p)["entries"] == kept


def test_backup_summary_reports_what_the_backup_holds():
    """The read half of the backup slot, for a confirm that can name both sides of a restore: the
    entry COUNT and a raw mtime (no formatting — this module stays display-agnostic), or None when
    there is nothing restorable."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        assert library.backup_summary(p) is None                 # nothing yet
        library.upsert_and_save(_entry("GX010060", track="MK"), p)
        library.upsert_and_save(_entry("GX010061", track="MK"), p)
        assert library.backup_summary(p) is None                 # a healthy save writes no backup
        library.clear(p)
        info = library.backup_summary(p)
        assert info is not None
        assert info["entries"] == 2
        assert info["path"] == library.backup_path(p) == p + ".bak"
        assert isinstance(info["mtime"], float) and info["mtime"] > 0


def test_migrate_hook_preserves_entries_and_backfills_trust_flags():
    """The _migrate hook (the PR #55 framework) must PRESERVE every entry AND, for the v1→v2 bump,
    back-fill the three trust flags with the trusted-unknown default so a pre-existing (flag-less)
    PB history is not discarded. Same count, same fingerprints, flags defaulted."""
    data = {"version": 1, "entries": [_entry("GX010060"), _entry("GX010061")]}
    out = library._migrate(data, 1)
    assert len(out["entries"]) == 2                               # nothing dropped
    assert [e["fingerprint"] for e in out["entries"]] == ["GX0060", "GX0061"]
    for e in out["entries"]:
        assert e["verified"] is True and e["degraded"] is False and e["dropout"] is False


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


# --------------------------------------------------------- v2 trust: PB exclusion + migration + summary

def test_is_trustworthy_gate():
    """is_trustworthy: a verified, non-degraded, non-dropout entry (incl. a legacy flag-less one)
    is trustworthy; flipping ANY of verified→False / degraded→True / dropout→True excludes it."""
    assert library.is_trustworthy(_entry("A"))                              # all clean
    assert library.is_trustworthy({"best": 68.0})                           # legacy flag-less → in
    assert not library.is_trustworthy(_entry("A", verified=False))          # provisional → out
    assert not library.is_trustworthy(_entry("A", degraded=True))           # estimated → out
    assert not library.is_trustworthy(_entry("A", dropout=True))            # dropout → out


def test_pb_series_excludes_untrustworthy_keeps_legacy_and_verified():
    """THE PB-TRUST RULE: pb_series (and the PB floor) must EXCLUDE a provisional / degraded /
    dropout best, INCLUDE a legacy trusted-unknown best and a verified one. A meaningless "best"
    never appears in the progression or sets the bar a real lap must beat."""
    idx = library.empty_index()
    library.upsert(idx, _entry("V", track="MK", date="2024-05-01", best=70.0))    # verified → in
    # A LEGACY (flag-less) entry: upserted raw so _norm_entry back-fills the trusted-unknown default.
    library.upsert(idx, {"fingerprint": "GX0061", "stem": "GX010061", "track": "MK",
                         "date": "2024-05-02", "lap_count": 5, "best": 69.5,
                         "theoretical": None, "paths": []})                        # legacy → in
    library.upsert(idx, _entry("P", track="MK", date="2024-05-03", best=60.0,     # provisional
                               verified=False))                                    #   (fastest!) → OUT
    library.upsert(idx, _entry("D", track="MK", date="2024-05-04", best=61.0,     # degraded → OUT
                               degraded=True))
    library.upsert(idx, _entry("R", track="MK", date="2024-05-05", best=62.0,     # dropout → OUT
                               dropout=True))
    series = library.pb_series(idx, "MK")
    # Only the verified + legacy bests appear; the three untrustworthy (and FASTER) bests are gone.
    assert series == [("2024-05-01", 70.0), ("2024-05-02", 69.5)]
    # And the PB floor is the verified/legacy min (69.5), NOT the provisional 60.0.
    assert library.prior_best(idx, "MK") == 69.5


def test_trust_label_reasons_and_priority():
    """trust_label: None for trustworthy (incl. legacy), else the most-significant reason —
    provisional > estimated > dropout — so exactly one tag renders."""
    assert library.trust_label(_entry("A")) is None
    assert library.trust_label({"best": 1.0}) is None                       # legacy → trustworthy
    assert library.trust_label(_entry("A", verified=False)) == "provisional"
    assert library.trust_label(_entry("A", degraded=True)) == "estimated"
    assert library.trust_label(_entry("A", dropout=True)) == "dropout"
    # Priority: an entry that is BOTH provisional and dropout reads as "provisional" (the worst).
    assert library.trust_label(_entry("A", verified=False, dropout=True)) == "provisional"


def test_real_v1_file_migrates_to_v2_all_entries_preserved():
    """THE #55 VALIDATION: a real on-disk schema-v1 file (no trust flags) round-trips to v2 with
    EVERY legacy entry preserved and back-filled to trusted-unknown — a pre-existing PB history is
    NOT retroactively discarded (all legacy bests stay eligible for the PB chart)."""
    # Two v1-shaped entries WITHOUT any trust flags (the pre-#55-user file on disk).
    v1_a = {"fingerprint": "GX0060", "stem": "GX010060", "track": "MK", "date": "2024-05-01",
            "lap_count": 8, "best": 68.4, "theoretical": 67.9, "paths": []}
    v1_b = {"fingerprint": "GX0061", "stem": "GX010061", "track": "MK", "date": "2024-06-01",
            "lap_count": 9, "best": 67.1, "theoretical": 66.8, "paths": []}
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        with open(p, "w") as f:
            json.dump({"version": 1, "entries": [v1_a, v1_b]}, f)
        idx = library.load(p)
        # Re-stamped to v2, both entries survived, each back-filled trusted-unknown.
        assert idx["version"] == 2 == library.VERSION
        assert {e["fingerprint"] for e in idx["entries"]} == {"GX0060", "GX0061"}
        for e in idx["entries"]:
            assert e["verified"] is True and e["degraded"] is False and e["dropout"] is False
        # The legacy bests are trustworthy → still charted (back-compat: history is not discarded).
        assert library.pb_series(idx, "MK") == [("2024-05-01", 68.4), ("2024-06-01", 67.1)]
        # A save writes the migrated v2 file back with no data loss.
        library.save(idx, p)
        back = library.load(p)
        assert back["version"] == 2
        assert {e["fingerprint"] for e in back["entries"]} == {"GX0060", "GX0061"}


def test_upsert_writes_v2_trust_flags():
    """A new upsert stores the entry's verified/degraded/dropout flags verbatim (the app sources
    them from the live Session), and they survive save→load."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "library.json")
        library.upsert_and_save(
            _entry("GX010060", verified=False, degraded=True, dropout=True), p)
        e = library.load(p)["entries"][0]
        assert e["verified"] is False and e["degraded"] is True and e["dropout"] is True


def test_track_summary_counts_best_pbs_and_trend():
    """track_summary is a light honest read from the TRUSTWORTHY dated series: sessions counts every
    row, best/best_date/pb_count/trend come from pb_series only (a provisional/degraded/dropout best
    never inflates them)."""
    idx = library.empty_index()
    library.upsert(idx, _entry("A", track="MK", date="2024-05-01", best=70.0))
    library.upsert(idx, _entry("B", track="MK", date="2024-06-01", best=69.0))   # new PB
    library.upsert(idx, _entry("C", track="MK", date="2024-07-01", best=68.0))   # new PB
    # A provisional FASTER "best" — counted as a session, but never the best / a PB.
    library.upsert(idx, _entry("P", track="MK", date="2024-08-01", best=50.0, verified=False))
    s = library.track_summary(idx, "MK")
    assert s["sessions"] == 4                       # every row counts
    assert s["best"] == 68.0 and s["best_date"] == "2024-07-01"   # trustworthy best, NOT 50.0
    # 70 → 69 → 68: TWO sessions beat the running best. The first seeds it (nothing to beat yet) —
    # library.pb_moment calls that same session "first", not a PB (QA L11-03).
    assert s["pb_count"] == 2
    assert s["trend"] == "improving"                 # latest trustworthy session holds the record
    # A stalled track: the latest session is off the earlier PB, so nothing was ever beaten.
    idx2 = library.empty_index()
    library.upsert(idx2, _entry("A", track="MK", date="2024-05-01", best=68.0))
    library.upsert(idx2, _entry("B", track="MK", date="2024-06-01", best=70.0))  # slower → stalled
    s2 = library.track_summary(idx2, "MK")
    assert s2["best"] == 68.0 and s2["pb_count"] == 0 and s2["trend"] == "stalled"
    # A brand-new track: one session, no prior best to beat → 0 PBs (it used to report "1 PB").
    idx4 = library.empty_index()
    library.upsert(idx4, _entry("A", track="MK", date="2024-05-01", best=68.0))
    s4 = library.track_summary(idx4, "MK")
    assert s4["sessions"] == 1 and s4["pb_count"] == 0 and s4["trend"] == "single"
    # A track with no trustworthy dated best → sessions counted, best None, trend "none".
    idx3 = library.empty_index()
    library.upsert(idx3, _entry("X", track="MK", date="2024-05-01", best=60.0, verified=False))
    s3 = library.track_summary(idx3, "MK")
    assert s3["sessions"] == 1 and s3["best"] is None and s3["trend"] == "none"
    assert library.track_summary(idx, None) is None


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


def test_dialog_pb_chart_hides_pyqtgraph_chrome():
    """P3: the PB-progression mini-chart is a read-only display, so it must not sprout pyqtgraph's
    developer chrome — the little "A" auto-range button that appears under the cursor, or the
    right-click plot menu."""
    with tempfile.NamedTemporaryFile(suffix=".MP4") as real:
        idx, _, _ = _two_entry_index([real.name])
        dlg = LibraryDialog(idx, _OpenSpy())
        assert dlg.pb_plot.getPlotItem().buttonsHidden, "PB chart still shows the 'A' button"
        assert not dlg.pb_plot.getPlotItem().menuEnabled(), "PB chart still has the plot menu"
        dlg.deleteLater()


def _pb_pens(dlg):
    """The PB chart's cosmetic pyqtgraph pens as {name: (width, colour)} — a cosmetic pen's width
    IS device px, so these numbers are what the chart actually draws on the screen it is on."""
    pens = {
        "left axis": dlg.pb_plot.getAxis("left").pen(),
        "bottom axis": dlg.pb_plot.getAxis("bottom").pen(),
        "PB line": dlg._pb_curve.opts["pen"],
        "marker outline": dlg._pb_curve.opts["symbolPen"],
    }
    return {k: (p.widthF(), p.color().name()) for k, p in pens.items()}


def test_pb_chart_line_weights_are_logical_pixels_not_device_pixels():
    """QA W11-03. The PB progression chart built its pens as MODULE CONSTANTS (`_PB_PEN =
    pg.mkPen(C.accent, width=2)`) and penned its axes from a bare colour. A pyqtgraph pen is
    cosmetic, so its width is in DEVICE pixels: those are 2 and 1 device px at every ratio, i.e.
    half weight on a Retina panel — while the charts and the map, converted by #175, scale. The
    guard that was supposed to catch this walked a hard-coded list of two other file names.

    A module constant cannot be right on both screens whatever it is set to, which is why the pens
    are accessors and the dialog re-issues them when it is shown and when its ratio changes."""
    from PySide6.QtCore import QEvent

    from studio import theme
    try:
        dlg = _wired_dialog(_many_entries())
        dlg.show()
        _settle()
        at_1 = _pb_pens(dlg)
        assert {w for w, _c in at_1.values()} == {1.0, 2.0}, at_1
        assert at_1["PB line"][0] == 2.0, at_1

        dlg.devicePixelRatioF = lambda: 2.0          # dragged onto the Retina panel
        dlg.event(QEvent(QEvent.Type.DevicePixelRatioChange))
        _settle()
        at_2 = _pb_pens(dlg)
        assert {k: w for k, (w, _c) in at_2.items()} == {
            k: w * 2 for k, (w, _c) in at_1.items()}, (
            f"on a DPR-2 screen (theme.pen_scale()={theme.pen_scale()}) the PB chart still draws "
            f"{at_2} device px, i.e. half its design weight: was {at_1}")
        assert theme.pen_scale() == 2.0
        assert ({c for _w, c in at_2.values()} == {c for _w, c in at_1.values()}), (at_1, at_2)

        dlg.devicePixelRatioF = lambda: 1.0          # …and back to the external monitor
        dlg.event(QEvent(QEvent.Type.DevicePixelRatioChange))
        _settle()
        assert _pb_pens(dlg) == at_1, (at_1, _pb_pens(dlg))
        dlg.hide()
        dlg.deleteLater()
    finally:
        theme.set_pen_scale(1.0)


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
    """A row is JUNK (quarantined) iff it has no valid laps. An UNKNOWN TRACK is NOT junk — the
    track registry ships with ~one circuit, so an unrecognised track is the common case and that
    recording still has real laps, a real best and a real file to re-open."""
    assert _entry_junk(_entry("hero6", track=None, laps=0))          # no track AND no laps
    assert _entry_junk(_entry("GX010060", laps=0))                   # no laps
    assert not _entry_junk(_entry("GX010060", track=None))           # unknown track, but 12 laps
    assert not _entry_junk(_entry("GX010060", track="MK", laps=5))   # a real recording


def test_dialog_unknown_track_row_with_laps_stays_openable():
    """QA L11-01: an unknown-track recording WITH valid laps is a first-class row — enabled,
    selectable, current-able and openable, tagged "provisional" (its start line is auto-fitted)
    rather than quarantined as "(no laps)" while the same row prints its best lap."""
    with tempfile.NamedTemporaryFile(suffix=".MP4") as real:
        idx = library.empty_index()
        # The F.D shape: no registry track, 25 valid laps, provisional (auto-fitted) start line.
        library.upsert(idx, _entry("GX010065", track=None, date="2026-08-30", best=13.073,
                                   theo=13.073, laps=25, verified=False, paths=[real.name]))
        dlg = LibraryDialog(idx, _OpenSpy())
        row = _row_with_date(dlg, "2026-08-30")
        date_item = dlg.table.item(row, _COL_DATE)
        assert date_item.flags() & Qt.ItemIsEnabled
        assert date_item.flags() & Qt.ItemIsSelectable
        assert not bool(date_item.data(MISSING_ROLE))          # not quarantined
        track_text = dlg.table.item(row, _COL_TRACK).text()
        assert "(no laps)" not in track_text                   # it HAS laps — 25 of them
        assert track_text == "unknown track  · provisional"    # named + trust-tagged, not blocked
        # It is what the dialog auto-selects (the only row), Open is live, and it routes to _load.
        assert dlg._selected_date_item() is date_item
        assert dlg.open_btn.isEnabled()
        spy = _OpenSpy()
        dlg._open_recording = spy
        dlg._open_selected()
        assert spy.calls == [[real.name]]
        dlg.deleteLater()


def test_dialog_pb_empty_state_separates_no_selection_from_unknown_track():
    """A selectable unknown-track row (QA L11-01) must not leave the chart telling the user to
    "select a recording" they have already selected: no selection and a selected-but-unknown track
    are different states and get different sentences."""
    with tempfile.NamedTemporaryFile(suffix=".MP4") as real:
        idx = library.empty_index()
        library.upsert(idx, _entry("GX010065", track=None, date="2026-08-30", best=13.073,
                                   theo=13.073, laps=25, verified=False, paths=[real.name]))
        dlg = LibraryDialog(idx, _OpenSpy())
        assert dlg._selected_date_item() is not None       # the unknown-track row IS selected
        assert "isn't in your database" in dlg._pb_empty.toPlainText()
        dlg.table.clearSelection()
        dlg._on_selection()
        assert "Select a recording" in dlg._pb_empty.toPlainText()
        dlg.deleteLater()


def test_dialog_quarantines_junk_row_and_does_not_select_it():
    """A user's existing library.json may carry a JUNK row (no valid laps — e.g. the legacy
    bundled-sample row). The dialog greys + disables it, never auto-selects it, and the auto-selected
    row is instead the real recording — so the dialog renders cleanly without manual cleanup."""
    with tempfile.NamedTemporaryFile(suffix=".MP4") as real:
        idx = library.empty_index()
        # A real recording (present file) + a junk row (0 laps).
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


def test_dialog_pb_empty_state_label_stays_centred_in_the_plot_across_a_resize():
    """QA L11-02: the empty-state label is a CHILD of the ViewBox, so it is positioned in the box's
    PIXEL space — a data-space viewRect() centre put it ~1.8e9 px off-screen on the date axis (the
    sentence was never seen). It must also re-centre on resize (a one-shot position drifts 150 px)."""
    with tempfile.NamedTemporaryFile(suffix=".MP4") as real:
        idx = library.empty_index()
        library.upsert(idx, _entry("GX010060", track="MK", date="2024-05-01", best=68.0,
                                   laps=8, paths=[real.name]))
        dlg = LibraryDialog(idx, _OpenSpy())
        dlg.resize(900, 700)
        dlg.show()
        _APP.processEvents()
        dlg._show_pb("MK")                       # 1 dated best → the explanatory message shows
        assert dlg._pb_empty.isVisible()

        def _assert_centred(where):
            """Assert the label sits at the ViewBox's pixel centre, fully inside the plot; returns
            that centre so the caller can prove the box really did change size."""
            box = dlg.pb_plot.getPlotItem().getViewBox().boundingRect()
            assert box.width() > 0 and box.height() > 0, where
            off = dlg._pb_empty.pos() - box.center()
            assert abs(off.x()) < 1.0 and abs(off.y()) < 1.0, (where, off)
            # …and the whole label lands inside the plot, not half off its edge.
            assert dlg.pb_plot.sceneRect().contains(dlg._pb_empty.sceneBoundingRect()), where
            return box.center()

        before = _assert_centred("at the opening size")
        dlg.resize(1200, 820)
        _APP.processEvents()
        after = _assert_centred("after a resize")
        assert after.x() - before.x() > 10.0         # the box really grew — the re-centre is load-bearing
        dlg.hide()
        dlg.deleteLater()


# ------------------------------------------------ v2 dialog: search / filter / trust tag / summary

def _visible_rows(dlg) -> set[str]:
    """The DATE-cell text of every VISIBLE (non-hidden) table row — the on-screen filtered set."""
    return {dlg.table.item(r, _COL_DATE).text()
            for r in range(dlg.table.rowCount()) if not dlg.table.isRowHidden(r)}


def test_dialog_search_filters_rows_by_track_and_date():
    """The search box filters rows live by a track OR date substring (case-insensitive); clearing
    it restores every row."""
    idx = library.empty_index()
    library.upsert(idx, _entry("A", track="Sonoma", date="2024-05-01", best=70.0, paths=[]))
    library.upsert(idx, _entry("B", track="Buttonwillow", date="2024-06-15", best=68.0, paths=[]))
    dlg = LibraryDialog(idx, _OpenSpy())
    # Track substring (case-insensitive) → only the Sonoma row.
    dlg.search.setText("sonoma")
    assert _visible_rows(dlg) == {"2024-05-01"}
    # Date substring → only the June row.
    dlg.search.setText("06-15")
    assert _visible_rows(dlg) == {"2024-06-15"}
    # Cleared → both rows back.
    dlg.search.setText("")
    assert _visible_rows(dlg) == {"2024-05-01", "2024-06-15"}
    dlg.deleteLater()


def test_dialog_track_filter_combo_scopes_to_one_track():
    """The track-filter combo lists the distinct tracks and, when set, hides other tracks' rows;
    'All tracks' shows everything."""
    idx = library.empty_index()
    library.upsert(idx, _entry("A", track="Sonoma", date="2024-05-01", best=70.0, paths=[]))
    library.upsert(idx, _entry("B", track="Buttonwillow", date="2024-06-15", best=68.0, paths=[]))
    dlg = LibraryDialog(idx, _OpenSpy())
    # Combo carries the sentinel + the two distinct tracks (sorted).
    items = [dlg.track_filter.itemText(i) for i in range(dlg.track_filter.count())]
    assert items == [_ALL_TRACKS, "Buttonwillow", "Sonoma"]
    dlg.track_filter.setCurrentText("Sonoma")
    assert _visible_rows(dlg) == {"2024-05-01"}
    dlg.track_filter.setCurrentText(_ALL_TRACKS)
    assert _visible_rows(dlg) == {"2024-05-01", "2024-06-15"}
    dlg.deleteLater()


def test_dialog_trust_tag_shows_on_untrustworthy_row():
    """An untrustworthy (provisional/estimated/dropout) recording renders a muted trust tag in its
    Track cell; a trustworthy row does not."""
    with tempfile.NamedTemporaryFile(suffix=".MP4") as real:
        idx = library.empty_index()
        library.upsert(idx, _entry("GX010060", track="MK", date="2024-05-01", best=70.0,
                                   laps=8, paths=[real.name]))                    # trustworthy
        library.upsert(idx, _entry("GX010062", track="MK", date="2024-06-01", best=68.0,
                                   laps=8, verified=False, paths=[real.name]))    # provisional
        dlg = LibraryDialog(idx, _OpenSpy())
        prov_row = _row_with_date(dlg, "2024-06-01")
        good_row = _row_with_date(dlg, "2024-05-01")
        prov_text = dlg.table.item(prov_row, _COL_TRACK).text()
        good_text = dlg.table.item(good_row, _COL_TRACK).text()
        assert "provisional" in prov_text                # the untrustworthy row is tagged
        assert "provisional" not in good_text            # the trustworthy row is not
        # The tag is muted+italic (the theme's trust tier), but the row stays openable (not disabled).
        assert dlg.table.item(prov_row, _COL_TRACK).font().italic()
        assert not bool(dlg.table.item(prov_row, _COL_DATE).data(MISSING_ROLE))
        dlg.deleteLater()


def test_dialog_row_tooltip_and_forget_confirm_name_the_recording_file():
    """QA L11-04: no column names a FILE, so two same-day sessions on an unknown track read as the
    same row. Every cell hovers to the recording's filename + full path (+ its extra chapters), and
    the destructive forget confirm — which deletes that recording's sidecar — leads with the name."""
    from PySide6.QtWidgets import QMessageBox
    with tempfile.NamedTemporaryFile(suffix=".MP4") as real:
        idx = library.empty_index()
        library.upsert(idx, _entry("GX010065", track=None, date="2026-08-30", best=13.073,
                                   theo=13.073, laps=25, verified=False,
                                   paths=[real.name, "/media/GX020065.MP4"]))
        dlg = LibraryDialog(idx, _OpenSpy())
        name = os.path.basename(real.name)
        row = _row_with_date(dlg, "2026-08-30")
        for col in range(dlg.table.columnCount()):
            tip = dlg.table.item(row, col).toolTip()
            assert name in tip and real.name in tip, (col, tip)
        assert "1 more chapter" in dlg.table.item(row, _COL_DATE).toolTip()
        # The confirm: capture its text and answer No (nothing is forgotten by this test).
        seen = []
        orig = QMessageBox.question
        QMessageBox.question = staticmethod(
            lambda *a, **k: (seen.append(a[2]), QMessageBox.No)[1])
        try:
            dlg._forget_row(dlg.table.item(row, _COL_DATE))
        finally:
            QMessageBox.question = orig
        assert seen and name in seen[0] and "unknown track" in seen[0]
        dlg.deleteLater()


def test_dialog_pb_axis_renders_lap_times_not_decimal_seconds():
    """QA L11-05: the PB chart's left axis printed decimal seconds ("69", "70.5", label "best lap
    (s)") while the Best lap column and the summary in the SAME frame read "1:09.905". It must use
    the app's one time formatter, and then the label must not still claim "(s)"."""
    idx = library.empty_index()
    library.upsert(idx, _entry("A", track="MK", date="2024-05-01", best=70.5, paths=[]))
    dlg = LibraryDialog(idx, _OpenSpy())
    ax = dlg.pb_plot.getAxis("left")
    assert ax.tickStrings([68.5, 69.0, 70.5], 1.0, 0.5) == ["1:08.500", "1:09.000", "1:10.500"]
    assert ax.labelText == "best lap" and "(s)" not in ax.labelText
    # …and that IS the formatter the table cell beside it uses.
    assert ax.tickStrings([70.5], 1.0, 0.5)[0] == fmt_time(70.5)
    dlg.deleteLater()


def test_dialog_empty_filter_says_so_and_counts_what_is_on_screen():
    """QA L11-06: a filter matching nothing left the dialog wholly blank — no "no matches" message
    and a header still asserting "2 analyzed recordings" over 0 visible rows. Both must describe
    what is on screen, and clearing the filter must restore both."""
    idx = library.empty_index()
    library.upsert(idx, _entry("A", track="Sonoma", date="2024-05-01", best=70.0, paths=[]))
    library.upsert(idx, _entry("B", track="Buttonwillow", date="2024-06-15", best=68.0, paths=[]))
    dlg = LibraryDialog(idx, _OpenSpy())
    assert dlg._title.text() == "2 analyzed recordings"
    assert dlg._no_matches.isHidden()
    dlg.search.setText("zzzz-no-such-recording")
    assert _visible_rows(dlg) == set()
    assert dlg._title.text() == "0 of 2 analyzed recordings"
    assert not dlg._no_matches.isHidden()
    assert "No recordings match" in dlg._no_matches.text()
    assert "zzzz-no-such-recording" in dlg._no_matches.text()   # it names the term that matched none
    # Cleared → both back to the whole library.
    dlg.search.setText("")
    assert dlg._title.text() == "2 analyzed recordings"
    assert dlg._no_matches.isHidden()
    dlg.deleteLater()


def test_dialog_empty_library_gets_no_no_matches_message():
    """The "no matches" sentence belongs to a FILTERED empty table only — an empty library is a
    different state (nothing indexed yet) and must not be told its search matched nothing."""
    dlg = LibraryDialog(library.empty_index(), _OpenSpy())
    assert dlg._no_matches.isHidden()
    assert dlg._title.text() == "0 analyzed recordings"
    dlg.search.setText("anything")
    assert dlg._no_matches.isHidden()
    dlg.deleteLater()


def test_dialog_empty_filter_drops_the_de_selected_rows_axis():
    """QA L11-06: de-selecting the charted row cleared the curve but LEFT its axis range — an empty
    grid still ticking 67.771–69.771 s, i.e. numbers about a recording no longer on screen."""
    with tempfile.NamedTemporaryFile(suffix=".MP4") as real:
        idx = library.empty_index()
        library.upsert(idx, _entry("A", track="MK", date="2024-05-01", best=70.0,
                                   laps=8, paths=[real.name]))
        library.upsert(idx, _entry("B", track="MK", date="2024-06-01", best=68.0,
                                   laps=8, paths=[real.name]))
        dlg = LibraryDialog(idx, _OpenSpy())
        vb = dlg.pb_plot.getPlotItem().getViewBox()
        charted = vb.viewRect()
        assert charted.y() > 60.0, charted        # framed on the two ~68-70 s bests
        assert dlg.pb_plot.getAxis("left").style["showValues"]
        # Filter everything away: the selection clears, so nothing is plotted any more.
        dlg.search.setText("zzzz-no-such-recording")
        assert _visible_rows(dlg) == set()
        empty = vb.viewRect()
        assert (empty.y(), empty.height()) == (0.0, 1.0), empty   # the stale range is gone
        assert not dlg.pb_plot.getAxis("left").style["showValues"]
        # Re-selecting a row re-frames it — the reset is not a one-way door.
        dlg.search.setText("")
        assert dlg.pb_plot.getPlotItem().getViewBox().viewRect().y() > 60.0
        assert dlg.pb_plot.getAxis("left").style["showValues"]
        dlg.deleteLater()


def test_dialog_unknown_track_bucket_reaches_the_rows_the_combo_could_not():
    """QA L11-06: the track combo listed only NAMED tracks, so an unknown-track recording — the
    common case, since the registry ships with about one circuit — could not be filtered to at all
    (2 of the 3 rows on the QA index). An "Unknown track" bucket collects them, and the search box
    matches the label they actually show."""
    with tempfile.NamedTemporaryFile(suffix=".MP4") as real:
        idx = library.empty_index()
        library.upsert(idx, _entry("GX010062", track="MK", date="2024-05-01", best=68.0,
                                   laps=8, paths=[real.name]))
        library.upsert(idx, _entry("GX010065", track=None, date="2024-06-01", best=13.0,
                                   laps=25, verified=False, paths=[real.name]))
        library.upsert(idx, _entry("GX010059", track=None, date="2024-07-01", best=23.0,
                                   laps=4, verified=False, paths=[real.name]))
        dlg = LibraryDialog(idx, _OpenSpy())
        items = [dlg.track_filter.itemText(i) for i in range(dlg.track_filter.count())]
        assert items == [_ALL_TRACKS, "MK", _UNKNOWN_TRACK], items
        dlg.track_filter.setCurrentText(_UNKNOWN_TRACK)
        assert _visible_rows(dlg) == {"2024-06-01", "2024-07-01"}
        dlg.track_filter.setCurrentText("MK")
        assert _visible_rows(dlg) == {"2024-05-01"}
        # The search box reaches them too, by the label the cell shows.
        dlg.track_filter.setCurrentText(_ALL_TRACKS)
        dlg.search.setText("unknown")
        assert _visible_rows(dlg) == {"2024-06-01", "2024-07-01"}
        dlg.deleteLater()


def test_dialog_track_cell_tooltip_leads_with_its_own_full_label():
    """QA L11-07: Track is the one STRETCH column, so at the dialog's own 489 px minimum width it
    elides ("unknown track  · provi…" — 172 px of text in a 141 px content box) with a tooltip that
    named only the FILE. The tooltip must lead with the cell's own full label, and still carry the
    recording's filename + path (QA L11-04)."""
    with tempfile.NamedTemporaryFile(suffix=".MP4") as real:
        idx = library.empty_index()
        library.upsert(idx, _entry("GX010065", track=None, date="2026-08-30", best=13.073,
                                   laps=25, verified=False, paths=[real.name]))
        library.upsert(idx, _entry("GX010062", track="Daytona Milton Keynes", date="2026-05-24",
                                   best=68.771, laps=21, paths=[real.name]))
        dlg = LibraryDialog(idx, _OpenSpy())
        dlg.resize(dlg.minimumSizeHint())        # the narrowest width Qt will let the dialog reach
        name = os.path.basename(real.name)
        for r in range(dlg.table.rowCount()):
            it = dlg.table.item(r, _COL_TRACK)
            tip = it.toolTip()
            assert tip.startswith(it.text()), (it.text(), tip)   # the elided tail is recoverable
            assert name in tip and real.name in tip, tip         # …and it still names the file
        # The other columns keep the plain file identity (they size to their contents, so they
        # never elide — repeating their own text would be noise).
        assert dlg.table.item(0, _COL_DATE).toolTip().startswith(name)
        dlg.deleteLater()


def test_dialog_header_and_clear_confirm_use_the_module_pluralizer():
    """QA L11-09: the header and the Clear-library confirm used the "(s)" placeholder plural two
    lines above the same module's own correct _plural helper."""
    from PySide6.QtWidgets import QMessageBox
    one = library.empty_index()
    library.upsert(one, _entry("A", track="MK", date="2024-05-01", best=70.0, paths=[]))
    dlg1 = LibraryDialog(one, _OpenSpy())
    assert dlg1._title.text() == "1 analyzed recording"       # singular, not "1 recording(s)"
    dlg1.deleteLater()

    idx = library.empty_index()
    for i, stem in enumerate(("A", "B", "C")):
        library.upsert(idx, _entry(stem, track="MK", date=f"2024-05-0{i + 1}", best=70.0, paths=[]))
    seen = []
    orig = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: (seen.append(a[2]), QMessageBox.No)[1])
    try:
        dlg = LibraryDialog(idx, _OpenSpy(), clear_library=library.empty_index)
        assert dlg._title.text() == "3 analyzed recordings"
        dlg._on_clear_library()
    finally:
        QMessageBox.question = orig
    assert seen and "Forget all 3 recordings from the library?" in seen[0], seen
    assert "(s)" not in seen[0]
    dlg.deleteLater()


def test_dialog_progress_summary_line_for_selected_track():
    """Selecting a track shows the compact progress summary (session count + trustworthy best +
    PB count) — and a provisional 'best' never inflates it."""
    with tempfile.NamedTemporaryFile(suffix=".MP4") as real:
        idx = library.empty_index()
        library.upsert(idx, _entry("GX010060", track="MK", date="2024-05-01", best=70.0,
                                   laps=8, paths=[real.name]))
        library.upsert(idx, _entry("GX010062", track="MK", date="2024-06-01", best=68.0,
                                   laps=8, paths=[real.name]))
        # A provisional faster best — a session, but not the best / a PB.
        library.upsert(idx, _entry("GX010064", track="MK", date="2024-07-01", best=50.0,
                                   laps=8, verified=False, paths=[real.name]))
        dlg = LibraryDialog(idx, _OpenSpy())
        dlg._show_summary("MK")
        text = dlg._summary.text()
        assert "3 sessions" in text                      # every row counts
        assert "50" not in text                          # the provisional 50.0 never shows as best
        assert fmt_time(68.0) in text                    # the trustworthy best does
        assert "1 PB" in text                            # 70 → 68 = one session beat the running best
        # No track selected → blank line.
        dlg._show_summary(None)
        assert dlg._summary.text() == ""
        dlg.deleteLater()


def test_dialog_summary_omits_the_pb_clause_on_a_first_session():
    """QA L11-03: a track's FIRST session has beaten nothing, so the summary says "1 session ·
    best …" and drops the PB clause entirely rather than claiming "1 PB" (or reading "0 PBs")."""
    with tempfile.NamedTemporaryFile(suffix=".MP4") as real:
        idx = library.empty_index()
        library.upsert(idx, _entry("GX010060", track="MK", date="2024-05-01", best=68.0,
                                   laps=8, paths=[real.name]))
        dlg = LibraryDialog(idx, _OpenSpy())
        dlg._show_summary("MK")
        text = dlg._summary.text()
        assert "1 session" in text and fmt_time(68.0) in text
        assert "PB" not in text
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
    # Trust-flag accessors (library schema v2). timing_verified reads track_name (set → Verified)
    # and timing_quality is getattr-guarded to high-quality on a bare Session; dropout_lap_ids walks
    # real lap columns absent here, so stub it (this recording has no dropout lap).
    s.dropout_lap_ids = lambda: set()
    # A relative path → the entry must store it absolute.
    rel = os.path.join("subdir", "GX010062.MP4")
    entry = Session.library_entry(s, [rel])
    assert entry["fingerprint"] == "GX0062"          # chapter-invariant identity
    assert entry["paths"] == [os.path.abspath(rel)]  # absolute, cwd-independent
    assert os.path.isabs(entry["paths"][0])
    assert entry["track"] == "Daytona MK" and entry["best"] == 68.4
    # v2 trust flags sourced from the live Session: a detected-track recording is Verified, its
    # default timing_quality is not degraded, and no dropout lap was flagged.
    assert entry["verified"] is True
    assert entry["degraded"] is False
    assert entry["dropout"] is False


def test_library_entry_dropout_flag_describes_the_BEST_lap_only():
    """UI-scrutiny C5: the `dropout` flag is read by library.is_trustworthy as "a GPS dropout
    BEST", so the writer must mean exactly that. It used to be "ANY valid lap has a dropout",
    which excluded a perfectly clean best from the PB history the moment any OTHER lap dropped
    a fix — the app then celebrated a PB toast the library simultaneously rejected (empty
    progression chart, and a slower clean session could later be crowned track PB)."""
    if not _pacer_available():
        print("skip test_library_entry_dropout_flag_describes_the_BEST_lap_only (no pacer)")
        return
    from studio.session import Session

    def _bare(best_id, dropouts):
        s = Session.__new__(Session)
        s._valid_cache = [0, 1, 2]
        s._best_cache = best_id
        s.track_name = "Daytona MK"
        s.laps = type("L", (), {"lap_time": staticmethod(lambda i: 68.4)})()
        s.session_date = lambda: "2024-05-01"
        s.theoretical_best = lambda: 67.9
        s.dropout_lap_ids = lambda: set(dropouts)
        return s

    # OTHER laps dropped fixes; the best (lap 1) is clean → trustworthy, stays in the PB set.
    entry = Session.library_entry(_bare(1, {0, 2}), ["GX010062.MP4"])
    assert entry["dropout"] is False
    assert library.is_trustworthy(entry), "a clean best must stay PB-eligible"
    # The BEST lap itself dropped fixes → flagged, and correctly excluded from the PB set.
    entry = Session.library_entry(_bare(1, {1}), ["GX010062.MP4"])
    assert entry["dropout"] is True
    assert not library.is_trustworthy(entry)
    assert library.trust_label(entry) == "dropout"
    # No best at all (no valid lap) → nothing to flag.
    assert Session.library_entry(_bare(None, {0}), ["GX010062.MP4"])["dropout"] is False
    print("test_library_entry_dropout_flag_describes_the_BEST_lap_only OK")


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
        win.session = type("S", (), {
            "valid_lap_ids": staticmethod(lambda: [0, 1]),
            "timing_verified": True,
            "timing_quality": SimpleNamespace(degraded=False),
            "library_entry": staticmethod(
                lambda paths: _entry("GX010060", track="MK", laps=2)),
        })()
        studio_app.StudioWindow._update_library(win, ["/m/GX010060.MP4"])
        assert len(upserts) == 1 and upserts[0]["fingerprint"] == "GX0060"


# ---------------------------------------------------- the entry must not freeze at load time (W7-02)
class _TrustSession:
    """The slice of Session that File ▸ Save as track… and the library upsert actually read, with
    the REAL trust rule: a named track IS a trusted start line (Session.timing_verified), and
    library_entry sources its track/verified fields from the live session — so an entry built after
    the save differs from one built before it."""

    def __init__(self):
        self.track_name = None
        self.confirmed = False

    @property
    def timing_verified(self):
        return self.track_name is not None or self.confirmed

    def valid_lap_ids(self):
        return [0, 1]

    def point_count(self):
        return 500

    def track_location(self):
        return ((-37.95, 145.10), (-37.96, 145.09, -37.94, 145.11))

    def timing_lines_latlon(self):
        return (((-37.95, 145.10), (-37.95, 145.11)), [])

    def adopt_track(self, name):
        """The seam File ▸ Save as track… actually goes through (Session.adopt_track): attaching
        the name also records WHICH LINES it certifies, so the name can't outlive them."""
        self.track_name = name
        self.track_lines = self.timing_lines_latlon()

    def library_entry(self, paths):
        return _entry("GX030059", track=self.track_name, laps=2, best=23.231,
                      verified=self.timing_verified, paths=list(paths))


def _save_track_window(monkeypatch, upserts):
    """A fabricated StudioWindow wired for _save_as_track — the same __new__ idiom the
    _update_library test above uses, plus the three collaborators that gesture touches: a stubbed
    track_db (no DB write), a spy library.upsert_and_save, and a status bar / view double."""
    from studio import app as studio_app
    monkeypatch.setattr(library, "upsert_and_save", lambda entry, *a, **k: upserts.append(entry))
    monkeypatch.setattr(studio_app.track_db, "make_entry",
                        lambda name, *a, **k: {"name": name})
    monkeypatch.setattr(studio_app.track_db, "replaces", lambda entry: None)
    monkeypatch.setattr(studio_app.track_db, "save_track", lambda entry, replace=False: None)
    monkeypatch.setattr(studio_app.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("Sandown Park", True)))

    win = studio_app.StudioWindow.__new__(studio_app.StudioWindow)
    win.session = _TrustSession()
    win._paths = ["/media/GX030059.MP4"]
    win.view = SimpleNamespace(refreshed=0)
    win.view.refresh_timing_trust = lambda: setattr(win.view, "refreshed",
                                                    win.view.refreshed + 1)
    # Instance attributes shadow the QMainWindow methods (never __init__'d, so no C++ status bar).
    bar = SimpleNamespace(messages=[], current="")
    bar.showMessage = lambda m, *a: (bar.messages.append(m), setattr(bar, "current", m))[0]
    bar.currentMessage = lambda: bar.current
    bar.clearMessage = lambda: setattr(bar, "current", "")
    win.statusBar = lambda: bar
    return win, bar


def test_save_as_track_rewrites_the_library_entry(monkeypatch):
    """QA W7-02: File ▸ Save as track… names the circuit and makes the session Verified, but the
    library entry was written ONLY on the load path — so it froze at load time. The row kept reading
    "unknown track · provisional", is_trustworthy stayed False, and the lap was silently ABSENT from
    the PB progression of the track it had just created (prior_best/pb_series empty) until the user
    happened to re-open the file. The save must re-upsert from the session as it now stands."""
    if not _pacer_available():
        print("skip test_save_as_track_rewrites_the_library_entry (no pacer)")
        return
    from studio import app as studio_app
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(library, "_app_support_dir", lambda: d)
        upserts = []
        win, bar = _save_track_window(monkeypatch, upserts)

        # Pre-condition: this is exactly the state the QA repro starts from.
        assert win.session.timing_verified is False
        before = win.session.library_entry(win._paths)
        assert before["track"] is None and before["verified"] is False
        assert library.trust_label(before) == "provisional"
        assert not library.is_trustworthy(before)

        studio_app.StudioWindow._save_as_track(win)

        # The gesture verified the session AND wrote the track…
        assert win.session.track_name == "Sandown Park"
        assert win.session.timing_verified is True
        assert win.view.refreshed == 1, "the views must be refreshed off the trust flip"
        assert "Sandown Park" in bar.current
        # …and it must have carried BOTH facts into the index, in one upsert.
        assert len(upserts) == 1, f"the save must re-upsert the library entry, got {upserts!r}"
        after = upserts[0]
        assert after["track"] == "Sandown Park", after
        assert after["verified"] is True, after
        assert library.trust_label(after) is None and library.is_trustworthy(after)
        # The consequence the user sees: the lap is now IN that track's PB progression.
        idx = library.empty_index()
        library.upsert(idx, after)
        assert library.prior_best(idx, "Sandown Park") == 23.231
        assert library.pb_series(idx, "Sandown Park") == [(after["date"], 23.231)]


def test_save_as_track_survives_a_library_write_failure(monkeypatch):
    """Saving a track must never become a way to crash: the index is additive, so a library-write
    failure logs and is swallowed, exactly as on the load path. The new upsert must be REACHED (or
    this pins nothing — a save that never touches the library trivially survives one that fails),
    the exception must not escape, and the track + trust flip must still stand."""
    if not _pacer_available():
        print("skip test_save_as_track_survives_a_library_write_failure (no pacer)")
        return
    from studio import app as studio_app
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(library, "_app_support_dir", lambda: d)
        win, bar = _save_track_window(monkeypatch, [])
        attempts = []

        def _boom(entry, *a, **k):
            attempts.append(entry)
            raise OSError("read-only volume")

        monkeypatch.setattr(library, "upsert_and_save", _boom)
        studio_app.StudioWindow._save_as_track(win)  # must not raise
        assert len(attempts) == 1, f"the save must attempt the library write, got {attempts!r}"
        assert win.session.track_name == "Sandown Park"
        assert win.session.timing_verified is True
        assert "Sandown Park" in bar.current, "the save is still confirmed to the user"


def test_refresh_library_entry_keeps_the_load_path_exclusions(monkeypatch):
    """The later refreshes admit exactly what the load-time upsert admits (_library_excludes is
    shared): a 0-lap session and the bundled DEFAULT_SAMPLE are still never indexed, so a drag on an
    unsegmentable recording can't leave a permanent junk row the load path refused to create."""
    if not _pacer_available():
        print("skip test_refresh_library_entry_keeps_the_load_path_exclusions (no pacer)")
        return
    from studio import app as studio_app
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(library, "_app_support_dir", lambda: d)
        upserts = []
        monkeypatch.setattr(library, "upsert_and_save", lambda e, *a, **k: upserts.append(e))
        win = studio_app.StudioWindow.__new__(studio_app.StudioWindow)

        # No loaded recording at all → nothing to refresh.
        win.session = _TrustSession()
        win._paths = []
        studio_app.StudioWindow._refresh_library_entry(win)
        assert upserts == []

        # 0 valid laps → skipped, same as the load path.
        win._paths = ["/media/GX030059.MP4"]
        win.session.valid_lap_ids = lambda: []
        studio_app.StudioWindow._refresh_library_entry(win)
        assert upserts == []

        # The bundled sample → skipped even with laps.
        win.session = _TrustSession()
        win._paths = [studio_app.DEFAULT_SAMPLE]
        studio_app.StudioWindow._refresh_library_entry(win)
        assert upserts == []

        # A real recording with laps → indexed, carrying the session's CURRENT trust state.
        win._paths = ["/media/GX030059.MP4"]
        win.session.confirmed = True          # what a start/finish drag does
        studio_app.StudioWindow._refresh_library_entry(win)
        assert len(upserts) == 1 and upserts[0]["verified"] is True


def test_recent_entries_include_an_unknown_track_recording(monkeypatch):
    """QA L11-01, the Open Recent half: a recording is a recent candidate when it has valid laps and
    a present file — an UNKNOWN track is fine (it re-opens identically and _recent_label already
    names it "unknown track"). Requiring a registry track dropped a whole track day from the menu."""
    if not _pacer_available():
        print("skip test_recent_entries_include_an_unknown_track_recording (no pacer)")
        return
    from studio import app as studio_app
    with tempfile.TemporaryDirectory() as d, tempfile.NamedTemporaryFile(suffix=".MP4") as real:
        monkeypatch.setattr(library, "_app_support_dir", lambda: d)
        idx = library.empty_index()
        library.upsert(idx, _entry("GX010065", track=None, date="2026-08-30", best=13.073,
                                   theo=13.073, laps=25, verified=False, paths=[real.name]))
        library.upsert(idx, _entry("GX010062", track="MK", date="2026-05-24", best=68.771,
                                   laps=21, paths=[real.name]))
        # 0 laps → still NOT a candidate, and neither is a recording whose file has gone.
        library.upsert(idx, _entry("hero6", track=None, date="2026-08-31", best=None, theo=None,
                                   laps=0, paths=[real.name]))
        library.upsert(idx, _entry("GX010099", track="MK", date="2026-09-01", best=70.0,
                                   laps=4, paths=["/definitely/missing/GX010099.MP4"]))
        library.save(idx)
        win = studio_app.StudioWindow.__new__(studio_app.StudioWindow)
        got = studio_app.StudioWindow._recent_entries(win)
        assert [e["fingerprint"] for e in got] == ["GX0065", "GX0062"]   # newest first
        assert studio_app.StudioWindow._recent_label(win, got[0]).startswith("unknown track")


# ------------------------------------------------ v2 dialog: size / density (QA W7-06) + restore

def _rows_visible(dlg) -> float:
    """How many recordings the table can actually SHOW at its current size — the measure the
    density finding is about (a 201-row library is worth nothing behind a 4-row viewport)."""
    return dlg.table.viewport().height() / dlg.table.rowHeight(0)


def _settle():
    """Let the layout apply. Offscreen Qt still needs an event pass before geometry is real."""
    for _ in range(4):
        _APP.processEvents()


def _many_entries(n=30):
    """An index of `n` dated, openable-looking recordings — enough rows that the table has more to
    show than it can fit at any sane dialog size."""
    idx = library.empty_index()
    for i in range(n):
        library.upsert(idx, _entry(f"GX01{4000 + i:04d}", track="MK",
                                   date=f"2024-{1 + i % 12:02d}-{1 + i % 28:02d}",
                                   best=68.0 + i * 0.1, paths=[]))
    return idx


def test_fit_to_screen_clamps_a_dialog_size_to_the_display():
    """The size clamp is pure so it can be tested without a display. A big screen leaves the wanted
    size alone; a small one (an old 1280x800 panel) gets the dialog cut down to fit rather than
    opening taller than the screen; and a bogus/zero screen never collapses it below the floor."""
    assert _fit_to_screen(880, 860, 1512, 944) == (880, 860)       # 14" MBP — unclamped
    assert _fit_to_screen(880, 860, 1470, 931) == (880, 860)       # 13" Air — the tightest fit
    assert _fit_to_screen(880, 860, 1280, 775) == (880, 715)       # small panel — height cut
    assert _fit_to_screen(1600, 1200, 800, 800) == (740, 740)      # both axes cut
    assert _fit_to_screen(880, 860, 0, 0) == (880, 860)            # no screen reported — left alone
    assert _fit_to_screen(880, 860, 100, 100) == (480, 420)        # never below the floor


def test_dialog_opens_tall_enough_to_browse_and_holds_the_pb_chart_to_its_band():
    """QA W7-06: at its own default size the library showed 4.6 of 201 recordings — the table got a
    139 px viewport while the PB chart sat on its 150 px floor, and every pixel the dialog gained
    went to the chart as much as to the list. The dialog now opens at a size meant for browsing
    (clamped to the screen), and the chart is held to a band so the list takes the rest."""
    from PySide6.QtGui import QGuiApplication
    assert _DEFAULT_SIZE[1] > 600, "the default is no taller than the one that showed 4.6 rows"
    dlg = LibraryDialog(_many_entries(), _OpenSpy())
    avail = QGuiApplication.primaryScreen().availableGeometry()
    assert (dlg.width(), dlg.height()) == _fit_to_screen(
        *_DEFAULT_SIZE, avail.width(), avail.height())
    # Measured at FIXED sizes so nothing depends on the test machine's screen. The chart is the
    # thing that has to stop growing: at 880x860 it is 260 px on main (13.9 rows of table here
    # vs 12.3 there), and it kept taking a share of every pixel the dialog gained.
    dlg.show()
    _settle()
    dlg.resize(880, 860)
    _settle()
    roomy, tall_rows = dlg.pb_plot.height(), _rows_visible(dlg)
    table_at_860 = dlg.table.viewport().height()
    assert roomy <= _PB_PLOT_MAX_H, (roomy, tall_rows)
    # Every further pixel the dialog gains belongs to the LIST: growing the dialog by 120 px must
    # grow the table by ~120 px, not the 72 px (60%) it grew by on main.
    dlg.resize(880, 980)
    _settle()
    gained = dlg.table.viewport().height() - table_at_860
    assert gained >= 114, (gained, _rows_visible(dlg))
    # …and the chart still yields FIRST when the dialog is small, back down toward its 150 px floor.
    dlg.resize(880, 600)
    _settle()
    assert dlg.pb_plot.height() < roomy, (dlg.pb_plot.height(), roomy)
    assert dlg.pb_plot.height() <= 160, dlg.pb_plot.height()
    dlg.hide()
    dlg.deleteLater()


def test_dialog_remembers_a_size_the_user_changed_but_never_pins_its_own_default():
    """QA W7-06 (second half): resizing the library and re-opening it used to hand back 720x600.
    The size is persisted through studio.prefs now — but ONLY when the user actually changed it, so
    a dialog that stores its own default on first close can't freeze that default for everyone."""
    from PySide6.QtGui import QGuiApplication
    avail = QGuiApplication.primaryScreen().availableGeometry()
    path = prefs.prefs_path()
    if os.path.exists(path):
        os.remove(path)
    try:
        opened = LibraryDialog(_many_entries(), _OpenSpy())
        opened.show()
        _settle()
        opened.done(0)                                   # closed without touching the size
        assert prefs.library_size() is None, prefs.library_size()

        resized = LibraryDialog(_many_entries(), _OpenSpy())
        resized.show()
        _settle()
        resized.resize(700, 640)
        _settle()
        resized.done(0)                                  # Close/Escape and Open both route here
        assert prefs.library_size() == (700, 640), prefs.library_size()

        again = LibraryDialog(_many_entries(), _OpenSpy())
        # 640 is below the browsable floor, so what OPENS is floored — while what was STORED stays
        # 640 (asserted above). That split is the contract: the pref records the size the user
        # asked for, and each open applies the constraints of the moment (the floor, then the
        # screen), so neither one silently rewrites the other.
        assert (again.width(), again.height()) == _fit_to_screen(
            700, max(640, _MIN_BROWSABLE_H), avail.width(), avail.height())
        for dlg in (opened, resized, again):
            dlg.deleteLater()
    finally:
        if os.path.exists(path):
            os.remove(path)                              # leave no size for the next test to inherit


def _wired_dialog(index):
    """The dialog exactly as ``StudioWindow._open_library`` builds it — all six file-op callbacks
    injected. Not decoration: the button row those callbacks build is what sets the dialog's
    minimum WIDTH (581 px wired, 184 px bare), and the width is what decides how tall the privacy
    paragraph wraps. A size measured on an unwired dialog is a measurement of a dialog that never
    ships."""
    return LibraryDialog(index, _OpenSpy(),
                         forget_recording=lambda e: index, clear_library=lambda: index,
                         reveal_library=lambda: None, backup_library=lambda: None,
                         restore_library=lambda: index, backup_info=lambda: None)


def _privacy_note(dlg):
    """The dialog's privacy paragraph label."""
    from PySide6.QtWidgets import QLabel
    for label in dlg.findChildren(QLabel):
        if label.text() == PRIVACY_NOTE:
            return label
    raise AssertionError("the privacy note is not in the dialog")


def test_dialog_privacy_note_fits_inside_the_dialog_at_its_own_minimum():
    """QA W9-02: the privacy note is a WRAPPED label, and a layout builds its minimum from each
    item's minimumSizeHint — one LINE for a wrapping label. So lengthening the note to name
    tracks.json did not raise the dialog's minimum at all: at the smallest size a drag can reach,
    the note needed 128 px in the 83 px it was given and painted 45 px past its box, straight
    through the button row, and the two sentences about tracks.json never rendered.

    Measured at the dialog's OWN minimum (resize(1, 1); Qt clamps), because that is both the worst
    case and — since the size is remembered — a state the app can open in."""
    from PySide6.QtWidgets import QPushButton
    dlg = _wired_dialog(_many_entries())
    dlg.show()
    _settle()
    dlg.resize(1, 1)                     # Qt clamps at the layout's own minimum
    _settle()
    note = _privacy_note(dlg)
    needs = note.heightForWidth(note.width())
    assert note.height() >= needs > 0, (
        f"the privacy note has {note.height()} px and needs {needs} px at {note.width()} px wide "
        f"(dialog {dlg.width()}x{dlg.height()}) — {needs - note.height()} px of it, including the "
        f"tracks.json sentences, paints outside its box")
    buttons_top = min(b.y() for b in dlg.findChildren(QPushButton))
    assert note.y() + needs <= buttons_top, (
        f"the note runs {note.y() + needs - buttons_top} px into the button row "
        f"(note y={note.y()} + {needs} px vs buttons at y={buttons_top})")
    dlg.hide()
    dlg.deleteLater()


def test_dialog_minimum_stays_within_the_smallest_supported_screen():
    """The guard on the fix above. Making the note's wrapped height part of the layout's minimum is
    correct, but it hands PRIVACY_NOTE a lever on a number nobody looks at: every sentence added to
    that paragraph raises the height below which the library cannot be opened at all. Pin it to the
    smallest Mac this app targets — a 13" Air reports ~869 px of available height, and
    _SCREEN_MARGIN leaves 809 of it — so a note that grew past what that screen can show fails here
    instead of shipping. Measured at the narrowest width, where the note wraps tallest."""
    dlg = _wired_dialog(_many_entries())
    dlg.show()
    _settle()
    dlg.resize(1, 1)
    _settle()
    need = dlg.minimumSizeHint().height()
    room = 869 - _SCREEN_MARGIN
    assert need <= room, (
        f"the library's minimum height is now {need} px, more than the {room} px a 13\" Air can "
        f"give it — PRIVACY_NOTE has outgrown the smallest screen this app targets")
    dlg.hide()
    dlg.deleteLater()


def test_dialog_never_reopens_too_small_to_show_the_list():
    """QA W9-03: the remembered size had no floor. One drag to the corner stored the layout's own
    minimum, where the table shows 0.97 of ONE row of the library — and every future open came
    back that way, with nothing in the dialog to undo it.

    The floor is applied to what OPENS, not to what is STORED: the pref still records the size the
    user asked for (asserted below), so the app is never caught silently forgetting a resize, and
    the open is where the size is made usable again — the same contract _fit_to_screen already has
    for a size remembered on a bigger display."""
    from PySide6.QtGui import QGuiApplication
    path = prefs.prefs_path()
    if os.path.exists(path):
        os.remove(path)
    try:
        shrunk = _wired_dialog(_many_entries())
        shrunk.show()
        _settle()
        shrunk.resize(1, 1)                          # the user drags the corner all the way in
        _settle()
        tiny = (shrunk.width(), shrunk.height())
        assert _rows_visible(shrunk) < 1.5, _rows_visible(shrunk)
        shrunk.done(0)
        assert prefs.library_size() == tiny, (prefs.library_size(), tiny)

        again = _wired_dialog(_many_entries())
        again.show()
        _settle()
        # A display too small to grant the floor is the screen's call, not this dialog's (the
        # clamp is _fit_to_screen's job and has its own test), so only assert where there is room.
        avail = QGuiApplication.primaryScreen().availableGeometry()
        room = _fit_to_screen(again.width(), 10_000, avail.width(), avail.height())[1]
        if room >= 700:
            assert _rows_visible(again) >= 5, (
                f"re-opened at {again.width()}x{again.height()} showing "
                f"{_rows_visible(again):.2f} rows of the library — a size remembered from one "
                f"stray drag still cannot show the list it exists to show")
            assert again.height() > tiny[1], (
                f"re-opened at the stored {again.height()} px, the same height the drag left it "
                f"at — the floor was not applied")
        for dlg in (shrunk, again):
            dlg.hide()
            dlg.deleteLater()
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_the_browsable_height_floor_still_gets_the_width_it_assumes():
    """QA W11-02, corrected. The finding was that #172's floor is on the wrong axis: it bounds only
    the HEIGHT, "on the stated argument that the width minimum is 581 px — it is 234 px", so one
    corner drag leaves a 234-px-wide dialog showing 0.63 of one row for ever.

    234 px is the minimum of a dialog built with only `open_recording` — two buttons. The app never
    builds that one: ``StudioWindow._open_library`` wires all six file-op callbacks, and that button
    row's minimum is 581 px. Measured on the shipping wiring, the finding's own gesture gives
    581x522 stored -> 581x680 re-opened -> 6.23 rows, i.e. the floor doing exactly what #172 said.
    (The unwired dialog reaches 234x662 -> 234x680 -> 1.23 rows, which is the number the finding
    reports.) So there is no width floor here: at every size the app can actually reach, the height
    floor is enough.

    What was missing is this test. The 680 px was derived AT a width no code guarantees — it is a
    side effect of how many buttons the row has — so the premise could rot silently, exactly the way
    the guard in test_charts_panel.py rotted. It is asserted now, at both halves: the width the
    button row grants, and the rows the floor buys after the worst drag a user can perform.

    The gesture is `minimumSizeHint()` re-read until it converges, not `resize(1, 1)`: the privacy
    note is a WrapLabel that re-asserts its wrapped height AFTER each resize, so the layout minimum
    grows under the drag and a single resize stops short of where a real drag ends up."""
    from PySide6.QtGui import QGuiApplication

    from studio.library_dialog import _BROWSABLE_H_MEASURED_AT_W
    path = prefs.prefs_path()
    if os.path.exists(path):
        os.remove(path)
    try:
        shrunk = _wired_dialog(_many_entries())
        shrunk.show()
        _settle()
        for _ in range(4):                           # the WrapLabel feedback loop; settles in 2
            hint = shrunk.minimumSizeHint()
            shrunk.resize(hint.width(), hint.height())
            _settle()
        tiny = (shrunk.width(), shrunk.height())
        assert tiny[0] >= _BROWSABLE_H_MEASURED_AT_W, (
            f"the layout's minimum width is now {tiny[0]} px, under the "
            f"{_BROWSABLE_H_MEASURED_AT_W} px _MIN_BROWSABLE_H was measured at — the button row no "
            f"longer grants the width the HEIGHT floor needs to buy 5 rows, so the floor is a "
            f"number derived at a width the dialog can no longer be relied on to have")
        shrunk.done(0)
        assert prefs.library_size() == tiny, (prefs.library_size(), tiny)

        again = _wired_dialog(_many_entries())
        again.show()
        _settle()
        avail = QGuiApplication.primaryScreen().availableGeometry()
        room = _fit_to_screen(again.width(), 10_000, avail.width(), avail.height())[1]
        if room >= 700:                              # a screen too small to grant it is its call
            assert _rows_visible(again) >= 5, (
                f"re-opened at {again.width()}x{again.height()} showing "
                f"{_rows_visible(again):.2f} rows — the drag a user can actually perform still "
                f"leaves the library unable to show a library")
        for dlg in (shrunk, again):
            dlg.hide()
            dlg.deleteLater()
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_dialog_clear_confirm_names_the_copy_it_keeps_and_the_way_back():
    """QA W7-04: the confirm named what SURVIVES a wipe (videos, sidecars) and never said whether
    the index itself could come back — it could not. It must now name the copy AND the route back,
    which differs by what is wired: Restore… when the app injects it, else the .bak file that
    "Reveal in Finder" opens the folder for."""
    from PySide6.QtWidgets import QMessageBox
    idx = _many_entries(3)
    seen = []
    orig = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: (seen.append(a[2]), QMessageBox.No)[1])
    try:
        bare = LibraryDialog(idx, _OpenSpy(), clear_library=library.empty_index)
        bare._on_clear_library()
        assert "library.json.bak" in seen[-1], seen[-1]
        assert "Reveal in Finder" in seen[-1], seen[-1]

        with_restore = LibraryDialog(idx, _OpenSpy(), clear_library=library.empty_index,
                                     restore_library=lambda: idx,
                                     backup_info=lambda: {"entries": 3, "mtime": None})
        with_restore._on_clear_library()
        assert "library.json.bak" in seen[-1], seen[-1]
        assert "Restore" in seen[-1], seen[-1]
    finally:
        QMessageBox.question = orig
    bare.deleteLater()
    with_restore.deleteLater()


def test_dialog_restore_confirm_names_both_sides_and_routes_through_the_callback():
    """The dialog had a "Back up…" and no way to read one back. Restore… is dependency-injected like
    every other file op: it confirms naming BOTH sides of the swap (a restore replaces a live index,
    so the count it is about to replace has to be on screen), fires the callback, and re-renders from
    the index it returns."""
    from PySide6.QtWidgets import QMessageBox
    idx = _many_entries(2)
    restored_to = library.empty_index()
    for stem in ("GX010080", "GX010081", "GX010082"):
        library.upsert(restored_to, _entry(stem, track="MK", paths=[]))
    calls, seen = [], []
    orig = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: (seen.append(a[2]), QMessageBox.Yes)[1])
    try:
        dlg = LibraryDialog(idx, _OpenSpy(), clear_library=library.empty_index,
                            restore_library=lambda: (calls.append(True), restored_to)[1],
                            backup_info=lambda: {"entries": 3, "mtime": 1_700_000_000.0})
        assert dlg.restore_btn.isEnabled()
        assert dlg.table.rowCount() == 2
        dlg._on_restore_library()
    finally:
        QMessageBox.question = orig
    assert calls == [True]
    assert "2 recordings" in seen[-1] and "3 recordings" in seen[-1], seen[-1]
    assert dlg.table.rowCount() == 3                     # re-rendered from the returned index
    dlg.deleteLater()


def test_dialog_restore_button_is_shown_disabled_before_there_is_a_backup():
    """The way back is visible BEFORE it is needed: with no backup yet the button is present but
    disabled and says why, rather than appearing out of nowhere once history is already gone. With
    no callbacks wired at all it isn't built (the browse-only DI contract)."""
    idx = _many_entries(2)
    empty = LibraryDialog(idx, _OpenSpy(), restore_library=lambda: idx, backup_info=lambda: None)
    assert empty.restore_btn.isEnabled() is False
    assert "No library backup yet" in empty.restore_btn.toolTip()
    # A query that raises must degrade to "no restore offered", never break the dialog.
    def _boom():
        raise OSError("app-support unreadable")
    broken = LibraryDialog(idx, _OpenSpy(), restore_library=lambda: idx, backup_info=_boom)
    assert broken.restore_btn.isEnabled() is False
    assert getattr(LibraryDialog(idx, _OpenSpy()), "restore_btn", None) is None
    empty.deleteLater()
    broken.deleteLater()


def test_prefs_library_size_roundtrips_and_rejects_garbage():
    """The persisted Library size is shape-guarded on read (two positive real ints; bool is an int
    subclass, so it is rejected explicitly) and its writer is fully guarded — remembering a window
    size must never disrupt the UI."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "prefs.json")
        assert prefs.library_size(p) is None                       # unset
        prefs.set_library_size(900, 780, p)
        assert prefs.library_size(p) == (900, 780)
        for bad in ([900], [900, 780, 1], ["900", "780"], [True, 780], [900, 0], [-1, 780], 900):
            prefs.set(prefs.LIBRARY_SIZE, bad, p)
            assert prefs.library_size(p) is None, bad
        prefs.set_library_size(900, 780, p)
        prefs.set_library_size("wide", 780, p)                     # non-numeric → ignored, not fatal
        assert prefs.library_size(p) == (900, 780)


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
