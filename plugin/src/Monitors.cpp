/*---------------------------------------------------------*\
| Monitors.cpp                                              |
\*---------------------------------------------------------*/

#include "Monitors.h"

#include <windows.h>

namespace ambi
{

static BOOL CALLBACK MonitorEnumProc(HMONITOR monitor, HDC, LPRECT, LPARAM param)
{
    std::vector<MonitorInfo>* list = (std::vector<MonitorInfo>*)param;
    MONITORINFOEXW            info;

    ZeroMemory(&info, sizeof(info));
    info.cbSize = sizeof(info);

    if(GetMonitorInfoW(monitor, (MONITORINFO*)&info))
    {
        MonitorInfo entry;

        entry.index   = (int)list->size();
        entry.device  = info.szDevice;
        entry.left    = info.rcMonitor.left;
        entry.top     = info.rcMonitor.top;
        entry.width   = info.rcMonitor.right - info.rcMonitor.left;
        entry.height  = info.rcMonitor.bottom - info.rcMonitor.top;
        entry.primary = (info.dwFlags & MONITORINFOF_PRIMARY) != 0;

        list->push_back(entry);
    }

    return TRUE;
}

std::vector<MonitorInfo> EnumerateMonitors()
{
    std::vector<MonitorInfo> list;

    EnumDisplayMonitors(NULL, NULL, MonitorEnumProc, (LPARAM)&list);

    return list;
}

bool ResolveMonitor(const std::wstring& device, int index, MonitorInfo& out)
{
    std::vector<MonitorInfo> monitors = EnumerateMonitors();

    if(monitors.empty())
    {
        return false;
    }

    if(!device.empty())
    {
        for(const MonitorInfo& monitor : monitors)
        {
            if(_wcsicmp(monitor.device.c_str(), device.c_str()) == 0)
            {
                out = monitor;
                return true;
            }
        }
    }

    if(index >= 0 && index < (int)monitors.size())
    {
        out = monitors[index];
        return true;
    }

    const MonitorInfo* best = &monitors[0];

    for(const MonitorInfo& monitor : monitors)
    {
        if((long long)monitor.width * monitor.height > (long long)best->width * best->height)
        {
            best = &monitor;
        }
    }

    out = *best;
    return true;
}

std::string ToUtf8(const std::wstring& text)
{
    if(text.empty())
    {
        return std::string();
    }

    int         size = WideCharToMultiByte(CP_UTF8, 0, text.c_str(), (int)text.size(), NULL, 0, NULL, NULL);
    std::string out(size, '\0');

    WideCharToMultiByte(CP_UTF8, 0, text.c_str(), (int)text.size(), &out[0], size, NULL, NULL);

    return out;
}

std::wstring FromUtf8(const std::string& text)
{
    if(text.empty())
    {
        return std::wstring();
    }

    int          size = MultiByteToWideChar(CP_UTF8, 0, text.c_str(), (int)text.size(), NULL, 0);
    std::wstring out(size, L'\0');

    MultiByteToWideChar(CP_UTF8, 0, text.c_str(), (int)text.size(), &out[0], size);

    return out;
}

}
