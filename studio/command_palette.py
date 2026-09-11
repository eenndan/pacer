"""CommandPalette (⌘K): find and run any of the app's commands by typing its name.

WHY IT EXISTS. Pacer's capability grew faster than its menu bar could announce it — five menus,
thirty-odd actions, plus a dozen keyboard-only toggles documented on a card most users never open.
"Compare vs reference recording", "Copy stats summary" and "Save as track…" are each three levels
of pointer travel from the thing you were looking at, and the four lap-panel pages had no name
anywhere except a digit.

WHERE ITS CONTENT COMES FROM, which is the part that matters: it is GENERATED, never authored.

  1. Every action on the window's own menu bar, walked live — so a menu item added tomorrow is in
     the palette tomorrow, at its real enablement, with the accelerator Qt itself paints.
  2. Every `help_dialog.Command` that carries a `run` and is not a menu action — the keyboard-only
     commands (Space / M / G / C / the four lap tabs / the two rate keys / F / ?). That is the SAME
     registry the ? shortcut card renders, which is what makes "the card and the palette agree" a
     property of the code rather than a promise: `help_dialog.SHORTCUT_GROUPS` is a view of
     `COMMANDS`, and so is this.

`tests/test_command_palette.py` pins both halves — every menu action reachable, every `run` string
resolving on a real window, and every registry row's key text identical to the card's.

IT IS A `QDialog`, and it looks like this app: a `QLineEdit` filter over a `QTableWidget`, exactly
the pair `library_dialog` uses, so it inherits the theme's own table/input rules and needs no
stylesheet of its own. A disabled command is listed with Qt's disabled item flags rather than a
hand-picked grey — the theme's Disabled colour group already says what "you cannot run this yet"
looks like, and an unselectable row also cannot be run by accident.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from . import APP_NAME, theme
from .help_dialog import COMMANDS, _key_text

# The palette's own extents, said in the app's units. A dialog MEASURE is not a gap, so the SPACE
# scale has nothing to say about it (tests/test_design_system.py argues exactly this for the three
# Help cards) — but it can still be a multiple of a declared step rather than a number somebody
# liked: 12 and 9 times SPACE_3XL are 576 x 432, a reading width for a command name plus its
# provenance and key, over about a dozen visible rows.
_MEASURE_PX = 12 * theme.SPACE_3XL
_HEIGHT_PX = 9 * theme.SPACE_3XL

#: Column order: what it is · where it lives · how to reach it from the keyboard.
_COL_TITLE, _COL_GROUP, _COL_KEY = 0, 1, 2

_HINT = "Arrows to move · Return to run · Esc to close"


class Entry(NamedTuple):
    """One runnable row — a menu `action`, or a window method `call`, never both.

    THE QAction IS HELD, not `action.trigger`. A PySide bound method does not keep its object's
    Python wrapper alive, and in this binding a QMenu is Python-OWNED: dropping the last wrapper
    for a menu (or for the action that opens it) destroys the C++ menu. Storing `action.trigger`
    made every stored row a wrapper waiting to be collected — measured, `entry.invoke()` raised
    "Internal C++ object (QAction) already deleted" on the very next event-loop turn. Holding the
    action in the row is what keeps it real. See `_menu_entries` for the other half of the same
    hazard."""

    title: str
    group: str
    key: str
    enabled: bool
    action: QAction | None = None
    call: Callable[[], None] | None = None

    def run(self) -> None:
        """Do the thing. A menu row goes through the QAction, so every consumer of that action's
        `triggered` sees it exactly as if the menu item had been clicked."""
        if self.action is not None:
            self.action.trigger()
        elif self.call is not None:
            self.call()


def _clean(text: str) -> str:
    """A menu action's label without its mnemonic markers — `&File` is "File" and the privacy
    item's escaped `&&` is a real ampersand. Qt keeps both in `text()`."""
    return text.replace("&&", "\x00").replace("&", "").replace("\x00", "&")


