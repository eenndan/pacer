macro(add_pacer_library name)
    # Parse keyword arguments: SOURCES and HEADERS
    set(options)
    set(oneValueArgs)
    set(multiValueArgs SOURCES HEADERS)
    cmake_parse_arguments(
        PACER            # Prefix for parsed variables
        "${options}"     # No flags
        "${oneValueArgs}"# No single-value args
        "${multiValueArgs}"
        ${ARGN}          # Input arguments (after `name`)
    )

    # Validate required arguments. SOURCES is OPTIONAL: a module with no .cpp is header-only and
    # is emitted as an INTERFACE library (no compiled artifact, just propagated include dirs).
    if(NOT DEFINED PACER_HEADERS)
        message(FATAL_ERROR "HEADERS not provided for target ${name}")
    endif()
    if(PACER_SOURCES)
        set(_pacer_header_only FALSE)
    else()
        set(_pacer_header_only TRUE)
    endif()

    # Create isolated include directory in the build tree
    set(include_dir "${CMAKE_CURRENT_BINARY_DIR}/include/pacer/${name}")
    set(include_prefix pacer/${name})
    file(MAKE_DIRECTORY "${include_dir}")

    # Copy headers to build include directory
    set(copied_headers)
    foreach(header IN LISTS PACER_HEADERS)
        get_filename_component(header_abs "${header}" ABSOLUTE)
        get_filename_component(header_name "${header}" NAME)
        set(output_header "${include_dir}/${include_prefix}/${header_name}")
        add_custom_command(
            OUTPUT "${output_header}"
            COMMAND ${CMAKE_COMMAND} -E create_symlink
                "${header_abs}"
                "${output_header}"
            DEPENDS "${header_abs}"
            COMMENT "Copying header ${header_name} to ${include_dir}"
        )
        list(APPEND copied_headers "${output_header}")
    endforeach()

    # Ensure headers are copied before building the library
    add_custom_target(pacer_${name}_headers DEPENDS ${copied_headers})

    if(_pacer_header_only)
        # Header-only module -> INTERFACE library: no compiled artifact, consumers just inherit
        # its include dirs (and, via add_dependencies, the header-copy step) when they link it.
        add_library(pacer_${name} INTERFACE)
        add_dependencies(pacer_${name} pacer_${name}_headers)
        target_include_directories(pacer_${name}
            INTERFACE
                $<BUILD_INTERFACE:${include_dir}>  # Public includes via build tree
        )
    else()
        # Create the library target
        add_library(pacer_${name} STATIC ${PACER_SOURCES} ${PACER_HEADERS})

        # Link headers copying to the target
        add_dependencies(pacer_${name} pacer_${name}_headers)

        # Set include directories
        target_include_directories(pacer_${name}
            # PRIVATE
            #     ${CMAKE_CURRENT_SOURCE_DIR}  # For private includes in source tree
            PUBLIC
                $<BUILD_INTERFACE:${include_dir}>  # Public includes via build tree
        )

        # put to folder pacer (xcode)
        set_property(TARGET pacer_${name} PROPERTY FOLDER "pacer")
    endif()

    # Create namespaced alias
    add_library(pacer::${name} ALIAS pacer_${name})

    set_property(TARGET pacer_${name}_headers PROPERTY FOLDER "headers helpers")

endmacro()

add_subdirectory(datatypes)
add_subdirectory(geometry)
add_subdirectory(gps-source)
add_subdirectory(laps)