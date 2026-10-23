# 스마트폰 가상 입어보기 카메라 연결

스마트폰 브라우저는 `http://192.168...` 같은 LAN 주소를 보안 연결로 보지 않아 카메라 API를 막습니다. 로컬 데모용 인증서를 만들고 HTTP와 HTTPS를 같은 서버 프로세스로 실행하면 휴대폰에서 카메라를 사용할 수 있습니다.

## HTTPS 서버 준비

프로젝트 폴더의 PowerShell에서 현재 PC의 Wi-Fi IPv4 주소를 지정해 인증서를 만듭니다.

```powershell
powershell -ExecutionPolicy Bypass -File tools/setup_lan_https.ps1 -IPAddress 192.168.0.67
```

명령이 끝나면 PC 화면에 표시된 CA fingerprint (SHA-256) 값을 따로 적어 둡니다. 이 지문은 휴대폰에 설치할 인증서가 이 PC에서 만든 파일인지 확인하는 데 씁니다.

같은 PowerShell 창에서 기존 데모 데이터를 쓰며 서버를 실행합니다. HTTP 8001과 HTTPS 8443이 한 프로세스에서 실행되어 로그인, 사진, 실험 데이터가 동일하게 공유됩니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/restart_server.ps1 -IPAddress 192.168.0.67
```



For the first start, use the command above so the script records the server PID for safe restarts. If you previously started `python -u server.py` manually, stop it once with Ctrl+C in its original terminal, then use the script for future starts and restarts.

## 휴대폰에서 열기

1. 휴대폰을 PC와 같은 Wi-Fi에 연결합니다.
2. `http://192.168.0.67:8001/api/demo-ca`에서 `fitshare-demo-ca.crt`를 내려받습니다.
3. 인증서를 신뢰하도록 설치하기 전에 휴대폰의 인증서 정보 화면에서 SHA-256 fingerprint를 확인하고, PC 화면에 표시된 값과 정확히 같은지 비교합니다. 일치하지 않으면 설치하거나 신뢰하지 말고 인증서를 삭제하세요. 휴대폰에서 설치 전에 지문을 볼 수 없다면 CA 파일을 USB 케이블이나 AirDrop 같은 별도 신뢰 경로로 옮겨 인증서 뷰어에서 먼저 확인합니다.
4. 지문이 일치한 인증서만 CA 인증서로 설치하고 신뢰합니다. Android에서는 보통 `설정 > 보안 및 개인정보 보호 > 기타 보안 설정 > 인증서 설치 > CA 인증서`를 사용합니다. iPhone에서는 프로파일 설치 후 `설정 > 일반 > 정보 > 인증서 신뢰 설정`에서 완전 신뢰를 켭니다.
5. `https://192.168.0.67:8443/`를 열고 카메라 권한을 허용합니다.

데모가 끝나면 휴대폰 인증서 설정에서 `FitShare Demo Local CA`를 제거하세요. 이 인증기관은 이 로컬 데모 서버만을 위한 것입니다. PC의 IP 주소가 바뀌면 인증서를 다시 만들고 휴대폰에도 다시 설치해야 합니다. 인증서 개인 키는 `server-data/` 안에만 저장되고 공개 다운로드 경로는 공개 CA 인증서만 제공합니다.


## 서버 시작 또는 재시작

프로젝트 폴더의 `FitShare 서버 재시작.bat`을 더블클릭하면 기본 데모 서버를 한 번에 재시작합니다. 자동 검색은 게이트웨이가 연결된 10.x, 172.16–31.x, 192.168.x 사설 IP를 우선 사용합니다. 창에 접속 주소와 로그 위치가 나오고, 성공 여부를 확인할 수 있도록 창이 열린 채로 기다립니다. IP를 찾지 못하면 아래 PowerShell 명령처럼 `-IPAddress`를 지정하세요.

프로젝트 폴더는 사용자 PATH에도 등록되어 있어, 새 터미널에서는 어느 위치에서든 `fitshare-restart.cmd` 명령으로 실행할 수 있습니다. PATH 설정은 이미 열린 터미널에는 자동 반영되지 않으므로 새 터미널을 열어 사용하세요.

프로젝트 폴더의 PowerShell에서 다음 명령을 실행합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/restart_server.ps1 -IPAddress 192.168.0.67
```

스크립트는 기본 포트 8001과 8443에서 실행 중인 FitShare `server.py` 프로세스만 종료한 뒤, 같은 `server-data\\nanum-demo`와 인증서로 숨김 실행합니다. 두 포트를 다른 프로그램이 사용 중이면 그 프로그램을 종료하지 않고 오류로 멈춥니다. 실행한 PID와 주소를 출력하고 서버 로그는 `server-data\\nanum-demo`에 기록합니다.

PC의 IP 주소가 바뀌어 기존 인증서가 맞지 않으면 새 IP로 인증서를 다시 만들고 휴대폰에 새 CA를 설치해야 합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/setup_lan_https.ps1 -IPAddress 192.168.0.XX -Force
powershell -NoProfile -ExecutionPolicy Bypass -File tools/restart_server.ps1 -IPAddress 192.168.0.XX
```
