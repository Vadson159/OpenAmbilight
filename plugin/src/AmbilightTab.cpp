/*---------------------------------------------------------*\
| AmbilightTab.cpp                                          |
\*---------------------------------------------------------*/

#include "AmbilightTab.h"

#include <QCheckBox>
#include <QComboBox>
#include <QFormLayout>
#include <QFrame>
#include <QGridLayout>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QKeyEvent>
#include <QLabel>
#include <QScrollArea>
#include <QSlider>
#include <QSpinBox>
#include <QTimeEdit>
#include <QTimer>
#include <QVBoxLayout>

#include <windows.h>

#include <cmath>

#include "AmbilightPlugin.h"
#include "ColorMath.h"
#include "GlobalHotkey.h"
#include "Monitors.h"
#include "UiText.h"

using namespace ambi;

static QString DescribeHotkey(const std::string& combo)
{
    std::string text = GlobalHotkey::Describe(combo);

    return text == "None" ? Tr("None", "Нет") : QString::fromStdString(text);
}

/*---------------------------------------------------------*\
| HotkeyButton                                              |
\*---------------------------------------------------------*/
HotkeyButton::HotkeyButton(QWidget* parent) : QPushButton(parent)
{
    setFocusPolicy(Qt::StrongFocus);
    connect(this, &QPushButton::clicked, this, &HotkeyButton::OnClicked);
}

void HotkeyButton::SetCombo(const std::string& new_combo)
{
    combo = new_combo;

    if(!recording)
    {
        setText(DescribeHotkey(combo));
    }
}

void HotkeyButton::OnClicked()
{
    if(recording)
    {
        return;
    }

    recording = true;
    setText(Tr("Press a key combination…  (Esc cancels, Backspace clears)", "Нажмите сочетание клавиш…  (Esc — отмена, Backspace — очистить)"));
    setFocus();
    grabKeyboard();

    emit RecordingStarted();
}

void HotkeyButton::Finish(bool changed, const std::string& new_combo)
{
    if(!recording)
    {
        return;
    }

    recording = false;
    releaseKeyboard();

    if(changed)
    {
        combo = new_combo;
    }

    setText(DescribeHotkey(combo));

    emit RecordingFinished(changed, QString::fromStdString(combo));
}

void HotkeyButton::keyPressEvent(QKeyEvent* event)
{
    if(!recording)
    {
        QPushButton::keyPressEvent(event);
        return;
    }

    unsigned int vk = event->nativeVirtualKey();

    switch(vk)
    {
        case VK_SHIFT:   case VK_LSHIFT:   case VK_RSHIFT:
        case VK_CONTROL: case VK_LCONTROL: case VK_RCONTROL:
        case VK_MENU:    case VK_LMENU:    case VK_RMENU:
        case VK_LWIN:    case VK_RWIN:
            return;                 /* wait for the actual key */
    }

    Qt::KeyboardModifiers held = event->modifiers();

    if(vk == VK_ESCAPE && held == Qt::NoModifier)
    {
        Finish(false, combo);
        return;
    }

    if((vk == VK_BACK || vk == VK_DELETE) && held == Qt::NoModifier)
    {
        Finish(true, "None");
        return;
    }

    unsigned int modifiers = 0;

    if(held & Qt::ControlModifier) modifiers |= MOD_CONTROL;
    if(held & Qt::AltModifier)     modifiers |= MOD_ALT;
    if(held & Qt::ShiftModifier)   modifiers |= MOD_SHIFT;
    if(held & Qt::MetaModifier)    modifiers |= MOD_WIN;

    std::string recorded = GlobalHotkey::FromVirtualKey(modifiers, vk);

    if(recorded.empty())
    {
        setText(Tr("That key cannot be a hotkey — try another  (Esc cancels)", "Эту клавишу нельзя назначить — выберите другую  (Esc — отмена)"));
        return;
    }

    Finish(true, recorded);
}

void HotkeyButton::focusOutEvent(QFocusEvent* event)
{
    Finish(false, combo);
    QPushButton::focusOutEvent(event);
}

/*---------------------------------------------------------*\
| Layout helpers                                            |
\*---------------------------------------------------------*/
static QLabel* Hint(const QString& text)
{
    QLabel* label = new QLabel(text);

    /*-----------------------------------------------------*\
    | Disabled = the theme's own muted text colour, in      |
    | OpenRGB's light and dark themes alike                 |
    \*-----------------------------------------------------*/
    label->setWordWrap(true);
    label->setEnabled(false);

    QFont font = label->font();
    font.setPointSizeF(font.pointSizeF() * 0.9);
    label->setFont(font);

    return label;
}

