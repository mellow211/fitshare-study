[CmdletBinding()]
param(
    [string]$IPAddress,
    [ValidateRange(1, 65535)][int]$HttpPort = 8001,
    [ValidateRange(1, 65535)][int]$HttpsPort = 8443,
    [string]$DataDir = 'server-data\nanum-demo',
    [string]$PythonPath,
    [ValidateRange(1, 120)][int]$ReadyTimeoutSeconds = 20,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
if ($HttpPort -eq $HttpsPort) {
    throw 'HTTP 포트와 HTTPS 포트는 서로 달라야 합니다.'
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$resolvedDataDir = if ([System.IO.Path]::IsPathRooted($DataDir)) {
    [System.IO.Path]::GetFullPath($DataDir)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $DataDir))
}
$ipFile = Join-Path $resolvedDataDir 'fitshare-server.ip'

if (-not $IPAddress) {
    if (Test-Path -LiteralPath $ipFile) {
        $savedAddress = (Get-Content -LiteralPath $ipFile -Raw).Trim()
        $savedAddressIsActive = $false
        foreach ($networkAddress in (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue)) {
            if ($networkAddress.IPAddress -ne $savedAddress) { continue }
            $ipInterface = Get-NetIPInterface -AddressFamily IPv4 -InterfaceIndex $networkAddress.InterfaceIndex -ErrorAction SilentlyContinue
            if ($ipInterface.ConnectionState -eq 'Connected') {
                $savedAddressIsActive = $true
                break
            }
        }
        if (-not $savedAddressIsActive) {
            Write-Warning "저장된 FitShare IP 주소 $savedAddress 는 더 이상 활성 네트워크에 없습니다. 사용 가능한 사설 IPv4 주소를 자동으로 선택합니다."
            $IPAddress = $null
        } else {
            $IPAddress = $savedAddress
        }
    }
}

if (-not $IPAddress) {
    $candidates = @(
        foreach ($configuration in (Get-NetIPConfiguration | Where-Object { $_.NetAdapter.Status -eq 'Up' })) {
            foreach ($address in $configuration.IPv4Address) {
                $value = $address.IPAddress
                if ($value -match '^(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})$') {
                    [pscustomobject]@{
                        IPAddress = $value
                        HasDefaultGateway = [bool]$configuration.IPv4DefaultGateway
                        IsPreferred192 = $value -like '192.168.*'
                    }
                }
            }
        }
    )
    $address = $candidates |
        Sort-Object @{ Expression = { if ($_.HasDefaultGateway) { 0 } else { 1 } } },
                    @{ Expression = { if ($_.IsPreferred192) { 0 } else { 1 } } } |
        Select-Object -First 1
    if ($address) { $IPAddress = $address.IPAddress }
}
if (-not $IPAddress) {
    throw '활성 사설 IPv4 주소(10.x, 172.16~31.x, 192.168.x)를 찾지 못했어요. Wi-Fi 또는 이더넷에 연결하거나 -IPAddress 옵션에 주소를 지정해 주세요.'
}

$setupScript = Join-Path $PSScriptRoot 'setup_lan_https.ps1'
$setupParameters = @{
    IPAddress = $IPAddress
    DataDir = $resolvedDataDir
}
if ($Force) { $setupParameters.Force = $true }
& $setupScript @setupParameters
if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
    throw '로컬 네트워크 HTTPS 인증서를 준비하지 못했어요. 기존 서버는 중지하지 않았습니다.'
}

if (-not $PythonPath) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) { $PythonPath = $pythonCommand.Source }
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
    throw 'python.exe를 찾지 못했어요. -PythonPath 옵션에 실행 파일의 전체 경로를 지정해 주세요.'
}

