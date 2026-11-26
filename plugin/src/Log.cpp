/*---------------------------------------------------------*\
| Log.cpp                                                   |
\*---------------------------------------------------------*/

#include "Log.h"

#include <windows.h>

#include <cstdarg>
#include <cstdio>
#include <mutex>

namespace ambi
{

/*---------------------------------------------------------*\
| Deliberately leaked: the log may be written from threads  |
| that are being torn down while the DLL detaches           |
\*---------------------------------------------------------*/
static std::mutex&   LogMutex() { static std::mutex* m = new std::mutex(); return *m; }
static FILE*         log_file = nullptr;
static std::wstring* log_path = nullptr;

static const long LOG_LIMIT = 1024 * 1024;

void LogOpen(const std::wstring& path)
{
    std::lock_guard<std::mutex> lock(LogMutex());

    if(log_file)
    {
        fclose(log_file);
        log_file = nullptr;
    }

    if(!log_path)
    {
        log_path = new std::wstring();
    }

    *log_path = path;
    log_file  = _wfopen(path.c_str(), L"ab");
}

void LogClose()
{
    std::lock_guard<std::mutex> lock(LogMutex());

    if(log_file)
    {
        fclose(log_file);
        log_file = nullptr;
    }
}

void Log(const char* format, ...)
{
    char    message[1024];
    va_list args;

    va_start(args, format);
    vsnprintf(message, sizeof(message), format, args);
    va_end(args);

    SYSTEMTIME now;

    GetLocalTime(&now);

    std::lock_guard<std::mutex> lock(LogMutex());

    if(!log_file)
    {
        return;
    }

    if(ftell(log_file) > LOG_LIMIT && log_path)
    {
        fclose(log_file);

        std::wstring previous = *log_path + L".1";

        DeleteFileW(previous.c_str());
        MoveFileW(log_path->c_str(), previous.c_str());

        log_file = _wfopen(log_path->c_str(), L"ab");

        if(!log_file)
        {
            return;
        }
    }

    fprintf(log_file, "%04u-%02u-%02u %02u:%02u:%02u.%03u  %s\r\n",
            now.wYear, now.wMonth, now.wDay, now.wHour, now.wMinute, now.wSecond, now.wMilliseconds, message);
    fflush(log_file);
}

}
