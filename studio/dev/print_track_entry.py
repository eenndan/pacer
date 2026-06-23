"""Print a recording's track detection anchor + start line (a debugging aid).

The app now persists named tracks in the track DATABASE (studio/track_db.py) via File ▸
Save as track…, so the normal flow is: drag the start line into place in the app, then Save
as track — no editing of source. This tool just *reports* what detection sees: load the
session exactly as the app does (clean → smooth → detect → segment), print the trace bbox +
centroid (the detection anchor, matched within tracks.DETECT_RADIUS_M) and the current start
line as absolute lat/lon (the FITTED line for a known track, or the auto-fit for an unknown
one — restored from the .pacer.json sidecar if one exists).

If the trace already matches a known track it says so. Only ever save tracks you have real
recordings of; the database is measured data, not guesses.

Run:  pixi run python -m studio.dev.print_track_entry -- /path/to/GX010060.MP4 [--full]
          [--name "Track Name"]
"""

from __future__ import annotations

import argparse

from .. import chapters, sidecar, tracks
from ..session import Session


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="print a recording's track detection anchor + start line")
    ap.add_argument("path", help="any chapter of the recording (.MP4)")
    ap.add_argument("--full", action="store_true",
                    help="chain all sibling chapters (like the app's --full)")
    ap.add_argument("--name", default="<TRACK NAME>",
                    help="track name to put in the entry")
    args = ap.parse_args(argv)

    paths = chapters.discover_siblings(args.path) if args.full else [args.path]
    session = Session.load(paths)
    if session.point_count() == 0:
        print("no GPS points — cannot derive a track entry")
        return 1

    # The same centroid Session.load anchors detection on: the trace bbox centre
    # (min_max returns lon/lat Points: x=lon, y=lat).
    mn, mx = session.laps.min_max()
    clat, clon = (mn.y + mx.y) / 2, (mn.x + mx.x) / 2
    known = tracks.detect_track(clat, clon)

    # The CURRENT start line in absolute lat/lon — the sidecar's own export, so what you
    # see here is exactly what a saved sidecar would restore. If a sidecar exists for this
    # recording it was applied during load, so a hand-tuned line is reflected automatically.
    start, _sectors = session.timing_lines_latlon()
    (a_lat, a_lon), (b_lat, b_lon) = start

    print(f"# recording: {chapters.recording_label(paths)} "
          f"({session.point_count()} pts, {len(session.valid_lap_ids())} valid laps)")
    print(f"# trace bbox: lat [{mn.y:.5f}, {mx.y:.5f}]  lon [{mn.x:.5f}, {mx.x:.5f}]")
    print(f"# sidecar:    {sidecar.sidecar_path(paths[0])}")
    if known is not None:
        print(f"# NOTE: already a known track ({known.name!r}) — it auto-detects already.")
    elif session.track_name is None:
        print("# WARNING: unknown track and the start line below is the AUTO-FIT — drag it")
        print("#          into place in the app and use File ▸ Save as track… instead.")
    print(f"# name:     {args.name if known is None else known.name}")
    print(f"# centroid: ({clat:.4f}, {clon:.4f})")
    print(f"# start:    ({a_lat:.5f}, {a_lon:.5f}) -- ({b_lat:.5f}, {b_lon:.5f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
