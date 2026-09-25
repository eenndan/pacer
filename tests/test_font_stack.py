"""The themed app never asks Qt for a font family this machine does not have (HEALTH-5).

The first lookup of a missing family makes Qt scan every installed font's aliases on the GUI thread
and log "Populating font family aliases took N ms. Replace uses of missing font family …". The mono
stack led with "SF Mono", which neither this Mac nor the CI runner has, so every launch paid that
scan at the first paint of the timecode readout: 53-64 ms in the owner's log, 5 launches of 5,
2026-09-25. `theme.register_fonts` now narrows the stack once to the installed faces, keeping their
order (Menlo here, pixel for pixel what painted before).

The scan runs once per PROCESS, so each case runs in a fresh interpreter: the themed mono surfaces
(the timecode QLabel#Readout, a Shortcuts KeyCap, the Inter-absent `_mono_stack_font`) are polished
and every Qt message is kept. The planted case skips the narrowing and leads the stack with a name
no machine has; it must produce the warning, or this test could not see the defect it guards.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

_CHILD = r"""
import os, sys
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
if {planted!r}:
    # The defect: the stack is not narrowed, and it leads with a family nobody has.
    theme._MONO_PREFERENCE = ("Pacer No Such Mono",) + theme._MONO_PREFERENCE
    QFontDatabase.families = lambda *a, **k: []
theme.register_fonts()
theme.apply_theme(app)
readout = QLabel("1:23:45.678")
readout.setObjectName("Readout")
for w in (readout, _key_cap("⌘⇧S")):
    w.ensurePolished()
    w.grab()
QFontInfo(theme._mono_stack_font(theme.CAPTION, theme.W_REGULAR)).family()
alias = [m for m in messages if "font family alias" in m.lower()]
print("FAMILIES", "|".join(theme.MONO_FAMILIES))
print("PAINTS", QFontInfo(readout.font()).family())
print("ALIAS", len(alias), alias[:1])
"""


def _run(planted: bool) -> dict:
    proc = subprocess.run([sys.executable, "-c", _CHILD.format(root=ROOT, planted=planted)],
                          capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert proc.returncode == 0, f"the probe process failed:\n{proc.stdout}\n{proc.stderr}"
    out = {}
    for line in proc.stdout.splitlines():
        key, _, value = line.partition(" ")
        out[key] = value
    return out


def test_the_themed_mono_surfaces_ask_for_no_missing_family():
    got = _run(planted=False)
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
    got = _run(planted=True)
    assert not got["ALIAS"].startswith("0 "), (
        "a stack led by a family no machine has produced no alias warning — this file can no "
        f"longer see the cost it guards against: {got}")
    print(f"ok planted control: {got['ALIAS']}")


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
