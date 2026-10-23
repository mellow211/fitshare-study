import unittest
import json
from pathlib import Path

from tools.garment_size_estimation import estimate_korean_size
from tools.complete_legacy_metadata import complete
from tools.garment_metadata import CHOICES


class LegacySizeEstimationTests(unittest.TestCase):
    def test_uses_garment_type_and_both_measured_dimensions(self):
        self.assertEqual(estimate_korean_size('top', 53.8, 46.8), '110')
        self.assertEqual(estimate_korean_size('dress', 37.2, 69.4), '130')
        self.assertEqual(estimate_korean_size('pants', 47.3, 96.5), '160')
        self.assertEqual(estimate_korean_size('skirt', 46.4, 48.1), '160')
        self.assertEqual(estimate_korean_size('shorts', 45.5, 47.3), '160')

    def test_rejects_missing_or_unsupported_measurements(self):
        for args in (('top', None, 50), ('top', 30, 0), ('top', float('inf'), 50),
                     ('other', 30, 50)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                estimate_korean_size(*args)

    def test_completes_only_missing_size_and_records_estimation_source(self):
        item = {'category': 'top', 'lengthCm': 46.8, 'widthCm': 53.8,
                'size': '', 'metadataSources': {'style': 'manual'}}
        result = complete(item)
        self.assertEqual(result['size'], '110')
        self.assertEqual(result['metadataSources']['size'], 'measurement-rule')
        self.assertEqual(result['metadataSources']['style'], 'manual')
        self.assertEqual(complete({**item, 'size': '140'})['size'], '140')

    def test_completes_skirt_size_from_its_measured_dimensions(self):
        result = complete({'category': 'skirt', 'lengthCm': 48.1, 'widthCm': 46.4})

        self.assertEqual(result['size'], '160')
        self.assertEqual(result['metadataSources']['size'], 'measurement-rule')

    def test_completes_short_pants_size_using_short_length_references(self):
        result = complete({'category': 'pants', 'bottomLength': '\ubc18\ubc14\uc9c0',
                           'lengthCm': 47.3, 'widthCm': 45.5})

        self.assertEqual(result['size'], '160')
        self.assertEqual(result['metadataSources']['size'], 'measurement-rule')

    def test_saved_direct_garments_have_supported_reference_estimates(self):
        root = Path(__file__).resolve().parents[1]
        for folder in ('server-data/garments', 'server-data/nanum-demo/garments'):
            records = json.loads((root / folder / 'index.json').read_text(encoding='utf-8'))
            direct = [item for item in records if not item.get('isDemo')]
            self.assertGreaterEqual(len(direct), 6)
            for item in direct:
                self.assertIn(item['size'], CHOICES['size'])
                self.assertEqual(item['metadataSources']['size'], 'measurement-rule')


if __name__ == '__main__':
    unittest.main()
