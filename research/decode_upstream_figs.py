import base64
import json
import sys

import numpy as np

DTYPE = {
    'int8': np.int8, 'uint8': np.uint8, 'int16': np.int16, 'uint16': np.uint16,
    'int32': np.int32, 'uint32': np.uint32, 'int64': np.int64, 'uint64': np.uint64,
    'float32': np.float32, 'float64': np.float64,
    'i1': np.int8, 'u1': np.uint8, 'i2': np.int16, 'u2': np.uint16,
    'i4': np.int32, 'u4': np.uint32, 'i8': np.int64, 'u8': np.uint64,
    'f4': np.float32, 'f8': np.float64,
}


def decode(v):
    """Decode a plotly value that may be a typed-array {dtype,bdata} or a plain list."""
    if isinstance(v, dict) and 'bdata' in v and 'dtype' in v:
        raw = base64.b64decode(v['bdata'])
        return np.frombuffer(raw, dtype=DTYPE[v['dtype']]).astype(float)
    if isinstance(v, list):
        return np.array([x for x in v if isinstance(x, (int, float))], float)
    return None


def summ(a):
    if a is None or not len(a):
        return 'empty'
    return (f'n={len(a)} min={a.min():.5f} max={a.max():.5f} mean={a.mean():.5f} '
            f'std={a.std():.5f} median|.|={np.median(np.abs(a)):.5f} '
            f'p90|.|={np.percentile(np.abs(a), 90):.5f} p99|.|={np.percentile(np.abs(a), 99):.5f}')


nb = json.load(open(sys.argv[1]))
want = [int(x) for x in sys.argv[2:]] if len(sys.argv) > 2 else None
for i in range(len(nb['cells'])):
    if want is not None and i not in want:
        continue
    c = nb['cells'][i]
    for o in c.get('outputs', []):
        d = o.get('data', {}).get('application/vnd.plotly.v1+json')
        if not d:
            continue
        lay = d.get('layout', {})
        t = lay.get('title', {})
        ttl = t.get('text') if isinstance(t, dict) else t
        print(f"--- cell {i}  title={ttl!r} ---")
        for j, tr in enumerate(d.get('data', [])):
            ttype = tr.get('type')
            y = decode(tr.get('y'))
            x = decode(tr.get('x'))
            line = f"  tr{j} type={ttype} name={tr.get('name')}"
            if y is not None:
                line += '\n     y: ' + summ(y)
            if x is not None:
                line += '\n     x: ' + summ(x)
            print(line)
