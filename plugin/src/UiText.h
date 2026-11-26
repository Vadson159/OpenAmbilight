/*---------------------------------------------------------*\
| UiText.h                                                  |
|                                                           |
|   The plugin speaks OpenRGB's language: Russian when      |
|   OpenRGB runs with its Russian translation, English      |
|   otherwise. OpenRGB's settings page names its language   |
|   by translating "English - US", so translating the same  |
|   string tells which translation OpenRGB installed.       |
\*---------------------------------------------------------*/

#pragma once

#include <QCoreApplication>
#include <QString>

namespace ambi
{

inline bool UiRussian()
{
    static int russian = -1;

    if(russian < 0)
    {
        QString probe = QCoreApplication::translate("OpenRGBSettingsPage", "English - US");

        russian = probe.startsWith(QString::fromUtf8("Рус")) ? 1 : 0;
    }

    return russian == 1;
}

inline QString Tr(const char* english, const char* russian)
{
    return QString::fromUtf8(UiRussian() ? russian : english);
}

}
