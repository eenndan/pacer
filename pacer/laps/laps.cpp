#include "laps.hpp"

#include <algorithm>
#include <vector>

#include <pacer/datatypes/datatypes.hpp>
#include <pacer/geometry/geometry.hpp>
#include <pacer/laps/point-track.hpp>

// The gap-aware SegmentDistance + the CumulativeDistances prefix sum now live in PointTrack
// (pacer/laps/point-track.hpp), which OWNS the point track and its coordinate system. Laps
// delegates point/distance operations to track_ and uses its boundary-chord helper for the
// start/finish partial segments, keeping lap/sector segmentation on top.

void pacer::Laps::Update() {
  if (sectors.start_line == dirty_start_line_ &&
      sectors.sector_lines == dirty_sector_lines_)
    return;

  dirty_start_line_ = sectors.start_line;
  dirty_sector_lines_ = sectors.sector_lines;

  laps_.clear();
  sectors_.clear();

  if (track_.PointCount() == 0)
    return;

  const CoordinateSystem &cs_ = track_.Cs();

  // Seed `previous` with the first real point. Using a default-constructed
  // (null-island) sentinel here used to make the very first segment spuriously
  // cross any timing line, producing a phantom leading lap.
  PointInTime<GPSSample> previous = track_.Point(0);

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

  for (size_t i = 1; i < track_.PointCount(); ++i) {
    PointInTime<GPSSample> current = track_.Point(i);

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
  if (track_.PointCount() < 2)
    return Segment{}; // not enough points to define a start line

  // Take the median point and one ~20 samples later (clamped to the last point
  // on short traces) to build a line perpendicular to the local direction.
  size_t i = track_.PointCount() / 2;
  size_t j = std::min(i + 20, track_.PointCount() - 1);
  if (i == j)
    i = j - 1;

  auto fst = track_.Point(i).point;
  auto snd = track_.Point(j).point;

  auto s1 = track_.Cs().Local(fst);
  auto s2 = track_.Cs().Local(snd);

  auto p1 = Point{s1[0], s1[1]}, p2 = Point{s2[0], s2[1]};
  auto m = (p1 + p2) / 2, dir = (p2 - p1);

  dir /= dir.Norm();
  dir = Point{-dir[1], dir[0]};

  // offset start midpoint by 5m
  return Segment{m - dir * 5, m + dir * 5};
}

auto pacer::Laps::MinMax() const -> std::pair<Point, Point> {
  auto points = track_.Points();
  if (points.empty())
    return {{0, 0}, {0, 0}};
  Point min{points[0].point.lon, points[0].point.lat}, max = min;
  for (const auto &[p, _] : points) {
    min.x = std::min(min.x, p.lon);
    max.x = std::max(max.x, p.lon);
    min.y = std::min(min.y, p.lat);
    max.y = std::max(max.y, p.lat);
  }
  return {min, max};
}

double pacer::Laps::LapChunk::Time() const { return finish.time - start.time; }

double pacer::Laps::GetLapDistance(size_t lap) const {
  // Uses the track's single coordinate system for ALL terms (via track_.ChordDistance /
  // track_.DistanceBetween) so the result is coherent with the cached cumulative odometer
  // (built from that same cs). (The old vestigial `cs` param was removed; it was always
  // ignored in favour of the member coordinate system.)
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
    return track_.ChordDistance(chunk.start, chunk.finish);
  }

  // start -> first interior point, then the bulk over the interior interpolation
  // points [start_index, finish_index), then the partial chord from the last
  // interior point to the finish crossing.
  double distance = track_.ChordDistance(chunk.start, track_.Point(start_index));
  distance += track_.DistanceBetween(start_index, finish_index - 1);
  distance += track_.ChordDistance(track_.Point(finish_index - 1), chunk.finish);

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

  auto track_points = track_.Points();
  std::vector<PointInTime<GPSSample>> points{chunk.start};
  points.insert(points.end(), track_points.begin() + start_index,
                track_points.begin() + finish_index);
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
        track_.ChordDistance(chunk.start, track_.Point(start_index));
    cum_distances.push_back(cap0);
    for (size_t k = 1; k < finish_index - start_index; ++k) {
      cum_distances.push_back(
          cap0 + track_.DistanceBetween(start_index, start_index + k));
    }
    cum_distances.push_back(cum_distances.back() +
                            track_.ChordDistance(track_.Point(finish_index - 1),
                                                 chunk.finish));
  } else {
    // Degenerate lap: points == [start, finish]; single chord between them.
    cum_distances.push_back(track_.ChordDistance(chunk.start, chunk.finish));
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

// Point/distance operations delegate to the owned PointTrack (which carries the same
// AddPoint/SetCoordinateSystem/ClearPoints semantics and invariants documented there).
void pacer::Laps::AddPoint(GPSSample s, double t) { track_.AddPoint(s, t); }

size_t pacer::Laps::PointCount() const { return track_.PointCount(); }

pacer::PointInTime<pacer::GPSSample> pacer::Laps::GetPoint(size_t row) const {
  return track_.Point(row);
}

void pacer::Laps::SetCoordinateSystem(CoordinateSystem coordinate_system) {
  track_.SetCoordinateSystem(coordinate_system);
}
size_t pacer::Laps::RecordedSectors() const { return sectors_.size(); }

size_t pacer::Lap::Count() const { return points.size(); }

void pacer::Lap::FillDistances(const CoordinateSystem &cs) {
  // Gap-aware (see PointTrack::SegmentDistance): a GPS chord across a dropout cuts the corner and
  // under-counts, so a long time-step uses the speed integral instead. Keeps the per-lap odometer
  // the Python delta/sector math reads consistent with GetLapDistance. The accumulation loop is
  // single-sourced in PointTrack::CumulativeDistances.
  cum_distances = PointTrack::CumulativeDistances(cs, points);
}
double pacer::Lap::LapTime() const {
  return points.back().time - points.front().time;
}
void pacer::Laps::ClearPoints() { track_.ClearPoints(); }
