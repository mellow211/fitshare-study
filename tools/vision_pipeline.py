"""OpenCV image processing used by the local measurement server."""

import base64
import json
import math
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHARUCO_MAP_PATH = PROJECT_ROOT / 'data' / 'charuco_marker_maps.json'
MAT_ALIASES = {
    'M1': 'point', 'point': 'point',
    'M2': 'cross', 'cross': 'cross',
    'M3': 'ChArUco', 'ChArUco': 'ChArUco',
    'M4': 'ChaArUco_Cross', 'ChaArUco_Cross': 'ChaArUco_Cross', 'charuco_cross': 'ChaArUco_Cross',
}
A0_WIDTH_CM = 84.1
A0_HEIGHT_CM = 118.9
OUTPUT_WIDTH = 600
OUTPUT_HEIGHT = 848
CHARUCO_SQUARE_SIZE_CM = 2.5
CHARUCO_MARKER_SIZE_CM = 1.875

def run_with_fallback(primary_attempt, fallback_attempt):
    """Run the alternate attempt only when the primary attempt fails."""
    try:
        result = primary_attempt()
    except Exception as primary_error:
        try:
            result = fallback_attempt()
        except Exception as fallback_error:
            raise ValueError(
                f'848px measurement failed: {primary_error}; '
                f'600px GrabCut fallback failed: {fallback_error}'
            ) from fallback_error
        return {**result, 'fallbackUsed': True, 'fallbackReason': str(primary_error)}
    return {**result, 'fallbackUsed': False, 'fallbackReason': None}


def measure_photo_with_fallback(photo, background_id, *, garment_id=None, category=None,
                                include_closet_image=False, allow_fallback=True):
    """Use 848px hybrid first; retry with 600px GrabCut only after failure.

    allow_fallback=False (옷 등록): 기장은 항상 YOLO→GrabCut이라 600px GrabCut 대체 측정을 하지 않는다.
    """
    def analyze(*, integrated):
        if integrated:
            flattened = rectify_photo(photo, background_id, preserve_resolution=True,
                                      max_output_long_side=848)
            working_side = 848
        else:
            flattened = rectify_photo(photo, background_id)
            working_side = 600
        if not flattened.get('ok'):
            raise ValueError(flattened.get('message', 'Photo correction failed.'))

        options = {}
        if integrated:
            try:
                from .garment_segmentation import get_yolo_grabcut_provider
            except ImportError:
                from garment_segmentation import get_yolo_grabcut_provider
            options['length_mask_provider'] = get_yolo_grabcut_provider(
                garment_id=garment_id, category=category
            )
        cv2, _ = _cv2_modules()
        cv2.setRNGSeed(0)
        measurement = measure_garment(
            flattened['image'], garment_id=garment_id, category=category,
            include_closet_image=include_closet_image,
            scale_cm_per_pixel=flattened['scaleCmPerPixel'],
            max_working_long_side=working_side,
            **options,
        )
        return {'measurement': measurement, 'rectified': flattened}

    if allow_fallback:
        result = run_with_fallback(lambda: analyze(integrated=True),
                                   lambda: analyze(integrated=False))
    else:
        result = {**analyze(integrated=True), 'fallbackUsed': False, 'fallbackReason': None}
    result['pipelineMode'] = '600px GrabCut fallback' if result['fallbackUsed'] else '848px hybrid'
    return result



def _cv2_modules():
    try:
        import cv2
        import numpy as np
    except ImportError as error:
        raise RuntimeError('자동 사진 분석을 쓰려면 requirements-vision.txt의 OpenCV를 설치해 주세요.') from error
    return cv2, np


def image_to_data_url(image):
    """사진을 서버 응답에 넣을 수 있는 작은 JPG 주소로 바꾼다."""
    cv2, _ = _cv2_modules()
    ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 76])
    if not ok:
        raise RuntimeError('사진 결과를 만들지 못했어요.')
    return 'data:image/jpeg;base64,' + base64.b64encode(encoded.tobytes()).decode('ascii')


