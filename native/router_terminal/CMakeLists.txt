cmake_minimum_required(VERSION 3.16)
project(RouterManagerTerminal LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_C_STANDARD 17)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
if(NOT ROUTERMANAGER_QT_VERSION MATCHES "^[0-9]+[.][0-9]+[.][0-9]+$")
    message(FATAL_ERROR "Use scripts/build_linux_terminal.py with the reviewed Qt version")
endif()
find_package(Qt6 ${ROUTERMANAGER_QT_VERSION} EXACT REQUIRED COMPONENTS Core Gui Widgets)
file(GLOB VTERM_SOURCES CONFIGURE_DEPENDS vendor/libvterm/src/*.c)
add_library(routermanager_terminal SHARED
    bridge.cpp
    vendor/kodoterm/src/KodoTerm.cpp
    vendor/kodoterm/src/KodoTermConfig.cpp
    vendor/kodoterm/include/KodoTerm/KodoTerm.hpp
    vendor/kodoterm/src/PtyProcess.h
    vendor/kodoterm/KodoTermThemes.qrc
    ${VTERM_SOURCES})
target_include_directories(routermanager_terminal PRIVATE
    vendor/kodoterm/include vendor/kodoterm/src
    vendor/libvterm/include vendor/libvterm/src)
target_link_libraries(routermanager_terminal PRIVATE Qt6::Core Qt6::Gui Qt6::Widgets)
# PySide loads the packaged Qt first; do not embed a developer SDK absolute RPATH.
set_target_properties(routermanager_terminal PROPERTIES SKIP_BUILD_RPATH TRUE)
if(MSVC)
    target_compile_definitions(routermanager_terminal PRIVATE _CRT_SECURE_NO_WARNINGS)
endif()
if(WIN32)
    set_target_properties(routermanager_terminal PROPERTIES OUTPUT_NAME winbox_terminal)
endif()
