#include "laps.hpp"

#include <algorithm>
#include <span>
#include <stdexcept>
#include <string>
#include <vector>

#include <pacer/datatypes/datatypes.hpp>
#include <pacer/geometry/geometry.hpp>
#include <pacer/laps/point-track.hpp>

// The gap-aware chord distance and the cumulative-odometer prefix sum live in
// PointTrack (pacer/laps/point-track.hpp), which owns the point track and its
// coordinate system. Laps delegates point/distance work to track_ and keeps
// only the lap/sector segmentation here.

namespace {
// Guards the Python-bound scalar accessors: a bad index arriving through the
// bindings must surface as std::out_of_range (which nanobind maps to a Python
// IndexError) rather than indexing a vector out of bounds (UB in Release). The
// empty-return accessors (GetLap / SampleCount / LapColumns) do not use this.
void CheckIndex(const char *accessor, size_t index, size_t size) {
  if (index >= size)
    throw std::out_of_range(std::string(accessor) + ": index " +
                            std::to_string(index) + " >= size " +
                            std::to_string(size));
}

// Shared by LapColumns and TrackColumns: project a run of points into `cs` and
// fill the four point-derived columns (times, local xs/ys, full_speed). The
// fifth column (cum_distances) is left to the caller, whose odometer source
// differs.
pacer::LapArrays
PointColumns(const pacer::CoordinateSystem &cs,
             std::span<const pacer::PointInTime<pacer::GPSSample>> points) {
  pacer::LapArrays cols;
  const size_t n = points.size();
  cols.times.reserve(n);
  cols.xs.reserve(n);
  cols.ys.reserve(n);
  cols.full_speed.reserve(n);
  for (const auto &p : points) {
    pacer::Vec3f loc = cs.Local(p.point);
    cols.times.push_back(p.time);
    cols.xs.push_back(loc.x);
    cols.ys.push_back(loc.y);
    cols.full_speed.push_back(p.point.full_speed);
  }
  return cols;
}
} // namespace

void pacer::Laps::Update() {
  // Skip the work unless something feeding the segmentation changed: the two
  // timing-line sentinels catch a start_line / sector_lines edit, and
  // segmentation_dirty_ catches a point-track / coordinate-system change. All
  // three must be unchanged to early-out — otherwise a points-only change would
  // keep stale lap_chunks_ pointing at the old track.
  if (!segmentation_dirty_ && sectors.start_line == dirty_start_line_ &&
      sectors.sector_lines == dirty_sector_lines_)
    return;

  dirty_start_line_ = sectors.start_line;
  dirty_sector_lines_ = sectors.sector_lines;
  segmentation_dirty_ = false;

  lap_chunks_.clear();
  sector_chunks_.clear();

  if (track_.PointCount() == 0)
    return;

  // Without intermediate sector lines there is nothing to record as a sector:
  // the rotating line would fall back to the start line and log every start
  // crossing as a phantom sector. Keep sector_chunks_ empty so
  // RecordedSectors() agrees with SectorCount() (== 0).
  const bool has_sectors = !sectors.sector_lines.empty();

  const CoordinateSystem &cs = track_.Cs();

  // Seed `prev` with the first real point. A default-constructed sentinel here
  // would make the first segment spuriously cross any line — a phantom lead
  // lap.
  PointInTime<GPSSample> prev = track_.Point(0);

  int sector_index = -1;
  auto active_sector_line = [&] {
    return sector_index == -1 ? sectors.start_line
                              : sectors.sector_lines[sector_index];
  };

  // The crossing test runs in lon-lat space, so map a local-metre timing
  // segment to lon-lat (z = 0) by round-tripping its endpoints through the
  // coordinate system.
  auto to_lonlat = [&](Segment x) -> Segment {
    auto gps_first = cs.Global(Vec3f{x.first.x, x.first.y, 0});
    auto gps_second = cs.Global(Vec3f{x.second.x, x.second.y, 0});
    return Segment{.first = {gps_first.lon, gps_first.lat},
                   .second = {gps_second.lon, gps_second.lat}};
  };

  // The start line is fixed for the whole trace, so convert it once (the
  // mapping is trig-heavy). The sector line only changes when sector_index
  // advances, so cache its conversion and refresh only on a switch.
  const Segment global_start = to_lonlat(sectors.start_line);
  Segment global_sector = to_lonlat(active_sector_line());
  int cached_sector_index = sector_index;

  for (size_t i = 1; i < track_.PointCount(); ++i) {
    PointInTime<GPSSample> cur = track_.Point(i);

    if (sector_index != cached_sector_index) {
      global_sector = to_lonlat(active_sector_line());
      cached_sector_index = sector_index;
    }

    auto lap_split = Split(global_start, prev, cur);
    // Only probe the rotating sector line when sector lines actually exist (see
    // has_sectors above); otherwise it is the start line and would record
    // phantom sectors.
    auto sector_split =
        has_sectors ? Split(global_sector, prev, cur) : std::nullopt;

    prev = cur;

    if (lap_split) {
      // Close the previous lap at this crossing, then open a new one.
      if (!lap_chunks_.empty()) {
        lap_chunks_.back().finish = *lap_split;
        lap_chunks_.back().finish_index = i;
      }

      lap_chunks_.push_back(LapChunk{.start = *lap_split,
                                     .finish = *lap_split,
                                     .start_index = i,
                                     .finish_index = i});
    }

    if (sector_split) {
      if (!sector_chunks_.empty()) {
        sector_chunks_.back().finish = *sector_split;
        sector_chunks_.back().finish_index = i;
      }

      sector_chunks_.push_back(LapChunk{
          .start = *sector_split,
          .finish = *sector_split,
          .start_index = i,
          .finish_index = i,
      });

      // Advance to the next sector line, wrapping back to the start line (-1).
      sector_index += 1;
      if (sector_index == static_cast<int>(sectors.sector_lines.size()))
        sector_index = -1;
    }
  }
}

