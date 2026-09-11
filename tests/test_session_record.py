"""Tests for the session record — the per-recording note of conditions, tyres and setup.

Three halves:

  * THE STORE (``studio.session_record``, no Qt): schema round-trip, the empty-record-is-a-removal
    rule, ``prefill``'s carry-forward, ``comparable``'s diffing — and, at length, the PERSISTENCE
    DISCIPLINE, because this is the most irreplaceable data pacer holds. A library row comes back
    the moment the footage is re-opened; a hand-typed tyre pressure does not, so every corruption,
    version and destructive path is asserted rather than assumed: corrupt bytes preserved verbatim
    in the ``.bak`` before the first overwrite, a NEWER file's unknown fields surviving a v1
    round-trip, an older/unstamped file migrated with every record kept, one malformed record
    dropped and the rest kept, the atomic write leaving no ``.tmp``, and clear / forget / restore
    each taking their copy first.
  * THE FORM (``studio.session_record_dialog``, offscreen Qt): the round-trip a driver actually
    performs — open, fill, read back, store, reload — plus the two states that are not a save
    (an emptied form removes the record; Delete reports itself), and the auto-stamped header.
  * THE SURFACES (``studio.library_dialog`` + ``studio.central_view``): that the record is visible
    where the comparison happens. The Library's two columns and conditions filter, the search box
    reaching a tyre set by name, the selected row's record line and its like-for-like verdict, and
    the lap panel's header chip — including that it is actually MOUNTED in the real view's header,
    not merely constructed.

CRITICAL: every test points the store at a TEMP directory (monkeypatching
``session_record._app_support_dir``, the single seam) — the suite NEVER touches the user's real
``~/Library/Application Support/pacer/``.

Run: QT_QPA_PLATFORM=offscreen python tests/test_session_record.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()            # module scope, BEFORE any widget: the shipping font stack

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QLabel  # noqa: E402

from studio import library  # noqa: E402
from studio import library_dialog as libdlg  # noqa: E402
from studio import session_record as sr
from studio.session_record_dialog import (  # noqa: E402
    SessionRecordDialog,
    carried_over_line,
    context_line,
)

# The Library dialog reads its remembered size from studio.prefs on construction and writes it
# back on close — redirect that seam too, module-wide, before any dialog exists (the same rule as
# the store below: the suite never reads or writes the user's real app-support dir).
_PREFS_TMP = tempfile.TemporaryDirectory(prefix="pacer-test-record-prefs-")
from studio import prefs  # noqa: E402

prefs._app_support_dir = lambda: _PREFS_TMP.name


# ============================================================================ helpers
def _tmp_store(tmp):
    """Point the store at `tmp` and return the path it resolves to."""
    sr._app_support_dir = lambda: tmp
    return sr.records_path()


def _record(**over):
    """A filled record: a dry day on a 42-lap set of MG Yellows, 11/82, on an OTK."""
    rec = sr.blank_record()
    rec.update({"conditions": "dry", "air_temp_c": 24.0, "track_temp_c": 38.0,
                "humidity_pct": 41.0, "tyre_set": "MG Yellow #3", "tyre_laps": 42,
                "cold_front": 10.0, "cold_rear": 10.5, "hot_front": 13.0, "hot_rear": 13.5,
                "chassis": "OTK 401R", "sprocket_front": 11, "sprocket_rear": 82,
                "axle": "H", "seat": "5 mm back", "notes": "loose on entry to 3"})
    rec.update(over)
    return sr._norm_record(rec)


# The Library greys and DISABLES a row whose file is gone, and a disabled row cannot be selected —
# so an entry built for these tests points at a real (empty) file. Held at module scope so the
# finalizer removes the directory at exit.
_FILES_TMP = tempfile.TemporaryDirectory(prefix="pacer-test-record-media-")


def _entry(stem="GX010062", *, track="Daytona MK", date="2026-06-14", laps=22, best=68.201,
           verified=True, degraded=False, dropout=False):
    path = os.path.join(_FILES_TMP.name, f"{stem}.MP4")
    if not os.path.exists(path):
        open(path, "wb").close()
    return {"fingerprint": library.fingerprint(stem), "stem": stem, "track": track, "date": date,
            "lap_count": laps, "best": best, "theoretical": None, "verified": verified,
            "degraded": degraded, "dropout": dropout, "paths": [path]}


# ============================================================================ 1. the store
def test_a_record_round_trips_through_the_file_unchanged():
    """The whole point of a store: what goes in comes out. Numbers keep their type (a pressure is
    not silently an int), text keeps its spelling, and the key is the library fingerprint."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _tmp_store(tmp)
        rec = _record()
        sr.put_and_save("GX0062", rec)
        back = sr.get(sr.load(), "GX0062")
        for key in (*sr.TEXT_FIELDS, *sr.NUM_FIELDS, *sr.INT_FIELDS, "conditions",
                    "pressure_unit"):
            assert back[key] == rec[key], f"{key}: {back[key]!r} != {rec[key]!r}"
        assert back["updated"], "a stored record is stamped with when it was written"
        assert json.load(open(path))["version"] == sr.VERSION
        print("test_a_record_round_trips_through_the_file_unchanged OK")


