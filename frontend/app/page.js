'use client';

import { useEffect, useRef, useState } from 'react';
import safety from '../../shared/safety.json';

const empty = { brand: '', model: '', model_confirmed: false, product_kind: 'power_bank', demo: false,
  manufactured_on: '', sale_region: '', lot: '', signs: [], signs_answered: false, scenario: 'complete' };
const signLabels = { smoke: '연기', flame: '불꽃', swelling: '팽창', damage: '파손', discoloration: '변색', heat: '이상 발열', odor: '특이한 냄새', sound: '이상 소리' };
const fields = { model: '모델명', brand: '브랜드', manufactured_on: '제조일', sale_region: '판매지역', lot: '로트', serial: '시리얼', serial_number: '일련번호', notice_review: '공지 검토' };
const conditionLabels = { match: '일치', mismatch: '불일치', unknown: '미확인' };
const sourceLabels = { available: 'API 자료 수집 완료', curated: '공식 웹 확인 자료', not_synced: 'API 갱신 전', failed: '최근 갱신 실패', stale: '자료 확인 기한 지남', unconfigured: '연결 준비 필요' };
const actionLabels = { unconfirmed: '아직 확인하지 않음', checking: '확인 중', user_completed: '사용자 완료 신고' };
const instant = value => value ? new Date(value).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) + ' KST' : '성공 기록 없음';
const bodyFor = form => ({ ...form, manufactured_on: form.manufactured_on || null, sale_region: form.sale_region || null, lot: form.lot || null });
const safeLink = value => { try { const u = new URL(value); return u.protocol === 'https:' ? u.href : undefined; } catch { return undefined; } };

