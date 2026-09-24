"""A synthetic GoPro recording with KNOWN ground truth: a HERO13-shaped chaptered .MP4 of a fictional
kart circuit, written from nothing but numpy, the standard library and the pixi env's ffmpeg.

WHY IT EXISTS. CI never ran Pacer's REAL loader on a session with laps: the one bundled real clip
(`hero6.mp4`) segments into 0 valid laps, and the golden gate's synthetic `Session` is SEEDED — no
GPMF, no `pacer.Laps` crossing interpolation, no band, 2-4 laps against lap-count gates of 5-8. And
a reader who clones the repo has nothing to open. The owner has not consented to publishing his own
footage, so both needs are met here with data that describes no person, no kart and no real place:
the circuit is invented and sits in the North Atlantic (`ORIGIN`), where no built-in track can match.

WHAT THE FILE CARRIES, each piece shaped the way the reader needs it (`GPMF_mp4reader.c`,
`pacer/gps-source/gps-source.cpp`, `studio/ingest.py`), laid out like the bundled
`3rdparty/gpmf-parser/samples/hero8.mp4`:
  * `ftyp` first (the reader refuses anything else), `mdat`, then `moov`, as a GoPro writes them;
  * a video trak (ffmpeg, H.264, 29.97 fps: exactly 30 frames per telemetry payload, so each chapter's
    video and GPMF tracks end together as GoPro's contract says they do);
  * a `GoPro MET` trak: handler `meta`, sample entry `gpmd`, timescale 1000, one 1.001 s payload per
    sample and one sample per chunk, each payload one `DEVC` holding DVNM "HERO13 Black" and the streams
    the loader reads — ACCL and GYRO (200 per payload, raw element order ZXY, ORIN with no ORIO, as a
    HERO13 writes them), GRAV and CORI (60 per payload, element order XZY) and GPS9 at 10 Hz.

WHAT IS KNOWN EXACTLY, and therefore testable (`Truth`): the circuit's centreline, where the kart was
at every instant (it drives the centreline; only its speed varies), and hence the lap time between
consecutive crossings of ANY line — including the one the app's unknown-track heuristic places, which
is the only honest reference for comparing its lap times. The IMU is the same trajectory seen from a
camera on a fixed, slightly misaligned mount: specific force, body rate, gravity and orientation are
one rigid-body motion, not four independent signals. The GPS carries the defects a real receiver has,
at the clean end of the range: white and slowly-drifting position noise scaled by DOP, an acquisition
period without a 3D fix, two lone teleport glitches for `load._clean` to remove, and the two clock
facts this repo measured on the owner's HERO13 recordings — GPS9 fixes filed ~0.46 s late against the
picture (`GPS_LAG_S`) and a media clock running 27 ppm fast (`MEDIA_PPM`) — plus CORI's gyro-integrated
world yaw drifting through each chapter (`CORI_DRIFT_DEG_S`). The noise amplitude is deliberately the
CLEAN end: the lap-time residual this fixture yields measures the pipeline, not a receiver's floor
(docs/ACCURACY.md has that: σ 0.053-0.087 s on D24). `gps_noise` scales it up.

Run (the output directory must not exist yet, or be empty — nothing is ever overwritten):
    pixi run python -m studio.dev.synth_gopro --out <new-dir> [--seed N] [--laps 14] [--chapters 2]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

import numpy as np

# ------------------------------------------------------------------------------ the fixed world
ORIGIN = (47.0, -32.0)          # deg: open ocean, ~1,100 km from any shore — matches no real venue
START_UTC = dt.datetime(2026, 6, 1, 10, 0, 0, tzinfo=dt.UTC)
DEVICE_NAME = "HERO13 Black"
RECORDING_NUMBER = 9001         # -> GX019001.MP4, GX029001.MP4
DEFAULT_SEED = 20260924
G = 9.80665

# The circuit: straights ("S", metres) and arcs ("C", turn degrees + = left, radius m), clockwise.
# The two straights named in CLOSE_ON are re-solved so the loop closes; the curvature is then
# smoothed over SMOOTH_M so every corner has clothoid-like entries (a kart cannot step its yaw rate).
LAYOUT = (("S", 230.0), ("C", -100.0, 15.0), ("S", 60.0), ("C", -60.0, 24.0), ("S", 150.0),
          ("C", 50.0, 22.0), ("S", 60.0), ("C", -90.0, 18.0), ("S", 120.0), ("C", -130.0, 12.0),
          ("S", 80.0), ("C", 70.0, 26.0), ("S", 70.0), ("C", -100.0, 24.0))
CLOSE_ON = (0, 2)               # the main straight and the one after C1 absorb the closure
SMOOTH_M = 10.0
DS = 0.25                       # m: the simulation's distance grid
PIT_STRAIGHT = 4                # (in Circuit.straights) the kart leaves from, and parks in, its middle

# The driver: grip, braking and engine, and how consistent each corner is (sigma of the grip factor).
LAT_G, BRAKE_G, ACCEL0, V_TOP = 1.6, 1.15, 4.4, 27.8
CORNER_SIGMA = (0.015, 0.02, 0.03, 0.02, 0.05, 0.025, 0.02)
CORNER_BIAS = (0.0, 0.0, -0.01, 0.0, -0.03, 0.0, 0.0)   # C5, the hairpin, is where the time is
SLOW_LAP = 5                    # 0-based timed lap with a half-spin at C5 and a lift after it
T_LEAD, T_TAIL = 25.0, 15.0     # s stationary before moving / after stopping

# The clocks and the sensors.
PAYLOAD_S = 1.001               # one GPMF payload (1001 ticks at timescale 1000)
FRAMES_PER_PAYLOAD = 30         # 29.97 fps
GPS_LAG_S = 0.46                # GPS9 fixes are filed this late against the picture (#291)
MEDIA_PPM = 27.0                # the media clock runs this fast against GPS time (media_clock.py)
CORI_DRIFT_DEG_S = 0.10         # CORI's world yaw drift (gmeter.py measured 0.08-0.15 deg/s)
ACCL_N, GRAV_N = 200, 60        # samples per payload
ACCL_SCAL, GYRO_SCAL, UNIT_SCAL = 417, 939, 32767
MOUNT_DEG = (7.0, 1.5, 4.0)     # camera pitch down, roll, yaw off the kart's axis
GPS9_SCAL = (10_000_000, 10_000_000, 1000, 1000, 100, 1, 1000, 100, 1)


# ------------------------------------------------------------------------------ the circuit
@dataclass(frozen=True)
class CornerTruth:
    cid: int            # 1-based, track order from the start of the main straight
    turn_deg: float     # + = left
    radius: float
    apex_s: float       # m along the lap


@dataclass(frozen=True)
class Circuit:
    s: np.ndarray       # (n+1,) m, s[-1] == length
    x: np.ndarray       # east, m (closed: x[-1] == x[0])
    y: np.ndarray       # north, m
    heading: np.ndarray  # rad, unwrapped
    kappa: np.ndarray   # 1/m, + = left
    corners: tuple[CornerTruth, ...]
    straights: tuple[tuple[float, float], ...]  # (start, end) m, in LAYOUT order; [0] = main

    @property
    def length(self) -> float:
        return float(self.s[-1])

    def at(self, s):
        """(x, y, heading, kappa) at lap distance `s` (wrapped)."""
        s = np.mod(np.asarray(s, float), self.length)
        return (np.interp(s, self.s, self.x), np.interp(s, self.s, self.y),
                np.interp(s, self.s, self.heading), np.interp(s, self.s, self.kappa))


def _kappa(layout) -> np.ndarray:
    parts = []
    for seg in layout:
        if seg[0] == "S":
            parts.append(np.zeros(int(round(seg[1] / DS))))
        else:
            turn = math.radians(seg[1])
            n = int(round(abs(turn) * seg[2] / DS))
            parts.append(np.full(n, turn / (n * DS)))   # integrates to exactly `turn`
    k = np.concatenate(parts)
    w = int(round(SMOOTH_M / DS)) | 1                   # circular boxcar: keeps every corner's turn
    ext = np.concatenate([k[-w:], k, k[:w]])
    return np.convolve(ext, np.ones(w) / w, mode="same")[w:-w]


def _integrate(k):
    th = np.concatenate([[0.0], np.cumsum(k * DS)])
    mid = th[:-1] + 0.5 * k * DS
    x = np.concatenate([[0.0], np.cumsum(np.cos(mid) * DS)])
    y = np.concatenate([[0.0], np.cumsum(np.sin(mid) * DS)])
    return th, x, y


def build_circuit(mirror: bool = False) -> Circuit:
    """The fixed circuit, closed by re-solving the CLOSE_ON straights (a straight's length enters the
    end-point gap linearly, along its own heading), then sheared by the last few centimetres of
    rounding so the loop meets itself exactly. `mirror` flips it anticlockwise."""
    layout = [tuple(seg) for seg in LAYOUT]
    if mirror:
        layout = [seg if seg[0] == "S" else ("C", -seg[1], seg[2]) for seg in layout]
    for _ in range(4):
        th, x, y = _integrate(_kappa(layout))
        heads = []
        for i in CLOSE_ON:
            turn = sum(math.radians(seg[1]) for seg in layout[:i] if seg[0] == "C")
            heads.append((math.cos(turn), math.sin(turn)))
        d = np.linalg.solve(np.array(heads).T, -np.array([x[-1], y[-1]]))
        for i, di in zip(CLOSE_ON, d, strict=True):
            layout[i] = ("S", layout[i][1] + float(di))
    k = _kappa(layout)
    th, x, y = _integrate(k)
    s = np.arange(len(x)) * DS
    x = x - x[-1] * s / s[-1]
    y = y - y[-1] * s / s[-1]
    corners, straights, pos = [], [], 0.0
    for seg in layout:
        if seg[0] == "S":
            straights.append((pos, pos + int(round(seg[1] / DS)) * DS))
            pos = straights[-1][1]
        else:
            arc = int(round(abs(math.radians(seg[1])) * seg[2] / DS)) * DS
            corners.append(CornerTruth(cid=len(corners) + 1, turn_deg=seg[1], radius=seg[2],
                                       apex_s=pos + arc / 2))
            pos += arc
    return Circuit(s=s, x=x, y=y, heading=th, kappa=np.append(k, k[0]), corners=tuple(corners),
                   straights=tuple(straights))


# ------------------------------------------------------------------------------ geodesy
_A, _E2 = 6378137.0, 6.69437999014e-3                   # WGS84


def _radii(lat0: float) -> tuple[float, float]:
    s2 = math.sin(math.radians(lat0)) ** 2
    m = _A * (1 - _E2) / (1 - _E2 * s2) ** 1.5          # meridian
    n = _A / math.sqrt(1 - _E2 * s2)                     # prime vertical
    return m, n * math.cos(math.radians(lat0))


def to_latlon(x, y, origin=ORIGIN):
    m, p = _radii(origin[0])
    return origin[0] + np.degrees(np.asarray(y) / m), origin[1] + np.degrees(np.asarray(x) / p)


def to_local(lat, lon, origin=ORIGIN):
    m, p = _radii(origin[0])
    return (np.radians(np.asarray(lon) - origin[1]) * p, np.radians(np.asarray(lat) - origin[0]) * m)


# ------------------------------------------------------------------------------ the session
@dataclass
class Truth:
    """Everything the recording was generated from. Times are TRUE (GPS) seconds from the start of
    the recording; a lap time is a difference of them, which is what the app's GPS9 axis measures."""

    circuit: Circuit
    seed: int
    laps: int                   # timed laps the session was built to contain
    slow_lap: int               # 0-based index among them
    s_start: float              # lap distance the kart starts (and ends) from
    d_nodes: np.ndarray         # distance driven, m, on the DS grid
    t_nodes: np.ndarray         # true time at each node, s
    v_nodes: np.ndarray         # m/s
    t_end: float                # the recording's true length, s
    gps_lag_s: float
    media_ppm: float
    chapter_payloads: list[int] = field(default_factory=list)

    def crossings(self, line_latlon) -> np.ndarray:
        """True times the kart crossed the segment [[lat, lon], [lat, lon]] (e.g. the app's own
        start line, `Session.timing_lines_latlon()[0]`), in order."""
        (la1, lo1), (la2, lo2) = line_latlon
        (x1, x2), (y1, y2) = to_local([la1, la2], [lo1, lo2])
        c = self.circuit
        px, py = c.x[:-1], c.y[:-1]
        qx, qy = c.x[1:], c.y[1:]
        rx, ry, sx, sy = qx - px, qy - py, x2 - x1, y2 - y1
        den = rx * sy - ry * sx
        with np.errstate(divide="ignore", invalid="ignore"):
            t = ((x1 - px) * sy - (y1 - py) * sx) / den
            u = ((x1 - px) * ry - (y1 - py) * rx) / den
        hit = np.flatnonzero((den != 0) & (t >= 0) & (t < 1) & (u >= 0) & (u <= 1))
        out = []
        for i in hit:
            s_hit = c.s[i] + t[i] * DS
            first = math.ceil((self.d_nodes[0] + self.s_start - s_hit) / c.length)
            for k in range(first, first + 10_000):
                d = s_hit + k * c.length - self.s_start
                if d > self.d_nodes[-1]:
                    break
                if d >= self.d_nodes[0]:
                    out.append(float(np.interp(d, self.d_nodes, self.t_nodes)))
        return np.sort(np.array(out))

    def lap_times(self, line_latlon) -> np.ndarray:
        return np.diff(self.crossings(line_latlon))

    def to_json(self) -> dict:
        c = self.circuit
        return {"seed": self.seed, "laps": self.laps, "slow_lap": self.slow_lap,
                "circuit_length_m": c.length, "origin": ORIGIN, "start_utc": START_UTC.isoformat(),
                "corners": [vars(k) for k in c.corners], "gps_lag_s": self.gps_lag_s,
                "media_ppm": self.media_ppm, "chapter_payloads": self.chapter_payloads,
                "duration_s": self.t_end}


