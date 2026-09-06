"""Tests for the SEAM-CROSSING branch of the overlay export's video-source resolution.

A lap whose window spans a GoPro chapter boundary decodes through the concat demuxer instead of a
single file. That branch has one job the single-file branch gets for free: **the first frame out
must be the frame at the requested global t0**, because `frame_times` stamps every burned-in
overlay from t0 exactly. Miss it and the numbers describe a different moment than the picture they
sit on — for the whole clip, silently, in a file that cannot be re-rendered.

It missed it. The span used to trim its first chapter with a concat `inpoint` at the lap start and
seek `-ss 0`; `inpoint` is KEYFRAME-GRANULAR, so the picture began at the keyframe at or before t0
— up to one GOP early. On the real 3-chapter D24 recording that was -0.956 s on lap 22 and
-0.193 s on lap 47, at 0 s padding and at 10 s alike. The span now declares each file's `duration`
(which is what makes a concat input seekable at all) and takes `time_offset = chapters[i0].offset`,
so ffmpeg's ordinary accurate `-ss` lands on the same frame the single-chapter branch lands on.

So this file measures the branch against REAL ffmpeg on two synthetic chapters, rather than
asserting the shape of an argv:

  1. the resolved SHAPE (durations declared, no inpoint, the offset and seek that follow from it),
     including the map that cannot state durations and the window that ends exactly on the seam;
  2. the PICTURE: the concat's first frame is byte-identical to the single-file accurate seek at
     the same instant, and the frame at the seam is byte-identical to the next chapter's first —
     with the old `inpoint` list decoded alongside as a control, so the test cannot pass vacuously;
  3. the AUDIO, measured independently of the picture by cross-correlating the decoded PCM against
     the chapter's own audio (deterministic `anoisesrc`, so the correlation peak is unambiguous —
     a tone would have been periodic and told us nothing).

The chapters are built at 96x64 / 30 fps / 4 s with a keyframe every 30 frames, so the GOP the bug
hid in is 1.000 s wide and the whole file runs in a couple of seconds.

Run: python tests/test_export_seam.py
"""
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from studio import chapters  # noqa: E402
from studio import export_video as ev  # noqa: E402

W, H, FPS = 96, 64, 30.0
GOP = 30                       # frames between keyframes -> a 1.000 s GOP, like the GoPro footage
FRAME_BYTES = W * H * 3
SR = 48000


def _in_pixi_env() -> bool:
    """True when running inside the project's pixi environment (CONDA_PREFIX under .pixi/envs)."""
    return os.path.join(".pixi", "envs") in os.environ.get("CONDA_PREFIX", "")


def _require_ffmpeg(label: str) -> bool:
    """Same gate as test_export_video.py: inside the pixi env ffmpeg is a LOCKED dependency, so its
    absence must FAIL rather than silently disable this regression test; outside it, skip."""
    if ev.ffmpeg_available():
        return True
    assert not _in_pixi_env(), (
        f"{label}: ffmpeg/ffprobe not found on PATH inside the pixi env, where they are a LOCKED "
        "dependency (pyproject.toml) — this no-media regression test must run in CI, not skip.")
    print(f"skip {label} (no ffmpeg; not in the pixi env)")
    return False


# ============================================================ the resolved shape (no ffmpeg needed)
def _map3(d=1000.0):
    """A synthetic 3-chapter map: ch0 [0,d), ch1 [d,2d), ch2 [2d,3d)."""
    return chapters.ChapterMap(["/v/GX010001.MP4", "/v/GX020001.MP4", "/v/GX030001.MP4"],
                               [d, d, d])


