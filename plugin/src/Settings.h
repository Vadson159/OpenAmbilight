/*---------------------------------------------------------*\
| Settings.h                                                |
|                                                           |
|   Ambilight's settings, shared between the Qt tab (which  |
|   edits them) and the capture thread (which reads a copy  |
|   whenever the version moves -- the "live" settings of    |
|   the old GUI).                                           |
\*---------------------------------------------------------*/

#pragma once

#include <atomic>
#include <cstdint>
#include <functional>
#include <mutex>
#include <string>

#include "ColorMath.h"

namespace ambi
{

struct AmbilightSettings
{
    /*-----------------------------------------------------*\
    | Capture                                               |
    \*-----------------------------------------------------*/
    std::wstring monitor_device;            /* \\.\DISPLAY1; empty = by index */
    int          monitor_index          = 0;
    int          fps                    = 60;
    Algo         algo                   = Algo::Dominant;
    bool         edges                  = false;
    bool         bars                   = false;
    int          black_threshold        = 20;

    /*-----------------------------------------------------*\
    | Colour                                                |
    \*-----------------------------------------------------*/
    int          brightness             = 100;  /* 1-100 */
    int          min_brightness         = 0;    /* 0-100 */
    int          smoothness             = 55;   /* 1-100 */
    bool         saturation             = false;
    bool         gamma                  = false;
    bool         hdr                    = false;

    /*-----------------------------------------------------*\
    | Devices and behaviour                                 |
    \*-----------------------------------------------------*/
    bool         sync_devices           = true;
    bool         display_sync           = true;
    bool         audio                  = false;
    std::string  hotkey                 = "ctrl+alt+a";
    bool         start_on_launch        = true;

    bool         schedule_start_enabled = false;
    int          schedule_start_h       = 18;
    int          schedule_start_m       = 0;
    bool         schedule_stop_enabled  = false;
    int          schedule_stop_h        = 2;
    int          schedule_stop_m        = 0;

    void         Clamp();
};

const char*  AlgoName(Algo algo);
Algo         AlgoFromName(const std::string& name);

class SettingsStore
{
public:
    AmbilightSettings   Get() const;
    uint64_t            Version() const { return version.load(); }
    void                Set(const AmbilightSettings& settings);
    void                Update(const std::function<void(AmbilightSettings&)>& change);

private:
    mutable std::mutex      mutex;
    AmbilightSettings       settings;
    std::atomic<uint64_t>   version{ 1 };
};

}