def _driver(rng, circuit: Circuit, n_laps: int):
    """Per-lap grip and braking factors per corner, and each lap-grid point's nearest corner."""
    apex = np.array([k.apex_s for k in circuit.corners])
    grid = circuit.s[:-1]
    gap = np.abs(grid[:, None] - apex[None, :])
    nearest = np.argmin(np.minimum(gap, circuit.length - gap), axis=1)
    nc = len(apex)
    form = 1.0 + rng.normal(0.0, 0.004, n_laps)
    form[1:3] -= (0.03, 0.015)                          # the first two flying laps: cold tyres
    grip = (1.0 + np.array(CORNER_BIAS)[None, :nc]
            + rng.normal(0.0, 1.0, (n_laps, nc)) * np.array(CORNER_SIGMA)[None, :nc])
    grip = grip * form[:, None]
    brake = np.clip(rng.normal(1.0, 0.06, (n_laps, nc)), 0.85, 1.1)
    return nearest, grip, brake


def simulate(seed: int = DEFAULT_SEED, laps: int = 14, mirror: bool = False) -> Truth:
    """The kart's motion: out of the pits, `laps` timed laps (one of them slow), an in-lap, a stop.

    Speed comes from the classic two-pass limit on a distance grid — the corner limit
    sqrt(grip / |kappa|), an engine that fades to V_TOP, braking at ~1.15 g — per lap, with each
    lap's grip varying per corner. Distance and time are exact under constant acceleration between
    nodes, so the time at any distance (and back) is known to microseconds."""
    rng = np.random.default_rng(seed)
    c = build_circuit(mirror)
    length = c.length
    s_start = sum(c.straights[PIT_STRAIGHT]) / 2.0
    # Where the flying laps begin and end: late on the main straight, just before the braking for
    # C1 — about where the app's unknown-track heuristic puts its line (the peak-speed point).
    line_s = c.straights[0][1] - 40.0
    # Lap j counts lap distance from s=0 (the start of the main straight). The kart leaves the pit
    # in lap 0 and parks there again in lap `laps + 1`, so it passes line_s in laps 1..laps+1: that
    # is `laps` flying laps between an out-lap and an in-lap.
    d_end = (laps + 1) * length
    n = int(round(d_end / DS)) + 1
    d = np.arange(n) * DS
    s_abs = s_start + d
    lap = np.floor(s_abs / length).astype(int)
    s_lap = s_abs - lap * length
    idx = np.minimum((s_lap / DS).astype(int), len(c.s) - 2)
    kappa = np.abs(c.kappa[idx])
    nearest, grip, brake = _driver(rng, c, laps + 2)
    corner = nearest[idx]
    g_lat = LAT_G * G * grip[lap, corner]
    a_brk = BRAKE_G * G * brake[lap, corner]
    v_top = np.full(n, V_TOP)
    # The out-lap and in-lap are driven gently; the stop comes off the backward pass (v=0 at the end).
    gentle = (lap == 0) | ((lap == 1) & (s_lap < line_s)) | ((lap == laps + 1) & (s_lap > line_s))
    pace = np.where(gentle, 0.75, 1.0)
    g_lat = g_lat * pace
    v_top = v_top * np.where(pace < 1, 0.8, 1.0)
    a_brk = np.where(pace < 1, np.minimum(a_brk, 0.5 * G), a_brk)
    # THE SLOW LAP: a half-spin at C5 (grip to a fifth, as if the kart went round on the exit) and a
    # yellow-flag lift down the next straight.
    slow = SLOW_LAP + 1
    c5 = next(k for k in c.corners if k.cid == 5)
    spin = (lap == slow) & (np.abs(s_lap - c5.apex_s) < 10.0)
    g_lat = np.where(spin, g_lat * 0.2, g_lat)
    lift = (lap == slow) & (s_lap > c5.apex_s) & (s_lap < c5.apex_s + 150.0)
    v_top = np.where(lift, 0.6 * V_TOP, v_top)
    with np.errstate(divide="ignore"):
        vlim = np.minimum(np.sqrt(g_lat / np.maximum(kappa, 1e-9)), v_top)
    v = np.empty(n)
    v[0] = 0.0
    for i in range(n - 1):
        acc = ACCEL0 * max(0.0, 1.0 - (v[i] / v_top[i]) ** 2)
        v[i + 1] = min(vlim[i + 1], math.sqrt(v[i] * v[i] + 2.0 * acc * DS))
    v[-1] = 0.0
    for i in range(n - 2, -1, -1):
        v[i] = min(v[i], math.sqrt(v[i + 1] * v[i + 1] + 2.0 * a_brk[i] * DS))
    t = T_LEAD + np.concatenate([[0.0], np.cumsum(2.0 * DS / (v[:-1] + v[1:]))])
    return Truth(circuit=c, seed=seed, laps=laps, slow_lap=SLOW_LAP, s_start=s_start, d_nodes=d,
                 t_nodes=t, v_nodes=v, t_end=float(t[-1] + T_TAIL), gps_lag_s=GPS_LAG_S,
                 media_ppm=MEDIA_PPM)


