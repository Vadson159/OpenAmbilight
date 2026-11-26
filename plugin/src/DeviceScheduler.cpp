/*---------------------------------------------------------*\
| DeviceScheduler.cpp                                       |
|                                                           |
|   See DeviceScheduler.h. SchedulerCore::Tick() is         |
|   AsyncColorSender._tick() statement for statement.       |
\*---------------------------------------------------------*/

#include "DeviceScheduler.h"

#include <algorithm>
#include <cctype>
#include <limits>

namespace ambi
{

const LatencyRule DEVICE_LATENCY_MS[4] =
{
    { "mystic light",   185.0 },
    { "fury",           140.0 },
    { "summoner",        31.0 },
    { "burst pro",        2.0 },
};

static std::string Lower(const std::string& text)
{
    std::string out = text;

    for(char& c : out)
    {
        c = (char)std::tolower((unsigned char)c);
    }

    return out;
}

double DeviceLatencyMs(const std::string& name)
{
    std::string lowered = Lower(name);

    for(const LatencyRule& rule : DEVICE_LATENCY_MS)
    {
        if(lowered.find(rule.key) != std::string::npos)
        {
            return rule.ms;
        }
    }

    return DEFAULT_LATENCY_MS;
}

void DeviceTimingMs(const std::string& name, const std::string& description,
                    const std::vector<unsigned int>& zone_led_counts,
                    double& latency_ms, double& busy_ms)
{
    double latency = DeviceLatencyMs(name);

    if(Lower(name).find("mystic light") != std::string::npos
    && Lower(description).find("761-byte") != std::string::npos)
    {
        unsigned int active = 0;

        for(unsigned int leds : zone_led_counts)
        {
            if(leds > 0)
            {
                active++;
            }
        }

        if(active > 0)
        {
            latency_ms = MSI_761_ZONE_MS * (active + 1) / 2;
            busy_ms    = MSI_761_ZONE_MS * active;
            return;
        }
    }

    latency_ms = latency;
    busy_ms    = latency;
}

SchedEntry SchedulerCore::MakeEntry(void* target, int id, const std::string& name, double latency_ms, double busy_ms)
{
    SchedEntry entry;

    entry.target     = target;
    entry.id         = id;
    entry.name       = name;
    entry.latency_ms = latency_ms;
    entry.busy_ms    = busy_ms;
    entry.lat        = latency_ms / 1000.0;
    entry.period     = (busy_ms / 1000.0) * DEVICE_PACE;

    return entry;
}

void SchedulerCore::SetPlan(const std::vector<SchedEntry>& entries)
{
    plan = entries;

    slowest      = 0.0;
    frame_period = 0.0;

    for(const SchedEntry& entry : plan)
    {
        slowest      = std::max(slowest, entry.lat);
        frame_period = std::max(frame_period, entry.period);
    }

    /*-----------------------------------------------------*\
    | A new device list starts a fresh schedule, but keeps  |
    | the colour: the next tick sends it to the new set.    |
    \*-----------------------------------------------------*/
    ResetSchedule();
}

void SchedulerCore::ResetSchedule()
{
    for(SchedEntry& entry : plan)
    {
        entry.next     = 0.0;
        entry.has_last = false;
        entry.last_t   = 0.0;
    }

    frame_active  = false;
    frame_started = false;
    pending.clear();
    next_frame_at = 0.0;
    was_sync      = sync;
}

void SchedulerCore::Reset()
{
    ResetSchedule();
    has_latest = false;
}

RGBi SchedulerCore::Scale(const RGBi& rgb) const
{
    RGBi out;

    out.r = std::max(0, std::min(255, PyInt(rgb.r * brightness)));
    out.g = std::max(0, std::min(255, PyInt(rgb.g * brightness)));
    out.b = std::max(0, std::min(255, PyInt(rgb.b * brightness)));

    return out;
}

double SchedulerCore::Tick(double now)
{
    const double inf = std::numeric_limits<double>::infinity();

    if(!has_latest || plan.empty())
    {
        return now + DEVICE_KEEPALIVE;
    }

    /*-----------------------------------------------------*\
    | Finish a cohort even if sync is switched off midway   |
    | through it. On entry to sync, first drain writes from |
    | the independent schedule.                             |
    \*-----------------------------------------------------*/
    if(!frame_active && sync != was_sync)
    {
        was_sync = sync;

        double latest_next = now;

        for(const SchedEntry& entry : plan)
        {
            latest_next = std::max(latest_next, entry.next);
        }

        next_frame_at = latest_next;
    }

    if(sync || frame_active)
    {
        if(!frame_active)
        {
            if(now < next_frame_at)
            {
                return next_frame_at;
            }

            RGBi   rgb        = Scale(latest);
            double refresh_at = inf;
            bool   all_have   = true;

            for(const SchedEntry& entry : plan)
            {
                refresh_at = std::min(refresh_at, entry.last_t + DEVICE_KEEPALIVE);

                if(!entry.has_last || entry.last_rgb != rgb)
                {
                    all_have = false;
                }
            }

            if(all_have && now < refresh_at)
            {
                return refresh_at;
            }

            frame_active  = true;
            frame_started = false;
            frame_rgb     = rgb;
            pending.clear();

            for(SchedEntry& entry : plan)
            {
                pending.push_back({ &entry, now + slowest - entry.lat });
            }
        }

        if(!frame_started)
        {
            /*---------------------------------------------*\
            | If nothing could be written yet, move the     |
            | whole unsent cohort forward together, keeping |
            | the compensation even on the first frame.     |
            \*---------------------------------------------*/
            double earliest = inf;

            for(const Pending& item : pending)
            {
                earliest = std::min(earliest, item.at);
            }

            double shift = std::max(0.0, now - earliest);

            for(Pending& item : pending)
            {
                item.at += shift;
            }
        }

        writes.clear();
        sent.clear();

        for(const Pending& item : pending)
        {
            if(item.at <= now)
            {
                writes.push_back({ item.entry, frame_rgb });
            }
        }

        if(!writes.empty() && dispatch)
        {
            dispatch(writes, sent);
        }

        if(!sent.empty() && !frame_started)
        {
            frame_started = true;
            next_frame_at = now + frame_period;
        }

        for(SchedEntry* entry : sent)
        {
            entry->has_last = true;
            entry->last_rgb = frame_rgb;
            entry->last_t   = now;
            entry->next     = now + entry->period;
        }

        if(!sent.empty())
        {
            pending.erase(std::remove_if(pending.begin(), pending.end(), [this](const Pending& item)
            {
                return std::find(sent.begin(), sent.end(), item.entry) != sent.end();
            }), pending.end());
        }

        if(pending.empty())
        {
            frame_active  = false;
            frame_started = false;
            return sync ? next_frame_at : now;
        }

        double wake = inf;

        for(const Pending& item : pending)
        {
            wake = std::min(wake, item.at > now ? item.at : now + 0.005);
        }

        return wake;
    }

    /*-----------------------------------------------------*\
    | Independent updates: every device on its own pace     |
    \*-----------------------------------------------------*/
    RGBi rgb = Scale(latest);

    writes.clear();
    sent.clear();

    for(SchedEntry& entry : plan)
    {
        if(now >= entry.next
        && (!entry.has_last || rgb != entry.last_rgb || now - entry.last_t >= DEVICE_KEEPALIVE))
        {
            writes.push_back({ &entry, rgb });
        }
    }

    if(!writes.empty() && dispatch)
    {
        dispatch(writes, sent);
    }

    for(SchedEntry* entry : sent)
    {
        entry->has_last = true;
        entry->last_rgb = rgb;
        entry->last_t   = now;
        entry->next     = now + entry->period;
    }

    double wake = inf;

    for(const SchedEntry& entry : plan)
    {
        double candidate = (!entry.has_last || rgb != entry.last_rgb) ? entry.next : entry.last_t + DEVICE_KEEPALIVE;

        wake = std::min(wake, std::max(now + 0.005, candidate));
    }

    return wake;
}

}
