import base64
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from tests import test_garment_registration as fixtures
from tools.tryon_photo import make_tryon_photo, original_tryon_source


def photo(transparent=True):
    image = np.zeros((100, 80, 4), dtype=np.uint8)
    image[:, :, :3] = (20, 80, 200)
    image[10:90, 10:70, 3] = 255
    if not transparent:
        image[:, :, 3] = 255
    return cv2.imencode('.png', image)[1].tobytes()


class TryOnPhotoTests(unittest.TestCase):
    def test_transparent_image_preserves_pixels_and_crops_margin_without_detection(self):
        with patch('tools.tryon_segmentation.detect_clothing') as model:
            image = cv2.imdecode(np.frombuffer(make_tryon_photo(photo(), 'top'), np.uint8), -1)
        self.assertEqual(image.shape, (80, 60, 4))
        self.assertEqual(image[0, 0].tolist(), [20, 80, 200, 255])
        model.assert_not_called()

    def test_opaque_photo_uses_display_mask_and_preserves_both_pants_legs(self):
        mask = np.zeros((100, 80), np.uint8)
        mask[5:30, 10:70] = 1
        mask[25:95, 10:30] = mask[25:95, 50:70] = 1
        with patch('tools.tryon_segmentation.detect_clothing', return_value=mask) as model, patch('tools.tryon_segmentation.refine_clothing', return_value=(mask, {})):
            image = cv2.imdecode(np.frombuffer(make_tryon_photo(photo(False), 'pants', '긴바지'), np.uint8), -1)
        self.assertEqual(model.call_args.args[1:], ('pants', '긴바지'))
        self.assertEqual(image.shape, (90, 60, 4))
        self.assertTrue(image[70, 5, 3] and image[70, 55, 3])
        self.assertEqual(image[70, 30, 3], 0)

    def test_shorts_do_not_force_trousers_class(self):
        mask = np.zeros((100, 80), np.uint8); mask[10:70, 10:70] = 1
        with patch('tools.tryon_segmentation.detect_clothing', return_value=mask) as model, patch('tools.tryon_segmentation.refine_clothing', return_value=(mask, {})):
            make_tryon_photo(photo(False), 'pants', '반바지')
        self.assertEqual(model.call_args.args[1:], ('pants', '반바지'))

    def test_bad_images_and_missing_mask_do_not_return_fake_cutout(self):
        for source, category in [(b'bad', 'top'), (b'', 'top'), (photo(), 'dress')]:
            with self.assertRaises(ValueError):make_tryon_photo(source, category)
        with patch('tools.tryon_segmentation.detect_clothing', return_value=np.ones((100,80), np.uint8)), patch('tools.tryon_segmentation.refine_clothing', return_value=(np.ones((100,80), np.uint8), {})):
            with self.assertRaises(ValueError):make_tryon_photo(photo(False), 'top')


class TryOnApiTests(unittest.TestCase):
    setUp = fixtures.GarmentRegistrationTests.setUp
    tearDown = fixtures.GarmentRegistrationTests.tearDown
    request_json = fixtures.GarmentRegistrationTests.request_json

    def test_endpoint_returns_png_and_does_not_change_registration(self):
        data='data:image/jpeg;base64,'+base64.b64encode(b'\xff\xd8\xffsample').decode()
        _,saved=self.request_json('/api/garments','POST',dict(name='Test shirt',category='top',garmentTypeId=1,gender='unisex',lengthCm=50,widthCm=40,colorName='파랑',photoDataUrl=data))
        folder=Path(self.temp_dir.name)/'garments'/saved['id']
        before=(folder/'garment.json').read_bytes()
        with patch('tools.tryon_photo.original_tryon_source', return_value=photo()):
            with self.opener.open(self.base_url+'/api/garments/'+saved['id']+'/tryon-photo') as result:
                self.assertEqual(result.status,200)
                self.assertEqual(result.headers['Content-Type'],'image/png')
                self.assertTrue(result.read().startswith(bytes([137])+b'PNG'))
        self.assertEqual((folder/'garment.json').read_bytes(),before)
        with self.assertRaises(Exception) as error:
            self.opener.open(self.base_url+'/api/garments/missing/tryon-photo')
        self.assertEqual(error.exception.code,404)

    def test_endpoint_uses_yolo_sam_cutout_and_falls_back_when_it_is_missing(self):
        data='data:image/jpeg;base64,'+base64.b64encode(bytes([255,216,255])+b'sample').decode()
        _,saved=self.request_json('/api/garments','POST',dict(name='Test shirt',category='top',garmentTypeId=1,gender='unisex',lengthCm=50,widthCm=40,colorName='파랑',photoDataUrl=data))
        url=self.base_url+'/api/garments/'+saved['id']+'/tryon-photo'
        with patch('server.ensure_garment_cutout', return_value=photo()) as cutout,              patch('tools.tryon_photo.original_tryon_source', side_effect=AssertionError('fallback must not run')):
            with self.opener.open(url) as result:
                self.assertTrue(result.read().startswith(bytes([137])+b'PNG'))
            self.assertEqual(cutout.call_count, 1)
        with patch('server.ensure_garment_cutout', side_effect=RuntimeError('SAM model missing')),              patch('tools.tryon_photo.original_tryon_source', return_value=photo()) as fallback:
            with self.opener.open(url) as result:
                self.assertTrue(result.read().startswith(bytes([137])+b'PNG'))
            self.assertEqual(fallback.call_count, 1)
        with patch('server.ensure_garment_cutout', side_effect=RuntimeError('SAM model missing')),              patch('tools.tryon_photo.original_tryon_source', side_effect=ValueError('옷 사진을 읽지 못했어요.')):
            with self.assertRaises(Exception) as error:
                self.opener.open(url)
            self.assertEqual(error.exception.code,422)

    def test_original_photo_recovers_sleeves_missing_from_old_closet_png(self):
        from tests.test_tryon_segmentation import garment_case
        image, truth, seed = garment_case(foreground=(30,70,160))
        folder = Path(self.temp_dir.name)/'original-case';folder.mkdir()
        original = cv2.imencode('.jpg', image)[1].tobytes()
        (folder/'photo.jpg').write_bytes(original)
        clipped = cv2.cvtColor(image,cv2.COLOR_BGR2BGRA);clipped[:,:,3]=seed.astype(np.uint8)*255
        old_png=cv2.imencode('.png',clipped)[1].tobytes();(folder/'closet.png').write_bytes(old_png)
        source=original_tryon_source(folder,{'closetPhotoSource':'provided-garment-only'})
        self.assertEqual(source,original)
        with patch('tools.tryon_segmentation.detect_clothing',return_value=seed):
            result=cv2.imdecode(np.frombuffer(make_tryon_photo(source,'top'),np.uint8),-1)
        self.assertGreater(result.shape[1],180)
        self.assertEqual((folder/'closet.png').read_bytes(),old_png)

    def test_mat_rectification_error_falls_back_to_original_without_disconnect(self):
        folder=Path(self.temp_dir.name)/'mat-error';folder.mkdir()
        source=cv2.imencode('.jpg',np.full((100,100,3),120,np.uint8))[1].tobytes()
        (folder/'photo.jpg').write_bytes(source)
        with patch('tools.vision_pipeline.rectify_photo',side_effect=cv2.error('bad mat')):
            self.assertEqual(original_tryon_source(folder,{'backgroundId':'M4'}),source)
