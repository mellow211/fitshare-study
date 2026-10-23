import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RegistrationPageTests(unittest.TestCase):
    def test_saved_garment_is_refreshed_to_the_first_registration_list_position(self):
        registration = (ROOT / 'js' / 'measurement' / 'register.js').read_text(encoding='utf-8')
        closet = (ROOT / 'js' / 'measurement' / 'garmentCloset.js').read_text(encoding='utf-8')

        self.assertIn('await loadGarments(saved.id);', registration)
        self.assertIn('async function loadGarments(prioritizeId)', registration)
        self.assertIn('reload(prioritizeId)', registration)
        self.assertIn('async function reload(prioritizeId = null)', closet)
        self.assertIn('if (prioritizeId && a.garment.id === prioritizeId) return -1;', closet)
        self.assertIn('if (prioritizeId && b.garment.id === prioritizeId) return 1;', closet)
        self.assertIn('register.js?v=progress-1', (ROOT / 'measure' / 'register.html').read_text(encoding='utf-8'))

    def test_garment_type_two_is_labeled_as_tshirt_in_registration_and_catalog(self):
        registration = (ROOT / 'measure' / 'register.html').read_text(encoding='utf-8')
        closet = (ROOT / 'js' / 'measurement' / 'garmentCloset.js').read_text(encoding='utf-8')
        server = (ROOT / 'server.py').read_text(encoding='utf-8')

        self.assertIn('<option value="2">2. 티셔츠</option>', registration)
        self.assertNotIn('<option value="2">2. 크롭 티셔츠</option>', registration)
        self.assertIn("{ id: 2, name: '티셔츠' }", closet)
        self.assertIn("2: '티셔츠'", server)

    def test_closet_thumbnail_priority_is_product_then_yolo_sam_cutout(self):
        closet = (ROOT / 'js' / 'measurement' / 'garmentCloset.js').read_text(encoding='utf-8')
        detail = (ROOT / 'modules' / 'garmentDetail.js').read_text(encoding='utf-8')
        product_first = '/thumbnail?v=product-yolo-priority-3'
        closet_cutout = '/closet-thumbnail?v=yolo-sam-v3'
        self.assertIn(product_first, closet)
        self.assertLess(detail.index(product_first), detail.index(closet_cutout))
        app = (ROOT / 'index.html').read_text(encoding='utf-8')
        self.assertIn('app.js?v=garment-codes-3', app)

    def test_school_closet_offers_available_and_all_garment_filters(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        closet = (ROOT / 'js/measurement/garmentCloset.js').read_text(encoding='utf-8')
        self.assertIn('data-closet-filter="available"', html)
        self.assertIn('data-closet-filter="all"', html)
        self.assertIn('filterControls', closet)
        self.assertIn("getShareStatus(garment).key === 'available'", closet)
        self.assertIn('aria-pressed', closet)

    def test_registration_type_list_includes_skirts(self):
        html = (ROOT / 'measure' / 'register.html').read_text(encoding='utf-8')
        self.assertIn('<option value="6">6. 치마</option>', html)
        self.assertIn('<option value="7">7. 기타</option>', html)

    def test_registration_carries_the_selected_school_code(self):
        app = (ROOT / 'js' / 'app.js').read_text(encoding='utf-8')
        registration = (ROOT / 'js' / 'measurement' / 'register.js').read_text(encoding='utf-8')
        self.assertIn("localStorage.setItem('fitshare.schoolCode', result.code)", app)
        self.assertIn("localStorage.getItem('fitshare.schoolCode')", registration)
        self.assertIn('schoolCode,', registration)
        self.assertIn('Number(garmentTypeInput.value) === 7', registration)
        self.assertIn('analyzeButton.disabled = Number(garmentTypeInput.value) === 7', registration)
        self.assertIn("if (garmentId === 7) return 'other'", registration)

    def test_closet_and_teacher_handoff_show_the_requested_garment_codes(self):
        closet = (ROOT / 'js' / 'measurement' / 'garmentCloset.js').read_text(encoding='utf-8')
        today = (ROOT / 'modules' / 'todaySharing.js').read_text(encoding='utf-8')
        self.assertIn('garment.garmentTypeCode', closet)
        self.assertIn('garment.garmentNumber', closet)
        self.assertIn('garment?.garmentCode', today)
        self.assertIn('garment?.donorId', today)

    def test_camera_action_opens_video_instead_of_only_a_file_picker(self):
        from html.parser import HTMLParser

        class Elements(HTMLParser):
            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                if 'id' in values:
                    self.items[values['id']] = (tag, values)

        parser = Elements()
        parser.items = {}
        parser.feed((ROOT / 'measure' / 'register.html').read_text(encoding='utf-8'))
        self.assertIn('open-camera', parser.items, '촬영 버튼이 파일 선택창에만 연결되어 있습니다.')
        self.assertEqual(parser.items['open-camera'][0], 'button')
        self.assertEqual(parser.items['camera-video'][0], 'video')
        self.assertIn('playsinline', parser.items['camera-video'][1])
        self.assertIn('hidden', parser.items['camera-panel'][1])
        self.assertEqual(parser.items['gallery-photo'][1]['type'], 'file')

    def test_gallery_picker_has_explicit_tablet_friendly_button(self):
        from html.parser import HTMLParser

        class Elements(HTMLParser):
            def __init__(self):
                super().__init__()
                self.items = {}

            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                if 'id' in values:
                    self.items[values['id']] = (tag, values)

        parser = Elements()
        parser.feed((ROOT / 'measure' / 'register.html').read_text(encoding='utf-8'))
        registration = (ROOT / 'js' / 'measurement' / 'register.js').read_text(encoding='utf-8')
        self.assertEqual(parser.items['choose-gallery-photo'][0], 'button')
        self.assertEqual(parser.items['gallery-photo'][0], 'input')
        self.assertIn("'#choose-gallery-photo'", registration)

    def test_gallery_button_opens_native_file_picker_from_user_tap(self):
        registration = (ROOT / 'js' / 'measurement' / 'register.js').read_text(encoding='utf-8')
        self.assertIn("document.querySelector('#choose-gallery-photo').addEventListener('click'", registration)
        self.assertIn('galleryInput.click()', registration)

    def test_cancelled_gallery_picker_does_not_process_an_empty_selection(self):
        registration = (ROOT / 'js' / 'measurement' / 'register.js').read_text(encoding='utf-8')
        self.assertIn('if (!selectedFile) return;', registration)

    def test_gallery_picker_resets_input_to_allow_reselecting_same_photo(self):
        registration = (ROOT / 'js' / 'measurement' / 'register.js').read_text(encoding='utf-8')
        self.assertIn("input.value = ''", registration)
        self.assertIn('const selectedFile = input.files?.[0]', registration)

    def test_school_code_is_prefilled_and_selected_for_easy_replacement(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        app = (ROOT / 'js' / 'app.js').read_text(encoding='utf-8')

        self.assertIn('id="school-code"', html)
        self.assertIn('value="나눔초등학교"', html)
        self.assertIn('placeholder="예: 나눔초등학교"', html)
        self.assertIn("addEventListener('focus', () => schoolInput.select())", app)
        self.assertIn("addEventListener('click', () => schoolInput.select())", app)

    def test_school_entry_shows_a_direct_registration_action(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        app = (ROOT / 'js' / 'app.js').read_text(encoding='utf-8')
        module = (ROOT / 'modules' / 'measurement.js').read_text(encoding='utf-8')

        self.assertIn('id="entry-action"', html)
        self.assertIn('href="measure/register.html"', html)
        self.assertIn('createSchoolEntryResult(input.value)', app)
        self.assertIn('action.hidden = !result.ok', app)
        self.assertNotIn('scrollIntoView', app)
        self.assertIn('href="measure/register.html"', module)
        self.assertIn('href="measure/index.html"', module)

    def test_registration_screen_offers_four_backgrounds_with_m4_selected(self):
        html = (ROOT / 'measure' / 'register.html').read_text(encoding='utf-8')
        script = (ROOT / 'js' / 'measurement' / 'register.js').read_text(encoding='utf-8')
        photo_helper = (ROOT / 'js' / 'measurement' / 'registrationPhoto.js').read_text(encoding='utf-8')

        self.assertIn('id="background-id"', html)
        for mat_id in ('M1', 'M2', 'M3', 'M4'):
            self.assertIn(f'value="{mat_id}"', html)
        self.assertIn('<option value="M4" selected>', html)
        self.assertIn('value="male"', html)
        self.assertIn('value="female"', html)
        self.assertIn('value="unisex"', html)
        self.assertIn('value="unknown"', html)
        self.assertIn('id="garment-type"', html)
        self.assertIn('backgroundId: backgroundInput.value', script)
        self.assertIn('readRegistrationPhoto(file)', script)
        self.assertIn('if (jpegFile)', photo_helper)
        self.assertIn('resolve(reader.result)', photo_helper)
        self.assertIn('image.naturalWidth || image.width', photo_helper)
        self.assertIn('canvas.toDataURL(\'image/jpeg\', 1)', photo_helper)
        self.assertIn('file.size > maxBytes', photo_helper)
        self.assertIn('chosen-garment-type', script)
        self.assertIn("/api/garments'", script)

    def test_both_closet_pages_use_fast_paged_cards_with_edit_and_photo_toggle(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        register_html = (ROOT / 'measure' / 'register.html').read_text(encoding='utf-8')
        closet = (ROOT / 'js' / 'measurement' / 'garmentCloset.js').read_text(encoding='utf-8')
        app = (ROOT / 'js' / 'app.js').read_text(encoding='utf-8')
        registration = (ROOT / 'js' / 'measurement' / 'register.js').read_text(encoding='utf-8')

        self.assertIn('school-closet-pages', html)
        self.assertIn('garment-list-pages', register_html)
        self.assertIn('PAGE_SIZE = 12', closet)
        self.assertIn('Date.parse(garment.createdAt)', closet)
        self.assertIn('return a.createdAt - b.createdAt', closet)
        css = (ROOT / 'css' / 'measurement.css').read_text(encoding='utf-8')
        self.assertIn('grid-template-columns:repeat(4,minmax(0,1fr))', css)
        self.assertIn('grid-template-columns:repeat(6,minmax(0,1fr))', css)
        self.assertIn('grid-template-columns:repeat(3,minmax(0,1fr))', css)
        self.assertIn('grid-template-columns:repeat(2,minmax(0,1fr))', css)
        self.assertIn('loading = \'lazy\'', closet)
        self.assertIn('closet-estimated-size', closet)
        self.assertIn('estimatedSize.textContent = `추정 호수 ${/^', closet)
        self.assertIn("/^\\d{2,3}$/.test(savedSize)", closet)
        self.assertIn('배경 포함 사진 보기', closet)
        self.assertIn('정보 수정', closet)
        self.assertIn('unknown', closet)
        self.assertIn('createGarmentCloset', app)
        self.assertIn('createGarmentCloset', registration)
        self.assertIn('.closet-card-actions .closet-open-action { grid-column:1 / -1;', css)

if __name__ == '__main__':
    unittest.main()
