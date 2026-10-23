"""FitShare static site and local experiment/photo storage API (Python stdlib only)."""

import base64
import binascii
import json
import math
import os
import re
import shutil
import ssl
import threading
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from tools.accounts import SESSION_SECONDS, AccountStore, AuthError
from tools.garment_thumbnails import GARMENT_MASK_PIPELINE_VERSION, THUMBNAIL_VERSION
from tools.garment_metadata import clean_metadata
from tools.garment_size_estimation import estimate_korean_size
from tools.clothing_wishes import clean_filters, new_wish, public_wish, valid_store


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = PROJECT_ROOT / 'server-data'
MAX_BODY_BYTES = 18 * 1024 * 1024
MAX_PHOTO_BYTES = 12 * 1024 * 1024
VALID_CATEGORIES = {'top', 'bottom', 'dress', 'pants', 'skirt', 'other'}
VALID_GENDERS = {'male', 'female', 'unisex', 'unknown'}
GARMENT_TYPE_NAMES = {1: '상의 외투', 2: '티셔츠', 3: '원피스', 4: '점퍼', 5: '바지', 6: '치마', 7: '기타'}
GARMENT_TYPE_CATEGORIES = {1: 'top', 2: 'top', 3: 'dress', 4: 'top', 5: 'pants', 6: 'skirt', 7: 'other'}
GARMENT_TYPE_CODES = {1: 'o', 2: 't', 3: 'd', 4: 'j', 5: 'p', 6: 's', 7: 'e'}
DEFAULT_SCHOOL_CODE = '나눔초등학교'
VALID_MATS = {'M1', 'M2', 'M3', 'M4'}
SAFE_GARMENT_ID = re.compile(r'^garment-[1-9][0-9]?$')
SAFE_PHOTO_NAME = re.compile(r'^[A-Za-z0-9_-]{1,100}\.jpg$')
PRODUCT_MASK_PIPELINE_VERSION = 'yolo-sam-vit-b-v3'
LEGACY_GARMENT_MASK_PIPELINE_VERSION = 'round7-848-yolo-grabcut-v1'


def clean_school_code(value):
    if not isinstance(value, str):
        value = DEFAULT_SCHOOL_CODE
    cleaned = re.sub(r'[^A-Za-z0-9가-힣.-]', '-', value.strip())
    cleaned = re.sub(r'-+', '-', cleaned).strip('-')[:30]
    return cleaned or DEFAULT_SCHOOL_CODE


def allocate_garment_code(item, sequences):
    school_code = clean_school_code(item.get('schoolCode'))
    type_code = GARMENT_TYPE_CODES.get(item.get('garmentTypeId'), 'e')
    sequence_key = f'{school_code}|{type_code}'
    try:
        previous_number = int(sequences.get(sequence_key, 0))
    except (TypeError, ValueError):
        previous_number = 0
    number = max(0, previous_number) + 1
    sequences[sequence_key] = number
    donor_id = item.get('donorId') or item.get('ownerUsername') or 'unknown'
    donor_id = re.sub(r'[^\w가-힣.-]', '-', str(donor_id), flags=re.UNICODE)[:60] or 'unknown'
    item.update({
        'schoolCode': school_code,
        'garmentTypeCode': type_code,
        'garmentNumber': number,
        'donorId': donor_id,
        'garmentCode': f'{school_code}_{type_code}_{number:03d}_{donor_id}',
    })
    return item


def owns_garment(user, garment):
    owner_username = garment.get('ownerUsername')
    if owner_username:
        return owner_username == user['username']
    return bool(garment.get('ownerName')) and garment['ownerName'] == user['name']


def load_env_file(path=None, environ=None):
    """Load simple KEY=VALUE settings from .env without replacing shell settings."""
    target = os.environ if environ is None else environ
    env_path = Path(path) if path is not None else PROJECT_ROOT / '.env'
    try:
        lines = env_path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return target
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in stripped:
            continue
        key, value = stripped.split('=', 1)
        key = key.strip()
        value = value.strip()
        if key in {'REPLICATE_API_TOKEN', 'REPLICATE_PRODUCT_MODEL_VERSION'} and key not in target:
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            if value:
                target[key] = value
    return target


load_env_file()


def get_replicate_api_token():
    return os.environ.get('REPLICATE_API_TOKEN', '').strip()


def get_replicate_product_model_version():
    return os.environ.get('REPLICATE_PRODUCT_MODEL_VERSION', '').strip()


def analyze_measure_dataset(output_dir):
    """Run the OpenCV comparison for the included public sample set."""
    from tools.measure_dataset import analyze_dataset

    return analyze_dataset(PROJECT_ROOT / 'measure_dataset', output_dir)


