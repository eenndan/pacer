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
OUTDIR = '/Users/daniil/Documents/Github/pacer/undefined/verify-1'
def pump(n=40):
    for _ in range(n): app.processEvents()
def shot(name):
    pump(); w.grab().save(os.path.join(OUTDIR, name + '.png')); print('shot', name)
pump(60)

plots = w.view.plots
def dr():
    (dmin, dmax) = plots.p_delta.getViewBox().viewRange()[1]
    return (round(dmin, 3), round(dmax, 3))

# --- Case A: default state (best lap on charts), toggle Ideal lap ---
print('A: delta y-range before ideal:', dr(), 'ideal_min:', plots._ideal_min)
shot('a1-default-before')
plots.ideal_btn.click()
pump(60)
print('A: delta y-range after ideal :', dr(), 'ideal_min:', plots._ideal_min)
shot('a2-default-ideal-on')

# What would the natural fit be without the clamp? Toggle off, redraw ideal manually is complex;
# instead: temporarily re-run autoRange to see the honest fit with the ideal curve present.
plots.p_delta.enableAutoRange()
plots.p_delta.autoRange()
pump(20)
print('A: natural autoRange fit (ideal on, no clamp):', dr())
plots.p_delta.disableAutoRange()
shot('a3-default-ideal-natural-fit')

# --- Case B: two close laps overlaid + ideal ---
plots.ideal_btn.click(); pump(30)   # ideal off again
sess = plots.session
best = sess.best_lap_id()
ids = sess.valid_lap_ids()
times = sorted(((sess.lap_time(l), l) for l in ids))
print('best lap id:', best, 'best time:', times[0])
second = times[1][1]  # 2nd fastest = a close lap
print('2nd fastest:', times[1])
plots.set_laps([best, second])
pump(30)
print('B: two-lap y-range before ideal:', dr())
shot('b1-2lap-before')
plots.ideal_btn.click(); pump(60)
print('B: two-lap y-range after ideal :', dr(), 'ideal_min:', plots._ideal_min)
shot('b2-2lap-ideal-on')

# --- Case C: the clamp's INTENDED case — a much slower lap dominating the range ---
plots.ideal_btn.click(); pump(30)  # off
slow = times[-1][1]
print('slowest lap:', times[-1])
plots.set_laps([best, slow]); pump(30)
print('C: slow-lap y-range before ideal:', dr())
shot('c1-slowlap-before')
plots.ideal_btn.click(); pump(60)
(dmin, dmax) = plots.p_delta.getViewBox().viewRange()[1]
imin = plots._ideal_min
frac = (-imin) / (dmax - dmin) if imin is not None else None
print('C: slow-lap y-range after ideal :', dr(), 'ideal_min:', imin,
      'visible frac of span below zero:', round(frac, 4) if frac else None,
      '(promised >= 0.18)')
shot('c2-slowlap-ideal-on')

