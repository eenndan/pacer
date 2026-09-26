"""How every studio JSON store reads and writes its file, and the lock its read-modify-writes hold.

RISK-4 (board review 2026-09-23). All seven stores wrote ``<file>.tmp`` and ``os.replace``-d it
into place, so two writers — two app processes, or the app and a dev tool — shared ONE temp name.
The second ``open(tmp, "w")`` truncated the first's half-written file, one ``os.replace`` moved the
interleaved bytes into place and the other raised ``FileNotFoundError``; the next ``load`` saw
unparseable JSON, returned an EMPTY store without a word, and the next save wrote that emptiness
back. Two processes each upserting 20 rows into a library seeded with 50: 3 of 10 trials destroyed
all 50 seeded rows, one of them leaving no ``.bak``. Two pieces close it, and every store uses both:

  * ``write_json`` — a UNIQUE temp in the destination's own directory (``O_CREAT | O_EXCL``, which
    is ``mkstemp``'s recipe), flushed and ``fsync``-ed, then ``os.replace``-d; removed on any
    failure. Two writers never share bytes, and a reader only ever sees a whole file. The temp is
    created with the mode ``open(path, "w")`` always gave (0o666 less the umask) rather than
    mkstemp's 0o600, so a user's store keeps the permissions it has today.
  * ``locked`` — an advisory ``flock`` on a sibling ``.<file>.lock``, held across a whole
    load-modify-save. The unique temp stops the WIPE; only the lock stops the LOST UPDATE, where two
    writers each load, each add their own row, and the second save drops the first's. With it the
    same probe kept 90 of 90 rows in 10 of 10 trials. It is re-entrant within a thread (a store's
    ``*_and_save`` holds it and calls ``save``, which takes it too), and a separate descriptor per
    acquisition makes it exclude other THREADS as well as other processes.

``read_object`` is the one reader (the ``_is_loadable_dict`` six stores each carried a copy of),
and it says so in the session log when a file that EXISTS cannot be read, once per state of that
file: an empty Library used to be the only symptom of a corrupt index. Each store still decides for
itself what to do next — its schema, migrations and ``.bak`` rules stay in its own module.

Deliberately NOT a single-instance guard, which would stop a second app window but not a dev tool
or harness writing the same store; and not ``F_FULLFSYNC``, whose full barrier costs tens of ms a
write for a power-loss window a laptop's battery already covers. Stdlib only, no Qt, no pacer.
"""
from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import logging
import os
import secrets
import threading

_log = logging.getLogger(__name__)

# Lock files held by THIS thread (absolute paths). flock locks belong to an open file description,
# so a thread that re-opened a lock it already holds would wait on itself forever.
_held = threading.local()

# (path, size, mtime) of every unreadable file already reported, so a corrupt prefs.json read ten
# times while the window builds is one log line, not ten.
_reported: set[tuple] = set()


def lock_path(path: str) -> str:
    """The lock file guarding the store at `path`: a hidden sibling, ``.<file>.lock``. It is never
    deleted — removing a lock file while another process waits on it would hand the two of them
    different inodes, and so no lock at all."""
    head, tail = os.path.split(os.path.abspath(path))
    return os.path.join(head, f".{tail}.lock")


def _acquire(lock: str) -> int | None:
    """Open and exclusively ``flock`` `lock`; the descriptor, or None when locking is impossible
    here (an unwritable directory, a filesystem without flock). A store write must never fail for
    want of a lock: without one the write is still atomic, and only a concurrent update can be
    lost — so that is logged and the caller proceeds."""
    try:
        os.makedirs(os.path.dirname(lock), exist_ok=True)
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o666)
    except OSError as exc:
        _log.warning("could not create the store lock %s (%r); writing without it", lock, exc)
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
    except OSError as exc:
        os.close(fd)
        _log.warning("could not lock %s (%r); writing without it", lock, exc)
        return None
    return fd


@contextlib.contextmanager
def locked(path: str):
    """Hold the store at `path` exclusively for the ``with`` block: every load-modify-save and every
    ``save`` runs inside one, so no other writer — thread or process — can land between the load
    and the save. Re-entrant within a thread. Blocks while another writer holds it, which is for the
    few milliseconds one read and one write of a small JSON file take."""
    lock = lock_path(path)
    held = _held.__dict__.setdefault("locks", set())
    if lock in held:
        yield
        return
    fd = _acquire(lock)
    held.add(lock)
    try:
        yield
    finally:
        held.discard(lock)
        if fd is not None:
            os.close(fd)             # closing the descriptor releases the flock


def _open_unique(directory: str, name: str) -> tuple[int, str]:
    """``mkstemp``'s O_EXCL loop, with the permissions a plain ``open(path, "w")`` gets."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    for _ in range(100):
        tmp = os.path.join(directory, f".{name}.{secrets.token_hex(6)}.tmp")
        try:
            return os.open(tmp, flags, 0o666), tmp
        except FileExistsError:
            continue
    raise FileExistsError(errno.EEXIST, "no free temporary file name", directory)


def write_json(path: str, obj) -> os.stat_result:
    """Replace `path` with `obj` as JSON — ``indent=2`` and a trailing newline, byte for byte what
    every store wrote before — atomically: a unique temp beside it, ``fsync``-ed, then
    ``os.replace``. On any failure the temp is removed and `path` is left exactly as it was. The
    directory must exist. Raises OSError (and whatever ``json.dump`` raises) to the caller.

    Returns the ``fstat`` of the file it wrote, taken on its own descriptor AFTER the replace, so a
    caller can recognise its own bytes on disk later (``library.load``'s cache) without a window
    in which another writer's file could be stat-ed in their place."""
    directory, name = os.path.split(os.path.abspath(path))
    fd, tmp = _open_unique(directory, name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
            os.replace(tmp, path)
            return os.fstat(f.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def report_unreadable(path: str, reason: str) -> None:
    """Log that `path` exists but cannot be used and is being read as empty — once per state of
    the file (its size and mtime), so a store read on every repaint cannot flood the log. The
    store's own ``.bak`` line follows when a save copies the bytes aside."""
    try:
        st = os.stat(path)
        key = (os.path.abspath(path), st.st_size, st.st_mtime_ns)
    except OSError:
        key = (os.path.abspath(path), reason)
    if key in _reported:
        return
    _reported.add(key)
    _log.warning("could not read %s (%s): it is read as empty", path, reason)


def read_object(path: str) -> tuple[bool, dict | None]:
    """``(True, parsed)`` when `path` exists and parses to a JSON object, else ``(False, None)``:
    absent, unreadable, not JSON, or not an object. The one place every store decides "genuine
    corruption" — the only case that falls back to empty and the only one that earns a backup —
    so a store's ``load`` and its pre-save backup check can never disagree. Absent is silent (every
    store starts that way); anything else that exists is reported (``report_unreadable``)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return False, None
    except OSError as exc:
        report_unreadable(path, exc.strerror or type(exc).__name__)
        return False, None
    except ValueError as exc:           # JSONDecodeError, UnicodeDecodeError
        report_unreadable(path, f"not valid JSON: {exc}")
        return False, None
    if not isinstance(data, dict):
        report_unreadable(path, f"a JSON {type(data).__name__}, not an object")
        return False, None
    return True, data
