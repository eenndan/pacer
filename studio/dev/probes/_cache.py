"""Shared load-once cache for the speculative-channel probes.

Loading one D24 recording is a ~20-25 GB GPMF scan; three probes that each did their own load
would spend an afternoon in `ingest`. This module loads a recording ONCE through the REAL
`Session.load` path, packs what every probe needs into a single `.npz` under `$TMPDIR`, and hands
back a light view.

    pixi run python -m studio.dev.probes._cache            # build both caches
    pixi run python -m studio.dev.probes._cache 0060       # build one

WHAT IT STORES, AND WHY EACH PIECE IS HERE
  * RAW ACCL/GRAV/GYRO at their native ~200 Hz. Not the derived channels: `gmeter.compute`
    boxcars 0.15 s and resamples to 50 Hz, and the wheel-hop question (p2) is specifically about
    the band that removes.
  * The per-lap GPS columns and the corner partition, as the app segmented them.
  * THE CLOCK. `media_clock` rate/offset AND `RotationCheck.gps_lag_s`, because the lap columns
    and the IMU streams are on different clocks and every probe here differences the two. See
    `_align`; a cache without these two numbers is a cache that quietly forges the signal.

TWO SAFETY PROPERTIES, both enforced rather than asserted in prose:
  * `~/Desktop/D24` IS READ-ONLY. Nothing here takes an output path near it, and `_d24_state`
    snapshots every chapter's (size, mtime) before and after the load and refuses to write a
    cache if any of them moved.
  * `Session.load` UPSERTS INTO THE USER'S LIBRARY. `studio.dev._jail` diverts every app-support
    seam to a throwaway dir first, so a probe run cannot touch the owner's library, track DB or
    preferences — and, because `track_db` is diverted too, the load takes the unknown-track
    auto-fit path every time instead of depending on which tracks they happen to have saved.
"""

from __future__ import annotations

import os
import sys
import time

import numpy as np

RECORDINGS = {
    "0060": ["GX020060.MP4", "GX030060.MP4"],
    "0062": ["GX010062.MP4", "GX020062.MP4", "GX030062.MP4"],
}
# ~/Desktop/D24 is READ-ONLY: these paths are only ever opened for reading, never passed as an
# output argument. GX010060.MP4 is deliberately absent — it is a 2.4 MB JSON stub that overwrote
# 11.9 GB of the owner's footage, and `Session.load` would drop it anyway.
D24 = os.path.expanduser("~/Desktop/D24")


def paths_for(key: str) -> list[str]:
    return [os.path.join(D24, n) for n in RECORDINGS[key]]


def cache_path(key: str) -> str:
    return os.path.join(os.environ.get("TMPDIR", "/tmp"), f"pacer_probe_{key}.npz")


def _d24_state(paths):
    """(size, mtime_ns) per chapter — the read-only tripwire, taken before and after the load."""
    return {p: (os.path.getsize(p), os.stat(p).st_mtime_ns) for p in paths}