def _is_placeholder(action: QAction) -> bool:
    """True for a menu row that is a SENTENCE ABOUT THE MENU rather than a command: disabled, no
    accelerator, and parenthesised — the `(none)` an empty Open Recent shows. Listing it in a
    palette offers the user a command called "(none)". A predicate rather than a name check, so it
    covers the next empty submenu too."""
    text = _clean(action.text()).strip()
    return (not action.isEnabled() and action.shortcut().isEmpty()
            and text.startswith("(") and text.endswith(")"))


def _menu_entries(window) -> list[Entry]:
    """Every action on `window`'s menu bar, grouped by the menu it sits in.

    THE WALK IS `findChildren`, AND THAT IS NOT A STYLE CHOICE — the obvious spelling destroys the
    app's menu bar. In this PySide binding a QMenu made by `addMenu` is PYTHON-owned, and
    `QAction.menu()` hands that ownership to the wrapper it returns: the natural recursive walk
    (`for top in bar.actions(): walk(top.menu(), …)`) therefore deletes every menu it descends into
    as soon as those temporary wrappers are collected. Measured on this window — after one walk and
    one event-loop turn, 29 of 31 menu actions reported their C++ object already deleted and
    `menuBar().actions()[0].menu()` raised. `QMenuBar.findChildren(QMenu)` transfers no ownership
    (measured over three consecutive walks: the bar is untouched), so the menus are found that way
    and the submenu-openers are identified by their own `menuAction()` rather than by asking an
    action for its menu. `tests/test_command_palette.py` reopens the palette and re-reads the bar,
    which is the regression this shape exists for. The hazard was already known to this repo, in
    one sentence that had never had to be acted on: `tests/test_app_chrome.py:_menus` reaches its
    five QMenus through `action.parent()` and says why — "NOT menuBar().actions(): those wrappers
    get reaped by Shiboken and take the whole menu bar down with them".

    THE GROUP IS THE DEEPEST MENU, not the top-level one: "Export", not "File". A path would be the
    honest answer and the app spells one `File ▸ Export`, but `▸` is one of the characters Inter
    does not carry (tests/test_glyph_vocabulary.py measured it resolving to Apple Symbols at a
    different size), and the deepest name is the more useful half anyway — it is what makes typing
    "export csv" find "Lap times (CSV)", which "File" cannot.

    EVERY MENU IS TOLD IT IS BEING SHOWN FIRST. Four of this window's menus fill or enable
    themselves in an `aboutToShow` handler (Open Recent's contents, File ▸ Export's enablement,
    Edit's undo/revert, View's session-dependent items), and a palette shows all of them at once
    without opening any. Without the emit it would list last session's recents and grey out
    commands that are live. Guarded and generic — the palette names none of those handlers, it just
    delivers the signal each menu already listens for.

    Separators are skipped, submenu-opening actions are not listed (there is nothing to run), and
    placeholders are dropped (see `_is_placeholder`).
    """
    menus = window.menuBar().findChildren(QMenu)
    # The openers, held for the length of the walk for the ownership reason above.
    openers = [m.menuAction() for m in menus]
    opener_ids = {id(a) for a in openers}
    out: list[Entry] = []
    seen: set[int] = set()
    for menu in menus:
        try:
            menu.aboutToShow.emit()
        except Exception:  # noqa: BLE001 — a stale menu must not cost the user the whole palette
            pass
        group = _clean(menu.title())
        for action in menu.actions():
            if action.isSeparator() or id(action) in opener_ids:
                continue
            if id(action) in seen or not _clean(action.text()).strip():
                continue
            seen.add(id(action))
            if _is_placeholder(action):
                continue
            out.append(Entry(
                title=_clean(action.text()),
                group=group,
                key=action.shortcut().toString(QKeySequence.NativeText),
                enabled=action.isEnabled(),
                action=action,
            ))
    return out