static QHBoxLayout* SliderRow(QSlider*& slider, QSpinBox*& spin, int min, int max)
{
    QHBoxLayout* row = new QHBoxLayout();

    slider = new QSlider(Qt::Horizontal);
    slider->setRange(min, max);

    spin = new QSpinBox();
    spin->setRange(min, max);
    spin->setMinimumWidth(64);

    QObject::connect(slider, &QSlider::valueChanged, spin, &QSpinBox::setValue);
    QObject::connect(spin, QOverload<int>::of(&QSpinBox::valueChanged), slider, &QSlider::setValue);

    row->addWidget(slider, 1);
    row->addWidget(spin);

    return row;
}

/*---------------------------------------------------------*\
| AmbilightTab                                              |
\*---------------------------------------------------------*/
AmbilightTab::AmbilightTab(AmbilightPlugin* owner, QWidget* parent) : QWidget(parent), plugin(owner)
{
    QVBoxLayout* outer = new QVBoxLayout(this);
    outer->setContentsMargins(0, 0, 0, 0);

    QScrollArea* scroll = new QScrollArea(this);
    scroll->setWidgetResizable(true);
    scroll->setFrameShape(QFrame::NoFrame);
    outer->addWidget(scroll);

    QWidget*     content = new QWidget();
    QVBoxLayout* root    = new QVBoxLayout(content);

    root->setContentsMargins(16, 12, 16, 12);
    root->setSpacing(10);
    scroll->setWidget(content);

    /*-----------------------------------------------------*\
    | Header and controls                                   |
    \*-----------------------------------------------------*/
    QLabel* title = new QLabel("Ambilight");
    QFont   title_font = title->font();

    title_font.setPointSizeF(title_font.pointSizeF() * 1.6);
    title_font.setBold(true);
    title->setFont(title_font);

    root->addWidget(title);
    root->addWidget(Hint(Tr("Screen-synced lighting for every OpenRGB device. Runs inside OpenRGB, so it starts "
                            "with OpenRGB at logon and drives the devices directly.",
                            "Подсветка всех устройств OpenRGB в цвет экрана. Работает внутри OpenRGB: запускается "
                            "вместе с ним при входе в систему и управляет устройствами напрямую.")));

    QHBoxLayout* controls = new QHBoxLayout();

    toggle = new QPushButton(Tr("▶  Start", "▶  Старт"));
    toggle->setMinimumSize(130, 34);

    QFont toggle_font = toggle->font();
    toggle_font.setBold(true);
    toggle->setFont(toggle_font);

    status = new QLabel(Tr("Stopped", "Остановлено"));

    swatch = new QFrame();
    swatch->setFixedSize(180, 26);
    swatch->setToolTip(Tr("The colour on the LEDs", "Цвет на подсветке"));

    controls->addWidget(toggle);
    controls->addSpacing(12);
    controls->addWidget(status, 1);
    controls->addWidget(swatch);
    root->addLayout(controls);

    notice = new QLabel();
    notice->setWordWrap(true);
    notice->setVisible(false);
    notice->setStyleSheet("QLabel { border: 1px solid #c8a200; border-radius: 4px; padding: 6px; }");
    root->addWidget(notice);

    /*-----------------------------------------------------*\
    | Capture                                               |
    \*-----------------------------------------------------*/
    QGroupBox*   capture_box  = new QGroupBox(Tr("Capture", "Захват"));
    QFormLayout* capture_form = new QFormLayout(capture_box);

    monitor = new QComboBox();
    capture_form->addRow(Tr("Display", "Монитор"), monitor);

    fps = new QSpinBox();
    fps->setRange(1, 240);
    fps->setSuffix(Tr(" FPS", " кадр/с"));
    capture_form->addRow(Tr("Refresh rate", "Частота"), fps);
    capture_form->addRow(Hint(Tr("How many times per second the screen is analysed. With \"Sync all devices\" the "
                                 "LEDs follow the slowest device (~3.6 updates/s here), so 20–30 looks the same.",
                                 "Сколько раз в секунду анализируется экран. С синхронизацией устройств подсветка "
                                 "следует за самым медленным (~3,6 обновления/с здесь), так что 20–30 выглядит так же.")));

    algo = new QComboBox();
    algo->addItem(Tr("Dominant", "Доминирующий цвет"));
    algo->addItem(Tr("Average", "Средний цвет"));
    algo->addItem(Tr("Center Focus", "Центр экрана"));
    capture_form->addRow(Tr("Algorithm", "Алгоритм"), algo);

    edges = new QCheckBox(Tr("Edge only", "Только края"));
    capture_form->addRow(edges);
    capture_form->addRow(Hint(Tr("Analyses only the outer 15% of the screen.", "Анализируются только внешние 15% экрана.")));

    bars = new QCheckBox(Tr("Skip black bars", "Пропускать чёрные полосы"));
    capture_form->addRow(bars);
    capture_form->addRow(Hint(Tr("Ignores letterbox bands so films don't darken the lighting.", "Не учитывает чёрные полосы фильмов, чтобы они не затемняли подсветку.")));

    black_threshold = new QSpinBox();
    black_threshold->setRange(1, 100);
    capture_form->addRow(Tr("Black threshold", "Порог чёрного"), black_threshold);
    capture_form->addRow(Hint(Tr("Pixels darker than this count as black bars.", "Пиксели темнее этого считаются чёрными полосами.")));

    /*-----------------------------------------------------*\
    | Colour                                                |
    \*-----------------------------------------------------*/
    QGroupBox*   colour_box  = new QGroupBox(Tr("Colour", "Цвет"));
    QFormLayout* colour_form = new QFormLayout(colour_box);

    colour_form->addRow(Tr("Max brightness", "Макс. яркость"), SliderRow(brightness_slider, brightness, 1, 100));
    colour_form->addRow(Tr("Min brightness", "Мин. яркость"), SliderRow(min_brightness_slider, min_brightness, 0, 100));
    colour_form->addRow(Hint(Tr("LEDs never go darker than this. 0 allows full black.", "Подсветка не становится темнее. 0 — можно погасить полностью.")));
    colour_form->addRow(Tr("Smoothness", "Плавность"), SliderRow(smoothness_slider, smoothness, 1, 100));

    smooth_hint = Hint("");
    colour_form->addRow(smooth_hint);

    saturation = new QCheckBox(Tr("Saturation boost (+50%)", "Усилить насыщенность (+50%)"));
    gamma      = new QCheckBox(Tr("Gamma correction", "Гамма-коррекция"));
    hdr        = new QCheckBox(Tr("Adaptive brightness (HDR)", "Адаптивная яркость (HDR)"));

    colour_form->addRow(saturation);
    colour_form->addRow(gamma);
    colour_form->addRow(hdr);
    colour_form->addRow(Hint(Tr("Adaptive brightness dims the LEDs in dark scenes and lifts them in bright ones.", "Адаптивная яркость приглушает подсветку в тёмных сценах и усиливает в ярких.")));

    /*-----------------------------------------------------*\
    | Devices                                               |
    \*-----------------------------------------------------*/
    QGroupBox*   devices_box    = new QGroupBox(Tr("Devices", "Устройства"));
    QVBoxLayout* devices_layout = new QVBoxLayout(devices_box);

    sync_devices = new QCheckBox(Tr("Sync all devices", "Синхронизировать устройства"));
    devices_layout->addWidget(sync_devices);
    devices_layout->addWidget(Hint(Tr("Shows the same frames on every device and compensates each device's delay. "
                                      "The update rate follows the slowest device; separate motherboard headers "
                                      "may still differ.",
                                      "Все устройства показывают одни и те же кадры с поправкой на задержку каждого. "
                                      "Частота обновления — по самому медленному; разные разъёмы материнской платы "
                                      "всё же могут немного расходиться.")));

    devices = new QLabel();
    devices->setWordWrap(true);
    devices->setTextFormat(Qt::RichText);
    devices_layout->addWidget(devices);
    devices_layout->addStretch(1);

    /*-----------------------------------------------------*\
    | Behaviour                                             |
    \*-----------------------------------------------------*/
    QGroupBox*   behaviour_box  = new QGroupBox(Tr("Behaviour", "Поведение"));
    QFormLayout* behaviour_form = new QFormLayout(behaviour_box);

    start_on_launch = new QCheckBox(Tr("Start Ambilight when OpenRGB starts", "Включать Ambilight при запуске OpenRGB"));
    behaviour_form->addRow(start_on_launch);
    behaviour_form->addRow(Hint(Tr("OpenRGB itself starts at logon (task \"OpenRGB (elevated)\").", "Сам OpenRGB запускается при входе в систему (задача «OpenRGB (elevated)»).")));

    display_sync = new QCheckBox(Tr("Turn off with display", "Выключать вместе с дисплеем"));
    behaviour_form->addRow(display_sync);
    behaviour_form->addRow(Hint(Tr("LEDs go dark while Windows has the display asleep or the PC is locked.", "Подсветка гаснет, пока дисплей спит или компьютер заблокирован.")));

    audio = new QCheckBox(Tr("Music mode (audio visualizer)", "Музыкальный режим (визуализатор звука)"));
    behaviour_form->addRow(audio);
    behaviour_form->addRow(Hint(Tr("Stops screen capture and pulses colours to the computer's sound.", "Вместо захвата экрана подсветка пульсирует в такт звуку компьютера.")));

    hotkey = new HotkeyButton();
    behaviour_form->addRow(Tr("Hotkey", "Горячая клавиша"), hotkey);

    hotkey_error = Hint("");
    hotkey_error->setEnabled(true);
    hotkey_error->setStyleSheet("QLabel { color: #d9534f; }");
    hotkey_error->setVisible(false);
    behaviour_form->addRow(hotkey_error);
    behaviour_form->addRow(Hint(Tr("Toggles Ambilight on and off from anywhere.", "Включает и выключает Ambilight откуда угодно.")));

    QHBoxLayout* start_row = new QHBoxLayout();
    schedule_start      = new QCheckBox(Tr("Start at", "Включать в"));
    schedule_start_time = new QTimeEdit();
    schedule_start_time->setDisplayFormat("HH:mm");
    start_row->addWidget(schedule_start);
    start_row->addWidget(schedule_start_time);
    start_row->addStretch(1);
    behaviour_form->addRow(start_row);

    QHBoxLayout* stop_row = new QHBoxLayout();
    schedule_stop      = new QCheckBox(Tr("Stop at", "Выключать в"));
    schedule_stop_time = new QTimeEdit();
    schedule_stop_time->setDisplayFormat("HH:mm");
    stop_row->addWidget(schedule_stop);
    stop_row->addWidget(schedule_stop_time);
    stop_row->addStretch(1);
    behaviour_form->addRow(stop_row);

    QGridLayout* grid = new QGridLayout();

    grid->setColumnStretch(0, 1);
    grid->setColumnStretch(1, 1);
    grid->addWidget(capture_box,   0, 0);
    grid->addWidget(colour_box,    0, 1);
    grid->addWidget(devices_box,   1, 0);
    grid->addWidget(behaviour_box, 1, 1);

    root->addLayout(grid);
    root->addStretch(1);

    /*-----------------------------------------------------*\
    | Every edit goes live and is saved                     |
    \*-----------------------------------------------------*/
    for(QCheckBox* box : { edges, bars, saturation, gamma, hdr, sync_devices, start_on_launch, display_sync, audio,
                           schedule_start, schedule_stop })
    {
        connect(box, &QCheckBox::toggled, this, &AmbilightTab::OnEdited);
    }

    for(QSpinBox* spin : { fps, black_threshold, brightness, min_brightness, smoothness })
    {
        connect(spin, QOverload<int>::of(&QSpinBox::valueChanged), this, &AmbilightTab::OnEdited);
    }

    connect(algo, QOverload<int>::of(&QComboBox::currentIndexChanged), this, &AmbilightTab::OnEdited);
    connect(monitor, QOverload<int>::of(&QComboBox::currentIndexChanged), this, &AmbilightTab::OnMonitorChosen);
    connect(schedule_start_time, &QTimeEdit::timeChanged, this, &AmbilightTab::OnEdited);
    connect(schedule_stop_time, &QTimeEdit::timeChanged, this, &AmbilightTab::OnEdited);
    connect(hotkey, &HotkeyButton::RecordingStarted, this, &AmbilightTab::OnHotkeyRecording);
    connect(hotkey, &HotkeyButton::RecordingFinished, this, &AmbilightTab::OnHotkeyRecorded);

    connect(toggle, &QPushButton::clicked, this, [this]()
    {
        if(plugin)
        {
            plugin->Toggle();
            RefreshStatus();
        }
    });

    timer = new QTimer(this);
    timer->setInterval(66);
    connect(timer, &QTimer::timeout, this, &AmbilightTab::RefreshStatus);

    FillMonitors();
    RefreshSettings();
    RefreshDevices();
    RefreshStatus();
}