def _kinematics(truth: Truth, tau):
    """(lap distance, speed, longitudinal accel) at true times `tau` — exact, constant-acceleration."""
    tn, dn, vn = truth.t_nodes, truth.d_nodes, truth.v_nodes
    tau = np.asarray(tau, float)
    i = np.clip(np.searchsorted(tn, tau, side="right") - 1, 0, len(tn) - 2)
    acc = (vn[i + 1] ** 2 - vn[i] ** 2) / (2.0 * DS)
    h = np.clip(tau - tn[i], 0.0, None)
    v = np.maximum(vn[i] + acc * h, 0.0)
    d = dn[i] + vn[i] * h + 0.5 * acc * h * h
    before, after = tau < tn[0], tau >= tn[-1]
    d = np.where(before, dn[0], np.where(after, dn[-1], d))
    v = np.where(before | after, 0.0, v)
    acc = np.where(before | after, 0.0, acc)
    return truth.s_start + d, v, acc


# ------------------------------------------------------------------------------ the sensors
def _rot(axis: int, deg: float) -> np.ndarray:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    r = np.eye(3)
    i, j = [(1, 2), (2, 0), (0, 1)][axis]
    r[i, i], r[i, j], r[j, i], r[j, j] = c, -s, s, c
    return r


# body (forward, left, up) -> camera (X right, Y forward, Z up), then the mount's small misalignment
_NOMINAL = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
MOUNT = _rot(0, -MOUNT_DEG[0]) @ _rot(1, MOUNT_DEG[1]) @ _rot(2, MOUNT_DEG[2]) @ _NOMINAL
ACCL_ORDER = (2, 0, 1)          # raw element order Z, X, Y (a HERO13's ORIN "ZXY")
GRAV_ORDER = (0, 2, 1)          # X, Z, Y: gmeter.GRAV_PERM (1, 0, 2) maps it onto ACCL's order
_P = np.eye(3)[list(GRAV_ORDER)]


