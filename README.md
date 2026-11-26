# OpenAmbilight

**English** | [Русский](README.ru.md)

Sync PC RGB with screen colors using OpenRGB.

![OpenAmbilight Preview](https://i.imgur.com/DGwdrtX.png)

## What it does
Captures the dominant color from your monitor and sends it to all RGB devices through OpenRGB. It works with RAM, fans, strips, motherboard LEDs, and any other device supported by OpenRGB.

## Two ways to run it

1. **Inside OpenRGB, as a plugin** (`plugin/`, what this PC uses). A C++ port
   of the Python app, loaded by the patched OpenRGB in `OpenRGB-patched/`: an
   "Ambilight" tab in the OpenRGB window, an Ambilight entry in OpenRGB's tray
   menu, `Ctrl+Alt+A`, and it starts together with OpenRGB at logon. No Python,
   no SDK connection, ~0.4% of one core for OpenRGB and Ambilight together.
   Build, install and tests: [plugin/README.md](plugin/README.md). The plugin
   must be built with the same compiler and headers as the OpenRGB it loads
   into.
2. **Standalone Python app** (`Ambilight.pyw`), talking to any OpenRGB through
   its SDK server — the rest of this page. When the plugin is installed next
   to it, double-clicking `Ambilight.pyw` offers to open OpenRGB instead (two
   copies would drive the LEDs twice); `--legacy` forces the old window.

## Device synchronization

The single-file Python app is `Ambilight.pyw`. **Sync all devices** is enabled
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

## Languages
Both the OpenRGB plugin and the standalone Python app (`Ambilight.pyw`) support **English** and **Russian**:
* Use the **Language / Язык** selector in the top-right corner of the UI to switch live between **Auto**, **English**, and **Русский**.
* In **Auto** mode, the plugin follows OpenRGB's language (updating live when you change it in OpenRGB settings), and `Ambilight.pyw` follows your Windows UI language.
* In CLI mode, pass `--lang en`, `--lang ru`, or `--lang auto`.

## Usage
```bash
Ambilight.pyw                        # open GUI (or double-click)
python Ambilight.pyw --cli           # headless mode (default 60 FPS)
python Ambilight.pyw --fps 30        # custom capture FPS
python Ambilight.pyw --monitor 2     # specific monitor
python Ambilight.pyw --width 1920    # monitor width hint
python Ambilight.pyw --brightness 70 # cap brightness at 70%
python Ambilight.pyw --no-sync       # independent device updates
python Ambilight.pyw --lang ru       # interface language (auto, en, ru)
python Ambilight.pyw --test          # test colors
python Ambilight.pyw --list          # list devices
python Ambilight.pyw --restore       # reset devices to default mode
```
Press `Ctrl+C` to stop the script.

## Hotkey (Optional)
The OpenRGB plugin and the Python window both register `Ctrl+Alt+A` (configurable)
themselves. For the old script-only setup, `ambilight_hotkey.ahk` (requires
[AutoHotkey v2](https://www.autohotkey.com/)) does the same — don't run it next
to either of them:
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

The plugin does the same in C++ inside the OpenRGB process and hands the colours straight to OpenRGB's device objects; its analysis is checked bit for bit against the Python functions (`plugin/tests/compare_colormath.py`).

---
## Support
If you find this project helpful, consider supporting its development:

[![Buy Me A Coffee](https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png)](https://buymeacoffee.com/vadson)