void AmbilightTab::Detach()
{
    plugin = nullptr;
    timer->stop();
}

void AmbilightTab::showEvent(QShowEvent* event)
{
    QWidget::showEvent(event);

    if(plugin)
    {
        FillMonitors();
        RefreshSettings();
        RefreshDevices();
        RefreshStatus();
        timer->start();
    }
}

void AmbilightTab::hideEvent(QHideEvent* event)
{
    QWidget::hideEvent(event);
    timer->stop();
}

void AmbilightTab::FillMonitors()
{
    if(!plugin)
    {
        return;
    }

    AmbilightSettings        s    = plugin->Store().Get();
    std::vector<MonitorInfo> list = EnumerateMonitors();
    int                      pick = -1;

    loading = true;
    monitor->clear();

    for(const MonitorInfo& m : list)
    {
        QString device = QString::fromStdWString(m.device);
        QString name   = device;

        name.remove("\\\\.\\");

        monitor->addItem(Tr("Monitor %1 — %2×%3%4  (%5)", "Монитор %1 — %2×%3%4  (%5)")
                         .arg(m.index).arg(m.width).arg(m.height)
                         .arg(m.primary ? Tr(", primary", ", основной") : QString()).arg(name), device);

        if(!s.monitor_device.empty() && _wcsicmp(m.device.c_str(), s.monitor_device.c_str()) == 0)
        {
            pick = m.index;
        }
    }

    if(pick < 0 && s.monitor_index < (int)list.size())
    {
        pick = s.monitor_index;
    }

    monitor->setCurrentIndex(pick);
    loading = false;
}

