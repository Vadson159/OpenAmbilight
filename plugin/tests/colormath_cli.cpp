/*---------------------------------------------------------*\
| colormath_cli.cpp                                         |
|                                                           |
|   Runs the plugin's frame analysis over cases written by  |
|   compare_colormath.py and prints the results, so the     |
|   script can hold them against Ambilight.pyw.             |
|                                                           |
|   Input (little endian):                                  |
|     "AMBI" u32 count, then per case:                      |
|     i32 width, i32 height, u8 bars, i32 threshold,        |
|     u8 edges, u8 algo, width*height*3 bytes RGB           |
|   Output, one line per case:                              |
|     raw r g b | top bot | sat r g b | gamma r g b | lum   |
|   then the smoothing / loop_rest probes.                  |
\*---------------------------------------------------------*/

#include <cstdio>
#include <cstring>
#include <vector>

#include "ColorMath.h"

using namespace ambi;

template<typename T>
static bool ReadValue(FILE* f, T& value)
{
    return fread(&value, sizeof(T), 1, f) == 1;
}

int main(int argc, char** argv)
{
    if(argc < 2)
    {
        fprintf(stderr, "usage: colormath_cli cases.bin\n");
        return 2;
    }

    FILE* f = fopen(argv[1], "rb");

    if(!f)
    {
        fprintf(stderr, "cannot open %s\n", argv[1]);
        return 2;
    }

    char     magic[4];
    uint32_t count = 0;

    if(fread(magic, 1, 4, f) != 4 || memcmp(magic, "AMBI", 4) != 0 || !ReadValue(f, count))
    {
        fprintf(stderr, "bad header\n");
        return 2;
    }

    FrameAnalyzer analyzer;

    for(uint32_t case_idx = 0; case_idx < count; case_idx++)
    {
        int32_t width, height, threshold;
        uint8_t bars, edges, algo;

        if(!ReadValue(f, width) || !ReadValue(f, height) || !ReadValue(f, bars)
        || !ReadValue(f, threshold) || !ReadValue(f, edges) || !ReadValue(f, algo))
        {
            fprintf(stderr, "truncated case %u\n", case_idx);
            return 2;
        }

        Frame frame;

        frame.width  = width;
        frame.height = height;
        frame.rgb.resize((size_t)width * height * 3);

        if(fread(frame.rgb.data(), 1, frame.rgb.size(), f) != frame.rgb.size())
        {
            fprintf(stderr, "truncated pixels %u\n", case_idx);
            return 2;
        }

        AnalysisOptions options;

        options.bars            = bars != 0;
        options.black_threshold = threshold;
        options.edges           = edges != 0;
        options.algo            = (Algo)algo;

        RGBd raw = analyzer.Analyze(frame, options);

        int top = 0, bot = height;

        if(options.bars)
        {
            TrimLetterbox(frame, threshold, BLACK_ROW_RATIO, top, bot);
        }

        RGBi sat   = ApplySaturation(raw, 1.5);
        RGBi gamma = ApplyGamma(raw, 1.8);

        printf("%.17g %.17g %.17g | %d %d | %d %d %d | %d %d %d | %.17g\n",
               raw.r, raw.g, raw.b, top, bot,
               sat.r, sat.g, sat.b, gamma.r, gamma.g, gamma.b, Luminance(raw));
    }

    fclose(f);

    /*-----------------------------------------------------*\
    | Smoothing and pacing probes                           |
    \*-----------------------------------------------------*/
    const double dts[] = { 0.0, 0.001, 1.0 / 60, 1.0 / 15, 0.05, 0.2, 1.0 };

    for(int smoothness = 0; smoothness <= 101; smoothness++)
    {
        printf("settle %d %.17g\n", smoothness, SmoothSettleMs(smoothness));

        for(double dt : dts)
        {
            printf("alpha %d %.17g %.17g\n", smoothness, dt, SmoothAlpha(smoothness, dt));
        }
    }

    const int    streaks[]  = { 0, 9, 10, 59, 60, 1000000 };
    const double intervals[] = { 1.0 / 60, 1.0 / 15, 1.0 / 240 };
    const double elapsed[]  = { 0.0, 0.002, 0.03, 1.0 };

    for(int streak : streaks)
    {
        for(double interval : intervals)
        {
            for(double e : elapsed)
            {
                for(int still = 0; still < 2; still++)
                {
                    double sleep, wait;

                    LoopRest(streak, interval, e, still != 0, sleep, wait);
                    printf("rest %d %.17g %.17g %d %.17g %.17g\n", streak, interval, e, still, sleep, wait);
                }
            }
        }
    }

    return 0;
}
