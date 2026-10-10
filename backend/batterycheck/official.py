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
# Fixed product-name filters; recalls are published under either wording. No user input is ever sent.
API_QUERIES = ("power bank", "portable charger")
CPSC_RETRY = timedelta(minutes=10)
_GENERIC_WORDS = {"POWER", "PORTABLE", "WIRELESS", "MAGNETIC", "SOLAR", "MINI", "BANK", "BANKS", "CHARGER", "CHARGERS", "MODEL",
                  "LITHIUM", "LITHIUM-ION", "BATTERY", "USB", "AND", "THE", "RECALLS", "RECALLED"}
CURATED_PATH = Path(__file__).with_name("official_catalog.json")
MAX_BYTES = 4 * 1024 * 1024
MAX_RECORDS = 2000
FRESH_FOR = timedelta(days=7)  # Application cache policy, not an agency SLA.
# Safety Korea Open API (국가기술표준원 인터페이스 설계서 v2.0): GET with the issued key in the AuthKey header.
KR_PATH = "www.safetykorea.kr/openapi/api/recall/recallList.json"
KR_BOARD = "https://www.safetykorea.kr/recall/recallBoard"
KR_ERRORS = {"4000": "Safety Korea 인증키가 올바르지 않습니다.", "4001": "Safety Korea에 등록되지 않은 접속 주소(IP)입니다.",
             "4005": "Safety Korea 요청 형식이 올바르지 않습니다."}
KR_RETRY = timedelta(minutes=10)
LABELS = {"incomplete": "리콜 여부를 확인하지 못했습니다", "needs_identification": "제품 정보를 더 입력해주세요",
          "needs_information": "제품 정보를 조금 더 확인해주세요", "matched": "리콜 대상 제품과 일치합니다",
          "no_match": "현재 일치하는 리콜 공지는 없습니다"}
REGION_NAMES = {"US": "미국", "KR": "대한민국"}


def _region_state(value, notice):
    """Compare the purchase country with the market the notice covers. Unknown input stays unknown."""
    home = "KR" if notice["evidence_kind"] == "official_api_kr" else "US"
    if not value:
        return home, "unknown"
    if value == home:
        return home, "match"
    # CPSC notices issued jointly with Canada or Mexico also cover those markets, which the form only knows as "other".
    if value == "OTHER" and (notice.get("raw") or {}).get("Inconjunctions"):
        return home, "unknown"
    return home, "mismatch"


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _norm(value):
    return " ".join(unicodedata.normalize("NFKC", value).upper().split())


def _token(needle, text):
    # Model punctuation and suffixes are identity, not fuzzy-search noise.
    return bool(re.search(r"(?<![\w-])" + re.escape(_norm(needle)) + r"(?![\w-])", _norm(text)))


def _loose(value):
    # Spacing and hyphens vary between a label and a notice (E33A, E-33A, E 33A); any other character must be equal.
    return re.sub(r"[\s-]", "", _norm(value))


def _has_model(model, notice):
    wanted = _loose(model)
    if any(_loose(m) == wanted for m in notice.get("models", [])):
        return True
    words = re.findall(r"[A-Z0-9][A-Z0-9 -]*[A-Z0-9]|[A-Z0-9]", _norm(notice.get("search_text", "")))
    return _token(model, notice.get("search_text", "")) or any(_loose(w) == wanted for word in words for w in (word, *word.split()))


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


FIELD_NAMES = {"serial_number": "시리얼번호(S/N)", "date_code": "제조 날짜 코드", "sale_region": "구매 국가", "official_check": "제조사 조회 결과"}
FIELD_HINTS = {"serial_number": "제품 뒷면이나 바닥 라벨의 S/N 뒤에 적힌 글자를 그대로 입력해주세요.",
               "date_code": "제품 라벨에 적힌 제조 날짜 코드를 그대로 입력해주세요.",
               "sale_region": "제품을 구매한 국가를 골라주세요. 해외 직구는 판매처 기준입니다."}
REMEDIES = {"Refund": "환불", "Replace": "교환", "Repair": "수리", "Dispose": "폐기"}
_SENTENCES = re.compile(r'(?<=\.)\s+(?=[A-Z"“])|(?<=\.["”])\s+(?=[A-Z])')
_CODE = r"[A-Z0-9][A-Z0-9-]{3,}"


