"""Build the stored Daytona Milton Keynes reference centerline (studio/mk_centerline.json).

The reference is the FALLBACK gap-fill source (used only where no measured lap covers a
section). The stored polyline is the BEST CLEAN LAP loop of a real session, resampled and
normalized to the unit square — i.e. the actual driven racing line, which is exactly the
shape the fill needs (gap mouths are pinned by a similarity transform, so only the corner
SHAPE matters). `studio/reference.py` aligns it to any session's best-lap loop at load
(cyclic arc-length correspondence + similarity transform, reflection allowed).

Regenerate from a session: python -m studio.dev.build_reference SESSION.MP4 [more chapters]
Validate only (no write):  python -m studio.dev.build_reference --validate SESSION.MP4 [...]
Legacy hand-trace build:   python -m studio.dev.build_reference --hand-trace

Each build also prints the fit RMS + footprint coverage against the source session (and
`--validate` against any other session) — regenerate from one session and `--validate`
against another for an honest cross-session check.

History: the original polyline was digitized BY EYE from a thumbnail of the track image
`~/Desktop/Tracks/MK/gmaps_pict.png` (no embedded geo-coordinates). That trace
turned out to be a poor rendition of the real layout — even a globally-correct similarity
fit (the cyclic-correspondence fit in reference.py) could only reach RMS ≈ 21 m / 42 %
footprint coverage against real sessions — so the shipped JSON was rebuilt from measured
GPS. The waypoints are kept below (`_WAYPOINTS`, `--hand-trace`) for provenance only.
"""

from __future__ import annotations

import json
import os

import numpy as np

# LEGACY (known-poor, see module docstring): ordered (x_norm, y_norm) waypoints traced by
# eye off gmaps_pict.png (origin top-left, y down) as a closed loop. Kept for provenance.
_WAYPOINTS = [
    # start near top-centre, go clockwise around the long outer loop first
    (0.50, 0.07), (0.58, 0.08), (0.64, 0.10), (0.68, 0.14), (0.70, 0.20),
    (0.71, 0.28), (0.72, 0.37), (0.73, 0.46), (0.74, 0.55), (0.75, 0.63),
    (0.76, 0.71), (0.76, 0.78), (0.74, 0.84), (0.70, 0.88), (0.64, 0.91),
    (0.57, 0.92), (0.49, 0.92), (0.41, 0.91), (0.35, 0.88), (0.31, 0.83),
    (0.30, 0.77),  # bottom-left of outer loop, turn back up into the infield
    (0.33, 0.72), (0.39, 0.69), (0.45, 0.68), (0.51, 0.69),  # lower infield switchback right
    (0.56, 0.66), (0.58, 0.61), (0.55, 0.57), (0.49, 0.56), (0.43, 0.57),  # mid switchback left
    (0.39, 0.53), (0.41, 0.48), (0.47, 0.46), (0.53, 0.47),  # upper-mid switchback right
    (0.56, 0.43), (0.54, 0.39), (0.48, 0.38), (0.42, 0.39),  # switchback left
    (0.37, 0.36), (0.34, 0.31), (0.30, 0.27),  # head out to the left side
    (0.25, 0.24), (0.21, 0.27), (0.20, 0.33), (0.22, 0.39),  # left-side small loop bottom
    (0.26, 0.42), (0.24, 0.36), (0.21, 0.30),  # back of the top-left hairpin
    (0.23, 0.23), (0.28, 0.18), (0.34, 0.14), (0.40, 0.11), (0.46, 0.08),  # along the top back to start
]

# Stored resolution for the from-session build: the best lap is ~900 points at 10 Hz over a
# ~1.3 km loop; 512 uniform arc-length samples keep ~2.5 m spacing (well under the corner
# scale) at a third of the size.
_N_STORE = 512


def _write(pts, source: str, out_path: str | None = None) -> str:
    """Normalize `pts` to the unit square and write the canonical JSON."""
    pts = np.asarray(pts, float)
    mn, mx = pts.min(0), pts.max(0)
    span = (mx - mn)
    span[span == 0] = 1.0
    norm = (pts - mn) / span
    # Canonical reference lives in studio/ (studio/reference.py loads it); this script now
    # lives in studio/dev/, so write one directory up.
    out_path = out_path or os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "mk_centerline.json")
    with open(out_path, "w") as fh:
        json.dump({"track": "Daytona Milton Keynes",
                   "source": source,
                   "points_norm": norm.tolist()}, fh, indent=1)
    print(f"wrote {len(norm)} waypoints -> {out_path}", flush=True)
    return out_path


def _best_clean_loop(paths: list[str]):
    """Load a session and return its best clean-lap loop (local metres) — the same lap
    selection reference fitting uses (fastest valid lap without a GPS dropout)."""
    from studio.session import Session  # deferred: pacer bindings only needed here

    sess = Session.load(list(paths))
    return sess._reference_fit_loop()


def build_from_session(paths: list[str], out_path: str | None = None,
                       validate_against: list[str] | None = None) -> str:
    """Build the stored centerline from a real session's best clean lap (the canonical
    build). The loop is resampled uniformly by arc length and normalized; no absolute
    geo-coordinates are stored. Prints the (by-construction near-perfect) self-fit as a
    plumbing check; pass `validate_against` (another session's chapters) for the honest
    cross-session number."""
    from studio import reference

    loop = _best_clean_loop(paths)
    if loop is None:
        raise SystemExit(f"no valid clean lap loop in {paths} — cannot build")
    out = _write(reference._resample_closed(loop, _N_STORE),
                 f"best clean lap of {'+'.join(os.path.basename(p) for p in paths)} "
                 f"(measured GPS, resampled x{_N_STORE}, normalized)", out_path)
    _fit_and_report(loop, f"self vs {os.path.basename(paths[0])}")
    if validate_against:
        _validate(validate_against, label="cross")
    return out


def build(out_path: str | None = None, validate_against: list[str] | None = None) -> str:
    """LEGACY hand-trace build (known-poor; see module docstring). Kept for provenance."""
    out = _write(_WAYPOINTS,
                 "gmaps_pict.png (traced centerline, normalized image coords, y-down)",
                 out_path)
    if validate_against:
        _validate(validate_against)
    return out


def _fit_and_report(loop, label: str):
    """Fit the stored reference to `loop` and print the winning RMS + footprint coverage."""
    from studio import reference

    fitted, info = reference.fit_loop_to_loop(reference._load_normalized(), loop)
    print(f"{label}: RMS {info['rms']:.2f} m, "
          f"{info['coverage']:.1%} of best-lap points within "
          f"{reference.COVERAGE_TOL_M:.0f} m "
          f"(scale {info['scale']:.1f}, reversed={info['reversed']}, "
          f"{len(fitted)} pts)", flush=True)


def _validate(paths: list[str], label: str = "validate"):
    """Fit the stored reference against a real session's best-lap loop and print the
    winning RMS + footprint coverage. HEAVY (loads the session, so it needs the pacer
    bindings and the chapter MP4s) — dev validation only, never on the app path."""
    loop = _best_clean_loop(paths)
    if loop is None:
        print(f"{label}: no valid lap loop in the session — cannot fit", flush=True)
        return
    _fit_and_report(loop, f"{label} vs {os.path.basename(paths[0])}")


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    if args and args[0] == "--hand-trace":
        build(validate_against=args[1:] or None)
    elif args and args[0] == "--validate":
        _validate(args[1:])
    elif args:
        build_from_session(args)
    else:
        raise SystemExit(__doc__.split("\n\n")[2])  # the usage block
