/*---------------------------------------------------------*\
| DeviceScheduler.h                                         |
|                                                           |
|   When each device gets which colour. A port of the       |
|   AsyncColorSender scheduling in Ambilight.pyw:           |
|                                                           |
|   * Pacing. A device is sent at most one frame per        |
|     (its hardware latency x DEVICE_PACE). The MSI board,  |
|     keyboard and mouse share one USB root hub; a board    |
|     streaming back to back made the keyboard 15x slower.  |
|                                                           |
|   * Keep-alive. A device that already shows the colour is |
|     left alone, but re-sent at least every second (the    |
|     XPG Summoner blacks out after ~2 s without a frame).  |
|                                                           |
|   * Sync. Freeze ONE colour per hardware frame and hand   |
|     it to each device at (deadline - its latency), so     |
|     every device changes at the same moment.              |
|                                                           |
|   SchedulerCore is single-threaded and clock-free (the    |
|   caller passes `now`), which is what the tests drive.    |
\*---------------------------------------------------------*/

#pragma once

#include <functional>
#include <string>
#include <utility>
#include <vector>

#include "ColorMath.h"

namespace ambi
{

/*---------------------------------------------------------*\
| Hardware write durations in milliseconds, measured on     |
| this PC with an OpenRGB build that times every            |
| RGBController::DeviceUpdateLEDs() call:                   |
|                                                           |
|   Roccat Burst Pro            2 ms  one small HID report  |
|   XPG Summoner keyboard      31 ms  two 264-byte reports  |
|   Kingston Fury DDR5 DRAM   140 ms  SMBus + apply         |
|   MSI MYSTIC LIGHT          185 ms  one 727-byte report   |
|                                     per used header       |
|                                                           |
|   Keys match case-insensitively as substrings of the      |
|   device name, first match wins.                          |
\*---------------------------------------------------------*/
struct LatencyRule
{
    const char* key;
    double      ms;
};

extern const LatencyRule DEVICE_LATENCY_MS[4];

constexpr double DEFAULT_LATENCY_MS = 25.0;
constexpr double MSI_761_ZONE_MS    = 92.5;
constexpr double DEVICE_PACE        = 1.5;
constexpr double DEVICE_KEEPALIVE   = 1.0;

double DeviceLatencyMs(const std::string& name);

/*---------------------------------------------------------*\
| (alignment latency, busy time) for a device. The MSI 761  |
| driver writes one report per header in turn: aim other    |
| devices at the middle header but pace by the full time.   |
\*---------------------------------------------------------*/
void DeviceTimingMs(const std::string& name, const std::string& description,
                    const std::vector<unsigned int>& zone_led_counts,
                    double& latency_ms, double& busy_ms);

struct SchedEntry
{
    void*       target      = nullptr;      /* RGBController* in the plugin */
    int         id          = 0;
    std::string name;
    double      latency_ms  = 0.0;
    double      busy_ms     = 0.0;
    double      lat         = 0.0;          /* seconds                      */
    double      period      = 0.0;          /* seconds                      */
    double      next        = 0.0;
    bool        has_last    = false;
    RGBi        last_rgb;
    double      last_t      = 0.0;
};

typedef std::pair<SchedEntry*, RGBi> SchedWrite;

/*---------------------------------------------------------*\
| Writes the given colours and fills `sent` with the        |
| entries that were actually written                        |
\*---------------------------------------------------------*/
typedef std::function<void(const std::vector<SchedWrite>& writes, std::vector<SchedEntry*>& sent)> SchedDispatch;

class SchedulerCore
{
public:
    static SchedEntry MakeEntry(void* target, int id, const std::string& name, double latency_ms, double busy_ms);

    void    SetPlan(const std::vector<SchedEntry>& entries);
    void    ResetSchedule();
    void    Reset();
    void    SetDispatch(SchedDispatch fn) { dispatch = fn; }

    void    SetSync(bool enabled) { sync = enabled; }
    bool    GetSync() const       { return sync; }
    void    SetBrightness(double value) { brightness = value; }
    double  GetBrightness() const { return brightness; }
    void    SetLatest(double t, const RGBi& rgb) { latest_t = t; latest = rgb; has_latest = true; }

    double  Tick(double now);

    double  Slowest() const     { return slowest; }
    double  FramePeriod() const { return frame_period; }

    /*-----------------------------------------------------*\
    | State, public for the tests                           |
    \*-----------------------------------------------------*/
    struct Pending
    {
        SchedEntry* entry;
        double      at;
    };

    std::vector<SchedEntry> plan;
    bool                    frame_active    = false;
    bool                    frame_started   = false;
    RGBi                    frame_rgb;
    std::vector<Pending>    pending;

private:
    RGBi    Scale(const RGBi& rgb) const;

    SchedDispatch           dispatch;
    bool                    sync            = true;
    bool                    was_sync        = true;
    double                  brightness      = 1.0;
    bool                    has_latest      = false;
    RGBi                    latest;
    double                  latest_t        = 0.0;
    double                  slowest         = 0.0;
    double                  frame_period    = 0.0;
    double                  next_frame_at   = 0.0;

    std::vector<SchedWrite>  writes;
    std::vector<SchedEntry*> sent;
};

}
