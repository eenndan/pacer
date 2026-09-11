#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>
#include <utility>
#include <vector>

#include <pacer/datatypes/datatypes.hpp>

namespace pacer {

// One IMU stream (ACCL / GYRO / GRAV / CORI) collected as parallel columns, so
// the studio layer crosses the binding ONCE per stream instead of once per
// sample (the old path ran a per-sample C++->Python trampoline callback — ~1.5M
// round-trips per load). The columns are the SAME samples the per-sample
// ReadAccl/ReadGyro/ReadGrav/ReadCori callbacks yield, in the same order, so
// the bulk output is byte-for-byte identical to collecting those callbacks.
//
// `times`, `xs`, `ys`, `zs` are populated for all four streams; `ws` carries
// the quaternion scalar and is filled ONLY by ReadCoriColumns (ACCL/GYRO/GRAV
// leave it empty). Every populated column has the same length (the sample
// count).
struct ImuArrays {
  std::vector<double> times;
  std::vector<double> ws;
  std::vector<double> xs;
  std::vector<double> ys;
  std::vector<double> zs;
};

// The container's OWN statement of how an IMU stream's three elements map onto
// the camera's physical axes: GPMF's `ORIN` (the orientation of the RAW
// elements as written, one letter per element, lowercase meaning negated) and
// `ORIO` (the orientation the camera intends a consumer to present). Either can
// be empty, and both often are: of the ten bundled sample clips four carry
// neither, and the NEWEST camera measured (HERO13) carries ORIN with no ORIO.
//
// READ IT, BUT DO NOT REORDER BY IT. Measured on the ten bundled sample clips
// and both D24 recordings, these fields are written ONLY on ACCL and GYRO;
// GRAV, CORI and IORI carry no orientation field on any camera that has them.
// And the studio g-meter's whole chain (ACCL minus GRAV, rotated by CORI) rides
// ONE element frame shared by all four streams — which measurement says is the
// RAW frame, not the presented one: the same fixed permutation maps raw GYRO
// onto the CORI-derived body rate on a HERO8 (ORIN "zxY"), a GoPro Max
// ("XzY") and a HERO13 ("ZXY"), three DIFFERENT declarations. Canonicalising
// ACCL/GYRO by ORIN while GRAV/CORI stay raw would therefore break an alignment
// that currently holds. See studio/docs/gmeter-validation.md.
//
// So this is a DIAGNOSTIC: it lets the app SAY which convention a recording
// declares instead of assuming one (studio.dev.diagnose prints it), and it is
// what tests/test_imu_orientation.py pins per camera generation so a camera
// that declares something new fails visibly. The runtime guard on the frame is
// a separate, measured one: studio.gmeter.axis_check.
struct ImuOrientation {
  std::string accl_in;  // ACCL `ORIN`, e.g. "ZXY" / "zxY" / "" when absent
  std::string accl_out; // ACCL `ORIO`, e.g. "ZXY" / "" when absent
  std::string gyro_in;  // GYRO `ORIN`
  std::string gyro_out; // GYRO `ORIO`
};

// Abstract source of raw GPS / IMU samples — "raw" meaning it hands back fixes
// without imposing a meaningful global timeline of its own.
//
// Return-code convention: the uint32_t-returning methods (ReadSamples, Seek)
// follow the GoPro GPMF parser's codes — 0 (GPMF_OK) is success and any nonzero
// value is some GPMF_ERROR_* diagnostic (GPMFSource::ReadSamples returns
// GPMF_ERROR_MEMORY == 1 when the cursor sits on an empty index). No caller
// ever branches on a particular nonzero code — only zero vs nonzero — so a
// source written outside the parser (a test, a Python subclass) may return 0
// for success and any nonzero value for "nothing here".
class RawGPSSource {
public:
  RawGPSSource() = default;
  virtual ~RawGPSSource() = default;

