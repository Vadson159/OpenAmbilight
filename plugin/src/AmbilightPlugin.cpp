/*---------------------------------------------------------*\
| AmbilightPlugin.cpp                                       |
|                                                           |
|   Lifetime notes (OpenRGB's PluginManager):               |
|   * The plugin object is created twice: once to read its  |
|     info (then destroyed and the DLL unloaded), once for  |
|     real. The constructor does nothing.                   |
|   * Unload() runs before OpenRGB removes the tab and the  |
|     tray menu, and the DLL is unloaded right after.       |
|     OpenRGB only removes the tab page, it never deletes   |
|     it, so Unload() deletes the tab widget itself --      |
|     its code lives in this DLL. The tray menu is deleted  |
|     in the destructor, after OpenRGB has detached it.     |
|   * Controllers are deleted on every rescan. The          |
|     detection-start callback runs before that, on the     |
|     thread doing it, and empties the device list          |
|     synchronously (see DeviceSender).                     |
\*---------------------------------------------------------*/

#include "AmbilightPlugin.h"

#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QIcon>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMenu>
#include <QPainter>
#include <QPixmap>
#include <QSaveFile>
#include <QTabWidget>
#include <QTime>
#include <QTimer>

#include <windows.h>

#include <cmath>

#include "RGBController.h"

#include "AmbilightTab.h"
#include "DisplayPower.h"
#include "GlobalHotkey.h"
#include "Log.h"
#include "Monitors.h"
#include "UiText.h"

using namespace ambi;

static const char PLUGIN_VERSION[] = "1.0.0";

/*---------------------------------------------------------*\
| The old app's icon: a dark disc with a pink centre        |
\*---------------------------------------------------------*/
static QImage MakeIcon(int size)
{
    QImage image(size, size, QImage::Format_ARGB32_Premultiplied);

    image.fill(Qt::transparent);

    QPainter painter(&image);
    double   scale = size / 64.0;

    painter.setRenderHint(QPainter::Antialiasing);
    painter.setPen(Qt::NoPen);
    painter.setBrush(QColor(26, 26, 46));
    painter.drawEllipse(QRectF(3 * scale, 3 * scale, 58 * scale, 58 * scale));
    painter.setBrush(QColor(233, 69, 96));
    painter.drawEllipse(QRectF(14 * scale, 14 * scale, 36 * scale, 36 * scale));

    return image;
}

static bool IsElevated()
{
    HANDLE          token;
    TOKEN_ELEVATION elevation = { 0 };
    DWORD           size      = 0;

    if(!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token))
    {
        return false;
    }

    GetTokenInformation(token, TokenElevation, &elevation, sizeof(elevation), &size);
    CloseHandle(token);

    return elevation.TokenIsElevated != 0;
}

/*---------------------------------------------------------*\
| Settings file. The keys are the old config.json's, so     |
| the same reader imports it: numbers may be strings there  |
| ("fps": "60", "schedule_start_h": "18").                  |
\*---------------------------------------------------------*/
static int JsonInt(const QJsonObject& o, const char* key, int fallback)
{
    QJsonValue v = o.value(key);

    if(v.isDouble())
    {
        return (int)std::lround(v.toDouble());
    }

    if(v.isString())
    {
        bool   ok    = false;
        double value = v.toString().trimmed().toDouble(&ok);

        if(ok)
        {
            return (int)std::lround(value);
        }
    }

    if(v.isBool())
    {
        return v.toBool() ? 1 : 0;
    }

    return fallback;
}

static bool JsonBool(const QJsonObject& o, const char* key, bool fallback)
{
    QJsonValue v = o.value(key);

    if(v.isBool())
    {
        return v.toBool();
    }

    if(v.isDouble())
    {
        return v.toDouble() != 0.0;
    }

    if(v.isString())
    {
        QString text = v.toString().trimmed().toLower();
        return text == "true" || text == "1" || text == "yes";
    }

    return fallback;
}

