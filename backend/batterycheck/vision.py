"""Label reading with a vision model. It only reads what is printed; recall decisions stay with the rule engine."""
import base64
import json
import logging
import os
import re

log = logging.getLogger('uvicorn.error')
DEBUG = os.environ.get('BATTERYCHECK_DEBUG', '').lower() in {'1', 'true'}
FIELDS = ('brand', 'product_name', 'model', 'lot_or_batch', 'serial_number')

SOURCES = ('visible_logo_text', 'label_text', 'manufacturer_text', 'website_domain', 'unknown')

PROMPT = '''이 이미지는 보조배터리 또는 충전기 제품 라벨이다.

사진에 실제로 보이는 글자만 읽어라. 보이지 않는 내용은 추측하지 마라. 제품 외형이나 로고만 보고 모델명을 짐작하지 마라. 이 결과는 리콜 확인에 쓰이므로 틀린 모델명은 없는 것보다 해롭다.

다음 정보를 추출하라: brand, product_name, model, lot_or_batch, serial_number.

brand는 "Brand:"라는 글자가 없어도 찾는다. 다음 순서로 후보를 본다: (1) 라벨 상단이나 제품 표면에서 가장 크고 눈에 띄는 글자, (2) 로고에 들어간 글자, (3) 제조사·회사명, (4) 제품 웹사이트 주소, (5) 제품명 주변에 반복되는 고유명사. 회사명이 "Shenzhen Baseus Technology Co., Ltd."이면 brand는 "Baseus"이다. "GETCASELY.COM"만 보이고 다른 브랜드 표시가 없으면 "Casely"를 후보로 쓸 수 있지만, 주소에서 짐작한 값은 로고에서 직접 읽은 값보다 confidence를 낮게 준다. 확실하지 않은 브랜드를 만들어내지는 마라. brand_source에는 brand를 찾은 곳을 visible_logo_text, label_text, manufacturer_text, website_domain, unknown 중에서 적는다.

특히 Model, Model No., Model Number, 모델명, 型番, LOT, Batch, S/N, Serial 주변을 자세히 확인하라. 모델명과 시리얼번호를 혼동하지 마라. 여러 숫자와 코드가 있어도 Model 표시 가까이에 있는 값만 model로 써라. 글자는 라벨에 적힌 그대로(하이픈, 접미사 포함) 옮겨라.

보이지 않는 값은 null로 둔다. image_quality는 글자가 선명하면 "clear", 흐리거나 너무 작아 읽기 어려우면 "blurry", 라벨 글자가 보이지 않는 제품 사진이면 "no_label"이다. confidence는 각 값을 얼마나 확실히 읽었는지 0에서 1 사이로 적는다.

JSON 객체 하나만 반환하라.'''

_NULLABLE = {'type': ['string', 'null']}
SCHEMA = {'type': 'object', 'additionalProperties': False,
          'required': [*FIELDS, 'brand_source', 'image_quality', 'confidence'],
          'properties': {**{field: _NULLABLE for field in FIELDS},
                         'brand_source': {'type': 'string', 'enum': list(SOURCES)},
                         'image_quality': {'type': 'string', 'enum': ['clear', 'blurry', 'no_label']},
                         'confidence': {'type': 'object', 'additionalProperties': False, 'required': list(FIELDS),
                                        'properties': {field: {'type': 'number'} for field in FIELDS}}}}

MESSAGES = {
    'found': '사진에서 제품 정보를 찾았어요. 이 정보가 맞나요?',
    'model_only': '사진에서 모델명을 찾았어요. 브랜드는 읽지 못했지만 모델명만으로도 리콜 정보를 확인할 수 있어요.',
    'brand_only': '모델명을 정확히 읽지 못했어요. 제품 뒷면에서 Model 또는 Model No. 부분을 가까이 촬영해주세요.',
    'not_found': '제품 정보를 찾지 못했어요. 제품 뒷면이나 바닥에서 브랜드와 Model 글자가 보이도록 가까이 촬영해주세요.',
    'blurry': '사진이 흐려서 글자를 정확히 읽기 어려워요. 초점을 맞춘 뒤 다시 촬영해주세요.',
    'unsure': '모델명을 정확히 확인하기 어려워요. 제품에 적힌 모델명을 확인해주세요.',
}
FAILURE = '사진을 확인하는 중 문제가 발생했어요. 직접 입력하거나 다시 시도해주세요.'


