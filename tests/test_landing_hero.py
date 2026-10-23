import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LandingHeroTests(unittest.TestCase):
    def test_ranking_and_recommendation_previews_open_their_tabs(self):
        page = (ROOT / 'index.html').read_text(encoding='utf-8')
        app = (ROOT / 'js' / 'app.js').read_text(encoding='utf-8')
        hero = page.split('<section class="hero"', 1)[1].split('<section id="school-closet"', 1)[0]
        self.assertIn('data-open-tab="ranking"', hero)
        self.assertIn('data-open-tab="recommendation"', hero)
        self.assertIn('selectTab(link.dataset.openTab)', app)

    def test_hero_pairs_ranking_with_a_vertical_garment_preview(self):
        page = (ROOT / 'index.html').read_text(encoding='utf-8')
        style = (ROOT / 'css/styles.css').read_text(encoding='utf-8')
        hero = page.split('<section class="hero"', 1)[1].split('<section id="school-closet"', 1)[0]
        self.assertIn('hero-ranking-preview', hero)
        self.assertIn('hero-garment-preview', hero)
        self.assertIn('hero-garment-list', hero)
        self.assertIn('세로로 나열한 추천 옷', hero)
        self.assertIn('.hero-garment-list { display:grid;', style)
        self.assertIn('.hero-ranking-preview', style)
        self.assertIn('.hero-garment-preview', style)
        self.assertIn('@media(max-width:700px)', style)

    def test_showcase_uses_live_ranking_and_recommendation_data(self):
        page = (ROOT / 'index.html').read_text(encoding='utf-8')
        app = (ROOT / 'js' / 'app.js').read_text(encoding='utf-8')
        showcase = (ROOT / 'js' / 'landingShowcase.js').read_text(encoding='utf-8')
        hero = page.split('<section class="hero"', 1)[1].split('<section id="school-closet"', 1)[0]

        self.assertIn('id="hero-rank-list"', hero)
        self.assertIn('id="hero-garment-list"', hero)
        self.assertNotIn('<li class="is-first">', hero)
        self.assertNotIn('<article class="hero-garment-item">', hero)
        self.assertIn('initializeLandingShowcase()', app)
        self.assertIn('computeRanking', showcase)
        self.assertIn('getRecommendationResults', showcase)
        self.assertIn("fetch('/api/garments'", showcase)


if __name__ == '__main__':
    unittest.main()
