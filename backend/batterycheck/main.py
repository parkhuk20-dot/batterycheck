import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .auth import Auth, RateLimiter, SESSION_COOKIE, SESSION_SECONDS
from .engine import CATALOG_PATH, evaluate, normalize
from .assistant import MODEL, Assistant, AssistantError
from .models import (ActionInput, AssistantInput, DeleteAccountInput, FeedbackInput, IdentifyInput, LoginInput,
                     PreferencesInput, ProductInput, RegisterInput, RiskInput)
from .safety import EMERGENCY_TEXT, SOURCES, risk_guidance
from .store import Store
from .worker import refresh_lock


def create_app(db_path=None, catalog_path=CATALOG_PATH, clock=None, official_service=None, ai_client=None):
    from .official import OfficialService
    from .ocr import engine_status

    app = FastAPI(title='배터리체크', version='0.2.0')
    hosts = os.environ.get('BATTERYCHECK_ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',')
    origins = {value.strip() for value in os.environ.get(
        'BATTERYCHECK_ALLOWED_ORIGINS', 'http://localhost:3114,http://127.0.0.1:3114').split(',') if value.strip()}
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[value.strip() for value in hosts if value.strip()])
    app.add_middleware(CORSMiddleware, allow_origins=list(origins), allow_credentials=True,
                       allow_methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'], allow_headers=['Content-Type'])
    path = db_path or os.environ.get('BATTERYCHECK_DB', Path(__file__).parents[1] / '.data/batterycheck.sqlite3')
    app.state.store = Store(path)
    app.state.auth = Auth(app.state.store)
    app.state.limiter = RateLimiter()
    app.state.catalog_path = catalog_path
    app.state.official_service = official_service if official_service is not None else OfficialService(Path(path).parent / 'official')
    app.state.sync_lock = threading.Lock()
    secure_cookie = os.environ.get('BATTERYCHECK_COOKIE_SECURE', '0').lower() in {'1', 'true'}

    def now_text():
        return (clock() if clock else datetime.now(timezone.utc)).isoformat()

    @app.middleware('http')
    async def request_boundaries(request: Request, call_next):
        origin = request.headers.get('origin')
        if origin and origin not in origins:
            return JSONResponse(status_code=403, content={'detail': '허용되지 않은 웹 출처입니다.'})
        if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            if request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse(status_code=403, content={'detail': '다른 사이트에서 보낸 변경 요청은 허용하지 않습니다.'})
            maximum = 6 * 1024 * 1024 if request.url.path == '/identify' else 16384
            try:
                length = int(request.headers.get('content-length', '0'))
                if length < 0:
                    raise ValueError
                if length > maximum:
                    return JSONResponse(status_code=413, content={'detail': '입력이 너무 큽니다.'})
            except ValueError:
                return JSONResponse(status_code=400, content={'detail': '잘못된 입력 길이입니다.'})
            chunks = []
            length = 0
            async for chunk in request.stream():
                length += len(chunk)
                if length > maximum:
                    return JSONResponse(status_code=413, content={'detail': '입력이 너무 큽니다.'})
                chunks.append(chunk)
            request._body = b''.join(chunks)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.exception_handler(sqlite3.Error)
    async def storage_failure(request, exc):
        return JSONResponse(status_code=503, content={'detail': '로컬 저장소에 접근하지 못했습니다. 저장 완료로 처리되지 않았습니다.'})

    def current_user(request: Request):
        user = app.state.auth.current(request.cookies.get(SESSION_COOKIE))
        if not user:
            raise HTTPException(401, '로그인이 필요합니다.')
        return user

    def limit(request, category, limit_count, window):
        if not app.state.limiter.allow(category, limit_count, window):
            raise HTTPException(429, '요청이 너무 많습니다. 잠시 후 다시 시도하세요.', headers={'Retry-After': str(window)})

    def auth_limit(request, username):
        client = request.client.host if request.client else 'unknown'
        limit(request, ('auth-ip', client), 30, 900)
        limit(request, ('auth-user', username), 8, 300)

    def set_session(response, request, user):
        token = app.state.auth.create_session(user['id'], request.cookies.get(SESSION_COOKIE))
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                            secure=secure_cookie, samesite='strict', path='/')

    @app.post('/auth/register', status_code=201)
    def register(body: RegisterInput, request: Request, response: Response):
        auth_limit(request, body.username)
        try:
            user = app.state.store.register(body.username, body.password, body.notification_consent, now_text())
        except sqlite3.IntegrityError:
            raise HTTPException(409, '이미 사용 중인 아이디입니다.') from None
        set_session(response, request, user)
        return user

    @app.post('/auth/login')
    def login(body: LoginInput, request: Request, response: Response):
        auth_limit(request, body.username)
        user = app.state.auth.authenticate(body.username, body.password)
        if not user:
            raise HTTPException(401, '아이디 또는 비밀번호가 올바르지 않습니다.')
        set_session(response, request, user)
        return user

    @app.get('/auth/me')
    def me(user=Depends(current_user)):
        return user

    @app.post('/auth/logout', status_code=204)
    def logout(request: Request, response: Response):
        app.state.auth.logout(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, path='/', secure=secure_cookie, httponly=True, samesite='strict')

    @app.patch('/auth/preferences')
    def preferences(body: PreferencesInput, user=Depends(current_user)):
        return app.state.store.preferences(user['id'], body.notification_consent, body.ai_consent, now_text())

    @app.get('/auth/export')
    def export(response: Response, user=Depends(current_user)):
        response.headers['Content-Disposition'] = 'attachment; filename="batterycheck-personal-data.json"'
        return app.state.store.export(user['id'])

    @app.delete('/auth/account', status_code=204)
    def delete_account(body: DeleteAccountInput, request: Request, response: Response, user=Depends(current_user)):
        auth_limit(request, user['username'])
        if not app.state.auth.authenticate(user['username'], body.password):
            raise HTTPException(401, '비밀번호가 올바르지 않습니다.')
        app.state.store.delete_account(user['id'])
        response.delete_cookie(SESSION_COOKIE, path='/', secure=secure_cookie, httponly=True, samesite='strict')

    @app.get('/health')
    def health():
        return {'status': 'ok', 'mode': 'evidence_assistant', 'official_apis': 'cpsc_cache_safetykorea_unconfigured',
                'ai': 'local_ocr' if engine_status()['available'] else 'ocr_unavailable', 'hardware_control': False,
                'assistant': app.state.assistant.available()}

    @app.get('/meta')
    def meta():
        ocr = engine_status()
        return {'mode': 'evidence_assistant', 'official_apis': app.state.official_service.status(),
                'ai': 'local_ocr' if ocr['available'] else 'ocr_unavailable', 'ocr': ocr,
                'assistant': app.state.assistant.status(),
                'notifications': 'consent_based_in_app',
                'notification_limitations': '로그인 후 공지 갱신을 요청하면 본인 저장 제품을 재확인합니다. 운영자가 선택적 갱신 워커를 실행한 경우 알림에 동의한 계정의 제품을 정기 재확인합니다. 앱내 알림이며 이메일·푸시는 발송하지 않습니다.',
                'accounts': 'local_password_sessions', 'emergency_text': EMERGENCY_TEXT,
                'safety_sources': SOURCES, 'safety_review': '전문가 검토 미완료',
                'data': '공식 CPSC 공개 공지와 가상 시연을 구분합니다. 국내 출처 연결과 회수 자격의 최종 확인은 별도입니다.',
                'privacy': {'version': '2026-10-02', 'retention': '계정 삭제 시 계정·제품·이력·알림 삭제',
                            'photos': 'OCR 처리 후 보관하지 않음', 'external_product_transmission': False,
                            'ai_assistant': 'AI 도우미 사용에 별도 동의한 계정만 질문·보관함 요약을 OpenAI 호환 API(코디세이 게이트웨이)로 전송. 사진은 AI 사진 판독을 켠 경우에만 전송하며 저장하지 않음'}}

    @app.get('/sources/status')
    def sources_status():
        return app.state.official_service.status()

    @app.post('/risk')
    def risk(body: RiskInput):
        return risk_guidance(body.signs, body.signs_answered)

    def inspect_product(body):
        if body.demo:
            return evaluate(body, app.state.catalog_path, now=clock() if clock else None)
        return app.state.official_service.evaluate(body)

    app.state.assistant = Assistant(app.state.store, app.state.official_service, inspect_product, ai_client)

    @app.get('/assistant/history')
    def assistant_history(user=Depends(current_user)):
        return app.state.store.assistant_history(user['id'])

    @app.delete('/assistant/history', status_code=204)
    def assistant_clear(user=Depends(current_user)):
        app.state.store.assistant_clear(user['id'])

    @app.post('/assistant')
    def ask_assistant(body: AssistantInput, request: Request, user=Depends(current_user)):
        if not user['ai_consent']:
            raise HTTPException(403, 'AI 도우미 이용에 먼저 동의하세요. 동의 전에는 질문과 제품 정보를 외부 AI로 보내지 않습니다.')
        if not app.state.assistant.available():
            raise HTTPException(503, 'AI 도우미가 설정되지 않았습니다. 운영자가 AI API 키를 설정해야 합니다.')
        limit(request, ('assistant', user['id']), 10, 300)
        try:
            reply = app.state.assistant.answer(user['id'], body.question)
        except AssistantError as exc:
            raise HTTPException(exc.status, exc.detail) from None
        created_at = now_text()
        app.state.store.assistant_append(user['id'], body.question, reply['answer'], reply['sources'], created_at)
        return reply | {'ai_generated': True, 'created_at': created_at,
                        'notice': 'AI가 생성한 설명입니다. 리콜 대상 여부와 안전을 확정하지 않습니다.'}

    @app.post('/feedback', status_code=201)
    def feedback(body: FeedbackInput, request: Request):
        client = request.client.host if request.client else 'unknown'
        limit(request, ('feedback', client), 30, 300)  # Behind the proxy every visitor shares one client address.
        user = app.state.auth.current(request.cookies.get(SESSION_COOKIE))
        app.state.store.add_feedback(user['id'] if user else None, body.rating, body.comment, now_text())
        return {'saved': True}

    @app.post('/checks')
    def check(body: ProductInput):
        return inspect_product(body)

    @app.post('/identify')
    def identify(body: IdentifyInput, request: Request, user=Depends(current_user)):
        from . import vision
        from .ocr import decode_image, extract_label
        limit(request, ('identify', user['id']), 15, 300)
        vision.log.info('[BatteryImage] file received')
        vision.log.info('[BatteryImage] file size: about %d bytes', len(body.image_base64) * 3 // 4)
        # Photos leave the server only for accounts that agreed to AI processing.
        use_ai = app.state.assistant.vision and user['ai_consent'] and app.state.assistant.available()
        vision.log.info('[BatteryImage] vision configured: %s', str(bool(use_ai)).lower())
        if use_ai:
            data = decode_image(body.image_base64)
            vision.log.info('[BatteryImage] file type: %s', 'image/png' if data.startswith(b'\x89PNG') else 'image/jpeg')
            try:
                local = extract_label(body.image_base64)
            except HTTPException:
                local = None  # The on-device reader is optional once the vision reading is available.
        else:
            local = extract_label(body.image_base64)
        seen = None
        if use_ai:
            import openai
            try:
                seen = vision.read_label(app.state.assistant.client(), MODEL, data)
            except (openai.APIError, IndexError, AttributeError) as exc:
                vision.log.warning('[BatteryImage] vision request failed: %s', type(exc).__name__)
                if not (local and (local.get('model_candidates') or local.get('brand_candidates'))):
                    raise HTTPException(502, vision.FAILURE) from None
        reading = vision.combine(local, seen)
        reply = (local or {'requires_confirmation': True, 'model_confirmed': False, 'photo_retained': False, 'engine': None}) | reading
        reply['processing'] = 'ai' if seen else 'local'
        reply['ai_hint'] = bool(app.state.assistant.vision and app.state.assistant.available() and not user['ai_consent'])
        if not vision.DEBUG:
            reply.pop('text', None)  # Raw label text can include a serial number.
        return reply

    def validate_save(body):
        if not body.brand or not body.model or not body.model_confirmed or body.product_kind != 'power_bank':
            raise HTTPException(422, '브랜드와 모델명을 입력한 뒤 저장해주세요.')
        if body.demo and (normalize(body.brand) != 'DEMO LAB' or not normalize(body.model).startswith('DEMO PB-')):
            raise HTTPException(422, '연습용 예시는 예시 제품으로만 저장할 수 있습니다.')
        if not body.demo and (normalize(body.brand) == 'DEMO LAB' or body.scenario != 'complete'):
            raise HTTPException(422, '실제 제품에는 연습용 설정을 사용할 수 없습니다.')

    @app.get('/products')
    def products(user=Depends(current_user)):
        return app.state.store.list(user['id'])

    @app.post('/products', status_code=201)
    def add_product(body: ProductInput, user=Depends(current_user)):
        validate_save(body)
        return app.state.store.save(body, inspect_product(body), user['id'])

    @app.put('/products/{product_id}')
    def recheck_product(product_id: str, body: ProductInput, user=Depends(current_user)):
        validate_save(body)
        if not app.state.store.get(product_id, user['id']):
            raise HTTPException(404, '저장된 제품을 찾을 수 없습니다.')
        saved = app.state.store.save(body, inspect_product(body), user['id'], product_id, notify=True)
        if saved is None:
            raise HTTPException(404, '저장된 제품을 찾을 수 없습니다.')
        return saved

    @app.patch('/products/{product_id}/action')
    def record_action(product_id: str, body: ActionInput, user=Depends(current_user)):
        saved = app.state.store.action(product_id, user['id'], body.state, now_text())
        if saved is None:
            raise HTTPException(404, '저장된 제품을 찾을 수 없습니다.')
        return saved

    @app.get('/products/{product_id}/history')
    def product_history(product_id: str, user=Depends(current_user)):
        history = app.state.store.history(product_id, user['id'])
        if history is None:
            raise HTTPException(404, '저장된 제품을 찾을 수 없습니다.')
        return history

    @app.delete('/products/{product_id}', status_code=204)
    def delete_product(product_id: str, user=Depends(current_user)):
        if not app.state.store.delete(product_id, user['id']):
            raise HTTPException(404, '저장된 제품을 찾을 수 없습니다.')

    @app.get('/notifications')
    def notifications(user=Depends(current_user)):
        return app.state.store.notifications(user['id'])

    @app.patch('/notifications/{notification_id}/read')
    def read_notification(notification_id: str, user=Depends(current_user)):
        saved = app.state.store.read_notification(notification_id, user['id'], now_text())
        if saved is None:
            raise HTTPException(404, '알림을 찾을 수 없습니다.')
        return saved

    @app.post('/sources/sync')
    def sync_sources(request: Request, user=Depends(current_user)):
        limit(request, ('sync', user['id']), 1, 60)
        if not app.state.sync_lock.acquire(blocking=False):
            raise HTTPException(429, '공식 공지를 갱신 중입니다. 잠시 후 다시 시도하세요.', headers={'Retry-After': '60'})
        try:
            with refresh_lock(app.state.store) as acquired:
                if not acquired:
                    raise HTTPException(429, '공식 공지를 갱신 중입니다. 잠시 후 다시 시도하세요.', headers={'Retry-After': '60'})
                source_status = app.state.official_service.sync()
                before = len(app.state.store.notifications(user['id']))
                rechecked = 0
                for saved in app.state.store.list(user['id']):
                    product = ProductInput.model_validate(saved['input'])
                    if not product.demo:
                        updated = app.state.store.save(product, inspect_product(product), user['id'], saved['id'], notify=True,
                                                       expected_input=saved['input'])
                        if updated is not None:
                            rechecked += 1
                return {'source_status': source_status, 'rechecked': rechecked,
                        'notifications_created': max(0, len(app.state.store.notifications(user['id'])) - before)}
        finally:
            app.state.sync_lock.release()

    return app


app = create_app()
