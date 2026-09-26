"""LibraryController — what pacer REMEMBERS about the driver, lifted out of `StudioWindow` (§7.1).

The second half of review §7.1's plan ("extract ExportController, then the library cluster"). Same
pattern and the same reason as `export_controller`, `scrub_controller` and `compare_controller`:
one object owns one cluster, the window holds it as `self.library_ctl`, and nothing here reaches
window state except through `self.win`.

WHAT IT OWNS — the app-support stores that outlive a session, and every gesture over them:
  * the session-library index: the load-time upsert and its personal-best MOMENT
    (`update_library`), the post-load refresh after a drag or a Save as track
    (`refresh_library_entry`), and the exclusions both share (`_library_excludes`);
  * the PB card's lifecycle (`show_pb_moment`, `_clear_pb_toast`, `_forget_pb_toast`) and where it
    may land (`_pb_card_keepout`);
  * File ▸ Library… and the privacy / portability controls injected into it — forget one
    recording, clear, restore, reveal, back up;
  * File ▸ Open Recent, which is a view of that same index;
  * the session records (File ▸ Session record…, the Library's editor callback, the lap panel's
    record chip);
  * the per-track focus list on the Coaching page;
  * the saved-tracks manager, including the rename that re-keys the library, the focus list and
    the records in one gesture (#295) and the delete that deliberately re-keys none of them.

WHY THIS BOUNDARY AND NOT A NARROWER ONE. Measured with an AST call graph over `StudioWindow`,
not chosen: the five library/PB methods the backlog named (`_update_library`,
`_refresh_library_entry`, `_show_pb_moment`, `_open_library`, `_forget_recording`) are NOT a
closed cluster on their own. They call the records (`_edit_session_record`, `_load_records`,
`_update_record_chip`), the focus list (`_update_focus_list`) and the track manager, and the
records call back into `_library_excludes` — a cycle, and the focus list and the track manager
sit on it too. Cutting at the five would have moved the coupling into two files. The smallest
strongly-connected set containing them is exactly the list above. What still crosses the
boundary is the window SHELL's own services — `_load`, `_apply_session_notice`, `_gate_action`,
the share-card pair, `_refresh_marks`, `_reveal_in_finder` and the reference load behind "Compare
with your previous PB" (`_compare_with_previous_pb`, which reads `previous_pb` back and calls
nothing here) — and none of them calls back into this cluster (a `_load` reaches `update_library`
again only when its worker finishes, on a later turn of the event loop).

WHAT IT DOES NOT OWN, and each is deliberate:
  * the MARKS (`_refresh_marks` and the rest). The privacy gestures call `self.win._refresh_marks()`
    three times, and nothing in marks calls back here, so marks sit outside the cycle; they are
    also the one store driven by the playhead and the video, not by the library.
  * `_reveal_in_finder`, shared with `ExportController` — it stays on the window both call.
  * File ▸ Save as track… (`_save_as_track`), a timing-trust gesture that ENDS by asking this
    controller to refresh the row.
  * three pieces of state that stay on the WINDOW because the window (or a tool) reads them:
    `_library_unwritable` (a clause of `_session_notice`), `_pb_toast` (read by
    `studio/dev/media_capture.py` and the lifecycle tests), and `_sidecar_path` (the load state
    `_disable_sidecar_if_open` clears).

TWO SHAPES KEEP THE IMPORT ONE-WAY (app -> controller, never back), as in `export_controller`:
`STATUS_MS` is passed in at construction, and nothing is imported from `app`. The logger keeps the
`studio.app` channel name these records were always written under (the stderr format prints it),
so moving the code did not rename a single log line.
"""
from __future__ import annotations

import logging
import math
import os
import shutil

# The Qt-object liveness probe (PySide6's own runtime): a Python wrapper outlives the C++ object a
# deleteLater() has collected, and _clear_pb_toast has to tell those two apart.
import shiboken6
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QFileDialog

from . import chapters, focus, library, session_record, sidecar, track_db
from . import marks as marks_model
from .library_dialog import LibraryDialog
from .overlays import PBToast
from .session import DEFAULT_SAMPLE, fmt_time
from .session_record_dialog import SessionRecordDialog

_log = logging.getLogger("studio.app")


def previous_pb_missing_text(entry: dict, missing_path: str | None) -> str:
    """What "Compare with your previous PB" says when that PB's footage is not on disk any more —
    plainly, naming the file and where it was, instead of a load failing on it (4 of the owner's 8
    library rows point at footage moved or deleted since). `missing_path` is the first recorded
    path that is gone, or None when the row recorded none. Here rather than in `library`, which
    is a data module: the ▸ menu path is window chrome."""
    track = entry.get("track") or "this track"
    best = entry.get("best")
    lap = f"Your previous best at {track}" + (f" ({fmt_time(float(best))})" if best is not None
                                              else "")
    if not missing_path:
        return f"{lap} has no footage on record, so there is nothing to compare it with."
    return (f"{lap} was recorded on footage that is no longer where Pacer saw it: "
            f"{os.path.basename(missing_path)} is missing from {os.path.dirname(missing_path)}. "
            "If you moved it, load it with Coaching ▸ Load reference recording… to compare.")


