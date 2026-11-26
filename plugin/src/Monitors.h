/*---------------------------------------------------------*\
| Monitors.h                                                |
|                                                           |
|   The monitors Windows knows about, in EnumDisplayMonitors |
|   order -- the order mss numbered them in, so a monitor   |
|   index from the old Ambilight config.json still means    |
|   the same screen.                                        |
\*---------------------------------------------------------*/

#pragma once

#include <string>
#include <vector>

namespace ambi
{

struct MonitorInfo
{
    int          index   = 0;
    std::wstring device;            /* e.g. \\.\DISPLAY1, as DXGI names its outputs */
    int          left    = 0;
    int          top     = 0;
    int          width   = 0;
    int          height  = 0;
    bool         primary = false;
};

std::vector<MonitorInfo> EnumerateMonitors();

/*---------------------------------------------------------*\
| The monitor named `device`, else the one at `index`, else |
| the largest (Ambilight.pyw's find_monitor fallback).      |
| Returns false only when there is no monitor at all.       |
\*---------------------------------------------------------*/
bool ResolveMonitor(const std::wstring& device, int index, MonitorInfo& out);

std::string  ToUtf8(const std::wstring& text);
std::wstring FromUtf8(const std::string& text);

}
