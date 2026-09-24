"""THE STORES' SHARED WRITE AND LOCK (RISK-4) — two writers at once must not cost anyone a row.

Until this, all seven JSON stores wrote ``<file>.tmp`` and ``os.replace``-d it into place, so two
writers shared ONE temp name: the second ``open(tmp, "w")`` truncated the first's half-written
file, one ``os.replace`` moved interleaved bytes into place, the other raised
``FileNotFoundError``, and the next ``load`` read the wreck as an EMPTY store that the next save
wrote back. The board review measured it with two processes each upserting 20 rows into a library
seeded with 50: 3 of 10 trials destroyed all 50 seeded rows, one with no ``.bak``. Marks and session
records are typed by the driver and cannot be re-derived from the footage.

``studio/_jsonstore.py`` is the fix every store now goes through: a unique temp per write
(fsync-ed, then ``os.replace``) and an advisory ``flock`` held across every load-modify-save. Four
halves here:

  * THE RACE — two real PROCESSES start each store's own public read-modify-write on the same
    signal and hammer it; every seeded row and every row either of them wrote must survive, with
    no error. This is the defect itself, driven through the entry points the app and the dev tools
    call.
  * THE FIXED NAME — a file already sitting at ``<store>.tmp`` (another writer's half-written temp)
    survives every store's save untouched. The deterministic form of the same defect: on the old
    code each of the seven saves opened, truncated and moved that very file.
  * UNREADABLE MEANS LOGGED AND KEPT — a store file that exists but cannot be read is named in the
    session log (once, not once per read) and its bytes land in ``.bak`` before the first save
    replaces them: all six app-support stores, the valid-JSON shapes ``load`` also reads as empty,
    and the timing-line sidecar, which used to overwrite an unreadable file with no copy at all.
  * THE HELPER — the write is byte-identical to the old one, keeps the old file mode, fsyncs, and
    leaves nothing behind on failure; the lock excludes another thread and another process, is
    re-entrant, is released on an exception, and degrades to an unlocked (still atomic) write when
    a lock cannot be taken.

Everything writes under a fresh ``TemporaryDirectory``; nothing resolves app-support. The first
three halves use only the stores' public API and import nothing new at module level, so this file
runs unchanged against the tree before the fix — which is how its failures there were watched.

Run: python tests/test_jsonstore.py
"""
import contextlib
import errno
import json
import logging
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio import focus, library, marks, prefs, session_record, sidecar, track_db  # noqa: E402

# One writer's rows in the race. 2 × 80 locked read-modify-writes per store, six stores and the
# sidecar: ~3 s on the dev Mac. The old code lost rows on every store at this size (the PR quotes it).
RACE_WRITES = 80
SEEDED = 25
RACE_TIMEOUT_S = 90.0

_FOCUS_ITEM = focus.item_from_dict({
    "cid": 1, "direction": 1, "enter_frac": 0.10, "exit_frac": 0.20, "median_s": 5.0,
    "iqr_s": 0.2, "n_laps": 8, "fingerprint": "GX0001", "date": "2026-09-19", "lap_total": 730.0,
    "verified": True})
_LINE = [[51.3760, -0.3610], [51.3761, -0.3611]]


def _lib_row(tag: str) -> dict:
    return {"fingerprint": tag, "stem": tag, "track": "Race", "date": "2026-09-24", "lap_count": 3,
            "best": 50.0, "theoretical": None, "verified": True, "degraded": False,
            "dropout": False, "paths": [f"/race/{tag}.MP4"]}


