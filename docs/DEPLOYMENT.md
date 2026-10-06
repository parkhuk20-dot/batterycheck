# 실행과 배포

현재 검증된 실행 방식은 Mac의 로컬 Next.js + FastAPI입니다. Docker 구성은 제공하지만, 이번 환경에 Docker 실행기가 없어 이미지 빌드·컨테이너 실행·공개 HTTPS를 검증하지 못했습니다. 외부 배포 완료나 대회 클라우드 승인으로 표시하지 않습니다.

## 로컬 실행

프로젝트 루트에서 `python3 scripts/run_local.py`를 실행하고 `http://127.0.0.1:3114`를 엽니다. 포트가 사용 중이면 기존 프로세스를 중단하지 않고 종료합니다. 프런트엔드 빌드와 설치는 README를 참고하세요. `BATTERYCHECK_PYTHON`으로 준비된 Python 경로를 지정할 수 있습니다.

Mac 사진 인식은 Apple Vision을 사용합니다. 첫 실행에는 Swift 컴파일러가 필요하며 생성된 실행 파일은 `backend/.data/ocr`에 보관합니다. 사진 자체는 임시 폴더에서 처리하고 성공·실패 후 삭제합니다. 다른 운영체제에서는 Tesseract가 설치되어야 합니다. 이 도구를 자동으로 전역 설치하지 않습니다.

## Vercel 배포

운영 주소: https://batterycheck-beryl.vercel.app

루트의 `Dockerfile.vercel`이 웹과 API를 한 컨테이너 이미지로 묶고, Vercel이 이를 컨테이너 함수로 실행합니다. Vercel은 요청을 여러 인스턴스에 나눠 보내고 인스턴스의 파일은 공유되지 않으므로, 계정과 기록은 Turso(libSQL) DB에 둡니다. `TURSO_DATABASE_URL`이 있으면 `store.py`가 `hrana.py`를 통해 Turso에 저장하고, 없으면 로컬 SQLite 파일을 씁니다.

```sh
vercel link --yes
printf 3000 | vercel env add PORT production      # 비루트 사용자가 열 수 있는 포트
vercel env add OPENAI_API_KEY production          # 코디세이 콘솔의 OpenAI 호환 키를 직접 입력
vercel integration add tursocloud/database --plan starter -m region=iad1 -e production   # 약관 동의는 브라우저에서
vercel deploy --prod
```

제한: 공식 공지 캐시(`official/`)와 요청 제한은 인스턴스마다 따로라서 "공식 자료 갱신" 결과가 인스턴스에 따라 다르게 보일 수 있습니다. 정기 갱신 worker는 이 배포에 포함되지 않습니다. 5분간 접속이 없으면 인스턴스가 내려가 다음 첫 요청이 몇 초 걸립니다. 허용 출처는 Vercel이 주는 호스트 이름으로 정하며, 다른 도메인을 붙이면 `BATTERYCHECK_ALLOWED_ORIGINS`를 직접 지정합니다.

## 컨테이너 실행

```sh
cp .env.example .env
docker compose config
docker compose up --build -d
```

웹 포트는 기본적으로 127.0.0.1:3114에만 열립니다. 백엔드 포트는 호스트에 공개하지 않습니다. 데이터는 `batterycheck-data` 볼륨에 보관합니다. 웹·API는 각 1프로세스이며 계정·제품 저장소는 SQLite입니다. 여러 서버를 연결하는 구성은 지원하지 않습니다.

Linux 컨테이너에는 Tesseract 영어 인식을 설치합니다. 한국어 브랜드의 직접 입력은 가능하지만 한국어 OCR 품질을 보장하지 않습니다. Mac의 Apple Vision과 컨테이너 Tesseract는 서로 다른 엔진이므로 동일 성능으로 보고하지 않습니다.

정기 갱신을 켜려면 다음과 같이 실행합니다. 6시간은 운영 설정이며 실제 CPSC 갱신 보장이 아닙니다. 수신 동의한 사용자만 재확인합니다. 오류 시 마지막 자료를 유지하고 다음 주기에 재시도하며, 같은 근거의 앱내 알림은 중복 생성하지 않습니다.

```sh
docker compose --profile notifications up -d
```

중단은 `docker compose stop worker`입니다. 앱내 수신 동의도 언제든 해지할 수 있습니다. 이메일·문자·OS 푸시 발송은 구현 범위에 포함하지 않습니다.

## 승인된 외부 서버에 공개할 때

도메인 DNS와 서버를 확보한 후 `.env`에 `SITE_ADDRESS=실제도메인`, `BATTERYCHECK_ALLOWED_ORIGINS=https://실제도메인`, `BATTERYCHECK_COOKIE_SECURE=1`을 설정합니다. 도메인 예시 문자열을 그대로 실행하지 마세요.

```sh
docker compose -f compose.yml -f compose.production.yml config
docker compose -f compose.yml -f compose.production.yml up --build -d
```

Caddy가 HTTPS를 종단합니다. DNS·80/443 접근·인증서 발급·로그인 쿠키·사진 인식·두 계정 격리·삭제를 실제 공개 URL에서 다시 확인한 후 배포 실적으로 기록합니다. 공개 환경에서는 서버 접근 통제와 백업 보관 기간도 운영자가 정해야 합니다. 기본 요청 제한은 단일 API 프로세스의 메모리 기반입니다.

## 데이터 보관과 복구

- 실행 DB, 쿠키 토큰, 비밀번호, `.env`와 업로드 사진을 Git에 넣지 않습니다. 인증 원문 비밀번호는 저장하지 않고 PBKDF2 해시만 보관합니다.
- 계정 삭제는 해당 계정·제품·이력·알림·세션을 삭제합니다. 이미 운영자가 별도 생성한 백업의 삭제·보관 정책은 운영자 책임입니다. 현재 자동 백업은 설정하지 않았습니다.
- 백업은 SQLite backup API 또는 서비스를 멈춘 후 볼륨 사본으로 수행해야 합니다. 실행 중인 DB 파일만 복사하지 마세요.
- 기존 소유자 없는 가상 제품은 새 가입자에게 자동 배정하지 않습니다.
- 비밀번호 재설정 이메일은 제공하지 않습니다. 현재 계정은 실증용이며 연락처 수집·외부 메시지 발송이 없습니다.

대회 플랫폼은 필수가 아니며 계정 승인은 별도입니다. 공식 일정·요건은 [대회 준비 문서](CONTEST_READINESS.md)를 따릅니다.
