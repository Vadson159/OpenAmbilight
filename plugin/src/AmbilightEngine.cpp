/*---------------------------------------------------------*\
| AmbilightEngine.cpp                                       |
|                                                           |
|   AmbilightGUI._run_loop, statement for statement where   |
|   it matters (the arithmetic, the idle logic and the      |
|   send rules), plus:                                      |
|   * the monitor can change while running (the old GUI     |
|     greyed the selector out instead),                     |
|   * "Turn off with display" also covers a display that    |
|     Windows put to sleep, not only the lock screen,       |
|   * music mode retries a speaker that failed to open.     |
\*---------------------------------------------------------*/

#include "AmbilightEngine.h"

#include <windows.h>
#include <objbase.h>

#include <algorithm>
#include <cmath>
#include <memory>

#include "AudioMeter.h"
#include "DeviceSender.h"
#include "DisplayPower.h"
#include "Log.h"
#include "Monitors.h"
#include "ScreenGrabber.h"

namespace ambi
{

static const double FORCE_RESEND_INTERVAL = 1.0;
static const int    RAW_WAKE_THRESHOLD    = 15;
static const int    FORCE_CYCLES          = 15;

/*---------------------------------------------------------*\
| Below-normal priority and EcoQoS for the capture thread   |
| only. The old app set these for its whole process; here   |
| the process is OpenRGB, whose device threads must not be  |
| slowed down.                                              |
\*---------------------------------------------------------*/
static void TuneCaptureThread()
{
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL);

    HMODULE kernel = GetModuleHandleW(L"kernel32.dll");

    typedef BOOL (WINAPI* SetThreadInformationFn)(HANDLE, THREAD_INFORMATION_CLASS, LPVOID, DWORD);
    typedef HRESULT (WINAPI* SetThreadDescriptionFn)(HANDLE, PCWSTR);

    SetThreadInformationFn set_info = (SetThreadInformationFn)(void*)GetProcAddress(kernel, "SetThreadInformation");

    if(set_info)
    {
        THREAD_POWER_THROTTLING_STATE throttling;

        ZeroMemory(&throttling, sizeof(throttling));
        throttling.Version     = THREAD_POWER_THROTTLING_CURRENT_VERSION;
        throttling.ControlMask = THREAD_POWER_THROTTLING_EXECUTION_SPEED;
        throttling.StateMask   = THREAD_POWER_THROTTLING_EXECUTION_SPEED;

        set_info(GetCurrentThread(), ThreadPowerThrottling, &throttling, sizeof(throttling));
    }

    SetThreadDescriptionFn describe = (SetThreadDescriptionFn)(void*)GetProcAddress(kernel, "SetThreadDescription");

    if(describe)
    {
        describe(GetCurrentThread(), L"Ambilight capture");
    }
}

struct AnalysisKey
{
    AnalysisOptions options;
    bool            saturation = false;
    bool            gamma      = false;

    bool operator==(const AnalysisKey& o) const
    {
        return options == o.options && saturation == o.saturation && gamma == o.gamma;
    }
};

static RGBd ToDouble(const RGBi& c)
{
    RGBd d;

    d.r = c.r;
    d.g = c.g;
    d.b = c.b;

    return d;
}

AmbilightEngine::AmbilightEngine(SettingsStore* settings_store, DeviceSender* device_sender, DisplayPowerWatcher* power_watcher)
    : settings(settings_store), sender(device_sender), power(power_watcher)
{
}

AmbilightEngine::~AmbilightEngine()
{
    Stop();
}

void AmbilightEngine::Start()
{
    if(thread_running.load())
    {
        return;
    }

    if(thread.joinable())
    {
        thread.join();
    }

    {
        std::lock_guard<std::mutex> lock(stop_mutex);
        stop_requested = false;
    }

    {
        std::lock_guard<std::mutex> lock(status_mutex);
        status = Status();
        status.state = Starting;
    }

    thread_running = true;
    thread         = std::thread(&AmbilightEngine::Run, this);
}