void AmbilightTab::RefreshSettings()
{
    if(!plugin)
    {
        return;
    }

    AmbilightSettings s = plugin->Store().Get();

    loading = true;

    fps->setValue(s.fps);
    algo->setCurrentIndex((int)s.algo);
    edges->setChecked(s.edges);
    bars->setChecked(s.bars);
    black_threshold->setValue(s.black_threshold);

    brightness->setValue(s.brightness);
    min_brightness->setValue(s.min_brightness);
    smoothness->setValue(s.smoothness);
    saturation->setChecked(s.saturation);
    gamma->setChecked(s.gamma);
    hdr->setChecked(s.hdr);

    sync_devices->setChecked(s.sync_devices);

    start_on_launch->setChecked(s.start_on_launch);
    display_sync->setChecked(s.display_sync);
    audio->setChecked(s.audio);

    if(!hotkey->IsRecording())
    {
        hotkey->SetCombo(s.hotkey);
    }

    schedule_start->setChecked(s.schedule_start_enabled);
    schedule_start_time->setTime(QTime(s.schedule_start_h, s.schedule_start_m));
    schedule_stop->setChecked(s.schedule_stop_enabled);
    schedule_stop_time->setTime(QTime(s.schedule_stop_h, s.schedule_stop_m));

    UpdateSmoothHint();

    QString error = plugin->HotkeyError();

    hotkey_error->setText(error);
    hotkey_error->setVisible(!error.isEmpty());

    loading = false;
}

