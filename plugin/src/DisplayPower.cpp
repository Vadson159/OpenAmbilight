/*---------------------------------------------------------*\
| DisplayPower.cpp                                          |
|                                                           |
|   PowerSettingRegisterNotification with a callback: no    |
|   window and no message loop needed. Windows calls back   |
|   once with the current state right after registering.    |
\*---------------------------------------------------------*/

#include "DisplayPower.h"

#include <windows.h>
#include <atomic>
#include <cstring>

#include "Log.h"

namespace ambi
{

/*---------------------------------------------------------*\
| GUID_CONSOLE_DISPLAY_STATE                                |
\*---------------------------------------------------------*/
static const GUID CONSOLE_DISPLAY_STATE = { 0x6FE69556, 0x704A, 0x47A0, { 0x8F, 0x24, 0xC2, 0x8D, 0x93, 0x6F, 0xDA, 0x47 } };

typedef ULONG (CALLBACK* NotifyCallback)(PVOID context, ULONG type, PVOID setting);

struct NotifySubscribeParameters
{
    NotifyCallback  callback;
    PVOID           context;
};

typedef DWORD (WINAPI* RegisterFn)(LPCGUID setting_guid, DWORD flags, HANDLE recipient, PVOID* registration);
typedef DWORD (WINAPI* UnregisterFn)(PVOID registration);

static const DWORD NOTIFY_CALLBACK          = 2;        /* DEVICE_NOTIFY_CALLBACK */
static const ULONG POWER_SETTING_CHANGE     = 0x8013;   /* PBT_POWERSETTINGCHANGE */

static HMODULE PowrProf()
{
    static HMODULE module = LoadLibraryExW(L"powrprof.dll", NULL, LOAD_LIBRARY_SEARCH_SYSTEM32);
    return module;
}

static std::atomic<int> display_state(DisplayPowerWatcher::Unknown);

DisplayPowerWatcher::~DisplayPowerWatcher()
{
    Stop();
}

int DisplayPowerWatcher::GetState() const
{
    return display_state.load();
}

unsigned long __stdcall DisplayPowerWatcher::Callback(void* /*context*/, unsigned long type, void* setting)
{
    if(type == POWER_SETTING_CHANGE && setting)
    {
        const POWERBROADCAST_SETTING* data = (const POWERBROADCAST_SETTING*)setting;

        if(memcmp(&data->PowerSetting, &CONSOLE_DISPLAY_STATE, sizeof(GUID)) == 0 && data->DataLength >= sizeof(DWORD))
        {
            DWORD value;

            memcpy(&value, data->Data, sizeof(DWORD));

            int previous = display_state.exchange((int)value);

            if(previous != (int)value)
            {
                Log("Display %s", value == 0 ? "switched off" : (value == 2 ? "dimmed" : "switched on"));
            }
        }
    }

    return 0;
}

bool DisplayPowerWatcher::Start()
{
    if(handle)
    {
        return true;
    }

    HMODULE module = PowrProf();

    if(!module)
    {
        return false;
    }

    RegisterFn reg = (RegisterFn)(void*)GetProcAddress(module, "PowerSettingRegisterNotification");

    if(!reg)
    {
        return false;
    }

    static_assert(sizeof(params) == sizeof(NotifySubscribeParameters), "subscribe parameters layout");

    NotifySubscribeParameters* subscribe = (NotifySubscribeParameters*)params;

    subscribe->callback = (NotifyCallback)Callback;
    subscribe->context  = this;

    PVOID   registration = nullptr;
    DWORD   result       = reg(&CONSOLE_DISPLAY_STATE, NOTIFY_CALLBACK, (HANDLE)subscribe, &registration);

    if(result != ERROR_SUCCESS)
    {
        Log("Display power notifications unavailable (error %lu)", (unsigned long)result);
        return false;
    }

    handle = registration;
    return true;
}

void DisplayPowerWatcher::Stop()
{
    if(!handle)
    {
        return;
    }

    UnregisterFn unreg = (UnregisterFn)(void*)GetProcAddress(PowrProf(), "PowerSettingUnregisterNotification");

    if(unreg)
    {
        unreg(handle);
    }

    handle        = nullptr;
    display_state = Unknown;
}

}
