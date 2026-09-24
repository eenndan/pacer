#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <functional>
#include <initializer_list>
#include <iterator>
#include <string>
#include <utility>
#include <vector>

#include <unistd.h> // mkdtemp

#include <pacer/datatypes/datatypes.hpp>
#include <pacer/gps-source/gps-source.hpp>

using pacer::GPMFSource;
using pacer::GPSSample;
using pacer::RawGPSSource;
using pacer::SequentialGPSSource;

namespace {

// An in-memory RawGPSSource that models a real GPMF chapter: a list of
// "payloads", each with a [in,out] media span and exactly ONE GPSSample (its
// .lat tags which payload it came from). It reproduces the GPMFSource control
// protocol the studio iterates with:
//
//     src.Seek(0);
//     while (!src.IsEnd()) {
//       auto [a,b] = src.CurrentTimeSpan();
//       src.ReadSamples(...);        // emits the current payload's sample
//       src.Next();
//     }
//
// IsEnd() is true once index_ has walked past the last payload, mirroring
// GPMFSource (whose IsEnd reports true when GetPayloadTime fails / the span is
// empty at the end).
class StubSource : public RawGPSSource {
public:
  struct Payload {
    double in, out;
    GPSSample sample;
  };
  explicit StubSource(std::vector<Payload> payloads)
      : payloads_(std::move(payloads)) {}

  uint32_t ReadSamples(
      std::function<void(GPSSample, uint32_t, uint32_t)> on_sample) override {
    if (index_ >= payloads_.size()) {
      return 1; // nothing at this index (matches GPMFSource "No payload")
    }
    on_sample(payloads_[index_].sample, 0, 1);
    return 0;
  }

  uint32_t Seek(double target) override {
    // Clamp to the first payload covering target; before the first payload ->
    // index 0.
    index_ = 0;
    for (size_t i = 0; i < payloads_.size(); ++i) {
      if (target < payloads_[i].out) {
        index_ = i;
        return 0;
      }
    }
    if (!payloads_.empty()) {
      index_ = payloads_.size() - 1; // past the end -> last payload
    }
    return 0;
  }

  void Next() override { ++index_; }

  bool IsEnd() override { return index_ >= payloads_.size(); }

  std::pair<double, double> CurrentTimeSpan() const override {
    if (index_ >= payloads_.size()) {
      return {0, 0};
    }
    return {payloads_[index_].in, payloads_[index_].out};
  }

  double GetTotalDuration() const override {
    return payloads_.empty() ? 0.0 : payloads_.back().out;
  }

private:
  std::vector<StubSource::Payload> payloads_;
  size_t index_ = 0;
};

// Drive a source through the same protocol _read_gpmf uses, collecting
// (global_span, sample) for every payload. This is the exact loop where the
// chapter-seam skip bug manifested.
std::vector<std::pair<std::pair<double, double>, GPSSample>>
CollectAll(RawGPSSource &src) {
  std::vector<std::pair<std::pair<double, double>, GPSSample>> out;
  src.Seek(0);
  while (!src.IsEnd()) {
    auto span = src.CurrentTimeSpan();
    src.ReadSamples(
        [&](GPSSample s, uint32_t, uint32_t) { out.emplace_back(span, s); });
    src.Next();
  }
  return out;
}

GPSSample MakeSample(double tag) {
  GPSSample s{};
  s.lat = tag; // tag which payload this came from
  return s;
}

} // namespace