def _values(fragment):
    """Identifier values in a notice phrase: quoted strings, a digit range ("0 through 9") and bare capital codes."""
    found = []
    for low, high in re.findall(r"\b(\d) through (\d)\b", fragment):
        found.extend(str(n) for n in range(int(low), int(high) + 1))
    fragment = re.sub(r"\b\d through \d\b", " ", fragment)
    found.extend(re.findall(r"(?<![\w-])[A-Z0-9][A-Z0-9/-]*(?![\w-])", fragment))
    return list(dict.fromkeys(found))


def _shown(values):
    if {str(n) for n in range(10)} <= set(values):
        values = ["0~9"] + [v for v in values if not (len(v) == 1 and v.isdigit())]
    return ", ".join(values[:8]) + (f" 외 {len(values) - 8}개" if len(values) > 8 else "")


def _condition(field, rule, values=(), models=(), text="", url=None):
    name = FIELD_NAMES[field]
    expected, basis = {
        "suffix": (f"끝자리 {_shown(values)}", f"이 제품은 {name} 끝자리에 따라 리콜 대상 여부가 달라집니다."),
        "prefix": (f"앞자리 {_shown(values)}", f"이 제품은 {name} 앞자리에 따라 리콜 대상 여부가 달라집니다."),
        "one_of": (_shown(values), f"이 제품은 공지에 적힌 {name}일 때만 리콜 대상입니다."),
        "region": ("미국 판매 제품", "이 리콜은 미국에서 판매된 제품만 대상입니다."),
        "external": ("제조사 페이지에서 조회", f"이 리콜은 제조사 페이지에서 {name}를 조회해야 대상 여부를 알 수 있습니다."),
        "manual": ("공지에 적힌 조건", f"이 리콜은 {name}에 따라 대상 여부가 달라집니다. 공지 원문: {text}"),
    }[rule]
    return {"field": field, "rule": rule, "values": list(values), "models": list(models), "expected": [expected], "text": basis} | ({"url": url} if url else {})


def _extract_conditions(text, models):
    """Read the identifying conditions out of a notice description with fixed rules; no model decides anything here.

    A condition applies to the models named in its sentence, or else to the models named most recently before it.
    """
    found, current = [], []
    def add(condition):
        same = [c for c in found if c["field"] == condition["field"] and c["models"] == condition["models"]]
        weak = {"manual", "external"}
        if not same:
            found.append(condition)
        elif same[0]["rule"] in weak and (condition["rule"] not in weak or (condition.get("url") and not same[0].get("url"))):
            found[found.index(same[0])] = condition
    for sentence in _SENTENCES.split(text):
        named = [m for m in models if _token(m, sentence)]
        current = named or current
        scope = current if len(models) > 1 else []
        if re.search(r"(?i)only [^.]*sold in the U\.S", sentence):
            add(_condition("sale_region", "region"))
        if not re.search(r"(?i)serial|date code|manufactur(?:ing|e) date", sentence) or re.search(r"(?i)all serial numbers are included", sentence):
            continue
        if match := re.search(r"(?i:serial numbers?[^.]*?\bend(?:ing|s)? (?:in|with))(.+)", sentence):
            add(_condition("serial_number", "suffix", _values(match[1].split(" The ")[0]), scope))
        elif match := re.search(r"(?i:serial numbers?[^.]*?\b(?:start|begin)(?:ning|ing|s)? with)[^\"“]*[\"“]([A-Z0-9]+)", sentence):
            add(_condition("serial_number", "prefix", [match[1]], scope))
        elif match := re.search(r"(?i:serial numbers?)[: ]+((?:%s(?:, ?| / | and | or | )?){2,})" % _CODE, sentence):
            # A table row names its own models: "MXB008 GK2309880, GK2309899 DBL6000M / DBL6000P GK2309901".
            rows, target = [], scope
            tokens = re.findall(_CODE, match[1])
            shape = lambda token: re.sub(r"[A-Z]", "A", re.sub(r"\d", "9", token))
            usual = max(map(shape, tokens), key=[shape(x) for x in tokens].count)
            for token in tokens:
                # Serial numbers in one table share a shape; a differently shaped code heads a row as its model.
                if any(_loose(token) == _loose(m) for m in models) or shape(token) != usual:
                    if rows and rows[-1][1]:
                        rows.append(([token], []))
                    elif rows:
                        rows[-1][0].append(token)
                    else:
                        rows.append(([token], []))
                else:
                    if not rows:
                        rows.append((list(target), []))
                    rows[-1][1].append(token)
            for row_models, values in rows:
                if values:
                    add(_condition("serial_number", "one_of", values, row_models))
        elif re.search(r"(?i)qualifying serial numbers?|check (?:their|the|your) serial numbers? at|do not contain a serial number|only [^.]*serial numbers?", sentence):
            url = re.search(r"https?://[^\s,)]+", sentence)
            rule = "external" if re.search(r"(?i)qualifying|check (?:their|the|your) serial", sentence) else "manual"
            add(_condition("serial_number", rule, models=scope, text=sentence.strip(), url=url[0].rstrip(".") if url else None))
        if match := re.search(r"(?i:date codes? \([A-Z/]+\): |manufactur(?:ing|e) dates? of )((?:\d[\d/]{3,}(?:, ?| or | and )*)+)", sentence):
            add(_condition("date_code", "one_of", re.findall(r"\d[\d/]{3,}", match[1]), scope))
        elif re.search(r"(?i)date codes? (?:included|are)\b", sentence):
            add(_condition("date_code", "manual", models=scope, text=sentence.strip()))
    return found


