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
  // Free the GPMF payload resource we own (allocated lazily, reused across calls). Must happen
  // before CloseSource. Previously every ReadSamples()/ReadStream() call leaked a fresh resource.
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
  // `index_` is unsigned, so a `--index_` at index 0 wraps to UINT32_MAX (a bogus, EOF-looking
  // index). Initialise in/out so the post-loop guard never reads garbage when the very first
  // GetPayloadTime fails, and only step `index_` back when the last lookup SUCCEEDED and we are
  // not already at 0. Net effect: a seek to before the first payload clamps to index 0 instead
  // of wrapping past the end.
  double in = 0, out = 0;
  uint32_t ret = GPMF_OK;
  do {
    ret = GetPayloadTime(mp4handle_, index_, &in, &out);
    if (ret == GPMF_OK) {
      if (out <= target)
        ++index_;
      // Step back toward the covering payload, but never below 0. At index 0 we are already
      // clamped, so stop the loop instead of spinning (the old code let `--index_` wrap to
      // UINT32_MAX here, after which GetPayloadTime failed and broke the loop).
      if (target < in) {
        if (index_ == 0)
          break;
        --index_;
      }
    }
  } while (ret == GPMF_OK && in < out && ((target > out) || (target < in)));
  if (ret == GPMF_OK && index_ && !(in < out)) {
    --index_;
  }

  return ret;
}

void GPMFSource::Next() { ++index_; }

bool GPMFSource::IsEnd() {
  double in = 0, out = 0;
  if (GetPayloadTime(mp4handle_, index_, &in, &out) != GPMF_OK) {
    return true;
  }
  return in + 1e-9 >= out;
}

std::pair<double, double> GPMFSource::CurrentTimeSpan() const {
  double in = 0, out = 0;
  // No payload at index_ -> report an empty span rather than returning uninitialised stack.
  if (GetPayloadTime(mp4handle_, index_, &in, &out) != GPMF_OK) {
    return {0, 0};
  }
  return {in, out};
}

double GPMFSource::GetTotalDuration() const { return GetDuration(mp4handle_); }

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

    // https://howardhinnant.github.io/date_algorithms.html
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

// Callback contract identical to RawGPSSource::ReadSamples: (sample, current_index,
// total_records). Passed by reference into the per-codec parsers below.
using SampleEmit = std::function<void(GPSSample, uint32_t, uint32_t)>;

// Parse one GPS9 STRM payload (already positioned at samples by GPMF_SeekToSamples) and emit
// every fix. GPS9 element order: [lat, lon, alt, 2D speed, 3D speed, days since 2000, secs
// since midnight (ms precision), DOP, fix (0/2/3)]. Indices 3-6 (speeds + timestamp) are read
// unconditionally; a struct narrower than 7 elements would read past the row, so the caller
// guards `elements >= 7`. DOP(7)/fix(8) are separately guarded and otherwise keep the
// GPSSample sentinel defaults. Byte-identical to the former inline GPS9 branch.
void ParseGPS9(GPMF_stream *ms, uint32_t samples, uint32_t elements,
               const SampleEmit &emit) {
  if (!(samples && elements >= 7)) {
    return;
  }
  // RAII scratch buffer (was a malloc/free pair). The malloc version leaked whenever `emit`
  // threw mid-loop (e.g. a Python callback raising through the binding trampoline) — the
  // vector unwinds cleanly. Same size, same layout, same reads.
  std::vector<double> tmpbuffer(static_cast<size_t>(samples) * elements);
  uint32_t buffersize =
      static_cast<uint32_t>(tmpbuffer.size() * sizeof(double));
  if (GPMF_OK != GPMF_ScaledData(ms, tmpbuffer.data(), buffersize, 0, samples,
                                 GPMF_TYPE_DOUBLE)) {
    return;
  }
  for (uint32_t i = 0; i < samples; ++i) {
    GPSSample gps{};
    // GPS9: [lat, lon, alt, 2D speed, 3D speed, days since 2000, secs
    // since midnight, DOP, fix]
    gps.lat = tmpbuffer[i * elements + 0];
    gps.lon = tmpbuffer[i * elements + 1];
    gps.altitude = tmpbuffer[i * elements + 2];
    gps.ground_speed = tmpbuffer[i * elements + 3];
    gps.full_speed = tmpbuffer[i * elements + 4];
    // Timestamp calculation from days since 2000 and seconds since
    // midnight
    double days_since_2000 = tmpbuffer[i * elements + 5];
    double secs_since_midnight = tmpbuffer[i * elements + 6];
    // Convert days since 2000-01-01 to ms since epoch
    constexpr int64_t epoch_2000 =
        946684800000LL; // ms since epoch for 2000-01-01T00:00:00Z
    int64_t ms_since_2000 = static_cast<int64_t>(
        days_since_2000 * 86400000.0 + secs_since_midnight * 1000.0);
    gps.timestamp_ms = epoch_2000 + ms_since_2000;
    // GPS9 quality: DOP (element 7) and fix type (element 8, 0/2/3). Present only
    // when the struct actually carries them; otherwise keep the sentinel defaults.
    if (elements > 7) {
      gps.dop = tmpbuffer[i * elements + 7];
    }
    if (elements > 8) {
      gps.fix = static_cast<int>(tmpbuffer[i * elements + 8]);
    }
    emit(gps, i, samples);
  }
}

