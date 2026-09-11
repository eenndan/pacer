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
  // ACCL is a 3-axis accelerometer in m/s^2 (native order Z,X,Y); GRAV is a
  // unit gravity vector (native order, permuted vs ACCL — the studio layer
  // resolves that).
  virtual void ReadAccl(std::function<void(IMUSample)> /*on_sample*/) {}
  // GYRO is the 3-axis rate gyroscope in rad/s on the SAME media clock.
  //
  // AXES: it declares the SAME element orientation as ACCL. Measured across
  // every clip in 3rdparty/gpmf-parser/samples plus both D24 recordings, the
  // GPMF ORIN/ORIO fields of ACCL and GYRO are identical on every camera —
  // HERO6/7 "YxZ", HERO8 "zxY", Max "XzY", HERO13 "ZXY", and absent on both for
  // HERO5/Fusion/Karma. So GYRO inherits whatever axis convention the studio
  // layer already applies to ACCL, and adds no orientation risk of its own.
  // (What it does NOT fix: that convention is a fitted constant, not a read of
  // ORIN, so it is right for the camera it was fitted on. Pre-existing, and the
  // same for both streams.)
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

  // Move the cursor to the chunk covering `target`.
  virtual uint32_t Seek(double target) = 0;

  // Advance to the next chunk.
  virtual void Next() = 0;

  // Has the cursor run past the last chunk?
  virtual bool IsEnd() = 0;

  // Time span of the chunk under the cursor.
  virtual auto CurrentTimeSpan() const -> std::pair<double, double> = 0;

  // Total media duration.
  virtual double GetTotalDuration() const = 0;
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

  uint32_t Seek(double target) override;
  void Next() override;
  bool IsEnd() override;
  std::pair<double, double> CurrentTimeSpan() const override;
  double GetTotalDuration() const override;

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
// timeline is shifted by the left child's duration so the pair reads as one
// continuous recording. `left` may itself be a SequentialGPSSource, so chains
// of any length nest.
class SequentialGPSSource : public RawGPSSource {
public:
  SequentialGPSSource(RawGPSSource *left, RawGPSSource *right)
      : left_{left}, right_{right}, current_{left_} {}

  virtual ~SequentialGPSSource() override = default;

  double GetTotalDuration() const override;
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

  uint32_t Seek(double target) override;
  void Next() override;
  std::pair<double, double> CurrentTimeSpan() const override;

private:
  // Read one IMU stream from both children, offsetting the right child's
  // samples by the left subtree's duration so they share one global media
  // clock. `read` is the member reader to invoke
  // (ReadAccl/ReadGyro/ReadGrav/ReadCori) and `S` the sample type, which must
  // have a `.time`. Going through `read`
  // lets a nested SequentialGPSSource on the left recurse correctly.
  template <class S, class Read>
  void ReadShifted(Read read, const std::function<void(S)> &on_sample) {
    (left_->*read)(on_sample);
    double off = left_->GetTotalDuration();
    (right_->*read)([&](S s) {
      s.time += off;
      on_sample(s);
    });
  }

  RawGPSSource *left_, *right_, *current_;
};

} // namespace pacer
