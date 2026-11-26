#!/usr/bin/env bash
# Builds OpenRGBAmbilightPlugin.dll with the MSYS2 MinGW64 toolchain that
# builds OpenRGB-patched (same compiler, same Qt -- required, the plugin
# shares C++ objects with OpenRGB.exe). From PowerShell:
#
#   $env:MSYSTEM='MINGW64'; & S:\_Vibe_Coding\Ambilight\_orgb_build\msys64\usr\bin\bash.exe -lc /s/_Vibe_Coding/Ambilight/plugin/build.sh
#
# Then install it with install-plugin.ps1.
set -e
cd "$(dirname "$0")"
mkdir -p build
cd build
qmake-qt5 ../OpenRGBAmbilightPlugin.pro
# msys make, not mingw32-make (see _orgb_build/_build.sh)
/usr/bin/make -j"$(nproc)"
ls -la release/OpenRGBAmbilightPlugin.dll
