/*---------------------------------------------------------*\
| test_capture.cpp                                          |
|                                                           |
|   ScreenGrabber against the real desktop, the C++ twin of |
|   the ScreenGrabber tests in tests/test_capture.py.       |
|                                                           |
|   To get desktop frames without disturbing anyone it      |
|   flickers a 2x2 px no-activate topmost popup in the      |
|   bottom-right corner of each monitor (no focus change,   |
|   nothing to see). Sends nothing to OpenRGB.              |
\*---------------------------------------------------------*/

#include <windows.h>

#include <atomic>
#include <cstdio>
#include <thread>

#include "ScreenGrabber.h"

using namespace ambi;

static int failures = 0;
static int checks   = 0;

#define CHECK(cond) do { checks++; if(!(cond)) { failures++; printf("  FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); } } while(0)

/*---------------------------------------------------------*\
| Corner flicker: a 2x2 popup repainted at ~120 Hz          |
\*---------------------------------------------------------*/
static std::atomic<bool> flicker_stop(false);
static std::atomic<int>  flicker_tick(0);

static LRESULT CALLBACK FlickerProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    if(msg == WM_TIMER)
    {
        flicker_tick++;
        InvalidateRect(hwnd, NULL, FALSE);
        return 0;
    }

    if(msg == WM_PAINT)
    {
        PAINTSTRUCT ps;
        HDC         dc    = BeginPaint(hwnd, &ps);
        int         t     = flicker_tick.load();
        HBRUSH      brush = CreateSolidBrush(RGB((t * 37) & 0xFF, (t * 91) & 0xFF, (t * 53) & 0xFF));

        FillRect(dc, &ps.rcPaint, brush);
        DeleteObject(brush);
        EndPaint(hwnd, &ps);
        return 0;
    }

    return DefWindowProcW(hwnd, msg, wp, lp);
}

static void FlickerThread(std::vector<MonitorInfo> monitors)
{
    WNDCLASSW wc;

    ZeroMemory(&wc, sizeof(wc));
    wc.lpfnWndProc   = FlickerProc;
    wc.hInstance     = GetModuleHandleW(NULL);
    wc.lpszClassName = L"AmbilightCaptureTestFlicker";
    RegisterClassW(&wc);

    std::vector<HWND> windows;

    for(const MonitorInfo& m : monitors)
    {
        HWND hwnd = CreateWindowExW(WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT,
                                    wc.lpszClassName, L"", WS_POPUP,
                                    m.left + m.width - 2, m.top + m.height - 2, 2, 2,
                                    NULL, NULL, wc.hInstance, NULL);

        ShowWindow(hwnd, SW_SHOWNOACTIVATE);
        SetTimer(hwnd, 1, 8, NULL);
        windows.push_back(hwnd);
    }

    MSG msg;

    while(!flicker_stop.load())
    {
        while(PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE))
        {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }

        MsgWaitForMultipleObjects(0, NULL, FALSE, 5, QS_ALLINPUT);
    }

    for(HWND hwnd : windows)
    {
        DestroyWindow(hwnd);
    }
}

static int Ceil(int value, int step)
{
    return (value + step - 1) / step;
}

static bool NextFrame(ScreenGrabber& grabber, Frame& frame)
{
    for(int attempt = 0; attempt < 40; attempt++)
    {
        if(grabber.Grab(0.1, frame) == ScreenGrabber::Result::Frame)
        {
            return true;
        }
    }

    return false;
}

