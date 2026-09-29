"""The themed app never asks Qt for a font family this machine does not have (HEALTH-5).

The first lookup of a missing family makes Qt scan every installed font's aliases on the GUI thread
and log "Populating font family aliases took N ms. Replace uses of missing font family …". The mono
stack led with "SF Mono", which neither this Mac nor the CI runner has, so every launch paid that
scan at the first paint of the timecode readout: 53-64 ms in the owner's log, 5 launches of 5,
2026-09-25. `theme.register_fonts` now narrows the stack once to the installed faces, keeping their
order (Menlo here, pixel for pixel what painted before).

The UI stack paid it too, one glyph later (APP-POLISH-2). It leads with the bundled Inter, which
looked free, but a glyph Inter LACKS makes Qt fall back per character down the rest of the list,
and the next name was "-apple-system". Painting ▸ (the menu-path connector: "File ▸ Save as
track…"), ✕, ⟳ or ⟲ in `ui_font` cost the same scan, 62-71 ms. That stack is narrowed the same way
now, and the grabbed pixels of a label holding all four are the same as the un-narrowed stack's.

The scan runs once per PROCESS, so each case runs in a fresh interpreter: the themed mono surfaces
(the timecode QLabel#Readout, a Shortcuts KeyCap, the Inter-absent `_mono_stack_font`) are polished
first and every Qt message is kept, then one label in the UI face. Two planted cases skip a
narrowing: "mono" leads the mono stack with a name no machine has, "ui" puts the stated UI
preference back. Each must produce the warning at its own surface, or this test could not see the
defect it guards.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# One label that carries every glyph measured to walk past Inter (the probe's combined string).
_UI_TEXT = "File ▸ Save as track… ✕ ⟳ ⟲"

_CHILD = r"""
import hashlib, os, sys
sys.path.insert(0, {root!r})
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtGui import QFontDatabase, QFontInfo
from PySide6.QtWidgets import QApplication, QLabel
messages = []
qInstallMessageHandler(lambda mode, ctx, text: messages.append(text))
app = QApplication([])
from studio import theme
from studio.help_dialog import _key_cap
real_families = QFontDatabase.families
planted = {planted!r}
if planted == "mono":
    # The defect: the stacks are not narrowed, and the mono one leads with a family nobody has.
    theme._MONO_PREFERENCE = ("Pacer No Such Mono",) + theme._MONO_PREFERENCE
    QFontDatabase.families = lambda *a, **k: []
theme.register_fonts()
if planted == "ui":
    # The defect: the UI stack keeps its stated preference, missing names and all.
    theme.UI_FAMILIES = theme._UI_PREFERENCE
    theme.UI_STACK = ",".join(f'"{{f}}"' for f in theme.UI_FAMILIES)
theme.apply_theme(app)

def alias():
    return [m for m in messages if "font family alias" in m.lower()]

readout = QLabel("1:23:45.678")
readout.setObjectName("Readout")
for w in (readout, _key_cap("⌘⇧S")):
    w.ensurePolished()
    w.grab()
QFontInfo(theme._mono_stack_font(theme.CAPTION, theme.W_REGULAR)).family()
mono_alias = alias()
ui = QLabel({text!r})
ui.setFont(theme.ui_font(theme.BODY))
ui.ensurePolished()
ui.resize(ui.sizeHint())
img = ui.grab().toImage()
ui_alias = alias()[len(mono_alias):]
installed = set(real_families())
print("FAMILIES", "|".join(theme.MONO_FAMILIES))
print("PAINTS", QFontInfo(readout.font()).family())
print("ALIAS", len(mono_alias), mono_alias[:1])
print("UIFAM", "|".join(theme.UI_FAMILIES))
print("UIMISSING", "|".join(f for f in theme.UI_FAMILIES if f not in installed))
print("UIALIAS", len(ui_alias), ui_alias[:1])
print("PIX", hashlib.md5(bytes(img.constBits())).hexdigest(), img.width(), img.height())
"""

_RUNS: dict = {}


def _run(planted: str) -> dict:
    """One fresh interpreter per plant ("" = the shipped theme), cached: the scan is per process."""
    if planted not in _RUNS:
        code = _CHILD.format(root=ROOT, planted=planted, text=_UI_TEXT)
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              timeout=120, cwd=ROOT)
        assert proc.returncode == 0, f"the probe process failed:\n{proc.stdout}\n{proc.stderr}"
        out = {}
        for line in proc.stdout.splitlines():
            key, _, value = line.partition(" ")
            out[key] = value
        _RUNS[planted] = out
    return _RUNS[planted]


def test_the_themed_mono_surfaces_ask_for_no_missing_family():
    got = _run("")
    assert got["ALIAS"].startswith("0 "), (
        f"theming asked Qt for a missing font family, so every launch pays its alias scan: "
        f"{got['ALIAS']} (mono stack {got['FAMILIES']!r})")
    families = got["FAMILIES"].split("|")
    assert families and "monospace" not in families, (
        f"the stack was not narrowed to installed faces: {families}")
    assert got["PAINTS"] == families[0], (
        f"the timecode paints {got['PAINTS']!r}, not the stack's first face {families[0]!r}")
    print(f"ok no alias scan: mono stack {families}, the timecode paints {got['PAINTS']}")


def test_the_probe_sees_the_scan_when_a_missing_family_leads():
    got = _run("mono")
    assert not got["ALIAS"].startswith("0 "), (
        "a stack led by a family no machine has produced no alias warning — this file can no "
        f"longer see the cost it guards against: {got}")
    print(f"ok planted control: {got['ALIAS']}")


def test_a_glyph_inter_lacks_asks_for_no_missing_family():
    """▸ ✕ ⟳ ⟲ fall back past Inter per character; the rest of the UI stack must all exist."""
    got = _run("")
    assert got["UIALIAS"].startswith("0 "), (
        f"painting {_UI_TEXT!r} in the UI face asked Qt for a missing font family, so the first "
        f"such paint pays the alias scan: {got['UIALIAS']} (UI stack {got['UIFAM']!r})")
    families = got["UIFAM"].split("|")
    assert families[0] == "Inter", f"the UI stack lost the bundled Inter: {families}"
    assert not got.get("UIMISSING"), (
        f"the UI stack names faces this machine does not have: {got['UIMISSING']!r} of {families}")
    print(f"ok no alias scan for {_UI_TEXT!r}: UI stack {families}")


def test_the_probe_sees_the_scan_when_the_ui_stack_is_not_narrowed():
    got = _run("ui")
    assert got["ALIAS"].startswith("0 ") and not got["UIALIAS"].startswith("0 "), (
        f"the stated UI preference painted {_UI_TEXT!r} without an alias scan at that label — "
        f"this file can no longer see the cost it guards against (did Inter gain the glyphs?): {got}")
    print(f"ok planted UI control: {got['UIALIAS']}")


def test_narrowing_the_ui_stack_changes_no_pixel():
    """The dropped names were never painted from: Qt skipped them to the same fallback face."""
    shipped, planted = _run("")["PIX"], _run("ui")["PIX"]
    assert shipped == planted, (
        f"{_UI_TEXT!r} in the UI face grabs {shipped} narrowed and {planted} with the stated "
        "preference: narrowing changed what paints")
    print(f"ok identical pixels, narrowed or not: {shipped}")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"FAIL {t.__name__}: {exc}")
    if failed:
        print(f"\n{failed}/{len(tests)} font-stack tests FAILED")
        sys.exit(1)
    print(f"\nALL {len(tests)} font-stack tests passed")
