param(
    [string]$ProjectRoot = 'C:\Final_Year_Project\backend\my_project',
    [string]$TaskName = 'SportMate Ground Cache Cleanup'
)

$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $ProjectRoot 'deployment\windows\cleanup_expired_sports_grounds.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -ProjectRoot `"$ProjectRoot`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours(1) -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Description 'Hourly SportMate temporary live-ground cache cleanup.' -Force
