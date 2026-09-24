"""Ingest: the pacer-touching GoPro/GPMF IO layer.

Builds the `SequentialGPSSource` chain and reads raw GPS + IMU (ACCL / GYRO / GRAV / CORI) plus
the camera's own device name; no Laps/analysis (session.py does that). One of the few
pacer-touching modules (see AGENTS.md).
"""

from __future__ import annotations

import contextlib
import threading

import numpy as np

import pacer

from . import chapters


class LoadCancelled(Exception):
    """The payload walk stopped because the load it belongs to was abandoned (see `cancellable`)."""


# The cancel check installed for reads on THIS thread (see `cancellable`). Thread-local, so the check
# a load worker installs for its own read can never stop a read another thread is doing.
_cancel = threading.local()


@contextlib.contextmanager
def cancellable(is_cancelled):
    """Run the reads this thread makes inside the block under `is_cancelled`: the GPS payload walk
    asks it once per payload and raises `LoadCancelled` as soon as it answers True.

    The walk is the one stage of a load whose length the FILE decides — one payload a second of
    footage, through a Python loop — so it is where a load can run for as long as a file says.
    A damaged moov once made it walk empty payload indices for hours, queueing every later open
    behind it and turning quit into a 60 s wait and an abort; the C++ cursor is bounded by the
    payload count now, and this is what lets an open or a quit stop any read that is merely slow.
    The check is a Python call in a loop that already crosses the binding several times per
    payload, so it costs nothing measurable."""
    previous = getattr(_cancel, "check", None)
    _cancel.check = is_cancelled
    try:
        yield
    finally:
        _cancel.check = previous

# `carries_telemetry`'s three answers. THREE, and the third is the point, exactly as in
# `chapters.probe_mp4`: "I opened this file and pacer found no GoPro telemetry in it" and "I could
# not read this file" are different facts about the world and must lead to opposite behaviour. The
# first is permanent and local to one file; the second is temporary and says nothing about the
# contents (a chmod, a still-copying file, an unmounted volume), so it must never be reported as a
# verdict on what the file holds.
TELEMETRY_PRESENT = "telemetry"
TELEMETRY_ABSENT = "no_telemetry"
TELEMETRY_UNKNOWN = "unknown"


def carries_telemetry(path: str) -> str:
    """Can pacer read GoPro telemetry out of `path`? -> `TELEMETRY_PRESENT` / `TELEMETRY_ABSENT` /
    `TELEMETRY_UNKNOWN`.

    This is the LOADER'S OWN FIRST GATE and nothing more: `pacer.GPMFSource`'s constructor calls
    `OpenMP4Source` for the GPMF trak and raises when the file hasn't got one
    (`pacer/gps-source/gps-source.cpp:17-23`). It reads the moov atom, not the payloads, so it is
    CHEAP — measured on this machine over the owner's own footage: 5.23 ms and 1.64 ms on the two
    11.9 GB chapters of D24 recording 0062, 0.78 ms on a 147 MB overlay export, 1.32 ms on an
    8.5 GB non-GoPro clip, 0.42 ms on the 2.4 MB stub. Cheap enough to answer "is this actually a
    recording?" on the UI thread, which is the whole reason it exists: `StudioWindow._open_recordings`
    would otherwise have to LOAD a file (2.2-2.4 s for a three-chapter recording) to find out, and
    that is the difference between a question the drop path can ask and one it cannot.

    `TELEMETRY_UNKNOWN` is returned whenever the bytes did not arrive — the caller must treat it as
    "assume it is a recording" and let the real load fail loudly on it, never as a silent skip. A
    file that READS and proves not to be an MP4 container at all (the destroyed stub) is
    `TELEMETRY_ABSENT`: that is a positive verdict, and `chapters.probe_mp4` already drew exactly
    that line.

    NOT a claim that the file has usable LAPS, a track, or anything else a load decides — only that
    pacer can get at its telemetry stream. Everything past that is `Session.load`'s business."""
    probe = chapters.probe_mp4(path)
    if probe == chapters.MP4_UNREADABLE:
        return TELEMETRY_UNKNOWN
    if probe == chapters.MP4_NOT_A_CONTAINER:
        return TELEMETRY_ABSENT
    try:
        pacer.GPMFSource(path)
    except Exception:  # noqa: BLE001 — any refusal from the C++ opener is "no telemetry here"
        return TELEMETRY_ABSENT
    return TELEMETRY_PRESENT


