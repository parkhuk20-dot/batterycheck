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
        'reported_none': '이상 증상이 없다고 입력했습니다. 제품 안전을 확인한 결과가 아닙니다.',
        'unanswered': '아직 이상 증상을 확인하지 않았습니다.',
    }[level]
    return {'level': level, 'text': text, 'reported_signs': sorted(set(signs)),
            'sources': SOURCES if signs else [], 'rule_version': GUIDANCE['rule_version'],
            'review_status': GUIDANCE['review_status']}
