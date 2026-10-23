"""Keep the Vercel /tmp copy of server-data in sync with Supabase.

server.py reads and writes plain files. Vercel functions lose /tmp between instances, so
before a request we download files that changed elsewhere, and after a request we upload
files this request changed. JSON files live in the fitshare_files table; photos live in the
private Supabase Storage bucket 'fitshare'. Table and function setup: supabase/schema.sql.
Python standard library only (urllib), like server.py.
"""

import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


BUCKET = 'fitshare'
SKIPPED_TOP_FOLDERS = {'model-cache'}
CONTENT_TYPES = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp'}
LOCK_NAME = 'server-data'


class StorageError(RuntimeError):
    pass


def _http(method, url, body=None, headers=None, timeout=60):
    request = Request(url, data=body, method=method, headers=headers or {})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as error:
        detail = error.read()[:300].decode('utf-8', 'replace')
        raise StorageError(f'{method} {url.split("?")[0]} failed ({error.code}): {detail}') from error
    except URLError as error:
        raise StorageError(f'{method} {url.split("?")[0]} failed: {error.reason}') from error


class SupabaseStore:
    """Supabase REST: database functions from supabase/schema.sql plus the private Storage bucket."""

    def __init__(self, url, key):
        self.url = url.rstrip('/')
        self.headers = {'apikey': key}
        if not key.startswith('sb_'):
            # 예전 형식(JWT) 키는 Authorization 헤더도 함께 보내야 해요.
            self.headers['Authorization'] = f'Bearer {key}'

    def rpc(self, name, **arguments):
        raw = _http('POST', f'{self.url}/rest/v1/rpc/{name}', json.dumps(arguments).encode('utf-8'),
                    {**self.headers, 'Content-Type': 'application/json'})
        return json.loads(raw) if raw else None

    # ---- file list and JSON files ----
    def manifest(self):
        return {row['path']: row for row in self.rpc('fitshare_manifest')}

    def read_texts(self, paths):
        return self.rpc('fitshare_read', paths=list(paths)) if paths else {}

    def apply(self, operations):
        return self.rpc('fitshare_apply', ops=operations) if operations else []

    # ---- photos ----
    def put_object(self, object_path, data, content_type):
        _http('POST', f'{self.url}/storage/v1/object/{BUCKET}/{quote(object_path)}', data,
              {**self.headers, 'Content-Type': content_type, 'x-upsert': 'true'}, timeout=120)

    def get_object(self, object_path):
        return _http('GET', f'{self.url}/storage/v1/object/authenticated/{BUCKET}/{quote(object_path)}',
                     headers=self.headers, timeout=120)

    def delete_objects(self, object_paths):
        if object_paths:
            _http('DELETE', f'{self.url}/storage/v1/object/{BUCKET}',
                  json.dumps({'prefixes': list(object_paths)}).encode('utf-8'),
                  {**self.headers, 'Content-Type': 'application/json'})

    # ---- write lock and big bodies ----
    def lock(self, token, seconds):
        return self.rpc('fitshare_lock', p_name=LOCK_NAME, p_token=token, p_seconds=seconds) is True

    def unlock(self, token):
        self.rpc('fitshare_unlock', p_name=LOCK_NAME, p_token=token)

    def put_chunk(self, key, value, seconds):
        self.rpc('fitshare_put_chunk', p_key=key, p_value=value, p_seconds=seconds)

    def take_chunks(self, keys):
        found = self.rpc('fitshare_take_chunks', keys=list(keys)) or {}
        return [found.get(key) for key in keys]


def store_from_env(environ=None):
    environ = os.environ if environ is None else environ
    url = environ.get('SUPABASE_URL')
    key = environ.get('SUPABASE_SECRET_KEY') or environ.get('SUPABASE_SERVICE_ROLE_KEY')
    if not url or not key:
        raise StorageError('Supabase가 연결되지 않았어요: SUPABASE_URL과 SUPABASE_SECRET_KEY를 확인해 주세요.')
    return SupabaseStore(url, key)