TEST_CASE(
    "SequentialGPSSource does not drop the first payload of the second chapter",
    "[gps-source][seam]") {
  // Two chapters, 3 payloads each. Chapter 1 spans [0,3); chapter 2 spans [0,3)
  // LOCALLY and is shifted by chapter 1's duration (3.0) on the global axis the
  // SequentialGPSSource reports.
  StubSource left({
      {0.0, 1.0, MakeSample(10)},
      {1.0, 2.0, MakeSample(11)},
      {2.0, 3.0, MakeSample(12)},
  });
  StubSource right({
      {0.0, 1.0,
       MakeSample(20)}, // <-- the payload that USED TO BE SKIPPED at the seam
      {1.0, 2.0, MakeSample(21)},
      {2.0, 3.0, MakeSample(22)},
  });
  SequentialGPSSource seq(&left, &right);

  auto got = CollectAll(seq);

  // Every payload from BOTH chapters must appear: 3 + 3 = 6, none dropped at
  // the boundary.
  REQUIRE(got.size() == 6);

  // The tags come out in order, with the second chapter's FIRST payload (20)
  // present.
  std::vector<double> tags;
  for (auto &g : got) {
    tags.push_back(g.second.lat);
  }
  REQUIRE(tags == std::vector<double>{10, 11, 12, 20, 21, 22});

  // Chapter 2's spans are offset by chapter 1's total duration (3.0). The first
  // sample of chapter 2 (tag 20) sits at global [3,4), i.e. CONTINUOUS with
  // chapter 1's last span [2,3).
  const auto &seam = got[3]; // first sample after the boundary
  REQUIRE(seam.second.lat == 20);
  REQUIRE(seam.first.first == Catch::Approx(3.0));
  REQUIRE(seam.first.second == Catch::Approx(4.0));

  // The spans are monotonic and gap-free across the whole session (no jump at
  // the seam).
  for (size_t i = 1; i < got.size(); ++i) {
    REQUIRE(got[i].first.first == Catch::Approx(got[i - 1].first.second));
  }
}

TEST_CASE("SequentialGPSSource total duration sums the chapters",
          "[gps-source][seam]") {
  StubSource left({{0.0, 3.0, MakeSample(1)}});
  StubSource right({{0.0, 2.0, MakeSample(2)}});
  SequentialGPSSource seq(&left, &right);
  REQUIRE(seq.GetTotalDuration() == Catch::Approx(5.0));
}