def _world_to_body(theta):
    c, s = np.cos(theta), np.sin(theta)
    z, o = np.zeros_like(theta), np.ones_like(theta)
    return np.stack([np.stack([c, s, z], -1), np.stack([-s, c, z], -1), np.stack([z, z, o], -1)], -2)


def _quat(m):
    """Unit quaternions (w, x, y, z) of rotation matrices (N,3,3) — the inverse of the matrix
    `gmeter._quat_rotate_world` builds — kept in one continuous hemisphere, because the loader
    interpolates CORI component by component. Shepperd's branch on the largest of the four squared
    components, since a kart that has turned half a lap is a 180-degree rotation, where w -> 0."""
    m00, m11, m22 = m[:, 0, 0], m[:, 1, 1], m[:, 2, 2]
    sq = np.column_stack([1 + m00 + m11 + m22, 1 + m00 - m11 - m22,
                          1 - m00 + m11 - m22, 1 - m00 - m11 + m22]) / 4.0
    big = np.argmax(sq, axis=1)
    r = 4.0 * np.sqrt(np.maximum(sq[np.arange(len(m)), big], 1e-12))   # 4 * the largest component
    d21, d02, d10 = m[:, 2, 1] - m[:, 1, 2], m[:, 0, 2] - m[:, 2, 0], m[:, 1, 0] - m[:, 0, 1]
    s01, s02, s12 = m[:, 0, 1] + m[:, 1, 0], m[:, 0, 2] + m[:, 2, 0], m[:, 1, 2] + m[:, 2, 1]
    cand = np.stack([np.column_stack([r * r / 4, d21, d02, d10]),
                     np.column_stack([d21, r * r / 4, s01, s02]),
                     np.column_stack([d02, s01, r * r / 4, s12]),
                     np.column_stack([d10, s02, s12, r * r / 4])])
    q = cand[big, np.arange(len(m))] / r[:, None]
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    flip = np.cumprod(np.where(np.sum(q[1:] * q[:-1], axis=1) < 0, -1.0, 1.0))
    q[1:] *= flip[:, None]
    return q