# The six app-support stores, each as: its file name, ONE read-modify-write adding a row keyed by
# `tag` through the store's own public entry point, and the set of tags the file holds.
STORES = {
    "library": ("library.json",
                lambda p, tag: library.upsert_and_save(_lib_row(tag), p),
                lambda p: {e["fingerprint"] for e in library.load(p)["entries"]}),
    "marks": ("marks.json",
              lambda p, tag: marks.put_and_save("GX0001", marks.new_mark("GX010001", 1.0, note=tag),
                                                p),
              lambda p: {m["note"] for m in marks.get(marks.load(p), "GX0001")}),
    "session_record": ("session_records.json",
                       lambda p, tag: session_record.put_and_save(tag, {"notes": tag}, p),
                       lambda p: set(session_record.load(p)["records"])),
    "prefs": ("prefs.json",
              lambda p, tag: prefs.set(tag, 1, p),
              lambda p: set(prefs.load(p)) - {"version"}),
    "track_db": ("tracks.json",
                 lambda p, tag: track_db.save_track(
                     track_db.make_entry(tag, (51.376, -0.361), _LINE, []), p),
                 lambda p: set(track_db.user_names(p))),
    "focus": ("focus.json",
              lambda p, tag: focus.save_for_track(tag, [_FOCUS_ITEM], p),
              lambda p: {e["track"] for e in focus.load(p)["lists"]}),
}
SIDECAR = "GX010001.pacer.json"


def _sidecar_write(path: str, tag: str) -> None:
    sidecar.save(path, tag, _LINE, [])


