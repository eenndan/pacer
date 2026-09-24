"""Video sync against GROUND TRUTH: what Pacer paints over a frame vs where the kart truly was (X2).

The synthetic GoPro (`studio/dev/synth_gopro.py`) knows where the kart was at every instant, plants
the GPS lag a real HERO13 has (fixes filed late against the picture), and runs its picture — the
IMU and the video frames — on the media clock. So "does the overlay show THIS frame's moment?" has
an exact answer here, which a real screen cannot give: the owner checked on 2026-09-23 that the
speed and the map line up with the video, and an eye does not resolve ~0.1 s.

Everything goes through the real path: `Session.load` measures and installs the lag, and each frame
is read the way the live view and the burned-in export both read it — `Session.telemetry_time` of
the frame's media time, then `Session.index_at_time` -> (tx, ty) for the map dot and tv for the
speed readout; `export_video.overlay_values_at` is asserted to pick the same sample. The error is
TIME along the track: how far ahead of the picture the map dot is (+ = ahead), found by projecting
the painted point onto the circuit's true centreline and dividing by the true speed.

MEASURED (fixed seed, every 3rd frame while moving; one frame at 29.97 fps is 33.4 ms). "Before" is
this test with both of X2's defects planted back (lag 0.2 s case; the harness stops at the first
failure), and it reproduces what B2 measured on the published demo, +125 ms (p10 +79, p90 +171):

                                before X2                       after X2 (all three cases)
    map dot, median             +122.5 ms (p10 +76, p90 +171)   +17.7 .. +19.0 ms
      the lookup's share        +49.7 ms                        -0.8 .. -0.9 ms (the NEAREST sample)
      the clock's share         +75.8 ms                        +20.8 .. +22.9 ms (a centred reference)
    speed readout (regression)  +115.5 ms                       +17.5 .. +18.8 ms

What is left is the load-time position boxcar's (`studio/rotation.py` module doc): inside one frame,
stated and not corrected. The Δ and the chart cursor interpolate at the same telemetry instant, so
they carry the clock's share only. Each defect planted alone fails here by name: the old ceiling
lookup at +50.7 ms, the old off-centre lag reference at +75.8 ms.

Run: python tests/test_sync_truth.py   (~15 s; needs the pixi env's ffmpeg for the video trak)
"""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import pacer  # noqa: E402
from studio import chapters  # noqa: E402
from studio import export_video as ev  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.session import Session  # noqa: E402

FRAME_S = sg.PAYLOAD_S / sg.FRAMES_PER_PAYLOAD   # one frame of the synthetic's 29.97 fps video
LOOKUP_MAX_S = 0.005    # the lookup's share: measured ~0 (the old ceiling: +50 ms)
MOVING_MPS = 5.0
# (planted GPS lag s, GPS noise, chapters): the planted range, the clean and noisier ends of it, and
# a chapter seam.
CASES = ((0.2, 1.0, 1), (0.46, 0.0, 2), (0.6, 2.0, 1))
_LOADED: dict = {}


def _load(lag, noise, chapters_n):
    key = (lag, noise, chapters_n)
    if key not in _LOADED:
        tmp = tempfile.TemporaryDirectory(prefix="sync_truth_test_")
        rec = sg.generate(os.path.join(tmp.name, "rec"), chapters=chapters_n, gps_noise=noise,
                          gps_lag_s=lag)
        _LOADED[key] = (tmp, rec, Session.load(chapters.discover_siblings(rec.paths[0])))
    return _LOADED[key][1:]


def _to_truth_frame(s, truth, xs, ys):
    """Session-local metres -> the truth's own local metres, through lat/lon."""
    lat, lon = np.empty(len(xs)), np.empty(len(xs))
    for k, (x, y) in enumerate(zip(xs, ys, strict=True)):
        g = s.cs.global_(pacer.Vec3f(float(x), float(y), 0.0))
        lat[k], lon[k] = g.lat, g.lon
    return sg.to_local(lat, lon, truth.origin)


def _along_track(circuit, px, py, s_true):
    """Signed distance (m) along the centreline from the true position (lap distance `s_true`) to
    the foot of the painted point (px, py); + = the painted point is further round the lap."""
    n, L = len(circuit.s) - 1, circuit.length
    k0 = np.rint(np.mod(s_true, L) / sg.DS).astype(int)
    idx = (k0[:, None] + np.arange(-200, 201)[None, :]) % n        # +-50 m around the truth
    d2 = (circuit.x[idx] - px[:, None]) ** 2 + (circuit.y[idx] - py[:, None]) ** 2
    j = idx[np.arange(len(px)), np.argmin(d2, axis=1)]
    h = circuit.heading[j]
    s_app = circuit.s[j] + (px - circuit.x[j]) * np.cos(h) + (py - circuit.y[j]) * np.sin(h)
    return (s_app - np.mod(s_true, L) + L / 2) % L - L / 2


