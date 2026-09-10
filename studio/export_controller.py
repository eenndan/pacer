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
renderer (`export_video.py`), and the window's own chrome. `_grab_png` lives here because the two
map-grab helpers that use it are export-only callers.

TWO DELIBERATE SHAPES, both to keep the import one-way (app -> controller, never back):

  * `STATUS_MS` is app.py's module constant and is PASSED IN at construction, not imported.
  * `ExportChoice` moved here with the picker that returns it, and app.py RE-EXPORTS it — so
    `studio.app.ExportChoice` still resolves for the tests that name it that way.
"""
from __future__ import annotations

import math
import os
from typing import NamedTuple

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, export_data, export_video, prefs, theme
from ._signal import lap_label
from .session import fmt_time
from .workers import VideoExportWorker


class ExportChoice(NamedTuple):
    """What the overlay-video options picker returns (None on cancel): the overlay `config` and
    `lead`, the seconds of run-up/run-off to add at each end of the lap.

    `lead` is deliberately NOT folded into `config`. An `OverlayConfig` carries layout and output
    knobs — resolution, quality, unit, palette — while the padding decides the export's WINDOW,
    which `export_video.build_lap_spec` has to widen before it can even resolve which chapter
    file(s) the clip comes from. Two different jobs, two different values."""
    config: object
    lead: float


class ExportController:
    """Owns the export cluster for ONE `StudioWindow`. Built once, in the window's constructor."""

    def __init__(self, win, status_ms: int):
        self.win = win
        self._status_ms = status_ms

    # ----------------------------------------------------------- data export (F11)
    # File ▸ Export Qt side (the writers are Qt-free in export_data.py).

    # WHY a gated export action is off. A disabled row that describes its feature tells you nothing
    # about how to reach it, and all of the gated ones did exactly that; Qt keeps showing a disabled
    # action's tooltip, so this is the only surface a greyed row has. Each string names the
    # CONDITION and the way out of it.
    _NO_LAPS_REASON = ("No complete laps in this recording — drag the start/finish line on the map "
                       "to set where a lap begins, then export.")
    _PROVISIONAL_REASON = ("This recording's timing is provisional: the start line was auto-fitted, "
                           "not confirmed by you. Save it as a track (File ▸ Save as track…) to "
                           "confirm it.")
    _NO_TRACK_REASON = ("Needs a complete lap and a GPS position — there are no usable timing lines "
                        "to promote into a reusable track.")
    _NO_LIBRARY_REASON = ("No recordings analysed yet — open a GoPro recording and it is remembered "
                          "here, with its track, date and best lap.")
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
        to the driver reviewing their own footage, an unverified brag card never is."""
        has = hasattr(self.win, "session")
        self.win._export_menu.setEnabled(has)
        has_laps = has and self.win._has_valid_laps()
        for action in (self.win._export_laps_action, self.win._export_channels_action,
                       self.win._export_report_action, self.win._copy_stats_action,
                       self.win._export_video_action):
            self.win._gate_action(action, has_laps, self._NO_LAPS_REASON)
        # The lap card also needs the timing to be TRUSTED. With a lap in hand the only thing
        # card_data can still be blocked on is the provisional start line, so the reason is exact.
        card_ok = has_laps and not self.win._share_card_blocked()
        self.win._gate_action(self.win._share_card_action, card_ok,
                          self._NO_LAPS_REASON if not has_laps else self._PROVISIONAL_REASON)
        self.win._gate_action(self.win._copy_card_action, card_ok,
                          self._NO_LAPS_REASON if not has_laps else self._PROVISIONAL_REASON)
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
        # Snapshot the map + charts as they are on screen right now (QWidget.grab) — the
        # report writer itself stays Qt-free and just embeds the bytes. The panels are reached
        # through the live central view. The map goes through the REPORT-flavoured grab so the
        # document doesn't carry the app's editing chrome (see _grab_report_map_png).
        # Each snapshot carries the width the DOCUMENT must lay it out at — see
        # _report_image_width for why the exported page would otherwise depend on this Mac's
        # screen rather than on the session.
        map_png = self.win._grab_report_map_png(self.win.view.map)
        plots_png = self.grab_png(self.win.view.plots)
        images = [("Track map", map_png, self.win._report_image_width(map_png)),
                  ("Speed · Δ to best", plots_png, self.win._report_image_width(plots_png))]
        # The report is a HUMAN document whose embedded chart axis and map colour bar already read
        # in the display unit, so its lap table must too — a km/h table under an mph chart put two
        # different numbers for the same lap on one page. (The CSVs stay canonical SI: they are
        # machine-readable files, and export_data's writers pass no unit.)
        if self._run_export(lambda: export_data.write_report_html(
                path, self.win.session,
                source_label=self.win._loaded_label() or "session",
                images=images, unit=self.win._speed_unit), path):
            self.win.statusBar().showMessage(f"exported {os.path.basename(path)}", self._status_ms)
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
        except Exception as exc:  # noqa: BLE001 — a clipboard failure must not disrupt the app
            print(f"studio: stats summary not copied ({exc!r}).", flush=True)
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
        if export_video.is_out_of_space(message or ""):
            folder = os.path.dirname(out_path) or "that folder"
            return (f"There's no room left on the disk holding {folder}. Free some space, or "
                    f"choose somewhere else, and export again.")
        if "permission denied" in low or "operation not permitted" in low:
            return ("Pacer isn't allowed to write there. Choose a different folder — your Movies "
                    "or Desktop folder will work.")
        if "no such file or directory" in low and "ffmpeg" not in low:
            return ("The folder you chose isn't there any more — it may have been moved, renamed "
                    "or unmounted. Choose another one and export again.")
        if "cancelled" in low or "canceled" in low:
            return "The export stopped before it finished."
        return ("The encoder stopped partway through. The details below are what it reported — "
                "Help ▸ Report a problem… if it keeps happening.")
    def _run_export(self, write, path: str) -> bool:
        """Run a writer (`write()`) under an OSError guard; on failure show a warning dialog +
        statusbar note. Returns True on success."""
        try:
            write()
        except OSError as exc:
            QMessageBox.warning(self.win, "Export failed",
                                f"Could not write {os.path.basename(path)}:\n{exc}")
            self.win.statusBar().showMessage(f"export failed: {exc}", self._status_ms)
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
        path = self._export_save_path("Export lap card", "_lap_card.png", "PNG images (*.png)")
        if not path:
            return
        if self._run_export(lambda: self.win._save_card_png(image, path), path):
            self.win.statusBar().showMessage(f"saved {os.path.basename(path)}", self._status_ms)
    # ------------------------------------------------- video-overlay export (F9)
    # File ▸ Export overlay video Qt side (renderer is event-loop-free in export_video.py).

    # Resolution maps to OverlayConfig.out_height (never upscales past source; "Source" is a huge
    # sentinel clamped back to source height); quality maps to OverlayConfig.quality.
    # "1080p" resolution + "High" quality is the default.
    _EXPORT_RES_OPTIONS = [
        ("720p", 720), ("1080p", 1080), ("1440p", 1440), ("Source (no downscale)", 99999),
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
    # The picker's three choices persist across relaunches like every other UI choice (the unit, the
    # palette, the lap-panel tab). Kept as call-site keys on prefs' generic get/set: they mean
    # nothing outside this dialog, and prefs.py is a store, not a registry of every screen's state.
    _PREF_EXPORT_RES = "export_res_idx"
    _PREF_EXPORT_QUALITY = "export_quality_idx"
    _PREF_EXPORT_LEAD = "export_lead_idx"
    # SIZE ESTIMATE. The dialog sells a file-size trade-off ("larger file" / "smaller file"), so it
    # has to put a number on it — the two presets really are ~3x apart. The estimate is derived per
    # encoder, never a stored megabyte figure, because the encoder choice is a property of the
    # MACHINE (a box where no VideoToolbox session opens falls back to libx264 and lands several
    # times larger for the same preset):
    #   * VideoToolbox is bitrate-targeted, so export_video.vt_target_bitrate IS the answer;
    #   * libx264 is CRF-targeted and has no bitrate to read, so these are bits per pixel per frame
    #     measured on real GoPro footage with the overlays burned in (CRF 20 -> 0.68 at 1080p,
    #     CRF 23 -> 0.51 at 720p). A CRF stream's real size follows how much the picture MOVES, so
    #     this is an order of magnitude, and the dialog says "about".
    _X264_BPP = {20: 0.68, 23: 0.51}
    _X264_BPP_FALLBACK = 0.60         # an unknown CRF sits between the two measured points
    _EXPORT_ASPECT = 16 / 9           # assumed for the width; GoPro's landscape modes are 16:9
    def _export_pref_index(self, key: str, default: int, count: int) -> int:
        """One persisted combo index, clamped into `[0, count)` — the guarded-accessor shape the
        rest of prefs uses, so a stale value from an older build (a resolution that no longer
        exists) opens the dialog on the default instead of raising."""
        try:
            value = prefs.get(key, default)
        except Exception:  # noqa: BLE001 — an unreadable pref never blocks an export
            return default
        return value if isinstance(value, int) and 0 <= value < count else default
    def _remember_export_prefs(self, res_idx: int, quality_idx: int, lead_idx: int) -> None:
        """Persist the picker's three choices. Fully guarded, like set_last_dir: remembering a
        preference must never disrupt an export the user has already confirmed."""
        try:
            prefs.set(self._PREF_EXPORT_RES, int(res_idx))
            prefs.set(self._PREF_EXPORT_QUALITY, int(quality_idx))
            prefs.set(self._PREF_EXPORT_LEAD, int(lead_idx))
        except OSError as exc:
            print(f"studio: export preset not remembered ({exc!r}).", flush=True)
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
    def _export_size_hint(self, dur: float, out_height: int, quality: str) -> str:
        """The second line of the picker's hint: about how big this export lands, how many frames
        it has to render, and WHICH encoder will do it. Derived (see _X264_BPP) — never a stored
        megabyte figure, because the encoder is a property of the machine. "" when there is nothing
        honest to say: an unknown lap duration, or "Source", whose pixel count we can't know without
        an ffprobe this dialog deliberately does not run."""
        fps = export_video.OverlayConfig.fps_cap or 30.0
        if not (dur > 0) or out_height >= 99999:
            return ""
        frames = int(math.ceil(dur * fps))
        out_w = int(round(out_height * self._EXPORT_ASPECT))
        bpp, crf = export_video.quality_params(quality)
        encoder = export_video.resolve_encoder("auto")
        if encoder == export_video.VT_H264:
            bits_per_s = export_video.vt_target_bitrate(out_w, out_height, fps, bpp)
        else:  # libx264 is CRF-driven: no target bitrate exists, so use the measured bpp
            bits_per_s = out_w * out_height * fps * self._X264_BPP.get(crf, self._X264_BPP_FALLBACK)
        megabytes = bits_per_s * dur / 8 / 1e6
        return (f"About {megabytes:.0f} MB — {frames} frames to render at {fps:g} fps "
                f"with {encoder}. Real size follows how much the footage moves.")
    def _ask_export_options(self, lap: int):
        """Modal resolution + quality + run-up/run-off picker returning an `ExportChoice`, or None
        on cancel. All three choices persist across relaunches (prefs), like the unit and the
        palette."""
        dlg = QDialog(self.win)
        dlg.setWindowTitle(f"Export overlay video — lap {lap_label(lap)}")
        dlg.setMinimumWidth(400)

        root = QVBoxLayout(dlg)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QLabel(f"Export overlay video — lap {lap_label(lap)}")
        header.setProperty("role", "PanelHeader")
        root.addWidget(header)

        body = QWidget(dlg)
        col = QVBoxLayout(body)
        # A CONTROL surface, not a prose one: two combos, a form and a button row, with two note
        # lines about them. So it takes the panel gutter (SPACE_M) rather than the Help cards'
        # SPACE_XL reading inset — this dialog is operated, not read. It shipped 16/14/16/14 with a
        # 10 px block gap, under the same exemption that called the Help cards "off the scale and
        # off it CONSISTENTLY"; they were three different insets for the same job.
        col.setContentsMargins(theme.SPACE_M, theme.SPACE_M, theme.SPACE_M, theme.SPACE_M)
        col.setSpacing(theme.SPACE_M)
        root.addWidget(body)

        desc = QLabel("Burns the overlays into your footage: g-meter, Δ / speed, map inset and the "
                      "lap strip.")
        desc.setWordWrap(True)
        desc.setProperty("role", "Note")
        col.addWidget(desc)

        # lap_time is a cheap pacer-free accessor (no ffprobe).
        dur = self.win.session.lap_time(lap) if hasattr(self.win, "session") else float("nan")
        lap_line = QLabel(f"Lap {lap_label(lap)}  ·  {fmt_time(dur)}")
        lap_line.setProperty("role", "Note")
        col.addWidget(lap_line)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(theme.SPACE_M)
        form.setVerticalSpacing(theme.SPACE_S)
        res_combo = QComboBox(dlg)
        for label, _h in self._EXPORT_RES_OPTIONS:
            res_combo.addItem(label)
        res_combo.setCurrentIndex(                                        # default 1080p
            self._export_pref_index(self._PREF_EXPORT_RES, 1, len(self._EXPORT_RES_OPTIONS)))
        q_combo = QComboBox(dlg)
        for label, _q in self._EXPORT_QUALITY_OPTIONS:
            q_combo.addItem(label)
        q_combo.setCurrentIndex(                                          # default High
            self._export_pref_index(self._PREF_EXPORT_QUALITY, 0, len(self._EXPORT_QUALITY_OPTIONS)))
        lead_combo = QComboBox(dlg)
        for label, _s in self._EXPORT_LEAD_OPTIONS:
            lead_combo.addItem(label)
        lead_combo.setCurrentIndex(                                       # default None
            self._export_pref_index(self._PREF_EXPORT_LEAD, 0, len(self._EXPORT_LEAD_OPTIONS)))
        form.addRow("Resolution", res_combo)
        form.addRow("Quality", q_combo)
        form.addRow("Run-up / run-off", lead_combo)
        col.addLayout(form)

        # States the target height + never-upscale rule (no ffprobe here; matches output_size()),
        # THEN what the two combos actually cost. "Larger file"/"smaller file" named no size at all,
        # on a choice that spans ~3x — and the default is the expensive end of it.
        hint = QLabel("")
        hint.setWordWrap(True)
        # [role="Hint"] ranks BELOW the description above it by SIZE, not by a dimmer colour: this
        # label read C.text_muted, which is 3.17:1 and reserved by contract for disabled chrome —
        # enabled prose in an enabled dialog had quietly borrowed the disabled token.
        hint.setProperty("role", "Hint")
        col.addWidget(hint)

        def _update_hint():
            h = self._EXPORT_RES_OPTIONS[res_combo.currentIndex()][1]
            quality = self._EXPORT_QUALITY_OPTIONS[q_combo.currentIndex()][1]
            lead = self._EXPORT_LEAD_OPTIONS[lead_combo.currentIndex()][1]
            if h >= 99999:
                lines = ["Output: source resolution (never upscaled) — size follows your footage."]
            else:
                lines = [f"Output: up to {h}p tall, source aspect — never upscaled past source."]
            # The clip's REAL length, through the exporter's own funnel, so the megabytes and the
            # frame count below are the ones this export will actually render — and so the run-up
            # a recording cannot give (the first lap, the last lap) is reported as what remains
            # rather than as what was asked for.
            clip = self._export_clip_seconds(lap, lead)
            if lead and clip > 0 and math.isfinite(dur):
                lines.append(f"Clip: {fmt_time(clip)} — the lap plus {clip - dur:.1f} s of "
                             "footage around it.")
            size = self._export_size_hint(clip, h, quality)
            if size:
                lines.append(size)
            hint.setText("  ".join(lines))
        # ALL THREE combos: the quality choice is the one the copy sells hardest, and the run-up
        # changes both numbers in the size line by changing how long the clip is.
        res_combo.currentIndexChanged.connect(_update_hint)
        q_combo.currentIndexChanged.connect(_update_hint)
        lead_combo.currentIndexChanged.connect(_update_hint)
        _update_hint()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dlg)
        buttons.button(QDialogButtonBox.Ok).setText("Export")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        col.addWidget(buttons)
        if dlg.exec() != QDialog.Accepted:
            return None
        ri, qi = res_combo.currentIndex(), q_combo.currentIndex()
        li = lead_combo.currentIndex()
        self._remember_export_prefs(ri, qi, li)   # survives this window, and the relaunch
        out_height = self._EXPORT_RES_OPTIONS[ri][1]
        quality = self._EXPORT_QUALITY_OPTIONS[qi][1]
        # Burn the current display unit + semantic palette into the overlay so the export matches the
        # on-screen readout (incl. the colour-blind Δ hue axis — the exported clip is the shared
        # artifact, so it must follow the user's colour-blind choice, not stay red/green).
        return ExportChoice(
            config=export_video.OverlayConfig(out_height=out_height, quality=quality,
                                              speed_unit=self.win._speed_unit,
                                              palette=theme.active_palette()),
            lead=self._EXPORT_LEAD_OPTIONS[li][1])
    # Every failure this export can raise says the product's name IN THE BODY. macOS drops a
    # QMessageBox's window title (documented at _show_error_report and _load_failure_dialog, pinned
    # by test_app_chrome), so "The render failed: …" used to arrive as an unattributed sentence in a
    # titleless box — the load path was fixed for exactly this and the export never was.
    _EXPORT_FAIL_TITLE = f"{APP_NAME} — could not export video"
    def export_overlay_video(self):
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
        lap = self._export_lap_id()  # the primary/selected lap, falling back to the best lap
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
        # Pick resolution + quality + run-up FIRST (so a cancel here writes nothing), then the
        # save path.
        choice = self._ask_export_options(lap)
        if choice is None:
            return
        out = self._export_save_path(f"Export overlay video — lap {lap_label(lap)}",
                                     f"_lap{lap_label(lap)}_overlay.mp4", "MP4 video (*.mp4)")
        if not out:
            return
        # Resolve the (run-up widened) lap window to its chapter file(s) + local seek; refuses a bad
        # window with a ValueError rather than launching a doomed ffmpeg. The padding goes through
        # build_lap_spec rather than being applied here, because the window has to be widened
        # BEFORE the video source is resolved — a lead-in can reach back over a chapter seam.
        try:
            spec = export_video.build_lap_spec(self.win.session, out, lap, config=choice.config,
                                               lead_in=choice.lead, lead_out=choice.lead)
        except ValueError as exc:
            QMessageBox.warning(self.win, self._EXPORT_FAIL_TITLE,
                                f"{APP_NAME} can't export this lap:\n{exc}")
            return
        self._run_video_export(spec, lap)
    def _run_video_export(self, spec, lap: int):
        """Run the render on a worker QThread behind a cancellable modal dialog. Starts indeterminate
        ("Preparing…"), flips to a determinate bar on the first frame's progress, and ALWAYS reaches
        a terminal state: the modal comes down the moment the render stops, whatever the outcome.

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
        hands off to _video_export_finished."""
        dlg = QProgressDialog(f"Preparing lap {lap_label(lap)} overlay video…", "Cancel", 0, 0, self.win)
        dlg.setWindowTitle("Export overlay video")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setValue(0)  # with max=0 too, Qt renders an indeterminate "busy" bar

        worker = VideoExportWorker(self.win.session, spec)
        self.win._video_worker = worker  # keep a ref so the thread isn't GC'd mid-render
        # AND put it in the DRAINED set. It was held on that attribute and nowhere else, so
        # closeEvent's drain — which exists precisely so no QThread is destroyed mid-run — walked
        # straight past the one worker that can still be running MINUTES after it started, and
        # quitting mid-render destroyed a live QThread. closeEvent cancels it before draining (the
        # renderer checks the flag once per frame), so joining it costs about one frame.
        self.win._load_workers.add(worker)
        started = {"first": False}

        def on_progress(done: int, total: int):
            if total > 0:
                if not started["first"]:
                    # First real frame: switch from the busy "Preparing…" bar to a determinate one.
                    started["first"] = True
                    dlg.setLabelText(f"Rendering lap {lap_label(lap)} overlay video…")
                dlg.setMaximum(total)
                dlg.setValue(done)

        def on_done(ok: bool, message: str):
            # The render is over, so the button can no longer mean "cancel": drop the connection
            # BEFORE the dialog goes, then hide it. (On the cancel path the dialog is already
            # hidden — QProgressDialog::cancel() force-hides regardless of autoClose — and this is
            # simply a no-op.)
            dlg.canceled.disconnect(worker.cancel)
            dlg.hide()
            worker.wait()
            self.win._load_workers.discard(worker)
            self.win._video_worker = None
            spec.source.cleanup()  # free any temp concat-list file the chapter resolution wrote
            if ok:
                self._video_export_finished(spec.out_path, lap)
            elif message == "cancelled":
                self.win.statusBar().showMessage("video export cancelled", self._status_ms)
            else:
                # PLAIN LANGUAGE FIRST, the encoder's own words behind Details — the same shape as
                # the load-failure table and the crash report. `message` is an ffmpeg stderr TAIL:
                # pasting it as the body handed the user "[h264_videotoolbox @ 0x…] Error encoding
                # frame: -12905" as the explanation of what to do next.
                box = QMessageBox(QMessageBox.Warning, self._EXPORT_FAIL_TITLE,
                                  f"{APP_NAME} couldn't finish the overlay video.\n\n"
                                  f"{self._export_failure_message(message, spec.out_path)}")
                box.setDetailedText(message)
                box.addButton(QMessageBox.Close)
                box.exec()

        worker.progress.connect(on_progress)
        worker.finished_export.connect(on_done)
        dlg.canceled.connect(worker.cancel)
        worker.start()
        dlg.exec()
    def _video_export_finished(self, out_path: str, lap: int) -> None:
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
        message lands on top of the "exported" one — the more recent, more specific fact."""
        name = os.path.basename(out_path)
        folder = os.path.dirname(os.path.abspath(out_path))
        # The body carries the product name: macOS drops the window title (see _EXPORT_FAIL_TITLE).
        box = QMessageBox(QMessageBox.Information, f"{APP_NAME} — export finished",
                          f"{APP_NAME} exported lap {lap_label(lap)} as an overlay video.\n\n"
                          f"{name}\n{folder}", parent=self.win)
        reveal_btn = box.addButton("Reveal in Finder", QMessageBox.ActionRole)
        done_btn = box.addButton("Done", QMessageBox.AcceptRole)
        box.setDefaultButton(done_btn)
        box.exec()
        self.win.statusBar().showMessage(f"exported {name}", self._status_ms)
        if box.clickedButton() is reveal_btn:
            self.win._reveal_in_finder(folder)
