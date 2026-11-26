/*---------------------------------------------------------*\
| Log.h                                                     |
|                                                           |
|   A small log of the plugin's own: OpenRGB's LogManager   |
|   lives in OpenRGB.exe and is not exported to plugins.    |
|   Written to <config>\logs\Ambilight.log, which OpenRGB's |
|   log rotation leaves alone (it only matches its own      |
|   OpenRGB_<timestamp>.log names). Kept under 1 MB, with   |
|   one previous file as Ambilight.log.1.                   |
\*---------------------------------------------------------*/

#pragma once

#include <string>

namespace ambi
{

void LogOpen(const std::wstring& path);
void LogClose();
void Log(const char* format, ...)
#if defined(__GNUC__)
    __attribute__((format(printf, 1, 2)))
#endif
    ;

}