// Parse one GPS5 STRM payload (already positioned at samples) and emit every fix. `ms` is the
// GPS5 stream; `timestamp` is the (vestigial) GPSU time the caller resolved at the same level.
// GPS5 element order: [lat, lon, alt, 2D speed, 3D speed]; carries no DOP/fix, so those keep
// the GPSSample sentinels. Byte-identical to the former inline GPS5 branch.
void ParseGPS5(GPMF_stream *ms, uint32_t samples, uint32_t elements,
               int64_t timestamp, const SampleEmit &emit) {
  // GPS5 element order is [lat, lon, alt, 2D speed, 3D speed]. Skip a malformed payload
  // whose struct is too narrow to carry the five fields (guards the explicit reads below).
  if (!(samples && elements >= 5)) {
    return;
  }
  // RAII scratch buffer (was a malloc/free pair) — see ParseGPS9; the malloc version leaked
  // when `emit` threw mid-loop.
  std::vector<double> tmpbuffer(static_cast<size_t>(samples) * elements);
  uint32_t buffersize =
      static_cast<uint32_t>(tmpbuffer.size() * sizeof(double));
  if (GPMF_OK != GPMF_ScaledData(ms, tmpbuffer.data(), buffersize, 0, samples,
                                 GPMF_TYPE_DOUBLE)) {
    return;
  }
  for (uint32_t i = 0; i < samples; ++i) {
    // Stride is the KNOWN element width (`elements`), not the UNITS-stream repeat:
    // when no SI_UNITS/UNITS sibling exists the old code's stride collapsed to 1 and
    // emitted 5 garbage samples per record. Write the fields EXPLICITLY (same as the
    // GPS9 branch) so 2D speed -> ground_speed and 3D speed -> full_speed; the prior
    // union field-order aliasing landed them swapped (2D in full_speed) versus GPS9.
    GPSSample gps{};
    gps.lat = tmpbuffer[i * elements + 0];
    gps.lon = tmpbuffer[i * elements + 1];
    gps.altitude = tmpbuffer[i * elements + 2];
    gps.ground_speed = tmpbuffer[i * elements + 3]; // 2D speed
    gps.full_speed = tmpbuffer[i * elements + 4];   // 3D speed
    gps.timestamp_ms = timestamp;
    // GPS5 carries no DOP / fix-type; leave the GPSSample "unknown" sentinels
    // (dop = -1, fix = -1) as defaulted in the struct.
    emit(gps, i, samples);
  }
}

} // namespace

