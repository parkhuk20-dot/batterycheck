import hashlib
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .models import Catalog, ProductInput
from .safety import risk_guidance

LABELS = {
    "matched": "리콜 대상 제품과 일치합니다",
    "needs_information": "제품 정보를 조금 더 확인해주세요",
    "no_match": "현재 일치하는 리콜 공지는 없습니다",
    "needs_identification": "제품 정보를 더 입력해주세요",
    "incomplete": "리콜 여부를 확인하지 못했습니다",
}
QUESTIONS = {
    "manufactured_on": "제품에 적힌 제조일을 확인해주세요. 구매일로 대신하지 마세요.",
    "sale_region": "제품을 구매한 국가를 확인해주세요. 지금 사는 곳과 다를 수 있습니다.",
    "lot": "제품 뒷면의 LOT / 제조번호를 확인해주세요. 앞자리 0까지 그대로 입력하세요.",
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
        "limitations": "연습용 가상 공지로 확인한 결과입니다. 실제 제품의 리콜 여부가 아닙니다.",
        "next_action": "공식 리콜 사이트에서 직접 확인해주세요.",
        "coverage_complete": False,
    }

    def finish(status):
        result.update(status=status, label=LABELS[status])
        return result

    if not product.brand or not product.model or not product.model_confirmed or product.product_kind != "power_bank":
        result["next_action"] = "제품 라벨에서 브랜드와 모델명을 확인해 입력해주세요."
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
        result["next_action"] = "리콜 정보를 불러오지 못했습니다. 잠시 후 다시 확인해주세요."
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
                                            "text": "공지 내용의 검토가 끝나지 않아 아직 리콜 대상이라고 말할 수 없습니다."})
    outcomes = {n["outcome"] for n in result["notices"]}
    # Cached candidate evidence remains visible, but failed/stale coverage is not a current verdict.
    if not result["coverage_complete"]:
        result["next_action"] = "리콜 정보를 불러오지 못했거나 정보가 오래됐습니다. 리콜이 없다는 뜻이 아닙니다. 잠시 후 다시 확인해주세요."
        return finish("incomplete")
    if "match" in outcomes:
        result["next_action"] = "사용과 충전을 중지하고 공지의 조치 방법을 확인하세요. (연습용 가상 공지이며 실제 회수·교환 안내가 아닙니다.)"
        return finish("matched")
    if outcomes & {"unknown", "pending_review"}:
        result["next_action"] = "아래 내용을 확인해 입력한 뒤 다시 확인해주세요."
        return finish("needs_information")
    result["next_action"] = "확인한 공지에서는 이 제품과 일치하는 리콜을 찾지 못했습니다. 리콜 공지가 없다는 것이 제품의 안전을 보증하지는 않습니다."
    return finish("no_match")
