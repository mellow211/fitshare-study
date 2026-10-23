import http.client
import json
import ssl
import socket
import tempfile
import threading
import unittest
from pathlib import Path

import server


FIXTURES = Path(__file__).parent / 'fixtures' / 'https'


class HttpsServerTests(unittest.TestCase):
    def make_tls_server(self, data_dir):
        httpd = server.create_server(
            '127.0.0.1', 0, data_dir,
            tls_certfile=FIXTURES / 'test-server.crt',
            tls_keyfile=FIXTURES / 'test-server.key',
            tls_port=0,
        )
        http_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        tls_server = httpd.tls_server
        tls_thread = threading.Thread(target=tls_server.serve_forever, daemon=True)
        http_thread.start()
        tls_thread.start()
        self.addCleanup(tls_thread.join, 2)
        self.addCleanup(tls_server.server_close)
        self.addCleanup(tls_server.shutdown)
        self.addCleanup(http_thread.join, 2)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd

    def test_https_server_serves_the_existing_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            httpd = self.make_tls_server(Path(temp_dir))
            connection = http.client.HTTPSConnection(
                '127.0.0.1', httpd.tls_server.server_address[1],
                context=ssl.create_default_context(cafile=str(FIXTURES / 'test-server.crt')), timeout=3,
            )
            self.addCleanup(connection.close)

            connection.request('GET', '/')
            response = connection.getresponse()

            self.assertEqual(response.status, 200)
            self.assertIn(b'<!doctype html>', response.read()[:256])

    def test_http_and_https_listeners_share_experiment_storage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            httpd = self.make_tls_server(Path(temp_dir))
            http_connection = http.client.HTTPConnection('127.0.0.1', httpd.server_address[1], timeout=3)
            self.addCleanup(http_connection.close)
            payload = (
                b'{"garments":[{"id":"garment-1","name":"Test top","category":"top",'
                b'"referenceLengthCm":50,"referenceWidthCm":40}]}'
            )
            http_connection.request('POST', '/api/experiments', body=payload,
                                    headers={'Content-Type': 'application/json'})
            create_response = http_connection.getresponse()
            experiment = create_response.read()
            self.assertEqual(create_response.status, 201)
            experiment_id = json.loads(experiment)['id']

            tls_connection = http.client.HTTPSConnection(
                '127.0.0.1', httpd.tls_server.server_address[1],
                context=ssl.create_default_context(cafile=str(FIXTURES / 'test-server.crt')), timeout=3,
            )
            self.addCleanup(tls_connection.close)
            tls_connection.request('GET', f'/api/experiments/{experiment_id}')
            detail_response = tls_connection.getresponse()

            self.assertEqual(detail_response.status, 200)
            self.assertEqual(json.loads(detail_response.read())['round'], 1)

    def test_https_rejects_a_hostname_that_does_not_match_the_certificate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            httpd = self.make_tls_server(Path(temp_dir))
            context = ssl.create_default_context(cafile=str(FIXTURES / 'test-server.crt'))
            connection = socket.create_connection(('127.0.0.1', httpd.tls_server.server_address[1]), timeout=3)
            with self.assertRaises(ssl.SSLCertVerificationError):
                context.wrap_socket(connection, server_hostname='127.0.0.2')

    def test_https_server_requires_both_certificate_and_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, 'certificate and key'):
                server.create_server(
                    '127.0.0.1', 0, Path(temp_dir),
                    tls_certfile=FIXTURES / 'test-server.crt',
                )

    def test_http_server_exposes_only_the_public_demo_ca_certificate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ca_dir = Path(temp_dir) / 'certs'
            ca_dir.mkdir()
            public_certificate = b'-----BEGIN CERTIFICATE-----\ntest public CA\n'
            (ca_dir / 'fitshare-demo-ca.crt').write_bytes(public_certificate)
            (ca_dir / 'fitshare-demo-ca.key').write_bytes(b'-----BEGIN PRIVATE KEY-----\nsecret\n')
            httpd = server.create_server('127.0.0.1', 0, Path(temp_dir))
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 2)
            self.addCleanup(httpd.server_close)
            self.addCleanup(httpd.shutdown)
            connection = http.client.HTTPConnection('127.0.0.1', httpd.server_address[1], timeout=3)
            self.addCleanup(connection.close)

            connection.request('GET', '/api/demo-ca')
            response = connection.getresponse()

            self.assertEqual(response.status, 200)
            downloaded_certificate = response.read()
            self.assertEqual(downloaded_certificate, public_certificate)
            self.assertNotIn(b'PRIVATE KEY', downloaded_certificate)
            self.assertIn('application/x-x509-ca-cert', response.getheader('Content-Type'))

    def test_demo_ca_route_does_not_expose_private_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            httpd = server.create_server('127.0.0.1', 0, Path(temp_dir))
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 2)
            self.addCleanup(httpd.server_close)
            self.addCleanup(httpd.shutdown)
            connection = http.client.HTTPConnection('127.0.0.1', httpd.server_address[1], timeout=3)
            self.addCleanup(connection.close)

            connection.request('GET', '/api/demo-ca')
            response = connection.getresponse()

            self.assertEqual(response.status, 404)


if __name__ == '__main__':
    unittest.main()
