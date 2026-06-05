include(litgen.cmake)

# The imgui Python bindings (bindings/imgui/nanobind_imgui.cpp) are auto-generated
# by litgen from imgui.h. They are currently STALE: hello_imgui now bundles imgui
# 1.92.x, whose dynamic-font / ImTextureRef API the committed generated code predates,
# so it no longer compiles. The package is also unused (nothing imports `imgui`).
# It is therefore opt-in until regenerated against imgui 1.92.x. See AGENTS.md.
option(PACER_BUILD_IMGUI_BINDINGS
  "Build the imgui Python bindings (needs regeneration against imgui 1.92.x)" OFF)

if(PACER_BUILD_IMGUI_BINDINGS)
  add_subdirectory(imgui)
endif()
add_subdirectory(pacer)
