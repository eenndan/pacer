"""User-preferences store (studio.prefs): corruption resilience + the per-key round-trips.

The defect this file was written for: ``prefs.load`` returns ``{}`` on ANY corruption and ``set`` is
load-modify-save — so ONE bad byte followed by ANY preference write (a unit toggle, a splitter drag,
opening the Library dialog at a new size) silently PERSISTED the wipe of all ten stored choices,
with no copy of the original anywhere. ``library.py`` had solved the identical failure mode (a
``.bak`` before the first overwrite, a read ``version`` with a migration hook) and had six tests for
it; this store had ``VERSION = 1`` written and read by nothing, and no test file at all.

Covered here:
  A. corruption — the corrupt bytes are preserved to ``prefs.json.bak`` BEFORE the write that
     resets them, subsequent writes still work (the file heals) and don't churn/clobber the
     sidecar, a healthy write never creates one, and every per-key accessor still falls back to
     its documented default while the file is broken;
  B. versioning — an unstamped/older file MIGRATES forward with every key kept (it is not
     corruption), and a NEWER file is read best-effort, keeps its unknown keys through the
     load-modify-save round-trip, and is backed up before this build stamps it back down;
  C. the accessors with no direct round-trip test anywhere — ``excluded_visible`` and
     ``map_key_collapsed`` (only view-level plumbing tests existed), plus the guarded-vs-propagating
     split every setter documents. (SPEED_UNIT / LAP_PANEL_TAB / GRID_SIZES live in test_units,
     COLORBLIND_PALETTE in test_accessible_cues, LAST_DIR in test_import_ergonomics, LIBRARY_SIZE in
     test_library — this file does not duplicate those.)

Pure Python: studio.prefs imports studio.units only — no Qt, no pacer, no telemetry file.
Run: python tests/test_prefs.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio import prefs, units  # noqa: E402

# Safety net FIRST, before any test can call an accessor: divert the app-support seam to a temp dir
# so a missing `path=` argument in this file can never read — or rewrite — the developer's own
# prefs.json (the idiom test_map_key / test_library / test_export_gates use).
_SEAM = tempfile.TemporaryDirectory(prefix="pacer-test-prefs-")
prefs._app_support_dir = (lambda d=_SEAM.name: d)


# --------------------------------------------------------------------------------- fixtures
def _write_every_preference(path):
    """Write all ten preferences through the PUBLIC setters and return what each accessor should
    read back. Ten choices in one file is the point: the wipe this module now survives cost the user
    every one of them, not the single value they were changing."""
    prefs.set_speed_unit(units.MPH, path)
    prefs.set_colorblind_palette(True, path)
    prefs.set_last_dir(os.path.dirname(path), path)
    prefs.set_excluded_visible(False, path)
    prefs.set_lap_panel_tab(2, path)
    prefs.set_grid_sizes([[500, 900], [400, 450], [300, 550]], path)
    prefs.set_map_key_collapsed(True, path)
    prefs.set_library_size(900, 780, path)
    return {
        "speed_unit": units.MPH,
        "colorblind_palette": True,
        "last_dir": os.path.dirname(path),
        "excluded_visible": False,
        "lap_panel_tab": 2,
        "grid_sizes": [[500, 900], [400, 450], [300, 550]],
        "map_key_collapsed": True,
        "library_size": (900, 780),
    }


def _read_every_preference(path):
    """Every accessor's current answer, keyed like ``_write_every_preference``."""
    return {
        "speed_unit": prefs.speed_unit(path),
        "colorblind_palette": prefs.colorblind_palette(path),
        "last_dir": prefs.last_dir(path),
        "excluded_visible": prefs.excluded_visible(path),
        "lap_panel_tab": prefs.lap_panel_tab(path),
        "grid_sizes": prefs.grid_sizes(path),
        "map_key_collapsed": prefs.map_key_collapsed(path),
        "library_size": prefs.library_size(path),
    }


# The defaults each accessor documents — what a user sees when the file is unreadable.
_DEFAULTS = {
    "speed_unit": units.KMH,
    "colorblind_palette": False,
    "last_dir": "",
    "excluded_visible": True,
    "lap_panel_tab": 0,
    "grid_sizes": None,
    "map_key_collapsed": False,
    "library_size": None,
}


