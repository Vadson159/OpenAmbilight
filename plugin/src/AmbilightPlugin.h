/*---------------------------------------------------------*\
| AmbilightPlugin.h                                         |
|                                                           |
|   Ambilight as an OpenRGB plugin: an "Ambilight" tab, an  |
|   "Ambilight" submenu in OpenRGB's tray menu, the global  |
|   hotkey and the start/stop schedule. Devices are driven  |
|   in-process -- no SDK connection -- and only from the    |
|   OpenRGB instance that detected them (the elevated       |
|   server), never from a client window.                    |
\*---------------------------------------------------------*/

#pragma once

#include <QDate>
#include <QObject>
#include <QPointer>
#include <QString>

#include <atomic>
#include <memory>
#include <string>
#include <vector>

#include "OpenRGBPluginInterface.h"
#include "ResourceManagerInterface.h"

#include "AmbilightEngine.h"
#include "DeviceSender.h"
#include "Settings.h"

class QAction;
class QMenu;
class QTimer;
class RGBController;
class AmbilightTab;

namespace ambi
{
class DisplayPowerWatcher;
class GlobalHotkey;
}

class AmbilightPlugin : public QObject, public OpenRGBPluginInterface
{
    Q_OBJECT
    Q_PLUGIN_METADATA(IID OpenRGBPluginInterface_IID)
    Q_INTERFACES(OpenRGBPluginInterface)

public:
    AmbilightPlugin();
    ~AmbilightPlugin() override;

    /*-----------------------------------------------------*\
    | OpenRGBPluginInterface                                |
    \*-----------------------------------------------------*/
    OpenRGBPluginInfo   GetPluginInfo() override;
    unsigned int        GetPluginAPIVersion() override;
    void                Load(ResourceManagerInterface* resource_manager_ptr) override;
    QWidget*            GetWidget() override;
    QMenu*              GetTrayMenu() override;
    void                Unload() override;

    /*-----------------------------------------------------*\
    | Used by the tab                                       |
    \*-----------------------------------------------------*/
    ambi::SettingsStore&            Store() { return store; }
    void                            SettingsEdited();
    bool                            IsRunning() const;
    ambi::AmbilightEngine::Status   EngineStatus() const;
    std::vector<ambi::DeviceInfo>   Devices() const;
    QString                         Unavailable() const;
    QString                         HotkeyError() const { return hotkey_error; }
    void                            SuspendHotkey();
    void                            ResumeHotkey();

public slots:
    void                Toggle();
    void                StartAmbilight();
    void                StopAmbilight();
    void                ShowSettings();
    void                SetMusicMode(bool enabled);

private slots:
    void                OnScheduleTimer();
    void                OnDevicesChanged();
    void                OnHotkey();
    void                UpdateTrayMenu();
    void                SaveSettings();

private:
    struct CallbackScope;

    static void         DeviceListChangedCallback(void* self);
    static void         DetectionStartedCallback(void* self);
    static void         DetectionEndedCallback(void* self);

    std::vector<RGBController*> LocalControllers(bool& client) const;
    void                RefreshControllers();
    void                LoadSettings();
    void                BindHotkey();
    void                Shutdown();

    ResourceManagerInterface*                   rm              = nullptr;
    bool                                        loaded          = false;
    std::atomic<bool>                           detecting{ false };
    std::atomic<bool>                           accepting_callbacks{ false };
    std::atomic<int>                            callbacks_running{ 0 };
    std::atomic<bool>                           client_mode{ false };

    ambi::SettingsStore                         store;
    std::unique_ptr<ambi::DisplayPowerWatcher>  power;
    std::unique_ptr<ambi::DeviceSender>         sender;
    std::unique_ptr<ambi::AmbilightEngine>      engine;
    std::unique_ptr<ambi::GlobalHotkey>         hotkey;
    std::string                                 bound_hotkey;
    QString                                     hotkey_error;

    QTimer*                                     schedule_timer  = nullptr;
    QTimer*                                     save_timer      = nullptr;
    QPointer<AmbilightTab>                      tab;
    QMenu*                                      tray_menu       = nullptr;
    QAction*                                    tray_toggle     = nullptr;
    QAction*                                    tray_music      = nullptr;

    QDate                                       last_start_fire;
    QDate                                       last_stop_fire;
    QString                                     settings_path;
};