void AmbilightEngine::Stop()
{
    {
        std::lock_guard<std::mutex> lock(stop_mutex);
        stop_requested = true;
    }

    stop_cv.notify_all();

    if(thread.joinable())
    {
        thread.join();
    }

    thread_running = false;

    std::lock_guard<std::mutex> lock(status_mutex);
    status = Status();
}

AmbilightEngine::Status AmbilightEngine::GetStatus() const
{
    std::lock_guard<std::mutex> lock(status_mutex);
    return status;
}

/*---------------------------------------------------------*\
| Sleep that a Stop() cuts short. True: keep going.         |
\*---------------------------------------------------------*/
bool AmbilightEngine::Pause(double seconds)
{
    std::unique_lock<std::mutex> lock(stop_mutex);

    if(seconds > 0.0)
    {
        stop_cv.wait_for(lock, std::chrono::duration<double>(seconds), [this] { return stop_requested; });
    }

    return !stop_requested;
}

void AmbilightEngine::SetState(State state, const std::string& detail)
{
    std::lock_guard<std::mutex> lock(status_mutex);

    status.state  = state;
    status.detail = detail;
}

void AmbilightEngine::SetBackend(const std::string& backend)
{
    std::lock_guard<std::mutex> lock(status_mutex);
    status.backend = backend;
}

void AmbilightEngine::ShowColor(const RGBi& color)
{
    std::lock_guard<std::mutex> lock(status_mutex);

    status.has_color = true;
    status.color     = color;
}

