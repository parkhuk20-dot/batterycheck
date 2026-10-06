"""On-device OCR with mandatory user confirmation; photos are never retained."""
import base64
import binascii
import difflib
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
    raise HTTPException(422, '이 사진 형식은 아직 지원하지 않아요. JPG, PNG 또는 WEBP 사진을 사용해주세요.')


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
                    raise HTTPException(503, '지금은 사진을 읽을 수 없습니다. 브랜드와 모델명을 직접 입력해주세요.') from exc
    return binary


# Display spelling for brands that labels print in capitals or as a logo.
BRANDS = {'ANKER': 'Anker', 'BASEUS': 'Baseus', 'BELKIN': 'Belkin', 'UGREEN': 'UGREEN', 'SAMSUNG': 'Samsung', 'XIAOMI': 'Xiaomi',
          'ROMOSS': 'ROMOSS', 'INIU': 'INIU', 'CASELY': 'Casely', 'AUKEY': 'AUKEY', 'MOPHIE': 'mophie', 'RAVPOWER': 'RAVPower',
          'VEGER': 'VEGER', 'CHARMAST': 'Charmast', 'DEMO LAB': 'DEMO LAB'}
_GENERIC = {'POWER', 'BANK', 'MODEL', 'INPUT', 'OUTPUT', 'CAPACITY', 'BATTERY', 'MADE', 'CHINA', 'RATED', 'TYPE', 'USB', 'LITHIUM'}


def brand_guesses(lines):
    """Brand candidates with where each came from. A label rarely says "Brand:", so several cues are tried in order."""
    text = '\n'.join(lines).upper()
    found = []

    def add(raw, source):
        key = re.sub(r'[^A-Z0-9 ]', '', raw.upper()).strip()
        if len(key) >= 3 and key not in _GENERIC and all(key != seen for seen, _ in found):
            found.append((key, source))
    for known in BRANDS:
        if re.search(r'(?<![A-Z0-9])' + re.escape(known) + r'(?![A-Z0-9])', text):
            add(known, 'label_text')
    for name in re.findall(r'([A-Z][A-Z0-9]{2,})\s+(?:INNOVATIONS?|TECHNOLOGY|ELECTRONICS?)\s+(?:LIMITED|LTD|CO\b|INC)', text):
        add(name, 'manufacturer_text')
    for name in re.findall(r'(?:SHENZHEN|GUANGZHOU|DONGGUAN|GUANGDONG|HONG KONG)\s+([A-Z][A-Z0-9]{2,})\s+(?:TECHNOLOGY|ELECTRONICS?|INNOVATIONS?|INDUSTR)', text):
        add(name, 'manufacturer_text')
    for domain in re.findall(r'(?:WWW\.|@)?([A-Z0-9][A-Z0-9-]{2,})\.(?:COM|NET|SHOP|STORE|IO|CO\.KR|KR)(?![A-Z])', text):
        name = re.sub(r'^(?:GET|SHOP|MY|TRY|WWW)(?=[A-Z]{4,})', '', domain.replace('-', ''))
        add(name, 'website_domain')
    # Logo lettering is often misread by one character (BASEU5, 8ASEUS).
    for word in dict.fromkeys(re.findall(r'[A-Z0-9]{4,10}', text)):
        close = difflib.get_close_matches(word, [b for b in BRANDS if ' ' not in b], n=1, cutoff=0.8)
        if close and word not in _GENERIC:
            add(close[0], 'visible_logo_text')
    return found


def display_brand(raw):
    return BRANDS.get(raw.upper(), raw.title() if raw.isupper() else raw) if raw else None


# Text readers sometimes return Cyrillic look-alikes for Latin label lettering ("ЕЗЗА" for "E33A").
_LOOKALIKES = str.maketrans('АВЕКМНОРСТХУЗаеорсхуз', 'ABEKMHOPCTXY3aeopcxy3')
_UNIT = re.compile(r'(?i)\d+(?:\.\d+)?\s?(?:mah|wh|v|w|a|hz)')  # a bare figure with a unit, e.g. 5000mAh
_STANDALONE_MODEL = re.compile(r'[A-Z]{1,5}-?\d{2,6}[A-Z]{0,3}(?:-[A-Z0-9]{1,6})?')


