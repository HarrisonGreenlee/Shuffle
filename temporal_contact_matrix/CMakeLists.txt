cmake_minimum_required(VERSION 3.18)

project(temporal_contact_matrix LANGUAGES C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

# Default build type for single-config generators like Ninja
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Debug CACHE STRING "Build type" FORCE)
endif()

# Path to NCLS source/header
set(NCLS_SRC_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../external_lib/ncls/ncls/src")

# Build the DLL
add_library(temporal_contact_matrix SHARED
    temporal_contact_matrix.c
    ${NCLS_SRC_DIR}/intervaldb.c
)

# Includes (local headers + NCLS headers)
target_include_directories(temporal_contact_matrix PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${NCLS_SRC_DIR}
)

# Match Replay / your batch files: disable Python-specific path in intervaldb.c
target_compile_definitions(temporal_contact_matrix PRIVATE BUILD_C_LIBRARY)

# Output name exactly "temporal_contact_matrix.dll"
set_target_properties(temporal_contact_matrix PROPERTIES
    OUTPUT_NAME "temporal_contact_matrix"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"   # .dll
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"   # non-Windows shared libs
    ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"   # .lib import lib
)

# -------------------------------
# MSVC settings (Debug/Release)
# -------------------------------
if(MSVC)
    # General warning level
    target_compile_options(temporal_contact_matrix PRIVATE /W4 /nologo /FC)

    # Debug vs Release behavior (roughly mirrors your .bat files)
    target_compile_options(temporal_contact_matrix PRIVATE
        $<$<CONFIG:Debug>:/Od /Zi /Z7 /MDd>
        $<$<CONFIG:Release>:/O2 /Zi /Z7 /MD /DNDEBUG>
    )

    target_link_options(temporal_contact_matrix PRIVATE
        /NOLOGO
        /INCREMENTAL:NO
        $<$<CONFIG:Debug>:/DEBUG>
        $<$<CONFIG:Release>:/DEBUG>
    )

    # Optional AddressSanitizer in Debug (matches build_slicer_debug.bat intent)
    option(ENABLE_ASAN "Enable AddressSanitizer (MSVC, Debug)" ON)
    if(ENABLE_ASAN)
        target_compile_options(temporal_contact_matrix PRIVATE
            $<$<CONFIG:Debug>:/fsanitize=address /GS- /Gy->
        )
        target_link_options(temporal_contact_matrix PRIVATE
            $<$<CONFIG:Debug>:/fsanitize=address>
        )
    endif()
endif()