def _read_or_none(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


# ================================================================================ THE RACE
def _race_worker(who: str, root: str, n: str) -> None:
    """One of the two racing processes: for each store in turn, say ready, wait for the parent's go
    (so both start together), make `n` distinct writes, and report every exception by type."""
    for name in (*STORES, "sidecar"):
        print(f"ready {name}", flush=True)
        if sys.stdin.readline().strip() != "go":
            return
        errors: dict[str, int] = {}
        for i in range(int(n)):
            try:
                if name == "sidecar":
                    _sidecar_write(os.path.join(root, SIDECAR), f"{who}-{i}")
                else:
                    fn, write, _keys = STORES[name]
                    write(os.path.join(root, fn), f"{who}-{i}")
            except Exception as exc:  # noqa: BLE001 — every failure is the measurement
                errors[type(exc).__name__] = errors.get(type(exc).__name__, 0) + 1
        print("done " + json.dumps(errors), flush=True)


def _expect(proc, prefix: str, stderr_path: str) -> str:
    line = proc.stdout.readline()
    if not line.startswith(prefix):
        with open(stderr_path, encoding="utf-8", errors="replace") as f:
            tail = f.read()[-2000:]
        raise AssertionError(f"race worker sent {line!r}, expected {prefix!r} (it died, hung past "
                             f"{RACE_TIMEOUT_S:.0f} s, or printed): {tail}")
    return line[len(prefix):].strip()


def test_two_processes_writing_every_store_at_once_lose_nothing():
    """THE DEFECT, END TO END. Two processes run each store's own read-modify-write at the same
    moment, `RACE_WRITES` rows each, over a store seeded with `SEEDED` rows. Nothing may be lost:
    not the seeded history (the unique temp's job) and not either writer's rows (the lock's job),
    and no write may raise. The sidecar is a whole-file write, so for it the check is that neither
    writer failed and what is left on disk is a sidecar ``load`` accepts."""
    with tempfile.TemporaryDirectory() as root:
        for fn, write, _keys in STORES.values():
            for i in range(SEEDED):
                write(os.path.join(root, fn), f"S-{i}")
        # A worker's stderr goes to a FILE: an unread pipe fills at 64 KB and would stall it.
        errs = [os.path.join(root, f"worker-{w}.stderr") for w in "AB"]
        sinks = [open(err, "w") for err in errs]
        procs = [subprocess.Popen([sys.executable, os.path.abspath(__file__), "--race-worker", w,
                                   root, str(RACE_WRITES)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sink,
                                  text=True)
                 for w, sink in zip("AB", sinks, strict=True)]
        watchdog = threading.Timer(RACE_TIMEOUT_S, lambda: [p.kill() for p in procs])
        watchdog.start()
        t0, problems, table = time.monotonic(), [], []
        try:
            for name in (*STORES, "sidecar"):
                for p, err in zip(procs, errs, strict=True):
                    _expect(p, f"ready {name}", err)
                for p in procs:
                    p.stdin.write("go\n")
                    p.stdin.flush()
                raised: dict[str, int] = {}
                for p, err in zip(procs, errs, strict=True):
                    for kind, count in json.loads(_expect(p, "done ", err)).items():
                        raised[kind] = raised.get(kind, 0) + count
                if name == "sidecar":
                    try:
                        state = sidecar.load(os.path.join(root, SIDECAR))["track"]
                    except Exception as exc:  # noqa: BLE001
                        state = f"UNREADABLE {exc!r}"[:80]
                        problems.append(f"sidecar: the file left on disk is unreadable ({exc!r})")
                    table.append(f"  sidecar          final track {state}  errors {raised}")
                else:
                    fn, _write, keys = STORES[name]
                    try:
                        have = keys(os.path.join(root, fn))
                    except Exception as exc:  # noqa: BLE001
                        have = set()
                        problems.append(f"{name}: could not read the store back ({exc!r})")
                    seeded = sum(f"S-{i}" in have for i in range(SEEDED))
                    written = sum(f"{w}-{i}" in have for w in "AB" for i in range(RACE_WRITES))
                    table.append(f"  {name:16s} seeded {seeded}/{SEEDED}  written "
                                 f"{written}/{2 * RACE_WRITES}  errors {raised}")
                    if seeded < SEEDED:
                        problems.append(f"{name}: {SEEDED - seeded} of {SEEDED} SEEDED rows destroyed")
                    if written < 2 * RACE_WRITES:
                        problems.append(f"{name}: {2 * RACE_WRITES - written} of "
                                        f"{2 * RACE_WRITES} racing rows lost")
                if raised:
                    problems.append(f"{name}: writes raised {raised}")
        finally:
            watchdog.cancel()
            for p in procs:
                with contextlib.suppress(Exception):
                    p.stdin.close()
                with contextlib.suppress(Exception):
                    p.wait(timeout=10)
                if p.poll() is None:
                    p.kill()
                    p.wait()
                p.stdout.close()
            for sink in sinks:
                sink.close()
        elapsed = time.monotonic() - t0
    report = "\n".join(table)
    assert not problems, ("two processes writing the stores at once lost data:\n  "
                          + "\n  ".join(problems) + f"\n{report}")
    print(f"test_two_processes_writing_every_store_at_once_lose_nothing OK — {elapsed:.1f} s\n"
          f"{report}")


# ================================================================================ THE FIXED NAME
def test_no_store_writes_through_the_old_shared_temp_name():
    """A file already at ``<store>.tmp`` — exactly what a second writer's half-written temp looked
    like — must survive every store's save byte for byte, and the save must leave no temp of its
    own behind. On the old code every one of the seven opened it, truncated it and renamed it."""
    decoy = "DECOY: another writer's half-written temp\n"
    clobbered = []
    with tempfile.TemporaryDirectory() as root:
        targets = {name: os.path.join(root, fn) for name, (fn, _w, _k) in STORES.items()}
        targets["sidecar"] = os.path.join(root, SIDECAR)
        for name, path in targets.items():
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                f.write(decoy)
            if name == "sidecar":
                _sidecar_write(path, "T-0")
            else:
                STORES[name][1](path, "T-0")
            if _read_or_none(path + ".tmp") != decoy.encode():
                clobbered.append(name)
        litter = sorted(n for n in os.listdir(root)
                        if n.endswith(".tmp") and not any(n == os.path.basename(p) + ".tmp"
                                                          for p in targets.values()))
    assert not clobbered, (f"these stores still write through the fixed <file>.tmp name another "
                           f"writer may be mid-way through: {clobbered}")
    assert not litter, f"a save left its own temp file behind: {litter}"
    print(f"test_no_store_writes_through_the_old_shared_temp_name OK — {len(targets)} stores")


# ================================================================================ UNREADABLE
class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@contextlib.contextmanager
def _captured():
    """Every WARNING any studio module logs while the block runs (the root logger is where the
    session log listens, so a record that reaches this handler reaches the log)."""
    cap, root = _Capture(), logging.getLogger()
    root.addHandler(cap)
    try:
        yield cap.messages
    finally:
        root.removeHandler(cap)


def test_an_unreadable_store_is_logged_once_and_its_bytes_kept_as_bak():
    """The review's other half of RISK-4: a corrupt index used to show as an empty Library with no
    line anywhere saying why. Each app-support store's file is replaced by bytes that do not parse;
    reading it (three times, as the window does) must name the file in the log exactly ONCE, and
    the first write must first copy those exact bytes to ``.bak``."""
    garbage = b'{"version": 1, "entries": [{"fingerprint": "GX00'   # a writer cut off mid-row
    problems = []
    for name, (fn, write, keys) in STORES.items():
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, fn)
            with open(path, "wb") as f:
                f.write(garbage)
            with _captured() as logged:
                for _ in range(3):
                    keys(path)
            named = [m for m in logged if path in m]
            if len(named) != 1:
                problems.append(f"{name}: reading an unreadable file 3 times logged {len(named)} "
                                f"line(s) naming it, not 1: {logged}")
            write(path, "after")
            kept = _read_or_none(path + ".bak")
            if kept != garbage:
                problems.append(f"{name}: the unreadable bytes were not kept as .bak "
                                f"({'no .bak' if kept is None else 'a different .bak'})")
            if "after" not in keys(path):
                problems.append(f"{name}: the write after the corruption did not land")
    assert not problems, "\n".join(problems)
    print(f"test_an_unreadable_store_is_logged_once_and_its_bytes_kept_as_bak OK — "
          f"{len(STORES)} stores")


def test_a_valid_json_file_load_reads_as_empty_is_kept_as_bak_too():
    """``load`` also reads two VALID-JSON shapes as empty — a version that is not a number, and a
    row container of the wrong type — and library and focus overwrote both with no copy (measured
    on the tree before this fix: ``.bak`` absent in all four cases, while marks and session records,
    which enumerate ``load``'s own fall-backs, kept theirs)."""
    row = _lib_row("GX0001")
    cases = [
        ("library", {"version": "4", "entries": [row]}),
        ("library", {"version": library.VERSION, "entries": {"GX0001": row}}),
        ("focus", {"version": "1", "lists": [{"track": "T", "items": []}]}),
        ("focus", {"version": focus.VERSION, "lists": {"T": []}}),
    ]
    problems = []
    for name, payload in cases:
        fn, write, _keys = STORES[name]
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, fn)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            with _captured() as logged:
                write(path, "after")
            if not os.path.exists(path + ".bak"):
                problems.append(f"{name} {payload!r:.60}: overwritten with no .bak")
            elif json.load(open(path + ".bak", encoding="utf-8")) != payload:
                problems.append(f"{name} {payload!r:.60}: the .bak is not the original")
            if not any(path in m for m in logged):
                problems.append(f"{name} {payload!r:.60}: read as empty without a log line")
    assert not problems, "\n".join(problems)
    print(f"test_a_valid_json_file_load_reads_as_empty_is_kept_as_bak_too OK — {len(cases)} cases")


