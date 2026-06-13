# OpenAmbilight

Sync PC RGB with screen colors using OpenRGB.

![OpenAmbilight Preview](https://i.imgur.com/DGwdrtX.png)

## What it does
Captures the dominant color from your monitor and sends it to all RGB devices through OpenRGB. It works with RAM, fans, strips, motherboard LEDs, and any other device supported by OpenRGB.

## Requirements
* **OS**: Windows 10/11
* **Python**: 3.10+ ([python.org](https://www.python.org/))
* **OpenRGB**: ([openrgb.org](https://openrgb.org/))

## Setup (3 minutes)
1. Install Python from [python.org](https://www.python.org/).
2. Run `install.bat` (this will automatically install all Python dependencies).
3. Open OpenRGB as **Administrator**:
   * Verify your hardware is detected.
   * Go to the **SDK Server** tab and click **Start Server**.
4. Test the device listing:
   ```bash
   python ambilight.py --list
   ```
   You should see your devices listed.
5. Test color cycles:
   ```bash
   python ambilight.py --test
   ```
   Colors should cycle on your devices.
6. Run Ambilight:
   ```bash
   python ambilight.py
   ```

## Usage
```bash
python ambilight.py                # default 15 FPS
python ambilight.py --fps 30       # faster updates
python ambilight.py --monitor 2    # specific monitor
python ambilight.py --width 1920   # monitor width hint
python ambilight.py --brightness 70  # cap brightness at 70%
python ambilight.py --test         # test colors
python ambilight.py --list         # list devices
python ambilight.py --restore      # reset devices to default mode
```
Press `Ctrl+C` to stop the script.

## Hotkey (Optional)
Run `ambilight_hotkey.ahk` (requires [AutoHotkey v2](https://www.autohotkey.com/)):
* `Ctrl+Alt+A`: Toggle Ambilight ON/OFF

## Troubleshooting
* **"Could not connect to OpenRGB SDK Server"**
  * Make sure OpenRGB is running.
  * Make sure the **SDK Server** is started in OpenRGB.
* **"Not all fans light up"**
  * Motherboard ARGB headers default to 0 LEDs.
  * Go to OpenRGB, find your header (e.g., JRAINBOW), and resize the zone to match the exact number of LEDs connected.

## How it works
The script captures your screen directly from the GPU using `dxcam` (Desktop Duplication API), which uses almost 0% CPU. It then calculates the most vibrant dominant color using `numpy`, smoothly transitions the colors, and sends commands over a local TCP connection to the OpenRGB SDK Server.
