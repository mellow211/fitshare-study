import json
import hashlib
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from tools.accounts import AccountStore, AuthError


class AccountRoleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = AccountStore(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_signup_defaults_to_student(self):
        user = self.store.signup('student', 'pw', 'Student')
        self.assertEqual(user['role'], 'student')

    def test_signup_with_initial_teacher_code_gets_teacher_role(self):
        user = self.store.signup('teacher', 'pw', 'Teacher', teacher_code='fitshare')
        self.assertEqual(user['role'], 'teacher')
        self.assertEqual(self.store.login('teacher', 'pw')['role'], 'teacher')

    def test_wrong_teacher_code_creates_no_account(self):
        with self.assertRaises(AuthError):
            self.store.signup('teacher', 'pw', 'Teacher', teacher_code='wrong')
        account_path = Path(self.directory.name, 'accounts.json')
        self.assertNotIn('teacher', json.loads(account_path.read_text(encoding='utf-8')) if account_path.exists() else {})

    def test_legacy_account_without_role_is_student(self):
        account_path = Path(self.directory.name, 'accounts.json')
        account_path.write_text(json.dumps({'legacy': {'name': 'Legacy', 'salt': '00' * 16,
                                                      'hash': 'unused', 'createdAt': 1}}), encoding='utf-8')
        token = 'legacy-token'
        token_key = hashlib.sha256(token.encode('utf-8')).hexdigest()
        Path(self.directory.name, 'sessions.json').write_text(json.dumps({token_key: {'username': 'legacy', 'expires': 9999999999}}), encoding='utf-8')
        self.assertEqual(self.store.user_for_token(token)['role'], 'student')

    def test_teacher_code_hash_is_not_plaintext(self):
        self.store.signup('teacher', 'pw', 'Teacher', teacher_code='fitshare')
        contents = Path(self.directory.name, 'teacher-code.json').read_text(encoding='utf-8')
        self.assertNotIn('fitshare', contents)

    def test_teacher_code_change_persists_after_store_restart(self):
        self.store.signup('teacher', 'pw', 'Teacher', teacher_code='fitshare')
        self.store.change_teacher_code('teacher', 'new-code')
        restarted = AccountStore(self.directory.name)
        self.assertEqual(restarted.signup('nextteacher', 'pw', 'Next', teacher_code='new-code')['role'], 'teacher')

    def test_teacher_can_reset_only_student_and_revokes_all_sessions(self):
        self.store.signup('teacher', 'pw', 'Teacher', teacher_code='fitshare')
        self.store.signup('student', 'pw', 'Student')
        token_one = self.store.create_session('student')
        token_two = self.store.create_session('student')
        self.store.reset_student_password('teacher', 'student', 'new-password')
        self.assertIsNone(self.store.user_for_token(token_one))
        self.assertIsNone(self.store.user_for_token(token_two))
        self.assertEqual(self.store.login('student', 'new-password')['role'], 'student')

    def test_student_cannot_change_code_or_reset_a_password(self):
        self.store.signup('student', 'pw', 'Student')
        with self.assertRaisesRegex(AuthError, '교사만'):
            self.store.change_teacher_code('student', 'new-code')
        with self.assertRaisesRegex(AuthError, '교사만'):
            self.store.reset_student_password('student', 'student', 'new-password')

    def test_teacher_cannot_reset_teacher_or_unknown_account(self):
        self.store.signup('teacher', 'pw', 'Teacher', teacher_code='fitshare')
        self.store.signup('otherteacher', 'pw', 'Other', teacher_code='fitshare')
        for target in ('teacher', 'missing'):
            with self.assertRaises(AuthError):
                self.store.reset_student_password('teacher', target, 'new-password')

    def test_password_reset_rejects_blank_and_oversized_password(self):
        self.store.signup('teacher', 'pw', 'Teacher', teacher_code='fitshare')
        self.store.signup('student', 'pw', 'Student')
        for password in (' ', 'x' * 201):
            with self.assertRaises(AuthError):
                self.store.reset_student_password('teacher', 'student', password)
        self.assertEqual(self.store.login('student', 'pw')['role'], 'student')

    def test_password_reset_revokes_sessions_before_saving_new_password(self):
        self.store.signup('teacher', 'pw', 'Teacher', teacher_code='fitshare')
        self.store.signup('student', 'pw', 'Student')
        token = self.store.create_session('student')
        original_write = __import__('tools.accounts', fromlist=['_write'])._write

        def fail_account_write(path, value):
            if path == self.store.accounts_path:
                raise OSError('simulated account write failure')
            return original_write(path, value)

        with patch('tools.accounts._write', side_effect=fail_account_write):
            with self.assertRaises(OSError):
                self.store.reset_student_password('teacher', 'student', 'new-password')
        self.assertIsNone(self.store.user_for_token(token))
        self.assertEqual(self.store.login('student', 'pw')['role'], 'student')

    def test_student_and_teacher_can_change_own_password_after_verifying_old_password(self):
        self.store.signup('student', 'student-old', 'Student')
        self.store.signup('teacher', 'teacher-old', 'Teacher', teacher_code='fitshare')
        tokens = {username: self.store.create_session(username) for username in ('student', 'teacher')}

        self.store.change_own_password('student', 'student-old', 'student-new')
        self.store.change_own_password('teacher', 'teacher-old', 'teacher-new')

        for username, role, new_password, old_password in (
            ('student', 'student', 'student-new', 'student-old'),
            ('teacher', 'teacher', 'teacher-new', 'teacher-old'),
        ):
            self.assertIsNone(self.store.user_for_token(tokens[username]))
            with self.assertRaises(AuthError):
                self.store.login(username, old_password)
            self.assertEqual(self.store.login(username, new_password)['role'], role)

    def test_own_password_change_rejects_wrong_current_and_invalid_new_password(self):
        self.store.signup('student', 'correct-old', 'Student')
        token = self.store.create_session('student')

        with self.assertRaisesRegex(AuthError, '현재 비밀번호'):
            self.store.change_own_password('student', 'wrong-old', 'new-password')
        for password in (' ', 'x' * 201):
            with self.assertRaises(AuthError):
                self.store.change_own_password('student', 'correct-old', password)

        self.assertIsNotNone(self.store.user_for_token(token))
        self.assertEqual(self.store.login('student', 'correct-old')['role'], 'student')


if __name__ == '__main__':
    unittest.main()
