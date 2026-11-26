# Off-screen renderer for the plugin's tab (see render_tab.cpp).
QT       += core gui widgets
TEMPLATE  = app
CONFIG   += console c++17 release
CONFIG   -= debug debug_and_release
TARGET    = render_tab

OPENRGB_DIR = $$PWD/../../_orgb_build/OpenRGB

INCLUDEPATH += $$OPENRGB_DIR $$OPENRGB_DIR/RGBController $$OPENRGB_DIR/i2c_smbus $$OPENRGB_DIR/net_port
DEFINES     -= UNICODE
DEFINES     += _MBCS WIN32 WIN32_LEAN_AND_MEAN NOMINMAX

SOURCES += render_tab.cpp

DESTDIR     = $$PWD/../build/tests
OBJECTS_DIR = $$PWD/../build/tests/render_obj
MOC_DIR     = $$PWD/../build/tests/render_moc
