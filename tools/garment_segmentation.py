"""YOLO → GrabCut for measurement; YOLO → SAM for product photo cutouts."""

import hashlib
import os
import threading
import urllib.request
from pathlib import Path

import cv2
import numpy as np


MODEL_SHA256 = '485bc106721febbd82fe24297e12a9fb49a435a552afff1e6b76308716555f76'
MODEL_URL = 'https://huggingface.co/HasanKabir23/Yolov26s-DeepFashion2/resolve/main/best.onnx'
# Vercel은 코드 폴더에 쓸 수 없어서 FITSHARE_MODEL_DIR(/tmp)로 모델 저장 위치를 바꿉니다.
MODEL_DIR = Path(os.environ.get('FITSHARE_MODEL_DIR') or Path(__file__).resolve().parents[1] / 'server-data' / 'model-cache')
MODEL_PATH = MODEL_DIR / 'deepfashion2-yolo26s-seg.onnx'
CLASS_NAMES = {
    0: 'short_sleeve_top', 1: 'long_sleeve_top', 2: 'short_sleeve_outwear',
    3: 'long_sleeve_outwear', 4: 'vest', 5: 'sling', 6: 'shorts', 7: 'trousers',
    8: 'skirt', 9: 'short_sleeve_dress', 10: 'long_sleeve_dress', 11: 'vest_dress', 12: 'sling_dress',
}
MODEL_OUTPUTS = ('output0', 'output1')
_load_lock = threading.Lock()
_inference_lock = threading.Lock()
_grabcut_lock = threading.Lock()
_detector = None
_sam_predictor = None
_sam_load_lock = threading.Lock()
_sam_inference_lock = threading.Lock()


def _valid_model(path):
    if not path.is_file():
        return False
    digest = hashlib.sha256()
    with path.open('rb') as model_file:
        for block in iter(lambda: model_file.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest() == MODEL_SHA256


def _ensure_model(path=MODEL_PATH):
    if _valid_model(path):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.download')
    try:
        digest = hashlib.sha256()
        with urllib.request.urlopen(MODEL_URL, timeout=60) as response, temporary.open('wb') as output:
            while block := response.read(1024 * 1024):
                digest.update(block)
                output.write(block)
        if digest.hexdigest() != MODEL_SHA256:
            raise RuntimeError('YOLO 모델 파일의 체크섬이 맞지 않아요.')
        temporary.replace(path)
    except Exception as error:
        temporary.unlink(missing_ok=True)
        if isinstance(error, RuntimeError):
            raise
        raise RuntimeError('YOLO 모델을 준비하지 못했어요. 네트워크와 server-data/model-cache를 확인해 주세요.') from error
    return path


def _load_detector():
    global _detector
    if _detector is not None:
        return _detector
    with _load_lock:
        if _detector is None:
            model_path = _ensure_model()
            try:
                # Python handles Korean Windows paths; pass the model buffer to OpenCV.
                net = cv2.dnn.readNetFromONNX(np.frombuffer(model_path.read_bytes(), dtype=np.uint8))
                net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
                net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
                _detector = YoloDetector(net)
            except cv2.error as error:
                raise RuntimeError('YOLO 모델을 OpenCV DNN으로 열지 못했어요.') from error
    return _detector


def _preprocess(image, input_size=416):
    height, width = image.shape[:2]
    scale = min(input_size / width, input_size / height)
    resized_width, resized_height = round(width * scale), round(height * scale)
    resized = cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)
    pad_left, pad_top = (input_size - resized_width) // 2, (input_size - resized_height) // 2
    canvas = np.full((input_size, input_size, 3), 114, dtype=np.uint8)
    canvas[pad_top:pad_top + resized_height, pad_left:pad_left + resized_width] = resized
    tensor = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    return np.transpose(tensor, (2, 0, 1))[None, ...], (scale, pad_left, pad_top, resized_width, resized_height)


def _category_classes(garment_id=None, category=None):
    if garment_id == 3 or category == 'dress':
        return {9, 10, 11, 12}
    if garment_id == 5:
        return {7}
    if category in {'pants', 'bottom', 'skirt'}:
        return {6, 7, 8}
    if category == 'other':
        return set(CLASS_NAMES)
    return {0, 1, 2, 3, 4, 5}


def _decode(output0, output1, original_shape, garment_id=None, category=None, input_size=416):
    predictions, prototypes = np.asarray(output0)[0], np.asarray(output1)[0]
    height, width = original_shape
    scale = min(input_size / width, input_size / height)
    resized_width, resized_height = round(width * scale), round(height * scale)
    pad_left, pad_top = (input_size - resized_width) // 2, (input_size - resized_height) // 2
    prototype_count, prototype_height, prototype_width = prototypes.shape
    allowed_classes = _category_classes(garment_id, category)
    results = []
    for row in predictions:
        confidence, class_id = float(row[4]), int(row[5])
        if confidence < .25 or class_id not in allowed_classes or class_id not in CLASS_NAMES:
            continue
        x1, y1, x2, y2 = (float(value) for value in row[:4])
        logits = row[6:6 + prototype_count] @ prototypes.reshape(prototype_count, -1)
        logits = cv2.resize(logits.reshape(prototype_height, prototype_width), (input_size, input_size))
        left, right = max(0, min(input_size, round(x1))), max(0, min(input_size, round(x2)))
        top, bottom = max(0, min(input_size, round(y1))), max(0, min(input_size, round(y2)))
        cropped = np.full((input_size, input_size), -20, dtype=np.float32)
        if right > left and bottom > top:
            cropped[top:bottom, left:right] = logits[top:bottom, left:right]
        content = (cropped > 0).astype(np.uint8)[pad_top:pad_top + resized_height, pad_left:pad_left + resized_width]
        mask = cv2.resize(content, (width, height), interpolation=cv2.INTER_NEAREST)
        if int(mask.sum()) < height * width * .002:
            continue
        results.append({'classId': class_id, 'confidence': confidence, 'mask': mask,
                        'box': [max(0, (x1 - pad_left) / scale), max(0, (y1 - pad_top) / scale),
                                min(float(width), (x2 - pad_left) / scale), min(float(height), (y2 - pad_top) / scale)]})
    results.sort(key=lambda item: item['confidence'], reverse=True)
    return results