static std::string JsonString(const QJsonObject& o, const char* key, const std::string& fallback)
{
    QJsonValue v = o.value(key);

    return v.isString() ? v.toString().toStdString() : fallback;
}

static void FromJson(const QJsonObject& o, AmbilightSettings& s)
{
    AmbilightSettings d;

    s.language               = LanguageFromCode(JsonString(o, "language", LanguageCode(d.language)));
    s.monitor_device         = FromUtf8(JsonString(o, "monitor_device", ""));
    s.monitor_index          = JsonInt(o, "monitor", d.monitor_index);
    s.fps                    = JsonInt(o, "fps", d.fps);
    s.algo                   = AlgoFromName(JsonString(o, "algo", AlgoName(d.algo)));
    s.edges                  = JsonBool(o, "edges", d.edges);
    s.bars                   = JsonBool(o, "bars", d.bars);
    s.black_threshold        = JsonInt(o, "black_threshold", d.black_threshold);
    s.brightness             = JsonInt(o, "brightness", d.brightness);
    s.min_brightness         = JsonInt(o, "min_brightness", d.min_brightness);
    s.smoothness             = JsonInt(o, "smoothness", d.smoothness);
    s.saturation             = JsonBool(o, "saturation", d.saturation);
    s.gamma                  = JsonBool(o, "gamma", d.gamma);
    s.hdr                    = JsonBool(o, "hdr", d.hdr);
    s.sync_devices           = JsonBool(o, "sync_devices", d.sync_devices);
    s.display_sync           = JsonBool(o, "display_sync", d.display_sync);
    s.audio                  = JsonBool(o, "audio", d.audio);
    s.hotkey                 = JsonString(o, "hotkey", d.hotkey);
    s.start_on_launch        = JsonBool(o, "start_on_launch", d.start_on_launch);
    s.schedule_start_enabled = JsonBool(o, "schedule_start_enabled", d.schedule_start_enabled);
    s.schedule_start_h       = JsonInt(o, "schedule_start_h", d.schedule_start_h);
    s.schedule_start_m       = JsonInt(o, "schedule_start_m", d.schedule_start_m);
    s.schedule_stop_enabled  = JsonBool(o, "schedule_stop_enabled", d.schedule_stop_enabled);
    s.schedule_stop_h        = JsonInt(o, "schedule_stop_h", d.schedule_stop_h);
    s.schedule_stop_m        = JsonInt(o, "schedule_stop_m", d.schedule_stop_m);

    s.Clamp();
}

static QJsonObject ToJson(const AmbilightSettings& s)
{
    QJsonObject o;

    o["language"]               = LanguageCode(s.language);
    o["monitor_device"]         = QString::fromStdWString(s.monitor_device);
    o["monitor"]                = s.monitor_index;
    o["fps"]                    = s.fps;
    o["algo"]                   = AlgoName(s.algo);
    o["edges"]                  = s.edges;
    o["bars"]                   = s.bars;
    o["black_threshold"]        = s.black_threshold;
    o["brightness"]             = s.brightness;
    o["min_brightness"]         = s.min_brightness;
    o["smoothness"]             = s.smoothness;
    o["saturation"]             = s.saturation;
    o["gamma"]                  = s.gamma;
    o["hdr"]                    = s.hdr;
    o["sync_devices"]           = s.sync_devices;
    o["display_sync"]           = s.display_sync;
    o["audio"]                  = s.audio;
    o["hotkey"]                 = QString::fromStdString(s.hotkey);
    o["start_on_launch"]        = s.start_on_launch;
    o["schedule_start_enabled"] = s.schedule_start_enabled;
    o["schedule_start_h"]       = s.schedule_start_h;
    o["schedule_start_m"]       = s.schedule_start_m;
    o["schedule_stop_enabled"]  = s.schedule_stop_enabled;
    o["schedule_stop_h"]        = s.schedule_stop_h;
    o["schedule_stop_m"]        = s.schedule_stop_m;

    return o;
}

