/*---------------------------------------------------------*\
| DisplayPower.h                                            |
|                                                           |
|   Whether Windows has the display switched on. The old    |
|   "Turn off with display" option only noticed the lock    |
|   screen (capture is refused there); a monitor that went  |
|   to sleep just stopped producing frames and the LEDs     |
|   kept their last colour all night. This listens for      |
|   GUID_CONSOLE_DISPLAY_STATE, so the option can do what   |
|   its label says.                                         |
\*---------------------------------------------------------*/

#pragma once

#include <atomic>

namespace ambi
{

class DisplayPowerWatcher
{
public:
    enum State
    {
        Unknown = -1,
        Off     = 0,
        On      = 1,
        Dimmed  = 2,
    };

    ~DisplayPowerWatcher();

    bool    Start();
    void    Stop();
    int     GetState() const;

    static unsigned long __stdcall Callback(void* context, unsigned long type, void* setting);

private:
    /*-----------------------------------------------------*\
    | The state itself lives in a static, not here: Windows |
    | does not promise that unregistering waits for a       |
    | callback already running, so the callback must not    |
    | depend on this object still existing                  |
    \*-----------------------------------------------------*/
    void*               handle    = nullptr;
    void*               params[2] = { nullptr, nullptr };   /* DEVICE_NOTIFY_SUBSCRIBE_PARAMETERS */
};

}
