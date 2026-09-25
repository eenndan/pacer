set(litgen_cmake_help_message "

This Cmake module provides several public functions:

litgen_find_python
******************
litgen_find_python() will find Python >= 3.8 with the interpreter and Development module.
(and working around quirks of CMake 3.18 and below)

When building via CMake, you may have to specify Python_EXECUTABLE via
     -DPython_EXECUTABLE=/path/to/your/venv/bin/python

litgen_find_nanobind
*********************
litgen_find_nanobind() will find nanobind and Python3

litgen_setup_module(bound_library python_native_module_name python_module_name)
*******************************************************************************
litgen_setup_module is a helper function that will:
* link the python native module (.so) to the bound C++ library (bound_library)
* set the install path of the native module to '.' (so that pip install works)
* set the VERSION_INFO macro to the project version defined in CMakeLists.txt
* when building as a C++ project (i.e. not with skbuild), it will copy the python module
  to the editable_bindings_folder and to the platform dependent installation directory
  (site-packages when using pip install), so that it is used by our next runs of python.


Note: how to specify the version of Python to use
*************************************************
When building via CMake, you may have to specify Python_EXECUTABLE via
     -DPython_EXECUTABLE=/path/to/your/venv/bin/python
")

macro(litgen_find_python)
    # cf https://nanobind.readthedocs.io/en/latest/building.html
    if (CMAKE_VERSION VERSION_LESS 3.18)
        set(DEV_MODULE Development)
    else()
        set(DEV_MODULE Development.Module)
    endif()

    find_package(Python 3.8 COMPONENTS Interpreter ${DEV_MODULE} REQUIRED)
endmacro()

macro(litgen_find_nanobind)
    litgen_find_python()

    # Detect the installed nanobind package and import it into CMake
    execute_process(
        COMMAND "${Python_EXECUTABLE}" -m nanobind --cmake_dir
        OUTPUT_STRIP_TRAILING_WHITESPACE OUTPUT_VARIABLE nanobind_ROOT)
    # find_package(nanobind CONFIG REQUIRED)

    if (NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
        set(CMAKE_BUILD_TYPE Release CACHE STRING "Choose the type of build." FORCE)
        set_property(CACHE CMAKE_BUILD_TYPE PROPERTY STRINGS "Debug" "Release" "MinSizeRel" "RelWithDebInfo")
    endif()
endmacro()


# Copy the built native module to `destination` only when its bytes differ (HEALTH-1, 2026-09-25).
# Both copies were `add_custom_target(... ALL COMMAND copy)`, which runs on EVERY build: a no-op
# `pixi run build` (and so every `pixi run studio`) rewrote both .so files. The stamp makes an
# unchanged module a no-op; declaring `destination` a byproduct makes a deleted copy come back on
# the next build. A copy that does happen still lands on a new inode (measured), so a running app
# keeps the library it mapped.
function(_litgen_deploy_native_module target_name native_module destination_dir)
    # The module's file name, spelled out: BYPRODUCTS takes no $<TARGET_FILE_NAME:…>. nanobind sets
    # PREFIX "" and SUFFIX (the interpreter's EXT_SUFFIX) on the target it adds.
    get_target_property(prefix ${native_module} PREFIX)
    get_target_property(suffix ${native_module} SUFFIX)
    if(NOT suffix)
        message(FATAL_ERROR "litgen.cmake: ${native_module} has no SUFFIX property, so its deployed "
                            "file name cannot be spelled out (was it made by nanobind_add_module?)")
    endif()
    if(NOT prefix)
        set(prefix "")
    endif()
    set(destination "${destination_dir}/${prefix}${native_module}${suffix}")
    set(stamp "${CMAKE_CURRENT_BINARY_DIR}/${target_name}.stamp")
    add_custom_command(
        OUTPUT "${stamp}"
        BYPRODUCTS "${destination}"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different $<TARGET_FILE:${native_module}> "${destination}"
        COMMAND ${CMAKE_COMMAND} -E touch "${stamp}"
        DEPENDS ${native_module}
        VERBATIM
    )
    add_custom_target(${target_name} ALL DEPENDS "${stamp}")
endfunction()


function(litgen_setup_module
    # Parameters explanation, with an example: let's say we want to build binding for a C++ library named "foolib",
    bound_library               #  name of the C++ for which we build bindings ("foolib")
    python_native_module_name   #  name of the native python module that provides bindings (for example "_foolib")
    python_module_name          #  name of the standard python module that will import the native module (for example "foolib")
    editable_bindings_folder    # path to the folder containing the python bindings for editable mode (for example "_stubs/")
)
    target_link_libraries(${python_native_module_name} PRIVATE ${bound_library})

    # Set python_native_module_name install path to ${python_module_name} (required by skbuild)
    install(TARGETS ${python_native_module_name} DESTINATION ${python_module_name})

    # Set VERSION_INFO macro to the project version defined in CMakeLists.txt (absolutely optional)
    target_compile_definitions(${python_native_module_name} PRIVATE VERSION_INFO=${PROJECT_VERSION})

    if (NOT SKBUILD)
        # If we are **not** building with skbuild, it means that we are **not** building a wheel for pipy or conda.
        #
        # Instead, we are building as a standard C++ project: in this case, we want to deploy our compiled module
        # so that it is used by our next runs of python.
        #
        # We will copy it into two different locations, to cover all cases:
        # - 1. ${editable_bindings_folder}: the user selected binding folder
        #      (if the user did *manually* prepend it to his python path)
        # - 2. ${Python_SITEARCH}: the platform dependent installation directory
        #      (site-packages when using pip install)

        # 1. Copy the python module to editable_bindings_folder
        set(bindings_module_folder ${editable_bindings_folder}/${python_module_name})
        _litgen_deploy_native_module(${python_module_name}_deploy_editable
            ${python_native_module_name} ${bindings_module_folder})

        # 2. Copy the python module to the platform dependent installation directory (site-packages when using pip install)
        # We'll rely on find_package(Python) which fills Python_SITEARCH, which is where we want to copy the module
        litgen_find_python()  # will call find_package(Python) and set Python_SITEARCH
        _litgen_deploy_native_module(${python_module_name}_deploy_editable_site_packages
            ${python_native_module_name} ${Python_SITEARCH}/${python_module_name})
        message(STATUS "litgen_setup_module: python native module will be copied to ${Python_SITEARCH}/${python_module_name}")
    endif(NOT SKBUILD)
endfunction()
