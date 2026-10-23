import unittest
from html.parser import HTMLParser
from pathlib import Path


PAGE = Path(__file__).resolve().parents[1] / 'docs' / 'measurement-process.html'


class MeasurementProcessPageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.headings = []
        self.current_heading = None
        self.code_blocks = 0
        self.code_comments = 0
        self.in_code = False
        self.token_classes = set()
        self.glossary_terms = 0
        self.glossary_definitions = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'h1', 'h2'}:
            self.current_heading = tag
            self.headings.append('')
        elif tag == 'pre':
            self.code_blocks += 1
        elif tag == 'code':
            self.in_code = True
        elif tag == 'span' and self.in_code:
            classes = dict(attrs).get('class', '').split()
            self.token_classes.update(name for name in classes if name.startswith('tok-'))
        elif tag == 'dt':
            self.glossary_terms += 1
        elif tag == 'dd':
            self.glossary_definitions += 1

    def handle_endtag(self, tag):
        if tag == self.current_heading:
            self.current_heading = None
        elif tag == 'code':
            self.in_code = False

    def handle_data(self, data):
        if self.current_heading:
            self.headings[-1] += data
        if self.in_code:
            self.code_comments += sum(
                line.lstrip().startswith('#') for line in data.splitlines()
            )


class MeasurementProcessPageTests(unittest.TestCase):
    def test_guide_is_reachable_from_the_published_docs_home(self):
        index = (PAGE.parent / 'index.html').read_text(encoding='utf-8')
        research = (PAGE.parent / 'clothing-size-research.html').read_text(encoding='utf-8')

        self.assertIn('href="clothing-size-research.html"', index)
        self.assertIn('href="measurement-process.html"', research)
        self.assertIn('id="ratio-example"', PAGE.read_text(encoding='utf-8'))

    def test_page_explains_the_full_flow_and_its_limits(self):
        html = PAGE.read_text(encoding='utf-8')
        parser = MeasurementProcessPageParser()
        parser.feed(html)
        parser.close()

        self.assertIn('사진 보정', html)
        self.assertIn('옷 영역', html)
        self.assertIn('cm', ' '.join(parser.headings))
        self.assertIn('겨드랑이 봉제선을 직접 찾지 않아요', html)
        self.assertIn('옷 주름을 자동으로 펴지 못해요', html)

    def test_code_examples_have_explanatory_comments_for_children(self):
        html = PAGE.read_text(encoding='utf-8')
        parser = MeasurementProcessPageParser()
        parser.feed(html)
        parser.close()

        self.assertGreaterEqual(parser.code_blocks, 3)
        self.assertGreaterEqual(parser.code_comments, 8)

    def test_code_tokens_are_colored_and_important_terms_are_explained(self):
        html = PAGE.read_text(encoding='utf-8')
        parser = MeasurementProcessPageParser()
        parser.feed(html)
        parser.close()

        for token_class in ('tok-variable', 'tok-function', 'tok-keyword', 'tok-comment'):
            with self.subTest(token_class=token_class):
                self.assertIn(token_class, parser.token_classes)
        self.assertGreaterEqual(parser.glossary_terms, 15)
        self.assertEqual(parser.glossary_terms, parser.glossary_definitions)

    def test_page_names_real_algorithms_without_a_browser_fallback_walkthrough(self):
        html = PAGE.read_text(encoding='utf-8')

        for term in ('warpPerspective', 'CIELAB', 'ArucoDetector'):
            with self.subTest(term=term):
                self.assertIn(term, html)

    def test_page_shows_illustrated_steps_and_a_worked_measurement_example(self):
        html = PAGE.read_text(encoding='utf-8')

        self.assertGreaterEqual(html.count('<svg'), 2)
        self.assertIn('aria-labelledby=', html)
        self.assertIn('예시로 따라 재기', html)
        self.assertIn('240px', html)
        self.assertIn('450px', html)
        self.assertIn('33.6cm', html)
        self.assertIn('63.1cm', html)
        self.assertEqual(round(240 * 84.1 / 600, 1), 33.6)
        self.assertEqual(round(450 * 118.9 / 848, 1), 63.1)

    def test_width_ratio_is_explained_as_a_fraction_of_the_detected_garment_height(self):
        html = PAGE.read_text(encoding='utf-8')

        self.assertIn('옷으로 고른 영역의 위쪽부터 아래쪽까지', html)
        self.assertIn('100px 높이의 40%는 40px', html)
        self.assertIn('겨드랑이 봉제선을 찾아 정한 숫자가 아니에요', html)
        self.assertIn('상의 외투</th><td>40%</td>', html)
        self.assertIn('흰 크롭티셔츠</th><td>32.5%</td>', html)

    def test_background_scale_explains_reference_board_and_pixel_conversion(self):
        html = PAGE.read_text(encoding='utf-8')

        self.assertIn('scale-example-title', html)
        self.assertIn('scale-example-desc', html)
        self.assertIn('기울어진 기준판', html)
        self.assertIn('실제 크기가 알려진 A0', html)
        self.assertIn('84.1cm', html)
        self.assertIn('118.9cm', html)
        self.assertIn('600px', html)
        self.assertIn('848px', html)
        self.assertIn('240px', html)
        self.assertIn('33.6cm', html)

    def test_kind_specific_width_ratios_have_an_illustrated_comparison(self):
        html = PAGE.read_text(encoding='utf-8')

        self.assertIn('kind-ratio-title', html)
        self.assertIn('kind-ratio-desc', html)
        for garment, ratio in (
            ('상의 외투', '40%'),
            ('흰 크롭티셔츠', '32.5%'),
            ('원피스', '15%'),
            ('점퍼', '20%'),
            ('바지', '10%'),
        ):
            with self.subTest(garment=garment):
                self.assertIn(garment, html)
                self.assertIn(ratio, html)
        self.assertIn('실제 봉제선을 찾아 정한 위치가 아니에요', html)

    def test_kind_ratio_illustration_uses_garment_silhouettes_not_plain_bars(self):
        html = PAGE.read_text(encoding='utf-8')

        for garment_shape in (
            'garment-shape-coat',
            'garment-shape-crop-top',
            'garment-shape-dress',
            'garment-shape-jumper',
            'garment-shape-pants',
        ):
            with self.subTest(garment_shape=garment_shape):
                self.assertIn(f'id="{garment_shape}"', html)
        self.assertIn('각 옷의 실제 모양을 보고 파란 선 위치를 비교해요', html)

    def test_browser_fallback_walkthrough_and_note_are_removed(self):
        html = PAGE.read_text(encoding='utf-8')

        self.assertNotIn('5. 서버를 못 쓰면 브라우저가 간단히 대신하기', html)
        self.assertNotIn('small.data', html)
        self.assertNotIn('참고: 서버 연결이 안 될 때', html)
        self.assertNotIn('앱이 자동으로 제한적인 브라우저 분석을 한 번 시도해요', html)
        self.assertNotIn('GrabCut', html)
        self.assertNotIn('경계 상자', html)

    def test_page_explains_korean_child_size_standards_and_their_scope(self):
        html = PAGE.read_text(encoding='utf-8')

        for term in ('KS K 0052', 'KS K 9402', 'KS K 9403', '만 7~12세', '2025년 9월 개정'):
            with self.subTest(term=term):
                self.assertIn(term, html)
        self.assertIn('옷의 길이와 같지 않아요', html)
        self.assertIn('https://www.kssn.net/search/stddetail.do?itemNo=K001010154267', html)
        self.assertIn('https://www.kssn.net/search/stddetail.do?itemNo=K001010123909', html)
        self.assertIn('https://www.kssn.net/search/stddetail.do?itemNo=K001010123918', html)

    def test_size_matcher_offers_korean_auto_mapping_and_manual_size_edit(self):
        html = PAGE.read_text(encoding='utf-8')

        self.assertIn('id="size-matcher"', html)
        self.assertIn('js/measurement-size-matcher.js', html)
        self.assertIn('data-garment-type', html)
        self.assertIn('data-auto-size', html)
        self.assertIn('치수를 입력하면', html)
        self.assertIn('직접 바꿀 수 있어요', html)
        self.assertIn('공식 KS 변환표나 실제 상품 치수 통계가 아니며', html)

    def test_size_matcher_omits_product_chart_and_keeps_manual_size_edit(self):
        html = PAGE.read_text(encoding='utf-8')

        self.assertNotIn('상품별 치수표', html)
        self.assertNotIn('data-chart-options', html)
        self.assertIn('data-measured-width', html)
        self.assertIn('data-measured-length', html)
        self.assertIn('data-auto-size', html)
        self.assertIn('aria-live="polite"', html)

    def test_code_links_include_github_pages_repository_path(self):
        html = PAGE.read_text(encoding='utf-8')

        self.assertIn('https://taehongkim.github.io/fitshare-study/tools/vision_pipeline.py', html)
        self.assertIn('https://taehongkim.github.io/fitshare-study/js/measurement/automaticMeasurement.js', html)
        self.assertIn('https://taehongkim.github.io/fitshare-study/js/measurement/calibration.js', html)


if __name__ == '__main__':
    unittest.main()