pacer::Segment pacer::Laps::PickRandomStart() const {
  if (track_.PointCount() < 2)
    return Segment{}; // too few points to define a line

  // Take the median point and one ~20 samples later (clamped on short traces)
  // and build a line through their midpoint, perpendicular to their direction.
  size_t i = track_.PointCount() / 2;
  size_t j = std::min(i + 20, track_.PointCount() - 1);
  if (i == j)
    i = j - 1;

  auto fst = track_.Point(i).point;
  auto snd = track_.Point(j).point;

  auto s1 = track_.Cs().Local(fst);
  auto s2 = track_.Cs().Local(snd);

  auto p1 = Point{s1[0], s1[1]}, p2 = Point{s2[0], s2[1]};
  auto mid = (p1 + p2) / 2, dir = (p2 - p1);

  dir /= dir.Norm();
  dir = Point{-dir[1], dir[0]};

  // Span 5 m either side of the midpoint.
  return Segment{mid - dir * 5, mid + dir * 5};
}

auto pacer::Laps::MinMax() const -> std::pair<Point, Point> {
  auto points = track_.Points();
  if (points.empty())
    return {{0, 0}, {0, 0}};
  Point lo{points[0].point.lon, points[0].point.lat}, hi = lo;
  for (const auto &[p, _] : points) {
    lo.x = std::min(lo.x, p.lon);
    hi.x = std::max(hi.x, p.lon);
    lo.y = std::min(lo.y, p.lat);
    hi.y = std::max(hi.y, p.lat);
  }
  return {lo, hi};
}

double pacer::Laps::LapChunk::Time() const { return finish.time - start.time; }

double pacer::Laps::GetLapDistance(size_t lap) const {
  // The distance the lap actually traversed, kept consistent with the cached
  // odometer (all terms use track_.ChordDistance / track_.DistanceBetween, the
  // same gap-aware metric that built cum_point_dist_ and FillDistances). It
  // must equal GetLap().cum_distances.back(), whose materialised points are
  //   [start, points_[start_index], ..., points_[finish_index - 1], finish]
  // i.e. the interior run is the HALF-OPEN range [start_index, finish_index).
  CheckIndex("GetLapDistance", lap, lap_chunks_.size());
  const LapChunk &chunk = lap_chunks_[lap];
  const size_t start_index = chunk.start_index;
  const size_t finish_index = chunk.finish_index;

  if (finish_index <= start_index) {
    // Tiny lap: both crossings land on one segment, so GetLap() materialises
    // just [start, finish] and the distance is the single chord between them.
    return track_.ChordDistance(chunk.start, chunk.finish);
  }

  // start -> first interior point, then the interior bulk over
  // [start_index, finish_index), then last interior point -> finish crossing.
  double distance =
      track_.ChordDistance(chunk.start, track_.Point(start_index));
  distance += track_.DistanceBetween(start_index, finish_index - 1);
  distance +=
      track_.ChordDistance(track_.Point(finish_index - 1), chunk.finish);

  return distance;
}

double pacer::Laps::LapTime(size_t lap) const {
  CheckIndex("LapTime", lap, lap_chunks_.size());
  return lap_chunks_[lap].Time();
}

size_t pacer::Laps::SampleCount(size_t lap) const {
  if (lap >= lap_chunks_.size()) {
    return 0;
  }
  // Interpolated start + interior points + interpolated finish ==
  // (finish_index - start_index) + 2 rows.
  return lap_chunks_[lap].finish_index - lap_chunks_[lap].start_index + 2;
}

double pacer::Laps::StartTimestamp(size_t lap) const {
  CheckIndex("StartTimestamp", lap, lap_chunks_.size());
  return lap_chunks_[lap].start.time;
}

