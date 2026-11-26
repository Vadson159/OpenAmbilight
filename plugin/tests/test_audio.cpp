/*---------------------------------------------------------*\
| test_audio.cpp                                            |
|                                                           |
|   AudioMeter against the real default speakers: opens the |
|   loopback stream, reads blocks for ~3 s and prints their |
|   loudness and pacing. Silence must come back as 0 within |
|   ~2 blocks (loopback delivers no packets at all then).   |
|   Touches no LEDs.                                        |
\*---------------------------------------------------------*/

#include <windows.h>
#include <objbase.h>

#include <algorithm>
#include <cstdio>

#include "AudioMeter.h"
#include "ScreenGrabber.h"

using namespace ambi;

int main()
{
    CoInitializeEx(NULL, COINIT_MULTITHREADED);

    AudioMeter  meter;
    std::string error;

    if(!meter.Open(error))
    {
        printf("open failed: %s\n", error.c_str());
        return 1;
    }

    int    blocks   = 0;
    int    loud     = 0;
    double longest  = 0.0;
    double peak     = 0.0;
    double start    = NowSeconds();
    double previous = start;

    while(NowSeconds() - start < 3.0)
    {
        double rms = 0.0;

        if(!meter.ReadBlock(rms, [] { return false; }))
        {
            printf("device error after %d blocks\n", blocks);
            return 1;
        }

        double now = NowSeconds();

        longest  = std::max(longest, now - previous);
        previous = now;
        peak     = std::max(peak, rms);
        loud    += (rms > 0.001);
        blocks++;
    }

    meter.Close();
    CoUninitialize();

    printf("%d blocks in 3 s (%.1f ms each on average, longest %.1f ms), %d with sound, peak RMS %.4f -> intensity %.2f\n",
           blocks, 3000.0 / blocks, longest * 1000.0, loud, peak, std::min(1.0, peak * 6.0));

    bool ok = blocks >= 40 && longest < 0.2;

    printf("%s\n", ok ? "ok" : "FAIL");
    return ok ? 0 : 1;
}
