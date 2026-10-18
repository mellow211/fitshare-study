import http.cookiejar
import importlib.util
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from tools import vercel_storage
from tools.vercel_storage import SyncedFolder


class FakeRedis:
    """Just the Redis commands SyncedFolder uses, kept in memory."""

    def __init__(self):
        self.values = {}
        self.hashes = {}
        self.calls = 0

    def pipeline(self, commands):
        self.calls += 1
        return [self.run(*command) for command in commands]

    def command(self, *args):
        return self.pipeline([list(args)])[0]

    def run(self, name, *args):
        if name == 'GET':
            return self.values.get(args[0])
        if name == 'SET':
            if 'NX' in args and args[0] in self.values:
                return None
            self.values[args[0]] = args[1]
            return 'OK'
        if name == 'DEL':
            return int(self.values.pop(args[0], None) is not None)
        if name == 'HGETALL':
            flat = []
            for key, value in self.hashes.get(args[0], {}).items():
                flat += [key, value]
            return flat
        if name == 'EVAL':
            script, _, *rest = args
            if script == vercel_storage.CAS_SET_SCRIPT:
                manifest, file_key, rel, expected, entry, text = rest
                current = self.hashes.setdefault(manifest, {}).get(rel)
                if (current is None and expected == '') or current == expected:
                    if text:
                        self.values[file_key] = text
                    self.hashes[manifest][rel] = entry
                    return 1
                return 0
            if script == vercel_storage.CAS_DELETE_SCRIPT:
                manifest, file_key, rel, expected = rest
                if self.hashes.setdefault(manifest, {}).get(rel) == expected:
                    del self.hashes[manifest][rel]
                    self.values.pop(file_key, None)
                    return 1
                return 0
            if script == vercel_storage.RELEASE_LOCK_SCRIPT:
                key, token = rest
                if self.values.get(key) == token:
                    del self.values[key]
                    return 1
                return 0
        raise AssertionError(f'unexpected command {name}')


class FakeBlob:
    def __init__(self):
        self.files = {}

    def put(self, pathname, data, content_type):
        url = f'https://store.private.blob.vercel-storage.com/{pathname}'
        self.files[url] = data
        return url

    def get(self, url):
        return self.files[url]

    def delete(self, urls):
        for url in urls:
            self.files.pop(url, None)


class SyncedFolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.redis = FakeRedis()
        self.blob = FakeBlob()
        self.a = SyncedFolder(Path(self.temp.name) / 'a', self.redis, self.blob)
        self.b = SyncedFolder(Path(self.temp.name) / 'b', self.redis, self.blob)

    def tearDown(self):
        self.temp.cleanup()

    def test_json_goes_to_redis_and_photo_to_blob_then_other_instance_reads_them(self):
        (self.a.root / 'garments' / 'g1').mkdir(parents=True)
        (self.a.root / 'garments' / 'g1' / 'garment.json').write_text('{"name": "셔츠"}', encoding='utf-8')
        (self.a.root / 'garments' / 'g1' / 'photo.jpg').write_bytes(b'\xff\xd8\xffphoto')
        self.assertEqual(self.a.push()['uploaded'], 2)
        self.assertEqual(len(self.blob.files), 1)
        self.assertIn('fitshare:file:garments/g1/garment.json', self.redis.values)

        self.b.pull()
        self.assertEqual((self.b.root / 'garments' / 'g1' / 'garment.json').read_text(encoding='utf-8'), '{"name": "셔츠"}')
        self.assertEqual((self.b.root / 'garments' / 'g1' / 'photo.jpg').read_bytes(), b'\xff\xd8\xffphoto')

    def test_overwrite_replaces_old_blob_and_delete_reaches_other_instance(self):
        photo = self.a.root / 'photo.png'
        photo.write_bytes(b'one')
        self.a.push()
        self.b.pull()
        photo.write_bytes(b'two!')
        self.a.push()
        self.assertEqual(list(self.blob.files.values()), [b'two!'])
        self.b.pull()
        self.assertEqual((self.b.root / 'photo.png').read_bytes(), b'two!')

        photo.unlink()
        self.assertEqual(self.a.push()['removed'], 1)
        self.b.pull()
        self.assertFalse((self.b.root / 'photo.png').exists())
        self.assertEqual(self.blob.files, {})

    def test_stale_instance_does_not_overwrite_newer_change(self):
        (self.a.root / 'index.json').write_text('[1]', encoding='utf-8')
        self.a.push()
        self.b.pull()
        (self.a.root / 'index.json').write_text('[1, 2]', encoding='utf-8')
        self.a.push()
        # b edits its old copy before seeing a's change
        (self.b.root / 'index.json').write_text('[1, 3]', encoding='utf-8')
        self.assertEqual(self.b.push()['conflicts'], 1)
        self.assertEqual(self.redis.values['fitshare:file:index.json'], '[1, 2]')
        self.b.pull()
        self.assertEqual((self.b.root / 'index.json').read_text(encoding='utf-8'), '[1, 2]')

    def test_temporary_dot_files_and_model_cache_are_not_uploaded(self):
        (self.a.root / '.index.json.123.tmp').write_text('x', encoding='utf-8')
        (self.a.root / 'garments' / '.tmp-abc').mkdir(parents=True)
        (self.a.root / 'garments' / '.tmp-abc' / 'photo.jpg').write_bytes(b'x')
        (self.a.root / 'model-cache').mkdir()
        (self.a.root / 'model-cache' / 'model.onnx').write_bytes(b'x')
        self.assertEqual(self.a.push(), {'uploaded': 0, 'removed': 0, 'conflicts': 0})

    def test_write_lock_allows_one_holder(self):
        token = self.a.acquire_write_lock()
        self.assertIsNotNone(token)
        self.assertIsNone(self.b.acquire_write_lock(wait_seconds=0))
        self.a.release_write_lock(token)
        self.assertIsNotNone(self.b.acquire_write_lock(wait_seconds=0))