def _imu(truth: Truth, media, chapter_start_media: float):
    """ACCL, GYRO (raw ZXY order), GRAV (XZY), CORI (XZY, world = the chapter's first frame) at the
    given media times: one rigid-body motion seen by a camera on MOUNT."""
    tau = media / (1.0 + truth.media_ppm * 1e-6)
    s_abs, v, a_long = _kinematics(truth, tau)
    _, _, heading, kappa = truth.circuit.at(s_abs)
    f_body = np.column_stack([a_long, v * v * kappa, np.full_like(v, G)])
    w_body = np.column_stack([np.zeros_like(v), np.zeros_like(v), v * kappa])
    f_cam = f_body @ MOUNT.T
    w_cam = w_body @ MOUNT.T
    up_cam = MOUNT @ np.array([0.0, 0.0, 1.0])
    tau0 = chapter_start_media / (1.0 + truth.media_ppm * 1e-6)
    drift = np.radians(CORI_DRIFT_DEG_S) * (tau - tau0)
    s0, _, _ = _kinematics(truth, np.array([tau0]))
    h0 = truth.circuit.at(s0)[2]
    m = (_P @ MOUNT) @ _world_to_body(heading + drift) @ _world_to_body(h0).transpose(0, 2, 1)
    m = m @ (MOUNT.T @ _P.T)
    return f_cam, w_cam, up_cam, _quat(m)


# ------------------------------------------------------------------------------ GPMF
def _klv(key: bytes, typ: bytes, ssize: int, rep: int, data: bytes = b"") -> bytes:
    return key + struct.pack(">cBH", typ, ssize, rep) + data + b"\0" * (-len(data) % 4)


def _nest(key: bytes, *items: bytes) -> bytes:
    body = b"".join(items)
    return key + struct.pack(">cBH", b"\0", 1, len(body)) + body


def _text(key: bytes, s: bytes) -> bytes:
    return _klv(key, b"c", 1, len(s), s)


def _i16(a, scale) -> bytes:
    return np.clip(np.round(np.asarray(a) * scale), -32768, 32767).astype(">i2").tobytes()