def test_an_empty_record_is_never_stored_and_an_emptied_one_is_removed():
    """A form nobody completed must leave no trace. An all-dashes row in the Library would claim a
    session was documented when it was not — which is the exact failure this feature prevents."""
    with tempfile.TemporaryDirectory() as tmp:
        _tmp_store(tmp)
        store = sr.empty_store()
        sr.put(store, "GX0062", sr.blank_record())
        assert store["records"] == {}, "an untouched form is not a record"
        # A record that carries only its AUTO-STAMPED context is still empty: the app filled those
        # in, the driver filled in nothing.
        stamped = sr.stamp_context(sr.blank_record(), _entry())
        assert sr.is_empty(stamped)
        sr.put(store, "GX0062", stamped)
        assert store["records"] == {}
        # …and clearing an existing one out through the same call removes it.
        sr.put(store, "GX0062", _record())
        assert "GX0062" in store["records"]
        sr.put(store, "GX0062", sr.blank_record())
        assert "GX0062" not in store["records"], "an emptied form deletes the record"
        print("test_an_empty_record_is_never_stored_and_an_emptied_one_is_removed OK")


def test_a_corrupt_file_reads_as_empty_and_its_bytes_survive_the_first_write():
    """THE PREFS DEFECT (#222), asserted here so it cannot be repeated on data that matters more.
    Unreadable bytes read as an empty store — there is nothing else they could read as — but the
    first write that overwrites them copies them to the ``.bak`` VERBATIM first, and a later write
    over the now-healthy file churns no backup."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _tmp_store(tmp)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        garbage = '{"version": 1, "records": {"GX0062": {"tyre_set": "MG'   # truncated mid-write
        open(path, "w").write(garbage)
        assert sr.load() == sr.empty_store(), "corrupt bytes must not crash the store"
        sr.put_and_save("GX0063", _record())
        bak = sr.backup_path(path)
        assert os.path.exists(bak), "the unreadable bytes were overwritten with no copy kept"
        assert open(bak).read() == garbage, "the .bak must hold the ORIGINAL bytes, verbatim"
        # The file healed; the next write leaves the sidecar alone (no churn over a good file).
        stamp = os.path.getmtime(bak)
        sr.put_and_save("GX0064", _record(tyre_set="MG Red"))
        assert os.path.getmtime(bak) == stamp, "a healthy write must not re-take the backup"
        assert set(sr.load()["records"]) == {"GX0063", "GX0064"}
        print("test_a_corrupt_file_reads_as_empty_and_its_bytes_survive_the_first_write OK")


def test_a_newer_file_is_read_best_effort_and_its_unknown_fields_survive():
    """A DOWNGRADE must not destroy a later schema's data. This is the one place the store is
    stricter than library.py: a library entry is re-derivable from the footage and a hand-typed
    field is not, so ``_norm_record`` carries unknown keys through a v1 round-trip."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _tmp_store(tmp)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        future = {"version": sr.VERSION + 5,
                  "records": {"GX0062": {**_record(), "rain_light_mm": 3.2,
                                         "tyre_first_used": "2026-05-01"}}}
        json.dump(future, open(path, "w"))
        loaded = sr.load()
        assert loaded["records"]["GX0062"]["tyre_set"] == "MG Yellow #3", "known fields still read"
        assert loaded["records"]["GX0062"]["rain_light_mm"] == 3.2, "an unknown field was dropped"
        # …and it survives a WRITE by this build, which is the half that actually loses data.
        sr.save(loaded)
        again = sr.load()
        assert again["records"]["GX0062"]["rain_light_mm"] == 3.2
        assert again["records"]["GX0062"]["tyre_first_used"] == "2026-05-01"
        # The newer file was backed up before this build stamped it down to v1.
        assert json.load(open(sr.backup_path(path)))["version"] == sr.VERSION + 5
        print("test_a_newer_file_is_read_best_effort_and_its_unknown_fields_survive OK")


def test_an_older_or_unstamped_file_is_migrated_with_every_record_kept():
    """A schema bump must MIGRATE, never wipe. v1 is the first schema so ``_migrate`` is the
    identity today — the assertion is that the hook RUNS and preserves, which is what makes the
    first real bump a one-line change instead of a data-loss decision."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _tmp_store(tmp)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        legacy = {"version": 0, "records": {"GX0062": _record(), "GX0060": _record(
            conditions="wet", tyre_set="MG Wet")}}
        json.dump(legacy, open(path, "w"))
        loaded = sr.load()
        assert set(loaded["records"]) == {"GX0062", "GX0060"}, "a migration must keep every record"
        assert loaded["version"] == sr.VERSION
        assert loaded["records"]["GX0060"]["conditions"] == "wet"
        # An older file is round-trippable, so no backup churn — only un-round-trippable bytes and
        # the deliberate destructive acts take the one .bak slot.
        sr.save(loaded)
        assert not os.path.exists(sr.backup_path(path)), "a migrated file must not churn the .bak"
        print("test_an_older_or_unstamped_file_is_migrated_with_every_record_kept OK")


def test_one_malformed_record_is_dropped_and_the_rest_are_kept():
    """The library's rule, for the same reason: rewriting only the survivors heals the file, and
    losing a whole notebook to one bad row is the failure worth ruling out."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _tmp_store(tmp)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump({"version": 1, "records": {"GX0062": _record(), "GX0060": "not a record",
                                             "": _record()}}, open(path, "w"))
        loaded = sr.load()
        assert set(loaded["records"]) == {"GX0062"}
        assert loaded["records"]["GX0062"]["tyre_set"] == "MG Yellow #3"
        print("test_one_malformed_record_is_dropped_and_the_rest_are_kept OK")