static bool ReadJsonFile(const QString& path, QJsonObject& out)
{
    QFile file(path);

    if(!file.open(QIODevice::ReadOnly))
    {
        return false;
    }

    QJsonParseError error;
    QJsonDocument   doc = QJsonDocument::fromJson(file.readAll(), &error);

    if(error.error != QJsonParseError::NoError || !doc.isObject())
    {
        return false;
    }

    out = doc.object();
    return true;
}

/*---------------------------------------------------------*\
| Plugin                                                    |
\*---------------------------------------------------------*/
AmbilightPlugin::AmbilightPlugin()
{
}

AmbilightPlugin::~AmbilightPlugin()
{
    Shutdown();
    delete tray_menu;
}

OpenRGBPluginInfo AmbilightPlugin::GetPluginInfo()
{
    OpenRGBPluginInfo info;

    info.Name           = "Ambilight";
    info.Description    = "Screen-synced ambient lighting on every OpenRGB device";
    info.Version        = PLUGIN_VERSION;
    info.Commit         = "";
    info.URL            = "";
    info.Icon           = MakeIcon(64);
    info.Location       = OPENRGB_PLUGIN_LOCATION_TOP;
    info.Label          = "Ambilight";
    info.TabIconString  = "";
    info.TabIcon        = MakeIcon(16);

    return info;
}

unsigned int AmbilightPlugin::GetPluginAPIVersion()
{
    return OPENRGB_PLUGIN_API_VERSION;
}

void AmbilightPlugin::Load(ResourceManagerInterface* resource_manager_ptr)
{
    if(loaded)
    {
        return;
    }

    rm = resource_manager_ptr;

    QString config_dir = QString::fromStdWString(rm->GetConfigurationDirectory().wstring());

    QDir().mkpath(config_dir + "/logs");
    QDir().mkpath(config_dir + "/plugins/settings");

    LogOpen((config_dir + "/logs/Ambilight.log").toStdWString());
    Log("---- Ambilight plugin %s loaded into OpenRGB (pid %lu, %s)", PLUGIN_VERSION,
        (unsigned long)GetCurrentProcessId(), IsElevated() ? "elevated" : "NOT elevated");

    settings_path = config_dir + "/plugins/settings/Ambilight.json";
    LoadSettings();

    power.reset(new DisplayPowerWatcher());
    power->Start();

    sender.reset(new DeviceSender());
    engine.reset(new AmbilightEngine(&store, sender.get(), power.get()));

    accepting_callbacks = true;

    rm->RegisterDeviceListChangeCallback(DeviceListChangedCallback, this);
    rm->RegisterDetectionStartCallback(DetectionStartedCallback, this);
    rm->RegisterDetectionEndCallback(DetectionEndedCallback, this);

    RefreshControllers();

    for(const DeviceInfo& info : sender->Devices())
    {
        Log("Device: %s (latency %.1f ms, busy %.1f ms)", info.name.c_str(), info.latency_ms, info.busy_ms);
    }

    schedule_timer = new QTimer(this);
    schedule_timer->setInterval(30000);
    connect(schedule_timer, &QTimer::timeout, this, &AmbilightPlugin::OnScheduleTimer);
    schedule_timer->start();

    save_timer = new QTimer(this);
    save_timer->setSingleShot(true);
    save_timer->setInterval(400);
    connect(save_timer, &QTimer::timeout, this, &AmbilightPlugin::SaveSettings);

    connect(qApp, &QCoreApplication::aboutToQuit, this, [this]() { Shutdown(); });

    loaded = true;

    if(client_mode)
    {
        hotkey_error_kind = HotkeyErrorKind::ClientInstance;
        hotkey_error_arg.clear();
        Log("This OpenRGB is a client of another instance; Ambilight stays off here");
    }
    else
    {
        BindHotkey();

        if(store.Get().start_on_launch)
        {
            QTimer::singleShot(500, this, &AmbilightPlugin::StartAmbilight);
        }
    }

    QTimer::singleShot(5000, this, &AmbilightPlugin::OnScheduleTimer);
}