int main()
{
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);

    std::vector<MonitorInfo> monitors = EnumerateMonitors();

    if(monitors.empty())
    {
        printf("no monitor to capture; skipped\n");
        return 0;
    }

    for(const MonitorInfo& m : monitors)
    {
        printf("monitor %d: %ls %dx%d at (%d,%d)%s\n", m.index, m.device.c_str(), m.width, m.height,
               m.left, m.top, m.primary ? " primary" : "");
    }

    std::thread flicker(FlickerThread, monitors);

    for(const MonitorInfo& m : monitors)
    {
        printf("-- monitor %d\n", m.index);

        /*-------------------------------------------------*\
        | The GPU thumbnail equals a full readback sampled  |
        | every 20th pixel, on one and the same frame       |
        \*-------------------------------------------------*/
        {
            ScreenGrabber grabber(m);
            Frame         shaded, sampled;
            bool          got = grabber.GrabBothForTest(3.0, shaded, sampled);

            CHECK(got);
            if(got)
            {
                CHECK(shaded.width == Ceil(m.width, DOWNSAMPLE) && shaded.height == Ceil(m.height, DOWNSAMPLE));
                CHECK(shaded.width == sampled.width && shaded.height == sampled.height);
                CHECK(shaded.rgb == sampled.rgb);

                size_t lit = 0;
                for(uint8_t v : shaded.rgb)
                {
                    lit += (v != 0);
                }
                printf("   shader vs full readback: %s (%dx%d, %zu non-zero channels)\n",
                       shaded.rgb == sampled.rgb ? "identical" : "DIFFERENT", shaded.width, shaded.height, lit);
            }
        }

        /*-------------------------------------------------*\
        | A frame has one pixel per step; backend is DXGI    |
        \*-------------------------------------------------*/
        {
            ScreenGrabber grabber(m);
            Frame         frame;

            CHECK(NextFrame(grabber, frame));
            CHECK(frame.width == Ceil(m.width, DOWNSAMPLE) && frame.height == Ceil(m.height, DOWNSAMPLE));
            CHECK(std::string(grabber.Backend()) == "DXGI");
            printf("   backend %s, display %ls\n", grabber.Backend(), grabber.Display().c_str());

            /*---------------------------------------------*\
            | Capture comes back after the desktop was lost |
            \*---------------------------------------------*/
            for(int with_device = 0; with_device < 2; with_device++)
            {
                grabber.DropForTest(with_device != 0);
                CHECK(grabber.Grab(0.0, frame) != ScreenGrabber::Result::Lost);
                CHECK(grabber.IsOpenForTest());
            }

            /*---------------------------------------------*\
            | The full-readback path alone gives the same    |
            | geometry                                       |
            \*---------------------------------------------*/
            grabber.SetShaderEnabled(false);
            CHECK(NextFrame(grabber, frame));
            CHECK(std::string(grabber.Backend()) == "DXGI (full readback)");
            CHECK(frame.width == Ceil(m.width, DOWNSAMPLE) && frame.height == Ceil(m.height, DOWNSAMPLE));
        }

        /*-------------------------------------------------*\
        | GDI fallback                                      |
        \*-------------------------------------------------*/
        {
            ScreenGrabber grabber(m);
            Frame         frame;

            grabber.ForceGdiForTest();
            CHECK(grabber.Grab(0.0, frame) == ScreenGrabber::Result::Frame);
            CHECK(std::string(grabber.Backend()) == "GDI");
            CHECK(frame.width == Ceil(m.width, DOWNSAMPLE) && frame.height == Ceil(m.height, DOWNSAMPLE));
        }
    }

    /*-----------------------------------------------------*\
    | Timing: what one grab costs on the main monitor       |
    \*-----------------------------------------------------*/
    {
        MonitorInfo m;

        if(ResolveMonitor(L"", -1, m))
        {
            ScreenGrabber grabber(m);
            Frame         frame;
            int           frames = 0;

            NextFrame(grabber, frame);

            auto cpu_ms = []()
            {
                FILETIME created, exited, kernel, user;

                GetThreadTimes(GetCurrentThread(), &created, &exited, &kernel, &user);

                ULARGE_INTEGER k, u;

                k.LowPart = kernel.dwLowDateTime; k.HighPart = kernel.dwHighDateTime;
                u.LowPart = user.dwLowDateTime;   u.HighPart = user.dwHighDateTime;

                return (double)(k.QuadPart + u.QuadPart) / 10000.0;
            };

            double cpu0  = cpu_ms();
            double start = NowSeconds();

            while(NowSeconds() - start < 2.0)
            {
                if(grabber.Grab(0.05, frame) == ScreenGrabber::Result::Frame)
                {
                    frames++;
                }
            }

            double cpu = cpu_ms() - cpu0;

            printf("-- %d frames in 2 s on %ls: %.1f ms CPU total, %.3f ms CPU per frame\n",
                   frames, m.device.c_str(), cpu, frames ? cpu / frames : 0.0);
        }
    }

    flicker_stop = true;
    flicker.join();

    printf("%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
