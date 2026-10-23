import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TeacherUiTests(unittest.TestCase):
    def test_signup_has_student_default_and_teacher_code_input(self):
        page = (ROOT / 'index.html').read_text(encoding='utf-8')
        app = (ROOT / 'js/app.js').read_text(encoding='utf-8')
        auth = (ROOT / 'js/auth.js').read_text(encoding='utf-8')
        self.assertIn('<option value="student">학생</option>', page)
        self.assertIn('id="auth-teacher-code"', page)
        self.assertIn("document.querySelector('#auth-teacher-code-row').hidden = !teacher", app)
        self.assertIn('teacherCode', auth)

    def test_profile_shows_teacher_controls_only_for_teacher(self):
        profile = (ROOT / 'modules/profile.js').read_text(encoding='utf-8')
        self.assertIn("getUser()?.role === 'teacher'", profile)
        self.assertIn('data-account-action="teacher-code"', profile)
        self.assertIn('data-account-action="reset-student-password"', profile)
        self.assertIn('${teacher ?', profile)

    def test_profile_offers_own_password_change_for_both_roles(self):
        profile = (ROOT / 'modules/profile.js').read_text(encoding='utf-8')
        auth = (ROOT / 'js/auth.js').read_text(encoding='utf-8')
        app = (ROOT / 'js/app.js').read_text(encoding='utf-8')
        self.assertIn('data-account-action="change-own-password"', profile)
        self.assertIn('name="currentPassword"', profile)
        self.assertIn('name="newPassword"', profile)
        self.assertIn('changeOwnPassword', auth)
        self.assertIn('fitshare:auth-changed', app)

    def test_product_photo_generation_shows_accessible_indeterminate_progress(self):
        detail = (ROOT / 'modules/garmentDetail.js').read_text(encoding='utf-8')
        style = (ROOT / 'css/ranking.css').read_text(encoding='utf-8')
        self.assertIn('garment-detail-generation-progress', detail)
        self.assertIn('role', detail)
        self.assertIn('aria-valuetext', detail)
        self.assertIn("generationProgress.setAttribute('aria-valuetext'", detail)
        self.assertIn('is-generating', detail)
        self.assertIn('garment-detail-generation-progress', style)
        self.assertIn('prefers-reduced-motion', style)

    def test_product_photo_action_is_teacher_gated_and_original_is_available(self):
        detail = (ROOT / 'modules/garmentDetail.js').read_text(encoding='utf-8')
        self.assertIn("getUser()?.role === 'teacher'", detail)
        self.assertIn('AI 상품 사진 만들기', detail)
        self.assertIn("/api/garments/${id}/photo", detail)

    def test_garment_delete_action_is_teacher_only_and_confirms_before_deleting(self):
        detail = (ROOT / 'modules/garmentDetail.js').read_text(encoding='utf-8')
        self.assertIn("getUser()?.role === 'teacher'", detail)
        self.assertIn('옷 삭제', detail)
        self.assertIn('window.confirm', detail)
        self.assertIn('method: \'DELETE\'', detail)
        self.assertIn('onClosetUpdated', detail)


if __name__ == '__main__':
    unittest.main()