def _read_image(source):
    cv2, np = _cv2_modules()
    if hasattr(source, 'shape'):
        image = source
    elif isinstance(source, (bytes, bytearray, memoryview)):
        image = cv2.imdecode(np.frombuffer(source, dtype=np.uint8), cv2.IMREAD_COLOR)
    else:
        # OpenCV's Windows file APIs do not reliably handle Unicode paths.
        image = cv2.imdecode(np.frombuffer(Path(source).read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or len(image.shape) != 3 or image.shape[2] != 3:
        raise ValueError('사진을 열지 못했어요. JPG나 PNG 사진을 다시 골라 주세요.')
    return image


def _resize_long_side(image, maximum=1000):
    cv2, _ = _cv2_modules()
    height, width = image.shape[:2]
    scale = min(1.0, maximum / max(height, width))
    if scale == 1:
        return image.copy()
    return cv2.resize(image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)


def _cm_per_pixel_from_spacing(horizontal_pixels, vertical_pixels, horizontal_cm, vertical_cm):
    """Convert a known pattern pitch in the rectification map into cm per pixel."""
    values = (horizontal_pixels, vertical_pixels, horizontal_cm, vertical_cm)
    if any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError('배경 무늬의 실제 간격과 픽셀 거리를 확인해 주세요.')
    return {'x': float(horizontal_cm / horizontal_pixels), 'y': float(vertical_cm / vertical_pixels)}


def _measure_pattern_scale(background_id, reference_points=None):
    """Derive cm/px from known dot, grid, or marker pitch in the rectification map."""
    _, np = _cv2_modules()
    canonical = MAT_ALIASES.get(str(background_id))
    if canonical == 'point':
        points = np.asarray(reference_points, dtype=np.float64).reshape(4, 2)
        # 외곽 점 9×12개는 가로 8칸·세로 11칸이고, 중심 간격은 10cm예요.
        horizontal_pitch = float(np.linalg.norm(points[1] - points[0]) / 8)
        vertical_pitch = float(np.linalg.norm(points[3] - points[0]) / 11)
        return _cm_per_pixel_from_spacing(horizontal_pitch, vertical_pitch, 10, 10)
    if canonical == 'cross':
        points = np.asarray(reference_points, dtype=np.float64).reshape(4, 2)
        # M2 기준 무늬는 가로 16칸·세로 23칸이며 한 칸은 5cm예요.
        horizontal_pitch = float(np.linalg.norm(points[1] - points[0]) / 16)
        vertical_pitch = float(np.linalg.norm(points[3] - points[0]) / 23)
        return _cm_per_pixel_from_spacing(horizontal_pitch, vertical_pitch, 5, 5)
    if canonical in {'ChArUco', 'ChaArUco_Cross'}:
        marker_map = _charuco_reference(canonical)
        horizontal_sides = []
        vertical_sides = []
        for marker in marker_map.values():
            points = np.asarray(marker, dtype=np.float64).reshape(4, 2)
            horizontal_sides.extend((float(np.linalg.norm(points[1] - points[0])),
                                     float(np.linalg.norm(points[2] - points[3]))))
            vertical_sides.extend((float(np.linalg.norm(points[2] - points[1])),
                                   float(np.linalg.norm(points[3] - points[0]))))
        marker_to_square_ratio = CHARUCO_SQUARE_SIZE_CM / CHARUCO_MARKER_SIZE_CM
        square_pitch_x = float(np.median(horizontal_sides)) * marker_to_square_ratio
        square_pitch_y = float(np.median(vertical_sides)) * marker_to_square_ratio
        return _cm_per_pixel_from_spacing(square_pitch_x, square_pitch_y,
                                          CHARUCO_SQUARE_SIZE_CM, CHARUCO_SQUARE_SIZE_CM)
    raise ValueError('배경 무늬 종류를 확인해 주세요.')


def _ordered_quad(points):
    _, np = _cv2_modules()
    points = np.asarray(points, dtype=np.float32).reshape(4, 2)
    # 사진은 세로 A0 매트이므로 y가 작은 꼭짓점 두 개가 위쪽 변을 이룬다.
    # x+y의 가장 작은 점만 찾는 방식은 비스듬한 사진에서 꼭짓점을 중복 선택할 수 있다.
    top = points[np.argsort(points[:, 1])[:2]]
    bottom = points[np.argsort(points[:, 1])[2:]]
    top = top[np.argsort(top[:, 0])]
    bottom = bottom[np.argsort(bottom[:, 0])]
    return np.asarray([top[0], top[1], bottom[1], bottom[0]], dtype=np.float32)


def _warp_with_correspondences(image, source_points, target_points):
    cv2, np = _cv2_modules()
    source = np.asarray(source_points, dtype=np.float32)
    target = np.asarray(target_points, dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(source, target)
    flattened = cv2.warpPerspective(image, matrix, (OUTPUT_WIDTH, OUTPUT_HEIGHT), flags=cv2.INTER_CUBIC,
                                    borderMode=cv2.BORDER_REPLICATE)
    return flattened, matrix


PERSPECTIVE_ERROR_THRESHOLD = 0.005


def choose_rectification_mode(source_points, target_points, threshold=PERSPECTIVE_ERROR_THRESHOLD):
    """Use perspective correction only when an affine map misses the fourth corner."""
    cv2, np = _cv2_modules()
    source = np.asarray(source_points, dtype=np.float32).reshape(-1, 2)
    target = np.asarray(target_points, dtype=np.float32).reshape(-1, 2)
    if len(source) != len(target) or len(source) < 4:
        raise ValueError('원근 보정 여부를 확인하려면 표식 위치가 네 개 이상 필요해요.')
    if len(source) == 4:
        affine = cv2.getAffineTransform(source[:3], target[:3])
    else:
        affine, _ = cv2.estimateAffine2D(source, target, method=cv2.RANSAC, ransacReprojThreshold=3.5)
        if affine is None:
            raise ValueError('표식 위치로 사진의 원근을 확인하지 못했어요.')
    predicted = cv2.transform(source.reshape(-1, 1, 2), affine).reshape(-1, 2)
    errors = np.linalg.norm(predicted - target, axis=1)
    diagonal = max(float(np.linalg.norm(target.max(axis=0) - target.min(axis=0))), 1.0)
    score = float(np.sqrt(np.mean(np.square(errors))) / diagonal)
    return {
        'mode': 'perspective' if score >= threshold else 'affine',
        'perspectiveScore': score,
        'threshold': threshold,
        'affineMatrix': affine.tolist(),
    }


def _warp_affine_with_correspondences(image, source_points, target_points):
    cv2, np = _cv2_modules()
    source = np.asarray(source_points, dtype=np.float32).reshape(-1, 2)
    target = np.asarray(target_points, dtype=np.float32).reshape(-1, 2)
    matrix = cv2.getAffineTransform(source[:3], target[:3])
    aligned = cv2.warpAffine(image, matrix, (OUTPUT_WIDTH, OUTPUT_HEIGHT), flags=cv2.INTER_CUBIC,
                             borderMode=cv2.BORDER_REPLICATE)
    return aligned, matrix


def _detect_dot_mat(image, accurate_grid=False):
    cv2, np = _cv2_modules()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, dark = cv2.threshold(gray, 110, 255, cv2.THRESH_BINARY_INV)
    count, _, stats, centers = cv2.connectedComponentsWithStats(dark, connectivity=8)
    points = []
    for index in range(1, count):
        x, y, width, height, area = stats[index]
        ratio = width / max(height, 1)
        radius = max(width, height)
        circularity = area / (np.pi * ((width + height) / 4) ** 2) if width + height else 0
        if 12 <= area <= 180 and .7 <= ratio <= 1.4 and radius <= 18 and circularity >= .55:
            points.append(centers[index])
    if len(points) < 24:
        raise ValueError('동그란 점을 충분히 못 찾았어요. 옷을 조금 옮기고 점 배경이 더 보이게 찍어 주세요.')

    point_array = np.asarray(points, dtype=np.float32)
    if accurate_grid:
        hull = cv2.convexHull(point_array.reshape(-1, 1, 2))
        perimeter = cv2.arcLength(hull, True)
        corners = cv2.approxPolyDP(hull, max(2.0, perimeter * .035), True).reshape(-1, 2)
    else:
        corners = np.empty((0, 2), dtype=np.float32)
    if len(corners) == 4:
        source = _ordered_quad(corners)
    else:
        rectangle = cv2.boxPoints(cv2.minAreaRect(point_array))
        source = _ordered_quad(rectangle)
    if np.linalg.norm(source[3] - source[0]) < np.linalg.norm(source[1] - source[0]):
        raise ValueError('점 배경이 옆으로 누워 보여요. 매트가 세로가 되게 사진을 돌려 주세요.')

    # 인쇄 도면의 9×12 점은 10cm 간격이며, 바깥 점은 종이 모서리에서 일정하게 떨어져 있어요.
    left_top_cm = 2.05
    target = np.array([
        [left_top_cm / A0_WIDTH_CM * OUTPUT_WIDTH, 4.45 / A0_HEIGHT_CM * OUTPUT_HEIGHT],
        [82.05 / A0_WIDTH_CM * OUTPUT_WIDTH, 4.45 / A0_HEIGHT_CM * OUTPUT_HEIGHT],
        [82.05 / A0_WIDTH_CM * OUTPUT_WIDTH, 114.45 / A0_HEIGHT_CM * OUTPUT_HEIGHT],
        [2.05 / A0_WIDTH_CM * OUTPUT_WIDTH, 114.45 / A0_HEIGHT_CM * OUTPUT_HEIGHT],
    ], dtype=np.float32)
    return source, target, min(.94, .48 + len(points) / 150)


def _cluster_grid_lines(image):
    cv2, np = _cv2_modules()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 40, 120)
    height, width = gray.shape
    threshold = max(24, int(min(width, height) * .045))
    min_length = max(24, int(min(width, height) * .035))
    raw = cv2.HoughLinesP(edges, 1, np.pi / 360, threshold=threshold,
                          minLineLength=min_length, maxLineGap=max(8, int(min(width, height) * .015)))
    if raw is None:
        raise ValueError('모눈 선을 찾지 못했어요. 격자 배경 전체가 보이게 다시 찍어 주세요.')

    horizontal = []
    vertical = []
    center_x = width / 2
    center_y = height / 2
    for x1, y1, x2, y2 in raw.reshape(-1, 4):
        dx = x2 - x1
        dy = y2 - y1
        length = float(np.hypot(dx, dy))
        angle = abs(float(np.degrees(np.arctan2(dy, dx))))
        if angle > 90:
            angle = 180 - angle
        if angle <= 14 and abs(dx) > 1:
            y_at_center = y1 + dy / dx * (center_x - x1)
            horizontal.append((float(y_at_center), length, (x1, y1), (x2, y2)))
        elif angle >= 76 and abs(dy) > 1:
            x_at_center = x1 + dx / dy * (center_y - y1)
            vertical.append((float(x_at_center), length, (x1, y1), (x2, y2)))

    tolerance = max(5, min(width, height) * .012)

    def groups_for(items):
        items.sort(key=lambda item: item[0])
        groups = []
        for item in items:
            if not groups or item[0] - np.average([part[0] for part in groups[-1]], weights=[part[1] for part in groups[-1]]) > tolerance:
                groups.append([item])
            else:
                groups[-1].append(item)
        minimum_length = max(65, min(width, height) * .11)
        groups = [group for group in groups if sum(item[1] for item in group) >= minimum_length]
        return groups

    horizontal_groups = groups_for(horizontal)
    vertical_groups = groups_for(vertical)
    if len(horizontal_groups) < 8 or len(vertical_groups) < 8:
        raise ValueError('격자 선이 너무 적게 보여요. 옷을 옮겨 격자를 더 드러내고 찍어 주세요.')

    def fit_group(group):
        points = np.asarray([point for item in group for point in item[2:]], dtype=np.float32).reshape(-1, 1, 2)
        vx, vy, x0, y0 = cv2.fitLine(points, cv2.DIST_L2, 0, .01, .01).flatten()
        return np.asarray([vx, vy, x0, y0], dtype=np.float64)

    return [fit_group(group) for group in horizontal_groups], [fit_group(group) for group in vertical_groups]


def _line_intersection(line_a, line_b):
    _, np = _cv2_modules()
    ax, ay, x1, y1 = line_a
    bx, by, x2, y2 = line_b
    matrix = np.asarray([[ax, -bx], [ay, -by]], dtype=np.float64)
    if abs(np.linalg.det(matrix)) < 1e-5:
        raise ValueError('격자 선이 서로 평행해서 사진을 펼 수 없어요.')
    parameters = np.linalg.solve(matrix, np.asarray([x2 - x1, y2 - y1]))
    return np.asarray([x1 + parameters[0] * ax, y1 + parameters[0] * ay], dtype=np.float32)


def _detect_cross_mat(image):
    cv2, np = _cv2_modules()
    horizontal, vertical = _cluster_grid_lines(image)
    horizontal.sort(key=lambda line: line[3])
    vertical.sort(key=lambda line: line[2])
    source = np.asarray([
        _line_intersection(horizontal[0], vertical[0]),
        _line_intersection(horizontal[0], vertical[-1]),
        _line_intersection(horizontal[-1], vertical[-1]),
        _line_intersection(horizontal[-1], vertical[0]),
    ], dtype=np.float32)
    source = _ordered_quad(source)
    target = np.asarray([
        [2.05 / A0_WIDTH_CM * OUTPUT_WIDTH, 1.95 / A0_HEIGHT_CM * OUTPUT_HEIGHT],
        [82.05 / A0_WIDTH_CM * OUTPUT_WIDTH, 1.95 / A0_HEIGHT_CM * OUTPUT_HEIGHT],
        [82.05 / A0_WIDTH_CM * OUTPUT_WIDTH, 116.95 / A0_HEIGHT_CM * OUTPUT_HEIGHT],
        [2.05 / A0_WIDTH_CM * OUTPUT_WIDTH, 116.95 / A0_HEIGHT_CM * OUTPUT_HEIGHT],
    ], dtype=np.float32)
    horizontal_gaps = np.diff([line[3] for line in horizontal])
    vertical_gaps = np.diff([line[2] for line in vertical])
    median_gap = float(np.median(np.concatenate([horizontal_gaps, vertical_gaps])))
    if not 18 <= median_gap <= min(image.shape[:2]) * .10:
        raise ValueError('격자 간격이 알맞지 않아요. 5cm 모눈 배경을 선택했는지 확인해 주세요.')
    confidence = min(.92, .55 + min(len(horizontal), len(vertical)) / 65)
    return source, target, confidence


def _charuco_reference(background_id):
    try:
        content = json.loads(CHARUCO_MAP_PATH.read_text(encoding='utf-8'))
        return content['backgrounds'][background_id]['markers']
    except (OSError, KeyError, json.JSONDecodeError) as error:
        raise RuntimeError(f'{background_id} 표식 기준표를 읽지 못했어요.') from error


def _detect_charuco_mat(image, background_id, perspective_policy='always'):
    cv2, np = _cv2_modules()
    if not hasattr(cv2, 'aruco'):
        raise RuntimeError('이 OpenCV에는 ArUco 표식 인식 기능이 없어요. contrib 버전을 설치해 주세요.')
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_1000)
    detector = cv2.aruco.ArucoDetector(dictionary)
    corners, ids, _ = detector.detectMarkers(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))
    if ids is None:
        raise ValueError('ChArUco 표식을 못 찾았어요. 표식이 있는 가장자리를 사진에 담아 주세요.')

    marker_map = _charuco_reference(background_id)
    source_points = []
    target_points = []
    matched_markers = 0
    for marker_corners, marker_id in zip(corners, ids.flatten()):
        reference = marker_map.get(str(int(marker_id)))
        if reference is None:
            continue
        source_points.extend(marker_corners.reshape(4, 2).tolist())
        target_points.extend(reference)
        matched_markers += 1
    if matched_markers < 6:
        raise ValueError('ChArUco 표식이 너무 적게 보여요. 매트 가장자리를 더 많이 찍어 주세요.')

    matrix, inliers = cv2.findHomography(np.asarray(source_points, dtype=np.float32),
                                         np.asarray(target_points, dtype=np.float32), cv2.RANSAC, 3.5)
    if matrix is None or inliers is None:
        raise ValueError('ChArUco 표식으로 사진을 펼 수 없어요. 네 가장자리를 다시 찍어 주세요.')
    inlier_ratio = float(np.mean(inliers))
    inlier_markers = int(inliers.reshape(-1, 4).all(axis=1).sum())
    if inlier_markers < 8 or inlier_ratio < .25:
        raise ValueError('표식이 서로 맞지 않아요. 사진 배경 종류를 확인해 주세요.')
    source_array = np.asarray(source_points, dtype=np.float32)
    target_array = np.asarray(target_points, dtype=np.float32)
    decision = choose_rectification_mode(source_array, target_array)
    affine_matrix = np.asarray(decision['affineMatrix'], dtype=np.float32)
    perspective_score = decision['perspectiveScore']
    use_affine = perspective_policy == 'conditional' and decision['mode'] == 'affine'
    if use_affine:
        flattened = cv2.warpAffine(image, affine_matrix, (OUTPUT_WIDTH, OUTPUT_HEIGHT), flags=cv2.INTER_CUBIC,
                                   borderMode=cv2.BORDER_REPLICATE)
        applied_matrix = affine_matrix
        mode = 'affine'
    else:
        flattened = cv2.warpPerspective(image, matrix, (OUTPUT_WIDTH, OUTPUT_HEIGHT), flags=cv2.INTER_CUBIC,
                                        borderMode=cv2.BORDER_REPLICATE)
        applied_matrix = matrix
        mode = 'perspective'
    return flattened, applied_matrix, min(.99, .55 + inlier_ratio * .42), matched_markers, mode, perspective_score


