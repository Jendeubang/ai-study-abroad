[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$taskName = 'StudyCompassOfficialSync'
$timeZone = Get-TimeZone
if ($timeZone.Id -ne 'China Standard Time') {
    throw "计划任务按上海时间 08:00 运行；请先将 Windows 时区设为 China Standard Time（当前：$($timeZone.Id)）。"
}
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runnerPath = Join-Path $PSScriptRoot 'run_weekly_sync.ps1'
if (-not (Test-Path -LiteralPath $runnerPath -PathType Leaf)) {
    throw "同步运行脚本不存在：$runnerPath"
}

$powershellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$action = New-ScheduledTaskAction -Execute $powershellPath -Argument "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runnerPath`"" -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At ([datetime]::ParseExact('08:00', 'HH:mm', [Globalization.CultureInfo]::InvariantCulture))
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description '每周一 08:00 按已登记权限采集官网招生字段，并将变化送交人工审核。' -Force | Out-Null
Write-Output "已为当前用户注册每周一 08:00 同步任务：$taskName"
Write-Output "项目目录：$projectRoot"
Write-Output '修改或删除任务：运行 scripts/remove_weekly_sync_task.ps1'
