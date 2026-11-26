/*---------------------------------------------------------*\
| render_tab.cpp                                            |
|                                                           |
|   Loads OpenRGBAmbilightPlugin.dll the way OpenRGB does   |
|   (QPluginLoader + qobject_cast), feeds it a stand-in     |
|   ResourceManager with no devices, and saves the tab as   |
|   a PNG -- rendered off screen, nothing pops up.          |
|                                                           |
|   render_tab <plugin.dll> <config dir> <out.png>          |
\*---------------------------------------------------------*/

#include <QApplication>
#include <QPluginLoader>
#include <QTranslator>
#include <QWidget>

#include <cstdio>

#include "OpenRGBPluginInterface.h"
#include "ResourceManagerInterface.h"

class StandInResourceManager : public ResourceManagerInterface
{
public:
    std::vector<i2c_smbus_interface*>   busses;
    std::vector<RGBController*>         controllers;
    std::vector<NetworkClient*>         clients;
    filesystem::path                    config;

    std::vector<i2c_smbus_interface*>& GetI2CBusses() override                                  { return busses; }
    void RegisterRGBController(RGBController*) override                                         {}
    void UnregisterRGBController(RGBController*) override                                       {}
    void RegisterDeviceListChangeCallback(DeviceListChangeCallback, void*) override             {}
    void RegisterDetectionProgressCallback(DetectionProgressCallback, void*) override           {}
    void RegisterDetectionStartCallback(DetectionStartCallback, void*) override                 {}
    void RegisterDetectionEndCallback(DetectionEndCallback, void*) override                     {}
    void RegisterI2CBusListChangeCallback(I2CBusListChangeCallback, void*) override             {}
    void UnregisterDeviceListChangeCallback(DeviceListChangeCallback, void*) override           {}
    void UnregisterDetectionProgressCallback(DetectionProgressCallback, void*) override         {}
    void UnregisterDetectionStartCallback(DetectionStartCallback, void*) override               {}
    void UnregisterDetectionEndCallback(DetectionEndCallback, void*) override                   {}
    void UnregisterI2CBusListChangeCallback(I2CBusListChangeCallback, void*) override           {}
    std::vector<RGBController*>& GetRGBControllers() override                                   { return controllers; }
    unsigned int GetDetectionPercent() override                                                 { return 100; }
    filesystem::path GetConfigurationDirectory() override                                       { return config; }
    std::vector<NetworkClient*>& GetClients() override                                          { return clients; }
    NetworkServer* GetServer() override                                                         { return nullptr; }
    ProfileManager* GetProfileManager() override                                                { return nullptr; }
    SettingsManager* GetSettingsManager() override                                              { return nullptr; }
    void UpdateDeviceList() override                                                            {}
    void WaitForDeviceDetection() override                                                      {}
};

int main(int argc, char** argv)
{
    if(argc < 4)
    {
        fprintf(stderr, "usage: render_tab <plugin.dll> <config dir> <out.png> [width height]\n");
        return 2;
    }

    QApplication  app(argc, argv);
    QTranslator   translator;

    /*-----------------------------------------------------*\
    | Optional 6th argument: an OpenRGB .qm translation, as |
    | OpenRGB installs it before loading plugins            |
    \*-----------------------------------------------------*/
    if(argc > 6 && translator.load(QString::fromLocal8Bit(argv[6])))
    {
        app.installTranslator(&translator);
    }

    QPluginLoader loader(QString::fromLocal8Bit(argv[1]));
    QObject*      instance = loader.instance();

    if(!instance)
    {
        fprintf(stderr, "load failed: %s\n", loader.errorString().toLocal8Bit().constData());
        return 1;
    }

    OpenRGBPluginInterface* plugin = qobject_cast<OpenRGBPluginInterface*>(instance);

    if(!plugin)
    {
        fprintf(stderr, "not an OpenRGB plugin\n");
        return 1;
    }

    OpenRGBPluginInfo info = plugin->GetPluginInfo();

    printf("plugin: %s %s, API %u (OpenRGB wants %d), location %u, label %s\n",
           info.Name.c_str(), info.Version.c_str(), plugin->GetPluginAPIVersion(), OPENRGB_PLUGIN_API_VERSION,
           info.Location, info.Label.c_str());

    StandInResourceManager rm;

    rm.config = filesystem::u8path(argv[2]);
    plugin->Load(&rm);

    QWidget* widget = plugin->GetWidget();
    int      width  = argc > 5 ? atoi(argv[4]) : 1150;
    int      height = argc > 5 ? atoi(argv[5]) : 860;

    widget->setAttribute(Qt::WA_DontShowOnScreen);
    widget->resize(width, height);
    widget->show();

    for(int i = 0; i < 10; i++)
    {
        app.processEvents();
    }

    bool saved = widget->grab().save(QString::fromLocal8Bit(argv[3]));

    printf("tray menu: %s\n", plugin->GetTrayMenu() ? "yes" : "no");
    plugin->Unload();

    printf("%s %s\n", saved ? "saved" : "FAILED to save", argv[3]);
    return saved ? 0 : 1;
}