def rectify_photo(source, background_id, perspective_policy='always', preserve_resolution=False,
                  max_output_long_side=1600):
    """Orient and perspective-correct one calibration-mat photograph to A0."""
    cv2, np = _cv2_modules()
    canonical_background = MAT_ALIASES.get(str(background_id))
    if canonical_background is None:
        return {'ok': False, 'image': None, 'confidence': 0, 'message': '배경 종류를 다시 선택해 주세요.'}
    try:
        original = _read_image(source)
        image = _resize_long_side(original)
        detect_height, detect_width = image.shape[:2]
        cv2.setRNGSeed(0)
        if perspective_policy not in {'always', 'conditional'}:
            raise ValueError('사진 보정 방식이 올바르지 않아요.')
        perspective_score = None
        pattern_reference_points = None
        if canonical_background == 'point':
            source_points, target_points, confidence = _detect_dot_mat(image, accurate_grid=perspective_policy == 'conditional')
            pattern_reference_points = target_points
            if perspective_policy == 'conditional':
                decision = choose_rectification_mode(source_points, target_points)
                perspective_score = decision['perspectiveScore']
                if decision['mode'] == 'affine':
                    flattened, matrix = _warp_affine_with_correspondences(image, source_points, target_points)
                    mode = 'affine'
                else:
                    flattened, matrix = _warp_with_correspondences(image, source_points, target_points)
                    mode = 'perspective'
            else:
                flattened, matrix = _warp_with_correspondences(image, source_points, target_points)
                mode = 'perspective'
            method = '10cm 점 격자'
            detected_markers = None
        elif canonical_background == 'cross':
            source_points, target_points, confidence = _detect_cross_mat(image)
            pattern_reference_points = target_points
            if perspective_policy == 'conditional':
                decision = choose_rectification_mode(source_points, target_points)
                perspective_score = decision['perspectiveScore']
                if decision['mode'] == 'affine':
                    flattened, matrix = _warp_affine_with_correspondences(image, source_points, target_points)
                    mode = 'affine'
                else:
                    flattened, matrix = _warp_with_correspondences(image, source_points, target_points)
                    mode = 'perspective'
            else:
                flattened, matrix = _warp_with_correspondences(image, source_points, target_points)
                mode = 'perspective'
            method = '5cm 모눈 선'
            detected_markers = None
        else:
            flattened, matrix, confidence, detected_markers, mode, perspective_score = _detect_charuco_mat(
                image, canonical_background, perspective_policy=perspective_policy
            )
            method = 'ChArUco ID 표식'
        output_scale = 1.0
        if preserve_resolution:
            output_scale = min(max_output_long_side / OUTPUT_HEIGHT,
                               max(1.0, original.shape[0] / OUTPUT_HEIGHT))
            output_width = round(OUTPUT_WIDTH * output_scale)
            output_height = round(OUTPUT_HEIGHT * output_scale)
            input_scale_x = detect_width / original.shape[1]
            input_scale_y = detect_height / original.shape[0]
            input_map = np.diag([input_scale_x, input_scale_y, 1.0])
            output_map = np.diag([output_scale, output_scale, 1.0])
            matrix_array = np.asarray(matrix, dtype=np.float64)
            if matrix_array.shape == (2, 3):
                matrix_array = np.vstack([matrix_array, [0, 0, 1]])
            high_matrix = output_map @ matrix_array @ input_map
            if mode == 'affine':
                flattened = cv2.warpAffine(original, high_matrix[:2], (output_width, output_height),
                                           flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
            else:
                flattened = cv2.warpPerspective(original, high_matrix, (output_width, output_height),
                                                flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        scale_cm_per_pixel = _measure_pattern_scale(canonical_background, pattern_reference_points)
        scale_cm_per_pixel = {axis: value / output_scale for axis, value in scale_cm_per_pixel.items()}
        return {
            'ok': True, 'image': flattened, 'matrix': matrix.tolist(),
            'scaleCmPerPixel': scale_cm_per_pixel,
            'confidence': float(confidence), 'method': method,
            'rectificationMode': mode, 'perspectiveScore': perspective_score,
            'detectedMarkers': detected_markers,
            'canvas': {'width': int(flattened.shape[1]), 'height': int(flattened.shape[0])},
            'message': f'{method}을 찾아 사진을 A0 정면 모양으로 펼쳤어요.'
        }
    except (ValueError, RuntimeError) as error:
        return {'ok': False, 'image': None, 'confidence': 0, 'message': str(error)}


GARMENT_PROFILES = {
    1: {'name': '상의 외투', 'widthRatio': .40, 'lengthSpanRatio': .60, 'bottomRatio': .68, 'seedY': .24},
    2: {'name': '흰 크롭티셔츠', 'widthRatio': .325, 'lengthSpanRatio': .75, 'bottomRatio': .48, 'seedY': .22},
    3: {'name': '원피스', 'widthRatio': .15, 'lengthSpanRatio': .65, 'bottomRatio': .78, 'seedY': .28},
    4: {'name': '점퍼', 'widthRatio': .20, 'lengthSpanRatio': .50, 'bottomRatio': .63, 'seedY': .22},
    5: {'name': '바지', 'widthRatio': .10, 'lengthSpanRatio': .525, 'bottomRatio': .96, 'seedY': .22},
    6: {'name': '치마', 'widthRatio': .12, 'lengthSpanRatio': .78, 'bottomRatio': .96, 'seedY': .20},
}


def width_line_y(width_top, width_bottom, width_ratio):
    if (isinstance(width_ratio, bool) or not isinstance(width_ratio, (int, float))
            or not math.isfinite(width_ratio) or not 0 < width_ratio < 1):
        raise ValueError('너비선 비율은 0보다 크고 1보다 작아야 해요.')
    return int(width_top + (width_bottom - width_top) * width_ratio)
CATEGORY_PROFILES = {
    'top': {'widthRatio': .4, 'bottomRatio': .70, 'seedY': .22},
    'dress': {'widthRatio': .22, 'bottomRatio': .80, 'seedY': .28},
    'pants': {'widthRatio': .08, 'bottomRatio': .96, 'seedY': .22},
    'bottom': {'widthRatio': .08, 'bottomRatio': .96, 'seedY': .22},
    'skirt': {'widthRatio': .12, 'bottomRatio': .96, 'seedY': .20},
}


def _component_mask(image, garment_id=None, category=None, max_working_long_side=848, mask_provider=None):
    cv2, np = _cv2_modules()
    profile = GARMENT_PROFILES.get(garment_id) if garment_id in GARMENT_PROFILES else None
    width_kind = 'waist' if garment_id in {5, 6} or category in {'pants', 'bottom', 'skirt'} else 'chest'
    if profile is None:
        profile = CATEGORY_PROFILES.get(category, CATEGORY_PROFILES['top'])

    scale = min(1.0, max_working_long_side / max(image.shape[:2]))
    working = cv2.resize(image, (round(image.shape[1] * scale), round(image.shape[0] * scale)), interpolation=cv2.INTER_AREA)
    height, width = working.shape[:2]
    is_pants = garment_id == 5 or category in {'pants', 'bottom'}
    bottom_ratio = profile['bottomRatio']
    if mask_provider is not None:
        # 실험 6: GrabCut 대신 다른 분할 모듈이 준 마스크를 같은 뒤처리로 측정한다.
        binary = (np.asarray(mask_provider(working, garment_id)) > 0).astype(np.uint8)
    else:
        labels = np.full((height, width), cv2.GC_PR_BGD, dtype=np.uint8)
        left_bound = .08 if is_pants else .10
        right_bound = .92 if is_pants else .90
        labels[int(height * .025):int(height * bottom_ratio), int(width * left_bound):int(width * right_bound)] = cv2.GC_PR_FGD

        if is_pants:
            labels[:, :int(width * .07)] = cv2.GC_BGD
            labels[:, int(width * .93):] = cv2.GC_BGD
            labels[int(height * .94):, :] = cv2.GC_BGD
            labels[int(height * .17):int(height * .55), int(width * .30):int(width * .45)] = cv2.GC_FGD
            labels[int(height * .17):int(height * .55), int(width * .55):int(width * .70)] = cv2.GC_FGD
        else:
            labels[:, :int(width * .18)] = cv2.GC_BGD
            labels[:, int(width * .82):] = cv2.GC_BGD
            labels[int(height * bottom_ratio):, :] = cv2.GC_BGD
            seed_top = int(height * profile['seedY'])
            labels[seed_top:int(height * .54), int(width * .40):int(width * .60)] = cv2.GC_FGD

        background_model = np.zeros((1, 65), dtype=np.float64)
        foreground_model = np.zeros((1, 65), dtype=np.float64)
        cv2.grabCut(working, labels, None, background_model, foreground_model, 3, cv2.GC_INIT_WITH_MASK)
        binary = ((labels == cv2.GC_FGD) | (labels == cv2.GC_PR_FGD)).astype(np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    count, component_labels, stats, centers = cv2.connectedComponentsWithStats(binary, connectivity=8)
    components = []
    for index in range(1, count):
        x, y, box_width, box_height, area = stats[index]
        center_x, center_y = centers[index]
        if area < width * height * .008 or center_x < width * .15 or center_x > width * .85:
            continue
        components.append((area, index))
    if not components:
        raise ValueError('옷 윤곽을 못 찾았어요. 옷과 배경이 잘 보이게 다시 찍어 주세요.')
    components.sort(reverse=True)
    selected = [components[0][1]]
    if is_pants:
        largest_area = components[0][0]
        selected.extend(index for area, index in components[1:] if area >= largest_area * .22)
    garment_mask = np.isin(component_labels, selected).astype(np.uint8)
    if garment_mask.sum() < width * height * .035:
        raise ValueError('옷의 보이는 부분이 너무 작아요. 옷을 매트 가운데에 놓고 다시 찍어 주세요.')
    return working, garment_mask, profile, width_kind


def _refine_mask_by_garment_color(working, garment_mask, width_kind):
    """옷 안쪽 색을 표본으로 삼아 매트와 닮지 않은 픽셀을 거른다."""
    cv2, np = _cv2_modules()
    height, width = garment_mask.shape
    lab = cv2.cvtColor(working, cv2.COLOR_BGR2LAB).astype(np.float32)
    sample_centers = [(0.36, 0.32), (0.64, 0.32)] if width_kind == 'waist' else [(0.50, 0.35)]
    sample_colors = []
    for ratio_x, ratio_y in sample_centers:
        center_x = int(width * ratio_x)
        center_y = int(height * ratio_y)
        radius = max(2, round(min(width, height) * 0.015))
        patch = lab[max(0, center_y - radius):center_y + radius + 1,
                    max(0, center_x - radius):center_x + radius + 1]
        sample_colors.append(np.median(patch.reshape(-1, 3), axis=0))

    # 밝은 매트나 검은 표식은 옷 안쪽 표본과 색 차이가 커서 자동으로 걸러진다.
    distances = np.stack([np.linalg.norm(lab - color, axis=2) for color in sample_colors])
    refined = (garment_mask.astype(bool) & (distances.min(axis=0) <= 75)).astype(np.uint8)
    refined = cv2.morphologyEx(
        refined, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    )
    count, labels, stats, _ = cv2.connectedComponentsWithStats(refined, connectivity=8)
    selected = set()
    for ratio_x, ratio_y in sample_centers:
        label = int(labels[int(height * ratio_y), int(width * ratio_x)])
        if label > 0:
            selected.add(label)
    if selected:
        refined = np.isin(labels, list(selected)).astype(np.uint8)
    if refined.sum() < width * height * 0.035:
        return garment_mask
    return refined


def validate_garment_dimensions(width_cm, length_cm):
    """A tiny isolated fragment must not be reported as a whole garment."""
    if not (5 <= width_cm <= 200 and 15 <= length_cm <= 300):
        raise ValueError('옷 전체를 찾지 못했어요. 옷이 모두 보이게 다시 찍어 주세요.')


def refine_hem_bottom(image, mask, top, bottom):
    """Trim a connected soft shadow only when a clear image edge sits just above it."""
    cv2, np = _cv2_modules()
    if image.shape[:2] != mask.shape or bottom <= top + 2 or bottom >= mask.shape[0] - 1:
        return int(bottom)
    garment_height = bottom - top
    search_radius = min(24, max(8, round(garment_height * .04)))
    start = max(top + 2, bottom - search_radius)
    sample_xs = np.flatnonzero(mask[bottom] > 0)
    if len(sample_xs) < 8:
        recent_rows = mask[max(top, bottom - 2):bottom + 1]
        sample_xs = np.flatnonzero(np.any(recent_rows > 0, axis=0))
    if len(sample_xs) < 8:
        return int(bottom)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    scores = []
    for y in range(start, bottom):
        # The two-pixel difference suppresses small fabric texture while retaining a hem/shadow edge.
        differences = np.abs(gray[y + 1, sample_xs] - gray[y - 1, sample_xs])
        scores.append(float(np.median(differences)))
    if not scores:
        return int(bottom)
    strongest = max(scores)
    if strongest < 24:
        return int(bottom)
    threshold = max(24, strongest * .88)
    for y, score in zip(range(start, bottom), scores):
        if score >= threshold:
            return int(y)
    return int(bottom)


def _dominant_color_name(image, mask):
    cv2, np = _cv2_modules()
    pixels = image[mask > 0]
    if len(pixels) == 0:
        return '색을 찾지 못했어요'
    median = np.median(pixels, axis=0).astype(np.uint8).reshape(1, 1, 3)
    hue, saturation, value = (int(part) for part in cv2.cvtColor(median, cv2.COLOR_BGR2HSV)[0, 0])
    if value < 48:
        return '검정'
    if saturation < 35:
        return '흰색' if value > 205 else '회색'
    for hue_limit, name in ((10, '빨강'), (25, '주황'), (35, '노랑'), (85, '초록'),
                            (100, '하늘색'), (130, '파랑'), (160, '보라'), (180, '빨강')):
        if hue < hue_limit:
            return name
    return '빨강'


def measure_garment(image, garment_id=None, category=None, include_closet_image=False, *,
                    scale_cm_per_pixel, max_working_long_side=848, mask_provider=None,
                    width_mask_provider=None, length_mask_provider=None, width_ratio_override=None):
    """Measure the garment's semantic width line and length on a flattened A0 image."""
    cv2, np = _cv2_modules()
    if mask_provider is not None:
        width_mask_provider = width_mask_provider or mask_provider
        length_mask_provider = length_mask_provider or mask_provider
    working, base_mask, profile, width_kind = _component_mask(
        image, garment_id, category, max_working_long_side=max_working_long_side,
        mask_provider=width_mask_provider)
    if length_mask_provider is None or length_mask_provider is width_mask_provider:
        length_working = working
        length_base_mask = base_mask
    else:
        length_working, length_base_mask, _, _ = _component_mask(
            image, garment_id, category, max_working_long_side=max_working_long_side,
            mask_provider=length_mask_provider)
    mask = _refine_mask_by_garment_color(working, base_mask, width_kind)
    height, width = base_mask.shape

    def row_spans(binary_mask):
        spans = np.zeros(height, dtype=np.float32)
        for y in range(height):
            xs = np.flatnonzero(binary_mask[y])
            if len(xs) >= 2:
                spans[y] = xs[-1] - xs[0] + 1
        return spans

    base_spans = row_spans(length_base_mask)
    maximum_span = float(base_spans.max())
    if maximum_span < width * .12:
        raise ValueError('옷의 양쪽 끝을 찾지 못했어요. 옷 전체가 사진에 들어오게 해 주세요.')
    # dataset에서 옷 종류별로 비교해 정한 기장 끝점 기준 비율을 쓴다.
    base_threshold = maximum_span * profile.get('lengthSpanRatio', .55)
    active_rows = np.flatnonzero(base_spans >= base_threshold)
    if len(active_rows) < height * .12:
        raise ValueError('옷의 위아래 끝을 찾지 못했어요. 사진을 다시 찍어 주세요.')
    top = int(active_rows[0])
    bottom = int(active_rows[-1])
    refined_bottom = refine_hem_bottom(length_working, length_base_mask, top, bottom)
    if refined_bottom < bottom:
        length_base_mask[refined_bottom + 1:] = 0
        base_spans = row_spans(length_base_mask)
        active_rows = np.flatnonzero(base_spans >= base_threshold)
        if len(active_rows) >= height * .12:
            top = int(active_rows[0])
            bottom = int(active_rows[-1])
    garment_height = bottom - top

    spans = row_spans(mask)
    refined_maximum_span = float(spans.max())
    if refined_maximum_span < width * .12:
        mask = base_mask
        spans = base_spans
        refined_maximum_span = maximum_span
    refined_rows = np.flatnonzero(spans >= refined_maximum_span * .55)
    if len(refined_rows) < height * .12:
        mask = base_mask
        spans = base_spans
        refined_rows = active_rows
    width_top = int(refined_rows[0])
    width_bottom = int(refined_rows[-1])
    width_height = width_bottom - width_top
    target_ratio = profile['widthRatio'] if width_ratio_override is None else width_ratio_override
    target_y = width_line_y(width_top, width_bottom, target_ratio)
    band_radius = max(2, round(width_height * .012))
    band = spans[max(width_top, target_y - band_radius):min(width_bottom + 1, target_y + band_radius + 1)]
    valid_spans = band[band > refined_maximum_span * .55 * .60]
    if len(valid_spans) == 0:
        raise ValueError('가슴선 또는 허리선을 찾지 못했어요. 직접 위치를 확인해 주세요.')
    width_pixels = float(np.median(valid_spans))

    if (
        not isinstance(scale_cm_per_pixel, dict)
        or any(isinstance(scale_cm_per_pixel.get(axis), bool)
               or not isinstance(scale_cm_per_pixel.get(axis), (int, float))
               or not math.isfinite(scale_cm_per_pixel[axis])
               or scale_cm_per_pixel[axis] <= 0 for axis in ('x', 'y'))
    ):
        raise ValueError('배경 무늬로 계산한 픽셀 크기를 확인해 주세요.')
    # 보정 사진을 마스크 크기로 줄인 비율을 반영해, 배경 무늬에서 얻은 cm/px를 적용합니다.
    output_height, output_width = image.shape[:2]
    cm_per_pixel_x = scale_cm_per_pixel['x'] * (output_width / width)
    cm_per_pixel_y = scale_cm_per_pixel['y'] * (output_height / height)
    width_cm = width_pixels * cm_per_pixel_x
    length_cm = garment_height * cm_per_pixel_y
    if not (5 <= width_cm <= 200 and 5 <= length_cm <= 300):
        raise ValueError('자동 측정 크기가 너무 커요. 옷 종류와 사진을 확인해 주세요.')
    validate_garment_dimensions(width_cm, length_cm)

    full_mask = cv2.resize(length_base_mask * 255, (output_width, output_height), interpolation=cv2.INTER_NEAREST)
    left, top_box, box_width, box_height = cv2.boundingRect(full_mask)
    closet_image_data_url = None
    if include_closet_image:
        # 옷장에서는 측정 매트가 보이지 않도록 옷 마스크만 투명 PNG로 저장합니다.
        # 이 이미지는 보여주기 전용이며, 치수 계산이나 원본 보관에는 쓰지 않습니다.
        margin_x = max(2, round(box_width * .04))
        margin_y = max(2, round(box_height * .025))
        cut_left = max(0, left - margin_x)
        cut_top = max(0, top_box - margin_y)
        cut_right = min(output_width, left + box_width + margin_x)
        cut_bottom = min(output_height, top_box + box_height + margin_y)
        cutout = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
        cutout[:, :, 3] = full_mask
        cutout = cutout[cut_top:cut_bottom, cut_left:cut_right]
        png_ok, png_encoded = cv2.imencode('.png', cutout, [cv2.IMWRITE_PNG_COMPRESSION, 6])
        if not png_ok:
            raise RuntimeError('투명한 옷장 사진을 만들지 못했어요.')
        closet_image_data_url = 'data:image/png;base64,' + base64.b64encode(png_encoded.tobytes()).decode('ascii')
    overlay = image.copy()
    tint = np.zeros_like(overlay)
    tint[:, :, 1] = 210
    selected = full_mask > 0
    overlay[selected] = cv2.addWeighted(overlay[selected], .55, tint[selected], .45, 0)
    line_x1 = int(np.flatnonzero(mask[target_y])[0] / width * output_width)
    line_x2 = int(np.flatnonzero(mask[target_y])[-1] / width * output_width)
    line_y = round(target_y / height * output_height)
    top_y = round(top / height * output_height)
    bottom_y = round(bottom / height * output_height)
    cv2.line(overlay, (line_x1, line_y), (line_x2, line_y), (255, 90, 40), 4)
    cv2.line(overlay, (output_width // 2, top_y), (output_width // 2, bottom_y), (20, 90, 255), 4)
    cv2.circle(overlay, (line_x1, line_y), 7, (255, 90, 40), -1)
    cv2.circle(overlay, (line_x2, line_y), 7, (255, 90, 40), -1)
    ok, encoded = cv2.imencode('.jpg', overlay, [cv2.IMWRITE_JPEG_QUALITY, 78])
    if not ok:
        raise RuntimeError('측정 결과 사진을 만들지 못했어요.')
    overlay_data_url = 'data:image/jpeg;base64,' + base64.b64encode(encoded.tobytes()).decode('ascii')
    confidence = min(.92, .55 + float(mask.sum()) / (height * width) * .7)
    result = {
        'ok': True,
        'measuredWidthCm': round(width_cm, 1),
        'measuredLengthCm': round(length_cm, 1),
        'widthKind': width_kind,
        'colorName': _dominant_color_name(working, mask),
        'maskMethods': {
            'width': getattr(width_mask_provider, 'mask_method', 'custom' if width_mask_provider else 'GrabCut'),
            'length': getattr(length_mask_provider, 'mask_method', 'custom' if length_mask_provider else 'GrabCut'),
        },
        'box': {'left': left, 'top': top_box, 'right': left + box_width, 'bottom': top_box + box_height},
        'widthLineY': line_y,
        'lengthLineX': output_width // 2,
        'topY': top_y,
        'bottomY': bottom_y,
        'confidence': round(confidence, 2),
        'overlayDataUrl': overlay_data_url,
        'message': f'사진을 펼친 뒤 {"허리" if width_kind == "waist" else "가슴"} 너비와 기장을 찾아봤어요. 선이 옷에 맞는지 확인해 주세요.'
    }
    if closet_image_data_url is not None:
        result['closetImageDataUrl'] = closet_image_data_url
    return result

