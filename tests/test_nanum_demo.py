import json
import csv
import tempfile
import unittest
from pathlib import Path

from tools.accounts import AccountStore
from tools.seed_nanum_demo import seed_demo


REALISTIC_STUDENT_NAMES = [
    '김서윤', '이도윤', '박하은', '최서준', '정지우',
    '강서아', '조이준', '윤하린', '장도현', '임지안',
    '한예준', '오수아', '서준우', '신채원', '권민준',
    '황다은', '안유나', '송시우', '류하준', '전가은'
]


class NanumDemoSeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.destination = self.root / 'nanum-demo'
        garments = self.source / 'garments'
        garments.mkdir(parents=True)
        records = []
        for number in range(20):
            garment_id = f'garment-{number + 1}'
            folder = garments / garment_id
            folder.mkdir()
            item = {'id': garment_id, 'name': f'상의 {number + 1}', 'category': 'top',
                    'garmentTypeId': 1, 'garmentTypeName': '상의', 'ownerName': '기존 체험 옷장',
                    'photoUrl': f'/api/garments/{garment_id}/photo', 'lengthCm': 40, 'widthCm': 30}
            (folder / 'garment.json').write_text(json.dumps(item, ensure_ascii=False), encoding='utf-8')
            (folder / 'photo.jpg').write_bytes(b'original-photo')
            records.append(item)
        (garments / 'index.json').write_text(json.dumps(records, ensure_ascii=False), encoding='utf-8')
        (self.source / 'accounts.json').write_text('{"keep":"original"}', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_seeds_twenty_accounts_all_garments_and_mutual_ranking_records(self):
        summary = seed_demo(self.source, self.destination)

        self.assertEqual(summary['students'], 20)
        self.assertEqual(summary['garments'], 20)
        accounts = json.loads((self.destination / 'accounts.json').read_text(encoding='utf-8'))
        self.assertEqual(len(accounts), 20)
        with (self.destination / 'demo-accounts.csv').open(encoding='utf-8-sig', newline='') as stream:
            credentials = list(csv.DictReader(stream))
        self.assertEqual(len(credentials), 20)
        self.assertEqual([credential['name'] for credential in credentials], REALISTIC_STUDENT_NAMES)
        store = AccountStore(self.destination)
        for credential in credentials:
            number = int(credential['username'][1:])
            self.assertEqual(credential['username'], f's{number}')
            self.assertEqual(credential['password'], '111')
            user = store.login(credential['username'], credential['password'])
            self.assertEqual(user['role'], 'student')
            self.assertEqual(user['name'], REALISTIC_STUDENT_NAMES[number - 1])
        items = json.loads((self.destination / 'garments' / 'index.json').read_text(encoding='utf-8'))
        self.assertEqual(len(items), 20)
        self.assertEqual({item['ownerUsername'] for item in items}, {f's{n}' for n in range(1, 21)})
        ranking = json.loads((self.destination / 'ranking.json').read_text(encoding='utf-8'))['state']
        self.assertEqual(len(ranking['users']), 20)
        self.assertEqual([user['name'] for user in ranking['users']], REALISTIC_STUDENT_NAMES)
        self.assertEqual(len(ranking['exchanges']), 20)
        self.assertTrue(all(exchange['status'] == 'completed' and exchange['revealedAt']
                            and exchange['giverToReceiver'] and exchange['receiverToGiver']
                            for exchange in ranking['exchanges']))
        self.assertTrue(all(exchange.get('isDemoReview') and exchange.get('review', {}).get('text')
                            and '\n' not in exchange['review']['text']
                            for exchange in ranking['exchanges']))
        self.assertEqual((self.source / 'accounts.json').read_text(encoding='utf-8'), '{"keep":"original"}')
        self.assertEqual((self.source / 'garments' / 'garment-1' / 'photo.jpg').read_bytes(), b'original-photo')

    def test_refuses_to_overwrite_an_existing_demo_directory(self):
        self.destination.mkdir()
        marker = self.destination / 'keep.txt'
        marker.write_text('keep', encoding='utf-8')

        with self.assertRaises(FileExistsError):
            seed_demo(self.source, self.destination)

        self.assertEqual(marker.read_text(encoding='utf-8'), 'keep')

    def test_allows_demo_under_data_root_but_not_inside_original_garments(self):
        summary = seed_demo(self.source, self.source / 'nanum-demo')
        self.assertEqual(summary['garments'], 20)
        with self.assertRaises(ValueError):
            seed_demo(self.source, self.source / 'garments' / 'nanum-demo')

    def test_demo_school_query_prefills_nanum_and_nanum_is_the_default(self):
        html = (Path(__file__).resolve().parents[1] / 'index.html').read_text(encoding='utf-8')
        app = (Path(__file__).resolve().parents[1] / 'js' / 'app.js').read_text(encoding='utf-8')
        self.assertIn('id="school-code" value="나눔초등학교"', html)
        self.assertIn("new URLSearchParams(window.location.search).get('school')", app)
        self.assertIn('schoolInput.value = requestedSchool.slice(0, 30)', app)

    def test_existing_demo_names_match_across_accounts_ranking_and_garments(self):
        demo = Path(__file__).resolve().parents[1] / 'server-data' / 'nanum-demo'
        with (demo / 'demo-accounts.csv').open(encoding='utf-8-sig', newline='') as stream:
            students = [row for row in csv.DictReader(stream) if row['username'].startswith('s')]
        self.assertEqual([row['name'] for row in students], REALISTIC_STUDENT_NAMES)

        accounts = json.loads((demo / 'accounts.json').read_text(encoding='utf-8'))
        self.assertEqual([accounts[f's{i}']['name'] for i in range(1, 21)], REALISTIC_STUDENT_NAMES)
        ranking = json.loads((demo / 'ranking.json').read_text(encoding='utf-8'))['state']
        ranked_students = [user for user in ranking['users'] if user['id'] != 'u21']
        self.assertEqual([user['name'] for user in ranked_students], REALISTIC_STUDENT_NAMES)
        self.assertEqual(next(user['name'] for user in ranking['users'] if user['id'] == 'u21'), '나눔선생님')
        garments = json.loads((demo / 'garments' / 'index.json').read_text(encoding='utf-8'))
        for garment in garments:
            number = int(garment['ownerUsername'][1:])
            self.assertEqual(garment['ownerName'], REALISTIC_STUDENT_NAMES[number - 1])

    def test_completed_demo_shares_have_one_line_virtual_reviews(self):
        demo = Path(__file__).resolve().parents[1] / 'server-data' / 'nanum-demo'
        ranking = json.loads((demo / 'ranking.json').read_text(encoding='utf-8'))['state']
        completed = [exchange for exchange in ranking['exchanges'] if exchange['status'] == 'completed']
        self.assertTrue(completed)
        for exchange in completed:
            self.assertTrue(exchange.get('isDemoReview'))
            self.assertTrue(exchange.get('review', {}).get('text'))
            self.assertLessEqual(len(exchange['review']['text']), 100)
            self.assertNotIn('\n', exchange['review']['text'])


if __name__ == '__main__':
    unittest.main()
