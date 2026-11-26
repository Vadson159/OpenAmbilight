/*---------------------------------------------------------*\
| Settings.cpp                                              |
\*---------------------------------------------------------*/

#include "Settings.h"

#include <algorithm>
#include <cctype>

namespace ambi
{

static std::atomic<int> ui_language_state{ (int)UiLanguage::Auto };

void AmbilightSettings::Clamp()
{
    if(language != UiLanguage::Auto && language != UiLanguage::English && language != UiLanguage::Russian)
    {
        language = UiLanguage::Auto;
    }

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

const char* LanguageCode(UiLanguage language)
{
    switch(language)
    {
        case UiLanguage::English: return "en";
        case UiLanguage::Russian: return "ru";
        default:                  return "auto";
    }
}

UiLanguage LanguageFromCode(const std::string& code)
{
    std::string lower;
    lower.reserve(code.size());

    for(unsigned char c : code)
    {
        lower.push_back((char)std::tolower(c));
    }

    if(lower == "en" || lower == "en_us" || lower == "en-us" || lower == "english")
    {
        return UiLanguage::English;
    }

    if(lower == "ru" || lower == "ru_ru" || lower == "ru-ru" || lower == "russian" || code == "\xD0\xA0\xD1\x83\xD1\x81\xD1\x81\xD0\xBA\xD0\xB8\xD0\xB9")
    {
        return UiLanguage::Russian;
    }

    return UiLanguage::Auto;
}

void SetUiLanguage(UiLanguage language)
{
    ui_language_state.store((int)language);
}

UiLanguage GetUiLanguage()
{
    int value = ui_language_state.load();

    if(value == (int)UiLanguage::English)
    {
        return UiLanguage::English;
    }

    if(value == (int)UiLanguage::Russian)
    {
        return UiLanguage::Russian;
    }

    return UiLanguage::Auto;
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