def _compare(condition, product, region):
    """One official condition against what the user entered: match, mismatch, or unknown when nothing was entered."""
    rule, values = condition.get("rule") or ("region" if condition["field"] == "sale_region" else "external"), condition.get("values", [])
    if rule == "region":
        return region, REGION_NAMES.get(product.sale_region, "그 밖의 지역" if product.sale_region else None)
    if rule in {"external", "manual"}:
        state = {"affected": "match", "not_affected": "mismatch"}.get(product.official_check, "unknown")
        return state, {"match": "대상이라고 확인함", "mismatch": "대상이 아니라고 확인함"}.get(state)
    entered = getattr(product, condition["field"], None)
    if not entered:
        return "unknown", None
    if condition["field"] == "date_code":
        digits = re.sub(r"\D", "", entered)
        return ("match" if any(digits == re.sub(r"\D", "", v) for v in values) else "mismatch"), entered
    serial = _loose(entered)
    # A label prints "SN: FSD230203D"; the marker is not part of the number.
    forms = {serial, re.sub(r"^S/?N[:.]?", "", serial)}
    test = {"suffix": str.endswith, "prefix": str.startswith, "one_of": lambda s, v: v in s}[rule]
    return ("match" if any(test(s, _loose(v)) for s in forms for v in values) else "mismatch"), entered


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
    brands = []
    makers = [company["Name"] for key in ("Manufacturers", "Importers", "Distributors") for company in (record.get(key) or [])
              if isinstance(company, dict) and isinstance(company.get("Name"), str) and company["Name"].strip()]
    for item in record.get("Products") or []:
        # "Casely Wireless Portable Power Banks" -> "Casely": the leading words before a generic product word.
        lead = []
        for word in (item.get("Name") or "").split():
            if word.strip(",.()").upper() in _GENERIC_WORDS or any(c.isdigit() for c in word):
                break
            lead.append(word)
        if 0 < len(lead) <= 3:
            # "Anker PowerCore" is brand + product line when the maker is "Anker Innovations Limited".
            brands.append(lead[0] if len(lead) > 1 and any(_norm(lead[0]) == _norm(name.split()[0].strip(",.")) for name in makers) else " ".join(lead))
    # "model numbers A1642, A1647 and A1652" names three models; a capitalised word ("They") ends the list.
    lists = re.findall(r"(?i:models?(?: numbers?| nos?\.?)?)[:\s]+[\"“]?((?:[A-Z0-9][A-Z0-9-]{2,}(?![a-z])[\"”]?(?:, | and | or | / )?)+)", "\n".join(search))
    models = list(dict.fromkeys(m for found in lists for m in re.findall(r"[A-Z0-9][A-Z0-9-]{2,}", found) if any(c.isdigit() for c in m)))
    remedy = "·".join(dict.fromkeys(REMEDIES[o["Option"]] for o in record.get("RemedyOptions") or [] if isinstance(o, dict) and o.get("Option") in REMEDIES))
    return {
        "brands": list(dict.fromkeys(brands)), "models": models,
        "id": "cpsc-" + str(identity), "title": record["Title"], "source_url": record["URL"],
        "source_id": "cpsc", "published_at": published,
        "last_publish_at": record.get("LastPublishDate"), "fetched_at": fetched_at,
        "content_hash": _digest(record), "evidence_kind": "official_api",
        "review_status": "pending", "search_text": "\n".join(search),
        "action": (f"공식 공지의 조치: {remedy}. " if remedy else "") + "신청 방법과 연락처는 공식 리콜 공지에 있습니다.",
        "conditions": _extract_conditions(record.get("Description") or "", models), "raw": record,
    }


