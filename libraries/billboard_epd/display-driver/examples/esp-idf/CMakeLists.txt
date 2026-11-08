cmake_minimum_required(VERSION 3.16)
# Works in this repository and inside a standalone component ZIP.
if(EXISTS "${CMAKE_CURRENT_LIST_DIR}/../../../C/components/billboard_epd/CMakeLists.txt")
    set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../../../C/components/billboard_epd")
elseif(EXISTS "${CMAKE_CURRENT_LIST_DIR}/../../include/billboard_epd.h")
    set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../..")
endif()
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(billboard_driver_example)
