"""Add one new app directory; never overwrite existing user files."""
import hashlib
import json
import shutil
import time
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
PARENT = Path('/Users/sejin/Desktop/codyssey/fianl project')
DESTINATION = PARENT / 'batterycheck'
PLAN = PARENT / '배터리체크_개선기획서.md'
if DESTINATION.exists():
    raise SystemExit('기존 batterycheck 디렉터리가 있어 복사를 중단했습니다. 사용자 파일을 덮어쓰지 않았습니다.')
original_hash = hashlib.sha256(PLAN.read_bytes()).hexdigest()


def ignored(directory, names):
    excluded = {'__pycache__', '.data', '.DS_Store', 'npm-logs'} & set(names)
    if Path(directory) == SOURCE / 'frontend/.next':
        excluded |= {'cache', 'dev'} & set(names)
    if Path(directory) == SOURCE / 'verification':
        excluded |= {name for name in names if name.startswith('browser-failure')}
    return excluded


started = time.monotonic()
shutil.copytree(SOURCE, DESTINATION, symlinks=True, ignore=ignored)
assert hashlib.sha256(PLAN.read_bytes()).hexdigest() == original_hash, '기존 기획서 해시가 바뀌었습니다.'
# Verify source/lock/docs/shared assets, independently of generated runtime dependencies.
verified = []
for relative in ['README.md', 'docs/IMPLEMENTATION_2026-10-02.md', 'shared/safety.json',
                 'frontend/package.json', 'frontend/package-lock.json', 'frontend/app/page.js',
                 'frontend/app/proxy.mjs', 'backend/batterycheck/engine.py', 'backend/batterycheck/main.py',
                 'backend/batterycheck/models.py', 'backend/batterycheck/store.py', 'backend/batterycheck/demo_catalog.json',
                 'backend/tests/test_flow.py', 'frontend/tests/proxy.test.mjs', 'frontend/tests/browser-e2e.mjs']:
    assert (SOURCE / relative).read_bytes() == (DESTINATION / relative).read_bytes(), relative
    verified.append(relative)
record = {'destination': str(DESTINATION), 'new_directory_only': True,
          'original_plan_preserved_sha256': original_hash, 'verified_files': verified,
          'dependencies': 'copied existing offline-installed node_modules; no reinstall',
          'excluded': ['local SQLite data', 'Python cache', 'Next build cache', 'npm logs', 'failed browser snapshots'],
          'seconds': round(time.monotonic() - started, 2)}
for location in [SOURCE, DESTINATION]:
    (location / 'verification/installation.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(record, ensure_ascii=False, indent=2))