# ============================================================ A. corruption is never silent loss
def test_corrupt_file_is_backed_up_before_the_first_write_resets_it():
    """THE DEFECT. A file with one byte lost reads as ``{}``, so the very next ``set`` — a unit
    toggle — rewrites the file from that empty dict and all ten choices are gone. The reset itself
    is unavoidable (nothing can un-corrupt the bytes); losing the ORIGINAL was not. ``save`` now
    copies the unreadable bytes to ``prefs.json.bak`` first, so the user's stored choices survive
    the write that resets them and can be read back out of the sidecar by hand."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "prefs.json")
        expected = _write_every_preference(p)
        assert _read_every_preference(p) == expected          # ten choices, healthy on disk

        with open(p, "rb") as f:
            healthy = f.read()
        corrupt = healthy.rstrip()[:-1]                        # one bad byte: the closing brace
        with open(p, "wb") as f:
            f.write(corrupt)
        assert prefs.load(p) == {}                             # unparseable -> the safe empty dict

        prefs.set_speed_unit(units.KMH, p)                     # the write that used to be the wipe

        bak = prefs.backup_path(p)
        assert os.path.exists(bak), "the corrupt prefs were overwritten with no copy kept"
        with open(bak, "rb") as f:
            assert f.read() == corrupt, "the .bak must hold the ORIGINAL bytes verbatim"
        # And those bytes really are the user's data: every stored choice is still in the sidecar,
        # so a hand-repair recovers the file the wipe would otherwise have destroyed.
        text = corrupt.decode("utf-8")
        for token in ('"speed_unit": "mph"', '"colorblind_palette": true',
                      '"excluded_visible": false', '"lap_panel_tab": 2',
                      '"map_key_collapsed": true', '"library_size"', '"grid_sizes"'):
            assert token in text, token
        # The live file healed: it is valid JSON carrying the value just written + the version stamp.
        with open(p, encoding="utf-8") as f:
            healed = json.load(f)
        assert healed["speed_unit"] == units.KMH
        assert healed["version"] == prefs.VERSION
        print("test_corrupt_file_is_backed_up_before_the_first_write_resets_it OK")


def test_writes_after_the_corruption_keep_working_and_leave_the_backup_alone():
    """(b) of the round-trip: the store is USABLE again after corruption — later writes accumulate
    normally — and they do not churn the sidecar. The .bak slot must still hold the ORIGINAL corrupt
    bytes after five more preference changes, not the last healthy file (a backup that the heal
    overwrites is no backup at all)."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "prefs.json")
        corrupt = b'{"speed_unit": "mph", "lap_panel_tab": 2'
        with open(p, "wb") as f:
            f.write(corrupt)

        expected = _write_every_preference(p)                  # the heal + five more writes
        assert _read_every_preference(p) == expected, _read_every_preference(p)

        bak = prefs.backup_path(p)
        with open(bak, "rb") as f:
            assert f.read() == corrupt, "a later healthy write clobbered the corrupt original"
        print("test_writes_after_the_corruption_keep_working_and_leave_the_backup_alone OK")


