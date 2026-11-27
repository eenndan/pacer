"""ExportController — every "this leaves the app" flow, lifted out of `StudioWindow` (review §7.1).

app.py had grown to 4,166 lines and 142 methods, of which this cluster was 21 methods and 535 —
the largest single responsibility in a class whose job is the WINDOW. Same pattern, and the same
reason, as `scrub_controller` and `compare_controller`: one object owns one cluster, the window
holds it as `self.exports`, and nothing here reaches for window state except through `self.win`.

WHAT IT OWNS: the File ▸ Export menu's enablement (`sync_menu`), the data writers' Qt side (laps /
channels CSV, the HTML report, the clipboard summary), the shareable lap card, and the whole
overlay-video flow — the options picker, the size estimate, the worker, the progress dialog, the
failure table and the completion card.

WHAT IT DOES NOT OWN: the writers themselves (`export_data.py` is Qt-free by contract), the
renderer (`export_video.py`), and the window's own chrome. `grab_png` lives here because the two
map-grab helpers that use it are export-only callers.

TWO DELIBERATE SHAPES, both to keep the import one-way (app -> controller, never back):

  * `STATUS_MS` is app.py's module constant and is PASSED IN at construction, not imported.
  * `ExportChoice` moved here with the picker that returns it, and app.py RE-EXPORTS it — so
    `studio.app.ExportChoice` still resolves for the tests that name it that way.
"""
from __future__ import annotations

import gc
import logging
import math
import os
import time
from typing import NamedTuple

from PySide6.QtCore import QBuffer, QIODevice, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import (
    APP_NAME,
    data_quality,
    export_compare,
    export_data,
    export_video,
    prefs,
    share_card,
    theme,
    workers,
)
from ._signal import fmt_hms, lap_label, plural
from .overlay import LAYOUT_PREFIX, broadcast, corner_cards, ghost
from .session import fmt_time
from .stats import corner_delta_step, sector_col
from .workers import VideoExportWorker

_log = logging.getLogger(__name__)


def _pass_noun(cid: int) -> str:
    """What a pass goes through, for a sentence: "corner", or "sector" for a `stats.sector_key`."""
    return "corner" if sector_col(cid) is None else "sector"


def report_tokens() -> dict[str, str]:
    """The app's colours the HTML report is drawn in (`export_data.REPORT_TOKENS`), read off
    theme — the one source; the Qt-free writer cannot import it. The semantic two follow the
    active palette, as every best-lap and behind cue in the app does."""
    return {"canvas": theme.C.canvas, "surface": theme.C.surface, "border": theme.C.border,
            "text": theme.C.text, "text_dim": theme.C.text_dim, "accent": theme.C.accent,
            "ahead": theme.best_lap_colour(), "behind": theme.behind_colour()}


def report_lapstrip_png(session, unit) -> bytes | None:
    """The lap-strip card (`share_lapstrip`) as PNG bytes for the report, or None where File ▸
    Export ▸ "Lap-strip card…" is greyed: the lap card's gate (provisional timing) or under two
    clean laps. The same data, render and palette as the card itself.

    One picture of the report, not the report: a strip that cannot be built is logged and left
    out (None), so it can never cost the user the whole document."""
    from . import share_lapstrip  # local, like gate_lapstrip's: the strip's Qt render
    try:
        data = share_lapstrip.lapstrip_data(session, unit=unit)
        if not data.offered:
            return None
        image = share_lapstrip.render_lapstrip(data, palette=theme.active_palette())
        return share_lapstrip.lapstrip_to_png(image)
    except Exception:  # noqa: BLE001 — see the docstring: the strip is optional, the page is not
        _log.exception("lap strip for the report could not be built; the report goes without it")
        return None


def report_card_png(win) -> bytes | None:
    """The lap card as PNG bytes for the report's opening, or None where the card is not made: the
    SAME build File ▸ Export ▸ "Lap card (image)…" saves (`StudioWindow._build_share_card`, its
    PB mark, palette and unit included), so its gate is the card's own — provisional timing or no
    valid lap gives no card, and the report then opens with no picture rather than a stand-in.

    One picture of the report, not the report: a card that cannot be built is logged and left out."""
    try:
        image = win._build_share_card()
        return None if image is None else share_card.card_to_png(image)
    except Exception:  # noqa: BLE001 — the card is optional, the page is not
        _log.exception("lap card for the report could not be built; the report goes without it")
        return None


class ExportChoice(NamedTuple):
    """What the overlay-video options picker returns (None on cancel): the overlay `config`, the
    `lead` seconds of run-up/run-off to add at each end of a lap, and the `scope` — which laps
    (or the whole session) get rendered.

    NEITHER `lead` NOR `scope` IS FOLDED INTO `config`, for the same reason. An `OverlayConfig`
    carries layout and output knobs — resolution, shape, quality, unit, palette — things that
    describe a FRAME. These two decide the export's WINDOW, which `export_video.build_scope_specs`
    has to resolve before it can even tell which chapter file(s) a clip comes from, and how many
    files there are. Different jobs, different values."""
    config: object
    lead: float
    scope: str = export_video.SCOPE_THIS_LAP
    cid: int | None = None        # SCOPE_PASS: the corner (or sector key) whose pass is rendered


class SteadyHint(QLabel):
    """An export picker's hint line: word-wrapped, `role="Hint"`, and NEVER SHORTER than it has
    been at this width while its dialog is open.

    The hint is re-written as the footage probe answers and as rows change, and a wrapped label
    that loses a line handed its height to the form above: every row moved 2-5 px down under the
    reader's eye (the demo: 3 lines at open, 2 once measured, in a dialog of fixed height). Kept
    at its tallest, the rows hold still and a shorter text leaves room under it instead. Keyed by
    width, so a dialog the user widens gets its own, smaller, floor.

    `show(text, reserve)`: while a line is still being measured the hint also keeps room for
    `reserve`, the text it expects to show once it lands, so a measured line LONGER than the
    "measuring…" one does not push the rows up either."""

    def __init__(self, text: str = ""):
        super().__init__(text)
        self.setWordWrap(True)
        # [role="Hint"] ranks BELOW the description above it by SIZE, not by a dimmer colour.
        self.setProperty("role", "Hint")
        # Top-left, not QLabel's vertical centre: in a box kept taller than its text (the floor,
        # the reserve) a centred "Measuring…" sat 7 px down and jumped up when the number landed.
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._floor: dict[int, int] = {}
        self._reserve = ""

    def show_text(self, text: str, reserve: str = "") -> None:
        self._reserve = reserve
        self.setText(text)
        self.updateGeometry()

    def _reserved_height(self, width: int) -> int:
        if not self._reserve:
            return 0
        m = self.contentsMargins()
        inner = max(1, width - m.left() - m.right() - 2 * self.margin())
        rect = self.fontMetrics().boundingRect(0, 0, inner, 0, Qt.TextWordWrap, self._reserve)
        return rect.height() + m.top() + m.bottom() + 2 * self.margin()

    def heightForWidth(self, width: int) -> int:  # noqa: N802 — Qt's virtual
        h = max(super().heightForWidth(width), self._floor.get(width, 0),
                self._reserved_height(width))
        self._floor[width] = h
        return h