def _registry_entries(window, taken: set[int]) -> list[Entry]:
    """The registry's keyboard-only commands: every `help_dialog.Command` with a `run` whose target
    is a window METHOD (a `run` naming a QAction is already in `taken`, harvested from the menu).

    A missing attribute is skipped rather than raised on — a palette must never be the thing that
    stops a window opening — and `tests/test_command_palette.py` is what makes that safe by
    asserting every `run` resolves. A METHOD row is listed enabled: it is the same global toggle its
    key is, and a key that quietly does nothing before a recording is loaded is the behaviour this
    app already ships, not something the palette invents. (A `run` naming a QAction takes the
    action's own enablement instead; none does today, and the branch is here so that the day one
    does, its greyed state is honest rather than assumed.)
    """
    out: list[Entry] = []
    for cmd in COMMANDS:
        if cmd.run is None:
            continue
        target = getattr(window, cmd.run, None)
        if target is None:
            continue
        key = cmd.palette_key or _key_text(cmd.key)
        if isinstance(target, QAction):
            if id(target) in taken:
                continue
            out.append(Entry(cmd.title, cmd.group, key, target.isEnabled(), action=target))
        elif callable(target):
            out.append(Entry(cmd.title, cmd.group, key, True, call=target))
    return out


def entries(window) -> list[Entry]:
    """Everything the palette offers, menu first (the app's own ordering, so the resting first row
    is `Open…`) then the keyboard-only commands in registry order."""
    menu = _menu_entries(window)
    return menu + _registry_entries(window, {id(e.action) for e in menu if e.action is not None})


def rank(entry: Entry, query: str) -> int | None:
    """How well `entry` matches `query`, lower is better — or None for no match.

    EVERY whitespace-separated token must appear somewhere in "<title> <group>", so "export csv"
    finds "Lap times (CSV)" under File and "stats copy" finds "Copy stats summary". The RANK is
    then decided by the first token alone against the TITLE: a title that starts with it, then one
    whose any word starts with it, then a bare substring, and last a match that only the group
    supplied. Ties keep the caller's order, which is menu order."""
    hay = f"{entry.title} {entry.group}".lower()
    tokens = query.lower().split()
    if not all(t in hay for t in tokens):
        return None
    if not tokens:
        return 0
    title, first = entry.title.lower(), tokens[0]
    if title.startswith(first):
        return 0
    if any(word.lstrip("(").startswith(first) for word in title.split()):
        return 1
    if first in title:
        return 2
    return 3


def matches(all_entries: list[Entry], query: str) -> list[Entry]:
    """`all_entries` filtered + ordered for `query` (empty query keeps everything, in order)."""
    scored = [(r, i, e) for i, e in enumerate(all_entries)
              if (r := rank(e, query)) is not None]
    scored.sort(key=lambda t: (t[0], t[1]))
    return [e for _r, _i, e in scored]