class YoloDetector:
    """Run a YOLO26 segmentation ONNX network through OpenCV DNN."""

    def __init__(self, net):
        self.net = net

    def detect(self, image, garment_id=None, category=None):
        tensor, _ = _preprocess(image)
        with _inference_lock:
            self.net.setInput(tensor)
            outputs = self.net.forward(MODEL_OUTPUTS)
        detections = _decode(*outputs, image.shape[:2], garment_id, category)
        if garment_id == 5:
            legs = [item for item in detections if item['classId'] == 7]
            if legs:
                combined = dict(legs[0])
                combined['mask'] = np.maximum.reduce([item['mask'] for item in legs]).astype(np.uint8)
                detections = [combined]
        if not detections:
            raise ValueError('YOLO가 옷을 찾지 못했어요. 옷 전체가 보이게 다시 찍어 주세요.')
        # 옷걸이처럼 작은 조각이 신뢰도만 높게 나와도 옷 본체가 뽑히도록 크기를 함께 본다.
        return max(detections, key=lambda item: item['confidence'] * int(np.asarray(item['mask']).sum()))


def yolo_grabcut_provider(garment_id=None, category=None, detector=None, iterations=3):
    detector = detector or _load_detector()

    def provide(image, _garment_id):
        selected_id = garment_id if garment_id is not None else _garment_id
        mask = detector.detect(image, selected_id, category)['mask'].astype(np.uint8)
        height, width = mask.shape
        radius = max(3, round(min(height, width) * .02))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
        sure_foreground = cv2.erode(mask, kernel)
        possible_foreground = cv2.dilate(mask, kernel)
        labels = np.full((height, width), cv2.GC_BGD, dtype=np.uint8)
        labels[possible_foreground > 0] = cv2.GC_PR_BGD
        labels[mask > 0] = cv2.GC_PR_FGD
        labels[sure_foreground > 0] = cv2.GC_FGD
        with _grabcut_lock:
            cv2.setRNGSeed(0)
            cv2.grabCut(image, labels, None, np.zeros((1, 65)), np.zeros((1, 65)), iterations, cv2.GC_INIT_WITH_MASK)
        return ((labels == cv2.GC_FGD) | (labels == cv2.GC_PR_FGD)).astype(np.uint8)

    provide.mask_method = 'YOLO→GrabCut'
    return provide


def get_yolo_grabcut_provider(garment_id=None, category=None):
    return yolo_grabcut_provider(garment_id, category)


def _load_sam_predictor():
    global _sam_predictor
    with _sam_load_lock:
        if _sam_predictor is None and os.environ.get('FITSHARE_SAM_PROVIDER') == 'replicate':
            # Vercel에는 PyTorch가 들어가지 않아 같은 SAM을 Replicate에서 실행해요.
            from tools.replicate_sam import ReplicateSamPredictor

            _sam_predictor = ReplicateSamPredictor(os.environ.get('REPLICATE_API_TOKEN', '').strip())
        if _sam_predictor is None:
            from experiments.garment_ai.sam_experiment import create_predictor

            checkpoint = MODEL_PATH.parent / 'sam_vit_b_01ec64.pth'
            if not checkpoint.is_file():
                raise RuntimeError('SAM 모델 파일을 server-data/model-cache에 준비해 주세요.')
            _sam_predictor = create_predictor(checkpoint)
    return _sam_predictor


def make_product_cutout(image, garment_id=None, category=None, *, detector=None, predictor=None):
    detector = detector if detector is not None else _load_detector()
    detection = detector.detect(image, garment_id, category)
    yolo_mask = np.asarray(detection['mask']) > 0
    if yolo_mask.shape != image.shape[:2] or not yolo_mask.any():
        raise ValueError('YOLO가 옷 위치를 찾지 못했어요.')
    rows, columns = np.nonzero(yolo_mask)
    # 두 바짓가랑이처럼 나뉜 영역도 모두 포함하는 상자를 SAM에 알려 줘요.
    box = np.array([columns.min(), rows.min(), columns.max(), rows.max()], dtype=np.float32)
    predictor = predictor if predictor is not None else _load_sam_predictor()
    with _sam_inference_lock:
        predictor.set_image(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        masks, _, _ = predictor.predict(box=box, multimask_output=False)
    mask = np.asarray(masks[0]) > 0
    if mask.shape != image.shape[:2] or not mask.any():
        raise ValueError('SAM이 옷 영역을 찾지 못했어요. 원본 사진을 확인해 주세요.')
    alpha = mask.astype(np.uint8) * 255
    left, top, width, height = cv2.boundingRect(alpha)
    cutout = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    cutout[:, :, 3] = alpha
    ok, encoded = cv2.imencode('.png', cutout[top:top + height, left:left + width])
    if not ok:
        raise RuntimeError('SAM 옷 분리 사진을 저장하지 못했어요.')
    return encoded.tobytes()