TEST_CASE("SequentialGPSSource chains three chapters with no payload dropped "
          "at either seam",
          "[gps-source][seam]") {
  // Three chapters folded LEFT-leaning exactly like studio/ingest.chain_sources
  // does:
  //     head = SequentialGPSSource(SequentialGPSSource(A, B), C)
  // so the bug surface is two seams (A->B inside the inner node, then inner->C
  // at the outer one) and the offset for C must be the CUMULATIVE duration of
  // A+B (the inner subtree), proving the shift recurses through a nested
  // SequentialGPSSource rather than only handling one boundary.
  //
  // Distinct, unequal durations make the offsets unambiguous:
  //   A: dur 2.0   B: dur 3.0   C: dur 3.0     => inner(A,B) dur = 5.0
  StubSource a({
      {0.0, 1.0, MakeSample(10)},
      {1.0, 2.0, MakeSample(11)},
  });
  StubSource b({
      {0.0, 1.5, MakeSample(20)}, // first payload of chapter 2 — dropped at the
                                  // seam by the old code
      {1.5, 3.0, MakeSample(21)},
  });
  StubSource c({
      {0.0, 1.0,
       MakeSample(30)}, // first payload of chapter 3 — the SECOND seam
      {1.0, 2.0, MakeSample(31)},
      {2.0, 3.0, MakeSample(32)},
  });
  SequentialGPSSource inner(&a, &b);
  SequentialGPSSource head(&inner, &c);

  // Total duration sums all three chapters (and the nested-node duration
  // composes correctly).
  REQUIRE(inner.GetTotalDuration() == Catch::Approx(5.0));
  REQUIRE(head.GetTotalDuration() == Catch::Approx(8.0)); // 2 + 3 + 3

  auto got = CollectAll(head);

  // Every payload from ALL THREE chapters appears: 2 + 2 + 3 = 7, none dropped
  // at either seam.
  REQUIRE(got.size() == 7);

  // Tags come out in chapter order, with BOTH post-seam first payloads (20 and
  // 30) present.
  std::vector<double> tags;
  for (auto &g : got) {
    tags.push_back(g.second.lat);
  }
  REQUIRE(tags == std::vector<double>{10, 11, 20, 21, 30, 31, 32});

  // Global spans: A unshifted; B shifted by A's dur (2.0); C shifted by inner's
  // dur (A+B = 5.0).
  const std::vector<std::pair<double, double>> expected_spans{
      {0.0, 1.0}, {1.0, 2.0}, // chapter A
      {2.0, 3.5}, {3.5, 5.0}, // chapter B  (+2.0)
      {5.0, 6.0}, {6.0, 7.0},
      {7.0, 8.0}, // chapter C  (+5.0, the CUMULATIVE A+B offset)
  };
  for (size_t i = 0; i < got.size(); ++i) {
    REQUIRE(got[i].first.first == Catch::Approx(expected_spans[i].first));
    REQUIRE(got[i].first.second == Catch::Approx(expected_spans[i].second));
  }

  // Spans are monotonic and gap-free across the WHOLE three-chapter session (no
  // jump at A->B at global 2.0, none at B->C at global 5.0).
  for (size_t i = 1; i < got.size(); ++i) {
    REQUIRE(got[i].first.first == Catch::Approx(got[i - 1].first.second));
  }

  // Seek lands in the right chapter on both sides of each seam, and IsEnd is
  // false mid-stream. Just inside chapter A:
  REQUIRE(head.Seek(0.5) == 0);
  REQUIRE_FALSE(head.IsEnd());
  REQUIRE(head.CurrentTimeSpan().first == Catch::Approx(0.0));
  // Straddling the FIRST seam (global 2.0 is chapter B's first payload
  // [2.0,3.5)):
  REQUIRE(head.Seek(2.5) == 0);
  REQUIRE_FALSE(head.IsEnd());
  REQUIRE(head.CurrentTimeSpan().first == Catch::Approx(2.0));
  REQUIRE(head.CurrentTimeSpan().second == Catch::Approx(3.5));
  // Straddling the SECOND seam (global 5.0 is chapter C's first payload
  // [5.0,6.0)):
  REQUIRE(head.Seek(5.5) == 0);
  REQUIRE_FALSE(head.IsEnd());
  REQUIRE(head.CurrentTimeSpan().first == Catch::Approx(5.0));
  REQUIRE(head.CurrentTimeSpan().second == Catch::Approx(6.0));

  // Seeking at/after the end of the last chapter parks on the final payload and
  // reports end only after the iteration has walked past it (the CollectAll
  // loop above already drained to IsEnd).
  REQUIRE(head.Seek(8.0) == 0);
  REQUIRE(head.CurrentTimeSpan().first == Catch::Approx(7.0));
  head.Next();
  REQUIRE(head.IsEnd()); // walked past the last payload of the last chapter
}

TEST_CASE("StubSource Seek-before-first-payload clamps to index 0 (no wrap / "
          "false EOF)",
          "[gps-source][seek]") {
  // Mirrors the GPMFSource::Seek fix at the protocol level: seeking to a target
  // BEFORE the first payload must land on payload 0 and NOT report
  // end-of-stream. (GPMFSource's unsigned index_ used to wrap to UINT32_MAX
  // here; this asserts the intended clamped behaviour the C++ Seek now
  // implements. The on-real-file underflow is exercised by the Python suite
  // against the actual GPMF source.)
  StubSource src({
      {1.0, 2.0, MakeSample(100)},
      {2.0, 3.0, MakeSample(101)},
  });
  REQUIRE(src.Seek(-5.0) == 0);
  REQUIRE_FALSE(src.IsEnd());
  auto [in, out] = src.CurrentTimeSpan();
  REQUIRE(in == Catch::Approx(1.0));
  REQUIRE(out == Catch::Approx(2.0));
}

