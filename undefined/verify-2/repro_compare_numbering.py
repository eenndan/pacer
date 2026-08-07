import glob, os, sys, tempfile, time
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen'); os.environ['PACER_NO_MEDIA'] = '1'
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from studio import library, prefs, theme
_tmp = tempfile.mkdtemp()                    # NEVER touch the user's real prefs/library
prefs._app_support_dir = lambda: _tmp
library._app_support_dir = lambda: _tmp
for name in ('critical', 'warning', 'information', 'question'):
    setattr(QMessageBox, name, staticmethod(lambda *a, **k: 0))
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ('', ''))
QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([], ''))
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: '')
app = QApplication([]); theme.register_fonts(); theme.apply_theme(app)  # BEFORE any widget!
from studio.app import StudioWindow
w = StudioWindow(sorted(glob.glob(os.path.expanduser('~/Desktop/D24/GX0?0060.MP4'))))
w.resize(1440, 900); w.show()
t0 = time.time()
while w.view is None and time.time() - t0 < 120: app.processEvents(); time.sleep(0.02)
assert w.view is not None, 'load timed out'
OUTDIR = '/Users/daniil/Documents/Github/pacer/undefined/verify-2'
def pump(n=40):
    for _ in range(n): app.processEvents()
def shot(name):
    pump(); w.grab().save(os.path.join(OUTDIR, name + '.png')); print('shot', name)
pump(60)

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

# --- enter compare mode via keyboard C ---
QTest.keyClick(w, Qt.Key_C)
pump(80)
shot('01-compare-entered')

# Inspect the pane captions / picker labels programmatically too
ctrl = w.view
vid = ctrl.video if hasattr(ctrl, 'video') else None
# StudioWindow.view — find the compare controller / video view
import studio.app as appmod
print('view type:', type(w.view))

# Dump every QComboBox visible-ish text and QLabel texts containing "lap"
from PySide6.QtWidgets import QComboBox, QLabel
for cb in w.findChildren(QComboBox):
    if cb.count() and 'lap' in (cb.currentText() or '').lower():
        print('COMBO current:', repr(cb.currentText()), '| first items:',
              [cb.itemText(i) for i in range(min(4, cb.count()))],
              '| hidden:', cb.isHidden())
for lb in w.findChildren(QLabel):
    t = lb.text()
    if t and 'lap' in t.lower() and len(t) < 80:
        print('LABEL:', repr(t), '| hidden:', lb.isHidden())