uint32_t GPMFSource::ReadSamples(
    std::function<void(GPSSample, uint32_t, uint32_t)> on_sample) {
  uint32_t payloadsize = GetPayloadSize(mp4handle_, index_);
  // Reuse the owned payload resource (grows in place); freed once in the destructor.
  payload_res_ = GetPayloadResource(mp4handle_, payload_res_, payloadsize);
  uint32_t *payload = GetPayload(mp4handle_, payload_res_, index_);

  if (payload == nullptr) {
    // No payload for this index (empty/EOF chunk). Return the error code silently — the iteration
    // protocol (is_end()/next()) already handles the empty case, and this is on the chapter-seam
    // hot path where a per-seam "No payload" printf is pure console noise.
    // GPMF_ERROR_MEMORY (== 1, "NULL Pointer") is the parser's own code for GetPayload
    // returning nullptr — same value the old literal `return 1` produced.
    return GPMF_ERROR_MEMORY;
  }

  GPMF_stream metadata_stream, *ms = &metadata_stream;
  auto ret = GPMF_Init(ms, payload, payloadsize);
  if (ret != GPMF_OK) {
    // Genuinely diagnostic (a corrupt/unparsable GPMF payload is NOT part of normal
    // iteration, unlike the empty-payload case above) — say what failed and where, on
    // stderr so it never pollutes piped stdout.
    fprintf(stderr, "pacer: GPMF_Init failed for payload %u (corrupt GPMF?)\n",
            index_);
    return ret;
  }

  while (GPMF_OK ==
         GPMF_FindNext(ms, STR2FOURCC("STRM"),
                       GPMF_LEVELS(GPMF_RECURSE_LEVELS | GPMF_TOLERANT))) {

    ret = GPMF_SeekToSamples(ms);
    if (ret != GPMF_OK) {
      // A sample-less STRM is unremarkable (the caller just sees the nonzero code); the old
      // per-occurrence "No Seek to Samples" printf carried zero information and could spam
      // the console once per payload on sources that hit it. Dropped, behavior unchanged.
      return ret;
    }

    char *rawdata = (char *)GPMF_RawData(ms);
    uint32_t key = GPMF_Key(ms);
    GPMF_SampleType type = GPMF_Type(ms);
    uint32_t samples = GPMF_Repeat(ms);
    uint32_t elements = GPMF_ElementsInStruct(ms);

    // Extract GPSU data from GPS5 stream
    // To do this, you need to search for the GPSU key at the same level as
    // GPS5. Typically, GPSU contains a timestamp for each GPS5 sample. You can
    // use GPMF_FindPrev or GPMF_FindNext to locate GPSU.

    // Example: Find GPSU at the same level as GPS5
    GPMF_stream gpsu_stream;

    const SampleEmit &emit = on_sample;

    if (key == STR2FOURCC("GPS9")) {
      // lat, long, alt, 2D speed, 3D speed, days since 2000, secs since
      // midnight (ms precision), DOP, fix (0, 2D or 3D)
      ParseGPS9(ms, samples, elements, emit);
    }

    if (key == STR2FOURCC("GPS5")) {
      int64_t timestamp = 0;
      GPMF_CopyState(ms, &gpsu_stream);
      if (GPMF_OK ==
          GPMF_FindPrev(&gpsu_stream, STR2FOURCC("GPSU"),
                        GPMF_LEVELS(GPMF_CURRENT_LEVEL | GPMF_TOLERANT))) {
        char *gpsu_data = (char *)GPMF_RawData(&gpsu_stream);
        uint32_t gpsu_size =
            GPMF_Repeat(&gpsu_stream) * GPMF_ElementsInStruct(&gpsu_stream);

        // GPSU is usually a 16-byte ASCII timestamp per sample, e.g.
        // "2017-01-01 12:34:56.000" If there are multiple samples, gpsu_data
        // contains concatenated timestamps.
        // TODO: GPS5 per-sample timing is VESTIGIAL — this loop keeps only the LAST GPSU
        // timestamp and stamps every sample in the payload with it. GPS9 (the live path)
        // carries a true per-sample fix time; GPS5 timing is not reworked in this batch.
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

// Reads one GPMF stream (e.g. ACCL/GRAV/CORI) over EVERY payload of this MP4, emitting each
// sample with a media-clock timestamp. The timing mirrors the research dump_imu.c: a payload
// covers the media span [in,out] (from GetPayloadTime, the same clock as the GPS payload
// spans), and the `samples` rows in it are spread evenly as in + (out-in)*(s/samples). This
// keeps the IMU on the identical media clock the GPS / video sync uses. nelem is clamped to 4
// (ACCL/GRAV are vec3, CORI is a quaternion).
void GPMFSource::ReadStream(
    uint32_t fourcc,
    const std::function<void(const double *, uint32_t, double)> &emit) const {
  uint32_t payloads = GetNumberPayloads(mp4handle_);
  // Reuse the owned payload resource (shared with ReadSamples(); grows in place); freed in dtor.
  for (uint32_t i = 0; i < payloads; ++i) {
    uint32_t payloadsize = GetPayloadSize(mp4handle_, i);
    payload_res_ = GetPayloadResource(mp4handle_, payload_res_, payloadsize);
    uint32_t *payload = GetPayload(mp4handle_, payload_res_, i);
    if (payload == nullptr) {
      continue;
    }
    double in = 0, out = 0;
    if (GetPayloadTime(mp4handle_, i, &in, &out) != GPMF_OK) {
      continue;
    }
    GPMF_stream metadata_stream, *ms = &metadata_stream;
    if (GPMF_Init(ms, payload, payloadsize) != GPMF_OK) {
      continue;
    }
    if (GPMF_OK == GPMF_FindNext(ms, fourcc,
                                 GPMF_LEVELS(GPMF_RECURSE_LEVELS |
                                             GPMF_TOLERANT))) {
      uint32_t samples = GPMF_Repeat(ms);
      uint32_t elements = GPMF_ElementsInStruct(ms);
      if (samples == 0 || elements == 0) {
        continue;
      }
      // RAII scratch buffer (was a malloc/free pair) — see ParseGPS9; the malloc version
      // leaked when `emit` threw mid-loop.
      std::vector<double> tmpbuffer(static_cast<size_t>(samples) * elements);
      uint32_t buffersize =
          static_cast<uint32_t>(tmpbuffer.size() * sizeof(double));
      if (GPMF_OK == GPMF_ScaledData(ms, tmpbuffer.data(), buffersize, 0,
                                     samples, GPMF_TYPE_DOUBLE)) {
        for (uint32_t s = 0; s < samples; ++s) {
          double t = in + (out - in) * (static_cast<double>(s) / samples);
          double vals[4] = {0, 0, 0, 0};
          uint32_t nelem = elements > 4 ? 4 : elements;
          for (uint32_t e = 0; e < nelem; ++e) {
            vals[e] = tmpbuffer[s * elements + e];
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
    on_sample(QuatSample{.w = v[0], .x = v[1], .y = v[2], .z = v[3], .time = t});
  });
}

// Multi-chapter: read each child's stream, shifting later chapters by the cumulative duration
// of the earlier ones (the same global media clock the GPS spans use). The shared per-stream
// logic lives in the ReadShifted template; these three just bind the matching reader verb.
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
    // Reuse left_duration (in scope from the if-init) rather than recomputing
    // left_->GetTotalDuration(); for a nested SequentialGPSSource that recomputation walked
    // the whole left subtree again. Same value, one traversal.
    target -= left_duration;
    return (current_ = right_)->Seek(target);
  }
}
void SequentialGPSSource::Next() {
  // At the left->right chapter seam, position `right_` AT its first payload and return without
  // advancing. The old code switched to `right_` (fresh at index 0) and then immediately called
  // Next() (++index_), silently dropping the first payload of every chapter after the first.
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
// Base default: a source with no GPS payload emits nothing. Concrete sources (GPMFSource /
// SequentialGPSSource — or a Python subclass via the binding trampoline) override.
uint32_t RawGPSSource::ReadSamples(
    std::function<void(GPSSample, uint32_t, uint32_t)> /*on_sample*/) {
  return 0;
}
} // namespace pacer
