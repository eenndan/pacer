// This file based on ImGui's demo app with ImPlot's demo sprinkled on top.
// I'm gonna leave all boilerplate for now.

// Dear ImGui: standalone example application for GLFW + OpenGL 3, using
// programmable pipeline (GLFW is a cross-platform general purpose library for
// handling windows, inputs, OpenGL/Vulkan/Metal graphics context creation,
// etc.)

// Learn about Dear ImGui:
// - FAQ                  https://dearimgui.com/faq
// - Getting Started      https://dearimgui.com/getting-started
// - Documentation        https://dearimgui.com/docs (same as your local docs/
// folder).
// - Introduction, links and more at the top of imgui.cpp

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#include "imgui.h"

#include "implot.h"
#include "implot_internal.h"
#include <hello_imgui/hello_imgui.h>
#include <nlohmann/json.hpp>

#include <pacer/datatypes/datatypes.hpp>
#include <pacer/geometry/geometry.hpp>
#include <pacer/gps-source/gps-source.hpp>
#include <pacer/interpolation/interpolation.hpp>
#include <pacer/laps-display/laps-display.hpp>
#include <pacer/laps/laps.hpp>

// [Win32] Our example includes a copy of glfw3.lib pre-compiled with VS2010 to
// maximize ease of testing and compatibility with old VS compilers. To link
// with VS2010-era libraries, VS2015+ requires linking with
// legacy_stdio_definitions.lib, which we do using this pragma. Your own project
// should not be affected, as you are likely to link with a newer binary of GLFW
// that is adequate for your version of Visual Studio.
#if defined(_MSC_VER) && (_MSC_VER >= 1900) &&                                 \
    !defined(IMGUI_DISABLE_WIN32_FUNCTIONS)
#pragma comment(lib, "legacy_stdio_definitions")
#endif

// Web/Emscripten: HelloImGui drives the emscripten main loop itself (no manual
// emscripten_set_main_loop stub needed). Build with tools/build-web.sh.

using pacer::GPSSample;