def test_a_junk_value_in_a_field_falls_back_rather_than_taking_the_record_down():
    """Per-FIELD validation, so a hand-edited file costs one value and not the record. A NaN
    pressure would print as a number and sort as a hole; a negative lap count is not a count; a
    condition outside the vocabulary would break the filter it exists for."""
    with tempfile.TemporaryDirectory() as tmp:
        _tmp_store(tmp)
        rec = sr._norm_record({"conditions": "monsoon", "air_temp_c": float("nan"),
                               "tyre_laps": -3, "tyre_set": 12, "pressure_unit": "bananas",
                               "sprocket_front": True, "cold_front": "10"})
        assert rec["conditions"] == ""            # not in CONDITIONS -> untagged
        assert rec["air_temp_c"] is None          # NaN is not a temperature
        assert rec["tyre_laps"] is None           # negative is not a count
        assert rec["tyre_set"] == ""              # a number is not a free-text set name
        assert rec["pressure_unit"] == sr.DEFAULT_PRESSURE_UNIT
        assert rec["sprocket_front"] is None      # bool is an int subclass; not a tooth count
        assert rec["cold_front"] is None          # a string is not a pressure
        print("test_a_junk_value_in_a_field_falls_back_rather_than_taking_the_record_down OK")


def test_the_write_is_atomic_and_creates_its_directory():
    """Temp file + os.replace, so a crash mid-write cannot leave a truncated notebook — and no
    stray .tmp survives a successful write."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _tmp_store(os.path.join(tmp, "does", "not", "exist"))
        assert not os.path.isdir(os.path.dirname(path))
        sr.put_and_save("GX0062", _record())
        assert os.path.exists(path)
        assert not os.path.exists(path + ".tmp"), "the atomic temp file was left behind"
        print("test_the_write_is_atomic_and_creates_its_directory OK")


def test_clear_forget_and_restore_each_keep_a_copy():
    """The three destructive paths. Each copies the store to the ONE ``.bak`` slot first, and
    ``restore`` is a reversible SWAP — the store it replaces becomes the new backup."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _tmp_store(tmp)
        sr.put_and_save("GX0062", _record())
        sr.put_and_save("GX0060", _record(tyre_set="MG Red"))

        # FORGET one: backed up, and the other record survives.
        store = sr.remove_and_save("GX0062")
        assert set(store["records"]) == {"GX0060"}
        assert set(sr.load(sr.backup_path(path))["records"]) == {"GX0062", "GX0060"}
        # …and forgetting a recording that never had a record takes no backup (no churn).
        stamp = os.path.getmtime(sr.backup_path(path))
        sr.remove_and_save("GX9999")
        assert os.path.getmtime(sr.backup_path(path)) == stamp

        # CLEAR: everything gone from the live file, everything in the backup.
        sr.clear()
        assert sr.load()["records"] == {}
        assert set(sr.load(sr.backup_path(path))["records"]) == {"GX0060"}
        assert sr.backup_summary()["records"] == 1

        # RESTORE: the swap puts it back, and is itself reversible.
        restored = sr.restore()
        assert set(restored["records"]) == {"GX0060"}
        assert sr.backup_summary() is None, "the empty store it replaced is now the backup"
        print("test_clear_forget_and_restore_each_keep_a_copy OK")


def test_restore_refuses_an_empty_or_missing_backup():
    """Replacing a live notebook with nothing is the very data loss restore exists to undo."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _tmp_store(tmp)
        sr.put_and_save("GX0062", _record())
        assert set(sr.restore()["records"]) == {"GX0062"}, "no backup -> leave the store alone"
        sr.save(sr.empty_store(), sr.backup_path(path))
        assert set(sr.restore()["records"]) == {"GX0062"}, "an empty backup must not be restored"
        print("test_restore_refuses_an_empty_or_missing_backup OK")


# ============================================================================ 2. fast to fill in
def test_prefill_carries_the_kart_forward_and_advances_the_tyre_laps():
    """The feature's real test. The kart did not change overnight, so the form opens already
    answered on the parts that persist — and the tyre laps, the one genuinely derivable number,
    advance by the last session's own lap count."""
    with tempfile.TemporaryDirectory() as tmp:
        _tmp_store(tmp)
        store = sr.empty_store()
        sr.put(store, "GX0060", sr.stamp_context(_record(), _entry("GX010060", laps=22)))
        new = sr.prefill(store, exclude="GX0062", entry=_entry("GX010062", laps=18))
        for key in sr.STICKY_FIELDS:
            if key != "tyre_laps":
                assert new[key] == _record()[key] or key == "tyre_laps", key
        assert new["tyre_laps"] == 42 + 22, "the set's laps advance by the last session's count"
        # …and NOTHING about the day carries. Yesterday's weather in today's record would be a
        # fabricated observation — which is what this store exists to replace.
        assert new["conditions"] == ""
        assert new["air_temp_c"] is None and new["humidity_pct"] is None
        assert new["cold_front"] is None and new["hot_rear"] is None
        assert new["notes"] == ""
        # The auto-stamp names the session being written up, not the one carried from.
        assert new["date"] == "2026-06-14" and new["lap_count"] == 18
        print("test_prefill_carries_the_kart_forward_and_advances_the_tyre_laps OK")


