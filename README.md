# 배터리체크

보조배터리의 공식 리콜 공지 조건을 내 제품과 대조하고, AI 안내 도우미가 근거와 다음 행동을 쉬운 말로 설명하는 웹 서비스입니다. 확인한 제품은 보관함에 남기고 공지가 바뀌면 다시 알려 줍니다. **배터리 내부 상태·화재 확률·안전 점수를 산출하지 않으며 리콜 대상을 확정하지 않습니다.**

코디세이 AI 네이티브 Final Project(자율 주제) 결과물입니다. 요건별 충족 여부는 [준비 상태](docs/CONTEST_READINESS.md#4-코디세이-final-project-요건-대응)에 있습니다.

## 팀원 및 역할

| 이름 | GitHub | 역할 | 담당 업무 |
|---|---|---|---|
| 박세진 | [parkhuk20-dot](https://github.com/parkhuk20-dot) | PM · Backend · AI | 서비스 기획과 일정 관리, FastAPI 백엔드, AI 안내 도우미(에이전트·문서 검색·대화 기억), 배포(Vercel·Turso) |
| 김민주 | [zzangminju](https://github.com/zzangminju) | Frontend · UX | 화면 구성과 반응형 레이아웃, 사용자 문구와 접근성 점검, 디자인 개선, 발표 자료와 시연 준비 |
| 한다혜 | [hdahye1130](https://github.com/hdahye1130) | Data · QA | 공식 리콜 출처 조사와 관리(CPSC·Safety Korea), 사용자 테스트 진행과 피드백 정리, 테스트 시나리오와 문서 |

## 기술 스택

- **AI**: OpenAI 호환 Chat Completions API(코디세이 게이트웨이, 도구 사용 에이전트), 자체 문서 검색, 로컬 OCR(Apple Vision / Tesseract)
- **Backend**: Python, FastAPI, SQLite
- **Frontend**: Next.js, React (PWA)
- **Infra**: Vercel 컨테이너 함수(`Dockerfile.vercel`), Turso(libSQL) DB. 자체 서버용 Docker Compose·Caddy 설정도 포함

선정 이유는 [구현 구조](docs/ARCHITECTURE.md#기술-선정-이유)에 있습니다.

## 시스템 아키텍처

```mermaid
flowchart LR
    U[사용자] --> W[Next.js 웹] --> P[API 프록시] --> A[FastAPI]
    A --> E[조건 대조 규칙 엔진]
    A --> H[AI 안내 도우미]
    H -->|AI 이용 동의 시| L[AI API]
    H --> R[공식 공지·안전 안내 검색]
    H --> M[계정별 대화 기억·보관함]
    J[정기 갱신 worker] --> R
    J --> N[앱내 알림]
```

상세 구조와 API는 [구현 구조](docs/ARCHITECTURE.md)를 참고하세요.

## 핵심 AI 활용

1. **AI Agent** — 질문을 받으면 모델이 `search_official_documents`, `get_my_products`, `check_product` 중 필요한 도구를 골라 호출하고 결과를 종합합니다.
2. **RAG** — 저장된 CPSC 공지와 소방청·산업통상자원부 안전 안내를 검색해 그 내용만 근거로 답하고, 참고한 자료의 링크를 답변 아래에 표시합니다.
3. **Long-term Memory** — 계정별 대화를 저장해 다음 질문에 이전 맥락을 이어 가고, 보관함의 제품·조치 이력을 조회합니다.
4. **자동화 워크플로우** — worker가 공지 수집 → 저장 제품 재확인 → 변경 감지 → 앱내 알림을 주기적으로 실행합니다(LLM 미사용).

AI 윤리: 모든 답변에 "AI 생성 답변 · 공식 판정 아님"을 표시합니다. AI 도우미는 가입 동의와 별개의 동의를 받은 계정에서만 작동하고, 그때만 질문과 보관함 요약(브랜드·모델·판매지역·제조일·로트·조치 상태·확인 결과)을 외부 AI API에 보냅니다. 사진은 보내지 않습니다. 리콜 후보 판정과 위험징후 문구는 AI가 아니라 규칙과 검토된 문서가 정합니다.

**검증 범위:** 에이전트는 가짜 클라이언트를 쓴 자동 테스트로 확인했습니다. 2026-10-06에 로컬에서 코디세이 게이트웨이의 `gpt-5.4`로 실제 질문 2건(보관함 조회·출처 표시, 이전 대화 기억)을 확인했습니다. 답변 품질을 체계적으로 평가한 것은 아닙니다.

## 현재 기능

- AI 안내 도우미(동의 계정), 대화 기록 조회·삭제, 사용 의견 보내기
- 계정 가입·로그인·로그아웃, 사용자별 제품·이력·알림 격리
- 사진의 브랜드·모델 후보 추출 → 사용자 직접 확인 → 조건 대조
- CPSC 공식 웹에서 확인한 Anker A1263 / INIU BI-B41 공지 후보와 원문·미확인 조건 표시
- CPSC 공개 API 어댑터, 오류·만료·원문 해시·변경 버전 관리
- 제품 보관함, 재확인 이력, 사용자 조치 자기보고, 삭제
- 수신 동의 기반 앱내 알림, 변경 중복 방지, 선택적 정기 갱신 worker
- 개인정보 내보내기·계정 삭제, 반응형 화면, PWA 공개 오프라인 안내
- 별도 가상 DEMO LAB 제품의 조건 판정 시연

**연동 현황:** CPSC API의 실제 공개 요청은 HTTP 503을 반환했습니다. 공식 웹 확인 자료 2건을 API 성공처럼 취급하지 않습니다. Safety Korea는 인증키·승인이 없어 미연결입니다. 실제 제품은 후보 검색 후 추가 확인으로 안내하며 해외 공지가 국내 회수 자격을 보장하지 않습니다. 공식 자료에 없는 제품도 등록할 수 있으나 조회 미완료가 안전 판정으로 바뀌지 않습니다.

## 실행 방법

요구사항: Python 3.13+ (3.14에서 테스트), Node.js 20+

```sh
git clone <저장소 URL>
cd batterycheck
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
npm ci --prefix frontend --ignore-scripts --no-audit --no-fund
NEXT_TELEMETRY_DISABLED=1 npm run build --prefix frontend
export OPENAI_API_KEY=...   # AI 도우미용. 없으면 도우미만 꺼지고 나머지는 동작
python3 scripts/run_local.py
```

웹: `http://127.0.0.1:3114` / API: `http://127.0.0.1:8814`. 두 포트가 비어 있어야 하며 종료는 Ctrl+C입니다. `run_local.py`는 프로젝트의 `.venv`를 먼저 쓰고, 다른 환경은 `BATTERYCHECK_PYTHON=/absolute/path/to/python`으로 지정합니다. 프로젝트 루트의 `.env`(Git 제외)가 있으면 읽으며, 셸에 설정한 값이 우선합니다. 코디세이 API 콘솔의 OpenAI 호환 키를 쓰려면 `.env.example`의 `OPENAI_BASE_URL` 예시를 따르세요. 모델은 `BATTERYCHECK_AI_MODEL`로 바꿀 수 있습니다(기본 `gpt-5.4`).

Mac OCR은 Apple Vision과 Swift 컴파일러를 사용합니다. 첫 실행 시 프로젝트의 `.data/ocr`에 인식 실행 파일을 생성합니다. Linux에서는 Tesseract가 필요하며 배포용 Dockerfile에 설치 과정이 있습니다. 두 엔진은 별도이며 실제 검증한 엔진과 데이터 범위는 [최종 검증 기록](verification/final_report.md)에 기재합니다. 사진은 4MB·2천만 화소 이하 PNG/JPEG만 지원하고, 인식 후 임시 원본을 삭제합니다. 사진은 외부 AI에 전송하지 않습니다.

## 사용 순서

1. 첫 화면에서 브랜드와 모델명을 입력하고 `리콜 정보 확인`을 누릅니다. 로그인 없이 할 수 있습니다.
2. 로그인했다면 `사진으로 확인`으로 라벨 사진을 올릴 수 있습니다. 읽은 값을 보여 주고 `맞아요, 리콜 확인` 또는 `직접 수정`을 고르게 합니다. 사진에서 읽은 값이 확인 없이 검색에 쓰이지 않습니다.
3. 결과는 다섯 가지입니다: 리콜 대상 제품과 일치 / 제품 정보를 조금 더 확인 / 현재 일치하는 리콜 공지 없음 / 제품 정보를 더 입력 / 리콜 여부를 확인하지 못함. 불러오기 실패는 "리콜 없음"으로 표시하지 않습니다.
4. 이상 증상은 `없음 / 있음 / 잘 모르겠음`으로 따로 묻고, 리콜 결과와 별도로 안내합니다.
5. 결과를 본 뒤 `내 제품에 저장`하면 최신 정보를 다시 확인할 때 결과 변경을 알림으로 받습니다.
6. 어려운 내용은 `AI에게 쉽게 설명받기`로 물어봅니다. AI는 리콜 여부를 결정하지 않습니다.

연습용 가상 공지(DEMO LAB)는 주소 뒤에 `?demo=1`을 붙였을 때만 보입니다. 사진 판독에 AI를 쓰려면 `BATTERYCHECK_VISION=1`이 필요하지만, 코디세이 게이트웨이의 OpenAI 호환 엔드포인트는 이미지 입력을 거부(`unsupported_feature`)해 현재는 기기 내 문자 인식만 씁니다.

## 정기 갱신과 데이터

기본 실행에는 정기 worker가 포함되지 않습니다. 켜려면 프로젝트 루트에서 다음을 실행합니다. 공식 공개 공지는 한 번 수집하고 수신 동의한 사용자의 실제 제품만 재확인합니다. 앱내 알림을 만들며 이메일·문자·OS 푸시는 보내지 않습니다.

```sh
PYTHONPATH=backend .venv/bin/python -m batterycheck.worker --once
PYTHONPATH=backend .venv/bin/python -m batterycheck.worker --interval 21600
```

데이터는 `backend/.data/batterycheck.sqlite3`, 공개 공지 버전은 `backend/.data/official/`에 있습니다. 계정별 접근을 검증한 단일 서버 구성입니다. 여러 API 인스턴스 운영이나 PostgreSQL 전환은 구현하지 않았습니다. 소유자 없는 이전 시연 제품은 새 사용자에게 노출하지 않습니다. 사진 원본·평문 비밀번호를 DB에 보관하지 않습니다. 비밀번호 복구 메일은 제공하지 않습니다.

## 검증

```sh
PYTHONPATH=backend PYTHONWARNINGS=error::ResourceWarning .venv/bin/python -m unittest discover -s backend/tests -v
npm test --prefix frontend
NEXT_TELEMETRY_DISABLED=1 npm run build --prefix frontend
# 위 로컬 서버를 실행한 상태에서
python3 scripts/smoke_http.py
node frontend/tests/browser-e2e.mjs
node frontend/tests/auth-tabs-e2e.mjs
node frontend/tests/pwa-e2e.mjs
node frontend/tests/ocr-eval.mjs
```

테스트는 생성한 계정을 마지막에 삭제합니다. 브라우저 검증은 기존 Chromium을 headless로 실행하며 외부 페이지 요청을 차단합니다. OCR의 합성 라벨 시험은 실제 생활사진 인식 정확도나 사용자 실증과 구분합니다. `verification/summary.json`과 `IMPLEMENTATION_2026-10-02.md`는 최초 가상 데모 기록이며 **최신 결과는 `verification/final_report.md`와 `summary-v2.json`**입니다.

## 사용자 테스트 결과

아직 실시하지 않았습니다. 팀원을 제외한 5명 이상을 대상으로 [검증 절차](docs/USER_VALIDATION.md)에 따라 진행하고, 인원·피드백·반영 내용을 여기에 적습니다. 앱 화면 아래 `사용 의견 보내기`로도 의견을 받습니다.

## 링크와 문서

- 배포 URL: https://batterycheck-beryl.vercel.app (Vercel 컨테이너 함수 + Turso DB)
- 가상 데이터 체험 URL: https://batterycheck-beryl.vercel.app/?demo=1 (`가상 예시 채우기` 버튼으로 연습용 DEMO LAB 공지를 확인. 실제 리콜 공지가 아님)
- 발표 자료: [발표 개요](docs/PRESENTATION.md) (슬라이드 작성 전), [시연 흐름](docs/DEMO_SCRIPT.md)
- 제출 문서: [프로젝트 기획서 (PDF)](docs/BatteryCheck_프로젝트_기획서_최종.pdf) · [기능 요구 명세서 및 시스템 아키텍처 (PDF)](docs/BatteryCheck_기능요구명세서_및_시스템아키텍처_최종.pdf)
- [기획서](docs/PROJECT_PLAN.md) · [요구사항 정의서](docs/REQUIREMENTS.md) · [구현 구조와 AI 설계](docs/ARCHITECTURE.md)
- [과제 요건과 준비 상태](docs/CONTEST_READINESS.md) · [사용자 검증 절차](docs/USER_VALIDATION.md)
- [공식 출처와 API 실행 증거](docs/SOURCES.md) · [배포 절차](docs/DEPLOYMENT.md) · [최종 검증 기록](verification/final_report.md)

Safety Korea 승인, 실제 사용자 참여, 안전 전문가 검토는 아직 하지 않았으며 코드 구현으로 대신하지 않습니다. 배포 환경에서는 계정·보관함·AI 대화·사용 의견이 Turso(미국 리전)에 저장됩니다.

## 라이선스

이 프로젝트의 새로 작성한 소스 코드는 [MIT](LICENSE)입니다. 공식 기관 문서·공지·상표·연결된 외부 자료에는 해당 권리자의 조건이 적용되며 이 라이선스에 포함되지 않습니다. 의존성은 각 패키지 라이선스를 따릅니다. [NOTICE](NOTICE.md)를 참고하세요.
