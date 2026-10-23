$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$restartScript = Join-Path $PSScriptRoot 'restart_server.ps1'
try {
    Write-Output 'FitShare 서버를 재시작하고 있어요...'
    & $restartScript @args
    Write-Output 'FitShare 서버가 실행 중이에요.'
} catch {
    [Console]::Error.WriteLine(('FitShare 서버를 다시 시작하지 못했어요. {0}' -f $_.Exception.Message))
    exit 1
}
