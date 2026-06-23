"""Ingest: the pacer-touching GoPro/GPMF IO layer.

Builds the `SequentialGPSSource` chain and reads raw GPS + IMU; no Laps/analysis (session.py
does that). One of the few pacer-touching modules (see AGENTS.md).
"""

from __future__ import annotations

import numpy as np

import pacer


def chain_sources(paths):
    """Build the `SequentialGPSSource` chain over the GoPro chapters -> (head, owners, durations).
    Chapters are folded onto ONE global clock (C++ shifts later chapters by cumulative duration).
    `owners` keeps intermediate sources alive while the caller iterates `head`. `durations` =
    each chapter's 0-based media duration, in `paths` order (the video layer's offset table)."""
    owners = [pacer.GPMFSource(paths[0])]
    durations = [owners[0].get_total_duration()]
    head = owners[0]
    for p in paths[1:]:
        nxt = pacer.GPMFSource(p)
        owners.append(nxt)
        durations.append(nxt.get_total_duration())
        head = pacer.SequentialGPSSource(head, nxt)
        owners.append(head)  # keep the chain alive while we iterate
    return head, owners, durations


def _read_gps_over(head):
    """Walk an already-built `head` -> (samples, spans, naive): seek(0) then iterate the payload
    cursor to the end."""
    samples, spans, naive = [], [], []
    head.seek(0)
    while not head.is_end():
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
    """Pack an ACCL/GRAV `ImuArrays` (times/xs/ys/zs columns) into an (N,4) [t,x,y,z] array,
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
    (samples, spans, naive, durations, accl, grav, cori). Avoids opening/parsing each chapter
    twice; byte-identical to read_gpmf + read_imu (the IMU pass is cursor-independent)."""
    head, _owners, durations = chain_sources(paths)
    samples, spans, naive = _read_gps_over(head)
    accl, grav, cori = _read_imu_over(head)
    return samples, spans, naive, durations, accl, grav, cori


def read_gpmf(paths):
    """GPS-only reader for dev scripts -> (samples, spans, naive, durations). Thin wrapper over
    chain_sources + _read_gps_over; prod load uses read_recording."""
    head, _owners, durations = chain_sources(paths)
    samples, spans, naive = _read_gps_over(head)
    return samples, spans, naive, durations


def read_imu(paths):
    """IMU-only reader for dev scripts -> (accl, grav, cori) on the global media clock.
    accl/grav (N,4) [t,x,y,z]; cori (N,5) [t,w,x,y,z]; empty arrays when a camera lacks a stream.
    Prod load uses read_recording."""
    head, _owners, _durations = chain_sources(paths)
    return _read_imu_over(head)
