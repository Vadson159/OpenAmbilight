# OpenAmbilight

Sync PC RGB with screen colors using OpenRGB.

![OpenAmbilight Preview](https://i.imgur.com/DGwdrtX.png)

## What it does
Captures the dominant color from your monitor and sends it to all RGB devices through OpenRGB. It works with RAM, fans, strips, motherboard LEDs, and any other device supported by OpenRGB.

## Device synchronization

The current single-file app is `Ambilight.pyw`. **Sync all devices** is enabled
by default: every device receives the same sampled frames, scheduled using
measured hardware delays. The shared update rate follows the slowest device
(about 3.6 FPS on this PC), independently of the screen capture FPS. Separate
MSI ARGB headers are written sequentially; other devices align between them.
`python Ambilight.pyw --cli --no-sync` restores independent updates.

Measurements, remaining hardware limits, and reproduction steps:
[synchronization report](diagnostics/sync-2026-10-23/REPORT.md).

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
   python Ambilight.pyw --list
   ```
   You should see your devices listed.
5. Test color cycles:
   ```bash
   python Ambilight.pyw --test
   ```
   Colors should cycle on your devices.
6. Run Ambilight:
   Double-click `Ambilight.pyw` to open the GUI, or run headless CLI mode:
   ```bash
   python Ambilight.pyw --cli
   ```

## Usage
```bash
Ambilight.pyw                        # open GUI (or double-click)
python Ambilight.pyw --cli           # headless mode (default 60 FPS)
python Ambilight.pyw --fps 30        # custom capture FPS
python Ambilight.pyw --monitor 2     # specific monitor
python Ambilight.pyw --width 1920    # monitor width hint
python Ambilight.pyw --brightness 70 # cap brightness at 70%
python Ambilight.pyw --no-sync       # independent device updates
python Ambilight.pyw --test          # test colors
python Ambilight.pyw --list          # list devices
python Ambilight.pyw --restore       # reset devices to default mode
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
The script captures your screen through the Windows Desktop Duplication API and shrinks each frame on the GPU, so only a thumbnail ever reaches the CPU and capture uses almost 0% CPU. It then calculates the most vibrant dominant color using `numpy`, smoothly transitions the colors, and sends commands over a local TCP connection to the OpenRGB SDK Server.

---
## Support
If you find this project helpful, consider supporting its development:

[![Buy Me A Coffee](https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png)](https://buymeacoffee.com/vadson)
