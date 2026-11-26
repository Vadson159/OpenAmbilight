/*---------------------------------------------------------*\
| test_scheduler.cpp                                        |
|                                                           |
|   tests/test_color_sender.py, ported to SchedulerCore.    |
|   Deterministic: no OpenRGB, no LEDs, no clock.           |
|                                                           |
|   The Python tests for the raw SDK socket and the direct  |
|   HID keyboard have no counterpart: inside OpenRGB there  |
|   is no socket, and OpenRGB drives the keyboard itself.   |
\*---------------------------------------------------------*/

#include <cmath>
#include <cstdio>
#include <functional>
#include <set>
#include <string>
#include <tuple>
#include <vector>

#include "DeviceScheduler.h"

using namespace ambi;

static int failures = 0;
static int checks   = 0;

#define CHECK(cond) do { checks++; if(!(cond)) { failures++; printf("  FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); } } while(0)

static bool AlmostEqual(double a, double b)
{
    /* unittest.assertAlmostEqual: round(a - b, 7) == 0 */
    return std::fabs(a - b) < 0.5e-7;
}

static RGBi C(int r, int g, int b)
{
    RGBi c;
    c.r = r;
    c.g = g;
    c.b = b;
    return c;
}

struct Sent
{
    double t;
    int    dev;
    RGBi   rgb;
};

struct Harness
{
    SchedulerCore     core;
    std::vector<Sent> sent;
    double            now          = 10.0;
    bool              drop_writes  = false;

    explicit Harness(bool sync = true)
    {
        std::vector<SchedEntry> plan;

        plan.push_back(SchedulerCore::MakeEntry(nullptr, 0, "slow",   200, 200));
        plan.push_back(SchedulerCore::MakeEntry(nullptr, 1, "medium",  80,  80));
        plan.push_back(SchedulerCore::MakeEntry(nullptr, 2, "fast",    10,  10));

        core.SetSync(sync);
        core.SetPlan(plan);
        core.SetBrightness(1.0);
        core.SetDispatch([this](const std::vector<SchedWrite>& writes, std::vector<SchedEntry*>& out)
        {
            if(drop_writes)
            {
                return;
            }

            for(const SchedWrite& write : writes)
            {
                sent.push_back({ now, write.first->id, write.second });
                out.push_back(write.first);
            }
        });
    }

    double Tick(double at)
    {
        now = at;
        return core.Tick(at);
    }

    double Tick(double at, const RGBi& rgb)
    {
        now = at;
        core.SetLatest(at, rgb);
        return core.Tick(at);
    }
};

static void test_first_frame_and_inflight_frame_use_one_color()
{
    Harness h;

    h.Tick(10, C(10, 20, 30));
    h.Tick(10.12, C(200, 150, 100));
    h.Tick(10.19);

    CHECK(h.sent.size() == 3);
    for(size_t i = 0; i < h.sent.size(); i++)
    {
        CHECK(h.sent[i].dev == (int)i);
        CHECK(h.sent[i].rgb == C(10, 20, 30));
        CHECK(AlmostEqual(h.sent[i].t + h.core.plan[h.sent[i].dev].lat, 10.2));
    }
}

static void test_stream_has_identical_sequences_and_bounded_cadence()
{
    Harness h;

    for(int i = 0; i < 2001; i++)
    {
        h.Tick(10 + i / 1000.0, C(i / 17, 50, 70));
    }

    /*-----------------------------------------------------*\
    | Drain the frozen cohort without starting another one  |
    \*-----------------------------------------------------*/
    h.core.SetSync(false);
    while(h.core.frame_active)
    {
        h.Tick(h.now + .001);
    }

    std::vector<std::vector<Sent>> per_device(3);
    for(const Sent& s : h.sent)
    {
        per_device[s.dev].push_back(s);
    }

    CHECK(per_device[0].size() > 5);
    CHECK(per_device[0].size() == per_device[1].size());
    CHECK(per_device[0].size() == per_device[2].size());

    for(size_t i = 0; i < per_device[0].size() && i < per_device[1].size() && i < per_device[2].size(); i++)
    {
        CHECK(per_device[0][i].rgb == per_device[1][i].rgb);
        CHECK(per_device[0][i].rgb == per_device[2][i].rgb);
    }

    for(const std::vector<Sent>& records : per_device)
    {
        for(size_t i = 1; i < records.size(); i++)
        {
            CHECK(records[i].t - records[i - 1].t >= .299);
        }
    }
}

static void test_skipped_first_write_is_retried_with_offsets_preserved()
{
    Harness h;

    h.drop_writes = true;
    h.Tick(10, C(20, 40, 60));
    CHECK(!h.core.plan[0].has_last);

    h.drop_writes = false;
    h.Tick(10.04, C(90, 90, 90));
    h.Tick(10.161);
    h.Tick(10.231);

    CHECK(h.sent.size() == 3);
    if(h.sent.size() == 3)
    {
        CHECK(h.sent[0].dev == 0 && h.sent[1].dev == 1 && h.sent[2].dev == 2);
        for(const Sent& s : h.sent)
        {
            CHECK(s.rgb == C(20, 40, 60));
        }
        CHECK(AlmostEqual(h.sent[2].t - h.sent[0].t, .191));
    }
}

static void test_idle_waits_for_keepalive_and_wakes_for_final_color()
{
    Harness h;

    h.Tick(10, C(1, 2, 3));
    h.Tick(10.12);
    h.Tick(10.19);

    CHECK(h.Tick(10.3) == 11);
    CHECK(h.sent.size() == 3);

    h.Tick(10.4, C(4, 5, 6));
    h.Tick(10.521);
    h.Tick(10.591);

    CHECK(h.sent.size() == 6);
    for(size_t i = 3; i < h.sent.size(); i++)
    {
        CHECK(h.sent[i].rgb == C(4, 5, 6));
    }
}