namespace {

// Where telemetry comes from. Populated from CLI args / a JSON config instead
// of hard-coded paths, so the app runs on any machine.
struct InputConfig {
  std::vector<std::string> gpmf_files; // GoPro .MP4 (GPMF) inputs, in order
  std::string dat_file;                // optional u-blox .dat input
  pacer::DatVersion dat_version = pacer::DatVersion::WITH_TIMESTAMP;
  bool interpolate = true; // recover GPMF timestamps via gradient descent
};

bool EndsWithCI(const std::string &s, const std::string &suffix) {
  if (s.size() < suffix.size())
    return false;
  return std::equal(
      suffix.rbegin(), suffix.rend(), s.rbegin(), [](char a, char b) {
        return std::tolower((unsigned char)a) == std::tolower((unsigned char)b);
      });
}

pacer::DatVersion ParseDatVersion(const std::string &s) {
  return s == "just_data" ? pacer::DatVersion::JUST_DATA
                          : pacer::DatVersion::WITH_TIMESTAMP;
}

// Load a JSON config of the form:
//   { "gpmf_files": ["a.MP4", ...], "dat_file": "x.dat",
//     "dat_version": "with_timestamp" }
InputConfig LoadConfigFile(const std::string &path) {
  InputConfig cfg;
  std::ifstream f(path);
  if (!f) {
    std::cerr << "pacer: cannot open config '" << path << "'\n";
    return cfg;
  }
  try {
    nlohmann::json j;
    f >> j;
    if (j.contains("gpmf_files"))
      cfg.gpmf_files = j.at("gpmf_files").get<std::vector<std::string>>();
    if (j.contains("dat_file"))
      cfg.dat_file = j.at("dat_file").get<std::string>();
    if (j.contains("dat_version"))
      cfg.dat_version = ParseDatVersion(j.at("dat_version").get<std::string>());
    if (j.contains("interpolate"))
      cfg.interpolate = j.at("interpolate").get<bool>();
  } catch (const std::exception &e) {
    std::cerr << "pacer: failed to parse '" << path << "': " << e.what()
              << "\n";
  }
  return cfg;
}

// Resolve inputs from CLI args; positional .MP4/.dat are added directly and a
// .json arg is loaded as config. If nothing is given, fall back to
// $PACER_CONFIG, then ./pacer.json.
InputConfig ResolveConfig(int argc, char **argv) {
  InputConfig cfg;
  for (int i = 1; i < argc; ++i) {
    std::string arg = argv[i];
    if (EndsWithCI(arg, ".json")) {
      InputConfig file_cfg = LoadConfigFile(arg);
      cfg.gpmf_files.insert(cfg.gpmf_files.end(), file_cfg.gpmf_files.begin(),
                            file_cfg.gpmf_files.end());
      if (cfg.dat_file.empty())
        cfg.dat_file = file_cfg.dat_file;
      cfg.dat_version = file_cfg.dat_version;
    } else if (EndsWithCI(arg, ".mp4")) {
      cfg.gpmf_files.push_back(arg);
    } else if (EndsWithCI(arg, ".dat")) {
      cfg.dat_file = arg;
    } else {
      std::cerr << "pacer: ignoring unrecognized argument '" << arg << "'\n";
    }
  }
  if (cfg.gpmf_files.empty() && cfg.dat_file.empty()) {
    if (const char *env = std::getenv("PACER_CONFIG"))
      return LoadConfigFile(env);
    if (std::ifstream("pacer.json"))
      return LoadConfigFile("pacer.json");
  }
  return cfg;
}

// Owns every source object; `head` is the one to iterate. Sources are kept in
// unique_ptrs because GPMFSource owns an mp4 handle (must not be copied) and
// SequentialGPSSource stores raw pointers to its children (must outlive it).
struct GpsSourceChain {
  std::vector<std::unique_ptr<pacer::RawGPSSource>> owned;
  pacer::RawGPSSource *head = nullptr;
};

GpsSourceChain BuildGpmfChain(const std::vector<std::string> &files) {
  GpsSourceChain chain;
  std::vector<pacer::RawGPSSource *> sources;
  for (const auto &f : files) {
    chain.owned.push_back(std::make_unique<pacer::GPMFSource>(f.c_str()));
    sources.push_back(chain.owned.back().get());
  }
  if (sources.empty())
    return chain;
  pacer::RawGPSSource *head = sources[0];
  for (size_t i = 1; i < sources.size(); ++i) {
    chain.owned.push_back(
        std::make_unique<pacer::SequentialGPSSource>(head, sources[i]));
    head = chain.owned.back().get();
  }
  chain.head = head;
  return chain;
}

void ReadInput(pacer::Laps *plaps, const InputConfig &cfg) {
  auto &laps = *plaps;
  if (!cfg.gpmf_files.empty()) {
    GpsSourceChain chain = BuildGpmfChain(cfg.gpmf_files);
    pacer::RawGPSSource *m = chain.head;

    if (cfg.interpolate) {
      // Collect each sample with its frame's [start, end] span, then recover
      // accurate per-sample timestamps via gradient descent instead of the
      // naive even-spread-within-frame heuristic (see pacer/interpolation).
      std::vector<GPSSample> samples;
      std::vector<std::pair<double, double>> spans;
      for (m->Seek(0); !m->IsEnd(); m->Next()) {
        auto [start, end] = m->CurrentTimeSpan();
        m->Samples([&](GPSSample s, size_t, size_t) {
          if (s.full_speed > 1e-6) {
            samples.push_back(s);
            spans.emplace_back(start, end);
          }
        });
      }
      if (samples.empty())
        return;
      pacer::CoordinateSystem cs(samples.front());
      auto res = pacer::InterpolateTimestamps(samples, spans, cs);
      for (size_t i = 0; i < samples.size(); ++i)
        laps.AddPoint(samples[i], res.timestamps[i]);
    } else {
      // Naive fallback: assume samples are evenly spread across each frame
      // span.
      for (m->Seek(0); !m->IsEnd(); m->Next()) {
        auto [start, end] = m->CurrentTimeSpan();
        m->Samples([&](GPSSample s, size_t current, size_t total) {
          if (s.full_speed > 1e-6)
            laps.AddPoint(s, start + (end - start) / total * current);
        });
      }
    }
  } else if (!cfg.dat_file.empty()) {
    pacer::ReadDatFile(
        cfg.dat_file.c_str(),
        [&](pacer::GPSSample sample, double time) {
          laps.AddPoint(sample, time);
        },
        cfg.dat_version);
  }
}

} // namespace