def now_text():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def write_json_safely(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_bytes_safely(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_bytes(value)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None


def valid_run_id(value):
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def valid_garment_record_id(value):
    return valid_run_id(value)


def decode_photo_data(value):
    if not isinstance(value, str) or not value.startswith('data:image/jpeg;base64,'):
        return None
    try:
        return base64.b64decode(value.split(',', 1)[1], validate=True)
    except (binascii.Error, ValueError):
        return None


def decode_png_data(value):
    if not isinstance(value, str) or not value.startswith('data:image/png;base64,'):
        return None
    try:
        photo = base64.b64decode(value.split(',', 1)[1], validate=True)
    except (binascii.Error, ValueError):
        return None
    return photo if photo.startswith(b'\x89PNG\r\n\x1a\n') else None


def make_garment_thumbnail(png_bytes, maximum_side=200):
    """Make a small transparent PNG for closet cards; keep the stored original cutout unchanged."""
    try:
        import cv2
        import numpy as np
    except ImportError as error:
        raise RuntimeError('옷 사진을 줄이는 도구를 불러오지 못했어요.') from error
    image = cv2.imdecode(np.frombuffer(png_bytes, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None or image.ndim != 3 or image.shape[2] != 4:
        raise ValueError('투명한 옷 사진이 올바르지 않아요.')
    height, width = image.shape[:2]
    scale = min(1, maximum_side / max(height, width))
    if scale < 1:
        image = cv2.resize(image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode('.png', image, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    if not ok:
        raise RuntimeError('작은 옷장 사진을 만들지 못했어요.')
    return encoded.tobytes()


def make_source_photo_thumbnail(jpeg_bytes, maximum_side=200):
    try:
        import cv2
        import numpy as np
    except ImportError as error:
        raise RuntimeError('사진 축소 도구를 불러오지 못했어요.') from error
    image = cv2.imdecode(np.frombuffer(jpeg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError('원본 사진을 읽지 못했어요.')
    height, width = image.shape[:2]
    scale = min(1, maximum_side / max(height, width))
    if scale < 1:
        image = cv2.resize(image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 78])
    if not ok:
        raise RuntimeError('사진 미리보기를 만들지 못했어요.')
    return encoded.tobytes()


def ensure_garment_cutout(garments_dir, garment_id, metadata_lock=None, *, for_product_photo=False):
    garment_dir = Path(garments_dir) / garment_id
    cutout_path = garment_dir / ('product-cutout.png' if for_product_photo else 'closet.png')
    version_key = 'productMaskPipelineVersion' if for_product_photo else 'maskPipelineVersion'
    version = PRODUCT_MASK_PIPELINE_VERSION if for_product_photo else GARMENT_MASK_PIPELINE_VERSION
    item = read_json(garment_dir / 'garment.json')
    if not item:
        raise FileNotFoundError('옷 사진을 찾지 못했어요.')
    try:
        if item.get(version_key) == version:
            return cutout_path.read_bytes()
    except OSError:
        pass
    from tools.garment_segmentation import make_product_cutout
    from tools.vision_pipeline import rectify_photo

    source = (garment_dir / 'photo.jpg').read_bytes()
    if item.get('closetPhotoSource') == 'provided-garment-only':
        import cv2
        import numpy as np
        image = cv2.imdecode(np.frombuffer(source, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError('원본 사진을 읽지 못했어요.')
    else:
        flattened = rectify_photo(source, item.get('backgroundId', 'M4'), preserve_resolution=True,
                                  max_output_long_side=848)
        if not flattened.get('ok'):
            raise ValueError(flattened.get('message', '옷 이미지를 만들지 못했어요.'))
        image = flattened['image']
    category = item.get('category')
    if category == 'pants' and item.get('bottomLength') in {'반바지', 'shorts'}:
        category = 'shorts'
    photo = make_product_cutout(image, garment_id=item.get('garmentTypeId'), category=category)
    if photo is None:
        raise ValueError('투명 옷장 사진을 만들지 못했어요.')
    write_bytes_safely(cutout_path, photo)
    with metadata_lock if metadata_lock is not None else nullcontext():
        latest = read_json(garment_dir / 'garment.json')
        if not latest:
            raise FileNotFoundError('옷 사진을 찾지 못했어요.')
        latest[version_key] = version
        if not for_product_photo:
            latest['thumbnailVersion'] = 0
        write_json_safely(garment_dir / 'garment.json', latest)
        if not for_product_photo:
            index_path = Path(garments_dir) / 'index.json'
            index = read_json(index_path)
            if isinstance(index, list):
                for record in index:
                    if isinstance(record, dict) and record.get('id') == garment_id:
                        record['maskPipelineVersion'] = version
                        record['thumbnailVersion'] = 0
                        break
                write_json_safely(index_path, index)
    return photo


def get_garment_thumbnail_bytes(garments_dir, garment_id, metadata_lock=None):
    garment_dir = Path(garments_dir) / garment_id
    item = read_json(garment_dir / 'garment.json')
    if not item:
        raise FileNotFoundError('옷 사진을 찾지 못했어요.')
    thumbnail_path = garment_dir / 'closet-thumbnail.png'
    try:
        if (item.get('thumbnailVersion') == THUMBNAIL_VERSION
                and item.get('maskPipelineVersion') == GARMENT_MASK_PIPELINE_VERSION):
            return thumbnail_path.read_bytes()
        cutout = ensure_garment_cutout(garments_dir, garment_id, metadata_lock)
        thumbnail = make_garment_thumbnail(cutout)
        write_bytes_safely(thumbnail_path, thumbnail)
        with metadata_lock if metadata_lock is not None else nullcontext():
            latest = read_json(garment_dir / 'garment.json') or {}
            latest['thumbnailVersion'] = THUMBNAIL_VERSION
            write_json_safely(garment_dir / 'garment.json', latest)
            index_path = Path(garments_dir) / 'index.json'
            index = read_json(index_path)
            if isinstance(index, list):
                for record in index:
                    if isinstance(record, dict) and record.get('id') == garment_id:
                        record['thumbnailVersion'] = THUMBNAIL_VERSION
                        break
                write_json_safely(index_path, index)
        return thumbnail
    except OSError:
        raise


def get_saved_garment_preview_bytes(garments_dir, garment_id):
    """Resize saved previews only; listing clothes must never start AI segmentation."""
    garment_dir = Path(garments_dir) / garment_id
    item = read_json(garment_dir / 'garment.json')
    # Explicit cutout refresh invalidates the old thumbnail; resize the new cutout instead.
    invalidated = isinstance(item, dict) and item.get('thumbnailVersion') == 0
    filenames = ('closet.png',) if invalidated else ('closet-thumbnail.png', 'closet.png')
    for filename in filenames:
        try:
            saved = (garment_dir / filename).read_bytes()
            if saved:
                return make_garment_thumbnail(saved)
        except (OSError, ValueError):
            continue
    raise FileNotFoundError('저장된 옷장 미리보기가 없어요.')


def get_product_thumbnail_bytes(garments_dir, garment_id):
    garment_dir = Path(garments_dir) / garment_id
    item = read_json(garment_dir / 'garment.json')
    filename = item.get('productPhotoFile') if isinstance(item, dict) else None
    if filename not in {'product-photo.png', 'product-photo.jpg', 'product-photo.webp'}:
        raise FileNotFoundError('아직 AI 상품 사진이 없어요.')
    source_path = garment_dir / filename
    thumbnail_path = garment_dir / 'product-thumbnail.jpg'
    source_time = source_path.stat().st_mtime_ns
    try:
        if thumbnail_path.stat().st_mtime_ns >= source_time:
            return thumbnail_path.read_bytes()
    except FileNotFoundError:
        pass
    thumbnail = make_source_photo_thumbnail(source_path.read_bytes())
    write_bytes_safely(thumbnail_path, thumbnail)
    return thumbnail


def create_server(host='0.0.0.0', port=8000, data_dir=DEFAULT_DATA_DIR, tls_certfile=None, tls_keyfile=None, tls_port=None):
    """Create optional local HTTPS listeners; Vercel uses the handler directly."""
    if bool(tls_certfile) != bool(tls_keyfile):
        raise ValueError('TLS certificate and key must both be supplied.')
    tls_context = None
    if tls_certfile:
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls_context.load_cert_chain(certfile=str(tls_certfile), keyfile=str(tls_keyfile))
    handler_class = create_handler_class(data_dir)
    httpd = ThreadingHTTPServer((host, port), handler_class)
    httpd.tls_server = None
    try:
        if tls_context is not None:
            if tls_port is None:
                raise ValueError('TLS port must be supplied when TLS certificate and key are supplied.')
            tls_server = ThreadingHTTPServer((host, tls_port), handler_class)
            try:
                tls_server.socket = tls_context.wrap_socket(tls_server.socket, server_side=True)
            except Exception:
                tls_server.server_close()
                raise
            tls_server.garment_metadata_lock = handler_class.garment_metadata_lock
            tls_server.product_generation_locks = handler_class.product_generation_locks
            httpd.tls_server = tls_server
    except Exception:
        httpd.server_close()
        raise
    httpd.garment_metadata_lock = handler_class.garment_metadata_lock
    httpd.product_generation_locks = handler_class.product_generation_locks
    return httpd


def create_handler_class(data_dir=DEFAULT_DATA_DIR):
    """Build the request handler; Vercel (api/index.py) uses it without starting a server."""
    storage = Path(data_dir).resolve()
    storage.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    product_generation_locks = {}
    product_generation_locks_guard = threading.Lock()
    experiments_dir = storage / 'experiments'
    garments_dir = storage / 'garments'
    accounts = AccountStore(storage)

    class FitShareHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(PROJECT_ROOT), **kwargs)

        def log_message(self, format_string, *args):
            # 기본 접속 기록은 간단한 상태 확인에만 사용합니다.
            super().log_message(format_string, *args)

        def translate_path(self, path):
            parts = [unquote(part) for part in urlsplit(path).path.split('/') if part]
            if any(part in {'.git', 'server-data', '__pycache__'} for part in parts):
                return str(PROJECT_ROOT / '__blocked_file__')
            return super().translate_path(path)

        def do_GET(self):
            parts = self.api_parts()
            if parts is None:
                return super().do_GET()
            try:
                if parts == ['experiments']:
                    return self.list_experiments()
                if parts == ['measure-dataset']:
                    return self.get_measure_dataset_report()
                if parts == ['measure-dataset', 'model-comparison']:
                    return self.get_measure_dataset_model_comparison()
                if parts == ['measure-dataset', 'round3']:
                    report = read_json(storage / 'measure-dataset-report' / 'round3.json')
                    if report is None:
                        return self.send_json(404, {'error': '3차 실험 결과가 아직 없어요. 먼저 전체 실험을 실행해 주세요.'})
                    gemini_report = read_json(storage / 'measure-dataset-report' / 'gemini-results.json')
                    if gemini_report is not None:
                        report['gemini'] = gemini_report
                    return self.send_json(200, report)
                if parts == ['auth', 'me']:
                    return self.send_json(200, {'user': self.current_user()})
                if parts == ['demo-ca']:
                    return self.get_demo_ca_certificate()
                if parts == ['ranking']:
                    return self.get_ranking()
                if parts == ['garments']:
                    return self.list_garments()
                if parts == ['clothing-wishes']:
                    return self.handle_clothing_wishes('GET')
                if len(parts) == 2 and parts[0] == 'garments':
                    return self.get_garment(parts[1])
                if len(parts) == 3 and parts[0] == 'garments' and parts[2] == 'product-photo':
                    return self.get_garment_product_photo(parts[1])
                if len(parts) == 3 and parts[0] == 'garments' and parts[2] == 'photo':
                    return self.get_garment_photo(parts[1])
                if len(parts) == 3 and parts[0] == 'garments' and parts[2] == 'closet-photo':
                    return self.get_garment_closet_photo(parts[1])
                if len(parts) == 3 and parts[0] == 'garments' and parts[2] == 'closet-thumbnail':
                    return self.get_garment_closet_thumbnail(parts[1])
                if len(parts) == 3 and parts[0] == 'garments' and parts[2] == 'tryon-photo':
                    return self.get_garment_tryon_photo(parts[1])
                if len(parts) == 3 and parts[0] == 'garments' and parts[2] == 'thumbnail':
                    return self.get_garment_thumbnail(parts[1])
                if len(parts) == 2 and parts[0] == 'experiments':
                    return self.get_experiment(parts[1])
                if len(parts) == 4 and parts[0] == 'experiments' and parts[2] == 'photos':
                    return self.get_photo(parts[1], parts[3])
                return self.send_json(404, {'error': '찾는 API 주소가 없어요.'})
            except OSError:
                return self.send_json(500, {'error': '실험 기록을 읽지 못했어요.'})

        def do_POST(self):
            parts = self.api_parts()
            if parts is None:
                return self.send_json(404, {'error': '저장할 API 주소가 없어요.'})
            payload = self.read_payload()
            if payload is None:
                return
            try:
                if parts == ['experiments']:
                    return self.create_experiment(payload)
                if parts == ['measure-dataset', 'run']:
                    return self.run_measure_dataset_analysis()
                if parts == ['measure-dataset', 'round3', 'run']:
                    return self.run_measure_dataset_round3()
                if parts == ['measure-dataset', 'save-as-experiment']:
                    return self.save_dataset_as_experiment()
                if parts == ['measure-dataset', 'gemini']:
                    return self.run_gemini_dataset_analysis()
                if parts == ['measurements', 'automatic']:
                    return self.measure_uploaded_photo(payload)
                if parts and parts[0] == 'auth' and len(parts) == 2:
                    return self.handle_auth(parts[1], payload)
                if parts == ['ranking']:
                    return self.save_ranking(payload)
                if parts == ['ranking', 'teacher-handoff']:
                    return self.complete_teacher_handoff(payload)
                if parts == ['garments', 'analyze']:
                    return self.analyze_garment(payload)
                if parts == ['garments']:
                    return self.save_garment(payload)
                if parts == ['clothing-wishes']:
                    return self.handle_clothing_wishes('POST', payload=payload)
                if len(parts) == 3 and parts[0] == 'clothing-wishes' and parts[2] == 'read':
                    return self.handle_clothing_wishes('READ', parts[1])
                if len(parts) == 3 and parts[0] == 'garments' and parts[2] == 'update':
                    return self.update_garment(parts[1], payload)
                if len(parts) == 3 and parts[0] == 'garments' and parts[2] == 'product-photo':
                    return self.create_garment_product_photo(parts[1], payload)
                if len(parts) == 3 and parts[0] == 'experiments' and parts[2] == 'records':
                    return self.save_record(parts[1], payload)
                if len(parts) == 3 and parts[0] == 'experiments' and parts[2] == 'complete':
                    return self.complete_experiment(parts[1])
                return self.send_json(404, {'error': '저장할 API 주소가 없어요.'})
            except OSError:
                return self.send_json(500, {'error': 'PC에 파일을 저장하지 못했어요. 디스크 여유 공간을 확인해 주세요.'})

        def do_DELETE(self):
            parts = self.api_parts()
            if parts is None:
                return self.send_json(404, {'error': '삭제할 API 주소가 없어요.'})
            try:
                if len(parts) == 2 and parts[0] == 'clothing-wishes':
                    return self.handle_clothing_wishes('DELETE', parts[1])
                if len(parts) == 2 and parts[0] == 'garments':
                    return self.delete_garment(parts[1])
                if len(parts) == 3 and parts[0] == 'garments' and parts[2] == 'product-photo':
                    return self.delete_product_photo(parts[1])
                return self.send_json(404, {'error': '삭제할 API 주소가 없어요.'})
            except OSError:
                return self.send_json(500, {'error': '옷 사진과 정보를 삭제하지 못했어요. 저장 공간 권한을 확인해 주세요.'})

        def handle_clothing_wishes(self, action, wish_id=None, payload=None):
            user = self.current_user()
            if user is None:
                return self.send_json(401, {'error': '로그인하면 원하는 옷 알림을 저장하고 확인할 수 있어요.'})
            path = storage / 'clothing-wishes.json'
            with lock:
                wishes = read_json(path)
                if wishes is None and path.exists():
                    return self.send_json(500, {'error': '저장한 알림 기록을 읽지 못했어요. 기록을 확인해 주세요.'})
                wishes = [] if wishes is None else wishes
                records = read_json(garments_dir / 'index.json')
                if records is None and (garments_dir / 'index.json').exists():
                    return self.send_json(500, {'error': '등록된 옷 목록을 읽지 못했어요. 잠시 후 다시 시도해 주세요.'})
                records = [] if records is None else records
                if not valid_store(wishes) or not isinstance(records, list) or any(not isinstance(item, dict) or not isinstance(item.get('id'), str) for item in records):
                    return self.send_json(500, {'error': '알림 기록을 읽지 못했어요. 잠시 후 다시 시도해 주세요.'})
                owned = [wish for wish in wishes if wish['username'] == user['username']]
                if action == 'GET':
                    return self.send_json(200, {'wishes': [public_wish(wish, records) for wish in owned]})
                if action == 'POST':
                    try:
                        filters = clean_filters(payload.get('filters'))
                    except ValueError as error:
                        return self.send_json(400, {'error': str(error)})
                    if any(wish['filters'] == filters for wish in owned):
                        return self.send_json(409, {'error': '이미 같은 조건으로 알림을 받고 있어요.'})
                    if len(owned) >= 20:
                        return self.send_json(400, {'error': '알림 조건은 최대 20개까지 저장할 수 있어요. 필요 없는 조건을 삭제해 주세요.'})
                    wish = new_wish(user['username'], filters, records)
                    wishes.append(wish)
                else:
                    wish = next((wish for wish in owned if wish['id'] == wish_id), None)
                    if wish is None:
                        return self.send_json(404, {'error': '저장한 알림 조건을 찾지 못했어요.'})
                    if action == 'DELETE':
                        wishes.remove(wish)
                    elif action == 'READ':
                        wish['readIds'] = list(set(wish['readIds']) | {str(item['id']) for item in public_wish(wish, records)['matches']})
                write_json_safely(path, wishes)
                return self.send_json(201 if action == 'POST' else 200, {'wishes': [public_wish(wish, records) for wish in wishes if wish['username'] == user['username']]})

        def api_parts(self):
            path = urlsplit(self.path).path
            if not path.startswith('/api/'):
                return None
            return [unquote(part) for part in path.strip('/').split('/')[1:]]

        def get_demo_ca_certificate(self):
            path = storage / 'certs' / 'fitshare-demo-ca.crt'
            if not path.is_file():
                return self.send_json(404, {'error': '데모 HTTPS 인증서를 찾지 못했어요.'})
            body = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'application/x-x509-ca-cert')
            self.send_header('Content-Disposition', 'attachment; filename="fitshare-demo-ca.crt"')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def read_payload(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                self.send_json(400, {'error': '사진 요청 크기를 확인할 수 없어요.'})
                return None
            if length <= 0:
                self.send_json(400, {'error': '저장할 내용이 비어 있어요.'})
                return None
            if length > MAX_BODY_BYTES:
                self.send_json(413, {'error': '사진 전송 크기가 너무 커요. 사진 파일 크기를 줄여 다시 골라 주세요. 측정 전 자동 축소는 하지 않아요.'})
                return None
            try:
                value = json.loads(self.rfile.read(length).decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_json(400, {'error': '보내는 내용을 읽지 못했어요.'})
                return None
            if not isinstance(value, dict):
                self.send_json(400, {'error': '저장 내용의 모양이 올바르지 않아요.'})
                return None
            return value

        def session_token(self):
            try:
                cookie = SimpleCookie(self.headers.get('Cookie', ''))
            except Exception:
                return ''
            return cookie['fitshare_session'].value if 'fitshare_session' in cookie else ''

        def current_user(self):
            return accounts.user_for_token(self.session_token())

        def cookie_header(self, token, max_age):
            return f'fitshare_session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={max_age}'

        def handle_auth(self, action, payload):
            try:
                if action == 'signup':
                    user = accounts.signup(payload.get('username'), payload.get('password'), payload.get('name'),
                                           teacher_code=payload.get('teacherCode'))
                    token = accounts.create_session(user['username'])
                    return self.send_json(201, {'user': user}, {'Set-Cookie': self.cookie_header(token, SESSION_SECONDS)})
                if action == 'login':
                    user = accounts.login(payload.get('username'), payload.get('password'))
                    token = accounts.create_session(user['username'])
                    return self.send_json(200, {'user': user}, {'Set-Cookie': self.cookie_header(token, SESSION_SECONDS)})
                if action == 'logout':
                    accounts.destroy_session(self.session_token())
                    return self.send_json(200, {'user': None}, {'Set-Cookie': self.cookie_header('', 0)})
                if action == 'teacher-code':
                    user = self.current_user()
                    if user is None:
                        return self.send_json(401, {'error': '로그인해 주세요.'})
                    accounts.change_teacher_code(user['username'], payload.get('newCode'))
                    return self.send_json(200, {'ok': True})
                if action == 'reset-student-password':
                    user = self.current_user()
                    if user is None:
                        return self.send_json(401, {'error': '로그인해 주세요.'})
                    accounts.reset_student_password(user['username'], payload.get('username'), payload.get('newPassword'))
                    return self.send_json(200, {'ok': True})
                if action == 'change-password':
                    user = self.current_user()
                    if user is None:
                        return self.send_json(401, {'error': '로그인해 주세요.'})
                    accounts.change_own_password(user['username'], payload.get('currentPassword'),
                                                 payload.get('newPassword'))
                    return self.send_json(200, {'ok': True}, {'Set-Cookie': self.cookie_header('', 0)})
            except AuthError as error:
                return self.send_json(error.status, {'error': str(error)})
            return self.send_json(404, {'error': '찾는 API 주소가 없어요.'})

        def send_json(self, status, value, headers=None):
            body = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            for name, header_value in (headers or {}).items():
                self.send_header(name, header_value)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def analyze_garment(self, payload):
            background_id = payload.get('backgroundId')
            if not isinstance(background_id, str) or background_id not in VALID_MATS:
                return self.send_json(400, {'error': '배경 종류를 골라 주세요.'})
            garment_id = payload.get('garmentId')
            if isinstance(garment_id, bool) or garment_id not in GARMENT_TYPE_NAMES or garment_id == 7:
                return self.send_json(400, {'error': '옷 종류를 먼저 골라 주세요.'})
            photo_bytes = decode_photo_data(payload.get('photoDataUrl'))
            if photo_bytes is None:
                return self.send_json(400, {'error': 'JPG 사진을 선택해 주세요.'})
            if not photo_bytes or len(photo_bytes) > MAX_PHOTO_BYTES:
                return self.send_json(413, {'error': '원본 사진이 12MB를 넘었어요. 파일 크기를 줄여 다시 골라 주세요. 측정 전 자동 축소는 하지 않아요.'})
            try:
                from tools.vision_pipeline import image_to_data_url, measure_photo_with_fallback

                analysis = measure_photo_with_fallback(
                    photo_bytes, background_id, garment_id=garment_id,
                    category=GARMENT_TYPE_CATEGORIES[garment_id], include_closet_image=True,
                    allow_fallback=False)
                flattened = analysis['rectified']
                measured = analysis['measurement']
            except (ImportError, RuntimeError) as error:
                return self.send_json(503, {'error': str(error) or '사진 분석 도구를 준비하지 못했어요.'})
            except (ValueError, OSError) as error:
                message = str(error).strip() or '옷을 찾지 못했어요. 매트 전체와 옷이 보이게 다시 찍어 주세요.'
                return self.send_json(422, {'error': message})
            width_label = '허리 너비' if measured['widthKind'] == 'waist' else '가슴 너비'
            return self.send_json(200, {
                'ok': True,
                'garmentId': garment_id,
                'garmentName': GARMENT_TYPE_NAMES[garment_id],
                'category': GARMENT_TYPE_CATEGORIES[garment_id],
                'lengthCm': measured['measuredLengthCm'],
                'widthCm': measured['measuredWidthCm'],
                'widthKind': measured['widthKind'],
                'widthLabel': width_label,
                'maskMethods': measured['maskMethods'],
                'colorName': measured['colorName'],
                'box': measured['box'],
                'confidence': measured['confidence'],
                'analysisPhotoDataUrl': measured['overlayDataUrl'],
                'closetImageDataUrl': measured['closetImageDataUrl'],
                'fullPhotoDataUrl': image_to_data_url(flattened['image']),
                'rectificationMethod': flattened['method'],
                'message': measured['message'] + (' 첫 분석이 실패해 600px 대체 방식으로 측정했어요. 결과를 확인해 주세요.' if analysis['fallbackUsed'] else ''),
                'fallbackUsed': analysis['fallbackUsed'],
                'fallbackReason': analysis['fallbackReason'],
                'pipelineMode': analysis['pipelineMode'],
            })

        def get_ranking(self):
            # 나눔·예약·점수 기록을 모든 접속자가 같이 보도록 PC(server-data)에 한 파일로 둡니다.
            record = read_json(storage / 'ranking.json')
            if not isinstance(record, dict) or not isinstance(record.get('state'), dict):
                record = {'rev': 0, 'state': None}
            return self.send_json(200, {'rev': record.get('rev', 0), 'state': record['state']})

        def save_ranking(self, payload):
            user = self.current_user()
            if user is None:
                return self.send_json(401, {'error': '로그인한 뒤에 나눔 기록을 저장할 수 있어요.'})
            state = payload.get('state')
            base_rev = payload.get('baseRev')
            if (not isinstance(state, dict) or isinstance(base_rev, bool) or not isinstance(base_rev, int)
                    or any(not isinstance(state.get(name), list) for name in ('users', 'exchanges', 'badges', 'finalizedMonths'))):
                return self.send_json(400, {'error': '랭킹 기록의 모양이 올바르지 않아요.'})
            with lock:
                record = read_json(storage / 'ranking.json')
                current = record.get('rev', 0) if isinstance(record, dict) else 0
                if base_rev != current:
                    # 다른 사람이 먼저 저장했어요. 화면이 최신 기록을 받아 다시 시도합니다.
                    return self.send_json(409, {'error': '다른 친구가 먼저 저장했어요.', 'rev': current,
                                                'state': record.get('state') if isinstance(record, dict) else None})
                previous_state = record.get('state') if isinstance(record, dict) else None
                if user.get('role') != 'teacher' and not self.student_can_confirm_own_handoff(previous_state, state, user):
                    return self.send_json(403, {'error': '다른 친구의 나눔 완료는 선생님만 표시할 수 있어요.'})
                write_json_safely(storage / 'ranking.json', {'rev': current + 1, 'state': state})
            return self.send_json(200, {'rev': current + 1})

        @staticmethod
        def student_can_confirm_own_handoff(previous_state, next_state, user):
            if not isinstance(previous_state, dict):
                return True
            previous_exchanges = previous_state.get('exchanges', [])
            next_exchanges = next_state.get('exchanges', [])
            next_by_id = {item.get('id'): item for item in next_exchanges if isinstance(item, dict)}
            previous_users = {item.get('id'): item.get('name') for item in previous_state.get('users', []) if isinstance(item, dict)}
            for previous in previous_exchanges:
                if not isinstance(previous, dict) or previous.get('status') != 'reserved':
                    continue
                updated = next_by_id.get(previous.get('id'))
                if not updated or updated.get('status') != 'completed':
                    continue
                if any(updated.get(key) != previous.get(key) for key in ('giverId', 'receiverId', 'garmentId', 'itemName')):
                    return False
                if previous_users.get(previous.get('receiverId')) != user.get('name'):
                    return False
            return True

        def complete_teacher_handoff(self, payload):
            user = self.current_user()
            if user is None:
                return self.send_json(401, {'error': '선생님 계정으로 로그인해 주세요.'})
            if user.get('role') != 'teacher':
                return self.send_json(403, {'error': '선생님만 전달 완료를 표시할 수 있어요.'})
            exchange_id = payload.get('exchangeId')
            base_rev = payload.get('baseRev')
            if not isinstance(exchange_id, str) or not exchange_id or isinstance(base_rev, bool) or not isinstance(base_rev, int):
                return self.send_json(400, {'error': '완료할 예약 정보가 올바르지 않아요.'})
            with lock:
                record = read_json(storage / 'ranking.json')
                current = record.get('rev', 0) if isinstance(record, dict) else 0
                state = record.get('state') if isinstance(record, dict) else None
                if base_rev != current:
                    return self.send_json(409, {'error': '다른 친구가 먼저 기록했어요.', 'rev': current, 'state': state})
                if not isinstance(state, dict) or not isinstance(state.get('exchanges'), list):
                    return self.send_json(404, {'error': '나눔 예약을 찾을 수 없어요.'})
                exchange = next((item for item in state['exchanges'] if isinstance(item, dict) and item.get('id') == exchange_id), None)
                if exchange is None:
                    return self.send_json(404, {'error': '나눔 예약을 찾을 수 없어요.'})
                if exchange.get('status') != 'reserved':
                    return self.send_json(409, {'error': '전달 대기 중인 예약만 완료로 표시할 수 있어요.'})
                completed_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                completed_exchange = {**exchange, 'status': 'completed', 'completedAt': completed_at}
                next_state = {**state, 'exchanges': [completed_exchange if isinstance(item, dict) and item.get('id') == exchange_id else item
                                                       for item in state['exchanges']]}
                write_json_safely(storage / 'ranking.json', {'rev': current + 1, 'state': next_state})
            return self.send_json(200, {'rev': current + 1, 'state': next_state})

        def ensure_garment_codes(self, items):
            sequence_path = garments_dir / '.sequences.json'
            sequences = read_json(sequence_path)
            if not isinstance(sequences, dict):
                sequences = {}
            changed = False
            sequences_changed = not sequence_path.exists()
            for item in items:
                if not isinstance(item, dict):
                    continue
                school_code = clean_school_code(item.get('schoolCode'))
                type_code = GARMENT_TYPE_CODES.get(item.get('garmentTypeId'), 'e')
                number = item.get('garmentNumber')
                if isinstance(number, int) and not isinstance(number, bool) and number > 0:
                    key = f'{school_code}|{type_code}'
                    try:
                        previous_number = int(sequences.get(key, 0))
                    except (TypeError, ValueError):
                        previous_number = 0
                    if number > previous_number:
                        sequences[key] = number
                        sequences_changed = True
            legacy = sorted(
                ((index, item) for index, item in enumerate(items)
                 if isinstance(item, dict) and not (isinstance(item.get('garmentNumber'), int)
                                                    and isinstance(item.get('garmentCode'), str))),
                key=lambda pair: (str(pair[1].get('createdAt', '')), pair[0]),
            )
            for _, item in legacy:
                allocate_garment_code(item, sequences)
                sequences_changed = True
                item_path = garments_dir / str(item.get('id', '')) / 'garment.json'
                if valid_garment_record_id(item.get('id')) and item_path.is_file():
                    write_json_safely(item_path, item)
                changed = True
            if changed:
                write_json_safely(garments_dir / 'index.json', items)
            if sequences_changed:
                write_json_safely(sequence_path, sequences)
            return items

        def list_garments(self):
            with lock:
                items = read_json(garments_dir / 'index.json') or []
                if isinstance(items, list):
                    items = self.ensure_garment_codes(items)
            return self.send_json(200, items if isinstance(items, list) else [])

        def get_garment(self, garment_id):
            if not valid_garment_record_id(garment_id):
                return self.send_json(404, {'error': '등록한 옷을 찾지 못했어요.'})
            item = read_json(garments_dir / garment_id / 'garment.json')
            if not item:
                return self.send_json(404, {'error': '등록한 옷을 찾지 못했어요.'})
            with lock:
                index = read_json(garments_dir / 'index.json') or []
                if isinstance(index, list):
                    self.ensure_garment_codes(index)
                    item = read_json(garments_dir / garment_id / 'garment.json') or item
            return self.send_json(200, item)

        def get_garment_photo(self, garment_id):
            if not valid_garment_record_id(garment_id):
                return self.send_json(404, {'error': '사진을 찾지 못했어요.'})
            photo_path = garments_dir / garment_id / 'photo.jpg'
            try:
                photo = photo_path.read_bytes()
            except OSError:
                return self.send_json(404, {'error': '사진을 찾지 못했어요.'})
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', str(len(photo)))
            self.send_header('Cache-Control', 'public, max-age=3600')
            self.end_headers()
            self.wfile.write(photo)

        def get_garment_closet_photo(self, garment_id):
            if not valid_garment_record_id(garment_id):
                return self.send_json(404, {'error': '?? ??? ?? ????.'})
            try:
                photo = ensure_garment_cutout(garments_dir, garment_id, lock)
            except FileNotFoundError as error:
                return self.send_json(404, {'error': str(error)})
            except (OSError, ValueError, RuntimeError, ImportError) as error:
                return self.send_json(422, {'error': str(error) or '?? ?? ??? ??? ????.'})
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(photo)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(photo)

        def get_garment_tryon_photo(self, garment_id):
            if not valid_garment_record_id(garment_id):
                return self.send_json(404, {'error': '옷을 찾지 못했어요.'})
            item = read_json(garments_dir / garment_id / 'garment.json')
            if not item:
                return self.send_json(404, {'error': '옷이 삭제되었거나 정보를 찾을 수 없어요.'})
            if item.get('category') not in ('top', 'pants'):
                return self.send_json(422, {'error': '상의와 하의만 입어볼 수 있어요.'})
            try:
                from tools.tryon_photo import make_tryon_photo, original_tryon_source
                photo = None
                try:
                    # 옷장·AI 상품 사진과 같은 YOLO+SAM 투명 컷아웃을 먼저 써요.
                    cutout = ensure_garment_cutout(garments_dir, garment_id, lock)
                    photo = make_tryon_photo(cutout, item.get('category'), item.get('bottomLength', ''))
                except Exception as error:
                    # 컷아웃이 없거나 YOLO·SAM 모델을 쓸 수 없으면 원본 사진에서 예전 방식으로 옷을 분리해요.
                    self.log_error('YOLO+SAM try-on cutout unavailable for %s: %s', garment_id, error)
                if photo is None:
                    source = original_tryon_source(garments_dir / garment_id, item)
                    photo = make_tryon_photo(source, item.get('category'), item.get('bottomLength', ''))
            except (OSError, ValueError, RuntimeError, ImportError) as error:
                return self.send_json(422, {'error': str(error) or '입어보기용 사진을 준비하지 못했어요.'})
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(photo)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(photo)

        def get_garment_thumbnail(self, garment_id):
            if not valid_garment_record_id(garment_id):
                return self.send_json(404, {'error': '옷 사진을 찾지 못했어요.'})
            thumbnail = None
            content_type = 'image/jpeg'
            cache_control = 'no-cache'
            try:
                thumbnail = get_product_thumbnail_bytes(garments_dir, garment_id)
            except FileNotFoundError:
                pass
            except (OSError, ValueError, RuntimeError, ImportError) as error:
                self.log_error('AI thumbnail unavailable for %s: %s', garment_id, error)
            if thumbnail is None:
                try:
                    thumbnail = get_saved_garment_preview_bytes(garments_dir, garment_id)
                    content_type = 'image/png'
                except FileNotFoundError:
                    pass
                except (OSError, ValueError, RuntimeError, ImportError) as error:
                    self.log_error('Closet thumbnail unavailable for %s: %s', garment_id, error)
            if thumbnail is None:
                try:
                    source = (garments_dir / garment_id / 'photo.jpg').read_bytes()
                    thumbnail = make_source_photo_thumbnail(source)
                    content_type = 'image/jpeg'
                    cache_control = 'no-store'
                except FileNotFoundError:
                    return self.send_json(404, {'error': '옷 원본 사진을 찾지 못했어요.'})
                except (OSError, ValueError, RuntimeError, ImportError) as error:
                    return self.send_json(422, {'error': str(error) or '사진 미리보기를 만들지 못했어요.'})
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(thumbnail)))
            self.send_header('Cache-Control', cache_control)
            self.end_headers()
            self.wfile.write(thumbnail)

        def get_garment_closet_thumbnail(self, garment_id):
            if not valid_garment_record_id(garment_id):
                return self.send_json(404, {'error': '옷 사진을 찾지 못했어요.'})
            try:
                thumbnail = get_garment_thumbnail_bytes(garments_dir, garment_id, lock)
            except FileNotFoundError:
                return self.send_json(404, {'error': '옷 사진을 찾지 못했어요.'})
            except (OSError, ValueError, RuntimeError, ImportError) as error:
                self.log_error('YOLO+SAM closet thumbnail unavailable for %s: %s', garment_id, error)
                return self.send_json(422, {'error': str(error) or '옷만 보기 썸네일을 만들지 못했어요.'})
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(thumbnail)))
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(thumbnail)

        def get_garment_product_photo(self, garment_id):
            user = self.current_user()
            if user is None:
                return self.send_json(401, {'error': '로그인해 주세요.'})
            if user.get('role') != 'teacher':
                return self.send_json(403, {'error': '교사만 AI 상품 사진을 볼 수 있어요.'})
            if not valid_garment_record_id(garment_id):
                return self.send_json(404, {'error': '옷 사진을 찾지 못했어요.'})
            garment_dir = garments_dir / garment_id
            item = read_json(garment_dir / 'garment.json')
            filename = item.get('productPhotoFile') if isinstance(item, dict) else None
            if filename not in {'product-photo.png', 'product-photo.jpg', 'product-photo.webp'}:
                return self.send_json(404, {'error': '아직 AI 상품 사진이 없어요.'})
            path = garment_dir / filename
            try:
                photo = path.read_bytes()
            except OSError:
                return self.send_json(404, {'error': '저장된 AI 상품 사진을 찾지 못했어요.'})
            mime = {'png': 'image/png', 'jpg': 'image/jpeg', 'webp': 'image/webp'}[path.suffix[1:]]
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(photo)))
            self.send_header('Cache-Control', 'private, no-cache')
            self.end_headers()
            self.wfile.write(photo)

        def create_garment_product_photo(self, garment_id, payload):
            user = self.current_user()
            if user is None:
                return self.send_json(401, {'error': '로그인해 주세요.'})
            regenerate = payload.get('regenerate', False)
            if not isinstance(regenerate, bool):
                return self.send_json(400, {'error': '재생성 여부는 true 또는 false로 보내 주세요.'})
            if regenerate and user.get('role') != 'teacher':
                return self.send_json(403, {'error': '선생님만 AI 사진을 다시 만들 수 있어요.'})
            if not valid_garment_record_id(garment_id):
                return self.send_json(404, {'error': '옷 사진을 찾지 못했어요.'})
            garment_dir = garments_dir / garment_id
            item = read_json(garment_dir / 'garment.json')
            if not isinstance(item, dict):
                return self.send_json(404, {'error': '등록된 옷을 찾지 못했어요.'})
            if user.get('role') != 'teacher' and not owns_garment(user, item):
                return self.send_json(403, {'error': '내가 등록한 옷의 AI 사진만 처음 만들 수 있어요.'})
            with product_generation_locks_guard:
                generation_lock = product_generation_locks.setdefault(garment_id, threading.Lock())
            if not generation_lock.acquire(blocking=not regenerate):
                return self.send_json(409, {'error': '이미 이 옷의 AI 사진을 만들고 있어요. 잠시 기다려 주세요.'})
            try:
                return self._create_garment_product_photo_locked(garment_id, regenerate=regenerate)
            finally:
                generation_lock.release()

        def _create_garment_product_photo_locked(self, garment_id, *, regenerate=False):
            user = self.current_user()
            if user is None:
                return self.send_json(401, {'error': '로그인해 주세요.'})
            if not valid_garment_record_id(garment_id):
                return self.send_json(404, {'error': '옷 사진을 찾지 못했어요.'})
            garment_dir = garments_dir / garment_id
            item_path = garment_dir / 'garment.json'
            item = read_json(item_path)
            if not item:
                return self.send_json(404, {'error': '등록된 옷을 찾지 못했어요.'})
            if user.get('role') != 'teacher' and (regenerate or not owns_garment(user, item)):
                return self.send_json(403, {'error': '내가 등록한 옷의 AI 사진만 처음 만들 수 있어요.'})
            if not regenerate and item.get('productPhotoFile') in {'product-photo.png', 'product-photo.jpg', 'product-photo.webp'}:
                image_path = garment_dir / item['productPhotoFile']
                if image_path.is_file():
                    return self.send_json(200, {'productPhotoUrl': f'/api/garments/{garment_id}/product-photo',
                                                'generatedAt': item.get('productPhotoGeneratedAt')})
            if item.get('productPhotoGeneratedAt') and user.get('role') != 'teacher':
                return self.send_json(403, {'error': '저장된 AI 사진이 없어요. 선생님에게 재생성을 요청해 주세요.'})
            token = get_replicate_api_token()
            model_version = get_replicate_product_model_version()
            if not token or not model_version:
                return self.send_json(503, {'error': 'AI 상품 사진 기능을 쓰려면 서버에 Replicate 토큰과 모델 버전을 설정해야 해요.'})
            try:
                from tools.replicate_product_photo import ProductPhotoError, generate_product_photo

                source = ensure_garment_cutout(garments_dir, garment_id, lock, for_product_photo=True)
                prompt = ('이 사진의 옷을 온라인 상점에 어울리는 깔끔한 스튜디오 상품 사진으로 만들어 주세요. '
                          '배경은 선명하고 즐거운 무지개 그라데이션으로 만들고, 무지개 색이 옷의 색상이나 원단에 영향을 주지 않게 해 주세요. '
                          '사진 속 옷의 종류와 실루엣, 색상, 원단 질감, 무늬, 로고, 봉제선, 단추, 펼쳐 놓은 자세와 비율을 그대로 보존해 주세요. '
                          '무늬와 장식은 원본 사진에서 실제로 보이는 것만 유지해 주세요. '
                          '민무늬인 부분이나 숨겨지거나 가려진 부분에 줄무늬, 무늬, 로고 또는 장식을 새로 만들거나 복사하거나 이어 그리지 마세요. '
                          '몸판의 무늬를 소매에 복사하지 말고, 보이지 않는 부분의 무늬를 추측하지 마세요. 민무늬 원단은 민무늬로 유지해 주세요. '
                          '옷 자체는 바꾸지 말고 배경과 조명만 바꿔 주세요. 소매를 접거나 옷의 자세를 바꾸지 마세요. '
                          '사람, 마네킹, 옷걸이, 글자, 라벨, 보노보노 캐릭터 또는 다른 물체를 추가하지 마세요.')
                photo, mime = generate_product_photo(source, prompt, token, model_version)
            except ProductPhotoError as error:
                return self.send_json(502, {'error': str(error)})
            except FileNotFoundError:
                return self.send_json(404, {'error': '옷 원본 사진을 찾지 못했어요.'})
            except (OSError, ValueError, RuntimeError, ImportError) as error:
                return self.send_json(422, {'error': str(error) or 'AI 상품 사진을 만들지 못했어요.'})
            extension = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp'}.get(mime)
            if extension is None:
                return self.send_json(502, {'error': '이미지 서비스가 지원하지 않는 사진 형식을 보냈어요.'})
            filename = f'product-photo.{extension}'
            temporary = garment_dir / f'.{filename}.{uuid.uuid4().hex}.tmp'
            try:
                temporary.write_bytes(photo)
                with lock:
                    index_path = garments_dir / 'index.json'
                    destination = garment_dir / filename
                    previous = {path: path.read_bytes() if path.exists() else None
                                for path in (destination, item_path, index_path)}
                    try:
                        temporary.replace(destination)
                        item = read_json(item_path) or item
                        item['productPhotoFile'] = filename
                        item['productPhotoUrl'] = f'/api/garments/{garment_id}/product-photo'
                        item['productPhotoGeneratedAt'] = datetime.now(timezone.utc).isoformat(timespec='microseconds')
                        write_json_safely(item_path, item)
                        index = read_json(index_path) or []
                        for index_item in index:
                            if isinstance(index_item, dict) and index_item.get('id') == garment_id:
                                index_item.update({key: item[key] for key in ('productPhotoFile', 'productPhotoUrl', 'productPhotoGeneratedAt')})
                                break
                        write_json_safely(index_path, index)
                        (garment_dir / 'product-thumbnail.jpg').unlink(missing_ok=True)
                    except OSError:
                        for path, content in previous.items():
                            try:
                                if content is None:
                                    path.unlink(missing_ok=True)
                                else:
                                    write_bytes_safely(path, content)
                            except OSError as error:
                                self.log_error('Could not restore product photo record %s: %s', garment_id, error)
                        raise
            except OSError:
                temporary.unlink(missing_ok=True)
                return self.send_json(500, {'error': '생성 사진을 PC에 저장하지 못했어요.'})
            return self.send_json(201, {'productPhotoUrl': item['productPhotoUrl'],
                                        'generatedAt': item['productPhotoGeneratedAt']})

        def update_garment(self, garment_id, payload):
            user = self.current_user()
            if user is None:
                return self.send_json(401, {'error': '로그인한 뒤 옷 정보를 수정할 수 있어요.'})
            if not valid_garment_record_id(garment_id):
                return self.send_json(404, {'error': '수정할 옷을 찾지 못했어요.'})
            item_path = garments_dir / garment_id / 'garment.json'
            item = read_json(item_path)
            if not item:
                return self.send_json(404, {'error': '수정할 옷을 찾지 못했어요.'})
            if user.get('role') != 'teacher' and not owns_garment(user, item):
                return self.send_json(403, {'error': '내가 올린 옷만 고칠 수 있어요.'})
            updated = dict(item)
            for key in ('name', 'category', 'gender', 'garmentTypeId', 'colorName', 'lengthCm', 'widthCm', 'ownerName'):
                if key in payload:
                    updated[key] = payload[key]
            name = updated.get('name')
            category = updated.get('category')
            gender = updated.get('gender')
            garment_type_id = updated.get('garmentTypeId')
            color_name = updated.get('colorName')
            length = updated.get('lengthCm')
            width = updated.get('widthCm')
            requested_owner = updated.get('ownerName', '')
            if not isinstance(requested_owner, str) or len(requested_owner.strip()) > 200:
                return self.send_json(400, {'error': '올린 사람 이름은 200글자까지 쓸 수 있어요.'})
            owner_name = item.get('ownerName', '')
            expected_category = GARMENT_TYPE_CATEGORIES.get(garment_type_id)
            if (not isinstance(name, str) or not name.strip() or len(name.strip()) > 60
                    or category != expected_category or category not in VALID_CATEGORIES
                    or not isinstance(gender, str) or gender not in VALID_GENDERS
                    or isinstance(garment_type_id, bool) or not isinstance(garment_type_id, int)
                    or garment_type_id not in GARMENT_TYPE_NAMES
                    or not isinstance(color_name, str) or not color_name.strip() or len(color_name) > 30
                    or not valid_measurement(length) or not valid_measurement(width)):
                return self.send_json(400, {'error': '이름, 종류, 용도, 색, 길이와 너비를 확인해 주세요.'})
            updated['name'] = name.strip()
            updated['ownerName'] = owner_name.strip()
            updated['colorName'] = color_name.strip()
            updated['lengthCm'] = round(float(length), 1)
            updated['widthCm'] = round(float(width), 1)
            updated['garmentTypeName'] = GARMENT_TYPE_NAMES[garment_type_id]
            updated['photoUrl'] = f'/api/garments/{garment_id}/photo'
            updated['closetPhotoUrl'] = f'/api/garments/{garment_id}/closet-photo'
            try:
                clean_metadata(payload, item)
            except ValueError as error:
                return self.send_json(400, {'error': str(error)})
            with lock:
                latest = read_json(item_path)
                if not isinstance(latest, dict):
                    return self.send_json(404, {'error': '수정할 옷을 찾지 못했어요.'})
                garment_index = read_json(garments_dir / 'index.json') or []
                if not isinstance(garment_index, list):
                    garment_index = []
                self.ensure_garment_codes(garment_index)
                latest = read_json(item_path)
                try:
                    metadata_update = clean_metadata(payload, latest)
                except ValueError:
                    return self.send_json(409, {'error': '다른 곳에서 옷 정보가 변경됐어요. 목록을 새로고침한 뒤 다시 수정해 주세요.'})
                type_changed = latest.get('garmentTypeId') != updated['garmentTypeId']
                for key in ('name', 'category', 'gender', 'garmentTypeId', 'colorName', 'lengthCm',
                            'widthCm', 'garmentTypeName', 'photoUrl', 'closetPhotoUrl'):
                    latest[key] = updated[key]
                latest.update(metadata_update)
                if type_changed:
                    sequences = read_json(garments_dir / '.sequences.json') or {}
                    allocate_garment_code(latest, sequences)
                    write_json_safely(garments_dir / '.sequences.json', sequences)
                updated = latest
                write_json_safely(item_path, updated)
                list_item = {key: value for key, value in updated.items() if key != 'photoUrl'}
                for position, old in enumerate(garment_index):
                    if old.get('id') == garment_id:
                        garment_index[position] = list_item
                        break
                else:
                    garment_index.insert(0, list_item)
                write_json_safely(garments_dir / 'index.json', garment_index)
            return self.send_json(200, updated)

        def delete_product_photo(self, garment_id):
            user = self.current_user()
            if user is None:
                return self.send_json(401, {'error': '로그인해 주세요.'})
            if user.get('role') != 'teacher':
                return self.send_json(403, {'error': '선생님만 AI 상품 사진을 삭제할 수 있어요.'})
            if not valid_garment_record_id(garment_id):
                return self.send_json(404, {'error': '옷 사진을 찾지 못했어요.'})
            with product_generation_locks_guard:
                generation_lock = product_generation_locks.setdefault(garment_id, threading.Lock())
            if not generation_lock.acquire(blocking=False):
                return self.send_json(409, {'error': 'AI 사진을 만들고 있어 지금은 삭제할 수 없어요. 잠시 뒤 다시 시도해 주세요.'})
            try:
                garment_dir = garments_dir / garment_id
                item_path = garment_dir / 'garment.json'
                index_path = garments_dir / 'index.json'
                keys = ('productPhotoFile', 'productPhotoUrl', 'productPhotoGeneratedAt')
                with lock:
                    item = read_json(item_path)
                    if not isinstance(item, dict):
                        return self.send_json(404, {'error': '등록된 옷을 찾지 못했어요.'})
                    if not item.get('productPhotoFile'):
                        return self.send_json(404, {'error': '삭제할 AI 상품 사진이 없어요.'})
                    for key in keys:
                        item.pop(key, None)
                    write_json_safely(item_path, item)
                    index = read_json(index_path)
                    if isinstance(index, list):
                        for record in index:
                            if isinstance(record, dict) and record.get('id') == garment_id:
                                for key in keys:
                                    record.pop(key, None)
                                break
                        write_json_safely(index_path, index)
                    # 기록을 먼저 지운 뒤 파일을 지워서, 중간에 실패해도 깨진 링크가 남지 않게 해요.
                    for name in ('product-photo.png', 'product-photo.jpg', 'product-photo.webp', 'product-thumbnail.jpg'):
                        (garment_dir / name).unlink(missing_ok=True)
            except OSError:
                return self.send_json(500, {'error': 'AI 상품 사진을 삭제하지 못했어요. 저장 공간 권한을 확인해 주세요.'})
            finally:
                generation_lock.release()
            return self.send_json(200, {'deleted': True})

        def delete_garment(self, garment_id):
            user = self.current_user()
            if user is None:
                return self.send_json(401, {'error': '로그인한 뒤 옷을 삭제할 수 있어요.'})
            is_teacher = user.get('role') == 'teacher'
            if not valid_garment_record_id(garment_id):
                return self.send_json(404, {'error': '삭제할 옷을 찾지 못했어요.'})

            with product_generation_locks_guard:
                generation_lock = product_generation_locks.setdefault(garment_id, threading.Lock())
            if not generation_lock.acquire(blocking=False):
                return self.send_json(409, {'error': 'AI 사진을 만들고 있어 지금은 삭제할 수 없어요. 잠시 뒤 다시 시도해 주세요.'})

            try:
                garment_dir = garments_dir / garment_id
                item_path = garment_dir / 'garment.json'
                garment = read_json(item_path)
                if not isinstance(garment, dict):
                    return self.send_json(404, {'error': '삭제할 옷을 찾지 못했어요.'})
                if not is_teacher and not owns_garment(user, garment):
                    return self.send_json(403, {'error': '본인이 올린 옷만 삭제할 수 있어요.'})

                index_path = garments_dir / 'index.json'
                with lock:
                    if not is_teacher:
                        ranking_path = storage / 'ranking.json'
                        ranking = read_json(ranking_path)
                        if ranking is None and ranking_path.exists():
                            return self.send_json(500, {'error': '나눔 신청 기록을 확인하지 못해 옷을 삭제할 수 없어요.'})
                        if ranking is not None:
                            state = ranking.get('state') if isinstance(ranking, dict) else None
                            exchanges = state.get('exchanges') if isinstance(state, dict) else None
                            if not isinstance(exchanges, list):
                                return self.send_json(500, {'error': '나눔 신청 기록을 확인하지 못해 옷을 삭제할 수 없어요.'})
                            if any(isinstance(exchange, dict) and exchange.get('garmentId') == garment_id
                                   for exchange in exchanges):
                                return self.send_json(409, {'error': '다른 친구가 나눔을 신청했던 옷은 삭제할 수 없어요.'})
                    index = read_json(index_path)
                    if index is None and index_path.exists():
                        return self.send_json(500, {'error': '옷 목록을 읽지 못해 삭제할 수 없어요.'})
                    if index is None:
                        index = []
                    if not isinstance(index, list):
                        return self.send_json(500, {'error': '옷 목록을 읽지 못해 삭제할 수 없어요.'})
                    updated_index = [item for item in index
                                     if not isinstance(item, dict) or item.get('id') != garment_id]
                    previous_index = index_path.read_bytes() if index_path.exists() else None
                    deleting_dir = garments_dir / f'.deleting-{garment_id}-{uuid.uuid4().hex}'
                    garment_dir.replace(deleting_dir)
                    try:
                        write_json_safely(index_path, updated_index)
                        shutil.rmtree(deleting_dir)
                    except OSError:
                        if deleting_dir.exists() and not garment_dir.exists():
                            deleting_dir.replace(garment_dir)
                        if previous_index is None:
                            index_path.unlink(missing_ok=True)
                        else:
                            write_bytes_safely(index_path, previous_index)
                        raise
                return self.send_json(200, {'ok': True})
            finally:
                generation_lock.release()

        def save_garment(self, payload):
            name = payload.get('name')
            category = payload.get('category')
            gender = payload.get('gender')
            garment_type_id = payload.get('garmentTypeId')
            color_name = payload.get('colorName')
            length = payload.get('lengthCm')
            width = payload.get('widthCm')
            photo_bytes = decode_photo_data(payload.get('photoDataUrl'))
            closet_image = decode_png_data(payload.get('closetImageDataUrl'))
            background_id = payload.get('backgroundId', 'M4')
            school_code = clean_school_code(payload.get('schoolCode'))
            owner_name = payload.get('ownerName', '')
            measurement_method = payload.get('measurementMethod', 'provided')
            if not isinstance(measurement_method, str) or measurement_method not in {'manual','photo-analysis','provided'}:
                return self.send_json(400, {'error': '치수 입력 방식을 확인해 주세요.'})
            try:
                recommendation_metadata = clean_metadata(payload)
            except ValueError as error:
                return self.send_json(400, {'error': str(error)})
            user = self.current_user()
            if user is not None:
                owner_name = user['name']  # 로그인했다면 올린 사람은 항상 나예요.
            if not isinstance(owner_name, str) or len(owner_name.strip()) > 200:
                return self.send_json(400, {'error': '올린 사람 이름은 200글자까지 쓸 수 있어요.'})
            if (not isinstance(name, str) or not name.strip() or len(name.strip()) > 60
                    or category != GARMENT_TYPE_CATEGORIES.get(garment_type_id)
                    or category not in VALID_CATEGORIES or gender not in VALID_GENDERS
                    or isinstance(garment_type_id, bool) or garment_type_id not in GARMENT_TYPE_NAMES
                    or not isinstance(color_name, str) or not color_name.strip() or len(color_name) > 30
                    or not valid_measurement(length) or not valid_measurement(width)
                    or photo_bytes is None or not photo_bytes.startswith(b'\xff\xd8\xff')
                    or len(photo_bytes) > MAX_PHOTO_BYTES or not isinstance(background_id, str) or background_id not in VALID_MATS
                    or (payload.get('closetImageDataUrl') is not None and closet_image is None)):
                return self.send_json(400, {'error': '이름, 종류, 용도, 색, 길이, 너비와 사진을 확인해 주세요.'})

            thumbnail = None
            if closet_image is not None:
                try:
                    thumbnail = make_garment_thumbnail(closet_image)
                except (ValueError, RuntimeError) as error:
                    return self.send_json(400, {'error': str(error)})

            record_id = str(uuid.uuid4())
            created_at = now_text()
            item = {
                **recommendation_metadata,
                'measurementMethod': measurement_method,
                'id': record_id, 'name': name.strip(), 'category': category, 'gender': gender,
                'garmentTypeId': garment_type_id,
                'garmentTypeName': GARMENT_TYPE_NAMES[garment_type_id],
                'schoolCode': school_code,
                'ownerName': owner_name.strip(),
                'colorName': color_name.strip(), 'lengthCm': round(float(length), 1),
                'widthCm': round(float(width), 1), 'createdAt': created_at,
                'photoUrl': f'/api/garments/{record_id}/photo',
                'closetPhotoUrl': f'/api/garments/{record_id}/closet-photo',
                'backgroundId': background_id,
                'maskPipelineVersion': LEGACY_GARMENT_MASK_PIPELINE_VERSION if closet_image is not None else '',
                'thumbnailVersion': 0,
            }
            size_category = 'shorts' if category == 'pants' and item.get('bottomLength') == '반바지' else category
            if not item.get('size') and size_category in {'top', 'dress', 'pants', 'skirt', 'shorts'}:
                try:
                    item['size'] = estimate_korean_size(size_category, item['widthCm'], item['lengthCm'])
                except ValueError:
                    pass
                else:
                    metadata_sources = dict(item.get('metadataSources') or {})
                    metadata_sources['size'] = 'measurement-rule'
                    item['metadataSources'] = metadata_sources
            if user is not None:
                item['ownerUsername'] = user['username']
                item['donorId'] = user['username']
            temporary_dir = garments_dir / f'.tmp-{record_id}'
            final_dir = garments_dir / record_id
            try:
                temporary_dir.mkdir(parents=True, exist_ok=False)
                (temporary_dir / 'photo.jpg').write_bytes(photo_bytes)
                if closet_image is not None:
                    (temporary_dir / 'closet.png').write_bytes(closet_image)
                    (temporary_dir / 'closet-thumbnail.png').write_bytes(thumbnail)
                with lock:
                    index = read_json(garments_dir / 'index.json') or []
                    if not isinstance(index, list):
                        index = []
                    self.ensure_garment_codes(index)
                    sequences = read_json(garments_dir / '.sequences.json') or {}
                    allocate_garment_code(item, sequences)
                    write_json_safely(temporary_dir / 'garment.json', item)
                    temporary_dir.replace(final_dir)
                    index.insert(0, {key: value for key, value in item.items() if key != 'photoUrl'})
                    write_json_safely(garments_dir / 'index.json', index)
                    write_json_safely(garments_dir / '.sequences.json', sequences)
            except OSError:
                shutil.rmtree(temporary_dir, ignore_errors=True)
                return self.send_json(500, {'error': '옷 사진을 저장하지 못했어요. 서버 저장 공간을 확인해 주세요.'})
            return self.send_json(201, item)

        def get_measure_dataset_report(self):
            report_path = storage / 'measure-dataset-report' / 'report.json'
            report = read_json(report_path)
            if report is not None:
                return self.send_json(200, report)
            return self.run_measure_dataset_analysis()

        def get_measure_dataset_model_comparison(self):
            report_path = storage / 'measure-dataset-report' / 'report.json'
            report = read_json(report_path)
            if report is None:
                try:
                    report = analyze_measure_dataset(storage / 'measure-dataset-report')
                except (ImportError, RuntimeError, OSError, ValueError) as error:
                    return self.send_json(503, {'error': f'기본 측정 결과를 준비하지 못했어요: {error}'})

            results_dir = PROJECT_ROOT / 'experiments' / 'garment_ai' / 'results'
            model_results = read_json(results_dir / 'results.json')
            hrnet_results = read_json(results_dir / 'hrnet_results.json')
            gemini_results = read_json(storage / 'measure-dataset-report' / 'gemini-results.json')
            if model_results is None or hrnet_results is None:
                return self.send_json(503, {'error': 'SAM·YOLO·HRNet의 저장된 실험 결과를 찾지 못했어요.'})
            try:
                from tools.model_comparison import build_model_comparison

                comparison = build_model_comparison(report, model_results, hrnet_results, gemini_results)
            except ValueError as error:
                return self.send_json(422, {'error': str(error)})
            return self.send_json(200, comparison)

        def run_gemini_dataset_analysis(self):
            token = get_replicate_api_token()
            if not token:
                return self.send_json(503, {'error': '서버에 REPLICATE_API_TOKEN 환경 변수를 설정한 뒤 다시 눌러 주세요.'})

            try:
                from tools.replicate_gemini import run_dataset_analysis

                result = run_dataset_analysis(PROJECT_ROOT / 'measure_dataset', token)
            except (OSError, RuntimeError, ValueError) as error:
                return self.send_json(502, {'error': f'Gemini 실험을 마치지 못했어요: {error}'})
            report_dir = storage / 'measure-dataset-report'
            write_json_safely(report_dir / 'gemini-results.json', result)
            return self.send_json(200, {
                'model': result.get('model'),
                'sampleCount': result.get('sampleCount', 0),
                'successCount': result.get('successCount', 0),
                'failureCount': result.get('failureCount', 0),
                'generatedAt': result.get('generatedAt'),
            })

        def run_measure_dataset_analysis(self):
            try:
                report = analyze_measure_dataset(storage / 'measure-dataset-report')
            except (ImportError, RuntimeError) as error:
                return self.send_json(503, {'error': str(error)})
            except (OSError, ValueError) as error:
                return self.send_json(500, {'error': f'사진 분석을 마치지 못했어요: {error}'})
            return self.send_json(200, report)

        def run_measure_dataset_round3(self):
            report_path = storage / 'measure-dataset-report' / 'round3.json'
            try:
                from tools.measure_dataset import analyze_round3_dataset

                report = analyze_round3_dataset(
                    PROJECT_ROOT / 'measure_dataset', report_path, repeat_count=100, workers=8
                )
            except (ImportError, RuntimeError) as error:
                return self.send_json(503, {'error': str(error)})
            except (OSError, ValueError) as error:
                return self.send_json(500, {'error': f'3차 실험을 실행하지 못했어요: {error}'})
            gemini_report = read_json(storage / 'measure-dataset-report' / 'gemini-results.json')
            if gemini_report is not None:
                report['gemini'] = gemini_report
            return self.send_json(200, report)

        def save_dataset_as_experiment(self):
            report_path = storage / 'measure-dataset-report' / 'report.json'
            report = read_json(report_path)
            if report is None:
                try:
                    report = analyze_measure_dataset(storage / 'measure-dataset-report')
                except (ImportError, RuntimeError) as error:
                    return self.send_json(503, {'error': str(error)})
                except (OSError, ValueError) as error:
                    return self.send_json(500, {'error': f'사진 분석을 불러오지 못했어요: {error}'})

            rows = report.get('rows') if isinstance(report, dict) else None
            if not isinstance(rows, list) or len(rows) != 20:
                return self.send_json(422, {'error': '사진 20장의 분석 결과가 모두 있어야 새 회차로 저장할 수 있어요. 먼저 사진을 다시 분석해 주세요.'})

            background_ids = {
                'point': 'M1', 'cross': 'M2', 'charuco': 'M3', 'chaaruco_cross': 'M4'
            }
            garment_info = {
                1: ('상의 외투', 'top'), 2: ('하얀 크롭티셔츠', 'top'),
                3: ('원피스', 'dress'), 4: ('점퍼', 'top'), 5: ('바지', 'pants')
            }
            validated_rows = []
            seen_pairs = set()
            dataset_root = (PROJECT_ROOT / 'measure_dataset').resolve()
            for row in rows:
                if not isinstance(row, dict) or row.get('status') != 'done':
                    return self.send_json(422, {'error': '분석이 끝나지 않은 사진이 있어요. 모든 사진을 다시 분석한 뒤 저장해 주세요.'})
                try:
                    garment_number = int(row.get('garmentId'))
                    garment_name, category = garment_info[garment_number]
                    background_key = str(row.get('backgroundId', '')).casefold()
                    mat_id = background_ids[background_key]
                    width = row.get('referenceWidthCm')
                    length = row.get('referenceLengthCm')
                    measured_width = row.get('measuredWidthCm')
                    measured_length = row.get('measuredLengthCm')
                    image_name = Path(str(row.get('imagePath') or row.get('imageName') or '')).name
                    source_path = (dataset_root / str(row.get('backgroundId')) / image_name).resolve()
                except (KeyError, TypeError, ValueError):
                    return self.send_json(422, {'error': '사진 이름이나 배경 정보를 읽지 못했어요. 데이터셋 파일을 확인해 주세요.'})
                if (not valid_measurement(width) or not valid_measurement(length)
                        or not valid_measurement(measured_width) or not valid_measurement(measured_length)):
                    return self.send_json(422, {'error': '기준 크기나 자동 측정값이 비어 있어요. 사진을 다시 분석해 주세요.'})
                pair = (mat_id, garment_number)
                if pair in seen_pairs:
                    return self.send_json(422, {'error': '같은 배경과 옷의 결과가 두 번 들어 있어요. 분석 결과를 확인해 주세요.'})
                seen_pairs.add(pair)
                if (source_path.parent != (dataset_root / str(row.get('backgroundId'))).resolve()
                        or not source_path.is_file() or source_path.suffix.lower() != '.jpg'):
                    return self.send_json(422, {'error': f'{image_name} 사진 파일을 찾지 못했어요.'})
                validated_rows.append({
                    'matId': mat_id, 'garmentNumber': garment_number, 'garmentName': garment_name,
                    'category': category, 'referenceWidthCm': float(width), 'referenceLengthCm': float(length),
                    'measuredWidthCm': float(measured_width), 'measuredLengthCm': float(measured_length),
                    'imageName': image_name, 'sourcePath': source_path,
                })
            if len(seen_pairs) != 20:
                return self.send_json(422, {'error': '배경 4종과 옷 5벌의 결과가 빠짐없이 있어야 저장할 수 있어요.'})

            garments_by_number = {}
            for row in validated_rows:
                garments_by_number[row['garmentNumber']] = {
                    'id': f"garment-{row['garmentNumber']}", 'name': row['garmentName'],
                    'category': row['category'], 'referenceLengthCm': row['referenceLengthCm'],
                    'referenceWidthCm': row['referenceWidthCm'],
                }
            for garment_number, garment in garments_by_number.items():
                matching = [row for row in validated_rows if row['garmentNumber'] == garment_number]
                if any(row['referenceLengthCm'] != garment['referenceLengthCm']
                       or row['referenceWidthCm'] != garment['referenceWidthCm'] for row in matching):
                    return self.send_json(422, {'error': '같은 옷의 기준 크기가 사진마다 달라요. 데이터셋을 확인해 주세요.'})

            run_id = str(uuid.uuid4())
            staging_dir = experiments_dir / f'.{run_id}.tmp'
            final_dir = experiments_dir / run_id
            records = []
            try:
                (staging_dir / 'photos').mkdir(parents=True, exist_ok=False)
                for row in validated_rows:
                    garment_id = f"garment-{row['garmentNumber']}"
                    filename = f"{row['matId']}-{garment_id}.jpg"
                    shutil.copyfile(row['sourcePath'], staging_dir / 'photos' / filename)
                    records.append({
                        'id': f"{row['matId']}-{garment_id}", 'matId': row['matId'],
                        'garmentId': garment_id, 'status': 'done', 'edited': False,
                        'measuredLengthCm': row['measuredLengthCm'], 'measuredWidthCm': row['measuredWidthCm'],
                        'errorLengthCm': round(row['measuredLengthCm'] - row['referenceLengthCm'], 1),
                        'errorWidthCm': round(row['measuredWidthCm'] - row['referenceWidthCm'], 1),
                        'imageName': row['imageName'], 'photoFile': filename,
                    })
                with lock:
                    rounds = []
                    if experiments_dir.exists():
                        for folder in experiments_dir.iterdir():
                            item = read_json(folder / 'metadata.json') if folder.is_dir() else None
                            if item and isinstance(item.get('round'), int):
                                rounds.append(item['round'])
                    item = {
                        'id': run_id, 'round': max(rounds, default=0) + 1, 'createdAt': now_text(),
                        'completedAt': now_text(), 'completed': True,
                        'garments': [garments_by_number[number] for number in sorted(garments_by_number)],
                        'records': records,
                    }
                    write_json_safely(staging_dir / 'metadata.json', item)
                    staging_dir.replace(final_dir)
            except OSError:
                shutil.rmtree(staging_dir, ignore_errors=True)
                raise
            return self.send_json(201, {
                'id': run_id, 'round': item['round'], 'completed': True,
                'garmentCount': len(item['garments']), 'recordCount': len(records),
                'resultUrl': f"/results/index.html?run={run_id}",
            })

        def measure_uploaded_photo(self, payload):
            mat_id = payload.get('backgroundId')
            category = payload.get('category')
            photo_data = payload.get('photoDataUrl', '')
            if mat_id not in VALID_MATS or category not in VALID_CATEGORIES:
                return self.send_json(400, {'error': '배경과 옷 종류를 다시 확인해 주세요.'})
            if not isinstance(photo_data, str) or not photo_data.startswith('data:image/jpeg;base64,'):
                return self.send_json(400, {'error': '사진을 JPG 형식으로 보내 주세요.'})
            try:
                photo_bytes = base64.b64decode(photo_data.split(',', 1)[1], validate=True)
            except (binascii.Error, ValueError):
                return self.send_json(400, {'error': '사진을 읽지 못했어요. 다시 촬영해 주세요.'})
            if not photo_bytes or len(photo_bytes) > MAX_PHOTO_BYTES:
                return self.send_json(413, {'error': '사진이 너무 커요. 크기를 줄여 다시 시도해 주세요.'})

            try:
                from tools.vision_pipeline import image_to_data_url, measure_photo_with_fallback

                analysis = measure_photo_with_fallback(photo_bytes, mat_id, category=category)
                flattened = analysis['rectified']
                measured = analysis['measurement']
            except (ImportError, RuntimeError) as error:
                return self.send_json(503, {'error': str(error)})
            except (ValueError, OSError) as error:
                return self.send_json(422, {'error': str(error)})

            return self.send_json(200, {
                'ok': True,
                'measuredLengthCm': measured['measuredLengthCm'],
                'measuredWidthCm': measured['measuredWidthCm'],
                'lengthCm': measured['measuredLengthCm'],
                'widthCm': measured['measuredWidthCm'],
                'widthKind': measured['widthKind'],
                'maskMethods': measured['maskMethods'],
                'box': measured['box'],
                'confidence': measured['confidence'],
                'photoDataUrl': measured['overlayDataUrl'],
                'fullPhotoDataUrl': image_to_data_url(flattened['image']),
                'rectificationMethod': flattened['method'],
                'message': measured['message'] + (' 첫 분석이 실패해 600px 대체 방식으로 측정했어요. 결과를 확인해 주세요.' if analysis['fallbackUsed'] else ''),
                'fallbackUsed': analysis['fallbackUsed'],
                'fallbackReason': analysis['fallbackReason'],
                'pipelineMode': analysis['pipelineMode'],
            })

        def read_experiment(self, run_id):
            if not valid_run_id(run_id):
                return None
            return read_json(experiments_dir / run_id / 'metadata.json')

        def list_experiments(self):
            rows = []
            if experiments_dir.exists():
                for folder in experiments_dir.iterdir():
                    item = read_json(folder / 'metadata.json') if folder.is_dir() else None
                    if not item:
                        continue
                    rows.append({
                        'id': item['id'], 'round': item['round'], 'createdAt': item['createdAt'],
                        'completedAt': item.get('completedAt'), 'completed': item.get('completed', False),
                        'garmentCount': len(item.get('garments', [])), 'recordCount': len(item.get('records', []))
                    })
            rows.sort(key=lambda item: item['round'], reverse=True)
            return self.send_json(200, rows)

        def get_experiment(self, run_id):
            item = self.read_experiment(run_id)
            if not item:
                return self.send_json(404, {'error': '해당 실험 회차를 찾지 못했어요.'})
            result = dict(item)
            result['records'] = [dict(record, photoUrl=f"/api/experiments/{run_id}/photos/{record['photoFile']}") for record in item.get('records', [])]
            for record in result['records']:
                record.pop('photoFile', None)
            return self.send_json(200, result)

        def get_photo(self, run_id, filename):
            if not valid_run_id(run_id) or not SAFE_PHOTO_NAME.fullmatch(filename):
                return self.send_json(404, {'error': '사진을 찾지 못했어요.'})
            path = (experiments_dir / run_id / 'photos' / filename).resolve()
            expected_parent = (experiments_dir / run_id / 'photos').resolve()
            if path.parent != expected_parent or not path.is_file():
                return self.send_json(404, {'error': '사진을 찾지 못했어요.'})
            body = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'private, max-age=3600')
            self.end_headers()
            self.wfile.write(body)

        def create_experiment(self, payload):
            garments = payload.get('garments')
            if not isinstance(garments, list) or not 1 <= len(garments) <= 30:
                return self.send_json(400, {'error': '옷은 1벌부터 30벌까지 등록할 수 있어요.'})
            clean_garments = []
            for garment in garments:
                if not isinstance(garment, dict):
                    return self.send_json(400, {'error': '옷 정보를 다시 확인해 주세요.'})
                name = str(garment.get('name', '')).strip()[:24]
                category = garment.get('category', 'other')
                length = garment.get('referenceLengthCm')
                width = garment.get('referenceWidthCm')
                collar_length = garment.get('referenceCollarToHemCm')
                if (not SAFE_GARMENT_ID.fullmatch(str(garment.get('id', ''))) or not name
                        or category not in VALID_CATEGORIES or not valid_measurement(length) or not valid_measurement(width)
                        or (collar_length is not None and not valid_measurement(collar_length))):
                    return self.send_json(400, {'error': '옷 이름, 종류, 줄자 기준 크기를 확인해 주세요.'})
                clean_garment = {'id': garment['id'], 'name': name, 'category': category,
                                 'referenceLengthCm': float(length), 'referenceWidthCm': float(width)}
                if collar_length is not None:
                    clean_garment['referenceCollarToHemCm'] = float(collar_length)
                clean_garments.append(clean_garment)
            with lock:
                rounds = []
                if experiments_dir.exists():
                    for folder in experiments_dir.iterdir():
                        item = read_json(folder / 'metadata.json') if folder.is_dir() else None
                        if item and isinstance(item.get('round'), int):
                            rounds.append(item['round'])
                run_id = str(uuid.uuid4())
                item = {
                    'id': run_id, 'round': max(rounds, default=0) + 1, 'createdAt': now_text(),
                    'completed': False, 'garments': clean_garments, 'records': []
                }
                (experiments_dir / run_id / 'photos').mkdir(parents=True, exist_ok=True)
                write_json_safely(experiments_dir / run_id / 'metadata.json', item)
            return self.send_json(201, item)

        def save_record(self, run_id, payload):
            item = self.read_experiment(run_id)
            record = payload.get('record')
            photo_data = payload.get('photoDataUrl', '')
            if not item:
                return self.send_json(404, {'error': '실험 회차를 찾지 못했어요. 새 실험을 시작해 주세요.'})
            if not isinstance(record, dict):
                return self.send_json(400, {'error': '측정 결과가 비어 있어요.'})
            mat_id = record.get('matId')
            garment_id = record.get('garmentId')
            if mat_id not in VALID_MATS or not any(g['id'] == garment_id for g in item['garments']):
                return self.send_json(400, {'error': '배경 또는 옷 정보를 확인해 주세요.'})
            if not isinstance(photo_data, str) or not photo_data.startswith('data:image/jpeg;base64,'):
                return self.send_json(400, {'error': '사진을 JPG 형식으로 저장해 주세요.'})
            try:
                photo_bytes = base64.b64decode(photo_data.split(',', 1)[1], validate=True)
            except (binascii.Error, ValueError):
                return self.send_json(400, {'error': '사진을 읽지 못했어요. 다시 촬영해 주세요.'})
            if not photo_bytes or len(photo_bytes) > MAX_PHOTO_BYTES:
                return self.send_json(413, {'error': '사진이 너무 커요. 다시 촬영해 주세요.'})
            filename = f'{mat_id}-{garment_id}.jpg'
            clean_record = {key: value for key, value in record.items() if key not in {'photo', 'rawPhoto', 'photoUrl'}}
            clean_record['photoFile'] = filename
            with lock:
                item = self.read_experiment(run_id)
                if not item:
                    return self.send_json(404, {'error': '실험 회차를 찾지 못했어요.'})
                photo_path = experiments_dir / run_id / 'photos' / filename
                photo_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = photo_path.with_suffix('.jpg.tmp')
                temporary.write_bytes(photo_bytes)
                temporary.replace(photo_path)
                records = item.setdefault('records', [])
                replaced = False
                for index, old in enumerate(records):
                    if old.get('id') == clean_record.get('id'):
                        records[index] = clean_record
                        replaced = True
                        break
                if not replaced:
                    records.append(clean_record)
                write_json_safely(experiments_dir / run_id / 'metadata.json', item)
            return self.send_json(200, dict(clean_record, photoUrl=f'/api/experiments/{run_id}/photos/{filename}'))

        def complete_experiment(self, run_id):
            with lock:
                item = self.read_experiment(run_id)
                if not item:
                    return self.send_json(404, {'error': '실험 회차를 찾지 못했어요.'})
                expected = len(item['garments']) * 4
                if len(item.get('records', [])) < expected:
                    return self.send_json(400, {'error': '사진을 모두 저장한 뒤 실험을 마칠 수 있어요.'})
                item['completed'] = True
                item['completedAt'] = now_text()
                write_json_safely(experiments_dir / run_id / 'metadata.json', item)
            return self.send_json(200, {'id': run_id, 'round': item['round'], 'completed': True})

    FitShareHandler.garment_metadata_lock = lock
    FitShareHandler.product_generation_locks = product_generation_locks
    return FitShareHandler


def valid_measurement(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 < value <= 300


def main():
    host = os.environ.get('FITSHARE_HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', '8000'))
    storage = Path(os.environ.get('FITSHARE_DATA_DIR', str(DEFAULT_DATA_DIR)))
    tls_certfile = os.environ.get('FITSHARE_TLS_CERT_FILE') or None
    tls_keyfile = os.environ.get('FITSHARE_TLS_KEY_FILE') or None
    tls_port = int(os.environ.get('FITSHARE_TLS_PORT', '8443')) if tls_certfile and tls_keyfile else None
    httpd = create_server(host, port, storage, tls_certfile=tls_certfile,
                          tls_keyfile=tls_keyfile, tls_port=tls_port)
    from tools.garment_thumbnails import prepare_garment_thumbnails

    def prepare_saved_thumbnails():
        try:
            prepare_garment_thumbnails(storage / 'garments',
                                       lambda root, garment_id: ensure_garment_cutout(root, garment_id, httpd.garment_metadata_lock),
                                       httpd.garment_metadata_lock)
        except Exception:
            # Cache preparation is best-effort and must not stop the HTTP server.
            return

    threading.Thread(target=prepare_saved_thumbnails, name='garment-thumbnail-warmup', daemon=True).start()
    print(f'FitShare HTTP is running: http://{host}:{httpd.server_address[1]}/')
    if httpd.tls_server is not None:
        tls_thread = threading.Thread(target=httpd.tls_server.serve_forever,
                                      name='fitshare-https', daemon=True)
        tls_thread.start()
        print(f'FitShare HTTPS is running: https://{host}:{httpd.tls_server.server_address[1]}/')
    print(f'Experiment photos and results are saved in: {storage.resolve()}')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nFitShare server stopped.')
    finally:
        if httpd.tls_server is not None:
            httpd.tls_server.shutdown()
            httpd.tls_server.server_close()
        httpd.server_close()


if __name__ == '__main__':
    main()
