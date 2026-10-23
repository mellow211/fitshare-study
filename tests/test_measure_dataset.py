import unittest
from pathlib import Path

import numpy as np

from tools.measure_dataset import analyze_dataset, measure_garment, read_dataset_label, rectify_photo
from tools.vision_pipeline import _component_mask, refine_hem_bottom


ROOT = Path(__file__).resolve().parents[1]


class DatasetLabelTests(unittest.TestCase):
    def test_filename_labels_define_background_garment_and_reference_units(self):
        label = read_dataset_label(ROOT / 'measure_dataset' / 'point' / 'p_1_w55_h65.jpg')

        self.assertEqual(label['backgroundId'], 'point')
        self.assertEqual(label['garmentId'], 1)
        self.assertEqual(label['referenceWidthCm'], 55)
        self.assertEqual(label['referenceLengthCm'], 65)
        self.assertEqual(label['widthKind'], 'chest')

    def test_pants_use_waist_width_and_other_garments_use_chest_width(self):
        pants = read_dataset_label(ROOT / 'measure_dataset' / 'cross' / 'c_5_w42_h90.jpg')
        crop_top = read_dataset_label(ROOT / 'measure_dataset' / 'point' / 'p_2_w54_h50.jpg')

        self.assertEqual(pants['widthKind'], 'waist')
        self.assertEqual(crop_top['widthKind'], 'chest')
        self.assertEqual(pants['referenceWidthCm'], 42)
        self.assertEqual(pants['referenceLengthCm'], 90)

    def test_unknown_or_malformed_dataset_filename_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '파일명'):
            read_dataset_label(ROOT / 'measure_dataset' / 'point' / 'not-a-measurement.jpg')

    def test_folder_and_filename_must_name_the_same_background(self):
        wrong_folder = ROOT / 'measure_dataset' / 'cross' / 'p_1_w55_h65.jpg'

        with self.assertRaisesRegex(ValueError, '서로 달라요'):
            read_dataset_label(wrong_folder)

    def test_dataset_has_one_labeled_photo_for_each_garment_and_background(self):
        image_paths = sorted((ROOT / 'measure_dataset').rglob('*.jpg'))
        labels = [read_dataset_label(path) for path in image_paths]
        pairs = {(row['backgroundId'], row['garmentId']) for row in labels}

        self.assertEqual(len(labels), 20)
        self.assertEqual(len(pairs), 20)
        self.assertEqual({row['backgroundId'] for row in labels}, {'point', 'cross', 'ChArUco', 'ChaArUco_Cross'})


