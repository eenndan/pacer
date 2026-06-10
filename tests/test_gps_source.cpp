#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include <cstdint>
#include <utility>
#include <vector>

#include <pacer/datatypes/datatypes.hpp>
#include <pacer/gps-source/gps-source.hpp>

using pacer::GPSSample;
using pacer::RawGPSSource;
using pacer::SequentialGPSSource;

namespace {

// An in-memory RawGPSSource that models a real GPMF chapter: a list of "payloads", each with a
// [in,out] media span and exactly ONE GPSSample (its .lat tags which payload it came from). It
// reproduces the GPMFSource control protocol the studio iterates with:
//
//     src.Seek(0);
//     while (!src.IsEnd()) {
//       auto [a,b] = src.CurrentTimeSpan();
//       src.Samples(...);            // emits the current payload's sample
//       src.Next();
//     }
//
// IsEnd() is true once index_ has walked past the last payload, mirroring GPMFSource (whose
// IsEnd reports true when GetPayloadTime fails / the span is empty at the end).
class StubSource : public RawGPSSource {
public:
  struct Payload {
    double in, out;
    GPSSample sample;
  };
  explicit StubSource(std::vector<Payload> payloads)
      : payloads_(std::move(payloads)) {}

  uint32_t Samples(void *data,
                   void (*on_sample)(void *, GPSSample, size_t,
                                     size_t)) override {
    if (index_ >= payloads_.size()) {
      return 1; // nothing at this index (matches GPMFSource "No payload")
    }
    on_sample(data, payloads_[index_].sample, 0, 1);
    return 0;
  }

  uint32_t Seek(double target) override {
    // Clamp to the first payload covering target; before the first payload -> index 0.
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

// Drive a source through the same protocol _read_gpmf uses, collecting (global_span, sample)
// for every payload. This is the exact loop where the chapter-seam skip bug manifested.
std::vector<std::pair<std::pair<double, double>, GPSSample>>
CollectAll(RawGPSSource &src) {
  std::vector<std::pair<std::pair<double, double>, GPSSample>> out;
  struct Cap {
    std::vector<std::pair<std::pair<double, double>, GPSSample>> *out;
    std::pair<double, double> span;
  };
  src.Seek(0);
  while (!src.IsEnd()) {
    Cap cap{&out, src.CurrentTimeSpan()};
    src.Samples(&cap, [](void *d, GPSSample s, size_t, size_t) {
      auto *c = static_cast<Cap *>(d);
      c->out->emplace_back(c->span, s);
    });
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

TEST_CASE("SequentialGPSSource does not drop the first payload of the second chapter",
          "[gps-source][seam]") {
  // Two chapters, 3 payloads each. Chapter 1 spans [0,3); chapter 2 spans [0,3) LOCALLY and is
  // shifted by chapter 1's duration (3.0) on the global axis the SequentialGPSSource reports.
  StubSource left({
      {0.0, 1.0, MakeSample(10)},
      {1.0, 2.0, MakeSample(11)},
      {2.0, 3.0, MakeSample(12)},
  });
  StubSource right({
      {0.0, 1.0, MakeSample(20)}, // <-- the payload that USED TO BE SKIPPED at the seam
      {1.0, 2.0, MakeSample(21)},
      {2.0, 3.0, MakeSample(22)},
  });
  SequentialGPSSource seq(&left, &right);

  auto got = CollectAll(seq);

  // Every payload from BOTH chapters must appear: 3 + 3 = 6, none dropped at the boundary.
  REQUIRE(got.size() == 6);

  // The tags come out in order, with the second chapter's FIRST payload (20) present.
  std::vector<double> tags;
  for (auto &g : got) {
    tags.push_back(g.second.lat);
  }
  REQUIRE(tags == std::vector<double>{10, 11, 12, 20, 21, 22});

  // Chapter 2's spans are offset by chapter 1's total duration (3.0). The first sample of
  // chapter 2 (tag 20) sits at global [3,4), i.e. CONTINUOUS with chapter 1's last span [2,3).
  const auto &seam = got[3]; // first sample after the boundary
  REQUIRE(seam.second.lat == 20);
  REQUIRE(seam.first.first == Catch::Approx(3.0));
  REQUIRE(seam.first.second == Catch::Approx(4.0));

  // The spans are monotonic and gap-free across the whole session (no jump at the seam).
  for (size_t i = 1; i < got.size(); ++i) {
    REQUIRE(got[i].first.first == Catch::Approx(got[i - 1].first.second));
  }
}

TEST_CASE("SequentialGPSSource total duration sums the chapters", "[gps-source][seam]") {
  StubSource left({{0.0, 3.0, MakeSample(1)}});
  StubSource right({{0.0, 2.0, MakeSample(2)}});
  SequentialGPSSource seq(&left, &right);
  REQUIRE(seq.GetTotalDuration() == Catch::Approx(5.0));
}

TEST_CASE("StubSource Seek-before-first-payload clamps to index 0 (no wrap / false EOF)",
          "[gps-source][seek]") {
  // Mirrors the GPMFSource::Seek fix at the protocol level: seeking to a target BEFORE the first
  // payload must land on payload 0 and NOT report end-of-stream. (GPMFSource's unsigned index_
  // used to wrap to UINT32_MAX here; this asserts the intended clamped behaviour the C++ Seek
  // now implements. The on-real-file underflow is exercised by the Python suite against the
  // actual GPMF source.)
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