QWidget* AmbilightPlugin::GetWidget()
{
    if(!tab)
    {
        tab = new AmbilightTab(this);
    }

    return tab;
}

QMenu* AmbilightPlugin::GetTrayMenu()
{
    if(!tray_menu)
    {
        tray_menu = new QMenu("Ambilight");
        tray_menu->setIcon(QIcon(QPixmap::fromImage(MakeIcon(32))));

        tray_toggle = tray_menu->addAction(Tr("Start Ambilight", "Включить Ambilight"));
        connect(tray_toggle, &QAction::triggered, this, &AmbilightPlugin::Toggle);

        tray_music = tray_menu->addAction(Tr("Music mode", "Музыкальный режим"));
        tray_music->setCheckable(true);
        connect(tray_music, &QAction::triggered, this, &AmbilightPlugin::SetMusicMode);

        tray_menu->addSeparator();

        tray_settings = tray_menu->addAction(Tr("Ambilight settings…", "Настройки Ambilight…"));
        connect(tray_settings, &QAction::triggered, this, &AmbilightPlugin::ShowSettings);

        connect(tray_menu, &QMenu::aboutToShow, this, &AmbilightPlugin::UpdateTrayMenu);
        UpdateTrayMenu();
    }

    return tray_menu;
}

void AmbilightPlugin::Unload()
{
    Shutdown();

    if(tab)
    {
        tab->Detach();
        delete tab.data();
    }

    SetUiLanguage(UiLanguage::Auto);
}

void AmbilightPlugin::Shutdown()
{
    if(!loaded)
    {
        return;
    }

    loaded = false;

    /*-----------------------------------------------------*\
    | OpenRGB calls back from its detection and SDK         |
    | threads. Refuse new calls, unregister, then wait for  |
    | any call already running before freeing what it uses |
    \*-----------------------------------------------------*/
    accepting_callbacks = false;

    rm->UnregisterDeviceListChangeCallback(DeviceListChangedCallback, this);
    rm->UnregisterDetectionStartCallback(DetectionStartedCallback, this);
    rm->UnregisterDetectionEndCallback(DetectionEndedCallback, this);

    for(int waited_ms = 0; callbacks_running.load() > 0 && waited_ms < 5000; waited_ms++)
    {
        Sleep(1);
    }

    if(schedule_timer)
    {
        schedule_timer->stop();
    }

    if(save_timer && save_timer->isActive())
    {
        save_timer->stop();
        SaveSettings();
    }

    hotkey.reset();

    if(engine)
    {
        engine->Stop();
    }

    engine.reset();
    sender.reset();

    if(power)
    {
        power->Stop();
    }

    power.reset();

    Log("Ambilight plugin unloaded");
    LogClose();
}

/*---------------------------------------------------------*\
| Devices                                                   |
\*---------------------------------------------------------*/
std::vector<RGBController*> AmbilightPlugin::LocalControllers(bool& client) const
{
    std::vector<RGBController*> list;
    bool                        any_remote = false;

    for(RGBController* controller : rm->GetRGBControllers())
    {
        if(controller->flags & CONTROLLER_FLAG_REMOTE)
        {
            any_remote = true;
            continue;
        }

        if(controller->colors.empty())
        {
            continue;
        }

        list.push_back(controller);
    }

    client = list.empty() && (any_remote || !rm->GetClients().empty());

    return list;
}

/*---------------------------------------------------------*\
| Only from a context where OpenRGB's list is stable: one   |
| of its callbacks, or the GUI thread outside detection     |
\*---------------------------------------------------------*/
void AmbilightPlugin::RefreshControllers()
{
    bool                        client = false;
    std::vector<RGBController*> list   = LocalControllers(client);

    client_mode = client;
    sender->SetControllers(list);

    QMetaObject::invokeMethod(this, "OnDevicesChanged", Qt::QueuedConnection);
}

/*---------------------------------------------------------*\
| Counts a callback in flight; `ok` is false once          |
| Shutdown() has started                                    |
\*---------------------------------------------------------*/
struct AmbilightPlugin::CallbackScope
{
    AmbilightPlugin* plugin;
    bool             ok;

