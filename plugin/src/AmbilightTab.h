/*---------------------------------------------------------*\
| AmbilightTab.h                                            |
|                                                           |
|   The "Ambilight" tab in OpenRGB's window: the settings   |
|   of the old tkinter GUI, a Start/Stop button, a status   |
|   line and a swatch of the colour on the LEDs. Every      |
|   change applies live to a running Ambilight and is       |
|   saved straight away.                                    |
\*---------------------------------------------------------*/

#pragma once

#include <QPushButton>
#include <QWidget>

#include <string>

class QCheckBox;
class QComboBox;
class QFrame;
class QLabel;
class QSlider;
class QSpinBox;
class QTimeEdit;
class QTimer;
class AmbilightPlugin;

/*---------------------------------------------------------*\
| Click, then press the combination: records a hotkey in    |
| the "ctrl+alt+a" spelling. Esc cancels, Backspace clears. |
\*---------------------------------------------------------*/
class HotkeyButton : public QPushButton
{
    Q_OBJECT

public:
    explicit HotkeyButton(QWidget* parent = nullptr);

    void                SetCombo(const std::string& combo);
    bool                IsRecording() const { return recording; }

signals:
    void                RecordingStarted();
    void                RecordingFinished(bool changed, const QString& combo);

protected:
    void                keyPressEvent(QKeyEvent* event) override;
    void                focusOutEvent(QFocusEvent* event) override;

private slots:
    void                OnClicked();

private:
    void                Finish(bool changed, const std::string& combo);

    bool                recording = false;
    std::string         combo;
};

class AmbilightTab : public QWidget
{
    Q_OBJECT

public:
    explicit AmbilightTab(AmbilightPlugin* plugin, QWidget* parent = nullptr);

    void                Detach();

public slots:
    void                RefreshDevices();
    void                RefreshSettings();
    void                RefreshStatus();

protected:
    void                showEvent(QShowEvent* event) override;
    void                hideEvent(QHideEvent* event) override;

private slots:
    void                OnEdited();
    void                OnMonitorChosen(int index);
    void                OnHotkeyRecording();
    void                OnHotkeyRecorded(bool changed, const QString& combo);

private:
    void                FillMonitors();
    void                UpdateSmoothHint();

    AmbilightPlugin*    plugin;
    bool                loading     = false;
    QTimer*             timer       = nullptr;

    QPushButton*        toggle;
    QLabel*             status;
    QFrame*             swatch;
    QLabel*             notice;

    QComboBox*          monitor;
    QSpinBox*           fps;
    QComboBox*          algo;
    QCheckBox*          edges;
    QCheckBox*          bars;
    QSpinBox*           black_threshold;

    QSlider*            brightness_slider;
    QSpinBox*           brightness;
    QSlider*            min_brightness_slider;
    QSpinBox*           min_brightness;
    QSlider*            smoothness_slider;
    QSpinBox*           smoothness;
    QLabel*             smooth_hint;
    QCheckBox*          saturation;
    QCheckBox*          gamma;
    QCheckBox*          hdr;

    QCheckBox*          sync_devices;
    QLabel*             devices;

    QCheckBox*          start_on_launch;
    QCheckBox*          display_sync;
    QCheckBox*          audio;
    HotkeyButton*       hotkey;
    QLabel*             hotkey_error;
    QCheckBox*          schedule_start;
    QTimeEdit*          schedule_start_time;
    QCheckBox*          schedule_stop;
    QTimeEdit*          schedule_stop_time;

    QString             shown_swatch;
};