  // Decode the GPS payload the cursor currently sits on (Seek/Next move it),
  // calling on_sample(sample, current_index, total_records) once per fix.
  // Returns 0 on success or a nonzero code (e.g. no payload here).
  //
  // It is a std::function virtual — the same shape as
  // ReadAccl/ReadGrav/ReadCori — so a Python subclass can override it through
  // the binding trampoline and feed GPS into the engine (for instance as a
  // child of a C++ SequentialGPSSource). The earlier raw-pointer +
  // function-pointer `Samples` virtual could not be trampolined, so Python
  // overrides silently produced nothing. The base implementation emits nothing
  // and returns 0; GPMFSource and SequentialGPSSource override it.
  virtual uint32_t
  ReadSamples(std::function<void(GPSSample, uint32_t, uint32_t)> on_sample);

  // Read the timestamped IMU streams (accelerometer / gyroscope / gravity)
  // across the WHOLE source. Each sample's `time` is on the MEDIA clock
  // (seconds), spread across the payload span so it lines up with the GPS spans
  // and the video; a multi-chapter source shifts later chapters by the
  // cumulative duration (see SequentialGPSSource) onto one continuous global
  // clock. The base is a no-op; GPMFSource / SequentialGPSSource override.
  //
  // ACCL is a 3-axis accelerometer in m/s^2, elements in the camera's own RAW
  // order (ZXY on a HERO13 — see ReadImuOrientation for what the container
  // declares and why it is not applied); GRAV is a unit gravity vector in the
  // same raw frame, permuted vs ACCL by a fixed swap the studio layer resolves.
  virtual void ReadAccl(std::function<void(IMUSample)> /*on_sample*/) {}
  // GYRO is the 3-axis rate gyroscope in rad/s on the SAME media clock.
  //
  // AXES: it declares the SAME element orientation as ACCL. Measured across
  // every clip in 3rdparty/gpmf-parser/samples plus both D24 recordings, the
  // GPMF ORIN/ORIO fields of ACCL and GYRO are identical on every camera,
  // whatever they say: HERO6/7 "YxZ"/"ZXY", HERO8 "zxY"/"ZXY", Max "XzY"/"ZXY",
  // HERO13 "ZXY" with NO ORIO at all, and BOTH fields absent on
  // HERO5/Fusion/Karma (and on older HERO6 firmware — hero6.mp4 carries none
  // where hero6a.mp4 does). So GYRO inherits whatever axis convention the
  // studio layer applies to ACCL, and adds no orientation risk of its own.
  //
  // The convention the studio layer applies is a FITTED constant rather than a
  // read of ORIN, and measurement says that is correct rather than merely
  // convenient: GRAV/CORI carry no ORIN on any camera and ride the RAW element
  // frame, so canonicalising ACCL/GYRO by ORIN alone would break them. The
  // field is read and reported (ReadImuOrientation) and the alignment it used
  // to stand in for is now MEASURED per recording by the studio g-meter's axis
  // check, which refuses the IMU path rather than mis-orienting it silently.
  //
  // RATE: ~200 Hz on the HERO13 recordings, where GYRO and ACCL happen to carry
  // identical per-payload sample counts — but that is a coincidence of that
  // model, not a rule. Measured on the bundled samples, GYRO runs at 2x ACCL on
  // HERO5 and Karma, 4x on a Max in 360 mode, and 17x on a Fusion. Never index
  // one stream by the other's row; interpolate on time.
  virtual void ReadGyro(std::function<void(IMUSample)> /*on_sample*/) {}
  virtual void ReadGrav(std::function<void(IMUSample)> /*on_sample*/) {}
  // CORI is the camera-orientation quaternion (w,x,y,z), ~60 Hz, media-clock
  // time.
  virtual void ReadCori(std::function<void(QuatSample)> /*on_sample*/) {}

