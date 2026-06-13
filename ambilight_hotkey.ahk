; Ambilight for OpenRGB - Hotkey Toggle
; Ctrl+Alt+A = Toggle ambilight ON/OFF

#Requires AutoHotkey v2.0

global SCRIPT_PATH := A_ScriptDir "\ambilight.py"
global ambilightPID := 0

ClearTip() {
    ToolTip()
}

^!a:: {
    global ambilightPID, SCRIPT_PATH

    if (ambilightPID != 0) {
        ToolTip("Ambilight stopping...")
        try {
            RunWait('cmd.exe /c taskkill /F /PID ' ambilightPID ' /T', , "Hide")
        }
        RunWait('cmd.exe /c python "' SCRIPT_PATH '" --restore', A_ScriptDir, "Hide")
        ambilightPID := 0
        ToolTip("Ambilight OFF")
        SetTimer(ClearTip, -3000)
    } else {
        ToolTip("Ambilight starting...")
        Run('cmd.exe /c python "' SCRIPT_PATH '" --fps 15', A_ScriptDir, "Hide", &pid)
        ambilightPID := pid
        ToolTip("Ambilight ON")
        SetTimer(ClearTip, -3000)
    }
}

A_IconTip := "Ambilight (Ctrl+Alt+A)"
TraySetIcon("shell32.dll", 172)