def recording_carries_telemetry(paths: list[str]) -> str:
    """The same three answers for a whole RECORDING (its chapter list), which is the unit the app
    opens.

    ANY chapter with telemetry makes the recording readable, because `Session.load` drops the
    chapters it can't parse and loads the rest — `~/Desktop/D24/GX010060.MP4` is 2.4 MB of JSON and
    recording 0060 still loads its other two chapters. So a recording is only `TELEMETRY_ABSENT`
    when EVERY chapter was read and none of them had any, and `TELEMETRY_UNKNOWN` the moment one
    chapter could not be read at all — the unreadable case propagates, so a locked or still-copying
    file can never turn into "this isn't a recording"."""
    verdicts = [carries_telemetry(p) for p in paths]
    if not verdicts or TELEMETRY_PRESENT in verdicts:
        return TELEMETRY_PRESENT if verdicts else TELEMETRY_UNKNOWN
    return TELEMETRY_UNKNOWN if TELEMETRY_UNKNOWN in verdicts else TELEMETRY_ABSENT


def chain_sources(paths):
    """Build the `SequentialGPSSource` chain over the GoPro chapters ->
    (head, owners, durations, meta_durations).
    Chapters are folded onto ONE global clock (C++ shifts later chapters by cumulative duration).
    `owners` keeps intermediate sources alive while the caller iterates `head`.

    `durations` = each chapter's VIDEO-track duration, in `paths` order — the offset table the
    video layer seeks with, and the same number the C++ chain shifts the following chapter's
    telemetry by, so the two axes cannot drift apart.

    `meta_durations` = the same chapters' GPMF-track durations. NOT interchangeable with the
    above, which is the whole reason both are returned: a GoPro chapter's metadata track ends on
    its own payload grid, and on GoPro's own sample clips it misses the video length by anything
    from -0.701 s (hero7) to +0.934 s (karma). Their DIFFERENCE is what `ChapterMap` reports as a
    desynced chapter."""
    owners: list[pacer.RawGPSSource] = [pacer.GPMFSource(paths[0])]
    durations = [owners[0].get_video_duration()]
    meta_durations = [owners[0].get_total_duration()]
    head = owners[0]
    for p in paths[1:]:
        nxt = pacer.GPMFSource(p)
        owners.append(nxt)
        durations.append(nxt.get_video_duration())
        meta_durations.append(nxt.get_total_duration())
        head = pacer.SequentialGPSSource(head, nxt)
        owners.append(head)  # keep the chain alive while we iterate
    return head, owners, durations, meta_durations


def _read_gps_over(head):
    """Walk an already-built `head` -> (samples, spans, naive): seek(0) then iterate the payload
    cursor to the end — or raise `LoadCancelled` at the first payload after this thread's
    `cancellable` check answers True."""
    samples, spans, naive = [], [], []
    is_cancelled = getattr(_cancel, "check", None)
    head.seek(0)
    while not head.is_end():
        if is_cancelled is not None and is_cancelled():
            raise LoadCancelled("the load was abandoned during its GPS read")
        a, b = head.current_time_span()
        chunk = []
        head.read_samples(lambda s, i, n, _c=chunk: _c.append((s, i, n)))
        for s, i, n in chunk:
            samples.append(s)
            spans.append((a, b))
            naive.append(a + (b - a) * (i / n if n else 0.0))
        head.next()
    return samples, spans, naive


def _vec3_columns(cols):
    """Pack an ACCL/GYRO/GRAV `ImuArrays` (times/xs/ys/zs columns) into an (N,4) [t,x,y,z] array,
    byte-identical to the old per-sample `(s.time, s.x, s.y, s.z)` append + reshape(-1, 4)."""
    return np.array([cols.times, cols.xs, cols.ys, cols.zs], float).T.reshape(-1, 4)