def test_the_span_seeks_with_the_same_local_time_a_single_chapter_would():
    """The property the fix IS: `local` means one thing on both branches. Take the SAME t0 (980, in
    chapter 0 of a 3x1000 s map) and vary only where the window ends. Ending at 999 it resolves to
    chapter 0 alone and seeks it at local 980. Ending at 1040 it spans into chapter 1 and seeks the
    SPAN at local 980 — the same number addressing the same instant, because the span's clock
    starts where its first chapter starts. It used to seek 0, with the span itself moved to begin
    at the lap."""
    cm = _map3(1000.0)
    inside = ev.resolve_video_source(cm, 980.0, 999.0)
    spanning = ev.resolve_video_source(cm, 980.0, 1040.0)
    try:
        assert inside.concat_list_path is None and inside.time_offset == 0.0
        assert spanning.concat_list_path is not None
        assert spanning.time_offset == 0.0, "the span's clock starts at the FIRST spanned chapter"
        a = ev.ExportSpec(out_path="/o.mp4", lap_id=1, t0=980.0, t1=999.0, source=inside)
        b = ev.ExportSpec(out_path="/o.mp4", lap_id=1, t0=980.0, t1=1040.0, source=spanning)
        assert a.local_t0 == 980.0 and b.local_t0 == 980.0, (a.local_t0, b.local_t0)
        # and in a later chapter the shift is that chapter's offset on both branches too
        late_in = ev.resolve_video_source(cm, 1980.0, 1999.0)
        late_span = ev.resolve_video_source(cm, 1980.0, 2040.0)
        try:
            assert late_in.time_offset == 1000.0 and late_span.time_offset == 1000.0
            assert late_span.concat_list_path is not None
            c = ev.ExportSpec(out_path="/o.mp4", lap_id=1, t0=1980.0, t1=2040.0, source=late_span)
            assert c.local_t0 == 980.0, c.local_t0
        finally:
            late_in.cleanup()
            late_span.cleanup()
    finally:
        inside.cleanup()
        spanning.cleanup()
    print("test_the_span_seeks_with_the_same_local_time_a_single_chapter_would OK "
          "(local_t0 == 980.0 on both branches, in chapter 0 and in chapter 1)")


def test_a_window_ending_exactly_on_a_seam_still_stays_one_chapter():
    """The half-open edge case, kept: a lap that ENDS at offset_{k+1} played entirely inside
    chapter k and must not drag the next file into a concat span. Checked on BOTH seams of a
    3-chapter map, and one millisecond past each seam must span — so the boundary is pinned from
    both sides rather than only from the side that used to be wrong."""
    cm = _map3(1000.0)
    for seam, first in ((1000.0, "/v/GX010001.MP4"), (2000.0, "/v/GX020001.MP4")):
        ends_on = ev.resolve_video_source(cm, seam - 60.0, seam)
        just_past = ev.resolve_video_source(cm, seam - 60.0, seam + 1e-3)
        try:
            assert ends_on.concat_list_path is None, f"window ending at {seam} must stay in one file"
            assert ends_on.probe_path == first and ends_on.time_offset == seam - 1000.0
            assert just_past.concat_list_path is not None, (
                f"a window running 1 ms past {seam} does span the seam")
        finally:
            ends_on.cleanup()
            just_past.cleanup()
    print("test_a_window_ending_exactly_on_a_seam_still_stays_one_chapter OK (2 seams, both sides)")


# ================================================================== the picture, through real ffmpeg
def _make_chapter(path: str, seconds: float, seed: int, hue: int) -> None:
    """One synthetic 'chapter': a moving test pattern at a fixed HUE (so the two chapters are
    visibly different) plus deterministic noise audio (so a cross-correlation has a sharp peak).
    `-g/-keyint_min GOP -sc_threshold 0` pins the keyframe grid, which is the granularity the old
    `inpoint` seek was stuck on."""
    if os.path.exists(path):
        os.remove(path)
    subprocess.run(
        [ev.FFMPEG, "-nostdin", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=size={W}x{H}:rate={FPS:g}:duration={seconds:g}",
         "-f", "lavfi", "-i", f"anoisesrc=color=white:seed={seed}:r={SR}:d={seconds:g}",
         "-vf", f"hue=h={hue}",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p",
         "-g", str(GOP), "-keyint_min", str(GOP), "-sc_threshold", "0",
         "-c:a", "aac", "-ar", str(SR), "-ac", "1", "-shortest", path],
        check=True, capture_output=True)
    assert os.path.getsize(path) > 0