  // Bulk column readers: collect the WHOLE stream into parallel std::vector
  // columns in ONE call (see ImuArrays), avoiding the per-sample Python
  // trampoline of the ReadAccl/ReadGyro/ReadGrav/ReadCori callbacks. They emit
  // the SAME samples in the SAME order as those callbacks (byte-for-byte), just
  // packed as columns. The vec3 readers fill times/xs/ys/zs; the quaternion
  // reader additionally fills ws. The base default reuses the per-sample
  // reader, so a Python subclass that overrides only
  // ReadAccl/ReadGyro/ReadGrav/ReadCori is bulk-read correctly through it;
  // GPMFSource/SequentialGPSSource inherit this default too (the collection
  // cost is identical — the win is one binding crossing).
  virtual ImuArrays ReadAcclColumns();
  virtual ImuArrays ReadGyroColumns();
  virtual ImuArrays ReadGravColumns();
  virtual ImuArrays ReadCoriColumns();

  // The recording camera's own name for itself — the GPMF `DVNM` field, e.g.
  // "HERO13 Black". Empty when the container carries none. It is the only
  // in-file statement of WHICH camera produced the streams, and the camera
  // model decides what the data can mean at all: a HERO12 has no GPS receiver,
  // and HERO9/10 carry no per-sample GPS clock. Read once (it is a per-payload
  // constant), never per sample. The base returns "".
  virtual std::string DeviceName() const { return {}; }

  // The ACCL/GYRO axis declaration this container carries (see ImuOrientation
  // for what it means and why it is a diagnostic, not a transform). Like
  // DeviceName it is a per-payload constant, so it is read once off the first
  // payloads that have it; every field is "" on a camera that writes none. The
  // base returns all-empty.
  virtual ImuOrientation ReadImuOrientation() const { return {}; }

  // Move the cursor to the chunk covering `target`.
  virtual uint32_t Seek(double target) = 0;

  // Advance to the next chunk.
  virtual void Next() = 0;

  // Has the cursor run past the last chunk?
  virtual bool IsEnd() = 0;

  // Time span of the chunk under the cursor.
  virtual auto CurrentTimeSpan() const -> std::pair<double, double> = 0;

  // Total duration of the stream this source READS — for a GPMF source that is
  // the metadata track, which is what the payload cursor is bounded by.
  virtual double GetTotalDuration() const = 0;

  // Duration of the VIDEO track: where the NEXT chapter's picture begins, and
  // therefore the only correct amount to shift a following chapter by.
  //
  // It is a SEPARATE question from GetTotalDuration() because the two tracks
  // are separate tracks. GoPro's own contract is that a chapter's metadata
  // length matches its video length EXCEPT in the last chapter of a recording,
  // where the GPMF track ends on its own payload grid — measured on the ten
  // GoPro sample clips in 3rdparty/gpmf-parser/samples, that exception runs
  // from -0.701 s (hero7) to +0.934 s (karma), i.e. up to a whole payload. A
  // chain that shifts by the metadata length therefore rides ~1 s of phantom
  // offset the moment a chapter exercises it, and the shift belongs to the
  // picture regardless. The default answers with GetTotalDuration() so a source
  // with no video track of its own (a test double, a Python subclass) behaves
  // exactly as it did before this existed.
  virtual double GetVideoDuration() const { return GetTotalDuration(); }
};

// A RawGPSSource backed by the GPMF metadata track of an MP4 container: opens
// the file and walks its GPS / IMU / orientation streams.
class GPMFSource : public RawGPSSource {
public:
  // C++ ONLY: take ownership of an already-opened gpmf-parser MP4 handle. Kept
  // out of the Python bindings (see generate-bindings.py) because a stray
  // integer from Python would be reinterpreted as an mp4-object pointer and
  // crash.
  explicit GPMFSource(size_t mp4handle);
  explicit GPMFSource(const char *filename);
  ~GPMFSource() noexcept;

  // See RawGPSSource::ReadSamples for the callback contract.
  uint32_t ReadSamples(
      std::function<void(GPSSample, uint32_t, uint32_t)> on_sample) override;

  void ReadAccl(std::function<void(IMUSample)> on_sample) override;
  void ReadGyro(std::function<void(IMUSample)> on_sample) override;
  void ReadGrav(std::function<void(IMUSample)> on_sample) override;
  void ReadCori(std::function<void(QuatSample)> on_sample) override;

