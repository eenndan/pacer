# Engineering notes — eight measurements that changed the code

Pacer is built by one person, with the implementation delegated to LLM coding agents. What keeps it
correct is not trust in either. It is a set of briefs, gates and measurement habits, and the rule
that a number outranks a plan. The eight stories below are the ones where a measurement overturned
something: a design, a review, a published figure, or the process itself. Each is anchored on one
number, and links to the pull request, test or document that holds it.

At the end is a [15-minute code tour](#a-15-minute-code-tour): the five files that matter.

- [1. The camera already knows the time](#1-the-camera-already-knows-the-time)
- [2. An ideal lap that was 44 % artefact](#2-an-ideal-lap-that-was-44--artefact)
- [3. Phase bars wrong on every corner](#3-phase-bars-wrong-on-every-corner)
- [4. The GPS is late, and the gyroscope said so](#4-the-gps-is-late-and-the-gyroscope-said-so)
- [5. Gates that are proven to go red](#5-gates-that-are-proven-to-go-red)
- [6. Fourteen features measured and refused](#6-fourteen-features-measured-and-refused)
- [7. The accuracy claim, re-proven on current footage](#7-the-accuracy-claim-re-proven-on-current-footage)
- [8. The argument that was an output](#8-the-argument-that-was-an-output)

---

## 1. The camera already knows the time

**4.5×.** That is how much more widely a fitted clock's lap times scattered around a real
transponder's than the times from the clock the camera writes itself.

A GoPro stores GPS in payloads of about one second. The media timestamps say when a payload
starts and ends, not when each fix inside it was taken. A tool that spreads the fixes evenly
across the payload runs about 0.1 % off the true rate, and that bias lands on every lap. The
upstream project Pacer was forked from fitted a per-sample clock to those payloads instead, and a
port of that fit lived in this repo for a while.

On a Hero 11 or a Hero 13, the GPS9 stream stamps every fix with the receiver's own time, so there
is nothing to fit. Against the transponder log of a 24-hour race, the fitted clock matched GPS9 on
the cleaner of two recordings. On the noisier one it diverged, with a per-lap spread 4.5 times
GPS9's, and it cut one lap nearly five seconds short. One recording would have called the two
methods equivalent. The fit was deleted, and GPS9 is the timing path. A recording without it falls
back to the video clock, and every duration derived from it is muted and labelled estimated.

Receipt: [the investigation](../studio/docs/upstream-20ms-investigation.md) (June 2026, both
recordings, against the transponder) · [how accuracy is measured](ACCURACY.md#how-its-measured) ·
`studio/load.py::_gps9_times`.

## 2. An ideal lap that was 44 % artefact

**44 %** of the ideal lap's headline gap to the best lap was an artefact of geometry. It was not
time the driver could find.

The ideal lap stitches each corner and straight from whichever lap drove it fastest. To compare
laps, the reference lap's corner boundaries are projected onto every other lap. That projection
chose its frame boundary by boundary. Where a spatial match succeeded it used the matched position;
where it failed, a proportional one. A segment with one edge in each frame shrank or stretched by
the local odometer offset, and one straight came out at under three-quarters of its length. The
per-segment minimum then collected every shrunken window and never paid for the stretched ones.

Nothing downstream could see it, because a lap's segment times still summed exactly to its lap
time. That was the only invariant being checked, and it is necessary but not sufficient. The share
of the gap that was artefact was measured against a null that the defect could not reach: laps
that sat below the drift gate, and so were projected identically by every candidate. The repair
gives each lap one alignment frame, a monotone warp anchored at the timing line, and admits a cell
only if its span is within 5 % of what that lap should cover.

Receipt: [#228](https://github.com/eenndan/pacer/pull/228) · `studio/corners.py::lap_alignment`.

## 3. Phase bars wrong on every corner

**+0.493 s.** That was the largest error in the coaching page's corner phase bars. It sat on the
slowest corner, and every one of the 12 corners on that lap was off.

A coaching row names a corner's time loss and splits it into entry, apex and exit bars. A critical
review found rows arguing with their own bars: one listed a loss beside bars that said the driver
had been faster. The review read this as two statistics needing reconciliation, and proposed
recomputing the bars as a cross-lap median. Measured, that fix cut the clashes from four to three,
one of them new.

The real cause was an estimator. The bars integrated distance over speed, and `1/v` amplifies any
speed error exactly where the kart is slowest, which is where a corner's time is largest. Audited
against the timing clock on the best lap, every corner's thirds disagreed with the corner's own
time. The fix reads the clock: the seconds between two points of the lap, interpolated exactly as
the corner splits are. The thirds now add up to the corner's time by construction.

The golden gate had barely noticed. The phase matrix was in no fingerprint, and the synthetic
fixture reported zero change for a fix that moved every phase number in the app. It is fingerprinted
now. The lasting rule: a review's diagnosis is a premise, so measure the proposed fix, not only the
defect.

Receipt: [#236](https://github.com/eenndan/pacer/pull/236) · `studio/coaching.py::_span_clock`.

## 4. The GPS is late, and the gyroscope said so

**0.46 s.** That is how late the GPS arrives against the picture. The camera stamps its
accelerometer and gyroscope on the video's own clock, while the GPS receiver stamps its fixes
itself.

The offset was measured by correlating the gyroscope's yaw rate with the yaw rate implied by the
GPS path. An estimator control delayed the real gyro stream by 0.4 s, and the answer moved by
exactly that. Switching each filter off in turn showed that none of them was manufacturing the
offset. To decide which clock was late, yaw was also read from the video frames themselves. The
picture agreed with the gyroscope, so the GPS is the late channel. The lag held constant across
chapter seams. Its apparent drift was the measurement's, not the channel's: the media clock runs a
few tens of ppm fast, which is why lags are measured on the picture's clock and not the
telemetry's.

The first PR only disclosed the offset. The fix came next, at one seam, and the seam had a trap:
shifting only the GPS would have pushed the g-meter's accelerometer axis ahead of the picture, so
both axes now cross the same map. Later, a synthetic recording with a known lag found a half-sample
bias in the reference filter, and the installed lag was re-measured on every working recording.

Receipt: [#291](https://github.com/eenndan/pacer/pull/291) (measured and disclosed) ·
[#301](https://github.com/eenndan/pacer/pull/301) (corrected) ·
[#320](https://github.com/eenndan/pacer/pull/320) (one figure, every clock crossing declared) ·
[#383](https://github.com/eenndan/pacer/pull/383) (checked against ground truth).

## 5. Gates that are proven to go red

**0.41 ms.** That is the largest lap-time error the real loader makes on a synthetic recording
with a noise-free trace, timed at a line on the straight, against the truth it was built from.

The golden gate compares a fingerprint of the whole analysis API, leaf by leaf, before and after a
change; in CI it runs on a synthetic session. Then the gate itself was tested against history. Two
real fixes, one of them story 2's, were reverted one at a time, and the gate stayed green both
times: its fixture had two laps on one polyline with noise-free speed, where neither defect can
occur. A new phase was built to reach them: a lap past the drift gate with one failed boundary
match, speed noise on the column the coast detector reads, and a third lap so the corners stay put.
Each ingredient was shown necessary, and both reverts now fail the gate.

The same idea, pushed further, is a synthetic GoPro recording with known truth. It is a chaptered
MP4 with real GPMF streams: GPS noise and glitches, the measured GPS lag and media-clock rate, and
an IMU on a misaligned mount. It goes through the real loader in CI, and the noise-free laps land
within 0.41 ms of the truth. Planted defects fail it by name: timing on the media clock, a 500 ppm
clock error, a step at a chapter seam, a gravity axis left unpermuted.

Receipt: [#286](https://github.com/eenndan/pacer/pull/286) ·
[#371](https://github.com/eenndan/pacer/pull/371) · `tests/test_golden_synthetic.py` ·
`tests/test_synth_gopro.py`.

## 6. Fourteen features measured and refused

**14** features were built far enough to measure, and the measurement said no.

An idea here is not refused in a meeting. It is prototyped against real recordings, given a
statistic that could prove it, and refused only when that statistic says so. One example is a
"mistake hangover" detector: does a bad corner make the next one worse? Two permutation nulls, with
a family-wise correction, found one significant corner pair on each of two recordings of the same
driver and track a day apart. It was a different pair each time. Others include an engine-RPM
readout from the audio track, a gyro bridge for the map across GPS dropouts, and taking a
neutralised race's slow laps out of the clean set.

Each refusal is written down with the number that killed it, so whoever proposes it next starts
from the evidence rather than the idea. The record is itself guarded. Its sections must be numbered
once each, its opening count is derived from them, and every citation of a section elsewhere in the
tree must land on one that exists.

Receipt: [Features measured and refused](../studio/docs/refused-2026-09.md) ·
`tests/test_measured_figures.py`.

## 7. The accuracy claim, re-proven on current footage

**σ 0.0247 s** per lap. That is Pacer's timing on a September 2026 sprint race, against the
circuit's own published timing.

The headline accuracy claim rested on two recordings validated in June 2026 against a transponder
log. When that footage left the development machine, the page said so: the claim "has not been
repeated". So it was repeated, on footage still on the machine. The sheet held every driver of
every heat that day, and the session was located in it with no race start and no hand matching.

The rule that decides a match was written into code before the sheet was first locked against it.
That order matters. Every driver in one race shares its slow opening laps and yellow flags, so
another driver's laps correlated strongly too. Correlation cannot see level or scale, so the lock
also requires the best-correlated window to be the one with the smallest residual, with every
rival's residual at least three times larger. The nearest rival's came out 44 times larger.

A real-footage check now re-runs that lock every time and holds the published row to it. The
adapter that reads the timing pages never reads a driver's name. The other circuit's recordings
still wait for their timing sheets, and the page says so.

Receipt: [#382](https://github.com/eenndan/pacer/pull/382) · [docs/ACCURACY.md](ACCURACY.md) ·
`studio/dev/_validate_wallclock.py`.

## 8. The argument that was an output

**11.9 GB.** That was the first chapter of a race recording, overwritten with 2.4 MB of JSON.

A developer tool, the golden-gate dump, took its output path as its first positional argument. A
coding agent passed it the path of a recording, reading that argument as the input. The tool did
exactly what it was told. The other two chapters survived; the first had no second copy.

The fix went to the interface, not to whoever typed. A positional argument reads as an input, and
a tool that lets one slip clobber any path is the defect. The changes are structural:

- **Outputs must earn the write.** The dump refuses any destination that is not a new `.json`. The
  media tool takes the recording as its positional input, outputs behind `--out`. The timing
  validator refuses to write into the footage folder or over an input, and a test holds it to that.
- **The app survives the damage.** A chapter that is not a video container is skipped and named, so
  the rest of the recording still opens.
- **Tests cannot reach the user's data.** Every test registration runs jailed, and a guard fails
  the build if a test, or its child process, can resolve the real app-support directory. It came
  after a test wrote one fixture row into a real library index.
- **Footage is read-only by rule and locked by the file system,** so a mistaken write fails before
  any code runs.

Receipt: `studio/dev/golden_session_dump.py::_resolve_out_path` ·
`studio/chapters.py::split_non_mp4` · [#328](https://github.com/eenndan/pacer/pull/328) ·
`tests/test_app_support_jail.py`.

---

## A 15-minute code tour

Five files, in reading order. They carry the load, and everything else is built on them.

1. **[`pacer/laps/laps.cpp`](../pacer/laps/laps.cpp)** (C++): `Laps::Update` walks consecutive
   fixes and asks `Split` (`pacer/geometry/`) whether their chord crosses the timing line. When it
   does, the crossing instant is interpolated along the chord, so lap times are sub-sample accurate.
   That is the whole of lap timing: geometry, no heuristics.
2. **[`studio/load.py`](../studio/load.py)**: `load_recording` turns the GPMF streams into the trace
   everything reads. It builds the GPS9 true-clock axis (`_gps9_times`), records which clock it
   actually built, and trims, cleans and smooths the fixes. The DATA TRUST card reports what it
   decided.
3. **[`studio/media_clock.py`](../studio/media_clock.py)**: the second clock. It holds one affine
   map per recording between the telemetry and the picture, and the crossing where the measured GPS
   lag is taken out (story 4).
4. **[`studio/corners.py`](../studio/corners.py)**: corners detected from pooled curvature, and
   `lap_alignment` / `project_boundaries`, the one-frame-per-lap warp from story 2. Every per-corner
   number in the app passes through here.
5. **[`studio/dev/golden_session_dump.py`](../studio/dev/golden_session_dump.py)**: `fingerprint()`
   walks the session's public analysis API into leaves, and `golden_compare` diffs two dumps leaf by
   leaf. `tests/test_golden_synthetic.py` runs it in CI (story 5).

`studio/session.py` is the facade that ties them together: read its method list, not its body. The
rules the agents work by are in [AGENT-PLAYBOOK.md](AGENT-PLAYBOOK.md).
