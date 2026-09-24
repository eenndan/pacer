"""The saved-tracks manager: rename a mistyped circuit, delete one that should not be there.

WHY IT EXISTS. ``File ▸ Save as track…`` could CREATE a named circuit and nothing could ever change
it: a name typed with a thumb on a phone-sized dialog was permanent, and a circuit saved by mistake
(the wrong anchor, a test entry, someone else's footage) stayed in the database auto-detecting
itself onto every future recording at that location. This dialog is the other half of that gesture.

    ┌──────────────────────────────────────────────┐
    │  3 saved tracks                              │
    │  These are the circuits Pacer auto-detects…  │ ← what a delete does and does not touch
    ├──────────────────────────────────────────────┤
    │  Daytona Milton Keynes   (built-in)          │ ← built-ins are listed, never editable
    │  Sandown Park   (built-in, refined by you)   │ ← deletable (reverts), never renamed
    │  Whilton Mill            (2 sector lines)    │
    ├──────────────────────────────────────────────┤
    │  [Rename…] [Delete…] [Restore…]      [Close] │
    └──────────────────────────────────────────────┘

FILE-OP-FREE AND PACER-FREE, like ``library_dialog``: every act is a dependency-injected callback
that the app owns and that returns the fresh row list, so this module opens no file, and a control
whose callback is absent simply is not built. That is also what keeps it hermetic in tests.

A ROW IS A PLAIN DICT, built by the app from the merged track view::

    {"name": "Sandown Park",   # the circuit's name
     "builtin": <bool>,        # it is one of the shipped SEED circuits
     "editable": <bool>,       # the USER's own file holds it, so a rename/delete can reach it
     "sectors": <int>}         # how many sector lines it carries (0 for most)

``builtin`` and ``editable`` are two different questions and both are needed: a built-in the user
has REFINED is both, and deleting it reverts to the shipped line rather than removing the circuit.

THE REFUSALS COME FROM THE STORE, NOT FROM HERE. ``track_db.rename_track`` / ``remove_track`` raise
ValueError subclasses whose messages are already written for a human ("a track called 'Croft' is
already saved — pick another name"), so this dialog shows the message it is given rather than
re-deciding the rule in a second place where the two could drift apart.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from . import APP_NAME, theme
from ._signal import plural
from .widgets import WrapLabel

_log = logging.getLogger(__name__)

NAME_ROLE = Qt.UserRole + 1       # the row's circuit name, raw
EDITABLE_ROLE = Qt.UserRole + 2    # whether a rename/delete can reach it

# What the note under the header says. It leads with what a delete DOESN'T touch, because that is
# the question a user hesitating over a destructive button is actually asking — and because it is
# the guarantee this feature had to be built to keep: a circuit's name is an identity key in the
# library's personal-best history, so deleting the circuit deliberately leaves every analysed
# session exactly where it is, filed under the name it was driven as.
_NOTE = (
    "These are the circuits Pacer auto-detects, with the start/finish and sector lines you saved "
    "for each (tracks.json). Deleting one only stops FUTURE recordings at that location detecting "
    "it — your analysed sessions, their lap times and their personal-best history are kept, and no "
    "video file is touched. A copy of the list is kept as tracks.json.bak before any change, so "
    "\"Restore…\" puts it back. Built-in circuits ship with Pacer and cannot be renamed or deleted; "
    "saving your own lines over one makes a copy you can delete, which puts Pacer's line back."
)


def _when(mtime) -> str:
    """" (from 14:05 on 3 Jun)" for a backup's POSIX mtime, or "" when there isn't one. Same
    best-effort shape as library_dialog._backup_when — a backup with no readable date still gets
    offered, it just doesn't say when."""
    if not mtime:
        return ""
    try:
        stamp = datetime.datetime.fromtimestamp(float(mtime))
    except (OSError, OverflowError, ValueError):
        return ""
    return f" (from {stamp.strftime('%H:%M on %-d %b')})"


def _row_label(row: dict) -> str:
    """One list row: the circuit's name, with the one or two facts that change what the buttons do.
    A built-in says so (its buttons are off); sector lines are named because they are the part of a
    saved circuit a user is most likely to have forgotten they placed."""
    parts = []
    if row.get("builtin"):
        parts.append("built-in" if not row.get("editable") else "built-in, refined by you")
    sectors = int(row.get("sectors") or 0)
    if sectors:
        parts.append(plural(sectors, "sector line"))
    name = str(row.get("name", ""))
    return f"{name}    ({',  '.join(parts)})" if parts else name


