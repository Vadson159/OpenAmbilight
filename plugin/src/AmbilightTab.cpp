/*---------------------------------------------------------*\
| AmbilightTab.cpp                                          |
\*---------------------------------------------------------*/

#include "AmbilightTab.h"

#include <QCheckBox>
#include <QComboBox>
#include <QEvent>
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

void HotkeyButton::Retranslate()
{
    if(!recording)
    {
        setText(DescribeHotkey(combo));
    }
    else if(bad_key)
    {
        setText(Tr("That key cannot be a hotkey — try another  (Esc cancels)", "Эту клавишу нельзя назначить — выберите другую  (Esc — отмена)"));
    }
    else
    {
        setText(Tr("Press a key combination…  (Esc cancels, Backspace clears)", "Нажмите сочетание клавиш…  (Esc — отмена, Backspace — очистить)"));
    }
}

void HotkeyButton::OnClicked()
{
    if(recording)
    {
        return;
    }

    recording = true;
    bad_key   = false;
    Retranslate();
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
    bad_key   = false;
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
        bad_key = true;
        Retranslate();
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
    QHBoxLayout* header = new QHBoxLayout();
    QLabel*      title  = new QLabel("Ambilight");
    QFont        title_font = title->font();

    title_font.setPointSizeF(title_font.pointSizeF() * 1.6);
    title_font.setBold(true);
    title->setFont(title_font);

    language_label = new QLabel();
    language       = new QComboBox();
    language->addItem("", (int)UiLanguage::Auto);
    language->addItem("English", (int)UiLanguage::English);
    language->addItem(QString::fromUtf8("Русский"), (int)UiLanguage::Russian);

    header->addWidget(title);
    header->addStretch(1);
    header->addWidget(language_label);
    header->addWidget(language);
    root->addLayout(header);

    subtitle_hint = Hint("");
    root->addWidget(subtitle_hint);

    QHBoxLayout* controls = new QHBoxLayout();

    toggle = new QPushButton();
    toggle->setMinimumSize(130, 34);

    QFont toggle_font = toggle->font();
    toggle_font.setBold(true);
    toggle->setFont(toggle_font);

    status = new QLabel();

    swatch = new QFrame();
    swatch->setFixedSize(180, 26);

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
    capture_box = new QGroupBox();
    QFormLayout* capture_form = new QFormLayout(capture_box);

    monitor_label = new QLabel();
    monitor       = new QComboBox();
    capture_form->addRow(monitor_label, monitor);

    fps_label = new QLabel();
    fps       = new QSpinBox();
    fps->setRange(1, 240);
    capture_form->addRow(fps_label, fps);
    fps_hint = Hint("");
    capture_form->addRow(fps_hint);

    algo_label = new QLabel();
    algo       = new QComboBox();
    algo->addItem("");
    algo->addItem("");
    algo->addItem("");
    capture_form->addRow(algo_label, algo);

    edges      = new QCheckBox();
    edges_hint = Hint("");
    capture_form->addRow(edges);
    capture_form->addRow(edges_hint);

    bars      = new QCheckBox();
    bars_hint = Hint("");
    capture_form->addRow(bars);
    capture_form->addRow(bars_hint);

    black_threshold_label = new QLabel();
    black_threshold       = new QSpinBox();
    black_threshold->setRange(1, 100);
    capture_form->addRow(black_threshold_label, black_threshold);
    black_threshold_hint = Hint("");
    capture_form->addRow(black_threshold_hint);

    /*-----------------------------------------------------*\
    | Colour                                                |
    \*-----------------------------------------------------*/
    colour_box = new QGroupBox();
    QFormLayout* colour_form = new QFormLayout(colour_box);

    brightness_label     = new QLabel();
    min_brightness_label = new QLabel();
    min_brightness_hint  = Hint("");
    smoothness_label     = new QLabel();
    smooth_hint          = Hint("");

    colour_form->addRow(brightness_label, SliderRow(brightness_slider, brightness, 1, 100));
    colour_form->addRow(min_brightness_label, SliderRow(min_brightness_slider, min_brightness, 0, 100));
    colour_form->addRow(min_brightness_hint);
    colour_form->addRow(smoothness_label, SliderRow(smoothness_slider, smoothness, 1, 100));
    colour_form->addRow(smooth_hint);

    saturation = new QCheckBox();
    gamma      = new QCheckBox();
    hdr        = new QCheckBox();
    hdr_hint   = Hint("");

    colour_form->addRow(saturation);
    colour_form->addRow(gamma);
    colour_form->addRow(hdr);
    colour_form->addRow(hdr_hint);

    /*-----------------------------------------------------*\
    | Devices                                               |
    \*-----------------------------------------------------*/
    devices_box = new QGroupBox();
    QVBoxLayout* devices_layout = new QVBoxLayout(devices_box);

    sync_devices      = new QCheckBox();
    sync_devices_hint = Hint("");
    devices_layout->addWidget(sync_devices);
    devices_layout->addWidget(sync_devices_hint);

    devices = new QLabel();
    devices->setWordWrap(true);
    devices->setTextFormat(Qt::RichText);
    devices_layout->addWidget(devices);
    devices_layout->addStretch(1);

    /*-----------------------------------------------------*\
    | Behaviour                                             |
    \*-----------------------------------------------------*/
    behaviour_box = new QGroupBox();
    QFormLayout* behaviour_form = new QFormLayout(behaviour_box);

    start_on_launch      = new QCheckBox();
    start_on_launch_hint = Hint("");
    behaviour_form->addRow(start_on_launch);
    behaviour_form->addRow(start_on_launch_hint);

    display_sync      = new QCheckBox();
    display_sync_hint = Hint("");
    behaviour_form->addRow(display_sync);
    behaviour_form->addRow(display_sync_hint);

    audio      = new QCheckBox();
    audio_hint = Hint("");
    behaviour_form->addRow(audio);
    behaviour_form->addRow(audio_hint);

    hotkey_label = new QLabel();
    hotkey       = new HotkeyButton();
    behaviour_form->addRow(hotkey_label, hotkey);

    hotkey_error = Hint("");
    hotkey_error->setEnabled(true);
    hotkey_error->setStyleSheet("QLabel { color: #d9534f; }");
    hotkey_error->setVisible(false);
    behaviour_form->addRow(hotkey_error);
    hotkey_hint = Hint("");
    behaviour_form->addRow(hotkey_hint);

    QHBoxLayout* start_row = new QHBoxLayout();
    schedule_start      = new QCheckBox();
    schedule_start_time = new QTimeEdit();
    schedule_start_time->setDisplayFormat("HH:mm");
    start_row->addWidget(schedule_start);
    start_row->addWidget(schedule_start_time);
    start_row->addStretch(1);
    behaviour_form->addRow(start_row);

    QHBoxLayout* stop_row = new QHBoxLayout();
    schedule_stop      = new QCheckBox();
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

    connect(language, QOverload<int>::of(&QComboBox::currentIndexChanged), this, &AmbilightTab::OnLanguageChosen);
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

    RefreshSettings();
    RetranslateUi();
}