class SyncedFolder:
    """One instance-wide mirror of server-data between /tmp and Supabase."""

    def __init__(self, root, store):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = store
        self.state_lock = threading.Lock()
        # rel path -> (version, object_path, size, mtime_ns) as last synced; version '' = must re-download
        self.known = {}
        self.last_pull = 0.0

    # ---- local files ----
    def _scan(self):
        found = {}
        for path in self.root.rglob('*'):
            rel = path.relative_to(self.root).as_posix()
            parts = rel.split('/')
            # This persistent counter must survive cold starts and deletion of the last garment.
            persistent_sequence = rel == 'garments/.sequences.json'
            if parts[0] in SKIPPED_TOP_FOLDERS or (not persistent_sequence and any(part.startswith('.') for part in parts)):
                continue
            if path.is_file():
                stat = path.stat()
                found[rel] = (stat.st_size, stat.st_mtime_ns)
        return found

    def _write_local(self, rel, data):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.sync')
        temporary.write_bytes(data)
        temporary.replace(path)
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns

    def _remove_local(self, rel):
        path = self.root / rel
        path.unlink(missing_ok=True)
        parent = path.parent
        while parent != self.root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    # ---- download ----
    def pull(self, max_age=0.0):
        """Bring in files other instances changed. Files this instance is still editing are kept."""
        if max_age and time.monotonic() - self.last_pull < max_age:
            return
        with self.state_lock:
            manifest = self.store.manifest()
            local = self._scan()
            wanted = []
            for rel, row in manifest.items():
                known = self.known.get(rel)
                if known and known[0] == row['version']:
                    continue
                if known and local.get(rel) != known[2:]:
                    continue  # changed here too; push() will settle it
                if not known and rel in local:
                    continue  # new file here that is not uploaded yet
                wanted.append(row)
            self._download(wanted)
            for rel in list(self.known):
                if rel not in manifest and local.get(rel) == self.known[rel][2:]:
                    self._remove_local(rel)
                    del self.known[rel]
            self.last_pull = time.monotonic()

    def _download(self, rows):
        texts = self.store.read_texts([row['path'] for row in rows if row['kind'] == 'text'])
        for row in rows:
            if row['kind'] == 'text' and texts.get(row['path']) is not None:
                stat = self._write_local(row['path'], texts[row['path']].encode('utf-8'))
                self.known[row['path']] = (row['version'], None, *stat)
        objects = [row for row in rows if row['kind'] == 'object']
        if objects:
            with ThreadPoolExecutor(max_workers=8) as pool:
                contents = list(pool.map(lambda row: self.store.get_object(row['object_path']), objects))
            for row, data in zip(objects, contents):
                self.known[row['path']] = (row['version'], row['object_path'], *self._write_local(row['path'], data))

    # ---- upload ----
    def push(self):
        """Upload files changed in /tmp since the last sync; skip a file another instance changed first."""
        with self.state_lock:
            local = self._scan()
            changed = [rel for rel, stat in local.items()
                       if rel not in self.known or self.known[rel][2:] != stat]
            removed = [rel for rel in self.known if rel not in local]
            if not changed and not removed:
                return {'uploaded': 0, 'removed': 0, 'conflicts': 0}
            uploads = []
            for rel in changed:
                data = (self.root / rel).read_bytes()
                version = uuid.uuid4().hex
                text = None
                if rel.endswith('.json'):
                    try:
                        text = data.decode('utf-8')
                    except UnicodeDecodeError:
                        text = None
                suffix = Path(rel).suffix.lower()
                uploads.append({
                    'action': 'set', 'path': rel, 'version': version, 'size': len(data),
                    'expected': self.known[rel][0] if rel in self.known else '',
                    'kind': 'text' if text is not None else 'object', 'content': text,
                    'object_path': None if text is not None else f'{rel}.{version}{suffix}',
                    '_data': data, '_stat': local[rel],
                })
            objects = [item for item in uploads if item['kind'] == 'object']
            if objects:
                def upload(item):
                    suffix = Path(item['path']).suffix.lower()
                    self.store.put_object(item['object_path'], item['_data'],
                                          CONTENT_TYPES.get(suffix, 'application/octet-stream'))
                with ThreadPoolExecutor(max_workers=8) as pool:
                    list(pool.map(upload, objects))
            operations = [{key: value for key, value in item.items() if not key.startswith('_')} for item in uploads]
            operations += [{'action': 'delete', 'path': rel, 'expected': self.known[rel][0]} for rel in removed]
            results = self.store.apply(operations)
            stale, conflicts = [], 0
            for item, ok in zip(uploads, results[:len(uploads)]):
                previous = self.known.get(item['path'])
                if ok:
                    if previous and previous[1]:
                        stale.append(previous[1])
                    self.known[item['path']] = (item['version'], item['object_path'], *item['_stat'])
                else:
                    conflicts += 1
                    if item['object_path']:
                        stale.append(item['object_path'])
                    self.known[item['path']] = ('', None, *item['_stat'])  # next pull() takes the other version
            for rel, ok in zip(removed, results[len(uploads):]):
                previous = self.known.pop(rel)
                if ok and previous[1]:
                    stale.append(previous[1])
            try:
                self.store.delete_objects(stale)
            except StorageError:
                pass  # leftover old photos only cost space
            return {'uploaded': len(uploads) - conflicts, 'removed': len(removed), 'conflicts': conflicts}

    # ---- cross-instance write lock ----
    def acquire_write_lock(self, wait_seconds=90, hold_seconds=330):
        token = uuid.uuid4().hex
        deadline = time.monotonic() + wait_seconds
        while True:
            if self.store.lock(token, hold_seconds):
                return token
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.25)

    def release_write_lock(self, token):
        if token:
            self.store.unlock(token)

    # ---- large request/response bodies (Vercel limit is 4.5MB per request) ----
    def save_chunk(self, key, text, ttl_seconds=900):
        self.store.put_chunk(key, text, ttl_seconds)

    def load_chunks(self, keys):
        return self.store.take_chunks(keys)
