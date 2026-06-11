#include "laps.hpp"

#include <algorithm>
#include <stdexcept>
#include <string>
#include <vector>

#include <pacer/datatypes/datatypes.hpp>
#include <pacer/geometry/geometry.hpp>
#include <pacer/laps/point-track.hpp>

// The gap-aware SegmentDistance + the CumulativeDistances prefix sum now live in PointTrack
// (pacer/laps/point-track.hpp), which OWNS the point track and its coordinate system. Laps
// delegates point/distance operations to track_ and uses its boundary-chord helper for the
// start/finish partial segments, keeping lap/sector segmentation on top.

namespace {
// Bounds guard for the Python-bound scalar accessors (LapTime, StartTimestamp,
// LapEntrySpeed, GetLapDistance, GetPoint, SectorTime, SectorStartTimestamp,
// SectorEntrySpeed): a bad index arriving through the bindings must surface as
// std::out_of_range — which nanobind translates to a Python IndexError — not
// index the underlying vector unguarded (UB in a Release build). In-range
// calls are bit-identical to before (this check is the only addition).
// GetLap / SampleCount / LapColumns keep their separately documented empty/0
// returns for an out-of-range lap and do NOT use this guard.
void CheckIndex(const char *accessor, size_t index, size_t size) {
  if (index >= size)
    throw std::out_of_range(std::string(accessor) + ": index " +
                            std::to_string(index) + " >= size " +
                            std::to_string(size));
}
} // namespace

void pacer::Laps::Update() {
  // Re-segment only when something that feeds the segmentation changed. The timing-line sentinels
  // catch a start_line/sector_lines edit; segmentation_dirty_ catches the OTHER input — the point
  // track / coordinate system (set by AddPoint/ClearPoints/SetCoordinateSystem). Without the latter
  // a re-segment after the points changed but the timing lines did NOT would early-out and keep
  // STALE lap_chunks_ pointing at the old track (the cache-staleness bug). All three must be
  // unchanged to skip.
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

  // With no intermediate sector lines there are no sectors to record: the rotating "sector line"
  // would otherwise fall back to the start line (sector_index == -1) and record every start-line
  // crossing as a phantom sector. SectorCount() is already 0 in that case; this keeps the recorded
  // sector_chunks_ consistent with it (RecordedSectors() == 0) instead of carrying start crossings.
  const bool has_sectors = !sectors.sector_lines.empty();

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
    // Only test the rotating sector line when there ARE intermediate sector lines; otherwise the
    // line is the start line and every crossing would be recorded as a phantom sector (see the
    // has_sectors note above). With no sector lines this stays empty -> nothing recorded.
    auto sector_split =
        has_sectors ? Split(global_sector, previous, current) : std::nullopt;

    previous = current;

    if (lap_split) {
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
  CheckIndex("GetLapDistance", lap, lap_chunks_.size());
  const LapChunk &chunk = lap_chunks_[lap];
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

double pacer::Laps::LapTime(size_t lap) const {
  CheckIndex("LapTime", lap, lap_chunks_.size());
  return lap_chunks_[lap].Time();
}

size_t pacer::Laps::SampleCount(size_t lap) const {
  if (lap >= lap_chunks_.size()) {
    return 0;
  }
  // GetLap / At expose: interpolated start + interior points + interpolated
  // finish == (finish_index - start_index) + 2 rows.
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

pacer::LapArrays pacer::Laps::LapColumns(size_t lap) const {
  // Materialize the lap exactly as GetLap does (interpolated start crossing + interior track
  // points + interpolated finish crossing, with the gap-aware cum_distances), then project each
  // point into the laps' OWN coordinate system. Reusing GetLap guarantees the points and
  // cum_distances are byte-identical to the per-element studio path (which read GetLap()'s
  // .points / .cum_distances); the only added work is the Local() projection, which the studio
  // layer did per point anyway — now batched into this single crossing.
  Lap materialized = GetLap(lap); // out-of-range -> empty Lap -> empty columns below
  const CoordinateSystem &cs = track_.Cs();

  LapArrays cols;
  const size_t n = materialized.points.size();
  cols.times.reserve(n);
  cols.xs.reserve(n);
  cols.ys.reserve(n);
  cols.full_speed.reserve(n);
  for (const auto &p : materialized.points) {
    Vec3f loc = cs.Local(p.point);
    cols.times.push_back(p.time);
    cols.xs.push_back(loc.x);
    cols.ys.push_back(loc.y);
    cols.full_speed.push_back(p.point.full_speed);
  }
  // The lap's per-point odometer is already built by GetLap (size == points.size()); move it
  // across verbatim so cum_distances matches Lap::cum_distances exactly.
  cols.cum_distances = std::move(materialized.cum_distances);
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

// Point/distance operations delegate to the owned PointTrack (which carries the same
// AddPoint/SetCoordinateSystem/ClearPoints semantics and invariants documented there). Each also
// marks the segmentation dirty so the next Update() re-segments against the changed track even when
// the timing lines were not touched (the timing-line sentinels alone would otherwise early-out).
void pacer::Laps::AddPoint(GPSSample s, double t) {
  track_.AddPoint(s, t);
  segmentation_dirty_ = true;
}

size_t pacer::Laps::PointCount() const { return track_.PointCount(); }

pacer::PointInTime<pacer::GPSSample> pacer::Laps::GetPoint(size_t row) const {
  // Bounds-checked HERE, at the bound surface, not inside PointTrack::Point —
  // the segmentation loop hits that accessor once per sample and stays
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
  // Gap-aware (see PointTrack::SegmentDistance): a GPS chord across a dropout cuts the corner and
  // under-counts, so a long time-step uses the speed integral instead. Keeps the per-lap odometer
  // the Python delta/sector math reads consistent with GetLapDistance. The accumulation loop is
  // single-sourced in PointTrack::CumulativeDistances.
  cum_distances = PointTrack::CumulativeDistances(cs, points);
}
double pacer::Lap::LapTime() const {
  return points.back().time - points.front().time;
}
void pacer::Laps::ClearPoints() {
  track_.ClearPoints();
  segmentation_dirty_ = true;
}
