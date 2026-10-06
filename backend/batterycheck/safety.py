"""Source-grounded navigation guidance; expert review is still pending."""
import json
from pathlib import Path

GUIDANCE = json.loads((Path(__file__).parents[2] / 'shared/safety.json').read_text())
SOURCES = GUIDANCE['sources']
EMERGENCY_TEXT = GUIDANCE['emergency_text']


def risk_guidance(signs, answered):
    level = 'emergency' if {'smoke', 'flame'} & set(signs) else 'concern' if signs else 'reported_none' if answered else 'unanswered'
    text = {
        'emergency': GUIDANCE['emergency_text'],
        'concern': GUIDANCE['concern_text'],
        'reported_none': '현재 응답에서 위험징후가 보고되지 않았습니다. 내부 상태나 제품 안전을 확인한 결과가 아닙니다.',
        'unanswered': '위험징후 질문에 아직 응답하지 않았습니다. 위험징후가 없다고 판단하지 않습니다.',
    }[level]
    return {'level': level, 'text': text, 'reported_signs': sorted(set(signs)),
            'sources': SOURCES if signs else [], 'rule_version': GUIDANCE['rule_version'],
            'review_status': GUIDANCE['review_status']}