def test_healthy_saves_never_create_a_backup():
    """A normal write over a healthy file must NOT leave a .bak — the sidecar is for the
    un-round-trippable cases only (mirrors library's no-backup-churn rule). Otherwise every unit
    toggle would drop a stale copy of the prefs beside them."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "prefs.json")
        prefs.set_speed_unit(units.MPH, p)                     # first write (no prior file)
        prefs.set_lap_panel_tab(3, p)                          # overwrite a healthy file
        prefs.set_excluded_visible(False, p)
        assert not os.path.exists(prefs.backup_path(p))
        print("test_healthy_saves_never_create_a_backup OK")


def test_every_accessor_falls_back_while_the_file_is_corrupt():
    """(c) the per-key fallbacks still behave: with the file in every shape ``load`` calls
    corruption, each accessor returns the default it documents — a corrupt file is never fatal and
    never opens a blank tab / an off-screen dialog / a stale folder."""
    bad_bodies = [
        "",                                # empty file (an interrupted write)
        "{ not json",                      # not JSON at all
        '{"speed_unit": "mph"',            # truncated object
        "[]",                              # JSON, but not an object
        '"speed_unit"',                    # a bare string
        "null",                            # JSON null
        "17",                              # a number
    ]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "prefs.json")
        for body in bad_bodies:
            with open(p, "w", encoding="utf-8") as f:
                f.write(body)
            assert prefs.load(p) == {}, body
            assert _read_every_preference(p) == _DEFAULTS, body
        # A missing file is the same story (a first-ever run), and reads no directory into being.
        missing = os.path.join(d, "nope", "prefs.json")
        assert prefs.load(missing) == {}
        assert _read_every_preference(missing) == _DEFAULTS
        assert not os.path.exists(os.path.dirname(missing))
        print("test_every_accessor_falls_back_while_the_file_is_corrupt OK")


# ================================================================ B. the version field is honoured
def test_unstamped_and_older_files_migrate_forward_keeping_every_preference():
    """A version MISMATCH is not corruption. An older — or unstamped, which only a hand-edit
    produces — file is migrated forward with every key preserved and re-stamped on the next write,
    NOT discarded. And because it round-trips cleanly, it takes no backup (no churn)."""
    stored = {"speed_unit": units.MPH, "colorblind_palette": True, "lap_panel_tab": 3,
              "excluded_visible": False, "map_key_collapsed": True, "library_size": [800, 700]}
    for stamp in ({}, {"version": 0}, {"version": True}):   # unstamped / older / a bogus bool stamp
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "prefs.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({**stored, **stamp}, f)
            # Every stored choice is honoured, not defaulted.
            assert prefs.speed_unit(p) == units.MPH, stamp
            assert prefs.colorblind_palette(p) is True, stamp
            assert prefs.lap_panel_tab(p) == 3, stamp
            assert prefs.excluded_visible(p) is False, stamp
            assert prefs.map_key_collapsed(p) is True, stamp
            assert prefs.library_size(p) == (800, 700), stamp
            # The next write re-stamps the version and keeps every migrated key.
            prefs.set_speed_unit(units.KMH, p)
            with open(p, encoding="utf-8") as f:
                out = json.load(f)
            assert out["version"] == prefs.VERSION, stamp
            assert out["lap_panel_tab"] == 3 and out["library_size"] == [800, 700], stamp
            assert not os.path.exists(prefs.backup_path(p)), stamp
    print("test_unstamped_and_older_files_migrate_forward_keeping_every_preference OK")


def test_newer_version_is_read_best_effort_and_backed_up_before_the_downgrade():
    """A NEWER file (the user ran a later build, then this one) is read BEST-EFFORT rather than
    wiped: a flat key/value store round-trips keys this build has never heard of. But the write
    stamps the version back DOWN, which a future schema could read as a lie, so the original bytes
    go to the .bak first — the same rule library applies to a downgrade."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "prefs.json")
        newer = {"version": 99, "speed_unit": units.MPH, "lap_panel_tab": 1,
                 "future_choice": {"nested": [1, 2, 3]}}
        original = json.dumps(newer, indent=2).encode("utf-8")
        with open(p, "wb") as f:
            f.write(original)

        assert prefs.speed_unit(p) == units.MPH        # best-effort: known keys still work
        assert prefs.lap_panel_tab(p) == 1

        prefs.set_excluded_visible(False, p)           # the write that stamps it down to v1

        bak = prefs.backup_path(p)
        assert os.path.exists(bak), "a newer prefs file was overwritten with no copy kept"
        with open(bak, "rb") as f:
            assert f.read() == original
        with open(p, encoding="utf-8") as f:
            out = json.load(f)
        assert out["version"] == prefs.VERSION
        assert out["speed_unit"] == units.MPH          # the known keys survived the round-trip
        assert out["future_choice"] == {"nested": [1, 2, 3]}, \
            "load-modify-save dropped a key from a newer schema"
        print("test_newer_version_is_read_best_effort_and_backed_up_before_the_downgrade OK")


def test_backup_sidecar_sits_next_to_the_prefs_file():
    """The .bak lives in the app-support dir beside prefs.json (the folder the Library dialog's
    "Reveal in Finder" opens), so "recover it by hand" is a real instruction. Resolves through the
    same ``_app_support_dir`` seam every other path here does."""
    assert prefs.backup_path() == prefs.prefs_path() + ".bak"
    assert os.path.dirname(prefs.backup_path()) == os.path.dirname(prefs.prefs_path())
    assert prefs.backup_path("/tmp/x/prefs.json") == "/tmp/x/prefs.json.bak"
    print("test_backup_sidecar_sits_next_to_the_prefs_file OK")