def test_prefill_does_not_advance_the_laps_of_a_different_tyre_set():
    """A new set starts at whatever the driver says. Guessing here would silently claim a fresh
    set was 64 laps old, which is worse than the blank field it replaced."""
    with tempfile.TemporaryDirectory() as tmp:
        _tmp_store(tmp)
        store = sr.empty_store()
        sr.put(store, "GX0060", sr.stamp_context(_record(tyre_set=""), _entry(laps=22)))
        new = sr.prefill(store, exclude="GX0062", entry=_entry("GX010062"))
        assert new["tyre_laps"] is None
        # An empty store prefills nothing at all (the first record ever).
        assert sr.prefill(sr.empty_store())["chassis"] == ""
        print("test_prefill_does_not_advance_the_laps_of_a_different_tyre_set OK")


def test_prefill_takes_the_most_recently_written_record():
    """`latest` orders by when the driver TYPED it, not by lap count or insertion order — the last
    thing he wrote down is the closest description of the kart as it stands."""
    with tempfile.TemporaryDirectory() as tmp:
        _tmp_store(tmp)
        store = sr.empty_store()
        store["records"]["old"] = sr._norm_record(
            {**_record(chassis="OLD"), "updated": "2026-01-01T09:00:00"})
        store["records"]["new"] = sr._norm_record(
            {**_record(chassis="NEW"), "updated": "2026-06-01T09:00:00"})
        assert sr.latest(store)["chassis"] == "NEW"
        assert sr.latest(store, exclude="new")["chassis"] == "OLD"
        assert sr.prefill(store, exclude="new")["chassis"] == "OLD"
        print("test_prefill_takes_the_most_recently_written_record OK")


# ============================================================================ 3. comparability
def test_comparable_reports_only_differences_it_can_actually_see():
    """The sentence the whole feature exists for — and the silence that keeps it honest. An
    unrecorded value is UNKNOWN, so it is never a difference: reporting "tyres differ" because one
    session forgot to write them down would manufacture the doubt this is meant to resolve."""
    dry, wet = _record(), _record(conditions="wet", air_temp_c=12.0, track_temp_c=14.0)
    diffs = sr.comparable(dry, wet)
    assert any("Dry vs Wet" in d for d in diffs)
    assert any(d.startswith("air ") for d in diffs)
    assert any(d.startswith("track ") for d in diffs)
    # Same day, same kart -> nothing to report.
    assert sr.comparable(dry, _record()) == []
    # A difference SMALLER than the threshold is not worth a lap time (the tolerances are coarse
    # on purpose: they flag what moves a lap, not what a thermometer can measure).
    assert sr.comparable(dry, _record(air_temp_c=25.0)) == []
    assert sr.comparable(dry, _record(air_temp_c=30.0)) != []
    # A field only ONE side recorded is not a difference.
    assert sr.comparable(dry, _record(air_temp_c=None)) == []
    assert sr.comparable(dry, sr.blank_record()) == []
    # …and neither is a missing record at all.
    assert sr.comparable(dry, None) == [] and sr.comparable(None, dry) == []
    # Gearing and the kart parts, once BOTH sides state them.
    assert any("gearing" in d for d in sr.comparable(dry, _record(sprocket_rear=80)))
    assert any("axle" in d for d in sr.comparable(dry, _record(axle="U")))
    print("test_comparable_reports_only_differences_it_can_actually_see OK")


def test_the_text_helpers_read_the_way_a_racer_writes():
    """One record, one set of number formats — "24" not "24.0", the pressures' unit said once."""
    rec = _record()
    assert sr.conditions_text(rec) == "Dry  ·  air 24°  ·  track 38°  ·  41% RH"
    assert sr.tyre_text(rec) == "MG Yellow #3, 42 laps"
    assert sr.tyre_text(_record(tyre_laps=1)) == "MG Yellow #3, 1 lap"
    assert sr.pressure_text(rec) == "cold 10/10.5, hot 13/13.5 psi"
    assert sr.pressure_text(_record(pressure_unit="bar", cold_front=1.05, cold_rear=1.1,
                                    hot_front=None, hot_rear=None)) == "cold 1.05/1.1 bar"
    assert sr.kart_text(rec) == "OTK 401R  ·  11/82  ·  axle H  ·  seat 5 mm back"
    assert sr.summary_line(None) == "" and sr.summary_line(sr.blank_record()) == ""
    assert sr.filled_fields(sr.blank_record()) == 0
    print("test_the_text_helpers_read_the_way_a_racer_writes OK")


