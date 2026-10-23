import base64
import json
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request
from pathlib import Path

import server


class ExperimentServerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.httpd = server.create_server('127.0.0.1', 0, Path(self.temp_dir.name))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f'http://127.0.0.1:{self.httpd.server_address[1]}'

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
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode('utf-8'))

    def create_experiment(self, name='상의'):
        _, result = self.request_json('/api/experiments', 'POST', {
            'garments': [{'id': 'garment-1', 'name': name, 'category': 'top', 'referenceLengthCm': 50, 'referenceWidthCm': 40}]
        })
        return result

    def test_new_experiments_get_increasing_round_numbers(self):
        first = self.create_experiment('첫 옷')
        second = self.create_experiment('둘째 옷')
        self.assertEqual(first['round'], 1)
        self.assertEqual(second['round'], 2)

    def test_round_and_records_survive_server_restart(self):
        experiment = self.create_experiment()
        old_id = experiment['id']
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.httpd = server.create_server('127.0.0.1', 0, Path(self.temp_dir.name))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f'http://127.0.0.1:{self.httpd.server_address[1]}'
        _, detail = self.request_json(f'/api/experiments/{old_id}')
        self.assertEqual(detail['round'], 1)

    def test_photo_record_is_saved_and_returned_in_experiment_detail(self):
        experiment = self.create_experiment()
        photo_bytes = b'example jpeg bytes'
        photo_data = 'data:image/jpeg;base64,' + base64.b64encode(photo_bytes).decode('ascii')
        record = {
            'id': 'M1-garment-1', 'matId': 'M1', 'garmentId': 'garment-1',
            'measuredLengthCm': 49.5, 'measuredWidthCm': 40.2,
            'errorLengthCm': -0.5, 'errorWidthCm': 0.2, 'status': 'done'
        }
        self.request_json(f"/api/experiments/{experiment['id']}/records", 'POST', {'record': record, 'photoDataUrl': photo_data})

        _, detail = self.request_json(f"/api/experiments/{experiment['id']}")
        self.assertEqual(detail['records'][0]['measuredLengthCm'], 49.5)
        photo_url = detail['records'][0]['photoUrl']
        with urllib.request.urlopen(self.base_url + photo_url, timeout=3) as response:
            self.assertEqual(response.read(), photo_bytes)

    def test_experiment_list_shows_round_and_completion(self):
        experiment = self.create_experiment()
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request_json(f"/api/experiments/{experiment['id']}/complete", 'POST', {})
        self.assertEqual(error.exception.code, 400)
        photo_data = 'data:image/jpeg;base64,' + base64.b64encode(b'photo').decode('ascii')
        for mat_id in ('M1', 'M2', 'M3', 'M4'):
            record = {'id': f'{mat_id}-garment-1', 'matId': mat_id, 'garmentId': 'garment-1', 'status': 'done'}
            self.request_json(f"/api/experiments/{experiment['id']}/records", 'POST', {'record': record, 'photoDataUrl': photo_data})
        self.request_json(f"/api/experiments/{experiment['id']}/complete", 'POST', {})
        _, listing = self.request_json('/api/experiments')
        self.assertEqual(listing[0]['round'], 1)
        self.assertTrue(listing[0]['completed'])
        self.assertEqual(listing[0]['garmentCount'], 1)

    def test_invalid_garment_values_are_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request_json('/api/experiments', 'POST', {'garments': [{'id': 'x', 'name': '', 'referenceLengthCm': 0}]})
        self.assertEqual(error.exception.code, 400)

    def test_experiment_saves_optional_collar_to_hem_reference(self):
        _, experiment = self.request_json('/api/experiments', 'POST', {
            'garments': [{'id': 'garment-1', 'name': '상의', 'category': 'top',
                          'referenceLengthCm': 50, 'referenceWidthCm': 40,
                          'referenceCollarToHemCm': 46.5}]
        })
        _, detail = self.request_json(f"/api/experiments/{experiment['id']}")
        self.assertEqual(detail['garments'][0]['referenceCollarToHemCm'], 46.5)

    def test_invalid_optional_collar_to_hem_reference_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request_json('/api/experiments', 'POST', {
                'garments': [{'id': 'garment-1', 'name': '상의', 'category': 'top',
                              'referenceLengthCm': 50, 'referenceWidthCm': 40,
                              'referenceCollarToHemCm': -1}]
            })
        self.assertEqual(error.exception.code, 400)

    def test_unknown_experiment_is_not_found(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request_json('/api/experiments/00000000-0000-4000-8000-000000000000')
        self.assertEqual(error.exception.code, 404)

    def test_dataset_report_api_returns_a_saved_comparison(self):
        report_dir = Path(self.temp_dir.name) / 'measure-dataset-report'
        report_dir.mkdir()
        (report_dir / 'report.json').write_text(json.dumps({
            'summary': {'imageCount': 20, 'successCount': 20, 'failureCount': 0}, 'rows': []
        }), encoding='utf-8')

        status, report = self.request_json('/api/measure-dataset')

        self.assertEqual(status, 200)
        self.assertEqual(report['summary']['imageCount'], 20)

    def test_dataset_report_can_be_recomputed(self):
        with mock.patch('server.analyze_measure_dataset', return_value={
            'summary': {'imageCount': 1, 'successCount': 1, 'failureCount': 0}, 'rows': []
        }) as analyze:
            status, report = self.request_json('/api/measure-dataset/run', 'POST', {})

        self.assertEqual(status, 200)
        self.assertEqual(report['summary']['imageCount'], 1)
        analyze.assert_called_once()

    def test_ai_model_comparison_api_aligns_saved_models_with_the_same_five_m4_photos(self):
        report_dir = Path(self.temp_dir.name) / 'measure-dataset-report'
        report_dir.mkdir()
        reference = [(55, 65), (54, 50), (40, 70), (49, 53), (42, 90)]
        measured = [(53.8, 66.2), (53.8, 46.6), (38.6, 72.7), (48.9, 62.6), (42.0, 96.9)]
        photos = [
            'chc_1_w55_h65.jpg', 'chc_2_w54_h50.jpg', 'chc_3_w40_h70.jpg',
            'chc_4_w49_h53.jpg', 'chc_5_w42_h90.jpg'
        ]
        rows = []
        for index, ((width, length), (measured_width, measured_length), photo) in enumerate(zip(reference, measured, photos), start=1):
            rows.append({
                'backgroundId': 'ChaArUco_Cross', 'imageName': photo,
                'garmentId': index, 'garmentName': f'옷 {index}', 'status': 'done',
                'referenceWidthCm': width, 'referenceLengthCm': length,
                'measuredWidthCm': measured_width, 'measuredLengthCm': measured_length,
                'overlayDataUrl': 'data:image/jpeg;base64,',
            })
        (report_dir / 'report.json').write_text(json.dumps({'rows': rows}), encoding='utf-8')
        gemini_rows = [
            {'photo': photo, 'status': 'done', 'widthCm': width + 1, 'lengthCm': length - 2}
            for photo, (width, length) in zip(photos, reference)
        ]
        (report_dir / 'gemini-results.json').write_text(
            json.dumps({'model': 'google/gemini-2.5-flash', 'rows': gemini_rows}), encoding='utf-8'
        )

        status, comparison = self.request_json('/api/measure-dataset/model-comparison')

        self.assertEqual(status, 200)
        self.assertEqual(comparison['sampleCount'], 5)
        methods = {method['id']: method for method in comparison['methods']}
        self.assertEqual(methods['fitshare']['widthMaeCm'], 0.58)
        self.assertEqual(methods['yolo']['widthMaeCm'], 3.4)
        self.assertEqual(methods['sam']['lengthMaeCm'], 11.48)
        self.assertEqual(methods['hrnet']['widthMaeCm'], 10.24)
        self.assertEqual(comparison['geminiComparison']['sampleCount'], 5)
        self.assertEqual(comparison['geminiComparison']['methods'][1]['widthMaeCm'], 1)

    def test_ai_model_comparison_api_explains_when_m4_measurements_are_missing(self):
        report_dir = Path(self.temp_dir.name) / 'measure-dataset-report'
        report_dir.mkdir()
        (report_dir / 'report.json').write_text(json.dumps({'rows': []}), encoding='utf-8')

        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request_json('/api/measure-dataset/model-comparison')

        self.assertEqual(error.exception.code, 422)
        self.assertIn('M4', error.exception.read().decode('utf-8'))

    def test_gemini_dataset_api_explains_when_replicate_token_is_missing(self):
        with mock.patch('server.get_replicate_api_token', return_value=''):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request_json('/api/measure-dataset/gemini', 'POST', {})

        self.assertEqual(error.exception.code, 503)
        message = error.exception.read().decode('utf-8')
        self.assertIn('REPLICATE_API_TOKEN', message)
        self.assertNotIn('r8_', message)

    def test_env_file_loads_a_token_without_overriding_the_process_environment(self):
        env_file = Path(self.temp_dir.name) / '.env'
        env_file.write_text('# private token\nREPLICATE_API_TOKEN="sample-token"\nREPLICATE_PRODUCT_MODEL_VERSION=sample-version\n', encoding='utf-8')
        values = {}

        server.load_env_file(env_file, values)

        self.assertEqual(values['REPLICATE_API_TOKEN'], 'sample-token')
        self.assertEqual(values['REPLICATE_PRODUCT_MODEL_VERSION'], 'sample-version')
        server.load_env_file(env_file, {'REPLICATE_API_TOKEN': 'already-set'})

    def test_env_file_does_not_replace_an_existing_token(self):
        env_file = Path(self.temp_dir.name) / '.env'
        env_file.write_text('REPLICATE_API_TOKEN=file-token\n', encoding='utf-8')
        values = {'REPLICATE_API_TOKEN': 'process-token'}

        server.load_env_file(env_file, values)

        self.assertEqual(values['REPLICATE_API_TOKEN'], 'process-token')

    def test_gemini_dataset_api_saves_measurements_without_returning_the_token(self):
        result = {
            'model': 'google/gemini-2.5-flash', 'sampleCount': 20,
            'successCount': 19, 'failureCount': 1, 'rows': [{'photo': 'p_1_w55_h65.jpg'}],
        }
        with mock.patch('server.get_replicate_api_token', return_value='private-test-token'), \
                mock.patch('tools.replicate_gemini.run_dataset_analysis', return_value=result) as run:
            status, response = self.request_json('/api/measure-dataset/gemini', 'POST', {})

        self.assertEqual(status, 200)
        self.assertEqual(response['sampleCount'], 20)
        self.assertEqual(response['successCount'], 19)
        self.assertNotIn('token', response)
        run.assert_called_once()
        saved = Path(self.temp_dir.name) / 'measure-dataset-report' / 'gemini-results.json'
        self.assertEqual(json.loads(saved.read_text(encoding='utf-8'))['failureCount'], 1)

    def write_dataset_report_fixture(self, failed_index=None):
        from tools.measure_dataset import read_dataset_label

        rows = []
        image_paths = sorted((server.PROJECT_ROOT / 'measure_dataset').rglob('*.jpg'))
        for index, image_path in enumerate(image_paths):
            label = read_dataset_label(image_path)
            failed = index == failed_index
            rows.append({
                **label,
                'status': 'failed' if failed else 'done',
                'measuredWidthCm': None if failed else label['referenceWidthCm'] + 1.2,
                'measuredLengthCm': None if failed else label['referenceLengthCm'] - 0.8,
            })
        report_dir = Path(self.temp_dir.name) / 'measure-dataset-report'
        report_dir.mkdir(exist_ok=True)
        (report_dir / 'report.json').write_text(json.dumps({'rows': rows}), encoding='utf-8')
        return image_paths, rows

    def test_dataset_results_can_be_saved_as_a_new_experiment_round(self):
        image_paths, _ = self.write_dataset_report_fixture()

        status, saved = self.request_json('/api/measure-dataset/save-as-experiment', 'POST', {})

        self.assertEqual(status, 201)
        self.assertEqual(saved['recordCount'], 20)
        self.assertEqual(saved['garmentCount'], 5)
        _, detail = self.request_json(f"/api/experiments/{saved['id']}")
        self.assertEqual(len(detail['records']), 20)
        record = next(row for row in detail['records'] if row['matId'] == 'M1' and row['garmentId'] == 'garment-1')
        self.assertEqual(record['errorWidthCm'], 1.2)
        self.assertEqual(record['errorLengthCm'], -0.8)
        photo_index = next(index for index, path in enumerate(image_paths) if path.name == record['imageName'])
        with urllib.request.urlopen(self.base_url + record['photoUrl'], timeout=3) as response:
            self.assertEqual(response.read(), image_paths[photo_index].read_bytes())
        _, listing = self.request_json('/api/experiments')
        self.assertEqual(listing[0]['id'], saved['id'])
        self.assertTrue(listing[0]['completed'])

    def test_dataset_results_with_a_failed_photo_are_not_saved_as_an_experiment(self):
        self.write_dataset_report_fixture(failed_index=0)

        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request_json('/api/measure-dataset/save-as-experiment', 'POST', {})

        self.assertEqual(error.exception.code, 422)
        _, listing = self.request_json('/api/experiments')
        self.assertEqual(listing, [])

    def test_dataset_results_require_all_twenty_photos(self):
        self.write_dataset_report_fixture()
        report_path = Path(self.temp_dir.name) / 'measure-dataset-report' / 'report.json'
        report = json.loads(report_path.read_text(encoding='utf-8'))
        report['rows'].pop()
        report_path.write_text(json.dumps(report), encoding='utf-8')

        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request_json('/api/measure-dataset/save-as-experiment', 'POST', {})

        self.assertEqual(error.exception.code, 422)
        _, listing = self.request_json('/api/experiments')
        self.assertEqual(listing, [])

    def test_uploaded_photo_uses_the_same_automatic_measurement_pipeline(self):
        photo = Path(server.PROJECT_ROOT / 'measure_dataset' / 'cross' / 'c_1_w55_h65.jpg').read_bytes()
        photo_data = 'data:image/jpeg;base64,' + base64.b64encode(photo).decode('ascii')

        status, result = self.request_json('/api/measurements/automatic', 'POST', {
            'backgroundId': 'M2', 'category': 'top', 'photoDataUrl': photo_data
        })

        self.assertEqual(status, 200)
        self.assertTrue(result['ok'])
        self.assertGreater(result['lengthCm'], 0)
        self.assertGreater(result['widthCm'], 0)
        self.assertEqual(result['widthKind'], 'chest')
        self.assertTrue(result['photoDataUrl'].startswith('data:image/jpeg;base64,'))
        self.assertTrue(result['fullPhotoDataUrl'].startswith('data:image/jpeg;base64,'))

    def test_uploaded_photo_rejects_an_unknown_background_or_category(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request_json('/api/measurements/automatic', 'POST', {
                'backgroundId': 'M9', 'category': 'hat', 'photoDataUrl': 'bad'
            })
        self.assertEqual(error.exception.code, 400)


if __name__ == '__main__':
    unittest.main()
