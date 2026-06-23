"""PyInstaller RUNTIME hook: point pacer's video export at the ffmpeg/ffprobe binaries bundled
inside the .app.

A frozen macOS .app launched from Finder inherits NO useful PATH, so studio.export_video's
`shutil.which("ffmpeg")` would fail and the "Export overlay video" feature would be dead. pacer.spec
bundles ffmpeg + ffprobe at the bundle root (sys._MEIPASS); this hook — which PyInstaller runs
BEFORE any app code imports — exports PACER_FFMPEG / PACER_FFPROBE so export_video._resolve_binary
picks them up at import time.

We only SET the vars if not already present (a user/dev override on PATH still wins) and only when
the bundled binary actually exists, so a spec built without ffmpeg degrades to the PATH lookup
rather than pointing at a missing file.
"""

import os
import sys

_base = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
for _name, _var in (("ffmpeg", "PACER_FFMPEG"), ("ffprobe", "PACER_FFPROBE")):
    if os.environ.get(_var):
        continue
    _cand = os.path.join(_base, _name)
    if os.path.isfile(_cand):
        os.environ[_var] = _cand