    explicit CallbackScope(AmbilightPlugin* p) : plugin(p)
    {
        plugin->callbacks_running++;
        ok = plugin->accepting_callbacks.load();
    }

    ~CallbackScope()
    {
        plugin->callbacks_running--;
    }
};

void AmbilightPlugin::DeviceListChangedCallback(void* self)
{
    AmbilightPlugin* plugin = (AmbilightPlugin*)self;
    CallbackScope    scope(plugin);

    if(scope.ok && !plugin->detecting.load() && plugin->sender)
    {
        plugin->RefreshControllers();
    }
}

void AmbilightPlugin::DetectionStartedCallback(void* self)
{
    AmbilightPlugin* plugin = (AmbilightPlugin*)self;
    CallbackScope    scope(plugin);

    if(!scope.ok)
    {
        return;
    }

    plugin->detecting = true;

    /*-----------------------------------------------------*\
    | OpenRGB deletes every controller right after this     |
    | returns: forget them now, waiting out any write       |
    \*-----------------------------------------------------*/
    if(plugin->sender)
    {
        plugin->sender->SetControllers(std::vector<RGBController*>());
    }

    Log("Rescan started: devices released");
    QMetaObject::invokeMethod(plugin, "OnDevicesChanged", Qt::QueuedConnection);
}

void AmbilightPlugin::DetectionEndedCallback(void* self)
{
    AmbilightPlugin* plugin = (AmbilightPlugin*)self;
    CallbackScope    scope(plugin);

    if(!scope.ok)
    {
        return;
    }

    plugin->detecting = false;

    if(plugin->sender)
    {
        plugin->RefreshControllers();
        Log("Rescan finished: %u device(s)", (unsigned)plugin->sender->DeviceCount());
    }
}

void AmbilightPlugin::OnDevicesChanged()
{
    if(tab)
    {
        tab->RefreshDevices();
        tab->RefreshStatus();
    }

    UpdateTrayMenu();
}

std::vector<DeviceInfo> AmbilightPlugin::Devices() const
{
    return sender ? sender->Devices() : std::vector<DeviceInfo>();
}

/*---------------------------------------------------------*\
| Start / stop                                              |
\*---------------------------------------------------------*/
bool AmbilightPlugin::IsRunning() const
{
    return engine && engine->IsRunning();
}

AmbilightEngine::Status AmbilightPlugin::EngineStatus() const
{
    return engine ? engine->GetStatus() : AmbilightEngine::Status();
}

QString AmbilightPlugin::Unavailable() const
{
    if(!loaded)
    {
        return Tr("Ambilight is unloading.", "Ambilight выгружается.");
    }

    if(client_mode)
    {
        return Tr("This OpenRGB window is a client of the OpenRGB server, which owns the devices. Ambilight runs "
                  "inside the server instance: open it from the OpenRGB tray icon.",
                  "Это окно OpenRGB — клиент сервера OpenRGB, которому принадлежат устройства. Ambilight работает "
                  "в серверном экземпляре: откройте его через значок OpenRGB в трее.");
    }

    return QString();
}

QString AmbilightPlugin::HotkeyError() const
{
    switch(hotkey_error_kind)
    {
        case HotkeyErrorKind::ClientInstance:
            return Tr("The hotkey is handled by the OpenRGB server instance.", "Горячую клавишу обслуживает серверный экземпляр OpenRGB.");

        case HotkeyErrorKind::AlreadyUsed:
            return Tr("%1 is already used by another program", "Сочетание %1 уже занято другой программой").arg(hotkey_error_arg);

        case HotkeyErrorKind::Unavailable:
            return Tr("Hotkey unavailable: %1", "Горячая клавиша недоступна: %1").arg(hotkey_error_arg);

        default:
            return QString();
    }
}

void AmbilightPlugin::OnLanguageChanged()
{
    SetUiLanguage(store.Get().language);
    UpdateTrayMenu();
}

