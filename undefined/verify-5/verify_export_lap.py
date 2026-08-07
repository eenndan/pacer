import glob, os, sys, tempfile, time
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen'); os.environ['PACER_NO_MEDIA'] = '1'
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from studio import library, prefs, theme
_tmp = tempfile.mkdtemp()                    # NEVER touch the user's real prefs/library
prefs._app_support_dir = lambda: _tmp
library._app_support_dir = lambda: _tmp
for name in ('critical', 'warning', 'information', 'question'):
    setattr(QMessageBox, name, staticmethod(lambda *a, **k: 0))

OUTDIR = '/Users/daniil/Documents/Github/pacer/undefined/verify-5'
LOG = []
def log(*a):
    line = ' '.join(str(x) for x in a)
    LOG.append(line); print(line, flush=True)

# --- Recorder for save dialogs: log caption + suggested filename, then cancel ---
_saves = []
def _rec_save(parent, title='', default='', filt='', *a, **k):
    _saves.append((title, default, filt))
    log(f'SAVE-DIALOG  title={title!r}  suggested={os.path.basename(default)!r}')
    return ('', '')
QFileDialog.getSaveFileName = staticmethod(_rec_save)
QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([], ''))
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: '')

app = QApplication([]); theme.register_fonts(); theme.apply_theme(app)  # BEFORE any widget!
from studio.app import StudioWindow
w = StudioWindow(sorted(glob.glob(os.path.expanduser('~/Desktop/D24/GX0?0060.MP4'))))
w.resize(1440, 900); w.show()
t0 = time.time()
while w.view is None and time.time() - t0 < 120: app.processEvents(); time.sleep(0.02)
assert w.view is not None, 'load timed out'
def pump(n=40):
    for _ in range(n): app.processEvents()
def shot(name, widget=None):
    pump(); (widget or w).grab().save(os.path.join(OUTDIR, name + '.png')); print('shot', name, flush=True)
pump(60)

from studio._signal import lap_label, fmt_time

best = w.session.best_lap_id()
log(f'BEST-LAP  internal_id={best}  app_displays_as=lap {lap_label(best)}  '
    f'time={fmt_time(w.session.lap_time(best))}')
shot('01_app_after_load')

# --- 1. File > Export > Lap channels (CSV) ---
w._export_channels_csv()
pump()

# --- 2. Export overlay video: capture the options dialog, accept it, capture the save dialog ---
from PySide6.QtWidgets import QDialog, QLabel
_dlg_seen = []
_orig_exec = QDialog.exec
def _capture_exec(self):
    labels = [l.text() for l in self.findChildren(QLabel)]
    _dlg_seen.append((self.windowTitle(), labels))
    log(f'OPTIONS-DIALOG  title={self.windowTitle()!r}')
    for t in labels:
        log(f'  label: {t!r}')
    self.show(); pump()
    self.grab().save(os.path.join(OUTDIR, '02_overlay_options_dialog.png'))
    print('shot 02_overlay_options_dialog', flush=True)
    self.hide()
    return QDialog.Accepted    # accept -> flow proceeds to the save dialog, which cancels
QDialog.exec = _capture_exec
w._export_overlay_video()
QDialog.exec = _orig_exec
pump()

# --- 3. The burned-in strip: render _paint_strip exactly as the exporter does, mid-best-lap ---
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from studio import export_video
win = w.session.lap_window(best)
t_mid = (win[0] + win[1]) / 2.0
vals = export_video.sample_overlay_values(w.session, t_mid) if hasattr(export_video, 'sample_overlay_values') else None
if vals is None:  # locate the sampling fn by line 462 signature
    for nm in dir(export_video):
        fn = getattr(export_video, nm)
        if callable(fn) and getattr(fn, '__code__', None) is not None and 'lap_at_time' in fn.__code__.co_names:
            try:
                vals = fn(w.session, t_mid)
                log(f'sampled via export_video.{nm}')
                break
            except TypeError:
                continue
assert vals is not None, 'could not sample overlay values'
log(f'OVERLAY-SAMPLE  t={t_mid:.2f}  vals.lap_id={vals.lap_id}  (app calls this lap {lap_label(vals.lap_id)})')
img = QImage(560, 64, QImage.Format_ARGB32_Premultiplied)
img.fill(Qt.transparent)
p = QPainter(img)
p.setRenderHint(QPainter.Antialiasing)
export_video._paint_strip(p, QRectF(4, 8, 552, 48), w.session, vals, win[0])
p.end()
img.save(os.path.join(OUTDIR, '03_burned_lap_strip.png'))
print('shot 03_burned_lap_strip', flush=True)

# --- 4. Cross-check: what the laps CSV *content* calls the same lap ---
from studio import export_data
csv_path = os.path.join(OUTDIR, 'laps.csv')
export_data.write_laps_csv(csv_path, w.session)
with open(csv_path) as f:
    lines = f.read().splitlines()
hits = [ln for ln in lines if '1:08.228' in ln or '68.228' in ln]
log('LAPS-CSV header:', lines[0] if lines else '(empty)')
for h in hits[:3]:
    log('LAPS-CSV best-lap row:', h)

with open(os.path.join(OUTDIR, 'probe_log.txt'), 'w') as f:
    f.write('\n'.join(LOG) + '\n')
print('DONE', flush=True)
