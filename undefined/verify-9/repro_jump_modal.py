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
OUTDIR = '/Users/daniil/Documents/Github/pacer/undefined/verify-9'
def pump(n=40):
    for _ in range(n): app.processEvents()
def shot(name):
    pump(); w.grab().save(os.path.join(OUTDIR, name + '.png')); print('shot', name)
pump(60)

# ---- Repro: Coaching > Opportunities... then click a row's Jump button while exec() runs.
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton
from studio.coaching_panel import OpportunitiesDialog, _COL_GO

state = {}

def drive_dialog():
    dlg = next((x for x in app.topLevelWidgets() if isinstance(x, OpportunitiesDialog)), None)
    assert dlg is not None, 'dialog did not open'
    state['dlg'] = dlg
    pump()
    dlg.grab().save(os.path.join(OUTDIR, '01_dialog_open.png'))
    print('dialog modality:', dlg.windowModality(), 'isModal:', dlg.isModal(),
          'geometry:', dlg.geometry())
    # Click the first row's Jump button, exactly as a user would.
    btn = dlg.table.cellWidget(0, _COL_GO)
    assert isinstance(btn, QPushButton) and btn.isEnabled(), 'no enabled Jump button'
    QTest.mouseClick(btn, Qt.LeftButton)
    pump(80)
    # Is the dialog still up after Jump?
    state['still_visible'] = dlg.isVisible()
    print('AFTER JUMP: dialog still visible =', dlg.isVisible())
    dlg.grab().save(os.path.join(OUTDIR, '02_dialog_after_jump.png'))
    w.grab().save(os.path.join(OUTDIR, '03_mainwindow_after_jump.png'))
    # Record what the jump did to the main window.
    print('lap tab index:', w.view.tab_bar.currentIndex(),
          'tab text:', w.view.tab_bar.tabText(w.view.tab_bar.currentIndex()))
    print('selected laps:', w.view.table.selected_ids()
          if hasattr(w.view.table, 'selected_ids') else '?')
    # Does the modal dialog geometry overlap the main window's map area?
    print('main window geometry:', w.geometry())
    # Close it so exec() returns.
    dlg.reject()

QTimer.singleShot(800, drive_dialog)
# Open the dialog through the real menu path (blocks in exec() until drive_dialog rejects).
w._open_opportunities()
pump(40)
print('exec returned; dialog still visible was:', state.get('still_visible'))
print('DONE')