def _gps_rows(truth: Truth, rng, gps_noise: float):
    """Every GPS9 fix of the session: (media filing time, raw int row), 10 Hz on the UTC grid."""
    phase = 0.037                                       # the receiver's epochs are not the camera's
    tk = phase + 0.1 * np.arange(int((truth.t_end - phase) / 0.1) + 1)
    s_abs, v, _ = _kinematics(truth, tk)
    x, y, _, _ = truth.circuit.at(s_abs)
    n = len(tk)
    rho = math.exp(-0.1 / 90.0)

    def ou(sigma):
        e = rng.normal(0.0, sigma * math.sqrt(1 - rho * rho), n)
        out = np.empty(n)
        out[0] = rng.normal(0.0, sigma)
        for i in range(1, n):
            out[i] = rho * out[i - 1] + e[i]
        return out

    dop = np.clip(1.1 + ou(0.12), 0.8, 2.5)
    fix = np.full(n, 3)
    fix[tk < 8.0] = 2
    fix[tk < 4.0] = 0
    dop = np.where(tk < 20.0, dop + np.clip(20.0 - tk, 0, None) * 0.6, dop)
    k = gps_noise * dop / 1.1
    ex = (ou(0.35) + rng.normal(0.0, 0.30, n)) * k
    ey = (ou(0.35) + rng.normal(0.0, 0.30, n)) * k
    moving = np.flatnonzero(v > 5.0)
    for frac, jump in ((0.35, (61.0, -44.0)), (0.71, (-38.0, 57.0))):   # lone teleport glitches
        i = moving[int(frac * len(moving))]
        ex[i] += jump[0]
        ey[i] += jump[1]
    lat, lon = to_latlon(x + ex, y + ey)
    alt = 31.0 + ou(1.2) + rng.normal(0.0, 0.4, n)
    v2 = np.maximum(v + rng.normal(0.0, 0.06, n), 0.0)
    v3 = np.maximum(v + rng.normal(0.0, 0.08, n), 0.0)
    # Fix k is stamped START_UTC + 0.1 k: a receiver's epochs sit on the UTC 100 ms grid, and the
    # camera started recording `phase` before one of them.
    ms0 = round((START_UTC - dt.datetime(2000, 1, 1, tzinfo=dt.UTC)).total_seconds() * 1000.0)
    days, msod = np.divmod(ms0 + 100 * np.arange(n, dtype=np.int64), 86_400_000)
    raw = np.column_stack([np.round(lat * 1e7), np.round(lon * 1e7), np.round(alt * 1000),
                           np.round(v2 * 1000), np.round(v3 * 100), days, msod])
    media = tk * (1.0 + truth.media_ppm * 1e-6) + truth.gps_lag_s
    rows = [struct.pack(">7i2H", *map(int, r), int(round(d * 100)), int(f))
            for r, d, f in zip(raw, dop, fix, strict=True)]
    return media, rows


def _payloads(truth: Truth, rng, n_payloads: int, first: int, gps, accl_noise: float):
    """The GPMF payloads of one chapter (global payload indices [first, first + n_payloads))."""
    g_media, g_rows = gps
    start_media = first * PAYLOAD_S
    out, tsmp = [], {"ACCL": 0, "GYRO": 0, "GRAV": 0, "CORI": 0, "GPS9": 0}
    fast = (np.arange(ACCL_N) / ACCL_N)[None, :]
    slow = (np.arange(GRAV_N) / GRAV_N)[None, :]
    spans = (first + np.arange(n_payloads))[:, None] * PAYLOAD_S
    m_fast = (spans + fast * PAYLOAD_S).ravel()
    m_slow = (spans + slow * PAYLOAD_S).ravel()
    f_cam, w_cam, _, _ = _imu(truth, m_fast, start_media)
    _, _, up_cam, q = _imu(truth, m_slow, start_media)
    accl = f_cam[:, ACCL_ORDER] + rng.normal(0.0, accl_noise, f_cam.shape)
    gyro = w_cam[:, ACCL_ORDER] + 0.0008 + rng.normal(0.0, 0.03, w_cam.shape)
    grav = np.tile(up_cam[list(GRAV_ORDER)], (len(m_slow), 1)) + rng.normal(0.0, 0.001, (len(m_slow), 3))
    cori = q + rng.normal(0.0, 0.0003, q.shape)
    for j in range(n_payloads):
        stmp = struct.pack(">Q", int(round(j * PAYLOAD_S * 1e6)))
        a = accl[j * ACCL_N:(j + 1) * ACCL_N]
        gy = gyro[j * ACCL_N:(j + 1) * ACCL_N]
        gr = grav[j * GRAV_N:(j + 1) * GRAV_N]
        co = cori[j * GRAV_N:(j + 1) * GRAV_N]
        lo, hi = np.searchsorted(g_media, [(first + j) * PAYLOAD_S, (first + j + 1) * PAYLOAD_S])
        streams = []
        for key, name, unit, scal, data in (
                (b"ACCL", b"Accelerometer", b"m/s\xb2", ACCL_SCAL, a),
                (b"GYRO", b"Gyroscope", b"rad/s", GYRO_SCAL, gy)):
            tsmp[key.decode()] += len(data)
            streams.append(_nest(b"STRM", _klv(b"STMP", b"J", 8, 1, stmp),
                                 _klv(b"TSMP", b"L", 4, 1, struct.pack(">I", tsmp[key.decode()])),
                                 _text(b"STNM", name), _klv(b"ORIN", b"c", 3, 1, b"ZXY"),
                                 _klv(b"SIUN", b"c", len(unit), 1, unit),
                                 _klv(b"SCAL", b"s", 2, 1, struct.pack(">h", scal)),
                                 _klv(b"TMPC", b"f", 4, 1, struct.pack(">f", 41.5)),
                                 _klv(key, b"s", 6, len(data), _i16(data, scal))))
        if hi > lo:
            tsmp["GPS9"] += hi - lo
            units = b"".join(u.ljust(3, b"\0") for u in (b"deg", b"deg", b"m", b"m/s", b"m/s", b"",
                                                         b"s", b"", b""))
            streams.append(_nest(b"STRM", _klv(b"STMP", b"J", 8, 1, stmp),
                                 _klv(b"TSMP", b"L", 4, 1, struct.pack(">I", tsmp["GPS9"])),
                                 _text(b"STNM", b"GPS (Lat., Long., Alt., 2D, 3D, days, secs, DOP, fix)"),
                                 _klv(b"UNIT", b"c", 3, 9, units), _text(b"TYPE", b"lllllllSS"),
                                 _klv(b"SCAL", b"l", 4, 9, struct.pack(">9i", *GPS9_SCAL)),
                                 _klv(b"GPSA", b"F", 4, 1, b"MSLV"),
                                 _klv(b"GPS9", b"?", 32, hi - lo, b"".join(g_rows[lo:hi]))))
        for key, name, size, data in ((b"CORI", b"CameraOrientation", 8, co),
                                      (b"GRAV", b"Gravity Vector", 6, gr)):
            tsmp[key.decode()] += len(data)
            streams.append(_nest(b"STRM", _klv(b"STMP", b"J", 8, 1, stmp),
                                 _klv(b"TSMP", b"L", 4, 1, struct.pack(">I", tsmp[key.decode()])),
                                 _text(b"STNM", name), _klv(b"SCAL", b"s", 2, 1, struct.pack(">h", UNIT_SCAL)),
                                 _klv(key, b"s", size, len(data), _i16(data, UNIT_SCAL))))
        out.append(_nest(b"DEVC", _klv(b"DVID", b"L", 4, 1, struct.pack(">I", 1)),
                         _text(b"DVNM", DEVICE_NAME.encode()), *streams))
    return out


