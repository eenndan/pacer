#pragma once

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <utility>

#include <pacer/datatypes/datatypes.hpp>

namespace pacer {

// Base class for raw GPS source.
//
// Being raw in this context means that it does not provide any meaningful
// timestamps to work with.
class RawGPSSource {
public:
  RawGPSSource() = default;
  virtual ~RawGPSSource() = default;

  // Main interface to take samples from current GPS source.
  //
  // Args:
  //   void *data:  associated data object with callback;
  //   on_sample:  void (*)(void *, GPSSample, size_t, size_t) callback
  //   function, takes following arguments:
  //     - data provided earlier;
  //     - sampled data;
  //     - index of current data;
  //     - total number of records in a batch.
  virtual uint32_t
  Samples(void *data, void (*on_sample)(void * /*data*/, GPSSample /*sample*/,
                                        size_t /*current_index*/,
                                        size_t /*total_records*/));

  // Convenient way of invoking Samples function: designed to be used with
  // functional objects (e.g. lambdas).
  template <class F> uint32_t Samples(F on_sample) {
    return Samples(&on_sample, [](void *data, GPSSample s, size_t i, size_t n) {
      auto &f = *reinterpret_cast<F *>(data);
      return f(s, i, n);
    });
  }

  uint32_t
  ReadSamples(std::function<void(GPSSample, uint32_t, uint32_t)> on_sample);

  // Reads the timestamped IMU streams (accelerometer / gravity vector) for the WHOLE source.
  // Each sample carries a `time` on the MEDIA clock (seconds), interpolated across the
  // payload span like the research `dump_imu.c` does, so it lines up with the GPS payload
  // spans and the video. Multi-chapter sources shift later chapters by the cumulative
  // duration (see SequentialGPSSource), so the times come out on one continuous global clock.
  // Default (RawGPSSource) is a no-op; GPMFSource / SequentialGPSSource override.
  //
  // ACCL: 3-axis accelerometer in m/s^2 (native order Z,X,Y).
  // GRAV: gravity unit vector (native order; permuted vs ACCL — resolved in the studio layer).
  virtual void ReadAccl(std::function<void(IMUSample)> /*on_sample*/) {}
  virtual void ReadGrav(std::function<void(IMUSample)> /*on_sample*/) {}
  // CORI: camera-orientation quaternion (w,x,y,z), ~60 Hz, media-clock time.
  virtual void ReadCori(std::function<void(QuatSample)> /*on_sample*/) {}

  // Seeks to data chunk covering target.
  virtual uint32_t Seek(double target) = 0;

  // Proceeds to next piece of data.
  virtual void Next() = 0;

  // Checks whenever we already reachend end of the stream.
  virtual bool IsEnd() = 0;

  // Returns current samples' time span.
  virtual auto CurrentTimeSpan() const -> std::pair<double, double> = 0;

  // Gets total MP4 duration.
  virtual double GetTotalDuration() const = 0;
};

// Handler for GPMF track inside MP4 container.
//
// Allows for traversing media file and getting GPS data out of it.
//
// TODO: Provide even more low-level access to underlying samples,
//       might be useful to keep buffer for data in some sort of iterator
//       with option to iterate over GPSSample-s on top of it.
class GPMFSource : public RawGPSSource {
public:
  explicit GPMFSource(size_t mp4handle);
  explicit GPMFSource(const char *filename);
  ~GPMFSource() noexcept;

  // See RawGPSSource::Samples for the callback contract.
  uint32_t Samples(void *data,
                   void (*on_sample)(void * /*data*/, GPSSample /*sample*/,
                                     size_t /*current_index*/,
                                     size_t /*total_records*/)) override;

  void ReadAccl(std::function<void(IMUSample)> on_sample) override;
  void ReadGrav(std::function<void(IMUSample)> on_sample) override;
  void ReadCori(std::function<void(QuatSample)> on_sample) override;

  // Seeks to data chunk covering target.
  uint32_t Seek(double target) override;

  // Proceeds to next piece of data.
  void Next() override;

  // Checks whenever we already reachend end of the stream.
  bool IsEnd() override;

  // Returns current samples' time span.
  std::pair<double, double> CurrentTimeSpan() const override;

  // Gets total MP4 duration.
  double GetTotalDuration() const override;

private:
  // Reads one 4-element-or-fewer GPMF stream over all payloads, emitting per-sample
  // media-clock-timestamped values. `emit(values[4], n_elems, time)` is called per sample.
  void ReadStream(uint32_t fourcc,
                  const std::function<void(const double * /*vals*/, uint32_t /*nelem*/,
                                           double /*time*/)> &emit) const;
  uint32_t index_ = 0;
  size_t mp4handle_;
  // Owned GPMF payload resource (resObject+buffer): allocated lazily on first use and REUSED
  // across Samples()/ReadStream() calls (GetPayloadResource grows it in place), then freed in
  // the destructor. Previously each call leaked a fresh resource. 0 == not yet allocated.
  mutable size_t payload_res_ = 0;
};

class SequentialGPSSource : public RawGPSSource {
public:
  SequentialGPSSource(RawGPSSource *left, RawGPSSource *right)
      : left_{left}, right_{right}, current_{left_} {}

  virtual ~SequentialGPSSource() override = default;

  double GetTotalDuration() const override;

  bool IsEnd() override;

  uint32_t Samples(void *data,
                   void (*on_sample)(void * /*data*/, GPSSample /*sample*/,
                                     size_t /*current_index*/,
                                     size_t /*total_records*/)) override;

  void ReadAccl(std::function<void(IMUSample)> on_sample) override;
  void ReadGrav(std::function<void(IMUSample)> on_sample) override;
  void ReadCori(std::function<void(QuatSample)> on_sample) override;

  uint32_t Seek(double target) override;

  void Next() override;

  // Returns current samples' time span.
  std::pair<double, double> CurrentTimeSpan() const override;

private:
  // Reads one IMU stream across both children, shifting the right (later) chapter's samples by
  // the left subtree's cumulative duration so everything lands on one continuous global media
  // clock. `read` is the per-source reader verb (ReadAccl/ReadGrav/ReadCori); `S` is the sample
  // type, which must carry a `.time` field. left_ may itself be a SequentialGPSSource, so
  // delegating through `read` recurses correctly.
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
