# 배터리체크 공식 자료와 조회 범위

확인일: 2026-10-02. 실제 제품의 결과는 **공식 공지 후보 검색**이며 리콜 대상·회수 자격의 최종 판정이 아니다. 국내 리콜 확인, 제조사 일련번호 자격 확인, 내부 배터리 안전 진단은 완료되지 않았다.

## CPSC 공개 API

- 공식 안내: https://www.cpsc.gov/Recalls/CPSC-Recalls-Application-Program-Interface-API-Information
- 공식 개발 문서 v1.4: https://www.cpsc.gov/s3fs-public/RecallRetrievalWebServicesProgrammersGuide20180917.pdf
- 미국 정부 데이터 목록: https://catalog.data.gov/dataset/recalls-api
- API: `https://www.saferproducts.gov/RestWebServices/Recall`
- 고정 검색 파라미터: `format=json&ProductName=power%20bank`

문서의 실제 파라미터명과 반환 필드(Title, Description, URL, RecallNumber, RecallDate, Products, Manufacturers)를 사용한다. 이 품목명 검색은 CPSC의 전체 데이터나 모든 보조배터리 공지를 포괄하지 않는다. 다른 표기나 분류로 올라온 공지가 빠질 수 있다. 미국 공지를 국내 제품의 회수 자격에 그대로 적용하지 않는다.

실제 공개 호출 결과는 품목 검색과 단건 리콜번호 검색(`RecallNumber=25338`) 모두 HTTP 503이었다. 단건 응답 본문은 `Page Unavailable` 안내였다. 호출·상태·응답 해시는 `verification/cpsc-live.json`에 보존한다. **API 연동 코드와 실패 처리는 구현했지만 실제 성공 응답을 수신했다고 주장하지 않는다.** 합성 transport fixture로 검증한 성공 경로는 실제 API 운영 성공 증거가 아니다.

앱의 명시적인 동기화 동작만 네트워크를 사용한다. 평상시 제품 대조·보관함·재확인은 저장한 자료에서 동작한다. 사용자 브랜드·모델·사진·일련번호·계정은 CPSC에 보내지 않는다. 목적지는 고정된 HTTPS 공식 API이며 리다이렉트는 따르지 않는다. 연결 5초, I/O 12초, 최대 2회 요청, 응답 4 MiB/2,000건 상한을 적용한다. 4xx는 재시도하지 않으며 429와 5xx/네트워크 장애에만 한 차례 추가 요청한다.

검증된 원문 JSON은 SHA-256 해시와 함께 `data_dir/revisions/`에 보존하고 현재 캐시를 원자적으로 교체한다. 실패 시 기존 캐시를 유지한다. 내용 해시는 재조회 시각을 제외하므로 동일 자료 재조회가 새 공지 알림으로 바뀌지 않는다. 원문·조건이 바뀌면 해시도 바뀐다. 자료의 조회 시각은 게시일과 별도다. 7일은 앱의 캐시 만료 정책이며 공식기관 갱신 SLA나 제품 안전 기준이 아니다.

## 직접 확인한 공식 웹 공지

API 장애 때도 확인할 수 있도록 공식 CPSC 웹 원문에서 다음 2건의 식별자와 조건을 추렸다. `official_web_curated`로 표시하며, API 수집 자료와 구분한다. 이 두 건은 모든 리콜을 대표하지 않는다. 웹 확인 시각·공지 게시일·원문 URL·직접 인용한 조건을 `backend/batterycheck/official_catalog.json`에 보존한다.

| 공지 | 직접 확인한 범위 | 확정을 보류하는 이유 |
|---|---|---|
| [Anker A1263, 25-338](https://www.cpsc.gov/Recalls/2025/More-than-One-Million-Anker-Power-Banks-Recalled-Due-to-Fire-and-Burn-Hazards-Manufactured-by-Anker-Innovations) | 2025-06-12 공지, 미국에서 판매한 A1263 중 대상 일련번호 | 제조사 일련번호 검증이 필요하다. 모델만으로 대상 확정하지 않는다. |
| [INIU BI-B41, 26-135](https://www.cpsc.gov/Recalls/2026/INIU-Recalls-Power-Banks-Due-to-Fire-and-Burn-Hazards-Sold-on-Amazon) | 원문 게시일 2025-12-05, 일련번호 000G21/000H21/000I21/000L21, Amazon.com 판매 범위 | 판매지역·제품 외관·일련번호와 제조사 안내를 직접 확인해야 한다. URL의 2026을 게시년도로 바꾸지 않았다. |

한글 설명은 공지에서 확인한 사실을 요약한 것이며 전문 번역·법적 자격 판정이 아니다. 모델의 접미사와 하이픈, 일련번호 앞자리 0을 보존한다. 일련번호를 기존 입력의 `lot`으로 대체하지 않는다. 모든 실제 후보의 `outcome`은 `pending_review`이고 최상위 결과는 추가 확인 필요이며, 만료된 근거만 있으면 조회 미완료다. 공지를 찾지 못해도 국내와 전체 출처 조회가 완료되지 않았으므로 안전 또는 리콜 없음으로 확정하지 않는다.

공식 웹 확인 자료도 시간이 지나면 만료되며 API 동기화만으로 웹 확인 시각을 갱신하지 않는다. 동일 원문 URL의 API 자료를 실제로 수신하면 해당 사본을 우선하지만 여전히 조건 검토는 보류한다.

## Safety Korea

- 공식 이용 절차: https://www.safetykorea.kr/release/openapi2
- 공식 직접 검색: https://www.safetykorea.kr/recall/recallBoard

공식 페이지에서 이용 신청·승인 후 인증키가 발급되고 키가 필수임을 확인했다. 이 프로젝트는 승인·키 발급·실제 API 응답 검증을 완료하지 않았다. 상태를 `unconfigured`로 표시하고 직접 검색 링크를 제공한다. 키 신청 이메일을 발송하거나 공공데이터 이용 승인을 신청하지 않았다. 이 부분은 사용자 명의의 신청·승인이 필요한 외부 의존성이다.

## 검증 해석

`backend/tests/test_official.py`는 미연결 상태, 후보·모델 접미사, 일련번호/로트 분리, 개인 입력 미전송, 원문 캐시/해시, 고정 URL, 장애·만료·변조·대용량 응답, 위험징후 독립성을 검증한다. API 성공 경로 fixture는 명시적인 가상 값이며 공식 corpus에 포함하지 않는다. 이 검증은 실제 리콜 매칭 정확도, 회수 자격 검증, 안전 효과 또는 사용자 실증을 뜻하지 않는다.