void AmbilightTab::UpdateSmoothHint()
{
    int value = smoothness->value();

    smooth_hint->setText(Tr("Higher = snappier. %1 ≈ %2 ms per colour change. Default: 55.", "Больше — быстрее. %1 ≈ %2 мс на смену цвета. По умолчанию: 55.")
                         .arg(value).arg((int)std::lround(SmoothSettleMs(value))));
}

void AmbilightTab::RefreshDevices()
{
    if(!plugin)
    {
        return;
    }

    std::vector<DeviceInfo> list = plugin->Devices();

    if(list.empty())
    {
        devices->setText(Tr("<i>No devices to drive.</i>", "<i>Нет устройств.</i>"));
        return;
    }

    QString html = "<table cellspacing='0' cellpadding='1'>";

    for(const DeviceInfo& info : list)
    {
        QString timing = Tr("%1 ms", "%1 мс").arg(std::lround(info.latency_ms));

        if(std::fabs(info.busy_ms - info.latency_ms) > 0.5)
        {
            timing += Tr(" (busy %1 ms)", " (занято %1 мс)").arg(std::lround(info.busy_ms));
        }

        html += QString("<tr><td>%1</td><td style='padding-left:12px'>%2</td></tr>")
                .arg(QString::fromStdString(info.name).toHtmlEscaped(), timing);
    }

    html += "</table>";
    devices->setText(html);
}

