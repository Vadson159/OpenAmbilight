#!/usr/bin/env bash
# Builds the plugin's test programs with the MSYS2 MinGW64 toolchain that
# builds OpenRGB. Run from the MinGW64 shell:
#   MSYSTEM=MINGW64 _orgb_build/msys64/usr/bin/bash.exe -lc plugin/tests/build_tests.sh
set -e
cd "$(dirname "$0")"
SRC=../src
OUT=../build/tests
mkdir -p "$OUT"
# -ffp-contract=off: no fused multiply-adds, so the colour maths rounds
# exactly like CPython. Static so the programs run outside the MinGW shell.
FLAGS="-std=c++17 -O2 -ffp-contract=off -Wall -Wextra -static -D_WIN32_WINNT=0x0A00 -DWINVER=0x0A00 -I$SRC"

g++ $FLAGS colormath_cli.cpp  $SRC/ColorMath.cpp -o "$OUT/colormath_cli.exe"
g++ $FLAGS test_scheduler.cpp $SRC/ColorMath.cpp $SRC/DeviceScheduler.cpp -o "$OUT/test_scheduler.exe"
if [ -f test_capture.cpp ]; then
  g++ $FLAGS test_capture.cpp $SRC/ScreenGrabber.cpp $SRC/Monitors.cpp $SRC/ColorMath.cpp \
      -o "$OUT/test_capture.exe" -ld3d11 -ldxgi -lgdi32 -luser32 -lole32
fi
if [ -f test_audio.cpp ]; then
  g++ $FLAGS test_audio.cpp $SRC/AudioMeter.cpp $SRC/ScreenGrabber.cpp $SRC/Monitors.cpp $SRC/ColorMath.cpp       -o "$OUT/test_audio.exe" -ld3d11 -ldxgi -lgdi32 -luser32 -lole32 -luuid
fi
echo "tests built in $OUT"