class DatasetVisionTests(unittest.TestCase):
    def test_shadow_connected_below_hem_is_trimmed_when_edge_is_clear(self):
        image = np.full((120, 80, 3), 220, dtype=np.uint8)
        image[20:82, 15:65] = 30
        image[82:89, 15:65] = 110
        mask = np.zeros((120, 80), dtype=np.uint8)
        mask[20:89, 15:65] = 1

        hem = refine_hem_bottom(image, mask, top=20, bottom=88)

        self.assertIn(hem, (81, 82))

    def test_weak_hem_edge_keeps_the_original_endpoint(self):
        image = np.full((120, 80, 3), 80, dtype=np.uint8)
        mask = np.zeros((120, 80), dtype=np.uint8)
        mask[20:89, 15:65] = 1

        hem = refine_hem_bottom(image, mask, top=20, bottom=88)

        self.assertEqual(hem, 88)

    def test_grabcut_keeps_seeded_garment_and_discards_calibration_mat(self):
        source = ROOT / 'measure_dataset' / 'cross' / 'c_5_w42_h90.jpg'
        flattened = rectify_photo(source, 'cross')
        self.assertTrue(flattened['ok'], flattened.get('message'))
        working, mask, _, _ = _component_mask(flattened['image'], garment_id=5)

        height, width = mask.shape
        self.assertEqual(mask[int(height * 0.32), int(width * 0.35)], 1, '왼쪽 바지 다리를 옷으로 남겨야 해요')
        self.assertEqual(mask[int(height * 0.32), int(width * 0.65)], 1, '오른쪽 바지 다리를 옷으로 남겨야 해요')
        self.assertEqual(mask[int(height * 0.10), int(width * 0.05)], 0, '매트 모서리는 옷 마스크에서 빠져야 해요')

    def test_quad_charuco_pants_waist_width_stays_near_the_reference(self):
        source = ROOT / 'measure_dataset' / 'ChaArUco_Cross' / 'chc_5_w42_h90.jpg'
        flattened = rectify_photo(source, 'ChaArUco_Cross')
        measured = measure_garment(flattened['image'], garment_id=5,
                                   scale_cm_per_pixel=flattened['scaleCmPerPixel'])

        self.assertLessEqual(abs(measured['measuredWidthCm'] - 42), 8)

    def test_point_mat_crop_shirt_includes_the_sleeves_in_chest_width(self):
        source = ROOT / 'measure_dataset' / 'point' / 'p_2_w54_h50.jpg'
        flattened = rectify_photo(source, 'point')
        measured = measure_garment(flattened['image'], garment_id=2,
                                   scale_cm_per_pixel=flattened['scaleCmPerPixel'])

        self.assertLessEqual(abs(measured['measuredWidthCm'] - 54), 8)

    def test_point_mat_crop_shirt_does_not_extend_into_the_mat_for_length(self):
        source = ROOT / 'measure_dataset' / 'point' / 'p_2_w54_h50.jpg'
        flattened = rectify_photo(source, 'point')
        measured = measure_garment(flattened['image'], garment_id=2,
                                   scale_cm_per_pixel=flattened['scaleCmPerPixel'])

        self.assertLessEqual(abs(measured['measuredLengthCm'] - 50), 8)

    def test_every_mat_family_is_flattened_to_the_same_a0_canvas(self):
        examples = [
            ('point', 'p_1_w55_h65.jpg'),
            ('cross', 'c_1_w55_h65.jpg'),
            ('ChArUco', 'ch_1_w55_h65.jpg'),
            ('ChaArUco_Cross', 'chc_1_w55_h65.jpg'),
        ]

        for background_id, filename in examples:
            with self.subTest(background=background_id):
                image_path = ROOT / 'measure_dataset' / background_id / filename
                result = rectify_photo(image_path, background_id)

                self.assertTrue(result['ok'], result.get('message'))
                self.assertEqual(result['image'].shape[:2], (848, 600))
                self.assertGreaterEqual(result['confidence'], 0.4)
                if background_id == 'point':
                    # 점 매트는 보정 뒤에도 여러 점이 화면 전체에 보여야 한다.
                    import cv2
                    import numpy as np

                    gray = cv2.cvtColor(result['image'], cv2.COLOR_BGR2GRAY)
                    _, dark = cv2.threshold(gray, 70, 255, cv2.THRESH_BINARY_INV)
                    count, _, stats, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
                    dot_count = sum(1 for area in stats[1:, cv2.CC_STAT_AREA] if 12 <= area <= 120)
                    self.assertGreaterEqual(dot_count, 30, f'점 매트 전체가 펴지지 않았어요: {dot_count}점')

    def test_all_twenty_photos_return_kind_specific_measurements_and_errors(self):
        report = analyze_dataset(ROOT / 'measure_dataset', ROOT / 'server-data' / 'test-measure-report')

        self.assertEqual(report['summary']['imageCount'], 20)
        self.assertEqual(report['summary']['successCount'], 20)
        self.assertEqual(len(report['rows']), 20)
        # This reproducible GrabCut baseline measures 3.8cm width and 5.2cm
        # length MAE on the checked-in 20-photo dataset. Keep a small margin
        # for OpenCV rounding while still catching material regressions.
        self.assertLessEqual(report['summary']['maeWidthCm'], 4.0)
        self.assertLessEqual(report['summary']['maeLengthCm'], 5.5)
        for row in report['rows']:
            self.assertEqual(row['status'], 'done', row)
            self.assertGreater(row['measuredWidthCm'], 0)
            self.assertGreater(row['measuredLengthCm'], 0)
            self.assertIn(row['widthKind'], {'chest', 'waist'})
            self.assertGreaterEqual(row['absoluteWidthErrorCm'], 0)
            self.assertGreaterEqual(row['absoluteLengthErrorCm'], 0)

    def test_bad_image_does_not_crash_the_full_dataset_run(self):
        report = analyze_dataset(ROOT / 'missing-measure-dataset', ROOT / 'server-data' / 'test-measure-report')

        self.assertEqual(report['summary']['imageCount'], 0)
        self.assertEqual(report['rows'], [])


if __name__ == '__main__':
    unittest.main()