void AmbilightTab::RefreshStatus()
{
    if(!plugin)
    {
        return;
    }

    QString unavailable = plugin->Unavailable();

    notice->setText(unavailable);
    notice->setVisible(!unavailable.isEmpty());

    bool running = plugin->IsRunning();

    toggle->setText(running ? Tr("■  Stop", "■  Стоп") : Tr("▶  Start", "▶  Старт"));
    toggle->setEnabled(running || unavailable.isEmpty());

    AmbilightEngine::Status st    = plugin->EngineStatus();
    size_t                  count = plugin->Devices().size();
    QString                 devs  = UiRussian() ? QString::fromUtf8("устройств: %1").arg(count)
                                            : QString("%1 device%2").arg(count).arg(count == 1 ? "" : "s");
    QString                 text;

    switch(st.state)
    {
        case AmbilightEngine::Starting:
            text = Tr("Starting…", "Запуск…");
            break;

        case AmbilightEngine::Running:
            text = Tr("Running  •  %1  •  %2", "Работает  •  %1  •  %2").arg(devs, QString::fromStdString(st.backend.empty() ? "DXGI" : st.backend));
            break;

        case AmbilightEngine::CaptureLost:
            text = Tr("Waiting for the desktop (lock screen, UAC prompt or a mode change)", "Ждём рабочий стол (экран блокировки, окно UAC или смена режима)");
            break;

        case AmbilightEngine::DisplayOff:
            text = Tr("Display is off  •  LEDs dark until it wakes", "Дисплей выключен  •  подсветка погашена до его включения");
            break;

        case AmbilightEngine::Music:
            text = Tr("Music mode  •  %1", "Музыкальный режим  •  %1").arg(devs);
            break;

        default:
            text = running ? Tr("Running", "Работает") : Tr("Stopped", "Остановлено");
            break;
    }

    status->setText(text);

    QString colour = (running && st.has_color)
                   ? QString("#%1%2%3").arg(st.color.r, 2, 16, QChar('0')).arg(st.color.g, 2, 16, QChar('0')).arg(st.color.b, 2, 16, QChar('0'))
                   : QString("transparent");

    if(colour != shown_swatch)
    {
        shown_swatch = colour;
        swatch->setStyleSheet(QString("QFrame { background-color: %1; border: 1px solid palette(mid); border-radius: 4px; }").arg(colour));
    }
}

void AmbilightTab::OnMonitorChosen(int)
{
    OnEdited();
}

void AmbilightTab::OnEdited()
{
    UpdateSmoothHint();

    if(loading || !plugin)
    {
        return;
    }

    plugin->Store().Update([this](AmbilightSettings& s)
    {
        if(monitor->currentIndex() >= 0)
        {
            s.monitor_device = monitor->currentData().toString().toStdWString();
            s.monitor_index  = monitor->currentIndex();
        }

        s.fps                    = fps->value();
        s.algo                   = (Algo)algo->currentIndex();
        s.edges                  = edges->isChecked();
        s.bars                   = bars->isChecked();
        s.black_threshold        = black_threshold->value();

        s.brightness             = brightness->value();
        s.min_brightness         = min_brightness->value();
        s.smoothness             = smoothness->value();
        s.saturation             = saturation->isChecked();
        s.gamma                  = gamma->isChecked();
        s.hdr                    = hdr->isChecked();

        s.sync_devices           = sync_devices->isChecked();

        s.start_on_launch        = start_on_launch->isChecked();
        s.display_sync           = display_sync->isChecked();
        s.audio                  = audio->isChecked();

        s.schedule_start_enabled = schedule_start->isChecked();
        s.schedule_start_h       = schedule_start_time->time().hour();
        s.schedule_start_m       = schedule_start_time->time().minute();
        s.schedule_stop_enabled  = schedule_stop->isChecked();
        s.schedule_stop_h        = schedule_stop_time->time().hour();
        s.schedule_stop_m        = schedule_stop_time->time().minute();
    });

    plugin->SettingsEdited();
}

void AmbilightTab::OnHotkeyRecording()
{
    if(plugin)
    {
        plugin->SuspendHotkey();
    }
}

void AmbilightTab::OnHotkeyRecorded(bool changed, const QString& combo)
{
    if(!plugin)
    {
        return;
    }

    if(changed)
    {
        std::string value = combo.toStdString();

        plugin->Store().Update([&value](AmbilightSettings& s) { s.hotkey = value; });
        plugin->SettingsEdited();
    }
    else
    {
        plugin->ResumeHotkey();
    }

    QString error = plugin->HotkeyError();

    hotkey_error->setText(error);
    hotkey_error->setVisible(!error.isEmpty());
}
