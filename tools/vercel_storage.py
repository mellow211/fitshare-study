"""Keep the Vercel /tmp copy of server-data in sync with Upstash Redis and Vercel Blob.

server.py reads and writes plain files. Vercel functions lose /tmp between instances, so
before a request we download files that changed elsewhere, and after a request we upload
files this request changed. JSON files live in Redis; photos live in private Vercel Blob.
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
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BLOB_API_URL = 'https://vercel.com/api/blob'
BLOB_API_VERSION = '13'
SKIPPED_TOP_FOLDERS = {'model-cache'}
CONTENT_TYPES = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp'}

# Update one file only if nobody else changed it since we read it (compare-and-set).
CAS_SET_SCRIPT = """
local current = redis.call('HGET', KEYS[1], ARGV[1])
if (current == false and ARGV[2] == '') or current == ARGV[2] then
  if ARGV[4] ~= '' then redis.call('SET', KEYS[2], ARGV[4]) end
  redis.call('HSET', KEYS[1], ARGV[1], ARGV[3])
  return 1
end
return 0
"""
CAS_DELETE_SCRIPT = """
local current = redis.call('HGET', KEYS[1], ARGV[1])
if current == ARGV[2] then
  redis.call('HDEL', KEYS[1], ARGV[1])
  redis.call('DEL', KEYS[2])
  return 1
