import http.client
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESTART_SCRIPT = ROOT / 'tools' / 'restart_server.ps1'
SETUP_SCRIPT = ROOT / 'tools' / 'setup_lan_https.ps1'
ONE_CLICK_LAUNCHER = ROOT / 'FitShare 서버 재시작.bat'
PATH_COMMAND = ROOT / 'fitshare-restart.cmd'


class RestartLauncherTests(unittest.TestCase):
    def test_path_command_dispatches_to_the_one_click_launcher(self):
        command = PATH_COMMAND.read_text(encoding='ascii')

        self.assertIn('chcp 65001 >nul', command)
        self.assertIn('"%~dp0tools\\restart_server_launcher.ps1"', command)
        self.assertIn('%*', command)
        self.assertIn('pause', command.lower())

    def test_launcher_uses_utf8_console_and_korean_status_messages(self):
        helper = (ROOT / 'tools' / 'restart_server_launcher.ps1').read_text(encoding='utf-8-sig')
        self.assertIn('[Console]::OutputEncoding', helper)
        self.assertIn('FitShare 서버를 재시작하고 있어요', helper)
        self.assertIn('FitShare 서버가 실행 중이에요', helper)
        self.assertIn('FitShare 서버를 다시 시작하지 못했어요', helper)

    def test_restart_script_prints_urls_and_completion_in_korean(self):
        script = RESTART_SCRIPT.read_text(encoding='utf-8-sig')
        self.assertIn('FitShare 서버가 실행 중이에요', script)
        self.assertIn('접속 주소 (HTTP)', script)
        self.assertIn('접속 주소 (HTTPS)', script)

    def test_one_click_launcher_calls_the_managed_restart_script_and_keeps_window_open(self):
        launcher = ONE_CLICK_LAUNCHER.read_text(encoding='ascii')

        self.assertIn('call "%~dp0fitshare-restart.cmd"', launcher)

    def test_restart_script_detects_private_ipv4_ranges_on_up_adapters(self):
        script = RESTART_SCRIPT.read_text(encoding='utf-8')

        self.assertIn('Get-NetIPConfiguration', script)
        self.assertIn("$_.NetAdapter.Status -eq 'Up'", script)
        self.assertIn('172\\.(1[6-9]|2\\d|3[01])', script)
        self.assertIn('HasDefaultGateway', script)

    def test_restart_script_passes_force_to_certificate_setup_when_requested(self):
        script = RESTART_SCRIPT.read_text(encoding='utf-8')

        self.assertIn('[switch]$Force', script)
        self.assertIn('IPAddress = $IPAddress', script)
        self.assertIn('DataDir = $resolvedDataDir', script)
        self.assertIn('if ($Force) { $setupParameters.Force = $true }', script)

    def test_restart_script_reuses_the_last_successful_ip(self):
        script = RESTART_SCRIPT.read_text(encoding='utf-8')

        self.assertIn("$ipFile = Join-Path $resolvedDataDir 'fitshare-server.ip'", script)
        self.assertIn('Get-Content -LiteralPath $ipFile', script)
        self.assertIn('Get-NetIPAddress -AddressFamily IPv4', script)
        self.assertIn("$ipInterface.ConnectionState -eq 'Connected'", script)
        self.assertIn('Set-Content -LiteralPath $ipFile -Value $IPAddress', script)

    def test_restart_script_falls_back_to_active_private_ip_when_saved_ip_is_stale(self):
        script = RESTART_SCRIPT.read_text(encoding='utf-8')

        stale_ip_check = script.index('if (-not $savedAddressIsActive)')
        auto_select = script.index('if (-not $IPAddress) {', stale_ip_check)
        stale_ip_branch = script[stale_ip_check:auto_select]

        self.assertIn('$IPAddress = $null', stale_ip_branch)
        self.assertNotIn('throw ', stale_ip_branch)
        self.assertIn('Get-NetIPConfiguration', script[auto_select:])
        self.assertIn('10\\.\\d{1,3}', script)
        self.assertIn('172\\.(1[6-9]|2\\d|3[01])', script)
        self.assertIn('192\\.168\\.', script)

    def test_restart_script_can_identify_this_projects_server_when_pid_file_is_stale(self):
        script = RESTART_SCRIPT.read_text(encoding='utf-8')

        self.assertIn("$serverScript = Join-Path $projectRoot 'server.py'", script)
        self.assertIn('$matchesProjectServer', script)


