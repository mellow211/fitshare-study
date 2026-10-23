import unittest
from html.parser import HTMLParser
from pathlib import Path


DOCS = Path(__file__).resolve().parents[1] / 'docs'
LANDING_PAGE = DOCS / 'index.html'
PAGE = DOCS / 'clothing-size-research.html'


class HomeParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.buttons = []
        self.ids = set()
        self.controls = set()
        self.hidden = []
        self.image_alts = []
        self.links = []
        self.active_link = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id'):
            self.ids.add(attrs['id'])
        if attrs.get('aria-controls'):
            self.controls.update(attrs['aria-controls'].split())
        if tag == 'button' and attrs.get('data-story-target'):
            self.buttons.append(attrs['data-story-target'].lstrip('#'))
        if attrs.get('hidden') is not None:
            self.hidden.append(attrs.get('id'))
        if tag == 'img':
            self.image_alts.append(attrs.get('alt', ''))
        if tag == 'a':
            self.links.append({'href': attrs.get('href'), 'label': attrs.get('aria-label'), 'text': ''})
            self.active_link = len(self.links) - 1

    def handle_data(self, data):
        if self.active_link is not None:
            self.links[self.active_link]['text'] += data

    def handle_endtag(self, tag):
        if tag == 'a':
            self.active_link = None


class ExperimentStoryHomeTests(unittest.TestCase):
    def test_home_reads_like_a_childs_question_and_experiment_log(self):
        html = PAGE.read_text(encoding='utf-8')
        parser = HomeParser()
        parser.feed(html)

        for phrase in ('왜 그럴까?', '내 생각', '바꿔 본 것', '알게 된 점', '다음 질문'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, html)
        self.assertGreaterEqual(len(parser.buttons), 3)
        self.assertEqual(len(parser.buttons), len(set(parser.buttons)))
        self.assertTrue(parser.controls.issubset(parser.ids))
        self.assertEqual(len(parser.buttons), len(parser.controls))

    def test_story_links_to_real_experiments_and_generated_art(self):
        html = PAGE.read_text(encoding='utf-8')
        parser = HomeParser()
        parser.feed(html)

        for path in (
            'measure-dataset.html', 'measure-dataset-round4.html',
            'measure-dataset-round5.html', 'measure-dataset-round8.html',
            'measurement-process.html#ratio-example',
            'assets/measurement-story-hero.png',
        ):
            with self.subTest(path=path):
                self.assertIn(path, html)
        self.assertTrue(any(alt.strip() for alt in parser.image_alts))
        self.assertIn('848px', html)
        self.assertIn('20장', html)
        self.assertIn('한 가지씩', html)

    def test_home_has_learning_path_buttons_with_working_destinations(self):
        html = LANDING_PAGE.read_text(encoding='utf-8')
        parser = HomeParser()
        parser.feed(html)
        links = {link['label']: link['href'] for link in parser.links if link['label']}

        expected_paths = {
            '옷 크기 재기 연구노트': 'clothing-size-research.html',
            '미리 입어 보기 (가상 입어보기)': '가상입어보기-개선보고서.html',
            '잘 어울리는 옷 추천': '추천기능-학습과발표.html',
            'FitShare 서버는 어떻게 일할까요?': 'server-explained.html',
        }
        for label, destination in expected_paths.items():
            with self.subTest(label=label):
                self.assertEqual(links.get(label), destination)
                if destination.startswith('#'):
                    self.assertIn(destination[1:], parser.ids)
                else:
                    self.assertTrue((LANDING_PAGE.parent / destination).is_file())

    def test_server_page_has_resolvable_local_resources_and_navigation(self):
        page = DOCS / 'server-explained.html'
        self.assertTrue(page.is_file(), 'The server topic must open a real page')
        parser = HomeParser()
        html = page.read_text(encoding='utf-8')
        parser.feed(html)
        self.assertTrue(any(link['href'] == 'index.html' for link in parser.links))
        for link in parser.links:
            href = link['href'] or ''
            if href.startswith('#'):
                self.assertIn(href[1:], parser.ids)
        resources = []

        class ResourceParser(HTMLParser):
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'img':
                    resources.append(attrs['src'])
                    self_test.assertTrue(attrs.get('alt', '').strip())
                if tag == 'link' and attrs.get('rel') == 'stylesheet':
                    resources.append(attrs['href'])
        self_test = self
        ResourceParser().feed(html)
        self.assertGreaterEqual(len(parser.image_alts), 2)
        for resource in resources:
            with self.subTest(resource=resource):
                self.assertNotIn('://', resource, 'Reading the guide must not need external resources')
                self.assertTrue((DOCS / resource).is_file())

    def test_landing_page_is_a_standalone_topic_selector(self):
        html = LANDING_PAGE.read_text(encoding='utf-8')

        self.assertIn('궁금한 주제를 골라 봐요', html)
        self.assertIn('어디부터 볼까요?', html)
        self.assertNotIn('질문 하나, 실험 하나씩', html)
        self.assertNotIn('id="탐구일지"', html)


if __name__ == '__main__':
    unittest.main()