void AmbilightPlugin::Toggle()
{
    if(IsRunning())
    {
        StopAmbilight();
    }
    else
    {
        StartAmbilight();
    }
}

void AmbilightPlugin::StartAmbilight()
{
    if(!loaded || IsRunning())
    {
        return;
    }

    if(client_mode)
    {
        Log("Start ignored: this OpenRGB is a client");
        return;
    }

    engine->Start();
    UpdateTrayMenu();

    if(tab)
    {
        tab->RefreshStatus();
    }
}

void AmbilightPlugin::StopAmbilight()
{
    if(!loaded || !engine)
    {
        return;
    }

    engine->Stop();
    UpdateTrayMenu();

    if(tab)
    {
        tab->RefreshStatus();
    }
}

void AmbilightPlugin::OnHotkey()
{
    Log("Hotkey pressed");
    Toggle();
}

/*---------------------------------------------------------*\
| Tray                                                      |
\*---------------------------------------------------------*/
void AmbilightPlugin::UpdateTrayMenu()
{
    if(!tray_menu)
    {
        return;
    }

    bool running = IsRunning();

    tray_toggle->setText(running ? Tr("Stop Ambilight", "Выключить Ambilight") : Tr("Start Ambilight", "Включить Ambilight"));
    tray_toggle->setEnabled(loaded && (running || !client_mode));
    tray_music->setText(Tr("Music mode", "Музыкальный режим"));
    tray_music->setChecked(store.Get().audio);
    tray_music->setEnabled(loaded && !client_mode);
    if(tray_settings)
    {
        tray_settings->setText(Tr("Ambilight settings…", "Настройки Ambilight…"));
    }
}

void AmbilightPlugin::SetMusicMode(bool enabled)
{
    store.Update([enabled](AmbilightSettings& s) { s.audio = enabled; });
    SettingsEdited();

    if(tab)
    {
        tab->RefreshSettings();
    }
}

void AmbilightPlugin::ShowSettings()
{
    if(!tab)
    {
        return;
    }

    /*-----------------------------------------------------*\
    | Our widget sits in a container page of OpenRGB's main |
    | tab bar: select that page                             |
    \*-----------------------------------------------------*/
    std::vector<QWidget*> chain;

    for(QWidget* w = tab; w; w = w->parentWidget())
    {
        if(QTabWidget* tabs = qobject_cast<QTabWidget*>(w))
        {
            for(QWidget* page : chain)
            {
                if(tabs->indexOf(page) >= 0)
                {
                    tabs->setCurrentWidget(page);
                    break;
                }
            }
            break;
        }

        chain.push_back(w);
    }

    QWidget* window = tab->window();

    window->show();
    window->setWindowState((window->windowState() & ~Qt::WindowMinimized) | Qt::WindowActive);
    window->raise();
    window->activateWindow();
}

/*---------------------------------------------------------*\
| Settings                                                  |
\*---------------------------------------------------------*/
void AmbilightPlugin::LoadSettings()
{
    AmbilightSettings s;
    QJsonObject       json;

    if(ReadJsonFile(settings_path, json))
    {
        FromJson(json, s);
        store.Set(s);
        SetUiLanguage(s.language);
        Log("Settings loaded from %s", settings_path.toUtf8().constData());
        return;
    }

    /*-----------------------------------------------------*\
    | First run: take over the settings of the Python app,  |
    | whose config.json sits next to the OpenRGB-patched    |
    | folder                                                |
    \*-----------------------------------------------------*/
    QString old_config = QDir(QCoreApplication::applicationDirPath()).absoluteFilePath("../config.json");

    if(ReadJsonFile(old_config, json))
    {
        FromJson(json, s);

        std::vector<MonitorInfo> monitors = EnumerateMonitors();

        if(s.monitor_index >= 0 && s.monitor_index < (int)monitors.size())
        {
            s.monitor_device = monitors[s.monitor_index].device;
        }

        Log("Imported the old Ambilight settings from %s", QDir::cleanPath(old_config).toUtf8().constData());
    }

    store.Set(s);
    SetUiLanguage(s.language);
    SaveSettings();
}

