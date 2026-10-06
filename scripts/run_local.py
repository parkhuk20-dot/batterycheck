"""Run only this project's two loopback services; do not modify the shared venv."""
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXISTING_PYTHON = Path('/Users/sejin/Desktop/codyssey/M1-2/backend/venv/bin/python')
PROJECT_PYTHON = ROOT / '.venv/bin/python'  # Has the Anthropic SDK for the AI assistant.
python = os.environ.get('BATTERYCHECK_PYTHON', str(next((p for p in (PROJECT_PYTHON, EXISTING_PYTHON) if p.exists()), Path(sys.executable))))
frontend = ROOT / 'frontend'
next_bin = frontend / 'node_modules/.bin/next'
if not next_bin.exists() or not (frontend / '.next/BUILD_ID').exists():
    raise SystemExit('먼저 frontend에서 npm ci 및 npm run build를 실행하세요. README.md 참고.')
for port in (3114, 8814):
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', port)) == 0:
            raise SystemExit(f'포트 {port}가 이미 사용 중입니다. 기존 프로세스를 중단하지 않았습니다.')

logs = ROOT / 'verification'
logs.mkdir(exist_ok=True)
env = os.environ.copy()
env['NEXT_TELEMETRY_DISABLED'] = '1'
processes = []
handles = []

def stop(*_):
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    for handle in handles:
        handle.close()

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
try:
    commands = [([python, '-m', 'uvicorn', 'batterycheck.main:app', '--host', '127.0.0.1', '--port', '8814', '--workers', '1'], ROOT / 'backend', 'backend-server.log'),
                ([str(next_bin), 'start', '--hostname', '127.0.0.1', '--port', '3114'], frontend, 'frontend-server.log')]
    for command, directory, filename in commands:
        handle = (logs / filename).open('w')
        handles.append(handle)
        processes.append(subprocess.Popen(command, cwd=directory, env=env, stdout=handle, stderr=subprocess.STDOUT))
    for _ in range(50):
        if any(process.poll() is not None for process in processes):
            raise SystemExit('서버가 시작되지 않았습니다. verification/*-server.log를 확인하세요.')
        ready = []
        for port in (3114, 8814):
            with socket.socket() as probe:
                ready.append(probe.connect_ex(('127.0.0.1', port)) == 0)
        if all(ready):
            break
        time.sleep(.2)
    else:
        raise SystemExit('서버 시작 시간을 초과했습니다. verification/*-server.log를 확인하세요.')
    print('배터리체크: http://127.0.0.1:3114', flush=True)
    print('공식 공지 후보 + 가상 시연 · 종료: Ctrl+C · localhost 외부 공개 없음', flush=True)
    while all(process.poll() is None for process in processes):
        time.sleep(.5)
finally:
    stop()
