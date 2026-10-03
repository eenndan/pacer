"""p20_packet_spread — what the packet-spread clock costs a fix and a lap, on the cameras it times.

THE QUESTION (qa-w3c EVAL-1). A camera with no GPS9 run, i.e. every GPS5-era GoPro, is timed on
`ingest`'s naive clock: each ~1 s GPMF payload's fixes spread evenly over the payload's media span,
and `load` keeps those times when `_used_gps9_trueclock` finds no GPS9 run. The app tells such a
recording "a lap may read <data_quality.MEDIA_CLOCK_LAP_ERROR> off". That bound read "up to ~0.1 s"
on a simulation that was never in the repo, on the premise that GPS5 was a different clock from the
one the pages describe on GPS9 data (studio/media_clock.py: 28 ms rms, up to 73 ms a fix). This
measures it on the nine bundled GPS5 clips.

THE INDEX MODEL. A receiver delivers its fixes on a fixed-rate grid, so with none dropped the true
time of fix k is t0 + k/r. Fit t0 and r to the naive times by least squares: the residual is how far
the clock put each fix from its true time, with the mean (no average bias) and the rate (the clocks'
~27 ppm) taken out. A lap time is the difference of two instants, so the most the clock can cost a
lap inside a clip is the residual's PEAK TO PEAK: the crossing's chord interpolation only averages
two neighbouring fixes, so it cannot exceed that.

TWO CHECKS OF THE METHOD.
  * GPSU. A GPS5 payload carries one UTC stamp, which pacer copies onto each of its fixes. Each
    payload's media start against its GPSU, de-meaned and de-rated, is that payload's first fix's
    error with no grid assumed, and it must agree with the index model's for the same fixes. Where
    GPSU steps by whole seconds inside a clip (hero8 and both MAX clips), it is not read.
  * --control <a GPS9 chapter .MP4>. GPS9 stamps every fix, so the clock's error there is known
    outright; over the chapter's longest run with no dropped fix the index model must equal it.

MEASURED 2026-10-03. The nine clips: 14-33 ms rms, up to 87 ms a fix, and 68-138 ms peak to peak,
widest on hero7.mp4 (12 s); GPSU agrees within 1.6-16 ms rms on the six it can read. The control,
one GPS9 chapter of the working set (6,973 fixes over 697 s): 27.93 ms rms, 62.2 ms at worst and
119.5 ms peak to peak against the stamps, and the index model differs from that by 0.0 ms. So a GPS5
camera sits at the GPS9 recordings' scale; the 18 Hz stream does not shrink it, because a full GPS5
payload holds 17-20 fixes and where a payload starts still jitters by about one 10 Hz period. The
bound is therefore the pages' "up to ~0.15 s": tests/test_measured_figures.py ties the constant to
them, and tests/test_load_pipeline.py re-measures the nine clips against it on every run.

    pixi run python -m studio.dev.probes.p20_packet_spread
    pixi run python -m studio.dev.probes.p20_packet_spread --control <a GPS9 chapter .MP4>

Reads the bundled samples and, if named, the control chapter, both read only. Writes nothing but
stdout; its app-support seams are jailed, though nothing it calls reaches them.
"""
from __future__ import annotations

import glob
import os
import sys

import numpy as np

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SAMPLES = os.path.join(_REPO, "3rdparty", "gpmf-parser", "samples")


def index_residual(t: np.ndarray) -> tuple[np.ndarray, float]:
    """(each fix's residual off the best-fitting fixed-rate grid, s; that grid's rate, Hz)."""
    k = np.arange(len(t), dtype=float)
    slope, icept = np.polyfit(k, t, 1)
    return t - (icept + slope * k), 1.0 / slope


def _ms(e: np.ndarray) -> tuple[float, float, float]:
    """(rms, worst |e|, peak to peak) in ms."""
    e = np.asarray(e, float) * 1e3
    return float(np.sqrt(np.mean(e ** 2))), float(np.abs(e).max()), float(np.ptp(e))