# ------------------------------------------------------------------------------ MP4
def _box(typ: bytes, *parts: bytes) -> bytes:
    body = b"".join(parts)
    return struct.pack(">I", 8 + len(body)) + typ + body


def _full(typ: bytes, flags: int, *parts: bytes) -> bytes:
    return _box(typ, struct.pack(">I", flags), *parts)


def _children(buf: bytes, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        size, typ = struct.unpack(">I4s", buf[pos:pos + 8])
        yield typ, pos, size
        pos += size


def _video_trak(path: str, delta: int) -> tuple[bytes, bytes, int]:
    """(the video trak with its chunk offsets moved by `delta`, the mdat body, the movie timescale)
    of an ffmpeg-written MP4."""
    with open(path, "rb") as f:
        buf = f.read()
    top = {typ: (pos, size) for typ, pos, size in _children(buf, 0, len(buf))}
    mpos, msize = top[b"mdat"]
    opos, osize = top[b"moov"]
    body_start = mpos + 8
    trak = mvhd = None
    for typ, pos, size in _children(buf, opos + 8, opos + osize):
        if typ == b"trak":
            trak = bytearray(buf[pos:pos + size])
        elif typ == b"mvhd":
            mvhd = buf[pos:pos + size]

    def patch(start, end):
        for typ, pos, size in _children(trak, start, end):
            if typ in (b"mdia", b"minf", b"stbl"):
                patch(pos + 8, pos + size)
            elif typ in (b"stco", b"co64"):
                w, fmt = (4, ">I") if typ == b"stco" else (8, ">Q")
                (count,) = struct.unpack(">I", trak[pos + 12:pos + 16])
                for k in range(count):
                    o = pos + 16 + k * w
                    (old,) = struct.unpack(fmt, trak[o:o + w])
                    trak[o:o + w] = struct.pack(fmt, old - body_start + delta)

    patch(8, len(trak))
    return bytes(trak), buf[body_start:mpos + msize], struct.unpack(">I", mvhd[20:24])[0]


def _encode_video(out_path: str, frames: int, ffmpeg: str) -> None:
    subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=0x1d2330:s=320x180:r=30000/1001", "-frames:v", str(frames),
                    "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
                    "-pix_fmt", "yuv420p", "-x264-params", "keyint=300:min-keyint=300:scenecut=0",
                    "-video_track_timescale", "30000", "-an", "-n", out_path],
                   check=True, capture_output=True)


