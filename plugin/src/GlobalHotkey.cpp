/*---------------------------------------------------------*\
| GlobalHotkey.cpp                                          |
\*---------------------------------------------------------*/

#include "GlobalHotkey.h"

#include <windows.h>

#include <cctype>
#include <cstdlib>
#include <cstring>
#include <condition_variable>
#include <mutex>
#include <sstream>
#include <vector>

namespace ambi
{

struct NamedKey
{
    const char*  name;
    unsigned int vk;
};

static const NamedKey MODIFIERS[] =
{
    { "ctrl",    MOD_CONTROL },
    { "control", MOD_CONTROL },
    { "alt",     MOD_ALT     },
    { "shift",   MOD_SHIFT   },
    { "win",     MOD_WIN     },
    { "windows", MOD_WIN     },
    { "cmd",     MOD_WIN     },
};

static const NamedKey KEYS[] =
{
    { "space",        0x20 },
    { "enter",        0x0D },
    { "tab",          0x09 },
    { "esc",          0x1B },
    { "escape",       0x1B },
    { "backspace",    0x08 },
    { "insert",       0x2D },
    { "delete",       0x2E },
    { "home",         0x24 },
    { "end",          0x23 },
    { "page up",      0x21 },
    { "page down",    0x22 },
    { "up",           0x26 },
    { "down",         0x28 },
    { "left",         0x25 },
    { "right",        0x27 },
    { "pause",        0x13 },
    { "print screen", 0x2C },
    { "scroll lock",  0x91 },
};

static std::string Trim(const std::string& text)
{
    size_t begin = text.find_first_not_of(" \t");
    size_t end   = text.find_last_not_of(" \t");

    return (begin == std::string::npos) ? std::string() : text.substr(begin, end - begin + 1);
}

static bool FindModifier(const std::string& name, unsigned int& flag)
{
    for(const NamedKey& m : MODIFIERS)
    {
        if(name == m.name)
        {
            flag = m.vk;
            return true;
        }
    }

    return false;
}

bool GlobalHotkey::Parse(const std::string& combo, unsigned int& modifiers, unsigned int& vk, std::string& error)
{
    std::string lowered;

    for(char c : combo)
    {
        lowered.push_back((char)std::tolower((unsigned char)c));
    }

    modifiers = 0;
    vk        = 0;

    std::stringstream stream(lowered);
    std::string       part;

    while(std::getline(stream, part, '+'))
    {
        std::string  name = Trim(part);
        unsigned int flag = 0;

        for(const char* side : { "left ", "right " })
        {
            size_t len = strlen(side);

            if(name.compare(0, len, side) == 0 && FindModifier(name.substr(len), flag))
            {
                name = name.substr(len);
            }
        }

        if(FindModifier(name, flag))
        {
            modifiers |= flag;
            continue;
        }

        if(vk != 0)
        {
            error = "more than one non-modifier key in \"" + combo + "\"";
            return false;
        }

        if(name.size() == 1 && std::isalnum((unsigned char)name[0]))
        {
            vk = (unsigned int)std::toupper((unsigned char)name[0]);
            continue;
        }

        if(name.size() >= 2 && name.size() <= 3 && name[0] == 'f' && std::isdigit((unsigned char)name[1]))
        {
            int number = atoi(name.c_str() + 1);

            if(number >= 1 && number <= 24 && std::to_string(number) == name.substr(1))
            {
                vk = 0x6F + number;
                continue;
            }
        }

        bool found = false;

        for(const NamedKey& key : KEYS)
        {
            if(name == key.name)
            {
                vk    = key.vk;
                found = true;
                break;
            }
        }

        if(!found)
        {
            error = "no virtual key for \"" + name + "\"";
            return false;
        }
    }

    if(vk == 0)
    {
        error = "no key in \"" + combo + "\"";
        return false;
    }

    return true;
}

static std::string KeyName(unsigned int vk)
{
    if((vk >= 'A' && vk <= 'Z') || (vk >= '0' && vk <= '9'))
    {
        return std::string(1, (char)std::tolower((int)vk));
    }

    if(vk >= 0x70 && vk <= 0x87)
    {
        return "f" + std::to_string(vk - 0x6F);
    }

    for(const NamedKey& key : KEYS)
    {
        if(key.vk == vk && std::string(key.name) != "escape")
        {
            return key.name;
        }
    }

    return std::string();
}

std::string GlobalHotkey::FromVirtualKey(unsigned int modifiers, unsigned int vk)
{
    std::string name = KeyName(vk);

    if(name.empty())
    {
        return std::string();
    }

    std::string combo;

    if(modifiers & MOD_CONTROL) combo += "ctrl+";
    if(modifiers & MOD_ALT)     combo += "alt+";
    if(modifiers & MOD_SHIFT)   combo += "shift+";
    if(modifiers & MOD_WIN)     combo += "win+";

    return combo + name;
}

std::string GlobalHotkey::Describe(const std::string& combo)
{
    unsigned int modifiers, vk;
    std::string  error;

    if(combo.empty() || combo == "None" || !Parse(combo, modifiers, vk, error))
    {
        return "None";
    }

    std::string text;

    if(modifiers & MOD_CONTROL) text += "Ctrl+";
    if(modifiers & MOD_ALT)     text += "Alt+";
    if(modifiers & MOD_SHIFT)   text += "Shift+";
    if(modifiers & MOD_WIN)     text += "Win+";

    std::string name = KeyName(vk);
    bool        cap  = true;

    for(char c : name)
    {
        text.push_back(cap ? (char)std::toupper((unsigned char)c) : c);
        cap = (c == ' ');
    }

    return text;
}

GlobalHotkey::~GlobalHotkey()
{
    Stop();
}

bool GlobalHotkey::Start(const std::string& combo, std::function<void()> cb, std::string& error)
{
    Stop();

    unsigned int modifiers, vk;

    if(!Parse(combo, modifiers, vk, error))
    {
        return false;
    }

    callback = cb;

    std::mutex              mutex;
    std::condition_variable cv;
    bool                    ready = false;

    thread = std::thread([this, modifiers, vk, &mutex, &cv, &ready]()
    {
        MSG msg;

        /*-------------------------------------------------*\
        | Make sure the thread has a message queue before   |
        | anyone posts WM_QUIT to it                        |
        \*-------------------------------------------------*/
        PeekMessageW(&msg, NULL, WM_USER, WM_USER, PM_NOREMOVE);

        thread_id  = GetCurrentThreadId();
        registered = RegisterHotKey(NULL, 1, modifiers | MOD_NOREPEAT, vk) != 0;

        /*-------------------------------------------------*\
        | Notify with the lock held: Start() can only see   |
        | `ready` and destroy these stack objects once it   |
        | gets the lock, i.e. after notify_one() returned   |
        \*-------------------------------------------------*/
        {
            std::lock_guard<std::mutex> lock(mutex);
            ready = true;
            cv.notify_one();
        }

        if(!registered)
        {
            return;
        }

        while(GetMessageW(&msg, NULL, 0, 0) > 0)
        {
            if(msg.message == WM_HOTKEY && callback)
            {
                callback();
            }
        }

        UnregisterHotKey(NULL, 1);
    });

    {
        std::unique_lock<std::mutex> lock(mutex);
        cv.wait(lock, [&ready] { return ready; });
    }

    if(!registered)
    {
        thread.join();
        error = "\"" + combo + "\" is already used by another program";
        return false;
    }

    return true;
}

void GlobalHotkey::Stop()
{
    if(thread.joinable())
    {
        if(registered)
        {
            PostThreadMessageW(thread_id, WM_QUIT, 0, 0);
        }

        thread.join();
    }

    registered = false;
    thread_id  = 0;
}

}