void AmbilightTab::Detach()
{
    plugin = nullptr;
    timer->stop();
}

void AmbilightTab::changeEvent(QEvent* event)
{
    QWidget::changeEvent(event);

    if(event->type() == QEvent::LanguageChange)
    {
        RetranslateUi();

        if(plugin)
        {
            plugin->OnLanguageChanged();
        }
    }
}

void AmbilightTab::showEvent(QShowEvent* event)
{
    QWidget::showEvent(event);

    if(plugin)
    {
        RefreshSettings();
        RetranslateUi();
        timer->start();
    }
}

void AmbilightTab::hideEvent(QHideEvent* event)
{
    QWidget::hideEvent(event);
    timer->stop();
}

void AmbilightTab::RetranslateUi()
{
    bool was_loading = loading;
    loading = true;

    subtitle_hint->setText(Tr("Screen-synced lighting for every OpenRGB device. Runs inside OpenRGB, so it starts "
                              "with OpenRGB at logon and drives the devices directly.",
                              "Подсветка всех устройств OpenRGB в цвет экрана. Работает внутри OpenRGB: запускается "
                              "вместе с ним при входе в систему и управляет устройствами напрямую."));

    language_label->setText(Tr("Language", "Язык"));
    language->setItemText(0, Tr("Auto (OpenRGB)", "Авто (OpenRGB)"));
    language->setItemText(1, "English");
    language->setItemText(2, QString::fromUtf8("Русский"));

    swatch->setToolTip(Tr("The colour on the LEDs", "Цвет на подсветке"));

    capture_box->setTitle(Tr("Capture", "Захват"));
    monitor_label->setText(Tr("Display", "Монитор"));
    fps_label->setText(Tr("Refresh rate", "Частота"));
    fps->setSuffix(Tr(" FPS", " кадр/с"));
    fps_hint->setText(Tr("How many times per second the screen is analysed. With \"Sync all devices\" the "
                         "LEDs follow the slowest device (~3.6 updates/s here), so 20–30 looks the same.",
                         "Сколько раз в секунду анализируется экран. С синхронизацией устройств подсветка "
                         "следует за самым медленным (~3,6 обновления/с здесь), так что 20–30 выглядит так же."));
    algo_label->setText(Tr("Algorithm", "Алгоритм"));
    algo->setItemText(0, Tr("Dominant", "Доминирующий цвет"));
    algo->setItemText(1, Tr("Average", "Средний цвет"));
    algo->setItemText(2, Tr("Center Focus", "Центр экрана"));
    edges->setText(Tr("Edge only", "Только края"));
    edges_hint->setText(Tr("Analyses only the outer 15% of the screen.", "Анализируются только внешние 15% экрана."));
    bars->setText(Tr("Skip black bars", "Пропускать чёрные полосы"));
    bars_hint->setText(Tr("Ignores letterbox bands so films don't darken the lighting.", "Не учитывает чёрные полосы фильмов, чтобы они не затемняли подсветку."));
    black_threshold_label->setText(Tr("Black threshold", "Порог чёрного"));
    black_threshold_hint->setText(Tr("Pixels darker than this count as black bars.", "Пиксели темнее этого считаются чёрными полосами."));

    colour_box->setTitle(Tr("Colour", "Цвет"));
    brightness_label->setText(Tr("Max brightness", "Макс. яркость"));
    min_brightness_label->setText(Tr("Min brightness", "Мин. яркость"));
    min_brightness_hint->setText(Tr("LEDs never go darker than this. 0 allows full black.", "Подсветка не становится темнее. 0 — можно погасить полностью."));
    smoothness_label->setText(Tr("Smoothness", "Плавность"));
    saturation->setText(Tr("Saturation boost (+50%)", "Усилить насыщенность (+50%)"));
    gamma->setText(Tr("Gamma correction", "Гамма-коррекция"));
    hdr->setText(Tr("Adaptive brightness (HDR)", "Адаптивная яркость (HDR)"));
    hdr_hint->setText(Tr("Adaptive brightness dims the LEDs in dark scenes and lifts them in bright ones.", "Адаптивная яркость приглушает подсветку в тёмных сценах и усиливает в ярких."));

    devices_box->setTitle(Tr("Devices", "Устройства"));
    sync_devices->setText(Tr("Sync all devices", "Синхронизировать устройства"));
    sync_devices_hint->setText(Tr("Shows the same frames on every device and compensates each device's delay. "
                                  "The update rate follows the slowest device; separate motherboard headers "
                                  "may still differ.",
                                  "Все устройства показывают одни и те же кадры с поправкой на задержку каждого. "
                                  "Частота обновления — по самому медленному; разные разъёмы материнской платы "
                                  "всё же могут немного расходиться."));

    behaviour_box->setTitle(Tr("Behaviour", "Поведение"));
    start_on_launch->setText(Tr("Start Ambilight when OpenRGB starts", "Включать Ambilight при запуске OpenRGB"));
    start_on_launch_hint->setText(Tr("OpenRGB itself starts at logon (task \"OpenRGB (elevated)\").", "Сам OpenRGB запускается при входе в систему (задача «OpenRGB (elevated)»)."));
    display_sync->setText(Tr("Turn off with display", "Выключать вместе с дисплеем"));
    display_sync_hint->setText(Tr("LEDs go dark while Windows has the display asleep or the PC is locked.", "Подсветка гаснет, пока дисплей спит или компьютер заблокирован."));
    audio->setText(Tr("Music mode (audio visualizer)", "Музыкальный режим (визуализатор звука)"));
    audio_hint->setText(Tr("Stops screen capture and pulses colours to the computer's sound.", "Вместо захвата экрана подсветка пульсирует в такт звуку компьютера."));
    hotkey_label->setText(Tr("Hotkey", "Горячая клавиша"));
    hotkey->Retranslate();
    hotkey_hint->setText(Tr("Toggles Ambilight on and off from anywhere.", "Включает и выключает Ambilight откуда угодно."));
    schedule_start->setText(Tr("Start at", "Включать в"));
    schedule_stop->setText(Tr("Stop at", "Выключать в"));

    UpdateSmoothHint();

    if(plugin)
    {
        QString error = plugin->HotkeyError();
        hotkey_error->setText(error);
        hotkey_error->setVisible(!error.isEmpty());
    }

    loading = was_loading;

    FillMonitors();
    RefreshDevices();
    RefreshStatus();
}

void AmbilightTab::FillMonitors()
{
    if(!plugin)
    {
        return;
    }

    AmbilightSettings        s           = plugin->Store().Get();
    std::vector<MonitorInfo> list        = EnumerateMonitors();
    int                      pick        = -1;
    bool                     was_loading = loading;

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
    loading = was_loading;
}

void AmbilightTab::RefreshSettings()
{
    if(!plugin)
    {
        return;
    }

    AmbilightSettings s           = plugin->Store().Get();
    bool              was_loading = loading;

    loading = true;

    language->setCurrentIndex((int)s.language);
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

    loading = was_loading;
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
    QString                 devs  = FormatDeviceCount(count);
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

void AmbilightTab::OnLanguageChosen(int)
{
    if(loading || !plugin)
    {
        return;
    }

    UiLanguage chosen = (UiLanguage)language->currentData().toInt();

    SetUiLanguage(chosen);
    plugin->Store().Update([chosen](AmbilightSettings& s) { s.language = chosen; });
    plugin->SettingsEdited();

    RetranslateUi();
    plugin->OnLanguageChanged();
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
        s.language = (UiLanguage)language->currentData().toInt();

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