# ============================================================================ 4. the form
def test_the_form_round_trips_a_record_through_the_real_dialog():
    """The gesture a driver actually performs: open the form on a fresh recording, type, save,
    re-open — and find what he typed. Driven on the REAL dialog, not on the store beneath it."""
    with tempfile.TemporaryDirectory() as tmp:
        _tmp_store(tmp)
        entry = _entry()
        dlg = SessionRecordDialog(sr.prefill(sr.load(), entry=entry), entry=entry,
                                  name="GX010062.MP4", is_new=True)
        dlg.conditions.setCurrentIndex(dlg.conditions.findData("dry"))
        dlg.air.setText("24")
        dlg.track_temp.setText("38")
        dlg.humidity.setText("41")
        dlg.tyre_set.setText("MG Yellow #3")
        dlg.tyre_laps.setText("42")
        dlg.cold_front.setText("10")
        dlg.cold_rear.setText("10,5")          # a European comma decimal, typed as typed
        dlg.sprocket_front.setText("11")
        dlg.sprocket_rear.setText("82")
        dlg.notes.setPlainText("loose on entry to 3")
        out = dlg.result_record()
        sr.put_and_save(entry["fingerprint"], out)

        back = sr.get(sr.load(), entry["fingerprint"])
        assert back["conditions"] == "dry"
        assert back["air_temp_c"] == 24.0 and back["track_temp_c"] == 38.0
        assert back["cold_rear"] == 10.5, "a comma decimal must reach the file as 10.5"
        assert back["tyre_set"] == "MG Yellow #3" and back["tyre_laps"] == 42
        assert back["sprocket_front"] == 11 and back["sprocket_rear"] == 82
        assert back["notes"] == "loose on entry to 3"
        # AUTO-STAMPED, never typed: the record knows which session it describes.
        assert back["date"] == "2026-06-14" and back["track"] == "Daytona MK"
        assert back["lap_count"] == 22

        # Re-opening the form shows it back — the fields are populated from the stored record.
        again = SessionRecordDialog(back, entry=entry, name="GX010062.MP4", is_new=False)
        assert again.tyre_set.text() == "MG Yellow #3"
        assert again.air.text() == "24", "a stored 24.0 must not read back as '24.0'"
        assert again.cold_rear.text() == "10.5"
        assert again.conditions.currentData() == "dry"
        assert again.notes.toPlainText() == "loose on entry to 3"
        print("test_the_form_round_trips_a_record_through_the_real_dialog OK")


def test_an_untouched_form_stores_nothing_and_an_emptied_one_removes_the_record():
    """Nothing is required, so an untouched form is a legitimate (and empty) save."""
    with tempfile.TemporaryDirectory() as tmp:
        _tmp_store(tmp)
        entry = _entry()
        dlg = SessionRecordDialog(sr.prefill(sr.load(), entry=entry), entry=entry, is_new=True)
        sr.put_and_save(entry["fingerprint"], dlg.result_record())
        assert sr.load()["records"] == {}, "an untouched form must leave no record"

        sr.put_and_save(entry["fingerprint"], _record())
        dlg = SessionRecordDialog(sr.get(sr.load(), entry["fingerprint"]), entry=entry,
                                  is_new=False)
        for edit in (dlg.tyre_set, dlg.air, dlg.track_temp, dlg.humidity, dlg.tyre_laps,
                     dlg.cold_front, dlg.cold_rear, dlg.hot_front, dlg.hot_rear, dlg.chassis,
                     dlg.sprocket_front, dlg.sprocket_rear, dlg.axle, dlg.seat):
            edit.setText("")
        dlg.conditions.setCurrentIndex(0)
        dlg.notes.setPlainText("")
        sr.put_and_save(entry["fingerprint"], dlg.result_record())
        assert sr.load()["records"] == {}, "an emptied form must delete the record"
        print("test_an_untouched_form_stores_nothing_and_an_emptied_one_removes_the_record OK")


def test_the_form_offers_delete_only_when_there_is_a_record_to_delete():
    """A permanently-greyed control on a first-run form is noise; the editor for a session with no
    record has nothing to delete."""
    entry = _entry()
    assert getattr(SessionRecordDialog(sr.blank_record(), entry=entry, is_new=True),
                   "delete_btn", None) is None
    dlg = SessionRecordDialog(_record(), entry=entry, is_new=False)
    assert dlg.delete_btn is not None
    assert dlg.deleted() is False, "nothing is deleted until the confirm is answered"
    print("test_the_form_offers_delete_only_when_there_is_a_record_to_delete OK")