async function api(path, method = 'GET', body) {
  const response = await fetch(`/api/${path}`, { method, cache: 'no-store', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = response.status === 204 ? null : await response.json();
  if (!response.ok) { const error = new Error(typeof data?.detail === 'string' ? data.detail : '입력값과 필수 확인 항목을 확인하세요.'); error.status = response.status; throw error; }
  return data;
}
function Sources({ sources = [] }) {
  return <ul className="sources">{sources.map(source => <li key={source.url}><a href={safeLink(source.url)} target="_blank" rel="noreferrer">{source.title} ↗</a><small>게시 {source.published_on} · 자료 확인 {source.checked_on}</small></li>)}</ul>;
}
function Risk({ risk }) {
  if (!risk) return null;
  return <section className={`risk ${risk.level}`} aria-label="위험징후 안내" role={['emergency', 'concern'].includes(risk.level) ? 'alert' : undefined}>
    <strong>{risk.level === 'emergency' ? '긴급 상황 안내' : risk.level === 'concern' ? '위험징후 안내 우선' : '위험징후 응답'}</strong><p>{risk.text}</p>
    {['emergency', 'concern'].includes(risk.level) && <><a className="phone" href="tel:119">119 전화 연결</a><small>사용자가 전화할 수 있는 링크입니다. 자동 신고하지 않습니다.</small></>}
    <Sources sources={risk.sources} /><small>규칙 {risk.rule_version} · {risk.review_status}</small>
  </section>;
}
function Result({ result, historical = false }) {
  return <article className="result" aria-label={historical ? '과거 확인 기록' : '확인 결과'}>
    <div className="result-title"><span className={`status ${result.status}`}>{result.label}</span><span className="tag">{result.synthetic ? '가상 공지 기반 시연' : '공식 공지 · 제한된 조회 범위'}</span></div>
    {historical && <p className="warning">과거 기록입니다. 현재 조회 결과가 아닙니다.</p>}
    <p>{result.limitations}</p><div className="next-action"><strong>다음 행동</strong><p>{result.next_action}</p></div>
    {result.questions?.length > 0 && <div className="questions"><strong>추가로 확인할 내용</strong><ul>{result.questions.map((q, i) => <li key={q.notice_id + q.field + i}>{q.text}</li>)}</ul></div>}
    {result.notices?.map(n => <details key={n.id} open><summary>{n.title}</summary><p className="small">{n.id} · {result.synthetic ? '가상 자료' : '공식 원문 근거 · 대상 조건 직접 확인 필요'}</p>
      {safeLink(n.source_url || n.url) && <a href={safeLink(n.source_url || n.url)} target="_blank" rel="noreferrer">공식 공지와 회수 절차 확인 ↗</a>}
      {n.checks?.length > 0 && <div className="table-wrap"><table><caption>조건별 대조 · 사용자 입력 기준</caption><thead><tr><th>항목</th><th>입력</th><th>공지 조건</th><th>결과</th></tr></thead><tbody>{n.checks.map((c, i) => <tr key={c.field + i}><th scope="row">{fields[c.field] || c.field}</th><td>{c.input || '모름'}</td><td>{Array.isArray(c.expected) ? c.expected.join(' / ') : c.expected}</td><td>{conditionLabels[c.state] || c.state}</td></tr>)}</tbody></table></div>}
      <p>{n.action}</p>
    </details>)}
    <dl className="evidence"><div><dt>대조 실행</dt><dd>{instant(result.checked_at)}</dd></div><div><dt>공지 버전</dt><dd>{result.catalog_version || '자료 없음'}</dd></div>
      {result.sources?.map(s => <div key={s.id}><dt>{s.name}</dt><dd>{s.state === 'complete' ? (result.synthetic ? '가상 저장소 대조 완료' : '표시된 범위 수집 완료') : s.state === 'failed' ? '조회 실패' : s.state === 'unconfigured' ? '미연결' : s.state === 'not_synced' ? '갱신 전' : sourceLabels[s.state] || '상태 확인 필요'}{s.freshness === 'stale_or_invalid' && ' · 자료 만료'}<br />마지막 자료 시각: {instant(s.last_success_at)}<br />범위: {s.scope}</dd></div>)}
    </dl><Risk risk={result.risk} />
  </article>;
}
function Account({ user, busy, onAuth, onLogout, onPreferences, onExport, onDelete }) {
  const [register, setRegister] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [consent, setConsent] = useState(false);
  const [deletePassword, setDeletePassword] = useState('');
  if (user) return <section className="account panel" aria-label="계정 관리"><div className="account-bar"><div><strong>{user.username}님의 보관함</strong><small>제품·확인 이력은 이 계정에서만 볼 수 있습니다.</small></div><button className="secondary" disabled={busy} onClick={onLogout}>로그아웃</button></div>
    <details><summary>알림 · 개인정보 관리</summary><label className="checkbox"><input type="checkbox" disabled={busy} checked={Boolean(user.notification_consent)} onChange={e => onPreferences(e.target.checked)} />공지 변경에 대한 앱내 알림을 받겠습니다.</label><p className="small">공식 자료를 갱신하고 보관함을 다시 확인할 때 변경 알림을 만듭니다. 이메일·문자 발송은 없습니다.</p><button className="secondary" onClick={onExport} disabled={busy}>내 기록 내려받기</button>
      <details className="delete-account"><summary>계정과 모든 기록 삭제</summary><p>제품, 이력, 알림과 로그인 세션이 삭제됩니다. 되돌릴 수 없습니다.</p><label>삭제 확인 비밀번호<input type="password" autoComplete="current-password" value={deletePassword} onChange={e => setDeletePassword(e.target.value)} /></label><button className="secondary danger" disabled={busy || !deletePassword} onClick={() => onDelete(deletePassword)}>모든 내 기록 영구 삭제</button></details>
    </details></section>;
  return <section className="account panel" aria-label="로그인"><div className="account-bar"><div><h2>{register ? '내 보관함 만들기' : '기록을 이어서 확인하세요'}</h2><p className="small">조회와 위험징후 안내는 로그인 없이도 이용할 수 있습니다.</p></div><button className="text-button" disabled={busy} onClick={() => setRegister(!register)}>{register ? '로그인으로 전환' : '회원가입으로 전환'}</button></div>
    <form onSubmit={e => { e.preventDefault(); onAuth(register, { username, password, ...(register ? { privacy_consent: consent } : {}) }); }}><fieldset disabled={busy}><div className="auth-fields"><label>아이디<input value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" required minLength={3} maxLength={32} pattern="(?:[A-Za-z0-9_.]|-)+" placeholder="영문·숫자·점·밑줄·하이픈" /></label><label>비밀번호<input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete={register ? 'new-password' : 'current-password'} minLength={10} maxLength={128} required /></label><button type="submit" disabled={register && !consent}>{register ? '동의하고 가입' : '로그인'}</button></div>
      {register && <label className="checkbox"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} required />아이디·암호화된 비밀번호·제품·확인 이력을 계정 삭제 때까지 보관하는 데 동의합니다. AI 도우미는 별도 동의 후에만 작동합니다. 사진 원본은 인식 후 삭제됩니다.</label>}
    </fieldset></form></section>;
}

function Assistant({ user, status, onConsent }) {
  const [items, setItems] = useState([]);
  const [question, setQuestion] = useState('');
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { if (user?.ai_consent) api('assistant/history').then(setItems).catch(() => {}); else setItems([]); }, [user?.ai_consent]);
  async function ask(event) {
    event.preventDefault();
    const asked = question.trim();
    if (!asked) return;
    setWaiting(true); setError('');
    try {
      const reply = await api('assistant', 'POST', { question: asked });
      setItems(current => [...current, { role: 'user', content: asked, sources: [] }, { role: 'assistant', content: reply.answer, sources: reply.sources }]);
      setQuestion('');
    } catch (e) { setError(e.message); } finally { setWaiting(false); }
  }
  async function clear() { setError(''); try { await api('assistant/history', 'DELETE'); setItems([]); } catch (e) { setError(e.message); } }
  return <section className="panel assistant" aria-label="AI 안내 도우미"><p className="eyebrow">03 AI 안내 도우미</p><h2>공지와 내 기록을 쉬운 말로</h2>
    <p className="small">생성형 AI가 저장된 공식 공지와 내 보관함 기록을 찾아 설명하고, 이전 대화를 기억합니다. <strong>AI가 생성한 답변이며 리콜 대상 여부나 제품 안전을 확정하지 않습니다.</strong> 연기·불꽃이 있다면 질문보다 119가 먼저입니다.</p>
    {!user ? <p className="small">로그인하면 이용할 수 있습니다.</p> : !status?.available ? <p className="warning">운영자가 AI 연결을 설정하지 않아 지금은 이용할 수 없습니다. 조건 대조와 보관함은 그대로 쓸 수 있습니다.</p> : <>
      <label className="checkbox"><input type="checkbox" checked={Boolean(user.ai_consent)} disabled={waiting} onChange={e => onConsent(e.target.checked)} />질문 내용과 내 보관함의 제품 정보(브랜드·모델·판매지역·제조일·로트·조치 상태·확인 결과)를 답변 생성을 위해 {status.provider}로 보내고, 대화를 내 계정에 저장하는 데 동의합니다. 사진은 보내지 않습니다. 동의는 언제든 해제할 수 있습니다.</label>
      {user.ai_consent && <>
        <div className="chat" aria-live="polite">{items.length === 0 && <p className="small">예: “내 보관함 제품에 지금 확인할 게 있나요?”, “Anker A1263 공지는 어떤 조건인가요?”</p>}
          {items.map((item, i) => <article key={i} className={`chat-item ${item.role}`}><span className="tag">{item.role === 'user' ? '내 질문' : 'AI 생성 답변 · 공식 판정 아님'}</span><p>{item.content}</p>
            {item.sources?.length > 0 && <ul className="sources">{item.sources.map(s => <li key={s.url}><a href={safeLink(s.url)} target="_blank" rel="noreferrer">{s.title} ↗</a><small>참고한 공식 자료 · {s.id}</small></li>)}</ul>}</article>)}
          {waiting && <p className="small" role="status">공식 자료와 내 기록을 확인하는 중…</p>}</div>
        {error && <p className="error" role="alert">{error}</p>}
        <form onSubmit={ask}><label>질문<textarea value={question} maxLength={1000} rows={3} disabled={waiting} onChange={e => setQuestion(e.target.value)} placeholder="제품의 공지 조건이나 다음 행동을 물어보세요" /></label>
          <div className="form-actions"><button type="submit" disabled={waiting || !question.trim()}>{waiting ? '답변 생성 중…' : '질문하기'}</button>{items.length > 0 && <button type="button" className="text-button danger" disabled={waiting} onClick={clear}>대화 기록 지우기</button>}</div></form></>}
    </>}
  </section>;
}
function Feedback() {
  const [rating, setRating] = useState('');
  const [comment, setComment] = useState('');
  const [state, setState] = useState('');
  async function send(event) { event.preventDefault(); try { await api('feedback', 'POST', { rating: Number(rating), comment }); setState('의견을 저장했습니다. 고맙습니다.'); setRating(''); setComment(''); } catch (e) { setState(e.message); } }
  return <details className="panel feedback"><summary>사용 의견 보내기</summary><p className="small">서비스 개선에만 사용합니다. 이름·연락처·시리얼번호는 적지 마세요. 로그인 상태에서 보낸 의견은 계정을 삭제하면 함께 삭제됩니다.</p>
    <form onSubmit={send}><label>이 서비스가 다음 행동을 정하는 데 도움이 되었나요?<select value={rating} required onChange={e => setRating(e.target.value)}><option value="">선택</option><option value="5">5 · 매우 도움 됨</option><option value="4">4 · 도움 됨</option><option value="3">3 · 보통</option><option value="2">2 · 별로</option><option value="1">1 · 전혀 아님</option></select></label>
      <label>불편했던 점이나 바라는 점 <span>선택</span><textarea value={comment} maxLength={1000} rows={3} onChange={e => setComment(e.target.value)} /></label><button type="submit" className="secondary">의견 보내기</button></form>
    {state && <p className="message" role="status">{state}</p>}</details>;
}

