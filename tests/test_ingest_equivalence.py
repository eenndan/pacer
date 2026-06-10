"""Single-pass ingest equivalence (PR #40): `ingest.read_recording` == the pre-#40 two-pass reads.

PR #40 replaced the studio load path's TWO `chain_sources` builds (read_gpmf opened and
GPMF-parsed every chapter once for GPS, read_imu opened them all AGAIN for ACCL/GRAV/CORI) with
ONE shared chain that both passes run over (`read_recording`). The claim is byte-identical
output. Two pins:

1. SHARED-CHAIN EQUIVALENCE on real media: `read_recording([sample])` over the bundled GoPro
   sample clips must equal a hand-built replica of the pre-#40 behaviour — two FRESH
   `GPMFSource` opens of the same file, one drained for GPS exactly as `read_gpmf`'s inline pass
   did, the other read for IMU exactly as `read_imu` did — exactly, for every GPS field and
   every IMU stream. hero6.mp4 carries GPS5 + ACCL (no GRAV/CORI on that camera; their
   equivalence there is empty == empty); hero8.mp4 carries GPS + all three IMU streams.

2. IMU-AFTER-GPS-DRAIN INVARIANCE on a Python stub: the single-pass design relies on the IMU
   readers being independent of the GPS payload cursor (`_read_imu_over` runs AFTER
   `_read_gps_over` has seeked/walked the same source to its end). Drive ingest's internal GPS
   helper over a plain-Python `RawGPSSource` whose IMU readers honour that same contract, and
   assert the IMU reads after the drain equal the reads before it. (The trampoline does NOT
   cover the GPS `Samples` virtual, so the stub feeds GPS only through ingest's *Python-side*
   helper, never through a C++ `SequentialGPSSource` — read_accl/grav/cori ARE covered, as
   test_gps_source_bindings.py proves.)

Imports pacer + studio.ingest only (no Qt). Run: python tests/test_ingest_equivalence.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pacer  # noqa: E402
from studio import ingest  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SAMPLES = os.path.join(_REPO, "3rdparty", "gpmf-parser", "samples")

# Every field of the bound GPSSample — the equivalence must hold for all of them, including the
# GPS9 quality fields (dop/fix keep their -1 sentinels on these GPS5-era sample clips).
_GPS_FIELDS = ("lat", "lon", "altitude", "full_speed", "ground_speed",
               "timestamp_ms", "dop", "fix")


def _gps_rows(samples):
    """Project bound GPSSample objects to plain tuples so lists compare exactly with ==."""
    return [tuple(getattr(s, f) for f in _GPS_FIELDS) for s in samples]


def _two_pass_reference(path):
    """The pre-#40 double-open behaviour, hand-built: two FRESH GPMFSource chains over the same
    single-chapter file. The first is drained for GPS exactly as `read_gpmf`'s inline pass did
    (seek(0), walk the payload cursor to the end, naive = span-interpolated sample time); the
    second — a SECOND `OpenMP4Source` + GPMF parse of the same container, the cost #40 removed —
    is read for ACCL/GRAV/CORI exactly as `read_imu` did. Returns the same 7-tuple shape as
    `read_recording`."""
    gps_src = pacer.GPMFSource(path)
    durations = [gps_src.get_total_duration()]
    samples, spans, naive = [], [], []
    gps_src.seek(0)
    while not gps_src.is_end():
        a, b = gps_src.current_time_span()
        chunk = []
        gps_src.read_samples(lambda s, i, n, _c=chunk: _c.append((s, i, n)))
        for s, i, n in chunk:
            samples.append(s)
            spans.append((a, b))
            naive.append(a + (b - a) * (i / n if n else 0.0))
        gps_src.next()

    imu_src = pacer.GPMFSource(path)  # the second, fresh open the old read_imu performed
    accl, grav, cori = [], [], []
    imu_src.read_accl(lambda s: accl.append((s.time, s.x, s.y, s.z)))
    imu_src.read_grav(lambda s: grav.append((s.time, s.x, s.y, s.z)))
    imu_src.read_cori(lambda s: cori.append((s.time, s.w, s.x, s.y, s.z)))
    a = np.asarray(accl, float).reshape(-1, 4)
    g = np.asarray(grav, float).reshape(-1, 4)
    c = np.asarray(cori, float).reshape(-1, 5)
    return samples, spans, naive, durations, a, g, c


def test_read_recording_equals_two_fresh_chains_on_real_media():
    """read_recording's single shared chain reproduces the two-fresh-chains output EXACTLY:
    every GPS field of every sample, the payload spans, the naive interpolated times, the
    chapter durations, and all three IMU arrays. hero6 (GPS5 + ACCL only) and hero8 (GPS + all
    of ACCL/GRAV/CORI) together cover every stream the readers handle."""
    ran = []
    for name, has_grav_cori in (("hero6.mp4", False), ("hero8.mp4", True)):
        path = os.path.join(_SAMPLES, name)
        if not os.path.exists(path):
            print(f"  {name}: SKIP (sample missing — gpmf-parser submodule not checked out?)")
            continue
        got_samples, got_spans, got_naive, got_durations, got_a, got_g, got_c = (
            ingest.read_recording([path]))
        ref_samples, ref_spans, ref_naive, ref_durations, ref_a, ref_g, ref_c = (
            _two_pass_reference(path))

        # The sample clips genuinely exercise the readers: GPS and ACCL are non-empty on both.
        assert got_samples, f"{name}: expected GPS samples"
        assert got_a.size, f"{name}: expected ACCL samples"
        if has_grav_cori:  # hero8-era cameras add GRAV + CORI; their equivalence is non-trivial
            assert got_g.size and got_c.size, f"{name}: expected GRAV+CORI samples"

        # GPS pass: all fields of all samples, the spans and the naive times — exactly equal.
        assert _gps_rows(got_samples) == _gps_rows(ref_samples), f"{name}: GPS samples differ"
        assert got_spans == ref_spans, f"{name}: payload spans differ"
        assert got_naive == ref_naive, f"{name}: naive times differ"
        assert got_durations == ref_durations, f"{name}: durations differ"

        # IMU pass: all three streams exactly equal (shape and every element).
        for label, got, ref in (("ACCL", got_a, ref_a), ("GRAV", got_g, ref_g),
                                ("CORI", got_c, ref_c)):
            assert got.shape == ref.shape, f"{name}: {label} shape {got.shape} != {ref.shape}"
            assert np.array_equal(got, ref), f"{name}: {label} stream differs"

        ran.append(name)
        print(f"  {name}: gps={len(got_samples)} accl={got_a.shape[0]} "
              f"grav={got_g.shape[0]} cori={got_c.shape[0]} — single-pass == two-pass")
    assert ran, "no sample clips found — nothing was verified"
    print("test_read_recording_equals_two_fresh_chains_on_real_media OK")


class _StubSource(pacer.RawGPSSource):
    """A plain-Python RawGPSSource with a GPS payload cursor (seek/next/is_end walk `payloads`)
    and IMU readers that — like the C++ GPMFSource/SequentialGPSSource ones — walk their full
    streams INDEPENDENT of that cursor. That cursor-independence is exactly the contract
    `read_recording` relies on to run the IMU pass after the GPS pass on one shared source."""

    def __init__(self, payloads, accl, grav, cori, duration):
        super().__init__()
        self._payloads = payloads  # [(span_start, span_end, [GPSSample, ...]), ...]
        self._idx = 0
        self._accl = accl
        self._grav = grav
        self._cori = cori
        self._duration = duration
        self.seek_calls = []

    # --- the GPS payload cursor surface `_read_gps_over` drives ---
    def seek(self, target):
        self.seek_calls.append(target)
        self._idx = 0
        return 0

    def next(self):
        self._idx += 1

    def is_end(self):
        return self._idx >= len(self._payloads)

    def current_time_span(self):
        a, b, _samples = self._payloads[self._idx]
        return (a, b)

    def get_total_duration(self):
        return self._duration

    def read_samples(self, on_sample):
        _a, _b, samples = self._payloads[self._idx]
        for i, s in enumerate(samples):
            on_sample(s, i, len(samples))
        return 0

    # --- IMU readers: cursor-independent, mirroring the C++ read_accl/grav/cori contract ---
    def read_accl(self, on_sample):
        for s in self._accl:
            on_sample(s)

    def read_grav(self, on_sample):
        for s in self._grav:
            on_sample(s)

    def read_cori(self, on_sample):
        for s in self._cori:
            on_sample(s)


def test_imu_reads_unaffected_by_prior_gps_drain_on_stub():
    """Order-independence pin: run ingest's GPS helper over the stub (seek(0) + full payload
    walk, leaving the cursor at the end), then its IMU helper over the SAME exhausted stub —
    the IMU arrays must equal a read taken BEFORE the drain, element for element."""
    payloads = [
        (0.0, 1.0, [pacer.GPSSample(lat=50.0, lon=8.0, altitude=100.0, full_speed=20.0,
                                    ground_speed=19.5, timestamp_ms=1000),
                    pacer.GPSSample(lat=50.1, lon=8.1, altitude=101.0, full_speed=21.0,
                                    ground_speed=20.5, timestamp_ms=1500)]),
        (1.0, 2.0, [pacer.GPSSample(lat=50.2, lon=8.2, altitude=102.0, full_speed=22.0,
                                    ground_speed=21.5, timestamp_ms=2000)]),
    ]
    accl = [pacer.IMUSample(x=1.0, y=2.0, z=3.0, time=0.1),
            pacer.IMUSample(x=4.0, y=5.0, z=6.0, time=0.2)]
    grav = [pacer.IMUSample(x=0.0, y=0.0, z=9.8, time=0.05)]
    cori = [pacer.QuatSample(w=1.0, x=0.0, y=0.0, z=0.0, time=0.05)]
    stub = _StubSource(payloads, accl, grav, cori, duration=2.0)

    # Baseline IMU read BEFORE any GPS pass touched the source.
    a0, g0, c0 = ingest._read_imu_over(stub)
    assert a0.shape == (2, 4) and g0.shape == (1, 4) and c0.shape == (1, 5)
    assert a0[0].tolist() == [0.1, 1.0, 2.0, 3.0]  # (time, x, y, z) packing
    assert c0[0].tolist() == [0.05, 1.0, 0.0, 0.0, 0.0]  # (time, w, x, y, z) packing

    # The GPS drain: ingest's helper must seek(0) first, then walk EVERY payload to the end,
    # producing the per-sample spans and span-interpolated naive times.
    samples, spans, naive = ingest._read_gps_over(stub)
    assert stub.seek_calls == [0], stub.seek_calls
    assert stub.is_end(), "the GPS pass did not exhaust the payload cursor"
    assert _gps_rows(samples) == _gps_rows([s for _, _, ss in payloads for s in ss])
    assert spans == [(0.0, 1.0), (0.0, 1.0), (1.0, 2.0)], spans
    assert naive == [0.0, 0.5, 1.0], naive  # a + (b - a) * i/n within each payload

    # IMU read AFTER the drain, off the same exhausted stub: identical to the baseline. This is
    # the invariance read_recording's GPS-then-IMU ordering on ONE shared chain relies on.
    a1, g1, c1 = ingest._read_imu_over(stub)
    assert np.array_equal(a0, a1), "ACCL changed after the GPS drain"
    assert np.array_equal(g0, g1), "GRAV changed after the GPS drain"
    assert np.array_equal(c0, c1), "CORI changed after the GPS drain"
    print("test_imu_reads_unaffected_by_prior_gps_drain_on_stub OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nALL {len(tests)} INGEST EQUIVALENCE TESTS PASSED")