def test_the_form_states_its_context_and_what_it_carried_over():
    """Two lines that are not fields. The first is what pacer already knows (so the driver never
    types it); the second names what ``prefill`` answered on his behalf, because an unannounced
    pre-filled field is how a chassis change goes unrecorded."""
    assert context_line(_entry()) == "Daytona MK  ·  2026-06-14  ·  22 laps  ·  best 1:08.201"
    assert context_line({"lap_count": 0, "track": None, "date": None, "best": None}) == ""
    assert context_line(None) == ""
    carried = carried_over_line(_record())
    assert "MG Yellow #3" in carried and "OTK 401R" in carried
    assert "correct anything that changed" in carried
    assert carried_over_line(sr.blank_record()) == "", "nothing carried -> nothing claimed"
    # …and the line is only shown for a NEW record: an existing one carried nothing.
    dlg = SessionRecordDialog(_record(), entry=_entry(), is_new=False)
    assert not any(carried in lb.text() for lb in dlg.findChildren(QLabel))
    print("test_the_form_states_its_context_and_what_it_carried_over OK")


def test_the_form_shows_what_the_tyres_will_have_done_after_this_session():
    """The arithmetic the app can do and the driver should not have to. Silent without both
    halves — a stale sum beside an emptied field is worse than no hint."""
    dlg = SessionRecordDialog(sr.blank_record(), entry=_entry(laps=22), is_new=True)
    assert dlg._tyre_after.text() == ""
    dlg.tyre_laps.setText("42")
    assert dlg._tyre_after.text() == "→ 64 after this session"
    dlg.tyre_laps.setText("")
    assert dlg._tyre_after.text() == ""
    # No library entry (so no lap count) -> nothing to add.
    bare = SessionRecordDialog(sr.blank_record(), entry=None, is_new=True)
    bare.tyre_laps.setText("42")
    assert bare._tyre_after.text() == ""
    print("test_the_form_shows_what_the_tyres_will_have_done_after_this_session OK")


# ============================================================================ 5. the Library
def _library_dialog(entries, records, **kw):
    """The Library dialog with the session-record wiring the app injects."""
    index = {"version": library.VERSION, "entries": entries}
    return libdlg.LibraryDialog(index, lambda paths: None, records=records, **kw)


def _cells(dlg, col):
    """Every row's text in one column, in the table's current order."""
    return [dlg.table.item(r, col).text() for r in range(dlg.table.rowCount())]


def _visible_rows(dlg):
    return [r for r in range(dlg.table.rowCount()) if not dlg.table.isRowHidden(r)]


def test_the_library_shows_the_conditions_and_tyre_age_beside_the_lap_times():
    """The columns that say whether the two time columns beside them are comparable. A row with no
    record shows an em dash in both — never a fabricated value."""
    store = sr.empty_store()
    sr.put(store, library.fingerprint("GX010062"), _record())
    dlg = _library_dialog([_entry("GX010062"), _entry("GX010060", date="2026-06-01")], store)
    dlg.table.sortItems(libdlg._COL_DATE, Qt.DescendingOrder)
    assert _cells(dlg, libdlg._COL_COND) == ["Dry", "—"]
    assert _cells(dlg, libdlg._COL_TYRES) == ["42", "—"]
    # The Tyres cell hovers with the WHOLE record; the undocumented row hovers with the invitation.
    assert "MG Yellow #3" in dlg.table.item(0, libdlg._COL_TYRES).toolTip()
    assert "No session record" in dlg.table.item(1, libdlg._COL_COND).toolTip()
    print("test_the_library_shows_the_conditions_and_tyre_age_beside_the_lap_times OK")


def test_the_tyres_column_sorts_by_age_not_by_text():
    """"Sort by tyre age" is the click that answers "am I comparing a fresh set with a worn one?",
    so the cell carries a numeric key — 9 must not sort above 40 the way its text does."""
    store = sr.empty_store()
    sr.put(store, library.fingerprint("GX010062"), _record(tyre_laps=9))
    sr.put(store, library.fingerprint("GX010060"), _record(tyre_laps=40))
    dlg = _library_dialog([_entry("GX010062"), _entry("GX010060", date="2026-06-01"),
                           _entry("GX010058", date="2026-05-01")], store)
    dlg.table.sortItems(libdlg._COL_TYRES, Qt.AscendingOrder)
    assert _cells(dlg, libdlg._COL_TYRES) == ["9", "40", "—"], "blanks last, numbers by value"
    print("test_the_tyres_column_sorts_by_age_not_by_text OK")