def candidates_from_text(lines):
    lines = [line.translate(_LOOKALIKES) for line in lines]
    text = '\n'.join(lines)[:16000]
    guesses = brand_guesses(lines)
    models, product_name = [], None
    for index, line in enumerate(lines):
        match = re.search(r'(?:model(?:\s*(?:no\.?|number))?|모델(?:명)?|제품명)\s*[:：#]?\s*([A-Za-z0-9][A-Za-z0-9._ /-]{1,60})', line, re.I)
        if match:
            value = match.group(1).strip()
            if any(c.isdigit() for c in value):
                models.append(value)
                # The product line usually sits right above the model line ("PowerCore 10000" over "Model A1263").
                for above in reversed(lines[max(0, index - 2):index]):
                    name = above.strip()
                    if (product_name is None and 3 <= len(name) <= 40 and ':' not in name and re.search(r'[A-Za-z]{3}', name)
                            and not re.search(r'(?i)\b(input|output|capacity|rated|made in|mah|wh)\b', name)):
                        product_name = name
        elif re.fullmatch(r'(?i)\s*model(?:\s*(?:no\.?|number|#))?\s*[:：]?\s*', line):
            # Table-style labels put "Model" in one column and its value in the next, so the value arrives as a later line.
            for later in lines[index + 1:index + 14]:
                value = later.strip().upper()
                if _STANDALONE_MODEL.fullmatch(value) and not _UNIT.fullmatch(value):
                    models.append(value)
                    break
        models.extend(re.findall(r'(?<![A-Za-z0-9._-])A\d{4}[A-Za-z0-9._-]*(?![A-Za-z0-9._-])', line, re.I))
    return {'text': text, 'product_name': product_name, 'brand_candidates': [brand for brand, _ in guesses][:8],
            'brand_sources': dict(guesses[:8]),
            'model_candidates': list(dict.fromkeys(models))[:8], 'requires_confirmation': True,
            'model_confirmed': False, 'photo_retained': False,
            'limitations': '사진에서 읽은 값은 틀릴 수 있습니다. 라벨과 같은지 꼭 확인해주세요.'}


def image_variants(data):
    """The photo as sent, then enlarged high-contrast copies (normal and inverted) for small or light-on-dark lettering."""
    yield ('label.png' if data.startswith(b'\x89PNG') else 'label.jpg'), data
    try:
        import io
        from PIL import Image, ImageFilter, ImageOps
        gray = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert('L')
        scale = min(3.0, max(1.0, 3200 / max(gray.size)))  # enlarging helps small type; never shrink here
        if scale > 1.05:
            gray = gray.resize((round(gray.width * scale), round(gray.height * scale)), Image.LANCZOS)
        gray = ImageOps.autocontrast(gray, cutoff=1).filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=3))
        for name, image in (('enhanced.png', gray), ('inverted.png', ImageOps.invert(gray))):
            buffer = io.BytesIO()
            image.save(buffer, 'PNG')
            yield name, buffer.getvalue()
    except Exception:  # Pillow missing or an unusual file: the plain reading above still stands.
        return


def decode_image(image_base64):
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
    return data


def extract_label(image_base64):
    data = decode_image(image_base64)
    status = engine_status()
    if not status['available']:
        raise HTTPException(503, '지금은 사진을 읽을 수 없습니다. 브랜드와 모델명을 직접 입력해주세요.')
    if not _slots.acquire(blocking=False):
        raise HTTPException(429, '다른 사진을 확인하는 중입니다. 잠시 후 다시 시도해주세요.')
    try:
        with tempfile.TemporaryDirectory(prefix='batterycheck-label-') as folder:
            lines, result = [], candidates_from_text([])
            try:
                # Several readings of the same photo are merged: a logo on the front and a small model line on the back
                # rarely come out of one pass, and light-on-dark labels need the inverted copy.
                for name, content in image_variants(data):
                    image = Path(folder) / name
                    image.write_bytes(content)
                    if status['engine'] == 'Apple Vision':
                        completed = subprocess.run([str(_vision_binary()), str(image)], check=True, timeout=25, capture_output=True, text=True)
                        lines += json.loads(completed.stdout)['lines']
                    else:
                        for mode in ('6', '11'):  # block layout, then sparse text for lone logos and small lines
                            completed = subprocess.run(['tesseract', str(image), 'stdout', '-l', 'eng', '--psm', mode],
                                                       check=True, timeout=12, capture_output=True, text=True)
                            lines += [line for line in completed.stdout.splitlines() if line.strip()]
                    lines = list(dict.fromkeys(lines))
                    result = candidates_from_text(lines)
                    if result['brand_candidates'] and result['model_candidates']:
                        break
            except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
                if not lines:
                    raise HTTPException(422, '사진을 읽지 못했습니다. 라벨이 선명하게 보이도록 다시 촬영하거나 직접 입력해주세요.') from exc
        result.update(engine=status['engine'], processing='local')
        return result
    finally:
        _slots.release()
