#include "gps-source.hpp"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>

#include "GPMF_common.h"
#include "GPMF_parser.h"
#include "demo/GPMF_mp4reader.h"
#include "pacer/datatypes/datatypes.hpp"

namespace pacer {

GPMFSource::GPMFSource(const char *filename)
    : mp4handle_(OpenMP4Source(const_cast<char *>(filename), MOV_GPMF_TRAK_TYPE,
                               MOV_GPMF_TRAK_SUBTYPE, 0)) {
  if (mp4handle_ == 0) {
    throw std::runtime_error(
        (std::string("Failed to open file: ") + std::string(filename)).c_str());
  }
}

GPMFSource::~GPMFSource() noexcept {
  // Release the lazily-allocated, call-to-call-reused payload buffer before
  // closing the source. Allocating one per call (the original behaviour)
  // leaked.
  if (payload_res_) {
    FreePayloadResource(mp4handle_, payload_res_);
    payload_res_ = 0;
  }
  if (mp4handle_) {
    CloseSource(mp4handle_);
  }
}

GPMFSource::GPMFSource(size_t mp4handle) : mp4handle_(mp4handle) {}

uint32_t GPMFSource::Seek(double target) {
  // `index_` is unsigned, so decrementing it at 0 wraps to UINT32_MAX — a bogus
  // index that then reads like EOF. We initialise the span in/out, only step
  // the index back when the previous lookup SUCCEEDED and we are not already at
  // 0, and stop at 0 rather than spin. Net effect: seeking before the first
  // payload clamps to index 0 instead of wrapping past the end.
  double span_in = 0, span_out = 0;
  uint32_t ret = GPMF_OK;
  do {
    ret = GetPayloadTime(mp4handle_, index_, &span_in, &span_out);
    if (ret == GPMF_OK) {
      if (span_out <= target)
        ++index_;
      if (target < span_in) {
        if (index_ == 0)
          break;
        --index_;
      }
    }
  } while (ret == GPMF_OK && span_in < span_out &&
           ((target > span_out) || (target < span_in)));
  if (ret == GPMF_OK && index_ && !(span_in < span_out)) {
    --index_;
  }

  return ret;
}

void GPMFSource::Next() { ++index_; }

bool GPMFSource::IsEnd() {
  double span_in = 0, span_out = 0;
  if (GetPayloadTime(mp4handle_, index_, &span_in, &span_out) != GPMF_OK) {
    return true;
  }
  return span_in + 1e-9 >= span_out;
}

std::pair<double, double> GPMFSource::CurrentTimeSpan() const {
  double span_in = 0, span_out = 0;
  // An index with no payload reports an empty span instead of leaking stack.
  if (GetPayloadTime(mp4handle_, index_, &span_in, &span_out) != GPMF_OK) {
    return {0, 0};
  }
  return {span_in, span_out};
}

double GPMFSource::GetTotalDuration() const { return GetDuration(mp4handle_); }

// The GPSU stream is a 16-byte ASCII timestamp "YYMMDDHHMMSS.mmm". This view
// reads the digits in place and converts to milliseconds since the Unix epoch.
union GPSUData {
  struct {
    char y[2], m[2], d[2], h[2], min[2], s[2], _, ms[3];
  } str;
  char data[16];

