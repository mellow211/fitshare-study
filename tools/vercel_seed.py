"""Copy the local server-data demo files into the Vercel Redis/Blob storage once.

Usage (after `vercel env pull .env.vercel`):
    python tools/vercel_seed.py --env-file .env.vercel

Login sessions and the YOLO model cache are not copied. Files that already exist in the
remote storage are left unchanged (the upload only fills in missing files).
"""

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.vercel_storage import SyncedFolder, backends_from_env  # noqa: E402

SKIPPED = {'sessions.json', 'model-cache'}


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


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--env-file', default='.env.vercel')
    parser.add_argument('--source', default=str(PROJECT_ROOT / 'server-data'))
    args = parser.parse_args()
    redis, blob = backends_from_env(read_env_file(args.env_file))
    source = Path(args.source)
    with tempfile.TemporaryDirectory() as temporary:
        staging = Path(temporary) / 'data'
        shutil.copytree(source, staging, ignore=lambda folder, names: [name for name in names if name in SKIPPED])
        folder = SyncedFolder(staging, redis, blob)
        result = folder.push()
    print(f"올림 {result['uploaded']}개, 이미 있어서 건너뜀 {result['conflicts']}개")


if __name__ == '__main__':
    main()