def _probe_duration(path: str) -> float:
    return float(subprocess.run(
        [ev.FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        check=True, capture_output=True, text=True).stdout.strip())


def _decode(argv: list[str], n: int) -> list[np.ndarray]:
    raw = subprocess.run(argv, check=True, capture_output=True).stdout
    out = []
    for i in range(n):
        chunk = raw[i * FRAME_BYTES:(i + 1) * FRAME_BYTES]
        if len(chunk) < FRAME_BYTES:
            break
        out.append(np.frombuffer(chunk, dtype=np.uint8).reshape(H, W, 3))
    return out


def _decode_at(seek: float, input_args: list[str], dur: float, n: int,
               resample: bool = True) -> list[np.ndarray]:
    """`n` frames from `seek`, through the same scale (+ optional fps) chain build_decode_cmd uses."""
    vf = f"scale={W}:{H},fps={FPS:.6f}" if resample else f"scale={W}:{H}"
    return _decode([ev.FFMPEG, "-nostdin", "-loglevel", "error",
                    "-ss", f"{seek:.6f}", *input_args, "-t", f"{dur:.6f}",
                    "-vf", vf, "-pix_fmt", "rgb24", "-f", "rawvideo",
                    "-an", "-sn", "-dn", "pipe:1"], n)


def _audio_at(seek: float, input_args: list[str], dur: float) -> np.ndarray:
    raw = subprocess.run(
        [ev.FFMPEG, "-nostdin", "-loglevel", "error",
         "-ss", f"{seek:.6f}", *input_args, "-t", f"{dur:.6f}",
         "-vn", "-sn", "-dn", "-f", "s16le", "-ac", "1", "-ar", str(SR), "pipe:1"],
        check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float64)


def _lag_samples(probe: np.ndarray, reference: np.ndarray) -> tuple[int, float]:
    """Where `probe` sits inside `reference`, by FFT cross-correlation. Returns (lag, peak/median)
    — the ratio is the evidence that the peak means something."""
    n = 1
    while n < len(reference) + len(probe):
        n <<= 1
    cc = np.fft.irfft(np.fft.rfft(reference - reference.mean(), n)
                      * np.fft.rfft((probe - probe.mean())[::-1], n), n)
    cc = cc[:len(reference) + len(probe) - 1]
    k = int(np.argmax(cc)) - (len(probe) - 1)
    med = float(np.median(np.abs(cc)))
    return k, (float(cc.max()) / med if med else float("inf"))


def test_a_seam_crossing_window_decodes_the_frame_it_asked_for():
    """THE REGRESSION, on real ffmpeg. Two 4 s chapters with a 1.000 s GOP; a window from 3.500 s
    (mid-GOP, deliberately) to 4.500 s crosses the seam, so it resolves to a concat span.

      * the span's FIRST frame must be byte-identical to `-ss 3.5` on chapter A alone;
      * the span must hand over to chapter B AT the seam — chapter B's own first frame, arriving
        directly after chapter A's own last frame, in the output slot at the seam (or the next one:
        at the exact boundary the `fps` filter can still hold chapter A's last frame, which covers
        the picture right up to the seam. That tie predates this change and is one frame wide);
      * the old `inpoint` list, decoded alongside as a control, starts at or before the request —
        which is the whole defect, and what keeps this test from passing vacuously.
    """
    if not _require_ffmpeg("seam_decodes_the_frame_it_asked_for"):
        return
    tmp = os.environ.get("TMPDIR", "/tmp")
    ch_a = os.path.join(tmp, "pacer_seam_a.mp4")
    ch_b = os.path.join(tmp, "pacer_seam_b.mp4")
    _make_chapter(ch_a, 4.0, seed=42, hue=0)
    _make_chapter(ch_b, 4.0, seed=99, hue=120)
    try:
        dur_a, dur_b = _probe_duration(ch_a), _probe_duration(ch_b)
        cm = chapters.ChapterMap([ch_a, ch_b], [dur_a, dur_b])
        seam = dur_a
        t0, t1 = seam - 0.5, seam + 0.5          # 3.5 s: 15 frames past the keyframe at 3.0 s
        src = ev.resolve_video_source(cm, t0, t1, tmp_dir=tmp)
        try:
            assert src.concat_list_path is not None, "a seam-crossing window must be a concat span"
            assert src.time_offset == 0.0
            spec = ev.ExportSpec(out_path=os.path.join(tmp, "unused.mp4"), lap_id=0,
                                 t0=t0, t1=t1, source=src)
            assert abs(spec.local_t0 - t0) < 1e-9, spec.local_t0

            n_seam = int(round((seam - t0) * FPS))
            got = _decode(ev.build_decode_cmd(spec, W, H, FPS), n_seam + 4)
            assert len(got) >= n_seam + 2, f"decoded only {len(got)} frames"

            want_first = _decode_at(t0, ["-i", ch_a], 0.2, 1)[0]
            want_seam = _decode_at(0.0, ["-i", ch_b], 0.2, 1)[0]
            a_last = _decode_at(max(0.0, dur_a - 0.2), ["-i", ch_a], 0.3, 30, resample=False)[-1]

            # --- a reference strip of chapter A's own frames around t0, so a MISS is reported as a
            #     time and not just as "different" (this is how the D24 offset was measured).
            strip_from = t0 - 1.2
            strip = _decode_at(strip_from, ["-i", ch_a], 2.0, 60, resample=False)

            def where(frame):
                d = [float(np.abs(frame.astype(np.int16) - r.astype(np.int16)).mean())
                     for r in strip]
                k = int(np.argmin(d))
                return strip_from + k / FPS, d[k]

            at, diff = where(got[0])
            assert np.array_equal(got[0], want_first), (
                f"the span's first frame is the picture at {at:.3f}s, not the requested "
                f"{t0:.3f}s — off by {at - t0:+.3f}s (best |delta| {diff:.3f})")
            handover = next((i for i, f in enumerate(got) if np.array_equal(f, want_seam)), None)
            assert handover is not None, "chapter B's first frame never appears in the span"
            assert abs(handover - n_seam) <= 1, (
                f"chapter B starts at output frame {handover} (t={t0 + handover / FPS:.4f}s), "
                f"{(handover - n_seam) / FPS:+.4f}s from the seam at {seam:.4f}s")
            assert np.array_equal(got[handover - 1], a_last), (
                "chapter A's last frame does not sit immediately before chapter B's first — "
                "the span drops or reorders picture across the boundary")

            # --- the control: the pre-fix list, built by hand. Its first frame is the keyframe at
            #     or before t0, never after it; on this GOP that is 3.000 s, half a second early.
            ctrl_path = os.path.join(tmp, "pacer_seam_inpoint.txt")
            with open(ctrl_path, "w", encoding="utf-8") as f:
                f.write(f"file '{ch_a}'\ninpoint {t0:.6f}\nfile '{ch_b}'\n")
            ctrl = _decode_at(0.0, ["-f", "concat", "-safe", "0", "-i", ctrl_path], 0.2, 1)
            ctrl_at, _ = where(ctrl[0])
            os.remove(ctrl_path)
            assert ctrl_at <= t0 + 1e-6, (
                f"the inpoint control started at {ctrl_at:.3f}s, AFTER the requested {t0:.3f}s — "
                "the control no longer models the defect this test guards")
            print(f"    control (pre-fix `inpoint` list): first frame at {ctrl_at:.3f}s, "
                  f"{ctrl_at - t0:+.3f}s from the request")
        finally:
            src.cleanup()
    finally:
        for p in (ch_a, ch_b):
            if os.path.exists(p):
                os.remove(p)
    print("test_a_seam_crossing_window_decodes_the_frame_it_asked_for OK "
          "(first frame exact at t0; seam frame exact)")


def test_the_span_audio_starts_at_the_same_instant_as_the_picture():
    """Audio is mapped from a SECOND ffmpeg input over the same source-local window
    (`build_encode_cmd`), so a source that seeks the wrong instant desyncs the sound as well as the
    numbers. Measured independently of the picture: cross-correlate the decoded PCM against chapter
    A's whole audio track and read off where it starts. Deterministic white noise, so the peak is
    unambiguous (a tone is periodic and would match anywhere).

    The span is compared against the SINGLE-FILE branch at the same instant, not against t0 in the
    abstract, because ffmpeg does not trim a concat input's audio to the sample: the span starts on
    the AAC packet boundary at or before t0, i.e. up to 1024/48000 = 21.3 ms early. That is what a
    concat input costs; the assertion is that it costs no MORE than that, and the measured number
    is printed so a regression reads as a number rather than as a pass."""
    if not _require_ffmpeg("seam_audio_starts_where_the_picture_does"):
        return
    tmp = os.environ.get("TMPDIR", "/tmp")
    ch_a = os.path.join(tmp, "pacer_seam_aud_a.mp4")
    ch_b = os.path.join(tmp, "pacer_seam_aud_b.mp4")
    _make_chapter(ch_a, 4.0, seed=7, hue=0)
    _make_chapter(ch_b, 4.0, seed=8, hue=120)
    try:
        cm = chapters.ChapterMap([ch_a, ch_b], [_probe_duration(ch_a), _probe_duration(ch_b)])
        t0, t1 = cm.chapters[1].offset - 0.5, cm.chapters[1].offset + 0.5
        src = ev.resolve_video_source(cm, t0, t1, tmp_dir=tmp)
        try:
            spec = ev.ExportSpec(out_path=os.path.join(tmp, "unused.mp4"), lap_id=0,
                                 t0=t0, t1=t1, source=src)
            enc = ev.build_encode_cmd(spec, W, H, FPS)
            assert enc[enc.index("-ss") + 1] == f"{spec.local_t0:.6f}", (
                "the mux must seek the audio to the same local instant the decode seeks the video")
            ref = _audio_at(0.0, ["-i", ch_a], 4.0)
            span_lag, span_sharp = _lag_samples(_audio_at(spec.local_t0, src.input_args(), 0.3), ref)
            one_lag, one_sharp = _lag_samples(_audio_at(t0, ["-i", ch_a], 0.3), ref)
            assert min(span_sharp, one_sharp) > 5.0, (
                f"correlation peak is not distinctive ({span_sharp:.1f}x / {one_sharp:.1f}x median)")
            span_off, one_off = span_lag / SR - t0, one_lag / SR - t0
            aac_frame = 1024.0 / SR
            assert abs(span_off - one_off) <= aac_frame + 1e-6, (
                f"the span's audio starts {span_off:+.5f}s from the requested {t0:.3f}s against the "
                f"single file's {one_off:+.5f}s — {span_off - one_off:+.5f}s apart, more than the "
                f"one AAC packet ({aac_frame * 1000:.1f} ms) a concat input costs")
            assert abs(span_off) < 1.0 / FPS, (
                f"the span's audio is {span_off:+.5f}s from t0 — over a video frame")
            print(f"    audio: span {span_off:+.5f}s, single file {one_off:+.5f}s, "
                  f"{span_off - one_off:+.5f}s apart (peaks {span_sharp:.0f}x / {one_sharp:.0f}x)")
        finally:
            src.cleanup()
    finally:
        for p in (ch_a, ch_b):
            if os.path.exists(p):
                os.remove(p)
    print("test_the_span_audio_starts_at_the_same_instant_as_the_picture OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok  {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"FAIL {t.__name__}: {exc}")
    if failed:
        print(f"\n{failed}/{len(tests)} export-seam tests FAILED")
        sys.exit(1)
    print(f"\nALL {len(tests)} export-seam tests passed")
