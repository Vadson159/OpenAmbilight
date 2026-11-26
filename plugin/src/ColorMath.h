/*---------------------------------------------------------*\
| ColorMath.h                                               |
|                                                           |
|   Frame analysis and colour maths for the Ambilight       |
|   plugin. A line-for-line port of the numpy code in       |
|   Ambilight.pyw: every function here returns the same     |
|   numbers as its Python original (tests/compare_colormath |
|   checks that on thousands of frames).                    |
|                                                           |
|   Nothing in this file touches Windows, Qt or OpenRGB.    |
\*---------------------------------------------------------*/

#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace ambi
{

/*---------------------------------------------------------*\
| Tuning constants, same names and values as Ambilight.pyw  |
\*---------------------------------------------------------*/
constexpr int    DOWNSAMPLE          = 20;      /* capture keeps 1 pixel in 20 per axis */
constexpr int    IDLE_STREAK_LIMIT   = 10;      /* frames until "idle" mode             */
constexpr double IDLE_MULTIPLIER     = 3.0;     /* sleep multiplier while idle          */
constexpr int    DEEP_IDLE_LIMIT     = 60;      /* frames until "deep idle"             */
constexpr double DEEP_IDLE_INTERVAL  = 0.05;    /* deep-idle poll period, seconds       */
constexpr double EDGE_RATIO          = 0.15;
constexpr int    BLACK_THRESHOLD     = 20;
constexpr double BLACK_ROW_RATIO     = 0.90;
constexpr double SMOOTH_REF_FPS      = 15.0;

struct RGBi
{
    int r = 0;
    int g = 0;
    int b = 0;
};

inline bool operator==(const RGBi& a, const RGBi& b) { return a.r == b.r && a.g == b.g && a.b == b.b; }
inline bool operator!=(const RGBi& a, const RGBi& b) { return !(a == b); }

/*---------------------------------------------------------*\
| A colour that may be fractional ("Average" yields floats) |
\*---------------------------------------------------------*/
struct RGBd
{
    double r = 0.0;
    double g = 0.0;
    double b = 0.0;
};

/*---------------------------------------------------------*\
| A downsampled screen frame: RGB, 3 bytes per pixel, rows  |
| packed with no padding (the numpy array is C-contiguous)  |
\*---------------------------------------------------------*/
struct Frame
{
    int                  width  = 0;
    int                  height = 0;
    std::vector<uint8_t> rgb;

    bool empty() const { return width <= 0 || height <= 0; }
    bool same_pixels(const Frame& other) const
    {
        return width == other.width && height == other.height && rgb == other.rgb;
    }
};

enum class Algo
{
    Dominant    = 0,
    Average     = 1,
    CenterFocus = 2,
};

struct AnalysisOptions
{
    bool bars               = false;            /* "Skip Black Bars"  */
    int  black_threshold    = BLACK_THRESHOLD;
    bool edges              = false;            /* "Edge Only"        */
    Algo algo               = Algo::Dominant;

    bool operator==(const AnalysisOptions& o) const
    {
        return bars == o.bars && black_threshold == o.black_threshold && edges == o.edges && algo == o.algo;
    }
    bool operator!=(const AnalysisOptions& o) const { return !(*this == o); }
};

/*---------------------------------------------------------*\
| trim_letterbox(): rows [top, bot) that carry the picture  |
| (the whole frame if no letterbox is found)                |
\*---------------------------------------------------------*/
void TrimLetterbox(const Frame& frame, int threshold, double row_ratio, int& top, int& bot);

/*---------------------------------------------------------*\
| extract_edges() on rows [top, bot): the outer ring of the |
| picture, as a flat list of RGB triples in numpy's order   |
\*---------------------------------------------------------*/
void ExtractEdges(const Frame& frame, int top, int bot, double ratio, std::vector<uint8_t>& out);

/*---------------------------------------------------------*\
| A rectangle of the frame as a flat list of RGB triples    |
\*---------------------------------------------------------*/
void CopyRegion(const Frame& frame, int x0, int y0, int x1, int y1, std::vector<uint8_t>& out);

/*---------------------------------------------------------*\
| get_dominant_color() over `count` RGB triples             |
\*---------------------------------------------------------*/
RGBi DominantColor(const uint8_t* pixels, size_t count, std::vector<uint16_t>& scratch);

/*---------------------------------------------------------*\
| px.reshape(-1, 3).mean(axis=0)                            |
\*---------------------------------------------------------*/
RGBd AverageColor(const uint8_t* pixels, size_t count);

/*---------------------------------------------------------*\
| The analysis step of the capture loop: letterbox, edges,  |
| then the selected algorithm. Returns the "raw" colour.    |
\*---------------------------------------------------------*/
class FrameAnalyzer
{
public:
    RGBd Analyze(const Frame& frame, const AnalysisOptions& options);

private:
    std::vector<uint8_t>  flat;
    std::vector<uint16_t> scores;
};

/*---------------------------------------------------------*\
| colorsys.rgb_to_hsv / hsv_to_rgb, exactly as CPython      |
\*---------------------------------------------------------*/
void RgbToHsv(double r, double g, double b, double& h, double& s, double& v);
void HsvToRgb(double h, double s, double v, double& r, double& g, double& b);

RGBi   ApplySaturation(const RGBd& color, double boost = 1.5);
RGBi   ApplyGamma(const RGBd& color, double gamma = 1.8);
double Luminance(const RGBd& color);
int    ColorDiff(const RGBi& a, const RGBi& b);

/*---------------------------------------------------------*\
| Smoothing: per-frame EMA factor for a setting of 1-100    |
| over a `dt`-second step, and the time it takes to cover   |
| 90% of a step                                             |
\*---------------------------------------------------------*/
double SmoothAlpha(int smoothness, double dt);
double SmoothSettleMs(int smoothness);

/*---------------------------------------------------------*\
| loop_rest(): how the capture loop spends the time until   |
| its next frame. `sleep` is slept through, `wait` is given |
| to the grabber, which returns as soon as a frame arrives  |
\*---------------------------------------------------------*/
void LoopRest(int idle_streak, double interval, double elapsed, bool still, double& sleep, double& wait);

/*---------------------------------------------------------*\
| Python's int(x) for the values used here (truncation)     |
\*---------------------------------------------------------*/
inline int PyInt(double x) { return (int)x; }

}
