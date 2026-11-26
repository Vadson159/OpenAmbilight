/*---------------------------------------------------------*\
| AudioMeter.h                                              |
|                                                           |
|   Loudness of whatever the default speakers are playing,  |
|   for the music mode: WASAPI loopback capture, RMS over   |
|   blocks of 1024 frames at 48 kHz (21 ms), as the         |
|   soundcard recorder in Ambilight.pyw delivered them.     |
|                                                           |
|   Opened only while music mode is on, so no loopback      |
|   stream sits in the audio engine for nothing. Needs COM  |
|   initialised on the calling thread.                      |
\*---------------------------------------------------------*/

#pragma once

#include <functional>
#include <string>

struct IAudioCaptureClient;
struct IAudioClient;
struct IMMDevice;

namespace ambi
{

class AudioMeter
{
public:
    ~AudioMeter();

    bool        Open(std::string& error);
    void        Close();
    bool        IsOpen() const { return capture != nullptr; }

    /*-----------------------------------------------------*\
    | One block's RMS (0 when nothing plays: loopback then  |
    | delivers no packets at all). False on a device error  |
    | -- the default device changed or went away -- and the |
    | caller re-opens later.                                |
    \*-----------------------------------------------------*/
    bool        ReadBlock(double& rms, const std::function<bool()>& stop_requested);

private:
    bool        Accumulate(const unsigned char* data, unsigned int frames, bool silent);

    IMMDevice*              device      = nullptr;
    IAudioClient*           client      = nullptr;
    IAudioCaptureClient*    capture     = nullptr;

    unsigned int            channels    = 0;
    unsigned int            rate        = 0;
    unsigned int            bits        = 0;
    bool                    is_float    = false;
    unsigned int            block_frames = 1024;

    double                  sum_squares = 0.0;
    unsigned long long      frames      = 0;
};

}
