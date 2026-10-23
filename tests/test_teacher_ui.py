import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TeacherUiTests(unittest.TestCase):
    def test_today_sharing_is_teacher_only_and_shows_handoff_details(self):
        page = (ROOT / 'index.html').read_text(encoding='utf-8')
        app = (ROOT / 'js/app.js').read_text(encoding='utf-8')
        sharing = (ROOT / 'modules/todaySharing.js').read_text(encoding='utf-8')
        server = (ROOT / 'server.py').read_text(encoding='utf-8')
        store = (ROOT / 'modules/rankingStore.js').read_text(encoding='utf-8')
        style = (ROOT / 'css/ranking.css').read_text(encoding='utf-8')
        self.assertIn('id="today-sharing-button"', page)
        self.assertIn("user?.role !== 'teacher'", app)
        self.assertIn('openTodaySharingDialog', app)
        self.assertIn("item.status === 'reserved'", sharing)
        self.assertIn('item.itemName', sharing)
        self.assertIn('users.get(item.giverId)', sharing)
        self.assertIn('users.get(item.receiverId)', sharing)
        self.assertIn('나눔 완료', sharing)
        self.assertIn('markTeacherHandoffShared', sharing)
        self.assertIn("if user.get('role') != 'teacher':", server)
        self.assertIn("parts == ['ranking', 'teacher-handoff']", server)
        self.assertIn("'/api/ranking/teacher-handoff'", store)
        self.assertIn('.today-sharing-card', style)

    def test_measured_size_estimates_are_labeled_as_estimates_in_detail(self):
        detail = (ROOT / 'modules/garmentDetail.js').read_text(encoding='utf-8')
        self.assertIn("garment.metadataSources?.size === 'measurement-rule'", detail)
        self.assertIn('호수는 실측값 기반 참고 추정이에요.', detail)

    def test_virtual_garments_show_a_badge_on_the_closet_card_and_detail(self):
        closet = (ROOT / 'js/measurement/garmentCloset.js').read_text(encoding='utf-8')
        detail = (ROOT / 'modules/garmentDetail.js').read_text(encoding='utf-8')
        closet_style = (ROOT / 'css/measurement.css').read_text(encoding='utf-8')
        detail_style = (ROOT / 'css/ranking.css').read_text(encoding='utf-8')
        self.assertIn("if (garment.isDemo)", closet)
        self.assertIn('closet-demo-badge', closet)
        self.assertIn("if (garment.isDemo)", detail)
        self.assertIn('garment-demo-badge', detail)
        self.assertIn('.closet-demo-badge', closet_style)
        self.assertIn('.garment-demo-badge', detail_style)

    def test_detail_photo_controls_explain_background_and_clothing_views(self):
        detail = (ROOT / 'modules/garmentDetail.js').read_text(encoding='utf-8')
        dress = (ROOT / 'server-data/nanum-demo/garments/3f55b205-6950-4cb1-bfb4-4be12aeed819/garment.json').read_text(encoding='utf-8')
        self.assertIn('"category": "dress"', dress)
        self.assertIn('\ubc30\uacbd \ud3ec\ud568 \uc0ac\uc9c4 \ubcf4\uae30', detail)
        self.assertIn('\uc637\ub9cc \ubcf4\uae30', detail)

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
        self.assertIn("if (currentUser?.role === 'teacher')", detail)
        self.assertIn('AI 상품 사진 만들기', detail)
        self.assertIn("/api/garments/${id}/photo", detail)

    def test_garment_delete_action_allows_owner_before_sharing_and_confirms(self):
        detail = (ROOT / 'modules/garmentDetail.js').read_text(encoding='utf-8')
        closet = (ROOT / 'js/measurement/garmentCloset.js').read_text(encoding='utf-8')
        server = (ROOT / 'server.py').read_text(encoding='utf-8')
        self.assertIn('isOwner && !hasExchangeHistory', detail)
        self.assertIn("if (currentUser?.role === 'teacher')", detail)
        self.assertLess(detail.index("if (currentUser?.role === 'teacher')"), detail.index('const productPhoto'))
        self.assertIn('hasExchangeHistory: sharingState.exchanges.some', closet)
        self.assertIn('owns_garment(user, garment)', server)
        self.assertIn("exchange.get('garmentId') == garment_id", server)
        self.assertIn('window.confirm', detail)
        self.assertIn("method: 'DELETE'", detail)
        self.assertIn('onClosetUpdated', detail)

    def test_completed_garment_history_shows_participants_and_only_revealed_scores(self):
        detail = (ROOT / 'modules/garmentDetail.js').read_text(encoding='utf-8')
        closet = (ROOT / 'js/measurement/garmentCloset.js').read_text(encoding='utf-8')
        self.assertIn('exchange: getGarmentExchange(sharingState, garment.id)', closet)
        self.assertIn('createExchangeHistory(garment, exchange, users)', detail)
        self.assertLess(detail.index('dialog.append(exchangeHistory)'), detail.index('dialog.append(image'))
        self.assertIn('else if (isRevealed(exchange))', detail)
        self.assertIn('exchange.giverToReceiver', detail)
        self.assertIn('exchange.receiverToGiver', detail)
        self.assertIn('둘 다 점수를 보내면 만족도 점수를 공개해요.', detail)

    def test_garment_detail_properties_use_star_cards_and_keep_measurements_clear(self):
        detail = (ROOT / 'modules/garmentDetail.js').read_text(encoding='utf-8')
        style = (ROOT / 'css/ranking.css').read_text(encoding='utf-8')
        self.assertIn('garment-detail-property', detail)
        self.assertIn('garment-detail-measurement', detail)
        self.assertIn('☆', detail)
        self.assertIn('.garment-detail-info { display:grid;', style)
        self.assertIn('.garment-detail-measurement', style)

    def test_completed_garment_popup_shows_labeled_review_and_star_satisfaction(self):
        detail = (ROOT / 'modules/garmentDetail.js').read_text(encoding='utf-8')
        style = (ROOT / 'css/ranking.css').read_text(encoding='utf-8')
        self.assertIn('exchange.review?.text', detail)
        self.assertIn('exchange.isDemoReview', detail)
        self.assertIn('체험용 가상 후기', detail)
        self.assertIn('garment-exchange-stars', detail)
        self.assertIn('.garment-exchange-review', style)

    def test_closet_cards_show_share_status_and_a_clear_reservation_action(self):
        closet = (ROOT / 'js/measurement/garmentCloset.js').read_text(encoding='utf-8')
        detail = (ROOT / 'modules/garmentDetail.js').read_text(encoding='utf-8')
        closet_style = (ROOT / 'css/measurement.css').read_text(encoding='utf-8')
        detail_style = (ROOT / 'css/ranking.css').read_text(encoding='utf-8')
        self.assertIn('getGarmentExchange', closet)
        self.assertIn('나눔 가능', closet)
        self.assertIn('예약 중', closet)
        self.assertIn('나눔 완료', closet)
        self.assertIn('data-closet-open', closet)
        self.assertIn('나눔 예약하기', detail)
        self.assertIn('.closet-share-status', closet_style)
        self.assertIn('.garment-detail-reserve', detail_style)

    def test_clicking_a_closet_garment_photo_opens_its_detail(self):
        closet = (ROOT / 'js/measurement/garmentCloset.js').read_text(encoding='utf-8')
        self.assertIn('closet-photo-open', closet)
        self.assertIn('imageAction.append(image)', closet)
        self.assertIn('imageAction.addEventListener(\'click\', openDetails)', closet)

    def test_teacher_role_uses_one_shared_auth_module_instance(self):
        auth_users = [
            'js/app.js', 'js/measurement/garmentCloset.js', 'js/measurement/register.js',
            'modules/garmentDetail.js', 'modules/profile.js'
        ]
        for relative_path in auth_users:
            source = (ROOT / relative_path).read_text(encoding='utf-8')
            self.assertNotIn('auth.js?v=auth-2', source, relative_path)
            if relative_path != 'js/app.js':
                self.assertIn('auth.js?v=auth-3', source, relative_path)

    def test_garment_detail_popup_buttons_share_dimensions_and_base_style(self):
        style = (ROOT / 'css/ranking.css').read_text(encoding='utf-8')
        self.assertIn('.garment-detail button {', style)
        self.assertIn('min-height:52px', style)
        self.assertIn('.garment-detail .rk-ghost {', style)
        self.assertIn('.garment-detail .garment-detail-reserve {', style)
        self.assertIn('.garment-detail .garment-detail-delete {', style)
        self.assertIn('.garment-detail-buttons { display:grid;', style)
        self.assertIn('grid-template-columns:repeat(2,minmax(0,1fr))', style)
        self.assertIn('width:100%; height:52px', style)
        self.assertIn('.garment-detail button[hidden] { display:none; }', style)
        self.assertIn("ranking.css?v=ranking-17", (ROOT / 'modules/garmentDetail.js').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
