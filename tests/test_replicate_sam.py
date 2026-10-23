import json
import unittest
from email.message import Message

try:
    import cv2
    import numpy as np
except ImportError:  # OpenCV는 requirements-vision.txt로 설치해요.
    cv2 = None

from tools.replicate_product_photo import ProductPhotoError


class FakeResponse:
    def __init__(self, body, content_type='application/json', status=200):
        self.body = body
        self.status = status
        self.headers = Message()
        self.headers['Content-Type'] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, *_):
        return self.body

    def geturl(self):
        return None


@unittest.skipIf(cv2 is None, 'OpenCV가 설치되어 있지 않아요.')
class ReplicateSamTests(unittest.TestCase):
    def setUp(self):
        from tools.replicate_sam import SAM_MODEL_VERSION, ReplicateSamPredictor

        self.version = SAM_MODEL_VERSION
        self.Predictor = ReplicateSamPredictor
        self.image = np.zeros((40, 60, 3), dtype=np.uint8)
        mask = np.zeros((40, 60), dtype=np.uint8)
        mask[10:30, 20:50] = 255
        self.mask_png = cv2.imencode('.png', cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR))[1].tobytes()

    def run_prediction(self, statuses, cutout=False):
        requests = []
        replies = iter(statuses)

        def opener(request, timeout=90):
            requests.append(request)
            if request.full_url.endswith('/files'):
                return FakeResponse(json.dumps({'id': 'file-1', 'urls': {'get': 'https://api.replicate.com/v1/files/file-1'}}).encode())
            if request.full_url.endswith('/predictions') or request.full_url.endswith('/predictions/p1'):
                return FakeResponse(json.dumps(next(replies)).encode())
            if request.full_url == 'https://replicate.delivery/mask.png':
                return FakeResponse(self.mask_png, 'image/png')
            return FakeResponse(b'', status=204)

        predictor = self.Predictor('secret-token', opener=opener, sleep=lambda _: None)
        predictor.set_image(self.image)
        if cutout:
            from tools.garment_segmentation import make_product_cutout

            yolo_mask = np.zeros((40, 60), dtype=np.uint8)
            yolo_mask[10:30, 20:50] = 1

            class Detector:
                def detect(self, *_args):
                    return {'mask': yolo_mask, 'box': [20, 10, 49, 29]}

            return make_product_cutout(self.image, garment_id=1, detector=Detector(), predictor=predictor), requests
        return predictor.predict(box=np.array([20, 10, 49, 29], dtype=np.float32), multimask_output=False), requests

    def test_remote_single_mask_works_with_the_updated_yolo_sam_cutout(self):
        png, _ = self.run_prediction([
            {'status': 'succeeded', 'output': ['https://replicate.delivery/mask.png']},
        ], cutout=True)
        image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
        self.assertEqual(image.shape, (20, 30, 4))
        self.assertTrue(np.all(image[:, :, 3] == 255))

    def test_box_is_sent_as_x_y_width_height_and_white_area_becomes_the_mask(self):
        (masks, _, _), requests = self.run_prediction([
            {'status': 'processing', 'urls': {'get': 'https://api.replicate.com/v1/predictions/p1'}},
            {'status': 'succeeded', 'output': ['https://replicate.delivery/mask.png']},
        ])
        sent = json.loads(next(r for r in requests if r.full_url.endswith('/predictions')).data)
        self.assertEqual(sent['version'], self.version)
        self.assertEqual(json.loads(sent['input']['box']), [20, 10, 29, 19])
        self.assertTrue(sent['input']['mask_only'])
        self.assertEqual(masks.shape, (1, 40, 60))
        self.assertTrue(masks[0][15, 30])
        self.assertFalse(masks[0][2, 2])
        self.assertTrue(any(r.full_url.endswith('/files/file-1') and r.get_method() == 'DELETE' for r in requests))

    def test_failed_prediction_raises_a_clear_error_and_still_deletes_upload(self):
        with self.assertRaises(ProductPhotoError):
            self.run_prediction([{'status': 'failed', 'error': 'boom'}])

    def test_missing_token_is_rejected_before_any_request(self):
        predictor = self.Predictor('  ', opener=lambda *_args, **_kwargs: self.fail('no request expected'))
        predictor.set_image(self.image)
        with self.assertRaises(ProductPhotoError):
            predictor.predict(box=[0, 0, 10, 10])


if __name__ == '__main__':
    unittest.main()