static void test_enabling_sync_drains_independent_writes()
{
    Harness h(false);

    h.Tick(10, C(10, 20, 30));
    h.core.SetSync(true);

    CHECK(AlmostEqual(h.Tick(10.05, C(20, 30, 40)), 10.3));
    CHECK(h.sent.size() == 3);

    h.Tick(10.3);
    CHECK(h.sent.back().dev == 0);
    CHECK(h.sent.back().rgb == C(20, 30, 40));
}

static void test_disabling_sync_finishes_current_cohort()
{
    Harness h;

    h.Tick(10, C(10, 20, 30));
    h.core.SetSync(false);
    h.Tick(10.12, C(200, 150, 100));
    h.Tick(10.19);

    CHECK(h.sent.size() == 3);
    for(const Sent& s : h.sent)
    {
        CHECK(s.rgb == C(10, 20, 30));
    }
}

static void test_failed_independent_write_does_not_suppress_retry()
{
    Harness h(false);

    h.drop_writes = true;
    h.Tick(10, C(10, 20, 30));
    for(const SchedEntry& e : h.core.plan)
    {
        CHECK(!e.has_last);
    }

    h.drop_writes = false;
    h.Tick(10.005);
    CHECK(h.sent.size() == 3);
}

static void test_sequential_msi_zones_align_midpoint_but_keep_full_busy_time()
{
    double latency, busy;

    DeviceTimingMs("MSI MYSTIC LIGHT", "MSI Mystic Light Device (761-byte)", { 0, 0, 60, 60 }, latency, busy);
    CHECK(latency == 138.75);
    CHECK(busy == 185.0);

    SchedEntry entry = SchedulerCore::MakeEntry(nullptr, 2, "MSI MYSTIC LIGHT", latency, busy);
    CHECK(AlmostEqual(entry.period, .2775));
}

static void test_latency_table_matches_device_names()
{
    CHECK(DeviceLatencyMs("Kingston Fury DDR5 DRAM") == 140.0);
    CHECK(DeviceLatencyMs("XPG Summoner Gaming Keyboard") == 31.0);
    CHECK(DeviceLatencyMs("Roccat Burst Pro") == 2.0);
    CHECK(DeviceLatencyMs("MSI MYSTIC LIGHT") == 185.0);
    CHECK(DeviceLatencyMs("Something Else") == DEFAULT_LATENCY_MS);

    double latency, busy;

    /* a non-761 MSI board keeps the table value for both */
    DeviceTimingMs("MSI MYSTIC LIGHT", "MSI Mystic Light Device (185-byte)", { 60, 60 }, latency, busy);
    CHECK(latency == 185.0 && busy == 185.0);

    /* a 761 board with every header at 0 LEDs as well */
    DeviceTimingMs("MSI MYSTIC LIGHT", "MSI Mystic Light Device (761-byte)", { 0, 0, 0, 0 }, latency, busy);
    CHECK(latency == 185.0 && busy == 185.0);
}

static void test_brightness_scales_like_python()
{
    Harness h(false);

    h.core.SetBrightness(0.55);
    h.Tick(10, C(255, 100, 3));
    CHECK(h.sent.size() == 3);
    /* int(255 * .55) = 140, int(100 * .55) = 55, int(3 * .55) = 1 */
    CHECK(h.sent[0].rgb == C(140, 55, 1));
}

static void test_new_plan_keeps_color_and_resends()
{
    Harness h(false);

    h.Tick(10, C(9, 9, 9));
    CHECK(h.sent.size() == 3);

    std::vector<SchedEntry> plan;
    plan.push_back(SchedulerCore::MakeEntry(nullptr, 0, "fast", 10, 10));
    h.core.SetPlan(plan);

    h.Tick(10.001);
    CHECK(h.sent.size() == 4);
    CHECK(h.sent.back().rgb == C(9, 9, 9));
}

int main()
{
    struct { const char* name; std::function<void()> fn; } tests[] =
    {
        { "first_frame_and_inflight_frame_use_one_color",               test_first_frame_and_inflight_frame_use_one_color },
        { "stream_has_identical_sequences_and_bounded_cadence",         test_stream_has_identical_sequences_and_bounded_cadence },
        { "skipped_first_write_is_retried_with_offsets_preserved",      test_skipped_first_write_is_retried_with_offsets_preserved },
        { "idle_waits_for_keepalive_and_wakes_for_final_color",         test_idle_waits_for_keepalive_and_wakes_for_final_color },
        { "enabling_sync_drains_independent_writes",                    test_enabling_sync_drains_independent_writes },
        { "disabling_sync_finishes_current_cohort",                     test_disabling_sync_finishes_current_cohort },
        { "failed_independent_write_does_not_suppress_retry",           test_failed_independent_write_does_not_suppress_retry },
        { "sequential_msi_zones_align_midpoint_but_keep_full_busy_time", test_sequential_msi_zones_align_midpoint_but_keep_full_busy_time },
        { "latency_table_matches_device_names",                         test_latency_table_matches_device_names },
        { "brightness_scales_like_python",                              test_brightness_scales_like_python },
        { "new_plan_keeps_color_and_resends",                           test_new_plan_keeps_color_and_resends },
    };

    for(auto& test : tests)
    {
        int before = failures;
        test.fn();
        printf("%s %s\n", failures == before ? "ok  " : "FAIL", test.name);
    }

    printf("%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
