[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$taskName = 'StudyCompassOfficialSync'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Output "计划任务不存在：$taskName"
    exit 0
}

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
Write-Output "已删除计划任务：$taskName"
