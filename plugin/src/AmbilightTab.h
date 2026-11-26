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
class QEvent;
class QFrame;
class QGroupBox;
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
    void                Retranslate();
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
    bool                bad_key   = false;
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
    void                changeEvent(QEvent* event) override;
    void                showEvent(QShowEvent* event) override;
    void                hideEvent(QHideEvent* event) override;

private slots:
    void                OnEdited();
    void                OnLanguageChosen(int index);
    void                OnMonitorChosen(int index);
    void                OnHotkeyRecording();
    void                OnHotkeyRecorded(bool changed, const QString& combo);

private:
    void                RetranslateUi();
    void                FillMonitors();
    void                UpdateSmoothHint();

    AmbilightPlugin*    plugin;
    bool                loading     = false;
    QTimer*             timer       = nullptr;

    QLabel*             subtitle_hint;
    QLabel*             language_label;
    QComboBox*          language;
    QPushButton*        toggle;
    QLabel*             status;
    QFrame*             swatch;
    QLabel*             notice;

    QGroupBox*          capture_box;
    QLabel*             monitor_label;
    QComboBox*          monitor;
    QLabel*             fps_label;
    QSpinBox*           fps;
    QLabel*             fps_hint;
    QLabel*             algo_label;
    QComboBox*          algo;
    QCheckBox*          edges;
    QLabel*             edges_hint;
    QCheckBox*          bars;
    QLabel*             bars_hint;
    QLabel*             black_threshold_label;
    QSpinBox*           black_threshold;
    QLabel*             black_threshold_hint;

    QGroupBox*          colour_box;
    QLabel*             brightness_label;
    QSlider*            brightness_slider;
    QSpinBox*           brightness;
    QLabel*             min_brightness_label;
    QSlider*            min_brightness_slider;
    QSpinBox*           min_brightness;
    QLabel*             min_brightness_hint;
    QLabel*             smoothness_label;
    QSlider*            smoothness_slider;
    QSpinBox*           smoothness;
    QLabel*             smooth_hint;
    QCheckBox*          saturation;
    QCheckBox*          gamma;
    QCheckBox*          hdr;
    QLabel*             hdr_hint;

    QGroupBox*          devices_box;
    QCheckBox*          sync_devices;
    QLabel*             sync_devices_hint;
    QLabel*             devices;

    QGroupBox*          behaviour_box;
    QCheckBox*          start_on_launch;
    QLabel*             start_on_launch_hint;
    QCheckBox*          display_sync;
    QLabel*             display_sync_hint;
    QCheckBox*          audio;
    QLabel*             audio_hint;
    QLabel*             hotkey_label;
    HotkeyButton*       hotkey;
    QLabel*             hotkey_error;
    QLabel*             hotkey_hint;
    QCheckBox*          schedule_start;
    QTimeEdit*          schedule_start_time;
    QCheckBox*          schedule_stop;
    QTimeEdit*          schedule_stop_time;

    QString             shown_swatch;
};
