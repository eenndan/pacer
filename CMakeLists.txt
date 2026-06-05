cmake_minimum_required(VERSION 3.28)

project(pacer)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

set_property(GLOBAL PROPERTY USE_FOLDERS ON)

if(EMSCRIPTEN)
  # Web/WASM build (see tools/build-web.sh). HelloImGui supplies GLFW/OpenGL via
  # emscripten ports, so we skip the system find_package() calls and the
  # Python bindings / native tests / examples. Build only the core libraries and
  # the timeline app. NOTE: file-based ingestion (GPMF/.dat via fopen) needs data
  # preloaded into emscripten's virtual FS; this target is groundwork, not a
  # finished web app.
  add_subdirectory(3rdparty)
  add_subdirectory(pacer)
  add_subdirectory(apps)
else()
  find_package(Python 3.13
    REQUIRED COMPONENTS Interpreter Development.Module
    OPTIONAL_COMPONENTS Development.SABIModule)

  find_package(glfw3 REQUIRED)
  find_package(OpenGL REQUIRED)
  find_package(Catch2 REQUIRED)

  enable_testing()  # required for add_test() in tests/ to register with CTest

  add_subdirectory(3rdparty)
  add_subdirectory(pacer)
  add_subdirectory(apps)
  add_subdirectory(examples)
  add_subdirectory(tests)
  add_subdirectory(bindings)
endif()
