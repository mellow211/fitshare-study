import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

import server
from tools import garment_segmentation


class ProductPhotoMaskTests(unittest.TestCase):
    def test_short_length_pants_use_the_shorts_class_for_yolo_sam(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            garment_id = str(uuid.uuid4())
            folder = root / garment_id
            folder.mkdir()
            item = {'id': garment_id, 'garmentTypeId': 5, 'category': 'pants', 'bottomLength': '반바지',
                    'backgroundId': 'M4'}
            (folder / 'garment.json').write_text(json.dumps(item), encoding='utf-8')
            (folder / 'photo.jpg').write_bytes(b'original')
            image = np.zeros((20, 30, 3), np.uint8)
            with patch('tools.vision_pipeline.rectify_photo', return_value={'ok': True, 'image': image}), patch(
                    'tools.garment_segmentation.make_product_cutout', return_value=b'cutout') as segment:
                server.ensure_garment_cutout(root, garment_id)
            segment.assert_called_once_with(image, garment_id=5, category='shorts')

    def test_closet_cutout_uses_the_same_yolo_sam_mask_and_refreshes_legacy_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            garment_id = str(uuid.uuid4())
            folder = root / garment_id
            folder.mkdir()
            item = {'id': garment_id, 'garmentTypeId': 1, 'category': 'top', 'backgroundId': 'M4',
                    'widthCm': 42, 'lengthCm': 55,
                    'maskPipelineVersion': 'round7-848-yolo-grabcut-v1'}
            (folder / 'garment.json').write_text(json.dumps(item), encoding='utf-8')
            (folder / 'photo.jpg').write_bytes(b'original')
            (folder / 'closet.png').write_bytes(b'old GrabCut cutout')
            image = np.zeros((20, 30, 3), np.uint8)
            png = b'new YOLO+SAM cutout'
            with patch('tools.vision_pipeline.rectify_photo', return_value={'ok': True, 'image': image}), patch(
                    'tools.garment_segmentation.make_product_cutout', return_value=png) as segment:
                output = server.ensure_garment_cutout(root, garment_id)
            self.assertEqual(output, png)
            segment.assert_called_once_with(image, garment_id=1, category='top')
            self.assertEqual((folder / 'closet.png').read_bytes(), png)
            saved = json.loads((folder / 'garment.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['maskPipelineVersion'], server.GARMENT_MASK_PIPELINE_VERSION)
            self.assertEqual((saved['widthCm'], saved['lengthCm']), (42, 55))

    def test_preisolated_catalog_photo_still_gets_a_transparent_sam_mask(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            garment_id = str(uuid.uuid4())
            folder = root / garment_id
            folder.mkdir()
            item = {'id': garment_id, 'garmentTypeId': 1, 'category': 'top', 'backgroundId': 'M4',
                    'closetPhotoSource': 'provided-garment-only'}
            (folder / 'garment.json').write_text(json.dumps(item), encoding='utf-8')
            photo = np.full((30, 20, 3), 240, dtype=np.uint8)
            ok, encoded = cv2.imencode('.jpg', photo)
            self.assertTrue(ok)
            (folder / 'photo.jpg').write_bytes(encoded.tobytes())
            png = b'YOLO+SAM transparent catalog cutout'
            with patch('tools.vision_pipeline.rectify_photo', side_effect=AssertionError('no mat on catalog photos')), patch(
                    'tools.garment_segmentation.make_product_cutout', return_value=png) as segment:
                self.assertEqual(server.ensure_garment_cutout(root, garment_id), png)
            segment.assert_called_once()
            saved = json.loads((folder / 'garment.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['maskPipelineVersion'], server.GARMENT_MASK_PIPELINE_VERSION)

    def test_yolo_box_prompts_sam_and_sam_alpha_is_used(self):
        image = np.full((20, 30, 3), (20, 80, 140), dtype=np.uint8)
        yolo_mask = np.zeros((20, 30), dtype=np.uint8)
        yolo_mask[3:17, 5:25] = 1
        sam_mask = np.zeros_like(yolo_mask)
        sam_mask[4:16, 6:24] = 1
        high_score_decoy = np.zeros_like(yolo_mask)
        high_score_decoy[0:3, 0:5] = 1

        class Detector:
            def detect(self, photo, garment_id, category):
                np.testing.assert_array_equal(photo, image)
                return {'mask': yolo_mask}

        class Predictor:
            def set_image(self, photo):
                np.testing.assert_array_equal(photo, image[:, :, ::-1])

            def predict(self, *, box, multimask_output):
                np.testing.assert_array_equal(box, [5, 3, 24, 16])
                self_test.assertTrue(multimask_output)
                return np.stack([high_score_decoy, sam_mask]), np.array([0.99, 0.90]), None

        self_test = self
        result = garment_segmentation.make_product_cutout(
            image, garment_id=1, detector=Detector(), predictor=Predictor())
        decoded = cv2.imdecode(np.frombuffer(result, np.uint8), cv2.IMREAD_UNCHANGED)
        self.assertEqual(decoded.shape, (12, 18, 4))
        self.assertTrue(np.all(decoded[:, :, 3] == 255))
        np.testing.assert_array_equal(decoded[0, 0, :3], image[4, 6])

    def test_empty_sam_mask_is_an_error_not_a_grabcut_fallback(self):
        class Detector:
            def detect(self, *_):
                return {'mask': np.ones((20, 30), dtype=np.uint8)}

        class Predictor:
            def set_image(self, _):
                pass

            def predict(self, **_):
                return np.zeros((1, 20, 30)), np.array([0.1]), None

        with self.assertRaisesRegex(ValueError, 'SAM'):
            garment_segmentation.make_product_cutout(
                np.zeros((20, 30, 3), np.uint8), detector=Detector(), predictor=Predictor())

    def test_product_cutout_does_not_reuse_old_grabcut_cache_or_change_measurements(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            garment_id = str(uuid.uuid4())
            folder = root / garment_id
            folder.mkdir()
            item = {
                'garmentTypeId': 1, 'backgroundId': 'M4', 'widthCm': 40, 'lengthCm': 60,
                'maskPipelineVersion': server.GARMENT_MASK_PIPELINE_VERSION
            }
            (folder / 'garment.json').write_text(json.dumps(item), encoding='utf-8')
            (folder / 'photo.jpg').write_bytes(b'original')
            (folder / 'closet.png').write_bytes(b'old GrabCut cutout')
            image = np.zeros((20, 30, 3), np.uint8)
            png = b'\x89PNG\r\n\x1a\nSAM product cutout'
            with patch('tools.vision_pipeline.rectify_photo', return_value={'ok': True, 'image': image}), patch(
                    'tools.garment_segmentation.make_product_cutout', return_value=png) as segment:
                output = server.ensure_garment_cutout(root, garment_id, for_product_photo=True)
            self.assertEqual(output, png)
            self.assertEqual(segment.call_count, 1)
            self.assertEqual((folder / 'closet.png').read_bytes(), b'old GrabCut cutout')
            self.assertEqual((folder / 'photo.jpg').read_bytes(), b'original')
            saved = json.loads((folder / 'garment.json').read_text(encoding='utf-8'))
            self.assertEqual((saved['widthCm'], saved['lengthCm']), (40, 60))
            self.assertEqual((folder / 'product-cutout.png').read_bytes(), png)
            with patch('tools.vision_pipeline.rectify_photo', side_effect=AssertionError('cache was ignored')):
                self.assertEqual(server.ensure_garment_cutout(root, garment_id, for_product_photo=True), png)


if __name__ == '__main__':
    unittest.main()