def test_an_unreadable_sidecar_is_kept_as_bak_before_a_drag_replaces_it():
    """The app already SAYS a damaged sidecar could not be read (``SidecarUnreadable``); the next
    drag of the start line then overwrote it with no copy — the one store without the rule. The
    rejected bytes (unparseable, or a version this build does not read) must land in
    ``<stem>.pacer.json.bak`` first, and a healthy sidecar being rewritten must not take one."""
    for bad in (b'{"version": 1, "start": [[51.37', b'{"version": 2, "start": "elsewhere"}'):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, SIDECAR)
            with open(path, "wb") as f:
                f.write(bad)
            _sidecar_write(path, "after")
            kept = _read_or_none(path + ".bak")
            assert kept is not None, (
                f"an unreadable sidecar ({bad[:24]!r}…) was overwritten with no .bak")
            assert kept == bad, "the sidecar .bak does not hold the rejected bytes"
            assert sidecar.load(path)["track"] == "after"
            assert sidecar.backup_path(path) == path + ".bak"
    with tempfile.TemporaryDirectory() as root:
        path = os.path.join(root, SIDECAR)
        _sidecar_write(path, "one")
        _sidecar_write(path, "two")
        assert not os.path.exists(path + ".bak"), "a healthy sidecar took a .bak"
    print("test_an_unreadable_sidecar_is_kept_as_bak_before_a_drag_replaces_it OK")