end
return 0
"""
RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""


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


class RedisRest:
    """Upstash Redis REST API (the variables Vercel adds when Upstash is connected)."""

    def __init__(self, url, token):
        self.url = url.rstrip('/')
        self.token = token

    def pipeline(self, commands):
        if not commands:
            return []
        body = json.dumps(commands).encode('utf-8')
        raw = _http('POST', f'{self.url}/pipeline', body,
                    {'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'})
        results = json.loads(raw)
        for item in results:
            if 'error' in item:
                raise StorageError(f'Redis error: {item["error"]}')
        return [item.get('result') for item in results]

    def command(self, *args):
        return self.pipeline([list(args)])[0]


class BlobRest:
    """Vercel Blob REST API, same requests the official @vercel/blob SDK sends."""

    def __init__(self, token, access='private'):
        self.token = token
        self.access = access
        parts = token.split('_')
        self.store_id = parts[3] if len(parts) > 3 else ''

    def _headers(self, extra=None):
        headers = {'authorization': f'Bearer {self.token}', 'x-api-version': BLOB_API_VERSION,
                   'x-vercel-blob-store-id': self.store_id}
        headers.update(extra or {})
        return headers

    def put(self, pathname, data, content_type):
        url = f'{BLOB_API_URL}/?{urlencode({"pathname": pathname})}'
        raw = _http('PUT', url, data, self._headers({
            'x-vercel-blob-access': self.access, 'x-add-random-suffix': '0', 'x-allow-overwrite': '1',
            'x-content-type': content_type, 'content-type': content_type,
        }), timeout=120)
        return json.loads(raw)['url']

    def get(self, url):
        # Every pathname we write contains a new version id, so the cached copy is always right.
        return _http('GET', url, headers={'authorization': f'Bearer {self.token}'}, timeout=120)

    def delete(self, urls):
        if urls:
            _http('POST', f'{BLOB_API_URL}/delete', json.dumps({'urls': list(urls)}).encode('utf-8'),
                  self._headers({'content-type': 'application/json'}))


def backends_from_env(environ=None):
    environ = os.environ if environ is None else environ
    redis_url = environ.get('KV_REST_API_URL') or environ.get('UPSTASH_REDIS_REST_URL')
    redis_token = environ.get('KV_REST_API_TOKEN') or environ.get('UPSTASH_REDIS_REST_TOKEN')
    blob_token = environ.get('BLOB_READ_WRITE_TOKEN')
    missing = [name for name, value in (('Upstash Redis (KV_REST_API_URL/TOKEN)', redis_url and redis_token),
                                        ('Vercel Blob (BLOB_READ_WRITE_TOKEN)', blob_token)) if not value]
    if missing:
        raise StorageError('Vercel 저장소가 연결되지 않았어요: ' + ', '.join(missing))
    return RedisRest(redis_url, redis_token), BlobRest(blob_token, environ.get('FITSHARE_BLOB_ACCESS', 'private'))


class SyncedFolder:
    """One instance-wide mirror of server-data between /tmp and Redis/Blob."""

    def __init__(self, root, redis, blob, prefix='fitshare'):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.redis = redis
        self.blob = blob
        self.manifest_key = f'{prefix}:manifest'
        self.file_prefix = f'{prefix}:file:'
        self.lock_key = f'{prefix}:write-lock'
        self.blob_prefix = f'{prefix}/'
        self.state_lock = threading.Lock()
        # rel path -> (raw manifest entry, size, mtime_ns) as last synced
        self.known = {}
        self.last_pull = 0.0

    # ---- local files ----
    def _scan(self):
        found = {}
        for path in self.root.rglob('*'):
            rel = path.relative_to(self.root).as_posix()
            parts = rel.split('/')
            if parts[0] in SKIPPED_TOP_FOLDERS or any(part.startswith('.') for part in parts):
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
            manifest = self._read_manifest()
            local = self._scan()
            wanted = []
            for rel, raw in manifest.items():
                known = self.known.get(rel)
                if known and known[0] == raw:
                    continue
                if known and local.get(rel) != known[1:]:
                    continue  # changed here too; push() will settle it
                if not known and rel in local:
                    continue  # new file here that is not uploaded yet
                wanted.append((rel, raw))
            self._download(wanted)
            for rel in list(self.known):
                if rel not in manifest and local.get(rel) == self.known[rel][1:]:
                    self._remove_local(rel)
                    del self.known[rel]
            self.last_pull = time.monotonic()

    def _read_manifest(self):
        flat = self.redis.command('HGETALL', self.manifest_key) or []
        return dict(zip(flat[0::2], flat[1::2]))

    def _download(self, wanted):
        if not wanted:
            return
        redis_items = [(rel, raw) for rel, raw in wanted if json.loads(raw)['kind'] == 'redis']
        blob_items = [(rel, raw) for rel, raw in wanted if json.loads(raw)['kind'] == 'blob']
        if redis_items:
            values = self.redis.pipeline([['GET', self.file_prefix + rel] for rel, _ in redis_items])
            for (rel, raw), value in zip(redis_items, values):
                if value is not None:
                    self.known[rel] = (raw, *self._write_local(rel, value.encode('utf-8')))
        if blob_items:
            with ThreadPoolExecutor(max_workers=8) as pool:
                contents = list(pool.map(lambda item: self.blob.get(json.loads(item[1])['url']), blob_items))
            for (rel, raw), data in zip(blob_items, contents):
                self.known[rel] = (raw, *self._write_local(rel, data))

    # ---- upload ----
    def push(self):
        """Upload files changed in /tmp since the last sync; skip a file another instance changed first."""
        with self.state_lock:
            local = self._scan()
            changed = [rel for rel, stat in local.items()
                       if rel not in self.known or self.known[rel][1:] != stat]
            removed = [rel for rel in self.known if rel not in local]
            if not changed and not removed:
                return {'uploaded': 0, 'removed': 0, 'conflicts': 0}
            uploads = []
            for rel in changed:
                data = (self.root / rel).read_bytes()
                version = uuid.uuid4().hex
                entry = {'v': version, 'size': len(data)}
                text = None
                if rel.endswith('.json'):
                    try:
                        text = data.decode('utf-8')
                    except UnicodeDecodeError:
                        text = None
                if text is not None:
                    entry['kind'] = 'redis'
                else:
                    entry['kind'] = 'blob'
                uploads.append([rel, entry, text, data, local[rel]])
            blob_uploads = [item for item in uploads if item[1]['kind'] == 'blob']
            if blob_uploads:
                def upload(item):
                    rel, entry, _, data, _ = item
                    suffix = Path(rel).suffix.lower()
                    pathname = f'{self.blob_prefix}{rel}~{entry["v"]}{suffix}'
                    entry['url'] = self.blob.put(pathname, data, CONTENT_TYPES.get(suffix, 'application/octet-stream'))
                with ThreadPoolExecutor(max_workers=8) as pool:
                    list(pool.map(upload, blob_uploads))
            commands = []
            for rel, entry, text, _, _ in uploads:
                expected = self.known[rel][0] if rel in self.known else ''
                commands.append(['EVAL', CAS_SET_SCRIPT, '2', self.manifest_key, self.file_prefix + rel,
                                 rel, expected, json.dumps(entry, sort_keys=True), text or ''])
            for rel in removed:
                commands.append(['EVAL', CAS_DELETE_SCRIPT, '2', self.manifest_key, self.file_prefix + rel,
                                 rel, self.known[rel][0]])
            results = self.redis.pipeline(commands)
            stale_blobs, conflicts = [], 0
            for (rel, entry, _, _, stat), ok in zip(uploads, results[:len(uploads)]):
                previous = self.known.get(rel)
                if ok == 1:
                    if previous and json.loads(previous[0]).get('url'):
                        stale_blobs.append(json.loads(previous[0])['url'])
                    self.known[rel] = (json.dumps(entry, sort_keys=True), *stat)
                else:
                    conflicts += 1
                    if entry.get('url'):
                        stale_blobs.append(entry['url'])
                    self.known[rel] = ('', *stat)  # next pull() takes the other instance's version
            for rel, ok in zip(removed, results[len(uploads):]):
                previous = self.known.pop(rel)
                if ok == 1 and json.loads(previous[0]).get('url'):
                    stale_blobs.append(json.loads(previous[0])['url'])
            try:
                self.blob.delete(stale_blobs)
            except StorageError:
                pass  # leftover old photos only cost space
            return {'uploaded': len(uploads) - conflicts, 'removed': len(removed), 'conflicts': conflicts}

    # ---- cross-instance write lock ----
    def acquire_write_lock(self, wait_seconds=90, hold_seconds=330):
        token = uuid.uuid4().hex
        deadline = time.monotonic() + wait_seconds
        while True:
            if self.redis.command('SET', self.lock_key, token, 'NX', 'PX', str(hold_seconds * 1000)) == 'OK':
                return token
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.2)

    def release_write_lock(self, token):
        if token:
            self.redis.command('EVAL', RELEASE_LOCK_SCRIPT, '1', self.lock_key, token)

    # ---- large request/response bodies (Vercel limit is 4.5MB per request) ----
    def save_chunk(self, key, text, ttl_seconds=900):
        self.redis.command('SET', key, text, 'EX', str(ttl_seconds))

    def load_chunks(self, keys):
        values = self.redis.pipeline([['GET', key] for key in keys])
        self.redis.pipeline([['DEL', key] for key in keys])
        return values
