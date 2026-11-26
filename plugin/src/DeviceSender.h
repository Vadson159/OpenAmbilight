/*---------------------------------------------------------*\
| DeviceSender.h                                            |
|                                                           |
|   Puts colours on OpenRGB's devices, in-process: each due |
|   device gets SetAllLEDs() + UpdateLEDs(), which in this  |
|   build only raises the device thread's flag (no SDK      |
|   socket, no packets). SchedulerCore decides who is due;  |
|   this runs it on a thread of its own.                    |
|                                                           |
|   Controllers belong to OpenRGB and are deleted on every  |
|   rescan. Every touch of a controller happens under one   |
|   mutex, and SetControllers() takes that mutex: once it   |
|   returns, no write is in flight and the old pointers are |
|   forgotten, so OpenRGB may free them.                    |
\*---------------------------------------------------------*/

#pragma once

#include <condition_variable>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "DeviceScheduler.h"

class RGBController;

namespace ambi
{

struct DeviceInfo
{
    std::string name;
    double      latency_ms;
    double      busy_ms;
};

class DeviceSender
{
public:
    DeviceSender();
    ~DeviceSender();

    /*-----------------------------------------------------*\
    | Replace the device list. Blocks until no write is in  |
    | flight. If the sender is running and `direct` is set, |
    | new devices are switched to their Direct mode.        |
    \*-----------------------------------------------------*/
    void                    SetControllers(const std::vector<RGBController*>& controllers);

    /*-----------------------------------------------------*\
    | Switch every device that has a Direct mode into it.   |
    | Returns how many had to change.                       |
    \*-----------------------------------------------------*/
    int                     EnsureDirectMode();

    void                    Start(bool sync, double brightness);
    void                    Stop();
    bool                    Running() const;

    void                    Send(const RGBi& rgb);
    void                    SetSync(bool sync);
    void                    SetBrightness(double brightness);

    std::vector<DeviceInfo> Devices() const;
    size_t                  DeviceCount() const;

private:
    void                    Run();
    void                    Dispatch(const std::vector<SchedWrite>& writes, std::vector<SchedEntry*>& sent);
    int                     EnsureDirectModeLocked();

    mutable std::mutex      mutex;
    std::condition_variable cv;
    bool                    wake        = false;
    bool                    stopping    = false;
    bool                    running     = false;

    SchedulerCore           core;
    std::thread             worker;
};

}
