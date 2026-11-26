/*---------------------------------------------------------*\
| ScreenGrabber.cpp                                         |
|                                                           |
|   See ScreenGrabber.h. Desktop Duplication on the one     |
|   adapter that drives the captured monitor (a D3D11       |
|   device costs ~25 MB and a handful of driver threads,    |
|   so there is exactly one).                               |
\*---------------------------------------------------------*/

#include "ScreenGrabber.h"

#include <windows.h>
#include <d3d11.h>
#include <d3dcompiler.h>
#include <dxgi1_2.h>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <thread>

namespace ambi
{

double NowSeconds()
{
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

template<class T>
static void SafeRelease(T*& object)
{
    if(object)
    {
        object->Release();
        object = nullptr;
    }
}

/*---------------------------------------------------------*\
| Point-samples every STEP-th pixel of the desktop into a   |
| render target 1/STEP the size on each axis: one triangle  |
| covering the target, and a pixel shader that fetches the  |
| texel its own position maps to.                           |
\*---------------------------------------------------------*/
static const char DOWNSAMPLE_HLSL[] =
    "Texture2D<float4> desktop : register(t0);\n"
    "\n"
    "float4 vs(uint id : SV_VertexID) : SV_Position\n"
    "{\n"
    "    return float4((id & 1) * 4.0 - 1.0, 1.0 - (id >> 1) * 4.0, 0.0, 1.0);\n"
    "}\n"
    "\n"
    "float4 ps(float4 pos : SV_Position) : SV_Target\n"
    "{\n"
    "    return desktop.Load(int3(int2(pos.xy) * STEP, 0));\n"
    "}\n";

typedef HRESULT (WINAPI *D3DCompileFn)(LPCVOID, SIZE_T, LPCSTR, const D3D_SHADER_MACRO*, ID3DInclude*,
                                       LPCSTR, LPCSTR, UINT, UINT, ID3DBlob**, ID3DBlob**);

/*---------------------------------------------------------*\
| The HLSL compiler that ships in Windows                   |
\*---------------------------------------------------------*/
static D3DCompileFn LoadCompiler()
{
    static D3DCompileFn compile = []() -> D3DCompileFn
    {
        HMODULE module = LoadLibraryExW(L"d3dcompiler_47.dll", NULL, LOAD_LIBRARY_SEARCH_SYSTEM32);

        if(!module)
        {
            return nullptr;
        }

        return (D3DCompileFn)(void*)GetProcAddress(module, "D3DCompile");
    }();

    return compile;
}

ScreenGrabber::ScreenGrabber(const MonitorInfo& monitor_info, int step_size)
    : monitor(monitor_info), step(std::max(1, step_size))
{
}

ScreenGrabber::~ScreenGrabber()
{
    CloseDxgi(true);
}

const char* ScreenGrabber::Backend() const
{
    if(gdi)
    {
        return "GDI";
    }

    return stage_is_full ? "DXGI (full readback)" : "DXGI";
}

void ScreenGrabber::Note(long hr, const char* what)
{
    char text[200];

    snprintf(text, sizeof(text), "%s failed: 0x%08lX", what, (unsigned long)hr);
    last_error = text;

    if(log && last_error != last_logged)
    {
        last_logged = last_error;
        log(last_error);
    }
}

/*---------------------------------------------------------*\
| (adapter, output) showing the monitor, as owned pointers  |
|                                                           |
|   The DXGI output name is the GDI device name, so that is |
|   matched first; the desktop origin and then the index    |
|   are fallbacks. Once found, the monitor is pinned by     |
|   name: a later search (after a driver reset, say) takes  |
|   that display or nothing, so a switched-off monitor does |
|   not hand the LEDs to whatever moved into its place.     |
\*---------------------------------------------------------*/
long ScreenGrabber::FindOutput(void** adapter_out, void** output_out)
{
    IDXGIFactory1* factory = nullptr;
    HRESULT        hr      = CreateDXGIFactory1(__uuidof(IDXGIFactory1), (void**)&factory);

    if(FAILED(hr))
    {
        return hr;
    }

    struct Found
    {
        IDXGIAdapter1* adapter;
        IDXGIOutput*   output;
        std::wstring   name;
        LONG           left;
        LONG           top;
    };

    std::vector<Found> found;

    for(UINT adapter_idx = 0; ; adapter_idx++)
    {
        IDXGIAdapter1* adapter = nullptr;

        if(FAILED(factory->EnumAdapters1(adapter_idx, &adapter)))
        {
            break;
        }

        for(UINT output_idx = 0; ; output_idx++)
        {
            IDXGIOutput* output_ptr = nullptr;

            if(FAILED(adapter->EnumOutputs(output_idx, &output_ptr)))
            {
                break;
            }

            DXGI_OUTPUT_DESC desc;

            ZeroMemory(&desc, sizeof(desc));
            output_ptr->GetDesc(&desc);

            adapter->AddRef();
            found.push_back({ adapter, output_ptr, desc.DeviceName, desc.DesktopCoordinates.left, desc.DesktopCoordinates.top });
        }

        adapter->Release();
    }

    factory->Release();

    int pick = -1;

    if(!display.empty())
    {
        for(size_t i = 0; i < found.size(); i++)
        {
            if(found[i].name == display)
            {
                pick = (int)i;
                break;
            }
        }
    }
    else
    {
        for(size_t i = 0; i < found.size() && pick < 0 && !monitor.device.empty(); i++)
        {
            if(_wcsicmp(found[i].name.c_str(), monitor.device.c_str()) == 0)
            {
                pick = (int)i;
            }
        }

        for(size_t i = 0; i < found.size() && pick < 0; i++)
        {
            if(found[i].left == monitor.left && found[i].top == monitor.top)
            {
                pick = (int)i;
            }
        }

        if(pick < 0 && !found.empty())
        {
            pick = (monitor.index >= 0 && monitor.index < (int)found.size()) ? monitor.index : 0;
        }
    }

    for(size_t i = 0; i < found.size(); i++)
    {
        if((int)i == pick)
        {
            *adapter_out = found[i].adapter;
            *output_out  = found[i].output;
            display      = found[i].name;
        }
        else
        {
            found[i].output->Release();
            found[i].adapter->Release();
        }
    }

    return (pick < 0) ? DXGI_ERROR_NOT_FOUND : S_OK;
}

long ScreenGrabber::OpenDevice()
{
    IDXGIAdapter1* adapter    = nullptr;
    IDXGIOutput*   output_ptr = nullptr;

    HRESULT hr = FindOutput((void**)&adapter, (void**)&output_ptr);

    if(FAILED(hr))
    {
        return hr;
    }

    IDXGIOutput1* output1 = nullptr;

    hr = output_ptr->QueryInterface(__uuidof(IDXGIOutput1), (void**)&output1);

    if(SUCCEEDED(hr))
    {
        /*-------------------------------------------------*\
        | The driver's own worker threads speed up draw-    |
        | call-heavy rendering; there are a handful of      |
        | calls a frame here.                               |
        \*-------------------------------------------------*/
        const D3D_FEATURE_LEVEL levels[] = { D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_10_1, D3D_FEATURE_LEVEL_10_0 };

        hr = D3D11CreateDevice(adapter, D3D_DRIVER_TYPE_UNKNOWN, NULL,
                               D3D11_CREATE_DEVICE_PREVENT_INTERNAL_THREADING_OPTIMIZATIONS,
                               levels, 3, D3D11_SDK_VERSION, &device, NULL, &context);

        if(FAILED(hr))
        {
            SafeRelease(output1);
        }
    }

    output_ptr->Release();
    adapter->Release();

    if(FAILED(hr))
    {
        return hr;
    }

    output = output1;

    if(use_shader)
    {
        HRESULT shader_hr = CreateShaders();

        if(FAILED(shader_hr))
        {
            Note(shader_hr, "building the downsampling shader (using full-frame readback)");
            use_shader = false;
        }
    }

    return S_OK;
}

long ScreenGrabber::CreateShaders()
{
    D3DCompileFn compile = LoadCompiler();

    if(!compile)
    {
        return E_NOINTERFACE;
    }

    char step_text[16];

    snprintf(step_text, sizeof(step_text), "%d", step);

    const D3D_SHADER_MACRO macros[] = { { "STEP", step_text }, { NULL, NULL } };

    ID3DBlob* vs_blob = nullptr;
    ID3DBlob* ps_blob = nullptr;
    ID3DBlob* errors  = nullptr;

    HRESULT hr = compile(DOWNSAMPLE_HLSL, strlen(DOWNSAMPLE_HLSL), NULL, macros, NULL, "vs", "vs_4_0", 0, 0, &vs_blob, &errors);
    SafeRelease(errors);

    if(SUCCEEDED(hr))
    {
        hr = compile(DOWNSAMPLE_HLSL, strlen(DOWNSAMPLE_HLSL), NULL, macros, NULL, "ps", "ps_4_0", 0, 0, &ps_blob, &errors);
        SafeRelease(errors);
    }

    if(SUCCEEDED(hr))
    {
        hr = device->CreateVertexShader(vs_blob->GetBufferPointer(), vs_blob->GetBufferSize(), NULL, &vertex_shader);
    }

    if(SUCCEEDED(hr))
    {
        hr = device->CreatePixelShader(ps_blob->GetBufferPointer(), ps_blob->GetBufferSize(), NULL, &pixel_shader);
    }

    SafeRelease(vs_blob);
    SafeRelease(ps_blob);

    if(FAILED(hr))
    {
        SafeRelease(vertex_shader);
        SafeRelease(pixel_shader);
    }

    return hr;
}

static D3D11_TEXTURE2D_DESC TextureDesc(int width, int height, D3D11_USAGE usage, UINT bind, UINT cpu_access)
{
    D3D11_TEXTURE2D_DESC desc;

    ZeroMemory(&desc, sizeof(desc));
    desc.Width              = (UINT)width;
    desc.Height             = (UINT)height;
    desc.MipLevels          = 1;
    desc.ArraySize          = 1;
    desc.Format             = DXGI_FORMAT_B8G8R8A8_UNORM;
    desc.SampleDesc.Count   = 1;
    desc.Usage              = usage;
    desc.BindFlags          = bind;
    desc.CPUAccessFlags     = cpu_access;

    return desc;
}

long ScreenGrabber::Open()
{
    HRESULT hr;

    if(!device)
    {
        hr = OpenDevice();

        if(FAILED(hr))
        {
            return hr;
        }
    }

    DXGI_OUTPUT_DESC desc;

    hr = output->GetDesc(&desc);

    if(FAILED(hr))
    {
        return hr;
    }

    int width  = desc.DesktopCoordinates.right - desc.DesktopCoordinates.left;
    int height = desc.DesktopCoordinates.bottom - desc.DesktopCoordinates.top;

    static const int rotations[] = { 0, 0, 90, 180, 270 };

    rotation = ((unsigned)desc.Rotation < 5) ? rotations[desc.Rotation] : 0;

    if(rotation == 90 || rotation == 270)
    {
        /*-------------------------------------------------*\
        | The duplicated texture is the panel's own         |
        | orientation                                       |
        \*-------------------------------------------------*/
        std::swap(width, height);
    }

    hr = output->DuplicateOutput(device, &dup);

    if(FAILED(hr))
    {
        return hr;
    }

    if(use_shader && vertex_shader && pixel_shader)
    {
        const int cols = (width  + step - 1) / step;
        const int rows = (height + step - 1) / step;

        D3D11_TEXTURE2D_DESC td = TextureDesc(cols, rows, D3D11_USAGE_DEFAULT, D3D11_BIND_RENDER_TARGET, 0);

        hr = device->CreateTexture2D(&td, NULL, &target);

        if(SUCCEEDED(hr))
        {
            hr = device->CreateRenderTargetView(target, NULL, &target_view);
        }

        if(SUCCEEDED(hr))
        {
            td = TextureDesc(cols, rows, D3D11_USAGE_STAGING, 0, D3D11_CPU_ACCESS_READ);
            hr = device->CreateTexture2D(&td, NULL, &stage);
        }

        if(FAILED(hr))
        {
            return hr;
        }

        stage_rows    = rows;
        stride        = 1;
        stage_is_full = false;

        /*-------------------------------------------------*\
        | Nothing else draws on this context, so the        |
        | pipeline is set once per duplication              |
        \*-------------------------------------------------*/
        D3D11_VIEWPORT viewport;

        viewport.TopLeftX = 0.0f;
        viewport.TopLeftY = 0.0f;
        viewport.Width    = (float)cols;
        viewport.Height   = (float)rows;
        viewport.MinDepth = 0.0f;
        viewport.MaxDepth = 1.0f;

        context->IASetInputLayout(NULL);
        context->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
        context->VSSetShader(vertex_shader, NULL, 0);
        context->PSSetShader(pixel_shader, NULL, 0);
        context->RSSetViewports(1, &viewport);
        context->OMSetRenderTargets(1, &target_view, NULL);
    }
    else
    {
        D3D11_TEXTURE2D_DESC td = TextureDesc(width, height, D3D11_USAGE_STAGING, 0, D3D11_CPU_ACCESS_READ);

        hr = device->CreateTexture2D(&td, NULL, &stage);

        if(FAILED(hr))
        {
            return hr;
        }

        stage_rows    = height;
        stride        = step;
        stage_is_full = true;
    }

    size_w      = width;
    size_h      = height;
    ever_opened = true;
    last_logged.clear();

    return S_OK;
}

void ScreenGrabber::CloseDxgi(bool release_device)
{
    if(context)
    {
        context->ClearState();
    }

    SafeRelease(target_view);
    SafeRelease(target);
    SafeRelease(stage);
    SafeRelease(dup);

    if(release_device)
    {
        SafeRelease(vertex_shader);
        SafeRelease(pixel_shader);
        SafeRelease(output);
        SafeRelease(context);
        SafeRelease(device);
    }
}

void ScreenGrabber::SetShaderEnabled(bool enabled)
{
    use_shader = enabled;
    CloseDxgi(true);
    retry_at   = 0.0;
    rebuild_at = 0.0;
}

ScreenGrabber::Result ScreenGrabber::Grab(double wait, Frame& out)
{
    if(gdi)
    {
        return GrabGdi(wait, out);
    }

    if(!dup)
    {
        double now = NowSeconds();

        if(now < retry_at)
        {
            return Result::Lost;
        }

        HRESULT hr = Open();

        if(FAILED(hr))
        {
            Note(hr, "opening Desktop Duplication");

            /*---------------------------------------------*\
            | No Desktop Duplication on this output at all  |
            | (some hybrid-GPU laptops): use GDI for good   |
            \*---------------------------------------------*/
            if(!ever_opened && (hr == DXGI_ERROR_UNSUPPORTED || hr == E_NOTIMPL))
            {
                CloseDxgi(true);
                gdi = true;

                if(log)
                {
                    log("Desktop Duplication unsupported here, capturing through GDI");
                }

                return GrabGdi(wait, out);
            }

            /*---------------------------------------------*\
            | The secure desktop refuses for as long as it  |
            | is up, and the device is fine. Anything else  |
            | -- a driver reset, a monitor switched off and |
            | on -- needs the device and output found       |
            | again, which is worth doing only now and      |
            | then: a new device costs ~35 ms of CPU.       |
            \*---------------------------------------------*/
            bool rebuild = (hr != E_ACCESSDENIED) && now >= rebuild_at;

            if(rebuild)
            {
                rebuild_at = now + 10.0;
            }

            CloseDxgi(rebuild);
            retry_at = now + CAPTURE_RETRY_INTERVAL;

            return Result::Lost;
        }
    }

    Result  result = Result::NoChange;
    HRESULT hr     = GrabDxgi(wait, out, result);

    if(FAILED(hr))
    {
        /*-------------------------------------------------*\
        | Almost always DXGI_ERROR_ACCESS_LOST: a mode      |
        | change, a game going exclusive full screen, or    |
        | the secure desktop. Re-open on a schedule; the    |
        | first attempt waits out the mode change.          |
        \*-------------------------------------------------*/
        Note(hr, "capturing a frame");
        CloseDxgi(false);
        retry_at = NowSeconds() + 0.1;

        return Result::Lost;
    }

    return result;
}

long ScreenGrabber::AcquireFrame(double wait, ID3D11Texture2D** texture, Result& result)
{
    DXGI_OUTDUPL_FRAME_INFO info;
    IDXGIResource*          resource = nullptr;
    double                  deadline = NowSeconds() + wait;

    *texture = nullptr;

    while(true)
    {
        double  left    = deadline - NowSeconds();
        UINT    timeout = (left > 0.0) ? (UINT)(left * 1000.0) : 0;
        HRESULT hr      = dup->AcquireNextFrame(timeout, &info, &resource);

        if(hr == DXGI_ERROR_WAIT_TIMEOUT)
        {
            result = Result::NoChange;
            return S_OK;
        }

        if(FAILED(hr))
        {
            return hr;
        }

        if(info.LastPresentTime.QuadPart != 0)
        {
            break;
        }

        /*-------------------------------------------------*\
        | No desktop update, only the pointer. That         |
        | includes the very first frame of a duplication,   |
        | whose texture is still empty -- copying it would  |
        | turn the LEDs black until the screen moves.       |
        \*-------------------------------------------------*/
        SafeRelease(resource);
        dup->ReleaseFrame();

        left = deadline - NowSeconds();

        if(left <= 0.0)
        {
            result = Result::NoChange;
            return S_OK;
        }

        /*-------------------------------------------------*\
        | A moving pointer delivers these at the refresh    |
        | rate; wait them out in one sleep, not one wake-up |
        | each.                                             |
        \*-------------------------------------------------*/
        std::this_thread::sleep_for(std::chrono::duration<double>(left));
        deadline = 0.0;
    }

    HRESULT hr = resource->QueryInterface(__uuidof(ID3D11Texture2D), (void**)texture);

    resource->Release();

    if(FAILED(hr))
    {
        dup->ReleaseFrame();
        return hr;
    }

    return S_OK;
}

/*---------------------------------------------------------*\
| Copy every `sample`-th pixel of a mapped BGRA texture     |
| into an RGB frame of `cols` x `rows`                       |
\*---------------------------------------------------------*/
static long ReadTexture(ID3D11DeviceContext* context, ID3D11Texture2D* texture, int cols, int rows, int sample, Frame& out)
{
    D3D11_MAPPED_SUBRESOURCE mapped;

    HRESULT hr = context->Map(texture, 0, D3D11_MAP_READ, 0, &mapped);

    if(FAILED(hr))
    {
        return hr;
    }

    out.width  = cols;
    out.height = rows;
    out.rgb.resize((size_t)cols * rows * 3);

    const uint8_t* base = (const uint8_t*)mapped.pData;
    uint8_t*       dst  = out.rgb.data();

    for(int y = 0; y < rows; y++)
    {
        const uint8_t* src = base + (size_t)y * sample * mapped.RowPitch;

        for(int x = 0; x < cols; x++, dst += 3)
        {
            const uint8_t* px = src + (size_t)x * sample * 4;

            dst[0] = px[2];
            dst[1] = px[1];
            dst[2] = px[0];
        }
    }

    context->Unmap(texture, 0);

    return S_OK;
}

long ScreenGrabber::ReadStage(Frame& out)
{
    const int cols = (size_w + step - 1) / step;
    const int rows = stage_is_full ? (stage_rows + stride - 1) / stride : stage_rows;

    HRESULT hr = ReadTexture(context, stage, cols, rows, stride, out);

    if(SUCCEEDED(hr) && rotation != 0)
    {
        Rotate(out);
    }

    return hr;
}

long ScreenGrabber::GrabDxgi(double wait, Frame& out, Result& result)
{
    ID3D11Texture2D* texture = nullptr;

    HRESULT hr = AcquireFrame(wait, &texture, result);

    if(FAILED(hr) || !texture)
    {
        return hr;
    }

    if(!stage_is_full)
    {
        ID3D11ShaderResourceView* view = nullptr;

        hr = device->CreateShaderResourceView(texture, NULL, &view);

        if(FAILED(hr))
        {
            /*---------------------------------------------*\
            | This driver will not sample the desktop       |
            | texture: reopen reading full frames, and stay |
            | that way                                      |
            \*---------------------------------------------*/
            Note(hr, "sampling the desktop texture (switching to full-frame readback)");
            texture->Release();
            dup->ReleaseFrame();

            use_shader = false;
            rebuild_at = 0.0;
            CloseDxgi(true);

            result = Result::Lost;
            return S_OK;
        }

        ID3D11ShaderResourceView* none = nullptr;

        context->PSSetShaderResources(0, 1, &view);
        context->Draw(3, 0);
        context->PSSetShaderResources(0, 1, &none);
        view->Release();

        context->CopyResource(stage, target);
    }
    else
    {
        context->CopyResource(stage, texture);
    }

    texture->Release();
    dup->ReleaseFrame();

    /*-----------------------------------------------------*\
    | Map waits for the copy                                |
    \*-----------------------------------------------------*/
    hr = ReadStage(out);

    if(FAILED(hr))
    {
        return hr;
    }

    result = Result::Frame;
    return S_OK;
}

/*---------------------------------------------------------*\
| numpy's rot90 as Ambilight.pyw applies it per rotation    |
\*---------------------------------------------------------*/
void ScreenGrabber::Rotate(Frame& frame)
{
    const int w = frame.width;
    const int h = frame.height;

    rotate_scratch.resize(frame.rgb.size());

    const uint8_t* src = frame.rgb.data();
    uint8_t*       dst = rotate_scratch.data();

    if(rotation == 180)
    {
        for(int i = 0; i < h; i++)
        {
            for(int j = 0; j < w; j++)
            {
                memcpy(dst + ((size_t)i * w + j) * 3, src + ((size_t)(h - 1 - i) * w + (w - 1 - j)) * 3, 3);
            }
        }
    }
    else
    {
        /*-------------------------------------------------*\
        | 90:  rot90(m, axes=(1, 0)): out[i][j] = m[h-1-j][i] |
        | 270: rot90(m, axes=(0, 1)): out[i][j] = m[j][w-1-i] |
        | Both give a w x h (rows x cols) result.            |
        \*-------------------------------------------------*/
        for(int i = 0; i < w; i++)
        {
            for(int j = 0; j < h; j++)
            {
                const uint8_t* from = (rotation == 90)
                                    ? src + ((size_t)(h - 1 - j) * w + i) * 3
                                    : src + ((size_t)j * w + (w - 1 - i)) * 3;

                memcpy(dst + ((size_t)i * h + j) * 3, from, 3);
            }
        }

        frame.width  = h;
        frame.height = w;
    }

    frame.rgb.swap(rotate_scratch);
}

ScreenGrabber::Result ScreenGrabber::GrabGdi(double wait, Frame& out)
{
    if(wait > 0.0)
    {
        std::this_thread::sleep_for(std::chrono::duration<double>(wait));
    }

    const int width  = monitor.width;
    const int height = monitor.height;

    if(width <= 0 || height <= 0)
    {
        return Result::Lost;
    }

    HDC screen = GetDC(NULL);
    HDC memory = CreateCompatibleDC(screen);

    BITMAPINFO bmi;

    ZeroMemory(&bmi, sizeof(bmi));
    bmi.bmiHeader.biSize        = sizeof(BITMAPINFOHEADER);
    bmi.bmiHeader.biWidth       = width;
    bmi.bmiHeader.biHeight      = -height;
    bmi.bmiHeader.biPlanes      = 1;
    bmi.bmiHeader.biBitCount    = 32;
    bmi.bmiHeader.biCompression = BI_RGB;

    void*   bits   = nullptr;
    HBITMAP dib    = CreateDIBSection(screen, &bmi, DIB_RGB_COLORS, &bits, NULL, 0);
    bool    copied = false;

    if(dib)
    {
        HGDIOBJ old = SelectObject(memory, dib);

        if(BitBlt(memory, 0, 0, width, height, screen, monitor.left, monitor.top, SRCCOPY | CAPTUREBLT))
        {
            GdiFlush();

            const int cols = (width  + step - 1) / step;
            const int rows = (height + step - 1) / step;

            out.width  = cols;
            out.height = rows;
            out.rgb.resize((size_t)cols * rows * 3);

            const uint8_t* base = (const uint8_t*)bits;
            uint8_t*       dst  = out.rgb.data();

            for(int y = 0; y < rows; y++)
            {
                const uint8_t* src = base + (size_t)y * step * width * 4;

                for(int x = 0; x < cols; x++, dst += 3)
                {
                    const uint8_t* px = src + (size_t)x * step * 4;

                    dst[0] = px[2];
                    dst[1] = px[1];
                    dst[2] = px[0];
                }
            }

            copied = true;
        }

        SelectObject(memory, old);
        DeleteObject(dib);
    }

    DeleteDC(memory);
    ReleaseDC(NULL, screen);

    return copied ? Result::Frame : Result::Lost;
}

/*---------------------------------------------------------*\
| One desktop frame through both paths: the shader          |
| thumbnail and a full readback sampled on the CPU          |
\*---------------------------------------------------------*/
bool ScreenGrabber::GrabBothForTest(double wait, Frame& shader_frame, Frame& full_frame)
{
    if(!dup && FAILED(Open()))
    {
        return false;
    }

    if(stage_is_full)
    {
        return false;
    }

    D3D11_TEXTURE2D_DESC td   = TextureDesc(size_w, size_h, D3D11_USAGE_STAGING, 0, D3D11_CPU_ACCESS_READ);
    ID3D11Texture2D*     full = nullptr;

    if(FAILED(device->CreateTexture2D(&td, NULL, &full)))
    {
        return false;
    }

    ID3D11Texture2D* texture  = nullptr;
    Result           result   = Result::NoChange;
    double           deadline = NowSeconds() + wait;

    while(!texture && NowSeconds() < deadline)
    {
        if(FAILED(AcquireFrame(0.2, &texture, result)))
        {
            break;
        }
    }

    if(!texture)
    {
        full->Release();
        return false;
    }

    ID3D11ShaderResourceView* view = nullptr;
    ID3D11ShaderResourceView* none = nullptr;
    bool                      ok   = SUCCEEDED(device->CreateShaderResourceView(texture, NULL, &view));

    if(ok)
    {
        context->PSSetShaderResources(0, 1, &view);
        context->Draw(3, 0);
        context->PSSetShaderResources(0, 1, &none);
        view->Release();
        context->CopyResource(stage, target);
        context->CopyResource(full, texture);
    }

    texture->Release();
    dup->ReleaseFrame();

    if(ok)
    {
        ok = SUCCEEDED(ReadStage(shader_frame))
          && SUCCEEDED(ReadTexture(context, full, (size_w + step - 1) / step, (size_h + step - 1) / step, step, full_frame));

        if(ok && rotation != 0)
        {
            Rotate(full_frame);
        }
    }

    full->Release();

    return ok;
}

}