def test_the_conditions_filter_narrows_to_one_kind_of_day():
    """Answering "was I comparing like for like?" by hiding the days that were not. The
    "No record" bucket is the absence of a record — NOT a record left untagged, which is a real
    record whose owner wrote down his pressures and not the weather."""
    store = sr.empty_store()
    sr.put(store, library.fingerprint("GX010062"), _record())                    # dry
    sr.put(store, library.fingerprint("GX010060"), _record(conditions="wet"))    # wet
    sr.put(store, library.fingerprint("GX010058"), _record(conditions=""))       # untagged
    entries = [_entry("GX010062"), _entry("GX010060", date="2026-06-01"),
               _entry("GX010058", date="2026-05-01"), _entry("GX010056", date="2026-04-01")]
    dlg = _library_dialog(entries, store)
    assert len(_visible_rows(dlg)) == 4

    dlg.condition_filter.setCurrentIndex(dlg.condition_filter.findData("dry"))
    assert len(_visible_rows(dlg)) == 1
    assert "1 of 4" in dlg._title.text(), "the header names what is on screen"

    dlg.condition_filter.setCurrentIndex(dlg.condition_filter.findData(libdlg._NO_RECORD))
    assert len(_visible_rows(dlg)) == 1, "only the row with no record at all"
    assert dlg.table.item(_visible_rows(dlg)[0], libdlg._COL_COND).text() == "—"

    # A filter that empties the table names the way back to ALL CONDITIONS, not to All tracks.
    dlg.search.setText("nothing matches this")
    assert not dlg._empty_note.isHidden()
    assert libdlg._ALL_CONDITIONS in dlg._empty_note.text()
    assert libdlg._ALL_TRACKS not in dlg._empty_note.text()
    print("test_the_conditions_filter_narrows_to_one_kind_of_day OK")


def test_the_search_box_reaches_a_tyre_set_by_name():
    """What the row SHOWS has to be findable by typing it — the rule the filename already follows.
    The record's own words are in the haystack, so "wet" and "MG Yellow" both land."""
    store = sr.empty_store()
    sr.put(store, library.fingerprint("GX010062"), _record())
    sr.put(store, library.fingerprint("GX010060"), _record(conditions="wet", tyre_set="MG Wet"))
    dlg = _library_dialog([_entry("GX010062"), _entry("GX010060", date="2026-06-01")], store)
    dlg.search.setText("mg yellow")
    assert len(_visible_rows(dlg)) == 1
    dlg.search.setText("wet")
    assert len(_visible_rows(dlg)) == 1
    print("test_the_search_box_reaches_a_tyre_set_by_name OK")


def test_the_selected_row_reads_its_record_or_invites_one():
    """A row with no record must read as an invitation, not as a grid of dashes: nothing is
    missing from the DATA — there is a note nobody has written."""
    store = sr.empty_store()
    sr.put(store, library.fingerprint("GX010062"), _record())
    dlg = _library_dialog([_entry("GX010062"), _entry("GX010060", date="2026-06-01")], store)
    dlg.table.sortItems(libdlg._COL_DATE, Qt.DescendingOrder)
    dlg.table.selectRow(0)
    assert "MG Yellow #3" in dlg._record_line.text()
    assert "cold 10/10.5" in dlg._record_line.text()
    dlg.table.selectRow(1)
    assert dlg._record_line.text() == libdlg._NO_RECORD_LINE
    # A SENTENCE, while the row's own two cells are em dashes — the cells say "no value", the line
    # says what to do about it, and neither pretends there is data missing.
    assert dlg.table.item(1, libdlg._COL_COND).text() == "—"
    assert "nothing written down" in dlg._record_line.text()
    print("test_the_selected_row_reads_its_record_or_invites_one OK")


def test_the_library_says_whether_a_row_is_like_for_like_with_the_tracks_best():
    """THE SENTENCE THE FEATURE EXISTS FOR — and the three silences that keep it honest."""
    fast, slow = _entry("GX010060", best=67.0, date="2026-06-01"), _entry("GX010062", best=69.0)
    store = sr.empty_store()
    sr.put(store, fast["fingerprint"], _record(conditions="dry", track_temp_c=38.0))
    sr.put(store, slow["fingerprint"], _record(conditions="wet", track_temp_c=14.0))
    dlg = _library_dialog([fast, slow], store)
    dlg.table.sortItems(libdlg._COL_DATE, Qt.DescendingOrder)   # the slower, newer row first
    dlg.table.selectRow(0)
    line = dlg._compare_line.text()
    assert line.startswith("Not like-for-like vs your best here (2026-06-01)")
    assert "Dry vs Wet" in line or "Wet vs Dry" in line
    assert "track " in line

    # The track's OWN best row has nothing to be unlike.
    dlg.table.selectRow(1)
    assert dlg._compare_line.text() == ""

    # Same conditions -> the reassuring converse, which is the answer the driver wants.
    same = sr.empty_store()
    sr.put(same, fast["fingerprint"], _record())
    sr.put(same, slow["fingerprint"], _record())
    dlg2 = _library_dialog([fast, slow], same)
    dlg2.table.sortItems(libdlg._COL_DATE, Qt.DescendingOrder)
    dlg2.table.selectRow(0)
    assert dlg2._compare_line.text().startswith("Comparable with your best here")

    # And SILENCE when either side has no record: "comparable" from two blanks would be a
    # reassurance backed by nothing.
    half = sr.empty_store()
    sr.put(half, slow["fingerprint"], _record())
    dlg3 = _library_dialog([fast, slow], half)
    dlg3.table.sortItems(libdlg._COL_DATE, Qt.DescendingOrder)
    dlg3.table.selectRow(0)
    assert dlg3._compare_line.text() == ""
    print("test_the_library_says_whether_a_row_is_like_for_like_with_the_tracks_best OK")


