# XPG Summoner Gaming Keyboard — Direct mode does not apply colors (wrong HID report type)

Related to issue #2145 (XPG Summoner). Controller added Aug 2025 by Erick Granados.

## Summary
On the XPG Summoner (VID `0x125F`, PID `0x9418`), OpenRGB detects and registers the
keyboard, switches to Direct mode, and sends color packets — but the LEDs never change.
The keyboard's own software (XPG PRIME) controls it fine, so the hardware works.

## Root cause
The keyboard's lighting interface (USB interface `MI_02`, HID usage page `0xFF01`)
exposes **only a HID Feature report**, not an Output report. Its report descriptor:

```
06 01 FF   Usage Page (Vendor 0xFF01)
09 01      Usage (0x01)
A1 01      Collection (Application)
85 07        Report ID (0x07)
09 31        Usage (0x31)
15 00 26 FF 00   Logical Min/Max 0..255
75 08        Report Size 8
96 07 01     Report Count 263
B1 02        Feature (Data,Var,Abs)   <-- FEATURE, not Output
C0         End Collection
```

`XPGSummonerController` sends the color/init/terminate packets with `hid_write()`
(an **Output** report). Because this interface has no Output report, the writes are
silently dropped and the LEDs never update. The data must be sent as a **Feature**
report via `hid_send_feature_report()`. The total length is **264** bytes
(1 report-id byte + 263 data bytes), not 265.

## Fix
`Controllers/XPGSummonerKeyboardController/XPGSummonerController.cpp` — replace the three
`hid_write(dev, buf, 265)` calls with `hid_send_feature_report(dev, buf, 264)` (in
`SendInitialize`, `SendColorDataPacket`, and `SendTerminateColorPacket`). See the attached
`xpg-summoner-featurereport.patch`.

```diff
-    hid_write(dev, init_buf, 265);
+    hid_send_feature_report(dev, init_buf, 264);
...
-    hid_write(dev, packet, 265);
+    hid_send_feature_report(dev, packet, 264);
...
-    hid_write(dev, terminate_buf, 265);
+    hid_send_feature_report(dev, terminate_buf, 264);
```

## Verification
Built OpenRGB from commit `f89cdac` with only this change. After the fix, the XPG Summoner
responds to Direct-mode color changes from both the OpenRGB GUI and the SDK (cycled
RED/GREEN/BLUE/WHITE/OFF across all keys). No other devices affected. The same payload sent
as a Feature report from a standalone Python `hidapi` script also works, confirming the
report-type is the only issue.

## Second issue: Direct-mode firmware timeout (keep-alive)
After the report-type fix, a second behavior surfaced: the Summoner's firmware reverts the
LEDs to OFF if it does not receive a color frame within ~2 seconds. So a color set once
(e.g. from the OpenRGB GUI, left idle) lights up but goes dark after ~2 s; a continuous
stream (XPG PRIME, or OpenRGB driven by an effect/SDK at >0.5 Hz) stays lit. Verified by
holding a single frame (goes off ~2 s) vs. streaming the same color (stays lit).

The attached patch also adds a small **keep-alive thread** to `XPGSummonerController` that
re-sends the last frame every ~800 ms (guarded by a mutex shared with `SendColors`), so a
static color persists. This part is optional/separable from the report-type fix.

System: MSI MAG X870E TOMAHAWK WIFI (AM5), Windows 11, OpenRGB 0.9+ (git1926).
