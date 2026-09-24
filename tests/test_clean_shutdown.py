"""A clean exit: nothing the bindings or the smoke build is still alive, or uncollectable, when
Python shuts down.

WHY THIS FILE EXISTS. `pixi run smoke`, the one end-to-end check a reader without footage can run,
ended with 85 lines of `nanobind: leaked …` AFTER `SMOKE OK`, closing on nanobind's "this is likely
caused by a reference counting issue in the binding code" (board review 2026-09-23, CEO-3).
Measured on 2026-09-24, it was two separate things, and neither was a refcount bug:

  * IMPORT-TIME CYCLES. A bare `import pacer` that built nothing ended with 3 instances, 4 types
    and 39 functions "leaked". litgen's auto-generated struct constructors declared INSTANCES as
    their defaults (`Segment(first=Point(), second=Point())`, `Sectors(start_line=Segment())`).
    nanobind keeps a default alive inside its function, which closes a type -> __init__ ->
    instance -> type cycle Python cannot collect (nanobind's docs: refleaks.rst, "Default
    arguments"). Every process that imported the bindings printed it at exit: the app, every
    test, every probe. Fixed in bindings/pacer/generate-bindings.py: those parameters are now
    `X | None = None`, and the default is built in C++ on each call.
  * THE SMOKE'S OWN WINDOW. Its module-level StudioWindow was still alive at exit, holding its
    Session's Laps and CoordinateSystem. studio/dev/_smoke.py now closes and deletes the window
    first, and asserts that its Session is gone.

Both checks run the real thing in a child process: nanobind prints its report from the
interpreter's last step, after any in-process check could look. And the detector is shown a leak
planted on purpose, so a silent child proves something.
"""
import os
import subprocess
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPORT = "nanobind: leaked"
# Unbuffered, so a child's stdout and stderr interleave in the order they were written: "the last
# line" then means what it means on a terminal.
_ENV = dict(os.environ, PYTHONUNBUFFERED="1")

# Every constructor the generator changed, called every way the repo calls it, with its values.
_CONSTRUCT = """
import pacer
p = pacer.Point(); p.x, p.y = 3.0, 4.0
seg = pacer.Segment(p, p)
assert (seg.first.x, pacer.Segment().first.x, pacer.Segment(first=p).second.x) == (3.0, 0.0, 0.0)
sec = pacer.Sectors(start_line=seg, sector_lines=[seg])
assert sec.start_line.first.y == 4.0 and len(sec.sector_lines) == 1
assert pacer.Sectors(start_line=None).start_line.first.x == 0.0
laps = pacer.Laps(sectors=sec)
assert laps.sectors.start_line.first.x == 3.0 and pacer.Laps().point_count() == 0
assert pacer.PointInTime_GPSSample().time == 0.0 and list(pacer.Lap().cum_distances) == []
print("constructed")
"""
# One reference leaked on purpose: the Point can never be freed, so nanobind must say so.
_PLANTED_LEAK = "import ctypes, pacer; ctypes.pythonapi.Py_IncRef(ctypes.py_object(pacer.Point()))"


def _python(*args, timeout=120):
    return subprocess.run([sys.executable, *args], cwd=_REPO, env=_ENV, capture_output=True,
                          text=True, timeout=timeout)


def test_importing_and_using_the_bindings_exits_silently():
    for label, code in (("a bare import", "import pacer"), ("the constructors", _CONSTRUCT)):
        p = _python("-c", code)
        assert p.returncode == 0, f"{label}: exit {p.returncode}\n{p.stdout}{p.stderr}"
        assert _REPORT not in p.stderr, (
            f"{label} ends with nanobind's leak report. A default argument that is an instance of a "
            f"bound type is the usual cause (see this file's docstring):\n{p.stderr}")
    p = _python("-c", _PLANTED_LEAK)
    assert "nanobind: leaked 1 instances!" in p.stderr and '"pacer._pacer.Point"' in p.stderr, (
        f"a Point leaked on purpose went unreported, so a silent exit proves nothing:\n{p.stderr}")
    print("test_importing_and_using_the_bindings_exits_silently OK (import + constructors silent; "
          "a planted leak is reported)")


def test_the_smoke_run_ends_with_smoke_ok():
    """What `pixi run smoke` prints, in order: its last line is its verdict."""
    p = subprocess.run([sys.executable, "-m", "studio.dev._smoke", "--no-video"], cwd=_REPO,
                       env=_ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                       timeout=300)
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    assert p.returncode == 0 and lines, f"the smoke failed (exit {p.returncode}):\n{p.stdout}"
    tail = "\n".join(lines[lines.index("SMOKE OK (no-video)"):] if "SMOKE OK (no-video)" in lines
                     else lines[-10:])
    assert lines[-1] == "SMOKE OK (no-video)", (
        f"the smoke's last line is {lines[-1]!r}, not its verdict. What follows SMOKE OK:\n{tail}")
    assert _REPORT not in p.stdout, f"the smoke prints nanobind's leak report:\n{tail}"
    print(f"test_the_smoke_run_ends_with_smoke_ok OK ({len(lines)} lines, the last is its verdict)")


if __name__ == "__main__":
    for fn in (test_importing_and_using_the_bindings_exits_silently,
               test_the_smoke_run_ends_with_smoke_ok):
        fn()
        print(f"ok  {fn.__name__}")
    print("\n2 clean-shutdown tests passed")
