#include "laps.hpp"

#include <algorithm>
#include <span>
#include <vector>

#include <pacer/datatypes/datatypes.hpp>
#include <pacer/geometry/geometry.hpp>

namespace {

// A normal GoPro GPS fix lands every ~0.1 s. An interior jump longer than this is a real
// DROPOUT (a run of quality-gated / lost fixes), not jitter. Across such a hole the straight
// GPS chord cuts the corner and UNDER-measures the lap by tens of metres (measured: a single
// 6 s dropout cost ~100 m on the 0060 session). The vehicle's reported speed, however, is
// still valid right up to each fix, so the distance actually travelled across the hole is well
// approximated by the speed integral. We therefore measure each segment geometrically (the GPS
// chord, which is the right thing for well-sampled track) EXCEPT across a gap, where we take
// the trapezoidal speed integral 1/2 (v0+v1) * dt instead. Normal segments are untouched, so
// only the dropout laps change — and they stop under-counting.
constexpr double kGapSeconds = 0.35;

// Distance attributed to the step prev -> curr: the GPS chord normally, the speed integral
// when the time step is a dropout (so a chord across a hole no longer cuts the lap short).
double SegmentDistance(const pacer::CoordinateSystem &cs,
                       const pacer::PointInTime<pacer::GPSSample> &prev,
                       const pacer::PointInTime<pacer::GPSSample> &curr) {
  double chord = cs.Distance(prev.point, curr.point);
  double dt = curr.time - prev.time;
  if (dt > kGapSeconds) {
    double speed_integral =
        0.5 * (prev.point.full_speed + curr.point.full_speed) * dt;
    // Never let the fill SHORTEN the measured chord (guards a bad speed reading); the gap arc
    // is at least the straight-line distance between its mouths.
    return std::max(chord, speed_integral);
  }
  return chord;
}

// Prefix sum of the gap-aware SegmentDistance over consecutive points: the single owner of the
// cumulative-distance accumulation loop shared by Laps::SetCoordinateSystem (member odometer)
// and Lap::FillDistances (per-lap odometer). Returns a vector of size points.size(): index 0 is
// 0, index i is the distance from points[0] to points[i]. Empty input -> {0}, matching the
// {0}-seed both call sites relied on.
std::vector<double>
CumulativeDistances(const pacer::CoordinateSystem &cs,
                    std::span<const pacer::PointInTime<pacer::GPSSample>> points) {
  std::vector<double> cum;
  cum.reserve(points.empty() ? 1 : points.size());
  cum.push_back(0.0);
  for (size_t i = 1; i < points.size(); ++i) {
    cum.push_back(cum.back() + SegmentDistance(cs, points[i - 1], points[i]));
  }
  return cum;
}

} // namespace

void pacer::Laps::Update() {
  if (sectors.start_line == dirty_start_line_ &&
      sectors.sector_lines == dirty_sector_lines_)
    return;

  dirty_start_line_ = sectors.start_line;
  dirty_sector_lines_ = sectors.sector_lines;

  laps_.clear();
  sectors_.clear();

  if (points_.empty())
    return;

  // Seed `previous` with the first real point. Using a default-constructed
  // (null-island) sentinel here used to make the very first segment spuriously
  // cross any timing line, producing a phantom leading lap.
  PointInTime<GPSSample> previous = points_[0];

  int sector_index = -1;
  auto sector_line = [&] {
    return sector_index == -1 ? sectors.start_line
                              : sectors.sector_lines[sector_index];
  };

  auto to_global = [&](Segment x) -> Segment {
    auto gps_first = cs_.Global(Vec3f{x.first.x, x.first.y, 0});
    auto gps_second = cs_.Global(Vec3f{x.second.x, x.second.y, 0});
    return Segment{.first = {gps_first.lon, gps_first.lat},
                   .second = {gps_second.lon, gps_second.lat}};
  };

  // The start line is constant across the whole trace; hoist its (trig-heavy)
  // global conversion out of the loop. The sector line only changes when
  // sector_index advances, so cache it and recompute only on a sector switch.
  const Segment global_start = to_global(sectors.start_line);
  Segment global_sector = to_global(sector_line());
  int cached_sector_index = sector_index;

  for (size_t i = 1; i < points_.size(); ++i) {
    PointInTime<GPSSample> current = points_[i];

    if (sector_index != cached_sector_index) {
      global_sector = to_global(sector_line());
      cached_sector_index = sector_index;
    }

    auto lap_split = Split(global_start, previous, current);
    auto sector_split = Split(global_sector, previous, current);

    previous = current;

    if (lap_split) {
      if (!laps_.empty()) {
        laps_.back().finish = *lap_split;
        laps_.back().finish_index = i;
      }

      laps_.push_back(LapChunk{.start = *lap_split,
                               .finish = *lap_split,
                               .start_index = i,
                               .finish_index = i});
    }

    if (sector_split) {
      if (!sectors_.empty()) {
        sectors_.back().finish = *sector_split;
        sectors_.back().finish_index = i;
      }

      sectors_.push_back(LapChunk{
          .start = *sector_split,
          .finish = *sector_split,
          .start_index = i,
          .finish_index = i,
      });

      sector_index += 1;
      if (sector_index == static_cast<int>(sectors.sector_lines.size()))
        sector_index = -1;
    }
  }
}