void AmbilightPlugin::SaveSettings()
{
    QSaveFile file(settings_path);

    if(!file.open(QIODevice::WriteOnly))
    {
        Log("Cannot write %s", settings_path.toUtf8().constData());
        return;
    }

    file.write(QJsonDocument(ToJson(store.Get())).toJson(QJsonDocument::Indented));

    if(!file.commit())
    {
        Log("Cannot save %s", settings_path.toUtf8().constData());
    }
}

void AmbilightPlugin::SettingsEdited()
{
    SetUiLanguage(store.Get().language);

    if(save_timer)
    {
        save_timer->start();
    }

    if(!client_mode && store.Get().hotkey != bound_hotkey)
    {
        BindHotkey();
    }

    UpdateTrayMenu();
}

/*---------------------------------------------------------*\
| Hotkey                                                    |
\*---------------------------------------------------------*/
void AmbilightPlugin::BindHotkey()
{
    std::string combo = store.Get().hotkey;

    hotkey.reset();
    hotkey_error_kind = HotkeyErrorKind::None;
    hotkey_error_arg.clear();
    bound_hotkey = combo;

    if(combo.empty() || combo == "None")
    {
        return;
    }

    hotkey.reset(new GlobalHotkey());

    std::string error;

    if(hotkey->Start(combo, [this]() { QMetaObject::invokeMethod(this, "OnHotkey", Qt::QueuedConnection); }, error))
    {
        Log("Hotkey %s registered", GlobalHotkey::Describe(combo).c_str());
    }
    else
    {
        if(error.find("already used") != std::string::npos)
        {
            hotkey_error_kind = HotkeyErrorKind::AlreadyUsed;
            hotkey_error_arg  = QString::fromStdString(GlobalHotkey::Describe(combo));
        }
        else
        {
            hotkey_error_kind = HotkeyErrorKind::Unavailable;
            hotkey_error_arg  = QString::fromStdString(error);
        }
        Log("Hotkey %s: %s", combo.c_str(), error.c_str());
        hotkey.reset();
    }
}

void AmbilightPlugin::SuspendHotkey()
{
    hotkey.reset();

    /*-----------------------------------------------------*\
    | Nothing is bound now: recording the same combination  |
    | again, or one that failed before, must register anew  |
    \*-----------------------------------------------------*/
    bound_hotkey.clear();
}

void AmbilightPlugin::ResumeHotkey()
{
    if(loaded && !client_mode)
    {
        BindHotkey();
    }
}

/*---------------------------------------------------------*\
| Schedule: start/stop once on the day the time comes up    |
| (the old GUI re-fired for two minutes, so a manual stop   |
| right after the start time was undone)                    |
\*---------------------------------------------------------*/
void AmbilightPlugin::OnScheduleTimer()
{
    if(!loaded)
    {
        return;
    }

    AmbilightSettings s     = store.Get();
    QTime             now   = QTime::currentTime();
    QDate             today = QDate::currentDate();
    int               now_m = now.hour() * 60 + now.minute();

    auto due = [now_m](int h, int m)
    {
        int diff = now_m - (h * 60 + m);
        return diff >= 0 && diff < 2;
    };

    if(s.schedule_start_enabled && due(s.schedule_start_h, s.schedule_start_m) && last_start_fire != today)
    {
        last_start_fire = today;

        if(!IsRunning() && !client_mode)
        {
            Log("Schedule: starting at %02d:%02d", s.schedule_start_h, s.schedule_start_m);
            StartAmbilight();
        }
    }

    if(s.schedule_stop_enabled && due(s.schedule_stop_h, s.schedule_stop_m) && last_stop_fire != today)
    {
        last_stop_fire = today;

        if(IsRunning())
        {
            Log("Schedule: stopping at %02d:%02d", s.schedule_stop_h, s.schedule_stop_m);
            StopAmbilight();
        }
    }
}