# ============================================== C. the accessors that had no round-trip test at all
def test_excluded_visible_roundtrip_and_coercion():
    """The ⊘ excluded-laps strip toggle: only the view-level plumbing (CentralView →
    LapTable.set_excluded_visible) was tested — the persisted half had nothing. Default True
    (shown), round-trips, and coerces a garbage stored value to a bool rather than crashing the View
    menu (the COLORBLIND_PALETTE / LAST_DIR pattern)."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "prefs.json")
        assert prefs.excluded_visible(p) is True               # missing file -> default: shown
        prefs.set_excluded_visible(False, p)
        assert prefs.excluded_visible(p) is False
        prefs.set_excluded_visible(True, p)
        assert prefs.excluded_visible(p) is True
        prefs.set(prefs.EXCLUDED_VISIBLE, "no thanks", p)      # garbage -> coerced, not fatal
        assert prefs.excluded_visible(p) is True
        prefs.set(prefs.EXCLUDED_VISIBLE, 0, p)
        assert prefs.excluded_visible(p) is False
        print("test_excluded_visible_roundtrip_and_coercion OK")


def test_map_key_collapsed_roundtrip_and_guarded_setter():
    """The map key's collapse: driven through the widget in test_map_key, never directly. Default
    False (expanded), round-trips, coerces garbage — and its setter is one of the three that
    SWALLOW an unwritable file, because remembering the state of a decorative plate must never
    disrupt the map."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "prefs.json")
        assert prefs.map_key_collapsed(p) is False             # missing file -> default: expanded
        prefs.set_map_key_collapsed(True, p)
        assert prefs.map_key_collapsed(p) is True
        prefs.set_map_key_collapsed(False, p)
        assert prefs.map_key_collapsed(p) is False
        prefs.set(prefs.MAP_KEY_COLLAPSED, "yes-please", p)
        assert prefs.map_key_collapsed(p) is True
        print("test_map_key_collapsed_roundtrip_and_guarded_setter OK")


def test_unwritable_store_is_swallowed_by_the_guarded_setters_only():
    """The documented split, pinned: the three cosmetic setters (last dir / map key / library size)
    swallow an unwritable prefs file — a preference that fails to save must never take the action
    with it — while ``set`` itself propagates, so a caller that must know still can."""
    with tempfile.TemporaryDirectory() as d:
        blocker = os.path.join(d, "not-a-dir")
        with open(blocker, "w", encoding="utf-8") as f:
            f.write("i am a file")
        p = os.path.join(blocker, "prefs.json")                # its parent is a FILE -> OSError

        prefs.set_last_dir(d, p)                               # all three: no raise
        prefs.set_map_key_collapsed(True, p)
        prefs.set_library_size(900, 780, p)
        assert not os.path.exists(p)

        raised = False
        try:
            prefs.set(prefs.SPEED_UNIT, units.MPH, p)
        except OSError:
            raised = True
        assert raised, "prefs.set must propagate a write failure (its callers guard it)"
        print("test_unwritable_store_is_swallowed_by_the_guarded_setters_only OK")


def test_set_keeps_its_sibling_preferences():
    """Load-modify-save on a HEALTHY file is additive — the property the corruption path broke.
    Ten writes leave ten values, plus the version stamp, and nothing else."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "prefs.json")
        expected = _write_every_preference(p)
        assert _read_every_preference(p) == expected
        with open(p, encoding="utf-8") as f:
            stored = json.load(f)
        assert set(stored) == {
            "version", prefs.SPEED_UNIT, prefs.COLORBLIND_PALETTE, prefs.LAST_DIR,
            prefs.EXCLUDED_VISIBLE, prefs.LAP_PANEL_TAB, prefs.GRID_SIZES,
            prefs.MAP_KEY_COLLAPSED, prefs.LIBRARY_SIZE}, sorted(stored)
        print("test_set_keeps_its_sibling_preferences OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} PREFS TESTS PASSED")
