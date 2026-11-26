/*---------------------------------------------------------*\
| UiText.h                                                  |
|                                                           |
|   The plugin supports English and Russian. In Auto mode   |
|   it follows OpenRGB's active translation dynamically:    |
|   OpenRGB's settings page names its language by           |
|   translating "English - US", so probing that string      |
|   reflects live language switches too. Users can also     |
|   select English or Russian explicitly in the tab.        |
\*---------------------------------------------------------*/

#pragma once

#include <QCoreApplication>
#include <QString>

#include <cstddef>

#include "Settings.h"

namespace ambi
{

inline bool HostUiRussian()
{
    QString probe = QCoreApplication::translate("OpenRGBSettingsPage", "English - US");

    return probe.startsWith(QString::fromUtf8("Рус"));
}

inline bool UiRussian()
{
    switch(GetUiLanguage())
    {
        case UiLanguage::Russian: return true;
        case UiLanguage::English: return false;
        default:                  return HostUiRussian();
    }
}

inline QString Tr(const char* english, const char* russian)
{
    return QString::fromUtf8(UiRussian() ? russian : english);
}

inline QString FormatDeviceCount(size_t count)
{
    if(!UiRussian())
    {
        return QString("%1 device%2").arg(count).arg(count == 1 ? "" : "s");
    }

    size_t mod100 = count % 100;
    size_t mod10  = count % 10;

    if(mod100 >= 11 && mod100 <= 14)
    {
        return QString::fromUtf8("%1 устройств").arg(count);
    }

    if(mod10 == 1)
    {
        return QString::fromUtf8("%1 устройство").arg(count);
    }

    if(mod10 >= 2 && mod10 <= 4)
    {
        return QString::fromUtf8("%1 устройства").arg(count);
    }

    return QString::fromUtf8("%1 устройств").arg(count);
}

}
