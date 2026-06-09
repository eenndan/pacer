"""Binding-surface round-trip for pacer.RawGPSSource.

The IMU/CORI readers are Python<->C++ TRAMPOLINE methods: a Python subclass overrides
`read_accl` / `read_grav` / `read_cori`, and the C++ side calls them back through the
`std::function` interface (NB_OVERRIDE_NAME). The pure-virtual control methods
(`seek` / `next` / `is_end` / `current_time_span` / `get_total_duration`) must also be
overridden in Python. This suite drives a Python subclass both DIRECTLY and — the real
test — THROUGH a C++ `SequentialGPSSource`, which holds two `RawGPSSource*` and dispatches
`ReadAccl`/etc. through the C++ vtable into the Python overrides, applying the chapter
offset to the right source. That exercises the full Python -> C++ -> Python round-trip plus
the IMUSample / QuatSample marshalling that the studio g-meter / orientation layer relies on.

Pure Python (no telemetry file). Run: python tests/test_gps_source_bindings.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pacer  # noqa: E402


class _PySource(pacer.RawGPSSource):
    """A minimal in-Python RawGPSSource backed by lists of synthetic samples."""

    def __init__(self, accl=None, grav=None, cori=None, duration=0.0):
        super().__init__()
        self._accl = accl or []
        self._grav = grav or []
        self._cori = cori or []
        self._duration = duration
        self.seek_calls = []

    # --- trampoline IMU/CORI readers (NB_OVERRIDE) ---
    def read_accl(self, on_sample):
        for s in self._accl:
            on_sample(s)

    def read_grav(self, on_sample):
        for s in self._grav:
            on_sample(s)

    def read_cori(self, on_sample):
        for s in self._cori:
            on_sample(s)

    # --- pure virtuals that MUST be overridden ---
    def seek(self, target):
        self.seek_calls.append(target)
        return 0

    def next(self):
        pass

    def is_end(self):
        return True

    def current_time_span(self):
        return (0.0, self._duration)

    def get_total_duration(self):
        return self._duration


def test_accl_trampoline_round_trips():
    """Overriding read_accl in Python and driving it returns the IMUSample fields intact."""
    src = _PySource(accl=[pacer.IMUSample(x=1.0, y=2.0, z=3.0, time=0.1),
                          pacer.IMUSample(x=4.0, y=5.0, z=6.0, time=0.2)])
    got = []
    src.read_accl(lambda s: got.append((s.x, s.y, s.z, s.time)))
    assert got == [(1.0, 2.0, 3.0, 0.1), (4.0, 5.0, 6.0, 0.2)], got
    print("test_accl_trampoline_round_trips OK")


def test_grav_and_cori_trampoline_round_trip():
    """GRAV (IMUSample) and CORI (QuatSample) both marshal Python -> C++ -> Python intact."""
    src = _PySource(grav=[pacer.IMUSample(x=0.0, y=0.0, z=9.8, time=0.05)],
                    cori=[pacer.QuatSample(w=1.0, x=0.0, y=0.0, z=0.0, time=0.05)])
    g = []
    src.read_grav(lambda s: g.append((s.x, s.y, s.z, s.time)))
    assert g == [(0.0, 0.0, 9.8, 0.05)], g

    c = []
    src.read_cori(lambda s: c.append((s.w, s.x, s.y, s.z, s.time)))
    assert c == [(1.0, 0.0, 0.0, 0.0, 0.05)], c
    print("test_grav_and_cori_trampoline_round_trip OK")


def test_pure_virtuals_dispatch_to_python():
    """The control surface (pure virtuals) calls back into the Python overrides."""
    src = _PySource(duration=12.5)
    assert src.get_total_duration() == 12.5
    assert src.is_end() is True
    assert src.current_time_span() == (0.0, 12.5)
    assert src.seek(3.0) == 0
    assert src.seek_calls == [3.0]
    print("test_pure_virtuals_dispatch_to_python OK")


def test_sequential_source_drives_python_trampoline_through_cpp():
    """The real round-trip: a C++ SequentialGPSSource holds two Python sources and calls their
    ReadAccl through the C++ vtable, shifting the RIGHT source's times by the LEFT's duration
    (the chapter-chaining offset). Proves Python -> C++ -> Python dispatch end to end."""
    left = _PySource(accl=[pacer.IMUSample(x=1.0, y=0.0, z=0.0, time=0.5)],
                     duration=10.0)
    right = _PySource(accl=[pacer.IMUSample(x=2.0, y=0.0, z=0.0, time=0.5)],
                      duration=5.0)
    seq = pacer.SequentialGPSSource(left, right)

    # Total duration sums both chapters.
    assert seq.get_total_duration() == 15.0

    got = []
    seq.read_accl(lambda s: got.append((s.x, round(s.time, 6))))
    # Left sample at its own time; right sample shifted by the left duration (10.0).
    assert got == [(1.0, 0.5), (2.0, 10.5)], got
    print("test_sequential_source_drives_python_trampoline_through_cpp OK")


def test_sequential_source_chains_cori_with_offset():
    """Same chapter-offset chaining for the CORI (QuatSample) stream."""
    left = _PySource(cori=[pacer.QuatSample(w=1.0, x=0.0, y=0.0, z=0.0, time=1.0)],
                     duration=8.0)
    right = _PySource(cori=[pacer.QuatSample(w=0.0, x=1.0, y=0.0, z=0.0, time=2.0)],
                      duration=4.0)
    seq = pacer.SequentialGPSSource(left, right)
    got = []
    seq.read_cori(lambda s: got.append((s.w, s.x, round(s.time, 6))))
    assert got == [(1.0, 0.0, 1.0), (0.0, 1.0, 10.0)], got  # right shifted by 8.0
    print("test_sequential_source_chains_cori_with_offset OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nALL {len(tests)} GPS-SOURCE BINDING TESTS PASSED")
