"""Run SAM on Replicate with the YOLO box, for Vercel where PyTorch does not fit.

It has the same set_image()/predict(box=...) shape as the local SamPredictor, so
make_product_cutout() keeps its YOLO box -> SAM steps (AGENTS.md rule 10).
"""

import json
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

import cv2
import numpy as np

from tools.replicate_product_photo import API_ROOT, ProductPhotoError, _call, _image_result, _json, _upload


# yyjim/segment-anything-tryout: Meta SAM, one bounding box in, one mask out.
SAM_MODEL_VERSION = '72d58a6b199b075ba3a2c1a0e0951384b819a0f6d99ba6c326727fb464a7c792'
# 오래 쉬던 모델은 깨어나는 데 2~3분 걸릴 수 있어요.
SAM_TIMEOUT_SECONDS = 240


class ReplicateSamPredictor:
    def __init__(self, token, version=SAM_MODEL_VERSION, opener=urlopen, sleep=time.sleep):
        self.token = token
        self.version = version
        self.opener = opener
        self.sleep = sleep
        self.image = None

    def set_image(self, rgb_image):
        self.image = rgb_image

    def predict(self, box, multimask_output=False):
        if not isinstance(self.token, str) or not self.token.strip():
            raise ProductPhotoError('SAM을 실행하려면 서버에 Replicate 토큰을 설정해야 해요.')
        if self.image is None:
            raise ProductPhotoError('SAM에 보낼 옷 사진이 없어요.')
        height, width = self.image.shape[:2]
        left, top, right, bottom = (int(round(float(value))) for value in box)
        ok, encoded = cv2.imencode('.png', cv2.cvtColor(self.image, cv2.COLOR_RGB2BGR))
        if not ok:
            raise ProductPhotoError('SAM에 보낼 사진을 만들지 못했어요.')
        file_id = None
        try:
            file_id, file_url = _upload(encoded.tobytes(), self.token, self.opener)
            result = _json(f'{API_ROOT}/predictions', self.token,
                           {'version': self.version, 'input': {
                               'image': file_url,
                               # 이 모델은 상자를 [x, y, 너비, 높이]로 받아요.
                               'box': json.dumps([left, top, right - left, bottom - top]),
                               'mask_only': True, 'multimask_output': False,
                           }},
                           {'Prefer': 'wait=60', 'Cancel-After': f'{SAM_TIMEOUT_SECONDS}s'}, 'POST', self.opener, timeout=75)
            deadline = time.monotonic() + SAM_TIMEOUT_SECONDS
            while result.get('status') in {'starting', 'processing', 'queued'}:
                poll_url = (result.get('urls') or {}).get('get')
                parsed = urlsplit(poll_url or '')
                if parsed.scheme != 'https' or parsed.netloc != 'api.replicate.com':
                    raise ProductPhotoError('SAM 작업 주소를 확인하지 못했어요.')
                if time.monotonic() >= deadline:
                    raise ProductPhotoError('SAM 옷 분리가 제한 시간 안에 끝나지 않았어요. 잠시 뒤 다시 시도해 주세요.')
                self.sleep(2)
                result = _json(poll_url, self.token, opener=self.opener, timeout=30)
            if result.get('status') != 'succeeded':
                raise ProductPhotoError('SAM 옷 분리를 마치지 못했어요. 원본 사진은 그대로 보존했어요.')
            mask_bytes, _ = _image_result(result, self.opener)
        finally:
            if file_id:
                try:
                    _call(f'{API_ROOT}/files/{file_id}', self.token, headers={}, method='DELETE',
                          opener=self.opener, timeout=30)
                except ProductPhotoError:
                    pass
        mask = cv2.imdecode(np.frombuffer(mask_bytes, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if mask is None:
            raise ProductPhotoError('SAM 결과 그림을 읽지 못했어요.')
        if mask.shape != (height, width):
            mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST)
        return np.array([mask > 127]), np.array([1.0]), None
