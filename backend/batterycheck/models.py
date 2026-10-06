from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


Sign = Literal["smoke", "flame", "swelling", "damage", "discoloration", "heat", "odor", "sound"]


class ProductInput(StrictModel):
    brand: str = Field(default="", max_length=80)
    model: str = Field(default="", max_length=80)
    model_confirmed: StrictBool = False
    product_kind: Literal["power_bank", "other"] = "power_bank"
    demo: StrictBool = False
    manufactured_on: date | None = None
    sale_region: str | None = Field(default=None, max_length=32)
    lot: str | None = Field(default=None, max_length=32)
    signs: list[Sign] = Field(default_factory=list, max_length=8)
    signs_answered: StrictBool = False
    scenario: Literal["complete", "outage", "stale"] = "complete"

    @field_validator("manufactured_on", mode="before")
    @classmethod
    def exact_date(cls, value):
        if value is None or isinstance(value, date):
            return value
        # A purchase date, timestamp, or a month must never become a manufacturing date.
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError("제조일은 YYYY-MM-DD 형식의 정확한 날짜로 입력하세요.")
        return date.fromisoformat(value)

    @model_validator(mode="after")
    def validate_answers(self):
        if self.manufactured_on and self.manufactured_on > datetime.now(timezone.utc).date():
            raise ValueError("미래 제조일을 입력할 수 없습니다.")
        if self.signs and not self.signs_answered:
            raise ValueError("위험징후 응답 여부를 확인하세요.")
        return self


class RiskInput(StrictModel):
    signs: list[Sign] = Field(default_factory=list, max_length=8)
    signs_answered: StrictBool = False


class ActionInput(StrictModel):
    state: Literal["unconfirmed", "checking", "user_completed"]


class Condition(StrictModel):
    field: Literal["manufactured_on", "sale_region", "lot"]
    operator: Literal["date_range", "one_of"]
    values: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_condition(self):
        if self.field == "manufactured_on":
            if self.operator != "date_range" or len(self.values) != 2:
                raise ValueError("제조일 조건의 형식이 올바르지 않습니다.")
            start, end = [date.fromisoformat(v) for v in self.values]
            if start > end:
                raise ValueError("제조일 범위가 뒤집혀 있습니다.")
        elif self.operator != "one_of":
            raise ValueError("문자 조건은 정확 일치만 지원합니다.")
        return self


class Notice(StrictModel):
    id: str
    title: str
    brand: str
    models: list[str] = Field(min_length=1)
    review_status: Literal["reviewed", "pending"]
    conditions: list[Condition]
    action: str


class Source(StrictModel):
    id: str
    name: str
    state: Literal["complete", "failed", "unconfigured"]
    last_success_at: datetime | None
    scope: str
    synthetic: bool

    @field_validator("last_success_at")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("출처 시각에는 시간대가 필요합니다.")
        return value


class Catalog(StrictModel):
    version: str
    source: Source
    notices: list[Notice]

    @model_validator(mode="after")
    def demo_only(self):
        if not self.source.synthetic:
            raise ValueError("이 프로토타입은 가상 공지만 지원합니다.")
        ids = [n.id for n in self.notices]
        if len(ids) != len(set(ids)):
            raise ValueError("공지 ID가 중복되었습니다.")
        if any(n.brand != "DEMO LAB" or not all(m.startswith("DEMO PB-") for m in n.models)
               for n in self.notices):
            raise ValueError("가상 공지에는 실제 브랜드를 넣을 수 없습니다.")
        return self


class LoginInput(BaseModel):
    # Passwords are never trimmed or otherwise normalized.
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=128)

    @field_validator('username')
    @classmethod
    def normalize_username(cls, value):
        import re
        normalized = value.strip().lower()
        if not re.fullmatch(r'[a-z0-9_.-]{3,32}', normalized):
            raise ValueError('아이디는 영문 소문자, 숫자, _, ., -를 사용해 3~32자로 입력하세요.')
        return normalized


class RegisterInput(LoginInput):
    password: str = Field(min_length=10, max_length=128)
    privacy_consent: StrictBool
    notification_consent: StrictBool = False

    @field_validator('privacy_consent')
    @classmethod
    def consent_required(cls, value):
        if not value:
            raise ValueError('계정과 제품 저장을 위한 개인정보 처리에 동의해야 합니다.')
        return value


class PreferencesInput(StrictModel):
    notification_consent: StrictBool | None = None
    ai_consent: StrictBool | None = None


class AssistantInput(StrictModel):
    question: str = Field(min_length=1, max_length=1000)


class FeedbackInput(StrictModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default='', max_length=1000)


class DeleteAccountInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    password: str = Field(min_length=1, max_length=128)


class IdentifyInput(StrictModel):
    image_base64: str = Field(min_length=1, max_length=6 * 1024 * 1024)
