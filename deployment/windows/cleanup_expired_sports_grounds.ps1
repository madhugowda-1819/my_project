param(
    [string]$ProjectRoot = 'C:\Final_Year_Project\backend\my_project',
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $ProjectRoot
$logDirectory = Join-Path $ProjectRoot 'logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
& $Python manage.py cleanup_expired_sports_grounds *>> (Join-Path $logDirectory 'ground-cache-cleanup.log')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
