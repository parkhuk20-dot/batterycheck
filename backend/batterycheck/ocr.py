"""On-device OCR with mandatory user confirmation; photos are never retained."""
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import tempfile
import threading

from fastapi import HTTPException

MAX_BYTES = 4 * 1024 * 1024
_lock = threading.Lock()
_slots = threading.BoundedSemaphore(1)


def image_dimensions(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) >= 33 and data[12:16] == b'IHDR':
        return struct.unpack('>II', data[16:24])
    if data.startswith(b'\xff\xd8'):
        i = 2
        while i + 4 <= len(data):
            if data[i] != 255:
                break
            while i < len(data) and data[i] == 255:
                i += 1
            if i >= len(data):
                break
            marker = data[i]
            i += 1
            if marker in (0xD9, 0xDA):
                break
            if marker == 0x01 or 0xD0 <= marker <= 0xD8:
                continue
            length = int.from_bytes(data[i:i + 2], 'big')
            if length < 2 or i + length > len(data):
                break
            if marker in (0xC0, 0xC1, 0xC2) and length >= 8:
                height, width = struct.unpack('>HH', data[i + 3:i + 7])
                return width, height
            i += length
    raise HTTPException(422, '일반 PNG 또는 JPEG 라벨 사진을 올려주세요.')


def engine_status():
    if platform.system() == 'Darwin' and shutil.which('swiftc'):
        return {'engine': 'Apple Vision', 'available': True, 'processing': 'local', 'retains_images': False}
    if shutil.which('tesseract'):
        return {'engine': 'Tesseract', 'available': True, 'processing': 'local', 'retains_images': False}
    return {'engine': None, 'available': False, 'processing': 'local', 'retains_images': False}


def _vision_binary():
    source = Path(__file__).with_name('vision_ocr.swift')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    runtime = Path(os.environ.get('BATTERYCHECK_RUNTIME_DIR', Path(__file__).parents[1] / '.data/ocr'))
    runtime.mkdir(parents=True, exist_ok=True)
    binary = runtime / ('vision-' + digest)
    with _lock:
        if not binary.exists():
            with tempfile.TemporaryDirectory(prefix='batterycheck-swift-') as cache:
                try:
                    subprocess.run(['swiftc', '-module-cache-path', cache, str(source), '-o', str(binary)],
                                   check=True, timeout=90, capture_output=True)
                except (OSError, subprocess.SubprocessError) as exc:
                    binary.unlink(missing_ok=True)
                    raise HTTPException(503, '로컬 OCR 엔진을 준비하지 못했습니다. 직접 입력을 이용하세요.') from exc
    return binary


def candidates_from_text(lines):
    text = '\n'.join(lines)[:16000]
    brands = []
    for known in ['ANKER', 'BELKIN', 'BASEUS', 'UGREEN', 'SAMSUNG', 'XIAOMI', 'ROMOSS', 'INIU', 'DEMO LAB']:
        if re.search(r'(?<![A-Z0-9])' + re.escape(known) + r'(?![A-Z0-9])', text.upper()):
            brands.append(known)
    models = []
    for line in lines:
        match = re.search(r'(?:model(?:\s*(?:no\.?|number))?|모델(?:명)?|제품명)\s*[:：#]?\s*([A-Za-z0-9][A-Za-z0-9._ /-]{1,60})', line, re.I)
        if match:
            value = match.group(1).strip()
            if any(c.isdigit() for c in value):
                models.append(value)
        models.extend(re.findall(r'(?<![A-Za-z0-9._-])A\d{4}[A-Za-z0-9._-]*(?![A-Za-z0-9._-])', line, re.I))
    return {'text': text, 'brand_candidates': list(dict.fromkeys(brands))[:8],
            'model_candidates': list(dict.fromkeys(models))[:8], 'requires_confirmation': True,
            'model_confirmed': False, 'photo_retained': False,
            'limitations': '사진에서 읽은 후보입니다. 브랜드·모델명을 라벨과 직접 대조하세요. 제조일·지역·로트는 추측하지 않습니다.'}


def extract_label(image_base64):
    if not isinstance(image_base64, str) or len(image_base64) > (MAX_BYTES * 4 // 3 + 4):
        raise HTTPException(413, '사진은 4MB 이하만 지원합니다.')
    try:
        data = base64.b64decode(image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, '사진 인코딩이 올바르지 않습니다.') from exc
    if len(data) > MAX_BYTES:
        raise HTTPException(413, '사진은 4MB 이하만 지원합니다.')
    width, height = image_dimensions(data)
    if not width or not height or width * height > 20_000_000:
        raise HTTPException(413, '사진 해상도는 2천만 화소 이하만 지원합니다.')
    status = engine_status()
    if not status['available']:
        raise HTTPException(503, 'OCR 엔진이 설치되지 않았습니다. 모델명을 직접 입력하세요.')
    if not _slots.acquire(blocking=False):
        raise HTTPException(429, '다른 사진을 인식 중입니다. 잠시 후 다시 시도하세요.')
    try:
        with tempfile.TemporaryDirectory(prefix='batterycheck-label-') as folder:
            image = Path(folder) / ('label.png' if data.startswith(b'\x89PNG') else 'label.jpg')
            image.write_bytes(data)
            if status['engine'] == 'Apple Vision':
                cmd = [str(_vision_binary()), str(image)]
            else:
                cmd = ['tesseract', str(image), 'stdout', '-l', 'eng', '--psm', '6']
            try:
                completed = subprocess.run(cmd, check=True, timeout=25, capture_output=True, text=True)
                lines = json.loads(completed.stdout)['lines'] if status['engine'] == 'Apple Vision' else completed.stdout.splitlines()
            except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
                raise HTTPException(422, '사진을 읽지 못했습니다. 라벨을 선명하게 촬영하거나 직접 입력하세요.') from exc
        result = candidates_from_text(lines)
        result.update(engine=status['engine'], processing='local')
        return result
    finally:
        _slots.release()