def _write_mp4(path: str, payloads: list[bytes], video_mp4: str) -> None:
    ftyp = _box(b"ftyp", b"mp41", struct.pack(">I", 0x20131018), b"mp41")
    trak_v, vdata, movie_ts = _video_trak(video_mp4, len(ftyp) + 8)
    meta = b"".join(payloads)
    mdat = struct.pack(">I", 8 + len(vdata) + len(meta)) + b"mdat" + vdata + meta
    offsets, off = [], len(ftyp) + 8 + len(vdata)
    for p in payloads:
        offsets.append(off)
        off += len(p)
    n = len(payloads)
    dur_meta = n * 1001                                   # timescale 1000
    dur_movie = n * 1001 * movie_ts // 1000
    stamp = int((START_UTC - dt.datetime(1904, 1, 1, tzinfo=dt.UTC)).total_seconds())
    matrix = struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
    tkhd = _full(b"tkhd", 0x0F, struct.pack(">IIIII", stamp, stamp, 2, 0, dur_movie), b"\0" * 8,
                 struct.pack(">hhhh", 0, 0, 0, 0), matrix, struct.pack(">II", 0, 0))
    mdhd = _full(b"mdhd", 0, struct.pack(">IIIIHH", stamp, stamp, 1000, dur_meta, 0x55C4, 0))
    hdlr = _full(b"hdlr", 0, b"mhlr", b"meta", b"\0" * 12, b"\x0bGoPro MET  ")
    gmhd = _box(b"gmhd", _full(b"gmin", 0, struct.pack(">HHHHhH", 0x40, 0x8000, 0x8000, 0x8000, 0, 0)),
                _box(b"gpmd", b"\0" * 4))
    dinf = _box(b"dinf", _full(b"dref", 0, struct.pack(">I", 1), _full(b"alis", 1)))
    stbl = _box(b"stbl",
                _full(b"stsd", 0, struct.pack(">I", 1),
                      _box(b"gpmd", b"\0" * 6, struct.pack(">H", 1), b"\0" * 4)),
                _full(b"stts", 0, struct.pack(">III", 1, n, 1001)),
                _full(b"stsc", 0, struct.pack(">IIII", 1, 1, 1, 1)),
                _full(b"stsz", 0, struct.pack(">II", 0, n), struct.pack(f">{n}I", *map(len, payloads))),
                _full(b"stco", 0, struct.pack(">I", n), struct.pack(f">{n}I", *offsets)))
    trak_m = _box(b"trak", tkhd, _box(b"mdia", mdhd, hdlr, _box(b"minf", gmhd, dinf, stbl)))
    mvhd = _full(b"mvhd", 0, struct.pack(">IIII", stamp, stamp, movie_ts, dur_movie),
                 struct.pack(">IH", 0x10000, 0x100), b"\0" * 10, matrix, b"\0" * 24, struct.pack(">I", 3))
    with open(path, "xb") as f:                           # never overwrite anything
        f.write(ftyp + mdat + _box(b"moov", mvhd, trak_v, trak_m))


# ------------------------------------------------------------------------------ entry points
@dataclass
class Recording:
    paths: list[str]
    truth: Truth


def generate(out_dir: str, seed: int = DEFAULT_SEED, laps: int = 14, chapters: int = 2,
             gps_noise: float = 1.0, mirror: bool = False, ffmpeg: str | None = None) -> Recording:
    """Write the synthetic recording into `out_dir` (created; must not already hold files) and return
    its chapter paths and ground truth. Deterministic: the same arguments give the same telemetry."""
    # PATH first (a `pixi run` puts the env's bin there), then the running interpreter's own env.
    ffmpeg = (ffmpeg or os.environ.get("PACER_FFMPEG") or shutil.which("ffmpeg")
              or shutil.which("ffmpeg", path=os.path.join(sys.prefix, "bin")))
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found (the pixi env provides it)")
    os.makedirs(out_dir, exist_ok=True)
    if os.listdir(out_dir):
        raise FileExistsError(f"{out_dir} is not empty; synth_gopro never writes over anything")
    truth = simulate(seed, laps, mirror)
    rng = np.random.default_rng([seed, 1])
    total = math.ceil(truth.t_end * (1.0 + truth.media_ppm * 1e-6) / PAYLOAD_S)
    gps = _gps_rows(truth, rng, gps_noise)
    bounds = np.linspace(0, total, chapters + 1).round().astype(int)
    truth.chapter_payloads = [int(b - a) for a, b in zip(bounds[:-1], bounds[1:], strict=True)]
    paths = []
    with tempfile.TemporaryDirectory(prefix="synth_gopro_") as tmp:
        for ch, (a, b) in enumerate(zip(bounds[:-1], bounds[1:], strict=True), start=1):
            video = os.path.join(tmp, f"video{ch}.mp4")
            _encode_video(video, int(b - a) * FRAMES_PER_PAYLOAD, ffmpeg)
            payloads = _payloads(truth, rng, int(b - a), int(a), gps, accl_noise=1.0)
            path = os.path.join(out_dir, f"GX{ch:02d}{RECORDING_NUMBER:04d}.MP4")
            _write_mp4(path, payloads, video)
            paths.append(path)
    return Recording(paths=paths, truth=truth)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="NEW (or empty) directory to write into")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--laps", type=int, default=14)
    ap.add_argument("--chapters", type=int, default=2)
    ap.add_argument("--gps-noise", type=float, default=1.0)
    ap.add_argument("--mirror", action="store_true", help="drive the circuit anticlockwise")
    args = ap.parse_args(argv)
    rec = generate(args.out, args.seed, args.laps, args.chapters, args.gps_noise, args.mirror)
    with open(os.path.join(args.out, "truth.json"), "x") as f:
        json.dump(rec.truth.to_json(), f, indent=1)
    for p in rec.paths:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
