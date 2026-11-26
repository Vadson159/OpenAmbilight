/*---------------------------------------------------------*\
| GlobalHotkey.h                                            |
|                                                           |
|   A system-wide hotkey through RegisterHotKey, with no    |
|   keyboard hook: Windows matches the combination itself   |
|   and posts one message when it is pressed, so nothing    |
|   sits in front of every keystroke in a game.             |
|                                                           |
|   Combinations use the old config's spelling, e.g.        |
|   "ctrl+alt+a", "shift+f9", "win+page up".                |
\*---------------------------------------------------------*/

#pragma once

#include <functional>
#include <string>
#include <thread>

namespace ambi
{

class GlobalHotkey
{
public:
    ~GlobalHotkey();

    static bool         Parse(const std::string& combo, unsigned int& modifiers, unsigned int& vk, std::string& error);
    static std::string  Describe(const std::string& combo);
    static std::string  FromVirtualKey(unsigned int modifiers, unsigned int vk);

    bool                Start(const std::string& combo, std::function<void()> callback, std::string& error);
    void                Stop();
    bool                Active() const { return registered; }

private:
    void                Run(unsigned int modifiers, unsigned int vk);

    std::function<void()>   callback;
    std::thread             thread;
    unsigned long           thread_id   = 0;
    bool                    registered  = false;
};

}