def load_vercel_entry(data_dir):
    os.environ['FITSHARE_DATA_DIR'] = str(data_dir)
    path = Path(__file__).resolve().parents[1] / 'api' / 'index.py'
    spec = importlib.util.spec_from_file_location('fitshare_vercel_entry', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VercelEntryTests(unittest.TestCase):
    """Two fake Vercel instances share one fake Redis/Blob, like real cold starts."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.redis = FakeRedis()
        self.blob = FakeBlob()
        self.servers = []
        self.urls = [self.start_instance(Path(self.temp.name) / name) for name in ('one', 'two')]
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def start_instance(self, data_dir):
        module = load_vercel_entry(data_dir)
        module._folder = SyncedFolder(data_dir, self.redis, self.blob)
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), module.handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.servers.append((httpd, thread, module))
        return f'http://127.0.0.1:{httpd.server_address[1]}'

    def tearDown(self):
        for httpd, thread, _ in self.servers:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
        os.environ.pop('FITSHARE_DATA_DIR', None)
        self.temp.cleanup()

    def request(self, base, path, payload=None, method=None, headers=None, body=None):
        data = body if body is not None else (json.dumps(payload).encode('utf-8') if payload is not None else None)
        request = urllib.request.Request(base + path, data=data, method=method,
                                         headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            with self.opener.open(request) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def test_login_on_one_instance_is_seen_by_the_other(self):
        status, _, _ = self.request(self.urls[0], '/api/auth/signup',
                                    {'username': 'student1', 'password': 'pass1234', 'name': '학생'})
        self.assertEqual(status, 201)
        status, _, body = self.request(self.urls[1], '/api/auth/me')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['user']['username'], 'student1')

    def test_platform_check_and_unknown_routes(self):
        status, _, body = self.request(self.urls[0], '/api/_platform')
        self.assertEqual((status, json.loads(body)['chunked']), (200, True))
        status, _, _ = self.request(self.urls[0], '/api/nothing-here')
        self.assertEqual(status, 404)

    def test_body_sent_in_pieces_is_joined_before_the_route_runs(self):
        self.request(self.urls[0], '/api/auth/signup', {'username': 'student2', 'password': 'pass1234', 'name': '학생2'})
        state = {'version': 1, 'users': [], 'exchanges': [], 'badges': [], 'finalizedMonths': [], 'note': 'x' * 50}
        text = json.dumps({'state': state, 'baseRev': 0})
        chunk_id = 'a' * 32
        halves = [text[:40], text[40:]]
        for index, piece in enumerate(halves):
            status, _, _ = self.request(self.urls[1], f'/api/_chunks/{chunk_id}/{index}', method='PUT',
                                        body=piece.encode('utf-8'), headers={'Content-Type': 'text/plain'})
            self.assertEqual(status, 200)
        status, _, body = self.request(self.urls[0], '/api/ranking', method='POST', body=b'{}',
                                       headers={'X-FitShare-Upload': f'{chunk_id}:2'})
        self.assertEqual((status, json.loads(body)), (200, {'rev': 1}))
        status, _, body = self.request(self.urls[1], '/api/ranking')
        self.assertEqual(json.loads(body)['state']['note'], 'x' * 50)

    def test_missing_piece_and_bad_chunk_id_are_rejected(self):
        status, _, _ = self.request(self.urls[0], '/api/ranking', method='POST', body=b'{}',
                                    headers={'X-FitShare-Upload': f'{"b" * 32}:2'})
        self.assertEqual(status, 400)
        status, _, _ = self.request(self.urls[0], '/api/_chunks/../0', method='PUT', body=b'x',
                                    headers={'Content-Type': 'text/plain'})
        self.assertIn(status, (400, 404))

    def test_big_json_response_is_split_and_can_be_collected(self):
        module = self.servers[0][2]
        module.RESPONSE_LIMIT_BYTES, module.CHUNK_CHARS = 20, 16
        try:
            self.request(self.urls[0], '/api/auth/signup', {'username': 'student3', 'password': 'pass1234', 'name': '학생3'})
            status, headers, body = self.request(self.urls[0], '/api/auth/me')
            marker = headers.get('X-FitShare-Chunked')
            self.assertEqual(status, 200)
            self.assertIsNotNone(marker)
            chunk_id, count = marker.split(':')
            pieces = [self.request(self.urls[0], f'/api/_chunks/{chunk_id}/{index}')[2].decode('utf-8')
                      for index in range(int(count))]
            self.assertEqual(json.loads(''.join(pieces))['user']['username'], 'student3')
        finally:
            module.RESPONSE_LIMIT_BYTES, module.CHUNK_CHARS = 4_000_000, 3_000_000

    def test_storage_missing_gives_clear_message(self):
        module = self.servers[0][2]
        saved = module._folder
        module._folder, module._folder_error = None, 'Vercel 저장소가 연결되지 않았어요: Vercel Blob'
        try:
            status, _, body = self.request(self.urls[0], '/api/garments')
            self.assertEqual(status, 503)
            self.assertIn('Vercel Blob', json.loads(body)['error'])
        finally:
            module._folder, module._folder_error = saved, None


if __name__ == '__main__':
    unittest.main()
