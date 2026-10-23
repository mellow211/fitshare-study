import base64
import json
import http.cookiejar
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import server


class GarmentRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.httpd = server.create_server('127.0.0.1', 0, Path(self.temp_dir.name))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f'http://127.0.0.1:{self.httpd.server_address[1]}'
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def request_json(self, path, method='GET', payload=None):
        body = None if payload is None else json.dumps(payload).encode('utf-8')
        request = urllib.request.Request(self.base_url + path, data=body, method=method)
        if body is not None:
            request.add_header('Content-Type', 'application/json')
        with self.opener.open(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode('utf-8'))

    def post_error(self, path, payload, expected_status):
        body = json.dumps(payload).encode('utf-8')
        request = urllib.request.Request(self.base_url + path, data=body, method='POST')
        request.add_header('Content-Type', 'application/json')
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.opener.open(request, timeout=15)
        self.assertEqual(error.exception.code, expected_status)

    def request_error(self, path, method, expected_status):
        request = urllib.request.Request(self.base_url + path, method=method)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.opener.open(request, timeout=15)
        self.assertEqual(error.exception.code, expected_status)

    def test_m4_sample_photo_returns_measurements_color_and_review_fields(self):
        sample = Path(server.PROJECT_ROOT / 'measure_dataset' / 'ChaArUco_Cross' / 'chc_1_w55_h65.jpg')
        photo_data = 'data:image/jpeg;base64,' + base64.b64encode(sample.read_bytes()).decode('ascii')

        status, result = self.request_json('/api/garments/analyze', 'POST', {
            'backgroundId': 'M4', 'garmentId': 1, 'photoDataUrl': photo_data
        })

        self.assertEqual(status, 200)
        self.assertTrue(result['ok'])
        self.assertEqual(result['garmentId'], 1)
        self.assertEqual(result['category'], 'top')
        self.assertEqual(result['garmentName'], '상의 외투')
        self.assertGreater(result['lengthCm'], 0)
        self.assertGreater(result['widthCm'], 0)
        self.assertTrue(result['colorName'])
        self.assertTrue(result['analysisPhotoDataUrl'].startswith('data:image/jpeg;base64,'))
        self.assertTrue(result['closetImageDataUrl'].startswith('data:image/png;base64,'))
        cutout = base64.b64decode(result['closetImageDataUrl'].split(',', 1)[1])
        self.assertTrue(cutout.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(cutout[25], 6)  # PNG color type 6 includes an alpha channel.
        import cv2
        import numpy as np
        decoded_cutout = cv2.imdecode(np.frombuffer(cutout, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        self.assertEqual(decoded_cutout.shape[2], 4)
        self.assertEqual(int(decoded_cutout[:, :, 3].min()), 0)
        self.assertEqual(int(decoded_cutout[:, :, 3].max()), 255)
        self.assertNotIn('gender', result)

    def test_registration_measurement_matches_experiment_pipeline_for_same_m4_photo(self):
        from tools.garment_segmentation import get_yolo_grabcut_provider
        from tools.vision_pipeline import measure_garment, rectify_photo

        photo = Path(server.PROJECT_ROOT / 'measure_dataset' / 'ChaArUco_Cross' / 'chc_2_w54_h50.jpg').read_bytes()
        flattened = rectify_photo(photo, 'M4', preserve_resolution=True, max_output_long_side=848)
        experiment_result = measure_garment(flattened['image'], garment_id=2,
                                             scale_cm_per_pixel=flattened['scaleCmPerPixel'], max_working_long_side=848,
                                             length_mask_provider=get_yolo_grabcut_provider(garment_id=2))
        photo_data = 'data:image/jpeg;base64,' + base64.b64encode(photo).decode('ascii')
        _, registration_result = self.request_json('/api/garments/analyze', 'POST', {
            'backgroundId': 'M4', 'garmentId': 2, 'photoDataUrl': photo_data
        })

        self.assertEqual(registration_result['lengthCm'], experiment_result['measuredLengthCm'])
        self.assertEqual(registration_result['widthCm'], experiment_result['measuredWidthCm'])
        self.assertEqual(registration_result['maskMethods'], {'width': 'GrabCut', 'length': 'YOLO→GrabCut'})
        self.assertEqual(registration_result['analysisPhotoDataUrl'], experiment_result['overlayDataUrl'])

    def test_pants_registration_uses_the_selected_hybrid_pipeline(self):
        from tools.garment_segmentation import get_yolo_grabcut_provider
        from tools.vision_pipeline import measure_garment, rectify_photo

        path = Path(server.PROJECT_ROOT / 'measure_dataset' / 'ChaArUco_Cross' / 'chc_5_w42_h90.jpg')
        flattened = rectify_photo(path.read_bytes(), 'M4', preserve_resolution=True, max_output_long_side=848)
        expected = measure_garment(flattened['image'], garment_id=5,
                                   scale_cm_per_pixel=flattened['scaleCmPerPixel'], max_working_long_side=848,
                                   length_mask_provider=get_yolo_grabcut_provider(garment_id=5))
        photo_data = 'data:image/jpeg;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')
        _, result = self.request_json('/api/garments/analyze', 'POST', {
            'backgroundId': 'M4', 'garmentId': 5, 'photoDataUrl': photo_data
        })
        self.assertEqual(result['widthCm'], expected['measuredWidthCm'])
        self.assertEqual(result['lengthCm'], expected['measuredLengthCm'])

    def test_analysis_accepts_each_supported_mat_using_its_matching_sample(self):
        samples = {'M1': ('point', 'p_1_w55_h65.jpg'), 'M2': ('cross', 'c_1_w55_h65.jpg'),
                   'M3': ('ChArUco', 'ch_1_w55_h65.jpg'),
                   'M4': ('ChaArUco_Cross', 'chc_1_w55_h65.jpg')}
        for mat_id, (folder, filename) in samples.items():
            with self.subTest(background=mat_id):
                path = Path(server.PROJECT_ROOT / 'measure_dataset' / folder / filename)
                photo_data = 'data:image/jpeg;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')
                status, result = self.request_json('/api/garments/analyze', 'POST', {
                    'backgroundId': mat_id, 'garmentId': 1, 'photoDataUrl': photo_data
                })
                self.assertEqual(status, 200)
                self.assertTrue(result['ok'])

    def test_five_m4_training_photos_produce_reviewable_size_and_kind_candidates(self):
        sample_dir = server.PROJECT_ROOT / 'measure_dataset' / 'ChaArUco_Cross'
        results = []
        for path in sorted(sample_dir.glob('*.jpg')):
            photo_data = 'data:image/jpeg;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')
            _, result = self.request_json('/api/garments/analyze', 'POST', {
                'backgroundId': 'M4', 'garmentId': int(path.name.split('_')[1]), 'photoDataUrl': photo_data
            })
            results.append(result)

        self.assertEqual(len(results), 5)
        self.assertEqual([result['garmentId'] for result in results], [1, 2, 3, 4, 5])
        self.assertTrue(all(20 <= result['lengthCm'] <= 130 and 20 <= result['widthCm'] <= 100 for result in results))
        self.assertTrue(all(result['colorName'] for result in results))

    def test_analysis_rejects_unknown_background_and_unreadable_photo(self):
        photo = 'data:image/jpeg;base64,' + base64.b64encode(b'not an image').decode('ascii')
        self.post_error('/api/garments/analyze', {'backgroundId': 'M9', 'garmentId': 1, 'photoDataUrl': photo}, 400)
        self.post_error('/api/garments/analyze', {'backgroundId': 'M4', 'garmentId': 1, 'photoDataUrl': photo}, 422)
        self.post_error('/api/garments/analyze', {'backgroundId': 'M4', 'garmentId': 7, 'photoDataUrl': photo}, 400)

    def test_saved_garment_requires_gender_choice_and_keeps_photo(self):
        jpeg_bytes = b'\xff\xd8\xfftest jpeg bytes'
        photo = 'data:image/jpeg;base64,' + base64.b64encode(jpeg_bytes).decode('ascii')
        payload = {
            'name': '파란 점퍼', 'category': 'top', 'garmentTypeId': 4, 'gender': 'unisex', 'lengthCm': 53,
            'widthCm': 49, 'colorName': '파랑', 'photoDataUrl': photo,
        }

        status, saved = self.request_json('/api/garments', 'POST', payload)

        self.assertEqual(status, 201)
        self.assertEqual(saved['gender'], 'unisex')
        self.assertEqual(saved['garmentTypeName'], '점퍼')
        _, listing = self.request_json('/api/garments')
        self.assertEqual(listing[0]['id'], saved['id'])
        _, detail = self.request_json(f"/api/garments/{saved['id']}")
        self.assertEqual(detail['name'], '파란 점퍼')
        with urllib.request.urlopen(self.base_url + detail['photoUrl'], timeout=3) as response:
            self.assertEqual(response.read(), jpeg_bytes)

    def test_registration_estimates_size_from_measurements_and_keeps_manual_size(self):
        photo = 'data:image/jpeg;base64,' + base64.b64encode(b'\xff\xd8\xffsample').decode('ascii')
        cases = (
            ('top', 1, 53.8, 46.8, '110', {}),
            ('skirt', 6, 46.4, 48.1, '160', {}),
            ('pants', 5, 45.5, 47.3, '160', {'bottomLength': '\ubc18\ubc14\uc9c0'}),
        )
        for index, (category, type_id, width, length, expected_size, details) in enumerate(cases):
            with self.subTest(category=category):
                _, saved = self.request_json('/api/garments', 'POST', {
                    'name': f'estimated-{index}', 'category': category, 'garmentTypeId': type_id,
                    'gender': 'unknown', 'lengthCm': length, 'widthCm': width,
                    'colorName': 'blue', 'photoDataUrl': photo, **details,
                })
                self.assertEqual(saved['size'], expected_size)
                self.assertEqual(saved['metadataSources']['size'], 'measurement-rule')

        _, manual = self.request_json('/api/garments', 'POST', {
            'name': 'manual-size', 'category': 'top', 'garmentTypeId': 1,
            'gender': 'unknown', 'lengthCm': 46.8, 'widthCm': 53.8,
            'colorName': 'blue', 'photoDataUrl': photo,
            'size': '140', 'metadataSources': {'size': 'manual'},
        })
        self.assertEqual(manual['size'], '140')
        self.assertEqual(manual['metadataSources']['size'], 'manual')

    def test_saved_garment_keeps_original_and_serves_transparent_closet_image(self):
        photo_bytes = Path(server.PROJECT_ROOT / 'measure_dataset' / 'ChaArUco_Cross' / 'chc_1_w55_h65.jpg').read_bytes()
        photo = 'data:image/jpeg;base64,' + base64.b64encode(photo_bytes).decode('ascii')
        _, analysis = self.request_json('/api/garments/analyze', 'POST', {
            'backgroundId': 'M4', 'garmentId': 1, 'photoDataUrl': photo
        })
        _, saved = self.request_json('/api/garments', 'POST', {
            'name': '테스트 상의', 'category': 'top', 'garmentTypeId': 1, 'gender': 'unisex',
            'lengthCm': analysis['lengthCm'], 'widthCm': analysis['widthCm'], 'colorName': analysis['colorName'],
            'photoDataUrl': photo, 'closetImageDataUrl': analysis['closetImageDataUrl'], 'backgroundId': 'M4'
        })
        self.assertEqual(saved['maskPipelineVersion'], server.LEGACY_GARMENT_MASK_PIPELINE_VERSION)
        with urllib.request.urlopen(self.base_url + saved['photoUrl'], timeout=3) as response:
            self.assertEqual(response.read(), photo_bytes)
        with urllib.request.urlopen(self.base_url + saved['closetPhotoUrl'], timeout=30) as response:
            cutout = response.read()
            self.assertEqual(response.headers.get_content_type(), 'image/png')
            self.assertEqual(response.headers.get('Cache-Control'), 'no-store')
        self.assertTrue(cutout.startswith(b'\x89PNG\r\n\x1a\n'))
        stored = json.loads((Path(self.temp_dir.name) / 'garments' / saved['id'] / 'garment.json').read_text(encoding='utf-8'))
        self.assertEqual(stored['maskPipelineVersion'], server.GARMENT_MASK_PIPELINE_VERSION)
        self.assertEqual(stored['thumbnailVersion'], 0)

    def test_legacy_garment_gets_a_generated_closet_cutout(self):
        source = Path(server.PROJECT_ROOT / 'measure_dataset' / 'ChaArUco_Cross' / 'chc_1_w55_h65.jpg').read_bytes()
        record_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
        garment_dir = Path(self.temp_dir.name) / 'garments' / record_id
        garment_dir.mkdir(parents=True)
        (garment_dir / 'photo.jpg').write_bytes(source)
        (garment_dir / 'garment.json').write_text(json.dumps({'garmentTypeId': 1, 'backgroundId': 'M4'}), encoding='utf-8')
        with urllib.request.urlopen(self.base_url + f'/api/garments/{record_id}/closet-photo', timeout=15) as response:
            cutout = response.read()
            self.assertEqual(response.headers.get_content_type(), 'image/png')
        self.assertTrue(cutout.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertTrue((garment_dir / 'closet.png').exists())

    def test_closet_thumbnail_is_small_cached_transparent_png(self):
        source = Path(server.PROJECT_ROOT / 'measure_dataset' / 'ChaArUco_Cross' / 'chc_1_w55_h65.jpg').read_bytes()
        record_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
        garment_dir = Path(self.temp_dir.name) / 'garments' / record_id
        garment_dir.mkdir(parents=True)
        (garment_dir / 'photo.jpg').write_bytes(source)
        (garment_dir / 'garment.json').write_text(json.dumps({'garmentTypeId': 1, 'backgroundId': 'M4'}), encoding='utf-8')
        with urllib.request.urlopen(self.base_url + f'/api/garments/{record_id}/thumbnail', timeout=30) as response:
            thumbnail = response.read()
            self.assertEqual(response.headers.get_content_type(), 'image/png')
            self.assertEqual(response.headers.get('Cache-Control'), 'no-cache')
        self.assertTrue((garment_dir / 'closet-thumbnail.png').exists())
        import cv2
        import numpy as np
        decoded = cv2.imdecode(np.frombuffer(thumbnail, dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertLessEqual(max(decoded.shape[:2]), 200)

    def test_garment_information_can_be_updated_without_changing_the_photo(self):
        self.request_json('/api/auth/signup', 'POST', {
            'username': 'teacher', 'password': 'pw', 'name': 'Teacher', 'teacherCode': 'fitshare'})
        original = b'\xff\xd8\xffsample original'
        photo = 'data:image/jpeg;base64,' + base64.b64encode(original).decode('ascii')
        _, saved = self.request_json('/api/garments', 'POST', {
            'name': '확인할 점퍼', 'category': 'top', 'garmentTypeId': 4, 'gender': 'unknown',
            'lengthCm': 50, 'widthCm': 45, 'colorName': '파랑', 'photoDataUrl': photo,
        })
        updated = {
            'name': '고친 원피스', 'category': 'dress', 'garmentTypeId': 3, 'gender': 'female',
            'lengthCm': 82.5, 'widthCm': 41.2, 'colorName': '보라'
        }
        status, result = self.request_json(f"/api/garments/{saved['id']}/update", 'POST', updated)
        self.assertEqual(status, 200)
        self.assertEqual({key: result[key] for key in updated}, updated)
        _, listing = self.request_json('/api/garments')
        self.assertEqual(listing[0]['name'], '고친 원피스')
        with urllib.request.urlopen(self.base_url + result['photoUrl'], timeout=3) as response:
            self.assertEqual(response.read(), original)

    def test_skirt_can_be_analyzed_saved_and_updated_as_a_skirt(self):
        from unittest.mock import patch

        measurement = {
            'measuredLengthCm': 52.0, 'measuredWidthCm': 31.0, 'widthKind': 'waist',
            'maskMethods': {'width': 'GrabCut', 'length': 'YOLO+GrabCut'}, 'colorName': 'blue',
            'box': [10, 10, 80, 90], 'confidence': 1.0,
            'overlayDataUrl': 'data:image/jpeg;base64,AA==',
            'closetImageDataUrl': 'data:image/png;base64,AA==', 'message': 'ok'
        }
        analysis = {'rectified': {'image': object(), 'method': 'test'}, 'measurement': measurement,
                    'fallbackUsed': False, 'fallbackReason': '', 'pipelineMode': 'hybrid'}
        photo = 'data:image/jpeg;base64,' + base64.b64encode(b'\xff\xd8\xff').decode('ascii')
        with patch('tools.vision_pipeline.measure_photo_with_fallback', return_value=analysis) as process_photo, \
                patch('tools.vision_pipeline.image_to_data_url', return_value='data:image/jpeg;base64,AA=='):
            _, measured = self.request_json('/api/garments/analyze', 'POST', {
                'backgroundId': 'M4', 'garmentId': 6, 'photoDataUrl': photo
            })
        self.assertEqual(measured['category'], 'skirt')
        self.assertEqual(measured['garmentName'], '치마')
        self.assertEqual(process_photo.call_args.kwargs['category'], 'skirt')

        self.request_json('/api/auth/signup', 'POST', {
            'username': 'skirt-owner', 'password': 'pw', 'name': 'Skirt Owner'})
        _, saved = self.request_json('/api/garments', 'POST', {
            'name': '파란 플리츠 치마', 'category': 'skirt', 'garmentTypeId': 6, 'gender': 'female',
            'lengthCm': 52, 'widthCm': 31, 'colorName': '파랑', 'photoDataUrl': photo,
        })
        self.assertEqual(saved['category'], 'skirt')
        self.assertEqual(saved['garmentTypeName'], '치마')
        updated = {**saved, 'name': '고친 플리츠 치마', 'category': 'skirt', 'garmentTypeId': 6,
                   'gender': 'female', 'lengthCm': 53, 'widthCm': 32, 'colorName': '파랑'}
        _, result = self.request_json(f"/api/garments/{saved['id']}/update", 'POST', updated)
        self.assertEqual(result['category'], 'skirt')
        self.assertEqual(result['garmentTypeName'], '치마')

    def test_garment_information_update_rejects_invalid_fields(self):
        self.request_json('/api/auth/signup', 'POST', {
            'username': 'teacher', 'password': 'pw', 'name': 'Teacher', 'teacherCode': 'fitshare'})
        original = b'\xff\xd8\xffsample original'
        photo = 'data:image/jpeg;base64,' + base64.b64encode(original).decode('ascii')
        _, saved = self.request_json('/api/garments', 'POST', {
            'name': '원래 이름', 'category': 'top', 'garmentTypeId': 1, 'gender': 'unknown',
            'lengthCm': 50, 'widthCm': 45, 'colorName': '파랑', 'photoDataUrl': photo,
        })
        self.post_error(f"/api/garments/{saved['id']}/update", {
            'name': '잘못된 정보', 'category': 'top', 'garmentTypeId': 1, 'gender': 'other',
            'lengthCm': 50, 'widthCm': 45, 'colorName': '파랑'
        }, 400)
        _, detail = self.request_json(f"/api/garments/{saved['id']}")
        self.assertEqual(detail['name'], '원래 이름')

    def test_teacher_can_delete_any_garment_and_its_files(self):
        self.request_json('/api/auth/signup', 'POST', {
            'username': 'student', 'password': 'pw', 'name': 'Student'})
        photo = 'data:image/jpeg;base64,' + base64.b64encode(b'\xff\xd8\xffsample original').decode('ascii')
        _, saved = self.request_json('/api/garments', 'POST', {
            'name': '학생이 올린 옷', 'category': 'top', 'garmentTypeId': 1, 'gender': 'unknown',
            'lengthCm': 50, 'widthCm': 45, 'colorName': '파랑', 'photoDataUrl': photo,
        })
        garment_dir = Path(self.temp_dir.name) / 'garments' / saved['id']
        (garment_dir / 'product-photo.jpg').write_bytes(b'product photo')
        (garment_dir / 'product-thumbnail.jpg').write_bytes(b'product thumbnail')

        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.request_json('/api/auth/signup', 'POST', {
            'username': 'teacher', 'password': 'pw', 'name': 'Teacher', 'teacherCode': 'fitshare'})
        status, result = self.request_json(f"/api/garments/{saved['id']}", 'DELETE')

        self.assertEqual(status, 200)
        self.assertEqual(result, {'ok': True})
        self.assertFalse(garment_dir.exists())
        _, garments = self.request_json('/api/garments')
        self.assertNotIn(saved['id'], [item['id'] for item in garments])

    def test_student_can_delete_their_own_garment_before_any_exchange(self):
        self.request_json('/api/auth/signup', 'POST', {
            'username': 'student', 'password': 'pw', 'name': 'Student'})
        photo = 'data:image/jpeg;base64,' + base64.b64encode(b'\xff\xd8\xffsample original').decode('ascii')
        _, saved = self.request_json('/api/garments', 'POST', {
            'name': '학생이 올린 옷', 'category': 'top', 'garmentTypeId': 1, 'gender': 'unknown',
            'lengthCm': 50, 'widthCm': 45, 'colorName': '파랑', 'photoDataUrl': photo,
        })
        garment_dir = Path(self.temp_dir.name) / 'garments' / saved['id']

        status, result = self.request_json(f"/api/garments/{saved['id']}", 'DELETE')

        self.assertEqual(status, 200)
        self.assertEqual(result, {'ok': True})
        self.assertFalse(garment_dir.exists())
        _, garments = self.request_json('/api/garments')
        self.assertNotIn(saved['id'], [item['id'] for item in garments])

    def test_student_cannot_delete_another_students_garment(self):
        self.request_json('/api/auth/signup', 'POST', {
            'username': 'student1', 'password': 'pw', 'name': 'Student One'})
        photo = 'data:image/jpeg;base64,' + base64.b64encode(b'\xff\xd8\xffsample original').decode('ascii')
        _, saved = self.request_json('/api/garments', 'POST', {
            'name': 'Shared owner garment', 'category': 'top', 'garmentTypeId': 1, 'gender': 'unknown',
            'lengthCm': 50, 'widthCm': 45, 'colorName': 'blue', 'photoDataUrl': photo,
        })
        self.request_json('/api/auth/logout', 'POST', {})
        self.request_json('/api/auth/signup', 'POST', {
            'username': 'student2', 'password': 'pw', 'name': 'Student Two'})

        self.request_error(f"/api/garments/{saved['id']}", 'DELETE', 403)

    def test_student_cannot_delete_after_an_exchange_request_even_if_cancelled(self):
        self.request_json('/api/auth/signup', 'POST', {
            'username': 'student', 'password': 'pw', 'name': 'Student'})
        photo = 'data:image/jpeg;base64,' + base64.b64encode(b'\xff\xd8\xffsample original').decode('ascii')
        _, saved = self.request_json('/api/garments', 'POST', {
            'name': 'Requested garment', 'category': 'top', 'garmentTypeId': 1, 'gender': 'unknown',
            'lengthCm': 50, 'widthCm': 45, 'colorName': 'blue', 'photoDataUrl': photo,
        })
        state = {'version': 1, 'users': [], 'exchanges': [
            {'id': 'e1', 'garmentId': saved['id'], 'status': 'cancelled'}
        ], 'badges': [], 'finalizedMonths': []}
        status, _ = self.request_json('/api/ranking', 'POST', {'baseRev': 0, 'state': state})
        self.assertEqual(status, 200)

        self.request_error(f"/api/garments/{saved['id']}", 'DELETE', 409)

    def test_student_cannot_delete_while_exchange_is_reserved(self):
        self.request_json('/api/auth/signup', 'POST', {
            'username': 'student', 'password': 'pw', 'name': 'Student'})
        photo = 'data:image/jpeg;base64,' + base64.b64encode(b'\xff\xd8\xffsample original').decode('ascii')
        _, saved = self.request_json('/api/garments', 'POST', {
            'name': 'Reserved garment', 'category': 'top', 'garmentTypeId': 1, 'gender': 'unknown',
            'lengthCm': 50, 'widthCm': 45, 'colorName': 'blue', 'photoDataUrl': photo,
        })
        state = {'version': 1, 'users': [], 'exchanges': [
            {'id': 'e1', 'garmentId': saved['id'], 'status': 'reserved'}
        ], 'badges': [], 'finalizedMonths': []}
        self.request_json('/api/ranking', 'POST', {'baseRev': 0, 'state': state})

        self.request_error(f"/api/garments/{saved['id']}", 'DELETE', 409)

    def test_garment_delete_requires_teacher_login_and_existing_id(self):
        self.request_error('/api/garments/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'DELETE', 401)
        self.request_json('/api/auth/signup', 'POST', {
            'username': 'teacher', 'password': 'pw', 'name': 'Teacher', 'teacherCode': 'fitshare'})
        self.request_error('/api/garments/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'DELETE', 404)

    def test_garment_delete_keeps_files_and_index_if_index_is_corrupt(self):
        self.request_json('/api/auth/signup', 'POST', {
            'username': 'teacher', 'password': 'pw', 'name': 'Teacher', 'teacherCode': 'fitshare'})
        photo = 'data:image/jpeg;base64,' + base64.b64encode(b'\xff\xd8\xffsample original').decode('ascii')
        _, saved = self.request_json('/api/garments', 'POST', {
            'name': '보호할 옷', 'category': 'top', 'garmentTypeId': 1, 'gender': 'unknown',
            'lengthCm': 50, 'widthCm': 45, 'colorName': '파랑', 'photoDataUrl': photo,
        })
        garment_dir = Path(self.temp_dir.name) / 'garments' / saved['id']
        index_path = Path(self.temp_dir.name) / 'garments' / 'index.json'
        index_path.write_text('{broken', encoding='utf-8')

        self.request_error(f"/api/garments/{saved['id']}", 'DELETE', 500)

        self.assertTrue((garment_dir / 'photo.jpg').is_file())
        self.assertEqual(index_path.read_text(encoding='utf-8'), '{broken')

    def test_garment_delete_restores_index_and_directory_if_file_cleanup_fails(self):
        self.request_json('/api/auth/signup', 'POST', {
            'username': 'teacher', 'password': 'pw', 'name': 'Teacher', 'teacherCode': 'fitshare'})
        photo = 'data:image/jpeg;base64,' + base64.b64encode(b'\xff\xd8\xffsample original').decode('ascii')
        _, saved = self.request_json('/api/garments', 'POST', {
            'name': '복구할 옷', 'category': 'top', 'garmentTypeId': 1, 'gender': 'unknown',
            'lengthCm': 50, 'widthCm': 45, 'colorName': '파랑', 'photoDataUrl': photo,
        })
        garment_dir = Path(self.temp_dir.name) / 'garments' / saved['id']

        with patch('server.shutil.rmtree', side_effect=OSError('simulated cleanup failure')):
            self.request_error(f"/api/garments/{saved['id']}", 'DELETE', 500)

        self.assertTrue((garment_dir / 'photo.jpg').is_file())
        _, garments = self.request_json('/api/garments')
        self.assertIn(saved['id'], [item['id'] for item in garments])

    def test_saved_garment_rejects_missing_or_invalid_values(self):
        photo = 'data:image/jpeg;base64,' + base64.b64encode(b'\xff\xd8\xffjpeg').decode('ascii')
        self.post_error('/api/garments', {'photoDataUrl': photo}, 400)
        self.post_error('/api/garments', {
            'name': '옷', 'category': 'top', 'garmentTypeId': 1, 'gender': 'unknown-value', 'lengthCm': 20,
            'widthCm': 30, 'colorName': '검정', 'photoDataUrl': photo,
        }, 400)
        self.post_error('/api/garments', {
            'name': '옷', 'category': 'top', 'garmentTypeId': 1, 'gender': 'unknown', 'lengthCm': 20,
            'widthCm': 30, 'colorName': '검정',
            'photoDataUrl': 'data:image/jpeg;base64,' + base64.b64encode(b'not a jpeg').decode('ascii'),
        }, 400)
        self.post_error('/api/garments/analyze', {
            'backgroundId': 'M4', 'garmentId': 0, 'photoDataUrl': photo,
        }, 400)


if __name__ == '__main__':
    unittest.main()