def measure(rec, s, step=3):
    """Per-frame offsets (s, + = the overlay is AHEAD of the picture) over the moving frames:
    {"dot": the painted map dot, "clock": the same instant interpolated (so dot - clock is the
    lookup's share), "speed": the speed readout's offset by regression on the true acceleration,
    "frames": media times probed}."""
    truth = rec.truth
    ppm = 1.0 + truth.media_ppm * 1e-6
    frames = np.arange(int(truth.t_end * ppm / FRAME_S)) * FRAME_S
    frames = frames[::step]
    tau = frames / ppm                                    # the instant each frame is a picture of
    s_true = truth.s_start + np.interp(tau, truth.t_nodes, truth.d_nodes)
    v_true = np.interp(tau, truth.t_nodes, truth.v_nodes)
    t_tel = np.array([s.telemetry_time(float(m)) for m in frames])
    keep = (v_true > MOVING_MPS) & (t_tel > s.tt[0]) & (t_tel < s.tt[-1])
    frames, tau, s_true, v_true, t_tel = (a[keep] for a in (frames, tau, s_true, v_true, t_tel))
    idx = np.array([s.index_at_time(float(t)) for t in t_tel])
    out = {"frames": frames}
    for name, (px, py) in (("dot", (s.tx[idx], s.ty[idx])),
                           ("clock", (np.interp(t_tel, s.tt, s.tx), np.interp(t_tel, s.tt, s.ty)))):
        qx, qy = _to_truth_frame(s, truth, px, py)
        out[name] = _along_track(truth.circuit, qx, qy, s_true) / v_true
    a_true = np.interp(tau, truth.t_nodes, np.gradient(truth.v_nodes, truth.t_nodes))
    err = s.tv[idx] / 3.6 - v_true
    strong = np.abs(a_true) > 2.0
    out["speed"] = float(np.sum(err[strong] * a_true[strong]) / np.sum(a_true[strong] ** 2))
    out["idx"] = idx
    out["t_tel"] = t_tel
    return out


def _ms(x):
    return f"{1000 * float(x):+.1f} ms"


def test_the_map_dot_shows_the_frame_it_is_drawn_over():
    """The PASS criterion: across the planted lag range, the map dot's median offset from where the
    kart truly is in that frame is inside one video frame — and so is each of its two shares."""
    for case in CASES:
        rec, s = _load(*case)
        m = measure(rec, s)
        dot, clock = float(np.median(m["dot"])), float(np.median(m["clock"]))
        lookup = float(np.median(m["dot"] - m["clock"]))
        print(f"  lag {case[0]:.2f} s, noise {case[1]}, {case[2]} chapter(s): map dot {_ms(dot)} "
              f"(p10 {_ms(np.percentile(m['dot'], 10))}, p90 {_ms(np.percentile(m['dot'], 90))}); "
              f"lookup {_ms(lookup)}, clock {_ms(clock)}, speed readout {_ms(m['speed'])}; "
              f"installed lag {s.media_clock.gps_lag:+.4f} s over {len(m['frames'])} frames")
        assert abs(lookup) < LOOKUP_MAX_S, (
            f"{case}: the lookup puts the dot {_ms(lookup)} off the frame's instant — the sample it "
            f"shows is not the nearest one (a ceiling reads +50 ms at 10 Hz)")
        assert abs(clock) < FRAME_S, (
            f"{case}: the installed GPS lag puts the overlay {_ms(clock)} off the picture "
            f"(installed {s.media_clock.gps_lag:+.4f} s for a planted {case[0]} s)")
        assert abs(dot) < FRAME_S, f"{case}: the map dot sits {_ms(dot)} off the picture"
        assert abs(m["speed"]) < FRAME_S, f"{case}: the speed readout is {_ms(m['speed'])} off"


def test_the_export_reads_the_sample_the_live_view_reads():
    """The burned-in overlay resolves each frame through `overlay_values_at`; it must land on the
    same sample, with the same speed, as the live readout's `index_at_time` — so the offsets above
    are the export's too."""
    rec, s = _load(*CASES[1])
    m = measure(rec, s, step=97)
    for frame, i in zip(m["frames"], m["idx"], strict=True):
        vals = ev.overlay_values_at(s, float(frame))
        assert vals.marker_index == i, (frame, vals.marker_index, i)
        assert vals.speed_kmh == float(s.tv[i]), (frame, vals.speed_kmh, s.tv[i])
    print(f"  {len(m['frames'])} frames: the export's sample and speed == the live view's")


def _run_all():
    for fn in (test_the_map_dot_shows_the_frame_it_is_drawn_over,
               test_the_export_reads_the_sample_the_live_view_reads):
        t0 = time.time()
        fn()
        print(f"ok {fn.__name__} ({time.time() - t0:.1f} s)")


if __name__ == "__main__":
    _run_all()
