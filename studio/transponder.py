"""Parse a lap-timing transponder CSV (the ground-truth lap times) — pure Python, no pacer.

Used by `studio/_validate_wallclock.py` to VALIDATE the GPS9 true-clock lap timing against real
transponder data out of sample. The transponder CSV is a reference INPUT only (never committed to
the repo).

The export from the timing system (e.g. "Team MIND, 24 Hour Race, Daytona 24 Hours 2026.csv")
has columns `Lap,Pos,Lap Time,Diff to Last Lap,Diff to Best Lap,Gap in Front,Diff to P1,Speed`.
The later columns embed commas and stray quotes (e.g. `2", laps`), so a naive full-row CSV
parse is unsafe — we split on comma and read only field[0] (`Lap`, an int) and field[2]
(`Lap Time`, `M:SS.mmm`).
"""

from __future__ import annotations


def parse_lap_time(text: str) -> float:
    """`M:SS.mmm` (1- or 2-digit seconds) → seconds.

    `'1:08.376'` → 68.376, `'1:9.030'` → 69.030, `'1:13.564'` → 73.564, `'3:45.985'` → 225.985.
    Raises ValueError on anything that isn't `M:SS[.mmm]`.
    """
    text = text.strip().strip('"').strip()
    minutes, sep, rest = text.partition(":")
    if not sep:
        raise ValueError(f"not a M:SS lap time: {text!r}")
    return int(minutes) * 60 + float(rest)


def parse_csv(path: str) -> dict[int, float]:
    """Read a transponder CSV → {lap_number: lap_time_seconds}.

    DEFENSIVE: split each line on comma and use only field[0] (Lap) and field[2] (Lap Time),
    skipping the header and any row whose first field isn't an integer or whose lap time doesn't
    parse — never trusting the embedded-comma later columns. Returns laps in file order (a dict
    preserves insertion order in CPython 3.7+)."""
    out: dict[int, float] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            fields = line.split(",")
            if len(fields) < 3:
                continue
            lap_str = fields[0].strip().strip('"')
            if not lap_str.lstrip("-").isdigit():
                continue  # header row or junk
            try:
                out[int(lap_str)] = parse_lap_time(fields[2])
            except (ValueError, IndexError):
                continue
    return out


def stint_times(laps: dict[int, float], lo: int, hi: int) -> list[tuple[int, float]]:
    """The (lap_number, seconds) pairs for laps in the inclusive range [lo, hi], in order.

    Used to slice the GoPro footage's stint out of the full-race transponder log (e.g. laps
    298–358 for the D24 0060 recording)."""
    return [(i, laps[i]) for i in range(lo, hi + 1) if i in laps]