# ================================================================================ THE HELPER
def test_write_json_is_byte_identical_keeps_the_mode_and_fsyncs():
    """Same bytes every store wrote before (``indent=2`` + newline), the same permissions a plain
    ``open(path, "w")`` gives (mkstemp's own 0o600 would have quietly changed every user's store),
    and an fsync of the temp before it is renamed into place."""
    from studio import _jsonstore
    obj = {"version": 1, "entries": [{"a": 1.5, "b": "é", "c": None}]}
    synced = []
    real_fsync = os.fsync
    with tempfile.TemporaryDirectory() as root:
        path, reference = os.path.join(root, "store.json"), os.path.join(root, "reference.json")
        with open(reference, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)
            f.write("\n")
        os.fsync = lambda fd: (synced.append(fd), real_fsync(fd))[1]
        try:
            _jsonstore.write_json(path, obj)
        finally:
            os.fsync = real_fsync
        assert open(path, "rb").read() == open(reference, "rb").read(), "the bytes changed"
        mode, ref_mode = (stat.S_IMODE(os.stat(p).st_mode) for p in (path, reference))
        assert mode == ref_mode, f"mode {oct(mode)}, but a plain open() gives {oct(ref_mode)}"
        assert synced, "write_json did not fsync before the rename"
        assert sorted(os.listdir(root)) == ["reference.json", "store.json"], os.listdir(root)
    print("test_write_json_is_byte_identical_keeps_the_mode_and_fsyncs OK")


def test_a_failed_write_leaves_the_old_file_and_no_temp():
    """A write that fails part-way — the object will not serialize, or the rename itself fails —
    must leave the previous file exactly as it was and nothing beside it."""
    from studio import _jsonstore
    with tempfile.TemporaryDirectory() as root:
        path = os.path.join(root, "store.json")
        _jsonstore.write_json(path, {"kept": True})
        before = open(path, "rb").read()
        try:
            _jsonstore.write_json(path, {"bad": object()})
        except TypeError:
            pass
        else:
            raise AssertionError("an unserializable object was written")
        real_replace = os.replace

        def refuse(src, dst):
            raise OSError(errno.EXDEV, "simulated rename failure")
        os.replace = refuse
        try:
            _jsonstore.write_json(path, {"new": True})
        except OSError:
            pass
        finally:
            os.replace = real_replace
        assert open(path, "rb").read() == before, "a failed write damaged the previous file"
        assert os.listdir(root) == ["store.json"], f"a failed write left {os.listdir(root)}"
    print("test_a_failed_write_leaves_the_old_file_and_no_temp OK")


def test_the_lock_is_reentrant_excludes_another_thread_and_is_released_on_error():
    """``save`` takes the lock inside every ``*_and_save`` that already holds it, so it must be
    re-entrant in one thread; a second THREAD must still wait (flock belongs to an open file
    description, and the helper opens one per acquisition); and an exception must not leave it
    held. The waiting thread is observed, not timed: it must not have entered while we hold it."""
    from studio import _jsonstore
    with tempfile.TemporaryDirectory() as root:
        path = os.path.join(root, "store.json")
        entered = threading.Event()

        def other():
            with _jsonstore.locked(path):
                entered.set()

        with _jsonstore.locked(path):
            with _jsonstore.locked(path):                  # re-entrant: no self-deadlock
                t = threading.Thread(target=other, daemon=True)
                t.start()
                assert not entered.wait(0.3), "another thread entered while the lock was held"
        assert entered.wait(10), "the other thread never got the lock after it was released"
        t.join(10)
        try:
            with _jsonstore.locked(path):
                raise KeyError("boom")
        except KeyError:
            pass
        entered.clear()
        t = threading.Thread(target=other, daemon=True)
        t.start()
        assert entered.wait(10), "the lock stayed held after an exception inside the block"
        t.join(10)
        assert os.path.basename(_jsonstore.lock_path(path)) == ".store.json.lock"
    print("test_the_lock_is_reentrant_excludes_another_thread_and_is_released_on_error OK")


