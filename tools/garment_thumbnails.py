"""Background preparation for small, versioned closet thumbnails."""

import json
import uuid
from contextlib import nullcontext
from pathlib import Path


THUMBNAIL_VERSION = 3
MAXIMUM_SIDE = 200
GARMENT_MASK_PIPELINE_VERSION = 'yolo-sam-vit-b-v3'


def make_garment_thumbnail(png_bytes, maximum_side=MAXIMUM_SIDE):
    try:
        import cv2
        import numpy as np
    except ImportError as error:
        raise RuntimeError('사진 축소 도구를 불러오지 못했어요.') from error
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


def _read_json(path, default):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, type(default)) else default
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path, value):
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_bytes(path, value):
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_bytes(value)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_garment_thumbnails(garments_dir, ensure_cutout, metadata_lock=None):
    root = Path(garments_dir)
    index_path = root / 'index.json'
    index = _read_json(index_path, [])
    prepared = 0
    failed = []
    for record in index:
        garment_id = record.get('id') if isinstance(record, dict) else None
        try:
            if str(uuid.UUID(garment_id)) != garment_id:
                continue
        except (ValueError, TypeError, AttributeError):
            continue
        garment_dir = root / garment_id
        item_path = garment_dir / 'garment.json'
        item = _read_json(item_path, {})
        if not item:
            continue
        thumbnail_path = garment_dir / 'closet-thumbnail.png'
        if item.get('thumbnailVersion') == THUMBNAIL_VERSION and thumbnail_path.is_file():
            continue
        try:
            cutout_path = garment_dir / 'closet.png'
            if item.get('maskPipelineVersion') == GARMENT_MASK_PIPELINE_VERSION and cutout_path.is_file():
                cutout = cutout_path.read_bytes()
            else:
                cutout = ensure_cutout(root, garment_id)
            thumbnail = make_garment_thumbnail(cutout)
            _write_bytes(thumbnail_path, thumbnail)
            with metadata_lock if metadata_lock is not None else nullcontext():
                latest = _read_json(item_path, {})
                if not latest:
                    raise FileNotFoundError('garment record disappeared during thumbnail preparation')
                latest['thumbnailVersion'] = THUMBNAIL_VERSION
                _write_json(item_path, latest)
                index = _read_json(index_path, [])
                for index_item in index:
                    if isinstance(index_item, dict) and index_item.get('id') == garment_id:
                        index_item['thumbnailVersion'] = THUMBNAIL_VERSION
                        index_item['maskPipelineVersion'] = latest.get('maskPipelineVersion')
                        break
                _write_json(index_path, index)
            prepared += 1
        except Exception:
            failed.append(garment_id)
    return {'prepared': prepared, 'failed': failed}