def _kr_notice(record, fetched_at):
    if not isinstance(record, dict) or not str(record.get("recallUid") or "").strip():
        raise ValueError("invalid_record")
    text = lambda key: record[key].strip() if isinstance(record.get(key), str) else ""
    models = [m.strip() for m in text("recallModelName").split(",") if m.strip()]
    brands = list(dict.fromkeys(b for b in (text("recallBrandName"), text("makerName"), text("recallCmpnyName")) if b))
    published = text("publishDate")
    action = text("publishActionDscr") or "Safety Korea 리콜 공지에서 조치 방법을 확인해주세요."
    if text("recallInqryTel"):
        action += f" 문의: {text('recallInqryTel')}"
    return {"id": "kr-" + str(record["recallUid"]).strip(),
            "title": f"{brands[0] if brands else ''} {text('recallProductName') or '제품'} 리콜 공지 (국내)".strip(),
            "brands": brands, "models": models, "source_id": "safety-korea", "source_url": KR_BOARD,
            "published_at": f"{published[:4]}-{published[4:6]}-{published[6:8]}" if len(published) == 8 else published,
            "fetched_at": fetched_at, "content_hash": _digest(record), "evidence_kind": "official_api_kr",
            "review_status": "pending", "action": action, "harm": text("harmDscr"), "conditions": [],
            "search_text": "\n".join([text("recallProductName"), *brands, *models, text("harmDscr")])}


