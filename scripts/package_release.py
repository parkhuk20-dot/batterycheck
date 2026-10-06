"""Package source and verification evidence, excluding credentials and runtime data."""
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'dist'
OUTPUT.mkdir(exist_ok=True)
archive = OUTPUT / 'batterycheck-0.2.0-source.zip'
excluded = {'node_modules', '.next', '.data', '__pycache__', '.venv', '.git', 'dist', 'test-results'}
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
    for file in sorted(ROOT.rglob('*')):
        relative = file.relative_to(ROOT)
        if not file.is_file() or file.is_symlink() or excluded.intersection(relative.parts):
            continue
        if (file.name.startswith('.env') and file.name != '.env.example') or file.name == '.DS_Store':
            continue
        if file.suffix in {'.sqlite3', '.db', '.pyc'} or 'browser-failure' in file.name or file.name.endswith('-server.log'):
            continue
        bundle.write(file, 'batterycheck/' + relative.as_posix())
    proposal = ROOT.parent / '배터리체크_개선기획서.md'
    if proposal.exists():
        bundle.write(proposal, proposal.name)
with zipfile.ZipFile(archive) as bundle:
    assert bundle.testzip() is None
    assert not any(excluded.intersection(Path(name).parts) for name in bundle.namelist())
    count = len(bundle.namelist())
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(OUTPUT / 'SHA256SUMS').write_text(digest + '  ' + archive.name + '\n')
print(f'{archive}\n{count} files, {archive.stat().st_size} bytes\nSHA-256 {digest}')
