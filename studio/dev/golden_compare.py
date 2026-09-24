"""Compare two golden_session dumps for WHOLE-API numerical equivalence (eps 0 / 1e-9).

Walks both JSON trees in lockstep; any structural mismatch, any float differing by more than
EPS, and any leaf that is NaN on one side only is reported (up to a cap), with the max abs diff,
the count of compared leaves and each side's NaN-leaf count printed.
Exit 0 iff every leaf matches; non-zero otherwise (so it can gate CI / the commit). This was
the F1 god-object-decomposition equivalence gate (paired with studio.dev.golden_session_dump).
Usage:  python -m studio.dev.golden_compare <golden.json> <candidate.json>
"""
from __future__ import annotations

import json
import math
import sys

EPS = 1e-9

# The placeholder golden_session_dump._UNSUPPORTED records for an accessor a non-strict fingerprint
# could not serve. Spelled out rather than imported: that module puts the bindings on sys.path and
# reads the footage default at import, none of which a comparison of two JSON files needs.
UNSUPPORTED = "__unsupported__"


def _is_nan(v) -> bool:
    return isinstance(v, float) and math.isnan(v)


def walk(a, b, path, diffs, stats):
    if isinstance(a, dict) and isinstance(b, dict):
        ka, kb = set(a), set(b)
        if ka != kb:
            diffs.append(f"{path}: key mismatch +{kb - ka} -{ka - kb}")
            return
        for k in sorted(ka):
            walk(a[k], b[k], f"{path}.{k}", diffs, stats)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            diffs.append(f"{path}: list len {len(a)} != {len(b)}")
            return
        for i, (x, y) in enumerate(zip(a, b, strict=True)):  # lengths checked equal above
            walk(x, y, f"{path}[{i}]", diffs, stats)
    elif isinstance(a, bool) or isinstance(b, bool):
        stats["n"] += 1
        if a != b:
            diffs.append(f"{path}: bool {a} != {b}")
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        stats["n"] += 1
        # NaN compares False with everything, so `|a - b| > EPS` below is False whenever either side
        # is NaN: a leaf that TURNED INTO NaN (0/0, the mean of an empty slice — the commonest numeric
        # regression) passed as equal. NaN on one side only is a difference; on both, a match.
        if _is_nan(a) or _is_nan(b):
            if _is_nan(a) != _is_nan(b):
                diffs.append(f"{path}: {a} != {b} (NaN on one side)")
            return
        d = abs(float(a) - float(b))
        if d > stats["max"]:
            stats["max"] = d
            stats["max_path"] = path
        if d > EPS:
            diffs.append(f"{path}: {a} != {b} (|Δ|={d:g})")
    else:
        stats["n"] += 1
        if a != b:
            diffs.append(f"{path}: {a!r} != {b!r}")


def census(tree) -> dict[str, int]:
    """Count one fingerprint's leaves, and among them the three kinds that carry no number: NaN,
    null and the UNSUPPORTED placeholder. A leaf moving into one of those is a regression even
    where a comparison cannot see it — e.g. in a baseline re-written over it."""
    counts = {"leaves": 0, "nan": 0, "null": 0, "unsupported": 0}
    stack = [tree]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
        else:
            counts["leaves"] += 1
            if o is None:
                counts["null"] += 1
            elif _is_nan(o):
                counts["nan"] += 1
            elif o == UNSUPPORTED:
                counts["unsupported"] += 1
    return counts


def main(argv: list[str] | None = None):
    golden, candidate = (sys.argv[1:] if argv is None else argv)[:2]
    with open(golden) as f:
        a = json.load(f)
    with open(candidate) as f:
        b = json.load(f)
    diffs: list[str] = []
    stats = {"n": 0, "max": 0.0, "max_path": ""}
    walk(a, b, "root", diffs, stats)
    print(f"compared {stats['n']} leaf values; max |Δ| = {stats['max']:g} "
          f"at {stats['max_path']}; NaN leaves: golden {census(a)['nan']}, "
          f"candidate {census(b)['nan']}")
    if diffs:
        print(f"MISMATCH: {len(diffs)} differing leaves (showing up to 40):")
        for d in diffs[:40]:
            print("  " + d)
        sys.exit(1)
    print("EQUIVALENT: every leaf matches within eps", EPS)


if __name__ == "__main__":
    main()
