cmake_minimum_required(VERSION 3.18)

project(BuildContactGraph LANGUAGES C)

# Build settings
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

# Default build type
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Debug CACHE STRING "Build type" FORCE)
endif()

# Path to NCLS sources (adjust if your folder differs)
set(NCLS_SRC_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../external_lib/ncls/ncls/src")

# Sources
set(SOURCES
    build_contact_graph.c
    ${NCLS_SRC_DIR}/intervaldb.c
)

add_executable(build_contact_graph ${SOURCES})

# Include paths
target_include_directories(build_contact_graph PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}   # uthash.h
    ${NCLS_SRC_DIR}               # intervaldb.h
)

# disable Python.h path in intervaldb
target_compile_definitions(build_contact_graph PRIVATE BUILD_C_LIBRARY)

# OpenMP (optional but preferred)
find_package(OpenMP)
if(OpenMP_C_FOUND)
    target_link_libraries(build_contact_graph PRIVATE OpenMP::OpenMP_C)
endif()

# MSVC-specific debug-ish settings
if(MSVC)
    target_compile_options(build_contact_graph PRIVATE /Zi /Od)
    target_link_options(build_contact_graph PRIVATE /DEBUG /NODEFAULTLIB:libcmtd.lib)
endif()