"""Compare two golden_session dumps for WHOLE-API numerical equivalence (eps 0 / 1e-9).

Walks both JSON trees in lockstep; any structural mismatch, any float differing by more than
EPS, and any leaf that is NaN on one side only is reported, with the max abs diff, the count of
compared leaves and each side's NaN-leaf count printed. The differing leaves are first named by
FAMILY — the same path in every phase, lap and row — ALL of them, then listed one per line up to
a cap: a cap alone showed a re-cut's first 40 leaves and hid whatever else it moved.
Exit 0 iff every leaf matches; non-zero otherwise (so it can gate CI / the commit). This was
the F1 god-object-decomposition equivalence gate (paired with studio.dev.golden_session_dump).
Usage:  python -m studio.dev.golden_compare <golden.json> <candidate.json>
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter

EPS = 1e-9

# The placeholder golden_session_dump._UNSUPPORTED records for an accessor a non-strict fingerprint
# could not serve. Spelled out rather than imported: that module puts the bindings on sys.path and
# reads the footage default at import, none of which a comparison of two JSON files needs.
UNSUPPORTED = "__unsupported__"

# A family is a leaf's path with what varies per lap / row taken out: list indices, and the dict
# keys that are lap ids (`per_lap.3`, `delta.12`) or lap pairs (`delta_between.0->13`). With only
# the indices collapsed, one per-lap change printed a family per lap id — hundreds on real footage.
_INDEX = re.compile(r"\[\d+\]")
_ID_KEY = re.compile(r"\.\d+(?:->\d+)?(?=[.\[]|$)")
_SEGMENT_END = re.compile(r"[.\[]")


def _is_nan(v) -> bool:
    return isinstance(v, float) and math.isnan(v)


def family(path: str) -> tuple[str, str]:
    """(phase, rest) for a `walk` path: `root.gopro_sectors.lap_rows[3].entry` ->
    (`gopro_sectors`, `lap_rows[].entry`). The phase is the first segment under `root` (a dump's
    base / ref / gopro ...); a top-level scalar has an empty rest, and `root` itself two empties."""
    tail = _ID_KEY.sub(".<id>", _INDEX.sub("[]", path.removeprefix("root")))
    tail = tail.removeprefix(".")
    phase = _SEGMENT_END.split(tail, maxsplit=1)[0]
    return phase, tail[len(phase):].removeprefix(".")


def _note(stats, path: str, kind: str, d: float = 0.0) -> None:
    """Count one differing leaf into its family. `kind` is "numeric" (then `d` is its |Δ|), or
    what else differed: "keys", "list len", "bool", "NaN one side", "other"."""
    fam = stats.setdefault("families", {}).setdefault(
        family(path), {"n": 0, "numeric": 0, "max": 0.0, "kinds": set()})
    fam["n"] += 1
    if kind == "numeric":
        fam["numeric"] += 1
        fam["max"] = max(fam["max"], d)
    else:
        fam["kinds"].add(kind)


def walk(a, b, path, diffs, stats):
    if isinstance(a, dict) and isinstance(b, dict):
        ka, kb = set(a), set(b)
        if ka != kb:
            diffs.append(f"{path}: key mismatch +{kb - ka} -{ka - kb}")
            _note(stats, path, "keys")
            return
        for k in sorted(ka):
            walk(a[k], b[k], f"{path}.{k}", diffs, stats)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            diffs.append(f"{path}: list len {len(a)} != {len(b)}")
            _note(stats, path, "list len")
            return
        for i, (x, y) in enumerate(zip(a, b, strict=True)):  # lengths checked equal above
            walk(x, y, f"{path}[{i}]", diffs, stats)
    elif isinstance(a, bool) or isinstance(b, bool):
        stats["n"] += 1
        if a != b:
            diffs.append(f"{path}: bool {a} != {b}")
            _note(stats, path, "bool")
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        stats["n"] += 1
        # NaN compares False with everything, so `|a - b| > EPS` below is False whenever either side
        # is NaN: a leaf that TURNED INTO NaN (0/0, the mean of an empty slice — the commonest numeric
        # regression) passed as equal. NaN on one side only is a difference; on both, a match.
        if _is_nan(a) or _is_nan(b):
            if _is_nan(a) != _is_nan(b):
                diffs.append(f"{path}: {a} != {b} (NaN on one side)")
                _note(stats, path, "NaN one side")
            return
        d = abs(float(a) - float(b))
        if d > stats["max"]:
            stats["max"] = d
            stats["max_path"] = path
        if d > EPS:
            diffs.append(f"{path}: {a} != {b} (|Δ|={d:g})")
            _note(stats, path, "numeric", d)
    else:
        stats["n"] += 1
        if a != b:
            diffs.append(f"{path}: {a!r} != {b!r}")
            _note(stats, path, "other")


def leaf_families(tree, path: str = "root") -> Counter:
    """How many leaves each family holds in one tree. A re-cut's NEW leaves are
    `leaf_families(new) - leaf_families(kept)`: a count per family, so a new lap id inside a family
    that already existed is counted too, which a set difference of family keys would miss."""
    counts: Counter = Counter()
    stack = [(tree, path)]
    while stack:
        o, p = stack.pop()
        if isinstance(o, dict):
            stack.extend((v, f"{p}.{k}") for k, v in o.items())
        elif isinstance(o, list):
            stack.extend((v, f"{p}[{i}]") for i, v in enumerate(o))
        else:
            counts[family(p)] += 1
    return counts


def family_report(stats, what: str = "moved") -> list[str]:
    """One line per family `walk` found differing, EVERY family (the cap is only on the per-leaf
    lines): leaf count, max |Δ| over its numeric leaves and/or what else differed, the path with
    lap / row indices collapsed, and the phases it moved in. Sorted by leaf count, then path.
    `stats` is walk's, or {"families": {(phase, rest): {"n": count}}} built from `leaf_families`
    (then `what` says what the counts are, e.g. "gained leaves")."""
    groups: dict[tuple[str, str], dict] = {}
    for (phase, rest), fam in stats.get("families", {}).items():
        # A top-level scalar (`root.gopro_telemetry_sha256`) has no rest: it IS its own family.
        key = (rest, "") if rest else ("", phase or "root")
        g = groups.setdefault(key, {"n": 0, "numeric": 0, "max": 0.0, "kinds": set(),
                                    "phases": set()})
        g["n"] += fam["n"]
        g["numeric"] += fam.get("numeric", 0)
        g["max"] = max(g["max"], fam.get("max", 0.0))
        g["kinds"] |= fam.get("kinds", set())
        if rest:
            g["phases"].add(phase)
    total = sum(g["n"] for g in groups.values())
    lines = [f"{len(groups)} {'family' if len(groups) == 1 else 'families'} {what} "
             f"({total} {'leaf' if total == 1 else 'leaves'}){':' if groups else ''}"]
    for (rest, top), g in sorted(groups.items(), key=lambda kv: (-kv[1]["n"], "".join(kv[0]))):
        how = ([f"max|Δ| {g['max']:.4g}"] if g["numeric"] else []) + sorted(g["kinds"])
        phases = f"  [{', '.join(sorted(g['phases']))}]" if g["phases"] else ""
        lines.append(f"  {g['n']:>6}  {', '.join(how):<18}  {rest or top}{phases}")
    return lines


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
        print(f"MISMATCH: {len(diffs)} differing leaves")
        for line in family_report(stats):
            print("  " + line)
        print("first differing leaves (up to 40):")
        for d in diffs[:40]:
            print("  " + d)
        sys.exit(1)
    print("EQUIVALENT: every leaf matches within eps", EPS)


if __name__ == "__main__":
    main()