def parse_reply(text):
    """Tolerates code fences and stray prose around the JSON; unknown fields are dropped, missing ones default."""
    match = re.search(r'\{.*\}', text or '', re.S)
    try:
        raw = json.loads(match.group(0)) if match else {}
    except ValueError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    confidence = raw.get('confidence') if isinstance(raw.get('confidence'), dict) else {}
    seen = {'image_quality': raw.get('image_quality') if raw.get('image_quality') in {'clear', 'blurry', 'no_label'} else 'clear',
            'brand_source': raw.get('brand_source') if raw.get('brand_source') in SOURCES else 'unknown', 'confidence': {}}
    for field in FIELDS:
        value = raw.get(field)
        seen[field] = value.strip()[:80] if isinstance(value, str) and value.strip() and value.strip().lower() != 'null' else None
        score = confidence.get(field)
        seen['confidence'][field] = float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else 0.0
    return seen


def read_label(client, model, data):
    """One vision request. The image travels as real image input, never as a description of it."""
    import openai
    media_type = 'image/png' if data.startswith(b'\x89PNG') else 'image/jpeg'
    request = {'model': model, 'messages': [{'role': 'user', 'content': [
        {'type': 'text', 'text': PROMPT},
        {'type': 'image_url', 'image_url': {'url': f'data:{media_type};base64,' + base64.b64encode(data).decode()}}]}]}
    log.info('[BatteryImage] vision request started')
    try:
        response = client.chat.completions.create(
            **request, response_format={'type': 'json_schema', 'json_schema': {'name': 'label', 'strict': True, 'schema': SCHEMA}})
    except openai.BadRequestError:
        # Gateways that do not take a schema still get the same instruction; parse_reply handles free-form JSON.
        response = client.chat.completions.create(**request)
    seen = parse_reply(response.choices[0].message.content)
    log.info('[BatteryImage] vision response received')
    if DEBUG:
        log.info('[BatteryImage] vision parsed result: %s', {k: v for k, v in seen.items() if k != 'serial_number'})
    return seen


# Photos are read through the Anthropic Messages API when a key for it is set: the gateway's OpenAI-compatible
# endpoint refuses image input. BATTERYCHECK_VISION_MODEL takes the model ID as the gateway lists it.
CLAUDE_MODEL = os.environ.get('BATTERYCHECK_VISION_MODEL', 'claude-opus-4-8')


def claude_client():
    """The SDK reads ANTHROPIC_API_KEY and ANTHROPIC_BASE_URL itself; no key means this route is off."""
    if not os.environ.get('ANTHROPIC_API_KEY'):
        return None
    import anthropic
    return anthropic.Anthropic(timeout=60, max_retries=1)


def read_label_claude(client, data, model=None):
    media_type = 'image/png' if data.startswith(b'\x89PNG') else 'image/webp' if data[8:12] == b'WEBP' else 'image/jpeg'
    log.info('[BatteryImage] vision request started')
    response = client.messages.create(model=model or CLAUDE_MODEL, max_tokens=700, messages=[{'role': 'user', 'content': [
        {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type, 'data': base64.b64encode(data).decode()}},
        {'type': 'text', 'text': PROMPT}]}])
    seen = parse_reply(next((block.text for block in response.content if block.type == 'text'), ''))
    log.info('[BatteryImage] vision response received')
    return seen


def _same(a, b):
    # Case, spaces and hyphens may differ between readings; any other character difference is a different model.
    return re.sub(r'[\s-]', '', a).upper() == re.sub(r'[\s-]', '', b).upper()