// ---------------------------------------------------------------------------
// Hostile input: a mutated COPY of a bundled GoPro clip, made at run time in a
// fresh mkdtemp directory (never the clip itself). The two defects below were
// found by the 2026-09 board review's mutation sweep over hero8.mp4.
namespace {

std::vector<char> ReadAll(const std::string &path) {
  std::ifstream in(path, std::ios::binary);
  return {std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>()};
}

uint32_t Be32(const std::vector<char> &b, size_t at) {
  uint32_t v = 0;
  for (size_t i = 0; i < 4; ++i) {
    v = (v << 8) | static_cast<unsigned char>(b[at + i]);
  }
  return v;
}

void PutBe32(std::vector<char> &b, size_t at, uint32_t v) {
  for (size_t i = 0; i < 4; ++i) {
    b[at + i] = static_cast<char>((v >> (8 * (3 - i))) & 0xFF);
  }
}

struct Box {
  size_t off, size, hdr;
  std::string type;
};

// The boxes directly inside [begin, end) of an ISO-BMFF (MP4) file.
std::vector<Box> Children(const std::vector<char> &b, size_t begin,
                          size_t end) {
  std::vector<Box> out;
  for (size_t off = begin; off + 8 <= end;) {
    size_t size = Be32(b, off), hdr = 8;
    if (size == 1) { // 64-bit size
      size = (static_cast<size_t>(Be32(b, off + 8)) << 32) | Be32(b, off + 12);
      hdr = 16;
    } else if (size == 0) { // runs to the end of its parent
      size = end - off;
    }
    if (size < hdr || size > end - off) {
      break;
    }
    out.push_back({off, size, hdr, std::string(&b[off + 4], 4)});
    off += size;
  }
  return out;
}

// Every box at the end of a type path from the file's top level.
std::vector<Box> Descend(const std::vector<char> &b,
                         std::initializer_list<const char *> path) {
  std::vector<Box> level{{0, b.size(), 0, ""}};
  for (const char *type : path) {
    std::vector<Box> next;
    for (const Box &parent : level) {
      for (const Box &child :
           Children(b, parent.off + parent.hdr, parent.off + parent.size)) {
        if (child.type == type) {
          next.push_back(child);
        }
      }
    }
    level = std::move(next);
  }
  return level;
}

// Offset of the first sample COUNT in the GPMF track's time-to-sample table
// (the stbl whose sample description is 'gpmd'), or 0 if there is none. The
// parser sums count x delta over that table into the track's duration.
size_t GpmfSttsFirstCount(const std::vector<char> &b) {
  for (const Box &stbl : Descend(b, {"moov", "trak", "mdia", "minf", "stbl"})) {
    size_t stts = 0;
    bool gpmd = false;
    for (const Box &t :
         Children(b, stbl.off + stbl.hdr, stbl.off + stbl.size)) {
      if (t.type == "stsd" && t.size >= t.hdr + 16 &&
          std::string(&b[t.off + t.hdr + 12], 4) == "gpmd") {
        gpmd = true;
      }
      if (t.type == "stts" && t.size >= t.hdr + 16) {
        stts = t.off + t.hdr + 8; // past version/flags + the entry count
      }
    }
    if (gpmd && stts != 0) {
      return stts;
    }
  }
  return 0;
}

// Set byte `k` of the value of every GPMF KLV named `key` holding chars (type
// 'c') to `value`; returns how many it changed. A KLV header is the FourCC,
// the type, the struct size and a 2-byte repeat, so the chars start 8 in.
size_t SetCharInEvery(std::vector<char> &b, const std::string &key, size_t k,
                      char value) {
  const std::string pattern = key + "c";
  size_t n = 0;
  for (auto it = b.begin(); (it = std::search(it, b.end(), pattern.begin(),
                                              pattern.end())) != b.end();
       ++it) {
    const size_t at = static_cast<size_t>(it - b.begin());
    if (at + 8 + k < b.size()) {
      b[at + 8 + k] = value;
      ++n;
    }
  }
  return n;
}

// A private copy of `bytes` on disk for as long as this object lives.
class ScratchClip {
public:
  explicit ScratchClip(const std::vector<char> &bytes) {
    std::string tmpl =
        (std::filesystem::temp_directory_path() / "pacer-gps-source-XXXXXX")
            .string();
    if (mkdtemp(tmpl.data()) != nullptr) {
      dir_ = tmpl;
      path_ = dir_ + "/mutant.mp4";
      std::ofstream(path_, std::ios::binary)
          .write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
    }
  }
  ~ScratchClip() {
    std::error_code ignored;
    if (!dir_.empty()) {
      std::filesystem::remove_all(dir_, ignored);
    }
  }
  ScratchClip(const ScratchClip &) = delete;
  ScratchClip &operator=(const ScratchClip &) = delete;
  const std::string &path() const { return path_; }

private:
  std::string dir_, path_;
};

struct Walk {
  size_t payloads = 0, fixes = 0;
  bool ended = false;
};

// The studio's payload walk (ingest._read_gps_over), capped: a cursor that
// does not end within `cap` payloads FAILS the test instead of hanging it.
Walk WalkPayloads(GPMFSource &src, size_t cap) {
  Walk w;
  src.Seek(0);
  while (!(w.ended = src.IsEnd()) && w.payloads < cap) {
    src.ReadSamples([&](GPSSample, uint32_t, uint32_t) { ++w.fixes; });
    src.Next();
    ++w.payloads;
  }
  return w;
}

const std::string kHero8 = std::string(PACER_GPMF_SAMPLES) + "/hero8.mp4";

} // namespace