function Get-PortOwnerIds([int]$Port) {
    return @(
        Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
}

$owners = @()
foreach ($port in @($HttpPort, $HttpsPort)) {
    foreach ($ownerId in (Get-PortOwnerIds $port)) {
        if ($owners -notcontains [int]$ownerId) { $owners += [int]$ownerId }
    }
}

$processesToStop = @()
$serverScript = Join-Path $projectRoot 'server.py'
$serverScriptPattern = '(?i)' + [regex]::Escape($serverScript)
$pidFile = Join-Path $resolvedDataDir 'fitshare-server.pid'
$managedPid = $null
if (Test-Path -LiteralPath $pidFile) {
    $pidText = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    $parsedPid = 0
    if ([int]::TryParse($pidText, [ref]$parsedPid)) { $managedPid = $parsedPid }
}
foreach ($ownerId in $owners) {
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerId"
    if (-not $owner) { continue }
    $isPythonServer = $owner.Name -in @('python.exe', 'pythonw.exe') -and
        $owner.CommandLine -match '(?i)(?:^|\s|\\)server\.py(?:"|\s|$)'
    $matchesProjectServer = $isPythonServer -and $owner.CommandLine -match $serverScriptPattern
    if (-not $isPythonServer -or ($managedPid -ne [int]$owner.ProcessId -and -not $matchesProjectServer)) {
        throw "포트 $port 을(를) PID $ownerId ($($owner.Name)) 프로세스가 사용 중이고 FitShare 서버가 아닙니다. 어떤 프로세스도 종료하지 않았어요."
    }
    $processesToStop += $owner
}

foreach ($owner in $processesToStop) {
    Stop-Process -Id $owner.ProcessId -Force
}
$stopDeadline = [DateTime]::UtcNow.AddSeconds(10)
while ([DateTime]::UtcNow -lt $stopDeadline) {
    $remaining = @()
    foreach ($port in @($HttpPort, $HttpsPort)) {
        $remaining += Get-PortOwnerIds $port
    }
    if (@($remaining | Where-Object { $_ -in $owners }).Count -eq 0) { break }
    Start-Sleep -Milliseconds 200
}
$stillRunning = @()
foreach ($port in @($HttpPort, $HttpsPort)) {
    $stillRunning += Get-PortOwnerIds $port
}
if (@($stillRunning | Where-Object { $_ -in $owners }).Count -gt 0) {
    throw '기존 FitShare 프로세스가 제한 시간 안에 포트를 해제하지 않았어요.'
}

New-Item -ItemType Directory -Path $resolvedDataDir -Force | Out-Null
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stdoutLog = Join-Path $resolvedDataDir "server-$timestamp.stdout.log"
$stderrLog = Join-Path $resolvedDataDir "server-$timestamp.stderr.log"

$environmentNames = @(
    'FITSHARE_HOST',
    'PORT',
    'FITSHARE_TLS_PORT',
    'FITSHARE_DATA_DIR',
    'FITSHARE_TLS_CERT_FILE',
    'FITSHARE_TLS_KEY_FILE'
)
$previousEnvironment = @{}
foreach ($name in $environmentNames) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
[Environment]::SetEnvironmentVariable('FITSHARE_HOST', '0.0.0.0', 'Process')
[Environment]::SetEnvironmentVariable('PORT', [string]$HttpPort, 'Process')
[Environment]::SetEnvironmentVariable('FITSHARE_TLS_PORT', [string]$HttpsPort, 'Process')
[Environment]::SetEnvironmentVariable('FITSHARE_DATA_DIR', $resolvedDataDir, 'Process')
[Environment]::SetEnvironmentVariable('FITSHARE_TLS_CERT_FILE', (Join-Path $resolvedDataDir 'certs\fitshare-demo-server.crt'), 'Process')
[Environment]::SetEnvironmentVariable('FITSHARE_TLS_KEY_FILE', (Join-Path $resolvedDataDir 'certs\fitshare-demo-server.key'), 'Process')

try {
    $newProcess = Start-Process -FilePath $PythonPath -ArgumentList @('-u', $serverScript) -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
} finally {
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process')
    }
}

$readyDeadline = [DateTime]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
$httpReady = $false
$httpsReady = $false
while ([DateTime]::UtcNow -lt $readyDeadline) {
    $newProcess.Refresh()
    if ($newProcess.HasExited) { break }
    $httpReady = (Get-PortOwnerIds $HttpPort) -contains $newProcess.Id
    $httpsReady = (Get-PortOwnerIds $HttpsPort) -contains $newProcess.Id
    if ($httpReady -and $httpsReady) { break }
    Start-Sleep -Milliseconds 250
}

if (-not $httpReady -or -not $httpsReady) {
    $newProcess.Refresh()
    if (-not $newProcess.HasExited) {
        $newOwner = Get-CimInstance Win32_Process -Filter "ProcessId = $($newProcess.Id)"
        if ($newOwner -and $newOwner.CommandLine -match '(?i)server\.py(?:"|\s|$)') {
            Stop-Process -Id $newProcess.Id -Force
        }
    }
    $logTail = @()
    if (Test-Path -LiteralPath $stderrLog) { $logTail = Get-Content -LiteralPath $stderrLog -Tail 20 }
    throw ("FitShare 서버가 두 포트에서 시작되지 않았어요. 로그를 확인해 주세요: {0}{1}{2}" -f $stderrLog, [Environment]::NewLine, ($logTail -join [Environment]::NewLine))
}

Write-Output "중지한 FitShare 프로세스 ID: $($owners -join ', ')"
Set-Content -LiteralPath $ipFile -Value $IPAddress -NoNewline
Set-Content -LiteralPath $pidFile -Value $newProcess.Id -NoNewline
Write-Output "FitShare 서버가 실행 중이에요. 프로세스 ID: $($newProcess.Id)"
Write-Output ("접속 주소 (HTTP): http://{0}:{1}/" -f $IPAddress, $HttpPort)
Write-Output ("접속 주소 (HTTPS): https://{0}:{1}/" -f $IPAddress, $HttpsPort)
Write-Output ("인증서 다운로드 주소: http://{0}:{1}/api/demo-ca" -f $IPAddress, $HttpPort)
Write-Output "서버 로그 파일: $stdoutLog 및 $stderrLog"
