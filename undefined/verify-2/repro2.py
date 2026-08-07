import glob, os, sys, tempfile, time
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen'); os.environ['PACER_NO_MEDIA'] = '1'
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from studio import library, prefs, theme
_tmp = tempfile.mkdtemp()
prefs._app_support_dir = lambda: _tmp
library._app_support_dir = lambda: _tmp
for name in ('critical', 'warning', 'information', 'question'):
    setattr(QMessageBox, name, staticmethod(lambda *a, **k: 0))
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ('', ''))
QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([], ''))
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: '')
app = QApplication([]); theme.register_fonts(); theme.apply_theme(app)
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

vid = w.view.video
print('compare_btn enabled:', vid.compare_btn.isEnabled(), 'checked:', vid.compare_btn.isChecked())
vid.compare_btn.click()
pump(120)
print('after click — checked:', vid.compare_btn.isChecked())
shot('02-compare')

# Dump pane pickers
for name, cell in (('A', getattr(vid, '_cell_a', None)), ('B', getattr(vid, '_cell_b', None))):
    if cell is None:
        print(name, 'cell missing'); continue
    print(f'pane {name}: role={cell.caption.text()!r} tooltip={cell.caption.toolTip()!r}')
    pk = cell.picker
    print(f'pane {name} picker current: {pk.currentText()!r} | first 3 items:',
          [pk.itemText(i) for i in range(min(3, pk.count()))],
          '| last item:', pk.itemText(pk.count()-1) if pk.count() else None)

# Legend text on the chart (plots) for cross-check
from PySide6.QtWidgets import QLabel
for lb in w.findChildren(QLabel):
    t = lb.text()
    if t and t.startswith('lap ') and not lb.isHidden():
        print('visible LABEL:', repr(t))

# Repoint pane A to the first lap (lap_id 0) via the picker to check "lap 0"
pk = vid._cell_a.picker
pk.setCurrentIndex(0)
pump(120)
print('after repoint — pane A picker:', repr(pk.currentText()))
shot('03-repointed-lap0')
for lb in w.findChildren(QLabel):
    t = lb.text()
    if t and t.startswith('lap ') and not lb.isHidden():
        print('visible LABEL after repoint:', repr(t))
