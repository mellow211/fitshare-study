import unittest

import numpy as np

from tools.garment_segmentation import _category_classes
from tools.vision_pipeline import GARMENT_PROFILES, _component_mask


class GarmentCategoryTests(unittest.TestCase):
    def test_skirt_measurement_uses_waist_line_and_skirt_detection_classes(self):
        image = np.full((120, 80, 3), 180, dtype=np.uint8)
        mask = np.zeros((120, 80), dtype=np.uint8)
        mask[8:115, 20:60] = 1

        _, _, profile, width_kind = _component_mask(
            image, garment_id=6, category='skirt', mask_provider=lambda *_: mask
        )

        self.assertEqual(profile['name'], '치마')
        self.assertEqual(width_kind, 'waist')
        self.assertEqual(_category_classes(6, 'skirt'), {6, 7, 8})

    def test_shorts_detection_uses_the_deepfashion_shorts_class(self):
        self.assertEqual(_category_classes(5, 'shorts'), {6})
        self.assertEqual(_category_classes(5, 'pants'), {6, 7})


if __name__ == '__main__':
    unittest.main()
