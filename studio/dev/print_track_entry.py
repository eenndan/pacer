"""Print a ready-to-paste `tracks.REGISTRY` entry for a recording's track.

Loads the session exactly as the app does (clean → smooth → detect → segment), so the
start line it prints is the FITTED one: the registry line for an already-known track, or
the auto-fit (`pick_random_start` + widen) for an unknown one. For an unknown track, drag
the start line into place in the app FIRST (it persists to the .pacer.json sidecar and is
restored here), then run this and paste the entry into `studio/tracks.py` REGISTRY — from
then on every session at that track gets the fixed line with no dragging.

Prints the trace bbox + centroid (the detection anchor, matched within
tracks.DETECT_RADIUS_M) and the current start line as absolute lat/lon. If the trace
already matches a registry entry it says so — nothing to paste then. Only ever add entries
for tracks we have real recordings of; the registry is measured data, not guesses.

Run:  pixi run python -m studio.dev.print_track_entry -- /path/to/GX010060.MP4 [--full]
          [--name "Track Name"]
"""

from __future__ import annotations

import argparse

from .. import chapters, sidecar, tracks
from ..session import Session


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="print a ready-to-paste tracks.REGISTRY entry for a recording")
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
        print(f"# NOTE: already in REGISTRY as {known.name!r} — nothing to paste.")
    elif session.track_name is None:
        print("# WARNING: unknown track and the start line below is the AUTO-FIT — drag it")
        print("#          into place in the app before pasting, or the entry pins a guess.")
    print("Track(")
    print(f'    name="{args.name if known is None else known.name}",')
    print(f"    centroid_lat={clat:.4f},")
    print(f"    centroid_lon={clon:.4f},")
    print(f"    start_a=({a_lat:.5f}, {a_lon:.5f}),")
    print(f"    start_b=({b_lat:.5f}, {b_lon:.5f}),")
    print("),")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
