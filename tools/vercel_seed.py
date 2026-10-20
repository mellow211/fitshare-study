"""Copy the local server-data demo files into the Supabase storage used on Vercel.

Usage (after running supabase/schema.sql and `vercel env pull .env.vercel`):
    python tools/vercel_seed.py --env-file .env.vercel

Login sessions and the YOLO model cache are not copied. The saved dataset experiment
results in docs/data are added so the measure-dataset pages can show them on Vercel.
Files that already exist in Supabase are left unchanged (the upload only fills in missing files).
"""

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.vercel_storage import SyncedFolder, store_from_env  # noqa: E402

SKIPPED = {'sessions.json', 'model-cache', 'measure-dataset-report'}
# docs/data 결과 파일 -> server.py가 읽는 server-data/measure-dataset-report 파일
SAVED_REPORTS = {'measure-dataset.json': 'report.json', 'measure-dataset-round3.json': 'round3.json'}


def read_env_file(path):
    values = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def copy_saved_reports(docs_data, report_dir):
    """GitHub Pages용 결과의 사진 주소(assets/...)를 사이트 기준(docs/assets/...)으로 바꿔 넣어요."""
    report_dir.mkdir(parents=True, exist_ok=True)
    for source_name, target_name in SAVED_REPORTS.items():
        report = json.loads((docs_data / source_name).read_text(encoding='utf-8'))
        for row in report.get('rows', []):
            source_url = str(row.get('sourceUrl') or '')
            if source_url.startswith('assets/'):
                row['sourceUrl'] = f'docs/{source_url}'
        (report_dir / target_name).write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--env-file', default='.env.vercel')
    parser.add_argument('--source', default=str(PROJECT_ROOT / 'server-data'))
    args = parser.parse_args()
    store = store_from_env(read_env_file(args.env_file))
    with tempfile.TemporaryDirectory() as temporary:
        staging = Path(temporary) / 'data'
        shutil.copytree(Path(args.source), staging, ignore=lambda folder, names: [name for name in names if name in SKIPPED])
        copy_saved_reports(PROJECT_ROOT / 'docs' / 'data', staging / 'measure-dataset-report')
        result = SyncedFolder(staging, store).push()
    print(f"uploaded {result['uploaded']}, already there {result['conflicts']}")


if __name__ == '__main__':
    main()
