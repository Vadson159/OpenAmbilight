/*---------------------------------------------------------*\
| AmbilightEngine.h                                         |
|                                                           |
|   The capture loop: grab a downsampled frame, find its    |
|   colour, ease towards it and hand it to DeviceSender.    |
|   A port of AmbilightGUI._run_loop in Ambilight.pyw,      |
|   with the live settings read from a SettingsStore.       |
\*---------------------------------------------------------*/

#pragma once

#include <atomic>
#include <condition_variable>
#include <mutex>
#include <string>
#include <thread>

#include "ColorMath.h"
#include "Settings.h"

namespace ambi
{

class DeviceSender;
class DisplayPowerWatcher;

class AmbilightEngine
{
public:
    enum State
    {
        Stopped,
        Starting,
        Running,
        CaptureLost,
        DisplayOff,
        Music,
    };

    struct Status
    {
        State           state       = Stopped;
        bool            has_color   = false;
        RGBi            color;
        std::string     backend;
        std::string     detail;
    };

    AmbilightEngine(SettingsStore* settings, DeviceSender* sender, DisplayPowerWatcher* power);
    ~AmbilightEngine();

    void            Start();
    void            Stop();
    bool            IsRunning() const { return thread_running.load(); }
    Status          GetStatus() const;

private:
    void            Run();
    bool            Pause(double seconds);
    void            SetState(State state, const std::string& detail = std::string());
    void            SetBackend(const std::string& backend);
    void            ShowColor(const RGBi& color);

    SettingsStore*          settings;
    DeviceSender*           sender;
    DisplayPowerWatcher*    power;

    std::thread             thread;
    std::atomic<bool>       thread_running{ false };
    std::mutex              stop_mutex;
    std::condition_variable stop_cv;
    bool                    stop_requested = false;

    mutable std::mutex      status_mutex;
    Status                  status;
};

}
