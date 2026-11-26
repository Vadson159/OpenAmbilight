/*---------------------------------------------------------*\
| Settings.cpp                                              |
\*---------------------------------------------------------*/

#include "Settings.h"

#include <algorithm>

namespace ambi
{

void AmbilightSettings::Clamp()
{
    monitor_index    = std::max(0, monitor_index);
    fps              = std::max(1, std::min(240, fps));
    black_threshold  = std::max(1, std::min(100, black_threshold));
    brightness       = std::max(1, std::min(100, brightness));
    min_brightness   = std::max(0, std::min(100, min_brightness));
    smoothness       = std::max(1, std::min(100, smoothness));
    schedule_start_h = std::max(0, std::min(23, schedule_start_h));
    schedule_start_m = std::max(0, std::min(59, schedule_start_m));
    schedule_stop_h  = std::max(0, std::min(23, schedule_stop_h));
    schedule_stop_m  = std::max(0, std::min(59, schedule_stop_m));
}

const char* AlgoName(Algo algo)
{
    switch(algo)
    {
        case Algo::Average:     return "Average";
        case Algo::CenterFocus: return "Center Focus";
        default:                return "Dominant";
    }
}

Algo AlgoFromName(const std::string& name)
{
    if(name == "Average")
    {
        return Algo::Average;
    }

    if(name == "Center Focus")
    {
        return Algo::CenterFocus;
    }

    return Algo::Dominant;
}

AmbilightSettings SettingsStore::Get() const
{
    std::lock_guard<std::mutex> lock(mutex);
    return settings;
}

void SettingsStore::Set(const AmbilightSettings& new_settings)
{
    {
        std::lock_guard<std::mutex> lock(mutex);
        settings = new_settings;
        settings.Clamp();
    }
    version++;
}

void SettingsStore::Update(const std::function<void(AmbilightSettings&)>& change)
{
    {
        std::lock_guard<std::mutex> lock(mutex);
        change(settings);
        settings.Clamp();
    }
    version++;
}

}
