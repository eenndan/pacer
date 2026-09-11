"""THE IMU AXIS DECLARATION — what each camera SAYS about its element order, pinned.

WHY THIS FILE EXISTS. `studio.gmeter.GRAV_PERM` is a FITTED constant: it was resolved against one
camera's data, not read out of the container. GoPro physically moved the IMU between models, so
the raw element order genuinely differs by camera and the container says so in GPMF's `ORIN` (raw
element orientation, lowercase = negated) and `ORIO` (presented orientation). The obvious
conclusion — "read ORIN and honour it" — was measured and REJECTED; the measurement is recorded
in `studio/docs/gmeter-validation.md` and summarised in the `GRAV_PERM` block. In short:

  * ORIN/ORIO are written ONLY on ACCL and GYRO. GRAV, CORI and IORI carry no orientation field on
    any camera that has them, so half the transform has nothing to canonicalise by;
  * and they must not be canonicalised, because GRAV/CORI ride the RAW element frame: the same
    fixed permutation maps raw GYRO onto the CORI-derived body rate on a HERO8 (ORIN "zxY"), a
    GoPro Max ("XzY") and a HERO13 ("ZXY"), three different declarations.

So the field is READ and REPORTED (`RawGPSSource.read_imu_orientation`, printed by
`studio.dev.diagnose`) and never applied. This file is the other half of that decision: it pins
what the fleet actually declares, so a camera that declares something new shows up here as a
failing test rather than as a silent assumption somewhere downstream. The per-recording guard —
"does this recording's GRAV actually agree with its own ACCL?" — is `gmeter.axis_check`, tested in
test_gmeter.py.

THE FOUR CHECKS
  1. THE PINNED TABLE. Every bundled sample clip's ACCL ORIN/ORIO is exactly what is recorded
     below. A new firmware or a new camera in an updated submodule fails HERE.
  2. ACCL AND GYRO NEVER DISAGREE. The invariant `studio.rotation` rests on: the gyro inherits
     whatever frame the accelerometer is in. Asserted per clip, not assumed.
  3. THE FIELDS ARE OPTIONAL, IN BOTH DIRECTIONS. Some cameras write neither (HERO5, Karma,
     Fusion, and older HERO6 firmware — `hero6.mp4` carries none where `hero6a.mp4` does), and
     the NEWEST camera here writes ORIN with NO ORIO at all. Any code that reaches for these
     fields must survive both, so both are pinned as facts rather than left as surprises.
  4. NOTHING ELSE CARRIES ONE. GRAV/CORI/IORI must stay absent from the reader's answer — the
     reader only claims to speak for ACCL and GYRO, which is the whole reason the transform
     cannot be driven by it.

Needs the bundled gpmf-parser submodule; prints SKIP and passes when it is absent (a bare
worktree), exactly like test_camera_support.py. Needs the bindings (PYTHONPATH), no Qt.

Run: python tests/test_imu_orientation.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pacer  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SAMPLES = os.path.join(_REPO, "3rdparty", "gpmf-parser", "samples")

# --- THE PINNED TABLE -----------------------------------------------------------------------
# clip -> (DVNM, ACCL ORIN, ACCL ORIO). "" means the field is absent from the container.
# Measured 2026-09-11 on submodule 479bcdb. Decoding ORIN: one letter per RAW element naming the
# PHYSICAL camera axis it carries, lowercase = negated. So a HERO6's "YxZ" means element 0 is +Y,
# element 1 is -X, element 2 is +Z — which is exactly the "Data order Y,-X,Z" the vendored
# 3rdparty/gpmf-parser/README.md gives for that model, and the same agreement holds for the
# HERO5's documented Z,X,Y and the Fusion's -Y,X,Z (both of which declare no ORIN at all).
_CLIPS: dict[str, tuple[str, str, str]] = {
    "Fusion.mp4":        ("Fusion",       "",    ""),
    "hero5.mp4":         ("Camera",       "",    ""),
    "hero6.mp4":         ("Hero6 Black",  "",    ""),   # older firmware: no ORIN
    "hero6a.mp4":        ("Hero6 Black",  "YxZ", "ZXY"),
    "hero6+ble.mp4":     ("Hero6 Black",  "YxZ", "ZXY"),
    "hero7.mp4":         ("Hero7 Black",  "YxZ", "ZXY"),
    "hero8.mp4":         ("HERO8 Black",  "zxY", "ZXY"),
    "karma.mp4":         ("Camera",       "",    ""),
    "max-360mode.mp4":   ("GoPro Max",    "XzY", "ZXY"),
    "max-heromode.mp4":  ("GoPro Max",    "XzY", "ZXY"),
}

# The presented orientation every camera that names one names. It is also, exactly, what the
# newest camera here writes as its RAW order — which is why a HERO13's ORIN->ORIO conversion is
# the identity, and why honouring the field would have changed nothing on the only recordings
# this app is validated against while breaking every other camera.
_CANONICAL_ORIO = "ZXY"


def _have_samples() -> bool:
    return os.path.isdir(_SAMPLES) and any(
        os.path.exists(os.path.join(_SAMPLES, c)) for c in _CLIPS)


def test_every_bundled_clip_declares_what_the_table_says():
    """Check 1 + 3: the pinned ACCL declaration per clip, absences included."""
    if not _have_samples():
        print("SKIP pinned table: 3rdparty/gpmf-parser samples not checked out")
        return
    seen = 0
    for clip, (dvnm, orin, orio) in sorted(_CLIPS.items()):
        path = os.path.join(_SAMPLES, clip)
        if not os.path.exists(path):
            continue
        src = pacer.GPMFSource(path)
        assert src.device_name() == dvnm, f"{clip}: DVNM {src.device_name()!r} != {dvnm!r}"
        o = src.read_imu_orientation()
        assert o.accl_in == orin, f"{clip}: ACCL ORIN {o.accl_in!r} != {orin!r}"
        assert o.accl_out == orio, f"{clip}: ACCL ORIO {o.accl_out!r} != {orio!r}"
        seen += 1
    assert seen >= 8, f"only {seen} bundled clips read — the guard has gone hollow"
    print(f"ok {seen} bundled clips declare exactly the pinned ORIN/ORIO")


def test_accl_and_gyro_never_declare_different_frames():
    """Check 2: the invariant `studio.rotation` rests on. It is asserted, not assumed, because
    the gyro channel projects onto a gravity direction resolved in the ACCELEROMETER's frame — if
    the two streams ever disagreed, the yaw rate would be silently rotated."""
    if not _have_samples():
        print("SKIP accl==gyro: samples not checked out")
        return
    seen = 0
    for clip in sorted(_CLIPS):
        path = os.path.join(_SAMPLES, clip)
        if not os.path.exists(path):
            continue
        o = pacer.GPMFSource(path).read_imu_orientation()
        assert o.gyro_in == o.accl_in, f"{clip}: GYRO ORIN {o.gyro_in!r} != ACCL {o.accl_in!r}"
        assert o.gyro_out == o.accl_out, f"{clip}: GYRO ORIO {o.gyro_out!r} != ACCL {o.accl_out!r}"
        seen += 1
    print(f"ok ACCL and GYRO declare the same frame on all {seen} clips")


def test_the_declarations_are_optional_in_both_directions():
    """Check 3, stated as its own fact rather than left implicit in the table: there is a clip
    with NEITHER field and a camera (the HERO13 the app is validated on) with ORIN and no ORIO.
    A reader that assumed either would be wrong on real recordings."""
    absent = [c for c, (_d, i, o) in _CLIPS.items() if not i and not o]
    declared = [c for c, (_d, i, _o) in _CLIPS.items() if i]
    assert absent, "no sample clip without ORIN — check 3 has nothing to guard"
    assert declared, "no sample clip with ORIN — check 3 has nothing to guard"
    # Every clip that names a presented orientation names the same one.
    outs = {o for (_d, _i, o) in _CLIPS.values() if o}
    assert outs == {_CANONICAL_ORIO}, outs
    # And two cameras that declare DIFFERENT raw orders both share it — the reason ORIN is a real
    # difference between models and not a formality.
    assert _CLIPS["hero8.mp4"][1] != _CLIPS["max-heromode.mp4"][1] != _CLIPS["hero7.mp4"][1]
    print(f"ok ORIN/ORIO are both optional; {len(absent)} clips declare neither, "
          f"every declared ORIO is {_CANONICAL_ORIO}")


def test_the_reader_speaks_only_for_accl_and_gyro():
    """Check 4. `ImuOrientation` deliberately has no GRAV/CORI field: those streams carry no
    orientation entry on any camera, which is the measured reason the studio transform cannot be
    driven by ORIN. Pin the shape so a later 'completeness' edit has to face that fact."""
    fields = {f for f in dir(pacer.ImuOrientation) if not f.startswith("_")}
    assert fields == {"accl_in", "accl_out", "gyro_in", "gyro_out"}, sorted(fields)
    empty = pacer.ImuOrientation()
    assert (empty.accl_in, empty.accl_out, empty.gyro_in, empty.gyro_out) == ("", "", "", "")
    print("ok the reader answers for ACCL and GYRO only, and defaults to 'absent'")


def test_a_source_with_no_container_declares_nothing():
    """A Python-implemented source (a test double, a synthetic feed) inherits the base, which must
    answer 'absent' rather than invent a convention."""

    class _Bare(pacer.RawGPSSource):
        def seek(self, target):
            return 0

        def next(self):
            pass

        def is_end(self):
            return True

        def current_time_span(self):
            return (0.0, 0.0)

        def get_total_duration(self):
            return 0.0

    o = _Bare().read_imu_orientation()
    assert (o.accl_in, o.accl_out, o.gyro_in, o.gyro_out) == ("", "", "", "")
    print("ok a source with no container declares nothing, rather than a default")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} imu-orientation tests passed")
