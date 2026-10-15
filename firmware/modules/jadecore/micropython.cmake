# CMake build (ESP32 port): USER_C_MODULES=<repo>/firmware/modules/micropython.cmake
add_library(usermod_jadecore INTERFACE)
target_sources(usermod_jadecore INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/jadecore.c
    ${CMAKE_CURRENT_LIST_DIR}/jade_vfs.c
    ${CMAKE_CURRENT_LIST_DIR}/jade_idx.c
    ${CMAKE_CURRENT_LIST_DIR}/jade_bridge.c
)
target_include_directories(usermod_jadecore INTERFACE ${CMAKE_CURRENT_LIST_DIR})
target_link_libraries(usermod INTERFACE usermod_jadecore)
