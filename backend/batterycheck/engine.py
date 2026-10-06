import hashlib
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .models import Catalog, ProductInput
from .safety import risk_guidance

LABELS = {
    "matched": "리콜 대상 일치",
    "needs_information": "추가 확인 필요",
    "no_match": "조회 범위에서 일치 없음",
    "needs_identification": "제품 식별 필요",
    "incomplete": "조회 미완료",
}
QUESTIONS = {
    "manufactured_on": "제조일을 확인하세요. 구매일로 대신하지 마세요.",
    "sale_region": "공지의 판매지역을 확인하세요. 현재 위치로 대신하지 마세요.",
    "lot": "라벨의 로트값을 확인하세요. 앞자리 0을 포함해 그대로 입력하세요.",
}
CATALOG_PATH = Path(__file__).with_name("demo_catalog.json")


def normalize(value):
    # Preserve hyphens, suffixes, and leading zeros; only normalize case/width/spacing.
    return " ".join(unicodedata.normalize("NFKC", value).upper().split())


def evaluate(product: ProductInput, catalog_path=CATALOG_PATH, now=None):
    now = now or datetime.now(timezone.utc)
    result = {
        "checked_at": now.isoformat(), "synthetic": True, "catalog_version": None,
        "catalog_hash": None, "status": "incomplete", "notices": [], "questions": [],
        "sources": [], "risk": risk_guidance(product.signs, product.signs_answered),
        "limitations": "가상 공지 기반 시연입니다. 실제 제품의 리콜·안전·회수 자격을 확인하지 않습니다.",
        "next_action": "공식 출처를 직접 확인하세요. 공식 리콜 API와 AI는 연결되지 않았습니다.",
        "coverage_complete": False,
    }

    def finish(status):
        result.update(status=status, label=LABELS[status])
        return result

    if not product.brand or not product.model or not product.model_confirmed or product.product_kind != "power_bank":
        result["next_action"] = "보조배터리 라벨의 브랜드와 모델명을 직접 확인하세요. 사진 AI는 미연결 상태입니다."
        return finish("needs_identification")
    if not product.demo or normalize(product.brand) != "DEMO LAB" or not normalize(product.model).startswith("DEMO PB-"):
        result["synthetic"] = False
        result["sources"] = [{"id": "official-apis", "name": "Safety Korea / CPSC / 제조사",
                              "state": "unconfigured", "last_success_at": None,
                              "scope": "공식 API 미연결 · 실제 제품 판정 불가", "synthetic": False}]
        return finish("incomplete")
    try:
        raw = Path(catalog_path).read_bytes()
        catalog = Catalog.model_validate_json(raw)
    except (OSError, ValueError):
        result["sources"] = [{"id": "demo-catalog", "name": "가상 공지 저장소",
                              "state": "failed", "last_success_at": None,
                              "scope": "파일 누락 또는 자료 검증 실패", "synthetic": True}]
        result["next_action"] = "자료를 읽거나 검증하지 못했습니다. 복구 후 다시 대조하세요."
        return finish("incomplete")

    source = catalog.source.model_dump(mode="json")
    last_success = catalog.source.last_success_at
    if product.scenario == "outage":
        source["state"] = "failed"
    elif product.scenario == "stale":
        last_success = now - timedelta(days=8)
        source["last_success_at"] = last_success.isoformat()
    stale = last_success is None or now - last_success > timedelta(days=7) or last_success > now
    source["freshness"] = "stale_or_invalid" if stale else "within_demo_window"
    result.update(sources=[source], catalog_version=catalog.version,
                  catalog_hash=hashlib.sha256(raw).hexdigest(),
                  coverage_complete=source["state"] == "complete" and not stale)

    for notice in catalog.notices:
        if normalize(notice.brand) != normalize(product.brand) or normalize(product.model) not in {normalize(m) for m in notice.models}:
            continue
        checks = [{"field": "model", "state": "match", "input": product.model,
                   "expected": notice.models, "basis": "사용자 확인 입력"}]
        for condition in notice.conditions:
            value = getattr(product, condition.field)
            if value is None or (isinstance(value, str) and not value.strip()):
                state = "unknown"
            elif condition.operator == "date_range":
                start, end = [date.fromisoformat(v) for v in condition.values]
                state = "match" if start <= value <= end else "mismatch"
            else:
                state = "match" if normalize(value) in {normalize(v) for v in condition.values} else "mismatch"
            checks.append({"field": condition.field, "state": state,
                           "input": value.isoformat() if isinstance(value, date) else value,
                           "expected": condition.values, "basis": "사용자 입력 · 공식 인증 아님"})
        states = {c["state"] for c in checks}
        # Missing information is never silently converted to a negative result.
        outcome = "pending_review" if notice.review_status != "reviewed" else "unknown" if "unknown" in states else "mismatch" if "mismatch" in states else "match"
        item = notice.model_dump(mode="json")
        item.update(checks=checks, outcome=outcome, evidence_kind="synthetic",
                    source_id=catalog.source.id)
        result["notices"].append(item)
        if outcome in {"unknown", "pending_review"}:
            for check in checks:
                if check["state"] == "unknown":
                    result["questions"].append({"notice_id": notice.id, "field": check["field"],
                                                "text": QUESTIONS[check["field"]]})
            if outcome == "pending_review":
                result["questions"].append({"notice_id": notice.id, "field": "notice_review",
                                            "text": "공지 조건의 검토가 끝나지 않아 대상 일치를 확정할 수 없습니다."})
    outcomes = {n["outcome"] for n in result["notices"]}
    # Cached candidate evidence remains visible, but failed/stale coverage is not a current verdict.
    if not result["coverage_complete"]:
        result["next_action"] = "가상 출처 조회가 실패했거나 자료가 오래되었습니다. 마지막 자료 시각을 확인하고 재대조하세요."
        return finish("incomplete")
    if "match" in outcomes:
        result["next_action"] = "가상 공지의 조건과 조치 안내를 확인하세요. 실제 회수·교환 안내가 아닙니다."
        return finish("matched")
    if outcomes & {"unknown", "pending_review"}:
        result["next_action"] = "아래 미확인 조건을 확인하고 다시 대조하세요."
        return finish("needs_information")
    result["next_action"] = "이 가상 공지 집합에서 일치하지 않았습니다. 실제 제품의 안전을 보증하지 않습니다."
    return finish("no_match")
