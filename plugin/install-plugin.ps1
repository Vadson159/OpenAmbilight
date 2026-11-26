# Installs the freshly built Ambilight plugin into the portable OpenRGB and
# restarts OpenRGB through its logon task (the running instance is elevated).
#
#   powershell -ExecutionPolicy Bypass -File install-plugin.ps1
#
# Build first with build.sh. Rollback: delete OpenRGB-patched\plugins\
# OpenRGBAmbilightPlugin.dll and run schtasks /run /tn "OpenRGB (elevated)".
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$dll  = Join-Path $PSScriptRoot "build\release\OpenRGBAmbilightPlugin.dll"
$dst  = Join-Path $root "OpenRGB-patched\plugins"
$task = "OpenRGB (elevated)"

if(-not (Test-Path $dll)) { throw "no plugin build at $dll - run build.sh first" }

Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
for($i = 0; $i -lt 50 -and (Get-Process OpenRGB -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Milliseconds 100 }
if(Get-Process OpenRGB -ErrorAction SilentlyContinue) { throw "OpenRGB is still running; stop it from its tray icon first" }

New-Item -ItemType Directory -Force $dst | Out-Null
Copy-Item $dll $dst -Force
Write-Host "installed $(Join-Path $dst (Split-Path -Leaf $dll))"

Start-ScheduledTask -TaskName $task
Write-Host "started task '$task'"
