[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$logDirectory = Join-Path $projectRoot 'logs'
$logPath = Join-Path $logDirectory 'source-sync.log'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "项目虚拟环境 Python 不存在：$pythonPath。请先按 README 创建虚拟环境。"
}
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')] sync started" | Add-Content -LiteralPath $logPath -Encoding utf8
& $pythonPath -m scripts.sync_program_sources --all *>&1 | ForEach-Object {
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')] $_" | Add-Content -LiteralPath $logPath -Encoding utf8
}
$syncExitCode = $LASTEXITCODE
"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')] sync exit=$syncExitCode" | Add-Content -LiteralPath $logPath -Encoding utf8
exit $syncExitCode
