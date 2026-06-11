import json
import sys

bp, ap = sys.argv[1], sys.argv[2]
b = json.load(open(bp))
a = json.load(open(ap))


def walk(x):
    if isinstance(x, bool):
        return
    if isinstance(x, (int, float)):
        yield float(x)
    elif isinstance(x, list):
        for e in x:
            yield from walk(e)


assert set(b) == set(a), ("session sets differ", set(b), set(a))
overall_max = 0.0
mismatches = []
for sess in b:
    bs, asd = b[sess], a[sess]
    assert set(bs) == set(asd), (sess, "key sets differ", set(bs) ^ set(asd))
    for k in bs:
        vb, va = bs[k], asd[k]
        if isinstance(vb, str) or vb is None:
            if vb != va:
                mismatches.append((sess, k, vb, va))
            continue
        fb = list(walk(vb))
        fa = list(walk(va))
        if len(fb) != len(fa):
            mismatches.append((sess, k, "LEN", len(fb), len(fa)))
            continue
        m = max((abs(x - y) for x, y in zip(fb, fa)), default=0.0)
        overall_max = max(overall_max, m)
        if m > 0:
            mismatches.append((sess, k, "maxdiff", m))

print("SESSIONS:", list(b))
for sess in b:
    print(f"  {sess}: pts={b[sess]['point_count']} laps={b[sess]['laps_count']} "
          f"best={b[sess]['best_lap_id']} gmeter_len={b[sess].get('gmeter_len')}")
print("OVERALL MAX-ABS-DIFF (all GPS/IMU/lap numeric outputs, both sessions):", overall_max)
print("MISMATCHES:", mismatches if mismatches else "NONE")
