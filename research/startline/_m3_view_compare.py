import json
import sys

b = json.load(open(sys.argv[1]))
a = json.load(open(sys.argv[2]))

keys = [k for k in b if k != "api"]
assert set(keys) == set(k for k in a if k != "api"), "view-dump key sets differ"


def walk(x):
    if isinstance(x, bool):
        yield 1.0 if x else 0.0
    elif isinstance(x, (int, float)):
        yield float(x)
    elif isinstance(x, list):
        for e in x:
            yield from walk(e)


overall = 0.0
per_key = {}
for k in keys:
    fb = list(walk(b[k]))
    fa = list(walk(a[k]))
    assert len(fb) == len(fa), (k, "len differ", len(fb), len(fa))
    m = max((abs(x - y) for x, y in zip(fb, fa)), default=0.0)
    per_key[k] = m
    overall = max(overall, m)

print("BEFORE api:", b["api"], " AFTER api:", a["api"])
for k in keys:
    print(f"  {k}: max-abs-diff = {per_key[k]}")
print("VIEW-PATH OVERALL MAX-ABS-DIFF:", overall)
