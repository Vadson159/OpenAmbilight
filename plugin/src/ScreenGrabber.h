/*---------------------------------------------------------*\
| ScreenGrabber.h                                           |
|                                                           |
|   Downsampled RGB snapshots of one monitor, taken when    |
|   the capture loop asks (port of ScreenGrabber in         |
|   Ambilight.pyw).                                         |
|                                                           |
|   Windows hands each new desktop frame over as a texture  |
|   on the GPU. A pixel shader keeps one pixel in every     |
|   `step` on each axis, and only that thumbnail -- 128x72  |
|   for 2560x1440 -- is copied to memory the CPU can read.  |
|   A full-frame readback sampled on the CPU is kept as the |
|   fallback for a driver that will not run the shader; it  |
|   returns exactly the same pixels. GDI is the last resort |
|   when Desktop Duplication is not available at all.       |
|                                                           |
|   Grab() returns                                          |
|     Frame     a new frame in `out`                        |
|     NoChange  the desktop has not changed                 |
|     Lost      nothing can be captured now (lock screen,   |
|               UAC prompt, mode change); Grab() keeps      |
|               retrying on its own schedule                |
|   Grab(wait) blocks up to `wait` seconds for a new frame, |
|   so an idle loop can sleep inside Windows and still      |
|   react at once.                                          |
\*---------------------------------------------------------*/

#pragma once

#include <functional>
#include <string>
#include <vector>

#include "ColorMath.h"
#include "Monitors.h"

struct ID3D11Device;
struct ID3D11DeviceContext;
struct ID3D11PixelShader;
struct ID3D11RenderTargetView;
struct ID3D11Texture2D;
struct ID3D11VertexShader;
struct IDXGIOutput1;
struct IDXGIOutputDuplication;

namespace ambi
{

/*---------------------------------------------------------*\
| How long to wait before trying to get the desktop back    |
| after Windows took it away. Behind the lock screen the    |
| secure desktop refuses duplication for as long as it is   |
| up; retrying with no pause would spin a core.             |
\*---------------------------------------------------------*/
constexpr double CAPTURE_RETRY_INTERVAL   = 0.5;

/*---------------------------------------------------------*\
| With the desktop gone this long, "Turn off with display"  |
| blanks the LEDs. Shorter losses are mode changes.         |
\*---------------------------------------------------------*/
constexpr double CAPTURE_LOST_BLANK_AFTER = 1.0;

double NowSeconds();

class ScreenGrabber
{
public:
    enum class Result
    {
        Frame,
        NoChange,
        Lost,
    };

    explicit ScreenGrabber(const MonitorInfo& monitor, int step = DOWNSAMPLE);
    ~ScreenGrabber();

    ScreenGrabber(const ScreenGrabber&)            = delete;
    ScreenGrabber& operator=(const ScreenGrabber&) = delete;

    Result          Grab(double wait, Frame& out);

    const char*     Backend() const;
    std::string     LastError() const       { return last_error; }
    std::wstring    Display() const         { return display; }
    const MonitorInfo& Monitor() const      { return monitor; }

    /*-----------------------------------------------------*\
    | Test hooks                                            |
    \*-----------------------------------------------------*/
    void            SetShaderEnabled(bool enabled);
    bool            GrabBothForTest(double wait, Frame& shader_frame, Frame& full_frame);
    void            ForceGdiForTest()       { CloseDxgi(true); gdi = true; }
    void            DropForTest(bool release_device) { CloseDxgi(release_device); retry_at = 0.0; rebuild_at = 0.0; }
    bool            IsOpenForTest() const   { return dup != nullptr; }

    std::function<void(const std::string&)> log;

private:
    long            FindOutput(void** adapter_out, void** output_out);
    long            OpenDevice();
    long            CreateShaders();
    long            Open();
    void            CloseDxgi(bool release_device);
    long            AcquireFrame(double wait, ID3D11Texture2D** texture, Result& result);
    long            GrabDxgi(double wait, Frame& out, Result& result);
    long            ReadStage(Frame& out);
    Result          GrabGdi(double wait, Frame& out);
    void            Rotate(Frame& frame);
    void            Note(long hr, const char* what);

    MonitorInfo             monitor;
    int                     step;

    ID3D11Device*           device          = nullptr;
    ID3D11DeviceContext*    context         = nullptr;
    IDXGIOutput1*           output          = nullptr;
    ID3D11VertexShader*     vertex_shader   = nullptr;
    ID3D11PixelShader*      pixel_shader    = nullptr;
    bool                    use_shader      = true;
    IDXGIOutputDuplication* dup             = nullptr;
    ID3D11Texture2D*        target          = nullptr;
    ID3D11RenderTargetView* target_view     = nullptr;
    ID3D11Texture2D*        stage           = nullptr;
    bool                    stage_is_full   = false;

    int                     stage_rows      = 0;
    int                     stride          = 1;
    int                     size_w          = 0;
    int                     size_h          = 0;
    int                     rotation        = 0;

    double                  retry_at        = 0.0;
    double                  rebuild_at      = 0.0;
    std::wstring            display;
    bool                    ever_opened     = false;
    bool                    gdi             = false;

    std::string             last_error;
    std::string             last_logged;
    std::vector<uint8_t>    rotate_scratch;
    std::vector<uint8_t>    gdi_pixels;
};

}
