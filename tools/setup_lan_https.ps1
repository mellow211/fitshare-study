param(
    [string]$IPAddress,
    [string]$DataDir = 'server-data\nanum-demo',
    [string]$OpenSslPath,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$storage = if ([System.IO.Path]::IsPathRooted($DataDir)) {
    [System.IO.Path]::GetFullPath($DataDir)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $DataDir))
}
$certificateDirectory = Join-Path $storage 'certs'
$caKey = Join-Path $certificateDirectory 'fitshare-demo-ca.key'
$caCertificate = Join-Path $certificateDirectory 'fitshare-demo-ca.crt'
$serverKey = Join-Path $certificateDirectory 'fitshare-demo-server.key'
$serverCertificate = Join-Path $certificateDirectory 'fitshare-demo-server.crt'

if (-not $IPAddress) {
    $adapterAddress = Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -like '192.168.*' -and $_.PrefixOrigin -ne 'WellKnown' } |
        Select-Object -First 1
    if ($adapterAddress) { $IPAddress = $adapterAddress.IPAddress }
}
$parsedAddress = $null
if (-not [System.Net.IPAddress]::TryParse($IPAddress, [ref]$parsedAddress) -or
    $parsedAddress.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
    throw '-IPAddress 옵션에 올바른 IPv4 로컬 네트워크 주소를 지정해 주세요.'
}

if (-not $OpenSslPath) {
    $opensslCandidates = @(
        (Join-Path $env:ProgramFiles 'Git\usr\bin\openssl.exe'),
        (Join-Path $env:ProgramFiles 'Git\mingw64\bin\openssl.exe')
    )
    $OpenSslPath = $opensslCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $OpenSslPath) {
        $opensslCommand = Get-Command openssl.exe -ErrorAction SilentlyContinue
        if ($opensslCommand) { $OpenSslPath = $opensslCommand.Source }
    }
}
if (-not $OpenSslPath -or -not (Test-Path -LiteralPath $OpenSslPath)) {
    throw 'OpenSSL을 찾지 못했어요. Git for Windows에 포함된 openssl.exe를 사용할 수 있습니다.'
}

function Invoke-OpenSsl([string[]]$Arguments) {
    & $OpenSslPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "OpenSSL command failed: openssl $($Arguments -join ' ')"
    }
}

function New-DemoServerCertificate([string]$Address) {
    $serverCsr = Join-Path $certificateDirectory 'fitshare-demo-server.csr'
    $extensionsPath = Join-Path $certificateDirectory 'fitshare-demo-extensions.cnf'
    Invoke-OpenSsl @(
        'req', '-new', '-newkey', 'rsa:2048', '-nodes',
        '-keyout', $serverKey,
        '-out', $serverCsr,
        '-subj', "/CN=$Address"
    )

    @"
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=IP:$Address
authorityKeyIdentifier=keyid,issuer
"@ | Set-Content -LiteralPath $extensionsPath -Encoding ASCII

    Invoke-OpenSsl @(
        'x509', '-req',
        '-in', $serverCsr,
        '-CA', $caCertificate, '-CAkey', $caKey, '-CAcreateserial',
        '-out', $serverCertificate, '-days', '365', '-sha256',
        '-extfile', $extensionsPath
    )
    Remove-Item -LiteralPath $serverCsr, $extensionsPath -Force
    $serial = Join-Path $certificateDirectory 'fitshare-demo-ca.srl'
    if (Test-Path -LiteralPath $serial) { Remove-Item -LiteralPath $serial -Force }
}

New-Item -ItemType Directory -Path $certificateDirectory -Force | Out-Null
$existingFiles = @($caKey, $caCertificate, $serverKey, $serverCertificate) |
    Where-Object { Test-Path -LiteralPath $_ }
