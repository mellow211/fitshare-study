import http.cookiejar
import importlib.util
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from tools.vercel_seed import copy_saved_reports
from tools.vercel_storage import SyncedFolder


class FakeSupabase:
    """The database functions in supabase/schema.sql and the Storage bucket, kept in memory."""

    def __init__(self):
        self.rows = {}
        self.objects = {}
        self.locks = {}
        self.chunks = {}

    def manifest(self):
        return {path: {key: row[key] for key in ('path', 'version', 'kind', 'size', 'object_path')}
                for path, row in self.rows.items()}

    def read_texts(self, paths):
        return {path: self.rows[path]['content'] for path in paths if path in self.rows}

    def apply(self, operations):
        results = []
        for op in operations:
            current = self.rows.get(op['path'])
            if op['action'] == 'delete':
                ok = current is not None and current['version'] == op['expected']
                if ok:
                    del self.rows[op['path']]
            else:
                ok = (current is None and op['expected'] == '') or (current is not None and current['version'] == op['expected'])
                if ok:
                    self.rows[op['path']] = dict(op)
            results.append(ok)
        return results

    def put_object(self, object_path, data, content_type):
        self.objects[object_path] = data

    def get_object(self, object_path):
        return self.objects[object_path]

    def delete_objects(self, object_paths):
        for object_path in object_paths:
            self.objects.pop(object_path, None)

    def lock(self, token, seconds):
        if 'server-data' in self.locks:
            return False
        self.locks['server-data'] = token
        return True

    def unlock(self, token):
        if self.locks.get('server-data') == token:
            del self.locks['server-data']

    def put_chunk(self, key, value, seconds):
        self.chunks[key] = value

    def take_chunks(self, keys):
        return [self.chunks.pop(key, None) for key in keys]


class SyncedFolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = FakeSupabase()
        self.a = SyncedFolder(Path(self.temp.name) / 'a', self.store)
        self.b = SyncedFolder(Path(self.temp.name) / 'b', self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_json_goes_to_table_and_photo_to_bucket_then_other_instance_reads_them(self):
        (self.a.root / 'garments' / 'g1').mkdir(parents=True)
        (self.a.root / 'garments' / 'g1' / 'garment.json').write_text('{"name": "셔츠"}', encoding='utf-8')
        (self.a.root / 'garments' / 'g1' / 'photo.jpg').write_bytes(b'\xff\xd8\xffphoto')
        self.assertEqual(self.a.push()['uploaded'], 2)
        self.assertEqual(len(self.store.objects), 1)
        self.assertEqual(self.store.rows['garments/g1/garment.json']['content'], '{"name": "셔츠"}')

        self.b.pull()
        self.assertEqual((self.b.root / 'garments' / 'g1' / 'garment.json').read_text(encoding='utf-8'), '{"name": "셔츠"}')
        self.assertEqual((self.b.root / 'garments' / 'g1' / 'photo.jpg').read_bytes(), b'\xff\xd8\xffphoto')

    def test_overwrite_replaces_old_photo_and_delete_reaches_other_instance(self):
        photo = self.a.root / 'photo.png'
        photo.write_bytes(b'one')
        self.a.push()
        self.b.pull()
        photo.write_bytes(b'two!')
        self.a.push()
        self.assertEqual(list(self.store.objects.values()), [b'two!'])
        self.b.pull()
        self.assertEqual((self.b.root / 'photo.png').read_bytes(), b'two!')

        photo.unlink()
        self.assertEqual(self.a.push()['removed'], 1)
        self.b.pull()
        self.assertFalse((self.b.root / 'photo.png').exists())
        self.assertEqual(self.store.objects, {})

    def test_stale_instance_does_not_overwrite_newer_change(self):
        (self.a.root / 'index.json').write_text('[1]', encoding='utf-8')
        self.a.push()
        self.b.pull()
        (self.a.root / 'index.json').write_text('[1, 2]', encoding='utf-8')
        self.a.push()
        # b edits its old copy before seeing a's change
        (self.b.root / 'index.json').write_text('[1, 3]', encoding='utf-8')
        self.assertEqual(self.b.push()['conflicts'], 1)
        self.assertEqual(self.store.rows['index.json']['content'], '[1, 2]')
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


class SeedReportTests(unittest.TestCase):
    def test_saved_reports_point_photos_at_the_docs_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs_data, report_dir = Path(temporary) / 'docs', Path(temporary) / 'report'
            docs_data.mkdir()
            row = {'sourceUrl': 'assets/photos/ChArUco/a.jpg', 'imagePath': 'measure_dataset/ChArUco/a.jpg'}
            (docs_data / 'measure-dataset.json').write_text(json.dumps({'rows': [row]}), encoding='utf-8')
            (docs_data / 'measure-dataset-round3.json').write_text(json.dumps({'rows': [dict(row, sourceUrl='/already/ok.jpg')]}), encoding='utf-8')
            copy_saved_reports(docs_data, report_dir)
            report = json.loads((report_dir / 'report.json').read_text(encoding='utf-8'))
            round3 = json.loads((report_dir / 'round3.json').read_text(encoding='utf-8'))
        self.assertEqual(report['rows'][0]['sourceUrl'], 'docs/assets/photos/ChArUco/a.jpg')
        self.assertEqual(round3['rows'][0]['sourceUrl'], '/already/ok.jpg')


def load_vercel_entry(data_dir):
    os.environ['FITSHARE_DATA_DIR'] = str(data_dir)
    path = Path(__file__).resolve().parents[1] / 'api' / 'index.py'
    spec = importlib.util.spec_from_file_location('fitshare_vercel_entry', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VercelEntryTests(unittest.TestCase):
    """Two fake Vercel instances share one fake Supabase, like real cold starts."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = FakeSupabase()
        self.servers = []
        self.urls = [self.start_instance(Path(self.temp.name) / name) for name in ('one', 'two')]
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def start_instance(self, data_dir):
        module = load_vercel_entry(data_dir)
        module._folder = SyncedFolder(data_dir, self.store)
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

    def test_platform_check_keepalive_and_unknown_routes(self):
        status, _, body = self.request(self.urls[0], '/api/_platform')
        self.assertEqual((status, json.loads(body)['chunked']), (200, True))
        status, _, body = self.request(self.urls[0], '/api/_keepalive')
        self.assertEqual((status, json.loads(body)), (200, {'ok': True}))
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

    def test_dataset_pages_show_saved_results_and_never_start_analysis(self):
        status, _, body = self.request(self.urls[0], '/api/measure-dataset')
        self.assertEqual(status, 404)
        self.assertIn('python server.py', json.loads(body)['error'])
        self.assertEqual(self.store.rows, {})  # nothing (like an empty report) was saved

        for path in ('/api/measure-dataset/run', '/api/measure-dataset/round3/run',
                     '/api/measure-dataset/gemini', '/api/measure-dataset/save-as-experiment'):
            status, _, body = self.request(self.urls[0], path, method='POST', body=b'{}')
            self.assertEqual(status, 503, path)

        report = {'generatedAt': 'x', 'summary': {}, 'byBackground': [], 'byGarment': [], 'rows': []}
        self.store.apply([{'action': 'set', 'path': 'measure-dataset-report/report.json', 'version': 'v1',
                           'expected': '', 'kind': 'text', 'size': 1, 'object_path': None,
                           'content': json.dumps(report)}])
        status, _, body = self.request(self.urls[1], '/api/measure-dataset')
        self.assertEqual((status, json.loads(body)), (200, report))

    def test_write_that_conflicts_does_not_report_success(self):
        self.request(self.urls[0], '/api/auth/signup', {'username': 'student4', 'password': 'pass1234', 'name': '학생4'})
        folder = self.servers[0][2]._folder
        real_push = folder.push
        folder.push = lambda: {'uploaded': 0, 'removed': 0, 'conflicts': 1}
        try:
            state = {'version': 1, 'users': [], 'exchanges': [], 'badges': [], 'finalizedMonths': []}
            status, _, body = self.request(self.urls[0], '/api/ranking', {'state': state, 'baseRev': 0}, method='POST')
        finally:
            folder.push = real_push
        self.assertEqual(status, 409)
        self.assertIn('새로고침', json.loads(body)['error'])
        self.assertEqual(self.store.locks, {})  # the write lock was released

    def test_product_photo_generation_stops_before_the_vercel_time_limit(self):
        module = self.servers[0][2]
        module._request.started = 0.0  # pretend SAM took very long
        try:
            with self.assertRaises(module.product_photo.ProductPhotoError) as caught:
                module.product_photo.generate_product_photo(b'png', 'prompt', 'token', 'version')
        finally:
            module._request.started = None
        self.assertIn('한 번 더', str(caught.exception))

    def test_storage_missing_gives_clear_message(self):
        module = self.servers[0][2]
        saved = module._folder
        module._folder, module._folder_error = None, 'Supabase가 연결되지 않았어요: SUPABASE_URL'
        try:
            status, _, body = self.request(self.urls[0], '/api/garments')
            self.assertEqual(status, 503)
            self.assertIn('Supabase', json.loads(body)['error'])
        finally:
            module._folder, module._folder_error = saved, None


if __name__ == '__main__':
    unittest.main()
