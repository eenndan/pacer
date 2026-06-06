"""Deterministic dump of every ANALYSIS value the studio surfaces, for the UI-only
byte-identity proof. Loads a session and prints (then MD5s) the lap times, distances,
entry speeds, per-sector splits (for a couple of sector configurations), and the full
delta/speed series — i.e. every quantity F1–F5 touch the *display* of but must not change.

Run: python -m studio._analysis_dump <video> [more videos]
The MD5 must be identical on the branch base and after the UI features land.
"""
import hashlib
import sys

import numpy as np

from studio.session import Session


def _fmt(x: float) -> str:
    # Fixed-precision so the textual dump is stable across runs (and float repr quirks).
    return f"{x:.9f}"


def dump(paths: list[str]) -> str:
    s = Session.load(paths)
    lines: list[str] = []
    lines.append(f"points={s.laps.point_count()} laps={s.lap_count()}")
    valid = s.valid_lap_ids()
    lines.append("valid=" + ",".join(str(i) for i in valid))
    lines.append("best=" + str(s.best_lap_id()))

    for i in valid:
        lines.append(
            f"lap {i}: time={_fmt(s.laps.lap_time(i))} "
            f"dist={_fmt(s.laps.get_lap_distance(i, s.cs))} "
            f"entry={_fmt(s.laps.lap_entry_speed(i) * 3.6)}"
        )

    # Sector splits under a few sector-line configurations (0, 1, 2, 3 sectors). F2/F5 redraw
    # these; the VALUES must be untouched. Use suggest_sector so the config is deterministic.
    start = s.start_line
    sectors: list = []
    for n in range(4):
        if n:
            sectors = [s.suggest_sector(k) for k in range(n)]
        s.set_timing_lines(start, sectors)
        lines.append(f"--- {n} sectors ---")
        for i in s.valid_lap_ids():
            sp = s.lap_sector_splits(i)
            lines.append(f"splits[{i}]=" + ",".join(_fmt(v) for v in sp))
    # Restore the default (no sectors) and dump the delta+speed series.
    s.set_timing_lines(start, [])
    for mode in ("distance", "time"):
        res = s.delta(valid, x_mode=mode)
        if res is None:
            lines.append(f"delta[{mode}]=None")
            continue
        best, speed, delta = res
        lines.append(f"delta[{mode}] best={best}")
        for lid in sorted(speed):
            sx, spd = speed[lid]
            dx, dl = delta[lid]
            h = hashlib.md5()
            for arr in (sx, spd, dx, dl):
                h.update(np.asarray(arr, dtype=np.float64).tobytes())
            lines.append(f"  series[{lid}]={h.hexdigest()}")

    text = "\n".join(lines) + "\n"
    return text


def main():
    paths = sys.argv[1:]
    if not paths:
        print("usage: python -m studio._analysis_dump <video> [...]", file=sys.stderr)
        return 2
    text = dump(paths)
    digest = hashlib.md5(text.encode()).hexdigest()
    sys.stdout.write(text)
    sys.stdout.write(f"\nMD5={digest}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
