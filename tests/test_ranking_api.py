import base64

import http.cookiejar

import json

import tempfile

import threading

import unittest

import urllib.error

import urllib.request

import os
from unittest.mock import patch

from pathlib import Path



import server



EMPTY = {'version': 1, 'users': [], 'exchanges': [], 'badges': [], 'finalizedMonths': []}





class RankingApiTests(unittest.TestCase):

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



    def call(self, path, payload=None):

        body = None if payload is None else json.dumps(payload).encode('utf-8')

        request = urllib.request.Request(self.base_url + path, data=body, method='GET' if body is None else 'POST')

        if body is not None:

            request.add_header('Content-Type', 'application/json')

        try:

            with self.opener.open(request, timeout=15) as response:

                return response.status, json.loads(response.read().decode('utf-8'))

        except urllib.error.HTTPError as error:

            return error.code, json.loads(error.read().decode('utf-8'))



    def login(self, username='minsu', name='민수'):

        status, result = self.call('/api/auth/signup', {'username': username, 'password': 'secret1', 'name': name})

        self.assertEqual(status, 201, result)



    def test_empty_ranking_then_save_and_read_back(self):

        self.login()

        self.assertEqual(self.call('/api/ranking'), (200, {'rev': 0, 'state': None}))

        state = {**EMPTY, 'users': [{'id': 'u1', 'name': '민수'}]}

        self.assertEqual(self.call('/api/ranking', {'baseRev': 0, 'state': state}), (200, {'rev': 1}))

        self.assertEqual(self.call('/api/ranking'), (200, {'rev': 1, 'state': state}))



    def test_stale_save_is_refused_with_latest_state(self):

        self.login()

        first = {**EMPTY, 'users': [{'id': 'u1', 'name': '민수'}]}

        self.call('/api/ranking', {'baseRev': 0, 'state': first})

        status, result = self.call('/api/ranking', {'baseRev': 0, 'state': EMPTY})

        self.assertEqual(status, 409)

        self.assertEqual((result['rev'], result['state']), (1, first))

        self.assertEqual(self.call('/api/ranking')[1]['state'], first)



    def test_malformed_ranking_is_rejected(self):

        self.login()

        for payload in ({'baseRev': 0}, {'baseRev': 'x', 'state': EMPTY}, {'baseRev': 0, 'state': {'users': []}}, {'baseRev': True, 'state': EMPTY}):

            self.assertEqual(self.call('/api/ranking', payload)[0], 400)

        self.assertEqual(self.call('/api/ranking')[1]['rev'], 0)



    def test_ranking_cannot_be_saved_without_login(self):

        self.assertEqual(self.call('/api/ranking', {'baseRev': 0, 'state': EMPTY})[0], 401)

        self.assertEqual(self.call('/api/ranking')[1]['rev'], 0)


    def test_teacher_handoff_requires_teacher_and_completes_only_reserved_exchange(self):
        state = {**EMPTY,
                 'users': [{'id': 'u1', 'name': '지수'}, {'id': 'u2', 'name': '민수'}],
                 'exchanges': [{'id': 'e1', 'giverId': 'u1', 'receiverId': 'u2', 'garmentId': 'coat-1',
                                'itemName': '겨울 코트', 'status': 'reserved', 'createdAt': '2026-10-23T00:00:00Z'}]}
        self.login('student', '민수')
        self.assertEqual(self.call('/api/ranking', {'baseRev': 0, 'state': state})[0], 200)
        self.assertEqual(self.call('/api/ranking/teacher-handoff', {'baseRev': 1, 'exchangeId': 'e1'})[0], 403)
        self.call('/api/auth/logout', {})
        status, teacher = self.call('/api/auth/signup', {'username': 'teacher', 'password': 'secret1',
                                                          'name': '선생님', 'teacherCode': 'fitshare'})
        self.assertEqual((status, teacher['user']['role']), (201, 'teacher'))
        status, result = self.call('/api/ranking/teacher-handoff', {'baseRev': 1, 'exchangeId': 'e1'})
        self.assertEqual(status, 200, result)
        self.assertEqual(result['state']['exchanges'][0]['status'], 'completed')
        self.assertTrue(result['state']['exchanges'][0]['completedAt'])
        self.assertEqual(self.call('/api/ranking/teacher-handoff', {'baseRev': 2, 'exchangeId': 'e1'})[0], 409)

    def test_teacher_handoff_requires_current_revision_and_existing_reservation(self):
        self.call('/api/auth/signup', {'username': 'teacher', 'password': 'secret1', 'name': '선생님',
                                       'teacherCode': 'fitshare'})
        self.assertEqual(self.call('/api/ranking/teacher-handoff', {'baseRev': 0, 'exchangeId': 'missing'})[0], 404)
        state = {**EMPTY, 'users': [{'id': 'u1', 'name': '지수'}, {'id': 'u2', 'name': '민수'}],
                 'exchanges': [{'id': 'e1', 'giverId': 'u1', 'receiverId': 'u2', 'itemName': '점퍼',
                                'status': 'reserved', 'createdAt': '2026-10-23T00:00:00Z'}]}
        self.call('/api/ranking', {'baseRev': 0, 'state': state})
        status, stale = self.call('/api/ranking/teacher-handoff', {'baseRev': 0, 'exchangeId': 'e1'})
        self.assertEqual((status, stale['rev'], stale['state']), (409, 1, state))


    def test_student_cannot_complete_another_students_reservation_through_general_sync(self):
        self.login('other', '다른학생')
        reserved = {**EMPTY, 'users': [{'id': 'u1', 'name': '지수'}, {'id': 'u2', 'name': '민수'}],
                    'exchanges': [{'id': 'e1', 'giverId': 'u1', 'receiverId': 'u2', 'garmentId': 'coat-1',
                                   'itemName': '겨울 코트', 'status': 'reserved', 'createdAt': '2026-10-23T00:00:00Z'}]}
        self.call('/api/ranking', {'baseRev': 0, 'state': reserved})
        forged = {**reserved, 'exchanges': [{**reserved['exchanges'][0], 'status': 'completed',
                                             'completedAt': '2026-10-23T00:01:00Z'}]}
        self.assertEqual(self.call('/api/ranking', {'baseRev': 1, 'state': forged})[0], 403)
        self.assertEqual(self.call('/api/ranking')[1]['state']['exchanges'][0]['status'], 'reserved')



    def test_signup_login_logout_and_me(self):

        self.assertEqual(self.call('/api/auth/me'), (200, {'user': None}))

        self.login('minsu', '민수')

        self.assertEqual(self.call('/api/auth/me')[1]['user'], {'username': 'minsu', 'name': '민수', 'role': 'student'})

        self.call('/api/auth/logout', {})

        self.assertEqual(self.call('/api/auth/me')[1]['user'], None)

        self.assertEqual(self.call('/api/auth/login', {'username': 'MinSu', 'password': 'secret1'})[0], 200)

        self.assertEqual(self.call('/api/auth/me')[1]['user']['name'], '민수')



    def test_signup_rejects_bad_or_duplicate_input(self):

        base = {'username': 'minsu', 'password': 'secret1', 'name': '민수'}

        for change in ({'username': ''}, {'username': '   '}, {'username': 'a' * 201}, {'password': ''},

                       {'name': ''}, {'name': '가' * 201}, {'password': 'x' * 201}):

            self.assertEqual(self.call('/api/auth/signup', {**base, **change})[0], 400, change)

        self.login()

        self.assertEqual(self.call('/api/auth/signup', {**base, 'name': '다른이름'})[0], 409)  # 같은 아이디

        self.assertEqual(self.call('/api/auth/signup', {**base, 'username': 'other'})[0], 409)  # 같은 이름

    def test_teacher_signup_requires_code_and_client_role_is_ignored(self):
        self.assertEqual(self.call('/api/auth/signup', {'username': 'badteacher', 'password': 'pw',
                                                        'name': 'Bad', 'role': 'teacher'})[1]['user']['role'], 'student')
        status, teacher = self.call('/api/auth/signup', {'username': 'teacher', 'password': 'pw',
                                                         'name': 'Teacher', 'role': 'student',
                                                         'teacherCode': 'fitshare'})
        self.assertEqual(status, 201)
        self.assertEqual(teacher['user']['role'], 'teacher')

    def test_teacher_account_routes_require_teacher_and_revoke_student_sessions(self):
        self.assertEqual(self.call('/api/auth/teacher-code', {'newCode': 'changed'})[0], 401)
        self.login('student', 'Student')
        self.assertEqual(self.call('/api/auth/teacher-code', {'newCode': 'changed'})[0], 403)
        self.call('/api/auth/logout', {})
        status, teacher = self.call('/api/auth/signup', {'username': 'teacher', 'password': 'pw', 'name': 'Teacher',
                                                        'teacherCode': 'fitshare'})
        self.assertEqual((status, teacher['user']['role']), (201, 'teacher'))
        student_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        login = urllib.request.Request(self.base_url + '/api/auth/login', data=json.dumps(
            {'username': 'student', 'password': 'secret1'}).encode(),
            headers={'Content-Type': 'application/json'}, method='POST')
        student_opener.open(login).read()
        self.assertEqual(self.call('/api/auth/teacher-code', {'newCode': 'changed'}), (200, {'ok': True}))
        status, result = self.call('/api/auth/reset-student-password',
                                   {'username': 'student', 'newPassword': 'reset'})
        self.assertEqual((status, result), (200, {'ok': True}))
        self.assertIsNone(json.loads(student_opener.open(self.base_url + '/api/auth/me').read())['user'])
        self.assertEqual(self.call('/api/auth/signup', {'username': 'oldcode', 'password': 'pw', 'name': 'Old',
                                                        'teacherCode': 'fitshare'})[0], 400)
        status, new_teacher = self.call('/api/auth/signup', {'username': 'newcode', 'password': 'pw', 'name': 'New',
                                                             'teacherCode': 'changed'})
        self.assertEqual((status, new_teacher['user']['role']), (201, 'teacher'))

    def test_own_password_change_requires_current_password_and_expires_session(self):
        self.login('student', 'Student')
        self.assertEqual(self.call('/api/auth/change-password', {'currentPassword': 'wrong',
                                                                  'newPassword': 'new-secret'})[0], 401)
        self.assertIsNotNone(self.call('/api/auth/me')[1]['user'])

        self.assertEqual(self.call('/api/auth/change-password', {'currentPassword': 'secret1',
                                                                  'newPassword': 'new-secret'}),
                         (200, {'ok': True}))
        self.assertIsNone(self.call('/api/auth/me')[1]['user'])
        self.assertEqual(self.call('/api/auth/login', {'username': 'student', 'password': 'secret1'})[0], 401)
        self.assertEqual(self.call('/api/auth/login', {'username': 'student', 'password': 'new-secret'})[0], 200)

    def test_own_password_change_requires_login_and_rejects_invalid_new_password(self):
        self.assertEqual(self.call('/api/auth/change-password', {'currentPassword': 'old',
                                                                  'newPassword': 'new'})[0], 401)
        self.login('student', 'Student')
        for password in (' ', 'x' * 201):
            self.assertEqual(self.call('/api/auth/change-password', {'currentPassword': 'secret1',
                                                                      'newPassword': password})[0], 400)

    def test_teacher_can_change_own_password(self):
        status, result = self.call('/api/auth/signup', {'username': 'teacher', 'password': 'teacher-old',
                                                        'name': 'Teacher', 'teacherCode': 'fitshare'})
        self.assertEqual((status, result['user']['role']), (201, 'teacher'))
        self.assertEqual(self.call('/api/auth/change-password', {'currentPassword': 'teacher-old',
                                                                  'newPassword': 'teacher-new'}),
                         (200, {'ok': True}))
        self.assertEqual(self.call('/api/auth/login', {'username': 'teacher', 'password': 'teacher-new'})[0], 200)

    def add_saved_garment(self):
        garment_id = 'a4831e58-d3d0-4e96-a7c8-b12734037a6c'
        root = Path(self.temp_dir.name) / 'garments'
        folder = root / garment_id
        folder.mkdir(parents=True)
        source = b'original photo stays unchanged'
        cutout = b'\x89PNG\r\n\x1a\ntransparent cutout'
        (folder / 'photo.jpg').write_bytes(source)
        (folder / 'closet.png').write_bytes(b'legacy GrabCut cutout')
        (folder / 'product-cutout.png').write_bytes(cutout)
        item = {'id': garment_id, 'name': 'Sample', 'maskPipelineVersion': server.GARMENT_MASK_PIPELINE_VERSION,
                'productMaskPipelineVersion': server.PRODUCT_MASK_PIPELINE_VERSION,
                'photoUrl': f'/api/garments/{garment_id}/photo'}
        (folder / 'garment.json').write_text(json.dumps(item), encoding='utf-8')
        (root / 'index.json').write_text(json.dumps([item]), encoding='utf-8')
        return garment_id, source

    def test_product_photo_is_teacher_only_and_saved_separately(self):
        garment_id, source = self.add_saved_garment()
        self.assertEqual(self.call(f'/api/garments/{garment_id}/product-photo', {})[0], 401)
        for suffix in range(10):
            self.assertEqual(self.call(f'/api/garments/missing-{suffix}/product-photo', {})[0], 401)
        self.assertEqual(self.httpd.product_generation_locks, {})
        self.login('student', 'Student')
        self.assertEqual(self.call(f'/api/garments/{garment_id}/product-photo', {})[0], 403)
        self.call('/api/auth/logout', {})
        self.call('/api/auth/signup', {'username': 'teacher', 'password': 'pw', 'name': 'Teacher',
                                       'teacherCode': 'fitshare'})
        generated = b'\x89PNG\r\n\x1a\nproduct photo'
        with patch('tools.replicate_product_photo.generate_product_photo', return_value=(generated, 'image/png')) as generate:
            with patch.dict(os.environ, {'REPLICATE_API_TOKEN': 'test-token', 'REPLICATE_PRODUCT_MODEL_VERSION': 'test-version'}):
                status, response = self.call(f'/api/garments/{garment_id}/product-photo', {})
                self.assertEqual(status, 201)
                self.assertEqual(response['productPhotoUrl'], f'/api/garments/{garment_id}/product-photo')
                status, cached = self.call(f'/api/garments/{garment_id}/product-photo', {})
                self.assertEqual(status, 200)
                self.assertEqual(cached, response)
                request = urllib.request.Request(self.base_url + response['productPhotoUrl'])
                with self.opener.open(request) as result:
                    self.assertEqual(result.headers.get_content_type(), 'image/png')
                    self.assertEqual(result.read(), generated)
            self.assertEqual(generate.call_count, 1)
        student_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        signup = urllib.request.Request(self.base_url + '/api/auth/signup', data=json.dumps(
            {'username': 'viewer', 'password': 'pw', 'name': 'Viewer'}).encode(),
            headers={'Content-Type': 'application/json'}, method='POST')
        student_opener.open(signup).read()
        with self.assertRaises(urllib.error.HTTPError) as denied:
            student_opener.open(self.base_url + f'/api/garments/{garment_id}/product-photo')
        self.assertEqual(denied.exception.code, 403)
        folder = Path(self.temp_dir.name) / 'garments' / garment_id
        self.assertEqual(generate.call_args.args[0], (folder / 'product-cutout.png').read_bytes())
        prompt = generate.call_args.args[1]
        self.assertIn('선명하고 즐거운 무지개 그라데이션', prompt)
        self.assertIn('옷의 색상', prompt)
        self.assertIn('실제로 보이는 것만', prompt)
        self.assertIn('숨겨지거나 가려진 부분', prompt)
        self.assertIn('보노보노 캐릭터', prompt)
        self.assertIn('영향을 주지', prompt)
        self.assertNotIn('warm ivory background', prompt)
        self.assertEqual((folder / 'photo.jpg').read_bytes(), source)
        self.assertTrue((folder / 'product-photo.png').exists())
        saved = json.loads((folder / 'garment.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['photoUrl'], f'/api/garments/{garment_id}/photo')
        self.assertEqual(saved['productPhotoFile'], 'product-photo.png')

    def test_only_teacher_can_delete_product_photo_and_original_stays(self):
        garment_id, source = self.add_saved_garment()
        folder = Path(self.temp_dir.name) / 'garments' / garment_id
        (folder / 'product-photo.png').write_bytes(b'ai-photo')
        (folder / 'product-thumbnail.jpg').write_bytes(b'thumb')
        item = json.loads((folder / 'garment.json').read_text(encoding='utf-8'))
        item.update({'productPhotoFile': 'product-photo.png', 'productPhotoUrl': 'x', 'productPhotoGeneratedAt': 'now'})
        (folder / 'garment.json').write_text(json.dumps(item), encoding='utf-8')
        (folder.parent / 'index.json').write_text(json.dumps([item]), encoding='utf-8')

        def delete():
            request = urllib.request.Request(self.base_url + f'/api/garments/{garment_id}/product-photo', method='DELETE')
            try:
                with self.opener.open(request) as result:
                    return result.status
            except urllib.error.HTTPError as error:
                return error.code

        self.assertEqual(delete(), 401)
        self.login('student', 'Student')
        self.assertEqual(delete(), 403)
        self.assertTrue((folder / 'product-photo.png').exists())
        self.call('/api/auth/logout', {})
        self.call('/api/auth/signup', {'username': 'teacher', 'password': 'pw', 'name': 'Teacher',
                                       'teacherCode': 'fitshare'})
        self.assertEqual(delete(), 200)
        self.assertEqual(delete(), 404)
        self.assertFalse((folder / 'product-photo.png').exists())
        self.assertFalse((folder / 'product-thumbnail.jpg').exists())
        self.assertEqual((folder / 'photo.jpg').read_bytes(), source)
        for path in (folder / 'garment.json', folder.parent / 'index.json'):
            data = json.loads(path.read_text(encoding='utf-8'))
            record = data[0] if isinstance(data, list) else data
            self.assertNotIn('productPhotoFile', record)
            self.assertNotIn('productPhotoGeneratedAt', record)

    def test_product_photo_reports_missing_server_configuration(self):
        garment_id, _ = self.add_saved_garment()
        self.call('/api/auth/signup', {'username': 'teacher', 'password': 'pw', 'name': 'Teacher',
                                       'teacherCode': 'fitshare'})
        with patch('server.get_replicate_api_token', return_value=''), patch(
                'server.get_replicate_product_model_version', return_value=''):
            status, result = self.call(f'/api/garments/{garment_id}/product-photo', {})
        self.assertEqual(status, 503)
        self.assertIn('서버에', result['error'])

    def prepare_saved_product_photo(self):
        garment_id, source = self.add_saved_garment()
        folder = Path(self.temp_dir.name) / 'garments' / garment_id
        old_photo = b'previous AI photo'
        (folder / 'product-photo.png').write_bytes(old_photo)
        (folder / 'product-thumbnail.jpg').write_bytes(b'previous thumbnail')
        item = json.loads((folder / 'garment.json').read_text(encoding='utf-8'))
        item.update(productPhotoFile='product-photo.png',
                    productPhotoUrl=f'/api/garments/{garment_id}/product-photo',
                    productPhotoGeneratedAt='2026-01-01T00:00:00+00:00')
        (folder / 'garment.json').write_text(json.dumps(item), encoding='utf-8')
        (folder.parent / 'index.json').write_text(json.dumps([item]), encoding='utf-8')
        self.call('/api/auth/signup', {'username': 'teacher', 'password': 'pw', 'name': 'Teacher',
                                       'teacherCode': 'fitshare'})
        return garment_id, folder, source, old_photo

    def test_student_can_generate_own_first_photo_but_not_regenerate_or_generate_for_others(self):
        garment_id, _ = self.add_saved_garment()
        folder = Path(self.temp_dir.name) / 'garments' / garment_id
        item = json.loads((folder / 'garment.json').read_text(encoding='utf-8'))
        item.update(ownerName='Student', ownerUsername='student')
        (folder / 'garment.json').write_text(json.dumps(item), encoding='utf-8')
        self.login('student', 'Student')
        with patch('server.get_replicate_api_token', return_value='test-token'), patch(
                'server.get_replicate_product_model_version', return_value='test-version'), patch(
                'tools.replicate_product_photo.generate_product_photo', return_value=(b'first photo', 'image/png')) as generate:
            self.assertEqual(self.call(f'/api/garments/{garment_id}/product-photo', {})[0], 201)
            self.assertEqual(self.call(f'/api/garments/{garment_id}/product-photo', {})[0], 200)
            self.assertEqual(self.call(f'/api/garments/{garment_id}/product-photo', {'regenerate': True})[0], 403)
            self.call('/api/auth/logout', {})
            self.login('another', 'Another')
            self.assertEqual(self.call(f'/api/garments/{garment_id}/product-photo', {})[0], 403)
            self.assertEqual(generate.call_count, 1)
        self.assertEqual((folder / 'product-photo.png').read_bytes(), b'first photo')

    def test_teacher_edits_other_students_clothes_without_taking_ownership(self):
        self.login('student', 'Student')
        photo = 'data:image/jpeg;base64,' + base64.b64encode(b'\xff\xd8\xfforiginal').decode('ascii')
        payload = {'name': 'Shirt', 'category': 'top', 'garmentTypeId': 2, 'gender': 'unknown',
                   'lengthCm': 50, 'widthCm': 45, 'colorName': 'White', 'photoDataUrl': photo}
        status, saved = self.call('/api/garments', payload)
        self.assertEqual(status, 201)
        self.assertEqual(saved.get('ownerUsername'), 'student')
        self.assertEqual(self.call(f"/api/garments/{saved['id']}/update", {'name': 'My shirt'})[0], 200)
        self.call('/api/auth/logout', {})
        self.assertEqual(self.call(f"/api/garments/{saved['id']}/update", {'name': 'Guest edit'})[0], 401)
        self.call('/api/auth/signup', {'username': 'teacher', 'password': 'pw', 'name': 'Teacher',
                                       'teacherCode': 'fitshare'})
        status, updated = self.call(f"/api/garments/{saved['id']}/update", {'name': 'Reviewed shirt'})
        self.assertEqual(status, 200)
        self.assertEqual((updated['name'], updated['ownerName'], updated['ownerUsername']),
                         ('Reviewed shirt', 'Student', 'student'))
        self.call('/api/auth/logout', {})
        self.login('other', 'Other')
        self.assertEqual(self.call(f"/api/garments/{saved['id']}/update", {'name': 'Stolen'})[0], 403)
        self.call('/api/auth/logout', {})
        self.call('/api/auth/login', {'username': 'student', 'password': 'secret1'})
        self.assertEqual(self.call(f"/api/garments/{saved['id']}/update", {'name': 'Still mine'})[0], 200)

    def test_teacher_can_regenerate_saved_photo_and_invalidate_thumbnail(self):
        garment_id, folder, source, _ = self.prepare_saved_product_photo()
        new_photo = b'new AI photo'
        with patch('server.get_replicate_api_token', return_value='test-token'), patch(
                'server.get_replicate_product_model_version', return_value='test-version'), patch(
                'tools.replicate_product_photo.generate_product_photo', return_value=(new_photo, 'image/png')) as generate:
            self.assertEqual(self.call(f'/api/garments/{garment_id}/product-photo', {})[0], 200)
            generate.assert_not_called()
            status, result = self.call(f'/api/garments/{garment_id}/product-photo', {'regenerate': True})
        self.assertEqual(status, 201)
        self.assertEqual((folder / 'product-photo.png').read_bytes(), new_photo)
        self.assertFalse((folder / 'product-thumbnail.jpg').exists())
        self.assertEqual((folder / 'photo.jpg').read_bytes(), source)
        self.assertEqual(generate.call_args.args[0], (folder / 'product-cutout.png').read_bytes())
        saved = json.loads((folder / 'garment.json').read_text(encoding='utf-8'))
        indexed = json.loads((folder.parent / 'index.json').read_text(encoding='utf-8'))[0]
        self.assertNotEqual(saved['productPhotoGeneratedAt'], '2026-01-01T00:00:00+00:00')
        self.assertEqual(saved['productPhotoGeneratedAt'], result['generatedAt'])
        self.assertEqual(saved, indexed)
        with self.opener.open(self.base_url + result['productPhotoUrl']) as response:
            self.assertEqual(response.read(), new_photo)
            self.assertEqual(response.headers['Cache-Control'], 'private, no-cache')

    def test_failed_regeneration_preserves_existing_photo_thumbnail_and_metadata(self):
        garment_id, folder, _, old_photo = self.prepare_saved_product_photo()
        before = {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}
        from tools.replicate_product_photo import ProductPhotoError
        with patch('server.get_replicate_api_token', return_value='test-token'), patch(
                'server.get_replicate_product_model_version', return_value='test-version'), patch(
                'tools.replicate_product_photo.generate_product_photo', side_effect=ProductPhotoError('provider failed')):
            status, result = self.call(f'/api/garments/{garment_id}/product-photo', {'regenerate': True})
        self.assertEqual(status, 502)
        self.assertEqual(result['error'], 'provider failed')
        self.assertEqual({path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}, before)
        with self.opener.open(self.base_url + f'/api/garments/{garment_id}/product-photo') as response:
            self.assertEqual(response.read(), old_photo)

    def test_regeneration_storage_failure_restores_previous_photo_and_record(self):
        garment_id, folder, _, _ = self.prepare_saved_product_photo()
        before = {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}
        original_write = server.write_json_safely

        def fail_metadata_write(path, value):
            if path == folder / 'garment.json':
                raise OSError('disk write failed')
            return original_write(path, value)

        with patch('server.get_replicate_api_token', return_value='test-token'), patch(
                'server.get_replicate_product_model_version', return_value='test-version'), patch(
                'tools.replicate_product_photo.generate_product_photo', return_value=(b'new image', 'image/png')), patch(
                'server.write_json_safely', side_effect=fail_metadata_write):
            status, _ = self.call(f'/api/garments/{garment_id}/product-photo', {'regenerate': True})
        self.assertEqual(status, 500)
        self.assertEqual({path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}, before)

    def test_regeneration_rejects_students_and_invalid_flags(self):
        garment_id, folder, _, old_photo = self.prepare_saved_product_photo()
        with patch('tools.replicate_product_photo.generate_product_photo') as generate:
            for value in ('true', 1, None, [], {}):
                self.assertEqual(self.call(f'/api/garments/{garment_id}/product-photo', {'regenerate': value})[0], 400)
            self.call('/api/auth/logout', {})
            self.assertEqual(self.call(f'/api/garments/{garment_id}/product-photo', {'regenerate': True})[0], 401)
            self.login('student', 'Student')
            self.assertEqual(self.call(f'/api/garments/{garment_id}/product-photo', {'regenerate': True})[0], 403)
            generate.assert_not_called()
        self.assertEqual((folder / 'product-photo.png').read_bytes(), old_photo)

    def test_edit_during_generation_does_not_discard_new_photo_metadata(self):
        garment_id, folder, _, _ = self.prepare_saved_product_photo()
        entered, proceed = threading.Event(), threading.Event()
        results = []
        original_validate = server.valid_measurement

        def validate(value):
            if threading.current_thread().name != 'MainThread' and not entered.is_set():
                entered.set()
                if not proceed.wait(timeout=5):
                    raise RuntimeError('test edit did not resume')
            return original_validate(value)

        update = {'name': 'Edited shirt', 'category': 'top', 'garmentTypeId': 2, 'gender': 'unknown',
                  'lengthCm': 50, 'widthCm': 45, 'colorName': 'White'}
        with patch('server.valid_measurement', side_effect=validate), patch(
                'server.get_replicate_api_token', return_value='test-token'), patch(
                'server.get_replicate_product_model_version', return_value='test-version'), patch(
                'tools.replicate_product_photo.generate_product_photo', return_value=(b'new photo', 'image/png')):
            edit = threading.Thread(target=lambda: results.append(
                self.call(f'/api/garments/{garment_id}/update', update)))
            edit.start()
            try:
                self.assertTrue(entered.wait(timeout=3))
                status, generated = self.call(f'/api/garments/{garment_id}/product-photo', {'regenerate': True})
                self.assertEqual(status, 201)
            finally:
                proceed.set()
                edit.join(timeout=5)
        self.assertEqual(results[0][0], 200)
        saved = json.loads((folder / 'garment.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['productPhotoGeneratedAt'], generated['generatedAt'])
        self.assertEqual(saved['name'], 'Edited shirt')

    def test_regeneration_rejects_duplicate_request_while_generation_is_running(self):
        garment_id, _, _, _ = self.prepare_saved_product_photo()
        started, finish = threading.Event(), threading.Event()
        statuses = []

        def generate_photo(*_):
            started.set()
            if not finish.wait(timeout=5):
                raise RuntimeError('test generation did not finish')
            return b'updated AI photo', 'image/png'

        with patch('server.get_replicate_api_token', return_value='test-token'), patch(
                'server.get_replicate_product_model_version', return_value='test-version'), patch(
                'tools.replicate_product_photo.generate_product_photo', side_effect=generate_photo) as generate:
            first = threading.Thread(target=lambda: statuses.append(
                self.call(f'/api/garments/{garment_id}/product-photo', {'regenerate': True})[0]))
            first.start()
            try:
                self.assertTrue(started.wait(timeout=3), 'saved photo was returned instead of regenerated')
                self.assertEqual(self.call(f'/api/garments/{garment_id}/product-photo', {'regenerate': True})[0], 409)
            finally:
                finish.set()
                first.join(timeout=5)
            self.assertEqual(statuses, [201])
            self.assertEqual(generate.call_count, 1)

    def test_nonexistent_garments_do_not_allocate_generation_locks(self):
        self.call('/api/auth/signup', {'username': 'teacher', 'password': 'pw', 'name': 'Teacher',
                                       'teacherCode': 'fitshare'})
        for suffix in range(10):
            self.assertEqual(self.call(f'/api/garments/{suffix:08d}-0000-4000-8000-000000000000/product-photo', {})[0], 404)
        self.assertEqual(self.httpd.product_generation_locks, {})

    def test_malformed_garment_metadata_does_not_allocate_generation_lock(self):
        self.call('/api/auth/signup', {'username': 'teacher', 'password': 'pw', 'name': 'Teacher',
                                       'teacherCode': 'fitshare'})
        garment_id = '00000000-0000-4000-8000-000000000010'
        folder = Path(self.temp_dir.name) / 'garments' / garment_id
        folder.mkdir(parents=True)
        (folder / 'garment.json').write_text('[]', encoding='utf-8')
        self.assertEqual(self.call(f'/api/garments/{garment_id}/product-photo', {})[0], 404)
        self.assertEqual(self.httpd.product_generation_locks, {})

    def test_concurrent_product_photo_requests_generate_once(self):
        garment_id, _ = self.add_saved_garment()
        self.call('/api/auth/signup', {'username': 'teacher', 'password': 'pw', 'name': 'Teacher',
                                       'teacherCode': 'fitshare'})
        generated = b'\x89PNG\r\n\x1a\nproduct photo'
        statuses = []

        def slow_generate(*_args):
            import time
            time.sleep(0.25)
            return generated, 'image/png'

        def request_photo():
            statuses.append(self.call(f'/api/garments/{garment_id}/product-photo', {})[0])

        with patch('tools.replicate_product_photo.generate_product_photo', side_effect=slow_generate) as generate:
            with patch.dict(os.environ, {'REPLICATE_API_TOKEN': 'test-token', 'REPLICATE_PRODUCT_MODEL_VERSION': 'test-version'}):
                requests = [threading.Thread(target=request_photo) for _ in range(2)]
                for request in requests:
                    request.start()
                for request in requests:
                    request.join(timeout=5)
            self.assertEqual(generate.call_count, 1)
        self.assertEqual(sorted(statuses), [200, 201])

    def test_failed_product_generation_leaves_source_and_record_untouched(self):
        garment_id, source = self.add_saved_garment()
        self.call('/api/auth/signup', {'username': 'teacher', 'password': 'pw', 'name': 'Teacher',
                                       'teacherCode': 'fitshare'})
        from tools.replicate_product_photo import ProductPhotoError
        with patch('server.get_replicate_api_token', return_value='test-token'), patch(
                'server.get_replicate_product_model_version', return_value='test-version'), patch(
                'tools.replicate_product_photo.generate_product_photo', side_effect=ProductPhotoError('provider failed')):
            status, result = self.call(f'/api/garments/{garment_id}/product-photo', {})
        self.assertEqual(status, 502)
        folder = Path(self.temp_dir.name) / 'garments' / garment_id
        self.assertEqual((folder / 'photo.jpg').read_bytes(), source)
        saved = json.loads((folder / 'garment.json').read_text(encoding='utf-8'))
        self.assertNotIn('productPhotoFile', saved)

    def test_wrong_password_is_refused_and_locks_after_five_tries(self):

        self.login()

        self.call('/api/auth/logout', {})

        for _ in range(5):

            self.assertEqual(self.call('/api/auth/login', {'username': 'minsu', 'password': 'wrong-one'})[0], 401)

        self.assertEqual(self.call('/api/auth/login', {'username': 'minsu', 'password': 'secret1'})[0], 429)

        self.assertEqual(self.call('/api/auth/login', {'username': 'nobody', 'password': 'secret1'})[0], 401)



    def test_password_is_not_stored_in_plain_text(self):

        self.login()

        saved = (Path(self.temp_dir.name) / 'accounts.json').read_text(encoding='utf-8')

        sessions = (Path(self.temp_dir.name) / 'sessions.json').read_text(encoding='utf-8')

        self.assertNotIn('secret1', saved)

        cookie = [c for c in self.opener.handlers if isinstance(c, urllib.request.HTTPCookieProcessor)][0].cookiejar

        token = next(c.value for c in cookie if c.name == 'fitshare_session')

        self.assertNotIn(token, sessions)



    def test_logged_in_user_owns_uploads_and_only_edits_own_garments(self):

        photo = 'data:image/jpeg;base64,' + base64.b64encode(bytes([255, 216, 255]) + b'sample').decode('ascii')
        garment = {'name': '점퍼', 'category': 'top', 'garmentTypeId': 4, 'gender': 'unknown',

                   'lengthCm': 50, 'widthCm': 45, 'colorName': '파랑', 'photoDataUrl': photo}

        self.login('minsu', '민수')

        status, saved = self.call('/api/garments', {**garment, 'ownerName': '다른사람'})

        self.assertEqual((status, saved['ownerName']), (201, '민수'))  # 올린 사람은 로그인한 나로 정해져요

        self.call('/api/auth/logout', {})

        self.login('jisu', '지수')

        update = {key: garment[key] for key in ('name', 'category', 'garmentTypeId', 'gender', 'lengthCm', 'widthCm', 'colorName')}

        self.assertEqual(self.call(f"/api/garments/{saved['id']}/update", update)[0], 403)



    def test_garment_keeps_owner_name_and_rejects_long_names(self):

        photo = 'data:image/jpeg;base64,' + base64.b64encode(bytes([255, 216, 255]) + b'sample').decode('ascii')
        garment = {'name': '점퍼', 'category': 'top', 'garmentTypeId': 4, 'gender': 'unknown',

                   'lengthCm': 50, 'widthCm': 45, 'colorName': '파랑', 'photoDataUrl': photo}

        status, saved = self.call('/api/garments', {**garment, 'ownerName': ' 지수 '})

        self.assertEqual((status, saved['ownerName']), (201, '지수'))

        self.assertEqual(self.call('/api/garments')[1][0]['ownerName'], '지수')

        self.assertEqual(self.call('/api/garments', {**garment, 'ownerName': '가' * 201})[0], 400)

        update = {'name': '점퍼', 'category': 'top', 'garmentTypeId': 4, 'gender': 'unknown',

                  'lengthCm': 50, 'widthCm': 45, 'colorName': '파랑', 'ownerName': '하준'}

        self.assertEqual(self.call(f"/api/garments/{saved['id']}/update", update)[0], 401)
        self.call('/api/auth/signup', {'username': 'teacher', 'password': 'pw', 'name': 'Teacher',
                                       'teacherCode': 'fitshare'})
        status, updated = self.call(f"/api/garments/{saved['id']}/update", update)

        self.assertEqual((status, updated['ownerName']), (200, '지수'))

        self.assertEqual(self.call(f"/api/garments/{saved['id']}/update", {**update, 'ownerName': 'x' * 201})[0], 400)

        legacy = {**garment}  # 올린 사람 없이 등록하는 예전 방식

        self.call('/api/auth/logout', {})
        self.assertEqual(self.call('/api/garments', legacy)[1].get('ownerName'), '')

    def test_garment_codes_are_sequenced_by_school_and_type_and_include_donor_username(self):
        self.login('minsu', '민수')
        photo = 'data:image/jpeg;base64,' + base64.b64encode(bytes([255, 216, 255]) + b'sample').decode('ascii')

        def register(name, garment_type_id, category, school_code='나눔초등학교'):
            status, item = self.call('/api/garments', {
                'name': name, 'category': category, 'garmentTypeId': garment_type_id, 'gender': 'unknown',
                'lengthCm': 45, 'widthCm': 35, 'colorName': '파랑', 'photoDataUrl': photo,
                'schoolCode': school_code,
            })
            self.assertEqual(status, 201, item)
            return item

        first_skirt = register('치마 1', 6, 'skirt')
        second_skirt = register('치마 2', 6, 'skirt')
        other = register('기타 옷', 7, 'other')
        other_school_skirt = register('다른 학교 치마', 6, 'skirt', '서원초등학교')

        self.assertEqual(first_skirt['garmentCode'], '나눔초등학교_s_001_minsu')
        self.assertEqual(second_skirt['garmentCode'], '나눔초등학교_s_002_minsu')
        self.assertEqual(other['garmentCode'], '나눔초등학교_e_001_minsu')
        self.assertEqual(other_school_skirt['garmentCode'], '서원초등학교_s_001_minsu')
        listed = {item['name']: item for item in self.call('/api/garments')[1]}
        self.assertEqual(listed['치마 1']['garmentNumber'], 1)
        self.assertEqual(listed['치마 2']['garmentNumber'], 2)
        self.assertEqual(listed['기타 옷']['garmentNumber'], 1)
        self.assertEqual(listed['다른 학교 치마']['garmentNumber'], 1)
        request = urllib.request.Request(self.base_url + f"/api/garments/{first_skirt['id']}", method='DELETE')
        with self.opener.open(request) as response:
            self.assertEqual(response.status, 200)
        third_skirt = register('치마 3', 6, 'skirt')
        self.assertEqual(third_skirt['garmentCode'], '나눔초등학교_s_003_minsu')

    def test_listing_existing_garments_backfills_stable_school_and_type_codes(self):
        garment_id = 'a4831e58-d3d0-4e96-a7c8-b12734037a6c'
        root = Path(self.temp_dir.name) / 'garments'
        folder = root / garment_id
        folder.mkdir(parents=True)
        legacy = {'id': garment_id, 'name': '이전 치마', 'garmentTypeId': 6, 'garmentTypeName': '치마',
                  'category': 'skirt', 'ownerName': '예전 학생 이름', 'ownerUsername': 'oldstudent',
                  'createdAt': '2020-01-01T00:00:00Z'}
        (folder / 'garment.json').write_text(json.dumps(legacy, ensure_ascii=False), encoding='utf-8')
        (root / 'index.json').write_text(json.dumps([legacy], ensure_ascii=False), encoding='utf-8')

        status, listed = self.call('/api/garments')

        self.assertEqual(status, 200)
        self.assertEqual(listed[0]['garmentCode'], '나눔초등학교_s_001_oldstudent')
        saved = json.loads((folder / 'garment.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['garmentCode'], listed[0]['garmentCode'])
        self.assertEqual(json.loads((root / '.sequences.json').read_text(encoding='utf-8'))['나눔초등학교|s'], 1)





if __name__ == '__main__':

    unittest.main()
