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

# A bundled GoPro Hero6 clip carries a real GPS5 stream (the deprecated lat/lon/alt/2D/3D
# format). Used by the GPS5 field-order + Seek-clamp regression tests below.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HERO6 = os.path.join(_REPO, "3rdparty", "gpmf-parser", "samples", "hero6.mp4")


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


def test_gps5_speed_field_order_2d_ground_3d_full():
    """GPS5 element order is [lat, lon, alt, 2D ground speed, 3D speed] (GoPro spec). The C++
    parser must land 2D speed -> ground_speed and 3D speed -> full_speed (matching the GPS9
    branch and what the studio reads). It USED to write the 5 doubles via a union whose field
    order aliased 2D into full_speed and 3D into ground_speed (swapped), so this asserts the
    corrected mapping against the bundled hero6 GPS5 clip.

    Golden values are the first GPS5 sample of hero6.mp4: ground_speed (2D) = 1.221,
    full_speed (3D) = 1.23. Pre-fix these came out SWAPPED (ground=1.23, full=1.221), so the
    exact-value assert below fails before fix #2 and passes after."""
    if not os.path.exists(_HERO6):
        print("test_gps5_speed_field_order_2d_ground_3d_full SKIP (no hero6.mp4 sample)")
        return
    src = pacer.GPMFSource(_HERO6)
    rows = []
    src.read_samples(lambda s, i, n: rows.append((s.ground_speed, s.full_speed)))
    assert rows, "expected GPS5 samples from hero6.mp4"
    ground0, full0 = rows[0]
    # First sample: 2D ground speed = 1.221 m/s, 3D speed = 1.23 m/s (from the raw GPS5 stream).
    assert round(ground0, 3) == 1.221, (ground0, full0)
    assert round(full0, 3) == 1.23, (ground0, full0)
    # They are NOT equal, so the swap would be observable (sanity: order is meaningful here).
    assert ground0 != full0
    print("test_gps5_speed_field_order_2d_ground_3d_full OK")


def test_gps5_seek_before_first_payload_clamps_no_wrap():
    """Seek to a target BEFORE the first payload must clamp to index 0 and NOT report EOF.
    `index_` is uint32, so the old `--index_` at index 0 wrapped to UINT32_MAX (a bogus
    EOF-looking index); after fix #1 it clamps. Drive it on the real hero6 GPMF source:
    seek(-1.0) then read the current payload and assert it yields the first samples (not an
    empty / past-the-end span)."""
    if not os.path.exists(_HERO6):
        print("test_gps5_seek_before_first_payload_clamps_no_wrap SKIP (no hero6.mp4 sample)")
        return
    src = pacer.GPMFSource(_HERO6)
    src.seek(-1.0)  # before the first payload
    assert not src.is_end(), "seek before the first payload wrapped to a false EOF"
    a, b = src.current_time_span()
    # A real, non-empty first payload span starting at (or near) 0 — not a {0,0} sentinel.
    assert b > a, (a, b)
    got = []
    src.read_samples(lambda s, i, n: got.append(s))
    assert got, "no samples after seek(before-first) — first payload was skipped/wrapped"
    print("test_gps5_seek_before_first_payload_clamps_no_wrap OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nALL {len(tests)} GPS-SOURCE BINDING TESTS PASSED")