def test_the_lock_holds_off_another_process_until_released():
    """Across PROCESSES, deterministically: while this process holds the library's lock, a child
    running ``library.upsert_and_save`` must still be waiting (its row not on disk); once released,
    it must finish and its row must be there alongside ours."""
    from studio import _jsonstore
    with tempfile.TemporaryDirectory() as root:
        path = os.path.join(root, "library.json")
        library.upsert_and_save(_lib_row("PARENT"), path)
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from studio import library; "
                "library.upsert_and_save({'fingerprint': 'CHILD', 'stem': 'CHILD', 'track': None, "
                "'date': None, 'lap_count': 1, 'best': None, 'theoretical': None, "
                "'verified': True, 'degraded': False, 'dropout': False, 'paths': []}, sys.argv[2])")
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with _jsonstore.locked(path):
            child = subprocess.Popen([sys.executable, "-c", code, repo, path])
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and child.poll() is None:
                time.sleep(0.05)
            assert child.poll() is None, "the child finished its write while the lock was held"
            assert STORES["library"][2](path) == {"PARENT"}, "the child wrote under our lock"
        assert child.wait(timeout=30) == 0, "the child failed after the lock was released"
        assert STORES["library"][2](path) == {"PARENT", "CHILD"}
    print("test_the_lock_holds_off_another_process_until_released OK")


def test_a_lock_that_cannot_be_taken_still_lets_the_write_happen():
    """Locking is protection, never a new way to fail: on a filesystem without flock the store is
    written anyway (atomically) and the log says it went unlocked."""
    import fcntl
    real_flock = fcntl.flock

    def unsupported(fd, op):
        raise OSError(errno.ENOTSUP, "flock not supported here")
    with tempfile.TemporaryDirectory() as root:
        path = os.path.join(root, "prefs.json")
        fcntl.flock = unsupported
        try:
            with _captured() as logged:
                prefs.set("k", 1, path)
        finally:
            fcntl.flock = real_flock
        assert prefs.get("k", None, path) == 1, "the write did not happen without the lock"
        assert any("without it" in m for m in logged), f"no log line said so: {logged}"
    print("test_a_lock_that_cannot_be_taken_still_lets_the_write_happen OK")


TESTS = [
    test_two_processes_writing_every_store_at_once_lose_nothing,
    test_no_store_writes_through_the_old_shared_temp_name,
    test_an_unreadable_store_is_logged_once_and_its_bytes_kept_as_bak,
    test_a_valid_json_file_load_reads_as_empty_is_kept_as_bak_too,
    test_an_unreadable_sidecar_is_kept_as_bak_before_a_drag_replaces_it,
    test_write_json_is_byte_identical_keeps_the_mode_and_fsyncs,
    test_a_failed_write_leaves_the_old_file_and_no_temp,
    test_the_lock_is_reentrant_excludes_another_thread_and_is_released_on_error,
    test_the_lock_holds_off_another_process_until_released,
    test_a_lock_that_cannot_be_taken_still_lets_the_write_happen,
]

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--race-worker":
        _race_worker(*sys.argv[2:5])
        sys.exit(0)
    failed = 0
    for test in TESTS:
        try:
            test()
        except Exception as exc:  # noqa: BLE001 — report every test, then fail the run
            failed += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}", flush=True)
    print(f"{len(TESTS) - failed}/{len(TESTS)} passed")
    sys.exit(1 if failed else 0)
