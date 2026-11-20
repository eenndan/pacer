"""Imported by the interpreter at start-up in every Python CTest registration, before the test file.

tests/CMakeLists.txt puts this directory first on each registration's PYTHONPATH. It only arms
tests/_hang_dump.py (a stack dump of every thread shortly before the CTest timeout); everything is
documented there. A process without `PACER_HANG_DUMP` — every child a test spawns — loads nothing.
Nothing here may raise into a test: a failure to arm costs the dump, never the run.
"""
import os

if os.environ.get("PACER_HANG_DUMP"):
    try:
        import importlib.util
        import sys

        _path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "_hang_dump.py")
        _spec = importlib.util.spec_from_file_location("_hang_dump", _path)
        if _spec is not None and _spec.loader is not None:
            _mod = importlib.util.module_from_spec(_spec)
            sys.modules["_hang_dump"] = _mod  # conftest's `import _hang_dump` then finds it armed
            _spec.loader.exec_module(_mod)
            _mod.arm_from_environment()
    except Exception as _exc:  # noqa: BLE001
        os.write(2, f"tests/_site/sitecustomize.py: hang dump not armed: {_exc!r}\n".encode())