class ExportController:
    """Owns the export cluster for ONE `StudioWindow`. Built once, in the window's constructor."""

    def __init__(self, win, status_ms: int):
        self.win = win
        self._status_ms = status_ms
        # The compare picker's "This pass" choice (`_ask_compare_options`): the live compare's
        # `PassAnchor`, or None for the two whole laps.
        self.compare_pass_chosen = None

    # ----------------------------------------------------------- data export (F11)
    # File ▸ Export Qt side (the writers are Qt-free in export_data.py).

    # WHY a gated export action is off. A disabled row that describes its feature tells you nothing
    # about how to reach it, and all of the gated ones did exactly that. These strings were written
    # for the action's TOOLTIP, on the belief that "Qt keeps showing a disabled action's tooltip,
    # so this is the only surface a greyed row has" — measured on the real screen, that is false on
    # macOS: this app's menu bar is native, so its items are NSMenuItems and never show a Qt
    # tooltip (see MENU_REASON_SEP in app.py). `StudioWindow._gate_action` now puts each string's
    # CONDITION clause on the menu item ITSELF and keeps the whole sentence on the tooltip, which
    # is reachable on the ⌘K palette's row. So each string still names the CONDITION and the way
    # out of it — in that order, separated by an em dash, because the first half is what the menu
    # shows.
    _NO_LAPS_REASON = ("No complete laps in this recording — drag the start/finish line on the map "
                       "to set where a lap begins, then export.")
    _PROVISIONAL_REASON = ("This recording's timing is provisional — the start line was "
                           "auto-fitted, not confirmed by you. Save it as a track "
                           "(File ▸ Save as track…) to confirm it.")
    _NO_SESSION_REASON = ("Open a recording first — there is nothing to export yet.")
    _NO_TRACK_REASON = ("Needs a complete lap and a GPS position — there are no usable timing lines "
                        "to promote into a reusable track.")
    _NO_LIBRARY_REASON = ("No recordings analysed yet — open a GoPro recording and it is remembered "
                          "here, with its track, date and best lap.")
    _NO_COMPARE_REASON = ("Nothing is being compared — press C (or the ⧉ Compare button over the "
                          "video) and pick the two laps, then export the pair.")
    def sync_menu(self):
        """Gate every export action on what IT needs. Connected to the File menu's aboutToShow
        (synced as the menu opens), so neither _load nor the failed-load path needs to reach into
        the menu.

        THREE predicates, deliberately — one per honest question:
          * a session at all — the submenu itself (nothing to export before a load);
          * at least one VALID lap — the four data exports. A recording the start line never
            segments has no rows to write: "Lap times (CSV)" wrote a header-only file and reported
            success in a window whose panels read "No complete laps found in this recording.";
          * VERIFIED timing — the shareable lap card. An auto-fitted start line makes the lap time
            arbitrary, so it is not a brag; card_data owns that verdict (blocked) and both card
            actions mirror it.

        The video export sits between the last two: it needs a lap, and on provisional timing it
        WARNS instead of refusing (see _export_overlay_video) — a provisional clip is still useful
        to the driver reviewing their own footage, an unverified brag card never is.

        The COMPARISON export has a FOURTH predicate, and it is not about the recording at all: it
        needs two panes actually pinned right now. It is the one export whose subject is a piece of
        live UI state rather than the session, so a menu item enabled with nothing being compared
        would be an item that can only fail."""
        has = hasattr(self.win, "session")
        self.win._export_menu.setEnabled(has)
        # The SUBMENU'S OWN ROW, which greys with it. With no session the six items below are not
        # on screen at all, so the six reasons they carry are not either — the opener has to say
        # why by itself. Gated AFTER setEnabled, which is what syncs a QMenu's enabled state onto
        # its menuAction (QMenu::changeEvent), so the gate has the last word on both.
        self.win._gate_action(self.win._export_menu.menuAction(), has, self._NO_SESSION_REASON)
        has_laps = has and self.win._has_valid_laps()
        for action in (self.win._export_laps_action, self.win._export_channels_action,
                       self.win._export_report_action, self.win._copy_stats_action,
                       self.win._export_video_action):
            self.win._gate_action(action, has_laps, self._NO_LAPS_REASON)
        compare_action = getattr(self.win, "_export_compare_action", None)
        if compare_action is not None:
            self.win._gate_action(compare_action, has_laps and self.compare_pair() is not None,
                                  self._NO_LAPS_REASON if not has_laps else self._NO_COMPARE_REASON)
        # The lap card also needs the timing to be TRUSTED. With a lap in hand the only thing
        # card_data can still be blocked on is the provisional start line, so the reason is exact.
        card_ok = has_laps and not self.win._share_card_blocked()
        self.win._gate_action(self.win._share_card_action, card_ok,
                          self._NO_LAPS_REASON if not has_laps else self._PROVISIONAL_REASON)
        self.win._gate_action(self.win._copy_card_action, card_ok,
                          self._NO_LAPS_REASON if not has_laps else self._PROVISIONAL_REASON)
        self.gate_lapstrip(has_laps)
        # Save-as-track needs USABLE timing lines (≥1 valid lap means the start line actually
        # segments this trace — the lines are worth promoting to a reusable track). NOT gated on
        # trust: promoting the lines is precisely how a provisional recording becomes verified, and
        # the map's own amber banner sends the user here to do it.
        self.win._gate_action(self.win._save_track_action, self.win._can_save_track(), self._NO_TRACK_REASON)
        # Library… needs a library. On a genuinely fresh index this item was enabled and opened a
        # 900x520 dialog whose table body was entirely blank, over a PB pane reading "Select a
        # recording to see its track's PB progression" — instructing an action that cannot be
        # performed (QA D4-07 / D2-03). Its neighbour "Back up library…" already answers honestly,
        # which is what made the pair read as an oversight rather than a decision. The dialog also
        # has a real empty state now (library_dialog), so this gate is belt-and-braces rather than
        # the only defence.
        self.win._gate_action(self.win._library_action, self.win._has_library(), self._NO_LIBRARY_REASON)
    def _export_default(self, suffix: str) -> str:
        """Default save path: next to the recording, named `<stem><suffix>` (e.g.
        `GX010060_laps.csv`). Falls back to just the suffix-derived name in the CWD when
        nothing is loaded from a real path (the bundled sample)."""
        first = self.win._paths[0] if getattr(self.win, "_paths", None) else ""
        stem = os.path.splitext(os.path.basename(first))[0]
        return os.path.join(os.path.dirname(first), f"{stem}{suffix}")
    def _export_save_path(self, title: str, suffix: str, filt: str) -> str | None:
        """One save prompt; None when the user cancels (⇒ the caller writes nothing)."""
        path, _ = QFileDialog.getSaveFileName(self.win, title, self._export_default(suffix), filt)
        return path or None
    def _export_folder_path(self, title: str, start: str) -> str | None:
        """One folder prompt, `_export_save_path`'s twin for the exports whose output is a folder's
        worth of files; None when the user cancels (⇒ the caller writes nothing)."""
        return QFileDialog.getExistingDirectory(self.win, title, start) or None
    def _export_lap_id(self) -> int | None:
        """The lap the channels CSV describes: the PRIMARY selected/followed lap (the same
        lap the Corners view tracks), falling back to the best lap. None when the session
        has no usable lap at all. The primary lap lives on the central view (self.win.view._corner_lap);
        resolved through it, with a defensive getattr for the no-view (failed-first-load) case."""
        view = getattr(self.win, "view", None)
        lap = getattr(view, "_corner_lap", None) if view is not None else None
        return lap if lap is not None else self.win.session.best_lap_id()
    def _no_laps_to_export(self) -> bool:
        """True (with the reason on the status bar) when there is nothing to export: no session, or
        a recording the start line never segmented. The click-time twin of _sync_export_menu's
        has_laps gate — the menu greys these actions out, this is the backstop for a shortcut or a
        stale menu state, and it replaces the old silent success on a header-only file."""
        if not hasattr(self.win, "session"):  # defensive: action fired with nothing loaded
            return True
        if not self.win._has_valid_laps():
            self.win.statusBar().showMessage(
                "no complete laps in this recording — nothing to export", self._status_ms)
            return True
        return False
    def export_laps_csv(self):
        if self._no_laps_to_export():
            return
        path = self._export_save_path("Export lap times", "_laps.csv", "CSV files (*.csv)")
        if not path:
            return
        if self._run_export(lambda: export_data.write_laps_csv(path, self.win.session), path):
            self.win.statusBar().showMessage(f"exported {os.path.basename(path)}", self._status_ms)
    def export_channels_csv(self):
        if not hasattr(self.win, "session"):
            return
        lap = self._export_lap_id()
        if lap is None:
            self.win.statusBar().showMessage("no valid lap to export channels for", self._status_ms)
            return
        path = self._export_save_path(f"Export lap {lap_label(lap)} channels",
                                      f"_lap{lap_label(lap)}_channels.csv", "CSV files (*.csv)")
        if not path:
            return
        if self._run_export(lambda: export_data.write_channels_csv(path, self.win.session, lap), path):
            self.win.statusBar().showMessage(f"exported {os.path.basename(path)}", self._status_ms)
    def export_report(self):
        if self._no_laps_to_export():
            return
        path = self._export_save_path("Export session report", "_report.html",
                                      "HTML files (*.html)")
        if not path:
            return
        # Render the map + charts to PNG here (QWidget.grab) — the report writer itself stays
        # Qt-free and just embeds the bytes. Each is a throwaway copy pinned to what the app opens
        # on, with the width the DOCUMENT lays it out at and a caption saying what it draws
        # (_grab_report_map / _grab_report_chart).
        images = [self._report_figure("Track map", self.win._grab_report_map),
                  self._report_figure("Speed · Δ", self.win._grab_report_chart)]
        # The two copies are destroyed already (app._discard_report_copy), but their pyqtgraph
        # items' Python halves sit in reference cycles that only a FULL collection frees, and the
        # app's object count makes those rare. Measured over 40 exports: +34 MB without this,
        # +21 MB with it (PR #591 review); it costs well under the ~0.2 s an export takes.
        gc.collect()
        # The report is a HUMAN document whose embedded chart axis and map colour bar already read
        # in the display unit, so its lap table must too — a km/h table under an mph chart put two
        # different numbers for the same lap on one page. (The CSVs stay canonical SI: they are
        # machine-readable files, and export_data's writers pass no unit.)
        # The lap strip is the strip card's own render, under its own gate (report_lapstrip_png);
        # the page's opening picture is the lap card's, under its (report_card_png).
        if self._run_export(lambda: export_data.write_report_html(
                path, self.win.session,
                source_label=self.win._loaded_label() or "session",
                images=images, unit=self.win._speed_unit,
                lapstrip=report_lapstrip_png(self.win.session, self.win._speed_unit),
                card=report_card_png(self.win), tokens=report_tokens()), path):
            self.win.statusBar().showMessage(f"exported {os.path.basename(path)}", self._status_ms)
    #: Under a figure the report could not draw, where the figure would have been.
    FIGURE_FAILED_NOTE = ("This figure could not be drawn (the session log has the reason); the "
                          "rest of the report is complete.")

    def _report_figure(self, title: str, draw):
        """One report figure as the writer's (title, png, width, caption), or — when drawing it
        raised — the section with no image and FIGURE_FAILED_NOTE: one picture of the report is
        not the report, the rule the lap strip follows (report_lapstrip_png), with the reader
        told rather than left with a gap."""
        try:
            got = draw()
        except Exception:  # noqa: BLE001 — a figure must never cost the user the whole document
            _log.exception("report figure %r could not be drawn; the report goes without it", title)
            return (title, b"", None, self.FIGURE_FAILED_NOTE)
        if len(got) == 3:                # the map: (png, width, caption)
            png, width, caption = got
            return (title, png, width, caption)
        png, chart_title, width, caption = got
        return (chart_title, png, width, caption)

    def copy_stats_summary(self):
        """File ▸ Export ▸ "Copy stats summary" (N13): the session's statistics as plain text on
        the clipboard, ready to paste into a chat.

        The text is `export_data.stats_summary_text` — the SAME builder the HTML report renders its
        groups from, off the same Session/SessionStats accessors the Stats page reads, so the
        pasted block, the exported page and the screen are three renderings of one computation.
        Guarded exactly like `_copy_share_card`: a clipboard hiccup reports and returns, it never
        disrupts the app."""
        if self._no_laps_to_export():
            return
        try:
            text = export_data.stats_summary_text(
                self.win.session, self.win._speed_unit,
                # The chapters that LOADED, not the ones asked for (#223's sweep — `_paths` is the
                # REQUEST). They differ when a chapter was skipped as not-video, and there the
                # difference is the whole point: with 3 requested and 2 loaded this title said
                # "recording 0060 · 3 chapters" while the report beside it said 2.
                title=self.win._loaded_label() or "")
            QApplication.clipboard().setText(text)
        except Exception:  # noqa: BLE001 — a clipboard failure must not disrupt the app
            _log.exception("stats summary not copied")
            self.win.statusBar().showMessage("could not copy the stats summary", self._status_ms)
            return
        self.win.statusBar().showMessage("stats summary copied — paste it into a chat", self._status_ms)
    @staticmethod
    def _export_failure_message(message: str, out_path: str) -> str:
        """Map a video-export failure to a sentence that names the CASE and a next action.

        The same job `_load_failure_message` does for the other end of the app, and for the same
        reason: what arrives here is an ffmpeg stderr tail, which is a diagnostic, not an
        explanation. It stays — behind Details, where a bug report can reach it.

        Only cases that can be told apart RELIABLY get their own sentence; everything else falls
        through to an honest generic. A wrong specific sentence is worse than a right vague one."""
        low = (message or "").casefold()
        if export_video.is_refused_for_space(message or ""):
            # The guard's own sentence already names the three numbers — needed, free, and
            # where — so it goes in front as written, followed by the way out.
            return (f"{message.strip()} Nothing was written. Free some space, or choose a folder "
                    f"on another disk, and export again.")
        if export_video.is_out_of_space(message or ""):
            # Only a render that ASKED THE DISK gets here. ffmpeg's "No space left on device"
            # alone is also printed by an overflowing queue inside ffmpeg with the disk nearly
            # empty (`export_video._NO_SPACE_MARKERS`), so the sentence is the renderer's, and it
            # carries what the disk said: the folder, its free space, the least the export needs.
            return (f"{export_video.disk_full_sentence(message)} Free some space, or choose "
                    f"somewhere else, and export again.")
        if "permission denied" in low or "operation not permitted" in low:
            return ("Pacer isn't allowed to write there. Choose a different folder — your Movies "
                    "or Desktop folder will work.")
        # ffmpeg says "No such file or directory" of a missing INPUT too, so its words alone are
        # not this case; every renderer failure begins "ffmpeg encode failed", though, so a
        # destination folder gone by render time is told apart by asking for the folder NOW.
        folder = os.path.dirname(os.path.abspath(out_path)) if out_path else ""
        if "no such file or directory" in low and (
                "ffmpeg" not in low or (folder and not os.path.isdir(folder))):
            return ("The folder you chose isn't there any more — it may have been moved, renamed "
                    "or unmounted. Choose another one and export again.")
        if export_video.is_truncated_footage(message or ""):
            return ("The footage stops partway through this clip, so only part of it could be "
                    "rendered. Nothing was written. The recording may still be copying from the "
                    "camera, or be damaged — try a lap further from the end of it.")
        if "cancelled" in low or "canceled" in low:
            return "The export stopped before it finished."
        # A style's layout with no place for what it must show in this frame size: the overlay's
        # own sentence ("overlay layout: the honesty stamp does not fit a 250×444 frame"), never
        # "the encoder stopped" — the encoder never started.
        at = (message or "").find(LAYOUT_PREFIX)
        if at >= 0:
            said = message[at:].split(" (", 1)[0].strip()
            return (f"{said[0].upper()}{said[1:]}. Nothing was written. Choose a larger "
                    "Resolution, another Shape, or the Classic style, and export again.")
        return ("The encoder stopped partway through. The details below are what it reported — "
                "Help ▸ Report a problem… if it keeps happening.")
    def _run_export(self, write, path: str) -> bool:
        """Run a writer (`write()`) under a failure guard; on failure show a warning dialog +
        statusbar note. Returns True on success.

        IT GUARDS `Exception`, NOT `OSError`, BECAUSE THESE WRITERS STOPPED BEING WRITERS. When
        this was written, every `write()` here did little more than format strings into a file, so
        the only thing that could go wrong was the file. It is now the front door to real
        computation: `write_report_html` runs the whole `SessionStats` reduction and the ideal-lap
        machinery INSIDE this try, over whatever a recording turned out to contain. Anything those
        raise — a ValueError out of a degenerate reduction, an IndexError on an empty channel — is
        not an OSError, so it went straight past this guard to the crash reporter: a Python
        traceback and a "Pacer stopped unexpectedly" dialog for a report the user simply cannot
        have, while the app's own plain-language failure dialog sat here unused.

        The two cases stay TOLD APART in the text. An OSError is about the FILE and names it, which
        is a thing the user can act on (wrong folder, full disk, unplugged drive); anything else is
        about the DATA, and promising a next action we cannot name would be a wrong specific
        sentence — the trap `_export_failure_message` exists to avoid. Either way the exception is
        logged with its traceback, so a bug report can still reach what went wrong."""
        try:
            write()
        except OSError as exc:
            QMessageBox.warning(self.win, "Export failed",
                                f"Could not write {os.path.basename(path)}:\n{exc}")
            self.win.statusBar().showMessage(f"export failed: {exc}", self._status_ms)
            return False
        except Exception as exc:  # noqa: BLE001 — see the docstring: the alternative is a crash dialog
            _log.exception("export of %s failed", os.path.basename(path))
            box = QMessageBox(QMessageBox.Warning, "Export failed",
                              f"{APP_NAME} couldn't build {os.path.basename(path)} from this "
                              f"recording, so nothing was written.\n\nHelp ▸ Report a problem… if "
                              f"it keeps happening.", parent=self.win)
            box.setDetailedText(f"{type(exc).__name__}: {exc}")
            box.addButton(QMessageBox.Close)
            box.exec()
            self.win.statusBar().showMessage(
                f"export failed: {type(exc).__name__}", self._status_ms)
            return False
        return True
    @staticmethod
    def grab_png(widget) -> bytes:
        """Render a live widget to PNG bytes (QWidget.grab → QImage → in-memory PNG) for
        the report's embedded snapshots."""
        image = widget.grab().toImage()
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        image.save(buf, "PNG")
        return bytes(buf.data())
    def export_share_card(self):
        """File ▸ Export ▸ "Lap card (image)…": render the card and save it as a PNG."""
        image = self.win._build_share_card()
        if image is None:
            self.win.statusBar().showMessage("no verified lap to make a shareable card for", self._status_ms)
            return
        # The card shows the BEST lap, so its name says which: `GX010067_lap14_card.png`.
        best = self.win.session.best_lap_id() if hasattr(self.win, "session") else None
        suffix = f"_lap{lap_label(best)}_card.png" if best is not None else "_lap_card.png"
        path = self._export_save_path("Export lap card", suffix, "PNG images (*.png)")
        if not path:
            return
        if self._run_export(lambda: self.win._save_card_png(image, path), path):
            self.win.statusBar().showMessage(f"saved {os.path.basename(path)}", self._status_ms)
    # The lap-strip card (share_lapstrip): the lap card's twin, so its gate and its save are the
    # lap card's too. Imported locally: these two methods are its only users here.
    def gate_lapstrip(self, has_laps: bool) -> None:
        """Grey "Lap-strip card…" with the lap card's reasons, plus its own: under two clean laps
        there is nothing for a strip to compare."""
        from . import share_lapstrip
        action = getattr(self.win, "_lapstrip_action", None)
        if action is None:
            return
        blocked = has_laps and self.win._share_card_blocked()
        few = has_laps and not blocked and \
            share_lapstrip.clean_lap_count(self.win.session) < share_lapstrip.MIN_CLEAN_LAPS
        reason = (self._NO_LAPS_REASON if not has_laps else
                  self._PROVISIONAL_REASON if blocked else share_lapstrip.FEW_LAPS_REASON)
        self.win._gate_action(action, has_laps and not blocked and not few, reason)
    def export_lapstrip_card(self):
        """File ▸ Export ▸ "Lap-strip card…": every lap as a bar, saved as a PNG where the dialog
        says. Named for the best lap it highlights, like the lap card: `GX010067_lapstrip_best_lap14.png`."""
        from . import share_lapstrip
        data = share_lapstrip.lapstrip_data(self.win.session, unit=self.win._speed_unit) \
            if hasattr(self.win, "session") else None
        if data is None or not data.offered:
            why = ("fewer than two clean laps — nothing for a lap strip to compare"
                   if data is not None and not data.blocked
                   else "no verified laps to make a lap strip for")
            self.win.statusBar().showMessage(why, self._status_ms)
            return
        image = share_lapstrip.render_lapstrip(data, palette=theme.active_palette())
        best = data.best_lap_id
        suffix = f"_lapstrip_best_lap{lap_label(best)}.png" if best is not None else "_lapstrip.png"
        path = self._export_save_path("Export lap-strip card", suffix, "PNG images (*.png)")
        if not path:
            return
        if self._run_export(lambda: share_lapstrip.save_lapstrip_png(image, path), path):
            self.win.statusBar().showMessage(f"saved {os.path.basename(path)}", self._status_ms)
    # ------------------------------------------------- video-overlay export (F9)
    # File ▸ Export overlay video Qt side (renderer is event-loop-free in export_video.py).

    # Resolution maps to OverlayConfig.out_height — the target SHORT side, which on the landscape
    # shapes this export used to be limited to is the height, and on a 9:16 clip is the width, so
    # "1080p" means the 1080 the user pictures either way. Never upscales past source; "Source" is
    # a huge sentinel clamped back. Quality maps to OverlayConfig.quality.
    # "1080p" resolution + "High" quality is the default.
    _EXPORT_RES_OPTIONS = [
        ("720p", 720), ("1080p", 1080), ("1440p", 1440), ("Source (no downscale)", 99999),
    ]
    # WHAT gets rendered. This is the row that decides whether the export takes ninety seconds or
    # half an hour, which makes it the first one in the dialog: the loudest complaint about any
    # overlay exporter is render time, and the cheapest answer to it is to render one lap.
    _EXPORT_SCOPE_OPTIONS = [
        ("This lap", export_video.SCOPE_THIS_LAP),
        ("Best lap", export_video.SCOPE_BEST_LAP),
        ("All laps — one file per lap", export_video.SCOPE_ALL_LAPS),
        ("Full session — the whole recording", export_video.SCOPE_SESSION),
    ]
    # The frame's SHAPE, and how the footage meets it. Vertical and square are here because the
    # documented way to get them today is to render 16:9, take it into another tool and rescale
    # the telemetry by hand — one published recipe renders the overlay onto a solid blue and
    # chroma-keys it in Premiere purely to move it. Doing the reflow in the renderer is what makes
    # that unnecessary.
    _EXPORT_ASPECT_OPTIONS = [
        ("Source — as filmed", export_video.ASPECT_SOURCE),
        ("9:16 — vertical", export_video.ASPECT_9_16),
        ("1:1 — square", export_video.ASPECT_1_1),
    ]
    _EXPORT_FIT_OPTIONS = [
        ("Crop to fill the frame", export_video.FIT_CROP),
        ("Fit the whole picture (bars)", export_video.FIT_FIT),
    ]
    # Footage + overlay, or the overlay ALONE over transparency for finishing in Resolve/Premiere.
    # The two alpha rows are the two formats an NLE actually takes an alpha track in. The FIRST row
    # is the one the dialog opens on, every time: see `_ask_export_options` for why this row alone
    # is not remembered.
    _EXPORT_CONTENT_COMPOSITE = "composite"
    _EXPORT_CONTENT_OPTIONS = [
        ("Footage with the overlay burned in", _EXPORT_CONTENT_COMPOSITE),
        ("Overlay only — ProRes 4444 (alpha)", export_video.ALPHA_PRORES),
        ("Overlay only — PNG sequence (alpha)", export_video.ALPHA_PNG),
    ]
    _EXPORT_QUALITY_OPTIONS = [
        ("High — larger file", "high"), ("Standard — smaller file", "standard"),
    ]
    # Run-up / run-off: the same number of seconds of footage before the start line and after the
    # finish, so a clip does not begin and end on a hard cut at the timing line. It is one choice
    # rather than two because it is one thing — "give the lap some room" — and the value goes to
    # export_video.build_lap_spec, which widens the window BEFORE the video source is resolved.
    _EXPORT_LEAD_OPTIONS = [
        ("None — cut on the timing line", 0.0), ("5 s before and after", 5.0),
        ("10 s before and after", 10.0),
    ]
    # The picker's choices persist across relaunches like every other UI choice (the unit, the
    # palette, the lap-panel tab). Kept as call-site keys on prefs' generic get/set: they mean
    # nothing outside this dialog, and prefs.py is a store, not a registry of every screen's state.
    # Contents has NO key: earlier builds stored it as `export_content_idx`, and where that key is
    # still in a prefs.json it is never read and never rewritten.
    _PREF_EXPORT_RES = "export_res_idx"
    _PREF_EXPORT_QUALITY = "export_quality_idx"
    _PREF_EXPORT_LEAD = "export_lead_idx"
    _PREF_EXPORT_SCOPE = "export_scope_idx"
    _PREF_EXPORT_ASPECT = "export_aspect_idx"
    _PREF_EXPORT_FIT = "export_fit_idx"
    # The overlay's look: Classic (the default, untouched) or the Broadcast graphic
    # (`overlay/broadcast.py`) — the same figures, drawn as a tower, a Δ bar and a big speed.
    _PREF_EXPORT_STYLE = "export_style_idx"
    _EXPORT_STYLE_OPTIONS = [
        ("Classic", broadcast.STYLE_CLASSIC),
        ("Broadcast — timing tower, Δ bar, big speed", broadcast.STYLE_BROADCAST),
    ]
    # The dialog's description line, which names the plates the chosen style burns.
    _EXPORT_STYLE_DESC = {
        broadcast.STYLE_CLASSIC: "Burns the overlays into your footage: g-meter, Δ / speed, map "
                                 "inset and the lap strip.",
        broadcast.STYLE_BROADCAST: "Burns the overlays into your footage: a timing tower, a Δ bar, "
                                   "a big speed, the g-meter and a bigger minimap that names its "
                                   "corners.",
    }
    # ...and with the pass row chosen: what a pass clip burns (no lap clock, no Δ: PassSubject).
    # `{noun}` is what the pass goes through, "corner" or "sector" (`_pass_noun`).
    _PASS_STYLE_DESC = {
        broadcast.STYLE_CLASSIC: "Burns the overlays into your footage: g-meter, speed, map inset "
                                 "and a strip naming the lap and the {noun}, with the {noun}'s "
                                 "time from its exit.",
        broadcast.STYLE_BROADCAST: "Burns the overlays into your footage: a tower naming the lap "
                                   "and the {noun}, with the {noun}'s time from its exit, a big "
                                   "speed, the g-meter and a bigger minimap that names its corners.",
    }
    # The Run-up row's reason while it is greyed for the pass row.
    _PASS_LEAD_TIP = "A pass carries its own lead-in and 1 s after the exit."
    # Each remembered row's DEFAULT, by its pref key: what a first launch opens on, and what "Use
    # defaults" puts back — one table, so the two can never disagree about what the defaults are.
    # (Contents' default is its first row, which it opens on every time.)
    _EXPORT_DEFAULT_INDEX = {
        _PREF_EXPORT_SCOPE: 0, _PREF_EXPORT_LEAD: 0, _PREF_EXPORT_ASPECT: 0, _PREF_EXPORT_FIT: 0,
        _PREF_EXPORT_RES: 1, _PREF_EXPORT_QUALITY: 0,   # 1080p, High
        _PREF_EXPORT_STYLE: 0,                           # Classic
    }
    # The Extras row: opt-in overlay elements (`studio/overlay/`), each a remembered checkbox. In
    # Classic they open UNCHECKED until the user ticks them, so the Classic export stays what it
    # was; in Broadcast they open checked (`_PREF_EXPORT_STYLE_EXTRAS` below).
    _PREF_EXPORT_GHOST = "export_ghost"
    _GHOST_LABEL = "Ghost of your best lap"
    _GHOST_TIP = ("A ring on the minimap where your best lap was at the same lap time — the lap "
                  "strip's Δ, drawn as a place on track. No ghost on the best lap's own file.")
    _PREF_EXPORT_CARDS = "export_corner_cards"
    # THE EXTRAS ARE REMEMBERED PER STYLE (the recorded decision: "OFF in Classic, ON in the
    # Broadcast style"). Broadcast's ticks live under their own keys and open ON; Classic's keep
    # the keys above and open OFF. Switching the Style row swaps the two boxes to that style's
    # ticks, and Export writes only the chosen style's — so nothing ticked in Broadcast can carry
    # over into a Classic export.
    _PREF_EXPORT_STYLE_EXTRAS = {
        broadcast.STYLE_CLASSIC: ((_PREF_EXPORT_GHOST, False), (_PREF_EXPORT_CARDS, False)),
        broadcast.STYLE_BROADCAST: (("export_broadcast_ghost", True),
                                    ("export_broadcast_corner_cards", True)),
    }
    _CARDS_LABEL = "Corner cards"
    _CARDS_TIP = ("As you leave each corner, a card for two seconds: its minimum speed and its time "
                  "against the same corner on your best lap — the Corners table's numbers (its "
                  "Δbest column, not the session-best corner time). Inside timing precision "
                  f"({corner_cards.PRECISION_S:.2f} s) it reads \"≈ best\"; a corner not matched "
                  "on track gets no card. With another recording loaded as the reference, the "
                  "time is against that recording's lap and reads \"≈ ref\" under "
                  f"{corner_delta_step(True):.2f} s, within what one lap of each reads apart with "
                  "nothing changed.")
    # Added to the tooltip while the start/finish line is unconfirmed: the cards keep their Δ (a
    # corner time does not depend on the line), but WHICH lap is the best is a lap-time verdict.
    _CARDS_PROVISIONAL = (" Lap times are provisional until the start/finish line is confirmed, "
                          "so the best lap these cards compare against is chosen from them.")
    # The index of "Source (no downscale)" in `_EXPORT_RES_OPTIONS`.
    _EXPORT_RES_SOURCE = 3
    # The compare export's own two rows. LAYOUT first because it decides the shape of the file:
    # stacked panes make a roughly 8:9 frame a phone shows nearly full-screen, side-by-side an
    # ultrawide one. There is no scope row (a compare is OF a pair, so the pair IS the scope) but
    # for a compare at a pass, "This pass" or "Both whole laps" (`_ask_compare_options`), no
    # run-up row (padding is footage outside the lap, where there is no track position to lock to)
    # and no shape/contents row (both panes must be the same shape, and an overlay-only compare has
    # no second picture to lock to) — every row the single-lap picker has that this one lacks is a
    # choice the feature does not offer rather than one it forgot.
    _COMPARE_LAYOUT_OPTIONS = [
        ("Stacked — one above the other", export_compare.LAYOUT_STACK),
        ("Side by side", export_compare.LAYOUT_SIDE),
    ]
    _PREF_COMPARE_LAYOUT = "export_compare_layout_idx"
    # THE COMPARE HAS ITS OWN RESOLUTION, remembered apart from the overlay's. They used to share
    # `export_res_idx`, so the owner's overlay at "Source" opened his next comparison at Source too:
    # two 4K panes, 3840x4320, H.264 level 6.0, 299 MB and 3:41 for one lap (JOURNEY-3). A compare
    # is two pictures in one frame and is shared to phones, so it opens on 1080p panes (1920x2160)
    # until the user picks another row here. Its "Source" is capped at a 4K frame
    # (`export_video.MAX_FRAME_PIXELS`), and says so: two full 4K panes are not a file anything
    # he shares it to will play.
    _COMPARE_RES_OPTIONS = [
        ("720p", 720), ("1080p", 1080), ("1440p", 1440), ("Source — up to a 4K frame", 99999),
    ]
    # Both Output lines' reason when "Source" is held to one 4K frame (`export_video.fits_one_frame`).
    _CAP_NOTE = " — capped at one 4K frame, the biggest H.264 picture phones and messaging apps play"
    _PREF_COMPARE_RES = "export_compare_res_idx"
    _COMPARE_RES_DEFAULT = 1                       # 1080p panes
    # SIZE ESTIMATE. The dialog sells a file-size trade-off ("larger file" / "smaller file"), so it
    # has to put a number on it — the two presets really are ~3x apart. The estimate is derived per
    # encoder, never a stored megabyte figure, because the encoder choice is a property of the
    # MACHINE (a box where no VideoToolbox session opens falls back to libx264 and lands several
    # times larger for the same preset):
    #   * VideoToolbox is bitrate-targeted, so export_video.vt_target_bitrate IS the answer;
    #   * libx264 is CRF-targeted and has no bitrate to read, so `export_video.X264_BPP` holds bits
    #     per pixel per frame measured on real GoPro footage with the overlays burned in. A CRF
    #     stream's real size follows how much the picture MOVES, so this is an order of magnitude,
    #     and the dialog says "about".
    # Both live in `export_video.estimate_output_bytes`, which the free-space guard reads too, so
    # the size this dialog promises and the space the guard requires come from one model.
    # Assumed for the SOURCE shape's width; GoPro's landscape modes are 16:9. The two fixed
    # shapes state their own ratio, so this is only the fallback the estimate needs when it is not
    # allowed to run an ffprobe to ask the footage.
    _SOURCE_ASPECT_GUESS = 16 / 9
    def _export_pref_index(self, key: str, default: int, count: int) -> int:
        """One persisted combo index, clamped into `[0, count)` — the guarded-accessor shape the
        rest of prefs uses, so a stale value from an older build (a resolution that no longer
        exists) opens the dialog on the default instead of raising."""
        try:
            value = prefs.get(key, default)
        except Exception:  # noqa: BLE001 — an unreadable pref never blocks an export
            return default
        return value if isinstance(value, int) and 0 <= value < count else default

    @staticmethod
    def _not_counted_ids(session) -> frozenset:
        """`data_quality.not_counted_ids` for a dialog line; empty without a session."""
        return data_quality.not_counted_ids(session) if session is not None else frozenset()

    @staticmethod
    def _not_counted_line(session, lap) -> str:
        """`data_quality.not_counted_line` for an export dialog, "" when it cannot be read: a
        caption never blocks the export it describes."""
        try:
            return data_quality.not_counted_line(session, lap) if session is not None else ""
        except Exception:  # noqa: BLE001
            return ""

    def _ghost_reason(self, scope: str, lap: int) -> str | None:
        """Why this scope can draw no ghost (the checkbox's disabled tooltip), or None. All laps
        asks only the session-wide rules: each lap's file then answers for itself, and the best
        lap's own file has none."""
        session = getattr(self.win, "session", None)
        if scope == export_video.SCOPE_PASS:
            return ghost.PASS_REASON
        try:
            if scope == export_video.SCOPE_SESSION:
                return ghost.unavailable_reason(session, None, follow_laps=True)
            if scope == export_video.SCOPE_ALL_LAPS:
                return ghost.unavailable_reason(session, None)
            target = session.best_lap_id() if scope == export_video.SCOPE_BEST_LAP else lap
            return ghost.unavailable_reason(session, target)
        except Exception:  # noqa: BLE001 — an option's availability never blocks an export
            _log.warning("ghost availability unknown", exc_info=True)
            return "The ghost is not available for this session."

    def _cards_reason(self, scope: str, lap: int, cid: int | None = None) -> str | None:
        """Why this scope can show no corner card (the checkbox's disabled tooltip), or None. All
        laps asks only the session-wide rules: each lap's file then answers for itself. A sector's
        pass (`cid` a `stats.sector_key`) shows none: a pass clip keeps to its own stretch."""
        session = getattr(self.win, "session", None)
        if scope == export_video.SCOPE_PASS and cid is not None and sector_col(cid) is not None:
            return corner_cards.SECTOR_PASS_REASON
        try:
            if scope == export_video.SCOPE_SESSION:
                return corner_cards.unavailable_reason(session, None, follow_laps=True)
            if scope == export_video.SCOPE_ALL_LAPS:
                return corner_cards.unavailable_reason(session, None)
            target = session.best_lap_id() if scope == export_video.SCOPE_BEST_LAP else lap
            return corner_cards.unavailable_reason(session, target)
        except Exception:  # noqa: BLE001 — an option's availability never blocks an export
            _log.warning("corner-card availability unknown", exc_info=True)
            return "Corner cards are not available for this session."

    def _ghostless_files(self, lap: int, reason=ghost.unavailable_reason,
                         what: str = "ghost") -> str:
        """For an All-laps batch with an Extras option ticked: one line naming the files that will
        carry no `what` and why ("" when every file has it). `reason` is the option's own
        per-lap rule — the ghost's by default (the best lap's own file is always one), the corner
        cards' for a lap with no corner matched on track."""
        session = getattr(self.win, "session", None)
        try:
            ids = export_video.scope_lap_ids(session, export_video.SCOPE_ALL_LAPS, lap)
            why = {i: reason(session, i) for i in ids}
        except Exception:  # noqa: BLE001 — a hint never blocks an export
            _log.warning("%s availability per lap unknown", what, exc_info=True)
            return ""
        out = [w for w in why.values() if w is not None]
        if not out:
            return ""
        return (f"No {what} in {plural(len(out), 'file')} of {len(ids)}: " + " ".join(out))

    def _remember_export_prefs(self, indices: dict) -> None:
        """Persist the picker's choices, `{pref key: combo index}`. Fully guarded, like
        set_last_dir: remembering a preference must never disrupt an export the user has already
        confirmed. It takes a mapping rather than a positional list because the picker grew from
        three rows to seven, and a seven-argument call site is a bug waiting for the eighth."""
        try:
            for key, index in indices.items():
                prefs.set(key, int(index))
        except OSError as exc:
            _log.warning("export preset not remembered (%r)", exc)
    def _export_clip_seconds(self, lap: int, lead: float) -> float:
        """How long the exported CLIP is for `lap` with `lead` seconds of run-up and run-off —
        measured through `export_video.lap_window_for_export`, the same funnel the render resolves
        its window with, so the dialog's estimate can never describe a different clip than the one
        that gets rendered.

        That matters most where the padding cannot be honoured: on the first and last lap of a
        recording the funnel clamps to the footage, and `lap_time + 2 * lead` would then promise
        seconds of run-up that do not exist. NaN when the lap has no usable window (the size hint
        then says nothing at all, which is its contract)."""
        session = getattr(self.win, "session", None)
        if session is None:
            return float("nan")
        try:
            win = export_video.lap_window_for_export(session, lap, lead_in=lead, lead_out=lead)
        except Exception:  # noqa: BLE001 — a hint must never take down the dialog it annotates
            return float("nan")
        return (win[1] - win[0]) if win is not None else float("nan")
    def _estimate_frame_size(self, out_height: int, aspect: str) -> tuple[int, int]:
        """The (W, H) the estimate reasons about until the footage's frame is known (then it is
        `frame_geometry`'s): the resolution row's SHORT side in the chosen shape."""
        ratio = export_video.ASPECT_RATIOS.get(aspect) or self._SOURCE_ASPECT_GUESS
        if ratio <= 1:                       # portrait or square: the short side is the WIDTH
            return int(out_height), int(round(out_height / ratio))
        return int(round(out_height * ratio)), int(out_height)
    def _export_size_hint(self, dur: float, out_height: int, quality: str,
                          aspect: str = export_video.ASPECT_SOURCE,
                          content: str = _EXPORT_CONTENT_COMPOSITE, files: int = 1,
                          source: tuple[int, int, float] | None = None,
                          fit: str = export_video.FIT_CROP, on_line: bool = False,
                          h264: str | None = None) -> str:
        """The second line of the picker's hint: about how big this export lands, how many frames
        it has to render, WHICH encoder will do it and about how long that takes. Derived (see
        X264_BPP and `export_video.RENDER_FPS`) — never a stored megabyte figure, because the
        encoder is a property of the machine. "" when there is nothing honest to say: an unknown
        lap duration, or "Source" before the footage's frame is known.

        `source` is the footage's (width, height, fps) when the picker has it
        (`export_video.known_video_size`, filled behind the dialog). With it every row is sized
        through `frame_geometry`, the renderer's own rule, never-upscale clamp included; without
        it the fixed resolutions assume a 16:9 frame and "Source" says nothing.

        `dur` is the length of ONE file and `files` how many of them an All-laps batch writes, so
        the frame count and the megabytes are both the batch's total — the number that decides
        whether this is worth starting. `on_line`: each file is cut on its finish line, so the
        render adds its finish frame (`export_video.planned_frames`).

        AN ALPHA EXPORT IS A DIFFERENT ORDER OF MAGNITUDE AND HAS TO SAY SO. ProRes 4444 is
        intra-only at roughly 330 Mbit/s for 1080p30 (Apple's own figure, which is where
        `PRORES_4444_BPP` comes from) — about twenty-five times the H.264 this dialog otherwise
        estimates. A user who pointed that at a thirty-minute recording reported 140 GB and six
        hours. The row is worth offering; offering it without the number is the trap.

        THE TIME IS ABOUT, AND SAYS WHOSE. It comes from throughput measured per path on the
        development Mac, and the progress dialog's live ETA, timed on the real render, is the one
        to trust. Until the ProRes probe has run (behind the dialog) the line names the format
        rather than an encoder and leaves the time out. Until the H.264 one has, a burned-in
        export has neither a size nor a time to state (VideoToolbox is priced by bitrate, libx264
        by the picture), so the line is "" and the hint says it is measuring; `h264` prices it
        with that encoder instead, for the room the hint keeps for the figure."""
        if not (dur > 0):
            return ""
        fps = export_video.OverlayConfig.fps_cap or 30.0
        if source is not None:
            src_w, src_h, src_fps = source
            # overlay_only too: that render divides the source rate (29.97 off 59.94) rather
            # than capping it, and the hint quotes the rate the renderer will use.
            cfg = export_video.OverlayConfig(
                out_height=out_height, aspect=aspect, frame_fit=fit,
                overlay_only=content != self._EXPORT_CONTENT_COMPOSITE)
            geo = export_video.frame_geometry(src_w, src_h, cfg)
            out_w, out_h = geo.out_w, geo.out_h
            fps = export_video.resolve_fps(cfg, src_fps)
        elif out_height >= 99999:
            return ""
        else:
            out_w, out_h = self._estimate_frame_size(out_height, aspect)
        files = max(1, int(files))
        frames = export_video.planned_frames(dur, fps, on_line) * files
        if content == export_video.ALPHA_PRORES:
            codec = export_video.known_alpha_encoder()      # None until the probe has settled
            encoder = f"ProRes 4444 via {codec}" if codec else "ProRes 4444"
        elif content == export_video.ALPHA_PNG:
            codec, encoder = content, "a PNG sequence"
        else:
            codec = encoder = h264 or export_video.known_encoder()   # None until the probe answers
            if codec is None:
                return ""
        size = export_video.fmt_bytes(export_video.estimate_output_bytes(
            out_w, out_h, fps, dur, quality, codec or content) * files)
        took = export_video.estimate_render_seconds(out_w, out_h, frames, codec) if codec else None
        timing = f", about {fmt_hms(took)} to render" if took else ""
        return (f"About {size} — {frames} frames to render at {fps:g} fps "
                f"with {encoder}{timing}.{self._size_caveat(codec)}")
    @staticmethod
    def _size_caveat(codec: str | None) -> str:
        """The hedge after the size, said only where it is true. libx264 is CRF-driven and spends
        what the picture asks for, so its size does follow the footage. VideoToolbox does not: it
        wrote the same 4.36 Mbit/s for a 1080p lap of night-time MK and of daytime SD19 (EXP-7),
        and the alpha formats are priced per pixel of overlay."""
        return " Real size follows how much the footage moves." if codec == export_video.SW_H264 else ""
    def _compare_size_hint(self, dur: float, out_height: int, quality: str, layout: str,
                           source: tuple[int, int, float] | None = None,
                           ends_on_finish: bool = True, h264: str | None = None) -> str:
        """The compare picker's size-and-time line, from the COMPARE'S OWN frame: two panes, side
        by side (twice as wide) or stacked (twice as tall), sized by `compare_geometry`, the
        renderer's own rule. The bytes come from the one size model every export reads; the time
        from `export_compare.estimate_compare_seconds`, which costs the second decode every
        frame pays. "" when there is nothing honest to say: an unknown lap A, or "Source" before
        pane A's footage frame is known (`source`, filled behind the dialog), or before the H.264
        probe has answered (`export_video.known_encoder`); `h264` prices it with that encoder
        instead, for the room the hint keeps for the figure.

        The frames are the render's own: lap A's window plus its FINISH FRAME, the one that shows
        both lap times (`with_finish_frame`), which a compare always ends on. The size is priced on
        the frame the cap leaves (`compare_geometry`), which is also the frame VideoToolbox's
        hardware encoder takes and `VT_H264_YIELD` was measured on. A compare at one pass
        (`ends_on_finish` False) is cut on no line: its frames are its longer clip's."""
        if not (dur > 0):
            return ""
        if source is None and out_height >= 99999:
            return ""
        src_w, src_h, src_fps = source if source is not None else (0, 0, 0.0)
        cfg = export_compare.CompareConfig(out_height=out_height, layout=layout)
        # Pane B is fitted into pane A's shape, so A's frame alone decides the output; an unknown
        # frame is `compare_geometry`'s own 16:9 guess.
        geo = export_compare.compare_geometry((src_w, src_h), (src_w, src_h), cfg)
        fps = export_video.resolve_fps(cfg, src_fps)
        frames = export_video.planned_frames(dur, fps, ends_on_finish=ends_on_finish)
        codec = h264 or export_video.known_encoder()   # None until the H.264 probe answers
        if codec is None:
            return ""
        size = export_video.fmt_bytes(export_video.estimate_output_bytes(
            geo.out_w, geo.out_h, fps, frames / fps, quality, codec))
        took = export_compare.estimate_compare_seconds(geo.out_w, geo.out_h, frames, codec)
        timing = f", about {fmt_hms(took)} to render" if took else ""
        how = "side by side" if geo.layout == export_compare.LAYOUT_SIDE else "one above the other"
        cap = self._CAP_NOTE if geo.capped else ""
        return (f"Output: {geo.out_w}x{geo.out_h}, two panes of {geo.pane_w}x{geo.pane_h} {how}"
                f"{cap}.  About {size} — {plural(frames, 'frame')} to render at {fps:g} fps with "
                f"{codec}{timing}; every frame decodes both laps' footage."
                f"{self._size_caveat(codec)}")
    def _export_session_seconds(self) -> float:
        """How long a FULL-SESSION export runs: the footage's own length, through the same
        accessor the renderer builds its window from, so the estimate and the render cannot
        describe different clips. NaN when the recording cannot state one (no chapter table),
        which is also exactly when the scope is refused."""
        session = getattr(self.win, "session", None)
        if session is None:
            return float("nan")
        try:
            total = export_video.footage_duration(session)
        except Exception:  # noqa: BLE001 — a hint must never take down the dialog it annotates
            return float("nan")
        return float(total) if total else float("nan")
    def _pass_seconds(self, lap: int, cid: int) -> float:
        """How long lap `lap`'s pass clip through corner `cid` runs: the window the render resolves
        (`export_video.pass_window_for_export`, `CornerModel.pass_clip`'s). NaN without one."""
        try:
            win = export_video.pass_window_for_export(self.win.session, lap, cid)
        except Exception:  # noqa: BLE001 — a hint must never take down the dialog it annotates
            return float("nan")
        return (win[3] - win[0]) if win is not None else float("nan")

    def pass_subject(self) -> tuple[int, int, str] | None:
        """(lap, cid, label) of the pass being studied (`CentralView.pass_subject`: the pass last
        played from a Quickest row, a Corners lap row, a CORNERS BY LAP cell, the slow-lap cue or
        the corner walk; a lap picked elsewhere forgets it), or None. The overlay picker offers its
        "This pass" scope only then."""
        view = getattr(self.win, "view", None)
        read = getattr(view, "pass_subject", None) if view is not None else None
        try:
            got = read() if callable(read) else None
        except Exception:  # noqa: BLE001 — an offer never blocks the export it adds to
            return None
        if got is None:
            return None
        lap, cid, label = got
        return int(lap), int(cid), label

    def _scope_plan(self, scope: str, lap: int, lead: float,
                    pass_: tuple[int, str] | None = None) -> tuple[float, int, str]:
        """(seconds per file, number of files, what the scope renders) for the hint — resolved
        through `export_video`'s own scope machinery rather than re-counted here, so a row the
        dialog offers is a render the module can actually build."""
        session = getattr(self.win, "session", None)
        if scope == export_video.SCOPE_SESSION:
            return self._export_session_seconds(), 1, "the whole recording"
        if session is None:
            return float("nan"), 1, "this lap"
        if scope == export_video.SCOPE_PASS and pass_ is not None:
            return self._pass_seconds(lap, pass_[0]), 1, (
                f"lap {lap_label(lap)}'s pass through {pass_[1]}, from its lead-in to 1 s after "
                f"the exit, with the {_pass_noun(pass_[0])}'s time and no lap clock")
        try:
            ids = export_video.scope_lap_ids(session, scope, lap)
        except Exception:  # noqa: BLE001 — a hint must never take down the dialog it annotates
            ids = [lap]
        if not ids:
            return float("nan"), 1, "nothing — this recording has no lap for that choice"
        if len(ids) == 1:
            return self._export_clip_seconds(ids[0], lead), 1, f"lap {lap_label(ids[0])}"
        # An All-laps batch renders each lap separately; the AVERAGE length is what makes the
        # total honest, and the total is what the user is deciding about.
        spans = [self._export_clip_seconds(i, lead) for i in ids]
        good = [s for s in spans if math.isfinite(s) and s > 0]
        mean = (sum(good) / len(good)) if good else float("nan")
        return mean, len(ids), f"{len(ids)} laps, one file each"
    def _scope_line(self, scope: str, lap: int) -> str:
        """The overlay dialog's line above its form: the lap(s) the Export row WILL render, from
        the same `scope_lap_ids` the hint's "Renders …" reads. "Selected lap 13 · 0:44.160",
        "Best lap 4 · 0:44.950", "All laps · 14 laps, one file each", "Full session · 9:12".

        It said "Selected lap …" whatever the row, so on "Best lap" (the path "my best lap as a
        video") it showed another lap's time above "Renders lap 4." (discovery 11)."""
        session = getattr(self.win, "session", None)
        if scope == export_video.SCOPE_SESSION:
            secs = self._export_session_seconds()
            return "Full session" + (f"  ·  {fmt_hms(secs)}" if math.isfinite(secs) else "")
        ids = [lap]
        if session is not None and scope != export_video.SCOPE_THIS_LAP:
            try:
                ids = export_video.scope_lap_ids(session, scope, lap)
            except Exception:  # noqa: BLE001 — a caption never blocks the export it describes
                ids = [lap]
        name = {export_video.SCOPE_BEST_LAP: "Best lap",
                export_video.SCOPE_ALL_LAPS: "All laps"}.get(scope, "Selected lap")
        if not ids:
            return f"{name}  ·  none in this recording"
        # A ⚠ lap among those that WILL render says so (the clip tags it too): it stays
        # exportable — it is his footage — but never reads as a lap that counts. Best lap never
        # carries it (the best is never a ⚠ lap); All laps says how many; a full session nothing.
        if scope == export_video.SCOPE_ALL_LAPS:
            uncounted = sum(1 for i in ids if i in self._not_counted_ids(session))
            return (f"{name}  ·  {plural(len(ids), 'lap')}, one file each"
                    + (f"  ·  ⚠ {uncounted} {data_quality.NOT_COUNTED}" if uncounted else ""))
        # lap_time is a cheap pacer-free accessor (no ffprobe).
        dur = session.lap_time(ids[0]) if session is not None else float("nan")
        why = "" if scope == export_video.SCOPE_BEST_LAP else self._not_counted_line(session, ids[0])
        return (f"{name} {lap_label(ids[0])}  ·  {fmt_time(dur)}"
                + (f"  ·  {why}" if why else ""))

    def _export_dialog(self, title: str) -> tuple[QDialog, QVBoxLayout]:
        """The shell both export pickers are built in: a titled modal at ONE measure, a panel
        header, and a body column laid out on the panel gutter. Returns the dialog and the column
        to fill.

        ONE MEASURE, because they are one kind of surface. The two pickers had grown byte-identical
        fourteen-line preambles, which is how a second dialog ends up 40 px narrower than the first
        for no reason anyone chose. 460 px is an EXTENT — a dialog's own measure, the width at
        which a "Run-up / run-off" label and its combo sit on one line — and the SPACE scale has
        nothing to say about extents (see tests/test_design_system.py, which exempts this ONE
        surface by name rather than the two that used to spell it out separately).

        A CONTROL surface, not a prose one: combos, a form and a button row. So the body takes the
        panel gutter (SPACE_M) rather than the Help cards' SPACE_XL reading inset — these dialogs
        are operated, not read."""
        dlg = QDialog(self.win)
        dlg.setWindowTitle(title)
        dlg.setMinimumWidth(460)
        root = QVBoxLayout(dlg)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QLabel(title)
        header.setProperty("role", "PanelHeader")
        root.addWidget(header)
        body = QWidget(dlg)
        col = QVBoxLayout(body)
        col.setContentsMargins(theme.SPACE_M, theme.SPACE_M, theme.SPACE_M, theme.SPACE_M)
        col.setSpacing(theme.SPACE_M)
        root.addWidget(body)
        return dlg, col

    @staticmethod
    def _redraw_once_probed(dlg: QDialog, src: str, redraw) -> QTimer | None:
        """Learn the footage's frame and the H.264 and ProRes encoders BEHIND a picker (an
        ffprobe and two VideoToolbox sessions: ~45 ms, ~0.5 s and ~0.3 s here) and call `redraw`
        as each answer lands, the last one included. Until then its hint says what it can, and
        that it is measuring (`_measuring_line`). Returns the poll timer for the caller to stop
        when the dialog closes, or None when everything was already known.

        IT WAITS AS LONG AS THE DIALOG IS OPEN. It used to stop waiting after 10 s, redrawing the
        hint as it stood — and a probe that answered after that was never shown. On a loaded Mac
        the first dialog of a session waited 13.7 s for its ffprobe, so the owner's first export at
        "Source" quoted no size and no time, and the second one did (REG-4, EXP-11). A 50 ms poll
        on a dialog the user is reading costs nothing; the caller stops it on close."""
        export_video.warm_export_probes(src)
        if export_video.export_probes_ready(src):
            return None
        poll = QTimer(dlg)
        poll.setInterval(50)

        def _known():
            return (export_video.known_video_size(src) is not None,
                    export_video.videotoolbox_state(), export_video.prores_videotoolbox_state())
        seen = [_known()]

        def _probes_in():
            # Each answer as it lands: the burned-in figure needs only the frame and H.264, and
            # should not wait for the ProRes session behind them.
            if export_video.export_probes_ready(src):
                poll.stop()
                redraw()
            elif _known() != seen[0]:
                seen[0] = _known()
                redraw()
        poll.timeout.connect(_probes_in)
        poll.start()
        return poll

    # What the hint says when the ProRes probe MISSED (`export_video.prores_videotoolbox_state`):
    # the hardware encoder could not be confirmed just now (no session, or its test frame did not
    # come back), and the export asks again as it starts, so the hint may promise neither encoder,
    # nor a time that depends on which one it gets.
    _PRORES_MISSED_LINE = (f"The hardware ProRes encoder could not be confirmed just now: the "
                           f"export tries {export_video.VT_PRORES} again as it starts, else uses "
                           f"{export_video.SW_PRORES}.")

    @staticmethod
    def _measuring_line(src: str) -> str:
        """What the hint says in place of a size and a time it is still measuring: the footage's
        frame or this Mac's encoders (`export_video.warm_export_probes`) are not in yet. "" once
        they are, or when there is no footage to ask; the hint then says what it knows, or
        nothing."""
        if not src or export_video.export_probes_ready(src):
            return ""
        # The footage only while its frame is still being asked: a file that could not be read
        # has its answer (none), and what is left to wait for is the encoder.
        if not export_video.video_size_asked(src):
            return "Measuring the footage for the size and the time to render…"
        return "Measuring this Mac's video encoder for the size and the time to render…"

    def _ask_export_options(self, lap: int, pass_: tuple[int, str] | None = None,
                            preset_pass: bool = False):
        """Modal scope + shape + output picker returning an `ExportChoice`, or None on cancel.
        Every choice but Contents persists across relaunches (prefs), like the unit and the palette.

        THE ROWS ARE ORDERED BY WHAT THEY COST. Scope first, because it is the difference between
        ninety seconds of render and half an hour of it and every other row is a detail next to
        that; then the run-up, which changes the same number; then the frame's shape; then what
        the frame contains and how big it lands. The hint underneath re-reads every one of them,
        so the consequences of a choice are visible before it is confirmed rather than after.

        `pass_` = (cid, label) of lap `lap`'s corner (or sector) pass: the Export row then ends in
        "This pass — C3, lap 13" (`SCOPE_PASS`), opened on when `preset_pass` (the pass's own command). That
        row is never remembered: a pass is the subject of one export, and the next dialog opens on
        the scope remembered before it."""
        dlg, col = self._export_dialog("Export overlay video")
        scope_options = list(self._EXPORT_SCOPE_OPTIONS)
        if pass_ is not None:
            scope_options.append((f"This pass — {pass_[1]}, lap {lap_label(lap)}",
                                  export_video.SCOPE_PASS))

        desc = QLabel(self._EXPORT_STYLE_DESC[broadcast.STYLE_CLASSIC])   # follows the Style
        desc.setWordWrap(True)
        desc.setProperty("role", "Note")
        col.addWidget(desc)

        # What the Export row WILL render (`_scope_line`), set by `_update_hint` below — with a ⚠
        # lap's reason, a sentence, so the line wraps rather than clips.
        lap_line = QLabel(self._scope_line(export_video.SCOPE_THIS_LAP, lap))
        lap_line.setWordWrap(True)
        lap_line.setProperty("role", "Note")
        col.addWidget(lap_line)
        # With the pass row chosen the line names the pass, never the lap's time (a pass clip
        # burns no lap clock): "Lap 13 · C3 · 2.32 s", the Quickest row's number.
        pass_text = ""
        if pass_ is not None:
            try:
                subject = export_video.pass_subject(self.win.session, lap, pass_[0])
            except Exception:  # noqa: BLE001 — a caption never blocks the export it describes
                subject = None
            pass_text = (f"Lap {lap_label(lap)}  ·  {pass_[1]}"
                         + (f"  ·  {subject.time_s:.2f} s" if subject is not None else ""))

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(theme.SPACE_M)
        form.setVerticalSpacing(theme.SPACE_S)

        rows = {}

        def _combo(options, pref_key):
            box = QComboBox(dlg)
            for label, _value in options:
                box.addItem(label)
            box.setCurrentIndex(self._export_pref_index(
                pref_key, self._EXPORT_DEFAULT_INDEX[pref_key], len(options)))
            rows[pref_key] = box
            return box

        scope_combo = _combo(self._EXPORT_SCOPE_OPTIONS, self._PREF_EXPORT_SCOPE)
        remembered_scope = scope_combo.currentIndex()
        if pass_ is not None:
            scope_combo.addItem(scope_options[-1][0])
            if preset_pass:
                scope_combo.setCurrentIndex(len(scope_options) - 1)
        lead_combo = _combo(self._EXPORT_LEAD_OPTIONS, self._PREF_EXPORT_LEAD)
        aspect_combo = _combo(self._EXPORT_ASPECT_OPTIONS, self._PREF_EXPORT_ASPECT)
        fit_combo = _combo(self._EXPORT_FIT_OPTIONS, self._PREF_EXPORT_FIT)
        # CONTENTS IS CHOSEN PER EXPORT AND NEVER REMEMBERED. Overlay-only is a specialist, one-off
        # choice: a transparent track for an editor. Remembered, it came back a day later as "the
        # video is completely black and time scale is odd" (the owner, 2026-09-25) — the correct
        # ProRes track, which QuickTime draws on black and times by the camera's timecode. So this
        # row opens on the burned-in footage every time, and no pref is read for it or written.
        content_combo = QComboBox(dlg)
        for label, _value in self._EXPORT_CONTENT_OPTIONS:
            content_combo.addItem(label)
        res_combo = _combo(self._EXPORT_RES_OPTIONS, self._PREF_EXPORT_RES)
        q_combo = _combo(self._EXPORT_QUALITY_OPTIONS, self._PREF_EXPORT_QUALITY)
        style_combo = _combo(self._EXPORT_STYLE_OPTIONS, self._PREF_EXPORT_STYLE)
        ghost_box = QCheckBox(self._GHOST_LABEL, dlg)
        cards_box = QCheckBox(self._CARDS_LABEL, dlg)
        # Each style's ticks for this dialog: remembered (or that style's default) until changed.
        ticks = {}
        for style, keys in self._PREF_EXPORT_STYLE_EXTRAS.items():
            got = []
            for key, default in keys:
                try:
                    value = prefs.get(key, default)
                except Exception:  # noqa: BLE001 — an unreadable pref opens on the default
                    value = default
                got.append(value if isinstance(value, bool) else default)
            ticks[style] = got

        def _style():
            return self._EXPORT_STYLE_OPTIONS[style_combo.currentIndex()][1]
        shown = [_style()]

        # `ticks` is the truth for each style; the boxes SHOW it, unticked while one cannot apply
        # here (a bright tick on a disabled box read as "on"), and `_update_hint` re-shows it with
        # the box's signals blocked, so only the user's own clicks write a tick.
        def _show_style_ticks(_index=None):
            shown[0] = _style()

        def _ticked(i, on):
            ticks[shown[0]][i] = on
        ghost_box.setChecked(ticks[shown[0]][0])
        cards_box.setChecked(ticks[shown[0]][1])
        style_combo.currentIndexChanged.connect(_show_style_ticks)
        ghost_box.toggled.connect(lambda on: _ticked(0, on))
        cards_box.toggled.connect(lambda on: _ticked(1, on))
        # One Extras row, the opt-in elements side by side.
        extras = QWidget(dlg)
        extras_row = QHBoxLayout(extras)
        extras_row.setContentsMargins(0, 0, 0, 0)
        extras_row.setSpacing(theme.SPACE_L)
        extras_row.addWidget(ghost_box)
        extras_row.addWidget(cards_box)
        extras_row.addStretch(1)

        # THE DIALOG OPENS ON THE LAST EXPORT'S CHOICES, AND A HEAVY ONE MUST NOT DO THAT SILENTLY.
        # "Video export has become extremely slow" was a remembered overlay-only ProRes at source
        # resolution: 2.4 minutes and 1.4 GB for one MK lap that takes 33 s on the defaults,
        # re-opened every time with nothing on screen saying the choice was his own, still in force.
        # Contents no longer comes back, so Source is the heavy row left: when the dialog opens on
        # it, one line names it, and "Use defaults" puts EVERY row back. It writes nothing: prefs
        # are saved on Export, as ever.
        # A remembered non-default Style is named too: it changes the whole look of the file.
        recalled = []
        if res_combo.currentIndex() == self._EXPORT_RES_SOURCE:
            recalled.append(f"resolution: {res_combo.currentText()}")
        if style_combo.currentIndex() != self._EXPORT_DEFAULT_INDEX[self._PREF_EXPORT_STYLE]:
            recalled.append(f"style: {style_combo.currentText().split(' — ')[0]}")
        if recalled:
            recall = QWidget(dlg)
            recall_row = QHBoxLayout(recall)
            recall_row.setContentsMargins(0, 0, 0, 0)
            recall_row.setSpacing(theme.SPACE_M)
            recall_text = QLabel(f"Opened on your last export's {' and '.join(recalled)}.",
                                 recall)
            recall_text.setWordWrap(True)
            recall_text.setProperty("role", "Note")
            recall_row.addWidget(recall_text, 1)
            use_defaults = QPushButton("Use defaults", recall)
            use_defaults.setAutoDefault(False)     # Return still means Export
            recall_row.addWidget(use_defaults, 0)
            col.addWidget(recall)

            def _use_defaults():
                for key, box in rows.items():
                    if box is scope_combo and box.currentIndex() >= len(self._EXPORT_SCOPE_OPTIONS):
                        continue              # the pass the dialog was opened for stays its subject
                    box.setCurrentIndex(self._EXPORT_DEFAULT_INDEX[key])
                content_combo.setCurrentIndex(0)
                ticks[_style()] = [False, False]
                _update_hint()
                recall.hide()
            use_defaults.clicked.connect(_use_defaults)

        form.addRow("Export", scope_combo)
        form.addRow("Run-up / run-off", lead_combo)
        form.addRow("Shape", aspect_combo)
        form.addRow("Source frame", fit_combo)
        form.addRow("Contents", content_combo)
        form.addRow("Resolution", res_combo)
        form.addRow("Quality", q_combo)
        form.addRow("Style", style_combo)
        form.addRow("Extras", extras)
        col.addLayout(form)

        # States the target short side + never-upscale rule (no ffprobe here; matches
        # frame_geometry()), THEN what the choices actually cost.
        hint = SteadyHint()
        col.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dlg)
        ok_button = buttons.button(QDialogButtonBox.Ok)
        ok_button.setText("Export")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        col.addWidget(buttons)

        def _update_hint():
            scope = scope_options[scope_combo.currentIndex()][1]
            lead = self._EXPORT_LEAD_OPTIONS[lead_combo.currentIndex()][1]
            aspect = self._EXPORT_ASPECT_OPTIONS[aspect_combo.currentIndex()][1]
            content = self._EXPORT_CONTENT_OPTIONS[content_combo.currentIndex()][1]
            h = self._EXPORT_RES_OPTIONS[res_combo.currentIndex()][1]
            quality = self._EXPORT_QUALITY_OPTIONS[q_combo.currentIndex()][1]
            overlay_only = content != self._EXPORT_CONTENT_COMPOSITE
            # A row that cannot change anything is disabled rather than quietly ignored: the
            # padding is a LAP's run-up and the session scope has no lap edges to pad; the
            # crop/fit choice only exists where the output's shape differs from the footage's;
            # the H.264 quality preset is not a knob ProRes or PNG have.
            session_scope = scope == export_video.SCOPE_SESSION
            pass_scope = scope == export_video.SCOPE_PASS    # it carries its own lead-in
            lead_combo.setEnabled(not (session_scope or pass_scope))
            lead_combo.setToolTip(self._PASS_LEAD_TIP if pass_scope else "")
            fit_combo.setEnabled(aspect != export_video.ASPECT_SOURCE)
            q_combo.setEnabled(not overlay_only)
            lead = 0.0 if (session_scope or pass_scope) else lead
            # The ghost is disabled, with its reason as the tooltip, where the rules forbid one;
            # its remembered tick is kept but a line below says it will not be drawn, so a choice
            # the user made is never dropped without a word.
            no_ghost = self._ghost_reason(scope, lap)
            ghost_box.setEnabled(no_ghost is None)
            ghost_box.setToolTip(self._GHOST_TIP if no_ghost is None else f"Off here: {no_ghost}")
            no_cards = self._cards_reason(scope, lap, pass_[0] if pass_ is not None else None)
            cards_box.setEnabled(no_cards is None)
            want_ghost, want_cards = ticks[_style()]
            for box, on in ((ghost_box, want_ghost and no_ghost is None),
                            (cards_box, want_cards and no_cards is None)):
                box.blockSignals(True)
                box.setChecked(on)
                box.blockSignals(False)
            desc.setText(self._PASS_STYLE_DESC[_style()].format(noun=_pass_noun(pass_[0]))
                         if pass_scope else self._EXPORT_STYLE_DESC[_style()])
            lap_line.setText(pass_text if pass_scope else self._scope_line(scope, lap))
            provisional = not bool(getattr(getattr(self.win, "session", None),
                                           "timing_verified", True))
            cards_box.setToolTip(
                (self._CARDS_TIP + (self._CARDS_PROVISIONAL if provisional else ""))
                if no_cards is None else f"Off here: {no_cards}")

            clip, files, what = self._scope_plan(scope, lap, lead, pass_)
            lines = [f"Renders {what}."]
            if no_ghost is not None and want_ghost:
                lines.append(f"No ghost in this export: {no_ghost}")
            elif scope == export_video.SCOPE_ALL_LAPS and want_ghost:
                missing = self._ghostless_files(lap)
                if missing:
                    lines.append(missing)
            if no_cards is not None and want_cards:
                lines.append(f"No corner cards in this export: {no_cards}")
            elif scope == export_video.SCOPE_ALL_LAPS and want_cards:
                missing = self._ghostless_files(lap, corner_cards.unavailable_reason,
                                                "corner cards")
                if missing:
                    lines.append(missing)
            source = export_video.known_video_size(src)
            fit = self._EXPORT_FIT_OPTIONS[fit_combo.currentIndex()][1]
            how = ("source aspect" if aspect == export_video.ASPECT_SOURCE else
                   "the sides cropped off" if fit == export_video.FIT_CROP
                   else "the whole picture, with bars")
            if source is not None:
                # The frame the render writes; a capped one says why, as the row's label cannot.
                geo = export_video.frame_geometry(source[0], source[1], export_video.OverlayConfig(
                    out_height=h, aspect=aspect, frame_fit=fit, overlay_only=overlay_only))
                own = (geo.out_w, geo.out_h) == (source[0], source[1])
                said = "the footage's own resolution" if own else how
                lines.append(f"Output: {geo.out_w}x{geo.out_h}, {said}"
                             f"{self._CAP_NOTE if geo.capped else ''}.")
            elif h >= 99999:
                lines.append("Output: source resolution (never upscaled).")
            elif aspect == export_video.ASPECT_SOURCE:
                lines.append(f"Output: up to {h}p tall, source aspect — never upscaled past source.")
            else:
                ow, oh = self._estimate_frame_size(h, aspect)
                lines.append(f"Output: up to {ow}x{oh}, {how} — never upscaled past source.")
            # THE PADDING IS NAMED FOR WHAT IT ACTUALLY CONTAINS. Seconds either side of the
            # timing line are seconds of the PREVIOUS and NEXT lap: a per-lap batch that pads is a
            # set of files each holding a piece of its neighbours. That is a real artefact other
            # exporters ship without a word; the fix is not to forbid it, it is to say it.
            if lead and clip > 0 and math.isfinite(clip):
                each = " each" if files > 1 else ""
                lines.append(f"Clip: {fmt_time(clip)}{each} — the lap plus {lead:g} s of the "
                             "previous lap at the head and the next lap at the tail.")
            if overlay_only:
                png = content == export_video.ALPHA_PNG
                where = "a folder of numbered PNG frames" if png else "a ProRes 4444 .mov"
                # A .mov is also something a player opens, and what it shows there is said before
                # the owner meets it. No player opens a folder of frames as a video.
                player = ("" if png else "; players such as QuickTime show it on black and count "
                          "time on the camera's clock")
                lines.append(f"Overlay only: {where} on a transparent background, no footage and "
                             "no audio — for compositing over the original in Resolve or "
                             f"Premiere{player}.")
            on_line = not (session_scope or pass_scope or lead)
            size = self._export_size_hint(clip, h, quality, aspect, content, files, source, fit,
                                          on_line=on_line)
            # Until the footage's FRAME is measured the size is a 16:9 guess at the row's
            # resolution (on the demo "About 26 MB … 30 fps" for a clip that lands at 9 MB, 29.97
            # fps), stated as if final: the line says it is measuring instead, and the number
            # arrives once. (A known frame with the ProRes probe still out keeps its size and
            # says the time is being measured.)
            measuring = self._measuring_line(src)
            # Only while there IS a frame to wait for: a file the probe cannot read keeps the
            # guess, the one size there will ever be.
            guessed = bool(measuring) and not export_video.video_size_asked(src)
            # A burned-in export before the H.264 probe has answered has no figure at all; the
            # room kept for it is libx264's, the longer line of the two.
            unpriced = bool(measuring) and not size and not overlay_only
            priced = size or (self._export_size_hint(
                clip, h, quality, aspect, content, files, source, fit, on_line=on_line,
                h264=export_video.SW_H264) if unpriced else "")
            reserve = "  ".join([*lines, priced]) if (guessed or unpriced) and priced else ""
            if size and not guessed:
                lines.append(size)
            if measuring and (guessed or "to render" not in size):
                lines.append(measuring)
            if (content == export_video.ALPHA_PRORES
                    and export_video.prores_videotoolbox_state() == "missed"):
                lines.append(self._PRORES_MISSED_LINE)
            hint.show_text("  ".join(lines), reserve)
            # A scope with nothing to render cannot be confirmed. `_scope_plan` already says why.
            ok_button.setEnabled(math.isfinite(clip) and clip > 0)
        # EVERY combo: each one moves at least one number in the lines above.
        for combo in (*rows.values(), content_combo):
            combo.currentIndexChanged.connect(_update_hint)
        ghost_box.toggled.connect(_update_hint)
        cards_box.toggled.connect(_update_hint)
        src = self.win._paths[0] if getattr(self.win, "_paths", None) else ""
        probe_poll = self._redraw_once_probed(dlg, src, _update_hint)
        _update_hint()
        if recalled:
            # "Use defaults" is drawn ABOVE the form, yet Tab reached it last, climbing back up
            # from Extras. Tab reads the dialog as it is drawn — set here, with the tree complete:
            # set where the form is built, the chain was still re-ordered after it. The caret still
            # opens on the Export row.
            order = (use_defaults, scope_combo, lead_combo, aspect_combo, fit_combo, content_combo,
                     res_combo, q_combo, style_combo, ghost_box, cards_box)
            for first, then in zip(order, order[1:], strict=False):
                QWidget.setTabOrder(first, then)
            scope_combo.setFocus()

        accepted = dlg.exec() == QDialog.Accepted
        if probe_poll is not None:
            probe_poll.stop()
        if not accepted:
            return None
        scope = scope_options[scope_combo.currentIndex()][1]
        indices = {
            self._PREF_EXPORT_SCOPE: (remembered_scope if scope == export_video.SCOPE_PASS
                                      else scope_combo.currentIndex()),
            self._PREF_EXPORT_LEAD: lead_combo.currentIndex(),
            self._PREF_EXPORT_ASPECT: aspect_combo.currentIndex(),
            self._PREF_EXPORT_FIT: fit_combo.currentIndex(),
            self._PREF_EXPORT_RES: res_combo.currentIndex(),
            self._PREF_EXPORT_QUALITY: q_combo.currentIndex(),
            self._PREF_EXPORT_STYLE: style_combo.currentIndex(),
        }
        self._remember_export_prefs(indices)   # survives this window, and the relaunch
        try:
            for (key, _default), on in zip(self._PREF_EXPORT_STYLE_EXTRAS[_style()],
                                           ticks[_style()], strict=True):
                prefs.set(key, on)
        except OSError as exc:
            _log.warning("export extras not remembered (%r)", exc)
        content = self._EXPORT_CONTENT_OPTIONS[content_combo.currentIndex()][1]
        overlay_only = content != self._EXPORT_CONTENT_COMPOSITE
        # Burn the current display unit + semantic palette into the overlay so the export matches the
        # on-screen readout (incl. the colour-blind Δ hue axis — the exported clip is the shared
        # artifact, so it must follow the user's colour-blind choice, not stay red/green).
        config = broadcast.styled(export_video.OverlayConfig(
            out_height=self._EXPORT_RES_OPTIONS[res_combo.currentIndex()][1],
            quality=self._EXPORT_QUALITY_OPTIONS[q_combo.currentIndex()][1],
            aspect=self._EXPORT_ASPECT_OPTIONS[aspect_combo.currentIndex()][1],
            frame_fit=self._EXPORT_FIT_OPTIONS[fit_combo.currentIndex()][1],
            overlay_only=overlay_only,
            alpha_codec=content if overlay_only else export_video.ALPHA_PRORES,
            speed_unit=self.win._speed_unit,
            palette=theme.active_palette(),
            elements=tuple(name for name, box in ((ghost.GhostElement.name, ghost_box),
                                                  (corner_cards.CornerCardsElement.name, cards_box))
                           if box.isChecked() and box.isEnabled())), _style())
        lead = (0.0 if scope in (export_video.SCOPE_SESSION, export_video.SCOPE_PASS)
                else self._EXPORT_LEAD_OPTIONS[lead_combo.currentIndex()][1])
        return ExportChoice(config=config, lead=lead, scope=scope,
                            cid=pass_[0] if scope == export_video.SCOPE_PASS else None)
    # Every failure this export can raise says the product's name IN THE BODY. macOS drops a
    # QMessageBox's window title (documented at _show_error_report and _load_failure_dialog, pinned
    # by test_app_chrome), so "The render failed: …" used to arrive as an unattributed sentence in a
    # titleless box — the load path was fixed for exactly this and the export never was.
    _EXPORT_FAIL_TITLE = f"{APP_NAME} — could not export video"
    # The scope row in the words the session log's start line uses.
    _SCOPE_LOG_WORDS = {
        export_video.SCOPE_THIS_LAP: "this lap", export_video.SCOPE_BEST_LAP: "best lap",
        export_video.SCOPE_ALL_LAPS: "all laps", export_video.SCOPE_SESSION: "full session",
        export_video.SCOPE_PASS: "pass",
    }
    @staticmethod
    def _overlay_suffix(choice, lap: int | None, corner: str | None = None) -> str:
        """What follows the recording's stem in an overlay export's default name, for `lap` (None =
        the whole recording): `_lap14_overlay.mp4`, `_session_overlay.mp4`, the
        `_lap14_overlay_alpha.mov` track, the `_lap14_overlay_png` folder (see
        `export_video.overlay_output_name`). `_export_default` puts the stem in front.

        The transparent track keeps a name of its own. It used to be `<stem>_overlay.mov`, one
        letter from the burned-in `<stem>_overlay.mp4` beside it, and nothing in Finder said which
        of the two was the video to watch."""
        return export_video.overlay_output_name("", lap, choice.config, corner)
    def _overlay_name(self, choice, lap: int | None, corner: str | None = None) -> str:
        """The default file (or PNG folder) NAME, stem included: `GX010067_lap14_overlay.mp4`
        (a pass: `GX010067_lap13_C3_overlay.mp4`)."""
        return os.path.basename(self._export_default(self._overlay_suffix(choice, lap, corner)))
    def _confirm_replace(self, specs, png: bool, panel_asked: bool) -> bool:
        """Ask ONCE before an export overwrites what is already there; True to go ahead.

        ALL LAPS REPLACED FILES WITHOUT A WORD. ffmpeg runs with `-y`, and the only overwrite
        prompt was the save panel's — about the one base name it was handed, which the batch never
        wrote (EXP-4). A batch is now asked for as a folder, so this is the one question about the
        files it WILL write: how many of them are already there.

        A PNG SEQUENCE MIXED TWO EXPORTS. Its frames went into a folder that could already hold an
        earlier sequence's, and a shorter lap left the longer one's last frames behind it (2,023
        new frames over 2,025 old ones, reported as one export: EXP-5). Replacing it now takes the
        old frames out first; anything else in the folder is left where it is.

        A single file the save panel already asked about (`panel_asked`) is not asked twice."""
        def taken(spec) -> bool:
            out = spec.out_path
            if png:
                try:
                    return os.path.isdir(out) and bool(os.listdir(out))
                except OSError:
                    return False
            return os.path.exists(out)
        existing = [s for s in specs if taken(s)]
        if not existing or (panel_asked and len(specs) == 1):
            return True
        folder = os.path.dirname(os.path.abspath(existing[0].out_path))
        if len(specs) > 1:
            what = "frame folders already hold files" if png else "files already exist"
            text = (f"{len(existing)} of {len(specs)} {what} — replace them?\n\n{folder}")
        else:
            name = os.path.basename(existing[0].out_path)
            text = (f"{name} already holds {plural(len(os.listdir(existing[0].out_path)), 'file')} — "
                    f"replace the frames in it?\n\n{folder}")
        box = QMessageBox(QMessageBox.Warning, f"{APP_NAME} — replace existing export?", text,
                          parent=self.win)
        replace_btn = box.addButton("Replace", QMessageBox.DestructiveRole)
        cancel_btn = box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(cancel_btn)      # the safe answer is the one Return gives
        box.exec()
        if box.clickedButton() is not replace_btn:
            return False
        if png:
            for spec in existing:
                export_video.remove_png_frames(spec.out_path)
        return True
    def export_pass_video(self, lap: int, cid: int, label: str) -> None:
        """Export lap `lap`'s pass through corner `cid` (`label`, "C3"), or through a sector (`cid`
        a `stats.sector_key`, "S2"): the overlay picker, opened on "This pass" (⌘K "Export the C3
        pass — lap 13…", offered while Corners ▸ Quickest passes names that pass)."""
        self.export_overlay_video(pass_=(int(lap), int(cid), label), preset_pass=True)

    def export_overlay_video(self, *, pass_: tuple[int, int, str] | None = None,
                             preset_pass: bool = False):
        """File ▸ Export overlay video…: the picker, then the save panel, then the render. Its
        "This pass" row is offered for `pass_` = (lap, cid, label), else for the pass Quickest
        passes names now (`pass_subject`).

        KEYWORD-ONLY, because the menu item connects straight to this method. PySide hands a slot
        the signal's arguments whenever it has positional room for them, so `triggered(bool)` landed
        `checked=False` in `pass_` and the File menu and ⌘K row raised a TypeError instead of
        opening the picker (0.17.0 – 0.18.0). A keyword-only parameter is never filled from a
        signal (measured on PySide 6.11); tests/test_export_menu_entry.py fires the real action."""
        if self._no_laps_to_export():
            return
        if not export_video.ffmpeg_available():
            QMessageBox.warning(self.win, self._EXPORT_FAIL_TITLE,
                                f"{APP_NAME} can't render an overlay video on this machine: "
                                "ffmpeg was not found. The video export needs ffmpeg/ffprobe on "
                                "PATH (they ship with the pixi environment).")
            return
        src = self.win._paths[0] if getattr(self.win, "_paths", None) else ""
        if not src or not os.path.exists(src):
            QMessageBox.warning(self.win, self._EXPORT_FAIL_TITLE,
                                f"{APP_NAME} can't render an overlay video for this session: "
                                "it has no source video file to render onto.")
            return
        if pass_ is None:
            pass_ = self.pass_subject()
        # the primary/selected lap, falling back to the best lap; a pass names its own lap
        lap = pass_[0] if (pass_ is not None and preset_pass) else self._export_lap_id()
        win = export_video.lap_window_for_export(self.win.session, lap) if lap is not None else None
        if win is None:
            self.win.statusBar().showMessage("no usable lap to export video for", self._status_ms)
            return
        # The MP4 obeys the SAME trust verdict as the lap card (card_data().blocked) — one decision,
        # every shareable output, no surface exempt. Asked here, after the mechanical guards, so a
        # machine with no ffmpeg gets the reason it can't export rather than a trust warning first.
        if self.win._share_card_blocked() and not self.win._confirm_provisional_video():
            self.win.statusBar().showMessage("video export cancelled", self._status_ms)
            return
        # Pick scope + shape + output FIRST (so a cancel here writes nothing), then the save path.
        if pass_ is not None and pass_[0] != lap:
            pass_ = None                  # the row names the lap the other rows render
        choice = (self._ask_export_options(lap) if pass_ is None
                  else self._ask_export_options(lap, pass_[1:], preset_pass))
        if choice is None:
            return
        scope = choice.scope
        corner = pass_[2] if (pass_ is not None and scope == export_video.SCOPE_PASS) else None
        png = choice.config.overlay_only and choice.config.alpha_codec == export_video.ALPHA_PNG
        batch = scope == export_video.SCOPE_ALL_LAPS
        # The lap the NAME carries is the lap the scope renders: "Best lap" names the best lap
        # whichever one is selected, and the full session names no lap at all.
        try:
            ids = export_video.scope_lap_ids(self.win.session, scope, lap)
        except Exception:  # noqa: BLE001 — a name must never block the export it names
            ids = [lap]
        name_lap = None if scope == export_video.SCOPE_SESSION else (ids[0] if ids else lap)
        home = os.path.dirname(self._export_default(""))
        lap_path = None
        if batch or png:
            # ASKED FOR AS A FOLDER, because a folder is what the user is choosing. A PNG sequence
            # is a directory of frames, and a save-file prompt would hand back a file name the
            # renderer had to reinterpret; an All-laps batch writes one file per lap, and a save
            # panel asked about a base name it never wrote while its real files were replaced
            # unasked (EXP-4). The frames go into a FRESH subfolder named for the lap, never
            # loose among the footage the chooser opens on (EXP-5).
            title = ("Choose a folder for the lap videos" if batch
                     else "Choose where to put the PNG frames folder")
            folder = self._export_folder_path(title, home)
            if not folder:
                return
            out = os.path.join(folder, self._overlay_name(choice, name_lap, corner))
            if batch:
                lap_path = lambda i: os.path.join(folder, self._overlay_name(choice, i))  # noqa: E731
        else:
            filt = ("ProRes 4444 with alpha (*.mov)" if choice.config.overlay_only
                    else "MP4 video (*.mp4)")
            out = self._export_save_path("Export overlay video",
                                         self._overlay_suffix(choice, name_lap, corner), filt)
            if not out:
                return
        # Resolve the scope to one spec per output file. The padding goes through the spec
        # builders rather than being applied here, because a window has to be widened BEFORE the
        # video source is resolved — a lead-in can reach back over a chapter seam.
        try:
            specs = export_video.build_scope_specs(
                self.win.session, out, scope, lap_id=lap, config=choice.config,
                src_path=src, lead=choice.lead, lap_path=lap_path, cid=choice.cid)
        except ValueError as exc:
            QMessageBox.warning(self.win, self._EXPORT_FAIL_TITLE,
                                f"{APP_NAME} can't export this:\n{exc}")
            return
        if not self._confirm_replace(specs, png, panel_asked=not (batch or png)):
            for spec in specs:
                spec.source.cleanup()
            self.win.statusBar().showMessage("video export cancelled", self._status_ms)
            return
        self._run_video_export(specs, scope_text=self._SCOPE_LOG_WORDS.get(scope, scope))
    # ------------------------------------------------------------------ compare export
    _COMPARE_FAIL_TITLE = f"{APP_NAME} — could not export the comparison"

    def compare_pair(self):
        """The live compare pane pair as `(lap_a, lap_b, session_b, cross)`, or None when the app is
        not comparing anything. The compare controller lives on the CENTRAL VIEW, which is None
        between views (a reload's loading card is up), so it is asked for rather than assumed —
        the same guard `app._enter_cross_compare` uses."""
        view = getattr(self.win, "view", None)
        compare = getattr(view, "compare", None) if view is not None else None
        if compare is None or not compare.active:
            return None
        a, b = compare.lap_a, compare.lap_b
        if a is None or b is None:
            return None
        return a, b, compare.session_b, bool(compare.cross)

    def compare_pass(self):
        """The pass the live compare is anchored at (`compare_controller.PassAnchor`: "Compare
        with my typical pass", a Coaching row's Compare), when it names one to export, or None.
        The compare picker offers "This pass" only then."""
        view = getattr(self.win, "view", None)
        compare = getattr(view, "compare", None) if view is not None else None
        anchor = getattr(compare, "anchor", None) if compare is not None else None
        if (anchor is None or not getattr(compare, "active", False) or compare.cross
                or getattr(anchor, "key", None) is None or getattr(anchor, "pass_s", None) is None):
            return None
        return anchor

    # The compare picker's two descriptions, one per scope.
    _COMPARE_LAPS_DESC = ("Both laps play locked to the same point on TRACK, not the same time on "
                          "the clock — so as one pulls ahead the two frames stay at the same "
                          "corner and the gap between the clocks is the delta.")
    _COMPARE_PASS_DESC = ("Both passes through {label}, each from its own lead-in to 1 s after "
                          "its exit, started together. The gap shows no "
                          "number before the entry, the gap since the entry through {label}, and "
                          "after the exit the two passes' gap as timed, held.")

    def _compare_pass_seconds(self, lap_a: int, lap_b: int, key: int) -> float:
        """How long a compare at one pass runs: the longer of its two pass clips
        (`export_compare.PassPair`), each the window the render resolves. NaN without both."""
        spans = [self._pass_seconds(lap, key) for lap in (lap_a, lap_b)]
        return max(spans) if all(math.isfinite(s) for s in spans) else float("nan")

    def _ask_compare_options(self):
        """Layout + resolution + quality for a compare export, or None on cancel. Every row
        persists, like the single-lap picker's; the resolution under its OWN key
        (`_PREF_COMPARE_RES`), so neither picker opens on the other's choice.

        A compare at a pass (`compare_pass`) adds an Export row first: "This pass — C3, lap 13
        against lap 1", opened on (the compare he is looking at), or "Both whole laps". Like the
        single-lap picker's pass row it is never remembered. The choice is left in
        `self.compare_pass_chosen` (the anchor, or None for the whole laps)."""
        self.compare_pass_chosen = None
        dlg, col = self._export_dialog("Export comparison video")
        desc = QLabel(self._COMPARE_LAPS_DESC)
        desc.setWordWrap(True)
        desc.setProperty("role", "Note")
        col.addWidget(desc)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(theme.SPACE_M)
        form.setVerticalSpacing(theme.SPACE_S)
        pair = self.compare_pair()
        # A ⚠ lap in the pair, each from its own recording, as the single-lap picker's lap line.
        if pair is not None:
            for lap, session in ((pair[0], getattr(self.win, "session", None)),
                                 (pair[1], pair[2] or getattr(self.win, "session", None))):
                why = self._not_counted_line(session, lap)
                if why:
                    note = QLabel(f"Lap {lap_label(lap)}  ·  {why}")
                    note.setWordWrap(True)
                    note.setProperty("role", "Note")
                    col.addWidget(note)
        anchor = self.compare_pass() if pair is not None else None
        scope_combo = None
        if anchor is not None:
            pair_text = f"lap {lap_label(pair[0])} against lap {lap_label(pair[1])}"
            scope_combo = QComboBox(dlg)
            scope_combo.addItem(f"This pass — {anchor.label}, {pair_text}")
            scope_combo.addItem(f"Both whole laps — {pair_text}")
            form.addRow("Export", scope_combo)

        def _pass_scope() -> bool:
            return scope_combo is not None and scope_combo.currentIndex() == 0

        def _combo(options, pref_key, default):
            box = QComboBox(dlg)
            for label, _value in options:
                box.addItem(label)
            box.setCurrentIndex(self._export_pref_index(pref_key, default, len(options)))
            return box

        layout_combo = _combo(self._COMPARE_LAYOUT_OPTIONS, self._PREF_COMPARE_LAYOUT, 0)
        res_combo = _combo(self._COMPARE_RES_OPTIONS, self._PREF_COMPARE_RES,
                           self._COMPARE_RES_DEFAULT)
        q_combo = _combo(self._EXPORT_QUALITY_OPTIONS, self._PREF_EXPORT_QUALITY, 0)  # High
        form.addRow("Layout", layout_combo)
        form.addRow("Resolution (each pane)", res_combo)
        form.addRow("Quality", q_combo)
        col.addLayout(form)
        hint = SteadyHint()
        col.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dlg)
        buttons.button(QDialogButtonBox.Ok).setText("Export")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        col.addWidget(buttons)
        # EXP-8: the single-lap picker has always said what an export costs; this one said
        # nothing, and a compare costs more than a lap of the same length. The clip is lap A's
        # window, as `build_compare_spec` resolves it (no run-up: a compare is locked lap to lap);
        # at one pass, the longer of the two pass clips (`build_pass_compare_spec`).
        clip = self._export_clip_seconds(pair[0], 0.0) if pair is not None else float("nan")
        pass_clip = (self._compare_pass_seconds(pair[0], pair[1], int(anchor.key))
                     if anchor is not None else float("nan"))
        src = self.win._paths[0] if getattr(self.win, "_paths", None) else ""

        def _update_hint():
            at_pass = _pass_scope()
            desc.setText(self._COMPARE_PASS_DESC.format(label=anchor.label) if at_pass
                         else self._COMPARE_LAPS_DESC)
            if at_pass:
                lines = [f"The shorter pass holds its last frame until the longer one ends. The "
                         f"clip carries lap {lap_label(pair[0])}'s sound."]
            else:
                lines = ["The clip is as long as the first lap, and carries that lap's audio — "
                         "the second pane is time-warped by the lock, so its sound would be too."]
            args = (pass_clip if at_pass else clip,
                    self._COMPARE_RES_OPTIONS[res_combo.currentIndex()][1],
                    self._EXPORT_QUALITY_OPTIONS[q_combo.currentIndex()][1],
                    self._COMPARE_LAYOUT_OPTIONS[layout_combo.currentIndex()][1],
                    export_video.known_video_size(src))
            size = self._compare_size_hint(*args, ends_on_finish=not at_pass)
            measuring = self._measuring_line(src)
            # While a figure is still being measured the hint keeps room for it, priced with
            # libx264 (the longer line), so the rows above do not jump when it lands — the dialog
            # is fixed-height, and the line it waits for is two lines longer than "Measuring…".
            reserve = ""
            if measuring and not size:
                priced = self._compare_size_hint(*args, ends_on_finish=not at_pass,
                                                 h264=export_video.SW_H264)
                reserve = "  ".join([*lines, priced]) if priced else ""
            if size:
                lines.append(size)
            elif measuring:
                lines.append(measuring)
            hint.show_text("  ".join(lines), reserve)
        for combo in (layout_combo, res_combo, q_combo, scope_combo):
            if combo is not None:
                combo.currentIndexChanged.connect(_update_hint)
        probe_poll = self._redraw_once_probed(dlg, src, _update_hint)
        _update_hint()
        accepted = dlg.exec() == QDialog.Accepted
        if probe_poll is not None:
            probe_poll.stop()
        if not accepted:
            return None
        self.compare_pass_chosen = anchor if _pass_scope() else None
        self._remember_export_prefs({
            self._PREF_COMPARE_LAYOUT: layout_combo.currentIndex(),
            self._PREF_COMPARE_RES: res_combo.currentIndex(),
            self._PREF_EXPORT_QUALITY: q_combo.currentIndex(),
        })
        return export_compare.CompareConfig(
            out_height=self._COMPARE_RES_OPTIONS[res_combo.currentIndex()][1],
            quality=self._EXPORT_QUALITY_OPTIONS[q_combo.currentIndex()][1],
            layout=self._COMPARE_LAYOUT_OPTIONS[layout_combo.currentIndex()][1],
            speed_unit=self.win._speed_unit, palette=theme.active_palette())

    def export_compare_video(self):
        """File ▸ "Export comparison video…": render the two laps currently pinned in compare mode
        into ONE distance-locked clip.

        THE PAIR IS READ OFF THE LIVE COMPARE, never asked for again. The user has already chosen
        it — with two pane pickers, against the lap table, possibly against a whole second
        recording — and a dialog that asked "which two laps?" a second time would be a dialog that
        can disagree with what is on screen. The action is disabled when nothing is being compared,
        which is the honest way to say the same thing."""
        pair = self.compare_pair()
        if pair is None:
            self.win.statusBar().showMessage(
                "turn on compare (C) and pick the two laps first", self._status_ms)
            return
        lap_a, lap_b, session_b, cross = pair
        if not export_video.ffmpeg_available():
            QMessageBox.warning(self.win, self._COMPARE_FAIL_TITLE,
                                f"{APP_NAME} can't render a comparison video on this machine: "
                                "ffmpeg was not found. The video export needs ffmpeg/ffprobe on "
                                "PATH (they ship with the pixi environment).")
            return
        # The same trust verdict as every other shareable output; asked after the mechanical guards
        # so a machine with no ffmpeg hears about ffmpeg first.
        if self.win._share_card_blocked() and not self.win._confirm_provisional_video():
            self.win.statusBar().showMessage("video export cancelled", self._status_ms)
            return
        self.compare_pass_chosen = None
        config = self._ask_compare_options()
        if config is None:
            return
        anchor = self.compare_pass_chosen       # "This pass" (`_ask_compare_options`), or None
        # Named for the PAIR, in the lap table's numbers: `GX010067_lap14_vs_lap15_compare.mp4`,
        # and for the pass when it is one: `GX010067_lap13_vs_lap1_C3_compare.mp4`.
        at = f"_{anchor.label}" if anchor is not None else ""
        out = self._export_save_path(
            "Export comparison video",
            f"_lap{lap_label(lap_a)}_vs_lap{lap_label(lap_b)}{at}_compare.mp4",
            "MP4 video (*.mp4)")
        if not out:
            return
        src = self.win._paths[0] if getattr(self.win, "_paths", None) else None
        try:
            if anchor is not None:
                spec = export_compare.build_pass_compare_spec(
                    self.win.session, out, lap_a, lap_b, anchor, config=config, src_path=src,
                    label_a=f"LAP {lap_label(lap_a)}", label_b=f"LAP {lap_label(lap_b)}")
            else:
                spec = export_compare.build_compare_spec(
                    self.win.session, out, lap_a, lap_b, session_b=session_b, config=config,
                    src_path_a=src,
                    label_a=f"LAP {lap_label(lap_a)}",
                    label_b=f"REF LAP {lap_label(lap_b)}" if cross else f"LAP {lap_label(lap_b)}")
        except ValueError as exc:
            QMessageBox.warning(self.win, self._COMPARE_FAIL_TITLE,
                                f"{APP_NAME} can't export this comparison:\n{exc}")
            return

        def make_renderer(session, spec):
            return export_compare.CompareRenderer(session, spec, session_b)

        self._run_video_export([spec], make_renderer=make_renderer, scope_text="comparison")

    @staticmethod
    def _describe_spec(spec, lap: int | None = None) -> str:
        """What one queued render is OF, for the progress dialog and the completion card. The
        full-session scope has no lap to name, so it says so rather than naming lap -1.

        Read off the spec with `getattr`, falling back to `lap`, because the duck-typed specs the
        Qt-side tests build carry only the two fields the dialog plumbing touches — and a
        describing helper that can only describe the real class would make those tests assert
        against a different code path than production runs."""
        if getattr(spec, "follow_laps", False):
            return "the full session"
        lap_id = getattr(spec, "lap_id", None)
        lap_id = lap if lap_id is None else lap_id
        lap_b = getattr(spec, "lap_b", None)
        if lap_id is not None and lap_b is not None:
            # A cross-recording pane B is a lap of ANOTHER recording, and the clip burns it in as
            # "REF LAP n"; "against lap 23" alone reads as this recording's lap 23.
            ref = "reference " if getattr(spec, "cross", False) else ""
            pair = getattr(spec, "pass_pair", None)
            at = f" through {pair.label}" if pair is not None else ""
            return f"lap {lap_label(lap_id)} against {ref}lap {lap_label(lap_b)}{at}"
        return f"lap {lap_label(lap_id)}" if lap_id is not None else "an overlay video"

    @staticmethod
    def _video_kind(spec, article: bool = False) -> str:
        """What the finished and the failed box call the file: a two-lap compare is a comparison
        video, everything else an overlay video (JOURNEY-4: the compare's box said "overlay")."""
        compare = getattr(spec, "lap_b", None) is not None
        if article:
            return "a comparison video" if compare else "an overlay video"
        return "comparison video" if compare else "overlay video"
    # An ETA needs EVIDENCE, and the start of a render is the worst possible sample of it: the
    # first chunk carries the ffmpeg spawn, the VideoToolbox session probe and the painter's pill
    # budget, so a rate measured over it reads far slower than the render settles at. These two
    # are the minimum sample a number gets published from — below them the line simply counts
    # frames, which is true, instead of a figure that would visibly halve a few seconds later.
    _ETA_MIN_SECONDS = 4.0
    _ETA_MIN_FRAMES = 48          # one chunk of the render pump
    @staticmethod
    def _render_progress_detail(done: int, total: int, rendered: int, elapsed: float) -> str:
        """The modal's second line: how much of THIS file is done, and how long is left.

        The app's longest modal used to say `Rendering lap 4 overlay video…` and nothing else for
        up to several minutes. A bar without a number answers "is it moving"; it does not answer
        the question a user actually leaves the machine on: how long.

        `rendered` / `elapsed` are frames and seconds since the FIRST frame of this file, which is
        the only rate that means anything — measuring from the dialog opening would fold the
        ffmpeg spawn and the encoder probe into the average. Nothing is claimed until that sample
        is big enough (see the two constants); until then the line is just the count.

        Returns a line, never a sentence with a full stop: it sits under the title as a caption."""
        head = f"frame {done} of {total}"
        # THE FRAMES BEING DONE IS NOT THE EXPORT BEING DONE. Once the last frame is written the
        # renderer closes the encoder's stdin and waits for ffmpeg to write the trailer: measured on
        # D24, 1.6-5.7 s, and 12.1 % of one 2160p export's whole wall clock. The old line said
        # "frame 2047 of 2047" — or, worse, sat at the last chunk boundary claiming "about 0:01
        # left" — and then nothing moved for six seconds. Naming the write is the difference between
        # a dialog that looks wedged and one that says what it is doing.
        if total > 0 and done >= total:
            return f"all {total} frames rendered · writing the file"
        if rendered < ExportController._ETA_MIN_FRAMES or elapsed < ExportController._ETA_MIN_SECONDS:
            return head
        left = total - done
        if left <= 0:
            return head
        seconds = left * elapsed / rendered
        return f"{head} · about {fmt_hms(seconds)} left"
    def _run_video_export(self, specs, lap: int | None = None, make_renderer=None,
                          scope_text: str = ""):
        """Run `specs` on a worker QThread behind ONE cancellable modal dialog, in order. Starts
        indeterminate ("Preparing…"), flips to a determinate bar on the first frame's progress, and
        ALWAYS reaches a terminal state: the modal comes down the moment the render stops, whatever
        the outcome.

        ONE DIALOG FOR THE WHOLE BATCH, not one per lap. An All-laps export is a single decision
        the user made once, so a modal that came down and went back up forty times would be forty
        chances to lose the queue — and no way to cancel the rest. The bar restarts per file and
        the label counts them, so the batch's shape is visible without pretending forty renders are
        one progress bar.

        THE MODAL USED TO OUTLIVE THE RENDER. `setAutoClose(False)` is deliberate — Qt closes a
        QProgressDialog by itself when value reaches maximum, which here is the last FRAME, several
        seconds before ffmpeg has finished muxing — but the completion handler then called
        `dlg.reset()`, and `QProgressDialog::reset()` only hides when `autoClose()` is true. Measured
        on the real window: after a SUCCESSFUL export the dialog was still up, its label still read
        "Rendering lap 4 overlay video…", its bar had snapped back to empty (`QProgressBar.value()`
        == -1 of 700 — reset() resets the bar too, so it looks like the render restarted), and its
        one button still read "Cancel". The only success signal was a 6 s status line painted BEHIND
        a WindowModal dialog. Dismissing your finished export told you that you cancelled it.

        So: hide() rather than reset() (hiding a QDialog exits its exec() loop; close() would go
        through QProgressDialog::closeEvent, which EMITS canceled()), the cancel connection is
        dropped first so the button can no longer mean "cancel" on a finished worker, and success
        hands off to _video_export_finished.

        A BARE SPEC IS STILL ACCEPTED, and `lap` with it. Both are what this method took before it
        learned to render more than one file, and several callers and tests still say it that way;
        a queue of one is exactly what they meant. Every spec carries the lap it is of, so the
        `lap` argument is no longer read.

        `make_renderer` is handed straight to the worker and is how the distance-locked COMPARE
        export reuses this whole apparatus — one modal, one cancel, one failure dialog, one
        completion card — instead of growing a second copy of it.

        `scope_text` is the scope row in words, for the session log's start line (HEALTH-3)."""
        specs = list(specs) if isinstance(specs, (list, tuple)) else [specs]
        if not specs:
            return
        total_files = len(specs)
        done_paths: list[str] = []
        done_syncs: list = []      # each finished file's `SourceSync` (None for a composite)
        done_notes: list = []      # what an asked-for extra that sat out said (RenderResult.notes)
        state = {"index": 0, "worker": None, "cancelled": False}

        def _title(i: int) -> str:
            of = f" ({i + 1} of {total_files})" if total_files > 1 else ""
            return f"{self._describe_spec(specs[i], lap).capitalize()}{of}"

        dlg = QProgressDialog(f"Preparing {self._describe_spec(specs[0], lap)}…", "Cancel", 0, 0,
                              self.win)
        dlg.setWindowTitle("Export overlay video")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setValue(0)  # with max=0 too, Qt renders an indeterminate "busy" bar

        def _cleanup_all():
            # Free any temp concat-list file the chapter resolution wrote. A spec that owns MORE
            # than one source (the compare export's two panes) says so with its own `cleanup`;
            # reaching for `spec.source` alone would leak pane B's list file on every compare.
            for spec in specs:
                own = getattr(spec, "cleanup", None)
                if callable(own):
                    own()
                else:
                    spec.source.cleanup()

        def _start(i: int):
            spec = specs[i]
            started = {"first": False}
            # When this FILE's first frame landed, and at what count — the only two numbers an
            # honest ETA can be built from (see _render_progress_detail). Per file, not per batch:
            # the bar restarts per file and so does the estimate.
            clock = {"t0": 0.0, "done0": 0}
            worker = VideoExportWorker(self.win.session, spec, make_renderer)
            if i == 0:
                # THE WHOLE QUEUE'S SPACE, ASKED ONCE, BEFORE ITS FIRST FRAME. Every spec rides
                # along because an All-laps batch fits or does not fit as a batch: refusing its
                # twentieth file after nineteen renders is the wait this exists to spare. It runs
                # on the worker's thread (the probe and the disk query can block) while the
                # dialog still says "Preparing…", and it refuses only what plainly cannot fit.
                # The ProRes encoder is resolved first, once for the whole queue
                # (`pin_alpha_encoder`), so the space asked for is the encoder's that renders.
                def _preflight():
                    export_video.pin_alpha_encoder(specs)
                    export_video.guard_free_space(specs)
                worker.preflight = _preflight
            worker.log_scope = (f"{scope_text}, file {i + 1} of {total_files}" if total_files > 1
                                else scope_text)
            state["worker"] = worker
            self.win._video_worker = worker  # keep a ref so the thread isn't GC'd mid-render
            # AND put it in the DRAINED set. It was held on that attribute and nowhere else, so
            # closeEvent's drain — which exists precisely so no QThread is destroyed mid-run —
            # walked straight past the one worker that can still be running MINUTES after it
            # started, and quitting mid-render destroyed a live QThread. closeEvent cancels it
            # before draining (the renderer checks the flag once per frame), so joining it costs
            # about one frame.
            self.win._load_workers.add(worker)
            dlg.setLabelText(f"Preparing {self._describe_spec(spec, lap)}…")
            dlg.setMaximum(0)
            dlg.setValue(0)

            def on_progress(done: int, count: int):
                if count > 0:
                    if not started["first"]:
                        # First real frame: switch from the busy "Preparing…" bar to a determinate
                        # one, and start the clock the estimate is measured against.
                        started["first"] = True
                        clock["t0"], clock["done0"] = time.monotonic(), done
                    dlg.setMaximum(count)
                    dlg.setValue(done)
                    # The label is rewritten per PROGRESS CALLBACK, which is once per chunk of 48
                    # frames (~40 times for a lap), not once per frame — setLabelText resizes the
                    # dialog, and per-frame relayout at 30 fps would be a cost for nothing.
                    dlg.setLabelText(f"Rendering {_title(i)}…\n" + self._render_progress_detail(
                        done, count, done - clock["done0"], time.monotonic() - clock["t0"]))

            def on_done(ok: bool, message: str):
                # The render is over, so the button can no longer mean "cancel" for THIS worker:
                # drop the connection before anything else. (On the cancel path the dialog is
                # already hidden — QProgressDialog::cancel() force-hides regardless of autoClose.)
                dlg.canceled.disconnect(worker.cancel)
                worker.wait()
                self.win._load_workers.discard(worker)
                self.win._video_worker = None
                state["worker"] = None
                if ok:
                    done_paths.append(spec.out_path)
                    done_syncs.append(getattr(getattr(worker, "result", None), "sync", None))
                    for note in getattr(getattr(worker, "result", None), "notes", ()) or ():
                        if note not in done_notes:
                            done_notes.append(note)
                    if i + 1 < total_files and not state["cancelled"]:
                        _start(i + 1)     # next file, same dialog
                        return
                # The queue is over, so the progress modal comes down for good — and is freed.
                # Parented to the window and only dropped, it outlived its export: one
                # QProgressDialog and its 3 children per export, for the rest of the session
                # (measured on the demo, 5 exports: +5 top-level windows, +20 widgets). Freed here,
                # not after `dlg.exec()`, because a Cancel hides it and returns from exec() while
                # the worker is still stopping and this callback has yet to run. Nothing reads it
                # after this line.
                dlg.hide()
                dlg.deleteLater()
                _cleanup_all()
                if ok:
                    self._video_export_finished(done_paths, specs[0], lap, done_syncs,
                                                notes=done_notes)
                    return
                if message == "cancelled":
                    if done_paths:
                        self._batch_cancelled(done_paths, total_files)
                        return
                    self.win.statusBar().showMessage("video export cancelled", self._status_ms)
                    return
                # PLAIN LANGUAGE FIRST, the encoder's own words behind Details — the same shape as
                # the load-failure table and the crash report. `message` is an ffmpeg stderr TAIL:
                # pasting it as the body handed the user "[h264_videotoolbox @ 0x…] Error encoding
                # frame: -12905" as the explanation of what to do next.
                # A refusal never began, so "couldn't finish" would describe a render that did
                # not happen.
                what = ("didn't start" if export_video.is_refused_for_space(message)
                        else "couldn't finish")
                box = QMessageBox(QMessageBox.Warning, self._EXPORT_FAIL_TITLE,
                                  f"{APP_NAME} {what} the {self._video_kind(spec)}.\n\n"
                                  f"{self._export_failure_message(message, spec.out_path)}")
                # A refusal's message is the guard's own sentence and is already the body: behind
                # Show Details it only said the same thing twice (EXP-6). Everything else keeps the
                # encoder's words there, for a bug report.
                if not export_video.is_refused_for_space(message):
                    box.setDetailedText(message)
                box.addButton(QMessageBox.Close)
                box.exec()

            worker.progress.connect(on_progress)
            worker.finished_export.connect(on_done)
            dlg.canceled.connect(worker.cancel)
            # After a GUI-thread gc.collect(), as every worker starts: the render runs Python on
            # its own thread for minutes, where a collect could free a cyclic Qt widget.
            workers.start_worker(worker)

        def on_cancel():
            # Cancelling stops the BATCH, not just the file being written: a queue that carried on
            # to lap 5 after the user pressed Cancel on lap 4 would be a cancel button that does
            # not cancel.
            state["cancelled"] = True

        dlg.canceled.connect(on_cancel)
        _start(0)
        dlg.exec()
    def _batch_cancelled(self, kept: list[str], total: int) -> None:
        """A batch cancelled after some of its files had FINISHED says which ones it kept (EXP-9).

        Cancel removes the file in flight, and only that one: the laps already written are whole,
        correct exports and stay. The status line used to say "video export cancelled" and no
        more, so an 18 MB lap-1 file sat in the folder unmentioned — a cancel that reads as "nothing
        was written" while something was."""
        folder = os.path.dirname(os.path.abspath(kept[0]))
        names = [os.path.basename(p) for p in kept]
        listed = "\n".join(names[:5]) + (f"\n… and {len(names) - 5} more" if len(names) > 5 else "")
        files = "file" if len(kept) == 1 else "files"
        box = QMessageBox(QMessageBox.Information, f"{APP_NAME} — export cancelled",
                          f"{APP_NAME} stopped the export after {len(kept)} of {plural(total, 'file')}. "
                          f"The finished {files} {'was' if len(kept) == 1 else 'were'} kept:\n\n"
                          f"{listed}\n\n{folder}", parent=self.win)
        reveal_btn = box.addButton("Reveal in Finder", QMessageBox.ActionRole)
        box.setDefaultButton(box.addButton("Done", QMessageBox.AcceptRole))
        box.exec()
        self.win.statusBar().showMessage(
            f"video export cancelled — kept {len(kept)} finished {files}", self._status_ms)
        if box.clickedButton() is reveal_btn:
            self.win._reveal_in_finder(folder)
    def _video_export_finished(self, out_paths, spec=None, lap: int | None = None,
                               syncs=None, notes=()) -> None:
        """The one thing a finished export owes the user: a plain sentence saying it finished, and
        the file it made.

        WHY A BOX AND NOT JUST THE STATUS LINE. Every other export here writes instantly and a
        transient status line is proportionate (`exported laps.csv`). An overlay video takes
        minutes, so the user is watching a modal when it ends — and an MP4, unlike a CSV, is a thing
        you then go and do something with. A QProgressDialog cannot host that: it lays its label,
        bar and single button out itself, and that button's click is wired to `canceled()`, so
        "flip Cancel to Done" would leave the app's terminal state on a signal named cancel with
        nowhere to put a reveal. The progress modal comes down and this replaces it.

        The status line the app already emitted is kept and moved AFTER the box, so it gets its full
        self._status_ms in the clear instead of expiring behind a modal — and it stays the same
        lower-case "exported <basename>" every other export writes.

        Reveal opens the CONTAINING FOLDER (see _reveal_in_finder) and reports both outcomes, so its
        message lands on top of the "exported" one — the more recent, more specific fact.

        A BATCH NAMES ITS COUNT AND ITS FOLDER rather than listing forty file names: the folder is
        what Reveal opens and what the user goes to, and forty rows in a message box is a wall.

        AN OVERLAY-ONLY FILE ALSO SAYS WHERE IT STARTS IN THE FOOTAGE (`syncs`, one `SourceSync`
        per file): it is a track to lay back over that footage in an editor, and the owner's first
        one arrived with no timecode and no word of where it began.

        AND A PRORES ONE SAYS WHAT IT IS, because this box is where the owner meets it: the next
        click is Reveal and a double-click, and QuickTime then shows "completely black" video
        timed 11:24:30:00 onwards (the owner, 2026-09-25). Both are right for an editor and wrong
        for watching, so the box says so and names the row that makes a video to watch."""
        paths = [out_paths] if isinstance(out_paths, str) else list(out_paths)
        if not paths:
            return
        folder = os.path.dirname(os.path.abspath(paths[0]))
        what = self._describe_spec(spec, lap) if spec is not None else "an overlay video"
        syncs = list(syncs or [])
        png = bool(getattr(spec, "is_png_sequence", False))
        timed = (len(syncs) == len(paths)
                 and all(s is not None and s.timecode for s in syncs))
        if len(paths) > 1:
            body = f"{APP_NAME} exported {len(paths)} overlay videos.\n\n{folder}"
            status = f"exported {len(paths)} overlay videos"
            if not png and timed:
                body += "\n\nEach file carries the timecode of where it starts in the footage."
        else:
            name = os.path.basename(paths[0])
            body = (f"{APP_NAME} exported {what} as {self._video_kind(spec, article=True)}.\n\n"
                    f"{name}\n{folder}")
            status = f"exported {name}"
            where = export_video.sync_sentence(syncs[0] if syncs else None, png=png)
            if where:
                body += f"\n\n{where}"
        # Read with getattr: a compare spec and the tests' duck-typed ones carry no overlay config.
        if getattr(getattr(spec, "config", None), "overlay_only", False) and not png:
            seen = ("looks black in QuickTime and runs on the footage's timecode rather "
                    "than from 0:00; both are what an editor lines it up by" if timed else
                    "looks black in QuickTime; an editor lays it over the footage")
            body += (f"\n\nA transparent overlay track {seen}. For a video to watch or share, "
                     f"export again with “{self._EXPORT_CONTENT_OPTIONS[0][0]}”.")
        # An extra that was ticked but sat this export out says so (corner cards on a frame too
        # small for them): a tick that silently did nothing would read as a defect.
        if notes:
            body += "\n\n" + " ".join(notes)
        # The body carries the product name: macOS drops the window title (see _EXPORT_FAIL_TITLE).
        box = QMessageBox(QMessageBox.Information, f"{APP_NAME} — export finished", body,
                          parent=self.win)
        reveal_btn = box.addButton("Reveal in Finder", QMessageBox.ActionRole)
        done_btn = box.addButton("Done", QMessageBox.AcceptRole)
        box.setDefaultButton(done_btn)
        box.exec()
        self.win.statusBar().showMessage(status, self._status_ms)
        if box.clickedButton() is reveal_btn:
            self.win._reveal_in_finder(folder)