def test_the_library_routes_editing_through_the_injected_callback():
    """The dialog writes NOTHING: the app owns the editor and the file op, which is what keeps this
    one hermetic. The button appears only when that callback is wired."""
    store = sr.empty_store()
    seen = []

    def _edit(entry):
        seen.append(entry["fingerprint"])
        fresh = sr.empty_store()
        sr.put(fresh, entry["fingerprint"], _record(conditions="wet", tyre_laps=7))
        return fresh

    dlg = _library_dialog([_entry("GX010062")], store, edit_record=_edit,
                          reload_records=lambda: store)
    assert dlg.record_btn.isEnabled(), "a selected row can always be written up"
    dlg._edit_selected_record()
    assert seen == [library.fingerprint("GX010062")]
    # The table re-rendered from the store the callback returned.
    assert _cells(dlg, libdlg._COL_COND) == ["Wet"]
    assert _cells(dlg, libdlg._COL_TYRES) == ["7"]

    # Un-wired: no button at all, and the columns still render from the data handed in.
    bare = _library_dialog([_entry("GX010062")], store)
    assert getattr(bare, "record_btn", None) is None
    print("test_the_library_routes_editing_through_the_injected_callback OK")


def test_a_library_with_no_records_at_all_renders_cleanly():
    """The first-run state of this feature is EVERY row, so it has to be the calm one: two em-dash
    columns, an inviting line, no comparability claim, and nothing that reads as broken."""
    dlg = _library_dialog([_entry("GX010062"), _entry("GX010060", date="2026-06-01")], None)
    assert _cells(dlg, libdlg._COL_COND) == ["—", "—"]
    assert _cells(dlg, libdlg._COL_TYRES) == ["—", "—"]
    dlg.table.selectRow(0)
    assert dlg._record_line.text() == libdlg._NO_RECORD_LINE
    assert dlg._compare_line.text() == ""
    print("test_a_library_with_no_records_at_all_renders_cleanly OK")


def test_the_privacy_note_names_the_records_file_and_says_it_is_never_fetched():
    """The disclosure has to name every file the app keeps, and this feature added one. It also
    states the non-goal the whole design rests on: conditions are typed, never looked up."""
    note = libdlg.PRIVACY_NOTE
    assert "session_records.json" in note
    assert "never looked up online" in note
    assert "session_records.json.bak" in note
    print("test_the_privacy_note_names_the_records_file_and_says_it_is_never_fetched OK")


# ============================================================================ 6. the lap panel
def test_the_lap_panel_chip_says_the_conditions_and_hides_when_there_is_nothing_to_say():
    """The chip's empty state is its ABSENCE — a permanent "no session record" pill over every lap
    grid would nag on every load about a form the File menu already offers."""
    from types import SimpleNamespace

    from studio.central_view import CentralView
    from studio.widgets import chip

    view = SimpleNamespace(record_chip=chip(""))
    CentralView.set_session_record(view, _record())
    assert not view.record_chip.isHidden()
    assert view.record_chip.text() == (
        "Dry  ·  air 24°  ·  track 38°  ·  41% RH  ·  MG Yellow #3, 42 laps")
    assert "cold 10/10.5" in view.record_chip.toolTip(), "the rest of the record is in the hover"
    assert "loose on entry to 3" in view.record_chip.toolTip()
    assert "File ▸ Session record…" in view.record_chip.toolTip()

    CentralView.set_session_record(view, None)
    assert view.record_chip.isHidden()
    CentralView.set_session_record(view, sr.blank_record())
    assert view.record_chip.isHidden(), "an empty record is nothing to say"

    # A record with ONLY a kart still says something — the chip must not go blank on it.
    kart_only = sr.blank_record()
    kart_only.update({"chassis": "OTK 401R", "sprocket_front": 11, "sprocket_rear": 82})
    CentralView.set_session_record(view, kart_only)
    assert view.record_chip.text() == "OTK 401R  ·  11/82"
    print("test_the_lap_panel_chip_says_the_conditions_and_hides_when_there_is_nothing_to_say OK")


def test_the_chip_is_actually_mounted_in_the_real_lap_panel_header():
    """A chip nobody can see is not a surface. Measured on the REAL CentralView: the record chip is
    in the LAP panel's header status slot, beside the quality badge and above the lap times it
    qualifies — which is the "show it where the comparison happens" half that is not the Library."""
    from test_central_view_realqt import _real_central_view

    view = _real_central_view()[0]
    assert view.record_chip in view._table_header.status, (
        "the session-record chip is not in the lap panel header's status slot")
    assert view.record_chip.isHidden(), "no record on the synthetic session -> no chip"
    view.set_session_record(_record())
    assert not view.record_chip.isHidden()
    assert view._table_header.height() == 36, (
        "adding the chip moved the panel header off its declared height")
    print("test_the_chip_is_actually_mounted_in_the_real_lap_panel_header OK")


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()


if __name__ == "__main__":
    _run_all()
    print("\nall session-record tests passed")