export default function Home() {
  const [form, setForm] = useState(empty);
  const [result, setResult] = useState(null);
  const [products, setProducts] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [history, setHistory] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [server, setServer] = useState('연결 확인 중');
  const [dirty, setDirty] = useState(true);
  const [user, setUser] = useState(null);
  const [notifications, setNotifications] = useState([]);
  const [ocr, setOcr] = useState(null);
  const [meta, setMeta] = useState(null);
  const [sourceStatus, setSourceStatus] = useState(null);
  const [online, setOnline] = useState(true);
  const accountGeneration = useRef(0);
  const accountChannel = useRef(null);
  const workSequence = useRef(0);
  async function guardedApi(...args) {
    const generation = accountGeneration.current;
    let data;
    try { data = await api(...args); } catch (error) {
      if (generation !== accountGeneration.current) error.stale = true;
      throw error;
    }
    if (generation !== accountGeneration.current) {
      const error = new Error('계정이 변경되어 이전 요청을 폐기했습니다.');
      error.stale = true;
      throw error;
    }
    return data;
  }

  async function loadPrivate() { const generation = accountGeneration.current; const [items, alerts] = await Promise.all([guardedApi('products'), guardedApi('notifications')]); if (generation !== accountGeneration.current) return; setProducts(items); setNotifications(alerts); }
  function clearPrivate() { accountGeneration.current += 1; workSequence.current += 1; setBusy(false); setUser(null); setProducts([]); setNotifications([]); setSelectedId(null); setHistory(null); setResult(null); setOcr(null); setForm(empty); setDirty(true); }
  useEffect(() => {
    guardedApi('health').then(() => setServer('서버 연결됨')).catch(() => setServer('서버 연결 필요'));
    guardedApi('meta').then(setMeta).catch(() => {});
    guardedApi('sources/status').then(setSourceStatus).catch(() => {});
    const generation = accountGeneration.current;
    guardedApi('auth/me').then(async account => { if (generation !== accountGeneration.current) return; setUser(account); await loadPrivate(); }).catch(() => {});
    const channel = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('batterycheck-auth') : null;
    accountChannel.current = channel;
    if (channel) channel.onmessage = () => {
      clearPrivate();
      const generation = accountGeneration.current;
      guardedApi('auth/me').then(async account => { if (generation !== accountGeneration.current) return; setUser(account); await loadPrivate(); }).catch(() => {});
    };
    const update = () => setOnline(navigator.onLine);
    update(); window.addEventListener('online', update); window.addEventListener('offline', update);
    if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
    return () => { channel?.close(); window.removeEventListener('online', update); window.removeEventListener('offline', update); };
  }, []);
  function change(field, value) { setForm(current => field === 'demo' ? { ...empty, demo: value, signs: current.signs, signs_answered: current.signs_answered } : ({ ...current, [field]: value, ...(['brand', 'model'].includes(field) ? { model_confirmed: false } : {}) })); setResult(null); setHistory(null); setDirty(true); setMessage(''); }
  function toggleSign(sign) { setForm(current => ({ ...current, signs_answered: true, signs: current.signs.includes(sign) ? current.signs.filter(s => s !== sign) : [...current.signs, sign] })); setResult(null); setDirty(true); }
  const localRisk = form.signs.length ? { level: form.signs.some(s => ['smoke', 'flame'].includes(s)) ? 'emergency' : 'concern', text: form.signs.some(s => ['smoke', 'flame'].includes(s)) ? safety.emergency_text : safety.concern_text, sources: safety.sources, rule_version: safety.rule_version, review_status: safety.review_status } : null;
  async function run(work) { const sequence = ++workSequence.current; setBusy(true); setError(''); setMessage(''); try { await work(); } catch (e) { if (e.stale) return; if (e.status === 401) clearPrivate(); setError(e.message); } finally { if (sequence === workSequence.current) setBusy(false); } }
  async function check(event) { event.preventDefault(); await run(async () => { setResult(null); setResult(await guardedApi('checks', 'POST', bodyFor(form))); setDirty(false); setServer('서버 연결됨'); }); }
  async function save() { await run(async () => { const saved = await guardedApi(selectedId ? `products/${selectedId}` : 'products', selectedId ? 'PUT' : 'POST', bodyFor(form)); setSelectedId(saved.id); setResult(saved.latest_result); setDirty(false); setProducts(current => [saved, ...current.filter(p => p.id !== saved.id)]); setHistory(null); setMessage('제품과 확인 이력을 내 보관함에 기록했습니다.'); }); }
  function selectProduct(product) { setForm({ ...product.input, manufactured_on: product.input.manufactured_on || '', sale_region: product.input.sale_region || '', lot: product.input.lot || '' }); setSelectedId(product.id); setResult(null); setDirty(true); setHistory(null); setOcr(null); setMessage('저장된 입력을 불러왔습니다. 현재 자료로 다시 대조하세요.'); }
  async function action(product, state) { await run(async () => { const updated = await guardedApi(`products/${product.id}/action`, 'PATCH', { state }); setProducts(current => current.map(p => p.id === product.id ? updated : p)); setHistory(null); setMessage('사용자 조치 상태를 기록했습니다. 사용 재개 허가나 회수 인증이 아닙니다.'); }); }
  async function remove(product) { await run(async () => { await guardedApi(`products/${product.id}`, 'DELETE'); setProducts(current => current.filter(p => p.id !== product.id)); setHistory(null); setNotifications(current => current.filter(n => n.product_id !== product.id)); if (selectedId === product.id) { setSelectedId(null); setHistory(null); setResult(null); setDirty(true); } setMessage('제품과 해당 확인·조치 이력을 삭제했습니다.'); }); }
  function sample() { setForm({ ...empty, brand: 'DEMO LAB', model: 'DEMO PB-100', demo: true, manufactured_on: '2026-02-15', sale_region: 'DEMO-KR', lot: '001A' }); setSelectedId(null); setResult(null); setDirty(true); setHistory(null); setOcr(null); setMessage('가상 예시입니다. 모델명 확인에 직접 체크한 뒤 대조하세요.'); }
  async function identify(file) { if (!file) return; const generation = accountGeneration.current; await run(async () => { if (!user) throw new Error('사진 인식은 로그인 후 이용할 수 있습니다.'); if (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 4 * 1024 * 1024) throw new Error('4MB 이하 PNG 또는 JPEG 사진을 선택하세요.'); setOcr(null); const data = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(',')[1]); reader.onerror = () => reject(new Error('사진을 읽지 못했습니다.')); reader.readAsDataURL(file); }); if (generation !== accountGeneration.current) return; setOcr(await guardedApi('identify', 'POST', { image_base64: data })); setMessage('인식 후보를 확인한 뒤 입력란에 적용하세요. 사진 원본은 보관하지 않습니다.'); }); }
  function applyOCR() { setForm({ ...empty, brand: ocr.brand_candidates[0] || '', model: ocr.model_candidates[0] || '' }); setResult(null); setHistory(null); setDirty(true); setSelectedId(null); setMessage('후보를 적용했습니다. 라벨과 대조하고 브랜드·모델명 확인에 체크하세요.'); }
  async function sync() { await run(async () => { const data = await guardedApi('sources/sync', 'POST', {}); setSourceStatus(data.source_status); await loadPrivate(); setMessage(`자료 갱신을 요청하고 ${data.rechecked ?? 0}개 제품을 재확인했습니다. 새 앱내 알림 ${data.notifications_created ?? 0}건. 아래 출처별 성공·실패 상태를 확인하세요.`); }); }
  const sourceItems = Array.isArray(sourceStatus?.sources) ? sourceStatus.sources : [];

  return <><a className="skip-link" href="#main">본문으로 이동</a><header><div className="brand"><span className="brand-mark" aria-hidden="true">B</span><span>배터리체크</span></div><span className="header-note">내 제품의 기록 <span className="dot" /> {server}</span></header>
    <main id="main"><div className="intro"><div><p className="eyebrow">확인에서, 다음 행동까지</p><h1>한 번의 검색을,<br />내 제품의 기록으로.</h1><p>라벨을 읽고, 공식 공지의 조건을 확인하고,<br />아직 끝나지 않은 조치를 이어가세요.</p></div><div className="scope"><span>제품 안전을 단정하지 않습니다</span><strong>근거와 미확인 조건을 함께</strong><p>미국 CPSC 공지 후보와 국내 조회의 한계를 구분합니다. 리콜 미발견은 안전 보증이 아닙니다.</p></div></div>
      {!online && <p className="error" role="alert">오프라인입니다. 최신 공지를 조회할 수 없습니다. 화면의 과거 결과를 현재 결과로 해석하지 마세요.</p>}
      <details className="emergency-always"><summary>연기·불꽃이 있다면 제품 등록을 기다리지 마세요</summary><p>{safety.emergency_text}</p><a className="phone" href="tel:119">119 전화 연결</a><Sources sources={[safety.sources[0], safety.sources[2]]} /><small>{safety.review_status} · {safety.rule_version}</small></details>
      {localRisk && <Risk risk={localRisk} />}
      {error && <div className="error" role="alert">{error}</div>}{message && <p className="message" role="status">{message}</p>}
      <Account key={user?.id || "anonymous"} user={user} busy={busy} onAuth={(register, data) => run(async () => { const generation = ++accountGeneration.current; const account = await guardedApi(`auth/${register ? 'register' : 'login'}`, 'POST', data); if (generation !== accountGeneration.current) return; setUser(account); accountChannel.current?.postMessage('changed'); await loadPrivate(); setMessage('내 보관함에 로그인했습니다.'); })} onLogout={() => run(async () => { await guardedApi('auth/logout', 'POST', {}); clearPrivate(); accountChannel.current?.postMessage('changed'); setMessage('로그아웃했습니다.'); })} onPreferences={consent => run(async () => { setUser(await guardedApi('auth/preferences', 'PATCH', { notification_consent: consent })); setMessage(consent ? '앱내 알림을 켰습니다.' : '앱내 알림을 껐습니다.'); })} onExport={() => run(async () => { const data = await guardedApi('auth/export'); const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })); const a = document.createElement('a'); a.href = url; a.download = 'batterycheck-my-records.json'; a.click(); URL.revokeObjectURL(url); })} onDelete={password => run(async () => { await guardedApi('auth/account', 'DELETE', { password }); clearPrivate(); accountChannel.current?.postMessage('changed'); setMessage('계정과 모든 기록을 삭제했습니다.'); })} />
      <section className="source-status panel" aria-label="공식 자료 상태"><div className="account-bar"><div><p className="eyebrow">출처를 먼저 확인하세요</p><h2>공식 공지와 갱신 상태</h2></div><button className="secondary" disabled={busy || !user} onClick={sync}>공식 자료 갱신 · 내 제품 재확인</button></div><p className="small">확인한 공식 웹 공지와 API 수집 상태를 별도로 표시합니다. 해외 공지는 국내 회수 자격을 보장하지 않습니다. 갱신은 로그인 후 가능합니다.</p>
        {sourceItems.length > 0 ? <div className="source-grid">{sourceItems.map((s, i) => <div key={s.id || i}><strong>{s.name || s.id}</strong><small>{sourceLabels[s.state || s.status] || '상태 확인 필요'} · {s.scope}</small><small>마지막 성공: {instant(s.last_success_at || s.fetched_at)}</small>{s.error && <small className="warning">{s.error}</small>}</div>)}</div> : <p className="small">CPSC 공식 웹 공지를 제한된 범위에서 대조합니다. API 최신 갱신 여부는 대조 결과의 출처 상태를 확인하세요. 국내 Safety Korea API는 미연결입니다.</p>}
        <div className="source-links"><a href="https://www.cpsc.gov/Recalls" target="_blank" rel="noreferrer">CPSC 원문 검색 ↗</a><a href="https://www.safetykorea.kr/" target="_blank" rel="noreferrer">Safety Korea 국내 직접 확인 ↗</a></div>
      </section>
      <div className="workspace"><section className="panel"><div className="section-heading"><div><p className="eyebrow">01 제품 확인</p><h2>{selectedId ? '저장 제품 다시 확인' : '라벨에서 시작하세요'}</h2></div><button type="button" className="secondary" disabled={busy} onClick={sample}>가상 예시 채우기</button></div>
        <div className="photo-input"><label>라벨 사진에서 후보 추출<input type="file" accept="image/png,image/jpeg" disabled={busy || !user} onChange={e => { identify(e.target.files?.[0]); e.target.value = ''; }} /></label><small>{user ? '4MB 이하 PNG·JPEG. 얼굴·연락처·시리얼은 가리고 라벨을 촬영하세요. 사진은 서버의 로컬 OCR로 처리 후 삭제합니다.' : '로그인하면 사진 후보 추출을 이용할 수 있습니다. 직접 입력은 바로 가능합니다.'}</small>{meta?.ocr?.engine && <small>인식 엔진: {meta.ocr.engine} · 추출 후보는 사용자 확인 필요</small>}
          {ocr && <div className="ocr-result"><strong>읽어낸 후보</strong><p>브랜드: {ocr.brand_candidates.join(' / ') || '읽지 못함'}<br />모델: {ocr.model_candidates.join(' / ') || '읽지 못함'}</p><button className="secondary" disabled={busy || (!ocr.brand_candidates.length && !ocr.model_candidates.length)} onClick={applyOCR}>첫 후보를 입력란에 적용</button><details><summary>인식 텍스트 확인</summary><pre>{ocr.text || '인식된 문자가 없습니다.'}</pre></details><small>{ocr.limitations}</small></div>}
        </div>
        {form.demo && <aside className="demo-banner"><strong>가상 데이터 시연</strong><span>DEMO LAB 공지는 실제 리콜이 아닙니다.</span></aside>}
        <form onSubmit={check}><fieldset disabled={busy}><div className="fields"><label>브랜드<input name="brand" value={form.brand} maxLength={80} onChange={e => change('brand', e.target.value)} placeholder="라벨에 적힌 브랜드" /></label><label>정확한 모델명<input name="model" value={form.model} maxLength={80} onChange={e => change('model', e.target.value)} placeholder="접미사까지 그대로 입력" /></label></div>
          <label className="checkbox"><input type="checkbox" checked={form.model_confirmed} onChange={e => change('model_confirmed', e.target.checked)} />입력한 브랜드와 모델명을 직접 확인했습니다.</label>
          <label className="checkbox"><input type="checkbox" checked={form.demo} onChange={e => change('demo', e.target.checked)} />가상 DEMO LAB 제품으로 시연합니다.</label>
          <div className="fields"><label>제조일 <span>모르면 비워두세요</span><input type="date" value={form.manufactured_on} onChange={e => change('manufactured_on', e.target.value)} /></label><label>판매지역<select value={form.sale_region} onChange={e => change('sale_region', e.target.value)}><option value="">모름</option><option value="KR">대한민국</option><option value="US">미국</option><option value="OTHER">그 밖의 지역</option>{form.demo && <><option value="DEMO-KR">DEMO-KR (가상)</option><option value="DEMO-US">DEMO-US (가상)</option></>}</select></label></div>
          <label>로트 <span>앞자리 0 유지 · 실제 시리얼번호는 입력하지 마세요</span><input value={form.lot} maxLength={32} onChange={e => change('lot', e.target.value)} /></label>
          <fieldset className="signs"><legend>사용자가 관찰한 위험징후</legend><p className="small">관찰 응답이며 내부 상태 측정이 아닙니다.</p><div className="sign-grid">{Object.entries(signLabels).map(([sign, label]) => <label className="checkbox" key={sign}><input type="checkbox" checked={form.signs.includes(sign)} onChange={() => toggleSign(sign)} />{label}</label>)}</div><label className="checkbox"><input type="checkbox" checked={form.signs_answered} onChange={e => { if (form.signs.length && !e.target.checked) return; change('signs_answered', e.target.checked); }} />위험징후 질문에 응답했습니다. 선택이 없으면 ‘보고 없음’으로 기록합니다.</label></fieldset>
          {form.demo && <label>데모 출처 상태<select value={form.scenario} onChange={e => change('scenario', e.target.value)}><option value="complete">가상 저장소 대조 완료</option><option value="outage">조회 실패 시연</option><option value="stale">자료 만료 시연</option></select></label>}
          <div className="form-actions"><button type="submit">{busy ? '처리 중…' : '조건 대조하기'}</button><button type="button" className="secondary" disabled={!user || !result || dirty || !form.model_confirmed || result.status === 'needs_identification'} onClick={save}>{selectedId ? '재확인 기록 저장' : '제품 보관함에 저장'}</button></div>
        </fieldset></form>{result ? <Result result={result} /> : <div className="empty-result"><strong>확인한 입력으로 대조합니다</strong><p>모르는 조건은 비워두세요. 정보가 부족하면 추가 확인을 안내합니다. 입력을 수정하면 다시 대조해야 합니다.</p></div>}
      </section>
      <aside className="panel vault"><p className="eyebrow">02 기록과 다음 행동</p><h2>제품 보관함 <span className="count">{products.length}</span></h2><p className="small">{user ? '내 계정에 저장한 제품과 과거 확인 기록입니다.' : '로그인하면 제품과 확인 이력을 저장할 수 있습니다.'}</p>
        {products.length === 0 && <div className="vault-empty"><span aria-hidden="true">＋</span><strong>제품의 확인 기록을 남겨보세요</strong><p>다시 확인할 조건과 사용자 조치 상태가 이곳에 남습니다.</p></div>}
        {products.map(product => <article className="saved-product" key={product.id}><span className="tag">{product.input.demo ? '가상 제품' : '등록 제품'} · 과거 결과</span><h3>{product.input.model}</h3><p>{product.input.brand}</p><p className="small">마지막 확인: {instant(product.latest_result.checked_at)}<br />저장 당시: {product.latest_result.label}</p><Risk risk={product.latest_result.risk} /><label>사용자 조치 상태<select aria-label={`${product.input.model} 조치 상태`} disabled={busy} value={product.action_state} onChange={e => action(product, e.target.value)}>{Object.entries(actionLabels).map(([state, label]) => <option value={state} key={state}>{label}</option>)}</select></label><p className="small">자기보고이며 회수 인증·사용 재개 허가가 아닙니다.</p><div className="vault-actions"><button className="secondary" disabled={busy} onClick={() => selectProduct(product)}>입력 불러오기</button><button className="text-button" disabled={busy} onClick={() => run(async () => setHistory(await guardedApi(`products/${product.id}/history`)))}>확인 이력</button><button className="text-button danger" disabled={busy} onClick={() => remove(product)}>삭제</button></div></article>)}
        {user && <section className="notifications"><h3>앱내 알림 <span className="count">{notifications.filter(n => !n.read_at).length}</span></h3><p className="small">{user.notification_consent ? '자료 갱신 후 새 공지·변경 사항을 확인하세요.' : '계정 관리에서 알림 수신에 동의하면 변경 사항이 쌓입니다.'}</p>{notifications.length === 0 && <p className="small">아직 알림이 없습니다.</p>}{notifications.map(n => <article key={n.id} className="notification"><strong>{n.title || '보관 제품의 공지 변경'}</strong><p>{n.message || n.body}</p><small>{instant(n.created_at)}</small>{!n.read_at && <button className="text-button" disabled={busy} onClick={() => run(async () => { await guardedApi(`notifications/${n.id}/read`, 'PATCH', {}); setNotifications(await guardedApi('notifications')); })}>읽음으로 표시</button>}</article>)}</section>}
      </aside></div>
      <Assistant key={`assistant-${user?.id || 'anonymous'}`} user={user} status={meta?.assistant} onConsent={consent => run(async () => { setUser(await guardedApi('auth/preferences', 'PATCH', { ai_consent: consent })); setMessage(consent ? 'AI 도우미 이용에 동의했습니다.' : 'AI 도우미 동의를 해제했습니다. 이후 질문은 전송되지 않습니다.'); })} />
      {history && <section className="panel history"><div className="section-heading"><h2>확인 이력 · 과거 입력과 공지 버전</h2><button className="text-button" onClick={() => setHistory(null)}>이력 닫기</button></div>{history.checks.map(check => <div key={check.id}><p>{check.input.brand} / {check.input.model} · 제조일 {check.input.manufactured_on || '미확인'} · 로트 {check.input.lot || '미확인'}</p><Result result={check.result} historical /></div>)}<h3>사용자 조치 기록</h3><ul>{history.actions.map((a, i) => <li key={i}>{actionLabels[a.state]} · {instant(a.recorded_at)}</li>)}</ul></section>}
      <Feedback />
      <footer><p>AI 도우미의 답변은 모델이 생성하며 사실 확인은 표시된 공식 자료에서 하세요.</p><p>사진 인식은 후보 추출을 돕습니다. 배터리 수명·내부 상태·화재확률을 계산하지 않습니다.</p><p>공지 적용 대상과 실제 회수 자격은 공식 원문·제조사에서 확인하세요. 안전 안내는 전문가 검토 전입니다.</p><p>개인정보: 계정·제품·이력·AI 대화는 계정 삭제 때까지 보관하며, AI 도우미에 동의한 경우에만 질문과 제품 정보를 외부 AI API에 전송합니다. 내 기록 내려받기와 영구 삭제를 제공합니다. 사진 원본은 인식 후 보관하지 않습니다.</p></footer>
    </main></>;
}
