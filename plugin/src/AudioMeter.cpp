/*---------------------------------------------------------*\
| AudioMeter.cpp                                            |
\*---------------------------------------------------------*/

#include "AudioMeter.h"

#include <windows.h>
#include <audioclient.h>
#include <mmdeviceapi.h>
#include <mmreg.h>

#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <thread>

#include "ScreenGrabber.h"

namespace ambi
{

/*---------------------------------------------------------*\
| KSDATAFORMAT_SUBTYPE_IEEE_FLOAT / _PCM                    |
\*---------------------------------------------------------*/
static const GUID SUBTYPE_IEEE_FLOAT = { 0x00000003, 0x0000, 0x0010, { 0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71 } };
static const GUID SUBTYPE_PCM        = { 0x00000001, 0x0000, 0x0010, { 0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71 } };

template<class T>
static void SafeRelease(T*& object)
{
    if(object)
    {
        object->Release();
        object = nullptr;
    }
}

static std::string HrText(const char* what, HRESULT hr)
{
    char text[160];

    snprintf(text, sizeof(text), "%s failed: 0x%08lX", what, (unsigned long)hr);
    return text;
}

AudioMeter::~AudioMeter()
{
    Close();
}

bool AudioMeter::Open(std::string& error)
{
    Close();

    IMMDeviceEnumerator* enumerator = nullptr;
    WAVEFORMATEX*        format     = nullptr;

    HRESULT hr = CoCreateInstance(__uuidof(MMDeviceEnumerator), NULL, CLSCTX_ALL,
                                  __uuidof(IMMDeviceEnumerator), (void**)&enumerator);

    if(FAILED(hr))
    {
        error = HrText("creating the audio device enumerator", hr);
        return false;
    }

    hr = enumerator->GetDefaultAudioEndpoint(eRender, eConsole, &device);
    enumerator->Release();

    if(FAILED(hr))
    {
        error = HrText("finding the default speakers", hr);
        Close();
        return false;
    }

    hr = device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, NULL, (void**)&client);

    if(SUCCEEDED(hr))
    {
        hr = client->GetMixFormat(&format);
    }

    if(FAILED(hr))
    {
        error = HrText("opening the speakers", hr);
        Close();
        return false;
    }

    channels = format->nChannels;
    rate     = format->nSamplesPerSec;
    bits     = format->wBitsPerSample;
    is_float = (format->wFormatTag == WAVE_FORMAT_IEEE_FLOAT);

    if(format->wFormatTag == WAVE_FORMAT_EXTENSIBLE && format->cbSize >= 22)
    {
        const WAVEFORMATEXTENSIBLE* ext = (const WAVEFORMATEXTENSIBLE*)format;

        if(memcmp(&ext->SubFormat, &SUBTYPE_IEEE_FLOAT, sizeof(GUID)) == 0)
        {
            is_float = true;
        }
        else if(memcmp(&ext->SubFormat, &SUBTYPE_PCM, sizeof(GUID)) != 0)
        {
            bits = 0;
        }
    }

    bool supported = channels > 0 && rate > 0
                  && ((is_float && bits == 32) || (!is_float && (bits == 16 || bits == 32)));

    if(!supported)
    {
        error = "unsupported speaker mix format";
        CoTaskMemFree(format);
        Close();
        return false;
    }

    /*-----------------------------------------------------*\
    | 200 ms of buffer; the reader drains it every ~5 ms    |
    \*-----------------------------------------------------*/
    hr = client->Initialize(AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK, 2000000, 0, format, NULL);
    CoTaskMemFree(format);

    if(SUCCEEDED(hr))
    {
        hr = client->GetService(__uuidof(IAudioCaptureClient), (void**)&capture);
    }

    if(SUCCEEDED(hr))
    {
        hr = client->Start();
    }

    if(FAILED(hr))
    {
        error = HrText("starting loopback capture", hr);
        Close();
        return false;
    }

    /*-----------------------------------------------------*\
    | 1024 frames at the 48 kHz the old recorder asked for  |
    \*-----------------------------------------------------*/
    block_frames = (unsigned int)std::lround(1024.0 * rate / 48000.0);

    if(block_frames == 0)
    {
        block_frames = 1024;
    }

    sum_squares = 0.0;
    frames      = 0;

    return true;
}

void AudioMeter::Close()
{
    if(client)
    {
        client->Stop();
    }

    SafeRelease(capture);
    SafeRelease(client);
    SafeRelease(device);
}

bool AudioMeter::Accumulate(const unsigned char* data, unsigned int count, bool silent)
{
    frames += count;

    if(silent || !data)
    {
        return true;
    }

    const size_t samples = (size_t)count * channels;
    double       sum     = 0.0;

    if(is_float)
    {
        const float* p = (const float*)data;

        for(size_t i = 0; i < samples; i++)
        {
            sum += (double)p[i] * (double)p[i];
        }
    }
    else if(bits == 16)
    {
        const int16_t* p = (const int16_t*)data;

        for(size_t i = 0; i < samples; i++)
        {
            double v = p[i] / 32768.0;
            sum += v * v;
        }
    }
    else
    {
        const int32_t* p = (const int32_t*)data;

        for(size_t i = 0; i < samples; i++)
        {
            double v = p[i] / 2147483648.0;
            sum += v * v;
        }
    }

    sum_squares += sum;
    return true;
}

bool AudioMeter::ReadBlock(double& rms, const std::function<bool()>& stop_requested)
{
    if(!capture)
    {
        return false;
    }

    const double block_seconds = (double)block_frames / (double)rate;
    const double start         = NowSeconds();

    while(true)
    {
        UINT32  packet = 0;
        HRESULT hr     = capture->GetNextPacketSize(&packet);

        while(SUCCEEDED(hr) && packet > 0)
        {
            BYTE*  data  = nullptr;
            UINT32 count = 0;
            DWORD  flags = 0;

            hr = capture->GetBuffer(&data, &count, &flags, NULL, NULL);

            if(FAILED(hr))
            {
                break;
            }

            Accumulate(data, count, (flags & AUDCLNT_BUFFERFLAGS_SILENT) != 0);
            capture->ReleaseBuffer(count);

            hr = capture->GetNextPacketSize(&packet);
        }

        if(FAILED(hr))
        {
            return false;
        }

        bool timed_out = (NowSeconds() - start) > 2.0 * block_seconds;

        if(frames >= block_frames || timed_out || stop_requested())
        {
            /*---------------------------------------------*\
            | A stream that stops mid-block, or never ran   |
            | (nothing playing), counts its missing frames  |
            | as silence                                    |
            \*---------------------------------------------*/
            unsigned long long counted = frames > block_frames ? frames : block_frames;

            rms         = std::sqrt(sum_squares / ((double)counted * channels));
            sum_squares = 0.0;
            frames      = 0;

            return true;
        }

        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
}

}
