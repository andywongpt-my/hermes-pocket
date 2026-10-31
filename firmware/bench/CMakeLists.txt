cmake_minimum_required(VERSION 3.16)
# Separate offline image: never includes the normal application/Live transport.
set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../components/bsp")
set(COMPONENTS main)
set(PROJECT_VER "bench-1")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(hermes-pocket-bench)
