# Ambilight — OpenRGB plugin

Ambilight runs inside the patched OpenRGB (`..\OpenRGB-patched`) as a native
plugin, `OpenRGBAmbilightPlugin.dll`. It is a C++ port of `..\Ambilight.pyw`:
same capture, same colour maths, same per-device scheduler, same settings.

## Where to find it

- **Tab "Ambilight"** in the OpenRGB window: Start/Stop, status, a swatch of
  the colour on the LEDs, and every setting of the old window. Changes apply
  live and are saved at once.
- **OpenRGB tray menu → Ambilight**: Start/Stop, Music mode, Settings….
- **Ctrl+Alt+A** (configurable): toggles Ambilight from anywhere.
- Double-clicking `OpenRGB.exe` (or `..\Ambilight.lnk`) brings the running
  OpenRGB window forward instead of opening a second copy.

The tab and tray menu support **English** and **Russian** (`src/UiText.h`).
By default (**Auto (OpenRGB)**), the plugin follows OpenRGB's active
translation and retranslates live when you switch OpenRGB's language in
Settings. You can also choose **English** or **Русский** explicitly in the
language selector at the top right of the Ambilight tab (saved as `"language"`
in `Ambilight.json`).

## Autostart

OpenRGB starts at logon through the scheduled task **"OpenRGB (elevated)"**
(`OpenRGB.exe --startminimized --server --noautoconnect`, 10 s after logon,
elevated so the RGB RAM works). OpenRGB loads the plugin once detection is
done, and the plugin starts Ambilight when "Start Ambilight when OpenRGB
starts" is on (default). The schedule (start 18:00 / stop 02:00 here) then
starts and stops it once a day. Nothing else needs to be in autostart; the
old Python app must not be.

## What changed against Ambilight.pyw

- Devices are driven in-process (`SetAllLEDs` + `UpdateLEDs`), not through
  the SDK socket. The scheduler (latency table, pacing, keep-alive, sync) is
  the same; the SDK server keeps running for other clients.
- No Python: one D3D11 device in OpenRGB, ~0.2 ms CPU per captured frame.
  OpenRGB with Ambilight running: ~0.4% of one core, 78 MB working set
  (before: OpenRGB 43 MB + Python app 83 MB and 1.3–2.4%).
- "Turn off with display" also covers Windows putting the display to sleep
  (GUID_CONSOLE_DISPLAY_STATE), not only the lock screen.
- The monitor can be changed while running; music mode retries a speaker
  that failed to open; the schedule fires once per day (a manual stop right
  after the start time is no longer undone two minutes later).
- The direct-HID keyboard fallback is gone: OpenRGB drives the XPG Summoner.

## Files

- Settings: `OpenRGB-patched\config\plugins\settings\Ambilight.json`
  (first run imported `..\config.json` of the Python app).
- Log: `OpenRGB-patched\config\logs\Ambilight.log` (≤ 1 MB, one `.1` kept).
- Plugin enable flag: OpenRGB → Settings → Plugins.

## Build and install

Same MSYS2 MinGW64 toolchain and Qt as OpenRGB (`..\_orgb_build\msys64`) —
required, the plugin shares C++ objects with `OpenRGB.exe`, so it must be
built against the same headers and compiler.

```powershell
$env:MSYSTEM='MINGW64'; & S:\_Vibe_Coding\Ambilight\_orgb_build\msys64\usr\bin\bash.exe -lc /s/_Vibe_Coding/Ambilight/plugin/build.sh
powershell -ExecutionPolicy Bypass -File S:\_Vibe_Coding\Ambilight\plugin\install-plugin.ps1
```

`install-plugin.ps1` stops OpenRGB through its task, copies the DLL to
`OpenRGB-patched\plugins\` and starts it again. Rollback: delete that DLL
and restart the task.

If `RGBController.h`, `ResourceManagerInterface.h` or
`OpenRGBPluginInterface.h` change in `_orgb_build\OpenRGB`, rebuild and
reinstall OpenRGB and the plugin together.

## Tests

```powershell
$env:MSYSTEM='MINGW64'; & S:\_Vibe_Coding\Ambilight\_orgb_build\msys64\usr\bin\bash.exe -lc /s/_Vibe_Coding/Ambilight/plugin/tests/build_tests.sh
```

- `build\tests\test_scheduler.exe` — `tests\test_color_sender.py` ported to
  the C++ scheduler.
- `tests\compare_colormath.py --cli build\tests\colormath_cli.exe --work <dir>`
  — runs the frame analysis of Ambilight.pyw and of the plugin on ~2700
  generated frames and compares every number (bit for bit).
- `build\tests\test_capture.exe` — the GPU thumbnail equals a full readback
  sampled every 20th pixel on the same frame, recovery after the desktop is
  lost, GDI fallback, CPU per frame. Flickers a 2×2 px corner popup to get
  frames; nothing visible, nothing sent to OpenRGB.
- `build\tests\test_audio.exe` — music mode's WASAPI loopback meter on the
  real speakers for 3 s (block pacing, silence handling); no LEDs touched.
- `tests\render_tab.pro` — renders the tab to a PNG off screen with a
  stand-in ResourceManager (no devices touched). An optional 6th argument
  loads an OpenRGB `.qm` translation (`lrelease-qt5` on
  `_orgb_build\OpenRGB\qt\i18n\OpenRGB_ru_RU.ts`) to check the Russian UI.

## Host-side changes in OpenRGB

`..\OpenRGB-patched\patches\ambilight-plugin-host.patch`:
- PluginManager no longer registers new plugins as disabled.
- A second launch of `OpenRGB.exe` shows the running instance's window and
  exits.
- A rescan requested while the previous detection is still finishing is
  ignored, instead of handing listeners the old controllers and deleting them.
- `RGBController`'s update-callback list is only changed under its mutex.

## Known limits

- Resizing a zone in OpenRGB reallocates that device's colour buffer; an SDK
  client or this plugin writing at that instant could touch the old buffer.
  OpenRGB has no lock for it. Stop Ambilight before resizing zones.
- Stop blocks the GUI for at most one capture wait: 50 ms at 60 FPS, up to
  3 s at 1 FPS.
