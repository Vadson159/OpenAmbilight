/*---------------------------------------------------------*\
| DeviceSender.cpp                                          |
\*---------------------------------------------------------*/

#include "DeviceSender.h"

#include <algorithm>
#include <cctype>
#include <chrono>

#include "Log.h"
#include "RGBController.h"
#include "ScreenGrabber.h"

namespace ambi
{

static bool ContainsDirect(const std::string& name)
{
    std::string lowered = name;

    for(char& c : lowered)
    {
        c = (char)std::tolower((unsigned char)c);
    }

    return lowered.find("direct") != std::string::npos;
}

DeviceSender::DeviceSender()
{
    core.SetDispatch([this](const std::vector<SchedWrite>& writes, std::vector<SchedEntry*>& sent)
    {
        Dispatch(writes, sent);
    });
}

DeviceSender::~DeviceSender()
{
    Stop();
}

void DeviceSender::SetControllers(const std::vector<RGBController*>& controllers)
{
    std::vector<SchedEntry> plan;

    for(RGBController* controller : controllers)
    {
        std::vector<unsigned int> zone_leds;

        for(const zone& z : controller->zones)
        {
            zone_leds.push_back(z.leds_count);
        }

        double latency_ms, busy_ms;

        DeviceTimingMs(controller->name, controller->description, zone_leds, latency_ms, busy_ms);
        plan.push_back(SchedulerCore::MakeEntry(controller, (int)plan.size(), controller->name, latency_ms, busy_ms));
    }

    std::lock_guard<std::mutex> lock(mutex);

    core.SetPlan(plan);

    if(running)
    {
        int switched = EnsureDirectModeLocked();

        if(switched > 0)
        {
            Log("Switched %d device(s) to Direct mode", switched);
        }

        wake = true;
        cv.notify_one();
    }
}

int DeviceSender::EnsureDirectModeLocked()
{
    int switched = 0;

    for(SchedEntry& entry : core.plan)
    {
        RGBController* controller = (RGBController*)entry.target;
        int            active     = controller->active_mode;

        /*-------------------------------------------------*\
        | Direct mode is what makes per-frame colour        |
        | possible. Changing mode is a controller-side      |
        | switch that can swallow colours for up to a       |
        | second, so only touch a device not there yet.     |
        \*-------------------------------------------------*/
        if(active >= 0 && active < (int)controller->modes.size() && ContainsDirect(controller->modes[active].name))
        {
            continue;
        }

        for(size_t mode_idx = 0; mode_idx < controller->modes.size(); mode_idx++)
        {
            if(ContainsDirect(controller->modes[mode_idx].name))
            {
                controller->SetMode((int)mode_idx);
                switched++;
                break;
            }
        }
    }

    return switched;
}

int DeviceSender::EnsureDirectMode()
{
    std::lock_guard<std::mutex> lock(mutex);
    return EnsureDirectModeLocked();
}

void DeviceSender::Start(bool sync, double brightness)
{
    Stop();

    std::lock_guard<std::mutex> lock(mutex);

    core.SetSync(sync);
    core.SetBrightness(brightness);
    core.Reset();

    stopping = false;
    wake     = false;
    running  = true;
    worker   = std::thread(&DeviceSender::Run, this);
}

void DeviceSender::Stop()
{
    {
        std::lock_guard<std::mutex> lock(mutex);

        if(!worker.joinable())
        {
            return;
        }

        stopping = true;
        cv.notify_one();
    }

    worker.join();

    std::lock_guard<std::mutex> lock(mutex);
    running = false;
}

bool DeviceSender::Running() const
{
    std::lock_guard<std::mutex> lock(mutex);
    return running;
}

void DeviceSender::Send(const RGBi& rgb)
{
    std::lock_guard<std::mutex> lock(mutex);

    core.SetLatest(NowSeconds(), rgb);
    wake = true;
    cv.notify_one();
}

void DeviceSender::SetSync(bool sync)
{
    std::lock_guard<std::mutex> lock(mutex);

    if(core.GetSync() != sync)
    {
        core.SetSync(sync);
        wake = true;
        cv.notify_one();
    }
}

void DeviceSender::SetBrightness(double brightness)
{
    std::lock_guard<std::mutex> lock(mutex);
    core.SetBrightness(brightness);
}

std::vector<DeviceInfo> DeviceSender::Devices() const
{
    std::lock_guard<std::mutex> lock(mutex);
    std::vector<DeviceInfo>     list;

    for(const SchedEntry& entry : core.plan)
    {
        list.push_back({ entry.name, entry.latency_ms, entry.busy_ms });
    }

    return list;
}

size_t DeviceSender::DeviceCount() const
{
    std::lock_guard<std::mutex> lock(mutex);
    return core.plan.size();
}

/*---------------------------------------------------------*\
| Called by SchedulerCore::Tick(), so with the mutex held   |
\*---------------------------------------------------------*/
void DeviceSender::Dispatch(const std::vector<SchedWrite>& writes, std::vector<SchedEntry*>& sent)
{
    for(const SchedWrite& write : writes)
    {
        RGBController* controller = (RGBController*)write.first->target;

        controller->SetAllLEDs(ToRGBColor(write.second.r, write.second.g, write.second.b));
        controller->UpdateLEDs();

        sent.push_back(write.first);
    }
}

void DeviceSender::Run()
{
    double                       poll = DEVICE_KEEPALIVE;
    std::unique_lock<std::mutex> lock(mutex);

    while(!stopping)
    {
        cv.wait_for(lock, std::chrono::duration<double>(poll), [this] { return wake || stopping; });

        if(stopping)
        {
            break;
        }

        wake = false;

        double wake_at = core.Tick(NowSeconds());

        poll = std::max(0.001, wake_at - NowSeconds());
    }
}

}