class OfficialService:
    def __init__(self, data_dir, *, transport=None, clock=None, curated_path=CURATED_PATH, auto_refresh=False):
        self.data_dir = Path(data_dir)
        self.transport = transport
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.curated_path = Path(curated_path)
        self.lock = threading.Lock()
        # Serverless instances start with an empty cache; with auto_refresh the first lookup fetches the public lists itself.
        self.auto_refresh = auto_refresh
        self.kr_key = os.environ.get("SAFETYKOREA_API_KEY", "").strip()
        self.kr_query = os.environ.get("SAFETYKOREA_QUERY", "보조배터리").strip()

    def _load_kr(self):
        cache = _read(self.data_dir / "safetykorea-cache.json", {})
        meta = _read(self.data_dir / "safetykorea-status.json", {})
        notices = []
        try:
            if isinstance(cache, dict) and cache:
                if _digest(cache["records"]) != cache["records_hash"]:
                    raise ValueError("cache_integrity")
                notices = [_kr_notice(record, cache["fetched_at"]) for record in cache["records"]]
        except (KeyError, ValueError, TypeError):
            cache, notices = {}, []
        return (cache if isinstance(cache, dict) else {}), (meta if isinstance(meta, dict) else {}), notices

    def _sync_kr(self):
        """Fetch the domestic recall list for the configured product name. The key travels only in the request header."""
        now = self.clock().isoformat()
        error = "Safety Korea에 연결하지 못했습니다. 저장된 정보를 그대로 사용합니다."
        for scheme in ("https://", "http://"):  # The specification lists http; https is tried first to protect the key.
            try:
                with httpx.Client(timeout=httpx.Timeout(10, connect=5), follow_redirects=False, transport=self.transport) as client:
                    response = client.get(scheme + KR_PATH, params={"conditionKey": "recallProductName", "conditionValue": self.kr_query},
                                          headers={"AuthKey": self.kr_key, "Accept": "application/json"})
                    response.raise_for_status()
                    if len(response.content) > MAX_BYTES:
                        raise ValueError("response_too_large")
                    body = response.json()
                code = str(body.get("resultCode"))
                if code == "2004":
                    records = []
                elif code == "2000":
                    records = body.get("resultData") or []
                else:
                    error = KR_ERRORS.get(code, f"Safety Korea가 오류를 반환했습니다 (코드 {code}).")
                    break
                if not isinstance(records, list) or len(records) > MAX_RECORDS:
                    raise ValueError("invalid_shape")
                for record in records:
                    _kr_notice(record, now)
                records.sort(key=lambda record: str(record["recallUid"]))
                _atomic(self.data_dir / "safetykorea-cache.json", {"fetched_at": now, "records": records, "records_hash": _digest(records),
                                                                    "query": self.kr_query})
                _atomic(self.data_dir / "safetykorea-status.json", {"state": "available", "last_attempt_at": now, "error": None})
                return True
            except httpx.RequestError:
                continue
            except httpx.HTTPStatusError as exc:
                error = f"Safety Korea 서버가 응답하지 않았습니다 (오류 {exc.response.status_code}). 저장된 정보를 그대로 사용합니다."
                break
            except (ValueError, TypeError, KeyError, AttributeError, OSError):
                error = "Safety Korea에서 받은 정보를 확인하지 못했습니다. 저장된 정보를 그대로 사용합니다."
                break
        _atomic(self.data_dir / "safetykorea-status.json", {"state": "failed", "last_attempt_at": now, "error": error})
        return False

    def _refresh_cpsc_if_due(self):
        if not self.auto_refresh:
            return
        cache, meta, _, _ = self._load()
        now = self.clock()
        attempted = _time(meta.get("last_attempt_at"))
        if _fresh(cache.get("fetched_at"), now) or (attempted and now - attempted < CPSC_RETRY):
            return
        self._sync_cpsc()

    def _refresh_kr_if_due(self):
        # Serverless instances start with an empty cache, so the first lookup fills it instead of waiting for a manual refresh.
        if not self.kr_key:
            return
        cache, meta, _ = self._load_kr()
        now = self.clock()
        attempted = _time(meta.get("last_attempt_at"))
        if _fresh(cache.get("fetched_at"), now) or (attempted and now - attempted < KR_RETRY):
            return
        if self.lock.acquire(blocking=False):
            try:
                self._sync_kr()
            finally:
                self.lock.release()

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
        # The API copy is the current text; the reviewed copy of the same notice supplies its checked conditions and identifiers.
        reviewed = {notice["source_url"]: notice for notice in curated_notices}
        for notice in notices:
            extra = reviewed.get(notice["source_url"])
            if extra:
                notice.update({key: extra[key] for key in ("conditions", "action", "manufacturer_url", "brands", "models") if extra.get(key)})
        combined = notices + [notice for notice in curated_notices if notice["source_url"] not in urls] + self._load_kr()[2]
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
            self._kr_source(now),
        ]
        return {"state": state, "sources": sources, "notice_count": len(notices), "notices_count": len(notices),
                "catalog_hash": _digest([(n["id"], n["content_hash"]) for n in notices]),
                "last_success_at": last_success, "last_attempt_at": meta.get("last_attempt_at"),
                "coverage_complete": False, "freshness_days": FRESH_FOR.days}

    def _kr_source(self, now):
        source = {"id": "safety-korea", "name": "Safety Korea (국내)", "source_url": KR_BOARD, "synthetic": False,
                  "setup_url": "https://www.safetykorea.kr/release/openapi2", "last_success_at": None, "notices_count": 0, "error": None}
        if not self.kr_key:
            return source | {"state": "unconfigured", "scope": "국내 리콜은 Safety Korea 사이트에서 직접 확인해주세요. 자동 확인은 준비 중입니다."}
        cache, meta, notices = self._load_kr()
        state = meta.get("state", "not_synced")
        if state == "available" and not _fresh(cache.get("fetched_at"), now):
            state = "stale"
        return source | {"state": state, "last_success_at": cache.get("fetched_at"), "last_attempt_at": meta.get("last_attempt_at"),
                         "scope": f"국내 리콜 공지 중 제품명에 '{self.kr_query}'가 들어간 {len(notices)}건",
                         "notices_count": len(notices), "count": len(notices), "error": meta.get("error")}

    def brands_for_model(self, model):
        """Brands that official notices list for exactly this model; used to fill in a brand a photo did not show."""
        self._refresh_cpsc_if_due()
        _, _, _, notices = self._load()
        brands = [notice["brands"][0] for notice in notices
                  if notice.get("brands") and _has_model(model, notice)]
        return list(dict.fromkeys(b.title() if b.isupper() and len(b) > 4 else b for b in brands))

    def models_in_text(self, text):
        """Models from official notices that appear word-for-word in text read off a label; offered for the user to confirm."""
        _, _, _, notices = self._load()
        listed = dict.fromkeys((notice["brands"][0] if notice.get("brands") else "", model) for notice in notices for model in notice.get("models", []) if len(model) >= 5)
        return [{"brand": brand, "model": model} for brand, model in listed if _token(model, text)][:5]

    def models_for_product(self, brand, product_name=None):
        """Official notices for a brand (and product name) whose model the photo did not show; candidates to check, not a match."""
        _, _, _, notices = self._load()
        found = []
        for notice in notices:
            text = _norm(notice.get("search_text") or notice["title"] + " " + " ".join(notice.get("brands", [])))
            if not any(_norm(brand) == _norm(b) for b in notice.get("brands", [])) and not _token(brand, text):
                continue
            if product_name and _norm(product_name) not in text:
                continue
            found.extend((notice["brands"][0] if notice.get("brands") else brand, model) for model in notice.get("models", []))
        return [{"brand": b, "model": m} for b, m in dict.fromkeys(found)][:5]

    def documents(self):
        """Retrieval corpus for the assistant: stored notices only, no network."""
        _, _, _, notices = self._load()
        documents = []
        for notice in notices:
            if notice["evidence_kind"] == "official_api_kr":
                parts = [" ".join(notice["brands"] + notice["models"]), notice["search_text"], notice["action"]]
            elif notice["evidence_kind"] == "official_web_curated":
                parts = [" ".join(notice["brands"] + notice["models"]), notice["action"],
                         *(c["text"] for c in notice.get("conditions", [])), *notice.get("excerpts", [])]
            else:
                parts = [notice["search_text"][:1500], notice["action"]]
            documents.append({"id": notice["id"], "title": notice["title"], "url": notice["source_url"],
                              "published_at": notice["published_at"], "text": "\n".join(parts)})
        return documents

    def sync(self):
        """Refreshes every configured official source; no product data is sent."""
        result = self._sync_cpsc()
        if self.kr_key:
            with self.lock:
                result["domestic_ok"] = self._sync_kr()
        return self.status() | {key: result[key] for key in ("ok", "error", "fetched_count", "domestic_ok") if key in result}

    def _sync_cpsc(self):
        with self.lock:
            now = self.clock().isoformat()
            error = "공식 API 조회 실패"
            for attempt in range(2):
                try:
                    raw, merged = b"", {}
                    with httpx.Client(timeout=httpx.Timeout(12, connect=5), follow_redirects=False,
                                      transport=self.transport) as client:
                        for query in API_QUERIES:
                            with client.stream("GET", API_URL, params=API_PARAMS | {"ProductName": query},
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
                            part = b"".join(chunks)
                            raw += part
                            found = json.loads(part)
                            if not isinstance(found, list) or len(found) > MAX_RECORDS:
                                raise ValueError("invalid_catalog_shape")
                            keys = [str(r.get("RecallNumber") or r.get("RecallID")) if isinstance(r, dict) else None for r in found]
                            if None in keys or len(set(keys)) != len(keys):
                                raise ValueError("duplicate_recall_id")
                            for record in found:
                                # The same recall can come back under both wordings; keep one copy.
                                merged.setdefault(str(record.get("RecallNumber") or record.get("RecallID")), record)
                    records = list(merged.values())
                    parsed = [_api_notice(record, now) for record in records]
                    if len({n["id"] for n in parsed}) != len(parsed):
                        raise ValueError("duplicate_recall_id")
                    records.sort(key=lambda record: str(record.get("RecallNumber") or record.get("RecallID")))
                    records_hash = _digest(records)
                    cache = {"fetched_at": now, "records": records, "records_hash": records_hash,
                             "response_hash": hashlib.sha256(raw).hexdigest(),
                             "request_url": API_URL, "request_params": API_PARAMS | {"ProductName": list(API_QUERIES)}}
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
        if product.model and product.model_confirmed and product.product_kind == "power_bank":
            self._refresh_cpsc_if_due()
            self._refresh_kr_if_due()
        now = self.clock()
        status = self.status()
        _, _, _, notices = self._load()
        result = {"checked_at": now.isoformat(), "synthetic": False,
                  "catalog_version": "cpsc-candidates-v1", "catalog_hash": status["catalog_hash"],
                  "status": "incomplete", "label": LABELS["incomplete"], "notices": [], "questions": [], "fields": [], "required_fields": [],
                  "sources": status["sources"], "risk": risk_guidance(product.signs, product.signs_answered),
                  "limitations": "지금은 미국(CPSC) 공식 리콜 공지 일부만 확인할 수 있습니다. 국내(Safety Korea) 정보는 아직 연결되지 않았습니다. 리콜 공지를 찾지 못했다고 해서 제품이 반드시 안전한 것은 아닙니다.",
                  "next_action": "확인할 수 있는 공지에서는 이 제품을 찾지 못했습니다. 리콜이 없다는 뜻은 아니니 Safety Korea와 제조사 사이트에서도 확인해주세요.",
                  "coverage_complete": False}
        # The model identifies a recall notice; a missing brand narrows nothing and must not block the lookup.
        if not product.model or not product.model_confirmed or product.product_kind != "power_bank":
            result.update(status="needs_identification", label=LABELS["needs_identification"],
                          next_action="제품 라벨에서 모델명을 확인해 입력해주세요. 사진에서 읽은 값은 라벨과 같은지 확인이 필요합니다.")
            return result
        domestic = next(source for source in status["sources"] if source["id"] == "safety-korea")
        if domestic["state"] == "available":
            result["limitations"] = "미국(CPSC) 공식 리콜 공지 일부와 국내(Safety Korea) 보조배터리 리콜 공지를 확인했습니다. 리콜 공지를 찾지 못했다고 해서 제품이 반드시 안전한 것은 아닙니다."
            result["next_action"] = "확인한 공지에서는 이 제품을 찾지 못했습니다. 리콜이 없다는 뜻은 아니니 제조사 안내도 함께 확인해주세요."
        for notice in notices:
            brand_state = "match"
            if notice["evidence_kind"] == "official_api_kr":
                # Domestic notices often write the brand in Korean or name the importer, so only the model must match exactly.
                candidate = _has_model(product.model, notice)
                known = [_norm(b) for b in notice["brands"]]
                brand_state = "match" if product.brand and any(_norm(product.brand) in b or b in _norm(product.brand) for b in known) else "skipped"
            elif notice["evidence_kind"] == "official_web_curated":
                candidate = _has_model(product.model, notice) and (
                    not product.brand or _norm(product.brand) in {_norm(b) for b in notice["brands"]})
                brand_state = "match" if product.brand else "skipped"
            else:
                candidate = _has_model(product.model, notice) and (not product.brand or _token(product.brand, notice["search_text"]))
                brand_state = "match" if product.brand else "skipped"
            if not candidate:
                continue
            home, region = _region_state(product.sale_region, notice)
            listed = next((m for m in notice.get("models", []) if _loose(m) == _loose(product.model)), None)
            # Conditions the notice ties to another model of the same recall do not apply to this product.
            conditions = [c for c in notice.get("conditions", []) if not c.get("models") or any(_loose(m) == _loose(product.model) for m in c["models"])]
            checks = [{"field": "brand", "state": brand_state, "input": product.brand or None, "expected": [product.brand] if brand_state == "match" and not any(_norm(product.brand) == _norm(b) for b in notice.get("brands", [])) else notice.get("brands") or ["공지에 브랜드 표기 없음"],
                       "basis": "공식 공지에 적힌 브랜드"},
                      {"field": "model", "state": "match", "input": product.model, "expected": [listed or product.model], "basis": "접미사·하이픈을 보존한 모델 식별자 대조"}]
            required = []
            for condition in conditions:
                state, shown = _compare(condition, product, region)
                checks.append({"field": condition["field"], "state": state, "input": shown, "expected": condition["expected"], "basis": condition["text"]})
                # A condition only the maker's page can answer is asked as a yes/no result; the others as the value itself.
                ask = "official_check" if (condition.get("rule") or "external") in {"external", "manual"} and condition["field"] != "sale_region" else condition["field"]
                question = {"notice_id": notice["id"], "field": ask, "text": condition["text"],
                            "label": FIELD_NAMES[condition["field"]], "hint": FIELD_HINTS.get(condition["field"]),
                            "url": condition.get("url") or (notice.get("manufacturer_url") if ask == "official_check" else None)}
                result["fields"].append(question)
                if state == "unknown":
                    required.append(ask)
                    result["questions"].append(question)
            if product.sale_region and not any(c["field"] == "sale_region" for c in conditions):
                checks.append({"field": "sale_region", "state": region, "input": REGION_NAMES.get(product.sale_region, "그 밖의 지역"),
                               "expected": [f"{REGION_NAMES[home]} 판매 제품"], "basis": f"이 공지는 {REGION_NAMES[home]}에서 판매된 제품 기준입니다."})
            if not conditions:
                checks.append({"field": "extra", "state": "match", "input": "없음", "expected": ["없음"], "basis": "이 공지는 모델명만으로 대상 제품을 정합니다."})
            states = {c["state"] for c in checks[2:] if c["field"] != "sale_region" or any(k["field"] == "sale_region" for k in conditions)}
            outcome = "mismatch" if "mismatch" in states else "pending_review" if "unknown" in states else "match"
            # Bought in another country: the same model, but this notice's remedy may not apply there. Any other mismatch rules the notice out.
            others = {c["state"] for c in checks[2:] if c["field"] != "sale_region"}
            if region == "mismatch" and "mismatch" not in others:
                outcome = "other_market"
            item = {key: value for key, value in notice.items() if key not in {"raw", "search_text", "brands", "models", "excerpts"}}
            item.update(checks=checks, outcome=outcome, region=region, required_fields=list(dict.fromkeys(required)), conditions=conditions,
                        market=REGION_NAMES[home], review_status="pending", stale=not _fresh(notice["fetched_at"], now))
            result["notices"].append(item)
        outcomes = {n["outcome"] for n in result["notices"]}
        result["required_fields"] = list(dict.fromkeys(f for n in result["notices"] for f in n["required_fields"]))
        if result["notices"]:
            hit = next((n for n in result["notices"] if n["outcome"] == "match"), None)
            if all(n["stale"] for n in result["notices"]):
                result["next_action"] = "저장된 리콜 정보가 만료됐습니다. 잠시 후 최신 정보로 다시 확인해주세요."
            elif hit:
                basis = "입력한 정보가 공식 리콜 조건과 모두 일치합니다." if hit["conditions"] else "공식 리콜 정보를 확인했어요. 이 제품은 모델명만으로 리콜 대상 여부를 확인할 수 있습니다."
                market = "" if hit["region"] == "match" else f" 이 공지는 {hit['market']}에서 판매된 제품 기준이라, 다른 나라에서 샀다면 교환·환불 절차는 제조사에 문의해주세요."
                result.update(status="matched", label=LABELS["matched"],
                              next_action=basis + " 사용과 충전을 중지하고 아래 조치 방법을 따라주세요." + market)
            elif "other_market" in outcomes and "pending_review" not in outcomes:
                result.update(status="needs_information", label="구매 국가가 공지 대상과 다릅니다",
                              next_action="모델명은 리콜 제품과 일치하지만 구매 국가가 공지 대상과 다릅니다. 같은 모델이므로 사용을 멈추고 제조사에 국내 조치가 있는지 문의해주세요.")
            elif "pending_review" in outcomes:
                names = [FIELD_NAMES[f] for f in result["required_fields"]]
                asked = "아래 항목을 알려주세요: " + ", ".join(names) + "."
                result.update(status="needs_information", label=("·".join(n.split("(")[0] for n in names) + " 확인이 필요합니다"),
                              next_action="모델명은 리콜 제품과 일치합니다. 정확한 확인을 위해 " + asked)
            else:
                result.update(status="no_match", label=LABELS["no_match"],
                              next_action="모델명은 같지만 입력한 정보가 공식 리콜 조건과 다릅니다. 이 공지의 대상이 아닙니다. 새 리콜이 등록될 수 있으니 제품을 저장해 두고 다시 확인해주세요.")
        elif status["sources"][0]["count"]:
            # The official list was read and holds nothing for this model: a real "none found", with its scope stated.
            covered = "미국(CPSC)과 국내(Safety Korea)" if domestic["state"] == "available" else "미국(CPSC)"
            result.update(status="no_match", label=LABELS["no_match"],
                          limitations=f"{covered}의 보조배터리 공식 리콜 공지 {status['notice_count']}건을 확인했습니다."
                          + ("" if domestic["state"] == "available" else " 국내(Safety Korea) 리콜은 포함되지 않았습니다.")
                          + " 리콜 공지가 없다고 해서 제품이 반드시 안전한 것은 아닙니다.",
                          next_action="확인한 공식 정보에서는 이 제품과 일치하는 리콜을 찾지 못했습니다. "
                          + ("" if domestic["state"] == "available" else "국내에서 구매했다면 Safety Korea에서도 확인해주세요. ")
                          + "앞으로 새 리콜이 등록될 수 있으니 제품을 저장해 두면 다시 확인하기 쉽습니다.")
        else:
            # An empty list is not a negative result: say the data is missing, not that nothing was found.
            result["next_action"] = "공식 리콜 데이터를 아직 불러오지 못했습니다. 현재 리콜 여부를 확인할 수 없습니다. 잠시 후 다시 확인해주세요."
        if status["state"] == "failed" and result["status"] != "incomplete":
            result["next_action"] += " 최신 리콜 정보를 불러오는 데 실패해 저장된 정보로 확인했습니다."
        return result