pacer::Segment pacer::Laps::PickRandomStart() const {
  if (points_.size() < 2)
    return Segment{}; // not enough points to define a start line

  // Take the median point and one ~20 samples later (clamped to the last point
  // on short traces) to build a line perpendicular to the local direction.
  size_t i = points_.size() / 2;
  size_t j = std::min(i + 20, points_.size() - 1);
  if (i == j)
    i = j - 1;

  auto fst = points_[i].point;
  auto snd = points_[j].point;

  auto s1 = cs_.Local(fst);
  auto s2 = cs_.Local(snd);

  auto p1 = Point{s1[0], s1[1]}, p2 = Point{s2[0], s2[1]};
  auto m = (p1 + p2) / 2, dir = (p2 - p1);

  dir /= dir.Norm();
  dir = Point{-dir[1], dir[0]};

  // offset start midpoint by 5m
  return Segment{m - dir * 5, m + dir * 5};
}

auto pacer::Laps::MinMax() const -> std::pair<Point, Point> {
  if (points_.empty())
    return {{0, 0}, {0, 0}};
  Point min{points_[0].point.lon, points_[0].point.lat}, max = min;
  for (const auto &[p, _] : points_) {
    min.x = std::min(min.x, p.lon);
    max.x = std::max(max.x, p.lon);
    min.y = std::min(min.y, p.lat);
    max.y = std::max(max.y, p.lat);
  }
  return {min, max};
}

double pacer::Laps::LapChunk::Time() const { return finish.time - start.time; }

double pacer::Laps::GetLapDistance(size_t lap) const {
  // Uses the member `cs_` for ALL terms so the result is coherent with
  // cum_point_dist_ (which was built from cs_). (The old vestigial `cs` param was
  // removed; it was always ignored in favour of cs_.)
  //
  // This must equal the lap's true traversed distance as modelled by
  // GetLap()/FillDistances, whose materialized points are:
  //   [start, points_[start_index], ..., points_[finish_index - 1], finish]
  // i.e. the interior run is the HALF-OPEN range [start_index, finish_index).
  // The previous implementation summed cum[finish_index] - cum[start_index] and
  // joined points_[finish_index] -> finish, over-counting exactly one segment.
  // Uses SegmentDistance (gap-aware, same as cum_point_dist_ and FillDistances)
  // for the two partial chords so this AGREES exactly with GetLap().cum_distances.
  const LapChunk &chunk = laps_[lap];
  const size_t start_index = chunk.start_index;
  const size_t finish_index = chunk.finish_index;

  if (finish_index <= start_index) {
    // Degenerate / tiny lap: start and finish crossings fall on the same
    // segment, so GetLap() materializes just [start, finish] and the lap
    // distance is the single chord between the two crossings.
    return SegmentDistance(cs_, chunk.start, chunk.finish);
  }

  // start -> first interior point, then the bulk over the interior interpolation
  // points [start_index, finish_index), then the partial chord from the last
  // interior point to the finish crossing.
  double distance = SegmentDistance(cs_, chunk.start, points_[start_index]);
  distance += cum_point_dist_[finish_index - 1] - cum_point_dist_[start_index];
  distance += SegmentDistance(cs_, points_[finish_index - 1], chunk.finish);

  return distance;
}

double pacer::Laps::LapTime(size_t lap) const { return laps_[lap].Time(); }

size_t pacer::Laps::SampleCount(size_t lap) const {
  if (lap >= laps_.size()) {
    return 0;
  }
  // GetLap / At expose: interpolated start + interior points + interpolated
  // finish == (finish_index - start_index) + 2 rows.
  return laps_[lap].finish_index - laps_[lap].start_index + 2;
}

double pacer::Laps::StartTimestamp(size_t lap) const {
  return laps_[lap].start.time;
}