if ($existingFiles.Count -eq 4 -and -not $Force) {
    & $OpenSslPath x509 -in $serverCertificate -checkip $IPAddress -noout 2>$null
    if ($LASTEXITCODE -eq 0) {
        $fingerprint = (& $OpenSslPath x509 -in $caCertificate -noout -fingerprint -sha256) -join ''
        Write-Output "로컬 IP 주소: $IPAddress"
        Write-Output "HTTPS 서버 인증서: $serverCertificate"
        Write-Output "HTTPS 서버 개인 키: $serverKey"
        Write-Output ("휴대폰 인증서 다운로드: http://{0}:8001/api/demo-ca" -f $IPAddress)
        Write-Output ("FitShare HTTPS 주소: https://{0}:8443/" -f $IPAddress)
        Write-Output "인증서 지문: $fingerprint"
        Write-Output '인증서를 신뢰하기 전에 휴대폰에 표시된 SHA-256 지문이 위 값과 같은지 확인해 주세요.'
        return
    }
    Write-Output "IP 주소 $IPAddress 가 기존 서버 인증서와 달라 서버 인증서만 새로 만들고 데모 인증서는 유지합니다."
    New-DemoServerCertificate $IPAddress
    $fingerprint = (& $OpenSslPath x509 -in $caCertificate -noout -fingerprint -sha256) -join ''
    Write-Output "로컬 IP 주소: $IPAddress"
    Write-Output "HTTPS 서버 인증서: $serverCertificate"
    Write-Output "HTTPS 서버 개인 키: $serverKey"
    Write-Output ("휴대폰 인증서 다운로드: http://{0}:8001/api/demo-ca" -f $IPAddress)
    Write-Output ("FitShare HTTPS 주소: https://{0}:8443/" -f $IPAddress)
    Write-Output "인증서 지문: $fingerprint"
    Write-Output '기존 데모 인증서를 유지했으므로, 이미 휴대폰에 설치한 인증서는 다시 설치하지 않아도 됩니다.'
    return
}
if ($existingFiles.Count -gt 0 -and -not $Force) {
    throw '일부 데모 인증서 파일이 이미 있습니다. 목록의 인증서 파일만 교체하려면 -Force 옵션을 붙여 다시 실행해 주세요.'
}
if ($Force) {
    if ($existingFiles.Count -gt 0) {
        Write-Warning '강제 실행은 데모 인증서와 서버 인증서를 교체합니다. HTTPS 카메라를 사용하는 휴대폰에는 내려받은 인증서를 다시 설치해야 합니다.'
    }
    @($caKey, $caCertificate, $serverKey, $serverCertificate,
      (Join-Path $certificateDirectory 'fitshare-demo-server.csr'),
      (Join-Path $certificateDirectory 'fitshare-demo-extensions.cnf'),
      (Join-Path $certificateDirectory 'fitshare-demo-ca.srl')) |
        Where-Object { Test-Path -LiteralPath $_ } |
        ForEach-Object { Remove-Item -LiteralPath $_ -Force }
}

Invoke-OpenSsl @(
    'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
    '-keyout', $caKey, '-out', $caCertificate, '-days', '3650', '-sha256',
    '-subj', '/CN=FitShare Demo Local CA',
    '-addext', 'basicConstraints=critical,CA:TRUE',
    '-addext', 'keyUsage=critical,keyCertSign,cRLSign'
)
New-DemoServerCertificate $IPAddress

$fingerprint = (& $OpenSslPath x509 -in $caCertificate -noout -fingerprint -sha256) -join ''
Write-Output "로컬 IP 주소: $IPAddress"
Write-Output "HTTPS 서버 인증서: $serverCertificate"
Write-Output "HTTPS 서버 개인 키: $serverKey"
Write-Output ("휴대폰 인증서 다운로드: http://{0}:8001/api/demo-ca" -f $IPAddress)
Write-Output ("FitShare HTTPS 주소: https://{0}:8443/" -f $IPAddress)
Write-Output "인증서 지문: $fingerprint"
Write-Output '인증서를 신뢰하기 전에 휴대폰에 표시된 SHA-256 지문이 위 값과 같은지 확인해 주세요.'
