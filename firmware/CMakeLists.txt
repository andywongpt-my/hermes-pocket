cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(hermes-pocket)

# App-only artifact boundary. This target performs host file checks only.
add_custom_target(check_app_bounds ALL
    COMMAND "${PYTHON}" "${CMAKE_CURRENT_LIST_DIR}/tools/check_app_bounds.py"
            --app "${CMAKE_BINARY_DIR}/${PROJECT_NAME}.bin"
            --partitions "${CMAKE_CURRENT_LIST_DIR}/partitions.csv"
    DEPENDS gen_project_binary
    VERBATIM)
add_dependencies(app check_app_bounds)
