#-----------------------------------------------------------------------------------------------#
# OpenRGB Ambilight plugin                                                                      #
#                                                                                               #
#   Built with the same MSYS2 MinGW64 toolchain, Qt and headers as OpenRGB-patched\OpenRGB.exe  #
#   (the plugin shares RGBController objects with it, so the class layouts must match).        #
#   Use build.sh; the DLL goes to OpenRGB-patched\plugins\.                                     #
#-----------------------------------------------------------------------------------------------#

QT       += core gui widgets
TEMPLATE  = lib
CONFIG   += plugin c++17 release warn_on
CONFIG   -= debug debug_and_release
TARGET    = OpenRGBAmbilightPlugin

OPENRGB_DIR = $$PWD/../_orgb_build/OpenRGB

INCLUDEPATH +=                                                                                  \
    $$PWD/src                                                                                   \
    $$OPENRGB_DIR                                                                               \
    $$OPENRGB_DIR/RGBController                                                                 \
    $$OPENRGB_DIR/i2c_smbus                                                                     \
    $$OPENRGB_DIR/net_port                                                                      \
    $$OPENRGB_DIR/dependencies/json                                                             \

# Same Windows defines as OpenRGB.pro, plus the Windows 10 API level for the
# DPI, power and thread-QoS calls.
DEFINES -= UNICODE
DEFINES +=                                                                                      \
    _MBCS                                                                                       \
    WIN32                                                                                       \
    WIN32_LEAN_AND_MEAN                                                                         \
    NOMINMAX                                                                                    \
    _WIN32_WINNT=0x0A00                                                                         \
    WINVER=0x0A00                                                                               \

# No fused multiply-adds: the colour maths must round exactly like the
# Python original (tests/compare_colormath.py checks it bit for bit).
QMAKE_CXXFLAGS += -ffp-contract=off -Wno-cast-function-type

HEADERS +=                                                                                      \
    $$OPENRGB_DIR/OpenRGBPluginInterface.h                                                      \
    $$OPENRGB_DIR/ResourceManagerInterface.h                                                    \
    src/AmbilightEngine.h                                                                       \
    src/AmbilightPlugin.h                                                                       \
    src/AmbilightTab.h                                                                          \
    src/AudioMeter.h                                                                            \
    src/ColorMath.h                                                                             \
    src/DeviceScheduler.h                                                                       \
    src/DeviceSender.h                                                                          \
    src/DisplayPower.h                                                                          \
    src/GlobalHotkey.h                                                                          \
    src/Log.h                                                                                   \
    src/Monitors.h                                                                              \
    src/ScreenGrabber.h                                                                         \
    src/Settings.h                                                                              \
    src/UiText.h                                                                                \

SOURCES +=                                                                                      \
    src/AmbilightEngine.cpp                                                                     \
    src/AmbilightPlugin.cpp                                                                     \
    src/AmbilightTab.cpp                                                                        \
    src/AudioMeter.cpp                                                                          \
    src/ColorMath.cpp                                                                           \
    src/DeviceScheduler.cpp                                                                     \
    src/DeviceSender.cpp                                                                        \
    src/DisplayPower.cpp                                                                        \
    src/GlobalHotkey.cpp                                                                        \
    src/Log.cpp                                                                                 \
    src/Monitors.cpp                                                                            \
    src/ScreenGrabber.cpp                                                                       \
    src/Settings.cpp                                                                            \

LIBS += -ld3d11 -ldxgi -lole32 -luuid -luser32 -lgdi32 -ladvapi32

DESTDIR     = $$PWD/build/release
OBJECTS_DIR = $$PWD/build/obj
MOC_DIR     = $$PWD/build/moc
RCC_DIR     = $$PWD/build/rcc
