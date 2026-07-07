"""Layering-contract test (the load-bearing architecture invariant).

The rule (AGENTS.md + studio/README): only the four data/pipeline modules — `session`, `load`,
`ingest`, `tracks` — may `import pacer` (the C++ core). Every studio VIEW / controller / helper
stays **pacer-free** and goes through `Session`. That contract holds today with zero violations,
but nothing enforced it: an agent could `import pacer` into a view for convenience and every other
gate (build, ruff, the offscreen widget tests) would still pass — silently eroding the layering.

This test walks the source with `ast` (no import side-effects, no Qt, no pacer, no telemetry file,
sub-100 ms) and fails the moment the pacer-import set drifts from the allow-list. `studio/dev/`
tools are standalone scripts, not part of the app, and are intentionally out of scope. Run:
    python tests/test_layering.py
"""
import ast
import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STUDIO = os.path.join(_REPO, "studio")

# The ONLY top-level studio/*.py modules permitted to import the pacer core (the data/pipeline
# layer). If a deliberate new pipeline module joins them, add it here in the same PR.
ALLOWED = {"session", "load", "ingest", "tracks"}


def _imports_pacer(path: str) -> bool:
    """True if the module imports `pacer` (or a `pacer.` submodule) at any depth."""
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == "pacer" or a.name.startswith("pacer.") for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "pacer" or mod.startswith("pacer."):
                return True
    return False


def test_only_the_data_layer_imports_pacer():
    importers = {
        fn[:-3]
        for fn in os.listdir(_STUDIO)
        if fn.endswith(".py") and fn != "__init__.py"
        and _imports_pacer(os.path.join(_STUDIO, fn))
    }
    extra = importers - ALLOWED      # a view / controller / helper reached into pacer
    missing = ALLOWED - importers    # an allow-listed module no longer imports pacer
    assert not extra, (
        f"pacer-free contract broken: {sorted(extra)} import the pacer core but are NOT the "
        f"data/pipeline layer {sorted(ALLOWED)}. Keep views/controllers/helpers pacer-free (go "
        f"through Session), or — if this is a deliberate new pipeline module — add it to ALLOWED.")
    assert not missing, (
        f"allow-list drift: {sorted(missing)} no longer import pacer — drop it from ALLOWED so the "
        f"contract stays exact.")
    print(f"test_only_the_data_layer_imports_pacer OK — pacer imported by exactly {sorted(importers)}")


if __name__ == "__main__":
    test_only_the_data_layer_imports_pacer()
    print("\n1 layering test passed")
