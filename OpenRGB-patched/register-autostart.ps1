# Makes this copy of OpenRGB the one that starts at logon.
#
# Registers (or re-points) the "OpenRGB (elevated)" scheduled task at the
# OpenRGB.exe next to this script, elevated, so SMBus (the RGB RAM) works.
# Run it again after moving the folder. Asks for UAC once.
#
# It also removes the other ways an OpenRGB used to get started at logon:
# a Startup-folder shortcut (OpenRGB recreates one itself when its
# "start at login" setting is on) and the old OpenRGB Windows service.
$ErrorActionPreference = 'Stop'
$taskName = 'OpenRGB (elevated)'
$exe      = Join-Path $PSScriptRoot 'OpenRGB.exe'
$taskArgs = '--startminimized --server --noautoconnect'
$log      = Join-Path $env:TEMP 'openrgb-register-autostart.log'

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Start-Process powershell.exe -Verb RunAs -Wait -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    if (Test-Path $log) { Get-Content $log }
    return
}

Start-Transcript -Path $log -Force | Out-Null
try {
    if (-not (Test-Path $exe)) { throw "not found: $exe" }
    $user = "$env:USERDOMAIN\$env:USERNAME"

    $action    = New-ScheduledTaskAction -Execute $exe -Argument $taskArgs -WorkingDirectory $PSScriptRoot
    $trigger   = New-ScheduledTaskTrigger -AtLogOn -User $user
    $trigger.Delay = 'PT10S'
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
    $settings  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
                     -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -Priority 7
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal `
        -Settings $settings -Force | Out-Null
    "task '$taskName' -> $exe $taskArgs"

    # Startup-folder shortcuts that launch any OpenRGB.exe
    $shell = New-Object -ComObject WScript.Shell
    foreach ($dir in @([Environment]::GetFolderPath('Startup'), [Environment]::GetFolderPath('CommonStartup'))) {
        Get-ChildItem $dir -Filter *.lnk -ErrorAction SilentlyContinue | ForEach-Object {
            if ($shell.CreateShortcut($_.FullName).TargetPath -match 'OpenRGB\.exe$') {
                Remove-Item $_.FullName -Force
                "removed startup shortcut $($_.FullName)"
            }
        }
    }

    # The old OpenRGB service (C:\Program Files\OpenRGB) grabbed the devices first
    $svc = Get-Service -Name OpenRGB -ErrorAction SilentlyContinue
    if ($svc) {
        if ($svc.Status -ne 'Stopped') { Stop-Service OpenRGB -Force }
        Set-Service OpenRGB -StartupType Disabled
        "disabled service OpenRGB"
    }
}
finally {
    Stop-Transcript | Out-Null
}