class TrackManagerDialog(QDialog):
    """Browse the saved circuits and rename or delete the ones the user's own file holds.

    `rows` is the row model described in the module docstring. Every mutating control is optional
    and injected; each callback returns the FRESH row list, and may raise ValueError (a refusal,
    with a message written for the user) or OSError (an unwritable store) — both are shown as a
    warning and leave the list as it was.
    """

    def __init__(self, rows: list[dict], parent=None,
                 rename_track: Callable[[str, str], list[dict]] | None = None,
                 delete_track: Callable[[str], list[dict]] | None = None,
                 restore_tracks: Callable[[], list[dict]] | None = None,
                 backup_info: Callable[[], dict | None] | None = None,
                 reverts_to_builtin: Callable[[str], dict | None] | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — saved tracks")
        self._rows = list(rows or [])
        self._rename_track = rename_track
        self._delete_track = delete_track
        self._restore_tracks = restore_tracks
        self._backup_info = backup_info
        self._reverts_to_builtin = reverts_to_builtin

        root = QVBoxLayout(self)
        root.setContentsMargins(theme.SPACE_M, theme.SPACE_M, theme.SPACE_M, theme.SPACE_M)
        root.setSpacing(theme.SPACE_S)

        self._title = QLabel()
        self._title.setProperty("role", "PanelHeader")
        root.addWidget(self._title)

        note = WrapLabel(_NOTE)
        note.setFont(theme.mono_font(theme.CAPTION))
        note.setProperty("role", "Note")
        root.addWidget(note)

        self.list = QListWidget()
        self.list.currentItemChanged.connect(lambda *_: self._sync_buttons())
        self.list.itemDoubleClicked.connect(lambda *_: self._rename_selected())
        root.addWidget(self.list, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(theme.SPACE_S)
        if self._rename_track is not None:
            self.rename_btn = QPushButton("Rename…")
            self.rename_btn.setToolTip(
                "Give this circuit a different name. Its start/finish line, its sector lines and "
                "its location are unchanged, and the sessions you have already analysed here keep "
                "their personal-best history")
            self.rename_btn.clicked.connect(self._rename_selected)
            buttons.addWidget(self.rename_btn)
        if self._delete_track is not None:
            self.delete_btn = QPushButton("Delete…")
            self.delete_btn.setToolTip(
                "Stop future recordings at this location detecting this circuit. Your analysed "
                "sessions and video files are not touched, and a copy is kept as tracks.json.bak")
            self.delete_btn.clicked.connect(self._delete_selected)
            buttons.addWidget(self.delete_btn)
        if self._restore_tracks is not None:
            self.restore_btn = QPushButton("Restore…")
            self.restore_btn.clicked.connect(self._restore)
            buttons.addWidget(self.restore_btn)
        buttons.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

        self._backup = self._read_backup_info()
        self._rerender()

    # ------------------------------------------------------------------ state
    def _read_backup_info(self) -> dict | None:
        """What the automatic backup holds (a ``track_db.backup_summary`` dict), or None when it
        isn't wired / there is nothing restorable. Guarded: a failing query just means "no restore
        offered", never a broken dialog."""
        if self._backup_info is None:
            return None
        try:
            info = self._backup_info()
        except Exception:  # noqa: BLE001 — a backup query must never break the dialog
            _log.warning("track backup not readable", exc_info=True)
            return None
        return info if isinstance(info, dict) and info.get("tracks") else None

    def _selected(self) -> dict | None:
        """The selected row's model dict, or None."""
        item = self.list.currentItem()
        if item is None:
            return None
        name = item.data(NAME_ROLE)
        return next((r for r in self._rows if r.get("name") == name), None)

    def _rerender(self):
        """Rebuild the list from ``self._rows`` and re-sync the header and the buttons."""
        self.list.clear()
        for row in self._rows:
            item = QListWidgetItem(_row_label(row))
            item.setData(NAME_ROLE, row.get("name"))
            item.setData(EDITABLE_ROLE, bool(row.get("editable")))
            if not row.get("editable"):
                # A built-in is shown but not offered: selecting it would arm two buttons that can
                # only refuse. Greyed the same way library_dialog greys a missing-file row.
                item.setFlags(item.flags() & ~Qt.ItemIsSelectable & ~Qt.ItemIsEnabled)
            self.list.addItem(item)
        self._title.setText(plural(len(self._rows), "saved track"))
        for i in range(self.list.count()):
            if self.list.item(i).flags() & Qt.ItemIsSelectable:
                self.list.setCurrentRow(i)
                break
        self._sync_buttons()

    def _sync_buttons(self):
        """Rename/Delete follow the selection; Restore follows whether there is a backup, and says
        what it holds so the button explains its own greyed-out state before it is clicked."""
        row = self._selected()
        on = row is not None and bool(row.get("editable"))
        # A built-in the user refined can be deleted (its shipped line comes back) but not renamed:
        # the store refuses that, because the seed would return under the old name beside it.
        for attr, enabled in (("rename_btn", on and not row.get("builtin")), ("delete_btn", on)):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setEnabled(bool(enabled))
        btn = getattr(self, "restore_btn", None)
        if btn is not None:
            info = self._backup
            btn.setEnabled(info is not None)
            if info is None:
                btn.setToolTip(
                    "No backup yet — one is kept automatically as tracks.json.bak whenever a "
                    "circuit is renamed or deleted")
            else:
                btn.setToolTip(
                    f"Put back the automatic backup{_when(info.get('mtime'))} "
                    f"({plural(int(info['tracks']), 'circuit')}). The list you have now is kept as "
                    "the backup, so you can swap back")

    def _apply(self, fresh):
        """Take a callback's fresh row list, re-read the backup (every mutation makes one) and
        re-render. A callback that returns something that isn't a list leaves the dialog alone."""
        if isinstance(fresh, list):
            self._rows = list(fresh)
        self._backup = self._read_backup_info()
        self._rerender()

    def _failed(self, title: str, exc: Exception):
        """Show a refusal or a write failure in the store's own words. The store writes these
        messages for a human, so nothing is re-worded here."""
        QMessageBox.warning(self, title, str(exc))

    # ------------------------------------------------------------------ the three acts
    def _rename_selected(self):
        """Ask for a new name and apply it. The store owns every refusal (blank, already taken, a
        built-in, absent) and its message is shown verbatim."""
        row = self._selected()
        # A built-in, refined or not, is never offered (the button is off; a double-click lands here).
        if self._rename_track is None or row is None or not row.get("editable") or row.get("builtin"):
            return
        old = str(row["name"])
        new, ok = QInputDialog.getText(self, "Rename track", "Track name:", text=old)
        if not ok:
            return
        new = new.strip()
        if not new or new == old:
            return
        try:
            self._apply(self._rename_track(old, new))
        except (OSError, ValueError) as exc:
            self._failed("Rename track", exc)

    def _delete_selected(self):
        """Confirm, then delete. The confirm names what SURVIVES — the analysed sessions and their
        PB history, the video files — because that is the fear this button has to answer, and it
        names where the copy went. A refined built-in gets a different sentence: that circuit is not
        being removed, its shipped line is coming back."""
        row = self._selected()
        if self._delete_track is None or row is None or not row.get("editable"):
            return
        name = str(row["name"])
        reverts = None
        if self._reverts_to_builtin is not None:
            try:
                reverts = self._reverts_to_builtin(name)
            except Exception:  # noqa: BLE001 — a wording query must never block the act
                _log.warning("could not tell whether %r reverts", name, exc_info=True)
        if reverts is not None:
            body = (
                f"Put the built-in “{name}” back?\n\n"
                "This circuit ships with Pacer, so it is not removed — the start/finish line you "
                "saved over it is discarded and Pacer's own line comes back. Recordings here will "
                "detect the built-in line instead of yours."
            )
        else:
            body = (
                f"Delete the saved track “{name}”?\n\n"
                "Recordings you make here will stop detecting it, and its start/finish and sector "
                "lines are removed. Your analysed sessions keep their lap times and their "
                "personal-best history under this name, and no video file is touched."
            )
        ok = QMessageBox.question(
            self, "Delete track",
            body + "\n\nA copy of the list is kept as tracks.json.bak, so \"Restore…\" puts it "
                   "back.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        try:
            self._apply(self._delete_track(name))
        except (OSError, ValueError) as exc:
            self._failed("Delete track", exc)

    def _restore(self):
        """Confirm, then put the automatic backup back. Names BOTH sides — a restore is destructive
        in the other direction — and says the current list becomes the backup, which is what makes
        it reversible."""
        if self._restore_tracks is None or not self._backup:
            return
        info = self._backup
        ok = QMessageBox.question(
            self, "Restore saved tracks",
            f"Replace this list ({plural(len(self._rows), 'circuit')}) with the backup"
            f"{_when(info.get('mtime'))} ({plural(int(info['tracks']), 'circuit')})?\n\n"
            "The list you have now is kept as the backup, so you can swap back. Your analysed "
            "sessions and video files are not touched either way.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        try:
            self._apply(self._restore_tracks())
        except (OSError, ValueError) as exc:
            self._failed("Restore saved tracks", exc)