def build(key: str) -> str:
    """Load recording `key` through the real Session pipeline and write its probe cache."""
    from studio.dev import _jail

    # BEFORE any studio import resolves a seam: the load-time library upsert resolves it at call
    # time, so diverting afterwards is too late for exactly the write that matters.
    jail = _jail.divert_app_support("pacer-probe-")
    print(f"[{key}] app-support seams diverted to {jail.dir}")

    from studio import ingest
    from studio.session import Session

    out = cache_path(key)
    paths = paths_for(key)
    before = _d24_state(paths)

    t0 = time.time()
    s = Session.load(paths)
    print(f"[{key}] Session.load {time.time() - t0:.1f}s  laps={s.lap_count()}")

    # One extra chain pass for the RAW streams: Session keeps only the derived 50 Hz g-meter and
    # the smoothed yaw rate, and p2 is specifically a question about the 200 Hz content that
    # resampling removes. The column readers are bulk and independent of the GPS payload cursor.
    t0 = time.time()
    head, _owners, _d, _m = ingest.chain_sources(paths)
    accl = ingest._vec3_columns(head.read_accl_columns())
    grav = ingest._vec3_columns(head.read_grav_columns())
    gyro = ingest._vec3_columns(head.read_gyro_columns())
    device = head.device_name()
    print(f"[{key}] raw IMU {time.time() - t0:.1f}s  accl={accl.shape} grav={grav.shape} "
          f"gyro={gyro.shape}  device={device!r}")

    # THE CLOCK — the two numbers without which none of these probes measures the car.
    clock = s.media_clock
    cross = s.rotation_cross()
    if cross is None or cross.gps_lag_s is None:
        raise SystemExit(
            f"[{key}] no rotation cross-check / no measured gps_lag_s: every probe in this "
            "package differences a GPS channel against an inertial one, and without that offset "
            "they would measure the clock. Refusing to build a cache that cannot be aligned.")
    print(f"[{key}] {clock!r}; gps_lag_s={cross.gps_lag_s:+.4f}s "
          f"(r={cross.lag_corr:+.3f} at that offset vs {cross.lag_corr_at_zero:+.3f} with it in); "
          f"loop gyro {cross.loop_ratio_gyro:.4f} path {cross.loop_ratio_path:.4f}")

    clean = s.consistency_lap_ids()
    valid = s.valid_lap_ids()
    cols = [s._lap_columns(i) for i in clean]
    lengths = [len(c[0]) for c in cols]
    starts = np.concatenate(([0], np.cumsum(lengths))).astype(np.int64)

    def cat(j):
        return np.concatenate([np.asarray(c[j], float) for c in cols])

    corners = s.corners.corner_list()
    best = s.best_lap_id()
    data = dict(
        accl=accl, grav=grav, gyro=gyro,
        lap_t=cat(0), lap_x=cat(1), lap_y=cat(2), lap_v=cat(3), lap_d=cat(4),
        lap_starts=starts,
        lap_ids=np.asarray(clean, np.int64),
        lap_times=np.asarray([s.lap_time(i) for i in clean], float),
        valid_lap_count=np.asarray([len(valid)], np.int64),
        corner_cid=np.asarray([c.cid for c in corners], np.int64),
        corner_enter=np.asarray([c.enter for c in corners], float),
        corner_exit=np.asarray([c.exit for c in corners], float),
        corner_apex=np.asarray([c.apex for c in corners], float),
        corner_dir=np.asarray([c.direction for c in corners], np.int64),
        corner_turn_deg=np.asarray([c.turn_deg for c in corners], float),
        best_lap_id=np.asarray([-1 if best is None else best], np.int64),
        # --- the clock (see _align) ---
        clock_rate=np.asarray([clock.rate], float),
        clock_offset=np.asarray([clock.offset], float),
        gps_lag_s=np.asarray([cross.gps_lag_s], float),
        lag_corr=np.asarray([cross.lag_corr], float),
        lag_corr_at_zero=np.asarray([cross.lag_corr_at_zero], float),
        loop_ratio_gyro=np.asarray([cross.loop_ratio_gyro], float),
        loop_ratio_path=np.asarray([cross.loop_ratio_path], float),
        rot_corr=np.asarray([cross.corr], float),
        rot_gain=np.asarray([cross.gain], float),
        device=np.asarray([device]),
        paths=np.asarray(paths),
    )

    after = _d24_state(paths)
    if after != before:
        moved = [os.path.basename(p) for p in paths if after[p] != before[p]]
        raise SystemExit(f"[{key}] REFUSING TO CONTINUE: a D24 chapter changed during the load "
                         f"({', '.join(moved)}). That directory is read-only.")
    print(f"[{key}] D24 read-only check: {len(paths)} chapters unchanged (size + mtime)")

    np.savez(out, **data)
    print(f"[{key}] wrote {out} ({os.path.getsize(out) / 1e6:.0f} MB), "
          f"clean laps={len(clean)} (valid {len(valid)}) corners={len(corners)}")
    return out


class Rec:
    """A loaded probe cache. Attribute access straight onto the stored arrays."""

    def __init__(self, key: str):
        self.key = key
        self.z = np.load(cache_path(key), allow_pickle=False)
        self.accl = self.z["accl"]
        self.grav = self.z["grav"]
        self.gyro = self.z["gyro"]
        self.lap_ids = self.z["lap_ids"]
        self.lap_times = self.z["lap_times"]
        self._starts = self.z["lap_starts"]
        self.device = str(self.z["device"][0])
        self.best_lap_id = int(self.z["best_lap_id"][0])
        self.valid_lap_count = int(self.z["valid_lap_count"][0])
        self.corner_cid = self.z["corner_cid"]
        self.corner_enter = self.z["corner_enter"]
        self.corner_exit = self.z["corner_exit"]
        self.corner_apex = self.z["corner_apex"]
        self.corner_dir = self.z["corner_dir"]
        self.corner_turn_deg = self.z["corner_turn_deg"]
        # --- the clock (see _align) ---
        self.clock_rate = float(self.z["clock_rate"][0])
        self.clock_offset = float(self.z["clock_offset"][0])
        self.gps_lag_s = float(self.z["gps_lag_s"][0])
        self.lag_corr = float(self.z["lag_corr"][0])
        self.lag_corr_at_zero = float(self.z["lag_corr_at_zero"][0])
        self.loop_ratio_gyro = float(self.z["loop_ratio_gyro"][0])
        self.loop_ratio_path = float(self.z["loop_ratio_path"][0])
        self.rot_corr = float(self.z["rot_corr"][0])
        self.rot_gain = float(self.z["rot_gain"][0])

    def __len__(self) -> int:
        return len(self.lap_ids)

    def lap(self, i: int):
        """(t, x, y, v, d) for the i-th clean lap.

        `t` is the lap columns' own TELEMETRY (GPS9 true-clock) axis, exactly as
        `Session._lap_columns` returns it — deliberately not pre-converted, so that every probe's
        conversion is visible at the point of use. Put it on the GYRO's clock with
        `_align.to_gyro_clock` and on the ACCL's with `_align.to_accl_clock` — two maps, because
        the two streams' content does not ride the same clock (see `_align`)."""
        sl = slice(int(self._starts[i]), int(self._starts[i + 1]))
        return (self.z["lap_t"][sl], self.z["lap_x"][sl], self.z["lap_y"][sl],
                self.z["lap_v"][sl], self.z["lap_d"][sl])

    def laps(self):
        for i in range(len(self)):
            yield i, self.lap(i)


def load(key: str) -> Rec:
    if not os.path.exists(cache_path(key)):
        build(key)
    return Rec(key)


if __name__ == "__main__":
    keys = sys.argv[1:] or list(RECORDINGS)
    for k in keys:
        build(k)
