#pragma once

#include <utility> // std::pair (MinMax)
#include <vector>

#include <pacer/datatypes/datatypes.hpp>
#include <pacer/geometry/geometry.hpp>
#include <pacer/laps/point-track.hpp>

namespace pacer {

// One materialised lap: the GPS points it covers (interpolated start crossing +
// interior track points + interpolated finish crossing) and the matching
// per-point cumulative odometer.
struct Lap {
  std::vector<PointInTime<GPSSample>> points;
  std::vector<double> cum_distances;

  void FillDistances(const CoordinateSystem &cs);

  double LapTime() const;

  size_t Count() const;
};

// The INPUT timing geometry in local metres: the start line plus the
// intermediate sector lines. Confusion trap: the studio does NOT take per-lap
// sector splits from the C++ crossing list these produce — it projects each
// sector line onto a lap's odometer by DISTANCE in Python (studio/session.py,
// lap_sector_splits), because a short line can geometrically miss a crossing on
// some laps.
struct Sectors {
  Segment start_line;
  std::vector<Segment> sector_lines;
};

// A lap's per-point data as parallel columns, so the studio layer crosses the
// binding ONCE per lap instead of once per point (it used to call cs.local /
// read full_speed / time / cum_distances in loops over hundreds of points).
// Every column has the same length as the materialised lap (Lap::Count(): start
// crossing + interior points + finish crossing) and they are mutually index-
// aligned:
//   times          media-clock seconds (== Lap::points[i].time)
//   xs, ys         LOCAL metres — CoordinateSystem::Local(point).x|y in the
//   laps'
//                  own coordinate system (the one set via SetCoordinateSystem)
//   full_speed     raw 3D GPS speed m/s (the studio scales to km/h)
//   cum_distances  the lap's gap-aware per-point odometer (==
//   Lap::cum_distances)
struct LapArrays {
  std::vector<double> times;
  std::vector<double> xs;
  std::vector<double> ys;
  std::vector<double> full_speed;
  std::vector<double> cum_distances;
};

struct Laps {
  /// Re-segment the trace against the current start_line / sector_lines.
  void Update();

  /// A default start line: perpendicular to the local direction near the median
  /// point. Used when no start line has been chosen yet.
  Segment PickRandomStart() const;

  //---------------------------- PRESENTATION -------------------------------//

  void SetCoordinateSystem(CoordinateSystem coordinate_system);

  /// Bounding box (min/max lon-lat) over all raw points.
  auto MinMax() const -> std::pair<Point, Point>;

  //-------------------------------- LAPS -----------------------------------//

  Sectors sectors;

  size_t LapsCount() const;
  /// Throws std::out_of_range (Python: IndexError) if lap >= LapsCount().
  double LapEntrySpeed(size_t lap) const;
  /// Throws std::out_of_range (Python: IndexError) if lap >= LapsCount().
  double LapTime(size_t lap) const;
  /// Out-of-range lap -> 0 (documented empty-return contract, like GetLap).
  size_t SampleCount(size_t lap) const;
  /// Throws std::out_of_range (Python: IndexError) if lap >= LapsCount().
  double StartTimestamp(size_t lap) const;
  /// Throws std::out_of_range (Python: IndexError) if index >= LapsCount().
  double GetLapDistance(size_t index) const;

  Lap GetLap(size_t lap) const;

  /// A lap's per-point columns (see LapArrays) in ONE binding crossing instead
  /// of one per point. Equal (to float round-off) to materialising GetLap(lap)
  /// and reading p.time / cs.Local(p.point).x|y / p.point.full_speed /
  /// cum_distances[i] per point, where cs is the laps' own coordinate system.
  /// Out-of-range lap -> all-empty arrays.
  LapArrays LapColumns(size_t lap) const;

  //------------------------------- SECTORS ---------------------------------//

  size_t SectorCount() const;
  size_t RecordedSectors() const;
  void ClearSectors();
  /// Throws std::out_of_range (Python: IndexError) if sector >=
  /// RecordedSectors().
  double SectorTime(size_t sector) const;
  /// Throws std::out_of_range (Python: IndexError) if sector >=
  /// RecordedSectors().
  double SectorStartTimestamp(size_t sector) const;
  /// Throws std::out_of_range (Python: IndexError) if sector >=
  /// RecordedSectors().
  double SectorEntrySpeed(size_t sector) const;

  //------------------------------ RAW POINTS -------------------------------//

  void AddPoint(GPSSample s, double t);
  size_t PointCount() const;
  /// Throws std::out_of_range (Python: IndexError) if row >= PointCount().
  PointInTime<GPSSample> GetPoint(size_t row) const;
  void ClearPoints();

  /// The WHOLE raw track as parallel columns (see LapArrays) — the LapColumns
  /// idiom over the full trace, so the studio builds its full-trace arrays in
  /// ONE crossing rather than a GetPoint + cs.local per point. times[i] / xs[i]
  /// / ys[i] / full_speed[i] equal GetPoint(i).time,
  /// cs.Local(GetPoint(i).point).x|y and GetPoint(i).point.full_speed for every
  /// i in [0, PointCount()), and cum_distances[i] is the gap-aware odometer
  /// from point 0 to point i (the same cached prefix sum GetLap slices interior
  /// distances from). All five columns have length PointCount(); an empty track
  /// yields all-empty arrays.
  LapArrays TrackColumns() const;

private:
  struct LapChunk {
    PointInTime<GPSSample> start, finish;
    size_t start_index, finish_index;

    double Time() const;
  };

  // The raw point track + its coordinate system + cumulative-distance
  // machinery. Laps delegates every point/distance operation to it and layers
  // lap/sector segmentation on top.
  PointTrack track_;

  // The computed start/finish crossings for laps and for sectors. Named
  // *_chunks_ to set them apart from the INPUT geometry in the public `sectors`
  // member. sector_chunks_ is the raw GEOMETRIC crossing list; the studio's
  // per-lap sector splits are NOT built from it (see the Sectors note above).
  std::vector<LapChunk> lap_chunks_;
  std::vector<LapChunk> sector_chunks_;

  // Update() re-segments only when an input changed. The two sentinels detect a
  // timing-line edit (start_line / sector_lines); segmentation_dirty_ detects
  // the OTHER input — the point track / coordinate system. It starts true (a
  // fresh Laps has never been segmented) and is raised by AddPoint /
  // ClearPoints / SetCoordinateSystem, so a re-segment after the points changed
  // but the timing lines did not still recomputes instead of early-outing on
  // stale chunks.
  Segment dirty_start_line_ = {};
  std::vector<Segment> dirty_sector_lines_ = {};
  bool segmentation_dirty_ = true;
};

} // namespace pacer