// Main code
int main(int argc, char **argv) {
  InputConfig cfg = ResolveConfig(argc, argv);

  pacer::Laps full_laps;
  ReadInput(&full_laps, cfg);

  if (full_laps.PointCount() == 0) {
    std::cerr << "pacer: no telemetry loaded.\n"
                 "  usage: timeline [FILE.MP4 ...] [FILE.dat] [CONFIG.json]\n"
                 "  or set PACER_CONFIG, or create ./pacer.json. "
                 "Opening an empty timeline.\n";
  }

  full_laps.sectors.start_line = full_laps.PickRandomStart();
  auto laps = full_laps;

  auto laps_display = pacer::LapsDisplay{&laps};
  pacer::DeltaLapsComparison delta;

  auto implotContext = ImPlot::CreateContext();

  HelloImGui::Run(
      [&] {
        laps.Update();

        std::vector<GPSSample> gps;
        static float start = 0, end = full_laps.PointCount();

        if (ImGui::Begin("Data Subset")) {
          ImGui::Text("Select data subset to display on the map");
          ImGui::SetNextItemWidth(ImGui::GetWindowWidth() / 2);
          if (ImGui::SliderFloat("Start", &start, 0, end) ||
              (ImGui::SameLine(), ImGui::SliderFloat("End", &end, start,
                                                     full_laps.PointCount()))) {
            laps.ClearPoints();
            for (size_t i = start; i < end; ++i) {
              auto [gps, time] = full_laps.GetPoint(i);
              laps.AddPoint(gps, time);
            }
          }
        }
        ImGui::End();

        delta.cs = laps_display.cs;
        static int old_selected_lap = laps_display.selected_lap;
        if (old_selected_lap != laps_display.selected_lap) {
          float width = delta.reference_lap.width;
          delta.reference_lap = laps.GetLap(laps_display.selected_lap);
          delta.reference_lap.width = width;
        }

        if (ImGui::Begin("Map")) {
          if (ImPlot::BeginPlot("GPS", ImVec2(-1, -1), ImPlotFlags_Equal)) {
            laps_display.DisplayMap();
            auto getter = [](int index, void *data) {
              auto &[gps, ld] = *reinterpret_cast<
                  std::pair<std::vector<GPSSample> &, pacer::LapsDisplay &> *>(
                  data);
              return ld.ToImPlotPoint(gps[index]);
            };

            std::pair<std::vector<GPSSample> &, pacer::LapsDisplay &> data = {
                gps, laps_display};

            ImPlot::PlotScatterG("data", getter, &data, (int)gps.size());

            if (!gps.empty()) {
              std::stringstream ss;
              ss << "Speed: " << gps.back().full_speed * 3.6 << "km/h";
              auto point = laps_display.ToImPlotPoint(gps.back());
              ImPlot::PlotText(ss.str().data(), point[0], point[1]);
            }
            delta.PlotSticks();
            ImPlot::EndPlot();
          }
        }
        ImGui::End();

        if (ImGui::Begin("Laps")) {
          delta.DrawSlider();
          ImGui::SameLine();
          laps_display.DisplayTable();
        }
        ImGui::End();

        if (ImGui::Begin("Delta")) {
          delta.Display(laps);
        }
        ImGui::End();

        if (ImGui::Begin("Lap Telemetry")) {
          laps_display.DisplayLapTelemetry();
        }
        ImGui::End();
      },
      "Pacer Timeline", true);

  ImPlot::DestroyContext(implotContext);

  return 0;
}
