<#
Registers (or replaces) a per-user logon task that starts
scripts/windows/run_trading_worker.cmd. No administrator rights needed; it
only affects the current Windows user. Remove it with:
  Unregister-ScheduledTask -TaskName "AI System Trading - Trading Worker" -Confirm:$false
#>
$taskName = "AI System Trading - Trading Worker"
$runner = Join-Path $PSScriptRoot "run_trading_worker.cmd"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$runner`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Keeps the paper-trading worker running until the server exists." -Force