  int64_t Timestamp() const {
    int64_t y = 2000 + (str.y[0] - '0') * 10 + (str.y[1] - '0');
    int64_t m = (str.m[0] - '0') * 10 + (str.m[1] - '0');
    int64_t d = (str.d[0] - '0') * 10 + (str.d[1] - '0');
    int64_t hour = (str.h[0] - '0') * 10 + (str.h[1] - '0');
    int64_t minute = (str.min[0] - '0') * 10 + (str.min[1] - '0');
    int64_t second = (str.s[0] - '0') * 10 + (str.s[1] - '0');
    int64_t milliseconds =
        (str.ms[0] - '0') * 100 + (str.ms[1] - '0') * 10 + (str.ms[2] - '0');

    // Days-from-civil (Howard Hinnant's algorithm,
    // https://howardhinnant.github.io/date_algorithms.html).
    y -= m <= 2;
    const int64_t era = (y >= 0 ? y : y - 399) / 400;
    const unsigned yoe = static_cast<unsigned>(y - era * 400); // [0, 399]
    const unsigned doy =
        (153 * (m > 2 ? m - 3 : m + 9) + 2) / 5 + d - 1;        // [0, 365]
    const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy; // [0, 146096]
    int64_t days_since_1970 = era * 146097 + static_cast<int64_t>(doe) - 719468;

    return days_since_1970 * 24 * 60 * 60 * 1000 +
           1000 * (hour * 60 * 60 + minute * 60 + second) + milliseconds;
  }
};

namespace {

// Same callback shape as RawGPSSource::ReadSamples: (sample, current_index,
// total_records). Handed by reference to the per-codec parsers below.
using SampleEmit = std::function<void(GPSSample, uint32_t, uint32_t)>;

// Decode one GPS9 payload (the stream must already be positioned at its
// samples) and emit every fix. GPS9 element layout:
//   [lat, lon, alt, 2D speed, 3D speed, days-since-2000, secs-since-midnight,
//    DOP, fix(0/2/3)]
// Indices 0-6 are mandatory (a struct narrower than 7 elements would over-read,
// hence the `elements >= 7` guard); DOP (7) and fix (8) are read only when
// present and otherwise keep the GPSSample sentinels.
void ParseGPS9(GPMF_stream *ms, uint32_t samples, uint32_t elements,
               const SampleEmit &emit) {
  if (!(samples && elements >= 7)) {
    return;
  }
  // A vector scratch buffer (rather than malloc/free) unwinds cleanly if `emit`
  // throws mid-loop — e.g. a Python callback raising back through the
  // trampoline.
  std::vector<double> scaled(static_cast<size_t>(samples) * elements);
  uint32_t nbytes = static_cast<uint32_t>(scaled.size() * sizeof(double));
  if (GPMF_OK != GPMF_ScaledData(ms, scaled.data(), nbytes, 0, samples,
                                 GPMF_TYPE_DOUBLE)) {
    return;
  }
  for (uint32_t i = 0; i < samples; ++i) {
    GPSSample gps{};
    gps.lat = scaled[i * elements + 0];
    gps.lon = scaled[i * elements + 1];
    gps.altitude = scaled[i * elements + 2];
    gps.ground_speed = scaled[i * elements + 3]; // 2D speed
    gps.full_speed = scaled[i * elements + 4];   // 3D speed
    // Fix time from days-since-2000 + seconds-since-midnight.
    double days_since_2000 = scaled[i * elements + 5];
    double secs_since_midnight = scaled[i * elements + 6];
    constexpr int64_t epoch_2000 = 946684800000LL; // 2000-01-01T00:00:00Z, ms
    int64_t ms_since_2000 = static_cast<int64_t>(days_since_2000 * 86400000.0 +
                                                 secs_since_midnight * 1000.0);
    gps.timestamp_ms = epoch_2000 + ms_since_2000;
    if (elements > 7) {
      gps.dop = scaled[i * elements + 7];
    }
    if (elements > 8) {
      gps.fix = static_cast<int>(scaled[i * elements + 8]);
    }
    emit(gps, i, samples);
  }
}

// Decode one GPS5 payload and emit every fix. `timestamp` is the GPSU time the
// caller resolved alongside it. GPS5 layout: [lat, lon, alt, 2D speed, 3D
// speed]; it carries no DOP / fix, so those keep the GPSSample sentinels. The
// fields are read by explicit index (stride = the struct's element width) so 2D
// speed lands in ground_speed and 3D speed in full_speed, matching the GPS9
// branch.
void ParseGPS5(GPMF_stream *ms, uint32_t samples, uint32_t elements,
               int64_t timestamp, const SampleEmit &emit) {
  if (!(samples && elements >= 5)) {
    return;
  }
  std::vector<double> scaled(static_cast<size_t>(samples) * elements);
  uint32_t nbytes = static_cast<uint32_t>(scaled.size() * sizeof(double));
  if (GPMF_OK != GPMF_ScaledData(ms, scaled.data(), nbytes, 0, samples,
                                 GPMF_TYPE_DOUBLE)) {
    return;
  }
  for (uint32_t i = 0; i < samples; ++i) {
    GPSSample gps{};
    gps.lat = scaled[i * elements + 0];
    gps.lon = scaled[i * elements + 1];
    gps.altitude = scaled[i * elements + 2];
    gps.ground_speed = scaled[i * elements + 3]; // 2D speed
    gps.full_speed = scaled[i * elements + 4];   // 3D speed
    gps.timestamp_ms = timestamp;
    emit(gps, i, samples);
  }
}

} // namespace

uint32_t GPMFSource::ReadSamples(
    std::function<void(GPSSample, uint32_t, uint32_t)> on_sample) {
  uint32_t psize = GetPayloadSize(mp4handle_, index_);
  payload_res_ = GetPayloadResource(mp4handle_, payload_res_, psize);
  uint32_t *payload = GetPayload(mp4handle_, payload_res_, index_);

  if (payload == nullptr) {
    // Empty / past-the-end index. Return the code quietly — the iteration
    // protocol (IsEnd/Next) handles empties, and this is the chapter-seam hot
    // path, so a per-seam log line would be pure noise. GPMF_ERROR_MEMORY (==
    // 1) is the parser's own code for GetPayload() == nullptr.
    return GPMF_ERROR_MEMORY;
  }

  GPMF_stream metadata_stream, *ms = &metadata_stream;
  auto ret = GPMF_Init(ms, payload, psize);
  if (ret != GPMF_OK) {
    // A corrupt payload is genuinely abnormal (unlike an empty one), so report
    // it on stderr — never on stdout, which may be piped.
    fprintf(stderr, "pacer: GPMF_Init failed for payload %u (corrupt GPMF?)\n",
            index_);
    return ret;
  }

  while (GPMF_OK ==
         GPMF_FindNext(ms, STR2FOURCC("STRM"),
                       GPMF_LEVELS(GPMF_RECURSE_LEVELS | GPMF_TOLERANT))) {

    ret = GPMF_SeekToSamples(ms);
    if (ret != GPMF_OK) {
      // A sample-less STRM is unremarkable; just surface the code.
      return ret;
    }

    uint32_t key = GPMF_Key(ms);
    uint32_t samples = GPMF_Repeat(ms);
    uint32_t elements = GPMF_ElementsInStruct(ms);
    const SampleEmit &emit = on_sample;

    if (key == STR2FOURCC("GPS9")) {
      ParseGPS9(ms, samples, elements, emit);
    }

    if (key == STR2FOURCC("GPS5")) {
      // GPS5 has no per-sample fix time; its timestamp lives in a sibling GPSU
      // entry at the same level. NOTE: GPS5 timing is vestigial — we keep only
      // the LAST GPSU value and stamp every sample in the payload with it. GPS9
      // (the live path) carries a true per-sample time.
      int64_t timestamp = 0;
      GPMF_stream gpsu_stream;
      GPMF_CopyState(ms, &gpsu_stream);
      if (GPMF_OK ==
          GPMF_FindPrev(&gpsu_stream, STR2FOURCC("GPSU"),
                        GPMF_LEVELS(GPMF_CURRENT_LEVEL | GPMF_TOLERANT))) {
        char *gpsu_data = (char *)GPMF_RawData(&gpsu_stream);
        uint32_t gpsu_size =
            GPMF_Repeat(&gpsu_stream) * GPMF_ElementsInStruct(&gpsu_stream);
        for (uint32_t k = 0; k < gpsu_size; k += 16) {
          GPSUData t;
          memcpy(t.data, gpsu_data + k, 16);
          timestamp = t.Timestamp();
        }
      }

      ParseGPS5(ms, samples, elements, timestamp, emit);
    }
  }

  return ret;
}

void GPMFSource::ReadStream(
    uint32_t fourcc,
    const std::function<void(const double *, uint32_t, double)> &emit) const {
  // Walk one stream (ACCL / GRAV / CORI) across EVERY payload, timestamping
  // each sample on the media clock. A payload spans [span_in, span_out]
  // (GetPayloadTime, same clock as the GPS spans), and its `samples` rows are
  // spread evenly across that span, matching the GPS / video sync. nelem is
  // clamped to 4 (vec3 for ACCL/GRAV, quaternion for CORI).
  uint32_t payloads = GetNumberPayloads(mp4handle_);
  for (uint32_t i = 0; i < payloads; ++i) {
    uint32_t psize = GetPayloadSize(mp4handle_, i);
    payload_res_ = GetPayloadResource(mp4handle_, payload_res_, psize);
    uint32_t *payload = GetPayload(mp4handle_, payload_res_, i);
    if (payload == nullptr) {
      continue;
    }
    double span_in = 0, span_out = 0;
    if (GetPayloadTime(mp4handle_, i, &span_in, &span_out) != GPMF_OK) {
      continue;
    }
    GPMF_stream metadata_stream, *ms = &metadata_stream;
    if (GPMF_Init(ms, payload, psize) != GPMF_OK) {
      continue;
    }
    if (GPMF_OK ==
        GPMF_FindNext(ms, fourcc,
                      GPMF_LEVELS(GPMF_RECURSE_LEVELS | GPMF_TOLERANT))) {
      uint32_t samples = GPMF_Repeat(ms);
      uint32_t elements = GPMF_ElementsInStruct(ms);
      if (samples == 0 || elements == 0) {
        continue;
      }
      std::vector<double> scaled(static_cast<size_t>(samples) * elements);
      uint32_t nbytes = static_cast<uint32_t>(scaled.size() * sizeof(double));
      if (GPMF_OK == GPMF_ScaledData(ms, scaled.data(), nbytes, 0, samples,
                                     GPMF_TYPE_DOUBLE)) {
        for (uint32_t s = 0; s < samples; ++s) {
          double t = span_in +
                     (span_out - span_in) * (static_cast<double>(s) / samples);
          double vals[4] = {0, 0, 0, 0};
          uint32_t nelem = elements > 4 ? 4 : elements;
          for (uint32_t e = 0; e < nelem; ++e) {
            vals[e] = scaled[s * elements + e];
          }
          emit(vals, nelem, t);
        }
      }
    }
  }
}

void GPMFSource::ReadAccl(std::function<void(IMUSample)> on_sample) {
  ReadStream(STR2FOURCC("ACCL"), [&](const double *v, uint32_t, double t) {
    on_sample(IMUSample{.x = v[0], .y = v[1], .z = v[2], .time = t});
  });
}

void GPMFSource::ReadGrav(std::function<void(IMUSample)> on_sample) {
  ReadStream(STR2FOURCC("GRAV"), [&](const double *v, uint32_t, double t) {
    on_sample(IMUSample{.x = v[0], .y = v[1], .z = v[2], .time = t});
  });
}

void GPMFSource::ReadCori(std::function<void(QuatSample)> on_sample) {
  ReadStream(STR2FOURCC("CORI"), [&](const double *v, uint32_t, double t) {
    on_sample(
        QuatSample{.w = v[0], .x = v[1], .y = v[2], .z = v[3], .time = t});
  });
}

// The three IMU readers just name the matching reader verb; the shifting logic
// (offset the right chapter by the left's duration) lives in ReadShifted.
void SequentialGPSSource::ReadAccl(std::function<void(IMUSample)> on_sample) {
  ReadShifted<IMUSample>(&RawGPSSource::ReadAccl, on_sample);
}

void SequentialGPSSource::ReadGrav(std::function<void(IMUSample)> on_sample) {
  ReadShifted<IMUSample>(&RawGPSSource::ReadGrav, on_sample);
}

void SequentialGPSSource::ReadCori(std::function<void(QuatSample)> on_sample) {
  ReadShifted<QuatSample>(&RawGPSSource::ReadCori, on_sample);
}

uint32_t SequentialGPSSource::ReadSamples(
    std::function<void(GPSSample, uint32_t, uint32_t)> on_sample) {
  return current_->ReadSamples(std::move(on_sample));
}

bool SequentialGPSSource::IsEnd() {
  return current_ == right_ && current_->IsEnd();
}

double SequentialGPSSource::GetTotalDuration() const {
  return left_->GetTotalDuration() + right_->GetTotalDuration();
}

uint32_t SequentialGPSSource::Seek(double target) {
  if (auto left_duration = left_->GetTotalDuration(); target < left_duration) {
    if (current_ == right_)
      right_->Seek(0);
    return (current_ = left_)->Seek(target);
  } else {
    // Reuse left_duration from the if-init rather than recomputing it — for a
    // nested SequentialGPSSource that recomputation re-walks the whole left
    // subtree. Same value, one traversal.
    target -= left_duration;
    return (current_ = right_)->Seek(target);
  }
}

void SequentialGPSSource::Next() {
  // At the left->right seam, position `right_` at its first payload and return
  // without advancing. Switching to a fresh `right_` (index 0) and then calling
  // Next() would skip the first payload of every chapter after the first.
  if (current_ == left_ && current_->IsEnd()) {
    current_ = right_;
    current_->Seek(0);
    return;
  }
  return current_->Next();
}

auto SequentialGPSSource::CurrentTimeSpan() const -> std::pair<double, double> {
  auto [start, end] = current_->CurrentTimeSpan();
  if (current_ == right_) {
    auto left_len = left_->GetTotalDuration();
    return {start + left_len, end + left_len};
  }
  return {start, end};
}

// Base default: a source with no GPS payload emits nothing. GPMFSource /
// SequentialGPSSource (or a Python subclass through the trampoline) override.
uint32_t RawGPSSource::ReadSamples(
    std::function<void(GPSSample, uint32_t, uint32_t)> /*on_sample*/) {
  return 0;
}

} // namespace pacer
