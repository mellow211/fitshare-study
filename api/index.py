"""Vercel entry point: runs the same FitShareHandler as server.py, with server-data kept in Redis/Blob.

Every /api/* request is rewritten here (vercel.json). Before the request we download files that
changed on other instances into /tmp; after it we upload what changed. Bodies over Vercel's 4.5MB
limit travel in pieces through /api/_chunks (see js/vercel-fetch.js).
"""

import io
import json
import os
import re
import sys
import threading
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault('FITSHARE_MODEL_DIR', '/tmp/fitshare-model-cache')

from server import MAX_BODY_BYTES, create_handler_class  # noqa: E402
from tools.vercel_storage import StorageError, SyncedFolder, backends_from_env  # noqa: E402


DATA_DIR = Path(os.environ.get('FITSHARE_DATA_DIR', '/tmp/fitshare-data'))
CHUNK_CHARS = 3_000_000
MAX_CHUNKS = 16
RESPONSE_LIMIT_BYTES = 4_000_000
SAFE_CHUNK_ID = re.compile(r'^[0-9a-f]{32}$')
# POST 요청 중 저장 공간을 바꾸지 않는 것(사진 분석)은 쓰기 잠금 없이 처리합니다.
READ_ONLY_POSTS = {('garments', 'analyze'), ('measurements', 'automatic')}
# 사진 GET은 1초 안에 다시 확인했다면 동기화를 건너뛰어 Redis 사용량을 줄입니다.
PHOTO_SUFFIXES = {'photo', 'closet-photo', 'tryon-photo', 'thumbnail', 'product-photo', 'photos'}

_folder = None
_folder_error = None
_folder_lock = threading.Lock()


def get_folder():
    global _folder, _folder_error
    with _folder_lock:
        if _folder is None and _folder_error is None:
            try:
                redis, blob = backends_from_env()
                _folder = SyncedFolder(DATA_DIR, redis, blob, os.environ.get('FITSHARE_REDIS_PREFIX', 'fitshare'))
            except StorageError as error:
                _folder_error = str(error)
    return _folder


BaseHandler = create_handler_class(DATA_DIR)