def _detrend(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """y less its least-squares line in x: the error with its mean and its rate taken out."""
    return y - np.polyval(np.polyfit(x, y, 1), x)


def gps5_clip(path: str) -> dict | None:
    """One bundled clip's packet-spread error, or None for a clip with no GPS stream."""
    from studio import ingest, load

    samples, spans, naive, _ = ingest.read_gpmf([path])
    if len(samples) < 20:
        return None
    t = np.asarray(naive, float)
    e, rate = index_residual(t)
    firsts = np.array([0] + [i for i in range(1, len(spans)) if spans[i] != spans[i - 1]])
    counts = np.diff(np.append(firsts, len(t)))
    row = {"gps9": bool(load._used_gps9_trueclock(samples)), "seconds": float(t[-1] - t[0]),
           "fixes": len(t), "rate": rate, "fix": _ms(e), "gpsu": None, "gpsu_vs_index": None,
           "per_payload": " ".join(f"{n}:{int((counts == n).sum())}" for n in sorted(set(counts)))}
    start = t[firsts]                                  # a payload's first fix sits at its start
    gpsu = np.array([samples[i].timestamp_ms for i in firsts], float) / 1e3
    gpsu -= gpsu[0]
    if np.all(np.abs(np.diff(gpsu) - np.diff(start)) < 0.5):   # GPSU advances with the payloads
        by_gpsu = _detrend(gpsu, start)
        by_index = e[firsts] - e[firsts].mean()
        row["gpsu"] = _ms(by_gpsu)
        row["gpsu_vs_index"] = float(np.sqrt(np.mean((by_gpsu - by_index) ** 2))) * 1e3
    return row


def control(path: str) -> dict:
    """The index model against a GPS9 chapter's own per-fix stamps, over its longest clean run."""
    from studio import ingest, load

    samples, _spans, naive, _ = ingest.read_gpmf([path])
    if not load._used_gps9_trueclock(samples):
        raise SystemExit(f"--control needs a GPS9 chapter; {os.path.basename(path)} has no GPS9 run")
    t = np.asarray(naive, float)
    stamp = np.array([s.timestamp_ms for s in samples], float) / 1e3
    stamp -= stamp[0]
    step = np.diff(stamp)
    good = np.abs(step - np.median(step)) < 0.1 * np.median(step)    # no dropped or doubled fix
    runs, begin = [], 0
    for i, ok in enumerate(np.append(good, False)):
        if not ok:
            runs.append((begin, i))
            begin = i + 1
    i0, i1 = max(runs, key=lambda r: r[1] - r[0])
    sl = slice(i0, i1 + 1)
    truth = _detrend(stamp[sl], t[sl])
    e, rate = index_residual(t[sl])
    return {"fixes": len(t), "run": i1 + 1 - i0, "seconds": float(t[sl][-1] - t[sl][0]),
            "rate": rate, "truth": _ms(truth), "index": _ms(e),
            "index_vs_truth": float(np.sqrt(np.mean((e - truth) ** 2))) * 1e3}


def main(argv: list[str]) -> int:
    from studio.dev._jail import divert_app_support

    divert_app_support("pacer-p20-")
    from studio import data_quality

    gps9 = None
    if argv:
        if argv[0] != "--control" or len(argv) != 2:
            raise SystemExit("usage: python -m studio.dev.probes.p20_packet_spread "
                             "[--control <a GPS9 chapter .MP4>]")
        gps9 = argv[1]
    print(f"{'clip':<18}{'s':>6}{'fixes':>7}{'Hz':>7}  {'fixes per payload':<20}"
          f"{'rms':>6}{'worst':>7}{'p-to-p':>8}   {'GPSU p-to-p':>11}{'GPSU vs index':>15}   (ms)")
    widest = ("", 0.0)
    for path in sorted(glob.glob(os.path.join(SAMPLES, "*.mp4"))):
        name, row = os.path.basename(path), gps5_clip(path)
        if row is None:
            print(f"{name:<18}  no GPS stream")
            continue
        if row["gps9"]:
            print(f"{name:<18}  has a GPS9 run: timed on its stamps, not this clock")
            continue
        rms, worst, p2p = row["fix"]
        gpsu = (f"{row['gpsu'][2]:11.1f}{row['gpsu_vs_index']:15.1f}" if row["gpsu"]
                else f"{'(GPSU steps)':>11}{'—':>15}")
        print(f"{name:<18}{row['seconds']:6.1f}{row['fixes']:7d}{row['rate']:7.2f}  "
              f"{row['per_payload']:<20}{rms:6.1f}{worst:7.1f}{p2p:8.1f}   {gpsu}")
        widest = max(widest, (name, p2p), key=lambda w: w[1])
    bound = data_quality.MEDIA_CLOCK_LAP_ERROR
    print(f"\nwidest: {widest[1]:.1f} ms peak to peak on {widest[0]}; the app's bound for these "
          f"cameras is {bound!r}")
    if gps9:
        c = control(gps9)
        print(f"\ncontrol {os.path.basename(gps9)}: {c['run']} of {c['fixes']} fixes over "
              f"{c['seconds']:.1f} s at {c['rate']:.4f} Hz, no dropped fix")
        for what, (rms, worst, p2p) in (("against its GPS9 stamps", c["truth"]),
                                        ("by the index model", c["index"])):
            print(f"  {what:<24}{rms:7.2f} ms rms {worst:7.1f} ms at worst {p2p:7.1f} ms peak to peak")
        print(f"  index model vs stamps    {c['index_vs_truth']:7.3f} ms rms")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
