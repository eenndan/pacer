"""pacer studio — a local PySide6 + pyqtgraph desktop app for race-telemetry analysis.

Greenfield UI on top of the existing C++ `pacer` core (via its nanobind bindings).
Single-language Python, optimized for fast iteration. Panels:
  * MapView   — track trace + draggable start/sector timing lines (local meters).
  * PlotsView — speed-vs-distance + lap-vs-best delta for the selected laps.
  * VideoView — the GoPro .mp4, synced both ways with the telemetry.
  * LapTable  — lap times / distances; selection drives the plots.

Run:  pixi run studio [GoPro.MP4 ...]   (or: python -m studio [files])
"""

# Canonical version + wordmark — the single source of truth for both (the About dialog reads
# these, the PyInstaller spec regex-reads __version__, and every user-facing title/label imports
# APP_NAME via `from . import APP_NAME`). Keep this a leaf: no submodule imports here, so
# `from . import APP_NAME` can never cycle.
# The version is DUPLICATED in two pyproject.toml files that cannot import this one (pyproject.toml
# names the .dmg; bindings/pacer/pyproject.toml is the bindings package). tests/test_version.py
# pins all three together — bump them in one commit.
#
# THE NAME CONVENTION — three forms, each with one job. tests/test_version.py enforces all three.
#   * FORMAL  "Pacer Studio" (APP_NAME): the product's identity. Use it for the macOS bundle
#     (packaging/pacer.spec's CFBundleName / CFBundleDisplayName, `Pacer Studio.app`, the
#     `Pacer-Studio-<v>.dmg`), the application and display names, every window and dialog title,
#     the About card, the landing page's product name and the header of every EXPORTED file,
#     because an export carries the product's identity outside the app. Always this constant,
#     never a retyped literal.
#   * SHORT   "Pacer", capitalised: the product in a SENTENCE ("Pacer never looks the weather
#     up"), the welcome screen's headline and the README's title.
#   * WORDMARK "pacer", lowercase: the share card's logotype, set as a graphic, and its lap-strip
#     twin's (share_lapstrip). Nowhere else.
# Lowercase "pacer" in a sentence is NOT a fourth form. It was the actual inconsistency (34
# user-visible sentences, fixed in U5). Identifiers are not names and keep their own spelling: the
# `pacer` package, "~/Library/Application Support/pacer", ".pacer.json" sidecars and the
# `app.pacer.studio` bundle id.
__version__ = "0.24.0"
APP_NAME = "Pacer Studio"


# ONE BLAS THREAD, FOR THE WHOLE PROCESS, BEFORE ANYTHING RUNS. numpy's OpenBLAS (pthreads) registers
# a fork handler that shuts its worker threads down in the PARENT before every fork(), and Python's
# subprocess forks on macOS (posix_spawn only with close_fds=False). A fork from one thread while
# another is inside a threaded BLAS call takes that call's workers away mid-flight, and the caller
# spins in OpenBLAS's exec_blas forever: the g-meter's (N, 2) @ (2, 2) in Session.load (threaded
# from N > 65536, the demo has 147 800) hung for good, 10 of 10 times, beside a thread running
# subprocess.run(["true"]) in a loop, and test_export_pass hung in CI exactly there while the
# export dialog's warm-up thread ran its ffmpeg probes. The app forks ffmpeg off the GUI thread
# (the warm-up probes, every export render) and loads on a worker. Pacer's arrays are a few
# hundred thousand rows at most, so one thread costs nothing measurable (the PR that added this
# timed it); a fork can then never strand a worker. tests/test_export_thread_safety.py holds it.
def _single_threaded_blas() -> int | None:
    """Set the BLAS pool numpy loaded to one thread; return its thread count after, or None when
    this numpy's BLAS exposes no OpenBLAS setter (a different BLAS: the test then says so)."""
    import ctypes

    import numpy  # noqa: F401 — loads the BLAS whose symbols are looked up below

    try:
        everything = ctypes.CDLL(None)   # dlsym over every image the process has loaded
    except OSError:
        return None
    # numpy's PyPI wheels prefix and suffix OpenBLAS's symbols (scipy-openblas, ILP64); a plain
    # OpenBLAS build has the bare names.
    for prefix, suffix in (("scipy_openblas", "64_"), ("scipy_openblas", ""),
                           ("openblas", "64_"), ("openblas", "")):
        setter = getattr(everything, f"{prefix}_set_num_threads{suffix}", None)
        getter = getattr(everything, f"{prefix}_get_num_threads{suffix}", None)
        if setter is not None and getter is not None:
            setter.argtypes, getter.restype = [ctypes.c_int], ctypes.c_int
            setter(1)
            return int(getter())
    return None


BLAS_THREADS = _single_threaded_blas()