class handler(BaseHandler):
    def do_GET(self):
        parts = self.api_parts() or []
        if parts == ['_platform']:
            return self.send_json(200, {'chunked': True, 'storage': get_folder() is not None})
        if len(parts) == 3 and parts[0] == '_chunks':
            return self.send_chunk(parts[1], parts[2])
        is_photo = len(parts) >= 3 and parts[-2 if parts[0] == 'experiments' else -1] in PHOTO_SUFFIXES
        return self.synced(super().do_GET, write=False, pull_max_age=1.0 if is_photo else 0.0)

    def do_PUT(self):
        parts = self.api_parts() or []
        if len(parts) == 3 and parts[0] == '_chunks':
            return self.receive_chunk(parts[1], parts[2])
        return self.send_json(404, {'error': '저장할 API 주소가 없어요.'})

    def do_POST(self):
        parts = tuple(self.api_parts() or [])
        return self.synced(super().do_POST, write=parts not in READ_ONLY_POSTS)

    def do_DELETE(self):
        return self.synced(super().do_DELETE, write=True)

    # ---- storage sync around the original handler ----
    def synced(self, method, write, pull_max_age=0.0):
        folder = get_folder()
        if folder is None:
            return self.send_json(503, {'error': _folder_error or '저장소를 준비하지 못했어요.'})
        token = None
        try:
            if write:
                token = folder.acquire_write_lock()
                if token is None:
                    return self.send_json(503, {'error': '다른 사람이 저장하는 중이에요. 잠시 뒤 다시 시도해 주세요.'})
            folder.pull(max_age=pull_max_age)
        except StorageError as error:
            if token:
                folder.release_write_lock(token)
            self.log_error('storage pull failed: %s', error)
            return self.send_json(503, {'error': '저장소에서 자료를 받아오지 못했어요. 잠시 뒤 다시 시도해 주세요.'})
        real_wfile = self.wfile
        buffer = io.BytesIO()
        self.wfile = buffer
        try:
            method()
        finally:
            self.wfile = real_wfile
            try:
                folder.push()
            except (StorageError, OSError) as error:
                self.log_error('storage push failed: %s', error)
                buffer = io.BytesIO()
                self.wfile = buffer
                self.send_json(503, {'error': '저장소에 저장하지 못했어요. 잠시 뒤 다시 시도해 주세요.'})
                self.wfile = real_wfile
            finally:
                if token:
                    try:
                        folder.release_write_lock(token)
                    except StorageError:
                        pass  # the lock expires by itself
        self.wfile.write(self.shrink_response(buffer.getvalue()))

    # ---- big bodies ----
    def read_payload(self):
        marker = self.headers.get('X-FitShare-Upload', '')
        if not marker:
            return super().read_payload()
        chunk_id, _, count = marker.partition(':')
        folder = get_folder()
        if folder is None or not SAFE_CHUNK_ID.fullmatch(chunk_id) or not count.isdigit() or not 0 < int(count) <= MAX_CHUNKS:
            self.send_json(400, {'error': '나눠 보낸 사진 내용을 확인할 수 없어요. 다시 시도해 주세요.'})
            return None
        try:
            pieces = folder.load_chunks([f'fitshare:chunk:up:{chunk_id}:{index}' for index in range(int(count))])
        except StorageError:
            pieces = [None]
        if any(piece is None for piece in pieces):
            self.send_json(400, {'error': '사진 전송이 끝나기 전에 끊겼어요. 다시 시도해 주세요.'})
            return None
        text = ''.join(pieces)
        if len(text.encode('utf-8')) > MAX_BODY_BYTES:
            self.send_json(413, {'error': '사진 전송 크기가 너무 커요. 사진 파일 크기를 줄여 다시 골라 주세요. 측정 전 자동 축소는 하지 않아요.'})
            return None
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            self.send_json(400, {'error': '보내는 내용을 읽지 못했어요.'})
            return None
        if not isinstance(value, dict):
            self.send_json(400, {'error': '저장 내용의 모양이 올바르지 않아요.'})
            return None
        return value

    def receive_chunk(self, chunk_id, index):
        folder = get_folder()
        if folder is None:
            return self.send_json(503, {'error': _folder_error or '저장소를 준비하지 못했어요.'})
        if not SAFE_CHUNK_ID.fullmatch(chunk_id) or not index.isdigit() or int(index) >= MAX_CHUNKS:
            return self.send_json(400, {'error': '나눠 보내는 순서가 올바르지 않아요.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            length = 0
        if not 0 < length <= CHUNK_CHARS * 2:
            return self.send_json(400, {'error': '나눠 보낸 조각 크기가 올바르지 않아요.'})
        try:
            text = self.rfile.read(length).decode('utf-8')
            folder.save_chunk(f'fitshare:chunk:up:{chunk_id}:{index}', text)
        except (UnicodeDecodeError, StorageError):
            return self.send_json(502, {'error': '사진 조각을 저장하지 못했어요. 다시 시도해 주세요.'})
        return self.send_json(200, {'ok': True})

    def send_chunk(self, chunk_id, index):
        folder = get_folder()
        if folder is None or not SAFE_CHUNK_ID.fullmatch(chunk_id) or not index.isdigit():
            return self.send_json(404, {'error': '응답 조각을 찾지 못했어요.'})
        try:
            piece = folder.load_chunks([f'fitshare:chunk:down:{chunk_id}:{index}'])[0]
        except StorageError:
            piece = None
        if piece is None:
            return self.send_json(404, {'error': '응답 조각을 찾지 못했어요. 다시 시도해 주세요.'})
        body = piece.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def shrink_response(self, raw):
        """Keep the response under 4.5MB: big JSON goes out in pieces, big photos are re-encoded for display."""
        head, separator, body = raw.partition(b'\r\n\r\n')
        if not separator or len(body) <= RESPONSE_LIMIT_BYTES:
            return raw
        lines = head.decode('latin-1').split('\r\n')
        headers = [line for line in lines[1:] if not line.lower().startswith('content-length:')]
        content_type = next((line.split(':', 1)[1].strip() for line in headers
                             if line.lower().startswith('content-type:')), '')
        folder = get_folder()
        if content_type.startswith('application/json') and folder is not None:
            text = body.decode('utf-8')
            chunk_id = uuid.uuid4().hex
            pieces = [text[start:start + CHUNK_CHARS] for start in range(0, len(text), CHUNK_CHARS)]
            try:
                for index, piece in enumerate(pieces):
                    folder.save_chunk(f'fitshare:chunk:down:{chunk_id}:{index}', piece, ttl_seconds=300)
            except StorageError:
                return raw
            headers.append(f'X-FitShare-Chunked: {chunk_id}:{len(pieces)}')
            body = json.dumps({'chunked': True}).encode('utf-8')
        elif content_type.startswith('image/'):
            body, content_type = shrink_image(body)
            headers = [line for line in headers if not line.lower().startswith('content-type:')]
            headers.append(f'Content-Type: {content_type}')
        else:
            return raw
        headers.append(f'Content-Length: {len(body)}')
        return '\r\n'.join([lines[0], *headers]).encode('latin-1') + b'\r\n\r\n' + body


def shrink_image(body):
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(body, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None:
        return body, 'application/octet-stream'
    has_alpha = image.ndim == 3 and image.shape[2] == 4
    while True:
        if has_alpha:
            ok, encoded = cv2.imencode('.png', image, [cv2.IMWRITE_PNG_COMPRESSION, 9])
        else:
            ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if ok and len(encoded) <= RESPONSE_LIMIT_BYTES:
            return encoded.tobytes(), 'image/png' if has_alpha else 'image/jpeg'
        height, width = image.shape[:2]
        image = cv2.resize(image, (max(1, int(width * 0.8)), max(1, int(height * 0.8))), interpolation=cv2.INTER_AREA)
