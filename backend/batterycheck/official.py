"""Bounded public-recall ingestion; candidate retrieval is never eligibility approval."""
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .models import ProductInput
from .safety import risk_guidance

API_URL = "https://www.saferproducts.gov/RestWebServices/Recall"
API_PARAMS = {"format": "json", "ProductName": "power bank"}
CURATED_PATH = Path(__file__).with_name("official_catalog.json")
MAX_BYTES = 4 * 1024 * 1024
MAX_RECORDS = 2000
FRESH_FOR = timedelta(days=7)  # Application cache policy, not an agency SLA.
LABELS = {"incomplete": "리콜 여부를 확인하지 못했습니다", "needs_identification": "제품 정보를 더 입력해주세요",
          "needs_information": "제품 정보를 조금 더 확인해주세요"}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _norm(value):
    return " ".join(unicodedata.normalize("NFKC", value).upper().split())


def _token(needle, text):
    # Model punctuation and suffixes are identity, not fuzzy-search noise.
    return bool(re.search(r"(?<![\w-])" + re.escape(_norm(needle)) + r"(?![\w-])", _norm(text)))


def _official_url(value):
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return (parsed.scheme == "https" and parsed.hostname in {"www.cpsc.gov", "cpsc.gov"}
            and parsed.username is None and parsed.password is None
            and parsed.netloc in {"www.cpsc.gov", "cpsc.gov"} and parsed.path.startswith("/Recalls/"))


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except ValueError:
        return None


def _fresh(value, now):
    parsed = _time(value)
    return parsed is not None and timedelta(0) <= now - parsed <= FRESH_FOR


def _atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".recalls-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read(path, default):
    try:
        if path.stat().st_size > MAX_BYTES * 2:
            return default
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _api_notice(record, fetched_at):
    if not isinstance(record, dict):
        raise ValueError("invalid_record")
    if not isinstance(record.get("Title"), str) or not record["Title"].strip():
        raise ValueError("missing_title")
    if not _official_url(record.get("URL")):
        raise ValueError("invalid_official_url")
    identity = record.get("RecallNumber") or record.get("RecallID")
    if not isinstance(identity, (str, int)) or isinstance(identity, bool) or not str(identity):
        raise ValueError("missing_recall_id")
    search = [record["Title"]]
    for field in ("Description",):
        if isinstance(record.get(field), str):
            search.append(record[field])
    for field, keys in (("Products", ("Name", "Description", "Model")), ("Manufacturers", ("Name",))):
        collection = record.get(field) or []
        if not isinstance(collection, list):
            raise ValueError("invalid_collection")
        for item in collection:
            if not isinstance(item, dict):
                raise ValueError("invalid_collection_item")
            search.extend(item[key] for key in keys if isinstance(item.get(key), str))
    published = record.get("RecallDate")
    if not isinstance(published, str) or not published:
        raise ValueError("missing_publish_date")
    return {
        "id": "cpsc-" + str(identity), "title": record["Title"], "source_url": record["URL"],
        "source_id": "cpsc", "published_at": published,
        "last_publish_at": record.get("LastPublishDate"), "fetched_at": fetched_at,
        "content_hash": _digest(record), "evidence_kind": "official_api",
        "review_status": "pending", "search_text": "\n".join(search),
        "action": "공식 공지에서 구매 국가, 일련번호, 조치 방법을 확인해주세요. 자동으로 찾은 결과만으로 리콜 대상이라고 단정할 수 없습니다.",
        "conditions": [], "raw": record,
    }


