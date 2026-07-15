# Registers a Windows scheduled task that runs `luxtock daily` once per day.
# Run manually from an elevated-or-normal PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\register_daily_task.ps1
# Default 09:00 local — after the US close for an Asia-timezone desk; pass
# -Time to change. Logs append to output\daily-task.log.
param(
    [string]$Time = "09:00",
    [string]$TaskName = "LuxtockDaily"
)

$repo = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $repo "output"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir "daily-task.log"

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
    "-NoProfile -WindowStyle Hidden -Command " +
    "`"Set-Location '$repo'; luxtock daily *>> '$log'`""
)
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Force | Out-Null
Write-Host "Registered '$TaskName': runs 'luxtock daily' daily at $Time (log: $log)"
Write-Host "Remove with: Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