class LibraryController:
    """Owns the library / records / focus / saved-tracks cluster for ONE `StudioWindow`. Built once,
    in the window's constructor, BEFORE `_build_menu` (which wires File ▸ Library…, Open Recent,
    Session record…, Reveal and Back up straight to it)."""

    def __init__(self, win, status_ms: int):
        self.win = win
        self._status_ms = status_ms
        # What the last `update_library` learned for the debrief landing (board review PS-B1):
        # whether the index had NO row for this recording before the upsert — a first open, as
        # opposed to a reload or a second chapter — and where its best lap stands against the
        # track's PB (`library.pb_standing_for`). Reset on every call.
        self.opened_new = False
        self.pb_standing: dict | None = None
        # Whether the last `update_library` was PART of a recording the index does not hold yet
        # (QA NEW-1): it decided no verdict and wrote no row, so both wait for the whole recording.
        # Read by the session notice (which says so) and by `refresh_library_entry` (which must not
        # write that row behind its back). Reset on every call.
        self.waiting_for_whole = False
        # The library row the last `update_library`'s NEW personal best beat (`library.previous_pb`)
        # — what "Compare with your previous PB" loads as the reference (board review PS-B4). None
        # unless that load celebrated a beat. Reset on every call.
        self.previous_pb: dict | None = None

    # --------------------------------------------------------------- session library index (F8)
    def update_library(self, paths: list[str]) -> dict | None:
        """Upsert the just-loaded recording into the local session-library index. Fully guarded: a
        library write must never disrupt a load. Skips the bundled DEFAULT_SAMPLE and any recording
        with no valid laps (a junk row the library would surface forever).

        Returns the "new personal best" MOMENT (a library.pb_moment dict) or None. The moment is
        decided against the index AS IT IS BEFORE THIS SESSION IS UPSERTED, and ONLY when the timing
        is VERIFIED and NOT data-quality degraded — a PB against an arbitrary provisional start line
        is meaningless, and a PB whose absolute timing the app itself calls ESTIMATED (media-clock /
        low GPS) isn't one to celebrate, so we never celebrate either. The caller shows the
        celebratory banner from the returned moment; a library-write failure still returns the
        moment (the comparison already succeeded).

        Deciding it BEFORE the upsert is NOT what stops a recording being its own prior PB — that
        line used to claim it did, and the app celebrated exactly that: open one chapter, click
        this window's own "Load full recording", and the toast announced the full recording beating
        the chapter it had just chained, "0.57 s faster than your previous best". The comparison is
        by TRACK, and the partial load had already put this recording's own entry under that track
        (one upsert earlier, seconds ago). The ENTRY'S FINGERPRINT is what makes the promise true,
        so it is passed in: library.pb_moment partitions the index on it and takes the prior from
        the OTHER recordings — so the same outing can no longer be the bar, while a full chain that
        genuinely beats a DIFFERENT recording on that track still celebrates (see there for why
        suppressing on mere presence would swallow exactly that).

        PART OF A RECORDING DECIDES NOTHING (QA NEW-1). A load that is a strict subset of its
        recording's chapters on disk (`chapters.chapter_subset`) returns no moment, sets no standing
        and is never a first open: its best lap and ranked corners are a sample of the outing. On
        the owner's SD_19_09, chapter 1 announced "0:46.862, 0.05 s faster" for a true 0:46.808,
        0.10 s faster, and a focus list without the session's biggest loss. For a recording the
        index does NOT hold yet it also writes NO ROW, and that is what saves the verdict for the
        whole recording: a stored partial row makes the full load a re-open (no debrief), and
        `library.pb_moment`'s own-best rule then silences its PB as well. With no row, the whole
        recording's first load — Load full recording in that window, or any door later — is
        decided exactly as a drop decides it. The cost: until then the session is in neither the
        Library nor Open Recent. Only the command line can open part of a new recording now, and
        the session notice says what waits (`waiting_for_whole`). A recording the index already
        holds is still upserted — a part of it never displaces a fuller row (`library._keeps`)."""
        self.opened_new, self.pb_standing, self.previous_pb = False, None, None
        self.waiting_for_whole = False
        if self._library_excludes(paths):
            return None
        moment = None
        try:
            entry = self.win.session.library_entry(paths)
            prior_index = library.load()
            key = entry.get("fingerprint")
            new = not any(e.get("fingerprint") == key for e in prior_index.get("entries", []))
            if chapters.chapter_subset(paths) is not None:
                if new:
                    self.waiting_for_whole = True
                    return None
                library.upsert_and_save(entry)
                self.win._library_unwritable = False
                return None
            # Decide the PB moment against the PRIOR index (before the upsert), gated on BOTH timing
            # axes — a provisional/unconfirmed start line makes the lap number meaningless, and a
            # data-quality-degraded (media-clock / low-GPS ESTIMATED) time isn't one to celebrate
            # (library.pb_moment_for returns None for either) — and on this recording's own IDENTITY,
            # which is what keeps the chapter it just chained from being its "previous best".
            trust = (self.win.session.timing_verified, prior_index, entry.get("track"),
                     entry.get("best"))
            degraded = self.win.session.timing_quality.degraded
            moment = library.pb_moment_for(*trust, degraded=degraded, fingerprint_key=key)
            standing = library.pb_standing_for(*trust, degraded=degraded, fingerprint_key=key)
            # The row that beat was measured against, from the same PRIOR index: after the upsert
            # this recording's own row is the track's best. Kept even if the write below fails,
            # like the moment it belongs to.
            if (moment or {}).get("kind") == "beat":
                self.previous_pb = library.previous_pb(prior_index, entry.get("track"), key)
            library.upsert_and_save(entry)
            # Only once the row is WRITTEN: a recording whose row could not be saved would land on
            # the debrief again on every open.
            self.opened_new, self.pb_standing = new, standing
            self.win._library_unwritable = False
        except OSError:
            # The DISK said no. That is the one library failure the user can act on, so it is the
            # one that earns a notice; the traceback goes to the logger rather than a print.
            self.win._library_unwritable = True
            _log.exception("session library not updated")
        except Exception:  # noqa: BLE001 — the index is additive; never break a load
            # ANYTHING ELSE IS OUR BUG, and must not be reported as theirs. The notice tells the
            # user to check permissions on their app-support dir; saying that about a TypeError in
            # our own entry construction sends them to fix a filesystem that is fine. (Caught in
            # review: a stub session in test_load_failure raised AttributeError here and the
            # status bar duly advised the user about permissions.) Logged, not surfaced.
            _log.exception("session library not updated (not a write failure)")
        return moment

    def debrief_pb_line(self) -> str | None:
        """The debrief's PB sentence for the last `update_library` (None when it has none)."""
        return library.pb_standing_text(self.pb_standing, fmt_time) if self.pb_standing else None

    def offers_pb_compare(self, moment: dict | None) -> bool:
        """Whether a PB surface showing `moment` (a ``pb_moment`` or ``pb_standing`` dict) may offer
        "Compare with your previous PB": it is a BEAT, and the row the last `update_library`
        remembered is the one whose best that moment quotes as the previous best — the button
        loads the lap the sentence beside it names, or it is not offered."""
        row = self.previous_pb
        if not moment or moment.get("kind") != "beat" or row is None or row.get("best") is None:
            return False
        return abs(float(row["best"]) - float(moment.get("prior", math.nan))) < 1e-9

    def _library_excludes(self, paths: list[str]) -> bool:
        """True when this recording must stay OUT of the session library: the bundled DEFAULT_SAMPLE
        (not the user's driving) or a recording with no valid lap (a junk row the library would
        surface forever). Shared by the load-time upsert and every later refresh so a recording can
        never be admitted by one and refused by the other."""
        if any(os.path.abspath(p) == os.path.abspath(DEFAULT_SAMPLE) for p in paths):
            return True
        return not self.win.session.valid_lap_ids()

    def refresh_library_entry(self):
        """Re-write the loaded recording's library entry from the session AS IT NOW STANDS.

        The entry — track name, the three trust flags, the best/theoretical lap times — used to be
        written ONLY on the load path, so it froze at load time and every later gesture that changed
        what `Session.library_entry()` reports silently desynced the index from the app:

          * File ▸ Save as track… names the circuit and makes the session Verified. The Library row
            kept painting "unknown track · provisional" in italics, `is_trustworthy` stayed False,
            and the lap was silently ABSENT from the PB progression of the track it had just created
            — `prior_best`/`pb_series` for that name were empty until the user happened to re-open
            the file (QA W7-02).
          * A start/finish drag confirms the timing AND re-times every lap. The entry kept both the
            provisional flag and the pre-drag `best`, so the library's PB history was quoting lap
            times the app no longer shows anywhere.

        Deliberately NOT a PB moment: the celebration is decided once, on load, against the index as
        it stood BEFORE this session entered it (see update_library). Re-deciding it here would
        re-fire the toast on every drag, and against an index that already contains this session.

        Fully guarded, like the load path: the index is additive, so a library-write failure logs
        and is never allowed to disrupt the session (saving a track must not become a way to crash).
        """
        paths = getattr(self.win, "_paths", None)
        if not paths or not hasattr(self.win, "session"):
            return
        try:
            if self._library_excludes(paths):
                return
            # Part of a recording the index does not hold yet has no row by design (update_library):
            # writing one from a drag would turn the whole recording's first load into a re-open.
            if not self.waiting_for_whole:
                library.upsert_and_save(self.win.session.library_entry(paths))
                self.win._library_unwritable = False
        except OSError:
            self.win._library_unwritable = True
            _log.exception("session library entry not refreshed")
            # this path runs on a drag, long after the load notice
            self.win._apply_session_notice()
        except Exception:  # noqa: BLE001 — the index is additive; never break the session
            _log.exception("session library entry not refreshed (not a write failure)")
        # A drag re-times every lap, so every focus measurement taken over this session is stale —
        # and it can also CONFIRM the start line, which is one of the gates the verdict reads.
        self.update_focus_list()

    # ----------------------------------------------------------------- the personal-best moment
    def show_pb_moment(self, moment: dict):
        """Show the transient "new personal best!" toast for a ``library.pb_moment`` result. Fully
        guarded — a celebration must never disrupt a load. The toast's "See your progress →" link
        opens the Library dialog's per-track PB-progression chart (the retention surface), and it
        auto-dismisses. Held on the WINDOW (`win._pb_toast`) so a rapid reload replaces the old one.

        CLEARING THE PREVIOUS CARD IS ITS OWN STEP, OUTSIDE THIS TRY, and that is the whole repair
        for a window that celebrated at most once. `PBToast.dismiss()` ends in `deleteLater()`, so
        after one turn of the event loop the C++ half is gone while this attribute still held the
        Python wrapper; the next moment's `old.dismiss()` then raised on the deleted QTimer — INSIDE
        the try — and the blanket except printed "personal-best moment not shown" and returned
        before the new card was ever built. Measured on this method: second moment, RuntimeError
        ("Internal C++ object (QTimer) already deleted"), zero toasts on screen. Every genuine PB
        after the first one was silently swallowed, and §3.2's false partial→full toast was usually
        the one that spent the single slot. Tidying up after the last celebration must not be able
        to cancel the next one, so it happens first, guarded on its own (`_clear_pb_toast`) — with
        its own blanket except, so "fully guarded" above still holds for the whole method and the
        load path behind it."""
        self._clear_pb_toast()
        try:
            title, body = library.pb_moment_text(moment, fmt_time)
            # Offer the one-tap share only when the card is actually shareable (verified lap) —
            # a PB moment is verified timing by construction, but stay honest via the same verdict
            # — and only for a PB that beat something: a first session has nothing to beat, and
            # "Share your PB →" under "the time to beat next time" was the card contradicting
            # itself (board review UX-9a). The lap card is still File ▸ Export's, any time.
            beat = moment.get("kind") == "beat"
            on_share = None if not beat or self.win._share_card_blocked() \
                else self.win._share_pb_card
            # A beat offers the ANALYSIS gesture as the card's primary action (board review
            # PS-B4); the share card stays, as a secondary link.
            on_compare = (self.win._compare_with_previous_pb if self.offers_pb_compare(moment)
                          else None)
            toast = PBToast(title, body, on_progress=self.open_library,
                             on_share=on_share, on_compare=on_compare, parent=self.win)
            self.win._pb_toast = toast
            # Let the reference die with the object it names, so this window never holds the wrapper
            # of a deleted card — the state the defect above was made of, and the one every OTHER
            # reader of `win._pb_toast` (studio/dev/media_capture.py calls `.close()` on it)
            # would hit.
            toast.destroyed.connect(lambda *_: self._forget_pb_toast(toast))
            toast.show_for(self.win, keepout=self._pb_card_keepout)
        except Exception:  # noqa: BLE001 — a celebration must never break a load
            _log.warning("personal-best moment not shown", exc_info=True)

    def _forget_pb_toast(self, toast):
        """Drop a destroyed celebration card from `_pb_toast` — but only while it is still the one
        being held, since a card that was replaced rather than dismissed is destroyed AFTER its
        successor is on screen, and must not take that successor's reference with it."""
        if getattr(self.win, "_pb_toast", None) is toast:
            self.win._pb_toast = None

    def _clear_pb_toast(self):
        """Dismiss the celebration card still up, if there is one, and drop the reference either
        way — never raising into the caller (see `show_pb_moment`).

        The reference is cleared FIRST so even a failure here leaves no stale wrapper for the next
        moment to trip on, and `shiboken6.isValid` is what tells a live card from the Python wrapper
        of one whose C++ half `deleteLater` has already collected (the `destroyed` hook normally
        clears those, so this is the belt to its braces: the same-turn window before that signal
        has run, and any future path that assigns `_pb_toast` without it).

        The except is BLANKET on purpose, even though the failure this method exists for is a
        RuntimeError. Moving out of `show_pb_moment`'s try bought back the celebration but took
        the containment with it: `show_pb_moment` is called unguarded from `_on_session_loaded`
        immediately before `loadFinished.emit()`, and `dismiss()` runs Python of its own (`hide()`
        reaches the host's event filter), so ANY escape from here strands a completed load with no
        `loadFinished` — the §3.4 shape. Tidying up after a celebration may fail; it may not take
        the load with it."""
        old = getattr(self.win, "_pb_toast", None)
        self.win._pb_toast = None
        if old is None or not shiboken6.isValid(old):
            return
        try:
            old.dismiss()
        except Exception:  # noqa: BLE001 — see above: this must never reach the load path
            _log.warning("previous personal-best card not dismissed", exc_info=True)

    def _pb_card_keepout(self):
        """The rectangles the PB card must not cover, in this window's coordinates: the lap grid's
        SELECTED row, full viewport width, and the excluded-laps strip under the grid. None when
        there is neither to protect.

        WHY THE SELECTION IS THE RIGHT RECTANGLE. An overlay may cover rows; it may not cover the
        row the app has just put the user on. On the path this card fires from that row IS the ★
        session best — the load selects it and scrolls it into view — so the rectangle protected
        here is the one holding the very lap time the card is announcing. Read through the
        selection rather than through the lap table's own best-lap bookkeeping so this stays
        public Qt on a widget another module owns: a QAbstractItemView's selection, its
        `visualRect` and its viewport.

        AND THE STRIP UNDER THE GRID (board review UX-9a). The card sits at the bottom of the lap
        panel's body, which is where the Laps page keeps its "N excluded" strip: measured at
        1440x900 on SD_30_08 the card covered 30 px of that strip's 42, across all 295 px of its own
        width — the count and the note under it — for the card's whole life.

        Returns None — i.e. "place the card as before" — for every uncertainty: no view, no grid,
        the Laps page not the one on screen (the grid is then not visible), nothing selected, the
        selected row scrolled out of the viewport, or any raise at all. A celebration must never
        break a load, and this runs three times per celebration."""
        try:
            table = getattr(getattr(self.win, "view", None), "table", None)
            grid = getattr(table, "table", None)
            if grid is None or not grid.isVisible():
                return None
            keep = []
            strip = table.excluded_strip() if hasattr(table, "excluded_strip") else None
            if strip is not None and strip.isVisible():
                keep.append(QRect(strip.mapTo(self.win, QPoint(0, 0)), strip.size()))
            band = self._selected_row_band(grid)
            if band is not None:
                keep.append(band)
            return keep or None
        except Exception:  # noqa: BLE001 — placement is best-effort; never fail a load
            _log.warning("personal-best card keep-out not resolved", exc_info=True)
            return None

    def _selected_row_band(self, grid) -> QRect | None:
        """The grid's selected row(s), full viewport width, in window coordinates; None when
        nothing is selected or the selection is scrolled out of the viewport."""
        model = grid.selectionModel()
        rows = model.selectedRows() if model is not None else []
        if not rows:
            return None
        viewport = grid.viewport()
        band = QRect()
        for index in rows:
            cell = grid.visualRect(index)
            band = band.united(QRect(0, cell.y(), viewport.width(), cell.height()))
        band = band.intersected(viewport.rect())
        if band.isEmpty():
            return None
        return QRect(viewport.mapTo(self.win, band.topLeft()), band.size())

    # -------------------------------------------------------------------------- File ▸ Library…
    def open_library(self):
        """File ▸ Library…: open the session-library dialog (a sortable list of analyzed
        recordings + per-track PB progression). Re-opening an entry routes back through the
        guarded `_load` path; the dialog reads the index defensively (empty when missing). The
        privacy controls (forget one recording / clear the library) are injected here — the dialog
        stays pacer-free + file-op-free, the app owns the index write + sidecar delete."""
        dlg = LibraryDialog(library.load(), open_recording=self.win._load, parent=self.win,
                            forget_recording=self._forget_recording,
                            clear_library=self._clear_library,
                            reveal_library=self.reveal_library,
                            backup_library=self.backup_library,
                            # The way back from "Clear library". Without these two the dialog
                            # builds no Restore… at all, and PR #168's backup is a file the app
                            # can write and never read — the half-feature its own docstring
                            # names. `backup_info` is what the confirm shows, so the user sees
                            # both sides of the swap before it happens.
                            restore_library=self._restore_library,
                            backup_info=library.backup_summary,
                            # The session records, joined to the index on the fingerprint: DATA in
                            # (the two comparability columns + the conditions filter read it), the
                            # EDITOR as a callback (the app owns every write), and a re-read for
                            # the three gestures that change the store behind the dialog's back
                            # (forget / clear / restore).
                            records=self._load_records(),
                            edit_record=self._edit_session_record,
                            reload_records=self._load_records,
                            # The saved-TRACK list is a different store from the session index, but
                            # it is the same question ("what has pacer remembered about my
                            # driving?") and this dialog is already where the app answers it.
                            manage_tracks=self._open_track_manager,
                            # The whole privacy account, one click from the note's one line.
                            show_privacy=self.win._show_privacy)
        dlg.exec()
        # Every record write in there already refreshed both readers (`_records_changed`); this
        # re-read is for a callback that raised part-way, which the dialog swallows.
        self._records_changed()

    # -------------------------------------------------------- privacy: restore / forget / clear
    def _restore_library(self) -> dict:
        """Put the automatic backup back as the live index and return the result, for the dialog to
        re-render. Mirrors `_clear_library`, its inverse, exactly: guarded, returns an index either
        way, and never raises into the dialog.

        `library.restore` refuses a missing, unreadable or empty backup by returning the current
        index unchanged — so a refusal here is silent by design, and the dialog only offers the
        button when `backup_summary` reports something restorable."""
        try:
            library.restore()
        except OSError as exc:
            _log.error("could not restore the library index (%r)", exc)
        # The records' own backup is swapped back with it — the same gesture undid the same wipe,
        # so it has to undo both halves or "Restore…" would put the rows back without the notes.
        # session_record.restore refuses an empty/missing backup the same way library.restore does,
        # so a library with records that were never backed up is left alone rather than emptied.
        try:
            session_record.restore()
        except OSError as exc:
            _log.error("could not restore the session records (%r)", exc)
        # The marks' backup comes back with them — one gesture, one undo. `marks.restore` refuses an
        # empty/missing backup the same way the other two do, so a library whose marks were never
        # backed up is left alone rather than emptied.
        try:
            marks_model.restore()
        except OSError as exc:
            _log.error("could not restore the marks (%r)", exc)
        if getattr(self.win, "view", None) is not None:
            self.win._refresh_marks()
        self._records_changed()
        return library.load()

    def _forget_recording(self, entry: dict) -> dict:
        """Privacy "forget this recording": drop `entry` from the library index AND delete its
        per-video `.pacer.json` timing-line sidecar, then return the fresh index (for the dialog to
        re-render). The media file is NEVER touched. Fully guarded — a failed index write or a
        missing/locked sidecar just logs; the deletion uses os.remove behind an existence check +
        try/except (never a shell rm)."""
        try:
            library.remove_and_save(entry.get("fingerprint"))   # one locked read-modify-write
        except OSError as exc:
            _log.error("could not update the library index (%r)", exc)
        # Delete the recording's sidecar (resolved from the FIRST recorded chapter path — the same
        # stem the sidecar was written under). Guarded end-to-end.
        paths = entry.get("paths") or []
        if paths:
            try:
                side = sidecar.sidecar_path(paths[0])
                # If the forgotten recording is the one CURRENTLY loaded, the live session still
                # holds this sidecar path — clear it FIRST so a passive timing nudge can't re-write
                # the file we're about to delete (an explicit re-save could re-establish it later).
                self._disable_sidecar_if_open(side)
                if os.path.exists(side):
                    os.remove(side)
                    _log.info("deleted timing-line sidecar %s", os.path.basename(side))
            except OSError as exc:
                _log.error("could not delete the sidecar (%r)", exc)
        # …and the session record written for it. "Forget this recording" has to mean the whole
        # recording: leaving the setup + conditions behind would keep a note about a session the
        # user just asked to be rid of, in a file the Library no longer shows a row for.
        # remove_and_save copies the store to its .bak FIRST — this is the one forgotten thing the
        # footage cannot give back.
        try:
            session_record.remove_and_save(entry.get("fingerprint") or "")
        except OSError as exc:
            _log.error("could not forget the session record (%r)", exc)
        except Exception:  # noqa: BLE001 — forgetting a record must never break the forget
            _log.exception("session record not forgotten")
        # …and the MARKS written against it, for exactly the same reason and with the same .bak
        # copy first. A driver who forgets a recording and leaves "baulked out of 4, again" behind
        # in a file no row points at has been told the recording is gone and has not been told the
        # truth. The derived marks need no forgetting: they were never written.
        try:
            marks_model.forget_and_save(entry.get("fingerprint") or "")
        except OSError as exc:
            _log.error("could not forget the marks (%r)", exc)
        except Exception:  # noqa: BLE001 — forgetting marks must never break the forget
            _log.exception("marks not forgotten")
        if getattr(self.win, "view", None) is not None:
            self.win._refresh_marks()
        self._records_changed()
        return library.load()

    def _disable_sidecar_if_open(self, forgotten_side: str) -> None:
        """When the recording being forgotten IS the currently-loaded session, null the live sidecar
        path on both the window and the central view so a subsequent passive timing nudge can't
        RE-CREATE the just-deleted ``.pacer.json`` (``CentralView._save_sidecar`` no-ops on an empty
        path). The window's ``_sidecar_path`` is also cleared so any rebuilt view stays de-linked.
        Matched by resolved sidecar path (chapter-invariant to how the sidecar was written); a
        no-match (forgetting a DIFFERENT recording) leaves the open session untouched."""
        live = getattr(self.win, "_sidecar_path", None)
        if not live or os.path.abspath(live) != os.path.abspath(forgotten_side):
            return
        self.win._sidecar_path = None
        view = getattr(self.win, "view", None)
        if view is not None:
            view._sidecar_path = None
        _log.info("cleared the open recording's sidecar link after forgetting it")

    def _clear_library(self) -> dict:
        """Privacy "clear library": wipe the whole app-support index (only the library history of
        what/where you recorded). The media files + their `.pacer.json` sidecars are left untouched.
        Returns the fresh (empty) index for the dialog to re-render. Guarded — a failed write logs
        and returns the current index unchanged."""
        try:
            library.clear()
        except OSError as exc:
            _log.error("could not clear the library index (%r)", exc)
        # The session records go with it: they ARE the personal history this control wipes, and a
        # user clearing the library for privacy would not expect their setup notes to survive it.
        # Backed up to session_records.json.bak first (session_record.clear), so Restore… below can
        # put both halves back — the dialog's confirm names both files.
        try:
            session_record.clear()
        except OSError as exc:
            _log.error("could not clear the session records (%r)", exc)
        # …and the marks, on the same argument and with the same marks.json.bak copy first. The
        # dialog's confirm names all three files and the Restore… beside it puts all three back.
        try:
            marks_model.clear()
        except OSError as exc:
            _log.error("could not clear the marks (%r)", exc)
        if getattr(self.win, "view", None) is not None:
            self.win._refresh_marks()
        self._records_changed()
        return library.load()

    # ------------------------------------------------------- data portability: reveal / back up
    def reveal_library(self) -> None:
        """Data portability: open the app-support FOLDER that holds ``library.json`` in Finder, so
        the durable index is findable/copyable. Reveals the DIRECTORY (created lazily on the first
        save; ``os.makedirs`` here so a never-saved library still opens to an existing folder rather
        than a Finder error), then hands off to the shared _reveal_in_finder."""
        directory = os.path.dirname(library.library_path())
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            _log.error("could not open the library folder (%r)", exc)
            self.win.statusBar().showMessage(f"could not open {directory}: {exc}",
                                             self._status_ms)
            return
        self.win._reveal_in_finder(directory)

    def backup_library(self) -> None:
        """Data portability: copy ``library.json`` to a user-chosen path (``QFileDialog`` →
        ``shutil.copy2``, preserving mtime). No-op with a gentle notice when there's no library yet
        (nothing analyzed) or the user cancels the dialog. Guarded — a failed copy just informs the
        user via the status bar (a backup failure must never disrupt the app)."""
        src = library.library_path()
        if not os.path.exists(src):
            self.win.statusBar().showMessage(
                "no library to back up yet — analyze a recording first", self._status_ms)
            return
        dest, _ = QFileDialog.getSaveFileName(
            self.win, "Back up library", os.path.join(os.path.expanduser("~"), "library.json"),
            "Library index (*.json)")
        if not dest:
            return  # user cancelled
        try:
            shutil.copy2(src, dest)
        except OSError as exc:
            _log.error("could not back up the library (%r)", exc)
            self.win.statusBar().showMessage(f"could not back up the library: {exc}",
                                             self._status_ms)
            return
        self.win.statusBar().showMessage(f"library backed up to {dest}", self._status_ms)

    # ----------------------------------------------------------------------- File ▸ Open Recent
    # Open Recent: recently analyzed recordings (most-recent-first), each re-opened via the guarded
    # `_load`. Sourced from the session-library index rather than a separate MRU list.
    _RECENT_LIMIT = 8

    def _recent_entries(self) -> list[dict]:
        """Open Recent candidates: openable library entries (valid laps, file present),
        most-recent-first by date, capped at _RECENT_LIMIT. Guarded: any failure yields [].
        An UNKNOWN-TRACK recording is a candidate like any other (it re-opens fine, and
        `_recent_label` already names it "unknown track") — matching library_dialog._entry_junk."""
        try:
            entries = library.load().get("entries", [])
        except Exception:  # noqa: BLE001 — the recents list is additive; never break the menu
            _log.warning("Open Recent unavailable", exc_info=True)
            return []
        usable = [
            e for e in entries
            if e.get("lap_count")
            and any(os.path.exists(p) for p in (e.get("paths") or []))
        ]
        # Newest first; missing date sorts last.
        usable.sort(key=lambda e: e.get("date") or "", reverse=True)
        return usable[:self._RECENT_LIMIT]

    def _recent_label(self, entry: dict) -> str:
        """A one-line Open Recent label: ``<track> — <best>  (<date>)`` from a library entry,
        gracefully degrading when a field is absent (an unknown-track or undated row)."""
        track = entry.get("track") or "unknown track"
        best = entry.get("best")
        parts = [track]
        if best is not None:
            parts.append(f"— {fmt_time(best)}")
        date = entry.get("date")
        if date:
            parts.append(f"({date})")
        return "  ".join(parts)

    def sync_recent_menu(self):
        """Rebuild the Open Recent submenu from the current library index. Called on the submenu's
        aboutToShow (so it always reflects the latest loads + on-disk state) and once at build time.
        Each entry re-opens via the guarded `_load` path with its recorded chapter paths. An empty
        recents list shows a single disabled "(none)" placeholder so the submenu is never blank."""
        self.win._recent_menu.clear()
        entries = self._recent_entries()
        if not entries:
            none_action = self.win._recent_menu.addAction("(none)")
            none_action.setEnabled(False)
            return
        for entry in entries:
            paths = list(entry.get("paths") or [])
            action = self.win._recent_menu.addAction(self._recent_label(entry))
            action.setToolTip(os.path.basename(paths[0]) if paths else "")
            # Bind THIS entry's paths into the slot (default-arg capture — a loop-closure over
            # `paths` would re-open whichever entry is last). Re-open through the same guarded
            # `_load` the Library dialog / File ▸ Open use, so the load guards + sidecar restore
            # + library upsert all apply identically.
            action.triggered.connect(lambda checked=False, p=paths: self.win._load(p))

    # ----------------------------------------------------- session records (setup + conditions)
    _NO_RECORD_REASON = ("Open a recording with at least one valid lap — a session record is "
                         "attached to a library row, and a recording with no laps has none")

    def sync_record_action(self) -> None:
        """Gate File ▸ Session record… on the one thing it needs: a loaded recording the LIBRARY
        will admit. The record is keyed on the library fingerprint and shown in the Library beside
        that recording's row, so a session the index refuses (the bundled sample, a recording with
        no valid lap) has nowhere to put one — and offering the form there would collect notes the
        user could never find again."""
        ok = hasattr(self.win, "session") and bool(self.win._paths) \
            and not self._library_excludes(self.win._paths)
        self.win._gate_action(self.win._record_action, ok, self._NO_RECORD_REASON)

    def _current_library_entry(self) -> dict | None:
        """The loaded recording's library entry — the identity a session record hangs off and the
        context the form auto-stamps. None when there is no session, no path, or the library
        excludes this recording. Guarded: building an entry walks the session's accessors, and a
        menu item must never raise."""
        if not hasattr(self.win, "session") or not self.win._paths:
            return None
        try:
            if self._library_excludes(self.win._paths):
                return None
            return self.win.session.library_entry(self.win._paths)
        except Exception:  # noqa: BLE001 — a record lookup must never raise into the UI
            _log.exception("session record: could not build the library entry")
            return None

    def edit_current_record(self) -> None:
        """File ▸ Session record…: write up the CURRENTLY-LOADED recording. The same editor the
        Library dialog opens for a selected row, pointed at this session — the difference is only
        where the entry comes from."""
        entry = self._current_library_entry()
        if entry is None:
            self.win.statusBar().showMessage("no session to write up", self._status_ms)
            return
        self._edit_session_record(entry)

    def _edit_session_record(self, entry: dict) -> dict:
        """Open the session-record editor for one library `entry`, persist the result, and return
        the fresh store. The Library dialog's injected `edit_record` callback AND the File-menu
        item's implementation — one function, so the two entry points cannot drift.

        The APP owns the write (the dialogs stay file-op-free, the rule the library controls
        already follow), and every write is guarded end-to-end: an unwritable app-support dir must
        never disrupt the app, and it is reported on the status bar rather than swallowed, because
        the note the user just typed is the one thing here that cannot be reconstructed."""
        store = self._load_records()
        fp = entry.get("fingerprint") or ""
        existing = session_record.get(store, fp)
        # A NEW record opens pre-filled from the driver's last session (chassis, axle, seat,
        # gearing, tyre set — and the tyre laps advanced by that session's own lap count). See
        # session_record.prefill: it is the whole of "fast to fill in after a session".
        record = existing if existing is not None else session_record.prefill(
            store, exclude=fp, entry=entry)
        paths = entry.get("paths") or []
        name = os.path.basename(paths[0]) if paths else (entry.get("stem") or "")
        dlg = SessionRecordDialog(record, entry=entry, name=name,
                                  is_new=existing is None, parent=self.win)
        if dlg.exec() != SessionRecordDialog.Accepted or not fp:
            return store
        try:
            if dlg.deleted():
                store = session_record.remove_and_save(fp)
                self.win.statusBar().showMessage("session record deleted", self._status_ms)
            else:
                result = dlg.result_record()
                store = session_record.put_and_save(fp, result)
                self.win.statusBar().showMessage(
                    "session record saved" if not session_record.is_empty(result)
                    else "session record cleared", self._status_ms)
        except OSError:
            _log.exception("session record not saved")
            self.win.statusBar().showMessage(
                "could not save the session record — check permissions on "
                "~/Library/Application Support/pacer", self._status_ms)
            return self._load_records()
        self._records_changed(store)
        return store

    def mark_sessions_dry(self, fingerprints: list) -> None:
        """The focus block's one-click answer to "no session record for 19 Jul and today": write a
        record saying the conditions were Dry — and nothing else — for each recording its button
        NAMED (``focus.Report.unrecorded``), then re-read both surfaces that show the store.

        Only a recording with NO record gets one (``session_record.put_if_blank_and_save``), so the
        click cannot overwrite anything the driver typed, even a record another window wrote after
        the page asked. Each record carries the same provenance a form save stamps (date, track and
        lap count off the recording's library row), so it reads the same standalone. Guarded like
        every other record write: a failure is reported on the status bar, never raised."""
        fps = [str(fp) for fp in fingerprints or [] if fp]
        if not fps:
            return
        try:
            rows = {e.get("fingerprint"): e for e in library.load().get("entries", [])}
            current = self._current_library_entry()
            if current:
                rows[current.get("fingerprint")] = current
            dry = {**session_record.blank_record(), "conditions": "dry"}
            store, written = session_record.put_if_blank_and_save(
                {fp: session_record.stamp_context(dry, rows.get(fp)) for fp in fps})
        except OSError:
            _log.exception("session records not saved")
            self.win.statusBar().showMessage(
                "could not save the session records — check permissions on "
                "~/Library/Application Support/pacer", self._status_ms)
            return
        n = len(written)
        self.win.statusBar().showMessage(
            f"{n} session record{'' if n == 1 else 's'} saved: Dry — File ▸ Session record… adds "
            "the rest" if n else "those sessions already have a record — nothing was changed",
            self._status_ms)
        self._records_changed(store)

    def _records_changed(self, store: dict | None = None) -> None:
        """Re-read the session-record store into BOTH surfaces that show it. Every write to the
        store ends here: the editor (save, clear, delete — from File ▸ Session record… and from the
        Library alike) and the Library's forget, clear and restore.

        A write used to refresh only the lap panel's chip. The Coaching page's focus verdict reads
        the same store — a record on each side of the comparison is one of its gates
        (`focus._blocker`) — and nothing re-read it, so a driver who did exactly what its refusal
        told him (File ▸ Session record… for today, then the Library's row for the other day)
        still read "no session record for 30 Aug and today" until he re-opened the recording
        (board review UX-5, measured on the working-set pair 0065 → 0068)."""
        self.update_record_chip(store)
        self.update_focus_list()

    @staticmethod
    def _load_records() -> dict:
        """The session-record store, guarded — a read that fails must leave the app usable, and
        ``session_record.load`` already self-heals every corruption it can name."""
        try:
            return session_record.load()
        except Exception:  # noqa: BLE001 — the guard must never raise out of a menu / dialog
            _log.exception("session records not read")
            return session_record.empty_store()

    def update_record_chip(self, store: dict | None = None) -> None:
        """Push the loaded recording's session record onto the LAP PANEL's header chip — the
        surface right above the lap times it qualifies.

        This is the "show it where the comparison happens" half that is not the Library: a driver
        reading a lap grid should be able to see, without leaving it, that these times were set on
        a wet day on a 300-lap set of tyres. Shown only when there IS a record; a permanent "no
        record" nag beside every lap grid would be a worse surface than none. Fully guarded — a
        decorative chip must never disrupt a load."""
        view = getattr(self.win, "view", None)
        if view is None or not hasattr(view, "set_session_record"):
            return
        try:
            entry = self._current_library_entry()
            record = session_record.get(store if store is not None else self._load_records(),
                                        (entry or {}).get("fingerprint") or "")
            view.set_session_record(record)
        except Exception:  # noqa: BLE001 — never let the chip break a load
            _log.warning("session-record chip not updated", exc_info=True)

    # ------------------------------------------------------- the focus list (the training loop)
    @staticmethod
    def _load_focus() -> dict:
        """The focus store, guarded — ``focus.load`` already self-heals every corruption it can
        name, and a read that still fails must leave the app usable."""
        try:
            return focus.load()
        except Exception:  # noqa: BLE001 — a store read must never raise into the UI
            _log.exception("focus list not read")
            return focus.empty_store()

    def update_focus_list(self) -> None:
        """Push the focus list + THIS session's verdict on it onto the Coaching page.

        The whole point of the feature is the gate, so the report is built even when it can say
        nothing: ``focus.verdict`` returns the refusals ("no session record for 23 May, so nothing
        says the two days were comparable") and the page states them. An empty report is the
        invitation state. Fully guarded — a training-loop read must never disrupt a load."""
        panel = getattr(getattr(self.win, "view", None), "opportunities", None)
        if panel is None or not hasattr(panel, "set_focus_report"):
            return
        try:
            entry = self._current_library_entry() or {}
            track = entry.get("track")
            if not track:
                # No detected track: there is nowhere to keep a per-track list, so the block stays
                # dormant rather than inviting the driver into an offer the app cannot honour.
                panel.set_focus_report(None)
                return
            items = focus.for_track(self._load_focus(), track)
            panel.set_focus_report(
                self.win.session.focus_report(items, entry, self._load_records(), track))
        except Exception:  # noqa: BLE001 — never let the focus block break a load
            _log.warning("focus list not updated", exc_info=True)

    def focus_add(self, cid: int) -> None:
        """Promote corner `cid` of the loaded recording onto this track's focus list.

        The baseline is measured HERE, now, over this session's clean laps (``Session.focus_items``)
        and stored as a lap-FRACTION window: the corner partition is re-derived per session, so a
        corner id alone would have compared two different stretches of track next time (measured:
        C1's window grew 6.1 m between the two working-set recordings focus.py measures, worth
        +0.062 s of imaginary slowing on a corner the driver took quicker). An untracked session
        cannot hold a list at all — the list is per track."""
        entry = self._current_library_entry() or {}
        track = entry.get("track")
        if not track:
            self._focus_failed("this recording has no detected track, so there is nowhere to keep "
                               "a focus list (File ▸ Save as track… names it)")
            return
        try:
            store = self._load_focus()
            items = focus.for_track(store, track)
            if any(i.cid == int(cid) for i in items) or len(items) >= focus.MAX_ITEMS:
                return
            added = self.win.session.focus_items([int(cid)], entry)
            if not added:
                self._focus_failed(f"C{cid} could not be measured on this session's clean laps")
                return
            focus.save_for_track(track, items + added)
        except OSError as exc:
            self._focus_failed(f"the focus list could not be saved ({exc.strerror or exc})",
                               logging.ERROR)
            return
        except Exception as exc:  # noqa: BLE001 — a promotion must never raise into the UI
            _log.exception("focus list not updated")
            self._focus_failed(f"the focus list could not be updated ({exc!r})", logging.ERROR)
            return
        self.update_focus_list()

    def pre_promote_focus(self, cids: list[int]) -> list[int]:
        """The debrief's explicit default (board review PS-B1): put `cids` — the Coaching
        headline's shortlist, in rank order — on this track's focus list, into its FREE slots only.
        Returns the corners actually added (the debrief says so, and each is one click from gone).

        Free slots only, because an item already on the list carries the baseline of the session it
        was promoted on, and that baseline is what the verdict above the ranking is measured
        against: replacing it with today's would delete the "did it move?" answer on the very
        screen that shows it, and overrule a corner the driver kept or chose. Nothing is promoted
        where the verdict could never speak — no track (nowhere to keep a list), a provisional
        start line or ESTIMATED timing (`focus._blocker` refuses every comparison with such a
        baseline, so an auto-made list would refuse forever)."""
        entry = self._current_library_entry() or {}
        track = entry.get("track")
        if not track or not entry.get("verified") or entry.get("degraded"):
            return []
        try:
            items = focus.for_track(self._load_focus(), track)
            taken = {i.cid for i in items}
            free = focus.MAX_ITEMS - len(items)
            wanted = [int(c) for c in cids if int(c) not in taken][:max(free, 0)]
            added = self.win.session.focus_items(wanted, entry) if wanted else []
            if not added:
                return []
            focus.save_for_track(track, items + added)
        except OSError as exc:
            self._focus_failed(f"the focus list could not be saved ({exc.strerror or exc})",
                               logging.ERROR)
            return []
        except Exception:  # noqa: BLE001 — a default must never raise into the load
            _log.exception("focus list not pre-filled")
            return []
        self.update_focus_list()
        return [i.cid for i in added]

    def focus_remove(self, cid: int) -> None:
        """Drop corner `cid` from this track's focus list (and the row entirely when it empties)."""
        entry = self._current_library_entry() or {}
        track = entry.get("track")
        if not track:
            return
        try:
            items = [i for i in focus.for_track(self._load_focus(), track) if i.cid != int(cid)]
            focus.save_for_track(track, items)
        except OSError as exc:
            self._focus_failed(f"the focus list could not be saved ({exc.strerror or exc})",
                               logging.ERROR)
            return
        except Exception as exc:  # noqa: BLE001
            _log.exception("focus list not updated")
            self._focus_failed(f"the focus list could not be updated ({exc!r})", logging.ERROR)
            return
        self.update_focus_list()

    def _focus_failed(self, why: str, level: int = logging.WARNING) -> None:
        """Say why a focus-list gesture did nothing, on the status bar the app already uses for its
        untimed notices — a button that silently does nothing is the worst of the three outcomes —
        and in the session log. A refusal is a WARNING; a list that could not be WRITTEN is the
        user's own action lost, and its callers pass ERROR."""
        _log.log(level, "focus list — %s.", why)
        bar = self.win.statusBar()
        if bar is not None:
            bar.showMessage(f"Focus list: {why}.", self._status_ms)

    # ------------------------------------------------------------------------ saved tracks (F2)
    def _track_rows(self) -> list[dict]:
        """The saved-tracks row model for ``TrackManagerDialog`` — the merged built-in + user view,
        each row saying whether a rename/delete can actually REACH it.

        ``editable`` is the user's own file holding it, which is a different question from
        ``builtin``: a built-in the user has refined is both, and deleting that one reverts to the
        shipped line instead of removing the circuit. Guarded — an unreadable DB lists nothing
        rather than breaking the dialog."""
        try:
            editable = set(track_db.user_names())
            return [{"name": e["name"],
                     "builtin": track_db.is_builtin(e["name"]),
                     "editable": e["name"] in editable,
                     "sectors": len(e.get("sectors") or [])}
                    for e in track_db.all_tracks()]
        except (OSError, ValueError) as exc:
            _log.warning("saved tracks not readable (%r)", exc)
            return []

    def _open_track_manager(self, parent=None) -> None:
        """Open the saved-tracks manager. `TrackManagerDialog` is imported here, not at module
        scope, exactly as it was while this lived in app.py — so moving the cluster changed no
        module's import surface."""
        from .track_dialog import TrackManagerDialog
        dlg = TrackManagerDialog(
            self._track_rows(), parent=parent if parent is not None else self.win,
            rename_track=self._rename_track, delete_track=self._delete_track,
            restore_tracks=self._restore_tracks, backup_info=track_db.backup_summary,
            reverts_to_builtin=track_db.reverts_to_builtin)
        dlg.exec()

    def _rename_track(self, old: str, new: str) -> list[dict]:
        """Rename a saved circuit EVERYWHERE its name is an identity key, and return the fresh rows.

        A track name is not just a label: the library index files a circuit's personal-best history
        under it, the focus list is keyed by it, and the session record stamps it as provenance. So
        renaming only the track database would split one circuit's history in two the moment the
        next recording auto-detected the new name. This composes the four stores into one gesture,
        exactly as ``_forget_recording`` composes the index, the sidecar, the record and the marks.

        The track DB goes FIRST and its refusals (blank name, name already in use, a built-in, no
        such track) propagate to the dialog, which shows them — nothing else has been written at
        that point. The three satellite stores are then each guarded on their own: a failure to
        re-key one must not leave the rename half-undone, so it is reported and the rest proceed."""
        track_db.rename_track(old, new)
        for label, call in (("library index", lambda: library.rename_track_and_save(old, new)),
                            ("focus list", lambda: focus.rename_track_and_save(old, new)),
                            ("session records",
                             lambda: session_record.rename_track_and_save(old, new))):
            try:
                call()
            except (OSError, ValueError) as exc:
                _log.error("%s not re-keyed to %r (%r)", label, new, exc)
        try:
            # The LIVE session, if it is the renamed circuit. A bare assignment is right here and
            # nowhere else: `adopt_track` exists to record which lines a name vouches for, and a
            # rename changes no lines at all — re-recording them would re-certify whatever is on
            # screen now. It also has to happen BEFORE anything re-writes this recording's library
            # row, or that row would be re-stamped with the old name and undo its own re-key.
            if getattr(getattr(self.win, "session", None), "track_name", None) == old:
                self.win.session.track_name = new
            if getattr(self.win, "view", None) is not None:
                self.win._apply_session_notice()
                self._records_changed()      # the records were re-keyed above, the list with them
        except Exception:  # noqa: BLE001 — a refresh must never undo a completed rename
            _log.warning("surfaces not refreshed after renaming a track", exc_info=True)
        return self._track_rows()

    def _delete_track(self, name: str) -> list[dict]:
        """Delete a saved circuit and return the fresh rows. Refusals (a built-in the user file does
        not hold) propagate to the dialog.

        DELIBERATELY NOT CASCADED. Every analysed session keeps the track name it was driven under,
        so the library's personal-best history, the focus list and the session records are left
        exactly as they are — see ``track_db.remove_track``. Deleting a circuit stops FUTURE
        recordings there detecting it; it is not a retraction of what was already measured, and no
        file beside the user's footage is touched."""
        track_db.remove_track(name)
        return self._track_rows()

    def _restore_tracks(self) -> list[dict]:
        """Put the automatic ``tracks.json.bak`` back and return the fresh rows. ``track_db.restore``
        refuses a missing/unreadable/empty backup by leaving the DB alone, and the dialog only
        offers the button when ``backup_summary`` reports something restorable."""
        try:
            track_db.restore()
        except OSError as exc:
            _log.error("could not restore the saved tracks (%r)", exc)
        return self._track_rows()