class CommandPalette(QDialog):
    """⌘K. Type to filter, Return to run, Esc to close.

    Built fresh per open (like `OpportunitiesDialog`) so the enablement it paints is the live one —
    `_menu_entries` primes each menu's `aboutToShow` as it walks, which is what refreshes Open
    Recent and the File ▸ Export enablement for a surface that never shows either menu."""

    def __init__(self, window, parent=None):
        super().__init__(parent if parent is not None else window)
        self.setWindowTitle(f"{APP_NAME} — commands")
        self.setMinimumWidth(_MEASURE_PX)
        self.resize(_MEASURE_PX, _HEIGHT_PX)
        self._entries = entries(window)
        self._shown: list[Entry] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(theme.SPACE_M, theme.SPACE_M, theme.SPACE_M, theme.SPACE_M)
        root.setSpacing(theme.SPACE_S)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search commands…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedHeight(theme.CTRL_H)
        self.search.textChanged.connect(self._apply_filter)
        self.search.returnPressed.connect(self._activate)
        # The arrows have to reach the LIST while the caret stays in the field — a palette where
        # you must tab to the results is not a palette. QLineEdit leaves Up/Down unhandled, but
        # "unhandled" still means "consumed by the focus widget's parent chain", not "delivered to
        # the table", so the filter routes them explicitly.
        self.search.installEventFilter(self)
        root.addWidget(self.search)

        self.table = QTableWidget(0, 3)
        self.table.horizontalHeader().setVisible(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(theme.GRID_ROW_H)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(_COL_TITLE, QHeaderView.Stretch)
        header.setSectionResizeMode(_COL_GROUP, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_COL_KEY, QHeaderView.ResizeToContents)
        self.table.itemActivated.connect(lambda _item: self._activate())
        self.table.cellDoubleClicked.connect(lambda *_: self._activate())
        root.addWidget(self.table, 1)

        # One line that is both the affordance and the empty state — the palette has nowhere else
        # to say "nothing matched", and a dedicated empty-state card in a list that refills on
        # every keystroke would flash in and out of the layout as you type.
        self.hint = QLabel(_HINT)
        self.hint.setProperty("role", "Note")
        root.addWidget(self.hint)

        self._apply_filter("")
        self.search.setFocus()

    # ------------------------------------------------------------------ filtering
    def _apply_filter(self, text: str) -> None:
        self._shown = matches(self._entries, text)
        self.table.setRowCount(len(self._shown))
        for row, entry in enumerate(self._shown):
            for col, value in ((_COL_TITLE, entry.title), (_COL_GROUP, entry.group),
                               (_COL_KEY, entry.key)):
                item = QTableWidgetItem(value)
                if col == _COL_KEY:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if not entry.enabled:
                    # Qt's own Disabled colour group (the theme sets it to C.text_muted, the token
                    # reserved for chrome you cannot act on) AND unselectable, so a command that
                    # needs a loaded session cannot be run from here by accident.
                    item.setFlags(Qt.NoItemFlags)
                self.table.setItem(row, col, item)
        self._select_first_runnable()
        self.hint.setText(_HINT if self._shown else f"No command matches “{text}”")

    def _select_first_runnable(self) -> None:
        """Park the selection on the first row that can actually be run, so Return always means
        something (the top match can be a greyed-out export)."""
        for row, entry in enumerate(self._shown):
            if entry.enabled:
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, _COL_TITLE))
                return
        self.table.clearSelection()

    # ------------------------------------------------------------------ running
    def selected(self) -> Entry | None:
        row = self.table.currentRow()
        if 0 <= row < len(self._shown) and self._shown[row].enabled:
            return self._shown[row]
        return None

    def _activate(self) -> None:
        """Run the selected command and close FIRST — several of them open a modal of their own
        (the export pickers, the Library), and a palette still on screen behind one is a window the
        user has to dismiss twice."""
        entry = self.selected()
        if entry is None:
            return
        self.accept()
        entry.run()

    # ------------------------------------------------------------------ keyboard
    def _step(self, delta: int) -> None:
        """Move the selection `delta` runnable rows, clamped — skipping the disabled ones, for the
        same reason `_select_first_runnable` exists."""
        runnable = [i for i, e in enumerate(self._shown) if e.enabled]
        if not runnable:
            return
        here = self.table.currentRow()
        if here in runnable:
            i = min(max(runnable.index(here) + delta, 0), len(runnable) - 1)
        else:
            i = 0 if delta > 0 else len(runnable) - 1
        self.table.selectRow(runnable[i])
        self.table.scrollToItem(self.table.item(runnable[i], _COL_TITLE))

    def eventFilter(self, obj, event):
        if obj is self.search and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Down, Qt.Key_Up):
                self._step(1 if event.key() == Qt.Key_Down else -1)
                return True
            if event.key() in (Qt.Key_PageDown, Qt.Key_PageUp):
                page = max(1, self.table.viewport().height() // max(theme.GRID_ROW_H, 1))
                self._step(page if event.key() == Qt.Key_PageDown else -page)
                return True
        return super().eventFilter(obj, event)
