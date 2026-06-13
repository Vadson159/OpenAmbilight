=============================================
 Ambilight for OpenRGB
 Sync PC RGB with screen colors
=============================================

WHAT IT DOES:
  Captures dominant color from your monitor and
  sends it to all RGB devices through OpenRGB.
  Works with RAM, fans, strips, motherboard LEDs,
  and any other device OpenRGB supports.

REQUIREMENTS:
  - Windows 10/11
  - Python 3.10+ (python.org)
  - OpenRGB (openrgb.org)

SETUP (3 min):
  1. Install Python from python.org
  2. Run install.bat (installs Python dependencies)
  3. Open OpenRGB as Administrator
     * Make sure your hardware is detected.
     * Go to "SDK Server" tab and click "Start Server"
  4. Test:
       python ambilight.py --list
     You should see your devices listed.
  5. Color test:
       python ambilight.py --test
     Colors should cycle on your devices.
  6. Run ambilight:
       python ambilight.py

USAGE:
  python ambilight.py                # default 15 FPS
  python ambilight.py --fps 30       # faster updates
  python ambilight.py --monitor 2    # specific monitor
  python ambilight.py --width 1920   # monitor width hint
  python ambilight.py --brightness 70  # cap brightness at 70%
  python ambilight.py --test         # test colors
  python ambilight.py --list         # list devices
  python ambilight.py --restore      # reset devices to default mode
  Ctrl+C                             # stop ambilight

HOTKEY (optional):
  Run ambilight_hotkey.ahk (requires AutoHotkey v2)
  Ctrl+Alt+A = Toggle ambilight ON/OFF

TROUBLESHOOTING:
  "Could not connect to OpenRGB SDK Server"
    -> Make sure OpenRGB is running.
    -> Make sure "SDK Server" is started in OpenRGB.

  "Not all fans light up"
    -> Motherboard ARGB headers default to 0 LEDs.
    -> Go to OpenRGB, find your header (JRAINBOW, etc.),
       and resize the zone to match the number of LEDs.

HOW IT WORKS:
  The script captures your screen directly from the GPU using 'dxcam'
  (Desktop Duplication API), which uses almost 0% CPU. It then calculates
  the most vibrant dominant color using numpy, smoothly transitions the colors,
  and sends commands over a local TCP connection to the OpenRGB SDK Server.