def _read_imu_over(head):
    """Read ACCL/GRAV/CORI off an already-built `head` -> accl(N,4), grav(N,4), cori(N,5).
    Uses the BULK column readers (read_*_columns) — one binding crossing per stream into parallel
    std::vector columns — instead of the old per-sample C++->Python callback (~1.5M trampoline
    round-trips per load). The columns are the same samples in the same order, so the packed
    arrays are byte-identical to the per-sample path. Independent of the GPS payload cursor, so
    pass order vs GPS doesn't matter."""
    a = _vec3_columns(head.read_accl_columns())
    g = _vec3_columns(head.read_grav_columns())
    cc = head.read_cori_columns()  # CORI carries the quaternion w -> (N,5) [t,w,x,y,z]
    c = np.array([cc.times, cc.ws, cc.xs, cc.ys, cc.zs], float).T.reshape(-1, 5)
    return a, g, c


def read_recording(paths):
    """Single-pass ingest: build the chain once, run GPS then IMU over the SAME sources ->
    (samples, spans, naive, durations, meta_durations, accl, grav, cori, gyro, device). Avoids
    opening/parsing each chapter twice; byte-identical to read_gpmf + read_imu (the IMU pass is
    cursor-independent). See `chain_sources` for why the two duration lists are both returned.

    GYRO AND THE DEVICE NAME RIDE ALONG NOW. `read_gyro` / `read_device_name` below each build
    their OWN chain, which means re-opening and re-parsing every chapter of an 11.9 GB recording —
    acceptable for a dev script, not on the load path now that `Session` builds the measured
    rotation channel at load. Both readers stay for the dev scripts; the app takes them from here,
    off the chain it already has."""
    head, _owners, durations, meta_durations = chain_sources(paths)
    samples, spans, naive = _read_gps_over(head)
    accl, grav, cori = _read_imu_over(head)
    gyro = _vec3_columns(head.read_gyro_columns())
    return (samples, spans, naive, durations, meta_durations, accl, grav, cori, gyro,
            head.device_name())


def read_gpmf(paths):
    """GPS-only reader for dev scripts -> (samples, spans, naive, durations). Thin wrapper over
    chain_sources + _read_gps_over; prod load uses read_recording."""
    head, _owners, durations, _meta = chain_sources(paths)
    samples, spans, naive = _read_gps_over(head)
    return samples, spans, naive, durations


def read_imu(paths):
    """IMU-only reader for dev scripts -> (accl, grav, cori) on the global media clock.
    accl/grav (N,4) [t,x,y,z]; cori (N,5) [t,w,x,y,z]; empty arrays when a camera lacks a stream.
    Prod load uses read_recording."""
    head, _owners, _durations, _meta = chain_sources(paths)
    return _read_imu_over(head)


def read_gyro(paths):
    """GYRO-only reader -> (N,4) [t,x,y,z] rad/s on the global media clock (empty when the camera
    writes no GYRO — pre-HERO5).

    A DEV-SCRIPT reader: it builds its own chain, so it re-opens and re-parses every chapter. The
    app reads GYRO off `read_recording`'s shared chain instead.

    GYRO rides the same chain and the same media clock as ACCL, but NOT the same sample rate: it
    runs 2x ACCL on a HERO5 and a Karma, 4x on a Max in 360 mode and 17x on a Fusion (measured on
    the bundled gpmf-parser samples), and only happens to match row for row on the HERO13. Join
    the two on `[:, 0]`, never by index."""
    head, _owners, _durations, _meta = chain_sources(paths)
    return _vec3_columns(head.read_gyro_columns())


def read_device_name(paths):
    """The recording camera's own name (GPMF `DVNM`, e.g. "HERO13 Black"), or "" when the
    container carries none. Cheap — it reads the first payload, not the stream."""
    head, _owners, _durations, _meta = chain_sources(paths)
    return head.device_name()
