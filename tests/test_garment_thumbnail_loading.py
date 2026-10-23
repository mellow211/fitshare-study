import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

import server
from tools.garment_thumbnails import prepare_garment_thumbnails


class GarmentThumbnailLoadingTests(unittest.TestCase):
    def test_list_preview_uses_saved_images_without_refreshing_segmentation(self):
        cases = [
            ('legacy-thumbnail', 'thumbnail', (20, 210, 30)),
            ('legacy-cutout', 'cutout', (20, 210, 30)),
            ('missing-preview', None, (220, 30, 20)),
            ('broken-preview', 'broken', (220, 30, 20)),
            ('empty-preview', 'empty', (220, 30, 20)),
            ('refreshed-cutout', 'refreshed', (30, 80, 100)),
        ]
        for name, saved, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                garment_id = str(uuid.uuid4())
                folder = root / 'garments' / garment_id
                folder.mkdir(parents=True)
                original = cv2.imencode('.jpg', np.full((600, 400, 3), (220, 30, 20), np.uint8))[1].tobytes()
                (folder / 'photo.jpg').write_bytes(original)
                metadata = json.dumps({'id': garment_id, 'maskPipelineVersion': 'legacy',
                                       'thumbnailVersion': 0 if saved == 'refreshed' else 2})
                (folder / 'garment.json').write_text(metadata, encoding='utf-8')
                if saved:
                    filename = 'closet.png' if saved == 'cutout' else 'closet-thumbnail.png'
                    png = cv2.imencode('.png', np.full((300, 240, 4), (20, 210, 30, 255), np.uint8))[1].tobytes()
                    (folder / filename).write_bytes(b'broken' if saved == 'broken' else b'' if saved == 'empty' else png)
                    if saved == 'refreshed':
                        updated = np.full((300, 240, 4), (30, 80, 100, 255), np.uint8)
                        (folder / 'closet.png').write_bytes(cv2.imencode('.png', updated)[1].tobytes())
                httpd = server.create_server('127.0.0.1', 0, root)
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                try:
                    with patch('server.ensure_garment_cutout', side_effect=RuntimeError('AI must not run')) as ai:
                        url = f'http://127.0.0.1:{httpd.server_port}/api/garments/{garment_id}/thumbnail'
                        with urllib.request.urlopen(url, timeout=3) as response:
                            image = cv2.imdecode(np.frombuffer(response.read(), np.uint8), cv2.IMREAD_UNCHANGED)
                        ai.assert_not_called()
                    self.assertLessEqual(max(image.shape[:2]), 200)
                    np.testing.assert_allclose(image[50, 50, :3], expected, atol=3)
                    self.assertEqual((folder / 'photo.jpg').read_bytes(), original)
                    self.assertEqual((folder / 'garment.json').read_text(encoding='utf-8'), metadata)
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                    thread.join(timeout=2)

    def test_product_thumbnail_cache_refreshes_when_photo_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            garment_id = str(uuid.uuid4())
            folder = root / garment_id
            folder.mkdir()
            source = folder / 'product-photo.png'
            (folder / 'garment.json').write_text(json.dumps({'productPhotoFile': 'product-photo.png'}), encoding='utf-8')
            _, first = cv2.imencode('.png', np.full((400, 300, 3), (10, 20, 230), dtype=np.uint8))
            source.write_bytes(first.tobytes())
            original_thumbnail = server.get_product_thumbnail_bytes(root, garment_id)
            with patch('server.make_source_photo_thumbnail', side_effect=AssertionError('unnecessary resize')):
                self.assertEqual(server.get_product_thumbnail_bytes(root, garment_id), original_thumbnail)
            _, second = cv2.imencode('.png', np.full((400, 300, 3), (210, 30, 40), dtype=np.uint8))
            source.write_bytes(second.tobytes())
            newer = (folder / 'product-thumbnail.jpg').stat().st_mtime_ns + 1_000_000_000
            os.utime(source, ns=(newer, newer))
            updated = server.get_product_thumbnail_bytes(root, garment_id)
            image = cv2.imdecode(np.frombuffer(updated, dtype=np.uint8), cv2.IMREAD_COLOR)
            np.testing.assert_allclose(image[50, 50], (210, 30, 40), atol=3)
            self.assertLessEqual(max(image.shape[:2]), 200)

    def test_thumbnail_prefers_product_then_closet_then_original(self):
        cases = [
            ('product', True, True, (10, 20, 230)),
            ('missing-product', False, True, (20, 210, 30)),
            ('corrupt-product', b'broken', True, (20, 210, 30)),
            ('missing-closet', False, False, (220, 30, 20)),
            ('corrupt-closet', False, b'broken', (220, 30, 20)),
            ('unsafe-product-path', '../outside.jpg', True, (20, 210, 30)),
        ]
        for name, product, closet, expected_color in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                garment_id = str(uuid.uuid4())
                folder = root / 'garments' / garment_id
                folder.mkdir(parents=True)
                _, original = cv2.imencode('.jpg', np.full((600, 400, 3), (220, 30, 20), dtype=np.uint8))
                (folder / 'photo.jpg').write_bytes(original.tobytes())
                item = {'id': garment_id, 'maskPipelineVersion': server.GARMENT_MASK_PIPELINE_VERSION,
                        'productPhotoFile': 'product-photo.jpg'}
                if product:
                    _, generated = cv2.imencode('.jpg', np.full((500, 400, 3), (10, 20, 230), dtype=np.uint8))
                    (folder / 'product-photo.jpg').write_bytes(product if isinstance(product, bytes) else generated.tobytes())
                if isinstance(product, str):
                    item['productPhotoFile'] = product
                    (folder.parent / 'outside.jpg').write_bytes(generated.tobytes())
                if closet:
                    image = np.full((480, 320, 4), (20, 210, 30, 255), dtype=np.uint8)
                    _, cutout = cv2.imencode('.png', image)
                    (folder / 'closet.png').write_bytes(closet if isinstance(closet, bytes) else cutout.tobytes())
                (folder / 'garment.json').write_text(json.dumps(item), encoding='utf-8')
                httpd = server.create_server('127.0.0.1', 0, root)
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                try:
                    url = f'http://127.0.0.1:{httpd.server_address[1]}/api/garments/{garment_id}/thumbnail'
                    with urllib.request.urlopen(url, timeout=5) as response:
                        image = cv2.imdecode(np.frombuffer(response.read(), dtype=np.uint8), cv2.IMREAD_COLOR)
                        self.assertLessEqual(max(image.shape[:2]), 200)
                        np.testing.assert_allclose(image[50, 50], expected_color, atol=3)
                        self.assertIn(response.headers['Cache-Control'], ('no-cache', 'no-store'))
                    self.assertEqual((folder / 'photo.jpg').read_bytes(), original.tobytes())
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                    thread.join(timeout=2)

    def test_closet_thumbnail_endpoint_uses_yolo_sam_cutout_before_product_photo(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            garment_id = str(uuid.uuid4())
            folder = root / 'garments' / garment_id
            folder.mkdir(parents=True)
            _, original = cv2.imencode('.jpg', np.full((240, 160, 3), (220, 30, 20), dtype=np.uint8))
            _, product = cv2.imencode('.jpg', np.full((240, 160, 3), (10, 20, 230), dtype=np.uint8))
            cutout = np.zeros((240, 160, 4), dtype=np.uint8)
            cutout[20:220, 30:130] = (20, 210, 30, 255)
            _, closet = cv2.imencode('.png', cutout)
            (folder / 'photo.jpg').write_bytes(original.tobytes())
            (folder / 'product-photo.jpg').write_bytes(product.tobytes())
            (folder / 'closet.png').write_bytes(closet.tobytes())
            (folder / 'garment.json').write_text(json.dumps({
                'id': garment_id,
                'maskPipelineVersion': server.GARMENT_MASK_PIPELINE_VERSION,
                'productPhotoFile': 'product-photo.jpg',
            }), encoding='utf-8')
            httpd = server.create_server('127.0.0.1', 0, root)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                url = f'http://127.0.0.1:{httpd.server_address[1]}/api/garments/{garment_id}/closet-thumbnail'
                with urllib.request.urlopen(url, timeout=5) as response:
                    self.assertEqual(response.headers.get_content_type(), 'image/png')
                    image = cv2.imdecode(np.frombuffer(response.read(), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
                    self.assertEqual(image.shape[2], 4)
                    self.assertEqual(int(image[:, :, 3].min()), 0)
                    np.testing.assert_allclose(image[50, 50, :3], (20, 210, 30), atol=3)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=2)

    def test_thumbnail_reports_invalid_original_when_no_usable_image_exists(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            garment_id = str(uuid.uuid4())
            folder = root / 'garments' / garment_id
            folder.mkdir(parents=True)
            (folder / 'photo.jpg').write_bytes(b'broken')
            (folder / 'garment.json').write_text(json.dumps({'id': garment_id}), encoding='utf-8')
            httpd = server.create_server('127.0.0.1', 0, root)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(f'http://127.0.0.1:{httpd.server_address[1]}/api/garments/{garment_id}/thumbnail')
                self.assertEqual(error.exception.code, 422)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=2)

    def make_cutout(self, size=(480, 320)):
        image = np.zeros((size[0], size[1], 4), dtype=np.uint8)
        image[20:-20, 30:-30] = (20, 120, 220, 255)
        ok, encoded = cv2.imencode('.png', image)
        self.assertTrue(ok)
        return encoded.tobytes()

    def test_thumbnail_is_at_most_200px(self):
        output = server.make_garment_thumbnail(self.make_cutout())
        image = cv2.imdecode(np.frombuffer(output, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        self.assertLessEqual(max(image.shape[:2]), 200)
        self.assertEqual(image.shape[2], 4)

    def test_corrupt_source_photo_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '원본 사진'):
            server.make_source_photo_thumbnail(b'not a jpeg')

    def test_legacy_thumbnail_resizes_from_saved_cutout_without_segmentation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            garment_id = str(uuid.uuid4())
            garment_dir = root / garment_id
            garment_dir.mkdir()
            (garment_dir / 'closet.png').write_bytes(self.make_cutout())
            item = {'id': garment_id, 'maskPipelineVersion': server.GARMENT_MASK_PIPELINE_VERSION}
            (garment_dir / 'garment.json').write_text(json.dumps(item), encoding='utf-8')
            with patch('tools.garment_segmentation.get_yolo_grabcut_provider', side_effect=AssertionError('segmentation called')):
                output = server.get_garment_thumbnail_bytes(root, garment_id)
            image = cv2.imdecode(np.frombuffer(output, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
            self.assertLessEqual(max(image.shape[:2]), 200)
            updated = json.loads((garment_dir / 'garment.json').read_text(encoding='utf-8'))
            self.assertEqual(updated['thumbnailVersion'], server.THUMBNAIL_VERSION)

    def test_prepare_creates_missing_thumbnail_from_current_cutout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            garment_id = str(uuid.uuid4())
            garment_dir = root / garment_id
            garment_dir.mkdir()
            (garment_dir / 'closet.png').write_bytes(self.make_cutout())
            (garment_dir / 'garment.json').write_text(json.dumps({'id': garment_id,
                                                                  'maskPipelineVersion': server.GARMENT_MASK_PIPELINE_VERSION}), encoding='utf-8')
            (root / 'index.json').write_text(json.dumps([{'id': garment_id,
                                                          'maskPipelineVersion': server.GARMENT_MASK_PIPELINE_VERSION}]), encoding='utf-8')
            result = prepare_garment_thumbnails(root, lambda *_: self.fail('cutout already exists'))
            self.assertEqual(result, {'prepared': 1, 'failed': []})
            self.assertTrue((garment_dir / 'closet-thumbnail.png').exists())

    def test_prepare_continues_after_one_garment_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bad_id, good_id = str(uuid.uuid4()), str(uuid.uuid4())
            for garment_id in (bad_id, good_id):
                (root / garment_id).mkdir()
                version = server.GARMENT_MASK_PIPELINE_VERSION if garment_id == good_id else 'stale-mask'
                (root / garment_id / 'garment.json').write_text(json.dumps({'id': garment_id, 'maskPipelineVersion': version}), encoding='utf-8')
            (root / good_id / 'closet.png').write_bytes(self.make_cutout())
            (root / 'index.json').write_text(json.dumps([{'id': bad_id}, {'id': good_id,
                                                                          'maskPipelineVersion': server.GARMENT_MASK_PIPELINE_VERSION}]), encoding='utf-8')
            result = prepare_garment_thumbnails(root, lambda *_: (_ for _ in ()).throw(ValueError('bad cutout')))
            self.assertEqual(result['prepared'], 1)
            self.assertEqual(result['failed'], [bad_id])

    def test_prepare_preserves_measurements_updated_while_cache_is_built(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            garment_id = str(uuid.uuid4())
            garment_dir = root / garment_id
            garment_dir.mkdir()
            item_path = garment_dir / 'garment.json'
            item_path.write_text(json.dumps({'id': garment_id, 'lengthCm': 40,
                                             'maskPipelineVersion': 'stale'}), encoding='utf-8')
            (root / 'index.json').write_text(json.dumps([{'id': garment_id}]), encoding='utf-8')

            def ensure_cutout(*_):
                latest = json.loads(item_path.read_text(encoding='utf-8'))
                latest['lengthCm'] = 52
                item_path.write_text(json.dumps(latest), encoding='utf-8')
                return self.make_cutout()

            result = prepare_garment_thumbnails(root, ensure_cutout)
            self.assertEqual(result, {'prepared': 1, 'failed': []})
            self.assertEqual(json.loads(item_path.read_text(encoding='utf-8'))['lengthCm'], 52)

    def test_source_preview_for_missing_cutout_does_not_run_segmentation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            garments = root / 'garments'
            garment_id = str(uuid.uuid4())
            garment_dir = garments / garment_id
            garment_dir.mkdir(parents=True)
            original = np.full((800, 600, 3), (40, 130, 210), dtype=np.uint8)
            ok, encoded = cv2.imencode('.jpg', original)
            self.assertTrue(ok)
            (garment_dir / 'photo.jpg').write_bytes(encoded.tobytes())
            (garment_dir / 'garment.json').write_text(json.dumps({'id': garment_id, 'name': 'Missing cutout',
                                                                  'maskPipelineVersion': 'old'}), encoding='utf-8')
            httpd = server.create_server('127.0.0.1', 0, root)
            import threading
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                url = f'http://127.0.0.1:{httpd.server_address[1]}/api/garments/{garment_id}/thumbnail'
                with patch('tools.garment_segmentation.get_yolo_grabcut_provider', side_effect=AssertionError('segmentation called')):
                    with urllib.request.urlopen(url, timeout=5) as response:
                        body = response.read()
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.headers.get_content_type(), 'image/jpeg')
                        self.assertEqual(response.headers['Cache-Control'], 'no-store')
                image = cv2.imdecode(np.frombuffer(body, dtype=np.uint8), cv2.IMREAD_COLOR)
                self.assertLessEqual(max(image.shape[:2]), 200)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