pacer::Lap pacer::Laps::GetLap(size_t lap) const {
  if (lap >= laps_.size())
    return {};

  const LapChunk &chunk = laps_[lap];
  const size_t start_index = chunk.start_index;
  const size_t finish_index = chunk.finish_index;

  std::vector<PointInTime<GPSSample>> points{chunk.start};
  points.insert(points.end(), points_.begin() + start_index,
                points_.begin() + finish_index);
  points.push_back(chunk.finish);

  // Build cum_distances from the cached cum_point_dist_ instead of re-walking
  // FillDistances. This matches what FillDistances(cs_) produces (to float
  // round-off) over the same materialized points:
  //   points = [start, points_[start_index .. finish_index), finish]
  // cum_distances[0]      = 0
  // cum_distances[1]      = cap0 = SegmentDistance(start, points_[start_index])
  // cum_distances[1 + k]  = cap0 + (cum_point_dist_[start_index + k]
  //                                 - cum_point_dist_[start_index])  (interior)
  // cum_distances[last]   = prev + SegmentDistance(last interior point, finish)
  // cum_point_dist_ already aggregates the interior steps with the same gap-aware
  // SegmentDistance; the two partial (start/finish) chords are not covered by it
  // and are added explicitly with the SAME SegmentDistance FillDistances used.
  std::vector<double> cum_distances;
  cum_distances.reserve(points.size());
  cum_distances.push_back(0.0);

  if (finish_index > start_index) {
    const double cap0 =
        SegmentDistance(cs_, chunk.start, points_[start_index]);
    cum_distances.push_back(cap0);
    for (size_t k = 1; k < finish_index - start_index; ++k) {
      cum_distances.push_back(
          cap0 + (cum_point_dist_[start_index + k] - cum_point_dist_[start_index]));
    }
    cum_distances.push_back(cum_distances.back() +
                            SegmentDistance(cs_, points_[finish_index - 1],
                                            chunk.finish));
  } else {
    // Degenerate lap: points == [start, finish]; single chord between them.
    cum_distances.push_back(SegmentDistance(cs_, chunk.start, chunk.finish));
  }

  return Lap{.width = 0.0f,
             .points = std::move(points),
             .cum_distances = std::move(cum_distances)};
}

double pacer::Laps::SectorStartTimestamp(size_t sector) const {
  return sectors_[sector].start.time;
}

double pacer::Laps::SectorEntrySpeed(size_t sector) const {
  return sectors_[sector].start.point.full_speed;
}

double pacer::Laps::SectorTime(size_t sector) const {
  return sectors_[sector].finish.time - sectors_[sector].start.time;
}

size_t pacer::Laps::SectorCount() const { return sectors.sector_lines.size(); }

double pacer::Laps::LapEntrySpeed(size_t lap) const {
  return laps_[lap].start.point.full_speed;
}

size_t pacer::Laps::LapsCount() const { return laps_.size(); }

void pacer::Laps::ClearSectors() { sectors.sector_lines.clear(); }

void pacer::Laps::AddPoint(GPSSample s, double t) {
  // Only grow points_ and push a placeholder so cum_point_dist_ stays the same
  // length as points_ (the seed {0} covers the first point). The real cumulative
  // distances are computed entirely in SetCoordinateSystem, which is the sole
  // authority: filling them here would use the still-default cs_ (garbage that
  // SetCoordinateSystem later overwrites) and waste a trig-heavy Distance call.
  // cum_point_dist_ is only meaningful AFTER SetCoordinateSystem has run.
  if (!points_.empty())
    cum_point_dist_.push_back(0.0);
  points_.emplace_back(s, t);
}

size_t pacer::Laps::PointCount() const { return points_.size(); }

pacer::PointInTime<pacer::GPSSample> pacer::Laps::GetPoint(size_t row) const {
  return points_[row];
}

void pacer::Laps::SetCoordinateSystem(CoordinateSystem coordinate_system) {
  cs_ = coordinate_system;
  // Guard: with no points keep the {0} seed (CumulativeDistances would also return {0}, but
  // bailing avoids rebuilding it and documents the invariant that index [0] / .back() stay
  // valid for a later AddPoint/SetCoordinateSystem).
  if (points_.empty())
    return;
  // SetCoordinateSystem is the sole authority for cum_point_dist_ (AddPoint only reserves
  // placeholders). The accumulation loop is single-sourced in CumulativeDistances; the result
  // has size points_.size(), restoring the cum_point_dist_.size() == points_.size() invariant.
  cum_point_dist_ = CumulativeDistances(cs_, points_);
}
size_t pacer::Laps::RecordedSectors() const { return sectors_.size(); }

size_t pacer::Lap::Count() const { return points.size(); }

void pacer::Lap::FillDistances(const CoordinateSystem &cs) {
  // Gap-aware (see SegmentDistance): a GPS chord across a dropout cuts the corner and
  // under-counts, so a long time-step uses the speed integral instead. Keeps the per-lap
  // odometer the Python delta/sector math reads consistent with GetLapDistance. The
  // accumulation loop is single-sourced in CumulativeDistances.
  cum_distances = CumulativeDistances(cs, points);
}
double pacer::Lap::LapTime() const {
  return points.back().time - points.front().time;
}
void pacer::Laps::ClearPoints() {
  points_.clear();
  // Reseed the {0} sentinel that the class declares: index [0] / .back() must
  // stay valid so a later AddPoint/SetCoordinateSystem is not UB. A bare
  // .clear() (size 0) broke that invariant.
  cum_point_dist_.assign(1, 0.0);
}