void AmbilightEngine::Run()
{
    TuneCaptureThread();

    HRESULT com = CoInitializeEx(NULL, COINIT_MULTITHREADED);

    uint64_t          version = settings->Version();
    AmbilightSettings s       = settings->Get();

    /*-----------------------------------------------------*\
    | Wait out a mode switch so the first colours are not   |
    | swallowed. After the first run every device is        |
    | already in Direct and this costs nothing.             |
    \*-----------------------------------------------------*/
    SetState(Starting, "switching devices to Direct mode");

    int switched = sender->EnsureDirectMode();

    if(switched > 0)
    {
        Log("Switched %d device(s) to Direct mode", switched);
    }

    if(switched == 0 || Pause(1.2))
    {
        double applied_br = std::max(1, std::min(100, s.brightness)) / 100.0;

        sender->Start(s.sync_devices, applied_br);

        std::unique_ptr<ScreenGrabber> grabber;
        std::wstring                   grab_device;
        int                            grab_index = -1;

        auto open_grabber = [&]()
        {
            grabber.reset();
            grab_device = s.monitor_device;
            grab_index  = s.monitor_index;

            MonitorInfo monitor;

            if(ResolveMonitor(s.monitor_device, s.monitor_index, monitor))
            {
                grabber.reset(new ScreenGrabber(monitor));
                grabber->log = [](const std::string& message) { Log("Capture: %s", message.c_str()); };

                Log("Capturing %s, %dx%d at (%d,%d)", ToUtf8(monitor.device).c_str(),
                    monitor.width, monitor.height, monitor.left, monitor.top);
            }
        };

        open_grabber();

        AudioMeter      audio;
        double          audio_retry_at  = 0.0;

        FrameAnalyzer   analyzer;
        Frame           frame;
        Frame           pixels;
        bool            has_pixels      = false;
        bool            has_analysed    = false;
        AnalysisKey     analysed_with;
        RGBd            raw;
        double          lum             = 0.0;

        bool            has_prev_sent   = false;
        RGBi            prev_sent;
        bool            has_prev_raw    = false;
        RGBi            prev_raw;
        bool            has_current     = false;
        double          current_f[3]    = { 0.0, 0.0, 0.0 };

        bool            has_lost_since  = false;
        double          lost_since      = 0.0;
        bool            display_blank   = false;
        int             idle_streak     = 0;
        int             force_cycles    = FORCE_CYCLES;
        double          wait            = 0.0;
        double          last_send_time  = NowSeconds();
        bool            has_frame_time  = false;
        double          last_frame_time = 0.0;
        double          current_fps     = std::max(1, s.fps);
        State           shown           = Starting;
        std::string     shown_backend;

        auto show = [&](State state, const std::string& detail = std::string())
        {
            if(state != shown)
            {
                shown = state;
                SetState(state, detail);
            }
        };

        auto send = [&](const RGBi& color)
        {
            sender->Send(color);
            ShowColor(color);
        };

        show(Running);
        Log("Ambilight running: %u device(s), %d FPS, sync %s", (unsigned)sender->DeviceCount(), s.fps,
            s.sync_devices ? "on" : "off");

        std::string     audio_error_logged;

        while(Pause(0.0))
        {
            /*---------------------------------------------*\
            | Live settings. The version is read before the |
            | copy, so an edit landing in between is picked |
            | up next pass instead of being marked as seen  |
            \*---------------------------------------------*/
            uint64_t latest_version = settings->Version();

            if(latest_version != version)
            {
                version = latest_version;
                s       = settings->Get();
            }

            if(s.fps > 0 && (double)s.fps != current_fps)
            {
                current_fps = s.fps;
            }

            const double interval = 1.0 / current_fps;
            const double base_br  = std::max(1, std::min(100, s.brightness)) / 100.0;

            sender->SetSync(s.sync_devices);

            bool force_update = false;

            if(force_cycles > 0)
            {
                force_update = true;
                force_cycles--;
            }

            /*---------------------------------------------*\
            | Music mode. It does not depend on the screen, |
            | so -- as in the Python app -- it keeps        |
            | pulsing with the display asleep or with no    |
            | monitor found                                 |
            \*---------------------------------------------*/
            if(s.audio)
            {
                if(!audio.IsOpen() && NowSeconds() >= audio_retry_at)
                {
                    std::string error;

                    if(audio.Open(error))
                    {
                        Log("Music mode: listening to the default speakers");
                        audio_error_logged.clear();
                    }
                    else
                    {
                        if(error != audio_error_logged)
                        {
                            Log("Music mode: %s (retrying every 5 s)", error.c_str());
                            audio_error_logged = error;
                        }

                        audio_retry_at = NowSeconds() + 5.0;
                    }
                }
            }
            else if(audio.IsOpen())
            {
                audio.Close();
            }

            if(s.audio && audio.IsOpen())
            {
                /*-----------------------------------------*\
                | Back in screen mode with the display      |
                | still off, blank the LEDs again           |
                \*-----------------------------------------*/
                display_blank = false;

                show(Music);

                if(std::fabs(base_br - applied_br) > 0.005)
                {
                    applied_br = base_br;
                    sender->SetBrightness(applied_br);
                    force_update = true;
                }

                double rms = 0.0;

                if(!audio.ReadBlock(rms, [this] { return !Pause(0.0); }))
                {
                    Log("Music mode: speakers went away, retrying");
                    audio.Close();
                    audio_retry_at = NowSeconds() + 2.0;
                    Pause(0.05);
                    continue;
                }

                double intensity = std::min(1.0, rms * 6.0);
                double hue       = std::fmod(NowSeconds(), 10.0) / 10.0;
                double r, g, b;

                HsvToRgb(hue, 1.0, intensity, r, g, b);

                RGBi color;

                color.r = PyInt(r * 255);
                color.g = PyInt(g * 255);
                color.b = PyInt(b * 255);

                int diff = has_prev_sent ? ColorDiff(color, prev_sent) : 999;

                if(diff > 5 || force_update)
                {
                    send(color);
                    prev_sent     = color;
                    has_prev_sent = true;
                    current_f[0]  = color.r;
                    current_f[1]  = color.g;
                    current_f[2]  = color.b;
                    has_current   = true;
                }

                continue;
            }

            if(s.monitor_device != grab_device || s.monitor_index != grab_index)
            {
                open_grabber();
                has_pixels   = false;
                has_analysed = false;
                wait         = 0.0;
            }

            if(!grabber)
            {
                show(CaptureLost, "no monitor found");

                if(!Pause(1.0))
                {
                    break;
                }

                open_grabber();
                continue;
            }

            /*---------------------------------------------*\
            | Windows switched the display off: LEDs off,   |
            | no capture until it comes back                |
            \*---------------------------------------------*/
            if(s.display_sync && power && power->GetState() == DisplayPowerWatcher::Off)
            {
                if(!display_blank)
                {
                    display_blank = true;

                    RGBi black;

                    send(black);
                    has_prev_sent = true;
                    prev_sent     = black;
                    has_current   = true;
                    current_f[0]  = current_f[1] = current_f[2] = 0.0;
                    show(DisplayOff);
                }

                Pause(0.25);
                continue;
            }

            if(display_blank)
            {
                display_blank = false;
                force_cycles  = FORCE_CYCLES;
                wait          = 0.0;
                show(Running);
            }

            /*---------------------------------------------*\
            | Screen capture                                |
            \*---------------------------------------------*/
            double t0 = NowSeconds();

            ScreenGrabber::Result result = grabber->Grab(wait, frame);

            if(wait > 0.0)
            {
                /*-----------------------------------------*\
                | The rest was taken inside Grab(); pace    |
                | from its return                           |
                \*-----------------------------------------*/
                wait = 0.0;
                t0   = NowSeconds();
            }

            if(shown_backend != grabber->Backend())
            {
                shown_backend = grabber->Backend();
                SetBackend(shown_backend);
            }

            if(result == ScreenGrabber::Result::Lost)
            {
                /*-----------------------------------------*\
                | Lock screen, UAC prompt or a mode change. |
                | Give a mode change time to pass before    |
                | calling it a lock.                        |
                \*-----------------------------------------*/
                if(!has_lost_since)
                {
                    has_lost_since = true;
                    lost_since     = t0;
                }
                else if(s.display_sync && has_current && (current_f[0] + current_f[1] + current_f[2]) > 0
                     && t0 - lost_since >= CAPTURE_LOST_BLANK_AFTER)
                {
                    RGBi black;

                    send(black);
                    current_f[0]  = current_f[1] = current_f[2] = 0.0;
                    prev_sent     = black;
                    has_prev_sent = true;
                }

                show(CaptureLost, grabber->LastError());
                Pause(CAPTURE_RETRY_INTERVAL / 2);
                continue;
            }

            has_lost_since = false;
            show(Running);

            bool still   = (result == ScreenGrabber::Result::NoChange);
            bool changed = false;

            if(result == ScreenGrabber::Result::Frame)
            {
                /*-----------------------------------------*\
                | Most desktop updates -- a blinking caret, |
                | a spinner -- miss every sampled pixel,    |
                | and the same pixels give the same colour  |
                \*-----------------------------------------*/
                changed = !has_pixels || !frame.same_pixels(pixels);
                std::swap(pixels, frame);
                has_pixels = true;
            }
            else if(!has_pixels)
            {
                wait = DEEP_IDLE_INTERVAL;      /* no first frame yet */
                continue;
            }

            /*---------------------------------------------*\
            | A still screen keeps easing towards the       |
            | colour it already has; it is only analysed    |
            | again when a setting that shapes the analysis |
            | changes                                       |
            \*---------------------------------------------*/
            AnalysisKey key;

            key.options.bars            = s.bars;
            key.options.black_threshold = s.black_threshold;
            key.options.edges           = s.edges;
            key.options.algo            = s.algo;
            key.saturation              = s.saturation;
            key.gamma                   = s.gamma;

            if(changed || !has_analysed || !(key == analysed_with))
            {
                analysed_with = key;
                has_analysed  = true;

                raw = analyzer.Analyze(pixels, key.options);
                lum = Luminance(raw);

                if(s.saturation)
                {
                    raw = ToDouble(ApplySaturation(raw, 1.5));
                }

                if(s.gamma)
                {
                    raw = ToDouble(ApplyGamma(raw, 1.8));
                }
            }

            /*---------------------------------------------*\
            | Brightness reaches the sender only when it    |
            | moves, so HDR mode does not force a re-send   |
            | on every frame of a dark scene                |
            \*---------------------------------------------*/
            double br = base_br;

            if(s.hdr)
            {
                br *= std::max(0.2, std::min(1.0, (lum / 255.0) * 2.0));
            }

            if(std::fabs(br - applied_br) > (s.hdr ? 0.05 : 0.005))
            {
                applied_br = br;
                sender->SetBrightness(applied_br);
                force_update = true;
            }

            /*---------------------------------------------*\
            | Wake-up detection on the raw screen colour    |
            \*---------------------------------------------*/
            RGBi raw_i;

            raw_i.r = PyInt(raw.r);
            raw_i.g = PyInt(raw.g);
            raw_i.b = PyInt(raw.b);

            if(has_prev_raw && ColorDiff(raw_i, prev_raw) > RAW_WAKE_THRESHOLD)
            {
                idle_streak = 0;
            }

            prev_raw     = raw_i;
            has_prev_raw = true;

            /*---------------------------------------------*\
            | Step the filter by real elapsed time, not the |
            | nominal interval                              |
            \*---------------------------------------------*/
            double now_f = NowSeconds();
            double dt    = has_frame_time ? (now_f - last_frame_time) : interval;

            last_frame_time = now_f;
            has_frame_time  = true;

            if(!has_current)
            {
                current_f[0] = raw.r;
                current_f[1] = raw.g;
                current_f[2] = raw.b;
                has_current  = true;
            }
            else
            {
                double a = SmoothAlpha(s.smoothness, dt);

                current_f[0] += (raw.r - current_f[0]) * a;
                current_f[1] += (raw.g - current_f[1]) * a;
                current_f[2] += (raw.b - current_f[2]) * a;
            }

            RGBi color;

            color.r = PyInt(current_f[0]);
            color.g = PyInt(current_f[1]);
            color.b = PyInt(current_f[2]);

            /*---------------------------------------------*\
            | Minimum brightness floor                      |
            \*---------------------------------------------*/
            if(s.min_brightness > 0)
            {
                int mx    = std::max(std::max(color.r, color.g), color.b);
                int floor = PyInt(255 * s.min_brightness / 100.0);

                if(mx < floor && floor > 0)
                {
                    if(mx > 0)
                    {
                        double scale = (double)floor / (double)mx;

                        color.r = std::min(255, PyInt(color.r * scale));
                        color.g = std::min(255, PyInt(color.g * scale));
                        color.b = std::min(255, PyInt(color.b * scale));
                    }
                    else
                    {
                        color.r = color.g = color.b = floor;
                    }
                }
            }

            int diff = has_prev_sent ? ColorDiff(color, prev_sent) : 999;

            /*---------------------------------------------*\
            | Forced periodic re-send against long freezes  |
            \*---------------------------------------------*/
            double now = NowSeconds();

            if(now - last_send_time >= FORCE_RESEND_INTERVAL)
            {
                force_update = true;
            }

            if(diff > 3 || force_update)
            {
                send(color);
                prev_sent      = color;
                has_prev_sent  = true;
                last_send_time = now;
            }

            /*---------------------------------------------*\
            | A forced re-send is not activity, or a still  |
            | screen would never reach deep idle            |
            \*---------------------------------------------*/
            if(diff > 3)
            {
                idle_streak = 0;
            }
            else
            {
                idle_streak++;
            }

            double elapsed = NowSeconds() - t0;
            double sleep;

            LoopRest(idle_streak, interval, elapsed, still, sleep, wait);

            if(sleep > 0.0)
            {
                Pause(sleep);
            }
        }

        grabber.reset();
        audio.Close();
        sender->Stop();
    }

    if(SUCCEEDED(com))
    {
        CoUninitialize();
    }

    Log("Ambilight stopped");
    thread_running = false;
}

}