  std::string DeviceName() const override;
  ImuOrientation ReadImuOrientation() const override;

  uint32_t Seek(double target) override;
  void Next() override;
  bool IsEnd() override;
  std::pair<double, double> CurrentTimeSpan() const override;
  double GetTotalDuration() const override;
  double GetVideoDuration() const override;

private:
  // Walk one fixed-width GPMF stream (<= 4 elements per sample) over every
  // payload, calling emit(values[nelem], nelem, media_time) per sample.
  void ReadStream(
      uint32_t fourcc,
      const std::function<void(const double * /*vals*/, uint32_t /*nelem*/,
                               double /*time*/)> &emit) const;

  uint32_t index_ = 0;
  size_t mp4handle_;
  // The owned GPMF payload buffer (resObject). Allocated lazily on first use
  // and GROWN in place across ReadSamples()/ReadStream() calls (the parser
  // reuses it), then released in the destructor; 0 means "not yet allocated".
  // Allocating per call, as the original code did, leaked one buffer per call.
  mutable size_t payload_res_ = 0;
};

// Concatenates two sources end to end (chapter chaining): the right child's
// timeline is shifted by the left child's VIDEO duration so the pair reads as
// one continuous recording. `left` may itself be a SequentialGPSSource, so
// chains of any length nest.
//
// THE SHIFT IS THE VIDEO'S, NOT THE METADATA TRACK'S. The right child's payload
// times are local to its own file and everything downstream (lap timing, the
// chapter offset table, the export's ffmpeg seek, the player's source switch)
// reads them as positions in the recording's PICTURE. Chapter k+1's picture
// starts at the end of chapter k's picture, so that is the offset; shifting by
// chapter k's GPMF length instead silently rides the difference between the two
// tracks, which on GoPro's own sample clips reaches 0.9 s.
class SequentialGPSSource : public RawGPSSource {
public:
  SequentialGPSSource(RawGPSSource *left, RawGPSSource *right)
      : left_{left}, right_{right}, current_{left_} {}

  virtual ~SequentialGPSSource() override = default;

  double GetTotalDuration() const override;
  double GetVideoDuration() const override;
  bool IsEnd() override;

  uint32_t ReadSamples(
      std::function<void(GPSSample, uint32_t, uint32_t)> on_sample) override;

  void ReadAccl(std::function<void(IMUSample)> on_sample) override;
  void ReadGyro(std::function<void(IMUSample)> on_sample) override;
  void ReadGrav(std::function<void(IMUSample)> on_sample) override;
  void ReadCori(std::function<void(QuatSample)> on_sample) override;

  // The chain's camera: the LEFT subtree's name, falling back to the right when
  // the left has none. Chapters of one recording come off one camera, so a
  // chain has a single device name; the fallback only matters for a chain whose
  // first chapter is a synthetic/nameless source.
  std::string DeviceName() const override;

  // The chain's axis declaration, resolved the same way and for the same
  // reason: chapters of one recording come off one camera.
  ImuOrientation ReadImuOrientation() const override;

  uint32_t Seek(double target) override;
  void Next() override;
  std::pair<double, double> CurrentTimeSpan() const override;

private:
  // Read one IMU stream from both children, offsetting the right child's
  // samples by the left subtree's VIDEO duration so they share one global
  // media clock. `read` is the member reader to invoke
  // (ReadAccl/ReadGyro/ReadGrav/ReadCori) and `S` the sample type, which must
  // have a `.time`. Going through `read`
  // lets a nested SequentialGPSSource on the left recurse correctly.
  template <class S, class Read>
  void ReadShifted(Read read, const std::function<void(S)> &on_sample) {
    (left_->*read)(on_sample);
    double off = left_->GetVideoDuration();
    (right_->*read)([&](S s) {
      s.time += off;
      on_sample(s);
    });
  }

  RawGPSSource *left_, *right_, *current_;
};

} // namespace pacer
