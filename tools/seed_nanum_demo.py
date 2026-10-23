"""Create an isolated local Nanum Elementary demo from the saved garment closet."""
import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import secrets
import shutil

from tools.accounts import AccountStore


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / 'server-data'
DEFAULT_DESTINATION = ROOT / 'server-data' / 'nanum-demo'
STUDENT_COUNT = 20
STUDENT_NICKNAMES = (
    '김서윤', '이도윤', '박하은', '최서준', '정지우',
    '강서아', '조이준', '윤하린', '장도현', '임지안',
    '한예준', '오수아', '서준우', '신채원', '권민준',
    '황다은', '안유나', '송시우', '류하준', '전가은'
)
DEMO_REVIEWS = (
    '색이 예쁘고 편해서 학교에 입고 왔어요!',
    '친구가 나눠 준 옷이라 더 소중하게 느껴져요.',
    '사이즈가 잘 맞아서 자주 입고 있어요.',
    '깨끗하고 따뜻해서 요즘 잘 입어요.',
    '마음에 쏙 들어요. 다음에도 같이 나누고 싶어요!',
    '새 옷을 만난 것 같아 기분이 좋아요.',
    '활동하기 편하고 색깔도 마음에 들어요.',
    '친구와 옷 이야기를 나눌 수 있어 즐거웠어요.',
    '잘 어울린다고 해서 기뻤어요. 고마워요!',
    '소중한 옷을 나눠 줘서 고맙고 잘 입을게요.'
)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def seed_demo(source_dir=DEFAULT_SOURCE, destination_dir=DEFAULT_DESTINATION):
    source = Path(source_dir).resolve()
    destination = Path(destination_dir).resolve()
    source_index = source / 'garments' / 'index.json'
    if not source_index.is_file():
        raise FileNotFoundError(f'옷장 목록을 찾지 못했어요: {source_index}')
    source_garments = source / 'garments'
    if destination == source or destination == source_garments or source_garments in destination.parents:
        raise ValueError('데모 저장소는 원본 옷장 폴더 안에 만들 수 없어요.')
    if destination.exists():
        raise FileExistsError(f'데모 저장소가 이미 있어요. 기존 자료를 보존합니다: {destination}')

    records = json.loads(source_index.read_text(encoding='utf-8'))
    if not isinstance(records, list) or len(records) < STUDENT_COUNT:
        raise ValueError(f'학생마다 옷을 한 벌씩 배정하려면 옷이 {STUDENT_COUNT}벌 이상 필요해요.')
    for record in records:
        folder = source / 'garments' / str(record.get('id', ''))
        if not folder.is_dir() or not (folder / 'photo.jpg').is_file() or not (folder / 'garment.json').is_file():
            raise FileNotFoundError(f'옷 사진이나 정보가 빠져 있어요: {record.get("id", "(id 없음)")}')

    # Destination must be new; build it completely before exposing the finished demo.
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(destination.name + f'.tmp-{secrets.token_hex(4)}')
    try:
        garments_dir = staging / 'garments'
        garments_dir.mkdir(parents=True)
        accounts = AccountStore(staging)
        students = []
        for number in range(1, STUDENT_COUNT + 1):
            username = f's{number}'
            password = '111'
            name = STUDENT_NICKNAMES[number - 1]
            account = accounts.signup(username, password, name)
            students.append({**account, 'password': password})

        demo_records = []
        now = datetime.now().astimezone().isoformat(timespec='seconds')
        exchanges = []
        for index, record in enumerate(records):
            owner_index = index % STUDENT_COUNT
            owner = students[owner_index]
            receiver = students[(owner_index + 1) % STUDENT_COUNT]
            garment_id = record['id']
            source_folder = source / 'garments' / garment_id
            target_folder = garments_dir / garment_id
            shutil.copytree(source_folder, target_folder)
            garment = json.loads((target_folder / 'garment.json').read_text(encoding='utf-8'))
            garment['ownerName'] = owner['name']
            garment['ownerUsername'] = owner['username']
            write_json(target_folder / 'garment.json', garment)

            list_item = dict(record)
            list_item['ownerName'] = owner['name']
            list_item['ownerUsername'] = owner['username']
            demo_records.append(list_item)

            review = DEMO_REVIEWS[index % len(DEMO_REVIEWS)]
            exchanges.append({
                'id': f'e{index + 1}', 'giverId': f'u{owner_index + 1}',
                'receiverId': f'u{(owner_index + 1) % STUDENT_COUNT + 1}',
                'itemName': record.get('name') or '나눔 옷', 'garmentId': garment_id,
                'status': 'completed', 'createdAt': now, 'completedAt': now,
                'giverToReceiver': 6 + (index % 5),
                'receiverToGiver': 5 + ((index * 3) % 6),
                'revealedAt': now, 'review': {'text': review, 'createdAt': now},
                'isDemoReview': True
            })

        write_json(garments_dir / 'index.json', demo_records)
        ranking = {
            'version': 1,
            'users': [{'id': f'u{n}', 'name': student['name']} for n, student in enumerate(students, 1)],
            'exchanges': exchanges, 'badges': [], 'finalizedMonths': []
        }
        write_json(staging / 'ranking.json', {'rev': 1, 'state': ranking})
        with (staging / 'demo-accounts.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['school', 'name', 'username', 'password'])
            for student in students:
                writer.writerow(['나눔초등학교', student['name'], student['username'], student['password']])
        write_json(staging / 'demo-manifest.json', {
            'school': '나눔초등학교', 'students': STUDENT_COUNT,
            'garments': len(demo_records), 'source': str(source),
            'generatedAt': now, 'rankingExchanges': len(exchanges)
        })
        staging.replace(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return {'students': STUDENT_COUNT, 'garments': len(demo_records), 'exchanges': len(exchanges),
            'destination': str(destination)}


def main():
    parser = argparse.ArgumentParser(description='원본 자료를 유지하며 나눔초등학교 로컬 데모를 준비합니다.')
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE, help='복사할 기존 server-data 폴더')
    parser.add_argument('--destination', type=Path, default=DEFAULT_DESTINATION, help='새 데모 데이터 폴더')
    args = parser.parse_args()
    result = seed_demo(args.source, args.destination)
    print(f"준비 완료: 학생 {result['students']}명, 옷 {result['garments']}벌, 완료된 나눔 {result['exchanges']}건")
    print(f"계정표: {Path(result['destination']) / 'demo-accounts.csv'}")
    print('원본 자료는 변경하지 않았습니다.')


if __name__ == '__main__':
    main()
