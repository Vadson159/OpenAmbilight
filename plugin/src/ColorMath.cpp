/*---------------------------------------------------------*\
| ColorMath.cpp                                             |
|                                                           |
|   See ColorMath.h. Each function names the Python one it  |
|   mirrors; the arithmetic is done in the same order and   |
|   the same precision so results match bit for bit.        |
\*---------------------------------------------------------*/

#include "ColorMath.h"

#include <algorithm>
#include <cmath>

namespace ambi
{

/*---------------------------------------------------------*\
| trim_letterbox                                            |
|                                                           |
|   A row "carries picture" when more than (1 - row_ratio)  |
|   of its pixels are brighter than `threshold` in their    |
|   brightest channel. Keep the span from the first such    |
|   row to the last, unless that span is under 30% of the   |
|   frame (then it is a dark scene, not a letterbox).       |
\*---------------------------------------------------------*/
void TrimLetterbox(const Frame& frame, int threshold, double row_ratio, int& top, int& bot)
{
    const int    height = frame.height;
    const int    width  = frame.width;
    const double limit  = 1.0 - row_ratio;

    top = 0;
    bot = height;

    if(width <= 0 || height <= 0)
    {
        return;
    }

    int first = -1;
    int last  = -1;

    for(int y = 0; y < height; y++)
    {
        const uint8_t* p     = frame.rgb.data() + (size_t)y * width * 3;
        int            count = 0;

        for(int x = 0; x < width; x++, p += 3)
        {
            uint8_t brightest = std::max(std::max(p[0], p[1]), p[2]);

            if(brightest > threshold)
            {
                count++;
            }
        }

        if((double)count / (double)width > limit)
        {
            if(first < 0)
            {
                first = y;
            }
            last = y;
        }
    }

    if(first < 0)
    {
        return;
    }

    int span_top = first;
    int span_bot = last + 1;

    if((double)(span_bot - span_top) < height * 0.3)
    {
        return;
    }

    top = span_top;
    bot = span_bot;
}

static void AppendBlock(const Frame& frame, int x0, int y0, int x1, int y1, std::vector<uint8_t>& out)
{
    if(x1 <= x0 || y1 <= y0)
    {
        return;
    }

    const size_t row_bytes = (size_t)(x1 - x0) * 3;

    for(int y = y0; y < y1; y++)
    {
        const uint8_t* src = frame.rgb.data() + ((size_t)y * frame.width + x0) * 3;
        out.insert(out.end(), src, src + row_bytes);
    }
}

/*---------------------------------------------------------*\
| extract_edges                                             |
|                                                           |
|   numpy: concatenate(frame[:bh], frame[-bh:],             |
|                      frame[bh:-bh, :bw], frame[bh:-bh, -bw:]) |
\*---------------------------------------------------------*/
void ExtractEdges(const Frame& frame, int top, int bot, double ratio, std::vector<uint8_t>& out)
{
    out.clear();

    const int height = bot - top;
    const int width  = frame.width;

    if(height <= 0 || width <= 0)
    {
        return;
    }

    const int bh = std::max(1, PyInt(height * ratio));
    const int bw = std::max(1, PyInt(width  * ratio));

    /*-----------------------------------------------------*\
    | frame[:bh] and frame[-bh:]                            |
    \*-----------------------------------------------------*/
    AppendBlock(frame, 0, top, width, top + std::min(bh, height), out);
    AppendBlock(frame, 0, top + std::max(0, height - bh), width, bot, out);

    /*-----------------------------------------------------*\
    | frame[bh:-bh, :bw] and frame[bh:-bh, -bw:]            |
    \*-----------------------------------------------------*/
    const int mid_top = top + bh;
    const int mid_bot = top + height - bh;

    AppendBlock(frame, 0, mid_top, std::min(bw, width), mid_bot, out);
    AppendBlock(frame, std::max(0, width - bw), mid_top, width, mid_bot, out);
}

void CopyRegion(const Frame& frame, int x0, int y0, int x1, int y1, std::vector<uint8_t>& out)
{
    out.clear();
    AppendBlock(frame, x0, y0, x1, y1, out);
}

/*---------------------------------------------------------*\
| get_dominant_color                                        |
|                                                           |
|   Score each pixel by chroma x value, average the top 10% |
|   (everything that ties the 10% threshold included), push |
|   the result away from grey by 2.5x and lift a dim result |
|   so its brightest channel reaches 100.                   |
\*---------------------------------------------------------*/
RGBi DominantColor(const uint8_t* pixels, size_t count, std::vector<uint16_t>& scratch)
{
    RGBi result;

    if(count == 0)
    {
        return result;
    }

    scratch.resize(count * 2);

    uint16_t* score     = scratch.data();
    uint16_t* partition = scratch.data() + count;

    const uint8_t* p = pixels;

    for(size_t i = 0; i < count; i++, p += 3)
    {
        uint8_t mx = std::max(std::max(p[0], p[1]), p[2]);
        uint8_t mn = std::min(std::min(p[0], p[1]), p[2]);

        score[i] = (uint16_t)((unsigned)(uint8_t)(mx - mn) * (unsigned)mx);
    }

    /*-----------------------------------------------------*\
    | np.partition(score, -k)[-k]: the k-th largest score   |
    \*-----------------------------------------------------*/
    const size_t k = std::max<size_t>(1, count / 10);

    std::copy(score, score + count, partition);
    std::nth_element(partition, partition + (count - k), partition + count);

    const uint16_t threshold = partition[count - k];

    int64_t top_r = 0, top_g = 0, top_b = 0;
    size_t  top_n = 0;

    p = pixels;

    for(size_t i = 0; i < count; i++, p += 3)
    {
        if(score[i] >= threshold)
        {
            top_r += p[0];
            top_g += p[1];
            top_b += p[2];
            top_n++;
        }
    }

    int64_t sr, sg, sb;
    size_t  n;

    if(10 < top_n && top_n < count)
    {
        sr = top_r;
        sg = top_g;
        sb = top_b;
        n  = top_n;
    }
    else
    {
        /*-------------------------------------------------*\
        | Too few vivid pixels to trust, or every pixel     |
        | tied: average them all                            |
        \*-------------------------------------------------*/
        sr = sg = sb = 0;
        p  = pixels;

        for(size_t i = 0; i < count; i++, p += 3)
        {
            sr += p[0];
            sg += p[1];
            sb += p[2];
        }

        n = count;
    }

    int dr = PyInt((double)sr / (double)n);
    int dg = PyInt((double)sg / (double)n);
    int db = PyInt((double)sb / (double)n);

    double avg = (double)(dr + dg + db) / 3.0;

    dr = std::min(255, std::max(0, PyInt(avg + ((double)dr - avg) * 2.5)));
    dg = std::min(255, std::max(0, PyInt(avg + ((double)dg - avg) * 2.5)));
    db = std::min(255, std::max(0, PyInt(avg + ((double)db - avg) * 2.5)));

    int mx_val = std::max(std::max(dr, dg), db);

    if(0 < mx_val && mx_val < 100)
    {
        double s = 100.0 / (double)mx_val;

        dr = std::min(255, PyInt((double)dr * s));
        dg = std::min(255, PyInt((double)dg * s));
        db = std::min(255, PyInt((double)db * s));
    }

    result.r = dr;
    result.g = dg;
    result.b = db;

    return result;
}

RGBd AverageColor(const uint8_t* pixels, size_t count)
{
    RGBd result;

    if(count == 0)
    {
        return result;
    }

    int64_t        sr = 0, sg = 0, sb = 0;
    const uint8_t* p  = pixels;

    for(size_t i = 0; i < count; i++, p += 3)
    {
        sr += p[0];
        sg += p[1];
        sb += p[2];
    }

    result.r = (double)sr / (double)count;
    result.g = (double)sg / (double)count;
    result.b = (double)sb / (double)count;

    return result;
}

static RGBd FromInt(const RGBi& c)
{
    RGBd result;

    result.r = c.r;
    result.g = c.g;
    result.b = c.b;

    return result;
}

RGBd FrameAnalyzer::Analyze(const Frame& frame, const AnalysisOptions& options)
{
    int top = 0;
    int bot = frame.height;

    if(frame.empty())
    {
        return RGBd();
    }

    if(options.bars)
    {
        TrimLetterbox(frame, options.black_threshold, BLACK_ROW_RATIO, top, bot);
    }

    const uint8_t* pixels;
    size_t         count;
    bool           two_d;

    if(options.edges)
    {
        ExtractEdges(frame, top, bot, EDGE_RATIO, flat);

        pixels = flat.data();
        count  = flat.size() / 3;
        two_d  = false;
    }
    else
    {
        pixels = frame.rgb.data() + (size_t)top * frame.width * 3;
        count  = (size_t)(bot - top) * frame.width;
        two_d  = true;
    }

    switch(options.algo)
    {
        case Algo::Average:
            return AverageColor(pixels, count);

        case Algo::CenterFocus:
            if(two_d)
            {
                const int ch = bot - top;
                const int cw = frame.width;
                const int y0 = ch / 4;
                const int y1 = 3 * ch / 4;
                const int x0 = cw / 4;
                const int x1 = 3 * cw / 4;

                if(y1 > y0 && x1 > x0)
                {
                    CopyRegion(frame, x0, top + y0, x1, top + y1, flat);
                    return FromInt(DominantColor(flat.data(), flat.size() / 3, scores));
                }
            }
            return FromInt(DominantColor(pixels, count, scores));

        case Algo::Dominant:
        default:
            return FromInt(DominantColor(pixels, count, scores));
    }
}

/*---------------------------------------------------------*\
| Python's float % 1.0 (result takes the divisor's sign)    |
\*---------------------------------------------------------*/
static double PyMod1(double x)
{
    double mod = std::fmod(x, 1.0);

    if(mod != 0.0)
    {
        if(mod < 0.0)
        {
            mod += 1.0;
        }
    }
    else
    {
        mod = std::copysign(0.0, 1.0);
    }

    return mod;
}

void RgbToHsv(double r, double g, double b, double& h, double& s, double& v)
{
    double maxc   = std::max(std::max(r, g), b);
    double minc   = std::min(std::min(r, g), b);
    double rangec = maxc - minc;

    v = maxc;

    if(minc == maxc)
    {
        h = 0.0;
        s = 0.0;
        return;
    }

    s = rangec / maxc;

    double rc = (maxc - r) / rangec;
    double gc = (maxc - g) / rangec;
    double bc = (maxc - b) / rangec;

    if(r == maxc)
    {
        h = bc - gc;
    }
    else if(g == maxc)
    {
        h = 2.0 + rc - bc;
    }
    else
    {
        h = 4.0 + gc - rc;
    }

    h = PyMod1(h / 6.0);
}

void HsvToRgb(double h, double s, double v, double& r, double& g, double& b)
{
    if(s == 0.0)
    {
        r = g = b = v;
        return;
    }

    int    i = PyInt(h * 6.0);
    double f = (h * 6.0) - (double)i;
    double p = v * (1.0 - s);
    double q = v * (1.0 - s * f);
    double t = v * (1.0 - s * (1.0 - f));

    i = ((i % 6) + 6) % 6;

    switch(i)
    {
        case 0:  r = v; g = t; b = p; break;
        case 1:  r = q; g = v; b = p; break;
        case 2:  r = p; g = v; b = t; break;
        case 3:  r = p; g = q; b = v; break;
        case 4:  r = t; g = p; b = v; break;
        default: r = v; g = p; b = q; break;
    }
}

RGBi ApplySaturation(const RGBd& color, double boost)
{
    double h, s, v;

    RgbToHsv(color.r / 255.0, color.g / 255.0, color.b / 255.0, h, s, v);

    s = std::min(1.0, s * boost);

    double rn, gn, bn;

    HsvToRgb(h, s, v, rn, gn, bn);

    RGBi result;

    result.r = PyInt(rn * 255);
    result.g = PyInt(gn * 255);
    result.b = PyInt(bn * 255);

    return result;
}

RGBi ApplyGamma(const RGBd& color, double gamma)
{
    double h, s, v;

    RgbToHsv(color.r / 255.0, color.g / 255.0, color.b / 255.0, h, s, v);

    v = std::pow(v, gamma);

    double rn, gn, bn;

    HsvToRgb(h, s, v, rn, gn, bn);

    RGBi result;

    result.r = PyInt(rn * 255);
    result.g = PyInt(gn * 255);
    result.b = PyInt(bn * 255);

    return result;
}

double Luminance(const RGBd& color)
{
    return 0.299 * color.r + 0.587 * color.g + 0.114 * color.b;
}

int ColorDiff(const RGBi& a, const RGBi& b)
{
    return std::abs(a.r - b.r) + std::abs(a.g - b.g) + std::abs(a.b - b.b);
}

double SmoothAlpha(int smoothness, double dt)
{
    double s = std::max(1, std::min(100, smoothness)) / 100.0;

    if(s >= 1.0 || dt <= 0.0)
    {
        return 1.0;
    }

    return 1.0 - std::pow(1.0 - s, dt * SMOOTH_REF_FPS);
}

double SmoothSettleMs(int smoothness)
{
    double s = std::max(1, std::min(100, smoothness)) / 100.0;

    if(s >= 1.0)
    {
        return 0.0;
    }

    return 1000.0 * std::log(0.1) / (SMOOTH_REF_FPS * std::log(1.0 - s));
}

void LoopRest(int idle_streak, double interval, double elapsed, bool still, double& sleep, double& wait)
{
    double rest;

    if(idle_streak >= DEEP_IDLE_LIMIT)
    {
        rest = DEEP_IDLE_INTERVAL;
    }
    else if(idle_streak >= IDLE_STREAK_LIMIT)
    {
        rest = interval * IDLE_MULTIPLIER - elapsed;
    }
    else
    {
        sleep = std::max(0.0, interval - elapsed);
        wait  = 0.0;
        return;
    }

    if(still)
    {
        sleep = 0.0;
        wait  = std::max(0.0, rest);
    }
    else
    {
        sleep = std::max(0.0, rest);
        wait  = 0.0;
    }
}

}