TEST_CASE("GPMFSource's walk ends at the last payload even when the metadata "
          "track claims to run for decades",
          "[gps-source][hostile]") {
  std::vector<char> bytes = ReadAll(kHero8);
  REQUIRE(bytes.size() > 1000000);
  const size_t count_at = GpmfSttsFirstCount(bytes);
  REQUIRE(count_at != 0);
  // ~1.07e9 samples at the real per-payload delta: each span still reads ~1 s,
  // and the track says it lasts ~34 years. The review's hanging mutants had
  // exactly this shape (0.9-2.2e9 s against a 12.6 s video trak).
  PutBe32(bytes, count_at, 0x40000000u);
  ScratchClip mutant(bytes);
  REQUIRE_FALSE(mutant.path().empty());

  GPMFSource intact(kHero8.c_str());
  GPMFSource damaged(mutant.path().c_str());
  REQUIRE(damaged.GetTotalDuration() > 1e8); // the corruption took
  REQUIRE(damaged.GetVideoDuration() ==
          Catch::Approx(intact.GetVideoDuration()));

  const size_t cap = 100000; // ~0.1 s of the unbounded walk
  const Walk good = WalkPayloads(intact, cap);
  const Walk bad = WalkPayloads(damaged, cap);
  REQUIRE(good.ended);
  REQUIRE(good.fixes > 0);
  // Before the payload-count bound this walked on past the last payload,
  // reading nothing, for as long as the stated duration lasted — hours.
  CHECK(bad.ended);
  CHECK(bad.payloads == good.payloads);
  CHECK(bad.fixes == good.fixes);
}

TEST_CASE("GPMFSource hands back camera and axis names with a damaged byte as "
          "printable ASCII",
          "[gps-source][hostile]") {
  std::vector<char> bytes = ReadAll(kHero8);
  REQUIRE(bytes.size() > 1000000);
  GPMFSource intact(kHero8.c_str());
  REQUIRE(intact.DeviceName() == "HERO8 Black");
  const pacer::ImuOrientation declared = intact.ReadImuOrientation();
  REQUIRE(declared.accl_in.size() == 3);

  // 0xFF into the second character of every payload's camera name and the
  // first of every axis declaration: the byte one mutant of the review's sweep
  // carried, which failed the whole load in Python with a UnicodeDecodeError.
  REQUIRE(SetCharInEvery(bytes, "DVNM", 1, static_cast<char>(0xFF)) > 0);
  REQUIRE(SetCharInEvery(bytes, "ORIN", 0, static_cast<char>(0xFF)) > 0);
  ScratchClip mutant(bytes);
  REQUIRE_FALSE(mutant.path().empty());
  GPMFSource damaged(mutant.path().c_str());

  CHECK(damaged.DeviceName() == "H?RO8 Black");
  const pacer::ImuOrientation got = damaged.ReadImuOrientation();
  CHECK(got.accl_in == "?" + declared.accl_in.substr(1));
  CHECK(got.gyro_in == "?" + declared.gyro_in.substr(1));
  CHECK(got.accl_out == declared.accl_out); // ORIO untouched
}