def combine(ocr, seen):
    """Merge on-device text reading with the vision reading. Disagreement on the model is never auto-resolved."""
    from .ocr import display_brand
    ocr = ocr or {}
    brands, models = list(ocr.get('brand_candidates', [])), list(ocr.get('model_candidates', []))
    sources = ocr.get('brand_sources', {})
    raw = brands[0] if brands else None
    result = {'brand': display_brand(raw), 'brand_raw': raw, 'brand_source': sources.get(raw, 'label_text') if raw else 'unknown',
              'model': models[0] if models else None, 'product_name': ocr.get('product_name'), 'lot_or_batch': ocr.get('lot'), 'used_ai': seen is not None,
              'serial_number': ocr.get('serial_number'), 'serial_state': ocr.get('serial_state', 'not_found'),
              'serial_candidates': list(ocr.get('serial_candidates', [])), 'date_code': ocr.get('date_code')}
    status = None
    if seen:
        result.update(product_name=seen['product_name'] or result['product_name'], lot_or_batch=seen['lot_or_batch'] or result['lot_or_batch'])
        ai_serial = re.sub(r'(?i)^S\s?/?\s?N\s*[:：#.]?\s*', '', seen.get('serial_number') or '')
        if ai_serial and not re.search(r'(?i)X{2,}', ai_serial):
            # The text reader agreeing settles it. Alone, the vision reading counts only when it reports a clear photo and
            # near-certainty and nothing contradicts it; on the review photos every doubtful serial scored below 0.95.
            sure = seen['image_quality'] == 'clear' and seen['confidence'].get('serial_number', 0) >= 0.95
            if any(ai_serial == c for c in result['serial_candidates']) or (sure and not result['serial_candidates']):
                result.update(serial_number=ai_serial, serial_state='read', serial_candidates=[ai_serial])
            else:
                result['serial_candidates'].insert(0, ai_serial)
                result.update(serial_number=None, serial_state='uncertain')
        if seen['brand']:
            result.update(brand=display_brand(seen['brand']), brand_raw=seen['brand'], brand_source=seen['brand_source'])
            if not any(_same(seen['brand'], b) for b in brands):
                brands.insert(0, seen['brand'])
        if seen['model']:
            agrees = any(_same(seen['model'], m) for m in models)
            if (models and not agrees) or (not models and seen['confidence']['model'] < 0.6):
                models.insert(0, seen['model'])
                result['model'], status = None, 'unsure'
            else:
                result['model'] = seen['model']
                if not agrees:
                    models.insert(0, seen['model'])
        elif models:
            # The vision reading saw no model; a lone text-pattern hit is not enough to present as found.
            result['model'], status = None, 'unsure'
        if not result['model'] and seen['image_quality'] == 'blurry':
            status = 'blurry'
    status = status or ('found' if result['model'] and result['brand'] else 'model_only' if result['model']
                        else 'brand_only' if result['brand'] else 'not_found')
    result.update(status=status, message=MESSAGES[status], brand_candidates=brands[:8], model_candidates=models[:8])
    log.info('[BatteryImage] extracted brand: %s (%s)', result['brand'], result['brand_source'])
    log.info('[BatteryImage] extracted model: %s', result['model'])
    return result


def enrich(result, known_brands):
    """A model read without a brand is looked up in the official notices; the notice's brand is offered, never assumed silently."""
    if result['status'] != 'model_only' or not known_brands:
        return result
    if len(known_brands) == 1:
        return result | {'brand': known_brands[0], 'brand_raw': None, 'brand_source': 'db_enrichment', 'status': 'found_db',
                         'message': f"사진에서 모델명 {result['model']}를 찾았어요. 공식 리콜 정보에서는 {known_brands[0]} 제품으로 확인됩니다. 이 제품이 맞나요?"}
    return result | {'status': 'choose', 'brand_options': known_brands,
                     'message': f"모델명 {result['model']}와 일치하는 제품이 {len(known_brands)}개 있습니다. 브랜드를 확인해주세요."}