pacer::Lap pacer::Laps::GetLap(size_t lap) const {
  if (lap >= lap_chunks_.size())
    return {};

  const LapChunk &chunk = lap_chunks_[lap];
  const size_t start_index = chunk.start_index;
  const size_t finish_index = chunk.finish_index;

  auto track_points = track_.Points();
  std::vector<PointInTime<GPSSample>> points{chunk.start};
  points.insert(points.end(), track_points.begin() + start_index,
                track_points.begin() + finish_index);
  points.push_back(chunk.finish);

  // Build cum_distances from the cached prefix sum rather than re-walking
  // FillDistances; this matches FillDistances(cs) to float round-off over the
  // materialised points [start, points_[start_index .. finish_index), finish]:
  //   [0] = 0
  //   [1] = cap0 = chord(start, points_[start_index])
  //   [1+k] = cap0 + (cum[start_index + k] - cum[start_index])   (interior)
  //   [last] = prev + chord(last interior point, finish)
  // The interior steps come from the gap-aware cached odometer; the two partial
  // (start/finish) chords are added explicitly with the same chord metric.
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
    cum_distances.push_back(
        cum_distances.back() +
        track_.ChordDistance(track_.Point(finish_index - 1), chunk.finish));
  } else {
    // Tiny lap: points == [start, finish]; one chord between them.
    cum_distances.push_back(track_.ChordDistance(chunk.start, chunk.finish));
  }

  return Lap{.points = std::move(points),
             .cum_distances = std::move(cum_distances)};
}

pacer::LapArrays pacer::Laps::LapColumns(size_t lap) const {
  // Materialise the lap exactly as GetLap (so points + cum_distances are
  // byte-identical to the per-element studio path), then project each point
  // into the laps' coordinate system via PointColumns. The only added work is
  // the Local() projection the studio used to do per point — now one crossing.
  Lap materialized = GetLap(lap); // out-of-range -> empty -> empty columns
  LapArrays cols = PointColumns(track_.Cs(), materialized.points);
  cols.cum_distances = std::move(materialized.cum_distances);
  return cols;
}

pacer::LapArrays pacer::Laps::TrackColumns() const {
  // Same idea as LapColumns but over the WHOLE raw track: PointColumns projects
  // every point, and the distance column is the track's own gap-aware
  // cumulative odometer (the cached prefix sum GetLap slices from). The loop
  // bound is PointCount(), so an empty track yields all-empty columns.
  LapArrays cols = PointColumns(track_.Cs(), track_.Points());
  const size_t n = track_.PointCount();
  cols.cum_distances.reserve(n);
  for (size_t i = 0; i < n; ++i) {
    cols.cum_distances.push_back(track_.CumulativeDistance(i));
  }
  return cols;
}

double pacer::Laps::SectorStartTimestamp(size_t sector) const {
  CheckIndex("SectorStartTimestamp", sector, sector_chunks_.size());
  return sector_chunks_[sector].start.time;
}

double pacer::Laps::SectorEntrySpeed(size_t sector) const {
  CheckIndex("SectorEntrySpeed", sector, sector_chunks_.size());
  return sector_chunks_[sector].start.point.full_speed;
}

double pacer::Laps::SectorTime(size_t sector) const {
  CheckIndex("SectorTime", sector, sector_chunks_.size());
  return sector_chunks_[sector].finish.time - sector_chunks_[sector].start.time;
}

size_t pacer::Laps::SectorCount() const { return sectors.sector_lines.size(); }

double pacer::Laps::LapEntrySpeed(size_t lap) const {
  CheckIndex("LapEntrySpeed", lap, lap_chunks_.size());
  return lap_chunks_[lap].start.point.full_speed;
}

size_t pacer::Laps::LapsCount() const { return lap_chunks_.size(); }

void pacer::Laps::ClearSectors() { sectors.sector_lines.clear(); }

// Point/distance operations delegate to the owned PointTrack and additionally
// raise segmentation_dirty_ so the next Update() re-segments against the
// changed track even when the timing lines were untouched.
void pacer::Laps::AddPoint(GPSSample s, double t) {
  track_.AddPoint(s, t);
  segmentation_dirty_ = true;
}

size_t pacer::Laps::PointCount() const { return track_.PointCount(); }

pacer::PointInTime<pacer::GPSSample> pacer::Laps::GetPoint(size_t row) const {
  // Bounds-checked here at the bound surface, not inside PointTrack::Point,
  // which the segmentation loop hits once per sample and must stay
  // unchecked-hot.
  CheckIndex("GetPoint", row, track_.PointCount());
  return track_.Point(row);
}

void pacer::Laps::SetCoordinateSystem(CoordinateSystem coordinate_system) {
  track_.SetCoordinateSystem(coordinate_system);
  segmentation_dirty_ = true;
}

size_t pacer::Laps::RecordedSectors() const { return sector_chunks_.size(); }

size_t pacer::Lap::Count() const { return points.size(); }

void pacer::Lap::FillDistances(const CoordinateSystem &cs) {
  // Gap-aware (see PointTrack::SegmentDistance): a GPS chord across a dropout
  // cuts the corner and under-counts, so a long time-step uses the speed
  // integral instead. The accumulation itself lives in
  // PointTrack::CumulativeDistances.
  cum_distances = PointTrack::CumulativeDistances(cs, points);
}

double pacer::Lap::LapTime() const {
  return points.back().time - points.front().time;
}

void pacer::Laps::ClearPoints() {
  track_.ClearPoints();
  segmentation_dirty_ = true;
}