@unittest.skipUnless(os.name == 'nt', 'The restart script controls Windows processes.')
class RestartServerScriptTests(unittest.TestCase):
    def free_port(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            return sock.getsockname()[1]

    def powershell(self, *args):
        executable = shutil.which('powershell.exe') or shutil.which('powershell')
        self.assertIsNotNone(executable, 'Windows PowerShell is required for this integration test.')
        return subprocess.run(
            [executable, '-NoProfile', '-ExecutionPolicy', 'Bypass', *map(str, args)],
            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=40,
        )

    def create_certificate(self, data_dir):
        result = self.powershell('-File', SETUP_SCRIPT, '-IPAddress', '127.0.0.1', '-DataDir', data_dir)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return data_dir / 'certs' / 'fitshare-demo-ca.crt'

    def test_setup_refreshes_server_certificate_for_new_ip_and_preserves_demo_ca(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            ca_path = self.create_certificate(data_dir)
            original_ca = ca_path.read_bytes()
            server_certificate = data_dir / 'certs' / 'fitshare-demo-server.crt'
            program_files = Path(os.environ.get('ProgramFiles', r'C:\Program Files'))
            openssl_candidates = (
                program_files / 'Git' / 'usr' / 'bin' / 'openssl.exe',
                program_files / 'Git' / 'mingw64' / 'bin' / 'openssl.exe',
            )
            openssl = next((str(path) for path in openssl_candidates if path.exists()), None)
            openssl = openssl or shutil.which('openssl.exe') or shutil.which('openssl')
            self.assertIsNotNone(openssl, 'OpenSSL is required to inspect demo certificates.')

            result = self.powershell(
                '-File', SETUP_SCRIPT, '-IPAddress', '127.0.0.2', '-DataDir', data_dir
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(ca_path.read_bytes(), original_ca)
            self.assertEqual(
                subprocess.run(
                    [openssl, 'x509', '-in', server_certificate, '-checkip', '127.0.0.2', '-noout'],
                    capture_output=True, text=True,
                ).returncode,
                0,
            )
            self.assertEqual(
                subprocess.run(
                    [openssl, 'verify', '-CAfile', ca_path, server_certificate],
                    capture_output=True, text=True,
                ).returncode,
                0,
            )

    def start_fitshare(self, data_dir, http_port, https_port):
        env = os.environ.copy()
        env.update({
            'PORT': str(http_port),
            'FITSHARE_TLS_PORT': str(https_port),
            'FITSHARE_DATA_DIR': str(data_dir),
            'FITSHARE_TLS_CERT_FILE': str(data_dir / 'certs' / 'fitshare-demo-server.crt'),
            'FITSHARE_TLS_KEY_FILE': str(data_dir / 'certs' / 'fitshare-demo-server.key'),
        })
        process = subprocess.Popen(
            [sys.executable, '-u', str(ROOT / 'server.py')], cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self.stop_pid, process.pid)
        return process

    def stop_pid(self, pid):
        result = self.powershell('-Command', (
            f'$p=Get-CimInstance Win32_Process -Filter "ProcessId={pid}"; '
            'if ($p -and $p.Name -eq \'python.exe\' -and $p.CommandLine -match \'server\\.py\') '
            f'{{ Stop-Process -Id {pid} -Force }}'
        ))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def wait_for_http(self, port, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
                conn.request('GET', '/')
                response = conn.getresponse()
                response.read()
                conn.close()
                if response.status == 200:
                    return
            except OSError:
                time.sleep(0.2)
        self.fail(f'FitShare did not serve HTTP on port {port}.')

    def test_running_fitshare_is_restarted_on_both_ports(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        data_dir = Path(folder.name)
        ca_file = self.create_certificate(data_dir)
        original_ca = ca_file.read_bytes()
        http_port, https_port = self.free_port(), self.free_port()
        old = self.start_fitshare(data_dir, http_port, https_port)
        self.wait_for_http(http_port)
        (data_dir / 'fitshare-server.pid').write_text(str(old.pid), encoding='ascii')

        executable = shutil.which('powershell.exe') or shutil.which('powershell')
        result = subprocess.run(
            [executable, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(RESTART_SCRIPT),
             '-IPAddress', '127.0.0.2', '-HttpPort', str(http_port), '-HttpsPort', str(https_port),
             '-DataDir', str(data_dir), '-PythonPath', sys.executable],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=40,
        )

        self.assertEqual(result.returncode, 0)
        new_pid = int((data_dir / 'fitshare-server.pid').read_text(encoding='ascii'))
        self.addCleanup(self.stop_pid, new_pid)
        self.assertNotEqual(new_pid, old.pid)
        self.assertEqual(ca_file.read_bytes(), original_ca)
        old.wait(timeout=5)
        self.wait_for_http(http_port)
        context = ssl.create_default_context(cafile=str(ca_file))
        connection = http.client.HTTPSConnection('127.0.0.2', https_port, context=context, timeout=3)
        connection.request('GET', '/api/auth/me')
        response = connection.getresponse()
        response.read()
        connection.close()
        self.assertEqual(response.status, 200)

    def test_stale_pid_file_reuses_pinned_ip_and_restarts_this_projects_listener(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        data_dir = Path(folder.name)
        ca_file = self.create_certificate(data_dir)
        ip_file = data_dir / 'fitshare-server.ip'
        ip_file.write_text('127.0.0.1', encoding='ascii')
        http_port, https_port = self.free_port(), self.free_port()
        old = self.start_fitshare(data_dir, http_port, https_port)
        self.wait_for_http(http_port)
        (data_dir / 'fitshare-server.pid').write_text('99999999', encoding='ascii')

        executable = shutil.which('powershell.exe') or shutil.which('powershell')
        result = subprocess.run(
            [executable, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(RESTART_SCRIPT),
             '-HttpPort', str(http_port), '-HttpsPort', str(https_port),
             '-DataDir', str(data_dir), '-PythonPath', sys.executable],
            cwd=ROOT, capture_output=True, text=True, timeout=40,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(ip_file.read_text(encoding='ascii'), '127.0.0.1')
        new_pid = int((data_dir / 'fitshare-server.pid').read_text(encoding='ascii'))
        self.addCleanup(self.stop_pid, new_pid)
        self.assertNotEqual(new_pid, old.pid)
        old.wait(timeout=5)
        self.wait_for_http(http_port)
        context = ssl.create_default_context(cafile=str(ca_file))
        connection = http.client.HTTPSConnection('127.0.0.1', https_port, context=context, timeout=3)
        connection.request('GET', '/api/auth/me')
        response = connection.getresponse()
        response.read()
        connection.close()
        self.assertEqual(response.status, 200)

    def test_equal_http_and_https_ports_are_rejected(self):
        executable = shutil.which('powershell.exe') or shutil.which('powershell')
        result = subprocess.run(
            [executable, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(RESTART_SCRIPT),
             '-IPAddress', '127.0.0.1', '-HttpPort', '8455', '-HttpsPort', '8455'],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_unmanaged_server_py_listener_is_not_terminated(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        data_dir = Path(folder.name)
        self.create_certificate(data_dir)
        http_port, https_port = self.free_port(), self.free_port()
        foreign_script = Path(folder.name) / 'server.py'
        foreign_script.write_text(
            'from http.server import HTTPServer, SimpleHTTPRequestHandler\n'
            f'HTTPServer(("0.0.0.0", {http_port}), SimpleHTTPRequestHandler).serve_forever()\n',
            encoding='utf-8',
        )
        unrelated = subprocess.Popen(
            [sys.executable, str(foreign_script)], cwd=folder.name,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        def stop_unrelated():
            if unrelated.poll() is None:
                unrelated.terminate()
                unrelated.wait(timeout=5)
        self.addCleanup(stop_unrelated)
        self.wait_for_http(http_port)

        executable = shutil.which('powershell.exe') or shutil.which('powershell')
        result = subprocess.run(
            [executable, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(RESTART_SCRIPT),
             '-IPAddress', '127.0.0.1', '-HttpPort', str(http_port), '-HttpsPort', str(https_port),
             '-DataDir', str(data_dir), '-PythonPath', sys.executable],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=40,
        )

        pid_file = data_dir / 'fitshare-server.pid'
        if pid_file.exists():
            self.addCleanup(self.stop_pid, int(pid_file.read_text(encoding='ascii')))
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(unrelated.poll())
        self.wait_for_http(http_port)


if __name__ == '__main__':
    unittest.main()
