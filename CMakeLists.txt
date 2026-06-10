cmake_minimum_required(VERSION 3.28)

project(pacer)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

set_property(GLOBAL PROPERTY USE_FOLDERS ON)

# Python resolution must be deterministic in two hostile setups:
#   1. Locally, a developer shell may export VIRTUAL_ENV (e.g. ~/.venvs/main). FindPython's
#      default (FIRST) would hijack the build to that venv, which lacks litgen/nanobind.
#   2. On CI (and any machine with Homebrew/system Pythons), STANDARD's plain PATH lookup can
#      pick a newer interpreter outside the pixi env (e.g. Homebrew python3.14) — that one has
#      no litgen either, so the bindings codegen step fails.
# Fix: when running under `pixi run` (which always exports CONDA_PREFIX pointing at
# .pixi/envs/default), pin the interpreter to the pixi env explicitly. This solves both cases.
# An explicit -DPython_EXECUTABLE=... on the command line still wins.
if(DEFINED ENV{CONDA_PREFIX} AND NOT DEFINED Python_EXECUTABLE)
  set(Python_EXECUTABLE "$ENV{CONDA_PREFIX}/bin/python3")
endif()
# Fallback for non-pixi invocations: standard PATH-based lookup instead of VIRTUAL_ENV-first.
# (STANDARD, not NEVER — NEVER is not a valid value; accepted values are FIRST/ONLY/STANDARD.)
set(Python_FIND_VIRTUALENV STANDARD)

find_package(Python 3.13
  REQUIRED COMPONENTS Interpreter Development.Module
  OPTIONAL_COMPONENTS Development.SABIModule)

find_package(Catch2 REQUIRED)

enable_testing()  # required for add_test() in tests/ to register with CTest

add_subdirectory(3rdparty)
add_subdirectory(pacer)
add_subdirectory(tests)
add_subdirectory(bindings)