class OfficialService:
    def __init__(self, data_dir, *, transport=None, clock=None, curated_path=CURATED_PATH):
        self.data_dir = Path(data_dir)
        self.transport = transport
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.curated_path = Path(curated_path)
        self.lock = threading.Lock()

    def _load(self):
        cache_path = self.data_dir / "cpsc-cache.json"
        cache = _read(cache_path, {})
        meta = _read(self.data_dir / "cpsc-status.json", {})
        curated = _read(self.curated_path, {})
        if not isinstance(cache, dict):
            cache = {}
        if not isinstance(meta, dict):
            meta = {}
        notices = []
        try:
            if cache:
                records = cache["records"]
                if not isinstance(records, list) or len(records) > MAX_RECORDS or _digest(records) != cache["records_hash"]:
                    raise ValueError("cache_integrity")
                notices = [_api_notice(record, cache["fetched_at"]) for record in records]
        except (KeyError, ValueError, TypeError):
            cache, notices = {}, []
            meta = meta | {"state": "failed", "error": "저장된 CPSC 자료 검증 실패"}
        if cache_path.exists() and not cache:
            meta = meta | {"state": "failed", "error": "저장된 CPSC 자료 검증 실패"}
        curated_notices = []
        if isinstance(curated, dict):
            for notice in curated.get("notices", []):
                if isinstance(notice, dict) and _official_url(notice.get("source_url")):
                    item = dict(notice)
                    item["content_hash"] = _digest({k: v for k, v in notice.items() if k != "fetched_at"})
                    curated_notices.append(item)
        # A newer API copy takes precedence over manually captured evidence at the same URL.
        urls = {notice["source_url"] for notice in notices}
        combined = notices + [notice for notice in curated_notices if notice["source_url"] not in urls]
        return cache, meta, curated_notices, sorted(combined, key=lambda notice: notice["id"])

    def status(self):
        cache, meta, curated, notices = self._load()
        now = self.clock()
        last_success = cache.get("fetched_at")
        state = meta.get("state", "not_synced")
        if state == "available" and not _fresh(last_success, now):
            state = "stale"
        curated_at = min((n.get("fetched_at", "") for n in curated), default=None)
        sources = [
            {"id": "cpsc", "name": "미국 CPSC 리콜 정보 (자동 업데이트)", "state": state,
             "last_success_at": last_success, "last_attempt_at": meta.get("last_attempt_at"),
             "scope": '미국 CPSC의 보조배터리(power bank) 리콜 목록 · 모든 제품을 포함하지는 않습니다',
             "source_url": "https://www.cpsc.gov/Recalls", "synthetic": False,
             "error": meta.get("error"), "count": len(cache.get("records", [])),
             "notices_count": len(cache.get("records", []))},
            {"id": "cpsc-curated", "name": "미국 CPSC 공식 공지", "state": "curated" if _fresh(curated_at, now) else "stale" if curated else "unconfigured",
             "last_success_at": curated_at, "scope": f"공식 사이트에서 직접 확인한 공지 {len(curated)}건 · 미국 판매 제품 기준",
             "source_url": "https://www.cpsc.gov/Recalls", "synthetic": False, "count": len(curated),
             "notices_count": len(curated), "error": None},
            {"id": "safety-korea", "name": "Safety Korea (국내)", "state": "unconfigured",
             "last_success_at": None, "scope": "아직 연결되지 않았습니다 · 국내 제품은 Safety Korea 사이트에서 직접 확인해주세요",
             "source_url": "https://www.safetykorea.kr/recall/recallBoard", "synthetic": False,
             "setup_url": "https://www.safetykorea.kr/release/openapi2", "notices_count": 0,
             "error": "연결 준비 중"},
        ]
        return {"state": state, "sources": sources, "notice_count": len(notices), "notices_count": len(notices),
                "catalog_hash": _digest([(n["id"], n["content_hash"]) for n in notices]),
                "last_success_at": last_success, "last_attempt_at": meta.get("last_attempt_at"),
                "coverage_complete": False, "freshness_days": FRESH_FOR.days}

    def documents(self):
        """Retrieval corpus for the assistant: stored notices only, no network."""
        _, _, _, notices = self._load()
        documents = []
        for notice in notices:
            if notice["evidence_kind"] == "official_web_curated":
                parts = [" ".join(notice["brands"] + notice["models"]), notice["action"],
                         *(c["text"] for c in notice.get("conditions", [])), *notice.get("excerpts", [])]
            else:
                parts = [notice["search_text"][:1500], notice["action"]]
            documents.append({"id": notice["id"], "title": notice["title"], "url": notice["source_url"],
                              "published_at": notice["published_at"], "text": "\n".join(parts)})
        return documents

    def sync(self):
        """Only this explicit operation calls the network; no product data is sent."""
        with self.lock:
            now = self.clock().isoformat()
            error = "공식 API 조회 실패"
            for attempt in range(2):
                try:
                    with httpx.Client(timeout=httpx.Timeout(12, connect=5), follow_redirects=False,
                                      transport=self.transport) as client:
                        with client.stream("GET", API_URL, params=API_PARAMS,
                                           headers={"Accept": "application/json", "User-Agent": "BatteryCheck/1.0 (public recall research)"}) as response:
                            response.raise_for_status()
                            if int(response.headers.get("content-length", "0")) > MAX_BYTES:
                                raise ValueError("response_too_large")
                            chunks, size = [], 0
                            for chunk in response.iter_bytes():
                                size += len(chunk)
                                if size > MAX_BYTES:
                                    raise ValueError("response_too_large")
                                chunks.append(chunk)
                            raw = b"".join(chunks)
                    records = json.loads(raw)
                    if not isinstance(records, list) or len(records) > MAX_RECORDS:
                        raise ValueError("invalid_catalog_shape")
                    parsed = [_api_notice(record, now) for record in records]
                    if len({n["id"] for n in parsed}) != len(parsed):
                        raise ValueError("duplicate_recall_id")
                    records.sort(key=lambda record: str(record.get("RecallNumber") or record.get("RecallID")))
                    records_hash = _digest(records)
                    cache = {"fetched_at": now, "records": records, "records_hash": records_hash,
                             "response_hash": hashlib.sha256(raw).hexdigest(),
                             "request_url": API_URL, "request_params": API_PARAMS}
                    revision_path = self.data_dir / "revisions" / (records_hash + ".json")
                    if not revision_path.exists():
                        _atomic(revision_path, cache)
                    _atomic(self.data_dir / "cpsc-cache.json", cache)
                    _atomic(self.data_dir / "cpsc-status.json", {"state": "available", "last_attempt_at": now, "error": None})
                    return self.status() | {"ok": True, "fetched_count": len(records)}
                except httpx.HTTPStatusError as exc:
                    error = f"미국 CPSC 서버가 응답하지 않았습니다 (오류 {exc.response.status_code}). 저장된 정보를 그대로 사용합니다."
                    if exc.response.status_code < 500 and exc.response.status_code != 429:
                        break
                except httpx.RequestError:
                    error = "미국 CPSC 서버에 연결하지 못했습니다. 저장된 정보를 그대로 사용합니다."
                except (ValueError, TypeError, OSError):
                    error = "미국 CPSC에서 받은 정보를 확인하지 못했습니다. 저장된 정보를 그대로 사용합니다."
                    break
                if attempt == 0:
                    time.sleep(0.25)
            _atomic(self.data_dir / "cpsc-status.json", {"state": "failed", "last_attempt_at": now, "error": error})
            return self.status() | {"ok": False, "error": error}

    def evaluate(self, product: ProductInput):
        now = self.clock()
        status = self.status()
        _, _, _, notices = self._load()
        result = {"checked_at": now.isoformat(), "synthetic": False,
                  "catalog_version": "cpsc-candidates-v1", "catalog_hash": status["catalog_hash"],
                  "status": "incomplete", "label": LABELS["incomplete"], "notices": [], "questions": [],
                  "sources": status["sources"], "risk": risk_guidance(product.signs, product.signs_answered),
                  "limitations": "지금은 미국(CPSC) 공식 리콜 공지 일부만 확인할 수 있습니다. 국내(Safety Korea) 정보는 아직 연결되지 않았습니다. 리콜 공지를 찾지 못했다고 해서 제품이 반드시 안전한 것은 아닙니다.",
                  "next_action": "확인할 수 있는 공지에서는 이 제품을 찾지 못했습니다. 리콜이 없다는 뜻은 아니니 Safety Korea와 제조사 사이트에서도 확인해주세요.",
                  "coverage_complete": False}
        if not product.brand or not product.model or not product.model_confirmed or product.product_kind != "power_bank":
            result.update(status="needs_identification", label=LABELS["needs_identification"],
                          next_action="제품 라벨에서 브랜드와 모델명을 확인해 입력해주세요. 사진에서 읽은 값은 라벨과 같은지 확인이 필요합니다.")
            return result
        for notice in notices:
            if notice["evidence_kind"] == "official_web_curated":
                candidate = _norm(product.brand) in {_norm(b) for b in notice["brands"]} and _norm(product.model) in {_norm(m) for m in notice["models"]}
            else:
                candidate = _token(product.brand, notice["search_text"]) and _token(product.model, notice["search_text"])
            if not candidate:
                continue
            checks = [{"field": "brand", "state": "match", "input": product.brand, "expected": [product.brand], "basis": "공식 공지 내 식별자 후보 · 자격 확인 아님"},
                      {"field": "model", "state": "match", "input": product.model, "expected": [product.model], "basis": "접미사·하이픈을 보존한 모델 식별자 대조"}]
            for condition in notice.get("conditions", []):
                # Serial numbers intentionally stay on the manufacturer's site, not the user's lot field.
                checks.append({"field": condition["field"], "state": "unknown", "input": product.sale_region if condition["field"] == "sale_region" else None,
                               "expected": condition["expected"], "basis": condition["text"]})
                result["questions"].append({"notice_id": notice["id"], "field": condition["field"], "text": condition["text"]})
            checks.append({"field": "notice_review", "state": "unknown", "input": None,
                           "expected": ["공식 공지와 제조사 안내 확인"], "basis": "자동으로 찾은 공지이며 직접 확인 필요"})
            result["questions"].append({"notice_id": notice["id"], "field": "notice_review",
                                        "text": "모델명이 같아도 모두 리콜 대상은 아닙니다. 공식 공지와 제조사 페이지에서 내 제품이 해당하는지 확인해주세요."})
            item = {key: value for key, value in notice.items() if key not in {"raw", "search_text", "brands", "excerpts"}}
            item.update(checks=checks, outcome="pending_review", review_status="pending", stale=not _fresh(notice["fetched_at"], now))
            result["notices"].append(item)
        if result["notices"]:
            if all(n["stale"] for n in result["notices"]):
                result["next_action"] = "저장된 리콜 정보가 만료됐습니다. 아래 공식 공지를 직접 확인하고 최신 정보를 다시 확인해주세요."
            else:
                result.update(status="needs_information", label=LABELS["needs_information"],
                              next_action="모델명이 같은 공식 리콜 공지가 있습니다. 구매 국가와 일련번호까지 맞아야 리콜 대상입니다. 아래 공식 공지에서 확인해주세요.")
        if status["state"] == "failed":
            result["next_action"] += " 최신 리콜 정보를 불러오는 데 실패해 저장된 정보로 확인했습니다."
        return result
